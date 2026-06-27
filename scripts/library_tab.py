"""
library_tab.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Project and episode management tab.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Callable, Optional

import config
from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, BTN_FG, SEL_BG,
    _F, FL, FB, FS, FBTN,
    STATUS_COLORS,
)
from ui.widgets import (
    _FlatBtn, _btn, _sec, _div,
    _EpisodeNameDialog, _InsertAtDialog,
)
from core.file_utils import copy_as_jpeg, natural_sort_key


# ── Stage definitions ─────────────────────────────────────────────────────────

_VIDEO_STAGES = [
    ("detect",     "DET"),
    ("extract",    "RFN"),
    ("screenshot", "SCR"),
    ("translate",  "TRL"),
    ("dub",        "DUB"),
    ("sync",       "SYN"),
    ("assemble",   "ASM"),
]
_PDF_STAGES = [
    ("extract",    "SLC"),
    ("narrate",    "NAR"),
    ("translate",  "TRL"),
    ("dub",        "DUB"),
    ("assemble",   "ASM"),
]
_SCREENSHOTS_STAGES = [
    ("upscale",    "UPS"),
    ("translate",  "TRL"),
    ("dub",        "DUB"),
    ("assemble",   "ASM"),
]


def _sanitize(name: str) -> str:
    """Turn any string into a safe filesystem folder name."""
    s = re.sub(r"[^\w\s\-]", "", name).strip()
    s = re.sub(r"[\s]+", "_", s)
    return s.lower() or "untitled"


# ── Screenshot Manager dialog ─────────────────────────────────────────────────

class _ScreenshotsManagerDialog(tk.Toplevel):
    """Full-featured panel management window for Screenshots episodes."""

    _IMG_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"})

    def __init__(self, parent, db, episode, on_log, on_refresh=None):
        super().__init__(parent)
        self.db          = db
        self._episode    = episode
        self._ep_id      = episode["id"]
        self._on_log     = on_log
        self._on_refresh = on_refresh or (lambda: None)
        self._stop_flag  = False

        self.title(f"Screenshot Manager — {episode.get('title', '?')}")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("900x660")
        self.minsize(700, 480)
        self.grab_set()
        self.transient(parent)

        self._build()
        self._refresh_grid()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width()  // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w // 2}+{ph - h // 2}")

    def _build(self):
        toolbar = tk.Frame(self, bg=PANEL, pady=8)
        toolbar.pack(fill="x")

        _btn(toolbar, "➕  ADD PANELS",
             self._add_panels, bg=ACCENT, fg="#000", pady=4, padx=10
             ).pack(side="left", padx=(8, 4))
        _btn(toolbar, "📍  ADD AT POSITION",
             self._add_at_position, bg=PANEL2, pady=4, padx=8
             ).pack(side="left", padx=4)
        _btn(toolbar, "🔄  REPLACE PANEL",
             self._replace_panel, bg=PANEL2, pady=4, padx=8
             ).pack(side="left", padx=4)
        tk.Frame(toolbar, bg=BORDER, width=1).pack(side="left", fill="y", padx=8, pady=4)
        _btn(toolbar, "⬆  UPSCALE SELECTED",
             self._upscale_selected, bg="#1a2a3a", fg=INFO, pady=4, padx=8
             ).pack(side="left", padx=4)
        _btn(toolbar, "⬆⬆  UPSCALE ALL",
             self._upscale_all, bg="#1a2a3a", fg=INFO, pady=4, padx=8
             ).pack(side="left", padx=4)
        tk.Frame(toolbar, bg=BORDER, width=1).pack(side="left", fill="y", padx=8, pady=4)
        _btn(toolbar, "🗑  DELETE PANEL",
             self._delete_selected, bg=PANEL2, fg=ERROR, pady=4, padx=8
             ).pack(side="left", padx=4)
        _btn(toolbar, "🗑🗑  DELETE ALL",
             self._delete_all, bg=PANEL2, fg=ERROR, pady=4, padx=8
             ).pack(side="left", padx=4)

        self._status_var = tk.StringVar(value="")
        self._status_lbl = tk.Label(self, textvariable=self._status_var,
                                    font=FS, bg=PANEL, fg=SUCCESS, anchor="w", padx=10)
        self._status_lbl.pack(fill="x", side="bottom")

        self._prog_var = tk.IntVar(value=0)
        self._prog_bar = ttk.Progressbar(
            self, variable=self._prog_var, maximum=100,
            mode="determinate", style="Accent.Horizontal.TProgressbar",
        )
        self._prog_bar.pack(fill="x", side="bottom")
        self._prog_bar.pack_forget()

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self._info_lbl = tk.Label(mid, text="", font=FS, bg=BG, fg=TEXT_DIM, anchor="w")
        self._info_lbl.pack(fill="x", pady=(0, 4))

        tree_frame = tk.Frame(mid, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style.configure("SM.Treeview",
                         background=PANEL2, foreground=TEXT,
                         fieldbackground=PANEL2, rowheight=24, font=FS, borderwidth=0)
        style.configure("SM.Treeview.Heading",
                         background=PANEL, foreground=ACCENT, font=FL, relief="flat")
        style.map("SM.Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", TEXT)])

        cols = ("#", "filename", "upscaled", "size")
        self._tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings",
            style="SM.Treeview", selectmode="extended",
        )
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._tree.heading("#",         text="#")
        self._tree.heading("filename",  text="FILE")
        self._tree.heading("upscaled",  text="UPSCALED")
        self._tree.heading("size",      text="SIZE")
        self._tree.column("#",         width=50,  anchor="center", stretch=False)
        self._tree.column("filename",  width=380, anchor="w")
        self._tree.column("upscaled",  width=90,  anchor="center", stretch=False)
        self._tree.column("size",      width=80,  anchor="center", stretch=False)
        self._tree.bind("<Double-1>", self._on_double_click)

    def _refresh_grid(self):
        self._tree.delete(*self._tree.get_children())
        panels = sorted(self.db.list_panels(self._ep_id), key=lambda p: p["panel_index"])
        n_up = 0
        for p in panels:
            img   = p.get("image_path") or ""
            fname = Path(img).name if img else "— missing —"
            up    = p.get("upscaled_path")
            up_ok = bool(up and Path(up).exists())
            up_lbl = "✓ done" if up_ok else "○ pending"
            if up_ok:
                n_up += 1
            try:
                sz = Path(img).stat().st_size // 1024 if img else 0
                sz_lbl = f"{sz} KB"
            except Exception:
                sz_lbl = "—"
            tag = "upscaled" if up_ok else "normal"
            self._tree.insert("", "end", iid=str(p["id"]),
                values=(p["panel_index"], fname, up_lbl, sz_lbl), tags=(tag,))

        self._tree.tag_configure("upscaled", foreground=SUCCESS)
        self._tree.tag_configure("normal",   foreground=TEXT)
        n = len(panels)
        self._info_lbl.config(text=f"{n} panel(s)  ·  {n_up} upscaled  ·  {n - n_up} pending")

    def _add_panels(self):
        choice = messagebox.askyesnocancel(
            "Add Panels",
            "How do you want to add panels?\n\n"
            "  YES  →  Select a folder of images\n"
            "  NO   →  Select individual image files\n"
            "  Cancel  →  do nothing",
            parent=self,
        )
        if choice is None:
            return
        if choice:
            source = filedialog.askdirectory(title="Select folder containing panel images", parent=self)
            if not source:
                return
            src_files = self._collect_sorted(Path(source))
        else:
            raw = filedialog.askopenfilenames(
                title="Select panel images",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.tiff *.bmp"), ("All files", "*")],
                parent=self,
            )
            if not raw:
                return
            src_files = sorted(
                [Path(f) for f in raw if Path(f).suffix.lower() in self._IMG_EXTS],
                key=natural_sort_key,
            )
        if not src_files:
            self._set_status("No valid image files found", ERROR)
            return
        self._run_bg(lambda: self._do_add_panels(src_files))

    def _do_add_panels(self, src_files):
        ep = self.db.get_episode(self._ep_id)
        output_folder = Path(ep["output_folder"])
        panels_folder = output_folder / "panels"
        panels_folder.mkdir(parents=True, exist_ok=True)
        existing = self.db.list_panels(self._ep_id)
        next_idx = max((p["panel_index"] for p in existing), default=-1) + 1
        total = len(src_files)
        done  = 0
        for i, src in enumerate(src_files):
            if self._stop_flag:
                break
            out = panels_folder / f"panel_{(next_idx + i):04d}.jpg"
            if copy_as_jpeg(src, out):
                self.db.add_panel(episode_id=self._ep_id, panel_index=next_idx + i, image_path=str(out))
                done += 1
            self.after(0, lambda v=int((i + 1) / total * 100): self._prog_var.set(v))
        self.db.update_episode(self._ep_id, panels_folder=str(panels_folder))
        msg = f"{done} panel(s) added ✓" if done else "No panels added"
        self.after(0, lambda: self._finish_op(msg, SUCCESS if done else WARNING))

    def _add_at_position(self):
        panels = self.db.list_panels(self._ep_id)
        n   = len(panels)
        dlg = _InsertAtDialog(self, max_index=n)
        if dlg.result is None:
            return
        target_idx, file_path = dlg.result
        if not file_path or not Path(file_path).exists():
            self._set_status("No valid file selected", ERROR)
            return
        self._run_bg(lambda: self._do_insert_at(target_idx, Path(file_path)))

    def _do_insert_at(self, target_idx: int, src: Path):
        ep = self.db.get_episode(self._ep_id)
        output_folder = Path(ep["output_folder"])
        panels_folder = output_folder / "panels"
        panels_folder.mkdir(parents=True, exist_ok=True)
        panels = sorted(self.db.list_panels(self._ep_id), key=lambda p: p["panel_index"])
        for p in reversed(panels):
            if p["panel_index"] >= target_idx:
                new_idx  = p["panel_index"] + 1
                old_file = Path(p["image_path"]) if p.get("image_path") else None
                new_file = panels_folder / f"panel_{new_idx:04d}.jpg"
                if old_file and old_file.exists():
                    old_file.rename(new_file)
                self.db.update_panel(
                    p["id"],
                    panel_index   = new_idx,
                    image_path    = str(new_file) if old_file else p.get("image_path"),
                    upscaled_path = None,
                )
        out = panels_folder / f"panel_{target_idx:04d}.jpg"
        if copy_as_jpeg(src, out):
            self.db.add_panel(episode_id=self._ep_id, panel_index=target_idx, image_path=str(out))
            self.after(0, lambda: self._finish_op(f"Panel inserted at position {target_idx} ✓", SUCCESS))
        else:
            self.after(0, lambda: self._finish_op("Failed to copy image to panels folder", ERROR))

    def _replace_panel(self):
        sel = self._get_selected_panels()
        if not sel:
            self._set_status("Select a panel in the list first", WARNING)
            return
        if len(sel) > 1:
            self._set_status("Select exactly one panel to replace", WARNING)
            return
        panel = sel[0]
        idx   = panel["panel_index"]
        new_file = filedialog.askopenfilename(
            title=f"Select replacement image for Panel {idx:04d}",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.tiff *.bmp"), ("All files", "*")],
            parent=self,
        )
        if not new_file:
            return
        self._run_bg(lambda: self._do_replace(idx, Path(new_file)))

    def _do_replace(self, panel_index: int, src: Path):
        try:
            from image_upscaler import ImageUpscaler
            engine = ImageUpscaler(self.db, self._episode["output_folder"], on_log=self._on_log)
            ok = engine.replace_panel(self._ep_id, panel_index, str(src))
            msg = f"Panel {panel_index:04d} replaced ✓" if ok else "Replace failed"
            lvl = SUCCESS if ok else ERROR
        except Exception as exc:
            msg = f"Replace error: {exc}"
            lvl = ERROR
        self.after(0, lambda: self._finish_op(msg, lvl))

    def _delete_selected(self):
        sel = self._get_selected_panels()
        if not sel:
            self._set_status("Select panel(s) to delete first", WARNING)
            return
        indices = sorted(p["panel_index"] for p in sel)
        noun = f"{len(sel)} panel(s)" if len(sel) > 1 else f"Panel {indices[0]:04d}"
        if not messagebox.askyesno("Delete Panel(s)",
            f"Delete {noun}?\n\nRemaining panels will be re-indexed.\nThis cannot be undone.",
            parent=self):
            return
        self._run_bg(lambda: self._do_delete(sel))

    def _do_delete(self, panels_to_delete: list):
        ep = self.db.get_episode(self._ep_id)
        panels_folder = Path(ep.get("panels_folder") or Path(ep["output_folder"]) / "panels")
        ids_to_delete = {p["id"] for p in panels_to_delete}
        for p in panels_to_delete:
            img = p.get("image_path")
            if img:
                try:
                    Path(img).unlink(missing_ok=True)
                except Exception:
                    pass
            self.db.delete_panel(p["id"])
        remaining = sorted(self.db.list_panels(self._ep_id), key=lambda p: p["panel_index"])
        for new_idx, p in enumerate(remaining):
            if p["panel_index"] != new_idx:
                old_path = Path(p["image_path"]) if p.get("image_path") else None
                new_path = panels_folder / f"panel_{new_idx:04d}.jpg"
                if old_path and old_path.exists() and old_path != new_path:
                    try:
                        old_path.rename(new_path)
                    except Exception:
                        new_path = old_path
                self.db.update_panel(
                    p["id"],
                    panel_index   = new_idx,
                    image_path    = str(new_path) if old_path else p.get("image_path"),
                    upscaled_path = None,
                )
        n_del = len(ids_to_delete)
        self.after(0, lambda: self._finish_op(f"{n_del} panel(s) deleted, sequence re-indexed ✓", WARNING))

    def _delete_all(self):
        panels = self.db.list_panels(self._ep_id)
        if not panels:
            self._set_status("No panels to delete", MUTED)
            return
        if not messagebox.askyesno("Delete ALL Panels",
            f"Delete all {len(panels)} panels?\n\nAll panel files on disk will be removed.\nThis cannot be undone.",
            parent=self):
            return
        self._run_bg(lambda: self._do_delete_all(panels))

    def _do_delete_all(self, panels: list):
        for p in panels:
            img = p.get("image_path")
            if img:
                try:
                    Path(img).unlink(missing_ok=True)
                except Exception:
                    pass
            self.db.delete_panel(p["id"])
        self.after(0, lambda: self._finish_op(f"All {len(panels)} panels deleted ✓", WARNING))

    def _upscale_selected(self):
        sel = self._get_selected_panels()
        if not sel:
            self._set_status("Select panel(s) to upscale first", WARNING)
            return
        self._run_bg(lambda: self._do_upscale(sel))

    def _upscale_all(self):
        panels = self.db.list_panels(self._ep_id)
        pending = [p for p in panels if not (p.get("upscaled_path") and Path(p["upscaled_path"]).exists())]
        if not pending:
            self._set_status("All panels are already upscaled ✓", SUCCESS)
            return
        self._run_bg(lambda: self._do_upscale(pending))

    def _do_upscale(self, panels: list):
        try:
            from image_upscaler import ImageUpscaler
            engine = ImageUpscaler(self.db, self._episode["output_folder"], on_log=self._on_log)
            ok = engine.upscale_panels(
                self._ep_id,
                on_progress=lambda cur, tot:
                    self.after(0, lambda: self._prog_var.set(int(cur / tot * 100) if tot else 0)),
            )
            msg = "Upscale complete ✓" if ok else "Upscale encountered errors"
            lvl = SUCCESS if ok else WARNING
        except Exception as exc:
            msg = f"Upscale error: {exc}"
            lvl = ERROR
        self.after(0, lambda: self._finish_op(msg, lvl))

    def _on_double_click(self, _event):
        sel = self._get_selected_panels()
        if not sel:
            return
        img = sel[0].get("image_path")
        if img and Path(img).exists():
            import os, sys
            if sys.platform == "darwin":
                os.system(f"open -R '{img}'")
            elif sys.platform == "win32":
                os.system(f'explorer /select,"{img}"')
            else:
                os.system(f"xdg-open '{Path(img).parent}'")

    def _get_selected_panels(self) -> list:
        iids = self._tree.selection()
        if not iids:
            return []
        result = []
        for pid in [int(i) for i in iids]:
            p = self.db.get_panel(pid)
            if p:
                result.append(p)
        return sorted(result, key=lambda p: p["panel_index"])

    def _run_bg(self, fn):
        self._stop_flag = False
        self._prog_var.set(0)
        self._prog_bar.pack(fill="x", side="bottom", before=self._status_lbl)
        self._set_status("Working…", INFO)

        def _wrap():
            try:
                fn()
            except Exception as exc:
                _msg = f"Error: {exc}"
                self.after(0, lambda m=_msg: self._finish_op(m, ERROR))
            finally:
                self.after(0, self._on_refresh)

        threading.Thread(target=_wrap, daemon=True, name="mgr-op").start()

    def _finish_op(self, msg: str, color: str = SUCCESS):
        self._prog_bar.pack_forget()
        self._set_status(msg, color)
        self._refresh_grid()
        self._on_refresh()

    def _set_status(self, msg: str, color: str = SUCCESS):
        self._status_var.set(msg)
        self._status_lbl.config(fg=color)

    @staticmethod
    def _collect_sorted(folder: Path) -> list:
        IMG_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"})
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS]
        return sorted(files, key=natural_sort_key)

    def _on_close(self):
        self._stop_flag = True
        self._on_refresh()
        self.destroy()


# ── Episode row card ──────────────────────────────────────────────────────────

class EpisodeRow(tk.Frame):
    """A single episode card in the episode list."""

    _BADGE_STYLE: dict = {
        "video":       {"bg": "#1e2a1e", "fg": SUCCESS,   "text": "▶ VIDEO"},
        "pdf":         {"bg": "#1a1a2e", "fg": INFO,      "text": "▸ PDF  "},
        "screenshots": {"bg": "#1a2a1a", "fg": "#a3e635", "text": "▸ UPSCALE"},
    }

    def __init__(self, parent, episode, on_open, on_delete, on_intake=None, on_manage=None, **kw):
        super().__init__(parent, bg=PANEL2, highlightbackground=BORDER,
                         highlightthickness=1, **kw)
        self._episode = episode
        self._pill_labels: dict = {}
        self._build(on_open, on_delete,
                    on_intake or (lambda _: None),
                    on_manage or (lambda _: None))

    def _build(self, on_open, on_delete, on_intake, on_manage):
        ep  = self._episode
        eid = ep["id"]
        src = ep.get("source_type", "video").lower()

        left = tk.Frame(self, bg=PANEL2)
        left.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        top = tk.Frame(left, bg=PANEL2)
        top.pack(fill="x")

        badge_style = self._BADGE_STYLE.get(src, self._BADGE_STYLE["video"])
        tk.Label(top, text=badge_style["text"], font=(_F, 8, "bold"),
                 bg=badge_style["bg"], fg=badge_style["fg"],
                 padx=5, pady=1).pack(side="left", padx=(0, 8))

        name_text = ep.get("title") or ep.get("name") or f"Episode {eid}"
        tk.Label(top, text=name_text, font=(_F, 10, "bold"),
                 bg=PANEL2, fg=TEXT, anchor="w").pack(side="left", fill="x", expand=True)

        src_path = ep.get("source_path") or ""
        src_name = Path(src_path).name if src_path else "—"
        if len(src_name) > 60:
            src_name = "…" + src_name[-57:]
        tk.Label(left, text=src_name, font=FS, bg=PANEL2, fg=MUTED, anchor="w"
                 ).pack(fill="x", pady=(2, 4))

        self._build_stage_pills(left, ep, src)

        right = tk.Frame(self, bg=PANEL2)
        right.pack(side="right", padx=8, pady=8)
        _btn(right, "OPEN EPISODE →", lambda: on_open(eid),
             bg=ACCENT2, fg="#fff", pady=5, padx=10).pack(pady=(0, 4))
        _btn(right, "DELETE", lambda: on_delete(eid),
             fg=ERROR, pady=3, padx=10).pack()

    def _build_stage_pills(self, parent, ep, src):
        if src == "video":
            stages = _VIDEO_STAGES
        elif src == "screenshots":
            stages = _SCREENSHOTS_STAGES
        else:
            stages = _PDF_STAGES

        row = tk.Frame(parent, bg=PANEL2)
        row.pack(fill="x")

        for key, label in stages:
            status = ep.get(f"stage_{key}") or "pending"
            color  = STATUS_COLORS.get(status, MUTED)
            pill = tk.Label(row, text=label, font=(_F, 7, "bold"),
                            bg=PANEL, fg=color, padx=5, pady=2)
            pill.pack(side="left", padx=1)
            self._pill_labels[key] = pill

    def refresh_stages(self, episode: dict):
        self._episode = episode
        src = episode.get("source_type", "video").lower()
        if src == "video":
            stages = _VIDEO_STAGES
        elif src == "screenshots":
            stages = _SCREENSHOTS_STAGES
        else:
            stages = _PDF_STAGES
        for key, _label in stages:
            if key in self._pill_labels:
                status = episode.get(f"stage_{key}") or "pending"
                color  = STATUS_COLORS.get(status, MUTED)
                self._pill_labels[key].config(fg=color)


# ── Library tab ───────────────────────────────────────────────────────────────

class LibraryTab(tk.Frame):
    """Project and episode management tab."""

    def __init__(self, parent, db, on_open_episode, on_log, on_stats_change=None):
        super().__init__(parent, bg=BG)
        self.db               = db
        self._on_open_episode = on_open_episode
        self._on_log          = on_log
        self._on_stats_change = on_stats_change or (lambda: None)
        self._selected_project_id: Optional[int] = None
        self._episode_rows: dict = {}
        self._build()
        self._refresh_projects()

    def _build(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=5, sashrelief="flat", sashpad=0)
        pw.pack(fill="both", expand=True, padx=0, pady=8)
        left  = tk.Frame(pw, bg=BG, width=260)
        right = tk.Frame(pw, bg=BG)
        pw.add(left,  minsize=200)
        pw.add(right, minsize=500)
        self._build_project_panel(left)
        self._build_episode_panel(right)

    def _build_project_panel(self, parent: tk.Frame):
        _sec(parent, "MANHWA SERIES")
        add_row = tk.Frame(parent, bg=BG)
        add_row.pack(fill="x", pady=(0, 6))
        self._series_var = tk.StringVar()
        entry = tk.Entry(add_row, textvariable=self._series_var, font=FB,
                         bg=BTN_BG, fg=TEXT, insertbackground=ACCENT, relief="flat",
                         highlightthickness=1, highlightcolor=ACCENT, highlightbackground=BORDER)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        entry.bind("<Return>", lambda _e: self._add_project())
        _btn(add_row, "+ ADD", self._add_project, bg=ACCENT, fg="#000").pack(side="right")

        lf = tk.Frame(parent, bg=PANEL2, highlightbackground=BORDER, highlightthickness=1)
        lf.pack(fill="both", expand=True)
        self._project_list = tk.Listbox(lf, font=FB, bg=PANEL2, fg=TEXT,
                                         selectbackground=ACCENT2, selectforeground="#fff",
                                         activestyle="none", relief="flat", bd=0,
                                         highlightthickness=0)
        self._project_list.pack(fill="both", expand=True, padx=2, pady=2)
        self._project_list.bind("<<ListboxSelect>>", self._on_project_select)
        _btn(parent, "DELETE SERIES", self._delete_project, fg=ERROR).pack(fill="x", pady=(4, 0))

    def _build_episode_panel(self, parent: tk.Frame):
        _sec(parent, "EPISODES")
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", pady=(0, 6))
        _btn(toolbar, "⊕  ADD VIDEO EPISODE", self._add_video_episode,
             bg=ACCENT, fg="#000", padx=10).pack(side="left", padx=(0, 6))
        _btn(toolbar, "⊕  ADD PDF EPISODE", self._add_pdf_episode,
             bg="#1a2a3a", fg=INFO, padx=10).pack(side="left", padx=(0, 6))
        _btn(toolbar, "⊕  ADD IMAGES TO UPSCALE", self._add_screenshots_episode,
             bg="#1a2a1a", fg="#a3e635", padx=10).pack(side="left")
        self._ep_count_lbl = tk.Label(toolbar, text="", font=FS, bg=BG, fg=MUTED)
        self._ep_count_lbl.pack(side="right")
        _div(parent)

        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True)
        self._canvas  = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scrollbar     = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        self._ep_inner = tk.Frame(self._canvas, bg=BG)
        self._ep_inner.bind("<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas_window = self._canvas.create_window((0, 0), window=self._ep_inner, anchor="nw")
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_window, width=e.width))
        self._canvas.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 * int(e.delta / 120), "units"))
        self._empty_lbl = tk.Label(self._ep_inner,
            text="Select a series, then add episodes above.",
            font=FB, bg=BG, fg=MUTED, justify="center")

    # ══════════════════════════════════════════════════════════════════════════
    # PROJECT ACTIONS
    # ══════════════════════════════════════════════════════════════════════════

    def _add_project(self):
        name = self._series_var.get().strip()
        if not name:
            return
        existing = self.db.get_project_by_name(name)
        if existing:
            self._log(f"Series '{name}' already exists", "warning")
            return
        folder_name = _sanitize(name)
        proj_id = self.db.add_project(name)
        (config.OUTPUT_DIR / folder_name).mkdir(parents=True, exist_ok=True)
        self._series_var.set("")
        self._log(f"Created series: {name}", "success")
        self._refresh_projects()
        try:
            all_projects = self.db.list_projects()
            idx = next((i for i, p in enumerate(all_projects) if p["id"] == proj_id), None)
            if idx is not None:
                self._project_list.selection_clear(0, "end")
                self._project_list.selection_set(idx)
                self._project_list.see(idx)
                self._on_project_select(None)
        except Exception:
            pass
        self._on_stats_change()

    def _delete_project(self):
        sel = self._project_list.curselection()
        if not sel:
            self._log("Select a series first", "warning")
            return
        name = self._project_list.get(sel[0])
        if not messagebox.askyesno("Delete Series",
                                    f"Delete '{name}' and all its episodes?\n\nThis cannot be undone."):
            return
        proj = self.db.get_project_by_name(name)
        if proj:
            self.db.delete_project(proj["id"])
        self._selected_project_id = None
        self._log(f"Deleted series: {name}", "warning")
        self._refresh_projects()
        self._refresh_episodes()
        self._on_stats_change()

    def _on_project_select(self, _event):
        sel = self._project_list.curselection()
        if not sel:
            return
        name = self._project_list.get(sel[0])
        proj = self.db.get_project_by_name(name)
        if proj:
            self._selected_project_id = proj["id"]
            self._refresh_episodes()

    # ══════════════════════════════════════════════════════════════════════════
    # EPISODE ACTIONS
    # ══════════════════════════════════════════════════════════════════════════

    def _add_video_episode(self):
        if not self._selected_project_id:
            self._log("Select a series first", "warning")
            return
        path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v"), ("All files", "*")],
        )
        if not path:
            return
        self._create_episode_from_path(path, "video")

    def _add_pdf_episode(self):
        if not self._selected_project_id:
            self._log("Select a series first", "warning")
            return
        path = filedialog.askopenfilename(
            title="Select PDF File",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*")],
        )
        if not path:
            return
        self._create_episode_from_path(path, "pdf")

    def _add_screenshots_episode(self):
        if not self._selected_project_id:
            self._log("Select a series first", "warning")
            return
        dlg  = _EpisodeNameDialog(self, "Chapter 01")
        name = dlg.result
        if not name:
            return
        try:
            proj = self.db.get_project(self._selected_project_id)
            if not proj:
                self._log("Series not found in database", "error")
                return
            ep_slug       = _sanitize(name)
            output_folder = config.OUTPUT_DIR / proj["folder_name"] / ep_slug
            panels_folder = output_folder / "panels"
            panels_folder.mkdir(parents=True, exist_ok=True)
            ep_id = self.db.add_episode(
                project_id    = self._selected_project_id,
                title         = name,
                source_type   = "screenshots",
                source_path   = str(panels_folder),
                output_folder = str(output_folder),
            )
            self.db.update_episode(ep_id, panels_folder=str(panels_folder))
            self._log(f"Created Screenshots episode: {name}", "success")
            self._refresh_episodes()
            self._on_stats_change()
        except Exception as exc:
            self._log(f"Failed to create Screenshots episode: {exc}", "error")
            import traceback; print(traceback.format_exc())

    def _create_episode_from_path(self, path: str, source_type: str):
        import shutil
        try:
            stem = Path(path).stem
            dlg  = _EpisodeNameDialog(self, stem)
            name = dlg.result
            if not name:
                return
            proj = None
            try:
                proj = self.db.get_project(self._selected_project_id)
            except AttributeError:
                pass
            if not proj:
                for p in self.db.list_projects():
                    if p["id"] == self._selected_project_id:
                        proj = p
                        break
            if not proj:
                self._log(f"Series not found in database (id={self._selected_project_id})", "error")
                return
            ep_slug       = _sanitize(name)
            output_folder = config.OUTPUT_DIR / proj["folder_name"] / ep_slug
            input_folder  = output_folder / "input"
            input_folder.mkdir(parents=True, exist_ok=True)
            src_filename = Path(path).name
            dest_path    = input_folder / src_filename
            if not dest_path.exists():
                self._log(f"Copying {src_filename} → input/ …", "muted")
                shutil.copy2(path, dest_path)
                self._log(f"Copied ✓", "muted")
            else:
                self._log(f"{src_filename} already in input/ — skipping copy", "muted")
            self.db.add_episode(
                project_id    = self._selected_project_id,
                title         = name,
                source_type   = source_type,
                source_path   = str(dest_path),
                output_folder = str(output_folder),
            )
            self._log(f"Added {source_type.upper()} episode: {name}", "success")
            self._log(f"Source copied to: input/{Path(path).name}", "muted")
            self._refresh_episodes()
            self._canvas.update_idletasks()
            self._on_stats_change()
        except Exception as exc:
            self._log(f"Failed to add episode: {exc}", "error")
            import traceback; print(traceback.format_exc())

    def _open_episode(self, episode_id: int):
        self._on_open_episode(episode_id)

    def _open_screenshots_manager(self, episode_id: int):
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return
        _ScreenshotsManagerDialog(
            parent=self, db=self.db, episode=ep,
            on_log=self._log, on_refresh=self._refresh_episodes,
        )

    def _intake_screenshots(self, episode_id: int):
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return
        choice = messagebox.askyesnocancel(
            "Intake Screenshots",
            "Select how to add your panel screenshots:\n\n"
            "  YES  →  Select a folder of images\n"
            "  NO   →  Select individual image files\n"
            "  Cancel  →  do nothing",
        )
        if choice is None:
            return
        if choice:
            source = filedialog.askdirectory(title="Select folder containing panel screenshots")
        else:
            source = filedialog.askopenfilenames(
                title="Select panel screenshot images",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.tiff *.bmp"), ("All files", "*")],
            )
            if source:
                source = list(source)
        if not source:
            return

        def _bg():
            try:
                from image_upscaler import ImageUpscaler
                engine = ImageUpscaler(self.db, ep["output_folder"], on_log=self._log)
                ok     = engine.intake_screenshots(episode_id, source)
                panels = self.db.list_panels(episode_id)
                n      = len(panels)
                if ok:
                    self._log(
                        f"Intake complete — {n} panel(s) registered ✓  "
                        f"(Now open the episode in PIPELINE → UPSCALE to run 4× upscaling)",
                        "success",
                    )
                    self.after(0, self._refresh_episodes)
                else:
                    self._log("Intake did not complete — check logs", "warning")
            except Exception as exc:
                self._log(f"Intake error: {exc}", "error")
                import traceback; print(traceback.format_exc())

        threading.Thread(target=_bg, daemon=True, name="intake").start()
        self._log("Intake started…", "info")

    def _delete_episode(self, episode_id: int):
        ep = None
        try:
            ep = self.db.get_episode(episode_id)
        except Exception:
            pass
        if not ep:
            self._log("Episode not found", "warning")
            return
        ep_name = ep.get("title") or ep.get("name") or f"Episode {episode_id}"
        input_folder = None
        output_folder = ep.get("output_folder")
        if output_folder:
            candidate = Path(output_folder) / "input"
            if candidate.exists():
                input_folder = candidate
        msg = f"Delete '{ep_name}' from the library?\n\nDatabase records will be removed."
        if input_folder:
            msg += "\n\nAlso delete the copied source file from input/ folder?"
            delete_input = messagebox.askyesnocancel("Delete Episode", msg)
            if delete_input is None:
                return
        else:
            if not messagebox.askyesno("Delete Episode", msg):
                return
            delete_input = False
        try:
            self.db.delete_episode(episode_id)
        except AttributeError:
            try:
                self.db.remove_episode(episode_id)
            except Exception as exc2:
                self._log(f"DB delete failed: {exc2}", "error")
                return
        except Exception as exc:
            self._log(f"DB delete failed: {exc}", "error")
            return
        if delete_input and input_folder and input_folder.exists():
            try:
                import shutil
                shutil.rmtree(str(input_folder))
                self._log(f"Removed input/ folder for '{ep_name}'", "muted")
            except Exception as exc:
                self._log(f"Could not remove input/: {exc}", "warning")
        self._log(f"Deleted episode: {ep_name}", "warning")
        self._refresh_episodes()
        self._on_stats_change()

    # ══════════════════════════════════════════════════════════════════════════
    # REFRESH HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_projects(self):
        self._project_list.delete(0, "end")
        for proj in self.db.list_projects():
            self._project_list.insert("end", proj["name"])

    def _refresh_episodes(self):
        for w in self._ep_inner.winfo_children():
            w.destroy()
        self._episode_rows.clear()

        if not self._selected_project_id:
            self._empty_lbl = tk.Label(self._ep_inner,
                text="Select a series on the left to see its episodes.",
                font=FB, bg=BG, fg=MUTED, justify="center")
            self._empty_lbl.pack(pady=60)
            self._ep_count_lbl.config(text="")
            return

        episodes = self.db.list_episodes(self._selected_project_id)
        if not episodes:
            self._ep_count_lbl.config(text="0 episodes")
            tk.Label(self._ep_inner,
                text="No episodes yet.\nUse the buttons above to add one.",
                font=FB, bg=BG, fg=MUTED, justify="center").pack(pady=60)
            return

        self._ep_count_lbl.config(text=f"{len(episodes)} episode(s)")
        for ep in episodes:
            row = EpisodeRow(
                self._ep_inner,
                episode   = ep,
                on_open   = self._open_episode,
                on_delete = self._delete_episode,
                on_intake = self._intake_screenshots,
                on_manage = self._open_screenshots_manager,
            )
            row.pack(fill="x", padx=4, pady=3)
            self._episode_rows[ep["id"]] = row
        self._canvas.yview_moveto(0)

    def refresh_episode_stages(self, episode_id: int):
        ep = self.db.get_episode(episode_id)
        if ep and episode_id in self._episode_rows:
            self._episode_rows[episode_id].refresh_stages(ep)

    def refresh(self):
        sel_name: Optional[str] = None
        sel = self._project_list.curselection()
        if sel:
            try:
                sel_name = self._project_list.get(sel[0])
            except Exception:
                pass
        self._refresh_projects()
        if sel_name:
            items = list(self._project_list.get(0, "end"))
            if sel_name in items:
                idx = items.index(sel_name)
                self._project_list.selection_set(idx)
                self._on_project_select(None)
                return
        self._refresh_episodes()

    def _log(self, msg: str, level: str = "info"):
        self._on_log(msg, level)
