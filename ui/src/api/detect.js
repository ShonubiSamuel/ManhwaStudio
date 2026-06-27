/**
 * ui/src/api/detect.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Wrappers for the Detect-stage tuning endpoints (scripts/api/routers/detect.py).
 *
 *   GET   /api/detect/config/{id}    DetectConfig
 *   PATCH /api/detect/config/{id}    DetectConfig
 *   POST  /api/detect/clip/{id}      DetectConfig   (extract test clip)
 *   POST  /api/detect/preview/{id}   { count, avg_duration, cuts[] }
 *   POST  /api/detect/tuner/{id}     { ok, message }
 */

import { get, patch, post } from "./client"

/**
 * @typedef {Object} DetectConfig
 * @property {number} episode_id
 * @property {string} mode
 * @property {string} priority
 * @property {number} silence_db
 * @property {number} min_silence_sec
 * @property {number} visual_threshold
 * @property {number} min_scene_sec
 * @property {number} frame_skip
 * @property {number} merge_window
 * @property {number} workers
 * @property {string} clip_start
 * @property {number} clip_duration
 * @property {boolean} confirmed
 * @property {boolean} clip_ready
 * @property {boolean} source_exists
 * @property {Object} defaults
 */

/** @returns {Promise<DetectConfig>} */
export function getDetectConfig(episodeId) {
  return get(`/detect/config/${episodeId}`)
}

/** @returns {Promise<DetectConfig>} */
export function saveDetectConfig(episodeId, body) {
  return patch(`/detect/config/${episodeId}`, body)
}

/** Extract a test clip. @returns {Promise<DetectConfig>} */
export function extractClip(episodeId, start, duration) {
  return post(`/detect/clip/${episodeId}`, { start, duration })
}

/** Preview cuts on the test clip. @returns {Promise<{count:number,avg_duration:number,cuts:object[]}>} */
export function runPreview(episodeId) {
  return post(`/detect/preview/${episodeId}`, {})
}

/** Launch the parameter tuner (opens in browser). @returns {Promise<{ok:boolean,message:string}>} */
export function openTuner(episodeId) {
  return post(`/detect/tuner/${episodeId}`, {})
}
