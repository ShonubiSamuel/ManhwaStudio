"""
speech/separate.py — split narration from background music/SFX (Demucs).

For a video with music, we dub only the VOICE and keep the original music. Demucs
(`--two-stems=vocals`) splits the audio into vocals.wav + no_vocals.wav; we keep
no_vocals.wav as the background to re-mix the dub over.

    separate_background(audio_path, out_dir) -> background_wav | None

Returns None if Demucs isn't available or fails — the caller then dubs without a
background (clean narration only). Demucs is heavy (PyTorch); it must be installed
in DEMUCS_PYTHON's environment (`pip install demucs`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

import config


def separate_background(
    audio_path: str,
    out_dir:    str,
    on_log:     Optional[Callable] = None,
) -> Optional[str]:
    log = on_log or (lambda *a, **k: None)
    import runtime_settings as rs
    py    = rs.get_str("demucs_python", config.DEMUCS_PYTHON)
    model = rs.get_str("demucs_model",  getattr(config, "DEMUCS_MODEL", "htdemucs"))

    src = Path(audio_path)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    log(f"Separating vocals/background with Demucs ({model}) — this can take a while …", "info")
    try:
        r = subprocess.run(
            [py, "-m", "demucs", "--two-stems=vocals", "-n", model,
             "-o", str(out), str(src)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3600,
        )
    except Exception as exc:
        log(f"Demucs unavailable ({exc}) — dubbing without background", "warning")
        return None
    if r.returncode != 0:
        log(f"Demucs failed — dubbing without background.\n{(r.stderr or '')[-400:]}", "warning")
        return None

    bg = out / model / src.stem / "no_vocals.wav"
    if bg.exists():
        log("Background (no_vocals) extracted ✓", "success")
        return str(bg)
    log("Demucs produced no background track — dubbing without it", "warning")
    return None
