/**
 * ui/src/api/dubbing.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Wrappers for the dubbing-config + voices endpoints.
 * Mirrors scripts/api/routers/dubbing.py.
 *
 *   GET   /api/voices                  → VoiceInfo[]
 *   GET   /api/dub/config/{episodeId}  → DubConfig
 *   PATCH /api/dub/config/{episodeId}  → DubConfig
 */

import { get, patch, post } from "./client"

/**
 * @typedef {Object} VoiceInfo
 * @property {string} name
 * @property {string} mode
 * @property {string} language
 * @property {string} model
 * @property {string} speaker
 *
 * @typedef {Object} DubLangOption
 * @property {string}  code
 * @property {string}  name
 * @property {boolean} has_translation
 *
 * @typedef {Object} DubConfig
 * @property {number} episode_id
 * @property {DubLangOption[]} languages
 * @property {string[]} enabled_langs
 * @property {Object.<string,string>} profiles
 * @property {Object.<string,string>} suggested
 * @property {string[]} voices
 * @property {number} batch_size
 */

/** @returns {Promise<VoiceInfo[]>} */
export function getVoices() {
  return get("/voices")
}

/**
 * @param   {number} episodeId
 * @returns {Promise<DubConfig>}
 */
export function getDubConfig(episodeId) {
  return get(`/dub/config/${episodeId}`)
}

/**
 * @param   {number} episodeId
 * @param   {{enabled_langs?: string[], profiles?: Object.<string,string>, batch_size?: number}} patchBody
 * @returns {Promise<DubConfig>}
 */
export function updateDubConfig(episodeId, patchBody) {
  return patch(`/dub/config/${episodeId}`, patchBody)
}

/** Clear generated audio so the next dub run regenerates it. Omit panelId for the whole language. */
export function clearDubAudio(episodeId, lang, panelId) {
  const q = panelId != null ? `?lang=${lang}&panel_id=${panelId}` : `?lang=${lang}`
  return post(`/dub/clear/${episodeId}${q}`, {})
}

/**
 * Generated dub batches for a language — each an independently playable +
 * regenerable unit.
 * @returns {Promise<{episode_id:number,lang:string,lang_name:string,voice:string,batch_size:number,batches:{idx:number,panel_from:number,panel_to:number,panels:number[],status:string,duration:number,audio_url:string}[]}>}
 */
export function getDubBatches(episodeId, lang) {
  return get(`/dub/batches/${episodeId}?lang=${lang}`)
}

/** Regenerate one dub batch in the background (202 — watch via the pipeline SSE). */
export function regenerateDubBatch(episodeId, lang, batchIdx) {
  return post(`/dub/regenerate-batch/${episodeId}`, { lang, batch_idx: batchIdx })
}

/** Reset a language's dub state so the next dub run regenerates it from scratch. */
export function resetDubLanguage(episodeId, lang) {
  return post(`/dub/reset/${episodeId}?lang=${lang}`, {})
}

/**
 * Fix "rushed" panels: re-translate shorter → re-dub → re-sync, best of 3.
 * Pass panelIndices to fix specific panels, or null to fix all rushed ones.
 * 202 — watch progress via the pipeline SSE.
 */
export function fixDubPanels(episodeId, lang, panelIndices) {
  return post(`/dub/fix/${episodeId}`, { lang, panel_indices: panelIndices || null })
}
