# Launch-hero render toolchain: measured comparison

Question: which toolchain should render a 20–40 s silent hero (1920×1080 or 2560×1440 master,
60 fps, frame-exact, deterministic, dark and light variants, Geist Sans/Mono) from which we
derive an MP4 master and an animated WebP for a GitHub README.

Measured 2026-09-24 on an Apple M1 Max (10 cores, 64 GB) under very heavy load. The load average
at each run is quoted beside its timing, and it ran from **~200 to ~630**, well above the ~35 the
brief expected. Every timing below is therefore noisy: read it as an order of magnitude, never as
a benchmark. All heavy commands ran under `nice -n 10`. Scratch lives under `/tmp/hero-launch/`
(`hf-eval/`, `mf-eval/`, `mf-eval-comp/`, `remotion-eval/`, `fonts-eval/`). Nothing was written
inside the claude-infrastructure checkout.

**Shared test composition** (the same design in all three tools): 3 s, 1920×1080, 60 fps
(180 frames), Geist Sans and Geist Mono variable woff2 (npm `geist@1.7.2`, OFL). The frame has a
headline with a CSS `@keyframes` rise (opacity, translate, `filter: blur`), a `steps()` typewriter
line in Geist Mono, a linear progress bar, an infinite CSS spin, and a dot moved by WAAPI
`element.animate()`. The theme comes from a variable.
**Heavy-content probe:** the existing sample film in `tools/motion-film/film/` (four large blurred
blobs, a grain overlay and blend modes), first 2 s = 120 frames.

---

## 1. Comparison table

| | **HyperFrames 0.8.73** | **motion-film (in-house)** | **Remotion 4.0.528** |
|---|---|---|---|
| **Determinism, simple content** (test comp, two runs) | **Byte-identical.** The MP4 sha256 matched across two 6-worker runs and all 180 decoded frames matched. The PNG sequence was 180/180 byte-identical across two 1-worker runs. | **Byte-identical.** 180/180 PNG frames matched across 2 runs. | **Byte-identical.** The MP4 sha256 matched across 2 runs and all 180 decoded frames matched. |
| **Determinism, blur-heavy content** (sample film, 120 frames) | **Near-exact, not bit-exact.** Hardware-GPU PNG: 35/120 frames identical, the rest ≤3/255 on ≤1.26 % of pixels. Software-GPU PNG: 30/120 identical (exactly frames 0–29, the first worker's chunk), the rest ≤3/255 on ≤0.13 %. The two MP4s have different bytes. | **Near-exact, not bit-exact.** 0/120 frames identical, all ≤2/255 on ≤0.16 % of pixels. | not measured |
| **Frame-exact?** | Yes. t = `quantizeTimeToFrame(frame/fps)`, and it seeks rather than records. The bar length per frame matches motion-film to ±1 px. | Yes. t = `from + f/fps`, and it poses rather than records. | Yes. The frame is an integer from `useCurrentFrame()`. |
| **Speed, 1080p** (ms/frame; "agg" = wall time of the capture phase ÷ frames) | Test comp, 1 worker: **97 / 105**. Test comp, 6 workers: **61 / 70 agg**. Sample film, hardware GPU, 4 workers: **86 / 78 agg**. Setup overhead per render was **8.6 s to 2 m 28 s**. | Test comp, 1 process, software raster: **141 / 99**. Sample film: **1078 / 739**, and that is painting only a 1280×720 stage. | Test comp, concurrency 5: **54 / 36 agg** (per-tab capture 62 / 47). Bundling took 30.9 s cold and 3.8 s cached. This ran at a *lower* load (~210–230). |
| **Fonts** (Geist woff2) | Works. The compiler inlines local `@font-face` files as data URIs, and lint requires an `@font-face` for every family named. | Works. The `@font-face` loads relative `file://` URLs, and capture awaits `document.fonts.ready`. | Works. Uses `@remotion/fonts` `loadFont(staticFile())`, which holds the render until the font loads. |
| **Dark/light variants** | Built in: `--variables '{"theme":"light"}'` (measured), plus `--variables-file` and `--batch`, which renders one output per row (documented, not run). | **No parameter.** `capture.mjs` loads `film/index.html` without a query string, so a variant needs a small flag patch or a second page. | Built in: `--props '{"theme":"light"}'` (measured). |
| **Output formats** | mp4, webm and mov (both with alpha), gif, png-sequence (RGBA), hls. **No WebP.** Default MP4 shifts neutral greys by up to 3 levels. png-sequence forces the html, body and root backgrounds to transparent. | Lossless PNG (or JPEG) frames, then `render.sh` encodes libx264 yuv420p bt709 tv-range. Colour is exact to ±1. **No WebP step.** | h264, h265, av1, vp8, vp9, prores, png, gif and others. The h264 path uses **JPEG intermediates** by default. **No WebP.** |
| **License** | Apache-2.0. Telemetry (PostHog) is **on by default**, opt-out by env var. No account is needed to render locally. | In-house, zero dependencies. | **Source-available, not OSI open source.** Free for individuals, organisations of ≤3 people and non-profits; larger companies need a paid license. |
| **Install weight** | 133 MB of node_modules (75 packages) plus a 193 MB pinned chrome-headless-shell 152. It can fall back to system Chrome. Needs system ffmpeg. | **120 KB.** Uses system Chrome 153, which is not pinned and auto-updates. Needs system ffmpeg. | 235 MB (251 packages) plus a 201 MB pinned headless shell 149, plus 164 MB of webpack cache after the first render: **600 MB**. |
| **Lock-in** | **Low** for plain HTML/CSS/WAAPI, or for a `__seek(t)` page via its `hf-seek` event (measured). It rises if you adopt its `data-*` timing, sub-compositions or GSAP registry. | **None.** The contract is two globals (`__duration`, `__seek(t)`). | **High.** Compositions are React components driven by `useCurrentFrame()`, and the docs say not to use CSS animations. |

---

## 2. Evidence per cell

### 2.1 HyperFrames (`hf-eval/`)

**Install and package facts.**
```
$ npm i hyperframes@0.8.73          # load 286 at start
added 75 packages in 25s            ; du -sh node_modules -> 133M
$ npm view hyperframes@0.8.73 license bin
license = 'Apache-2.0'
bin = { hyperframes: 'bin/hyperframes.mjs', 'hyperframes-localize-fonts': ... }
deps: puppeteer-core ^25.10, @puppeteer/browsers, sharp, esbuild, hono, open, fontkit, postcss, ...
time.modified = '2026-09-24T16:47:59Z'  (0.8.73 was published the day of this test; 0.8.70–0.8.73 are consecutive)
$ hyperframes browser ensure        # load 385
Path: <HOME>/.cache/hyperframes/chrome/chrome-headless-shell/mac_arm-152.0.7977.30/...   real 23.00
du -sh .cache/hyperframes -> 194M
```
Every command ran with `HOME=/tmp/hero-launch/hf-eval/home HYPERFRAMES_NO_TELEMETRY=1
DO_NOT_TRACK=1 HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_SKIP_SKILLS=1`, so its config and the
Chrome download stayed in scratch.

**Phones home.** In `dist/cli.js`:
- The telemetry transport posts to `https://us.i.posthog.com/batch/` with a hard-coded `phc_…`
  key. It is disabled only by `HYPERFRAMES_NO_TELEMETRY` or `DO_NOT_TRACK`.
- The written `~/.hyperframes/config.json` holds `"telemetryEnabled": true` and an
  `anonymousId`, so telemetry is on by default.
- `init` did an npm version check even with `HYPERFRAMES_NO_UPDATE_CHECK=1`: the config's
  `lastUpdateCheck` equals the init timestamp.
- The `init` help says it "always checks AI skills against GitHub" unless
  `HYPERFRAMES_SKIP_SKILLS=1` is set.
- The scaffolded blank template loads GSAP from `cdn.jsdelivr.net` at render time.
- `auth`, `publish` and `cloud` are optional HeyGen account features. None were used, and a local
  render needs no account.

**Time model: it seeks.** The runtime `dist/hyperframe.runtime.iife.js` ships adapters named
`css`, `waapi`, `gsap`, `animejs`, `lottie` and `seek-dispatch`:
- **CSS adapter.** For each element with a computed `animation-name`, it takes
  `el.getAnimations()`, sets `currentTime = (t - data-start)*1000` and calls `pause()`. If no
  animations are found, it falls back to `animation-play-state: paused` with a negative
  `animation-delay`.
- **WAAPI adapter.** It patches `Element.prototype.animate` so that script-created animations are
  registered too.
- **GSAP adapter.** It calls `timeline.totalTime(t)`.
- **seek-dispatch.** It fires `window.dispatchEvent(new CustomEvent("hf-seek", {detail:{time}}))`
  on every seek. That event is the hook a pure `__seek(t)` page uses.
- **Frame times** are `quantizeTimeToFrame(frameIdx / fps)`.
- **Capture** asks for `HeadlessExperimental.beginFrame` where it is available. Every macOS run
  here reported `screenshot capture` after trying `drawelement`.
- **Clock.** No `Date.now`, `performance.now` or `requestAnimationFrame` override was found in the
  runtime. Its lint rule 6 simply forbids `Date.now()` and `Math.random()`.

**Test render, determinism and speed.**
```
$ hyperframes render . -o out/run1.mp4 --fps 60     # load 392
   591.3 KB · 3.0s video · rendered in 44.2s
   screenshot capture · hardware gpu · ... setup 30.2s · capture 10.9s · encode 2.7s   (6 workers)
$ ... -o out/run2.mp4                                 # load 430
   591.3 KB · rendered in 37.6s · setup 16.4s · capture 12.6s · encode 7.3s          (6 workers)
$ shasum -a 256 run1.mp4 run2.mp4
4e9bc747...29f18d  run1.mp4
4e9bc747...29f18d  run2.mp4
$ ffmpeg -i runN.mp4 fr-runN/f%04d.png ; diff of shasum lists -> 0 differing of 180; 180 unique frames
$ hyperframes render . -o out/pngN --format png-sequence --fps 60 -w 1     # load 500 / 499
   9.4 MB · 180 frames · rendered in 50.7s · setup 29.2s · capture 17.4s      (96.7 ms/frame)
   9.4 MB · 180 frames · rendered in 56.0s · setup 36.0s · capture 18.9s      (105.0 ms/frame)
   diff png1.sha png2.sha -> 0 differing; frames are 1920x1080 8-bit RGBA
```
**Fonts.** The render log shows
`[Compiler] Embedded local font file: assets/fonts/Geist-Variable.woff2 (68 KB → data URI)`, and
the same line for GeistMono. The contact sheet (`hf-eval/out/sheet-run1.png`) shows Geist and
Geist Mono and the correct motion at t = 0, 0.5, 1, 1.5, 2.5 and 2.98 s.

**Variants.** The composition declares
`data-composition-variables='[{"id":"theme",...,"default":"dark"}]'` and reads
`window.__hyperframes.getVariables()`.
```
$ hyperframes render . -o out/light.mp4 --fps 60 --variables '{"theme":"light"}'   # load 546
   633.5 KB · rendered in 34.1s · capture 12.6s
```
`hf-eval/out/sheet-light.png` shows the light theme.

**Output formats.** From `render --help`:
`--format mp4, webm, mov, gif, png-sequence, hls (MOV/WebM render with transparency; png-sequence
writes RGBA frames ...)`. There is no WebP, and `--resolution` presets include 1080p and 4k but
not 1440p.

Gotchas measured:
- **MP4 colour shift.** Decoding frame 0 of `run1.mp4` gives background `#0a0a0a` → (7,10,9) and
  the dot `#f4f4f5` → (241,244,243). The light MP4 gives `#fafafa` → (247,250,249). The PNG
  sequence is exact: dot (244,244,245) and border (59,130,246). Encoding motion-film's frames with
  `render.sh`'s ffmpeg line gives (10,10,10) and (245,245,245).
- **Transparent background in non-MP4 formats.** `outputNeedsAlpha(format)` is true for every
  format except mp4 and hls. The CSS it injects is
  `html,body,[data-composition-id]{background:transparent !important;...}`. The PNG pixel at (5,5)
  is (0,0,0,0) even though `#root` sets a background, so the background must be painted on an
  inner layer. That inner-layer fix is inferred from the selector, not tested.

**Portability of a `__seek(t)` page.** I rendered the sample film unchanged except for three edits:
root attributes on `#stage`, `class="capturing"` on `<html>`, and
`window.addEventListener("hf-seek", e => __seek(e.detail.time))`. The film was correct at frame 100
(`/tmp/hero-launch/cmp-sample-100.png`).
```
sample1.mp4  (load 302)  2.0s video · rendered in 44.5s  · setup 30.1s   · capture 10.3s (4 workers) = 86 ms/frame agg
sample2.mp4  (load ~338) 2.0s video · rendered in 2m18.2s · setup 2m5.5s · capture 9.4s              = 78 ms/frame agg
sha256 differ; decoded: 0/120 identical (x264 amplifies small input differences)
png-seq hardware GPU x2 (load 330 / 322): capture 33.3s / 35.2s; setup 2m28.5s / 1m51.4s
  composited over bg: 35/120 identical, differing-pixel fraction median 0.15 % max 1.26 %, worst delta 3
png-seq --no-browser-gpu x2 (load 262 / 200): capture 51.6s / 51.1s (430 / 426 ms/frame agg)
  composited: 30/120 identical (frames 0–29 only), median 0.12 % max 0.13 %, worst delta 3
```
**1440p.** A 2560×1440 composition (`data-width=2560 data-height=1440`) rendered 2560×1440 × 180
frames: `capture 11.4s` (6 workers, 63 ms/frame agg) but `setup 2m 6.0s`, at load 297. The layout
of that probe was not validated, because I mis-set a `zoom` on it. Dimensions and timing only.

### 2.2 motion-film (copied to `mf-eval/` and `mf-eval-comp/`, run only there)

**Contract and loop** (`capture.mjs`):
- Launches system Chrome, found first at the `/Applications/Google Chrome.app` path, with
  `--headless=new --disable-gpu --disable-lcd-text --force-device-scale-factor=1`.
- Sets `Emulation.setDeviceMetricsOverride` to the requested width, height and `--scale`.
- Awaits `document.fonts.ready`, then runs
  `for f: evaluate("window.__seek(t)"); Page.captureScreenshot({format:'png', fromSurface:true})`.
- Uses one process and one tab, strictly sequential.
- Arguments: `--fps --from --to --width --height --quality --format png|jpeg --scale --review`.
  There is no theme or query parameter, and the page path is hard-coded to `film/index.html`.
- The system Chrome is `Google Chrome 153.0.8010.53`. It is not pinned and auto-updates.

**Encode** (`render.sh`): `ffmpeg -framerate FPS -i frames/f%06d.png -vf
"scale=...:in_range=full:out_range=tv,format=yuv420p" -c:v libx264 -preset veryslow -crf 16 -tune
animation -x264-params keyint=FPS:... -colorspace bt709 -color_primaries bt709 -color_trc bt709
-movflags +faststart`. There is no WebP step.

**Existing film, 2 s:**
```
$ node capture.mjs --fps 60 --from 0 --to 2 --width 1920 --height 1080 --format png
  run1 (load 630): captured in 129.4s  real 203.99   -> 1078 ms/frame
  run2 (load 536): captured in 88.7s   real 170.53   ->  739 ms/frame
  framediff: frames=120 pixel-identical=0 differing=120
             differing-pixel fraction median=0.1352% max=0.1642%; worst max channel delta=2
```
**Latent defect, measured.** The film is authored at `--stage-w: 1280px; --stage-h: 720px`
(`film.css:25`). At `--width 1920 --height 1080`, only the top-left 1280×720 is painted: the rest
of frame 100 is a single colour, (11,11,12). `render.sh`'s `RES=1080p|1440p|4k` changes only the
viewport, so its "1080p master" of this film is a 720p picture in a corner. `--scale` is the knob
that works.

**Test composition** (the same HTML; `__seek(t)` drives CSS keyframes and WAAPI through
`document.getAnimations()`):
```
$ node capture.mjs --fps 60 --from 0 --to 3 --width 1920 --height 1080 --format png
  run1 (load 374): captured in 25.3s real 38.79  -> 140.6 ms/frame
  run2 (load 399): captured in 17.8s real 67.13  ->  98.9 ms/frame
  framediff: frames=180 pixel-identical=180 differing=0 ; shasum lists: 0 differing
```
Geist and Geist Mono rendered from `file://` woff2 (`mf-eval-comp/sheet.png`). Cross-tool frame
exactness: the progress-bar length on row 904 at frames 0,1,2,60,120,179 was HF 0,9,18,534,1067,1592
and MF 0,9,18,533,1066,1591.

**1440p:**
`node capture.mjs --fps 60 --to 1 --width 1920 --height 1080 --scale 1.3333333` (load 275) →
`captured in 8.3s` for 60 frames, 138 ms/frame. The PNGs are 2560×1440 and the full-frame layout
checked visually.

**WebP derivation** (the same for every tool once PNG frames exist; this ffmpeg build has no
libwebp encoder, so I used `img2webp`):
`ffmpeg -i f%06d.png -vf "fps=30,scale=1280:-1:flags=lanczos"` followed by
`img2webp -loop 0 -lossy -q 80 -m 4 -d 33` gave **335,462 bytes** for 3 s at 1280×720/30 fps,
`real 5.53`.

### 2.3 Remotion (`remotion-eval/`; a quick install, so it was measured)

```
$ npm i remotion@4.0.528 @remotion/cli @remotion/fonts react@19 react-dom@19   # load 179
added 251 packages in 46s  -> 235M
$ npx remotion browser ensure      # storage.googleapis.com chrome-for-testing-public/149.0.7790.0, 93.5 MB zip
real 21.58 ; node_modules/.remotion -> 201M ; after first render node_modules/.cache -> 164M ; total 600M
$ npx remotion render src/index.ts Hero out/run1.mp4 --log=verbose   # load 209
Bundling done in 30865ms ; Concurrency = 5x ; -vcodec mjpeg (JPEG intermediates) -crf 18
Rendering frames done in 9725ms ; capture => 62ms (n = 180) ; real 45.89
$ ... out/run2.mp4                                                  # load 230
Bundling done in 3819ms ; Rendering frames done in 6561ms ; capture => 47ms ; real 14.73
sha256 run1 == run2 (6120ba7e...dc7648) ; decoded frames 180/180 identical
$ ... out/light.mp4 --props='{"theme":"light"}'  -> Encoded 180/180, 357.3 kB ; #fafafa decodes (250,250,250)
```
**License.** From `LICENSE.md` and `docs/license/faq.mdx` on GitHub:
- Free for "an individual, whether for personal or commercial use", for organisations of up to 3
  people, and for non-profits. A Company License is needed otherwise.
- The FAQ says: "Remotion is source-available software, but it is not open source software
  according to the [OSI] Open Source Definition."
- `LICENSE.md` adds: "In Remotion 5.0, the license will slightly change."
- Consequence for an open-source repo: the individual author is free, but anyone at a company of
  4+ people who clones the repo and renders the hero needs a paid license.
- Telemetry (`docs/telemetry.md`): server-side renders send it only when `licenseKey` is set.

**Time model and determinism** (`docs/flickering.md`, `docs/troubleshooting/css-animations.md`):
- Frames render out of order across tabs, so "animations run purely off `useCurrentFrame()`".
- "Don't use CSS animations": `transition`, `@keyframes` and timers are wrong. The listed
  workaround for CSS is `animation-play-state` plus `animation-delay`.
- React is a required peer (`react >=16.8.0`).

### 2.4 The WAAPI route (the existing hero checker)

`/tmp/hero-webkit/check.cjs` (Playwright; chromium, webkit and firefox) poses animated SVG heroes
with `document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms })` and then takes a
screenshot. That is exactly the `__seek(t)` I gave the test composition in `mf-eval-comp/`, and it
measured **byte-identical across runs and frame-exact** in motion-film.
- **It lets a composition keep `@keyframes` and `element.animate()`** instead of hand-writing
  `render(t)`. HyperFrames' CSS adapter is the same mechanism.
- **Limits.** Documented in `docs/research/cv-design-review-2026-08-26/pipeline/P1-capture.md`:
  it covers CSS animations, transitions and WAAPI only. rAF-driven libraries (GSAP,
  react-spring, Lenis) are invisible to it, and animations inside an `<img>`-embedded SVG cannot
  be reached.

---

## 3. Recommendation

**Author the hero to the neutral pose contract, render it with the in-house motion-film capture,
and encode it with its own ffmpeg step. Conviction: 60 %.**

The contract is one HTML page exposing `window.__duration` and `window.__seek(t)`. `@keyframes`
and WAAPI are allowed, driven through `document.getAnimations()`. Fonts are local Geist woff2
loaded by `@font-face`. The theme comes from a URL parameter.

The pipeline:
1. Render with a *copy* of motion-film `capture.mjs`, patched with a flag that appends
   `?theme=dark|light` and one that points at a pinned headless shell.
2. Capture at `--width 1920 --height 1080 --scale 1.3333333` for the 1440p master.
3. Encode the MP4 with `render.sh`'s ffmpeg line.
4. Build the WebP with `ffmpeg` scale/fps and `img2webp`.

Why, from the measurements:
- **Determinism and frame-exactness are a tie** between motion-film and HyperFrames: both were
  byte-identical on the test composition, and both were within 2–3/255 on blur-heavy content.
  HyperFrames buys nothing here.
- **motion-film's weaknesses are small, known patches,** not re-architecture: no variant flag,
  unpinned system Chrome, and the 1280×720 stage issue, which is avoided by authoring at size or
  using `--scale`.
- **HyperFrames' measured gotchas each need a workaround anyway.** Its MP4 shifts neutral greys by
  up to 3 levels, its PNG sequence forces the root background transparent, telemetry is on by
  default, and 0.8.73 shipped the day of the test.
- **Remotion has the most friction for this repo.** It is source-available rather than open
  source, which is friction for anyone at a larger company who renders it. It also means a React
  rewrite and 600 MB of install.
- **The page stays portable.** Because the page is neutral, HyperFrames can render it unchanged
  through one `hf-seek` listener (measured on the sample film). Choosing motion-film forecloses
  nothing.

**The one thing that would change it: the real hero's per-frame cost.** If the final design is
blur/filter-heavy and sequential software capture measures above ~0.5 s/frame at 1440p, switch the
renderer to HyperFrames. The sample film measured 0.74–1.08 s/frame in motion-film against
~0.08 s/frame aggregate in HyperFrames with GPU and 4 workers, which would make the
3,600-frame two-variant render ~45–65 min against ~5 min (an estimate extrapolated from those
per-frame rates at load 300–600). In that case, render HyperFrames' `png-sequence` with the
background on an inner layer, and keep motion-film's encoder.
