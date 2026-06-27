"""
ui/stages/dub_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
DUBBING stage — batch TTS generation + align/split (Phase 2 + Phase 3).
"""

from __future__ import annotations

import json
import threading
import subprocess as _sp
import platform
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, _F, FS, FL, SEL_BG
)
from ui.widgets import _btn, _div


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the DUBBING stage UI inside parent."""
    tab._stage_top_bar(parent, key)

    # Scrollable canvas container
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    vsb    = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    outer = tk.Frame(canvas, bg=BG)
    _win  = canvas.create_window((0, 0), window=outer, anchor="nw")
    outer.bind("<Configure>", lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfig(_win, width=e.width))
    canvas.bind_all("<MouseWheel>",
        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    P = dict(padx=14)

    # ── Section 1: voice profile per language ─────────────────────────────
    tk.Label(outer, text="VOICE PROFILE PER LANGUAGE", font=FL,
             bg=BG, fg=ACCENT).pack(anchor="w", pady=(8, 4), **P)
    tk.Label(outer,
        text="Profiles are filtered by language suffix (e.g. Adam_en for English).\n"
             "Create profiles in the DUBBING tab → VOICE PROFILES.\n"
             "Check the box next to each language to include it in the next dub run.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
        ).pack(anchor="w", pady=(0, 6), **P)

    tab._dub_profile_vars       = {}
    tab._dub_lang_enabled_vars  = {}
    tab._dub_profile_frame = tk.Frame(outer, bg=PANEL2,
                                        highlightbackground=BORDER,
                                        highlightthickness=1)
    ref_hdr = tk.Frame(outer, bg=BG)
    ref_hdr.pack(fill="x", pady=(0, 2), **P)
    _btn(ref_hdr, "↺ REFRESH LANGUAGES",
         lambda: _refresh_dub_profile_table(tab),
         bg=PANEL2, pady=3, padx=8).pack(side="right")
    tab._dub_profile_frame.pack(fill="x", pady=(0, 8), **P)
    _refresh_dub_profile_table(tab)

    _div(outer)

    # ── Section 2: batch settings ─────────────────────────────────────────
    tk.Label(outer, text="BATCH SETTINGS", font=FL, bg=BG, fg=ACCENT
             ).pack(anchor="w", pady=(0, 4), **P)

    batch_row = tk.Frame(outer, bg=BG)
    batch_row.pack(fill="x", pady=(0, 4), **P)
    tk.Label(batch_row, text="Panels per batch:", font=FS, bg=BG,
             fg=TEXT_DIM, width=18, anchor="w").pack(side="left")
    tab._dub_batch_var = tk.StringVar(
        value=tab.db.get_setting("dub_batch_size", "5"))
    tk.Entry(batch_row, textvariable=tab._dub_batch_var, font=FS, width=5,
             bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat",
             highlightthickness=1, highlightcolor=ACCENT,
             highlightbackground=BORDER).pack(side="left")
    tab._dub_batch_var.trace_add(
        "write",
        lambda *_: tab.db.set_setting(
            "dub_batch_size", tab._dub_batch_var.get())
    )
    tk.Label(batch_row,
        text="  —  N panels sent to TTS per call (5 is a safe default)",
        font=FS, bg=BG, fg=MUTED).pack(side="left")

    _div(outer)

    # ── Section 3: batch results table ────────────────────────────────────
    res_hdr = tk.Frame(outer, bg=BG)
    res_hdr.pack(fill="x", pady=(0, 4), **P)
    tk.Label(res_hdr, text="BATCH RESULTS", font=FL, bg=BG,
             fg=ACCENT).pack(side="left")
    _btn(res_hdr, "↺ REFRESH", lambda: _refresh_dub_batch_table(tab),
         bg=PANEL2, pady=3, padx=8).pack(side="right")

    # Language filter dropdown
    filter_row = tk.Frame(outer, bg=BG)
    filter_row.pack(fill="x", pady=(0, 4), **P)
    tk.Label(filter_row, text="Show language:", font=FS, bg=BG,
             fg=TEXT_DIM).pack(side="left", padx=(0, 6))
    tab._dub_lang_filter_var = tk.StringVar(value="all")
    tab._dub_lang_filter_cb  = ttk.Combobox(
        filter_row, textvariable=tab._dub_lang_filter_var,
        state="readonly", width=14, font=FS)
    tab._dub_lang_filter_cb["values"] = ["all"] + [
        f"{lc} ({config.SUPPORTED_LANGUAGES.get(lc, lc)})"
        for lc in config.SUPPORTED_LANGUAGES
    ]
    tab._dub_lang_filter_cb.pack(side="left")
    tab._dub_lang_filter_var.trace_add(
        "write", lambda *_: _refresh_dub_batch_table(tab))

    # Action buttons
    action_row = tk.Frame(outer, bg=BG)
    action_row.pack(fill="x", pady=(0, 4), **P)
    _btn(action_row, "🗑  DELETE BATCH",
         lambda: _dub_delete_selected_batch(tab), fg=ERROR, pady=4, padx=10
         ).pack(side="left", padx=(0, 6))
    _btn(action_row, "🗑  DELETE ALL BATCHES",
         lambda: _dub_delete_all_batches(tab), fg=ERROR, pady=4, padx=10
         ).pack(side="left", padx=(0, 6))
    _btn(action_row, "▶  REGEN BATCH",
         lambda: _dub_regen_selected_batch(tab), bg=PANEL2, pady=4, padx=10
         ).pack(side="left", padx=(0, 6))
    _btn(action_row, "▶▶  REGEN ALL BATCHES",
         lambda: _dub_regen_all_batches(tab), bg=PANEL2, pady=4, padx=10
         ).pack(side="left")

    tree_f = tk.Frame(outer, bg=PANEL2,
                      highlightbackground=BORDER, highlightthickness=1)
    tree_f.pack(fill="x", pady=(0, 12), **P)

    style = ttk.Style()
    style.configure("DB.Treeview",
                     background=PANEL2, foreground=TEXT,
                     fieldbackground=PANEL2, rowheight=24, font=FS)
    style.configure("DB.Treeview.Heading",
                     background=PANEL, foreground=ACCENT, font=FL,
                     relief="flat")
    style.map("DB.Treeview",
              background=[("selected", SEL_BG)],
              foreground=[("selected", TEXT)])

    cols = ("batch", "lang", "panels", "status", "duration", "play")
    tab._dub_batch_tree = ttk.Treeview(
        tree_f, columns=cols, show="headings",
        style="DB.Treeview", selectmode="browse", height=20)
    dbt_sb = ttk.Scrollbar(tree_f, orient="vertical",
                           command=tab._dub_batch_tree.yview)
    tab._dub_batch_tree.configure(yscrollcommand=dbt_sb.set)
    tab._dub_batch_tree.pack(side="left", fill="both", expand=True)
    dbt_sb.pack(side="right", fill="y")

    for col, heading, width, anchor in (
        ("batch",    "BATCH",    55,  "center"),
        ("lang",     "LANG",     50,  "center"),
        ("panels",   "PANELS",   90,  "center"),
        ("status",   "STATUS",   65,  "center"),
        ("duration", "DURATION", 75,  "center"),
        ("play",     "▶ / ■",    65,  "center"),
    ):
        tab._dub_batch_tree.heading(col, text=heading)
        tab._dub_batch_tree.column(col, width=width, anchor=anchor)

    tab._dub_batch_tree.tag_configure("done",    foreground=SUCCESS)
    tab._dub_batch_tree.tag_configure("failed",  foreground=ERROR)
    tab._dub_batch_tree.tag_configure("pending", foreground=MUTED)

    tab._dub_batch_tree.bind("<ButtonRelease-1>",
                              lambda e: _dub_batch_tree_click(tab, e))
    tab._dub_play_proc   = None
    tab._dub_playing_iid = ""

    _refresh_dub_batch_table(tab)


def load(tab: "PipelineTab"):
    """Refresh language rows and batch table."""
    _refresh_dub_profile_table(tab)
    _refresh_dub_batch_table(tab)


def runner(tab: "PipelineTab") -> bool:
    """
    DUBBING runner: batch TTS generation per language (Phase 2).
    SYNC stage handles Phase 3 (align+split) and Phase 4 (time-stretch).
    """
    from dub_engine import DubEngine
    from tts.voice_profile import VoiceProfileManager

    engine = DubEngine(tab.db, on_log=tab._log)
    tab._active_engine = engine

    panels = tab.db.list_panels(tab._episode_id)
    langs  = []
    for code in config.SUPPORTED_LANGUAGES:
        has_text = any(
            (tab.db.get_panel_audio(p["id"], code) or {}).get("translated_text")
            for p in panels[:2]
        )
        if has_text or code == "en":
            langs.append(code)

    if not langs:
        tab._log("No translated languages — run TRANSLATE first", "error")
        return False

    enabled = tab.db.get_setting_json(f"dub_enabled_langs_{tab._episode_id}", [])
    if not isinstance(enabled, list):
        enabled = []
    if enabled:
        langs = [lc for lc in langs if lc in enabled]
        if not langs:
            tab._log(
                "All languages unchecked in DUBBING → DUB? column — "
                "tick at least one and re-run.",
                "error",
            )
            return False

    try:
        batch_size = max(1, int(tab.db.get_setting("dub_batch_size", "5")))
    except ValueError:
        batch_size = 5

    vpm = VoiceProfileManager(str(config.VOICES_DIR))

    try:
        profile_assignments = tab.db.get_setting_json(
            f"dub_profiles_{tab._episode_id}", {})
        if not isinstance(profile_assignments, dict):
            profile_assignments = {}
    except Exception:
        profile_assignments = {}

    # Auto-assign by naming convention for ANY language missing a profile (not
    # just when nothing is saved). This makes the profile the UI already shows
    # (the naming-convention suggestion, e.g. portugures_pt for 'pt') actually
    # usable — so a visible voice can be run without a manual "Refresh".
    try:
        all_profiles = vpm.list_profiles()
        changed = False
        for code in langs:
            cur = profile_assignments.get(code)
            if not cur or cur == "— none —":
                match = [p for p in all_profiles if p.lower().endswith(f"_{code}")]
                if match:
                    profile_assignments[code] = match[0]
                    changed = True
        if changed:
            tab.db.set_setting(f"dub_profiles_{tab._episode_id}", json.dumps(profile_assignments))
            tab._log("Voice profiles auto-assigned from file names ✓", "info")
            tab.after(0, lambda: _refresh_dub_profile_table(tab))
    except Exception as exc:
        tab._log(f"Profile auto-assign warning: {exc}", "warning")

    missing = [
        f"{config.SUPPORTED_LANGUAGES.get(lc, lc)} ({lc})"
        for lc in langs
        if not profile_assignments.get(lc) or profile_assignments[lc] == "— none —"
    ]
    if missing:
        tab._log(
            f"No voice profile for: {', '.join(missing)}\n"
            "  → Open DUBBING, click ↺ REFRESH LANGUAGES, assign profiles, re-run.",
            "error",
        )
        return False

    tab._log(f"Dubbing {len(langs)} language(s), batch_size={batch_size}", "accent")

    for lc in langs:
        if tab._stop_flag:
            return False
        profile_name = profile_assignments.get(lc)
        profile      = vpm.load(profile_name)
        if not profile:
            tab._log(f"  Profile '{profile_name}' not found — skipping '{lc}'", "warning")
            continue
        tab._log(f"  Generating '{lc}' with profile '{profile_name}' …", "info")
        ok = engine.generate_all_batches(
            tab._episode_id, lc, profile, batch_size,
            on_log        = tab._log,
            on_progress   = tab._on_progress,
            on_batch_done = lambda _: tab.after(
                0,
                lambda: _refresh_dub_batch_table(tab)
            ),
        )
        if not ok:
            tab._log(f"  Batch generation failed for '{lc}'", "error")
            return False

    tab._log(
        "DUBBING complete ✓ — continuous audio ready for all language(s).\n"
        "Run SYNC to align, split, and time-stretch per-panel clips.",
        "success",
    )
    return True


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _refresh_dub_profile_table(tab: "PipelineTab"):
    """Build per-language profile dropdown rows."""
    if not hasattr(tab, "_dub_profile_frame"):
        return
    frame = tab._dub_profile_frame
    for w in frame.winfo_children():
        w.destroy()

    if not tab._episode_id:
        tk.Label(frame, text="Load an episode first.", font=FS,
                 bg=PANEL2, fg=MUTED).pack(padx=12, pady=6)
        return

    try:
        from tts.voice_profile import VoiceProfileManager
        all_profiles = VoiceProfileManager(str(config.VOICES_DIR)).list_profiles()
    except Exception:
        all_profiles = []

    # Load saved assignments
    try:
        saved = json.loads(
            tab.db.get_setting(f"dub_profiles_{tab._episode_id}", "{}"))
    except Exception:
        saved = {}

    # Which languages have translated text?
    panels = tab.db.list_panels(tab._episode_id)
    active_langs = []
    for code in config.SUPPORTED_LANGUAGES:
        has = any(
            (tab.db.get_panel_audio(p["id"], code) or {}).get("translated_text")
            for p in panels[:2]
        )
        if has or code == "en":
            active_langs.append(code)

    if not active_langs:
        tk.Label(frame, text="No translated languages found — run TRANSLATE first.",
                 font=FS, bg=PANEL2, fg=MUTED).pack(padx=12, pady=6)
        return

    if not hasattr(tab, "_dub_profile_vars"):
        tab._dub_profile_vars = {}
    tab._dub_profile_vars.clear()
    
    if not hasattr(tab, "_dub_lang_enabled_vars"):
        tab._dub_lang_enabled_vars = {}
    tab._dub_lang_enabled_vars.clear()

    # Load saved language selection (default: all active langs enabled)
    try:
        saved_enabled = json.loads(
            tab.db.get_setting(
                f"dub_enabled_langs_{tab._episode_id}", "[]"))
    except Exception:
        saved_enabled = []

    header_row = tk.Frame(frame, bg=PANEL)
    header_row.pack(fill="x")
    for txt, w in (("LANGUAGE", 14), ("VOICE PROFILE", 26), ("", 4), ("DUB?", 5)):
        tk.Label(header_row, text=txt, font=FL, bg=PANEL, fg=ACCENT,
                 width=w, anchor="w").pack(side="left", padx=6, pady=3)

    def _save_assignments():
        sel = {c: v.get() for c, v in tab._dub_profile_vars.items()}
        tab.db.set_setting(f"dub_profiles_{tab._episode_id}", json.dumps(sel))

    def _save_enabled():
        enabled = [c for c, v in tab._dub_lang_enabled_vars.items()
                   if v.get()]
        tab.db.set_setting(
            f"dub_enabled_langs_{tab._episode_id}", json.dumps(enabled))

    for code in active_langs:
        lang_name = config.SUPPORTED_LANGUAGES.get(code, code.upper())
        # Filter profiles by suffix
        filtered = [p for p in all_profiles
                    if p.lower().endswith(f"_{code}")]
        options  = filtered if filtered else all_profiles
        if not options:
            options = ["— none —"]

        var = tk.StringVar(value=saved.get(code, options[0] if options else ""))
        tab._dub_profile_vars[code] = var

        # Enabled checkbox — default True if no saved selection yet
        enabled_default = (code in saved_enabled) if saved_enabled else True
        en_var = tk.BooleanVar(value=enabled_default)
        tab._dub_lang_enabled_vars[code] = en_var

        row = tk.Frame(frame, bg=PANEL2)
        row.pack(fill="x")
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x")

        tk.Label(row, text=f"{lang_name} ({code})", font=FS,
                 bg=PANEL2, fg=TEXT, width=18, anchor="w"
                 ).pack(side="left", padx=8, pady=4)

        om = tk.OptionMenu(row, var, *options,
                           command=lambda _v, fn=_save_assignments: fn())
        om.config(font=FS, bg=BTN_BG, fg=TEXT, activebackground=ACCENT2,
                  relief="flat", highlightthickness=0, width=24)
        om["menu"].config(bg=BTN_BG, fg=TEXT, activebackground=ACCENT2, font=FS)
        om.pack(side="left", padx=4)

        # Status indicator
        cont_wav = None
        if tab._episode:
            ep = tab.db.get_episode(tab._episode_id)
            if ep:
                from pathlib import Path
                cw = Path(ep["output_folder"]) / "dub" / code / "_continuous.wav"
                if cw.exists():
                    cont_wav = str(cw)
        icon  = "✓" if cont_wav else "○"
        color = SUCCESS if cont_wav else MUTED
        tk.Label(row, text=icon, font=FS, bg=PANEL2, fg=color
                 ).pack(side="left", padx=4)

        # DUB? checkbox
        tk.Checkbutton(
            row, variable=en_var,
            command=_save_enabled,
            bg=PANEL2, activebackground=PANEL2,
            selectcolor=BTN_BG, highlightthickness=0, cursor="hand2",
        ).pack(side="left", padx=8)

    # Auto-persist the current assignments (including auto-detected defaults)
    if tab._dub_profile_vars:
        _save_assignments()
    if tab._dub_lang_enabled_vars:
        _save_enabled()


def _refresh_dub_batch_table(tab: "PipelineTab"):
    if not hasattr(tab, "_dub_batch_tree"):
        return
    tv = tab._dub_batch_tree
    tv.delete(*tv.get_children())
    if not tab._episode_id:
        return

    raw_filter = getattr(tab, "_dub_lang_filter_var",
                         tk.StringVar(value="all")).get()
    lang_filter = None if raw_filter == "all" else raw_filter.split(" ")[0]

    try:
        from dub_engine import DubEngine
        state = DubEngine(tab.db, on_log=tab._log).load_batch_state(
            tab._episode_id)
    except Exception:
        return

    for lang_code, lang_data in state.items():
        if not isinstance(lang_data, dict):
            continue
        if lang_filter and lang_code != lang_filter:
            continue
        for batch in lang_data.get("batches", []):
            b_idx    = batch.get("idx", 0)
            p_from   = batch.get("panel_from", "?")
            p_to     = batch.get("panel_to",   "?")
            status   = batch.get("status",     "pending")
            duration = batch.get("duration",   0.0)
            dur_s    = f"{duration:.1f}s" if duration else "—"
            has_wav  = bool(batch.get("audio_path")
                            and Path(batch["audio_path"]).exists())
            play_lbl = "▶ play" if has_wav else "—"
            tag      = status if status in ("done", "failed", "pending") \
                       else "pending"
            tv.insert("", "end",
                iid=f"{lang_code}:{b_idx}",
                values=(
                    b_idx + 1,
                    lang_code,
                    f"{p_from}–{p_to}",
                    "✓" if status == "done"
                        else ("✗" if status == "failed" else "○"),
                    dur_s,
                    play_lbl,
                ),
                tags=(tag,),
            )

    if getattr(tab, "_dub_playing_iid", ""):
        try:
            vals = list(tv.item(tab._dub_playing_iid, "values"))
            if vals:
                vals[-1] = "■ stop"
                tv.item(tab._dub_playing_iid, values=vals)
        except Exception:
            pass


def _dub_delete_selected_batch(tab: "PipelineTab"):
    sel = tab._dub_batch_tree.selection() if hasattr(tab, "_dub_batch_tree") else []
    if not sel:
        tab._log("Select a batch row first", "warning"); return
    parts     = sel[0].split(":")
    lang_code = parts[0]
    batch_idx = int(parts[1])
    try:
        from dub_engine import DubEngine
        ok = DubEngine(tab.db, on_log=tab._log).delete_batch(
            tab._episode_id, lang_code, batch_idx)
        tab._log(f"Batch {batch_idx + 1} ({lang_code}) deleted ✓" if ok
                  else "Delete failed", "success" if ok else "error")
    except Exception as exc:
        tab._log(f"Delete error: {exc}", "error")
    _refresh_dub_batch_table(tab)


def _dub_regen_selected_batch(tab: "PipelineTab"):
    sel = tab._dub_batch_tree.selection() if hasattr(tab, "_dub_batch_tree") else []
    if not sel:
        tab._log("Select a batch row first", "warning"); return
    if tab._active_thread and tab._active_thread.is_alive():
        tab._log("A stage is already running — wait or press Stop", "warning")
        return
    parts     = sel[0].split(":")
    lang_code = parts[0]
    batch_idx = int(parts[1])

    try:
        saved = json.loads(
            tab.db.get_setting(f"dub_profiles_{tab._episode_id}", "{}"))
    except Exception:
        saved = {}
    profile_name = saved.get(lang_code)
    if not profile_name:
        tab._log(f"No profile assigned for '{lang_code}' — set it above", "warning")
        return

    def _bg():
        try:
            from dub_engine import DubEngine
            from tts.voice_profile import VoiceProfileManager
            vpm     = VoiceProfileManager(str(config.VOICES_DIR))
            profile = vpm.load(profile_name)
            if not profile:
                tab._log(f"Profile '{profile_name}' not found", "error"); return
            engine = DubEngine(tab.db, on_log=tab._log)
            tab._active_engine = engine
            ok = engine.regenerate_batch(
                tab._episode_id, lang_code, profile, batch_idx,
                on_log=tab._log)
            tab._log(f"Batch {batch_idx + 1} regenerated ✓" if ok
                      else f"Batch {batch_idx + 1} regen failed",
                      "success" if ok else "error")
        except Exception as exc:
            tab._log(f"Regen error: {exc}", "error")
        tab.after(0, lambda: _refresh_dub_batch_table(tab))
    threading.Thread(target=_bg, daemon=True, name="batch-regen").start()


def _dub_delete_all_batches(tab: "PipelineTab"):
    raw = getattr(tab, "_dub_lang_filter_var",
                  tk.StringVar(value="all")).get()
    if raw == "all":
        tab._log(
            "Select a specific language in the filter dropdown first — "
            "'all' is too broad for delete-all",
            "warning",
        )
        return
    lang_code = raw.split(" ")[0]
    _dub_stop_playback(tab)
    try:
        from dub_engine import DubEngine
        n = DubEngine(tab.db, on_log=tab._log).delete_all_batches(
            tab._episode_id, lang_code, on_log=tab._log)
        tab._log(
            f"All batches for '{lang_code}' deleted "
            f"({n} file(s) removed) ✓",
            "success",
        )
    except Exception as exc:
        tab._log(f"Delete-all error: {exc}", "error")
    _refresh_dub_batch_table(tab)


def _dub_regen_all_batches(tab: "PipelineTab"):
    raw = getattr(tab, "_dub_lang_filter_var",
                  tk.StringVar(value="all")).get()
    if raw == "all":
        tab._log(
            "Select a specific language in the filter dropdown first",
            "warning",
        )
        return
    lang_code = raw.split(" ")[0]
    if tab._active_thread and tab._active_thread.is_alive():
        tab._log("A stage is already running — wait or press Stop",
                  "warning")
        return
    try:
        saved = json.loads(
            tab.db.get_setting(f"dub_profiles_{tab._episode_id}", "{}"))
    except Exception:
        saved = {}
    profile_name = saved.get(lang_code)
    if not profile_name:
        tab._log(
            f"No profile assigned for '{lang_code}' — "
            "set it in the VOICE PROFILE table above",
            "warning",
        )
        return
    try:
        batch_size = int(tab.db.get_setting("dub_batch_size", "5"))
    except ValueError:
        batch_size = 5

    tab._set_ui_running(True)

    def _bg():
        try:
            from dub_engine import DubEngine
            from tts.voice_profile import VoiceProfileManager
            vpm     = VoiceProfileManager(str(config.VOICES_DIR))
            profile = vpm.load(profile_name)
            if not profile:
                tab._log(f"Profile '{profile_name}' not found", "error")
                return
            engine = DubEngine(tab.db, on_log=tab._log)
            tab._active_engine = engine
            ok = engine.generate_all_batches(
                tab._episode_id, lang_code, profile, batch_size,
                on_log        = tab._log,
                on_progress   = tab._on_progress,
                on_batch_done = lambda _: tab.after(
                    0, lambda: _refresh_dub_batch_table(tab)),
            )
            tab._log(
                f"Regen all [{lang_code}] "
                f"{'complete ✓' if ok else 'failed ✗'}",
                "success" if ok else "error",
            )
        except Exception as exc:
            tab._log(f"Regen-all error: {exc}", "error")
        finally:
            tab.after(0, lambda: tab._set_ui_running(False))
            tab.after(0, lambda: _refresh_dub_batch_table(tab))

    tab._active_thread = threading.Thread(
        target=_bg, daemon=True, name="batch-regen-all")
    tab._active_thread.start()


def _dub_batch_tree_click(tab: "PipelineTab", event):
    tv     = tab._dub_batch_tree
    region = tv.identify_region(event.x, event.y)
    if region != "cell":
        return
    if tv.identify_column(event.x) != "#6":
        return
    iid = tv.identify_row(event.y)
    if not iid:
        return
    if getattr(tab, "_dub_playing_iid", "") == iid:
        _dub_stop_playback(tab)
    else:
        _dub_play_batch(tab, iid)


def _dub_play_batch(tab: "PipelineTab", iid: str):
    _dub_stop_playback(tab)
    parts = iid.split(":")
    if len(parts) != 2:
        return
    lang_code = parts[0]
    batch_idx = int(parts[1])
    try:
        from dub_engine import DubEngine
        state = DubEngine(tab.db).load_batch_state(tab._episode_id)
    except Exception:
        return
    batches = state.get(lang_code, {}).get("batches", [])
    batch   = next((b for b in batches if b.get("idx") == batch_idx), None)
    if not batch:
        return
    wav = batch.get("audio_path", "")
    if not wav or not Path(wav).exists():
        tab._log(f"No audio file for batch {batch_idx + 1} ({lang_code})",
                  "warning")
        return
    try:
        proc = _sp.Popen(
            ["afplay", wav] if platform.system() == "Darwin"
            else ["aplay", "-q", wav])
    except FileNotFoundError as exc:
        tab._log(f"Playback command not found: {exc}", "error")
        return
    tab._dub_play_proc   = proc
    tab._dub_playing_iid = iid
    tv = tab._dub_batch_tree
    try:
        vals = list(tv.item(iid, "values"))
        vals[-1] = "■ stop"
        tv.item(iid, values=vals)
    except Exception:
        pass

    def _poll():
        if tab._dub_play_proc and tab._dub_play_proc.poll() is None:
            tab.after(500, _poll)
        else:
            tab._dub_playing_iid = ""
            tab._dub_play_proc   = None
            try:
                vals = list(tv.item(iid, "values"))
                vals[-1] = "▶ play"
                tv.item(iid, values=vals)
            except Exception:
                pass

    tab.after(500, _poll)


def _dub_stop_playback(tab: "PipelineTab"):
    proc = getattr(tab, "_dub_play_proc", None)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    iid = getattr(tab, "_dub_playing_iid", "")
    if iid and hasattr(tab, "_dub_batch_tree"):
        try:
            tv   = tab._dub_batch_tree
            vals = list(tv.item(iid, "values"))
            vals[-1] = "▶ play"
            tv.item(iid, values=vals)
        except Exception:
            pass
    tab._dub_play_proc   = None
    tab._dub_playing_iid = ""