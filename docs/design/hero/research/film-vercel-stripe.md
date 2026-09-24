# Film study: Vercel Ship and Stripe Sessions. Motion grammar for a silent, looping README hero

Written 2026-09-24. Four official films were analysed frame by frame. A fifth, the Stripe Sessions 2025 opener, was checked and rejected; see D.
Frame = 1920x1080 unless stated. "%H" = share of frame height, "%W" = share of frame width. At a 1280x720 README embed, 1 %H = 7.2 px.

**How each number was obtained.** Every number carries one of these tags:
- `[M:cuts]`: measured with `vs-cuts.py`, which flags hard cuts as spikes in the per-frame mean absolute luma difference at 192x108. It was cross-checked with `ffmpeg select='gt(scene,0.3)'`.
- `[M:beats]`: measured with `vs-beats.py`. It finds change points in the frame difference at a 0.2 s lag, with threshold 4. This catches soft transitions and type swaps that are not hard cuts.
- `[M:probe]`: measured with `~/Development/claude-infrastructure/tools/motion-film/motion-probe.py curve`. The probe tracks the vertical centroid and fits easing curves. It separates eased from linear reliably. It does NOT reliably tell apart ease-out families (outQuart, outExpo, outBack), so the family names below are the probe's best fit, not a claim.
- `[M:track]`: measured with `vs-track.py`. It tracks the text bounding box, centroid and contrast per frame at native rate, then fits the same easing set as the probe.
- `[M:type]`: measured with `vs-type.py` on full-res frames. It groups connected glyphs into lines. "Tallest" is the tallest glyph, cap or ascender, and runs about 3–5 % over the true cap height. "x" is the median glyph height, which is close to the x-height.
- `[M:luma]`: measured as the mean frame luma per frame.
- `[M:px]`: a pixel colour sampled from a full-res frame.
- `[E:strip]` / `[E:sheet]`: estimated by eye from 10–30 fps labelled strips or from 1 fps contact sheets.

All helper scripts are in /tmp/hero-launch/films/vs-*.

---

## Sources

| # | Film | URL | Channel | What was used |
|---|---|---|---|---|
| A | **Vercel Ship 2024** (teaser/sizzle, 36 s, 2024-02-27) | https://www.youtube.com/watch?v=PHvw0IP7dEU | Vercel (official) | whole film, 1080p60 |
| B | **Vercel Ship 2025: Opening Keynote**, first 100 s (2025-06-30) | https://www.youtube.com/watch?v=lNmO7fDiyuE | Vercel (official) | 0–100 s: 3 s chrome bumper, 6 s title card, then an 81 s 3D-animated opener film (8.98–90.5 s), then the cut to stage |
| C | **Stripe Sessions 2024 \| Payments updates** (64 s, 2024-04-29) | https://www.youtube.com/watch?v=7CjdgmJEkys | Stripe (official) | whole film, 1080p30. This is the main Stripe film. |
| C' | **Stripe Sessions 2024 \| Upgrades for Connect platforms** (77 s, 2024-04-29) | https://www.youtube.com/watch?v=8WclHauB_KE | Stripe (official) | whole film. Same system as C; used as a cross-check. |
| D | **Stripe Sessions 2025 \| Opening keynote: The future of commerce**, first 125 s | https://www.youtube.com/watch?v=ONIexChUpuw | Stripe (official) | the "Opening and intro" chapter (0–123 s). Checked, then **rejected**; see D. |

**Deviations from the brief.**
1. The Stripe Sessions 2025 opener (D) is a countdown followed by a live-action comedy musical skit. It carries no type, UI or infrastructure grammar, and without sound it means nothing. I substituted the nearest official Stripe launch films from the same event series: the Sessions 2024 product films C and C'.
2. For the Vercel Ship 2025 keynote, yt-dlp's default client returned HTTP 403 on section downloads. The download worked with `--extractor-args youtube:player_client=web_embedded`.
3. Total download: 53 MB.

---

## A. Vercel Ship 2024 teaser (36 s): per-film measurements

| Property | Measurement |
|---|---|
| **t = 0** | Black (#000), with a few hundred white dither particles near centre. |
| **t = 1 s** | A particle "flow field" swirls across the frame. It peaks at 1.05 s (99k lit px) and converges on a stippled white triangle, the Vercel mark `[M:track]`. |
| **t = 3 s** | A solid white triangle. It holds 1.65–2.6 s, then shrinks by 32 % of its area over 2.6–3.0 s. A hard cut at 3.04 s brings in the fragmented "SHIP" glyphs `[M:track]`. |
| **Product known by 3 s?** | **Brand, yes. Product/event, no.** The mark is legible at about 1.7 s. "SHIP" is built by 3.85 s. The full event page (date, "Get Tickets", mono blurb) is on screen by 4.7 s `[E:strip]`. |
| **Shots** | 12 shots including the end cards. Median 2.85 s, min 1.12 s, max 6.5 s. Excluding the end cards: 8 shots, median 2.65 s, min 1.30 s, max 3.65 s `[M:cuts]`. |
| **Cut types** | 8 hard cuts (3.04, 6.10, 8.33, 11.00, 12.33, 14.97, 16.27, 19.92). 2 fade-through-black (24.3, 28.25). 1 particle-assembly entrance (0–1.65 s). 1 particle-disintegration exit (32.0–34.5 s, sweeping left to right). 1 fragment wipe-build (3.03–3.85 s). There are no crossfades and no whips. The cuts are "thematic match cuts": the cursor-dissolves-into-particles motif recurs in 5 shots (4.7, 6.1, 12.3, 16.3, 32.0 s) `[M:cuts]` `[E:sheet]`. |
| **Type: family** | Three voices: 1) a custom display "SHIP" cut into rectangular fragments; 2) a grotesk (Geist Sans, estimated from glyph shapes) for "Vercel Ship" and "Get Tickets"; 3) a monospace (Geist Mono, estimated), in caps, for every piece of information. |
| **Type: size** | SHIP glyphs stand 464 px = **43 %H** tall. End-card mono caps are **4.5 %H**, with a 9.4 %H baseline-to-baseline gap (leading about 2× cap), and each line spans only 22–30 %W. The "VERCEL.COM/SHIP" card is 4.4 %H. The final lockup "Vercel Ship [24]" is 4.3 %H x / 5.6 %H tallest and spans 25 %W. Mono info text on the page is 1.3 %H, which is decorative only `[M:type]`. |
| **Type: case / tracking** | Mono is all caps with the face's native wide set. The sans is sentence case with tight tracking. SHIP is caps. |
| **Words per card** | SHIP = 1 word. End cards: "23 MAY 2024 / NEW YORK CITY" (6 words over 2 lines), "VERCEL.COM/SHIP" (1), "Vercel Ship [24]" (2). |
| **Type: entrance** | **SHIP**: rectangular fragments wipe-grow in left-to-right order. The H stem grows 159→311 px in 0.283 s, eased (probe best fit outQuad, RMSE 0.046; linear 0.154) `[M:probe]`. Fragments start 33–67 ms apart; the whole build takes 0.82 s `[E:strip 30 fps]`. **End cards**: pure opacity fades with no movement, and the fades are **LINEAR**, at a constant +22–25/255 every 100 ms. Line 1 goes 0→100 % in 1.1 s. Line 2 starts 0.8 s later and also takes 1.1 s `[M:track]`. **Lockup**: a linear fade-in lasting **3.9 s** (28.3→32.2 s) with about 2 %W of growth `[M:track]`. |
| **Holds** | Date card: about 3.3 s after its fade-in. URL card: fade-in 1.1 s, hold 1.8 s, linear fade-out 0.9 s. Lockup: no hold; it fades in, then disintegrates over 2.5 s. Black tail: 1.1 s `[M:track]`. |
| **Colour** | Strictly monochrome: #000 background and white type. The macro shots are charcoal #0a0c0e with "screen white" #cac7c8 `[M:px]`. There are **0 accent colours**. 85 % of frames are dark (mean luma < 60) and 0 % are light `[M:luma]`. Light theme is not handled at all. |
| **Visualising the abstract** | Particles and dither stand in for "compute". The mark assembles from a point cloud, and solid type disintegrates into points wherever the cursor passes, filmed as the site's real hover interaction. Fragmented letters suggest modular assembly. |
| **Space / camera** | Real 3D macro filming of a screen. The screen is tilted about 25–35° (estimated) with shallow depth of field, so the pixel grid/moiré is visible. In the logo shot the camera pushes in: vertical extent grows **+18 % over 2.7 s (about 6.5 %/s)** `[M:track]`. The product (the event website) is shown **as the real product**, photographed. |
| **Memorable image** | The cursor drifting over "Get Tickets" and the logo, with the solid type **dissolving into particles** behind it. It sticks because it is the product's own interaction, it is surprising (solid becomes powder), and it reads with no words. |
| **Ending / loop** | End cards, then the lockup dissolves into particles sweeping left to right, then 1.1 s of black. The film **starts on black and ends on black**, so the seam is invisible. This is the most loopable film of the set. |
| **With no sound** | It survives almost entirely. Rhythm comes from hard cuts every 2.65 s (median), particle bursts as visual downbeats, the cursor as a moving focal point, and the end-card fades staggered 0.8 s apart. |

## B. Vercel Ship 2025 opening keynote (first 100 s)

| Property | Measurement |
|---|---|
| **t = 0** | Pure black. |
| **t = 1 s** | A dim fragment of a liquid-chrome triangle morphing on black. Peak contrast is only about 35/255 above the background `[M:track]`. |
| **t = 3 s** | Title card fading up. It goes from 0 to full in **0.3 s** (2.90–3.20) `[M:track]`, then holds **5.8 s** (3.2–8.98). The card reads "Vercel Ship [25]" as a 2.9 %H eyebrow, "Opening keynote" at 9.0 %H tallest (cap about 6.7 %H), and four speaker rows in mono caps at about 1 %H. It is left-aligned at an 11 %W margin inside a hairline frame, with "+" crosshairs at the grid intersections `[M:type]`. |
| **Product known by 3 s?** | **No.** The card names the event, not a product. The opener film starts at 8.98 s on a 3D street. The product (the v0 UI) first appears at 18.6 s, which is 9.6 s into the film `[M:cuts]`. |
| **Shots** (film 8.98–90.5 s) | 25 shots. **Median 2.84 s, min 0.77 s, max 9.5 s** `[M:cuts]`. |
| **Cut types** | 21 hard cuts. 2 cuts on black: 71.17 s (luma 3.4) and 74.01 s (luma 2.1), hard cuts hidden in darkness. 1 morph (65–67 s): black liquid chrome floods the office from the ceiling and turns the colour world into the chrome world. 2 foreground-object wipes: legs crossing the lens at 33.4–35.9 s, and a body passing the badge reader at 37.4–38.2 s. 1 mask collapse transition (76.0–80.7 s). 2 fades to black (80.7–81.0 s and 89.5–90.3 s). 1 crossfade from bumper to title (2.9–3.2 s) `[M:cuts]` `[M:luma]` `[E:strip]`. |
| **Type** | Grotesk (Geist, estimated) for the title card. Mono caps for metadata. Inside the film, type appears only as **real v0 UI** on a tilted monitor: "What can I help you build?" and a prompt typed at **about 22–25 chars/s (40–45 ms/char)**, measured as "apply the liquid shader to this monitor" over 44.4–45.9 s `[M:track]`. |
| **Colour** | Title card is #000 with white type `[M:px]`. The film is warm CG (brick, sunlight). The only product accent is a **cyan** glow on the badge-reader triangle when the badge is tapped (37.0 s) `[E:strip]`. The finale is chrome on black, with mean frame luma **0.0–12/255 from 67 to 90 s** and **0.1 from 81 to 89 s** `[M:luma]`. 75 % of film frames are dark `[M:luma]`. |
| **Visualising the abstract** | Code reflected in the character's glasses (22.8–26.1 s): the work is seen as a reflection, not head-on. A plant grows on the desk (26.1–28.6 s) as a metaphor for growth. The badge reader is a Vercel triangle, so access and identity become a physical object. Prompt leads to world: typing "apply the liquid shader to this monitor" makes the real monitor take the shader (48.6–56 s), so cause and effect are both on screen. The finale compresses **Manhattan into the triangle**: an aerial city seen through a triangle mask that shrinks **from 96 %W to 13 %W in 4.7 s** (best fit outQuad, RMSE 0.034; linear 0.152) `[M:track]`. The message is "the city runs on this". |
| **Space / camera** | Full 3D CG (character animation). Camera moves include low-angle tracking, a top-down view, an orbit around the monitor (48.6–56.1 s, the longest shot at 7.5 s), a tilt up the Statue of Liberty and Empire State in chrome, and a rotating aerial. The UI is the real product composited onto a 3D monitor. |
| **Memorable image** | **The city collapsing into the Vercel triangle** (76–81 s). It sticks because it is one continuous shape change that states the brand promise (all of it lives inside the mark), and the mark is the last thing standing. |
| **Ending / loop** | The chrome liquid triangle on black (81–89.5 s) fades to black, then cuts to the live stage at 90.5 s. The stream opens (0–2.9 s) and closes the film with the same chrome triangle, so it is bookended. On a README, though, the bookend is effectively **invisible**: mean luma 0.1. |
| **With no sound** | It survives poorly. A character story paced by music. The 9 s of near-black chrome at the end is a sound-design moment. Visual rhythm comes only from the cut cadence (median 2.84 s) and camera moves. |

## C. Stripe Sessions 2024 "Payments updates" (64 s), cross-checked with "Connect" (77 s)

| Property | Measurement |
|---|---|
| **t = 0** | Flat dark aubergine **#221a35** (not black) `[M:px]`. The "stripe" wordmark begins a **left-to-right wipe reveal from 0.25 to 0.50 s** `[M:track]`. |
| **t = 1 s** | "stripe" wordmark on a white card, which grew in behind the logo from 0.65 to 0.85 s. Behind it is a violet-magenta gradient, and a 3D iridescent ribbon ("swoosh") rises from the lower right `[M:track]`. |
| **t = 3 s** | "**Announced**" alone on white, in a blurple-to-pink gradient, 22.4 %H tall, spanning 85 %W `[M:type]`. |
| **Product known by 3 s?** | **Brand and "this is an announcement": yes. The product: no.** The product area ("PAYMENTS", selected in a list) arrives at 4.4 s, the first feature ("A/B test") at 6.3 s, and product UI at 7.93 s `[M:cuts]`. In C' the product name "CONNECT" arrives at 5.5 s. |
| **Shots / beats** | Hard-cut shots: C has 14 shots, **median 2.68 s**, min 0.97 s, max 23.07 s. The max is one continuous 27–50 s "one-take" of type and UI morphs. C' has 20 shots, median 2.84 s, min 1.00 s, max 10.78 s `[M:cuts]`. **Beats** (every text or UI state change, including in-shot changes): C has 31 beats, **median 1.77 s**, min 0.80 s, max 6.53 s. C' (7.6–44.6 s) has 26 beats, **median 1.32 s**, min 0.66 s, max 2.50 s `[M:beats]`. |
| **Cut types** (C) | 13 hard cuts, usually paired with a **background flip** between dark, white and saturated gradient. About 17 in-shot type transitions: slam-zoom, slide-in, word-add, knob slide, and push to the next line. 2 logo wipes (in at 0.25–0.5 s, out at 62.3–62.6 s). 1 split-screen. 1 slot-machine roll (57.9–60.9 s, 6 items). No crossfades. The only fades are the background dimming to dark after the final logo (61.1–62.3 s) `[M:cuts]` `[M:track]` `[E:strip]`. |
| **Type: family** | Grotesk throughout (Söhne, Stripe's brand face, estimated from glyph shapes). Weight medium to semibold, about 500–600 (estimated). Mostly **sentence case**. ALL CAPS only for the product-area list (PAYMENTS / CONNECT / REVENUE AND FINANCE AUTOMATION). Tracking is default to slightly tight. No mono anywhere. |
| **Type: size vs word count** | Size falls as word count rises. **1 word**: "Announced" 22.4 %H tallest / 16.5 %H x; "Embed" 23.4 %H; "manage" x-height 16.8 %H; "disputes" x 15.9 %H. **2 words**: "at Sessions" 21.4 %H; "A/B test" about 22 %H; "local currencies" 15.8 %H / 11.9 %H x. **3 words**: "Create ◯ custom rules" cap about 10 %H, with a toggle pill 20.9 %H tall; "See fraud insights" 11.9 %H / 8.8 %H x. **4–5 words**: "Write fraud prevention rules with" about 8–9 %H (estimated). **Product-name end card**: "Radar Assistant" 9.0 %H, "Payment method rules" 8.2 %H, "Embedded components" 10.2 %H. **Secondary**: the list's non-selected items are 15.4 %H and 5.4 %H; the floating "rule chips" are 1.4–4.2 %H. Hero lines span **55–88 %W** `[M:type]`. |
| **Words per card** | C: Announced \| at Sessions \| PAYMENTS \| A/B test \| Create custom rules \| Automatically \| convert prices to \| local currencies \| See → See fraud insights \| Write fraud prevention rules with \| Radar Assistant \| Manage all \| your Stripe accounts \| centrally. That is **median 2 words, max 5**. Sentences are split across cards ("convert prices to" / "local currencies"). |
| **Type: entrances** (numbers) | 1) **Slam-zoom**: hard cut to the word at about 2.4× final size, down to 1.03× in 0.10 s, settled at 0.23 s (height 590→243 px; best fit outExpo-type, RMSE 0.052, linear 0.413) `[M:track]`. In C', "Embed" goes **3.0×→1.0× in 0.23 s** (best fit outQuad, RMSE 0.007, linear 0.176) as a gradient **outline**, then outline fills to solid in about 0.25 s `[M:track]` `[E:strip]`. 2) **Slide-in**: "at Sessions" slides in from the right, left edge 32.9 %W → 11.3 %W (**21.6 %W of travel in 0.33 s**, outQuart-type, RMSE 0.002, linear 0.318) `[M:track]`. 3) **Per-letter type-on**: "components" is added at **43 ms per letter** (10 letters in 0.43 s). The letters rise into the baseline, and the hero word "Embed" **shrinks to 0.57×** at the same time to make room `[M:track]`. 4) **Roll through a clip line**: product names rise **74–76 px = 6.9–7.1 %H in 0.27–0.30 s**, strongly eased (outQuart-type, RMSE 0.017 and 0.029; linear 0.309 and 0.327: weighted). Each exits upward, accelerating, in about 0.20 s (ease-in family, weak fit). One name every **0.50 s** `[M:probe]`. 5) **Selection by scale** (list/rolodex): the scroll takes about 0.27 s (4.53–4.80 s). The selected item is 19.4 %H, its neighbours 15.4 and 5.4 %H. The non-selected items then fade out over about 0.2 s and the selection centres itself `[E:strip]` `[M:type]`. 6) The gradient fill **shimmers**: its colour stops travel across the letters while the word holds, about 0.3–0.8 s `[E:strip]`. **No blur-in.** One slam frame shows ghost echoes of the previous word (motion trail). |
| **Holds** | After the entrance, a word holds 0.6–1.3 s ("Announced" 0.64 s; "at Sessions" 0.1 s still, then 0.67 s of accelerating drift). Beats run 1.3–1.8 s median `[M:track]` `[M:beats]`. |
| **Motion during holds** | Never fully still. "at Sessions" drifts left 11.3→7.3 %W over 0.67 s, accelerating into the cut. The final logo card pulls back −7 % in width over 1.2 s `[M:track]`. |
| **Colour** | Three background modes: dark **#221a35**, white **#ffffff**, and a saturated mesh gradient (magenta **#e539aa**, violet, orange) `[M:px]`. Type is **one horizontal gradient**, #7f75ff → #9f89fe → #c9a5ff → #f9c4fe, left to right `[M:px]`, plus white on dark. There is **one warm secondary accent** (orange/yellow) reserved for *values*: ">25", "1000+", "40–49" `[E:sheet]`. That makes **2 accents** in total. Light/dark: the film **alternates per beat**. C is 64 % dark / 30 % mid / 6 % light frames; C' is 36 / 29 / 36 `[M:luma]`. The same gradient type sits on both white and dark. Contrast of the gradient stops **on white** is 3.56, 2.81, 2.04 and **1.46:1** (the pink end fails); on #221a35 it is 4.65–11.3:1 (computed with WCAG from the sampled colours). |
| **Visualising the abstract** | See "Making plumbing visible" below. In short: the UI control lives inside the sentence, the split-screen *is* the traffic split, chart lines draw on while the camera follows them, condition chips float at several depths, natural language morphs into a code rule, a tree gets drawn, capabilities dock into ghost slots, and a roll of product names closes the film. |
| **Space / camera** | Mostly flat 2D with depth cues: rule chips at several depths with depth-of-field blur, a 3D chrome ribbon behind the logo, and the camera trucking right to follow the head of the chart line (14.8–17.8 s). UI is **abstracted**: redrawn, simplified vector UI at large scale, with fictional brands (Rocket Rides, Pose) and a cursor that narrates by clicking. There are no real screenshots and no device frames, except a stylised browser chrome in C'. |
| **Memorable image** | **"Create ◯ custom rules"**: the word *custom* sits inside a live toggle switch whose knob slides across, and when it flips on the whole screen floods with gradient (18.0–19.7 s). It sticks because the sentence *is* the interface, and the state change fills the world. (Runner-up: the A/B split-screen, where the line between halves moves from 50 to 42.7 %W as the slider sets 30/70 `[M:track]`.) |
| **Ending / loop** | Slot-roll recap of 6 product names at 0.5 s each (57.9–60.9 s). Hard cut to the "stripe" logo card (61.03 s). The background dims to dark over 1.2 s, the logo wipes out left to right in 0.3 s (62.3–62.6 s), and then **1.4 s of flat #221a35**, the same as frame 0 `[M:track]` `[M:luma]`. The film is **bookended on an identical dark frame**, so it is loop-ready. C' ends the same way: "Available now" on white for 1.2 s, the logo card, 1.4 s of dimming, 0.9 s of dark. |
| **With no sound** | It survives completely. The kinetic type *is* the voice-over, so the film is its own captions. Rhythm comes from: slam-zoom arrivals (a 2.4–3× → 1× snap in about 0.1–0.23 s) as downbeats; **background flips** (dark/white/magenta) as accents; 43 ms/letter type-on and the 0.5 s roll as sub-divisions; and cursor clicks as syncopation. Beats land every 1.3–1.8 s. |

## D. Stripe Sessions 2025 opener: checked and rejected

The first 125 s run as follows `[E:sheet]` `[M:cuts]`:
- 0–5.5 s: an iridescent 3D ribbon (orange → magenta → violet) behind the "stripe sessions" wordmark, on light grey.
- 6–14.5 s: a countdown "09"→"01" at **1.0 s per number**, on skewed red-orange cards that rotate in 3D.
- **15–46 s: blank grey dead air (31 s)**.
- 46–56 s: a second countdown.
- 56–123 s: a live-action comedy musical skit, with the captions "Time to Sessions / 1 minute".
- 123 s: the stage.

Nothing here is usable for a silent README. The countdown is the only kinetic-type device, and it only works live.

---

## Grammar rules to steal

1. **Slam, then settle.** Cut each keyword in at **2.4–3× its final size** and settle to 1.0× in **200–250 ms** with a strong ease-out. Linear was rejected in both measurements (RMSE 0.41 and 0.18 against best fits of 0.05 and 0.007). Then hold at least 0.6 s. At README frame rates, render at 30 fps or more, or stretch the settle to about 300 ms, so the snap is not a 2-frame pop.
2. **One idea per card; size set by word count.** Use 1–3 words per card (Stripe median 2, max 5) and a **beat of 1.3–1.8 s** (median C' 1.32 s, C 1.77 s). Caps: 1 word about **20–23 %H** (x-height about 16 %H); 2–3 words about **12–16 %H**; 4–5 words about **8–10 %H**. Hero lines should span 55–88 %W. At 1280x720 that gives roughly 60–160 px caps. Keep secondary text **at least 2.5 %H (18 px)**. Vercel's 1.3 %H mono (9 px) and Stripe's 1.4 %H chips are decoration, not copy.
3. **Nothing is ever fully still.** During holds, drift or push. Measured: Vercel pushes in **about 6.5 %/s** (+18 % over 2.7 s); Stripe drifts type about 6 %W/s, accelerating into the exit, and pulls the logo back −7 % over 1.2 s. For a loop, run a constant 2–6 %/s push under every hold.
4. **Enter slow-out, exit fast-in, through a clip line.** Items rise **7 %H in 0.27–0.30 s** (ease-out), then leave upward in **about 0.20 s** (ease-in), at **one item per 0.5 s**. Use this slot-machine roll to list 4–6 subsystems or commands in 2–3 s. It loops naturally.
5. **Type-on at about 40–45 ms per character.** Measured: Stripe 43 ms/letter; the v0 prompt 40–45 ms/char. While a line types on, **shrink the hero word to about 0.57×** to make room instead of cutting away. For a developer tool, type the actual command at this speed.
6. **Kinetic moves are eased; fades are linear, slow, and reserved for the quiet end.** Vercel's end-card fades are linear: **1.1 s per line, 0.8 s between lines**, a 1.8 s hold, and a 0.9 s fade-out. Everything that moves is eased. Do not fade the lockup in over 3.9 s inside a README loop; it reads as a stall.
7. **Assemble the mark from its parts, fast.** Particles or fragments should form the logo in **0.8–1.65 s**, with a **33–67 ms stagger** from left to right and each fragment growing in about 0.28 s (eased). The mark is readable by 1.7 s. Then put the **product noun on screen by 3 s**. Neither studied film does this (Stripe reaches the product area at 4.4 s and a feature at 6.3 s; Vercel's product arrives 9.6 s into its film), and a README cannot afford to wait.
8. **Bookend the loop on an identical frame.** Stripe opens on flat #221a35 for 0.25 s and closes on it for 1.4 s. Vercel goes black to black, with 1.1 s of tail. Wipe the mark in from the left and out to the left-to-right as well, so the seam reads as one continuous wipe.
9. **Background flips are the silent drum.** Stripe changes background (dark / white / saturated gradient) on almost every hard cut, and those flips carry the rhythm with no music. For a README that must work in dark and light themes: flip between two mid-to-dark tones rather than to full white, and use a type gradient whose **every stop keeps at least 3:1 on both #ffffff and #0d1117**. Stripe's pale stop #f9c4fe is 1.46:1 on white, so clamp the gradient to its blurple-to-lilac half.
10. **Select by scale, not by highlight.** In a list/rolodex, the chosen item is **19.4 %H**, its neighbours 15.4 %H and 5.4 %H. The scroll takes about 0.27 s, and the unselected items fade out within about 0.2 s. This is a strong way to say "this component of the system" without explaining anything.

## Making plumbing visible (every technique observed)

From Stripe (C and C'):
- **The control lives inside the sentence.** "Create [toggle] custom rules": the knob slides, and the switch turning on floods the whole frame with gradient. Configuration becomes a verb you can watch.
- **The split-screen is the data.** A|B halves in different hues. The dividing line moves with the traffic slider (**50 → 42.7 %W**, 30/70), so the allocation is literally a share of the screen.
- **Lines draw on while the camera follows.** The conversion chart's two lines (treatment vs control) draw from left to right over **3 s** as the camera trucks right with the leading end of the line.
- **A field of tokens.** A grid of 40+ price pills in different currencies and flags scrolls past. Breadth and scale are shown by *count*, not by claims.
- **Condition chips at several depths.** Rule inputs ("Card count for IP address is >25", "Amount in USD is 1000+") float around the headline at different sizes (1.4–4.2 %H) with depth-of-field blur. *Values* take the warm accent. The rule engine's inputs become a visible cloud.
- **Natural language → code.** The prompt "block transactions from customers who've had a lot of declines" morphs into the rule `Block if :declined_charges_per_customer_all_time: > 25`, with highlighted tokens. Intent turns into executable config on screen.
- **Hierarchy drawn as connectors.** An organisation node grows tree lines to 4 accounts, which populate one by one.
- **Capabilities dock into ghost slots.** Pills fly in from the right into *empty placeholder capsules* already waiting in a stack ("pay out instantly", "use apps", "calculate tax"…). The empty slots show capacity before it is filled.
- **Sequential checklist and progress.** Onboarding steps tick one after another, then a progress donut. Process is shown as state.
- **A roll of product names as the recap** (0.5 s each). The whole surface area goes by in 3 s.
- **Redrawn, simplified UI at large scale with a narrating cursor.** No screenshots: only the 1–3 controls that matter, and clicks cause the next state.

From Vercel (A and B):
- **Particles and dither as the substrate.** The mark condenses out of a flowing point cloud, and solid type breaks back into points where the cursor touches it. Compute and network become grain.
- **Modular fragment assembly.** SHIP is cut into rectangles that wipe in with a staggered order. The system is built out of parts.
- **The blueprint grid.** Hairline frame lines with "+" crosshairs at the intersections: the layout grid itself looks like infrastructure.
- **The work seen in reflection.** Code scrolls in the developer's glasses. The invisible work shows up on a surface instead of head-on.
- **Cause → effect on the same screen.** A typed prompt ("apply the liquid shader to this monitor"), then the physical monitor takes the effect.
- **The world compressed into the mark.** A city aerial inside a triangle mask shrinks from 96 %W to 13 %W over 4.7 s: "all of this runs inside this".
- **Access as an object.** A badge reader shaped like the triangle glows cyan on tap: identity and permission as a physical click.

## Do not steal

**Needs sound:**
- The Stripe Sessions 2025 opener skit (a musical number) and its countdown (1 s per tick only makes sense live).
- Vercel 2025's 9 s chrome-triangle finale at **mean luma 0.1/255**, a sub-bass moment. In a README it is an invisible black box in dark theme and a black slab in light theme.
- Vercel 2025's character story, whose pacing is carried by music. It runs 81 s and the product arrives 9.6 s in.

**Needs real product footage or a CG budget:**
- The full CG character film (Vercel 2025).
- Macro photography of a real screen with shallow depth of field and a tilt of about 30° (Vercel 2024). It is beautiful, but tilted, blurred type loses legibility at 1280 px, and faking it in code is costly.
- The real v0 UI composited onto a 3D monitor.

**Wrong for a README:**
- A 5.8 s static title card listing speakers (dead air). The 31 s of blank grey in the Sessions 2025 stream.
- Linear fades of 3.9 s on the lockup: in an autoplay loop, a viewer who arrives mid-fade sees a stall.
- Text at 1–1.4 %H (9–10 px at 720 p): Vercel's mono page copy, Stripe's smallest chips.
- The pale end of Stripe's gradient on white (1.46:1).
- **Full-white frames** (Stripe's white cards at luma about 250) flashing every few seconds on a dark-theme GitHub page; limit to one beat, or use a mid tone.
- Dense 1–2 px dither particles (Vercel 2024). They break down under GIF or low-bitrate H.264 compression; keep any particles at 3 px or more at 1280 wide.
- Near-black stretches: Vercel 2025 sits below luma 12 from 67 to 90 s.
- A slam-zoom whose settle is only 0.1 s at GIF frame rates (15–24 fps), where it becomes a 2-frame pop.

---

## Artefacts (all under /tmp/hero-launch/films/)

**Films** (53 MB total): `vercel-ship24-teaser.mp4`, `vercel-ship25.mp4` (0–100 s), `stripe-payments24.mp4`, `stripe-connect24.mp4`, `stripe-sessions25.mp4` (0–125 s).

**Contact sheets** (1 fps, 4 columns, labelled):
- `vercel-ship24-teaser-sheet00..01.png`
- `vercel-ship25-sheet00..04.png`
- `stripe-payments24-sheet00..02.png`
- `stripe-connect24-sheet00..03.png`
- `stripe-sessions25-sheet00..05.png`

**Key-frame montages** (t = 0, 0.25, 0.5, 1, 1.5, 2, 3, 4, 5 s): `*-keysheet.png`. The full-res frames are in `vercel-ship24-teaser-key/`, `vercel-ship25-key/`, `stripe-payments24-key/`, `stripe-connect24-key/` (named `t<sec>.png`).

**Full-res type frames**: `stripe-payments24-type/`, `stripe-connect24-type/`, `vercel-ship24-teaser-type/`, `vercel-ship25-type/`.

**Motion strips** (10–30 fps, labelled):
- `stripe-payments24-strip-{announced,list,ab,chart,endroll}.png`
- `stripe-connect24-strip-{embed,pills}.png`
- `vercel-ship24-teaser-strip-{ship,ship30,endcard}.png`
- `vercel-ship25-strip-{walk,finale}.png`

**Slit-scan slices, first 10 s**:
- `stripe-payments24-slice-0-10.png` (plus `-wide.png`)
- `vercel-ship24-teaser-slice-0-10.png` (plus `-wide.png`)
- `vercel-ship25-slice-0-10.png`
- `stripe-connect24-slice-0-10.png`

**Cut lists**: `*.scenes.txt` (ffmpeg scene > 0.3) and `*.mp4.diff.npy` (per-frame difference, histogram correlation and luma).

**Scripts**: `vs-cuts.py`, `vs-beats.py`, `vs-track.py`, `vs-type.py`, `vs-strip.sh`, `vs-sheet.sh`.
