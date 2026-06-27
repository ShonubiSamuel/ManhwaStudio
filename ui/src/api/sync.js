/**
 * ui/src/api/sync.js — ManhwaStudio v2
 * Wrapper for sync regenerate-clearing (scripts/api/routers/sync.py).
 */

import { get, post, patch } from "./client"

/** Which dubbed languages can be synced + which are selected. */
export function getSyncConfig(episodeId) {
  return get(`/sync/config/${episodeId}`)
}

/** Set which languages to sync. */
export function setSyncConfig(episodeId, selected) {
  return patch(`/sync/config/${episodeId}`, { selected })
}

/** Clear synced clip(s) so the next sync run regenerates them. Omit panelId for the whole language. */
export function clearSync(episodeId, lang, panelId) {
  const q = panelId != null ? `?lang=${lang}&panel_id=${panelId}` : `?lang=${lang}`
  return post(`/sync/clear/${episodeId}${q}`, {})
}

/**
 * Per-batch sync state for a language + the whole-language ("full audio") track.
 * @returns {Promise<{episode_id:number,lang:string,lang_name:string,is_reference:boolean,full_audio_url:string,full_is_synced:boolean,batches:{idx:number,panel_from:number,panel_to:number,synced:number,total:number,stretch_pct:number|null,status:string,synced_url:string}[]}>}
 */
export function getSyncBatches(episodeId, lang) {
  return get(`/sync/batches/${episodeId}?lang=${lang}`)
}

/** Clear sync for a panel range so the next sync run redoes just those panels. */
export function clearSyncRange(episodeId, lang, panelFrom, panelTo) {
  return post(`/sync/clear-range/${episodeId}`, { lang, panel_from: panelFrom, panel_to: panelTo })
}
