/**
 * ui/src/components/LogPanel.jsx — ManhwaStudio v2
 *
 * Scrolling log viewer for live pipeline output.  Auto-scrolls to the
 * bottom as new lines arrive, unless the user has scrolled up to read
 * history — in that case auto-scroll pauses until they scroll back down.
 *
 * Props
 * ─────
 *   lines     Array<{ message: string, level: string, id: number }>
 *   height    number — px height of the scroll area, default 280
 *   onClear   fn     — optional clear button handler
 */

import { useRef, useEffect, useState } from "react"
import { colors, fonts, radius, logColors } from "../theme"

export default function LogPanel({ lines = [], height = 280, onClear }) {
  const scrollRef       = useRef(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Auto-scroll to bottom on new lines, unless the user scrolled up manually.
  useEffect(() => {
    if (!autoScroll || !scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [lines, autoScroll])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    // Within 24px of the bottom counts as "still following"
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    setAutoScroll(atBottom)
  }

  return (
    <div style={{
      background:   "#0a0a0b",
      border:       `1px solid ${colors.border}`,
      borderRadius: radius.md,
      overflow:     "hidden",
    }}>
      {/* Header */}
      <div style={{
        display:        "flex",
        alignItems:     "center",
        justifyContent: "space-between",
        padding:        "8px 12px",
        borderBottom:   `1px solid ${colors.border}`,
        background:     colors.panel,
      }}>
        <span style={{
          color:         colors.textDim,
          fontSize:      fonts.xs,
          fontWeight:    fonts.bold,
          letterSpacing: "0.08em",
        }}>
          LIVE LOG
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {!autoScroll && (
            <button
              onClick={() => setAutoScroll(true)}
              style={{
                background:   "none",
                border:       `1px solid ${colors.border}`,
                borderRadius: radius.sm,
                color:        colors.accent,
                fontSize:     fonts.xs,
                padding:      "2px 8px",
                cursor:       "pointer",
              }}
            >
              ↓ Jump to latest
            </button>
          )}
          {onClear && (
            <button
              onClick={onClear}
              style={{
                background: "none",
                border:     "none",
                color:      colors.muted,
                fontSize:   fonts.xs,
                cursor:     "pointer",
              }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Scroll area */}
      <div
        ref      = {scrollRef}
        onScroll = {handleScroll}
        style={{
          height:     `${height}px`,
          overflowY:  "auto",
          padding:    "10px 12px",
          fontFamily: fonts.mono,
          fontSize:   fonts.sm,
          // Force the log to be selectable/copyable — the desktop webview
          // defaults app content to non-selectable, which blocks copying errors.
          userSelect:       "text",
          WebkitUserSelect:  "text",
          cursor:           "text",
        }}
      >
        {lines.length === 0 && (
          <div style={{ color: colors.muted, fontStyle: "italic" }}>
            No output yet — run a stage to see live logs here.
          </div>
        )}
        {lines.map(line => (
          <div
            key={line.id}
            style={{
              color:        logColors[line.level] || colors.text,
              whiteSpace:   "pre-wrap",
              wordBreak:    "break-word",
              lineHeight:   1.6,
            }}
          >
            {line.message}
          </div>
        ))}
      </div>
    </div>
  )
}
