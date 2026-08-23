/** Standalone long-form TTS, backed by the exact same clip pipeline as dubbing. */
import { useEffect, useMemo, useRef, useState } from "react"
import { listVoices, renderTTS, quickTTSStatus } from "../api/voices"
import { useNotify } from "../store/notify"
import Button from "../components/Button"
import { colors, fonts, radius } from "../theme"

const API = "http://127.0.0.1:8000"

export default function TextToSpeech() {
  const { notify } = useNotify()
  const [voices, setVoices] = useState([])
  const [voice, setVoice] = useState("")
  const [text, setText] = useState("")
  const [job, setJob] = useState(null)
  const [audioUrl, setAudioUrl] = useState("")
  const audioRef = useRef(null)

  useEffect(() => {
    listVoices().then(rows => {
      setVoices(rows)
      if (rows.length) setVoice(rows[0].name)
    }).catch(e => notify({ severity: "error", message: e.message }))
  }, [notify])

  useEffect(() => {
    if (audioUrl) audioRef.current?.play().catch(() => {})
  }, [audioUrl])

  const selected = useMemo(() => voices.find(v => v.name === voice), [voices, voice])
  const working = job?.status === "running"
  const characterCount = text.length.toLocaleString()

  const render = async () => {
    if (!text.trim() || !voice) return
    setAudioUrl("")
    try {
      let current = await renderTTS(text, voice, selected?.language)
      setJob(current)
      while (current.status === "running") {
        await new Promise(resolve => setTimeout(resolve, 1000))
        current = await quickTTSStatus(current.job_id)
        setJob(current)
      }
      if (current.status === "failed") throw new Error(current.error || "Generation failed")
      const url = (current.audio_url || "").split("?")[0]
      setAudioUrl(`${API}${url}?v=${Date.now()}`)
      notify({ severity: "success", message: "Audio is ready" })
    } catch (e) {
      setJob({ status: "failed", error: e.message })
      notify({ severity: "error", message: e.message })
    }
  }

  return (
    <main style={{ flex: 1, overflowY: "auto", padding: "34px clamp(24px, 5vw, 72px)", color: colors.text }}>
      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        <div style={{ marginBottom: 28 }}>
          <div style={{ color: colors.accent, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.12em", marginBottom: 8 }}>STANDALONE AUDIO</div>
          <h1 style={{ margin: 0, fontSize: 28, letterSpacing: "-0.02em" }}>Text to Speech</h1>
          <p style={{ color: colors.textDim, maxWidth: 720, lineHeight: 1.6, marginBottom: 0 }}>
            Turn a full passage into one WAV file. Long text is split into clean, speakable sentences and assembled with the same warm-up, hiccup cleanup, onset trim, and level matching used by dubbing.
          </p>
        </div>

        <section style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 24 }}>
          <label style={labelStyle}>VOICE</label>
          <select value={voice} onChange={e => setVoice(e.target.value)} disabled={working || !voices.length} style={inputStyle}>
            {!voices.length && <option>Loading voices…</option>}
            {voices.map(v => <option key={v.name} value={v.name}>{v.name}{v.language ? ` — ${v.language}` : ""}</option>)}
          </select>
          {selected && <div style={{ color: colors.muted, fontSize: fonts.xs, marginTop: 7 }}>Uses {selected.mode || "voice"} · {selected.language || "language from profile"}</div>}

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "end", marginTop: 25 }}>
            <label style={{ ...labelStyle, margin: 0 }}>TEXT</label>
            <span style={{ color: colors.muted, fontSize: fonts.xs }}>{characterCount} characters</span>
          </div>
          <textarea value={text} onChange={e => setText(e.target.value)} disabled={working}
            placeholder="Paste or write as much narration as you need…"
            style={{ ...inputStyle, display: "block", minHeight: 260, resize: "vertical", lineHeight: 1.65, marginTop: 8, fontFamily: fonts.ui }} />

          <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 18, flexWrap: "wrap" }}>
            <Button variant="primary" onClick={render} disabled={working || !text.trim() || !voice} style={{ padding: "10px 18px" }}>
              {working ? "Generating audio…" : "Generate audio"}
            </Button>
            {working && <span style={{ color: colors.textDim, fontSize: fonts.sm }}>{job?.message || "Preparing synthesis…"}</span>}
            {job?.status === "failed" && <span style={{ color: colors.error, fontSize: fonts.sm }}>{job.error}</span>}
          </div>
        </section>

        {audioUrl && (
          <section style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 20, marginTop: 18 }}>
            <div style={{ color: colors.text, fontWeight: fonts.bold, marginBottom: 12 }}>Your audio</div>
            <audio ref={audioRef} controls src={audioUrl} style={{ width: "100%" }} />
            <div style={{ marginTop: 12 }}><Button variant="secondary" onClick={() => window.open(audioUrl, "_blank")}>Open / save WAV</Button></div>
          </section>
        )}
      </div>
    </main>
  )
}

const labelStyle = { display: "block", color: colors.textDim, fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.09em" }
const inputStyle = { width: "100%", boxSizing: "border-box", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "11px 12px", borderRadius: radius.md, fontSize: fonts.base, outline: "none" }
