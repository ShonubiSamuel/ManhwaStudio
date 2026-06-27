"""
speech/aligner.py — lay dubbed cue clips onto one track at the source's timing.

This is what makes the dub sound natural instead of "few words … long silence":

  • Each cue's clip is placed at its SOURCE start time.
  • The time available to a cue runs until the NEXT cue starts — so a clip that's
    a little long simply breathes into the natural pause instead of being rushed.
  • Only a clip that overruns that window is time-compressed (capped), and only
    as a last resort truncated.
  • The gaps between cues are the source's own pauses — real breaths, not invented
    dead air — and a short fade on each clip removes clicks / abrupt pickups.

    assemble_track([{ "path", "start" }, ...], total_duration, out_path)
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

import config


def _load(path: str):
    import soundfile as sf
    y, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    return y, sr


def _fade(y, sr: int, in_ms: float = 30.0, out_ms: float = 60.0):
    import numpy as np
    ni = int(sr * in_ms / 1000.0)
    no = int(sr * out_ms / 1000.0)
    if ni > 0 and len(y) > ni:
        y[:ni] *= np.linspace(0.0, 1.0, ni, dtype="float32")
    if no > 0 and len(y) > no:
        y[-no:] *= np.linspace(1.0, 0.0, no, dtype="float32")
    return y


def _trim_silence(y, sr: int, thr_db: float = -38.0, guard_ms: float = 15.0):
    """Strip leading/trailing near-silence so the SPEECH starts at the clip's
    origin. This is what makes a cue land exactly on its panel/start time instead
    of a beat late (the pieces otherwise carry ~half a pause on each end)."""
    import numpy as np
    if len(y) == 0:
        return y
    fl = max(1, int(sr * 0.01))          # 10 ms frames
    n = len(y) // fl
    if n < 2:
        return y
    env = np.sqrt(np.mean(y[:n * fl].reshape(n, fl) ** 2, axis=1) + 1e-12)
    peak = float(env.max())
    if peak <= 0:
        return y
    thr = peak * (10.0 ** (thr_db / 20.0))
    v = np.where(env > thr)[0]
    if len(v) == 0:
        return y
    guard = int(sr * guard_ms / 1000.0)
    a = max(0, v[0] * fl - guard)
    b = min(len(y), (v[-1] + 1) * fl + guard)
    return y[a:b]


def _normalize(y, target_rms: float = 0.12, max_gain: float = 6.0):
    """Bring every cue to a consistent loudness so the track doesn't jump
    cue-to-cue (a major source of 'inconsistent' dub). Peak-limited to avoid
    clipping."""
    import numpy as np
    rms = float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0
    if rms > 1e-5:
        y = y * min(target_rms / rms, max_gain)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0.99:
        y = y * (0.99 / peak)
    return y.astype("float32")


def assemble_track(
    placements:     List[dict],
    total_duration: float,
    out_path:       str,
    sr:             Optional[int] = None,
    max_stretch:    Optional[float] = None,
    fade_ms:        float = 15.0,
    on_log:         Optional[Callable] = None,
) -> bool:
    """
    placements: [{"path": wav, "start": seconds}, ...] in time order.
    Writes a single track of length total_duration with each clip placed at its
    start, compressed only if it overruns the gap to the next cue.
    """
    import numpy as np
    import runtime_settings as rs
    log = on_log or (lambda *a, **k: None)
    # Cap compression to a quality-safe rate — beyond this any stretcher gets
    # robotic, so we'd rather a clip ride a little into the pause.
    max_stretch = float(max_stretch if max_stretch is not None
                        else rs.get_float("dub_speech_max_stretch",
                                          getattr(config, "DUB_SPEECH_MAX_STRETCH", 1.3))) or 1.3
    fade_in  = rs.get_float("dub_fade_in_ms",  getattr(config, "DUB_FADE_IN_MS", 25))
    fade_out = rs.get_float("dub_fade_out_ms", getattr(config, "DUB_FADE_OUT_MS", 80))
    # Clean gap kept between a cue's end and the next cue's start, so a line can
    # never bleed onto the next one (overlap = two voices = the "broken" sound).
    min_gap = rs.get_float("dub_speech_min_gap", getattr(config, "DUB_SPEECH_MIN_GAP", 0.06))

    clips = []
    for p in placements:
        path = p.get("path")
        if path and Path(path).exists():
            try:
                clips.append(_load(path))
            except Exception:
                clips.append(None)
        else:
            clips.append(None)

    sr = sr or next((c[1] for c in clips if c), 24000)
    n  = len(placements)
    canvas = np.zeros(int(round(total_duration * sr)) + 1, dtype="float32")

    compressed = 0
    for i, (p, clip) in enumerate(zip(placements, clips)):
        if clip is None:
            continue
        y, csr = clip
        if csr != sr:                       # keep it simple: TTS clips share sr
            log(f"  cue {i}: sample-rate {csr}≠{sr}, placing as-is", "muted")
        start = float(p["start"])
        nxt   = float(placements[i + 1]["start"]) if i + 1 < n else float(total_duration)
        slot  = max(0.05, nxt - start)      # time until the next cue speaks

        # Trim the silence padding off each piece so the SPEECH lands exactly on
        # the cue's start time (fixes the "starts a beat late" / last-cue-cut-off
        # feel, and removes the start-of-track hiccup).
        y   = _trim_silence(y, sr)
        dur = len(y) / sr

        # NEVER slow a clip down — stretching to fill a gap is what made cues
        # sound dragged ("Saluuut les gaaars"). Natural speed + a real pause is
        # how good dubs sound. We ONLY compress, and only to keep a line from
        # bleeding onto the NEXT cue. Target = the slot minus a small clean gap,
        # so consecutive cues never overlap. Capped so it's not robotic.
        note = "as-is"
        allow = max(0.05, slot - min_gap)
        if dur > allow:
            rate = min(max_stretch, dur / allow)
            if rate > 1.02:
                try:
                    import pyrubberband as pyrb
                    # crispness 6 = best transient preservation → cleaner speech.
                    y = pyrb.time_stretch(y, sr, rate, rbargs={"-c": "6"})
                    compressed += 1
                    note = f"sped {rate:.2f}×" + (" (capped — may overrun)" if dur / allow > max_stretch + 0.01 else "")
                except Exception as exc:
                    log(f"  cue {i}: stretch failed ({exc}) — placing as-is", "warning")
                    note = "as-is(no stretch)"

        gap = (nxt - (start + len(y) / sr))
        log(f"   place cue {i + 1:02d} @ {start:6.2f}s  {dur:.1f}s→slot {slot:.1f}s  "
            f"[{note}]  then {max(0.0, gap):.1f}s pause", "muted")

        # NO per-piece normalization: every piece comes from ONE continuous read
        # at one level, so they're already consistent. Normalizing each piece
        # would boost the quiet/short ones and amplify artifacts (what made the
        # dub sound worse than the raw TTS). Only a gentle fade to avoid clicks.
        y  = _fade(y, sr, fade_in, fade_out)
        si = int(round(start * sr))
        ei = min(len(canvas), si + len(y))
        if ei > si:
            canvas[si:ei] += y[: ei - si]

    # One gentle peak limit on the whole track (prevents clipping from any
    # overlap) — preserves the natural dynamics of the read.
    peak = float(np.max(np.abs(canvas))) if len(canvas) else 0.0
    if peak > 0.99:
        canvas = canvas * (0.99 / peak)

    # Declick the very start and end of the whole track so it can never open or
    # close on a non-zero sample (the sharp "blow" you heard at the start).
    edge = int(sr * 0.012)
    if len(canvas) > edge * 2 and edge > 0:
        canvas[:edge]  *= np.linspace(0.0, 1.0, edge, dtype="float32")
        canvas[-edge:] *= np.linspace(1.0, 0.0, edge, dtype="float32")

    try:
        import soundfile as sf
        sf.write(str(out_path), canvas, sr, subtype="PCM_16")
    except Exception as exc:
        log(f"assemble_track write failed: {exc}", "error")
        return False
    log(f"Assembled {n} cue(s) → {Path(out_path).name} "
        f"({total_duration:.1f}s, {compressed} compressed)", "info")
    return True
