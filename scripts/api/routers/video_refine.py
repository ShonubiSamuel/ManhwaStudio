"""
scripts/api/routers/video_refine.py — ManhwaStudio v2

Backend for the "Video Refine" workflow (manga recaps):

  • render an uploaded PDF to page images for the in-app crop tool,
  • crop a panel from a page (server-side, from the high-res render) and
    immediately 4× UPSCALE it (RealESRGAN anime),
  • grab a low-quality reference frame from the source video per cue,
  • persist a per-project "video refine" session (separate from the dub session,
    so the Voiceover/audio system is never touched).

All file inputs are absolute local paths (the app uses native file pickers), and
outputs live under  OUTPUT_DIR/<project_id>/refine/  +  /panels/.
"""

from __future__ import annotations

import json
import subprocess
import threading
import base64
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import config
from api.deps import get_db
from database import Database

router = APIRouter(prefix="/video-refine", tags=["Video Refine"])

FILES_PREFIX = "/files/"

# Lazily-built, cached RealESRGAN upsampler (heavy to load — share across crops).
_upsampler = None
_upsampler_lock = threading.Lock()
_MODEL_PATH = config.BASE_DIR / "models" / "RealESRGAN_x4plus_anime_6B.pth"


def _files_url(p: Path) -> str:
    try:
        rel = Path(p).resolve().relative_to(Path(config.OUTPUT_DIR).resolve())
        return FILES_PREFIX + rel.as_posix()
    except (ValueError, OSError):
        return ""


def _project_dir(project_id: int) -> Path:
    d = Path(config.OUTPUT_DIR) / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Session (separate file from the dub session)
# ─────────────────────────────────────────────────────────────────────────────

def _session_path(project_id: int) -> Path:
    return _project_dir(project_id) / "video_refine_session.json"


def _kind_key(pid: int) -> str:
    return f"project_kind_{pid}"


def _dub_session_path(pid: int) -> Path:
    # Video Refine reuses the SAME dub session as Voiceover (it IS the Voiceover
    # editor + panel cropping), so cues/voice/dub/panels all live in one file.
    return _project_dir(pid) / "dub_session.json"


@router.get("/projects")
def list_refine_projects(kind: str = "video_refine", db: Database = Depends(get_db)):
    """Projects of one kind: 'video_refine' (video-sourced) or 'recap' (PDF-sourced)."""
    out = []
    for row in db.list_projects():
        pid = row["id"]
        if db.get_setting(_kind_key(pid), "") != kind:
            continue
        sess = {}
        p = _dub_session_path(pid)
        if p.exists():
            try:    sess = json.loads(p.read_text(encoding="utf-8"))
            except Exception: sess = {}
        cues = sess.get("cues") or []
        out.append({
            "id": pid, "title": row.get("name") or f"Project {pid}",
            "cue_count": len(cues), "source_path": sess.get("sourcePath") or "",
            "updated_at": sess.get("updatedAt") or row.get("updated_at"),
        })
    out.sort(key=lambda p: p.get("updated_at") or 0, reverse=True)
    return out


class RefineProjectCreate(BaseModel):
    name:        str
    source_path: str = ""
    kind:        str = "video_refine"    # or "recap" (PDF-sourced, no video)


@router.post("/projects")
def create_refine_project(body: RefineProjectCreate, db: Database = Depends(get_db)):
    name = (body.name or "").strip() or "Untitled refine"
    kind = body.kind if body.kind in ("video_refine", "recap") else "video_refine"
    pid = db.add_project(name)
    # add_project may auto-suffix a duplicate name ("Foo (2)") to keep projects
    # distinct — report the ACTUAL stored title so the UI matches the DB.
    name = (db.get_project(pid) or {}).get("name") or name
    # Tag the kind so each section lists only its own projects.
    db.set_setting(_kind_key(pid), kind)
    # Seed the DUB session (both sections use the Voiceover editor).
    # No default language: the user adds languages themselves (the + button).
    # Recap: the "source" IS the PDF — seed pdfPath so the editor opens on it.
    _dub_session_path(pid).write_text(json.dumps({
        "cues": [], "sourcePath": body.source_path, "targetLang": "",
        "languages": [], "selectedLang": "", "kind": kind,
        "pdfPath": body.source_path if kind == "recap" else "",
        "updatedAt": __import__("time").time() * 1000,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": pid, "title": name, "cue_count": 0, "source_path": body.source_path}


# ── Recap profile + legacy compatibility, per project ─────────────────────────

def _recap_state_path(pid: int) -> Path:
    return _project_dir(pid) / "recap_state.json"


def _load_recap_state(pid: int) -> dict:
    from ai import recap_narrator
    p = _recap_state_path(pid)
    if p.exists():
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            st.setdefault("registry", {"characters": []})
            st.setdefault("memory", "")
            st.setdefault("cast_seed", "")
            return st
        except Exception:
            pass
    return recap_narrator.blank_state()


def _save_recap_state(pid: int, state: dict) -> None:
    _recap_state_path(pid).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/recap-state/{project_id}")
def get_recap_state(project_id: int, db: Database = Depends(get_db)):
    """Legacy-compatible recap profile (authored cast seed + rolling summary)."""
    return _load_recap_state(project_id)


@router.put("/recap-state/{project_id}")
def put_recap_state(project_id: int, body: dict, db: Database = Depends(get_db)):
    """Persist authored cast context and the rolling recap summary."""
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    state = _load_recap_state(project_id)
    if isinstance(body.get("registry"), dict):     state["registry"] = body["registry"]
    if "memory" in body:                            state["memory"] = str(body.get("memory") or "")
    if "cast_seed" in body:                         state["cast_seed"] = str(body.get("cast_seed") or "")
    _save_recap_state(project_id, state)
    return {"ok": True}


@router.get("/story-memory/{project_id}")
def get_story_memory(project_id: int, db: Database = Depends(get_db)):
    """Reviewable canonical memory.  Every entity/event retains panel evidence."""
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    from ai import story_memory
    # Existing manually maintained registry is imported lazily, so old projects
    # gain structured memory without a destructive conversion step.
    story_memory.sync_legacy_registry(db, project_id, _load_recap_state(project_id).get("registry", {}))
    return story_memory.snapshot(db, project_id)


@router.patch("/story-memory/{project_id}/characters/{stable_id}")
def patch_story_character(project_id: int, stable_id: str, body: dict,
                          db: Database = Depends(get_db)):
    """Human corrections win over model output and feed every later retrieval."""
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    if not db.get_story_character(stable_id):
        raise HTTPException(404, f"Character {stable_id} not found")
    before = db.story_memory_snapshot_raw(project_id)
    updated = db.update_story_character(project_id, stable_id, **body)
    db.record_story_memory_history(project_id, "edit character", before, db.story_memory_snapshot_raw(project_id))
    from ai import story_memory
    return {"character": updated or {}, "memory": story_memory.snapshot(db, project_id)}


@router.delete("/story-memory/{project_id}/characters/{stable_id}")
def delete_story_character(project_id: int, stable_id: str, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    character = db.get_story_character(stable_id)
    if not character:
        raise HTTPException(404, f"Character {stable_id} not found")
    before = db.story_memory_snapshot_raw(project_id)
    from ai import story_memory
    story_memory.retire_character_labels(db, project_id, character)
    db.delete_story_character(project_id, stable_id)
    db.record_story_memory_history(project_id, "delete character", before, db.story_memory_snapshot_raw(project_id))
    return story_memory.snapshot(db, project_id)


@router.post("/story-memory/{project_id}/undo")
def undo_story_memory(project_id: int, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    db.story_memory_undo(project_id)
    from ai import story_memory
    return story_memory.snapshot(db, project_id)


@router.post("/story-memory/{project_id}/redo")
def redo_story_memory(project_id: int, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    db.story_memory_redo(project_id)
    from ai import story_memory
    return story_memory.snapshot(db, project_id)


@router.get("/magi/status")
def magi_status():
    """Read-only local Magi v3 availability. Does not load or download a model."""
    from ai import magi
    return magi.status()


@router.post("/magi/install")
def install_magi():
    """Install the official Magi v3 checkpoint when the user requests it."""
    from ai import magi
    try:
        return magi.install()
    except Exception as exc:
        raise HTTPException(500, f"Could not install Magi v3: {exc}")


def _model_for_provider(db: Database, provider: str, kind: str) -> str:
    """The model id a provider uses for a task. kind: 'translate'|'refine'|'narrate'
    (narrate = the vision model)."""
    if provider == "nvidia":
        if kind == "narrate":
            return db.get_setting("nvidia_vision_model", "") or config.NVIDIA_VISION_MODEL
        return db.get_setting(f"nvidia_{kind}_model", "") or config.NVIDIA_MODEL
    if provider == "gemini":
        return (db.get_setting("gemini_vision_model", "") or "gemini-3.5-flash-lite") if kind == "narrate" else ""
    if provider == "groq":
        return db.get_setting("groq_model", "") or ""
    if provider == "lm_studio":
        return db.get_setting("lm_studio_model", "")
    return ""


@router.get("/narrate-plan")
def narrate_plan(db: Database = Depends(get_db)):
    """How the Recap narration will batch given the current model settings — the
    UI uses effective_batch to slice crops so it auto-adapts to each model
    (a 1-image model drops to 1; a capable model honours your preference)."""
    from ai import model_caps
    vprovider = db.get_setting("ai_provider_narrate", "nvidia")
    tprovider = db.get_setting("ai_provider_refine", "nvidia")
    vmodel = _model_for_provider(db, vprovider, "narrate")
    rmodel = _model_for_provider(db, tprovider, "refine")
    try:    pref = int(db.get_setting("recap_batch_size", "2"))
    except (TypeError, ValueError): pref = 2
    vcap = model_caps.caps_for(vmodel, vision=True)
    rcap = model_caps.caps_for(rmodel, vision=False)
    return {
        "effective_batch":   model_caps.effective_batch(pref, vmodel),
        "preferred_batch":   pref,
        "vision_provider":   vprovider, "vision_model": vmodel,
        "vision_max_images": vcap["max_images"],
        "reasoner_provider": tprovider, "reasoner_model": rmodel,
        "reasoner_max_output": rcap["max_output"],
    }


class NarrateRequest(BaseModel):
    project_id:   int
    prompt:       str = ""          # narration style instructions
    images:       List[dict] = []   # [{index:int, data:str(b64 or data-url)}]
    reset_memory: bool = False      # start a fresh chapter (keeps confirmed memory)
    use_magi:      bool = False     # opt-in local visual grounding


@router.post("/narrate")
def narrate_panels(req: NarrateRequest, db: Database = Depends(get_db)):
    """ONE batch of cropped panels → narration lines, via the stateful two-call
    storytelling pipeline (see ai/recap_narrator.py):

        Call 1  VISION           → honest per-panel facts (no name guessing).
        Call 2  RESOLVE+NARRATE  → identity-aware narration + registry/memory update.

    The registry + rolling memory persist in recap_state.json and carry forward
    across batches (and chapters), so the narration knows names, relationships,
    and appearance changes instead of reading each panel in isolation."""
    import time
    from ai import recap_narrator
    t0 = time.time()
    log_lines = []

    # Create the log row up front and STREAM every line into it as it happens, so
    # the Logs page shows real-time progress instead of one dump at the end.
    log_id = db.log_adhoc_start("recap_narrate", "Recap")

    def _log(msg, level="info"):
        log_lines.append({"timestamp": time.time(), "level": level, "message": msg})
        db.log_adhoc_update(log_id, "running", log_lines)

    def _finish(status, error=""):
        db.log_adhoc_update(log_id, status, log_lines, error)

    if not req.images:
        _log("No images in batch", "error"); _finish("failed", "No images in batch")
        raise HTTPException(400, "No images in batch")

    vprovider = db.get_setting("ai_provider_narrate", "nvidia")   # VISION (call 1)
    tprovider = db.get_setting("ai_provider_refine", "nvidia")    # TEXT reasoning (call 2)
    nvidia_key = db.get_setting("nvidia_api_key", "")
    gemini_key = db.get_setting("gemini_api_key", "")
    vmodel = _model_for_provider(db, vprovider, "narrate")
    rmodel = _model_for_provider(db, tprovider, "refine")
    lm_model = db.get_setting("lm_studio_model", "")
    try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
    except (TypeError, ValueError): ctx = 32768

    b64s = [(img.get("data") or "").split(",", 1)[-1] for img in req.images]
    idxs = [int(img.get("index", i)) for i, img in enumerate(req.images)]
    style = (req.prompt or "").strip() or (
        "Engaging, dramatic, CONCISE third-person recap narration — present tense, punchy, "
        "one or two short sentences per beat, no filler.")

    # Defensive: never send more images than the vision model accepts.
    from ai import model_caps
    vmax = model_caps.caps_for(vmodel, vision=True)["max_images"] or 1
    if len(b64s) > vmax:
        _log(f"batch of {len(b64s)} exceeds {vmodel}'s {vmax}-image limit", "error")
        _finish("failed", f"{vmodel} accepts at most {vmax} image(s) per call — lower the batch size.")
        raise HTTPException(400, f"{vmodel} accepts at most {vmax} image(s) per call.")
    # The storyteller's reply can be large (narration + registry + memory); size
    # max_tokens to what the reasoner model actually allows so it never truncates.
    rmax = model_caps.output_cap(rmodel, vision=False, want=16000)

    state = _load_recap_state(req.project_id)
    if req.reset_memory:
        state["memory"] = ""
        _log("story memory reset for a new chapter (confirmed memory kept)", "muted")

    _log(f"▶ Batch of {len(b64s)} panel(s) — vision {vmodel} ({vprovider}), story {rmodel} ({tprovider}, out≤{rmax})", "accent")
    try:
        _log(f"vision: sending {len(b64s)} panel(s) to {vmodel} ({vprovider}) — analysing…", "muted")
        facts = recap_narrator.extract_facts(
            b64s, vprovider, nvidia_key=nvidia_key, nvidia_vision_model=vmodel,
            gemini_key=gemini_key, gemini_vision_model=vmodel, log=_log)
        _log(f"vision: extracted facts for {len(facts)} panel(s)", "muted")
        if req.use_magi:
            _add_magi_visual_evidence(b64s, facts, _log)
            _log("Magi v3: attached character, text, tail and dialogue-link evidence", "muted")
        _log(f"story: {rmodel} ({tprovider}) resolving identities & narrating {len(idxs)} panel(s) — this can take a while…", "muted")
        result = recap_narrator.narrate_batch(
            facts, idxs, style, state,
            provider=tprovider, nvidia_key=nvidia_key, lm_model=lm_model,
            context_length=ctx, log=_log, max_tokens=rmax)
    except Exception as exc:
        _log(f"Narration failed: {exc}", "error"); _finish("failed", str(exc))
        raise HTTPException(502, f"Narration failed: {exc}")

    _save_recap_state(req.project_id, state)
    n = sum(1 for l in result["lines"] if l["text"])
    _log(f"✓ Narrated {n}/{len(idxs)} panel(s)", "success")
    _finish("done")
    return {"lines": result["lines"],
            "registry": state.get("registry", {"characters": []}),
            "memory": state.get("memory", "")}


# The vision provider rejects requests whose total payload exceeds ~25 MB. Cropped
# panels arrive as full-res PNGs (often 4x-upscaled) — far more than a vision model
# needs to READ a panel — so we downscale + recompress to JPEG before sending, and
# also cap each chunk by cumulative bytes (not just image count) as a hard backstop.
_VISION_MAX_DIM   = 1536          # px on the long side — keeps panel text legible
_VISION_JPEG_Q    = 85
_VISION_BYTE_CAP  = 22_000_000    # per-chunk base64 budget, under the 25 MB limit


def _shrink_b64_for_vision(b64: str) -> str:
    """Decode a panel, downscale to _VISION_MAX_DIM on the long side, re-encode as
    JPEG. Falls back to the original string if anything goes wrong."""
    import base64, io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        w, h = im.size
        if max(w, h) > _VISION_MAX_DIM:
            scale = _VISION_MAX_DIM / float(max(w, h))
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=_VISION_JPEG_Q)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return b64


def _add_magi_visual_evidence(b64s: list[str], facts: list[dict], log) -> None:
    """Attach Magi v3 grounding to LLM facts without making it a fake ReID system.

    Magi's character clusters and dialogue links are evidence within each source
    image.  The later story-bible pass combines that evidence with dialogue,
    user corrections and the canonical story ledger for cross-chapter identity.
    """
    from io import BytesIO
    from PIL import Image
    from ai import magi

    if not magi.is_installed():
        raise RuntimeError("Magi v3 is not installed. Install it from the Recap narration window, or turn off Magi visual grounding.")
    images = []
    for b64 in b64s:
        try:
            images.append(Image.open(BytesIO(base64.b64decode(b64))).convert("RGB"))
        except Exception as exc:
            raise RuntimeError(f"Could not prepare a panel for Magi v3: {exc}") from exc
    # Four images keeps the 0.8B local model inside a comfortable MPS/CPU memory
    # envelope while still preserving every panel's grounding data.
    evidence = []
    for start in range(0, len(images), 4):
        end = min(len(images), start + 4)
        log(f"Magi v3: grounding panel(s) {start + 1}–{end}…", "muted")
        evidence.extend(magi.analyse(images[start:end]))
    for fact, item in zip(facts, evidence):
        if isinstance(fact, dict):
            fact["magi_visual_evidence"] = item


def _vision_chunks(b64s: list, idxs: list, vmax: int):
    """Group panels into vision calls bounded by BOTH the model's image limit and
    the provider's payload-size limit. Yields (chunk_b64s, chunk_idxs)."""
    cur_b, cur_i, cur_bytes = [], [], 0
    for b, ix in zip(b64s, idxs):
        sz = len(b)
        if cur_b and (len(cur_b) >= vmax or cur_bytes + sz > _VISION_BYTE_CAP):
            yield cur_b, cur_i
            cur_b, cur_i, cur_bytes = [], [], 0
        cur_b.append(b); cur_i.append(ix); cur_bytes += sz
    if cur_b:
        yield cur_b, cur_i


@router.post("/narrate-all")
def narrate_all_panels(req: NarrateRequest, db: Database = Depends(get_db)):
    """WHOLE-CHAPTER narration — the identity-aware pipeline (see recap_narrator):

        Phase 1  VISION (chunked)   → rich per-panel facts for EVERY panel.
        Phase 2a UNDERSTAND         → one text pass reads all facts → identity map
                                      + arc outline (full-chapter hindsight).
        Phase 2b NARRATE (windowed) → every panel gets one tight line, using the
                                      identity map + a rolling compressing summary.

    Unlike /narrate (forward-only 2-3 panel batches), this sees the whole chapter
    before writing, so a character revealed late is named from their first panel,
    and the summary carries the thread across windows and into the next chapter."""
    import time
    from ai import recap_narrator, model_caps
    log_lines = []
    log_id = db.log_adhoc_start("recap_narrate", "Recap")

    def _log(msg, level="info"):
        log_lines.append({"timestamp": time.time(), "level": level, "message": msg})
        db.log_adhoc_update(log_id, "running", log_lines)

    def _finish(status, error=""):
        db.log_adhoc_update(log_id, status, log_lines, error)

    if not req.images:
        _log("No panels to narrate", "error"); _finish("failed", "No images in batch")
        raise HTTPException(400, "No images in batch")

    vprovider = db.get_setting("ai_provider_narrate", "nvidia")   # VISION
    tprovider = db.get_setting("ai_provider_refine", "nvidia")    # TEXT reasoning
    nvidia_key = db.get_setting("nvidia_api_key", "")
    gemini_key = db.get_setting("gemini_api_key", "")
    vmodel = _model_for_provider(db, vprovider, "narrate")
    rmodel = _model_for_provider(db, tprovider, "refine")
    lm_model = db.get_setting("lm_studio_model", "")
    try:    ctx = int(db.get_setting("lm_studio_context_length", "32768"))
    except (TypeError, ValueError): ctx = 32768

    b64s = [(img.get("data") or "").split(",", 1)[-1] for img in req.images]
    idxs = [int(img.get("index", i)) for i, img in enumerate(req.images)]
    style = (req.prompt or "").strip() or (
        "Engaging, dramatic, CONCISE third-person recap narration — present tense, punchy, "
        "one or two short sentences per beat, no filler.")

    # Vision chunk size = the user's "Recap panels per call" setting, capped to the
    # model's real image limit. Smaller chunks read each panel more carefully
    # (less hallucination); the story pass still sees the whole chapter as text.
    model_max = model_caps.caps_for(vmodel, vision=True)["max_images"] or 1
    try:    pref = int(db.get_setting("recap_batch_size", "2"))
    except (TypeError, ValueError): pref = 2
    vmax = max(1, min(pref, model_max))
    rmax = model_caps.output_cap(rmodel, vision=False, want=16000)

    state = _load_recap_state(req.project_id)
    if req.reset_memory:
        state["memory"] = ""
        _log("story memory reset for a new chapter (confirmed memory kept)", "muted")

    _log(f"▶ Whole-chapter recap of {len(b64s)} panel(s) — vision {vmodel} ({vprovider}, "
         f"{vmax}/call of a {model_max}-image limit), story {rmodel} ({tprovider}, out≤{rmax})", "accent")
    try:
        # ── Phase 1: VISION in chunks — gather rich facts for every panel ──
        # Downscale panels for the vision model (keeps requests under the payload
        # limit and is faster), then chunk by BOTH image count and byte size.
        _log("vision: preparing panel images (downscaling for the vision model)…", "muted")
        b64s = [_shrink_b64_for_vision(b) for b in b64s]
        chunks = list(_vision_chunks(b64s, idxs, vmax))
        facts_by_idx: dict = {}
        for ci, (chunk, chunk_ids) in enumerate(chunks, 1):
            mb = sum(len(b) for b in chunk) / 1_000_000
            _log(f"vision: chunk {ci}/{len(chunks)} — reading panel(s) "
                 f"{chunk_ids[0]}–{chunk_ids[-1]} ({len(chunk)} img, ~{mb:.1f} MB)…", "muted")
            cf = recap_narrator.extract_facts(
                chunk, vprovider, nvidia_key=nvidia_key, nvidia_vision_model=vmodel,
                gemini_key=gemini_key, gemini_vision_model=vmodel, log=_log)
            for ix, f in zip(chunk_ids, cf):
                facts_by_idx[ix] = f
        facts = [facts_by_idx.get(ix, {}) for ix in idxs]
        _log(f"vision: gathered facts for {len(facts)} panel(s)", "muted")
        if req.use_magi:
            _add_magi_visual_evidence(b64s, facts, _log)
            _log("Magi v3: attached character, text, tail and dialogue-link evidence", "muted")

        # ── Phase 2a: UNDERSTAND — read everything, resolve identity w/ hindsight ──
        from ai import story_memory
        retrieved = story_memory.retrieve_context(db, req.project_id, facts, state.get("registry", {}))
        _log("story memory: retrieved a bounded set of prior identity evidence", "muted")
        _log(f"story: {rmodel} reading the whole chapter to resolve identities…", "muted")
        bible = recap_narrator.build_story_bible(
            facts, idxs, state, provider=tprovider, nvidia_key=nvidia_key,
            lm_model=lm_model, context_length=ctx, log=_log, retrieved_memory=retrieved)
        tag_to_id = story_memory.record_chapter_identities(
            db, req.project_id, facts, idxs, bible, state.get("registry", {}))

        # ── Phase 2b: NARRATE — windowed, every panel a tight name-aware line ──
        result = recap_narrator.narrate_all(
            facts, idxs, style, state, provider=tprovider, nvidia_key=nvidia_key,
            lm_model=lm_model, context_length=ctx, log=_log, max_tokens=rmax, bible=bible)
        story_memory.record_narrated_events(db, req.project_id, result["lines"], facts, idxs, tag_to_id)
    except Exception as exc:
        _log(f"Narration failed: {exc}", "error"); _finish("failed", str(exc))
        raise HTTPException(502, f"Narration failed: {exc}")

    _save_recap_state(req.project_id, state)
    n = sum(1 for l in result["lines"] if l["text"])
    _log(f"✓ Narrated {n}/{len(idxs)} panel(s)", "success")
    _finish("done")
    return {"lines": result["lines"], "memory": state.get("memory", "")}


@router.get("/session/{project_id}")
def get_session(project_id: int, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    p = _session_path(project_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.put("/session/{project_id}")
def put_session(project_id: int, body: dict, db: Database = Depends(get_db)):
    if not db.get_project(project_id):
        raise HTTPException(404, f"Project {project_id} not found")
    p = _session_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"Could not save session: {exc}")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# PDF → page images (for the crop tool)
# ─────────────────────────────────────────────────────────────────────────────

class PdfRequest(BaseModel):
    project_id: int
    pdf_path:   str
    dpi:        int = 130       # viewing DPI — kept modest for speed/RAM; the crop
                               # is 4× upscaled anyway, so final quality stays high


@router.post("/pdf")
def render_pdf(req: PdfRequest, db: Database = Depends(get_db)):
    if not db.get_project(req.project_id):
        raise HTTPException(404, f"Project {req.project_id} not found")
    src = Path(req.pdf_path).expanduser()
    if not src.is_file():
        raise HTTPException(404, f"PDF not found: {src}")
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise HTTPException(500, "pdf2image is not installed (pip install pdf2image + poppler).")

    pages_dir = _project_dir(req.project_id) / "refine" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    dpi = max(72, min(int(req.dpi or 200), 400))
    try:
        images = convert_from_path(str(src), dpi=dpi, fmt="png")
    except Exception as exc:
        raise HTTPException(500, f"Could not render PDF (is poppler installed?): {exc}")

    pages = []
    for i, img in enumerate(images):
        out = pages_dir / f"page_{i:04d}.png"
        img.save(str(out), "PNG")
        pages.append({"index": i, "url": _files_url(out), "w": img.width, "h": img.height})
    return {"count": len(pages), "dpi": dpi, "pdf_path": str(src), "pages": pages}


# ─────────────────────────────────────────────────────────────────────────────
# Crop a panel (+ upscale) and a video frame
# ─────────────────────────────────────────────────────────────────────────────

class CropBox(BaseModel):
    x: float; y: float; w: float; h: float       # normalised 0..1 of the page


class CropRequest(BaseModel):
    project_id: int
    page:       int
    box:        CropBox
    cue_index:  int
    upscale:    bool = True


def _get_upsampler(db: Database, _log):
    global _upsampler
    with _upsampler_lock:
        if _upsampler is None:
            from image_upscaler import ImageUpscaler
            inst = ImageUpscaler(db, str(config.OUTPUT_DIR), on_log=_log)
            _upsampler = inst._load_realesrgan_model(_MODEL_PATH)
            _upsampler._ms_helper = inst       # keep the helper for _upscale_one_image
    return _upsampler


@router.post("/crop")
def crop_panel(req: CropRequest, db: Database = Depends(get_db)):
    from PIL import Image

    page_png = _project_dir(req.project_id) / "refine" / "pages" / f"page_{req.page:04d}.png"
    if not page_png.exists():
        raise HTTPException(404, f"Page {req.page} not rendered — load the PDF first.")

    panels = _project_dir(req.project_id) / "panels"
    panels.mkdir(parents=True, exist_ok=True)
    raw_path = panels / f"cue_{req.cue_index:04d}.png"

    try:
        img = Image.open(page_png).convert("RGB")
        W, H = img.size
        b = req.box
        left   = max(0, int(b.x * W));            top    = max(0, int(b.y * H))
        right  = min(W, int((b.x + b.w) * W));     bottom = min(H, int((b.y + b.h) * H))
        if right - left < 4 or bottom - top < 4:
            raise HTTPException(400, "Crop box is too small.")
        img.crop((left, top, right, bottom)).save(str(raw_path), "PNG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Crop failed: {exc}")

    # Upscale in the BACKGROUND so the crop returns instantly (attach feels fast).
    # The upscaled file is saved for the final render; the editor shows the raw crop.
    if req.upscale:
        up_path = panels / f"cue_{req.cue_index:04d}_up.jpg"
        threading.Thread(target=_bg_upscale, args=(db, raw_path, up_path), daemon=True).start()

    return {
        "ok": True,
        "raw": _files_url(raw_path),
        "image": _files_url(raw_path),     # show the raw crop immediately
        "upscaled": False,                 # upscale runs in the background
        "page": req.page,
        "box": req.box.model_dump(),
    }


def _bg_upscale(db: Database, raw_path: Path, up_path: Path):
    try:
        up = _get_upsampler(db, lambda *a, **k: None)
        up._ms_helper._upscale_one_image(up, raw_path, up_path)
    except Exception as exc:
        print(f"[video-refine] background upscale failed: {exc}")


class SavePanelRequest(BaseModel):
    project_id: int
    cue_index:  int
    data:       str          # PNG data-URL (or bare base64) from the browser crop


@router.post("/save-panel")
def save_panel(req: SavePanelRequest, db: Database = Depends(get_db)):
    """Save a panel cropped CLIENT-SIDE (PDF.js canvas). No upscale here — that's
    a deferred batch step. Fast: just decode + write."""
    import base64
    if not db.get_project(req.project_id):
        raise HTTPException(404, f"Project {req.project_id} not found")
    panels = _project_dir(req.project_id) / "panels"
    panels.mkdir(parents=True, exist_ok=True)
    raw = panels / f"cue_{req.cue_index:04d}.png"
    d = req.data.split(",", 1)[1] if "," in req.data else req.data
    try:
        raw.write_bytes(base64.b64decode(d))
    except Exception as exc:
        raise HTTPException(400, f"Bad image data: {exc}")
    # Re-cropping invalidates any previous upscale of this cue — delete it so
    # "Upscale all" regenerates from the LATEST saved crop (it skips panels that
    # already have an _up file, which would otherwise export the stale image).
    for stale in (panels / f"cue_{req.cue_index:04d}_up.jpg",
                  panels / f"cue_{req.cue_index:04d}_up.png"):
        try:
            if stale.exists(): stale.unlink()
        except OSError:
            pass
    return {"ok": True, "image": _files_url(raw), "raw": _files_url(raw)}


class PanelRef(BaseModel):
    project_id: int
    cue_index:  int


@router.post("/delete-panel")
def delete_panel(req: PanelRef, db: Database = Depends(get_db)):
    """Remove a cue's panel files (raw + any upscaled) so a later 'Upscale all'
    never touches a panel the user discarded."""
    panels = _project_dir(req.project_id) / "panels"
    removed = 0
    for pat in (f"cue_{req.cue_index:04d}.png", f"cue_{req.cue_index:04d}.jpg",
                f"cue_{req.cue_index:04d}_up.jpg", f"cue_{req.cue_index:04d}_up.png"):
        f = panels / pat
        try:
            if f.exists(): f.unlink(); removed += 1
        except OSError:
            pass
    return {"ok": True, "removed": removed}


# Batch upscale (deferred until the user is done cropping) ────────────────────
_upscale_jobs: dict = {}      # project_id -> {"total": n, "done": n, "running": bool}


@router.post("/upscale-all")
def upscale_all(req: dict, db: Database = Depends(get_db)):
    """Upscale every cropped panel that doesn't yet have an `_up` version.
    Runs in the background; poll /upscale-status/{project_id}."""
    pid = int(req.get("project_id"))
    panels = _project_dir(pid) / "panels"
    todo = []
    if panels.exists():
        for raw in sorted(panels.glob("cue_*.png")):
            if raw.stem.endswith("_up"):
                continue
            up = panels / f"{raw.stem}_up.jpg"
            if not up.exists():
                todo.append((raw, up))
    if not todo:
        _upscale_jobs[pid] = {"total": 0, "done": 0, "running": False}
        return {"ok": True, "total": 0}
    _upscale_jobs[pid] = {"total": len(todo), "done": 0, "running": True}

    def _run():
        for raw, up in todo:
            _bg_upscale(db, raw, up)
            _upscale_jobs[pid]["done"] += 1
        _upscale_jobs[pid]["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "total": len(todo)}


@router.get("/upscale-status/{project_id}")
def upscale_status(project_id: int):
    return _upscale_jobs.get(project_id, {"total": 0, "done": 0, "running": False})


class FrameRequest(BaseModel):
    project_id:  int
    source_path: str
    time:        float
    cue_index:   int


@router.post("/frame")
def grab_frame(req: FrameRequest, db: Database = Depends(get_db)):
    src = Path(req.source_path).expanduser()
    if not src.is_file():
        raise HTTPException(404, f"Source video not found: {src}")
    frames = _project_dir(req.project_id) / "refine" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    out = frames / f"cue_{req.cue_index:04d}.jpg"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, req.time):.3f}", "-i", str(src),
             "-frames:v", "1", "-q:v", "5", str(out)],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        raise HTTPException(500, f"ffmpeg error: {exc}")
    if r.returncode != 0 or not out.exists():
        raise HTTPException(500, f"Frame grab failed: {(r.stderr or '')[-300:]}")
    return {"ok": True, "image": _files_url(out)}
