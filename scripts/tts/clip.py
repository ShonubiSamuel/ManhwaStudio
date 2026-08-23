"""
tts/clip.py — the ONE place a single TTS clip is generated and cleaned.

Every feature that produces speech routes through here:
  • Voices preview / test        (voices._run_quick_tts)
  • Recap / Voiceover / Refine   (speech._run_dub_cues)
  • Per-cue redub                (speech._run_redub_cue)
  • Voice design sample          (voices._run_voice_design)

so a voice-level fix (warm-up strip, hum/onset cleanup, silence trim, levelling)
applies EVERYWHERE automatically. Timeline concerns — cue grouping, track
assembly, mastering — stay in the dub layer; this module owns only per-clip
generation and cleanup.

    synth_clips(profile, texts, out_paths, …)  → generate N clips (one model
                                                  load) + clean each.
    synth_clip(profile, text, out_path, …)     → the single-clip convenience.
    clean_clip(path, …)                        → the shared cleanup (also used
                                                  by the dub after its batched
                                                  synthesis).
    warmup_word()                              → the per-clip warm-up prefix.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

import config
from tts import synth


def _noop(*_a, **_k) -> None:
    pass


def _stage_path(p: Path, tag: str) -> Path:
    """Sibling audit-trail filename for a cue clip: cue_0000_0.wav → cue_0000_<tag>.wav."""
    base = p.stem
    if base.endswith("_0"):
        base = base[:-2]
    return p.with_name(f"{base}_{tag}.wav")


def speakable(text: str) -> str:
    """Normalise written-prose punctuation into marks the TTS model actually
    voices, so a preview reads the same as a dub. The engine doesn't pause on
    em/en-dashes (it glues the surrounding words) and over-runs semicolons and
    parentheticals — rewrite them into comma/period pauses. Shared by the Voices
    preview and every dub (speech._speakable delegates here)."""
    t = re.sub(r"\s*[—–]\s*", ", ", text or "")          # em/en dash → comma pause
    t = t.replace("…", "...").replace(";", ",")
    t = re.sub(r"\s*\(([^)]*)\)", r", \1,", t)           # parentheticals → comma clause
    return re.sub(r"\s{2,}", " ", re.sub(r",\s*,", ",", t)).strip()


# Words that end in a period without ending a sentence — don't break after them,
# or a preview reads "Meet Mr. [pause] Smith" with a spurious mid-sentence gap.
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "sr", "jr", "prof", "vs", "etc",
           "inc", "ltd", "co", "no", "vol", "fig", "gen", "col", "capt", "sgt"}


def _no_break_after(sentence: str) -> bool:
    """True when `sentence`'s last token is an abbreviation or a single-letter
    initial (e.g. 'Mr.', 'A.') — so the next piece belongs to the same sentence."""
    m = re.search(r"([A-Za-z]+)\.$", sentence)
    if not m:
        return False
    w = m.group(1).lower()
    return w in _ABBREV or len(w) == 1


def split_sentences(text: str) -> List[str]:
    """Split a passage into sentence-sized pieces (Latin + CJK end marks), so a
    long preview is generated as a BATCH of clips — each with its own warm-up
    'running start' and cleanup — instead of one long single-warm-up generation.
    Tiny fragments and abbreviation/initial boundaries are merged back so we never
    strand a warm-up on two characters or pause mid-sentence. Returns [text] when
    it's already one sentence, so short previews behave exactly as before."""
    t = (text or "").strip()
    if not t:
        return []
    # Break after a sentence-ending mark, whether followed by space (Latin) or
    # not (CJK runs have no spaces). Keep the mark with its sentence.
    rough = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", t)
    out: List[str] = []
    for piece in rough:
        piece = (piece or "").strip()
        if not piece:
            continue
        # Merge into the previous sentence when this is a tiny fragment, or the
        # previous "sentence" only ended on an abbreviation / single-letter initial.
        if out and (len(piece) < 3 or _no_break_after(out[-1])):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out or [t]


def warmup_word() -> str:
    """The throwaway warm-up word prepended to every clip's generation (then
    stripped) to stabilise the model's first token. '' when disabled in config."""
    if not bool(getattr(config, "DUB_WARMUP_PER_CUE", False)):
        return ""
    return (getattr(config, "DUB_WARMUP_WORD", "") or "").strip()


def comfortable_cps(lang_code: str) -> float:
    from speech import cps
    return cps.comfortable_cps(lang_code)


def _level_clip(path: str, target_rms: float = 0.08, max_gain: float = 4.0) -> None:
    """Bring a clip toward a common loudness so clips don't jump in volume."""
    import soundfile as sf
    import numpy as np
    try:
        y, sr = sf.read(path, dtype="float32", always_2d=False)
    except Exception:
        return
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    rms = float(np.sqrt(np.mean(y ** 2))) if len(y) else 0.0
    if rms < 0.005:                       # near-silent — don't amplify noise
        return
    y = y * min(target_rms / rms, max_gain)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0.99:
        y = y * (0.99 / peak)
    try:
        sf.write(path, y, sr)
    except Exception:
        pass


def _warmup_cut_time(y, sr: int, lo_s: float = 0.22, hi_s: float = 1.05,
                     quiet: float = 0.14) -> Optional[float]:
    """Find where a prepended warm-up word ENDS, for clips where the model runs
    the warm-up straight into the sentence with no real pause (so the pause-based
    strip_lead_segment can't see it). We look for the FIRST brief energy trough
    after lo_s (past the warm word) and before hi_s, and return its time in
    seconds — the model still dips between the throwaway word and the first real
    word even when it doesn't pause. Returns None when there's no clear dip."""
    import numpy as np
    frame = max(1, int(sr * 0.01))          # 10 ms — fine enough to see word dips
    n = len(y) // frame
    if n < 3:
        return None
    env = np.sqrt(np.mean(y[:n * frame].reshape(n, frame).astype("float64") ** 2, axis=1) + 1e-9)
    pk = float(env.max()) or 1.0
    env = env / pk
    lo, hi = int(lo_s / 0.01), min(int(hi_s / 0.01), n)
    if lo >= hi or env[:lo].max() < 0.35:   # need the warm word's own speech before the window
        return None
    for i in range(lo, hi):
        if env[i] < quiet:                  # a trough → boundary between warm word and sentence
            j = i
            while j + 1 < hi and env[j + 1] <= env[i]:
                j += 1
            return (j + 0.5) * frame / sr
    return None


def clean_clip(path: str, *, strip_warmup: str = "", comf: float = 14.0,
               on_log: Optional[Callable] = None) -> None:
    """Voice-level cleanup applied to EVERY generated clip, in place:
      • strip the prepended warm-up word (bounded so it can't eat real speech),
      • remove a 'hmm/mmm' lead-in and the shaky first-token onset wobble,
      • trim near-silence at both ends,
      • level to a common loudness.
    The single source of truth for per-clip processing.
    """
    import soundfile as sf
    from speech import aligner
    from speech.wordsplit import strip_lead_segment
    log = on_log or _noop
    p = Path(path)
    if not p.exists():
        return
    try:
        y, sr = sf.read(str(p))
        if getattr(y, "ndim", 1) > 1:
            y = y.mean(axis=1)
        if strip_warmup:
            # Remove the prepended warm-up word. Preferred: cut at the brief energy
            # dip right after it — this is the ONLY reliable signal when the model
            # runs the warm-up straight into the sentence with no pause (the common
            # failure that leaked "Bon"/"波恩" into clips).
            t = _warmup_cut_time(y, sr)
            if t is not None and 0.15 <= t <= 1.1:
                y = y[int(t * sr):]
                log(f"warm-up: dip-trimmed {t:.2f}s lead", "muted")
            else:
                # Fallback for clips that DO pause after the warm-up: cut at the first
                # real pause, else an estimated word length. Decoupled from `comf`
                # (the sentence CPS) — a warm-up word's length doesn't scale with how
                # fast you want sentences, so a high CPS_COMFORTABLE must not shrink it.
                warm_est = max(0.65, len(strip_warmup) / 9.0 + 0.30)
                y2, stripped = strip_lead_segment(y, sr, max_strip_s=warm_est * 1.8, on_log=log)
                if stripped < 0.15:
                    cut = int(min(warm_est, len(y) / sr * 0.6) * sr)
                    y2 = y[cut:]
                    log(f"warm-up: estimate-trimmed {cut / sr:.2f}s lead", "muted")
                y = y2
        y = aligner.strip_leading_hum(y, sr)   # remove a 'hmm/mmm' lead-in
        y = aligner.trim_onset_wobble(y, sr)   # remove the shaky first-token onset
        y = aligner._trim_silence(y, sr)       # trim near-silence both ends
        sf.write(str(p), y, sr)
    except Exception as exc:
        log(f"clip cleanup failed for {p.name}: {exc}", "warning")
    _level_clip(str(p))


def synth_clips(profile, texts: List[str], out_paths: List[str], *,
                warmups: Optional[List[str]] = None, clean: bool = True,
                save_stages: bool = False, comf: float = 14.0,
                on_log: Optional[Callable] = None, timeout: int = 1800) -> bool:
    """Generate one or more clips for ONE voice in a single model load (the shared
    generation path), then clean EACH via clean_clip. `warmups[i]` is prepended to
    texts[i] and stripped afterward. When `save_stages`, keep a per-clip audit trail
    (cue_NNNN_raw.wav = untouched model output, cue_NNNN_clean.wav = post-cleanup).
    Returns True iff every clip was produced."""
    import shutil
    log = on_log or _noop
    warmups = warmups or [""] * len(texts)
    gen_texts = [(f"{w} {t}" if w else t) for w, t in zip(warmups, texts)]
    script = synth.build_synth_script(
        profile=profile, sentences=gen_texts,
        output_paths=[str(p) for p in out_paths], skip_indices=set())
    r = subprocess.run(
        [synth.synth_python(profile), "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=synth.synth_env(profile))
    ok = all(Path(p).exists() for p in out_paths)
    if not ok:
        log("synthesis: some clips were not produced.\n"
            + (r.stdout or "")[-300:] + "\n" + (r.stderr or "")[-400:], "error")
    if save_stages:                       # snapshot the UNTOUCHED model output first;
        for p in out_paths:               # the cleaned version IS the working cue_NNNN_0.wav
            pp = Path(p)
            if pp.exists():
                try: shutil.copy2(pp, _stage_path(pp, "raw"))
                except OSError: pass
    if clean:
        for p, w in zip(out_paths, warmups):
            if Path(p).exists():
                clean_clip(str(p), strip_warmup=w, comf=comf, on_log=log)
    return ok


def synth_clip(profile, text: str, out_path: str, *, warmup: str = "",
               clean: bool = True, save_stages: bool = False, comf: float = 14.0,
               on_log: Optional[Callable] = None, timeout: int = 600) -> bool:
    """Generate + clean a SINGLE clip — the common path for redub, one-liners,
    etc. Thin convenience over synth_clips for one text."""
    return synth_clips(profile, [text], [out_path], warmups=[warmup], clean=clean,
                       save_stages=save_stages, comf=comf, on_log=on_log, timeout=timeout)


def synth_text(profile, text: str, out_path: str, *, warmup: str = "",
               clean: bool = True, comf: float = 14.0, gap: float = 0.28,
               on_log: Optional[Callable] = None, timeout: int = 1800) -> bool:
    """Generate ONE wav for a free-form passage — the Voices preview / test path.

    This is the SAME per-clip pipeline the dub uses, applied to arbitrary text:
      1. normalise punctuation via speakable() (identical to the dub),
      2. split into sentences and synthesise them as a BATCH (one model load) —
         so a long passage gets a warm-up 'running start' + cleanup on EVERY
         sentence, not just the first (this is what removes the mid-text
         wobble/hiccup), then
      3. concatenate the cleaned clips into out_path with a natural `gap`.
    A single-sentence passage is just one cleaned clip, so short previews are
    unchanged. Timeline concerns (breathing, slot-fitting) stay in the dub layer.
    """
    from core.audio_utils import concat_wavs
    log = on_log or _noop
    sentences = split_sentences(speakable(text))
    if not sentences:
        log("nothing to synthesise (empty text)", "warning")
        return False

    out = Path(out_path)
    if len(sentences) == 1:
        return synth_clip(profile, sentences[0], str(out), warmup=warmup,
                          clean=clean, comf=comf, on_log=log, timeout=timeout)

    log(f"batching {len(sentences)} sentences (one model load, warm-up per clip)", "muted")
    work = out.parent / f".{out.stem}_parts"
    work.mkdir(parents=True, exist_ok=True)
    parts = [str(work / f"{i:03d}.wav") for i in range(len(sentences))]
    ok = synth_clips(profile, sentences, parts, warmups=[warmup] * len(sentences),
                     clean=clean, comf=comf, on_log=log, timeout=timeout)
    produced = [p for p in parts if Path(p).exists()]
    if not produced:
        return False
    joined = concat_wavs(produced, str(out), silence_secs=gap)
    for p in parts:
        try: Path(p).unlink()
        except OSError: pass
    try: work.rmdir()
    except OSError: pass
    return bool(joined) and out.exists()
