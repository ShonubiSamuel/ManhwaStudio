"""
speech/dub_cues.py — synthesize translated cues and lay them on one track.

Ties Phase 3 together:
  1. TTS every cue's translated text in ONE subprocess (one model load → one
     consistent voice across the whole language), via the existing synth engine.
  2. Hand the clips + their source start times to the aligner, which places them
     at the source timing (preserving natural breaths) and writes the track.

    dub_cues(cues, "fr", voice_profile, total_duration, out_path) -> bool
"""

from __future__ import annotations

import subprocess
import tempfile
from copy import copy
from pathlib import Path
from typing import Callable, List, Optional

import config
from speech import aligner


def dub_cues(
    cues:           List[dict],
    lang_code:      str,
    voice_profile,
    total_duration: float,
    out_path:       str,
    on_log:         Optional[Callable] = None,
) -> bool:
    """cues must each carry 'translated' + 'start'. Writes the dubbed track to out_path."""
    log = on_log or (lambda *a, **k: None)
    from tts import synth
    import runtime_settings as rs

    if not any((c.get("translated") or "").strip() for c in cues):
        log(f"[{lang_code}] no translated text to dub", "warning")
        return False

    # Group cues into CONTINUOUS READS so Qwen3 flows across several cues (the
    # voice stays consistent instead of resetting every cue). A read holds up to
    # `bsize` cues AND ≤ read_max seconds, then it's word-split back per cue.
    bsize    = max(1, rs.get_int("dub_cue_batch", getattr(config, "DUB_CUE_BATCH", 8)))
    read_max = rs.get_float("dub_read_max_sec", getattr(config, "DUB_READ_MAX_SEC", 30))
    batches, cur, cur_dur = [], [], 0.0
    for c in cues:
        d = float(c["end"]) - float(c["start"])
        if cur and (len(cur) >= bsize or cur_dur + d > read_max):
            batches.append(cur); cur, cur_dur = [], 0.0
        cur.append(c); cur_dur += d
    if cur:
        batches.append(cur)

    def _join(batch):
        parts = []
        for c in batch:
            t = (c.get("translated") or "").strip()
            if t and t[-1] not in ".!?…。！？":
                t += "."                       # clean sentence breaks for the model
            if t:
                parts.append(t)
        return " ".join(parts)

    texts = [_join(b) for b in batches]
    work  = Path(tempfile.mkdtemp(prefix="cuedub_"))
    outs  = [str(work / f"batch_{i:04d}.wav") for i in range(len(batches))]
    skip  = {i for i, t in enumerate(texts) if not t.strip()}

    prof = copy(voice_profile)
    prof.language = config.SUPPORTED_LANGUAGES.get(lang_code, "Auto")

    eng  = synth.engine_for(prof)
    mode = ("PER-CUE (no split)" if bsize == 1
            else f"CONTINUOUS reads (≤{bsize} cues / ≤{read_max:.0f}s), word-split")
    n_reads = len(texts) - len(skip)
    log(f"[{lang_code}] DUB — {mode} via {eng}; {len(cues)} cue(s) in {n_reads} read(s), "
        f"ONE process (model + clone prompt loaded once → consistent voice)", "accent")
    script = synth.build_synth_script(prof, texts, outs, skip)   # ONE subprocess → one voice
    try:
        proc = subprocess.Popen(
            [synth.synth_python(prof), "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=synth.synth_env(prof),
        )
        for raw in proc.stdout:                       # stream so each read is visible live
            line = raw.strip()
            if not line:
                continue
            if line == "MODEL_READY":
                log("   model loaded ✓", "success")
            elif line == "VOICE_READY":
                log("   voice/clone prompt built once ✓", "success")
            elif line == "WARMUP_OK":
                log("   warm-up done ✓", "muted")
            elif line.startswith("DONE:"):
                try: idx = int(line.split(":")[1])
                except (ValueError, IndexError): idx = -1
                log(f"   ✓ read {idx + 1}/{len(texts)} generated", "muted")
            elif line.startswith(("ERROR:", "FATAL")):
                log(f"   ✗ {line}", "error")
            elif line.startswith("WARMUP_FAIL"):
                log("   warm-up skipped (non-fatal)", "muted")
        proc.wait(timeout=30)
    except Exception as exc:
        log(f"[{lang_code}] TTS subprocess failed: {exc}", "error")
        return False

    # Split each continuous read back into per-cue pieces by WORD ALIGNMENT, so
    # every cue lands at its OWN source time (sync) while the voice was generated
    # in one flowing read (consistency). The pieces keep the in-context prosody.
    import soundfile as sf
    from speech import wordsplit
    placements = []
    for i, b in enumerate(batches):
        if i in skip or not Path(outs[i]).exists():
            continue
        try:
            y, csr = sf.read(outs[i], dtype="float32", always_2d=False)
            if getattr(y, "ndim", 1) > 1:
                y = y.mean(axis=1)
        except Exception:
            continue
        cue_texts = [(c.get("translated") or "").strip() for c in b]
        pieces    = wordsplit.split_read(y, csr, cue_texts, lang_code, on_log=log)
        for k, (c, piece) in enumerate(zip(b, pieces)):
            pp = str(work / f"cue_{i:04d}_{k:02d}.wav")
            try:
                sf.write(pp, piece, csr)
                placements.append({"path": pp, "start": float(c["start"])})
            except Exception:
                pass

    if not placements:
        log(f"[{lang_code}] TTS produced no audio", "error")
        return False

    return aligner.assemble_track(placements, total_duration, out_path, on_log=log)
