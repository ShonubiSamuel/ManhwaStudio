"""
ui/widgets.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Shared UI components used across every tab in the application.

Previously scattered across (and copy-pasted into) six files:
  gui.py, library_tab.py, pipeline_tab.py,
  dub_tab.py, settings_tab.py, logs_tab.py

Contents
────────
  _FlatBtn            macOS-safe button (tk.Label subclass)
  _btn                Shorthand factory for _FlatBtn
  _sec                Labelled horizontal rule section header
  _div                Thin horizontal divider line
  _scrolled_tree      ttk.Treeview + vertical scrollbar, themed
  _entry              Themed tk.Entry (monospace, dark style)
  _option_menu        Themed tk.OptionMenu
  _StageRow           Clickable pipeline stage row (left sidebar)
  _LangRow            Clickable language status row (dub tab sidebar)
  _EpisodeNameDialog  Modal: ask user for an episode name
  _InsertAtDialog     Modal: ask for panel index + image file

Usage
─────
    from ui.widgets import _FlatBtn, _btn, _sec, _div, _scrolled_tree
    from ui.widgets import _StageRow, _LangRow
    from ui.widgets import _EpisodeNameDialog, _InsertAtDialog
"""

from __future__ import annotations

from tkinter import filedialog, ttk
import tkinter as tk
from typing import Callable, List, Optional

from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING,
    BTN_BG, BTN_FG, SEL_BG,
    _F, FL, FB, FS, FBTN,
    STATUS_COLORS, STATUS_ICONS,
)


# ══════════════════════════════════════════════════════════════════════════════
# CORE BUTTON
# ══════════════════════════════════════════════════════════════════════════════

class _FlatBtn(tk.Label):
    """
    Drop-in replacement for tk.Button that reliably renders on macOS.

    tk.Button with relief="flat" is rendered by macOS NSButton, which ignores
    custom bg/fg until the first OS redraw (invisible text on startup).
    tk.Label always applies our colours immediately — we simulate button
    behaviour with <Button-1> / <ButtonRelease-1> bindings.

    This is the canonical version (sourced from gui.py).  It supersedes the
    five separate copies that previously lived in each tab file.
    """

    def __init__(
        self,
        parent,
        text:               str  = "",
        command                  = None,
        bg:                 str  = None,
        fg:                 str  = None,
        font                     = None,
        pady:               int  = 6,
        padx:               int  = 12,
        width:              int  = None,
        activebackground:   str  = None,
        activeforeground:   str  = None,
        disabledforeground: str  = None,
        cursor:             str  = "hand2",
        relief:             str  = "flat",
        overrelief:         str  = "flat",
        bd:                 int  = 0,
        highlightthickness: int  = 0,
        highlightbackground: str = None,
        highlightcolor:     str  = None,
        **kw,
    ):
        self._cmd     = command or (lambda: None)
        self._bg      = bg  or BTN_BG
        self._fg      = fg  or BTN_FG
        self._dfg     = disabledforeground or MUTED
        self._enabled = True

        lkw = dict(
            text   = text,
            bg     = self._bg,
            fg     = self._fg,
            font   = font or FBTN,
            pady   = pady,
            padx   = padx,
            cursor = cursor,
            relief = "flat",
            bd     = 0,
        )
        if width is not None:
            lkw["width"] = width

        super().__init__(parent, **lkw)
        self.bind("<Button-1>",        self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_click(self, _e):
        if self._enabled:
            super().configure(relief="sunken")

    def _on_release(self, _e):
        if self._enabled:
            super().configure(relief="flat")
            self._cmd()

    def configure(self, **kw):
        if "state" in kw:
            s = kw.pop("state")
            if s == "disabled":
                self._enabled = False
                super().configure(fg=self._dfg, cursor="arrow")
            else:
                self._enabled = True
                super().configure(fg=self._fg, cursor="hand2")
        if "command" in kw:
            self._cmd = kw.pop("command")
        if "bg" in kw:
            self._bg = kw["bg"]
        if "fg" in kw:
            self._fg = kw["fg"]
        # Drop tk.Button-only kwargs silently (keeps callers compatible)
        for drop in (
            "activebackground", "activeforeground", "overrelief",
            "highlightthickness", "highlightbackground", "highlightcolor",
            "disabledforeground", "bd",
        ):
            kw.pop(drop, None)
        if kw:
            super().configure(**kw)

    # Alias so code that calls btn.config(...) also works
    config = configure


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _btn(
    parent,
    text:    str,
    command: Callable,
    bg:      str = BTN_BG,
    fg:      str = BTN_FG,
    pady:    int = 6,
    padx:    int = 12,
    width:   int = None,
) -> _FlatBtn:
    """Shorthand factory for the most common _FlatBtn usage."""
    kw = dict(bg=bg, fg=fg, pady=pady, padx=padx)
    if width is not None:
        kw["width"] = width
    return _FlatBtn(parent, text=text, command=command, **kw)


def _sec(parent: tk.Widget, text: str) -> tk.Frame:
    """Labelled horizontal rule: ─── TITLE ──────────────────"""
    f = tk.Frame(parent, bg=BG)
    f.pack(fill="x", pady=(14, 4))
    tk.Label(f, text=text, font=FL, bg=BG, fg=ACCENT).pack(side="left")
    tk.Frame(f, bg=BORDER, height=1).pack(
        side="left", fill="x", expand=True, padx=(8, 0)
    )
    return f


def _div(parent: tk.Widget) -> None:
    """Thin 1 px horizontal separator."""
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=6)


def _entry(
    parent,
    var:   tk.StringVar,
    width: int = 22,
    show:  str = None,
) -> tk.Entry:
    """Themed Entry widget (monospace, dark, accent focus ring)."""
    kw = dict(
        textvariable        = var,
        font                = FB,
        bg                  = BTN_BG,
        fg                  = TEXT,
        insertbackground    = ACCENT,
        relief              = "flat",
        width               = width,
        highlightthickness  = 1,
        highlightcolor      = ACCENT,
        highlightbackground = BORDER,
    )
    if show:
        kw["show"] = show
    return tk.Entry(parent, **kw)


def _option_menu(parent, var: tk.StringVar, options: list) -> tk.OptionMenu:
    """Themed OptionMenu widget."""
    om = tk.OptionMenu(parent, var, *options)
    om.config(
        font              = FS,
        bg                = BTN_BG,
        fg                = TEXT,
        activebackground  = ACCENT2,
        relief            = "flat",
        highlightthickness = 0,
    )
    om["menu"].config(bg=BTN_BG, fg=TEXT, activebackground=ACCENT2, font=FS)
    return om


def _scrolled_tree(
    parent,
    columns:    list,
    col_widths: dict = None,
    height:     int  = 12,
) -> ttk.Treeview:
    """
    Themed ttk.Treeview with a vertical scrollbar.

    The frame, style, and scrollbar are wired automatically.
    Returns the Treeview widget so callers can bind events and insert rows.
    """
    frame = tk.Frame(
        parent, bg=PANEL2,
        highlightbackground=BORDER, highlightthickness=1,
    )
    frame.pack(fill="both", expand=True, pady=4)

    style = ttk.Style()
    style.configure(
        "P.Treeview",
        background      = PANEL2,
        foreground      = TEXT,
        fieldbackground = PANEL2,
        rowheight       = 22,
        font            = FS,
        borderwidth     = 0,
    )
    style.configure(
        "P.Treeview.Heading",
        background = PANEL,
        foreground = ACCENT,
        font       = FL,
        relief     = "flat",
    )
    style.map(
        "P.Treeview",
        background=[("selected", SEL_BG)],
        foreground=[("selected", TEXT)],
    )

    tv = ttk.Treeview(
        frame, columns=columns, show="headings",
        style="P.Treeview", height=height,
    )
    sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    _text_cols = {"text", "en", "narration"}
    for col in columns:
        w = (col_widths or {}).get(col, 120)
        tv.heading(col, text=col.upper().replace("_", " "))
        tv.column(col, width=w, anchor="w" if col in _text_cols else "center")

    return tv


# ══════════════════════════════════════════════════════════════════════════════
# STAGE ROW  (pipeline left sidebar)
# ══════════════════════════════════════════════════════════════════════════════

class _StageRow(tk.Frame):
    """
    Clickable stage row displayed in the Pipeline tab's left sidebar.

    Shows:  status icon  |  stage label  |  status text
    Clicking anywhere on the row calls on_click(key).
    Call set_status() after a stage completes to update the icon and colour.
    Call set_selected() when this stage becomes the active content panel.
    """

    def __init__(
        self,
        parent,
        key:      str,
        label:    str,
        desc:     str,
        on_click: Callable,
        **kw,
    ):
        super().__init__(
            parent,
            bg                  = BTN_BG,
            highlightbackground = BORDER,
            highlightthickness  = 1,
            cursor              = "hand2",
            **kw,
        )
        self._key      = key
        self._status   = "pending"
        self._selected = False

        # Status icon (○ ● ✓ ✗ —)
        self._icon_lbl = tk.Label(
            self, text="○", font=(_F, 11),
            bg=BTN_BG, fg=MUTED, width=2,
        )
        self._icon_lbl.pack(side="left", padx=(8, 4), pady=6)

        # Label + status text, stacked vertically
        inner = tk.Frame(self, bg=BTN_BG)
        inner.pack(side="left", fill="x", expand=True, pady=6)
        self._name_lbl = tk.Label(
            inner, text=label, font=FL,
            bg=BTN_BG, fg=TEXT, anchor="w",
        )
        self._name_lbl.pack(fill="x")
        self._status_lbl = tk.Label(
            inner, text="pending", font=FS,
            bg=BTN_BG, fg=MUTED, anchor="w",
        )
        self._status_lbl.pack(fill="x")

        # Make the whole row — including every child widget — clickable
        for widget in (self, self._icon_lbl, inner,
                       self._name_lbl, self._status_lbl):
            widget.bind("<Button-1>", lambda _e: on_click(key))

    def set_status(self, status: str):
        self._status = status
        icon  = STATUS_ICONS.get(status, "○")
        color = STATUS_COLORS.get(status, MUTED)
        self._icon_lbl.config(text=icon,   fg=color)
        self._status_lbl.config(text=status, fg=color)

    def set_selected(self, selected: bool):
        self._selected = selected
        bg = SEL_BG if selected else BTN_BG
        hl = ACCENT  if selected else BORDER
        self.config(bg=bg, highlightbackground=hl)
        for w in (self._icon_lbl, self._status_lbl, self._name_lbl):
            w.config(bg=bg)
        for child in self.winfo_children():
            if isinstance(child, tk.Frame):
                child.config(bg=bg)
                for sub in child.winfo_children():
                    sub.config(bg=bg)


# ══════════════════════════════════════════════════════════════════════════════
# LANG ROW  (dubbing tab left sidebar)
# ══════════════════════════════════════════════════════════════════════════════

class _LangRow(tk.Frame):
    """
    Clickable language status row shown in the Dubbing tab's left sidebar.

    Shows the language name + code and three phase-status pills:
      GEN (Phase 2) · SPLIT (Phase 3) · SYNC (Phase 4)
    Pills are green when the phase is done, grey otherwise.
    """

    def __init__(
        self,
        parent,
        code:     str,
        name:     str,
        p2:       bool,
        p3:       bool,
        p4:       bool,
        on_click: Callable,
        **kw,
    ):
        super().__init__(
            parent,
            bg                  = BTN_BG,
            highlightbackground = BORDER,
            highlightthickness  = 1,
            cursor              = "hand2",
            **kw,
        )
        self._code     = code
        self._selected = False
        self._build(code, name, p2, p3, p4, on_click)

    def _build(self, code, name, p2, p3, p4, on_click):
        left = tk.Frame(self, bg=BTN_BG)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=5)

        header = tk.Frame(left, bg=BTN_BG)
        header.pack(fill="x")
        tk.Label(
            header, text=f"{name}  ({code})",
            font=(_F, 9, "bold"), bg=BTN_BG, fg=TEXT,
        ).pack(side="left")

        pill_row = tk.Frame(left, bg=BTN_BG)
        pill_row.pack(fill="x", pady=(2, 0))
        for label, done in (("GEN", p2), ("SPLIT", p3), ("SYNC", p4)):
            c = SUCCESS if done else MUTED
            tk.Label(
                pill_row, text=label, font=(_F, 7, "bold"),
                bg=PANEL, fg=c, padx=4, pady=1,
            ).pack(side="left", padx=1)

        # Bind click to every sub-widget so the whole row is clickable
        for w in (self, left, header, pill_row):
            w.bind("<Button-1>", lambda _e: on_click(code))

    def set_selected(self, selected: bool):
        self._selected = selected
        bg = SEL_BG if selected else BTN_BG
        hl = ACCENT  if selected else BORDER
        self.config(bg=bg, highlightbackground=hl)
        self._repaint(self, bg)

    def _repaint(self, widget, bg: str):
        try:
            widget.config(bg=bg)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._repaint(child, bg)


# ══════════════════════════════════════════════════════════════════════════════
# EPISODE NAME DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class _EpisodeNameDialog(tk.Toplevel):
    """
    Small themed modal that asks the user for an episode name.
    Pre-filled with the filename stem.

    Usage:
        dlg = _EpisodeNameDialog(parent, stem="Solo Leveling E47")
        name = dlg.result   # str on OK, None on Cancel
    """

    def __init__(self, parent: tk.Widget, stem: str):
        super().__init__(parent)
        self.result: Optional[str] = None

        self.title("Episode Name")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        tk.Label(
            self, text="Episode name:", font=FL, bg=BG, fg=TEXT_DIM,
        ).pack(anchor="w", padx=20, pady=(18, 4))

        self._var = tk.StringVar(value=stem)
        entry = tk.Entry(
            self,
            textvariable        = self._var,
            font                = FB,
            bg                  = BTN_BG,
            fg                  = TEXT,
            insertbackground    = ACCENT,
            relief              = "flat",
            width               = 36,
            highlightthickness  = 1,
            highlightcolor      = ACCENT,
            highlightbackground = BORDER,
        )
        entry.pack(padx=20, pady=(0, 4))
        entry.select_range(0, "end")
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._ok())
        entry.bind("<Escape>", lambda _e: self._cancel())

        tk.Label(
            self,
            text = "e.g.  Solo Leveling S1E47 — Arise",
            font = (_F, 8),
            bg   = BG,
            fg   = MUTED,
        ).pack(anchor="w", padx=20, pady=(0, 12))

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 18))
        _btn(row, "OK",     self._ok,     bg=ACCENT, fg="#000").pack(side="left", padx=(0, 8))
        _btn(row, "Cancel", self._cancel, bg=PANEL2          ).pack(side="left")

        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width()  // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w // 2}+{ph - h // 2}")
        self.wait_window()

    def _ok(self):
        name = self._var.get().strip()
        if not name:
            return
        self.result = name
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# INSERT-AT-POSITION DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class _InsertAtDialog(tk.Toplevel):
    """
    Ask the user for a target panel_index and an image file to insert there.

    Usage:
        dlg = _InsertAtDialog(parent, max_index=42)
        if dlg.result:
            index, path = dlg.result
    """

    def __init__(self, parent: tk.Widget, max_index: int):
        super().__init__(parent)
        self.result        = None
        self._max          = max_index

        self.title("Insert Panel at Position")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        tk.Label(
            self, text="Target position (0 = before first panel):",
            font=FL, bg=BG, fg=TEXT_DIM,
        ).pack(anchor="w", padx=20, pady=(18, 4))

        self._idx_var = tk.StringVar(value=str(max_index))
        tk.Entry(
            self, textvariable=self._idx_var, font=FB,
            bg=BTN_BG, fg=TEXT, insertbackground=ACCENT,
            relief="flat", width=12,
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground=BORDER,
        ).pack(anchor="w", padx=20, pady=(0, 4))

        tk.Label(
            self, text=f"(0 – {max_index})", font=FS, bg=BG, fg=MUTED,
        ).pack(anchor="w", padx=20)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=20, pady=10)

        tk.Label(
            self, text="Image file to insert:",
            font=FL, bg=BG, fg=TEXT_DIM,
        ).pack(anchor="w", padx=20, pady=(0, 4))

        file_row = tk.Frame(self, bg=BG)
        file_row.pack(fill="x", padx=20, pady=(0, 4))
        self._file_var = tk.StringVar()
        tk.Entry(
            file_row, textvariable=self._file_var, font=FS,
            bg=BTN_BG, fg=TEXT, insertbackground=ACCENT,
            relief="flat", width=34,
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground=BORDER,
        ).pack(side="left", padx=(0, 6))
        _btn(file_row, "BROWSE", self._browse, bg=PANEL2,
             pady=3, padx=8).pack(side="left")

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(12, 18))
        _btn(row, "INSERT", self._ok,     bg=ACCENT, fg="#000").pack(side="left", padx=(0, 8))
        _btn(row, "Cancel", self._cancel, bg=PANEL2          ).pack(side="left")

        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width()  // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w // 2}+{ph - h // 2}")
        self.wait_window()

    def _browse(self):
        path = filedialog.askopenfilename(
            title     = "Select image file",
            filetypes = [
                ("Images", "*.jpg *.jpeg *.png *.webp *.tiff *.bmp"),
                ("All files", "*"),
            ],
            parent=self,
        )
        if path:
            self._file_var.set(path)

    def _ok(self):
        try:
            idx = int(self._idx_var.get())
        except ValueError:
            return
        idx = max(0, min(idx, self._max))
        f   = self._file_var.get().strip()
        if not f:
            return
        self.result = (idx, f)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
