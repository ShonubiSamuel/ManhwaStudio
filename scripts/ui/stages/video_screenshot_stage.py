"""
ui/stages/video_screenshot_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
VIDEO SCREENSHOT stage — Grabs one representative frame per panel (video only).
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import BG, PANEL2, TEXT, MUTED, FS, FB
from ui.widgets import _btn


def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the SCREENSHOT stage UI."""
    tab._stage_top_bar(parent, key)

    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="x", padx=16, pady=16)

    tab._screenshot_info_lbl = tk.Label(inner, text="", font=FB, bg=BG, fg=TEXT)
    tab._screenshot_info_lbl.pack(anchor="w", pady=(0, 8))

    tab._screenshot_folder_lbl = tk.Label(inner, text="", font=FS, bg=BG, fg=MUTED, wraplength=560)
    tab._screenshot_folder_lbl.pack(anchor="w", pady=(0, 12))

    _btn(inner, "OPEN PANELS FOLDER", 
         lambda: _open_panels_folder(tab), bg=PANEL2).pack(anchor="w")


def load(tab: "PipelineTab"):
    """Populate labels with current screenshot count."""
    if not tab._episode:
        return
    panels  = tab.db.list_panels(tab._episode_id)
    n_done  = sum(1 for p in panels if p.get("screenshot_path"))
    folder  = tab._episode.get("panels_folder") or "—"
    
    if hasattr(tab, "_screenshot_info_lbl"):
        tab._screenshot_info_lbl.config(
            text=f"{n_done} of {len(panels)} screenshots extracted"
        )
    if hasattr(tab, "_screenshot_folder_lbl"):
        tab._screenshot_folder_lbl.config(text=folder)


def runner(tab: "PipelineTab") -> bool:
    """Extract one screenshot frame per panel (video source)."""
    from video_engine import VideoEngine, DetectionParams

    ep     = tab.db.get_episode(tab._episode_id)
    engine = VideoEngine(tab.db, ep["output_folder"], on_log=tab._log)
    tab._active_engine = engine
    return engine.extract_screenshots(
        tab._episode_id,
        params      = DetectionParams(),
        on_progress = tab._on_progress,
    )


def _open_panels_folder(tab: "PipelineTab"):
    if not tab._episode:
        return
    folder = tab._episode.get("panels_folder")
    if folder and Path(folder).exists():
        import os, sys
        if sys.platform == "darwin": 
            os.system(f"open '{folder}'")
        elif sys.platform == "win32": 
            os.startfile(str(folder))
        else: 
            os.system(f"xdg-open '{folder}'")
    else:
        tab._log("Panels folder not found yet — run SCREENSHOT stage first", "warning")