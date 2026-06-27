"""
speech/wordsplit.py — split a CONTINUOUS dubbed read back into per-cue pieces.

Why a continuous read: generating each cue in isolation makes the voice "reset"
every couple of seconds. One long read keeps the voice flowing — but then we must
cut it back into cues so each lands at its own source time, and the cut quality is
everything (a mid-word cut ruins the dub).

How we cut — FORCED ALIGNMENT BY CONTENT (the robust, research-backed method):
  1. Transcribe the read with word-level timestamps (faster-whisper, already
     cached locally — no new model download, so it works on a flaky network).
  2. Align the RECOGNISED word sequence to the KNOWN cue texts by *content*
     (difflib sequence alignment), not by word count. Counting drifts whenever
     the ASR hears a different number of words (contractions, mis-hearings) and
     causes the mid-word / from-the-start cuts. Content alignment anchors each
     cut on the actual word where one cue ends and the next begins.
  3. Snap each boundary to the nearest real pause (lowest-energy point) so the
     cut is clean, and keep cuts strictly increasing.

If transcription is unavailable (offline, model missing), we fall back to a
pure energy/silence split, then to a proportional split — so the dub never
crashes. Always returns exactly len(cue_texts) pieces.

    split_read(y, sr, cue_texts, lang_code) -> [np.ndarray per cue]
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, List, Optional

import config

# Language NAME (as used elsewhere in the app, e.g. "French") → Whisper ISO code.
_LANG_ISO = {
    "english": "en", "french": "fr", "spanish": "es", "german": "de",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "polish": "pl",
    "russian": "ru", "japanese": "ja", "korean": "ko", "chinese": "zh",
    "arabic": "ar", "hindi": "hi", "turkish": "tr", "vietnamese": "vi",
    "indonesian": "id", "thai": "th",
}


def _iso(lang_code: str) -> Optional[str]:
    if not lang_code:
        return None
    lc = lang_code.strip().lower()
    if lc in _LANG_ISO:
        return _LANG_ISO[lc]
    if len(lc) == 2:                       # already an ISO code
        return lc
    return None                            # unknown → let Whisper auto-detect


def _norm(w: str) -> str:
    """Lowercase, strip accents and punctuation — so 'L'épée,' and 'lepee' match."""
    w = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", w.lower())


def _words(text: str) -> List[str]:
    return [t for t in (_norm(t) for t in text.split()) if t]


# ─────────────────────────────────────────────────────────────────────────────
# Energy / silence helpers (clean-cut snapping + offline fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _proportional(y, cue_texts: List[str]):
    """Last-resort cut purely by character weight (may cut mid-word)."""
    n = len(cue_texts)
    weights = [max(1, len(t.strip())) for t in cue_texts]
    total = sum(weights)
    pieces, pos, acc = [], 0, 0
    for k in range(n):
        acc += weights[k]
        end = len(y) if k == n - 1 else int(len(y) * acc / total)
        pieces.append(y[pos:end]); pos = end
    return pieces


def strip_lead_segment(y, sr: int, min_gap_s: float = 0.25, min_speech_s: float = 0.20,
                       max_strip_s: float = 4.0, on_log: Optional[Callable] = None):
    """Remove a throwaway warm-up utterance at the very start of the read.

    The warm-up is one short fluent sentence followed by a pause. We find the
    FIRST silence gap (≥ min_gap_s) that comes after at least min_speech_s of
    speech, and cut there — so everything up to and including the warm-up + its
    trailing pause is discarded, ASR-free. Returns (trimmed_audio, stripped_secs).
    Falls back to the original audio if no clear pause is found (never breaks)."""
    import numpy as np
    log = on_log or (lambda *a, **k: None)
    env, frame = _envelope(y, sr)
    vb = _voiced_bounds(env, sr, frame)
    if vb is None:
        return y, 0.0
    f_first, f_last, thr = vb
    speech_frames = 0
    i = f_first
    while i <= f_last:
        if env[i] > thr:
            speech_frames += 1
            i += 1
        else:
            j = i
            while j <= f_last and env[j] <= thr:
                j += 1
            gap = (j - i) * frame / sr
            if speech_frames * frame / sr >= min_speech_s and gap >= min_gap_s:
                cut = int((i + j) / 2 * frame)        # middle of the pause
                if cut / sr <= max_strip_s:
                    log(f"   warm-up: stripped {cut / sr:.2f}s lead segment", "muted")
                    return y[cut:], cut / sr
                return y, 0.0
            i = j
    return y, 0.0


def _envelope(y, sr: int, frame_ms: float = 20.0):
    import numpy as np
    frame = max(1, int(sr * frame_ms / 1000.0))
    n_frames = len(y) // frame
    if n_frames < 1:
        return np.array([0.0], dtype="float32"), frame
    trimmed = y[: n_frames * frame].reshape(n_frames, frame)
    env = np.sqrt(np.mean(trimmed.astype("float64") ** 2, axis=1) + 1e-12)
    return env.astype("float32"), frame


def _voiced_bounds(env, sr: int, frame: int):
    """First/last voiced frame index and the silence threshold."""
    import numpy as np
    peak = float(env.max())
    floor = float(np.percentile(env, 10))
    thr = max(floor * 1.8, peak * 0.06)
    voiced = np.where(env > thr)[0]
    if len(voiced) == 0:
        return None
    guard = max(1, int(0.02 * sr / frame))
    f_first = max(0, voiced[0] - guard)
    f_last = min(len(env) - 1, voiced[-1] + guard)
    return f_first, f_last, thr


def _snap_to_pause(env, frame, sr, f_first, f_last, est_t):
    """Move an estimated cut time to the quietest frame within the snap window."""
    import numpy as np
    win_f = max(1, int(max(0.05, config.DUB_SNAP_WINDOW_MS / 1000.0) * sr / frame))
    est_f = int(round(est_t * sr / frame))
    lo, hi = max(f_first + 1, est_f - win_f), min(f_last, est_f + win_f)
    if hi <= lo:
        cut_f = min(max(est_f, f_first + 1), f_last)
    else:
        cut_f = lo + int(np.argmin(env[lo:hi]))
    return (cut_f + 0.5) * frame / sr


def _energy_split(y, sr: int, cue_texts: List[str], log):
    """Offline fallback: estimate boundaries by character share, assign each to a
    DISTINCT pause (so no cue collapses), snap, and cut."""
    import numpy as np
    n = len(cue_texts)
    env, frame = _envelope(y, sr)
    if len(env) < n * 3:
        return _proportional(y, cue_texts)
    vb = _voiced_bounds(env, sr, frame)
    if vb is None:
        return _proportional(y, cue_texts)
    f_first, f_last, thr = vb
    start_t = f_first * frame / sr
    end_t = min(len(y) / sr, (f_last + 1) * frame / sr)
    speech_dur = max(1e-3, end_t - start_t)

    weights = [max(1, len(t.strip())) for t in cue_texts]
    tot = sum(weights)
    est, cum = [], 0
    for i in range(n - 1):
        cum += weights[i]
        est.append(start_t + speech_dur * cum / tot)

    # distinct pauses
    min_run = max(1, int(0.06 * sr / frame))
    centers, i = [], f_first
    while i <= f_last:
        if env[i] <= thr:
            j = i
            while j <= f_last and env[j] <= thr:
                j += 1
            if j - i >= min_run:
                centers.append((i + j) / 2.0 * frame / sr)
            i = j
        else:
            i += 1

    if len(centers) >= (n - 1):
        cuts = _assign_pauses(centers, est)
    else:
        cuts = [_snap_to_pause(env, frame, sr, f_first, f_last, t) for t in est]

    return _slice(y, sr, _monotonic([start_t] + sorted(cuts) + [end_t]), n, log,
                  "energy/silence")


def _assign_pauses(centers: List[float], est: List[float]) -> List[float]:
    """Pick one DISTINCT pause per boundary, in order, minimising squared
    distance to the estimates (small DP) — stops two boundaries collapsing."""
    import numpy as np
    m, k = len(centers), len(est)
    INF = float("inf")
    cost = [[INF] * m for _ in range(k)]
    back = [[-1] * m for _ in range(k)]
    for j in range(m):
        cost[0][j] = (centers[j] - est[0]) ** 2
    for i in range(1, k):
        prefix_min, prefix_arg = INF, -1
        for j in range(m):
            if j - 1 >= 0 and cost[i - 1][j - 1] < prefix_min:
                prefix_min, prefix_arg = cost[i - 1][j - 1], j - 1
            if prefix_min < INF:
                cost[i][j] = prefix_min + (centers[j] - est[i]) ** 2
                back[i][j] = prefix_arg
    end = int(np.argmin([cost[k - 1][j] for j in range(m)]))
    idxs = [0] * k
    idxs[k - 1] = end
    for i in range(k - 1, 0, -1):
        idxs[i - 1] = back[i][idxs[i]]
    return [centers[j] for j in idxs]


def _monotonic(bounds: List[float]) -> List[float]:
    for j in range(1, len(bounds)):
        if bounds[j] <= bounds[j - 1]:
            bounds[j] = bounds[j - 1] + 0.02
    return bounds


def _slice(y, sr: int, bounds: List[float], n: int, log, how: str):
    pieces = []
    for k in range(n):
        a = int(max(0.0, bounds[k]) * sr)
        b = int(min(len(y) / sr, bounds[k + 1]) * sr)
        pieces.append(y[a:b] if b > a else y[a:a + 1])
    log(f"   split: {n} cue piece(s) via {how}", "muted")
    return pieces


# ─────────────────────────────────────────────────────────────────────────────
# Forced alignment by content
# ─────────────────────────────────────────────────────────────────────────────

def _transcribe_words(y, sr: int, lang_code: str, log) -> List[dict]:
    try:
        import os, tempfile
        import soundfile as sf
        import runtime_settings as rs
        from faster_whisper import WhisperModel
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, y, sr)
        size = rs.get_str("dub_whisper_model", getattr(config, "DUB_WHISPER_MODEL", "small"))
        model = WhisperModel(size, device="cpu", compute_type="int8")
        segs, _ = model.transcribe(tmp.name, language=_iso(lang_code),
                                   word_timestamps=True, beam_size=5)
        words = [{"w": _norm(w.word), "start": float(w.start), "end": float(w.end)}
                 for s in segs for w in (s.words or []) if _norm(w.word)]
        del model
        try: os.unlink(tmp.name)
        except OSError: pass
        return words
    except Exception as exc:
        log(f"   split: transcription unavailable ({exc}) — energy fallback", "muted")
        return []


def _map_words_to_cues(rec_words: List[str], cue_texts: List[str]) -> List[int]:
    """Return cue_of[j] = which cue each recognised word j belongs to, by aligning
    the recognised word sequence to the expected (concatenated) cue words."""
    exp_words, exp_cue = [], []
    for i, text in enumerate(cue_texts):
        for w in _words(text):
            exp_words.append(w); exp_cue.append(i)

    cue_of = [-1] * len(rec_words)
    sm = SequenceMatcher(a=exp_words, b=rec_words, autojunk=False)
    for ai, bj, size in sm.get_matching_blocks():
        for k in range(size):
            cue_of[bj + k] = exp_cue[ai + k]

    # Fill gaps: carry the last known cue forward, then back-fill the head.
    last = -1
    for j in range(len(cue_of)):
        if cue_of[j] == -1:
            cue_of[j] = last
        else:
            last = cue_of[j]
    first_known = next((c for c in cue_of if c != -1), 0)
    for j in range(len(cue_of)):
        if cue_of[j] == -1:
            cue_of[j] = first_known
        else:
            break
    return cue_of


def split_read(
    y, sr: int, cue_texts: List[str], lang_code: str,
    on_log: Optional[Callable] = None,
):
    log = on_log or (lambda *a, **k: None)
    n = len(cue_texts)
    if n <= 1:
        return [y]

    import numpy as np

    words = _transcribe_words(y, sr, lang_code, log)
    if len(words) < n:                      # too little to align → energy split
        return _energy_split(y, sr, cue_texts, log)

    cue_of = _map_words_to_cues([w["w"] for w in words], cue_texts)

    # Boundary between cue i and i+1 = midpoint of (last word of i, first word of i+1).
    est = []
    ok = True
    for i in range(n - 1):
        last_i = max((j for j in range(len(words)) if cue_of[j] == i), default=None)
        first_n = min((j for j in range(len(words)) if cue_of[j] == i + 1), default=None)
        if last_i is None or first_n is None or first_n <= last_i:
            ok = False
            break
        est.append((words[last_i]["end"] + words[first_n]["start"]) / 2.0)
    if not ok:
        return _energy_split(y, sr, cue_texts, log)

    # Snap each content boundary to the nearest real pause for a click-free cut,
    # and trim leading/trailing silence.
    env, frame = _envelope(y, sr)
    vb = _voiced_bounds(env, sr, frame)
    if vb is None:
        return _energy_split(y, sr, cue_texts, log)
    f_first, f_last, _ = vb
    start_t = max(0.0, words[0]["start"] - 0.04, f_first * frame / sr)
    end_t = min(len(y) / sr, words[-1]["end"] + 0.06, (f_last + 1) * frame / sr)

    cuts = [_snap_to_pause(env, frame, sr, f_first, f_last, t) for t in est]
    return _slice(y, sr, _monotonic([start_t] + sorted(cuts) + [end_t]), n, log,
                  "forced alignment (content)")
