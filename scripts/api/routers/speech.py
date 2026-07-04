"""
scripts/api/routers/speech.py — ManhwaStudio v2

Speech-segment dubbing endpoints (the new "dub any video" engine):

  POST /api/speech/dub/{episode_id}     start a speech-segment dub (background)
  GET  /api/speech/cues/{episode_id}/{lang}   cues.json for the editor timeline

The run streams logs/progress through the same SSE channel as the pipeline
(emit_log / emit_progress keyed by episode_id), then a terminal stage_done.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_db
from api.events import emit_log, emit_progress, emit_stage_done, clear_queue
from api.models import AdhocTranslateRequest, AdhocTranslateJob, AdhocSyncRequest, AdhocSyncJob
from database import Database
import config
import uuid

router = APIRouter(tags=["Speech dub"])

_threads: dict = {}
_lock = threading.Lock()

_adhoc_jobs: dict = {}
_adhoc_jobs_lock = threading.Lock()


class SpeechDubRequest(BaseModel):
    target_langs: List[str]
    keep_music:   Optional[bool] = None


def _resolve_voice(db: Database, episode_id: int, lang: str):
    """Per-episode dub_profiles assignment, else first voice matching the language."""
    from tts.voice_profile import VoiceProfileManager
    vpm = VoiceProfileManager(str(config.VOICES_DIR))
    try:
        profiles = db.get_setting_json(f"dub_profiles_{episode_id}", {}) or {}
    except Exception:
        profiles = {}
    name = profiles.get(lang) if isinstance(profiles, dict) else None
    if name and vpm.exists(name):
        return vpm.load(name)
    want = config.SUPPORTED_LANGUAGES.get(lang, "").lower()
    for n in vpm.list_profiles():
        p = vpm.load(n)
        if p and (getattr(p, "language", "") or "").lower() == want:
            return p
    return None


def _run(episode_id: int, video: str, langs: List[str], keep_music, db: Database) -> None:
    from speech import pipeline as sp
    log = lambda m, lvl="info": emit_log(episode_id, m, lvl)
    ok = False
    try:
        provider = db.get_setting("ai_provider_translate", "nvidia")
        api_key  = db.get_setting("nvidia_api_key", "")
        lm_model = db.get_setting("lm_studio_model", "")
        try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
        except (TypeError, ValueError): ctx = 32768
        ep   = db.get_episode(episode_id) or {}
        work = Path(ep.get("output_folder") or (Path(config.OUTPUT_DIR) / str(episode_id))) / "speech_dub"

        log("▶  Speech-segment dubbing started …", "accent")
        results = sp.run_speech_dub(
            video, langs,
            voice_for      = lambda lc: _resolve_voice(db, episode_id, lc),
            work_dir       = str(work),
            keep_music     = keep_music,
            provider       = provider, api_key = api_key,
            lm_studio_model= lm_model, context_length = ctx,
            tone_text      = ep.get("tone_prompt") or "",
            on_log         = log,
            on_progress    = lambda done, tot: emit_progress(episode_id, round(done / tot * 100) if tot else 0,
                                                             f"{done}/{tot} languages"),
        )
        good = [lc for lc, r in results.items() if r.ok]
        bad  = [f"{lc} ({r.error})" for lc, r in results.items() if not r.ok]
        if good:
            log(f"✓  Dubbed: {', '.join(good)}", "success")
        if bad:
            log(f"✗  Failed: {', '.join(bad)}", "error")
        ok = bool(good)
    except Exception as exc:
        emit_log(episode_id, f"✗  Speech dub crashed: {exc}", "error")
        ok = False
    finally:
        emit_stage_done(episode_id, "speech_dub", ok)
        with _lock:
            _threads.pop(episode_id, None)


@router.post("/speech/dub/{episode_id}", status_code=202)
def start_speech_dub(episode_id: int, body: SpeechDubRequest, db: Database = Depends(get_db)):
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")
    video = ep.get("source_path") or ""
    if not video or not Path(video).exists():
        raise HTTPException(400, "Episode has no source video on disk")
    if not body.target_langs:
        raise HTTPException(400, "No target languages selected")

    with _lock:
        t = _threads.get(episode_id)
        if t and t.is_alive():
            raise HTTPException(409, "A speech dub is already running for this episode")
        clear_queue(episode_id)
        th = threading.Thread(
            target=_run, args=(episode_id, video, body.target_langs, body.keep_music, db),
            daemon=True, name=f"speechdub-ep{episode_id}",
        )
        _threads[episode_id] = th
        th.start()
    return {"ok": True, "message": f"Speech dub started for {len(body.target_langs)} language(s)"}


def _files_url(abs_path: Path) -> str:
    try:
        rel = Path(abs_path).resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return "/files/" + rel.as_posix()
    except (ValueError, OSError):
        return ""


@router.get("/speech/cues/{episode_id}/{lang}")
def get_cues(episode_id: int, lang: str, db: Database = Depends(get_db)):
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")
    path = Path(ep.get("output_folder") or "") / "speech_dub" / lang / "cues.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _persist_adhoc_log(job_id: str, stage: str, db: Database, duration_secs: float, is_sync_job: bool = False, project_name: str = "Dub Studio"):
    job = _adhoc_sync_jobs.get(job_id) if is_sync_job else _adhoc_jobs.get(job_id)
    if not job:
        return
    try:
        db.log_adhoc_activity(
            stage=stage,
            status=job.get("status", "failed"),
            duration_secs=duration_secs,
            log_lines=job.get("log", []),
            error=job.get("error", ""),
            project_name=project_name
        )
    except Exception:
        pass


@router.get("/speech/result/{episode_id}/{lang}")
def get_result(episode_id: int, lang: str, db: Database = Depends(get_db)):
    """Everything the editor needs for a language: cues + playable URLs."""
    ep = db.get_episode(episode_id)
    if not ep:
        raise HTTPException(404, f"Episode {episode_id} not found")
    base  = Path(ep.get("output_folder") or "") / "speech_dub" / lang
    cpath = base / "cues.json"
    cues  = []
    if cpath.exists():
        try:
            cues = json.loads(cpath.read_text(encoding="utf-8"))
        except Exception:
            cues = []
    video = base / f"dubbed_{lang}.mp4"
    audio = base / "final.wav"
    return {
        "cues":      cues,
        "video_url": _files_url(video) if video.exists() else "",
        "audio_url": _files_url(audio) if audio.exists() else "",
        "exists":    cpath.exists(),
    }


def _run_adhoc_translate(job_id: str, source_path: str, lang: str):
    from speech import segmenter, translate_cues
    from database import Database
    import config
    import time
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    
    log_id = db.log_adhoc_start("translate_cues")
    
    def _set(status=None, message=None, cues=None, error=None):
        with _adhoc_jobs_lock:
            job = _adhoc_jobs.get(job_id)
            if not job: return
            if status: job["status"] = status
            if cues is not None: job["cues"] = cues
            if error: job["error"] = error
            
            if message:
                job["log"].append({"timestamp": time.time(), "level": "info" if not error else "error", "message": message})
            
            db.log_adhoc_update(log_id, job["status"], job["log"], job.get("error", ""))

    def _log(msg: str, level: str = "info"):
        with _adhoc_jobs_lock:
            job = _adhoc_jobs.get(job_id)
            if job:
                job["log"].append({
                    "timestamp": time.time(),
                    "level": level,
                    "message": msg
                })
                db.log_adhoc_update(log_id, job["status"], job["log"], job.get("error", ""))

    try:
        if not Path(source_path).exists():
            _set(status="failed", error="Source audio file not found on disk.")
            return

        _set(message="Transcribing English audio into cues...")
        _log("▶ Starting extraction...", "accent")
        cues = segmenter.transcribe_to_cues(source_path, "en", on_log=_log)
        if not cues:
            _set(status="failed", error="Failed to extract any cues from the audio.")
            return

        # Transcribe-only mode (Video Refine): skip translation entirely so the
        # editor gets the raw English wording, untouched, with an empty 2nd slot.
        if not lang or lang.strip().lower() in ("", "none", "transcribe", "original"):
            for c in cues:
                c["translated"] = ""
            _set(status="done", message="Transcription complete", cues=cues)
            return

        _set(message=f"Translating {len(cues)} cues to {lang}...")
        provider = db.get_setting("ai_provider_translate", "nvidia")
        api_key  = db.get_setting("nvidia_api_key", "")
        lm_model = db.get_setting("lm_studio_model", "")
        try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
        except (TypeError, ValueError): ctx = 32768

        translated = translate_cues.translate_cues(
            cues=cues,
            lang_code=lang,
            provider=provider,
            api_key=api_key,
            lm_studio_model=lm_model,
            context_length=ctx,
            fix_attempts=3,
            on_log=_log,
        )

        if cues and all(not c.get("translated", "").strip() for c in translated) and any(c.get("text", "").strip() for c in cues):
            _set(status="failed", error="Translation failed (check API keys and provider logs).")
            return

        _set(status="done", message="Extraction and translation complete", cues=translated)

    except Exception as exc:
        _set(status="failed", error=str(exc))
    finally:
        db.close()


@router.post("/speech/adhoc-translate", response_model=AdhocTranslateJob, status_code=202)
def adhoc_translate(body: AdhocTranslateRequest, db: Database = Depends(get_db)):
    if not body.source_path.strip():
        raise HTTPException(400, "source_path is required")
        
    job_id = uuid.uuid4().hex[:12]
    with _adhoc_jobs_lock:
        _adhoc_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "message": "Queued ...",
            "cues": [],
            "log": [],
            "error": ""
        }
        
    threading.Thread(
        target=_run_adhoc_translate,
        args=(job_id, body.source_path, body.target_lang),
        daemon=True,
        name=f"adhoctranslate-{job_id}"
    ).start()
    
    return AdhocTranslateJob(**_adhoc_jobs[job_id])


@router.get("/speech/adhoc-translate/{job_id}", response_model=AdhocTranslateJob)
def get_adhoc_translate_status(job_id: str):
    with _adhoc_jobs_lock:
        job = _adhoc_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return AdhocTranslateJob(**job)


# ── Translate EXISTING cues to a language (Video Refine, no re-transcribe) ────

class TranslateCuesRequest(BaseModel):
    cues:      List[dict]
    lang_code: str


def _run_translate_cues(job_id: str, cues: list, lang: str):
    from database import Database
    import config
    from speech import translate_cues
    import time
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    log_id = db.log_adhoc_start("translate_cues")

    def _set(**kw):
        with _adhoc_jobs_lock:
            job = _adhoc_jobs.get(job_id)
            if job: job.update({k: v for k, v in kw.items() if v is not None})
            if job: db.log_adhoc_update(log_id, job["status"], job["log"], job.get("error", ""))

    def _log(msg, level="info"):
        with _adhoc_jobs_lock:
            job = _adhoc_jobs.get(job_id)
            if not job: return
            job["log"].append({"timestamp": time.time(), "level": level, "message": msg})
            db.log_adhoc_update(log_id, job["status"], job["log"], job.get("error", ""))

    try:
        _set(message=f"Translating {len(cues)} cues to {lang}...")
        provider = db.get_setting("ai_provider_translate", "nvidia")
        api_key  = db.get_setting("nvidia_api_key", "")
        lm_model = db.get_setting("lm_studio_model", "")
        try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
        except (TypeError, ValueError): ctx = 32768
        translated = translate_cues.translate_cues(
            cues=cues, lang_code=lang, provider=provider, api_key=api_key,
            lm_studio_model=lm_model, context_length=ctx, fix_attempts=3, on_log=_log,
        )
        if cues and all(not c.get("translated", "").strip() for c in translated) and any(c.get("text", "").strip() for c in cues):
            _set(status="failed", error="Translation failed (check API keys and provider logs).")
            return
        _set(status="done", message="Translation complete", cues=translated)
    except Exception as exc:
        _set(status="failed", error=str(exc))
    finally:
        db.close()


@router.post("/speech/translate-cues", response_model=AdhocTranslateJob, status_code=202)
def translate_cues_endpoint(body: TranslateCuesRequest, db: Database = Depends(get_db)):
    if not body.cues:
        raise HTTPException(400, "cues is required")
    job_id = uuid.uuid4().hex[:12]
    with _adhoc_jobs_lock:
        _adhoc_jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued ...", "cues": [], "log": [], "error": ""}
    threading.Thread(target=_run_translate_cues, args=(job_id, body.cues, body.lang_code),
                     daemon=True, name=f"translatecues-{job_id}").start()
    return AdhocTranslateJob(**_adhoc_jobs[job_id])


# ── Adhoc Sync ─────────────────────────────────────────────────────────────

_adhoc_sync_jobs = {}
_adhoc_sync_jobs_lock = threading.Lock()

def _run_adhoc_sync(job_id: str, req: AdhocSyncRequest):
    import time
    import urllib.parse
    import tempfile
    import soundfile as sf
    from pathlib import Path
    from speech import wordsplit, aligner
    import config
    from database import Database
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    log_id = db.log_adhoc_start("adhoc_sync")

    def _set(status=None, message=None, synced_audio_url=None, error=None):
        with _adhoc_sync_jobs_lock:
            job = _adhoc_sync_jobs.get(job_id)
            if not job: return
            if status is not None: job["status"] = status
            if message is not None: job["message"] = message
            if synced_audio_url is not None: job["synced_audio_url"] = synced_audio_url
            if error is not None: job["error"] = error
            db.log_adhoc_update(log_id, job["status"], job["log"], job.get("error", ""))

    def _log(msg: str, level: str = "info"):
        with _adhoc_sync_jobs_lock:
            job = _adhoc_sync_jobs.get(job_id)
            if job:
                job["log"].append({
                    "timestamp": time.time(),
                    "level": level,
                    "message": msg
                })
                db.log_adhoc_update(log_id, job["status"], job["log"], job.get("error", ""))

    try:
        if req.audio_url.startswith("/files/"):
            rel_path = urllib.parse.unquote(req.audio_url[7:])
            audio_path = str(Path(config.OUTPUT_DIR) / rel_path)
        else:
            audio_path = req.audio_url
            
        if not Path(audio_path).exists():
            _set(status="failed", error=f"Audio file not found: {audio_path}")
            return
            
        _log("▶ Starting sync process...", "accent")
        _set(message="Reading TTS audio...")
        y, sr = sf.read(audio_path, dtype="float32", always_2d=False)
        if getattr(y, "ndim", 1) > 1:
            y = y.mean(axis=1)
            
        cue_texts = [(c.get("translated") or "").strip() for c in req.cues]
        # The read may begin with a throwaway warm-up sentence (absorbs the TTS
        # first-utterance hiccup). Strip it by detecting the first pause after it —
        # ASR-free, so it's robust to how the warm-up gets transcribed.
        if (req.lead_dummy or "").strip():
            _set(message="Removing warm-up lead...")
            y, _stripped = wordsplit.strip_lead_segment(y, sr, on_log=_log)

        _set(message="Splitting continuous read into sentences...")
        pieces = wordsplit.split_read(y, sr, cue_texts, req.lang_code, on_log=_log)

        # Write into the project's own folder when we know which project this is
        # (so dub output lives with the project), else fall back to a scratch dir.
        if req.project_id is not None:
            work = Path(config.OUTPUT_DIR) / str(req.project_id) / "dub" / req.lang_code
        else:
            work = Path(config.OUTPUT_DIR) / "adhoc_sync" / job_id
        work.mkdir(parents=True, exist_ok=True)
        placements = []
        for k, (c, piece) in enumerate(zip(req.cues, pieces)):
            pp = str(work / f"cue_{k:04d}.wav")
            try:
                sf.write(pp, piece, sr)
                placements.append({"path": pp, "start": float(c["start"])})
            except Exception as e:
                _log(f"Failed to write piece {k}: {e}", "warning")
                
        if not placements:
            _set(status="failed", error="No audio pieces extracted.")
            return
            
        total_duration = 0.0
        if req.cues:
            total_duration = max([float(c.get("end", 0)) for c in req.cues]) + 2.0
            
        _set(message="Aligning and crossfading audio...")
        raw_path   = str(work / "synced_raw.wav")
        final_path = str(work / "synced_final.wav")
        success = aligner.assemble_track(placements, total_duration, raw_path, on_log=_log)

        if success:
            # Final voice-mastering pass (EQ + compression + loudness-normalise +
            # 48 kHz). Falls back to the raw track if mastering is off/unavailable.
            _set(message="Mastering audio (EQ + loudness)...")
            from speech.master import master
            if not master(raw_path, final_path, on_log=_log):
                final_path = raw_path
            out_url = _files_url(Path(final_path))
            _set(status="done", message="Sync complete!", synced_audio_url=out_url)
            _log("▶ Sync complete! Audio is ready.", "success")
        else:
            _set(status="failed", error="Alignment failed.")
    except Exception as exc:
        _set(status="failed", error=str(exc))
        _log(f"Fatal error: {exc}", "error")
    finally:
        db.close()
@router.post("/speech/adhoc-sync", response_model=AdhocSyncJob, status_code=202)
def adhoc_sync(req: AdhocSyncRequest, db: Database = Depends(get_db)):
    import uuid
    import threading
    job_id = str(uuid.uuid4())
    
    with _adhoc_sync_jobs_lock:
        _adhoc_sync_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "message": "Queued ...",
            "synced_audio_url": "",
            "log": [],
            "error": ""
        }
        
    threading.Thread(target=_run_adhoc_sync, args=(job_id, req), daemon=True).start()
    return AdhocSyncJob(**_adhoc_sync_jobs[job_id])

@router.get("/speech/adhoc-sync/{job_id}", response_model=AdhocSyncJob)
def adhoc_sync_status(job_id: str):
    with _adhoc_sync_jobs_lock:
        job = _adhoc_sync_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return AdhocSyncJob(**job)


# ── Per-cue dubbing (TTS each line separately → place → master) ───────────────
# Replaces the fragile "one continuous read + split" path. Each cue is its own
# clean clip, so there are NO mid-word cuts and NO leaked warm-up. Generated in a
# SINGLE model load (consistent timbre) with a discarded warm-up first (stabilises
# the first real line) and per-clip loudness levelling (even cue-to-cue volume —
# the inconsistency that short clips used to cause).

class DubCuesRequest(BaseModel):
    cues:       List[dict]
    voice:      str
    lang_code:  str = "French"
    project_id: Optional[int] = None
    warmup:     Optional[str] = "Bonjour à tous."   # discarded first generation
    repack_timings: bool = False


def _balanced_comma_split(text: str):
    """Split a line into two halves at the comma nearest its middle (so a short
    line can breathe a real pause at a genuine boundary). Returns [text] if there
    is no usable comma."""
    import re
    commas = [m.start() for m in re.finditer(",", text)]
    if not commas:
        return [text]
    mid = len(text) / 2
    c = min(commas, key=lambda i: abs(i - mid))
    a, b = text[:c].strip(), text[c + 1:].strip()
    return [a, b] if a and b else [text]


def _level_clip(path: str, target_rms: float = 0.08, max_gain: float = 4.0):
    """Bring a clip toward a common loudness so cues don't jump in volume."""
    import soundfile as sf, numpy as np
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
    sf.write(path, y.astype("float32"), sr, subtype="PCM_16")


def _run_dub_cues(job_id: str, req: DubCuesRequest):
    from tts import synth
    from tts.voice_profile import VoiceProfileManager
    from speech import aligner, cps
    from speech.master import master
    import soundfile as sf
    import time
    from database import Database
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    log_id = db.log_adhoc_start("dub_cues")

    def _set(**kw):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id].update(kw)
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))
    def _log(msg, level="info"):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id]["log"].append({"timestamp": __import__("time").time(), "level": level, "message": msg})
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    try:
        vpm = VoiceProfileManager(str(config.VOICES_DIR))

        cues = req.cues
        if not cues:
            _set(status="failed", error="No cues to dub"); return

        if req.project_id is not None:
            work = Path(config.OUTPUT_DIR) / str(req.project_id) / "dub" / req.lang_code
        else:
            work = Path(config.OUTPUT_DIR) / "adhoc_sync" / job_id
        work.mkdir(parents=True, exist_ok=True)

        # Build the flat segment list. A cue that is clearly SHORT for its slot
        # and has a comma is split in two so it can breathe a pause at that real
        # boundary (instead of one chunk + long trailing silence).
        comf = cps.comfortable_cps(req.lang_code)
        warm = (req.warmup or "").strip()
        # Per-cue warm-up: give EVERY cue a short throwaway prefix (default "Bon.")
        # in its own generation, then strip it. This gives every line a "running
        # start" so its first words synthesise stable (no hum/wobble/breath) — the
        # general fix, not just for cue 1. Tune DUB_WARMUP_WORD to taste.
        per_cue   = bool(getattr(config, "DUB_WARMUP_PER_CUE", False))
        warm_word = (getattr(config, "DUB_WARMUP_WORD", "Bon.") or "").strip()
        segments_by_voice = {}
        for i, c in enumerate(cues):
            voice_name = c.get("voice") or req.voice
            text = (c.get("translated") or "").strip()
            start = float(c.get("start", 0)); end = float(c.get("end", 0))
            nxt = float(cues[i + 1]["start"]) if i + 1 < len(cues) else end
            slot = max(0.05, nxt - start)
            est = (len(text) / comf) if comf else 0.0       # expected speech seconds
            # Say the whole line as ONE clip so its comma pause is the AI's own
            # natural pause (like Maestra), NOT a gap our code inserts between two
            # split halves — that inserted gap was the long, unnatural silence.
            # Slot-filling is handled by breathing (lengthening the real pause).
            parts = [text]
            for s, part in enumerate(parts):
                seg = {"cue": i, "sub": s, "nsub": len(parts), "text": part,
                       "path": str(work / f"cue_{i:04d}_{s}.wav"), "slot": slot, "start": start, "next": nxt}
                # Warm the model up INSIDE the first clip's OWN generation, so the
                # first real words aren't synthesised as an unstable "first
                # utterance" (the hiccup). The warm-up audio is stripped back off
                # after synthesis. A separate warm-up clip does NOT help — the
                # model resets per generation, so the prefix must share the call.
                if s == 0:
                    if per_cue and warm_word:                 # every cue gets a running start
                        seg["text"] = f"{warm_word} {part}"
                        seg["strip_warmup"] = warm_word
                    elif warm and i == 0:                     # else just cue 1 (old behaviour)
                        seg["text"] = f"{warm} {part}"
                        seg["strip_warmup"] = warm
                segments_by_voice.setdefault(voice_name, []).append(seg)

        _set(message="Generating voiceover (per cue)…")
        
        all_segments = []
        for voice_name, voice_segments in segments_by_voice.items():
            profile = vpm.load(voice_name)
            if not profile:
                _set(status="failed", error=f"Voice '{voice_name}' not found"); return
            profile.language = req.lang_code
            
            sentences = [s["text"] for s in voice_segments]
            outpaths  = [s["path"] for s in voice_segments]
            script = synth.build_synth_script(profile=profile, sentences=sentences, output_paths=outpaths, skip_indices=set())
            
            import subprocess
            r = subprocess.run([synth.synth_python(profile), "-c", script], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800, env=synth.synth_env(profile))
            made = sum(1 for s in voice_segments if Path(s["path"]).exists())
            if made < len([s for s in voice_segments if s["cue"] >= 0]):
                _set(status="failed", error=f"Synthesis failed for voice {voice_name}.\n" + (r.stdout or "")[-300:] + "\n" + (r.stderr or "")[-400:]); return
            
            all_segments.extend(voice_segments)

        # Sort segments back into original chronological order
        segments = sorted(all_segments, key=lambda s: (s["cue"], s["sub"]))

        # Clean each clip's onset, then level for consistent loudness:
        #  • the first clip: strip the prepended warm-up lead (by pause), and
        #  • EVERY clip: trim the soft "shaky" onset wobble (model first-token
        #    instability) without clipping the word.
        from speech.wordsplit import strip_lead_segment
        _set(message="Cleaning & levelling clips…")
        for s in segments:
            if not Path(s["path"]).exists():
                continue
            try:
                y, sr = sf.read(s["path"])
                if getattr(y, "ndim", 1) > 1:
                    y = y.mean(axis=1)
                if s.get("strip_warmup"):
                    y2, stripped = strip_lead_segment(y, sr, on_log=_log)
                    if stripped < 0.15:               # no clear pause → estimate & trim
                        est = len(s["strip_warmup"]) / max(1.0, comf) + 0.15
                        cut = int(min(est, len(y) / sr * 0.6) * sr)
                        y2 = y[cut:]
                        _log(f"warm-up: estimate-trimmed {cut / sr:.2f}s lead", "muted")
                    y = y2
                y = aligner.strip_leading_hum(y, sr)   # remove a 'hmm/mmm' lead-in
                y = aligner.trim_onset_wobble(y, sr)   # remove the shaky lead-in
                sf.write(s["path"], y, sr)
            except Exception as exc:
                _log(f"onset clean failed for cue {s['cue']}: {exc}", "warning")
            _level_clip(s["path"])

        # Build placements. Single-clip cues: at the cue start. Split cues: place
        # the two halves with the leftover slot time as a real pause between them.
        placements = []
        real = [s for s in segments if s["cue"] >= 0 and Path(s["path"]).exists()]
        by_cue = {}
        for s in real:
            by_cue.setdefault(s["cue"], []).append(s)
            
        updated_cues = None
        if getattr(req, "repack_timings", False):
            updated_cues = []
            current_time = 0.0
            for i in range(len(cues)):
                c = dict(cues[i])
                c["start"] = round(current_time, 3)
                if i in by_cue:
                    group = by_cue[i]
                    group.sort(key=lambda s: s["sub"])
                    durs = []
                    for s in group:
                        try:
                            y, sr = sf.read(s["path"]); durs.append((len(y) / sr) if sr else 0.0)
                        except Exception:
                            durs.append(0.0)
                            
                    if len(group) == 1:
                        placements.append({"path": group[0]["path"], "start": round(current_time, 3)})
                        current_time += durs[0]
                    else:
                        gap = 0.25  # natural gap between split sentences
                        for s, d in zip(group, durs):
                            placements.append({"path": s["path"], "start": round(current_time, 3)})
                            current_time += d + gap
                        current_time -= gap
                c["end"] = round(current_time, 3)
                updated_cues.append(c)
                current_time += 0.4  # minimum pause between different cues (paragraphs/speakers)
        else:
            for i, group in by_cue.items():
                group.sort(key=lambda s: s["sub"])
                start = group[0]["start"]; nxt = group[0]["next"]; slot = max(0.05, nxt - start)
                durs = []
                for s in group:
                    try:
                        y, sr = sf.read(s["path"]); durs.append((len(y) / sr) if sr else 0.0)
                    except Exception:
                        durs.append(0.0)
                if len(group) == 1:
                    placements.append({"path": group[0]["path"], "start": start})
                else:
                    gap = max(0.15, (slot - sum(durs) - 0.08) / max(1, len(group) - 1))
                    t = start
                    for s, d in zip(group, durs):
                        placements.append({"path": s["path"], "start": round(t, 3)})
                        t += d + gap
        
        placements.sort(key=lambda p: p["start"])

        if getattr(req, "repack_timings", False) and updated_cues:
            total_duration = max(float(c.get("end", 0)) for c in updated_cues) + 2.0
        else:
            total_duration = max(float(c.get("end", 0)) for c in cues) + 2.0
            
        _set(message="Assembling track…")
        raw = str(work / "synced_raw.wav")
        if not aligner.assemble_track(placements, total_duration, raw, on_log=_log):
            _set(status="failed", error="Assembly failed."); return

        _set(message="Mastering audio…")
        final = str(work / "synced_final.wav")
        if not master(raw, final, on_log=_log):
            final = raw
        final_url = _files_url(Path(final))
        
        if getattr(req, "repack_timings", False):
            _set(status="done", message="Done", synced_audio_url=final_url, updated_cues=updated_cues)
        else:
            _set(status="done", message="Done", synced_audio_url=final_url)
            
        _log(f"Dub complete in {time.time() - start_time:.1f}s")
    except Exception as exc:
        _set(status="failed", error=str(exc))
        _log(f"Fatal error: {exc}", "error")
    finally:
        db.close()


@router.post("/speech/dub-cues", response_model=AdhocSyncJob, status_code=202)
def dub_cues(req: DubCuesRequest, db: Database = Depends(get_db)):
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    with _adhoc_sync_jobs_lock:
        _adhoc_sync_jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued …",
                                    "synced_audio_url": "", "log": [], "error": ""}
    threading.Thread(target=_run_dub_cues, args=(job_id, req), daemon=True).start()
    return AdhocSyncJob(**_adhoc_sync_jobs[job_id])


# ── Incremental redub — re-synthesise ONE cue, then cheaply reassemble ────────
# Editing a line should redub only THAT clip (1 TTS call) and rebuild the final
# track (just audio placement, no TTS) — never re-voice every cue. This is what
# makes the editor usable on hours of audio.

def _clean_cue_clip(path: str, strip_warmup: str, comf: float, _log) -> None:
    """Apply the same onset cleanups as the full dub to a single clip."""
    import soundfile as sf
    from speech import aligner
    from speech.wordsplit import strip_lead_segment
    try:
        y, sr = sf.read(path)
        if getattr(y, "ndim", 1) > 1:
            y = y.mean(axis=1)
        if strip_warmup:
            y2, stripped = strip_lead_segment(y, sr, on_log=_log)
            if stripped < 0.15:
                est = len(strip_warmup) / max(1.0, comf) + 0.15
                cut = int(min(est, len(y) / sr * 0.6) * sr)
                y2 = y[cut:]
            y = y2
        y = aligner.strip_leading_hum(y, sr)
        y = aligner.trim_onset_wobble(y, sr)
        sf.write(path, y, sr)
    except Exception as exc:
        _log(f"clean failed: {exc}", "warning")
    _level_clip(path)


def _assemble_project_dub(cues: list, work: Path, _log) -> str | None:
    """Reassemble the final track from each cue's existing clip (cue_NNNN_0.wav)
    placed at its start, then master. Returns the /files URL or None."""
    import soundfile as sf
    from speech import aligner
    from speech.master import master
    placements = []
    for i, c in enumerate(cues):
        p = work / f"cue_{i:04d}_0.wav"
        if p.exists():
            placements.append({"path": str(p), "start": float(c.get("start", 0))})
    if not placements:
        return None
    total = max(float(c.get("end", 0)) for c in cues) + 2.0
    raw   = str(work / "synced_raw.wav")
    final = str(work / "synced_final.wav")
    if not aligner.assemble_track(placements, total, raw, on_log=_log):
        return None
    if not master(raw, final, on_log=_log):
        final = raw
    return _files_url(Path(final))


def _run_redub_cue(job_id: str, req: "RedubCueRequest"):
    from tts import synth
    from tts.voice_profile import VoiceProfileManager
    from speech import cps
    from pathlib import Path as _P
    import time
    import config
    from database import Database
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    import subprocess
    log_id = db.log_adhoc_start("redub_cue")

    def _set(**kw):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id].update(kw)
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    def _log(msg, level="info"):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id]["log"].append(
                    {"timestamp": __import__("time").time(), "level": level, "message": msg})
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    try:
        i = int(req.index)
        if i < 0 or i >= len(req.cues):
            _set(status="failed", error="Bad cue index"); return
        voice_name = req.cues[i].get("voice") or req.voice
        profile = VoiceProfileManager(str(config.VOICES_DIR)).load(voice_name)
        if not profile:
            _set(status="failed", error=f"Voice '{voice_name}' not found"); return
        profile.language = req.lang_code

        work = Path(config.OUTPUT_DIR) / str(req.project_id) / "dub" / req.lang_code
        work.mkdir(parents=True, exist_ok=True)
        comf = cps.comfortable_cps(req.lang_code)

        text = (req.cues[i].get("translated") or "").strip()
        if not text:
            _set(status="failed", error="Cue has no text to dub"); return
        warm = config.DUB_WARMUP_WORD if (getattr(config, "DUB_WARMUP_PER_CUE", False)
                                          and getattr(config, "DUB_WARMUP_WORD", "")) else ""
        gen_text = f"{warm} {text}" if warm else text
        path = work / f"cue_{i:04d}_0.wav"
        # drop any legacy split half for this cue
        old1 = work / f"cue_{i:04d}_1.wav"
        if old1.exists():
            try: old1.unlink()
            except OSError: pass

        _set(message=f"Re-voicing cue {i + 1}…")
        script = synth.build_synth_script(profile=profile, sentences=[gen_text],
                                          output_paths=[str(path)], skip_indices=set())
        r = subprocess.run([synth.synth_python(profile), "-c", script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, env=synth.synth_env(profile))
        if not path.exists():
            _set(status="failed", error="Synthesis failed.\n" + (r.stderr or "")[-400:]); return

        _set(message="Cleaning clip…")
        _clean_cue_clip(str(path), warm, comf, _log)

        _set(message="Reassembling track…")
        url = _assemble_project_dub(req.cues, work, _log)
        if not url:
            _set(status="failed", error="Reassembly failed"); return
        _set(status="done", message="Cue redubbed!", synced_audio_url=url)
        _log(f"▶ Redubbed cue {i + 1}.", "success")
    except Exception as exc:
        _set(status="failed", error=str(exc))
        _log(f"Fatal error: {exc}", "error")
    finally:
        db.close()


class RedubCueRequest(BaseModel):
    project_id: int
    voice:      str
    lang_code:  str = "French"
    cues:       List[dict]      # all cues (for timing + reassembly)
    index:      int             # which cue to re-voice


@router.post("/speech/redub-cue", response_model=AdhocSyncJob)
def redub_cue(req: RedubCueRequest):
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    with _adhoc_sync_jobs_lock:
        _adhoc_sync_jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued …",
                                    "synced_audio_url": "", "log": [], "error": ""}
    threading.Thread(target=_run_redub_cue, args=(job_id, req), daemon=True).start()
    return AdhocSyncJob(**_adhoc_sync_jobs[job_id])


# ── Refine one cue (the per-cue ✦AI button) ──────────────────────────────────
# Re-translate a single cue SHORTER so it fits its time slot (lower CPS). One
# LLM call (a few attempts, tightening the target). Synchronous — the editor
# shows a per-cue spinner while it waits.

class RefineCueRequest(BaseModel):
    text:       str                 # original English source line
    translated: str                 # current (too-long) translation
    start:      float
    end:        float
    lang_code:  str = "fr"


@router.post("/speech/refine-cue")
def refine_cue(req: RefineCueRequest, db: Database = Depends(get_db)):
    from ai import translator
    from speech import cps
    import time
    start_time = time.time()

    dur = float(req.end) - float(req.start)
    if dur <= 0:
        db.log_adhoc_activity("refine_cue", "failed", time.time() - start_time, [], "Cue has no positive duration")
        raise HTTPException(400, "Cue has no positive duration")

    provider = db.get_setting("ai_provider_translate", "nvidia")
    api_key  = db.get_setting("nvidia_api_key", "")
    lm_model = db.get_setting("lm_studio_model", "")
    try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
    except (TypeError, ValueError): ctx = 32768

    original = (req.translated or "").strip()
    best     = original
    source   = (req.text or best)
    comf     = cps.comfortable_cps(req.lang_code)
    fit      = lambda t: abs(cps.cps_of(t, dur) - comf)
    target   = cps.target_chars(dur, req.lang_code)
    got_response = False

    try:
        for _ in range(3):
            # raise_on_error=True → a network/provider failure surfaces as a real
            # error below instead of silently "succeeding" with the unchanged line.
            cand = (translator.shorten_line(
                source, best, target, req.lang_code,
                provider=provider, api_key=api_key,
                lm_studio_model=lm_model, context_length=ctx,
                on_log=lambda *a, **k: None,
                raise_on_error=True,
            ) or "").strip()
            got_response = True
            if cand and fit(cand) < fit(best):
                best = cand
            if not cps.is_rushed(best, dur, req.lang_code):
                break
            target = int(target * 0.9)
    except Exception as exc:
        db.log_adhoc_activity("refine_cue", "failed", time.time() - start_time, [], f"Couldn't reach the translation model: {exc}")
        raise HTTPException(503, f"Couldn't reach the translation model: {exc}")

    if not got_response:
        db.log_adhoc_activity("refine_cue", "failed", time.time() - start_time, [], "No response from the translation model")
        raise HTTPException(503, "No response from the translation model")

    changed = best != original
    log_msg = f"Refined cue from {len(original)} chars to {len(best)} chars" if changed else "No change needed"
    db.log_adhoc_activity("refine_cue", "done", time.time() - start_time, [{"timestamp": time.time(), "level": "info", "message": log_msg}])
    
    return {
        "translated": best,
        "cps":        round(cps.cps_of(best, dur), 1) if dur > 0 else 0.0,
        "rushed":     bool(cps.is_rushed(best, dur, req.lang_code)),
        "changed":    changed,
    }


# ── AI Refine — rewrite the whole narration script (Brief/Standard/Detailed) ──
# Refines every line at once so the script reads like a polished recap, not the
# raw transcript. Keeps the line COUNT and order so it stays time-synced. Works
# on whichever field is being dubbed (the translation by default).

class RefineScriptRequest(BaseModel):
    lines:        List[str]          # the narration lines to refine, in order
    durations:    List[float] = []   # each line's time slot (s) — for CPS fitting
    level:        str = "standard"   # brief | standard | detailed
    instructions: str = ""           # optional free-form guidance
    lang:         str = "French"     # language of the lines


_LEVEL_DESC = {
    "brief":    "BRIEF — tighten each line to its punchy essence; cut filler, keep the impact. A touch SHORTER than the input.",
    "standard": "STANDARD — clear, natural, well-written narration. About the SAME length as the input.",
    "detailed": "DETAILED — richer and more vivid; add a little descriptive colour. A touch LONGER, but still concise.",
}


@router.post("/speech/refine-script")
def refine_script(req: RefineScriptRequest, db: Database = Depends(get_db)):
    from ai import translator
    from speech import cps
    import time
    start_time = time.time()

    lines = [(s or "").strip() for s in req.lines]
    n = len(lines)
    if n == 0:
        db.log_adhoc_activity("refine_script", "failed", time.time() - start_time, [], "No lines to refine")
        raise HTTPException(400, "No lines to refine")

    durs = list(req.durations) if len(req.durations) == n else [0.0] * n
    # aim_chars = how many characters fit each line's time slot at a comfortable
    # speaking rate. The refine MUST respect this or lines come out at 50+ CPS.
    aims = [cps.target_chars(d, req.lang) if d > 0 else max(1, int(len(l) * 0.95))
            for d, l in zip(durs, lines)]

    provider = db.get_setting("ai_provider_translate", "nvidia")
    api_key  = db.get_setting("nvidia_api_key", "")
    lm_model = db.get_setting("lm_studio_model", "")
    try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
    except (TypeError, ValueError): ctx = 32768

    level = (req.level or "standard").lower()
    level_desc = _LEVEL_DESC.get(level, _LEVEL_DESC["standard"])
    extra = (req.instructions or "").strip()
    items = [{"text": l, "aim_chars": a} for l, a in zip(lines, aims)]

    prompt = (
        f"You are a professional {req.lang} video-narration writer polishing a recap/voiceover script.\n\n"
        f"LEVEL: {level_desc}\n"
        + (f"EXTRA INSTRUCTIONS: {extra}\n" if extra else "")
        + f"\nThis is TIME-SYNCED dubbing. Each line has an \"aim_chars\" = the most characters that "
        f"fit its on-screen time at a natural speaking rate.\n"
        f"Rewrite EACH of the {n} {req.lang} lines so the script reads like a crafted recap — more "
        f"engaging than the raw transcript — while:\n"
        f"  • STAYING AT OR UNDER each line's aim_chars. A line longer than aim_chars gets sped up and "
        f"sounds robotic — this is the #1 rule. Brief = well under; Detailed = close to but not over.\n"
        f"  • Preserving the meaning, facts and story ORDER. Do NOT merge, split, add, or drop lines.\n"
        f"  • Writing natural, fluent {req.lang} (no English, no notes).\n"
        f"Return EXACTLY {n} lines.\n\n"
        f"Return ONLY a JSON array of exactly {n} {req.lang} strings. No markdown, no code fences, no notes.\n\n"
        f"Input ({n} lines, each with its aim_chars):\n{json.dumps(items, ensure_ascii=False, indent=2)}"
    )

    try:
        resp = translator.call_provider(
            prompt, provider=provider, api_key=api_key,
            lm_model=lm_model, max_tokens=4096, context_length=ctx,
        )
    except Exception as exc:
        db.log_adhoc_activity("refine_script", "failed", time.time() - start_time, [], f"Couldn't reach the AI model: {exc}")
        raise HTTPException(503, f"Couldn't reach the AI model: {exc}")

    txt = translator.strip_markdown_fences(translator.strip_thinking_blocks(resp)).strip()
    try:
        arr = json.loads(translator.extract_json_array(txt))
    except Exception:
        db.log_adhoc_activity("refine_script", "failed", time.time() - start_time, [], "AI returned an unparseable response")
        raise HTTPException(502, "AI returned an unparseable response — try again.")
    arr = [str(x).strip() for x in arr if str(x).strip()]
    if len(arr) < n:
        arr += lines[len(arr):]          # fall back to originals for any missing
    elif len(arr) > n:
        arr = arr[:n]

    # CPS-fit pass: any refined line still too dense for its slot gets shortened
    # to fit (so we never hand back a 50-CPS line). Runs concurrently.
    from concurrent.futures import ThreadPoolExecutor

    def _fit(i):
        d, t = durs[i], arr[i]
        if d <= 0 or not cps.is_rushed(t, d, req.lang):
            return t
        target = cps.target_chars(d, req.lang)
        for _ in range(2):
            cand = (translator.shorten_line(t, t, target, req.lang, provider=provider,
                    api_key=api_key, lm_studio_model=lm_model, context_length=ctx,
                    on_log=lambda *a, **k: None) or "").strip()
            if cand and cps.cps_of(cand, d) < cps.cps_of(t, d):
                t = cand
            if not cps.is_rushed(t, d, req.lang):
                break
            target = int(target * 0.9)
        return t

    rushed = [i for i in range(n) if durs[i] > 0 and cps.is_rushed(arr[i], durs[i], req.lang)]
    if rushed:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for i, fixed in zip(rushed, ex.map(_fit, rushed)):
                arr[i] = fixed

    db.log_adhoc_activity("refine_script", "done", time.time() - start_time, [{"timestamp": time.time(), "level": "info", "message": f"Refined {n} lines"}])
    return {"lines": arr}


# ── Per-project Dub Studio session (persisted on disk) ───────────────────────
# Saved to OUTPUT_DIR/<project_id>/dub_session.json so a project's cues, source,
# voice and generated-audio URL survive an app restart and live alongside the
# rest of that project's output.

def _session_path(project_id: int) -> Path:
    return Path(config.OUTPUT_DIR) / str(project_id) / "dub_session.json"


@router.get("/voiceover/projects")
def list_voiceover_projects(db: Database = Depends(get_db)):
    """The Voiceover landing list ('My Files'): every project with the metadata
    its saved session carries (language, duration, cue count, dub status)."""
    out = []
    for row in db.list_projects():
        pid = row["id"]
        if db.get_setting(f"project_kind_{pid}", "") == "video_refine":
            continue                          # Video Refine projects live in their own section
        sess = {}
        p = _session_path(pid)
        if p.exists():
            try:    sess = json.loads(p.read_text(encoding="utf-8"))
            except Exception: sess = {}
        cues = sess.get("cues") or []
        duration = max((float(c.get("end", 0)) for c in cues), default=0.0) if cues else 0.0
        out.append({
            "id":          pid,
            "title":       row.get("name") or f"Project {pid}",
            "language":    sess.get("targetLang") or "",
            "source_path": sess.get("sourcePath") or "",
            "cue_count":   len(cues),
            "duration":    round(duration, 2),
            "has_dub":     bool(sess.get("ttsAudioUrl")),
            "updated_at":  sess.get("updatedAt") or row.get("updated_at"),
            "created_at":  row.get("created_at"),
        })
    return out


@router.get("/speech/dub-session/{project_id}")
def get_dub_session(project_id: int, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    p = _session_path(project_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.put("/speech/dub-session/{project_id}")
def put_dub_session(project_id: int, body: dict, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    p = _session_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"Could not save session: {exc}")
    return {"ok": True}


# ── Export the finished dub (MP4 video or MP3 audio) ─────────────────────────

import re as _re
import subprocess as _sp


def _safe(name: str) -> str:
    return _re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "export").strip()) or "export"


class DubExportRequest(BaseModel):
    lang_code:   str = "French"     # the dub subfolder (matches the session)
    fmt:         str = "video"      # "video" (mp4) | "audio" (mp3)
    source_path: str = ""           # original media (needed for video mux)


@router.post("/speech/export/{project_id}")
def export_dub(project_id: int, body: DubExportRequest, db: Database = Depends(get_db)):
    import time
    start_time = time.time()
    
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project {project_id} not found")

    dub_dir = Path(config.OUTPUT_DIR) / str(project_id) / "dub" / body.lang_code
    audio = dub_dir / "synced_final.wav"
    if not audio.exists():
        raise HTTPException(400, "No dubbed audio yet — generate the dub first.")

    exports = Path(config.OUTPUT_DIR) / str(project_id) / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    base = f"{_safe(proj['name'])}_{_safe(body.lang_code)}"

    if body.fmt == "audio":
        out = exports / f"{base}.mp3"
        try:
            r = _sp.run(["ffmpeg", "-y", "-i", str(audio), "-c:a", "libmp3lame",
                         "-b:a", "192k", str(out)], capture_output=True, text=True, timeout=600)
        except Exception as exc:
            db.log_adhoc_activity("export_dub", "failed", time.time() - start_time, [], f"ffmpeg error: {exc}", proj.get("name", ""))
            raise HTTPException(500, f"ffmpeg error: {exc}")
        if r.returncode != 0 or not out.exists():
            db.log_adhoc_activity("export_dub", "failed", time.time() - start_time, [], f"Audio export failed: {(r.stderr or '')[-400:]}", proj.get("name", ""))
            raise HTTPException(500, f"Audio export failed: {(r.stderr or '')[-400:]}")
    else:
        if not body.source_path or not Path(body.source_path).exists():
            db.log_adhoc_activity("export_dub", "failed", time.time() - start_time, [], "Original video not found", proj.get("name", ""))
            raise HTTPException(400, "Original video not found — needed for an MP4 export.")
        from speech.mux import mux
        out = exports / f"{base}.mp4"
        if not mux(body.source_path, str(audio), str(out)):
            db.log_adhoc_activity("export_dub", "failed", time.time() - start_time, [], "Video export (mux) failed", proj.get("name", ""))
            raise HTTPException(500, "Video export (mux) failed — see logs.")

    db.log_adhoc_activity("export_dub", "done", time.time() - start_time, [{"timestamp": time.time(), "level": "success", "message": f"Exported {body.fmt} to {out.name}"}], "", proj.get("name", ""))
    return {"ok": True, "url": _files_url(out), "path": str(out), "filename": out.name}
