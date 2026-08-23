/**
 * ui/src/api/settings.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Typed wrappers for the settings endpoints.
 *
 * Mirrors scripts/api/routers/settings.py 1-to-1.
 */

import { get, patch } from "./client"

/**
 * @typedef {Object} SettingsPayload
 * @property {Object.<string, any>}        values    every known setting key → resolved value
 * @property {Object.<string, string[]>}    sections  section name → list of keys in that section
 */

/**
 * Return every known setting with DB-saved values applied over config.py
 * defaults, grouped into sections for tabbed display.
 * @returns {Promise<SettingsPayload>}
 */
export function getSettings() {
  return get("/settings")
}

/**
 * Update one or more settings.
 * @param   {Object.<string, any>} updates  flat { key: value } — only these keys change
 * @returns {Promise<SettingsPayload>}       the full refreshed settings payload
 */
export function updateSettings(updates) {
  return patch("/settings", updates)
}

/**
 * Curated live model list for a provider + task.
 * @param {"nvidia"|"groq"} provider
 * @param {"translate"|"refine"|"vision"} task  only models that FIT the task
 * @returns {Promise<{models: string[], cached: boolean}>}
 */
export function listProviderModels(provider, task = "refine") {
  return get(`/settings/models/${encodeURIComponent(provider)}?task=${encodeURIComponent(task)}`)
}
