/**
 * ui/src/pages/Library.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Full Library screen.  Two-column layout:
 *
 *   Left  (260px)  — project list with create / delete
 *   Right (flex 1) — episodes for the selected project with import / delete
 *
 * Clicking an episode dispatches setEpisode() which navigates to Pipeline.
 */

import { useState, useEffect, useCallback } from "react"
import { useApp, actions }                  from "../store/app"
import { listProjects, createProject, deleteProject } from "../api/projects"
import { listEpisodes, createEpisode, deleteEpisode } from "../api/episodes"
import { colors, fonts, radius, shadows, status as stageStatus } from "../theme"
import Button                from "../components/Button"
import Modal, { FormField, TextInput, TextArea } from "../components/Modal"
import { useResizableRail, RailDragHandle } from "../components/Rail"

// ── Stage display config per source type ─────────────────────────────────────

// Keyed by episodes-table column (the /api/episodes payload keys stages by
// DB column).  "tts" removed — TTS is folded into the "dub" stage.
const STAGE_MAP = {
  video:       ["detect", "extract", "translate", "dub", "sync", "assemble"],
  pdf:         ["extract", "narrate", "translate", "dub", "assemble"],
  screenshots: ["upscale", "narrate", "translate", "dub", "assemble"],
}

const STAGE_SHORT = {
  detect:    "DETECT",
  extract:   "EXTRACT",
  narrate:   "NARRATE",
  upscale:   "UPSCALE",
  translate: "TRANSL",
  dub:       "DUB",
  sync:      "SYNC",
  assemble:  "ASSEMBLE",
}


// ══════════════════════════════════════════════════════════════════════════════
// ROOT PAGE
// ══════════════════════════════════════════════════════════════════════════════

export default function Library() {
  const { state, dispatch } = useApp()

  // ── Data ──────────────────────────────────────────────────────────────────
  const [projects,        setProjects]        = useState([])
  const [episodes,        setEpisodes]        = useState([])
  const [loadingProjects, setLoadingProjects] = useState(false)
  const [loadingEpisodes, setLoadingEpisodes] = useState(false)
  const [error,           setError]           = useState(null)

  // ── Modal state ───────────────────────────────────────────────────────────
  const [showCreateProject, setShowCreateProject] = useState(false)
  const [showImportEpisode, setShowImportEpisode] = useState(false)
  const [deleteTarget,      setDeleteTarget]      = useState(null)
    // { type: "project"|"episode", item: {...} }

  // ── Fetch projects ────────────────────────────────────────────────────────
  const fetchProjects = useCallback(async () => {
    setLoadingProjects(true)
    setError(null)
    try {
      const data = await listProjects()
      setProjects(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingProjects(false)
    }
  }, [])

  useEffect(() => { fetchProjects() }, [fetchProjects])

  // ── Fetch episodes when selected project changes ──────────────────────────
  const fetchEpisodes = useCallback(async (projectId) => {
    if (!projectId) { setEpisodes([]); return }
    setLoadingEpisodes(true)
    try {
      const data = await listEpisodes(projectId)
      setEpisodes(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingEpisodes(false)
    }
  }, [])

  useEffect(() => {
    fetchEpisodes(state.activeProjectId)
  }, [state.activeProjectId, fetchEpisodes])

  // ── Active project object ─────────────────────────────────────────────────
  const activeProject = projects.find(p => p.id === state.activeProjectId) || null

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSelectProject = (project) => {
    dispatch(actions.setProject(project.id))
  }

  const handleOpenEpisode = (episode) => {
    dispatch(actions.setEpisode(episode))
  }

  const handleProjectCreated = (project) => {
    setProjects(prev => [project, ...prev])
    dispatch(actions.setProject(project.id))
    setShowCreateProject(false)
  }

  const handleEpisodeImported = (episode) => {
    setEpisodes(prev => [episode, ...prev])
    setShowImportEpisode(false)
  }

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return
    try {
      if (deleteTarget.type === "project") {
        await deleteProject(deleteTarget.item.id)
        setProjects(prev => prev.filter(p => p.id !== deleteTarget.item.id))
        if (state.activeProjectId === deleteTarget.item.id) {
          dispatch(actions.setProject(null))
          setEpisodes([])
        }
      } else {
        await deleteEpisode(deleteTarget.item.id)
        setEpisodes(prev => prev.filter(e => e.id !== deleteTarget.item.id))
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setDeleteTarget(null)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>

      {/* ── Projects panel (left) ─────────────────────────────────────── */}
      <ProjectsPanel
        projects         = {projects}
        activeProjectId  = {state.activeProjectId}
        loading          = {loadingProjects}
        onSelect         = {handleSelectProject}
        onDelete         = {(p) => setDeleteTarget({ type: "project", item: p })}
        onCreateClick    = {() => setShowCreateProject(true)}
      />

      {/* ── Episodes panel (right) ────────────────────────────────────── */}
      <EpisodesPanel
        project        = {activeProject}
        episodes       = {episodes}
        loading        = {loadingEpisodes}
        onOpenEpisode  = {handleOpenEpisode}
        onDeleteEpisode= {(e) => setDeleteTarget({ type: "episode", item: e })}
        onImportClick  = {() => setShowImportEpisode(true)}
      />

      {/* ── Error toast ───────────────────────────────────────────────── */}
      {error && (
        <div style={{
          position:  "fixed",
          bottom:    24,
          left:      "50%",
          transform: "translateX(-50%)",
          background: colors.error,
          color:     "#000",
          padding:   "10px 20px",
          borderRadius: radius.md,
          fontSize:  fonts.sm,
          fontWeight: fonts.medium,
          zIndex:    2000,
          boxShadow: shadows.md,
        }}>
          {error}
          <button
            onClick={() => setError(null)}
            style={{ marginLeft: 12, opacity: 0.7, cursor: "pointer",
                     background: "none", border: "none", color: "#000" }}
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Create project modal ───────────────────────────────────────── */}
      <CreateProjectModal
        open      = {showCreateProject}
        onClose   = {() => setShowCreateProject(false)}
        onCreated = {handleProjectCreated}
      />

      {/* ── Import episode modal ───────────────────────────────────────── */}
      {activeProject && (
        <ImportEpisodeModal
          open       = {showImportEpisode}
          project    = {activeProject}
          onClose    = {() => setShowImportEpisode(false)}
          onImported = {handleEpisodeImported}
        />
      )}

      {/* ── Delete confirm modal ───────────────────────────────────────── */}
      <Modal
        open           = {!!deleteTarget}
        title          = {deleteTarget?.type === "project" ? "Delete Project" : "Delete Episode"}
        subtitle       = {
          deleteTarget?.type === "project"
            ? "This will permanently delete the project and all its episodes."
            : "This will delete the episode record. Output files on disk are not removed."
        }
        onClose        = {() => setDeleteTarget(null)}
        onConfirm      = {handleDeleteConfirm}
        confirmLabel   = "Delete"
        confirmVariant = "danger"
        width          = {420}
      >
        <p style={{ color: colors.textDim, fontSize: fonts.base }}>
          Are you sure you want to delete{" "}
          <strong style={{ color: colors.text }}>
            "{deleteTarget?.item?.title}"
          </strong>?
          {" "}This cannot be undone.
        </p>
      </Modal>

    </div>
  )
}


// ══════════════════════════════════════════════════════════════════════════════
// PROJECTS PANEL
// ══════════════════════════════════════════════════════════════════════════════

function ProjectsPanel({ projects, activeProjectId, loading, onSelect, onDelete, onCreateClick }) {
  const rail = useResizableRail({ storageKey: "ms_library", defaultWidth: 260, min: 190, max: 380 })
  return (
    <div style={{
      width:        rail.width,
      minWidth:     rail.width,
      height:       "100%",
      background:   colors.panel,
      borderRight:  `1px solid ${colors.border}`,
      display:      "flex",
      flexDirection:"column",
      flexShrink:   0,
      position:     "relative",
    }}>
      {/* Header */}
      {rail.collapsed ? (
        <div style={{ padding: "14px 0 10px", borderBottom: `1px solid ${colors.border}`, display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
          <button onClick={rail.toggle} title="Expand projects" aria-label="Expand projects"
            style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 18 }}>»</button>
          <button onClick={onCreateClick} title="New project" aria-label="New project"
            style={{ width: 30, height: 28, borderRadius: radius.sm, border: "none", background: colors.accent, color: "#000", cursor: "pointer", fontSize: 16 }}>+</button>
        </div>
      ) : (
        <div style={{ padding: "16px 16px 12px", borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button onClick={rail.toggle} title="Collapse projects" aria-label="Collapse projects"
              style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 16 }}>«</button>
            <span style={{ color: colors.textDim, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.1em" }}>PROJECTS</span>
          </div>
          <Button variant="primary" size="sm" onClick={onCreateClick}>+ New</Button>
        </div>
      )}

      {/* List */}
      <div style={{ flex: 1, overflow: "auto", padding: "6px" }}>
        {loading && !rail.collapsed && <Spinner label="Loading projects…" />}

        {!loading && !rail.collapsed && projects.length === 0 && (
          <EmptyState
            title="No projects yet"
            message='Click "+ New" to create your first project.'
          />
        )}

        {rail.collapsed
          ? projects.map(project => (
              <button key={project.id} onClick={() => onSelect(project)} title={project.name}
                style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 38, height: 38, margin: "4px auto",
                  borderRadius: radius.md, cursor: "pointer", fontSize: fonts.sm, fontWeight: fonts.bold,
                  border: `1px solid ${project.id === activeProjectId ? colors.accent : colors.border}`,
                  background: project.id === activeProjectId ? "rgba(255,107,53,0.12)" : "transparent",
                  color: project.id === activeProjectId ? colors.accent : colors.textDim }}>
                {(project.name || "?").trim().charAt(0).toUpperCase()}
              </button>
            ))
          : projects.map(project => (
              <ProjectRow
                key      = {project.id}
                project  = {project}
                active   = {project.id === activeProjectId}
                onSelect = {() => onSelect(project)}
                onDelete = {() => onDelete(project)}
              />
            ))}
      </div>
      {!rail.collapsed && <RailDragHandle onMouseDown={rail.onDragStart} />}
    </div>
  )
}


function ProjectRow({ project, active, onSelect, onDelete }) {
  const [hovered, setHovered] = useState(false)

  return (
    <div
      onClick           = {onSelect}
      onMouseEnter      = {() => setHovered(true)}
      onMouseLeave      = {() => setHovered(false)}
      style={{
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        padding:        "9px 10px",
        borderRadius:   radius.sm,
        cursor:         "pointer",
        background:     active
          ? `rgba(255,107,53,0.12)`
          : hovered
          ? `rgba(255,255,255,0.04)`
          : "transparent",
        transition:     "background 0.1s",
      }}
    >
      <div style={{ overflow: "hidden" }}>
        <div style={{
          color:        active ? colors.accent : colors.text,
          fontSize:     fonts.base,
          fontWeight:   active ? fonts.medium : fonts.normal,
          overflow:     "hidden",
          textOverflow: "ellipsis",
          whiteSpace:   "nowrap",
        }}>
          {project.title}
        </div>
        <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 2 }}>
          {project.episode_count} episode{project.episode_count !== 1 ? "s" : ""}
        </div>
      </div>

      {/* Delete button — only visible on hover */}
      {hovered && (
        <button
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          style={{
            background: "none",
            border:     "none",
            color:      colors.muted,
            cursor:     "pointer",
            padding:    "2px 4px",
            borderRadius: radius.sm,
            fontSize:   fonts.md,
            lineHeight: 1,
            flexShrink: 0,
          }}
          title="Delete project"
        >
          ✕
        </button>
      )}
    </div>
  )
}


// ══════════════════════════════════════════════════════════════════════════════
// EPISODES PANEL
// ══════════════════════════════════════════════════════════════════════════════

function EpisodesPanel({ project, episodes, loading, onOpenEpisode, onDeleteEpisode, onImportClick }) {
  if (!project) {
    return (
      <div style={{
        flex:           1,
        display:        "flex",
        alignItems:     "center",
        justifyContent: "center",
      }}>
        <EmptyState
          title="No project selected"
          message="Select a project on the left to view its episodes."
        />
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

      {/* Header */}
      <div style={{
        padding:        "16px 24px 14px",
        borderBottom:   `1px solid ${colors.border}`,
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        flexShrink:     0,
      }}>
        <div>
          <div style={{ color: colors.text, fontSize: fonts.lg, fontWeight: fonts.bold }}>
            {project.title}
          </div>
          <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 2 }}>
            {project.episode_count} episode{project.episode_count !== 1 ? "s" : ""}
          </div>
        </div>
        <Button variant="primary" size="md" onClick={onImportClick}>
          + Import Episode
        </Button>
      </div>

      {/* Episode list */}
      <div style={{ flex: 1, overflow: "auto", padding: "16px 20px" }}>
        {loading && <Spinner label="Loading episodes…" />}

        {!loading && episodes.length === 0 && (
          <EmptyState
            title="No episodes yet"
            message='Click "+ Import Episode" to add your first video, PDF, or screenshots.'
          />
        )}

        {episodes.map(ep => (
          <EpisodeCard
            key      = {ep.id}
            episode  = {ep}
            onOpen   = {() => onOpenEpisode(ep)}
            onDelete = {() => onDeleteEpisode(ep)}
          />
        ))}
      </div>
    </div>
  )
}


function EpisodeCard({ episode, onOpen, onDelete }) {
  const [hovered, setHovered] = useState(false)

  const stages     = episode.stages || {}
  const stageKeys  = STAGE_MAP[episode.source_type] || STAGE_MAP.video
  const typeColor  = episode.source_type === "video" ? colors.info
                   : episode.source_type === "pdf"   ? colors.warning
                   :                                   colors.success

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background:   hovered ? colors.panel2 : colors.panel,
        border:       `1px solid ${hovered ? colors.border : "transparent"}`,
        borderRadius: radius.lg,
        padding:      "16px 20px",
        marginBottom: "10px",
        transition:   "background 0.12s, border-color 0.12s",
        cursor:       "default",
      }}
    >
      {/* Top row: title + type badge + delete */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ flex: 1, marginRight: 12 }}>
          <div style={{
            color:      colors.text,
            fontSize:   fonts.md,
            fontWeight: fonts.medium,
            lineHeight: 1.3,
          }}>
            {episode.title}
          </div>
          {episode.source_path && (
            <div style={{
              color:        colors.muted,
              fontSize:     fonts.xs,
              marginTop:    3,
              overflow:     "hidden",
              textOverflow: "ellipsis",
              whiteSpace:   "nowrap",
              maxWidth:     "420px",
            }}>
              {episode.source_path}
            </div>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {/* Source type badge */}
          <span style={{
            background:   `${typeColor}18`,
            color:        typeColor,
            border:       `1px solid ${typeColor}40`,
            borderRadius: radius.full,
            padding:      "2px 9px",
            fontSize:     fonts.xs,
            fontWeight:   fonts.bold,
            letterSpacing:"0.05em",
          }}>
            {episode.source_type.toUpperCase()}
          </span>
          {/* Delete */}
          <button
            onClick={onDelete}
            style={{
              background:   "none",
              border:       "none",
              color:        hovered ? colors.muted : "transparent",
              cursor:       "pointer",
              fontSize:     fonts.sm,
              padding:      "2px 4px",
              borderRadius: radius.sm,
              transition:   "color 0.12s",
            }}
            title="Delete episode"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Progress bar */}
      <div style={{ marginBottom: 10 }}>
        <div style={{
          display:        "flex",
          justifyContent: "space-between",
          marginBottom:   5,
        }}>
          <span style={{ color: colors.textDim, fontSize: fonts.xs }}>Overall progress</span>
          <span style={{ color: colors.textDim, fontSize: fonts.xs }}>{episode.overall}%</span>
        </div>
        <div style={{ height: 4, background: colors.panel2, borderRadius: 2 }}>
          <div style={{
            height:       "100%",
            width:        `${episode.overall}%`,
            background:   episode.overall === 100 ? colors.success : colors.accent,
            borderRadius: 2,
            transition:   "width 0.3s ease",
          }} />
        </div>
      </div>

      {/* Stage dots */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
        {stageKeys.map(key => {
          const info   = stages[key] || { status: "pending", progress: 0 }
          const color  = stageStatus.color[info.status] || colors.muted
          const icon   = stageStatus.icon[info.status]  || "○"
          return (
            <span
              key   = {key}
              title = {`${STAGE_SHORT[key] || key}: ${info.status} (${info.progress}%)`}
              style={{
                display:     "inline-flex",
                alignItems:  "center",
                gap:         4,
                color:       color,
                fontSize:    fonts.xs,
                background:  `${color}12`,
                padding:     "2px 7px",
                borderRadius: radius.full,
              }}
            >
              {icon} {STAGE_SHORT[key] || key}
            </span>
          )
        })}
      </div>

      {/* Open button */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Button variant="primary" size="sm" onClick={onOpen}>
          Open in Pipeline →
        </Button>
      </div>
    </div>
  )
}


// ══════════════════════════════════════════════════════════════════════════════
// MODALS
// ══════════════════════════════════════════════════════════════════════════════

function CreateProjectModal({ open, onClose, onCreated }) {
  const [form,   setForm]   = useState({ title: "", notes: "" })
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState("")

  const handleSubmit = async () => {
    if (!form.title.trim()) { setError("Title is required"); return }
    setSaving(true)
    setError("")
    try {
      const project = await createProject(form.title, form.notes)
      onCreated(project)
      setForm({ title: "", notes: "" })
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleClose = () => {
    setForm({ title: "", notes: "" })
    setError("")
    onClose()
  }

  return (
    <Modal
      open           = {open}
      title          = "New Project"
      subtitle       = "A project groups related episodes together."
      onClose        = {handleClose}
      onConfirm      = {handleSubmit}
      confirmLabel   = "Create Project"
      confirmLoading = {saving}
      width          = {460}
    >
      <FormField label="Project name" error={error}>
        <TextInput
          autoFocus
          value       = {form.title}
          onChange    = {e => { setForm(f => ({ ...f, title: e.target.value })); setError("") }}
          onKeyDown   = {e => e.key === "Enter" && handleSubmit()}
          placeholder = "e.g. Solo Leveling"
          maxLength   = {200}
        />
      </FormField>
      <FormField label="Notes" hint="Optional">
        <TextArea
          value       = {form.notes}
          onChange    = {e => setForm(f => ({ ...f, notes: e.target.value }))}
          placeholder = "Any notes about this project…"
          rows        = {3}
        />
      </FormField>
    </Modal>
  )
}


function ImportEpisodeModal({ open, project, onClose, onImported }) {
  const [form, setForm] = useState({
    title:       "",
    source_type: "video",
    source_path: "",
    tone_prompt: "",
  })
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState({})

  // Native file/folder picker via pywebview (only in desktop window)
  const canPickFile = typeof window.pywebview !== "undefined"

  const handleBrowseFile = async () => {
    if (!canPickFile) return
    const fileTypes =
      form.source_type === "video"
        ? ["Video Files (*.mp4;*.mkv;*.avi;*.mov;*.webm)", "All Files (*.*)"]
        : ["PDF Files (*.pdf)", "All Files (*.*)"]
    try {
      const path = await window.pywebview.api.pick_file(fileTypes)
      if (path) setForm(f => ({ ...f, source_path: path }))
    } catch (e) { /* ignored — user cancelled */ }
  }

  const handleBrowseFolder = async () => {
    if (!canPickFile) return
    try {
      const path = await window.pywebview.api.pick_folder()
      if (path) setForm(f => ({ ...f, source_path: path }))
    } catch (e) { /* ignored — user cancelled */ }
  }

  const validate = () => {
    const e = {}
    if (!form.title.trim())       e.title       = "Title is required"
    if (!form.source_path.trim()) e.source_path = "File path is required"
    return e
  }

  const handleSubmit = async () => {
    const e = validate()
    if (Object.keys(e).length) { setErrors(e); return }

    setSaving(true)
    setErrors({})
    try {
      const episode = await createEpisode({
        project_id:  project.id,
        title:       form.title.trim(),
        source_type: form.source_type,
        source_path: form.source_path.trim(),
        tone_prompt: form.tone_prompt.trim(),
      })
      onImported(episode)
      setForm({ title: "", source_type: "video", source_path: "", tone_prompt: "" })
    } catch (err) {
      setErrors({ api: err.message })
    } finally {
      setSaving(false)
    }
  }

  const handleClose = () => {
    setForm({ title: "", source_type: "video", source_path: "", tone_prompt: "" })
    setErrors({})
    onClose()
  }

  const isFolder = form.source_type === "screenshots"

  return (
    <Modal
      open           = {open}
      title          = "Import Episode"
      subtitle       = {`Adding to: ${project?.title}`}
      onClose        = {handleClose}
      onConfirm      = {handleSubmit}
      confirmLabel   = "Import"
      confirmLoading = {saving}
      width          = {520}
    >
      {/* Source type selector */}
      <FormField label="Source type">
        <div style={{ display: "flex", gap: 8 }}>
          {[
            { value: "video",       label: "Video",       hint: "MP4, MKV, AVI…" },
            { value: "pdf",         label: "PDF",         hint: "Manga PDF"       },
            { value: "screenshots", label: "Screenshots", hint: "Folder of images"},
          ].map(({ value, label, hint }) => (
            <TypeButton
              key      = {value}
              label    = {label}
              hint     = {hint}
              selected = {form.source_type === value}
              onClick  = {() => setForm(f => ({ ...f, source_type: value, source_path: "" }))}
            />
          ))}
        </div>
      </FormField>

      {/* Episode title */}
      <FormField label="Episode title" error={errors.title}>
        <TextInput
          value       = {form.title}
          onChange    = {e => { setForm(f => ({ ...f, title: e.target.value })); setErrors(x => ({ ...x, title: "" })) }}
          placeholder = "e.g. Chapter 1 — The Awakening"
          maxLength   = {200}
        />
      </FormField>

      {/* File / folder path */}
      <FormField
        label = {isFolder ? "Screenshots folder" : "Source file"}
        error = {errors.source_path || errors.api}
        hint  = {!canPickFile ? "Paste the full path" : undefined}
      >
        <div style={{ display: "flex", gap: 8 }}>
          <TextInput
            value       = {form.source_path}
            onChange    = {e => { setForm(f => ({ ...f, source_path: e.target.value })); setErrors(x => ({ ...x, source_path: "", api: "" })) }}
            placeholder = {isFolder ? "/path/to/screenshots/folder" : "/path/to/file.mp4"}
            style       = {{ flex: 1 }}
          />
          {canPickFile && (
            <Button
              variant = "ghost"
              size    = "md"
              onClick = {isFolder ? handleBrowseFolder : handleBrowseFile}
              style   = {{ flexShrink: 0 }}
            >
              Browse
            </Button>
          )}
        </div>
      </FormField>

      {/* Tone prompt */}
      <FormField label="Narration tone" hint="Optional — used by REFINE and TRANSLATE stages">
        <TextArea
          value       = {form.tone_prompt}
          onChange    = {e => setForm(f => ({ ...f, tone_prompt: e.target.value }))}
          placeholder = "e.g. Dramatic, intense storytelling. Reference the character by name when possible."
          rows        = {3}
        />
      </FormField>
    </Modal>
  )
}


// ── TypeButton (source type selector) ────────────────────────────────────────

function TypeButton({ label, hint, selected, onClick }) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      onClick       = {onClick}
      onMouseEnter  = {() => setHovered(true)}
      onMouseLeave  = {() => setHovered(false)}
      style={{
        flex:         1,
        padding:      "10px 8px",
        borderRadius: radius.md,
        border:       `1px solid ${selected ? colors.accent : hovered ? colors.textDim : colors.border}`,
        background:   selected ? `rgba(255,107,53,0.1)` : hovered ? colors.panel2 : "transparent",
        color:        selected ? colors.accent : colors.textDim,
        cursor:       "pointer",
        textAlign:    "center",
        transition:   "all 0.12s",
      }}
    >
      <div style={{ fontWeight: fonts.medium, fontSize: fonts.sm }}>{label}</div>
      <div style={{ fontSize: fonts.xs, opacity: 0.7, marginTop: 2 }}>{hint}</div>
    </button>
  )
}


// ══════════════════════════════════════════════════════════════════════════════
// SHARED UTILITIES
// ══════════════════════════════════════════════════════════════════════════════

function EmptyState({ title, message }) {
  return (
    <div style={{
      display:        "flex",
      flexDirection:  "column",
      alignItems:     "center",
      justifyContent: "center",
      padding:        "48px 24px",
      textAlign:      "center",
      color:          colors.muted,
      gap:            8,
    }}>
      <div style={{ fontSize: 28, opacity: 0.4 }}>⊡</div>
      <div style={{ color: colors.textDim, fontSize: fonts.base, fontWeight: fonts.medium }}>
        {title}
      </div>
      <div style={{ fontSize: fonts.sm, lineHeight: 1.5, maxWidth: 260 }}>
        {message}
      </div>
    </div>
  )
}

function Spinner({ label }) {
  return (
    <div style={{
      display:        "flex",
      alignItems:     "center",
      justifyContent: "center",
      gap:            10,
      padding:        "32px",
      color:          colors.muted,
      fontSize:       fonts.sm,
    }}>
      <span style={{
        display:      "inline-block",
        width:        14,
        height:       14,
        border:       `2px solid ${colors.border}`,
        borderTopColor: colors.accent,
        borderRadius: "50%",
        animation:    "ms-spin 0.7s linear infinite",
      }} />
      {label}
    </div>
  )
}
