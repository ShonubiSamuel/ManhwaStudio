"""
orchestrator.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Pipeline orchestration POLICY.

This module decides *what should run* for an episode; it does not run anything
itself.  Execution (background threads, SSE, the ApiTab shim) lives in
api/routers/pipeline.py, which calls the functions here.  Keeping policy pure
and side-effect-free makes the auto/resume behaviour testable without spinning
up engines.

Two entry points, split at the single review checkpoint (between narration and
translation — see pipeline_logic.SOURCE_FLOWS):

    AUTO    — run the source-type's pre-checkpoint stages, then stop so the
              user can review/edit the narration grid.
    RESUME  — after approval, run translate → dub → (sync) → assemble.

Both skip stages already finished ("done"/"skipped") so a run is resumable and
re-issuing it is cheap.  Pass force=True to re-run everything.
"""

from __future__ import annotations

from typing import List, Optional

from pipeline_logic import (
    STAGE_DB_COLUMN, db_column_for_stage, label_for_stage, stage_plan,
)

# Statuses that mean "no need to run this stage again".
_DONE_STATES = {"done", "skipped"}


# ── Status helpers ─────────────────────────────────────────────────────────────

def stage_status(episode: dict, stage: str) -> str:
    """Current status of a runnable stage, read from its mapped DB column."""
    col = db_column_for_stage(stage)
    return (episode or {}).get(f"stage_{col}") or "pending"


def stage_progress(episode: dict, stage: str) -> int:
    col = db_column_for_stage(stage)
    return int((episode or {}).get(f"progress_{col}") or 0)


def _filter_runnable(episode: dict, stages: List[str], force: bool) -> List[str]:
    """Drop stages that are already done/skipped (unless force)."""
    if force:
        return list(stages)
    return [s for s in stages if stage_status(episode, s) not in _DONE_STATES]


# ── Chain computation ──────────────────────────────────────────────────────────

def auto_chain(db, episode_id: int, *, force: bool = False) -> List[str]:
    """
    Pre-checkpoint stages to run for an auto "Run to review".
    Empty list means narration is already complete — go straight to review.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        return []
    plan = stage_plan(ep["source_type"])
    return _filter_runnable(ep, plan["pre"], force)


def narration_ready(db, episode_id: int) -> bool:
    """
    True when every pre-checkpoint stage is done or skipped — i.e. the review
    grid has narration to review and resume is allowed.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        return False
    plan = stage_plan(ep["source_type"])
    return all(stage_status(ep, s) in _DONE_STATES for s in plan["pre"])


def resume_chain(db, episode_id: int, *, force: bool = False) -> List[str]:
    """
    Post-checkpoint stages to run for "Approve & finish".
    Empty list means the episode is already fully assembled.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        return []
    plan = stage_plan(ep["source_type"])
    return _filter_runnable(ep, plan["post"], force)


# ── Full plan (for GET /api/pipeline/plan/{id}) ────────────────────────────────

def plan_for_episode(db, episode_id: int) -> Optional[dict]:
    """
    Describe the full ordered plan for an episode, annotated with each stage's
    current status — everything the Pipeline page needs to render the Auto-mode
    timeline and decide which controls to enable.

    Returns None if the episode does not exist.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        return None

    plan = stage_plan(ep["source_type"])
    pre_set = set(plan["pre"])

    stages = []
    for stage in plan["stages"]:
        stages.append({
            "key":       stage,
            "label":     label_for_stage(stage),
            "db_column": db_column_for_stage(stage),
            "phase":     "pre" if stage in pre_set else "post",
            "status":    stage_status(ep, stage),
            "progress":  stage_progress(ep, stage),
        })

    return {
        "episode_id":       episode_id,
        "source_type":      ep["source_type"],
        "stages":           stages,
        "checkpoint_index": plan["checkpoint_index"],
        "narration_ready":  narration_ready(db, episode_id),
        "auto_remaining":   auto_chain(db, episode_id),
        "resume_remaining": resume_chain(db, episode_id),
    }
