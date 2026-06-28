"""
ai/translator.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Live translation system (Generation 3 — flat-pool parallel).

Extracted from ai_engine.py.  This module contains ONLY the current live
system.  The two deprecated predecessors have been deleted:

  Gen 1 (deleted): clean_and_translate, apply_tone_and_translate, _run_text_call
  Gen 2 (deleted): translate_panels_for_language

Public API
──────────
    translate_panels_parallel(...)     → Dict[str, List[str]]
        All panels × N languages, every panel the same length.

    translate_subset_parallel(...)     → Dict[str, List[str]]
        Per-language subsets of different lengths (gap-fill / re-translate).
        This is what pipeline_tab.py's _runner_translate calls.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

import config
import nvidia_provider
import lmstudio_provider
from ai.text_utils import (
    strip_thinking_blocks,
    strip_markdown_fences,
    extract_json_array,
    call_provider,
)


# ── Internal: normalise a model-returned item to a plain string ───────────────

def _coerce_translation(item) -> str:
    """
    Models occasionally wrap each translation in an object — e.g.
    {"text": "..."} or {"translation": "..."} — instead of returning a bare
    string. Without this, str(item) leaks the dict into the dub
    (e.g. "{'text': 'Le garçon ...'}"). Extract the actual string instead.
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "translation", "translated", "translated_text",
                    "output", "result", "value", "string"):
            v = item.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in item.values():            # fallback: first string value
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    if isinstance(item, list):             # fallback: join nested strings
        return " ".join(_coerce_translation(x) for x in item).strip()
    return str(item).strip()


# ── Internal: single-batch translation with split-retry ──────────────────────

def _translate_batch_raw(
    texts:           List[str],
    lang_code:       str,
    lang_name:       str,
    tone_text:       str,
    label:           str,
    tag:             str,
    provider:        str,
    api_key:         str,
    lm_studio_model: str,
    context_length:  int,
    on_log:          Callable,
    depth:           int = 0,
    _sem:            Optional[threading.Semaphore] = None,
    enforce_len:     Optional[bool] = None,
    aim_chars:       Optional[List[int]] = None,
) -> List[str]:
    """
    Translate a single batch of panel texts into lang_code.

    Split-retry is triggered for BOTH failure modes:
      1. JSON truncation   (model hit max_tokens mid-output)
      2. Short response    (model returned fewer items than expected)

    Both cases split the batch in half and retry each half recursively
    (max depth 3) so every panel gets a proper translation instead of
    being silently padded with an empty string.

    Returns list of exactly len(texts) translated strings.
    """
    log = on_log or print
    nb  = len(texts)

    # Per-line length budget (characters). The dub must fit the same on-screen
    # time as the English, so the translation has to be about as short — or it
    # gets time-compressed and sounds rushed. Smaller is better than longer (a
    # short line is just padded with silence). CJK scripts pack far more sound per
    # character, so their budget is much tighter. Tunable via config.
    import runtime_settings as rs
    cjk           = lang_code in ("zh", "ja", "ko")
    budget_factor = (
        rs.get_float("translate_len_budget_cjk", getattr(config, "TRANSLATE_LEN_BUDGET_CJK", 0.55)) if cjk
        else rs.get_float("translate_len_budget", getattr(config, "TRANSLATE_LEN_BUDGET", 0.95))
    )
    budgets = [max(1, int(len(t) * budget_factor)) for t in texts]
    items   = [{"en": t, "aim_chars": b} for t, b in zip(texts, budgets)]

    # FAITHFUL mode (speech-segment cue path, enforce_len=False): translate fully
    # so the dub fills its time window — over-condensing here is what leaves long
    # awkward silences. Only genuinely-rushed cues are shortened afterwards (CPS).
    if enforce_len is False:
        # BEST-FIT (speech cue path): each line has an "aim_chars" = the natural
        # amount of speech for its time slot. Aim for it — being far UNDER leaves
        # silence, being far OVER forces a robotic speed-up. Both are bad; land
        # close to the aim while staying complete and faithful (no padding).
        aims  = aim_chars if (aim_chars and len(aim_chars) == nb) else [max(1, int(len(t) * 0.95)) for t in texts]
        fitms = [{"en": t, "aim_chars": a} for t, a in zip(texts, aims)]
        prompt = (
            f"You are a professional DUBBING translator: English → {lang_name}.\n\n"
            f"TONE:\n{tone_text}\n\n"
            f"This is time-synced dubbing. Each line has an \"aim_chars\" = the natural length "
            f"that fits its on-screen time. Translate each line COMPLETELY and faithfully into "
            f"fluent {lang_name}, aiming for ABOUT that many characters:\n"
            f"  • Don't go far UNDER aim_chars — a too-short line leaves awkward silence. Keep the "
            f"full meaning and detail.\n"
            f"  • Don't go far OVER aim_chars — a too-long line gets sped up and sounds robotic. "
            f"Use tighter phrasing if needed, but NEVER pad with filler.\n"
            f"  • Landing close to aim_chars (a little under or over is fine) is the goal.\n"
            f"  • PRESERVE the original's punctuation and phrase breaks — especially commas. "
            f"If the English pauses (e.g. \"Sup, my fellas.\" → \"Salut, mes gars.\"), keep that "
            f"comma/break in the {lang_name}. Those commas become the dub's natural breathing "
            f"pauses, so a short line still feels alive instead of dead air. Do not flatten "
            f"\"Sup, my fellas.\" into \"Salut les gars\" with no internal pause.\n"
            f"Do NOT merge, split, add, or drop lines. Return EXACTLY {nb} strings in order.\n\n"
            f"Return ONLY a JSON array of exactly {nb} {lang_name} strings (plain strings, not "
            f"objects). No markdown, no code fences, no notes.\n\n"
            f"Input ({nb} lines):\n{json.dumps(fitms, indent=2, ensure_ascii=False)}"
        )
    else:
        prompt = (
        f"You are a professional DUBBING translator: English → {lang_name}.\n\n"
        f"TONE:\n{tone_text}\n\n"
        f"This is for time-synced video dubbing. Each line MUST strictly adhere to the character budget provided below.\n\n"
        f"RULES (in absolute priority order):\n"
        f"  1. LENGTH LIMIT IS HARD: The character length of the translation MUST be equal to or less than the target \"aim_chars\". Under no circumstances should it exceed this number. Synthesize, condense, and use shorter phrasing to fit the limit.\n"
        f"  2. CORE MEANING SECOND: Preserve the core plot action, emotion, and intent. It is acceptable to drop minor, non-essential descriptive filler words to meet the length requirement in Rule 1. Do not let literal translation ruin the length constraint. BUT never reduce a line to a short, vague fragment that loses the point — shorten by tighter phrasing, NOT by deleting the story.\n"
        f"  3. Do NOT merge, split, add, or drop lines. Return EXACTLY {nb} strings in order.\n\n"
        f"Return ONLY a JSON array of exactly {nb} {lang_name} strings (plain strings, not objects). No markdown, no code fences, no notes.\n"
        f"Example (3 items): [\"…\", \"…\", \"…\"]\n\n"
        f"Input ({nb} lines) — translate each \"en\" into {lang_name}, staying under or equal to \"aim_chars\":\n"
        f"{json.dumps(items, indent=2, ensure_ascii=False)}"
        )

    _RETRYABLE    = ("connection", "timeout", "429", "rate", "503", "502", "500")
    _RETRY_DELAYS = (2, 5, 15)
    response  = ""
    _last_exc: Optional[Exception] = None

    for _attempt in range(len(_RETRY_DELAYS) + 1):
        if _attempt > 0:
            _wait = _RETRY_DELAYS[_attempt - 1]
            log(
                f"  {tag} {label} ⚠  {_last_exc} — "
                f"retry {_attempt}/{len(_RETRY_DELAYS)} in {_wait}s …",
                "warning",
            )
            time.sleep(_wait)
        try:
            with (_sem if _sem is not None else contextlib.nullcontext()):
                response = call_provider(
                    prompt,
                    provider       = provider,
                    api_key        = api_key,
                    lm_model       = lm_studio_model,
                    max_tokens     = 8192,
                    context_length = context_length,
                )
            break
        except Exception as exc:
            _last_exc = exc
            if not any(k in str(exc).lower() for k in _RETRYABLE):
                log(f"  {tag} {label} API error: {exc} — skipping", "error")
                return [""] * nb
    else:
        log(
            f"  {tag} {label} API error (all retries failed): {_last_exc} — skipping",
            "error",
        )
        return [""] * nb

    cleaned = strip_thinking_blocks(response)
    cleaned = strip_markdown_fences(cleaned)
    cleaned = extract_json_array(cleaned)

    def _split_retry(reason: str) -> List[str]:
        mid = nb // 2
        log(
            f"  {tag} {label} ⚠  {reason} — "
            f"splitting {nb} → {mid}+{nb - mid} and retrying …",
            "warning",
        )
        left  = _translate_batch_raw(
            texts[:mid], lang_code, lang_name, tone_text,
            f"{label}L", tag, provider, api_key, lm_studio_model,
            context_length, on_log, depth + 1, _sem,
        )
        right = _translate_batch_raw(
            texts[mid:], lang_code, lang_name, tone_text,
            f"{label}R", tag, provider, api_key, lm_studio_model,
            context_length, on_log, depth + 1, _sem,
        )
        return left + right

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        if "Unterminated string" in str(exc) and depth < 3 and nb > 1:
            return _split_retry(f"truncated at char {exc.pos}")
        log(f"  {tag} {label} ✗  invalid JSON: {exc} — skipping", "error")
        return [""] * nb

    if not isinstance(parsed, list):
        log(f"  {tag} {label} ✗  non-list response — skipping", "error")
        return [""] * nb

    if len(parsed) > nb:
        parsed = parsed[:nb]
    elif len(parsed) < nb:
        if depth < 2 and nb > 1:
            return _split_retry(f"got {len(parsed)} (expected {nb})")
        log(
            f"  {tag} {label} ⚠  got {len(parsed)} (expected {nb}) — padding",
            "warning",
        )
        parsed = parsed + [""] * (nb - len(parsed))

    result = [_coerce_translation(t) for t in parsed]

    # ── Iterative length fit ("back and forth") ───────────────────────────────
    # Drive every line to ≤ its budget (a bit under the English length): any line
    # still too long is sent back to be re-shortened, looping up to N rounds.
    # Shortening keeps the CORE MEANING (rephrase freely, drop only filler) — a
    # candidate is REJECTED if it collapses below TRANSLATE_FIT_FLOOR × the English
    # length, so we shorten by tighter phrasing, never by gutting the story into a
    # fragment.  Lines that genuinely can't fit are left longer; Sync then
    # compresses them to the English length (a small, flagged rush).
    _enforce = (rs.get_bool("translate_len_enforce", getattr(config, "TRANSLATE_LEN_ENFORCE", True))
                if enforce_len is None else enforce_len)
    if _enforce and depth < 2:
        iters = rs.get_int("translate_fit_iters", getattr(config, "TRANSLATE_FIT_ITERS", 3))
        floor = rs.get_float("translate_fit_floor", getattr(config, "TRANSLATE_FIT_FLOOR", 0.45))
        for _round in range(max(0, iters)):
            over = [i for i, t in enumerate(result) if t and len(t) > budgets[i]]
            if not over:
                break
            log(f"  {tag} {label} ↻ fit round {_round + 1}: shortening {len(over)} line(s) …", "muted")
            fit_prompt = (
                f"These {lang_name} dubbing lines are still TOO LONG. Rewrite EACH one SHORTER "
                f"so its character length is ≤ its \"limit\". Keep the CORE MEANING and the key "
                f"story beats — rephrase freely and use the tightest natural wording, dropping "
                f"only filler. Do NOT reduce a line to a vague fragment that loses the point.\n\n"
                f"Return ONLY a JSON array of exactly {len(over)} {lang_name} strings, in order. "
                f"No markdown, no notes.\n\n"
                f"{json.dumps([{'text': result[i], 'limit': budgets[i]} for i in over], ensure_ascii=False, indent=2)}"
            )
            try:
                with (_sem if _sem is not None else contextlib.nullcontext()):
                    resp2 = call_provider(
                        fit_prompt, provider=provider, api_key=api_key,
                        lm_model=lm_studio_model, max_tokens=4096,
                        context_length=context_length,
                    )
                arr = json.loads(extract_json_array(strip_markdown_fences(strip_thinking_blocks(resp2))))
            except Exception as exc:
                log(f"  {tag} {label} fit round skipped: {exc}", "muted")
                break
            if not isinstance(arr, list):
                break
            for k, i in enumerate(over):
                cand   = _coerce_translation(arr[k]) if k < len(arr) else ""
                en_len = len(texts[i])
                # Accept real progress (shorter) that isn't a gutting collapse.
                if cand and len(cand) < len(result[i]) and len(cand) >= max(1, int(en_len * floor)):
                    result[i] = cand

        still = sum(1 for i, t in enumerate(result) if t and len(t) > budgets[i])
        if still:
            log(f"  {tag} {label} ⚠  {still} line(s) couldn't fit without losing meaning — "
                f"Sync will compress them (a small rush)", "muted")

    return result


# ── Public: condense one line to a character target (fix loop) ────────────────

def shorten_line(
    english:         str,
    current:         str,
    target_chars:    int,
    lang_code:       str,
    provider:        str = "nvidia",
    api_key:         str = "",
    lm_studio_model: str = "",
    context_length:  int = lmstudio_provider.CONTEXT_LENGTH,
    on_log:          Callable = None,
    raise_on_error:  bool = False,
) -> str:
    """
    Re-translate ONE line shorter — used by the per-panel "fix rushed" loop.

    Produces a {lang} line of at most ~target_chars characters that keeps the
    core meaning and key story beats of the English (rephrase freely, drop only
    filler — never a vague fragment).  Returns "" on failure so the caller can
    keep the previous best.

    raise_on_error=True re-raises provider/network failures instead of swallowing
    them — used by the interactive "AI Fix" button so the UI can tell the user the
    model was unreachable instead of silently reporting a fake success.
    """
    log       = on_log or print
    lang_name = config.SUPPORTED_LANGUAGES.get(lang_code, lang_code.upper())
    target    = max(1, int(target_chars))
    prompt = (
        f"You are a professional {lang_name} dubbing translator.\n"
        f"The {lang_name} line below is too long for its time slot and gets sped up. Rewrite it "
        f"SHORTER and TIGHTER while keeping the COMPLETE meaning and every key story beat of the "
        f"English. Use concise phrasing and contractions, and drop only filler — aim for roughly "
        f"{target} characters, but KEEPING THE MEANING MATTERS MORE THAN THE EXACT NUMBER.\n"
        f"Return a COMPLETE, natural {lang_name} sentence — never a short vague fragment, never "
        f"empty, never just one or two words. It must still convey what the English says.\n\n"
        f"English: {english}\n"
        f"Too-long {lang_name}: {current}\n\n"
        f"Return ONLY the rewritten {lang_name} sentence as plain text — no quotes, no JSON, no notes."
    )
    try:
        resp = call_provider(
            prompt, provider=provider, api_key=api_key,
            lm_model=lm_studio_model, max_tokens=1024, context_length=context_length,
        )
    except Exception as exc:
        log(f"  shorten_line error: {exc}", "muted")
        if raise_on_error:
            raise
        return ""
    txt = strip_thinking_blocks(resp)
    txt = strip_markdown_fences(txt).strip()
    # Tolerate a JSON-ish wrap (["..."] or {"text": "..."}).
    if txt[:1] in ("[", "{"):
        try:
            txt = _coerce_translation(json.loads(extract_json_array(txt)
                                                 if txt[0] == "[" else txt))
        except Exception:
            pass
    # Strip a leading "Label:" the model sometimes prepends, then quotes.
    txt = txt.strip().strip('"').strip()
    if "\n" in txt:
        txt = txt.split("\n")[0].strip()        # keep the first line only
    return txt


# ── Internal: shared parallel execution pool ──────────────────────────────────

def _run_translation_pool(
    all_tasks:       List[Tuple[str, int, int, int, int, List[str]]],
    results:         Dict[str, List[str]],
    total_panels:    int,
    tone_text:       str,
    provider:        str,
    api_key:         str,
    lm_studio_model: str,
    context_length:  int,
    max_concurrent:  int,
    on_log:          Optional[Callable],
    on_progress:     Optional[Callable],
    on_batch_done:   Optional[Callable],
    should_stop:     Optional[Callable[[], bool]],
) -> None:
    """
    Execute a pre-built list of translation tasks in a flat ThreadPoolExecutor.

    all_tasks: list of (lang_code, batch_idx, n_batches_for_lang,
                        start, end, texts_slice)
    results:   pre-allocated {lang_code: [""] * n} dict — written in place.

    This is the single implementation of the pool pattern that was previously
    duplicated verbatim between translate_panels_parallel and
    translate_subset_parallel.
    """
    log       = on_log or print
    completed = [0]
    lock      = threading.Lock()
    _sem      = threading.Semaphore(max_concurrent)

    def _run_one(
        lc:       str,
        bi:       int,
        n_batches: int,
        start:    int,
        end:      int,
        texts:    List[str],
    ) -> None:
        if should_stop and should_stop():
            return

        tag       = f"[{lc}]"
        label     = f"Batch {bi + 1}/{n_batches}"
        lang_name = config.SUPPORTED_LANGUAGES.get(lc, lc.upper())

        log(f"  {tag} {label} (panels {start}–{end - 1}) …", "muted")

        batch_result = _translate_batch_raw(
            texts           = texts,
            lang_code       = lc,
            lang_name       = lang_name,
            tone_text       = tone_text,
            label           = label,
            tag             = tag,
            provider        = provider,
            api_key         = api_key,
            lm_studio_model = lm_studio_model,
            context_length  = context_length,
            on_log          = on_log,
            _sem            = _sem,
        )

        n_ok = sum(1 for t in batch_result if t)
        nb   = len(texts)
        if n_ok == nb:
            log(f"  {tag} {label} ✓  ({n_ok} panels)", "success")
        elif n_ok > 0:
            log(f"  {tag} {label} ⚠  {n_ok}/{nb} panels OK", "warning")
        else:
            log(f"  {tag} {label} ✗  all panels failed", "error")

        with lock:
            for i, txt in enumerate(batch_result):
                results[lc][start + i] = txt
            completed[0] += nb

        if on_batch_done:
            on_batch_done(lc, start, end, batch_result)
        if on_progress:
            on_progress(completed[0], total_panels)

    workers = min(max_concurrent, max(1, len(all_tasks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, lc, bi, nb, s, e, texts): (lc, bi)
            for lc, bi, nb, s, e, texts in all_tasks
        }
        for future in as_completed(futures):
            if should_stop and should_stop():
                for f in futures:
                    f.cancel()
                break
            lc, bi = futures[future]
            try:
                future.result()
            except Exception as exc:
                log(f"  [{lc}] Batch {bi + 1} error: {exc}", "error")


# ── Public: flat-pool parallel (uniform panel set) ────────────────────────────

def translate_panels_parallel(
    panel_texts:     List[str],
    lang_codes:      List[str],
    tone_prompt:     str,
    provider:        str      = "nvidia",
    api_key:         str      = "",
    lm_studio_url:   str      = "http://localhost:1234/v1",
    lm_studio_model: str      = "",
    batch_size:      int      = nvidia_provider.BATCH_SIZE,
    max_concurrent:  int      = nvidia_provider.MAX_CONCURRENT,
    on_log:          Callable = None,
    on_progress:     Callable = None,
    on_batch_done:   Callable = None,
    context_length:  int      = lmstudio_provider.CONTEXT_LENGTH,
    should_stop:     Optional[Callable[[], bool]] = None,
) -> Dict[str, List[str]]:
    """
    Translate all panels into every language using a flat worker pool.

    All (language × batch) tasks are submitted to a single
    ThreadPoolExecutor so up to max_concurrent API requests run
    simultaneously regardless of language boundaries.

    Note: languages are processed in the order supplied by the caller.
    CJK reordering (reorder_languages) is intentionally not applied here
    because all tasks are submitted at once — ordering only affects which
    tasks enter the queue first, which has negligible impact on a parallel
    pool.  Sequential callers that want CJK-first ordering should call
    reorder_languages() on lang_codes before passing it in.

    on_batch_done(lang_code, start, end, texts) — called after each batch
    for real-time DB writes and UI table updates.

    Returns {lang_code: [N translated strings], ...}
    """
    log = on_log or print
    n   = len(panel_texts)

    if not lang_codes or n == 0:
        return {}

    prov_label = "LM Studio" if provider == "lm_studio" else "NVIDIA NIM"
    tone_text  = (
        tone_prompt.strip() if tone_prompt and tone_prompt.strip()
        else "Natural, engaging narration. Conversational storytelling voice."
    )

    lang_list    = list(lang_codes)
    n_batches    = (n + batch_size - 1) // batch_size
    total_panels = n * len(lang_list)
    results      = {lc: [""] * n for lc in lang_list}

    log(
        f"Translating {len(lang_list)} language(s) × {n_batches} batch(es) "
        f"— pool ×{max_concurrent} via {prov_label} …",
        "accent",
    )

    all_tasks: List[Tuple[str, int, int, int, int, List[str]]] = [
        (
            lc, bi, n_batches,
            bi * batch_size,
            min((bi + 1) * batch_size, n),
            panel_texts[bi * batch_size : min((bi + 1) * batch_size, n)],
        )
        for lc in lang_list
        for bi in range(n_batches)
    ]

    _run_translation_pool(
        all_tasks, results, total_panels, tone_text,
        provider, api_key, lm_studio_model, context_length,
        max_concurrent, on_log, on_progress, on_batch_done, should_stop,
    )

    for lc in lang_list:
        n_done    = sum(1 for t in results[lc] if t)
        lang_name = config.SUPPORTED_LANGUAGES.get(lc, lc.upper())
        log(f"  [{lc}] {lang_name}: {n_done}/{n} panels ✓", "success")

    return results


# ── Public: flat-pool parallel (per-language subsets) ────────────────────────

def translate_subset_parallel(
    lang_subset:      Dict[str, List[str]],
    tone_prompt:      str,
    provider:         str      = "nvidia",
    api_key:          str      = "",
    lm_studio_url:    str      = "http://localhost:1234/v1",
    lm_studio_model:  str      = "",
    batch_size:       int      = nvidia_provider.BATCH_SIZE,
    max_concurrent:   int      = nvidia_provider.MAX_CONCURRENT,
    on_log:           Callable = None,
    on_progress:      Callable = None,
    on_batch_done:    Callable = None,
    context_length:   int      = lmstudio_provider.CONTEXT_LENGTH,
    should_stop:      Optional[Callable[[], bool]] = None,
    lang_batch_sizes: Dict[str, int] = None,
) -> Dict[str, List[str]]:
    """
    Flat-pool parallel translation where each language may have a different
    number of texts (e.g. only the panels that are missing translation).

    This is what pipeline_tab._runner_translate calls — it pre-filters each
    language to only the panels that don't yet have a translation, so panel
    counts differ between languages.

    lang_batch_sizes: optional per-language batch size overrides.
        e.g. {"ko": 12, "zh": 20}

    Returns {lang_code: [translated strings], ...}
    """
    log = on_log or print

    if not lang_subset:
        return {}

    prov_label = "LM Studio" if provider == "lm_studio" else "NVIDIA NIM"
    tone_text  = (
        tone_prompt.strip() if tone_prompt and tone_prompt.strip()
        else "Natural, engaging narration. Conversational storytelling voice."
    )

    lang_list    = list(lang_subset.keys())
    results      = {lc: [""] * len(lang_subset[lc]) for lc in lang_list}
    total_panels = sum(len(lang_subset[lc]) for lc in lang_list)

    all_tasks: List[Tuple[str, int, int, int, int, List[str]]] = []
    for lc in lang_list:
        texts_lc  = lang_subset[lc]
        n_lc      = len(texts_lc)
        lc_batch  = (lang_batch_sizes or {}).get(lc, batch_size)
        n_batches = (n_lc + lc_batch - 1) // lc_batch
        for bi in range(n_batches):
            start = bi * lc_batch
            end   = min(start + lc_batch, n_lc)
            all_tasks.append((lc, bi, n_batches, start, end, texts_lc[start:end]))

    log(
        f"Translating {len(lang_list)} language(s) × up to {len(all_tasks)} batch(es) "
        f"— pool ×{max_concurrent} via {prov_label} …",
        "accent",
    )

    _run_translation_pool(
        all_tasks, results, total_panels, tone_text,
        provider, api_key, lm_studio_model, context_length,
        max_concurrent, on_log, on_progress, on_batch_done, should_stop,
    )

    for lc in lang_list:
        n_done    = sum(1 for t in results[lc] if t)
        n_total   = len(lang_subset[lc])
        lang_name = config.SUPPORTED_LANGUAGES.get(lc, lc.upper())
        log(f"  [{lc}] {lang_name}: {n_done}/{n_total} panels ✓", "success")

    return results