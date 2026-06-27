"""
scripts/api/routers/voices.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Voice-profile management + a Quick TTS playground for the Dubbing studio.

Voice profiles are JSON files under config.VOICES_DIR (VoiceProfileManager is
the single source of truth).  Quick TTS reuses the exact dubbing synthesis path
(build_chapter_script → qwen3-tts subprocess) to render a one-off sample, run in
a background thread with a poll endpoint.

Endpoints
─────────
  GET    /api/voices/{name}      full voice profile
  POST   /api/voices             create a profile
  PATCH  /api/voices/{name}      update a profile
  DELETE /api/voices/{name}      delete a profile
  POST   /api/tts/quick          start a Quick-TTS sample job
  GET    /api/tts/quick/{job}    poll a Quick-TTS job
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException

from api.models import (
    VoiceProfileDetail, VoiceProfileUpsert, VoiceReferenceRequest,
    QuickTTSRequest, VoiceDesignRequest, AdhocDubRequest, QuickTTSJob, OkResponse,
)
from tts.voice_profile import VoiceProfile, VoiceProfileManager
import config

router = APIRouter(tags=["Voices"])

# name -> display name (for picking the Whisper language when transcribing refs)
_NAME_TO_CODE = {n.lower(): c for c, n in config.SUPPORTED_LANGUAGES.items()}

_DETAIL_FIELDS = (
    "name", "mode", "model", "language", "speaker", "instruct",
    "ref_wav_path", "ref_wav_text", "x_vector_only",
    "temperature", "top_p", "top_k", "repetition_penalty", "max_new_tokens", "seed",
)


def _vpm() -> VoiceProfileManager:
    return VoiceProfileManager(str(config.VOICES_DIR))


def _to_detail(p: VoiceProfile) -> VoiceProfileDetail:
    return VoiceProfileDetail(**{f: getattr(p, f) for f in _DETAIL_FIELDS})


# ── Supported languages (for the voice-creation dropdown) ───────────────────────

@router.get("/languages")
def list_languages():
    """Every selectable language + which engine will synthesize it."""
    return [
        {"code": code, "name": name, "engine": config.engine_for_language(code)}
        for code, name in config.SUPPORTED_LANGUAGES.items()
    ]


# ── Voice profile CRUD ─────────────────────────────────────────────────────────

@router.get("/voices/{name}", response_model=VoiceProfileDetail)
def get_voice(name: str):
    p = _vpm().load(name)
    if not p:
        raise HTTPException(404, f"Voice profile '{name}' not found")
    return _to_detail(p)


@router.post("/voices", response_model=VoiceProfileDetail, status_code=201)
def create_voice(body: VoiceProfileUpsert):
    vpm = _vpm()
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Voice name is required")
    if vpm.exists(name):
        raise HTTPException(409, f"Voice profile '{name}' already exists")
    p = VoiceProfile(name)
    for f in _DETAIL_FIELDS:
        v = getattr(body, f, None)
        if v is not None:
            setattr(p, f, v)
    vpm.save(p)
    return _to_detail(p)


@router.patch("/voices/{name}", response_model=VoiceProfileDetail)
def update_voice(name: str, body: VoiceProfileUpsert):
    vpm = _vpm()
    p = vpm.load(name)
    if not p:
        raise HTTPException(404, f"Voice profile '{name}' not found")
    for f in _DETAIL_FIELDS:
        if f == "name":
            continue
        v = getattr(body, f, None)
        if v is not None:
            setattr(p, f, v)
    vpm.save(p)
    return _to_detail(p)


@router.delete("/voices/{name}", response_model=OkResponse)
def delete_voice(name: str):
    vpm = _vpm()
    if not vpm.exists(name):
        raise HTTPException(404, f"Voice profile '{name}' not found")
    vpm.delete(name)
    # Also remove the copied reference clip so deleted voices leave nothing behind.
    try:
        ref = Path(config.VOICE_REF_DIR) / f"{name}.wav"
        if ref.exists():
            ref.unlink()
    except OSError:
        pass
    return OkResponse(ok=True, message=f"Deleted voice '{name}'")


# ── Reference clip (the unified, model-agnostic voice source) ───────────────────

def _to_wav(src: Path, dst: Path) -> bool:
    """Convert any audio file to a mono 24 kHz WAV via ffmpeg."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "24000", str(dst)],
            capture_output=True, text=True, timeout=120,
        )
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


def _transcribe_reference(wav: Path, lang_name: str) -> str:
    """
    One-off transcription of a (short) reference clip with a big, accurate
    multilingual Whisper.  Best-effort — returns "" on any failure; the user can
    always paste/edit the transcript afterwards.

    The language is AUTO-DETECTED from the audio, not forced to the voice's
    target language — the reference transcript must match what the clip actually
    says, which for cross-lingual cloning differs from the output language
    (e.g. an English clip used for a French voice). Forcing the wrong language
    makes Whisper hallucinate boilerplate ("Sous-titres par …").
    """
    try:
        from faster_whisper import WhisperModel
        import runtime_settings as rs
        model = WhisperModel(
            rs.get_str("voice_ref_whisper_model", config.VOICE_REF_WHISPER_MODEL),
            device="cpu", compute_type="int8",
        )
        segs, _ = model.transcribe(
            str(wav), language=None, beam_size=5, vad_filter=True,   # auto-detect
        )
        text = " ".join(s.text.strip() for s in segs).strip()
        del model
        return text
    except Exception:
        return ""


@router.post("/voices/{name}/reference", response_model=VoiceProfileDetail)
def set_voice_reference(name: str, body: VoiceReferenceRequest):
    """
    Attach a reference clip (a local file path, e.g. from the OS file picker) to
    a voice and optionally auto-transcribe it.  This makes the voice a unified,
    model-agnostic clone source usable by BOTH Qwen3 and dots.tts.  The transcript
    stays editable (PATCH the voice) — paste your own any time.
    """
    vpm = _vpm()
    p = vpm.load(name)
    if not p:
        raise HTTPException(404, f"Voice profile '{name}' not found")

    src = Path(body.source_path).expanduser()
    if not src.exists():
        raise HTTPException(400, f"Reference file not found: {src}")

    dst = Path(config.VOICE_REF_DIR) / f"{name}.wav"
    if not _to_wav(src, dst):
        raise HTTPException(500, "Couldn't convert the reference clip to WAV (is ffmpeg installed?)")

    p.mode         = "VoiceClone"
    p.ref_wav_path = str(dst)
    if body.transcribe:
        text = _transcribe_reference(dst, p.language)
        if text:
            p.ref_wav_text = text
    # ICL cloning (with transcript) is the consistent path; fall back to
    # x-vector-only when we have no transcript.
    p.x_vector_only = not bool((p.ref_wav_text or "").strip())
    vpm.save(p)
    return _to_detail(p)


@router.post("/voices/stage-reference")
def stage_reference(body: VoiceReferenceRequest):
    """
    Convert + auto-transcribe a reference clip WITHOUT creating a voice. Returns
    a staged WAV path + transcript; the voice and its final reference are
    committed only when the user hits Save (via createVoice + set-reference).
    This avoids creating "ghost" voices when the new-voice dialog is cancelled.
    """
    src = Path(body.source_path).expanduser()
    if not src.exists():
        raise HTTPException(400, f"Reference file not found: {src}")
    staging = Path(config.VOICE_REF_DIR) / "_staging"
    staging.mkdir(parents=True, exist_ok=True)
    dst = staging / f"{uuid.uuid4().hex[:12]}.wav"
    if not _to_wav(src, dst):
        raise HTTPException(500, "Couldn't convert the reference clip to WAV (is ffmpeg installed?)")
    transcript = _transcribe_reference(dst, "") if body.transcribe else ""
    return {"staged_path": str(dst), "transcript": transcript}


# ── Quick TTS (background sample synthesis) ─────────────────────────────────────

_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _files_url(abs_path: Path) -> str:
    try:
        rel = abs_path.resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return "/files/" + rel.as_posix()
    except (ValueError, OSError):
        return ""


def _job_set(job_id: str, **kw) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def _samples_dir() -> Path:
    d = Path(config.OUTPUT_DIR) / "tts_samples"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _start_job(target, *args, name: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"job_id": job_id, "status": "running", "message": "Queued …",
                         "audio_url": "", "path": "", "error": ""}
    threading.Thread(target=target, args=(job_id, *args), daemon=True, name=f"{name}-{job_id}").start()
    return job_id


def _dub_read_dir(project_id: int, lang_code: str | None) -> Path:
    """Project-scoped output for a dub read, so finished work lives with the
    project under a clean name (not the scratch tts_samples folder)."""
    sub = (lang_code or "audio").strip() or "audio"
    d = Path(config.OUTPUT_DIR) / str(project_id) / "dub" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_quick_tts(job_id: str, text: str, voice: str, language: str | None,
                   project_id: int | None = None, lang_code: str | None = None) -> None:
    from tts import synth
    try:
        profile = _vpm().load(voice)
        if not profile:
            _job_set(job_id, status="failed", error=f"Voice '{voice}' not found"); return
        if language:
            profile.language = language
        if project_id is not None:
            out = _dub_read_dir(project_id, lang_code) / "tts_read.wav"
        else:
            out = _samples_dir() / f"{job_id}.wav"
        _job_set(job_id, message="Loading model & generating …")
        script = synth.build_synth_script(profile=profile, sentences=[text], output_paths=[str(out)], skip_indices=set())
        r = subprocess.run([synth.synth_python(profile), "-c", script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, env=synth.synth_env(profile))
        if "DONE:0" in (r.stdout or "") and out.exists():
            _job_set(job_id, status="done", message="Sample ready", audio_url=_files_url(out), path=str(out))
        else:
            _job_set(job_id, status="failed", error="Synthesis failed.\n" + (r.stderr or "")[-600:])
    except Exception as exc:
        _job_set(job_id, status="failed", error=str(exc))


def _run_voice_design(job_id: str, instruct: str, text: str, language: str) -> None:
    # Qwen3 VoiceDesign — dots.tts can't design, so this always uses the Qwen
    # engine regardless of TTS_BACKEND. The resulting clip is engine-agnostic.
    from tts.script_builder import build_chapter_script, CONDA_PYTHON, subprocess_env
    from tts.voice_profile import VoiceProfile
    try:
        p = VoiceProfile("design")
        p.mode = "VoiceDesign"; p.model = "1.7B-VoiceDesign"
        p.instruct = instruct or ""; p.language = language or "English"
        out = _samples_dir() / f"{job_id}.wav"
        _job_set(job_id, message="Designing voice (Qwen3) …")
        script = build_chapter_script(profile=p, sentences=[text], output_paths=[str(out)], skip_indices=set())
        r = subprocess.run([CONDA_PYTHON, "-c", script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, env=subprocess_env())
        if "DONE:0" in (r.stdout or "") and out.exists():
            _job_set(job_id, status="done", message="Voice designed", audio_url=_files_url(out), path=str(out))
        else:
            _job_set(job_id, status="failed", error="Voice design failed (needs the Qwen3 env).\n" + (r.stderr or "")[-600:])
    except Exception as exc:
        _job_set(job_id, status="failed", error=str(exc))


def _run_adhoc_dub(job_id: str, text: str, voice: str, language: str | None) -> None:
    from tts import synth
    from core.audio_utils import concat_wavs
    try:
        profile = _vpm().load(voice)
        if not profile:
            _job_set(job_id, status="failed", error=f"Voice '{voice}' not found"); return
        if language:
            profile.language = language
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if not lines:
            _job_set(job_id, status="failed", error="No lines to dub"); return
        d = _samples_dir() / job_id; d.mkdir(parents=True, exist_ok=True)
        outs = [str(d / f"{i:03d}.wav") for i in range(len(lines))]
        _job_set(job_id, message=f"Dubbing {len(lines)} line(s) …")
        script = synth.build_synth_script(profile=profile, sentences=lines, output_paths=outs, skip_indices=set())
        subprocess.run([synth.synth_python(profile), "-c", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800, env=synth.synth_env(profile))
        produced = [o for o in outs if Path(o).exists()]
        if not produced:
            _job_set(job_id, status="failed", error="Dubbing produced no audio (check the TTS engine setup)."); return
        combined = _samples_dir() / f"{job_id}.wav"
        concat_wavs(produced, str(combined))
        _job_set(job_id, status="done", message=f"Dubbed {len(produced)}/{len(lines)} line(s)",
                 audio_url=_files_url(combined), path=str(combined))
    except Exception as exc:
        _job_set(job_id, status="failed", error=str(exc))


@router.post("/tts/quick", response_model=QuickTTSJob, status_code=202)
def quick_tts(body: QuickTTSRequest):
    if not (body.text or "").strip():
        raise HTTPException(400, "Text is required")
    if not _vpm().exists(body.voice):
        raise HTTPException(404, f"Voice '{body.voice}' not found")
    job_id = _start_job(_run_quick_tts, body.text.strip(), body.voice, body.language,
                        body.project_id, body.lang_code, name="quicktts")
    return QuickTTSJob(**_jobs[job_id])


@router.post("/voices/design", response_model=QuickTTSJob, status_code=202)
def design_voice(body: VoiceDesignRequest):
    if not (body.text or "").strip():
        raise HTTPException(400, "Sample text is required")
    job_id = _start_job(_run_voice_design, body.instruct or "", body.text.strip(), body.language, name="design")
    return QuickTTSJob(**_jobs[job_id])


@router.post("/tts/dub-adhoc", response_model=QuickTTSJob, status_code=202)
def dub_adhoc(body: AdhocDubRequest):
    if not (body.text or "").strip():
        raise HTTPException(400, "Text is required")
    if not _vpm().exists(body.voice):
        raise HTTPException(404, f"Voice '{body.voice}' not found")
    job_id = _start_job(_run_adhoc_dub, body.text, body.voice, body.language, name="adhoc")
    return QuickTTSJob(**_jobs[job_id])


@router.get("/tts/quick/{job_id}", response_model=QuickTTSJob)
def quick_tts_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return QuickTTSJob(**job)
