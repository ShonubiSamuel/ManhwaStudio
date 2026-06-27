#!/usr/bin/env python
"""
speech_dub.py — run the NEW speech-segment dubbing engine from the command line.

This is the test-drive for the new pipeline before the editor UI (Phase 7) is
built. It dubs ANY video by following the source narration's own timing (cues +
CPS), with an optional background-music keep/remove.

Usage
─────
  venv/bin/python scripts/speech_dub.py "input/1hour.mp4" --langs fr
  venv/bin/python scripts/speech_dub.py video.mp4 --langs fr es --no-music
  venv/bin/python scripts/speech_dub.py video.mp4 --langs fr --source-lang en --out /tmp/dubout

Prereqs on your machine:
  • a voice profile whose language matches each target (create in Dubbing → Voices)
  • ffmpeg + faster-whisper + your TTS env (already used by the app)
  • optional: `pip install demucs` for "keep background music"
  • NVIDIA key set in the app (read from the DB) for translation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # scripts/ on path

import config
from database import Database
from speech import pipeline as sp


_COLORS = {"error": "\033[91m", "success": "\033[92m", "warning": "\033[93m", "accent": "\033[96m"}
_RESET  = "\033[0m"


def _log(msg, level="info"):
    print(f"{_COLORS.get(level, '')}{msg}{_RESET if level in _COLORS else ''}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Speech-segment dubbing (any video).")
    ap.add_argument("video", help="path to the source video")
    ap.add_argument("--langs", nargs="+", required=True, help="target language codes, e.g. fr es")
    ap.add_argument("--source-lang", default="en", help="language spoken in the source (default: en)")
    ap.add_argument("--no-music", action="store_true", help="don't keep background music (clean narration)")
    ap.add_argument("--out", default=None, help="output/work dir (default: alongside the video)")
    args = ap.parse_args()

    video = Path(args.video).expanduser()
    if not video.exists():
        _log(f"Video not found: {video}", "error")
        return 1

    db = Database(str(config.DB_PATH))
    provider = db.get_setting("ai_provider_translate", "nvidia")
    api_key  = db.get_setting("nvidia_api_key", "")
    lm_model = db.get_setting("lm_studio_model", "")
    try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
    except (TypeError, ValueError): ctx = 32768

    if provider == "nvidia" and not api_key:
        _log("No NVIDIA API key found in the app settings — translation will fail. "
             "Set it in the app (Settings → AI & Providers) first.", "warning")

    work = args.out or str(video.parent / f"{video.stem}_speechdub")
    _log(f"Dubbing {video.name} → {', '.join(args.langs)} "
         f"(music: {'off' if args.no_music else 'keep'})", "accent")

    results = sp.run_speech_dub(
        str(video), list(args.langs),
        source_lang     = args.source_lang,
        work_dir        = work,
        keep_music      = (not args.no_music),
        provider        = provider, api_key = api_key,
        lm_studio_model = lm_model, context_length = ctx,
        on_log          = _log,
        on_progress     = lambda d, t: _log(f"  ── {d}/{t} languages done", "accent"),
    )

    print()
    any_ok = False
    for lc, r in results.items():
        if r.ok:
            any_ok = True
            _log(f"{lc}: ✓  {r.video}", "success")
            _log(f"      cues: {r.cues}", "info")
        else:
            _log(f"{lc}: ✗  {r.error}", "error")
    return 0 if any_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
