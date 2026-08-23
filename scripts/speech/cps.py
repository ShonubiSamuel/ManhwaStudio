"""
speech/cps.py — characters-per-second helpers for speech-segment dubbing.

CPS is the metric professional tools (Netflix, Maestra, VideoLingo) use to judge
whether a line fits its time slot at a natural speaking rate. We use it two ways:
  • target_chars(duration, lang) — how long a translation SHOULD be for a cue,
  • is_rushed(text, duration, lang) — flag a cue that's too dense to speak cleanly.

CJK languages (zh/ja/ko/yue) pack more meaning per character, so their comfortable
CPS is much lower.
"""

from __future__ import annotations

import config

_CJK = {"zh", "ja", "ko", "yue"}


def _is_cjk(lang_code: str) -> bool:
    return (lang_code or "").lower() in _CJK


def comfortable_cps(lang_code: str) -> float:
    return config.CPS_COMFORTABLE_CJK if _is_cjk(lang_code) else config.CPS_COMFORTABLE


def max_cps(lang_code: str) -> float:
    return config.CPS_MAX_CJK if _is_cjk(lang_code) else config.CPS_MAX


def target_chars(duration_sec: float, lang_code: str) -> int:
    """Ideal translation length (chars) to speak comfortably within duration_sec."""
    return max(1, int(round(max(0.0, duration_sec) * comfortable_cps(lang_code))))


def cps_of(text: str, duration_sec: float) -> float:
    """Characters per second this text would need to fit duration_sec."""
    n = len((text or "").strip())
    return (n / duration_sec) if duration_sec > 0 else 0.0


def is_rushed(text: str, duration_sec: float, lang_code: str) -> bool:
    """True if `text` is too dense to speak naturally in duration_sec."""
    return cps_of(text, duration_sec) > max_cps(lang_code)


# ── Canonical duration + metric recomputation ────────────────────────────────
# Until this existed, four places independently implemented "how long may this
# cue speak for": translate_cues._eff_dur, refine_cue's `dur`, and the UI's
# effDur/computeCps. They disagreed, so the `cps` stored on a cue depended on
# which path last touched it — sessions with identical timings ended up with
# different numbers. Everything must now route through here.

def effective_duration(cues, index: int, min_gap: float | None = None) -> float:
    """
    The speaking window for cues[index].

    Its own span, extended into the silence before the next cue (less a breath)
    when that is longer — the dub engine lets audio run until the next cue
    starts, so trailing dead air is usable time. Judging against the raw
    end-start flags cues as rushed that actually fit, and wastes shorten calls
    fighting a phantom.

    Tolerates malformed cues by returning 0.0 rather than raising: a bad cue
    should score as unmeasurable, not abort a whole session.
    """
    gap = config.DUB_SPEECH_MIN_GAP if min_gap is None else min_gap
    try:
        cue = cues[index]
        raw = max(0.0, float(cue["end"]) - float(cue["start"]))
    except (IndexError, KeyError, TypeError, ValueError):
        return 0.0
    if index + 1 < len(cues):
        try:
            return max(raw, float(cues[index + 1]["start"]) - float(cue["start"]) - gap)
        except (KeyError, TypeError, ValueError):
            return raw
    return raw


def spoken_text(cue: dict) -> str:
    """
    The line that will actually be spoken, or "" if the cue has no translation.

    Deliberately does NOT fall back to `text`: that holds the source line, so
    falling back would score an untranslated cue against its own original and
    report a gap as a success.
    """
    for key in ("translated", "dubbed"):
        v = cue.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


# Reverse of SUPPORTED_LANGUAGES: display name ("French") -> code ("fr").
_NAME_TO_CODE = {name.lower(): code for code, name in config.SUPPORTED_LANGUAGES.items()}


def normalise_lang(lang: str) -> str:
    """
    Accept a code ("fr", "fr-FR") or a display name ("French") and return a code.

    Sessions persist display names while the CPS thresholds key off codes.
    Normalising here means no caller has to remember which form it holds - the
    original bug in this area came from exactly that kind of mismatch.
    """
    s = (lang or "").strip().lower()
    if not s:
        return ""
    if s in _NAME_TO_CODE:
        return _NAME_TO_CODE[s]
    return s.split("-")[0].split("_")[0]


def recompute_cue_metrics(cues, lang_code: str) -> int:
    """
    Recompute `cps` and `rushed` on every cue from its CURRENT text and timings.

    Mutates `cues` in place and returns how many cues changed.

    `cps`/`rushed` are derived values kept on the cue for the UI's benefit, which
    means any edit to text, timings or cue boundaries can leave them stale — and
    re-segmentation can leave them attached to the wrong cue entirely. Calling
    this at the persistence boundary makes that class of bug impossible instead
    of fixing it one path at a time.
    """
    lang_code = normalise_lang(lang_code)
    changed = 0
    for i, cue in enumerate(cues):
        if not isinstance(cue, dict):
            continue
        dur = effective_duration(cues, i)
        text = spoken_text(cue)
        new_cps = round(cps_of(text, dur), 1) if dur > 0 else 0.0
        new_rushed = bool(dur > 0 and text.strip() and is_rushed(text, dur, lang_code))
        if cue.get("cps") != new_cps or bool(cue.get("rushed")) != new_rushed:
            changed += 1
        cue["cps"] = new_cps
        cue["rushed"] = new_rushed
    return changed
