/**
 * ui/src/store/notify.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * App-wide notification layer.
 *
 *   notify({ severity, message, action })  → shows a toast
 *     • severity "success" | "info"  → transient, auto-dismisses after ~3.5s
 *     • severity "warning" | "error" → persistent, stays until dismissed, and
 *       increments `unreadIssues` so the sidebar Logs item shows a badge even
 *       when the user is on another page.
 *   action: { label, onClick } renders a one-tap button inside the toast
 *           (e.g. "Set tone prompt" → jump straight to the fix).
 *
 * The badge count is cleared by `clearUnread()` (called when Logs is opened).
 * Mount <Toaster/> once near the app root.
 */

import { createContext, useContext, useReducer, useCallback, useRef } from "react"
import { colors, fonts, radius } from "../theme"

const TRANSIENT = new Set(["success", "info"])
const AUTO_MS = 3500

const NotifyContext = createContext(null)

let _id = 0

function reducer(state, action) {
  switch (action.type) {
    case "ADD": {
      const isIssue = !TRANSIENT.has(action.toast.severity)
      return {
        ...state,
        toasts: [...state.toasts, action.toast],
        unreadIssues: state.unreadIssues + (isIssue ? 1 : 0),
      }
    }
    case "DISMISS":
      return { ...state, toasts: state.toasts.filter(t => t.id !== action.id) }
    case "CLEAR_UNREAD":
      return { ...state, unreadIssues: 0 }
    default:
      return state
  }
}

export function NotificationsProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, { toasts: [], unreadIssues: 0 })
  const timers = useRef({})

  const dismiss = useCallback((id) => {
    clearTimeout(timers.current[id]); delete timers.current[id]
    dispatch({ type: "DISMISS", id })
  }, [])

  const notify = useCallback((opts) => {
    const toast = {
      id: ++_id,
      severity: opts.severity || "info",
      message: opts.message || "",
      action: opts.action || null,
    }
    dispatch({ type: "ADD", toast })
    if (TRANSIENT.has(toast.severity)) {
      timers.current[toast.id] = setTimeout(() => dismiss(toast.id), AUTO_MS)
    }
    return toast.id
  }, [dismiss])

  const clearUnread = useCallback(() => dispatch({ type: "CLEAR_UNREAD" }), [])

  return (
    <NotifyContext.Provider value={{ ...state, notify, dismiss, clearUnread }}>
      {children}
    </NotifyContext.Provider>
  )
}

export function useNotify() {
  const ctx = useContext(NotifyContext)
  if (!ctx) throw new Error("useNotify() must be used inside <NotificationsProvider>")
  return ctx
}

// ── Toaster ─────────────────────────────────────────────────────────────────

const SEV = {
  success: { fg: colors.success, bg: "#16261b" },
  info:    { fg: colors.info,    bg: "#142233" },
  warning: { fg: colors.warning, bg: "#2a2415" },
  error:   { fg: colors.error,   bg: "#2a1717" },
}

export function Toaster() {
  const { toasts, dismiss } = useNotify()
  return (
    <div style={{
      position: "fixed", top: 16, right: 16, zIndex: 1000,
      display: "flex", flexDirection: "column", gap: 8, width: 320, maxWidth: "90vw",
      pointerEvents: "none",
    }}>
      {toasts.map(t => {
        const s = SEV[t.severity] || SEV.info
        return (
          <div key={t.id} style={{
            pointerEvents: "auto",
            display: "flex", alignItems: "flex-start", gap: 9,
            background: s.bg, border: `1px solid ${s.fg}`, color: s.fg,
            borderRadius: radius.md, padding: "10px 12px", fontSize: fonts.sm,
            fontFamily: fonts.ui, boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
          }}>
            <div style={{ flex: 1, lineHeight: 1.45 }}>
              <div>{t.message}</div>
              {t.action && (
                <button
                  onClick={() => { t.action.onClick?.(); dismiss(t.id) }}
                  style={{
                    marginTop: 6, background: "none", border: `1px solid ${s.fg}`,
                    color: s.fg, borderRadius: radius.sm, padding: "3px 9px",
                    fontSize: fonts.xs, cursor: "pointer",
                  }}
                >{t.action.label}</button>
              )}
            </div>
            <button onClick={() => dismiss(t.id)} aria-label="Dismiss"
              style={{ background: "none", border: "none", color: s.fg, cursor: "pointer", fontSize: fonts.md, opacity: 0.7, lineHeight: 1 }}>×</button>
          </div>
        )
      })}
    </div>
  )
}
