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
import re
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_db
from api.models import AdhocTranslateRequest, AdhocTranslateJob, AdhocSyncRequest, AdhocSyncJob
from database import Database
import config
import uuid

router = APIRouter(tags=["Speech dub"])

_lock = threading.Lock()

_adhoc_jobs: dict = {}
_adhoc_jobs_lock = threading.Lock()


def _files_url(abs_path: Path) -> str:
    try:
        rel = Path(abs_path).resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return "/files/" + rel.as_posix()
    except (ValueError, OSError):
        return ""




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
            total_duration = max([float(c.get("end", 0)) for c in req.cues]) + 0.4
            
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
    group:      bool = True   # False for recaps: each panel is its OWN clip (with
                              # real silence between), never merged into one read


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


def _dub_groups(cues: list, default_voice: str) -> list:
    """Deterministically partition cues into contiguous groups for synthesis.

    Back-to-back cues (same voice, source gap < DUB_GROUP_MAX_GAP) are spoken in
    ONE generation for real cross-sentence prosody. Deterministic on the cue list
    alone, so dub and redub always agree on the grouping. Returns [[idx, ...], …].
    """
    if not bool(getattr(config, "DUB_GROUP_ENABLE", True)):
        return [[i] for i in range(len(cues))]
    max_gap   = float(getattr(config, "DUB_GROUP_MAX_GAP", 0.40))
    max_cues  = int(getattr(config, "DUB_GROUP_MAX_CUES", 3))
    max_chars = int(getattr(config, "DUB_GROUP_MAX_CHARS", 280))
    groups, cur, cur_chars = [], [], 0
    for i, c in enumerate(cues):
        text = (c.get("translated") or "").strip()
        if cur:
            prev = cues[cur[-1]]
            gap = float(c.get("start", 0)) - float(prev.get("end", 0))
            same_voice = (c.get("voice") or default_voice) == (prev.get("voice") or default_voice)
            if (same_voice and 0 <= gap < max_gap and len(cur) < max_cues
                    and cur_chars + len(text) <= max_chars):
                cur.append(i); cur_chars += len(text)
                continue
            groups.append(cur)
        cur, cur_chars = [i], len(text)
    if cur:
        groups.append(cur)
    return groups


def _run_dub_cues(job_id: str, req: DubCuesRequest):
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
        # Contiguous cues (same voice, tiny source gap) are spoken TOGETHER in one
        # generation — real cross-sentence prosody instead of per-cue "islands"
        # that each end on a full-stop melody. Each group is ONE clip placed at
        # the group's start, spanning the combined slot (no re-splitting needed).
        # Recaps (group=False): one clip per cue, so every panel is its own audio
        # with real silence between — never merged into a single continuous read.
        groups = _dub_groups(cues, req.voice) if getattr(req, "group", True) else [[i] for i in range(len(cues))]
        joined = sum(1 for g in groups if len(g) > 1)
        if joined:
            _log(f"flow: joined {joined} contiguous group(s) for smoother prosody", "muted")

        def _speakable(t: str) -> str:
            # Shared with the Voices preview — one normalisation, one source of truth.
            from tts import clip as _clip
            return _clip.speakable(t)

        segments_by_voice = {}
        for g in groups:
            first, last = g[0], g[-1]
            voice_name = cues[first].get("voice") or req.voice
            text = " ".join(_speakable((cues[i].get("translated") or "").strip()) for i in g).strip()
            start = float(cues[first].get("start", 0)); end = float(cues[last].get("end", 0))
            nxt = float(cues[last + 1]["start"]) if last + 1 < len(cues) else end
            slot = max(0.05, nxt - start)
            # Say the whole passage as ONE clip so every pause in it is the AI's
            # own natural pause, not a gap our code inserts. Slot-filling is
            # handled by breathing (lengthening those real pauses).
            seg = {"cue": first, "sub": 0, "nsub": 1, "text": text, "members": list(g),
                   "path": str(work / f"cue_{first:04d}_0.wav"), "slot": slot, "start": start, "next": nxt}
            # Record the warm-up word (prepended INSIDE the clip's own generation,
            # then stripped) that stabilises the first token. The shared pipeline
            # (clip.synth_clips) does the prepend + strip; we keep seg["text"] raw.
            if per_cue and warm_word:                 # every generation gets a running start
                seg["strip_warmup"] = warm_word
            elif warm and first == 0:                 # else just cue 1 (old behaviour)
                seg["strip_warmup"] = warm
            segments_by_voice.setdefault(voice_name, []).append(seg)
            # Remove stale clips for absorbed members (and legacy split halves) so
            # a reassembly never double-places words that now live in the group clip.
            for m in g:
                for stale in ([f"cue_{m:04d}_1.wav"] + ([f"cue_{m:04d}_0.wav"] if m != first else [])):
                    p = work / stale
                    if p.exists():
                        try: p.unlink()
                        except OSError: pass

        _set(message="Generating voiceover (grouped cues)…")

        # Generate + clean every clip through the ONE shared per-clip pipeline —
        # identical to the Voices preview and redub. Each voice's clips render in a
        # single model load; clip.synth_clips also does the warm-up strip, hum/onset
        # cleanup, silence trim and levelling. (Cue grouping above + track assembly
        # below stay here, since those are timeline concerns.)
        from tts import clip as _clip
        all_segments = []
        for voice_name, voice_segments in segments_by_voice.items():
            profile = vpm.load(voice_name)
            if not profile:
                _set(status="failed", error=f"Voice '{voice_name}' not found"); return
            profile.language = req.lang_code
            ok = _clip.synth_clips(
                profile,
                [s["text"] for s in voice_segments],
                [s["path"] for s in voice_segments],
                warmups=[s.get("strip_warmup", "") for s in voice_segments],
                clean=True, save_stages=getattr(config, "DUB_SAVE_STAGES", False),
                comf=comf, on_log=_log, timeout=1800)
            if not ok:
                _set(status="failed", error=f"Synthesis failed for voice {voice_name}."); return
            all_segments.extend(voice_segments)

        # Sort segments back into original chronological order
        segments = sorted(all_segments, key=lambda s: (s["cue"], s["sub"]))

        # Build placements. One clip per group, placed at the group's start; the
        # clip internally carries the group's natural cross-sentence prosody.
        placements = []
        real = [s for s in segments if s["cue"] >= 0 and Path(s["path"]).exists()]
        by_head = {s["cue"]: s for s in real}

        def _clip_dur(path):
            try:
                y, sr = sf.read(path); return (len(y) / sr) if sr else 0.0
            except Exception:
                return 0.0

        updated_cues = None
        if getattr(req, "repack_timings", False):
            # Repack: lay the clips end-to-end at their natural lengths. A group
            # clip spans its member cues; apportion its duration across them by
            # text share (timeline display only — the audio is one natural read).
            updated_cues = [None] * len(cues)
            current_time = 0.0
            for i in range(len(cues)):
                if updated_cues[i] is not None:
                    continue                      # already timed as a group member
                seg = by_head.get(i)
                if seg:
                    dur = _clip_dur(seg["path"])
                    placements.append({"path": seg["path"], "start": round(current_time, 3)})
                    members = seg.get("members", [i])
                    weights = [max(1, len((cues[m].get("translated") or "").strip())) for m in members]
                    tot = float(sum(weights))
                    t = current_time
                    for m, w in zip(members, weights):
                        c = dict(cues[m]); c["start"] = round(t, 3)
                        t += dur * (w / tot)
                        c["end"] = round(t, 3)
                        updated_cues[m] = c
                    current_time += dur
                else:
                    c = dict(cues[i])
                    c["start"] = c["end"] = round(current_time, 3)
                    updated_cues[i] = c
                current_time += 0.3  # natural breath between passages (clips are now silence-trimmed)
            updated_cues = [c for c in updated_cues if c is not None]
            # PERFECT SPLIT: no dead gaps in the timeline. Each cue's slot runs right
            # up to the next cue's start, so the inter-cue breath is absorbed INTO the
            # slot (audio still breathes; the timeline tiles seamlessly). The cues now
            # sum exactly to the track's spoken length — e.g. a 3:00 read of two even
            # cues becomes 1:30 + 1:30 with no gap between them.
            for a, b in zip(updated_cues, updated_cues[1:]):
                a["end"] = b["start"]
        else:
            for i, seg in by_head.items():
                placements.append({"path": seg["path"], "start": seg["start"]})

        placements.sort(key=lambda p: p["start"])

        # Per-cue ACTUAL audio length (the clip's real duration, split across a
        # group's member cues by text share). The editor uses this to show where
        # each cue's audio ends and the silence gap before the next cue — most
        # visible on translations that are shorter than the original slot.
        audio_durs = [0.0] * len(cues)
        for seg in real:
            members = seg.get("members", [seg["cue"]])
            total = _clip_dur(seg["path"])
            weights = [max(1, len((cues[m].get("translated") or "").strip())) for m in members]
            tot = float(sum(weights)) or 1.0
            for m, w in zip(members, weights):
                if 0 <= m < len(cues):
                    audio_durs[m] = round(total * (w / tot), 3)

        if getattr(req, "repack_timings", False) and updated_cues:
            total_duration = max(float(c.get("end", 0)) for c in updated_cues) + 0.4
        else:
            total_duration = max(float(c.get("end", 0)) for c in cues) + 0.4

        # Audit: the TRUE combined output at natural pace — every cue's clip glued
        # back-to-back at its own length, NO timeline fitting, NO speed-compression.
        # This is the honest uncompressed combined length; synced_raw.wav below is the
        # SAME clips fit to the video timeline (and sped up if they overran their
        # slot), so combined_natural vs synced_raw isolates what the timing step did.
        if getattr(config, "DUB_SAVE_STAGES", False) and real:
            try:
                from core.audio_utils import concat_wavs
                concat_wavs([s["path"] for s in real], str(work / "combined_natural.wav"), silence_secs=0.3)
                _log(f"audit: combined_natural.wav = {len(real)} clip(s) at natural pace", "muted")
            except Exception as exc:
                _log(f"audit: combined_natural failed: {exc}", "warning")

        _set(message="Assembling track…")
        raw = str(work / "synced_raw.wav")
        # Recaps (group=False) don't breathe short lines to fill the slot — they
        # keep a real pause of silence after each panel.
        if not aligner.assemble_track(placements, total_duration, raw, on_log=_log,
                                      breathe=getattr(req, "group", True)):
            _set(status="failed", error="Assembly failed."); return

        _set(message="Mastering audio…")
        final = str(work / "synced_final.wav")
        if not master(raw, final, on_log=_log):
            final = raw
        final_url = _files_url(Path(final))
        
        # Archive every clip so this whole audio state is undoable later.
        audio_keys = _archive_and_key(cues, work, req.voice, getattr(req, "group", True))
        if getattr(req, "repack_timings", False):
            _set(status="done", message="Done", synced_audio_url=final_url, updated_cues=updated_cues, audio_durs=audio_durs, audio_keys=audio_keys)
        else:
            _set(status="done", message="Done", synced_audio_url=final_url, audio_durs=audio_durs, audio_keys=audio_keys)

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

def _audio_durs_from_disk(cues: list, work: Path, default_voice: str, group: bool) -> list:
    """Per-cue ACTUAL audio length, read from the clips ON DISK. A group is one
    clip on its head cue; its duration is split across the group's member cues by
    text share — the same attribution _run_dub_cues does in memory. The editor's
    timeline uses this to draw where each cue's speech ends and the silence begins.
    Grouping must match generation exactly, so mirror the redub's own choice."""
    import soundfile as sf
    def _dur(p):
        try:
            info = sf.info(str(p)); return float(info.frames) / float(info.samplerate or 1)
        except Exception:
            return 0.0
    groups = _dub_groups(cues, default_voice) if group else [[i] for i in range(len(cues))]
    durs = [0.0] * len(cues)
    for g in groups:
        head = g[0]
        p = work / f"cue_{head:04d}_0.wav"
        if not p.exists():
            continue
        total = _dur(p)
        weights = [max(1, len((cues[m].get("translated") or "").strip())) for m in g]
        tot = float(sum(weights)) or 1.0
        for m, w in zip(g, weights):
            if 0 <= m < len(cues):
                durs[m] = round(total * (w / tot), 3)
    return durs


# ── Audio history (undo/redo of the actual generated audio) ──────────────────
# TTS is non-deterministic, so "undo to the previous audio" can't re-synthesise —
# it must restore the exact clip that was live before. Every time a cue's clip is
# (re)generated we copy it into work/history/<key>.wav and hand the key back; the
# editor stores it on the cue, so an undo snapshot carries the audio identity and
# can be materialised again by _run_restore_audio.

def _archive_clip(work: Path, head: int) -> str:
    """Copy a freshly generated head clip into the history store and return its key
    (''  if the clip isn't on disk)."""
    import shutil, uuid
    src = work / f"cue_{head:04d}_0.wav"
    if not src.exists():
        return ""
    hist = work / "history"
    try:
        hist.mkdir(exist_ok=True)
        key = f"{head:04d}_{uuid.uuid4().hex[:10]}"
        shutil.copy2(src, hist / f"{key}.wav")
        return key
    except OSError:
        return ""


def _archive_and_key(cues: list, work: Path, default_voice: str, group: bool,
                     changed_heads: set | None = None) -> list:
    """Archive the group-head clips and return a per-cue list of version keys (a
    group's members share the head's key). `changed_heads=None` archives every
    group (a full generate); otherwise only those heads are archived and every
    other cue's entry is None so the editor keeps its existing key."""
    groups = _dub_groups(cues, default_voice) if group else [[i] for i in range(len(cues))]
    keys: list = [None] * len(cues)
    for g in groups:
        head = g[0]
        if changed_heads is not None and head not in changed_heads:
            continue
        k = _archive_clip(work, head)
        for m in g:
            if 0 <= m < len(cues):
                keys[m] = k
    return keys


def _assemble_project_dub(cues: list, work: Path, _log, breathe: bool = True) -> str | None:
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
    total = max(float(c.get("end", 0)) for c in cues) + 0.4
    raw   = str(work / "synced_raw.wav")
    final = str(work / "synced_final.wav")
    if not aligner.assemble_track(placements, total, raw, on_log=_log, breathe=breathe):
        return None
    if not master(raw, final, on_log=_log):
        final = raw
    return _files_url(Path(final))


def _run_redub_cue(job_id: str, req: "RedubCueRequest"):
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

        # The cue may live inside a contiguous GROUP (spoken as one passage) — the
        # grouping is deterministic on the cue list, so recompute it and re-voice
        # the WHOLE group. That keeps the cross-sentence flow intact and keeps the
        # one-clip-per-group file layout consistent with the full dub.
        grp = (next((g for g in _dub_groups(req.cues, req.voice) if i in g), [i])
               if getattr(req, "group", True) else [i])
        head = grp[0]
        text = " ".join((req.cues[m].get("translated") or "").strip() for m in grp).strip()
        if not text:
            _set(status="failed", error="Cue has no text to dub"); return
        path = work / f"cue_{head:04d}_0.wav"
        # drop stale member clips + legacy split halves (their words are in the group clip)
        for m in grp:
            for stale in ([f"cue_{m:04d}_1.wav"] + ([f"cue_{m:04d}_0.wav"] if m != head else [])):
                p = work / stale
                if p.exists():
                    try: p.unlink()
                    except OSError: pass

        _set(message=f"Re-voicing cue {i + 1}" + (f" (group of {len(grp)})" if len(grp) > 1 else "") + "…")
        # Generate + clean via the SHARED pipeline (same as the full dub / preview).
        from tts import clip as _clip
        if not _clip.synth_clip(profile, text, str(path), warmup=_clip.warmup_word(),
                                save_stages=getattr(config, "DUB_SAVE_STAGES", False),
                                comf=comf, on_log=_log, timeout=600) or not path.exists():
            _set(status="failed", error="Synthesis failed."); return

        _set(message="Reassembling track…")
        url = _assemble_project_dub(req.cues, work, _log, breathe=getattr(req, "group", True))
        if not url:
            _set(status="failed", error="Reassembly failed"); return
        # Return the fresh per-cue durations so the editor's timeline updates the
        # re-voiced cue's speech/silence split (not just the audio) immediately, plus
        # the new clip's version key (only this group's cues change) for audio-undo.
        grouping = getattr(req, "group", True)
        audio_durs = _audio_durs_from_disk(req.cues, work, req.voice, grouping)
        audio_keys = _archive_and_key(req.cues, work, req.voice, grouping, changed_heads={head})
        _set(status="done", message="Cue redubbed!", synced_audio_url=url, audio_durs=audio_durs, audio_keys=audio_keys)
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
    group:      bool = True     # False for recaps: re-voice only this cue


@router.post("/speech/redub-cue", response_model=AdhocSyncJob)
def redub_cue(req: RedubCueRequest):
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    with _adhoc_sync_jobs_lock:
        _adhoc_sync_jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued …",
                                    "synced_audio_url": "", "log": [], "error": ""}
    threading.Thread(target=_run_redub_cue, args=(job_id, req), daemon=True).start()
    return AdhocSyncJob(**_adhoc_sync_jobs[job_id])


# ── Batch redub — re-voice SEVERAL cues, then reassemble ONCE ─────────────────
# The single-cue redub reassembles+masters the whole track every call. When the
# editor fixes many cues at once (batch ✦AI Fix), that would master N times.
# This re-voices every requested cue's group (batched per voice = one model load
# per voice) and reassembles+masters exactly once.

def _run_redub_cues(job_id: str, req: "RedubCuesRequest"):
    from tts.voice_profile import VoiceProfileManager
    from speech import cps
    import time
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    log_id = db.log_adhoc_start("redub_cues")

    def _set(**kw):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id].update(kw)
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    def _log(msg, level="info"):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id]["log"].append({"timestamp": time.time(), "level": level, "message": msg})
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    try:
        vpm = VoiceProfileManager(str(config.VOICES_DIR))
        n = len(req.cues)
        want = sorted({int(i) for i in req.indices if 0 <= int(i) < n})
        if not want:
            _set(status="failed", error="No valid cue indices"); return

        work = Path(config.OUTPUT_DIR) / str(req.project_id) / "dub" / req.lang_code
        work.mkdir(parents=True, exist_ok=True)
        comf = cps.comfortable_cps(req.lang_code)
        grouping = getattr(req, "group", True)

        # Map each requested cue to its group (deterministic), deduped by head so a
        # group touched by two selected members is only re-voiced once.
        groups_all = _dub_groups(req.cues, req.voice) if grouping else [[i] for i in range(n)]
        heads: dict = {}
        for idx in want:
            g = next((g for g in groups_all if idx in g), [idx])
            heads[g[0]] = g

        # Bucket the group heads by voice so each voice renders in ONE model load.
        by_voice: dict = {}
        for head, g in heads.items():
            text = " ".join((req.cues[m].get("translated") or "").strip() for m in g).strip()
            if not text:
                continue
            # Drop stale member clips + legacy split halves (words live in the head clip).
            for m in g:
                for stale in ([f"cue_{m:04d}_1.wav"] + ([f"cue_{m:04d}_0.wav"] if m != head else [])):
                    p = work / stale
                    if p.exists():
                        try: p.unlink()
                        except OSError: pass
            voice_name = req.cues[head].get("voice") or req.voice
            by_voice.setdefault(voice_name, []).append(
                {"head": head, "text": text, "path": str(work / f"cue_{head:04d}_0.wav")})

        if not by_voice:
            _set(status="failed", error="Selected cues have no text to dub"); return

        from tts import clip as _clip
        done = 0
        total = sum(len(v) for v in by_voice.values())
        for voice_name, items in by_voice.items():
            profile = vpm.load(voice_name)
            if not profile:
                _set(status="failed", error=f"Voice '{voice_name}' not found"); return
            profile.language = req.lang_code
            _set(message=f"Re-voicing {done + len(items)}/{total} cue(s)…")
            ok = _clip.synth_clips(
                profile, [it["text"] for it in items], [it["path"] for it in items],
                warmups=[_clip.warmup_word()] * len(items),
                save_stages=getattr(config, "DUB_SAVE_STAGES", False),
                comf=comf, on_log=_log, timeout=1800)
            if not ok:
                _set(status="failed", error=f"Synthesis failed for voice {voice_name}."); return
            done += len(items)

        _set(message="Reassembling track…")
        url = _assemble_project_dub(req.cues, work, _log, breathe=grouping)
        if not url:
            _set(status="failed", error="Reassembly failed"); return
        audio_durs = _audio_durs_from_disk(req.cues, work, req.voice, grouping)
        audio_keys = _archive_and_key(req.cues, work, req.voice, grouping, changed_heads=set(heads.keys()))
        _set(status="done", message=f"Redubbed {total} cue(s)!", synced_audio_url=url, audio_durs=audio_durs, audio_keys=audio_keys)
        _log(f"▶ Batch-redubbed {total} cue(s) in {time.time() - start_time:.1f}s.", "success")
    except Exception as exc:
        _set(status="failed", error=str(exc))
        _log(f"Fatal error: {exc}", "error")
    finally:
        db.close()


class RedubCuesRequest(BaseModel):
    project_id: int
    voice:      str
    lang_code:  str = "French"
    cues:       List[dict]      # all cues (for timing + reassembly)
    indices:    List[int]       # which cues to re-voice
    group:      bool = True     # False for recaps: re-voice each cue on its own


@router.post("/speech/redub-cues", response_model=AdhocSyncJob)
def redub_cues(req: RedubCuesRequest):
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    with _adhoc_sync_jobs_lock:
        _adhoc_sync_jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued …",
                                    "synced_audio_url": "", "log": [], "error": ""}
    threading.Thread(target=_run_redub_cues, args=(job_id, req), daemon=True).start()
    return AdhocSyncJob(**_adhoc_sync_jobs[job_id])


# ── Restore audio (undo/redo of the actual generated audio) ───────────────────
# The editor snapshots each cue's clip version key. To go back/forward in audio
# history it sends the target snapshot's keys here: we copy each cue's archived
# clip back into place from work/history/<key>.wav and reassemble the track. No
# TTS runs — this is pure file restore + master, so it's fast.

def _run_restore_audio(job_id: str, req: "RestoreAudioRequest"):
    import shutil, time
    start_time = time.time()
    db = Database(str(config.DB_PATH))
    log_id = db.log_adhoc_start("restore_audio")

    def _set(**kw):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id].update(kw)
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    def _log(msg, level="info"):
        with _adhoc_sync_jobs_lock:
            if job_id in _adhoc_sync_jobs:
                _adhoc_sync_jobs[job_id]["log"].append({"timestamp": time.time(), "level": level, "message": msg})
                db.log_adhoc_update(log_id, _adhoc_sync_jobs[job_id]["status"], _adhoc_sync_jobs[job_id]["log"], _adhoc_sync_jobs[job_id].get("error", ""))

    try:
        work = Path(config.OUTPUT_DIR) / str(req.project_id) / "dub" / req.lang_code
        hist = work / "history"
        grouping = getattr(req, "group", True)
        groups = _dub_groups(req.cues, req.voice) if grouping else [[i] for i in range(len(req.cues))]
        keys = req.keys or []

        restored = missing = 0
        for g in groups:
            head = g[0]
            key = keys[head] if head < len(keys) else None
            if not key:
                continue
            src = hist / f"{key}.wav"
            if not src.exists():
                missing += 1
                continue
            try:
                shutil.copy2(src, work / f"cue_{head:04d}_0.wav")
                restored += 1
            except OSError:
                missing += 1
        if missing:
            _log(f"{missing} clip version(s) were no longer in history — kept current audio for those.", "warning")

        _set(message="Reassembling track…")
        url = _assemble_project_dub(req.cues, work, _log, breathe=grouping)
        if not url:
            _set(status="failed", error="Reassembly failed"); return
        audio_durs = _audio_durs_from_disk(req.cues, work, req.voice, grouping)
        _set(status="done", message=f"Restored {restored} clip(s)", synced_audio_url=url, audio_durs=audio_durs)
        _log(f"↺ Audio restored in {time.time() - start_time:.1f}s.", "success")
    except Exception as exc:
        _set(status="failed", error=str(exc))
        _log(f"Fatal error: {exc}", "error")
    finally:
        db.close()


class RestoreAudioRequest(BaseModel):
    project_id: int
    voice:      str
    lang_code:  str = "French"
    cues:       List[dict]              # target snapshot's cues (timing + grouping)
    keys:       List[Optional[str]]     # per-cue clip version key to restore
    group:      bool = True


@router.post("/speech/restore-audio", response_model=AdhocSyncJob)
def restore_audio(req: RestoreAudioRequest):
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    with _adhoc_sync_jobs_lock:
        _adhoc_sync_jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued …",
                                    "synced_audio_url": "", "log": [], "error": ""}
    threading.Thread(target=_run_restore_audio, args=(job_id, req), daemon=True).start()
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

    # Each retry sees the FULL attempt history with measured CPS, so the model
    # learns from its misses ("26.4, then 27.1 — go clearly shorter than both")
    # instead of re-rolling the same length. Too-SHORT lines (dead air before the
    # next cue) get the inverse treatment: expand toward the slot, no new facts.
    cps_now  = cps.cps_of(best, dur)
    sparse   = comf > 0 and dur >= 2.0 and cps_now < 0.55 * comf and not cps.is_rushed(best, dur, req.lang_code)
    attempts = []
    kw = dict(provider=provider, api_key=api_key, lm_studio_model=lm_model,
              context_length=ctx, on_log=lambda *a, **k: None, raise_on_error=True, comf=comf)
    try:
        if sparse:
            target = int(dur * comf * 0.85)          # fill ~85% of the slot
            for _ in range(3):
                cand = (translator.expand_line(source, best, target, req.lang_code,
                                               attempts=attempts, **kw) or "").strip()
                got_response = True
                if cand:
                    attempts.append({"text": cand, "cps": cps.cps_of(cand, dur)})
                    if fit(cand) < fit(best):
                        best = cand
                if cps.cps_of(best, dur) >= 0.7 * comf:
                    break
        else:
            for _ in range(3):
                # raise_on_error=True → a network/provider failure surfaces as a
                # real error below instead of silently "succeeding" unchanged.
                cand = (translator.shorten_line(source, best, target, req.lang_code,
                                                attempts=attempts, **kw) or "").strip()
                got_response = True
                if cand:
                    attempts.append({"text": cand, "cps": cps.cps_of(cand, dur)})
                    if fit(cand) < fit(best):
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
            lm_model=lm_model, max_tokens=4096, context_length=ctx, task="refine",
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
        if db.get_setting(f"project_kind_{pid}", "") in ("video_refine", "recap"):
            continue                          # those live in their own sections, never in Voiceover
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
