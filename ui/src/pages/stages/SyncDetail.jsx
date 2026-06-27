/**
 * ui/src/pages/stages/SyncDetail.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Detail view for the Sync stage (video only).
 *
 * English is the timing reference; each other language's per-panel audio is
 * stretched/compressed to match the English panel duration.
 *
 *   • Reviewing      — pick any dubbed language (English = the reference).
 *   • Full audio     — play the whole combined synced track for that language.
 *   • Per panel      — a table of English vs target duration, the stretch
 *     applied, with per-panel playback and re-sync.
 *
 * Run = full re-sync of every non-English language (clear + run).  Continue =
 * incremental (fills only the panels that aren't synced yet — resume a stopped
 * run).  Re-sync on a row redoes just that panel.
 */

import { useState, useEffect, useCallback } from "react"
import { clearSync, getSyncBatches, getSyncConfig, setSyncConfig } from "../../api/sync"
import { getDubConfig, fixDubPanels } from "../../api/dubbing"
import { useNotify } from "../../store/notify"
import { colors, fonts, radius } from "../../theme"
import { useEpisodePanels, DetailCenter, DetailHeader, AudioButton, RegenButton } from "./common"

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export default function SyncDetail({ episode, signal, busy, onRun, onCustomRun, status, progress, onDataChanged }) {
  const { notify } = useNotify()
  const { panels, loading, error, reload } = useEpisodePanels(episode?.id, signal)
  const [codes, setCodes] = useState([])   // [{code,name}] dubbed languages (incl. English) — for Reviewing
  const [cfg, setCfg] = useState(null)      // sync target selection (non-English candidates)
  // Reviewing language persists per-episode (no longer snaps back to English).
  const reviewKey = `ms_review_sync_${episode?.id}`
  const [lang, setLangState] = useState(() => lsGet(reviewKey, ""))
  const setLang = useCallback((v) => { setLangState(v); lsSet(reviewKey, v) }, [reviewKey])
  const [info, setInfo] = useState(null)    // full-audio url + reference flag for the selected language

  // Languages with a dub continuous track can be synced; English is included as
  // the reference (its clips are its own — never stretched).
  useEffect(() => {
    let cancelled = false
    if (!episode?.id) return
    getDubConfig(episode.id)
      .then(c => { if (!cancelled) setCodes((c.languages || []).filter(l => l.has_continuous).map(l => ({ code: l.code, name: l.name }))) })
      .catch(() => {})
    getSyncConfig(episode.id).then(c => { if (!cancelled) setCfg(c) }).catch(() => {})
    return () => { cancelled = true }
  }, [episode?.id, signal])
  useEffect(() => { setLangState(lsGet(`ms_review_sync_${episode?.id}`, "")) }, [episode?.id])
  // Keep the reviewing language valid, preferring the saved choice.
  useEffect(() => {
    if (!codes.length) return
    if (!codes.some(c => c.code === lang)) setLang(codes[0].code)
  }, [codes, lang])  // eslint-disable-line react-hooks/exhaustive-deps

  const loadInfo = useCallback(async () => {
    if (!episode?.id || !lang) { setInfo(null); return }
    try { setInfo(await getSyncBatches(episode.id, lang)) } catch { setInfo(null) }
  }, [episode?.id, lang])
  useEffect(() => { loadInfo() }, [loadInfo, signal])

  const isRef = lang === "en"
  const langName = info?.lang_name || lang.toUpperCase()
  const targets = cfg?.languages || []          // non-English sync candidates
  const selected = cfg?.selected || []
  const total = cfg?.total_panels || 0

  const toggleTarget = async (code) => {
    if (!cfg) return
    const next = selected.includes(code) ? selected.filter(c => c !== code) : [...selected, code]
    setCfg({ ...cfg, selected: next })           // optimistic
    try { setCfg(await setSyncConfig(episode.id, next)) }
    catch (err) { notify({ severity: "error", message: err.message }); getSyncConfig(episode.id).then(setCfg).catch(() => {}) }
  }

  // Continue = panels still unsynced across the SELECTED languages.
  const missing = targets.reduce((sum, t) =>
    selected.includes(t.code) ? sum + Math.max(0, total - (t.synced_count || 0)) : sum, 0)

  // Rushed = panels whose dub runs longer than English beyond the comfort band
  // (these are the ones that sound sped-up). The fix re-translates them shorter.
  const rushed = isRef ? 0 : panels.filter(p => {
    const en = p.translations?.en?.raw_duration
    const t  = p.translations?.[lang]?.raw_duration
    return en > 0 && t > 0 && t / en > STRETCH_CAP
  }).length

  // Run = full re-sync of every SELECTED language (clear all + run).
  const runAll = async () => {
    if (!selected.length) return
    try {
      for (const c of selected) await clearSync(episode.id, c)
      onDataChanged?.(); onRun()
    } catch (err) { notify({ severity: "error", message: err.message }) }
  }
  // Fix = re-translate shorter → re-dub → re-sync (best of 3), as a background run.
  const fixRushed = () => {
    if (isRef || !rushed) return
    onCustomRun?.(() => fixDubPanels(episode.id, lang, null), `Fixing rushed ${langName} panels…`)
    onDataChanged?.()
  }
  const fixPanel = (panelIndex) => {
    if (isRef) return
    onCustomRun?.(() => fixDubPanels(episode.id, lang, [panelIndex]), `Fixing ${langName} panel ${panelIndex + 1}…`)
    onDataChanged?.()
  }

  return (
    <div>
      <DetailHeader
        title="Sync" subtitle="Stretch each language to English timing · play & compare per panel"
        status={status} progress={progress} busy={busy} onRun={runAll}
        runLabel="Run" runDisabled={selected.length === 0}
        extra={(rushed > 0 || missing > 0)
          ? <div style={{ display: "flex", gap: 8 }}>
              {rushed > 0 && <RegenButton label={`Fix rushed (${rushed})`} onClick={fixRushed} disabled={busy} />}
              {missing > 0 && <RegenButton label={`Continue (${missing})`} onClick={() => onRun()} disabled={busy} />}
            </div>
          : null}
      />
      <div style={{ fontSize: fonts.xs, color: colors.muted, marginBottom: 10 }}>Video only · English is the timing reference.</div>

      {status === "outdated" && (
        <Banner>Audio changed upstream — re-sync the affected panels.</Banner>
      )}

      {targets.length > 0 && (
        <div style={card}>
          <div style={{ fontSize: fonts.md, fontWeight: fonts.medium }}>Target languages</div>
          <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "2px 0 10px" }}>Which dubbed languages to time-stretch to English. Run syncs the selected ones.</div>
          <div>
            {targets.map(t => {
              const on = selected.includes(t.code)
              return (
                <span key={t.code} onClick={() => toggleTarget(t.code)}
                  style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: fonts.sm, cursor: "pointer",
                    border: `1px solid ${on ? colors.accent : colors.border}`, color: on ? colors.accent : colors.textDim,
                    background: on ? "rgba(255,107,53,0.1)" : "transparent", borderRadius: radius.full,
                    padding: "4px 11px", margin: "0 6px 6px 0" }}>
                  {t.name}
                  {t.synced_count > 0 && <span style={{ color: colors.muted, fontSize: 10 }}>{t.synced_count}/{total}</span>}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {codes.length === 0 && <DetailCenter>No dubbed languages yet — run Dub first, then sync.</DetailCenter>}

      {codes.length > 0 && (
        <div style={card}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: fonts.sm, color: colors.textDim }}>Reviewing</span>
            <select value={lang} onChange={e => setLang(e.target.value)}
              style={{ width: 170, background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "5px 8px", fontSize: fonts.sm }}>
              {codes.map(c => <option key={c.code} value={c.code}>{c.name}{c.code === "en" ? " · reference" : ""}</option>)}
            </select>
            {isRef && <span style={{ fontSize: fonts.xs, color: colors.muted }}>English is the reference — not stretched</span>}
          </div>

          {/* Full-language player */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 10px", border: `1px solid ${colors.border}`, borderRadius: radius.md, marginBottom: 12 }}>
            <AudioButton url={info?.full_audio_url || ""} title={info?.full_audio_url ? "Play full track" : "Not available yet"} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: fonts.sm }}>Full audio · {langName}</div>
              <div style={{ fontSize: fonts.xs, color: colors.muted }}>
                {!info?.full_audio_url ? "run Dub (and Sync) to produce the track"
                  : info.full_is_synced ? "combined synced track · real pacing"
                  : "dub output (pre-sync) — run Sync to build the combined track"}
              </div>
            </div>
          </div>

          {loading && <DetailCenter>Loading panels…</DetailCenter>}
          {error && !loading && <DetailCenter><div style={{ color: colors.error }}>{error}</div><button onClick={reload} style={retry}>Retry</button></DetailCenter>}

          {!loading && !error && panels.length > 0 && (() => {
            const overPanels = isRef ? [] : panels.filter(p => {
              const en = p.translations?.en?.raw_duration ?? p.duration_sec
              const o = p.translations?.[lang]?.raw_duration
              return en && o && (o / en) > STRETCH_CAP
            })
            return (
            <>
              {overPanels.length > 0 && (
                <div style={{ fontSize: fonts.xs, color: colors.warning, background: "rgba(251,191,36,0.08)",
                  border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "7px 10px", marginBottom: 10 }}>
                  ⚠ {overPanels.length} panel{overPanels.length === 1 ? "" : "s"} were compressed more than +{Math.round((STRETCH_CAP - 1) * 100)}% to fit the English length and may sound rushed. Shorten the {langName} translation for those panels in the Translate stage and re-sync.
                </div>
              )}
              <div style={{ fontSize: fonts.xs, color: colors.muted, marginBottom: 6 }}>
                {isRef
                  ? "English is the reference — these are its own clip lengths."
                  : `Every panel is matched to the English length (synced ≈ EN). Fit = how it got there — padded (short), a small compress, or "rushed" when the translation was too long.`}
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: fonts.sm }}>
                <thead>
                  <tr>{(isRef ? ["#", "Length", "", ""] : ["#", "EN", `${langName} orig`, `${langName} synced`, "Fit", "", ""]).map((h, i) =>
                    <th key={i} style={th}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {panels.map(p => {
                    // English AUDIO clip length (the budget) — falls back to the
                    // video panel length if not split yet.
                    const enDur = p.translations?.en?.raw_duration ?? p.duration_sec
                    if (isRef) {
                      const t = p.translations?.en
                      return (
                        <tr key={p.id}>
                          <td style={td}>{p.panel_index + 1}</td>
                          <td style={td}>{enDur != null ? `${enDur.toFixed(2)}s` : "—"}</td>
                          <td style={td}><AudioButton url={t?.audio_url || ""} title="Play English panel" /></td>
                          <td style={td} />
                        </tr>
                      )
                    }
                    const t = p.translations?.[lang]
                    const orig = t?.raw_duration
                    const synced = t?.synced_duration
                    // Fit indicator from the natural overrun (orig vs budget).
                    let fit = <span style={{ color: colors.muted }}>—</span>
                    if (orig != null && enDur) {
                      const ratio = orig / enDur
                      if (orig <= enDur) fit = <span style={{ color: colors.muted }}>padded</span>
                      else if (ratio > STRETCH_CAP) fit = <span style={{ color: colors.error }}>rushed +{Math.round((ratio - 1) * 100)}%</span>
                      else fit = <span style={{ color: colors.info }}>−{Math.round((ratio - 1) * 100)}%</span>
                    }
                    return (
                      <tr key={p.id}>
                        <td style={td}>{p.panel_index + 1}</td>
                        <td style={td}>{enDur != null ? `${enDur.toFixed(2)}s` : "—"}</td>
                        <td style={td}>{orig != null ? `${orig.toFixed(2)}s` : <span style={{ color: colors.muted }}>—</span>}</td>
                        <td style={td}>
                          {t?.is_synced && synced != null
                            ? <span style={{ color: colors.success }}>{synced.toFixed(2)}s</span>
                            : <span style={{ color: colors.muted }}>not synced</span>}
                        </td>
                        <td style={td}>{fit}</td>
                        <td style={td}><AudioButton url={t?.synced_url || ""} title={t?.synced_url ? "Play synced panel" : "Not synced yet"} /></td>
                        <td style={td}><RegenButton label="Fix" onClick={() => fixPanel(p.panel_index)} disabled={busy} title="Re-translate shorter, re-dub & re-sync this panel" /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </>
            )
          })()}
        </div>
      )}
    </div>
  )
}

function Banner({ children }) {
  return (
    <div style={{ fontSize: fonts.xs, color: colors.warning, background: "rgba(251,191,36,0.08)",
      border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "7px 10px", marginBottom: 12 }}>⟳ {children}</div>
  )
}

// Mirror of config.DUB_MAX_STRETCH — panels beyond this overrun are flagged.
const STRETCH_CAP = 1.20
const card = { background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "12px 14px", marginBottom: 12 }
const th = { textAlign: "left", color: colors.accent, fontWeight: fonts.medium, fontSize: fonts.xs, padding: "5px 7px", borderBottom: `1px solid ${colors.border}` }
const td = { padding: "6px 7px", borderBottom: `1px solid ${colors.border}`, color: colors.textDim, verticalAlign: "middle" }
const retry = { marginTop: 10, background: "#2a2a2e", color: "#f0f0f0", border: "none", borderRadius: 4, padding: "5px 12px", cursor: "pointer", fontSize: 12 }
