"""
gui.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Main application window.

Responsibilities
────────────────
  Window lifecycle Init → build → mainloop → graceful close.
                   Ensures DB closes and active engines are stopped before
                   the process exits.

  Tab structure    Hosts five tabs in a ttk.Notebook.  PipelineTab,
                   DubbingTab, and SettingsTab are lazy-loaded the first time
                   their tab is selected — keeps startup fast and avoids
                   importing heavy GPU libraries on launch.

  Shared services  log(msg, level) — thread-safe, routes to the Logs tab
                   and mirrors the last message in the status bar.
                   set_status() / set_activity() — status bar helpers.

  Stats header     Live series / episode / complete counts from the DB.

Tab layout
──────────
  0  LIBRARY    LibraryTab          — project + episode management
  1  PIPELINE   PipelineTab         — run stages for the open episode (lazy)
  2  DUBBING    DubbingTab          — multi-language dub workflow       (lazy)
  3  SETTINGS   SettingsTab         — API keys, TTS, pipeline options   (lazy)
  4  LOGS       LogsTab             — all log output (filtering + search)
"""

from __future__ import annotations

from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk

import config
from database    import Database
from library_tab import LibraryTab
from logs_tab    import LogsTab

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, BTN_FG, SEL_BG,
    _F, FL, FB, FS, FBTN, FLOG, FH1, FH2, FH3,
    LOG_COLORS,
)
from ui.widgets import _FlatBtn, _btn, _sec, _div


# ── Main window ────────────────────────────────────────────────────────────────

class ManhwaStudio(tk.Tk):
    """
    Top-level application window.

    Builds the persistent shell — header, notebook, status bar — then
    instantiates each tab.  Pipeline / Dubbing / Settings are lazy-loaded
    the first time their tab is activated so startup is near-instant and
    optional GPU libraries are not touched until needed.
    """

    # Tab index constants for programmatic selection
    TAB_LIBRARY  = 0
    TAB_PIPELINE = 1
    TAB_DUBBING  = 2
    TAB_SETTINGS = 3
    TAB_LOGS     = 4

    def __init__(self):
        super().__init__()

        # ── Window ────────────────────────────────────────────────────────────
        self.title(f"{config.APP_NAME}  v{config.APP_VERSION}")
        self.geometry("1220x840")
        self.minsize(960, 660)
        self.configure(bg=BG)
        self.resizable(True, True)

        # ── Ensure all runtime directories exist ──────────────────────────────
        for d in (
            config.OUTPUT_DIR,
            config.VOICES_DIR,
            config.LOGS_DIR,
            config.BASE_DIR / "models",
        ):
            d.mkdir(parents=True, exist_ok=True)

        # ── Core service — database ───────────────────────────────────────────
        self.db = Database(str(config.DB_PATH))

        # ── Lazy tab handles (None = not yet built; True = failed import) ─────
        self._pipeline_tab: object = None
        self._dubbing_tab:  object = None
        self._settings_tab: object = None

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_styles()
        self._build_header()
        self._build_notebook()
        self._build_statusbar()

        # ── Global key bindings ───────────────────────────────────────────────
        self.bind("<Control-q>", lambda _e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Initial render + macOS colour-application fix ────────────────────
        self.after(50,  self.update_idletasks)
        self.after(120, self.update_idletasks)
        self.after(200, self._upd_stats)

    # ══════════════════════════════════════════════════════════════════════════
    # STYLES
    # ══════════════════════════════════════════════════════════════════════════

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("default")

        s.configure("TNotebook",
                    background=BG, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab",
                    background=BTN_BG, foreground=TEXT_DIM,
                    font=FL, padding=[18, 7], borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", BG)],
              foreground=[("selected", ACCENT)])

        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=BORDER, background=ACCENT,
                    borderwidth=0, thickness=4)

        s.configure("L.Treeview",
                    background=PANEL2, foreground=TEXT,
                    fieldbackground=PANEL2, rowheight=28,
                    font=FB, borderwidth=0)
        s.configure("L.Treeview.Heading",
                    background=PANEL, foreground=ACCENT,
                    font=FL, relief="flat")
        s.map("L.Treeview",
              background=[("selected", SEL_BG)],
              foreground=[("selected", TEXT)])

        s.configure("TScrollbar",
                    background=BTN_BG, troughcolor=PANEL,
                    borderwidth=0, arrowcolor=MUTED)

    # ══════════════════════════════════════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════════════════════════════════════

    def _build_header(self):
        h = tk.Frame(self, bg=BG, pady=14)
        h.pack(fill="x", padx=24)

        tk.Label(h, text="MANHWA", font=FH1, bg=BG, fg=ACCENT ).pack(side="left")
        tk.Label(h, text="STUDIO",  font=FH2, bg=BG, fg=TEXT  ).pack(side="left", padx=(4, 0))
        tk.Label(h, text="  automation pipeline",
                 font=FH3, bg=BG, fg=MUTED).pack(side="left", pady=(6, 0))

        self._stat_lbl = tk.Label(h, text="", font=FS, bg=BG, fg=TEXT_DIM)
        self._stat_lbl.pack(side="right")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24)

    # ══════════════════════════════════════════════════════════════════════════
    # NOTEBOOK — five tabs
    # ══════════════════════════════════════════════════════════════════════════

    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=24, pady=8)

        # Tab 0: LIBRARY (built eagerly)
        self._lib_tab = LibraryTab(
            self.nb,
            db              = self.db,
            on_open_episode = self._open_episode_in_pipeline,
            on_log          = self.log,
            on_stats_change = self._upd_stats,
        )
        self.nb.add(self._lib_tab, text="  LIBRARY  ")

        # Tab 1: PIPELINE (lazy)
        self._pipeline_frame = tk.Frame(self.nb, bg=BG)
        self.nb.add(self._pipeline_frame, text="  PIPELINE ")

        # Tab 2: DUBBING (lazy)
        self._dubbing_frame = tk.Frame(self.nb, bg=BG)
        self.nb.add(self._dubbing_frame, text="  DUBBING  ")

        # Tab 3: SETTINGS (lazy)
        self._settings_frame = tk.Frame(self.nb, bg=BG)
        self.nb.add(self._settings_frame, text="  SETTINGS ")

        # Tab 4: LOGS — LogsTab is a full tk.Frame with filtering + search
        self._logs_tab = LogsTab(self.nb)
        self.nb.add(self._logs_tab, text="  LOGS     ")

        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    # ══════════════════════════════════════════════════════════════════════════
    # STATUS BAR
    # ══════════════════════════════════════════════════════════════════════════

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL, height=28)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self._sv = tk.StringVar(value="Ready")
        self._sl = tk.Label(
            bar, textvariable=self._sv,
            font=FS, bg=PANEL, fg=SUCCESS, padx=12,
        )
        self._sl.pack(side="left")

        self._av = tk.StringVar(value="")
        tk.Label(
            bar, textvariable=self._av,
            font=FS, bg=PANEL, fg=WARNING, padx=12,
        ).pack(side="right")

    # ══════════════════════════════════════════════════════════════════════════
    # LAZY TAB LOADING
    # ══════════════════════════════════════════════════════════════════════════

    def _on_tab_change(self, _event):
        """Build lazy tabs the first time their tab is selected."""
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:
            return

        if idx == self.TAB_PIPELINE and self._pipeline_tab is None:
            self._load_pipeline_tab()
        elif idx == self.TAB_DUBBING and self._dubbing_tab is None:
            self._load_dubbing_tab()
        elif idx == self.TAB_SETTINGS and self._settings_tab is None:
            self._load_settings_tab()

    def _load_pipeline_tab(self):
        try:
            from pipeline_tab import PipelineTab
            self._pipeline_tab = PipelineTab(
                self._pipeline_frame,
                db     = self.db,
                on_log = self.log,
            )
            self._pipeline_tab.pack(fill="both", expand=True)
            self._pipeline_frame.update_idletasks()
            self.update_idletasks()
        except ImportError:
            _show_placeholder(
                self._pipeline_frame,
                "PIPELINE  —  pipeline_tab.py not yet installed.",
            )
            self._pipeline_tab = True

    def _load_dubbing_tab(self):
        try:
            from dub_tab import DubbingTab
            self._dubbing_tab = DubbingTab(
                self._dubbing_frame,
                db     = self.db,
                on_log = self.log,
            )
            self._dubbing_tab.pack(fill="both", expand=True)
            self._dubbing_frame.update_idletasks()
            self.update_idletasks()
        except ImportError:
            _show_placeholder(
                self._dubbing_frame,
                "DUBBING  —  dub_tab.py not yet installed.",
            )
            self._dubbing_tab = True

    def _load_settings_tab(self):
        try:
            from settings_tab import SettingsTab
            self._settings_tab = SettingsTab(
                self._settings_frame,
                db     = self.db,
                on_log = self.log,
            )
            self._settings_tab.pack(fill="both", expand=True)
            self._settings_frame.update_idletasks()
            self.update_idletasks()
        except ImportError:
            _show_placeholder(
                self._settings_frame,
                "SETTINGS  —  settings_tab.py not yet installed.",
            )
            self._settings_tab = True

    # ══════════════════════════════════════════════════════════════════════════
    # EPISODE ROUTING (called by LibraryTab)
    # ══════════════════════════════════════════════════════════════════════════

    def _open_episode_in_pipeline(self, episode_id: int):
        if self._pipeline_tab is None:
            self._load_pipeline_tab()

        if self._pipeline_tab is not True:
            try:
                self._pipeline_tab.load_episode(episode_id)
            except Exception as exc:
                self.log(f"Pipeline tab error: {exc}", "error")

        self.nb.select(self.TAB_PIPELINE)

        ep = self.db.get_episode(episode_id)
        if ep:
            self.log(
                f"Opened: {ep.get('title') or ep.get('name', '?')}  [{ep['source_type'].upper()}]",
                "accent",
            )

    # ══════════════════════════════════════════════════════════════════════════
    # SHARED LOGGING  (thread-safe)
    # ══════════════════════════════════════════════════════════════════════════

    def log(self, msg: str, level: str = "info"):
        """
        Append a timestamped entry to the Logs tab and mirror the last
        message in the status bar.  Safe to call from any thread.
        LogsTab handles timestamping, colour coding, and thread safety.
        """
        self._logs_tab.log(msg, level)

        def _update_status():
            short = msg[:90] + ("…" if len(msg) > 90 else "")
            self._sv.set(short)
            self._sl.config(fg=LOG_COLORS.get(level, TEXT))

        self.after(0, _update_status)

    def set_status(self, text: str, color: str = SUCCESS):
        """Explicitly set the status bar text and colour from any thread."""
        self.after(0, lambda: (
            self._sv.set(text),
            self._sl.config(fg=color),
        ))

    def set_activity(self, text: str):
        """Update the right-side activity indicator from any thread."""
        self.after(0, lambda: self._av.set(text))

    # ══════════════════════════════════════════════════════════════════════════
    # STATS
    # ══════════════════════════════════════════════════════════════════════════

    def _upd_stats(self):
        """Refresh the header counter from the database."""
        try:
            projects = self.db.list_projects()
            all_eps  = []
            for p in projects:
                all_eps.extend(self.db.list_episodes(p["id"]))

            n_complete = sum(
                1 for e in all_eps
                if e.get("stage_assemble") == "done"
            )
            n_failed = sum(
                1 for e in all_eps
                if any(
                    e.get(f"stage_{s}") == "failed"
                    for s in ("detect", "extract", "translate", "tts", "dub")
                )
            )
            self._stat_lbl.config(
                text=(
                    f"{len(projects)} series  ·  "
                    f"{len(all_eps)} episodes  ·  "
                    f"{n_complete} complete  ·  "
                    f"{n_failed} failed"
                )
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # GRACEFUL CLOSE
    # ══════════════════════════════════════════════════════════════════════════

    def _on_close(self):
        for attr in ("_pipeline_tab", "_dubbing_tab"):
            tab = getattr(self, attr, None)
            if tab not in (None, True):
                try:
                    tab.stop()
                except Exception:
                    pass

        try:
            self.db.close()
        except Exception:
            pass

        self.destroy()


# ── Module helpers ─────────────────────────────────────────────────────────────

def _show_placeholder(parent: tk.Frame, message: str):
    """Centre a muted message in a frame when a tab module is missing."""
    tk.Label(
        parent,
        text       = message,
        font       = FB,
        bg         = BG,
        fg         = MUTED,
        justify    = "center",
        wraplength = 520,
    ).place(relx=0.5, rely=0.5, anchor="center")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ManhwaStudio()
    app.mainloop()
