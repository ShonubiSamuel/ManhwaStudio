/** Browser-safe local-file import for source PDFs. */
import { ApiError } from "./client"

const API = "http://127.0.0.1:8000/api"

export async function importPdf(file) {
  if (!file) throw new ApiError("Choose a PDF first")
  const res = await fetch(`${API}/media/import?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/pdf" },
    body: file,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(data?.detail || "Could not import the PDF", res.status)
  return data
}
