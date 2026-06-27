/**
 * ui/src/components/ProgressBar.jsx — ManhwaStudio v2
 *
 * Props
 * ─────
 *   pct        number  0–100
 *   label      string  optional left-side label
 *   showPct    bool    show "NN%" on the right, default true
 *   color      string  override fill colour, default accent (or success at 100%)
 *   height     number  bar height in px, default 6
 *   animated   bool    pulses while running, default false
 */

import { colors, fonts, radius } from "../theme"

export default function ProgressBar({
  pct      = 0,
  label,
  showPct  = true,
  color,
  height   = 6,
  animated = false,
}) {
  const clamped  = Math.max(0, Math.min(100, pct))
  const fillColor = color || (clamped >= 100 ? colors.success : colors.accent)

  return (
    <div>
      {(label || showPct) && (
        <div style={{
          display:        "flex",
          justifyContent: "space-between",
          marginBottom:   5,
        }}>
          {label && (
            <span style={{ color: colors.textDim, fontSize: fonts.xs }}>{label}</span>
          )}
          {showPct && (
            <span style={{ color: colors.textDim, fontSize: fonts.xs }}>{clamped}%</span>
          )}
        </div>
      )}
      <div style={{
        height:       `${height}px`,
        background:   colors.panel2,
        borderRadius: radius.full,
        overflow:     "hidden",
      }}>
        <div style={{
          height:       "100%",
          width:        `${clamped}%`,
          background:   fillColor,
          borderRadius: radius.full,
          transition:   "width 0.3s ease, background 0.2s ease",
          ...(animated ? { animation: "ms-pulse 1.4s ease-in-out infinite" } : {}),
        }} />
      </div>
    </div>
  )
}
