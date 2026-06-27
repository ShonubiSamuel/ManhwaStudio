"""
ai_engine.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Thin public API.  Routes all AI work to the correct module:

  ai/text_utils.py   — JSON parsing, provider router
  ai/translator.py   — parallel translation (Gen 3, live system)
  ai/narrator.py     — refine_transcript, narrate_with_vision

  nvidia_provider.py  — NVIDIA NIM cloud backend
  lmstudio_provider.py— LM Studio local backend

All internal implementation has been moved to the modules above.
This file exists solely so the rest of the app can continue to do:
    from ai_engine import refine_transcript, translate_subset_parallel, ...

DELETED (Generation 1 — single-call NVIDIA pipeline):
    clean_and_translate()
    apply_tone_and_translate()
    _run_text_call()

DELETED (Generation 2 — sequential per-language):
    translate_panels_for_language()

DELETED (Generation 3 duplicate):
    translate_panels_parallel()   → use translate_subset_parallel instead
"""

from __future__ import annotations

from typing import Tuple

import lmstudio_provider
import nvidia_provider

# ── Re-exports — the rest of the app imports from here ───────────────────────

from ai.narrator    import refine_transcript, narrate_with_vision        # noqa: F401
from ai.translator  import translate_subset_parallel                      # noqa: F401
from ai.translator  import translate_panels_parallel                      # noqa: F401  (keep for dub_tab compat)
from ai.text_utils  import (                                              # noqa: F401
    strip_markdown_fences,
    strip_thinking_blocks,
    safe_json_loads,
    reorder_languages,
    build_lang_map,
    parse_translation_response,
)


# ── LM Studio lifecycle ───────────────────────────────────────────────────────

def load_lmstudio_model(
    base_url:       str,
    model_name:     str,
    context_length: int = lmstudio_provider.CONTEXT_LENGTH,
) -> bool:
    """
    Load model_name into LM Studio memory.
    base_url is accepted for API compatibility but not used — the SDK
    auto-discovers the correct port.
    Returns True on success.
    """
    return lmstudio_provider.load_model(model_name, context_length)


def unload_lmstudio_model(
    model_name: str,
    base_url:   str = "",
) -> bool:
    """
    Unload model_name from LM Studio memory, freeing GPU/CPU RAM.
    Returns True on success.
    """
    return lmstudio_provider.unload_model(model_name)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_api_key(api_key: str) -> Tuple[bool, str]:
    """
    Basic format validation of a NVIDIA NIM API key.
    No network call.  Returns (is_valid, error_message).
    """
    return nvidia_provider.validate_api_key(api_key)
