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
    "gemini_api_key":           "",
    "gemini_vision_model":      "gemini-3.5-flash-lite",
    "ai_provider_translate":    "nvidia",
    "ai_provider_refine":       "nvidia",
    "ai_provider_narrate":      "nvidia",   # PDF panels → narration (vision; Recap)
    "nvidia_translate_model":   config.NVIDIA_MODEL,          # NVIDIA text model for translation
    "nvidia_refine_model":      config.NVIDIA_MODEL,          # NVIDIA text model for AI Refine (+ Recap storyteller)
    "recap_batch_size":         "2",                          # panels per narration call (auto-capped per model)
    "groq_api_key":             "",
    "groq_model":               "llama-3.3-70b-versatile",
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
    # One provider + ONE task-scoped model picker per task (the *_model keys are
    # virtual in the UI: each writes the right provider-specific key). API keys
    # live at the bottom. LM Studio was removed from the product.
    "ai_providers": ["ai_provider_translate", "translate_model",
                     "ai_provider_refine", "refine_model",
                     "ai_provider_narrate", "narrate_model", "recap_batch_size",
                     "nvidia_api_key", "gemini_api_key", "groq_api_key",
                     "nvidia_batch_size", "nvidia_max_concurrent"],
    "voices_tts":   ["dots_weights_dir", "dots_num_steps",
                     "dots_guidance_scale", "dots_speaker_scale", "dots_seed",
                     "voice_ref_whisper_model", "tts_recommended_voice_design",
                     "tts_recommended_voice_clone", "tts_recommended_custom_voice"],
    # "detection" section removed: the video panel-detection pipeline it tuned
    # died with the old Pipeline page. Keys stay in DEFAULTS (harmless) so any
    # stored values are preserved if it ever comes back.
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


# ── Live model catalogs (curated per task) ────────────────────────────────────
# The Settings UI shows ONE model field per task (Translation / Refine / Vision)
# that follows the chosen provider and lists only models that FIT the task:
#   vision    → multimodal models only (accept image input)
#   translate → strong multilingual instruct LLMs
#   refine    → strong writing/reasoning LLMs (storytelling)
# NVIDIA/Groq catalogs are fetched live and filtered by curated family patterns.
# Free-text entry still works in the UI for brand-new ids.

_model_cache: dict = {}   # (provider, task) → {"at": ts, "models": [...]}
_MODEL_CACHE_TTL = 600    # 10 min

# Substring patterns of model FAMILIES that fit each task (matched on the id).
_NVIDIA_VISION = ("vl", "vision", "kimi", "llava", "neva", "fuyu", "kosmos",
                  "paligemma", "internvl", "florence", "maverick", "scout", "gemma-3")
_TEXT_FAMILIES = ("llama-3", "llama-4", "llama3", "qwen", "mistral", "mixtral",
                  "gemma", "deepseek", "glm", "kimi", "nemotron", "granite",
                  "jamba", "command", "phi-4", "gpt-oss", "yi-large", "seed-oss",
                  "exaone", "solar", "minimax")
# Never useful for narration/translation: embeddings, rerankers, guards, ASR/TTS…
_TEXT_EXCLUDE  = ("embed", "rerank", "guard", "safety", "clip", "ocr", "asr",
                  "tts", "riva", "parakeet", "canary", "bge-", "nv-embed",
                  "retriev", "moderat", "stable-diffusion", "sdxl", "genmol",
                  "molmim", "audio", "-base", "code", "coder", "math")


def _fits(mid: str, patterns: tuple) -> bool:
    m = mid.lower()
    return any(p in m for p in patterns)


@router.get("/models/{provider}")
def list_provider_models(provider: str, task: str = "refine", db: Database = Depends(get_db)):
    """Curated live model list for a provider + task ('translate'|'refine'|'vision').
    Returns {"models": [...], "cached": bool}. Empty list (never an error) when the
    provider is unreachable or keyless — the UI falls back to free text."""
    import time
    import httpx

    provider, task = provider.lower(), task.lower()
    ck = (provider, task)
    hit = _model_cache.get(ck)
    if hit and time.time() - hit["at"] < _MODEL_CACHE_TTL:
        return {"models": hit["models"], "cached": True}

    models: list = []
    try:
        if provider == "nvidia":
            key = db.get_setting("nvidia_api_key", "") or ""
            r = httpx.get(f"{config.NVIDIA_BASE_URL.rstrip('/')}/models",
                          headers={"Authorization": f"Bearer {key}"} if key else {},
                          timeout=15.0)
            r.raise_for_status()
            ids = sorted({m.get("id", "") for m in r.json().get("data", [])} - {""})
            if task == "vision":
                models = [m for m in ids if _fits(m, _NVIDIA_VISION)
                          and not _fits(m, ("embed", "clip", "ocr", "rerank"))]
            else:
                models = [m for m in ids if _fits(m, _TEXT_FAMILIES)
                          and not _fits(m, _TEXT_EXCLUDE + ("-vl", "vision", "llava"))]
            models = models or ids            # never return nothing if the fetch worked

        elif provider == "gemini":
            # Stable public Gemini vision model IDs; no catalog request needed.
            models = ["gemini-3.5-flash-lite", "gemini-3.5-flash"] if task == "vision" else []

        elif provider == "groq":
            gk = db.get_setting("groq_api_key", "") or ""
            if gk:
                r = httpx.get("https://api.groq.com/openai/v1/models",
                              headers={"Authorization": f"Bearer {gk}"}, timeout=15.0)
                r.raise_for_status()
                ids = sorted({m.get("id", "") for m in r.json().get("data", [])} - {""})
                bad = ("whisper", "tts", "guard", "embed", "allam")
                if task == "vision":
                    models = [m for m in ids if _fits(m, ("vision", "maverick", "scout", "-vl"))]
                else:
                    models = [m for m in ids if not _fits(m, bad + ("vision",))]
            else:                              # no key yet → known-good defaults
                models = ([] if task == "vision" else
                          ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                           "qwen/qwen3-32b", "moonshotai/kimi-k2-instruct",
                           "openai/gpt-oss-120b", "openai/gpt-oss-20b"])
    except Exception:
        models = []   # unreachable / bad key → UI just uses the free-text input

    if models:
        _model_cache[ck] = {"at": time.time(), "models": models}
    return {"models": models, "cached": False}
