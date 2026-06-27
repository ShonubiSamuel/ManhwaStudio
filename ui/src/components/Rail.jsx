/**
 * ui/src/components/Rail.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Shared "resizable rail" behaviour — a side panel the user can manually drag
 * to resize and collapse to a narrow strip, with the width + collapsed state
 * persisted per rail. Mirrors the Pipeline stages rail so every side panel
 * (nav, settings, library) behaves the same way.
 *
 *   const rail = useResizableRail({ storageKey: "ms_nav", defaultWidth: 220 })
 *   <aside style={{ width: rail.width, position: "relative" }}>
 *     … rail.collapsed ? compact : full …
 *     {!rail.collapsed && <RailDragHandle onMouseDown={rail.onDragStart} />}
 *   </aside>
 */

import { useState, useRef, useEffect, useCallback } from "react"
import { colors } from "../theme"

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export function useResizableRail({ storageKey, defaultWidth, min = 150, max = 360, collapsedWidth = 52 }) {
  const [width, setWidth] = useState(() => Number(lsGet(`${storageKey}_w`, defaultWidth)) || defaultWidth)
  const [collapsed, setCollapsed] = useState(() => lsGet(`${storageKey}_c`, "0") === "1")
  const dragging = useRef(false)
  const startRef = useRef({ x: 0, w: 0 })
  const widthRef = useRef(width)
  widthRef.current = width

  useEffect(() => {
    const move = (e) => {
      if (!dragging.current) return
      const w = Math.max(min, Math.min(max, startRef.current.w + (e.clientX - startRef.current.x)))
      setWidth(w)
    }
    const up = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.userSelect = ""
      document.body.style.cursor = ""
      lsSet(`${storageKey}_w`, String(Math.round(widthRef.current)))
    }
    window.addEventListener("mousemove", move)
    window.addEventListener("mouseup", up)
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up) }
  }, [min, max, storageKey])

  const onDragStart = useCallback((e) => {
    e.preventDefault()
    dragging.current = true
    startRef.current = { x: e.clientX, w: widthRef.current }
    document.body.style.userSelect = "none"
    document.body.style.cursor = "col-resize"
  }, [])

  const toggle = useCallback(() => {
    setCollapsed(c => { const v = !c; lsSet(`${storageKey}_c`, v ? "1" : "0"); return v })
  }, [storageKey])

  return { width: collapsed ? collapsedWidth : width, collapsed, toggle, onDragStart }
}

/** Thin draggable strip pinned to the right edge of a position:relative rail. */
export function RailDragHandle({ onMouseDown }) {
  return (
    <div
      onMouseDown={onMouseDown}
      title="Drag to resize"
      style={{ position: "absolute", top: 0, right: -3, width: 6, height: "100%", cursor: "col-resize", zIndex: 5, background: "transparent" }}
      onMouseEnter={e => (e.currentTarget.style.background = colors.accent)}
      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
    />
  )
}
