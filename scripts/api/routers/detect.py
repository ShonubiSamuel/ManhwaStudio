"""
scripts/api/routers/detect.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Detect-stage configuration and tuning actions.

The Detect stage finds panel cuts in a video. Its tuning workflow needs more
than a generic "run": configurable detection settings, a test-clip extraction,
a preview pass on that clip, and a parameter tuner. This router exposes those
as typed endpoints over the engine that already implements them
(video_engine.VideoEngine) and the per-episode detect_* columns.

Endpoints
─────────
  GET   /api/detect/config/{id}    current settings + defaults + readiness flags
  PATCH /api/detect/config/{id}    save settings (clears the "confirmed" flag)
  POST  /api/detect/clip/{id}      extract a short test clip (ffmpeg)
  POST  /api/detect/preview/{id}   run detection on the clip, return cuts
  POST  /api/detect/tuner/{id}     launch the interactive parameter tuner

The full detection pass is the existing pipeline run (stage "detect").
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.deps   import get_db
from api.models import (
    DetectConfig, DetectConfigUpdate, DetectClipRequest,
    DetectPreviewResponse, DetectCut,
)
from database   import Database
import config

router = APIRouter(prefix="/detect", tags=["Detect"])

_CLIP_NAME = "detect_clip.mp4"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clip_path(ep: dict) -> Path:
    return Path(ep.get("output_folder") or ".") / _CLIP_NAME


def _defaults() -> dict:
    return {
        "mode":             config.DETECT_MODE,
        "priority":         config.DETECT_PRIORITY,
        "silence_db":       config.DETECT_SILENCE_DB,
        "min_silence_sec":  config.DETECT_MIN_SILENCE,
        "visual_threshold": config.DETECT_THRESHOLD,
        "min_scene_sec":    config.DETECT_MIN_SCENE,
        "frame_skip":       config.DETECT_FRAME_SKIP,
        "merge_window":     config.DETECT_MERGE_WINDOW,
        "workers":          config.DETECT_WORKERS,
    }


def _build_config(ep: dict) -> DetectConfig:
    source = ep.get("source_path") or ""
    return DetectConfig(
        episode_id       = ep["id"],
        mode             = ep.get("detect_mode")        or config.DETECT_MODE,
        priority         = ep.get("detect_priority")    or config.DETECT_PRIORITY,
        silence_db       = float(ep.get("detect_silence_db",   config.DETECT_SILENCE_DB)),
        min_silence_sec  = float(ep.get("detect_min_silence",  config.DETECT_MIN_SILENCE)),
        visual_threshold = float(ep.get("detect_threshold",    config.DETECT_THRESHOLD)),
        min_scene_sec    = float(ep.get("detect_min_scene",    config.DETECT_MIN_SCENE)),
        frame_skip       = int(ep.get("detect_frame_skip",     config.DETECT_FRAME_SKIP)),
        merge_window     = float(ep.get("detect_merge_window", config.DETECT_MERGE_WINDOW)),
        workers          = int(ep.get("detect_workers",        config.DETECT_WORKERS)),
        clip_start       = ep.get("detect_clip_start")    or "00:00:00",
        clip_duration    = int(ep.get("detect_clip_duration") or 120),
        confirmed        = bool(ep.get("detect_confirmed", 0)),
        clip_ready       = _clip_path(ep).exists(),
        source_exists    = bool(source and Path(source).exists()),
        defaults         = _defaults(),
    )


def _params_from_episode(ep: dict):
    from video_engine import detection_params_from_episode
    return detection_params_from_episode(ep)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/config/{episode_id}", response_model=DetectConfig)
def get_detect_config(episode_id: int, db: Database = Depends(get_db)):
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")
    if ep["source_type"] != "video":
        raise HTTPException(400, "Detect settings apply to video episodes only")
    return _build_config(ep)


@router.patch("/config/{episode_id}", response_model=DetectConfig)
def update_detect_config(episode_id: int, body: DetectConfigUpdate, db: Database = Depends(get_db)):
    """
    Persist detection settings. Editing settings clears the `confirmed` flag —
    the preview should be re-run after a change (mirrors the old desktop flow).
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    col = {
        "mode":             "detect_mode",
        "priority":         "detect_priority",
        "silence_db":       "detect_silence_db",
        "min_silence_sec":  "detect_min_silence",
        "visual_threshold": "detect_threshold",
        "min_scene_sec":    "detect_min_scene",
        "frame_skip":       "detect_frame_skip",
        "merge_window":     "detect_merge_window",
        "workers":          "detect_workers",
        "clip_start":       "detect_clip_start",
        "clip_duration":    "detect_clip_duration",
    }
    fields = {}
    for key, column in col.items():
        val = getattr(body, key)
        if val is not None:
            fields[column] = val
    if fields:
        fields["detect_confirmed"] = 0   # settings changed → must re-preview
        db.update_episode(episode_id, **fields)
        db.log_action(episode_id, "detect", status="settings saved")

    return _build_config(db.get_episode(episode_id))


@router.post("/clip/{episode_id}", response_model=DetectConfig)
def extract_clip(episode_id: int, body: DetectClipRequest, db: Database = Depends(get_db)):
    """Extract a short test clip with ffmpeg, saved as detect_clip.mp4."""
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    source = ep.get("source_path") or ""
    if not source or not Path(source).exists():
        raise HTTPException(400, "Source video not found — check the episode source path")

    db.update_episode(episode_id, detect_clip_start=body.start, detect_clip_duration=body.duration)

    from video_engine import VideoEngine
    engine = VideoEngine(db, ep["output_folder"])
    ok = engine.extract_clip(source, str(_clip_path(ep)), body.start, body.duration)
    if not ok:
        db.log_action(episode_id, "detect", status="clip extraction failed",
                      error=f"start={body.start}, duration={body.duration}s")
        raise HTTPException(500, "Clip extraction failed — see logs")

    db.log_action(episode_id, "detect",
                  status=f"test clip extracted ({body.duration}s @ {body.start})")
    return _build_config(db.get_episode(episode_id))


@router.post("/preview/{episode_id}", response_model=DetectPreviewResponse)
def run_preview(episode_id: int, db: Database = Depends(get_db)):
    """
    Run detection on the extracted test clip using the saved settings and
    return the cuts — without touching the panels table.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    clip = _clip_path(ep)
    if not clip.exists():
        raise HTTPException(409, "No test clip yet — extract a clip first")

    from video_engine import VideoEngine
    engine = VideoEngine(db, ep["output_folder"])
    cuts = engine.detect_on_clip(
        source_path = ep.get("source_path") or "",
        clip_path   = str(clip),
        params      = _params_from_episode(ep),
    ) or []

    out = [
        DetectCut(
            panel_index     = c.get("panel_index", i),
            start_time_sec  = round(float(c.get("start_time_sec", 0)), 2),
            end_time_sec    = round(float(c.get("end_time_sec", 0)), 2),
            duration_sec    = round(float(c.get("duration_sec",
                                  (c.get("end_time_sec", 0) - c.get("start_time_sec", 0)))), 2),
            transcript_text = (c.get("transcript_text") or "").strip(),
        )
        for i, c in enumerate(cuts)
    ]
    avg = round(sum(c.duration_sec for c in out) / len(out), 2) if out else 0.0
    if out:
        db.log_action(episode_id, "detect",
                      status=f"preview · {len(out)} cuts · avg {avg}s")
    else:
        db.log_action(episode_id, "detect", status="preview · 0 cuts",
                      error="No cuts found on the test clip — try lowering the thresholds.")
    return DetectPreviewResponse(count=len(out), avg_duration=avg, cuts=out)


@router.post("/tuner/{episode_id}")
def open_tuner(episode_id: int, db: Database = Depends(get_db)):
    """
    Launch the interactive parameter tuner (visualize_params.py) on the test
    clip. It writes an HTML report and opens it in the default browser.
    Best-effort — returns immediately; the report opens out-of-process.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    clip = _clip_path(ep)
    if not clip.exists():
        raise HTTPException(409, "No test clip yet — extract a clip first")

    script = Path(__file__).resolve().parent.parent.parent / "visualize_params.py"
    if not script.exists():
        raise HTTPException(500, "visualize_params.py not found")

    p   = _params_from_episode(ep)
    out = str(Path(ep["output_folder"]) / "tuner")
    try:
        subprocess.Popen(
            [sys.executable, str(script), str(clip),
             "--output",       out,
             "--silence-db",   str(p.silence_db),
             "--min-silence",  str(p.min_silence_sec),
             "--threshold",    str(p.visual_threshold),
             "--min-scene",    str(p.min_scene_sec),
             "--merge-window", str(p.merge_window),
             "--frame-skip",   str(p.frame_skip)],
        )
    except Exception as exc:
        db.log_action(episode_id, "detect", status="tuner launch failed", error=str(exc))
        raise HTTPException(500, f"Could not launch tuner: {exc}")

    db.log_action(episode_id, "detect", status="parameter tuner launched")
    return {"ok": True, "message": "Parameter tuner launching — it opens in your browser."}
