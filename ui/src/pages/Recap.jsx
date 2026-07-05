/**
 * ui/src/pages/Recap.jsx — the Recap Automation section.
 *
 * PDF-sourced recaps: create a project from a manga/manhwa PDF, stack up crops
 * chapter by chapter (they stay visible, numbered), send them in small batches
 * to the vision model (GitHub Models GPT-4.1 by default) → each crop becomes a
 * cue with its panel attached and an AI narration line. From there it's the
 * same shared editor as Voiceover/Video Refine: AI Refine → English dub (the
 * timeline baseline) → add languages → translate → dub.
 */

import { useState, useEffect } from "react"
import { useApp, actions, PAGES } from "../store/app"
import { useNotify } from "../store/notify"
import { listRefineProjects, createRefineProject } from "../api/videoRefine"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import { ProjectDubStudio } from "./DubStudio"

export default function Recap() {
  const { state, dispatch } = useApp()
  // Only OUR project — the other sections share activeProjectId but tag it with
  // the section that opened it.
  const pid = state.activeProjectSection === PAGES.RECAP ? state.activeProjectId : null
  if (pid != null) {
    return <ProjectDubStudio key={pid} projectId={pid} refine onBack={() => dispatch(actions.setProject(null))} />
  }
  return <RecapHome onOpen={(id) => dispatch(actions.setProject(id, PAGES.RECAP))} />
}

function RecapHome({ onOpen }) {
  const { notify } = useNotify()
  const [projects, setProjects] = useState(null)
  const [showNew, setShowNew] = useState(false)

  useEffect(() => {
    listRefineProjects("recap")
      .then(setProjects)
      .catch((e) => { notify({ severity: "error", message: e.message }); setProjects([]) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div style={{ flex: 1, overflowY: "auto", background: colors.bg, padding: "26px 28px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", maxWidth: 760 }}>
        <div>
          <h1 style={{ color: colors.text, fontSize: 26, fontWeight: fonts.bold }}>Recap Automation</h1>
          <p style={{ color: colors.muted, fontSize: fonts.sm, marginTop: 4 }}>
            PDF in → crop the panels → AI narrates each crop into a cue → refine, dub, translate.
          </p>
        </div>
        <Button variant="primary" onClick={() => setShowNew(true)} style={{ borderRadius: radius.full, padding: "10px 18px", fontWeight: fonts.bold }}>+ New Recap</Button>
      </div>
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: radius.lg, overflow: "hidden", background: colors.panel, maxWidth: 760, marginTop: 18 }}>
        {projects === null ? (
          <div style={{ padding: 40, textAlign: "center", color: colors.muted }}>Loading…</div>
        ) : projects.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: colors.muted }}>No recaps yet — click “New Recap”.</div>
        ) : projects.map((p) => (
          <button key={p.id} onClick={() => onOpen(p.id)}
            style={{ display: "flex", width: "100%", alignItems: "center", justifyContent: "space-between", padding: "14px 16px", borderBottom: `1px solid ${colors.border}`, background: "transparent", color: colors.text, textAlign: "left" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 10 }}><span>🪄</span><span style={{ fontWeight: fonts.medium }}>{p.title}</span></span>
            <span style={{ color: colors.muted, fontSize: fonts.sm }}>{p.cue_count || 0} cues ›</span>
          </button>
        ))}
      </div>
      {showNew && <NewRecapModal onClose={() => setShowNew(false)} onCreated={(id) => { setShowNew(false); onOpen(id) }} />}
    </div>
  )
}

function NewRecapModal({ onClose, onCreated }) {
  const { notify } = useNotify()
  const [name, setName] = useState("")
  const [pdfPath, setPdfPath] = useState("")
  const [busy, setBusy] = useState(false)

  const pick = async () => {
    try {
      if (window.pywebview?.api?.pick_file) {
        const p = await window.pywebview.api.pick_file(["PDF (*.pdf)"])
        if (p) { setPdfPath(p); if (!name) setName((p.split(/[\\/]/).pop() || "").replace(/\.[^.]+$/, "")) }
      } else {
        const p = window.prompt("Absolute path to the manga PDF:")
        if (p) setPdfPath(p.trim())
      }
    } catch (e) { notify({ severity: "error", message: e.message }) }
  }

  const create = async () => {
    if (!pdfPath.trim()) { notify({ severity: "error", message: "Pick the manga PDF first." }); return }
    setBusy(true)
    try {
      const res = await createRefineProject((name || "Untitled recap").trim(), pdfPath.trim(), "recap")
      onCreated(res.id)
    } catch (e) { notify({ severity: "error", message: e.message }) }
    finally { setBusy(false) }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 520, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>New Recap</h2>
          <button onClick={onClose} style={{ color: colors.muted, fontSize: 20 }}>✕</button>
        </div>
        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Project name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Solo Leveling — ch. 1-10"
          style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 14 }} />
        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Manga PDF</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
          <Button variant="secondary" onClick={pick}>Choose PDF</Button>
          <input value={pdfPath} onChange={(e) => setPdfPath(e.target.value)} placeholder="/absolute/path/to/chapter.pdf"
            style={{ flex: 1, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }} />
        </div>
        <Button variant="primary" onClick={create} disabled={busy || !pdfPath.trim()} loading={busy} style={{ width: "100%", padding: 12, borderRadius: radius.md, fontWeight: fonts.bold }}>
          {busy ? "Creating…" : "Create & open"}
        </Button>
        <p style={{ color: colors.muted, fontSize: fonts.xs, textAlign: "center", marginTop: 10 }}>Opens straight into cropping — stack crops, then 🪄 Narrate turns them into cues.</p>
      </div>
    </div>
  )
}
