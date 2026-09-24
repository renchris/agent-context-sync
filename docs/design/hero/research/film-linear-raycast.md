# Launch-film motion grammar: Linear and Raycast

Research date: 2026-09-24. I studied four official films, two per company, all 55 s or shorter. They were downloaded video-only at 1080p avc1, so **no audio was analysed**. Anything I say about sound is inference.

**How to read the numbers.** Every number carries a tag. `m:` means measured, and the tag names the tool. `e:` means estimated, and the tag names the method.

- `m:scene`: ffmpeg `select=gt(scene,T)` scene score, at T=0.3 and at T=0.08, plus a per-frame series.
- `m:flow`: `motion-probe.py flow`, Farnebäck optical flow at 320 px width, sampled every 100 ms (125 ms for the 24 fps film). Converted here to % of frame width per second.
- `m:slice` and `m:curve`: `~/Development/claude-infrastructure/tools/motion-film/motion-probe.py slice` and `curve`. For horizontal travel, `curve` was run on a clip rotated 90° with ffmpeg `transpose=1`, so the travel became vertical.
- `m:cc`: OpenCV threshold plus connected components on full-resolution frames. Used for glyph heights, text edges, glyph counts and the easing fits marked "custom fit", which use the probe's own easing set.
- `m:diff`: a per-frame ROI absolute-difference event detector. Used for typing cadence, word swaps and card swaps.
- `m:orb`: ORB feature matching plus partial-affine RANSAC between a shot's first and last frames. Gives scale and translation. Only results with at least 100 inliers are quoted.
- `m:luma`: ffmpeg `signalstats` YAVG on 0–255, and OpenCV max, percentile and Laplacian-variance sharpness in an ROI.
- `m:hsv`: the share of saturated pixels (S > 0.35 and V > 0.25) in a frame.
- `e:strip`: read by eye from frame strips sampled every 33–200 ms. `e:eye`: read by eye from a full-resolution frame.

Glyph sizes are given as % of the film's own frame height (H) and width (W). The Linear films are 1920×960 (2:1). The Raycast films are 1920×1080 (16:9). Where the %W conversion to a 1280 px hero matters, it is marked "derived".

---

## Sources

| slug | URL | Title | Channel | Uploaded | Analysed |
|---|---|---|---|---|---|
| linear-agent | https://www.youtube.com/watch?v=mRql2VJ99gM | Introducing Linear Agent | Linear (official, UCWivgq8xSp7QcXVyPHml-Aw) | 2026-03-24 | full 54.85 s, 1920×960, 60 fps |
| linear-releases | https://www.youtube.com/watch?v=6dIwFoQ0eVg | Introducing Linear Releases | Linear (official) | 2026-04-30 | full 30.0 s, 1920×960, 60 fps |
| raycast-new | https://www.youtube.com/watch?v=Mi173xGb0ZA | New Raycast. Coming 2026 | Raycast (official, UCPvOHaaP9E6FqSqG1NMV_Hw) | 2025-12-18 | full 38.53 s, 1920×1080, 30 fps |
| raycast-windows | https://www.youtube.com/watch?v=QTcUR5BjN2M | A New Start \| Raycast for Windows | Raycast (official) | 2025-11-20 | full 52.83 s, 1920×1080, 24 fps |

**Substitution.** The official Raycast 2.0 release upload is "It's out" (https://www.youtube.com/watch?v=uJ70iGerYzE, 2026-05-14). It is a talking head followed by a capture of the website, not a produced film, and its download also returned HTTP 403 after about 58 s. I dropped it. In its place I used the Raycast 2.0 teaser, "New Raycast. Coming 2026", which is the produced film for 2.0, plus the Raycast for Windows launch film.

Searches for "Raycast AI" found only tutorials and a 2023 "in 55 seconds" explainer. There was no produced launch film.

---

## Per-film measurements

### Linear

| Measure | Introducing Linear Agent | Introducing Linear Releases |
|---|---|---|
| **t = 0 s** | Dark Linear app window in 3D perspective. Sidebar reads Linear / Inbox / My issues / Pulse. Breadcrumb reads "Data loading › Project spec". The doc title "Project spec" is half in focus. The camera is already moving (flow 0.24 px per 100 ms at t=0.1, `m:flow`). | Black field. Glowing hairline "threads" carry issue-ID labels (MOB-9124, ENG-9833) in mono with shallow depth of field. Already drifting. |
| **t = 1 s** | Same shot. The camera trucks left by −7.9 % W over 2.7 s with +0.8 % scale (`m:orb`). "spec" comes into focus by about 1.9 s (`e:strip`). | Same shot. More threads converge toward a trunk. Constant drift of 1.6 %W/s (`m:flow`). |
| **t = 3 s** | Hard cut at 3.05 s to a blank dark panel with an "Ask Linear" pill at bottom right. | Same shot. Threads braid into one trunk. The next cut is at 4.68 s. |
| **Product known by 3 s?** | Brand only, if you recognise the Linear sidebar. The sidebar word "Linear" is about 1.5 % H (`e:eye`). The feature (the agent) first reads at about 7 s, when "Find issues…" is typed. | No. The trunk reaches a "LAUNCH v1.5" node at about 6 s. The title "PLAN AND TRACK RELEASES" arrives at 9.07 s. |
| **Shots** | 10 segments including 2 end cards. Median 5.32 s, min 3.05, max 8.20. Body only (8 shots): median 5.44 s (`m:scene` at 0.08, verified on transition strips). | 7 segments. Median 4.68 s, min 1.68 (title card), max 6.16 (`m:scene` plus `m:luma`). |
| **Cut types** | 9 hard cuts. They score only 0.09–0.20 because every shot is near-black. 0 crossfades, 0 whips, 0 morphs. 1 in-card type transformation: the word re-centres as the tagline writes on. | 5 hard cuts. 1 dissolve through black: the title fades 1.1 s from max luma 255 to 45, and the next shot fades up over about 0.3 s (`m:luma`). 1 in-card clear-and-retype at 24.0 s. The lockup follows 0.3 s of black. |
| **Type: family and case** | Grotesk (Inter-like), semibold "Linear." plus regular "At your command.". Sentence case. | Monospace, uppercase, regular weight, for every title card. Issue labels are also mono. The lockup is the grotesk wordmark plus the logo. |
| **Hero cap height** | "Linear" alone: 'L' is 84 px = 8.75 % H (4.4 % W) at 45.75 s. It shrinks to 69 px = **7.19 % H (3.6 % W)** in the final line. x-height is 49–51 px, a ratio of 0.72 (`m:cc`). The final line is 53.4 % W wide. At a 1280 px hero width, 3.6 % W ≈ 46 px cap (derived). | Typed "RELEASES" / "AVAILABLE NOW": cap 58 px = **6.0 % H (3.0 % W)**. Title "PLAN AND TRACK RELEASES": 52 px = 5.4 % H (2.7 % W), line 51.6 % W wide. Block cursor: 93 px = 9.7 % H. Lockup: 10.4 % H tall, 20.8 % W wide (`m:cc`). |
| **Secondary cap height** | In-UI doc title "Project spec": 33 px = 3.4 % H (`m:cc`). UI body text about 1.5–2 % H (`e:eye`). | Issue labels (MOB-9124): about 28 px = 2.9 % H (`m:cc`). |
| **Tracking** | Tight, about −1 to −2 % (`e:eye`). | Default mono advance. Advance ÷ cap = 0.83: 990 px across 23 characters at a 52 px cap (`m:cc`). No extra letter-spacing. |
| **Words per card** | 1 ("Linear"), then 4 ("Linear. At your command."), then the logo. | 4, then 1, then 2, then the lockup. |
| **Text entrance** | **Wordmark:** hard cut to black, then fade plus blur-in. Max luma climbs 31 → 158 roughly linearly over 45.10–45.60. Focus snaps in the last ~0.15 s: sharpness goes from 3 to 264 between 45.60 and 45.75. Full brightness at 45.95, so 0.85 s in total (`m:luma`). **No rise:** vertical travel is under 1 % H (`m:cc`). The word then shrinks −9.4 % in 1.0 s (`m:orb`) and −18 % by 48.0 s (`m:cc`). A lavender/blue glint sweeps across it at 46.0–46.75 (`e:strip`). **Tagline:** 15 glyphs in 0.77 s (47.22–47.98). The stagger decelerates from 33–50 ms early to 67–133 ms late (`m:cc` glyph count). Each glyph blurs in (`e:strip`). **Line re-centre:** 1.65 s, decelerating. The custom fit ranks outCubic/outQuart best (RMSE 0.065–0.069) against linear at 0.29–0.31, so the move is eased. **In the UI:** result rows appear as scrambled glyphs that decode row by row, with a ~270 ms stagger and ~0.9 s per row. A reply paragraph fades in over ~0.35 s with ~170 ms between lines. Simulated typing runs at ~85 ms/char (all `e:strip`). | **Title:** hard cut in, no entrance. **Type-on:** a block cursor appears at 21.733 s and the first character follows 217 ms later. Characters arrive every **83–100 ms**: 8 characters over 21.97–22.63 s. The line clears instantly at 24.0 s. It retypes 13 characters at **83 ms** each over 24.28–25.30 s (`m:diff`). No blur, no rise. |
| **Hold durations** | "Linear" alone: 1.55 s. Full line after settling: 1.55 s. Logo: 4.45 s, to the end of the film. | Title: 0.93 s at full brightness plus a 1.1 s fade. "RELEASES": 1.37 s. "AVAILABLE NOW": 0.90 s. Lockup: 3.5 s (`m:diff`). |
| **Colour** | Black. The darkest 30 % of pixels have median RGB 0,0,0. The frame median is RGB 4–19, and YAVG per second is 17–36 of 255 (`m:luma`). White type. Colour appears only inside the product: yellow, orange and green status icons plus traffic lights. Saturated pixels are **≤ 0.17 %** of the frame (`m:hsv`). The glint on the wordmark is lavender. There is no light variant. | Black at about RGB 1,1,1, with white and grey hairlines. Two tiny accents: a green "SCHEDULED RELEASE" dot and a blue "CONTINUOUS RELEASE" dot. Saturated pixels **≤ 0.04 %** (`m:hsv`). Dark only. |
| **Space and camera** | 3D. The real product UI sits on a tilted plane, about 30–45° (`e:eye`), with a narrow focus band (`e:eye`). The camera drifts at constant speed: median 0.8–1.9 %W/s per shot, and within shot 1 the flow stays within ±7 % (`m:flow`). There are no push-ins. There is a pull-back of −10.4 % scale over 5.1 s (35.3–40.4 s) and a vertical pan of −12 % H over 3 s (`m:orb`). The UI is real screens with staged data. | 3D. The camera orbits and trucks across a tilted "release graph" plane at a constant 1.6–2.4 %W/s. Flow stays within about ±10 % across a shot (`m:flow`), and the streaks in the slice are straight (`m:slice`). Depth of field throughout. The UI is **abstracted**: a data-viz of issues merging into a release, plus stylised MOBILE APP / SDK / PRODUCTION panels. |
| **Memorable image** | The near-black product UI tilted into a landscape with a knife-thin band of focus. It sticks because a flat SaaS screen becomes an object you fly over. | About 20 issue-ID threads braiding into one trunk that ends at "LAUNCH v1.5". It sticks because the product's data model (many issues become one release) is drawn as a single image. |
| **Ending and loop** | Logo mark on #000 for 4.45 s, with no fade out. The first frame is dark UI at YAVG 23, already moving, against YAVG 17 at the end. A 6/255 luma jump means a hard-cut loop hides well (`m:luma`). | Lockup on black for 3.5 s. The first frame is YAVG 28 and already moving, so it is loopable with a cut (`m:luma`). |
| **Without sound** | Rhythm comes from simulated typing (~85 ms/char), decode staggers (~270 ms/row) and a cut every ~5.4 s. The drift is constant and calm. 30 % of sampled frames are still, mostly in the 10 s end sequence (166 of 548 below 0.05 px, `m:flow`). It survives silence, but slowly. Little of the UI is legible in the first 3 s. | Fully. The typed "RELEASES █" at 83–100 ms per character *is* the beat, and the drift is metronomically constant. |

### Raycast

| Measure | New Raycast. Coming 2026 | A New Start \| Raycast for Windows |
|---|---|---|
| **t = 0 s** | A near-black render of abstract silk or brushed material (YAVG 24, `m:luma`). No UI, and completely static until 0.97 s (flow 0.01 %W/s, `m:flow`). | A 4:3 pillarboxed VHS clip: Windows 95 clouds with a "Start" button (YAVG 116). |
| **t = 1 s** | Hard cut at 0.97 s to a **macro** of the search bar: Raycast mark, caret, and the placeholder "Search" with 'S' at 152 px = **14.1 % H** (7.9 % W) (`m:cc`). | Same clip. Hard cut to black at 1.83 s. |
| **t = 3 s** | Same bar, sliding −7.9 % W over 2.55 s with no scale change (`m:orb`). The slide accelerates into the cut: flow rises from 0.1 to 1.0 px per 100 ms (`m:flow`). Cut at 3.67 s to "Search for apps and commands". | Pixel-font mono typing "DEAR START BUTT…", cap 51 px = 4.7 % H, left-aligned at 22 % W (`m:cc`). |
| **Product known by 3 s?** | Yes. A launcher search bar with the Raycast mark is on screen at 0.97 s. | No. It reads as Windows nostalgia. Raycast UI first appears at about 45.3 s through a dissolve, and the name at 47.7 s. |
| **Shots** | 14 segments. Median 2.32 s, min 0.97, max 8.40 (the text-card run). Body only (11 shots): **median 2.40 s**, min 1.73, max 3.30 (`m:scene`, verified on strips). | 30 shots. **Median 1.29 s**, min 0.12, max 12.16 (the end sequence). Body: 29 shots, max 4.17 (`m:scene` at 0.3). |
| **Cut types** | 12 hard cuts, including a scale jump cut at 16.2 s. 1 **morph**: the mic tile collapses to a dot, which grows into the voice pill (17.2–17.6 s). 1 scroll-through: a list keeps scrolling and becomes the emoji grid (11.3 s). 9 hard word swaps. 1 converge (`m:scene`, `m:diff`, `e:strip`). | 29 hard cuts, including cuts to and from black. 1 glitch/datamosh passage over the BSOD at about 26 s (`e:contact sheet`). 1 frame-shrink push-out (41.7–44.5 s). 1 dissolve from the photo to the Raycast window (45.3–46.1 s) (`e:strip`). 1 fade in from black (`m:luma`). 2 hard card swaps (`m:diff`). |
| **Type: family and case** | The UI is set in the product's grotesk, sentence case. The end cards are **monospace uppercase**, regular, with an italic mono second line ("COMING 2026"). | Pixel bitmap mono in uppercase with RGB fringing. The end cards are a condensed serif in title case with RGB split and scanlines. |
| **Hero cap height** | Macro placeholder: 14.1 % H. Typed query "clipboard": ascender 68 px = 6.3 % H, x-height 53 = 4.9 % H, cap about 6.0 % H (`m:cc`, cap inferred from ascender). Caret: 126 px = 11.7 % H. End-card mono: **31–32 px = 2.9 % H (1.7 % W)**. At a 1280 px hero width that is only about 21 px cap (derived). | Pixel mono: 4.7 % H. End serif: tallest glyph 70–71 px = 6.5 % H, cap about 5.9 % H (`m:cc`, `e:eye` for the cap), line 27.2 % W wide. |
| **Secondary cap height** | Result row "Clipboard History": cap 51 px = 4.7 % H, x-height 37–39 px (`m:cc`). | Not applicable. |
| **Tracking** | End mono: advance ÷ cap = 0.73 (256 px across 11 characters at a 32 px cap, `m:cc`). That is default-to-slightly-tight mono. | Serif at default tracking, condensed (`e:eye`). |
| **Words per card** | 2–4 ("NEW" plus a 1–3-word feature), then "NEW RAYCAST", then "NEW RAYCAST / COMING 2026", then the logo. | 3, then 2, then 3, then the logo. |
| **Text entrance** | **Slot swaps:** "NEW" stays pinned left at 34 % W. The feature word, right-aligned at 67.5 % W, **hard-swaps every 400 ms**, 9 times; the first card holds 667 ms (`m:diff`). **Converge:** both words slide together over **1.45 s** (32.62–34.07 s), each travelling about 11 % W (211 px). `m:curve` on the transposed clip ranks **inOutCubic best, RMSE 0.064, against linear at 0.133**: the move is weighted, S-shaped, with no overshoot in the strips. "COMING 2026" pops in within 1 frame at 34.467 s, with no fade (`m:diff`). **In the UI:** the results panel pops in within 100 ms of the first keystroke. Typing runs at ~120 ms/char, irregular. The caret is on ≥ 0.5 s and off ~0.2 s. The AI menu pops in within 1 frame (33 ms) and settles over about 3 frames (all `e:strip`). The **mic tile slides to centre** in 0.8 s: `m:curve` on a brightness-masked, transposed clip ranks outQuad best (0.091) with linear close at 0.109, so linear cannot be ruled out. Its **shrink** is accelerating: the custom fit ranks inQuad at 0.073 against linear at 0.237. The camera pulls back −63 % scale in 0.9 s into the morph (`m:orb`). | **Typed:** 17 characters in about 1.1 s at irregular 42–125 ms intervals, like a person typing. The underscore cursor blinks with a 250 ms half-period (`m:diff`). **End serif:** fades in from black over 1.08 s (47.67–48.75 s) with an eased-out tail (`m:luma`). It hard-swaps to the second line at 49.58 s, and to the logo at 51.46 s (`m:diff`). No rise: scale changes by −0.26 % and there is no translation (`m:orb`). |
| **Hold durations** | Each slot word: 400 ms. The full lockup ("NEW RAYCAST / COMING 2026") holds 2.20 s. The logo holds 1.87 s, to the end (`m:diff`). | "Discover Raycast": 0.83 s after the fade completes. "Now on Windows": 1.88 s. Logo: 1.37 s. |
| **Colour** | Black, with a grey silk render (median RGB up to 30, highlights up to YAVG 64) and white UI type. Colour comes only from content: emoji, the orange Clipboard icon, and #FF6363 / #56C2FF swatches. Saturated pixels are 0–0.7 %, except the emoji shots at 3.5–5.9 %. End cards are pure black and white (`m:hsv`). Dark only. | Archival full colour: the BSOD frame is 97 % saturated pixels, the Windows 11 blue 66 %. The ending turns to a desaturated dark Bliss hill (≤ 0.5 %, `m:hsv`). Dark ending. |
| **Space and camera** | Flat 2D crops of the **real product UI**, often at macro scale, over a 3D-rendered material backdrop. Moves: a lateral slide that accelerates into its cut; static holds (3.75–5.6 s has zero motion, `m:orb`); vertical pans of −13 to −19 % H over 1.5–1.6 s (`m:orb`); the −63 % pull-back into the morph. No parallax observed (`e:strip`). | Flat archival footage, mostly locked off: under 0.1 % scale change and under 2 % translation on the 4 tested archival shots (`m:orb`). The product appears only at the end, dimmed. |
| **Memorable image** | The "NEW ___" slot machine: one word flips every 400 ms beside a pinned "NEW", then the pair glides together into "NEW RAYCAST". It sticks because it reads a feature list as a drumbeat, in pure type. | The Windows 95 Start button on clouds, followed by "DEAR START BUTTON" typed in pixel mono. It sticks through nostalgia. |
| **Ending and loop** | Logo on black for 1.87 s. The film opens on 0.97 s of near-black static silk (YAVG 24), so the black-to-near-black seam makes this **the most loopable** of the four (`m:luma`). | Logo on a dark Bliss hill (YAVG 26), but the film opens on bright clouds (YAVG 116). That is a ×4.5 luma jump at the seam, so it is not loopable without a fade (`m:luma`). |
| **Without sound** | Good. The 400 ms swaps are a visible metronome; they were probably cut to music, but that is unverified. The caret blink, typing, waveform and "Checking for updates…" spinner all give the eye a rhythm. | Poor. The film is framed as a letter ("Dear Start Button"), which implies a voiced or music-led track (unverified). Silently it reads as a 1.29 s-median nostalgia collage with the product absent for 45 s. |

---

## Grammar rules to steal

1. **Show the product in the first second.** The first legible product frame should arrive by **1.0 s**. Raycast shows its bar, mark and a 14 % H placeholder at 0.97 s. By contrast, Linear Releases names its feature at 9.07 s and Raycast for Windows names the product at 47.7 s.
2. **Use two type roles and no more.** The brand line is a grotesk semibold/regular pair with **cap 7–9 % of frame height** (Linear: 7.2–8.75 % H, 3.6–4.4 % W, about 46–56 px on a 1280 px hero, derived). Labels and kickers are **uppercase mono at 5–6 % H** with default mono advance (advance ≈ 0.73–0.83 × cap). Keep to **1–4 words per card**.
3. **Let typing be the silent beat.** Type at **83–100 ms per character**. Use a block cursor at **1.6× cap height** (93 px against a 58 px cap). Land the cursor **~200 ms before** the first character, and hold **0.9–1.4 s** after the last. Clear the line instantly and retype for the next phrase.
4. **Run a word-slot metronome.** Pin one word and **hard-swap** the other **every 400 ms** (the first hold is 667 ms). Nine swaps take about 4 s. Swaps are 1-frame changes with no fades.
5. **Settle type with an S-curve or a deceleration, never a spring.** The converge takes **1.45 s ease-in-out** (inOutCubic RMSE 0.064 against linear 0.133) for about **11 % W** of travel per word. The Linear re-centre takes **1.65 s decelerating** (outCubic/outQuart family, RMSE 0.065 against linear 0.31). No overshoot is visible in either. The probe does not separate outExpo from outBack, so choose the family, not the exact curve.
6. **Bring the brand mark in from blur.** From black: a **0.5 s** brightness ramp, then focus snaps in the last **~0.15 s**, then a slow scale settle of **−10 % in 1 s** (−18 % by 2.3 s). **No rise**: vertical travel stays under 1 % H. Per-letter follow-ons stagger at **~50 ms**, decelerating to about 100 ms.
7. **Move the camera at constant velocity and ease only the type.** Background drift runs at **0.8–2.4 % of frame width per second**, with speed within **±7–10 %** across a shot. Constant speed hides the loop seam. The two accelerating camera moves I measured both accelerate *into* the next transition: Raycast's slide into its 3.67 s cut (`m:flow`), and its −63 % pull-back into the morph (inQuad shrink).
8. **Keep beats at about 2.4 s.** The Raycast body median is 2.40 s (range 1.73–3.30). Linear's 5.4 s median is too slow for a README. An 8–12 s loop holds **3–4 beats**.
9. **Stay monochrome and let colour come only from content.** Saturated pixels stay **≤ 0.2 %** of the frame on Linear shots and on every type card. Colour lives only inside product content: status dots, emoji, swatches, up to **6 %**.
10. **Bookend in black.** Open on **≤ 1 s** of near-black (YAVG about 24/255). End on the mark held **1.9–4.5 s**. Keep the luma jump at the seam **≤ 10/255** (Raycast New: 24 against 16, `m:luma`) so the loop point disappears.

## Do not steal

- **Full-bleed pure #000 cards** (all four films). On GitHub's light theme they sit as a black slab. On the dark theme the page is not #000, so a #000 frame edge shows. None of the four films has a light-theme treatment; that problem stays unsolved.
- **Linear Agent's 3D-tilted real UI with shallow depth of field.** It needs real product footage plus 3D compositing. With median luminance of 4–7 % and UI text at 1.5–3.4 % H, it turns to mush under GIF/MP4 compression at 1280 px.
- **Linear Releases' 9 s wait before the title, and its 1.1 s dissolve through black.** That runs 6 s past this brief's 3 s test.
- **Raycast New's 2.9 % H end-card mono.** It is fine on a TV, but it is about a 21 px cap at 1280 px (derived) and smaller on mobile. Keep the grammar and raise the size to at least 5 % H.
- **Raycast New's macro crops of real UI over a rendered silk backdrop.** They need the real product at high DPI and a 3D material render. The silk highlights (YAVG up to 64) also fight a light page.
- **Raycast for Windows' archival montage.** Median 1.29 s, min 0.12 s, with BSOD flashes at 97 % saturated pixels, which is a flashing risk in an autoplay loop. It needs licensed footage, depends on nostalgia and presumably voice-over or music, and the product is absent for 45 s.
- **RGB-split, CRT scanline and pixel-font textures** (Raycast for Windows). They compress badly and read as retro-gaming, not dev tool.
- **Anything timed to a music hit.** The 400 ms swap grid survives without sound, but nothing in these films carries emotion silently except type rhythm and the one image.

## Artefacts (all under /tmp/hero-launch/films/)

- **Films:** `linear-agent.mp4`, `linear-releases.mp4`, `raycast-new.mp4`, `raycast-windows.mp4`. About 24 MB kept; about 65 MB transferred in total, counting the failed partial "It's out" attempts.
- **Key frames, full resolution** (t = 0, 0.25, 0.5, 1, 1.5, 2, 3, 4, 5 s): `lr/<slug>/key-t<T>.png`
- **Labelled key-frame montages:** `lr/linear-agent/keys-3x3.png`, `lr/linear-releases/keys-3x3.png`, `lr/raycast-new/keys-3x3.png`, `lr/raycast-windows/keys-3x3.png`
- **Contact sheets** (1 fps, 4 columns, timestamp-labelled, 16 frames per sheet):
  - `lr/linear-agent/contact-1fps-{00-15,16-31,32-47,48-53}s.png`
  - `lr/linear-releases/contact-1fps-{00-15,16-29}s.png`
  - `lr/raycast-new/contact-1fps-{00-15,16-31,32-37}s.png`
  - `lr/raycast-windows/contact-1fps-{00-15,16-31,32-47,48-51}s.png`
- **Fine frame strips:**
  - `lr/linear-agent/strip-{title,input,rows,reply,end}.png`
  - `lr/linear-releases/strip-{title,end}.png`
  - `lr/raycast-new/strip-{intro,results,mid,mic,menu,late,converge,end}.png`
  - `lr/raycast-windows/strip-{open,end}.png`
- **Transition strips:** `lr/linear-agent/transitions.png`, `lr/linear-releases/transitions.png`
- **Slit-scans** (first 10 s, with cut marks): `lr/<slug>/slice-0-10.png`
- **Type crops at native pixel size:** `lr/type-crops.png`, `lr/type-*.png`
- **Data series:** `lr/<slug>.flow.txt` (optical flow), `lr/<slug>.scenes.txt` and `.scenes08.txt` (cuts), `lr/<slug>.scoreseries.txt` (per-frame scene score), `lr/<slug>.yavgseries.txt` (luma)
- **Probe inputs** (transposed or masked clips): `lr/raycast-new/converge-T.mp4`, `lr/raycast-new/mictile-T-thr.mp4`
- **Helper scripts:** `lr/track.py`, `lr/typemeasure.py`, `lr/camscale.py`
