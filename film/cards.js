// film/cards.js: what a document looks like. Every file in the world is a card drawn here, onto a 2D
// canvas that becomes its texture: a page with a folded corner, its name, and a preview that says what
// kind of thing it is (a Word page, a spreadsheet, a deck, a PDF, an email, a chat, a markdown page).
//
// A file the sync has only seen as METADATA shows its name and a faint outline of its body: we know
// what it is, not what it says. A file whose bytes were READ turns amber, top to bottom, as the bytes
// arrive. Colour means cost (docs/design/VISUAL.md): amber is the only loud colour.

import { rng } from './lib.js'

export const CARD_W = 400
export const CARD_H = 512

// The corpus. Names are invented but ordinary; no product logos, the sources are named in type.
export const LANES = [
  {
    name: 'OneDrive',
    files: [
      ['proposal.docx', 'Northwind renewal'], ['budget-fy27.xlsx', 'Finance'], ['roadmap.pptx', '12 slides'], ['contract-draft.pdf', '14 pages'],
      ['interview-notes.docx', 'Hiring'], ['pricing-model.xlsx', 'v3'], ['kickoff.pptx', '8 slides'], ['travel-policy.pdf', '6 pages'],
      ['onboarding.docx', 'People'], ['headcount.xlsx', 'Q4'], ['q3-review.pptx', '21 slides'], ['meeting-notes.docx', 'Mon 9:30'],
    ],
  },
  {
    name: 'SharePoint',
    files: [
      ['forecast.xlsx', 'Sales · Q3'], ['board-deck.pptx', '18 slides'], ['handbook.pdf', '42 pages'], ['design-spec.docx', 'Platform'],
      ['pipeline.xlsx', 'Sales'], ['security-review.docx', 'IT'], ['brand-guide.pdf', '30 pages'], ['launch-plan.pptx', '9 slides'],
      ['vendor-list.xlsx', 'Ops'], ['minutes-0912.docx', 'Leadership'], ['runbook.docx', 'Support'], ['org-chart.pdf', '2 pages'],
    ],
  },
  {
    name: 'Outlook',
    files: [
      ['RE: renewal terms', 'Priya Shah'], ['Offsite agenda', 'Marcus Lee'], ['Q3 numbers', 'Dana Ortiz'], ['Invoice 4471', 'Accounts'],
      ['FW: security review', 'IT Desk'], ['Lunch Thursday?', 'Sam Wu'], ['Contract signed', 'Legal'], ['Board prep', 'Elena Ruiz'],
      ['Standup notes', 'Team'], ['Re: pricing', 'Priya Shah'], ['Welcome aboard', 'People'], ['Travel booked', 'Travel'],
    ],
    kind: 'eml',
  },
  {
    name: 'Teams',
    files: [
      ['#sales', 'Dana: new forecast is up'], ['#launch', 'Marcus: date moved'], ['#design', 'Elena: spec v2'], ['#support', 'Sam: ticket 881'],
      ['#finance', 'Priya: invoices done'], ['#general', 'Welcome, Ana'], ['#eng', 'Lee: deploy at 4'], ['#hiring', 'Dana: 3 onsites'],
      ['#legal', 'Contract is back'], ['#ops', 'Vendor call moved'], ['#marketing', 'Brand refresh'], ['#random', 'Coffee?'],
    ],
    kind: 'chat',
  },
]
// docs/: one converted page per source file (docs/mirror/<source>/) and the curated pages
// (docs/topics/). proposal.md is not here: it appears only in its own slot, where a sync lands it.
export const DOCS_FILES = [
  ['budget-fy27.md', 'mirror/onedrive'], ['roadmap.md', 'mirror/onedrive'], ['contract-draft.md', 'mirror/onedrive'], ['interview-notes.md', 'mirror/onedrive'],
  ['pricing-model.md', 'mirror/onedrive'], ['kickoff.md', 'mirror/onedrive'], ['travel-policy.md', 'mirror/onedrive'], ['onboarding.md', 'mirror/onedrive'],
  ['forecast.md', 'mirror/sharepoint'], ['board-deck.md', 'mirror/sharepoint'], ['handbook.md', 'mirror/sharepoint'], ['design-spec.md', 'mirror/sharepoint'],
  ['pipeline.md', 'mirror/sharepoint'], ['security-review.md', 'mirror/sharepoint'], ['brand-guide.md', 'mirror/sharepoint'], ['launch-plan.md', 'mirror/sharepoint'],
  ['renewal-terms.md', 'mirror/outlook'], ['offsite-agenda.md', 'mirror/outlook'], ['q3-numbers.md', 'mirror/outlook'], ['invoice-4471.md', 'mirror/outlook'],
  ['contract-signed.md', 'mirror/outlook'], ['board-prep.md', 'mirror/outlook'], ['standup-notes.md', 'mirror/outlook'], ['welcome-aboard.md', 'mirror/outlook'],
  ['sales.md', 'mirror/teams'], ['launch.md', 'mirror/teams'], ['design.md', 'mirror/teams'], ['support.md', 'mirror/teams'],
  ['finance.md', 'mirror/teams'], ['eng.md', 'mirror/teams'], ['hiring.md', 'mirror/teams'], ['ops.md', 'mirror/teams'],
  ['northwind.md', 'topics/clients'], ['contoso.md', 'topics/clients'], ['pricing.md', 'topics/decisions'], ['q3-plan.md', 'topics/decisions'],
  ['launch-date.md', 'topics/decisions'], ['vendors.md', 'topics/workstreams'], ['security.md', 'topics/workstreams'], ['INDEX.md', 'docs/'],
]

export function kindOf(lane, name) {
  if (lane.kind) return lane.kind
  return name.split('.').pop()
}

function card(g, T, w, h, { fill, edge, lw = 5 }) {
  const r = 18
  const dog = 70
  g.beginPath()
  g.moveTo(r, 0)
  g.lineTo(w - dog, 0)
  g.lineTo(w, dog)
  g.lineTo(w, h - r)
  g.arcTo(w, h, w - r, h, r)
  g.lineTo(r, h)
  g.arcTo(0, h, 0, h - r, r)
  g.lineTo(0, r)
  g.arcTo(0, 0, r, 0, r)
  g.closePath()
  g.fillStyle = fill
  g.fill()
  g.lineWidth = lw
  g.strokeStyle = edge
  g.stroke()
  // The fold.
  g.beginPath()
  g.moveTo(w - dog, 0)
  g.lineTo(w - dog, dog - r * 0.2)
  g.quadraticCurveTo(w - dog, dog, w - dog + r * 0.2, dog)
  g.lineTo(w, dog)
  g.closePath()
  g.fillStyle = edge
  g.fill()
}

// The preview body of each kind, drawn in one colour into the box [x, y, w, h].
function body(g, kind, x, y, w, h, c, seed, strong) {
  const R = rng(seed)
  g.fillStyle = c
  g.strokeStyle = c
  const bar = (bx, by, bw, bh = 12) => g.fillRect(Math.round(bx), Math.round(by), Math.round(bw), bh)
  if (kind === 'docx' || kind === 'pdf') {
    let yy = y
    if (kind === 'pdf') {
      g.globalAlpha *= 0.55
      g.fillRect(x, yy, w, 110)
      g.globalAlpha /= 0.55
      yy += 132
    } else {
      bar(x, yy, w * 0.62, 22)
      yy += 48
    }
    while (yy < y + h - 12) {
      const para = 2 + Math.floor(R() * 3)
      for (let k = 0; k < para && yy < y + h - 12; k += 1) {
        bar(x, yy, k === para - 1 ? w * (0.35 + R() * 0.4) : w * (0.9 + R() * 0.1))
        yy += 24
      }
      yy += 14
    }
  } else if (kind === 'xlsx') {
    const cols = 4
    const rows = 9
    const cw = w / cols
    const rh = Math.min(34, h / rows)
    g.lineWidth = 3
    for (let r = 0; r <= rows; r += 1) g.fillRect(x, y + r * rh, w, 3)
    for (let k = 0; k <= cols; k += 1) g.fillRect(x + k * cw - (k === cols ? 3 : 0), y, 3, rows * rh)
    for (let r = 0; r < rows; r += 1) {
      for (let k = 0; k < cols; k += 1) {
        if (r === 0) bar(x + k * cw + 10, y + 11, cw * 0.55, 12)
        else if (k === 0) bar(x + 10, y + r * rh + 12, cw * (0.4 + R() * 0.4), 10)
        else bar(x + k * cw + cw - 12 - cw * (0.3 + R() * 0.35), y + r * rh + 12, cw * (0.3 + R() * 0.35), 10)
      }
    }
  } else if (kind === 'pptx') {
    const sh = w * 0.5625
    g.lineWidth = 4
    g.strokeRect(x + 2, y + 2, w - 4, sh - 4)
    bar(x + 22, y + 24, w * 0.5, 18)
    const n = 5
    for (let k = 0; k < n; k += 1) {
      const bh = (sh - 90) * (0.3 + R() * 0.7)
      g.fillRect(x + 26 + k * ((w - 60) / n), y + sh - 22 - bh, (w - 60) / n - 14, bh)
    }
    const th = (w - 20) / 3 * 0.5625
    for (let k = 0; k < 3; k += 1) g.strokeRect(x + k * ((w + 10) / 3) + 2, y + sh + 22, (w - 20) / 3 - 4, th)
  } else if (kind === 'eml') {
    // An email: sender and date, the subject, then the message.
    g.beginPath()
    g.arc(x + 20, y + 20, 20, 0, Math.PI * 2)
    g.fill()
    bar(x + 54, y + 6, w * 0.4, 12)
    bar(x + 54, y + 28, w * 0.25, 10)
    g.fillRect(x, y + 58, w, 3)
    let yy = y + 82
    while (yy < y + h - 12) {
      bar(x, yy, w * (0.7 + R() * 0.3))
      yy += 24
      if (R() < 0.25) yy += 14
    }
  } else if (kind === 'chat') {
    // A channel: messages, each an avatar and a bubble.
    let yy = y
    let k = 0
    while (yy < y + h - 60) {
      const mine = k % 3 === 2
      const bw = w * (0.45 + R() * 0.35)
      const bh = 34 + Math.floor(R() * 2) * 24
      if (!mine) {
        g.beginPath()
        g.arc(x + 16, yy + 16, 16, 0, Math.PI * 2)
        g.fill()
      }
      const bx = mine ? x + w - bw : x + 44
      g.globalAlpha *= strong ? 1 : 0.6
      g.beginPath()
      g.roundRect(bx, yy, bw, bh, 14)
      g.fill()
      g.globalAlpha /= strong ? 1 : 0.6
      yy += bh + 22
      k += 1
    }
  } else if (kind === 'md') {
    g.font = '700 44px "Geist Mono"'
    g.textBaseline = 'top'
    g.fillText('#', x, y - 4)
    bar(x + 44, y + 8, w * 0.5, 20)
    let yy = y + 64
    let n = 0
    while (yy < y + h - 12) {
      if (n === 4) {
        g.fillText('##', x, yy - 2)
        bar(x + 60, yy + 6, w * 0.35, 14)
        yy += 44
      } else {
        bar(x, yy, w * (n % 3 === 2 ? 0.55 : 0.92 + R() * 0.08))
        yy += 24
      }
      n += 1
    }
  }
}

/**
 * Draw one document. spec = { lane, name, sub, seed }. state:
 *   read   0..1  how much of the file's bytes have arrived (0 = metadata only)
 *   ghost  true  the counterfactual: a read that never happens, drawn hollow
 *   md     true  a converted markdown page in docs/
 */
export function drawDoc(g, T, spec, { read = 0, ghost = false, md = false, landed = 0, reported = false } = {}) {
  const w = CARD_W
  const h = CARD_H
  g.clearRect(0, 0, w, h)
  g.save()
  const kind = md ? 'md' : kindOf(LANES[spec.lane] ?? {}, spec.name)
  if (ghost) {
    // The counterfactual: a file read for nothing. Solid, so the wall is a wall, and a step dimmer
    // than a real read, because it is the cost the design never pays.
    card(g, T, w, h, { fill: T.ghostFill, edge: T.amber, lw: 10 })
    g.globalAlpha = 0.45
    body(g, 'docx', 36, 118, w - 72, h - 150, T.bg, spec.seed, false)
    g.restore()
    return
  }
  const pad = 36
  const top = 118
  // Metadata: the card, its name and what it is. Free, and always known.
  // In docs/, the page a sync just published is the one outlined in ink.
  // A reported file (a source said it changed; metadata only) is outlined a step brighter.
  card(g, T, w, h, { fill: md ? (landed ? T.mdNew : T.mdFill) : T.cell, edge: md && landed ? T.ink : reported ? T.muted : T.edge, lw: (md && landed) || reported ? 9 : 5 })
  const nameC = (md && landed) || reported ? T.ink : T.muted
  g.fillStyle = nameC
  g.textBaseline = 'alphabetic'
  let size = 38
  g.font = `500 ${size}px "Geist Mono"`
  while (g.measureText(spec.name).width > w - pad * 2 - 40 && size > 22) {
    size -= 2
    g.font = `500 ${size}px "Geist Mono"`
  }
  g.fillText(spec.name, pad, 64)
  g.font = '400 26px "Geist"'
  g.fillStyle = T.muted
  g.fillText(spec.sub ?? '', pad, 98)
  // The body: faint while it is only metadata. (Faint is also cheap: the loop is an animated WebP,
  // which has no motion compensation, so every fine line in the field costs bytes when the camera moves.)
  g.globalAlpha = md ? 0.75 : reported ? 0.5 : 0.3
  body(g, kind, pad, top + 14, w - pad * 2, h - top - 44, md ? (landed ? T.ink : T.muted) : T.skeleton, spec.seed, md)
  g.globalAlpha = 1
  if (read > 0) {
    // The read: the page turns amber from the top down, as its bytes arrive.
    const y1 = Math.round(h * read)
    g.save()
    g.beginPath()
    g.rect(0, 0, w, y1)
    g.clip()
    card(g, T, w, h, { fill: T.amber, edge: T.amberTop, lw: 6 })
    g.fillStyle = T.onAmber
    g.font = `600 ${size}px "Geist Mono"`
    g.fillText(spec.name, pad, 64)
    g.font = '500 26px "Geist"'
    g.fillText(spec.sub ?? '', pad, 98)
    g.globalAlpha = 0.72
    body(g, kind, pad, top + 14, w - pad * 2, h - top - 44, T.onAmber, spec.seed, true)
    g.restore()
    if (read < 1) {
      g.fillStyle = T.amberTop
      g.fillRect(0, y1 - 3, w, 6)
    }
  }
  g.restore()
}

/** A texture-sized canvas with the web fonts ready. */
export function canvas(w = CARD_W, h = CARD_H) {
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  return c
}
