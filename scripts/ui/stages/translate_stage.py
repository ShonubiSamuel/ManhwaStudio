"""
ui/stages/translate_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
TRANSLATE stage — Translates refined English text into selected languages.
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, List

import config

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, INFO,
    BTN_BG, FS, FL, SEL_BG
)
from ui.widgets import _btn, _div


def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the TRANSLATE stage UI."""
    tab._stage_top_bar(parent, key)

    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    vsb    = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    scroll_outer = tk.Frame(canvas, bg=BG)
    _win = canvas.create_window((0, 0), window=scroll_outer, anchor="nw")
    scroll_outer.bind("<Configure>", lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(_win, width=e.width))
    canvas.bind_all("<MouseWheel>",
        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    P = dict(padx=12)

    # ── AI Provider toggle ────────────────────────────────────────────────
    tk.Label(scroll_outer, text="AI PROVIDER",
             font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(8, 6), **P)

    saved_translate_provider = tab.db.get_setting("ai_provider_translate", "nvidia")
    tab._translate_provider_var = tk.StringVar(value=saved_translate_provider)

    prov_row = tk.Frame(scroll_outer, bg=BG)
    prov_row.pack(fill="x", pady=(0, 6), **P)

    def _set_translate_provider(p):
        tab._translate_provider_var.set(p)
        tab.db.set_setting("ai_provider_translate", p)
        if p == "lm_studio":
            lms_trans_frame.pack(fill="x", pady=(0, 8))
        else:
            lms_trans_frame.pack_forget()

    tk.Radiobutton(
        prov_row, text="NVIDIA NIM  (cloud — parallel)",
        variable=tab._translate_provider_var, value="nvidia",
        command=lambda: _set_translate_provider("nvidia"),
        font=FS, bg=BG, fg=TEXT, activebackground=BG, activeforeground=ACCENT,
        selectcolor=BTN_BG, highlightthickness=0, cursor="hand2",
    ).pack(side="left", padx=(0, 16))
    tk.Radiobutton(
        prov_row, text="LM Studio  (local — parallel)",
        variable=tab._translate_provider_var, value="lm_studio",
        command=lambda: _set_translate_provider("lm_studio"),
        font=FS, bg=BG, fg=TEXT, activebackground=BG, activeforeground=ACCENT,
        selectcolor=BTN_BG, highlightthickness=0, cursor="hand2",
    ).pack(side="left")

    lms_trans_frame = tk.Frame(scroll_outer, bg=PANEL2,
                               highlightbackground=BORDER, highlightthickness=1)
    tk.Label(lms_trans_frame,
             text="  ⓘ  Server URL, model name, and Max Concurrent are configured\n"
                  "       in Settings  →  LM STUDIO",
             font=FS, bg=PANEL2, fg=INFO, justify="left",
             ).pack(anchor="w", padx=12, pady=10)
    tk.Frame(lms_trans_frame, bg=PANEL2, height=2).pack()

    if saved_translate_provider == "lm_studio":
        lms_trans_frame.pack(fill="x", pady=(0, 8), **P)

    _div(scroll_outer)

    # ── Language checkboxes ───────────────────────────────────────────────
    tk.Label(scroll_outer, text="TRANSLATE INTO:", font=FL,
             bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0, 4), **P)

    lang_grid = tk.Frame(scroll_outer, bg=BG)
    lang_grid.pack(fill="x", pady=(0, 10), **P)

    tab._translate_lang_vars = {}
    saved_langs = tab.db.get_setting_json(f"translate_langs_{tab._episode_id}", [])

    def _on_lang_toggle():
        selected = [c for c, v in tab._translate_lang_vars.items() if v.get()]
        tab.db.set_setting(f"translate_langs_{tab._episode_id}", json.dumps(selected))

    for i, (code, name) in enumerate(config.SUPPORTED_LANGUAGES.items()):
        if code == "en": continue
        var = tk.BooleanVar(value=code in saved_langs)
        tab._translate_lang_vars[code] = var
        col, row = i % 5, i // 5
        tk.Checkbutton(
            lang_grid, text=f"{name} ({code})", variable=var,
            font=FS, bg=BG, fg=TEXT, activebackground=BG, activeforeground=ACCENT,
            selectcolor=BTN_BG, highlightthickness=0, cursor="hand2", command=_on_lang_toggle,
        ).grid(row=row, column=col, sticky="w", padx=8, pady=2)
        
    _div(scroll_outer)

    # ── Per-language settings ─────────────────────────────────────────────
    tk.Label(scroll_outer, text="PER-LANGUAGE SETTINGS", font=FL, bg=BG, fg=ACCENT).pack(anchor="w", pady=(0, 4), **P)
    tk.Label(
        scroll_outer,
        text="Override batch size per language (smaller = fewer API truncations).\n"
             "RETRANSLATE clears and re-runs one language without touching others.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left"
    ).pack(anchor="w", pady=(0, 6), **P)

    tab._translate_lang_settings_frame = tk.Frame(scroll_outer, bg=BG)
    tab._translate_lang_settings_frame.pack(fill="x", pady=(0, 6), **P)
    tab._translate_batch_size_vars = {}
    _build_translate_lang_settings(tab)

    # ── Panel range retranslate ───────────────────────────────────────────
    range_row = tk.Frame(scroll_outer, bg=BG)
    range_row.pack(fill="x", pady=(0, 6), **P)
    tk.Label(range_row, text="Retranslate range:", font=FS, bg=BG, fg=TEXT_DIM).pack(side="left", padx=(0, 6))
    tk.Label(range_row, text="Lang:", font=FS, bg=BG, fg=TEXT_DIM).pack(side="left")
    
    tab._range_lang_var = tk.StringVar(value="")
    range_lang_cb = ttk.Combobox(range_row, textvariable=tab._range_lang_var, state="readonly", width=10, font=FS)
    range_lang_cb["values"] = [f"{lc}" for lc in config.SUPPORTED_LANGUAGES if lc != "en"]
    range_lang_cb.pack(side="left", padx=(0, 10))
    
    tk.Label(range_row, text="Panels:", font=FS, bg=BG, fg=TEXT_DIM).pack(side="left")
    tab._range_from_var = tk.StringVar(value="0")
    tab._range_to_var   = tk.StringVar(value="0")
    tk.Entry(range_row, textvariable=tab._range_from_var, font=FS, width=5, bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat").pack(side="left", padx=4)
    tk.Label(range_row, text="–", font=FS, bg=BG, fg=TEXT_DIM).pack(side="left")
    tk.Entry(range_row, textvariable=tab._range_to_var, font=FS, width=5, bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat").pack(side="left", padx=4)
    _btn(range_row, "🗑 CLEAR RANGE", lambda: _translate_clear_range(tab), fg=ERROR, pady=3, padx=8).pack(side="left", padx=(10, 0))
    tk.Label(range_row, text="← clears those panels; next TRANSLATE fills gaps", font=FS, bg=BG, fg=MUTED).pack(side="left", padx=(8, 0))

    _div(scroll_outer)

    # ── Run button ────────────────────────────────────────────────────────
    _btn(scroll_outer, "▶  TRANSLATE NOW", lambda: tab._run_single("translate"), bg=ACCENT, fg="#000", pady=6, padx=14).pack(anchor="w", pady=(6, 10), **P)

    # ── Results treeview (Wide table) ─────────────────────────────────────
    tk.Label(scroll_outer, text="RESULTS:", font=FL, bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(4, 2), **P)

    tree_wrap = tk.Frame(scroll_outer, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
    tree_wrap.pack(fill="both", expand=True, pady=4, **P)

    _style = ttk.Style()
    _style.configure("P.Treeview", background=PANEL2, foreground=TEXT, fieldbackground=PANEL2, rowheight=22, font=FS, borderwidth=0)
    _style.configure("P.Treeview.Heading", background=PANEL, foreground=ACCENT, font=FL, relief="flat")
    _style.map("P.Treeview", background=[("selected", SEL_BG)], foreground=[("selected", TEXT)])

    all_cols   = ["#", "en"] + list(config.SUPPORTED_LANGUAGES.keys())[1:]
    col_widths = {"#": 40, "en": 220, **{k: 130 for k in list(config.SUPPORTED_LANGUAGES.keys())[1:]}}

    tab._translate_tree = ttk.Treeview(tree_wrap, columns=all_cols, show="headings", style="P.Treeview", height=22)
    vsb_t = ttk.Scrollbar(tree_wrap, orient="vertical", command=tab._translate_tree.yview)
    hsb_t = ttk.Scrollbar(tree_wrap, orient="horizontal", command=tab._translate_tree.xview)
    tab._translate_tree.configure(yscrollcommand=vsb_t.set, xscrollcommand=hsb_t.set)

    vsb_t.pack(side="right", fill="y")
    hsb_t.pack(side="bottom", fill="x")
    tab._translate_tree.pack(side="left", fill="both", expand=True)

    for col in all_cols:
        w = col_widths.get(col, 130)
        tab._translate_tree.heading(col, text=col.upper().replace("_", " "))
        tab._translate_tree.column(col, width=w, anchor="w" if col in ("en",) else "center")

    _reload_translate_tree(tab)


def load(tab: "PipelineTab"):
    _reload_translate_tree(tab)
    _build_translate_lang_settings(tab)


def runner(tab: "PipelineTab") -> bool:
    from ai_engine import translate_subset_parallel

    provider = tab.db.get_setting("ai_provider_translate", "nvidia")
    api_key         = tab.db.get_setting("nvidia_api_key", "")
    lm_studio_url   = tab.db.get_setting("lm_studio_url", "http://localhost:1234/v1")
    lm_studio_model = tab.db.get_setting("lm_studio_model", "")
    context_length  = int(tab.db.get_setting("lm_studio_context_length", "32768"))

    if provider == "lm_studio":
        max_concurrent = int(tab.db.get_setting("lm_studio_max_concurrent", "4"))
        batch_size     = int(tab.db.get_setting("lm_studio_batch_size", "6"))
    else:
        max_concurrent = int(tab.db.get_setting("nvidia_max_concurrent", "6"))
        batch_size     = int(tab.db.get_setting("nvidia_batch_size", "30"))

    if provider == "nvidia" and not api_key:
        tab._log("NVIDIA API key not set — add it in Settings tab", "error")
        return False
    elif provider == "lm_studio" and not lm_studio_model.strip():
        tab._log("LM Studio model name not set — configure it in Settings", "error")
        return False

    single = getattr(tab, "_translate_single_lang", None)
    if single:
        langs = [single]
    else:
        langs = [lc for lc, v in tab._translate_lang_vars.items() if v.get()]
        if not langs:
            langs = tab.db.get_setting_json(f"translate_langs_{tab._episode_id}", [])
            # Also translate any language enabled in DUB — so a language you ticked
            # in the Dub stage (e.g. for a "Run all") gets translated automatically,
            # not skipped for "no translation".
            dub_enabled = tab.db.get_setting_json(f"dub_enabled_langs_{tab._episode_id}", [])
            langs = list(dict.fromkeys(list(langs) + list(dub_enabled or [])))
            langs = [lc for lc in langs if lc in config.SUPPORTED_LANGUAGES and lc != "en"]

    if not langs:
        tab._log("No languages selected — open TRANSLATE and tick at least one", "warning")
        return False

    ep     = tab.db.get_episode(tab._episode_id)
    panels = sorted(tab.db.list_panels(tab._episode_id), key=lambda p: p["panel_index"])
    tone   = ep.get("tone_prompt") or ""
    panel_texts = [(p.get("narration_text") or p.get("transcript_text") or "").strip() for p in panels]
    n = len(panel_texts)

    lang_work = {}
    for lc in langs:
        missing = [i for i, panel in enumerate(panels) if not ((tab.db.get_panel_audio(panel["id"], lc) or {}).get("translated_text", "").strip())]
        lang_name = config.SUPPORTED_LANGUAGES.get(lc, lc.upper())
        if not missing:
            tab._log(f"  [{lc}] {lang_name}: all {n} panels already translated \u2014 skipping", "info")
            continue
        tab._log(f"  [{lc}] {lang_name}: {len(missing)}/{n} missing \u2014 translating gaps only", "info")
        lang_work[lc] = {"indices": missing, "texts": [panel_texts[i] for i in missing]}

    if not lang_work:
        tab._log("All selected languages already fully translated ✓", "info")
        return True

    prov_label = f"LM Studio (parallel ×{max_concurrent})" if provider == "lm_studio" else f"NVIDIA NIM (parallel ×{max_concurrent})"
    total_missing = sum(len(w["indices"]) for w in lang_work.values())
    tab._log(f"TRANSLATE — {len(lang_work)} language(s), {total_missing} missing panel(s), batch={batch_size} [{prov_label}]", "accent")

    if provider == "lm_studio":
        try:
            from ai_engine import load_lmstudio_model
            tab._log(f"Loading '{lm_studio_model}' into LM Studio …", "info")
            if not load_lmstudio_model(lm_studio_url, lm_studio_model, context_length):
                tab._log("LM Studio load failed \u2014 is it running?", "error")
                return False
        except Exception as exc:
            tab._log(f"LM Studio load error: {exc}", "error")
            return False

    def _on_batch_done(lang_code: str, start: int, end: int, texts: List[str]):
        indices = lang_work[lang_code]["indices"]
        for i, txt in enumerate(texts):
            sub_i  = start + i
            if sub_i >= len(indices) or not txt: continue
            panel  = panels[indices[sub_i]]
            try:
                tab.db.ensure_panel_audio(panel["id"], lang_code)
                row = tab.db.get_panel_audio(panel["id"], lang_code)
                if row: tab.db.update_panel_audio(row["id"], translated_text=txt)
            except Exception: pass
        
        orig_slice = [panels[indices[start + j]] for j in range(end - start) if (start + j) < len(indices)]
        tab.after(0, lambda lc=lang_code, sl=orig_slice, tx=list(texts): _patch_translate_rows(tab, lc, sl, tx))

    lang_batch_sizes = {}
    for lc in langs:
        val = tab.db.get_setting(f"translate_batch_size_{lc}", "").strip()
        if val:
            try: lang_batch_sizes[lc] = max(1, int(val))
            except ValueError: pass

    try:
        results = translate_subset_parallel(
            lang_subset      = {lc: w["texts"] for lc, w in lang_work.items()},
            tone_prompt      = tone, provider         = provider, api_key          = api_key,
            lm_studio_url    = lm_studio_url, lm_studio_model  = lm_studio_model,
            batch_size       = batch_size, max_concurrent   = max_concurrent,
            on_log           = tab._log, on_progress      = tab._on_progress,
            on_batch_done    = _on_batch_done, context_length   = context_length,
            lang_batch_sizes = lang_batch_sizes if lang_batch_sizes else None,
        )

        for lc, translated in results.items():
            indices = lang_work[lc]["indices"]
            for i, txt in enumerate(translated):
                if i >= len(indices) or not txt: continue
                panel  = panels[indices[i]]
                try:
                    tab.db.ensure_panel_audio(panel["id"], lc)
                    row = tab.db.get_panel_audio(panel["id"], lc)
                    if row and not (row.get("translated_text") or "").strip():
                        tab.db.update_panel_audio(row["id"], translated_text=txt)
                except Exception: pass

        total_got = sum(sum(1 for t in translated if t) for translated in results.values())
        if total_got == 0 and total_missing > 0:
            tab._log("TRANSLATE failed — 0 panels saved. Check API connection.", "error")
            return False

        tab.after(0, lambda: _reload_translate_tree(tab))
        return True

    finally:
        if provider == "lm_studio":
            try:
                from ai_engine import unload_lmstudio_model
                unload_lmstudio_model(lm_studio_model, lm_studio_url)
            except Exception: pass


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _reload_translate_tree(tab: "PipelineTab"):
    if not hasattr(tab, "_translate_tree") or not tab._episode_id: return
    tv = tab._translate_tree
    tv.delete(*tv.get_children())
        
    panels = sorted(tab.db.list_panels(tab._episode_id), key=lambda p: p["panel_index"])
    all_codes = list(config.SUPPORTED_LANGUAGES.keys())
    
    for p in panels:
        row_vals = [p["panel_index"] + 1]
        for lc in all_codes:
            if lc == "en":
                # Show narration_text only — no fallback to transcript_text.
                # When REFINE is in progress or has been cleared, the English
                # column should show blank (pending) rather than reverting to
                # the raw Whisper output, which is confusing and misleading.
                txt = (p.get("narration_text") or "")[:60]
            else:
                audio = tab.db.get_panel_audio(p["id"], lc)
                txt   = ((audio or {}).get("translated_text") or "")[:60]
            row_vals.append(txt)
        tv.insert("", "end", iid=str(p["id"]), values=row_vals)


def _patch_translate_rows(tab: "PipelineTab", lang_code: str, panels_slice: list, texts: List[str]):
    tv = getattr(tab, "_translate_tree", None)
    if tv is None: return
    all_codes = list(config.SUPPORTED_LANGUAGES.keys())
    try: col_idx = all_codes.index(lang_code) + 1
    except ValueError: return
    
    for panel, txt in zip(panels_slice, texts):
        if not txt: continue
        try:
            vals = list(tv.item(str(panel["id"]), "values"))
            if col_idx < len(vals):
                vals[col_idx] = txt[:60]
                tv.item(str(panel["id"]), values=vals)
        except Exception: pass


def _build_translate_lang_settings(tab: "PipelineTab"):
    if not hasattr(tab, "_translate_lang_settings_frame"): return
    frame = tab._translate_lang_settings_frame
    for w in frame.winfo_children(): w.destroy()
    tab._translate_batch_size_vars.clear()

    hdr = tk.Frame(frame, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
    hdr.pack(fill="x", pady=(0, 2))
    for text, width in (("LANGUAGE", 160), ("BATCH SIZE", 90), ("STATUS", 100), ("ACTION", 160)):
        tk.Label(hdr, text=text, font=FL, bg=PANEL2, fg=ACCENT, width=width // 8, anchor="w").pack(side="left", padx=8)

    for lc, lang_name in config.SUPPORTED_LANGUAGES.items():
        if lc == "en": continue
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", pady=1)

        tk.Label(row, text=f"{lang_name} ({lc})", font=FS, bg=BG, fg=TEXT, width=20, anchor="w").pack(side="left", padx=8)

        saved_bs = tab.db.get_setting(f"translate_batch_size_{lc}", "")
        bs_var   = tk.StringVar(value=saved_bs)
        tab._translate_batch_size_vars[lc] = bs_var
        bs_entry = tk.Entry(row, textvariable=bs_var, font=FS, width=5, bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", highlightthickness=1, highlightcolor=ACCENT, highlightbackground=BORDER)
        bs_entry.pack(side="left", padx=4)
        tk.Label(row, text="(blank = default)", font=FS, bg=BG, fg=MUTED).pack(side="left", padx=(2, 12))
        
        bs_var.trace_add("write", lambda *_, c=lc, v=bs_var: tab.db.set_setting(f"translate_batch_size_{c}", v.get().strip()))

        if tab._episode_id:
            try:
                panels  = tab.db.list_panels(tab._episode_id)
                n_total = len(panels)
                n_done  = sum(1 for p in panels if ((tab.db.get_panel_audio(p["id"], lc) or {}).get("translated_text", "").strip()))
                status_txt = f"{n_done}/{n_total} ✓" if n_done == n_total else (f"{n_done}/{n_total} ⚠" if n_done else "0 —")
                status_fg  = SUCCESS if n_done == n_total else (ACCENT if n_done else MUTED)
            except Exception:
                status_txt, status_fg = "—", MUTED
        else:
            status_txt, status_fg = "—", MUTED

        tk.Label(row, text=status_txt, font=FS, bg=BG, fg=status_fg, width=10).pack(side="left", padx=4)
        _btn(row, f"↺ RETRANSLATE {lc.upper()}", lambda c=lc: _retranslate_language(tab, c), fg=ACCENT, bg=PANEL2, pady=3, padx=8).pack(side="left")


def _retranslate_language(tab: "PipelineTab", lang_code: str):
    if tab._active_thread and tab._active_thread.is_alive():
        tab._log("A stage is already running — wait or press Stop", "warning")
        return
    n = tab.db.clear_language_translation(tab._episode_id, lang_code)
    tab._log(f"[{lang_code}]: {n} translations cleared. Retranslating...", "warning")
    try:
        from dub_engine import DubEngine
        DubEngine(tab.db, on_log=tab._log).delete_all_batches(tab._episode_id, lang_code, on_log=tab._log)
    except Exception: pass
        
    if hasattr(tab, "_translate_tree"): _reload_translate_tree(tab)
        
    tab._translate_single_lang = lang_code
    try: tab._run_single("translate")
    finally: tab._translate_single_lang = None


def _translate_clear_range(tab: "PipelineTab"):
    lc = getattr(tab, "_range_lang_var", tk.StringVar()).get().strip()
    if not lc: return tab._log("Select a language for the range clear", "warning")
    try:
        from_p = int(getattr(tab, "_range_from_var", tk.StringVar(value="0")).get())
        to_p   = int(getattr(tab, "_range_to_var", tk.StringVar(value="0")).get())
    except ValueError: return tab._log("Panel range must be integers", "error")
    
    n = tab.db.clear_language_translation_range(tab._episode_id, lc, from_p, to_p)
    tab._log(f"[{lc}] Panels {from_p}–{to_p} cleared ({n} row(s)).", "warning")
    if hasattr(tab, "_translate_tree"): _reload_translate_tree(tab)