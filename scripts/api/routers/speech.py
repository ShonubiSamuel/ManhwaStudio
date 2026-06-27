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


def _run_adhoc_translate(job_id: str, source_path: str, lang: str, db: Database):
    from speech import segmenter, translate_cues
    import time
    
    def _set(status=None, message=None, cues=None, error=None):
        with _adhoc_jobs_lock:
            job = _adhoc_jobs.get(job_id)
            if not job: return
            if status is not None: job["status"] = status
            if message is not None: job["message"] = message
            if cues is not None: job["cues"] = cues
            if error is not None: job["error"] = error

    def _log(msg: str, level: str = "info"):
        with _adhoc_jobs_lock:
            job = _adhoc_jobs.get(job_id)
            if job:
                job["log"].append({
                    "timestamp": time.time(),
                    "level": level,
                    "message": msg
                })

    try:
        if not Path(source_path).exists():
            _set(status="failed", error="Source audio file not found on disk.")
            return

        _set(message="Transcribing English audio into cues...")
        _log("▶ Starting extraction and translation...", "accent")
        cues = segmenter.transcribe_to_cues(source_path, "en", on_log=_log)
        if not cues:
            _set(status="failed", error="Failed to extract any cues from the audio.")
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

        _set(status="done", message="Extraction and translation complete", cues=translated)

    except Exception as exc:
        _set(status="failed", error=str(exc))


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
        args=(job_id, body.source_path, body.target_lang, db),
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

    def _set(status=None, message=None, synced_audio_url=None, error=None):
        with _adhoc_sync_jobs_lock:
            job = _adhoc_sync_jobs.get(job_id)
            if not job: return
            if status is not None: job["status"] = status
            if message is not None: job["message"] = message
            if synced_audio_url is not None: job["synced_audio_url"] = synced_audio_url
            if error is not None: job["error"] = error

    def _log(msg: str, level: str = "info"):
        with _adhoc_sync_jobs_lock:
            job = _adhoc_sync_jobs.get(job_id)
            if job:
                job["log"].append({
                    "timestamp": time.time(),
                    "level": level,
                    "message": msg
                })

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

@router.post("/speech/adhoc-sync", response_model=AdhocSyncJob)
def adhoc_sync(req: AdhocSyncRequest):
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

    dur = float(req.end) - float(req.start)
    if dur <= 0:
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
        raise HTTPException(503, f"Couldn't reach the translation model: {exc}")

    if not got_response:
        raise HTTPException(503, "No response from the translation model")

    changed = best != original
    return {
        "translated": best,
        "cps":        round(cps.cps_of(best, dur), 1) if dur > 0 else 0.0,
        "rushed":     bool(cps.is_rushed(best, dur, req.lang_code)),
        "changed":    changed,
    }


# ── Per-project Dub Studio session (persisted on disk) ───────────────────────
# Saved to OUTPUT_DIR/<project_id>/dub_session.json so a project's cues, source,
# voice and generated-audio URL survive an app restart and live alongside the
# rest of that project's output.

def _session_path(project_id: int) -> Path:
    return Path(config.OUTPUT_DIR) / str(project_id) / "dub_session.json"


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
            raise HTTPException(500, f"ffmpeg error: {exc}")
        if r.returncode != 0 or not out.exists():
            raise HTTPException(500, f"Audio export failed: {(r.stderr or '')[-400:]}")
    else:
        if not body.source_path or not Path(body.source_path).exists():
            raise HTTPException(400, "Original video not found — needed for an MP4 export.")
        from speech.mux import mux
        out = exports / f"{base}.mp4"
        if not mux(body.source_path, str(audio), str(out)):
            raise HTTPException(500, "Video export (mux) failed — see logs.")

    return {"ok": True, "url": _files_url(out), "path": str(out), "filename": out.name}
