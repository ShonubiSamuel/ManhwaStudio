/**
 * ui/src/components/Sidebar.jsx — ManhwaStudio v2
 *
 * Left navigation panel.  Shows the app logo, page nav items, and a footer
 * badge when an episode is loaded into the Pipeline.  Collapsible to a narrow
 * icon-only rail (like the Pipeline stages rail); the choice is persisted.
 */

import { useState } from "react"
import { useApp, actions, PAGES } from "../store/app"
import { useNotify } from "../store/notify"
import { useResizableRail, RailDragHandle } from "./Rail"
import { colors, fonts, radius } from "../theme"

const NAV = [
  { page: PAGES.LIBRARY,    label: "Library",    icon: "⊞" },
  { page: PAGES.PIPELINE,   label: "Pipeline",   icon: "▶" },
  { page: PAGES.DUB_STUDIO, label: "Dub Studio", icon: "🎬" },
  { page: PAGES.DUBBING,    label: "Dubbing",    icon: "♪" },
  { page: PAGES.SETTINGS,   label: "Settings",   icon: "⚙" },
  { page: PAGES.LOGS,       label: "Logs",       icon: "≡" },
]

export default function Sidebar() {
  const { state, dispatch } = useApp()
  const { unreadIssues, clearUnread } = useNotify()
  const rail = useResizableRail({ storageKey: "ms_nav", defaultWidth: 220, min: 170, max: 320 })
  const collapsed = rail.collapsed
  const toggle = rail.toggle

  const handleNav = (page) => {
    if (page === PAGES.LOGS) clearUnread()
    dispatch(actions.setPage(page))
  }

  return (
    <aside style={{
      width:        rail.width,
      minWidth:     rail.width,
      height:       "100vh",
      background:   colors.panel,
      borderRight:  `1px solid ${colors.border}`,
      display:      "flex",
      flexDirection:"column",
      userSelect:   "none",
      flexShrink:   0,
      position:     "relative",
    }}>

      {/* ── Logo + collapse toggle ───────────────────────────────────── */}
      {collapsed ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "16px 0 12px", gap: 10 }}>
          <div style={{ color: colors.accent, fontSize: "14px", fontWeight: fonts.bold, letterSpacing: "0.06em" }}>MS</div>
          <IconBtn title="Expand sidebar" onClick={toggle}>»</IconBtn>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", padding: "22px 14px 18px 20px" }}>
          <div>
            <div style={{ color: colors.accent, fontSize: "15px", fontWeight: fonts.bold, letterSpacing: "0.12em", lineHeight: 1.2 }}>MANHWA</div>
            <div style={{ color: colors.text, fontSize: "15px", fontWeight: fonts.normal, letterSpacing: "0.12em", lineHeight: 1.2 }}>STUDIO</div>
            <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 5 }}>v2.0  ·  new architecture</div>
          </div>
          <IconBtn title="Collapse sidebar" onClick={toggle}>«</IconBtn>
        </div>
      )}

      <Divider collapsed={collapsed} />

      {/* ── Navigation ───────────────────────────────────────────────── */}
      <nav style={{ flex: 1, padding: collapsed ? "10px 6px" : "10px 10px" }}>
        {!collapsed && <SectionLabel>NAVIGATION</SectionLabel>}
        {NAV.map(({ page, label, icon }) => (
          <NavItem
            key={page}
            label={label}
            icon={icon}
            collapsed={collapsed}
            active={state.page === page}
            badge={page === PAGES.LOGS ? unreadIssues : 0}
            onClick={() => handleNav(page)}
          />
        ))}
      </nav>

      {/* ── Active episode badge ──────────────────────────────────────── */}
      {state.activeEpisode && (
        <>
          <Divider collapsed={collapsed} />
          {collapsed ? (
            <button
              onClick={() => dispatch(actions.setPage(PAGES.PIPELINE))}
              title={`${state.activeEpisode.title} · ${state.activeEpisode.overall}%`}
              style={{ display: "flex", justifyContent: "center", padding: "14px 0", background: "none", border: "none", cursor: "pointer" }}
            >
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: colors.accent }} />
            </button>
          ) : (
            <div onClick={() => dispatch(actions.setPage(PAGES.PIPELINE))} style={{ padding: "14px 20px", cursor: "pointer" }}>
              <SectionLabel>ACTIVE EPISODE</SectionLabel>
              <div style={{ color: colors.accent, fontSize: fonts.sm, fontWeight: fonts.medium, marginTop: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {state.activeEpisode.title}
              </div>
              <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 3 }}>
                {state.activeEpisode.source_type.toUpperCase()}{" · "}{state.activeEpisode.overall}% complete
              </div>
            </div>
          )}
        </>
      )}

      {!collapsed && <RailDragHandle onMouseDown={rail.onDragStart} />}
    </aside>
  )
}


// ── Sub-components ────────────────────────────────────────────────────────────

function IconBtn({ children, title, onClick }) {
  return (
    <button onClick={onClick} title={title} aria-label={title}
      style={{ background: "none", border: "none", color: colors.muted, cursor: "pointer", fontSize: 18, lineHeight: 1, padding: 2 }}>
      {children}
    </button>
  )
}

function NavItem({ label, icon, active, onClick, badge = 0, collapsed }) {
  const [hovered, setHovered] = useState(false)

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={collapsed ? label : ""}
      style={{
        position:     "relative",
        display:      "flex",
        alignItems:   "center",
        justifyContent: collapsed ? "center" : "flex-start",
        gap:          collapsed ? 0 : "10px",
        width:        "100%",
        padding:      collapsed ? "9px 0" : "8px 10px",
        margin:       collapsed ? "3px 0" : "0",
        borderRadius: radius.sm,
        background:   active
          ? `rgba(255,107,53,0.12)`
          : hovered
          ? `rgba(255,255,255,0.04)`
          : "transparent",
        color:      active ? colors.accent : hovered ? colors.text : colors.textDim,
        fontSize:   fonts.base,
        fontWeight: active ? fonts.medium : fonts.normal,
        border:     "none",
        cursor:     "pointer",
        textAlign:  "left",
        transition: "background 0.12s, color 0.12s",
      }}
    >
      {!collapsed && (
        <span style={{ width: "3px", height: "14px", background: active ? colors.accent : "transparent", borderRadius: "2px", flexShrink: 0, transition: "background 0.12s" }} />
      )}
      <span style={{ fontSize: collapsed ? "14px" : "11px", opacity: collapsed ? 1 : 0.7 }}>{icon}</span>
      {!collapsed && <span style={{ flex: 1 }}>{label}</span>}
      {badge > 0 && (
        <span style={{
          background: colors.error, color: "#2a1717", fontSize: "10px",
          fontWeight: fonts.medium, borderRadius: radius.full,
          minWidth: 16, height: 16, padding: "0 5px",
          display: "flex", alignItems: "center", justifyContent: "center",
          position: collapsed ? "absolute" : "static", top: collapsed ? 4 : "auto", right: collapsed ? 6 : "auto",
        }}>{badge > 99 ? "99+" : badge}</span>
      )}
    </button>
  )
}

function Divider({ collapsed }) {
  return <div style={{ height: "1px", background: colors.border, margin: collapsed ? "0 10px" : "0 16px" }} />
}

function SectionLabel({ children }) {
  return (
    <div style={{ color: colors.muted, fontSize: "10px", fontWeight: fonts.bold, letterSpacing: "0.1em", padding: "4px 10px 6px" }}>
      {children}
    </div>
  )
}
