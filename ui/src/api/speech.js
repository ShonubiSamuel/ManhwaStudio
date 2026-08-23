/**
 * ui/src/api/speech.js — speech-segment dubbing engine (scripts/api/routers/speech.py).
 */

import { get, post, put } from "./client"

/** Start a speech-segment dub for an episode (background). Watch progress via connectEvents(). */
export function startSpeechDub(episodeId, targetLangs, keepMusic = null) {
  return post(`/speech/dub/${episodeId}`, { target_langs: targetLangs, keep_music: keepMusic })
}

/** Everything the editor needs for a language: { cues, video_url, audio_url, exists }. */
export function getSpeechResult(episodeId, lang) {
  return get(`/speech/result/${episodeId}/${encodeURIComponent(lang)}`)
}

export function startAdhocTranslate(sourcePath, targetLang = "fr") {
  return post("/speech/adhoc-translate", { source_path: sourcePath, target_lang: targetLang })
}

export function getAdhocTranslateStatus(jobId) {
  return get(`/speech/adhoc-translate/${jobId}`)
}

/** Translate EXISTING cues to a language (no re-transcribe). Same job shape as
 *  adhoc-translate, so poll with getAdhocTranslateStatus. */
export function startTranslateCues(cues, langCode) {
  return post("/speech/translate-cues", { cues, lang_code: langCode })
}

export function startAdhocSync(audioUrl, cues, langCode = "fr", projectId = null, leadDummy = null) {
  return post("/speech/adhoc-sync", { audio_url: audioUrl, cues, lang_code: langCode, project_id: projectId, lead_dummy: leadDummy })
}

/** Per-cue dubbing: synthesize each line separately, place, master. One call →
 *  poll with getAdhocSyncStatus. Replaces the TTS-read + split flow. */
export function startDubCues(cues, voice, langCode = "French", projectId = null, repackTimings = false, group = true) {
  return post("/speech/dub-cues", { cues, voice, lang_code: langCode, project_id: projectId, repack_timings: repackTimings, group })
}

export function getAdhocSyncStatus(jobId) {
  return get(`/speech/adhoc-sync/${jobId}`)
}

/**
 * Re-translate ONE cue shorter to lower its CPS (the per-cue ✦AI button).
 * @returns {Promise<{translated:string, cps:number, rushed:boolean}>}
 */
export function refineCue({ text, translated, start, end, langCode = "fr" }) {
  return post("/speech/refine-cue", { text, translated, start, end, lang_code: langCode })
}

/** The Voiceover landing list — projects + session metadata. */
export function listVoiceoverProjects() {
  return get("/voiceover/projects")
}

/** Redub ONE cue (re-synthesize just that clip + reassemble the final track).
 *  Watch progress via getAdhocSyncStatus(job_id). @returns the started job. */
export function startRedubCue(projectId, voice, langCode, cues, index, group = true) {
  return post("/speech/redub-cue", { project_id: projectId, voice, lang_code: langCode, cues, index, group })
}

/** Batch redub: re-voice several cues, then reassemble the track ONCE.
 *  Watch progress via getAdhocSyncStatus(job_id). @returns the started job. */
export function startRedubCues(projectId, voice, langCode, cues, indices, group = true) {
  return post("/speech/redub-cues", { project_id: projectId, voice, lang_code: langCode, cues, indices, group })
}

/** Restore audio to a snapshot's clip versions (audio undo/redo): copies each
 *  cue's archived clip back and reassembles. `keys` is per-cue (null = skip).
 *  Watch progress via getAdhocSyncStatus(job_id). @returns the started job. */
export function startRestoreAudio(projectId, voice, langCode, cues, keys, group = true) {
  return post("/speech/restore-audio", { project_id: projectId, voice, lang_code: langCode, cues, keys, group })
}

/** AI Refine: rewrite the whole narration script at a level (brief|standard|detailed).
 *  @returns {Promise<{lines: string[]}>} refined lines, same count/order. */
export function refineScript(lines, { durations = [], level = "standard", instructions = "", lang = "French" } = {}) {
  return post("/speech/refine-script", { lines, durations, level, instructions, lang })
}

/** Load a project's saved Dub Studio session ({} if none yet). */
export function getDubSession(projectId) {
  return get(`/speech/dub-session/${projectId}`)
}

/** Persist a project's Dub Studio session to disk (project output folder). */
export function saveDubSession(projectId, session) {
  return put(`/speech/dub-session/${projectId}`, session)
}

/** Export the finished dub. fmt: "video" (mp4) | "audio" (mp3).
 *  @returns {Promise<{ok,url,path,filename}>} */
export function exportDub(projectId, { langCode = "French", fmt = "video", sourcePath = "" }) {
  return post(`/speech/export/${projectId}`, { lang_code: langCode, fmt, source_path: sourcePath })
}
