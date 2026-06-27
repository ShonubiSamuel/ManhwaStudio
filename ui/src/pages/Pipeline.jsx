/**
 * ui/src/pages/Pipeline.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Unified Pipeline workspace.
 *
 *   Left rail   — resizable / collapsible / hideable list of stages. Each stage
 *                 shows status + its own Run button and selects a detail view.
 *   Right side  — the selected stage's detail view only. No log tab/console:
 *                 progress shows inline, and outcomes/errors surface as toast
 *                 notifications (the Logs section is the optional deep view).
 *   Run model   — ONE model, no Auto/Manual split: "Run all" chains the whole
 *                 pipeline but pauses at the narration review (Refine/Narrate);
 *                 then the same button becomes "Continue" to finish. Each stage
 *                 can also be run on its own from the rail.
 *
 * Stage detail views live in ./stages/*. Detect and Refine are built; the rest
 * use StageStub until built out one at a time.
 */

import { useState, useEffect, useRef, useCallback } from "react"
import { useApp, actions } from "../store/app"
import { useNotify } from "../store/notify"
import {
  runStage, stopStage, getEpisodeStatus, isRunning, connectEvents,
  runAuto, resume, getPlan,
} from "../api/pipeline"
import { colors, fonts, radius, status as stageStatus } from "../theme"
import Button      from "../components/Button"
import ProgressBar from "../components/ProgressBar"
import LogPanel    from "../components/LogPanel"
import DetectDetail    from "./stages/DetectDetail"
import RefineDetail    from "./stages/RefineDetail"
import TranslateDetail from "./stages/TranslateDetail"
import DubDetail       from "./stages/DubDetail"
import SyncDetail      from "./stages/SyncDetail"
import StageStub       from "./stages/StageStub"

// ── Stage definitions per source type (runnable keys → label/hint/icon) ───────
const STAGE_FLOWS = {
  video: [
    { key: "detect",       label: "Detect",     icon: "✂", hint: "Transcribe audio, capture panel screenshots" },
    { key: "video_refine", label: "Refine",     icon: "✦", hint: "AI cleanup of transcript into narration" },
    { key: "translate",    label: "Translate",  icon: "文", hint: "Translate narration into target languages" },
    { key: "dub",          label: "Dub",        icon: "♪", hint: "Batch TTS — dubbed audio per language" },
    { key: "sync",         label: "Sync",       icon: "↻", hint: "Align and time-stretch to English timing" },
    { key: "assemble",     label: "Assemble",   icon: "▣", hint: "Combine video + audio into final output" },
  ],
  pdf: [
    { key: "pdf_slice",   label: "Slice",     icon: "▤", hint: "Slice PDF pages into panels, downscale" },
    { key: "pdf_narrate", label: "Narrate",   icon: "✦", hint: "AI vision narration per panel" },
    { key: "translate",   label: "Translate", icon: "文", hint: "Translate narration into target languages" },
    { key: "dub",         label: "Dub",       icon: "♪", hint: "Batch TTS — dubbed audio per language" },
    { key: "assemble",    label: "Assemble",  icon: "▣", hint: "Combine panels + audio into final output" },
  ],
  screenshots: [
    { key: "upscale",     label: "Upscale",   icon: "⤢", hint: "Real-ESRGAN upscale of imported panels" },
    { key: "pdf_narrate", label: "Narrate",   icon: "✦", hint: "AI vision narration per panel" },
    { key: "translate",   label: "Translate", icon: "文", hint: "Translate narration into target languages" },
    { key: "dub",         label: "Dub",       icon: "♪", hint: "Batch TTS — dubbed audio per language" },
    { key: "assemble",    label: "Assemble",  icon: "▣", hint: "Combine panels + audio into final output" },
  ],
}

const NARRATION_KEYS = new Set(["video_refine", "pdf_narrate"])

// Stage key → episodes-table status column (mirrors pipeline_logic.STAGE_DB_COLUMN).
// Most match, but a few runnable keys share a column — e.g. "video_refine" and
// "pdf_slice" both record status under "extract". Without this, Refine looks up
// stages["video_refine"] (which doesn't exist) and shows a stale grey dot.
const STAGE_COL = {
  detect: "detect", video_refine: "extract", pdf_slice: "extract",
  pdf_narrate: "narrate", upscale: "upscale", translate: "translate",
  dub: "dub", sync: "sync", assemble: "assemble",
}
const MINI_BELOW = 132

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }


export default function Pipeline() {
  const { state, dispatch } = useApp()
  const { notify } = useNotify()
  const episode = state.activeEpisode
  const stages  = STAGE_FLOWS[episode?.source_type] || STAGE_FLOWS.video

  // ── Layout state (persisted) ───────────────────────────────────────────────
  const [railWidth, setRailWidth] = useState(() => Number(lsGet("ms_rail_w", 220)))
  const [railHidden, setRailHidden] = useState(() => lsGet("ms_rail_hidden", "0") === "1")
  const dragging = useRef(false)
  const mini = railWidth < MINI_BELOW

  // ── Run / view state ────────────────────────────────────────────────────────
  // Restore the last stage viewed for this episode (so returning to Pipeline
  // doesn't snap back to the first stage).
  const initialStage = () => {
    const saved = lsGet(`ms_stage_${episode?.id}`, "")
    return stages.find(s => s.key === saved)?.key || stages[0]?.key
  }
  const [selectedKey, setSelectedKey] = useState(initialStage)
  const selectStage = useCallback((key) => {
    setSelectedKey(key)
    if (episode) lsSet(`ms_stage_${episode.id}`, key)
  }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps
  const [activeStage, setActiveStage] = useState(null)   // manual single run
  const [chainRunning, setChainRunning] = useState(false)
  const [atCheckpoint, setAtCheckpoint] = useState(false)
  const [stagePct, setStagePct] = useState(0)
  const [stageMsg, setStageMsg] = useState("")
  const [stopping, setStopping] = useState(false)
  const [freshEpisode, setFreshEpisode] = useState(episode)
  const [plan, setPlan] = useState(null)
  const [signal, setSignal] = useState(0)   // bump to refresh detail views

  // ── Live developer console ──────────────────────────────────────────────────
  // Every log line the engines stream over SSE (model loading, batch counts,
  // per-language progress, errors) is captured here and shown in a collapsible
  // console — the "see everything that's happening" view the toasts can't give.
  const [logLines, setLogLines] = useState([])
  const [consoleOpen, setConsoleOpen] = useState(() => lsGet("ms_console_open", "1") === "1")
  const lineIdRef = useRef(0)
  const LOG_CAP = 1000
  const pushLog = useCallback((message, level = "info") => {
    setLogLines(prev => {
      const next = [...prev, { id: ++lineIdRef.current, message, level }]
      return next.length > LOG_CAP ? next.slice(next.length - LOG_CAP) : next
    })
  }, [])
  const toggleConsole = () => { const v = !consoleOpen; setConsoleOpen(v); lsSet("ms_console_open", v ? "1" : "0") }

  const esRef = useRef(null)
  const lastErrorRef = useRef("")   // most recent error log line, for the failure toast
  const anyRunning = activeStage !== null || chainRunning

  const refreshEpisode = useCallback(async () => {
    try {
      const fresh = await getEpisodeStatus(episode.id)
      setFreshEpisode(fresh); dispatch(actions.updateEpisode(fresh))
    } catch { /* non-fatal */ }
  }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps

  const refreshPlan = useCallback(async () => {
    try { setPlan(await getPlan(episode.id)) } catch { /* best effort */ }
  }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps

  // Called by detail views after an edit/regenerate so the whole screen reflects
  // changes live (rail dots, plan, and the detail itself) — no manual refresh.
  const handleDataChanged = useCallback(() => {
    refreshEpisode(); refreshPlan(); setSignal(s => s + 1)
  }, [refreshEpisode, refreshPlan])

  // Reset on episode change.
  useEffect(() => {
    setFreshEpisode(episode)
    setSelectedKey(initialStage())
    setActiveStage(null); setChainRunning(false); setAtCheckpoint(false)
    setStagePct(0); setStageMsg(""); setStopping(false)
    setPlan(null); setSignal(s => s + 1)
    esRef.current?.close()
    if (episode) refreshPlan()
  }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps

  // Reattach to an in-progress run on mount.
  useEffect(() => {
    if (!episode) return
    let cancelled = false
    isRunning(episode.id).then(res => {
      if (!cancelled && res.running) { setChainRunning(true); attachStream(episode.id) }
    }).catch(() => {})
    return () => { cancelled = true }
  }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => esRef.current?.close(), [])

  // Rail drag-resize.
  useEffect(() => {
    const move = (e) => {
      if (!dragging.current) return
      const w = Math.max(56, Math.min(340, e.clientX - 220))  // 220 = sidebar width
      setRailWidth(w)
    }
    const up = () => {
      if (dragging.current) { dragging.current = false; document.body.style.userSelect = ""; lsSet("ms_rail_w", String(railWidth)) }
    }
    window.addEventListener("mousemove", move)
    window.addEventListener("mouseup", up)
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up) }
  }, [railWidth])

  if (!episode) {
    return <Center>No episode selected. Go to Library and open an episode.</Center>
  }

  function attachStream(episodeId) {
    esRef.current?.close()
    lastErrorRef.current = ""
    esRef.current = connectEvents(episodeId, {
      // Feed the live console with every line, and remember the latest error so
      // a failure toast can explain *why* (e.g. "no tone prompt set").
      onLog: (m, level) => { pushLog(m, level); if (level === "error") lastErrorRef.current = m },
      onProgress: (pct, m) => { setStagePct(pct); if (m) setStageMsg(m) },
      onStageAdvance: async () => {
        setStagePct(0); setStageMsg("")
        setSignal(s => s + 1)
        await refreshEpisode(); await refreshPlan()
      },
      onStageDone: async (stage, success, event) => {
        if (event?.chain && event?.checkpoint) {
          notify({ severity: "info", message: "Review checkpoint reached — review the narration, then Continue." })
          setAtCheckpoint(true)
          const narr = stages.find(s => NARRATION_KEYS.has(s.key))
          if (narr) selectStage(narr.key)
        } else if (success) {
          notify({ severity: "success", message: "Run finished." })
        } else {
          notify({
            severity: "error",
            message: lastErrorRef.current || "Run did not complete. Open Logs for details.",
          })
        }
        setActiveStage(null); setChainRunning(false)
        setStagePct(0); setStageMsg(""); setStopping(false)
        setSignal(s => s + 1)
        await refreshEpisode(); await refreshPlan()
      },
      onError: () => {},
    })
  }

  const startRun = async (fn, setBusy, startedMsg) => {
    setBusy(); setStagePct(0); setStageMsg("")
    setLogLines([]); setConsoleOpen(true)   // fresh console, opened for the run
    if (startedMsg) pushLog(`$ ${startedMsg}`, "accent")
    try {
      await fn()
      if (startedMsg) notify({ severity: "info", message: startedMsg })
      attachStream(episode.id)
    } catch (err) {
      const m = err.message || "Couldn't start the run."
      pushLog(`✗  ${m}`, "error")
      notify({ severity: "error", message: m })
      setActiveStage(null); setChainRunning(false)
    }
  }

  const handleRunStage = (key) => {
    const label = stages.find(s => s.key === key)?.label || key
    selectStage(key)
    startRun(() => runStage(episode.id, key), () => setActiveStage(key), `${label} started…`)
  }
  // Lets a stage detail view kick off a targeted background run (e.g. regenerate
  // one dub batch) that streams through the same SSE console + refresh flow.
  const handleCustomRun = (fn, startedMsg) =>
    startRun(fn, () => setActiveStage(selectedKey), startedMsg)
  const handleRunAll  = () => startRun(() => runAuto(episode.id), () => { setChainRunning(true); setAtCheckpoint(false) }, "Running to review…")
  const handleResume  = () => startRun(() => resume(episode.id),  () => { setChainRunning(true); setAtCheckpoint(false) }, "Finishing run…")
  const handleStop    = async () => {
    setStopping(true)
    try { await stopStage(episode.id) } catch (err) { notify({ severity: "error", message: err.message || "Couldn't stop the run." }); setStopping(false) }
  }

  const toggleHide = () => { const v = !railHidden; setRailHidden(v); lsSet("ms_rail_hidden", v ? "1" : "0") }

  const stageMap = freshEpisode?.stages || episode.stages || {}
  // Reflect a manual single-stage run as "running" immediately, before the
  // first status refresh comes back — otherwise the badge/dot stays on the old
  // state (e.g. "done") for a beat after Run is clicked.
  const infoFor = (key) => {
    const base = stageMap[STAGE_COL[key] || key] || { status: "pending", progress: 0 }
    return activeStage === key ? { ...base, status: "running" } : base
  }
  const overall  = freshEpisode?.overall ?? episode.overall ?? 0
  const narrationReady = plan?.narration_ready
  const resumeLeft = plan?.resume_remaining?.length ?? 0
  const fullyDone  = narrationReady && resumeLeft === 0

  // Primary "Run all / Continue" button content.
  let runAllBtn
  if (chainRunning) {
    runAllBtn = <Button variant="danger" size="sm" onClick={handleStop} loading={stopping}>Stop</Button>
  } else if (narrationReady && !fullyDone) {
    runAllBtn = <Button variant="primary" size="sm" onClick={handleResume}>Continue ▶</Button>
  } else if (fullyDone) {
    runAllBtn = <Button variant="secondary" size="sm" disabled>Finished</Button>
  } else {
    runAllBtn = <Button variant="primary" size="sm" onClick={handleRunAll} disabled={activeStage !== null}>Run all ▶</Button>
  }

  const selected = stages.find(s => s.key === selectedKey) || stages[0]
  const selInfo  = infoFor(selected.key)

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <div style={{ padding: "14px 20px", borderBottom: `1px solid ${colors.border}`, flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
          <button onClick={() => dispatch(actions.clearEpisode())} title="Back to Library"
            style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: fonts.lg, padding: 0 }}>←</button>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: colors.text, fontSize: fonts.lg, fontWeight: fonts.bold, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{episode.title}</div>
            <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 2 }}>{episode.source_type.toUpperCase()} · {episode.total_panels || 0} panels</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>
          <div style={{ width: 150 }}><ProgressBar pct={overall} label="Overall" /></div>
          {runAllBtn}
        </div>
      </div>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

        {railHidden ? (
          <div style={{ width: 34, borderRight: `1px solid ${colors.border}`, background: colors.panel,
            display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 10 }}>
            <button onClick={toggleHide} title="Show stages"
              style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 18 }}>»</button>
          </div>
        ) : (
          <>
            <div style={{ width: railWidth, minWidth: 56, borderRight: `1px solid ${colors.border}`,
              background: colors.panel, overflow: "auto", flexShrink: 0 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: mini ? "center" : "space-between",
                padding: "9px 10px", borderBottom: `1px solid ${colors.border}` }}>
                {!mini && <span style={{ fontSize: 10.5, letterSpacing: "0.1em", color: colors.muted, fontWeight: fonts.bold }}>STAGES</span>}
                <button onClick={toggleHide} title="Collapse"
                  style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 16 }}>«</button>
              </div>
              {stages.map(s => (
                <StageRow
                  key={s.key} stage={s} mini={mini}
                  info={infoFor(s.key)}
                  selected={s.key === selectedKey}
                  isActive={activeStage === s.key}
                  anyRunning={anyRunning}
                  onSelect={() => selectStage(s.key)}
                  onRun={() => handleRunStage(s.key)}
                  onStop={handleStop}
                  stopping={stopping && activeStage === s.key}
                />
              ))}
            </div>
            <div onMouseDown={() => { dragging.current = true; document.body.style.userSelect = "none" }}
              title="Drag to resize"
              style={{ width: 6, cursor: "col-resize", flexShrink: 0, background: "transparent" }}
              onMouseEnter={e => (e.currentTarget.style.background = colors.accent)}
              onMouseLeave={e => (e.currentTarget.style.background = "transparent")} />
          </>
        )}

        {/* Right: stage detail only (no log tab/console) */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {/* Inline run progress — the live signal that replaces the console. */}
          {anyRunning && (
            <div style={{ padding: "10px 18px 0" }}>
              <ProgressBar pct={stagePct} label={stageMsg || "Running…"} />
            </div>
          )}
          <div style={{ flex: 1, overflow: "auto", padding: "14px 18px" }}>
            {atCheckpoint && NARRATION_KEYS.has(selected.key) && (
              <div style={{ fontSize: fonts.xs, color: colors.warning, background: "rgba(251,191,36,0.08)",
                border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "7px 10px", marginBottom: 12 }}>
                ◆ Run all paused here — review the narration below, then press Continue to finish.
              </div>
            )}
            <StageDetail
              stage={selected} info={selInfo} episode={episode} signal={signal}
              busy={anyRunning} onRun={() => handleRunStage(selected.key)}
              onCustomRun={handleCustomRun}
              onDataChanged={handleDataChanged}
            />
          </div>

          {/* Live developer console — collapsible dock pinned to the bottom */}
          <div style={{ flexShrink: 0, borderTop: `1px solid ${colors.border}`, background: colors.panel }}>
            <button onClick={toggleConsole}
              style={{ width: "100%", display: "flex", alignItems: "center", gap: 8, background: "none",
                border: "none", color: colors.textDim, cursor: "pointer", padding: "7px 18px",
                fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.06em" }}>
              <span style={{ color: anyRunning ? colors.warning : colors.muted }}>{consoleOpen ? "▾" : "▸"}</span>
              CONSOLE
              {anyRunning && <span style={{ width: 7, height: 7, borderRadius: "50%", background: colors.warning }} />}
              <span style={{ color: colors.muted, fontWeight: fonts.normal }}>
                {logLines.length ? `${logLines.length} line${logLines.length === 1 ? "" : "s"}` : "idle"}
              </span>
              <span style={{ flex: 1 }} />
              <span style={{ color: colors.muted, fontWeight: fonts.normal }}>
                {consoleOpen ? "click to hide" : "click to show live output"}
              </span>
            </button>
            {consoleOpen && (
              <div style={{ padding: "0 12px 12px" }}>
                <LogPanel lines={logLines} height={200} onClear={() => setLogLines([])} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}


// ── Detail dispatcher ───────────────────────────────────────────────────────
function StageDetail({ stage, info, episode, signal, busy, onRun, onCustomRun, onDataChanged }) {
  const common = {
    episode, signal, busy, onRun, onCustomRun, onDataChanged,
    status: info.status, progress: info.progress,
  }
  if (stage.key === "detect") return <DetectDetail {...common} />
  if (NARRATION_KEYS.has(stage.key)) return <RefineDetail {...common} />
  if (stage.key === "translate") return <TranslateDetail {...common} />
  if (stage.key === "dub") return <DubDetail {...common} />
  if (stage.key === "sync") return <SyncDetail {...common} />
  return <StageStub label={stage.label} hint={stage.hint} {...common} />
}


// ── Rail row ──────────────────────────────────────────────────────────────────
function StageRow({ stage, info, mini, selected, isActive, anyRunning, onSelect, onRun, onStop, stopping }) {
  const color = stageStatus.color[info.status] || colors.muted
  return (
    <div onClick={onSelect} title={mini ? stage.label : ""}
      style={{
        display: "flex", alignItems: "center", gap: mini ? 0 : 9, justifyContent: mini ? "center" : "flex-start",
        padding: "9px 10px", margin: mini ? "5px 4px" : "5px 6px",
        borderRadius: radius.md, cursor: "pointer",
        background: selected ? "rgba(255,107,53,0.12)" : "transparent",
        border: `1px solid ${selected ? colors.accent : "transparent"}`,
      }}>
      <span style={{ fontSize: 15, width: 18, textAlign: "center", color: selected ? colors.accent : colors.textDim, flexShrink: 0 }}>{stage.icon}</span>
      {!mini && <span style={{ flex: 1, color: colors.text, fontSize: fonts.base, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{stage.label}</span>}
      {!mini && <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0 }} />}
      {!mini && (
        isActive
          ? <button onClick={e => { e.stopPropagation(); onStop() }} title="Stop"
              style={runBtn(colors.error)}>{stopping ? "…" : "■"}</button>
          : <button onClick={e => { e.stopPropagation(); onRun() }} title={`Run ${stage.label}`} disabled={anyRunning}
              style={runBtn(anyRunning ? colors.muted : colors.textDim)}>▶</button>
      )}
    </div>
  )
}

const runBtn = (col) => ({
  background: "none", border: `1px solid ${colors.border}`, color: col,
  borderRadius: 5, width: 24, height: 24, display: "flex", alignItems: "center",
  justifyContent: "center", cursor: "pointer", flexShrink: 0, fontSize: 11,
})

function Center({ children }) {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
      color: colors.muted, fontSize: fonts.base, padding: 40, textAlign: "center" }}>{children}</div>
  )
}
