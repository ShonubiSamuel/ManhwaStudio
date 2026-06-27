"""
dub/aligner.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Forced-alignment pipeline for dubbing.

Extracted from DubEngine (Phase 3 of the dub pipeline).  All functions here
are pure — no database access, no subprocess calls, no global state.
DubEngine._split_one_language() calls these in sequence.

Pipeline
────────
    # Load Whisper once for all languages in the SYNC stage:
    model = load_whisper_model(log)
    try:
        for lang_code, audio_path in langs.items():
            words = transcribe_audio(audio_path, lang_code, log, model=model)
            if not words:
                timings = even_split_timings(audio_path, n_panels)
            else:
                timings = match_segments_to_words(panel_texts, words, log)
            snap_and_cut(audio_path, timings, output_paths, log)
    finally:
        del model

Dependencies (lazy-imported at call time)
──────────────────────────────────────────
    faster_whisper   — transcription
    rapidfuzz        — fuzzy token matching
    pydub            — audio slicing
    numpy            — vectorised RMS in snap_to_silence (with pydub fallback)
    detection_utils  — silence detection (even_split_timings fallback)
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

import config
from core.audio_utils import get_wav_duration


# ── Step 1 — Transcription ────────────────────────────────────────────────────

def load_whisper_model(log: Callable = print):
    """
    Load and return a WhisperModel for batch transcription.

    Load this ONCE before a loop over multiple languages, pass the returned
    model to each transcribe_audio() call, then delete it when done.  This
    avoids the 10–30 s model load overhead for every language.

    Example (inside DubEngine.align_and_split_all):
        model = load_whisper_model(self._log)
        try:
            for lc, wav_path in language_wavs.items():
                words = transcribe_audio(wav_path, lc, self._log, model=model)
                ...
        finally:
            del model

    Returns the loaded WhisperModel instance.
    Raises on failure — caller should catch and fall back to even_split_timings.
    """
    from faster_whisper import WhisperModel
    model_size = config.DUB_WHISPER_MODEL
    log(f"Loading Whisper model ({model_size}) for batch transcription …", "info")
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe_audio(
    audio_path: str,
    lang_code:  str,
    log:        Callable,
    model=None,
) -> List[dict]:
    """
    Transcribe audio_path with faster-whisper.

    model:
        Pass a pre-loaded WhisperModel from load_whisper_model() to amortise
        the model load cost across multiple languages in a single SYNC run.
        If None (default), a fresh model is loaded for this call alone and
        deleted immediately after — correct but slow for multi-language batches.

    Returns a flat list of word dicts:
        [{"word": str, "start": float, "end": float}, ...]

    Returns [] on any error — callers fall back to even_split_timings.
    """
    from faster_whisper import WhisperModel

    model_size  = config.DUB_WHISPER_MODEL
    _owns_model = model is None

    if _owns_model:
        log(f"  Transcribing with faster-whisper (model={model_size}) …", "info")
        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
        except Exception as exc:
            log(f"  Whisper model load error: {exc}", "error")
            return []
    else:
        log(f"  Transcribing [{lang_code}] with faster-whisper …", "info")

    try:
        segs_iter, _ = model.transcribe(
            audio_path,
            language        = lang_code if lang_code != "en" else None,
            word_timestamps = True,
            beam_size       = 5,
            vad_filter      = True,
            vad_parameters  = {"min_silence_duration_ms": 300},
        )
        words: List[dict] = []
        for seg in segs_iter:
            if seg.words:
                for w in seg.words:
                    word = w.word.strip()
                    if word:
                        words.append({"word": word, "start": w.start, "end": w.end})
        log(f"  {len(words)} words transcribed", "info")
        return words
    except Exception as exc:
        log(f"  Transcription error: {exc}", "error")
        return []
    finally:
        # Only unload the model when we created it here.  A caller-supplied
        # model is the caller's responsibility to delete.
        if _owns_model and model is not None:
            del model


# ── Step 2 — Fuzzy segment matching ──────────────────────────────────────────

def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> List[str]:
    return [t for t in normalize_text(text).split() if t]


def find_anchor(
    anchor_tokens: List[str],
    hay_tokens:    List[str],
    cursor:        int,
    search_window: int,
) -> Tuple[int, float]:
    """
    Slide a window across hay_tokens starting at cursor.
    Returns (best_start_index, normalised_edit_distance).
    Lower distance = better match.
    """
    from rapidfuzz.distance import Levenshtein

    a_len  = len(anchor_tokens)
    needle = " ".join(anchor_tokens)

    best_dist = float("inf")
    best_idx  = cursor
    end = max(cursor + 1,
              min(cursor + search_window,
                  len(hay_tokens) - a_len + 1))

    for i in range(cursor, end):
        window = " ".join(hay_tokens[i: i + a_len])
        d      = Levenshtein.distance(needle, window)
        if d < best_dist:
            best_dist = d
            best_idx  = i
            if d == 0:
                break

    norm_dist = best_dist / max(len(needle), 1)
    return best_idx, norm_dist


def match_segments_to_words(
    seg_texts: List[str],
    words:     List[dict],
    log:       Callable,
) -> List[dict]:
    """
    Match each panel text to a span in the Whisper word timeline.

    Returns [{"start": float, "end": float}, ...] — one timing per panel.
    Uses fuzzy anchor matching so minor transcription errors don't break alignment.
    """
    # Build flat token list with word-index back-pointer
    hay_tokens:   List[str] = []
    hay_word_idx: List[int] = []
    for i, w in enumerate(words):
        for part in normalize_text(w["word"]).split():
            if part:
                hay_tokens.append(part)
                hay_word_idx.append(i)

    start_tok_indices: List[int] = []
    cursor   = 0
    warnings = 0

    for i, seg_text in enumerate(seg_texts):
        seg_tokens = tokenize(seg_text)
        if not seg_tokens:
            start_tok_indices.append(cursor)
            continue

        anchor_len    = min(8, max(2, len(seg_tokens)))
        anchor        = seg_tokens[:anchor_len]
        search_window = max(150, min(500, len(seg_tokens) + 100))

        tok_idx, norm_dist = find_anchor(anchor, hay_tokens, cursor, search_window)
        start_tok_indices.append(tok_idx)

        if norm_dist > 0.40:
            log(
                f"  ⚠ panel {i + 1} low-confidence match "
                f"(dist={norm_dist:.2f}) — "
                f"anchor: '{' '.join(anchor[:5])}'",
                "warning",
            )
            warnings += 1

        cursor = tok_idx + 1

    if warnings:
        log(f"  {warnings} panel(s) had low match confidence — review those panels",
            "warning")

    # Convert token start indices → (start_sec, end_sec) pairs
    hay_len   = len(hay_word_idx)
    timings: List[dict] = []

    for i, tok_idx in enumerate(start_tok_indices):
        # Guard against token indices that exceed the transcribed word list.
        # This happens when fuzzy search finds no plausible match and the
        # cursor advances past the end of the token array.  The original
        # code clamped silently; now we log a warning so the user knows
        # something went wrong for these panels.
        if tok_idx >= hay_len:
            log(
                f"  ⚠ panel {i + 1}: token index {tok_idx} exceeds word array "
                f"({hay_len} tokens) — clamping to last word",
                "warning",
            )
            tok_idx = hay_len - 1

        w_i     = hay_word_idx[tok_idx]
        t_start = words[w_i]["start"]

        if i + 1 < len(start_tok_indices):
            next_tok = start_tok_indices[i + 1]
            if next_tok >= hay_len:
                next_tok = hay_len - 1
            w_next = hay_word_idx[next_tok]
            t_end  = words[w_next]["start"]
        else:
            t_end = words[-1]["end"]

        if t_end <= t_start:
            t_end = t_start + 0.5

        timings.append({"start": t_start, "end": t_end})
        log(
            f"  panel {i + 1}: {t_start:.2f}s → {t_end:.2f}s  "
            f'"{seg_texts[i][:50]}"',
            "muted",
        )

    return timings


def even_split_timings(audio_path: str, n: int) -> List[dict]:
    """
    Fallback: divide audio_path into n time-segments.

    Attempts silence-based splitting first using detect_silence_ffmpeg
    so cut points land in natural pause gaps rather than mid-word.  Falls
    back to equal-duration slices only when silence detection fails or finds
    no pauses.

    Used when transcribe_audio() returns [] (e.g. Whisper unavailable or
    audio contains no recognisable speech).
    """
    if n <= 0:
        return []

    dur = get_wav_duration(audio_path)

    if n == 1:
        return [{"start": 0.0, "end": dur}]

    # ── Attempt silence-based splitting ──────────────────────────────────────
    # Use permissive thresholds (short 80ms silences at -35 dBFS) so we find
    # any natural pause even in fast-paced narration.  The goal is just to
    # avoid landing in the middle of a word — exact timing precision is less
    # important here since this is already a degraded fallback path.
    try:
        from detection_utils import detect_silence_ffmpeg

        silences = detect_silence_ffmpeg(
            audio_path,
            min_silence_sec = 0.08,
            silence_db      = -35.0,
        )

        if silences:
            # Use silence midpoints as candidate cut locations.
            candidates = sorted((s + e) / 2.0 for s, e in silences)
            remaining  = list(candidates)

            # Pick n-1 cuts, each closest to the evenly-spaced target times.
            # This is a greedy O(n) approach — good enough for a fallback.
            cuts: List[float] = []
            for k in range(1, n):
                target = k * dur / n
                if remaining:
                    best = min(remaining, key=lambda c, t=target: abs(c - t))
                    cuts.append(best)
                    remaining.remove(best)
                else:
                    # No more silence candidates — use the equal-spaced target
                    cuts.append(target)

            cuts.sort()

            if len(cuts) == n - 1:
                boundaries = [0.0] + cuts + [dur]
                return [
                    {"start": boundaries[i], "end": boundaries[i + 1]}
                    for i in range(n)
                ]
    except Exception:
        pass   # silence detection failed — fall through to equal-duration

    # ── Equal-duration fallback ───────────────────────────────────────────────
    step = dur / n
    return [
        {"start": i * step, "end": (i + 1) * step}
        for i in range(n)
    ]


# ── Step 3 — Silence-snap and cut ────────────────────────────────────────────

def snap_to_silence(
    audio,
    cut_ms:    int,
    window_ms: int = 600,
    chunk_ms:  int = 10,
) -> Tuple[int, int]:
    """
    Scan ±(window_ms / 2) around cut_ms for the quietest chunk.
    Returns (snapped_ms, shift_ms).

    audio must be a pydub AudioSegment (passed in from snap_and_cut).

    Implementation
    ──────────────
    Uses numpy to compute RMS for all chunks simultaneously via a single
    reshape + vectorised sqrt(mean(x²)) call.

    The original implementation sliced audio[pos:pos+chunk_ms] inside a
    Python loop — 60 iterations for a 600ms window at 10ms chunks — each
    allocating a new pydub AudioSegment object.  For 300 panels that was
    18,000 object allocations per SYNC stage run.

    The numpy path extracts the search window into a contiguous PCM array
    once, reshapes it into (n_chunks, chunk_frames), and finds the minimum
    RMS row with np.argmin.  The result is identical to the pydub loop but
    without the per-chunk Python overhead.

    A pydub loop fallback is kept in case numpy is absent, though in practice
    numpy is always available as a transitive dependency of torch/faster-whisper.
    """
    half  = window_ms // 2
    start = max(0,          cut_ms - half)
    end   = min(len(audio), cut_ms + half)

    if start >= end - chunk_ms:
        # Window too narrow for even one chunk — nothing to snap.
        return cut_ms, 0

    try:
        import numpy as np

        # Extract the search window from the pydub segment once.
        window_seg  = audio[start:end]
        n_channels  = audio.channels
        sample_rate = audio.frame_rate

        # PCM bytes → int16 flat sample array.
        raw = np.frombuffer(window_seg.raw_data, dtype=np.int16)

        # For stereo/multi-channel audio, average across channels to get a
        # single mono energy value per frame.
        if n_channels > 1:
            raw = raw.reshape(-1, n_channels).mean(axis=1)

        # Frames per chunk (e.g. 160 frames at 16 kHz with chunk_ms=10).
        chunk_frames = max(1, int(sample_rate * chunk_ms / 1000))
        n_chunks     = len(raw) // chunk_frames

        if n_chunks == 0:
            return cut_ms, 0

        # Reshape to (n_chunks, chunk_frames) and compute RMS per row.
        # We cast to float32 to avoid int16 overflow when squaring.
        chunks    = raw[:n_chunks * chunk_frames].reshape(n_chunks, chunk_frames).astype(np.float32)
        rms_vals  = np.sqrt(np.mean(chunks ** 2, axis=1))
        best_idx  = int(np.argmin(rms_vals))

        # Map chunk index → absolute millisecond position.
        # Chunk k starts at ms offset k * chunk_ms from `start`, so:
        #   best_ms = start + best_idx * chunk_ms
        # This is the direct equivalent of `pos` in the original pydub loop.
        best_ms = start + best_idx * chunk_ms
        return best_ms, best_ms - cut_ms

    except ImportError:
        # numpy unavailable — fall back to the pydub per-chunk loop.
        best_ms  = cut_ms
        best_rms = float("inf")
        for pos in range(start, end - chunk_ms, chunk_ms):
            rms = audio[pos: pos + chunk_ms].rms
            if rms < best_rms:
                best_rms = rms
                best_ms  = pos
        return best_ms, best_ms - cut_ms


def snap_and_cut(
    audio_path:   str,
    timings:      List[dict],
    output_paths: List[str],
    log:          Callable,
):
    """
    Convert timings → snapped millisecond boundaries, then cut the
    continuous WAV into per-panel files using pydub.

    Snap window is config.DUB_SNAP_WINDOW_MS.
    Panels with empty ranges after snapping are skipped with a warning.
    """
    from pydub import AudioSegment

    audio    = AudioSegment.from_file(audio_path)
    total_ms = len(audio)
    snap_win = config.DUB_SNAP_WINDOW_MS

    # Build N+1 boundaries from N timings
    raw_boundaries = [int(timings[0]["start"] * 1000)]
    for t in timings:
        raw_boundaries.append(int(t["end"] * 1000))

    # Snap each interior boundary to the nearest silence
    snapped = [raw_boundaries[0]]
    for raw_ms in raw_boundaries[1:]:
        snapped_ms, shift = snap_to_silence(audio, raw_ms, snap_win)
        snapped.append(snapped_ms)
        sign = "+" if shift >= 0 else "-"
        log(
            f"  cut {raw_ms / 1000:.2f}s → "
            f"{snapped_ms / 1000:.2f}s "
            f"({sign}{abs(shift)}ms snap)",
            "muted",
        )

    # Export each panel slice
    for i, out_path in enumerate(output_paths):
        s_ms = max(0,        snapped[i])
        e_ms = min(total_ms, snapped[i + 1])
        if e_ms <= s_ms:
            log(f"  panel {i + 1} empty after snap — skipping", "warning")
            continue
        audio[s_ms:e_ms].export(out_path, format="wav")