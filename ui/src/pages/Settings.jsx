/**
 * ui/src/pages/Settings.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Full Settings screen.  Mirrors the six sections from the Tkinter
 * settings_tab.py exactly: API Keys, Slicer, Optimizer, Detection, TTS, Dubbing.
 *
 * Layout
 * ──────
 *   Left nav (200px)  — section buttons + Save All
 *   Right content     — scrollable form for the selected section
 *
 * Behaviour
 * ─────────
 *   Loads the full settings payload once on mount.
 *   Local edits are tracked separately from the saved server state ("dirty"),
 *   so unsaved changes are visually obvious before Save is clicked.
 *   Save sends only the changed keys via PATCH /settings.
 */

import { useState, useEffect, useCallback, useMemo } from "react"
import { getSettings, updateSettings, listProviderModels } from "../api/settings"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import { TextInput } from "../components/Modal"
import { useResizableRail, RailDragHandle } from "../components/Rail"

// ══════════════════════════════════════════════════════════════════════════════
// Field metadata — defines how each setting key renders.
// type: "text" | "password" | "number" | "select" | "boolean"
// ══════════════════════════════════════════════════════════════════════════════

// Which underlying setting key a task's model picker reads/writes, per provider.
// Mirrors scripts/api/routers/video_refine.py::_model_for_provider.
const TASK_MODEL_KEY = {
  nvidia: { translate: "nvidia_translate_model", refine: "nvidia_refine_model", vision: "nvidia_vision_model" },
  gemini: { vision: "gemini_vision_model" },
  groq:   { translate: "groq_model",   refine: "groq_model",   vision: "groq_model" },
}

const FIELDS = {
  // ── API Keys ────────────────────────────────────────────────────────────
  nvidia_api_key: {
    label: "NVIDIA API Key", type: "password",
    hint: "Get a key at build.nvidia.com → any model card → Get API Key",
  },
  gemini_api_key: {
    label: "Gemini API Key", type: "password",
    hint: "Create a Google AI Studio key. Gemini 3.5 Flash-Lite accepts images on the API free tier.",
  },
  ai_provider_narrate: {
    label: "Narration Provider (Recap vision)", type: "select",
    options: [["nvidia", "NVIDIA (cloud)"], ["gemini", "Google Gemini (AI Studio)"]],
    hint: "Turns cropped manga panels into narration (needs a vision model)",
  },
  // Task-scoped model pickers. Each is VIRTUAL: it follows its task's provider
  // select and reads/writes the right provider-specific setting key underneath
  // (see TASK_MODEL). The dropdown lists ONLY models curated for that task.
  translate_model: {
    label: "Translation Model", type: "taskmodel", task: "translate",
    providerKey: "ai_provider_translate",
    hint: "Multilingual models good at translation, from the selected provider — pick one or type any new id",
  },
  refine_model: {
    label: "Refine / Story Model", type: "taskmodel", task: "refine",
    providerKey: "ai_provider_refine",
    hint: "Strong writing & reasoning models (storytelling, script refine) — pick one or type any new id",
  },
  narrate_model: {
    label: "Narration (Vision) Model", type: "taskmodel", task: "vision",
    providerKey: "ai_provider_narrate",
    hint: "Only multimodal models that accept images are listed — pick one or type any new id",
  },
  groq_api_key: {
    label: "Groq API Key", type: "password",
    hint: "console.groq.com → API Keys",
  },
  recap_batch_size: {
    label: "Recap panels per call", type: "number", min: 1, max: 8,
    hint: "How many panels the Recap sends to the vision model at once (auto-capped to the model's real limit — a 1-image model drops to 1). Fewer = more detail per panel.",
  },
  nvidia_batch_size: {
    label: "NVIDIA Batch Size", type: "number", min: 1, max: 100,
  },
  nvidia_max_concurrent: {
    label: "NVIDIA Max Concurrent", type: "number", min: 1, max: 20,
  },
  // ── Slicer ──────────────────────────────────────────────────────────────
  narr_mode: {
    label: "Narration Slice Mode", type: "select",
    options: [["page", "Page"], ["slice", "Slice by height"], ["merge", "Merge pages"]],
  },
  narr_slice_height: { label: "Slice Height (px)",      type: "number", min: 200, max: 5000, step: 50 },
  narr_merge_count:  { label: "Pages per Merge",        type: "number", min: 1,   max: 10 },
  narr_images_per_batch: { label: "Images per Narration Batch", type: "number", min: 1, max: 10 },
  pdf_dpi:            { label: "PDF Rasterisation DPI", type: "number", min: 72, max: 600, hint: "Higher = sharper, slower" },
  pdf_skip_first_last:{ label: "Skip Cover + Back Page", type: "boolean" },
  pdf_jpeg_quality:   { label: "Raw Slice JPEG Quality", type: "number", min: 1, max: 100 },

  // ── Optimizer ───────────────────────────────────────────────────────────
  opt_compression_mode: {
    label: "Compression Mode", type: "select",
    options: [["quality", "Quality"], ["target_size", "Target Size"], ["aggressive", "Aggressive"]],
  },
  opt_jpeg_quality: { label: "JPEG Quality",          type: "number", min: 1, max: 100, hint: "85=high  75=balanced  65=lean  45=aggressive" },
  opt_target_kb:    { label: "Target Size (KB)",      type: "number", min: 10, max: 2000 },
  opt_min_quality:  { label: "Minimum Quality Floor", type: "number", min: 1, max: 100 },
  opt_max_width:    { label: "Max Width (px)",        type: "number", min: 200, max: 4000, step: 50 },
  opt_grayscale:    { label: "Grayscale",  type: "boolean", hint: "Saves 30–40% tokens, no narration quality loss" },
  opt_autocrop:     { label: "Auto-crop",  type: "boolean", hint: "Strip white border padding before resize" },
  opt_sharpen:      { label: "Sharpen",    type: "boolean", hint: "UnsharpMask pass after resize to keep text crisp" },

  // ── Detection ───────────────────────────────────────────────────────────
  detect_mode: {
    label: "Detection Mode", type: "select",
    options: [["combined", "Combined"], ["audio", "Audio only"], ["visual", "Visual only"]],
  },
  detect_silence_db:   { label: "Silence Threshold (dBFS)", type: "number", step: 0.5, hint: "Lower = stricter silence requirement" },
  detect_min_silence:  { label: "Min Silence Duration (s)", type: "number", step: 0.05, min: 0 },
  detect_threshold:    { label: "Visual Score Threshold",   type: "number", step: 0.1, hint: "Lower = more sensitive" },
  detect_min_scene:    { label: "Min Scene Gap (s)",        type: "number", step: 0.1, min: 0 },
  detect_frame_skip:   { label: "Frame Skip",               type: "number", min: 0, max: 10, hint: "Check every (N+1)th frame" },
  detect_merge_window: { label: "Merge Window (s)",         type: "number", step: 0.1, min: 0 },
  detect_priority: {
    label: "Detection Priority", type: "select",
    options: [["combined", "Combined"], ["visual_first", "Visual first"], ["audio_first", "Audio first"]],
  },
  detect_workers: { label: "Parallel ffmpeg Workers", type: "number", min: 1, max: 16 },
  whisper_model: {
    label: "Whisper Model (Transcription)", type: "select",
    options: [
      ["tiny.en", "tiny.en"], ["base.en", "base.en"], ["small.en", "small.en"],
      ["medium.en", "medium.en"], ["large-v3", "large-v3"],
    ],
  },
  screenshot_offset: { label: "Screenshot Offset (s)", type: "number", step: 0.1, min: 0, hint: "Seconds into each panel to grab the frame" },

  // ── AI providers (per-task) ───────────────────────────────────────────────
  ai_provider_translate: {
    label: "Translation Provider", type: "select",
    options: [["nvidia", "NVIDIA (cloud)"], ["groq", "Groq"]],
  },
  ai_provider_refine: {
    label: "Refine Provider", type: "select",
    options: [["nvidia", "NVIDIA (cloud)"], ["groq", "Groq"]],
  },

  // ── Voices & TTS ──────────────────────────────────────────────────────────
  // No engine selector — the engine is chosen automatically from each voice's
  // language (Qwen3 for the languages it covers, dots.tts for the rest).
  dots_weights_dir:    { label: "dots.tts Weights Dir", type: "text", hint: "e.g. ~/dots-tts-mlx-weights/int4 — used for languages Qwen3 doesn't cover (Hindi, Arabic, …)" },
  dots_num_steps:      { label: "dots.tts Steps",          type: "number", min: 1, max: 32, hint: "Blank = auto: 4 for MeanFlow (mf-*) weights, 10 for soar. Soar at 4 = gibberish." },
  dots_guidance_scale: { label: "dots.tts Guidance Scale", type: "number", step: 0.1, min: 0, max: 5 },
  dots_speaker_scale:  { label: "dots.tts Speaker Scale",  type: "number", step: 0.1, min: 0, max: 5, hint: "Higher = stronger voice match" },
  dots_seed:           { label: "dots.tts Seed",           type: "number", min: 0, hint: "Fixed seed = reproducible voice" },
  voice_ref_whisper_model: {
    label: "Reference Transcription Model", type: "select",
    options: [["tiny", "tiny"], ["base", "base"], ["small", "small"], ["medium", "medium"], ["large-v3", "large-v3"]],
    hint: "Used once per voice to auto-transcribe a reference clip (editable after)",
  },

  // ── TTS ─────────────────────────────────────────────────────────────────
  tts_recommended_voice_design: {
    label: "Recommended Model — Voice Design", type: "select",
    options: TTS_MODEL_OPTIONS(),
  },
  tts_recommended_voice_clone: {
    label: "Recommended Model — Voice Clone", type: "select",
    options: TTS_MODEL_OPTIONS(),
  },
  tts_recommended_custom_voice: {
    label: "Recommended Model — Custom Voice", type: "select",
    options: TTS_MODEL_OPTIONS(),
  },

  // ── Dubbing & Sync (quality knobs) ───────────────────────────────────────
  dub_max_stretch: {
    label: "Comfort Stretch (flag rushed)", type: "number", step: 0.05, min: 1, max: 3,
    hint: "Panels needing more speed-up than this are flagged/auto-fixed (1.20 ≈ 20%)",
  },
  dub_hard_stretch: {
    label: "Hard Stretch Cap (safety)", type: "number", step: 0.5, min: 1, max: 8,
    hint: "Absolute limit so audio always matches English length",
  },
  dub_mild_stretch: { label: "Mild Stretch Target", type: "number", step: 0.05, min: 1, max: 3 },
  dub_auto_fix_rushed: {
    label: "Auto-fix Rushed Panels", type: "boolean",
    hint: "On Sync, automatically re-translate→re-dub→re-sync panels longer than English",
  },
  dub_fix_attempts: { label: "Auto-fix Attempts", type: "number", min: 1, max: 6 },
  translate_len_budget: {
    label: "Translation Length Budget", type: "number", step: 0.05, min: 0.3, max: 1.2,
    hint: "Target translation length vs English (0.95 = aim ≤95%). Smaller = tighter.",
  },
  translate_len_budget_cjk: {
    label: "Length Budget (CJK)", type: "number", step: 0.05, min: 0.2, max: 1,
    hint: "Chinese/Japanese/Korean are denser — lower budget",
  },
  translate_fit_iters: { label: "Fit-to-Length Iterations", type: "number", min: 0, max: 6, hint: "Back-and-forth passes to shrink an over-long translation" },
  translate_fit_floor: {
    label: "Anti-gutting Floor", type: "number", step: 0.05, min: 0.2, max: 0.9,
    hint: "Never compress below this fraction of English — protects meaning",
  },
  translate_len_enforce: { label: "Enforce Length Fitting", type: "boolean" },
  en_chars_per_sec: { label: "English Chars/sec (estimate)", type: "number", step: 0.5, min: 5, max: 25, hint: "Used to estimate target speech length" },

  dub_whisper_model: {
    label: "Whisper Model (Dub Splitting)", type: "select",
    options: [
      ["tiny", "tiny"], ["base", "base"], ["small", "small"],
      ["medium", "medium"], ["large-v2", "large-v2"], ["large-v3", "large-v3"],
    ],
  },
  dub_snap_window_ms:     { label: "Silence Snap Window (ms)", type: "number", min: 50, max: 3000, step: 50 },
  dub_normalize_rms:      { label: "Normalize Target RMS",     type: "number", min: 100, max: 10000, step: 100 },
  dub_continuous_timeout: { label: "TTS Idle Timeout per batch (s)", type: "number", min: 60, max: 3600, step: 60, hint: "Abort only if no batch completes in this long" },
  keep_background_music:  { label: "Keep Background Music", type: "boolean", hint: "Separate narration from music, dub the voice, re-mix over the original music (needs Demucs)" },
  dub_voice_gain:         { label: "Narration Level", type: "number", step: 0.05, min: 0, max: 3 },
  dub_music_gain:         { label: "Music Level", type: "number", step: 0.05, min: 0, max: 3, hint: "Ducked under the narration (0.8 = a bit quieter)" },
  cue_whisper_model:      { label: "Cue Segmentation Model", type: "select", options: [["tiny","tiny"],["base","base"],["small","small"],["medium","medium"],["large-v3","large-v3"]], hint: "Whisper model for splitting narration into cues (small = fast)" },
  dub_cue_batch:          { label: "Cues per continuous read", type: "number", min: 1, max: 20, hint: "Join up to N cues into one flowing Qwen3 read for a consistent voice, then word-split back per cue. 1 = per-cue (no split)." },
  dub_read_max_sec:       { label: "Max read length (s)", type: "number", min: 5, max: 120, step: 5, hint: "Cap how long one continuous read can get (token + alignment safety)" },
  dub_speech_max_stretch: { label: "Max speed-up (×)", type: "number", min: 1, max: 3, step: 0.05, hint: "Hardest a cue is sped up to fit. Lower = better quality, more cues ride into the pause (1.5 = up to 50% faster)" },
  dub_fade_in_ms:         { label: "Fade-in (ms)", type: "number", min: 0, max: 500, step: 5, hint: "Soft onset on each segment so it doesn't pop in" },
  dub_fade_out_ms:        { label: "Fade-out (ms)", type: "number", min: 0, max: 800, step: 5, hint: "Soft tail into the pause" },

  // ── Advanced (paths) ──────────────────────────────────────────────────────
  conda_python:  { label: "Qwen3 Python Path", type: "text", hint: "Python in the qwen3-tts conda env" },
  dots_python:   { label: "dots.tts Python Path", type: "text", hint: "Python in the dots_tts conda env" },
  demucs_python: { label: "Demucs Python Path", type: "text", hint: "Python env with `demucs` installed (music separation)" },
  demucs_model:  { label: "Demucs Model", type: "text", hint: "htdemucs (default)" },
}

// TTS model dropdown options — hardcoded list matching config.TTS_MODEL_PATHS keys
function TTS_MODEL_OPTIONS() {
  return [
    ["0.6B-Base", "0.6B Base"],
    ["0.6B-CustomVoice", "0.6B Custom Voice"],
    ["0.6B-VoiceDesign", "0.6B Voice Design"],
    ["1.7B-Base", "1.7B Base"],
    ["1.7B-CustomVoice", "1.7B Custom Voice"],
    ["1.7B-VoiceDesign", "1.7B Voice Design"],
  ]
}

const SECTION_LABELS = {
  ai_providers: "AI & Providers",
  voices_tts:   "Voices & TTS",
  dubbing_sync: "Dubbing & Sync",
  pdf_import:   "PDF Import",
  advanced:     "Advanced",
}

const SECTION_ORDER = ["ai_providers", "voices_tts", "dubbing_sync", "pdf_import", "advanced"]

// Short labels shown when the settings rail is collapsed.
const SECTION_ABBR = {
  ai_providers: "AI", voices_tts: "TTS",
  dubbing_sync: "DUB", pdf_import: "PDF", advanced: "ADV",
}


// ══════════════════════════════════════════════════════════════════════════════
// Page
// ══════════════════════════════════════════════════════════════════════════════

export default function Settings() {
  const [payload,    setPayload]    = useState(null)   // { values, sections }
  const [edited,     setEdited]     = useState({})     // key → new value (unsaved)
  const [activeTab,  setActiveTab]  = useState(SECTION_ORDER[0])
  const [loading,    setLoading]    = useState(true)
  const [saving,     setSaving]     = useState(false)
  const [error,      setError]      = useState(null)
  const [savedFlash, setSavedFlash] = useState(false)
  const rail = useResizableRail({ storageKey: "ms_settings", defaultWidth: 200, min: 150, max: 300 })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getSettings()
      setPayload(data)
      setEdited({})
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const isDirty = Object.keys(edited).length > 0

  const handleChange = (key, value) => {
    setEdited(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = async () => {
    if (!isDirty) return
    setSaving(true)
    setError(null)
    try {
      const fresh = await updateSettings(edited)
      setPayload(fresh)
      setEdited({})
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 1800)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDiscard = () => setEdited({})

  // Merge saved values with in-progress edits for display
  const displayValues = useMemo(() => {
    if (!payload) return {}
    return { ...payload.values, ...edited }
  }, [payload, edited])

  if (loading) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: colors.muted }}>
        Loading settings…
      </div>
    )
  }

  if (!payload) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: colors.error }}>
        {error || "Failed to load settings"}
      </div>
    )
  }

  const sectionKeys = payload.sections[activeTab] || []

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>

      {/* ── Left nav (drag-resizable + collapsible) ──────────────────────── */}
      <div style={{
        width: rail.width, minWidth: rail.width, borderRight: `1px solid ${colors.border}`,
        display: "flex", flexDirection: "column", padding: rail.collapsed ? "14px 6px" : "16px 10px",
        position: "relative",
      }}>
        {rail.collapsed ? (
          <div style={{ display: "flex", justifyContent: "center", marginBottom: 10 }}>
            <button onClick={rail.toggle} title="Expand settings" aria-label="Expand settings"
              style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 18 }}>»</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 6px 10px" }}>
            <span style={{ color: colors.muted, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.1em" }}>SETTINGS</span>
            <button onClick={rail.toggle} title="Collapse settings" aria-label="Collapse settings"
              style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 16 }}>«</button>
          </div>
        )}
        {SECTION_ORDER.map(key => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            title={rail.collapsed ? SECTION_LABELS[key] : ""}
            style={{
              display: "block", width: "100%", textAlign: rail.collapsed ? "center" : "left",
              padding: "8px 10px", marginBottom: 2, borderRadius: radius.sm,
              background: activeTab === key ? "rgba(255,107,53,0.12)" : "transparent",
              color:      activeTab === key ? colors.accent : colors.textDim,
              fontSize: rail.collapsed ? fonts.xs : fonts.base, fontWeight: activeTab === key ? fonts.medium : fonts.normal,
              border: "none", cursor: "pointer",
            }}
          >
            {rail.collapsed ? SECTION_ABBR[key] : SECTION_LABELS[key]}
          </button>
        ))}

        <div style={{ flex: 1 }} />

        {rail.collapsed ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
            {isDirty && <span title="unsaved changes" style={{ width: 7, height: 7, borderRadius: "50%", background: colors.warning }} />}
            <button onClick={handleSave} disabled={!isDirty} title={savedFlash ? "Saved" : "Save changes"} aria-label="Save changes"
              style={{ width: 32, height: 30, borderRadius: radius.sm, border: "none", cursor: isDirty ? "pointer" : "default",
                background: isDirty ? colors.accent : colors.btnBg, color: isDirty ? "#000" : colors.muted, fontSize: 14 }}>
              {savedFlash ? "✓" : "⤓"}
            </button>
          </div>
        ) : (
          <>
            {isDirty && (
              <div style={{ padding: "0 10px 8px", color: colors.warning, fontSize: fonts.xs }}>
                {Object.keys(edited).length} unsaved change{Object.keys(edited).length !== 1 ? "s" : ""}
              </div>
            )}
            <div style={{ padding: "0 10px", display: "flex", flexDirection: "column", gap: 8 }}>
              <Button variant="primary" onClick={handleSave} disabled={!isDirty} loading={saving} fullWidth>
                {savedFlash ? "Saved ✓" : "Save Changes"}
              </Button>
              {isDirty && (
                <Button variant="ghost" onClick={handleDiscard} fullWidth>
                  Discard
                </Button>
              )}
            </div>
          </>
        )}
        {!rail.collapsed && <RailDragHandle onMouseDown={rail.onDragStart} />}
      </div>

      {/* ── Right form area ──────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: "auto", padding: "24px 32px" }}>
        <div style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold, marginBottom: 4 }}>
          {SECTION_LABELS[activeTab]}
        </div>
        <div style={{ color: colors.muted, fontSize: fonts.sm, marginBottom: 24 }}>
          {sectionHint(activeTab)}
        </div>

        {error && (
          <div style={{
            background: "rgba(248,113,113,0.1)", border: `1px solid ${colors.error}`,
            borderRadius: radius.md, padding: "10px 14px", marginBottom: 20,
            color: colors.error, fontSize: fonts.sm,
          }}>
            {error}
          </div>
        )}

        <div style={{ maxWidth: "560px" }}>
          {sectionKeys.map(key => {
            const meta = FIELDS[key] || { label: key, type: "text" }
            // Task-scoped model pickers are VIRTUAL: they follow the task's
            // provider select and read/write the provider's own model key.
            if (meta.type === "taskmodel") {
              const provider = displayValues[meta.providerKey] || "nvidia"
              const targetKey = (TASK_MODEL_KEY[provider] || {})[meta.task] || `${provider}_model`
              return (
                <Field
                  key      = {key}
                  fieldKey = {targetKey}
                  meta     = {{ ...meta, provider }}
                  value    = {displayValues[targetKey]}
                  dirty    = {targetKey in edited}
                  onChange = {(v) => handleChange(targetKey, v)}
                />
              )
            }
            return (
              <Field
                key      = {key}
                fieldKey = {key}
                meta     = {meta}
                value    = {displayValues[key]}
                dirty    = {key in edited}
                onChange = {(v) => handleChange(key, v)}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}

function sectionHint(section) {
  switch (section) {
    case "ai_providers": return "Pick a provider per task — each model list shows only models suited to that task. API keys live below."
    case "slicer":    return "How PDF pages are sliced and prepared for AI narration."
    case "optimizer": return "Image compression settings for AI model uploads."
    case "detection":  return "Video panel detection thresholds and transcription settings."
    case "tts":       return "Recommended Qwen3 TTS model variant per voice mode."
    case "dubbing":   return "Forced-alignment and audio normalisation settings for dubbing."
    default:          return ""
  }
}


// ══════════════════════════════════════════════════════════════════════════════
// Field — renders the correct control based on FIELDS[key].type
// ══════════════════════════════════════════════════════════════════════════════

function Field({ meta, value, dirty, onChange }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <label style={{ color: colors.textDim, fontSize: fonts.sm, fontWeight: fonts.medium }}>
          {meta.label}
        </label>
        {dirty && (
          <span style={{
            width: 6, height: 6, borderRadius: "50%", background: colors.warning, flexShrink: 0,
          }} title="Unsaved change" />
        )}
      </div>

      {meta.type === "boolean" ? (
        <Toggle checked={!!value} onChange={onChange} />
      ) : meta.type === "select" ? (
        <Select options={meta.options} value={value} onChange={onChange} />
      ) : meta.type === "taskmodel" ? (
        // key: remount when the provider flips so the old provider's list never lingers
        <ModelField key={`${meta.provider}:${meta.task}`} provider={meta.provider} task={meta.task} value={value ?? ""} onChange={onChange} />
      ) : meta.type === "password" ? (
        <TextInput
          type        = "password"
          value       = {value ?? ""}
          onChange    = {e => onChange(e.target.value)}
          placeholder = "••••••••••••"
          autoComplete= "off"
        />
      ) : meta.type === "number" ? (
        <TextInput
          type     = "number"
          value    = {value ?? ""}
          min      = {meta.min}
          max      = {meta.max}
          step     = {meta.step ?? 1}
          onChange = {e => onChange(e.target.value)}
        />
      ) : (
        <TextInput
          type     = "text"
          value    = {value ?? ""}
          onChange = {e => onChange(e.target.value)}
        />
      )}

      {meta.hint && (
        <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 4 }}>
          {meta.hint}
        </div>
      )}
    </div>
  )
}


// ── Model picker (live provider catalog + free text) ─────────────────────────
// A combobox for model ids: a searchable dropdown of every model the provider
// currently serves (fetched live, shared across fields via a module cache) that
// also accepts free text — so a brand-new model works before the catalog knows it.

const _catalogCache = {}   // "provider:task" → Promise<string[]>
function fetchCatalog(provider, task) {
  const ck = `${provider}:${task}`
  if (!_catalogCache[ck]) {
    _catalogCache[ck] = listProviderModels(provider, task)
      .then((r) => r.models || [])
      .catch(() => { delete _catalogCache[ck]; return [] })
  }
  return _catalogCache[ck]
}

function ModelField({ provider, task, value, onChange }) {
  const [models, setModels] = useState([])
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState("")

  useEffect(() => {
    let alive = true
    fetchCatalog(provider, task).then((m) => { if (alive) setModels(m) })
    return () => { alive = false }
  }, [provider, task])

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const list = q ? models.filter((m) => m.toLowerCase().includes(q)) : models
    return list.slice(0, 200)
  }, [models, filter])

  return (
    <div style={{ position: "relative" }}>
      <div style={{ display: "flex", gap: 6 }}>
        <TextInput
          type="text"
          value={value}
          placeholder={models.length ? "Pick from the list or type a model id…" : "Type a model id…"}
          onChange={(e) => { onChange(e.target.value); setFilter(e.target.value); setOpen(true) }}
          onFocus={() => { setFilter(""); setOpen(true) }}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          style={{ flex: 1 }}
        />
        <button
          onMouseDown={(e) => { e.preventDefault(); setFilter(""); setOpen((o) => !o) }}
          title={models.length ? `${models.length} models available` : "Catalog unavailable — type the id"}
          style={{ background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.textDim,
                   borderRadius: radius.md, padding: "0 12px", cursor: "pointer", flexShrink: 0 }}>
          ▾
        </button>
      </div>
      {open && shown.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 40, marginTop: 4,
                      maxHeight: 260, overflowY: "auto", background: colors.panel,
                      border: `1px solid ${colors.border}`, borderRadius: radius.md,
                      boxShadow: "0 8px 24px rgba(0,0,0,0.45)" }}>
          {shown.map((m) => (
            <div key={m}
              onMouseDown={(e) => { e.preventDefault(); onChange(m); setOpen(false) }}
              style={{ padding: "7px 12px", cursor: "pointer", fontSize: fonts.sm,
                       color: m === value ? colors.accent : colors.text,
                       background: m === value ? colors.panel2 : "transparent" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = colors.panel2 }}
              onMouseLeave={(e) => { e.currentTarget.style.background = m === value ? colors.panel2 : "transparent" }}>
              {m}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


// ── Toggle (boolean switch) ───────────────────────────────────────────────────

function Toggle({ checked, onChange }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        width: 40, height: 22, borderRadius: 11, border: "none", cursor: "pointer",
        background: checked ? colors.accent : colors.border,
        position: "relative", transition: "background 0.15s", padding: 0,
      }}
    >
      <span style={{
        position: "absolute", top: 2, left: checked ? 20 : 2,
        width: 18, height: 18, borderRadius: "50%", background: "#fff",
        transition: "left 0.15s",
      }} />
    </button>
  )
}


// ── Select (dropdown) ─────────────────────────────────────────────────────────

function Select({ options, value, onChange }) {
  return (
    <select
      value    = {value ?? ""}
      onChange = {e => onChange(e.target.value)}
      style={{
        width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`,
        borderRadius: radius.sm, color: colors.text, padding: "8px 10px",
        fontSize: fonts.base, cursor: "pointer",
      }}
    >
      {options.map(([val, label]) => (
        <option key={val} value={val}>{label}</option>
      ))}
    </select>
  )
}
