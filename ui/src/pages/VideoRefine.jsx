/**
 * ui/src/pages/VideoRefine.jsx — the Video Refine section (manga recaps).
 *
 * Video Refine IS the Voiceover editor (DubStudio) with manga-panel cropping
 * bolted on: the exact same cue list, Speaker/CPS, AI Fix, AI Refine, timeline,
 * volumes, Regenerate Dub, Auto-Scroll / Hide Original, undo/redo — plus a
 * right-pane toggle between the Source Preview and a PDF panel cropper, and a
 * panel thumbnail on each cue. The editor is shared via `ProjectDubStudio`
 * (refine mode); only the project list + creation live here.
 */

import { useState } from "react"
import { useApp, actions, PAGES } from "../store/app"
import { useNotify } from "../store/notify"
import { createRefineProject } from "../api/videoRefine"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import { ProjectDubStudio } from "./DubStudio"
import RefineProjectList from "./RefineProjectList"

export default function VideoRefine() {
  const { state, dispatch } = useApp()
  const pid = state.activeProjectSection === PAGES.VIDEO_REFINE ? state.activeProjectId : null
  if (pid != null) {
    return <ProjectDubStudio key={pid} projectId={pid} refine onBack={() => dispatch(actions.setProject(null))} />
  }
  return <RefineHome onOpen={(id) => dispatch(actions.setProject(id, PAGES.VIDEO_REFINE))} />
}

/* ─────────────────────────────────────────────────────────────────────────────
   Project picker
───────────────────────────────────────────────────────────────────────────── */

function RefineHome({ onOpen }) {
  return (
    <RefineProjectList
      kind="video_refine"
      icon="🖼"
      title="Video Refine"
      subtitle="Turn a video into a recap: refine the narration and crop high-quality manga panels."
      newLabel="+ New Video Refine"
      emptyText="No video refines yet — click “New Video Refine”."
      onOpen={onOpen}
      renderNewModal={({ onClose, onCreated }) => <NewRefineModal onClose={onClose} onCreated={onCreated} />}
    />
  )
}

function NewRefineModal({ onClose, onCreated }) {
  const { notify } = useNotify()
  const [name, setName] = useState("")
  const [sourcePath, setSourcePath] = useState("")
  const [busy, setBusy] = useState(false)

  const pick = async () => {
    try {
      if (window.pywebview?.api?.pick_file) {
        const p = await window.pywebview.api.pick_file(["Media Files (*.mp4;*.mkv;*.mov;*.m4a;*.mp3;*.wav)"])
        if (p) { setSourcePath(p); if (!name) setName((p.split(/[\\/]/).pop() || "").replace(/\.[^.]+$/, "")) }
      } else {
        const p = window.prompt("Absolute path to the source video:")
        if (p) setSourcePath(p.trim())
      }
    } catch (e) { notify({ severity: "error", message: e.message }) }
  }

  const create = async () => {
    setBusy(true)
    try {
      const res = await createRefineProject((name || "Untitled refine").trim(), sourcePath)
      onCreated(res.id)
    } catch (e) { notify({ severity: "error", message: e.message }) }
    finally { setBusy(false) }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 520, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>New Video Refine</h2>
          <button onClick={onClose} style={{ color: colors.muted, fontSize: 20 }}>✕</button>
        </div>
        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Project name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My recap"
          style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 14 }} />
        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Source video (for the transcript)</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
          <Button variant="secondary" onClick={pick}>Choose file</Button>
          <input value={sourcePath} onChange={(e) => setSourcePath(e.target.value)} placeholder="/absolute/path/to/video.mp4"
            style={{ flex: 1, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }} />
        </div>
        <Button variant="primary" onClick={create} disabled={busy} loading={busy} style={{ width: "100%", padding: 12, borderRadius: radius.md, fontWeight: fonts.bold }}>
          {busy ? "Creating…" : "Create & open"}
        </Button>
        <p style={{ color: colors.muted, fontSize: fonts.xs, textAlign: "center", marginTop: 10 }}>Opening the project extracts the transcript, then you translate &amp; dub like Voiceover.</p>
      </div>
    </div>
  )
}
