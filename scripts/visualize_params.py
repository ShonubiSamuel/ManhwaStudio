#!/usr/bin/env python3
"""
visualize_params.py — Real-Data Parameter Tuner
------------------------------------------------
Runs on your actual video and produces an interactive HTML report in your
browser.  Drag sliders to see exactly how each flag affects detection on
your real audio and video data — before committing to a full run.

Detection logic is shared with video_engine.py via detection_utils.py, so
parameter values transfer directly between the tuner and the pipeline engine.

Output: {stem}_tuner/{stem}_params.html  (auto-opens in browser)

Usage:
    python visualize_params.py episode.mp4
    python visualize_params.py episode.mp4 --duration 180   (first 3 min only)
    python visualize_params.py episode.mp4 --frame-skip 4   (faster scan)
"""

import sys, json, argparse, time, subprocess
from pathlib import Path

from detection_utils import (
    get_media_duration,
    get_audio_rms,
    detect_silence_ffmpeg,
    detect_visual_frames,
)

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts"}

def fmt(s):
    h, r = divmod(int(s), 3600)
    m, s2 = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s2:02d}"


# ── HTML pieces ────────────────────────────────────────────────────────────────

HTML_STYLE = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f7f7f5;color:#1a1a18;padding:20px;line-height:1.6}
h1{font-size:18px;font-weight:500;margin-bottom:4px}
.meta{font-size:13px;color:#888;margin-bottom:20px}
.main-grid{display:grid;grid-template-columns:320px 1fr;gap:16px;align-items:start}
@media(max-width:900px){.main-grid{grid-template-columns:1fr}}
.col-left{display:flex;flex-direction:column;gap:12px}
.col-right{display:flex;flex-direction:column;gap:12px}
.card{background:#fff;border:.5px solid #e0e0dc;border-radius:12px;padding:16px}
.card-title{font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.07em;color:#888;margin-bottom:12px}
.row{display:grid;grid-template-columns:1fr auto 52px;align-items:center;gap:8px;margin-bottom:10px}
.plabel{font-size:13px;font-weight:500}
.phint{font-size:11px;color:#999;margin-top:1px}
.val{font-size:13px;font-weight:500;text-align:right;white-space:nowrap}
input[type=range]{width:100%}
.pill-group{display:flex;gap:6px;flex-wrap:wrap}
.pill{padding:5px 14px;font-size:12px;border-radius:20px;border:.5px solid #ccc;background:#fff;cursor:pointer;color:#666;transition:all .15s}
.pill.active{background:#1a1a18;color:#fff;border-color:#1a1a18}
.tl-section{background:#fff;border:.5px solid #e0e0dc;border-radius:12px;overflow:hidden}
.tl-header{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:.5px solid #f0f0ee}
.tl-title{font-size:13px;font-weight:500}
.tl-time{font-size:11px;color:#888;min-width:120px;text-align:right}
.tl-wrap{background:#f9f9f7}
.tl-canvas{display:block;width:100%;cursor:grab}
.tl-canvas:active{cursor:grabbing}
.tl-footer{display:flex;align-items:center;justify-content:space-between;padding:6px 14px;border-top:.5px solid #f0f0ee;background:#fff}
.tl-hint{font-size:11px;color:#aaa}
.tt{position:fixed;background:#1c1c1a;color:#fff;font-size:12px;padding:5px 10px;border-radius:6px;pointer-events:none;display:none;z-index:999;white-space:nowrap}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:11px;color:#666}
.leg{display:flex;align-items:center;gap:4px}
.ldot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:#f5f5f3;border-radius:8px;padding:8px 12px;min-width:80px}
.stat-n{font-size:20px;font-weight:500}
.stat-l{font-size:11px;color:#888;margin-top:1px}
.insight{background:#eef2ff;border-left:3px solid #7080cc;border-radius:0 6px 6px 0;padding:8px 12px;font-size:12px;color:#444;line-height:1.5}
.cmd{background:#1c1c1a;color:#d4d4cc;font-family:monospace;font-size:11px;padding:12px;border-radius:8px;line-height:1.8;white-space:pre-wrap;word-break:break-all}
.cpbtn{padding:6px 16px;font-size:12px;border-radius:8px;border:.5px solid #bbb;background:#fff;cursor:pointer;font-weight:500}
.cpbtn:hover{background:#f0f0ee}
.zbtn{padding:3px 10px;font-size:11px;border-radius:6px;border:.5px solid #bbb;background:#fff;cursor:pointer}
.zbtn:hover{background:#f0f0ee}
.exact-pill{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:3px 9px;border-radius:10px;margin-top:8px;font-weight:500}
.exact-pill.exact{background:#e8f5e2;color:#2d7a1f;border:.5px solid #b8ddb0}
.exact-pill.approx{background:#f5f5f3;color:#888;border:.5px solid #ddd}
"""

HTML_BODY = """
<h1>Parameter Tuner &mdash; STEM</h1>
<p class="meta">Duration: TOTAL_DUR &nbsp;&middot;&nbsp; AUDIO_COUNT audio samples &nbsp;&middot;&nbsp; FRAME_COUNT video frames &nbsp;&middot;&nbsp; <em>Analyzing: ANALYZE_DUR</em></p>
<div id="tt" class="tt"></div>

<div class="main-grid">

  <div class="col-left">

    <div class="card">
      <div class="card-title">Audio silence &mdash; --mode audio</div>
      <div class="row">
        <div><div class="plabel">--min-silence</div><div class="phint">Shortest pause to count as panel gap (s)</div></div>
        <input type="range" min="0.05" max="1.5" step="0.05" value="0.25" id="s-ms" oninput="upd()">
        <div class="val" id="v-ms">0.25s</div>
      </div>
      <div class="row">
        <div><div class="plabel">--silence-db</div><div class="phint">Volume threshold &mdash; quieter = silence</div></div>
        <input type="range" min="-62" max="-20" step="1" value="-45" id="s-db" oninput="upd()">
        <div class="val" id="v-db">&minus;45 dB</div>
      </div>
      <div class="stats" style="margin-top:10px">
        <div class="stat"><div class="stat-n" id="a-cuts">--</div><div class="stat-l">audio cuts</div></div>
        <div class="stat"><div class="stat-n" id="a-avg">--</div><div class="stat-l">avg sec/panel</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Visual detection &mdash; AdaptiveDetector</div>
      <div class="row">
        <div><div class="plabel">--threshold</div><div class="phint">Sensitivity (lower = catches more cuts)</div></div>
        <input type="range" min="0.5" max="100.0" step="0.1" value="3.0" id="s-thr" oninput="upd()">
        <div class="val" id="v-thr">3.0</div>
      </div>
      <div class="row">
        <div><div class="plabel">--min-scene</div><div class="phint">Min seconds between cuts (zoom filter)</div></div>
        <input type="range" min="0.2" max="6.0" step="0.1" value="1.5" id="s-msc" oninput="upd()">
        <div class="val" id="v-msc">1.5s</div>
      </div>
      <div class="stats" style="margin-top:10px">
        <div class="stat"><div class="stat-n" id="v-kept">--</div><div class="stat-l">visual cuts</div></div>
        <div class="stat"><div class="stat-n" id="v-drop">--</div><div class="stat-l">artifacts dropped</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Merge strategy &mdash; who takes priority?</div>
      <div class="pill-group" style="margin-bottom:10px">
        <button class="pill active" id="pri-combined" onclick="setPriority('combined')">Equal</button>
        <button class="pill" id="pri-visual" onclick="setPriority('visual')">Visual first</button>
        <button class="pill" id="pri-audio" onclick="setPriority('audio')">Audio first</button>
      </div>
      <div class="insight" id="pri-desc" style="margin-bottom:12px"></div>
      <div class="row">
        <div><div class="plabel">--merge-window</div><div class="phint">Max gap (s) to link audio + visual</div></div>
        <input type="range" min="0.2" max="5.0" step="0.1" value="1.5" id="s-mw" oninput="upd()">
        <div class="val" id="v-mw">1.5s</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Final result</div>
      <div class="stats">
        <div class="stat"><div class="stat-n" id="m-cuts">--</div><div class="stat-l">final cuts</div></div>
        <div class="stat"><div class="stat-n" id="m-segs">--</div><div class="stat-l">segments</div></div>
        <div class="stat"><div class="stat-n" id="m-avg">--</div><div class="stat-l">avg sec/panel</div></div>
        <div class="stat"><div class="stat-n" id="m-drop">--</div><div class="stat-l">dropped</div></div>
      </div>
      <div id="exact-badge" class="exact-pill exact" style="margin-bottom:6px">&#9889; Exact ffmpeg detection</div>
      <div class="insight" id="m-ins" style="margin-top:4px"></div>
      <div class="cmd" id="cmd-box" style="margin-top:12px;font-size:11px"></div>
      <button class="cpbtn" onclick="copyCmd(this)" style="margin-top:8px">Copy command</button>
    </div>

  </div>

  <div class="col-right">

    <div class="tl-section">
      <div class="tl-header">
        <span class="tl-title">&#128266; Audio waveform</span>
        <div style="display:flex;align-items:center;gap:8px"><span class="tl-time" id="tv-audio"></span><button class="zbtn" onclick="tlReset('audio')">Reset</button></div>
      </div>
      <div class="tl-wrap"><canvas id="c-audio" class="tl-canvas" height="200"></canvas></div>
      <div class="tl-footer">
        <div class="legend">
          <span class="leg"><span class="ldot" style="background:#378ADD"></span>Speech</span>
          <span class="leg"><span class="ldot" style="background:#E24B4A"></span>Silence</span>
          <span class="leg"><span class="ldot" style="background:#3a9922"></span>Audio cut</span>
        </div>
        <div class="tl-hint"><span>&#x1F50D; Scroll=zoom &nbsp; &#x27A1; Drag=pan</span></div>
      </div>
    </div>

    <div class="tl-section">
      <div class="tl-header">
        <span class="tl-title">&#127910; Visual frame scores</span>
        <div style="display:flex;align-items:center;gap:8px"><span class="tl-time" id="tv-visual"></span><button class="zbtn" onclick="tlReset('visual')">Reset</button></div>
      </div>
      <div class="tl-wrap"><canvas id="c-visual" class="tl-canvas" height="200"></canvas></div>
      <div class="tl-footer">
        <div class="legend">
          <span class="leg"><span class="ldot" style="background:#9090e0"></span>Score</span>
          <span class="leg"><span class="ldot" style="background:#E24B4A;width:2px;border-radius:0"></span>Threshold</span>
          <span class="leg"><span class="ldot" style="background:#3a9922"></span>Kept</span>
          <span class="leg"><span class="ldot" style="background:rgba(226,75,74,.6)"></span>Dropped</span>
        </div>
        <div class="tl-hint"><span>&#x1F50D; Scroll=zoom &nbsp; &#x27A1; Drag=pan</span></div>
      </div>
    </div>

    <div class="tl-section">
      <div class="tl-header">
        <span class="tl-title">&#9999; Merged cuts</span>
        <div style="display:flex;align-items:center;gap:8px"><span class="tl-time" id="tv-merge"></span><button class="zbtn" onclick="tlReset('merge')">Reset</button></div>
      </div>
      <div class="tl-wrap"><canvas id="c-merge" class="tl-canvas" height="130"></canvas></div>
      <div class="tl-footer">
        <div class="legend">
          <span class="leg"><span class="ldot" style="background:#378ADD"></span>Audio</span>
          <span class="leg"><span class="ldot" style="background:#9090e0"></span>Visual</span>
          <span class="leg"><span class="ldot" style="background:#3a9922"></span>Confirmed</span>
          <span class="leg"><span class="ldot" style="background:rgba(226,75,74,.6)"></span>Dropped</span>
        </div>
        <div class="tl-hint"><span>&#x1F50D; Scroll=zoom &nbsp; &#x27A1; Drag=pan</span></div>
      </div>
    </div>

  </div>
</div>
"""


HTML_SCRIPT = r"""
const D   = __DATA_PLACEHOLDER__;
const dur = D.duration;
const inp = D.input;
const DPR = window.devicePixelRatio || 2;
const TL  = {};
const tt  = document.getElementById('tt');
let   _priority = 'combined';

// ── Exact-mode flag ───────────────────────────────────────────────────────────
// true  = sliders are at initial values → use ffmpeg-exact data from Python
// false = sliders have been moved       → use JS approximate calculation
let _exact_mode = true;

function _paramsMatchInit() {
  const ip = D.init_params;
  return (
    Math.abs(parseFloat(document.getElementById('s-ms').value) - ip.ms)  < 0.001 &&
    Math.abs(parseFloat(document.getElementById('s-db').value) - ip.db)  < 0.1   &&
    Math.abs(parseFloat(document.getElementById('s-thr').value) - ip.thr) < 0.05  &&
    Math.abs(parseFloat(document.getElementById('s-msc').value) - ip.msc) < 0.05  &&
    Math.abs(parseFloat(document.getElementById('s-mw').value) - ip.mw)  < 0.05
  );
}

// ── Timeline engine ───────────────────────────────────────────────────────────
function tlInit(name, drawFn) {
  const cv = document.getElementById('c-' + name);
  if (!cv) return;
  const ctx = cv.getContext('2d');
  TL[name] = { cv, ctx, startT:0, spp:null, drag:false, dragX:0, dragT:0, drawFn };
  tlResize(name);

  cv.addEventListener('wheel', e => {
    e.preventDefault();
    const tl = TL[name];
    const mx = (e.clientX - cv.getBoundingClientRect().left) * DPR;
    const tM = tl.startT + mx * tl.spp;
    tl.spp = Math.max(0.0003, Math.min(tl.spp * (e.deltaY > 0 ? 1.25 : 0.8), dur / (cv.width * 0.05)));
    tl.startT = Math.max(0, tM - mx * tl.spp);
    tlClamp(name); drawFn();
  }, { passive: false });

  cv.addEventListener('mousedown', e => {
    const tl = TL[name]; tl.drag = true;
    tl.dragX = e.clientX; tl.dragT = tl.startT;
  });
  window.addEventListener('mousemove', e => {
    const tl = TL[name]; if (!tl || !tl.drag) return;
    tl.startT = Math.max(0, tl.dragT - (e.clientX - tl.dragX) * DPR * tl.spp);
    tlClamp(name); drawFn();
  });
  window.addEventListener('mouseup', () => { if (TL[name]) TL[name].drag = false; });

  let tx0 = 0, tt0 = 0, pd = 0;
  cv.addEventListener('touchstart', e => {
    e.preventDefault(); const tl = TL[name];
    if (e.touches.length === 1) { tl.drag = true; tx0 = e.touches[0].clientX; tt0 = tl.startT; }
    if (e.touches.length === 2) { tl.drag = false; pd = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY); }
  }, { passive: false });
  cv.addEventListener('touchmove', e => {
    e.preventDefault(); const tl = TL[name];
    if (e.touches.length === 1 && tl.drag) { tl.startT = Math.max(0, tt0 - (e.touches[0].clientX - tx0) * DPR * tl.spp); tlClamp(name); drawFn(); }
    if (e.touches.length === 2) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      const mx = ((e.touches[0].clientX + e.touches[1].clientX) / 2 - cv.getBoundingClientRect().left) * DPR;
      const tM = tl.startT + mx * tl.spp;
      tl.spp = Math.max(0.0003, Math.min(tl.spp * (pd / d), dur / (cv.width * 0.05)));
      tl.startT = Math.max(0, tM - mx * tl.spp); tlClamp(name); drawFn(); pd = d;
    }
  }, { passive: false });
  cv.addEventListener('touchend', () => { if (TL[name]) TL[name].drag = false; });
}

function tlResize(name) {
  const tl = TL[name];
  const w = tl.cv.parentElement.clientWidth || tl.cv.parentElement.offsetWidth || 600;
  tl.cv.width  = w * DPR;
  tl.cv.height = tl.cv.offsetHeight * DPR || 200 * DPR;
  tl.spp = dur / tl.cv.width;
}
function tlClamp(name) {
  const tl = TL[name]; const vd = tl.cv.width * tl.spp;
  if (tl.startT + vd > dur) tl.startT = Math.max(0, dur - vd);
}
function tlReset(name) {
  const tl = TL[name]; tl.startT = 0; tl.spp = dur / tl.cv.width; tl.drawFn();
}
function tlLabel(name) {
  const tl = TL[name]; const end = Math.min(dur, tl.startT + tl.cv.width * tl.spp);
  const el = document.getElementById('tv-' + name);
  if (el) el.textContent = tl.startT.toFixed(2) + 's \u2013 ' + end.toFixed(2) + 's';
}
function tX(tl, t) { return (t - tl.startT) / tl.spp; }

// ── Tooltip ───────────────────────────────────────────────────────────────────
function showTT(e, msg) { tt.textContent = msg; tt.style.display = 'block'; tt.style.left = (e.clientX + 14) + 'px'; tt.style.top = (e.clientY - 30) + 'px'; }
function hideTT() { tt.style.display = 'none'; }

// ── Time axis ─────────────────────────────────────────────────────────────────
function drawAxis(ctx, tl, y, h) {
  const W = tl.cv.width; const vd = W * tl.spp;
  if (!isFinite(vd) || vd <= 0 || !isFinite(tl.spp) || tl.spp <= 0) return;
  const steps = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const tickSec = steps.find(s => s / tl.spp >= 55 * DPR) || 600;
  ctx.font = `${10 * DPR}px -apple-system,sans-serif`;
  ctx.textAlign = 'center';
  const first = Math.ceil(tl.startT / tickSec) * tickSec;
  for (let t = first; t <= tl.startT + vd + tickSec; t += tickSec) {
    const x = tX(tl, t); if (x < 0 || x > W) continue;
    ctx.fillStyle = 'rgba(0,0,0,0.08)'; ctx.fillRect(x, y, DPR, h);
    ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fillText(t.toFixed(t < 10 ? 2 : t < 100 ? 1 : 0) + 's', x, y + h - 2 * DPR);
  }
}

// ── Signal state ──────────────────────────────────────────────────────────────
let _sil = [], _good = [], _vk = [], _vd = [], _conf = [], _dvs = [];

// Approximate silence computation from RMS waveform — used when sliders move
function computeSilencesApprox(minS, db) {
  const thresh = Math.pow(10, db / 20), segs = []; let start = null;
  for (let i = 0; i < D.ar.length; i++) {
    if (D.ar[i] < thresh) { if (start === null) start = D.at[i]; }
    else { if (start !== null) { const e = D.at[i], sd = e - start; segs.push({ start, end: e, dur: sd, mid: (start + e) / 2, ok: sd >= minS }); start = null; } }
  }
  if (start !== null) { const e = D.at[D.at.length - 1]; segs.push({ start, end: e, dur: e - start, mid: (start + e) / 2, ok: e - start >= minS }); }
  return segs;
}

// Approximate visual computation from frame scores — used when sliders move
function computeVisualApprox(thr, msc) {
  if (!D.vs.length) return { kept: [], dropped: [] };
  const raw = []; for (let i = 0; i < D.vs.length; i++) if (D.vs[i] >= thr) raw.push({ t: D.vt[i], s: D.vs[i] });
  const kept = [], dropped = []; let lk = -9999;
  for (const ev of raw) { if (ev.t - lk >= msc) { kept.push(ev.t); lk = ev.t; } else dropped.push(ev.t); }
  return { kept, dropped };
}

function mergeSignals(sil, vk, mw, priority) {
  const gs = sil.filter(s => s.ok);

  if (priority === 'audio') {
    const cuts = gs.map(s => s.mid);
    return { confirmed: cuts, droppedVis: vk.slice() };
  }

  if (priority === 'visual') {
    const confirmed = [];
    for (const vt of vk) {
      let best = null, bestD = Infinity;
      for (let i = 0; i < gs.length; i++) { const d = Math.abs(vt - gs[i].mid); if (d < bestD) { bestD = d; best = i; } }
      if (best !== null && bestD <= mw) confirmed.push(gs[best].mid);
      else confirmed.push(vt);
    }
    confirmed.sort((a, b) => a - b);
    const deduped = []; for (const t of confirmed) if (!deduped.length || t - deduped[deduped.length - 1] > 0.4) deduped.push(t);
    return { confirmed: deduped, droppedVis: _vd.slice() };
  }

  // Equal (combined)
  const acc = new Set(), us = new Set();
  for (const vt of vk) {
    let b = null, bd = Infinity;
    for (let i = 0; i < gs.length; i++) { const d = Math.abs(vt - gs[i].mid); if (d < bd) { bd = d; b = i; } }
    if (b !== null && bd <= mw) { acc.add(gs[b].mid); us.add(b); }
  }
  for (let i = 0; i < gs.length; i++) if (!us.has(i) && gs[i].dur >= 0.4) acc.add(gs[i].mid);
  const s = Array.from(acc).sort((a, b) => a - b), dd = [];
  for (const t of s) if (!dd.length || t - dd[dd.length - 1] > 0.5) dd.push(t);
  const dv = vk.filter(vt => !gs.some(s => Math.abs(vt - s.mid) <= mw));
  return { confirmed: dd, droppedVis: dv };
}

// ── Priority UI ───────────────────────────────────────────────────────────────
const PRI_DESC = {
  combined: 'Equal weight: a cut is confirmed only when BOTH audio silence AND visual change agree within the merge window. Most conservative.',
  visual:   'Visual first: every visual panel change is treated as a real cut. Audio silence is used to snap the cut to the nearest quiet moment. Most accurate for Ken Burns / static-panel video style.',
  audio:    'Audio first: narrator pauses drive all cuts. Visual detection is ignored. Fastest and simplest.'
};
function setPriority(p) {
  _priority = p;
  document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
  document.getElementById('pri-' + p).classList.add('active');
  document.getElementById('pri-desc').textContent = PRI_DESC[p];
  upd();
}

// ── Draw: audio ───────────────────────────────────────────────────────────────
function drawAudio() {
  const tl = TL.audio; if (!tl) return;
  const { cv, ctx } = tl; const W = cv.width, H = cv.height;
  const AH = 16 * DPR, WH = H - AH;
  const thresh = Math.pow(10, parseFloat(document.getElementById('s-db').value) / 20);
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#f9f9f7'; ctx.fillRect(0, 0, W, H);

  const pd = new Float32Array(W), ps = new Uint8Array(W);
  for (let i = 0; i < D.at.length; i++) {
    const x = Math.round(tX(tl, D.at[i])); if (x < 0 || x >= W) continue;
    if (D.ar[i] > pd[x]) pd[x] = D.ar[i];
    if (D.ar[i] < thresh) ps[x] = 1;
  }
  let mv = 0; for (let x = 0; x < W; x++) if (pd[x] > mv) mv = pd[x]; if (mv === 0) mv = 1;
  for (let x = 0; x < W; x++) {
    if (pd[x] === 0) continue;
    const bh = Math.max(2 * DPR, (pd[x] / mv) * (WH - 4));
    ctx.fillStyle = ps[x] ? '#E24B4A' : '#378ADD';
    ctx.fillRect(x, WH - bh, 1, bh);
  }
  for (const s of _sil) { if (!s.ok) continue; const x1 = Math.max(0, tX(tl, s.start)), x2 = Math.min(W, tX(tl, s.end)); if (x2 < 0 || x1 > W) continue; ctx.fillStyle = 'rgba(226,75,74,0.06)'; ctx.fillRect(x1, 0, x2 - x1, WH); }
  for (const s of _good) {
    const x = tX(tl, s.mid); if (x < 2 || x > W - 2) continue;
    ctx.fillStyle = '#3a9922'; ctx.fillRect(x - 1.5 * DPR, 0, 3 * DPR, WH);
    ctx.beginPath(); ctx.moveTo(x - 5 * DPR, 0); ctx.lineTo(x + 5 * DPR, 0); ctx.lineTo(x, 8 * DPR); ctx.fill();
  }
  drawAxis(ctx, tl, WH, AH); tlLabel('audio');
  tl.cv.onmousemove = e => { const rx = (e.clientX - tl.cv.getBoundingClientRect().left) * DPR; const near = _good.find(s => Math.abs(tX(tl, s.mid) - rx) < 8 * DPR); if (near) showTT(e, 'Audio cut: ' + near.mid.toFixed(2) + 's (pause ' + near.dur.toFixed(2) + 's)'); else hideTT(); };
  tl.cv.onmouseleave = hideTT;
}

// ── Draw: visual ──────────────────────────────────────────────────────────────
function drawVisual() {
  const tl = TL.visual; if (!tl) return;
  const { cv, ctx } = tl; const W = cv.width, H = cv.height;
  const AH = 16 * DPR, CH = H - AH;
  const thr = parseFloat(document.getElementById('s-thr').value);
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#f9f9f7'; ctx.fillRect(0, 0, W, H);
  if (!D.vs.length) { ctx.fillStyle = '#888'; ctx.font = `${13 * DPR}px sans-serif`; ctx.textAlign = 'center'; ctx.fillText('No video frame data', W / 2, H / 2); return; }
  let mv = 0; for (const v of D.vs) if (v > mv) mv = v; mv = Math.max(mv, thr * 1.5, 1);
  ctx.beginPath(); ctx.strokeStyle = 'rgba(130,120,220,0.7)'; ctx.lineWidth = 1.5 * DPR; let first = true;
  for (let i = 0; i < D.vt.length; i++) {
    const x = tX(tl, D.vt[i]); if (x < -10 || x > W + 10) { first = true; continue; }
    const y = CH - (D.vs[i] / mv) * (CH - 4);
    if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
  }
  ctx.stroke();
  const ty = CH - (thr / mv) * (CH - 4);
  ctx.beginPath(); ctx.strokeStyle = 'rgba(226,75,74,0.8)'; ctx.lineWidth = 1.5 * DPR; ctx.setLineDash([6 * DPR, 3 * DPR]);
  ctx.moveTo(0, ty); ctx.lineTo(W, ty); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(226,75,74,0.7)'; ctx.font = `${10 * DPR}px sans-serif`; ctx.textAlign = 'right';
  ctx.fillText('thr ' + thr.toFixed(1), W - 4 * DPR, ty - 4 * DPR);
  for (const t of _vd) { const x = tX(tl, t); if (x < 0 || x > W) continue; const idx = D.vt.findIndex(v => Math.abs(v - t) < 0.15); const sc = idx >= 0 ? D.vs[idx] : thr; const y = CH - (sc / mv) * (CH - 4); ctx.fillStyle = 'rgba(226,75,74,0.55)'; ctx.beginPath(); ctx.arc(x, y, 5 * DPR, 0, Math.PI * 2); ctx.fill(); }
  for (const t of _vk) { const x = tX(tl, t); if (x < 0 || x > W) continue; const idx = D.vt.findIndex(v => Math.abs(v - t) < 0.15); const sc = idx >= 0 ? D.vs[idx] : thr; const y = CH - (sc / mv) * (CH - 4); ctx.strokeStyle = 'rgba(58,153,34,0.3)'; ctx.lineWidth = 1 * DPR; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, CH); ctx.stroke(); ctx.fillStyle = '#3a9922'; ctx.beginPath(); ctx.arc(x, y, 5.5 * DPR, 0, Math.PI * 2); ctx.fill(); }
  drawAxis(ctx, tl, CH, AH); tlLabel('visual');
  tl.cv.onmousemove = e => { const rx = (e.clientX - tl.cv.getBoundingClientRect().left) * DPR; const nk = _vk.find(t => Math.abs(tX(tl, t) - rx) < 10 * DPR); const nd = _vd.find(t => Math.abs(tX(tl, t) - rx) < 10 * DPR); if (nk) showTT(e, 'Visual cut: ' + nk.toFixed(2) + 's'); else if (nd) showTT(e, 'Dropped (zoom/pan): ' + nd.toFixed(2) + 's'); else hideTT(); };
  tl.cv.onmouseleave = hideTT;
}

// ── Draw: merge ───────────────────────────────────────────────────────────────
function drawMerge() {
  const tl = TL.merge; if (!tl) return;
  const { cv, ctx } = tl; const W = cv.width, H = cv.height;
  const AH = 16 * DPR, CH = H - AH; const RH = CH / 3; const R = 4 * DPR;
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#f9f9f7'; ctx.fillRect(0, 0, W, H);
  ctx.font = `${9 * DPR}px -apple-system,sans-serif`; ctx.textAlign = 'left';
  ['visual', 'merged', 'audio'].forEach((lbl, i) => {
    if (i > 0) { ctx.strokeStyle = 'rgba(0,0,0,0.06)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, i * RH); ctx.lineTo(W, i * RH); ctx.stroke(); }
    ctx.fillStyle = 'rgba(0,0,0,0.28)'; ctx.fillText(lbl, 3 * DPR, (i + 0.55) * RH);
  });
  for (const s of _good) { const x = tX(tl, s.mid); if (x < 0 || x > W) continue; ctx.fillStyle = '#378ADD'; ctx.beginPath(); ctx.moveTo(x, 2.5 * RH - R); ctx.lineTo(x + R, 2.5 * RH); ctx.lineTo(x, 2.5 * RH + R); ctx.lineTo(x - R, 2.5 * RH); ctx.closePath(); ctx.fill(); }
  for (const t of _vk) { const x = tX(tl, t); if (x < 0 || x > W) continue; ctx.fillStyle = '#9090e0'; ctx.beginPath(); ctx.moveTo(x, 0.5 * RH - R); ctx.lineTo(x + R, 0.5 * RH + R); ctx.lineTo(x - R, 0.5 * RH + R); ctx.closePath(); ctx.fill(); }
  for (const t of _dvs) { const x = tX(tl, t); if (x < 0 || x > W) continue; ctx.strokeStyle = 'rgba(226,75,74,0.7)'; ctx.lineWidth = 2 * DPR; ctx.beginPath(); ctx.moveTo(x - R, 0.5 * RH - R); ctx.lineTo(x + R, 0.5 * RH + R); ctx.stroke(); ctx.beginPath(); ctx.moveTo(x + R, 0.5 * RH - R); ctx.lineTo(x - R, 0.5 * RH + R); ctx.stroke(); }
  for (const t of _conf) { const x = tX(tl, t); if (x < 0 || x > W) continue; ctx.fillStyle = '#3a9922'; ctx.beginPath(); ctx.arc(x, 1.5 * RH, R * 1.3, 0, Math.PI * 2); ctx.fill(); }
  drawAxis(ctx, tl, CH, AH); tlLabel('merge');
  tl.cv.onmousemove = e => { const rx = (e.clientX - tl.cv.getBoundingClientRect().left) * DPR; const nc = _conf.find(t => Math.abs(tX(tl, t) - rx) < 10 * DPR); const na = _good.find(s => Math.abs(tX(tl, s.mid) - rx) < 8 * DPR); const nv = _vk.find(t => Math.abs(tX(tl, t) - rx) < 8 * DPR); if (nc) showTT(e, 'Panel cut: ' + nc.toFixed(2) + 's'); else if (na) showTT(e, 'Audio: ' + na.mid.toFixed(2) + 's (' + na.dur.toFixed(2) + 's)'); else if (nv) showTT(e, 'Visual: ' + nv.toFixed(2) + 's'); else hideTT(); };
  tl.cv.onmouseleave = hideTT;
}

// ── Main update ───────────────────────────────────────────────────────────────
function r1(n) { return Math.round(n * 10) / 10; }

function upd() {
  const ms  = parseFloat(document.getElementById('s-ms').value);
  const db  = parseFloat(document.getElementById('s-db').value);
  const thr = parseFloat(document.getElementById('s-thr').value);
  const msc = parseFloat(document.getElementById('s-msc').value);
  const mw  = parseFloat(document.getElementById('s-mw').value);

  document.getElementById('v-ms').textContent  = ms.toFixed(2) + 's';
  document.getElementById('v-db').textContent  = (db < 0 ? '\u2212' : '') + Math.abs(db).toFixed(0) + ' dB';
  document.getElementById('v-thr').textContent = thr.toFixed(1);
  document.getElementById('v-msc').textContent = msc.toFixed(1) + 's';
  document.getElementById('v-mw').textContent  = mw.toFixed(1) + 's';

  // ── Choose exact (ffmpeg) or approximate (JS) signal data ─────────────────
  _exact_mode = _paramsMatchInit();

  if (_exact_mode) {
    // Use pre-computed ffmpeg results embedded by Python at startup
    _sil  = D.exact_silences  || [];
    _good = _sil.filter(s => s.ok);
    _vk   = D.exact_vcuts     || [];
    _vd   = D.exact_vdropped  || [];
  } else {
    // Sliders moved — approximate from waveform/frame data in the browser
    const sil = computeSilencesApprox(ms, db);
    _sil  = sil;
    _good = sil.filter(s => s.ok);
    const { kept, dropped } = computeVisualApprox(thr, msc);
    _vk = kept; _vd = dropped;
  }

  const { confirmed, droppedVis } = mergeSignals(_sil, _vk, mw, _priority);
  _conf = confirmed; _dvs = droppedVis;

  for (const n of ['audio', 'visual', 'merge']) {
    if (TL[n] && (!isFinite(TL[n].spp) || TL[n].spp <= 0 || TL[n].cv.width === 0)) tlResize(n);
  }
  drawAudio(); drawVisual(); drawMerge();

  document.getElementById('a-cuts').textContent = _good.length;
  document.getElementById('a-avg').textContent  = _good.length ? r1(dur / (_good.length + 1)).toFixed(1) + 's' : '--';
  document.getElementById('v-kept').textContent = _vk.length;
  document.getElementById('v-drop').textContent = _vd.length;
  document.getElementById('m-cuts').textContent = confirmed.length;
  document.getElementById('m-segs').textContent = confirmed.length + 1;
  document.getElementById('m-avg').textContent  = confirmed.length ? r1(dur / (confirmed.length + 1)).toFixed(1) + 's' : '--';
  document.getElementById('m-drop').textContent = droppedVis.length;

  // ── Exact/approximate badge ────────────────────────────────────────────────
  const badge = document.getElementById('exact-badge');
  if (badge) {
    if (_exact_mode) {
      badge.textContent = '\u26A1 Exact ffmpeg detection';
      badge.className   = 'exact-pill exact';
    } else {
      badge.textContent = '\u2248 Approximate (slider moved from initial)';
      badge.className   = 'exact-pill approx';
    }
  }

  const modeFlag = _priority === 'audio' ? 'audio' : 'combined';
  document.getElementById('m-ins').textContent =
    confirmed.length + ' cuts \u2192 ' + (confirmed.length + 1) +
    ' segments \u00b7 ' + droppedVis.length + ' dropped \u00b7 priority: ' + _priority;

  const cmd = 'python auto_segment_pro.py "' + inp + '" \\\n  --mode ' + modeFlag +
    ' \\\n  --min-silence ' + ms.toFixed(2) +
    ' \\\n  --silence-db '  + db.toFixed(0) +
    ' \\\n  --threshold '   + thr.toFixed(1) +
    ' \\\n  --min-scene '   + msc.toFixed(1) +
    ' \\\n  --merge-window '+ mw.toFixed(1) +
    ' \\\n  --save-transcript';
  document.getElementById('cmd-box').textContent = cmd;
}

function copyCmd(btn) {
  navigator.clipboard.writeText(document.getElementById('cmd-box').textContent)
    .then(() => { btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy command', 2000); });
}

window.addEventListener('load', () => {
  tlInit('audio', drawAudio); tlInit('visual', drawVisual); tlInit('merge', drawMerge);
  setPriority('combined');
  upd();
});
window.addEventListener('resize', () => {
  for (const n of ['audio', 'visual', 'merge']) if (TL[n]) { tlResize(n); TL[n].drawFn(); }
});
"""


# ── HTML builder ───────────────────────────────────────────────────────────────

def build_html(
    stem:             str,
    audio_times:      list,
    audio_rms:        list,
    video_times:      list,
    video_scores:     list,
    duration:         float,
    analyze_dur:      float,
    input_path,
    args,
    exact_silences:   list,
    exact_vcuts:      list,
    exact_vdropped:   list,
) -> str:
    data = {
        "stem":     stem,
        "duration": round(analyze_dur, 2),
        "input":    str(input_path),
        # Waveform / frame-score arrays (display only)
        "at": audio_times,
        "ar": audio_rms,
        "vt": video_times,
        "vs": video_scores,
        # Exact detection results from Python at initial param values
        "exact_silences":  exact_silences,
        "exact_vcuts":     exact_vcuts,
        "exact_vdropped":  exact_vdropped,
        # Initial param values — JS uses these to detect slider movement
        "init_params": {
            "ms":  round(args.min_silence, 3),
            "db":  args.silence_db,
            "thr": args.threshold,
            "msc": args.min_scene,
            "mw":  args.merge_window,
        },
    }
    data_json = json.dumps(data, separators=(',', ':'))

    body = HTML_BODY
    body = body.replace('STEM',        stem)
    body = body.replace('TOTAL_DUR',   fmt(duration))
    body = body.replace('ANALYZE_DUR', fmt(analyze_dur))
    body = body.replace('AUDIO_COUNT', str(len(audio_rms)))
    body = body.replace('FRAME_COUNT', str(len(video_scores)))
    # Pre-populate sliders with current episode settings
    body = body.replace('value="0.25" id="s-ms"',
                        f'value="{args.min_silence:.2f}" id="s-ms"')
    body = body.replace('value="-45" id="s-db"',
                        f'value="{args.silence_db:.0f}" id="s-db"')
    body = body.replace('value="3.0" id="s-thr"',
                        f'value="{args.threshold:.1f}" id="s-thr"')
    body = body.replace('value="1.5" id="s-msc"',
                        f'value="{args.min_scene:.1f}" id="s-msc"')
    body = body.replace('value="1.5" id="s-mw"',
                        f'value="{args.merge_window:.1f}" id="s-mw"')

    script = HTML_SCRIPT.replace('__DATA_PLACEHOLDER__', data_json)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Param Tuner \u2014 {stem}</title>
<style>{HTML_STYLE}</style>
</head>
<body>
{body}
<script>
{script}
</script>
</body>
</html>"""


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Visualize detection parameters on your actual video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Opens an interactive HTML report in your browser.  Drag sliders to explore
how each parameter affects detection.  The initial state uses exact ffmpeg
results — the same algorithm as video_engine.py.

Tips:
  --duration 180   Analyze only first 3 minutes (much faster for tuning)
  --frame-skip 4   Faster frame scan (every 5th frame instead of every 3rd)

Examples:
  python visualize_params.py episode.mp4
  python visualize_params.py episode.mp4 --duration 120
  python visualize_params.py episode.mp4 --frame-skip 4 --duration 300
        """,
    )
    ap.add_argument("input")
    ap.add_argument("--duration",      type=float, default=None,   dest="duration")
    ap.add_argument("--frame-skip",    type=int,   default=2,      dest="frame_skip")
    ap.add_argument("--silence-db",    type=float, default=-45.0,  dest="silence_db")
    ap.add_argument("--min-silence",   type=float, default=0.25,   dest="min_silence")
    ap.add_argument("--threshold",     type=float, default=3.0,    dest="threshold")
    ap.add_argument("--min-scene",     type=float, default=1.5,    dest="min_scene")
    ap.add_argument("--merge-window",  type=float, default=1.5,    dest="merge_window")
    ap.add_argument("-o", "--output",  default=None)
    args = ap.parse_args()

    if not Path(args.input).exists():
        sys.exit(f"\n[ERROR] File not found: '{args.input}'\n")

    is_video = Path(args.input).suffix.lower() in VIDEO_EXTS
    stem     = Path(args.input).stem
    out_dir  = Path(args.output) if args.output else Path(args.input).parent / f"{stem}_tuner"
    out_dir.mkdir(parents=True, exist_ok=True)

    bar = "━" * 60
    print(f"\n{bar}")
    print(f"  Parameter Visualizer — {stem}")
    print(bar)

    # ── [1/4] Extract audio ────────────────────────────────────────────────────
    if is_video:
        audio_path = str(out_dir / f"{stem}_audio.mp3")
        print(f"\n[1/4]  Extracting audio …")
        t0 = time.time()
        subprocess.run(
            ["ffmpeg", "-i", args.input, "-vn", "-acodec", "libmp3lame",
             "-ab", "192k", "-ar", "44100", "-y", audio_path],
            capture_output=True, check=True,
        )
        print(f"   Done in {time.time()-t0:.1f}s")
    else:
        audio_path = args.input
        print(f"\n[1/4]  Using audio file directly")

    full_duration = get_media_duration(audio_path)
    analyze_dur   = min(args.duration, full_duration) if args.duration else full_duration
    print(f"   File duration : {fmt(full_duration)}")
    if analyze_dur < full_duration:
        print(f"   Analyzing     : {fmt(analyze_dur)} (first {analyze_dur:.0f}s only)")

    # ── [2/4] Waveform RMS samples (display only) ──────────────────────────────
    print(f"\n[2/4]  Sampling audio waveform …")
    t0 = time.time()
    audio_times, audio_rms = get_audio_rms(audio_path, analyze_dur)
    print(f"   {len(audio_rms)} samples in {time.time()-t0:.1f}s")

    # ── [3/4] Exact detection at initial params ────────────────────────────────
    print(f"\n[3/4]  Running exact detection (ffmpeg + OpenCV) …")

    # Audio: exact silence regions via ffmpeg
    t0 = time.time()
    raw_silences = detect_silence_ffmpeg(audio_path, args.min_silence, args.silence_db)
    # Filter to analysis window and build JS-ready dicts
    exact_silences = [
        {
            "start": round(s, 3),
            "end":   round(e, 3),
            "mid":   round((s + e) / 2, 3),
            "dur":   round(e - s, 3),
            "ok":    (e - s) >= args.min_silence,
        }
        for s, e in raw_silences
        if not analyze_dur or s < analyze_dur
    ]
    print(f"   Silence: {len(exact_silences)} region(s) in {time.time()-t0:.1f}s")

    # Visual: frame scores + exact cuts via OpenCV
    video_times, video_scores, exact_vcuts = [], [], []
    exact_vdropped = []
    if is_video:
        t0 = time.time()
        video_times, video_scores, exact_vcuts = detect_visual_frames(
            args.input,
            threshold     = args.threshold,
            min_scene_sec = args.min_scene,
            frame_skip    = args.frame_skip,
            max_duration  = analyze_dur,
        )
        # Compute dropped: frames that hit threshold but were filtered by gap
        last_kept = -9999.0
        for t, score in zip(video_times, video_scores):
            if score >= args.threshold:
                if t - last_kept >= args.min_scene:
                    last_kept = t  # kept — already in exact_vcuts
                else:
                    exact_vdropped.append(round(t, 3))
        print(f"   Visual : {len(exact_vcuts)} cut(s), {len(exact_vdropped)} dropped  in {time.time()-t0:.1f}s")
    else:
        print(f"   Visual : skipped (audio-only input)")

    # ── [4/4] Build HTML ───────────────────────────────────────────────────────
    print(f"\n[4/4]  Building HTML report …")
    html = build_html(
        stem, audio_times, audio_rms,
        video_times, video_scores,
        full_duration, analyze_dur, args.input, args,
        exact_silences, exact_vcuts, exact_vdropped,
    )
    out_path = out_dir / f"{stem}_params.html"
    out_path.write_text(html, encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024

    print(f"\n{bar}")
    print(f"  Done — {size_kb} KB")
    print(f"\n  open \"{out_path}\"")
    print(bar + "\n")

    import webbrowser
    webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()