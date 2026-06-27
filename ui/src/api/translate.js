/**
 * ui/src/api/translate.js — ManhwaStudio v2
 * Wrappers for translate config + regenerate-clearing (scripts/api/routers/translate.py).
 */

import { get, patch, post } from "./client"

/** @returns {Promise<{episode_id:number,total_panels:number,languages:object[],selected:string[]}>} */
export function getTranslateConfig(episodeId) {
  return get(`/translate/config/${episodeId}`)
}

/** Set which languages to translate into. */
export function setTranslateConfig(episodeId, selected) {
  return patch(`/translate/config/${episodeId}`, { selected })
}

/** Clear a translation so the next run regenerates it. Omit panelId for the whole language. */
export function clearTranslation(episodeId, lang, panelId) {
  const q = panelId != null ? `?lang=${lang}&panel_id=${panelId}` : `?lang=${lang}`
  return post(`/translate/clear/${episodeId}${q}`, {})
}
