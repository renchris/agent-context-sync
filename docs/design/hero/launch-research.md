# Launch-film research: what the hero takes from it

A synthesis of five measured reports in [`research/`](research/), written 2026-09-24 before and
during the build of the launch film ([`DIRECTION.md` § Launch video](DIRECTION.md#launch-video)).
Every number below is measured in the report it cites; re-derive it there, never quote it from here.

## 1. The medium ([`research/github-medium.md`](research/github-medium.md))

| Fact | Consequence for the hero |
|---|---|
| Only images autoplay; `<video>` is stripped or rewritten | the inline hero is an animated WebP |
| An animated WebP in `<picture>` animates in Chromium 153 and WebKit 26.6, and is not frozen for reduced motion | dark and light grades are possible, and the loop must be gentle: no flashes |
| A user-attachments MP4 is a click-to-play player on frame 0, no autoplay, loop or poster; its URL expires 300 s after render | the film is linked, not embedded; its frame 0 is the poster |
| A lone `<a href="video"><img></a>` is replaced by the player | the hero sits in a `<div>` with a caption line |
| The README column is 838 CSS px | author for 838 px: design px × 0.4365 is the rendered size |
| Upload: `POST uploads.github.com/user-attachments/assets` with the `gh` token; irreversible | the agent can publish the film without a browser session |

## 2. The best README heroes ([`research/readme-heroes.md`](research/readme-heroes.md), 20 measured)

- **Frame 0 is a finished statement, held 2.5–3.5 s.** Only 1 of 20 (vercel/ai) opens on its value in
  words; 11 of 20 open on an empty prompt. Our poster is the answer, held 3 s.
- **Visitors land mid-loop.** The hero sits a median 2,062 px down the page and the clock starts at
  page load. So every 3 s window has to stand alone: ours holds either the governing thought or one
  step's card, with the wordmark always on screen.
- **Loops run 8–15 s** (median 12.6 s). Ours is 15 s.
- **Budget 1–5 MB** (median 1.04 MB, upper quartile 4.89 MB). Ours is 2.5 MB per grade.
- **Text at ≥ 0.75× its capture size.** Ours is authored at 1920 and shown at 838, so every size in
  the film is chosen as design px × 0.4365 ≥ 13 CSS px, and card titles land at 37 CSS px.

## 3. Launch-film grammar (Linear, Raycast, Vercel, Stripe, Apple, Arc: 12 films measured)

| Rule (source report) | Measured | What the film does |
|---|---|---|
| One line alone on the field, big (apple-arc) | hero cap 13 % H for 1–2 words, 9–11 % H for 2–3 | card titles 84 design px (≈ 5.6 % H cap) for 4–5-word lines, because README words are longer than Apple's |
| Two type roles only (linear-raycast) | grotesk + mono | Geist Sans for sentences, Geist Mono for identifiers |
| Beats ~0.9–2.4 s (apple-arc, linear-raycast) | Apple 0.92 s, Raycast 2.40 s, Stripe 1.3–1.8 s | LOOP shots 2.5–3.5 s, because each carries a sentence to read at 838 px; FILM shots 3–4.8 s |
| Settle, never spring (linear-raycast, vercel-stripe) | inOutCubic / outCubic families; no overshoot | per-word rise out of a blur on `settle` (expo out), 60 ms stagger |
| Nothing fully still during holds (vercel-stripe, apple-arc) | 2–6 %/s push; −8 to −10 % shrink per hold | FILM shots drift at a constant rate; LOOP shots are locked off, because a camera move costs ~1 MB/s as WebP (§ 5) |
| Show scale as many things collapsing into one (apple-arc) | collage → one word | tens of thousands of pages → two pillars |
| The state change fills the world (vercel-stripe, "Create ◯ custom rules") | the toggle floods the frame | FILM: every page rises as a hollow outline (the counterfactual), then the cut to one lane |
| The sentence is the interface (vercel-stripe) | the control sits inside the sentence | the converted page lands in the words "One commit." |
| Hold colour back until the reveal (apple-arc) | Arc: 76 % monochrome, blue for the last 2.97 s | amber appears only as the two reads; the counterfactual is neutral |
| Monochrome except content (linear-raycast) | saturated pixels ≤ 0.2 % of frame | the plane is neutral; amber, green and red are all content |
| Bookend on one frame (all) | luma jump at the seam ≤ 10/255 | both cuts open and close on the poster; the LOOP seam is a still frame |
| Type-on 40–45 ms a character (vercel-stripe) | Stripe 43 ms | the proof plate types its commands at 30 ms a character and prints outputs at once, like a terminal |

**Not taken:** full-bleed pure `#000` (a slab on GitHub's light page and a visible edge on its dark
one; the film is graded to `#0d1117` and `#ffffff` instead), real-product 3D footage (there is no
product yet), anything timed to music, flashes and grain (photosensitivity in an autoplay loop),
end-card text under 5 % H.

## 4. Tooling ([`research/tooling.md`](research/tooling.md))

The film is a web page that is a pure function of time (`window.__seek(t)`), photographed frame by
frame over the Chrome DevTools Protocol, then encoded with ffmpeg and libwebp. That in-house pattern
was byte-identical across runs, costs no dependencies and outputs PNG, which the WebP step needs.
HyperFrames 0.8.73 was also byte-identical but has no WebP output, sends telemetry by default and
installs ~330 MB; Remotion 4 is source-available rather than open source. The report's conviction for
the in-house route was 60 %, and building on it confirmed it: one residual is that system Chrome is
not pinned, so a Chrome update can shift anti-aliasing between two renders.

## 5. Encoding (measured during the build)

| Measurement | Result |
|---|---|
| WebP bytes per second at 1280 × 720, 30 fps | locked-off shot 25–70 KB/s; slow camera move ~1 MB/s lossy, ~2.9 MB/s near-lossless |
| The 15 s LOOP at 1676 × 943 (dark) | lossy q70 650 KB · q80 807 KB · q92 1.29 MB · near-lossless 40 **2.46 MB** |
| Flat-region seam (column-profile stdev, `demo-recording` skill; source 0.003–0.054) | every variant ≤ 0.056; the known-bad case is 0.4–0.5 |
| RMS against the source frame | near-lossless 40: 0.80–0.98; q92: 0.99–1.10; q80: 1.80–2.11 |
| Frame timing | `-d 33` for every frame makes a 30 fps loop 14.85 s long; durations alternate 33/33/34 ms so the loop is exactly 15.000 s |

Near-lossless 40 ships: the best fidelity for dark gradients and small type at 2.5 MB, inside the
measured 1–5 MB range.
