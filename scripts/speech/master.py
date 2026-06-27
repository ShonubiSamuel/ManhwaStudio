"""
speech/master.py — final "voice mastering" pass on the assembled dub track.

Raw TTS is dark, boomy, uneven and hot. Pro dubs (e.g. Maestra) are bright,
controlled and level-normalised. This applies the standard broadcast dialogue
chain with one ffmpeg pass:

  high-pass (kill rumble/boom)
    → de-mud cut ~300 Hz
    → presence/clarity boost ~3 kHz   (consonants = intelligibility)
    → gentle air shelf
    → mild compression (even out dynamics)
    → loudness normalise (EBU R128) to a dialogue target with true-peak headroom
    → resample to 48 kHz (removes the 24 kHz format ceiling)

    master(in_path, out_path) -> bool

Falls back gracefully (caller keeps the unmastered track) if ffmpeg fails.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

import config


def _params():
    import runtime_settings as rs
    return dict(
        lufs = rs.get_float("dub_master_lufs", getattr(config, "DUB_MASTER_LUFS", -16.0)),
        tp   = rs.get_float("dub_master_tp",   getattr(config, "DUB_MASTER_TP", -1.5)),
        hp   = rs.get_float("dub_master_highpass_hz", getattr(config, "DUB_MASTER_HIGHPASS_HZ", 100)),
        bassg= rs.get_float("dub_master_bass_cut_db", getattr(config, "DUB_MASTER_BASS_CUT_DB", -4.0)),
        mud  = rs.get_float("dub_master_mud_cut_db",  getattr(config, "DUB_MASTER_MUD_CUT_DB", -2.0)),
        pres = rs.get_float("dub_master_presence_db", getattr(config, "DUB_MASTER_PRESENCE_DB", 5.0)),
        clar = rs.get_float("dub_master_clarity_db",  getattr(config, "DUB_MASTER_CLARITY_DB", 2.5)),
        air  = rs.get_float("dub_master_air_db",      getattr(config, "DUB_MASTER_AIR_DB", 3.0)),
    )


def _eq_chain() -> str:
    """Tone-shaping only (no loudnorm) — brighten + control the boomy low end of
    the raw TTS so the voice reads clear instead of muffled."""
    p = _params()
    return ",".join([
        f"highpass=f={p['hp']:g}",                          # kill rumble
        f"bass=g={p['bassg']:g}:f=200",                     # tame boom (low shelf)
        f"equalizer=f=300:t=q:w=1.1:g={p['mud']:g}",        # de-mud low-mids
        f"equalizer=f=3200:t=q:w=1.5:g={p['pres']:g}",      # presence (consonants)
        f"equalizer=f=5000:t=q:w=1.8:g={p['clar']:g}",      # clarity
        f"treble=g={p['air']:g}:f=7000",                    # air
        "acompressor=threshold=-20dB:ratio=2.0:attack=12:release=140",
    ])


def _measure_loudnorm(in_path: str, eq: str, lufs: float, tp: float, log) -> Optional[dict]:
    """Pass 1 of two-pass loudnorm: measure the EQ'd signal so pass 2 can hit the
    target accurately (single-pass overshoots by a couple dB)."""
    af = f"{eq},loudnorm=I={lufs:g}:TP={tp:g}:LRA=11:print_format=json"
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(in_path),
                            "-af", af, "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
    except Exception as exc:
        log(f"master measure error: {exc}", "warning")
        return None
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def master(in_path: str, out_path: str, on_log: Optional[Callable] = None) -> bool:
    log = on_log or (lambda *a, **k: None)
    if not Path(in_path).exists():
        log(f"master: input not found: {in_path}", "error")
        return False
    if not getattr(config, "DUB_MASTER_ENABLE", True):
        return False
    import runtime_settings as rs
    p   = _params()
    eq  = _eq_chain()
    sr  = int(rs.get_float("dub_master_sr", getattr(config, "DUB_MASTER_SR", 48000)) or 48000)

    # Two-pass loudnorm for accurate loudness; fall back to single-pass.
    meas = _measure_loudnorm(in_path, eq, p["lufs"], p["tp"], log)
    if meas:
        ln = (f"loudnorm=I={p['lufs']:g}:TP={p['tp']:g}:LRA=11"
              f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
              f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
              f":offset={meas['target_offset']}:linear=true")
    else:
        ln = f"loudnorm=I={p['lufs']:g}:TP={p['tp']:g}:LRA=11"

    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(in_path), "-af", f"{eq},{ln}",
             "-ar", str(sr), str(out_path)],
            capture_output=True, text=True, timeout=600,
        )
    except Exception as exc:
        log(f"master ffmpeg error: {exc}", "error")
        return False
    if r.returncode != 0 or not Path(out_path).exists():
        log(f"master failed.\n{(r.stderr or '')[-400:]}", "error")
        return False
    log(f"Mastered dub (EQ + compression + {'2-pass ' if meas else ''}loudness-normalise + {sr//1000} kHz) ✓", "success")
    return True
