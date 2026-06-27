"""
ui/stages/sync_stage.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
SYNC stage — Phase 3 (align+split) and Phase 4 (time-stretch).
"""

from __future__ import annotations

import wave
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from pipeline_tab import PipelineTab

from ui.theme import (
    BG, FL, FS, ACCENT, TEXT_DIM, MUTED, SUCCESS, WARNING, INFO
)
from ui.widgets import _div


# ── WAV validation helper ─────────────────────────────────────────────────────

def _is_valid_wav(path: Path) -> bool:
    """
    Return True only if path is a readable, non-empty WAV file.

    A file-size check (> N bytes) is insufficient: a partially-written WAV
    can be large enough to pass the threshold while having a truncated or
    corrupt audio data section.  Opening with the wave module validates the
    RIFF header and getnframes() > 0 confirms there is at least some audio.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() > 0
    except Exception:
        return False


# ── Stage interface ───────────────────────────────────────────────────────────

def build(parent: tk.Frame, key: str, tab: "PipelineTab"):
    """Build the SYNC stage UI."""
    tab._stage_top_bar(parent, key)

    inner = tk.Frame(parent, bg=BG)
    inner.pack(fill="both", expand=True, padx=16, pady=10)

    tk.Label(inner, text="SYNC DUBBED AUDIO TO ENGLISH", font=FL, bg=BG,
             fg=ACCENT).pack(anchor="w", pady=(0, 4))
    tk.Label(inner,
        text="Stretches or compresses each language's per-panel audio to exactly "
             "match the duration of the corresponding English panel clip.\n\n"
             "Requires Phase 3 (ALIGN & SPLIT) to have run in the DUBBING stage "
             "or in the DUBBING tab so English panel clips exist as the reference.",
        font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=560,
        ).pack(anchor="w", pady=(0, 10))

    _div(inner)

    tk.Label(inner, text="LANGUAGE STATUS", font=FL, bg=BG, fg=ACCENT
             ).pack(anchor="w", pady=(0, 6))

    tab._sync_status_frame = tk.Frame(inner, bg=BG)
    tab._sync_status_frame.pack(fill="x", pady=(0, 8))

    load(tab)   # Populate immediately


def load(tab: "PipelineTab"):
    """
    Refresh the per-language sync status display.

    On the first call (from build()) the language rows are created and stored
    on the tab.  On every subsequent call only the text and colour of the
    existing labels are updated — no widgets are destroyed or recreated.

    Previously this function destroyed and recreated all child widgets every
    time it was called, which created and garbage-collected 27 Label objects
    (3 per language × 9 languages) on each refresh.
    """
    if not hasattr(tab, "_sync_status_frame"):
        return

    if not hasattr(tab, "_sync_status_rows"):
        _build_status_rows(tab)
    else:
        _update_status_rows(tab)


# ── Status row helpers ────────────────────────────────────────────────────────

def _build_status_rows(tab: "PipelineTab"):
    """
    Create one row per non-English language inside _sync_status_frame and
    store label references in tab._sync_status_rows for later updates.

    Structure of tab._sync_status_rows:
        {lang_code: (display_name, icon_label, detail_label)}
    """
    frame = tab._sync_status_frame
    tab._sync_status_rows: dict = {}

    for code, name in config.SUPPORTED_LANGUAGES.items():
        if code == "en":
            continue

        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", pady=1)

        # icon_label carries the status icon + language name.
        # Width=26 matches the original layout.
        icon_lbl = tk.Label(
            row, text="", font=FS, bg=BG,
            width=26, anchor="w",
        )
        icon_lbl.pack(side="left")

        detail_lbl = tk.Label(row, text="", font=FS, bg=BG, fg=MUTED)
        detail_lbl.pack(side="left")

        tab._sync_status_rows[code] = (name, icon_lbl, detail_lbl)

    _update_status_rows(tab)


def _update_status_rows(tab: "PipelineTab"):
    """
    Update the text and colour of every status row without touching widget
    structure.  Called on every subsequent load() after the rows are built.
    """
    if not hasattr(tab, "_sync_status_rows"):
        return

    if not tab._episode_id or not tab._episode:
        for code, (name, icon_lbl, detail_lbl) in tab._sync_status_rows.items():
            icon_lbl.config(text=f"  —  {name} ({code})", fg=MUTED)
            detail_lbl.config(text="no episode loaded")
        return

    ep = tab.db.get_episode(tab._episode_id)
    if not ep:
        return

    for code, (name, icon_lbl, detail_lbl) in tab._sync_status_rows.items():
        folder = Path(ep["output_folder"]) / "dub" / code

        if folder.exists():
            synced = list(folder.glob("panel_*_sync.wav"))
            split  = [p for p in folder.glob("panel_*.wav")
                      if "_sync" not in p.name]
        else:
            synced = []
            split  = []

        if synced:
            icon, color  = "✓", SUCCESS
            detail       = f"{len(synced)} panel(s) synced"
        elif split:
            icon, color  = "○", WARNING
            detail       = f"split done ({len(split)} panels) — sync pending"
        else:
            icon, color  = "—", MUTED
            detail       = "run DUBBING first"

        icon_lbl.config(text=f"  {icon}  {name} ({code})", fg=color)
        detail_lbl.config(text=detail)


# ── Runner ────────────────────────────────────────────────────────────────────

def _voice_for(tab, lang_code):
    """Resolve a voice profile for a language (explicit dub_profiles assignment,
    else naming-convention match) — used by the auto-fix pass."""
    import json as _json
    from tts.voice_profile import VoiceProfileManager
    try:
        profiles = tab.db.get_setting_json(f"dub_profiles_{tab._episode_id}", {}) or {}
    except Exception:
        profiles = {}
    if isinstance(profiles, str):
        try: profiles = _json.loads(profiles)
        except Exception: profiles = {}
    vpm  = VoiceProfileManager(str(config.VOICES_DIR))
    name = profiles.get(lang_code)
    if not name:
        match = [p for p in vpm.list_profiles() if p.lower().endswith(f"_{lang_code}")]
        name  = match[0] if match else None
    return vpm.load(name) if name else None


def runner(tab: "PipelineTab") -> bool:
    """
    SYNC runner — two phases in sequence:

    Phase 3 — align_and_split_all
      Transcribes each language's _continuous.wav, fuzzy-matches panel texts
      to word timestamps, snaps cut points to silence, saves per-panel WAVs.
      English is processed first so its durations exist for Phase 4.

    Phase 4 — sync_to_english
      Stretches/compresses each non-English panel clip to match the
      corresponding English panel clip duration (pyrubberband).
    """
    from dub_engine import DubEngine

    engine = DubEngine(tab.db, on_log=tab._log)
    tab._active_engine = engine

    ep = tab.db.get_episode(tab._episode_id)
    if not ep:
        tab._log("Episode not found", "error")
        return False

    output_folder = Path(ep["output_folder"])

    # Phase-banded progress: the SYNC stage spans align+split → time-stretch →
    # auto-fix → stitch. Map each phase onto a slice of 0–100 so the bar climbs
    # monotonically and never reads 100% while auto-fix/stitch are still running.
    def _band(lo, hi):
        def cb(cur, tot=None):
            try:
                frac = (cur / tot) if tot else (float(cur) / 100.0)
            except (TypeError, ValueError, ZeroDivisionError):
                frac = 0.0
            tab._on_progress(int(lo + max(0.0, min(1.0, frac)) * (hi - lo)), 100)
        return cb

    # Detect languages that have a usable _continuous.wav (Phase 2 output).
    #
    # Previously used a file-size check (> 1000 bytes) which passes for
    # partially-written files whose RIFF header is intact but whose audio
    # data is truncated or zeroed.  Now we open each file with the wave
    # module to validate the header and confirm at least one frame exists.
    all_langs = [
        code for code in config.SUPPORTED_LANGUAGES
        if _is_valid_wav(output_folder / "dub" / code / "_continuous.wav")
    ]

    if not all_langs:
        tab._log("No valid continuous audio found — run DUBBING first", "error")
        return False

    non_en = [lc for lc in all_langs if lc != "en"]

    # Respect the per-episode sync target selection (if the user picked specific
    # languages). English stays — it is always the timing reference.
    sel = tab.db.get_setting_json(f"sync_langs_{tab._episode_id}", None)
    if isinstance(sel, list) and sel:
        non_en    = [lc for lc in non_en if lc in sel]
        all_langs = [lc for lc in all_langs if lc == "en" or lc in sel]
    tab._log(
        f"SYNC — {len(all_langs)} language(s) to align+split, "
        f"{len(non_en)} to time-stretch",
        "accent",
    )

    # Phase 3
    tab._log("Phase 3: aligning and splitting …", "accent")
    ok = engine.align_and_split_all(
        tab._episode_id, all_langs,
        on_log=tab._log, on_progress=_band(0, 40),
    )
    if not ok:
        tab._log("Phase 3 (align+split) failed — stopping SYNC", "error")
        return False

    if tab._stop_flag:
        return False

    # Phase 5 — build the combined synced track per language so the UI can play
    # the whole-language audio at its real (post-stretch) pacing.
    def _stitch(langs):
        tab._log("Phase 5: building combined synced tracks …", "accent")
        tab._on_progress(96, 100)
        for lc in langs:
            if tab._stop_flag:
                break
            try:
                engine.stitch_synced(tab._episode_id, lc, on_log=tab._log)
            except Exception as exc:
                tab._log(f"  [{lc}] combined track skipped: {exc}", "warning")
        tab._on_progress(100, 100)

    # Phase 4.5 — automatically fix "rushed" panels (dub longer than English):
    # re-translate shorter → re-dub → re-sync, best of N. Runs without any clicks
    # so the first Sync already cleans up the worst speed-ups.
    def _auto_fix(langs):
        import runtime_settings as rs
        if not rs.get_bool("dub_auto_fix_rushed", getattr(config, "DUB_AUTO_FIX_RUSHED", True)):
            return
        from ai import translator
        comfort  = rs.get_float("dub_max_stretch", getattr(config, "DUB_MAX_STRETCH", 1.20)) or 1.20
        attempts = rs.get_int("dub_fix_attempts", getattr(config, "DUB_FIX_ATTEMPTS", 3))
        floor    = rs.get_float("translate_fit_floor", getattr(config, "TRANSLATE_FIT_FLOOR", 0.45)) or 0.45
        provider = tab.db.get_setting("ai_provider_translate", "nvidia")
        api_key  = tab.db.get_setting("nvidia_api_key", "")
        lm_model = tab.db.get_setting("lm_studio_model", "")
        try:    ctx = int(tab.db.get_setting("lm_studio_context_length", "32768"))
        except (TypeError, ValueError): ctx = 32768

        tab._log("Phase 4.5: checking for rushed panels to auto-fix …", "accent")
        tab._on_progress(72, 100)
        fixed_any = False
        for lc in langs:
            if tab._stop_flag:
                break
            profile = _voice_for(tab, lc)
            if not profile:
                continue
            def _shorten(english, current, target, _lc=lc):
                return translator.shorten_line(
                    english, current, target, _lc,
                    provider=provider, api_key=api_key,
                    lm_studio_model=lm_model, context_length=ctx, on_log=tab._log,
                )
            try:
                res = engine.fix_rushed_panels(
                    tab._episode_id, lc, profile, _shorten,
                    comfort=comfort, attempts=attempts, floor=floor,
                    on_log=tab._log, on_progress=_band(72, 95),
                )
                if res.get("fixed"):
                    fixed_any = True
            except Exception as exc:
                tab._log(f"  [{lc}] auto-fix skipped: {exc}", "warning")
        if not fixed_any:
            tab._log("No rushed panels needed fixing ✓", "info")

    # Phase 4
    if not non_en:
        tab._log("No non-English languages to time-stretch ✓", "info")
        _stitch(all_langs)
        tab.after(0, lambda: load(tab))
        return True

    tab._log(f"Phase 4: syncing {len(non_en)} language(s) to English timing …", "accent")
    all_ok = True
    for lc in non_en:
        if tab._stop_flag:
            all_ok = False
            break
        ok = engine.sync_to_english(
            tab._episode_id, lc,
            on_log=tab._log, on_progress=_band(40, 72),
        )
        if not ok:
            tab._log(f"  Sync failed for '{lc}'", "error")
            all_ok = False

    if all_ok:
        _auto_fix(non_en)        # fix rushed panels before stitching the final tracks
        _stitch(all_langs)
        tab.after(0, lambda: load(tab))
    return all_ok