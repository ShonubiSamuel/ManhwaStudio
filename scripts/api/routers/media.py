"""
scripts/api/routers/media.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Stream an arbitrary local source media file to the UI.

The /files mount (main.py) only exposes OUTPUT_DIR, so it cannot serve the
*source* video/audio the user picks in Dub Studio (those live in input/ or
anywhere on disk).  Dub Studio's <video> needs a real, seekable URL, so this
router streams any readable file by absolute path.

    GET /api/media?path=<absolute-path>

Starlette's FileResponse honours the HTTP `Range` header automatically and
replies 206 Partial Content, which is what makes the <video> scrubber able to
seek without downloading the whole file first.

Security note
─────────────
This is a localhost-only desktop app (CORS is restricted to the Vite dev
origin, the server binds 127.0.0.1).  The user already supplies arbitrary
absolute source paths through the picker, so reading those same paths back is
not a new capability.  We still reject directories and non-existent paths.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(tags=["Media"])


@router.get("/media")
def stream_media(path: str = Query(..., description="Absolute path to a media file")):
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise HTTPException(400, "path must be absolute")
    try:
        if not p.is_file():
            raise HTTPException(404, f"File not found: {p}")
    except OSError as exc:
        raise HTTPException(400, f"Cannot access path: {exc}")

    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    # FileResponse adds Accept-Ranges and serves 206 for Range requests.
    return FileResponse(str(p), media_type=media_type, filename=p.name)
