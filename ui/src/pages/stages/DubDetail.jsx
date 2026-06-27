/**
 * ui/src/pages/stages/DubDetail.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Detail view for the Dub stage.
 *
 *   • Languages & voices — enable a language and assign its voice (you can only
 *     generate ones with a translation + a voice).
 *   • Batches            — playback is PER BATCH, not one full track: pick a
 *     language, play each generated batch to find the one that sounds wrong,
 *     and regenerate just that batch. Regenerating a batch re-runs its audio and
 *     re-syncs only its panel range — across all languages for English (the
 *     timing reference), or that language alone otherwise. Other languages' dub
 *     audio is never touched.
 *
 * Whole-language playback lives in the Sync stage, not here.
 */

import { useState, useEffect, useCallback } from "react"
import {
  getDubConfig, updateDubConfig, resetDubLanguage,
  getDubBatches, regenerateDubBatch,
} from "../../api/dubbing"
import { listVoices } from "../../api/voices"
import { useNotify } from "../../store/notify"
import { colors, fonts, radius } from "../../theme"
import { useEpisodePanels, DetailCenter, DetailHeader, AudioButton, RegenButton } from "./common"

const NONE = "— none —"

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }

export default function DubDetail({ episode, signal, busy, onRun, onCustomRun, status, progress, onDataChanged }) {
  const { notify } = useNotify()
  const { panels } = useEpisodePanels(episode?.id, signal)
  const [cfg, setCfg] = useState(null)
  const [allVoices, setAllVoices] = useState([])   // {name, language} — for per-language filtering
  const [langsOpen, setLangsOpen] = useState(() => lsGet("ms_dub_langs_open", "0") === "1")
  const toggleLangs = () => { const v = !langsOpen; setLangsOpen(v); lsSet("ms_dub_langs_open", v ? "1" : "0") }

  const loadCfg = useCallback(async () => {
    if (!episode?.id) return
    try { setCfg(await getDubConfig(episode.id)) } catch (err) { notify({ severity: "error", message: err.message }) }
  }, [episode?.id])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadCfg() }, [loadCfg, signal])
  useEffect(() => { listVoices().then(setAllVoices).catch(() => {}) }, [signal])

  // Voices whose language matches a row's language — so the dropdown only shows
  // relevant voices (no more scrolling every language's voices, no _en/_fr names).
  const voicesForLang = (lang) => {
    const want = (lang.name || "").toLowerCase()
    return allVoices.filter(v => (v.language || "").toLowerCase() === want).map(v => v.name)
  }

  const profileFor = (code) => (cfg?.profiles?.[code]) || (cfg?.suggested?.[code]) || NONE
  const hasTranslation = (lang) => (lang.has_translation) ||
    panels.some(p => (p.translations?.[lang.code]?.translated_text || "").trim())
  const generatable = (lang) => (lang.code === "en" || hasTranslation(lang)) && profileFor(lang.code) !== NONE

  const enabled = cfg?.enabled_langs || []
  const langs = cfg?.languages || []
  const genLangs = langs.filter(generatable)

  const saveCfg = async (patch) => {
    try { setCfg(await updateDubConfig(episode.id, patch)); onDataChanged?.() }
    catch (err) { notify({ severity: "error", message: err.message }); loadCfg() }
  }
  const toggleEnable = (code) => {
    const next = enabled.includes(code) ? enabled.filter(c => c !== code) : [...enabled, code]
    setCfg({ ...cfg, enabled_langs: next })
    saveCfg({ enabled_langs: next })
  }
  const setVoice = (code, name) => {
    const next = { ...(cfg.profiles || {}) }
    if (name === NONE) delete next[code]; else next[code] = name
    setCfg({ ...cfg, profiles: next })
    saveCfg({ profiles: next })
  }

  // Enabled languages that can actually be generated (translation + voice).
  const genEnabled = enabled.filter(c => genLangs.some(l => l.code === c))
  // Enabled languages not yet fully dubbed — drives the incremental "Continue".
  const incomplete = genEnabled.filter(c => !langs.find(l => l.code === c)?.has_continuous)
  // Run = (re)generate every selected language: reset its dub state (so it
  // doesn't skip as "already done"), then run the stage. Single regenerate path
  // — select the languages you want, then Run. Per-batch regenerate stays below.
  const runDub = async () => {
    if (!genEnabled.length) return
    try {
      for (const c of genEnabled) await resetDubLanguage(episode.id, c)
      onDataChanged?.(); onRun()
    } catch (err) { notify({ severity: "error", message: err.message }) }
  }

  if (!cfg) return <div><DetailHeader title="Dub" status={status} progress={progress} busy={busy} onRun={onRun} runLabel="Run" /><DetailCenter>Loading…</DetailCenter></div>

  return (
    <div>
      <DetailHeader
        title="Dub" subtitle="Voice per language · Run (re)generates · Continue resumes · play/regenerate by batch"
        status={status} progress={progress} busy={busy} onRun={runDub}
        runLabel="Run" runDisabled={genEnabled.length === 0}
        extra={incomplete.length > 0
          ? <RegenButton label={`Continue (${incomplete.length})`} onClick={() => onRun()} disabled={busy} />
          : null}
      />

      {status === "outdated" && (
        <Banner>Translations changed upstream — regenerate to update the affected clips.</Banner>
      )}

      <div style={card}>
        <div onClick={toggleLangs} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          <span style={{ color: colors.muted, fontSize: fonts.sm, width: 12 }}>{langsOpen ? "▾" : "▸"}</span>
          <div style={{ fontSize: fonts.md, fontWeight: fonts.medium, flex: 1 }}>Languages &amp; voices</div>
          {!langsOpen && (
            <span style={{ fontSize: fonts.xs, color: colors.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "60%" }}>
              {enabled.length
                ? `${enabled.length} enabled · ${langs.filter(l => enabled.includes(l.code)).map(l => l.name).join(", ")}`
                : "none enabled — click to choose"}
            </span>
          )}
        </div>
        {!langsOpen ? null : <>
        <div style={{ fontSize: fonts.xs, color: colors.muted, margin: "6px 0 8px 20px" }}>
          Tick a language and give it a voice. A voiced language can be queued even before it's
          translated — "Run all" will translate (if needed) then dub it.
        </div>
        {langs.map(lang => {
          const tr      = lang.code === "en" || hasTranslation(lang)
          const voice   = profileFor(lang.code)
          const hasVoice = voice !== NONE
          const on      = enabled.includes(lang.code)
          let stat, sc
          if (!hasVoice) { stat = "needs voice"; sc = colors.warning }
          else if (lang.has_continuous) { stat = "generated"; sc = colors.success }
          else if (!tr) { stat = on ? "queued — will translate" : "no translation"; sc = on ? colors.info : colors.muted }
          else { stat = "ready"; sc = colors.muted }
          return (
            <div key={lang.code} style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 0", borderBottom: `1px solid ${colors.border}` }}>
              <input type="checkbox" checked={on} disabled={!hasVoice}
                onChange={() => toggleEnable(lang.code)} style={{ accentColor: colors.accent, width: 15, height: 15 }} />
              <div style={{ flex: 1, color: hasVoice ? colors.text : colors.muted, fontSize: fonts.sm }}>
                {lang.name}{lang.code === "en" && <span style={{ color: colors.muted, fontSize: 10 }}> · master</span>}
              </div>
              <select value={voice} onChange={e => setVoice(lang.code, e.target.value)}
                style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "4px 7px", fontSize: fonts.xs, maxWidth: 150 }}>
                <option value={NONE}>{NONE}</option>
                {[...new Set([...(voice !== NONE ? [voice] : []), ...voicesForLang(lang)])].map(v => <option key={v} value={v}>{v}</option>)}
              </select>
              <span style={{ fontSize: 10, color: sc, border: `1px solid ${sc === colors.muted ? colors.border : sc}`, borderRadius: radius.full, padding: "1px 8px", whiteSpace: "nowrap" }}>{stat}</span>
            </div>
          )
        })}
        </>}
      </div>

      <BatchesCard
        episode={episode} signal={signal} busy={busy}
        genLangs={genLangs} enabled={enabled}
        onCustomRun={onCustomRun} onDataChanged={onDataChanged} notify={notify}
      />
    </div>
  )
}

// ── Batches ───────────────────────────────────────────────────────────────────

function BatchesCard({ episode, signal, busy, genLangs, enabled, onCustomRun, onDataChanged, notify }) {
  const [lang, setLang] = useState("")
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [confirm, setConfirm] = useState(null)   // batch idx awaiting confirmation

  // Languages worth showing batches for: enabled + generatable (fallback: all generatable).
  const options = (genLangs.filter(l => enabled.includes(l.code)).length
    ? genLangs.filter(l => enabled.includes(l.code)) : genLangs)

  useEffect(() => {
    if (!options.length) { setLang(""); return }
    if (!options.some(l => l.code === lang)) setLang(options[0].code)
  }, [options, lang])

  const load = useCallback(async () => {
    if (!episode?.id || !lang) { setData(null); return }
    setLoading(true)
    try { setData(await getDubBatches(episode.id, lang)) }
    catch (err) { notify({ severity: "error", message: err.message }); setData(null) }
    finally { setLoading(false) }
  }, [episode?.id, lang])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); setConfirm(null) }, [load, signal])

  // While a run is active, batch statuses change continuously (failed → done as
  // they regenerate) but `signal` only ticks at stage boundaries — so poll the
  // batch list so the rows update live instead of showing stale "failed".
  useEffect(() => {
    if (!busy || !lang) return
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [busy, lang, load])

  const langName = data?.lang_name || lang.toUpperCase()
  const doRegen = (idx) => {
    setConfirm(null)
    onCustomRun?.(
      () => regenerateDubBatch(episode.id, lang, idx),
      `Regenerating ${langName} batch ${idx + 1}…`,
    )
    onDataChanged?.()
  }

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <div style={{ fontSize: fonts.md, fontWeight: fonts.medium, flex: 1 }}>Batches</div>
        {options.length > 0 && (
          <select value={lang} onChange={e => setLang(e.target.value)}
            style={{ background: colors.panel2, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "4px 8px", fontSize: fonts.xs }}>
            {options.map(l => <option key={l.code} value={l.code}>{l.name}{l.code === "en" ? " · master" : ""}</option>)}
          </select>
        )}
      </div>
      <div style={{ fontSize: fonts.xs, color: colors.muted, marginBottom: 10 }}>
        Listen batch-by-batch to pinpoint a bad one, then regenerate just it. No full-track player here — that lives in Sync.
      </div>

      {options.length === 0 && <DetailCenter>Enable a language with a translation and voice to generate batches.</DetailCenter>}
      {options.length > 0 && loading && <DetailCenter>Loading batches…</DetailCenter>}
      {options.length > 0 && !loading && data && data.batches.length === 0 && (
        <DetailCenter>No batches yet for {langName} — run Dub to generate them.</DetailCenter>
      )}

      {!loading && data && data.batches.map(b => {
        const ready = b.status === "done" && b.audio_url
        let sc = colors.muted, label = b.status
        if (b.status === "done") { sc = colors.success; label = "done" }
        else if (b.status === "failed") { sc = colors.error; label = "failed" }
        else if (b.status === "pending") { sc = colors.warning; label = "pending" }
        return (
          <div key={b.idx}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: `1px solid ${colors.border}` }}>
              <AudioButton url={ready ? b.audio_url : ""} title={ready ? `Play batch ${b.idx + 1}` : "Not generated yet"} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: fonts.sm }}>Batch {b.idx + 1} · panels {b.panel_from + 1}–{b.panel_to + 1}</div>
                <div style={{ fontSize: fonts.xs, color: colors.muted }}>{b.duration ? `${b.duration.toFixed(1)}s` : "—"}</div>
              </div>
              <span style={{ fontSize: 10, color: sc, border: `1px solid ${sc === colors.muted ? colors.border : sc}`, borderRadius: radius.full, padding: "1px 8px", whiteSpace: "nowrap" }}>{label}</span>
              <RegenButton label="Regenerate" onClick={() => setConfirm(confirm === b.idx ? null : b.idx)} disabled={busy} />
            </div>
            {confirm === b.idx && (
              <ConfirmRegen lang={lang} langName={langName} b={b}
                onConfirm={() => doRegen(b.idx)} onCancel={() => setConfirm(null)} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function ConfirmRegen({ lang, langName, b, onConfirm, onCancel }) {
  const range = `${b.panel_from + 1}–${b.panel_to + 1}`
  const msg = lang === "en"
    ? <>Re-runs this batch’s audio, then re-syncs panels <b>{range}</b> for <b>all languages</b> (English is the timing reference). Other languages’ dub audio is <b>not</b> touched.</>
    : <>Re-runs this batch’s audio, then re-syncs panels <b>{range}</b> for <b>{langName}</b> only. No other language is affected.</>
  return (
    <div style={{ background: "rgba(251,191,36,0.08)", border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "9px 11px", margin: "0 0 8px", fontSize: fonts.xs, color: colors.warning }}>
      <div style={{ lineHeight: 1.55 }}>Regenerate {langName} Batch {b.idx + 1}? {msg}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button onClick={onConfirm} style={{ background: colors.accent, color: "#000", border: "none", borderRadius: radius.sm, padding: "4px 12px", fontSize: fonts.xs, fontWeight: fonts.medium, cursor: "pointer" }}>Regenerate</button>
        <button onClick={onCancel} style={{ background: "none", color: colors.textDim, border: `1px solid ${colors.border}`, borderRadius: radius.sm, padding: "4px 12px", fontSize: fonts.xs, cursor: "pointer" }}>Cancel</button>
      </div>
    </div>
  )
}

function Banner({ children }) {
  return (
    <div style={{ fontSize: fonts.xs, color: colors.warning, background: "rgba(251,191,36,0.08)",
      border: `1px solid rgba(251,191,36,0.35)`, borderRadius: radius.md, padding: "7px 10px", marginBottom: 12 }}>⟳ {children}</div>
  )
}

const card = { background: colors.panel, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: "12px 14px", marginBottom: 12 }
