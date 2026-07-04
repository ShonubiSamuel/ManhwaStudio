"""
scripts/api/routers/video_refine.py — ManhwaStudio v2

Backend for the "Video Refine" workflow (manga recaps):

  • render an uploaded PDF to page images for the in-app crop tool,
  • crop a panel from a page (server-side, from the high-res render) and
    immediately 4× UPSCALE it (RealESRGAN anime),
  • grab a low-quality reference frame from the source video per cue,
  • persist a per-project "video refine" session (separate from the dub session,
    so the Voiceover/audio system is never touched).

All file inputs are absolute local paths (the app uses native file pickers), and
outputs live under  OUTPUT_DIR/<project_id>/refine/  +  /panels/.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import config
from api.deps import get_db
from database import Database

router = APIRouter(prefix="/video-refine", tags=["Video Refine"])

FILES_PREFIX = "/files/"

# Lazily-built, cached RealESRGAN upsampler (heavy to load — share across crops).
_upsampler = None
_upsampler_lock = threading.Lock()
_MODEL_PATH = config.BASE_DIR / "models" / "RealESRGAN_x4plus_anime_6B.pth"


def _files_url(p: Path) -> str:
    try:
        rel = Path(p).resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return FILES_PREFIX + rel.as_posix()
    except (ValueError, OSError):
        return ""


def _project_dir(project_id: int) -> Path:
    d = Path(config.OUTPUT_DIR) / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Session (separate file from the dub session)
# ─────────────────────────────────────────────────────────────────────────────

def _session_path(project_id: int) -> Path:
    return _project_dir(project_id) / "video_refine_session.json"


def _kind_key(pid: int) -> str:
    return f"project_kind_{pid}"


def _dub_session_path(pid: int) -> Path:
    # Video Refine reuses the SAME dub session as Voiceover (it IS the Voiceover
    # editor + panel cropping), so cues/voice/dub/panels all live in one file.
    return _project_dir(pid) / "dub_session.json"


@router.get("/projects")
def list_refine_projects(db: Database = Depends(get_db)):
    """Only projects created in Video Refine (kept separate from Voiceover)."""
    out = []
    for row in db.list_projects():
        pid = row["id"]
        if db.get_setting(_kind_key(pid), "") != "video_refine":
            continue
        sess = {}
        p = _dub_session_path(pid)
        if p.exists():
            try:    sess = json.loads(p.read_text(encoding="utf-8"))
            except Exception: sess = {}
        cues = sess.get("cues") or []
        out.append({
            "id": pid, "title": row.get("name") or f"Project {pid}",
            "cue_count": len(cues), "source_path": sess.get("sourcePath") or "",
            "updated_at": sess.get("updatedAt") or row.get("updated_at"),
        })
    out.sort(key=lambda p: p.get("updated_at") or 0, reverse=True)
    return out


class RefineProjectCreate(BaseModel):
    name:        str
    source_path: str = ""


@router.post("/projects")
def create_refine_project(body: RefineProjectCreate, db: Database = Depends(get_db)):
    name = (body.name or "").strip() or "Untitled refine"
    pid = db.add_project(name)
    # Tag it as a Video Refine project so it never shows in Voiceover.
    db.set_setting(_kind_key(pid), "video_refine")
    # Seed the DUB session (Video Refine uses the Voiceover editor) so its setup
    # screen pre-fills the source the user just picked.
    # No default language: the user adds languages themselves (the + button).
    _dub_session_path(pid).write_text(json.dumps({
        "cues": [], "sourcePath": body.source_path, "targetLang": "",
        "languages": [], "selectedLang": "",
        "pdfPath": "", "updatedAt": __import__("time").time() * 1000,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": pid, "title": name, "cue_count": 0, "source_path": body.source_path}


@router.get("/session/{project_id}")
def get_session(project_id: int, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    p = _session_path(project_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.put("/session/{project_id}")
def put_session(project_id: int, body: dict, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    p = _session_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"Could not save session: {exc}")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# PDF → page images (for the crop tool)
# ─────────────────────────────────────────────────────────────────────────────

class PdfRequest(BaseModel):
    project_id: int
    pdf_path:   str
    dpi:        int = 130       # viewing DPI — kept modest for speed/RAM; the crop
                               # is 4× upscaled anyway, so final quality stays high


@router.post("/pdf")
def render_pdf(req: PdfRequest, db: Database = Depends(get_db)):
    if not db.get_project(req.project_id):
        raise HTTPException(404, f"Project {req.project_id} not found")
    src = Path(req.pdf_path).expanduser()
    if not src.is_file():
        raise HTTPException(404, f"PDF not found: {src}")
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise HTTPException(500, "pdf2image is not installed (pip install pdf2image + poppler).")

    pages_dir = _project_dir(req.project_id) / "refine" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    dpi = max(72, min(int(req.dpi or 200), 400))
    try:
        images = convert_from_path(str(src), dpi=dpi, fmt="png")
    except Exception as exc:
        raise HTTPException(500, f"Could not render PDF (is poppler installed?): {exc}")

    pages = []
    for i, img in enumerate(images):
        out = pages_dir / f"page_{i:04d}.png"
        img.save(str(out), "PNG")
        pages.append({"index": i, "url": _files_url(out), "w": img.width, "h": img.height})
    return {"count": len(pages), "dpi": dpi, "pdf_path": str(src), "pages": pages}


# ─────────────────────────────────────────────────────────────────────────────
# Crop a panel (+ upscale) and a video frame
# ─────────────────────────────────────────────────────────────────────────────

class CropBox(BaseModel):
    x: float; y: float; w: float; h: float       # normalised 0..1 of the page


class CropRequest(BaseModel):
    project_id: int
    page:       int
    box:        CropBox
    cue_index:  int
    upscale:    bool = True


def _get_upsampler(db: Database, _log):
    global _upsampler
    with _upsampler_lock:
        if _upsampler is None:
            from image_upscaler import ImageUpscaler
            inst = ImageUpscaler(db, str(config.OUTPUT_DIR), on_log=_log)
            _upsampler = inst._load_realesrgan_model(_MODEL_PATH)
            _upsampler._ms_helper = inst       # keep the helper for _upscale_one_image
    return _upsampler


@router.post("/crop")
def crop_panel(req: CropRequest, db: Database = Depends(get_db)):
    from PIL import Image

    page_png = _project_dir(req.project_id) / "refine" / "pages" / f"page_{req.page:04d}.png"
    if not page_png.exists():
        raise HTTPException(404, f"Page {req.page} not rendered — load the PDF first.")

    panels = _project_dir(req.project_id) / "panels"
    panels.mkdir(parents=True, exist_ok=True)
    raw_path = panels / f"cue_{req.cue_index:04d}.png"

    try:
        img = Image.open(page_png).convert("RGB")
        W, H = img.size
        b = req.box
        left   = max(0, int(b.x * W));            top    = max(0, int(b.y * H))
        right  = min(W, int((b.x + b.w) * W));     bottom = min(H, int((b.y + b.h) * H))
        if right - left < 4 or bottom - top < 4:
            raise HTTPException(400, "Crop box is too small.")
        img.crop((left, top, right, bottom)).save(str(raw_path), "PNG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Crop failed: {exc}")

    # Upscale in the BACKGROUND so the crop returns instantly (attach feels fast).
    # The upscaled file is saved for the final render; the editor shows the raw crop.
    if req.upscale:
        up_path = panels / f"cue_{req.cue_index:04d}_up.jpg"
        threading.Thread(target=_bg_upscale, args=(db, raw_path, up_path), daemon=True).start()

    return {
        "ok": True,
        "raw": _files_url(raw_path),
        "image": _files_url(raw_path),     # show the raw crop immediately
        "upscaled": False,                 # upscale runs in the background
        "page": req.page,
        "box": req.box.model_dump(),
    }


def _bg_upscale(db: Database, raw_path: Path, up_path: Path):
    try:
        up = _get_upsampler(db, lambda *a, **k: None)
        up._ms_helper._upscale_one_image(up, raw_path, up_path)
    except Exception as exc:
        print(f"[video-refine] background upscale failed: {exc}")


class SavePanelRequest(BaseModel):
    project_id: int
    cue_index:  int
    data:       str          # PNG data-URL (or bare base64) from the browser crop


@router.post("/save-panel")
def save_panel(req: SavePanelRequest, db: Database = Depends(get_db)):
    """Save a panel cropped CLIENT-SIDE (PDF.js canvas). No upscale here — that's
    a deferred batch step. Fast: just decode + write."""
    import base64
    if not db.get_project(req.project_id):
        raise HTTPException(404, f"Project {req.project_id} not found")
    panels = _project_dir(req.project_id) / "panels"
    panels.mkdir(parents=True, exist_ok=True)
    raw = panels / f"cue_{req.cue_index:04d}.png"
    d = req.data.split(",", 1)[1] if "," in req.data else req.data
    try:
        raw.write_bytes(base64.b64decode(d))
    except Exception as exc:
        raise HTTPException(400, f"Bad image data: {exc}")
    return {"ok": True, "image": _files_url(raw), "raw": _files_url(raw)}


class PanelRef(BaseModel):
    project_id: int
    cue_index:  int


@router.post("/delete-panel")
def delete_panel(req: PanelRef, db: Database = Depends(get_db)):
    """Remove a cue's panel files (raw + any upscaled) so a later 'Upscale all'
    never touches a panel the user discarded."""
    panels = _project_dir(req.project_id) / "panels"
    removed = 0
    for pat in (f"cue_{req.cue_index:04d}.png", f"cue_{req.cue_index:04d}.jpg",
                f"cue_{req.cue_index:04d}_up.jpg", f"cue_{req.cue_index:04d}_up.png"):
        f = panels / pat
        try:
            if f.exists(): f.unlink(); removed += 1
        except OSError:
            pass
    return {"ok": True, "removed": removed}


# Batch upscale (deferred until the user is done cropping) ────────────────────
_upscale_jobs: dict = {}      # project_id -> {"total": n, "done": n, "running": bool}


@router.post("/upscale-all")
def upscale_all(req: dict, db: Database = Depends(get_db)):
    """Upscale every cropped panel that doesn't yet have an `_up` version.
    Runs in the background; poll /upscale-status/{project_id}."""
    pid = int(req.get("project_id"))
    panels = _project_dir(pid) / "panels"
    todo = []
    if panels.exists():
        for raw in sorted(panels.glob("cue_*.png")):
            if raw.stem.endswith("_up"):
                continue
            up = panels / f"{raw.stem}_up.jpg"
            if not up.exists():
                todo.append((raw, up))
    if not todo:
        _upscale_jobs[pid] = {"total": 0, "done": 0, "running": False}
        return {"ok": True, "total": 0}
    _upscale_jobs[pid] = {"total": len(todo), "done": 0, "running": True}

    def _run():
        for raw, up in todo:
            _bg_upscale(db, raw, up)
            _upscale_jobs[pid]["done"] += 1
        _upscale_jobs[pid]["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "total": len(todo)}


@router.get("/upscale-status/{project_id}")
def upscale_status(project_id: int):
    return _upscale_jobs.get(project_id, {"total": 0, "done": 0, "running": False})


class FrameRequest(BaseModel):
    project_id:  int
    source_path: str
    time:        float
    cue_index:   int


@router.post("/frame")
def grab_frame(req: FrameRequest, db: Database = Depends(get_db)):
    src = Path(req.source_path).expanduser()
    if not src.is_file():
        raise HTTPException(404, f"Source video not found: {src}")
    frames = _project_dir(req.project_id) / "refine" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    out = frames / f"cue_{req.cue_index:04d}.jpg"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, req.time):.3f}", "-i", str(src),
             "-frames:v", "1", "-q:v", "5", str(out)],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        raise HTTPException(500, f"ffmpeg error: {exc}")
    if r.returncode != 0 or not out.exists():
        raise HTTPException(500, f"Frame grab failed: {(r.stderr or '')[-300:]}")
    return {"ok": True, "image": _files_url(out)}
