"""
ui/stages/pdf_narrate_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
PDF NARRATE stage — AI vision narration (PDF pipeline).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, FS, FL, SEL_BG, BTN_BG
)
from ui.widgets import _btn


# ── Stop flag ─────────────────────────────────────────────────────────────────

class _StopFlag:
    """
    Minimal object that satisfies two interfaces simultaneously:

      • tab._active_engine interface — pipeline_tab calls .stop() on whatever
        is assigned to tab._active_engine when the user presses STOP.

      • Callable interface — narrate_with_vision() accepts
        should_stop: Callable[[], bool] and calls should_stop() between
        batches to check for cancellation.

    Assigning an instance to tab._active_engine before launching the
    background thread means the STOP button reaches the running narration
    job, which previously had no cancellation path at all.
    """
    __slots__ = ("_stopped",)

    def __init__(self):
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def __call__(self) -> bool:
        return self._stopped


# ── Stage interface ───────────────────────────────────────────────────────────

def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the NARRATE stage UI inside parent."""
    tab._stage_top_bar(parent, key)

    nb = ttk.Notebook(parent)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    auto_frame   = tk.Frame(nb, bg=BG)
    manual_frame = tk.Frame(nb, bg=BG)
    review_frame = tk.Frame(nb, bg=BG)
    nb.add(auto_frame,   text="  🤖  AUTO (AI)  ")
    nb.add(manual_frame, text="  ✋  MANUAL  ")
    nb.add(review_frame, text="  ✏  REVIEW / EDIT  ")

    _build_narrate_auto(auto_frame, tab)
    _build_narrate_manual(manual_frame, tab)
    _build_narrate_review(review_frame, tab)


def load(tab: "PipelineTab"):
    """Refresh the review tree when the tab is opened."""
    if hasattr(tab, "_narrate_review_reload"):
        tab._narrate_review_reload()


def runner(tab: "PipelineTab") -> bool:
    """Validate narration is present."""
    panels = tab.db.list_panels(tab._episode_id)
    n_done = sum(1 for p in panels if (p.get("narration_text") or "").strip())
    if n_done == 0:
        tab._log("No narration found — run AUTO or paste JSON in MANUAL tab", "warning")
        return False
    tab._log(f"Narration present: {n_done}/{len(panels)} panels ✓", "info")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _build_narrate_auto(parent: tk.Frame, tab: "PipelineTab"):
    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=16, pady=10)

    tk.Label(inner, text="AUTO NARRATION — NVIDIA Llama 3.2 90B Vision", font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4))
    tk.Label(inner, text="Sends the optimised panel images to the vision model in batches.\nEach image gets one narration sentence. Requires a valid NVIDIA API key.", font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560).pack(anchor="w", pady=(0, 10))

    row = tk.Frame(inner, bg=BG); row.pack(fill="x", pady=(0, 4))
    tk.Label(row, text="NVIDIA API key:", font=FS, bg=BG, fg=TEXT_DIM, width=18, anchor="w").pack(side="left")
    tab._narr_key_var = tk.StringVar(value=tab.db.get_setting("nvidia_api_key", ""))
    tk.Entry(row, textvariable=tab._narr_key_var, font=FS, width=36, show="*", bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", highlightthickness=1, highlightcolor=ACCENT, highlightbackground=BORDER).pack(side="left", padx=(0, 6))
    _btn(row, "SAVE", lambda: tab.db.set_setting("nvidia_api_key", tab._narr_key_var.get().strip()), bg=PANEL2, pady=2, padx=6).pack(side="left")

    row2 = tk.Frame(inner, bg=BG); row2.pack(fill="x", pady=(0, 4))
    tk.Label(row2, text="Images per batch:", font=FS, bg=BG, fg=TEXT_DIM, width=18, anchor="w").pack(side="left")
    tab._narr_batch_var = tk.StringVar(value=str(config.NARR_IMAGES_PER_BATCH))
    tk.Entry(row2, textvariable=tab._narr_batch_var, font=FS, width=4, bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", highlightthickness=1, highlightcolor=ACCENT, highlightbackground=BORDER).pack(side="left")
    tk.Label(row2, text="(reduce to 1–2 if hitting token limits)", font=FS, bg=BG, fg=MUTED).pack(side="left", padx=(8, 0))

    tk.Label(inner, text="Narration tone / style:", font=FL, bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(8, 2))
    tab._narr_tone_text = scrolledtext.ScrolledText(inner, font=FS, bg=PANEL2, fg=TEXT, insertbackground=ACCENT, relief="flat", padx=8, pady=8, wrap="word", height=3)
    tab._narr_tone_text.pack(fill="x", pady=(0, 6))
    tone = (tab._episode or {}).get("tone_prompt", "")
    if tone:
        tab._narr_tone_text.insert("end", tone)

    tab._narr_auto_prog_var = tk.IntVar(value=0)
    tab._narr_auto_prog = ttk.Progressbar(inner, variable=tab._narr_auto_prog_var, maximum=100, mode="determinate")
    tab._narr_auto_prog.pack(fill="x", pady=(0, 4))
    tab._narr_auto_lbl = tk.Label(inner, text="", font=FS, bg=BG, fg=TEXT_DIM)
    tab._narr_auto_lbl.pack(anchor="w", pady=(0, 6))

    _btn(inner, "▶  RUN AUTO NARRATION", lambda: _run_auto_narration(tab), bg=ACCENT, fg="#000", pady=6, padx=16).pack(anchor="w")


def _build_narrate_manual(parent: tk.Frame, tab: "PipelineTab"):
    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=16, pady=10)

    tk.Label(inner, text="MANUAL NARRATION — External AI (Claude, GPT-4V, etc.)", font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4))
    instructions = "STEP 1 — Click OPEN OPTIMISED FOLDER to reveal the downscaled images.\n\nSTEP 2 — Upload them to Claude.ai / ChatGPT / any vision model along with your narration prompt.\n\nSTEP 3 — Ask the AI to respond as a JSON array: [\"narration for image 1\", \"narration for image 2\", ...]\n\nSTEP 4 — Paste the response below and click PARSE & SAVE."
    tk.Label(inner, text=instructions, font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560).pack(anchor="w", pady=(0, 10))

    _btn(inner, "OPEN OPTIMISED FOLDER", lambda: _open_narrator_folder(tab), bg=PANEL2, pady=4, padx=8).pack(anchor="w", pady=(0, 10))

    tk.Label(inner, text="Paste AI response JSON below:", font=FL, bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0, 2))
    tab._narrate_text = scrolledtext.ScrolledText(inner, font=FS, bg=PANEL2, fg=TEXT, insertbackground=ACCENT, relief="flat", padx=10, pady=10, wrap="word", height=10)
    tab._narrate_text.pack(fill="both", expand=True, pady=(0, 8))

    _btn(inner, "PARSE & SAVE TO DATABASE", lambda: _parse_narration(tab), bg=ACCENT, fg="#000", pady=5, padx=12).pack(anchor="w")


def _build_narrate_review(parent: tk.Frame, tab: "PipelineTab"):
    top = tk.Frame(parent, bg=BG)
    top.pack(fill="x", padx=12, pady=(8, 4))

    tk.Label(top, text="Edit any narration text directly in the table. Empty panels are highlighted.", font=FS, bg=BG, fg=TEXT_DIM).pack(side="left")
    _btn(top, "RELOAD", lambda: _narrate_review_reload(tab), bg=PANEL2, pady=3, padx=8).pack(side="right", padx=(4, 0))
    _btn(top, "SAVE ALL", lambda: _narrate_review_save(tab), bg=ACCENT, fg="#000", pady=3, padx=8).pack(side="right")

    cols = ("#", "narration")
    style = ttk.Style()
    style.configure("NR.Treeview", background=PANEL2, foreground=TEXT, fieldbackground=PANEL2, rowheight=28, font=FS, borderwidth=0)
    style.configure("NR.Treeview.Heading", background=PANEL, foreground=ACCENT, font=FL, relief="flat")
    style.map("NR.Treeview", background=[("selected", SEL_BG)], foreground=[("selected", TEXT)])

    tree_frame = tk.Frame(parent, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
    tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

    tab._review_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", style="NR.Treeview", selectmode="browse")
    sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tab._review_tree.yview)
    tab._review_tree.configure(yscrollcommand=sb.set)
    tab._review_tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    tab._review_tree.heading("#", text="#")
    tab._review_tree.heading("narration", text="NARRATION TEXT")
    tab._review_tree.column("#", width=50, anchor="center", stretch=False)
    tab._review_tree.column("narration", width=540, anchor="w")
    tab._review_tree.bind("<Double-1>", lambda e: _narrate_review_edit(tab, e))

    edit_area = tk.Frame(parent, bg=BG)
    edit_area.pack(fill="x", padx=8, pady=(0, 4))
    tk.Label(edit_area, text="Edit selected panel:", font=FS, bg=BG, fg=TEXT_DIM).pack(anchor="w")
    tab._review_edit_box = scrolledtext.ScrolledText(edit_area, font=FS, bg=PANEL2, fg=TEXT, insertbackground=ACCENT, relief="flat", padx=8, pady=6, wrap="word", height=3)
    tab._review_edit_box.pack(fill="x", pady=(2, 4))
    _btn(edit_area, "APPLY TO PANEL", lambda: _narrate_review_apply(tab), bg=PANEL2, pady=3, padx=8).pack(anchor="w")

    tab._narrate_review_reload = lambda: _narrate_review_reload(tab)
    tab._narrate_review_reload()


def _open_narrator_folder(tab: "PipelineTab"):
    if not tab._episode: return
    folder = Path(tab._episode["output_folder"]) / "ai_narrator" / "optimized"
    folder.mkdir(parents=True, exist_ok=True)
    import os, sys
    if sys.platform == "darwin": os.system(f"open '{folder}'")
    elif sys.platform == "win32": os.startfile(str(folder))
    else: os.system(f"xdg-open '{folder}'")


def _run_auto_narration(tab: "PipelineTab"):
    if not tab._episode: return
    api_key = tab._narr_key_var.get().strip() or tab.db.get_setting("nvidia_api_key", "")
    if not api_key:
        tab._log("NVIDIA API key required — enter it above", "error")
        return
    opt_dir = Path(tab._episode["output_folder"]) / "ai_narrator" / "optimized"
    if not opt_dir.exists():
        tab._log("Run SLICE first — no optimised images found", "warning")
        return
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = sorted([str(p) for p in opt_dir.iterdir() if p.suffix.lower() in img_exts])
    if not images:
        tab._log("No optimised images found — run SLICE first", "warning")
        return
    try: batch_size = int(tab._narr_batch_var.get())
    except ValueError: batch_size = config.NARR_IMAGES_PER_BATCH
    tone = tab._narr_tone_text.get("1.0", "end").strip() if hasattr(tab, "_narr_tone_text") else ""
    if not tone: tone = (tab._episode or {}).get("tone_prompt", "")

    tab._narr_auto_prog_var.set(0)
    tab._narr_auto_lbl.config(text="Starting…")
    tab._log(f"Auto narration: {len(images)} image(s), batch={batch_size}", "info")

    # Create a stop flag and assign it to tab._active_engine so the STOP
    # button in pipeline_tab can call .stop() to cancel between batches.
    # The same object is callable (returns bool) so it doubles as the
    # should_stop argument passed to narrate_with_vision().
    stop_flag = _StopFlag()
    tab._active_engine = stop_flag

    def _bg():
        try:
            from ai_engine import narrate_with_vision
            results = narrate_with_vision(
                image_paths  = images,
                tone_prompt  = tone,
                api_key      = api_key,
                batch_size   = batch_size,
                on_log       = tab._log,
                on_progress  = lambda c, t: tab.after(0, lambda: (
                    tab._narr_auto_prog_var.set(int(c / t * 100) if t else 0),
                    tab._narr_auto_lbl.config(text=f"{c}/{t} batches done"),
                )),
                should_stop  = stop_flag,
            )
            panels = sorted(tab.db.list_panels(tab._episode_id), key=lambda p: p["panel_index"])
            saved = 0
            for i, panel in enumerate(panels):
                if i < len(results) and results[i].strip():
                    tab.db.update_panel(panel["id"], narration_text=results[i].strip())
                    saved += 1
            tab.db.set_episode_stage(tab._episode_id, "narrate", "done")
            tab.after(0, lambda: (
                tab._narr_auto_lbl.config(text=f"Done — {saved} panel(s) narrated ✓"),
                tab._log(f"Auto narration complete: {saved} panels ✓", "success"),
                tab._refresh_all_statuses(),
                _narrate_review_reload(tab),
            ))
        except Exception as exc:
            tab.after(0, lambda: (
                tab._log(f"Auto narration error: {exc}", "error"),
                tab._narr_auto_lbl.config(text=f"Error: {exc}"),
            ))
        finally:
            # Clear the engine handle so pipeline_tab doesn't hold a stale
            # reference to a finished job.
            tab._active_engine = None

    threading.Thread(target=_bg, daemon=True, name="narr-auto").start()


def _parse_narration(tab: "PipelineTab"):
    if not hasattr(tab, "_narrate_text") or not tab._narrate_text: return
    raw = tab._narrate_text.get("1.0", "end").strip()
    if not raw:
        tab._log("Paste the AI's JSON response first", "warning")
        return
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw.strip())
    try: data = json.loads(raw)
    except json.JSONDecodeError as exc:
        tab._log(f"Invalid JSON: {exc}", "error")
        return
    if isinstance(data, dict): texts = data.get("en") or next(iter(data.values()), [])
    elif isinstance(data, list): texts = data
    else:
        tab._log("Unexpected format — expected JSON array", "error")
        return
    panels = sorted(tab.db.list_panels(tab._episode_id), key=lambda p: p["panel_index"])
    saved = 0
    for i, panel in enumerate(panels):
        if i < len(texts):
            txt = str(texts[i]).strip()
            if txt:
                tab.db.update_panel(panel["id"], narration_text=txt)
                saved += 1
    tab._log(f"Narration saved: {saved} panel(s) ✓", "success")
    tab.db.set_episode_stage(tab._episode_id, "narrate", "done")
    tab._refresh_all_statuses()
    _narrate_review_reload(tab)


def _narrate_review_reload(tab: "PipelineTab"):
    if not hasattr(tab, "_review_tree"): return
    tab._review_tree.delete(*tab._review_tree.get_children())
    panels = sorted(tab.db.list_panels(tab._episode_id), key=lambda p: p["panel_index"])
    for p in panels:
        txt = (p.get("narration_text") or "").strip()
        tag = "empty" if not txt else "ok"
        tab._review_tree.insert("", "end", iid=str(p["id"]), values=(p["panel_index"] + 1, txt or "— empty —"), tags=(tag,))
    tab._review_tree.tag_configure("empty", foreground=ERROR)
    tab._review_tree.tag_configure("ok", foreground=TEXT)


def _narrate_review_edit(tab: "PipelineTab", _event):
    sel = tab._review_tree.selection()
    if not sel or not hasattr(tab, "_review_edit_box"): return
    panel = tab.db.get_panel(int(sel[0]))
    if panel:
        tab._review_edit_box.delete("1.0", "end")
        tab._review_edit_box.insert("end", (panel.get("narration_text") or "").strip())


def _narrate_review_apply(tab: "PipelineTab"):
    sel = tab._review_tree.selection()
    if not sel or not hasattr(tab, "_review_edit_box"): return
    txt = tab._review_edit_box.get("1.0", "end").strip()
    tab.db.update_panel(int(sel[0]), narration_text=txt)
    _narrate_review_reload(tab)
    tab._log("Panel narration updated ✓", "success")


def _narrate_review_save(tab: "PipelineTab"):
    """
    Save all narrations from the review tree to the database.

    Bug fixed (Batch 2): previously, if the user edited a panel's text in
    the edit box but clicked SAVE ALL without first clicking APPLY, their
    edits were silently lost because SAVE ALL only read from the Treeview
    (which still showed the old text).

    Fix: before the bulk save loop, check whether the edit box contains
    unsaved changes for the currently selected row.  If it does, write those
    to the DB first and track which panel was handled so the main loop skips
    it — otherwise the loop would immediately overwrite the just-saved edit
    with the stale tree value.
    """
    if not hasattr(tab, "_review_tree"):
        return

    # ── Flush any unsaved edit-box changes before the bulk save ──────────────
    pending_iid = None
    sel = tab._review_tree.selection()
    if sel and hasattr(tab, "_review_edit_box"):
        pending_text = tab._review_edit_box.get("1.0", "end").strip()
        vals         = tab._review_tree.item(sel[0], "values")
        tree_text    = vals[1] if len(vals) > 1 else ""
        if tree_text == "— empty —":
            tree_text = ""
        if pending_text != tree_text:
            tab.db.update_panel(int(sel[0]), narration_text=pending_text)
            pending_iid = sel[0]

    # ── Bulk save every other row from the tree ───────────────────────────────
    for iid in tab._review_tree.get_children():
        if iid == pending_iid:
            continue  # already written from the edit box above
        vals = tab._review_tree.item(iid, "values")
        txt  = vals[1] if len(vals) > 1 else ""
        if txt == "— empty —":
            txt = ""
        tab.db.update_panel(int(iid), narration_text=txt)

    tab._log("All narrations saved ✓", "success")
    _narrate_review_reload(tab)