/**
 * ui/src/pages/Dubbing.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * The Dubbing studio — tabbed workspace for voice work outside the per-episode
 * pipeline. Phase 1 ships Voices + Text-to-Speech; Voice Design + Ad-hoc Dubbing
 * arrive in Phase 2.
 *
 * A "voice" is model-agnostic: a reference clip + transcript. Both TTS engines
 * (Qwen3 and dots.tts) clone from it, so the same voice works for either engine.
 */

import { useState, useEffect, useCallback } from "react"
import {
  listVoices, getVoice, createVoice, updateVoice, deleteVoice, setVoiceReference,
  quickTTS, quickTTSStatus, designVoice, dubAdhoc, getLanguages, stageReference,
} from "../api/voices"
import { useNotify } from "../store/notify"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import Modal, { FormField, TextInput, TextArea } from "../components/Modal"
import { AudioButton } from "./stages/common"
import { FILES_ORIGIN } from "../api/panels"

// Poll a TTS job (quick / design / ad-hoc) to completion.
async function runJob(start, setJob, notify) {
  setJob({ status: "running", message: "Starting…" })
  try {
    const started = await start()
    let cur = started
    for (let i = 0; i < 600 && cur.status === "running"; i++) { await sleep(1500); cur = await quickTTSStatus(started.job_id); setJob(cur) }
    setJob(cur)
    if (cur.status === "failed") notify({ severity: "error", message: "Failed — see details below" })
    return cur
  } catch (err) { setJob({ status: "failed", error: err.message }); notify({ severity: "error", message: err.message }); return null }
}

async function saveAudio(job, notify, suggested) {
  if (!job?.path) { notify({ severity: "error", message: "Nothing to save yet" }); return }
  try {
    if (window.pywebview?.api?.save_file) {
      const dest = await window.pywebview.api.save_file(job.path, suggested || job.path.split("/").pop())
      notify(dest ? { severity: "success", message: `Saved to ${dest}` } : { severity: "info", message: "Save cancelled" })
    } else {
      window.open(`${FILES_ORIGIN}${job.audio_url}`, "_blank")   // browser fallback
    }
  } catch (err) { notify({ severity: "error", message: err.message }) }
}

function JobResult({ job, label = "result", suggested, notify }) {
  if (!job) return null
  if (job.status === "failed") return (
    <div style={{ marginTop: 10, fontSize: fonts.xs, color: colors.error, fontFamily: fonts.mono, whiteSpace: "pre-wrap", maxHeight: 160, overflow: "auto", background: colors.panel2, borderRadius: radius.md, padding: "8px 10px" }}>{job.error || "Failed."}</div>
  )
  if (job.status === "done" && job.audio_url) return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
      <AudioButton url={job.audio_url} title={`Play ${label}`} />
      <button onClick={() => saveAudio(job, notify, suggested)}
        style={{ background: "none", border: `1px solid ${colors.border}`, color: colors.accent, borderRadius: radius.sm, padding: "4px 10px", fontSize: fonts.xs, cursor: "pointer" }}>Save…</button>
      <span style={{ fontSize: fonts.xs, color: colors.success }}>{job.message || "ready"}</span>
    </div>
  )
  return <span style={{ fontSize: fonts.xs, color: colors.muted, marginLeft: 10 }}>{job.message || "working…"}</span>
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

const TABS = [
  { key: "voices",  label: "Voices" },
  { key: "tts",     label: "Text-to-Speech" },
  { key: "design",  label: "Voice Design" },
  { key: "adhoc",   label: "Ad-hoc Dubbing" },
]

export default function Dubbing() {
  const [tab, setTab] = useState(() => lsGet("ms_dub_tab", "voices"))
  const select = (k) => { setTab(k); lsSet("ms_dub_tab", k) }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{ padding: "16px 24px 0", borderBottom: `1px solid ${colors.border}` }}>
        <div style={{ color: colors.text, fontSize: fonts.lg, fontWeight: fonts.bold }}>Dubbing studio</div>
        <div style={{ color: colors.muted, fontSize: fonts.xs, margin: "2px 0 12px" }}>Create & manage voices · text-to-speech · works with both Qwen3 and dots.tts</div>
        <div style={{ display: "flex", gap: 4 }}>
          {TABS.map(t => (
            <button key={t.key} onClick={() => select(t.key)}
              style={{ background: "none", border: "none", borderBottom: `2px solid ${tab === t.key ? colors.accent : "transparent"}`,
                color: tab === t.key ? colors.text : colors.muted, fontSize: fonts.sm, fontWeight: tab === t.key ? fonts.medium : fonts.normal,
                padding: "8px 12px", cursor: "pointer" }}>{t.label}</button>
          ))}
        </div>
      </div>

      {/* Tabs stay MOUNTED (hidden via display) so their inputs + generated
          audio persist when you switch tabs and come back. */}
      <div style={{ flex: 1, overflow: "auto", padding: "16px 24px" }}>
        <div style={{ display: tab === "voices" ? "block" : "none" }}><VoicesTab /></div>
        <div style={{ display: tab === "tts"    ? "block" : "none", maxWidth: 760 }}><TtsTab /></div>
        <div style={{ display: tab === "design" ? "block" : "none", maxWidth: 760 }}><DesignTab /></div>
        <div style={{ display: tab === "adhoc"  ? "block" : "none", maxWidth: 760 }}><AdhocTab /></div>
      </div>
    </div>
  )
}

// ── Voice Design tab ──────────────────────────────────────────────────────────

function DesignTab() {
  const { notify } = useNotify()
  const [instruct, setInstruct] = useState("")
  const [text, setText] = useState("Hey! This is a quick sample of my voice.")
  const [language, setLanguage] = useState("English")
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)

  const design = async () => {
    if (!text.trim()) return
    setBusy(true)
    await runJob(() => designVoice(instruct, text, language), setJob, notify)
    setBusy(false)
  }
  const saveAsVoice = async () => {
    if (job?.status !== "done" || !job.path) return
    const name = window.prompt("Name this voice:")?.trim()
    if (!name) return
    setSaving(true)
    try {
      await createVoice({ name, language, mode: "VoiceClone", model: "1.7B-Base" })
      await setVoiceReference(name, job.path, false)            // generated clip; transcript = the input text
      await updateVoice(name, { name, ref_wav_text: text, x_vector_only: false })
      notify({ severity: "success", message: `Saved voice “${name}” — usable by both engines` })
    } catch (err) { notify({ severity: "error", message: err.message }) }
    finally { setSaving(false) }
  }

  return (
    <div style={card}>
      <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>Voice Design</div>
      <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 12px" }}>Describe a persona → Qwen3 synthesizes a sample → save it as a reusable voice (works on both engines). Requires the Qwen3 env.</div>
      <FormField label="Persona description" hint="e.g. Male, late 20s, warm, confident narrator">
        <TextArea value={instruct} onChange={e => setInstruct(e.target.value)} rows={2} placeholder="Describe how the voice should sound…" />
      </FormField>
      <FormField label="Sample line (becomes the reference transcript)">
        <TextInput value={text} onChange={e => setText(e.target.value)} />
      </FormField>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
        <TextInput value={language} onChange={e => setLanguage(e.target.value)} style={{ width: 140 }} />
        <Button variant="primary" size="sm" onClick={design} disabled={busy || !text.trim()} loading={busy}>Design</Button>
        {job?.status === "done" && job.path && <Button variant="secondary" size="sm" onClick={saveAsVoice} loading={saving}>Save as voice</Button>}
      </div>
      <JobResult job={job} label="designed voice" notify={notify} suggested="voice_design.wav" />
    </div>
  )
}

// ── Ad-hoc Dubbing tab ────────────────────────────────────────────────────────

function AdhocTab() {
  const { notify } = useNotify()
  const [voices, setVoices] = useState([])
  const [voice, setVoice] = useState("")
  const [text, setText] = useState("")
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { listVoices().then(setVoices).catch(() => {}) }, [])
  useEffect(() => { if (!voice && voices.length) setVoice(voices[0].name) }, [voices, voice])

  const dub = async () => {
    if (!text.trim() || !voice) return
    setBusy(true)
    await runJob(() => dubAdhoc(text, voice), setJob, notify)
    setBusy(false)
  }
  const lines = text.split("\n").filter(l => l.trim()).length

  return (
    <div style={card}>
      <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>Ad-hoc Dubbing</div>
      <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 12px" }}>Paste a script (one line per segment) and dub it with a voice into one combined track.</div>
      <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false} placeholder={"Line one…\nLine two…"}
        style={{ width: "100%", minHeight: 140, resize: "vertical", background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "9px 11px", fontSize: fonts.base, fontFamily: fonts.ui, lineHeight: 1.6, outline: "none" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
        <select value={voice} onChange={e => setVoice(e.target.value)} style={sel}>
          {voices.length === 0 && <option value="">No voices</option>}
          {voices.map(v => <option key={v.name} value={v.name}>{v.name} ({v.language})</option>)}
        </select>
        <Button variant="primary" size="sm" onClick={dub} disabled={busy || !voice || !text.trim()} loading={busy}>Dub{lines > 0 ? ` (${lines})` : ""}</Button>
      </div>
      <JobResult job={job} label="dub" notify={notify} suggested="dub.wav" />
    </div>
  )
}

// ── Voices tab ────────────────────────────────────────────────────────────────

function VoicesTab() {
  const { notify } = useNotify()
  const [voices, setVoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(null)
  const [isNew, setIsNew] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try { setVoices(await listVoices()) }
    catch (err) { notify({ severity: "error", message: err.message }) }
    finally { setLoading(false) }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [load])

  const openEdit = async (name) => {
    try { setEditing(await getVoice(name)); setIsNew(false) }
    catch (err) { notify({ severity: "error", message: err.message }) }
  }
  const remove = async (name) => {
    if (!window.confirm(`Delete voice "${name}"? This cannot be undone.`)) return
    try { await deleteVoice(name); notify({ severity: "success", message: `Deleted ${name}` }); load() }
    catch (err) { notify({ severity: "error", message: err.message }) }
  }

  return (
    <div style={{ maxWidth: 920 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ fontSize: fonts.sm, color: colors.muted }}>
          A voice is a reference clip + transcript — both engines clone from it. Engine (Qwen3/dots) is set in <span style={{ color: colors.textDim }}>Settings → Voices &amp; TTS</span>; the Qwen variant + seed are per-voice.
        </div>
        <Button variant="primary" size="sm" onClick={() => { setEditing(blankProfile()); setIsNew(true) }}>+ New voice</Button>
      </div>

      {loading && <div style={{ color: colors.muted, fontSize: fonts.sm, padding: 12 }}>Loading…</div>}
      {!loading && voices.length === 0 && <div style={{ color: colors.muted, fontSize: fonts.sm, padding: 12 }}>No voices yet — create one from a reference clip.</div>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
        {voices.map(v => (
          <div key={v.name} style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "11px 13px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div style={{ fontSize: fonts.sm, fontWeight: fonts.medium, color: colors.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v.name}</div>
              <span style={{ fontSize: 10, color: v.mode === "VoiceClone" ? colors.success : colors.muted, border: `1px solid ${v.mode === "VoiceClone" ? colors.success : colors.border}`, borderRadius: radius.full, padding: "1px 7px", whiteSpace: "nowrap" }}>
                {v.mode === "VoiceClone" ? "ref voice" : v.mode}
              </span>
            </div>
            <div style={{ fontSize: fonts.xs, color: colors.muted, marginTop: 6 }}>{v.language || "—"} · <span style={{ color: colors.textDim }}>{v.model}</span></div>
            <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
              <button onClick={() => openEdit(v.name)} style={miniBtn}>Edit</button>
              <button onClick={() => remove(v.name)} style={{ ...miniBtn, color: colors.error, borderColor: "rgba(248,113,113,0.4)" }}>Delete</button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <VoiceEditor profile={editing} isNew={isNew} notify={notify}
          onClose={() => { setEditing(null); load() }} onSaved={() => { setEditing(null); load() }} />
      )}
    </div>
  )
}

// Best-default profile. The user only picks name/language/reference/transcript;
// model = best Qwen clone variant, params = recommended, seed = -1 (natural).
// These ride along silently so the form stays clean.
const blankProfile = () => ({
  name: "", mode: "VoiceClone", model: "1.7B-Base", language: "English",
  speaker: "Aiden", instruct: "", ref_wav_path: "", ref_wav_text: "",
  x_vector_only: true, temperature: 0.7, top_p: 1.0, top_k: 50,
  repetition_penalty: 1.1, max_new_tokens: 2048, seed: -1,
})

// ── Voice editor (unified, reference-based) ─────────────────────────────────────

function VoiceEditor({ profile, isNew, notify, onClose, onSaved }) {
  const [p, setP] = useState(profile)
  const [saving, setSaving] = useState(false)
  const [refBusy, setRefBusy] = useState(false)
  const [langs, setLangs] = useState([])
  const [stagedPath, setStagedPath] = useState(null)   // picked clip, committed on Save
  const [refLabel, setRefLabel] = useState((profile.ref_wav_path || "").split("/").pop())
  const set = (k, v) => setP(prev => ({ ...prev, [k]: v }))

  useEffect(() => { getLanguages().then(setLangs).catch(() => {}) }, [])

  // Pick + stage a clip. Nothing is created/written until Save — so cancelling
  // the dialog never leaves a "ghost" voice behind.
  const chooseReference = async () => {
    let path
    try {
      if (window.pywebview?.api?.pick_file) {
        path = await window.pywebview.api.pick_file(["Audio Files (*.wav;*.mp3;*.m4a;*.flac;*.ogg)"])
      } else {
        path = window.prompt("Path to a reference audio clip (a few seconds):") || null
      }
    } catch { path = null }
    if (!path) return
    setRefBusy(true)
    try {
      const { staged_path, transcript } = await stageReference(path, true)   // convert + transcribe, no voice yet
      setStagedPath(staged_path)
      setRefLabel(path.split("/").pop())
      if (transcript) set("ref_wav_text", transcript)
      notify({ severity: "success", message: "Clip ready — transcript auto-filled (editable). Save to keep it." })
    } catch (err) { notify({ severity: "error", message: err.message }) }
    finally { setRefBusy(false) }
  }

  const save = async () => {
    if (!p.name.trim()) { notify({ severity: "error", message: "Name is required" }); return }
    // A voice clones from its reference, so a reference is required — either one
    // just picked (stagedPath) or one already attached (editing).
    if (!stagedPath && !(p.ref_wav_path || "").trim()) {
      notify({ severity: "error", message: "Add a reference clip first — click “Choose audio…”." }); return
    }
    setSaving(true)
    try {
      if (isNew) await createVoice(p); else await updateVoice(p.name, p)
      if (stagedPath) await setVoiceReference(p.name, stagedPath, false)   // commit the picked clip
      notify({ severity: "success", message: `Saved “${p.name}”` }); onSaved()
    } catch (err) { notify({ severity: "error", message: err.message }); setSaving(false) }
  }

  const refName    = refLabel
  const curEngine  = langs.find(l => l.name === p.language)?.engine
  const engineHint = curEngine ? `engine: ${curEngine === "dots" ? "dots.tts" : "Qwen3"} (chosen automatically)` : "engine chosen automatically"

  return (
    <Modal open title={isNew ? "New voice" : `Edit ${p.name}`} onClose={onClose}
      onConfirm={save} confirmLabel="Save" confirmLoading={saving} width={560}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <FormField label="Name">
          <TextInput value={p.name} onChange={e => set("name", e.target.value)} disabled={!isNew} placeholder="e.g. Adam" />
        </FormField>
        <FormField label="Language" hint={engineHint}>
          <select value={p.language} onChange={e => set("language", e.target.value)}
            style={{ width: "100%", background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "8px 9px", fontSize: fonts.sm }}>
            {langs.length === 0 && <option value={p.language}>{p.language}</option>}
            {langs.map(l => <option key={l.code} value={l.name}>{l.name}</option>)}
          </select>
        </FormField>
      </div>

      <FormField label="Reference clip" hint="a few seconds of clean speech — the voice is cloned from this">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Button variant="secondary" size="sm" onClick={chooseReference} loading={refBusy}>
            {refName ? "Replace clip…" : "Choose audio…"}
          </Button>
          <span style={{ fontSize: fonts.xs, color: refName ? colors.success : colors.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {refBusy ? "uploading & transcribing…" : refName || "no reference yet"}
          </span>
        </div>
      </FormField>

      <FormField label="Transcript" hint="auto-filled from the clip — edit it or paste your own (e.g. from Gemini). Leave blank for x-vector-only cloning.">
        <TextArea value={p.ref_wav_text} onChange={e => set("ref_wav_text", e.target.value)} rows={3}
          placeholder="What the reference clip says…" />
      </FormField>
    </Modal>
  )
}

// ── Text-to-Speech tab (Quick TTS) ──────────────────────────────────────────────

function TtsTab() {
  const { notify } = useNotify()
  const [voices, setVoices] = useState([])
  const [text, setText] = useState("Hello, this is a quick voice sample.")
  const [voice, setVoice] = useState("")
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { listVoices().then(setVoices).catch(() => {}) }, [])
  useEffect(() => { if (!voice && voices.length) setVoice(voices[0].name) }, [voices, voice])

  const generate = async () => {
    if (!text.trim() || !voice) return
    setBusy(true); setJob({ status: "running", message: "Starting…" })
    try {
      const started = await quickTTS(text, voice)
      let cur = started
      for (let i = 0; i < 400 && cur.status === "running"; i++) { await sleep(1500); cur = await quickTTSStatus(started.job_id); setJob(cur) }
      setJob(cur)
      if (cur.status === "failed") notify({ severity: "error", message: "TTS failed — see details below" })
    } catch (err) { setJob({ status: "failed", error: err.message }); notify({ severity: "error", message: err.message }) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "14px 16px" }}>
      <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>Text-to-Speech</div>
      <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 12px" }}>Render a sample with any voice — uses the active engine (Qwen3 / dots.tts).</div>
      <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false} placeholder="Type something to say…"
        style={{ width: "100%", minHeight: 80, resize: "vertical", background: colors.panel2, color: colors.text,
          border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "9px 11px", fontSize: fonts.base, fontFamily: fonts.ui, lineHeight: 1.5, outline: "none" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
        <select value={voice} onChange={e => setVoice(e.target.value)} style={sel}>
          {voices.length === 0 && <option value="">No voices</option>}
          {voices.map(v => <option key={v.name} value={v.name}>{v.name} ({v.language})</option>)}
        </select>
        <Button variant="primary" size="sm" onClick={generate} disabled={busy || !voice || !text.trim()} loading={busy}>
          {busy ? "Generating…" : "Generate"}
        </Button>
        {busy && <span style={{ fontSize: fonts.xs, color: colors.muted }}>{job?.message || "the model loads on first run — this can take a minute"}</span>}
      </div>
      <JobResult job={job} label="sample" notify={notify} suggested="tts_sample.wav" />
    </div>
  )
}

const card = { background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "14px 16px" }
const sel = { background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "7px 9px", fontSize: fonts.sm, minWidth: 160 }
const miniBtn = { flex: 1, background: "none", border: `1px solid ${colors.border}`, color: colors.textDim, borderRadius: radius.sm, padding: "5px 0", fontSize: fonts.xs, cursor: "pointer" }
