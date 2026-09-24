# Launch film: critique, round 2 (fresh context)

Reviewed build: `ec689a6` plus the round-1 fixes, as rendered on 2026-09-24. What was adopted and
declined is recorded in [`DIRECTION.md` § Decisions (film)](DIRECTION.md#decisions-film).

Material reviewed: the shipped `docs/media/hero-{dark,light}.webp`, decoded with `webpinfo` durations and Pillow (my own per-frame diff and crops, plus the r2 sheets and posters), and the MP4, decoded frame by frame with `ffmpeg -ss`. Source was read only to explain a mechanism (`film/film.js`, `film/world.js`, `film/lib.js`). Rendered size = design px × 0.4365 (838/1920).

Labels: **[M]** means measured, and the method is named. **[E]** means estimated, and the method is named.

---

## Findings, most costly first

### 1. The README still ships the retired SVG hero. LOOP, both grades (integration blocker)

- **What is wrong.** `README.md` lines 1–5 point `<picture>` at `docs/media/hero-{dark,light}.svg`. Both files are deleted in the working tree ([M] `git status`: `D docs/media/hero-dark.svg`, `D …light.svg`). The alt text describes the old SVG ("2 of 256 source objects… four blocks… SharePoint's since-token expires (410)"), and the LOOP shows none of that. Line 1 names `npm run hero` / `scripts/render-hero.mjs`, which is deleted. There is no `<div>` wrapper, no caption line and no film link, although DIRECTION requires all three.
- **Why it costs.** Pushed as it is, the README's first element is a broken image and the film cannot be reached. Every other finding is moot until this is fixed.
- **Fix.** Point `srcset`/`src` at `hero-{dark,light}.webp`. Wrap the `<picture>` in a `<div>` with one caption line that links the MP4. Rewrite the alt text to describe this loop: frame 0 carries the governing thought and two amber pillars, one capped green; then ask, decide and read; then the converted page lands in "One commit." Change the line-1 comment to `scripts/film-render.sh loop`. *(If this is already queued in another change, skip this one, but check it before any push.)*

### 2. The FILM is a captioned slide deck, not a film. FILM 1.6–26.0 (dark), and the LOOP step shots in both grades

- **What is wrong.** Every shot that is not the poster uses the same template:
  - a header bar: the 36 px mono wordmark plus a 32 px muted tagline at y 84–125;
  - one 84 px headline at (114, 166);
  - a diagram below it.

  [M] from the `FILM` edit list and `LINES` (`film/film.js:152-162, 210-222`): 11 shots in 29 s, a mean of 2.6 s each. They carry 9 distinct headlines, plus the two-tier governing thought and the two-line payoff: 12 statements. No FILM shot is free of text. The publish shot (22.8–26.0) has four text blocks on screen at once: the wordmark, the tagline, the two-line headline and "Two files read."
- **Why it costs.** Header, headline, diagram, next slide: that is the grammar of an explainer, and it is the "secondary supplementary infographic" the operator rejected. DIRECTION says the hero should "leave the explaining to the diagrams further down", and launch-research §3 takes "one line alone on the field" from the films studied. Instead, the FILM walks through five mechanisms: the 410, H0/H1/H2, online-only, the no-op save and `git log`. That is README work.
- **Fix (FILM; the LOOP needs the tagline for its every-3-s rule and can keep it).**
  - Drop the wordmark-and-tagline header from 1.6 to 26.0. The film is reached from a link that already names it; bring the wordmark back on the end card only.
  - Cut to about four lines, one per act: "Tens of thousands of files." / no line over the counterfactual (finding 4) / "Only what changed." / the end card (the governing thought plus "Two files read. One commit.").
  - Keep at most one mechanism beat. The no-op save is the strongest, because it is the green cap.
  - Leave two shots with no type at all: the glide and the rise.
  - Change where the type sits by act (the problem centred, the turn left, the end card as it is), so the frame stops looking like a template.

### 3. The key image reads as a 3D bar chart with a data label. LOOP 0–4.5 and 10.3–15.0, FILM 0–1.6 and 22.8–29.0, both grades

- **What is wrong.**
  - **Flat boxes.** The pillars are two-tone boxes (`world.js:139-158`: one flat fill per face). There is no gradient, no contact shadow, and no light on the tiles around them.
  - **Thin and at the edge.** At 838 px they are 35 and 41 CSS px wide ([M] PIL colour mask on `poster-838.png`), and the right one stands 41 CSS px from the frame edge. In the posterIn framing it is 21 design px from the edge ([M] ffmpeg frame 26.02).
  - **Unequal heights.** On screen they are 301 and 262 CSS px tall ([M] same mask): the no-op pillar is 13 % shorter, because `forecast.xlsx` sits two rows deeper (`REPORTED` j 20 against j 18, `film.js:45-47`). DIRECTION rules that "both pillars have the same height, because both reads are one object each".
  - **Chart furniture.** "Two files read. / One commit." sits right-aligned over their heads like a chart annotation, and the green cap is a top segment, which reads as a stacked bar.
- **Why it costs.** This is the frame most visitors see: 9.2 s of the 15 s loop ([M] edit list and webpinfo durations), and the first and last frame of the film. In a grammar where height means cost, the unequal heights say "the no-op save cost less". Two labelled bars on a grid are the definition of an infographic. The "monoliths standing up into the sky" from DIRECTION never happen.
- **Fix.**
  - Put `forecast.xlsx` on row 18, or yaw the poster camera, so both pillars sit at equal depth and are equal on screen.
  - Light the world: a vertical gradient on each face (top 15 % lighter), a soft amber pool on the tiles round each base (bytes read light up their neighbourhood; every other tile stays neutral), and one long shadow across the lanes from a low key light.
  - Lower the camera (`CAM.poster` y 4.5 → about 2.5) and pull the pillars in from the edge onto a third, so the taller one crosses the top third.
  - Move "Two files read. One commit." into the left column as a third tier under "what changed.", so the pillars stand unlabelled.

### 4. The film's most expensive event is drawn as its cheapest-looking motion. FILM 4.4–7.0, dark

- **What is wrong.** In `drawWave` (`film.js:291-329`) every page in the counterfactual rises only 1.6 tiles; one real read is 11 (`PILLAR`, `film.js:70`). The pages are 1.3 px outlines at 35 % alpha, and each eases in over 0.45 s on `settle` rather than at the constant `walk` rate. At 838 px the lines are 0.57 CSS px, so the lanes read as an amber tint with moiré, not as height ([M]: `ffmpeg` frames at 5.2 and 6.9, resized to 838).
- **Why it costs.** The grammar is that height means cost and that a read is the only slow, constant-rate motion. Re-reading everything is drawn at one seventh of one read's height, fast and eased, which inverts the argument at the moment it is made. The film also never stages its own research rule, "show scale as many things collapsing into one": the counterfactual (4.4–7.0) and the two real pillars (12.0–15.8) are four shots and 5 s apart. Launch-research §3 also says "the counterfactual is neutral", but the build spends amber 8 s before the reveal.
- **Fix.**
  - Raise the counterfactual to full pillar height at the `walk` rate, as translucent neutral volumes (or amber outlines of at least 2 design px): a wall of reading that runs from the lens to the horizon.
  - Then **match-cut on the same camera** (waveB) to the true state: a flat field with two pillars. That before-and-after on one framing is the memorable image this film lacks. It also keeps DIRECTION's rules that a cut is the only way time is skipped and that nothing drains.
  - Budget: grow the shot from 2.6 s to about 3.5 s, and take the time from the proof plate (finding 7).

### 5. Half the loop is a still image. LOOP 11.83 → seam → 4.50, both grades

- **What is wrong.**
  - **One 7.67 s freeze.** [M] webpinfo durations plus a per-frame diff (pixels changed by more than 8/255): frame 0 is one stored frame of 4,500 ms. The last frame is 3,033 ms (dark) or 3,100 ms (light). No pixel changes from 11.83 s, through the seam, to 4.50 s. That is 7.67 s of unbroken stillness, 51 % of the loop, in both grades.
  - **Rushed step shots.** Meanwhile the two map shots, which each carry a sentence and new marks, get 1.43 s and 1.37 s ([M] cuts at 4.500, 5.933 and 7.300 s). The green exit rings are fully green for about 0.5 s (6.1–6.6, [E] from `S.exit` and `S.settle` mapped through the `LOOP` edit), while the eye is still on the new headline.
- **Why it costs.** Visitors land mid-loop, so about half of them land inside the freeze ([E] uniform landing time). They see a poster with no sign that it moves: the static diagram again. The project's own research measured frame-0 holds of 2.5–3.5 s and asked for LOOP shots of 2.5–3.5 s "because each carries a sentence to read at 838 px". The build does the opposite of both.
- **Fix.** Keep the loop at 15 s:
  - Head hold: 4.5 → 3.0 s.
  - Tail after "One commit." lands: 3.0 → 1.5 s. Continuous stillness drops to about 4.5 s, 30 %.
  - Give the 3 s saved to the map shots, 1.4 → 2.4 s each, with the green held 1 s after the headline has settled.

### 6. The payoff lands on a letter and then blinks out. LOOP 11.67–11.80, FILM 25.28–25.52, both grades

- **What is wrong.**
  - **It lands on a letter.** `drawLiftAndTravel` (`film.js:416-420`) aims the page at 30 % of the width of "One commit.", which is between "One" and "commit". It stops drawing the page at `S.travel[1]`, while the words are still fading in.
  - **Decoded frames [M].** LOOP 11.70 and 11.75: the page sits on the "e" and the line reads as "On▯commit.". At 11.80 the page is gone. FILM 25.30: the page crosses "Two files read." and "One". At 25.45 it is on the "e"; at 25.53 it is gone.
  - **The target is missing.** The page flies toward words that are not on screen yet (`payCommit` opacity is 0 until s 7.55).
  - **It breaks the rising rule.** The lift and the 60 px hop are upward moves, in a film whose own rule is that "rising means bytes" (`film.js:166`).
- **Why it costs.** This is the only motion that carries step 4 (publish, git), and it ends in what looks like a missing-glyph box and a pop, on the loop's last move.
- **Fix.**
  - Show "One commit." muted from the cut, so the destination exists.
  - Fly the page on a flat or descending path, with no hop, into a slot left of the right-aligned line (`r.left − 40` px).
  - On arrival, turn it into a persistent mark at cap height, such as a commit dot or the page glyph, while the line goes from muted to ink.
  - Never overlap glyphs, and never remove the page before the text has fully arrived.

### 7. The proof plate argues backwards and trims the recording. FILM 15.8–19.8, dark

- **Order.** 12.0–15.8 shows two reads rising ("Read only on purpose."). Then the plate shows a read refused with `bytes=0` ("Online-only stays online."). The viewer is told reads happened, then that they do not, with nothing to bridge the two.
- **Tautology.** The plate opens on `$ readfp off blob.bin`: `off` is passed by hand, so "policy off, no bytes" proves nothing. The line that makes it a finding is cut. In the recording it comes first: `$ launchd-run.sh readfp default /dev/null` prints `policy=off(1)`, which shows that a launchd job's *default* is off. README line 241 builds on exactly that.
- **"Whole lines" is not true.** `film.js:178-179` says "four whole lines… nothing is paraphrased". But the command line drops its recorded comment, `# that policy, on the placeholder`. The plate also inks `(dataless)` and `bytes=0` and mutes the rest, which the recording does not do: it prints everything in `#e6edf3`, and only `read=…(errno 11)` in red. [M] I compared it with the final frame of `dataless-read.webp` (`magick -coalesce`, frame 267). Plate lines 2–4 match it character for character.
- **Label.** "recorded run · docs/media/dataless-read.webp" does not say this is a macOS probe rather than the tool. Under a present-tense product tagline (finding 8), `readfp` reads as the product's command line.
- **Dead time.** 15.8–16.3 is a headline over an empty black frame. The plate is fully printed by 17.7 s and then held for 2.1 s ([E] `PLATE_AT` mapped to film time). At 4.0 s off-world, it is the second-longest shot in the film.
- **Fix.**
  - Move the plate *before* the read, straight after "Decide".
  - Change the headline to "Online-only stays online by default." (README step 3), so the rise that follows reads as the budgeted opt-in.
  - Set the recorded lines whole and in the recording's colours: the `launchd-run.sh` command with its `policy=off(1)` line, then `$ readfp off blob.bin   # that policy, on the placeholder` with its policy, read and after lines.
  - Label it "recorded probe (macOS File Provider) · docs/media/dataless-read.webp".
  - Start typing on the cut and cut the shot to about 3 s.

### 8. The film states a finished product that does not exist. Both cuts, both grades

- **What is wrong.** On every frame that is not the poster, the tagline says "agent-context-sync **keeps** docs/ in sync with Microsoft 365, processing only what changed" (`film.js:144`). The poster sets "Two files read. One commit." like a tool's readout. The word "design" never appears in either cut, yet README line 14 says there is no implementation.
- **Why it costs.** The honesty bar is that nothing may pretend to be output from a working tool. "Keeps", the readout and a terminal plate together tell a viewer the tool exists.
- **Fix.** Use the README's own imperative for the tagline: "keep docs/ in sync with Microsoft 365, processing only what changed", or "a design for keeping…". On the FILM end card (26–29), add one muted line under the wordmark: "a design with measured probes · no implementation yet", at 30 design px or more (13 CSS px).

### 9. The green cap only works for people who see green. Both cuts, both grades

- **What is wrong.** [M] WCAG contrast from the `lib.js` hex values: the green face against the amber face is 1.21:1 in dark and 1.33:1 in light; the green side against the amber side is 1.22:1 and 1.37:1. The silhouette does not change: the cap only recolours the top 0.9 units (`world.js:138-157`). [M] A deuteranopia simulation (Machado 2009) of `poster-838.png` turns the cap into `#ac9e58` on a `#cfba3c` pillar, which reads as a shadow band.
- **Why it costs.** The cap carries the whole "a no-op save is free" story, which is the second pillar's only meaning. DIRECTION claims "shape (a cap) as well as hue", but there is no shape. Red-green colour blindness affects roughly 1 man in 12 ([E] commonly cited prevalence).
- **Fix.**
  - Make it a shape: a slab 25–30 % wider than the pillar and about 0.35 units thick, overhanging on all sides, with a thin background-coloured gap above the amber.
  - Add a lightness step: a lighter green in dark (about `#7ee787` top, `#56d364` face) and a darker one in light (`#116329`), so it still reads in greyscale.
  - Check the result through a greyscale filter.

### 10. A jump cut on the last beat. FILM 26.0, dark

- **What is wrong.** Both closing edits run posterIn → poster (`film.js:220-221`). So the camera dollies out from 22.8 to 26.0, snaps back in at 26.0, and dollies out again. [M] ffmpeg frames at 25.95 and 26.02 with an amber mask: the pillars jump from x 1270–1825 (top y 294) to x 1302–1899 (top y 250) while the type swaps.
- **Why it costs.** Same angle, about 6 % change of scale: it reads as an edit error on the final beat.
- **Fix.** Use one continuous dolly from posterIn to poster across 22.8–29.0, with a hard text swap at 26.0. Or hold 22.8–26.0 locked on posterIn and start the dolly at 26.0.

### 11. The expiry beat shows the effect before its cause, and puts the cause off the safe area. FILM 7.0–10.6, dark

- **Effect before cause.** On the map (7.0–8.4, `walk: true`), SharePoint's walk line starts at 7.5 s, before any 410 has been shown. The 410 first appears at 8.53 s ([E] `S.expire` mapped through the expire edit).
- **A cut that rewinds.** The 8.4 cut takes story time back from s 1.4 to s 0.2 (`film.js:214-215`), so the walk plays a second time. That cut repeats time, which breaks DIRECTION's rule "cut: the only way time is skipped".
- **Chip placement.** The `410 Gone` chip spans y 991–1058 of 1080 ([M] red mask on the 9.5 s key frame). That is outside 90 % title-safe (y ≤ 1026) and under a browser player's control bar. The ground-painted "SharePoint" (x 405–1522, y 886–946 [M]) is the largest type in the shot after the headline, and it upstages the chip.
- **Fix.**
  - Show no walk on the map: SharePoint's lane stays silent.
  - Show 410 → walk → new token once, in the close-up.
  - Raise the chip to about y 800.
  - Cut the ground text from 1.55 to about 0.9 units high.

### 12. The read pillar cuts through the tagline. FILM 15.3–15.8, dark

- **What is wrong.** [M] ffmpeg crop at x 1150–1350 with an amber mask: the top of the `.docx` pillar reaches y 110–116. The tagline's glyphs sit at about y 92–125 in that band ("only" in "processing only"). Muted grey on amber is 1.47:1 [M].
- **Fix.** Pull `CAM.readB` back (z −12.2 → −13, or y 2.4 → 2.0) so the top stays at y 150 or lower. The LOOP's read shot clears at y 158 [M].

### 13. The encoder leaves a ghost of the governing thought in every step shot. LOOP 4.5–10.3, light (dark to a lesser degree)

- **What is wrong.** [M] Pillow on the decoded WebP: the empty sky where the 118 px thought sat still carries its letterforms, 3–4/255 off `#ffffff`, in every step frame. That is 1,160–3,724 px at 3 levels or more inside an 810-px-wide probe band; dark has 2,166 px. The source PNGs captured in the same render, a minute before the WebP was encoded, are exactly `#ffffff` there. So this is an `img2webp -near_lossless 40` inter-frame artefact, not the render. It is visible in the 838 px renders as a faint "by processing only what changed." under "Ask each source what changed."
- **Fix.** Force a full keyframe at each hard cut (4.5, 5.9, 7.3 and 10.3 s: `img2webp -kmin/-kmax`, or encode those frames lossless), then re-verify. Also add a per-frame "maximum deviation from the source" check to `scripts/film-verify.py`, which today checks only the seam.

### 14. The lane names are the smallest type on screen. LOOP 4.5–7.3, FILM 7.0–8.4 and 10.6–12.0, both grades

- **What is wrong.** [M] The glyphs are 21 design px tall (PIL, 8 s key frame), which is 9.2 CSS px at 838. The ground projection shears the two left names left and the two right names right, so they read as mixed italics. Launch-research's own floor is 13 CSS px.
- **Fix.** Raise them to about 2.3 units, or stand them upright at the lane heads. In the LOOP you can drop them entirely, because the poster's subject line already names the four sources.

### 15. H1 ≠ on an Office no-op save contradicts the design's classifier. FILM 19.8–22.8, dark

- **What is wrong.** The design's classifier (`agent-context-sync.md` line 266) reads: "`canonical_hash == row.canonical_hash → TOUCHED_NOT_CHANGED (the Office no-op save): update H0, stop`". In other words, an Office no-op save stops at H1. The film shows `H1 ≠`, `H2 =` "because Office re-saved it" (`film.js:62`). README line 28 and design line 113 back the film ("H2 is what makes that save free"), so the design disagrees with itself.
- **Fix.** Make `forecast.xlsx` a LibreOffice re-save. That is the measured case where H1 differs, and nothing on screen changes; fix the code comment. Then make design line 266 agree with line 113 and README line 28.

### 16. Small ones

- **No sound.** The FILM has no audio track ([M] `ffprobe`: a single h264 stream). The films it is measured against are not silent. Optional: a restrained bed that steps up only on the two reads would carry cost in a third channel.
- **Gap after "docs/".** The mono `docs/` (tracking −0.04em) followed by a sans space leaves a visibly wider gap ("docs/  folder") in the subject and the tagline at 838 px. Put a thin space after the slash, or tighten word spacing on that span.
- **Light-grade field.** Tile fill against white is 1.06:1 and the edges 2.01:1 [M]. At 838 px the light poster's field is a pale hatch, and the portrait pages with folded corners disappear. Darken the near-field cell and edge by one step in light only.

---

## Keep

- **The seam.** 0 px of the last frame differ from frame 0 by more than 8/255, in both grades [M]. The loop point is invisible.
- **Frame 0 answers.** The two-tier governing thought (19 and 51 CSS px) plus the payoff. Every 3 s window of the LOOP holds either the governing thought or the tagline (checked against the `LOOP` edit list).
- **Palette discipline and contrast** [M, WCAG from `lib.js`]:

  | Token on background | Dark | Light |
  |---|---|---|
  | ink | 16.0:1 | 15.8:1 |
  | muted | 6.15:1 | 6.11:1 |
  | amber text | 9.02:1 | 4.83:1 |
  | green | 7.45:1 | 5.08:1 |
  | red on its fill | 6.80:1 | 4.67:1 |

- **The glide down the gutter** (FILM 1.6–4.4). It is the film's one real camera move and its best shot.
- **The constant-rate rise as the only slow motion,** with the read getting the longest shot: 1.6 s in the LOOP, 2.9 s in the FILM ([E] from `S.read` and `rate`).
- **Pages, not squares.** Portrait tiles with folded corners, which read as documents in the close shots.
- **Words arrive by opacity only.** Type never rises, so rising stays with bytes.
- **The H0 / H1 / H2 chip stack.** Legible at 18 CSS px, with green only on "=".
- **Plate lines 2–4** are verbatim against the recording.
- **SharePoint's rings appear as the walk passes them.** That causal detail is worth keeping once the order in finding 11 is fixed.
- **File size.** Locked-off LOOP shots keep each grade at about 1 MB (994,200 and 1,069,414 bytes [M]).

## Verdict

It is closer to a launch film than the SVG was: it has a world, a lens, a real glide and a good central metaphor (height means cost). It still reads as an animated explainer. Every FILM shot is a header bar, a headline and a diagram. Twelve statements go by in 29 s. The one image that should be remembered, a wall of reading collapsing to two monoliths, is never staged: the counterfactual is an amber tint, and the outcome is two flat, unequal, labelled bars at the frame edge. In the LOOP, half of every cycle is a frozen poster, and the payoff lands on a letter. What would still make the operator say "infographic" is the two-bar poster with its label, the persistent header, the map shots with rings and small lane names, the terminal slate and the hash chips. Two things would turn it into a film. First, stage the collapse as one image: a full-height counterfactual, then a match cut to two lit, equal pillars. Second, cut the FILM to about four lines, with two shots left silent. Fix the README integration, the landing collision, the jump cut and the proof plate's order and trimming before anything ships.
