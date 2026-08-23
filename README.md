# ManhwaStudio

An automated localisation pipeline for long-form comics and video: transcribe, translate, re-voice, and re-time — with the translated audio fitted back into the original timing rather than simply appended.

**Python · FastAPI · Whisper · vision-language detection · voice-cloning TTS · React front end**

---

## The problem that makes dubbing hard

Translation is the easy half. The hard half is **duration**. A line that takes 2.1 seconds in Korean may take 3.4 seconds in French, and if you just synthesise the translation you get audio that drifts out of sync and overruns the next cue. Fix it by speeding up playback and it sounds like a chipmunk; fix it by truncating and you lose meaning.

This pipeline treats timing as a first-class constraint. `speech/cps.py` works in **characters per second** — the translation stage is given a duration budget per cue, so the text it produces is *already* the right length to speak in the space available. Alignment then confirms it, rather than hoping.

## Pipeline

```
source audio/video
   │
   ├─ speech/separate.py      split vocals from music & effects (the bed survives untouched)
   ├─ speech/segmenter.py     cue boundaries
   ├─ speech/splitter.py      ─┐
   ├─ speech/wordsplit.py     ─┴ sub-cue granularity for tight alignment
   │
   ├─ dub/aligner.py          Whisper ASR — loaded once and shared across all target
   │                          languages in the sync stage, not reloaded per language
   ├─ speech/cps.py           characters-per-second budget per cue
   ├─ speech/translate_cues.py ─┐
   ├─ ai/translator.py         ─┴ translation under the duration constraint
   │
   ├─ tts/script_builder.py   assemble the speakable script
   ├─ tts/voice_profile.py    per-speaker voice identity
   ├─ tts/synth.py            voice-cloning synthesis
   ├─ tts/worker.py           ─┐
   ├─ dub/batch_manager.py    ─┴ batched/queued inference
   │
   ├─ speech/remix.py         re-lay voices over the preserved music bed
   ├─ speech/mux.py           mux back to the video
   └─ speech/master.py        loudness/mastering pass
```

`speech/separate.py` matters more than it looks: separating vocals from the music and effects bed means the original score survives the dub intact, instead of being replaced along with the dialogue.

## Recap: visual evidence for narration

A second pipeline generates spoken recaps of prior chapters — and this is where it stops being a dubbing tool.

- **`ai/magi.py`** runs [Magi v3](https://huggingface.co/ragavsachdeva/magiv3) locally to detect panels, characters and speech attribution from comic pages. Narration is grounded in what's actually *on the page*, not in a summary of the text alone.
- **`ai/story_memory.py`** is an evidence-backed, selectively retrieved memory: it decides what prior context is relevant to the current chapter instead of feeding everything forward. It includes a deliberately conservative heuristic so a role phrase ("the swordsman") never gets promoted into a fake character name — the failure mode that makes generated recaps sound confidently wrong.
- **`ai/recap_narrator.py`** / **`ai/narrator.py`** turn the retrieved evidence into narration, then hand it to the same TTS stack.

## Layout

| Path | What |
|---|---|
| `scripts/speech/` | Audio: separation, segmentation, alignment, CPS budgeting, remix, mux, master |
| `scripts/dub/` | Whisper alignment and batch orchestration |
| `scripts/ai/` | Translation, Magi visual evidence, story memory, narration, model capability routing |
| `scripts/tts/` | Voice profiles, script building, synthesis backends, worker queue |
| `scripts/api/` | FastAPI service — routers for pipeline, dubbing, translate, media, voices, settings, sync, logs |
| `scripts/core/` | Subprocess runner, audio and file utilities, engine base class |
| `ui/` | React + Vite front end (includes `pdfjs-dist` for page ingest) |

~122 first-party source files. `tts/voice_profile.py` uses an explicit attribute whitelist in `from_dict()` so a malformed or hostile profile can't set arbitrary attributes — worth noting because it's the kind of thing usually skipped in a personal project.

## Running it

Requires Python 3.11+, ffmpeg, and model weights fetched separately (see below).

```bash
pip install -r requirements.txt
uvicorn scripts.api.main:app --reload     # API
cd ui && npm install && npm run dev       # front end
```

Model weights — Whisper, Magi v3, and the TTS backend — are **not committed**; `models/` is gitignored because the Magi checkpoint alone is 1.6 GB. `ai/magi.py` expects it at `models/magiv3`.

## State of the project

Working, and used to produce real output — but a personal tool, not a product. Known gaps, stated plainly:

- **Evaluation is fit-only so far.** `scripts/eval/` measures whether dubs *fit* their
  time slots (see below); BLEU/chrF/WER need reference translations and transcripts,
  which don't exist yet. Semantic quality is still assessed by listening.
- **No test suite outside `scripts/eval/tests/`.**
- Source media, generated audio and rendered video are gitignored; only code is tracked.
- Language coverage depends on the configured TTS backend.

## Evaluation

`scripts/eval/` scores dub quality. The fit metrics are **reference-free** — they need
nothing but the pipeline's own output, so they run on every session already on disk and
can gate a regression in CI:

```bash
python -m scripts.eval                                  # writes docs/eval/report.{md,json}
python -m scripts.eval --fail-on-overrun 0.05           # CI gate
python -m scripts.eval --references refs.json           # adds BLEU/chrF/WER
python -m pytest scripts/eval/tests -q                  # 26 unit tests
```

Current results over 147 cues in 7 sessions ([full report](docs/eval/report.md)):

| Lang | Cues | Coverage | Median CPS | Comfortable | Rushed | Overrunning |
|---|---:|---:|---:|---:|---:|---:|
| en | 84 | 100.0% | 18.0 | 71.4% | 7.1% | 7.1% |
| fr | 63 | 100.0% | 18.26 | 76.2% | 15.9% | 15.9% |

The distinction that matters: **rushed** is a quality problem (audibly hurried),
**overrunning** is a correctness problem (the line cannot fit even at maximum rate, so
it collides with the next cue). Two findings from the first run:

- **French overruns at twice the English rate** (15.9% vs 7.1%) — French expands more
  against the source, and the shortening loop isn't fully absorbing it.
- **The `cps` value stored on each cue is stale.** Recomputing from the text that
  actually shipped disagrees by >1 char/sec on 85 of 147 cues (mean 2.9, worst session
  7.1). Anything reading `cue["cps"]` downstream is reading a pre-shortening value.

`--references` accepts a JSON file keyed by session id with `translations` and
`transcript` arrays in cue order; `sacrebleu` and `jiwer` are optional and imported
lazily, so the harness always runs without them.

## Legal

This is tooling. It ships **no comic pages, no audio and no video** — all source material and generated output are excluded from version control. Localising copyrighted work requires the rights-holder's permission; that's on the operator, not the tool.

## License

Not yet licensed — all rights reserved.
