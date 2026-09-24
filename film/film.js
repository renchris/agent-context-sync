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
import { COLS, PX, REGIONS, ROWS, SX, SZ, camera, drawBox, drawGroundText, drawPlane, drawRing, mix, tileC, tileX, tileZ } from './world.js'

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
// commit), and OneDrive's proposal.docx becomes one commit in docs/. The two stand either side of
// the gutter between their lanes, on the same row, so the outcome is one image and both reads (one
// object each) stand at the same height on screen.
const REPORTED = [
  { r: 0, i: 14, j: 18, read: 'commit' },
  { r: 0, i: 4, j: 36 },
  { r: 1, i: 1, j: 18, read: 'noop' },
  { r: 1, i: 12, j: 44 },
  { r: 2, i: 6, j: 14 },
  { r: 2, i: 11, j: 39 },
  { r: 3, i: 4, j: 27 },
  { r: 3, i: 12, j: 12 },
]
const S = {
  ask: 0.15, // rings appear, lane by lane (story seconds)
  exit: [2.0, 2.25], // H0 equal on six: their rings flash green...
  settle: [2.9, 3.2], // ...are held, and leave the field: it goes quiet again
  read: [3.4, 5.0], // two files are read: the only slow motion (1.6 s at a constant rate)
  // forecast.xlsx was re-saved with no edit. The measured case is a LibreOffice round trip, which
  // rewrites styles and every sheet, so H1 differs; the converted page is the same, so H2 is equal.
  h1: 5.3,
  h2: 5.55,
  cap: [5.6, 5.9], // so it stops there: a green cap
  lift: [6.2, 6.8], // proposal.docx's converted page lifts off its pillar
  travel: [7.25, 7.7], // and lands beside the words "One commit."
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
  const cap = e(s, S.cap[0], S.cap[1], ease.walk)
  return (r, i, j) => {
    const o = reportedAt.get(key(r, i, j))
    if (!o?.read) return null
    const h = PILLAR * rise
    return h > 0.001 ? { h, cap: o.read === 'noop' ? cap : 0 } : null
  }
}
// Each read lights the pages round its base. The light comes up as the read starts and then holds:
// a pool that brightened with every frame of the rise would repaint the whole ground it covers on
// every frame, which in the loop was 1.7 MB of a 2.7 MB file.
function poolsAt(s) {
  const k = e(s, S.read[0], S.read[0] + 0.4)
  if (k <= 0) return []
  return [docx, xlsx].map((o) => {
    const [x, z] = tileC(o.r, o.i, o.j)
    return { x, z, k }
  })
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
  // The poster: a camera at eye height in OneDrive's lane, and the two reads, equal and unlabelled,
  // standing up into the sky on the right third. The lanes run to a vanishing point under the words.
  poster: { x: dX - 11.1, y: 2.5, z: dZ - 20.4, yaw: 0, pitch: 0.02, F: 1100, hz: 680, cx: 700 },
  posterIn: { x: dX - 10.9, y: 2.45, z: dZ - 18.9, yaw: 0, pitch: 0.02, F: 1100, hz: 680, cx: 700 },
  // The map: high, looking down all four lanes, their names painted at the lane heads.
  mapA: { x: 0, y: 27, z: -44, yaw: 0, pitch: 0.3, F: 1000, hz: 330, cx: 960 },
  // The read: low and close, a 3/4 on proposal.docx from the side of the map, its lane receding.
  readA: { x: dX - 5, y: 2.5, z: dZ - 13, yaw: 0.05, pitch: 0, F: 900, hz: 760, cx: 960 },
  readB: { x: dX - 4.6, y: 2.4, z: dZ - 12.2, yaw: 0.05, pitch: 0, F: 900, hz: 760, cx: 960 },
  // The hashes: the reverse angle, from in front of forecast.xlsx, both reads below the line.
  hashesA: { x: xX, y: 5, z: xZ - 16, yaw: -0.3, pitch: 0, F: 900, hz: 700, cx: 1200 },
  hashesB: { x: xX, y: 5, z: xZ - 15.2, yaw: -0.3, pitch: 0, F: 900, hz: 700, cx: 1200 },
  hookA: { x: 0, y: 3.4, z: -8, yaw: 0, pitch: 0.1, F: 1150, hz: 470, cx: 960 },
  hookB: { x: 0, y: 3.4, z: 22, yaw: 0, pitch: 0.1, F: 1150, hz: 470, cx: 960 },
  // The wall and the match cut share one move: C continues B at the same speed, so the cut changes
  // the world and nothing else.
  waveA: { x: 0, y: 20, z: -34, yaw: 0, pitch: 0.3, F: 1200, hz: 360, cx: 960 },
  waveB: { x: 0, y: 18, z: -29, yaw: 0, pitch: 0.3, F: 1200, hz: 360, cx: 960 },
  waveC: { x: 0, y: 15.83, z: -23.57, yaw: 0, pitch: 0.3, F: 1200, hz: 360, cx: 960 },
}
const FOG = {
  poster: { fogNear: 60, fogFar: 260 },
  map: { fogNear: 60, fogFar: 170 },
  read: { fogNear: 30, fogFar: 160 },
  hashes: { fogNear: 30, fogFar: 160 },
  hook: { fogNear: 20, fogFar: 150 },
  wall: { fogNear: 40, fogFar: 200 },
  match: { fogNear: 40, fogFar: 200 },
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

// The wordmark is on the poster, and on every LOOP shot. In the LOOP, when the governing thought is
// not on screen, a tagline beside the wordmark says what the design does, so every 3 s window a
// visitor lands on says it. The FILM is reached from a link that already names it, so its shots
// between the two posters carry no header at all.
const mark = el('mono', { left: '120px', top: '84px', fontSize: '36px', color: 'var(--muted)', letterSpacing: '-0.01em' }, 'agent-context-sync')
const tagline = el('', { left: '536px', top: '88px', fontSize: '32px', fontWeight: '400', color: 'var(--muted)', letterSpacing: '-0.01em' }, `keep ${mono('docs/')} in sync with Microsoft 365, processing only what changed`)
// Nothing on screen may pretend to be a working tool: the FILM's poster says what this is.
const status = el('', { left: '536px', top: '90px', fontSize: '30px', fontWeight: '400', color: 'var(--muted)', letterSpacing: '-0.01em' }, 'a design with measured probes · no implementation yet')
const subject = el('', { left: '118px', top: '150px', fontSize: '44px', fontWeight: '400', lineHeight: '1.18', letterSpacing: '-0.02em', color: 'var(--ink)' }, `Keep an agent-readable ${mono('docs/')} folder in sync with<br>OneDrive, SharePoint, Outlook and Teams`)
const thought = el('', { left: '110px', top: '276px', fontSize: '118px', fontWeight: '600', lineHeight: '1.0', letterSpacing: '-0.045em', color: 'var(--ink)' }, 'by processing only<br>what changed.')
// The outcome is the third tier of the sentence, so the reads stand unlabelled. "One commit." is on
// screen from the cut, so the converted page has somewhere to land.
const payoff = el('', { left: '114px', top: '566px', fontSize: '54px', fontWeight: '600', letterSpacing: '-0.03em' }, '<span style="color: var(--amberText)">Two files read.</span> <span class="commit">One commit.</span>')
const payCommit = payoff.querySelector('.commit')
for (const d of [tagline, subject]) {
  d.querySelectorAll('.mono').forEach((m) => Object.assign(m.style, { letterSpacing: '-0.04em', marginRight: '-0.12em' }))
}

// One line per shot, at display scale; the FILM has three, and leaves its two quietest shots
// without type. Explanations live in the README, right below the hero.
const LINES = {
  hook: ['Tens of thousands of files.', 84, 'centre'],
  match: ['Only what changed.', 96],
  ask: ['Ask each source what changed.'],
  decide: ['Decide before reading a byte.'],
  read: ['Read only on purpose.'],
  hashes: ['A no-op save: one read, no commit.'],
}
const lines = {}
for (const [k, [text, size, align]] of Object.entries(LINES)) {
  const at = align === 'centre' ? { left: '0px', width: '1920px', textAlign: 'center', top: '190px' } : { left: '114px', top: '166px' }
  const d = el('', { ...at, fontSize: `${size ?? 84}px`, fontWeight: '600', letterSpacing: '-0.04em', color: 'var(--ink)' })
  // Words are spans so they can arrive one after another, by opacity only: rising means bytes.
  d.innerHTML = text.replace(/(<span class="mono">[^<]*<\/span>|<br>|[^\s<]+)/g, (m) => (m === '<br>' ? m : `<span class="w">${m}</span>`))
  d.style.lineHeight = '1.05'
  lines[k] = d
}

// The three hashes on forecast.xlsx (FILM hashes shot).
const chipStyle = { fontSize: '42px', lineHeight: '1', padding: '8px 16px 12px', borderRadius: '10px', border: '3px solid var(--faint)', background: 'var(--bg)', color: 'var(--ink)' }
const hashChips = ['H0 ≠', 'H1 ≠', 'H2 ='].map((h) => el('mono', chipStyle, h))

// ------------------------------------------------------------------------------------ the edits
// Film time [from, to) shows camera `cam` (or a move between two), a line of type, and story time
// s = s0 + (t - from) * rate, clamped to the story's end. `still` holds s at s0. A move runs at
// constant speed, so it can continue across a cut; `arrive` decelerates it into its last key.
const LOOP = [
  { from: 0, to: 3.0, shot: 'poster', cam: ['poster'], s0: S.END, still: true },
  { from: 3.0, to: 5.5, shot: 'map', cam: ['mapA'], line: 'ask', s0: 0, rate: 0.72 },
  { from: 5.5, to: 8.0, shot: 'map', cam: ['mapA'], line: 'decide', s0: 1.8, rate: 0.6 },
  { from: 8.0, to: 11.8, shot: 'read', cam: ['readA'], line: 'read', s0: 3.3, rate: 0.75 },
  { from: 11.8, to: 15.0, shot: 'poster', cam: ['poster'], s0: 6.2 },
]
// The FILM in four acts: the problem, set centred; the turn, set left, as a match cut from a wall of
// re-reading to the two reads that are all a sync costs; one mechanism, the no-op save; the poster.
// The rise is cut on the action, from the match shot into the close one, so story time never rewinds.
const FILM = [
  { from: 0, to: 2.2, shot: 'poster', cam: ['poster', 'posterIn'], s0: S.END, still: true },
  { from: 2.2, to: 5.6, shot: 'hook', cam: ['hookA', 'hookB'], line: 'hook', s0: -1, still: true },
  { from: 5.6, to: 9.1, shot: 'wall', cam: ['waveA', 'waveB'], s0: -1, still: true },
  { from: 9.1, to: 12.9, shot: 'match', cam: ['waveB', 'waveC'], line: 'match', s0: 0, rate: 1.04 },
  { from: 12.9, to: 16.2, shot: 'read', cam: ['readA', 'readB'], s0: 3.96, rate: 0.45 },
  { from: 16.2, to: 19.4, shot: 'hashes', cam: ['hashesA', 'hashesB'], line: 'hashes', s0: 5.2, rate: 0.27 },
  { from: 19.4, to: 25.0, shot: 'poster', cam: ['posterIn', 'poster'], s0: 6.0, rate: 0.8, move: 'arrive' },
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

// Rings: a reported file. Muted; H0-equal ones flash green, are held, then leave the field.
function drawRings(cam, s, lw, radius) {
  REPORTED.forEach((o, n) => {
    const at = S.ask + o.r * 0.22 + n * 0.02
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

// The lane names, painted at the lane heads like road markings, at 13 CSS px or more.
function drawLaneNames(cam) {
  REGIONS.forEach((name, r) => {
    drawGroundText(g, cam, name, tileX(r, 0) + (COLS * PX - 0.3) / 2, -3.8, 2.3, T.muted)
  })
}

// The counterfactual: re-reading everything. Every file rises at the one rate a read has, row by
// row from the lens toward the horizon, into a wall that is still growing when the shot cuts away.
// It is dimmer than a real read, because it is the cost the design never pays.
const WALL_COLS = []
for (let r = 0; r < REGIONS.length; r += 1) for (let i = 0; i < COLS; i += 1) WALL_COLS.push(tileX(r, i))
function drawWall(cam, u) {
  const rate = PILLAR / (S.read[1] - S.read[0])
  const cols = [...WALL_COLS].sort((a, b) => Math.abs(b + SX / 2 - cam.x) - Math.abs(a + SX / 2 - cam.x))
  for (let j = ROWS - 1; j >= 0; j -= 1) {
    const z = tileZ(j)
    const h = Math.min(PILLAR, (u - 0.25 - 2.1 * (j / 110) ** 0.85) * rate)
    if (h <= 0.02) continue
    const c = cam.proj(0, 0, z)
    if (!c) continue
    const fog = clamp01((c[2] - 40) / 160)
    if (fog > 0.985) continue
    const colours = {
      front: mix(T.amber, T.bg, 0.5 + fog * 0.5),
      side: mix(T.amberSide, T.bg, 0.62 + fog * 0.38),
      top: mix(T.amberTop, T.bg, 0.15 + fog * 0.85),
    }
    for (const x of cols) {
      const a = cam.proj(x, 0, z)
      if (!a || a[0] < -80 || a[0] > W + 80) continue
      drawBox(g, cam, [x, x + SX, z, z + SZ], 0, h, colours)
    }
  }
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

// Where it lands: a slot just after "One commit.", at cap height, where it stays as the commit's
// mark. So the poster, frame 0, carries the page beside the words.
const PAGE_AT_REST = 0.5
function slot() {
  const r = payCommit.getBoundingClientRect()
  return [r.right + 20 + (60 * PAGE_AT_REST) / 2, r.top + r.height * 0.52]
}

// The page descends in one fast move, with no hop: rising means bytes, and a commit is not a read. It never overlaps a glyph, and it is never removed: it becomes the mark.
function drawLiftAndTravel(cam, s) {
  if (s < S.lift[0]) return
  if (s >= S.travel[1]) {
    const [x, y] = slot()
    drawPage(x, y, PAGE_AT_REST, T.ink)
    return
  }
  const hv = hover(cam, s)
  if (!hv) return
  if (s < S.travel[0]) {
    g.globalAlpha = clamp01(hv.up * 2.5)
    drawPage(hv.x, hv.y, 1, T.muted)
    g.globalAlpha = 1
    return
  }
  // Down past the pillar's face first, then across under the governing thought: a quadratic whose
  // corner sits below the page, so the path clears every glyph above the payoff.
  const [x, y] = slot()
  const k = ease.travel(p(s, S.travel[0], S.travel[1]))
  const j = 1 - k
  drawPage(j * j * hv.x + 2 * k * j * hv.x + k * k * x, j * j * hv.y + 2 * k * j * y + k * k * y, lerp(1, PAGE_AT_REST, k), T.ink)
}

function seek(t) {
  const ed = EDITS.find((x) => t >= x.from && t < x.to) ?? EDITS[EDITS.length - 1]
  const u = t - ed.from
  const s = ed.still ? ed.s0 : Math.min(S.END, (ed.s0 ?? 0) + u * (ed.rate ?? 1))
  hideAll()
  g.fillStyle = T.bg
  g.fillRect(0, 0, W, H)
  const poster = ed.shot === 'poster'
  if (CUT === 'loop' || poster) show(mark)
  if (CUT === 'loop' && !poster) show(tagline)
  // The FILM's status line joins the poster once the page has landed, so the page never crosses it.
  if (CUT === 'film' && poster) show(status, s >= S.END ? 1 : e(s, S.words[0], S.words[1]))

  const [a, b] = ed.cam
  const k = p(t, ed.from, ed.to)
  const cam = mk(b ? mixCam(CAM[a], CAM[b], ed.move === 'arrive' ? 1 - (1 - k) ** 3 : k) : CAM[a])
  const wall = ed.shot === 'wall'
  drawPlane(g, T, cam, wall ? () => null : tileState(s), { ...FOG[ed.shot], pools: wall ? [] : poolsAt(s) })
  if (wall) drawWall(cam, u)
  if (ed.shot === 'map') drawLaneNames(cam)
  if (ed.shot === 'map' || ed.shot === 'match') drawRings(cam, s, 5, 2.1)
  if (ed.shot === 'read') drawRings(cam, s, 3, 1.25)
  if (ed.shot === 'hashes') placeHashChips(cam, s)
  if (ed.line) showLine(ed.line, u)
  if (poster) {
    show(subject)
    show(thought)
    show(payoff)
    payCommit.style.color = mix(T.muted, T.ink, s >= S.END ? 1 : e(s, S.words[0], S.words[1]))
    drawLiftAndTravel(cam, s)
  }
}

window.__duration = DURATION
window.__seek = (t) => seek(((t % DURATION) + DURATION) % DURATION)
// Canvas text needs the web fonts loaded first; the capture waits on document.fonts.ready.
document.fonts.load('500 200px "Geist Mono"')
document.fonts.load('700 26px "Geist Mono"')
document.fonts.ready.then(() => window.__seek(Number(q.get('t') ?? 0)))
