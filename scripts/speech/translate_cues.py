"""
speech/translate_cues.py — translate timed cues, fitting each to its own time.

Unlike the old panel flow (translate freely, then fight the length afterwards),
this targets each cue's CPS up front: a 4 s cue gets ~4 s worth of speech. Only
cues that still come out RUSHED (over CPS_MAX) are iteratively shortened — so we
never gut a line that already fits.

    translate_cues(cues, "fr", provider=…, api_key=…) ->
        [{"text","start","end","translated","cps","rushed"}, ...]

Cues are translated in context-preserving chunks (not one-by-one) for coherence.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import config
from ai import translator
from speech import cps

_CHUNK = 20   # cues per translation request (split-retry handles overflow)


def translate_cues(
    cues:            List[dict],
    lang_code:       str,
    tone_text:       str = "",
    provider:        str = "nvidia",
    api_key:         str = "",
    lm_studio_model: str = "",
    context_length:  int = 32768,
    fix_attempts:    int = 3,
    on_log:          Optional[Callable] = None,
    on_progress:     Optional[Callable] = None,
) -> List[dict]:
    log       = on_log or (lambda *a, **k: None)
    lang_name = config.SUPPORTED_LANGUAGES.get(lang_code, lang_code.upper())
    texts     = [c["text"] for c in cues]

    n_chunks = (len(texts) + _CHUNK - 1) // _CHUNK
    log(f"[{lang_code}] TRANSLATE — {len(texts)} cue(s) in {n_chunks} context "
        f"batch(es) of ≤{_CHUNK} (each batch = 1 LLM call, NOT one-by-one)", "accent")

    # Per-cue best-fit target: the natural number of characters for the cue's own
    # time slot (duration × comfortable CPS). The translator aims for this — fills
    # the slot without overflowing — so few cues need shortening OR speed-up.
    aims = [cps.target_chars(float(c["end"]) - float(c["start"]), lang_code) for c in cues]

    # 1) Context-aware batch translation (in chunks), each targeting its slot.
    translations: List[str] = []
    for i in range(0, len(texts), _CHUNK):
        chunk      = texts[i:i + _CHUNK]
        chunk_aims = aims[i:i + _CHUNK]
        log(f"  → batch {i // _CHUNK + 1}/{n_chunks}: cues {i + 1}–{i + len(chunk)} "
            f"(aiming for slot-fit lengths) …", "muted")
        tr = translator._translate_batch_raw(
            chunk, lang_code, lang_name, tone_text,
            f"Cues {i // _CHUNK + 1}", f"[{lang_code}]",
            provider, api_key, lm_studio_model, context_length, log,
            enforce_len=False, aim_chars=chunk_aims,
        )
        translations.extend(tr)
    # Guard length mismatch (shouldn't happen — split-retry pads).
    while len(translations) < len(cues):
        translations.append("")

    # 2) CPS-fit: shorten ONLY rushed cues — CONCURRENTLY, since each rushed cue
    #    is independent. Otherwise 8 rushed cues run one-after-another (the stall
    #    you saw). Within a cue the tries are still sequential (try 2 needs try 1).
    import time
    import runtime_settings as rs
    from concurrent.futures import ThreadPoolExecutor

    workers = max(1, rs.get_int("nvidia_max_concurrent", 6) if provider == "nvidia"
                  else rs.get_int("lm_studio_max_concurrent", 4))

    def _is_rushed(cue, tr):
        d = float(cue["end"]) - float(cue["start"])
        t = (tr or "").strip()
        return d > 0 and t and cps.is_rushed(t, d, lang_code)

    comf = cps.comfortable_cps(lang_code)
    def _fit_score(text, dur):                     # distance from the ideal CPS = best fit
        return abs(cps.cps_of(text, dur) - comf)

    def _fit_one(n, cue, tr):
        dur  = float(cue["end"]) - float(cue["start"])
        best = (tr or "").strip()
        if _is_rushed(cue, tr):
            t0 = time.time()
            target, start_cps = cps.target_chars(dur, lang_code), cps.cps_of(best, dur)
            for attempt in range(max(0, fix_attempts)):
                cand = (translator.shorten_line(
                    cue["text"], best, target, lang_code,
                    provider=provider, api_key=api_key,
                    lm_studio_model=lm_studio_model, context_length=context_length,
                    on_log=lambda *a, **k: None, # suppress internal API error logs from cluttering
                ) or "").strip()
                
                cand_cps = cps.cps_of(cand, dur) if cand else 0.0
                log(f"    ↳ cue {n + 1:02d} attempt {attempt + 1}: returned {cand_cps:.1f} CPS", "muted")

                # Keep the BEST FIT (closest to the comfortable CPS), not the
                # smallest — over-shortening just trades speed-up for silence.
                if cand and _fit_score(cand, dur) < _fit_score(best, dur):
                    best = cand
                if not cps.is_rushed(best, dur, lang_code):
                    log(f"    ↳ cue {n + 1:02d} successfully shortened below limit!", "success")
                    break
                target = int(target * 0.9)
                
            log(f"  cue {n + 1:02d} finished shortening {start_cps:.1f}→{cps.cps_of(best, dur):.1f} CPS "
                f"in {time.time() - t0:.1f}s", "muted" if cps.is_rushed(best, dur, lang_code) else "success")
        c = dict(cue)
        c["translated"] = best
        c["cps"]        = round(cps.cps_of(best, dur), 1) if dur > 0 else 0.0
        c["rushed"]     = bool(dur > 0 and cps.is_rushed(best, dur, lang_code))
        return n, c

    n_rushed = sum(1 for cue, tr in zip(cues, translations) if _is_rushed(cue, tr))
    log(f"  CPS-fit: {n_rushed} rushed cue(s) → shortening in parallel (≤{workers} at once) …", "info")

    out: List[dict] = [None] * len(cues)
    fit_t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_fit_one, n, cue, tr) for n, (cue, tr) in enumerate(zip(cues, translations))]
        for done, fut in enumerate(futs):
            n, c = fut.result()
            out[n] = c
            if on_progress:
                on_progress(done + 1, len(cues))
    log(f"  CPS-fit done in {time.time() - fit_t0:.1f}s", "info")

    for n, c in enumerate(out):       # ordered final summary
        log(f"  cue {n + 1:02d}  {c['cps']:>4} CPS  \"{(c['translated'] or '—')[:60]}\"", "muted")

    rushed_fixed = n_rushed
    still_rushed = sum(1 for c in out if c["rushed"])
    log(f"[{lang_code}] translated {len(out)} cue(s) · CPS-fit {rushed_fixed} · "
        f"{still_rushed} still tight", "info")
    return out
