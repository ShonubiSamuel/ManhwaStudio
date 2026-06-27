/**
 * ui/src/api/client.js — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Base HTTP client used by every API module.
 *
 * Responsibilities
 * ────────────────
 *   • Sets the base URL so every other module uses a plain path ("/projects")
 *   • Attaches Content-Type header on requests with a body
 *   • Parses FastAPI error responses ({ detail: "..." }) into readable messages
 *   • Throws a typed ApiError so callers can distinguish network failures from
 *     application-level errors (404 / 400 / 500 etc.)
 *
 * Usage
 * ─────
 *   import { get, post, patch, del } from "./client"
 *
 *   const projects = await get("/projects")
 *   const created  = await post("/projects", { title: "My Project" })
 *   await del("/projects/5")
 */

// ── Base URL ──────────────────────────────────────────────────────────────────
// Points at the FastAPI server.  Change this one constant for staging / prod.

const BASE = "http://127.0.0.1:8000/api"


// ── ApiError ──────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  /**
   * @param {string} message   Human-readable description
   * @param {number} status    HTTP status code (0 for network failures)
   */
  constructor(message, status = 0) {
    super(message)
    this.name   = "ApiError"
    this.status = status
  }
}


// ── Core request helper ───────────────────────────────────────────────────────

/**
 * Send an HTTP request to the FastAPI backend and return parsed JSON.
 *
 * @param {"GET"|"POST"|"PATCH"|"DELETE"} method
 * @param {string}  path   Relative path, e.g. "/projects" or "/episodes/5"
 * @param {object}  [body] Request body (serialised to JSON automatically)
 * @returns {Promise<any>} Parsed response body, or null for 204 No Content
 * @throws {ApiError}
 */
async function request(method, path, body = null) {
  const headers = {}
  let   bodyStr = undefined

  if (body !== null) {
    headers["Content-Type"] = "application/json"
    bodyStr = JSON.stringify(body)
  }

  let res
  try {
    res = await fetch(`${BASE}${path}`, { method, headers, body: bodyStr })
  } catch (networkErr) {
    // fetch() itself throws only on genuine network failures (server down,
    // DNS failure, etc.) — not on non-2xx HTTP responses.
    throw new ApiError(
      `Cannot reach ManhwaStudio API — is app.py running?  (${networkErr.message})`,
      0,
    )
  }

  // 204 No Content — successful but no body to parse
  if (res.status === 204) return null

  // Try to parse the body regardless of status — FastAPI always returns JSON
  let data
  try {
    data = await res.json()
  } catch {
    throw new ApiError(`Server returned non-JSON response (HTTP ${res.status})`, res.status)
  }

  if (!res.ok) {
    // FastAPI wraps validation and HTTPException messages in { detail: "..." }
    const message =
      (typeof data?.detail === "string"  ? data.detail  : null) ||
      (typeof data?.message === "string" ? data.message : null) ||
      `Request failed (HTTP ${res.status})`
    throw new ApiError(message, res.status)
  }

  return data
}


// ── Exported verbs ────────────────────────────────────────────────────────────

/** GET  /api{path} */
export const get   = (path)        => request("GET",    path)

/** POST /api{path} with JSON body */
export const post  = (path, body)  => request("POST",   path, body)

/** PATCH /api{path} with JSON body */
export const patch = (path, body)  => request("PATCH",  path, body)

/** PUT  /api{path} with JSON body */
export const put   = (path, body)  => request("PUT",    path, body)

/** DELETE /api{path} */
export const del   = (path)        => request("DELETE", path)
