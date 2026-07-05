/**
 * ui/src/components/PdfReader.jsx — client-side PDF panel cropper (PDF.js).
 *
 * Renders a PDF as one continuous webtoon strip (each page at its own aspect),
 * lets you draw / move / resize a crop box across page boundaries, zoom to the
 * cursor, and composites the crop to a PNG data-URL. Shared by Video Refine.
 */

import { useState, useEffect, useRef, useMemo, useLayoutEffect } from "react"
import * as pdfjs from "pdfjs-dist"
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"
import { colors, fonts, radius } from "../theme"
import Button from "./Button"

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

const clamp01 = (v) => Math.min(1, Math.max(0, v))
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"]
const HCURSOR = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" }

function PageCanvas({ doc, pageNum, scrollRef }) {
  const ref = useRef(null)
  const [vis, setVis] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || !scrollRef.current) return
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) setVis(true) },
      { root: scrollRef.current, rootMargin: "400px 0px" })
    io.observe(el)
    return () => io.disconnect()
  }, [scrollRef])
  useEffect(() => {
    if (!vis || !doc) return
    let cancelled = false
    doc.getPage(pageNum).then((page) => {
      if (cancelled) return
      const c = ref.current
      if (!c) return
      const s = 1.3 * (window.devicePixelRatio || 1)
      const vp = page.getViewport({ scale: s })
      c.width = vp.width; c.height = vp.height
      page.render({ canvasContext: c.getContext("2d"), viewport: vp })
    }).catch(() => {})
    return () => { cancelled = true }
  }, [vis, doc, pageNum])
  return <canvas ref={ref} style={{ width: "100%", height: "100%", display: "block" }} />
}

// multi mode (Recap): every finished crop STAYS on the page as a numbered box,
// stacking one under another so the whole chapter's cropping is reviewable
// before sending; onNarrate(dataUrls) fires with every crop composited in order.
function PdfReader({ pdfUrl, cropping, activeCue, onOpen, onAttach, multi = false, onNarrate = null, narrating = false, boxesValue = null, onBoxesChange = null }) {
  const [doc, setDoc] = useState(null)
  const [dims, setDims] = useState([])     // intrinsic {w,h} per page (scale 1)
  const [loading, setLoading] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [box, setBox] = useState(null)     // { x,w : fraction of width ; y,h : intrinsic px in column }
  // Committed crops. CONTROLLED when the parent passes boxesValue/onBoxesChange
  // (Recap persists them in the session so they survive leaving/closing the app).
  const [boxesInternal, setBoxesInternal] = useState([])
  const controlled = multi && typeof onBoxesChange === "function"
  const boxes = controlled ? (boxesValue || []) : boxesInternal
  const setBoxes = controlled
    ? (u) => onBoxesChange(typeof u === "function" ? u(boxesValue || []) : u)
    : setBoxesInternal
  const [space, setSpace] = useState(false)
  const [panning, setPanning] = useState(false)
  const [err, setErr] = useState("")
  const scrollRef = useRef(null)

  // Load the PDF client-side (PDF.js) — instant first page, lazy rest.
  useEffect(() => {
    let cancelled = false
    // Reset view for the new PDF (legitimate reset-on-input-change).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    // Don't wipe committed crops when controlled — the parent owns/persists them.
    setBox(null); if (!controlled) setBoxesInternal([])
    setDoc(null); setDims([]); setErr(""); setLoading(!!pdfUrl)
    if (!pdfUrl) return
    const task = pdfjs.getDocument({ url: pdfUrl, disableRange: false })
    task.promise.then(async (d) => {
      const ds = []
      for (let i = 1; i <= d.numPages; i++) {
        const pg = await d.getPage(i)
        const v = pg.getViewport({ scale: 1 })
        ds.push({ w: v.width, h: v.height })
      }
      if (cancelled) { d.destroy?.(); return }
      setDoc(d); setDims(ds); setLoading(false)
    }).catch((e) => {
      if (cancelled) return
      console.error("pdf load failed:", e, "url=", pdfUrl)
      setErr(`${e?.name || "Error"}: ${e?.message || e}`)
      setLoading(false)
    })
    return () => { cancelled = true; task.destroy?.() }
  }, [pdfUrl])

  // Per-page geometry. All pages are shown at the SAME display width but each at
  // its OWN aspect ratio (height = width × h/w), so narrower pages aren't squashed.
  // Heights are measured in "units of one column width"; offsets accumulate them.
  const displayW = 620 * zoom
  const geo = useMemo(() => {
    const uh = dims.map((d) => (d.w ? d.h / d.w : 1))   // height per unit width
    const uoff = []; let t = 0
    for (const h of uh) { uoff.push(t); t += h }
    return { uh, uoff, total: t || 1 }
  }, [dims])
  const colH = displayW * geo.total          // total display column height

  // Box is stored as fractions: x,w of width ; y,h of the whole column.
  const normInColumn = (e, colEl) => {
    const r = colEl.getBoundingClientRect()
    return { x: clamp01((e.clientX - r.left) / r.width), y: clamp01((e.clientY - r.top) / r.height) }
  }

  // Draw / move / resize a box across the continuous column (cross-page).
  //   idx <  0 → the ACTIVE (orange) box being drawn.
  //   idx >= 0 → a COMMITTED (blue) box in `boxes` — so crops stay adjustable
  //              after drawing (move body, drag handles) just like a fresh one.
  const begin = (e, mode, colEl, idx = -1) => {
    if (space || e.button !== 0) return
    if (mode !== "draw") e.stopPropagation()
    const sm = normInColumn(e, colEl)
    const editing = idx >= 0
    const sb = editing ? { ...boxes[idx] } : (box ? { ...box } : null)
    if (mode === "draw") setBox(null)

    // cur tracks the latest geometry in a plain closure var, so the commit reads
    // it directly — NEVER call setBoxes inside a setBox updater (StrictMode
    // double-invokes updaters, which is what made one crop count as two).
    let cur = mode === "draw" ? null : sb
    let isDrawing = false
    const apply = (nb) => {
      cur = nb
      if (editing) setBoxes((list) => list.map((b, k) => (k === idx ? nb : b)))
      else setBox(nb)
    }
    const mv = (ev) => {
      const m = normInColumn(ev, colEl)
      if (mode === "draw") {
        const w = Math.abs(m.x - sm.x), h = Math.abs(m.y - sm.y)
        if (!isDrawing && (w > 0.005 || h > 0.005)) isDrawing = true
        if (isDrawing) apply({ x: Math.min(sm.x, m.x), y: Math.min(sm.y, m.y), w, h })
      } else if (mode === "move" && sb) {
        apply({ ...sb,
          x: Math.min(clamp01(sb.x + (m.x - sm.x)), 1 - sb.w),
          y: Math.min(clamp01(sb.y + (m.y - sm.y)), 1 - sb.h) })
      } else if (sb) {
        const hh = mode.slice(7)
        let L = sb.x, T = sb.y, R = sb.x + sb.w, B = sb.y + sb.h
        if (hh.includes("w")) L = m.x; if (hh.includes("e")) R = m.x
        if (hh.includes("n")) T = m.y; if (hh.includes("s")) B = m.y
        apply({ x: Math.min(L, R), y: Math.min(T, B), w: Math.abs(R - L), h: Math.abs(B - T) })
      }
    }
    const up = () => {
      window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up)
      // Multi mode: a finished DRAW commits the crop into `boxes` (once).
      if (multi && mode === "draw") {
        if (cur && cur.w > 0.005 && cur.h > 0.002) setBoxes((list) => [...list, cur])
        setBox(null)
      }
    }
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up)
  }

  // Composite the crop from every page a box overlaps (re-rendered crisp).
  // Everything is in "units of one column width"; the box's y/h are fractions of
  // the whole column (geo.total units).
  const cropToDataUrl = async (bArg) => {
    const b0 = bArg || box
    if (!doc || !b0 || b0.w < 0.005 || b0.h < 0.002) return null
    const cs = 2                                    // render quality (× intrinsic)
    const yU0 = b0.y * geo.total, yU1 = (b0.y + b0.h) * geo.total   // in units
    const refW = dims[0]?.w || 1
    const outW = Math.max(1, Math.round(b0.w * refW * cs))
    const pxPerUnit = outW / b0.w                  // output px for one column width
    const out = document.createElement("canvas")
    out.width = outW
    out.height = Math.max(1, Math.round((yU1 - yU0) * pxPerUnit))
    const ctx = out.getContext("2d")
    let dyOut = 0
    for (let i = 0; i < dims.length; i++) {
      const top = geo.uoff[i], bot = top + geo.uh[i]
      const a = Math.max(yU0, top) - top, b = Math.min(yU1, bot) - top   // overlap (units from page top)
      if (b <= a) continue
      const page = await doc.getPage(i + 1)
      const vp = page.getViewport({ scale: cs })
      const tmp = document.createElement("canvas"); tmp.width = vp.width; tmp.height = vp.height
      await page.render({ canvasContext: tmp.getContext("2d"), viewport: vp }).promise
      const sx = b0.x * dims[i].w * cs
      const sw = b0.w * dims[i].w * cs
      const sy = a * dims[i].w * cs                 // a units × page-width px (= intrinsic y)
      const sh = (b - a) * dims[i].w * cs
      const dh = (b - a) * pxPerUnit
      ctx.drawImage(tmp, sx, sy, sw, sh, 0, dyOut, outW, dh)
      dyOut += dh
    }
    return out.toDataURL("image/png")
  }

  const attach = async () => {
    const data = await cropToDataUrl()
    if (!data) return
    const ok = await onAttach(data)
    if (ok) setBox(null)
  }

  // Multi mode: composite every committed crop (top-to-bottom order) and hand
  // the stack to the parent (which saves panels + narrates in batches).
  const narrateAll = async () => {
    if (!onNarrate) return
    // The PDF must be fully parsed to composite crops. After reopening the app
    // the persisted boxes can paint a frame before doc/dims finish loading.
    if (!doc || dims.length === 0) { onNarrate([], "loading"); return }
    if (!boxes.length) { onNarrate([], "empty"); return }
    const ordered = [...boxes].sort((a, b) => a.y - b.y)
    const crops = []
    for (const b of ordered) {
      try {
        const data = await cropToDataUrl(b)
        if (data && data.length > 200) crops.push({ data, box: b })   // skip degenerate/empty
        else console.warn("[recap] crop produced no data", b)
      } catch (e) {
        console.error("[recap] crop failed", b, e)
      }
    }
    onNarrate(crops, crops.length ? "ok" : "failed")
  }

  // Keyboard: A attach · Space pan · arrows nudge.
  useEffect(() => {
    const down = (e) => {
      const t = e.target
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) return
      if (e.code === "Space") { e.preventDefault(); setSpace(true) }
      else if ((e.key === "a" || e.key === "A") && box) { e.preventDefault(); attach() }
      else if (box && e.key.startsWith("Arrow")) {
        e.preventDefault()
        const dx = e.shiftKey ? 0.02 : 0.004
        const dy = (e.shiftKey ? 0.02 : 0.004)
        setBox((b) => !b ? b : ({ ...b,
          x: clamp01(b.x + (e.key === "ArrowRight" ? dx : e.key === "ArrowLeft" ? -dx : 0)),
          y: clamp01(b.y + (e.key === "ArrowDown" ? dy : e.key === "ArrowUp" ? -dy : 0)) }))
      }
    }
    const up = (e) => { if (e.code === "Space") setSpace(false) }
    window.addEventListener("keydown", down); window.addEventListener("keyup", up)
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up) }
  }, [box, doc, dims, geo.total])  // eslint-disable-line react-hooks/exhaustive-deps

  // Zoom about a screen point, keeping the document point under it fixed.
  // Document position under cursor = scrollOffset + cursorInViewport; after the
  // strip scales by `ratio`, that point moves to docPos*ratio, so the new scroll
  // = docPos*ratio − cursor. Applied in useLayoutEffect (after the strip resizes,
  // before paint) so the view never jumps. (Origin must be at scroll 0,0 — that's
  // why the strip has no top margin.)
  const pendingZoom = useRef(null)   // { left, top }
  useLayoutEffect(() => {
    const p = pendingZoom.current
    if (!p) return
    pendingZoom.current = null
    const el = scrollRef.current
    if (!el) return
    el.scrollLeft = p.left
    el.scrollTop = p.top
  }, [zoom])
  const zoomAbout = (factor, clientX, clientY) => {
    const el = scrollRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const cx = clientX != null ? clientX - rect.left : el.clientWidth / 2
    const cy = clientY != null ? clientY - rect.top : el.clientHeight / 2
    setZoom((z0) => {
      const z1 = Math.min(4, Math.max(0.1, +(z0 * factor).toFixed(3)))
      if (z1 === z0) return z0
      const ratio = z1 / z0
      pendingZoom.current = {
        left: (el.scrollLeft + cx) * ratio - cx,
        top:  (el.scrollTop + cy) * ratio - cy,
      }
      return z1
    })
  }
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onWheel = (e) => {
      if (e.ctrlKey) {                 // pinch / ⌃-scroll → zoom to cursor
        e.preventDefault()
        zoomAbout(Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY)
      } else {                         // two-finger scroll → drive it ourselves
        const k = e.deltaMode === 1 ? 16 : 1   // lines → px
        el.scrollTop += e.deltaY * k
        el.scrollLeft += e.deltaX * k
        e.preventDefault()
      }
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const zoomBtn = (f) => zoomAbout(f)

  const stripRef = useRef(null)

  // Pan with Space held, or draw box from outside the PDF.
  const startPanOrDraw = (e) => {
    if (space) {
      if (!scrollRef.current) return
      const s = { sx: e.clientX, sy: e.clientY, left: scrollRef.current.scrollLeft, top: scrollRef.current.scrollTop }
      setPanning(true)
      const mv = (ev) => { if (scrollRef.current) { scrollRef.current.scrollLeft = s.left - (ev.clientX - s.sx); scrollRef.current.scrollTop = s.top - (ev.clientY - s.sy) } }
      const up = () => { setPanning(false); window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up) }
      window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up)
    } else if (stripRef.current) {
      begin(e, "draw", stripRef.current)
    }
  }

  const sizeTxt = box ? `${Math.round(box.w * displayW)} × ${Math.round(box.h * colH)} px` : ""

  return (
    // minHeight/minWidth:0 are REQUIRED so this fills (and clips to) its flex
    // parent — without minHeight:0 in a column parent it grows to the full strip
    // height and the inner scroller never gets a bounded height (= no scrolling).
    <div className="no-select" style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", background: "#0a0a0b", userSelect: "none" }}>
      <div style={{ height: 44, flexShrink: 0, borderBottom: `1px solid ${colors.border}`, display: "flex", alignItems: "center", gap: 10, padding: "0 14px" }}>
        <span style={{ color: colors.muted, fontSize: fonts.sm, marginRight: "auto" }}>
          {space ? "✋ Pan — drag" : "Drag to draw · handles resize · Space pan · ⌃-scroll / pinch to zoom"}
        </span>
        {sizeTxt && <span style={{ color: colors.textDim, fontSize: fonts.sm, fontVariantNumeric: "tabular-nums" }}>{sizeTxt}</span>}
        <IconBtn onClick={() => zoomBtn(0.83)}>−</IconBtn>
        <span style={{ color: colors.textDim, fontSize: fonts.sm, width: 44, textAlign: "center" }}>{Math.round(zoom * 100)}%</span>
        <IconBtn onClick={() => zoomBtn(1.2)}>+</IconBtn>
        <IconBtn onClick={() => setZoom(1)} title="Reset zoom">⊡</IconBtn>
        {box && <IconBtn onClick={() => setBox(null)} title="Clear box">✕</IconBtn>}
        {multi ? (
          <>
            <span style={{ color: colors.textDim, fontSize: fonts.sm }}>{boxes.length} crop{boxes.length === 1 ? "" : "s"}</span>
            {boxes.length > 0 && <IconBtn onClick={() => setBoxes([])} title="Clear all crops">🗑</IconBtn>}
            <Button variant="primary" disabled={narrating || !boxes.length} loading={narrating}
              onClick={narrateAll} style={{ borderRadius: radius.md }}>
              {narrating ? "Narrating…" : `🪄 Narrate ${boxes.length || ""} crop${boxes.length === 1 ? "" : "s"}`}
            </Button>
          </>
        ) : (
          <Button variant="primary" disabled={cropping || !box} loading={cropping} onClick={attach} style={{ borderRadius: radius.md }}>
            {cropping ? "Saving…" : `Attach to cue ${activeCue}  (A)`}
          </Button>
        )}
      </div>

      <div ref={scrollRef} onMouseDown={startPanOrDraw}
        style={{ flex: 1, minHeight: 0, overflow: "auto", position: "relative", cursor: space ? (panning ? "grabbing" : "grab") : "crosshair" }}>
        {loading && (
          <div style={{ position: "absolute", inset: 0, zIndex: 5, background: "rgba(0,0,0,0.55)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, color: colors.text }}>
            <span style={{ width: 26, height: 26, border: "3px solid currentColor", borderTopColor: "transparent", borderRadius: "50%", display: "inline-block", animation: "ms-spin 0.7s linear infinite" }} />
            <span>Loading PDF…</span>
          </div>
        )}
        {!pdfUrl ? (
          <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", color: colors.muted }}>
            <p style={{ marginBottom: 12 }}>No PDF loaded.</p>
            <Button variant="primary" onClick={onOpen}>Open PDF</Button>
          </div>
        ) : err ? (
          <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 12, color: colors.muted, padding: 24, textAlign: "center" }}>
            <p style={{ color: colors.error || "#e35", fontWeight: fonts.bold }}>Couldn’t open this PDF</p>
            <code style={{ color: colors.textDim, fontSize: fonts.xs, maxWidth: 420, wordBreak: "break-word" }}>{err}</code>
            <Button variant="secondary" onClick={onOpen}>Try another PDF</Button>
          </div>
        ) : dims.length > 0 && (
          <div ref={stripRef}
            style={{ width: displayW, height: colH, margin: "0 auto", position: "relative", userSelect: "none" }}>
            {dims.map((d, i) => {
              // Each page keeps its OWN aspect (height = width × h/w). Round to whole
              // pixels so pages tile seamlessly into one continuous webtoon strip.
              const top = Math.round(geo.uoff[i] * displayW)
              const bot = Math.round((geo.uoff[i] + geo.uh[i]) * displayW)
              return (
                <div key={i} style={{ position: "absolute", top, left: 0, width: displayW, height: bot - top, background: "#fff" }}>
                  <PageCanvas doc={doc} pageNum={i + 1} scrollRef={scrollRef} />
                </div>
              )
            })}
            {/* Committed crops (multi mode): numbered by reading order, and still
                fully adjustable — drag the body to move, drag a handle to resize,
                ✕ to remove. They never disappear, so the whole chapter's cropping
                stays reviewable. Rank (number) is by vertical position. */}
            {multi && boxes.map((b, realIdx) => {
              const rank = boxes.filter((o) => o.y < b.y || (o.y === b.y && boxes.indexOf(o) <= realIdx)).length
              return (
                <div key={realIdx}
                  onMouseDown={(e) => begin(e, "move", e.currentTarget.parentElement, realIdx)}
                  style={{ position: "absolute", left: b.x * displayW, top: b.y * colH, width: b.w * displayW, height: b.h * colH,
                    border: "2px solid #4ea1ff", background: "rgba(78,161,255,0.10)", zIndex: 1, cursor: space ? "inherit" : "move" }}>
                  <span style={{ position: "absolute", top: -1, left: -1, background: "#4ea1ff", color: "#000", fontSize: 11, fontWeight: 700, padding: "1px 7px", borderRadius: "0 0 6px 0" }}>{rank}</span>
                  <button onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); setBoxes((list) => list.filter((_, k) => k !== realIdx)) }}
                    title="Remove this crop"
                    style={{ position: "absolute", top: 2, right: 2, width: 18, height: 18, borderRadius: "50%", background: "rgba(0,0,0,0.65)", color: "#fff", fontSize: 11, lineHeight: "18px" }}>✕</button>
                  {!space && HANDLES.map((h) => (
                    <div key={h} onMouseDown={(e) => begin(e, `resize-${h}`, e.currentTarget.parentElement.parentElement, realIdx)}
                      style={{
                        position: "absolute", width: 12, height: 12, background: "#4ea1ff", border: "1px solid #fff", borderRadius: 2, cursor: HCURSOR[h],
                        left: h.includes("w") ? -6 : h.includes("e") ? "calc(100% - 6px)" : "calc(50% - 6px)",
                        top: h.includes("n") ? -6 : h.includes("s") ? "calc(100% - 6px)" : "calc(50% - 6px)",
                      }} />
                  ))}
                </div>
              )
            })}
            {box && (
              <div onMouseDown={(e) => begin(e, "move", e.currentTarget.parentElement)}
                style={{ position: "absolute", left: box.x * displayW, top: box.y * colH, width: box.w * displayW, height: box.h * colH, border: `2px solid ${colors.accent}`, background: "rgba(255,107,53,0.12)", boxShadow: multi ? "none" : "0 0 0 9999px rgba(0,0,0,0.45)", cursor: space ? "inherit" : "move", zIndex: 2 }}>
                {!space && HANDLES.map((h) => (
                  <div key={h} onMouseDown={(e) => begin(e, `resize-${h}`, e.currentTarget.parentElement.parentElement)}
                    style={{
                      position: "absolute", width: 12, height: 12, background: colors.accent, border: "1px solid #fff", borderRadius: 2, cursor: HCURSOR[h],
                      left: h.includes("w") ? -6 : h.includes("e") ? "calc(100% - 6px)" : "calc(50% - 6px)",
                      top: h.includes("n") ? -6 : h.includes("s") ? "calc(100% - 6px)" : "calc(50% - 6px)",
                    }} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function IconBtn({ children, onClick }) {
  return (
    <button onClick={onClick} style={{ width: 28, height: 28, borderRadius: radius.sm, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.textDim, display: "flex", alignItems: "center", justifyContent: "center" }}>{children}</button>
  )
}

export default PdfReader
