"""
ui/stages/upscale_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
UPSCALE stage — Real-ESRGAN 4× upscaling (screenshots pipeline only).
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL2, BORDER, ACCENT, TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, FS, FL, FB
)
from ui.widgets import _btn


def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the UPSCALE stage UI."""
    tab._stage_top_bar(parent, key)
    
    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=16, pady=10)

    src = (tab._episode or {}).get("source_type", "pdf").lower()
    is_screenshots = (src == "screenshots")

    if not is_screenshots:
        tk.Label(inner, text="STEP 1 — INTAKE SCREENSHOTS", font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4))
        tk.Label(inner,
            text="Select the panel screenshots you saved from the manhwa site.\n"
                 "They will be renamed to panel_0000.jpg … in panel order.",
            font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
        ).pack(anchor="w", pady=(0, 8))

        intake_row = tk.Frame(inner, bg=BG)
        intake_row.pack(fill="x", pady=(0, 4))
        _btn(intake_row, "SELECT SCREENSHOT FOLDER", lambda: _intake_from_folder(tab), bg=PANEL2, pady=5, padx=10).pack(side="left", padx=(0, 8))
        _btn(intake_row, "SELECT INDIVIDUAL FILES", lambda: _intake_individual_files(tab), bg=PANEL2, pady=5, padx=10).pack(side="left")

        tab._intake_result_lbl = tk.Label(inner, text="", font=FS, bg=BG, fg=SUCCESS)
        tab._intake_result_lbl.pack(anchor="w", pady=(4, 10))

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(0, 10))
        upscale_label = "STEP 2 — UPSCALE  (4× Real-ESRGAN)"
    else:
        tk.Label(inner, text="Panels managed via Library → Screenshot Manager.", font=FS, bg=BG, fg=MUTED, justify="left").pack(anchor="w", pady=(0, 10))
        upscale_label = "UPSCALE  (4× Real-ESRGAN)"

    tk.Label(inner, text=upscale_label, font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4))
    tk.Label(inner,
        text="Requires the Real-ESRGAN model file at:\n"
             f"{config.BASE_DIR / 'models' / 'RealESRGAN_x4plus_anime_6B.pth'}",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
    ).pack(anchor="w", pady=(0, 8))

    tab._upscale_info_lbl = tk.Label(inner, text="", font=FB, bg=BG, fg=TEXT)
    tab._upscale_info_lbl.pack(anchor="w")


def load(tab: "PipelineTab"):
    """Refresh upscale panel counts."""
    if not tab._episode_id: return
    panels   = tab.db.list_panels(tab._episode_id)
    n_total  = len(panels)
    n_up     = sum(1 for p in panels if p.get("upscaled_path"))
    
    if hasattr(tab, "_upscale_info_lbl"):
        tab._upscale_info_lbl.config(text=f"{n_up} / {n_total} panels upscaled")


def runner(tab: "PipelineTab") -> bool:
    """Run Real-ESRGAN upscaling on all panel images."""
    from image_upscaler import ImageUpscaler

    ep     = tab.db.get_episode(tab._episode_id)
    engine = ImageUpscaler(tab.db, ep["output_folder"], on_log=tab._log)
    tab._active_engine = engine
    return engine.upscale_panels(
        tab._episode_id,
        on_progress = tab._on_progress,
    )


def _intake_from_folder(tab: "PipelineTab"):
    folder = filedialog.askdirectory(title="Select Screenshots Folder")
    if not folder: return
    _run_intake(tab, folder)


def _intake_individual_files(tab: "PipelineTab"):
    files = filedialog.askopenfilenames(
        title="Select Panel Screenshots",
        filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.tiff *.bmp"), ("All files", "*")]
    )
    if not files: return
    _run_intake(tab, list(files))


def _run_intake(tab: "PipelineTab", source):
    """Run ImageUpscaler.intake_screenshots in a background thread."""
    def _bg():
        try:
            from image_upscaler import ImageUpscaler
            ep = tab.db.get_episode(tab._episode_id)
            engine = ImageUpscaler(tab.db, ep["output_folder"], on_log=tab._log)
            ok = engine.intake_screenshots(tab._episode_id, source, on_progress=tab._on_progress)
            panels = tab.db.list_panels(tab._episode_id)
            n = len(panels)
            msg = f"{n} screenshot(s) registered ✓" if ok else "Intake failed"
            level = "success" if ok else "error"
            
            tab.after(0, lambda: (
                hasattr(tab, "_intake_result_lbl") and tab._intake_result_lbl.config(text=msg, fg=SUCCESS if ok else ERROR),
                load(tab)
            ))
            tab._log(msg, level)
        except Exception as exc:
            tab._log(f"Intake error: {exc}", "error")
        finally:
            tab.after(0, lambda: tab._set_ui_running(False))

    tab._set_ui_running(True)
    threading.Thread(target=_bg, daemon=True, name="intake").start()