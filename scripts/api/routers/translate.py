"""
scripts/api/routers/translate.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Translate-stage configuration + regenerate-clearing.

The translate runner is incremental: it only translates panels that are missing
a translation for each selected language (setting translate_langs_{id}).  So
"regenerate" = clear the target text, then run the translate stage again
(the run is the existing POST /api/pipeline/run {stage:"translate"}).

Endpoints
─────────
  GET   /api/translate/config/{id}    target languages + per-language counts + selection
  PATCH /api/translate/config/{id}    set which languages to translate into
  POST  /api/translate/clear/{id}     clear a translation (one panel, or a whole
                                      language) so the next run regenerates it
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps   import get_db
from api.models import TranslateConfig, TranslateLangOption, TranslateConfigUpdate, OkResponse
from database   import Database
from pipeline_logic import (
    clear_language_translation, invalidate_panel_downstream,
)
import config

router = APIRouter(prefix="/translate", tags=["Translate"])


def _selected(db: Database, episode_id: int) -> list[str]:
    val = db.get_setting(f"translate_langs_{episode_id}", [])
    if isinstance(val, str):
        import json
        try: val = json.loads(val)
        except Exception: val = []
    return [c for c in (val or []) if c in config.SUPPORTED_LANGUAGES and c != "en"]


def _build(db: Database, episode_id: int) -> TranslateConfig:
    panels = db.list_panels(episode_id)
    total  = len(panels)
    selected = _selected(db, episode_id)
    langs = []
    for code, name in config.SUPPORTED_LANGUAGES.items():
        if code == "en":
            continue
        n = 0
        for p in panels:
            a = db.get_panel_audio(p["id"], code)
            if a and (a.get("translated_text") or "").strip():
                n += 1
        langs.append(TranslateLangOption(code=code, name=name, translated_count=n))
    return TranslateConfig(episode_id=episode_id, total_panels=total,
                           languages=langs, selected=selected)


@router.get("/config/{episode_id}", response_model=TranslateConfig)
def get_config(episode_id: int, db: Database = Depends(get_db)):
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    return _build(db, episode_id)


@router.patch("/config/{episode_id}", response_model=TranslateConfig)
def set_config(episode_id: int, body: TranslateConfigUpdate, db: Database = Depends(get_db)):
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    bad = [c for c in body.selected if c not in config.SUPPORTED_LANGUAGES or c == "en"]
    if bad:
        raise HTTPException(400, f"Invalid target language(s): {', '.join(bad)}")
    db.set_setting(f"translate_langs_{episode_id}", list(dict.fromkeys(body.selected)))
    db.log_action(episode_id, "translate", status=f"languages set: {', '.join(body.selected) or 'none'}")
    return _build(db, episode_id)


@router.post("/clear/{episode_id}", response_model=OkResponse)
def clear(
    episode_id: int,
    lang: str = Query(..., description="Language code to clear"),
    panel_id: Optional[int] = Query(None, description="Clear just this panel; omit for the whole language"),
    db: Database = Depends(get_db),
):
    """
    Clear a translation so the next translate run regenerates it.
    Also invalidates the affected panel(s)' downstream audio.
    """
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    if panel_id is not None:
        invalidate_panel_downstream(db, panel_id, [lang])
        db.log_action(episode_id, "translate", status=f"regenerate {lang} · 1 panel")
        return OkResponse(ok=True, message=f"Cleared {lang} for panel {panel_id}")
    n = clear_language_translation(db, episode_id, lang)
    db.log_action(episode_id, "translate", status=f"regenerate {lang} · {n} panels")
    return OkResponse(ok=True, message=f"Cleared {lang} for {n} panels")
