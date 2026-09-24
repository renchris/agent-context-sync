// film/film.js: the launch film for agent-context-sync, as a pure function of time.
//
// Direction: docs/design/hero/DIRECTION.md § Launch video. Two cuts share one world and one story:
//   ?cut=loop  the README hero, an animated WebP: locked-off shots joined by hard cuts
//   ?cut=film  the MP4 behind the link under the hero
// and two grades (?theme=dark|light), tuned to GitHub's page colours so the frame edge vanishes.
//
// Structure. STORY is one sync on its own clock s. A cut is an edit list: each edit picks a camera
// and a line of type, and maps film time t to story time s. So no shot owns state, and a cut can
// show one moment from two places. The edit follows the design's idea, tempo means cost: free work
// gets short shots, and the read gets the longest.

import { THEMES, clamp01, e, ease, lerp, p } from './lib.js'
import { COLS, PX, REGIONS, ROWS, SX, SZ, camera, drawGroundText, drawPlane, drawRing, laneX, tileC, tileX, tileZ } from './world.js'

const q = new URLSearchParams(location.search)
const THEME = q.get('theme') === 'light' ? 'light' : 'dark'
const CUT = q.get('cut') === 'film' ? 'film' : 'loop'
const T = THEMES[THEME]
for (const [k, v] of Object.entries(T)) document.documentElement.style.setProperty(`--${k}`, v)
document.documentElement.dataset.theme = THEME

const W = 1920
const H = 1080
const stage = document.getElementById('stage')
const cv = document.createElement('canvas')
cv.width = W * 2
cv.height = H * 2
cv.style.width = `${W}px`
cv.style.height = `${H}px`
stage.append(cv)
const g = cv.getContext('2d')
g.scale(2, 2)
const layer = document.createElement('div')
layer.style.position = 'absolute'
layer.style.inset = '0'
stage.append(layer)

// ------------------------------------------------------------------------------------ the story
// One sync. Eight files are reported. H0 is equal on six, so they leave with 0 bytes read. Two are
// read: SharePoint's forecast.xlsx is a no-op save (H1 differs, H2 is equal: a green cap, no
// commit), and OneDrive's proposal.docx becomes one commit in docs/. The two stand side by side,
// either side of the gutter between their lanes, so the outcome is one image.
const REPORTED = [
  { r: 0, i: 12, j: 18, read: 'commit' },
  { r: 0, i: 4, j: 36 },
  { r: 1, i: 3, j: 20, read: 'noop' },
  { r: 1, i: 12, j: 44 },
  { r: 2, i: 6, j: 14 },
  { r: 2, i: 11, j: 39 },
  { r: 3, i: 4, j: 27 },
  { r: 3, i: 12, j: 12 },
]
const S = {
  ask: 0.15, // rings appear, lane by lane (story seconds)
  expire: 0.3, // SharePoint's since-token answers 410: expired, by design
  walk: [0.5, 1.5], // so its metadata is walked, reading 0 bytes, and its reported files ring as it passes
  restore: [1.5, 1.8], // the walk's last page stores a new token: the chip returns to neutral
  exit: [2.0, 2.25], // H0 equal on six: their rings flash green...
  settle: [2.5, 2.85], // ...and leave the field: it goes quiet again
  read: [3.4, 5.0], // two files are read: the only slow motion (1.6 s at a constant rate)
  h1: 5.3, // forecast.xlsx: H1 differs, because Office re-saved it...
  h2: 5.55, // ...and H2 is equal, because the converted page is the same
  cap: [5.6, 5.9], // so it stops there: a green cap
  lift: [6.2, 6.8], // proposal.docx's converted page lifts off its pillar
  travel: [7.25, 7.7], // and lands in the words "One commit."
  words: [7.55, 7.85],
  END: 8.0,
}
const PILLAR = 11 // a read's height, in file lengths
const key = (r, i, j) => `${r},${i},${j}`
const reportedAt = new Map(REPORTED.map((o) => [key(o.r, o.i, o.j), o]))
const docx = REPORTED[0]
const xlsx = REPORTED[2]

function tileState(s) {
  const rise = e(s, S.read[0], S.read[1], ease.walk)
  const cap = e(s, S.cap[0], S.cap[1])
  return (r, i, j) => {
    const o = reportedAt.get(key(r, i, j))
    if (!o?.read) return null
    const h = PILLAR * rise
    return h > 0.001 ? { h, cap: o.read === 'noop' ? cap : 0 } : null
  }
}

// ------------------------------------------------------------------------------------ cameras
// hz is the horizon's screen y; cx moves the vanishing point sideways (a lens shift).
function mk(c) {
  return camera({ x: c.x, y: c.y, z: c.z, yaw: c.yaw ?? 0, pitch: c.pitch, F: c.F, cx: c.cx ?? 960, cy: c.hz + c.F * Math.tan(c.pitch) })
}
function mixCam(a, b, k) {
  const o = {}
  for (const n of Object.keys(a)) o[n] = lerp(a[n], b[n] ?? a[n], k)
  return o
}
const [dX, dZ] = tileC(docx.r, docx.i, docx.j)
const [xX, xZ] = tileC(xlsx.r, xlsx.i, xlsx.j)
const CAM = {
  // The poster: a low camera, a low horizon, and the two reads standing up into the sky.
  poster: { x: -35.26, y: 4.5, z: 5.9, yaw: 0, pitch: 0.02, F: 1100, hz: 700, cx: 700 },
  posterIn: { x: -35.0, y: 4.4, z: 7.4, yaw: 0, pitch: 0.02, F: 1100, hz: 700, cx: 700 },
  // The map: high, looking down all four lanes, their names painted at the lane heads.
  mapA: { x: 0, y: 27, z: -44, yaw: 0, pitch: 0.3, F: 1000, hz: 330, cx: 960 },
  mapB: { x: 0, y: 26.4, z: -42.6, yaw: 0, pitch: 0.3, F: 1000, hz: 330, cx: 960 },
  mapC: { x: 0, y: 25.8, z: -41.2, yaw: 0, pitch: 0.3, F: 1000, hz: 330, cx: 960 },
  // The read: low and close, a 3/4 on proposal.docx from the side of the map, its lane receding.
  readA: { x: dX - 5, y: 2.5, z: dZ - 13, yaw: 0.05, pitch: 0, F: 900, hz: 760, cx: 960 },
  readB: { x: dX - 4.6, y: 2.4, z: dZ - 12.2, yaw: 0.05, pitch: 0, F: 900, hz: 760, cx: 960 },
  // The hashes: the reverse angle, from in front of forecast.xlsx, both reads below the line.
  hashesA: { x: xX, y: 5, z: xZ - 16, yaw: -0.3, pitch: 0, F: 900, hz: 700, cx: 1200 },
  hashesB: { x: xX, y: 5, z: xZ - 15.2, yaw: -0.3, pitch: 0, F: 900, hz: 700, cx: 1200 },
  hookA: { x: 0, y: 3.4, z: -8, yaw: 0, pitch: 0.1, F: 1150, hz: 470, cx: 960 },
  hookB: { x: 0, y: 3.4, z: 22, yaw: 0, pitch: 0.1, F: 1150, hz: 470, cx: 960 },
  waveA: { x: 0, y: 32, z: -46, yaw: 0, pitch: 0.42, F: 1300, hz: 395, cx: 960 },
  waveB: { x: 0, y: 29, z: -40, yaw: 0, pitch: 0.42, F: 1300, hz: 395, cx: 960 },
  expireA: { x: laneX(1), y: 6, z: -16, yaw: 0, pitch: 0.2, F: 1100, hz: 420, cx: 960 },
  expireB: { x: laneX(1), y: 5.8, z: -14.5, yaw: 0, pitch: 0.2, F: 1100, hz: 420, cx: 960 },
}
const FOG = {
  poster: { fogNear: 60, fogFar: 260 },
  map: { fogNear: 60, fogFar: 170 },
  read: { fogNear: 30, fogFar: 160 },
  hashes: { fogNear: 30, fogFar: 160 },
  hook: { fogNear: 20, fogFar: 150 },
  wave: { fogNear: 40, fogFar: 200 },
  expire: { fogNear: 40, fogFar: 150 },
}

// ------------------------------------------------------------------------------------ the type
function el(cls, style, html = '') {
  const d = document.createElement('div')
  d.className = `t ${cls}`
  Object.assign(d.style, style)
  d.innerHTML = html
  layer.append(d)
  return d
}
const mono = (s) => `<span class="mono">${s}</span>`

// The wordmark is on screen at every t. When the governing thought is not, a tagline beside it says
// what the tool does, so every 3 s window a visitor lands on says it.
const mark = el('mono', { left: '120px', top: '84px', fontSize: '36px', color: 'var(--muted)', letterSpacing: '-0.01em' }, 'agent-context-sync')
const tagline = el('', { left: '536px', top: '88px', fontSize: '32px', fontWeight: '400', color: 'var(--muted)', letterSpacing: '-0.01em' }, `keeps ${mono('docs/')} in sync with Microsoft 365, processing only what changed`)
const subject = el('', { left: '118px', top: '150px', fontSize: '44px', fontWeight: '400', lineHeight: '1.18', letterSpacing: '-0.02em', color: 'var(--ink)' }, `Keep an agent-readable ${mono('docs/')} folder in sync with<br>OneDrive, SharePoint, Outlook and Teams`)
const thought = el('', { left: '110px', top: '276px', fontSize: '118px', fontWeight: '600', lineHeight: '1.0', letterSpacing: '-0.045em', color: 'var(--ink)' }, 'by processing only<br>what changed.')
const payRead = el('', { right: '120px', left: 'auto', top: '146px', fontSize: '54px', fontWeight: '600', letterSpacing: '-0.03em', color: 'var(--amberText)', textAlign: 'right' }, 'Two files read.')
const payCommit = el('', { right: '120px', left: 'auto', top: '208px', fontSize: '54px', fontWeight: '600', letterSpacing: '-0.03em', color: 'var(--ink)', textAlign: 'right' }, 'One commit.')
for (const d of [tagline, subject]) d.querySelectorAll('.mono').forEach((m) => Object.assign(m.style, { letterSpacing: '-0.04em' }))

// One line per shot, at display scale. Explanations live in the README, right below the hero.
const LINES = {
  hook: ['Tens of thousands of files.'],
  wave: ['Re-reading everything: hours of downloads.', 72],
  ask: ['Ask each source what changed.'],
  expire: ['An expired token costs 0 bytes.'],
  decide: ['Decide before reading a byte.'],
  read: ['Read only on purpose.'],
  proof: ['Online-only stays online.'],
  hashes: ['A no-op save: one read, no commit.'],
  publish: [`What changed since Tuesday<br>is ${mono('git log')}.`, 80],
}
const lines = {}
for (const [k, [text, size]] of Object.entries(LINES)) {
  const d = el('', { left: '114px', top: '166px', fontSize: `${size ?? 84}px`, fontWeight: '600', letterSpacing: '-0.04em', color: 'var(--ink)' })
  // Words are spans so they can arrive one after another, by opacity only: rising means bytes.
  d.innerHTML = text.replace(/(<span class="mono">[^<]*<\/span>|<br>|[^\s<]+)/g, (m) => (m === '<br>' ? m : `<span class="w">${m}</span>`))
  d.style.lineHeight = '1.05'
  d.querySelectorAll('.mono').forEach((m) => Object.assign(m.style, { letterSpacing: '-0.03em', wordSpacing: '-0.3em' }))
  lines[k] = d
}

// Chips: SharePoint's since-token (FILM expire shot) and the three hashes (FILM hashes shot).
const chipStyle = { fontSize: '42px', lineHeight: '1', padding: '8px 16px 12px', borderRadius: '10px', border: '3px solid var(--faint)', background: 'var(--bg)', color: 'var(--ink)' }
const token = el('mono', { ...chipStyle, transform: 'translate(-50%, -100%)' })
const hashChips = ['H0 ≠', 'H1 ≠', 'H2 ='].map((h) => el('mono', chipStyle, h))

// The proof plate re-sets four whole lines of the recorded run in docs/media/dataless-read.webp:
// the command, and the three lines it printed. Nothing is paraphrased.
const PLATE = [
  [['$ ', 'muted'], ['readfp off blob.bin', 'ink']],
  [['policy=off(1)  before: flags=0x40000060 ', 'muted'], ['(dataless)', 'ink'], [' blocks=0', 'muted']],
  [['open=ok  ', 'muted'], ['read=Resource deadlock avoided (errno 11)', 'red'], ['  ', 'muted'], ['bytes=0', 'ink'], ['  0.000s', 'muted']],
  [['after:  flags=0x40000060 ', 'muted'], ['(dataless)', 'ink'], [' blocks=0', 'muted']],
]
const plate = el('mono', { left: '120px', top: '430px', fontSize: '38px', lineHeight: '1.6', whiteSpace: 'pre' })
const plateRuns = PLATE.map((line) => {
  const row = document.createElement('div')
  row.style.minHeight = '1.6em'
  plate.append(row)
  return line.map(([text, tone]) => {
    const span = document.createElement('span')
    span.style.color = `var(--${tone})`
    row.append(span)
    return { span, text }
  })
})
const plateNote = el('mono', { left: '120px', top: '860px', fontSize: '30px', color: 'var(--muted)' }, 'recorded run · docs/media/dataless-read.webp')

// ------------------------------------------------------------------------------------ the edits
// Film time [from, to) shows camera `cam` (or a move between two), a line of type, and story time
// s = s0 + (t - from) * rate, clamped to the story's end. `still` holds s at s0.
const LOOP = [
  { from: 0, to: 4.5, shot: 'poster', cam: ['poster'], s0: S.END, still: true },
  { from: 4.5, to: 5.9, shot: 'map', cam: ['mapA'], line: 'ask', s0: 0 },
  { from: 5.9, to: 7.3, shot: 'map', cam: ['mapA'], line: 'decide', s0: 1.8 },
  { from: 7.3, to: 10.3, shot: 'read', cam: ['readA'], line: 'read', s0: 3.2 },
  { from: 10.3, to: 15.0, shot: 'poster', cam: ['poster'], s0: 6.2 },
]
const FILM = [
  { from: 0, to: 1.6, shot: 'poster', cam: ['poster', 'posterIn'], s0: S.END, still: true },
  { from: 1.6, to: 4.4, shot: 'hook', cam: ['hookA', 'hookB'], line: 'hook', s0: -1, still: true },
  { from: 4.4, to: 7.0, shot: 'wave', cam: ['waveA', 'waveB'], line: 'wave', s0: -1, still: true },
  { from: 7.0, to: 8.4, shot: 'map', cam: ['mapA', 'mapB'], line: 'ask', s0: 0, walk: true },
  { from: 8.4, to: 10.6, shot: 'expire', cam: ['expireA', 'expireB'], line: 'expire', s0: 0.2, rate: 0.75, walk: true },
  { from: 10.6, to: 12.0, shot: 'map', cam: ['mapB', 'mapC'], line: 'decide', s0: 1.8, walk: true },
  { from: 12.0, to: 15.8, shot: 'read', cam: ['readA', 'readB'], line: 'read', s0: 3.2, rate: 0.55 },
  { from: 15.8, to: 19.8, shot: 'proof', line: 'proof' },
  { from: 19.8, to: 22.8, shot: 'hashes', cam: ['hashesA', 'hashesB'], line: 'hashes', s0: 5.2, rate: 0.27 },
  { from: 22.8, to: 26.0, shot: 'poster', cam: ['posterIn', 'poster'], line: 'publish', s0: 6.0, rate: 0.625 },
  { from: 26.0, to: 29.0, shot: 'poster', cam: ['posterIn', 'poster'], s0: S.END, still: true },
]
const EDITS = CUT === 'film' ? FILM : LOOP
const DURATION = EDITS[EDITS.length - 1].to

// ------------------------------------------------------------------------------------ drawing
function show(d, o = 1) {
  d.style.visibility = o > 0.001 ? 'visible' : 'hidden'
  d.style.opacity = o.toFixed(3)
}
function hideAll() {
  for (const d of layer.children) d.style.visibility = 'hidden'
}

// A line arrives word by word, by opacity only, starting on the cut.
function showLine(k, u) {
  const d = lines[k]
  show(d)
  d.querySelectorAll('.w').forEach((w, n) => {
    // Type is on screen from the cut; the words only finish arriving over the first 0.2 s.
    w.style.opacity = clamp01(0.55 + (u - n * 0.03) / 0.12).toFixed(3)
  })
}

// Rings: a reported file. Muted; H0-equal ones flash green, then leave the field.
function ringAt(o, n, walk) {
  if (o.r === 1 && walk) return S.walk[0] + (S.walk[1] - S.walk[0]) * clamp01(tileZ(o.j) / tileZ(50))
  return S.ask + o.r * 0.1 + n * 0.02
}
function drawRings(cam, s, lw, walk, radius) {
  REPORTED.forEach((o, n) => {
    const at = ringAt(o, n, walk)
    const appear = e(s, at, at + 0.25)
    if (appear <= 0) return
    if (o.read) {
      const alpha = appear * (1 - e(s, S.read[0], S.read[0] + 0.3, ease.leave))
      drawRing(g, cam, o.r, o.i, o.j, T.muted, alpha, radius, lw)
      return
    }
    const g0 = S.exit[0] + n * 0.03
    const green = e(s, g0, g0 + 0.2)
    const alpha = appear * (1 - e(s, S.settle[0], S.settle[1], ease.leave))
    drawRing(g, cam, o.r, o.i, o.j, green > 0.5 ? T.green : T.muted, alpha, radius, green > 0.5 ? lw * 1.4 : lw)
  })
}

// SharePoint's walk: a line crossing its lane, moving away, reading metadata only.
function drawWalk(cam, s) {
  const k = p(s, S.walk[0], S.walk[1])
  if (k <= 0 || k >= 1) return
  const z = k * tileZ(50)
  const a = cam.proj(tileX(1, 0) - 0.3, 0.02, z)
  const b = cam.proj(tileX(1, COLS - 1) + SX + 0.3, 0.02, z)
  if (!a || !b) return
  g.strokeStyle = T.muted
  g.lineWidth = 4
  g.beginPath()
  g.moveTo(a[0], a[1])
  g.lineTo(b[0], b[1])
  g.stroke()
}

// The lane names, painted at the lane heads like road markings.
function drawLaneNames(cam, only) {
  REGIONS.forEach((name, r) => {
    if (only !== undefined && only !== r) return
    drawGroundText(g, cam, name, tileX(r, 0) + (COLS * PX - 0.3) / 2, -3.6, 1.55, T.muted)
  })
}

// The counterfactual: every file rises as a faint, hollow amber outline. It is hollow so that solid
// amber height stays reserved for the two real reads.
function drawWave(cam, u) {
  g.strokeStyle = T.amber
  g.lineWidth = 1.3
  g.globalAlpha = 0.35
  g.beginPath()
  for (let j = ROWS - 1; j >= 0; j -= 1) {
    const z = tileZ(j)
    for (let r = 0; r < REGIONS.length; r += 1) {
      for (let i = 0; i < COLS; i += 1) {
        const x = tileX(r, i)
        const xn = (r * (COLS + 2.5) + i) / (4 * COLS + 7.5)
        const start = 0.3 + xn * 1.2
        const h = 1.6 * e(u, start, start + 0.45)
        if (h <= 0.01) continue
        const a = cam.proj(x, 0, z)
        if (!a || a[2] > 150) continue
        const b = cam.proj(x + SX, 0, z)
        const c = cam.proj(x + SX, 0, z + SZ)
        const d = cam.proj(x, 0, z + SZ)
        if (!b || !c || !d || Math.max(a[0], b[0]) < 0 || Math.min(a[0], b[0]) > W) continue
        const ha = (cam.V * h) / a[2]
        const hd = (cam.V * h) / d[2]
        g.moveTo(a[0], a[1] - ha)
        g.lineTo(b[0], b[1] - ha)
        g.lineTo(c[0], c[1] - hd)
        g.lineTo(d[0], d[1] - hd)
        g.closePath()
        g.moveTo(a[0], a[1])
        g.lineTo(a[0], a[1] - ha)
        g.moveTo(b[0], b[1])
        g.lineTo(b[0], b[1] - ha)
      }
    }
  }
  g.stroke()
  g.globalAlpha = 1
}

// SharePoint's since-token chip, above its lane head: delta → 410 Gone → delta (a new token).
function placeToken(cam, s) {
  const pt = cam.proj(laneX(1), 0, -6.5)
  if (!pt) return
  const back = e(s, S.restore[0], S.restore[1])
  const red = s >= S.expire && back < 0.5
  show(token, s >= S.restore[0] ? Math.abs(1 - 2 * back) * 0.5 + 0.5 : 1)
  token.textContent = red ? '410 Gone' : 'delta'
  token.style.left = `${Math.round(pt[0])}px`
  token.style.top = `${Math.round(pt[1] - 40)}px`
  token.style.color = red ? 'var(--red)' : 'var(--muted)'
  token.style.borderColor = red ? 'var(--red)' : 'var(--faint)'
  token.style.background = red ? 'var(--redFill)' : 'var(--bg)'
}

// H0, H1 and H2 on forecast.xlsx, stacked beside its pillar.
function placeHashChips(cam, s) {
  const base = cam.proj(xX, 0, xZ)
  if (!base) return
  const top = base[1] - (cam.V * PILLAR) / base[2]
  const shown = [1, e(s, S.h1, S.h1 + 0.08), e(s, S.h2, S.h2 + 0.08)]
  hashChips.forEach((d, n) => {
    show(d, shown[n])
    d.style.left = `${Math.round(base[0] + 80)}px`
    d.style.top = `${Math.round(Math.max(top, 400) + n * 92)}px`
    const eq = n === 2
    d.style.color = eq ? 'var(--green)' : 'var(--ink)'
    d.style.borderColor = eq ? 'var(--green)' : 'var(--faint)'
  })
}

// The command types at 30 ms a character; each output line prints at once, like a terminal.
const PLATE_AT = [0.5, 1.4, 1.65, 1.9]
function drawPlate(u) {
  show(plate)
  plateRuns.forEach((runs, n) => {
    let budget = n === 0 ? Math.max(0, Math.floor((u - PLATE_AT[0]) / 0.03)) : u >= PLATE_AT[n] ? Infinity : 0
    for (const run of runs) {
      const k = Math.min(run.text.length, budget)
      run.span.textContent = run.text.slice(0, k)
      budget -= k
    }
  })
  if (u > 2.4) show(plateNote)
}

// The converted page: a sheet with a markdown heading, not a file (files have a folded corner).
// Muted while it hovers; ink once it travels.
function drawPage(x, y, k, color) {
  const w = 60 * k
  const h = 76 * k
  g.lineWidth = 5 * k
  g.strokeStyle = color
  g.fillStyle = T.bg
  g.beginPath()
  g.roundRect(x - w / 2, y - h / 2, w, h, 6 * k)
  g.fill()
  g.stroke()
  g.fillStyle = color
  g.font = `700 ${Math.round(26 * k)}px "Geist Mono"`
  g.textBaseline = 'top'
  g.fillText('#', x - w / 2 + 10 * k, y - h / 2 + 8 * k)
  g.fillRect(x - w / 2 + 28 * k, y - h / 2 + 16 * k, 20 * k, 6 * k)
  for (let n = 0; n < 3; n += 1) g.fillRect(x - w / 2 + 10 * k, y - h / 2 + (38 + n * 11) * k, (n === 2 ? 22 : 40) * k, 4 * k)
}

// Where the converted page hovers: above proposal.docx's pillar, in the same corrected screen space
// the pillar is drawn in (its verticals are vertical on screen).
function hover(cam, s) {
  const base = cam.proj(dX, 0, dZ)
  if (!base) return null
  const up = e(s, S.lift[0], S.lift[1])
  return { x: base[0], y: base[1] - (cam.V * (PILLAR + 0.2 + up * 0.5)) / base[2] - 45, up }
}

function drawLiftAndTravel(cam, s, shot) {
  if (s < S.lift[0] || s >= S.travel[1] || shot === 'map') return
  const hv = hover(cam, s)
  if (!hv) return
  if (s < S.travel[0] || shot !== 'poster') {
    g.globalAlpha = clamp01(hv.up * 2.5)
    drawPage(hv.x, hv.y, 1, T.muted)
    g.globalAlpha = 1
    return
  }
  // A short, fast move into "One commit.", which writes itself as the page arrives.
  const r = payCommit.getBoundingClientRect()
  const dest = [r.left + r.width * 0.3, r.top + r.height / 2]
  const u = ease.travel(p(s, S.travel[0], S.travel[1]))
  drawPage(lerp(hv.x, dest[0], u), lerp(hv.y, dest[1], u) - Math.sin(u * Math.PI) * 60, lerp(1, 0.55, u), T.ink)
}

function seek(t) {
  const ed = EDITS.find((x) => t >= x.from && t < x.to) ?? EDITS[EDITS.length - 1]
  const u = t - ed.from
  const s = ed.still ? ed.s0 : Math.min(S.END, (ed.s0 ?? 0) + u * (ed.rate ?? 1))
  hideAll()
  g.fillStyle = T.bg
  g.fillRect(0, 0, W, H)
  show(mark)
  const governing = ed.shot === 'poster' && !ed.line
  if (!governing) show(tagline)

  if (ed.shot === 'proof') {
    showLine('proof', u)
    drawPlate(u)
    return
  }
  const [a, b] = ed.cam
  const cam = mk(b ? mixCam(CAM[a], CAM[b], ease.walk(p(t, ed.from, ed.to))) : CAM[a])
  drawPlane(g, T, cam, tileState(s), FOG[ed.shot])
  if (ed.shot === 'map') drawLaneNames(cam)
  if (ed.shot === 'expire') drawLaneNames(cam, 1)
  if (ed.shot === 'wave') drawWave(cam, u)
  if (ed.walk && (ed.shot === 'map' || ed.shot === 'expire')) drawWalk(cam, s)
  if (ed.shot === 'map' || ed.shot === 'expire' || ed.shot === 'read') drawRings(cam, s, ed.shot === 'read' ? 3 : 5, !!ed.walk, ed.shot === 'map' ? 2.1 : 1.25)
  if (ed.shot === 'expire') placeToken(cam, s)
  if (ed.shot === 'hashes') placeHashChips(cam, s)
  if (ed.line) showLine(ed.line, u)
  if (ed.shot === 'poster') {
    if (governing) {
      show(subject)
      show(thought)
    }
    show(payRead)
    show(payCommit, s >= S.END ? 1 : e(s, S.words[0], S.words[1]))
  }
  drawLiftAndTravel(cam, s, ed.shot)
}

window.__duration = DURATION
window.__seek = (t) => seek(((t % DURATION) + DURATION) % DURATION)
// Canvas text needs the web fonts loaded first; the capture waits on document.fonts.ready.
document.fonts.load('500 200px "Geist Mono"')
document.fonts.load('700 26px "Geist Mono"')
document.fonts.ready.then(() => window.__seek(Number(q.get('t') ?? 0)))
