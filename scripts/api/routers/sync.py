"""
scripts/api/routers/sync.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Sync-stage regenerate-clearing.

Sync aligns/time-stretches each language's per-panel audio to the English
timing.  "Regenerate" = clear the synced clip(s), then run the sync stage again
(POST /api/pipeline/run {stage:"sync"}).

Endpoints
─────────
  POST /api/sync/clear/{id}   clear synced clip(s): one panel, or a whole language
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps   import get_db
from api.models import (
    OkResponse, SyncBatch, SyncBatchesResponse, SyncClearRangeRequest,
    SyncLangOption, SyncConfigResponse, SyncConfigUpdate,
)
from database   import Database
from dub.batch_manager import load_batch_state, get_batch_state_path
from pipeline_logic import (
    clear_panel_sync, clear_sync_language, continuous_wav_path,
)
import config

router = APIRouter(prefix="/sync", tags=["Sync"])


def _files_url(abs_path: Path) -> str:
    try:
        rel = abs_path.resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return "/files/" + rel.as_posix()
    except (ValueError, OSError):
        return ""


def _full_audio_url(out_folder: str, lang: str) -> tuple[str, bool]:
    """
    Whole-language track for playback.  Prefer the combined SYNCED track the
    Sync stage builds (dub/{lang}/_synced.wav) — every panel time-stretched to
    English timing, so it reflects the real pacing.  Fall back to the dub
    continuous track (pre-sync) only when sync hasn't produced the combined
    track yet.  Returns (url, is_synced_combined).
    """
    synced = Path(out_folder) / "dub" / lang / "_synced.wav"
    if synced.exists():
        return _files_url(synced), True
    cont = continuous_wav_path(out_folder, lang)
    return (_files_url(cont) if cont.exists() else ""), False


@router.post("/clear/{episode_id}", response_model=OkResponse)
def clear(
    episode_id: int,
    lang: str = Query(..., description="Language code to clear"),
    panel_id: Optional[int] = Query(None, description="Clear just this panel; omit for the whole language"),
    db: Database = Depends(get_db),
):
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    if panel_id is not None:
        clear_panel_sync(db, panel_id, lang)
        db.log_action(episode_id, "sync", status=f"regenerate {lang} · 1 panel")
        return OkResponse(ok=True, message=f"Cleared sync for {lang} panel {panel_id}")
    n = clear_sync_language(db, episode_id, lang)
    db.log_action(episode_id, "sync", status=f"regenerate {lang} · {n} panels")
    return OkResponse(ok=True, message=f"Cleared sync for {lang} ({n} panels)")


# ── Per-batch grouping + range clear ────────────────────────────────────────────

def _batch_ranges(out_folder: str, lang: str) -> list[dict]:
    """
    The panel ranges to group synced clips under — the language's own dub
    batches, falling back to English's (same ranges) so English's reference
    view lines up with the languages stretched to it.
    """
    state = load_batch_state(get_batch_state_path(out_folder))
    if not isinstance(state, dict):
        return []
    src = state.get(lang, {}).get("batches") or state.get("en", {}).get("batches") or []
    return sorted(src, key=lambda b: b.get("idx", 0))


def _sync_candidates(out_folder: str) -> list[str]:
    """Non-English languages that have a dub continuous track (sync candidates)."""
    return [
        c for c in config.SUPPORTED_LANGUAGES
        if c != "en" and (Path(out_folder) / "dub" / c / "_continuous.wav").exists()
    ]


def _build_sync_config(db: Database, episode_id: int) -> SyncConfigResponse:
    ep      = db.get_episode(episode_id) or {}
    out     = ep.get("output_folder") or ""
    cands   = _sync_candidates(out)
    sel     = db.get_setting_json(f"sync_langs_{episode_id}", None)
    if sel is None or not isinstance(sel, list):
        sel = list(cands)                       # default: every candidate
    sel = [c for c in sel if c in cands]
    panels  = db.list_panels(episode_id)
    langs = []
    for c in cands:
        n = sum(1 for p in panels if (db.get_panel_audio(p["id"], c) or {}).get("is_synced"))
        langs.append(SyncLangOption(code=c, name=config.SUPPORTED_LANGUAGES.get(c, c.upper()), synced_count=n))
    return SyncConfigResponse(episode_id=episode_id, languages=langs, selected=sel, total_panels=len(panels))


@router.get("/config/{episode_id}", response_model=SyncConfigResponse)
def get_sync_config(episode_id: int, db: Database = Depends(get_db)):
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    return _build_sync_config(db, episode_id)


@router.patch("/config/{episode_id}", response_model=SyncConfigResponse)
def set_sync_config(episode_id: int, body: SyncConfigUpdate, db: Database = Depends(get_db)):
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    bad = [c for c in body.selected if c not in config.SUPPORTED_LANGUAGES]
    if bad:
        raise HTTPException(400, f"Unknown language code(s): {', '.join(bad)}")
    db.set_setting(f"sync_langs_{episode_id}", list(dict.fromkeys(body.selected)))
    return _build_sync_config(db, episode_id)


@router.get("/batches/{episode_id}", response_model=SyncBatchesResponse)
def list_sync_batches(
    episode_id: int,
    lang: str = Query(..., description="Language code"),
    db: Database = Depends(get_db),
):
    """
    Group the per-panel synced clips under their dub batch ranges, for one
    language, plus a whole-language ("full audio") track.  English is the timing
    reference, so it has no stretched clips — its rows are shown as 'reference'.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    out_folder = ep.get("output_folder") or ""
    is_ref     = (lang == "en")
    panels     = {p["panel_index"]: p for p in db.list_panels(episode_id)}
    full_url, full_synced = _full_audio_url(out_folder, lang)

    batches: list[SyncBatch] = []
    for b in _batch_ranges(out_folder, lang):
        idx     = int(b.get("idx", 0))
        pfrom   = int(b.get("panel_from", 0))
        pto     = int(b.get("panel_to", 0))
        indices = b.get("panels") or list(range(pfrom, pto + 1))

        synced, stretches, first_url = 0, [], ""
        for pidx in indices:
            panel = panels.get(pidx)
            if not panel:
                continue
            row = db.get_panel_audio(panel["id"], lang) or {}
            if row.get("is_synced"):
                synced += 1
                if not first_url and row.get("synced_wav"):
                    first_url = _files_url(Path(row["synced_wav"]))
            en_dur, tgt = panel.get("duration_sec"), row.get("raw_duration")
            if en_dur and tgt:
                stretches.append(en_dur / tgt * 100)

        total = len(indices)
        if is_ref:
            status = "reference"
        elif synced == 0:
            status = "pending"
        elif synced < total:
            status = "partial"
        else:
            status = "synced"

        batches.append(SyncBatch(
            idx         = idx,
            panel_from  = pfrom,
            panel_to    = pto,
            synced      = synced,
            total       = total,
            stretch_pct = (round(sum(stretches) / len(stretches)) if stretches else None),
            status      = status,
            synced_url  = first_url,
        ))

    return SyncBatchesResponse(
        episode_id     = episode_id,
        lang           = lang,
        lang_name      = config.SUPPORTED_LANGUAGES.get(lang, lang.upper()),
        is_reference   = is_ref,
        full_audio_url = full_url,
        full_is_synced = full_synced,
        batches        = batches,
    )


@router.post("/clear-range/{episode_id}", response_model=OkResponse)
def clear_range(
    episode_id: int, body: SyncClearRangeRequest, db: Database = Depends(get_db),
):
    """
    Clear synced clips for a panel range of one language so re-running Sync
    redoes just those panels (the sync runner is incremental).
    """
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")

    cleared = 0
    for pidx in range(body.panel_from, body.panel_to + 1):
        panel = db.get_panel_by_index(episode_id, pidx)
        if panel and db.get_panel_audio(panel["id"], body.lang):
            clear_panel_sync(db, panel["id"], body.lang)
            cleared += 1

    db.log_action(episode_id, "sync",
                  status=f"regenerate {body.lang} · panels {body.panel_from + 1}–{body.panel_to + 1}")
    return OkResponse(ok=True, message=f"Cleared sync for {body.lang} panels "
                                       f"{body.panel_from + 1}–{body.panel_to + 1} ({cleared})")
