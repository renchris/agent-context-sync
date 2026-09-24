// film/lib.js: the clock, the easings and the theme tokens every scene uses.
//
// The film is a pure function of time. Nothing here keeps state between calls to seek(t), so any
// frame can be rendered in any order and renders the same pixels every time.

export const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x)
export const lerp = (a, b, k) => a + (b - a) * k

// Named easings. Arrivals settle (expo out), exits leave (expo in), moves between states travel
// (cubic in-out), and anything metered walks at constant speed. No overshoot: this is plumbing.
export const ease = {
  settle: (x) => (x >= 1 ? 1 : 1 - 2 ** (-10 * x)),
  leave: (x) => (x <= 0 ? 0 : 2 ** (10 * x - 10)),
  travel: (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2),
  glide: (x) => (x < 0.5 ? 16 * x ** 5 : 1 - (-2 * x + 2) ** 5 / 2), // quint in-out, for the camera
  walk: (x) => x,
}

/** Progress of t through [a, b], clamped to 0..1. */
export const p = (t, a, b) => clamp01((t - a) / (b - a))
/** Eased progress of t through [a, b]. */
export const e = (t, a, b, fn = ease.settle) => fn(p(t, a, b))
/** A value that rises over [a, b] and falls over [c, d]. */
export const env = (t, a, b, c, d, fin = ease.settle, fout = ease.leave) => e(t, a, b, fin) * (1 - e(t, c, d, fout))

// colour means cost (docs/design/VISUAL.md). neutral = zero-byte work, amber = bytes read,
// green = a free exit, red = an expiry. Amber is the only loud colour.
export const THEMES = {
  dark: {
    bg: '#0d1117', ink: '#e6edf3', muted: '#8b949e', faint: '#3d444d', cell: '#161b22',
    amber: '#f0a33a', amberSide: '#c98933', amberTop: '#f6bc68', amberText: '#f0a33a', amberFill: '#2d2111', green: '#3fb950', greenSide: '#339a43', greenTop: '#62cc72', greenFill: '#0f2a19', red: '#ff7b72', redFill: '#2d1417',
  },
  light: {
    bg: '#ffffff', ink: '#1f2328', muted: '#59636e', faint: '#afb8c1', cell: '#f6f8fa',
    amber: '#c86a00', amberSide: '#b35900', amberTop: '#d98a2e', amberText: '#b35900', amberFill: '#fff1dc', green: '#1a7f37', greenSide: '#146b2e', greenTop: '#2a9a4a', greenFill: '#dafbe1', red: '#cf222e', redFill: '#ffebe9',
  },
}

/** Deterministic PRNG (mulberry32), so every "random" layout is the same on every frame and run. */
export function rng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
