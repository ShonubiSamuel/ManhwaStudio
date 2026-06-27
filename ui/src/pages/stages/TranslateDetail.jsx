/**
 * ui/src/pages/stages/TranslateDetail.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Detail view for the Translate stage.
 *
 *   • Target languages — chips pick which languages to translate into.
 *   • Reviewing        — choose a language to view/edit per panel.
 *   • Per panel        — English narration (source) above the editable
 *                        translation, with per-row Regenerate.
 *   • Header           — Run (fills missing) + Regenerate all (clear + run).
 *
 * Regenerate = clear the target text (translate runs incrementally), then run
 * the stage — handled by onRun from the Pipeline shell (with progress + toasts).
 * Editing a translation flips Dub/Sync to "outdated"; onDataChanged refreshes.
 */

import { useState, useEffect, useCallback } from "react"
import { updateTranslation } from "../../api/panels"
import { getTranslateConfig, setTranslateConfig, clearTranslation } from "../../api/translate"
import { useNotify } from "../../store/notify"
import { colors, fonts, radius } from "../../theme"
import { useEpisodePanels, DetailCenter, DetailHeader, RegenButton } from "./common"

// (header "extra" slot is built into DetailHeader)

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export default function TranslateDetail({ episode, signal, busy, onRun, status, progress, onDataChanged }) {
  const { notify } = useNotify()
  const { panels, setPanels, loading, error, reload } = useEpisodePanels(episode?.id, signal)
  const [cfg, setCfg]   = useState(null)
  // Reviewing language persists per-episode, so leaving and returning keeps your
  // choice (it no longer snaps back to the first language).
  const reviewKey = `ms_review_translate_${episode?.id}`
  const [lang, setLangState] = useState(() => lsGet(reviewKey, ""))
  const setLang = useCallback((v) => { setLangState(v); lsSet(reviewKey, v) }, [reviewKey])

  const loadCfg = useCallback(async () => {
    if (!episode?.id) return
    try { setCfg(await getTranslateConfig(episode.id)) } catch { /* non-fatal */ }
  }, [episode?.id])
  useEffect(() => { loadCfg() }, [loadCfg, signal])
  // Reload the saved reviewing language when the episode changes.
  useEffect(() => { setLangState(lsGet(`ms_review_translate_${episode?.id}`, "")) }, [episode?.id])

  const selected = cfg?.selected || []
  const allLangs = cfg?.languages || []   // every supported language
  // Reviewing is INDEPENDENT of the target chips — it lists all languages, so
  // toggling targets never changes it. Keep `lang` pointed at a valid code,
  // preferring the saved choice and only defaulting to the first when unset/invalid.
  useEffect(() => {
    if (!allLangs.length) return
    if (!allLangs.some(l => l.code === lang)) setLang(allLangs[0].code)
  }, [allLangs, lang])  // eslint-disable-line react-hooks/exhaustive-deps

  // Incremental "Continue": how many panel-translations are still missing across
  // the selected target languages (drives the Continue button + its count).
  const total = cfg?.total_panels || 0
  const missing = selected.reduce((sum, c) => {
    const l = allLangs.find(x => x.code === c)
    return sum + Math.max(0, total - (l?.translated_count || 0))
  }, 0)

  const toggleLang = async (code) => {
    if (!cfg) return
    const next = selected.includes(code) ? selected.filter(c => c !== code) : [...selected, code]
    setCfg({ ...cfg, selected: next })  // optimistic
    try { setCfg(await setTranslateConfig(episode.id, next)) }
    catch (err) { notify({ severity: "error", message: `Couldn't update languages: ${err.message}` }); loadCfg() }
  }

  const replace = (fresh) => setPanels(prev => prev.map(p => (p.id === fresh.id ? fresh : p)))

  // Run = (re)generate every SELECTED language: clear their translations, then
  // run the stage. This is the single regenerate path — there's no separate
  // "Regenerate all" (select the languages you want, then Run).
  const runSelected = async () => {
    if (!selected.length) return
    try {
      for (const c of selected) await clearTranslation(episode.id, c)
      onDataChanged?.(); onRun()
    } catch (err) { notify({ severity: "error", message: err.message }) }
  }
  const regenPanel = async (panelId) => {
    if (!lang) return
    try { await clearTranslation(episode.id, lang, panelId); onDataChanged?.(); onRun() }
    catch (err) { notify({ severity: "error", message: err.message }) }
  }

  return (
    <div>
      <DetailHeader
        title="Translate" subtitle="Pick languages · Run (re)generates · Continue fills gaps · edit per panel"
        status={status} progress={progress} busy={busy} onRun={runSelected}
        runLabel="Run" runDisabled={selected.length === 0}
        extra={missing > 0 && selected.length > 0
          ? <RegenButton label={`Continue (${missing})`} onClick={() => onRun()} disabled={busy} />
          : null}
      />

      {status === "outdated" && (
        <Banner>Narration changed upstream — regenerate to update the affected translations.</Banner>
      )}

      <div style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "12px 14px", marginBottom: 12 }}>
        <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>Target languages</div>
        <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 10px" }}>Which languages to translate the narration into.</div>
        <div>
          {(cfg?.languages || []).map(l => {
            const on = selected.includes(l.code)
            return (
              <span key={l.code} onClick={() => toggleLang(l.code)}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: fonts.sm, cursor: "pointer",
                  border: `1px solid ${on ? colors.accent : colors.border}`, color: on ? colors.accent : colors.textDim,
                  background: on ? "rgba(255,107,53,0.1)" : "transparent", borderRadius: radius.full,
                  padding: "4px 11px", margin: "0 6px 6px 0" }}>
                {l.name}
                {l.translated_count > 0 && <span style={{ color: colors.muted, fontSize: 10 }}>{l.translated_count}/{cfg.total_panels}</span>}
              </span>
            )
          })}
        </div>
      </div>

      <>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: fonts.sm, color: colors.textDim }}>Reviewing</span>
            <select value={lang} onChange={e => setLang(e.target.value)}
              style={{ width: 160, background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "5px 8px", fontSize: fonts.sm }}>
              {allLangs.map(l => <option key={l.code} value={l.code}>{l.name} ({l.code})</option>)}
            </select>
            <span style={{ fontSize: fonts.xs, color: colors.muted }}>view any language · independent of the targets above</span>
          </div>

          {loading && <DetailCenter>Loading panels…</DetailCenter>}
          {error && !loading && <DetailCenter><div style={{ color: colors.error }}>{error}</div><button onClick={reload} style={retry}>Retry</button></DetailCenter>}
          {!loading && !error && panels.map(panel => (
            <TransRow key={panel.id} panel={panel} lang={lang} onSaved={replace}
              onDataChanged={onDataChanged} onRegen={() => regenPanel(panel.id)} busy={busy} />
          ))}
      </>
    </div>
  )
}

function TransRow({ panel, lang, onSaved, onDataChanged, onRegen, busy }) {
  const tr = panel.translations?.[lang]
  const initial = tr?.translated_text || ""
  const [val, setVal] = useState(initial)
  const [server, setServer] = useState(initial)
  const [msg, setMsg] = useState("")

  useEffect(() => { setVal(initial); setServer(initial); setMsg("") }, [initial, lang])

  const dirty = val !== server
  const commit = async () => {
    if (!dirty) return
    setMsg("saving…")
    try {
      const res = await updateTranslation(panel.id, lang, val)
      onSaved(res.panel); setServer(val); setMsg("saved"); onDataChanged?.()
    } catch (err) { setMsg(err.message || "save failed") }
  }

  const en = panel.narration_text || panel.transcript_text || ""
  return (
    <div style={{ background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 12, marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
        <span style={{ color: colors.muted, fontFamily: fonts.mono, fontSize: fonts.xs }}>#{panel.panel_index + 1}</span>
        <span style={{ fontSize: fonts.xs, color: tr?.translated_text ? colors.success : colors.muted }}>
          {tr?.translated_text ? "translated" : "not translated"}
        </span>
      </div>
      <div style={{ fontSize: fonts.xs, color: colors.textDim, fontWeight: fonts.bold, letterSpacing: "0.06em", marginBottom: 3 }}>ENGLISH</div>
      <div style={{ fontSize: fonts.sm, color: colors.muted, lineHeight: 1.45, marginBottom: 9 }}>{en || <span style={{ fontStyle: "italic" }}>no narration</span>}</div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span style={{ fontSize: fonts.xs, color: colors.textDim, fontWeight: fonts.bold, letterSpacing: "0.06em" }}>{lang.toUpperCase()}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: fonts.xs, color: dirty ? colors.warning : colors.muted }}>{msg || (dirty ? "unsaved" : "")}</span>
          <RegenButton label="Regenerate" onClick={onRegen} disabled={busy} />
        </div>
      </div>
      <textarea value={val} placeholder="Not translated yet" onChange={e => setVal(e.target.value)} onBlur={commit} spellCheck={false}
        style={{ width: "100%", minHeight: 64, resize: "vertical", background: colors.panel2, color: colors.text,
          border: `1px solid ${dirty ? colors.warning : colors.border}`, borderRadius: radius.md,
          padding: "8px 10px", fontSize: fonts.base, fontFamily: fonts.ui, lineHeight: 1.5, outline: "none" }} />
    </div>
  )
}

function Banner({ children }) {
  return (
    <div style={{ fontSize: fonts.xs, color: colors.warning, background: "rgba(251,191,36,0.08)",
      border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "7px 10px", marginBottom: 12 }}>
      ⟳ {children}
    </div>
  )
}

const retry = { marginTop: 10, background: "#2a2a2e", color: "#f0f0f0", border: "none", borderRadius: 4, padding: "5px 12px", cursor: "pointer", fontSize: 12 }
