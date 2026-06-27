/**
 * ui/src/pages/stages/RefineDetail.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Detail view for the narration stage (video "Refine" / pdf "Narrate").
 *
 * This is the review checkpoint folded into the stage that produces the data:
 *   • Narration tone & style  — per-episode, guides Refine + Translate.
 *   • Per panel  — detected image beside its editable AI narration.
 *
 * Editing a narration autosaves and cascades: the backend invalidates that
 * panel's downstream translation + audio and flips Translate/Dub/Sync to
 * "outdated"; onDataChanged() refreshes the rest of the UI live.
 * Raw transcript is NOT here — it lives in the Detect stage.
 */

import { useState, useEffect } from "react"
import { updateNarration } from "../../api/panels"
import { updateEpisode } from "../../api/episodes"
import { useNotify } from "../../store/notify"
import { colors, fonts, radius } from "../../theme"
import { useEpisodePanels, PanelThumb, DetailCenter, DetailHeader } from "./common"

export default function RefineDetail({ episode, signal, busy, onRun, status, progress, onDataChanged }) {
  const { notify } = useNotify()
  const { panels, setPanels, loading, error, reload } = useEpisodePanels(episode?.id, signal)

  const replace = (fresh) => setPanels(prev => prev.map(p => (p.id === fresh.id ? fresh : p)))

  return (
    <div>
      <DetailHeader
        title="Refine — narration review"
        subtitle="Edit AI narration beside each panel · autosaves · edits re-queue Translate, Dub & Sync"
        status={status} progress={progress} busy={busy}
        onRun={onRun} runLabel={status === "done" ? "Re-run" : "Run"}
      />

      <ToneCard episode={episode} onSaved={onDataChanged} />

      {loading && <DetailCenter>Loading panels…</DetailCenter>}
      {error && !loading && (
        <DetailCenter><div style={{ color: colors.error }}>{error}</div>
          <button onClick={reload} style={retry}>Retry</button></DetailCenter>
      )}
      {!loading && !error && panels.length === 0 && (
        <DetailCenter>No panels yet — run this stage to generate narration.</DetailCenter>
      )}

      {!loading && !error && panels.map(panel => (
        <PanelRow key={panel.id} panel={panel} onSaved={replace} notify={notify} onDataChanged={onDataChanged} />
      ))}
    </div>
  )
}

function ToneCard({ episode, onSaved }) {
  const initial = episode?.tone_prompt || ""
  const [val, setVal] = useState(initial)
  const [server, setServer] = useState(initial)
  const [saving, setSaving] = useState(false)
  const { notify } = useNotify()

  useEffect(() => { setVal(episode?.tone_prompt || ""); setServer(episode?.tone_prompt || "") }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = val !== server
  const save = async () => {
    setSaving(true)
    try {
      await updateEpisode(episode.id, { tone_prompt: val })
      setServer(val); notify({ severity: "success", message: "Narration tone saved" })
      onSaved?.()
    } catch (err) { notify({ severity: "error", message: `Couldn't save tone: ${err.message}` }) }
    finally { setSaving(false) }
  }

  return (
    <div style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>Narration tone &amp; style</div>
      <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 10px" }}>
        Guides how the AI writes — used by Refine and Translate. Set this before running.
      </div>
      <textarea
        value={val} onChange={e => setVal(e.target.value)} spellCheck={false}
        placeholder="e.g. Cinematic recap voice — dramatic, present tense, concise."
        style={{ width: "100%", minHeight: 60, resize: "vertical", background: colors.panel2, color: colors.text,
          border: `1px solid ${dirty ? colors.warning : colors.border}`, borderRadius: radius.md,
          padding: "8px 10px", fontSize: fonts.base, fontFamily: fonts.ui, lineHeight: 1.5, outline: "none" }}
      />
      <div style={{ marginTop: 8 }}>
        <button onClick={save} disabled={!dirty || saving}
          style={{ background: dirty ? colors.accent : colors.btnBg, color: dirty ? "#000" : colors.muted,
            border: "none", borderRadius: radius.sm, padding: "6px 13px", fontSize: fonts.sm,
            fontWeight: fonts.medium, cursor: dirty ? "pointer" : "default" }}>
          {saving ? "Saving…" : "Save tone"}
        </button>
      </div>
    </div>
  )
}

function PanelRow({ panel, onSaved, notify, onDataChanged }) {
  return (
    <div style={{ display: "flex", gap: 14, alignItems: "stretch", background: colors.panel,
      border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 12, marginBottom: 10 }}>
      <div style={{ width: 34, flexShrink: 0, color: colors.muted, fontFamily: fonts.mono, fontSize: fonts.sm }}>
        <div style={{ color: colors.textDim, fontWeight: fonts.bold }}>#{panel.panel_index + 1}</div>
        {panel.duration_sec != null && <div style={{ marginTop: 4, fontSize: fonts.xs }}>{panel.duration_sec.toFixed(1)}s</div>}
      </div>
      <PanelThumb panel={panel} />
      <NarrationField panel={panel}
        onSave={async (text) => {
          const res = await updateNarration(panel.id, text)
          onSaved(res.panel)
          if (res.invalidated_langs?.length) onDataChanged?.()
          return res.invalidated_langs?.length
            ? `saved — re-queued ${res.invalidated_langs.length} language(s)` : "saved"
        }}
        notify={notify}
      />
    </div>
  )
}

function NarrationField({ panel, onSave }) {
  const initial = panel.narration_text || ""
  const [value, setValue] = useState(initial)
  const [server, setServer] = useState(initial)
  const [msg, setMsg] = useState("")
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => { setValue(initial); setServer(initial); setMsg(""); setFailed(false) }, [initial])

  const dirty = value !== server
  const commit = async () => {
    if (!dirty) return
    setSaving(true); setFailed(false); setMsg("saving…")
    try { setMsg(await onSave(value) || "saved"); setServer(value) }
    catch (err) { setFailed(true); setMsg(err.message || "save failed") }
    finally { setSaving(false) }
  }

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
        <span style={{ color: colors.textDim, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.06em" }}>NARRATION</span>
        <span style={{ fontSize: fonts.xs, color: failed ? colors.error : dirty ? colors.warning : colors.muted }}>
          {saving ? "saving…" : msg || (dirty ? "unsaved" : "")}
        </span>
      </div>
      <textarea value={value} placeholder="No narration yet"
        onChange={e => setValue(e.target.value)} onBlur={commit} spellCheck={false}
        style={{ flex: 1, minHeight: 84, resize: "vertical", background: colors.panel2, color: colors.text,
          border: `1px solid ${dirty ? colors.warning : colors.border}`, borderRadius: radius.md,
          padding: "9px 11px", fontSize: fonts.base, fontFamily: fonts.ui, lineHeight: 1.5, outline: "none" }} />
    </div>
  )
}

const retry = { marginTop: 10, background: "#2a2a2e", color: "#f0f0f0", border: "none", borderRadius: 4, padding: "5px 12px", cursor: "pointer", fontSize: 12 }
