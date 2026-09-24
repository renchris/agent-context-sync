// film/world.js: the plane. A company's knowledge as a flat field of document tiles, four regions
// wide (one per source). Reading bytes is the only thing on the plane that has height.
//
// Everything here is a pure function of the arguments passed to draw(): the camera and the per-tile
// state are computed by the caller from the film clock, so any frame renders identically.

export const REGIONS = ['OneDrive', 'SharePoint', 'Outlook', 'Teams']
export const COLS = 16 // tiles across one region
export const ROWS = 190 // tiles deep
export const GAP = 2.5 // tiles between regions
// A tile is a page lying face up: portrait, with its far right corner folded.
export const SX = 0.78 // page width (world units)
export const SZ = 1.0 // page length
export const S = SX // kept for callers that centre on a tile's width
export const P = 0.3 // gap between pages
export const PX = SX + P
export const PZ = SZ + P
export const PITCH_ = PZ
export const N = REGIONS.length * COLS * ROWS // 12,160
export const WIDTH = (REGIONS.length * COLS + (REGIONS.length - 1) * GAP) * PX
export const X0 = -WIDTH / 2
const DOG = 0.24 // the folded corner

/** World x of a page's left edge and z of its near edge. */
export const tileX = (r, i) => X0 + (r * (COLS + GAP) + i) * PX
export const tileZ = (j) => j * PZ
/** The centre of a page's footprint. */
export const tileC = (r, i, j) => [tileX(r, i) + SX / 2, tileZ(j) + SZ / 2]
/** The centre line of a lane. */
export const laneX = (r) => tileX(r, 0) + (COLS * PX - P) / 2
/** The centre of a region's near edge, where its since-token sits. */
export const regionFront = (r) => [tileX(r, 0) + (COLS * PITCH_) / 2, 0, -2.2]

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
export function mix(a, b, k) {
  const A = hex(a)
  const B = hex(b)
  return `rgb(${A.map((v, i) => Math.round(v + (B[i] - v) * k)).join(',')})`
}

/** A pinhole camera: position, pitch (radians, looking down), yaw, focal length in px, horizon y. */
export function camera({ x, y, z, pitch, yaw = 0, F, cx = 960, cy = 600 }) {
  const cp = Math.cos(pitch)
  const sp = Math.sin(pitch)
  const cyw = Math.cos(yaw)
  const syw = Math.sin(yaw)
  return {
    F,
    x,
    V: F * cp, // screen px per world unit of height, per unit of depth (verticals stay vertical)
    proj(wx, wy, wz) {
      let dx = wx - x
      const dy = wy - y
      let dz = wz - z
      ;[dx, dz] = [dx * cyw - dz * syw, dx * syw + dz * cyw]
      const zc = dz * cp - dy * sp
      const yc = dy * cp + dz * sp
      if (zc < 0.2) return null
      return [cx + (F * dx) / zc, cy - (F * yc) / zc, zc]
    },
  }
}

function poly(g, pts, fill, stroke, lw) {
  g.beginPath()
  g.moveTo(pts[0][0], pts[0][1])
  for (let k = 1; k < pts.length; k += 1) g.lineTo(pts[k][0], pts[k][1])
  g.closePath()
  if (fill) {
    g.fillStyle = fill
    g.fill()
  }
  if (stroke) {
    g.strokeStyle = stroke
    g.lineWidth = lw
    g.stroke()
  }
}

/**
 * Draw the plane. `state(r, i, j)` returns null for an ordinary tile, or an object with any of:
 *   h      pillar height in tiles (bytes read); 0 = flat
 *   cap    0..1, a flat green cap on the pillar (paid, then free at H2)
 * `fog` pushes distant tiles toward the background; `dim` lowers the whole plane (for type).
 */
export function drawPlane(g, T, cam, state, { fogNear = 60, fogFar = 180, dim = 0 } = {}) {
  const pillars = []
  // Pages lie on the ground and never overlap, so their order does not matter. Pillars stand on the
  // ground, so no page can hide one: they are painted after every page, far to near by camera depth.
  for (let j = ROWS - 1; j >= 0; j -= 1) {
    const z = tileZ(j)
    for (let r = 0; r < REGIONS.length; r += 1) {
      for (let i = 0; i < COLS; i += 1) {
        const x = tileX(r, i)
        const st = state(r, i, j)
        if (st && st.h > 0.001) {
          pillars.push({ x, z, st })
          continue
        }
        const a = cam.proj(x, 0, z)
        if (!a) continue
        const b = cam.proj(x + SX, 0, z)
        const d = cam.proj(x, 0, z + SZ)
        if (!b || !d) continue
        if (Math.max(a[0], b[0]) < -40 || Math.min(a[0], b[0]) > 1960) continue
        if (a[1] < -40 || d[1] > 1120) continue
        const fog = Math.min(1, Math.max(0, (a[2] - fogNear) / (fogFar - fogNear)))
        const k = Math.min(1, fog * 0.92 + dim)
        if (k >= 0.995) continue
        const px = Math.abs(b[0] - a[0])
        const fill = mix(T.cell, T.bg, k)
        const edge = px > 2.2 ? mix(T.faint, T.bg, Math.min(1, k * 1.05)) : null
        if (px > 9) {
          // Near pages show their folded corner.
          const c1 = cam.proj(x + SX, 0, z + SZ - DOG)
          const c2 = cam.proj(x + SX - DOG * 0.8, 0, z + SZ)
          if (!c1 || !c2) continue
          poly(g, [a, b, c1, c2, d], fill, edge, px > 30 ? 1.5 : 1)
          poly(g, [c1, c2, cam.proj(x + SX - DOG * 0.8, 0, z + SZ - DOG)], mix(T.faint, T.bg, k * 0.6), null, 1)
        } else {
          const c = cam.proj(x + SX, 0, z + SZ)
          if (!c) continue
          poly(g, [a, b, c, d], fill, edge, 1)
        }
      }
    }
  }
  const depth = (p) => cam.proj(p.x + SX / 2, 0, p.z + SZ / 2)?.[2] ?? 0
  pillars.sort((a, b) => depth(b) - depth(a))
  for (const p of pillars) drawPillar(g, T, cam, p.x, p.z, p.st)
}

// A read is a pillar: bytes are the only thing on the plane with height. Its verticals are drawn
// vertical on screen (a two-point correction), so it stands instead of leaning away from the lens.
// The faces are full-strength amber, shaded by face and never darkened by more than about 15 %, so a
// read is always the loudest thing in its frame. `cap` (0..1) recolours the top of the pillar green
// from the top down: paid, then free at H2. It never adds height, because height means bytes.
export const CAP = 0.9
export function drawPillar(g, T, cam, x, z, st) {
  const h = st.h
  const q = (xx, zz) => cam.proj(xx, 0, zz)
  const b0 = [q(x, z), q(x + SX, z), q(x + SX, z + SZ), q(x, z + SZ)]
  if (b0.some((v) => !v)) return
  const at = (y) => b0.map(([sx, sy, sz]) => [sx, sy - (cam.V * y) / sz, sz])
  const capH = CAP * (st.cap ?? 0)
  const mid = at(Math.max(0, h - capH))
  const top = at(h)
  // A side face shows only when the camera is beyond its plane; from straight in front, neither does.
  const side = cam.x > x + SX ? 'right' : cam.x < x ? 'left' : null
  const faces = (lo, hi, front, sideFill) => {
    poly(g, [lo[0], lo[1], hi[1], hi[0]], front, front, 1)
    if (side === 'right') poly(g, [lo[1], lo[2], hi[2], hi[1]], sideFill, sideFill, 1)
    if (side === 'left') poly(g, [lo[3], lo[0], hi[0], hi[3]], sideFill, sideFill, 1)
  }
  faces(b0, mid, T.amber, T.amberSide)
  if (capH > 0) faces(mid, top, T.green, T.greenSide)
  poly(g, top, capH > 0 ? T.greenTop : T.amberTop, capH > 0 ? T.greenTop : T.amberTop, 1)
}

// Text painted flat on the ground, like a road marking: it foreshortens with the plane instead of
// floating over it. The text's baseline sits at depth z0 and its top at z0 + height; x0 is its left
// edge. Rendered in horizontal strips, each an affine map of one band of the text image, which is
// exact for a camera with no yaw (the only cameras that use it).
const glyphCache = new Map()
export function drawGroundText(g, cam, text, xCenter, z0, height, color, font = '500 200px "Geist Mono"') {
  const cacheKey = `${text}|${color}|${font}`
  let img = glyphCache.get(cacheKey)
  // Never cache a fallback face: only cache once the web font is actually available.
  if (!img || !img.real) {
    const c = document.createElement('canvas')
    const m = c.getContext('2d')
    m.font = font
    const w = Math.ceil(m.measureText(text).width) + 8
    c.width = w
    c.height = 200
    const n = c.getContext('2d')
    n.font = font
    n.fillStyle = color
    n.textBaseline = 'alphabetic'
    n.fillText(text, 4, 160)
    img = { c, w, h: 200, base: 160, cap: 140, real: document.fonts.check(font) }
    glyphCache.set(cacheKey, img)
  }
  // World units per image pixel: the cap height (about 140 px) spans `height`.
  const k = height / img.cap
  const width = img.w * k
  const x0 = xCenter - width / 2
  const strips = 40
  for (let n = 0; n < strips; n += 1) {
    const v0 = (img.h * n) / strips
    const v1 = (img.h * (n + 1)) / strips
    const zTop = z0 + (img.base - v0) * k
    const zBot = z0 + (img.base - v1) * k
    const p00 = cam.proj(x0, 0.01, zTop)
    const p10 = cam.proj(x0 + width, 0.01, zTop)
    const p01 = cam.proj(x0, 0.01, zBot)
    if (!p00 || !p10 || !p01) continue
    const ax = (p10[0] - p00[0]) / img.w
    const ay = (p10[1] - p00[1]) / img.w
    const bx = (p01[0] - p00[0]) / (v1 - v0)
    const by = (p01[1] - p00[1]) / (v1 - v0)
    g.save()
    g.setTransform(ax * 2, ay * 2, bx * 2, by * 2, (p00[0] - bx * v0) * 2, (p00[1] - by * v0) * 2)
    g.drawImage(img.c, 0, v0, img.w, v1 - v0 + 0.6, 0, v0, img.w, v1 - v0 + 0.6)
    g.restore()
  }
  return width
}

/** An ellipse on the ground around a tile: "reported". color, alpha, radius in tiles. */
export function drawRing(g, cam, r, i, j, color, alpha, radius = 1.6, lw = 2) {
  if (alpha <= 0.001) return
  const cx = tileX(r, i) + SX / 2
  const cz = tileZ(j) + SZ / 2
  g.beginPath()
  for (let k = 0; k <= 48; k += 1) {
    const a = (k / 48) * Math.PI * 2
    const p = cam.proj(cx + Math.cos(a) * radius, 0.02, cz + Math.sin(a) * radius)
    if (!p) return
    if (k === 0) g.moveTo(p[0], p[1])
    else g.lineTo(p[0], p[1])
  }
  g.globalAlpha = alpha
  g.strokeStyle = color
  g.lineWidth = lw
  g.stroke()
  g.globalAlpha = 1
}
