"""
eval/references.py — reference-based translation and ASR scoring.

These are the metrics that let you say something about *quality* rather than
fit: BLEU/chrF against human translations, WER/CER against human transcripts.
They need reference data, so unlike metrics.py they cannot run on the sessions
already on disk — you have to supply references first.

sacrebleu and jiwer are imported lazily and are optional dependencies. The
harness degrades to fit-only metrics rather than failing when they are absent,
so `python -m scripts.eval` always works.

Reference file format (JSON), keyed by session id:

    {
      "42": {
        "translations": ["reference line 1", "reference line 2", ...],
        "transcript":   ["source line 1", "source line 2", ...]
      }
    }

`translations` are references for the target-language output, in cue order.
`transcript` are references for the ASR stage, also in cue order. Either may be
omitted. Use an empty string to skip an individual cue.
"""

from __future__ import annotations

from typing import Any, Sequence


class MissingDependency(RuntimeError):
    """Raised when a metric is requested but its library is not installed."""


def _require(module: str, package: str):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MissingDependency(
            f"{package} is required for this metric. Install it with: pip install {package}"
        ) from exc


def available() -> dict[str, bool]:
    """Which optional scorers are installed."""
    out = {}
    for module, name in (("sacrebleu", "sacrebleu"), ("jiwer", "jiwer")):
        try:
            __import__(module)
            out[name] = True
        except ImportError:
            out[name] = False
    return out


def _paired(hyps: Sequence[str], refs: Sequence[str]) -> tuple[list[str], list[str]]:
    """
    Align hypotheses to references, dropping pairs where either side is blank.

    Scoring a blank reference silently destroys the score, so pairs are dropped
    explicitly and the count is reported rather than hidden.
    """
    h_out, r_out = [], []
    for h, r in zip(hyps, refs):
        if (h or "").strip() and (r or "").strip():
            h_out.append(h.strip())
            r_out.append(r.strip())
    return h_out, r_out


def score_translation(hyps: Sequence[str], refs: Sequence[str]) -> dict[str, Any]:
    """
    Corpus BLEU and chrF for translated cues.

    chrF is reported alongside BLEU because BLEU is unreliable on the short,
    single-sentence segments a dub produces — chrF's character n-grams degrade
    more gracefully at that length.
    """
    sacrebleu = _require("sacrebleu", "sacrebleu")
    h, r = _paired(hyps, refs)
    if not h:
        return {"scored": 0, "skipped": len(hyps), "bleu": None, "chrf": None}
    return {
        "scored": len(h),
        "skipped": len(hyps) - len(h),
        "bleu": round(sacrebleu.corpus_bleu(h, [r]).score, 2),
        "chrf": round(sacrebleu.corpus_chrf(h, [r]).score, 2),
    }


def score_asr(hyps: Sequence[str], refs: Sequence[str]) -> dict[str, Any]:
    """Word and character error rate for the transcription stage."""
    jiwer = _require("jiwer", "jiwer")
    h, r = _paired(hyps, refs)
    if not h:
        return {"scored": 0, "skipped": len(hyps), "wer": None, "cer": None}
    return {
        "scored": len(h),
        "skipped": len(hyps) - len(h),
        "wer": round(jiwer.wer(r, h), 4),
        "cer": round(jiwer.cer(r, h), 4),
    }
