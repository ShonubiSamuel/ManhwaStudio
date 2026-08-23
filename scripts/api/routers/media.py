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
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
import config

router = APIRouter(tags=["Media"])


@router.post("/media/import")
async def import_media(request: Request, filename: str = Query(..., description="Original file name")):
    """Accept a locally selected browser file and return its stable local path.

    Native pywebview can reveal an absolute path, but normal/in-app browsers
    deliberately cannot. This raw-body upload keeps the same path-based media
    reader working in both environments without requiring ``python-multipart``.
    """
    safe_name = Path(filename).name.strip()
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(400, "A file name is required")
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Please select a PDF file")

    dest_dir = Path(config.OUTPUT_DIR) / "imports"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex[:12]}_{safe_name}"
    size = 0
    try:
        with dest.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 500 * 1024 * 1024:
                    raise HTTPException(413, "PDF is larger than the 500 MB import limit")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Couldn't save the selected PDF: {exc}")
    if not size:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The selected PDF was empty")
    return {"path": str(dest), "filename": safe_name}


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
