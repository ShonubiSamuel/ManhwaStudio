"""
scripts/api/routers/dubbing.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Dubbing configuration + available voices.

The dub stage (ui/stages/dub_stage.py) reads three pieces of config:

    setting  dub_enabled_langs_{episode_id}   JSON list[str]   — langs to dub
    setting  dub_profiles_{episode_id}        JSON {lang:name} — voice per lang
    setting  dub_batch_size                   int (global)     — TTS batch size

This router is the typed front door to exactly those settings, plus the voice
catalogue from tts/voice_profile.VoiceProfileManager.  It does not change how
the dub stage behaves — it writes the same keys the stage already reads, so the
React Dubbing page and the runner stay in lock-step.

Endpoints
─────────
  GET   /api/voices                    available voice profiles
  GET   /api/dub/config/{episode_id}   enabled langs + per-lang profile + batch
  PATCH /api/dub/config/{episode_id}   update any of the above
"""

from __future__ import annotations

import json
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps   import get_db
from api.models import (
    VoiceInfo, DubLangOption, DubConfigResponse, DubConfigUpdate, OkResponse,
    DubBatch, DubBatchesResponse,
)
from pathlib import Path

from database   import Database
from dub.batch_manager import load_batch_state, save_batch_state, get_batch_state_path
from pipeline_logic import (
    clear_language_audio, clear_panel_audio,
    continuous_wav_path, languages_with_continuous,
)
from tts.voice_profile import VoiceProfileManager
import config

router = APIRouter(tags=["Dubbing"])

_DEFAULT_BATCH = 5


# ── Helpers ────────────────────────────────────────────────────────────────────

def _vpm() -> VoiceProfileManager:
    return VoiceProfileManager(str(config.VOICES_DIR))


def _enabled_key(episode_id: int) -> str:
    return f"dub_enabled_langs_{episode_id}"


def _profiles_key(episode_id: int) -> str:
    return f"dub_profiles_{episode_id}"


def _read_json_setting(db: Database, key: str, default):
    """get_setting already JSON-decodes; tolerate raw strings + bad data."""
    val = db.get_setting(key, default)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default
    return val if val is not None else default


def _langs_with_translation(db: Database, episode_id: int) -> set[str]:
    """Language codes that have at least one non-empty translated_text."""
    found: set[str] = set()
    for panel in db.list_panels(episode_id):
        for audio in db.list_panel_audio(panel["id"]):
            if (audio.get("translated_text") or "").strip():
                found.add(audio["lang_code"])
    return found


def _suggest_profiles(voices: List[str], codes: List[str]) -> Dict[str, str]:
    """
    Naming-convention auto-assignment, mirroring dub_stage:
    pick the first profile whose name ends with '_{code}', else leave unset.
    """
    suggested: Dict[str, str] = {}
    for code in codes:
        match = [p for p in voices if p.lower().endswith(f"_{code}")]
        if match:
            suggested[code] = match[0]
    return suggested


def _files_url(abs_path: Path) -> str:
    try:
        rel = abs_path.resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return "/files/" + rel.as_posix()
    except (ValueError, OSError):
        return ""


def _build_config(db: Database, episode_id: int) -> DubConfigResponse:
    ep           = db.get_episode(episode_id) or {}
    out_folder   = ep.get("output_folder") or ""
    voices       = _vpm().list_profiles()
    translated   = _langs_with_translation(db, episode_id)
    continuous   = set(languages_with_continuous(out_folder))
    enabled      = _read_json_setting(db, _enabled_key(episode_id), [])
    profiles     = _read_json_setting(db, _profiles_key(episode_id), {})
    if not isinstance(enabled, list):
        enabled = []
    if not isinstance(profiles, dict):
        profiles = {}

    # Self-heal: drop assignments that point at a voice that no longer exists
    # (e.g. the user deleted/renamed it). Otherwise the UI shows a ghost name and
    # any later save re-validates it and fails.
    known = set(voices)
    profiles = {c: n for c, n in profiles.items() if n in known}

    # Every supported language is dubbable; 'en' is the master voice.
    # has_continuous = Dub produced its continuous wav (the real "generated"
    # signal — per-panel clips only exist after Sync splits it).
    languages = [
        DubLangOption(
            code=code, name=name,
            has_translation=(code in translated or code == "en"),
            has_continuous=(code in continuous),
            continuous_url=(_files_url(continuous_wav_path(out_folder, code))
                            if code in continuous else ""),
        )
        for code, name in config.SUPPORTED_LANGUAGES.items()
    ]

    # Suggest profiles only for languages not already assigned.
    unassigned = [c for c in config.SUPPORTED_LANGUAGES if c not in profiles]
    suggested  = _suggest_profiles(voices, unassigned)

    try:
        batch = max(1, int(db.get_setting("dub_batch_size", _DEFAULT_BATCH)))
    except (TypeError, ValueError):
        batch = _DEFAULT_BATCH

    return DubConfigResponse(
        episode_id    = episode_id,
        languages     = languages,
        enabled_langs = [str(c) for c in enabled],
        profiles      = {str(k): str(v) for k, v in profiles.items()},
        suggested     = suggested,
        voices        = voices,
        batch_size    = batch,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/voices", response_model=List[VoiceInfo])
def list_voices():
    """Return every available voice profile with its key attributes."""
    vpm = _vpm()
    out: List[VoiceInfo] = []
    for name in vpm.list_profiles():
        p = vpm.load(name)
        if not p:
            continue
        out.append(VoiceInfo(
            name          = p.name,
            mode          = getattr(p, "mode", "") or "",
            language      = getattr(p, "language", "") or "",
            model         = getattr(p, "model", "") or "",
            speaker       = getattr(p, "speaker", "") or "",
            has_reference = bool(getattr(p, "ref_wav_path", "") or ""),
        ))
    return out


@router.get("/dub/config/{episode_id}", response_model=DubConfigResponse)
def get_dub_config(episode_id: int, db: Database = Depends(get_db)):
    """Return the dubbing configuration for an episode."""
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    return _build_config(db, episode_id)


@router.patch("/dub/config/{episode_id}", response_model=DubConfigResponse)
def update_dub_config(
    episode_id: int, body: DubConfigUpdate, db: Database = Depends(get_db)
):
    """
    Update any of: enabled languages, per-language voice profile, batch size.
    Writes the exact settings keys the dub stage reads, so changes take effect
    on the next dub run.  Validates that assigned profiles + languages exist.
    """
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")

    valid_langs = set(config.SUPPORTED_LANGUAGES)

    if body.enabled_langs is not None:
        bad = [c for c in body.enabled_langs if c not in valid_langs]
        if bad:
            raise HTTPException(400, f"Unknown language code(s): {', '.join(bad)}")
        db.set_setting(_enabled_key(episode_id), list(dict.fromkeys(body.enabled_langs)))

    if body.profiles is not None:
        known_voices = set(_vpm().list_profiles())
        for code in body.profiles:
            if code not in valid_langs:
                raise HTTPException(400, f"Unknown language code: {code}")
        # Keep only real assignments: a known voice, not the placeholder. Unknown
        # voices (deleted/stale, or a stale entry carried along in the payload)
        # are dropped silently rather than failing the whole save — so switching
        # one language never breaks because another still points at a dead voice.
        cleaned = {
            c: n for c, n in body.profiles.items()
            if n and n != "— none —" and n in known_voices
        }
        db.set_setting(_profiles_key(episode_id), cleaned)

    if body.batch_size is not None:
        db.set_setting("dub_batch_size", int(body.batch_size))

    db.log_action(episode_id, "dub", status="config saved")
    return _build_config(db, episode_id)


@router.post("/dub/clear/{episode_id}", response_model=OkResponse)
def clear_dub_audio(
    episode_id: int,
    lang: str = Query(..., description="Language code to clear audio for"),
    panel_id: int = Query(None, description="Clear just this panel; omit for the whole language"),
    db: Database = Depends(get_db),
):
    """
    Clear generated audio (keeping the translation) so the next dub run
    regenerates it. The dub runner is incremental, so clearing one panel and
    re-running regenerates exactly that clip.
    """
    if not db.get_episode(episode_id):
        raise HTTPException(404, f"Episode {episode_id} not found")
    if panel_id is not None:
        clear_panel_audio(db, panel_id, lang)
        db.log_action(episode_id, "dub", status=f"regenerate {lang} audio · 1 panel")
        return OkResponse(ok=True, message=f"Cleared {lang} audio for panel {panel_id}")
    n = clear_language_audio(db, episode_id, lang)
    db.log_action(episode_id, "dub", status=f"regenerate {lang} audio · {n} panels")
    return OkResponse(ok=True, message=f"Cleared {lang} audio ({n} panels)")


# ── Per-batch playback + regeneration ──────────────────────────────────────────

@router.get("/dub/batches/{episode_id}", response_model=DubBatchesResponse)
def list_dub_batches(
    episode_id: int,
    lang: str = Query(..., description="Language code"),
    db: Database = Depends(get_db),
):
    """
    Return the generated dub batches for a language (from dub/batch_state.json),
    each with its panel range, status, duration and a playable /files URL.

    This is what the Dub stage shows instead of one full-track player: you play
    batch-by-batch to find the one that sounds wrong, then regenerate just it.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")

    out_folder = ep.get("output_folder") or ""
    state      = load_batch_state(get_batch_state_path(out_folder))
    lang_state = state.get(lang, {}) if isinstance(state, dict) else {}
    lang_folder = Path(out_folder) / "dub" / lang

    batches = []
    for b in sorted(lang_state.get("batches", []), key=lambda x: x.get("idx", 0)):
        idx       = int(b.get("idx", 0))
        wav       = lang_folder / f"batch_{idx:04d}.wav"
        panels    = b.get("panels") or list(range(b.get("panel_from", 0), b.get("panel_to", 0) + 1))
        ready     = b.get("status") == "done" and wav.exists()
        batches.append(DubBatch(
            idx        = idx,
            panel_from = int(b.get("panel_from", panels[0] if panels else 0)),
            panel_to   = int(b.get("panel_to",   panels[-1] if panels else 0)),
            panels     = [int(p) for p in panels],
            status     = b.get("status", "pending"),
            duration   = round(float(b.get("duration", 0.0)), 2),
            audio_url  = _files_url(wav) if ready else "",
        ))

    profiles = _read_json_setting(db, _profiles_key(episode_id), {})
    try:
        batch_size = max(1, int(state.get("batch_size") or db.get_setting("dub_batch_size", _DEFAULT_BATCH)))
    except (TypeError, ValueError):
        batch_size = _DEFAULT_BATCH

    return DubBatchesResponse(
        episode_id = episode_id,
        lang       = lang,
        lang_name  = config.SUPPORTED_LANGUAGES.get(lang, lang.upper()),
        voice      = str((profiles or {}).get(lang, "") or lang_state.get("profile", "")),
        batch_size = batch_size,
        batches    = batches,
    )


@router.post("/dub/reset/{episode_id}", response_model=OkResponse)
def reset_dub_language(
    episode_id: int,
    lang: str = Query(..., description="Language code to reset"),
    db: Database = Depends(get_db),
):
    """
    Reset a language's dub state so the next dub run regenerates it from scratch.

    Deletes its batch wavs + the continuous/combined tracks, drops it from
    batch_state.json, and clears its panel audio (translations are kept).  This
    is what makes a plain "Run" actually re-dub a language: without it the dub
    runner sees the batches as already "done" and finishes instantly.
    """
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")
    if lang not in config.SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Unknown language code: {lang}")

    lang_folder = Path(ep.get("output_folder") or ".") / "dub" / lang
    if lang_folder.is_dir():
        for pat in ("batch_*.wav", "batch_*_state.json", "_continuous.wav", "_synced.wav"):
            for f in lang_folder.glob(pat):
                try: f.unlink()
                except OSError: pass

    state = load_batch_state(get_batch_state_path(ep.get("output_folder") or "."))
    if isinstance(state, dict) and lang in state:
        del state[lang]
        save_batch_state(get_batch_state_path(ep.get("output_folder") or "."), state)

    n = clear_language_audio(db, episode_id, lang)
    db.log_action(episode_id, "dub", status=f"reset {lang} for regeneration · {n} panels")
    return OkResponse(ok=True, message=f"Reset {lang} dub ({n} panels) — run Dub to regenerate")


