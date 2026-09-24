# Visual direction

**One idea: colour means cost.** This repository argues that a sync pipeline should read file bytes
as rarely as possible, so every diagram colours a step by what it costs, and uses colour for nothing
else. A reader can scan any diagram and see where the money goes.

| Role | Meaning in a diagram | Dark (on `#0d1117`) | Light (on `#ffffff`) |
|---|---|---|---|
| **neutral** | zero-byte work: metadata, ids, stat tuples, tokens. The default, and most of every diagram | fill `#161b22` · stroke `#3d444d` | fill `#f6f8fa` · stroke `#d1d9e0` |
| **amber** — *bytes* | reads, downloads, hashes or converts file content. The only expensive thing, and the only loud colour | fill `#2d2111` · stroke `#f0a33a` | fill `#fff1dc` · stroke `#b35900` |
| **green** — *free exit* | an early cutoff where the pipeline stops paying (unchanged, no-op save) | fill `#0f2a19` · stroke `#3fb950` | fill `#dafbe1` · stroke `#1a7f37` |
| **red** — *expiry / failure* | a token expires, a read is refused, a breaker trips. Always leads to a recovery path | fill `#2d1417` · stroke `#ff7b72` | fill `#ffebe9` · stroke `#cf222e` |
| text · muted | labels · edge labels | `#e6edf3` · `#8b949e` | `#1f2328` · `#59636e` |

**Type.** GitHub's system stack (`-apple-system, "Segoe UI", "Noto Sans", Helvetica, Arial`),
because GitHub's image proxy blocks web fonts inside `<img>`. beautiful-mermaid fixes label sizes,
so hierarchy comes from weight: the **first line of a node is bold** and names the thing, and the
second line gives the mechanism in regular weight. Edge labels are muted and name a condition.

**Dark and light.** Every diagram ships as a `-dark.svg` / `-light.svg` pair behind `<picture>`,
rendered transparent on GitHub's exact palettes, with the four roles above hand-tuned per mode
(`@tokens` in the `.mmd` sources, substituted by `scripts/render-diagrams.mjs`).

**The recording** uses the same chassis: terminal background `#0d1117`, foreground `#e6edf3`, the
refused read highlighted red and the successful download green, so the demo reads in the same
cost vocabulary as the diagrams.

**What was cut.** No hero banner, badges wall, or decorative icons. The README's one memorable
element is the recording of a real refused read, and everything around it stays quiet.
