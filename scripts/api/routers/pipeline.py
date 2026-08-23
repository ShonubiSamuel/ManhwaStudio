"""
scripts/api/routers/pipeline.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Pipeline management endpoints.

Endpoints
─────────
  POST /api/pipeline/run              start a stage in a background thread
  POST /api/pipeline/stop/{id}        abort the running stage for an episode
  GET  /api/pipeline/events/{id}      SSE stream of log + progress events
  GET  /api/pipeline/panels/{id}      all panels for an episode
  GET  /api/pipeline/episode/{id}     fresh episode data (status poll)

ApiTab
──────
The Tkinter PipelineTab object is replaced by ApiTab — a minimal shim that
satisfies every attribute and method the stage runner functions call:

    tab.db                  →  shared Database instance
    tab._episode_id         →  episode being processed
    tab._episode            →  raw episode dict from DB
    tab._log(msg, level)    →  emit_log(episode_id, msg, level)
    tab._on_progress(p, m)  →  emit_progress(episode_id, p, m)
    tab._active_engine      →  set by runner for abort() calls
    tab._stop_flag          →  bool; set True to request abort
    tab.after(0, func)      →  calls func() immediately (no event loop)
    tab._reload_stages()    →  no-op (UI polls instead)
    tab._refresh_all_statuses()   →  no-op
    tab._set_ui_running(b)  →  no-op
    tab._cascade_wipe_downstream() → no-op

Stage runners already guard Tkinter-specific attributes with hasattr() so
absent attributes (like _refined_tree, _sync_status_rows) are silently skipped.
"""

from __future__ import annotations

import importlib
import threading
import traceback
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api.deps   import get_db
from api.events import (
    clear_queue, emit_log, emit_progress,
    emit_stage_advance, emit_stage_done, stream_events,
)
from api.models import OkResponse, PanelResponse, PipelineRunRequest, EpisodeResponse, StageInfo
from database   import Database
from pipeline_logic import (
    overall_progress, db_column_for_stage, label_for_stage,
    cascade_invalidate_downstream,
)
import orchestrator
import config

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


# ── Stage → module path map ───────────────────────────────────────────────────
# Maps the stage key sent by the UI to the Python module that owns runner().
# Imports are lazy (via importlib) so Tkinter is not loaded at startup.

_STAGE_MODULES: Dict[str, str] = {
    "detect":       "ui.stages.detect_stage",      # detect panels + screenshots
    "video_refine": "ui.stages.video_refine_stage",
    "pdf_slice":    "ui.stages.pdf_slice_stage",
    "pdf_narrate":  "ui.stages.pdf_narrate_stage",
    "upscale":      "ui.stages.upscale_stage",
    "translate":    "ui.stages.translate_stage",
    "dub":          "ui.stages.dub_stage",
    "sync":         "ui.stages.sync_stage",
    "assemble":     "ui.stages.assemble_stage",
}

# Stages whose manual "Run" automatically continues into the next stage(s), so a
# cycle runs in one click. Dub → Sync keeps the dub and its sync (incl. auto-fix)
# together — the user shouldn't have to click Sync separately for consistency.
_RUN_FOLLOWUPS: Dict[str, list] = {
    "dub": ["sync"],
}

# Human-readable labels come from pipeline_logic.label_for_stage() — the single
# source of truth shared with the orchestrator's /plan response.
# NOTE: "tts" is intentionally absent — TTS runs inside "dub" (DubEngine).

_ALL_STAGES = list(_STAGE_MODULES.keys())

# Every run is logged at the thread level so the Logs archive never misses a
# run and always records WHY a stage failed (engines may also log their own
# finer-grained rows — that extra detail is welcome).

def _log_start(db: Database, episode_id: int, col: str):
    try:
        return db.log_stage_start(episode_id, col)
    except Exception:
        return None


def _log_end(db: Database, log_id, *, success: bool, stopped: bool,
             error: str = "", lines: list | None = None) -> None:
    if log_id is None:
        return
    status = "done" if success else ("skipped" if stopped else "failed")
    if not error and stopped and not success:
        error = "Stopped by user"
    # Persist the full developer transcript so the Logs archive can show
    # exactly what happened (model loading, batch counts, errors) after the
    # run — not just the coarse final status.
    metadata = {"log": lines} if lines else None
    try:
        db.log_stage_end(log_id, status, error=(error or "")[:4000], metadata=metadata)
    except Exception:
        pass


# ── Global run-time state ─────────────────────────────────────────────────────
# Protected by _state_lock for thread safety.

_state_lock:  threading.Lock            = threading.Lock()
_running:     Dict[int, threading.Thread] = {}   # episode_id → thread
_active_tabs: Dict[int, "ApiTab"]         = {}   # episode_id → current ApiTab


# ══════════════════════════════════════════════════════════════════════════════
# ApiTab — Tkinter tab shim for the API context
# ══════════════════════════════════════════════════════════════════════════════

class ApiTab:
    """
    Minimal replacement for PipelineTab used by stage runner functions.

    Stage runners call tab._log(), tab._on_progress(), tab.after() etc.
    This class wires those calls to the SSE event system and provides
    harmless no-ops for every Tkinter-specific method.
    """

    # Keep the most recent N log lines per run so the full developer transcript
    # can be persisted to the Logs archive (processing_logs.metadata_json).
    _LOG_BUFFER_MAX = 1000

    def __init__(self, episode_id: int, db: Database) -> None:
        self._episode_id:    int            = episode_id
        self.db:             Database       = db
        self._episode:       Optional[dict] = db.get_episode(episode_id)
        self._active_engine: object         = None
        self._stop_flag:     bool           = False
        self._last_error:    str            = ""   # most recent error log line
        self._log_lines:     list           = []   # buffered transcript for the archive
        # Tkinter-era per-stage variables some runners read directly (not via
        # hasattr).  Empty defaults make those runners fall back to the DB
        # settings the API writes (e.g. translate reads translate_langs_{id}).
        self._translate_lang_vars: dict   = {}
        self._translate_single_lang       = None

    # ── Callbacks wired to SSE ────────────────────────────────────────────

    def _log(self, msg: str, level: str = "info") -> None:
        msg = str(msg)
        if level == "error":
            self._last_error = msg   # captured into the Logs row
        # Buffer for the persistent transcript (capped so a long run can't grow
        # unbounded — keep the most recent lines, which is what matters).
        self._log_lines.append({"level": level, "message": msg})
        if len(self._log_lines) > self._LOG_BUFFER_MAX:
            del self._log_lines[: len(self._log_lines) - self._LOG_BUFFER_MAX]
        emit_log(self._episode_id, msg, level)

    def reset_log_buffer(self) -> None:
        """Start a fresh transcript (called before each stage in a chain)."""
        self._log_lines = []

    def _on_progress(self, current, total=None, msg: str = "") -> None:
        """
        Progress callback used by every engine, which calls it as
        on_progress(current, total) — two absolute counts.  Convert that to a
        percentage plus an "x / total" label here (the SSE/UI layer expects a
        0–100 pct).  A single-argument call is treated as an already-computed
        percentage so any (pct, msg) caller still works.
        """
        try:
            if total is None:
                pct, label = int(current or 0), (str(msg) if msg else "")
            else:
                cur, tot = int(current or 0), int(total or 0)
                pct   = round(cur / tot * 100) if tot > 0 else 0
                label = msg or (f"{cur} / {tot}" if tot > 0 else str(cur))
        except (TypeError, ValueError):
            pct, label = 0, str(msg or "")
        emit_progress(self._episode_id, pct, label)

    # ── Tkinter.after() replacement ───────────────────────────────────────
    # Tkinter stages use tab.after(0, func) to schedule work on the main
    # thread.  In the API context there is no event loop — just call immediately.

    def after(self, _delay, func) -> None:
        try:
            func()
        except Exception:
            pass   # never crash the runner over a UI callback

    # ── No-ops for UI-only methods ────────────────────────────────────────

    def _reload_stages(self, *_keys) -> None:       pass
    def _refresh_all_statuses(self) -> None:        pass
    def _set_ui_running(self, _running) -> None:    pass
    def _cascade_wipe_downstream(self) -> None:     pass
    def _update_progress(self, _pct, _msg) -> None: pass


# ══════════════════════════════════════════════════════════════════════════════
# Stage dispatch
# ══════════════════════════════════════════════════════════════════════════════

def _get_runner(stage: str):
    """
    Lazily import the stage module and return its runner() function.
    Raises ValueError for unknown stage names.
    """
    module_path = _STAGE_MODULES.get(stage)
    if not module_path:
        raise ValueError(
            f"Unknown stage '{stage}'. "
            f"Valid stages: {', '.join(_STAGE_MODULES)}"
        )
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(f"Cannot import stage module '{module_path}': {exc}")

    runner = getattr(mod, "runner", None)
    if not callable(runner):
        raise RuntimeError(f"Stage module '{module_path}' has no runner() function")

    return runner


_TERMINAL_STATUSES = {"done", "failed", "skipped"}


def _persist_stage_status(
    db: Database, episode_id: int, stage: str,
    *, success: bool, stopped: bool,
) -> None:
    """
    Author the episodes-table status for a finished stage.

    Engines author their own status for some stages (detect/extract/upscale/
    narrate) and the assemble runner deliberately marks itself "skipped".
    To avoid clobbering those intentional terminal states, we only write a
    final status when the column has NOT already been left terminal by the
    runner/engine.  This is what makes the previously-stale stages
    (translate, dub, sync) finally flip to "done" in the UI grid while
    respecting narrate="done" and assemble="skipped".
    """
    col = db_column_for_stage(stage)
    ep  = db.get_episode(episode_id)
    if not ep:
        return
    current = ep.get(f"stage_{col}") or "pending"
    if current in _TERMINAL_STATUSES:
        return  # runner/engine already recorded a final state — leave it
    if success:
        db.set_episode_stage(episode_id, col, "done", progress=100)
    elif stopped:
        db.set_episode_stage(episode_id, col, "pending")
    else:
        db.set_episode_stage(episode_id, col, "failed")


def _cascade_downstream(db: Database, episode_id: int, stage: str) -> None:
    """
    After a stage completes successfully, invalidate the stages that depend on
    it (the API-context replacement for the desktop tab's cascade-wipe).  Stale
    downstream stages flip "done" → "outdated" and their data is cleared so the
    UI never shows a downstream stage as "done" over data the re-run replaced
    (e.g. re-running Detect must un-"done" Refine/Translate/Dub/Sync).

    The cascade itself is a no-op when nothing downstream was complete, so this
    is safe to call after every stage — first runs and forward chains included.
    """
    col = db_column_for_stage(stage)
    try:
        result = cascade_invalidate_downstream(db, episode_id, col)
    except Exception:
        return  # invalidation must never abort a successful run
    changed = result.get("stages_outdated") or []
    if not changed:
        return
    pretty = " · ".join(label_for_stage(c) for c in changed)
    emit_log(
        episode_id,
        f"↺  {label_for_stage(stage)} re-ran — {pretty} marked outdated "
        f"(cleared {result.get('langs_cleared', 0)} language(s))",
        "warning",
    )
    try:
        db.log_action(
            episode_id, col,
            status=f"downstream invalidated → {', '.join(changed)}",
        )
    except Exception:
        pass


def _run_stage_thread(stage: str, episode_id: int, db: Database) -> None:
    """
    Runs in a daemon background thread.
    Dispatches to the appropriate stage runner, emits events, persists the
    stage's DB status, and always emits a terminal stage_done event.
    """
    label = label_for_stage(stage)
    col   = db_column_for_stage(stage)
    tab   = ApiTab(episode_id, db)

    with _state_lock:
        _active_tabs[episode_id] = tab

    success = False
    log_id  = None
    try:
        tab._log(f"▶  Starting {label} …", "accent")
        try:
            db.set_episode_stage(episode_id, col, "running", progress=0)
        except Exception:
            pass  # never let a status write abort the run
        log_id = _log_start(db, episode_id, col)

        runner  = _get_runner(stage)
        success = bool(runner(tab))

        if success:
            tab._log(f"✓  {label} complete", "success")
        else:
            if tab._stop_flag:
                tab._log(f"⏹  {label} stopped", "warning")
            else:
                tab._log(f"✗  {label} failed", "error")

    except Exception as exc:
        tab._last_error = f"{exc}\n\n{traceback.format_exc()}"
        tab._log(f"✗  {label} crashed: {exc}", "error")
        success = False

    finally:
        _log_end(db, log_id, success=success, stopped=tab._stop_flag,
                 error=tab._last_error, lines=tab._log_lines)
        try:
            _persist_stage_status(
                db, episode_id, stage,
                success=success, stopped=tab._stop_flag,
            )
        except Exception:
            pass
        if success and not tab._stop_flag:
            _cascade_downstream(db, episode_id, stage)
        emit_stage_done(episode_id, stage, success)
        with _state_lock:
            _running.pop(episode_id,     None)
            _active_tabs.pop(episode_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# Chain dispatch (orchestrator — auto / resume)
# ══════════════════════════════════════════════════════════════════════════════

def _run_chain_thread(
    stages: list[str], episode_id: int, db: Database, *,
    stop_at_checkpoint: bool,
) -> None:
    """
    Run a list of stages sequentially in ONE background thread.

    Unlike _run_stage_thread (one stage → one terminal stage_done), a chain
    emits a NON-terminal stage_advance after each stage and a single terminal
    stage_done at the very end, so the SSE stream stays open for the whole run.

    Behaviour:
      • Aborts the whole chain on the first stage failure or a stop request.
      • Shares one ApiTab across all stages (registered for /stop to reach).
      • For an auto run (stop_at_checkpoint=True) the final stage_done carries
        checkpoint=True when the chain completed successfully — the UI opens the
        review gate.
      • An empty `stages` list is valid: nothing to run, emit the terminal event
        immediately (e.g. narration already done → straight to review).
    """
    tab = ApiTab(episode_id, db)
    with _state_lock:
        _active_tabs[episode_id] = tab

    success = True
    try:
        for stage in stages:
            if tab._stop_flag:
                emit_log(episode_id, "⏹  Run stopped before next stage", "warning")
                success = False
                break

            label = label_for_stage(stage)
            col   = db_column_for_stage(stage)
            tab._active_engine = None  # reset so /stop targets the current engine
            tab._last_error = ""
            tab.reset_log_buffer()     # fresh transcript per stage in the chain
            tab._log(f"▶  Starting {label} …", "accent")
            try:
                db.set_episode_stage(episode_id, col, "running", progress=0)
            except Exception:
                pass
            log_id = _log_start(db, episode_id, col)

            ok = False
            try:
                runner = _get_runner(stage)
                ok = bool(runner(tab))
            except Exception as exc:
                tab._last_error = f"{exc}\n\n{traceback.format_exc()}"
                tab._log(f"✗  {label} crashed: {exc}", "error")
                ok = False

            _log_end(db, log_id, success=ok, stopped=tab._stop_flag,
                     error=tab._last_error, lines=tab._log_lines)
            try:
                _persist_stage_status(
                    db, episode_id, stage, success=ok, stopped=tab._stop_flag
                )
            except Exception:
                pass

            if ok:
                if not tab._stop_flag:
                    _cascade_downstream(db, episode_id, stage)
                emit_log(episode_id, f"✓  {label} complete", "success")
                emit_stage_advance(episode_id, stage, True)
            else:
                if tab._stop_flag:
                    emit_log(episode_id, f"⏹  {label} stopped — run halted", "warning")
                else:
                    emit_log(episode_id, f"✗  {label} failed — run halted", "error")
                emit_stage_advance(episode_id, stage, False)
                success = False
                break

    except Exception as exc:
        emit_log(episode_id, f"✗  Run crashed: {exc}", "error")
        success = False

    finally:
        checkpoint = bool(stop_at_checkpoint and success)
        if checkpoint:
            emit_log(
                episode_id,
                "◆  Review checkpoint reached — edit the script, then Approve & finish.",
                "accent",
            )
        elif success and not stop_at_checkpoint:
            emit_log(episode_id, "✓  Run finished", "success")
        emit_stage_done(
            episode_id, "__chain__", success,
            checkpoint=checkpoint, chain=True,
        )
        with _state_lock:
            _running.pop(episode_id,     None)
            _active_tabs.pop(episode_id, None)


def _ensure_not_running(episode_id: int) -> None:
    """Raise 409 if a stage or chain is already running for the episode."""
    with _state_lock:
        thread = _running.get(episode_id)
    if thread and thread.is_alive():
        raise HTTPException(
            409,
            f"A run is already in progress for episode {episode_id}. "
            "Stop it first with POST /pipeline/stop/{episode_id}.",
        )


def _launch_chain(
    stages: list[str], episode_id: int, db: Database, *,
    stop_at_checkpoint: bool, name: str,
) -> None:
    """Clear stale events and start the chain thread (registered for /stop)."""
    clear_queue(episode_id)
    thread = threading.Thread(
        target = _run_chain_thread,
        args   = (stages, episode_id, db),
        kwargs = {"stop_at_checkpoint": stop_at_checkpoint},
        daemon = True,
        name   = name,
    )
    with _state_lock:
        _running[episode_id] = thread
    thread.start()


# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/run", response_model=OkResponse, status_code=202)
def run_stage(body: PipelineRunRequest, db: Database = Depends(get_db)):
    """
    Start a pipeline stage for an episode.

    Returns 202 Accepted immediately.  Connect to GET /pipeline/events/{id}
    to receive real-time log and progress events via SSE.

    Returns 409 if a stage is already running for this episode.
    Returns 404 if the episode does not exist.
    """
    ep = db.get_episode(body.episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {body.episode_id} not found")

    with _state_lock:
        thread = _running.get(body.episode_id)
        if thread and thread.is_alive():
            raise HTTPException(
                409,
                f"A stage is already running for episode {body.episode_id}. "
                "Stop it first with POST /pipeline/stop/{episode_id}."
            )

    # Some stages automatically continue into the next so the cycle runs in one
    # click. Dub → Sync (which includes the auto-fix) keeps the dub and its sync
    # together, the way the user works — no separate Sync click needed.
    followups = _RUN_FOLLOWUPS.get(body.stage, [])
    if followups:
        chain = [body.stage] + followups
        _launch_chain(
            chain, body.episode_id, db,
            stop_at_checkpoint=False, name=f"run-{body.stage}-ep{body.episode_id}",
        )
        flow = " → ".join(label_for_stage(s) for s in chain)
        return OkResponse(ok=True, message=f"Running {flow} for episode {body.episode_id}")

    # Clear any stale events from a previous run before starting
    clear_queue(body.episode_id)

    thread = threading.Thread(
        target = _run_stage_thread,
        args   = (body.stage, body.episode_id, db),
        daemon = True,
        name   = f"stage-{body.stage}-ep{body.episode_id}",
    )

    with _state_lock:
        _running[body.episode_id] = thread

    thread.start()

    label = label_for_stage(body.stage)
    return OkResponse(ok=True, message=f"Stage '{label}' started for episode {body.episode_id}")


@router.post("/stop/{episode_id}", response_model=OkResponse)
def stop_stage(episode_id: int):
    """
    Request an abort of the currently running stage for an episode.

    Sets the stop flag on the ApiTab (checked by stage runners between
    batches) and calls abort() on the active engine if it supports it.
    Also pushes a terminal stage_done event so the SSE stream closes cleanly.
    """
    with _state_lock:
        tab    = _active_tabs.get(episode_id)
        thread = _running.get(episode_id)

    if not tab and (not thread or not thread.is_alive()):
        return OkResponse(ok=True, message="No stage currently running")

    # Signal the runner loop to exit on its next check
    if tab:
        tab._stop_flag = True

        # Tell the engine to abort its current operation
        engine = tab._active_engine
        if engine is not None:
            for method_name in ("abort", "stop", "cancel"):
                method = getattr(engine, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
                    break

    emit_log(episode_id, "Stop requested …", "warning")
    return OkResponse(ok=True, message="Stop signal sent")


@router.get("/events/{episode_id}")
async def events(episode_id: int):
    """
    Server-Sent Events stream for a running stage.

    Connect immediately after POST /pipeline/run.  The stream emits:
      { type: "log",        message, level }
      { type: "progress",   pct, message }
      { type: "stage_done", stage, success }   ← terminal event

    The stream closes itself after "stage_done" is emitted.

    Browser / pywebview usage (JavaScript):
        const es = new EventSource(`http://127.0.0.1:8000/api/pipeline/events/${id}`)
        es.onmessage = (e) => {
            const event = JSON.parse(e.data)
            // handle log / progress / stage_done
        }
        // EventSource closes automatically when "stage_done" arrives
        // (the server closes the connection)
    """
    return StreamingResponse(
        stream_events(episode_id),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering":"no",      # disable nginx buffering
            "Connection":       "keep-alive",
        },
    )


@router.get("/panels/{episode_id}", response_model=list[PanelResponse])
def list_panels(episode_id: int, db: Database = Depends(get_db)):
    """
    Return all panels for an episode ordered by panel_index.

    Used by the Pipeline page to render the panel table (transcript,
    narration, image path, translation status per language).
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    panels = db.list_panels(episode_id)
    return [
        PanelResponse(
            id              = p["id"],
            episode_id      = p["episode_id"],
            panel_index     = p["panel_index"],
            transcript_text = p.get("transcript_text") or "",
            narration_text  = p.get("narration_text")  or "",
            image_path      = p.get("image_path")       or "",
            updated_at      = p.get("updated_at")       or 0.0,
        )
        for p in panels
    ]


@router.get("/episode/{episode_id}", response_model=EpisodeResponse)
def get_episode_status(episode_id: int, db: Database = Depends(get_db)):
    """
    Return fresh episode data including all stage statuses and overall progress.

    The Pipeline page polls this endpoint after each stage_done SSE event
    to update its stage status grid without a full page reload.
    """
    row = db.get_episode(episode_id)
    if not row:
        raise HTTPException(404, f"Episode {episode_id} not found")

    # The Pipeline page keys its stage grid by RUNNABLE stage key, but the
    # episodes table stores status under the DB column (which differs for
    # video_refine/pdf_slice → "extract" and pdf_narrate → "narrate").
    # Translate each runnable key to its column before reading, otherwise the
    # grid reads a non-existent stage_<key> column and always shows "pending".
    stages: dict = {}
    for stage in _ALL_STAGES:
        col      = db_column_for_stage(stage)
        status   = row.get(f"stage_{col}") or "pending"
        progress = int(row.get(f"progress_{col}") or 0)
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
        overall       = overall_progress(db, episode_id),
        total_panels  = int(row.get("total_panels") or 0),
        duration_secs = row.get("duration_secs"),
        total_pages   = row.get("total_pages"),
        error_message = row.get("error_message") or "",
        created_at    = row["created_at"],
        updated_at    = row["updated_at"],
    )


@router.get("/running/{episode_id}")
def is_running(episode_id: int):
    """Check whether a stage is currently running for an episode."""
    with _state_lock:
        thread = _running.get(episode_id)
    return {
        "running":    bool(thread and thread.is_alive()),
        "episode_id": episode_id,
    }


# ── Orchestrator routes (auto-chain to checkpoint / resume / plan) ─────────────

@router.post("/auto/{episode_id}", response_model=OkResponse, status_code=202)
def run_auto(episode_id: int, db: Database = Depends(get_db)):
    """
    "Run all": run the WHOLE pipeline end-to-end in one background run — analysis,
    narration, translate, dub, sync, assemble — with NO review-checkpoint pause.
    Clicking Run all means "do everything"; per-stage runs remain for granular
    review.  Stages already done/skipped are skipped, so it is resumable.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    _ensure_not_running(episode_id)
    # Full chain = pre-checkpoint (detect/refine…) + post-checkpoint (translate/
    # dub/sync/assemble), both filtered to what still needs running.
    stages = orchestrator.auto_chain(db, episode_id) + orchestrator.resume_chain(db, episode_id)
    _launch_chain(
        stages, episode_id, db,
        stop_at_checkpoint=False, name=f"auto-ep{episode_id}",
    )
    msg = (
        f"Run all started — {len(stages)} stage(s), end to end"
        if stages else
        "Nothing to run — every stage is already done"
    )
    return OkResponse(ok=True, message=msg)


@router.post("/resume/{episode_id}", response_model=OkResponse, status_code=202)
def run_resume(episode_id: int, db: Database = Depends(get_db)):
    """
    "Approve & finish": after the review checkpoint, run the post-checkpoint
    stages (translate → dub → sync(video) → assemble).  Rejected with 409 if
    narration is not complete yet.  Stages already done/skipped are skipped.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    _ensure_not_running(episode_id)
    if not orchestrator.narration_ready(db, episode_id):
        raise HTTPException(
            409,
            "Narration is not complete yet — run the pre-checkpoint stages "
            "first (POST /pipeline/auto/{episode_id}).",
        )

    stages = orchestrator.resume_chain(db, episode_id)
    _launch_chain(
        stages, episode_id, db,
        stop_at_checkpoint=False, name=f"resume-ep{episode_id}",
    )
    msg = (
        f"Finishing run — {len(stages)} stage(s)"
        if stages else
        "Episode already assembled — nothing to finish"
    )
    return OkResponse(ok=True, message=msg)


@router.get("/plan/{episode_id}")
def get_plan(episode_id: int, db: Database = Depends(get_db)):
    """
    Return the ordered stage plan for an episode, annotated with each stage's
    current status/progress and the review-checkpoint position.  Drives the
    Auto-mode timeline in the Pipeline page.
    """
    plan = orchestrator.plan_for_episode(db, episode_id)
    if plan is None:
        raise HTTPException(404, f"Episode {episode_id} not found")
    return plan
