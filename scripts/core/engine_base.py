"""
core/engine_base.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Base class for VideoEngine, PdfSlicer, and ImageUpscaler.
Provides the three methods that were previously copy-pasted across all three:
  _abort()  _log()  stop()

Engines that inherit this class only need to call super().__init__(db, on_log).
"""

from __future__ import annotations

from typing import Callable, Optional


class BaseEngine:
    """
    Minimal shared base for all ManhwaStudio pipeline engines.

    Subclass and override the _prefix class attribute to get a
    labelled log prefix automatically.

    Usage
    ─────
        class VideoEngine(BaseEngine):
            _prefix = "VideoEngine"

            def __init__(self, db, output_folder, on_log=None):
                super().__init__(db, on_log)
                self.output_folder = output_folder
    """

    _prefix: str = "Engine"   # override in each subclass for log labels

    def __init__(self, db, on_log: Optional[Callable] = None):
        self.db         = db
        self.on_log     = on_log
        self._stop_flag = False

    # ── Public ────────────────────────────────────────────────────────────────

    def stop(self):
        """Signal the running stage to cancel after its current unit of work."""
        self._stop_flag = True

    # ── Private helpers ───────────────────────────────────────────────────────

    def _abort(self, episode_id: int, log_id: int, stage: str) -> bool:
        """
        Mark a stage failed due to user cancellation.
        Always returns False so callers can do: return self._abort(...)

        log_id may be 0 or None if the stage was cancelled before
        log_stage_start() was called — the log_stage_end call is
        skipped safely in that case.
        """
        self.db.set_episode_stage(
            episode_id, stage, "failed", error="Cancelled by user"
        )
        if log_id:
            self.db.log_stage_end(log_id, "failed", error="Cancelled by user")
        self._log("Cancelled", "warning")
        return False

    def _log(self, msg: str, level: str = "info") -> None:
        if self.on_log:
            self.on_log(msg, level)
        else:
            print(f"[{self._prefix}] {msg}")