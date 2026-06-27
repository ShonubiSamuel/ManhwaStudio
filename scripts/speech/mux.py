"""
speech/mux.py — put the dubbed audio back onto the original video.

The video stream is copied untouched and the audio track is replaced with the
dubbed (and optionally re-mixed) track. Because the dub follows the source's
speech timing, the visuals stay in sync automatically — no re-rendering, no
panel re-cutting.

    mux(video_path, audio_path, out_path) -> bool
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional


def mux(
    video_path: str,
    audio_path: str,
    out_path:   str,
    on_log:     Optional[Callable] = None,
) -> bool:
    log = on_log or (lambda *a, **k: None)
    if not Path(video_path).exists():
        log(f"mux: video not found: {video_path}", "error"); return False
    if not Path(audio_path).exists():
        log(f"mux: audio not found: {audio_path}", "error"); return False
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", str(out_path)],
            capture_output=True, text=True, timeout=1800,
        )
    except Exception as exc:
        log(f"mux ffmpeg error: {exc}", "error"); return False
    if r.returncode != 0 or not Path(out_path).exists():
        log(f"mux failed.\n{(r.stderr or '')[-400:]}", "error"); return False
    log(f"Muxed dubbed audio into {Path(out_path).name} ✓", "success")
    return True
