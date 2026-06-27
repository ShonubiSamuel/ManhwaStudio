/**
 * ui/src/components/Button.jsx — ManhwaStudio v2
 *
 * Props
 * ─────
 *   variant   "primary" | "secondary" | "ghost" | "danger"   default: "secondary"
 *   size      "sm" | "md"                                      default: "md"
 *   loading   bool — shows spinner, disables click            default: false
 *   disabled  bool                                             default: false
 *   fullWidth bool — stretches to container width             default: false
 *   onClick   handler
 *   style     additional inline styles (merged last)
 */

import { useState } from "react"
import { colors, fonts, radius } from "../theme"

// ── Style maps ────────────────────────────────────────────────────────────────

const VARIANT = {
  primary: {
    base:  { background: colors.accent,  color: "#000",          border: "none" },
    hover: { background: colors.accent2, color: "#000" },
  },
  secondary: {
    base:  { background: colors.btnBg,   color: colors.text,     border: "none" },
    hover: { background: "#353538",      color: colors.text },
  },
  ghost: {
    base:  { background: "transparent",  color: colors.textDim,  border: `1px solid ${colors.border}` },
    hover: { background: colors.panel2,  color: colors.text },
  },
  danger: {
    base:  { background: "transparent",  color: colors.error,    border: `1px solid ${colors.error}` },
    hover: { background: "rgba(248,113,113,0.08)", color: colors.error },
  },
}

const SIZE = {
  sm: { padding: "4px 10px",  fontSize: fonts.sm,   fontWeight: fonts.medium },
  md: { padding: "7px 14px",  fontSize: fonts.base, fontWeight: fonts.medium },
}


// ── Component ─────────────────────────────────────────────────────────────────

export default function Button({
  children,
  variant   = "secondary",
  size      = "md",
  onClick,
  disabled  = false,
  loading   = false,
  fullWidth = false,
  style,
  ...rest
}) {
  const [hovered, setHovered] = useState(false)

  const v   = VARIANT[variant] || VARIANT.secondary
  const s   = SIZE[size]       || SIZE.md
  const off = disabled || loading

  const computed = {
    // Base layout
    display:        "inline-flex",
    alignItems:     "center",
    justifyContent: "center",
    gap:            "6px",
    borderRadius:   radius.sm,
    cursor:         off ? "not-allowed" : "pointer",
    opacity:        disabled ? 0.4 : 1,
    whiteSpace:     "nowrap",
    letterSpacing:  "0.02em",
    transition:     "background 0.12s, color 0.12s, border-color 0.12s",
    width:          fullWidth ? "100%" : undefined,
    userSelect:     "none",

    // Variant + size
    ...v.base,
    ...s,

    // Hover (only when not disabled)
    ...(hovered && !off ? v.hover : {}),

    // Consumer overrides
    ...style,
  }

  return (
    <button
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={!off ? onClick : undefined}
      style={computed}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  )
}


// ── Spinner (internal) ────────────────────────────────────────────────────────

function Spinner() {
  return (
    <span style={{
      display:      "inline-block",
      width:        "10px",
      height:       "10px",
      border:       "1.5px solid currentColor",
      borderTopColor: "transparent",
      borderRadius: "50%",
      animation:    "ms-spin 0.65s linear infinite",
      flexShrink:   0,
    }} />
  )
}
