/**
 * ui/src/pages/DubStudio.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Dub Studio — the per-cue dubbing editor.
 *
 *   SetupScreen     pick source media + target language → extract & translate
 *   AdvancedEditor  three-pane editor:
 *                     • left   cue list (original + editable translation)
 *                     • right  seekable video player + dub-audio + volumes
 *                     • bottom waveform-style timeline with live playhead
 *
 * Source media is streamed through GET /api/media (mediaSrc) because the /files
 * mount only exposes OUTPUT_DIR — see api/panels.js + scripts/api/routers/media.py.
 */

import { useState, useEffect, useRef, useCallback, useMemo, forwardRef } from "react"
import { listVoices, quickTTS, quickTTSStatus } from "../api/voices"
import { startAdhocTranslate, getAdhocTranslateStatus, startAdhocSync, getAdhocSyncStatus, refineCue, getDubSession, saveDubSession, exportDub } from "../api/speech"
import { listProjects } from "../api/projects"
import { useApp, actions } from "../store/app"
import { useNotify } from "../store/notify"
import { FILES_ORIGIN, mediaSrc } from "../api/panels"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * Dub Studio is scoped to the project selected in the Library.  Each project's
 * session (cues, source, voice, generated-audio URL, reviewed marks) lives on
 * disk in its output folder, so closing the app and reopening the project
 * restores everything — see scripts/api/routers/speech.py (dub-session).
 */
export default function DubStudio() {
  const { state } = useApp()
  const projectId = state.activeProjectId
  if (!projectId) return <NoProjectScreen />
  // key forces a clean remount when switching projects.
  return <ProjectDubStudio key={projectId} projectId={projectId} />
}

function ProjectDubStudio({ projectId }) {
  const { notify } = useNotify()
  const [loaded, setLoaded] = useState(false)
  const [cues, setCues] = useState(null)
  const [ttsAudioUrl, setTtsAudioUrl] = useState("")
  const [targetLang, setTargetLang] = useState("French")
  const [sourcePath, setSourcePath] = useState("")
  const [voice, setVoice] = useState("")
  const [reviewed, setReviewed] = useState([])

  // Load this project's saved session (falling back to migrating the old global
  // localStorage session the first time, so nobody loses in-progress work).
  useEffect(() => {
    let alive = true
    getDubSession(projectId).then((s) => {
      if (!alive) return
      if (s && Array.isArray(s.cues) && s.cues.length) {
        setCues(s.cues)
        setTtsAudioUrl(s.ttsAudioUrl || "")
        setTargetLang(s.targetLang || "French")
        setSourcePath(s.sourcePath || "")
        setVoice(s.voice || "")
        setReviewed(Array.isArray(s.reviewed) ? s.reviewed : [])
      } else {
        try {
          const old = JSON.parse(localStorage.getItem("dub_cues") || "null")
          if (Array.isArray(old) && old.length) {
            setCues(old)
            setTtsAudioUrl(localStorage.getItem("dub_ttsAudioUrl") || "")
            setTargetLang(localStorage.getItem("dub_targetLang") || "French")
            setSourcePath(localStorage.getItem("dub_sourcePath") || "")
            setVoice(localStorage.getItem("dub_voice") || "")
          }
        } catch { /* ignore */ }
      }
      setLoaded(true)
    }).catch((e) => {
      notify({ severity: "error", message: `Could not load session: ${e.message}` })
      setLoaded(true)
    })
    return () => { alive = false }
  }, [projectId])

  // Auto-save (debounced) whenever anything persistent changes.
  useEffect(() => {
    if (!loaded) return
    const t = setTimeout(() => {
      saveDubSession(projectId, {
        cues: cues || [], ttsAudioUrl, targetLang, sourcePath, voice, reviewed,
        updatedAt: Date.now(),
      }).catch(() => { /* best-effort; next change retries */ })
    }, 700)
    return () => clearTimeout(t)
  }, [loaded, cues, ttsAudioUrl, targetLang, sourcePath, voice, reviewed, projectId])

  // Voices + the shared dub-generation pipeline (TTS → sync). Used by BOTH the
  // setup screen's "Start Dubbing" and the editor's "Regenerate Dub", so a fresh
  // project dubs automatically right after extraction.
  const [voices, setVoices] = useState([])
  const [busy, setBusy] = useState(false)
  const [statusMsg, setStatusMsg] = useState("")

  useEffect(() => {
    listVoices().then((v) => {
      setVoices(v)
      setVoice((cur) => cur || (v[0]?.name ?? ""))
    }).catch((e) => console.error(e))
  }, [])

  const generateDub = useCallback(async (cuesArg) => {
    const useCues = cuesArg || cues
    if (!useCues || !useCues.length) return
    const useVoice = voice || voices[0]?.name
    if (!useVoice) { notify({ severity: "error", message: "No voice available — add one under Voices first." }); return }
    setBusy(true)
    try {
      setStatusMsg("Generating voiceover (TTS)…")
      // Prepend a throwaway "warm-up" line: TTS models are unstable on their
      // very first utterance (the breath/hiccup you hear). The model warms up on
      // this line; sync then strips it (see lead_dummy) so the first REAL cue
      // starts clean. Keep it ONE short fluent phrase with NO commas, so it has
      // no internal pause — the strip cuts at the first pause, i.e. right after it.
      const LEAD_DUMMY = "Bonjour à tous."
      const text = [LEAD_DUMMY, ...useCues.map((c) => c.translated)].join("\n")
      const ttsStart = await quickTTS(text, useVoice, targetLang, projectId, targetLang)
      let curTTS = ttsStart
      while (curTTS.status === "running") {
        await sleep(1500)
        curTTS = await quickTTSStatus(ttsStart.job_id)
        if (curTTS.message) setStatusMsg(curTTS.message)
      }
      if (curTTS.status === "failed") throw new Error(curTTS.error || "TTS failed")
      setTtsAudioUrl(curTTS.audio_url)

      setStatusMsg("Syncing audio to the timeline…")
      const syncStart = await startAdhocSync(curTTS.audio_url, useCues, targetLang, projectId, LEAD_DUMMY)
      let curSync = syncStart
      while (curSync.status === "running") {
        await sleep(1500)
        curSync = await getAdhocSyncStatus(syncStart.job_id)
        if (curSync.message) setStatusMsg(curSync.message)
      }
      if (curSync.status === "failed") throw new Error(curSync.error || "Sync failed")
      setTtsAudioUrl(curSync.synced_audio_url)
      notify({ severity: "success", message: "Dub generated!" })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setBusy(false)
      setStatusMsg("")
    }
  }, [cues, voice, voices, targetLang, projectId])

  // Extraction finished → load cues into the editor AND start dubbing right away.
  const onExtracted = useCallback((newCues) => {
    setCues(newCues)
    generateDub(newCues)
  }, [generateDub])

  if (!loaded) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", background: colors.bg, color: colors.muted }}>
        Loading session…
      </div>
    )
  }

  if (!cues || cues.length === 0) {
    return (
      <SetupScreen
        onExtracted={onExtracted}
        sourcePath={sourcePath}
        setSourcePath={setSourcePath}
        targetLang={targetLang}
        setTargetLang={setTargetLang}
      />
    )
  }

  return (
    <AdvancedEditor
      projectId={projectId}
      cues={cues} setCues={setCues}
      sourcePath={sourcePath} setSourcePath={setSourcePath}
      ttsAudioUrl={ttsAudioUrl}
      targetLang={targetLang} setTargetLang={setTargetLang}
      voice={voice} setVoice={setVoice} voices={voices}
      reviewed={reviewed} setReviewed={setReviewed}
      generateDub={generateDub} busy={busy} statusMsg={statusMsg}
    />
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   No project selected — pick one to start/continue dubbing
───────────────────────────────────────────────────────────────────────────── */

function NoProjectScreen() {
  const { dispatch } = useApp()
  const { notify } = useNotify()
  const [projects, setProjects] = useState(null)

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch((e) => { notify({ severity: "error", message: e.message }); setProjects([]) })
  }, [])

  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", background: colors.bg }}>
      <div style={{ width: 520, background: colors.panel, padding: 32, borderRadius: radius.lg, border: `1px solid ${colors.border}` }}>
        <h2 style={{ color: colors.text, marginBottom: 8, fontSize: fonts.xxl, fontWeight: fonts.bold }}>Choose a project</h2>
        <p style={{ color: colors.muted, marginBottom: 20, fontSize: fonts.sm }}>
          Dub Studio saves your progress per project. Pick one to start or continue dubbing.
        </p>
        {projects === null ? (
          <div style={{ color: colors.muted }}>Loading projects…</div>
        ) : projects.length === 0 ? (
          <div style={{ color: colors.muted }}>No projects yet — create one in the Library first.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 360, overflowY: "auto" }}>
            {projects.map((p) => (
              <button key={p.id} onClick={() => dispatch(actions.setProject(p.id))}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md,
                  padding: "12px 14px", color: colors.text, textAlign: "left",
                }}>
                <span style={{ fontWeight: fonts.medium }}>{p.title}</span>
                <span style={{ color: colors.muted, fontSize: fonts.sm }}>{p.episode_count} ep ›</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Setup screen
───────────────────────────────────────────────────────────────────────────── */

function SetupScreen({ onExtracted, sourcePath, setSourcePath, targetLang, setTargetLang }) {
  const { notify } = useNotify()
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState("")

  const pickAudio = async () => {
    try {
      if (window.pywebview?.api?.pick_file) {
        const path = await window.pywebview.api.pick_file(["Media Files (*.wav;*.mp3;*.m4a;*.mp4;*.mkv)"])
        if (path) setSourcePath(path)
      } else {
        const path = window.prompt("Absolute path to source media file:")
        if (path) setSourcePath(path.trim())
      }
    } catch (err) {
      notify({ severity: "error", message: err.message })
    }
  }

  const runTranslation = async () => {
    if (!sourcePath.trim()) return
    setBusy(true)
    setStatus("Extracting words and translating...")
    try {
      const started = await startAdhocTranslate(sourcePath, targetLang)
      let cur = started
      for (let i = 0; i < 400 && cur.status === "running"; i++) {
        await sleep(1500)
        cur = await getAdhocTranslateStatus(started.job_id)
        if (cur.message) setStatus(cur.message)
      }
      if (cur.status === "failed") {
        notify({ severity: "error", message: cur.error || "Translation failed" })
        setBusy(false); setStatus("")
      } else if (cur.status === "done") {
        notify({ severity: "success", message: "Cues extracted — generating dub…" })
        // Hand off to the editor, which immediately starts generating the dub.
        if (cur.cues) onExtracted(cur.cues)
      }
    } catch (err) {
      notify({ severity: "error", message: err.message })
      setBusy(false); setStatus("")
    }
  }

  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", background: colors.bg }}>
      <div style={{ width: 500, background: colors.panel, padding: 32, borderRadius: radius.lg, border: `1px solid ${colors.border}` }}>
        <h2 style={{ color: colors.text, marginBottom: 8, fontSize: fonts.xxl, fontWeight: fonts.bold }}>Start Dubbing Project</h2>
        <p style={{ color: colors.muted, marginBottom: 24, fontSize: fonts.sm }}>Extract the dialogue, translate it, and generate the {targetLang} voiceover — all in one step.</p>

        <div style={{ marginBottom: 20 }}>
          <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 8 }}>Source Media (Audio or Video)</label>
          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="secondary" onClick={pickAudio} disabled={busy}>Select File</Button>
            <input type="text" value={sourcePath} onChange={(e) => setSourcePath(e.target.value)} disabled={busy}
              placeholder="/absolute/path/to/media.mp4"
              style={{ flex: 1, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "8px 12px", borderRadius: radius.sm }} />
          </div>
        </div>

        <div style={{ marginBottom: 24 }}>
          <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 8 }}>Target Language</label>
          <input type="text" value={targetLang} onChange={(e) => setTargetLang(e.target.value)} disabled={busy}
            style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "8px 12px", borderRadius: radius.sm }} />
        </div>

        <Button variant="primary" onClick={runTranslation} disabled={busy || !sourcePath} loading={busy} style={{ width: "100%", padding: 12 }}>
          {busy ? status || "Working..." : "Start Dubbing"}
        </Button>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Resizable splitter hook — single source of truth in state, window-level
   pointer listeners so the drag survives passing over <video>/<textarea>.
───────────────────────────────────────────────────────────────────────────── */

function useSplitter({ initial, min, max, axis }) {
  const [size, setSize] = useState(initial)

  const onPointerDown = useCallback((e) => {
    e.preventDefault()
    const vertical = axis === "y"
    document.body.classList.add(vertical ? "ms-row-resizing" : "ms-col-resizing")

    const move = (ev) => {
      const next = vertical
        ? window.innerHeight - ev.clientY     // timeline grows from the bottom
        : window.innerWidth - ev.clientX      // right panel grows from the right
      setSize(Math.max(min, Math.min(max, next)))
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
      document.body.classList.remove("ms-row-resizing", "ms-col-resizing")
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
  }, [axis, min, max])

  return [size, onPointerDown]
}

/* ─────────────────────────────────────────────────────────────────────────────
   Advanced editor
───────────────────────────────────────────────────────────────────────────── */

function AdvancedEditor({ projectId, cues, setCues, sourcePath, ttsAudioUrl, targetLang, voice, setVoice, voices, reviewed, setReviewed, generateDub, busy, statusMsg }) {
  const { notify } = useNotify()
  const [exporting, setExporting] = useState(false)

  // View options
  const [hideOriginal, setHideOriginal] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [pxPerSec, setPxPerSec] = useState(100)

  // Playback
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [sourceVol, setSourceVol] = useState(0)         // 0..10  (muted source by default)
  const [dubVol, setDubVol] = useState(10)
  const [speed, setSpeed] = useState(1)

  // Per-cue ✦AI refine (re-translate shorter to cut CPS) — set of busy indices
  const [refining, setRefining] = useState(() => new Set())

  // Undo / redo of translation edits
  const [undoStack, setUndoStack] = useState([])
  const [redoStack, setRedoStack] = useState([])
  const editSnapshot = useRef(null)

  const videoRef = useRef(null)
  const audioRef = useRef(null)
  const cueListRef = useRef(null)
  const timelineScrollRef = useRef(null)
  const stopAtRef = useRef(null)

  const totalTime = useMemo(() => maxTime(cues), [cues])

  // Panels
  const [rightWidth, onColDown] = useSplitter({ initial: 420, min: 320, max: 760, axis: "x" })
  const [timelineH, onRowDown] = useSplitter({ initial: 200, min: 120, max: 420, axis: "y" })

  /* ── Media element wiring ─────────────────────────────────────────────── */

  // Keep volumes / rate in sync.  The source video is MUTED via the element's
  // own `muted` flag when its slider is 0, so the dub track is audible alone.
  useEffect(() => {
    const v = videoRef.current
    if (v) { v.volume = sourceVol / 10; v.muted = sourceVol === 0 }
  }, [sourceVol])
  useEffect(() => { if (audioRef.current) audioRef.current.volume = dubVol / 10 }, [dubVol, ttsAudioUrl])
  useEffect(() => { if (videoRef.current) videoRef.current.playbackRate = speed }, [speed])
  useEffect(() => { if (audioRef.current) audioRef.current.playbackRate = speed }, [speed, ttsAudioUrl])

  // When the dub track URL changes, (re)load it and apply the current volume so
  // a stale/empty element never silently swallows playback.
  useEffect(() => {
    const a = audioRef.current
    if (a && ttsAudioUrl) a.load()   // volume is applied by the effect above
  }, [ttsAudioUrl])

  const syncDub = useCallback(() => {
    const v = videoRef.current, a = audioRef.current
    if (!v || !a) return
    if (Math.abs(a.currentTime - v.currentTime) > 0.25) {
      try { a.currentTime = v.currentTime } catch { /* not yet seekable */ }
    }
  }, [])

  const onTimeUpdate = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    setCurrentTime(v.currentTime)
    // NOTE: never re-seek the dub audio while it's playing — a seek mid-playback
    // glitches the sound. The two elements are started together and both advance
    // at 1×, so they stay in sync. We only resync on an explicit user seek.
    if (stopAtRef.current != null && v.currentTime >= stopAtRef.current) {
      stopAtRef.current = null
      v.pause()
    }
  }, [])

  const play = useCallback(() => {
    const v = videoRef.current, a = audioRef.current
    if (!v) return
    v.play()
    if (a) {
      try { a.currentTime = v.currentTime } catch { /* */ }
      // Call play() synchronously inside the user gesture so autoplay policy
      // doesn't block it. If the track isn't buffered yet the browser queues it;
      // only if that rejects do we retry once it can play.
      a.play().catch(() => {
        a.addEventListener("canplay",
          () => a.play().catch((err) => console.warn("dub audio play blocked:", err)),
          { once: true })
      })
    }
  }, [])

  const pause = useCallback(() => {
    videoRef.current?.pause()
    audioRef.current?.pause()
  }, [])

  const togglePlay = useCallback(() => {
    stopAtRef.current = null
    if (videoRef.current?.paused) play(); else pause()
  }, [play, pause])

  const seek = useCallback((t) => {
    const v = videoRef.current, a = audioRef.current
    const clamped = Math.max(0, Math.min(t, duration || totalTime))
    if (v) v.currentTime = clamped
    if (a) { try { a.currentTime = clamped } catch { /* */ } }
    setCurrentTime(clamped)
  }, [duration, totalTime])

  const playCue = useCallback((c) => {
    stopAtRef.current = c.end
    seek(c.start)
    setTimeout(play, 0)
  }, [seek, play])

  // The playhead updates from the video's own timeupdate (~4fps) and glides
  // smoothly between ticks via a CSS transition — no per-frame React re-render
  // (which was saturating the main thread and breaking audio).

  const activeIdx = useMemo(
    () => cues.findIndex((c) => currentTime >= c.start && currentTime < c.end),
    [cues, currentTime],
  )

  // Auto-scroll the timeline + cue list to keep the playhead/active cue visible.
  useEffect(() => {
    if (!autoScroll || !playing) return
    const ts = timelineScrollRef.current
    if (ts) {
      const x = currentTime * pxPerSec
      if (x < ts.scrollLeft + 80 || x > ts.scrollLeft + ts.clientWidth - 80) {
        ts.scrollTo({ left: Math.max(0, x - ts.clientWidth / 2), behavior: "smooth" })
      }
    }
  }, [currentTime, autoScroll, playing, pxPerSec])

  useEffect(() => {
    if (!autoScroll || activeIdx < 0 || !cueListRef.current) return
    const el = cueListRef.current.querySelector(`[data-cue="${activeIdx}"]`)
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" })
  }, [activeIdx, autoScroll])

  /* ── Cue editing + undo/redo ──────────────────────────────────────────── */

  const updateCue = (idx, newTranslated) => {
    setCues((prev) => {
      const next = [...prev]
      const c = next[idx]
      const dur = Math.max(0, (c.end ?? 0) - (c.start ?? 0))
      const cps = computeCps(newTranslated, dur)
      next[idx] = { ...c, translated: newTranslated, cps, rushed: dur > 0 && cps > CPS_MAX }
      return next
    })
  }

  const refine = async (idx) => {
    const c = cues[idx]
    if (!c || refining.has(idx)) return
    setRefining((s) => new Set(s).add(idx))
    const before = cues
    try {
      const res = await refineCue({
        text: c.text, translated: c.translated,
        start: c.start, end: c.end, langCode: targetLang,
      })
      if (res?.changed === false) {
        notify({ severity: "info", message: `Cue ${idx + 1} already fits (${res.cps} CPS) — no change.` })
      } else if (res?.translated) {
        setUndoStack((s) => [...s, before])   // make the AI edit undoable
        setRedoStack([])
        setCues((prev) => {
          const next = [...prev]
          next[idx] = { ...next[idx], translated: res.translated, cps: res.cps, rushed: res.rushed }
          return next
        })
        notify({ severity: "success", message: `Cue ${idx + 1} refined → ${res.cps} CPS` })
      }
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setRefining((s) => { const n = new Set(s); n.delete(idx); return n })
    }
  }

  const beginEdit = () => { editSnapshot.current = cues }
  const commitEdit = () => {
    if (editSnapshot.current && JSON.stringify(editSnapshot.current) !== JSON.stringify(cues)) {
      setUndoStack((s) => [...s, editSnapshot.current])
      setRedoStack([])
    }
    editSnapshot.current = null
  }

  const undo = () => {
    setUndoStack((s) => {
      if (!s.length) return s
      const prev = s[s.length - 1]
      setRedoStack((r) => [...r, cues])
      setCues(prev)
      return s.slice(0, -1)
    })
  }
  const redo = () => {
    setRedoStack((r) => {
      if (!r.length) return r
      const next = r[r.length - 1]
      setUndoStack((s) => [...s, cues])
      setCues(next)
      return r.slice(0, -1)
    })
  }

  const toggleReviewed = (idx) => {
    setReviewed((prev) => (prev.includes(idx) ? prev.filter((i) => i !== idx) : [...prev, idx]))
  }
  const checkAll = () => {
    setReviewed((prev) => (prev.length === cues.length ? [] : cues.map((_, i) => i)))
  }
  const reviewedPct = cues.length ? Math.round((reviewed.length / cues.length) * 100) : 0

  /* ── Export (MP4 / MP3) ───────────────────────────────────────────────── */

  const doExport = async (fmt) => {
    if (!ttsAudioUrl) { notify({ severity: "error", message: "Generate the dub first." }); return }
    if (fmt === "video" && !sourcePath) { notify({ severity: "error", message: "No source video to mux." }); return }
    setExporting(true)
    try {
      const res = await exportDub(projectId, { langCode: targetLang, fmt, sourcePath })
      // The file is already written to the project's exports/ folder. Offer a
      // real "Save As" via the native dialog. NEVER use <a href> to a server URL
      // — pywebview navigates the whole window to it (the stuck media player).
      if (window.pywebview?.api?.save_file) {
        const dest = await window.pywebview.api.save_file(res.path, res.filename)
        notify(dest
          ? { severity: "success", message: `Saved to ${dest}` }
          : { severity: "info", message: `Saved in project folder: ${res.path}` })
      } else {
        // Browser/dev fallback: download via a Blob object-URL (does not navigate).
        const r = await fetch(`${FILES_ORIGIN}${res.url}`)
        const blob = await r.blob()
        const u = URL.createObjectURL(blob)
        const a = document.createElement("a")
        a.href = u; a.download = res.filename
        document.body.appendChild(a); a.click(); a.remove()
        URL.revokeObjectURL(u)
        notify({ severity: "success", message: `Exported ${res.filename}` })
      }
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setExporting(false)
    }
  }

  const videoSrc = mediaSrc(sourcePath)

  /* ── Render ───────────────────────────────────────────────────────────── */

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100%", background: colors.bg, color: colors.text, overflow: "hidden", fontFamily: fonts.ui }}>

      {/* Global top bar */}
      <TopBar onExport={doExport} exporting={exporting} canExport={!!ttsAudioUrl} />

      {/* Top split area */}
      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>

        {/* Left: cue editor */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>

          {/* Left header */}
          <div style={{ height: 56, borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", padding: "0 16px", justifyContent: "space-between", background: colors.panel, flexShrink: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "6px 10px" }}>
                <span style={{ fontSize: 16 }}>🏳️</span>
                <span style={{ fontWeight: fonts.bold }}>{targetLang}</span>
              </div>
              <select value={voice} onChange={(e) => setVoice(e.target.value)}
                style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "7px 10px", borderRadius: radius.md }}>
                {voices.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
              </select>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Button variant="primary" onClick={() => generateDub()} disabled={busy} loading={busy}
                title="Generate the voiceover: text-to-speech + sync to the timeline"
                style={{ background: colors.accent, color: "#000", fontWeight: fonts.bold, border: "none", borderRadius: radius.full, padding: "8px 18px" }}>
                {busy ? statusMsg || "Working…" : (ttsAudioUrl ? "✨ Regenerate Dub" : "✨ Generate Dub")}
              </Button>
            </div>
          </div>

          {/* Cue list */}
          <div ref={cueListRef} style={{ flex: 1, overflowY: "auto", padding: "20px 16px", display: "flex", flexDirection: "column", gap: 18 }}>
            {cues.map((c, i) => (
              <CueRow
                key={i}
                index={i}
                cue={c}
                active={i === activeIdx}
                reviewed={reviewed.includes(i)}
                refining={refining.has(i)}
                hideOriginal={hideOriginal}
                onToggleReviewed={() => toggleReviewed(i)}
                onPlay={() => playCue(c)}
                onRefine={() => refine(i)}
                onChange={(val) => updateCue(i, val)}
                onFocus={beginEdit}
                onBlur={commitEdit}
              />
            ))}
            <div style={{ height: 8 }} />
          </div>

          {/* Footer toolbar */}
          <div style={{ height: 48, borderTop: `1px solid ${colors.border}`, background: colors.panel, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 16px", flexShrink: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <Toggle label="Auto-Scroll" on={autoScroll} onClick={() => setAutoScroll((v) => !v)} />
              <Toggle label="Hide Original" on={hideOriginal} onClick={() => setHideOriginal((v) => !v)} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <IconBtn title="Undo" disabled={!undoStack.length} onClick={undo}><IconUndo /></IconBtn>
              <span style={{ color: colors.textDim, fontSize: fonts.sm }}>Undo / Redo</span>
              <IconBtn title="Redo" disabled={!redoStack.length} onClick={redo}><IconRedo /></IconBtn>
            </div>
          </div>
        </div>

        {/* Column splitter */}
        <Splitter axis="x" onPointerDown={onColDown} />

        {/* Right: media player */}
        <div style={{ width: rightWidth, flexShrink: 0, background: colors.panel, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ height: 56, padding: "0 16px", borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontWeight: fonts.bold }}>Source Preview</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: fonts.sm }}>
              <span style={{ color: colors.textDim }}>{formatTime(totalTime)}</span>
              <span style={{ color: reviewedPct === 100 ? colors.success : colors.textDim }}>Reviewed {reviewedPct}%</span>
              <button onClick={checkAll} style={{ color: colors.accent, fontSize: fonts.sm }}>Check All</button>
            </div>
          </div>

          <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: 16, gap: 12, overflowY: "auto" }}>
            <div style={{ position: "relative", borderRadius: radius.md, overflow: "hidden", background: "#000", display: "flex", alignItems: "center", justifyContent: "center", minHeight: 200 }}>
              {videoSrc ? (
                <video
                  ref={videoRef}
                  src={videoSrc}
                  onLoadedMetadata={(e) => setDuration(e.target.duration || 0)}
                  onTimeUpdate={onTimeUpdate}
                  onSeeking={syncDub}
                  onPlay={() => { setPlaying(true); audioRef.current?.play().catch(() => {}) }}
                  onPause={() => { setPlaying(false); audioRef.current?.pause() }}
                  onClick={togglePlay}
                  style={{ width: "100%", maxHeight: 360, objectFit: "contain", display: "block", cursor: "pointer" }}
                />
              ) : (
                <div style={{ color: colors.muted, padding: 40 }}>No media loaded</div>
              )}
            </div>

            {/* Transport controls */}
            <PlayerControls
              currentTime={currentTime}
              duration={duration || totalTime}
              playing={playing}
              speed={speed}
              setSpeed={setSpeed}
              onSeek={seek}
              onToggle={togglePlay}
              onSkip={(d) => seek(currentTime + d)}
            />

            {/* Volumes */}
            <div style={{ background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
              <VolumeRow icon={<IconMic muted={sourceVol === 0} />} label="Source Volume" value={sourceVol} onChange={setSourceVol} />
              <VolumeRow icon={<IconHeadphones />} label="Voiceover/Dub Volume" value={dubVol} onChange={setDubVol} disabled={!ttsAudioUrl} />
            </div>

            {!ttsAudioUrl && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, background: "rgba(255,107,53,0.08)", border: `1px solid ${colors.accent}`, borderRadius: radius.md, padding: "10px 12px", color: colors.textDim, fontSize: fonts.sm }}>
                <span style={{ fontSize: 16 }}>🔇</span>
                <span>No voiceover yet — click <b style={{ color: colors.accent }}>✨ Generate Dub</b> (top right) to create the {targetLang} audio.</span>
              </div>
            )}

            {ttsAudioUrl && (
              <audio
                ref={audioRef}
                src={`${FILES_ORIGIN}${ttsAudioUrl}`}
                preload="auto"
                onError={() => notify({ severity: "error", message: "Dub audio failed to load — try re-running AI Dubbing." })}
                style={{ display: "none" }}
              />
            )}
          </div>
        </div>
      </div>

      {/* Row splitter */}
      <Splitter axis="y" onPointerDown={onRowDown} />

      {/* Bottom: timeline */}
      <Timeline
        ref={timelineScrollRef}
        cues={cues}
        height={timelineH}
        pxPerSec={pxPerSec}
        setPxPerSec={setPxPerSec}
        totalTime={totalTime}
        currentTime={currentTime}
        activeIdx={activeIdx}
        onSeek={seek}
      />
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Top bar
───────────────────────────────────────────────────────────────────────────── */

function TopBar({ onExport, exporting, canExport }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <div style={{ height: 52, flexShrink: 0, background: colors.panel, borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, border: `1px solid ${colors.accent}`, borderRadius: radius.full, padding: "6px 14px", color: colors.accent, fontWeight: fonts.bold }}>
          ✦ AI Summary
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Button variant="ghost" style={{ borderRadius: radius.md }}>⚙ Tools</Button>
        <Button variant="ghost" style={{ borderRadius: radius.md }}>↗ Share</Button>
        <div style={{ position: "relative" }}>
          <Button variant="primary" loading={exporting} disabled={exporting}
            onClick={() => canExport ? setMenuOpen((o) => !o) : null}
            title={canExport ? "Export the finished dub" : "Generate the dub first"}
            style={{ borderRadius: radius.md, opacity: canExport ? 1 : 0.5 }}>
            {exporting ? "Exporting…" : "⬆ Download/Export"}
          </Button>
          {menuOpen && canExport && !exporting && (
            <>
              <div onClick={() => setMenuOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 40 }} />
              <div style={{ position: "absolute", right: 0, top: "calc(100% + 6px)", zIndex: 41, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, boxShadow: "0 8px 24px rgba(0,0,0,0.6)", overflow: "hidden", minWidth: 200 }}>
                <MenuItem label="🎬 Video (MP4)" sub="Original video + dubbed audio" onClick={() => { setMenuOpen(false); onExport("video") }} />
                <MenuItem label="🎵 Audio only (MP3)" sub="Just the dubbed track" onClick={() => { setMenuOpen(false); onExport("audio") }} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function MenuItem({ label, sub, onClick }) {
  const [hover, setHover] = useState(false)
  return (
    <button onClick={onClick} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: "block", width: "100%", textAlign: "left", padding: "10px 14px", background: hover ? colors.panel : "transparent", color: colors.text }}>
      <div style={{ fontWeight: fonts.medium }}>{label}</div>
      <div style={{ fontSize: fonts.sm, color: colors.muted }}>{sub}</div>
    </button>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Cue row
───────────────────────────────────────────────────────────────────────────── */

function CueRow({ index, cue, active, reviewed, refining, hideOriginal, onToggleReviewed, onPlay, onRefine, onChange, onFocus, onBlur }) {
  const cps = cue.cps ?? 0
  const rushed = cue.rushed ?? cps > 20
  return (
    <div data-cue={index} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: fonts.sm }}>
        <span style={{ fontWeight: fonts.bold, fontSize: fonts.md, color: active ? colors.accent : colors.text }}>{String(index + 1).padStart(2, "0")}</span>
        <button onClick={onToggleReviewed} title="Mark reviewed"
          style={{ color: reviewed ? colors.success : colors.muted, fontSize: 14, lineHeight: 1 }}>
          {reviewed ? "✓" : "○"}
        </button>
        <span style={{ color: colors.info, fontWeight: fonts.medium }}>Speaker 1</span>
        <span style={{
          background: rushed ? "rgba(248,113,113,0.15)" : colors.panel2,
          color: rushed ? colors.error : colors.warning,
          border: `1px solid ${rushed ? colors.error : colors.border}`,
          padding: "2px 7px", borderRadius: radius.full, fontSize: 10, fontWeight: fonts.bold,
        }}>{cps.toFixed(1)} CPS</span>
        <span style={{ color: colors.textDim, background: colors.panel2, border: `1px solid ${colors.border}`, padding: "2px 8px", borderRadius: radius.full, fontVariantNumeric: "tabular-nums" }}>
          {formatTime(cue.start)} - {formatTime(cue.end)}
        </span>
        <button
          onClick={onRefine}
          disabled={refining}
          title="AI: re-translate this line shorter to lower its CPS"
          style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            color: refining ? colors.muted : colors.warning, fontSize: 11, fontWeight: fonts.bold,
            border: `1px solid ${refining ? colors.border : "rgba(251,191,36,0.4)"}`,
            background: refining ? colors.panel2 : "rgba(251,191,36,0.10)",
            borderRadius: radius.full, padding: "2px 8px",
            cursor: refining ? "wait" : "pointer",
          }}>
          {refining
            ? <><span style={{ width: 9, height: 9, border: "1.5px solid currentColor", borderTopColor: "transparent", borderRadius: "50%", display: "inline-block", animation: "ms-spin 0.65s linear infinite" }} /> Fixing…</>
            : "✦ AI Fix"}
        </button>
        <div style={{ flex: 1 }} />
        <IconBtn title="Play cue" onClick={onPlay}><IconHeadphones small /></IconBtn>
      </div>

      <div style={{
        border: `1px solid ${active ? colors.accent : colors.border}`,
        borderRadius: radius.md, overflow: "hidden",
        boxShadow: active ? `0 0 0 1px ${colors.accent}` : "none",
      }}>
        {!hideOriginal && (
          <div style={{ padding: "10px 12px", color: colors.textDim, fontSize: fonts.base, borderBottom: `1px solid ${colors.border}` }}>
            {cue.text}
          </div>
        )}
        <textarea
          value={cue.translated || ""}
          onChange={(e) => onChange(e.target.value)}
          onFocus={onFocus}
          onBlur={onBlur}
          rows={1}
          style={{ width: "100%", background: colors.cueTrans, border: "none", color: colors.text, padding: "10px 12px", fontSize: fonts.base, resize: "vertical", minHeight: 44, display: "block", fontFamily: fonts.ui }}
        />
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Player controls
───────────────────────────────────────────────────────────────────────────── */

function PlayerControls({ currentTime, duration, playing, speed, setSpeed, onSeek, onToggle, onSkip }) {
  const pct = duration ? (currentTime / duration) * 100 : 0
  const SPEEDS = [0.5, 1, 1.5, 2]
  const cycleSpeed = () => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length])
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <input
        type="range" className="ms-range" min={0} max={duration || 0} step={0.01} value={currentTime}
        onChange={(e) => onSeek(parseFloat(e.target.value))}
        style={{ background: `linear-gradient(to right, ${colors.accent} ${pct}%, ${colors.border} ${pct}%)` }}
      />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: fonts.sm, color: colors.textDim, fontVariantNumeric: "tabular-nums" }}>{formatTime(currentTime)}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <IconBtn title="Back 5s" onClick={() => onSkip(-5)}><IconRewind /></IconBtn>
          <button onClick={onToggle} style={{ width: 38, height: 38, borderRadius: "50%", background: colors.accent, color: "#000", display: "flex", alignItems: "center", justifyContent: "center" }}>
            {playing ? <IconPause /> : <IconPlay />}
          </button>
          <IconBtn title="Forward 5s" onClick={() => onSkip(5)}><IconFastForward /></IconBtn>
        </div>
        <button onClick={cycleSpeed} style={{ fontSize: fonts.sm, color: colors.textDim, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "4px 8px" }}>
          {speed}× Speed
        </button>
      </div>
    </div>
  )
}

function VolumeRow({ icon, label, value, onChange, disabled }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, opacity: disabled ? 0.45 : 1 }}>
      <span style={{ color: colors.textDim, display: "flex", width: 18 }}>{icon}</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: fonts.sm, color: colors.textDim, marginBottom: 6 }}>{label}</div>
        <input type="range" className="ms-range" min={0} max={10} step={1} value={value} disabled={disabled}
          onChange={(e) => onChange(parseInt(e.target.value, 10))}
          style={{ background: `linear-gradient(to right, ${colors.accent} ${value * 10}%, ${colors.border} ${value * 10}%)`, cursor: disabled ? "not-allowed" : "pointer" }} />
      </div>
      <span style={{ width: 18, textAlign: "right", color: colors.text, fontVariantNumeric: "tabular-nums" }}>{value}</span>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Timeline
───────────────────────────────────────────────────────────────────────────── */

const Timeline = forwardRef(function Timeline({ cues, height, pxPerSec, setPxPerSec, totalTime, currentTime, activeIdx, onSeek }, scrollRef) {
  const width = Math.max(1000, totalTime * pxPerSec)
  const tickStep = pxPerSec < 60 ? 5 : 1     // seconds between labelled ticks
  const ticks = Math.ceil(totalTime / tickStep) + 1

  const onTrackClick = (e) => {
    const wrap = e.currentTarget
    const rect = wrap.getBoundingClientRect()
    const x = e.clientX - rect.left + wrap.scrollLeft
    onSeek(x / pxPerSec)
  }

  return (
    <div style={{ height, flexShrink: 0, background: colors.panel, borderTop: `1px solid ${colors.border}`, display: "flex", flexDirection: "column" }}>
      {/* Zoom controls */}
      <div style={{ height: 34, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 6, padding: "0 12px", borderBottom: `1px solid ${colors.border}` }}>
        <span style={{ color: colors.muted, fontSize: fonts.sm, marginRight: "auto" }}>Timeline</span>
        <IconBtn title="Zoom out" onClick={() => setPxPerSec((p) => Math.max(20, p - 20))}>−</IconBtn>
        <span style={{ color: colors.textDim, fontSize: fonts.sm, width: 40, textAlign: "center" }}>{pxPerSec}px/s</span>
        <IconBtn title="Zoom in" onClick={() => setPxPerSec((p) => Math.min(300, p + 20))}>+</IconBtn>
      </div>

      <div ref={scrollRef} onClick={onTrackClick}
        style={{ flex: 1, minHeight: 0, overflowX: "auto", overflowY: "hidden", position: "relative", cursor: "text" }}>
        <div style={{ width, height: "100%", position: "relative" }}>

          {/* Ruler */}
          <div style={{ height: 22, background: colors.timelineRuler, borderBottom: `1px solid ${colors.border}`, position: "sticky", top: 0, zIndex: 1 }}>
            {Array.from({ length: ticks }).map((_, k) => {
              const sec = k * tickStep
              return (
                <div key={k} style={{ position: "absolute", left: sec * pxPerSec, top: 0, height: 22, borderLeft: `1px solid #3a3a3e`, paddingLeft: 4, fontSize: 10, color: colors.muted, fontVariantNumeric: "tabular-nums" }}>
                  {formatTime(sec)}
                </div>
              )
            })}
          </div>

          {/* Cue blocks */}
          <div style={{ position: "absolute", top: 34, bottom: 12, left: 0, right: 0 }}>
            {cues.map((c, i) => {
              const dur = Math.max(0, c.end - c.start)
              return (
                <div key={i} title={c.translated}
                  style={{
                    position: "absolute", left: c.start * pxPerSec, width: Math.max(8, dur * pxPerSec), top: 0, bottom: 0,
                    background: i === activeIdx ? "#2563eb" : colors.timelineCue,
                    border: i === activeIdx ? `1px solid ${colors.accent}` : "1px solid rgba(255,255,255,0.08)",
                    borderRadius: radius.sm, padding: "6px 8px", color: "#fff", overflow: "hidden", fontSize: 11,
                    boxShadow: "0 2px 4px rgba(0,0,0,0.4)",
                  }}>
                  <div style={{ fontWeight: fonts.bold, marginBottom: 2 }}>{i + 1}</div>
                  <div style={{ whiteSpace: "nowrap", textOverflow: "ellipsis", overflow: "hidden", opacity: 0.9 }}>{c.translated}</div>
                </div>
              )
            })}
          </div>

          {/* Playhead — glides between the ~4fps time updates */}
          <div style={{ position: "absolute", top: 0, bottom: 0, left: currentTime * pxPerSec, width: 2, background: colors.accent, zIndex: 3, pointerEvents: "none", transition: "left 0.22s linear" }}>
            <div style={{ position: "absolute", top: 0, left: -4, width: 10, height: 10, borderRadius: "50%", background: colors.accent }} />
          </div>
        </div>
      </div>
    </div>
  )
})

/* ─────────────────────────────────────────────────────────────────────────────
   Small UI atoms
───────────────────────────────────────────────────────────────────────────── */

function Splitter({ axis, onPointerDown }) {
  const [hover, setHover] = useState(false)
  const vertical = axis === "y"
  return (
    <div
      onPointerDown={onPointerDown}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        flexShrink: 0,
        background: hover ? colors.accent : colors.border,
        transition: "background 0.12s",
        ...(vertical
          ? { height: 6, width: "100%", cursor: "row-resize" }
          : { width: 6, cursor: "col-resize" }),
      }}
    />
  )
}

function Toggle({ label, on, onClick }) {
  return (
    <button onClick={onClick} style={{ display: "flex", alignItems: "center", gap: 8, color: on ? colors.text : colors.textDim }}>
      <span style={{
        width: 34, height: 18, borderRadius: radius.full, background: on ? colors.accent : colors.border,
        position: "relative", transition: "background 0.15s", flexShrink: 0,
      }}>
        <span style={{
          position: "absolute", top: 2, left: on ? 18 : 2, width: 14, height: 14, borderRadius: "50%",
          background: "#fff", transition: "left 0.15s",
        }} />
      </span>
      <span style={{ fontSize: fonts.sm, fontWeight: fonts.medium }}>{label}</span>
    </button>
  )
}

function IconBtn({ children, onClick, title, disabled }) {
  const [hover, setHover] = useState(false)
  return (
    <button title={title} onClick={disabled ? undefined : onClick}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
        width: 30, height: 30, borderRadius: radius.sm, display: "flex", alignItems: "center", justifyContent: "center",
        color: disabled ? colors.muted : colors.textDim,
        background: hover && !disabled ? colors.panel2 : "transparent",
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
      }}>{children}</button>
  )
}

/* ── Inline SVG icons ─────────────────────────────────────────────────────── */

const sv = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" }
const IconPlay = () => <svg {...sv} fill="currentColor" stroke="none"><path d="M8 5v14l11-7z" /></svg>
const IconPause = () => <svg {...sv} fill="currentColor" stroke="none"><path d="M6 5h4v14H6zM14 5h4v14h-4z" /></svg>
const IconRewind = () => <svg {...sv}><path d="M11 19l-7-7 7-7M20 19l-7-7 7-7" /></svg>
const IconFastForward = () => <svg {...sv}><path d="M13 5l7 7-7 7M4 5l7 7-7 7" /></svg>
const IconUndo = () => <svg {...sv}><path d="M3 7v6h6M3 13a9 9 0 1 0 3-7" /></svg>
const IconRedo = () => <svg {...sv}><path d="M21 7v6h-6M21 13a9 9 0 1 1-3-7" /></svg>
const IconHeadphones = ({ small }) => <svg {...sv} width={small ? 14 : 16} height={small ? 14 : 16}><path d="M3 18v-6a9 9 0 0 1 18 0v6" /><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" /></svg>
const IconMic = ({ muted }) => <svg {...sv}><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0M12 19v3" />{muted && <line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" />}</svg>

/* ─────────────────────────────────────────────────────────────────────────────
   Time helpers
───────────────────────────────────────────────────────────────────────────── */

function formatTime(secs) {
  if (isNaN(secs)) return "0:00:00.000"
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  const ms = Math.floor((secs % 1) * 1000)
  return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}.${ms.toString().padStart(3, "0")}`
}

function maxTime(cues) {
  return cues.reduce((max, c) => Math.max(max, c.end), 0) + 2
}

// Mirrors scripts/speech/cps.py: characters (trimmed) per second.
const CPS_MAX = 20.0   // non-CJK "rushed" threshold (config.CPS_MAX)
function computeCps(text, durationSec) {
  const n = (text || "").trim().length
  return durationSec > 0 ? Math.round((n / durationSec) * 10) / 10 : 0
}
