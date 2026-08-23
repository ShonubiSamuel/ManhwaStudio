"""
tts/script_builder.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
TTS subprocess script construction utilities.

Extracted from tts_engine.py.  Previously dub_engine.py imported these
private functions from tts_engine at runtime inside method bodies:

    from tts_engine import _build_chapter_script, CONDA_PYTHON, _subprocess_env

Those hidden local imports are now gone.  Both tts_engine and dub_engine
import from this module at the top of their file like normal code.

Public API
──────────
    CONDA_PYTHON            path to the Qwen3-TTS conda Python binary
    MODEL_PATHS             dict: model name → absolute path
    AVAILABLE_MODELS        list of model names
    PRESET_SPEAKERS         list of built-in speaker names
    LANGUAGES               list of language display names
    RECOMMENDED_MODELS      dict: mode → recommended model name

    split_script(text, max_chars)   → list[str]
    build_chapter_script(...)       → str
"""

from __future__ import annotations

import os
import re
from typing import Set

import config
from tts.voice_profile import VoiceProfile


# ── Module constants (all sourced from config.py) ─────────────────────────────

CONDA_PYTHON       = config.CONDA_PYTHON
MODEL_PATHS        = config.TTS_MODEL_PATHS
AVAILABLE_MODELS   = list(config.TTS_MODEL_PATHS.keys())
PRESET_SPEAKERS    = config.TTS_PRESET_SPEAKERS
LANGUAGES          = config.TTS_LANGUAGES
RECOMMENDED_MODELS = config.TTS_RECOMMENDED_MODELS


# ── CJK warm-up texts ─────────────────────────────────────────────────────────
# One short native-language sentence per CJK language.  The generated script
# runs this immediately after model/voice load to absorb the MPS/bf16
# cold-start penalty before real sentence 0.

_WARMUP_TEXTS: dict = {
    "Chinese":  "你好，今天天气不错。",
    "Japanese": "こんにちは、今日はいい天気ですね。",
    "Korean":   "안녕하세요, 오늘 날씨가 좋네요.",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    """Escape a string for safe embedding inside a Python string literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def subprocess_env() -> dict:
    """Return os.environ copy with UTF-8 forced for every child process."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


# ── Script splitter ───────────────────────────────────────────────────────────

def split_script(script: str, max_chars: int = 250) -> list:
    """
    Split a script string into TTS-sized chunks.

    CJK terminators (。！？) and pause punctuation (，、) are recognised
    alongside Latin punctuation.  Uses \\s* (not \\s+) so CJK text — which
    has no space after 。 — splits correctly without losing the next character.
    """
    script = re.sub(r"\n---\n", "\n", script)
    raw    = re.split(r"(?<=[.!?。！？])\s*", script.strip())
    raw    = [s.strip() for s in raw if s.strip()]
    chunks = []
    current = ""

    for sentence in raw:
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            if len(sentence) > max_chars:
                parts = re.split(r"(?<=[,，、])\s*", sentence)
                for part in parts:
                    if part.strip():
                        chunks.append(part.strip())
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


# ── Chapter script builder ────────────────────────────────────────────────────

def build_chapter_script(
    profile:      VoiceProfile,
    sentences:    list,
    output_paths: list,
    skip_indices: Set[int],
    _worker:      bool = False,
) -> str:
    """
    Build a self-contained Python script string that:
      1. Validates all required packages before touching the model
      2. Loads the Qwen3 TTS model once
      3. Extracts voice fingerprint once (VoiceClone only)
      4. Runs a warm-up pass to absorb MPS/bf16 cold-start
      5. Loops all sentences, generates each, saves each WAV
      6. Prints DONE:i after each successful sentence
      7. Prints SKIP:i for already-done sentences
      8. Prints ERROR:i:msg on failure

    The script is designed to run in one long-lived subprocess so that
    voice identity is guaranteed consistent across every sentence.
    """
    # The model MUST match the mode: VoiceClone needs a clone-capable "-Base"
    # model, VoiceDesign a "-VoiceDesign" model, CustomVoice a "-CustomVoice" one.
    # A mismatch (e.g. a VoiceClone voice on a CustomVoice model) is a FATAL
    # "does not support create_voice_clone_prompt". So if profile.model is missing
    # or incompatible, use the RECOMMENDED model for the mode — never a blanket
    # CustomVoice fallback.
    def _mode_ok(key):
        if not key or key not in MODEL_PATHS:
            return False
        if profile.mode == "VoiceClone":
            return key.endswith("-Base")
        if profile.mode == "VoiceDesign":
            return key.endswith("-VoiceDesign")
        return key.endswith("-CustomVoice")

    rec_key = RECOMMENDED_MODELS.get(profile.mode, "1.7B-CustomVoice")
    model_key = profile.model if _mode_ok(profile.model) else rec_key
    model_path = str(MODEL_PATHS.get(model_key, MODEL_PATHS.get(rec_key, MODEL_PATHS["1.7B-CustomVoice"])))

    sentences_repr = "[\n" + ",\n".join(
        f'    "{_esc(s)}"' for s in sentences
    ) + "\n]"

    paths_repr = "[\n" + ",\n".join(
        f'    "{_esc(p)}"' for p in output_paths
    ) + "\n]"

    skip_repr = repr(sorted(skip_indices))

    gen_kwargs = (
        f"do_sample=True,\n"
        f"        temperature={profile.temperature},\n"
        f"        top_p={profile.top_p},\n"
        f"        top_k={profile.top_k},\n"
        f"        repetition_penalty={profile.repetition_penalty},\n"
        f"        max_new_tokens={profile.max_new_tokens},\n"
        f"        subtalker_dosample=True,\n"
        f"        subtalker_top_k={profile.top_k},\n"
        f"        subtalker_top_p=1.0,\n"
        f"        subtalker_temperature={profile.temperature},"
    )

    if profile.mode == "VoiceClone":
        ref_audio = _esc(profile.ref_wav_path)
        ref_text  = _esc(profile.ref_wav_text)
        # Prefer in-context-learning cloning (ref_audio + ref_text) — it is far
        # more consistent across lines than x-vector-only (the developer's
        # recommended "design once → clone" workflow). Fall back to x-vector-only
        # only when there is no transcript.
        use_xvec = profile.x_vector_only or not (profile.ref_wav_text or "").strip()
        if use_xvec:
            build_prompt = f'''
    voice_clone_prompt = model.create_voice_clone_prompt(
        ref_audio="{ref_audio}",
        x_vector_only_mode=True,
    )'''
        else:
            build_prompt = f'''
    voice_clone_prompt = model.create_voice_clone_prompt(
        ref_audio="{ref_audio}",
        ref_text="{ref_text}",
        x_vector_only_mode=False,
    )'''
        generate_call = f'''wavs, sr = model.generate_voice_clone(
            text=sentence,
            language="{_esc(profile.language)}",
            voice_clone_prompt=voice_clone_prompt,
            {gen_kwargs}
        )'''

    elif profile.mode == "VoiceDesign":
        build_prompt  = ""
        instruct      = _esc(profile.instruct)
        generate_call = f'''wavs, sr = model.generate_voice_design(
            text=sentence,
            instruct="{instruct}",
            language="{_esc(profile.language)}",
            {gen_kwargs}
        )'''

    else:  # CustomVoice
        build_prompt  = ""
        instruct_line = (
            f'instruct="{_esc(profile.instruct)}",'
            if profile.instruct.strip() else ""
        )
        generate_call = f'''wavs, sr = model.generate_custom_voice(
            text=sentence,
            speaker="{_esc(profile.speaker)}",
            language="{_esc(profile.language)}",
            {instruct_line}
            {gen_kwargs}
        )'''

    warm_escaped = _esc(
        _WARMUP_TEXTS.get(profile.language, "Hello, this is a warm-up sentence.")
    )

    # ── Resident worker variant ───────────────────────────────────────────────
    # Loads the model + voice ONCE, then serves generation requests off stdin
    # (one JSON line per request → one WAV), so the auto-fix loop reuses the
    # loaded model instead of paying a fresh model load per panel/attempt.
    # Same model/voice/seed/generate code as the one-shot above — only the input
    # loop differs. Emits a tiny protocol: VOICE_READY, then DONE / ERR per line.
    if _worker:
        return f'''
import sys, json

_missing = []
try:
    import torch
except ImportError:
    _missing.append("torch")
try:
    import soundfile
except ImportError:
    _missing.append("soundfile")
try:
    from qwen_tts import Qwen3TTSModel as _qtts_check; del _qtts_check
except ImportError:
    _missing.append("qwen_tts")
if _missing:
    print(f"FATAL:Missing required packages: {{', '.join(_missing)}}", flush=True, file=sys.stderr)
    sys.exit(1)
del _missing

import soundfile as sf

device = "mps" if torch.backends.mps.is_available() else "cpu"
dtype  = torch.bfloat16 if device == "mps" else torch.float32

SEED = {int(getattr(profile, "seed", -1))}
def _seed():
    if SEED < 0:
        return
    try:
        torch.manual_seed(SEED)
        if torch.backends.mps.is_available():
            torch.mps.manual_seed(SEED)
    except Exception:
        pass

try:
    from qwen_tts import Qwen3TTSModel

    print("LOADING_MODEL", flush=True)
    model = Qwen3TTSModel.from_pretrained(
        "{_esc(model_path)}",
        device_map=device,
        dtype=dtype,
        attn_implementation="sdpa",
    )
    print("MODEL_READY", flush=True)
    {build_prompt}
    print("VOICE_READY", flush=True)

    for _line in sys.stdin:
        _line = _line.strip()
        if not _line:
            continue
        try:
            _req = json.loads(_line)
        except Exception:
            print("ERR:bad-request", flush=True, file=sys.stderr)
            continue
        if _req.get("cmd") == "quit":
            break
        sentence = _req.get("text", "")
        _out     = _req.get("out", "")
        try:
            _seed()
            {generate_call}
            sf.write(_out, wavs[0], sr)
            print("DONE", flush=True)
        except Exception as e:
            print(f"ERR:{{e}}", flush=True, file=sys.stderr)
    print("BYE", flush=True)

except Exception as e:
    print(f"FATAL:{{e}}", flush=True, file=sys.stderr)
    sys.exit(1)
'''

    # ── Generated script template ─────────────────────────────────────────────
    # Double braces ({{...}}) in this f-string become single braces ({...}) in
    # the generated script — they are Python f-string expressions evaluated at
    # subprocess runtime, not at build time.
    return f'''
import sys

# ── Dependency pre-check ──────────────────────────────────────────────────────
# Validate all required packages BEFORE starting the slow model load.
# A missing or broken package detected here saves 30–60 s of wasted GPU RAM
# allocation and model initialisation.
#
# torch and soundfile are collected first so we can report all missing packages
# in a single FATAL message rather than failing on each one individually.
# qwen_tts is checked via a throw-away import that is then deleted; the actual
# import for use is inside the try block below.
_missing = []
try:
    import torch
except ImportError:
    _missing.append("torch")
try:
    import soundfile
except ImportError:
    _missing.append("soundfile")
try:
    from qwen_tts import Qwen3TTSModel as _qtts_check; del _qtts_check
except ImportError:
    _missing.append("qwen_tts")
if _missing:
    print(f"FATAL:Missing required packages: {{', '.join(_missing)}}", flush=True, file=sys.stderr)
    sys.exit(1)
del _missing
# ─────────────────────────────────────────────────────────────────────────────

import soundfile as sf

device = "mps" if torch.backends.mps.is_available() else "cpu"
dtype  = torch.bfloat16 if device == "mps" else torch.float32

# Optional seed. SEED < 0 → leave RNG free (natural prosody variation, the
# recommended default). A fixed seed only locks prosody — consistency of the
# *voice* comes from reusing the clone prompt off one reference, not the RNG.
SEED = {int(getattr(profile, "seed", -1))}
def _seed():
    if SEED < 0:
        return
    try:
        torch.manual_seed(SEED)
        if torch.backends.mps.is_available():
            torch.mps.manual_seed(SEED)
    except Exception:
        pass

sentences    = {sentences_repr}
out_paths    = {paths_repr}
skip_indices = set({skip_repr})

try:
    from qwen_tts import Qwen3TTSModel

    print("LOADING_MODEL", flush=True)
    model = Qwen3TTSModel.from_pretrained(
        "{_esc(model_path)}",
        device_map=device,
        dtype=dtype,
        attn_implementation="sdpa",
    )
    print("MODEL_READY", flush=True)
    {build_prompt}
    print("VOICE_READY", flush=True)

    sentence = "{warm_escaped}"
    try:
        _seed()
        {generate_call}
        print("WARMUP_OK", flush=True)
    except Exception as _we:
        print(f"WARMUP_FAIL:{{_we}}", flush=True, file=sys.stderr)

    for i, (sentence, out_path) in enumerate(zip(sentences, out_paths)):
        if i in skip_indices:
            print(f"SKIP:{{i}}", flush=True)
            continue
        try:
            _seed()
            {generate_call}
            sf.write(out_path, wavs[0], sr)
            print(f"DONE:{{i}}", flush=True)
        except Exception as e:
            print(f"ERROR:{{i}}:{{e}}", flush=True, file=sys.stderr)

    print("ALL_DONE", flush=True)

except Exception as e:
    print(f"FATAL:{{e}}", flush=True, file=sys.stderr)
    sys.exit(1)
'''


def build_worker_script(profile: VoiceProfile) -> str:
    """Resident-model variant of build_chapter_script (loads once, serves stdin)."""
    return build_chapter_script(profile, [], [], set(), _worker=True)