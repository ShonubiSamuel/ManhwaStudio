"""
lmstudio_provider.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
LM Studio local AI backend.

Connection
──────────
  Uses the lmstudio Python SDK (pip install lmstudio) which communicates
  via WebSocket — NOT the OpenAI HTTP REST port.  The SDK auto-discovers
  the correct port via find_default_local_api_host(), so the user only
  needs to keep LM Studio open; no manual port configuration required.

Memory management
─────────────────
  call_text() opens a short-lived client connection and will trigger model
  loading if the model isn't already in RAM.  For predictable behaviour,
  pipeline stages call load_model() explicitly before inference and
  unload_model() immediately after, so GPU RAM is only occupied during
  active AI work:

    REFINE     → load_model → call_text (×N batches) → unload_model
    TRANSLATE  → load_model → call_text (×N batches) → unload_model
    UPSCALE    → (model not in RAM — full 24 GB available to Real-ESRGAN)
    DETECT     → (model not in RAM — Whisper has full memory)

Parallelism
───────────
  call_text() opens its own short-lived client per call — safe to call
  from multiple threads simultaneously.  This is how parallel batch
  processing works in refine and translate.  Set LM Studio's
  "Max Concurrent Predictions" in the model loader to match
  max_concurrent for best throughput.

Reference
─────────
  https://lmstudio.ai/docs/python-sdk
"""

from __future__ import annotations

# ── Provider defaults ─────────────────────────────────────────────────────────
# Conservative defaults for a 24 GB M4 Pro running Qwen3 35B A3B.
# Override via Settings → LM STUDIO.

BATCH_SIZE     = 6      # panels per call (smaller — local context is tighter)
MAX_CONCURRENT = 4      # workers (match LM Studio's Max Concurrent Predictions)
MAX_TOKENS     = 6096   # output token cap
CONTEXT_LENGTH = 32768  # tokens loaded into model context window


def _find_host() -> str:
    """
    Locate the LM Studio SDK WebSocket port.
    Raises RuntimeError  if LM Studio is not running.
    Raises ImportError   if the lmstudio package is not installed.
    """
    import lmstudio as lms
    host = lms.Client.find_default_local_api_host()
    if not host:
        raise RuntimeError(
            "Cannot reach LM Studio — make sure the app is open "
            "and running before using the LM Studio provider."
        )
    return host


def call_text(
    prompt:         str,
    model:          str,
    max_tokens:     int = MAX_TOKENS,
    context_length: int = CONTEXT_LENGTH,
) -> str:
    """
    Single LM Studio inference call via the Python SDK.

    Opens a short-lived client connection per call — safe to call from
    multiple threads simultaneously for parallel batch processing.

    The model should already be loaded for low latency (call load_model()
    before the batch loop).  If not loaded, the SDK loads it automatically
    but this adds several seconds of latency to the first call.

    Returns the full response string.
    Raises RuntimeError  if LM Studio is unreachable.
    Raises ImportError   if lmstudio SDK is not installed.
    """
    import lmstudio as lms
    import time
    import logging
    logger = logging.getLogger(__name__)
    start_time = time.time()
    logger.info(f"Requesting LM Studio model {model or 'default'} (prompt: {len(prompt)} chars)...")

    try:
        host = _find_host()
        with lms.Client(host) as client:
            lm_model = client.llm.model(
                model or "default",
                config={"contextLength": context_length},
            )
            result = lm_model.respond(
                prompt,
                config={
                    "maxTokens":   max_tokens,
                    "temperature": 0.2,
                    "topP":        0.7,
                },
            )
            duration = time.time() - start_time
            logger.info(f"Received text response ({len(result)} chars) in {duration:.2f}s")
            return str(result)
    except Exception as exc:
        duration = time.time() - start_time
        logger.error(f"LM Studio text request failed after {duration:.2f}s: {exc}")
        raise


def load_model(
    model_name:     str,
    context_length: int = CONTEXT_LENGTH,
) -> bool:
    """
    Load model_name into LM Studio memory.

    If the model is already loaded the SDK silently returns the existing
    instance — context_length is ignored in that case.  To force a new
    context window size, unload first via the LM Studio UI or unload_model().

    Returns True on success.
    Raises RuntimeError  if LM Studio is unreachable.
    Raises ImportError   if lmstudio SDK is not installed.
    """
    import lmstudio as lms

    host = _find_host()
    with lms.Client(host) as client:
        client.llm.model(
            model_name,
            config={"contextLength": context_length},
        )
    return True


def unload_model(model_name: str) -> bool:
    """
    Unload model_name from LM Studio memory, freeing GPU/CPU RAM immediately.

    Call this after REFINE or TRANSLATE finishes so that UPSCALE, DETECT,
    and TTS stages have the full system memory available.

    Returns True on success.
    Raises RuntimeError  if LM Studio is unreachable.
    Raises ImportError   if lmstudio SDK is not installed.
    """
    import lmstudio as lms

    host = _find_host()
    with lms.Client(host) as client:
        # The original implementation called client.llm.model(model_name)
        # to get a handle before unloading.  That call triggers loading the
        # model into RAM if it is not already there — the opposite of what
        # unload_model is supposed to do.
        #
        # Fix: use client.llm.list_loaded() to inspect what is currently in
        # memory without triggering any load.  If the model is found, call
        # .unload() on the already-resident handle.  If it is not found,
        # return True immediately — the model is already absent, so the
        # caller's goal (freeing RAM) is already achieved.
        #
        # Fallback: if list_loaded() is unavailable in this SDK version,
        # catch the AttributeError and fall back to the original approach.
        # In that edge case load-then-unload is still wasteful but correct.
        try:
            loaded_models = client.llm.list_loaded()
            for m in loaded_models:
                # model_key is the canonical identifier in the LM Studio SDK.
                # We also check the string representation as a last resort for
                # forwards compatibility with future SDK versions.
                key = getattr(m, "model_key", None) or str(m)
                if model_name in str(key) or str(key) in model_name:
                    m.unload()
                    return True
            # Model was not in the loaded list — already unloaded.
            return True

        except AttributeError:
            # list_loaded() not available in this SDK version — fall back to
            # the original handle-based approach.
            model = client.llm.model(model_name)
            model.unload()
            return True