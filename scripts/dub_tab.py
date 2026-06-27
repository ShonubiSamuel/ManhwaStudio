"""
dub_tab.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Top-level DUBBING tab.

⚠  MANUAL DEBUG INTERFACE — use the Pipeline tab for production runs.
   Phase runners here use the same engine methods as pipeline_tab (generate_all_batches).

Voice profile modes
───────────────────
  CUSTOM VOICE  — preset speaker (Aiden, Eric, Sohee, …)
  VOICE DESIGN  — text description of the voice you want
  VOICE CLONE   — reference audio file + optional transcript
"""

from __future__ import annotations

import os
import sys
import subprocess
import tempfile
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk
from typing import Callable, Dict, List, Optional

import config
from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, BTN_FG, SEL_BG,
    _F, FL, FB, FS, FBTN,
)
from ui.widgets import (
    _FlatBtn, _btn, _sec, _div, _entry, _option_menu, _LangRow,
)


# ── Add Language dialog ───────────────────────────────────────────────────────

class _AddLangDialog(tk.Toplevel):
    def __init__(self, parent, existing):
        super().__init__(parent)
        self.result: List[str] = []
        self.title("Add Language")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        tk.Label(self, text="Select languages to add:",
                 font=FL, bg=BG, fg=TEXT_DIM).pack(anchor="w", padx=20, pady=(18, 6))
        self._vars: Dict[str, tk.BooleanVar] = {}
        grid = tk.Frame(self, bg=BG)
        grid.pack(padx=20, pady=(0, 10))
        items = [(c, n) for c, n in config.SUPPORTED_LANGUAGES.items() if c not in existing]
        for i, (code, name) in enumerate(items):
            var = tk.BooleanVar(value=False)
            self._vars[code] = var
            row, col = divmod(i, 2)
            tk.Checkbutton(grid, text=f"{name}  ({code})", variable=var,
                           font=FS, bg=BG, fg=TEXT, activebackground=BG,
                           selectcolor=BTN_BG, highlightthickness=0, cursor="hand2",
                           ).grid(row=row, column=col, sticky="w", padx=8, pady=2)
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=20, pady=(0, 16))
        _btn(btn_row, "ADD SELECTED", self._ok, bg=ACCENT, fg="#000").pack(side="left", padx=(0, 8))
        _btn(btn_row, "Cancel", self.destroy, bg=PANEL2).pack(side="left")
        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width()  // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        self.geometry(f"+{pw - self.winfo_width()//2}+{ph - self.winfo_height()//2}")
        self.wait_window()

    def _ok(self):
        self.result = [c for c, v in self._vars.items() if v.get()]
        self.destroy()


# ── Main DubbingTab ───────────────────────────────────────────────────────────

class DubbingTab(tk.Frame):

    def __init__(self, parent, db, on_log: Callable):
        super().__init__(parent, bg=BG)
        self.db      = db
        self._on_log = on_log

        # Episode state
        self._episode_id:   Optional[int]  = None
        self._episode:      Optional[dict] = None
        self._active_langs: List[str]      = []

        # UI vars
        self._ep_var          = tk.StringVar()
        self._ep_map:   Dict  = {}
        self._lang_rows: Dict = {}
        self._selected_lang   = tk.StringVar(value="en")
        self._profile_var     = tk.StringVar()
        self._phase_lang_var  = tk.StringVar(value="all")
        self._prog_var        = tk.IntVar(value=0)

        # Profile editor vars
        self._pe_name_var     = tk.StringVar()
        self._pe_lang_var     = tk.StringVar(value="English")
        self._pe_mode_var     = tk.StringVar(value="CustomVoice")
        self._pe_model_var    = tk.StringVar(value=config.TTS_RECOMMENDED_MODELS.get("CustomVoice","1.7B-CustomVoice"))
        self._pe_speaker_var  = tk.StringVar(value=config.TTS_PRESET_SPEAKERS[0] if config.TTS_PRESET_SPEAKERS else "Aiden")
        self._pe_instruct_var = tk.StringVar()
        self._pe_refwav_var   = tk.StringVar()
        self._pe_reftext_var  = tk.StringVar()
        self._pe_xvec_var     = tk.BooleanVar(value=True)
        self._pe_temp_var     = tk.StringVar(value="0.7")
        self._pe_topp_var     = tk.StringVar(value="1.0")
        self._pe_topk_var     = tk.StringVar(value="50")
        self._pe_rep_var      = tk.StringVar(value="1.1")
        self._pe_maxt_var     = tk.StringVar(value="2048")
        self._pe_adv_shown    = False
        self._pe_adv_frame: Optional[tk.Frame] = None
        self._pe_preview_lbl: Optional[tk.Label] = None

        self._custom_frame: Optional[tk.Frame] = None
        self._design_frame: Optional[tk.Frame] = None
        self._clone_frame:  Optional[tk.Frame]  = None

        self._active_engine = None
        self._active_thread: Optional[threading.Thread] = None
        self._stop_flag      = False

        self._build()
        self._refresh_episode_menu()

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    def load_episode(self, episode_id: int):
        self._episode_id = episode_id
        self._episode    = self.db.get_episode(episode_id)
        if not self._episode:
            return
        for label, eid in self._ep_map.items():
            if eid == episode_id:
                self._ep_var.set(label)
                break
        self._on_load_episode()

    def stop(self):
        self._stop_flag = True
        if self._active_engine:
            try:
                self._active_engine.stop()
            except Exception:
                pass

    def refresh(self):
        self._refresh_episode_menu()
        if self._episode_id:
            self._refresh_lang_list()
            self._refresh_audio_table()
            self._refresh_profile_list()

    # ══════════════════════════════════════════════════════════════════════════
    # BUILD — SKELETON
    # ══════════════════════════════════════════════════════════════════════════

    def _build(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=5, sashrelief="flat")
        pw.pack(fill="both", expand=True)
        left  = tk.Frame(pw, bg=BG, width=260)
        right = tk.Frame(pw, bg=BG)
        pw.add(left,  minsize=220)
        pw.add(right, minsize=540)
        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent: tk.Frame):
        # Warning banner — Phase 0 D1 decision
        warn = tk.Frame(parent, bg="#2a1a0a")
        warn.pack(fill="x", pady=(0, 6))
        tk.Label(warn,
            text="⚠  MANUAL DEBUG INTERFACE\nUse the Pipeline tab for production runs.",
            font=(_F, 7, "bold"), bg="#2a1a0a", fg=WARNING,
            justify="center", pady=4,
        ).pack()

        _sec(parent, "EPISODE")
        ep_menu = tk.OptionMenu(parent, self._ep_var, "— none —")
        ep_menu.config(font=FS, bg=BTN_BG, fg=TEXT, activebackground=ACCENT2,
                       relief="flat", highlightthickness=0, wraplength=220)
        ep_menu["menu"].config(bg=BTN_BG, fg=TEXT, activebackground=ACCENT2, font=FS)
        ep_menu.pack(fill="x", pady=(0, 4))
        self._ep_menu = ep_menu

        _btn(parent, "LOAD", self._on_load_click, bg=ACCENT, fg="#000").pack(fill="x", pady=(0, 8))
        self._ep_info_lbl = tk.Label(parent, text="", font=FS, bg=BG, fg=MUTED,
                                      wraplength=230, justify="left")
        self._ep_info_lbl.pack(anchor="w")

        _sec(parent, "LANGUAGES")
        lang_frame = tk.Frame(parent, bg=PANEL2,
                              highlightbackground=BORDER, highlightthickness=1)
        lang_frame.pack(fill="both", expand=True, pady=(0, 4))
        self._lang_inner = tk.Frame(lang_frame, bg=PANEL2)
        self._lang_inner.pack(fill="both", expand=True, padx=2, pady=2)

        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill="x", pady=(0, 4))
        _btn(btn_row, "+ ADD",    self._add_languages,  bg=ACCENT, fg="#000", pady=4, padx=8).pack(side="left", padx=(0, 4))
        _btn(btn_row, "− REMOVE", self._remove_language, fg=ERROR,  pady=4, padx=8).pack(side="left")

        _sec(parent, "ACTIVE DUBBING PROFILE")
        prof_row = tk.Frame(parent, bg=BG)
        prof_row.pack(fill="x", pady=(0, 4))
        self._profile_menu = tk.OptionMenu(prof_row, self._profile_var, "— none —")
        self._profile_menu.config(font=FS, bg=BTN_BG, fg=TEXT,
                                   activebackground=ACCENT2, relief="flat", highlightthickness=0)
        self._profile_menu["menu"].config(bg=BTN_BG, fg=TEXT, activebackground=ACCENT2, font=FS)
        self._profile_menu.pack(side="left", fill="x", expand=True)
        _btn(prof_row, "↺", self._refresh_active_profile_menu,
             bg=PANEL2, pady=3, padx=6).pack(side="left", padx=(4, 0))
        self._refresh_active_profile_menu()

    def _build_right(self, parent: tk.Frame):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)
        vp_frame  = tk.Frame(nb, bg=BG)
        dub_frame = tk.Frame(nb, bg=BG)
        nb.add(vp_frame,  text="  🎙  VOICE PROFILES  ")
        nb.add(dub_frame, text="  🎬  DUBBING  ")
        self._build_voice_profiles_tab(vp_frame)
        self._build_dubbing_tab(dub_frame)

    # ══════════════════════════════════════════════════════════════════════════
    # VOICE PROFILES TAB
    # ══════════════════════════════════════════════════════════════════════════

    def _build_voice_profiles_tab(self, parent: tk.Frame):
        pw = tk.PanedWindow(parent, orient="horizontal", bg=BG,
                            sashwidth=4, sashrelief="flat")
        pw.pack(fill="both", expand=True, padx=4, pady=4)
        list_frame   = tk.Frame(pw, bg=BG, width=220)
        editor_frame = tk.Frame(pw, bg=BG)
        pw.add(list_frame,   minsize=180)
        pw.add(editor_frame, minsize=380)
        self._build_profile_list(list_frame)
        self._build_profile_editor(editor_frame)

    def _build_profile_list(self, parent: tk.Frame):
        tk.Label(parent, text="PROFILES", font=FL, bg=BG, fg=ACCENT
                 ).pack(anchor="w", pady=(8, 4), padx=8)
        tree_frame = tk.Frame(parent, bg=PANEL2,
                              highlightbackground=BORDER, highlightthickness=1)
        tree_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        style = ttk.Style()
        style.configure("VP.Treeview",
                         background=PANEL2, foreground=TEXT,
                         fieldbackground=PANEL2, rowheight=22, font=FS)
        style.configure("VP.Treeview.Heading",
                         background=PANEL, foreground=ACCENT, font=FL, relief="flat")
        style.map("VP.Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", TEXT)])
        self._profile_tree = ttk.Treeview(
            tree_frame, columns=("name","mode","lang"),
            show="headings", style="VP.Treeview", selectmode="browse")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._profile_tree.yview)
        self._profile_tree.configure(yscrollcommand=sb.set)
        self._profile_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._profile_tree.heading("name", text="NAME")
        self._profile_tree.heading("mode", text="MODE")
        self._profile_tree.heading("lang", text="LANG")
        self._profile_tree.column("name", width=100, anchor="w")
        self._profile_tree.column("mode", width=70,  anchor="center")
        self._profile_tree.column("lang", width=45,  anchor="center")
        self._profile_tree.bind("<<TreeviewSelect>>", self._on_profile_list_select)
        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill="x", padx=4, pady=(0, 4))
        _btn(btn_row, "+ NEW",   self._profile_new,    bg=ACCENT, fg="#000", pady=3, padx=8).pack(side="left", padx=(0, 3))
        _btn(btn_row, "🗑 DEL",  self._profile_delete, fg=ERROR,  pady=3, padx=8).pack(side="left", padx=(0, 3))
        _btn(btn_row, "↺",       self._refresh_profile_list, bg=PANEL2, pady=3, padx=8).pack(side="left")
        self._refresh_profile_list()

    def _refresh_profile_list(self):
        if not hasattr(self, "_profile_tree"):
            return
        self._profile_tree.delete(*self._profile_tree.get_children())
        try:
            from tts.voice_profile import VoiceProfileManager, VoiceProfile
            vpm = VoiceProfileManager(str(config.VOICES_DIR))
            for name in vpm.list_profiles():
                p = vpm.load(name)
                if p:
                    mode_short = {"CustomVoice": "Custom", "VoiceDesign": "Design",
                                  "VoiceClone": "Clone"}.get(p.mode, p.mode)
                    lang_short = p.language[:2].lower() if p.language else "??"
                    self._profile_tree.insert("", "end", iid=name,
                        values=(name, mode_short, lang_short))
        except Exception as exc:
            self._log(f"Could not load profiles: {exc}", "warning")
        self._refresh_active_profile_menu()

    def _on_profile_list_select(self, _event):
        sel = self._profile_tree.selection()
        if not sel:
            return
        self._profile_load_into_editor(sel[0])

    def _profile_load_into_editor(self, name: str):
        try:
            from tts.voice_profile import VoiceProfileManager
            vpm     = VoiceProfileManager(str(config.VOICES_DIR))
            profile = vpm.load(name)
            if not profile:
                return
            self._pe_name_var.set(profile.name)
            self._pe_lang_var.set(profile.language)
            self._pe_mode_var.set(profile.mode)
            self._pe_model_var.set(profile.model)
            self._pe_speaker_var.set(profile.speaker or "")
            self._pe_instruct_var.set(profile.instruct or "")
            self._pe_refwav_var.set(profile.ref_wav_path or "")
            self._pe_reftext_var.set(profile.ref_wav_text or "")
            self._pe_xvec_var.set(profile.x_vector_only)
            self._pe_temp_var.set(str(profile.temperature))
            self._pe_topp_var.set(str(profile.top_p))
            self._pe_topk_var.set(str(profile.top_k))
            self._pe_rep_var.set(str(profile.repetition_penalty))
            self._pe_maxt_var.set(str(profile.max_new_tokens))
            if hasattr(self, "_design_instruct_text"):
                self._design_instruct_text.delete("1.0", "end")
                if profile.mode == "VoiceDesign":
                    self._design_instruct_text.insert("end", profile.instruct or "")
            self._on_mode_change()
        except Exception as exc:
            self._log(f"Load profile error: {exc}", "error")

    def _profile_new(self):
        self._pe_name_var.set("")
        self._pe_lang_var.set("English")
        self._pe_mode_var.set("CustomVoice")
        self._pe_model_var.set(config.TTS_RECOMMENDED_MODELS.get("CustomVoice","1.7B-CustomVoice"))
        self._pe_speaker_var.set(config.TTS_PRESET_SPEAKERS[0] if config.TTS_PRESET_SPEAKERS else "Aiden")
        self._pe_instruct_var.set("")
        self._pe_refwav_var.set("")
        self._pe_reftext_var.set("")
        self._pe_xvec_var.set(True)
        self._pe_temp_var.set("0.7")
        self._pe_topp_var.set("1.0")
        self._pe_topk_var.set("50")
        self._pe_rep_var.set("1.1")
        self._pe_maxt_var.set("2048")
        if hasattr(self, "_design_instruct_text"):
            self._design_instruct_text.delete("1.0", "end")
        if hasattr(self, "_pe_preview_lbl") and self._pe_preview_lbl:
            self._pe_preview_lbl.config(text="")
        self._on_mode_change()

    def _profile_delete(self):
        sel = self._profile_tree.selection()
        if not sel:
            self._log("Select a profile to delete first", "warning")
            return
        name = sel[0]
        if not messagebox.askyesno("Delete Profile",
                                    f"Permanently delete profile '{name}'?",
                                    parent=self):
            return
        try:
            from tts.voice_profile import VoiceProfileManager
            VoiceProfileManager(str(config.VOICES_DIR)).delete(name)
            self._log(f"Profile '{name}' deleted ✓", "success")
            self._refresh_profile_list()
            self._profile_new()
        except Exception as exc:
            self._log(f"Delete failed: {exc}", "error")

    def _build_profile_editor(self, parent: tk.Frame):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        vsb    = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win   = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_resize(e):
            canvas.itemconfig(win, width=e.width)

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_canvas_resize)

        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._build_editor_inner(inner)

    def _build_editor_inner(self, inner: tk.Frame):
        pad = dict(padx=16)

        tk.Label(inner, text="VOICE PROFILE EDITOR", font=FL, bg=BG, fg=ACCENT
                 ).pack(anchor="w", pady=(12, 6), **pad)

        row = tk.Frame(inner, bg=BG); row.pack(fill="x", pady=(0, 4), **pad)
        tk.Label(row, text="Profile name:", font=FS, bg=BG, fg=TEXT_DIM,
                 width=16, anchor="w").pack(side="left")
        _entry(row, self._pe_name_var, width=28).pack(side="left", padx=(0, 6))
        tk.Label(row, text="e.g. Adam_en", font=FS, bg=BG, fg=MUTED).pack(side="left")

        row2 = tk.Frame(inner, bg=BG); row2.pack(fill="x", pady=(0, 8), **pad)
        tk.Label(row2, text="Language:", font=FS, bg=BG, fg=TEXT_DIM,
                 width=16, anchor="w").pack(side="left")
        _option_menu(row2, self._pe_lang_var, config.TTS_LANGUAGES).pack(side="left")

        _div(inner)

        tk.Label(inner, text="VOICE MODE", font=FL, bg=BG, fg=ACCENT
                 ).pack(anchor="w", pady=(0, 6), **pad)

        mode_row = tk.Frame(inner, bg=BG); mode_row.pack(fill="x", **pad, pady=(0, 8))
        for val, lbl, desc in [
            ("CustomVoice", "CUSTOM VOICE",  "Preset speaker identity"),
            ("VoiceDesign", "VOICE DESIGN",  "Describe the voice you want"),
            ("VoiceClone",  "VOICE CLONE",   "Clone from reference audio"),
        ]:
            col = tk.Frame(mode_row, bg=BG)
            col.pack(side="left", padx=(0, 16))
            tk.Radiobutton(col, text=lbl, variable=self._pe_mode_var, value=val,
                           font=(_F, 8, "bold"), bg=BG, fg=TEXT, selectcolor=BG,
                           activebackground=BG, activeforeground=ACCENT,
                           command=self._on_mode_change).pack(anchor="w")
            tk.Label(col, text=desc, font=FS, bg=BG, fg=MUTED).pack(anchor="w")

        mode_content = tk.Frame(inner, bg=BG)
        mode_content.pack(fill="x", **pad, pady=(0, 6))
        self._custom_frame = tk.Frame(mode_content, bg=BG)
        self._design_frame = tk.Frame(mode_content, bg=BG)
        self._clone_frame  = tk.Frame(mode_content, bg=BG)
        self._build_custom_fields(self._custom_frame)
        self._build_design_fields(self._design_frame)
        self._build_clone_fields(self._clone_frame)
        self._custom_frame.pack(fill="x")

        _div(inner)

        row3 = tk.Frame(inner, bg=BG); row3.pack(fill="x", pady=(0, 4), **pad)
        tk.Label(row3, text="Model:", font=FS, bg=BG, fg=TEXT_DIM,
                 width=16, anchor="w").pack(side="left")
        _option_menu(row3, self._pe_model_var, list(config.TTS_MODEL_PATHS.keys())).pack(side="left")
        tk.Label(row3, text="← auto-set with mode change",
                 font=FS, bg=BG, fg=MUTED).pack(side="left", padx=(8, 0))

        adv_toggle_row = tk.Frame(inner, bg=BG)
        adv_toggle_row.pack(fill="x", **pad, pady=(4, 0))
        self._adv_toggle_btn = _btn(adv_toggle_row, "▶  ADVANCED SETTINGS",
                                     self._toggle_advanced, bg=PANEL2, pady=3, padx=8)
        self._adv_toggle_btn.pack(side="left")

        self._pe_adv_frame = tk.Frame(inner, bg=PANEL2,
                                       highlightbackground=BORDER, highlightthickness=1)
        adv_inner = tk.Frame(self._pe_adv_frame, bg=PANEL2)
        adv_inner.pack(fill="x", padx=12, pady=8)
        for label, var, tip in [
            ("Temperature:",        self._pe_temp_var, "0.7 recommended"),
            ("Top-p:",              self._pe_topp_var, "1.0 recommended"),
            ("Top-k:",              self._pe_topk_var, "50 recommended"),
            ("Repetition penalty:", self._pe_rep_var,  "1.1 recommended"),
            ("Max new tokens:",     self._pe_maxt_var, "2048 for most content"),
        ]:
            r = tk.Frame(adv_inner, bg=PANEL2); r.pack(fill="x", pady=2)
            tk.Label(r, text=label, font=FS, bg=PANEL2, fg=TEXT_DIM,
                     width=20, anchor="w").pack(side="left")
            _entry(r, var, width=8).pack(side="left")
            tk.Label(r, text=tip, font=FS, bg=PANEL2, fg=MUTED).pack(side="left", padx=(8, 0))

        _div(inner)

        act_row = tk.Frame(inner, bg=BG); act_row.pack(fill="x", **pad, pady=(4, 4))
        _btn(act_row, "💾  SAVE PROFILE", self._profile_save, bg=ACCENT, fg="#000",
             pady=5, padx=12).pack(side="left", padx=(0, 8))
        _btn(act_row, "▶  PREVIEW VOICE", self._profile_preview, bg=PANEL2,
             pady=5, padx=12).pack(side="left", padx=(0, 8))
        _btn(act_row, "✕  CLEAR", self._profile_new, bg=PANEL2,
             pady=5, padx=12).pack(side="left")

        self._pe_preview_lbl = tk.Label(inner, text="", font=FS, bg=BG, fg=TEXT_DIM)
        self._pe_preview_lbl.pack(anchor="w", **pad, pady=(2, 12))

    def _build_custom_fields(self, parent: tk.Frame):
        row = tk.Frame(parent, bg=BG); row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="Speaker:", font=FS, bg=BG, fg=TEXT_DIM,
                 width=16, anchor="w").pack(side="left")
        _option_menu(row, self._pe_speaker_var, config.TTS_PRESET_SPEAKERS).pack(side="left")
        tk.Label(parent, text="Style note  (optional):", font=FS, bg=BG,
                 fg=TEXT_DIM, anchor="w").pack(anchor="w", pady=(4, 2))
        _entry(parent, self._pe_instruct_var, width=44).pack(anchor="w", pady=(0, 2))
        tk.Label(parent, text='e.g.  "Speak slowly and with gravitas"',
                 font=FS, bg=BG, fg=MUTED).pack(anchor="w")

    def _build_design_fields(self, parent: tk.Frame):
        tk.Label(parent, text="Describe the voice you want:",
                 font=FL, bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0, 4))
        tk.Label(parent,
            text='e.g.  "A deep, calm male narrator with a slight rasp and measured pacing."',
            font=FS, bg=BG, fg=MUTED, wraplength=440, justify="left",
            ).pack(anchor="w", pady=(0, 4))
        self._design_instruct_text = scrolledtext.ScrolledText(
            parent, font=FS, bg=PANEL2, fg=TEXT,
            insertbackground=ACCENT, relief="flat",
            padx=8, pady=8, wrap="word", height=4,
        )
        self._design_instruct_text.pack(fill="x", pady=(0, 2))

    def _build_clone_fields(self, parent: tk.Frame):
        tk.Label(parent, text="Reference audio:", font=FS, bg=BG,
                 fg=TEXT_DIM, anchor="w").pack(anchor="w", pady=(0, 2))
        ref_row = tk.Frame(parent, bg=BG); ref_row.pack(fill="x", pady=(0, 6))
        _entry(ref_row, self._pe_refwav_var, width=38).pack(side="left", padx=(0, 6))
        _btn(ref_row, "BROWSE", self._browse_ref_wav, bg=PANEL2, pady=3, padx=8).pack(side="left")
        tk.Label(parent,
            text="A 5–30s clean recording of the target voice.  WAV/MP3/FLAC all work.",
            font=FS, bg=BG, fg=MUTED, wraplength=440, justify="left",
            ).pack(anchor="w", pady=(0, 8))
        xvec_row = tk.Frame(parent, bg=BG); xvec_row.pack(fill="x", pady=(0, 4))
        tk.Checkbutton(xvec_row, text="x-vector only  (no transcript needed — recommended)",
                       variable=self._pe_xvec_var, font=FS, bg=BG, fg=TEXT,
                       selectcolor=BG, activebackground=BG,
                       command=self._on_xvec_change).pack(side="left")
        self._ref_text_frame = tk.Frame(parent, bg=BG)
        tk.Label(self._ref_text_frame, text="Reference transcript:", font=FS,
                 bg=BG, fg=TEXT_DIM, anchor="w").pack(anchor="w", pady=(0, 2))
        _entry(self._ref_text_frame, self._pe_reftext_var, width=50).pack(anchor="w", pady=(0, 2))
        tk.Label(self._ref_text_frame,
            text="Exact words spoken in the reference audio.  Improves alignment accuracy.",
            font=FS, bg=BG, fg=MUTED, wraplength=440, justify="left",
            ).pack(anchor="w")

    def _on_mode_change(self):
        mode = self._pe_mode_var.get()
        for frame in (self._custom_frame, self._design_frame, self._clone_frame):
            if frame:
                frame.pack_forget()
        target = {"CustomVoice": self._custom_frame,
                  "VoiceDesign": self._design_frame,
                  "VoiceClone":  self._clone_frame}.get(mode)
        if target:
            target.pack(fill="x")
        rec = config.TTS_RECOMMENDED_MODELS.get(mode, "1.7B-CustomVoice")
        self._pe_model_var.set(rec)

    def _on_xvec_change(self):
        if self._pe_xvec_var.get():
            self._ref_text_frame.pack_forget()
        else:
            self._ref_text_frame.pack(fill="x", pady=(4, 0))

    def _toggle_advanced(self):
        self._pe_adv_shown = not self._pe_adv_shown
        if self._pe_adv_shown:
            self._pe_adv_frame.pack(fill="x", padx=16, pady=(4, 0))
            self._adv_toggle_btn.configure(text="▼  ADVANCED SETTINGS")
        else:
            self._pe_adv_frame.pack_forget()
            self._adv_toggle_btn.configure(text="▶  ADVANCED SETTINGS")

    def _browse_ref_wav(self):
        path = filedialog.askopenfilename(
            title="Select reference audio",
            filetypes=[("Audio files","*.wav *.mp3 *.flac *.ogg *.m4a"),
                       ("All files","*")],
            parent=self,
        )
        if path:
            self._pe_refwav_var.set(path)

    def _build_profile_from_form(self):
        from tts.voice_profile import VoiceProfile
        name = self._pe_name_var.get().strip()
        if not name:
            return None, "Profile name is required"
        p               = VoiceProfile(name=name)
        p.language      = self._pe_lang_var.get()
        p.mode          = self._pe_mode_var.get()
        p.model         = self._pe_model_var.get()
        p.speaker       = self._pe_speaker_var.get()
        p.x_vector_only = self._pe_xvec_var.get()
        p.ref_wav_path  = self._pe_refwav_var.get().strip()
        p.ref_wav_text  = self._pe_reftext_var.get().strip()
        if p.mode == "VoiceDesign" and hasattr(self, "_design_instruct_text"):
            p.instruct = self._design_instruct_text.get("1.0", "end").strip()
        else:
            p.instruct = self._pe_instruct_var.get().strip()
        try:
            p.temperature        = float(self._pe_temp_var.get())
            p.top_p              = float(self._pe_topp_var.get())
            p.top_k              = int(self._pe_topk_var.get())
            p.repetition_penalty = float(self._pe_rep_var.get())
            p.max_new_tokens     = int(self._pe_maxt_var.get())
        except ValueError as exc:
            return None, f"Invalid advanced setting: {exc}"
        if p.mode == "VoiceClone" and not p.ref_wav_path:
            return None, "VoiceClone requires a reference audio file"
        if p.mode == "VoiceDesign" and not p.instruct.strip():
            return None, "VoiceDesign requires a voice description"
        return p, None

    def _profile_save(self):
        profile, err = self._build_profile_from_form()
        if err:
            self._log(err, "error")
            return
        try:
            from tts.voice_profile import VoiceProfileManager
            VoiceProfileManager(str(config.VOICES_DIR)).save(profile)
            self._log(f"Profile '{profile.name}' saved ✓", "success")
            self._refresh_profile_list()
            if hasattr(self, "_pe_preview_lbl") and self._pe_preview_lbl:
                self._pe_preview_lbl.config(
                    text=f"Saved ✓  {profile.mode}  {profile.language}", fg=SUCCESS)
        except Exception as exc:
            self._log(f"Save failed: {exc}", "error")

    def _profile_preview(self):
        profile, err = self._build_profile_from_form()
        if err:
            self._log(err, "error")
            return
        if profile.mode == "VoiceClone" and not Path(profile.ref_wav_path).exists():
            self._log("Reference audio file not found", "error")
            return
        if self._active_thread and self._active_thread.is_alive():
            self._log("Already running — wait or press Stop", "warning")
            return

        sample_text = {
            "Chinese":  "你好，这是声音预览。",
            "Japanese": "こんにちは、これは音声プレビューです。",
            "Korean":   "안녕하세요, 이것은 음성 미리보기입니다.",
        }.get(profile.language, "Hello, this is a voice preview. How does this sound?")

        if self._pe_preview_lbl:
            self._pe_preview_lbl.config(text="Generating preview … (may take 30s)", fg=WARNING)

        def _bg():
            try:
                from dub_engine import DubEngine
                tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                engine = DubEngine(self.db, on_log=self._log)
                ok = engine._generate_batch_wav(
                    texts         = [sample_text],
                    lang_code     = "en",
                    voice_profile = profile,
                    out_path      = tmp_wav,
                    sentences_dir = Path(tmp_wav).parent / "_preview_sentences",
                    state_file    = Path(tmp_wav).parent / "_preview_state.json",
                    on_log        = self._log,
                )
                if ok and Path(tmp_wav).exists():
                    self.after(0, lambda: self._pe_preview_lbl.config(text="▶ Playing preview …", fg=INFO))
                    self._play_audio(tmp_wav)
                    self.after(0, lambda: self._pe_preview_lbl.config(text="Preview done ✓", fg=SUCCESS))
                else:
                    self.after(0, lambda: self._pe_preview_lbl.config(text="Preview failed — check logs", fg=ERROR))
            except Exception as exc:
                msg = f"Preview error: {exc}"
                self.after(0, lambda m=msg: self._pe_preview_lbl.config(text=m, fg=ERROR))

        self._active_thread = threading.Thread(target=_bg, daemon=True, name="preview")
        self._active_thread.start()

    @staticmethod
    def _play_audio(wav_path: str):
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", wav_path])
            elif sys.platform == "win32":
                os.startfile(wav_path)
            else:
                subprocess.Popen(["xdg-open", wav_path])
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # DUBBING TAB (phases 2-5, panel audio table)
    # ══════════════════════════════════════════════════════════════════════════

    def _build_dubbing_tab(self, parent: tk.Frame):
        ctrl = tk.Frame(parent, bg=PANEL, pady=8)
        ctrl.pack(fill="x")
        for label, cmd, col in (
            ("2: GEN BATCHES",    self._run_phase2, ACCENT),
            ("3: ALIGN & SPLIT",  self._run_phase3, "#2a3a2a"),
            ("4: SYNC TO EN",     self._run_phase4, "#2a2a3a"),
            ("5: STITCH FINAL",   self._run_phase5, "#3a2a2a"),
        ):
            _btn(ctrl, label, cmd, bg=col, fg="#fff", pady=5, padx=10
                 ).pack(side="left", padx=4)
        _btn(ctrl, "⏹ STOP", self.stop, fg=ERROR, pady=5, padx=10
             ).pack(side="right", padx=8)

        target_row = tk.Frame(parent, bg=BG, pady=4)
        target_row.pack(fill="x", padx=8)
        tk.Label(target_row, text="Apply phase to:", font=FS, bg=BG, fg=MUTED
                 ).pack(side="left", padx=(0, 6))
        for val, label in (("all","ALL LANGUAGES"), ("sel","SELECTED LANGUAGE")):
            tk.Radiobutton(target_row, text=label, variable=self._phase_lang_var,
                           value=val, font=FS, bg=BG, fg=TEXT_DIM,
                           activebackground=BG, selectcolor=BTN_BG,
                           highlightthickness=0, cursor="hand2",
                           ).pack(side="left", padx=4)

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(4, 0))
        ttk.Progressbar(parent, variable=self._prog_var, maximum=100,
                        mode="determinate",
                        style="Accent.Horizontal.TProgressbar").pack(fill="x")
        self._prog_lbl = tk.Label(parent, text="", font=FS, bg=BG, fg=TEXT_DIM)
        self._prog_lbl.pack(anchor="w", padx=8)
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(0, 4))

        table_hdr = tk.Frame(parent, bg=BG)
        table_hdr.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(table_hdr, text="PANEL AUDIO", font=FL, bg=BG, fg=ACCENT).pack(side="left")
        self._table_lbl = tk.Label(table_hdr, text="", font=FS, bg=BG, fg=TEXT_DIM)
        self._table_lbl.pack(side="left", padx=8)
        _btn(table_hdr, "REGEN SELECTED PANEL", self._regen_selected_panel,
             bg=PANEL2, pady=3, padx=8).pack(side="right")
        _btn(table_hdr, "↺ REFRESH TABLE", self._refresh_audio_table,
             bg=PANEL2, pady=3, padx=8).pack(side="right", padx=4)

        tree_frame = tk.Frame(parent, bg=PANEL2,
                              highlightbackground=BORDER, highlightthickness=1)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        style = ttk.Style()
        style.configure("D.Treeview",
                         background=PANEL2, foreground=TEXT,
                         fieldbackground=PANEL2, rowheight=22, font=FS)
        style.configure("D.Treeview.Heading",
                         background=PANEL, foreground=ACCENT, font=FL, relief="flat")
        style.map("D.Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", TEXT)])
        self._audio_tree = ttk.Treeview(
            tree_frame,
            columns=("#","en_text","lang_text","duration","status"),
            show="headings", style="D.Treeview", selectmode="browse")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._audio_tree.yview)
        self._audio_tree.configure(yscrollcommand=sb.set)
        self._audio_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for col, heading, width, anchor in (
            ("#",        "#",        45,  "center"),
            ("en_text",  "EN TEXT",  220, "w"),
            ("lang_text","LANG TEXT",220, "w"),
            ("duration", "DUR",       60, "center"),
            ("status",   "STATUS",    60, "center"),
        ):
            self._audio_tree.heading(col, text=heading)
            self._audio_tree.column(col, width=width, anchor=anchor)
        self._audio_tree.tag_configure("done",    foreground=SUCCESS)
        self._audio_tree.tag_configure("pending", foreground=MUTED)
        self._audio_tree.tag_configure("error",   foreground=ERROR)

    # ══════════════════════════════════════════════════════════════════════════
    # EPISODE MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_episode_menu(self):
        try:
            projects = self.db.list_projects()
            all_eps  = []
            for p in projects:
                for ep in self.db.list_episodes(p["id"]):
                    label = f"{p.get('name', p.get('title','?'))}  —  {ep.get('title') or ep.get('name','?')}"
                    self._ep_map[label] = ep["id"]
                    all_eps.append(label)
        except Exception:
            all_eps = []
        menu = self._ep_menu["menu"]
        menu.delete(0, "end")
        menu.add_command(label="— none —", command=lambda: self._ep_var.set("— none —"))
        for label in all_eps:
            menu.add_command(label=label, command=lambda v=label: self._ep_var.set(v))

    def _on_load_click(self):
        label = self._ep_var.get()
        eid   = self._ep_map.get(label)
        if not eid:
            self._log("Select an episode first", "warning")
            return
        self._episode_id = eid
        self._episode    = self.db.get_episode(eid)
        self._on_load_episode()

    def _on_load_episode(self):
        if not self._episode:
            return
        ep   = self._episode
        src  = ep.get("source_type", "").upper()
        name = ep.get("title") or ep.get("name", "")
        self._ep_info_lbl.config(text=f"[{src}]  {name}")
        self._refresh_lang_list()
        self._refresh_audio_table()
        self._log(f"Loaded episode: {name}", "info")

    # ══════════════════════════════════════════════════════════════════════════
    # LANGUAGE MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_lang_list(self):
        for w in self._lang_inner.winfo_children():
            w.destroy()
        self._lang_rows.clear()
        if not self._active_langs:
            tk.Label(self._lang_inner, text="No languages added yet.",
                     font=FS, bg=PANEL2, fg=MUTED).pack(pady=12)
            return
        for code in self._active_langs:
            name = config.SUPPORTED_LANGUAGES.get(code, code.upper())
            p2   = self._check_phase2_done(code)
            p3   = self._check_phase3_done(code)
            p4   = self._check_phase4_done(code)
            row  = _LangRow(self._lang_inner, code, name, p2, p3, p4,
                            on_click=self._on_lang_select)
            row.pack(fill="x", padx=2, pady=1)
            self._lang_rows[code] = row
        sel = self._selected_lang.get()
        if sel in self._lang_rows:
            self._lang_rows[sel].set_selected(True)

    def _on_lang_select(self, code: str):
        self._selected_lang.set(code)
        for c, row in self._lang_rows.items():
            row.set_selected(c == code)
        self._refresh_audio_table()

    def _add_languages(self):
        dlg = _AddLangDialog(self, self._active_langs)
        if dlg.result:
            for code in dlg.result:
                if code not in self._active_langs:
                    self._active_langs.append(code)
            self._refresh_lang_list()
            self._log(f"Added: {', '.join(dlg.result)}", "success")

    def _remove_language(self):
        code = self._selected_lang.get()
        if code not in self._active_langs:
            self._log("Select a language to remove", "warning")
            return
        name = config.SUPPORTED_LANGUAGES.get(code, code)
        if not messagebox.askyesno("Remove Language",
                                    f"Remove '{name}' from this session?",
                                    parent=self):
            return
        self._active_langs.remove(code)
        self._selected_lang.set(self._active_langs[0] if self._active_langs else "")
        self._refresh_lang_list()
        self._refresh_audio_table()

    def _check_phase2_done(self, code):
        if not self._episode:
            return False
        f = Path(self._episode["output_folder"]) / "dub" / code / "_continuous.wav"
        return f.exists()

    def _check_phase3_done(self, code):
        if not self._episode:
            return False
        folder = Path(self._episode["output_folder"]) / "dub" / code
        return bool(list(folder.glob("panel_*.wav"))) if folder.exists() else False

    def _check_phase4_done(self, code):
        if not self._episode:
            return False
        folder = Path(self._episode["output_folder"]) / "dub" / code
        return bool(list(folder.glob("panel_*_sync.wav"))) if folder.exists() else False

    # ══════════════════════════════════════════════════════════════════════════
    # ACTIVE DUBBING PROFILE
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_active_profile_menu(self):
        try:
            from tts.voice_profile import VoiceProfileManager
            profiles = VoiceProfileManager(str(config.VOICES_DIR)).list_profiles()
        except Exception:
            profiles = []
        menu = self._profile_menu["menu"]
        menu.delete(0, "end")
        if not profiles:
            menu.add_command(label="— none —", command=lambda: self._profile_var.set(""))
            self._profile_var.set("")
            return
        for p in profiles:
            menu.add_command(label=p, command=lambda v=p: self._profile_var.set(v))
        if not self._profile_var.get() and profiles:
            self._profile_var.set(profiles[0])

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL AUDIO TABLE
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_audio_table(self):
        tv = self._audio_tree
        tv.delete(*tv.get_children())
        if not self._episode_id:
            return
        lang    = self._selected_lang.get() or "en"
        panels  = sorted(self.db.list_panels(self._episode_id),
                         key=lambda p: p["panel_index"])
        n_done  = 0
        for panel in panels:
            idx       = panel["panel_index"]
            en_text   = (panel.get("narration_text") or "")[:60]
            if lang == "en":
                lang_text = en_text
            else:
                row       = self.db.get_panel_audio(panel["id"], lang)
                lang_text = ((row or {}).get("translated_text") or "")[:60]
            audio_row = self.db.get_panel_audio(panel["id"], lang)
            wav       = (audio_row or {}).get("audio_segment_path") if audio_row else None
            has_audio = bool(wav and Path(wav).exists())
            if has_audio:
                from core.audio_utils import get_wav_duration
                dur   = get_wav_duration(wav)
                dur_s = f"{dur:.1f}s"
                status= "✓"; tag = "done"; n_done += 1
            else:
                dur_s = ""; status = "○"; tag = "pending"
            tv.insert("", "end",
                       values=(idx + 1, en_text, lang_text, dur_s, status),
                       tags=(tag,), iid=str(idx))
        lang_name = config.SUPPORTED_LANGUAGES.get(lang, lang)
        self._table_lbl.config(
            text=f"{lang_name}  ·  {n_done}/{len(panels)} panels with audio")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE RUNNERS
    # ══════════════════════════════════════════════════════════════════════════

    def _get_target_langs(self):
        if self._phase_lang_var.get() == "sel":
            sel = self._selected_lang.get()
            return [sel] if sel else []
        return list(self._active_langs)

    def _run_phase2(self):
        langs = self._get_target_langs()
        if not langs:
            self._log("Add and select a language first", "warning"); return
        self._start_thread(self._bg_phase2, langs)

    def _run_phase3(self):
        langs = self._get_target_langs()
        if not langs:
            self._log("Add languages first", "warning"); return
        self._start_thread(self._bg_phase3, langs)

    def _run_phase4(self):
        langs = [l for l in self._get_target_langs() if l != "en"]
        if not langs:
            self._log("No non-English languages to sync", "warning"); return
        self._start_thread(self._bg_phase4, langs)

    def _run_phase5(self):
        lang = self._selected_lang.get()
        if not lang:
            self._log("Select a language first", "warning"); return
        self._start_thread(self._bg_phase5, [lang])

    def _bg_phase2(self, langs):
        """
        Phase 2 — batch TTS generation.
        Uses generate_all_batches() (same method as pipeline_tab) for consistency.
        """
        from dub_engine import DubEngine
        from tts.voice_profile import VoiceProfileManager

        engine       = DubEngine(self.db, on_log=self._log)
        self._active_engine = engine
        profile_name = self._profile_var.get()
        if not profile_name:
            self._log("Select an active dubbing profile first", "error"); return

        vpm     = VoiceProfileManager(str(config.VOICES_DIR))
        profile = vpm.load(profile_name)
        if not profile:
            self._log(f"Profile '{profile_name}' not found", "error"); return

        try:
            batch_size = max(1, int(self.db.get_setting("dub_batch_size", "5")))
        except Exception:
            batch_size = 5

        n = len(langs)
        for i, lc in enumerate(langs):
            if self._stop_flag: break
            self._update_prog(int(i / n * 100), f"Generating {lc} …")
            ok = engine.generate_all_batches(
                self._episode_id, lc, profile, batch_size,
                on_log=self._log, on_progress=self._on_phase_progress)
            if not ok:
                self._log(f"Phase 2 failed for '{lc}'", "error")
        self._after_phase("Phase 2 complete ✓")

    def _bg_phase3(self, langs):
        from dub_engine import DubEngine
        engine = DubEngine(self.db, on_log=self._log)
        self._active_engine = engine
        engine.align_and_split_all(self._episode_id, langs,
                                   on_log=self._log,
                                   on_progress=self._on_phase_progress)
        self._after_phase("Phase 3 complete ✓")

    def _bg_phase4(self, langs):
        from dub_engine import DubEngine
        engine = DubEngine(self.db, on_log=self._log)
        self._active_engine = engine
        for lc in langs:
            if self._stop_flag: break
            engine.sync_to_english(self._episode_id, lc,
                                   on_log=self._log,
                                   on_progress=self._on_phase_progress)
        self._after_phase("Phase 4 complete ✓")

    def _bg_phase5(self, langs):
        from dub_engine import DubEngine
        engine = DubEngine(self.db, on_log=self._log)
        self._active_engine = engine
        for lc in langs:
            if self._stop_flag: break
            path = engine.stitch_final(self._episode_id, lc, on_log=self._log)
            if path:
                self._log(f"Final audio → {Path(path).name}", "success")
        self._after_phase("Phase 5 complete ✓")

    def _after_phase(self, msg):
        def _u():
            self._log(msg, "success")
            self._update_prog(100, "Done")
            self._refresh_lang_list()
            self._refresh_audio_table()
            self._set_busy(False)
        self.after(0, _u)

    def _regen_selected_panel(self):
        sel  = self._audio_tree.selection()
        if not sel:
            self._log("Select a panel row to regenerate", "warning"); return
        panel_index  = int(sel[0])
        lang         = self._selected_lang.get()
        profile_name = self._profile_var.get()
        if not lang:
            self._log("Select a language first", "warning"); return
        if not profile_name:
            self._log("Select an active dubbing profile first", "warning"); return

        def _bg():
            from dub_engine import DubEngine
            from tts.voice_profile import VoiceProfileManager
            vpm     = VoiceProfileManager(str(config.VOICES_DIR))
            profile = vpm.load(profile_name)
            if not profile:
                self._log(f"Profile '{profile_name}' not found", "error"); return
            engine = DubEngine(self.db, on_log=self._log)
            self._active_engine = engine
            ok = engine.regenerate_segment(
                self._episode_id, lang, panel_index, profile, on_log=self._log)
            self.after(0, lambda: (
                self._refresh_audio_table(),
                self._refresh_lang_list(),
                self._set_busy(False),
            ))
            if ok:
                self._log(f"Panel {panel_index + 1} regenerated ✓", "success")
        self._start_thread(_bg, None)

    # ══════════════════════════════════════════════════════════════════════════
    # THREAD + PROGRESS
    # ══════════════════════════════════════════════════════════════════════════

    def _start_thread(self, target_fn, langs):
        if self._active_thread and self._active_thread.is_alive():
            self._log("Already running — wait or press Stop", "warning"); return
        self._stop_flag = False
        self._set_busy(True)
        self._update_prog(0, "Starting …")

        def _wrapped():
            try:
                if langs is None:
                    target_fn()
                else:
                    target_fn(langs)
            except Exception as exc:
                self._log(f"Phase error: {exc}", "error")
                self.after(0, lambda: self._set_busy(False))

        self._active_thread = threading.Thread(target=_wrapped, daemon=True)
        self._active_thread.start()

    def _on_phase_progress(self, current, total):
        if total <= 0: return
        pct = int(current / total * 100)
        self.after(0, lambda: self._update_prog(pct, f"{current}/{total}"))

    def _update_prog(self, pct, msg):
        def _u():
            self._prog_var.set(pct)
            self._prog_lbl.config(text=msg)
        self.after(0, _u)

    def _set_busy(self, busy): pass

    def _log(self, msg, level="info"):
        self._on_log(msg, level)
