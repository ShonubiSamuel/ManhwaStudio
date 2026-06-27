/**
 * ui/src/pages/stages/DetectDetail.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Detail view for the Detect stage — the detection-tuning workflow.
 *
 *   1. Detection settings  — how cuts are found (saved to the episode).
 *   2. Tune on a sample    — extract a short clip, open the parameter tuner,
 *                            and preview the cuts (with expandable transcript)
 *                            before committing to the full pass.
 *   Header "Run detect"    — runs the full detection on the whole video.
 *
 * Feedback is inline + toasts (no console): progress shows on the run button,
 * and every action confirms or errors via a toast.
 */

import { useState, useEffect, useCallback } from "react"
import {
  getDetectConfig, saveDetectConfig, extractClip, runPreview, openTuner,
} from "../../api/detect"
import { useNotify } from "../../store/notify"
import { colors, fonts, radius } from "../../theme"
import { DetailHeader, DetailCenter } from "./common"

const MODES = [
  ["combined", "combined — audio + visual"],
  ["audio",    "audio — silence only"],
  ["visual",   "visual — scene only"],
]
const PRIORITIES = [
  ["combined",     "combined"],
  ["visual_first", "visual first"],
  ["audio_first",  "audio first"],
]
const NUMFIELDS = [
  ["silence_db",       "Silence threshold (dBFS)", "lower = stricter silence"],
  ["min_silence_sec",  "Min silence (s)",          "shortest pause = a panel gap"],
  ["visual_threshold", "Visual threshold",         "lower = more sensitive"],
  ["min_scene_sec",    "Min scene gap (s)",        "min gap between visual cuts"],
  ["frame_skip",       "Frame skip",               "2 = every 3rd frame"],
  ["merge_window",     "Merge window (s)",         "max gap to link audio + visual"],
  ["workers",          "Parallel workers",         "ffmpeg workers for export"],
]
const EDITABLE = ["mode", "priority", ...NUMFIELDS.map(f => f[0]), "clip_start", "clip_duration"]

export default function DetectDetail({ episode, signal, busy, onRun, status, progress }) {
  const { notify } = useNotify()
  const [cfg, setCfg]       = useState(null)
  const [form, setForm]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState("")
  const [dirty, setDirty]   = useState(false)
  const [saving, setSaving] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [cuts, setCuts]     = useState(null)

  const hydrate = useCallback((c) => {
    const f = {}
    for (const k of EDITABLE) f[k] = String(c[k])
    setCfg(c); setForm(f); setDirty(false)
  }, [])

  const load = useCallback(async () => {
    if (!episode?.id) return
    setLoading(true); setError("")
    try { hydrate(await getDetectConfig(episode.id)) }
    catch (err) { setError(err.message || "Failed to load detect settings") }
    finally { setLoading(false) }
  }, [episode?.id, hydrate])

  useEffect(() => { load() }, [load, signal])

  if (loading) return <Frame onRun={onRun} status={status} progress={progress} busy={busy}><DetailCenter>Loading settings…</DetailCenter></Frame>
  if (error)   return <Frame onRun={onRun} status={status} progress={progress} busy={busy}><DetailCenter><div style={{ color: colors.error }}>{error}</div><button onClick={load} style={btnReset}>Retry</button></DetailCenter></Frame>
  if (!form)   return null

  const set = (k, v) => { setForm(p => ({ ...p, [k]: v })); setDirty(true) }

  const toPayload = () => ({
    mode: form.mode, priority: form.priority,
    silence_db: parseFloat(form.silence_db), min_silence_sec: parseFloat(form.min_silence_sec),
    visual_threshold: parseFloat(form.visual_threshold), min_scene_sec: parseFloat(form.min_scene_sec),
    frame_skip: parseInt(form.frame_skip, 10), merge_window: parseFloat(form.merge_window),
    workers: parseInt(form.workers, 10),
    clip_start: form.clip_start, clip_duration: parseInt(form.clip_duration, 10),
  })

  const save = async () => {
    setSaving(true)
    try { hydrate(await saveDetectConfig(episode.id, toPayload())); notify({ severity: "success", message: "Detection settings saved" }) }
    catch (err) { notify({ severity: "error", message: `Couldn't save settings: ${err.message}` }) }
    finally { setSaving(false) }
  }

  const reset = () => {
    const d = cfg.defaults || {}
    setForm(p => ({ ...p, ...Object.fromEntries(Object.entries(d).map(([k, v]) => [k, String(v)])) }))
    setDirty(true)
    notify({ severity: "info", message: "Reset to defaults — review and Save to apply" })
  }

  const doExtract = async () => {
    setExtracting(true)
    try {
      const c = await extractClip(episode.id, form.clip_start, parseInt(form.clip_duration, 10))
      setCfg(c); notify({ severity: "success", message: `Test clip ready (${form.clip_duration}s from ${form.clip_start})` })
    } catch (err) { notify({ severity: "error", message: `Clip extraction failed: ${err.message}` }) }
    finally { setExtracting(false) }
  }

  const doTuner = async () => {
    try { const r = await openTuner(episode.id); notify({ severity: "info", message: r.message || "Parameter tuner opening in your browser" }) }
    catch (err) { notify({ severity: "error", message: `Tuner failed: ${err.message}` }) }
  }

  const doPreview = async () => {
    setPreviewing(true)
    try {
      if (dirty) await saveDetectConfig(episode.id, toPayload())  // preview uses saved settings
      const r = await runPreview(episode.id)
      setCuts(r)
      notify({ severity: r.count ? "success" : "warning", message: `Preview · ${r.count} cuts${r.count ? ` · avg ${r.avg_duration}s` : " — try adjusting settings"}` })
      setDirty(false)
    } catch (err) { notify({ severity: "error", message: `Preview failed: ${err.message}` }) }
    finally { setPreviewing(false) }
  }

  const clipReady = cfg.clip_ready

  return (
    <Frame onRun={onRun} status={status} progress={progress} busy={busy}>
      {/* Settings */}
      <Card title="Detection settings" sub="How cuts are found. Defaults work for most chapters — tune below if cuts look off.">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "11px 16px" }}>
          <Field label="Mode">
            <select value={form.mode} onChange={e => set("mode", e.target.value)} style={inp}>
              {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Merge priority">
            <select value={form.priority} onChange={e => set("priority", e.target.value)} style={inp}>
              {PRIORITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          {NUMFIELDS.map(([k, label, hint]) => (
            <Field key={k} label={label} hint={hint}>
              <input value={form[k]} onChange={e => set(k, e.target.value)} style={inp} inputMode="decimal" />
            </Field>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 14, paddingTop: 12, borderTop: `1px solid ${colors.border}`, alignItems: "center" }}>
          <button onClick={save} disabled={saving || !dirty} style={btnSave(dirty)}>{saving ? "Saving…" : "Save"}</button>
          <button onClick={reset} style={btnReset}>Reset defaults</button>
          {!cfg.confirmed && <span style={{ fontSize: fonts.xs, color: colors.muted, marginLeft: 4 }}>tip: preview a sample before the full run</span>}
        </div>
      </Card>

      {/* Tune on a sample */}
      <Card title="Tune on a sample" sub="Extract 1–3 min, preview the cuts (with transcript), and dial settings in before the full run." optional>
        {!cfg.source_exists && (
          <div style={warnBox}>Source video not found — check the episode's source path before extracting.</div>
        )}
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <Field label="Start time" style={{ width: 120 }}>
            <input value={form.clip_start} onChange={e => set("clip_start", e.target.value)} style={inp} />
          </Field>
          <Field label="Duration (s)" style={{ width: 110 }}>
            <input value={form.clip_duration} onChange={e => set("clip_duration", e.target.value)} style={inp} inputMode="numeric" />
          </Field>
          <button onClick={doExtract} disabled={extracting || !cfg.source_exists} style={ghost(extracting || !cfg.source_exists)}
            title={cfg.source_exists ? "" : "Source video not found"}>{extracting ? "Extracting…" : "▶ Extract clip"}</button>
          <button onClick={doTuner} disabled={!clipReady} style={ghost(!clipReady)} title={clipReady ? "" : "Extract a clip first"}>⌕ Parameter tuner</button>
          <button onClick={doPreview} disabled={previewing || !clipReady} style={ghost(previewing || !clipReady)} title={clipReady ? "" : "Extract a clip first"}>{previewing ? "Running…" : "▶ Run preview"}</button>
          {!clipReady && <span style={{ fontSize: fonts.xs, color: colors.muted, alignSelf: "center" }}>Extract a test clip to enable the tuner & preview.</span>}
        </div>

        {cuts && (
          <div style={{ marginTop: 12 }}>
            {cuts.count === 0 ? (
              <div style={{ color: colors.muted, fontSize: fonts.sm }}>No cuts found on this clip — try lowering the thresholds.</div>
            ) : (
              <>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: fonts.sm }}>
                  <thead>
                    <tr>{["#", "Start", "End", "Dur", "Transcript"].map(h => (
                      <th key={h} style={th}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {cuts.cuts.map(c => <CutRow key={c.panel_index} c={c} />)}
                  </tbody>
                </table>
                <span style={chip}>{cuts.count} cuts · avg {cuts.avg_duration}s ✓</span>
              </>
            )}
          </div>
        )}
      </Card>
    </Frame>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Frame({ children, onRun, status, progress, busy }) {
  return (
    <div>
      <DetailHeader
        title="Detect" subtitle="Find panel cuts — tune, preview, then run the full pass."
        status={status} progress={progress} busy={busy}
        onRun={onRun} runLabel={status === "done" ? "Re-run detect" : "Run detect"}
      />
      {children}
    </div>
  )
}

function CutRow({ c }) {
  const [open, setOpen] = useState(false)
  const txt = c.transcript_text || ""
  return (
    <tr onClick={() => txt && setOpen(o => !o)} style={{ cursor: txt ? "pointer" : "default" }}>
      <td style={td}>{c.panel_index + 1}</td>
      <td style={td}>{c.start_time_sec.toFixed(2)}s</td>
      <td style={td}>{c.end_time_sec.toFixed(2)}s</td>
      <td style={td}>{c.duration_sec.toFixed(2)}s</td>
      <td style={{ ...td, color: colors.textDim }}>
        {txt
          ? <div style={open ? {} : { display: "-webkit-box", WebkitLineClamp: 1, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{txt}</div>
          : <span style={{ color: colors.muted }}>—</span>}
      </td>
    </tr>
  )
}

function Card({ title, sub, optional, children }) {
  return (
    <div style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, margin: "0 0 14px", padding: "14px 16px" }}>
      <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>
        {title}{optional && <span style={{ color: colors.muted, fontWeight: fonts.normal, fontSize: fonts.sm }}> — optional</span>}
      </div>
      <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 13px" }}>{sub}</div>
      {children}
    </div>
  )
}

function Field({ label, hint, style, children }) {
  return (
    <div style={style}>
      <label style={{ display: "block", fontSize: fonts.xs, color: colors.textDim, marginBottom: 4 }}>{label}</label>
      {children}
      {hint && <div style={{ fontSize: 10.5, color: colors.muted, marginTop: 3 }}>{hint}</div>}
    </div>
  )
}

const inp = { width: "100%", background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "6px 8px", fontSize: fonts.sm, fontFamily: fonts.ui }
const th  = { textAlign: "left", color: colors.accent, fontWeight: fonts.medium, fontSize: fonts.xs, padding: "6px 8px", borderBottom: `1px solid ${colors.border}` }
const td  = { padding: "7px 8px", borderBottom: `1px solid ${colors.border}`, color: colors.textDim, verticalAlign: "top" }
const chip = { display: "inline-block", fontSize: fonts.xs, color: colors.success, border: `1px solid ${colors.success}`, borderRadius: radius.full, padding: "2px 10px", marginTop: 10 }
const warnBox = { fontSize: fonts.xs, color: colors.warning, background: "rgba(251,191,36,0.08)", border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "7px 10px", marginBottom: 12 }
const btnGhost = { background: "none", border: `1px solid ${colors.border}`, color: colors.textDim, borderRadius: radius.sm, padding: "6px 12px", fontSize: fonts.sm, cursor: "pointer" }
// Ghost button that visibly grays out when disabled (so the user can see at a
// glance that an action isn't available yet — e.g. before a clip is extracted).
const ghost = (disabled) => disabled
  ? { ...btnGhost, color: colors.muted, borderColor: colors.border, opacity: 0.5, cursor: "not-allowed" }
  : btnGhost
const btnReset = { ...btnGhost }
const btnSave = (dirty) => ({ background: dirty ? colors.accent : colors.btnBg, color: dirty ? "#000" : colors.muted, border: "none", borderRadius: radius.sm, padding: "6px 14px", fontSize: fonts.sm, fontWeight: fonts.medium, cursor: dirty ? "pointer" : "default" })
