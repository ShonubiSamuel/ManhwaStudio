/**
 * ui/src/api/panels.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Typed wrappers for the Review-checkpoint panel endpoints.
 * Mirrors scripts/api/routers/panels.py.
 *
 *   GET   /api/panels/{episodeId}   → ReviewPanel[]
 *   PATCH /api/panels/{panelId}     → { panel, invalidated_langs, ... }
 */

import { get, patch } from "./client"

/** Base origin for building absolute /files/* image URLs. */
export const FILES_ORIGIN = "http://127.0.0.1:8000"

/**
 * @typedef {Object} PanelTranslation
 * @property {string}  lang_code
 * @property {string}  translated_text
 * @property {boolean} has_audio
 * @property {boolean} is_synced
 *
 * @typedef {Object} ReviewPanel
 * @property {number} id
 * @property {number} episode_id
 * @property {number} panel_index
 * @property {string} transcript_text
 * @property {string} narration_text
 * @property {string} narration_status
 * @property {string} image_path
 * @property {string} thumbnail_url        relative "/files/..." (empty if none)
 * @property {number|null} start_time_sec
 * @property {number|null} end_time_sec
 * @property {number|null} duration_sec
 * @property {Object.<string, PanelTranslation>} translations
 * @property {number} updated_at
 */

/**
 * Resolve a panel's thumbnail_url into a fully-qualified, loadable URL.
 * Returns "" when the panel has no servable image.
 * @param {ReviewPanel} panel
 * @returns {string}
 */
export function panelImageSrc(panel) {
  if (!panel?.thumbnail_url) return ""
  return `${FILES_ORIGIN}${panel.thumbnail_url}`
}

/**
 * Build a seekable URL for an arbitrary local source media file (video/audio).
 * The /files mount only serves OUTPUT_DIR, so source media (input/, etc.) is
 * streamed through GET /api/media instead. Returns "" for a falsy path.
 *
 * Accepts either an absolute disk path (the Dub Studio picker) or an existing
 * "/files/..." relative URL (speech-result outputs), which it passes through.
 * @param {string} path
 * @returns {string}
 */
export function mediaSrc(path) {
  if (!path) return ""
  if (path.startsWith("/files/")) return `${FILES_ORIGIN}${path}`
  if (/^https?:\/\//.test(path)) return path
  return `${FILES_ORIGIN}/api/media?path=${encodeURIComponent(path)}`
}

/**
 * Fetch every panel of an episode for the Review grid.
 * @param   {number} episodeId
 * @returns {Promise<ReviewPanel[]>}
 */
export function getPanels(episodeId) {
  return get(`/panels/${episodeId}`)
}

/**
 * Edit a panel's narration text.  Cascades: downstream translations + audio
 * are invalidated server-side and reflected in the returned panel.
 * @param   {number} panelId
 * @param   {string} narrationText
 * @returns {Promise<{panel: ReviewPanel, invalidated_langs: string[]}>}
 */
export function updateNarration(panelId, narrationText) {
  return patch(`/panels/${panelId}`, { narration_text: narrationText })
}

/**
 * Edit one language's translation for a panel.  Clears that language's stale
 * audio so just that clip regenerates.
 * @param   {number} panelId
 * @param   {string} langCode
 * @param   {string} translatedText
 * @returns {Promise<{panel: ReviewPanel, invalidated_langs: string[]}>}
 */
export function updateTranslation(panelId, langCode, translatedText) {
  return patch(`/panels/${panelId}`, {
    lang_code:       langCode,
    translated_text: translatedText,
  })
}
