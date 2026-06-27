"""
scripts/api/routers/settings.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Settings endpoints.

Mirrors the exact key/value pattern from settings_tab.py:
  db.get_setting(key, default)   — read one value, falling back to config.py
  db.set_setting(key, value)     — write one value
  db.get_all_settings()          — read every stored row (sparse — only keys
                                    the user has actually changed from default)

Endpoints
─────────
  GET   /api/settings        full settings object — every known key, with
                              defaults from config.py applied for anything
                              not yet saved in the DB (matches settings_tab.py
                              first-run behaviour exactly)
  PATCH /api/settings        update one or more keys — body is a flat
                              { key: value } dict, only supplied keys change

DEFAULTS below is copied 1-to-1 from settings_tab.py's _DEFAULTS dict —
same keys, same config.py fallbacks, same section grouping — so the new UI
and the old Tkinter Settings tab always agree on what a "default" is.
"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends

from api.deps import get_db
from database import Database
import config

router = APIRouter(prefix="/settings", tags=["Settings"])


# ── Defaults — mirrors settings_tab.py _DEFAULTS exactly ─────────────────────

DEFAULTS: Dict[str, object] = {
    # ── AI & Providers ──
    "nvidia_api_key":           "",
    "nvidia_vision_model":      config.NVIDIA_VISION_MODEL,
    "ai_provider_translate":    "nvidia",
    "ai_provider_refine":       "nvidia",
    "nvidia_batch_size":        "30",
    "nvidia_max_concurrent":    "6",
    "lm_studio_url":            "http://localhost:1234/v1",
    "lm_studio_model":          "",
    "lm_studio_context_length": "32768",
    "lm_studio_max_concurrent": "4",
    "lm_studio_batch_size":     "6",

    # ── Voices & TTS ──
    # (No engine selector — the engine is auto-chosen per language:
    #  Qwen3 where it has the language, dots.tts for the rest.)
    "dots_weights_dir":         str(getattr(config, "DOTS_WEIGHTS_DIR", "")),
    "dots_num_steps":           "",   # blank = auto (4 for MeanFlow mf-*, 10 for soar)
    "dots_guidance_scale":      str(getattr(config, "DOTS_GUIDANCE_SCALE", 1.2)),
    "dots_speaker_scale":       str(getattr(config, "DOTS_SPEAKER_SCALE", 1.5)),
    "dots_seed":                str(getattr(config, "DOTS_SEED", 42)),
    "voice_ref_whisper_model":  getattr(config, "VOICE_REF_WHISPER_MODEL", "large-v3"),
    "tts_recommended_voice_design": config.TTS_RECOMMENDED_MODELS.get("VoiceDesign", "1.7B-VoiceDesign"),
    "tts_recommended_voice_clone":  config.TTS_RECOMMENDED_MODELS.get("VoiceClone",  "1.7B-Base"),
    "tts_recommended_custom_voice": config.TTS_RECOMMENDED_MODELS.get("CustomVoice", "1.7B-CustomVoice"),

    # ── Detection ──
    "detect_mode":              config.DETECT_MODE,
    "detect_silence_db":        str(config.DETECT_SILENCE_DB),
    "detect_min_silence":       str(config.DETECT_MIN_SILENCE),
    "detect_threshold":         str(config.DETECT_THRESHOLD),
    "detect_min_scene":         str(config.DETECT_MIN_SCENE),
    "detect_frame_skip":        str(config.DETECT_FRAME_SKIP),
    "detect_merge_window":      str(config.DETECT_MERGE_WINDOW),
    "detect_priority":          str(config.DETECT_PRIORITY),
    "detect_workers":           str(config.DETECT_WORKERS),
    "whisper_model":            config.WHISPER_MODEL,
    "screenshot_offset":        str(config.SCREENSHOT_OFFSET),

    # ── Dubbing & Sync (quality knobs) ──
    "dub_max_stretch":          str(getattr(config, "DUB_MAX_STRETCH", 1.20)),
    "dub_hard_stretch":         str(getattr(config, "DUB_HARD_STRETCH", 4.0)),
    "dub_mild_stretch":         str(getattr(config, "DUB_MILD_STRETCH", 1.15)),
    "dub_auto_fix_rushed":      getattr(config, "DUB_AUTO_FIX_RUSHED", True),
    "dub_fix_attempts":         str(getattr(config, "DUB_FIX_ATTEMPTS", 3)),
    "translate_len_budget":     str(getattr(config, "TRANSLATE_LEN_BUDGET", 0.95)),
    "translate_len_budget_cjk": str(getattr(config, "TRANSLATE_LEN_BUDGET_CJK", 0.55)),
    "translate_fit_iters":      str(getattr(config, "TRANSLATE_FIT_ITERS", 3)),
    "translate_fit_floor":      str(getattr(config, "TRANSLATE_FIT_FLOOR", 0.45)),
    "translate_len_enforce":    getattr(config, "TRANSLATE_LEN_ENFORCE", True),
    "en_chars_per_sec":         str(getattr(config, "EN_CHARS_PER_SEC", 14.0)),
    "dub_whisper_model":        config.DUB_WHISPER_MODEL,
    "dub_snap_window_ms":       str(config.DUB_SNAP_WINDOW_MS),
    "dub_normalize_rms":        str(config.DUB_NORMALIZE_RMS),
    "dub_continuous_timeout":   str(config.DUB_CONTINUOUS_TIMEOUT),
    "keep_background_music":    getattr(config, "KEEP_BACKGROUND_MUSIC", True),
    "dub_voice_gain":           str(getattr(config, "DUB_VOICE_GAIN", 1.0)),
    "dub_music_gain":           str(getattr(config, "DUB_MUSIC_GAIN", 0.8)),
    "cue_whisper_model":        getattr(config, "CUE_WHISPER_MODEL", "small"),
    "dub_cue_batch":            str(getattr(config, "DUB_CUE_BATCH", 8)),
    "dub_read_max_sec":         str(getattr(config, "DUB_READ_MAX_SEC", 30)),
    "dub_speech_max_stretch":   str(getattr(config, "DUB_SPEECH_MAX_STRETCH", 1.5)),
    "dub_fade_in_ms":           str(getattr(config, "DUB_FADE_IN_MS", 25)),
    "dub_fade_out_ms":          str(getattr(config, "DUB_FADE_OUT_MS", 80)),

    # ── PDF Import (slicer + optimizer) ──
    "narr_mode":                config.NARR_MODE,
    "narr_slice_height":        str(config.NARR_SLICE_HEIGHT),
    "narr_merge_count":         str(config.NARR_MERGE_COUNT),
    "narr_images_per_batch":    str(config.NARR_IMAGES_PER_BATCH),
    "pdf_dpi":                  str(config.PDF_DPI),
    "pdf_skip_first_last":      config.PDF_SKIP_FIRST_LAST,
    "pdf_jpeg_quality":         str(config.PDF_JPEG_QUALITY),
    "opt_compression_mode":     config.OPT_COMPRESSION_MODE,
    "opt_jpeg_quality":         str(config.OPT_JPEG_QUALITY),
    "opt_target_kb":            str(config.OPT_TARGET_KB),
    "opt_min_quality":          str(config.OPT_MIN_QUALITY),
    "opt_max_width":            str(config.OPT_MAX_WIDTH),
    "opt_grayscale":            config.OPT_GRAYSCALE,
    "opt_autocrop":             config.OPT_AUTOCROP,
    "opt_sharpen":              config.OPT_SHARPEN,

    # ── Advanced (paths) ──
    "conda_python":             str(getattr(config, "CONDA_PYTHON", "")),
    "dots_python":              str(getattr(config, "DOTS_PYTHON", "")),
    "demucs_python":            str(getattr(config, "DEMUCS_PYTHON", "")),
    "demucs_model":             getattr(config, "DEMUCS_MODEL", "htdemucs"),
}

# Section grouping — six domain groups the UI renders as tabs.
SECTIONS: Dict[str, list] = {
    "ai_providers": ["nvidia_api_key", "nvidia_vision_model",
                     "ai_provider_translate", "ai_provider_refine",
                     "nvidia_batch_size", "nvidia_max_concurrent",
                     "lm_studio_url", "lm_studio_model", "lm_studio_context_length",
                     "lm_studio_max_concurrent", "lm_studio_batch_size"],
    "voices_tts":   ["dots_weights_dir", "dots_num_steps",
                     "dots_guidance_scale", "dots_speaker_scale", "dots_seed",
                     "voice_ref_whisper_model", "tts_recommended_voice_design",
                     "tts_recommended_voice_clone", "tts_recommended_custom_voice"],
    "detection":    [k for k in DEFAULTS if k.startswith(("detect_", "whisper_", "screenshot_"))],
    "dubbing_sync": ["dub_max_stretch", "dub_hard_stretch", "dub_mild_stretch",
                     "dub_auto_fix_rushed", "dub_fix_attempts",
                     "translate_len_budget", "translate_len_budget_cjk",
                     "translate_fit_iters", "translate_fit_floor", "translate_len_enforce",
                     "en_chars_per_sec", "dub_whisper_model", "dub_snap_window_ms",
                     "dub_normalize_rms", "dub_continuous_timeout",
                     "keep_background_music", "dub_voice_gain", "dub_music_gain",
                     "cue_whisper_model", "dub_cue_batch", "dub_read_max_sec",
                     "dub_speech_max_stretch", "dub_fade_in_ms", "dub_fade_out_ms"],
    "pdf_import":   [k for k in DEFAULTS if k.startswith(("narr_", "pdf_", "opt_"))],
    "advanced":     ["conda_python", "dots_python", "demucs_python", "demucs_model"],
}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
def get_settings(db: Database = Depends(get_db)):
    """
    Return every known setting, with DB-saved values taking priority over
    config.py defaults — identical first-run behaviour to settings_tab.py.

    Response shape:
        {
          "values":   { key: value, ... }   — every key in DEFAULTS, resolved
          "sections": { section: [keys] }    — for grouping the UI into tabs
        }
    """
    stored = db.get_all_settings()
    values = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
    return {"values": values, "sections": SECTIONS}


@router.patch("")
def update_settings(body: Dict[str, object], db: Database = Depends(get_db)):
    """
    Update one or more settings.

    Body is a flat { key: value } dict — only the supplied keys are written.
    Unknown keys (not in DEFAULTS) are still saved, since db.set_setting()
    is generic — this keeps the endpoint forward-compatible with new
    settings added to config.py without requiring an API change.
    """
    for key, value in body.items():
        db.set_setting(key, value)

    # Let config-reading modules (runtime_settings) pick up changes immediately.
    try:
        import runtime_settings
        runtime_settings.invalidate()
    except Exception:
        pass

    stored = db.get_all_settings()
    values = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
    return {"values": values, "sections": SECTIONS}
