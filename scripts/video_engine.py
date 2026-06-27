"""
video_engine.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Pipeline A engine — processes an existing video file into database panels.

Three independently resumable stages
─────────────────────────────────────
  detect     Audio silence + visual scene detection run in parallel threads.
             Three merge modes give full control over which signals drive cuts.
             Panel rows written to the database with timestamps.
             cuts_state.json saved to the episode output folder as a
             resumable backup — re-running detect loads it automatically.

  extract    faster-whisper transcribes the episode audio in safe N-minute
             chunks.  Each chunk's word timestamps are offset back to absolute
             time, then every word is assigned to the panel whose time range
             contains it.  transcript_text saved per panel in the database.
             whisper_words.json saved to disk so transcription is never re-run
             unnecessarily.

  screenshot One ffmpeg frame grab per panel at (start_time + offset).
             Screenshot JPEG saved to {output_folder}/panels/panel_NNNN.jpg.
             Path stored in panels.screenshot_path.  Already-extracted frames
             are skipped automatically.

All three stages:
  • Accept on_log(message, level) and on_progress(current, total) callbacks.
  • Respect self._stop_flag for graceful mid-stage cancellation.
  • Write stage status and progress to the database.

Reference: auto_segment_pro.py (~80%), make_panel_pdf.py (screenshot logic)
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import config

from detection_utils import (
    detect_silence_ffmpeg as _det_silence,
    detect_visual_frames  as _det_visual,
    merge_signals         as _merge_sigs,
)


# ── Detection parameters dataclass ────────────────────────────────────────────

@dataclass
class DetectionParams:
    """
    All tunable parameters for Panel detection, transcript extraction, and
    screenshot grabbing.  Defaults come from config.py — each episode can
    carry its own overrides without touching global settings.
    """
    # ── Signal detection ──────────────────────────────────────────────────────
    mode:            str   = field(default_factory=lambda: config.DETECT_MODE)
    silence_db:      float = field(default_factory=lambda: config.DETECT_SILENCE_DB)
    min_silence_sec: float = field(default_factory=lambda: config.DETECT_MIN_SILENCE)
    visual_threshold: float = field(default_factory=lambda: config.DETECT_THRESHOLD)
    min_scene_sec:   float = field(default_factory=lambda: config.DETECT_MIN_SCENE)
    frame_skip:      int   = field(default_factory=lambda: config.DETECT_FRAME_SKIP)
    merge_window:    float = field(default_factory=lambda: config.DETECT_MERGE_WINDOW)
    priority:        str   = field(default_factory=lambda: config.DETECT_PRIORITY)
    workers:         int   = field(default_factory=lambda: config.DETECT_WORKERS)
    # ── Transcript extraction ─────────────────────────────────────────────────
    whisper_model:   str   = field(default_factory=lambda: config.WHISPER_MODEL)
    chunk_min:       int   = field(default_factory=lambda: config.WHISPER_CHUNK_MIN)
    # ── Screenshot extraction ─────────────────────────────────────────────────
    screenshot_offset: float = field(default_factory=lambda: config.SCREENSHOT_OFFSET)


# ── VideoEngine class ──────────────────────────────────────────────────────────

def detection_params_from_episode(ep: dict) -> "DetectionParams":
    """
    Build a DetectionParams from a DB episode row.
    Falls back to config.py defaults for any missing column.
    """
    return DetectionParams(
        mode             = ep.get("detect_mode")         or config.DETECT_MODE,
        silence_db       = float(ep.get("detect_silence_db")   or config.DETECT_SILENCE_DB),
        min_silence_sec  = float(ep.get("detect_min_silence")  or config.DETECT_MIN_SILENCE),
        visual_threshold = float(ep.get("detect_threshold")    or config.DETECT_THRESHOLD),
        min_scene_sec    = float(ep.get("detect_min_scene")    or config.DETECT_MIN_SCENE),
        frame_skip       = int(ep.get("detect_frame_skip")     or config.DETECT_FRAME_SKIP),
        merge_window     = float(ep.get("detect_merge_window") or config.DETECT_MERGE_WINDOW),
        priority         = ep.get("detect_priority")     or config.DETECT_PRIORITY,
        workers          = int(ep.get("detect_workers")        or config.DETECT_WORKERS),
    )


class VideoEngine:
    """
    Processes a video episode through three stages: detect, extract, screenshot.
    All database writes go through the injected db object.
    All user feedback goes through on_log / on_progress callbacks.
    """

    #: File extensions treated as video (require audio extraction for Whisper).
    _VIDEO_EXTS: frozenset = frozenset(
        {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts"}
    )

    def __init__(self, db, output_base: str, on_log: Callable = None):
        self.db          = db
        self.output_base = Path(output_base)
        self.on_log      = on_log
        self._stop_flag  = False

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC STAGE METHODS
    # ══════════════════════════════════════════════════════════════════════════

    def detect_on_clip(
        self,
        source_path:  str,
        clip_path:    str,
        params:       DetectionParams = None,
        on_log:       Callable = None,
    ) -> List[dict]:
        """
        Run detection on a short clip and return a list of panel dicts with
        start_time_sec, end_time_sec, duration_sec — without touching the DB.
        Used by the DETECT stage preview (Step 3).

        Returns list of panel dicts, or empty list on failure.
        """
        log = on_log or self._log
        p   = params or DetectionParams()
        try:
            cuts_sec = self._run_detection(clip_path, p)
        except Exception as exc:
            log(f"Clip detection error: {exc}", "error")
            return []

        if not cuts_sec:
            cuts_sec = []

        # Build panel boundary list from cuts
        clip_duration = self._get_duration(clip_path)
        boundaries    = [0.0] + sorted(cuts_sec) + [clip_duration]
        panels: List[dict] = []
        for i in range(len(boundaries) - 1):
            s = boundaries[i]
            e = boundaries[i + 1]
            if e - s < 0.1:
                continue
            panels.append({
                "panel_index":   i,
                "start_time_sec": round(s, 3),
                "end_time_sec":   round(e, 3),
                "duration_sec":   round(e - s, 3),
                "transcript_text": "",
            })
        log(f"Clip detection: {len(panels)} panel(s) found — transcribing …",
            "success")

        # ── Run Whisper on the clip to get transcripts ────────────────────────
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(
                p.whisper_model, device="cpu", compute_type="int8"
            )
            segs, _ = model.transcribe(
                clip_path,
                language        = "en",
                word_timestamps = True,
                beam_size       = 5,
                vad_filter      = True,
                vad_parameters  = {"min_silence_duration_ms": 300},
            )
            words: List[Dict] = []
            for seg in segs:
                if seg.words:
                    for w in seg.words:
                        words.append({
                            "word":  w.word,
                            "start": round(w.start, 3),
                            "end":   round(w.end,   3),
                        })
            transcripts = VideoEngine._build_panel_transcripts(panels, words)
            for panel in panels:
                panel["transcript_text"] = transcripts.get(
                    panel["panel_index"], "").strip()
            n_words = len(words)
            log(f"Clip transcription: {n_words} word(s) assigned ✓", "success")
        except Exception as exc:
            log(f"Clip transcription skipped: {exc}", "warning")
            for panel in panels:
                panel["transcript_text"] = ""

        return panels

    def extract_clip(
        self,
        source_path: str,
        out_path:    str,
        start:       str  = "00:00:00",
        duration:    int  = 120,
        on_log:      Callable = None,
    ) -> bool:
        """
        Extract a short clip from source_path using ffmpeg.
        start    — HH:MM:SS or seconds as string
        duration — clip length in seconds
        Returns True on success.
        """
        import subprocess
        log = on_log or self._log
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", str(source_path),
                "-t", str(duration),
                "-c", "copy",
                str(out_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                log(f"ffmpeg clip error: {result.stderr[-300:]}", "error")
                return False
            log(f"Clip extracted → {Path(out_path).name} ✓", "success")
            return True
        except Exception as exc:
            log(f"Extract clip failed: {exc}", "error")
            return False

    def detect_panels(
        self,
        episode_id:  int,
        params:      DetectionParams = None,
        on_progress: Callable = None,
    ) -> bool:
        """
        Stage: detect
        ──────────────
        1. Extracts audio from the source video (cached after first run).
        2. Runs silence and/or visual detection (mode-dependent).
        3. Merges signals into a final sorted cut list.
        4. Writes panel rows to the database (clears any previous panels first).
        5. Saves cuts_state.json to the episode output folder.

        If cuts_state.json already exists, step 2–3 are skipped entirely and
        the saved cut list is used directly.

        on_progress(current, total) is called as panel rows are written.
        Returns True on success, False on cancellation or error.
        """
        p  = params or DetectionParams()
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        source_path   = ep["source_path"]
        output_folder = Path(ep["output_folder"])
        output_folder.mkdir(parents=True, exist_ok=True)

        log_id = self.db.log_stage_start(episode_id, "detect")
        self.db.set_episode_stage(episode_id, "detect", "running")
        self._stop_flag = False

        try:
            # ── Force audio mode for non-video sources ────────────────────────
            is_video = Path(source_path).suffix.lower() in self._VIDEO_EXTS
            if not is_video and p.mode in ("visual", "combined"):
                self._log(
                    "Audio-only source detected — switching to audio detection mode",
                    "warning",
                )
                p.mode = "audio"

            # ── Resume from backup if available ───────────────────────────────
            state_path = output_folder / "cuts_state.json"
            cuts_sec   = self._load_cuts_backup(state_path)

            if cuts_sec is not None:
                self._log(
                    f"Resumed from cuts_state.json — {len(cuts_sec)} cut(s) loaded",
                    "info",
                )
            else:
                # ── Run detection ─────────────────────────────────────────────
                self._log(f"Detecting panel boundaries (mode={p.mode}) …", "accent")
                t_start  = time.time()
                cuts_sec = self._run_detection(source_path, p)

                if self._stop_flag:
                    return self._abort(episode_id, log_id, "detect")

                if not cuts_sec:
                    raise RuntimeError(
                        "No panel cuts detected.\n"
                        "  Suggestions: lower --silence-db (e.g. -35), "
                        "reduce --min-silence, or lower --threshold"
                    )

                elapsed = round(time.time() - t_start, 1)
                self._log(f"Detection complete in {elapsed}s", "info")

                # Backup immediately in case downstream processing fails
                audio_path_for_backup = str(
                    output_folder / f"{Path(source_path).stem}_audio.mp3"
                )
                self._save_cuts_backup(state_path, cuts_sec, audio_path_for_backup)
                self._log(f"Backup saved → {state_path.name}", "muted")

            # ── Build boundaries and measure duration ─────────────────────────
            duration   = self._get_duration(source_path)
            n_panels   = len(cuts_sec) + 1
            boundaries = [0.0] + sorted(cuts_sec) + [duration]

            self._log(f"{len(cuts_sec)} cut(s) → {n_panels} panel(s)", "success")
            self.db.update_episode(episode_id, duration_secs=round(duration, 3))

            # ── Clear any existing panels (fresh detection = fresh panels) ────
            existing = self.db.list_panels(episode_id)
            if existing:
                self._log(
                    f"Clearing {len(existing)} existing panel(s) "
                    f"(and their associated audio) for fresh detection …",
                    "warning",
                )
                for panel in existing:
                    self.db.delete_panel(panel["id"])

            # ── Write panel rows ──────────────────────────────────────────────
            panels_folder = output_folder / "panels"
            panels_folder.mkdir(parents=True, exist_ok=True)

            self._log("Writing panel rows to database …", "info")
            for i in range(n_panels):
                if self._stop_flag:
                    return self._abort(episode_id, log_id, "detect")

                start = round(boundaries[i],     6)
                end   = round(boundaries[i + 1], 6)
                dur   = round(end - start,        6)

                self.db.add_panel(
                    episode_id     = episode_id,
                    panel_index    = i,
                    start_time_sec = start,
                    end_time_sec   = end,
                    duration_sec   = dur,
                )

                if on_progress:
                    on_progress(i + 1, n_panels)

            # ── Finalise ──────────────────────────────────────────────────────
            self.db.set_episode_stage(
                episode_id, "detect", "done",
                output_path = str(state_path),
            )
            self.db.log_stage_end(log_id, "done",
                                  metadata={"panels": n_panels, "cuts": len(cuts_sec)})
            self._log(f"Detect complete — {n_panels} panel(s) ✓", "success")
            return True

        except Exception as exc:
            error = str(exc)
            self._log(f"Detect failed: {error}", "error")
            self.db.set_episode_stage(episode_id, "detect", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

    # ──────────────────────────────────────────────────────────────────────────

    def extract_transcript(
        self,
        episode_id:  int,
        params:      DetectionParams = None,
        on_progress: Callable = None,
    ) -> bool:
        """
        Stage: extract
        ──────────────
        1. Extracts audio from the source video (cached after first run).
        2. Transcribes audio with faster-whisper in safe chunk_min-minute slices.
        3. Assigns every word to the panel whose time range contains it.
        4. Saves transcript_text per panel in the database.
        5. Saves whisper_words.json to disk.

        If whisper_words.json already exists, transcription is skipped and
        the saved word list is used directly.

        Must be called after detect_panels — requires panel rows with timestamps.
        on_progress(current, total) is called as panel transcripts are written.
        Returns True on success, False on cancellation or error.
        """
        p  = params or DetectionParams()
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        source_path   = ep["source_path"]
        output_folder = Path(ep["output_folder"])
        panels        = self.db.list_panels(episode_id)

        if not panels:
            self._log(
                "No panels found for this episode — run detect_panels first",
                "error",
            )
            return False

        log_id = self.db.log_stage_start(episode_id, "extract")
        self.db.set_episode_stage(episode_id, "extract", "running")
        self._stop_flag = False

        try:
            # ── Ensure MP3 audio exists for Whisper ───────────────────────────
            audio_path = self._ensure_audio(source_path, output_folder)
            if not audio_path:
                raise RuntimeError("Audio extraction failed")

            # ── Resume from word backup if available ──────────────────────────
            words_path = output_folder / "whisper_words.json"
            words      = self._load_words_backup(words_path)

            if words is not None:
                self._log(
                    f"Resumed from whisper_words.json — {len(words)} word(s) loaded",
                    "info",
                )
            else:
                # ── Run faster-whisper (chunked) ──────────────────────────────
                self._log(
                    f"Transcribing with faster-whisper "
                    f"(model={p.whisper_model}, chunk={p.chunk_min} min) …",
                    "accent",
                )
                words = self._transcribe_chunked(
                    audio_path    = audio_path,
                    model_size    = p.whisper_model,
                    chunk_min     = p.chunk_min,
                    output_folder = output_folder,
                )

                if self._stop_flag:
                    return self._abort(episode_id, log_id, "extract")

                words_path.write_text(
                    json.dumps(words, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                self._log(
                    f"Word list backup saved → {words_path.name}", "muted"
                )

            # ── Match words → panels ──────────────────────────────────────────
            self._log(
                f"Matching {len(words)} word(s) to {len(panels)} panel(s) …",
                "info",
            )
            panel_texts = self._build_panel_transcripts(panels, words)

            for i, panel in enumerate(panels):
                if self._stop_flag:
                    return self._abort(episode_id, log_id, "extract")

                text = panel_texts.get(panel["panel_index"], "").strip()
                self.db.update_panel(panel["id"], transcript_text=text)

                if on_progress:
                    on_progress(i + 1, len(panels))

            # ── Finalise ──────────────────────────────────────────────────────
            self.db.set_episode_stage(
                episode_id, "extract", "done",
                output_path = str(output_folder / "panels"),
            )
            self.db.log_stage_end(
                log_id, "done",
                metadata={"words": len(words), "panels": len(panels)},
            )
            self._log("Transcript extraction complete ✓", "success")
            return True

        except Exception as exc:
            error = str(exc)
            self._log(f"Extract failed: {error}", "error")
            self.db.set_episode_stage(episode_id, "extract", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

    # ──────────────────────────────────────────────────────────────────────────

    def extract_screenshots(
        self,
        episode_id:  int,
        params:      DetectionParams = None,
        on_progress: Callable = None,
    ) -> bool:
        """
        Grabs one frame per panel at (start_time + screenshot_offset) and
        saves it as {output_folder}/panels/panel_NNNN.jpg.

        This method is independent of stage tracking — it updates individual
        panel rows (screenshot_path) without touching episode stage columns.
        Panels that already have a valid screenshot file are skipped.

        Must be called after detect_panels — requires panel rows with timestamps.
        on_progress(current, total) called after each panel.
        Returns True if at least one screenshot was saved, False on total failure.
        """
        p  = params or DetectionParams()
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        source_path   = ep["source_path"]
        output_folder = Path(ep["output_folder"])
        panels        = self.db.list_panels(episode_id)

        if not panels:
            self._log(
                "No panels found for this episode — run detect_panels first",
                "error",
            )
            return False

        panels_folder = output_folder / "panels"
        panels_folder.mkdir(parents=True, exist_ok=True)

        total  = len(panels)
        done   = 0
        failed = 0
        self._stop_flag = False

        self._log(f"Extracting {total} screenshot(s) …", "accent")

        for i, panel in enumerate(panels):
            if self._stop_flag:
                self._log("Screenshot extraction cancelled", "warning")
                break

            start    = panel.get("start_time_sec") or 0.0
            duration = panel.get("duration_sec")   or 0.0
            idx      = panel["panel_index"]

            # Grab frame at offset, but never past halfway into the panel
            grab_t   = start + min(p.screenshot_offset, max(duration * 0.5, 0.01))
            out_path = str(panels_folder / f"panel_{idx:04d}.jpg")

            # Skip if already extracted and non-empty
            if Path(out_path).exists() and Path(out_path).stat().st_size > 500:
                self.db.update_panel(panel["id"], screenshot_path=out_path)
                done += 1
            elif self._extract_frame(source_path, grab_t, out_path):
                self.db.update_panel(panel["id"], screenshot_path=out_path)
                done += 1
            else:
                self._log(f"  Panel {idx + 1}: screenshot failed", "warning")
                failed += 1

            if on_progress:
                on_progress(i + 1, total)

        # Update panels_folder on the episode once all screenshots are done
        if done > 0:
            self.db.update_episode(episode_id, panels_folder=str(panels_folder))

        self._log(
            f"Screenshots: {done} saved, {failed} failed",
            "success" if failed == 0 else "warning",
        )
        return done > 0

    # ──────────────────────────────────────────────────────────────────────────

    def stop(self):
        """Signal the running stage to cancel after its current unit of work."""
        self._stop_flag = True

    # ══════════════════════════════════════════════════════════════════════════
    # SIGNAL DETECTION  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _run_detection(
        self,
        source_path: str,
        p:           DetectionParams,
    ) -> List[float]:
        """
        Dispatch to audio, visual, or combined detection based on p.mode.
        Returns a sorted list of cut timestamps in seconds.
        """
        if p.mode == "audio":
            self._log(
                "Audio silence via ffmpeg silencedetect "
                "(streaming, zero RAM load) …", "info",
            )
            silences = self._detect_silence_ffmpeg(
                source_path, p.min_silence_sec, p.silence_db
            )
            self._log(f"{len(silences)} silence region(s) found", "info")
            return sorted((s + e) / 2.0 for s, e in silences)

        elif p.mode == "visual":
            self._log(
                f"Visual scene detection "
                f"(threshold={p.visual_threshold}, "
                f"frame_skip={p.frame_skip}) …", "info",
            )
            cuts = self._detect_visual_fast(
                source_path, p.visual_threshold, p.min_scene_sec, p.frame_skip
            )
            self._log(f"{len(cuts)} visual transition(s) found", "info")
            return cuts

        else:  # combined — run both in parallel threads
            self._log(
                "Running audio silence + visual scene detection in parallel …",
                "info",
            )
            silences:    List[Tuple[float, float]] = []
            visual_cuts: List[float]               = []

            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="detect") as ex:
                fut_sil = ex.submit(
                    self._detect_silence_ffmpeg,
                    source_path, p.min_silence_sec, p.silence_db,
                )
                fut_vis = ex.submit(
                    self._detect_visual_fast,
                    source_path, p.visual_threshold, p.min_scene_sec, p.frame_skip,
                )
                silences    = fut_sil.result()
                visual_cuts = fut_vis.result()

            self._log(
                f"Silence regions: {len(silences)}  |  "
                f"Visual transitions: {len(visual_cuts)}",
                "info",
            )

            merged  = self._merge_signals(
                silences, visual_cuts, p.merge_window, p.priority
            )
            dropped = sum(
                1 for v in visual_cuts
                if not any(abs(v - c) < p.merge_window for c in merged)
            )
            self._log(
                f"Merged: {len(merged)} cut(s)  |  "
                f"{dropped} zoom/pan artifact(s) dropped",
                "info",
            )
            return merged

    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_silence_ffmpeg(source_path, min_silence_sec, silence_db):
        return _det_silence(source_path, min_silence_sec, silence_db)

    # ──────────────────────────────────────────────────────────────────────────

    def _detect_visual_fast(self, video_path, threshold, min_scene_sec, frame_skip):
        _, _, cuts = _det_visual(
            video_path, threshold, min_scene_sec, frame_skip,
            should_stop = lambda: self._stop_flag,
            on_log      = self._log,
        )
        return cuts

    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_signals(silences, visual_cuts, merge_window, priority="combined"):
        return _merge_sigs(silences, visual_cuts, merge_window, priority)

    # ══════════════════════════════════════════════════════════════════════════
    # TRANSCRIPTION  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _transcribe_chunked(
        self,
        audio_path:    str,
        model_size:    str,
        chunk_min:     int,
        output_folder: Path,
    ) -> List[Dict]:
        """
        Split audio into chunk_min-minute pieces, transcribe each with
        faster-whisper, then offset every word's timestamp back to absolute
        time.  Chunk temp files are deleted immediately to keep disk use low.

        Returns a flat list of word dicts:
            [{"word": str, "start": float, "end": float}, …]
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed.\n"
                "Fix: pip install faster-whisper"
            )

        duration  = self._get_duration(audio_path)
        chunk_sec = chunk_min * 60.0
        n_chunks  = max(1, math.ceil(duration / chunk_sec))

        self._log(
            f"Audio: {self._fmt_time(duration)}  |  "
            f"{n_chunks} chunk(s) × {chunk_min} min  |  model: {model_size}",
            "info",
        )

        model: WhisperModel = WhisperModel(
            model_size, device="cpu", compute_type="int8"
        )
        all_words: List[Dict] = []

        try:
            with tempfile.TemporaryDirectory(dir=str(output_folder)) as tmp:
                for i in range(n_chunks):
                    if self._stop_flag:
                        break

                    t_start = i * chunk_sec
                    t_end   = min(t_start + chunk_sec, duration)
                    if t_start >= duration:
                        break

                    remaining_h = (duration - t_start) / 3600
                    self._log(
                        f"Chunk {i + 1}/{n_chunks}  "
                        f"[{self._fmt_time(t_start)} → {self._fmt_time(t_end)}]  "
                        f"({remaining_h:.2f} h remaining) …",
                        "info",
                    )

                    chunk_path = str(Path(tmp) / f"chunk_{i:04d}.mp3")

                    subprocess.run([
                        "ffmpeg",
                        "-i",  audio_path,
                        "-ss", str(t_start),
                        "-to", str(t_end),
                        "-acodec", "libmp3lame",
                        "-ab",     "128k",
                        "-y",      chunk_path,
                    ], capture_output=True, check=True)

                    segs, _ = model.transcribe(
                        chunk_path,
                        language         = "en",
                        word_timestamps  = True,
                        beam_size        = 5,
                        vad_filter       = True,
                        vad_parameters   = {"min_silence_duration_ms": 300},
                    )
                    chunk_words = 0
                    for seg in segs:
                        if seg.words:
                            for w in seg.words:
                                if w.word.strip():
                                    all_words.append({
                                        "word":  w.word,
                                        "start": round(w.start + t_start, 3),
                                        "end":   round(w.end   + t_start, 3),
                                    })
                                    chunk_words += 1

                    self._log(
                        f"  Chunk {i + 1} → {chunk_words} word(s)", "muted"
                    )

        finally:
            del model  # release GPU/MPS memory immediately

        self._log(
            f"{len(all_words)} word(s) transcribed across {n_chunks} chunk(s)",
            "success",
        )
        return all_words

    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_panel_transcripts(
        panels: List[Dict],
        words:  List[Dict],
    ) -> Dict[int, str]:
        """
        Assign each word to the panel whose time range contains it.

        Assignment rule:
            A word belongs to panel i  if  start_time_sec ≤ word.start < end_time_sec.
            The last panel absorbs all remaining words regardless of end time.
            Words before the first panel's start_time go to panel 0.

        Returns {panel_index: transcript_text}.
        """
        if not panels:
            return {}

        # Sort panels by index — should already be sorted, but guarantee it
        sorted_panels = sorted(panels, key=lambda p: p["panel_index"])

        # Initialise empty buckets for every panel
        buckets: Dict[int, List[str]] = {
            p["panel_index"]: [] for p in sorted_panels
        }

        if not words:
            return {idx: "" for idx in buckets}

        w_idx   = 0
        n_words = len(words)

        for panel_pos, panel in enumerate(sorted_panels):
            end     = panel.get("end_time_sec") or float("inf")
            idx     = panel["panel_index"]
            is_last = (panel_pos == len(sorted_panels) - 1)

            while w_idx < n_words:
                word_t = words[w_idx]["start"]

                if is_last:
                    # Last panel absorbs everything that remains
                    buckets[idx].append(words[w_idx]["word"])
                    w_idx += 1
                elif word_t < end:
                    buckets[idx].append(words[w_idx]["word"])
                    w_idx += 1
                else:
                    break  # This word belongs to the next panel

        # Join and normalise whitespace per panel
        return {
            idx: re.sub(r" +", " ", " ".join(word_list)).strip()
            for idx, word_list in buckets.items()
        }

    # ══════════════════════════════════════════════════════════════════════════
    # AUDIO & FRAME HELPERS  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _ensure_audio(
        self,
        source_path:   str,
        output_folder: Path,
    ) -> Optional[str]:
        """
        If source_path is a video file, extract and cache a 192k MP3 alongside
        the episode output.  If it is already an audio file, return it unchanged.

        Returns the path to the audio file as a string, or None on failure.
        """
        suffix = Path(source_path).suffix.lower()
        if suffix not in self._VIDEO_EXTS:
            return source_path  # already an audio file

        stem       = Path(source_path).stem
        audio_path = output_folder / f"{stem}_audio.mp3"

        if audio_path.exists():
            self._log("Audio already extracted — skipping", "muted")
            return str(audio_path)

        self._log("Extracting audio from video …", "info")
        try:
            subprocess.run([
                "ffmpeg",
                "-i",      source_path,
                "-vn",
                "-acodec", "libmp3lame",
                "-ab",     "192k",
                "-ar",     "44100",
                "-y",      str(audio_path),
            ], capture_output=True, check=True)
            self._log(f"Audio saved → {audio_path.name}", "success")
            return str(audio_path)
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode(errors="replace")[-300:] if exc.stderr else ""
            self._log(f"Audio extraction failed: {err}", "error")
            return None

    @staticmethod
    def _extract_frame(
        video_path: str,
        timestamp:  float,
        out_path:   str,
    ) -> bool:
        """
        Extract one video frame at the given timestamp via ffmpeg.
        Returns True if the output file was created with non-zero size.
        """
        r = subprocess.run([
            "ffmpeg",
            "-ss",       f"{timestamp:.3f}",
            "-i",        video_path,
            "-frames:v", "1",
            "-q:v",      "2",     # JPEG quality scale (2 = high quality)
            "-y",        out_path,
        ], capture_output=True)
        return (
            r.returncode == 0
            and Path(out_path).exists()
            and Path(out_path).stat().st_size > 500
        )

    @staticmethod
    def _get_duration(path: str) -> float:
        """
        Return media duration in seconds via ffprobe.
        Returns 0.0 on any error so callers are safe without try/except.
        """
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", path],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                return 0.0
            return float(json.loads(r.stdout)["format"]["duration"])
        except Exception:
            return 0.0

    # ══════════════════════════════════════════════════════════════════════════
    # STATE PERSISTENCE  (private)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _save_cuts_backup(
        path:       Path,
        cuts_sec:   List[float],
        audio_path: str,
    ) -> None:
        """Write cuts_state.json — same format as auto_segment_pro.py."""
        path.write_text(
            json.dumps(
                {"cuts_sec": cuts_sec, "audio_path": audio_path},
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _load_cuts_backup(path: Path) -> Optional[List[float]]:
        """
        Load cuts_state.json.  Accepts both the dict format
        {"cuts_sec": [...]} and a bare list.
        Returns None if the file does not exist or cannot be parsed.
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("cuts_sec"), list):
                return data["cuts_sec"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return None

    @staticmethod
    def _load_words_backup(path: Path) -> Optional[List[Dict]]:
        """
        Load whisper_words.json.
        Returns None if the file does not exist or cannot be parsed.
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _abort(self, episode_id: int, log_id: int, stage: str) -> bool:
        """
        Mark a stage as failed due to user cancellation.
        Always returns False so callers can do: return self._abort(...)
        """
        self.db.set_episode_stage(
            episode_id, stage, "failed", error="Cancelled by user"
        )
        self.db.log_stage_end(log_id, "failed", error="Cancelled by user")
        self._log("Cancelled", "warning")
        return False

    def _log(self, msg: str, level: str = "info") -> None:
        if self.on_log:
            self.on_log(msg, level)
        else:
            print(f"[VideoEngine] {msg}")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h, r = divmod(int(seconds), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"