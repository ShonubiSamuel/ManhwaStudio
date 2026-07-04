"""
scripts/api/main.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
FastAPI application — the communication bridge between the React UI and
every Python engine.

Running
───────
    Started automatically by scripts/app.py.
    Binds to http://127.0.0.1:8000

Swagger UI (dev)
────────────────
    http://127.0.0.1:8000/api/docs
    All endpoints are browsable and testable here without touching the UI.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from api.routers import (
    projects, episodes, pipeline, settings, panels, dubbing, detect,
    translate, sync, logs, voices, speech, media, video_refine,
)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title    = config.APP_NAME,
    version  = config.APP_VERSION,
    docs_url = "/api/docs",
)

# Allow the Vite dev server (port 5173) to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins = ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods = ["*"],
    allow_headers = ["*"],
)


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(projects.router,  prefix="/api")
app.include_router(episodes.router,  prefix="/api")
app.include_router(pipeline.router,  prefix="/api")
app.include_router(settings.router,  prefix="/api")
app.include_router(panels.router,    prefix="/api")
app.include_router(dubbing.router,    prefix="/api")
app.include_router(detect.router,     prefix="/api")
app.include_router(translate.router,  prefix="/api")
app.include_router(sync.router,       prefix="/api")
app.include_router(logs.router,       prefix="/api")
app.include_router(voices.router,     prefix="/api")
app.include_router(speech.router,     prefix="/api")
app.include_router(media.router,      prefix="/api")
app.include_router(video_refine.router, prefix="/api")


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """Quick ping — confirms the API is reachable from the UI."""
    return {
        "status":  "ok",
        "app":     config.APP_NAME,
        "version": config.APP_VERSION,
    }


# ── Panel image serving ───────────────────────────────────────────────────────
# Read-only static mount over the output directory so the Review page can load
# panel thumbnails by URL (GET /files/<rel-path-from-OUTPUT_DIR>).
# StaticFiles confines access to OUTPUT_DIR and rejects path traversal, so no
# file outside the output root is ever reachable.  The panels router only emits
# /files URLs for paths it has verified live under OUTPUT_DIR.

config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/files",
    StaticFiles(directory=str(config.OUTPUT_DIR)),
    name="files",
)


# ── Static files (production build only) ─────────────────────────────────────
# In production, `npm run build` outputs to ui/dist/.
# FastAPI serves that as the root so pywebview only needs one server.
# In development, Vite runs its own server and this block is bypassed.
# Mounted LAST so /api/* and /files/* take precedence over the SPA catch-all.

_dist = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
