"""
logs_tab.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Running log of all application activity.

Features
────────
  Timestamped entries  Every line carries [HH:MM:SS] prepended by the caller.
  Colour coding        Each log level (error, warning, success, info, muted)
                       gets a distinct foreground colour.
  Level filter         Toggle buttons hide/show each level instantly.
                       ALL resets to show everything.  Toggles can be combined
                       (e.g. show only ERROR + WARNING).
  Text search          Filter bar hides lines that do not contain the search
                       term (case-insensitive).  Clears with one click.
  Entry counter        Live badge showing total entries and visible count.
  Auto-scroll          New entries scroll to bottom unless paused.
  CLEAR + SAVE LOG     Standard actions always visible in the toolbar.

Architecture
────────────
  All log entries are kept in self._entries (never discarded).
  Filtering rebuilds the text widget from the in-memory list — fast enough
  for tens of thousands of lines.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk
import tkinter as tk
from typing import Callable, List, Optional, Tuple

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, BTN_FG,
    _F, FL, FB, FS, FBTN, FLOG,
)
from ui.widgets import _FlatBtn


# Level → (key, display_label, foreground_colour)
_LEVELS: List[Tuple[str, str, str]] = [
    ("error",   "ERROR",   ERROR),
    ("warning", "WARN",    WARNING),
    ("success", "SUCCESS", SUCCESS),
    ("info",    "INFO",    INFO),
    ("accent",  "ACCENT",  ACCENT),
    ("muted",   "DEBUG",   MUTED),
]

_ALL_LEVEL_KEYS = {k for k, *_ in _LEVELS}


# ── Logs tab ──────────────────────────────────────────────────────────────────

class LogsTab(tk.Frame):
    """
    Full-featured log viewer tab.

    Accepts log entries via .log(msg, level).  Entries are kept in memory
    so they survive filter changes.  Filtering and searching rebuild the
    display widget from the in-memory list.
    """

    def __init__(self, parent, on_save: Optional[Callable] = None, **kw):
        super().__init__(parent, bg=BG, **kw)

        self._entries: List[Tuple[str, str]] = []
        self._visible_levels: set = set(_ALL_LEVEL_KEYS)
        self._search_text     = ""
        self._auto_scroll     = True
        self._on_save         = on_save
        self._level_btns: dict = {}

        self._build()

    # ══════════════════════════════════════════════════════════════════════════
    # BUILD
    # ══════════════════════════════════════════════════════════════════════════

    def _build(self):
        # ── Toolbar row 1: actions + entry counter ────────────────────────────
        bar1 = tk.Frame(self, bg=BG)
        bar1.pack(fill="x", pady=(8, 2))

        self._make_btn(bar1, "CLEAR",    self.clear
                       ).pack(side="left", padx=(0, 6))
        self._make_btn(bar1, "SAVE LOG", self._save_log
                       ).pack(side="left", padx=(0, 16))

        self._auto_scroll_btn = self._make_btn(
            bar1, "AUTO-SCROLL  ON",
            self._toggle_auto_scroll, bg=PANEL2,
        )
        self._auto_scroll_btn.pack(side="left", padx=(0, 6))

        self._count_lbl = tk.Label(bar1, text="", font=FS, bg=BG, fg=MUTED)
        self._count_lbl.pack(side="right", padx=(0, 4))

        # ── Toolbar row 2: level filters + search ────────────────────────────
        bar2 = tk.Frame(self, bg=BG)
        bar2.pack(fill="x", pady=(0, 4))

        tk.Label(bar2, text="SHOW:", font=FL, bg=BG, fg=MUTED
                 ).pack(side="left", padx=(0, 4))

        self._make_btn(bar2, "ALL", self._show_all, bg=BTN_BG
                       ).pack(side="left", padx=1)

        for key, label, color in _LEVELS:
            btn = _FlatBtn(bar2, text=label,
                           command=lambda k=key: self._toggle_level(k),
                           bg=BTN_BG, fg=color, pady=4, padx=8)
            btn.pack(side="left", padx=1)
            self._level_btns[key] = btn

        tk.Label(bar2, text="  SEARCH:", font=FL, bg=BG, fg=MUTED
                 ).pack(side="left", padx=(12, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)

        search_entry = tk.Entry(
            bar2,
            textvariable        = self._search_var,
            font                = FS,
            bg                  = BTN_BG,
            fg                  = TEXT,
            insertbackground    = ACCENT,
            relief              = "flat",
            width               = 24,
            highlightthickness  = 1,
            highlightcolor      = ACCENT,
            highlightbackground = BORDER,
        )
        search_entry.pack(side="left", padx=(0, 4))
        self._make_btn(bar2, "✕", self._clear_search, bg=BTN_BG,
                       pady=3, padx=6).pack(side="left")

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(2, 0))

        # ── Log text widget ───────────────────────────────────────────────────
        self._text = scrolledtext.ScrolledText(
            self,
            font             = FLOG,
            bg               = "#080808",
            fg               = TEXT,
            insertbackground = ACCENT,
            relief           = "flat",
            padx             = 12,
            pady             = 12,
            state            = "disabled",
            wrap             = "word",
        )
        self._text.pack(fill="both", expand=True)

        for key, _label, color in _LEVELS:
            self._text.tag_config(key, foreground=color)
        self._text.tag_config("info", foreground=TEXT)

        self._update_level_button_states()

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    def log(self, msg: str, level: str = "info"):
        """
        Add a log entry.  Thread-safe — wraps the actual write in self.after(0).
        """
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.after(0, lambda: self._append_entry(line, level))

    def clear(self):
        """Erase all entries from memory and the text widget."""
        self._entries.clear()
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        self._update_count()

    def save_to_file(self, path: str):
        """Write all stored entries to a plain-text file."""
        try:
            with open(path, "w", encoding="utf-8") as fh:
                for line, _level in self._entries:
                    fh.write(line)
        except Exception as exc:
            self.log(f"Failed to save log: {exc}", "error")

    # ══════════════════════════════════════════════════════════════════════════
    # ENTRY HANDLING
    # ══════════════════════════════════════════════════════════════════════════

    def _append_entry(self, line: str, level: str):
        self._entries.append((line, level))
        if self._entry_matches_filters(line, level):
            self._text.configure(state="normal")
            self._text.insert("end", line, level)
            if self._auto_scroll:
                self._text.see("end")
            self._text.configure(state="disabled")
        self._update_count()

    def _entry_matches_filters(self, line: str, level: str) -> bool:
        effective = level if level in _ALL_LEVEL_KEYS else "info"
        if effective not in self._visible_levels:
            return False
        if self._search_text and self._search_text not in line.lower():
            return False
        return True

    def _rebuild_display(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        visible = 0
        for line, level in self._entries:
            if self._entry_matches_filters(line, level):
                self._text.insert("end", line, level)
                visible += 1
        if self._auto_scroll:
            self._text.see("end")
        self._text.configure(state="disabled")
        self._update_count(visible)

    # ══════════════════════════════════════════════════════════════════════════
    # FILTER ACTIONS
    # ══════════════════════════════════════════════════════════════════════════

    def _toggle_level(self, key: str):
        if key in self._visible_levels:
            if len(self._visible_levels) == 1:
                return
            self._visible_levels.discard(key)
        else:
            self._visible_levels.add(key)
        self._update_level_button_states()
        self._rebuild_display()

    def _show_all(self):
        self._visible_levels = set(_ALL_LEVEL_KEYS)
        self._update_level_button_states()
        self._rebuild_display()

    def _on_search_change(self, *_args):
        self._search_text = self._search_var.get().lower().strip()
        self._rebuild_display()

    def _clear_search(self):
        self._search_var.set("")

    def _toggle_auto_scroll(self):
        self._auto_scroll = not self._auto_scroll
        label = "AUTO-SCROLL  ON" if self._auto_scroll else "AUTO-SCROLL  OFF"
        color = SUCCESS if self._auto_scroll else MUTED
        self._auto_scroll_btn.config(text=label, fg=color)
        if self._auto_scroll:
            self._text.see("end")

    def _update_level_button_states(self):
        for key, _label, color in _LEVELS:
            btn = self._level_btns.get(key)
            if btn:
                active = key in self._visible_levels
                btn.config(fg=color if active else MUTED)

    # ══════════════════════════════════════════════════════════════════════════
    # TOOLBAR ACTIONS
    # ══════════════════════════════════════════════════════════════════════════

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension = ".txt",
            filetypes        = [("Text file", "*.txt"), ("All files", "*")],
        )
        if path:
            self.save_to_file(path)
            self.log(f"Log saved → {path}", "success")
        elif self._on_save:
            self._on_save()

    # ══════════════════════════════════════════════════════════════════════════
    # COUNTER
    # ══════════════════════════════════════════════════════════════════════════

    def _update_count(self, visible: Optional[int] = None):
        total = len(self._entries)
        if visible is None:
            if self._visible_levels == _ALL_LEVEL_KEYS and not self._search_text:
                visible = total
            else:
                visible = sum(
                    1 for line, level in self._entries
                    if self._entry_matches_filters(line, level)
                )
        if visible == total:
            self._count_lbl.config(text=f"{total} entries")
        else:
            self._count_lbl.config(text=f"{visible} / {total} entries")

    # ══════════════════════════════════════════════════════════════════════════
    # WIDGET HELPER
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _make_btn(parent, text, command, bg=BTN_BG, fg=BTN_FG,
                  pady=5, padx=10):
        return _FlatBtn(parent, text=text, command=command,
                        bg=bg, fg=fg, pady=pady, padx=padx)
