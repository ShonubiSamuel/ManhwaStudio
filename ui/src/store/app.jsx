/**
 * ui/src/store/app.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Global application state using React Context + useReducer.
 *
 * No external library needed — this covers everything the UI needs to share
 * across components:  which page is showing, which project/episode is active,
 * and whether a pipeline stage is currently running.
 *
 * Usage
 * ─────
 *   // Wrap the app once (in main.jsx):
 *   <AppProvider><App /></AppProvider>
 *
 *   // Read and update state anywhere:
 *   const { state, dispatch } = useApp()
 *   dispatch(actions.setPage("pipeline"))
 *   dispatch(actions.setEpisode(episode))
 */

import { createContext, useContext, useReducer } from "react"

// ── Pages ─────────────────────────────────────────────────────────────────────

export const PAGES = {
  // ── AI Voice app sections (current) ──────────────────────────────────────
  VOICEOVER:     "voiceover",     // the main, fully-built section
  VIDEO_REFINE:  "video_refine",  // manga recaps — crop panels, refine, render
  TRANSCRIPTION: "transcription", // coming soon
  SUBTITLE:      "subtitle",      // coming soon
  REALTIME:      "realtime",      // coming soon
  VOICES:        "voices",        // voice library
  SETTINGS:      "settings",
  LOGS:          "logs",

  // ── Legacy manhwa pages (retired from nav; kept so old files still compile)
  LIBRARY:    "library",
  PIPELINE:   "pipeline",
  DUB_STUDIO: "dub_studio",
  DUBBING:    "dubbing",
}

// ── Initial state ─────────────────────────────────────────────────────────────

const initialState = {
  /** Which top-level page is currently visible. */
  page: PAGES.VOICEOVER,

  /** The project whose episodes are shown in the Library sidebar. */
  activeProjectId: null,

  /**
   * The episode currently loaded into the Pipeline view.
   * Storing the full object avoids an extra fetch every time the
   * Pipeline page re-renders.
   */
  activeEpisode: null,

  /**
   * True while a pipeline stage is running in the background.
   * Used by the Pipeline page to disable stage buttons and show a spinner.
   */
  stageRunning: false,

  /**
   * Lightweight notification shown at the bottom of the screen.
   * { message: string, level: "success"|"error"|"info"|"warning" } | null
   */
  toast: null,
}

// ── Reducer ───────────────────────────────────────────────────────────────────

function reducer(state, action) {
  switch (action.type) {

    case "SET_PAGE":
      return { ...state, page: action.page }

    case "SET_PROJECT":
      // Selecting a project clears the active episode — it belongs to a
      // different context.
      return {
        ...state,
        activeProjectId: action.projectId,
        activeEpisode:   null,
        stageRunning:    false,
      }

    case "SET_EPISODE":
      // Opening an episode always navigates to Pipeline.
      return {
        ...state,
        activeEpisode: action.episode,
        page:          PAGES.PIPELINE,
        stageRunning:  false,
      }

    case "UPDATE_EPISODE":
      // Merge fresh data into the active episode (e.g. after a stage completes).
      if (!state.activeEpisode || state.activeEpisode.id !== action.episode.id) {
        return state
      }
      return { ...state, activeEpisode: action.episode }

    case "CLEAR_EPISODE":
      return {
        ...state,
        activeEpisode: null,
        stageRunning:  false,
        page:          PAGES.LIBRARY,
      }

    case "SET_STAGE_RUNNING":
      return { ...state, stageRunning: action.running }

    case "SHOW_TOAST":
      return { ...state, toast: { message: action.message, level: action.level } }

    case "HIDE_TOAST":
      return { ...state, toast: null }

    default:
      return state
  }
}

// ── Context ───────────────────────────────────────────────────────────────────

const AppContext = createContext(null)

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  )
}

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error("useApp() must be called inside <AppProvider>")
  return ctx
}

// ── Action creators ───────────────────────────────────────────────────────────
// Clean, named functions so components never construct raw action objects.

export const actions = {
  /** Navigate to a page without changing any other state. */
  setPage: (page) =>
    ({ type: "SET_PAGE", page }),

  /** Select a project in the Library (clears active episode). */
  setProject: (projectId) =>
    ({ type: "SET_PROJECT", projectId }),

  /** Load an episode into the Pipeline view (navigates to Pipeline). */
  setEpisode: (episode) =>
    ({ type: "SET_EPISODE", episode }),

  /** Merge updated episode data after an API poll. */
  updateEpisode: (episode) =>
    ({ type: "UPDATE_EPISODE", episode }),

  /** Deselect the active episode and return to Library. */
  clearEpisode: () =>
    ({ type: "CLEAR_EPISODE" }),

  /** Set whether a stage is currently running (disables stage buttons). */
  setStageRunning: (running) =>
    ({ type: "SET_STAGE_RUNNING", running }),

  /** Show a brief toast notification. level: "success"|"error"|"info"|"warning" */
  showToast: (message, level = "info") =>
    ({ type: "SHOW_TOAST", message, level }),

  /** Hide the current toast. */
  hideToast: () =>
    ({ type: "HIDE_TOAST" }),
}
