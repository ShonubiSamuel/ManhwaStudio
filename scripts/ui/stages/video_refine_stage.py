"""
ui/stages/video_refine_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
VIDEO REFINE stage — Transcribes audio and uses AI to refine the text.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from typing import TYPE_CHECKING

import config
from pipeline_logic import reset_episode_from_stage, invalidate_panel_downstream

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, INFO,
    BTN_BG, _F, FS, FL, SEL_BG
)
from ui.widgets import _btn, _div


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Video REFINE stage. Two-tab layout: RAW TRANSCRIPT and REFINE."""
    tab._stage_top_bar(parent, key)
    
    nb = ttk.Notebook(parent)
    nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    raw_frame    = tk.Frame(nb, bg=BG)
    refine_frame = tk.Frame(nb, bg=BG)
    nb.add(raw_frame,    text="  📋  RAW TRANSCRIPT  ")
    nb.add(refine_frame, text="  ✨  REFINE  ")

    _build_refine_raw_tab(raw_frame, tab, key)
    _build_refine_edit_tab(refine_frame, tab)


def load(tab: "PipelineTab"):
    """Populate both treeviews in the REFINE stage when it is opened."""
    panels = sorted(tab.db.list_panels(tab._episode_id),
                    key=lambda p: p["panel_index"])

    # Reload tone prompt in case it was saved elsewhere
    if hasattr(tab, "_refine_tone_text"):
        tone = (tab._episode or {}).get("tone_prompt", "")
        tab._refine_tone_text.delete("1.0", "end")
        if tone:
            tab._refine_tone_text.insert("end", tone)

    # Raw transcript treeview
    if hasattr(tab, "_raw_transcript_tree"):
        tab._raw_transcript_tree.delete(
            *tab._raw_transcript_tree.get_children())
        for p in panels:
            txt = (p.get("transcript_text") or "").strip()
            tab._raw_transcript_tree.insert(
                "", "end", iid=str(p["id"]),
                values=(p["panel_index"], txt or "— empty —"))

    # Refined text treeview
    _load_refined_tree(tab, panels)


def runner(tab: "PipelineTab") -> bool:
    """Whisper transcription, then optional AI refinement."""
    # ── Step 1: Whisper transcription ─────────────────────────────────────────
    from video_engine import VideoEngine, DetectionParams
    ep     = tab.db.get_episode(tab._episode_id)
    engine = VideoEngine(tab.db, ep["output_folder"], on_log=tab._log)
    tab._active_engine = engine
    ok = engine.extract_transcript(
        tab._episode_id,
        params      = DetectionParams(),
        on_progress = tab._on_progress,
    )
    if not ok:
        return False

    # ── Step 2: AI refinement (optional — requires API key + tone) ────────────
    api_key = tab.db.get_setting("nvidia_api_key", "")
    tone    = ep.get("tone_prompt", "").strip()
    if api_key and tone:
        provider        = tab.db.get_setting("ai_provider_refine", "nvidia")
        lm_studio_url   = tab.db.get_setting("lm_studio_url", "http://localhost:1234/v1")
        lm_studio_model = tab.db.get_setting("lm_studio_model", "")
        context_length  = int(tab.db.get_setting("lm_studio_context_length", "32768"))
        if provider == "lm_studio":
            batch_size     = int(tab.db.get_setting("lm_studio_batch_size", "6"))
            max_concurrent = int(tab.db.get_setting("lm_studio_max_concurrent", "4"))
        else:
            batch_size     = int(tab.db.get_setting("nvidia_batch_size", "30"))
            max_concurrent = int(tab.db.get_setting("nvidia_max_concurrent", "6"))

        prov_label = "LM Studio" if provider == "lm_studio" else "NVIDIA NIM"
        tab._log(
            f"AI Refine — {prov_label}  "
            f"(batch={batch_size}, parallel ×{max_concurrent}) …",
            "accent",
        )
        try:
            from ai_engine import refine_transcript
            panels = sorted(tab.db.list_panels(tab._episode_id),
                            key=lambda p: p["panel_index"])
            texts  = [(p.get("transcript_text") or "").strip()
                      for p in panels]
            results = refine_transcript(
                panel_texts     = texts,
                tone_prompt     = tone,
                api_key         = api_key,
                batch_size      = batch_size,
                on_log          = tab._log,
                on_progress     = tab._on_progress,
                provider        = provider,
                lm_studio_url   = lm_studio_url,
                lm_studio_model = lm_studio_model,
                max_concurrent  = max_concurrent,
                context_length  = context_length,
            )
            saved = 0
            for i, panel in enumerate(panels):
                if i < len(results) and results[i]:
                    tab.db.update_panel(panel["id"],
                                         narration_text=results[i])
                    saved += 1
            tab._log(
                f"AI refinement complete — {saved}/{len(panels)} panels ✓",
                "success",
            )
        except Exception as exc:
            tab._log(f"AI refinement skipped: {exc}", "warning")
    else:
        if not api_key:
            tab._log(
                "⚠  No NVIDIA API key set — AI narration will NOT be generated. "
                "Add your key under Settings, then Re-run this stage.",
                "warning",
            )
        elif not tone:
            tab._log(
                "⚠  No narration tone set — AI narration will NOT be generated. "
                "Set a tone above (or in Settings), then Re-run this stage.",
                "warning",
            )

    # ── Report narration coverage ─────────────────────────────────────────────
    # The stage can legitimately finish with empty narration (no API key/tone,
    # or AI refinement failed).  Surface that explicitly instead of silently
    # reporting "done" over empty panels — and record WHY so the Logs archive
    # captures it (tab._last_error is written to the stage's log row).
    panels   = tab.db.list_panels(tab._episode_id)
    total    = len(panels)
    narrated = sum(1 for p in panels if (p.get("narration_text") or "").strip())
    if total and narrated == 0:
        if not api_key:
            reason = "no NVIDIA API key set"
        elif not tone:
            reason = "no narration tone set"
        else:
            reason = "AI refinement produced no text"
        msg = (
            f"Refine finished but narration is empty for all {total} panel(s) "
            f"— {reason}. Set a tone (and API key) under Settings, then Re-run."
        )
        tab._log(msg, "warning")
        tab._last_error = msg
    elif total:
        tab._log(f"Refine complete — {narrated}/{total} panel(s) have narration ✓", "success")

    return True


# ══════════════════════════════════════════════════════════════════════════════
# UI BUILDERS & HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _build_refine_raw_tab(parent: tk.Frame, tab: "PipelineTab", key: str):
    """Read-only view of detected transcript_text + transcription controls."""
    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=12, pady=8)

    tk.Label(inner,
        text="Raw transcript from DETECT stage (read-only).  "
             "This is preserved and never overwritten.",
        font=FS, bg=BG, fg=TEXT_DIM, anchor="w", wraplength=560,
    ).pack(fill="x", pady=(0, 6))

    btn_row = tk.Frame(inner, bg=BG)
    btn_row.pack(fill="x", pady=(0, 6))
    _btn(btn_row, "▶  RUN TRANSCRIPTION",
         lambda: tab._run_single(key),
         bg=ACCENT2, fg="#fff", pady=4, padx=10).pack(side="left", padx=(0, 8))
    _btn(btn_row, "📤  EXPORT TRANSCRIPT JSON",
         lambda: _export_transcript_json(tab),
         bg=PANEL2, pady=4, padx=10).pack(side="left")

    tree_f = tk.Frame(inner, bg=PANEL2,
                      highlightbackground=BORDER, highlightthickness=1)
    tree_f.pack(fill="both", expand=True)

    style = ttk.Style()
    style.configure("RT.Treeview",
                    background=PANEL2, foreground=TEXT,
                    fieldbackground=PANEL2, rowheight=22, font=FS)
    style.configure("RT.Treeview.Heading",
                    background=PANEL, foreground=ACCENT, font=FL, relief="flat")
    style.map("RT.Treeview",
              background=[("selected", SEL_BG)],
              foreground=[("selected", TEXT)])

    tab._raw_transcript_tree = ttk.Treeview(
        tree_f, columns=("#", "transcript"), show="headings",
        style="RT.Treeview", selectmode="browse")
    sb = ttk.Scrollbar(tree_f, orient="vertical",
                       command=tab._raw_transcript_tree.yview)
    tab._raw_transcript_tree.configure(yscrollcommand=sb.set)
    tab._raw_transcript_tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    tab._raw_transcript_tree.heading("#",          text="#")
    tab._raw_transcript_tree.heading("transcript", text="RAW TRANSCRIPT")
    tab._raw_transcript_tree.column("#",          width=50,  anchor="center", stretch=False)
    tab._raw_transcript_tree.column("transcript", width=540, anchor="w")


def _build_refine_edit_tab(parent: tk.Frame, tab: "PipelineTab"):
    """Tone setting + AI refine + import JSON + editable refined text."""
    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=12, pady=8)

    tk.Label(inner, text="TONE / STYLE PROMPT",
             font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4))
    tk.Label(inner,
        text="Describe how you want the narration to sound.  "
             "Applied by the AI when refining the transcript.",
        font=FS, bg=BG, fg=TEXT_DIM, anchor="w", wraplength=560,
    ).pack(fill="x", pady=(0, 4))

    tab._refine_tone_text = scrolledtext.ScrolledText(
        inner, font=FS, bg=PANEL2, fg=TEXT,
        insertbackground=ACCENT, relief="flat",
        padx=8, pady=6, wrap="word", height=3,
    )
    tab._refine_tone_text.pack(fill="x", pady=(0, 4))
    tone = (tab._episode or {}).get("tone_prompt", "")
    if tone:
        tab._refine_tone_text.insert("end", tone)

    _btn(inner, "SAVE TONE",
         lambda: _save_refine_tone(tab), bg=PANEL2, pady=3, padx=8
         ).pack(anchor="w", pady=(0, 8))

    _div(inner)

    tk.Label(inner, text="AI PROVIDER",
             font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 6))

    saved_refine_provider = tab.db.get_setting("ai_provider_refine", "nvidia")
    tab._refine_provider_var = tk.StringVar(value=saved_refine_provider)

    prov_row = tk.Frame(inner, bg=BG)
    prov_row.pack(fill="x", pady=(0, 6))

    def _set_refine_provider(p):
        tab._refine_provider_var.set(p)
        tab.db.set_setting("ai_provider_refine", p)
        if p == "lm_studio":
            lms_refine_frame.pack(fill="x", pady=(0, 8))
        else:
            lms_refine_frame.pack_forget()

    tk.Radiobutton(
        prov_row, text="NVIDIA NIM  (cloud)",
        variable=tab._refine_provider_var, value="nvidia",
        command=lambda: _set_refine_provider("nvidia"),
        font=FS, bg=BG, fg=TEXT,
        activebackground=BG, activeforeground=ACCENT,
        selectcolor=BTN_BG, highlightthickness=0, cursor="hand2",
    ).pack(side="left", padx=(0, 16))
    tk.Radiobutton(
        prov_row, text="LM Studio  (local)",
        variable=tab._refine_provider_var, value="lm_studio",
        command=lambda: _set_refine_provider("lm_studio"),
        font=FS, bg=BG, fg=TEXT,
        activebackground=BG, activeforeground=ACCENT,
        selectcolor=BTN_BG, highlightthickness=0, cursor="hand2",
    ).pack(side="left")

    lms_refine_frame = tk.Frame(inner, bg=PANEL2,
                                highlightbackground=BORDER,
                                highlightthickness=1)
    tk.Label(lms_refine_frame,
             text="  ⓘ  Server URL and model name are configured in\n"
                  "       Settings  →  LM STUDIO",
             font=FS, bg=PANEL2, fg=INFO, justify="left",
             ).pack(anchor="w", padx=12, pady=8)

    if saved_refine_provider == "lm_studio":
        lms_refine_frame.pack(fill="x", pady=(0, 8))

    _div(inner)

    tk.Label(inner, text="ACTIONS",
             font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 6))

    act_row = tk.Frame(inner, bg=BG)
    act_row.pack(fill="x", pady=(0, 4))
    _btn(act_row, "▶  AI REFINE",
         lambda: _run_ai_refine(tab, retry_only=True),
         bg=ACCENT, fg="#000",
         pady=4, padx=10).pack(side="left", padx=(0, 8))
    _btn(act_row, "↺  RE-RUN ALL",
         lambda: _run_ai_refine(tab, retry_only=False),
         bg=ACCENT2, fg="#fff",
         pady=4, padx=10).pack(side="left", padx=(0, 8))
    _btn(act_row, "📥  IMPORT JSON",
         lambda: _import_refined_json(tab), bg=PANEL2,
         pady=4, padx=10).pack(side="left", padx=(0, 8))
    _btn(act_row, "💾  SAVE MANUAL EDITS",
         lambda: _save_refined_edits(tab), bg=PANEL2,
         pady=4, padx=10).pack(side="left")

    tab._refine_prog_var = tk.IntVar(value=0)
    tab._refine_prog_bar = ttk.Progressbar(
        inner, variable=tab._refine_prog_var, maximum=100,
        mode="determinate")
    tab._refine_prog_bar.pack(fill="x", pady=(4, 0))
    tab._refine_prog_bar.pack_forget()

    tab._refine_status_lbl = tk.Label(inner, text="",
        font=FS, bg=BG, fg=TEXT_DIM)
    tab._refine_status_lbl.pack(anchor="w", pady=(2, 6))

    _div(inner)

    tk.Label(inner, text="REFINED TEXT  (double-click a row to edit)",
             font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4))

    paned = ttk.PanedWindow(inner, orient="vertical")
    paned.pack(fill="both", expand=True)

    tree_f = tk.Frame(paned, bg=PANEL2,
                      highlightbackground=BORDER, highlightthickness=1)
    paned.add(tree_f, weight=1)

    style = ttk.Style()
    style.configure("RF.Treeview",
                    background=PANEL2, foreground=TEXT,
                    fieldbackground=PANEL2, rowheight=22, font=FS)
    style.configure("RF.Treeview.Heading",
                    background=PANEL, foreground=ACCENT, font=FL, relief="flat")
    style.map("RF.Treeview",
              background=[("selected", SEL_BG)],
              foreground=[("selected", TEXT)])

    tab._refined_tree = ttk.Treeview(
        tree_f, columns=("#", "refined"), show="headings",
        style="RF.Treeview", selectmode="browse")
    sb2 = ttk.Scrollbar(tree_f, orient="vertical",
                        command=tab._refined_tree.yview)
    tab._refined_tree.configure(yscrollcommand=sb2.set)
    tab._refined_tree.pack(side="left", fill="both", expand=True)
    sb2.pack(side="right", fill="y")

    tab._refined_tree.heading("#",       text="#")
    tab._refined_tree.heading("refined", text="REFINED TEXT")
    tab._refined_tree.column("#",       width=50,  anchor="center", stretch=False)
    tab._refined_tree.column("refined", width=540, anchor="w")
    tab._refined_tree.bind("<Double-1>", lambda e: _refined_tree_edit(tab, e))
    tab._refined_tree.tag_configure("filled", foreground=SUCCESS)
    tab._refined_tree.tag_configure("empty",  foreground=ERROR)

    edit_f = tk.Frame(paned, bg=BG)
    paned.add(edit_f, weight=0)

    tk.Label(edit_f, text="Edit:", font=FS, bg=BG, fg=TEXT_DIM
             ).pack(anchor="w", padx=8, pady=(8, 2))
    tab._refined_edit_box = scrolledtext.ScrolledText(
        edit_f, font=FS, bg=PANEL2, fg=TEXT,
        insertbackground=ACCENT, relief="flat",
        padx=8, pady=6, wrap="word", height=4,
    )
    tab._refined_edit_box.pack(fill="both", expand=True, padx=8, pady=(2, 4))

    btn_row = tk.Frame(edit_f, bg=BG)
    btn_row.pack(fill="x", padx=8, pady=(0, 8))
    _btn(btn_row, "APPLY TO PANEL",
         lambda: _refined_tree_apply(tab), bg=PANEL2, pady=3, padx=8
         ).pack(side="left")


def _load_refined_tree(tab: "PipelineTab", panels=None):
    if not hasattr(tab, "_refined_tree"):
        return
    if panels is None:
        panels = sorted(tab.db.list_panels(tab._episode_id),
                        key=lambda p: p["panel_index"])
    tab._refined_tree.delete(*tab._refined_tree.get_children())
    for p in panels:
        txt = (p.get("narration_text") or "").strip()
        tag = "filled" if txt else "empty"
        tab._refined_tree.insert(
            "", "end", iid=str(p["id"]),
            values=(p["panel_index"], txt or "— not refined yet —"),
            tags=(tag,))


def _save_refine_tone(tab: "PipelineTab"):
    if not hasattr(tab, "_refine_tone_text"):
        return
    tone = tab._refine_tone_text.get("1.0", "end").strip()
    if tab._episode_id:
        tab.db.update_episode(tab._episode_id, tone_prompt=tone)
        if tab._episode:
            tab._episode["tone_prompt"] = tone
        tab._log("Tone prompt saved ✓", "success")


def _export_transcript_json(tab: "PipelineTab"):
    if not tab._episode_id:
        return
    panels = sorted(tab.db.list_panels(tab._episode_id),
                    key=lambda p: p["panel_index"])
    data = [
        {"panel": p["panel_index"],
         "transcript": (p.get("transcript_text") or "").strip()}
        for p in panels
    ]
    path = filedialog.asksaveasfilename(
        title        = "Export Transcript JSON",
        defaultextension = ".json",
        filetypes    = [("JSON files", "*.json"), ("All files", "*")],
        initialfile  = f"transcript_{tab._episode_id}.json",
        parent       = tab,
    )
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        tab._log(f"Transcript exported → {Path(path).name} ✓", "success")
    except Exception as exc:
        tab._log(f"Export failed: {exc}", "error")


def _import_refined_json(tab: "PipelineTab"):
    if not tab._episode_id:
        return
    path = filedialog.askopenfilename(
        title     = "Import Refined JSON",
        filetypes = [("JSON files", "*.json"), ("All files", "*")],
        parent    = tab,
    )
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        tab._log(f"Could not read file: {exc}", "error")
        return

    panels = sorted(tab.db.list_panels(tab._episode_id),
                    key=lambda p: p["panel_index"])
    saved = 0

    if isinstance(data, list) and data and isinstance(data[0], str):
        for i, panel in enumerate(panels):
            if i < len(data) and str(data[i]).strip():
                tab.db.update_panel(panel["id"], narration_text=str(data[i]).strip())
                saved += 1
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        idx_map = {p["panel_index"]: p for p in panels}
        for item in data:
            idx = item.get("panel", item.get("index", item.get("panel_index")))
            txt = item.get("refined", item.get("narration", item.get("text", "")))
            if idx is not None and txt and int(idx) in idx_map:
                tab.db.update_panel(idx_map[int(idx)]["id"], narration_text=str(txt).strip())
                saved += 1
    else:
        tab._log("Unsupported JSON format — expected array of strings or objects", "error")
        return

    tab._log(f"Imported {saved} refined panel(s) ✓", "success")
    _load_refined_tree(tab)


def _run_ai_refine(tab: "PipelineTab", retry_only: bool = True):
    if not tab._episode_id:
        return
    if tab._active_thread and tab._active_thread.is_alive():
        tab._log("A stage is already running — wait or press Stop", "warning")
        return

    tone = ""
    if hasattr(tab, "_refine_tone_text"):
        tone = tab._refine_tone_text.get("1.0", "end").strip()
    if not tone:
        tone = (tab._episode or {}).get("tone_prompt", "")
    if not tone:
        tab._log("No tone prompt set — add one above before refining", "warning")
        return

    all_panels = sorted(tab.db.list_panels(tab._episode_id), key=lambda p: p["panel_index"])
    if not any((p.get("transcript_text") or "").strip() for p in all_panels):
        tab._log("No transcript text found — run DETECT first", "error")
        return

    if retry_only:
        panels_to_run = [p for p in all_panels if not (p.get("narration_text") or "").strip()]
        if not panels_to_run:
            tab._log(
                "All panels already refined — use ↺ RE-RUN ALL to rewrite everything.",
                "info",
            )
            return
    else:
        for p in all_panels:
            tab.db.update_panel(p["id"], narration_text="")
        panels_to_run = list(all_panels)
        tab.after(0, lambda: tab._cascade_wipe_downstream())

    texts = [(p.get("transcript_text") or "").strip() for p in panels_to_run]
    provider        = getattr(tab, "_refine_provider_var", tk.StringVar(value="nvidia")).get()
    lm_studio_url   = tab.db.get_setting("lm_studio_url", "http://localhost:1234/v1")
    lm_studio_model = tab.db.get_setting("lm_studio_model", "")
    context_length  = int(tab.db.get_setting("lm_studio_context_length", "32768"))
    
    if provider == "lm_studio":
        batch_size     = int(tab.db.get_setting("lm_studio_batch_size", "6"))
        max_concurrent = int(tab.db.get_setting("lm_studio_max_concurrent", "4"))
    else:
        batch_size     = int(tab.db.get_setting("nvidia_batch_size", "30"))
        max_concurrent = int(tab.db.get_setting("nvidia_max_concurrent", "6"))
    provider_label  = "LM Studio" if provider == "lm_studio" else "NVIDIA NIM"

    api_key = tab.db.get_setting("nvidia_api_key", "")
    if provider == "nvidia" and not api_key:
        tab._log("NVIDIA API key not set — add it in Settings", "error")
        return
    if provider == "lm_studio" and not lm_studio_model.strip():
        tab._log("LM Studio model name not set — configure it in Settings", "error")
        return

    tab._set_ui_running(True)
    if hasattr(tab, "_refine_prog_bar"):
        tab._refine_prog_var.set(0)
        tab._refine_prog_bar.pack(fill="x", pady=(4, 0))
    if hasattr(tab, "_refine_status_lbl"):
        tab._refine_status_lbl.config(text=f"Connecting to {provider_label} …", fg=TEXT_DIM)

    mode_label = "RETRY FAILED" if retry_only else "RE-RUN ALL"
    tab._log(f"AI Refine [{mode_label}] via {provider_label} …", "accent")

    if hasattr(tab, "_refined_tree"):
        if retry_only:
            for p in panels_to_run:
                try: tab._refined_tree.item(str(p["id"]), values=(p["panel_index"] + 1, "⏳  waiting …"), tags=("empty",))
                except Exception: pass
        else:
            tab._refined_tree.delete(*tab._refined_tree.get_children())
            for p in all_panels:
                tab._refined_tree.insert("", "end", iid=str(p["id"]), values=(p["panel_index"] + 1, "⏳  waiting …"), tags=("empty",))

    def _on_progress(done: int, total: int):
        pct = round(done / total * 100) if total else 0
        tab.after(0, lambda: (
            hasattr(tab, "_refine_prog_var") and tab._refine_prog_var.set(pct),
            hasattr(tab, "_refine_status_lbl") and tab._refine_status_lbl.config(text=f"Refining \u2026 {done}/{total} panels", fg=TEXT_DIM),
        ))

    _saved_count = [0]

    def _batch_done(batch_idx: int, start: int, end: int, batch_result: dict):
        n_saved = 0
        for rel_idx, text in batch_result.items():
            abs_idx = start + rel_idx
            if abs_idx < len(panels_to_run) and text:
                tab.db.update_panel(panels_to_run[abs_idx]["id"], narration_text=text)
                n_saved += 1
        _saved_count[0] += n_saved
        _start, _result = start, dict(batch_result)

        def _update_rows():
            if not hasattr(tab, "_refined_tree"): return
            for rel_idx, text in _result.items():
                abs_idx = _start + rel_idx
                if abs_idx < len(panels_to_run):
                    p = panels_to_run[abs_idx]
                    try: tab._refined_tree.item(str(p["id"]), values=(p["panel_index"] + 1, text or ""), tags=("filled" if text else "empty",))
                    except Exception: pass
            if hasattr(tab, "_refine_status_lbl"):
                tab._refine_status_lbl.config(text=f"{_saved_count[0]}/{len(panels_to_run)} panels refined", fg=TEXT_DIM)

            # Keep the translate stage English column in sync with refine in
            # real-time.  _patch_translate_rows() updates only the affected
            # rows (no full tree reload), so this is the same zero-overhead
            # cell-level update that translate already uses for every other
            # language via its own _on_batch_done callback.
            if hasattr(tab, "_translate_tree"):
                try:
                    from ui.stages.translate_stage import _patch_translate_rows
                    panels_batch: list = []
                    texts_batch:  list = []
                    for _ri in sorted(_result.keys()):
                        _ai = _start + _ri
                        if _ai < len(panels_to_run):
                            panels_batch.append(panels_to_run[_ai])
                            texts_batch.append(_result[_ri])
                    _patch_translate_rows(tab, "en", panels_batch, texts_batch)
                except Exception:
                    pass  # translate stage not built yet — safe no-op
        tab.after(0, _update_rows)

    def _bg():
        if provider == "lm_studio":
            try:
                from ai_engine import load_lmstudio_model
                tab.after(0, lambda: tab._log(f"Loading '{lm_studio_model}' into LM Studio …", "info"))
                if not load_lmstudio_model(lm_studio_url, lm_studio_model, context_length):
                    tab.after(0, lambda: (tab._log("LM Studio load failed", "error"), tab._set_ui_running(False)))
                    return
            except Exception as _exc:
                tab.after(0, lambda m=str(_exc): (tab._log(f"LM Studio load error: {m}", "error"), tab._set_ui_running(False)))
                return

        try:
            from ai_engine import refine_transcript
            refine_transcript(
                panel_texts     = texts, tone_prompt     = tone, api_key         = api_key,
                batch_size      = batch_size, on_log     = tab._log, on_progress = _on_progress,
                on_batch_done   = _batch_done, provider  = provider, lm_studio_url = lm_studio_url,
                lm_studio_model = lm_studio_model, max_concurrent= max_concurrent, context_length= context_length,
            )
            def _done():
                saved = _saved_count[0]
                if hasattr(tab, "_refine_prog_var"): tab._refine_prog_var.set(100)
                if hasattr(tab, "_refine_prog_bar"): tab._refine_prog_bar.pack_forget()
                _load_refined_tree(tab)
                reset_episode_from_stage(tab.db, tab._episode_id, "translate")
                tab._refresh_all_statuses()
                # Reload translate so it immediately shows the new refined text
                # rather than staying on whatever state the cascade left it in.
                tab._reload_stages("translate")
                tab._set_ui_running(False)
                tab._log(f"AI Refine complete ✓ — {saved}/{len(panels_to_run)} panels. TRANSLATE · DUBBING · SYNC reset to pending.", "success")
            tab.after(0, _done)
        except Exception as exc:
            tab.after(0, lambda m=str(exc): (tab._log(f"AI Refine failed: {m}", "error"), tab._set_ui_running(False)))
        finally:
            if provider == "lm_studio":
                try:
                    from ai_engine import unload_lmstudio_model
                    unload_lmstudio_model(lm_studio_model, lm_studio_url)
                except Exception: pass

    tab._active_thread = threading.Thread(target=_bg, daemon=True, name="ai-refine")
    tab._active_thread.start()


def _save_refined_edits(tab: "PipelineTab"):
    if not hasattr(tab, "_refined_tree"): return
    all_langs  = [lc for lc in config.SUPPORTED_LANGUAGES if lc != "en"]
    n_updated, n_cleared, n_batches = 0, 0, 0

    for iid in tab._refined_tree.get_children():
        vals    = tab._refined_tree.item(iid, "values")
        new_txt = (vals[1] if len(vals) > 1 else "")
        if new_txt in ("— not refined yet —",): new_txt = ""

        panel_id = int(iid)
        panel    = tab.db.get_panel(panel_id)
        if not panel: continue
        old_txt = (panel.get("narration_text") or "").strip()
        if new_txt.strip() == old_txt: continue

        tab.db.update_panel(panel_id, narration_text=new_txt)
        n_updated += 1
        summary    = invalidate_panel_downstream(tab.db, panel_id, all_langs)
        n_cleared += summary["translations_cleared"]
        n_batches += _invalidate_dubbing_batches_for_panel(tab, panel)

    if n_updated:
        reset_episode_from_stage(tab.db, tab._episode_id, "translate")
        tab._refresh_all_statuses()
        tab._reload_stages("translate")
        tab._log(f"Saved {n_updated} panel(s) ✓  | {n_cleared} translations cleared | {n_batches} batches reset", "accent")
        tab._log("  → Stages TRANSLATE, DUBBING, SYNC reset — Run All will re-process", "warning")
    else:
        tab._log("No changes detected — nothing to save", "info")
    _load_refined_tree(tab)


def _refined_tree_edit(tab: "PipelineTab", _event):
    if not hasattr(tab, "_refined_tree"): return
    sel = tab._refined_tree.selection()
    if not sel: return
    panel = tab.db.get_panel(int(sel[0]))
    if panel and hasattr(tab, "_refined_edit_box"):
        tab._refined_edit_box.delete("1.0", "end")
        tab._refined_edit_box.insert("end", (panel.get("narration_text") or "").strip())


def _refined_tree_apply(tab: "PipelineTab"):
    if not hasattr(tab, "_refined_tree") or not hasattr(tab, "_refined_edit_box"): return
    sel = tab._refined_tree.selection()
    if not sel: return
    panel_id = int(sel[0])
    panel    = tab.db.get_panel(panel_id)
    if not panel: return

    new_text = tab._refined_edit_box.get("1.0", "end").strip()
    old_text = (panel.get("narration_text") or "").strip()
    if new_text == old_text:
        tab._log("No change detected — nothing to update", "info")
        return

    tab.db.update_panel(panel_id, narration_text=new_text)
    all_langs = [lc for lc in config.SUPPORTED_LANGUAGES if lc != "en"]
    summary   = invalidate_panel_downstream(tab.db, panel_id, all_langs)
    n_batches = _invalidate_dubbing_batches_for_panel(tab, panel)

    reset_episode_from_stage(tab.db, tab._episode_id, "translate")
    tab._refresh_all_statuses()
    tab._reload_stages("translate")
    _load_refined_tree(tab)

    panel_num = panel.get("panel_index", 0) + 1
    tab._log(f"Panel {panel_num} updated ✓ | Translations cleared: {summary['translations_cleared']} | Batches reset: {n_batches}", "accent")
    tab._log("  → Stages TRANSLATE, DUBBING, SYNC reset to pending.", "warning")


def _invalidate_dubbing_batches_for_panel(tab: "PipelineTab", panel: dict) -> int:
    if not tab._episode_id: return 0
    panel_index = panel.get("panel_index")
    if panel_index is None: return 0
    try:
        from dub_engine import DubEngine
        state = DubEngine(tab.db, on_log=tab._log).load_batch_state(tab._episode_id)
    except Exception: return 0

    ep = tab.db.get_episode(tab._episode_id)
    output_folder = (ep or {}).get("output_folder", "")
    n_invalidated = 0

    for lang_code, lang_data in state.items():
        if not isinstance(lang_data, dict): continue
        lang_touched = False
        for batch in lang_data.get("batches", []):
            if panel_index not in batch.get("panels", []): continue
            audio_path = batch.get("audio_path", "")
            if audio_path and Path(audio_path).exists():
                try: Path(audio_path).unlink()
                except Exception: pass
            if output_folder:
                batch_wav = Path(output_folder) / "dub" / lang_code / f"batch_{batch['idx']:04d}.wav"
                if batch_wav.exists():
                    try: batch_wav.unlink()
                    except Exception: pass
            batch["status"] = "pending"
            batch["audio_path"] = ""
            batch["duration"] = 0.0
            n_invalidated += 1
            lang_touched = True
            tab._log(f"  Batch {batch['idx'] + 1} [{lang_code}] deleted — contains edited panel {panel_index + 1}", "warning")
        
        if lang_touched and output_folder:
            continuous = Path(output_folder) / "dub" / lang_code / "_continuous.wav"
            if continuous.exists():
                try: continuous.unlink()
                except Exception: pass

    if output_folder:
        try:
            state_path = Path(output_folder) / "dub" / "batch_state.json"
            from dub_engine import DubEngine
            DubEngine._save_batch_state(state_path, state)
        except Exception as exc: tab._log(f"Could not save batch state: {exc}", "warning")
    return n_invalidated