/**
 * ui/src/api/pipeline.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Typed wrappers for every pipeline endpoint, plus a connectEvents() helper
 * that wraps the native EventSource API for the SSE log/progress stream.
 *
 * Mirrors scripts/api/routers/pipeline.py endpoints 1-to-1.
 */

import { get, post } from "./client"

const BASE = "http://127.0.0.1:8000/api"

/**
 * @typedef {Object} Panel
 * @property {number} id
 * @property {number} episode_id
 * @property {number} panel_index
 * @property {string} transcript_text
 * @property {string} narration_text
 * @property {string} image_path
 * @property {number} updated_at
 */

/**
 * Start a pipeline stage for an episode.
 * Returns immediately (202 Accepted) — connect to connectEvents() right
 * after calling this to receive live progress.
 *
 * @param   {number} episodeId
 * @param   {string} stage   "detect"|"video_refine"|"pdf_slice"|"pdf_narrate"|
 *                            "upscale"|"translate"|"dub"|"sync"|"assemble"
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function runStage(episodeId, stage) {
  return post("/pipeline/run", { episode_id: episodeId, stage })
}

/**
 * Request an abort of the currently running stage for an episode.
 * @param   {number} episodeId
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function stopStage(episodeId) {
  return post(`/pipeline/stop/${episodeId}`, {})
}

/**
 * Return all panels for an episode, ordered by panel_index.
 * @param   {number} episodeId
 * @returns {Promise<Panel[]>}
 */
export function listPanels(episodeId) {
  return get(`/pipeline/panels/${episodeId}`)
}

/**
 * Return fresh episode data (stage statuses + overall progress).
 * Used to refresh the stage grid after a stage_done event.
 * @param   {number} episodeId
 * @returns {Promise<import("./episodes").Episode>}
 */
export function getEpisodeStatus(episodeId) {
  return get(`/pipeline/episode/${episodeId}`)
}

/**
 * Check whether a stage is currently running for an episode.
 * Useful on page load to resume watching an in-progress run.
 * @param   {number} episodeId
 * @returns {Promise<{ running: boolean, episode_id: number }>}
 */
export function isRunning(episodeId) {
  return get(`/pipeline/running/${episodeId}`)
}

// ── Orchestrator (auto-chain to review / resume / plan) ───────────────────────

/**
 * Auto "Run to review" — run pre-checkpoint stages, stop at the review gate.
 * Returns 202; watch progress via connectEvents(). The terminal stage_done
 * carries { checkpoint: true } on success.
 * @param   {number} episodeId
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function runAuto(episodeId) {
  return post(`/pipeline/auto/${episodeId}`, {})
}

/**
 * "Approve & finish" — run post-checkpoint stages (translate → dub →
 * sync(video) → assemble). 409 if narration is not complete yet.
 * @param   {number} episodeId
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function resume(episodeId) {
  return post(`/pipeline/resume/${episodeId}`, {})
}

/**
 * @typedef {Object} PlanStage
 * @property {string} key
 * @property {string} label
 * @property {string} db_column
 * @property {"pre"|"post"} phase
 * @property {string} status
 * @property {number} progress
 *
 * @typedef {Object} Plan
 * @property {number} episode_id
 * @property {string} source_type
 * @property {PlanStage[]} stages
 * @property {number} checkpoint_index
 * @property {boolean} narration_ready
 * @property {string[]} auto_remaining
 * @property {string[]} resume_remaining
 */

/**
 * Ordered stage plan + per-stage status + checkpoint position for an episode.
 * @param   {number} episodeId
 * @returns {Promise<Plan>}
 */
export function getPlan(episodeId) {
  return get(`/pipeline/plan/${episodeId}`)
}

/**
 * Open an SSE connection to the live event stream for an episode.
 *
 * The native EventSource API closes itself when the server ends the
 * response (after a "stage_done" event), but onClose is still called
 * explicitly so callers can clean up UI state without inspecting event
 * payloads.
 *
 * @param {number}   episodeId
 * @param {Object}   handlers
 * @param {function(string, string): void}          handlers.onLog          (message, level)
 * @param {function(number, string): void}          handlers.onProgress     (pct, message)
 * @param {function(string, boolean): void}         [handlers.onStageAdvance] (stage, success) — non-terminal, multi-stage chains
 * @param {function(string, boolean, object): void} handlers.onStageDone    (stage, success, event) — terminal; event may carry { checkpoint, chain }
 * @param {function(): void}                         [handlers.onClose]
 * @param {function(Event): void}                    [handlers.onError]
 * @returns {EventSource} — call .close() to disconnect manually
 */
export function connectEvents(episodeId, { onLog, onProgress, onStageAdvance, onStageDone, onClose, onError }) {
  const es = new EventSource(`${BASE}/pipeline/events/${episodeId}`)

  es.onmessage = (e) => {
    let event
    try {
      event = JSON.parse(e.data)
    } catch {
      return   // ignore malformed payloads (e.g. keepalive comments are not 'message' events)
    }

    switch (event.type) {
      case "log":
        onLog?.(event.message, event.level)
        break
      case "progress":
        onProgress?.(event.pct, event.message)
        break
      case "stage_advance":
        onStageAdvance?.(event.stage, event.success)
        break
      case "stage_done":
        onStageDone?.(event.stage, event.success, event)
        es.close()
        onClose?.()
        break
      default:
        break
    }
  }

  es.onerror = (e) => {
    onError?.(e)
    // EventSource auto-retries on transient errors; if the server has
    // genuinely gone away, repeated failures will keep firing this.
    // Callers can decide whether to es.close() based on their own state.
  }

  return es
}
