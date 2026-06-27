/**
 * ui/src/components/Modal.jsx — ManhwaStudio v2
 *
 * Generic dark modal dialog.  Used for create-project, import-episode,
 * and confirm-delete flows.
 *
 * Props
 * ─────
 *   open           bool   — whether the modal is visible
 *   title          string — header title
 *   subtitle       string — optional muted sub-line under title
 *   onClose        fn     — called when backdrop clicked or Escape pressed
 *   onConfirm      fn     — confirm button handler (omit to hide confirm button)
 *   confirmLabel   string — default "Confirm"
 *   confirmVariant string — Button variant for the confirm button, default "primary"
 *   confirmLoading bool   — shows spinner on confirm button
 *   cancelLabel    string — default "Cancel"
 *   width          number — modal card width in px, default 480
 *   children       node   — modal body content
 */

import { useEffect } from "react"
import { colors, fonts, radius, shadows } from "../theme"
import Button from "./Button"

export default function Modal({
  open,
  title,
  subtitle,
  onClose,
  onConfirm,
  confirmLabel   = "Confirm",
  confirmVariant = "primary",
  confirmLoading = false,
  cancelLabel    = "Cancel",
  width          = 480,
  children,
}) {
  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (e.key === "Escape") onClose?.() }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [open, onClose])

  if (!open) return null

  return (
    // Backdrop
    <div
      onClick={onClose}
      style={{
        position:       "fixed",
        inset:          0,
        background:     "rgba(0, 0, 0, 0.65)",
        display:        "flex",
        alignItems:     "center",
        justifyContent: "center",
        zIndex:         1000,
        backdropFilter: "blur(2px)",
      }}
    >
      {/* Card — stop clicks from bubbling to backdrop */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background:    colors.panel,
          border:        `1px solid ${colors.border}`,
          borderRadius:  radius.lg,
          boxShadow:     shadows.lg,
          width:         `${width}px`,
          maxWidth:      "calc(100vw - 48px)",
          maxHeight:     "calc(100vh - 96px)",
          display:       "flex",
          flexDirection: "column",
          overflow:      "hidden",
        }}
      >
        {/* ── Header ───────────────────────────────────────────────────── */}
        <div style={{
          padding:      "20px 24px 16px",
          borderBottom: `1px solid ${colors.border}`,
          flexShrink:   0,
        }}>
          <div style={{
            color:      colors.text,
            fontSize:   fonts.lg,
            fontWeight: fonts.bold,
            lineHeight: 1.3,
          }}>
            {title}
          </div>
          {subtitle && (
            <div style={{
              color:     colors.textDim,
              fontSize:  fonts.sm,
              marginTop: 4,
            }}>
              {subtitle}
            </div>
          )}
        </div>

        {/* ── Body ─────────────────────────────────────────────────────── */}
        <div style={{ padding: "20px 24px", overflow: "auto", flex: 1 }}>
          {children}
        </div>

        {/* ── Footer ───────────────────────────────────────────────────── */}
        <div style={{
          padding:         "14px 24px",
          borderTop:       `1px solid ${colors.border}`,
          display:         "flex",
          justifyContent:  "flex-end",
          gap:             "8px",
          flexShrink:      0,
        }}>
          <Button variant="ghost" onClick={onClose}>
            {cancelLabel}
          </Button>
          {onConfirm && (
            <Button
              variant={confirmVariant}
              onClick={onConfirm}
              loading={confirmLoading}
            >
              {confirmLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}


// ── FormField helper (exported for use in Library and future pages) ────────────

/**
 * A labelled form field wrapper used inside modals.
 *
 * <FormField label="Title" hint="Required">
 *   <input ... />
 * </FormField>
 */
export function FormField({ label, hint, error, children, style }) {
  return (
    <div style={{ marginBottom: "16px", ...style }}>
      <div style={{
        display:        "flex",
        justifyContent: "space-between",
        marginBottom:   "6px",
      }}>
        <label style={{ color: colors.textDim, fontSize: fonts.sm, fontWeight: fonts.medium }}>
          {label}
        </label>
        {hint && (
          <span style={{ color: colors.muted, fontSize: fonts.xs }}>{hint}</span>
        )}
      </div>
      {children}
      {error && (
        <div style={{ color: colors.error, fontSize: fonts.xs, marginTop: 4 }}>
          {error}
        </div>
      )}
    </div>
  )
}


/**
 * Styled text input that matches the dark theme.
 * Pass all standard <input> props.
 */
export function TextInput({ style, ...props }) {
  return (
    <input
      style={{
        width:           "100%",
        background:      colors.panel2,
        border:          `1px solid ${colors.border}`,
        borderRadius:    radius.sm,
        color:           colors.text,
        padding:         "8px 10px",
        fontSize:        fonts.base,
        transition:      "border-color 0.12s",
        ...style,
      }}
      onFocus={(e) => { e.target.style.borderColor = colors.accent }}
      onBlur={(e)  => { e.target.style.borderColor = colors.border }}
      {...props}
    />
  )
}


/**
 * Styled textarea that matches the dark theme.
 */
export function TextArea({ style, ...props }) {
  return (
    <textarea
      style={{
        width:        "100%",
        background:   colors.panel2,
        border:       `1px solid ${colors.border}`,
        borderRadius: radius.sm,
        color:        colors.text,
        padding:      "8px 10px",
        fontSize:     fonts.base,
        resize:       "vertical",
        minHeight:    "72px",
        transition:   "border-color 0.12s",
        ...style,
      }}
      onFocus={(e) => { e.target.style.borderColor = colors.accent }}
      onBlur={(e)  => { e.target.style.borderColor = colors.border }}
      {...props}
    />
  )
}
