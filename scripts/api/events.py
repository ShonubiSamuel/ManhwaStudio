"""
scripts/api/events.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Server-Sent Events (SSE) infrastructure.

Replaces the Tkinter on_log / on_progress callbacks.  Stage runners running
in background threads call emit_*() functions here.  The async SSE endpoint
in pipeline.py polls the per-episode queue and streams events to React.

Thread model
────────────
    Background thread  →  emit_log() / emit_progress()  →  queue.Queue
    Async SSE handler  ←  stream_events() polls queue    ←  React EventSource

Event types
───────────
    {"type": "log",           "message": "...",  "level": "info|warning|error|..."}
    {"type": "progress",      "pct": 0-100,      "message": "..."}
    {"type": "stage_advance", "stage": "...",     "success": true|false}
    {"type": "stage_done",    "stage": "...",     "success": true|false, ...}

The "stage_done" event is the terminal event — stream_events() stops
yielding after it so React knows the SSE connection can be closed.

"stage_advance" is a NON-terminal event used by the orchestrator to report
that one stage of a multi-stage chain finished while the chain continues
running.  The stream stays open; only the final chain completion emits
"stage_done".  A single-stage manual run still emits "stage_done" directly.

The terminal "stage_done" may carry extra fields the orchestrator sets, e.g.
  "checkpoint": true   → the chain stopped at the review checkpoint (auto run)
  "chain":      true   → this terminated a multi-stage chain (vs one stage)
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Dict, AsyncGenerator

# ── Per-episode queues ────────────────────────────────────────────────────────

_queues:     Dict[int, queue.Queue] = {}
_queue_lock: threading.Lock         = threading.Lock()


def _get_queue(episode_id: int) -> queue.Queue:
    """Return the queue for an episode, creating it if needed."""
    with _queue_lock:
        if episode_id not in _queues:
            _queues[episode_id] = queue.Queue()
        return _queues[episode_id]


def clear_queue(episode_id: int) -> None:
    """
    Replace the episode's queue with a fresh empty one.
    Called before starting a new stage run to discard any stale events
    from a previous run (e.g. if the client reconnects mid-run).
    """
    with _queue_lock:
        _queues[episode_id] = queue.Queue()


# ── Emitters (called from background threads) ─────────────────────────────────

def emit(episode_id: int, event: dict) -> None:
    """Push any event dict onto the episode's queue."""
    _get_queue(episode_id).put(event)


def emit_log(episode_id: int, message: str, level: str = "info") -> None:
    """
    Push a log line.  level matches the Tkinter log level system:
    "info" | "accent" | "success" | "warning" | "error" | "muted"
    """
    emit(episode_id, {
        "type":    "log",
        "message": str(message),
        "level":   level,
    })


def emit_progress(episode_id: int, pct: int, message: str = "") -> None:
    """Push a progress update (0–100)."""
    emit(episode_id, {
        "type":    "progress",
        "pct":     max(0, min(100, int(pct))),
        "message": str(message),
    })


def emit_stage_advance(episode_id: int, stage: str, success: bool) -> None:
    """
    Push a NON-terminal stage-transition event for a multi-stage chain.
    The stream stays open; the orchestrator keeps running the next stage.
    """
    emit(episode_id, {
        "type":    "stage_advance",
        "stage":   stage,
        "success": bool(success),
    })


def emit_stage_done(episode_id: int, stage: str, success: bool, **extra) -> None:
    """
    Push the terminal event for a stage run (or the end of a chain).
    stream_events() stops yielding after receiving this.

    Extra keyword fields are merged into the event payload — used by the
    orchestrator to flag e.g. checkpoint=True / chain=True.
    """
    event = {
        "type":    "stage_done",
        "stage":   stage,
        "success": bool(success),
    }
    event.update(extra)
    emit(episode_id, event)


# ── SSE stream (consumed by the async FastAPI endpoint) ───────────────────────

async def stream_events(episode_id: int) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted strings for one episode.

    Polls the thread-safe queue every 100 ms.  Drains all available events
    each iteration so fast stages don't introduce unnecessary latency.
    Yields ": keepalive" comment lines between events so proxies and pywebview
    don't close the connection due to inactivity.

    Terminates automatically when a "stage_done" event is emitted.

    Usage in FastAPI:
        return StreamingResponse(stream_events(episode_id),
                                 media_type="text/event-stream")
    """
    q = _get_queue(episode_id)

    while True:
        had_event = False

        # Drain every event currently in the queue
        while True:
            try:
                event = q.get_nowait()
                had_event = True
                yield f"data: {json.dumps(event)}\n\n"

                # Terminal event — stop the stream
                if event.get("type") == "stage_done":
                    return

            except queue.Empty:
                break

        # If no events were available, send a keepalive and wait
        if not had_event:
            yield ": keepalive\n\n"

        await asyncio.sleep(0.1)   # 100 ms poll interval
