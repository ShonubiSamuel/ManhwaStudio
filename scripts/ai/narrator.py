"""
ai/narrator.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Narration and transcript refinement via AI.

Extracted from ai_engine.py.

Public API
──────────
    refine_transcript(...)     → List[str]   video pipeline — REFINE stage
    narrate_with_vision(...)   → List[str]   PDF pipeline — NARRATE stage
"""

from __future__ import annotations

import base64
import contextlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import nvidia_provider
import lmstudio_provider
from ai.text_utils import (
    strip_thinking_blocks,
    strip_markdown_fences,
    extract_json_object,
    extract_json_array,
    safe_json_loads,
    call_provider,
)


# ── REFINE ────────────────────────────────────────────────────────────────────

def refine_transcript(
    panel_texts:     List[str],
    tone_prompt:     str,
    api_key:         str      = "",
    batch_size:      int      = nvidia_provider.BATCH_SIZE,
    on_log:          Callable = None,
    on_progress:     Callable = None,
    on_batch_done:   Callable = None,
    provider:        str      = "nvidia",
    lm_studio_url:   str      = "http://localhost:1234/v1",
    lm_studio_model: str      = "",
    max_concurrent:  int      = 1,
    context_length:  int      = lmstudio_provider.CONTEXT_LENGTH,
    should_stop:     Optional[Callable[[], bool]] = None,
) -> List[str]:
    """
    REFINE stage — rewrite raw Whisper transcripts for narration quality.

    Batches panel_texts into batch_size chunks and rewrites each with the AI
    applying tone_prompt style.  transcript_text is never modified; results
    go to narration_text.

    max_concurrent > 1 → ThreadPoolExecutor parallel dispatch.
    Returns list of N refined strings (empty str for failed panels).
    """
    log       = on_log or print
    n         = len(panel_texts)
    if n == 0:
        raise ValueError("panel_texts is empty — nothing to refine")

    tone_text = (
        tone_prompt.strip() if tone_prompt and tone_prompt.strip()
        else "Natural, engaging narration. Conversational storytelling voice."
    )
    results:   List[str] = [""] * n
    n_batches  = (n + batch_size - 1) // batch_size
    prov_label = "LM Studio" if provider == "lm_studio" else "NVIDIA NIM"
    _sem       = threading.Semaphore(max_concurrent) if max_concurrent > 1 else None

    def _call_and_parse(texts: List[str], label: str, depth: int = 0) -> dict:
        nb            = len(texts)
        indexed_input = {str(i): texts[i] for i in range(nb)}

        prompt = (
            f"You are refining raw speech transcripts for manhwa YouTube narration.\n\n"
            f"TONE STYLE (apply to every output string):\n{tone_text}\n\n"
            f"Task — Rewrite each transcript string for narration quality:\n"
            f"  • Fix grammar, punctuation, and awkward phrasing\n"
            f"  • Apply the tone style consistently\n"
            f"  • Keep all story details and meaning intact\n"
            f"  • NEVER merge two panels into one string\n"
            f"  • Every input key must appear in the output\n\n"
            f"CRITICAL: Return ONLY a valid JSON object with exactly {nb} keys "
            f"(\"0\" to \"{nb - 1}\").\n"
            f"No markdown, no code fences, no explanation — just the JSON object.\n"
            f"Example (3 panels): "
            f"{{\"0\": \"refined text\", \"1\": \"refined text\", "
            f"\"2\": \"refined text\"}}\n\n"
            f"Input ({nb} panels):\n"
            f"{json.dumps(indexed_input, indent=2, ensure_ascii=False)}"
        )

        _RETRYABLE    = ("connection", "timeout", "429", "rate", "503", "502", "500")
        _RETRY_DELAYS = (2, 5, 15)
        raw       = ""
        _last_r:  Optional[Exception] = None

        for _attempt_r in range(len(_RETRY_DELAYS) + 1):
            if _attempt_r > 0:
                _wait_r = _RETRY_DELAYS[_attempt_r - 1]
                log(
                    f"  {label} ⚠  {_last_r} — "
                    f"retry {_attempt_r}/{len(_RETRY_DELAYS)} in {_wait_r}s …",
                    "warning",
                )
                time.sleep(_wait_r)
            try:
                with (_sem if _sem is not None else contextlib.nullcontext()):
                    raw = call_provider(
                        prompt,
                        provider       = provider,
                        api_key        = api_key,
                        lm_model       = lm_studio_model,
                        max_tokens     = 8192,
                        context_length = context_length,
                    )
                break
            except Exception as exc:
                _last_r = exc
                err = str(exc)
                if "Context size" in err or "context" in err.lower():
                    log(
                        f"  {label} ✗  Context size exceeded — "
                        f"context_length={context_length} tokens.  "
                        f"Fix: raise Context length in Settings → LM STUDIO, "
                        f"or reduce Panels per batch.",
                        "error",
                    )
                    return {}
                if not any(k in err.lower() for k in _RETRYABLE):
                    log(f"  {label} ✗  API error: {exc}", "error")
                    return {}
        else:
            log(f"  {label} ✗  API error (all retries failed): {_last_r}", "error")
            return {}

        cleaned = strip_thinking_blocks(raw)
        cleaned = strip_markdown_fences(cleaned)
        cleaned = extract_json_object(cleaned)

        if not cleaned:
            log(f"  {label} ✗  empty response (raw: {repr(raw[:120])})", "error")
            return {}

        try:
            parsed = safe_json_loads(cleaned)
        except json.JSONDecodeError as exc:
            is_truncated = "Unterminated string" in str(exc)
            if is_truncated and depth < 3 and nb > 1:
                mid = nb // 2
                log(
                    f"  {label} ⚠  truncated at char {exc.pos} — "
                    f"splitting {nb} → {mid}+{nb - mid} and retrying …",
                    "warning",
                )
                left  = _call_and_parse(texts[:mid], f"{label}L", depth + 1)
                right = _call_and_parse(texts[mid:], f"{label}R", depth + 1)
                return {**left, **{k + mid: v for k, v in right.items()}}
            else:
                log(
                    f"  {label} ✗  JSON parse error: {exc}  "
                    f"(cleaned: {repr(cleaned[:120])})",
                    "error",
                )
                return {}

        result: dict = {}
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                try:
                    idx = int(k)
                    if 0 <= idx < nb:
                        result[idx] = str(v).strip()
                except (ValueError, TypeError):
                    pass
        elif isinstance(parsed, list):
            for i, v in enumerate(parsed[:nb]):
                result[i] = str(v).strip()
        return result

    def _process_batch(batch_idx: int) -> Tuple[int, dict]:
        if should_stop and should_stop():
            return batch_idx, {}
        start = batch_idx * batch_size
        end   = min(start + batch_size, n)
        batch = panel_texts[start:end]
        label = f"Batch {batch_idx + 1}/{n_batches}"

        log(f"  {label} (panels {start}–{end - 1}) → sending …", "muted")
        batch_result = _call_and_parse(batch, label)

        missing = [i for i in range(len(batch)) if i not in batch_result]
        if missing:
            log(f"  {label} ⚠  missing panel(s): {[start + i for i in missing]}", "warning")
        elif batch_result:
            log(f"  {label} ✓  panels {start}–{end - 1} done", "success")
        else:
            log(f"  {label} ✗  all panels failed", "error")

        if on_progress:
            on_progress(end, n)
        return batch_idx, batch_result

    def _apply(batch_idx: int, batch_result: dict):
        start = batch_idx * batch_size
        for rel_idx, text in batch_result.items():
            results[start + rel_idx] = text

    if max_concurrent > 1:
        workers = min(max_concurrent, n_batches)
        log(
            f"Refining {n} panel(s) via {prov_label} — "
            f"{n_batches} batch(es) parallel ×{workers} …",
            "info",
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_batch, i): i for i in range(n_batches)}
            for future in as_completed(futures):
                if should_stop and should_stop():
                    for f in futures:
                        f.cancel()
                    break
                bi, br = future.result()
                _apply(bi, br)
                if on_batch_done and br:
                    s = bi * batch_size
                    e = min(s + batch_size, n)
                    on_batch_done(bi, s, e, br)
    else:
        log(
            f"Refining {n} panel(s) via {prov_label} — "
            f"{n_batches} batch(es) of ≤{batch_size} …",
            "info",
        )
        for i in range(n_batches):
            if should_stop and should_stop():
                break
            bi, br = _process_batch(i)
            _apply(bi, br)
            if on_batch_done and br:
                s = bi * batch_size
                e = min(s + batch_size, n)
                on_batch_done(bi, s, e, br)

    n_done = sum(1 for r in results if r)
    log(f"Refined {n_done}/{n} panel(s) ✓", "success")
    return results


# ── NARRATE (vision) ──────────────────────────────────────────────────────────

def narrate_with_vision(
    image_paths: List[str],
    tone_prompt: str,
    api_key:     str,
    batch_size:  int      = 3,
    on_log:      Callable = None,
    on_progress: Callable = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[str]:
    """
    PDF narration via NVIDIA Llama 3.2 90B Vision.
    Sends panel images in batches of batch_size.
    Returns list of N narration strings (one per panel image).

    Fixes vs original:
      • should_stop support — checked at the start of each batch so the
        user's STOP button can cancel mid-run.
      • Retry-with-backoff — transient 429 / 503 / 502 errors are retried
        up to three times (2 s / 5 s / 15 s delays) instead of immediately
        leaving all panels in the batch empty.
      • Failed image loads are skipped entirely instead of sending an empty
        base64 string to the Vision API.  Results are mapped back to their
        original panel positions so unaffected panels are still narrated.
    """
    log = on_log or print
    n   = len(image_paths)
    if n == 0:
        raise ValueError("image_paths list is empty — nothing to narrate")

    tone_text = (
        tone_prompt.strip() if tone_prompt and tone_prompt.strip()
        else "Natural, engaging narration. Conversational storytelling voice."
    )
    narrations: List[str] = [""] * n
    n_batches  = (n + batch_size - 1) // batch_size

    _RETRYABLE    = ("connection", "timeout", "429", "rate", "503", "502", "500")
    _RETRY_DELAYS = (2, 5, 15)

    log(
        f"Narrating {n} panel(s) via Llama 3.2 Vision "
        f"({n_batches} batch(es) of ≤{batch_size}) …",
        "info",
    )

    for batch_idx in range(n_batches):
        # ── Stop check ────────────────────────────────────────────────────────
        if should_stop and should_stop():
            log("Narration cancelled by user", "warning")
            break

        start      = batch_idx * batch_size
        end        = min(start + batch_size, n)
        batch      = image_paths[start:end]
        n_in_batch = len(batch)

        log(f"  Batch {batch_idx + 1}/{n_batches}  (panel {start}–{end - 1}) …", "muted")

        # ── Load images — track which ones succeed with their batch position ──
        # Failed images are skipped entirely.  Sending an empty base64 string
        # to the Vision API produces undefined / garbage output; it is always
        # better to ask the model for fewer narrations and leave the failed
        # panel empty in the output.
        loaded: List[Tuple[int, str, str]] = []   # (batch_pos, mime, b64_data)

        for j, img_path in enumerate(batch):
            try:
                path   = Path(img_path)
                suffix = path.suffix.lower()
                mime   = (
                    "image/jpeg" if suffix in {".jpg", ".jpeg"} else
                    "image/png"  if suffix == ".png"            else
                    "image/webp" if suffix == ".webp"           else
                    "image/jpeg"
                )
                loaded.append((j, mime, base64.b64encode(path.read_bytes()).decode("ascii")))
            except Exception as exc:
                log(f"  Could not load {Path(img_path).name}: {exc}", "warning")

        if not loaded:
            log(f"  Batch {batch_idx + 1} skipped — all {n_in_batch} image(s) failed to load", "warning")
            if on_progress:
                on_progress(end, n)
            continue

        n_to_send  = len(loaded)
        images_b64 = [(mime, b64) for _, mime, b64 in loaded]

        prompt = (
            f"You are writing narration for a manhwa YouTube video.\n\n"
            f"TONE:\n{tone_text}\n\n"
            f"You are looking at {n_to_send} panel image(s).\n"
            f"Write exactly one narration for each panel in the order they appear.\n"
            f"Keep each narration concise — it will be read as voiceover.\n\n"
            f"CRITICAL: Return ONLY a JSON array with exactly {n_to_send} strings.\n"
            f"No markdown. No explanation. No code fences.\n"
            f"Example format: [\"Narration for panel 1.\", \"Narration for panel 2.\", ...]"
        )

        # ── API call with retry-with-backoff ──────────────────────────────────
        response  = None
        _last_exc = None

        for _attempt in range(len(_RETRY_DELAYS) + 1):
            if _attempt > 0:
                _wait = _RETRY_DELAYS[_attempt - 1]
                log(
                    f"  Batch {batch_idx + 1} ⚠  {_last_exc} — "
                    f"retry {_attempt}/{len(_RETRY_DELAYS)} in {_wait}s …",
                    "warning",
                )
                time.sleep(_wait)
            try:
                response = nvidia_provider.call_vision(images_b64, prompt, api_key)
                break   # success
            except Exception as exc:
                _last_exc = exc
                if not any(k in str(exc).lower() for k in _RETRYABLE):
                    log(
                        f"  Batch {batch_idx + 1} ✗  Vision API error (non-retryable): {exc}",
                        "error",
                    )
                    break   # do not retry non-transient errors

        if response is None:
            log(
                f"  Batch {batch_idx + 1} ✗  Vision API failed — "
                f"panels {start}–{end - 1} left empty"
                + (f": {_last_exc}" if _last_exc else ""),
                "error",
            )
            if on_progress:
                on_progress(end, n)
            continue

        # ── Parse response and map back to original panel positions ───────────
        try:
            cleaned = strip_markdown_fences(response)
            cleaned = extract_json_array(cleaned)
            parsed  = json.loads(cleaned)

            if not isinstance(parsed, list):
                raise ValueError(f"Expected list, got {type(parsed).__name__}")

            if len(parsed) > n_to_send:
                parsed = parsed[:n_to_send]
            elif len(parsed) < n_to_send:
                log(
                    f"  Batch {batch_idx + 1}: got {len(parsed)} narrations "
                    f"(expected {n_to_send}) — padding",
                    "warning",
                )
                parsed += [""] * (n_to_send - len(parsed))

            # Use loaded[] to map each result back to its original batch position.
            # If some images failed to load, loaded[] is shorter than n_in_batch,
            # so we cannot use a plain range(n_in_batch) index here.
            for result_idx, (orig_batch_pos, _, _) in enumerate(loaded):
                narrations[start + orig_batch_pos] = str(parsed[result_idx]).strip()

            log(
                f"  Batch {batch_idx + 1} complete — "
                f"{n_to_send} narration(s) received ✓",
                "success",
            )
        except (json.JSONDecodeError, ValueError) as exc:
            log(
                f"  Batch {batch_idx + 1} parse error: {exc} — "
                f"panels {start}–{end - 1} left empty.  Raw: {response[:200]}",
                "error",
            )

        if on_progress:
            on_progress(end, n)

    received = sum(1 for t in narrations if t)
    log(
        f"Vision narration complete — {received}/{n} panel(s) narrated ✓",
        "success" if received == n else "warning",
    )
    return narrations