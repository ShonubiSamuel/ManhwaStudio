/**
 * ui/src/App.jsx — ManhwaStudio v2
 * Root component.  Wraps everything in AppProvider and switches between
 * pages based on global state.  No URL router needed — this is a native
 * desktop window with no address bar.
 */

import { AppProvider, useApp, PAGES } from "./store/app"
import { NotificationsProvider, Toaster } from "./store/notify"
import { colors, fonts } from "./theme"
import Sidebar  from "./components/Sidebar"
import Library   from "./pages/Library"
import Pipeline  from "./pages/Pipeline"
import DubStudio from "./pages/DubStudio"
import Dubbing   from "./pages/Dubbing"
import Settings  from "./pages/Settings"
import Logs      from "./pages/Logs"

function PageRouter() {
  const { state } = useApp()

  switch (state.page) {
    case PAGES.LIBRARY:    return <Library />
    case PAGES.PIPELINE:   return <Pipeline />
    case PAGES.DUB_STUDIO: return <DubStudio />
    case PAGES.DUBBING:    return <Dubbing />
    case PAGES.SETTINGS: return <Settings />
    case PAGES.LOGS:     return <Logs />
    default:              return <Library />
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
