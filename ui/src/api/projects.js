/**
 * ui/src/api/projects.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Typed wrappers for every project endpoint.
 *
 * Components never call fetch() directly — they call these functions.
 * This keeps the URL structure in one place and makes the UI easy to test.
 *
 * Every function returns the parsed response object or throws ApiError.
 *
 * Mirrors scripts/api/routers/projects.py endpoints 1-to-1.
 */

import { get, post, patch, del } from "./client"

/**
 * @typedef {Object} Project
 * @property {number} id
 * @property {string} title
 * @property {string} notes
 * @property {string} cover_path
 * @property {number} episode_count
 * @property {number} created_at   — Unix timestamp
 * @property {number} updated_at   — Unix timestamp
 */

/**
 * Return every project with their episode counts.
 * @returns {Promise<Project[]>}
 */
export function listProjects() {
  return get("/projects")
}

/**
 * Return a single project by ID.
 * @param   {number} id
 * @returns {Promise<Project>}
 */
export function getProject(id) {
  return get(`/projects/${id}`)
}

/**
 * Create a new project.
 * @param   {string} title
 * @param   {string} [notes]
 * @returns {Promise<Project>}
 */
export function createProject(title, notes = "") {
  return post("/projects", { title, notes })
}

/**
 * Update a project's title and/or notes.
 * @param   {number} id
 * @param   {{ title?: string, notes?: string }} updates
 * @returns {Promise<Project>}
 */
export function updateProject(id, updates) {
  return patch(`/projects/${id}`, updates)
}

/**
 * Delete a project and all its episodes (cascade).
 * @param   {number} id
 * @returns {Promise<{ ok: boolean, message: string }>}
 */
export function deleteProject(id) {
  return del(`/projects/${id}`)
}
