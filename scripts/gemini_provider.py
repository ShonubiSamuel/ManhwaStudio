"""Google Gemini Developer API adapter for visual recap analysis."""

from __future__ import annotations

from typing import List, Tuple


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_VISION_MODEL = "gemini-3.5-flash-lite"


def call_vision(images_b64: List[Tuple[str, str]], prompt: str, api_key: str,
                max_tokens: int = 4096, model: str = "") -> str:
    """Analyze inline images with a finite timeout and return Gemini's text."""
    import httpx

    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("Gemini API key is missing — add it in Settings → AI & Providers.")
    mid = (model or DEFAULT_VISION_MODEL).strip()
    parts = [{"text": prompt}]
    parts.extend({"inline_data": {"mime_type": mime, "data": data}} for mime, data in images_b64)
    try:
        response = httpx.post(
            f"{GEMINI_BASE_URL}/models/{mid}:generateContent",
            # AI Studio now issues Authorization (AQ) keys. They must be passed
            # in this header, never embedded in a URL that might enter a log.
            headers={"x-goog-api-key": key},
            json={"contents": [{"role": "user", "parts": parts}], "generationConfig": {
                "temperature": 0.2, "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            }},
            timeout=httpx.Timeout(connect=15.0, read=90.0, write=60.0, pool=15.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Do not re-raise HTTPX's default message: it includes the full URL and
        # would disclose a query-string key in an application error/log.
        raise RuntimeError(f"Gemini request failed (HTTP {exc.response.status_code}).") from exc
    try:
        return "".join(part.get("text", "") for part in response.json()["candidates"][0]["content"]["parts"]
                       if isinstance(part, dict)).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini returned no usable response.") from exc
