/**
 * ui/src/pages/Voiceover.jsx — the Voiceover section.
 *
 * Two views in one page:
 *   • project list ("My Files")  — when no project is open
 *   • the editor (ProjectDubStudio) — when a project is open
 *
 * Opening a project = setting activeProjectId; "← Projects" clears it.
 * "New Voiceover" creates a project (source + language) and opens it.
 */

import { useState, useEffect, useMemo } from "react"
import { useApp, actions, PAGES } from "../store/app"
import { useNotify } from "../store/notify"
import { listVoiceoverProjects, saveDubSession } from "../api/speech"
import { createProject, deleteProject } from "../api/projects"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import { ProjectDubStudio } from "./DubStudio"

export default function Voiceover() {
  const { state, dispatch } = useApp()
  // Only render the editor for a project THIS section opened. A legacy null
  // section (older sessions) also counts as Voiceover so nothing regresses.
  const owns = state.activeProjectSection === PAGES.VOICEOVER || state.activeProjectSection == null
  const pid = owns ? state.activeProjectId : null

  if (pid != null) {
    return <ProjectDubStudio key={pid} projectId={pid} onBack={() => dispatch(actions.setProject(null))} />
  }
  return <VoiceoverHome onOpen={(id) => dispatch(actions.setProject(id, PAGES.VOICEOVER))} />
}

/* ─────────────────────────────────────────────────────────────────────────────
   Project list ("My Files")
───────────────────────────────────────────────────────────────────────────── */

function VoiceoverHome({ onOpen }) {
  const { notify } = useNotify()
  const [projects, setProjects] = useState(null)
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState("recent")          // recent | name
  const [showNew, setShowNew] = useState(false)
  const [confirmDel, setConfirmDel] = useState(null)

  useEffect(() => {
    let alive = true
    listVoiceoverProjects()
      .then((d) => { if (alive) setProjects(d) })
      .catch((e) => { if (alive) { notify({ severity: "error", message: e.message }); setProjects([]) } })
    return () => { alive = false }
  }, [])

  const rows = useMemo(() => {
    let r = projects || []
    if (query.trim()) r = r.filter((p) => (p.title || "").toLowerCase().includes(query.toLowerCase()))
    r = [...r].sort((a, b) => sort === "name"
      ? (a.title || "").localeCompare(b.title || "")
      : (b.updated_at || 0) - (a.updated_at || 0))
    return r
  }, [projects, query, sort])

  const onCreate = (proj) => { setShowNew(false); setProjects((p) => [proj, ...(p || [])]); onOpen(proj.id) }

  const onDelete = async (p) => {
    try {
      await deleteProject(p.id)
      setProjects((cur) => (cur || []).filter((x) => x.id !== p.id))
      notify({ severity: "success", message: `Deleted “${p.title}”` })
    } catch (e) { notify({ severity: "error", message: e.message }) }
    setConfirmDel(null)
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", background: colors.bg }}>
      {/* Header */}
      <div style={{ padding: "26px 28px 18px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ color: colors.text, fontSize: 26, fontWeight: fonts.bold }}>Voiceover</h1>
          <p style={{ color: colors.muted, fontSize: fonts.sm, marginTop: 4 }}>Turn any audio or video into a translated AI voiceover.</p>
        </div>
        <Button variant="primary" onClick={() => setShowNew(true)} style={{ borderRadius: radius.full, padding: "10px 18px", fontWeight: fonts.bold }}>
          + New Voiceover
        </Button>
      </div>

      {/* Toolbar */}
      <div style={{ padding: "0 28px 14px", display: "flex", gap: 10, alignItems: "center" }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="🔍  Search projects"
          style={{ flex: 1, maxWidth: 360, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }} />
        <select value={sort} onChange={(e) => setSort(e.target.value)}
          style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "9px 12px", borderRadius: radius.md }}>
          <option value="recent">Recently edited</option>
          <option value="name">Name (A–Z)</option>
        </select>
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0 28px 28px" }}>
        <div style={{ border: `1px solid ${colors.border}`, borderRadius: radius.lg, overflow: "hidden", background: colors.panel }}>
          <Row header />
          {projects === null ? (
            <Empty>Loading…</Empty>
          ) : rows.length === 0 ? (
            <Empty>{query ? "No projects match your search." : "No voiceovers yet — click “New Voiceover” to start."}</Empty>
          ) : rows.map((p) => (
            <Row key={p.id} p={p} onOpen={() => onOpen(p.id)} onDelete={() => setConfirmDel(p)} />
          ))}
        </div>
      </div>

      {showNew && <NewVoiceoverModal onClose={() => setShowNew(false)} onCreate={onCreate} />}
      {confirmDel && (
        <ConfirmModal
          title={`Delete “${confirmDel.title}”?`}
          body="The project and its output folder move to the Trash. This can't be undone from here."
          onCancel={() => setConfirmDel(null)}
          onConfirm={() => onDelete(confirmDel)}
        />
      )}
    </div>
  )
}

function Row({ header, p, onOpen, onDelete }) {
  const [hover, setHover] = useState(false)
  const cols = { display: "grid", gridTemplateColumns: "1fr 130px 110px 130px 90px", alignItems: "center", gap: 12, padding: "12px 16px" }
  if (header) {
    return (
      <div style={{ ...cols, borderBottom: `1px solid ${colors.border}`, color: colors.muted, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.06em" }}>
        <div>NAME</div><div>LANGUAGE</div><div>DURATION</div><div>STATUS</div><div style={{ textAlign: "right" }}>ACTIONS</div>
      </div>
    )
  }
  const status = p.has_dub ? { t: "Dub ready", c: colors.success } : p.cue_count ? { t: "Draft", c: colors.warning } : { t: "Empty", c: colors.muted }
  return (
    <div onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ ...cols, borderBottom: `1px solid ${colors.border}`, background: hover ? colors.panel2 : "transparent", cursor: "pointer" }}
      onClick={onOpen}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <span style={{ fontSize: 16 }}>🎙</span>
        <span style={{ color: colors.text, fontWeight: fonts.medium, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title}</span>
      </div>
      <div style={{ color: colors.textDim, fontSize: fonts.sm }}>{p.language || "—"}</div>
      <div style={{ color: colors.textDim, fontSize: fonts.sm, fontVariantNumeric: "tabular-nums" }}>{fmtDur(p.duration)}</div>
      <div><span style={{ color: status.c, fontSize: fonts.sm, fontWeight: fonts.medium }}>● {status.t}</span></div>
      <div style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
        <button onClick={onDelete} title="Delete" style={{ color: colors.muted, padding: "4px 8px", borderRadius: radius.sm }}>🗑</button>
      </div>
    </div>
  )
}

function Empty({ children }) {
  return <div style={{ padding: "40px 16px", textAlign: "center", color: colors.muted, fontSize: fonts.sm }}>{children}</div>
}

/* ─────────────────────────────────────────────────────────────────────────────
   New Voiceover modal (Phase 1)
───────────────────────────────────────────────────────────────────────────── */

const LANGS = ["French", "Spanish", "German", "Italian", "Portuguese", "Japanese", "Korean", "Chinese", "Arabic", "Hindi", "Russian", "English"]

function NewVoiceoverModal({ onClose, onCreate }) {
  const { notify } = useNotify()
  const [name, setName] = useState("")
  const [sourcePath, setSourcePath] = useState("")
  const [sourceLang, setSourceLang] = useState("auto")
  const [translate, setTranslate] = useState(true)
  const [targetLang, setTargetLang] = useState("French")
  const [busy, setBusy] = useState(false)

  const pick = async () => {
    try {
      if (window.pywebview?.api?.pick_file) {
        const path = await window.pywebview.api.pick_file(["Media Files (*.wav;*.mp3;*.m4a;*.mp4;*.mkv;*.mov)"])
        if (path) { setSourcePath(path); if (!name) setName(baseName(path)) }
      } else {
        const path = window.prompt("Absolute path to source media file:")
        if (path) { setSourcePath(path.trim()); if (!name) setName(baseName(path.trim())) }
      }
    } catch (e) { notify({ severity: "error", message: e.message }) }
  }

  const create = async () => {
    const title = (name || baseName(sourcePath) || "Untitled voiceover").trim()
    setBusy(true)
    try {
      const proj = await createProject(title)
      // Seed the session so the editor opens pre-filled with the chosen options.
      await saveDubSession(proj.id, {
        cues: [], ttsAudioUrl: "", targetLang: translate ? targetLang : "English",
        sourcePath, sourceLang, voice: "", reviewed: [], updatedAt: Date.now(),
      })
      onCreate({ id: proj.id, title, language: translate ? targetLang : "English", source_path: sourcePath, cue_count: 0, duration: 0, has_dub: false, updated_at: Date.now() })
    } catch (e) {
      notify({ severity: "error", message: e.message })
    } finally { setBusy(false) }
  }

  return (
    <Backdrop onClose={onClose}>
      <div style={{ width: 560, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 26 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 18 }}>
          <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>New Voiceover</h2>
          <button onClick={onClose} style={{ color: colors.muted, fontSize: 20 }}>✕</button>
        </div>

        <Field label="Project name">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My voiceover"
            style={inp} />
        </Field>

        <Field label="Source media (audio or video)">
          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="secondary" onClick={pick}>Choose file</Button>
            <input value={sourcePath} onChange={(e) => setSourcePath(e.target.value)} placeholder="/absolute/path/to/media.mp4" style={{ ...inp, flex: 1 }} />
          </div>
        </Field>

        <Field label="Source language">
          <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)} style={inp}>
            <option value="auto">Auto-detect</option>
            <option value="English">English</option>
            {LANGS.filter((l) => l !== "English").map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </Field>

        <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "6px 0 12px" }}>
          <Switch on={translate} onClick={() => setTranslate((v) => !v)} />
          <span style={{ color: colors.text, fontSize: fonts.md }}>Translate to another language</span>
        </div>

        {translate && (
          <Field label="Target language">
            <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)} style={inp}>
              {LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </Field>
        )}

        <Button variant="primary" onClick={create} disabled={busy || !sourcePath.trim()} loading={busy}
          style={{ width: "100%", padding: 12, marginTop: 12, borderRadius: radius.md, fontWeight: fonts.bold }}>
          {busy ? "Creating…" : "Create & open"}
        </Button>
        <p style={{ color: colors.muted, fontSize: fonts.xs, textAlign: "center", marginTop: 10 }}>
          You'll generate the voiceover in the next step.
        </p>
      </div>
    </Backdrop>
  )
}

/* ── small shared bits ───────────────────────────────────────────────────── */

const inp = { width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 7 }}>{label}</label>
      {children}
    </div>
  )
}

function Switch({ on, onClick }) {
  return (
    <button onClick={onClick} style={{ width: 38, height: 22, borderRadius: radius.full, background: on ? colors.accent : colors.border, position: "relative", transition: "background 0.15s", flexShrink: 0 }}>
      <span style={{ position: "absolute", top: 2, left: on ? 18 : 2, width: 18, height: 18, borderRadius: "50%", background: "#fff", transition: "left 0.15s" }} />
    </button>
  )
}

function Backdrop({ children, onClose }) {
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
      {children}
    </div>
  )
}

function ConfirmModal({ title, body, onCancel, onConfirm }) {
  return (
    <Backdrop onClose={onCancel}>
      <div style={{ width: 420, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ color: colors.text, fontSize: fonts.lg, fontWeight: fonts.bold, marginBottom: 8 }}>{title}</h3>
        <p style={{ color: colors.muted, fontSize: fonts.sm, marginBottom: 20 }}>{body}</p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="danger" onClick={onConfirm}>Delete</Button>
        </div>
      </div>
    </Backdrop>
  )
}

function fmtDur(s) {
  if (!s) return "—"
  const m = Math.floor(s / 60), sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, "0")}`
}
function baseName(p) {
  return (p || "").split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "") || ""
}
