# Switching the dub backend to dots.tts (Apple Silicon / MLX)

dots.tts is a zero-shot voice-cloning TTS — far more consistent voices than Qwen3,
24 languages, and (via the MLX port) fast + light on Apple Silicon. The app keeps
Qwen3 too; you switch with one config line.

## 1. One-time setup
```bash
bash scripts/setup_dots_tts.sh
```
This creates a `dots_tts` conda env, installs the [pure-MLX port](https://github.com/sb1992/dots-tts-mlx),
and downloads the **MeanFlow int4** weights (`mf-int4`, ~2.4 GB, the fast path —
comfortable on a 24 GB M-series). For higher quality at ~2× the time, download
`int4` (the 10-step `soar` decoder) instead and point `DOTS_WEIGHTS_DIR` at it.

## 2. Reference voices (one per language)
dots clones from a short clip, so each language needs a **few-second WAV** under
`input/Reference Voices/`, named `<name>_<lang>.wav` (e.g. `french_fr.wav`,
`english_en.wav`). You already ship clips there — make sure each target language
has one as WAV (MP3 may work but WAV is safest).

Better cloning fidelity: add the clip's transcript per language in
`config.DOTS_REFERENCE_TEXT`, e.g.
```python
DOTS_REFERENCE_TEXT = {"fr": "Bonjour, ceci est un échantillon de voix.", "en": "..."}
```
Leave a language out to use x-vector-only cloning (timbre from audio alone).

## 3. Flip the switch
In `scripts/config.py`:
```python
TTS_BACKEND = "dots"          # was "qwen3"
DOTS_PYTHON = "<env python>"   # printed by the setup script
DOTS_WEIGHTS_DIR = "~/dots-tts-mlx-weights/mf-int4"
```
Restart the backend. Dub now uses dots.tts; everything downstream (batches, sync,
fix-rushed) is unchanged because both backends speak the same worker protocol.

## Tuning (config.py)
| Setting | Default | Notes |
|---|---|---|
| `DOTS_NUM_STEPS` | 4 | `mf`=4, `soar`=10–32 |
| `DOTS_GUIDANCE_SCALE` | 1.2 | ignored by MeanFlow |
| `DOTS_SPEAKER_SCALE` | 1.5 | speaker-conditioning strength |
| `DOTS_SEED` | 42 | reproducible output |

## Notes / limits
- The dub engine loads the model **once per batch subprocess** and enrolls the
  reference **once**, so the voice is identical across all panels of a language.
- dots has a known accuracy gap on Arabic/Hindi/Turkish/Vietnamese (not in this
  app's language set). If a language ever struggles, switch `TTS_BACKEND` back to
  `"qwen3"` for that run.
- The `tts/dots_backend.py` worker matches the documented `dots_tts_mlx` API
  (`from_pretrained` → `model.enroll` → `model.generate`). If a future version of
  the port changes those names, adjust them in that one file.
