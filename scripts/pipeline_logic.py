"""
pipeline_logic.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Pipeline orchestration logic.

Extracted from database.py where it did not belong — the DB class should
only read and write rows, not make decisions about stage order or file layout.

Every function here takes `db` as its first argument (the Database instance)
so callers do not need to change their logic — only their import:

    BEFORE:  self.db.invalidate_panel_downstream(panel_id, lang_codes)
    AFTER:   from pipeline_logic import invalidate_panel_downstream
             invalidate_panel_downstream(db, panel_id, lang_codes)

Stage order
───────────
    video:       detect → extract → screenshot → translate → dub → sync → assemble
    pdf:         extract → upscale → narrate → translate → dub → assemble
    screenshots: upscale → translate → dub → assemble

Canonical stage registry
─────────────────────────
This module is the SINGLE SOURCE OF TRUTH for the pipeline's stage taxonomy.

There are two distinct naming layers that historically drifted apart:

  • Runnable keys   — what the UI/API dispatch (see api.routers.pipeline
                      _STAGE_MODULES).  e.g. "video_refine", "pdf_slice".
  • DB columns      — the stage_<col> / progress_<col> columns on the
                      episodes table.  e.g. "extract", "narrate".

Several runnable keys map onto the SAME DB column (video_refine and pdf_slice
both write "extract"; they are mutually exclusive by source_type so there is
no conflict).  STAGE_DB_COLUMN below is the authoritative mapping; every place
that needs to translate a runnable key into its episodes column must use it
rather than assuming  stage_<runnable_key>  exists.

The legacy "tts" stage has been retired — TTS happens inside the "dub" stage
(DubEngine batch generation).  There is no tts_stage.py runner, so "tts" is
no longer listed as a runnable stage anywhere.  The stage_tts / progress_tts
columns remain on the table (harmless) but are never authored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


# ── Canonical stage registry ──────────────────────────────────────────────────
# Maps a runnable stage key (dispatched by the API / clicked in the UI) to the
# episodes-table column that records its status & progress.

STAGE_DB_COLUMN: Dict[str, str] = {
    "detect":       "detect",     # video — panel-cut detection
    "video_refine": "extract",    # video — transcript extract + AI refine
    "pdf_slice":    "extract",    # pdf   — slice + optimize pages
    "pdf_narrate":  "narrate",    # pdf   — AI vision narration
    "upscale":      "upscale",    # screenshots — Real-ESRGAN upscale
    "translate":    "translate",  # all   — translate narration
    "dub":          "dub",        # all   — batch TTS per language
    "sync":         "sync",       # video — align + time-stretch audio
    "assemble":     "assemble",   # all   — final video build
}


def db_column_for_stage(stage: str) -> str:
    """
    Return the episodes-table stage column for a runnable stage key.
    Falls back to the key itself for unknown stages so callers degrade
    gracefully rather than crashing.
    """
    return STAGE_DB_COLUMN.get(stage, stage)


# Human-readable label per runnable stage key — single source of truth shared
# by the pipeline router (log lines) and the orchestrator (/plan response).
STAGE_LABELS: Dict[str, str] = {
    "detect":       "Detect & Screenshot",
    "video_refine": "Video Refine",
    "pdf_slice":    "PDF Slice",
    "pdf_narrate":  "Narrate",
    "upscale":      "Upscale",
    "translate":    "Translate",
    "dub":          "Dubbing",
    "sync":         "Sync",
    "assemble":     "Assemble",
}


def label_for_stage(stage: str) -> str:
    """Friendly label for a runnable stage key (falls back to Title Case)."""
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())


# ── Stage dependency order ────────────────────────────────────────────────────
# Expressed in DB-column terms (the order in which stage_<col> values progress).
# "tts" intentionally removed — see module docstring.

_STAGE_ORDER: List[str] = [
    "detect", "extract", "screenshot", "upscale", "narrate",
    "translate", "dub", "sync", "assemble",
]


# ── Source-type stage flows + the review checkpoint ───────────────────────────
# Expressed in RUNNABLE-key terms (what the API dispatches).  Each flow is split
# at the single review checkpoint that sits between narration and translation:
#
#   pre   — analysis / narration stages, run before the human review gate.
#   post  — translate → dub → (sync, video only) → assemble, run after approval.
#
# The orchestrator runs `pre` for an auto run (stopping at the checkpoint) and
# `post` for a resume run.  This is the single source of truth the /plan route
# and the React Pipeline page both derive from, so they can never drift.

SOURCE_FLOWS: Dict[str, Dict[str, List[str]]] = {
    "video": {
        "pre":  ["detect", "video_refine"],
        "post": ["translate", "dub", "sync", "assemble"],
    },
    "pdf": {
        "pre":  ["pdf_slice", "pdf_narrate"],
        "post": ["translate", "dub", "assemble"],
    },
    "screenshots": {
        "pre":  ["upscale", "pdf_narrate"],
        "post": ["translate", "dub", "assemble"],
    },
}


def stage_plan(source_type: str) -> dict:
    """
    Return the ordered runnable-stage plan for a source type, split at the
    review checkpoint.

    {
        "source_type":      str,
        "pre":              [stage keys before the checkpoint],
        "post":             [stage keys after the checkpoint],
        "stages":           pre + post,
        "checkpoint_index": len(pre),   # the gate sits between pre and post
    }

    Unknown source types fall back to the video flow.
    """
    flow = SOURCE_FLOWS.get(source_type) or SOURCE_FLOWS["video"]
    pre, post = list(flow["pre"]), list(flow["post"])
    return {
        "source_type":      source_type,
        "pre":              pre,
        "post":             post,
        "stages":           pre + post,
        "checkpoint_index": len(pre),
    }


# ── Panel-level invalidation ──────────────────────────────────────────────────

def invalidate_panel_downstream(
    db,
    panel_id:   int,
    lang_codes: List[str],
) -> dict:
    """
    Cascade-invalidate everything downstream of a single panel's text edit.

    For every language in lang_codes:
      • Deletes the per-panel split WAV from disk  (Phase 3 output)
      • Deletes the per-panel sync WAV from disk   (Phase 4 output)
      • Clears translated_text → marks the panel for re-translation
      • Clears raw_wav, synced_wav, durations in panel_audio

    File paths are constructed from the known folder layout — not read from
    DB fields which may already be NULL from a previous partial invalidation.

    Returns {"translations_cleared": N, "audio_deleted": N, "sync_deleted": N}
    """
    summary = {"translations_cleared": 0, "audio_deleted": 0, "sync_deleted": 0}

    panel_row = db._fetchone(
        "SELECT panel_index, episode_id FROM panels WHERE id=?", (panel_id,)
    )
    if not panel_row:
        return summary

    panel_index   = panel_row["panel_index"]
    episode       = db.get_episode(panel_row["episode_id"])
    output_folder = (episode or {}).get("output_folder", "")
    now           = db._now()

    for lang_code in lang_codes:
        if output_folder:
            split_wav = (
                Path(output_folder) / "dub" / lang_code
                / f"panel_{panel_index:04d}.wav"
            )
            if split_wav.exists():
                try:
                    split_wav.unlink()
                    summary["audio_deleted"] += 1
                except Exception:
                    pass

            sync_wav = (
                Path(output_folder) / "dub" / lang_code
                / f"panel_{panel_index:04d}_sync.wav"
            )
            if sync_wav.exists():
                try:
                    sync_wav.unlink()
                    summary["sync_deleted"] += 1
                except Exception:
                    pass

        existing = db.get_panel_audio(panel_id, lang_code)
        if existing:
            db._execute(
                """UPDATE panel_audio
                   SET translated_text = '',
                       raw_wav         = NULL,
                       raw_duration    = NULL,
                       synced_wav      = NULL,
                       synced_duration = NULL,
                       is_synced       = 0,
                       updated_at      = ?
                   WHERE panel_id = ? AND lang_code = ?""",
                (now, panel_id, lang_code),
            )
            summary["translations_cleared"] += 1

    return summary


# ── Episode-level invalidation ────────────────────────────────────────────────

def clear_episode_downstream(
    db,
    episode_id: int,
    lang_codes:  List[str],
) -> dict:
    """
    Bulk-clear ALL translation and audio data for every panel in the episode
    across every supplied language.

    Called when REFINE re-runs from scratch so downstream stages start fresh.
    Does NOT delete panel_audio rows — keeps the row structure so re-running
    TRANSLATE can UPDATE rather than INSERT, avoiding duplicate-key errors.

    Returns {"panels_cleared": N, "langs_cleared": M}
    """
    panels = db.list_panels(episode_id)
    if not panels or not lang_codes:
        return {"panels_cleared": 0, "langs_cleared": 0}

    panel_ids    = [p["id"] for p in panels]
    now          = db._now()
    placeholders = ",".join("?" * len(panel_ids))

    for lang_code in lang_codes:
        db._execute(
            f"""UPDATE panel_audio
                SET translated_text = '',
                    raw_wav         = NULL,
                    raw_duration    = NULL,
                    synced_wav      = NULL,
                    synced_duration = NULL,
                    is_synced       = 0,
                    updated_at      = ?
                WHERE lang_code = ?
                AND   panel_id  IN ({placeholders})""",
            [now, lang_code] + panel_ids,
        )

    return {"panels_cleared": len(panels), "langs_cleared": len(lang_codes)}


def clear_language_translation(
    db,
    episode_id: int,
    lang_code:  str,
) -> int:
    """
    Clear translated_text and all audio fields for EVERY panel of a single
    language.  Used when the user wants to retranslate one language without
    touching any other.

    Returns the number of panel_audio rows updated.
    """
    panels = db.list_panels(episode_id)
    if not panels:
        return 0

    panel_ids    = [p["id"] for p in panels]
    now          = db._now()
    placeholders = ",".join("?" * len(panel_ids))

    db._execute(
        f"""UPDATE panel_audio
            SET translated_text = '',
                raw_wav         = NULL,
                raw_duration    = NULL,
                synced_wav      = NULL,
                synced_duration = NULL,
                is_synced       = 0,
                updated_at      = ?
            WHERE lang_code = ?
            AND   panel_id  IN ({placeholders})""",
        [now, lang_code] + panel_ids,
    )
    return len(panels)


def clear_language_translation_range(
    db,
    episode_id:  int,
    lang_code:   str,
    from_panel:  int,
    to_panel:    int,
) -> int:
    """
    Clear translated_text for panels whose panel_index is in [from_panel, to_panel]
    (both inclusive, 0-based) for a single language.

    Returns the number of rows updated.
    """
    panels = [
        p for p in db.list_panels(episode_id)
        if from_panel <= p["panel_index"] <= to_panel
    ]
    if not panels:
        return 0

    panel_ids    = [p["id"] for p in panels]
    now          = db._now()
    placeholders = ",".join("?" * len(panel_ids))

    db._execute(
        f"""UPDATE panel_audio
            SET translated_text = '',
                raw_wav         = NULL,
                raw_duration    = NULL,
                synced_wav      = NULL,
                synced_duration = NULL,
                is_synced       = 0,
                updated_at      = ?
            WHERE lang_code = ?
            AND   panel_id  IN ({placeholders})""",
        [now, lang_code] + panel_ids,
    )
    return len(panels)


# ── Audio-only invalidation (keep translation, drop generated audio) ──────────

def _episode_panel_ids(db, episode_id: int) -> List[int]:
    return [p["id"] for p in db.list_panels(episode_id)]


def clear_language_audio(db, episode_id: int, lang_code: str) -> int:
    """
    Clear generated audio for every panel of one language WITHOUT touching the
    translated text — used by "regenerate audio" on the Dub stage.
    Returns number of panels affected.
    """
    ids = _episode_panel_ids(db, episode_id)
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    db._execute(
        f"""UPDATE panel_audio
            SET raw_wav=NULL, raw_duration=NULL,
                synced_wav=NULL, synced_duration=NULL, is_synced=0, updated_at=?
            WHERE lang_code=? AND panel_id IN ({ph})""",
        [db._now(), lang_code] + ids,
    )
    return len(ids)


def clear_panel_audio(db, panel_id: int, lang_code: str) -> None:
    """Clear generated audio for ONE panel+language (keeps translated_text)."""
    db._execute(
        """UPDATE panel_audio
           SET raw_wav=NULL, raw_duration=NULL,
               synced_wav=NULL, synced_duration=NULL, is_synced=0, updated_at=?
           WHERE panel_id=? AND lang_code=?""",
        (db._now(), panel_id, lang_code),
    )


def clear_panel_sync(db, panel_id: int, lang_code: str) -> None:
    """Clear only the synced clip for ONE panel+language (keeps raw audio)."""
    db._execute(
        """UPDATE panel_audio
           SET synced_wav=NULL, synced_duration=NULL, is_synced=0, updated_at=?
           WHERE panel_id=? AND lang_code=?""",
        (db._now(), panel_id, lang_code),
    )


def clear_sync_language(db, episode_id: int, lang_code: str) -> int:
    """Clear synced clips for every panel of one language (keeps raw audio)."""
    ids = _episode_panel_ids(db, episode_id)
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    db._execute(
        f"""UPDATE panel_audio
            SET synced_wav=NULL, synced_duration=NULL, is_synced=0, updated_at=?
            WHERE lang_code=? AND panel_id IN ({ph})""",
        [db._now(), lang_code] + ids,
    )
    return len(ids)


# ── Continuous-audio detection (Dub output / Sync input) ──────────────────────
# Dub generates one _continuous.wav per language; Sync later splits it into the
# per-panel clips. So "did Dub produce audio for language X" = does its
# continuous wav exist — NOT whether per-panel raw_wav rows exist (those are a
# Sync output).

def continuous_wav_path(output_folder: str, lang_code: str) -> Path:
    return Path(output_folder or "") / "dub" / lang_code / "_continuous.wav"


def languages_with_continuous(output_folder: str) -> List[str]:
    """Language codes that have a non-empty continuous wav (Dub finished)."""
    import config
    out = []
    for code in config.SUPPORTED_LANGUAGES:
        wav = continuous_wav_path(output_folder, code)
        try:
            if wav.exists() and wav.stat().st_size > 0:
                out.append(code)
        except OSError:
            pass
    return out


# ── Cross-stage "outdated" marking (live cascade) ─────────────────────────────
# When an upstream edit invalidates downstream output, flip the affected
# downstream stages from "done" → "outdated" so the UI reflects staleness
# immediately (the rail dot turns amber) and the orchestrator re-runs them.

_DOWNSTREAM_OF = {
    "narration": ["translate", "dub", "sync"],   # editing narration
    "translate": ["dub", "sync"],                # editing a translation
    "dub":       ["sync"],                        # regenerating audio
}


def mark_downstream_outdated(db, episode_id: int, after: str) -> List[str]:
    """
    Flip downstream stages that are currently "done" to "outdated".
    `after` ∈ {"narration","translate","dub"}.  Returns the columns changed.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        return []
    changed = []
    for col in _DOWNSTREAM_OF.get(after, []):
        if (ep.get(f"stage_{col}") or "") == "done":
            db.set_episode_stage(episode_id, col, "outdated")
            changed.append(col)
    return changed


def cascade_invalidate_downstream(db, episode_id: int, after_col: str) -> dict:
    """
    Re-running a stage invalidates everything after it.  This is the API-context
    equivalent of the desktop PipelineTab._cascade_wipe_downstream(), generalised
    to ANY stage rather than only narration.

    Given the DB column of a stage that just completed (`after_col`), for every
    stage that comes later in `_STAGE_ORDER` and was already complete:
      • flip its status "done" → "outdated" so the rail dot turns amber and the
        orchestrator (auto/resume) re-runs it — see orchestrator._DONE_STATES;
      • wipe the now-stale translation / audio / sync data so a re-run starts
        clean and TRANSLATE doesn't keep reporting a stale "done".

    What gets wiped depends on how far upstream the change was:
      • before TRANSLATE (detect / extract / upscale / narrate) → panels or
        narration changed: clear translations + audio + sync for all languages.
      • TRANSLATE re-ran → keep translations, drop generated audio + sync.
      • DUB re-ran        → keep audio, drop sync clips.

    Returns {"stages_outdated": [...], "panels_cleared": N, "langs_cleared": M}
    for logging.  A no-op (empty result) for the last stage or an unknown column.
    """
    import config

    summary = {"stages_outdated": [], "panels_cleared": 0, "langs_cleared": 0}

    ep = db.get_episode(episode_id)
    if not ep:
        return summary
    try:
        start = _STAGE_ORDER.index(after_col)
    except ValueError:
        return summary

    later = _STAGE_ORDER[start + 1:]

    # 1. Flip already-complete downstream stages to "outdated".
    for col in later:
        if (ep.get(f"stage_{col}") or "") == "done":
            db.set_episode_stage(episode_id, col, "outdated")
            summary["stages_outdated"].append(col)

    # 2. Wipe stale downstream data so re-runs are correct, not just re-flagged.
    all_langs = list(config.SUPPORTED_LANGUAGES.keys())
    if "translate" in later:
        cleared = clear_episode_downstream(db, episode_id, all_langs)
        summary.update(cleared)
    elif "dub" in later:                       # translate just re-ran
        for code in all_langs:
            clear_language_audio(db, episode_id, code)
            clear_sync_language(db, episode_id, code)
        summary["langs_cleared"] = len(all_langs)
    elif "sync" in later:                      # dub just re-ran
        for code in all_langs:
            clear_sync_language(db, episode_id, code)
        summary["langs_cleared"] = len(all_langs)

    return summary


# ── Episode stage management ──────────────────────────────────────────────────

def reset_episode_from_stage(
    db,
    episode_id: int,
    from_stage: str,
):
    """
    Reset all stages at and after from_stage to "pending".
    Stages that are "skipped" are left untouched (they are not relevant
    to this episode's source type).

    Example: reset_episode_from_stage(db, 5, "translate") resets
    translate → dub → sync → assemble to pending.
    """
    try:
        start = _STAGE_ORDER.index(from_stage)
    except ValueError:
        start = 0

    ep = db.get_episode(episode_id)
    if not ep:
        return

    fields = {"updated_at": db._now(), "error_message": ""}
    for stage in _STAGE_ORDER[start:]:
        if ep.get(f"stage_{stage}") != "skipped":
            fields[f"stage_{stage}"]    = "pending"
            fields[f"progress_{stage}"] = 0

    sets   = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [episode_id]
    db._execute(f"UPDATE episodes SET {sets} WHERE id=?", values)


def overall_progress(db, episode_id: int) -> int:
    """
    Return a single 0–100 progress percentage for the episode by averaging
    all active (non-skipped) stage progress values.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        return 0

    stages = [
        "detect", "extract", "upscale", "narrate",
        "translate", "dub", "assemble",
    ]
    active = [s for s in stages if ep.get(f"stage_{s}") != "skipped"]
    if not active:
        return 0

    total = sum(ep.get(f"progress_{s}", 0) for s in active)
    return round(total / len(active))
