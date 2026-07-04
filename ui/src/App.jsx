/**
 * ui/src/App.jsx — ManhwaStudio v2
 * Root component.  Wraps everything in AppProvider and switches between
 * pages based on global state.  No URL router needed — this is a native
 * desktop window with no address bar.
 */

import { AppProvider, useApp, PAGES } from "./store/app"
import { NotificationsProvider, Toaster } from "./store/notify"
import { colors } from "./theme"
import Sidebar    from "./components/Sidebar"
import Voiceover  from "./pages/Voiceover"
import VideoRefine from "./pages/VideoRefine"
import Voices     from "./pages/Voices"
import ComingSoon from "./pages/ComingSoon"
import Settings   from "./pages/Settings"
import Logs       from "./pages/Logs"

function PageRouter() {
  const { state } = useApp()
  const p = state.page

  // Render all pages but hide the inactive ones to preserve their state!
  return (
    <>
      <div style={{ display: p === PAGES.VOICEOVER ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <Voiceover />
      </div>
      <div style={{ display: p === PAGES.VIDEO_REFINE ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <VideoRefine />
      </div>
      <div style={{ display: p === PAGES.TRANSCRIPTION ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <ComingSoon title="Transcription" icon="≣" desc="Upload audio or video and get an accurate, speaker-aware transcript you can edit and export (SRT / TXT)." />
      </div>
      <div style={{ display: p === PAGES.SUBTITLE ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <ComingSoon title="Subtitle" icon="▭" desc="Generate, translate, and style subtitles, then export SRT / VTT or burn them into the video." />
      </div>
      <div style={{ display: p === PAGES.REALTIME ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <ComingSoon title="Real-Time" icon="◉" desc="Live transcription and voice for meetings and streams." />
      </div>
      <div style={{ display: p === PAGES.VOICES ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <Voices />
      </div>
      <div style={{ display: p === PAGES.SETTINGS ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <Settings />
      </div>
      <div style={{ display: p === PAGES.LOGS ? "flex" : "none", flex: 1, flexDirection: "column", overflow: "hidden" }}>
        <Logs />
      </div>
      {/* Fallback rendering for undefined pages defaults to Voiceover */}
      {(!Object.values(PAGES).includes(p)) && (
        <div style={{ display: "flex", flex: 1, flexDirection: "column", overflow: "hidden" }}>
          <Voiceover />
        </div>
      )}
    </>
  )
}

function AppShell() {
  return (
    <div style={{
      display:    "flex",
      height:     "100vh",
      overflow:   "hidden",
      background: colors.bg,
    }}>
      <Sidebar />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <PageRouter />
      </div>
      <Toaster />
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <NotificationsProvider>
        <AppShell />
      </NotificationsProvider>
    </AppProvider>
  )
}
