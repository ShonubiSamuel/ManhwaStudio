"""Evaluation harness for the dubbing pipeline.

Reference-free fit metrics (metrics.py) run on any session already on disk.
Reference-based translation/ASR scoring (references.py) needs supplied
references and the optional sacrebleu / jiwer packages.
"""

from .metrics import FitReport, evaluate_fit, usable_duration
from .report import aggregate, load_sessions, render_markdown, score_session

__all__ = [
    "FitReport",
    "evaluate_fit",
    "usable_duration",
    "load_sessions",
    "score_session",
    "aggregate",
    "render_markdown",
]
