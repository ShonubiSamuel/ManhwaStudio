/**
 * ui/src/api/logs.js — ManhwaStudio v2
 * Wrappers for the Logs archive (scripts/api/routers/logs.py).
 */

import { get, del } from "./client"

/** @returns {Promise<object[]>} recent runs across all episodes, newest first */
export function getRecentLogs(limit = 200) {
  return get(`/logs?limit=${limit}`)
}

/** @returns {Promise<object[]>} full history for one episode */
export function getEpisodeLogs(episodeId) {
  return get(`/logs/${episodeId}`)
}

/** Clear logs (all, or one episode). */
export function clearLogs(episodeId) {
  return del(episodeId != null ? `/logs?episode_id=${episodeId}` : "/logs")
}
