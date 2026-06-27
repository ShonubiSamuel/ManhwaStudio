/**
 * ui/src/api/voices.js — ManhwaStudio v2
 * Voice-profile CRUD + Quick TTS (scripts/api/routers/voices.py + dubbing.py).
 */

import { get, post, patch, del } from "./client"

/** @returns {Promise<{name,mode,language,model,speaker}[]>} all voice profiles */
export function listVoices() {
  return get("/voices")
}

/** @returns {Promise<object>} full profile */
export function getVoice(name) {
  return get(`/voices/${encodeURIComponent(name)}`)
}

export function createVoice(body) {
  return post("/voices", body)
}

export function updateVoice(name, body) {
  return patch(`/voices/${encodeURIComponent(name)}`, body)
}

export function deleteVoice(name) {
  return del(`/voices/${encodeURIComponent(name)}`)
}

/** Attach a reference clip (local file path) to a voice + auto-transcribe. */
export function setVoiceReference(name, sourcePath, transcribe = true) {
  return post(`/voices/${encodeURIComponent(name)}/reference`, { source_path: sourcePath, transcribe })
}

/** Convert + transcribe a clip WITHOUT creating a voice. @returns {staged_path, transcript} */
export function stageReference(sourcePath, transcribe = true) {
  return post("/voices/stage-reference", { source_path: sourcePath, transcribe })
}

/** Selectable languages: [{ code, name, engine }]. */
export function getLanguages() {
  return get("/languages")
}

/** Start a Quick-TTS sample. @returns {Promise<{job_id,status,...}>}
 *  When projectId is given, the audio is written into that project's dub folder
 *  (under lang_code) instead of the scratch tts_samples folder. */
export function quickTTS(text, voice, language, projectId = null, langCode = null) {
  return post("/tts/quick", { text, voice, language: language || null, project_id: projectId, lang_code: langCode })
}

/** Poll any TTS job (quick / design / ad-hoc). */
export function quickTTSStatus(jobId) {
  return get(`/tts/quick/${jobId}`)
}

/** Design a reference clip from a text persona (Qwen3 VoiceDesign). @returns job */
export function designVoice(instruct, text, language) {
  return post("/voices/design", { instruct, text, language: language || "English" })
}

/** Dub a free-form multi-line script with one voice (active engine). @returns job */
export function dubAdhoc(text, voice, language) {
  return post("/tts/dub-adhoc", { text, voice, language: language || null })
}
