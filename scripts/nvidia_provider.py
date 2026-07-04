"""
nvidia_provider.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
NVIDIA NIM API backend — Llama 3.3 70B text + Llama 3.2 90B Vision.

Free tier facts
───────────────
  • 40 RPM rolling window — not a daily quota.  Hit 429?  Wait 60 s.
  • Llama 3.3 70B context: 128K tokens.  A batch of 30 panels ≈ 3K tokens
    in + 4K out — enormous headroom.
  • At max_concurrent=6 + batch_size=30, a 600-panel refine uses ~3–4 RPM
    and a 9-language translate uses ~6 RPM.  Never approaches 40.
  • Llama 3.3 70B at concurrency 25 delivers 623 tok/s vs 51 tok/s
    at concurrency 1 — parallel requests are well-supported.

Retry behaviour
───────────────
  OpenAI client is built with max_retries=3.  This activates automatic
  exponential backoff on 429 (rate-limited) and 504 (gateway timeout)
  responses — no manual retry loops needed.

Reference
─────────
  https://docs.api.nvidia.com/
  https://build.nvidia.com/explore/discover
"""

from __future__ import annotations

from typing import List, Tuple

import config

# ── Provider defaults ─────────────────────────────────────────────────────────
# Starting points for NVIDIA free tier.  Override via Settings → API KEYS.

BATCH_SIZE     = 30   # panels per API call  (128K context — very generous)
MAX_CONCURRENT = 6    # parallel language workers  (safe within 40 RPM)
MAX_TOKENS     = 4096 # output token cap per call


def call_text(
    prompt:     str,
    api_key:    str,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """
    Single NVIDIA NIM text-model call (Llama 3.3 70B).

    Streams via the OpenAI-compatible endpoint at config.NVIDIA_BASE_URL.
    max_retries=3 on the client enables automatic exponential backoff on
    429 (rate limit) and 504 (gateway timeout) — no manual retry needed.

    Returns the full assembled response string.
    Raises openai.APIError on non-retryable failures.
    """
    import time
    import logging
    logger = logging.getLogger(__name__)
    start_time = time.time()
    logger.info(f"Requesting text model {config.NVIDIA_MODEL} (prompt: {len(prompt)} chars)...")

    from openai import OpenAI

    client = OpenAI(
        base_url    = config.NVIDIA_BASE_URL,
        api_key     = api_key,
        max_retries = 3,
        timeout     = 60.0,   # fail a hung/dropped stream fast instead of waiting forever
    )
    
    try:
        completion = client.chat.completions.create(
            model       = config.NVIDIA_MODEL,
            messages    = [{"role": "user", "content": prompt}],
            temperature = 0.2,
            top_p       = 0.7,
            max_tokens  = max_tokens,
            stream      = True,
        )
        result = ""
        for chunk in completion:
            if chunk.choices and chunk.choices[0].delta.content is not None:
                result += chunk.choices[0].delta.content
        duration = time.time() - start_time
        logger.info(f"Received text response ({len(result)} chars) in {duration:.2f}s")
        return result
    except Exception as exc:
        duration = time.time() - start_time
        logger.error(f"Text request failed after {duration:.2f}s: {exc}")
        raise


def call_vision(
    images_b64: List[Tuple[str, str]],   # [(mime_type, b64_data), ...]
    prompt:     str,
    api_key:    str,
    max_tokens: int = 2048,
) -> str:
    """
    Single NVIDIA NIM vision-model call (Llama 3.2 90B Vision).

    images_b64: list of (mime_type, base64_data) tuples — all images are
    sent in one message content array alongside the text prompt.

    Returns the full assembled response string.
    """
    import time
    import logging
    logger = logging.getLogger(__name__)
    start_time = time.time()
    logger.info(f"Requesting vision model {config.NVIDIA_VISION_MODEL} ({len(images_b64)} images, prompt: {len(prompt)} chars)...")

    from openai import OpenAI

    client = OpenAI(
        base_url    = config.NVIDIA_BASE_URL,
        api_key     = api_key,
        max_retries = 3,
    )
    content = []
    for mime, b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"}
        })
    content.append({"type": "text", "text": prompt})

    try:
        completion = client.chat.completions.create(
            model       = config.NVIDIA_VISION_MODEL,
            messages    = [{"role": "user", "content": content}],
            temperature = 0.2,
            top_p       = 0.7,
            max_tokens  = max_tokens,
            stream      = True,
        )
        result = ""
        for chunk in completion:
            if chunk.choices and chunk.choices[0].delta.content is not None:
                result += chunk.choices[0].delta.content
        duration = time.time() - start_time
        logger.info(f"Received vision response ({len(result)} chars) in {duration:.2f}s")
        return result
    except Exception as exc:
        duration = time.time() - start_time
        logger.error(f"Vision request failed after {duration:.2f}s: {exc}")
        raise


def validate_api_key(api_key: str) -> Tuple[bool, str]:
    """
    Basic format validation of a NVIDIA NIM API key.
    No network call — actual validation happens on first use.
    Returns (is_valid, error_message).  error_message is "" on success.
    """
    key = (api_key or "").strip()
    if not key:
        return False, "Key is empty"
    if len(key) < 20:
        return False, "Key looks too short — check you copied the full key"
    if not key.startswith("nvapi-"):
        return False, "NVIDIA keys should start with 'nvapi-'"
    return True, ""