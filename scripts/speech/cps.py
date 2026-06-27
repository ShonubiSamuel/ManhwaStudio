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
