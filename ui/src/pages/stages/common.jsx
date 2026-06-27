/**
 * ui/src/pages/stages/common.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Shared building blocks for stage detail views: a panel-fetching hook and a
 * thumbnail component with graceful fallback.
 */

import { useState, useEffect, useCallback, useRef } from "react"
import { getPanels, panelImageSrc, FILES_ORIGIN } from "../../api/panels"
import { colors, fonts, radius } from "../../theme"

/**
 * Fetch all panels for an episode. Refetches when episodeId or `signal` change
 * (Pipeline bumps `signal` after a stage finishes so detail views refresh).
 */
export function useEpisodePanels(episodeId, signal = 0) {
  const [panels,  setPanels]  = useState([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState("")

  const reload = useCallback(async () => {
    if (!episodeId) return
    setLoading(true); setError("")
    try {
      setPanels(await getPanels(episodeId))
    } catch (err) {
      setError(err.message || "Failed to load panels")
    } finally {
      setLoading(false)
    }
  }, [episodeId])

  useEffect(() => { reload() }, [reload, signal])

  return { panels, setPanels, loading, error, reload }
}

/** Panel thumbnail with a "no image" fallback. */
export function PanelThumb({ panel, w = 80, h = 108 }) {
  const [failed, setFailed] = useState(false)
  const src = panelImageSrc(panel)

  const box = {
    width: w, minWidth: w, height: h, flexShrink: 0,
    borderRadius: radius.md, overflow: "hidden",
    background: colors.panel2, border: `1px solid ${colors.border}`,
    display: "flex", alignItems: "center", justifyContent: "center",
    color: colors.muted, fontSize: fonts.xs, textAlign: "center",
  }
  if (!src || failed) return <div style={box}>no image</div>
  return (
    <div style={box}>
      <img
        src={src} alt={`panel ${panel.panel_index + 1}`}
        onError={() => setFailed(true)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </div>
  )
}

// One shared <audio> so only one clip plays at a time across the app.
let _audio = null

/** Play/stop button for a generated audio clip (url is a relative /files path). */
export function AudioButton({ url, title = "Play" }) {
  const [playing, setPlaying] = useState(false)
  const myUrl = useRef(url)
  myUrl.current = url

  const toggle = () => {
    if (!url) return
    if (_audio) { _audio.pause(); _audio = null }
    if (playing) { setPlaying(false); return }
    const a = new Audio(`${FILES_ORIGIN}${url}`)
    _audio = a
    a.onended = () => setPlaying(false)
    a.onerror = () => setPlaying(false)
    a.play().then(() => setPlaying(true)).catch(() => setPlaying(false))
  }

  const disabled = !url
  return (
    <button onClick={toggle} disabled={disabled} title={disabled ? "No audio yet" : title}
      style={{
        width: 26, height: 26, borderRadius: "50%", flexShrink: 0,
        border: `1px solid ${disabled ? colors.border : colors.accent}`,
        background: "none", color: disabled ? colors.muted : colors.accent,
        cursor: disabled ? "default" : "pointer", fontSize: 11,
      }}>
      {playing ? "■" : "▶"}
    </button>
  )
}

/** Small "regenerate" icon button. */
export function RegenButton({ onClick, label = "Regenerate", disabled, busy }) {
  return (
    <button onClick={onClick} disabled={disabled || busy} title={label}
      style={{
        background: "none", border: `1px solid ${colors.border}`,
        color: disabled ? colors.muted : colors.textDim, borderRadius: radius.sm,
        padding: "3px 9px", fontSize: fonts.xs, cursor: disabled || busy ? "default" : "pointer",
        whiteSpace: "nowrap",
      }}>
      {busy ? "…" : "⟳"} {label}
    </button>
  )
}

/** Centered message used by detail views for empty/loading/error states. */
export function DetailCenter({ children }) {
  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      color: colors.muted, fontSize: fonts.base, padding: 40, textAlign: "center",
      minHeight: 200,
    }}>{children}</div>
  )
}

/** Shared header strip for a stage detail view: title, subtitle, status, extra
 *  slot (e.g. "Regenerate all"), and the Run button. */
export function DetailHeader({ title, subtitle, status, progress, busy, onRun, runLabel = "Run", runDisabled = false, extra }) {
  const color = {
    done: colors.success, running: colors.warning, failed: colors.error,
    skipped: colors.muted, outdated: colors.warning,
  }[status] || colors.muted
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 12, marginBottom: 12, flexWrap: "wrap",
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: fonts.md, fontWeight: fonts.bold, color: colors.text }}>{title}</div>
        {subtitle && <div style={{ fontSize: fonts.xs, color: colors.muted, marginTop: 2 }}>{subtitle}</div>}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <span style={{
          fontSize: fonts.xs, color, border: `1px solid ${color}`,
          borderRadius: radius.full, padding: "2px 9px",
        }}>
          {status}{status === "running" && progress ? ` ${progress}%` : ""}
        </span>
        {extra}
        {onRun && (() => {
          const off = busy || runDisabled
          return (
            <button
              onClick={onRun} disabled={off}
              title={runDisabled && !busy ? "Select at least one language first" : ""}
              style={{
                background: off ? colors.btnBg : colors.accent, color: off ? colors.muted : "#000",
                border: "none", borderRadius: radius.sm, padding: "6px 13px",
                fontSize: fonts.sm, fontWeight: fonts.medium, cursor: off ? "not-allowed" : "pointer",
              }}
            >{runLabel}</button>
          )
        })()}
      </div>
    </div>
  )
}
