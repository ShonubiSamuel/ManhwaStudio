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
import ComingSoon from "./pages/ComingSoon"
import Settings   from "./pages/Settings"
import Logs       from "./pages/Logs"

function PageRouter() {
  const { state } = useApp()

  switch (state.page) {
    case PAGES.VOICEOVER:     return <Voiceover />
    case PAGES.TRANSCRIPTION: return <ComingSoon title="Transcription" icon="≣" desc="Upload audio or video and get an accurate, speaker-aware transcript you can edit and export (SRT / TXT)." />
    case PAGES.SUBTITLE:      return <ComingSoon title="Subtitle" icon="▭" desc="Generate, translate, and style subtitles, then export SRT / VTT or burn them into the video." />
    case PAGES.REALTIME:      return <ComingSoon title="Real-Time" icon="◉" desc="Live transcription and voice for meetings and streams." />
    case PAGES.VOICES:        return <ComingSoon title="Voices" icon="♪" desc="Browse the voice library by gender, language, and expression, favourite voices, and clone your own." />
    case PAGES.SETTINGS:      return <Settings />
    case PAGES.LOGS:          return <Logs />
    default:                  return <Voiceover />
  }
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
