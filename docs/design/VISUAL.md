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

**The hero loop** (`docs/media/hero-{dark,light}.svg`, from `scripts/render-hero.mjs`; direction in
[`hero/DIRECTION.md`](hero/DIRECTION.md)) extends the same idea to time. Free work is instant and
neutral, and a byte read is the only slow motion and the only amber. It is not a banner: frame 0 is
the README's governing thought and its four steps as text, outlined in Geist Sans and Mono (OFL),
over a field of 256 source objects of which two are ever read. It adds two light-mode hero tokens.
The cell outline is `#afb8c1`, because `#d1d9e0` is 1.43:1 on white and a field of cells drawn in it
disappears. The filled amber is `#c86a00` (chip and meter), because `#b35900` fills a 9 px chip
rust-brown. That meter is 24 px, which is large text, and 3.81:1 clears the 3:1 bar there.

**What was cut.** No banner of logos, no badge wall, no decorative icons. The README has two
memorable elements, and they do different jobs: the hero loop states the argument, and the
recording of a real refused read proves one step of it. Everything around them stays quiet.
*(Until 2026-09-24 this line said "No hero banner" and named the recording as the one memorable
element. The hero reverses that on purpose: it carries the message rather than decorating it.)*
