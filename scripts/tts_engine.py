"""
tts_engine.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Qwen3 TTS engine — one continuous WAV per language per episode.

VoiceProfile, VoiceProfileManager, and the script-building utilities have
moved to dedicated modules.  This file re-exports them for backward compat
so any code that does  `from tts_engine import VoiceProfile`  still works.

Re-exported from tts.voice_profile:
    VoiceProfile, VoiceProfileManager

Re-exported from tts.script_builder:
    split_script, _esc, _build_chapter_script, _subprocess_env,
    CONDA_PYTHON, MODEL_PATHS, AVAILABLE_MODELS, PRESET_SPEAKERS,
    LANGUAGES, RECOMMENDED_MODELS, _WARMUP_TEXTS
"""

import json
import subprocess
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import config

# ── Re-exports for backward compatibility ─────────────────────────────────────

from tts.voice_profile import VoiceProfile, VoiceProfileManager   # noqa: F401
from tts.script_builder import (                                    # noqa: F401
    split_script,
    _esc,
    _build_chapter_script,
    _subprocess_env,
    CONDA_PYTHON,
    MODEL_PATHS,
    AVAILABLE_MODELS,
    PRESET_SPEAKERS,
    LANGUAGES,
    RECOMMENDED_MODELS,
    _WARMUP_TEXTS,
)
from core.audio_utils import normalize_wav                          # noqa: F401


# ── TTS Engine ────────────────────────────────────────────────────────────────

class TTSEngine:
    """
    Episode-level TTS engine.  Generates one continuous WAV per language.

    Runs one subprocess per language generation.
    The model loads once, voice fingerprint is extracted once,
    all sentences generated in the same session — guaranteed voice consistency.

    Folder structure (per language):
        {output_folder}/tts/{lang_code}/sentences/sentence_NNNN.wav
        {output_folder}/tts/{lang_code}/chapter_audio.wav
        {output_folder}/tts/{lang_code}/tts_state.json
    """

    def __init__(
        self,
        db,
        episode_id:    int,
        lang_code:     str,
        output_folder: str,
        profile:       VoiceProfile,
        on_progress:   Optional[Callable] = None,
        on_log:        Optional[Callable] = None,
    ):
        self.db            = db
        self.episode_id    = episode_id
        self.lang_code     = lang_code
        self.output_folder = Path(output_folder)
        self.profile       = profile
        self.on_progress   = on_progress
        self.on_log        = on_log

        self.tts_folder  = self.output_folder / "tts" / lang_code
        self.raw_folder  = self.tts_folder / "sentences"
        self.final_path  = self.tts_folder / "chapter_audio.wav"
        self.state_path  = self.tts_folder / "tts_state.json"

        self.raw_folder.mkdir(parents=True, exist_ok=True)

        self._sentences: list = []
        self._generated: set  = set()
        self._stop_flag       = False
        self._pause_flag      = False
        self._proc            = None

        self._load_state()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def load_from_db(self) -> int:
        """
        Load narration texts from the database for this episode and language.

        FIX 4: Panel texts are kept as separate list items — they are never
        joined with '. ' which would corrupt CJK sentence boundaries.
        """
        panels = self.db.list_panels(self.episode_id)
        if not panels:
            self._log("No panels found in DB", "error")
            return 0

        panels_sorted = sorted(panels, key=lambda p: p["panel_index"])
        texts = []

        for panel in panels_sorted:
            if self.lang_code == "en":
                text = (panel.get("narration_text") or "").strip()
            else:
                audio_row = self.db.get_panel_audio(panel["id"], self.lang_code)
                text = ((audio_row or {}).get("translated_text") or "").strip()
            if text:
                texts.append(text)

        if not texts:
            self._log(
                f"No narration text found for language '{self.lang_code}' — "
                f"run translate stage first",
                "error",
            )
            return 0

        self._log(f"Loaded {len(texts)} panel text(s) from DB for '{self.lang_code}'", "info")
        return self.set_sentences(texts)

    def load_script(self, script_path: str = None) -> int:
        """Load sentences from a plain text script file."""
        if not script_path:
            script_path = str(self.output_folder / "ai_narrator" / "script.txt")
        path = Path(script_path)
        if not path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")
        self._sentences = split_script(path.read_text(encoding="utf-8"))
        self._save_state()
        return len(self._sentences)

    def set_sentences(self, sentences: list) -> int:
        """Set sentences directly from a list, splitting each through split_script."""
        split: list = []
        for text in sentences:
            if text.strip():
                split.extend(split_script(text))
        self._sentences = split
        self._save_state()
        return len(self._sentences)

    # ── Generation ────────────────────────────────────────────────────────────

    def generate_all(self) -> bool:
        """
        Run one subprocess that loads the model once and generates all
        pending sentences.  Returns True on success.
        """
        if not self._sentences:
            self._log("No sentences loaded — call load_from_db() first", "error")
            return False

        log_id = self.db.log_stage_start(self.episode_id, "tts")
        self.db.set_episode_stage(self.episode_id, "tts", "running")

        self._stop_flag  = False
        self._pause_flag = False
        total            = len(self._sentences)

        already_done: set = set()
        for i in self._generated:
            wav = self.raw_folder / f"sentence_{i:04d}.wav"
            if wav.exists() and wav.stat().st_size > 1000:
                already_done.add(i)

        pending = [i for i in range(total) if i not in already_done]

        if not pending:
            self._log("All sentences already generated — merging", "info")
            success = self.merge_audio()
            if success:
                self._finalise_stage(log_id)
            else:
                self.db.set_episode_stage(self.episode_id, "tts", "failed", error="Merge failed")
                self.db.log_stage_end(log_id, "failed", error="Merge failed")
            return success

        self._log(
            f"Starting generation: {len(pending)} sentence(s), "
            f"{len(already_done)} already done  (lang={self.lang_code})", "info")
        self._log("Loading model (this takes ~30s) …", "info")

        all_paths = [
            str(self.raw_folder / f"sentence_{i:04d}.wav")
            for i in range(total)
        ]

        script = _build_chapter_script(
            profile      = self.profile,
            sentences    = self._sentences,
            output_paths = all_paths,
            skip_indices = already_done,
        )

        failed = []

        try:
            # FIX 5: force UTF-8 env + explicit encoding so CJK text survives macOS locale
            self._proc = subprocess.Popen(
                [CONDA_PYTHON, "-c", script],
                stdout   = subprocess.PIPE,
                stderr   = subprocess.PIPE,
                text     = True,
                encoding = "utf-8",
                errors   = "replace",
                bufsize  = 1,
                env      = _subprocess_env(),
            )

            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if self._stop_flag:
                    self._proc.terminate()
                    self._log("Generation stopped by user", "warning")
                    break

                if line == "LOADING_MODEL":
                    self._log("Loading model …", "info")
                elif line == "MODEL_READY":
                    self._log("Model loaded ✓", "success")
                elif line == "VOICE_READY":
                    self._log("Voice fingerprint ready ✓", "success")
                elif line == "WARMUP_OK":
                    self._log("Warm-up complete ✓", "success")
                elif line.startswith("WARMUP_FAIL:"):
                    msg = line.split(":", 1)[1] if ":" in line else line
                    self._log(f"Warm-up failed (non-fatal): {msg}", "warning")
                elif line.startswith("DONE:"):
                    i = int(line.split(":")[1])
                    self._generated.add(i)
                    already_done.add(i)
                    self._save_state()
                    done_count = len(already_done)
                    self._log(f"  Sentence {i + 1}/{total} done", "info")
                    self._progress(done_count, total)
                elif line.startswith("SKIP:"):
                    i          = int(line.split(":")[1])
                    done_count = len(already_done)
                    self._progress(done_count, total)
                elif line == "ALL_DONE":
                    self._log("All sentences generated ✓", "success")
                elif line.startswith("ERROR:"):
                    parts = line.split(":", 2)
                    i     = int(parts[1])
                    msg   = parts[2] if len(parts) > 2 else "unknown error"
                    self._log(f"  Sentence {i + 1} failed: {msg}", "error")
                    failed.append(i)

            stderr_output = self._proc.stderr.read().strip()
            if stderr_output and "FATAL:" in stderr_output:
                self._log(f"Fatal error: {stderr_output[-400:]}", "error")

            self._proc.wait()
            self._proc = None

        except Exception as exc:
            self._log(f"Subprocess error: {exc}", "error")
            self._proc = None
            error = str(exc)
            self.db.set_episode_stage(self.episode_id, "tts", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

        if failed:
            error = f"{len(failed)} sentences failed"
            self.db.set_episode_stage(self.episode_id, "tts", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

        if self._stop_flag:
            self.db.set_episode_stage(self.episode_id, "tts", "failed", error="Cancelled by user")
            self.db.log_stage_end(log_id, "failed", error="Cancelled by user")
            return False

        success = self.merge_audio()
        if success:
            self._finalise_stage(log_id)
        else:
            self.db.set_episode_stage(self.episode_id, "tts", "failed", error="Merge failed")
            self.db.log_stage_end(log_id, "failed", error="Merge failed")
        return success

    def generate_sentence(self, index: int) -> bool:
        """Regenerate a single sentence. Used for individual regen from UI."""
        if index >= len(self._sentences):
            return False

        sentence = self._sentences[index]
        out_path = str(self.raw_folder / f"sentence_{index:04d}.wav")

        script = _build_chapter_script(
            profile      = self.profile,
            sentences    = [sentence],
            output_paths = [out_path],
            skip_indices = set(),
        )

        try:
            # FIX 5: UTF-8 env + explicit encoding
            result = subprocess.run(
                [CONDA_PYTHON, "-c", script],
                capture_output = True,
                text           = True,
                encoding       = "utf-8",
                errors         = "replace",
                timeout        = 300,
                env            = _subprocess_env(),
            )
            success = (result.returncode == 0 and "DONE:0" in result.stdout)
            if success:
                self._generated.add(index)
                self._save_state()
            else:
                error = result.stderr.strip()[-300:]
                self._log(f"Regen failed: {error}", "error")
            return success
        except subprocess.TimeoutExpired:
            self._log("Regen timed out", "error")
            return False
        except Exception as exc:
            self._log(f"Regen error: {exc}", "error")
            return False

    def stop(self):
        """Signal generation to stop after the current sentence finishes."""
        self._stop_flag = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def pause(self):
        self._pause_flag = True

    def resume(self):
        self._pause_flag = False
        self._stop_flag  = False

    # ── Merge ─────────────────────────────────────────────────────────────────

    def merge_audio(self) -> bool:
        """
        Concatenate all sentence WAV files into one continuous chapter audio.
        Normalises each clip with normalize_wav from core.audio_utils,
        then concatenates with 0.4s silence between sentences.
        """
        wav_files = sorted(self.raw_folder.glob("sentence_*.wav"))
        if not wav_files:
            self._log("No audio files to merge", "error")
            return False

        try:
            # Normalize each clip in-place before concatenating
            for wav_path in wav_files:
                normalize_wav(str(wav_path))

            all_frames: list = []
            sample_rate      = None
            channels         = None
            sampwidth        = None

            for wav_path in wav_files:
                with wave.open(str(wav_path), "rb") as wf:
                    if sample_rate is None:
                        sample_rate = wf.getframerate()
                        channels    = wf.getnchannels()
                        sampwidth   = wf.getsampwidth()
                    all_frames.append(wf.readframes(wf.getnframes()))

            # 0.4s silence between sentences
            silence = (b"\x00" * sampwidth) * (int(sample_rate * 0.4) * channels)

            with wave.open(str(self.final_path), "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(sampwidth)
                out.setframerate(sample_rate)
                for i, frames in enumerate(all_frames):
                    out.writeframes(frames)
                    if i < len(all_frames) - 1:
                        out.writeframes(silence)

            self._log(f"Audio saved → {self.final_path.name}", "success")
            return True

        except Exception as exc:
            self._log(f"Merge failed: {exc}", "error")
            return False

    # ── Stage finalisation ────────────────────────────────────────────────────

    def _finalise_stage(self, log_id: int):
        self.db.set_episode_stage(
            self.episode_id, "tts", "done", output_path=str(self.final_path))
        self.db.log_stage_end(
            log_id, "done",
            metadata={
                "lang_code":   self.lang_code,
                "audio_path":  str(self.final_path),
                "n_sentences": len(self._sentences),
            })
        self.db.set_episode_tts_audio(self.episode_id, self.lang_code, str(self.final_path))

    # ── State ─────────────────────────────────────────────────────────────────

    def _save_state(self):
        self.state_path.write_text(json.dumps({
            "episode_id": self.episode_id,
            "lang_code":  self.lang_code,
            "sentences":  self._sentences,
            "generated":  list(self._generated),
            "saved_at":   time.time(),
        }, indent=2), encoding="utf-8")

    def _load_state(self):
        if not self.state_path.exists():
            return
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._sentences = s.get("sentences", [])
            self._generated = set(s.get("generated", []))
        except Exception:
            pass

    def _progress(self, current: int, total: int):
        if self.on_progress:
            self.on_progress(current, total)

    def _log(self, msg: str, level: str = "info"):
        if self.on_log:
            self.on_log(msg, level)
        else:
            print(f"[TTS:{self.lang_code}] {msg}")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def total_sentences(self) -> int:
        return len(self._sentences)

    @property
    def generated_count(self) -> int:
        return len(self._generated)

    @property
    def progress_pct(self) -> int:
        if not self._sentences:
            return 0
        return round((self.generated_count / len(self._sentences)) * 100)

    @property
    def is_complete(self) -> bool:
        return (
            bool(self._sentences)
            and self.generated_count == len(self._sentences)
            and self.final_path.exists()
        )

    def summary(self) -> dict:
        return {
            "lang_code": self.lang_code,
            "total":     self.total_sentences,
            "generated": self.generated_count,
            "progress":  self.progress_pct,
            "complete":  self.is_complete,
            "audio":     str(self.final_path) if self.final_path.exists() else None,
        }
