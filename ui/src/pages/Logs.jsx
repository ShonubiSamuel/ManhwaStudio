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

const FILTERS = ["all", "dub_studio", "failed", "done", "running"]
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

  const shown = filter === "all" ? rows
    : filter === "dub_studio" ? rows.filter(r => r.episode_id === 0 || r.stage.includes("adhoc") || r.stage.includes("dub") || r.stage.includes("refine"))
    : rows.filter(r => r.status === filter)
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
            {FILTERS.map(f => (
              <option key={f} value={f}>
                {f === "all" ? "All entries" : f === "dub_studio" ? "Dub Studio / Adhoc" : f}
              </option>
            ))}
          </select>
          <Button variant="secondary" size="sm" onClick={load}>Reload</Button>
          <Button variant="danger" size="sm" onClick={onClear}>Clear all</Button>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "12px 24px" }}>
        {error && !loading && <Center><span style={{ color: colors.error }}>{error}</span></Center>}
        <div style={{ flex: 1, background: "#0a0a0a", borderRadius: radius.md, border: `1px solid ${colors.border}`, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ flex: 1, overflowY: "auto", padding: 16, fontFamily: fonts.mono, fontSize: fonts.sm, color: "#d4d4d4", lineHeight: 1.6 }}>
          {loading && rows.length === 0 ? <Center>Loading...</Center> : rows.length === 0 ? <Center>No log entries yet.</Center> : (
            rows.map((r, idx) => (
              <div key={r.id || idx} style={{ marginBottom: 24 }}>
                <div style={{ color: "#666", marginBottom: 6, fontSize: fonts.xs }}>
                  <span style={{ color: "#888" }}>[{fmtTime(r.started_at)}]</span>{" "}
                  <span style={{ color: "#569cd6" }}>[{r.stage}]</span>{" "}
                  <span>{r.project_name}{r.episode_id !== 0 ? ` · Ep ${r.episode_id}` : ""}</span>{" "}
                  <span style={{ color: r.status === "failed" ? "#f48771" : r.status === "done" ? "#89d185" : "#cca700" }}>[{r.status.toUpperCase()}]</span>{" "}
                  <span>({fmtDur(r.duration_secs)})</span>
                </div>
                <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {r.log && r.log.length > 0 ? (
                    r.log.map((ln, i) => {
                      const msg = typeof ln === "string" ? ln : (ln.message || JSON.stringify(ln));
                      return <div key={i} style={{ color: ln.level === "error" ? "#f48771" : ln.level === "warning" ? "#cca700" : "inherit" }}>{msg}</div>;
                    })
                  ) : (
                    <div style={{ color: r.status === "failed" ? "#f48771" : "#666" }}>
                      {r.error || "No detailed logs available."}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
        </div>
      </div>
    </div>
  )
}

function Center({ children }) {
  return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: colors.muted, fontSize: fonts.base, textAlign: "center" }}>{children}</div>
}
