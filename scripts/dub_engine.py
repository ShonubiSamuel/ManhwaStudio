"""
dub_engine.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Full dubbing pipeline.  Three phases, one engine.

  Phase 2 — generate_continuous() / generate_all_batches()
  Phase 3 — align_and_split_all()
  Phase 4 — sync_to_english()
  Phase 5 — stitch_final()  (optional)

Extracted code locations:
  Alignment algorithm    → dub/aligner.py
  Batch state CRUD       → dub/batch_manager.py
  Audio utilities        → core/audio_utils.py
  Script building        → tts/script_builder.py
  Voice profiles         → tts/voice_profile.py
"""

from __future__ import annotations

import datetime
import json
import os
import queue
import re
import subprocess
import threading
import time
from copy import copy
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import config
import runtime_settings as rs
from tts.script_builder  import (
    build_chapter_script, CONDA_PYTHON, subprocess_env,
    split_script, MODEL_PATHS,
)
from tts                 import synth
from tts.voice_profile   import VoiceProfile, VoiceProfileManager
from core.audio_utils    import (
    get_wav_duration, normalize_wav, stretch_audio, pad_to_duration, concat_wavs,
)
from dub.aligner         import (
    transcribe_audio, match_segments_to_words, even_split_timings, snap_and_cut,
)
from dub.batch_manager   import load_batch_state, save_batch_state, get_batch_state_path


# ── DubEngine ─────────────────────────────────────────────────────────────────

class DubEngine:
    """
    Three-phase dubbing engine.

    Constructor only needs db and an optional log callback.
    All path information is read from episode.output_folder at runtime.
    """

    def __init__(self, db, on_log: Callable = None):
        self.db         = db
        self.on_log     = on_log
        self._stop_flag = False

    # ══════════════════════════════════════════════════════════════════════════
    # FOLDER & DB HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def _get_lang_folder(self, episode: dict, lang_code: str) -> Path:
        folder = Path(episode["output_folder"]) / "dub" / lang_code
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _ensure_panel_audio(self, panel_id: int, lang_code: str) -> dict:
        row = self.db.get_panel_audio(panel_id, lang_code)
        if not row:
            self.db.ensure_panel_audio(panel_id, lang_code)
            row = self.db.get_panel_audio(panel_id, lang_code)
        return row

    def _ensure_voice_design_ref(
        self,
        episode:       dict,
        lang_code:     str,
        voice_profile,
        on_log:        Callable = None,
    ) -> object:
        """
        VoiceDesign → VoiceClone pivot.
        Generates one reference WAV and returns a VoiceClone profile
        pointing at it.  Subsequent calls reuse the cached WAV.
        """
        log = on_log or self._log

        if getattr(voice_profile, "mode", "") != "VoiceDesign":
            return voice_profile

        lang_folder = self._get_lang_folder(episode, lang_code)
        ref_path    = lang_folder / "_voice_ref.wav"
        lang_name   = config.SUPPORTED_LANGUAGES.get(lang_code, "Auto")

        if ref_path.exists() and ref_path.stat().st_size > 1000:
            log(f"[{lang_code}] reusing cached voice design ref ✓", "info")
        else:
            log(f"[{lang_code}] generating one-time voice design reference …", "accent")
            seed_map = {
                "Chinese":  "今天天气不错，我们出去走走吧。",
                "Japanese": "今日はいい天気ですね、散歩しましょう。",
                "Korean":   "오늘 날씨가 좋네요, 산책하러 갑시다.",
            }
            seed_text    = seed_map.get(lang_name, "Hello, this is a voice reference sample.")
            ref_profile  = copy(voice_profile)
            ref_profile.language = lang_name

            script = synth.build_synth_script(
                profile      = ref_profile,
                sentences    = [seed_text],
                output_paths = [str(ref_path)],
                skip_indices = set(),
            )

            try:
                result = subprocess.run(
                    [synth.synth_python(ref_profile), "-c", script],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=300, env=synth.synth_env(ref_profile),
                )
            except Exception as exc:
                log(f"[{lang_code}] voice ref error: {exc} — VoiceDesign fallback", "warning")
                return voice_profile

            if "DONE:0" not in result.stdout or not ref_path.exists():
                log(f"[{lang_code}] voice ref failed — voice may vary across batches", "warning")
                return voice_profile

            log(f"[{lang_code}] voice design ref saved → {ref_path.name} ✓", "success")

        base_model = voice_profile.model.replace("VoiceDesign", "Base")
        if base_model not in MODEL_PATHS:
            base_model = "1.7B-Base"

        clone_profile               = copy(voice_profile)
        clone_profile.mode          = "VoiceClone"
        clone_profile.model         = base_model
        clone_profile.ref_wav_path  = str(ref_path)
        clone_profile.ref_wav_text  = ""
        clone_profile.x_vector_only = True
        return clone_profile

    # ══════════════════════════════════════════════════════════════════════════
    # FIX 5: CJK-aware segment joining
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _join_segments_for_tts(texts: List[str], lang_code: str = "en") -> str:
        """
        Join panel texts into one paragraph for TTS input.
        FIX 5: Chinese/Japanese use 。 separator (not '. ').
        """
        use_cjk     = lang_code in ("zh", "ja")
        sep         = "。" if use_cjk else ". "
        end         = "。" if use_cjk else "."
        strip_chars = ".,!?…。！？、，"
        cleaned: List[str] = []
        for text in texts:
            text = (text or "").strip()
            if text:
                cleaned.append(text.rstrip(strip_chars))
        return (sep.join(cleaned) + end) if cleaned else ""

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2 — CONTINUOUS TTS GENERATION
    # ══════════════════════════════════════════════════════════════════════════

    def generate_continuous(
        self,
        episode_id:   int,
        lang_code:    str,
        voice_profile,
        on_log:       Callable = None,
        on_progress:  Callable = None,
    ) -> bool:
        """
        Phase 2 — Generate ONE continuous WAV for an entire language.
        Legacy method kept for backward compatibility and dub_tab manual use.
        Pipeline tab uses generate_all_batches() instead (Phase 0 D1).
        """
        log = on_log or self._log

        episode = self.db.get_episode(episode_id)
        if not episode:
            log("Episode not found", "error")
            return False

        panels = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])
        if not panels:
            log("No panels found for this episode", "error")
            return False

        texts: List[str] = []
        for panel in panels:
            if lang_code == "en":
                text = (panel.get("narration_text") or "").strip()
            else:
                row  = self.db.get_panel_audio(panel["id"], lang_code)
                text = ((row or {}).get("translated_text") or "").strip()
            if text:
                texts.append(text)

        if not texts:
            log(f"No text found for '{lang_code}' — run translate stage first", "error")
            return False

        # FIX 5: CJK-aware joining
        joined = self._join_segments_for_tts(texts, lang_code)
        if not joined.strip():
            log(f"'{lang_code}' produced empty joined text", "error")
            return False

        lang_folder     = self._get_lang_folder(episode, lang_code)
        continuous_path = str(lang_folder / "_continuous.wav")

        log(f"[{lang_code}] {len(texts)} panel(s)  →  {len(joined)} chars joined", "info")
        log(f"[{lang_code}] generating continuous audio …", "accent")

        # FIX 2: copy profile + set correct language tag
        lang_name   = config.SUPPORTED_LANGUAGES.get(lang_code, "Auto")
        gen_profile = copy(voice_profile)
        gen_profile.language = lang_name

        # FIX 7: VoiceDesign → VoiceClone pivot
        gen_profile = self._ensure_voice_design_ref(episode, lang_code, gen_profile, log)

        script = synth.build_synth_script(
            profile      = gen_profile,
            sentences    = [joined],
            output_paths = [continuous_path],
            skip_indices = set(),
        )

        self._stop_flag = False

        try:
            proc = subprocess.Popen(
                [synth.synth_python(gen_profile), "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=synth.synth_env(gen_profile),
            )

            line_q: queue.Queue = queue.Queue()
            stderr_q: queue.Queue = queue.Queue()

            def _reader(p=proc, q=line_q):
                for raw in p.stdout: q.put(raw)
                q.put(None)

            def _stderr_reader(p=proc, q=stderr_q):
                for raw in p.stderr: q.put(raw)
                q.put(None)

            threading.Thread(target=_reader,        daemon=True, name="dub-tts-stdout").start()
            threading.Thread(target=_stderr_reader, daemon=True, name="dub-tts-stderr").start()

            start_time = last_heartbeat = time.time()
            ok = False
            timeout = config.DUB_CONTINUOUS_TIMEOUT

            while True:
                if self._stop_flag:
                    proc.terminate()
                    log("Stopped by user", "warning")
                    break
                try:
                    raw = line_q.get(timeout=1)
                except queue.Empty:
                    elapsed = time.time() - start_time
                    if time.time() - last_heartbeat >= 30:
                        log(f"  [{lang_code}] still generating … ({elapsed:.0f}s)", "muted")
                        last_heartbeat = time.time()
                    if elapsed > timeout:
                        log(f"  [{lang_code}] timed out after {elapsed:.0f}s — aborting", "error")
                        proc.terminate()
                        try: proc.wait(timeout=10)
                        except subprocess.TimeoutExpired: proc.kill()
                        break
                    continue
                if raw is None:
                    break
                line = raw.strip()
                if not line:
                    continue
                last_heartbeat = time.time()
                if line == "MODEL_READY":
                    log(f"  [{lang_code}] model loaded ✓", "success")
                elif line == "VOICE_READY":
                    log(f"  [{lang_code}] voice ready ✓", "success")
                elif line == "WARMUP_OK":
                    log(f"  [{lang_code}] warm-up complete ✓", "success")
                elif line.startswith("WARMUP_FAIL:"):
                    msg = line.split(":", 1)[1] if ":" in line else line
                    log(f"  [{lang_code}] warm-up failed (non-fatal): {msg}", "warning")
                elif line.startswith("DONE:"):
                    ok = True
                    log(f"  [{lang_code}] generation complete ✓", "success")
                elif line.startswith("ERROR:"):
                    parts = line.split(":", 2)
                    log(f"  [{lang_code}] error: {parts[2] if len(parts) > 2 else '?'}", "error")

            stderr_lines = []
            try:
                while True:
                    item = stderr_q.get_nowait()
                    if item is None: break
                    stderr_lines.append(item.rstrip())
            except Exception:
                pass
            if stderr_lines:
                stderr_text = "\n".join(stderr_lines)
                if any(kw in stderr_text for kw in ("FATAL", "Error", "Traceback")):
                    log(f"  [{lang_code}] subprocess stderr:\n{stderr_text[-600:]}", "error")
                    return False

            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()

        except Exception as exc:
            log(f"  [{lang_code}] subprocess error: {exc}", "error")
            return False

        if not ok or not Path(continuous_path).exists():
            log(f"  [{lang_code}] _continuous.wav was not produced", "error")
            return False

        normalize_wav(continuous_path)
        dur = get_wav_duration(continuous_path)
        log(f"  [{lang_code}] continuous audio ready — {dur:.1f}s ✓", "success")
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 3 — FASTER-WHISPER SPLIT + SILENCE-SNAP
    # ══════════════════════════════════════════════════════════════════════════

    def align_and_split_all(
        self,
        episode_id:  int,
        languages:   List[str],
        on_log:      Callable = None,
        on_progress: Callable = None,
    ) -> bool:
        """Phase 3 — split each language's _continuous.wav into per-panel WAVs."""
        log = on_log or self._log

        for pkg, install in (
            ("faster_whisper",     "pip install faster-whisper"),
            ("rapidfuzz.distance", "pip install rapidfuzz"),
            ("pydub",              "pip install pydub"),
        ):
            try:
                __import__(pkg)
            except ImportError:
                log(f"{pkg} not installed — run: {install}", "error")
                return False

        # English first — its per-panel durations are needed by sync
        ordered = (
            [lc for lc in languages if lc == "en"] +
            [lc for lc in languages if lc != "en"]
        )

        all_ok = True
        total  = len(ordered)

        for i, lc in enumerate(ordered):
            if self._stop_flag:
                break
            log(f"Splitting '{lc}' ({i + 1}/{total}) …", "accent")
            ok     = self._split_one_language(episode_id, lc, on_log=log)
            all_ok = all_ok and ok
            if on_progress:
                on_progress(i + 1, total)

        log("Split phase complete ✓", "success" if all_ok else "warning")
        return all_ok

    def _split_one_language(
        self,
        episode_id: int,
        lang_code:  str,
        on_log:     Callable = None,
    ) -> bool:
        """Split one language's _continuous.wav into per-panel WAVs."""
        import json as _json

        log     = on_log or self._log
        episode = self.db.get_episode(episode_id)

        lang_folder     = self._get_lang_folder(episode, lang_code)
        continuous_path = str(lang_folder / "_continuous.wav")

        if not Path(continuous_path).exists():
            log(f"  [{lang_code}] _continuous.wav missing — run generate_continuous first", "warning")
            return False

        panels_sorted = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])

        panel_rows:  List[tuple] = []
        panel_texts: List[str]   = []

        for panel in panels_sorted:
            if lang_code == "en":
                text = (panel.get("narration_text") or "").strip()
            else:
                row  = self.db.get_panel_audio(panel["id"], lang_code)
                text = ((row or {}).get("translated_text") or "").strip()
            if not text:
                continue
            audio_row = self._ensure_panel_audio(panel["id"], lang_code)
            panel_rows.append((panel, audio_row))
            panel_texts.append(text)

        if not panel_rows:
            log(f"  [{lang_code}] no panels with text — skipping", "warning")
            return False

        # Step 1: Transcribe — uses dub.aligner.transcribe_audio
        words = transcribe_audio(continuous_path, lang_code, log)
        if not words:
            log(f"  [{lang_code}] transcription returned nothing — falling back to even split", "warning")
            timings = even_split_timings(continuous_path, len(panel_texts))
        else:
            # Step 2: Fuzzy-match text spans to word timeline
            timings = match_segments_to_words(panel_texts, words, log)

        (lang_folder / "_timings.json").write_text(
            _json.dumps(timings, indent=2), encoding="utf-8")

        # Step 3: Silence-snap boundaries and cut
        output_paths = [
            str(lang_folder / f"{lang_code}_panel_{panel['panel_index']:04d}.wav")
            for panel, _ in panel_rows
        ]
        snap_and_cut(continuous_path, timings, output_paths, log)

        # Step 4: Normalise and persist to DB
        saved = 0
        for i, (panel, audio_row) in enumerate(panel_rows):
            wav = output_paths[i]
            if Path(wav).exists() and Path(wav).stat().st_size > 500:
                normalize_wav(wav)
                dur = get_wav_duration(wav)
                # Persist the raw clip AND its duration — the UI compares this
                # (the language's natural pacing) against the English timing.
                self.db.update_panel_audio(audio_row["id"], raw_wav=wav, raw_duration=round(dur, 3))
                log(f"  [{lang_code}] panel {panel['panel_index'] + 1} → {dur:.2f}s", "muted")
                saved += 1
            else:
                log(f"  [{lang_code}] panel {panel['panel_index'] + 1} cut missing", "warning")

        log(f"  [{lang_code}] {saved}/{len(panel_rows)} panels saved ✓",
            "success" if saved == len(panel_rows) else "warning")
        return saved > 0

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 4 — SYNC TO ENGLISH TIMING
    # ══════════════════════════════════════════════════════════════════════════

    def sync_to_english(
        self,
        episode_id:    int,
        lang_code:     str,
        panel_indices: List[int] = None,
        on_log:        Callable  = None,
        on_progress:   Callable  = None,
    ) -> bool:
        """Phase 4 — Stretch each panel's dubbed audio to match English timing."""
        log     = on_log or self._log
        episode = self.db.get_episode(episode_id)
        panels  = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])
        lang_folder = self._get_lang_folder(episode, lang_code)

        work  = [p for p in panels if panel_indices is None or p["panel_index"] in panel_indices]
        total = len(work)

        comfort = rs.get_float("dub_max_stretch", getattr(config, "DUB_MAX_STRETCH", 1.20)) or 1.20
        log(f"Syncing '{lang_code}' — {total} panel(s) "
            f"(every panel matched to the English length; panels needing more than "
            f"+{int((comfort - 1) * 100)}% compression are flagged as rushed)", "accent")

        done = failed = over = skipped = 0
        for panel in work:
            n          = panel["panel_index"] + 1
            en_audio   = self.db.get_panel_audio(panel["id"], "en")
            lang_audio = self.db.get_panel_audio(panel["id"], lang_code)

            # A panel with no audio (e.g. an empty/silent panel with no narration
            # or translation) is SKIPPED, not failed — one such panel must not
            # halt the whole sync run.
            if not en_audio or not en_audio.get("raw_wav"):
                log(f"  Panel {n}: no English audio — skipping (empty panel?)", "warning")
                skipped += 1
                continue

            if not lang_audio or not lang_audio.get("raw_wav"):
                log(f"  Panel {n}: no '{lang_code}' audio — skipping (empty/blank translation?)", "warning")
                skipped += 1
                continue

            budget = get_wav_duration(en_audio["raw_wav"])
            if budget <= 0:
                log(f"  Panel {n}: English audio has zero duration — skipping", "warning")
                skipped += 1
                continue

            src_path    = lang_audio["raw_wav"]
            foreign     = get_wav_duration(src_path)
            synced_path = str(lang_folder / f"{lang_code}_panel_{panel['panel_index']:04d}_sync.wav")

            # Fit each panel to the English length (REQUIRED for frame-aligned
            # assembly). Translation should already have made this small.
            if foreign <= budget:
                # Shorter → pad with (inaudible) trailing silence; never slow the voice.
                ok, err = pad_to_duration(src_path, synced_path, budget)
                tag = "pad"
            else:
                # Longer → compress to the English length. The length match is
                # non-negotiable; if a panel needs more than the comfort threshold
                # it still matches but is flagged so its translation can be shortened.
                ok, err = stretch_audio(src_path, synced_path, budget)
                if foreign / budget > comfort + 0.001:
                    over += 1
                    tag = "rushed"
                    log(f"  Panel {n}: {lang_code} translation too long "
                        f"({foreign:.2f}s vs {budget:.2f}s English) — compressed to fit and "
                        f"flagged as rushed; shorten this panel's translation", "warning")
                else:
                    tag = "fit"

            if ok:
                # Keep raw_wav/raw_duration (the pre-fit clip) so the UI can compare
                # original vs budget; record the fitted clip in synced_* fields.
                synced_dur = get_wav_duration(synced_path)
                self.db.update_panel_audio(
                    lang_audio["id"],
                    synced_wav      = synced_path,
                    synced_duration = round(synced_dur, 3),
                    is_synced       = 1,
                )
                done += 1
                if tag != "rushed":
                    log(f"  Panel {n} → {synced_dur:.2f}s ({tag})", "muted")
            else:
                failed += 1
                log(f"  Panel {n} fit failed: {err}", "error")

            if on_progress:
                on_progress(done + failed, total)

        msg = f"Sync done — {done} fitted to English length"
        if skipped:
            msg += f", {skipped} skipped (no audio)"
        if over:
            msg += f", {over} rushed (re-translate shorter)"
        if failed:
            msg += f", {failed} failed"
        # Skipped (empty) panels are fine — only a real processing error fails the run.
        log(msg, "success" if failed == 0 else "warning")
        return failed == 0

    # ══════════════════════════════════════════════════════════════════════════
    # SINGLE-PANEL REGENERATION
    # ══════════════════════════════════════════════════════════════════════════

    def _regen_profile(self, episode, lang_code, voice_profile):
        """The voice profile used to regenerate one panel — language-tagged, and
        (for VoiceDesign) pivoted onto the cached reference clip so a regen keeps
        the chapter's voice identity. Shared by regenerate_segment and the
        resident worker so both produce the same voice."""
        lang_name     = config.SUPPORTED_LANGUAGES.get(lang_code, "Auto")
        regen_profile = copy(voice_profile)
        regen_profile.language = lang_name
        if getattr(regen_profile, "mode", "") == "VoiceDesign":
            lang_folder = self._get_lang_folder(episode, lang_code)
            cached_ref  = lang_folder / "_voice_ref.wav"
            if cached_ref.exists() and cached_ref.stat().st_size > 1000:
                base_model = regen_profile.model.replace("VoiceDesign", "Base")
                if base_model not in MODEL_PATHS:
                    base_model = "1.7B-Base"
                regen_profile               = copy(regen_profile)
                regen_profile.mode          = "VoiceClone"
                regen_profile.model         = base_model
                regen_profile.ref_wav_path  = str(cached_ref)
                regen_profile.ref_wav_text  = ""
                regen_profile.x_vector_only = True
        return regen_profile

    def regenerate_segment(
        self,
        episode_id:  int,
        lang_code:   str,
        panel_index: int,
        voice_profile,
        on_log:      Callable = None,
        worker=None,
    ) -> bool:
        """Regenerate audio for ONE panel. If `worker` (a resident TTSWorker) is
        given it's used (no model reload); otherwise a short dedicated subprocess
        is spawned. A worker failure falls back to the subprocess automatically."""
        log = on_log or self._log

        panels = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])
        panel  = next((p for p in panels if p["panel_index"] == panel_index), None)
        if not panel:
            log(f"Panel index {panel_index} not found", "error")
            return False

        if lang_code == "en":
            text = (panel.get("narration_text") or "").strip()
        else:
            row  = self.db.get_panel_audio(panel["id"], lang_code)
            text = ((row or {}).get("translated_text") or "").strip()

        if not text:
            log(f"No text for panel {panel_index + 1} in '{lang_code}'", "error")
            return False

        episode     = self.db.get_episode(episode_id)
        lang_folder = self._get_lang_folder(episode, lang_code)

        for suffix in ("", "_sync"):
            p = lang_folder / f"{lang_code}_panel_{panel_index:04d}{suffix}.wav"
            if p.exists():
                p.unlink()

        out_path  = str(lang_folder / f"{lang_code}_panel_{panel_index:04d}.wav")
        audio_row = self._ensure_panel_audio(panel["id"], lang_code)

        regen_profile = self._regen_profile(episode, lang_code, voice_profile)
        log(f"Regenerating panel {panel_index + 1} [{lang_code}] "
            f"(language={regen_profile.language}) …", "accent")

        ok = False
        if worker is not None:
            ok = worker.generate(text, out_path)   # resident model — no reload

        if not ok:   # no worker, or the worker request failed → one-shot subprocess
            script = synth.build_synth_script(
                profile      = regen_profile,
                sentences    = [text],
                output_paths = [out_path],
                skip_indices = set(),
            )
            try:
                result = subprocess.run(
                    [synth.synth_python(regen_profile), "-c", script],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=300, env=synth.synth_env(regen_profile),
                )
            except subprocess.TimeoutExpired:
                log(f"  Panel {panel_index + 1} regen timed out", "error")
                return False
            except Exception as exc:
                log(f"  Panel {panel_index + 1} regen error: {exc}", "error")
                return False
            if "DONE:0" not in result.stdout or not Path(out_path).exists():
                err = result.stderr.strip()[-300:] if result.stderr else "no output"
                log(f"  Panel {panel_index + 1} regen failed: {err}", "error")
                return False

        normalize_wav(out_path)
        dur = get_wav_duration(out_path)
        self.db.update_panel_audio(audio_row["id"], raw_wav=out_path, raw_duration=round(dur, 3))
        log(f"  Panel {panel_index + 1} regenerated ({dur:.1f}s) ✓", "success")

        if lang_code != "en":
            self.sync_to_english(
                episode_id=episode_id, lang_code=lang_code,
                panel_indices=[panel_index], on_log=log,
            )
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # FIX RUSHED PANELS  (re-translate shorter → re-dub → re-sync, best of N)
    # ══════════════════════════════════════════════════════════════════════════

    def fix_rushed_panels(
        self,
        episode_id:    int,
        lang_code:     str,
        voice_profile,
        shorten_fn:    Callable,            # (english, current, target_chars) -> str
        comfort:       float = 1.20,
        attempts:      int   = 3,
        floor:         float = 0.35,
        indices:       List[int] = None,
        on_log:        Callable = None,
        on_progress:   Callable = None,
    ) -> dict:
        """
        For each "rushed" panel (its dub runs longer than the English budget by
        more than `comfort`), re-translate the line shorter via `shorten_fn`,
        re-dub just that panel, re-sync it, and keep the best of `attempts`.

        `shorten_fn(english, current, target_chars) -> str` is injected so the
        translation/LLM dependency stays out of the engine.  Does NOT stitch —
        the caller rebuilds the combined track once afterwards.

        Returns {"targets": N, "fixed": M}.
        """
        log    = on_log or self._log
        panels = {p["panel_index"]: p for p in self.db.list_panels(episode_id)}

        def _ratio(p):
            en  = (self.db.get_panel_audio(p["id"], "en") or {}).get("raw_duration") or 0
            tgt = (self.db.get_panel_audio(p["id"], lang_code) or {}).get("raw_duration") or 0
            return (tgt / en) if en > 0 else 0.0

        if indices is not None:
            targets = [i for i in indices if i in panels]
        else:
            targets = sorted(i for i, p in panels.items() if _ratio(p) > comfort + 0.001)
        if not targets:
            return {"targets": 0, "fixed": 0}

        log(f"Fixing {len(targets)} rushed '{lang_code}' panel(s) — up to {attempts} "
            f"attempt(s) each, keeping the best fit …", "accent")

        # Load the model ONCE for the whole fix pass (instead of per panel/attempt).
        # Falls back to per-call subprocesses if the worker can't start.
        episode = self.db.get_episode(episode_id)
        worker  = None
        try:
            from tts.worker import TTSWorker
            w = TTSWorker(self._regen_profile(episode, lang_code, voice_profile), on_log=log)
            worker = w if w.start() else None
        except Exception as exc:
            log(f"  resident worker unavailable ({exc}) — per-call mode", "muted")
            worker = None

        fixed = 0
        fixed_idx: set = set()   # panels whose translation actually changed
        try:
          for n, pidx in enumerate(targets):
            p   = panels[pidx]
            row = self.db.get_panel_audio(p["id"], lang_code) or {}
            en  = (self.db.get_panel_audio(p["id"], "en") or {}).get("raw_duration") or 0
            english = (p.get("narration_text") or "").strip()
            cur     = (row.get("translated_text") or "").strip()
            cur_dur = row.get("raw_duration") or 0
            if en <= 0 or not english or not cur or "id" not in row:
                log(f"  Panel {pidx + 1}: missing data — skipping", "muted")
                continue

            cur_ratio    = (cur_dur / en) if en else 99.0
            best         = {"tr": cur, "ratio": cur_ratio}
            on_disk      = cur
            min_len      = max(8, int(len(english) * floor))
            target_chars = max(min_len, int(len(cur) * comfort / max(1.0, cur_ratio)))

            for a in range(attempts):
                new_tr  = shorten_fn(english, cur, target_chars) or ""
                preview = new_tr.replace("\n", " ")[:60]
                log(f"  Panel {pidx + 1}: try {a + 1} → \"{preview}\" ({len(new_tr)}c, target {target_chars}c)", "muted")
                if not new_tr:
                    log(f"  Panel {pidx + 1}: try {a + 1} skipped — model returned nothing", "warning")
                    continue
                if len(new_tr) < min_len:
                    log(f"  Panel {pidx + 1}: try {a + 1} too short ({len(new_tr)}c < {min_len}c) — would gut, skipped", "warning")
                    target_chars = int(target_chars * 1.25)
                    continue
                self.db.update_panel_audio(row["id"], translated_text=new_tr)
                if not self.regenerate_segment(episode_id, lang_code, pidx, voice_profile, on_log=log, worker=worker):
                    continue
                on_disk = new_tr
                ndur  = (self.db.get_panel_audio(p["id"], lang_code) or {}).get("raw_duration") or 0
                ratio = (ndur / en) if en else 99.0
                log(f"  Panel {pidx + 1}: try {a + 1} dubbed → {ndur:.2f}s vs {en:.2f}s ({int((ratio - 1) * 100):+d}%)", "muted")
                if abs(1 - ratio) < abs(1 - best["ratio"]):
                    best = {"tr": new_tr, "ratio": ratio}
                if ratio <= comfort:
                    break
                target_chars = max(min_len, int(len(new_tr) * comfort / max(1.0, ratio)))

            if best["tr"] != on_disk:
                self.db.update_panel_audio(row["id"], translated_text=best["tr"])
                self.regenerate_segment(episode_id, lang_code, pidx, voice_profile, on_log=log, worker=worker)

            if best["tr"] != cur:
                fixed_idx.add(pidx)
            if best["ratio"] < cur_ratio - 0.01:
                fixed += 1
                log(f"  Panel {pidx + 1}: improved {int((cur_ratio - 1) * 100):+d}% → "
                    f"{int((best['ratio'] - 1) * 100):+d}% ✓", "success")
            else:
                log(f"  Panel {pidx + 1}: couldn't shorten without gutting — left as is", "warning")
            if on_progress:
                on_progress(n + 1, len(targets))
        finally:
            if worker:
                worker.close()

        # Consistency pass: re-dub each AFFECTED BATCH as a unit, so a fixed panel
        # is read together with its batch-mates again (not in isolation), then
        # re-split + re-sync. Best-effort — if it fails, the per-panel fix stands.
        if fixed_idx:
            try:
                batches = self._batches_for_panels(episode_id, lang_code, fixed_idx)
                if batches:
                    log(f"Re-dubbing {len(batches)} affected batch(es) as a unit "
                        f"for seamless voice …", "accent")
                    for b in batches:
                        self.regenerate_batch(episode_id, lang_code, voice_profile, b, on_log=log)
                    self._split_one_language(episode_id, lang_code, on_log=log)
                    self.sync_to_english(episode_id, lang_code, on_log=log)
                    log("Affected batch(es) re-dubbed + re-synced ✓", "success")
            except Exception as exc:
                log(f"  batch re-dub pass skipped ({exc}) — per-panel fix kept", "warning")

        return {"targets": len(targets), "fixed": fixed}

    def _batches_for_panels(self, episode_id, lang_code, panel_indices) -> list:
        """Batch indices whose panel range includes any of panel_indices."""
        ep = self.db.get_episode(episode_id)
        if not ep:
            return []
        state   = load_batch_state(Path(ep["output_folder"]) / "dub" / "batch_state.json")
        batches = (state.get(lang_code, {}) or {}).get("batches", [])
        want    = set(panel_indices)
        return sorted(b["idx"] for b in batches if want.intersection(b.get("panels", [])))

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 5 — STITCH FINAL AUDIO TRACK
    # ══════════════════════════════════════════════════════════════════════════

    def stitch_final(
        self,
        episode_id: int,
        lang_code:  str,
        on_log:     Callable = None,
    ) -> Optional[str]:
        """Phase 5 — Concatenate all per-panel audio clips into one final track."""
        import wave as _wave
        log     = on_log or self._log
        episode = self.db.get_episode(episode_id)
        panels  = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])

        wav_files: List[str] = []
        for panel in panels:
            audio = self.db.get_panel_audio(panel["id"], lang_code)
            if not audio:
                continue
            # Prefer the synced (time-stretched) clip; fall back to the raw clip
            # for English (the reference, never stretched).
            wav = audio.get("synced_wav") or audio.get("raw_wav")
            if wav and Path(wav).exists():
                wav_files.append(wav)
            else:
                log(f"  Panel {panel['panel_index'] + 1} missing audio — skipped", "warning")

        if not wav_files:
            log("No audio files to stitch", "error")
            return None

        final_dir = Path(episode["output_folder"]) / "dub" / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        ts         = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        final_path = final_dir / f"{lang_code}_dubbed_{ts}.wav"

        try:
            # Normalize each clip in-place before concatenating
            for wav_path in wav_files:
                normalize_wav(wav_path)

            # Concatenate using wave (no silence between panels — sync stage
            # already matched each clip's duration to English panel timing)
            all_frames: List[bytes] = []
            sample_rate = channels = sampwidth = None

            for wav_path in wav_files:
                with _wave.open(str(wav_path), "rb") as wf:
                    if sample_rate is None:
                        sample_rate = wf.getframerate()
                        channels    = wf.getnchannels()
                        sampwidth   = wf.getsampwidth()
                    all_frames.append(wf.readframes(wf.getnframes()))

            with _wave.open(str(final_path), "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(sampwidth)
                out.setframerate(sample_rate)
                for frames in all_frames:
                    out.writeframes(frames)

            log(f"Final dubbed track → {final_path.name}", "success")
            return str(final_path)

        except Exception as exc:
            log(f"Stitch failed: {exc}", "error")
            return None

    def stitch_synced(
        self,
        episode_id: int,
        lang_code:  str,
        on_log:     Callable = None,
    ) -> Optional[str]:
        """
        Concatenate a language's per-panel SYNCED clips into one full track,
        written to a stable path: dub/{lang}/_synced.wav.

        This is the post-Sync combined audio — each clip already time-stretched
        to the English panel timing — so playing it reflects the real pacing of
        the whole language.  English has no stretched clips (it is the timing
        reference), so its raw per-panel clips are used instead.  Overwrites the
        previous file each Sync run so it always reflects the latest sync.
        """
        log     = on_log or self._log
        episode = self.db.get_episode(episode_id)
        if not episode:
            return None
        panels  = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])

        wavs: List[str] = []
        for panel in panels:
            audio = self.db.get_panel_audio(panel["id"], lang_code) or {}
            wav   = audio.get("synced_wav") or audio.get("raw_wav")
            if wav and Path(wav).exists():
                wavs.append(wav)

        if not wavs:
            return None

        out_path = self._get_lang_folder(episode, lang_code) / "_synced.wav"
        ok = concat_wavs(wavs, str(out_path))
        if ok:
            dur = get_wav_duration(str(out_path))
            log(f"  [{lang_code}] combined synced track → {out_path.name} ({dur:.1f}s) ✓", "success")
            return str(out_path)
        log(f"  [{lang_code}] could not build combined synced track", "warning")
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # BATCH TTS GENERATION
    # ══════════════════════════════════════════════════════════════════════════

    def generate_all_batches(
        self,
        episode_id:    int,
        lang_code:     str,
        voice_profile,
        batch_size:    int       = 5,
        on_log:        Callable  = None,
        on_progress:   Callable  = None,
        on_batch_done: Callable  = None,
    ) -> bool:
        """
        Generate TTS audio in batches of batch_size panels, using ONE subprocess
        per language so the model loads once and voice is guaranteed consistent.
        After all batches complete, concatenate into _continuous.wav.
        """
        log = on_log or self._log
        ep  = self.db.get_episode(episode_id)
        if not ep:
            log("Episode not found", "error")
            return False

        panels = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])
        if not panels:
            log("No panels found", "error")
            return False

        all_texts: List[str] = []
        for panel in panels:
            if lang_code == "en":
                text = (panel.get("narration_text") or "").strip()
            else:
                row  = self.db.get_panel_audio(panel["id"], lang_code)
                text = ((row or {}).get("translated_text") or "").strip()
            all_texts.append(text)

        if not any(all_texts):
            log(f"No text for '{lang_code}' — run TRANSLATE first", "error")
            return False

        lang_folder  = self._get_lang_folder(ep, lang_code)
        state_path   = Path(ep["output_folder"]) / "dub" / "batch_state.json"
        state        = load_batch_state(state_path)

        if lang_code not in state:
            state[lang_code] = {"profile": voice_profile.name, "batches": []}

        # Batch-size change guard
        prev_batch_size = state.get("batch_size")
        if prev_batch_size is not None and prev_batch_size != batch_size:
            log(
                f"  [{lang_code}] batch_size changed {prev_batch_size} → {batch_size}: "
                f"clearing stale batch entries …", "warning",
            )
            for old_b in state[lang_code].get("batches", []):
                old_wav = old_b.get("audio_path", "")
                if old_wav and Path(old_wav).exists():
                    try: Path(old_wav).unlink()
                    except Exception: pass
            state[lang_code]["batches"] = []

        state["batch_size"]         = batch_size
        state[lang_code]["profile"] = voice_profile.name

        # Build batch groups
        batch_groups: List[List[int]] = [
            list(range(i, min(i + batch_size, len(panels))))
            for i in range(0, len(panels), batch_size)
        ]
        total    = len(batch_groups)
        existing = {b["idx"]: b for b in state[lang_code].get("batches", [])}

        pending_indices: List[int] = []
        pending_paths:   List[str] = []
        all_wav_paths:   List[str] = []

        for batch_idx, panel_indices in enumerate(batch_groups):
            batch_wav = str(lang_folder / f"batch_{batch_idx:04d}.wav")
            all_wav_paths.append(batch_wav)
            prev = existing.get(batch_idx, {})
            if (prev.get("status") == "done"
                    and prev.get("audio_path")
                    and Path(prev["audio_path"]).exists()):
                log(f"  Batch {batch_idx + 1}/{total} already done ✓", "info")
                if on_progress:
                    on_progress(batch_idx + 1, total)
                continue
            batch_texts = [all_texts[i] for i in panel_indices if all_texts[i]]
            if not batch_texts:
                log(f"  Batch {batch_idx + 1}: no text — skipping", "warning")
                continue
            joined = self._join_segments_for_tts(batch_texts, lang_code)
            if joined.strip():
                pending_indices.append(batch_idx)
                pending_paths.append(batch_wav)

        if not pending_indices:
            log("All batches already done — concatenating …", "info")
            done_wavs = [p for p in all_wav_paths if Path(p).exists()]
            return concat_wavs(done_wavs, str(lang_folder / "_continuous.wav"))

        # Path diagnostics
        log(f"  [{lang_code}] WAV output folder  : {lang_folder}", "info")
        log(f"  [{lang_code}] First batch path   : {pending_paths[0]}", "info")
        log(f"  [{lang_code}] Folder writable    : "
            f"{'YES' if os.access(str(lang_folder), os.W_OK) else 'NO — PERMISSIONS ERROR'}",
            "info")

        # Build ONE set of sentences (one joined string per pending batch)
        sentences: List[str] = []
        for batch_idx in pending_indices:
            panel_indices = batch_groups[batch_idx]
            batch_texts   = [all_texts[i] for i in panel_indices if all_texts[i]]
            sentences.append(self._join_segments_for_tts(batch_texts, lang_code))

        # FIX 2: correct language tag for the profile
        lang_name   = config.SUPPORTED_LANGUAGES.get(lang_code, "Auto")
        gen_profile = copy(voice_profile)
        gen_profile.language = lang_name

        # FIX 7: VoiceDesign → VoiceClone pivot
        gen_profile = self._ensure_voice_design_ref(ep, lang_code, gen_profile, log)

        # Guard: a clone voice with no (or missing) reference clip can't be
        # synthesized — fail with a clear message instead of a cryptic crash
        # deep inside the TTS subprocess ("No such file or directory").
        if gen_profile.mode == "VoiceClone":
            ref = (gen_profile.ref_wav_path or "").strip()
            if not ref or not Path(ref).exists():
                log(f"Voice '{gen_profile.name}' has no reference clip "
                    f"(ref_wav_path is {'empty' if not ref else 'missing: ' + ref}). "
                    f"Open Dubbing → Voices, edit it, choose an audio clip, and Save.",
                    "error")
                return False

        # Cross-run consistency warning for VoiceClone
        done_count = sum(1 for b in existing.values() if b.get("status") == "done")
        if done_count > 0 and pending_indices and gen_profile.mode == "VoiceClone":
            log(
                f"  [{lang_code}] ⚠ resuming {len(pending_indices)} "
                f"pending batch(es) with {done_count} already-done batch(es). "
                f"VoiceClone prompt is re-extracted per subprocess run — "
                f"subtle voice drift is possible.  Delete all batches for a clean run.",
                "warning",
            )

        log(
            f"  [{lang_code}] generating {len(pending_indices)} batch(es) "
            f"in ONE subprocess (consistent voice) …", "accent",
        )

        script = synth.build_synth_script(
            profile      = gen_profile,
            sentences    = sentences,
            output_paths = pending_paths,
            skip_indices = set(),
        )

        self._stop_flag = False
        done_set: set   = set()

        try:
            proc = subprocess.Popen(
                [synth.synth_python(gen_profile), "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=synth.synth_env(gen_profile),
            )

            line_q: queue.Queue   = queue.Queue()
            stderr_q: queue.Queue = queue.Queue()

            def _reader(p=proc, q=line_q):
                for raw in p.stdout: q.put(raw)
                q.put(None)

            def _stderr_reader(p=proc, q=stderr_q):
                for raw in p.stderr: q.put(raw)
                q.put(None)

            threading.Thread(target=_reader,        daemon=True, name="dub-batch-stdout").start()
            threading.Thread(target=_stderr_reader, daemon=True, name="dub-batch-stderr").start()

            log(f"  [{lang_code}] writing batch WAVs to: {lang_folder}", "info")

            last_heartbeat = batch_start_time = time.time()
            timeout = config.DUB_CONTINUOUS_TIMEOUT

            while True:
                if self._stop_flag:
                    proc.terminate()
                    log("Stopped by user", "warning")
                    break
                try:
                    raw = line_q.get(timeout=1)
                except queue.Empty:
                    batch_elapsed = time.time() - batch_start_time
                    if time.time() - last_heartbeat >= 30:
                        log(f"    still generating … ({batch_elapsed:.0f}s since last batch)", "muted")
                        last_heartbeat = time.time()
                    # IDLE timeout: abort only if NO batch has completed in `timeout`
                    # seconds — not a total budget. A long episode with many batches
                    # keeps going as long as it makes steady progress.
                    if batch_elapsed > timeout:
                        log(f"    no batch completed in {timeout}s — aborting (stuck)", "error")
                        proc.terminate()
                        try: proc.wait(timeout=10)
                        except subprocess.TimeoutExpired: proc.kill()
                        break
                    continue
                if raw is None:
                    break
                line = raw.strip()
                if not line:
                    continue
                last_heartbeat = time.time()
                if line == "MODEL_READY":
                    log(f"    model loaded ✓", "success")
                elif line == "VOICE_READY":
                    log(f"    voice ready ✓", "success")
                elif line == "WARMUP_OK":
                    log(f"    warm-up done ✓", "success")
                elif line.startswith("WARMUP_FAIL:"):
                    log(f"    warm-up skipped (non-fatal)", "warning")
                elif line.startswith("DONE:"):
                    parts = line.split(":")
                    seq   = int(parts[1])
                    real_batch = pending_indices[seq]
                    done_set.add(seq)
                    batch_start_time = time.time()
                    if on_progress:
                        on_progress(real_batch + 1, total)
                    if on_batch_done:
                        on_batch_done(real_batch)
                    log(f"    batch {real_batch + 1}/{total} done ✓", "success")
                elif line.startswith("ERROR:"):
                    parts = line.split(":", 2)
                    seq   = int(parts[1])
                    real_batch = pending_indices[seq]
                    log(f"    batch {real_batch + 1} error: {parts[2] if len(parts) > 2 else '?'}", "error")

            stderr_lines = []
            try:
                while True:
                    item = stderr_q.get_nowait()
                    if item is None: break
                    stderr_lines.append(item.rstrip())
            except Exception:
                pass
            if stderr_lines:
                stderr_text = "\n".join(stderr_lines)
                if any(kw in stderr_text for kw in ("FATAL", "Error", "Traceback")):
                    log(f"    subprocess stderr:\n{stderr_text[-600:]}", "error")
                else:
                    log(f"    subprocess stderr (warnings):\n{stderr_text[-300:]}", "muted")

            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()

        except Exception as exc:
            log(f"    subprocess error: {exc}", "error")
            return False

        # Update batch state
        for seq, batch_idx in enumerate(pending_indices):
            panel_indices = batch_groups[batch_idx]
            wav_path      = pending_paths[seq]
            ok            = seq in done_set and Path(wav_path).exists()
            record = {
                "idx":        batch_idx,
                "panels":     panel_indices,
                "panel_from": panel_indices[0],
                "panel_to":   panel_indices[-1],
                "audio_path": wav_path if ok else "",
                "status":     "done" if ok else "failed",
                "duration":   get_wav_duration(wav_path) if ok else 0.0,
                "created_at": time.time(),
            }
            idx_positions = [b["idx"] for b in state[lang_code]["batches"]]
            if batch_idx in idx_positions:
                state[lang_code]["batches"][idx_positions.index(batch_idx)] = record
            else:
                state[lang_code]["batches"].append(record)

        save_batch_state(state_path, state)

        failed = [b for b in state[lang_code]["batches"] if b.get("status") == "failed"]
        if failed:
            log(f"  {len(failed)} batch(es) failed", "error")
            return False

        done_wavs = [
            str(lang_folder / f"batch_{b['idx']:04d}.wav")
            for b in sorted(state[lang_code]["batches"], key=lambda x: x["idx"])
            if b.get("status") == "done"
            and Path(str(lang_folder / f"batch_{b['idx']:04d}.wav")).exists()
        ]

        ok = concat_wavs(done_wavs, str(lang_folder / "_continuous.wav"))
        if ok:
            dur = get_wav_duration(str(lang_folder / "_continuous.wav"))
            log(f"  [{lang_code}] continuous audio ready — {dur:.1f}s ✓", "success")
        return ok

    def regenerate_batch(
        self,
        episode_id:   int,
        lang_code:    str,
        voice_profile,
        batch_idx:    int,
        on_log:       Callable = None,
    ) -> bool:
        """Regenerate a single batch and update _continuous.wav."""
        log = on_log or self._log
        ep  = self.db.get_episode(episode_id)
        if not ep:
            log("Episode not found", "error")
            return False

        state_path = Path(ep["output_folder"]) / "dub" / "batch_state.json"
        state      = load_batch_state(state_path)
        lang_state = state.get(lang_code, {})
        batches    = lang_state.get("batches", [])
        batch      = next((b for b in batches if b["idx"] == batch_idx), None)

        if not batch:
            log(f"Batch {batch_idx} not found in state", "error")
            return False

        lang_folder   = self._get_lang_folder(ep, lang_code)
        panel_indices = batch["panels"]
        panels        = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])
        all_texts: List[str] = []
        for panel in panels:
            if lang_code == "en":
                text = (panel.get("narration_text") or "").strip()
            else:
                row  = self.db.get_panel_audio(panel["id"], lang_code)
                text = ((row or {}).get("translated_text") or "").strip()
            all_texts.append(text)

        batch_texts = [all_texts[i] for i in panel_indices
                       if i < len(all_texts) and all_texts[i]]
        if not batch_texts:
            log(f"No text for batch {batch_idx}", "error")
            return False

        batch_wav  = lang_folder / f"batch_{batch_idx:04d}.wav"
        state_file = lang_folder / f"batch_{batch_idx:04d}_state.json"
        if batch_wav.exists():  batch_wav.unlink()
        if state_file.exists(): state_file.unlink()

        if panel_indices:
            self._delete_panel_wavs(episode_id, lang_code, panel_indices, on_log=on_log)

        batch["status"]     = "pending"
        batch["audio_path"] = ""
        save_batch_state(state_path, state)

        ok = self._generate_batch_wav(
            texts         = batch_texts,
            lang_code     = lang_code,
            voice_profile = voice_profile,
            out_path      = str(batch_wav),
            sentences_dir = lang_folder / f"batch_{batch_idx:04d}_sentences",
            state_file    = state_file,
            on_log        = on_log,
        )

        batch["status"]     = "done" if ok else "failed"
        batch["audio_path"] = str(batch_wav) if ok else ""
        batch["duration"]   = get_wav_duration(str(batch_wav)) if ok else 0.0
        save_batch_state(state_path, state)

        if not ok:
            return False

        batch_wavs = sorted(
            [b["audio_path"] for b in batches
             if b.get("status") == "done" and b.get("audio_path")],
            key=lambda p: int(Path(p).stem.split("_")[-1])
                         if Path(p).stem.split("_")[-1].isdigit() else 0,
        )
        continuous_path = str(lang_folder / "_continuous.wav")
        return concat_wavs(batch_wavs, continuous_path)

    def _delete_panel_wavs(
        self,
        episode_id:    int,
        lang_code:     str,
        panel_indices: List[int],
        on_log:        Callable = None,
    ):
        """Delete per-panel split WAV and sync WAV files for a set of panel indices."""
        log = on_log or self._log
        ep  = self.db.get_episode(episode_id)
        if not ep:
            return

        lang_folder = self._get_lang_folder(ep, lang_code)
        panels      = sorted(self.db.list_panels(episode_id), key=lambda p: p["panel_index"])
        panel_map   = {p["panel_index"]: p for p in panels}
        deleted_audio = deleted_sync = 0

        for idx in panel_indices:
            split_wav = lang_folder / f"{lang_code}_panel_{idx:04d}.wav"
            if split_wav.exists():
                try: split_wav.unlink(); deleted_audio += 1
                except Exception: pass

            sync_wav = lang_folder / f"{lang_code}_panel_{idx:04d}_sync.wav"
            if sync_wav.exists():
                try: sync_wav.unlink(); deleted_sync += 1
                except Exception: pass

            panel = panel_map.get(idx)
            if panel:
                row = self.db.get_panel_audio(panel["id"], lang_code)
                if row:
                    self.db.update_panel_audio(
                        row["id"],
                        raw_wav=None, raw_duration=None,
                        synced_wav=None, synced_duration=None, is_synced=0,
                    )

        if deleted_audio or deleted_sync:
            log(
                f"  [{lang_code}] cleaned {deleted_audio} split + "
                f"{deleted_sync} sync WAV(s) for panels {panel_indices}", "info",
            )

    def delete_batch(self, episode_id: int, lang_code: str, batch_idx: int,
                     on_log: Callable = None) -> bool:
        """Delete a batch's TTS audio file and mark it pending."""
        ep = self.db.get_episode(episode_id)
        if not ep:
            return False

        state_path = Path(ep["output_folder"]) / "dub" / "batch_state.json"
        state      = load_batch_state(state_path)
        batches    = state.get(lang_code, {}).get("batches", [])
        batch      = next((b for b in batches if b["idx"] == batch_idx), None)
        if not batch:
            return False

        audio = batch.get("audio_path", "")
        if audio and Path(audio).exists():
            try: Path(audio).unlink()
            except Exception: pass

        panel_indices = batch.get("panels", [])
        if panel_indices:
            self._delete_panel_wavs(episode_id, lang_code, panel_indices, on_log=on_log)

        batch["status"]     = "pending"
        batch["audio_path"] = ""
        batch["duration"]   = 0.0
        save_batch_state(state_path, state)
        return True

    def delete_all_batches(self, episode_id: int, lang_code: str,
                           on_log: Callable = None) -> int:
        """Delete ALL batch WAV files and panel wavs for lang_code."""
        log = on_log or self._log
        ep  = self.db.get_episode(episode_id)
        if not ep:
            return 0

        state_path = Path(ep["output_folder"]) / "dub" / "batch_state.json"
        state      = load_batch_state(state_path)
        lang_state = state.get(lang_code, {})
        batches    = lang_state.get("batches", [])
        deleted    = 0

        for batch in batches:
            wav = batch.get("audio_path", "")
            if wav and Path(wav).exists():
                try: Path(wav).unlink(); deleted += 1
                except Exception: pass

        all_panel_indices = [
            idx for batch in batches for idx in batch.get("panels", [])
        ]
        if all_panel_indices:
            self._delete_panel_wavs(episode_id, lang_code, all_panel_indices, on_log=on_log)

        lang_folder = self._get_lang_folder(ep, lang_code)
        cont_wav    = lang_folder / "_continuous.wav"
        if cont_wav.exists():
            try: cont_wav.unlink(); deleted += 1
            except Exception: pass

        if lang_code in state:
            state[lang_code] = {"profile": lang_state.get("profile", ""), "batches": []}
            save_batch_state(state_path, state)

        log(f"  [{lang_code}] all batches deleted — {deleted} WAV file(s) removed ✓", "info")
        return deleted

    def load_batch_state(self, episode_id: int) -> dict:
        """Return the full batch state dict for this episode (for UI display)."""
        ep = self.db.get_episode(episode_id)
        if not ep:
            return {}
        state_path = Path(ep["output_folder"]) / "dub" / "batch_state.json"
        return load_batch_state(state_path)

    def _generate_batch_wav(
        self,
        texts:         List[str],
        lang_code:     str,
        voice_profile,
        out_path:      str,
        sentences_dir: Path,
        state_file:    Path,
        on_log:        Callable = None,
    ) -> bool:
        """Run TTS for a list of texts, producing one WAV at out_path."""
        log = on_log or self._log

        joined = self._join_segments_for_tts(texts, lang_code)
        if not joined.strip():
            log(f"Batch produced empty joined text for '{lang_code}'", "error")
            return False

        lang_name   = config.SUPPORTED_LANGUAGES.get(lang_code, "Auto")
        gen_profile = copy(voice_profile)
        gen_profile.language = lang_name

        # FIX 7: reuse cached voice ref for regen
        if getattr(gen_profile, "mode", "") == "VoiceDesign":
            cached_ref = sentences_dir.parent / "_voice_ref.wav"
            if cached_ref.exists() and cached_ref.stat().st_size > 1000:
                base_model = gen_profile.model.replace("VoiceDesign", "Base")
                if base_model not in MODEL_PATHS:
                    base_model = "1.7B-Base"
                gen_profile               = copy(gen_profile)
                gen_profile.mode          = "VoiceClone"
                gen_profile.model         = base_model
                gen_profile.ref_wav_path  = str(cached_ref)
                gen_profile.ref_wav_text  = ""
                gen_profile.x_vector_only = True
                log(f"    [{lang_code}] reusing cached voice ref for regen ✓", "info")

        sentences_dir.mkdir(parents=True, exist_ok=True)

        script = synth.build_synth_script(
            profile      = gen_profile,
            sentences    = [joined],
            output_paths = [out_path],
            skip_indices = set(),
        )

        self._stop_flag = False

        try:
            proc = subprocess.Popen(
                [synth.synth_python(gen_profile), "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=synth.synth_env(gen_profile),
            )

            line_q: queue.Queue = queue.Queue()

            def _reader(p=proc, q=line_q):
                for raw in p.stdout: q.put(raw)
                q.put(None)

            threading.Thread(target=_reader, daemon=True, name="dub-batch-reader").start()

            start_time = last_heartbeat = time.time()
            ok = False
            timeout = config.DUB_CONTINUOUS_TIMEOUT

            while True:
                if self._stop_flag:
                    proc.terminate()
                    log("Batch stopped by user", "warning")
                    break
                try:
                    raw = line_q.get(timeout=1)
                except queue.Empty:
                    elapsed = time.time() - start_time
                    if time.time() - last_heartbeat >= 30:
                        log(f"    still generating … ({elapsed:.0f}s)", "muted")
                        last_heartbeat = time.time()
                    if elapsed > timeout:
                        log(f"    timed out after {elapsed:.0f}s", "error")
                        proc.terminate()
                        try: proc.wait(timeout=10)
                        except subprocess.TimeoutExpired: proc.kill()
                        break
                    continue
                if raw is None:
                    break
                line = raw.strip()
                if not line:
                    continue
                last_heartbeat = time.time()
                if line == "MODEL_READY":
                    log(f"    model loaded ✓", "success")
                elif line == "VOICE_READY":
                    log(f"    voice ready ✓", "success")
                elif line == "WARMUP_OK":
                    log(f"    warm-up done ✓", "success")
                elif line.startswith("WARMUP_FAIL:"):
                    log(f"    warm-up skipped (non-fatal)", "warning")
                elif line.startswith("DONE:"):
                    ok = True
                    log(f"    generation done ✓", "success")
                elif line.startswith("ERROR:"):
                    parts = line.split(":", 2)
                    log(f"    error: {parts[2] if len(parts) > 2 else '?'}", "error")

            stderr_out = proc.stderr.read().strip()
            if stderr_out and "FATAL" in stderr_out:
                log(f"    fatal: {stderr_out[-300:]}", "error")
                return False
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()

        except Exception as exc:
            log(f"    subprocess error: {exc}", "error")
            return False

        if not ok or not Path(out_path).exists():
            log(f"    WAV not produced", "error")
            return False

        normalize_wav(out_path)
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # CONTROL & LOGGING
    # ══════════════════════════════════════════════════════════════════════════

    def stop(self):
        """Signal the current phase to abort after its next panel."""
        self._stop_flag = True

    def _log(self, msg: str, level: str = "info"):
        if self.on_log:
            self.on_log(msg, level)
        else:
            print(f"[DUB] {msg}")