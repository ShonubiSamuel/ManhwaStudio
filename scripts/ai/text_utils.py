"""
ai/text_utils.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Shared AI response parsing utilities + provider router.

Extracted from ai_engine.py module level.  All functions here are pure
(no network calls, no DB) except _call() which routes to the selected
backend provider.

Public API
──────────
    strip_markdown_fences(text)         → str
    strip_thinking_blocks(text)         → str
    safe_json_loads(text)               → any
    extract_json_object(text)           → str
    extract_json_array(text)            → str
    parse_translation_response(...)     → dict
    reorder_languages(lang_codes)       → list
    build_lang_map(lang_codes)          → dict
    call_provider(prompt, ...)          → str
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

import config
import nvidia_provider
import lmstudio_provider


# ── CJK priority ──────────────────────────────────────────────────────────────

_CJK_PRIORITY = ("zh", "ja", "ko")


# ── Text cleaning ─────────────────────────────────────────────────────────────

def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers the model may add."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()


def strip_thinking_blocks(text: str) -> str:
    """
    Remove <think>...</think> reasoning blocks produced by Qwen3 and other
    reasoning models.  Run BEFORE strip_markdown_fences.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ── JSON parsing ──────────────────────────────────────────────────────────────

def safe_json_loads(text: str):
    """
    Parse JSON robustly, handling two common model misbehaviours:

    1. Preamble/postamble text around the JSON.
    2. Multiple JSON objects instead of one combined object — merged into
       a single dict.

    Raises json.JSONDecodeError if no valid JSON can be parsed.
    """
    text      = text.strip()
    _first_exc = None

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Capture the original exception so we can re-raise it if the
        # multi-object fallback also fails.  We do NOT check the exception
        # message for "Extra data" here because that string is specific to
        # CPython's json implementation and may not appear in other Python
        # runtimes or future CPython versions.  Instead, always attempt the
        # multi-object extraction — raw_decode handles all cases cleanly.
        _first_exc = exc

    decoder   = json.JSONDecoder()
    merged:   dict = {}
    pos       = 0
    found_any = False

    while pos < len(text):
        remaining = text[pos:].lstrip()
        offset    = len(text[pos:]) - len(remaining)
        if not remaining.startswith("{"):
            break
        try:
            obj, rel_end = decoder.raw_decode(remaining)
            if isinstance(obj, dict):
                merged.update(obj)
                found_any = True
            pos += offset + rel_end
        except json.JSONDecodeError:
            break

    if found_any:
        return merged
    raise _first_exc


def extract_json_object(text: str) -> str:
    """Extract the first {...} JSON object from text, ignoring preamble."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def extract_json_array(text: str) -> str:
    """Extract the first [...] JSON array from text, ignoring preamble."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group(0) if match else text


# ── Language helpers ──────────────────────────────────────────────────────────

def reorder_languages(target_languages: List[str]) -> List[str]:
    """
    Put CJK languages first.

    Note: this function is kept for callers that process languages
    sequentially and benefit from CJK-first ordering.  The flat-pool
    translation functions (translator.py) do NOT call this because all
    tasks are submitted simultaneously — ordering only affects which tasks
    enter the queue first, which provides negligible real-world benefit.
    """
    return (
        [lc for lc in _CJK_PRIORITY if lc in target_languages] +
        [lc for lc in target_languages if lc not in _CJK_PRIORITY]
    )


def build_lang_map(target_languages: List[str]) -> Dict[str, str]:
    """Build {lang_code: display_name} using config.SUPPORTED_LANGUAGES."""
    return {
        lc: config.SUPPORTED_LANGUAGES.get(lc, lc.upper())
        for lc in target_languages
    }


# ── Translation response parser ───────────────────────────────────────────────

def parse_translation_response(
    raw_response: str,
    all_keys:     List[str],
    n_panels:     int,
    on_log:       Callable,
) -> dict:
    """
    Parse a multi-language translation response.
    Expects JSON: {"en": [...N...], "zh": [...N...], ...}
    Missing keys are logged; wrong counts are auto-corrected.
    Raises RuntimeError on unparseable JSON.
    """
    cleaned = strip_markdown_fences(raw_response)
    cleaned = extract_json_object(cleaned)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Provider returned invalid JSON: {exc}\n"
            f"First 500 chars: {cleaned[:500]}"
        )

    if not isinstance(result, dict):
        raise RuntimeError(f"Expected JSON object, got {type(result).__name__}")
    if "en" not in result or not isinstance(result.get("en"), list):
        raise RuntimeError("Response missing 'en' array — check model output")

    validated: dict = {}
    for key in all_keys:
        if key not in result:
            on_log(f"  Warning: '{key}' missing from response — skipping", "warning")
            continue
        val = result[key]
        if not isinstance(val, list):
            on_log(f"  Warning: '{key}' is not a list — skipping", "warning")
            continue
        n_got = len(val)
        if n_got != n_panels:
            on_log(
                f"  Warning: '{key}' has {n_got} elements "
                f"(expected {n_panels}) — auto-adjusting",
                "warning",
            )
            val = (val[:n_panels] if n_got > n_panels
                   else val + [""] * (n_panels - n_got))
        validated[key] = val
    return validated


# ── Provider router ───────────────────────────────────────────────────────────

def call_provider(
    prompt:         str,
    provider:       str,
    api_key:        str = "",
    lm_model:       str = "",
    max_tokens:     int = 4096,
    context_length: int = lmstudio_provider.CONTEXT_LENGTH,
    task:           str = "translate",   # translate | refine | narrate (NVIDIA model pick)
) -> str:
    """Route a single prompt to the correct backend provider.

    `task` selects which per-task model to use for NVIDIA — "translate",
    "refine" or "narrate" → nvidia_<task>_model in Settings (falls back to the
    config default). Groq reads its own model setting.
    """
    if provider == "lm_studio":
        return lmstudio_provider.call_text(
            prompt, lm_model, max_tokens, context_length
        )
    if provider == "groq":
        from ai import openai_compat
        return openai_compat.call_text(prompt, provider, max_tokens)
    # NVIDIA: pick the per-task model chosen in Settings.
    import runtime_settings as rs
    nv_model = rs.get_str(f"nvidia_{task}_model", "") or config.NVIDIA_MODEL
    return nvidia_provider.call_text(prompt, api_key, max_tokens, model=nv_model)
