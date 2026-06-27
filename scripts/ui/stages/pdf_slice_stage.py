"""
ui/stages/pdf_slice_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
PDF SLICE stage — Slices PDF pages into panels and downscales/optimizes them.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL2, BORDER, ACCENT, TEXT, TEXT_DIM, MUTED, SUCCESS, FS, FL, BTN_BG
)
from ui.widgets import _btn


def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """PDF slice + downscale stage UI."""
    tab._stage_top_bar(parent, key)

    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=16, pady=10)

    tk.Label(inner, text="SLICE SETTINGS", font=FL, bg=BG,
             fg=ACCENT).pack(anchor="w", pady=(0, 8))

    # ── Slice mode ────────────────────────────────────────────────────────
    row = tk.Frame(inner, bg=BG); row.pack(fill="x", pady=(0, 6))
    tk.Label(row, text="Slice mode:", font=FS, bg=BG, fg=TEXT_DIM,
             width=18, anchor="w").pack(side="left")
    tab._slice_mode_var = tk.StringVar(value=config.NARR_MODE)
    for val, lbl in [("page", "Page"), ("slice", "Slice by height"), ("merge", "Merge pages")]:
        tk.Radiobutton(row, text=lbl, variable=tab._slice_mode_var, value=val,
                       font=FS, bg=BG, fg=TEXT, selectcolor=BG,
                       activebackground=BG, activeforeground=ACCENT
                       ).pack(side="left", padx=(0, 12))

    # ── DPI ───────────────────────────────────────────────────────────────
    row2 = tk.Frame(inner, bg=BG); row2.pack(fill="x", pady=(0, 4))
    tk.Label(row2, text="DPI:", font=FS, bg=BG, fg=TEXT_DIM,
             width=18, anchor="w").pack(side="left")
    tab._slice_dpi_var = tk.StringVar(value=str(config.PDF_DPI))
    tk.Entry(row2, textvariable=tab._slice_dpi_var, font=FS, width=6,
             bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left")
    tk.Label(row2, text="(higher = sharper, slower)", font=FS, bg=BG,
             fg=MUTED).pack(side="left", padx=(8, 0))

    # ── Merge count ───────────────────────────────────────────────────────
    row3 = tk.Frame(inner, bg=BG); row3.pack(fill="x", pady=(0, 4))
    tk.Label(row3, text="Pages per merge:", font=FS, bg=BG, fg=TEXT_DIM,
             width=18, anchor="w").pack(side="left")
    tab._slice_merge_var = tk.StringVar(value=str(config.NARR_MERGE_COUNT))
    tk.Entry(row3, textvariable=tab._slice_merge_var, font=FS, width=4,
             bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left")
    tk.Label(row3, text="(merge mode only)", font=FS, bg=BG,
             fg=MUTED).pack(side="left", padx=(8, 0))

    # ── Skip cover / back page ────────────────────────────────────────────
    tab._slice_skip_var = tk.BooleanVar(value=config.PDF_SKIP_FIRST_LAST)
    tk.Checkbutton(inner, text="Skip first and last page (cover + back)",
                   variable=tab._slice_skip_var,
                   font=FS, bg=BG, fg=TEXT, selectcolor=BG,
                   activebackground=BG).pack(anchor="w", pady=(0, 10))

    tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(0, 10))
    tk.Label(inner, text="DOWNSCALE / OPTIMIZE", font=FL, bg=BG,
             fg=ACCENT).pack(anchor="w", pady=(0, 8))

    # ── Max width ─────────────────────────────────────────────────────────
    row4 = tk.Frame(inner, bg=BG); row4.pack(fill="x", pady=(0, 4))
    tk.Label(row4, text="Max width (px):", font=FS, bg=BG, fg=TEXT_DIM,
             width=18, anchor="w").pack(side="left")
    tab._slice_maxw_var = tk.StringVar(value=str(config.OPT_MAX_WIDTH))
    tk.Entry(row4, textvariable=tab._slice_maxw_var, font=FS, width=6,
             bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left")

    # ── JPEG quality ──────────────────────────────────────────────────────
    row5 = tk.Frame(inner, bg=BG); row5.pack(fill="x", pady=(0, 4))
    tk.Label(row5, text="JPEG quality:", font=FS, bg=BG, fg=TEXT_DIM,
             width=18, anchor="w").pack(side="left")
    tab._slice_quality_var = tk.StringVar(value=str(config.OPT_JPEG_QUALITY))
    tk.Entry(row5, textvariable=tab._slice_quality_var, font=FS, width=4,
             bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left")
    tk.Label(row5, text="(1–95; 65 = lean, 85 = high quality)", font=FS,
             bg=BG, fg=MUTED).pack(side="left", padx=(8, 0))

    # ── Grayscale + autocrop + sharpen ────────────────────────────────────
    opts_row = tk.Frame(inner, bg=BG); opts_row.pack(anchor="w", pady=(0, 10))
    tab._slice_gray_var = tk.BooleanVar(value=config.OPT_GRAYSCALE)
    tab._slice_crop_var = tk.BooleanVar(value=config.OPT_AUTOCROP)
    tab._slice_shrp_var = tk.BooleanVar(value=config.OPT_SHARPEN)
    for var, lbl in [
        (tab._slice_gray_var, "Grayscale"),
        (tab._slice_crop_var, "Auto-crop"),
        (tab._slice_shrp_var, "Sharpen"),
    ]:
        tk.Checkbutton(opts_row, text=lbl, variable=var,
                       font=FS, bg=BG, fg=TEXT, selectcolor=BG,
                       activebackground=BG).pack(side="left", padx=(0, 14))

    # ── Result label + run button ─────────────────────────────────────────
    tab._slice_result_lbl = tk.Label(inner, text="", font=FS, bg=BG, fg=SUCCESS)
    tab._slice_result_lbl.pack(anchor="w", pady=(0, 4))

    def _open_narrator_folder():
        if not tab._episode:
            return
        folder = Path(tab._episode["output_folder"]) / "ai_narrator" / "optimized"
        folder.mkdir(parents=True, exist_ok=True)
        import os, sys
        if sys.platform == "darwin":
            os.system(f"open '{folder}'")
        elif sys.platform == "win32":
            os.startfile(str(folder))
        else:
            os.system(f"xdg-open '{folder}'")

    _btn(inner, "▶  RUN SLICE + DOWNSCALE",
         lambda: tab._run_single(key), bg=ACCENT, fg="#000", pady=4, padx=8
         ).pack(side="left", pady=(0, 4), padx=(0, 8))
    _btn(inner, "OPEN OUTPUT FOLDER",
         _open_narrator_folder, bg=PANEL2, pady=4, padx=8
         ).pack(side="left", pady=(0, 4))


def load(tab: "PipelineTab"):
    """No special load behavior required for slice stage."""
    pass


def runner(tab: "PipelineTab") -> bool:
    """
    PDF slice + optimize via PdfSlicer.prepare_for_narration.

    Fixes applied vs original:
      - Import changed from 'pdf_engine' (wrong) to 'pdf_slicer' (correct).
      - PdfSlicer constructor now receives the required output_base argument.
      - Parameters passed as SliceParams / OptimizeParams dataclass instances
        instead of plain dicts (prepare_for_narration accesses them as
        attributes, so dict access would raise AttributeError at runtime).
      - Tab variable 'skip_edges' renamed to match the SliceParams field
        name 'skip_first_last'.
      - tab._active_engine assigned so the STOP button can reach the engine.
      - Parameter helpers now use typed _get_str / _get_int / _get_bool
        functions that handle empty or invalid field values gracefully.
        The previous _get() helper returned var.get() as-is, so a cleared
        DPI field ("") would pass to int() and raise ValueError at runtime.
    """
    from pdf_slicer import PdfSlicer, SliceParams, OptimizeParams

    ep = tab.db.get_episode(tab._episode_id)
    if not ep:
        tab._log("Episode not found", "error")
        return False

    engine = PdfSlicer(tab.db, ep["output_folder"], on_log=tab._log)
    tab._active_engine = engine

    # ── Typed parameter helpers ───────────────────────────────────────────────
    # Each helper reads the named Tkinter variable from the tab, converts it
    # to the target type, and falls back to the config default if the variable
    # is absent, empty, or non-parseable.  This replaces the previous pattern:
    #
    #   getattr(tab, "_slice_dpi_var", None) and tab._slice_dpi_var.get()
    #       or config.PDF_DPI
    #
    # which silently falls back to the default whenever the value is falsy —
    # including the valid value 0 for integers and False for booleans.

    def _get_str(attr: str, default: str) -> str:
        var = getattr(tab, attr, None)
        if var is None:
            return str(default)
        val = var.get().strip() if hasattr(var, "get") else ""
        return val if val else str(default)

    def _get_int(attr: str, default: int) -> int:
        try:
            return int(_get_str(attr, default))
        except (ValueError, TypeError):
            return default

    def _get_bool(attr: str, default: bool) -> bool:
        var = getattr(tab, attr, None)
        if var is None:
            return default
        try:
            return bool(var.get())
        except Exception:
            return default

    sp = SliceParams(
        mode            = _get_str("_slice_mode_var",  config.NARR_MODE),
        dpi             = _get_int("_slice_dpi_var",   config.PDF_DPI),
        merge_count     = _get_int("_slice_merge_var", config.NARR_MERGE_COUNT),
        skip_first_last = _get_bool("_slice_skip_var", config.PDF_SKIP_FIRST_LAST),
    )
    op = OptimizeParams(
        max_width    = _get_int( "_slice_maxw_var",    config.OPT_MAX_WIDTH),
        jpeg_quality = _get_int( "_slice_quality_var", config.OPT_JPEG_QUALITY),
        grayscale    = _get_bool("_slice_gray_var",    config.OPT_GRAYSCALE),
        autocrop     = _get_bool("_slice_crop_var",    config.OPT_AUTOCROP),
        sharpen      = _get_bool("_slice_shrp_var",    config.OPT_SHARPEN),
    )

    ok = engine.prepare_for_narration(
        tab._episode_id,
        slice_params = sp,
        opt_params   = op,
        on_progress  = tab._on_progress,
    )

    if ok and hasattr(tab, "_slice_result_lbl"):
        opt_dir = Path(ep["output_folder"]) / "ai_narrator" / "optimized"
        n = len(list(opt_dir.glob("*.jpg"))) + len(list(opt_dir.glob("*.png")))
        tab.after(0, lambda: tab._slice_result_lbl.config(
            text=f"{n} optimised image(s) ready ✓"
        ))

    return ok
