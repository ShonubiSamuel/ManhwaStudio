"""
pipeline_tab.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Pipeline tab — orchestration only.

All stage-specific build/load/runner code lives in ui/stages/.
This file contains only:
  - Episode loading and state
  - Paned layout scaffold
  - Stage sidebar with Run All / Stop / progress
  - Stage dispatch (_on_stage_click routes to stage module)
  - Thread management (_run_single, _run_all)
  - Stage completion handling (_on_stage_done)
  - Screenshots panel manager
"""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, ttk
import tkinter as tk
from typing import Callable, Dict, Optional

import config
from pipeline_logic import reset_episode_from_stage, clear_episode_downstream
from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, BTN_FG, SEL_BG,
    _F, FL, FB, FS, FBTN,
)
from ui.widgets import (
    _FlatBtn, _btn, _sec, _div,
    _StageRow, _InsertAtDialog,
)
from ui.stages import STAGE_MODULES


# ── Stage definitions ──────────────────────────────────────────────────────────

_VIDEO_STAGES = [
    ("detect",           "DETECT",     "Detect panel cuts from audio + visual signals"),
    ("video_refine",     "REFINE",     "Transcribe + AI-refine transcript with tone style"),
    ("video_screenshot", "SCREENSHOT", "Grab one representative frame per panel"),
    ("translate",        "TRANSLATE",  "Translate to all selected languages"),
    ("dub",              "DUBBING",    "Generate + align dubbed audio in batches"),
    ("sync",             "SYNC",       "Sync dubbed audio to English panel timing"),
    ("assemble",         "ASSEMBLE",   "Build final dubbed video"),
]

_PDF_STAGES = [
    ("pdf_slice",   "SLICE",     "Slice PDF pages + downscale for AI narration"),
    ("pdf_narrate", "NARRATE",   "Generate narration via AI (auto or manual)"),
    ("translate",   "TRANSLATE", "Apply tone + translate to all languages"),
    ("dub",         "DUBBING",   "Generate + align dubbed audio per language"),
    ("assemble",    "ASSEMBLE",  "Build final dubbed video"),
]

_SCREENSHOTS_STAGES = [
    ("upscale",   "UPSCALE",   "4× upscale panel screenshots (Real-ESRGAN)"),
    ("translate", "TRANSLATE", "Apply tone + translate to all languages"),
    ("dub",       "DUBBING",   "Generate + align dubbed audio per language"),
    ("assemble",  "ASSEMBLE",  "Build final dubbed video"),
]


# ── Pipeline tab ───────────────────────────────────────────────────────────────

class PipelineTab(tk.Frame):
    def __init__(self, parent, db, on_log: Callable):
        super().__init__(parent, bg=BG)
        self.db      = db
        self._on_log = on_log

        # Episode state
        self._episode_id:  Optional[int]  = None
        self._episode:     Optional[dict] = None
        self._stage_defs:  list           = []

        # UI refs
        self._stage_rows:     Dict[str, _StageRow] = {}
        self._content_frames: Dict[str, tk.Frame]  = {}
        self._built_stages:   set                  = set()
        self._current_stage:  Optional[str]        = None

        # Thread / engine
        self._active_engine = None
        self._active_thread: Optional[threading.Thread] = None
        self._stop_flag      = False

        # Progress widgets
        self._run_all_btn = None
        self._stop_btn    = None
        self._prog_var    = tk.IntVar(value=0)
        self._prog_lbl    = None

        self._build_empty_state()

    def _db_key(self, ui_key: str) -> str:
        """Map modular UI stage keys to actual database columns."""
        mapping = {
            "video_refine":     "extract",
            "pdf_slice":        "extract",
            "video_screenshot": "screenshot",
            "pdf_narrate":      "narrate",
            "upscale":          "upscale"
        }
        return mapping.get(ui_key, ui_key)
    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    def load_episode(self, episode_id: int):
        self._episode_id = episode_id
        self._episode    = self.db.get_episode(episode_id)
        if not self._episode:
            self._log("Episode not found in database", "error")
            return

        src = self._episode.get("source_type", "video").lower()
        if src == "video":
            self._stage_defs = _VIDEO_STAGES
        elif src == "screenshots":
            self._stage_defs = _SCREENSHOTS_STAGES
        else:
            self._stage_defs = _PDF_STAGES

        for w in self.winfo_children():
            w.destroy()
        self._stage_rows.clear()
        self._content_frames.clear()
        self._built_stages.clear()
        self._current_stage = None

        if src == "screenshots":
            self._build_screenshots_panel_manager()
        else:
            self._build_all()
            self._refresh_all_statuses()
            if self._stage_defs:
                self._on_stage_click(self._stage_defs[0][0])

        self._log(
            f"Loaded: {self._episode.get('title') or self._episode.get('name', '?')}  "
            f"[{src.upper()}]",
            "accent",
        )

    def stop(self):
        self._stop_flag = True
        if self._active_engine:
            try: self._active_engine.stop()
            except Exception: pass

    # ══════════════════════════════════════════════════════════════════════════
    # SCAFFOLD
    # ══════════════════════════════════════════════════════════════════════════

    def _build_empty_state(self):
        tk.Label(
            self, text="Open an episode from the Library tab to begin.",
            font=FB, bg=BG, fg=MUTED, justify="center",
        ).place(relx=0.5, rely=0.5, anchor="center")

    def _build_all(self):
        self._build_header()
        _div(self)

        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=5, sashrelief="flat", sashpad=0)
        pw.pack(fill="both", expand=True)

        left  = tk.Frame(pw, bg=BG, width=280)
        right = tk.Frame(pw, bg=BG)
        pw.add(left,  minsize=220)
        pw.add(right, minsize=420)

        self._content_parent = right
        self._build_sidebar(left)

    def _build_header(self):
        h   = tk.Frame(self, bg=BG, pady=10)
        h.pack(fill="x", padx=16)
        ep  = self._episode or {}
        src = ep.get("source_type", "video").lower()

        if src == "video":
            badge_bg, badge_fg, badge_tx = "#1e2a1e", SUCCESS,  "▶ VIDEO"
        elif src == "screenshots":
            badge_bg, badge_fg, badge_tx = "#1a2a1a", "#a3e635","📸 SHOTS"
        else:
            badge_bg, badge_fg, badge_tx = "#1a1a2e", INFO,     "▸ PDF"

        tk.Label(h, text=badge_tx, font=(_F, 8, "bold"),
                 bg=badge_bg, fg=badge_fg, padx=6, pady=2
                 ).pack(side="left", padx=(0, 10))
        tk.Label(h, text=ep.get("title") or ep.get("name", "—"),
                 font=(_F, 11, "bold"), bg=BG, fg=TEXT
                 ).pack(side="left")

    def _build_sidebar(self, parent: tk.Frame):
        _sec(parent, "PIPELINE STAGES")

        for key, label, desc in self._stage_defs:
            row = _StageRow(parent, key, label, desc,
                            on_click=self._on_stage_click)
            row.pack(fill="x", padx=0, pady=1)
            self._stage_rows[key] = row

        _div(parent)

        ctrl = tk.Frame(parent, bg=BG)
        ctrl.pack(fill="x", pady=2)
        self._run_all_btn = _btn(ctrl, "▶  RUN ALL",
                                 self._run_all, bg=ACCENT, fg="#000")
        self._run_all_btn.pack(fill="x", pady=2)
        self._stop_btn = _btn(ctrl, "⏹  STOP", self.stop, fg=ERROR)
        self._stop_btn.pack(fill="x", pady=2)

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(10, 4))
        ttk.Progressbar(
            parent, variable=self._prog_var, maximum=100,
            mode="determinate", style="Accent.Horizontal.TProgressbar",
        ).pack(fill="x")
        self._prog_lbl = tk.Label(parent, text="", font=FS, bg=BG, fg=TEXT_DIM)
        self._prog_lbl.pack(anchor="w", pady=(2, 0))

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE DISPATCH
    # ══════════════════════════════════════════════════════════════════════════

    def _on_stage_click(self, key: str):
        if key not in self._built_stages:
            frame = tk.Frame(self._content_parent, bg=BG)
            self._content_frames[key] = frame
            mod = STAGE_MODULES.get(key)
            if mod and hasattr(mod, "build"):
                mod.build(frame, key, self)
            else:
                self._build_stage_generic(frame, key)
            self._built_stages.add(key)

        self._show_stage(key)

        mod = STAGE_MODULES.get(key)
        if mod and hasattr(mod, "load"):
            mod.load(self)

    def _show_stage(self, key: str):
        for k, f in self._content_frames.items():
            f.pack(fill="both", expand=True) if k == key else f.pack_forget()
        for k, row in self._stage_rows.items():
            row.set_selected(k == key)
        self._current_stage = key

    def _refresh_all_statuses(self):
        if not self._episode_id: return
        ep = self.db.get_episode(self._episode_id)
        if not ep: return
        self._episode = ep
        for key, _lbl, _desc in self._stage_defs:
            db_key = self._db_key(key)
            status = ep.get(f"stage_{db_key}") or "pending"
            if key in self._stage_rows:
                self._stage_rows[key].set_status(status)

    def _build_stage_generic(self, parent: tk.Frame, key: str):
        self._stage_top_bar(parent, key)

    def _stage_top_bar(self, parent: tk.Frame, key: str) -> tk.Frame:
        bar = tk.Frame(parent, bg=PANEL, pady=8)
        bar.pack(fill="x")
        label = next((l for k, l, _ in self._stage_defs if k == key), key.upper())
        tk.Label(bar, text=label, font=(_F, 11, "bold"),
                 bg=PANEL, fg=TEXT).pack(side="left", padx=12)
        _btn(bar, f"▶  RUN {label}", lambda: self._run_single(key),
             bg=ACCENT2, fg="#fff", pady=4, padx=10).pack(side="right", padx=8)
        return bar

    # ══════════════════════════════════════════════════════════════════════════
    # THREAD MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    def _run_single(self, key: str) -> bool:
        if self._active_thread and self._active_thread.is_alive():
            self._log("A stage is already running — wait or press Stop", "warning")
            return False
        self._stop_flag = False
        self._set_ui_running(True)
        self._update_progress(0, f"Starting {key.upper()} …")

        def _bg():
            ok = False
            try:
                mod = STAGE_MODULES.get(key)
                ok  = mod.runner(self) if mod and hasattr(mod, "runner") else False
            except Exception as exc:
                self._log(f"{key.upper()} failed: {exc}", "error")
            finally:
                self.after(0, lambda: self._on_stage_done(key, ok))

        self._active_thread = threading.Thread(target=_bg, daemon=True, name=f"stage-{key}")
        self._active_thread.start()
        return True

    def _run_all(self):
        if self._active_thread and self._active_thread.is_alive():
            self._log("A stage is already running", "warning")
            return
        self._stop_flag = False
        self._set_ui_running(True)

        def _bg():
            for key, _lbl, _desc in self._stage_defs:
                if self._stop_flag: break
                ep = self.db.get_episode(self._episode_id) or {}
                db_key = self._db_key(key)
                if ep.get(f"stage_{db_key}") == "done": continue
                
                self.after(0, lambda k=key: self._stage_rows.get(k) and self._stage_rows[k].set_status("running"))
                self._log(f"Running: {key.upper()}", "accent")
                self._update_progress(0, f"Starting {key.upper()} …")
                
                ok = False
                try:
                    mod = STAGE_MODULES.get(key)
                    ok  = mod.runner(self) if mod and hasattr(mod, "runner") else False
                except Exception as exc:
                    self._log(f"{key.upper()} error: {exc}", "error")
                
                self.after(0, lambda k=key, s=ok: self._on_stage_done(k, s, quiet=True))
                if not ok:
                    self._log(f"{key.upper()} failed — stopping Run All", "error")
                    break
            self.after(0, lambda: self._set_ui_running(False))

        self._active_thread = threading.Thread(target=_bg, daemon=True, name="run-all")
        self._active_thread.start()

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE COMPLETION
    # ══════════════════════════════════════════════════════════════════════════

    def _on_stage_done(self, key: str, success: bool, quiet: bool = False):
        status = "done" if success else "failed"
        db_key = self._db_key(key)
        try: self.db.set_episode_stage(self._episode_id, db_key, status)
        except Exception: pass

        if key in self._stage_rows:
            self._stage_rows[key].set_status(status)
        
        self._set_ui_running(False)
        self._update_progress(100 if success else 0, "")

        if not quiet:
            level = "success" if success else "error"
            self._log(f"{key.upper()} {'complete ✓' if success else 'failed ✗'}", level)
        self._refresh_all_statuses()

        # Always reload the just-completed stage so it reflects its new state,
        # regardless of whether the user is currently looking at it.
        # The old guard (self._current_stage == key) meant that any stage
        # running in the background while the user viewed a different stage
        # would leave that stage stale until the user navigated away and back.
        self._reload_stages(key)

        # Cross-stage cascades
        if key == "detect" and success and "video_refine" in self._built_stages:
            mod = STAGE_MODULES.get("video_refine")
            if mod: self.after(200, lambda: mod.load(self))

        if key in ["video_refine", "pdf_narrate"] and success:
            self.after(100, self._cascade_wipe_downstream)

    def _set_ui_running(self, running: bool):
        state = "disabled" if running else "normal"
        if self._run_all_btn: self._run_all_btn.config(state=state)

    # ══════════════════════════════════════════════════════════════════════════
    # PROGRESS / LOG / DOWNSTREAM WIPING
    # ══════════════════════════════════════════════════════════════════════════

    def _on_progress(self, current: int, total: int):
        if total <= 0: return
        pct = int(current / total * 100)
        self.after(0, lambda: (
            self._prog_var.set(pct),
            self._prog_lbl and self._prog_lbl.config(text=f"{current}/{total}  {pct}%"),
        ))

    def _update_progress(self, pct: int, msg: str = ""):
        def _u():
            self._prog_var.set(pct)
            if self._prog_lbl: self._prog_lbl.config(text=msg)
        self.after(0, _u)

    def _log(self, msg: str, level: str = "info"):
        self._on_log(msg, level)

    def _reload_stages(self, *keys: str):
        """
        Reload the data display of any already-built stage panels.

        This is the single, canonical call-site for forcing a UI refresh
        after any data mutation — stage completion, cascade wipe, panel
        edit, manual save.  Every place in the codebase that changes
        underlying data should call this so the user never sees a stale
        panel regardless of which stage they are currently viewing.

        Thread-safe: batches all load() calls into a single self.after(0, ...)
        so it is safe to call from background threads without touching
        Tkinter directly.

        Only reloads stages that are already built (clicking a stage builds
        it; unvisited stages have nothing to reload).
        """
        def _do():
            for key in keys:
                if key in self._built_stages:
                    mod = STAGE_MODULES.get(key)
                    if mod and hasattr(mod, "load"):
                        mod.load(self)
        self.after(0, _do)

    def _cascade_wipe_downstream(self):
        if not self._episode_id:
            return

        # Include English — when REFINE reruns, narration_text changes for all
        # panels.  The old English panel_audio entries reference the previous
        # narration_text and must be cleared alongside the other languages so
        # the translate stage shows every language (including English) as
        # pending rather than reverting to the raw transcript fallback.
        all_langs = list(config.SUPPORTED_LANGUAGES.keys())

        reset_episode_from_stage(self.db, self._episode_id, "translate")
        result = clear_episode_downstream(self.db, self._episode_id, all_langs)
        self._wipe_dub_folder()

        # Reload every downstream stage that might already be open.
        # _reload_stages is a no-op for stages the user hasn't visited yet.
        self._reload_stages("translate", "dub", "sync", "assemble")
        self._refresh_all_statuses()

        self._log(
            f"Downstream cleared ✓  —  "
            f"{result['panels_cleared']} panel(s) × "
            f"{result['langs_cleared']} language(s) wiped.  "
            f"TRANSLATE · DUBBING · SYNC reset to pending.",
            "warning",
        )

    def _wipe_dub_folder(self):
        import shutil
        ep = self.db.get_episode(self._episode_id)
        if not ep: return
        dub_dir = Path(ep.get("output_folder", "")) / "dub"
        if not dub_dir.is_dir(): return
        n = 0
        for item in dub_dir.iterdir():
            try:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
                n += 1
            except Exception: pass
        if n: self._log(f"Dub folder wiped — {n} item(s) deleted ✓", "info")


    # ══════════════════════════════════════════════════════════════════════════
    # SCREENSHOTS PANEL MANAGER
    # ══════════════════════════════════════════════════════════════════════════

    _IMG_EXTS = frozenset({".jpg",".jpeg",".png",".webp",".tiff",".tif",".bmp"})

    def _build_screenshots_panel_manager(self):
        ep    = self._episode or {}
        h = tk.Frame(self, bg=BG, pady=10)
        h.pack(fill="x", padx=16)
        tk.Label(h, text="▸ UPSCALE", font=(_F, 8, "bold"),
                 bg="#1a2a1a", fg="#a3e635", padx=6, pady=2
                 ).pack(side="left", padx=(0, 10))
        tk.Label(h, text=ep.get("title") or "—",
                 font=(_F, 11, "bold"), bg=BG, fg=TEXT
                 ).pack(side="left")
        _div(self)

        toolbar = tk.Frame(self, bg=PANEL, pady=7)
        toolbar.pack(fill="x")
        def _tbtn(text, cmd, bg=PANEL2, fg=BTN_FG):
            return _btn(toolbar, text, cmd, bg=bg, fg=fg, pady=4, padx=8)

        _tbtn("➕  ADD PANELS",       lambda: self._spm_add_panels(),      bg=ACCENT, fg="#000").pack(side="left", padx=(8,4))
        _tbtn("📍  ADD AT POSITION",  lambda: self._spm_add_at_position()).pack(side="left", padx=4)
        _tbtn("🔄  REPLACE PANEL",    lambda: self._spm_replace_panel()).pack(side="left", padx=4)
        tk.Frame(toolbar, bg=BORDER, width=1).pack(side="left", fill="y", padx=6, pady=4)
        _tbtn("⬆  UPSCALE SELECTED",  lambda: self._spm_upscale_selected(), bg="#1a2a3a", fg=INFO).pack(side="left", padx=4)
        _tbtn("⬆⬆  UPSCALE ALL",     lambda: self._spm_upscale_all(),      bg="#1a2a3a", fg=INFO).pack(side="left", padx=4)
        tk.Frame(toolbar, bg=BORDER, width=1).pack(side="left", fill="y", padx=6, pady=4)
        _tbtn("🗑  DELETE PANEL",      lambda: self._spm_delete_selected(),  fg=ERROR).pack(side="left", padx=4)
        _tbtn("🗑🗑  DELETE ALL",      lambda: self._spm_delete_all(),       fg=ERROR).pack(side="left", padx=4)

        self._spm_info_lbl = tk.Label(self, text="", font=FS, bg=BG, fg=TEXT_DIM, anchor="w")
        self._spm_info_lbl.pack(fill="x", padx=12, pady=(6, 2))

        tree_frame = tk.Frame(self, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        style = ttk.Style()
        style.configure("SPM.Treeview", background=PANEL2, foreground=TEXT, fieldbackground=PANEL2, rowheight=24, font=FS, borderwidth=0)
        style.configure("SPM.Treeview.Heading", background=PANEL, foreground=ACCENT, font=FL, relief="flat")
        style.map("SPM.Treeview", background=[("selected", SEL_BG)], foreground=[("selected", TEXT)])

        cols = ("#", "filename", "upscaled", "size")
        self._spm_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", style="SPM.Treeview", selectmode="extended")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._spm_tree.yview)
        self._spm_tree.configure(yscrollcommand=sb.set)
        self._spm_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._spm_tree.heading("#", text="#")
        self._spm_tree.heading("filename", text="FILE")
        self._spm_tree.heading("upscaled", text="UPSCALED")
        self._spm_tree.heading("size", text="SIZE")
        self._spm_tree.column("#", width=50, anchor="center", stretch=False)
        self._spm_tree.column("filename", width=420, anchor="w")
        self._spm_tree.column("upscaled", width=90, anchor="center", stretch=False)
        self._spm_tree.column("size", width=80, anchor="center", stretch=False)
        self._spm_tree.bind("<Double-1>", self._spm_reveal_file)

        self._spm_prog_var = tk.IntVar(value=0)
        self._spm_prog_bar = ttk.Progressbar(self, variable=self._spm_prog_var, maximum=100, mode="determinate", style="Accent.Horizontal.TProgressbar")
        self._spm_refresh_grid()

    def _spm_refresh_grid(self):
        if not hasattr(self, "_spm_tree"): return
        self._spm_tree.delete(*self._spm_tree.get_children())
        panels = sorted(self.db.list_panels(self._episode_id), key=lambda p: p["panel_index"])
        n_up = 0
        for p in panels:
            img = p.get("image_path") or ""
            fname = Path(img).name if img else "— missing —"
            up = p.get("upscaled_path")
            up_ok = bool(up and Path(up).exists())
            if up_ok: n_up += 1
            sz = f"{Path(img).stat().st_size // 1024} KB" if img and Path(img).exists() else "—"
            tag = "up" if up_ok else "no"
            self._spm_tree.insert("", "end", iid=str(p["id"]), values=(p["panel_index"], fname, "✓ done" if up_ok else "○ pending", sz), tags=(tag,))
        self._spm_tree.tag_configure("up", foreground=SUCCESS)
        self._spm_tree.tag_configure("no", foreground=TEXT)
        n = len(panels)
        if hasattr(self, "_spm_info_lbl"):
            self._spm_info_lbl.config(text=f"{n} panel(s)  ·  {n_up} upscaled  ·  {n - n_up} pending")

    def _spm_selected_panels(self):
        iids = self._spm_tree.selection() if hasattr(self, "_spm_tree") else []
        result = [self.db.get_panel(int(i)) for i in iids]
        return sorted([r for r in result if r], key=lambda p: p["panel_index"])

    @staticmethod
    def _spm_natural_key(path):
        import re as _re
        return [int(x) if x.isdigit() else x.lower() for x in _re.split(r"(\d+)", path.name)]

    @staticmethod
    def _spm_copy_jpeg(src, dst, quality=95):
        try:
            from PIL import Image as _I
            import shutil as _sh
            if src.suffix.lower() in {".jpg", ".jpeg"}: _sh.copy2(str(src), str(dst))
            else: _I.open(src).convert("RGB").save(str(dst), "JPEG", quality=quality, optimize=True)
            return True
        except Exception: return False

    def _spm_run_bg(self, fn):
        import threading
        self._stop_flag = False
        self._spm_prog_var.set(0)
        self._spm_prog_bar.pack(fill="x", padx=8, pady=(0, 2))
        def _wrap():
            try: fn()
            except Exception as exc: self.after(0, lambda m=f"Error: {exc}": self._log(m, "error"))
            finally:
                self.after(0, self._spm_prog_bar.pack_forget)
                self.after(0, self._spm_refresh_grid)
        threading.Thread(target=_wrap, daemon=True, name="spm-op").start()

    def _spm_add_panels(self):
        from tkinter import messagebox as _mb
        choice = _mb.askyesnocancel("Add Panels", "YES → Select a folder of images\nNO → Select individual files", parent=self)
        if choice is None: return
        if choice:
            src = filedialog.askdirectory(title="Select folder", parent=self)
            if not src: return
            files = sorted([f for f in Path(src).iterdir() if f.is_file() and f.suffix.lower() in self._IMG_EXTS], key=self._spm_natural_key)
        else:
            raw = filedialog.askopenfilenames(title="Select images", filetypes=[("Images","*.jpg *.jpeg *.png *.webp *.tiff *.bmp"), ("All files","*")], parent=self)
            if not raw: return
            files = sorted([Path(f) for f in raw if Path(f).suffix.lower() in self._IMG_EXTS], key=self._spm_natural_key)
        if not files: self._log("No valid image files found", "warning"); return
        self._spm_run_bg(lambda: self._spm_do_add(files))

    def _spm_do_add(self, src_files):
        ep = self.db.get_episode(self._episode_id)
        pf = Path(ep["output_folder"]) / "panels"
        pf.mkdir(parents=True, exist_ok=True)
        existing = self.db.list_panels(self._episode_id)
        next_idx = max((p["panel_index"] for p in existing), default=-1) + 1
        done = 0
        for i, src in enumerate(src_files):
            if self._stop_flag: break
            out = pf / f"panel_{(next_idx+i):04d}.jpg"
            if self._spm_copy_jpeg(src, out):
                self.db.add_panel(self._episode_id, next_idx+i, image_path=str(out))
                done += 1
            self.after(0, lambda v=int((i+1)/len(src_files)*100): self._spm_prog_var.set(v))
        self.db.update_episode(self._episode_id, panels_folder=str(pf))
        self._log(f"{done} panel(s) added ✓", "success")

    def _spm_add_at_position(self):
        panels = self.db.list_panels(self._episode_id)
        dlg = _InsertAtDialog(self, max_index=len(panels))
        if dlg.result is None: return
        target_idx, file_path = dlg.result
        if not Path(file_path).exists(): self._log("File not found", "error"); return
        self._spm_run_bg(lambda: self._spm_do_insert(target_idx, Path(file_path)))

    def _spm_do_insert(self, target_idx, src):
        ep = self.db.get_episode(self._episode_id)
        pf = Path(ep["output_folder"]) / "panels"
        pf.mkdir(parents=True, exist_ok=True)
        panels = sorted(self.db.list_panels(self._episode_id), key=lambda p: p["panel_index"])
        for p in reversed(panels):
            if p["panel_index"] >= target_idx:
                new_idx = p["panel_index"] + 1
                old_file = Path(p["image_path"]) if p.get("image_path") else None
                new_file = pf / f"panel_{new_idx:04d}.jpg"
                if old_file and old_file.exists(): old_file.rename(new_file)
                self.db.update_panel(p["id"], panel_index=new_idx, image_path=str(new_file) if old_file else p.get("image_path"), upscaled_path=None)
        out = pf / f"panel_{target_idx:04d}.jpg"
        if self._spm_copy_jpeg(src, out):
            self.db.add_panel(self._episode_id, target_idx, image_path=str(out))
            self._log(f"Panel inserted at position {target_idx} ✓", "success")
        else: self._log("Failed to copy image", "error")

    def _spm_replace_panel(self):
        sel = self._spm_selected_panels()
        if not sel: self._log("Select a panel to replace first", "warning"); return
        if len(sel) > 1: self._log("Select exactly one panel to replace", "warning"); return
        idx = sel[0]["panel_index"]
        new_file = filedialog.askopenfilename(title=f"Replacement for Panel {idx:04d}", filetypes=[("Images","*.jpg *.jpeg *.png *.webp *.tiff *.bmp"), ("All files","*")], parent=self)
        if not new_file: return
        self._spm_run_bg(lambda: self._spm_do_replace(idx, Path(new_file)))

    def _spm_do_replace(self, panel_index, src):
        try:
            from image_upscaler import ImageUpscaler
            ep = self.db.get_episode(self._episode_id)
            ok = ImageUpscaler(self.db, ep["output_folder"], on_log=self._log).replace_panel(self._episode_id, panel_index, str(src))
            self._log(f"Panel {panel_index:04d} replaced ✓" if ok else "Replace failed", "success" if ok else "error")
        except Exception as exc: self._log(f"Replace error: {exc}", "error")

    def _spm_delete_selected(self):
        sel = self._spm_selected_panels()
        if not sel: self._log("Select panel(s) to delete first", "warning"); return
        from tkinter import messagebox as _mb
        if not _mb.askyesno("Delete", f"Delete {len(sel)} panel(s)? Remaining panels will be re-indexed.", parent=self): return
        self._spm_run_bg(lambda: self._spm_do_delete(sel))

    def _spm_do_delete(self, panels_to_delete):
        ep = self.db.get_episode(self._episode_id)
        pf = Path(ep.get("panels_folder") or Path(ep["output_folder"]) / "panels")
        for p in panels_to_delete:
            img = p.get("image_path")
            if img:
                try: Path(img).unlink(missing_ok=True)
                except Exception: pass
            self.db.delete_panel(p["id"])
        remaining = sorted(self.db.list_panels(self._episode_id), key=lambda p: p["panel_index"])
        for new_idx, p in enumerate(remaining):
            if p["panel_index"] != new_idx:
                old_p = Path(p["image_path"]) if p.get("image_path") else None
                new_p = pf / f"panel_{new_idx:04d}.jpg"
                if old_p and old_p.exists() and old_p != new_p:
                    try: old_p.rename(new_p)
                    except Exception: new_p = old_p
                self.db.update_panel(p["id"], panel_index=new_idx, image_path=str(new_p) if old_p else p.get("image_path"), upscaled_path=None)
        self._log(f"{len(panels_to_delete)} panel(s) deleted, re-indexed ✓", "warning")

    def _spm_delete_all(self):
        panels = self.db.list_panels(self._episode_id)
        if not panels: self._log("No panels to delete", "info"); return
        from tkinter import messagebox as _mb
        if not _mb.askyesno("Delete ALL", f"Delete all {len(panels)} panels? This cannot be undone.", parent=self): return
        self._spm_run_bg(lambda: self._spm_do_delete_all(panels))

    def _spm_do_delete_all(self, panels):
        for p in panels:
            img = p.get("image_path")
            if img:
                try: Path(img).unlink(missing_ok=True)
                except Exception: pass
            self.db.delete_panel(p["id"])
        self._log(f"All {len(panels)} panels deleted ✓", "warning")

    def _spm_upscale_selected(self):
        if not self._spm_selected_panels(): self._log("Select panel(s) to upscale first", "warning"); return
        self._spm_run_bg(lambda: self._spm_do_upscale())

    def _spm_upscale_all(self):
        panels = self.db.list_panels(self._episode_id)
        if not [p for p in panels if not (p.get("upscaled_path") and Path(p["upscaled_path"]).exists())]:
            self._log("All panels already upscaled ✓", "success"); return
        self._spm_run_bg(lambda: self._spm_do_upscale())

    def _spm_do_upscale(self):
        try:
            from image_upscaler import ImageUpscaler
            ep = self.db.get_episode(self._episode_id)
            ok = ImageUpscaler(self.db, ep["output_folder"], on_log=self._log).upscale_panels(
                self._episode_id, on_progress=lambda c, t: self.after(0, lambda v=int(c/t*100) if t else 0: self._spm_prog_var.set(v))
            )
            self._log("Upscale complete ✓" if ok else "Upscale had errors", "success" if ok else "warning")
        except Exception as exc: self._log(f"Upscale error: {exc}", "error")

    def _spm_reveal_file(self, _event):
        sel = self._spm_selected_panels()
        if not sel: return
        img = sel[0].get("image_path")
        if img and Path(img).exists():
            import os, sys
            if sys.platform == "darwin": os.system(f"open -R '{img}'")
            elif sys.platform == "win32": os.system(f'explorer /select,"{img}"')
            else: os.system(f"xdg-open '{Path(img).parent}'")