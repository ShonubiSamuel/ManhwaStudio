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
import { listVoices, getLanguages } from "../api/voices"
import { startAdhocTranslate, getAdhocTranslateStatus, getAdhocSyncStatus, startDubCues, startRedubCue, refineCue, refineScript, getDubSession, saveDubSession, exportDub, startTranslateCues } from "../api/speech"
import { listProjects } from "../api/projects"
import { useApp, actions } from "../store/app"
import { useNotify } from "../store/notify"
import { savePanel, deletePanel, upscaleAll, getUpscaleStatus } from "../api/videoRefine"
import { FILES_ORIGIN, mediaSrc } from "../api/panels"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import PdfReader from "../components/PdfReader"
import LogPanel from "../components/LogPanel"

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

export function ProjectDubStudio({ projectId, onBack, refine = false }) {
  const { notify } = useNotify()
  const [loaded, setLoaded] = useState(false)
  const [cues, setCues] = useState(null)
  const [ttsAudioUrl, setTtsAudioUrl] = useState("")
  const [targetLang, setTargetLang] = useState("French")
  const [sourcePath, setSourcePath] = useState("")
  const [voice, setVoice] = useState("")
  const [reviewed, setReviewed] = useState([])
  const [pdfPath, setPdfPath] = useState("")   // Video Refine only (manga PDF)
  // Video Refine multi-language: a project can hold several languages, each with
  // its own voice; the active one drives targetLang/voice/translated/ttsAudioUrl.
  const [languages, setLanguages] = useState([])     // [{ name, voice }]
  const [selectedLang, setSelectedLang] = useState("")
  const [dubUrls, setDubUrls] = useState({})         // { langName: syncedAudioUrl }
  const [speakersMap, setSpeakersMap] = useState({}) // { "Speaker 0": "alloy" }
  const [sourceLang, setSourceLang] = useState("English") // Original language of the source media

  // Load this project's saved session (falling back to migrating the old global
  // localStorage session the first time, so nobody loses in-progress work).
  useEffect(() => {
    let alive = true
    getDubSession(projectId).then((s) => {
      if (!alive) return
      // Always apply the scalar settings (so a freshly-created project pre-fills
      // the setup screen with the source + language picked in the New modal).
      if (s && typeof s === "object") {
        if (s.targetLang)  setTargetLang(s.targetLang)
        if (s.sourcePath)  setSourcePath(s.sourcePath)
        if (s.voice)       setVoice(s.voice)
        if (s.ttsAudioUrl) setTtsAudioUrl(s.ttsAudioUrl)
        if (s.pdfPath)     setPdfPath(s.pdfPath)
        if (Array.isArray(s.reviewed)) setReviewed(s.reviewed)
        // Multi-language: use the saved list, else migrate the single language.
        const langs = Array.isArray(s.languages) && s.languages.length
          ? s.languages
          : (s.targetLang ? [{ name: s.targetLang, voice: s.voice || "" }] : [])
        setLanguages(langs)
        setSelectedLang(s.selectedLang || langs[0]?.name || s.targetLang || "")
        setDubUrls(s.dubUrls || (s.ttsAudioUrl && s.targetLang ? { [s.targetLang]: s.ttsAudioUrl } : {}))
        if (s.speakersMap) setSpeakersMap(s.speakersMap)
        if (s.sourceLang) setSourceLang(s.sourceLang)
      }
      if (s && Array.isArray(s.cues) && s.cues.length) {
        // Seed each cue's "dubbed" baseline when a dub already exists, so edits
        // from here on are detected (dirty) and can show a per-cue Redub.
        const hasAudio = !!s.ttsAudioUrl
        const seedLang = s.selectedLang || s.targetLang || ""
        setCues(s.cues.map((c) => {
          let next = c
          if (hasAudio && c.dubbed === undefined) next = { ...next, dubbed: c.translated }
          // Ensure a per-language translations map exists (migrate the single field).
          if (!next.translations) next = { ...next, translations: seedLang ? { [seedLang]: c.translated || "" } : {} }
          if (!next.speaker) next = { ...next, speaker: "Speaker 0" }
          return next
        }))
      } else {
        try {
          const old = JSON.parse(localStorage.getItem("dub_cues") || "null")
          if (Array.isArray(old) && old.length) {
            setCues(old)
            setTtsAudioUrl(localStorage.getItem("dub_ttsAudioUrl") || "")
            if (!s?.targetLang) setTargetLang(localStorage.getItem("dub_targetLang") || "French")
            if (!s?.sourcePath) setSourcePath(localStorage.getItem("dub_sourcePath") || "")
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
        cues: cues || [], ttsAudioUrl, targetLang, sourcePath, voice, reviewed, pdfPath,
        languages, selectedLang, dubUrls, speakersMap, sourceLang,
        updatedAt: Date.now(),
      }).catch(() => { /* best-effort; next change retries */ })
    }, 700)
    return () => clearTimeout(t)
  }, [loaded, cues, ttsAudioUrl, targetLang, sourcePath, voice, reviewed, pdfPath, languages, selectedLang, dubUrls, speakersMap, sourceLang, projectId])

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

  const generateDub = useCallback(async (cuesArg, resumeJobId = null) => {
    const useCues = cuesArg || cues
    if (!useCues || !useCues.length) return
    const useVoice = voice || voices[0]?.name
    if (!useVoice) { notify({ severity: "error", message: "No voice available — add one under Voices first." }); return }
    setBusy(true)
    try {
      // Per-cue dubbing: each line is synthesized as its own clean clip (one
      // model load), then placed + mastered. No continuous-read splitting, so no
      // mid-word cuts and no leaked warm-up.
      setStatusMsg("Generating voiceover…")
      const lsKey = `DubStudio-dub-${projectId}`
      let cur
      let jobId
      const isResuming = typeof resumeJobId === 'string'
      if (isResuming) {
        jobId = resumeJobId
        cur = await getAdhocSyncStatus(jobId)
      } else {
        const payloadCues = useCues.map(c => ({ ...c, voice: speakersMap[c.speaker] || useVoice }))
        const repackTimings = refine && targetLang === sourceLang
        const start = await startDubCues(payloadCues, useVoice, targetLang, projectId, repackTimings)
        cur = start
        jobId = start.job_id
        localStorage.setItem(lsKey, jobId)
      }
      
      while (cur && cur.status === "running") {
        await sleep(1500)
        cur = await getAdhocSyncStatus(jobId)
        if (cur && cur.message) setStatusMsg(cur.message)
      }
      localStorage.removeItem(lsKey)
      if (!cur || cur.status === "failed") throw new Error((cur && cur.error) || "Dub failed")
      // Cache-bust: the file path is identical across regenerations, so without a
      // unique query the <audio> element replays the browser's STALE cached copy
      // (clean file on disk, old audio in the app). The query is ignored by the
      // /files static server. This is why the app could differ from the file.
      const base = (cur.synced_audio_url || "").split("?")[0]
      const url = `${base}?v=${Date.now()}`
      setTtsAudioUrl(url)
      setDubUrls((d) => ({ ...d, [targetLang]: url }))   // remember this language's dub
      // Mark every cue as "dubbed" (clean) so later edits can show a per-cue Redub.
      // If the backend repacked the timings (Video Refine original lang), use those updated cues.
      setCues((prev) => {
        const baseCues = cur.updated_cues || prev
        return baseCues.map((c, i) => ({ ...c, dubbed: c.translated || prev[i]?.translated }))
      })
      notify({ severity: "success", message: "Dub generated!" })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setBusy(false)
      setStatusMsg("")
    }
  }, [cues, voice, voices, targetLang, projectId, speakersMap, refine, sourceLang])

  // Extraction finished → load cues into the editor. Voiceover dubs immediately;
  // Video Refine just transcribes (you AI-Refine first, then translate + dub).
  const onExtracted = useCallback((newCues) => {
    if (refine) {
      const lang = sourceLang || "English"
      setCues(newCues.map((c) => ({
        ...c,
        translated: "",
        translations: { [lang]: "" },
        speaker: "Speaker 0",
      })))
      // Auto-seed the source language into the languages list
      setLanguages((prev) => prev.some((l) => l.name === lang) ? prev : [{ name: lang, voice: voice || "" }, ...prev])
      setSelectedLang((prev) => prev || lang)
      setTargetLang((prev) => prev === "French" ? lang : prev)  // override the default
      return
    }
    const withSpeakers = newCues.map(c => ({ ...c, speaker: c.speaker || "Speaker 0" }))
    setCues(withSpeakers)
    generateDub(withSpeakers)
  }, [generateDub, refine, sourceLang, voice])

  // Incremental redub: re-voice ONE cue, then cheaply reassemble the final track.
  // Lets an edit redub just that clip instead of re-voicing the whole project.
  const [redubbing, setRedubbing] = useState(() => new Set())
  const redubOne = useCallback(async (idx, cuesOverride, resumeJobId = null) => {
    const cuesNow = cuesOverride || cues
    if (!cuesNow[idx]) return
    const useVoice = voice || voices[0]?.name
    if (!useVoice) { notify({ severity: "error", message: "No voice available." }); return }
    setRedubbing((s) => { const n = new Set(s); n.add(idx); return n })
    try {
      const lsKey = `DubStudio-redub-${projectId}-${idx}`
      let cur
      let jobId
      const isResuming = typeof resumeJobId === 'string'
      if (isResuming) {
        jobId = resumeJobId
        cur = await getAdhocSyncStatus(jobId)
      } else {
        const payloadCues = cuesNow.map(c => ({ ...c, voice: speakersMap[c.speaker] || useVoice }))
        const start = await startRedubCue(projectId, useVoice, targetLang, payloadCues, idx)
        cur = start
        jobId = start.job_id
        localStorage.setItem(lsKey, jobId)
      }
      
      while (cur && cur.status === "running") { 
        await sleep(1200)
        cur = await getAdhocSyncStatus(jobId)
      }
      localStorage.removeItem(lsKey)
      if (!cur || cur.status === "failed") throw new Error((cur && cur.error) || "Redub failed")
      setCues((prev) => prev.map((x, i) => (i === idx ? { ...x, dubbed: x.translated } : x)))
      const base = (cur.synced_audio_url || "").split("?")[0]
      if (base) setTtsAudioUrl(`${base}?v=${Date.now()}`)
      notify({ severity: "success", message: `Cue ${idx + 1} re-voiced` })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setRedubbing((s) => { const n = new Set(s); n.delete(idx); return n })
    }
  }, [cues, voice, voices, targetLang, projectId, speakersMap])

  // ── Multi-language (Video Refine) ─────────────────────────────────────────
  // Make `lang` the active language: its voice + per-cue translation + dub drive
  // the editor, so generate/redub keep working against the active language.
  const activateLang = useCallback((name, voiceName, list) => {
    const langs = list || languages
    const v = voiceName ?? langs.find((l) => l.name === name)?.voice ?? ""
    setSelectedLang(name); setTargetLang(name); setVoice(v)
    setTtsAudioUrl(dubUrls[name] || "")
    const hasDub = !!dubUrls[name]
    setCues((prev) => prev.map((c) => {
      const t = c.translations?.[name] || ""
      const dur = Math.max(0, (c.end ?? 0) - (c.start ?? 0))
      const cps = computeCps(t, dur)
      return { ...c, translated: t, dubbed: hasDub ? t : undefined, cps, rushed: dur > 0 && cps > CPS_MAX }
    }))
  }, [languages, dubUrls])

  const addLanguage = useCallback((name, voiceName) => {
    setLanguages((prev) => {
      const next = prev.some((l) => l.name === name)
        ? prev.map((l) => (l.name === name ? { name, voice: voiceName } : l))
        : [...prev, { name, voice: voiceName }]
      activateLang(name, voiceName, next)
      return next
    })
  }, [activateLang])

  const [translating, setTranslating] = useState(false)
  const translateToSelected = useCallback(async (resumeJobId = null) => {
    if (!cues || !cues.length) return
    if (!selectedLang) { notify({ severity: "error", message: "Add a language first (the + button)." }); return }
    setTranslating(true)
    try {
      const lsKey = `DubStudio-translate-${projectId}`
      let cur
      let jobId
      const isResuming = typeof resumeJobId === 'string'
      if (isResuming) {
        jobId = resumeJobId
        cur = await getAdhocTranslateStatus(jobId)
      } else {
        const start = await startTranslateCues(cues, selectedLang)
        cur = start
        jobId = start.job_id
        localStorage.setItem(lsKey, jobId)
      }

      while (cur && cur.status === "running") {
        await sleep(1500)
        cur = await getAdhocTranslateStatus(jobId)
      }
      localStorage.removeItem(lsKey)
      if (!cur || cur.status === "failed") throw new Error((cur && cur.error) || "Translation failed")
      const out = cur.cues || []
      setCues((prev) => prev.map((c, i) => {
        const t = out[i]?.translated || ""
        const dur = Math.max(0, (c.end ?? 0) - (c.start ?? 0))
        const cps = computeCps(t, dur)
        return { ...c, translated: t, translations: { ...(c.translations || {}), [selectedLang]: t }, cps, rushed: dur > 0 && cps > CPS_MAX }
      }))
      notify({ severity: "success", message: `Translated to ${selectedLang}` })
    } catch (e) {
      notify({ severity: "error", message: e.message })
    } finally {
      setTranslating(false)
    }
  }, [cues, selectedLang, notify])

  // Resume jobs if they were running when the component unmounted
  useEffect(() => {
    if (!loaded) return
    const tJob = localStorage.getItem(`DubStudio-translate-${projectId}`)
    if (tJob) translateToSelected(tJob)
    
    const dJob = localStorage.getItem(`DubStudio-dub-${projectId}`)
    if (dJob) generateDub(null, dJob)

    if (cues) {
      for (let i = 0; i < cues.length; i++) {
        const rJob = localStorage.getItem(`DubStudio-redub-${projectId}-${i}`)
        if (rJob) redubOne(i, null, rJob)
      }
    }
  }, [loaded]) // Only run once when loaded becomes true


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
        onBack={onBack}
        refineMode={refine}
        sourceLang={sourceLang}
        setSourceLang={setSourceLang}
      />
    )
  }

  return (
    <AdvancedEditor
      projectId={projectId}
      onBack={onBack}
      cues={cues} setCues={setCues}
      sourcePath={sourcePath} setSourcePath={setSourcePath}
      ttsAudioUrl={ttsAudioUrl}
      targetLang={targetLang} setTargetLang={setTargetLang}
      voice={voice} setVoice={setVoice} voices={voices}
      reviewed={reviewed} setReviewed={setReviewed}
      generateDub={generateDub} busy={busy} statusMsg={statusMsg}
      redubOne={redubOne} redubbing={redubbing}
      refineMode={refine} pdfPath={pdfPath} setPdfPath={setPdfPath}
      languages={languages} selectedLang={selectedLang} onSwitchLang={activateLang}
      onAddLanguage={addLanguage} onTranslate={translateToSelected} translating={translating}
      speakersMap={speakersMap} setSpeakersMap={setSpeakersMap}
      sourceLang={sourceLang}
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

function SetupScreen({ onExtracted, sourcePath, setSourcePath, targetLang, setTargetLang, onBack, refineMode = false, sourceLang = "English", setSourceLang = () => {} }) {
  const { notify } = useNotify()
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState("")
  const [availableLangs, setAvailableLangs] = useState([])

  useEffect(() => {
    if (refineMode) {
      getLanguages().then((l) => setAvailableLangs(l || [])).catch(() => {})
    }
  }, [refineMode])

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
    setStatus(refineMode ? "Transcribing…" : "Extracting words and translating...")
    try {
      // Video Refine transcribes ONLY (empty target lang) — no translation, no dub.
      const started = await startAdhocTranslate(sourcePath, refineMode ? "" : targetLang)
      let cur = started
      for (let i = 0; i < 400 && cur.status === "running"; i++) {
        await sleep(1500)
        cur = await getAdhocTranslateStatus(started.job_id)
        if (cur.message) setStatus(cur.message)
      }
      if (cur.status === "failed") {
        notify({ severity: "error", message: cur.error || (refineMode ? "Transcription failed" : "Translation failed") })
        setBusy(false); setStatus("")
      } else if (cur.status === "done") {
        notify({ severity: "success", message: refineMode ? "Transcript ready" : "Cues extracted — generating dub…" })
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
        {onBack && (
          <button onClick={onBack} style={{ color: colors.textDim, fontSize: fonts.sm, marginBottom: 14 }}>← Back to projects</button>
        )}
        <h2 style={{ color: colors.text, marginBottom: 8, fontSize: fonts.xxl, fontWeight: fonts.bold }}>{refineMode ? "Start Video Refine" : "Start Voiceover Project"}</h2>
        <p style={{ color: colors.muted, marginBottom: 24, fontSize: fonts.sm }}>
          {refineMode
            ? "Transcribe the original narration. You'll AI-Refine it, then translate and dub inside the editor — whenever you're ready."
            : `Extract the dialogue, translate it, and generate the ${targetLang} voiceover — all in one step.`}
        </p>

        <div style={{ marginBottom: 20 }}>
          <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 8 }}>Source Media (Audio or Video)</label>
          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="secondary" onClick={pickAudio} disabled={busy}>Select File</Button>
            <input type="text" value={sourcePath} onChange={(e) => setSourcePath(e.target.value)} disabled={busy}
              placeholder="/absolute/path/to/media.mp4"
              style={{ flex: 1, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "8px 12px", borderRadius: radius.sm }} />
          </div>
        </div>

        {refineMode ? (
          <div style={{ marginBottom: 24 }}>
            <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 8 }}>Source Language</label>
            <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)} disabled={busy}
              style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }}>
              {availableLangs.length > 0
                ? availableLangs.map((l) => <option key={l.name} value={l.name}>{l.name}</option>)
                : <option value={sourceLang}>{sourceLang}</option>
              }
            </select>
          </div>
        ) : (
          <div style={{ marginBottom: 24 }}>
            <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 8 }}>Target Language</label>
            <input type="text" value={targetLang} onChange={(e) => setTargetLang(e.target.value)} disabled={busy}
              style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "8px 12px", borderRadius: radius.sm }} />
          </div>
        )}

        <Button variant="primary" onClick={runTranslation} disabled={busy || !sourcePath} loading={busy} style={{ width: "100%", padding: 12 }}>
          {busy ? status || "Working..." : refineMode ? "Transcribe" : "Start Dubbing"}
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

function AdvancedEditor({ projectId, onBack, cues, setCues, sourcePath, ttsAudioUrl, targetLang, voice, setVoice, voices, reviewed, setReviewed, generateDub, busy, statusMsg, redubOne, redubbing, refineMode = false, pdfPath = "", setPdfPath = () => {}, languages = [], selectedLang = "", onSwitchLang = () => {}, onAddLanguage = () => {}, onTranslate = () => {}, translating = false, speakersMap = {}, setSpeakersMap = () => {}, sourceLang = "English" }) {
  const { notify } = useNotify()
  const [exporting, setExporting] = useState(false)
  const [speakerModalOpen, setSpeakerModalOpen] = useState(false)
  const [mergeModalTarget, setMergeModalTarget] = useState(null)

  // ── Video Refine: manga-panel cropping (opt-in; Voiceover ignores all of it) ──
  const [rightView, setRightView] = useState(refineMode ? "panels" : "source")  // "source" | "panels"
  const [cropIdx, setCropIdx] = useState(0)        // which cue a crop attaches to
  const [cropping, setCropping] = useState(false)
  const [upscaling, setUpscaling] = useState(null) // {total,done} | null
  const [selected, setSelected] = useState(() => new Set())   // cues picked for Combine
  const [addLangOpen, setAddLangOpen] = useState(false)       // add-language modal

  const openPdf = async () => {
    let path = pdfPath
    try {
      if (window.pywebview?.api?.pick_file) {
        const picked = await window.pywebview.api.pick_file(["PDF (*.pdf)"])
        if (picked) path = picked
      } else {
        const picked = window.prompt("Absolute path to the manga PDF:", pdfPath)
        if (picked) path = picked.trim()
      }
    } catch (e) { notify({ severity: "error", message: e.message }); return }
    if (path) { setPdfPath(path); setRightView("panels") }
  }
  const attachPanel = async (dataUrl) => {
    const idx = cropIdx
    if (idx < 0 || idx >= cues.length || !dataUrl) return false
    setCropping(true)
    try {
      const res = await savePanel(projectId, idx, dataUrl)
      const v = `?v=${Date.now()}`
      setCues((prev) => prev.map((c, i) => i === idx ? { ...c, image: (res.image || "") + v, raw: res.raw || "" } : c))
      notify({ severity: "success", message: `Cue ${idx + 1}: panel attached` })
      for (let j = idx + 1; j < cues.length; j++) { if (!cues[j].image) { setCropIdx(j); break } }
      return true
    } catch (e) { notify({ severity: "error", message: e.message }); return false }
    finally { setCropping(false) }
  }
  const removePanel = (idx) => {
    deletePanel(projectId, idx).catch(() => {})
    setCues((prev) => prev.map((c, i) => i === idx ? { ...c, image: null, raw: null } : c))
  }
  const runUpscaleAll = async () => {
    try {
      const res = await upscaleAll(projectId)
      if (!res.total) { notify({ severity: "info", message: "Nothing to upscale — all panels are done." }); return }
      setUpscaling({ total: res.total, done: 0 })
      notify({ severity: "success", message: `Upscaling ${res.total} panel(s)…` })
    } catch (e) { notify({ severity: "error", message: e.message }) }
  }
  useEffect(() => {
    if (!upscaling) return
    const t = setInterval(async () => {
      try {
        const s = await getUpscaleStatus(projectId)
        setUpscaling({ total: s.total, done: s.done })
        if (!s.running) { clearInterval(t); setUpscaling(null); notify({ severity: "success", message: "Upscaling complete." }) }
      } catch { clearInterval(t); setUpscaling(null) }
    }, 1500)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [upscaling?.total, projectId])

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
    if (v) { 
      const isSource = rightView === "source"
      v.volume = isSource ? sourceVol / 10 : 0
      v.muted = !isSource || sourceVol === 0
    }
  }, [sourceVol, rightView])
  useEffect(() => {
    const a = audioRef.current
    if (a) {
      const isDub = rightView !== "source"
      a.volume = isDub ? dubVol / 10 : 0
      a.muted = !isDub || dubVol === 0
    }
  }, [dubVol, ttsAudioUrl, rightView])
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

  // Spacebar toggles playback — except while typing in a text field (so editing
  // a translation with spaces still works normally).
  useEffect(() => {
    const onKey = (e) => {
      if (e.code !== "Space" && e.key !== " ") return
      const t = e.target
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return
      e.preventDefault()
      togglePlay()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [togglePlay])

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

  const pushUndo = () => { setUndoStack((s) => [...s, cues]); setRedoStack([]) }

  const updateCue = (idx, newTranslated) => {
    setCues((prev) => {
      const next = [...prev]
      const c = next[idx]
      const dur = Math.max(0, (c.end ?? 0) - (c.start ?? 0))
      const cps = computeCps(newTranslated, dur)
      // Keep the per-language map in sync with the active language (Video Refine).
      const translations = refineMode ? { ...(c.translations || {}), [selectedLang]: newTranslated } : c.translations
      next[idx] = { ...c, translated: newTranslated, translations, cps, rushed: dur > 0 && cps > CPS_MAX }
      return next
    })
  }

  // ── Combine selected cues into one (Video Refine) ─────────────────────────
  const toggleSelect = (idx) => setSelected((s) => { const n = new Set(s); n.has(idx) ? n.delete(idx) : n.add(idx); return n })
  const combineSelected = () => {
    const idxs = [...selected].sort((a, b) => a - b)
    if (idxs.length < 2) return
    pushUndo()
    setCues((prev) => {
      const group = idxs.map((i) => prev[i])
      const join = (arr) => arr.filter(Boolean).join(" ").replace(/\s+/g, " ").trim()
      const translations = {}
      group.forEach((c) => { for (const k in (c.translations || {})) translations[k] = join([translations[k], c.translations[k]]) })
      const start = Math.min(...group.map((c) => c.start ?? 0))
      const end = Math.max(...group.map((c) => c.end ?? 0))
      const translated = translations[targetLang] || join(group.map((c) => c.translated))
      const cps = computeCps(translated, Math.max(0, end - start))
      const merged = {
        ...group[0],
        text: join(group.map((c) => c.text)),
        translated, translations, start, end, cps, rushed: cps > CPS_MAX,
        image: group.find((c) => c.image)?.image || null,
        raw: group.find((c) => c.raw)?.raw || null,
        dubbed: undefined,
      }
      const out = []
      prev.forEach((c, i) => { if (i === idxs[0]) out.push(merged); else if (!idxs.includes(i)) out.push(c) })
      return out
    })
    setSelected(new Set())
    notify({ severity: "success", message: `Combined ${idxs.length} cues` })
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
        const next = cues.map((x, i) => (i === idx ? { 
          ...x,
          text: selectedLang === sourceLang ? res.translated : x.text,
          translated: res.translated, 
          translations: { ...(x.translations || {}), [selectedLang]: res.translated },
          cps: res.cps, 
          rushed: res.rushed 
        } : x))
        setCues(next)
        notify({ severity: "success", message: `Cue ${idx + 1} refined → ${res.cps} CPS` })
        // AI Fix also redubs that one cue automatically (if a dub already exists).
        if (ttsAudioUrl && redubOne) await redubOne(idx, next)
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

  /* ── AI Refine (rewrite the whole narration) ──────────────────────────── */

  const [refineOpen, setRefineOpen] = useState(false)
  const [refineBusy, setRefineBusy] = useState(false)

  const runRefine = async (instructions) => {
    setRefineBusy(true)
    try {
      const res = await refineScript(
        cues.map((c) => c.translated || c.text || ""),
        { durations: cues.map((c) => Math.max(0, (c.end ?? 0) - (c.start ?? 0))), level: "standard", instructions, lang: targetLang },
      )
      const lines = res?.lines || []
      setUndoStack((s) => [...s, cues])    // whole-script refine is one undo step
      setRedoStack([])
      setCues((prev) => prev.map((c, i) => {
        const t = lines[i] ?? c.translated ?? c.text
        const dur = Math.max(0, (c.end ?? 0) - (c.start ?? 0))
        const cps = computeCps(t, dur)
        return { 
          ...c, 
          text: selectedLang === sourceLang ? t : c.text,
          translated: t, 
          translations: { ...(c.translations || {}), [selectedLang]: t },
          cps, 
          rushed: dur > 0 && cps > CPS_MAX 
        }
      }))
      notify({ severity: "success", message: `Refined — Undo to revert` })
      setRefineOpen(false)
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setRefineBusy(false)
    }
  }

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
      <TopBar onExport={doExport} exporting={exporting} canExport={!!ttsAudioUrl} onBack={onBack}
        onRefine={() => setRefineOpen(true)} />
      {refineOpen && (
        <RefineModal busy={refineBusy} targetLang={targetLang}
          onClose={() => !refineBusy && setRefineOpen(false)} onGenerate={runRefine} />
      )}
      {addLangOpen && (
        <AddLanguageModal voices={voices} existing={languages.map((l) => l.name)}
          onClose={() => setAddLangOpen(false)}
          onAdd={(name, vname) => { onAddLanguage(name, vname); setAddLangOpen(false) }} />
      )}
      {speakerModalOpen && (
        <SpeakerModal 
          speakersMap={speakersMap} 
          setSpeakersMap={setSpeakersMap} 
          voices={voices} 
          onClose={() => setSpeakerModalOpen(false)} 
          onAddSpeaker={(newSpeaker) => setSpeakersMap(prev => ({ ...prev, [newSpeaker]: "" }))}
        />
      )}
      {mergeModalTarget && (
        <MergeModal 
          targetSpeaker={mergeModalTarget} 
          speakersMap={speakersMap} 
          onClose={() => setMergeModalTarget(null)}
          onMerge={(target) => {
            setCues(prev => prev.map(c => c.speaker === mergeModalTarget ? { ...c, speaker: target } : c))
            setSpeakersMap(prev => { const n = {...prev}; delete n[mergeModalTarget]; return n })
            setMergeModalTarget(null)
          }} 
        />
      )}

      {/* Top split area */}
      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>

        {/* Left: cue editor */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>

          {/* Left header */}
          <div style={{ height: 56, borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", padding: "0 16px", justifyContent: "space-between", background: colors.panel, flexShrink: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "6px 10px" }}>
                <span style={{ fontSize: 16 }}>🏳️</span>
                {refineMode ? (
                  <select value={selectedLang} onChange={(e) => onSwitchLang(e.target.value)}
                    style={{ background: "transparent", color: colors.text, border: "none", fontWeight: fonts.bold, outline: "none" }}>
                    {languages.length === 0 && <option value="">No language</option>}
                    {languages.map((l) => <option key={l.name} value={l.name}>{l.name}{l.name === sourceLang ? " (Original)" : ""}</option>)}
                  </select>
                ) : (
                  <span style={{ fontWeight: fonts.bold }}>{targetLang}</span>
                )}
              </div>
              {refineMode ? (
                <button onClick={() => setAddLangOpen(true)} title="Add a language + voice"
                  disabled={selectedLang === sourceLang && !cues.some(c => c.translated)}
                  style={{ width: 34, height: 34, borderRadius: radius.md, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.accent, fontSize: 18, fontWeight: fonts.bold, opacity: (selectedLang === sourceLang && !cues.some(c => c.translated)) ? 0.5 : 1 }}>+</button>
              ) : (
                <select value={voice} onChange={(e) => setVoice(e.target.value)}
                  style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "7px 10px", borderRadius: radius.md }}>
                  {voices.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                </select>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {refineMode && selected.size >= 2 && (
                <Button variant="secondary" onClick={combineSelected}>⛓ Combine {selected.size}</Button>
              )}
              {refineMode && selectedLang !== sourceLang && (
                <Button variant="secondary" onClick={onTranslate} disabled={translating || !selectedLang} loading={translating}
                  title={`Translate the ${sourceLang} cues to ${selectedLang || "the selected language"}`}>
                  {translating ? "Translating…" : `🌐 Translate`}
                </Button>
              )}
              <Button variant="primary" onClick={() => generateDub()} disabled={busy || (refineMode && !selectedLang) || (refineMode && selectedLang === sourceLang && !cues.some(c => c.translated))} loading={busy}
                title={refineMode && selectedLang === sourceLang && !cues.some(c => c.translated) ? "You must refine the original text before generating a dub" : "Generate the voiceover: text-to-speech + sync to the timeline"}
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
                dirty={!!ttsAudioUrl && c.dubbed !== undefined && c.translated !== c.dubbed}
                redubbing={redubbing?.has(i)}
                hideOriginal={hideOriginal}
                onToggleReviewed={() => toggleReviewed(i)}
                onPlay={() => playCue(c)}
                onRefine={() => refine(i)}
                onRedub={() => redubOne(i)}
                onChange={(val) => updateCue(i, val)}
                onFocus={beginEdit}
                onBlur={commitEdit}
                onSelect={() => { seek(c.start); if (refineMode) setCropIdx(i) }}
                refineMode={refineMode}
                cropTarget={refineMode && i === cropIdx}
                speakersMap={speakersMap}
                onSpeakerChange={(s) => setCues(prev => prev.map((c, idx) => idx === i ? { ...c, speaker: s } : c))}
                onOpenSpeakerModal={() => setSpeakerModalOpen(true)}
                onAddSpeaker={() => {
                  const maxIdx = Math.max(0, ...cues.map(c => parseInt((c.speaker||"").replace("Speaker ", "") || 0)))
                  setCues(prev => prev.map((c, idx) => idx === i ? { ...c, speaker: `Speaker ${maxIdx + 1}` } : c))
                }}
                onDeleteSpeaker={(s) => setMergeModalTarget(s)}
                onRemovePanel={() => removePanel(i)}
                isSelected={selected.has(i)}
                onToggleSelect={() => toggleSelect(i)}
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
              {refineMode ? (
                <div style={{ display: "flex", background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, overflow: "hidden" }}>
                  {["source", "panels", "preview"].map((v) => (
                    <button key={v} onClick={() => setRightView(v)}
                      style={{ padding: "6px 12px", fontSize: fonts.sm, fontWeight: fonts.medium,
                        background: rightView === v ? colors.accent : "transparent",
                        color: rightView === v ? "#000" : colors.textDim }}>
                      {v === "source" ? "🎬 Source" : v === "panels" ? "🖼 Panels" : "▶ Preview"}
                    </button>
                  ))}
                </div>
              ) : (
                <span style={{ fontWeight: fonts.bold }}>Source Preview</span>
              )}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: fonts.sm }}>
              {refineMode && rightView === "panels" ? (
                <>
                  <Button variant="secondary" onClick={runUpscaleAll} disabled={!!upscaling} style={{ padding: "5px 10px" }}>
                    {upscaling ? `Upscaling ${upscaling.done}/${upscaling.total}…` : "⬆ Upscale all"}
                  </Button>
                  <Button variant="primary" onClick={openPdf} style={{ padding: "5px 10px" }}>{pdfPath ? "Change PDF" : "Open PDF"}</Button>
                </>
              ) : (
                <>
                  <span style={{ color: colors.textDim }}>{formatTime(totalTime)}</span>
                  <span style={{ color: reviewedPct === 100 ? colors.success : colors.textDim }}>Reviewed {reviewedPct}%</span>
                  <button onClick={checkAll} style={{ color: colors.accent, fontSize: fonts.sm }}>Check All</button>
                </>
              )}
            </div>
          </div>

          {/* Panel cropper (Video Refine) — always rendered but hidden to preserve state */}
          {refineMode && (
            <div style={{ display: rightView === "panels" ? "flex" : "none", flex: 1, minHeight: 0 }}>
              <PdfReader pdfUrl={pdfPath ? mediaSrc(pdfPath) : ""} cropping={cropping} activeCue={cropIdx + 1}
                onOpen={openPdf} onAttach={attachPanel} />
            </div>
          )}

          <div style={{ flex: 1, minHeight: 0, display: refineMode && rightView === "panels" ? "none" : "flex", flexDirection: "column", padding: 16, gap: 12, overflowY: "auto" }}>
            <div style={{ position: "relative", borderRadius: radius.md, overflow: "hidden", background: "#000", display: "flex", alignItems: "center", justifyContent: "center", minHeight: 200 }}>
              {videoSrc ? (
                <>
                  <video
                    ref={videoRef}
                    src={videoSrc}
                    muted={sourceVol === 0 || rightView !== "source"}
                    onLoadedMetadata={(e) => setDuration(e.target.duration || 0)}
                    onTimeUpdate={onTimeUpdate}
                    onSeeking={syncDub}
                    onPlay={() => { setPlaying(true) }}
                    onPause={() => { setPlaying(false) }}
                    onClick={togglePlay}
                    style={{ width: "100%", maxHeight: 360, objectFit: "contain", display: rightView === "preview" ? "none" : "block", cursor: "pointer" }}
                  />
                  {rightView === "preview" && (
                    <div onClick={togglePlay} style={{ width: "100%", height: 360, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                      {(() => {
                        let displayIdx = -1
                        for (let i = 0; i < cues.length; i++) {
                          if (currentTime >= cues[i].start) displayIdx = i
                          else break
                        }
                        return displayIdx >= 0 && cues[displayIdx]?.image ? (
                          <img src={mediaSrc(cues[displayIdx].image)} alt="Panel Preview" style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
                        ) : (
                          <div style={{ color: colors.muted }}>{displayIdx >= 0 ? "No panel for this cue" : "Waiting for first cue…"}</div>
                        )
                      })()}
                    </div>
                  )}
                </>
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

function TopBar({ onExport, exporting, canExport, onBack, onRefine }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <div style={{ height: 52, flexShrink: 0, background: colors.panel, borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {onBack && (
          <button onClick={onBack} title="Back to projects"
            style={{ display: "flex", alignItems: "center", gap: 6, color: colors.textDim, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "6px 12px" }}>
            ← Projects
          </button>
        )}
        <button onClick={onRefine} title="Rewrite the narration into a polished recap"
          style={{ display: "flex", alignItems: "center", gap: 8, border: `1px solid ${colors.accent}`, borderRadius: radius.full, padding: "6px 14px", color: colors.accent, fontWeight: fonts.bold, background: "transparent" }}>
          ✦ AI Refine
        </button>
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
   AI Refine modal — rewrite the whole narration into a polished recap
───────────────────────────────────────────────────────────────────────────── */

function RefineModal({ busy, targetLang, onClose, onGenerate }) {
  const [instructions, setInstructions] = useState("")
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 520, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, boxShadow: "0 12px 40px rgba(0,0,0,0.6)", padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>✦ AI Refine</h2>
          <button onClick={onClose} style={{ color: colors.muted, fontSize: 18 }}>✕</button>
        </div>
        <p style={{ color: colors.muted, fontSize: fonts.sm, marginBottom: 16 }}>
          Rewrite the whole narration into a polished recap. Timing and line count stay the same.
        </p>

        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Instructions</label>
        <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} disabled={busy}
          placeholder="e.g. Make it more dramatic. Focus on the action. Keep it punchy."
          style={{ width: "100%", minHeight: 70, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, borderRadius: radius.sm, padding: "10px 12px", fontSize: fonts.base, resize: "vertical", fontFamily: fonts.ui }} />

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 18 }}>
          <span style={{ fontSize: 11, color: colors.muted }}>AI-generated content may be inaccurate.</span>
          <Button variant="primary" onClick={() => onGenerate(instructions)} disabled={busy || !instructions.trim()} loading={busy}>
            {busy ? "Refining…" : "✦ Generate"}
          </Button>
        </div>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Add-language modal (Video Refine): pick a language, then a voice for it
───────────────────────────────────────────────────────────────────────────── */

function AddLanguageModal({ voices, existing, onClose, onAdd }) {
  const { notify } = useNotify()
  const [langs, setLangs] = useState([])      // [{code,name,engine}]
  const [lang, setLang] = useState("")
  const [voice, setVoice] = useState("")

  useEffect(() => {
    getLanguages().then((l) => {
      setLangs(l || [])
      setLang((l || []).find((x) => !existing.includes(x.name))?.name || l?.[0]?.name || "")
    }).catch((e) => notify({ severity: "error", message: e.message }))
  }, [])

  // Voices whose language matches the picked language (fall back to all if none).
  const matches = voices.filter((v) => (v.language || "").toLowerCase() === lang.toLowerCase())
  const voiceList = matches.length ? matches : voices
  // Derived: if the chosen voice isn't valid for this language, default to the first.
  const effVoice = voiceList.some((v) => v.name === voice) ? voice : (voiceList[0]?.name || "")

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>Add a language</h2>
          <button onClick={onClose} style={{ color: colors.muted, fontSize: 20 }}>✕</button>
        </div>

        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Language</label>
        <select value={lang} onChange={(e) => setLang(e.target.value)}
          style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 16 }}>
          {langs.map((l) => (
            <option key={l.name} value={l.name} disabled={existing.includes(l.name)}>
              {l.name}{existing.includes(l.name) ? " (added)" : ""}
            </option>
          ))}
        </select>

        <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Voice profile</label>
        <select value={effVoice} onChange={(e) => setVoice(e.target.value)}
          style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 8 }}>
          {voiceList.length === 0 && <option value="">No voices — create one under Voices</option>}
          {voiceList.map((v) => <option key={v.name} value={v.name}>{v.name}{v.language ? ` · ${v.language}` : ""}</option>)}
        </select>
        {!matches.length && voices.length > 0 && (
          <p style={{ color: colors.muted, fontSize: fonts.xs, marginBottom: 8 }}>No voice tagged for {lang} — showing all voices.</p>
        )}

        <Button variant="primary" disabled={!lang || !effVoice} onClick={() => onAdd(lang, effVoice)}
          style={{ width: "100%", padding: 12, borderRadius: radius.md, fontWeight: fonts.bold, marginTop: 8 }}>
          Add {lang}
        </Button>
      </div>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Cue row
───────────────────────────────────────────────────────────────────────────── */

function CueRow({ index, cue, active, reviewed, refining, dirty, redubbing, hideOriginal, onToggleReviewed, onPlay, onRefine, onRedub, onChange, onFocus, onBlur, onSelect, refineMode = false, cropTarget = false, onRemovePanel, isSelected = false, onToggleSelect, speakersMap = {}, onSpeakerChange = () => {}, onOpenSpeakerModal = () => {}, onAddSpeaker = () => {}, onDeleteSpeaker = () => {} }) {
  const cps = cue.cps ?? 0
  const rushed = cue.rushed ?? cps > 20
  const body = (
    <div data-cue={index} onClick={onSelect} style={{ display: "flex", flexDirection: "column", gap: 8, cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: fonts.sm }}>
        <span style={{ fontWeight: fonts.bold, fontSize: fonts.md, color: active ? colors.accent : colors.text }}>{String(index + 1).padStart(2, "0")}</span>
        <button onClick={onToggleReviewed} title="Mark reviewed"
          style={{ color: reviewed ? colors.success : colors.muted, fontSize: 14, lineHeight: 1 }}>
          {reviewed ? "✓" : "○"}
        </button>
        <SpeakerBadge 
          speaker={cue.speaker || "Speaker 0"} 
          speakersMap={speakersMap} 
          onChange={onSpeakerChange} 
          onOpenModal={onOpenSpeakerModal} 
          onAdd={onAddSpeaker} 
          onDelete={onDeleteSpeaker} 
        />
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
        {(dirty || redubbing) && (
          <button onClick={onRedub} disabled={redubbing}
            title="Re-voice just this line with your edited translation"
            style={{
              display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, fontWeight: fonts.bold,
              color: "#fff", background: redubbing ? colors.accent2 : colors.accent,
              border: "none", borderRadius: radius.full, padding: "3px 10px",
              cursor: redubbing ? "wait" : "pointer",
            }}>
            {redubbing
              ? <><span style={{ width: 9, height: 9, border: "1.5px solid currentColor", borderTopColor: "transparent", borderRadius: "50%", display: "inline-block", animation: "ms-spin 0.65s linear infinite" }} /> Redubbing…</>
              : <><IconHeadphones small /> Redub</>}
          </button>
        )}
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
          onClick={(e) => e.stopPropagation()}
          rows={1}
          style={{ width: "100%", background: colors.cueTrans, border: "none", color: colors.text, padding: "10px 12px", fontSize: fonts.base, resize: "vertical", minHeight: 44, display: "block", fontFamily: fonts.ui }}
        />
      </div>
    </div>
  )

  if (!refineMode) return body

  // Video Refine: a panel thumbnail to the left. Click it to make this cue the
  // crop target; ✕ deletes the attached panel.
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "stretch" }}>
      <input type="checkbox" checked={isSelected} onChange={onToggleSelect} onClick={(e) => e.stopPropagation()}
        title="Select for Combine" style={{ alignSelf: "flex-start", marginTop: 30, accentColor: colors.accent, width: 16, height: 16, flexShrink: 0 }} />
      <button onClick={onSelect} title={cropTarget ? "Crop target" : "Make this the crop target"}
        style={{ width: 70, flexShrink: 0, alignSelf: "flex-start", marginTop: 26, position: "relative",
          aspectRatio: "3 / 4", borderRadius: radius.md, overflow: "hidden", background: colors.panel2,
          border: `2px solid ${cropTarget ? colors.accent : colors.border}`,
          boxShadow: cropTarget ? `0 0 0 2px ${colors.accent}` : "none",
          display: "flex", alignItems: "center", justifyContent: "center" }}>
        {cue.image
          ? <img src={`${FILES_ORIGIN}${cue.image}`} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
          : <span style={{ color: colors.muted, fontSize: 10, textAlign: "center", padding: 4 }}>{cropTarget ? "crop here" : "no panel"}</span>}
        {cue.image && (
          <span role="button" onClick={(e) => { e.stopPropagation(); onRemovePanel() }} title="Remove panel"
            style={{ position: "absolute", top: 3, right: 3, width: 16, height: 16, borderRadius: "50%", background: "rgba(0,0,0,0.65)", color: "#fff", fontSize: 10, lineHeight: "16px", textAlign: "center" }}>✕</span>
        )}
      </button>
      <div style={{ flex: 1, minWidth: 0 }}>{body}</div>
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

/* ─────────────────────────────────────────────────────────────────────────────
   Multi-Speaker UI Components
───────────────────────────────────────────────────────────────────────────── */

function SpeakerBadge({ speaker, speakersMap, onChange, onOpenModal, onAdd, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const assignedVoice = speakersMap[speaker]
  const displayName = speaker === "Speaker 0" ? "Speaker 0" : (assignedVoice || speaker)

  const allSpeakers = Object.keys(speakersMap).length ? Object.keys(speakersMap) : [speaker]
  if (!allSpeakers.includes(speaker)) allSpeakers.push(speaker)
  allSpeakers.sort((a, b) => {
    const na = parseInt(a.replace("Speaker ", "") || 0)
    const nb = parseInt(b.replace("Speaker ", "") || 0)
    return na - nb
  })

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button 
        onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
        style={{ color: colors.info, fontWeight: fonts.medium, cursor: "pointer", background: "none", border: "none", padding: 0 }}
      >
        {displayName} <span style={{ fontSize: 10 }}>▼</span>
      </button>
      
      {open && (
        <div style={{
          position: "absolute", top: "100%", left: 0, zIndex: 100,
          background: colors.panel, border: `1px solid ${colors.border}`, 
          borderRadius: radius.md, padding: "4px 0", minWidth: 180,
          boxShadow: "0 4px 12px rgba(0,0,0,0.5)", marginTop: 4
        }}>
          <div style={{ padding: "8px 12px", borderBottom: `1px solid ${colors.border}`, color: colors.textDim, fontSize: fonts.sm, cursor: "pointer" }}
               onClick={(e) => { e.stopPropagation(); setOpen(false); onOpenModal() }}>
            Change Speaker Voice
          </div>
          <div style={{ maxHeight: 200, overflowY: "auto" }}>
            {allSpeakers.map(s => (
              <div key={s} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 12px" }}>
                <button
                  onClick={(e) => { e.stopPropagation(); setOpen(false); onChange(s) }}
                  style={{ flex: 1, textAlign: "left", background: "none", border: "none", color: s === speaker ? colors.accent : colors.text, cursor: "pointer", fontSize: fonts.sm }}
                >
                  {s === "Speaker 0" ? "Speaker 0" : (speakersMap[s] || s)}
                </button>
                {s !== "Speaker 0" && (
                  <button onClick={(e) => { e.stopPropagation(); setOpen(false); onDelete(s) }} style={{ color: colors.error, background: "none", border: "none", cursor: "pointer", fontSize: 12 }}>✕</button>
                )}
              </div>
            ))}
          </div>
          <div style={{ padding: "8px 12px", borderTop: `1px solid ${colors.border}`, color: colors.textDim, fontSize: fonts.sm, cursor: "pointer" }}
               onClick={(e) => { e.stopPropagation(); setOpen(false); onAdd() }}>
            Add Speaker {Math.max(0, ...allSpeakers.map(s => parseInt((s||"").replace("Speaker ", "") || 0))) + 1}
          </div>
        </div>
      )}
    </div>
  )
}

function MergeModal({ targetSpeaker, speakersMap, onMerge, onClose }) {
  const [selected, setSelected] = useState("")
  const available = Object.keys(speakersMap).filter(s => s !== targetSpeaker)
  if (!available.includes("Speaker 0") && targetSpeaker !== "Speaker 0") available.push("Speaker 0")
  available.sort((a, b) => parseInt(a.replace("Speaker ", "") || 0) - parseInt(b.replace("Speaker ", "") || 0))

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: colors.panel, padding: 24, borderRadius: radius.lg, width: 400, border: `1px solid ${colors.border}` }}>
        <h3 style={{ margin: "0 0 16px", color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>Merge Sentences</h3>
        <p style={{ color: colors.textDim, marginBottom: 16, fontSize: fonts.sm }}>
          Select a speaker to merge the sentences of <strong>{speakersMap[targetSpeaker] || targetSpeaker}</strong> with:
        </p>
        <select value={selected} onChange={e => setSelected(e.target.value)}
          style={{ width: "100%", background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "8px 12px", borderRadius: radius.md, marginBottom: 20 }}>
          <option value="">Select...</option>
          {available.map(s => <option key={s} value={s}>{speakersMap[s] || s}</option>)}
        </select>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!selected} onClick={() => onMerge(selected)}>Merge</Button>
        </div>
      </div>
    </div>
  )
}

function SpeakerModal({ speakersMap, setSpeakersMap, voices, onClose, onAddSpeaker }) {
  const allSpeakers = Object.keys(speakersMap).filter(s => s !== "Speaker 0")
  allSpeakers.sort((a, b) => parseInt(a.replace("Speaker ", "") || 0) - parseInt(b.replace("Speaker ", "") || 0))

  const [activeTab, setActiveTab] = useState(allSpeakers[0] || "Speaker 1")
  const [search, setSearch] = useState("")

  // If no speakers exist, ensure the active tab is correctly tracked when one is added
  useEffect(() => {
    if (allSpeakers.length > 0 && !allSpeakers.includes(activeTab)) {
      setActiveTab(allSpeakers[allSpeakers.length - 1])
    }
  }, [allSpeakers.length])

  const filteredVoices = voices.filter(v => v.name.toLowerCase().includes(search.toLowerCase()) || (v.language && v.language.toLowerCase().includes(search.toLowerCase())))

  const assignVoice = (vname) => {
    setSpeakersMap(prev => ({ ...prev, [activeTab]: vname }))
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: colors.panel, width: "90%", maxWidth: 1000, height: "85vh", display: "flex", flexDirection: "column", borderRadius: radius.lg, border: `1px solid ${colors.border}`, overflow: "hidden" }}>
        
        <div style={{ display: "flex", alignItems: "center", padding: "16px 20px", borderBottom: `1px solid ${colors.border}`, gap: 16 }}>
          <div style={{ display: "flex", gap: 8, flex: 1, overflowX: "auto", paddingBottom: 4 }}>
            {allSpeakers.map(s => {
              const assigned = speakersMap[s]
              let bg = "transparent", col = colors.textDim, border = `1px solid ${colors.border}`
              if (activeTab === s) {
                bg = colors.accent; col = "#000"; border = `1px solid ${colors.accent}`
              } else if (!assigned && s !== "Speaker 0") {
                bg = "rgba(251,191,36,0.15)"; col = colors.warning; border = `1px solid ${colors.warning}`
              } else if (!assigned && s === "Speaker 0") {
                bg = "rgba(255,255,255,0.05)"; col = colors.text; border = `1px solid ${colors.border}`
              } else {
                border = `1px solid ${colors.textDim}`; col = colors.text
              }
              return (
                <button key={s} onClick={() => setActiveTab(s)}
                  style={{ background: bg, color: col, border, padding: "6px 12px", borderRadius: radius.full, fontSize: fonts.sm, fontWeight: fonts.bold, cursor: "pointer", whiteSpace: "nowrap" }}>
                  {s}
                </button>
              )
            })}
            <button onClick={() => {
              const maxIdx = Math.max(0, ...allSpeakers.map(s => parseInt((s||"").replace("Speaker ", "") || 0)))
              const nextSpeaker = `Speaker ${maxIdx + 1}`
              onAddSpeaker(nextSpeaker)
              setActiveTab(nextSpeaker)
            }}
            style={{ background: colors.panel2, color: colors.text, border: `1px dashed ${colors.border}`, padding: "6px 12px", borderRadius: radius.full, fontSize: fonts.sm, fontWeight: fonts.bold, cursor: "pointer", whiteSpace: "nowrap" }}>
              + Add Speaker
            </button>
          </div>
          <button onClick={onClose} style={{ color: colors.textDim, background: "none", border: "none", fontSize: 24, cursor: "pointer" }}>✕</button>
        </div>

        {allSpeakers.length === 0 ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: colors.textDim }}>
            Click "+ Add Speaker" to create a new character profile.
          </div>
        ) : (
          <>
            <div style={{ padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: `1px solid ${colors.border}` }}>
              <div style={{ fontSize: fonts.base, color: colors.text }}>Assign a voice to <strong style={{color: colors.accent}}>{activeTab}</strong></div>
              <input type="text" placeholder="Search for a speaker..." value={search} onChange={e => setSearch(e.target.value)}
                style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "8px 12px", borderRadius: radius.md, width: 250, fontSize: fonts.sm }} />
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
                {filteredVoices.map(v => {
                  const isActive = speakersMap[activeTab] === v.name
                  return (
                    <div key={v.name} onClick={() => assignVoice(v.name)}
                      style={{
                        background: colors.panel2, border: `1px solid ${isActive ? colors.accent : colors.border}`,
                        borderRadius: radius.md, padding: 12, cursor: "pointer",
                        boxShadow: isActive ? `0 0 0 1px ${colors.accent}` : "none",
                        display: "flex", alignItems: "center", gap: 12
                      }}>
                      <div style={{ width: 40, height: 40, borderRadius: radius.full, background: colors.bg, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, color: isActive ? colors.accent : colors.text }}>
                        {v.name.charAt(0).toUpperCase()}
                      </div>
                      <div style={{ flex: 1, overflow: "hidden" }}>
                        <div style={{ color: colors.text, fontWeight: fonts.bold, fontSize: fonts.sm, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{v.name}</div>
                        {v.language && <div style={{ color: colors.textDim, fontSize: fonts.xs, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{v.language}</div>}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          </>
        )}

      </div>
    </div>
  )
}

