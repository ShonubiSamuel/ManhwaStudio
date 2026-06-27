"""
speech/ — ManhwaStudio v2 (speech-segment dubbing engine)
─────────────────────────────────────────────────────────────────────────────
The professional dubbing model, replacing panel-based timing:

  source audio → (optional vocal separation) → ASR word timestamps → sentence
  cues (with the source's own pauses) → CPS-fit translation → TTS per cue →
  time-align to the cue's slot → (optional) re-mix with background → mux.

Because each cue follows the SOURCE narration's timing, the result keeps the
original rhythm (natural breaths, no invented silence) and — for a recap video —
stays panel-synced for free, since the creator already timed panels to speech.

This package is built alongside the existing panel pipeline; nothing here
replaces it until the new flow is wired in and validated.
"""
