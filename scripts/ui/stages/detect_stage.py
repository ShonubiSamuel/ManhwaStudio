"""
ui/stages/detect_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
DETECT stage — 4-step gated workflow:
  Step 1 — Configure detection settings
  Step 2 — Extract a short clip from the full video
  Step 3 — Open Parameter Tuner (interactive browser report)
  Step 4 — Preview on clip, then confirm & run on full video
"""

from __future__ import annotations

import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, _F, FS, FL,
)
from ui.widgets import _btn, _div, _scrolled_tree


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the DETECT stage UI inside parent."""
    tab._stage_top_bar(parent, key)

    # Scrollable container
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    vsb    = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=BG)
    win   = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
    canvas.bind_all("<MouseWheel>",
        lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    P = dict(padx=16)

    # ── STEP 1: Detection Settings ────────────────────────────────────────────
    _step_header(inner, "1", "DETECTION SETTINGS")
    tk.Label(inner,
        text="Configure the parameters used to detect panel cuts.\n"
             "Adjust these, then extract a clip and preview before running on the full video.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
    ).pack(anchor="w", pady=(0, 8), **P)

    # Settings grid
    sg = tk.Frame(inner, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
    sg.pack(fill="x", pady=(0, 8), **P)
    sg_inner = tk.Frame(sg, bg=PANEL2)
    sg_inner.pack(fill="x", padx=12, pady=8)

    # Mode + Priority
    tab._det_mode_var     = tk.StringVar(value="combined")
    tab._det_priority_var = tk.StringVar(value="combined")

    row0 = tk.Frame(sg_inner, bg=PANEL2); row0.pack(fill="x", pady=2)
    tk.Label(row0, text="Mode:", font=FS, bg=PANEL2, fg=TEXT_DIM,
             width=22, anchor="w").pack(side="left")
    om_mode = tk.OptionMenu(row0, tab._det_mode_var, "combined", "audio", "visual")
    om_mode.config(font=FS, bg=BTN_BG, fg=TEXT, activebackground=ACCENT2,
                   relief="flat", highlightthickness=0, width=14)
    om_mode["menu"].config(bg=BTN_BG, fg=TEXT, font=FS)
    om_mode.pack(side="left")
    tk.Label(row0, text="combined=both  audio=silence only  visual=scene only",
             font=FS, bg=PANEL2, fg=MUTED).pack(side="left", padx=8)

    row1 = tk.Frame(sg_inner, bg=PANEL2); row1.pack(fill="x", pady=2)
    tk.Label(row1, text="Merge priority:", font=FS, bg=PANEL2, fg=TEXT_DIM,
             width=22, anchor="w").pack(side="left")
    om_pri = tk.OptionMenu(row1, tab._det_priority_var,
                           "combined", "visual_first", "audio_first")
    om_pri.config(font=FS, bg=BTN_BG, fg=TEXT, activebackground=ACCENT2,
                  relief="flat", highlightthickness=0, width=14)
    om_pri["menu"].config(bg=BTN_BG, fg=TEXT, font=FS)
    om_pri.pack(side="left")

    # Numeric fields
    tab._det_vars = {}
    fields = [
        ("detect_silence_db",   "Silence threshold (dBFS):", "-45.0",
         "Lower = stricter silence. e.g. -45.0"),
        ("detect_min_silence",  "Min silence (sec):",        "0.25",
         "Shortest pause = panel gap. e.g. 0.25"),
        ("detect_threshold",    "Visual threshold:",         "3.0",
         "Lower = more sensitive. e.g. 3.0"),
        ("detect_min_scene",    "Min scene gap (sec):",      "1.5",
         "Min gap between visual cuts. e.g. 1.5"),
        ("detect_frame_skip",   "Frame skip:",               "2",
         "Every (N+1)th frame. 2 = every 3rd"),
        ("detect_merge_window", "Merge window (sec):",       "1.5",
         "Max gap to link audio+visual. e.g. 1.5"),
        ("detect_workers",      "Parallel workers:",         "4",
         "ffmpeg workers for cut export"),
    ]
    for fkey, label, default, hint in fields:
        var = tk.StringVar(value=default)
        tab._det_vars[fkey] = var
        r = tk.Frame(sg_inner, bg=PANEL2); r.pack(fill="x", pady=2)
        tk.Label(r, text=label, font=FS, bg=PANEL2, fg=TEXT_DIM,
                 width=22, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=var, font=FS, width=10,
                 bg=BTN_BG, fg=TEXT, insertbackground=ACCENT,
                 relief="flat", highlightthickness=1,
                 highlightcolor=ACCENT,
                 highlightbackground=BORDER).pack(side="left", padx=(0, 8))
        tk.Label(r, text=hint, font=FS, bg=PANEL2, fg=MUTED).pack(side="left")

    save_row = tk.Frame(inner, bg=BG)
    save_row.pack(fill="x", pady=(0, 8), **P)
    _btn(save_row, "💾  SAVE SETTINGS",
         lambda: _detect_save_settings(tab), bg=PANEL2, pady=4, padx=10
         ).pack(side="left", padx=(0, 8))
    _btn(save_row, "↺  RESET TO CONFIG DEFAULTS",
         lambda: _detect_reset_defaults(tab), bg=PANEL2, pady=4, padx=10
         ).pack(side="left")

    _div(inner)

    # ── STEP 2: Extract Clip ──────────────────────────────────────────────────
    _step_header(inner, "2", "EXTRACT A TEST CLIP")
    tk.Label(inner,
        text="Extract a short portion of the video to tune and preview settings on.\n"
             "Recommended: 1–3 minutes from a representative section.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
    ).pack(anchor="w", pady=(0, 6), **P)

    clip_row = tk.Frame(inner, bg=BG)
    clip_row.pack(fill="x", pady=(0, 4), **P)
    tk.Label(clip_row, text="Start time:", font=FS, bg=BG,
             fg=TEXT_DIM, width=12, anchor="w").pack(side="left")
    tab._detect_clip_start_var = tk.StringVar(value="00:00:00")
    tk.Entry(clip_row, textvariable=tab._detect_clip_start_var,
             font=FS, width=10, bg=BTN_BG, fg=TEXT,
             insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left", padx=(0, 12))
    tk.Label(clip_row, text="Duration (s):", font=FS, bg=BG,
             fg=TEXT_DIM).pack(side="left")
    tab._detect_clip_dur_var = tk.StringVar(value="120")
    tk.Entry(clip_row, textvariable=tab._detect_clip_dur_var,
             font=FS, width=6, bg=BTN_BG, fg=TEXT,
             insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left", padx=(0, 12))

    tab._detect_clip_status = tk.Label(inner, text="", font=FS, bg=BG, fg=TEXT_DIM)
    tab._detect_clip_status.pack(anchor="w", **P)

    _btn(inner, "▶  EXTRACT CLIP",
         lambda: _detect_extract_clip(tab),
         bg=ACCENT2, fg="#fff", pady=5, padx=12
         ).pack(anchor="w", pady=(4, 0), **P)

    _div(inner)

    # ── STEP 3: Parameter Tuner ───────────────────────────────────────────────
    _step_header(inner, "3", "OPEN PARAMETER TUNER")
    tk.Label(inner,
        text="Opens an interactive HTML report in your browser using the clip.\n"
             "Drag sliders to see exactly how each setting affects detection.\n"
             "Copy any better values back into Step 1 and save.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
    ).pack(anchor="w", pady=(0, 6), **P)
    _btn(inner, "🔍  OPEN PARAMETER TUNER",
         lambda: _detect_open_tuner(tab),
         bg=PANEL2, pady=5, padx=12
         ).pack(anchor="w", pady=(0, 0), **P)

    _div(inner)

    # ── STEP 4: Preview + Confirm & Run Full Detect ───────────────────────────
    _step_header(inner, "4", "PREVIEW + CONFIRM & RUN FULL DETECT")
    tk.Label(inner,
        text="Run detection on the clip to verify your settings, then confirm\n"
             "and run on the full video when satisfied.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
    ).pack(anchor="w", pady=(0, 8), **P)

    _btn(inner, "▶  RUN PREVIEW ON CLIP",
         lambda: _detect_run_preview(tab),
         bg=ACCENT2, fg="#fff", pady=5, padx=12
         ).pack(anchor="w", pady=(0, 6), **P)

    tab._detect_preview_status = tk.Label(inner, text="", font=FS, bg=BG, fg=TEXT_DIM)
    tab._detect_preview_status.pack(anchor="w", **P)

    tab._detect_tree = _scrolled_tree(
        inner,
        columns    = ("#", "start", "end", "duration", "transcript"),
        col_widths = {"#": 45, "start": 75, "end": 75,
                      "duration": 65, "transcript": 330},
    )
    tab._detect_preview_lbl = tk.Label(inner, text="", font=FS, bg=BG, fg=TEXT_DIM)
    tab._detect_preview_lbl.pack(anchor="w", **P, pady=(2, 0))

    _div(inner)

    tab._detect_confirm_status = tk.Label(inner,
        text="⚠  Run a preview first (Step 4)",
        font=(_F, 9, "bold"), bg=BG, fg=WARNING)
    tab._detect_confirm_status.pack(anchor="w", **P, pady=(8, 6))

    tab._detect_run_btn = _btn(inner,
        "✓  SATISFIED — SAVE SETTINGS & RUN FULL DETECT",
        lambda: _detect_confirm_and_run(tab),
        bg="#2a3a2a", fg=SUCCESS, pady=6, padx=14)
    tab._detect_run_btn.pack(anchor="w", **P, pady=(0, 12))


def load(tab: "PipelineTab"):
    """Populate settings fields and panel table from DB when stage opens."""
    ep = tab.db.get_episode(tab._episode_id) if tab._episode_id else None
    if not ep:
        return

    if hasattr(tab, "_detect_clip_start_var"):
        tab._detect_clip_start_var.set(ep.get("detect_clip_start") or "00:00:00")
    if hasattr(tab, "_detect_clip_dur_var"):
        tab._detect_clip_dur_var.set(str(ep.get("detect_clip_duration") or 120))

    if hasattr(tab, "_det_mode_var"):
        tab._det_mode_var.set(ep.get("detect_mode") or "combined")
    if hasattr(tab, "_det_priority_var"):
        tab._det_priority_var.set(ep.get("detect_priority") or "combined")
    if hasattr(tab, "_det_vars"):
        for key, var in tab._det_vars.items():
            val = ep.get(key)
            if val is not None:
                var.set(str(val))

    confirmed = bool(ep.get("detect_confirmed", 0))
    tab._detect_confirmed = confirmed
    if hasattr(tab, "_detect_confirm_status"):
        if confirmed:
            tab._detect_confirm_status.config(
                text="✓  Settings confirmed — run detect any time", fg=SUCCESS)
        else:
            tab._detect_confirm_status.config(
                text="⚠  Run a preview first (Step 3)", fg=WARNING)

    _load_detect_table_from_db(tab)


def runner(tab: "PipelineTab") -> bool:
    """
    DETECT stage — full pass on the source video:
      1. detect_panels        — find panel cuts and create panel rows.
      2. extract_screenshots  — grab one frame per detected panel.

    Both are needed before Refine/Review: detection creates the panels and
    screenshots give each panel an image to show beside its narration.
    """
    from video_engine import VideoEngine, detection_params_from_episode

    ep     = tab.db.get_episode(tab._episode_id)
    engine = VideoEngine(tab.db, ep["output_folder"], on_log=tab._log)
    tab._active_engine = engine
    params = detection_params_from_episode(ep)

    if not engine.detect_panels(tab._episode_id, params=params, on_progress=tab._on_progress):
        return False
    if tab._stop_flag:
        return False

    tab._log("Capturing one screenshot per detected panel …", "accent")
    return engine.extract_screenshots(
        tab._episode_id, params=params, on_progress=tab._on_progress,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _step_header(parent: tk.Frame, number: str, title: str):
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", padx=16, pady=(10, 4))
    tk.Label(row, text=f" {number} ", font=(_F, 9, "bold"),
             bg=ACCENT, fg="#000", padx=4, pady=1
             ).pack(side="left", padx=(0, 8))
    tk.Label(row, text=title, font=(_F, 10, "bold"),
             bg=BG, fg=TEXT).pack(side="left")
    tk.Frame(row, bg=BORDER, height=1).pack(
        side="left", fill="x", expand=True, padx=(10, 0))


def _load_detect_table_from_db(tab: "PipelineTab"):
    if not hasattr(tab, "_detect_tree") or not tab._episode_id:
        return
    tv     = tab._detect_tree
    panels = sorted(tab.db.list_panels(tab._episode_id),
                    key=lambda p: p["panel_index"])
    if not panels:
        return

    existing_transcripts = {}
    for iid in tv.get_children():
        vals = tv.item(iid, "values")
        if vals and len(vals) >= 5 and vals[4]:
            try:
                existing_transcripts[int(vals[0])] = vals[4]
            except ValueError:
                pass

    tv.delete(*tv.get_children())
    for p in panels:
        s         = p.get("start_time_sec") or 0
        e         = p.get("end_time_sec")   or 0
        dur       = round(e - s, 2)
        panel_num = p["panel_index"] + 1
        db_txt    = (p.get("transcript_text") or "").strip()
        txt       = (db_txt or existing_transcripts.get(panel_num, ""))[:80]
        tv.insert("", "end", values=(
            panel_num, f"{s:.2f}s", f"{e:.2f}s", f"{dur:.2f}s", txt,
        ))
    if hasattr(tab, "_detect_preview_lbl"):
        tab._detect_preview_lbl.config(
            text=f"{len(panels)} panels detected ✓", fg=SUCCESS)


def _detect_get_params(tab: "PipelineTab"):
    from video_engine import DetectionParams
    def _f(key, fallback):
        try:   return float(tab._det_vars[key].get())
        except Exception: return fallback
    def _i(key, fallback):
        try:   return int(float(tab._det_vars[key].get()))
        except Exception: return fallback
    return DetectionParams(
        mode             = tab._det_mode_var.get()     if hasattr(tab, "_det_mode_var")     else "combined",
        priority         = tab._det_priority_var.get() if hasattr(tab, "_det_priority_var") else "combined",
        silence_db       = _f("detect_silence_db",   -45.0),
        min_silence_sec  = _f("detect_min_silence",   0.25),
        visual_threshold = _f("detect_threshold",      3.0),
        min_scene_sec    = _f("detect_min_scene",      1.5),
        frame_skip       = _i("detect_frame_skip",       2),
        merge_window     = _f("detect_merge_window",   1.5),
        workers          = _i("detect_workers",          4),
    )


def _detect_save_settings(tab: "PipelineTab", reset_confirm: bool = True):
    if not tab._episode_id:
        return
    params = _detect_get_params(tab)
    try:
        start = tab._detect_clip_start_var.get().strip() if hasattr(tab, "_detect_clip_start_var") else "00:00:00"
        dur   = int(tab._detect_clip_dur_var.get())      if hasattr(tab, "_detect_clip_dur_var")   else 120
    except Exception:
        start, dur = "00:00:00", 120
    tab.db.update_episode(tab._episode_id,
        detect_mode         = params.mode,
        detect_priority     = params.priority,
        detect_silence_db   = params.silence_db,
        detect_min_silence  = params.min_silence_sec,
        detect_threshold    = params.visual_threshold,
        detect_min_scene    = params.min_scene_sec,
        detect_frame_skip   = params.frame_skip,
        detect_merge_window = params.merge_window,
        detect_workers      = params.workers,
        detect_clip_start   = start,
        detect_clip_duration= dur,
        detect_confirmed    = 0 if reset_confirm else 1,
    )
    if reset_confirm:
        tab._detect_confirmed = False
        if hasattr(tab, "_detect_confirm_status"):
            tab._detect_confirm_status.config(
                text="⚠  Settings changed — re-run preview to confirm", fg=WARNING)
    tab._log("Detection settings saved ✓", "success")


def _detect_reset_defaults(tab: "PipelineTab"):
    import config as _cfg
    if hasattr(tab, "_det_mode_var"):
        tab._det_mode_var.set(_cfg.DETECT_MODE)
    if hasattr(tab, "_det_priority_var"):
        tab._det_priority_var.set(_cfg.DETECT_PRIORITY)
    defaults = {
        "detect_silence_db":   str(_cfg.DETECT_SILENCE_DB),
        "detect_min_silence":  str(_cfg.DETECT_MIN_SILENCE),
        "detect_threshold":    str(_cfg.DETECT_THRESHOLD),
        "detect_min_scene":    str(_cfg.DETECT_MIN_SCENE),
        "detect_frame_skip":   str(_cfg.DETECT_FRAME_SKIP),
        "detect_merge_window": str(_cfg.DETECT_MERGE_WINDOW),
        "detect_workers":      str(_cfg.DETECT_WORKERS),
    }
    if hasattr(tab, "_det_vars"):
        for key, var in tab._det_vars.items():
            if key in defaults:
                var.set(defaults[key])
    tab._log("Detection settings reset to config defaults ✓", "info")


def _detect_extract_clip(tab: "PipelineTab"):
    if not tab._episode_id or not tab._episode:
        tab._log("Load an episode first", "warning"); return
    ep = tab.db.get_episode(tab._episode_id)
    if not ep:
        return
    source = ep.get("source_path", "")
    if not source or not Path(source).exists():
        tab._log("Source video not found — check episode source path", "error")
        return
    try:
        start = tab._detect_clip_start_var.get().strip() or "00:00:00"
        dur   = int(tab._detect_clip_dur_var.get())
    except Exception:
        start, dur = "00:00:00", 120
    tab.db.update_episode(tab._episode_id,
                          detect_clip_start=start, detect_clip_duration=dur)
    clip_path = str(Path(ep["output_folder"]) / "detect_clip.mp4")
    if hasattr(tab, "_detect_clip_status"):
        tab._detect_clip_status.config(text="Extracting clip …", fg=WARNING)

    def _bg():
        from video_engine import VideoEngine
        engine = VideoEngine(tab.db, ep["output_folder"], on_log=tab._log)
        ok = engine.extract_clip(source, clip_path, start, dur, on_log=tab._log)
        tab._detect_clip_path = clip_path if ok else ""
        tab.after(0, lambda: tab._detect_clip_status.config(
            text=f"✓  Clip ready: {Path(clip_path).name}  ({dur}s from {start})" if ok
                 else "✗  Clip extraction failed — check logs",
            fg=SUCCESS if ok else ERROR))

    threading.Thread(target=_bg, daemon=True, name="detect-clip").start()


def _detect_open_tuner(tab: "PipelineTab"):
    import subprocess, sys
    clip = getattr(tab, "_detect_clip_path", "")
    if not clip or not Path(clip).exists():
        tab._log("Extract a clip first (Step 2)", "warning"); return
    script = Path(__file__).resolve().parent.parent.parent / "visualize_params.py"
    if not script.exists():
        tab._log("visualize_params.py not found in scripts folder", "error")
        return
    ep      = tab.db.get_episode(tab._episode_id)
    out_dir = str(Path(ep["output_folder"]) / "tuner") if ep else "/tmp"
    tab._log("Launching Parameter Tuner … (opens in browser)", "accent")
    params  = _detect_get_params(tab)

    def _bg():
        result = subprocess.run(
            [sys.executable, str(script), clip,
             "--output",       out_dir,
             "--silence-db",   str(params.silence_db),
             "--min-silence",  str(params.min_silence_sec),
             "--threshold",    str(params.visual_threshold),
             "--min-scene",    str(params.min_scene_sec),
             "--merge-window", str(params.merge_window),
             "--frame-skip",   str(params.frame_skip)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            html_files = list(Path(out_dir).glob("*.html"))
            if html_files:
                import webbrowser
                webbrowser.open(html_files[-1].as_uri())
                tab.after(0, lambda: tab._log(
                    "Parameter Tuner opened in browser ✓", "success"))
        else:
            err = result.stderr[-300:] if result.stderr else "unknown error"
            tab.after(0, lambda m=err: tab._log(f"Tuner failed: {m}", "error"))

    threading.Thread(target=_bg, daemon=True, name="detect-tuner").start()


def _detect_run_preview(tab: "PipelineTab"):
    clip = getattr(tab, "_detect_clip_path", "")
    if not clip or not Path(clip).exists():
        tab._log("Extract a clip first (Step 2)", "warning"); return
    params = _detect_get_params(tab)
    if hasattr(tab, "_detect_preview_status"):
        tab._detect_preview_status.config(
            text="Running detection on clip …", fg=WARNING)
    if hasattr(tab, "_detect_preview_lbl"):
        tab._detect_preview_lbl.config(text="")
    _detect_save_settings(tab, reset_confirm=True)

    def _bg():
        from video_engine import VideoEngine
        ep     = tab.db.get_episode(tab._episode_id)
        engine = VideoEngine(tab.db, ep["output_folder"], on_log=tab._log)
        panels = engine.detect_on_clip(
            source_path = ep.get("source_path", ""),
            clip_path   = clip,
            params      = params,
            on_log      = tab._log,
        )
        tab.after(0, lambda: _detect_show_preview(tab, panels))

    threading.Thread(target=_bg, daemon=True, name="detect-preview").start()


def _detect_show_preview(tab: "PipelineTab", panels: list):
    if not hasattr(tab, "_detect_tree"):
        return
    tv = tab._detect_tree
    tv.delete(*tv.get_children())
    for p in panels:
        s   = p.get("start_time_sec", 0)
        e   = p.get("end_time_sec",   0)
        dur = p.get("duration_sec",   round(e - s, 2))
        txt = (p.get("transcript_text") or "")[:80]
        tv.insert("", "end", values=(
            p.get("panel_index", 0) + 1,
            f"{s:.2f}s", f"{e:.2f}s", f"{dur:.2f}s", txt,
        ))
    n   = len(panels)
    avg = sum(p.get("duration_sec", 0) for p in panels) / n if n else 0
    if hasattr(tab, "_detect_preview_status"):
        tab._detect_preview_status.config(
            text=f"Preview: {n} panel(s) detected  ·  avg {avg:.1f}s per panel",
            fg=SUCCESS if n > 0 else ERROR)
    if hasattr(tab, "_detect_preview_lbl"):
        tab._detect_preview_lbl.config(
            text="↑ Preview results (clip only).  If satisfied, confirm below ↓",
            fg=INFO)
    if hasattr(tab, "_detect_confirm_status") and n > 0:
        tab._detect_confirm_status.config(
            text="⚠  Preview done — click confirm when ready", fg=WARNING)


def _detect_confirm_and_run(tab: "PipelineTab"):
    if not tab._episode_id:
        tab._log("No episode loaded", "warning"); return

    _detect_save_settings(tab, reset_confirm=False)
    tab._detect_confirmed = True

    from pipeline_logic import reset_episode_from_stage
    reset_episode_from_stage(tab.db, tab._episode_id, "detect")

    tab._refresh_all_statuses()

    if hasattr(tab, "_detect_confirm_status"):
        tab._detect_confirm_status.config(
            text="✓  Settings confirmed — running full detect …", fg=SUCCESS)
    tab._log(
        "Settings confirmed ✓ — all downstream stages reset to pending. "
        "Starting full detection …",
        "accent",
    )
    tab._run_single("detect")