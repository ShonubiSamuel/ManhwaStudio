/**
 * ui/src/api/episodes.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Typed wrappers for every episode endpoint.
 *
 * Mirrors scripts/api/routers/episodes.py endpoints 1-to-1.
 */

import { get, post, patch, del } from "./client"

/**
 * @typedef {Object} StageInfo
 * @property {string} status    "pending"|"running"|"done"|"failed"|"skipped"
 * @property {number} progress  0–100
 */

/**
 * @typedef {Object} Episode
 * @property {number}  id
 * @property {number}  project_id
 * @property {string}  title
 * @property {string}  source_type     "video"|"pdf"|"screenshots"
 * @property {string}  source_path
 * @property {string}  output_folder
 * @property {string}  tone_prompt
 * @property {Object.<string, StageInfo>} stages
 * @property {number}  overall         0–100 average progress
 * @property {number}  total_panels
 * @property {number|null} duration_secs
 * @property {number|null} total_pages
 * @property {string}  error_message
 * @property {number}  created_at
 * @property {number}  updated_at
 */

/**
 * Return all episodes for a given project, newest first.
 * @param   {number} projectId
 * @returns {Promise<Episode[]>}
 */
export function listEpisodes(projectId) {
  return get(`/episodes?project_id=${projectId}`)
}

/**
 * Return a single episode with its full stage map and overall progress.
 * The Pipeline page polls this to update progress bars.
 * @param   {number} id
 * @returns {Promise<Episode>}
 */
export function getEpisode(id) {
  return get(`/episodes/${id}`)
}

/**
 * Import a new episode into a project.
 * @param {{
 *   project_id:  number,
 *   title:       string,
 *   source_type: "video"|"pdf"|"screenshots",
 *   source_path: string,
 *   tone_prompt?: string,
 * }} data
 * @returns {Promise<Episode>}
 */
export function createEpisode(data) {
  return post("/episodes", data)
}

/**
 * Update an episode's title and/or tone prompt.
 * @param   {number} id
 * @param   {{ title?: string, tone_prompt?: string }} updates
 * @returns {Promise<Episode>}
 */
export function updateEpisode(id, updates) {
  return patch(`/episodes/${id}`, updates)
}

/**
 * Delete an episode and all its associated data (panels, audio, logs).
 * Disk output files are NOT deleted.
 * @param   {number} id
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function deleteEpisode(id) {
  return del(`/episodes/${id}`)
}
