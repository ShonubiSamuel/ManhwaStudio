"""
detection_utils.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Single source of truth for all panel-detection primitives.

Imported by both video_engine.py and visualize_params.py so that identical
algorithm implementations (and therefore identical parameter semantics) are
guaranteed across the pipeline engine and the interactive tuner tool.

All functions are pure — no database access, no UI state, no global side
effects beyond subprocess calls to ffmpeg/ffprobe.

Public API
──────────
    get_media_duration(path)                         → float
    get_audio_rms(audio_path, duration, chunk_ms)    → (times, rms)
    detect_silence_ffmpeg(source, min_sec, db)       → [(start, end), ...]
    detect_visual_frames(video, thr, min_scene, ...)  → (times, scores, cuts)
    merge_signals(silences, visual_cuts, window, pri) → [cut_times]
"""

from __future__ import annotations

import array
import json
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple


# ── Media duration ─────────────────────────────────────────────────────────────

def get_media_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe.  Raises RuntimeError on failure."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed on '{Path(path).name}': {r.stderr.strip()[-200:]}"
        )
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Audio RMS — waveform display only ─────────────────────────────────────────

def get_audio_rms(
    audio_path: str,
    duration:   Optional[float] = None,
    chunk_ms:   int             = 150,
) -> Tuple[List[float], List[float]]:
    """
    Stream audio through ffmpeg at 8 kHz mono and compute RMS per chunk.

    Used exclusively for waveform visualisation in the parameter tuner —
    the interactive browser chart needs a compact numeric array, not silence
    events.  The actual silence *detection* is done by detect_silence_ffmpeg.

    Never loads the full file into RAM.  Safe for any length.
    Returns (times_sec, rms_values).
    """
    sr  = 8000
    cmd = ["ffmpeg", "-i", audio_path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-af", f"aresample={sr}", "-f", "s16le", "-ac", "1", "-"]

    r = subprocess.run(cmd, capture_output=True)
    try:
        raw = array.array("h")
        raw.frombytes(r.stdout)
    except Exception:
        return [], []

    chunk_n = max(1, int(sr * chunk_ms / 1000))
    times, rms = [], []
    for i in range(0, len(raw) - chunk_n, chunk_n):
        c = raw[i : i + chunk_n]
        v = (sum(x * x for x in c) / len(c)) ** 0.5 / 32768.0
        times.append(round(i / sr, 2))
        rms.append(round(v, 5))
    return times, rms


# ── Audio silence detection ────────────────────────────────────────────────────

def detect_silence_ffmpeg(
    source_path:     str,
    min_silence_sec: float,
    silence_db:      float,
) -> List[Tuple[float, float]]:
    """
    Run ffmpeg's silencedetect filter on source_path.

    Audio is never decoded into Python RAM — works on both video and audio
    files of any size.  Returns a list of (start_sec, end_sec) tuples, one
    per detected silence region.

    Parameters
    ----------
    source_path     : path to any ffmpeg-readable file
    min_silence_sec : minimum duration (s) for a region to be reported
    silence_db      : volume threshold in dBFS — quieter = silence
                      typical range: -20 (permissive) to -55 (strict)
    """
    cmd = [
        "ffmpeg", "-i", source_path,
        "-af", f"silencedetect=noise={silence_db}dB:duration={min_silence_sec}",
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)

    silences: List[Tuple[float, float]] = []
    pending_start: Optional[float]      = None

    for line in r.stderr.splitlines():
        if "silence_start" in line:
            m = re.search(r"silence_start: ([\d.e+\-]+)", line)
            if m:
                pending_start = float(m.group(1))
        elif "silence_end" in line and pending_start is not None:
            m = re.search(r"silence_end: ([\d.e+\-]+)", line)
            if m:
                silences.append((pending_start, float(m.group(1))))
                pending_start = None

    return silences


# ── Visual frame-difference detection ─────────────────────────────────────────

def detect_visual_frames(
    video_path:    str,
    threshold:     float,
    min_scene_sec: float,
    frame_skip:    int,
    max_duration:  Optional[float] = None,
    should_stop:   Optional[Callable[[], bool]] = None,
    on_log:        Optional[Callable[[str, str], None]] = None,
) -> Tuple[List[float], List[float], List[float]]:
    """
    Adaptive frame-difference detector using OpenCV.

    Algorithm — identical in both video_engine and the parameter tuner so
    that threshold values transfer directly between the two tools:
      • Resize each sampled frame to 160×90 grayscale.
      • Mean absolute pixel difference from the previous frame (diff).
      • Rolling window of the last 15 diff values.
      • score = (diff − rolling_mean) / rolling_std
      • Accept score ≥ threshold with a gap of ≥ min_scene_sec since last cut.

    Parameters
    ----------
    should_stop  : callable () → bool, optional
                   Checked before each frame group; stops early when True.
                   Pass ``lambda: self._stop_flag`` from VideoEngine, or None.
    on_log       : callable (message: str, level: str), optional
                   Progress messages every 10 % of frames.

    Returns
    -------
    frame_times  : timestamp of every scored frame (for chart display)
    frame_scores : adaptive score at that timestamp  (for chart display)
    cut_times    : timestamps that pass threshold + gap filter (panel cuts)
    """
    def _log(msg: str, level: str = "info") -> None:
        if on_log:
            on_log(msg, level)

    try:
        import cv2
    except ImportError:
        raise RuntimeError(
            "OpenCV is required for visual detection.\n"
            "Fix: pip install opencv-python-headless"
        )

    cap     = cv2.VideoCapture(video_path)
    fps     = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_duration:
        total_f = min(total_f, int(max_duration * fps))
    step        = frame_skip + 1
    WINDOW      = 15
    total_steps = max(1, total_f // step)

    window_vals:  List[float] = []
    frame_times:  List[float] = []
    frame_scores: List[float] = []
    prev_gray     = None
    fi            = 0
    last_pct      = -1

    while fi < total_f:
        if should_stop and should_stop():
            break

        ret, frame = cap.read()
        if not ret:
            break

        if fi % step == 0:
            small = cv2.resize(frame, (160, 90))
            gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(float)

            if prev_gray is not None:
                diff = float(abs(gray - prev_gray).mean())
                window_vals.append(diff)
                if len(window_vals) > WINDOW:
                    window_vals.pop(0)
                if len(window_vals) > 2:
                    n     = len(window_vals) - 1
                    mean  = sum(window_vals[:n]) / n
                    std   = (sum((x - mean) ** 2 for x in window_vals[:n]) / n) ** 0.5
                    score = (diff - mean) / max(std, 0.1)
                    frame_times.append(round(fi / fps, 2))
                    frame_scores.append(round(score, 3))

            prev_gray = gray
            pct = min(99, (fi // step) * 100 // total_steps)
            if pct >= last_pct + 10:
                _log(f"  Visual scan: {pct}% …", "muted")
                last_pct = pct

        fi += 1

    cap.release()
    _log(f"  Visual scan: 100%  ({len(frame_scores)} frames scored)", "muted")

    # Apply threshold + minimum-scene-gap filter
    cut_times: List[float] = []
    last_cut = -9999.0
    for t, score in zip(frame_times, frame_scores):
        if score >= threshold and t - last_cut >= min_scene_sec:
            cut_times.append(t)
            last_cut = t

    return frame_times, frame_scores, cut_times


# ── Signal merge ───────────────────────────────────────────────────────────────

def merge_signals(
    silences:     List[Tuple[float, float]],
    visual_cuts:  List[float],
    merge_window: float,
    priority:     str = "combined",
) -> List[float]:
    """
    Combine silence regions and visual cuts into one final sorted cut list.

    combined (default)
        visual + nearby silence  → KEEP  (confirmed panel change)
        visual + no silence      → DROP  (zoom/pan artefact)
        long silence (≥ 0.4 s) with no visual match → KEEP  (subtle blend)
        short silence, no visual match              → DROP  (breath/pause)

    visual_first
        Every visual cut is treated as a real boundary.
        Silence only snaps each cut to the nearest quiet moment.
        Audio-only silences are ignored entirely.

    audio_first
        Narrator pauses are the sole driver of cuts.
        Visual signal is ignored entirely.
    """
    s_mids: List[float] = [(s + e) / 2.0 for s, e in silences]
    s_durs: List[float] = [e - s          for s, e in silences]

    # ── audio_first ───────────────────────────────────────────────────────────
    if priority == "audio_first":
        merged: List[float] = []
        for t in sorted(s_mids):
            if not merged or t - merged[-1] > 0.5:
                merged.append(t)
        return merged

    # ── visual_first ──────────────────────────────────────────────────────────
    if priority == "visual_first":
        confirmed: List[float] = []
        for v_cut in visual_cuts:
            best_i, best_d = None, float("inf")
            for i, sm in enumerate(s_mids):
                d = abs(v_cut - sm)
                if d < best_d:
                    best_d = d
                    best_i = i
            # Snap to nearest silence if within merge_window, else use visual time
            if best_i is not None and best_d <= merge_window:
                confirmed.append(s_mids[best_i])
            else:
                confirmed.append(v_cut)
        confirmed.sort()
        merged = []
        for t in confirmed:
            if not merged or t - merged[-1] > 0.4:
                merged.append(t)
        return merged

    # ── combined (equal weight) ───────────────────────────────────────────────
    accepted: List[float] = []
    used_sil: set         = set()

    for v_cut in visual_cuts:
        best_i, best_d = None, float("inf")
        for i, sm in enumerate(s_mids):
            d = abs(v_cut - sm)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is not None and best_d <= merge_window:
            accepted.append(s_mids[best_i])
            used_sil.add(best_i)

    for i, (sm, dur) in enumerate(zip(s_mids, s_durs)):
        if i not in used_sil and dur >= 0.4:
            accepted.append(sm)

    accepted.sort()
    merged = []
    for t in accepted:
        if not merged or t - merged[-1] > 0.5:
            merged.append(t)
    return merged
