/**
 * ui/src/api/videoRefine.js — typed wrappers for the Video Refine backend
 * (scripts/api/routers/video_refine.py). Separate from the audio/dub system.
 */

import { get, post, put } from "./client"

/** List projects of one kind: "video_refine" (default) or "recap". */
export function listRefineProjects(kind = "video_refine") {
  return get(`/video-refine/projects?kind=${encodeURIComponent(kind)}`)
}

/** Create a Video Refine or Recap project (tagged so sections never mix). */
export function createRefineProject(name, sourcePath = "", kind = "video_refine") {
  return post("/video-refine/projects", { name, source_path: sourcePath, kind })
}

/** Narrate ONE batch of cropped panels via the stateful storytelling pipeline.
 *  images: [{index, data(dataURL)}] · returns {lines:[{index,text}], registry, memory} */
export function narratePanels(projectId, prompt, images, resetMemory = false) {
  return post("/video-refine/narrate", { project_id: projectId, prompt, images, reset_memory: resetMemory })
}

/** How narration will batch given the current model settings.
 *  @returns {effective_batch, vision_model, reasoner_model, ...} */
export function getNarratePlan() {
  return get("/video-refine/narrate-plan")
}

/** The living character registry + rolling story memory + cast seed. */
export function getRecapState(projectId) {
  return get(`/video-refine/recap-state/${projectId}`)
}

/** Persist user edits to the registry / cast seed / memory. */
export function saveRecapState(projectId, state) {
  return put(`/video-refine/recap-state/${projectId}`, state)
}

/** Render a PDF to page images for the crop tool. @returns {count, pages:[{index,url,w,h}]} */
export function renderPdf(projectId, pdfPath, dpi = 200) {
  return post("/video-refine/pdf", { project_id: projectId, pdf_path: pdfPath, dpi })
}

/** Crop a panel from a page (box normalised 0..1) and upscale it.
 *  @returns {image, raw, upscaled} */
export function cropPanel(projectId, page, box, cueIndex, upscale = true) {
  return post("/video-refine/crop", { project_id: projectId, page, box, cue_index: cueIndex, upscale })
}

/** Save a panel cropped in the browser (PDF.js). data = PNG data-URL. */
export function savePanel(projectId, cueIndex, data) {
  return post("/video-refine/save-panel", { project_id: projectId, cue_index: cueIndex, data })
}

/** Delete a cue's panel files (raw + upscaled) from disk. */
export function deletePanel(projectId, cueIndex) {
  return post("/video-refine/delete-panel", { project_id: projectId, cue_index: cueIndex })
}

/** Upscale every cropped panel that isn't upscaled yet (background batch job). */
export function upscaleAll(projectId) {
  return post("/video-refine/upscale-all", { project_id: projectId })
}

/** Poll the batch-upscale progress. @returns {total, done, running} */
export function getUpscaleStatus(projectId) {
  return get(`/video-refine/upscale-status/${projectId}`)
}

/** Grab a low-quality reference frame from the source video at a time (s). */
export function grabFrame(projectId, sourcePath, time, cueIndex) {
  return post("/video-refine/frame", { project_id: projectId, source_path: sourcePath, time, cue_index: cueIndex })
}

/** Load a project's Video Refine session ({} if none). */
export function getRefineSession(projectId) {
  return get(`/video-refine/session/${projectId}`)
}

/** Persist a project's Video Refine session. */
export function saveRefineSession(projectId, session) {
  return put(`/video-refine/session/${projectId}`, session)
}
