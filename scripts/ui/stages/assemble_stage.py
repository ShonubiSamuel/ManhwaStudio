"""
ui/stages/assemble_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
ASSEMBLE stage — final video build (placeholder until video_builder.py lands).
"""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import BG, TEXT_DIM, FS


def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    tab._stage_top_bar(parent, key)

    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="x", padx=16, pady=16)

    ep  = tab._episode or {}
    out = ep.get("output_folder", "—")

    tk.Label(
        inner,
        text=(
            "Assembles final dubbed video — one per language.\n\n"
            "video_builder.py combines panel images/clips with the\n"
            "synced dubbed audio tracks from the SYNC stage.\n\n"
            "This stage is not yet implemented and will be skipped automatically."
        ),
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
    ).pack(anchor="w", pady=(0, 12))

    tk.Label(
        inner,
        text=f"Output folder:  {out}",
        font=FS, bg=BG, fg=TEXT_DIM, wraplength=560,
    ).pack(anchor="w")


def load(tab: "PipelineTab"):
    pass


def runner(tab: "PipelineTab") -> bool:
    """
    ASSEMBLE is not yet implemented.

    Previously this returned False which caused pipeline_tab to mark the
    episode stage as 'failed'.  Now it marks the stage as 'skipped' and
    returns True so the episode is not poisoned when video_builder.py is
    absent.
    """
    tab._log(
        "ASSEMBLE — video_builder.py not yet implemented; stage skipped",
        "warning",
    )
    tab.db.set_episode_stage(tab._episode_id, "assemble", "skipped")
    return True