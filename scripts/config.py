"""
config.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Global constants only.  Every other module imports from here and nothing else.
No environment variables, no .env file, no runtime computation.

Edit this file once to match your system and every script picks up the change.

Sections
────────
  Application       — paths, name, version
  NVIDIA NIM API    — base URL and model IDs (key stored in DB, not here)
  Qwen3 TTS         — conda env path, model variants, speakers
  Panel Detection   — video segmentation defaults (tunable per-episode)
  PDF Processing    — slicer, upscaler, narration batch defaults
  Image Optimizer   — Claude upload compression defaults
  Dubbing           — whisper split, sync, and normalisation defaults
  Supported Languages — lang code → display name mapping
"""

from pathlib import Path


# ── Application ────────────────────────────────────────────────────────────────
APP_NAME    = "ManhwaStudio"
APP_VERSION = "2.0.0"

# All runtime data lives under BASE_DIR.
# Using Path(__file__) makes this portable — move the whole project folder
# anywhere on disk and every path resolves correctly without editing this file.
# scripts/config.py  →  parent = scripts/  →  parent.parent = project root
BASE_DIR   = Path(__file__).resolve().parent.parent
DB_PATH    = BASE_DIR / "studio.db"
OUTPUT_DIR = BASE_DIR / "output"
VOICES_DIR = BASE_DIR / "voices"
LOGS_DIR   = BASE_DIR / "logs"


# ── NVIDIA NIM API ─────────────────────────────────────────────────────────────
# The API key is stored in the database (settings table) — not here.
# Get a key at: https://build.nvidia.com → any model card → Get API Key
NVIDIA_BASE_URL     = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL        = "meta/llama-3.3-70b-instruct"          # text: clean / translate
NVIDIA_VISION_MODEL = "meta/llama-3.2-90b-vision-instruct"   # PDF narration (vision)


# ── TTS backend selection ──────────────────────────────────────────────────────
# Which text-to-speech engine the dub stage uses:
#   "qwen3" — Qwen3-TTS (conda env, preset speakers / voice design / voice clone)
#   "dots"  — dots.tts on Apple Silicon (pure-MLX port, zero-shot voice cloning;
#             far more consistent voices + 24 languages). Run scripts/setup_dots_tts.sh
#             first (creates the env + downloads weights), THEN flip this to "dots".
TTS_BACKEND = "qwen3"


# ── Qwen3 TTS ──────────────────────────────────────────────────────────────────
# Full absolute path to the Python binary inside the qwen3-tts conda env.
# This is the ONLY system-specific line — update it once to match your machine.
CONDA_PYTHON    = str(Path.home() / "miniconda3" / "envs" / "qwen3-tts" / "bin" / "python")
TTS_MODELS_BASE = Path.home() / "qwen3-tts-models"

TTS_MODEL_PATHS: dict = {
    "0.6B-Base":        TTS_MODELS_BASE / "Qwen3-TTS-12Hz-0.6B-Base",
    "0.6B-CustomVoice": TTS_MODELS_BASE / "Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "0.6B-VoiceDesign": TTS_MODELS_BASE / "Qwen3-TTS-12Hz-0.6B-VoiceDesign",
    "1.7B-Base":        TTS_MODELS_BASE / "Qwen3-TTS-12Hz-1.7B-Base",
    "1.7B-CustomVoice": TTS_MODELS_BASE / "Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "1.7B-VoiceDesign": TTS_MODELS_BASE / "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}

# Recommended model variant per TTS mode.
TTS_RECOMMENDED_MODELS: dict = {
    "VoiceDesign": "1.7B-VoiceDesign",
    "VoiceClone":  "1.7B-Base",
    "CustomVoice": "1.7B-CustomVoice",
}

# Unified voice profiles store their reference clip here (voices/refs/<name>.wav).
VOICE_REF_DIR = VOICES_DIR / "refs"
# Whisper model used to AUTO-TRANSCRIBE a reference clip once when a voice is
# created. It runs on one short clip per voice, so a big, accurate multilingual
# model is cheap here — the transcript is always editable afterwards (paste your
# own from Gemini etc.). Drop to "medium"/"small" if downloads/CPU are a concern.
VOICE_REF_WHISPER_MODEL = "large-v3"

# Built-in speaker identities available in CustomVoice mode.
TTS_PRESET_SPEAKERS: list = [
    "Aiden", "Ryan",
    "Vivian", "Serena",
    "Uncle_Fu", "Dylan",
    "Eric", "Ono_Anna", "Sohee",
]

# Language options shown in the voice profile editor.
TTS_LANGUAGES: list = [
    "Auto", "English", "Chinese", "Japanese", "Korean",
    "German", "French", "Russian", "Portuguese", "Spanish", "Italian",
]


# ── dots.tts (Apple Silicon / MLX) ─────────────────────────────────────────────
# Pure-MLX port: https://github.com/sb1992/dots-tts-mlx  (weights: shraey/dots-tts-mlx)
# Set up once with scripts/setup_dots_tts.sh, then set TTS_BACKEND = "dots".
DOTS_PYTHON      = str(Path.home() / "miniconda3" / "envs" / "dots_tts" / "bin" / "python")
# Folder holding the downloaded MLX weights.  Use "mf-int4" (MeanFlow, NFE-4) for
# the fastest on-device generation on Apple Silicon; "int4" is the higher-quality
# 10-step "soar" decoder.
DOTS_WEIGHTS_DIR = str(Path.home() / "dots-tts-mlx-weights" / "mf-int4")
# dots.tts clones a voice from a short reference clip per language.  These are the
# clips already shipped under input/Reference Voices (named like french_fr.wav).
DOTS_REFERENCE_DIR = str(BASE_DIR / "input" / "Reference Voices")
# Optional per-language transcript of the reference clip (better cloning).  Leave a
# language out for x-vector-only cloning (timbre from audio alone).
DOTS_REFERENCE_TEXT: dict = {}

# Generation settings (developer-recommended).
DOTS_NUM_STEPS      = 4      # MeanFlow (mf) = 4; soar = 10
DOTS_GUIDANCE_SCALE = 1.2    # ignored by the MeanFlow decoder
DOTS_SPEAKER_SCALE  = 1.5
DOTS_SEED           = 42


# ── Panel Detection  (video engine defaults, tunable per-episode) ───────────────
# ─ Signal detection ─
DETECT_MODE         = "combined"   # "combined" | "audio" | "visual"
DETECT_SILENCE_DB   = -35.0        # dBFS threshold — lower = stricter silence requirement
DETECT_MIN_SILENCE  = 0.1         # minimum silence duration to trigger a cut (sec)
DETECT_THRESHOLD    = 18          # visual score threshold — lower = more sensitive
DETECT_MIN_SCENE    = 0.5          # minimum gap between consecutive visual cuts (sec)
DETECT_FRAME_SKIP   = 2            # check every (N+1)th frame  (2 = every 3rd frame)
DETECT_MERGE_WINDOW = 0.3          # max time gap to merge an audio + visual signal (sec)
DETECT_PRIORITY     = "visual_first"   # "combined" | "visual_first" | "audio_first"
DETECT_WORKERS      = 4            # parallel ffmpeg workers for cut export

# ─ Transcript extraction ─
WHISPER_MODEL     = "small.en"     # faster-whisper model for English transcription
WHISPER_CHUNK_MIN = 30             # audio chunk size — prevents OOM on 10h+ files (min)

# ─ Screenshot extraction ─
SCREENSHOT_OFFSET = 0.5            # seconds into each panel to grab the frame
                                   # (skips opening transition frames)


# ── PDF Processing  (pdf engine defaults, tunable per-episode) ─────────────────
PDF_DPI             = 200          # rasterisation resolution (higher = sharper, slower)
PDF_SKIP_FIRST_LAST = True         # skip cover + back page during slicing
PDF_JPEG_QUALITY    = 95           # raw slice JPEG quality before the optimizer runs

# Real-ESRGAN upscaling slice settings
UPSCALE_MODE         = "slice"     # "slice" | "page" | "merge"
UPSCALE_SLICE_HEIGHT = 1800        # max height per slice in "slice" mode (px)

# Claude narration upload slice settings
NARR_MODE            = "page"      # "slice" | "page" | "merge"
NARR_SLICE_HEIGHT    = 1800        # max height per slice in "slice" mode (px)
NARR_MERGE_COUNT     = 3           # pages per merged image in "merge" mode
NARR_IMAGES_PER_BATCH = 3          # panel images sent to Claude per narration batch


# ── Image Optimizer  (Claude upload compression) ───────────────────────────────
OPTIMIZE_FOR_CLAUDE  = True
OPT_COMPRESSION_MODE = "quality"   # "quality" | "target_size" | "aggressive"

# "quality" mode — fixed JPEG quality, predictable file sizes
OPT_JPEG_QUALITY  = 65             # 85=high  75=balanced  65=lean  45=aggressive

# "target_size" mode — auto-adjusts quality to hit a per-image KB ceiling
OPT_TARGET_KB     = 150            # target KB per image
OPT_MIN_QUALITY   = 25             # quality floor (never compress below this)

# Shared resize + cleanup settings
OPT_MAX_WIDTH  = 800               # max pixel width after downscale (700=smaller 900=larger)
OPT_GRAYSCALE  = True              # grayscale saves 30–40% tokens; no narration quality loss
OPT_AUTOCROP   = True              # strip white border padding before resize
OPT_SHARPEN    = True              # UnsharpMask pass after resize to keep text crisp


# ── Dubbing ─────────────────────────────────────────────────────────────────────
DUB_WHISPER_MODEL      = "small"   # faster-whisper model for continuous audio splitting
DUB_SNAP_WINDOW_MS     = 600       # silence-snap search window per cut point (ms)
DUB_CONTINUOUS_TIMEOUT = 900       # TTS subprocess hard timeout in seconds (15 min)
DUB_NORMALIZE_RMS      = 3000      # target RMS level for per-panel audio normalisation

# ── Sync / timing fit ───────────────────────────────────────────────────────────
# Each dubbed panel is fitted to a time budget = the English narration audio length.
# REQUIREMENT: the final per-language track must be the SAME total length as English
# (the assembled video needs every language frame-aligned), so each panel's synced
# audio is always made to match the English duration.  Fit order: good translation
# first, then silence padding (for short clips), then time-stretch (for long clips).
#   • DUB_MAX_STRETCH  — "comfortable" ratio.  Past this a panel is flagged as
#     rushed (its translation is too long) so it can be re-translated shorter, but
#     it is STILL compressed to the English length (length match is non-negotiable).
#   • DUB_HARD_STRETCH — absolute safety cap so a pathological translation can't ask
#     pyrubberband for an insane rate; reachable only when a panel is wildly long.
DUB_MAX_STRETCH        = 1.20      # comfort threshold — flag panels needing more
DUB_HARD_STRETCH       = 4.0       # absolute safety cap on the compress ratio
DUB_MILD_STRETCH       = 1.15      # "imperceptible" band — stretch freely up to here
EN_CHARS_PER_SEC       = 14.0      # rough English speaking rate, for budget estimates

# Translation length budget — a per-line character TARGET (not a hard cap), as a
# fraction of the English line length.  MEANING ALWAYS COMES FIRST: the translator
# keeps the full story and only tightens wording / drops filler to get close.  A
# faithful translation that ends up longer is fine — Sync compresses the residual
# (and flags anything that has to rush a lot).  CJK scripts pack more sound per
# character, so their character target is lower.
TRANSLATE_LEN_BUDGET     = 0.95    # non-CJK: a bit shorter than English (NOT half — that guts meaning)
TRANSLATE_LEN_BUDGET_CJK = 0.55    # CJK: ~55% of the English character count
TRANSLATE_LEN_ENFORCE    = True    # run the iterative shorten-loop after translating
# Iterative "back and forth" length fit: any line longer than its budget is sent
# back to the model to be shortened, up to TRANSLATE_FIT_ITERS rounds.  A candidate
# is only accepted if it stays above TRANSLATE_FIT_FLOOR × the English length — this
# is the anti-gutting guard: we shorten by tighter phrasing, never by collapsing a
# sentence into a meaningless fragment.  Lines that can't fit without gutting are
# left longer and Sync compresses them (a small, flagged rush) instead.
TRANSLATE_FIT_ITERS      = 3       # max shorten rounds per batch
TRANSLATE_FIT_FLOOR      = 0.45    # reject a "shortened" line below this × English length

# After Sync, automatically fix panels that came out "rushed" (dub longer than
# the English reference beyond DUB_MAX_STRETCH): re-translate them shorter,
# re-dub, and re-sync — best of DUB_FIX_ATTEMPTS — with no manual clicking.
DUB_AUTO_FIX_RUSHED      = True
DUB_FIX_ATTEMPTS         = 3


# ── Supported Languages ─────────────────────────────────────────────────────────
# Maps ISO 639-1 code → display name used across the entire application.
# English (en) is always the master language — do not remove it.
# Add or remove other entries to change which languages appear in the UI.
SUPPORTED_LANGUAGES: dict = {
    # ── Covered by Qwen3-TTS (used automatically — higher quality) ──
    "en": "English",
    "zh": "Chinese",
    "es": "Spanish",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "it": "Italian",
    # ── Only on dots.tts (used automatically for these) ──
    "ar":  "Arabic",
    "yue": "Cantonese",
    "cs":  "Czech",
    "nl":  "Dutch",
    "fi":  "Finnish",
    "el":  "Greek",
    "hi":  "Hindi",
    "id":  "Indonesian",
    "pl":  "Polish",
    "ro":  "Romanian",
    "th":  "Thai",
    "tr":  "Turkish",
    "uk":  "Ukrainian",
    "vi":  "Vietnamese",
}

# Which TTS engine handles which language. A voice profile only picks a
# *language*; the engine is derived from it — Qwen3 where it has the language
# (better quality), dots.tts for the rest (its wider 24-language roster is the
# only reason it's here). No manual engine switch anywhere.
QWEN_LANGUAGES: set = {"en", "zh", "es", "ja", "ko", "fr", "de", "pt", "ru", "it"}
DOTS_LANGUAGES: set = QWEN_LANGUAGES | {
    "ar", "yue", "cs", "nl", "fi", "el", "hi", "id", "pl", "ro", "th", "tr", "uk", "vi",
}


def engine_for_language(code: str) -> str:
    """Return the TTS engine ('qwen3' | 'dots') that should synthesize `code`."""
    code = (code or "").lower()
    if code in QWEN_LANGUAGES:
        return "qwen3"
    if code in DOTS_LANGUAGES:
        return "dots"
    return "qwen3"   # safe default for anything unexpected


# ── Speech-segment dubbing  (CPS = characters per second) ──────────────────────
# The professional approach: segment the SOURCE narration into sentence-sized
# cues (with the source's own pauses), then fit each language to the cue's own
# time using CPS — instead of forcing audio to fill a fixed visual panel.
#
# Comfortable spoken narration ≈ 150 wpm ≈ 15 CPS (Netflix reads at ≤17). CJK is
# information-dense, so ≈9-10 CPS. These drive translation length + the "rushed"
# flag per cue. CJK = Chinese / Japanese / Korean / Cantonese.
CPS_COMFORTABLE     = 16.0   # the BEST-FIT target — translations aim for this so the
                             # dub fills its slot WITHOUT speed-up and WITHOUT silence
CPS_MAX             = 20.0   # above this a cue is "rushed" and gets shortened
# Time-compression is what sounds robotic when overdone. Cap it HARD for speech:
# a clip is sped up at most this much; beyond that we'd rather it ride slightly
# into the pause than break the voice. Good translations keep this near 1.0×.
# Max time-COMPRESSION (speed-up) of a cue, applied only when it would run into
# the next cue. Kept modest so speed-ups aren't obvious. Cues are NEVER slowed
# down — dragging a short line to fill silence sounds artificial; we leave the
# pause instead (natural speed + real pauses, like professional dubs).
DUB_SPEECH_MAX_STRETCH = 1.3
# Clean gap (seconds) kept between one cue's end and the next cue's start, so a
# line can never bleed onto the next (overlap = two voices = a "broken" sound).
DUB_SPEECH_MIN_GAP = 0.06

# ── Breathing room (reduce long silences WITHOUT stretching the speech) ───────
# A line shorter than its slot leaves dead air. Instead of slowing the voice
# (which sounds dragged) we LENGTHEN the natural pauses BETWEEN its phrases so it
# breathes across the slot — the spoken words are never touched. Each pause is
# capped so it never sounds like the narrator froze; whatever can't be filled
# naturally stays as a (capped) tail pause.
DUB_BREATHE_ENABLE     = True
DUB_BREATHE_FILL_RATIO = 0.85   # spread the line to fill up to this much of its slot
DUB_BREATHE_MAX_PAUSE  = 0.4    # never let a single internal pause exceed this (s)

# ── Voice mastering (final pass) ──────────────────────────────────────────────
# Raw TTS is dark/boomy/hot; this brightens, evens, and level-normalises the
# assembled dub so it sounds like a pro track. See speech/master.py.
# Per-cue "running start": prefix every cue with a short throwaway word in its
# OWN generation so the first real words come out stable (no hum/wobble/breath),
# then strip the prefix back off by pause detection. Prevents the artifact at the
# source on every line — more reliable than detecting each artifact after the
# fact. WARMUP_WORD ends in a period so the model pauses (a clean cut point); tune
# it (e.g. "Voilà.", "Alors.") if its tone ever bleeds through.
DUB_WARMUP_PER_CUE = True
DUB_WARMUP_WORD    = "Bon."

DUB_MASTER_ENABLE       = True
DUB_MASTER_LUFS         = -16.0    # dialogue loudness target (online/streaming)
DUB_MASTER_TP           = -1.5     # true-peak ceiling (dBTP) — keeps headroom
DUB_MASTER_SR           = 48000    # output sample rate (removes 24 kHz ceiling)
DUB_MASTER_HIGHPASS_HZ  = 100      # remove sub-bass rumble / boom
DUB_MASTER_BASS_CUT_DB  = -4.0     # tame the boomy low end (low shelf @200 Hz)
DUB_MASTER_MUD_CUT_DB   = -2.0     # cut boxy low-mids around 300 Hz
DUB_MASTER_PRESENCE_DB  = 5.0      # boost ~3 kHz for consonant clarity
DUB_MASTER_CLARITY_DB   = 2.5      # boost ~5 kHz for definition
DUB_MASTER_AIR_DB       = 3.0      # gentle high shelf for air
CPS_COMFORTABLE_CJK = 9.0
CPS_MAX_CJK         = 13.0

# Cue segmentation from word-level timestamps.
CUE_WHISPER_MODEL = "small"   # Whisper model for cue segmentation — "small" is
                              # fast and accurate enough for sentence boundaries;
                              # bump to "medium"/"large-v3" only if you need it.
CUE_MAX_SEC = 6.0    # hard-split a cue that would run longer than this
CUE_GAP_SEC = 0.5    # a pause ≥ this between words starts a new cue (a breath)
CUE_MIN_SEC = 1.0    # don't end a cue on punctuation before it reaches this
# Merge fragment cues (a comma-pause split mid-sentence) back into a whole
# sentence — fewer tiny clips means less choppiness and less dead air.
CUE_MERGE_SHORT_SEC = 1.4   # a cue shorter than this is a candidate to merge up
CUE_MERGE_GAP_SEC   = 0.7   # …if its gap to the neighbour is under this
CUE_MERGE_MAX_SEC   = 8.0   # never let a merged cue grow past this

# Dubbing reads = how cues are grouped into ONE continuous Qwen3 read for a
# consistent, flowing voice. Each read is then split back into per-cue pieces by
# WORD-LEVEL alignment (Whisper) — reliable, unlike a silence split.
#   DUB_CUE_BATCH > 1 → group up to this many cues per read (and ≤ DUB_READ_MAX_SEC),
#                       so the voice flows across them. RECOMMENDED for consistency.
#   DUB_CUE_BATCH = 1 → per-cue (each cue alone). Perfect sync, no split, but the
#                       voice "resets" each cue. Use if word-split ever mis-cuts.
DUB_CUE_BATCH   = 8    # max cues per continuous read
DUB_READ_MAX_SEC = 30  # …and cap a read's length (token + alignment safety)
DUB_FADE_IN_MS  = 35   # soft onset on each segment (avoids a sharp pickup)
DUB_FADE_OUT_MS = 80   # soft tail into the pause


# ── Vocal separation + re-mix  (background music keep/remove) ──────────────────
# When the source video has music/SFX, separate the narration from the
# background (Demucs), dub only the voice, then re-mix the dub OVER the original
# background. Toggle per the user's preference; Demucs must be pip-installed in
# DEMUCS_PYTHON's environment. ffmpeg handles the actual mix (resample + gain).
import sys as _sys
DEMUCS_PYTHON         = _sys.executable   # python with `demucs` installed (configurable)
DEMUCS_MODEL          = "htdemucs"        # Demucs separation model
KEEP_BACKGROUND_MUSIC = True              # re-mix dub over the source music (default)
DUB_VOICE_GAIN        = 1.0               # narration level in the mix
DUB_MUSIC_GAIN        = 0.8               # duck the music slightly under the narration