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
import { listVoices, getLanguages, quickTTS, quickTTSStatus } from "../api/voices"
import { startAdhocTranslate, getAdhocTranslateStatus, getAdhocSyncStatus, startDubCues, startRedubCue, startRedubCues, startRestoreAudio, refineCue, refineScript, getDubSession, saveDubSession, exportDub, startTranslateCues } from "../api/speech"
import { listProjects } from "../api/projects"
import { useApp, actions, PAGES } from "../store/app"
import { useNotify } from "../store/notify"
import { savePanel, upscaleAll, getUpscaleStatus, narrateAll, getRecapState, saveRecapState, getNarratePlan, getStoryMemory, updateStoryCharacter, deleteStoryCharacter, undoStoryMemory, redoStoryMemory, getMagiStatus, installMagi } from "../api/videoRefine"
import { FILES_ORIGIN, mediaSrc } from "../api/panels"
import { importPdf } from "../api/media"
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
  const [pdfPath, setPdfPath] = useState("")   // Video Refine only (manga PDF)
  // Video Refine multi-language: a project can hold several languages, each with
  // its own voice; the active one drives targetLang/voice/translated/ttsAudioUrl.
  const [languages, setLanguages] = useState([])     // [{ name, voice }]
  const [selectedLang, setSelectedLang] = useState("")
  const [dubUrls, setDubUrls] = useState({})         // { langName: syncedAudioUrl }
  const [speakersMap, setSpeakersMap] = useState({}) // { "Speaker 0": "alloy" }
  const [sourceLang, setSourceLang] = useState("English") // Original language of the source media
  // Languages whose dub was fit to a timeline that has since MOVED (the source
  // dub was regenerated and repacked). Non-destructive: just a banner until each
  // language is re-translated/re-dubbed against the new timing.
  const [staleTimingLangs, setStaleTimingLangs] = useState([])
  const [kind, setKind] = useState("")   // "recap" = PDF-sourced (no video, cues from narration)
  const [recapBoxes, setRecapBoxes] = useState([])   // persisted crop boxes (Recap)

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
        // Multi-language: use the saved list, else migrate the single language.
        let langs = Array.isArray(s.languages) && s.languages.length
          ? s.languages
          : (s.targetLang ? [{ name: s.targetLang, voice: s.voice || "" }] : [])
        // Video Refine keeps an English baseline (the video's original audio).
        // Recap makes NO language assumption — the FIRST language the user adds
        // becomes the original, so a fresh recap starts with an empty list.
        const isRecap = s.kind === "recap"
        const srcName = s.sourceLang || (isRecap ? "" : "English")
        if (refine && srcName && !langs.some((l) => l.name === srcName)) {
          langs = [{ name: srcName, voice: s.voice || "" }, ...langs]
        }
        setLanguages(langs)
        const sel = s.selectedLang || langs[0]?.name || s.targetLang || ""
        setSelectedLang(sel)
        // Recap / Video Refine: the ACTIVE language IS the dub target, so keep
        // targetLang in sync with it on load. Otherwise a stale "French" default
        // (or a saved stale value) makes an English narration dub land in the French
        // folder and skips the timeline repack (which needs targetLang === sourceLang).
        if (refine && sel) setTargetLang(sel)
        setDubUrls(s.dubUrls || (s.ttsAudioUrl && s.targetLang ? { [s.targetLang]: s.ttsAudioUrl } : {}))
        if (s.speakersMap) setSpeakersMap(s.speakersMap)
        if (s.sourceLang) setSourceLang(s.sourceLang)
        else if (isRecap) setSourceLang("")   // fresh recap: no assumed original language
        if (Array.isArray(s.staleTimingLangs)) setStaleTimingLangs(s.staleTimingLangs)
        if (s.kind) setKind(s.kind)
        if (Array.isArray(s.recapBoxes)) setRecapBoxes(s.recapBoxes)
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
          // Frozen SOURCE timestamps: scrub the Source video with these forever.
          // start/end is the (movable) dub timeline — it repacks when the source
          // dub regenerates. Migration seeds them from the current start/end.
          if (next.sourceStart === undefined) next = { ...next, sourceStart: next.start ?? 0, sourceEnd: next.end ?? 0 }
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
        cues: cues || [], ttsAudioUrl, targetLang, sourcePath, voice, pdfPath,
        languages, selectedLang, dubUrls, speakersMap, sourceLang, staleTimingLangs, kind, recapBoxes,
        updatedAt: Date.now(),
      }).catch(() => { /* best-effort; next change retries */ })
    }, 700)
    return () => clearTimeout(t)
  }, [loaded, cues, ttsAudioUrl, targetLang, sourcePath, voice, pdfPath, languages, selectedLang, dubUrls, speakersMap, sourceLang, staleTimingLangs, kind, recapBoxes, projectId])

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
    const useVoice = voice || voiceForLang(voices, targetLang)
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
        // Recap timeline authority: the ORIGINAL (source-language) dub locks the
        // cue timeline from its REAL audio (gapless split); translated dubs then fit
        // into it. Also lock on the first dub when the original hasn't been generated
        // yet — otherwise the cues stay on the provisional text-length estimate, whose
        // slots are far longer than the actual audio.
        const timelineLocked = !!dubUrls[sourceLang]
        const repackTimings = refine && (targetLang === sourceLang || !timelineLocked)
        // Recap: one clip per panel (never merge cues into a single read), so each
        // panel gets its own audio with real silence between.
        const group = kind !== "recap"
        const start = await startDubCues(payloadCues, useVoice, targetLang, projectId, repackTimings, group)
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
      const audioDurs = cur.audio_durs || []
      const audioKeys = cur.audio_keys || []
      setCues((prev) => {
        const baseCues = cur.updated_cues || prev
        return baseCues.map((c, i) => ({
          ...c,
          dubbed: c.translated || prev[i]?.translated,
          dubbedVoice: speakersMap[c.speaker] || useVoice,
          // Remember THIS language's real audio length per cue → the timeline can
          // show the silence gap after a short translated line.
          audioDurs: { ...(c.audioDurs || {}), [targetLang]: audioDurs[i] },
          // …and this clip's version key, so undo/redo can restore its audio.
          audioKeys: audioKeys[i] ? { ...(c.audioKeys || {}), [targetLang]: audioKeys[i] } : (c.audioKeys || {}),
        }))
      })
      // Timeline honesty: repacking the SOURCE dub moves every cue's slot, so any
      // other language's existing dub was fit to timing that no longer exists —
      // flag them (non-destructive banner). Regenerating a translation's dub
      // against the current timeline clears its flag.
      if (refine) {
        if (targetLang === sourceLang && cur.updated_cues) {
          setStaleTimingLangs(languages.filter((l) => l.name !== sourceLang && dubUrls[l.name]).map((l) => l.name))
        } else if (targetLang !== sourceLang) {
          setStaleTimingLangs((prev) => prev.filter((n) => n !== targetLang))
        }
      }
      notify({ severity: "success", message: "Dub generated!" })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setBusy(false)
      setStatusMsg("")
    }
  }, [cues, voice, voices, targetLang, projectId, speakersMap, refine, sourceLang, languages, dubUrls, kind])

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
        // Freeze the transcription timestamps — the Source tab scrubs with these
        // forever, while start/end becomes the movable dub timeline.
        sourceStart: c.start ?? 0,
        sourceEnd: c.end ?? 0,
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
    const useVoice = voice || voiceForLang(voices, targetLang)
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
        const start = await startRedubCue(projectId, useVoice, targetLang, payloadCues, idx, kind !== "recap")
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
      // Apply the fresh per-cue durations (timeline split) and clip version keys
      // (audio undo). Redub reassembles the whole track, so durations can shift on
      // every cue; keys change only for the re-voiced group (null = keep existing).
      const newDurs = cur.audio_durs || []
      const newKeys = cur.audio_keys || []
      const dursOk = newDurs.length === (cuesNow.length)
      setCues((prev) => prev.map((x, i) => {
        const patch = dursOk ? { audioDurs: { ...(x.audioDurs || {}), [targetLang]: newDurs[i] } } : {}
        const key = newKeys[i]
        if (key) patch.audioKeys = { ...(x.audioKeys || {}), [targetLang]: key }
        return i === idx
          ? { ...x, dubbed: x.translated, dubbedVoice: speakersMap[x.speaker] || useVoice, ...patch }
          : { ...x, ...patch }
      }))
      const base = (cur.synced_audio_url || "").split("?")[0]
      if (base) setTtsAudioUrl(`${base}?v=${Date.now()}`)
      notify({ severity: "success", message: `Cue ${idx + 1} re-voiced` })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setRedubbing((s) => { const n = new Set(s); n.delete(idx); return n })
    }
  }, [cues, voice, voices, targetLang, projectId, speakersMap, kind])

  // Batch redub: re-voice SEVERAL cues, then reassemble the track ONCE (the
  // backend masters a single time instead of per-cue). Used by batch AI-Fix.
  const redubMany = useCallback(async (indices, cuesOverride) => {
    const cuesNow = cuesOverride || cues
    const useVoice = voice || voiceForLang(voices, targetLang)
    if (!useVoice) { notify({ severity: "error", message: "No voice available." }); return }
    const idxs = [...new Set(indices)].filter((i) => cuesNow[i])
    if (!idxs.length) return
    const idxSet = new Set(idxs)
    setRedubbing((s) => { const n = new Set(s); idxs.forEach((i) => n.add(i)); return n })
    try {
      const payloadCues = cuesNow.map((c) => ({ ...c, voice: speakersMap[c.speaker] || useVoice }))
      const start = await startRedubCues(projectId, useVoice, targetLang, payloadCues, idxs, kind !== "recap")
      let cur = start
      const jobId = start.job_id
      while (cur && cur.status === "running") { await sleep(1200); cur = await getAdhocSyncStatus(jobId) }
      if (!cur || cur.status === "failed") throw new Error((cur && cur.error) || "Redub failed")
      const newDurs = cur.audio_durs || []
      const newKeys = cur.audio_keys || []
      const dursOk = newDurs.length === cuesNow.length
      setCues((prev) => prev.map((x, i) => {
        const patch = dursOk ? { audioDurs: { ...(x.audioDurs || {}), [targetLang]: newDurs[i] } } : {}
        const key = newKeys[i]
        if (key) patch.audioKeys = { ...(x.audioKeys || {}), [targetLang]: key }
        return idxSet.has(i)
          ? { ...x, dubbed: x.translated, dubbedVoice: speakersMap[x.speaker] || useVoice, ...patch }
          : { ...x, ...patch }
      }))
      const base = (cur.synced_audio_url || "").split("?")[0]
      if (base) setTtsAudioUrl(`${base}?v=${Date.now()}`)
      notify({ severity: "success", message: `Re-voiced ${idxs.length} cue(s)` })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setRedubbing((s) => { const n = new Set(s); idxs.forEach((i) => n.delete(i)); return n })
    }
  }, [cues, voice, voices, targetLang, projectId, speakersMap, kind])

  // Audio undo/redo: restore the track to a snapshot's clip versions. The editor's
  // undo/redo (which lives in AdvancedEditor) calls this with the target snapshot's
  // cues; we ask the backend to copy each cue's archived clip back and reassemble.
  // A token guards against races when the user undoes/redoes quickly.
  const restoreTokenRef = useRef(0)
  const restoreAudioTo = useCallback(async (targetCues) => {
    const lang = targetLang
    const useVoice = voice || voiceForLang(voices, targetLang)
    if (!useVoice || !Array.isArray(targetCues)) return
    const keys = targetCues.map((c) => c.audioKeys?.[lang] || null)
    if (!keys.some(Boolean)) return   // nothing was archived for this language → can't restore
    const myToken = ++restoreTokenRef.current
    try {
      const payloadCues = targetCues.map((c) => ({ ...c, voice: speakersMap[c.speaker] || useVoice }))
      const start = await startRestoreAudio(projectId, useVoice, lang, payloadCues, keys, kind !== "recap")
      let cur = start
      while (cur && cur.status === "running") { await sleep(1200); cur = await getAdhocSyncStatus(cur.job_id) }
      if (myToken !== restoreTokenRef.current) return   // a newer undo/redo superseded this one
      if (cur && cur.status === "done") {
        const base = (cur.synced_audio_url || "").split("?")[0]
        if (base) setTtsAudioUrl(`${base}?v=${Date.now()}`)
      }
    } catch { /* best-effort; visuals already reverted */ }
  }, [voice, voices, targetLang, projectId, speakersMap, kind])

  // ── Multi-language (Video Refine) ─────────────────────────────────────────
  // Make `lang` the active language: its voice + per-cue translation + dub drive
  // the editor, so generate/redub keep working against the active language.
  const activateLang = useCallback((name, voiceName, list) => {
    const langs = list || languages
    const v = voiceName ?? langs.find((l) => l.name === name)?.voice ?? ""
    setSelectedLang(name); setTargetLang(name); setVoice(v)
    setTtsAudioUrl(dubUrls[name] || "")
    const hasDub = !!dubUrls[name]
    // `cues` is null on a brand-new project — guard it, or adding the first
    // language (before any narration) blank-screens the app (null.map()).
    setCues((prev) => (prev || []).map((c, i, arr) => {
      const t = c.translations?.[name] || ""
      const dur = effDur(arr, i)
      const cps = computeCps(t, dur)
      return { ...c, translated: t, dubbed: hasDub ? t : undefined, cps, rushed: dur > 0 && cps > CPS_MAX }
    }))
  }, [languages, dubUrls])

  const addLanguage = useCallback((name, voiceName) => {
    // The FIRST language added is the ORIGINAL (source) language — this is how a
    // recap picks its original without us assuming English.
    setSourceLang((sl) => sl || name)
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
      setCues((prev) => prev.map((c, i, arr) => {
        const t = out[i]?.translated || ""
        const dur = effDur(arr, i)
        const cps = computeCps(t, dur)
        // A fresh translation clears this language's "original changed" flag.
        const staleLangs = (c.staleLangs || []).filter((n) => n !== selectedLang)
        return { ...c, translated: t, translations: { ...(c.translations || {}), [selectedLang]: t }, staleLangs, cps, rushed: dur > 0 && cps > CPS_MAX }
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

  // Recap projects skip the setup screen entirely — there is no video to
  // transcribe; cues are born from narrated PDF crops inside the editor.
  if ((!cues || cues.length === 0) && kind !== "recap") {
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
      cues={cues || []} setCues={setCues}
      sourcePath={sourcePath} setSourcePath={setSourcePath}
      ttsAudioUrl={ttsAudioUrl}
      targetLang={targetLang} setTargetLang={setTargetLang}
      voice={voice} setVoice={setVoice} voices={voices}
      generateDub={generateDub} busy={busy} statusMsg={statusMsg}
      redubOne={redubOne} redubMany={redubMany} restoreAudioTo={restoreAudioTo} redubbing={redubbing}
      refineMode={refine} pdfPath={pdfPath} setPdfPath={setPdfPath}
      languages={languages} selectedLang={selectedLang} onSwitchLang={activateLang}
      onAddLanguage={addLanguage} onTranslate={translateToSelected} translating={translating}
      speakersMap={speakersMap} setSpeakersMap={setSpeakersMap}
      sourceLang={sourceLang}
      dubUrls={dubUrls} staleTimingLangs={staleTimingLangs}
      recapMode={kind === "recap"} recapBoxes={recapBoxes} setRecapBoxes={setRecapBoxes}
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

function AdvancedEditor({ projectId, onBack, cues, setCues, sourcePath, ttsAudioUrl, targetLang, voice, setVoice, voices, generateDub, busy, statusMsg, redubOne, redubMany = () => {}, restoreAudioTo = () => {}, redubbing, refineMode = false, pdfPath = "", setPdfPath = () => {}, languages = [], selectedLang = "", onSwitchLang = () => {}, onAddLanguage = () => {}, onTranslate = () => {}, translating = false, speakersMap = {}, setSpeakersMap = () => {}, sourceLang = "English", dubUrls = {}, staleTimingLangs = [], recapMode = false, recapBoxes = [], setRecapBoxes = () => {} }) {
  const { notify } = useNotify()
  const pdfInputRef = useRef(null)
  const [exporting, setExporting] = useState(false)
  const [speakerModalOpen, setSpeakerModalOpen] = useState(false)
  const [mergeModalTarget, setMergeModalTarget] = useState(null)

  // ── Video Refine: manga-panel cropping (opt-in; Voiceover ignores all of it) ──
  const [rightView, setRightView] = useState(refineMode ? "panels" : "source")  // "source" | "panels"
  const [cropIdx, setCropIdx] = useState(0)        // which cue a crop attaches to
  const [cropping, setCropping] = useState(false)
  const [upscaling, setUpscaling] = useState(null) // {total,done} | null

  // ── Recap: PDF crops → AI narration → cues ────────────────────────────────
  const [pendingCrops, setPendingCrops] = useState(null)   // crops awaiting the prompt confirm
  const [narrating, setNarrating] = useState(false)
  const [narrPrompt, setNarrPrompt] = useState(
    "Write an engaging, dramatic third-person recap narration in present tense — punchy, flowing, like a top manhwa recap channel.")
  const [castSeed, setCastSeed] = useState("")            // authoritative cast list (names + looks)
  const [resetMemory, setResetMemory] = useState(false)  // start a fresh chapter
  const [storyMemoryOpen, setStoryMemoryOpen] = useState(false)
  const [storyMemory, setStoryMemory] = useState({ characters: [], events: [] })
  const [magi, setMagi] = useState({ installed: false })
  const [useMagi, setUseMagi] = useState(false)
  const [installingMagi, setInstallingMagi] = useState(false)
  // The language the narration is written in. On the FIRST narration it becomes
  // the project's original language; on later chapters it's fixed to that.
  const [langOptions, setLangOptions] = useState([])     // [{code,name,engine}]
  const [narrateLang, setNarrateLang] = useState("")

  useEffect(() => {
    if (!recapMode) return
    getLanguages().then((l) => setLangOptions(l || [])).catch(() => {})
  }, [recapMode])

  // The cast seed is optional authored context. Canonical identities themselves
  // live in Story Memory, not in the retired registry editor.
  useEffect(() => {
    if (!recapMode) return
    getRecapState(projectId).then((s) => {
      setCastSeed(s?.cast_seed || "")
    }).catch(() => {})
    getMagiStatus().then((status) => {
      setMagi(status || { installed: false })
      setUseMagi(!!status?.installed)
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, recapMode])

  const runNarration = async () => {
    const crops = (pendingCrops || []).filter((c) => c && c.data && c.data.length > 200)
    // Narration language: fixed to the original once set (later chapters), else the
    // one chosen in the Narrate window (defaulting to the first listed). That choice
    // DEFINES the original language.
    const origLang = sourceLang || narrateLang || langOptions[0]?.name || ""
    if (!origLang) {
      setPendingCrops(null)
      notify({ severity: "error", message: "Choose the narration language first." })
      return
    }
    setPendingCrops(null)
    if (!crops.length) { notify({ severity: "error", message: "No usable crops to narrate." }); return }
    setNarrating(true)
    try {
      // Persist the cast seed first so the pipeline's identity resolver uses it.
      await saveRecapState(projectId, { cast_seed: castSeed }).catch(() => {})
      // Whole-chapter pass: the backend reads EVERY panel (vision chunked to the
      // model's image limit), resolves identities with full-chapter hindsight, then
      // narrates every panel in one go. Registry + rolling summary persist server-side.
      const plan = await getNarratePlan().catch(() => ({}))
      if (plan.vision_model) {
        notify({ severity: "info", message: `Vision: ${plan.vision_model} · Story: ${plan.reasoner_model} · whole-chapter pass` })
      }
      const lines = new Array(crops.length).fill("")
      notify({ severity: "info", message: `Narrating all ${crops.length} panel(s) — watch the Logs page for live progress…` })
      // Narrate in the chosen original language (no assumption of English).
      const langDirective = origLang && origLang.toLowerCase() !== "english"
        ? `\n\nIMPORTANT: Write the narration in ${origLang}.` : ""
      const res = await narrateAll(projectId, narrPrompt + langDirective, crops.map((c, k) => ({ index: k, data: c.data })), resetMemory, useMagi)
      for (const l of res.lines || []) lines[l.index] = l.text || ""
      setResetMemory(false)
      // Save each crop as its cue's panel, then append the new cues. Timing is a
      // provisional estimate — the ENGLISH dub repack defines the real timeline.
      const newCues = []
      let t = (cues || []).reduce((m, c) => Math.max(m, c.end ?? 0), 0)
      if (t > 0) t += 0.5
      for (let i = 0; i < crops.length; i++) {
        // Stable panel id (NOT the array index): survives cue deletion/reorder
        // without ever colliding with another cue's panel file on disk.
        const pid = newPanelId(i)
        const saved = await savePanel(projectId, pid, crops[i].data)
        const text = lines[i] || ""
        const dur = Math.max(2.5, Math.min(14, text.length / 15))
        newCues.push({
          // The narration is the ORIGINAL-language text — stored under that
          // language's slot so its dub can generate immediately (AI Refine optional).
          text, translated: text, translations: { [origLang]: text }, speaker: "Speaker 0",
          start: +t.toFixed(2), end: +(t + dur).toFixed(2),
          sourceStart: +t.toFixed(2), sourceEnd: +(t + dur).toFixed(2),
          // eslint-disable-next-line react-hooks/purity -- event handler, not render (cache-bust query)
          image: (saved.image || "") + `?v=${Date.now()}`, raw: saved.raw || "",
          // The cue now OWNS its panel: stable file id + the source crop geometry,
          // so its panel can be re-edited later without touching any other cue.
          panelId: pid, panelBox: crops[i].box || null,
        })
        t += dur + 0.3
      }
      // One atomic undo step: narration converts the staged boxes into cues that
      // own them, so the global cropper is cleared for the next batch. Undo
      // restores BOTH the previous cues and the staged boxes.
      pushUndo("narrate crops")
      setCues((prev) => [...(prev || []), ...newCues])
      setRecapBoxes([])
      // Register the narration language as the ORIGINAL (first time only) — this is
      // what makes the header language dropdown + the "+" appear afterward.
      if (!languages.some((l) => l.name === origLang)) onAddLanguage(origLang, "")
      notify({ severity: "success", message: `${crops.length} panel(s) narrated → ${crops.length} new cue(s)` })
    } catch (e) {
      notify({ severity: "error", message: e.message })
    } finally {
      setNarrating(false)
    }
  }

  const openStoryMemory = async () => {
    try {
      setStoryMemory(await getStoryMemory(projectId))
      setStoryMemoryOpen(true)
    } catch (err) {
      notify({ severity: "error", message: err.message })
    }
  }

  const installMagiModel = async () => {
    setInstallingMagi(true)
    try {
      const status = await installMagi()
      setMagi(status)
      setUseMagi(!!status?.installed)
      notify({ severity: "success", message: "Magi v3 is installed. Visual grounding will be used for this recap." })
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setInstallingMagi(false)
    }
  }
  const [addLangOpen, setAddLangOpen] = useState(false)       // add-language modal

  const openPdf = async () => {
    let path = pdfPath
    try {
      if (window.pywebview?.api?.pick_file) {
        const picked = await window.pywebview.api.pick_file(["PDF (*.pdf)"])
        if (picked) path = picked
      } else {
        pdfInputRef.current?.click()
        return
      }
    } catch (e) { notify({ severity: "error", message: e.message }); return }
    if (path) { setPdfPath(path); setRightView("panels") }
  }
  const importSelectedPdf = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    try {
      const imported = await importPdf(file)
      setPdfPath(imported.path)
      setRightView("panels")
      notify({ severity: "success", message: `Loaded ${imported.filename}` })
    } catch (err) { notify({ severity: "error", message: err.message }) }
  }
  // Panels are stored on disk by a STABLE per-cue id, never the array index —
  // deleting a cue shifts indexes, and index-keyed files let a later save
  // silently overwrite a panel another cue still references (project "corruption").
  // Legacy cues without a panelId keep resolving to their original index.
  // eslint-disable-next-line react-hooks/purity -- called from event handlers only, never during render
  const newPanelId = (offset = 0) => (Date.now() % 2_000_000_000) + offset

  const attachPanel = async (dataUrl, box = null) => {
    const idx = cropIdx
    if (idx < 0 || idx >= cues.length || !dataUrl) return false
    setCropping(true)
    try {
      pushUndo("attach panel")
      const pid = cues[idx].panelId != null ? cues[idx].panelId : newPanelId()
      const res = await savePanel(projectId, pid, dataUrl)
      // eslint-disable-next-line react-hooks/purity -- event handler, not render (cache-bust query)
      const v = `?v=${Date.now()}`
      setCues((prev) => prev.map((c, i) => i === idx ? { ...c, panelId: pid, image: (res.image || "") + v, raw: res.raw || "", panelBox: box || c.panelBox || null } : c))
      notify({ severity: "success", message: `Cue ${idx + 1}: panel attached` })
      for (let j = idx + 1; j < cues.length; j++) { if (!cues[j].image) { setCropIdx(j); break } }
      return true
    } catch (e) { notify({ severity: "error", message: e.message }); return false }
    finally { setCropping(false) }
  }
  const removePanel = (idx) => {
    pushUndo("remove panel")
    // DETACH only — the file stays on disk so Undo can restore the cue's image.
    // Orphaned files are cheap; deleting here made undone cues point at nothing.
    setCues((prev) => prev.map((c, i) => i === idx ? { ...c, image: null, raw: null, panelBox: null } : c))
  }

  // ── Per-cue panel editor: a cue's thumbnail opens ITS OWN crop, alone ──────
  const [editPanelIdx, setEditPanelIdx] = useState(null)   // cue index | null
  const saveEditedPanel = async (idx, dataUrl, box) => {
    pushUndo("edit panel")
    const pid = cues[idx].panelId != null ? cues[idx].panelId : newPanelId()
    const res = await savePanel(projectId, pid, dataUrl)
    // eslint-disable-next-line react-hooks/purity -- event handler, not render (cache-bust query)
    const v = `?v=${Date.now()}`
    setCues((prev) => prev.map((c, i) => i === idx ? { ...c, panelId: pid, image: (res.image || "") + v, raw: res.raw || "", panelBox: box || null } : c))
    notify({ severity: "success", message: `Cue ${idx + 1}: panel updated` })
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
  // Multi-cue selection for BATCH actions (batch ✦AI Fix). Holds cue indices.
  const [selectedCues, setSelectedCues] = useState(() => new Set())
  const toggleSelect = useCallback((i) => {
    setSelectedCues((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n })
  }, [])
  const clearSelection = useCallback(() => setSelectedCues(new Set()), [])

  // ── Unified undo / redo ────────────────────────────────────────────────────
  // ONE chronological history for EVERY edit in the project: cue text edits,
  // cue delete/merge/insert, AI refine, narration, panel attach/remove/re-crop,
  // and crop-box create/move/resize/delete. Each entry is an atomic snapshot of
  // { cues, boxes } so undo/redo can never desync translations from crops
  // (the old cues-only stack undid translations while crop boxes stayed put).
  const [undoStack, setUndoStack] = useState([])   // [{ cues, boxes, label }]
  const [redoStack, setRedoStack] = useState([])
  const editSnapshot = useRef(null)
  const HISTORY_MAX = 80

  const videoRef = useRef(null)
  const audioRef = useRef(null)
  const cueListRef = useRef(null)
  const timelineScrollRef = useRef(null)
  const stopAtRef = useRef(null)
  const playIntentRef = useRef(false)  // guards zombie canplay listeners

  const totalTime = useMemo(() => maxTime(cues), [cues])

  // Panels
  const [rightWidth, onColDown] = useSplitter({ initial: 420, min: 320, max: 760, axis: "x" })
  const [timelineH, onRowDown] = useSplitter({ initial: 200, min: 120, max: 420, axis: "y" })

  /* ── Media element wiring ─────────────────────────────────────────────── */

  // TWO CLOCKS, ONE DRIVER. In Video Refine each tab owns exactly one engine:
  //   Source  → the <video> plays with its own audio (dub fully stopped),
  //   Preview → the dub <audio> plays on ITS OWN clock (video fully stopped),
  //   Panels  → nothing plays.
  // Voiceover (refineMode=false) keeps the classic coupled behavior: the video
  // is the clock and the dub audio rides along, mixed by the two sliders.
  // In Panels (cropping) view there's no video, but the dub audio stays mounted
  // — so if a dub exists, let it be the driver there too. That's what restores
  // Space / Play to control timeline audio while you crop.
  // Does the ACTIVE language have a generated dub? Drives the timeline (cleared
  // when false) and what Space/Play can control.
  const hasDubForSelected = refineMode ? !!dubUrls[selectedLang] : !!ttsAudioUrl
  const driver = refineMode
    ? (rightView === "preview" ? "audio"
       : rightView === "source" ? "video"
       : (ttsAudioUrl ? "audio" : "none"))
    : "video"
  const [audioDuration, setAudioDuration] = useState(0)

  // Switching tabs stops whatever was playing — the engines never overlap.
  // setPlaying here syncs React to the elements we just paused: their own
  // onPause handlers are driver-gated and the driver has ALREADY changed by the
  // time the pause event fires, so they can't be relied on for this transition.
  useEffect(() => {
    if (!refineMode) return
    videoRef.current?.pause()
    audioRef.current?.pause()
    playIntentRef.current = false
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPlaying(false)
  }, [rightView, refineMode])

  // Pause all media when the user navigates to a different page.
  // Pages are kept mounted (display:none) to preserve state, but CSS hiding
  // does NOT stop <video>/<audio> playback — we must do it explicitly.
  const { state: appState } = useApp()
  // A recap project has BOTH refineMode and recapMode true and lives on the RECAP
  // page — so recapMode MUST win here. Getting this wrong made isActivePageRef
  // always false (Space did nothing) and the pause-effect mute playback on the
  // recap page (it thought we were off-page).
  const ownerPage = recapMode ? PAGES.RECAP : (refineMode ? PAGES.VIDEO_REFINE : PAGES.VOICEOVER)
  useEffect(() => {
    if (appState.page !== ownerPage) {
      videoRef.current?.pause()
      audioRef.current?.pause()
      playIntentRef.current = false
      setPlaying(false)
    }
    return () => {
      // Unmount cleanup: stop any playing media when the component is removed
      // (e.g. navigating back to the project list within the same page).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      videoRef.current?.pause()
      // eslint-disable-next-line react-hooks/exhaustive-deps
      audioRef.current?.pause()
    }
  }, [appState.page, ownerPage])

  // Ref mirror of "is this editor's page the active one?" — the spacebar
  // handler captures this via ref so it never fires on a hidden page (all pages
  // stay mounted, so ALL their global keydown listeners fire simultaneously).
  const isActivePageRef = useRef(true)
  useEffect(() => { isActivePageRef.current = appState.page === ownerPage }, [appState.page, ownerPage])

  // Keep volumes / rate in sync. The sliders are the ONLY mixing authority
  // within the active engine; WHICH engine runs is decided by the tab (driver),
  // never by muting the other one in the background.
  useEffect(() => {
    const v = videoRef.current
    if (v) { v.volume = sourceVol / 10; v.muted = sourceVol === 0 }
  }, [sourceVol])
  useEffect(() => {
    const a = audioRef.current
    if (a) { a.volume = dubVol / 10; a.muted = dubVol === 0 }
  }, [dubVol, ttsAudioUrl])
  useEffect(() => { if (videoRef.current) videoRef.current.playbackRate = speed }, [speed])
  useEffect(() => { if (audioRef.current) audioRef.current.playbackRate = speed }, [speed, ttsAudioUrl])

  // When the dub track URL changes, (re)load it and apply the current volume so
  // a stale/empty element never silently swallows playback.
  useEffect(() => {
    const a = audioRef.current
    if (a && ttsAudioUrl) a.load()   // volume is applied by the effect above
  }, [ttsAudioUrl])

  const syncDub = useCallback(() => {
    if (driver !== "video" || refineMode) return   // coupled mode only (Voiceover)
    const v = videoRef.current, a = audioRef.current
    if (!v || !a) return
    if (Math.abs(a.currentTime - v.currentTime) > 0.25) {
      try { a.currentTime = v.currentTime } catch { /* not yet seekable */ }
    }
  }, [driver, refineMode])

  const onTimeUpdate = useCallback(() => {
    const v = videoRef.current
    if (!v || driver !== "video") return
    setCurrentTime(v.currentTime)
    // NOTE: never re-seek the dub audio while it's playing — a seek mid-playback
    // glitches the sound. The two elements are started together and both advance
    // at 1×, so they stay in sync. We only resync on an explicit user seek.
    if (stopAtRef.current != null && v.currentTime >= stopAtRef.current) {
      stopAtRef.current = null
      v.pause()
    }
  }, [driver])

  // The dub audio's OWN clock — drives the playhead/panel image on Preview.
  const onAudioTime = useCallback(() => {
    const a = audioRef.current
    if (!a || driver !== "audio") return
    setCurrentTime(a.currentTime)
    if (stopAtRef.current != null && a.currentTime >= stopAtRef.current) {
      stopAtRef.current = null
      a.pause()
    }
  }, [driver])

  const play = useCallback(() => {
    const v = videoRef.current, a = audioRef.current
    if (driver === "none") return
    playIntentRef.current = true
    if (driver === "audio") {
      if (!a) return
      a.play().catch(() => {
        a.addEventListener("canplay",
          () => { if (playIntentRef.current) a.play().catch((err) => console.warn("dub audio play blocked:", err)) },
          { once: true })
      })
      return
    }
    if (!v) return
    v.play()
    // Voiceover only: the dub rides along with the video. In refine Source tab
    // the dub stays stopped — you're listening to the original.
    if (!refineMode && a) {
      try { a.currentTime = v.currentTime } catch { /* */ }
      a.play().catch(() => {
        a.addEventListener("canplay",
          () => { if (playIntentRef.current) a.play().catch((err) => console.warn("dub audio play blocked:", err)) },
          { once: true })
      })
    }
  }, [driver, refineMode])

  const pause = useCallback(() => {
    playIntentRef.current = false
    videoRef.current?.pause()
    audioRef.current?.pause()
  }, [])

  const togglePlay = useCallback(() => {
    stopAtRef.current = null
    const el = driver === "audio" ? audioRef.current : videoRef.current
    if (!el || driver === "none") return
    if (el.paused) play(); else pause()
  }, [play, pause, driver])

  const seek = useCallback((t) => {
    const v = videoRef.current, a = audioRef.current
    const limit = driver === "audio" ? (audioDuration || totalTime) : (duration || totalTime)
    const clamped = Math.max(0, Math.min(t, limit))
    if (driver === "audio") {
      if (a) { try { a.currentTime = clamped } catch { /* */ } }
    } else {
      if (v) v.currentTime = clamped
      if (!refineMode && a) { try { a.currentTime = clamped } catch { /* */ } }
    }
    setCurrentTime(clamped)
  }, [duration, totalTime, audioDuration, driver, refineMode])

  const playCue = useCallback((c) => {
    // Source tab scrubs the FROZEN transcription timestamps; everything else
    // (Preview / Voiceover) runs on the dub timeline (start/end).
    const useSource = refineMode && driver === "video"
    stopAtRef.current = useSource ? (c.sourceEnd ?? c.end) : c.end
    seek(useSource ? (c.sourceStart ?? c.start) : c.start)
    setTimeout(play, 0)
  }, [seek, play, driver, refineMode])

  // Spacebar toggles playback — except while typing in a text field (so editing
  // a translation with spaces still works normally).
  useEffect(() => {
    const onKey = (e) => {
      if (e.code !== "Space" && e.key !== " ") return
      const t = e.target
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return
      // Only the ACTIVE page's editor should respond — all pages stay mounted
      // (display:none) so every editor's global listener fires on every keypress.
      if (!isActivePageRef.current) return
      e.preventDefault()
      togglePlay()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [togglePlay])

  // The playhead updates from the video's own timeupdate (~4fps) and glides
  // smoothly between ticks via a CSS transition — no per-frame React re-render
  // (which was saturating the main thread and breaking audio).

  // Which cue is under the playhead. On the Source tab the clock is the video,
  // so match against the FROZEN transcription timestamps; everywhere else the
  // clock runs on the dub timeline (start/end).
  const activeIdx = useMemo(() => {
    const srcClock = refineMode && rightView === "source"
    return cues.findIndex((c) => {
      const s = srcClock ? (c.sourceStart ?? c.start) : c.start
      const e = srcClock ? (c.sourceEnd ?? c.end) : c.end
      return currentTime >= s && currentTime < e
    })
  }, [cues, currentTime, refineMode, rightView])

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

  // Snapshot the CURRENT project edit state as one atomic history entry.
  const snapshot = (label = "") => ({ cues, boxes: recapBoxes, label })
  const pushEntry = (entry) => {
    setUndoStack((s) => [...s.slice(-(HISTORY_MAX - 1)), entry])
    setRedoStack([])
  }
  const pushUndo = (label = "") => pushEntry(snapshot(label))
  const restore = (entry) => { setCues(entry.cues); setRecapBoxes(entry.boxes || []) }

  const updateCue = (idx, newTranslated) => {
    setCues((prev) => {
      const next = [...prev]
      const c = next[idx]
      const dur = effDur(next, idx)
      const cps = computeCps(newTranslated, dur)
      // Keep the per-language map in sync with the active language (Video Refine).
      const translations = refineMode ? { ...(c.translations || {}), [selectedLang]: newTranslated } : c.translations
      // Invariant: when the ORIGINAL language is selected, text and translated
      // are the same thing (the refined original) — keep them in lockstep so
      // Translate always works from the refined text, and flag other languages'
      // existing translations as stale.
      const isSource = refineMode && selectedLang === sourceLang
      const staleLangs = isSource
        ? [...new Set([...(c.staleLangs || []),
            ...Object.keys(c.translations || {}).filter((k) => k !== sourceLang && (c.translations[k] || "").trim())])]
        : c.staleLangs
      next[idx] = { ...c, translated: newTranslated, translations, cps, rushed: dur > 0 && cps > CPS_MAX,
        ...(isSource ? { text: newTranslated, staleLangs } : {}) }
      return next
    })
  }

  // Edit the ORIGINAL text directly (the top box — refine mode only). Syncs the
  // source-language slot and flags every already-translated language as stale
  // for this cue (non-destructive: a badge, nothing is deleted).
  const updateOriginal = (idx, t) => {
    setCues((prev) => prev.map((c, i, arr) => {
      if (i !== idx) return c
      const others = Object.keys(c.translations || {}).filter((k) => k !== sourceLang && (c.translations[k] || "").trim())
      const staleLangs = [...new Set([...(c.staleLangs || []), ...others])]
      const translations = { ...(c.translations || {}), [sourceLang]: t }
      const base = { ...c, text: t, translations, staleLangs }
      if (selectedLang === sourceLang) {
        const dur = effDur(arr, i)
        const cps = computeCps(t, dur)
        return { ...base, translated: t, cps, rushed: dur > 0 && cps > CPS_MAX }
      }
      return base
    }))
  }

  // ── Maestra-style per-cue actions (hover ✕ delete, +/merge between cues) ──
  const join = (arr) => arr.filter(Boolean).join(" ").replace(/\s+/g, " ").trim()

  const deleteCue = (idx) => {
    pushUndo("delete cue")
    setCues((prev) => prev.filter((_, i) => i !== idx))
  }

  // Merge cue idx with the one AFTER it (the between-cue merge control).
  const mergeAdjacent = (idx) => {
    if (idx < 0 || idx + 1 >= cues.length) return
    pushUndo("merge cues")
    setCues((prev) => {
      const a = prev[idx], b = prev[idx + 1]
      const translations = {}
      for (const c of [a, b]) for (const k in (c.translations || {})) translations[k] = join([translations[k], c.translations[k]])
      const start = Math.min(a.start ?? 0, b.start ?? 0)
      const end = Math.max(a.end ?? 0, b.end ?? 0)
      const translated = translations[targetLang] || join([a.translated, b.translated])
      // Score against the merged span, not effDur(prev, idx): after a merge the
      // indices in `prev` no longer line up, so this silently measured the
      // wrong window.
      const cps = computeCps(translated, Math.max(0, end - start))
      const merged = {
        ...a,
        text: join([a.text, b.text]),
        translated, translations, start, end, cps, rushed: cps > CPS_MAX,
        image: a.image || b.image || null, raw: a.raw || b.raw || null, dubbed: undefined,
      }
      return prev.flatMap((c, i) => (i === idx ? [merged] : i === idx + 1 ? [] : [c]))
    })
  }

  // Insert a fresh empty cue AFTER idx, timed into the gap to the next cue.
  const insertCueAfter = (idx) => {
    pushUndo("insert cue")
    setCues((prev) => {
      const a = prev[idx]
      const next = prev[idx + 1]
      const s = a ? (a.end ?? 0) : 0
      const e = next ? Math.max(s + 0.5, (next.start ?? s + 2)) : s + 2.5
      const fresh = {
        text: "", translated: "", translations: refineMode ? { [sourceLang]: "" } : {},
        speaker: a?.speaker || "Speaker 0",
        start: +s.toFixed(2), end: +e.toFixed(2), sourceStart: +s.toFixed(2), sourceEnd: +e.toFixed(2),
        cps: 0, image: null, raw: null,
      }
      return [...prev.slice(0, idx + 1), fresh, ...prev.slice(idx + 1)]
    })
  }

  const refine = async (idx) => {
    const c = cues[idx]
    if (!c || refining.has(idx)) return
    setRefining((s) => new Set(s).add(idx))
    const before = snapshot("AI fix cue")
    try {
      const res = await refineCue({
        text: c.text, translated: c.translated,
        // Judge against the EFFECTIVE window (until the next cue speaks) — the
        // raw end-start made short-slot lines look impossible to fix (26+ CPS)
        // when their audio actually fit in the dead air that followed.
        start: c.start, end: (c.start ?? 0) + effDur(cues, idx), langCode: targetLang,
      })
      if (res?.changed === false) {
        notify({ severity: "info", message: `Cue ${idx + 1} already fits (${res.cps} CPS) — no change.` })
      } else if (res?.translated) {
        pushEntry(before)   // make the AI edit undoable
        const next = cues.map((x, i) => (i === idx ? {
          ...x,
          text: selectedLang === sourceLang ? res.translated : x.text,
          translated: res.translated,
          translations: { ...(x.translations || {}), [selectedLang]: res.translated },
          staleLangs: (x.staleLangs || []).filter((n) => n !== selectedLang),
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

  // Batch ✦AI Fix: re-translate every selected cue shorter, then re-voice all the
  // ones that actually changed in a SINGLE reassembly (one master pass) — instead
  // of the one-by-one loop that redubs (and masters) per cue.
  const refineManySelected = async () => {
    const list = [...selectedCues].filter((i) => cues[i] && !refining.has(i)).sort((a, b) => a - b)
    if (!list.length) return
    setRefining((s) => { const n = new Set(s); list.forEach((i) => n.add(i)); return n })
    const before = snapshot("AI fix cues")
    try {
      let working = cues
      const changed = []
      for (const idx of list) {
        const c = working[idx]
        try {
          const res = await refineCue({
            text: c.text, translated: c.translated,
            start: c.start, end: (c.start ?? 0) + effDur(working, idx), langCode: targetLang,
          })
          if (res?.translated && res.changed !== false) {
            working = working.map((x, i) => (i === idx ? {
              ...x,
              text: selectedLang === sourceLang ? res.translated : x.text,
              translated: res.translated,
              translations: { ...(x.translations || {}), [selectedLang]: res.translated },
              staleLangs: (x.staleLangs || []).filter((n) => n !== selectedLang),
              cps: res.cps, rushed: res.rushed,
            } : x))
            changed.push(idx)
          }
        } catch { /* skip this cue, keep going */ }
      }
      if (changed.length) {
        pushEntry(before)            // one undo entry for the whole batch
        setCues(working)
        notify({ severity: "success", message: `Refined ${changed.length}/${list.length} cue(s)` })
        if (hasDubForSelected && redubMany) await redubMany(changed, working)
      } else {
        notify({ severity: "info", message: "All selected cues already fit — no change." })
      }
      clearSelection()
    } catch (err) {
      notify({ severity: "error", message: err.message })
    } finally {
      setRefining((s) => { const n = new Set(s); list.forEach((i) => n.delete(i)); return n })
    }
  }

  const beginEdit = () => { editSnapshot.current = snapshot("edit text") }
  const commitEdit = () => {
    if (editSnapshot.current && JSON.stringify(editSnapshot.current.cues) !== JSON.stringify(cues)) {
      pushEntry(editSnapshot.current)
    }
    editSnapshot.current = null
  }

  // The per-language audio identity of a cue list — if this differs between the
  // current state and the one we're restoring, the actual audio must be rebuilt.
  const audioSig = (arr, lang) => (arr || []).map((c) => c.audioKeys?.[lang] || "").join("|")

  const undo = () => {
    if (!undoStack.length) return
    const prev = undoStack[undoStack.length - 1]
    const needAudio = audioSig(cues, targetLang) !== audioSig(prev.cues, targetLang)
    setRedoStack((r) => [...r, snapshot()])
    setUndoStack((s) => s.slice(0, -1))
    restore(prev)
    if (needAudio) restoreAudioTo(prev.cues)   // rebuild the track from the old clip versions
  }
  const redo = () => {
    if (!redoStack.length) return
    const next = redoStack[redoStack.length - 1]
    const needAudio = audioSig(cues, targetLang) !== audioSig(next.cues, targetLang)
    setUndoStack((s) => [...s, snapshot()])
    setRedoStack((r) => r.slice(0, -1))
    restore(next)
    if (needAudio) restoreAudioTo(next.cues)
  }

  // ⌘Z / ⌃Z undo · ⇧⌘Z / ⌃Y redo — skipped while typing (the field's own
  // native undo applies there; our history captures the edit on blur).
  useEffect(() => {
    const onKey = (e) => {
      if (!(e.metaKey || e.ctrlKey)) return
      const k = e.key.toLowerCase()
      if (k !== "z" && k !== "y") return
      const t = e.target
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return
      if (!isActivePageRef.current) return
      e.preventDefault()
      if (k === "y" || (k === "z" && e.shiftKey)) redo()
      else undo()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cues, recapBoxes, undoStack, redoStack])


  /* ── AI Refine (rewrite the whole narration) ──────────────────────────── */

  const [refineOpen, setRefineOpen] = useState(false)
  const [refineBusy, setRefineBusy] = useState(false)

  const runRefine = async (instructions) => {
    setRefineBusy(true)
    try {
      const res = await refineScript(
        cues.map((c) => c.translated || c.text || ""),
        { durations: cues.map((c, i) => effDur(cues, i)), level: "standard", instructions, lang: targetLang },
      )
      const lines = res?.lines || []
      pushUndo("AI refine script")    // whole-script refine is one undo step
      setCues((prev) => prev.map((c, i, arr) => {
        const t = lines[i] ?? c.translated ?? c.text
        const dur = effDur(arr, i)
        const cps = computeCps(t, dur)
        // Refining the ORIGINAL flags every already-translated language stale;
        // refining a translation clears that language's own flag.
        const staleLangs = selectedLang === sourceLang
          ? [...new Set([...(c.staleLangs || []),
              ...Object.keys(c.translations || {}).filter((k) => k !== sourceLang && (c.translations[k] || "").trim())])]
          : (c.staleLangs || []).filter((n) => n !== selectedLang)
        return {
          ...c,
          text: selectedLang === sourceLang ? t : c.text,
          translated: t,
          translations: { ...(c.translations || {}), [selectedLang]: t },
          staleLangs,
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

  const videoSrc = recapMode ? "" : mediaSrc(sourcePath)   // recap: the "source" is the PDF

  /* ── Render ───────────────────────────────────────────────────────────── */

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100%", background: colors.bg, color: colors.text, overflow: "hidden", fontFamily: fonts.ui }}>
      <input ref={pdfInputRef} type="file" accept="application/pdf,.pdf" onChange={importSelectedPdf} style={{ display: "none" }} />

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
      {pendingCrops && (
        <div onClick={() => setPendingCrops(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 600, maxHeight: "88vh", overflowY: "auto", background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }}>
            <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold, marginBottom: 6 }}>Narrate {pendingCrops.length} panel{pendingCrops.length === 1 ? "" : "s"}</h2>
            <p style={{ color: colors.muted, fontSize: fonts.sm, marginBottom: 14 }}>
              A whole-chapter storyteller: vision reads every panel, then a reasoning model reads the ENTIRE chapter first to resolve who's who with hindsight. Story Memory carries forward only confirmed, evidence-backed identities, appearances, and events, so continuity improves without bloating the prompt.
            </p>

            {/* Narration language: chosen here on the FIRST narration — it becomes
                the project's original language. Locked to it for later chapters. */}
            <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Narrate in <span style={{ color: colors.muted }}>(this becomes the original language)</span></label>
            {sourceLang ? (
              <div style={{ background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 14, fontWeight: fonts.medium }}>
                {sourceLang} <span style={{ color: colors.muted, fontWeight: fonts.normal }}>· original (fixed for this recap)</span>
              </div>
            ) : (
              <select value={narrateLang || (langOptions[0]?.name || "")} onChange={(e) => setNarrateLang(e.target.value)}
                style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 14 }}>
                {langOptions.length === 0 && <option value="">Loading languages…</option>}
                {langOptions.map((l) => <option key={l.name} value={l.name}>{l.name}</option>)}
              </select>
            )}

            <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Cast list <span style={{ color: colors.muted }}>(optional but recommended — anchors identities)</span></label>
            <textarea value={castSeed} onChange={(e) => setCastSeed(e.target.value)} rows={3}
              placeholder={"Main character — black hair, swordsman; later muscular armored form\nRival — blonde, ally of the main character"}
              style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 14, fontFamily: fonts.ui, resize: "vertical" }} />

            <label style={{ display: "block", color: colors.textDim, fontSize: fonts.sm, marginBottom: 6 }}>Narration style / instructions</label>
            <textarea value={narrPrompt} onChange={(e) => setNarrPrompt(e.target.value)} rows={3}
              style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md, marginBottom: 12, fontFamily: fonts.ui, resize: "vertical" }} />

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 16 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, color: colors.textDim, fontSize: fonts.sm, cursor: "pointer" }}>
                <input type="checkbox" checked={resetMemory} onChange={(e) => setResetMemory(e.target.checked)} style={{ accentColor: colors.accent }} />
                Start a new chapter (reset the running summary, keep confirmed Story Memory)
              </label>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button onClick={openStoryMemory}
                  style={{ color: colors.info, fontSize: fonts.sm, background: "none", border: "none", cursor: "pointer" }}>
                  ⬡ Review Story Memory
                </button>
              </div>
            </div>

            <div style={{ background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "10px 12px", marginBottom: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, color: colors.text, fontSize: fonts.sm, cursor: magi.installed ? "pointer" : "default" }}>
                  <input type="checkbox" checked={useMagi} disabled={!magi.installed || installingMagi}
                    onChange={(event) => setUseMagi(event.target.checked)} style={{ accentColor: colors.accent }} />
                  <span><strong>Magi v3 visual grounding</strong> <span style={{ color: colors.muted }}>character boxes, OCR, and speech links</span></span>
                </label>
                {!magi.installed && <Button variant="secondary" disabled={installingMagi} onClick={installMagiModel} style={{ padding: "6px 10px" }}>
                  {installingMagi ? "Installing Magi…" : "Install Magi v3"}
                </Button>}
              </div>
              <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 6 }}>
                {magi.installed
                  ? `Installed locally${magi.device ? ` · active on ${magi.device}` : " · loads on first recap"}. Its visual evidence is reviewed through Story Memory.`
                  : "Optional local model. Its official checkpoint is downloaded only if you choose Install. It is licensed for personal, research, non-commercial and not-for-profit use."}
              </div>
            </div>

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <Button variant="secondary" onClick={() => setPendingCrops(null)}>Cancel</Button>
              <Button variant="primary" onClick={runNarration}>🪄 Narrate</Button>
            </div>
          </div>
        </div>
      )}
      {storyMemoryOpen && (
        <StoryMemoryModal
          memory={storyMemory}
          onClose={() => setStoryMemoryOpen(false)}
          onSaveCharacter={async (stableId, changes) => {
            const result = await updateStoryCharacter(projectId, stableId, changes)
            setStoryMemory(result.memory)
            return result.memory
          }}
          onDeleteCharacter={async (stableId) => { const memory = await deleteStoryCharacter(projectId, stableId); setStoryMemory(memory); return memory }}
          onUndo={async () => { const memory = await undoStoryMemory(projectId); setStoryMemory(memory); return memory }}
          onRedo={async () => { const memory = await redoStoryMemory(projectId); setStoryMemory(memory); return memory }}
        />
      )}
      {editPanelIdx != null && cues[editPanelIdx] && (
        <PanelEditorModal
          idx={editPanelIdx}
          cue={cues[editPanelIdx]}
          pdfUrl={pdfPath ? mediaSrc(pdfPath) : ""}
          onOpenPdf={openPdf}
          onSave={async (data, box) => { await saveEditedPanel(editPanelIdx, data, box); setEditPanelIdx(null) }}
          onRemove={() => { removePanel(editPanelIdx); setEditPanelIdx(null) }}
          onClose={() => setEditPanelIdx(null)}
        />
      )}
      {speakerModalOpen && (
        <SpeakerModal
          speakersMap={speakersMap}
          setSpeakersMap={setSpeakersMap}
          voices={voices}
          defaultVoice={voice || voiceForLang(voices, selectedLang)}
          onClose={() => setSpeakerModalOpen(false)}
          onAddSpeaker={(newSpeaker) => setSpeakersMap(prev => ({ ...prev, [newSpeaker]: prev[newSpeaker] || voiceForLang(voices, selectedLang) }))}
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
              {/* Recap makes no language assumption: until the user adds one, only
                  the + (add language) and Story Memory show. The language selector
                  appears once at least one language exists. */}
              {refineMode ? (
                languages.length > 0 && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "6px 10px" }}>
                    <span style={{ fontSize: 16 }}>🏳️</span>
                    <select value={selectedLang} onChange={(e) => onSwitchLang(e.target.value)}
                      style={{ background: "transparent", color: colors.text, border: "none", fontWeight: fonts.bold, outline: "none" }}>
                      {languages.map((l) => <option key={l.name} value={l.name}>{l.name}{l.name === sourceLang ? " (Original)" : ""}</option>)}
                    </select>
                  </div>
                )
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 8, background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: "6px 10px" }}>
                  <span style={{ fontSize: 16 }}>🏳️</span>
                  <span style={{ fontWeight: fonts.bold }}>{targetLang}</span>
                </div>
              )}
              {refineMode ? (
                // The + adds a TRANSLATION language, and only appears once the
                // original exists. The original itself is chosen in the Narrate
                // window (below), so a fresh recap has no + and no language dropdown.
                languages.length > 0 && (
                  <button onClick={() => setAddLangOpen(true)} title="Add another language + voice"
                    style={{ width: 34, height: 34, borderRadius: radius.md, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.accent, fontSize: 18, fontWeight: fonts.bold }}>+</button>
                )
              ) : (
                <select value={voice} onChange={(e) => setVoice(e.target.value)}
                  style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "7px 10px", borderRadius: radius.md }}>
                  {voices.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                </select>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {recapMode && (
                <Button variant="secondary" onClick={openStoryMemory}
                  title="Review evidence-backed characters and events">
                  ⬡ Story Memory
                </Button>
              )}
              {(() => {
                const isTranslation = refineMode && selectedLang !== sourceLang
                const hasTranslation = cues.some((c) => (c.translations?.[selectedLang] || "").trim())
                // A translation must be translated before it can be dubbed. Show ONLY
                // Translate until then; afterward the button becomes Generate Dub.
                if (isTranslation && !hasTranslation) {
                  return (
                    <Button variant="primary" onClick={onTranslate} disabled={translating || !selectedLang || !dubUrls[sourceLang]} loading={translating}
                      title={!dubUrls[sourceLang] ? `Generate the ${sourceLang} (original) dub first` : `Translate the ${sourceLang} cues to ${selectedLang}`}
                      style={{ background: colors.accent, color: "#000", fontWeight: fonts.bold, border: "none", borderRadius: radius.full, padding: "8px 18px" }}>
                      {translating ? "Translating…" : `🌐 Translate to ${selectedLang}`}
                    </Button>
                  )
                }
                // Generate Dub — only once there's a language + narrated cues.
                if (refineMode && (!selectedLang || cues.length === 0)) return null
                const noOriginalText = refineMode && selectedLang === sourceLang && !cues.some((c) => c.translated)
                const needsSourceDub = isTranslation && !dubUrls[sourceLang]
                const noVoice = refineMode && voices.length === 0   // generate falls back to any voice, so only truly blocked when NONE exist
                const disabled = busy || (refineMode && !selectedLang) || noOriginalText || needsSourceDub || noVoice
                const title = noVoice ? "Create a voice profile first (Voices section)"
                  : noOriginalText ? "Refine the original text before generating a dub"
                  : needsSourceDub ? `Generate the ${sourceLang} (original) dub first — it defines the timeline every translation fits into`
                  : "Generate the voiceover: text-to-speech + sync to the timeline"
                return (
                  <Button variant="primary" onClick={() => generateDub()} disabled={disabled} loading={busy} title={title}
                    style={{ background: colors.accent, color: "#000", fontWeight: fonts.bold, border: "none", borderRadius: radius.full, padding: "8px 18px" }}>
                    {busy ? statusMsg || "Working…"
                      : `✨ ${ttsAudioUrl ? "Regenerate" : "Generate"}${refineMode && selectedLang ? ` ${selectedLang}` : ""} Dub`}
                  </Button>
                )
              })()}
            </div>
          </div>

          {/* Workflow banners (Video Refine) — non-destructive honesty signals */}
          {refineMode && selectedLang !== sourceLang && !dubUrls[sourceLang] && (
            <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", background: "rgba(251,191,36,0.10)", borderBottom: `1px solid rgba(251,191,36,0.35)`, color: colors.warning, fontSize: fonts.sm }}>
              <span>⚠</span>
              <span>Generate the <b>{sourceLang} (Original)</b> dub first — it defines the timeline that {selectedLang || "each translation"} will be fitted into.</span>
            </div>
          )}
          {refineMode && staleTimingLangs.length > 0 && (
            <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", background: "rgba(251,191,36,0.10)", borderBottom: `1px solid rgba(251,191,36,0.35)`, color: colors.warning, fontSize: fonts.sm }}>
              <span>⚠</span>
              <span>The {sourceLang} timing changed — <b>{staleTimingLangs.join(", ")}</b> {staleTimingLangs.length > 1 ? "were" : "was"} fit to the old timeline. Re-translate + regenerate {staleTimingLangs.length > 1 ? "their dubs" : "its dub"} when ready.</span>
            </div>
          )}

          {/* Batch action bar — appears once one or more cues are checked. */}
          {selectedCues.size > 0 && (
            <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 12, padding: "8px 16px",
              background: "rgba(251,191,36,0.10)", borderBottom: `1px solid rgba(251,191,36,0.35)`, color: colors.text, fontSize: fonts.sm }}>
              <span style={{ fontWeight: fonts.bold }}>{selectedCues.size} selected</span>
              <div style={{ flex: 1 }} />
              {!(refineMode && selectedLang === sourceLang) && (
                <button onClick={refineManySelected} disabled={refining.size > 0 || busy}
                  title="AI: re-translate every selected line shorter, then re-voice them in one pass"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: fonts.bold,
                    color: refining.size > 0 ? colors.muted : colors.warning,
                    border: `1px solid ${refining.size > 0 ? colors.border : "rgba(251,191,36,0.5)"}`,
                    background: refining.size > 0 ? colors.panel2 : "rgba(251,191,36,0.14)",
                    borderRadius: radius.full, padding: "4px 12px", cursor: refining.size > 0 ? "wait" : "pointer" }}>
                  {refining.size > 0
                    ? <><span style={{ width: 10, height: 10, border: "1.5px solid currentColor", borderTopColor: "transparent", borderRadius: "50%", display: "inline-block", animation: "ms-spin 0.65s linear infinite" }} /> Fixing {refining.size}…</>
                    : `✦ AI Fix ${selectedCues.size} selected`}
                </button>
              )}
              <button onClick={clearSelection} disabled={refining.size > 0}
                style={{ color: colors.textDim, fontSize: 12, fontWeight: fonts.bold, padding: "4px 8px" }}>
                Clear
              </button>
            </div>
          )}

          {/* Cue list */}
          <div ref={cueListRef} style={{ flex: 1, overflowY: "auto", padding: "20px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
            {recapMode && cues.length === 0 && (
              <div style={{ margin: "auto", maxWidth: 340, textAlign: "center", color: colors.muted, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ fontSize: 30 }}>🪄</div>
                <div style={{ color: colors.text, fontWeight: fonts.bold }}>No cues yet</div>
                <div style={{ fontSize: fonts.sm, lineHeight: 1.5 }}>
                  On the right, drag boxes over the panels you want (they stack up and stay), then hit
                  <b style={{ color: colors.accent }}> Narrate crops</b>. Each crop becomes a cue here with its own AI narration.
                </div>
              </div>
            )}
            {cues.length > 0 && <CueDivider onInsert={() => insertCueAfter(-1)} />}
            {cues.map((c, i) => (
              <div key={i}>
              <CueRow
                index={i}
                cue={c}
                active={i === activeIdx}
                selected={selectedCues.has(i)}
                onToggleSelect={() => toggleSelect(i)}
                refining={refining.has(i)}
                defaultVoice={voice || voiceForLang(voices, selectedLang)}
                dirty={!!ttsAudioUrl && c.dubbed !== undefined && (c.translated !== c.dubbed || (c.dubbedVoice !== undefined && (speakersMap[c.speaker] || voice || voiceForLang(voices, targetLang)) !== c.dubbedVoice))}
                redubbing={redubbing?.has(i)}
                hideOriginal={hideOriginal}
                onPlay={() => playCue(c)}
                onRefine={() => refine(i)}
                onRedub={() => redubOne(i)}
                onChange={(val) => updateCue(i, val)}
                onFocus={beginEdit}
                onBlur={commitEdit}
                onSelect={() => { seek(refineMode && rightView === "source" ? (c.sourceStart ?? c.start) : c.start); if (refineMode) setCropIdx(i) }}
                refineMode={refineMode}
                cropTarget={refineMode && i === cropIdx}
                speakersMap={speakersMap}
                onSpeakerChange={(s) => setCues(prev => prev.map((c, idx) => idx === i ? { ...c, speaker: s } : c))}
                onOpenSpeakerModal={() => setSpeakerModalOpen(true)}
                onAddSpeaker={() => {
                  const maxIdx = Math.max(0, ...cues.map(c => parseInt((c.speaker||"").replace("Speaker ", "") || 0)))
                  const ns = `Speaker ${maxIdx + 1}`
                  // Default a new speaker to a voice of the current language.
                  setSpeakersMap(prev => ({ ...prev, [ns]: prev[ns] || voiceForLang(voices, selectedLang) }))
                  setCues(prev => prev.map((c, idx) => idx === i ? { ...c, speaker: ns } : c))
                }}
                onDeleteSpeaker={(s) => setMergeModalTarget(s)}
                onRemovePanel={() => removePanel(i)}
                onEditPanel={refineMode && pdfPath ? () => setEditPanelIdx(i) : null}
                onOriginalChange={(t) => updateOriginal(i, t)}
                stale={refineMode && selectedLang !== sourceLang && (c.staleLangs || []).includes(selectedLang)}
                sourceLang={sourceLang}
                noCps={refineMode && selectedLang === sourceLang}
                onDelete={() => deleteCue(i)}
              />
              {/* Between-cue controls: insert a new cue, or merge with the next. */}
              <CueDivider onInsert={() => insertCueAfter(i)}
                onMerge={i < cues.length - 1 ? () => mergeAdjacent(i) : null} />
              </div>
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
                  {(recapMode ? ["panels", "preview"] : ["source", "panels", "preview"]).map((v) => (
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
                </>
              )}
            </div>
          </div>

          {/* Panel cropper (Video Refine) — always rendered but hidden to preserve state */}
          {refineMode && (
            <div style={{ display: rightView === "panels" ? "flex" : "none", flex: 1, minHeight: 0 }}>
              <PdfReader pdfUrl={pdfPath ? mediaSrc(pdfPath) : ""} cropping={cropping} activeCue={cropIdx + 1}
                onOpen={openPdf} onAttach={attachPanel}
                multi={recapMode} narrating={narrating}
                boxesValue={recapMode ? recapBoxes : null}
                onBoxesChange={recapMode ? setRecapBoxes : null}
                onBeforeChange={recapMode ? pushUndo : null}
                onNarrate={(crops, status) => {
                  if (status === "loading") { notify({ severity: "error", message: "The PDF is still loading — wait a second, then hit Narrate again." }); return }
                  if (status === "empty") { notify({ severity: "info", message: "Draw at least one crop box first." }); return }
                  if (!crops || !crops.length) { notify({ severity: "error", message: "Couldn't read any crops from the page — re-crop and try again." }); return }
                  setPendingCrops(crops)
                }} />
            </div>
          )}

          <div style={{ flex: 1, minHeight: 0, display: refineMode && rightView === "panels" ? "none" : "flex", flexDirection: "column", padding: 16, gap: 12, overflowY: "auto" }}>
            <div style={{ position: "relative", borderRadius: radius.md, overflow: "hidden", background: "#000", display: "flex", alignItems: "center", justifyContent: "center", minHeight: 200 }}>
              {videoSrc || rightView === "preview" ? (
                <>
                  {videoSrc && <video
                    ref={videoRef}
                    src={videoSrc}
                    muted={sourceVol === 0 || (refineMode && rightView !== "source")}
                    onLoadedMetadata={(e) => setDuration(e.target.duration || 0)}
                    onTimeUpdate={onTimeUpdate}
                    onSeeking={syncDub}
                    onPlay={() => { if (driver === "video") setPlaying(true) }}
                    onPause={() => { if (driver === "video") { setPlaying(false); if (!refineMode) audioRef.current?.pause() } }}
                    onClick={togglePlay}
                    style={{ width: "100%", maxHeight: 360, objectFit: "contain", display: rightView === "preview" ? "none" : "block", cursor: "pointer" }}
                  />}
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
              duration={driver === "audio" ? (audioDuration || totalTime) : (duration || totalTime)}
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
                onLoadedMetadata={(e) => setAudioDuration(e.target.duration || 0)}
                onTimeUpdate={onAudioTime}
                onPlay={() => { if (driver === "audio") setPlaying(true) }}
                onPause={() => { if (driver === "audio") setPlaying(false) }}
                onEnded={() => { if (driver === "audio") setPlaying(false) }}
                onError={() => notify({ severity: "error", message: "Dub audio failed to load — try re-running AI Dubbing." })}
                style={{ display: "none" }}
              />
            )}
          </div>
        </div>
      </div>

      {/* Bottom: timeline — reflects the DUB timeline, so in Video Refine it only
          shows on Preview (the tab that plays the dub). On Source you scrub the
          video with its native transport; on Panels there's nothing to scrub. */}
      {(!refineMode || rightView === "preview") && (
        <>
          <Splitter axis="y" onPointerDown={onRowDown} />
          <Timeline
            ref={timelineScrollRef}
            // A cue's start/end is the DUB timeline. A language with no dub has no
            // real timeline — showing the source dub's timing there is misleading,
            // so the strip is cleared until this language is actually dubbed.
            cues={hasDubForSelected ? cues : []}
            noDubLang={hasDubForSelected ? "" : selectedLang}
            dubLang={selectedLang}
            height={timelineH}
            pxPerSec={pxPerSec}
            setPxPerSec={setPxPerSec}
            totalTime={hasDubForSelected ? totalTime : 0}
            currentTime={currentTime}
            activeIdx={activeIdx}
            onSeek={seek}
          />
        </>
      )}
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

// Plays a voice profile's stored reference clip so you can hear it before
// choosing. Toggles play/stop; falls back to a disabled tooltip if the voice
// has no reference audio (the backend returns 404 → onerror).
// Preview a voice by replaying the LAST test sample you generated in the Voices
// section (saved per voice at tts_samples/<voice>.wav). If none exists yet, it
// generates one on the fly (which also saves it for next time). Never plays the
// raw reference clip the voice was cloned from.
function VoicePreviewButton({ voiceName, language, disabled }) {
  const [state, setState] = useState("idle")   // idle | loading | playing
  const audioRef = useRef(null)

  useEffect(() => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset on prop change
    setState("idle")
  }, [voiceName])

  const stableName = (voiceName || "").replace(/[/\\]/g, "_")
  const storedSampleUrl = () => `http://127.0.0.1:8000/files/tts_samples/${encodeURIComponent(stableName)}.wav?t=${Date.now()}`

  const playUrl = (url, onFail) => {
    const a = new Audio(url)
    audioRef.current = a
    a.onended = () => { setState("idle"); audioRef.current = null }
    a.onerror = () => { audioRef.current = null; if (onFail) onFail(); else setState("idle") }
    a.play().then(() => setState("playing")).catch(() => { audioRef.current = null; if (onFail) onFail(); else setState("idle") })
  }

  const generate = async () => {
    setState("loading")
    try {
      let cur = await quickTTS("Hello — this is a quick preview of this voice.", voiceName, language)
      while (cur && cur.status === "running") {
        await new Promise((r) => setTimeout(r, 1000))
        cur = await quickTTSStatus(cur.job_id)
      }
      if (!cur || cur.status === "failed") { setState("idle"); return }
      const b = (cur.synced_audio_url || cur.audio_url || "").split("?")[0]
      if (!b) { setState("idle"); return }
      playUrl(`http://127.0.0.1:8000${b}?v=${Date.now()}`)
    } catch { setState("idle") }
  }

  const toggle = () => {
    if (!voiceName || state === "loading") return
    if (state === "playing" && audioRef.current) { audioRef.current.pause(); audioRef.current = null; setState("idle"); return }
    // Replay the stored test sample; only generate if there isn't one yet.
    playUrl(storedSampleUrl(), generate)
  }

  return (
    <button type="button" onClick={toggle} disabled={disabled || state === "loading"}
      title={state === "loading" ? "Generating a sample…" : state === "playing" ? "Stop preview" : "Preview this voice (plays your Voices test sample)"}
      style={{ width: 38, height: 38, flexShrink: 0, borderRadius: radius.md,
        background: state === "playing" ? colors.accent : colors.panel2, color: state === "playing" ? "#000" : colors.text,
        border: `1px solid ${colors.border}`, cursor: (disabled || state === "loading") ? "default" : "pointer", fontSize: 15 }}>
      {state === "loading"
        ? <span style={{ width: 12, height: 12, border: "2px solid currentColor", borderTopColor: "transparent", borderRadius: "50%", display: "inline-block", animation: "ms-spin 0.7s linear infinite" }} />
        : state === "playing" ? "■" : "▶"}
    </button>
  )
}

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

  // Only voices tagged for the picked language — empty if none (no cross-language
  // profiles, which caused wrong picks like a French voice under Chinese).
  const voiceList = voices.filter((v) => (v.language || "").toLowerCase() === lang.toLowerCase())
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
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
          <select value={effVoice} onChange={(e) => setVoice(e.target.value)}
            style={{ flex: 1, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }}>
            {voiceList.length === 0 && <option value="">No {lang} voice — create one under Voices</option>}
            {voiceList.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
          <VoicePreviewButton voiceName={effVoice} language={voices.find((v) => v.name === effVoice)?.language || lang} disabled={!effVoice} />
        </div>
        <p style={{ color: colors.muted, fontSize: fonts.xs, marginBottom: 8 }}>
          {voiceList.length === 0
            ? `No voice tagged for ${lang} — you can add the language now and pick a voice later on each cue's 🎙 chip.`
            : "Optional — you can also set/change the voice later on each cue's 🎙 chip."}
        </p>

        <Button variant="primary" disabled={!lang} onClick={() => onAdd(lang, effVoice)}
          style={{ width: "100%", padding: 12, borderRadius: radius.md, fontWeight: fonts.bold, marginTop: 8 }}>
          Add {lang}
        </Button>
      </div>
    </div>
  )
}

function StoryMemoryModal({ memory, onClose, onSaveCharacter, onDeleteCharacter, onUndo, onRedo }) {
  const [characters, setCharacters] = useState(() => (memory?.characters || []).map((character) => ({ ...character })))
  const [saving, setSaving] = useState("")
  const [busy, setBusy] = useState(false)
  useEffect(() => setCharacters((memory?.characters || []).map((character) => ({ ...character }))), [memory])
  const update = (id, field, value) => setCharacters((current) => current.map((character) =>
    character.stable_id === id ? { ...character, [field]: value } : character))
  const save = async (character) => {
    setSaving(character.stable_id)
    try {
      const next = await onSaveCharacter(character.stable_id, {
        canonical_name: character.canonical_name,
        role_label: character.role_label,
        aliases: (character.aliases || []).join(";").split(";").map((value) => value.trim()).filter(Boolean),
        appearance: character.appearance,
        status: character.status,
      })
      setCharacters((next?.characters || []).map((item) => ({ ...item })))
    } finally { setSaving("") }
  }
  const applyHistory = async (fn) => {
    setBusy(true)
    try { setCharacters(((await fn())?.characters || []).map((item) => ({ ...item }))) }
    finally { setBusy(false) }
  }
  const remove = async (character) => {
    setBusy(true)
    try { setCharacters(((await onDeleteCharacter(character.stable_id))?.characters || []).map((item) => ({ ...item }))) }
    finally { setBusy(false) }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1001, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(event) => event.stopPropagation()} style={{ width: "92%", maxWidth: 900, height: "85vh", background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: `1px solid ${colors.border}`, display: "flex", justifyContent: "space-between", gap: 16 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ color: colors.text, fontSize: fonts.xl, fontWeight: fonts.bold }}>Story Memory</h2>
            <p style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 2, maxWidth: 650, overflowWrap: "anywhere" }}>Canonical characters and narrated events are saved with source-panel evidence. Corrections here are used in the next chapter.</p>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Button variant="secondary" disabled={busy || !memory?.history?.can_undo} onClick={() => applyHistory(onUndo)} style={{ padding: "5px 9px" }}>↶ Undo</Button>
            <Button variant="secondary" disabled={busy || !memory?.history?.can_redo} onClick={() => applyHistory(onRedo)} style={{ padding: "5px 9px" }}>↷ Redo</Button>
            <button onClick={onClose} style={{ color: colors.muted, fontSize: 22, background: "none", border: "none", cursor: "pointer" }}>✕</button>
          </div>
        </div>
        <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(280px, 0.8fr)" }}>
          <div style={{ overflowY: "auto", padding: 18, borderRight: `1px solid ${colors.border}` }}>
            <div style={{ color: colors.textDim, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.08em", marginBottom: 10 }}>CHARACTERS</div>
            {!characters.length && <div style={{ color: colors.muted, padding: 20 }}>No resolved characters yet. Generate a recap first.</div>}
            {characters.map((character) => (
              <div key={character.stable_id} style={{ background: colors.panel2, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: 12, marginBottom: 10 }}>
                <div style={{ color: colors.muted, fontSize: "10px", fontFamily: fonts.mono, marginBottom: 8 }}>{character.stable_id} · last verified panel {character.last_seen_panel ?? "—"}</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <input value={character.canonical_name || ""} onChange={(event) => update(character.stable_id, "canonical_name", event.target.value)} placeholder="Canonical name"
                    style={memoryInput} />
                  <input value={character.role_label || ""} onChange={(event) => update(character.stable_id, "role_label", event.target.value)} placeholder="Role label if unnamed"
                    style={memoryInput} />
                </div>
                <input value={(character.aliases || []).join("; ")} onChange={(event) => update(character.stable_id, "aliases", event.target.value.split(";").map((value) => value.trim()).filter(Boolean))} placeholder="Aliases, separated by ;"
                  style={{ ...memoryInput, marginTop: 8 }} />
                <input value={character.appearance || ""} onChange={(event) => update(character.stable_id, "appearance", event.target.value)} placeholder="Verified appearance"
                  style={{ ...memoryInput, marginTop: 8 }} />
                <input value={character.status || ""} onChange={(event) => update(character.stable_id, "status", event.target.value)} placeholder="Current status"
                  style={{ ...memoryInput, marginTop: 8 }} />
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 9 }}>
                  <span style={{ color: colors.muted, fontSize: fonts.xs }}>Identity confidence: {Math.round((character.confidence || 0) * 100)}%</span>
                  <div style={{ display: "flex", gap: 7 }}>
                    <Button variant="secondary" disabled={busy || saving === character.stable_id} onClick={() => save(character)} style={{ padding: "5px 10px" }}>
                      {saving === character.stable_id ? "Saving…" : "Save"}
                    </Button>
                    <Button variant="danger" disabled={busy} onClick={() => remove(character)} style={{ padding: "5px 10px" }}>Delete</Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ overflowY: "auto", padding: 18 }}>
            <div style={{ color: colors.textDim, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.08em", marginBottom: 10 }}>RECENT EVIDENCE-BACKED EVENTS</div>
            {(memory?.events || []).map((event) => (
              <div key={event.id} style={{ borderLeft: `2px solid ${colors.info}`, paddingLeft: 10, marginBottom: 13 }}>
                <div style={{ color: colors.muted, fontSize: fonts.xs }}>Panel {event.panel_from ?? "?"}</div>
                <div style={{ color: colors.text, fontSize: fonts.sm, lineHeight: 1.45 }}>{event.summary}</div>
              </div>
            ))}
            {!memory?.events?.length && <div style={{ color: colors.muted, fontSize: fonts.sm }}>Events will appear after narration.</div>}
          </div>
        </div>
      </div>
    </div>
  )
}

const memoryInput = { width: "100%", boxSizing: "border-box", background: colors.panel, border: `1px solid ${colors.border}`, color: colors.text, padding: "7px 9px", borderRadius: radius.sm, fontSize: fonts.sm }

/* ─────────────────────────────────────────────────────────────────────────────
   Between-cue divider — hover to insert a new cue or merge with the next one
───────────────────────────────────────────────────────────────────────────── */

const IconMerge = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M7 4l5 5 5-5M7 20l5-5 5 5" />
  </svg>
)

function CueDivider({ onInsert, onMerge }) {
  const [hover, setHover] = useState(false)
  return (
    <div onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ position: "relative", height: 14 }}>
      <div style={{ position: "absolute", left: 8, right: 8, top: "50%", height: 1, background: colors.accent, opacity: hover ? 0.35 : 0, transition: "opacity .12s" }} />
      {hover && (
        <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%,-50%)", display: "flex", gap: 6, zIndex: 6 }}>
          <button onClick={onInsert} title="Insert a new cue here"
            style={{ width: 22, height: 22, borderRadius: "50%", background: "#4ea1ff", color: "#fff", border: "none", fontSize: 16, lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 1px 4px rgba(0,0,0,0.4)" }}>+</button>
          {onMerge && (
            <button onClick={onMerge} title="Merge these two cues"
              style={{ width: 22, height: 22, borderRadius: "50%", background: colors.panel2, color: colors.textDim, border: `1px solid ${colors.border}`, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 1px 4px rgba(0,0,0,0.4)" }}>
              <IconMerge />
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   Cue row
───────────────────────────────────────────────────────────────────────────── */

/* ─────────────────────────────────────────────────────────────────────────────
   Per-cue panel editor — a cue's thumbnail opens ITS OWN crop, in isolation.
   Shows the source PDF with ONLY this cue's stored crop box seeded (no other
   cue's boxes are loaded). Move / resize / redraw / reset / remove, then Save
   re-crops and updates ONLY this cue — its text, other cues, and their panels
   are untouched. Save is one atomic undo step (handled by the parent).
───────────────────────────────────────────────────────────────────────────── */

function PanelEditorModal({ idx, cue, pdfUrl, onOpenPdf, onSave, onRemove, onClose }) {
  const [busy, setBusy] = useState(false)
  // Bump to remount the reader → re-seeds from the cue's saved box ("Reset").
  const [resetKey, setResetKey] = useState(0)
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 210, background: "rgba(0,0,0,0.72)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ width: "min(1100px, 94vw)", height: "88vh", background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: `1px solid ${colors.border}`, flexShrink: 0 }}>
          {cue.image && (
            <img src={`${FILES_ORIGIN}${cue.image}`} alt="" style={{ width: 34, height: 44, objectFit: "cover", borderRadius: 4, border: `1px solid ${colors.border}` }} />
          )}
          <div style={{ minWidth: 0 }}>
            <div style={{ color: colors.text, fontWeight: fonts.bold }}>Cue {idx + 1} — edit panel</div>
            <div style={{ color: colors.muted, fontSize: fonts.xs, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 520 }}>
              {(cue.text || cue.translated || "").slice(0, 120) || "This cue's own crop — other cues are untouched."}
            </div>
          </div>
          <div style={{ flex: 1 }} />
          {cue.panelBox && (
            <Button variant="secondary" size="sm" onClick={() => setResetKey((k) => k + 1)} title="Restore the saved crop box">↺ Reset crop</Button>
          )}
          {cue.image && (
            <Button variant="danger" size="sm" onClick={onRemove} title="Delete this cue's panel image">🗑 Remove panel</Button>
          )}
          <button onClick={onClose} style={{ color: colors.muted, fontSize: 20, background: "none", border: "none", cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
          <PdfReader key={resetKey} pdfUrl={pdfUrl} cropping={busy}
            onOpen={onOpenPdf}
            initialBox={cue.panelBox || null}
            attachLabel="💾 Save panel"
            onAttach={async (data, box) => {
              setBusy(true)
              try { await onSave(data, box); return true }
              catch { return false }
              finally { setBusy(false) }
            }} />
        </div>
      </div>
    </div>
  )
}

function CueRow({ index, cue, active, selected = false, onToggleSelect = () => {}, refining, dirty, redubbing, hideOriginal, onPlay, onRefine, onRedub, onChange, onFocus, onBlur, onSelect, refineMode = false, cropTarget = false, onRemovePanel, onEditPanel = null, defaultVoice = "", speakersMap = {}, onSpeakerChange = () => {}, onOpenSpeakerModal = () => {}, onAddSpeaker = () => {}, onDeleteSpeaker = () => {}, onOriginalChange = () => {}, stale = false, sourceLang = "English", noCps = false, onDelete = null }) {
  const cps = cue.cps ?? 0
  const rushed = cue.rushed ?? cps > CPS_MAX
  const [hovered, setHovered] = useState(false)
  const body = (
    <div data-cue={index} onClick={onSelect} style={{ display: "flex", flexDirection: "column", gap: 8, cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: fonts.sm }}>
        <input type="checkbox" checked={selected}
          onClick={(e) => e.stopPropagation()}
          onChange={onToggleSelect}
          title="Select this cue for a batch action"
          style={{ accentColor: colors.warning, cursor: "pointer", width: 14, height: 14 }} />
        <span style={{ fontWeight: fonts.bold, fontSize: fonts.md, color: active ? colors.accent : colors.text }}>{String(index + 1).padStart(2, "0")}</span>
        {/* The full multi-speaker control: hover/click to change this cue's
            speaker, assign a voice ("Change Speaker Voice"), add or delete
            speakers. Used in every mode (recap included). */}
        <SpeakerBadge
          speaker={cue.speaker || "Speaker 0"}
          speakersMap={speakersMap}
          defaultVoice={defaultVoice}
          onChange={onSpeakerChange}
          onOpenModal={onOpenSpeakerModal}
          onAdd={onAddSpeaker}
          onDelete={onDeleteSpeaker}
        />
        {/* No CPS on the ORIGINAL language — its timeline derives FROM the audio
            (repack), so there is no slot to fit and nothing to be "rushed" against. */}
        {!noCps && <span style={{
          background: rushed ? "rgba(248,113,113,0.15)" : colors.panel2,
          color: rushed ? colors.error : colors.warning,
          border: `1px solid ${rushed ? colors.error : colors.border}`,
          padding: "2px 7px", borderRadius: radius.full, fontSize: 10, fontWeight: fonts.bold,
        }}>{cps.toFixed(1)} CPS</span>}
        {stale && (
          <span title={`The ${sourceLang} text changed after this translation was made — re-translate this cue.`}
            style={{ background: "rgba(251,191,36,0.15)", color: colors.warning, border: "1px solid rgba(251,191,36,0.5)",
              padding: "2px 7px", borderRadius: radius.full, fontSize: 10, fontWeight: fonts.bold }}>
            ⚠ {sourceLang} changed
          </span>
        )}
        <span style={{ color: colors.textDim, background: colors.panel2, border: `1px solid ${colors.border}`, padding: "2px 8px", borderRadius: radius.full, fontVariantNumeric: "tabular-nums" }}>
          {formatTime(cue.start)} - {formatTime(cue.end)}
        </span>
        {!noCps && (rushed || refining) && <button
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
        </button>}
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
        {onDelete && hovered && (
          <button onClick={(e) => { e.stopPropagation(); onDelete() }} title="Delete cue"
            className="ms-cue-del"
            style={{ width: 22, height: 22, borderRadius: "50%", background: colors.error, color: "#fff", border: "none", fontSize: 12, lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>✕</button>
        )}
        <IconBtn title="Play cue" onClick={onPlay}><IconHeadphones small /></IconBtn>
      </div>

      <div style={{
        border: `1px solid ${selected ? colors.warning : active ? colors.accent : colors.border}`,
        borderRadius: radius.md, overflow: "hidden",
        background: selected ? "rgba(251,191,36,0.06)" : "transparent",
        boxShadow: active ? `0 0 0 1px ${colors.accent}` : "none",
      }}>
        {!hideOriginal && (
          refineMode ? (
            // Editable ORIGINAL (refine mode): editing it flags every translated
            // language stale for this cue — see updateOriginal in the editor.
            <textarea
              value={cue.text || ""}
              onChange={(e) => onOriginalChange(e.target.value)}
              onFocus={onFocus}
              onBlur={onBlur}
              onClick={(e) => e.stopPropagation()}
              rows={1}
              title={`Original (${sourceLang}) — editable`}
              style={{ width: "100%", background: "transparent", border: "none", borderBottom: `1px solid ${colors.border}`, color: colors.textDim, padding: "10px 12px", fontSize: fonts.base, resize: "vertical", minHeight: 40, display: "block", fontFamily: fonts.ui }}
            />
          ) : (
            <div style={{ padding: "10px 12px", color: colors.textDim, fontSize: fonts.base, borderBottom: `1px solid ${colors.border}` }}>
              {cue.text}
            </div>
          )
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

  // Video Refine: a panel thumbnail to the left. Click it to edit THIS cue's own
  // panel crop in isolation (falls back to crop-target select); ✕ deletes it.
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "stretch" }}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <button onClick={onEditPanel || onSelect}
        title={onEditPanel ? "Edit this cue's panel crop" : cropTarget ? "Crop target" : "Make this the crop target"}
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

const Timeline = forwardRef(function Timeline({ cues, height, pxPerSec, setPxPerSec, totalTime, currentTime, activeIdx, onSeek, noDubLang = "", dubLang = "" }, scrollRef) {
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

      {noDubLang ? (
        <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center", color: colors.muted, fontSize: fonts.sm, textAlign: "center", padding: 12 }}>
          No {noDubLang} dub yet — the timeline appears once you generate the {noDubLang} dub.
        </div>
      ) : (

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

          {/* Cue blocks. Each block = its slot (start→end). Inside it, the SPEECH
              (actual audio) is a solid bar; the remaining SILENCE up to the next
              cue is a faded, hatched region — so a short translated line visibly
              shows the gap of silence before the next cue starts. */}
          <div style={{ position: "absolute", top: 34, bottom: 12, left: 0, right: 0 }}>
            {cues.map((c, i) => {
              const slot = Math.max(0, c.end - c.start)
              const nextStart = cues[i + 1] ? cues[i + 1].start : c.end
              // Real audio length for the active language, capped to the slot.
              const rawDur = c.audioDurs?.[dubLang]
              const speechDur = rawDur != null ? Math.min(Math.max(0, rawDur), nextStart - c.start) : slot
              const silenceDur = Math.max(0, slot - speechDur)
              const active = i === activeIdx
              return (
                <div key={i} title={c.translated}
                  style={{
                    position: "absolute", left: c.start * pxPerSec, width: Math.max(8, slot * pxPerSec), top: 0, bottom: 0,
                    borderRadius: radius.sm, overflow: "hidden", fontSize: 11,
                    display: "flex",
                  }}>
                  {/* speech */}
                  <div style={{ width: `${slot > 0 ? (speechDur / slot) * 100 : 100}%`, minWidth: 0, position: "relative",
                    background: active ? "#2563eb" : colors.timelineCue,
                    border: active ? `1px solid ${colors.accent}` : "1px solid rgba(255,255,255,0.08)",
                    borderRadius: silenceDur > 0.02 ? `${radius.sm}px 0 0 ${radius.sm}px` : radius.sm,
                    padding: "6px 8px", color: "#fff", overflow: "hidden", boxShadow: "0 2px 4px rgba(0,0,0,0.4)" }}>
                    <div style={{ fontWeight: fonts.bold, marginBottom: 2 }}>{i + 1}</div>
                    <div style={{ whiteSpace: "nowrap", textOverflow: "ellipsis", overflow: "hidden", opacity: 0.9 }}>{c.translated}</div>
                  </div>
                  {/* silence gap (only when audio is shorter than the slot) */}
                  {silenceDur > 0.02 && (
                    <div title={`${silenceDur.toFixed(1)}s silence`} style={{ flex: 1, minWidth: 0,
                      borderTop: "1px dashed rgba(255,255,255,0.14)", borderBottom: "1px dashed rgba(255,255,255,0.14)", borderRight: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: `0 ${radius.sm}px ${radius.sm}px 0`,
                      background: "repeating-linear-gradient(45deg, rgba(255,255,255,0.03) 0 6px, transparent 6px 12px)" }} />
                  )}
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
      )}
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
  // End the timeline exactly at the last cue (a hair of margin for the playhead),
  // not +2s of empty track past the audio.
  return cues.reduce((max, c) => Math.max(max, c.end), 0) + 0.2
}

// Mirrors scripts/speech/cps.py: characters (trimmed) per second.
const CPS_MAX = 24.0   // non-CJK "rushed" threshold — keep in sync with config.CPS_MAX
/** Effective speaking window for cue i. The dub engine lets audio run until the
 *  NEXT cue starts (minus a breath), so dead air after a short slot is usable
 *  time. Judging CPS against the raw end−start was a phantom constraint — lines
 *  looked "stuck" at 26+ CPS when their audio actually fit fine. */
// Mirrors speech/cps.py::effective_duration. These two MUST agree: when they
// drifted, a cue's stored `cps` depended on whether Python or the UI last wrote
// it, and sessions with identical timings ended up with different numbers.
// MIN_GAP matches config.DUB_SPEECH_MIN_GAP.
const MIN_GAP = 0.24

function effDur(cues, i) {
  const c = cues?.[i]
  if (!c) return 0
  const dur = Math.max(0, (c.end ?? 0) - (c.start ?? 0))
  const nxt = cues[i + 1]
  if (!nxt) return dur
  return Math.max(dur, (nxt.start ?? c.end ?? 0) - (c.start ?? 0) - MIN_GAP)
}

function computeCps(text, durationSec) {
  const n = (text || "").trim().length
  return durationSec > 0 ? Math.round((n / durationSec) * 10) / 10 : 0
}

// The default voice for a language: a profile tagged for that language, else an
// English profile (a sensible default when e.g. Chinese has none), else ANY
// voice. "" only when there are no voice profiles at all.
function voiceForLang(voices, lang) {
  const byLang = (want) => (voices || []).find((v) => (v.language || "").toLowerCase() === want)
  const l = (lang || "").toLowerCase()
  return (byLang(l) || byLang("english") || (voices || [])[0])?.name || ""
}

/* ─────────────────────────────────────────────────────────────────────────────
   Multi-Speaker UI Components
───────────────────────────────────────────────────────────────────────────── */

function SpeakerBadge({ speaker, speakersMap, defaultVoice = "", onChange, onOpenModal, onAdd, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  // Show the VOICE PROFILE the cue will actually speak with: the speaker's
  // assigned voice, else the language default. Falls back to the speaker label
  // only when there are no voices at all.
  const assignedVoice = speakersMap[speaker]
  const displayName = assignedVoice || defaultVoice || speaker

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
                  {speakersMap[s] || (s === speaker ? defaultVoice : "") || s}
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

function SpeakerModal({ speakersMap, setSpeakersMap, voices, onClose, onAddSpeaker, defaultVoice = "" }) {
  // Speaker 0 is the DEFAULT speaker every cue starts as — always show it first
  // so its voice can be reassigned (e.g. off the language default onto a specific
  // profile). Then the numbered speakers, in order.
  const others = Object.keys(speakersMap).filter(s => s !== "Speaker 0")
  others.sort((a, b) => parseInt(a.replace("Speaker ", "") || 0) - parseInt(b.replace("Speaker ", "") || 0))
  const allSpeakers = ["Speaker 0", ...others]

  const [activeTab, setActiveTab] = useState("Speaker 0")
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
              <div style={{ fontSize: fonts.base, color: colors.text }}>
                Assign a voice to <strong style={{ color: colors.accent }}>{activeTab}</strong>
                {activeTab === "Speaker 0" && !speakersMap["Speaker 0"] && (
                  <span style={{ color: colors.textDim, fontSize: fonts.sm }}> — currently the language default ({defaultVoice || "none"})</span>
                )}
              </div>
              <input type="text" placeholder="Search for a speaker..." value={search} onChange={e => setSearch(e.target.value)}
                style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, padding: "8px 12px", borderRadius: radius.md, width: 250, fontSize: fonts.sm }} />
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
                {filteredVoices.map(v => {
                  const currentVoice = speakersMap[activeTab] || (activeTab === "Speaker 0" ? defaultVoice : "")
                  const isActive = currentVoice === v.name
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
