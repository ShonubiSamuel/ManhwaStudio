/**
 * ui/src/pages/Logs.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * The Logs archive — the permanent historical record of every stage run across
 * all episodes. Reads processing_logs via /api/logs. This is the deep "what
 * happened" view; the Pipeline shows live status inline + via toasts.
 *
 * Opening this page clears the sidebar unread-issues badge.
 */

import { useState, useEffect, useCallback } from "react"
import { getRecentLogs, clearLogs } from "../api/logs"
import { useNotify } from "../store/notify"
import { colors, fonts, radius, status as stStatus, logColors } from "../theme"
import Button from "../components/Button"

const FILTERS = ["all", "failed", "done", "running"]
const KNOWN_STATUS = new Set(["pending", "running", "done", "failed", "skipped", "outdated"])

export default function Logs() {
  const { clearUnread, notify } = useNotify()
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [filter, setFilter] = useState("all")
  const [open, setOpen] = useState(null)   // expanded row id

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError("")
    try { setRows(await getRecentLogs(300)) }
    catch (err) { if (!silent) setError(err.message || "Failed to load logs") }
    finally { if (!silent) setLoading(false) }
  }, [])

  // Load on open + auto-refresh every 4s so new runs/actions appear live.
  useEffect(() => {
    load(); clearUnread()
    const id = setInterval(() => load(true), 4000)
    return () => clearInterval(id)
  }, [load, clearUnread])

  const onClear = async () => {
    try { await clearLogs(); setRows([]); notify({ severity: "success", message: "Logs cleared" }) }
    catch (err) { notify({ severity: "error", message: err.message }) }
  }

  const shown = filter === "all" ? rows : rows.filter(r => r.status === filter)
  const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString() : "—"
  const fmtDur  = (d) => d == null ? "—" : d < 60 ? `${d.toFixed(1)}s` : `${Math.floor(d / 60)}m ${Math.round(d % 60)}s`

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{ padding: "16px 24px", borderBottom: `1px solid ${colors.border}`, flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ color: colors.text, fontSize: fonts.lg, fontWeight: fonts.bold }}>Logs</div>
          <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 2 }}>Every stage run across all episodes · newest first</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <select value={filter} onChange={e => setFilter(e.target.value)}
            style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "6px 9px", fontSize: fonts.sm }}>
            {FILTERS.map(f => <option key={f} value={f}>{f === "all" ? "All statuses" : f}</option>)}
          </select>
          <Button variant="secondary" size="sm" onClick={load}>Reload</Button>
          <Button variant="danger" size="sm" onClick={onClear}>Clear all</Button>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "12px 24px" }}>
        {loading && <Center>Loading…</Center>}
        {error && !loading && <Center><span style={{ color: colors.error }}>{error}</span></Center>}
        {!loading && !error && shown.length === 0 && <Center>No log entries{filter !== "all" ? ` with status “${filter}”` : ""} yet.</Center>}

        {!loading && !error && shown.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: fonts.sm }}>
            <thead>
              <tr>{["When", "Project · Episode", "Stage", "Status", "Duration"].map(h =>
                <th key={h} style={th}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {shown.map(r => {
                const sc = stStatus.color[r.status] || colors.muted
                const expandable = !!((r.log && r.log.length) || (r.error && r.error.trim()))
                return (
                  <FragmentRow key={r.id} r={r} sc={sc} expandable={expandable}
                    open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)}
                    fmtTime={fmtTime} fmtDur={fmtDur} />
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function FragmentRow({ r, sc, expandable, open, onToggle, fmtTime, fmtDur }) {
  return (
    <>
      <tr onClick={expandable ? onToggle : undefined} style={{ cursor: expandable ? "pointer" : "default" }}>
        <td style={td}>{fmtTime(r.started_at)}</td>
        <td style={td}>
          <span style={{ color: colors.muted }}>{r.project_name}</span>
          <span style={{ color: colors.textDim }}> · {r.episode_title || `#${r.episode_id}`}</span>
        </td>
        <td style={td}>{r.stage}</td>
        <td style={td}>
          {KNOWN_STATUS.has(r.status)
            ? <span style={{ color: sc, border: `1px solid ${sc}`, borderRadius: radius.full, padding: "1px 8px", fontSize: fonts.xs }}>{r.status}</span>
            : <span style={{ color: colors.textDim, fontSize: fonts.xs }}>{r.status}</span>}
        </td>
        <td style={td}>{fmtDur(r.duration_secs)}{expandable && <span style={{ color: colors.muted, marginLeft: 8 }}>{open ? "▾" : "▸"}</span>}</td>
      </tr>
      {open && expandable && (
        <tr><td colSpan={5} style={{ ...td, background: colors.panel2, padding: 0 }}>
          <div style={{ maxHeight: 360, overflowY: "auto", padding: "10px 12px", fontFamily: fonts.mono, fontSize: fonts.xs, lineHeight: 1.6 }}>
            {r.log && r.log.length
              ? r.log.map((ln, i) => (
                  <div key={i} style={{ color: logColors[ln.level] || colors.text, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{ln.message}</div>
                ))
              : <div style={{ color: colors.error, whiteSpace: "pre-wrap" }}>{r.error}</div>}
          </div>
        </td></tr>
      )}
    </>
  )
}

function Center({ children }) {
  return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "60%", color: colors.muted, fontSize: fonts.base, textAlign: "center" }}>{children}</div>
}

const th = { textAlign: "left", color: colors.muted, fontWeight: fonts.bold, fontSize: fonts.xs, letterSpacing: "0.05em", padding: "7px 8px", borderBottom: `1px solid ${colors.border}`, position: "sticky", top: 0, background: colors.bg }
const td = { padding: "7px 8px", borderBottom: `1px solid ${colors.border}`, color: colors.textDim, verticalAlign: "top" }
