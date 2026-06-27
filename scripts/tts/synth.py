"""
tts/synth.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
TTS backend dispatcher.

The dub engine generates audio by running a self-contained Python script in a
TTS subprocess.  Which engine that script targets — Qwen3-TTS or dots.tts — is
chosen *automatically from the voice's language*: Qwen3 where it supports the
language (higher quality), dots.tts for the rest of its 24-language roster.
There is no manual engine switch — see config.engine_for_language().

Both backends emit the SAME stdout protocol, so dub_engine's subprocess parsing
is identical regardless of backend:

    synth_python(profile) → the Python binary to run the script with
    synth_env(profile)    → environment for the subprocess
    build_synth_script(profile, …) → the script string (Qwen or dots)

Pass the same `profile` to all three so they agree on the engine.
"""

from __future__ import annotations

from typing import Set

import config
import runtime_settings as rs
from tts import script_builder as _qwen

# Reverse of SUPPORTED_LANGUAGES: display name ("French") → code ("fr").
_NAME_TO_CODE = {name.lower(): code for code, name in config.SUPPORTED_LANGUAGES.items()}


def _code_for(profile) -> str:
    name = (getattr(profile, "language", "") or "").strip().lower()
    if name in _NAME_TO_CODE:
        return _NAME_TO_CODE[name]
    return name if len(name) <= 3 else "en"   # already a code, else default


def engine_for(profile) -> str:
    """Engine ('qwen3' | 'dots') for a voice profile, from its language."""
    if profile is None:
        return "qwen3"
    return config.engine_for_language(_code_for(profile))


def synth_python(profile=None) -> str:
    if engine_for(profile) == "dots":
        return rs.get_str("dots_python", config.DOTS_PYTHON)
    return rs.get_str("conda_python", _qwen.CONDA_PYTHON)


def synth_env(profile=None) -> dict:
    if engine_for(profile) == "dots":
        from tts import dots_backend
        return dots_backend.dots_env()
    return _qwen.subprocess_env()


def build_worker_script(profile):
    """
    Resident-worker script (loads model once, serves generation requests off
    stdin) for engines that support it. Returns None when there's no worker for
    this engine — callers then fall back to the one-shot per-call path.

    Currently Qwen3 only; dots languages use the one-shot path until a dots
    worker is validated.
    """
    if engine_for(profile) == "qwen3":
        return _qwen.build_worker_script(profile)
    return None


def build_synth_script(
    profile,
    sentences:    list,
    output_paths: list,
    skip_indices: Set[int],
) -> str:
    """
    Build the TTS worker script for the engine that owns this voice's language.
    Drop-in replacement for build_chapter_script.
    """
    if engine_for(profile) == "dots":
        from tts import dots_backend
        return dots_backend.build_dots_script(
            profile, sentences, output_paths, skip_indices,
        )
    return _qwen.build_chapter_script(
        profile=profile, sentences=sentences,
        output_paths=output_paths, skip_indices=skip_indices,
    )
