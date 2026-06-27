"""
tts/dots_backend.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
dots.tts backend (Apple Silicon / MLX) for the dub stage.

This mirrors tts/script_builder.build_chapter_script: it produces a self-contained
Python script (run in the dots_tts MLX env via config.DOTS_PYTHON) that loads the
model ONCE, enrolls a speaker profile from a short reference clip (so the voice is
identical across every line), and generates each sentence to a WAV.

It prints the SAME stdout protocol the Qwen3 builder uses, so dub_engine's existing
subprocess parsing works unchanged:

    LOADING_MODEL · MODEL_READY · VOICE_READY · WARMUP_OK ·
    SKIP:i · DONE:i · ERROR:i:msg · ALL_DONE · FATAL:msg

dots.tts is a zero-shot voice-cloning model — there are no preset speakers or
"voice design". Every voice comes from a reference clip + (optionally) its
transcript. References live in config.DOTS_REFERENCE_DIR (e.g. french_fr.wav).

API reference (sb1992/dots-tts-mlx):
    from dots_tts_mlx.loader import from_pretrained
    model = from_pretrained(weights_dir, dtype=mx.bfloat16).model
    profile = model.enroll(ref_audio, ref_text, speaker_scale=1.5)
    out = model.generate(text, profile=profile, language="FR",
                         num_steps=4, guidance_scale=1.2, seed=42)
    # out["audio"] (mlx array), out["sample_rate"]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Set, Tuple

import config
import runtime_settings as rs
from tts.voice_profile import VoiceProfile

_AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg")

# dots.tts wants an uppercase ISO 639-1 code. The dub engine sets
# profile.language to a DISPLAY name (e.g. "French"), so map it back.
_NAME_TO_CODE = {name.lower(): code for code, name in config.SUPPORTED_LANGUAGES.items()}


def _lang_code(profile: VoiceProfile) -> str:
    """Resolve the ISO 639-1 code for a profile (default English)."""
    name = (getattr(profile, "language", "") or "").strip().lower()
    return _NAME_TO_CODE.get(name, name if len(name) == 2 else "en")


def dots_env() -> dict:
    """os.environ copy with UTF-8 forced (matches the Qwen subprocess env)."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def resolve_reference(profile: VoiceProfile, lang_code: str) -> Tuple[Optional[str], str]:
    """
    Find the reference clip + transcript for a language.

    Priority:
      1. profile.ref_wav_path (an explicit VoiceClone reference) + profile.ref_wav_text
      2. a file under DOTS_REFERENCE_DIR whose name ends with "_<lang>" (e.g.
         french_fr.wav for 'fr'), with the transcript from DOTS_REFERENCE_TEXT.

    Returns (audio_path | None, transcript).  A None audio path means "no
    reference for this language" — the worker will report it as FATAL.
    """
    ref_path = (getattr(profile, "ref_wav_path", "") or "").strip()
    if ref_path and Path(ref_path).exists():
        return ref_path, (getattr(profile, "ref_wav_text", "") or "").strip()

    ref_dir = Path(config.DOTS_REFERENCE_DIR)
    if ref_dir.is_dir():
        cands = [
            p for p in sorted(ref_dir.iterdir())
            if p.suffix.lower() in _AUDIO_EXTS
            and p.stem.lower().endswith(f"_{lang_code.lower()}")
        ]
        # Prefer a .wav over compressed formats when several match.
        cands.sort(key=lambda p: 0 if p.suffix.lower() == ".wav" else 1)
        if cands:
            text = (config.DOTS_REFERENCE_TEXT or {}).get(lang_code, "")
            return str(cands[0]), text

    return None, ""


def build_dots_script(
    profile:      VoiceProfile,
    sentences:    list,
    output_paths: list,
    skip_indices: Set[int],
) -> str:
    """Build the dots.tts MLX worker script (see module docstring)."""
    lang_code = _lang_code(profile)
    ref_audio, ref_text = resolve_reference(profile, lang_code)

    weights = rs.get_str("dots_weights_dir", str(config.DOTS_WEIGHTS_DIR))
    # num_steps MUST match the checkpoint: MeanFlow (mf-*) wants 4 (and ignores
    # guidance); the soar flow-matching checkpoint wants ~10. Running soar at 4
    # produces under-denoised gibberish — so derive the default from the weights
    # folder name, and only override it if the user set num_steps in Settings.
    is_meanflow  = "mf" in Path(weights).name.lower()
    default_steps = 4 if is_meanflow else 10

    cfg = {
        "weights":      weights,
        "ref_audio":    ref_audio or "",
        "ref_text":     ref_text or "",
        "language":     (lang_code or "en").upper(),
        "num_steps":    rs.get_int("dots_num_steps", default_steps),
        "guidance":     rs.get_float("dots_guidance_scale", getattr(config, "DOTS_GUIDANCE_SCALE", 1.2)),
        "speaker":      rs.get_float("dots_speaker_scale", getattr(config, "DOTS_SPEAKER_SCALE", 1.5)),
        "seed":         rs.get_int("dots_seed", getattr(config, "DOTS_SEED", 42)),
        "sentences":    list(sentences),
        "outputs":      list(output_paths),
        "skip":         sorted(skip_indices or set()),
    }
    cfg_json = json.dumps(cfg, ensure_ascii=True)

    # The script reads a single JSON blob (avoids any string-escaping pitfalls
    # with non-ASCII narration) and emits the shared stdout protocol.
    return f'''
import sys, json

_CFG = json.loads({cfg_json!r})

_missing = []
try:
    import mlx.core as mx
except ImportError:
    _missing.append("mlx")
try:
    import soundfile  # noqa
except ImportError:
    _missing.append("soundfile")
try:
    import numpy  # noqa
except ImportError:
    _missing.append("numpy")
try:
    from dots_tts_mlx.loader import from_pretrained as _fp; del _fp
except ImportError:
    _missing.append("dots_tts_mlx")
if _missing:
    print("FATAL:Missing packages in the dots_tts env: " + ", ".join(_missing), flush=True, file=sys.stderr)
    sys.exit(1)
del _missing

import numpy as np
import soundfile as sf
import mlx.core as mx
from dots_tts_mlx.loader import from_pretrained

if not _CFG["ref_audio"]:
    print("FATAL:No reference voice clip found for language '" + _CFG["language"] + "'. "
          "Add one to the Reference Voices folder named like <name>_" + _CFG["language"].lower() + ".wav",
          flush=True, file=sys.stderr)
    sys.exit(1)

try:
    print("LOADING_MODEL", flush=True)
    model = from_pretrained(_CFG["weights"], dtype=mx.bfloat16).model
    print("MODEL_READY", flush=True)

    # Enroll ONCE so the cloned voice is identical for every line.
    profile = model.enroll(_CFG["ref_audio"], _CFG["ref_text"], speaker_scale=_CFG["speaker"])
    print("VOICE_READY", flush=True)

    # Diagnostics (stderr): if output is gibberish or slow, these tell us why —
    # variant/steps mismatch, a too-long reference, or per-line generation time.
    import time as _t
    try:
        _rinfo = sf.info(_CFG["ref_audio"]); _rdur = _rinfo.frames / float(_rinfo.samplerate or 1)
    except Exception:
        _rdur = -1.0
    print("dots: weights=%s steps=%s lang=%s ref_dur=%.1fs (aim ~10s) text=%r"
          % (_CFG["weights"], _CFG["num_steps"], _CFG["language"], _rdur, (_CFG["ref_text"] or "")[:60]),
          flush=True, file=sys.stderr)

    def _synth(text):
        return model.generate(
            text,
            profile=profile,
            language=_CFG["language"],
            num_steps=_CFG["num_steps"],
            guidance_scale=_CFG["guidance"],
            seed=_CFG["seed"],
        )

    try:
        _t0 = _t.time()
        _synth("Hello.")
        print("dots: warmup %.1fs" % (_t.time() - _t0), flush=True, file=sys.stderr)
        print("WARMUP_OK", flush=True)
    except Exception as _we:
        print("WARMUP_FAIL:" + str(_we), flush=True, file=sys.stderr)

    skip = set(_CFG["skip"])
    for i, (sentence, out_path) in enumerate(zip(_CFG["sentences"], _CFG["outputs"])):
        if i in skip:
            print("SKIP:" + str(i), flush=True)
            continue
        if not str(sentence).strip():
            print("SKIP:" + str(i), flush=True)
            continue
        try:
            _t1 = _t.time()
            res = _synth(sentence)
            wav = np.array(mx.array(res["audio"]).astype(mx.float32)).squeeze()
            sf.write(out_path, wav, int(res["sample_rate"]))
            print("dots: line %d gen %.1fs -> %.1fs audio" % (i, _t.time() - _t1, len(wav) / float(res["sample_rate"] or 1)), flush=True, file=sys.stderr)
            print("DONE:" + str(i), flush=True)
        except Exception as e:
            print("ERROR:" + str(i) + ":" + str(e), flush=True, file=sys.stderr)

    print("ALL_DONE", flush=True)

except Exception as e:
    print("FATAL:" + str(e), flush=True, file=sys.stderr)
    sys.exit(1)
'''
