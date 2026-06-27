"""
scripts/api/models.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Pydantic models for every API request body and response shape.

Single source of truth for what the React UI sends and receives.
All routers import from here — no inline schema definitions anywhere else.

Sections
────────
  Shared       — enums and generic responses used across domains
  Project      — ProjectCreate / ProjectResponse
  Episode      — EpisodeCreate / EpisodeUpdate / EpisodeResponse
  Pipeline     — PipelineRunRequest / PanelResponse
"""

from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


# ── Shared ────────────────────────────────────────────────────────────────────

class OkResponse(BaseModel):
    """Generic success response for operations that don't return data."""
    ok:      bool
    message: str


class StageInfo(BaseModel):
    """Status and progress for a single pipeline stage."""
    status:   str  # "pending" | "running" | "done" | "failed" | "skipped"
    progress: int  # 0–100


# ── Project ───────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    """Payload for POST /api/projects."""
    title: str = Field(..., min_length=1, max_length=200, description="Project name")
    notes: str = Field(default="", description="Optional notes")


class ProjectResponse(BaseModel):
    """
    Full project record returned to the UI.
    episode_count is computed at response time — not stored in the DB.
    """
    id:            int
    title:         str
    notes:         str
    cover_path:    str
    episode_count: int
    created_at:    float
    updated_at:    float


# ── Episode ───────────────────────────────────────────────────────────────────

class EpisodeCreate(BaseModel):
    """Payload for POST /api/episodes."""
    project_id:  int
    title:       str = Field(..., min_length=1, max_length=200)
    source_type: str = Field(..., description='"video" | "pdf" | "screenshots"')
    source_path: str = Field(..., min_length=1, description="Absolute path to source file")
    tone_prompt: str = Field(default="", description="Narration style / tone instructions")


class EpisodeUpdate(BaseModel):
    """
    Payload for PATCH /api/episodes/{id}.
    All fields optional — only supplied fields are updated.
    """
    title:       Optional[str] = Field(default=None, min_length=1, max_length=200)
    tone_prompt: Optional[str] = None


class EpisodeResponse(BaseModel):
    """
    Full episode record returned to the UI.

    stages      — dict keyed by stage name with status + progress per stage.
                  Keys: detect, extract, screenshot, upscale, narrate,
                        translate, dub, sync, assemble
    overall     — 0–100 average across all active (non-skipped) stages,
                  computed by pipeline_logic.overall_progress()
    """
    id:            int
    project_id:    int
    title:         str
    source_type:   str
    source_path:   str
    output_folder: str
    tone_prompt:   str
    stages:        Dict[str, StageInfo]
    overall:       int
    total_panels:  int
    duration_secs: Optional[float]
    total_pages:   Optional[int]
    error_message: str
    created_at:    float
    updated_at:    float


# ── Pipeline ──────────────────────────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    """Payload for POST /api/pipeline/run."""
    episode_id: int = Field(..., description="ID of the episode to process")
    stage:      str = Field(
        ...,
        description=(
            "Stage to run. One of: detect, video_refine, pdf_slice, "
            "pdf_narrate, upscale, translate, dub, sync, assemble"
        ),
    )


class PanelResponse(BaseModel):
    """
    Single panel record for the Pipeline panel table.

    image_path is the absolute on-disk path to the panel image.
    The Pipeline page uses this to display panel thumbnails.
    """
    id:              int
    episode_id:      int
    panel_index:     int
    transcript_text: str   # raw Whisper output
    narration_text:  str   # AI-refined text (source for translate / TTS)
    image_path:      str   # absolute path on disk
    updated_at:      float


# ── Review checkpoint (panels editor) ──────────────────────────────────────────

class PanelTranslation(BaseModel):
    """Per-language translation + audio state for one panel."""
    lang_code:  str
    translated_text: str = ""
    has_audio:  bool = False     # True once raw_wav exists
    is_synced:  bool = False     # True once synced_wav exists
    audio_url:  str = ""         # /files URL for the generated clip (empty if none)
    synced_url: str = ""         # /files URL for the synced clip (empty if none)
    raw_duration:    Optional[float] = None   # generated clip length (s)
    synced_duration: Optional[float] = None   # length after sync stretch (s)


class ReviewPanel(BaseModel):
    """
    Full panel row for the Review/Script-editor checkpoint.

    Extends PanelResponse with:
      • thumbnail_url   — a /files/... URL the browser can load directly
                          (relative to config.OUTPUT_DIR; empty if no image).
      • timing          — start/end/duration seconds (video source only).
      • translations    — per-language translated text + audio status,
                          keyed by lang_code.
    """
    id:              int
    episode_id:      int
    panel_index:     int
    transcript_text: str
    narration_text:  str
    narration_status: str
    image_path:      str
    thumbnail_url:   str
    start_time_sec:  Optional[float] = None
    end_time_sec:    Optional[float] = None
    duration_sec:    Optional[float] = None
    translations:    Dict[str, PanelTranslation] = Field(default_factory=dict)
    updated_at:      float


class PanelUpdate(BaseModel):
    """
    Payload for PATCH /api/panels/{panel_id}.

    All fields optional — supply only what changed:
      • narration_text   — edits the master narration.  Changing it cascades:
                           downstream translations + audio for every (or the
                           supplied) language are invalidated.
      • translated_text  — edits one language's translation (requires lang_code).
      • lang_code        — which language translated_text applies to.
    """
    narration_text:  Optional[str] = None
    translated_text: Optional[str] = None
    lang_code:       Optional[str] = None


class PanelUpdateResult(BaseModel):
    """Response from PATCH /api/panels/{panel_id} — the fresh row + what changed."""
    panel:                ReviewPanel
    invalidated_langs:    List[str] = Field(default_factory=list)
    translations_cleared: int = 0
    audio_deleted:        int = 0
    sync_deleted:         int = 0


# ── Dubbing configuration + voices ─────────────────────────────────────────────

class VoiceInfo(BaseModel):
    """One voice profile from VoiceProfileManager (GET /api/voices)."""
    name:     str
    mode:     str = ""        # CustomVoice | VoiceDesign | VoiceClone
    language: str = ""
    model:    str = ""
    speaker:  str = ""


class VoiceProfileDetail(BaseModel):
    """Full voice profile (GET/POST/PATCH /api/voices/{name})."""
    name:               str
    mode:               str = "CustomVoice"
    model:              str = "1.7B-CustomVoice"
    language:           str = "English"
    speaker:            str = "Aiden"
    instruct:           str = ""
    ref_wav_path:       str = ""
    ref_wav_text:       str = ""
    x_vector_only:      bool = True
    temperature:        float = 0.7
    top_p:              float = 1.0
    top_k:              int = 50
    repetition_penalty: float = 1.1
    max_new_tokens:     int = 2048
    seed:               int = -1


class VoiceProfileUpsert(BaseModel):
    """Create/update body — only `name` is required; rest take profile defaults."""
    name:               str
    mode:               Optional[str] = None
    model:              Optional[str] = None
    language:           Optional[str] = None
    speaker:            Optional[str] = None
    instruct:           Optional[str] = None
    ref_wav_path:       Optional[str] = None
    ref_wav_text:       Optional[str] = None
    x_vector_only:      Optional[bool] = None
    temperature:        Optional[float] = None
    top_p:              Optional[float] = None
    top_k:              Optional[int] = None
    repetition_penalty: Optional[float] = None
    max_new_tokens:     Optional[int] = None
    seed:               Optional[int] = None


class VoiceReferenceRequest(BaseModel):
    """Attach a reference clip to a voice (local file path) + auto-transcribe."""
    source_path: str
    transcribe:  bool = True


class QuickTTSRequest(BaseModel):
    text:  str
    voice: str                       # voice profile name
    language: Optional[str] = None   # override the profile's language
    project_id: Optional[int] = None # route output into the project's dub folder
    lang_code:  Optional[str] = None # subfolder name for the project's dub output


class VoiceDesignRequest(BaseModel):
    """Synthesize a reference clip from a text persona (Qwen3 VoiceDesign)."""
    instruct: str                    # the persona description
    text:     str                    # what the sample clip should say
    language: str = "English"


class AdhocDubRequest(BaseModel):
    """Dub a free-form multi-line script with one voice (active engine)."""
    text:     str
    voice:    str
    language: Optional[str] = None


class QuickTTSJob(BaseModel):
    job_id:  str
    status:  str = "running"         # running | done | failed
    message: str = ""
    audio_url: str = ""
    path:    str = ""                # absolute server path (for save-as-voice)
    error:   str = ""


class DubLangOption(BaseModel):
    """A language the episode can be dubbed into."""
    code:           str
    name:           str
    has_translation: bool = False   # at least one panel has translated text
    has_continuous:  bool = False   # Dub finished — continuous wav exists
    continuous_url:  str  = ""      # /files URL to play the continuous clip


class DubConfigResponse(BaseModel):
    """
    Dubbing configuration for an episode (GET /api/dub/config/{id}).

    enabled_langs  — languages ticked for dubbing.  Persisted in setting
                     dub_enabled_langs_{episode_id}; empty list means "all
                     translated languages" (matches dub_stage behaviour).
    profiles       — {lang_code: voice_profile_name}, from dub_profiles_{id}.
    suggested      — naming-convention auto-assignment ({lang: profile})
                     for languages without an explicit profile yet.
    voices         — every available voice profile name.
    batch_size     — global dub_batch_size setting.
    """
    episode_id:  int
    languages:   List[DubLangOption]
    enabled_langs: List[str]
    profiles:    Dict[str, str]
    suggested:   Dict[str, str]
    voices:      List[str]
    batch_size:  int


class DubConfigUpdate(BaseModel):
    """
    Payload for PATCH /api/dub/config/{id}.  All fields optional — only the
    supplied ones are written.
    """
    enabled_langs: Optional[List[str]]      = None
    profiles:      Optional[Dict[str, str]] = None
    batch_size:    Optional[int]            = Field(default=None, ge=1, le=64)


# ── Dub / Sync batch playback ───────────────────────────────────────────────────

class DubBatch(BaseModel):
    """
    One generated dub batch for a language (from dub/batch_state.json).
    Each batch is an independently-playable, independently-regenerable unit
    covering a contiguous panel range — so a single bad batch can be pinpointed
    by ear and re-run without touching the others.
    """
    idx:         int
    panel_from:  int            # 0-based panel index (inclusive)
    panel_to:    int            # 0-based panel index (inclusive)
    panels:      List[int] = []
    status:      str = "pending"   # done | pending | failed
    duration:    float = 0.0
    audio_url:   str = ""          # /files URL to play this batch's wav


class DubBatchesResponse(BaseModel):
    episode_id:  int
    lang:        str
    lang_name:   str = ""
    voice:       str = ""
    batch_size:  int = 5
    batches:     List[DubBatch] = []


class DubRegenBatchRequest(BaseModel):
    """Regenerate a single dub batch (heavy — runs in the background)."""
    lang:      str
    batch_idx: int


class DubFixRequest(BaseModel):
    """Fix rushed panels: re-translate shorter → re-dub → re-sync (best of N)."""
    lang:          str
    panel_indices: Optional[List[int]] = None   # omit → fix all rushed panels


class SyncBatch(BaseModel):
    """
    A dub batch's panel range, annotated with that range's sync state for one
    language.  Sync runs per-panel; this groups the per-panel synced clips back
    under their dub batch so the user reviews/re-syncs in the same unit they
    dubbed in.  `stretch_pct` is the average English/target stretch over the
    range; `synced_url` plays the first synced panel of the range.
    """
    idx:         int
    panel_from:  int
    panel_to:    int
    synced:      int = 0        # how many panels in the range are synced
    total:       int = 0        # panels in the range
    stretch_pct: Optional[int] = None
    status:      str = "pending"   # synced | partial | outdated | pending
    synced_url:  str = ""


class SyncBatchesResponse(BaseModel):
    episode_id:     int
    lang:           str
    lang_name:      str = ""
    is_reference:   bool = False   # True for English (timing reference, not stretched)
    full_audio_url: str = ""       # whole-language track (stitched if available, else continuous)
    full_is_synced: bool = False   # True when full_audio_url is the post-sync stitched track
    batches:        List[SyncBatch] = []


class SyncClearRangeRequest(BaseModel):
    lang:       str
    panel_from: int
    panel_to:   int


class SyncLangOption(BaseModel):
    code:         str
    name:         str
    synced_count: int = 0


class SyncConfigResponse(BaseModel):
    """Which dubbed languages can be synced + which are selected to sync."""
    episode_id:   int
    languages:    List[SyncLangOption] = []   # non-English languages with dub audio
    selected:     List[str] = []
    total_panels: int = 0


class SyncConfigUpdate(BaseModel):
    selected: List[str]


# ── Detect configuration + tuning ──────────────────────────────────────────────

class DetectConfig(BaseModel):
    """
    Detection settings for an episode (GET /api/detect/config/{id}).
    Mirrors the detect_* columns on the episodes table + the test-clip window.
    `defaults` carries config.py values so the UI can offer "reset to defaults"
    without a round-trip. `clip_ready`/`source_exists` gate the tune actions.
    """
    episode_id:       int
    mode:             str
    priority:         str
    silence_db:       float
    min_silence_sec:  float
    visual_threshold: float
    min_scene_sec:    float
    frame_skip:       int
    merge_window:     float
    workers:          int
    clip_start:       str
    clip_duration:    int
    confirmed:        bool
    clip_ready:       bool
    source_exists:    bool
    defaults:         Dict[str, float | int | str]


class DetectConfigUpdate(BaseModel):
    """PATCH /api/detect/config/{id} — all optional; saving clears `confirmed`."""
    mode:             Optional[str]   = None
    priority:         Optional[str]   = None
    silence_db:       Optional[float] = None
    min_silence_sec:  Optional[float] = None
    visual_threshold: Optional[float] = None
    min_scene_sec:    Optional[float] = None
    frame_skip:       Optional[int]   = None
    merge_window:     Optional[float] = None
    workers:          Optional[int]   = None
    clip_start:       Optional[str]   = None
    clip_duration:    Optional[int]   = None


class DetectClipRequest(BaseModel):
    """POST /api/detect/clip/{id} — extract a test clip."""
    start:    str = Field(default="00:00:00", description="HH:MM:SS or seconds")
    duration: int = Field(default=120, ge=1, le=3600)


class DetectCut(BaseModel):
    """One detected cut in a preview run."""
    panel_index:     int
    start_time_sec:  float
    end_time_sec:    float
    duration_sec:    float
    transcript_text: str = ""


class DetectPreviewResponse(BaseModel):
    """POST /api/detect/preview/{id} — cuts found on the test clip."""
    count:        int
    avg_duration: float
    cuts:         List[DetectCut]


# ── Translate configuration ────────────────────────────────────────────────────

class TranslateLangOption(BaseModel):
    code:            str
    name:            str
    translated_count: int = 0     # panels already translated for this language


class TranslateConfig(BaseModel):
    """GET /api/translate/config/{id}."""
    episode_id:     int
    total_panels:   int
    languages:      List[TranslateLangOption]   # all targets (excludes 'en')
    selected:       List[str]                    # languages chosen to translate


class TranslateConfigUpdate(BaseModel):
    """PATCH /api/translate/config/{id}."""
    selected: List[str]


# ── Logs ────────────────────────────────────────────────────────────────────

class LogLine(BaseModel):
    level:   str = "info"
    message: str = ""


class LogEntry(BaseModel):
    id:            int
    episode_id:    int
    episode_title: str = ""
    project_name:  str = ""
    stage:         str
    status:        str
    started_at:    float
    finished_at:   Optional[float] = None
    duration_secs: Optional[float] = None
    error:         str = ""
    # Full developer transcript captured during the run (model loading, batch
    # counts, per-language progress, errors) — shown when a Logs row is opened.
    log:           List[LogLine] = []

class AdhocTranslateRequest(BaseModel):
    """Payload for POST /api/speech/adhoc-translate."""
    source_path: str
    target_lang: str = "fr"

class AdhocTranslateJob(BaseModel):
    """Job status for ad-hoc transcription and translation."""
    job_id:  str
    status:  str = "running"         # running | done | failed
    message: str = ""
    cues:    List[dict] = []
    log:     List[dict] = []
    error:   str = ""

class AdhocSyncRequest(BaseModel):
    audio_url: str
    cues: List[dict]
    lang_code: str = "fr"
    project_id: Optional[int] = None   # route output into the project's folder
    lead_dummy: Optional[str] = None   # throwaway warm-up line at the read's start
                                       # (absorbs the TTS first-utterance hiccup);
                                       # its split piece is discarded.

class AdhocSyncJob(BaseModel):
    job_id: str
    status: str = "running"         # running | done | failed
    message: str = ""
    synced_audio_url: str = ""
    log: List[dict] = []
    error: str = ""

