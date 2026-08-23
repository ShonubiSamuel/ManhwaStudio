import { useState, useEffect, useMemo, useRef } from "react"
import { useNotify } from "../store/notify"
import { listVoices, getLanguages, createVoice, setVoiceReference, deleteVoice, updateVoice, quickTTS, quickTTSStatus } from "../api/voices"
import { colors, fonts, radius } from "../theme"
import Button from "../components/Button"
import Modal, { FormField, TextInput } from "../components/Modal"

export default function Voices() {
  const { notify } = useNotify()
  const [voices, setVoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState("")
  const [filterLang, setFilterLang] = useState("All")
  
  const [showClone, setShowClone] = useState(false)
  const [selectedVoice, setSelectedVoice] = useState(null)
  const [confirmDel, setConfirmDel] = useState(null)
  
  const [languages, setLanguages] = useState([])
  const [testStates, setTestStates] = useState(() => {
    try { return JSON.parse(localStorage.getItem("voices_testStates")) || {} } catch { return {} }
  })

  const fetchVoices = async () => {
    setLoading(true)
    try {
      const data = await listVoices()
      setVoices(data)
    } catch (e) {
      notify({ severity: "error", message: e.message })
    } finally {
      setLoading(false)
    }
  }

  const fetchLangs = async () => {
    try {
      const data = await getLanguages()
      setLanguages(data)
    } catch (e) {}
  }

  useEffect(() => {
    fetchVoices()
    fetchLangs()
  }, [])

  const rows = useMemo(() => {
    let r = voices
    if (filterLang !== "All") r = r.filter(v => v.language === filterLang)
    if (query.trim()) r = r.filter(v => (v.name || "").toLowerCase().includes(query.toLowerCase()))
    return r.sort((a, b) => a.name.localeCompare(b.name))
  }, [voices, query, filterLang])

  const langsInUse = useMemo(() => {
    const s = new Set(voices.map(v => v.language).filter(Boolean))
    return Array.from(s).sort()
  }, [voices])

  const handleDelete = async (v) => {
    try {
      await deleteVoice(v.name)
      setVoices(cur => cur.filter(x => x.name !== v.name))
      notify({ severity: "success", message: `Deleted voice "${v.name}"` })
      if (selectedVoice?.name === v.name) setSelectedVoice(null)
    } catch (e) {
      notify({ severity: "error", message: e.message })
    }
    setConfirmDel(null)
  }

  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden", background: colors.bg }}>
      {/* Left Sidebar */}
      <div style={{ width: 260, background: colors.panel, borderRight: `1px solid ${colors.border}`, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "24px 20px 16px" }}>
          <h2 style={{ color: colors.text, fontSize: 20, fontWeight: fonts.bold, marginBottom: 16 }}>Voices</h2>
          <Button variant="primary" onClick={() => setShowClone(true)} style={{ width: "100%", borderRadius: radius.md, padding: "10px 0", fontWeight: fonts.bold }}>
            + Clone Voice
          </Button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 20px" }}>
          <div style={{ color: colors.textDim, fontSize: fonts.xs, fontWeight: fonts.bold, padding: "8px 10px", marginTop: 8, letterSpacing: "0.06em" }}>LANGUAGES</div>
          <FilterItem active={filterLang === "All"} onClick={() => setFilterLang("All")}>All Languages</FilterItem>
          {langsInUse.map(lang => (
            <FilterItem key={lang} active={filterLang === lang} onClick={() => setFilterLang(lang)}>{lang}</FilterItem>
          ))}
        </div>
      </div>

      {/* Main Content */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "24px 28px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="🔍 Search voices..."
            style={{ width: 300, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }} />
        </div>
        
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 28px 28px" }}>
          {loading ? (
            <div style={{ color: colors.muted }}>Loading voices...</div>
          ) : rows.length === 0 ? (
            <div style={{ color: colors.muted }}>No voices found.</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 16 }}>
              {rows.map(v => (
                <div key={v.name} onClick={() => setSelectedVoice(v)}
                  style={{
                    background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.md, padding: 16, cursor: "pointer",
                    display: "flex", flexDirection: "column", gap: 12, transition: "border-color 0.15s",
                    ...(selectedVoice?.name === v.name ? { borderColor: colors.accent, boxShadow: `0 0 0 1px ${colors.accent}` } : {})
                  }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ width: 44, height: 44, borderRadius: radius.full, background: colors.panel2, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, color: colors.text }}>
                      {v.name.charAt(0).toUpperCase()}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ color: colors.text, fontWeight: fonts.bold, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{v.name}</div>
                      <div style={{ color: colors.textDim, fontSize: fonts.sm }}>{v.language || "Unknown"}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right Sidebar Details */}
      {selectedVoice && (
        <VoiceDetailsPanel 
          key={selectedVoice.name}
          voice={selectedVoice} 
          testState={testStates[selectedVoice.name] || { testText: "Hello, this is a quick test of my voice.", audioUrl: null, testing: false }}
          setTestState={(partial) => setTestStates(cur => {
            const next = { 
              ...cur, 
              [selectedVoice.name]: { 
                ...(cur[selectedVoice.name] || { testText: "Hello, this is a quick test of my voice.", audioUrl: null, testing: false }), 
                ...partial 
              } 
            }
            localStorage.setItem("voices_testStates", JSON.stringify(next))
            return next
          })}
          onClose={() => setSelectedVoice(null)} 
          onDelete={() => setConfirmDel(selectedVoice)}
          onUpdated={(updated) => {
            setVoices(cur => cur.map(v => v.name === updated.name ? updated : v))
            setSelectedVoice(updated)
          }}
        />
      )}

      {showClone && (
        <CloneVoiceModal 
          languages={languages} 
          onClose={() => setShowClone(false)} 
          onCreated={(v) => { setVoices(cur => [...cur, v]); setShowClone(false); setSelectedVoice(v) }} 
        />
      )}

      {confirmDel && (
        <Modal open title={`Delete voice "${confirmDel.name}"?`} body="This action cannot be undone." confirmVariant="danger" confirmLabel="Delete"
          onClose={() => setConfirmDel(null)} onConfirm={() => handleDelete(confirmDel)} />
      )}
    </div>
  )
}

function FilterItem({ active, onClick, children }) {
  return (
    <div onClick={onClick} style={{
      padding: "8px 10px", margin: "2px 0", borderRadius: radius.md, cursor: "pointer",
      background: active ? "rgba(251,191,36,0.15)" : "transparent",
      color: active ? colors.warning : colors.textDim,
      fontWeight: active ? fonts.bold : fonts.medium,
      fontSize: fonts.sm
    }}>
      {children}
    </div>
  )
}

function CloneVoiceModal({ languages, onClose, onCreated }) {
  const { notify } = useNotify()
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState("")
  const [language, setLanguage] = useState("English")
  const [refPath, setRefPath] = useState("")
  const [transcribe, setTranscribe] = useState(true)

  const pickAudio = async () => {
    try {
      if (window.pywebview?.api?.pick_file) {
        const path = await window.pywebview.api.pick_file(["Media Files (*.wav;*.mp3;*.m4a;*.mp4;*.mkv;*.mov)"])
        if (path) setRefPath(path)
      } else {
        const path = window.prompt("Absolute path to reference audio:")
        if (path) setRefPath(path.trim())
      }
    } catch (e) {
      notify({ severity: "error", message: e.message })
    }
  }

  const handleSubmit = async () => {
    if (!name.trim()) return notify({ severity: "error", message: "Name is required" })
    if (!refPath) return notify({ severity: "error", message: "Reference audio is required" })
    setBusy(true)
    try {
      const v = await createVoice({ name: name.trim(), language })
      notify({ severity: "info", message: "Processing reference audio..." })
      // setVoiceReference flips the profile to VoiceClone (+ a -Base model) and
      // returns the UPDATED profile — use THAT, not the pre-reference `v` (which is
      // still the CustomVoice default), or the panel shows the wrong mode/engine.
      const updated = await setVoiceReference(v.name, refPath, transcribe)
      notify({ severity: "success", message: `Voice "${v.name}" cloned successfully!` })
      onCreated(updated || v)
    } catch (e) {
      notify({ severity: "error", message: e.message })
      setBusy(false)
    }
  }

  return (
    <Modal open title="Clone New Voice" onClose={!busy ? onClose : undefined} confirmLabel={busy ? "Cloning..." : "Clone"} onConfirm={handleSubmit} confirmLoading={busy}>
      <FormField label="Voice Name">
        <TextInput value={name} onChange={e => setName(e.target.value)} disabled={busy} placeholder="e.g. John Doe" />
      </FormField>
      <FormField label="Primary Language">
        <select value={language} onChange={e => setLanguage(e.target.value)} disabled={busy}
          style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "9px 12px", borderRadius: radius.md }}>
          {languages.map(l => <option key={l.code} value={l.name}>{l.name}</option>)}
        </select>
      </FormField>
      <FormField label="Reference Audio">
        <div style={{ display: "flex", gap: 8 }}>
          <Button variant="secondary" onClick={pickAudio} disabled={busy}>Select File</Button>
          <input type="text" value={refPath} readOnly disabled
            placeholder="No file selected"
            style={{ flex: 1, background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.textDim, padding: "8px 12px", borderRadius: radius.sm }} />
        </div>
      </FormField>
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16, cursor: "pointer", color: colors.text, fontSize: fonts.sm }}>
        <input type="checkbox" checked={transcribe} onChange={e => setTranscribe(e.target.checked)} disabled={busy} />
        Auto-transcribe reference audio
      </label>
    </Modal>
  )
}

function VoiceDetailsPanel({ voice, onClose, onDelete, onUpdated, testState, setTestState }) {
  const { notify } = useNotify()
  const { testText, audioUrl, testing } = testState
  const audioRef = useRef()

  const handleTest = async () => {
    if (!testText.trim()) return
    setTestState({ testing: true, audioUrl: null })
    try {
      let cur = await quickTTS(testText, voice.name, voice.language)
      while (cur && cur.status === "running") {
        await new Promise(r => setTimeout(r, 1000))
        cur = await quickTTSStatus(cur.job_id)
      }
      if (cur.status === "failed") throw new Error(cur.error || "Generation failed")
      const base = (cur.synced_audio_url || cur.audio_url || "").split("?")[0]
      setTestState({ testing: false, audioUrl: `http://127.0.0.1:8000${base}?v=${Date.now()}` })
    } catch (e) {
      notify({ severity: "error", message: e.message })
      setTestState({ testing: false })
    }
  }

  useEffect(() => {
    if (audioUrl && audioRef.current) {
      audioRef.current.play().catch(console.error)
    }
  }, [audioUrl])

  return (
    <div style={{ width: 340, background: colors.panel, borderLeft: `1px solid ${colors.border}`, display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "20px", borderBottom: `1px solid ${colors.border}`, display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h3 style={{ color: colors.text, fontSize: 20, fontWeight: fonts.bold, margin: "0 0 4px" }}>{voice.name}</h3>
          <div style={{ color: colors.textDim, fontSize: fonts.sm }}>{voice.language || "Unknown Language"}</div>
        </div>
        <button onClick={onClose} style={{ color: colors.textDim, background: "none", border: "none", fontSize: 20, cursor: "pointer" }}>✕</button>
      </div>

      <div style={{ padding: 20, flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 24 }}>
        
        {/* Quick TTS */}
        <div>
          <h4 style={{ color: colors.text, fontSize: fonts.sm, fontWeight: fonts.bold, marginBottom: 8 }}>Quick Test</h4>
          <textarea value={testText} onChange={e => setTestState({ testText: e.target.value })} disabled={testing}
            rows={3} style={{ width: "100%", background: colors.panel2, border: `1px solid ${colors.border}`, color: colors.text, padding: "10px", borderRadius: radius.md, resize: "vertical", fontFamily: fonts.ui, fontSize: fonts.sm, marginBottom: 10 }} />
          <Button variant="primary" onClick={handleTest} disabled={testing || !testText.trim()} style={{ width: "100%", padding: 8 }}>
            {testing ? "Generating..." : "Test Voice"}
          </Button>
          {audioUrl && (
            <audio ref={audioRef} controls src={audioUrl} style={{ width: "100%", marginTop: 12, height: 36 }} />
          )}
        </div>

        {/* Info */}
        <div>
          <h4 style={{ color: colors.text, fontSize: fonts.sm, fontWeight: fonts.bold, marginBottom: 8 }}>Details</h4>
          <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: "8px", fontSize: fonts.sm }}>
            <div style={{ color: colors.textDim }}>Mode</div><div style={{ color: colors.text }}>{voice.mode || "—"}</div>
            <div style={{ color: colors.textDim }}>Engine</div><div style={{ color: colors.text }}>{voice.model || "—"}</div>
            {voice.mode === "CustomVoice"
              ? (<><div style={{ color: colors.textDim }}>Speaker ID</div><div style={{ color: colors.text }}>{voice.speaker || "—"}</div></>)
              : (voice.has_reference || voice.ref_wav_path)
                ? (<><div style={{ color: colors.textDim }}>Reference</div><div style={{ color: colors.text }}>✓ cloned from audio</div></>)
                : null}
          </div>
        </div>

      </div>

      <div style={{ padding: 20, borderTop: `1px solid ${colors.border}` }}>
        <Button variant="danger" onClick={onDelete} style={{ width: "100%", padding: 8, background: "transparent", color: colors.error, border: `1px solid ${colors.error}` }}>
          Delete Voice
        </Button>
      </div>
    </div>
  )
}
