/**
 * ui/src/theme.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Single source of truth for every colour, font, radius, shadow and spacing
 * value used across the React UI.
 *
 * Mirrors ui/theme.py from the Tkinter app exactly so the two UIs stay
 * visually consistent during the transition period.
 *
 * Usage
 * ─────
 *   import { colors, fonts, radius, status } from "../theme"
 *
 *   <div style={{ background: colors.bg, color: colors.text }}>...</div>
 */

// ── Colour palette ────────────────────────────────────────────────────────────

export const colors = {
  // Surfaces
  bg:      "#0e0e0f",   // app background — near-black
  panel:   "#141416",   // elevated surface (sidebar, top bar)
  panel2:  "#1a1a1c",   // double-elevated (card, modal background)
  border:  "#2a2a2e",   // subtle dividers and widget borders

  // Brand
  accent:  "#ff6b35",   // primary action — orange
  accent2: "#c94e1f",   // darker accent (hover / destructive)

  // Text
  text:    "#f0f0f0",   // primary body text
  textDim: "#aaaaaa",   // secondary / label text
  muted:   "#666666",   // placeholder / disabled

  // Semantic
  success: "#4ade80",   // green  — done / ok
  error:   "#f87171",   // red    — failed / destructive
  warning: "#fbbf24",   // amber  — running / caution
  info:    "#60a5fa",   // blue   — informational

  // Interactive
  btnBg:   "#2a2a2e",   // default button background
  btnFg:   "#f0f0f0",   // default button foreground
  selBg:   "#2a2020",   // selected-row highlight (warm dark)
  
  // Dub Studio Screenshot Colors
  cueOrig:       "transparent", // The original text area has no background
  cueTrans:      "#1e293b",     // Dark blue/slate for translated input
  timelineCue:   "#1d4ed8",     // Bright blue for timeline blocks
  timelineRuler: "#27272a",     // Ruler background
}


// ── Typography ─────────────────────────────────────────────────────────────────
// UI text uses the best system font for each platform (SF Pro on macOS,
// Segoe UI on Windows).  Log panels and code use a monospace stack.

export const fonts = {
  // Primary — clean and readable at all sizes
  ui:   '"system-ui", "-apple-system", "Segoe UI", "Helvetica Neue", sans-serif',

  // Monospace — log panels, panel indices, code snippets
  mono: '"JetBrains Mono", "Fira Code", "Cascadia Code", "Courier New", monospace',

  // Size scale
  xs:   "11px",
  sm:   "12px",
  base: "13px",
  md:   "14px",
  lg:   "16px",
  xl:   "20px",
  xxl:  "24px",

  // Weight
  normal: 400,
  medium: 500,
  bold:   600,
}


// ── Border radius ─────────────────────────────────────────────────────────────

export const radius = {
  sm:   "4px",
  md:   "8px",
  lg:   "12px",
  full: "9999px",
}


// ── Spacing scale (multiples of 4px) ─────────────────────────────────────────

export const space = {
  1:  "4px",
  2:  "8px",
  3:  "12px",
  4:  "16px",
  5:  "20px",
  6:  "24px",
  8:  "32px",
  10: "40px",
  12: "48px",
}


// ── Shadows ───────────────────────────────────────────────────────────────────

export const shadows = {
  sm:  "0 1px 3px rgba(0,0,0,0.4)",
  md:  "0 4px 12px rgba(0,0,0,0.5)",
  lg:  "0 8px 24px rgba(0,0,0,0.6)",
}


// ── Stage / status system ─────────────────────────────────────────────────────
// Maps pipeline stage status strings to colour and icon.
// Mirrors STATUS_COLORS and STATUS_ICONS from the Python theme.

export const status = {
  color: {
    pending:  colors.muted,
    running:  colors.warning,
    done:     colors.success,
    failed:   colors.error,
    skipped:  "#444444",
    outdated: colors.warning,   // upstream edit invalidated this stage's output
  },
  icon: {
    pending:  "○",
    running:  "●",
    done:     "✓",
    failed:   "✗",
    skipped:  "—",
    outdated: "⟳",
  },
  label: {
    pending:  "Pending",
    running:  "Running",
    done:     "Done",
    failed:   "Failed",
    skipped:  "Skipped",
    outdated: "Outdated",
  },
}


// ── Log level colours ─────────────────────────────────────────────────────────
// Mirrors LOG_COLORS from the Python theme.

export const logColors = {
  accent:  colors.accent,
  success: colors.success,
  error:   colors.error,
  warning: colors.warning,
  info:    colors.info,
  muted:   colors.muted,
}


// ── Common reusable style objects ─────────────────────────────────────────────
// Frequently repeated combinations — avoids duplicating the same four
// properties on every component.

export const styles = {
  // Full-height dark page background
  page: {
    background: colors.bg,
    minHeight:  "100vh",
    color:      colors.text,
    fontFamily: fonts.ui,
    fontSize:   fonts.base,
  },

  // Slightly elevated card / panel surface
  card: {
    background:   colors.panel,
    border:       `1px solid ${colors.border}`,
    borderRadius: radius.lg,
  },

  // Thin horizontal rule
  divider: {
    height:     "1px",
    background: colors.border,
    border:     "none",
    margin:     "0",
  },

  // Clickable row that highlights on hover (apply via className + CSS,
  // or spread this and add :hover via inline onMouseEnter/Leave)
  row: {
    display:     "flex",
    alignItems:  "center",
    gap:         space[3],
    padding:     `${space[2]} ${space[4]}`,
    cursor:      "pointer",
    borderRadius: radius.sm,
  },
}
