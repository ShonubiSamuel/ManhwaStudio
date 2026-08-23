"""
eval/metrics.py — pure, I/O-free metrics for dub quality.

Two families:

  REFERENCE-FREE (always available). These judge whether a dub *fits* — whether
  each translated line can be spoken naturally in the time the original occupied.
  They need nothing but the pipeline's own output, so they run on every session
  already on disk and can gate a regression in CI.

  REFERENCE-BASED (see references.py). BLEU/chrF/WER against human translations
  or transcripts. Stronger claims, but they need reference data we may not have.

The fit metrics deliberately mirror translate_cues.py's own notion of a cue's
*usable* duration: a cue may run past its own `end` into the dead air before the
next cue starts. Judging CPS against raw end-start would flag lines as rushed
that in practice have room to breathe, so the same allowance is applied here.
Measuring against a different definition than the pipeline optimises for would
make every number meaningless.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# ── CPS thresholds ───────────────────────────────────────────────────────────
# Prefer the live project config so the harness can never drift from what the
# pipeline actually targets. Fall back to the documented defaults so the module
# stays importable (and unit-testable) outside the app.
def _load_cfg():
    """Find the project config however the harness happens to be invoked.

    `import config` works when scripts/ is on sys.path (as the app runs it);
    `scripts.config` works when invoked as `python -m scripts.eval` from the
    project root. Try both before falling back.
    """
    for name in ("config", "scripts.config"):
        try:
            mod = __import__(name, fromlist=["*"])
            if hasattr(mod, "CPS_COMFORTABLE"):
                return mod
        except Exception:
            continue
    return None


try:  # pragma: no cover - environment dependent
    _cfg = _load_cfg()
    if _cfg is None:
        raise ImportError("project config not importable")

    CPS_COMFORTABLE = float(_cfg.CPS_COMFORTABLE)
    CPS_MAX = float(_cfg.CPS_MAX)
    CPS_COMFORTABLE_CJK = float(_cfg.CPS_COMFORTABLE_CJK)
    CPS_MAX_CJK = float(_cfg.CPS_MAX_CJK)
    CONFIG_SOURCE = "project config"
except Exception:  # pragma: no cover
    CPS_COMFORTABLE, CPS_MAX = 20.0, 24.0
    CPS_COMFORTABLE_CJK, CPS_MAX_CJK = 6.0, 10.0
    CONFIG_SOURCE = "built-in defaults"

CJK = {"zh", "ja", "ko", "yue"}

# Sessions record display names ("French"); cps thresholds key off codes.
_DISPLAY_TO_CODE = {
    "english": "en", "french": "fr", "spanish": "es", "german": "de",
    "italian": "it", "portuguese": "pt", "russian": "ru", "arabic": "ar",
    "hindi": "hi", "indonesian": "id", "vietnamese": "vi", "thai": "th",
    "turkish": "tr", "polish": "pl", "dutch": "nl", "yoruba": "yo",
    "chinese": "zh", "japanese": "ja", "korean": "ko", "cantonese": "yue",
}

# A cue may use the silence before the next cue, minus a breath.
BREATH_SEC = 0.24


def lang_code(lang: str | None) -> str:
    """Normalise 'French' or 'fr-FR' to 'fr'. Unknown input returns ''."""
    s = (lang or "").strip().lower()
    if not s:
        return ""
    if s in _DISPLAY_TO_CODE:
        return _DISPLAY_TO_CODE[s]
    return s.split("-")[0].split("_")[0]


def is_cjk(lang: str | None) -> bool:
    return lang_code(lang) in CJK


def comfortable_cps(lang: str | None) -> float:
    return CPS_COMFORTABLE_CJK if is_cjk(lang) else CPS_COMFORTABLE


def max_cps(lang: str | None) -> float:
    return CPS_MAX_CJK if is_cjk(lang) else CPS_MAX


def target_chars(duration_sec: float, lang: str | None) -> int:
    """Ideal translation length to speak comfortably within duration_sec."""
    return max(1, round(max(0.0, duration_sec) * comfortable_cps(lang)))


def cps_of(text: str, duration_sec: float) -> float:
    """Characters per second this text needs to fit duration_sec."""
    n = len((text or "").strip())
    return (n / duration_sec) if duration_sec > 0 else 0.0


def usable_duration(cues: Sequence[dict], index: int, breath: float = BREATH_SEC) -> float:
    """
    How long cue[index] may actually speak for.

    Its own span, extended into the gap before the next cue (less a breath) when
    that is longer. Mirrors translate_cues.py so both judge the same slot.
    """
    cue = cues[index]
    try:
        raw = max(0.0, float(cue["end"]) - float(cue["start"]))
    except (KeyError, TypeError, ValueError):
        return 0.0
    if index + 1 < len(cues):
        try:
            extended = float(cues[index + 1]["start"]) - float(cue["start"]) - breath
            return max(raw, extended)
        except (KeyError, TypeError, ValueError):
            return raw
    return raw


def _translated_text(cue: dict) -> str:
    """
    The line that will actually be spoken, or "" if the cue was never translated.

    Deliberately does NOT fall back to `text`. `text` holds the *source* line, so
    falling back to it would score an untranslated cue against its own original —
    scoring a pipeline failure as a success and hiding exactly the gap coverage
    is meant to expose.
    """
    for key in ("translated", "dubbed"):
        v = cue.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _source_text(cue: dict) -> str:
    """The original transcribed line, used as the ASR hypothesis."""
    v = cue.get("text")
    return v if isinstance(v, str) else ""


@dataclass
class FitReport:
    """Reference-free fit metrics for one session (or an aggregate)."""

    cues: int = 0
    translated: int = 0
    empty: int = 0
    lang: str = ""

    cps_values: list[float] = field(default_factory=list)
    length_ratios: list[float] = field(default_factory=list)
    overruns: list[float] = field(default_factory=list)

    rushed: int = 0
    comfortable: int = 0
    overrunning: int = 0
    zero_duration: int = 0

    # ── derived ──────────────────────────────────────────────────────────────
    @property
    def coverage(self) -> float:
        return (self.translated / self.cues) if self.cues else 0.0

    @property
    def rushed_rate(self) -> float:
        return (self.rushed / self.translated) if self.translated else 0.0

    @property
    def comfortable_rate(self) -> float:
        return (self.comfortable / self.translated) if self.translated else 0.0

    @property
    def overrun_rate(self) -> float:
        return (self.overrunning / self.translated) if self.translated else 0.0

    @property
    def mean_cps(self) -> float:
        return statistics.fmean(self.cps_values) if self.cps_values else 0.0

    @property
    def median_cps(self) -> float:
        return statistics.median(self.cps_values) if self.cps_values else 0.0

    @property
    def p95_cps(self) -> float:
        if not self.cps_values:
            return 0.0
        ordered = sorted(self.cps_values)
        return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]

    @property
    def mean_length_ratio(self) -> float:
        return statistics.fmean(self.length_ratios) if self.length_ratios else 0.0

    @property
    def total_overrun_sec(self) -> float:
        return sum(self.overruns)

    @property
    def worst_overrun_sec(self) -> float:
        return max(self.overruns) if self.overruns else 0.0

    def merge(self, other: "FitReport") -> "FitReport":
        """Combine two reports (for cross-session aggregates)."""
        out = FitReport(lang=self.lang if self.lang == other.lang else "mixed")
        out.cues = self.cues + other.cues
        out.translated = self.translated + other.translated
        out.empty = self.empty + other.empty
        out.cps_values = self.cps_values + other.cps_values
        out.length_ratios = self.length_ratios + other.length_ratios
        out.overruns = self.overruns + other.overruns
        out.rushed = self.rushed + other.rushed
        out.comfortable = self.comfortable + other.comfortable
        out.overrunning = self.overrunning + other.overrunning
        out.zero_duration = self.zero_duration + other.zero_duration
        return out


def evaluate_fit(cues: Sequence[dict], lang: str | None) -> FitReport:
    """
    Score how well a session's translations fit their time slots.

    A cue is:
      comfortable  — achieved CPS <= comfortable threshold for the language
      rushed       — achieved CPS >  max threshold (too dense to speak cleanly)
      overrunning  — projected speech time exceeds the usable slot, i.e. it will
                     collide with the next cue even at the maximum rate
    """
    rep = FitReport(lang=lang_code(lang))
    rep.cues = len(cues)
    comfortable, ceiling = comfortable_cps(lang), max_cps(lang)

    for i, cue in enumerate(cues):
        text = _translated_text(cue)
        if not text.strip():
            rep.empty += 1
            continue
        rep.translated += 1

        dur = usable_duration(cues, i)
        if dur <= 0:
            rep.zero_duration += 1
            continue

        achieved = cps_of(text, dur)
        rep.cps_values.append(achieved)

        if achieved > ceiling:
            rep.rushed += 1
        elif achieved <= comfortable:
            rep.comfortable += 1

        rep.length_ratios.append(len(text.strip()) / max(1, target_chars(dur, lang)))

        # Time needed at the maximum acceptable rate; anything beyond the slot
        # is an unavoidable collision, not merely a rushed line.
        needed = len(text.strip()) / ceiling if ceiling > 0 else 0.0
        if needed > dur:
            rep.overrunning += 1
            rep.overruns.append(needed - dur)

    return rep


def shortening_effectiveness(cues: Iterable[dict], lang: str | None) -> dict[str, Any]:
    """
    Compare the CPS the pipeline recorded per cue against what we recompute.

    translate_cues.py iteratively shortens rushed lines and stores the result in
    `cps`. Recomputing from the final text and comparing surfaces cues whose
    stored value no longer matches the text that shipped — a stale-metadata bug
    that would otherwise hide a regression.
    """
    checked = drifted = 0
    deltas: list[float] = []
    cue_list = list(cues)
    for i, cue in enumerate(cue_list):
        stored = cue.get("cps")
        text = _translated_text(cue)
        if stored is None or not text.strip():
            continue
        dur = usable_duration(cue_list, i)
        if dur <= 0:
            continue
        checked += 1
        delta = abs(float(stored) - cps_of(text, dur))
        deltas.append(delta)
        if delta > 1.0:  # more than 1 char/sec apart
            drifted += 1
    return {
        "checked": checked,
        "drifted": drifted,
        "drift_rate": (drifted / checked) if checked else 0.0,
        "mean_abs_delta": statistics.fmean(deltas) if deltas else 0.0,
    }
