"""
speech/remix.py — combine the dubbed narration with the source background.

If there's a background track (from separate.py) we mix the dub OVER it with
ffmpeg (which resamples both to a common rate, applies gains, and overlays). If
there's no background (music toggle off, or separation failed/clean source) we
just hand back the dub as-is.

    remix(dub_wav, background_wav | None, out_wav) -> bool
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

import config


def remix(
    dub_path:        str,
    background_path: Optional[str],
    out_path:        str,
    voice_gain:      Optional[float] = None,
    music_gain:      Optional[float] = None,
    on_log:          Optional[Callable] = None,
) -> bool:
    log = on_log or (lambda *a, **k: None)
    import runtime_settings as rs
    voice_gain = rs.get_float("dub_voice_gain", config.DUB_VOICE_GAIN) if voice_gain is None else voice_gain
    music_gain = rs.get_float("dub_music_gain", config.DUB_MUSIC_GAIN) if music_gain is None else music_gain

    # No background → the dub IS the final audio.
    if not background_path or not Path(background_path).exists():
        try:
            shutil.copy(str(dub_path), str(out_path))
            return True
        except Exception as exc:
            log(f"remix copy failed: {exc}", "error")
            return False

    filt = (
        f"[0:a]volume={voice_gain},aresample=48000[v];"
        f"[1:a]volume={music_gain},aresample=48000[m];"
        f"[v][m]amix=inputs=2:duration=longest:normalize=0[a]"
    )
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(dub_path), "-i", str(background_path),
             "-filter_complex", filt, "-map", "[a]", "-ar", "48000", str(out_path)],
            capture_output=True, text=True, timeout=600,
        )
    except Exception as exc:
        log(f"remix ffmpeg error: {exc}", "error")
        return False
    if r.returncode != 0 or not Path(out_path).exists():
        log(f"remix failed.\n{(r.stderr or '')[-400:]}", "error")
        return False
    log("Re-mixed dub over background music ✓", "success")
    return True
