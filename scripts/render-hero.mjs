#!/usr/bin/env node
// Render the README hero loop → docs/media/hero-dark.svg / hero-light.svg.
//
// Usage:  npm run hero          — (re)render both SVGs
//         npm run hero:check    — exit 1 if the committed SVGs are stale
//
// Design: docs/design/hero/DIRECTION.md (tempo means cost, the way colour does).
// One source renders both GitHub colour modes; palette values come from
// docs/design/VISUAL.md. All text is outlined at build time from Geist Sans
// and Geist Mono (SIL OFL 1.1), because GitHub's image proxy blocks web fonts
// inside <img>. Only transform, opacity and stroke-dashoffset animate, and
// every animation shares one period T, so the loop is exact.
import opentype from 'opentype.js'
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const ROOT = fileURLToPath(new URL('../', import.meta.url))
const OUT = join(ROOT, 'docs/media')
const CHECK = process.argv.includes('--check')

// ── palette (docs/design/VISUAL.md) ─────────────────────────────────────────
const PALETTE = {
  dark: {
    text: '#e6edf3', muted: '#8b949e', faint: '#6e7681',
    'neutral-bg': '#161b22', 'neutral-fg': '#3d444d',
    'amber-bg': '#2d2111', 'amber-fg': '#f0a33a',
    'green-bg': '#0f2a19', 'green-fg': '#3fb950',
    'red-bg': '#2d1417', 'red-fg': '#ff7b72',
    field: '#3d444d', chip: '#f0a33a',
  },
  light: {
    text: '#1f2328', muted: '#59636e', faint: '#818b98',
    'neutral-bg': '#f6f8fa', 'neutral-fg': '#d1d9e0',
    'amber-bg': '#fff1dc', 'amber-fg': '#b35900',
    'green-bg': '#dafbe1', 'green-fg': '#1a7f37',
    'red-bg': '#ffebe9', 'red-fg': '#cf222e',
    field: '#afb8c1', chip: '#c86a00',
  },
}

// ── type: outlined glyphs, defined once, placed with <use> ─────────────────
const FONT_FILES = {
  sans: 'geist-sans/files/geist-sans-latin-400-normal.woff',
  sansMedium: 'geist-sans/files/geist-sans-latin-500-normal.woff',
  sansSemi: 'geist-sans/files/geist-sans-latin-600-normal.woff',
  mono: 'geist-mono/files/geist-mono-latin-400-normal.woff',
  monoMedium: 'geist-mono/files/geist-mono-latin-500-normal.woff',
  monoSemi: 'geist-mono/files/geist-mono-latin-600-normal.woff',
}
const FONTS = Object.fromEntries(
  Object.entries(FONT_FILES).map(([key, file]) => {
    const buf = readFileSync(join(ROOT, 'node_modules/@fontsource', file))
    return [key, opentype.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength))]
  }),
)
const FONT_IDS = { sans: 'a', sansMedium: 'b', sansSemi: 'c', mono: 'm', monoMedium: 'n', monoSemi: 'o' }

const round = (n, d = 2) => {
  const s = Number(n.toFixed(d)).toString()
  return s === '-0' ? '0' : s
}

// Glyph outlines in font units (y up); each text run flips them with scale(k,-k).
const glyphDefs = new Map()
function glyphRef(fontKey, glyph) {
  const id = `${FONT_IDS[fontKey]}${glyph.index}`
  if (!glyphDefs.has(id)) {
    const d = glyph.getPath(0, 0, FONTS[fontKey].unitsPerEm).toPathData(0)
    // getPath at size = unitsPerEm yields font units with y already flipped
    // (screen space). Store as-is; runs scale by size / unitsPerEm.
    glyphDefs.set(id, d)
  }
  return id
}

function layoutRun(fontKey, str, size, tracking = 0) {
  const font = FONTS[fontKey]
  const k = size / font.unitsPerEm
  const glyphs = font.stringToGlyphs(str)
  let x = 0
  const placed = []
  glyphs.forEach((g, i) => {
    if (g.index === 0 && str[i] !== ' ') throw new Error(`missing glyph for ${JSON.stringify(str[i])} in ${fontKey}`)
    if (g.getPath(0, 0, 1).commands.length) placed.push([glyphRef(fontKey, g), x])
    if (i < glyphs.length - 1) x += g.advanceWidth + font.getKerningValue(g, glyphs[i + 1]) + tracking * font.unitsPerEm
    else x += g.advanceWidth
  })
  return { placed, width: x * k, k }
}

function measure(fontKey, str, size, tracking = 0) {
  return layoutRun(fontKey, str, size, tracking).width
}

// A run of text as <use> references. anchor: start | middle | end.
function text(fontKey, str, x, y, size, { fill, anchor = 'start', attrs = '', tracking = 0 } = {}) {
  const { placed, width, k } = layoutRun(fontKey, str, size, tracking)
  const x0 = anchor === 'middle' ? x - width / 2 : anchor === 'end' ? x - width : x
  const uses = placed.map(([id, gx]) => `<use href="#${id}" x="${Math.round(gx)}"/>`).join('')
  const fillAttr = fill ? ` fill="${fill}"` : ''
  return `<g${fillAttr}${attrs ? ' ' + attrs : ''}><g transform="translate(${round(x0)} ${round(y)}) scale(${round(k, 5)})">${uses}</g></g>`
}

// Several styled runs laid end to end on one baseline.
function spans(parts, x, y, { attrs = '' } = {}) {
  let cx = x
  const out = []
  for (const { font, str, size, fill, tracking = 0, dx = 0 } of parts) {
    cx += dx
    out.push(text(font, str, cx, y, size, { fill, tracking }))
    cx += measure(font, str, size, tracking)
  }
  return `<g${attrs ? ' ' + attrs : ''}>${out.join('')}</g>`
}

// ── time: one master period, keyframes built from beat lists ───────────────
const T = 30 // seconds; every animation below has this duration
const EASE = {
  settle: 'cubic-bezier(0.16, 1, 0.3, 1)',
  leave: 'cubic-bezier(0.7, 0, 0.84, 0)',
  travel: 'cubic-bezier(0.65, 0, 0.35, 1)',
  walk: 'linear',
}
const pct = (t) => {
  if (t < 0 || t > T) throw new Error(`beat ${t}s outside [0, ${T}]`)
  return `${round((t / T) * 100, 4)}%`
}

// track(name, prop, stops): stops = [[t, value, ease?], ...] in time order.
// The ease on a stop governs the segment that starts there. The first and
// last stops are pinned to 0 and T with the element's resting value, so the
// seam is exact and reduced motion (no animation) shows the rest frame.
const keyframes = []
let animCount = 0
function track(prop, rest, stops, fmt = (v) => v) {
  const name = `k${(animCount++).toString(36)}`
  const all = [[0, rest], ...stops, [T, rest]]
  for (let i = 1; i < all.length; i++) {
    if (all[i][0] < all[i - 1][0]) throw new Error(`${name}: stops out of order at ${all[i][0]}s`)
  }
  // The implicit last segment runs from the final stop back to the resting
  // value at T. If the value differs, that is a slow ramp nobody asked for
  // (it once re-lit a commit dot over 27 s); end such tracks with a hold at
  // T - 0.001 so the change is a jump at the seam, where it is invisible.
  const last = all[all.length - 2]
  if (JSON.stringify(last[1]) !== JSON.stringify(rest) && T - last[0] > 0.01)
    throw new Error(`${name}: ${prop} drifts from ${JSON.stringify(last[1])} at ${last[0]}s back to rest over ${round(T - last[0])}s; add a hold`)
  const body = all
    .map(([t, v, ease]) => {
      const e = ease ? ` animation-timing-function: ${EASE[ease] ?? ease};` : ''
      return `${pct(t)} { ${prop}: ${fmt(v)};${e} }`
    })
    .join(' ')
  keyframes.push(`@keyframes ${name} { ${body} }`)
  return `style="animation-name:${name}"`
}
const fade = (rest, stops) => track('opacity', rest, stops, (v) => round(v, 3))
const move = (stops) =>
  track('transform', [0, 0], stops, ([x, y, s]) =>
    `translate(${round(x)}px, ${round(y)}px)${s !== undefined ? ` scale(${round(s, 4)})` : ''}`,
  )
const scaleY = (rest, stops) => track('transform', rest, stops, (v) => `scale(1, ${round(v, 4)})`)
const draw = (len, stops) => track('stroke-dashoffset', len, stops, (v) => round(v))

// ── scene ───────────────────────────────────────────────────────────────────
const W = 830
const H = 440

// The message, verbatim from README.md (line 3 and the numbered key line).
// The headline breaks on phrase boundaries, so "by processing only what
// changed." sits on its own line directly above the picture of it.
const HEADLINE = [
  ['Keep an agent-readable ', ['docs/'], [' folder in sync with', -2]],
  ['OneDrive, SharePoint, Outlook and Teams'],
  ['by processing only what changed.'],
]
const STEPS = [
  'Ask each source what changed, and make expiry cheap',
  'Decide with three hashes before reading a byte',
  'Read bytes only on purpose',
  'Publish a pure function of the source, in git',
]

// Beats, in seconds (docs/design/hero/DIRECTION.md § Time). The rest frame is
// the OUTCOME of a sync (answer first): the loop opens on it, clears it in one
// "next sync" beat, replays how it was reached, and holds it again to T.
const BEAT = {
  next: [2.0, 2.5, 2.9], // next sync: the git line slides (2.0–2.5), the outcome clears (2.5–2.9)
  ring: 3.5, // step 1: the sources answer with what changed
  expire: 4.0, // SharePoint's token has expired (410)
  walk: [4.5, 5.5], // …so its metadata is walked, zero bytes
  exits: [7.5, 7.8, 8.1, 8.4, 8.7, 9.0], // step 2: H0 equal, free exits
  exitHold: 0.7, // a free exit flashes green, then falls back into the grey field
  reads: [
    [11.0, 12.6],
    [12.6, 14.2],
  ], // step 3: the only two reads, the only amber, the only slow motion
  cutoff: 15.5, // step 4: H2 equal on the .xlsx: same page, no commit
  travel: [17.0, 17.15, 17.8, 18.0], // H2 differs on the .docx: its page goes to git (down, across, up)
  write: [17.4, 18.0], // the line is drawn as the page arrives
  landed: [18.0, 18.15], // the page becomes the new commit
  label: [18.1, 18.4],
}
// Which key-line step is in focus, from each time on (null = all four).
const FOCUS = [
  [3.0, 0],
  [7.0, 1],
  [10.5, 2],
  [15.0, 3],
  [18.5, null],
]

// The field: 256 objects, 64 per source, 8 × 8 cells on a 12 px pitch.
const SOURCES = ['OneDrive', 'SharePoint', 'Outlook', 'Teams']
const FIELD = { x: 24, y: 320, blockGap: 136, pitch: 12, cell: 9 }
const cellAt = (block, row, col) => [
  FIELD.x + block * FIELD.blockGap + col * FIELD.pitch,
  FIELD.y + row * FIELD.pitch,
]
const WALK_X = [FIELD.x + FIELD.blockGap - 2, FIELD.x + FIELD.blockGap + 7 * FIELD.pitch + FIELD.cell + 2]
const walkCross = (col) => {
  const [a, b] = BEAT.walk
  const x = FIELD.x + FIELD.blockGap + col * FIELD.pitch + FIELD.cell / 2
  return a + ((x - WALK_X[0]) / (WALK_X[1] - WALK_X[0])) * (b - a)
}
// The eight objects a sync reports, and what happens to each. The .docx sits
// on the bottom row so its page can travel to git under the field without
// crossing another source's objects.
const OBJECTS = [
  { at: [0, 2, 5], seen: BEAT.ring, fate: 'h0' },
  { at: [0, 7, 2], seen: BEAT.ring, fate: 'commit', read: 0 }, // a .docx that really changed
  { at: [1, 2, 6], seen: walkCross(6), fate: 'h0' },
  { at: [1, 4, 3], seen: walkCross(3), fate: 'h2', read: 1 }, // an .xlsx no-op save
  { at: [2, 0, 2], seen: BEAT.ring + 0.1, fate: 'h0' },
  { at: [2, 6, 5], seen: BEAT.ring + 0.1, fate: 'h0' },
  { at: [3, 3, 1], seen: BEAT.ring + 0.2, fate: 'h0' },
  { at: [3, 5, 6], seen: BEAT.ring + 0.2, fate: 'h0' },
]
{
  let n = 0
  for (const o of OBJECTS) if (o.fate === 'h0') o.exit = BEAT.exits[n++]
}

// The right-hand column, the fifth slot of the block rhythm: the meter (the
// stage's headline), a two-line ledger, and the git line on the last row.
const COL = { x: 568, meter: 337, ledger: [358, 376], git: 408 }
const GIT = { clipX: 620, clipEnd: 812, dots: [640, 680, 720, 760, 800], next: 840, r: 4 }

function scene(c, variant) {
  const out = []
  const dim = variant === 'dark' ? 0.55 : 0.65 // both clear 4.5:1 (measured)
  const [nSlide, nClear, nDone] = BEAT.next
  // An outcome element: shown at rest, cleared by the next sync, rebuilt at `on`.
  const outcome = (on, dur = 0.3, ease = 'settle') => [[nClear, 1, 'travel'], [nDone, 0], [on, 0, ease], [on + dur, 1]]

  // Governing thought: static, full contrast at every t.
  HEADLINE.forEach((line, i) =>
    out.push(
      spans(
        // A string is Sans; [id] is a Mono identifier; [str, dx] is Sans
        // shifted by dx (the Mono slash's right bearing leaves a wide gap).
        line.map((part) =>
          typeof part === 'string'
            ? { font: 'sansSemi', str: part, size: 28, fill: c.text, tracking: -0.01 }
            : part.length === 1
              ? { font: 'monoSemi', str: part[0], size: 26, fill: c.text, tracking: -0.04 }
              : { font: 'sansSemi', str: part[0], size: 28, fill: c.text, tracking: -0.01, dx: part[1] },
        ),
        24,
        50 + i * 36,
      ),
    ),
  )

  // Key line: the step in focus stays bright; the others dim.
  STEPS.forEach((l, i) => {
    const stops = []
    let prev = 1
    for (const [t, f] of FOCUS) {
      const v = f === null || f === i ? 1 : dim
      if (v !== prev) stops.push([t, prev, 'travel'], [t + 0.3, v])
      prev = v
    }
    const y = 170 + i * 30
    out.push(
      `<g ${fade(1, stops)}>${text('mono', String(i + 1), 24, y, 18, { fill: c.text })}${text('sans', l, 54, y, 20, { fill: c.text })}</g>`,
    )
  })

  // Field: static cells, one def reused 256 times.
  out.push(
    `<defs><rect id="cell" width="${FIELD.cell}" height="${FIELD.cell}" rx="2" fill="${c['neutral-bg']}" stroke="${c.field}" stroke-width="1"/></defs>`,
  )
  const cells = []
  SOURCES.forEach((name, b) => {
    const [bx] = cellAt(b, 0, 0)
    out.push(text('mono', name, bx, FIELD.y - 12, 12, { fill: c.muted }))
    const dotX = bx + measure('mono', name, 12) + 9
    const dotY = FIELD.y - 16
    out.push(`<circle cx="${round(dotX)}" cy="${dotY}" r="3" fill="${c.muted}"/>`)
    if (name === 'SharePoint') {
      // Expire: the since-token turns red and says 410, until the walk ends.
      const red = [
        [BEAT.expire, 0, 'settle'],
        [BEAT.expire + 0.15, 1],
        [BEAT.walk[1], 1, 'travel'],
        [BEAT.walk[1] + 0.4, 0],
      ]
      out.push(`<circle cx="${round(dotX)}" cy="${dotY}" r="3.5" fill="${c['red-fg']}" opacity="0" ${fade(0, red)}/>`)
      out.push(text('mono', '410', dotX + 8, FIELD.y - 12, 12, { fill: c['red-fg'], attrs: `opacity="0" ${fade(0, red)}` }))
    }
    for (let r = 0; r < 8; r++)
      for (let col = 0; col < 8; col++) {
        const [x, y] = cellAt(b, r, col)
        cells.push(`<use href="#cell" x="${x + 0.5}" y="${y + 0.5}"/>`)
      }
  })
  out.push(`<g>${cells.join('')}</g>`)

  // Walk: a line crosses SharePoint's block at constant speed, reading nothing.
  {
    const [a, b] = BEAT.walk
    const vis = [[a - 0.1, 0, 'settle'], [a, 1], [b, 1, 'travel'], [b + 0.2, 0]]
    const dx = WALK_X[1] - WALK_X[0]
    out.push(
      `<g opacity="0" ${fade(0, vis)}><g transform="translate(${WALK_X[0]} ${FIELD.y - 3})"><line x1="0" y1="0" x2="0" y2="${8 * FIELD.pitch + 3}" stroke="${c.muted}" stroke-width="1.5" ${move([[a, [0, 0], 'walk'], [b, [dx, 0]], [b + 0.2, [dx, 0]], [b + 0.21, [0, 0]]])}/></g></g>`,
    )
  }

  // Each reported object: a muted ring (free) → amber if read (paid) → a green
  // frame if it exits free, or bare amber once its page is in git.
  const frame = (x, y, stroke, width, rest, stops) => {
    const g = 1.5 + width / 2
    return `<rect x="${x - g + 0.5}" y="${y - g + 0.5}" width="${FIELD.cell + 2 * g}" height="${FIELD.cell + 2 * g}" rx="3" fill="none" stroke="${stroke}" stroke-width="${width}" opacity="${rest}" ${fade(rest, stops)}/>`
  }
  for (const o of OBJECTS) {
    const [x, y] = cellAt(...o.at)
    const off = o.fate === 'h0' ? o.exit : o.fate === 'h2' ? BEAT.cutoff : BEAT.landed[0]
    out.push(frame(x, y, c.muted, 1.5, 0, [[o.seen, 0, 'settle'], [o.seen + 0.2, 1], [off, 1, 'leave'], [off + 0.3, 0]]))
    if (o.fate === 'h0')
      out.push(
        frame(x, y, c['green-fg'], 1.5, 0, [
          [o.exit, 0, 'settle'],
          [o.exit + 0.3, 1],
          [o.exit + 0.3 + BEAT.exitHold, 1, 'leave'],
          [o.exit + 0.8 + BEAT.exitHold, 0],
        ]),
      )
    if (o.fate === 'h2') out.push(frame(x, y, c['green-fg'], 2, 1, outcome(BEAT.cutoff)))
    if (o.read !== undefined) {
      // Read: amber rises through the cell at a constant rate. Bytes, once
      // paid for, stay amber through the outcome; only the next sync clears them.
      const [a, b] = BEAT.reads[o.read]
      const rise = scaleY(1, [[nDone, 1], [nDone + 0.01, 0], [a, 0, 'walk'], [b, 1]])
      const keep = fade(1, [[nClear, 1, 'travel'], [nDone, 0], [nDone + 0.02, 0], [nDone + 0.03, 1]])
      out.push(
        `<g ${keep}><g transform="translate(${x} ${y + FIELD.cell + 1})"><rect y="${-FIELD.cell - 1}" width="${FIELD.cell + 1}" height="${FIELD.cell + 1}" rx="2.5" fill="${c.chip}" ${rise}/></g></g>`,
      )
    }
  }

  // Meter: the stage's headline, one unit. It rests on the outcome, 2 of 256.
  {
    const [r1, r2] = BEAT.reads.map(([, end]) => end)
    const xf = 0.15
    const states = [
      ['0 of 256', c.text, 0, [[nClear, 0, 'travel'], [nClear + 0.3, 1], [r1, 1, 'travel'], [r1 + xf, 0]]],
      ['1 of 256', c.chip, 0, [[r1, 0, 'travel'], [r1 + xf, 1], [r2, 1, 'travel'], [r2 + xf, 0]]],
      ['2 of 256', c.chip, 1, [[nClear, 1, 'travel'], [nClear + 0.3, 0], [r2, 0, 'travel'], [r2 + xf, 1]]],
    ]
    for (const [str, fill, rest, stops] of states)
      out.push(text('mono', str, COL.x, COL.meter, 24, { fill, attrs: `opacity="${rest}" ${fade(rest, stops)}` }))
    out.push(text('mono', 'read', COL.x + measure('mono', '0 of 256 ', 24), COL.meter, 24, { fill: c.muted }))
  }

  // Ledger: two lines. During the story each line first carries the step's
  // event, then settles on the fact the outcome keeps. Identifiers are Mono
  // (Geist Sans draws H0 as "HO"); 410 is the only red word.
  {
    const m = (str, fill = c.muted) => ({ font: 'mono', str, size: 13, fill })
    const t = (str) => ({ font: 'sans', str, size: 13, fill: c.muted })
    const lines = [
      // [parts, line, rest, stops]
      [[m('410', c['red-fg']), t(': metadata walk, 0 bytes read')], 0, 0, [[BEAT.expire, 0, 'settle'], [BEAT.expire + 0.3, 1], [7.0, 1, 'travel'], [7.3, 0]]],
      [[m('H0'), t(' equal on 6 of 8: 0 bytes read')], 0, 1, outcome(BEAT.exits[0])],
      [[m('H0'), t(' differs on 2: budgeted reads')], 1, 0, [[BEAT.reads[0][0], 0, 'settle'], [BEAT.reads[0][0] + 0.3, 1], [15.0, 1, 'travel'], [15.3, 0]]],
      [[m('H2'), t(' equal on 1 of 2: page unchanged')], 1, 1, outcome(BEAT.cutoff)],
    ]
    for (const [parts, line, rest, stops] of lines) {
      const width = parts.reduce((w, p) => w + measure(p.font, p.str, p.size), 0)
      if (width > W - 24 - COL.x) throw new Error(`ledger line too wide (${round(width)} px): ${parts.map((p) => p.str).join('')}`)
      out.push(spans(parts, COL.x, COL.ledger[line], { attrs: `opacity="${rest}" ${fade(rest, stops)}` }))
    }
  }

  // Git line: a conveyor. The rest frame shows the last sync's commit, bright,
  // at x = 800. The next sync slides the line one spacing left (the oldest
  // commit leaves under the clip) and that commit settles into history; this
  // sync's commit is written at the same place, so the frame at T is the frame
  // at 0.
  {
    const y = COL.git
    const [wa, wb] = BEAT.write
    const [la, lb] = BEAT.landed
    const end = T - 0.001
    const spacing = GIT.next - GIT.dots[4]
    out.push(text('mono', 'docs/', COL.x, y + 4, 12, { fill: c.muted }))
    out.push(
      `<defs><clipPath id="gitclip"><rect x="${GIT.clipX}" y="${y - 12}" width="${GIT.clipEnd - GIT.clipX}" height="24"/></clipPath></defs>`,
    )
    const belt = move([[nSlide, [0, 0], 'travel'], [nSlide + 0.5, [-spacing, 0]], [end, [-spacing, 0]]])
    const dot = (x, stroke, rest, stops, fill = c['neutral-bg']) =>
      `<circle cx="${x}" cy="${y}" r="${GIT.r}" fill="${fill}" stroke="${stroke}" stroke-width="1.5"${stops ? ` opacity="${rest}" ${fade(rest, stops)}` : ''}/>`
    const parts = [
      `<line x1="${GIT.clipX - spacing - 20}" y1="${y}" x2="${GIT.dots[4]}" y2="${y}" stroke="${c.field}" stroke-width="1.5"/>`,
      `<line x1="${GIT.dots[4]}" y1="${y}" x2="${GIT.next}" y2="${y}" stroke="${c.field}" stroke-width="1.5" stroke-dasharray="${spacing}" stroke-dashoffset="${spacing}" ${draw(spacing, [[wa, spacing, 'settle'], [wb, 0], [end, 0]])}/>`,
      // the oldest commit fades as it slides under the clip, rather than being cut in half
      dot(GIT.dots[0], c.muted, 1, [[nSlide, 1, 'travel'], [nSlide + 0.35, 0], [end, 0]]),
      ...GIT.dots.slice(1).map((x) => dot(x, c.muted)),
      // the last sync's commit: bright at rest, settling as the line slides
      dot(GIT.dots[4], c.text, 1, [[nSlide, 1, 'travel'], [nSlide + 0.5, 0], [end, 0]], 'none'),
      // this sync's commit
      dot(GIT.next, c.muted, 0, [[la, 0, 'settle'], [lb, 1], [end, 1]]),
      dot(GIT.next, c.text, 0, [[la, 0, 'settle'], [lb, 1], [end, 1]], 'none'),
    ]
    out.push(`<g clip-path="url(#gitclip)"><g ${belt}>${parts.join('')}</g></g>`)
    out.push(
      text('mono', '+1 commit', W - 24, y - 12, 12, {
        fill: c.text,
        anchor: 'end',
        attrs: `opacity="1" ${fade(1, [[nSlide, 1, 'travel'], [nSlide + 0.3, 0], [BEAT.label[0], 0, 'settle'], [BEAT.label[1], 1]])}`,
      }),
    )
  }

  // Travel: the converted page (text colour, not amber: it is markdown, not
  // source bytes) leaves the .docx cell, runs under the field and rises into
  // the git line as its segment is drawn.
  {
    const docx = OBJECTS.find((o) => o.fate === 'commit')
    const [x, y] = cellAt(...docx.at)
    const cx = x + 0.5 + FIELD.cell / 2
    const cy = y + 0.5 + FIELD.cell / 2
    const [a, down, across, b] = BEAT.travel
    const under = 426 - cy
    const dx = GIT.dots[4] - cx
    const dy = COL.git - cy
    out.push(
      `<g opacity="0" ${fade(0, [[a - 0.1, 0], [a, 1], [BEAT.landed[0], 1, 'travel'], [BEAT.landed[1], 0]])}><g transform="translate(${round(cx)} ${round(cy)})"><rect x="-4" y="-4" width="8" height="8" rx="1.5" fill="${c.text}" ${move([[a, [0, 0], 'travel'], [down, [0, under], 'travel'], [across, [dx, under], 'settle'], [b, [dx, dy]], [BEAT.landed[1], [dx, dy]], [BEAT.landed[1] + 0.01, [0, 0]]])}/></g></g>`,
    )
  }

  return out
}

// ── assemble ────────────────────────────────────────────────────────────────
function render(variant) {
  glyphDefs.clear()
  keyframes.length = 0
  animCount = 0
  const c = PALETTE[variant]
  const body = scene(c, variant).join('\n')
  const defs = [...glyphDefs].map(([id, d]) => `<path id="${id}" d="${d}"/>`).join('')
  const css = [
    `*{animation-duration:${T}s;animation-iteration-count:infinite;animation-timing-function:linear;animation-fill-mode:both}`,
    ...keyframes,
    '@media (prefers-reduced-motion: reduce){*{animation:none!important}}',
  ].join('\n')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">
<style>
${css}
</style>
<defs>${defs}</defs>
${body}
</svg>
`
}

if (!existsSync(OUT)) mkdirSync(OUT, { recursive: true })
let stale = 0
for (const variant of ['dark', 'light']) {
  const svg = render(variant)
  const path = join(OUT, `hero-${variant}.svg`)
  if (CHECK) {
    if (!existsSync(path) || readFileSync(path, 'utf8') !== svg) {
      console.error(`STALE: docs/media/hero-${variant}.svg — run \`npm run hero\``)
      stale++
    }
  } else {
    writeFileSync(path, svg)
    console.log(`rendered docs/media/hero-${variant}.svg (${(svg.length / 1024).toFixed(1)} KB)`)
  }
}
if (CHECK) {
  if (stale) process.exit(1)
  console.log('hero SVGs up to date')
}

