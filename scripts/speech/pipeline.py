"""
speech/pipeline.py — end-to-end speech-segment dubbing for ANY video.

One call runs the whole flow and, for each target language, writes a dubbed
video plus a cues.json artifact (text, timing, translation, CPS) that the
editor UI reads:

    video → extract audio → [separate vocals/music] → ASR cues (source pauses)
          → per language: CPS-fit translate → dub + align → [re-mix music] → mux

    run_speech_dub(video, ["fr","es"]) -> { "fr": DubResult, "es": DubResult }

Voice per language is resolved by `voice_for(lang)` (defaults to the first voice
profile whose language matches). Heavy steps (ASR / TTS / Demucs) run on the
user's machine; this module is the orchestration + artifacts.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

import config
from speech import segmenter, translate_cues as _tc, dub_cues as _dub, separate, remix, mux


@dataclass
class DubResult:
    lang:  str
    ok:    bool = False
    video: str = ""
    audio: str = ""
    cues:  str = ""
    error: str = ""


# ── ffmpeg helpers ──────────────────────────────────────────────────────────────

def extract_audio(video_path: str, out_wav: str, on_log: Callable) -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-vn",
             "-c:a", "pcm_s16le", "-ar", "48000", str(out_wav)],
            capture_output=True, text=True, timeout=1800,
        )
        return r.returncode == 0 and Path(out_wav).exists()
    except Exception as exc:
        on_log(f"audio extract failed: {exc}", "error")
        return False


def media_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float((r.stdout or "0").strip())
    except Exception:
        return 0.0


def _default_voice_for(lang_code: str):
    from tts.voice_profile import VoiceProfileManager
    vpm  = VoiceProfileManager(str(config.VOICES_DIR))
    want = config.SUPPORTED_LANGUAGES.get(lang_code, "").lower()
    for name in vpm.list_profiles():
        p = vpm.load(name)
        if p and (getattr(p, "language", "") or "").lower() == want:
            return p
    return None


# ── Orchestrator ────────────────────────────────────────────────────────────────

def run_speech_dub(
    video_path:   str,
    target_langs: List[str],
    source_lang:  str = "en",
    voice_for:    Optional[Callable] = None,
    work_dir:     Optional[str] = None,
    keep_music:   Optional[bool] = None,
    provider:     str = "nvidia",
    api_key:      str = "",
    lm_studio_model: str = "",
    context_length:  int = 32768,
    tone_text:    str = "",
    on_log:       Optional[Callable] = None,
    on_progress:  Optional[Callable] = None,
    should_stop:  Optional[Callable] = None,
) -> Dict[str, DubResult]:
    import time
    import runtime_settings as rs
    _raw_log = on_log or (lambda *a, **k: None)
    _t0 = time.time()
    def log(m, lvl="info"):                  # prefix every line with elapsed time
        _raw_log(f"[{time.time() - _t0:6.1f}s] {m}", lvl)
    voice_for = voice_for or _default_voice_for
    stop     = should_stop or (lambda: False)
    if keep_music is None:
        keep_music = rs.get_bool("keep_background_music", getattr(config, "KEEP_BACKGROUND_MUSIC", True))

    work = Path(work_dir or Path(config.OUTPUT_DIR) / "speech_dub")
    work.mkdir(parents=True, exist_ok=True)
    results: Dict[str, DubResult] = {}

    # 1) Extract source audio + total duration (the alignment canvas).
    src_wav  = work / "source.wav"
    log("Extracting source audio …", "accent")
    if not extract_audio(video_path, str(src_wav), log):
        for lc in target_langs:
            results[lc] = DubResult(lc, error="audio extraction failed")
        return results
    total = media_duration(str(video_path)) or segmenter_total(str(src_wav))
    log(f"Source duration: {total:.1f}s", "info")

    # 2) Optional vocal/music separation.
    background = None
    asr_audio  = str(src_wav)
    if keep_music:
        sep_dir = work / "separated"
        background = separate.separate_background(str(src_wav), str(sep_dir), on_log=log)
        if background:
            voc = Path(background).with_name("vocals.wav")
            if voc.exists():
                asr_audio = str(voc)     # transcribe the clean narration

    if stop(): return results

    # 3) ASR → source cues (once; reused for every language).
    log("Transcribing into speech cues …", "accent")
    cues = segmenter.transcribe_to_cues(asr_audio, source_lang, on_log=log)
    if not cues:
        for lc in target_langs:
            results[lc] = DubResult(lc, error="no speech cues found")
        return results
    (work / "cues_source.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"{len(cues)} source cue(s)", "success")

    # 4) Per language: translate → dub+align → remix → mux.
    for n, lc in enumerate(target_langs):
        if stop():
            break
        res = DubResult(lc)
        try:
            voice = voice_for(lc)
            if not voice:
                res.error = f"no voice profile for {lc}"
                log(f"[{lc}] {res.error} — skipping", "warning")
                results[lc] = res
                continue

            ldir = work / lc
            ldir.mkdir(parents=True, exist_ok=True)

            log(f"[{lc}] translating {len(cues)} cue(s) to fit time …", "accent")
            _t = time.time()
            tcues = _tc.translate_cues(
                cues, lc, tone_text=tone_text, provider=provider, api_key=api_key,
                lm_studio_model=lm_studio_model, context_length=context_length, on_log=log,
            )
            log(f"[{lc}] translation done in {time.time() - _t:.1f}s", "success")
            cues_json = ldir / "cues.json"
            cues_json.write_text(json.dumps(tcues, ensure_ascii=False, indent=2), encoding="utf-8")
            res.cues = str(cues_json)

            _t = time.time()
            dub_wav = ldir / "dub.wav"
            if not _dub.dub_cues(tcues, lc, voice, total, str(dub_wav), on_log=log):
                res.error = "dubbing failed"; results[lc] = res; continue
            log(f"[{lc}] dubbing + alignment done in {time.time() - _t:.1f}s", "success")

            _t = time.time()
            final_wav = ldir / "final.wav"
            remix.remix(str(dub_wav), background, str(final_wav), on_log=log)
            res.audio = str(final_wav)

            out_mp4 = ldir / f"dubbed_{lc}.mp4"
            if mux.mux(str(video_path), str(final_wav), str(out_mp4), on_log=log):
                res.video = str(out_mp4); res.ok = True
            else:
                res.error = "mux failed"
            log(f"[{lc}] remix + mux done in {time.time() - _t:.1f}s · "
                f"language total {time.time() - _t0:.1f}s", "success")
        except Exception as exc:
            res.error = str(exc)
            log(f"[{lc}] failed: {exc}", "error")
        results[lc] = res
        if on_progress:
            on_progress(n + 1, len(target_langs))

    return results


def segmenter_total(wav_path: str) -> float:
    """Fallback total duration from a WAV when ffprobe on the video fails."""
    try:
        import wave
        with wave.open(str(wav_path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def result_to_dict(results: Dict[str, DubResult]) -> dict:
    return {lc: asdict(r) for lc, r in results.items()}
