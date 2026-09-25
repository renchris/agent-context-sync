// film/film.js: the launch film for agent-context-sync, as a pure function of time.
//
// Direction: docs/design/hero/DIRECTION.md § Launch video. Two cuts share one world and one story:
//   ?cut=loop  the README hero, an animated WebP
//   ?cut=film  the MP4 behind the link under the hero
// and two grades (?theme=dark|light), tuned to GitHub's page colours so the frame edge vanishes.
//
// Round 3 (operator verdict: "the scene is very abstract", "no 3D transitions from scene to scene"):
// the world is real documents (film/cards.js, film/world.js), and ONE camera flies through it. There
// are no cuts. A scene is a place the camera holds; a transition is the camera travelling from one
// place to the next through the same world.
//
// Structure. STORY is one sync on its own clock s, and it happens in one stretch of the field. A cut
// is an edit list: each edit holds a pose or flies between two, shows a line of type, and maps film
// time t to story time s. Each loop is the NEXT sync, one stretch further on, so the last frame is
// the first one moved down the field and nothing is ever undone.

import { THEMES, clamp01, e, ease, lerp, p } from './lib.js'
import { LANES } from './cards.js'
import { DL, DW, GIT_X, GROW, MD_SLOT, PZ, REPORTED, SR, buildWorld, docC } from './world.js'

const LANE_NAMES = LANES.map((l) => l.name)

const q = new URLSearchParams(location.search)
const THEME = q.get('theme') === 'light' ? 'light' : 'dark'
const CUT = q.get('cut') === 'film' ? 'film' : 'loop'
const T = THEMES[THEME]
for (const [k, v] of Object.entries(T)) document.documentElement.style.setProperty(`--${k}`, v)
document.documentElement.dataset.theme = THEME

const W = 1920
const H = 1080
const stage = document.getElementById('stage')
const layer = document.createElement('div')
layer.style.position = 'absolute'
layer.style.inset = '0'

// ------------------------------------------------------------------------------------ the story
// One sync, in story seconds. Eight files are reported. H0 is equal on six, so they leave with 0
// bytes read. Two are read: each is opened (it stands up, 0.45 s) and then turns amber from the top
// down as its bytes arrive, the only slow motion, 2.0 s at a constant rate. forecast.xlsx is a no-op
// save: H1 differs, H2 is equal, so it takes a green cap and stops there. proposal.docx's converted
// page leaves it and lands in docs/: one commit.
const S = {
  ask: 0.15, // rings appear, lane by lane
  exit: [2.0, 2.25], // H0 equal on six: their rings turn green...
  settle: [2.9, 3.2], // ...are held, and leave the field
  open: [3.4, 3.85],
  read: [3.7, 5.7],
  h1: 5.9,
  h2: 6.15,
  cap: [6.2, 6.5],
  peel: [6.55, 6.7], // the converted page leaves proposal.docx
  travel: [6.7, 7.5], // and crosses to docs/, with the camera following it
  land: 7.55,
  commit: [7.55, 7.9],
  END: 8.3,
}
const NONE = -1e9

function syncState(s) {
  const ring = []
  const green = []
  REPORTED.forEach((o, n) => {
    const at = S.ask + o.l * 0.22 + n * 0.02
    const appear = e(s, at, at + 0.25)
    if (o.read) {
      ring.push(appear * (1 - e(s, S.open[0], S.open[0] + 0.3, ease.leave)))
      green.push(0)
      return
    }
    const g0 = S.exit[0] + n * 0.03
    green.push(e(s, g0, g0 + 0.2))
    ring.push(appear * (1 - e(s, S.settle[0], S.settle[1], ease.leave)))
  })
  const open = e(s, S.open[0], S.open[1])
  return {
    ring,
    green,
    read: p(s, S.read[0], S.read[1]),
    stand: open,
    grow: open,
    pool: e(s, S.open[0], S.open[0] + 0.4),
    cap: e(s, S.cap[0], S.cap[1]),
    landed: s >= S.land ? 1 : 0,
    commit: e(s, S.commit[0], S.commit[1]),
  }
}
const DONE = syncState(S.END)
const BEFORE = syncState(NONE)

// The converted page, in the stretch of the sync that makes it. It leaves proposal.docx's face,
// crosses above the field and settles face up in its slot in docs/. It never rises: rising is bytes.
const DZ = SR * PZ // one stretch, in world units
function mdState(k, s) {
  if (s < S.peel[0] || s >= S.land) return null
  const prop = REPORTED[0]
  const [px, pz] = docC(prop.l, prop.i, k * SR + prop.j)
  const [sx, sz] = docC(-1, MD_SLOT.i, k * SR + MD_SLOT.j)
  const Hs = DL * GROW
  const face = { x: px, y: Hs / 2, z: pz + DL / 2 + 0.12 }
  if (s < S.peel[1]) {
    const u = e(s, S.peel[0], S.peel[1])
    return { visible: true, x: face.x, y: face.y, z: face.z + u * 0.5, rx: Math.PI / 2, scale: GROW * (1 - 0.15 * u), landed: 0 }
  }
  const u = ease.travel(p(s, S.travel[0], S.travel[1]))
  const a = { x: face.x, y: face.y, z: face.z + 0.5 }
  const c = { x: (face.x + sx) / 2, y: Hs * 0.42, z: face.z + 1.2 }
  const b = { x: sx, y: 0.03, z: sz }
  const j = 1 - u
  const bz = (k0) => j * j * a[k0] + 2 * j * u * c[k0] + u * u * b[k0]
  return { visible: true, x: bz('x'), y: bz('y'), z: bz('z'), rx: (Math.PI / 2) * (1 - u), scale: lerp(GROW * 0.85, 1, u), landed: 0 }
}

// ------------------------------------------------------------------------------------ cameras
// A pose is a position, a point it looks at, a focal length F (px) and a principal point (cx, cy).
// Poses are written for stretch 0; a pose in stretch k is the same pose moved k stretches on.
const [dX, dZ] = docC(REPORTED[0].l, REPORTED[0].i, REPORTED[0].j)
const [mX, mZ] = docC(-1, MD_SLOT.i, MD_SLOT.j)
const POSES = {
  // The poster: eye height, left of the reads, which stand on the right third either side of the gutter.
  poster: { p: [-5.4, 1.5, dZ + 9.8], at: [-4.4, 1.5, dZ], F: 1500, cx: 700, cy: 600 },
  posterIn: { p: [-5.25, 1.47, dZ + 9.2], at: [-4.25, 1.47, dZ], F: 1500, cx: 700, cy: 600 },
  // The map: above the head of the stretch the sync is in, all five lanes in view. It sits just past
  // the last sync's reads, so they are behind it.
  map: { p: [11, 13, dZ + 30.4], at: [11, 0, dZ + 4], F: 760, cx: 960, cy: 560 },
  // The read: low and close, a 3/4 from the left.
  read: { p: [-4.4, 1.25, dZ + 6.2], at: [0.2, 1.1, dZ - 0.6], F: 1250, cx: 900, cy: 600 },
  // docs/: low over the lane of converted pages and the git line, where the page lands.
  docs: { p: [-10.9, 4.0, mZ + 3.4], at: [-14.6, 0, mZ - 0.9], F: 1300, cx: 960, cy: 680 },
  // FILM only. Over the last sync's two reads, rising.
  over: { p: [-2.5, 6.5, dZ + 1.5], at: [4, 0.5, dZ - 22], F: 1100, cx: 960, cy: 540 },
  // High above the next stretch: the lanes run to the horizon.
  high: { p: [7, 24, dZ + 34.7], at: [7, 0, dZ - 24], F: 900, cx: 960, cy: 500 },
  highB: { p: [7, 12, dZ + 24], at: [7, 1, dZ - 24], F: 900, cx: 960, cy: 520 },
  // Coming down onto the eight reported files.
  mid: { p: [2, 7.5, dZ + 17], at: [3, 0, dZ - 4], F: 1000, cx: 960, cy: 540 },
  // The read, drifting in; then round the front of the pair to forecast.xlsx from the right.
  readB: { p: [-3.9, 1.22, dZ + 5.5], at: [0.3, 1.1, dZ - 0.6], F: 1250, cx: 900, cy: 600 },
  front: { p: [1.2, 1.5, dZ + 7.2], at: [0.6, 1.25, dZ], F: 1250, cx: 960, cy: 600 },
  hashes: { p: [6.2, 1.6, dZ + 5.0], at: [0.6, 1.3, dZ - 0.4], F: 1300, cx: 820, cy: 600 },
  hashesB: { p: [5.8, 1.55, dZ + 4.6], at: [0.6, 1.3, dZ - 0.4], F: 1300, cx: 820, cy: 600 },
  docsB: { p: [-11.3, 3.6, mZ + 3.0], at: [-14.6, 0, mZ - 0.9], F: 1300, cx: 960, cy: 680 },
}
function at(name, k) {
  const o = POSES[name]
  return { ...o, p: [o.p[0], o.p[1], o.p[2] - k * DZ], at: [o.at[0], o.at[1], o.at[2] - k * DZ] }
}
function toCam(c) {
  const d = [c.at[0] - c.p[0], c.at[1] - c.p[1], c.at[2] - c.p[2]]
  const yaw = Math.atan2(d[0], -d[2])
  const pitch = Math.atan2(-d[1], Math.hypot(d[0], d[2]))
  return { x: c.p[0], y: c.p[1], z: c.p[2], yaw, pitch, F: c.F, cx: c.cx, cy: c.cy }
}
// A flight: from pose a to pose b along a curve through `via` (an offset from the straight line's
// midpoint), eased so it leaves and arrives at rest.
function fly(a, b, k, via = [0, 0, 0], viaAt = [0, 0, 0]) {
  const j = 1 - k
  const bez = (A, B, off) => A.map((v, i) => j * j * v + 2 * j * k * ((v + B[i]) / 2 + off[i]) + k * k * B[i])
  return { p: bez(a.p, b.p, via), at: bez(a.at, b.at, viaAt), F: lerp(a.F, b.F, k), cx: lerp(a.cx, b.cx, k), cy: lerp(a.cy, b.cy, k) }
}
// The FILM's camera never stops until its last frame: one smooth path through key poses at key
// times. Monotone cubic (PCHIP) per component, so no key is overshot. It leaves the first key from
// rest (so frame 0 is sharp) and pushes in from frame 1; it arrives on the last key at rest and holds
// there, so the film's last frames are exactly frame 0.
const flat = (c) => [...c.p, ...c.at, c.F, c.cx, c.cy]
const unflat = (v) => ({ p: v.slice(0, 3), at: v.slice(3, 6), F: v[6], cx: v[7], cy: v[8] })
function pathCam(keys, t) {
  const P = keys.map((k) => flat(at(k.pose, k.k)))
  const n = keys.length
  const found = keys.findIndex((k, j) => j < n - 1 && t < keys[j + 1].t)
  const i = found < 0 ? n - 2 : found // past the last key: hold on it
  const slope = (a, c) => (P[a + 1][c] - P[a][c]) / (keys[a + 1].t - keys[a].t)
  const tangent = (j, c) => {
    if (j === n - 1 || j === 0) return 0
    const d0 = slope(j - 1, c)
    const d1 = slope(j, c)
    if (d0 * d1 <= 0) return 0
    const h0 = keys[j].t - keys[j - 1].t
    const h1 = keys[j + 1].t - keys[j].t
    const w1 = 2 * h1 + h0
    const w2 = h1 + 2 * h0
    return (w1 + w2) / (w1 / d0 + w2 / d1)
  }
  const h = keys[i + 1].t - keys[i].t
  const u = clamp01((t - keys[i].t) / h)
  const h00 = 2 * u ** 3 - 3 * u ** 2 + 1
  const h10 = u ** 3 - 2 * u ** 2 + u
  const h01 = -2 * u ** 3 + 3 * u ** 2
  const h11 = u ** 3 - u ** 2
  return unflat(P[i].map((v, c) => h00 * v + h10 * h * tangent(i, c) + h01 * P[i + 1][c] + h11 * h * tangent(i + 1, c)))
}

// ------------------------------------------------------------------------------------ the type
// A scrim under the headlines: the field runs up behind them in every shot but the poster.
const scrim = document.createElement('div')
Object.assign(scrim.style, { position: 'absolute', left: '0', top: '0', width: '1920px', height: '300px', background: 'linear-gradient(to bottom, var(--bg) 0, var(--bg) 120px, transparent 300px)' })
layer.append(scrim)
function el(cls, style, html = '') {
  const d = document.createElement('div')
  d.className = `t ${cls}`
  Object.assign(d.style, style)
  d.innerHTML = html
  layer.append(d)
  return d
}
const mono = (s) => `<span class="mono">${s}</span>`
const mark = el('mono', { left: '120px', top: '84px', fontSize: '36px', color: 'var(--muted)', letterSpacing: '-0.01em' }, 'agent-context-sync')
const tagline = el('', { left: '536px', top: '88px', fontSize: '32px', fontWeight: '400', color: 'var(--muted)', letterSpacing: '-0.01em' }, `keep ${mono('docs/')} in sync with Microsoft 365, processing only what changed`)
// Nothing on screen may pretend to be a working tool: the FILM's poster says what this is.
const status = el('', { left: '536px', top: '90px', fontSize: '30px', fontWeight: '400', color: 'var(--muted)', letterSpacing: '-0.01em' }, 'a design with measured probes · no implementation yet')
const subject = el('', { left: '118px', top: '150px', fontSize: '44px', fontWeight: '400', lineHeight: '1.18', letterSpacing: '-0.02em', color: 'var(--ink)' }, `Keep an agent-readable ${mono('docs/')} folder in sync with<br>OneDrive, SharePoint, Outlook and Teams`)
const thought = el('', { left: '110px', top: '276px', fontSize: '118px', fontWeight: '600', lineHeight: '1.0', letterSpacing: '-0.045em', color: 'var(--ink)' }, 'by processing only<br>what changed.')
const payoff = el('', { left: '114px', top: '566px', fontSize: '54px', fontWeight: '600', letterSpacing: '-0.03em' }, '<span style="color: var(--amberText)">Two files read.</span> <span class="commit">One commit.</span>')
for (const d of [tagline, subject]) {
  d.querySelectorAll('.mono').forEach((m) => Object.assign(m.style, { letterSpacing: '-0.04em', marginRight: '-0.12em' }))
}
const LINES = {
  hook: ['Tens of thousands of files.', 84, 'centre'],
  wall: ['Re-reading all of it: hours of downloads.', 84, 'centre'],
  match: ['Only what changed.', 96],
  ask: ['Ask each source what changed.'],
  decide: ['Decide before reading a byte.'],
  read: ['Read only on purpose.'],
  hashes: ['A no-op save: one read, no commit.'],
  publish: [`Publish it to ${mono('docs/')}, in git.`],
  agent: [`Your agent reads ${mono('docs/')}.`],
}
const lines = {}
for (const [k, [text, size, align]] of Object.entries(LINES)) {
  const pos = align === 'centre' ? { left: '0px', width: '1920px', textAlign: 'center', top: '190px' } : { left: '114px', top: '166px' }
  const d = el('', { ...pos, fontSize: `${size ?? 84}px`, fontWeight: '600', letterSpacing: '-0.04em', color: 'var(--ink)', lineHeight: '1.05' }, text)
  d.querySelectorAll('.mono').forEach((m) => Object.assign(m.style, { letterSpacing: '-0.05em' }))
  lines[k] = d
}
// Hash chips: H0 on each reported file while the sync decides; H1 and H2 on the no-op save.
const chipStyle = { fontSize: '30px', lineHeight: '1', padding: '6px 12px 9px', borderRadius: '9px', border: '3px solid var(--faint)', background: 'var(--bg)', color: 'var(--ink)' }
// Each says what the hash decided, in words: H0 whether to read, H1 whether the bytes changed, H2
// whether the converted page did.
const h0Chips = REPORTED.map((o) => el('mono', chipStyle, o.read ? 'H0 ≠ · read' : 'H0 = · skip'))
const hChips = ['H1 ≠ · new bytes', 'H2 = · same page'].map((h) => el('mono', { ...chipStyle, fontSize: '34px' }, h))
// On the poster, each read says where it came from.
const srcLabels = REPORTED.filter((o) => o.read).map((o) => el('', { fontSize: '30px', fontWeight: '500', color: 'var(--muted)', letterSpacing: '-0.01em' }, LANE_NAMES[o.l]))
// In docs/: where the converted page landed, and the commit that carries it.
const pathChip = el('mono', { ...chipStyle, fontSize: '32px', color: 'var(--ink)', borderColor: 'var(--ink)' }, 'docs/mirror/onedrive/proposal.md')
const commitChip = el('mono', { ...chipStyle, fontSize: '30px', color: 'var(--ink)' }, '+1 commit')

// ------------------------------------------------------------------------------------ the edits
// An edit holds `cam` (a pose name) or flies `fly: [from, k, to, k]`, in stretch k, and maps film
// time to story time: s = s0 + (t - from) * rate (s0 held when rate is 0). Type: `line` (a key of
// LINES) or `poster`. The LOOP's camera is locked between flights, because an animated WebP has no
// motion compensation: a still frame is nearly free and a moving one costs about 1 MB a second.
const LOOP = [
  { from: 0, to: 2.5, cam: 'poster', k: 0, s0: S.END, rate: 0, poster: true },
  { from: 2.5, to: 3.5, fly: ['poster', 0, 'map', 1], via: [0, 6, 0], viaAt: [0, -4, 0], k: 1, s0: 0, rate: 0, blur: true },
  { from: 3.5, to: 5.0, cam: 'map', k: 1, line: 'ask', s0: 0, rate: 0.75 },
  { from: 5.0, to: 6.6, cam: 'map', k: 1, line: 'decide', s0: 1.8, rate: 0.9, h0: true },
  { from: 6.6, to: 7.5, fly: ['map', 1, 'read', 1], via: [-3, 2, 0], k: 1, s0: 3.3, rate: 0, blur: true },
  // The read, then the no-op save held a full second before the camera leaves.
  { from: 7.5, to: 10.9, cam: 'read', k: 1, line: 'read', s0: 3.3, rate: 1.35, sMax: 6.54, hx: true },
  // Up and over to docs/, following the converted page, so the viewer sees where docs/ is.
  { from: 10.9, to: 11.9, fly: ['read', 1, 'docs', 1], via: [0, 3, 1.5], k: 1, s0: 6.54, rate: 1.0, blur: true },
  { from: 11.9, to: 13.3, cam: 'docs', k: 1, line: 'publish', s0: 7.54, rate: 0.55, dx: true },
  // REKEY: this flight's background is two levels off (invisible), so every pixel of the poster that
  // follows differs from the frame before it and the encoder re-sends the poster whole, near-lossless.
  // Otherwise a region that stopped changing mid-flight (the faded tagline) keeps its lossy pixels.
  { from: 13.3, to: 14.3, fly: ['docs', 1, 'poster', 1], via: [1.5, 1, 1.5], k: 1, s0: S.END, rate: 0, blur: true, rekey: true },
  { from: 14.3, to: 15.0, cam: 'poster', k: 1, s0: S.END, rate: 0, poster: true },
]
// The FILM: one camera path that never stops until it arrives back on frame 0. The edits here only
// carry the story clock and the type; the camera comes from FILM_PATH.
const FILM = [
  { from: 0, to: 2.4, k: 0, s0: S.END, rate: 0, poster: true, typeIn: null },
  { from: 2.4, to: 6.2, k: 1, s0: -1, rate: 0, line: 'hook' },
  // The counterfactual, captioned as one: every file in view read again, row after row.
  { from: 6.2, to: 9.7, k: 1, s0: -1, rate: 0, line: 'wall' },
  // It never happens: the wall falls away, and only what changed is left.
  { from: 9.7, to: 12.7, k: 1, s0: -0.3, rate: 1.25, line: 'match', h0: true },
  { from: 12.7, to: 16.2, k: 1, s0: 3.45, rate: 0.686, line: 'read' },
  { from: 16.2, to: 19.2, k: 1, s0: 5.85, rate: 0.24, line: 'hashes', hx: true },
  { from: 19.2, to: 20.6, k: 1, s0: 6.57, rate: 0.7 },
  { from: 20.6, to: 23.1, k: 1, s0: 7.55, rate: 0.3, line: 'agent', dx: true },
  { from: 23.1, to: 26.0, k: 1, s0: S.END, rate: 0, poster: true, typeIn: [24.8, 25.4], typeOut: null },
]
const FILM_PATH = [
  { t: 0, pose: 'poster', k: 0 },
  { t: 2.4, pose: 'posterIn', k: 0 },
  { t: 4.0, pose: 'over', k: 0 },
  { t: 6.2, pose: 'high', k: 1 },
  { t: 9.7, pose: 'highB', k: 1 },
  { t: 12.2, pose: 'mid', k: 1 },
  { t: 13.6, pose: 'read', k: 1 },
  { t: 15.8, pose: 'readB', k: 1 },
  { t: 17.0, pose: 'front', k: 1 },
  { t: 18.4, pose: 'hashes', k: 1 },
  { t: 19.3, pose: 'hashesB', k: 1 },
  { t: 20.6, pose: 'docs', k: 1 },
  { t: 23.1, pose: 'docsB', k: 1 },
  { t: 25.3, pose: 'poster', k: 1 },
]
// The wall: when row j of the next stretch stands (0..1). Each file stands at a read's own pace,
// rows following one another from the lens to the horizon; then all of it falls away at once.
const WALL0 = SR // the first row of stretch 1
function wallAt(t) {
  const u = t - 6.2
  const fall = 1 - e(t, 9.7, 10.25, ease.leave)
  if (u <= 0 || fall <= 0.001) return null
  return (j) => {
    const d = (j - WALL0) / 70
    return clamp01((u - 0.2 - 2.6 * d ** 0.85) / 0.45) * fall
  }
}
const EDITS = CUT === 'film' ? FILM : LOOP
const DURATION = EDITS[EDITS.length - 1].to
const SHUTTER = Number(q.get('shutter') ?? 1 / 120)
const SUBS = Number(q.get('subs') ?? (CUT === 'film' ? 4 : 6))

// ------------------------------------------------------------------------------------ one frame
// Built once the web fonts are in, because every document's texture is drawn with them.
let world = null

function editAt(t) {
  return EDITS.find((x) => t >= x.from && t < x.to) ?? EDITS[EDITS.length - 1]
}
function storyAt(ed, t) {
  return Math.min(ed.sMax ?? S.END, ed.s0 + (t - ed.from) * (ed.rate ?? 0))
}
function camAt(ed, t) {
  if (CUT === 'film') return pathCam(FILM_PATH, t)
  if (ed.cam) return at(ed.cam, ed.k)
  const [a, ka, b, kb] = ed.fly
  const k = ease.travel(p(t, ed.from, ed.to))
  return fly(at(a, ka), at(b, kb), k, ed.via, ed.viaAt)
}
// Stretch state: every stretch before the current one is done, every one after has not started.
function stateFn(k, s) {
  const cur = s < -0.5 ? BEFORE : syncState(s)
  return (kk) => (kk < k ? DONE : kk > k ? BEFORE : cur)
}

function applyWorld(t) {
  const ed = editAt(t)
  const s = storyAt(ed, t)
  const c = toCam(camAt(ed, t))
  world.pose(c)
  const wall = CUT === 'film' ? wallAt(t) : null
  world.update(stateFn(ed.k, s), { fog: [30, 150], md: mdState(ed.k, s), ghostWall: wall, ghostFrom: WALL0, rekey: !!ed.rekey })
  return { ed, s }
}

function show(d, o = 1) {
  d.style.visibility = o > 0.001 ? 'visible' : 'hidden'
  d.style.opacity = o.toFixed(3)
}
function hideAll() {
  for (const d of layer.children) d.style.visibility = 'hidden'
}
let posterK = 0
function showType(x, k) {
  if (!x || k <= 0.001) return
  if (x.poster) {
    show(subject, k)
    show(thought, k)
    show(payoff, k)
    if (CUT === 'film') show(status, k)
    posterK = Math.max(posterK, k)
  } else if (x.line) show(lines[x.line], k)
}
function typeAt(t) {
  hideAll()
  show(mark)
  posterK = 0
  document.documentElement.style.setProperty('--bg', editAt(t).rekey ? T.bgRekey : T.bg)
  const ed = editAt(t)
  const i = EDITS.indexOf(ed)
  const prev = EDITS[i - 1]
  const next = EDITS[i + 1]
  const u = p(t, ed.from, ed.to)
  if (CUT === 'film') {
    // Each edit's type fades in over its first 0.4 s and out over its last, unless told otherwise.
    const tin = ed.typeIn === undefined ? [ed.from, ed.from + 0.4] : ed.typeIn
    const tout = ed.typeOut === undefined ? [ed.to - 0.4, ed.to] : ed.typeOut
    showType(ed, (tin ? e(t, tin[0], tin[1], ease.travel) : 1) * (tout ? 1 - e(t, tout[0], tout[1], ease.travel) : 1))
  } else if (ed.fly) {
    // Across a flight the old line leaves over its first 40 % and the new one arrives over its last.
    const out = 1 - e(u, 0, 0.4, ease.travel)
    const inn = e(u, 0.6, 1, ease.travel)
    showType(prev, out)
    showType(next, inn)
    if (!(prev?.poster && out > 0.001) && !(next?.poster && inn > 0.001)) show(tagline)
  } else {
    showType(ed, 1)
    if (!ed.poster) show(tagline)
  }
  show(scrim, 1 - posterK)
  // The labels belong to whichever poster is on screen, which during a flight is the neighbour's.
  if (posterK > 0.001) placeSources(ed.poster ? ed : prev?.poster ? prev : next, posterK)
  const s = storyAt(ed, t)
  if (ed.h0) placeH0(ed, s)
  if (ed.hx) placeHx(ed, s)
  if (ed.dx) placeDx(ed, s)
  payoff.querySelector('.commit').style.color = ed.k > 0 || s >= S.END ? 'var(--ink)' : 'var(--muted)'
}
function chipAt(d, x, y, z, o, dx = 44, dy = -22) {
  const pt = world.project(x, y, z)
  if (!pt) return
  show(d, o)
  d.style.left = `${Math.round(pt[0] + dx)}px`
  d.style.top = `${Math.round(pt[1] + dy)}px`
}
function placeH0(ed, s) {
  REPORTED.forEach((o, n) => {
    const [x, z] = docC(o.l, o.i, ed.k * SR + o.j)
    const o1 = e(s, S.exit[0] - 0.25 + n * 0.03, S.exit[0] - 0.05 + n * 0.03) * (1 - e(s, S.settle[0], S.settle[1], ease.leave))
    if (o1 <= 0.001) return
    const d = h0Chips[n]
    d.style.color = o.read ? 'var(--ink)' : 'var(--green)'
    d.style.borderColor = o.read ? 'var(--faint)' : 'var(--green)'
    chipAt(d, x, 0, z, o1, o.read === 'commit' ? -236 : 30, -18)
  })
}
function placeSources(ed, k) {
  REPORTED.filter((o) => o.read).forEach((o, n) => {
    const [x, z] = docC(o.l, o.i, ed.k * SR + o.j)
    const d = srcLabels[n]
    const pt = world.project(x, 0, z + DL / 2)
    if (!pt) return
    show(d, k)
    d.style.left = `${Math.round(pt[0] - d.offsetWidth / 2)}px`
    d.style.top = `${Math.round(pt[1] + 14)}px`
  })
}
function placeDx(ed, s) {
  const a = e(s, S.land, S.land + 0.25)
  if (a <= 0.001) return
  const [x, z] = docC(-1, MD_SLOT.i, ed.k * SR + MD_SLOT.j)
  chipAt(pathChip, x + DW / 2, 0, z - DL / 2, a, 26, -30)
  chipAt(commitChip, GIT_X, 0, z, e(s, S.commit[0], S.commit[1]), -210, -24)
}
function placeHx(ed, s) {
  const o = REPORTED[1]
  const [x, z] = docC(o.l, o.i, ed.k * SR + o.j)
  const top = DL * GROW
  const a = [e(s, S.h1, S.h1 + 0.08), e(s, S.h2, S.h2 + 0.08)]
  hChips.forEach((d, n) => {
    if (a[n] <= 0.001) return
    const eq = n === 1
    d.style.color = eq ? 'var(--green)' : 'var(--ink)'
    d.style.borderColor = eq ? 'var(--green)' : 'var(--faint)'
    chipAt(d, x + DW * GROW * 0.5, top * (0.78 - n * 0.2), z + DL / 2, a[n], 20, -24)
  })
}

function seek(t) {
  const ed = editAt(t)
  // The FILM's camera never stops, so every FILM frame is blurred; the LOOP only blurs its flights.
  if ((ed.blur || CUT === 'film') && SUBS > 1) {
    const subs = Array.from({ length: SUBS }, (_, k) => () => applyWorld(Math.max(ed.from, Math.min(ed.to - 1e-4, t + ((k + 0.5) / SUBS - 0.5) * SHUTTER))))
    world.render(subs)
  } else {
    applyWorld(t)
    world.render(null)
  }
  applyWorld(t) // leave the camera at t for the type's projections
  typeAt(t)
}

// A pose can be overridden from the URL while composing: ?pose=name:{"p":[...],...}
if (q.get('pose')) {
  const [name, json] = q.get('pose').split(/:(.*)/s)
  Object.assign(POSES[name], JSON.parse(json))
}

window.__duration = DURATION
// What the encoder needs to know about the cut: where the camera is flying (film-render.sh encodes
// those frames lossy, at 20 fps, and the held frames for crisp type).
// The poster holds are encoded near-lossless: frame 0 is the most-seen frame, and the loop's seam
// is exact only if the poster decodes the same both times it is shown.
window.__meta = {
  cut: CUT,
  duration: DURATION,
  flights: EDITS.filter((x) => x.fly).map((x) => [x.from, x.to]),
  nearLossless: EDITS.filter((x) => x.poster && x.cam).map((x) => [x.from, x.to]),
}
const seekAny = (t) => seek(((t % DURATION) + DURATION) % DURATION)
Promise.all(['400 26px "Geist"', '500 26px "Geist"', '600 26px "Geist"', '500 26px "Geist Mono"', '600 26px "Geist Mono"', '700 26px "Geist Mono"'].map((f) => document.fonts.load(f))).then(() => document.fonts.ready).then(() => {
  world = buildWorld(T, { width: W, height: H })
  stage.append(world.renderer.domElement)
  stage.append(layer)
  window.__seek = seekAny
  window.__ready = true
  window.__seek(Number(q.get('t') ?? 0))
})
