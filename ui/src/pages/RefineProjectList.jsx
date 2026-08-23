/**
 * ui/src/pages/RefineProjectList.jsx — shared project picker for the Video Refine
 * and Recap Automation sections. Both are the same editor (ProjectDubStudio),
 * differing only by source kind + their "new project" modal, so the list itself
 * — search, sort, and delete (parity with the Voiceover list) — lives here.
 *
 * Deleting a project calls the projects delete endpoint, which moves the whole
 * output/<id> folder (panels, cues, AND the recap character state) to the Trash.
 * That's what keeps a fresh project from ever inheriting an old one's characters.
 */

import { useState, useEffect, useMemo } from "react"
import { useNotify } from "../store/notify"
import { listRefineProjects } from "../api/videoRefine"
import { deleteProject } from "../api/projects"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"

export default function RefineProjectList({ kind, icon, title, subtitle, newLabel, emptyText, onOpen, renderNewModal }) {
  const { notify } = useNotify()
  const [projects, setProjects] = useState(null)
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState("recent")            // recent | name
  const [showNew, setShowNew] = useState(false)
  const [confirmDel, setConfirmDel] = useState(null)

  useEffect(() => {
    let alive = true
    listRefineProjects(kind)
      .then((d) => { if (alive) setProjects(d) })
      .catch((e) => { if (alive) { notify({ severity: "error", message: e.message }); setProjects([]) } })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  const rows = useMemo(() => {
    let r = projects || []
    if (query.trim()) r = r.filter((p) => (p.title || "").toLowerCase().includes(query.toLowerCase()))
    r = [...r].sort((a, b) => sort === "name"
      ? (a.title || "").localeCompare(b.title || "")
      : (b.updated_at || 0) - (a.updated_at || 0))
    return r
  }, [projects, query, sort])

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
          <h1 style={{ color: colors.text, fontSize: 26, fontWeight: fonts.bold }}>{title}</h1>
          <p style={{ color: colors.muted, fontSize: fonts.sm, marginTop: 4 }}>{subtitle}</p>
        </div>
        <Button variant="primary" onClick={() => setShowNew(true)} style={{ borderRadius: radius.full, padding: "10px 18px", fontWeight: fonts.bold }}>{newLabel}</Button>
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
        <div style={{ border: `1px solid ${colors.border}`, borderRadius: radius.lg, overflow: "hidden", background: colors.panel, maxWidth: 760 }}>
          <Row header />
          {projects === null ? (
            <Empty>Loading…</Empty>
          ) : rows.length === 0 ? (
            <Empty>{query ? "No projects match your search." : emptyText}</Empty>
          ) : rows.map((p) => (
            <Row key={p.id} p={p} icon={icon} onOpen={() => onOpen(p.id)} onDelete={() => setConfirmDel(p)} />
          ))}
        </div>
      </div>

      {showNew && renderNewModal({ onClose: () => setShowNew(false), onCreated: (id) => { setShowNew(false); onOpen(id) } })}
      {confirmDel && (
        <ConfirmModal
          title={`Delete “${confirmDel.title}”?`}
          body="The project and its output folder — panels, cues, and character memory — move to the Trash. This can't be undone from here."
          onCancel={() => setConfirmDel(null)}
          onConfirm={() => onDelete(confirmDel)}
        />
      )}
    </div>
  )
}

function Row({ header, p, icon, onOpen, onDelete }) {
  const [hover, setHover] = useState(false)
  const cols = { display: "grid", gridTemplateColumns: "1fr 110px 70px", alignItems: "center", gap: 12, padding: "12px 16px" }
  if (header) {
    return (
      <div style={{ ...cols, borderBottom: `1px solid ${colors.border}`, color: colors.muted, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.06em" }}>
        <div>NAME</div><div>CUES</div><div style={{ textAlign: "right" }}>ACTIONS</div>
      </div>
    )
  }
  return (
    <div onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ ...cols, borderBottom: `1px solid ${colors.border}`, background: hover ? colors.panel2 : "transparent", cursor: "pointer" }}
      onClick={onOpen}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <span style={{ fontSize: 16 }}>{icon}</span>
        <span style={{ color: colors.text, fontWeight: fonts.medium, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title}</span>
      </div>
      <div style={{ color: colors.textDim, fontSize: fonts.sm, fontVariantNumeric: "tabular-nums" }}>{p.cue_count || 0}</div>
      <div style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
        <button onClick={onDelete} title="Delete" style={{ color: colors.muted, padding: "4px 8px", borderRadius: radius.sm }}>🗑</button>
      </div>
    </div>
  )
}

function Empty({ children }) {
  return <div style={{ padding: "40px 16px", textAlign: "center", color: colors.muted, fontSize: fonts.sm }}>{children}</div>
}

function ConfirmModal({ title, body, onCancel, onConfirm }) {
  return (
    <div onClick={onCancel} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
      <div style={{ width: 420, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ color: colors.text, fontSize: fonts.lg, fontWeight: fonts.bold, marginBottom: 8 }}>{title}</h3>
        <p style={{ color: colors.muted, fontSize: fonts.sm, marginBottom: 20 }}>{body}</p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="danger" onClick={onConfirm}>Delete</Button>
        </div>
      </div>
    </div>
  )
}
