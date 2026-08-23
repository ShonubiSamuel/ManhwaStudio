"""
scripts/ai/openai_compat.py — generic OpenAI-compatible chat providers.

One tiny client for every service that speaks the OpenAI chat-completions
protocol behind a different base URL. Groq is the only registered provider:

    groq    Groq Cloud                 https://api.groq.com/openai/v1

Tokens/models are read from runtime settings at call time so the caller only
needs a provider NAME — no per-call-site key plumbing:

    groq_api_key,  groq_model    (default llama-3.3-70b-versatile)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PROVIDERS = {
    "groq": {
        "base_url":      "https://api.groq.com/openai/v1",
        "key_setting":   "groq_api_key",
        "model_setting": "groq_model",
        "default_model": "llama-3.3-70b-versatile",
        "label":         "Groq",
    },
}


def _client_and_model(provider: str):
    import runtime_settings as rs
    from openai import OpenAI
    p = PROVIDERS.get(provider)
    if not p:
        raise RuntimeError(f"Unsupported OpenAI-compatible provider: {provider}")
    key = rs.get_str(p["key_setting"], "")
    if not key:
        raise RuntimeError(f"{p['label']}: no API token set — add it in Settings → AI & Providers.")
    model = rs.get_str(p["model_setting"], "") or p["default_model"]
    return OpenAI(base_url=p["base_url"], api_key=key, max_retries=3), model


def call_text(prompt: str, provider: str, max_tokens: int = 4096) -> str:
    """Single text completion via an OpenAI-compatible provider."""
    client, model = _client_and_model(provider)
    logger.info(f"[{provider}] text model {model} (prompt: {len(prompt)} chars)")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.4,
    )
    return (resp.choices[0].message.content or "").strip()


def call_vision(prompt: str, images_b64: list, provider: str = "groq",
                max_tokens: int = 4096, model_override: str = "") -> str:
    """One vision call with N inline JPEG/PNG images (OpenAI image_url parts)."""
    client, model = _client_and_model(provider)
    if model_override:
        model = model_override
    content = [{"type": "text", "text": prompt}]
    for b64 in images_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    logger.info(f"[{provider}] vision model {model} ({len(images_b64)} images)")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=max_tokens,
        temperature=0.5,
    )
    return (resp.choices[0].message.content or "").strip()
