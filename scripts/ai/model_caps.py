"""
scripts/ai/model_caps.py — per-model capability table + auto-batching.

Every model has TWO independent ceilings that matter to us:

  • max_images  — how many images it will accept in ONE request (INPUT side).
                  Some vision models take 1, others take many. We must never
                  send more than this — and we also never want to send more than
                  the user's preference (more panels per call = the model starts
                  summarising / skipping).
  • max_output  — how many tokens it can WRITE back in one reply (OUTPUT side).
                  This is what got truncated when it was too small.

These are per-MODEL (not per-provider), and the hosting provider can cap output
further, so we keep a small table of the models we actually use and fall back
conservatively for anything unknown (assume 1 image, modest output — safe).

The effective panels-per-call is:  min(user preference, vision model max_images)
so a 1-image model auto-drops to 1 while a capable model still honours your
chosen batch (default 2).
"""

from __future__ import annotations

# Keyed by the exact model id used in the API `model` field. Add new models here.
MODEL_CAPS: dict = {
    # ── Multimodal (can be BOTH the "Eyes" AND the "Storyteller") ──
    "moonshotai/kimi-k2.6":                 {"max_images": 8,  "max_output": 16000, "vision": True},

    # ── Vision-capable (used for the Recap "Eyes" call) ──
    "openai/gpt-4.1":                       {"max_images": 10, "max_output": 32000, "vision": True},
    "openai/gpt-4.1-mini":                  {"max_images": 10, "max_output": 32000, "vision": True},
    "openai/gpt-4o":                        {"max_images": 10, "max_output": 16000, "vision": True},
    "openai/gpt-4o-mini":                   {"max_images": 8,  "max_output": 16000, "vision": True},

    # ── Text reasoners (used for the Recap "Storyteller" + refine/translate) ──
    "z-ai/glm-5.2":                         {"max_images": 0, "max_output": 32000, "vision": False},
    "z-ai/glm-5.2[1m]":                     {"max_images": 0, "max_output": 32000, "vision": False},
    "z-ai/glm-4.7":                         {"max_images": 0, "max_output": 32000, "vision": False},
    "meta/llama-3.1-70b-instruct":          {"max_images": 0, "max_output": 4096,  "vision": False},
    "meta/llama-3.3-70b-instruct":          {"max_images": 0, "max_output": 8000,  "vision": False},
}

# Conservative fallbacks for models not in the table.
_FALLBACK_VISION = {"max_images": 1, "max_output": 4096, "vision": True}
_FALLBACK_TEXT   = {"max_images": 0, "max_output": 4096, "vision": False}


def caps_for(model_id: str, *, vision: bool) -> dict:
    """Capabilities for a model id, with a safe fallback (unknown vision model →
    1 image, so we never overwhelm it)."""
    c = MODEL_CAPS.get((model_id or "").strip())
    if c:
        return c
    return dict(_FALLBACK_VISION if vision else _FALLBACK_TEXT)


def effective_batch(user_pref: int, vision_model: str) -> int:
    """Panels per call = min(user preference, what the vision model accepts)."""
    cap = caps_for(vision_model, vision=True)["max_images"] or 1
    return max(1, min(int(user_pref or 1), cap))


def output_cap(model_id: str, *, vision: bool, want: int = 8000) -> int:
    """A safe max_tokens for `model_id`: what we WANT, clamped to what it allows."""
    return max(256, min(int(want), caps_for(model_id, vision=vision)["max_output"]))
