"""
speech/segmenter.py — turn source narration into sentence-sized timed cues.

This is the heart of the speech-segment approach. Instead of arbitrary visual
panels, we cut the narration where the *speaker* naturally does:
  • at sentence punctuation (. ! ? … 。！？), and
  • at pauses (a gap ≥ CUE_GAP_SEC between words = a breath), and
  • before any cue grows past CUE_MAX_SEC.

Each cue carries its own start/end from the word timestamps, so the dub inherits
the source's rhythm — natural breaths between cues, no invented long silences.

    transcribe_to_cues(audio, lang, model) → [{"text","start","end"}, ...]

The pure grouping logic (group_words_into_cues) is engine-free and unit-tested;
only the Whisper call needs audio + a model.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import config

_SENTENCE_END = (".", "!", "?", "…", "。", "！", "？", "”", '"')


def group_words_into_cues(
    words:       List[dict],
    max_cue_sec: float = None,
    gap_sec:     float = None,
    min_cue_sec: float = None,
) -> List[dict]:
    """
    Group word dicts ({"word","start","end"}) into sentence-sized cues.

    A cue ends when: it hits sentence-ending punctuation and is at least
    min_cue_sec long; OR the gap to the next word is ≥ gap_sec (a breath); OR it
    would exceed max_cue_sec. Returns [{"text","start","end"}].
    """
    max_cue_sec = config.CUE_MAX_SEC if max_cue_sec is None else max_cue_sec
    gap_sec     = config.CUE_GAP_SEC if gap_sec     is None else gap_sec
    min_cue_sec = config.CUE_MIN_SEC if min_cue_sec is None else min_cue_sec

    cues: List[dict] = []
    cur:  List[dict] = []

    def _flush():
        if not cur:
            return
        text = "".join(w.get("word", "") for w in cur).strip()
        if text:
            cues.append({"text": text, "start": float(cur[0]["start"]), "end": float(cur[-1]["end"])})

    n = len(words)
    for i, w in enumerate(words):
        cur.append(w)
        dur      = float(w["end"]) - float(cur[0]["start"])
        nxt_gap  = (float(words[i + 1]["start"]) - float(w["end"])) if i + 1 < n else 1e9
        token    = (w.get("word") or "").strip()
        ends_sentence = token.endswith(_SENTENCE_END)

        if (ends_sentence and dur >= min_cue_sec) or nxt_gap >= gap_sec or dur >= max_cue_sec:
            _flush()
            cur = []

    _flush()
    return cues


def merge_short_cues(cues, short_sec=None, gap_sec=None, max_sec=None) -> List[dict]:
    """
    Merge fragment cues back into whole sentences. Whisper splits at every pause,
    so a comma mid-sentence ("The protagonist, / his mouth open, / roared.") makes
    tiny cues that dub choppily with dead air between them. We merge a cue into the
    previous one when the previous sentence isn't finished (no end punctuation) or
    this cue is very short — as long as they're close and not too long together.
    """
    short_sec = config.CUE_MERGE_SHORT_SEC if short_sec is None else short_sec
    gap_sec   = config.CUE_MERGE_GAP_SEC   if gap_sec   is None else gap_sec
    max_sec   = config.CUE_MERGE_MAX_SEC   if max_sec   is None else max_sec
    if not cues:
        return cues
    out = [dict(cues[0])]
    for c in cues[1:]:
        prev               = out[-1]
        gap                = float(c["start"]) - float(prev["end"])
        merged_dur         = float(c["end"]) - float(prev["start"])
        prev_ends_sentence = prev["text"].rstrip().endswith(_SENTENCE_END)
        # Merge ONLY when the previous cue is an unfinished sentence (Whisper split
        # it mid-sentence at a comma). NEVER merge two complete sentences — each
        # complete sentence is its own panel and must keep its own source time, or
        # the dub packs them up front and drifts off the panels.
        if not prev_ends_sentence and gap <= gap_sec and merged_dur <= max_sec:
            prev["text"] = (prev["text"].rstrip() + " " + c["text"].lstrip()).strip()
            prev["end"]  = float(c["end"])
        else:
            out.append(dict(c))
    return out


def transcribe_to_cues(
    audio_path: str,
    lang_code:  str,
    model=None,
    on_log: Optional[Callable] = None,
) -> List[dict]:
    """
    Transcribe `audio_path` with faster-whisper (word timestamps) and group into
    cues. Returns [] on any failure — callers can fall back. `model` may be a
    pre-loaded WhisperModel to amortise load cost across calls.
    """
    log = on_log or (lambda *a, **k: None)
    try:
        from faster_whisper import WhisperModel
        own = model is None
        if own:
            import runtime_settings as rs
            size = rs.get_str("cue_whisper_model", getattr(config, "CUE_WHISPER_MODEL", "small"))
            log(f"Loading Whisper ({size}) for cue segmentation …", "info")
            model = WhisperModel(size, device="cpu", compute_type="int8")

        segs, _ = model.transcribe(
            str(audio_path),
            language        = (lang_code if lang_code and lang_code != "en" else None),
            word_timestamps = True,
            beam_size       = 5,
            vad_filter      = True,
            vad_parameters  = {"min_silence_duration_ms": 300},
        )
        words: List[dict] = []
        for s in segs:
            for w in (s.words or []):
                words.append({"word": w.word, "start": w.start, "end": w.end})
        if own:
            del model
        if not words:
            return []
        raw  = group_words_into_cues(words)
        cues = merge_short_cues(raw)
        log(f"Segmented {len(words)} words → {len(raw)} raw cue(s) → {len(cues)} after "
            f"merging fragments:", "info")
        for i, c in enumerate(cues):
            log(f"   cue {i + 1:02d}  [{c['start']:6.2f}–{c['end']:6.2f}s · "
                f"{c['end'] - c['start']:.1f}s]  {c['text']}", "muted")
        return cues
    except Exception as exc:
        log(f"Cue segmentation failed: {exc}", "error")
        return []
