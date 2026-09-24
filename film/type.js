// film/type.js: kinetic type. A line is built once as spans (one per word) and posed per frame.
//
// Grammar (docs/design/hero/launch-research.md): type settles with a deceleration and never
// springs; words arrive per word on a 70 ms stagger, rising a short distance out of a blur; a line
// leaves faster than it arrived. Identifiers are set in Geist Mono (Geist Sans draws H0 as "HO").

import { clamp01, e, ease } from './lib.js'

/**
 * Make a text block. `spec.parts` is an array of [text, className?] runs; words keep their run's
 * class. Returns { el, words, pose(u, opts) } where u is the block's local time in seconds.
 */
export function block(parent, spec) {
  const el = document.createElement('div')
  el.className = `t ${spec.className ?? ''}`
  Object.assign(el.style, spec.style ?? {})
  const words = []
  for (const [text, cls] of spec.parts) {
    const pieces = text.split(/(\s+|\n)/)
    for (const piece of pieces) {
      if (piece === '') continue
      if (piece === '\n') {
        el.append(document.createElement('br'))
        continue
      }
      if (/^\s+$/.test(piece)) {
        el.append(document.createTextNode(' '))
        continue
      }
      const w = document.createElement('span')
      w.textContent = piece
      w.style.display = 'inline-block'
      w.style.whiteSpace = 'pre'
      if (cls) w.className = cls
      el.append(w)
      words.push(w)
    }
  }
  parent.append(el)
  return { el, words }
}

/**
 * Pose a block at local time u. It reveals over [inAt, inAt + dur] word by word and leaves over
 * [outAt, outAt + outDur]. rise is in px, blur in px. Words that have not started are invisible.
 */
export function pose(b, u, { inAt = 0, stagger = 0.07, dur = 0.9, rise = 22, blur = 9, outAt = Infinity, outDur = 0.35, outRise = -10 } = {}) {
  const out = e(u, outAt, outAt + outDur, ease.leave)
  b.words.forEach((w, i) => {
    const k = e(u, inAt + i * stagger, inAt + i * stagger + dur, ease.settle)
    const op = clamp01(k * 1.6) * (1 - out)
    const y = (1 - k) * rise + out * outRise
    const bl = (1 - k) * blur
    w.style.opacity = op.toFixed(4)
    w.style.transform = `translate3d(0, ${y.toFixed(2)}px, 0)`
    w.style.filter = bl > 0.05 ? `blur(${bl.toFixed(2)}px)` : 'none'
  })
  b.el.style.visibility = u < inAt - 0.001 || out >= 1 ? 'hidden' : 'visible'
}

/** Show a block fully, with no motion (for held frames such as the poster). */
export function still(b, opacity = 1) {
  for (const w of b.words) {
    w.style.opacity = '1'
    w.style.transform = 'none'
    w.style.filter = 'none'
  }
  b.el.style.opacity = String(opacity)
  b.el.style.visibility = opacity > 0 ? 'visible' : 'hidden'
}
