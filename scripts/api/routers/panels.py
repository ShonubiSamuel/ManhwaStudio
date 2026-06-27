"""
scripts/api/routers/panels.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Panel endpoints for the REVIEW checkpoint — the editable per-panel script grid
that sits between narration and translation in the pipeline.

Endpoints
─────────
  GET   /api/panels/{episode_id}      all panels for an episode, with per-language
                                      translations and a browser-loadable
                                      thumbnail URL.
  PATCH /api/panels/{panel_id}        edit narration and/or a single language's
                                      translation.  Editing narration cascades:
                                      downstream translations + audio are
                                      invalidated via
                                      pipeline_logic.invalidate_panel_downstream.

Why a dedicated router (vs the existing /api/pipeline/panels/{id})
──────────────────────────────────────────────────────────────────
The pipeline router returns a thin PanelResponse for the live-run table.
The Review page needs more: editable text, per-language translation state, and
an image URL the <img> tag can load.  Keeping that here avoids bloating the
pipeline router and matches the API contract in the rebuild plan (Phase 0.2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.deps   import get_db
from api.models import (
    ReviewPanel, PanelTranslation, PanelUpdate, PanelUpdateResult,
)
from database   import Database
from pipeline_logic import invalidate_panel_downstream, mark_downstream_outdated
import config

router = APIRouter(prefix="/panels", tags=["Panels"])


# ── Thumbnail URL helper ──────────────────────────────────────────────────────

def _output_root() -> Path:
    return Path(config.OUTPUT_DIR).resolve()


def _best_image_path(panel: dict) -> str:
    """
    Pick the most display-worthy on-disk image for a panel.
    Preference: explicit panel image → screenshot (video) → upscaled.
    """
    for key in ("image_path", "screenshot_path", "upscaled_path"):
        val = (panel.get(key) or "").strip()
        if val:
            return val
    return ""


def _file_url(abs_path: str) -> str:
    """
    Convert an absolute on-disk path into a /files/<rel> URL the browser can
    fetch, but ONLY if the path is inside config.OUTPUT_DIR.  Paths outside the
    output root return "" (never expose arbitrary filesystem paths).  Used for
    both panel images and generated audio clips.
    """
    if not abs_path:
        return ""
    try:
        resolved = Path(abs_path).resolve()
        rel = resolved.relative_to(_output_root())
    except (ValueError, OSError):
        return ""
    return "/files/" + rel.as_posix()


# Back-compat alias (images).
_thumbnail_url = _file_url


# ── Row builder ───────────────────────────────────────────────────────────────

def _build_review_panel(db: Database, panel: dict) -> ReviewPanel:
    translations: dict[str, PanelTranslation] = {}
    for audio in db.list_panel_audio(panel["id"]):
        code = audio["lang_code"]
        translations[code] = PanelTranslation(
            lang_code       = code,
            translated_text = audio.get("translated_text") or "",
            has_audio       = bool(audio.get("raw_wav")),
            is_synced       = bool(audio.get("is_synced")),
            audio_url       = _file_url(audio.get("raw_wav") or ""),
            synced_url      = _file_url(audio.get("synced_wav") or ""),
            raw_duration    = audio.get("raw_duration"),
            synced_duration = audio.get("synced_duration"),
        )

    img = _best_image_path(panel)
    return ReviewPanel(
        id               = panel["id"],
        episode_id       = panel["episode_id"],
        panel_index      = panel["panel_index"],
        transcript_text  = panel.get("transcript_text") or "",
        narration_text   = panel.get("narration_text")  or "",
        narration_status = panel.get("narration_status") or "pending",
        image_path       = img,
        thumbnail_url    = _thumbnail_url(img),
        start_time_sec   = panel.get("start_time_sec"),
        end_time_sec     = panel.get("end_time_sec"),
        duration_sec     = panel.get("duration_sec"),
        translations     = translations,
        updated_at       = panel.get("updated_at") or 0.0,
    )


def _langs_for_panel(db: Database, panel_id: int) -> list[str]:
    """Languages that have a panel_audio row for this panel (excludes master 'en')."""
    return [
        a["lang_code"]
        for a in db.list_panel_audio(panel_id)
        if a["lang_code"] != "en"
    ]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{episode_id}", response_model=list[ReviewPanel])
def list_review_panels(episode_id: int, db: Database = Depends(get_db)):
    """
    Return every panel of an episode (ordered by panel_index) with the data the
    Review page needs: transcript, narration, per-language translations, and a
    browser-loadable thumbnail URL.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")
    return [_build_review_panel(db, p) for p in db.list_panels(episode_id)]


@router.patch("/{panel_id}", response_model=PanelUpdateResult)
def update_panel(panel_id: int, body: PanelUpdate, db: Database = Depends(get_db)):
    """
    Edit a panel's narration and/or one language's translation.

    Narration edit
    ──────────────
    Persists narration_text (and flags narration_status='done'), then cascades:
    every translated language for this panel is invalidated — its translated
    text is cleared and any generated/synced audio is deleted — so the panel
    is re-translated and re-dubbed on the next run.

    Translation edit
    ────────────────
    Requires lang_code.  Persists translated_text for that language and clears
    only that language's generated audio (so just that clip regenerates),
    leaving the master narration and other languages untouched.
    """
    panel = db.get_panel(panel_id)
    if not panel:
        raise HTTPException(404, f"Panel {panel_id} not found")

    if body.translated_text is not None and not body.lang_code:
        raise HTTPException(400, "translated_text requires a lang_code")

    invalidated: list[str] = []
    summary = {"translations_cleared": 0, "audio_deleted": 0, "sync_deleted": 0}

    # ── Narration edit → cascade-invalidate all downstream languages ──────────
    if body.narration_text is not None:
        new_text = body.narration_text
        changed  = new_text != (panel.get("narration_text") or "")
        db.update_panel(
            panel_id,
            narration_text   = new_text,
            narration_status = "done" if new_text.strip() else "pending",
        )
        if changed:
            langs = _langs_for_panel(db, panel_id)
            if langs:
                result = invalidate_panel_downstream(db, panel_id, langs)
                invalidated = langs
                for k in summary:
                    summary[k] += result.get(k, 0)
            # Reflect staleness across stages immediately (rail dots → outdated).
            mark_downstream_outdated(db, panel["episode_id"], after="narration")
            db.log_action(panel["episode_id"], "refine",
                          status=f"narration edited · panel {panel['panel_index'] + 1}")

    # ── Translation edit → invalidate just that language's audio ──────────────
    if body.translated_text is not None and body.lang_code:
        code = body.lang_code
        db.ensure_panel_audio(panel_id, code)
        # Editing a translation makes any previously generated audio stale.
        db.update_panel_audio(
            db.get_panel_audio(panel_id, code)["id"],
            translated_text = body.translated_text,
            raw_wav         = None,
            raw_duration    = None,
            synced_wav      = None,
            synced_duration = None,
            is_synced       = 0,
        )
        if code not in invalidated and code != "en":
            invalidated.append(code)
        # Editing a translation makes its dub/sync stale.
        mark_downstream_outdated(db, panel["episode_id"], after="translate")
        db.log_action(panel["episode_id"], "translate",
                      status=f"{code} edited · panel {panel['panel_index'] + 1}")

    fresh = db.get_panel(panel_id)
    return PanelUpdateResult(
        panel                = _build_review_panel(db, fresh),
        invalidated_langs    = invalidated,
        translations_cleared = summary["translations_cleared"],
        audio_deleted        = summary["audio_deleted"],
        sync_deleted         = summary["sync_deleted"],
    )
