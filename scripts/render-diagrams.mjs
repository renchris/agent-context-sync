#!/usr/bin/env node
// Render docs/diagrams/*.mmd → <name>-dark.svg / <name>-light.svg via
// beautiful-mermaid (the ELK-based engine behind Cursor's agent panel).
//
// Usage:  npm run diagrams          — (re)render all SVGs
//         npm run diagrams:check    — exit 1 if committed SVGs are stale (CI)
//
// Diagrams render transparent on GitHub's exact dark/light palettes and are
// embedded in README.md via <picture> + prefers-color-scheme. To change a
// diagram: edit its .mmd here, run `npm run diagrams`, commit both.
//
// Semantic colors: .mmd classDef lines use @tokens (e.g. fill:@green-bg)
// substituted per variant from PALETTE below, so one source renders with
// hand-tuned colors on BOTH GitHub color modes.
import { renderMermaidSVG, THEMES } from 'beautiful-mermaid'
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const DIR = fileURLToPath(new URL('../docs/diagrams/', import.meta.url))
const CHECK = process.argv.includes('--check')

const PALETTE = {
  // Colour means cost (docs/design/VISUAL.md): neutral = zero-byte work,
  // amber = reads file bytes, green = free early exit, red = expiry/failure.
  dark: {
    text: '#e6edf3', muted: '#8b949e',
    'neutral-bg': '#161b22', 'neutral-fg': '#3d444d',
    'amber-bg': '#2d2111', 'amber-fg': '#f0a33a',
    'green-bg': '#0f2a19', 'green-fg': '#3fb950',
    'red-bg': '#2d1417', 'red-fg': '#ff7b72',
  },
  light: {
    text: '#1f2328', muted: '#59636e',
    'neutral-bg': '#f6f8fa', 'neutral-fg': '#d1d9e0',
    'amber-bg': '#fff1dc', 'amber-fg': '#b35900',
    'green-bg': '#dafbe1', 'green-fg': '#1a7f37',
    'red-bg': '#ffebe9', 'red-fg': '#cf222e',
  },
}

// Arrowheads take the muted grey, not GitHub blue: colour is reserved for cost.
const VARIANTS = [
  ['dark', { ...THEMES['github-dark'], accent: '#8b949e' }],
  ['light', { ...THEMES['github-light'], accent: '#59636e' }],
]

// Tokens are substituted only on styling lines, so label text such as
// `@odata.deltaLink` is never mistaken for a palette token.
function applyPalette(src, variant) {
  return src
    .split('\n')
    .map((line) =>
      /^\s*(classDef|style|linkStyle)\b/.test(line)
        ? line.replace(/@([a-z][a-z0-9-]*)/g, (match, token) => {
            const value = PALETTE[variant][token]
            if (!value) throw new Error(`unknown palette token ${match}`)
            return value
          })
        : line,
    )
    .join('\n')
}

// .mmd sources stay native-mermaid-valid (angle brackets as &lt;/&gt; so the
// README's interactive fence renders them); beautiful-mermaid does NOT decode
// entities, so decode before rendering. &amp; is decoded last.
function decodeEntities(src) {
  return src
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"')
    .replaceAll('&amp;', '&')
}

// GitHub serves README images through a proxy that blocks external loads, so
// the default Google-Fonts @import can never resolve there. Strip it and pin
// GitHub's own font stack for a native look.
const GITHUB_FONTS =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
function githubReady(svg) {
  return svg
    .replace(/^\s*@import url\([^\n]*\);\s*$\n?/m, '')
    .replaceAll("'Inter', system-ui, sans-serif", GITHUB_FONTS)
}

const README = fileURLToPath(new URL('../README.md', import.meta.url))
const ROOT = fileURLToPath(new URL('../', import.meta.url))

// README <details> blocks carry a native ```mermaid fence as the interactive
// fallback (zoom/pan/select on github.com). Fence bodies are auto-synced from
// the .mmd sources (dark palette baked — native mermaid can't switch per mode).
const FENCE_RE =
  /(<!-- mermaid-fence: (\S+) \(auto-synced by `npm run diagrams`\) -->\n```mermaid\n)([\s\S]*?)(```)/g
function syncReadmeFences() {
  if (!existsSync(README)) return false
  const readme = readFileSync(README, 'utf8')
  const updated = readme.replace(FENCE_RE, (_m, head, relPath, _body, tail) =>
    head + applyPalette(readFileSync(join(ROOT, relPath), 'utf8'), 'dark') + tail,
  )
  if (updated === readme) return false
  if (!CHECK) writeFileSync(README, updated)
  return true
}

const sources = readdirSync(DIR).filter((f) => f.endsWith('.mmd')).sort()
if (sources.length === 0) {
  console.error(`no .mmd sources found in ${DIR}`)
  process.exit(1)
}

let stale = 0
for (const file of sources) {
  const src = readFileSync(join(DIR, file), 'utf8')
  for (const [variant, theme] of VARIANTS) {
    const out = file.replace(/\.mmd$/, `-${variant}.svg`)
    const svg = githubReady(
      renderMermaidSVG(decodeEntities(applyPalette(src, variant)), { ...theme, transparent: true }),
    )
    const path = join(DIR, out)
    if (CHECK) {
      const current = existsSync(path) ? readFileSync(path, 'utf8') : null
      if (current !== svg) {
        console.error(`STALE: ${out} does not match ${file} — run \`npm run diagrams\``)
        stale++
      }
    } else {
      writeFileSync(path, svg)
      console.log(`rendered ${out} (${(svg.length / 1024).toFixed(1)} KB)`)
    }
  }
}

const fencesChanged = syncReadmeFences()
if (CHECK) {
  if (fencesChanged) {
    console.error('STALE: README mermaid fence(s) do not match .mmd sources — run `npm run diagrams`')
    stale++
  }
  if (stale) process.exit(1)
  console.log(`all ${sources.length * VARIANTS.length} SVGs + README fences up to date`)
} else if (fencesChanged) {
  console.log('synced README mermaid fence(s)')
}
