"""
scripts/api/routers/projects.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Project CRUD endpoints.

Schema note
───────────
The projects table column is `name` (UNIQUE, NOT NULL), not `title`.
The API response shape uses `title` for consistency with the episodes
endpoints and the React UI — _to_response() maps row["name"] → title at
the boundary so this detail never leaks past this file.

db.add_project(name) only accepts the name — notes/cover_path are set
via a follow-up db.update_project() call if supplied at creation time.

Endpoints
─────────
  GET    /api/projects              list all projects
  POST   /api/projects              create a project
  GET    /api/projects/{id}         get one project
  PATCH  /api/projects/{id}         update name / notes
  DELETE /api/projects/{id}         delete project + all its episodes (cascade)
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException

import config
from api.deps import get_db
from api.models import OkResponse, ProjectCreate, ProjectResponse
from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


def trash_path(path: Path) -> bool:
    """Remove a file/folder, PREFERRING the OS Trash (recoverable). If send2trash
    is unavailable or fails — e.g. a packaged runtime that didn't bundle it — fall
    back to a permanent delete so we never silently leave an orphaned project
    folder behind. Never raises; deleting the DB row must still succeed. Returns
    True if the path existed and is now gone."""
    if not path or not path.exists():
        return False
    try:
        from send2trash import send2trash
        send2trash(str(path))
        return True
    except Exception as exc:
        logger.warning("send2trash unavailable/failed for %s (%s); removing permanently", path, exc)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return not path.exists()
    except Exception as exc:
        logger.error("could not remove %s: %s", path, exc)
        return False


# ── Helper ────────────────────────────────────────────────────────────────────

def _to_response(db: Database, row: dict) -> ProjectResponse:
    """
    Convert a raw DB project row to a ProjectResponse.
    row["name"] (DB column) is exposed as `title` in the API response.
    """
    return ProjectResponse(
        id            = row["id"],
        title         = row["name"],
        notes         = row.get("notes") or "",
        cover_path    = row.get("cover_path") or "",
        episode_count = len(db.list_episodes(row["id"])),
        created_at    = row["created_at"],
        updated_at    = row["updated_at"],
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ProjectResponse])
def list_projects(db: Database = Depends(get_db)):
    """Return every project with its episode count."""
    return [_to_response(db, row) for row in db.list_projects()]


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(body: ProjectCreate, db: Database = Depends(get_db)):
    """
    Create a new project.

    db.add_project(name) always creates a DISTINCT project — if the name is
    already taken it auto-suffixes ' (2)', ' (3)', … rather than returning the
    existing one, so projects never share a folder/state. notes are applied
    afterward; the response carries the actual stored title.
    """
    title = body.title.strip()
    project_id = db.add_project(title)

    if body.notes.strip():
        db.update_project(project_id, notes=body.notes.strip())

    row = db.get_project(project_id)
    if not row:
        raise HTTPException(500, "Project created but could not be retrieved")
    return _to_response(db, row)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, db: Database = Depends(get_db)):
    """Return a single project by ID."""
    row = db.get_project(project_id)
    if not row:
        raise HTTPException(404, f"Project {project_id} not found")
    return _to_response(db, row)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    body:       dict,
    db:         Database = Depends(get_db),
):
    """
    Update project name and/or notes.
    Only the fields present in the request body are changed.

    `title` in the request body maps to the `name` DB column.
    Renaming to a name that already exists on another project returns 409,
    since `name` is UNIQUE in the schema.
    """
    row = db.get_project(project_id)
    if not row:
        raise HTTPException(404, f"Project {project_id} not found")

    updates: dict = {}
    if body.get("title") is not None:
        updates["name"] = body["title"].strip()
    if body.get("notes") is not None:
        updates["notes"] = body["notes"].strip()

    if updates:
        try:
            db.update_project(project_id, **updates)
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"A project named '{updates.get('name')}' already exists")

    row = db.get_project(project_id)
    return _to_response(db, row)


@router.delete("/{project_id}", response_model=OkResponse)
def delete_project(project_id: int, db: Database = Depends(get_db)):
    """
    Delete a project and all its episodes.
    SQLite ON DELETE CASCADE (foreign_keys = ON) removes episodes, panels,
    and all related rows automatically.  The project's output folder
    (OUTPUT_DIR/<project_id>, which contains every episode folder + the saved
    Dub Studio session) is moved to the OS Trash so it can be recovered.
    """
    row = db.get_project(project_id)
    if not row:
        raise HTTPException(404, f"Project {project_id} not found")
    name = row["name"]
    folder = Path(config.OUTPUT_DIR) / str(project_id)
    db.delete_project(project_id)
    removed = trash_path(folder)
    suffix = " (output folder removed)" if removed else ""
    return OkResponse(ok=True, message=f"Project '{name}' deleted{suffix}")
