"""
scripts/api/routers/logs.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
The Logs archive — the permanent historical record of every stage run.

Reads the processing_logs table (written by every stage via log_stage_start /
log_stage_end).  This is the deep "what happened" view; the Pipeline surfaces
live status inline + via toasts.

Endpoints
─────────
  GET    /api/logs                recent runs across all episodes (newest first)
  GET    /api/logs/{episode_id}   full history for one episode
  DELETE /api/logs                clear all logs  (optional ?episode_id=)
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps   import get_db
from api.models import LogEntry, LogLine, OkResponse
from database   import Database

router = APIRouter(prefix="/logs", tags=["Logs"])


def _transcript(row: dict) -> list[LogLine]:
    """Parse the persisted developer transcript out of metadata_json["log"]."""
    raw = row.get("metadata_json")
    if not raw:
        return []
    try:
        lines = (json.loads(raw) or {}).get("log") or []
    except (ValueError, TypeError):
        return []
    out = []
    for ln in lines:
        if isinstance(ln, dict):
            out.append(LogLine(level=ln.get("level") or "info",
                               message=str(ln.get("message") or "")))
    return out


def _to_entry(row: dict) -> LogEntry:
    return LogEntry(
        id            = row["id"],
        episode_id    = row["episode_id"],
        episode_title = row.get("episode_title") or "",
        project_name  = row.get("project_name") or "",
        stage         = row.get("stage") or "",
        status        = row.get("status") or "",
        started_at    = row.get("started_at") or 0.0,
        finished_at   = row.get("finished_at"),
        duration_secs = row.get("duration_secs"),
        error         = row.get("error") or "",
        log           = _transcript(row),
    )


@router.get("", response_model=list[LogEntry])
def recent_logs(limit: int = Query(200, ge=1, le=2000), db: Database = Depends(get_db)):
    return [_to_entry(r) for r in db.list_recent_logs(limit)]


@router.get("/{episode_id}", response_model=list[LogEntry])
def episode_logs(episode_id: int, db: Database = Depends(get_db)):
    return [_to_entry(r) for r in db.get_episode_logs(episode_id)]


@router.delete("", response_model=OkResponse)
def clear_logs(episode_id: Optional[int] = Query(None), db: Database = Depends(get_db)):
    n = db.clear_logs(episode_id)
    return OkResponse(ok=True, message=f"Cleared {n} log entr{'y' if n == 1 else 'ies'}")
