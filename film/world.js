// film/world.js: one world, in three.js. A company's files are four lanes of real documents running
// to the horizon, one lane per source, and beside them a fifth lane, docs/: the converted markdown
// pages an agent reads, with the git history running down its middle.
//
// The world is PERIODIC in depth. It is cut into stretches of SR rows, one per sync, and every stretch
// holds the same documents in the same places. A sync happens in one stretch; the camera then moves
// on to the next one. So the loop never has to undo anything: the last sync's two reads are still
// standing behind the camera, bytes never drain, and the last frame is the first one moved one
// stretch down the field.
//
// Nothing here keeps time. film.js computes every state from the clock and calls update(); any frame
// renders the same pixels in any order.

import * as THREE from '../node_modules/three/build/three.module.js'
import { CARD_H, CARD_W, DOCS_FILES, LANES, canvas, drawDoc } from './cards.js'
import { clamp01, ease, rng } from './lib.js'

// --------------------------------------------------------------------------------- the layout
export const DW = 0.78 // a document's width (world units)
export const DL = 1.0 // and its length
export const PX = DW + 0.3 // pitch across
export const PZ = DL + 0.3 // pitch down the lane
export const COLS = 8 // documents across one source lane
export const DCOLS = 6 // across docs/
export const GAP = 2.5 // pitches between lanes
export const SR = 24 // rows per stretch: one sync
export const ROW0 = -70
export const ROW1 = 330
// Lane l = -1 is docs/; 0..3 are the sources. x of a document's left edge.
const laneStart = (l) => (l < 0 ? 0 : DCOLS + GAP + l * (COLS + GAP))
// x = 0 is the middle of the OneDrive/SharePoint gutter, between the two files one sync reads.
const X0 = -((laneStart(0) + COLS - 1 + laneStart(1)) / 2) * PX - DW / 2
export const docX = (l, i) => X0 + (laneStart(l) + i) * PX
export const laneMid = (l) => docX(l, 0) + ((l < 0 ? DCOLS : COLS) * PX - 0.3) / 2
/** World z of a row's near edge (the lanes run away from the camera, toward -z). */
export const rowZ = (j) => -j * PZ
/** A document's centre on the ground. */
export const docC = (l, i, j) => [docX(l, i) + DW / 2, rowZ(j) - DL / 2]

// One sync: the eight files its sources report, as (lane, column, row within the stretch). H0 is
// equal on six. SharePoint's forecast.xlsx is a no-op save (H1 differs, H2 is equal); OneDrive's
// proposal.docx becomes one page in docs/. The two stand either side of the gutter, on one row.
export const REPORTED = [
  { l: 0, i: 7, j: 14, name: 'proposal.docx', sub: 'Northwind renewal', read: 'commit' },
  { l: 1, i: 0, j: 14, name: 'forecast.xlsx', sub: 'Sales · Q3', read: 'noop' },
  { l: 0, i: 2, j: 7, name: 'roadmap.pptx', sub: '12 slides' },
  { l: 1, i: 5, j: 19, name: 'handbook.pdf', sub: '42 pages' },
  { l: 2, i: 2, j: 9, name: 'Offsite agenda', sub: 'Marcus Lee' },
  { l: 2, i: 6, j: 17, name: 'RE: renewal terms', sub: 'Priya Shah' },
  { l: 3, i: 1, j: 11, name: '#sales', sub: 'Dana: new forecast is up' },
  { l: 3, i: 5, j: 5, name: '#launch', sub: 'Marcus: date moved' },
]
// Where the converted page lands in docs/, and where its commit sits on the git line.
export const MD_SLOT = { i: 4, j: 14 }
export const GIT_X = docX(-1, 3) - 0.15
const STRETCHES = [-2, -1, 0, 1, 2, 3] // stretches whose sync is drawn object by object
const key = (l, i, j) => `${l},${i},${j}`
const reportedSlot = new Map(REPORTED.map((o, n) => [key(o.l, o.i, o.j), n]))
export const GROW = 2.4 // a read page stands up at this size: the bytes made it a whole document

// Deterministic field: which of a lane's twelve files sits in each place, periodic in the stretch.
function variantAt(l, i, j) {
  const jj = ((j % SR) + SR) % SR
  const R = rng(1009 * (l + 7) + 131 * i + 17 * jj)
  R()
  return Math.floor(R() * 12)
}

// --------------------------------------------------------------------------------- building
export function buildWorld(T, { width = 1920, height = 1080 } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(1)
  renderer.setSize(width, height)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  const maxAniso = renderer.capabilities.getMaxAnisotropy()
  const scene = new THREE.Scene()
  const bg = new THREE.Color(T.bg)
  scene.background = bg
  scene.fog = new THREE.Fog(bg, 40, 160)
  const cam = new THREE.PerspectiveCamera(40, width / height, 0.05, 600)
  cam.rotation.order = 'YXZ'

  const tex = (c) => {
    const t = new THREE.CanvasTexture(c)
    t.colorSpace = THREE.SRGBColorSpace
    t.anisotropy = maxAniso
    t.generateMipmaps = true
    t.minFilter = THREE.LinearMipmapLinearFilter
    return t
  }
  const flat = new THREE.PlaneGeometry(DW, DL).rotateX(-Math.PI / 2)

  // The field: every ordinary document, instanced by (lane, variant).
  const fieldTex = LANES.map((lane, l) =>
    lane.files.map(([name, sub], v) => {
      const c = canvas()
      drawDoc(c.getContext('2d'), T, { lane: l, name, sub, seed: 97 * l + v })
      return tex(c)
    }),
  )
  const docsTex = DOCS_FILES.map(([name, sub], v) => {
    const c = canvas()
    drawDoc(c.getContext('2d'), T, { lane: -1, name, sub, seed: 500 + v }, { md: true })
    return tex(c)
  })
  const buckets = new Map()
  const push = (k, x, z) => {
    if (!buckets.has(k)) buckets.set(k, [])
    buckets.get(k).push([x, z])
  }
  const firstK = STRETCHES[0]
  const lastK = STRETCHES[STRETCHES.length - 1]
  for (let j = ROW0; j < ROW1; j += 1) {
    const k = Math.floor(j / SR)
    const jj = j - k * SR
    for (let l = 0; l < 4; l += 1) {
      for (let i = 0; i < COLS; i += 1) {
        if (jj < 2) continue // each stretch opens on a band that carries the lane names
        if (k >= firstK && k <= lastK && reportedSlot.has(key(l, i, jj))) continue
        const [x, z] = docC(l, i, j)
        push(`${l}:${variantAt(l, i, j)}`, x, z)
      }
    }
    for (let i = 0; i < DCOLS; i += 1) {
      if (i === 2 || i === 3 || jj < 2) continue // the git line runs here
      if (k >= firstK && k <= lastK && i === MD_SLOT.i && jj === MD_SLOT.j) continue
      const [x, z] = docC(-1, i, j)
      // Forty pages, dealt so neighbours differ: a real folder has one of each.
      push(`d:${(jj * 5 + i * 11) % DOCS_FILES.length}`, x, z) // periodic in the stretch, like the field
    }
  }
  const m4 = new THREE.Matrix4()
  for (const [k, list] of buckets) {
    const [a, b] = k.split(':')
    const map = a === 'd' ? docsTex[Number(b)] : fieldTex[Number(a)][Number(b)]
    const mesh = new THREE.InstancedMesh(flat, new THREE.MeshBasicMaterial({ map, transparent: false, alphaTest: 0.5 }), list.length)
    list.forEach(([x, z], n) => mesh.setMatrixAt(n, m4.makeTranslation(x, 0, z)))
    mesh.frustumCulled = false
    scene.add(mesh)
  }

  // Lane names, painted on the ground at the head of every stretch like road markings.
  const nameTex = [-1, 0, 1, 2, 3].map((l) => {
    const text = l < 0 ? 'docs/' : LANES[l].name
    const c = canvas(1024, 200)
    const g = c.getContext('2d')
    g.font = '500 150px "Geist Mono"'
    g.fillStyle = l < 0 ? T.ink : T.muted
    g.textAlign = 'center'
    g.textBaseline = 'alphabetic'
    g.fillText(text, 512, 158)
    return { t: tex(c), l }
  })
  const nameGeo = new THREE.PlaneGeometry(1, 200 / 1024).rotateX(-Math.PI / 2)
  for (let k = Math.floor(ROW0 / SR); k * SR < ROW1; k += 1) {
    for (const { t, l } of nameTex) {
      const w = (l < 0 ? DCOLS : COLS) * PX * 0.92
      const m = new THREE.Mesh(nameGeo, new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false }))
      m.scale.set(w, 1, w)
      m.position.set(laneMid(l), 0.004, rowZ(k * SR) - PZ + 0.1)
      m.renderOrder = 1
      scene.add(m)
    }
  }

  // The git line down docs/: history is behind, and it ends at the newest commit (HEAD).
  const lineMat = new THREE.MeshBasicMaterial({ color: T.faint }) // quieter than the commits on it
  const gitLine = new THREE.Mesh(new THREE.PlaneGeometry(0.09, 1).rotateX(-Math.PI / 2), lineMat)
  gitLine.renderOrder = 2
  scene.add(gitLine)
  const dotGeo = new THREE.CircleGeometry(0.24, 48).rotateX(-Math.PI / 2)
  const dotRingGeo = new THREE.RingGeometry(0.24, 0.36, 48).rotateX(-Math.PI / 2)
  // Earlier history: a commit every six rows behind the line's tip, muted. Only HEAD is in ink.
  const hist = []
  for (let j = ROW0 + 3; j < ROW1; j += 6) if ((((j - MD_SLOT.j) % SR) + SR) % SR !== 0) hist.push(j)
  const histDots = new THREE.InstancedMesh(new THREE.CircleGeometry(0.16, 32).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: T.muted }), hist.length)
  hist.forEach((j, n) => histDots.setMatrixAt(n, m4.makeTranslation(GIT_X, 0.013, docC(-1, 0, j)[1])))
  histDots.renderOrder = 3
  histDots.frustumCulled = false
  scene.add(histDots)
  const commits = STRETCHES.map((k) => {
    const g = new THREE.Group()
    const [, z] = docC(-1, 0, k * SR + MD_SLOT.j)
    const dot = new THREE.Mesh(dotGeo, new THREE.MeshBasicMaterial({ color: T.ink, transparent: true }))
    const halo = new THREE.Mesh(dotRingGeo, new THREE.MeshBasicMaterial({ color: T.bg, transparent: true }))
    g.add(halo, dot)
    g.position.set(GIT_X, 0.012, z)
    g.renderOrder = 3
    scene.add(g)
    return { k, g, dot, z }
  })
  for (const c of commits) {
    c.dot.renderOrder = 4
    c.halo = c.g.children[0]
    c.halo.renderOrder = 3
  }

  // The reported documents, one object each, in the stretches a cut can see.
  const box = new THREE.BoxGeometry(DW, 0.02, DL)
  const sideMat = new THREE.MeshBasicMaterial({ color: T.edge })
  const ringGeo = new THREE.RingGeometry(0.76, 1.0, 72).rotateX(-Math.PI / 2)
  const events = []
  for (const k of STRETCHES) {
    REPORTED.forEach((o, n) => {
      const c = canvas()
      const t = tex(c)
      const faceMat = new THREE.MeshBasicMaterial({ map: t })
      // Box faces: +x, -x, +y (the page), -y, +z, -z.
      const back = new THREE.MeshBasicMaterial({ color: T.cell })
      const mesh = new THREE.Mesh(box, [sideMat, sideMat, faceMat, back, sideMat, sideMat])
      // Pivot on the page's near edge, so a read stands up facing the camera.
      const pivot = new THREE.Group()
      const [x, z] = docC(o.l, o.i, k * SR + o.j)
      pivot.position.set(x, 0.011, z + DL / 2)
      mesh.position.set(0, 0, -DL / 2)
      pivot.add(mesh)
      scene.add(pivot)
      const ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ color: T.muted, transparent: true, depthWrite: false }))
      ring.position.set(x, 0.006, z)
      ring.renderOrder = 2
      scene.add(ring)
      events.push({ k, n, o, c, t, pivot, mesh, ring, x, z, last: '' })
    })
  }

  // A read's light and shadow: a warm pool round its base, and one long shadow from a low key light
  // at the front left.
  const poolC = canvas(256, 256)
  {
    const g = poolC.getContext('2d')
    // An alpha map reads the green channel, so it is drawn opaque, white to black.
    const gr = g.createRadialGradient(128, 128, 0, 128, 128, 128)
    gr.addColorStop(0, '#ffffff')
    gr.addColorStop(0.3, '#8a8a8a')
    gr.addColorStop(0.65, '#262626')
    gr.addColorStop(1, '#000000')
    g.fillStyle = gr
    g.fillRect(0, 0, 256, 256)
  }
  const poolTex = new THREE.CanvasTexture(poolC)
  const shadowC = canvas(64, 256)
  {
    const g = shadowC.getContext('2d')
    const gr = g.createLinearGradient(0, 0, 0, 256)
    gr.addColorStop(0, '#ffffff')
    gr.addColorStop(0.4, '#808080')
    gr.addColorStop(1, '#000000')
    g.fillStyle = gr
    g.fillRect(0, 0, 64, 256)
  }
  const shadowTex = new THREE.CanvasTexture(shadowC)
  const LIGHT = new THREE.Vector2(0.55, -0.8).normalize() // on the ground: toward +x and into the field
  for (const ev of events) {
    if (!ev.o.read) continue
    const pool = new THREE.Mesh(new THREE.PlaneGeometry(6.5, 6.5).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: T.pool, alphaMap: poolTex, transparent: true, depthWrite: false }))
    pool.position.set(ev.x, 0.008, ev.z)
    pool.renderOrder = 1
    scene.add(pool)
    const sg = new THREE.BufferGeometry()
    sg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(12), 3))
    // v = 1 at the base (the dark end of the gradient) and 0 at the tip.
    sg.setAttribute('uv', new THREE.BufferAttribute(new Float32Array([0, 1, 1, 1, 1, 0, 0, 0]), 2))
    sg.setIndex([0, 1, 2, 0, 2, 3])
    const shadow = new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ color: T.shadow, alphaMap: shadowTex, transparent: true, opacity: T.shadowA, depthWrite: false }))
    shadow.renderOrder = 2
    shadow.frustumCulled = false
    scene.add(shadow)
    // The cap: a green slab clipped over the top of the no-op save: paid, then free at H2.
    let cap = null
    if (ev.o.read === 'noop') {
      // A green band across the page, below its name and overhanging both edges: paid, then free at
      // H2. A shape as well as a hue, and it says what it is.
      const bc = canvas(512, 96)
      {
        const g = bc.getContext('2d')
        g.fillStyle = T.capFace
        g.fillRect(0, 0, 512, 96)
        g.fillStyle = T.onCap
        g.font = '600 50px "Geist Mono"'
        g.textAlign = 'center'
        g.textBaseline = 'middle'
        g.fillText('no-op save', 256, 50)
      }
      const bandTex = tex(bc)
      const side = new THREE.MeshBasicMaterial({ color: T.capSide })
      cap = new THREE.Mesh(new THREE.BoxGeometry(DW + 0.16, 0.05, 0.16), [side, side, new THREE.MeshBasicMaterial({ map: bandTex }), new THREE.MeshBasicMaterial({ color: T.capUnder }), side, side])
      ev.mesh.add(cap)
    }
    Object.assign(ev, { pool, shadow, cap })
  }

  // The converted page: proposal.md, which leaves proposal.docx and lands in docs/.
  const mdC = canvas()
  const mdTex = tex(mdC)
  const mdMesh = new THREE.Mesh(new THREE.BoxGeometry(DW, 0.02, DL), [sideMat, sideMat, new THREE.MeshBasicMaterial({ map: mdTex }), new THREE.MeshBasicMaterial({ color: T.cell }), sideMat, sideMat])
  scene.add(mdMesh)
  const mdSlots = STRETCHES.map((k) => {
    const c = canvas()
    const t = tex(c)
    const m = new THREE.Mesh(flat, new THREE.MeshBasicMaterial({ map: t, alphaTest: 0.5 }))
    const [x, z] = docC(-1, MD_SLOT.i, k * SR + MD_SLOT.j)
    m.position.set(x, 0.001, z)
    scene.add(m)
    return { k, c, t, m, x, z, last: '' }
  })

  // The counterfactual (FILM only): every file in view read again, drawn hollow and dimmer.
  const ghostC = canvas()
  drawDoc(ghostC.getContext('2d'), T, { lane: 0, name: '', seed: 1 }, { ghost: true })
  const ghostTex = tex(ghostC)
  const GHOST_MAX = 4 * COLS * 70
  const ghosts = new THREE.InstancedMesh(new THREE.PlaneGeometry(DW, DL).translate(0, DL / 2, 0), new THREE.MeshBasicMaterial({ map: ghostTex, alphaTest: 0.5, side: THREE.DoubleSide }), GHOST_MAX)
  ghosts.count = 0
  ghosts.frustumCulled = false
  ghosts.renderOrder = 5
  scene.add(ghosts)

  // Accumulation, for motion blur: each sub-frame renders into `one`, then adds into `acc`.
  const rtOpts = { type: THREE.HalfFloatType, samples: 4, colorSpace: THREE.LinearSRGBColorSpace }
  const one = new THREE.WebGLRenderTarget(width, height, rtOpts)
  const acc = new THREE.WebGLRenderTarget(width, height, { type: THREE.HalfFloatType })
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2))
  const quadScene = new THREE.Scene()
  quadScene.add(quad)
  const quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)
  const addMat = new THREE.ShaderMaterial({
    uniforms: { src: { value: null }, w: { value: 1 } },
    vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
    fragmentShader: 'uniform sampler2D src; uniform float w; varying vec2 vUv; void main() { gl_FragColor = vec4(texture2D(src, vUv).rgb * w, 1.0); }',
    blending: THREE.AdditiveBlending, depthTest: false, depthWrite: false, transparent: true,
  })
  const outMat = new THREE.ShaderMaterial({
    uniforms: { src: { value: null } },
    vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
    fragmentShader: 'uniform sampler2D src; varying vec2 vUv; void main() { gl_FragColor = vec4(texture2D(src, vUv).rgb, 1.0);\n#include <colorspace_fragment>\n}',
    depthTest: false, depthWrite: false,
  })

  // ------------------------------------------------------------------------------ the camera
  // A pose: position, yaw (turning right is positive), pitch (looking down is positive), focal length
  // F in px and the principal point (cx, cy). A principal point off-centre is a lens shift: it moves
  // the horizon without tilting the camera, so standing pages stay vertical.
  function pose(p) {
    cam.position.set(p.x, p.y, p.z)
    cam.rotation.set(-(p.pitch ?? 0), -(p.yaw ?? 0), 0)
    const n = cam.near
    const cx = p.cx ?? width / 2
    const cy = p.cy ?? height / 2
    cam.projectionMatrix.makePerspective((-cx / p.F) * n, ((width - cx) / p.F) * n, (cy / p.F) * n, (-(height - cy) / p.F) * n, n, cam.far)
    cam.projectionMatrixInverse.copy(cam.projectionMatrix).invert()
    cam.updateMatrixWorld(true)
  }
  const v3 = new THREE.Vector3()
  /** Screen position of a world point under the current pose, or null behind the camera. */
  function project(x, y, z) {
    v3.set(x, y, z).applyMatrix4(cam.matrixWorldInverse)
    if (v3.z > -0.05) return null
    const d = -v3.z
    v3.applyMatrix4(cam.projectionMatrix)
    return [(v3.x + 1) * 0.5 * width, (1 - v3.y) * 0.5 * height, d]
  }

  // ------------------------------------------------------------------------------ the state
  const q = new THREE.Quaternion()
  const s3 = new THREE.Vector3()
  const p3 = new THREE.Vector3()
  const xAxis = new THREE.Vector3(1, 0, 0)

  /**
   * Apply one moment. st(k) returns stretch k's sync state:
   *   { ring[n], green[n], read (0..1), stand (0..1), grow (0..1), h1, h2, cap (0..1), md: {...}, commit (0..1) }
   * ghostWall: null or { rows: fn(j) -> height 0..1 } for the counterfactual.
   */
  const bgRekey = new THREE.Color(T.bgRekey)
  function update(st, { fog = [40, 160], ghostWall = null, ghostFrom = 0, md = null, rekey = false } = {}) {
    scene.fog.near = fog[0]
    scene.fog.far = fog[1]
    scene.background = rekey ? bgRekey : bg
    scene.fog.color.copy(rekey ? bgRekey : bg)
    let newest = null
    for (const c of commits) {
      const s = st(c.k)
      c.g.visible = s.commit > 0.001
      c.dot.material.opacity = s.commit
      c.halo.material.opacity = s.commit
      if (s.commit > 0.001 && (newest === null || c.k > newest.k)) newest = { k: c.k, z: c.z, a: s.commit }
    }
    // The line runs from the far past up to HEAD, and draws its last segment as the commit lands.
    const zBack = rowZ(ROW0)
    let zHead = newest ? newest.z : rowZ(ROW0 + 1)
    if (newest && newest.a < 1) {
      const prev = commits.find((c) => c.k === newest.k - 1)
      if (prev) zHead = prev.z + (newest.z - prev.z) * newest.a
    }
    gitLine.scale.set(1, 1, Math.max(0.001, zBack - zHead))
    gitLine.position.set(GIT_X, 0.01, (zBack + zHead) / 2)
    histDots.count = hist.filter((j) => docC(-1, 0, j)[1] > zHead + 0.2).length
    for (const c of commits) {
      const head = newest && c.k === newest.k
      c.dot.material.color.set(head ? T.ink : T.muted)
      c.g.scale.setScalar(head ? 1 : 0.75)
    }

    for (const ev of events) {
      const s = st(ev.k)
      const o = ev.o
      // Rings: reported, metadata only. Green once H0 is equal, then gone.
      const ra = s.ring[ev.n]
      ev.ring.visible = ra > 0.001
      ev.ring.material.opacity = ra
      ev.ring.material.color.set(s.green[ev.n] > 0.5 ? T.green : T.muted)
      ev.ring.scale.setScalar(s.green[ev.n] > 0.5 ? 1.06 : 1)
      const r = o.read ? s.read : 0
      const stand = o.read ? s.stand : 0
      const grow = o.read ? s.grow : 0
      const hot = ra > 0.5 && s.green[ev.n] < 0.5
      const sig = `${r.toFixed(3)}${hot ? 'h' : ''}`
      if (sig !== ev.last) {
        drawDoc(ev.c.getContext('2d'), T, { lane: o.l, name: o.name, sub: o.sub, seed: 97 * o.l + ev.n + 11 }, { read: r, reported: hot })
        ev.t.needsUpdate = true
        ev.last = sig
      }
      const sc = 1 + (GROW - 1) * grow
      ev.pivot.rotation.set(stand * Math.PI / 2, 0, 0)
      ev.pivot.scale.setScalar(sc)
      if (ev.pool) {
        ev.pool.visible = s.pool > 0.001
        ev.pool.material.opacity = s.pool * 0.85
        // The shadow: the standing page's top edge thrown along the light onto the ground.
        const H = DL * sc * Math.sin(stand * Math.PI / 2)
        ev.shadow.visible = H > 0.01
        const L = H * 1.9
        const bx0 = ev.x - (DW * sc) / 2
        const bx1 = ev.x + (DW * sc) / 2
        const bz = ev.z + DL / 2
        const pos = ev.shadow.geometry.attributes.position.array
        const ox = LIGHT.x * L
        const oz = LIGHT.y * L
        pos.set([bx0, 0.009, bz, bx1, 0.009, bz, bx1 + ox, 0.009, bz + oz, bx0 + ox, 0.009, bz + oz])
        ev.shadow.geometry.attributes.position.needsUpdate = true
        if (ev.cap) {
          const c = s.cap
          ev.cap.visible = c > 0.001
          // It slides down onto the page from above its top edge.
          ev.cap.position.set(0, 0.035, -DL / 2 + 0.36 - (1 - c) * 0.3)
          ev.cap.scale.set(1, 1, 1)
          ev.cap.material.forEach((m) => {
            m.transparent = c < 1
            m.opacity = c
          })
        }
      }
    }
    // The converted page.
    mdMesh.visible = !!md?.visible
    if (md?.visible) {
      const sig = 'md'
      if (mdMesh.userData.sig !== sig) {
        drawDoc(mdC.getContext('2d'), T, { lane: -1, name: 'proposal.md', sub: 'mirror/onedrive', seed: 500 }, { md: true, landed: 1 })
        mdTex.needsUpdate = true
        mdMesh.userData.sig = sig
      }
      mdMesh.position.set(md.x, md.y, md.z)
      mdMesh.rotation.set(md.rx, md.ry ?? 0, 0)
      mdMesh.scale.setScalar(md.scale)
    }
    for (const sl of mdSlots) {
      const s = st(sl.k)
      const landed = s.landed
      const sig = landed > 0.5 ? 'new' : 'old'
      if (sig !== sl.last) {
        drawDoc(sl.c.getContext('2d'), T, { lane: -1, name: 'proposal.md', sub: 'mirror/onedrive', seed: 500 }, { md: true, landed: landed > 0.5 ? 1 : 0 })
        sl.t.needsUpdate = true
        sl.last = sig
      }
    }
    // The counterfactual wall.
    let n = 0
    if (ghostWall) {
      for (let j = ghostFrom; j < ghostFrom + 70; j += 1) {
        const hgt = ghostWall(j)
        if (hgt <= 0.001) continue
        for (let l = 0; l < 4; l += 1) {
          for (let i = 0; i < COLS; i += 1) {
            if (n >= GHOST_MAX) break
            const [x, z] = docC(l, i, j)
            // Flat is a quarter turn back into the field; standing faces the camera.
            // It stands up and grows to a read's full size, like a real read.
            q.setFromAxisAngle(xAxis, -(Math.PI / 2) * (1 - hgt))
            const sc = 1 + (GROW - 1) * hgt
            s3.set(sc, sc, 1)
            p3.set(x, 0.02, z + DL / 2)
            m4.compose(p3, q, s3)
            ghosts.setMatrixAt(n, m4)
            n += 1
          }
        }
      }
    }
    ghosts.count = n
    ghosts.instanceMatrix.needsUpdate = true
  }

  /** Render the current state, or the average of several (motion blur). */
  function render(samples) {
    if (!samples) {
      renderer.setRenderTarget(null)
      renderer.render(scene, cam)
      return
    }
    renderer.setRenderTarget(acc)
    renderer.setClearColor(0x000000, 1)
    renderer.clear()
    samples.forEach((apply, k) => {
      apply()
      renderer.setRenderTarget(one)
      renderer.render(scene, cam)
      quad.material = addMat
      addMat.uniforms.src.value = one.texture
      addMat.uniforms.w.value = 1 / samples.length
      renderer.setRenderTarget(acc)
      renderer.autoClear = false
      renderer.render(quadScene, quadCam)
      renderer.autoClear = true
    })
    quad.material = outMat
    outMat.uniforms.src.value = acc.texture
    renderer.setRenderTarget(null)
    renderer.render(quadScene, quadCam)
  }

  return { renderer, scene, cam, pose, project, update, render, events }
}

export { ease, clamp01, CARD_W, CARD_H }
