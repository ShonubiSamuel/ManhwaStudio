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

export function startAdhocSync(audioUrl, cues, langCode = "fr", projectId = null, leadDummy = null) {
  return post("/speech/adhoc-sync", { audio_url: audioUrl, cues, lang_code: langCode, project_id: projectId, lead_dummy: leadDummy })
}

/** Per-cue dubbing: synthesize each line separately, place, master. One call →
 *  poll with getAdhocSyncStatus. Replaces the TTS-read + split flow. */
export function startDubCues(cues, voice, langCode = "French", projectId = null) {
  return post("/speech/dub-cues", { cues, voice, lang_code: langCode, project_id: projectId })
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
