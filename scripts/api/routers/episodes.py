"""
scripts/api/routers/episodes.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Episode CRUD endpoints.

Schema note
───────────
db.add_episode(project_id, title, source_type, source_path, output_folder,
               mode="auto", tone_prompt="") is the real creation method
(there is no db.create_episode).  It determines per-stage default statuses
(pending/skipped) internally based on source_type, so the router does not
need to set stage_* columns itself.

Endpoints
─────────
  GET    /api/episodes?project_id=N   list episodes for a project
  POST   /api/episodes                import a new episode
  GET    /api/episodes/{id}           get one episode (stages + overall progress)
  PATCH  /api/episodes/{id}           update title / tone_prompt
  DELETE /api/episodes/{id}           delete episode + all its data (cascade)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db
from api.models import (
    EpisodeCreate, EpisodeUpdate, EpisodeResponse, OkResponse, StageInfo
)
from database import Database
from pipeline_logic import overall_progress
import config

router = APIRouter(prefix="/episodes", tags=["Episodes"])


# ── Stage names — matches the stage_* / progress_* columns in episodes table ──

_STAGES = [
    "detect", "extract", "screenshot", "upscale", "narrate",
    "translate", "dub", "sync", "assemble",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """Strip characters unsafe for folder names and collapse whitespace."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r'\s+', "_", name.strip())
    return name or "episode"


def _build_output_folder(project_id: int, episode_title: str) -> str:
    """
    Derive the output folder path from the project ID and episode title.
    Layout:  OUTPUT_DIR / <project_id> / <safe_episode_title>
    Using project_id (not name) avoids breakage if the project is renamed.
    """
    return str(config.OUTPUT_DIR / str(project_id) / _safe_name(episode_title))


def _to_response(db: Database, row: dict) -> EpisodeResponse:
    """Convert a raw DB episode row to a full EpisodeResponse."""
    stages: dict = {}
    for stage in _STAGES:
        status   = row.get(f"stage_{stage}") or "pending"
        progress = int(row.get(f"progress_{stage}") or 0)
        stages[stage] = StageInfo(status=status, progress=progress)

    return EpisodeResponse(
        id            = row["id"],
        project_id    = row["project_id"],
        title         = row["title"],
        source_type   = row["source_type"],
        source_path   = row.get("source_path") or "",
        output_folder = row.get("output_folder") or "",
        tone_prompt   = row.get("tone_prompt") or "",
        stages        = stages,
        overall       = overall_progress(db, row["id"]),
        total_panels  = int(row.get("total_panels") or 0),
        duration_secs = row.get("duration_secs"),
        total_pages   = row.get("total_pages"),
        error_message = row.get("error_message") or "",
        created_at    = row["created_at"],
        updated_at    = row["updated_at"],
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[EpisodeResponse])
def list_episodes(
    project_id: int = Query(..., description="Filter by project"),
    db:         Database = Depends(get_db),
):
    """Return all episodes belonging to a project, ordered by creation time."""
    rows = db.list_episodes(project_id)
    return [_to_response(db, r) for r in rows]


@router.post("", response_model=EpisodeResponse, status_code=201)
def create_episode(body: EpisodeCreate, db: Database = Depends(get_db)):
    """
    Import a new episode into a project.

    source_type must be one of: video | pdf | screenshots
    source_path must be an absolute path to the file on disk.
    output_folder is derived automatically from project ID + episode title.

    Calls db.add_episode() — the real method name on Database.  There is
    no db.create_episode(); the per-stage default statuses (pending vs
    skipped) are determined inside add_episode() based on source_type.
    """
    project = db.get_project(body.project_id)
    if not project:
        raise HTTPException(404, f"Project {body.project_id} not found")

    valid_types = {"video", "pdf", "screenshots"}
    if body.source_type not in valid_types:
        raise HTTPException(
            400,
            f"source_type must be one of {sorted(valid_types)}, got '{body.source_type}'"
        )

    src = Path(body.source_path)
    if not src.exists():
        raise HTTPException(
            400,
            f"Source file not found on disk: {body.source_path}"
        )

    output_folder = _build_output_folder(body.project_id, body.title)
    Path(output_folder).mkdir(parents=True, exist_ok=True)

    episode_id = db.add_episode(
        project_id    = body.project_id,
        title         = body.title.strip(),
        source_type   = body.source_type,
        source_path   = body.source_path,
        output_folder = output_folder,
        tone_prompt   = body.tone_prompt.strip(),
    )

    row = db.get_episode(episode_id)
    if not row:
        raise HTTPException(500, "Episode created but could not be retrieved")

    return _to_response(db, row)


@router.get("/{episode_id}", response_model=EpisodeResponse)
def get_episode(episode_id: int, db: Database = Depends(get_db)):
    """
    Return a single episode with its full stage status map and overall progress.
    The UI polls this endpoint to update progress bars during a pipeline run.
    """
    row = db.get_episode(episode_id)
    if not row:
        raise HTTPException(404, f"Episode {episode_id} not found")
    return _to_response(db, row)


@router.patch("/{episode_id}", response_model=EpisodeResponse)
def update_episode(
    episode_id: int,
    body:       EpisodeUpdate,
    db:         Database = Depends(get_db),
):
    """Update episode title and/or tone prompt."""
    row = db.get_episode(episode_id)
    if not row:
        raise HTTPException(404, f"Episode {episode_id} not found")

    updates = body.model_dump(exclude_none=True)
    if updates:
        db.update_episode(episode_id, **updates)

    row = db.get_episode(episode_id)
    return _to_response(db, row)


@router.delete("/{episode_id}", response_model=OkResponse)
def delete_episode(episode_id: int, db: Database = Depends(get_db)):
    """
    Delete an episode and all its associated data.
    SQLite ON DELETE CASCADE removes panels, panel_audio, narration_batches,
    processing_logs and dub_languages rows automatically.
    The episode's output folder is moved to the OS Trash (recoverable).
    """
    from api.routers.projects import trash_path

    row = db.get_episode(episode_id)
    if not row:
        raise HTTPException(404, f"Episode {episode_id} not found")
    title = row["title"]
    folder = row.get("output_folder")
    db.delete_episode(episode_id)
    trashed = trash_path(Path(folder)) if folder else False
    suffix = " (output moved to Trash)" if trashed else ""
    return OkResponse(ok=True, message=f"Episode '{title}' deleted{suffix}")
