# Launch film, round 1: critique of the rendered frames

Scope: LOOP dark and light (`r1-sheet-loop-*.png`, `r1-key-*/`, `r1-loop-*/`), FILM dark
(`r1-sheet-film-dark.png`, `r1-film-dark/`, 2 fps), and the 838 px posters. Source checked:
`film/film.js`, `film/world.js`, `film/lib.js`, `film/type.js`. Claims checked against `README.md`,
`docs/design/agent-context-sync.md`, `docs/media/dataless-read.webp` (its last frame) and
`docs/media/dataless-read.tape`.

How the numbers were obtained:
- **838 px sizes:** design px × 838/1920 = × 0.4365.
- **Pixel colours and pillar extents:** sampled with PIL from the key frames.
- **Contrast:** WCAG 2.x relative luminance, computed from the hex values in `film/lib.js` and from
  the sampled pixels.
- **Seam:** a PIL difference of LOOP t = 13.5 and t = 14.5 against t = 0.
- **Word counts:** the card strings in `film/film.js`, counted by a script.

Every number below is labelled **measured** (how) or **estimated** (method).

---

## Findings, most costly first

### 1. The poster has no hero image: two matchsticks over a row of axis labels, and it reads as a bar chart

**Where:** LOOP 0.0–3.0 and 11.6–15.0 (43 % of the loop, contiguous across the seam), both
themes. FILM 0.0–3.5 and 36–42, and the MP4's frame 0.

**What is wrong:**
- The two reads are thin sticks in the lower-left third. **Measured** (PIL column scan of
  `r1-key-dark/t0000.000.png`):
  - OneDrive pillar: 30 × 223 design px, **13 × 97 CSS px** at 838.
  - SharePoint pillar: 21 × 245 design px, **9 × 107 CSS px**.
- Both pillar tops stop well below the horizon (y ≈ 570), so neither breaks the skyline.
- Under them, four Mono chips (`OneDrive` `SharePoint` `Outlook` `Teams`, 14.0 CSS px) sit on a
  common baseline. Upright amber bars over a row of category labels is a bar chart, and it is read
  as data rather than as a place.
- Between the display line and the plane there is an empty band of about 210 design px (≈ 92 CSS
  px, **measured** by eye from the key frame).
- The payoff `Two read. One commit.` sits in the top-right corner, ≈ 1,100 design px from the
  pillars it describes (**estimated** from the key frame).

**Why it costs:**
- This is the frame most visitors see first and longest, and the one they would describe a day
  later.
- As rendered it is "headline above a chart". The operator rejected exactly that as "a
  supplementary infographic".

**Fix: re-block the wide shot as a monolith composition.**
- Lower the camera toward the hook camera (`hookA`: y ≈ 3.4, pitch ≈ 0.1) and pull the horizon to
  y ≈ 560–620.
- Then the pillars rise through the horizon into the empty sky and silhouette against it. Target
  ≥ 40 % of frame height (≥ 190 CSS px) and ≥ 20 CSS px wide.
- The type block keeps the sky above them.
- Delete the four lane labels from the wide. The subject line already names the sources. If lanes
  must be named, letter the names flat on the ground in perspective, like road markings, not as a
  chip row.
- Move `Two read. / One commit.` so it sits beside the pillars, or set it directly under the display
  line on the left margin. The eye should go headline, then payoff, then pillars in one sweep.

### 2. Every shot is a captioned slide: title plus explanatory subtitle over a diagram stage

**Where:** LOOP 3.0–11.6. FILM 3.5–36. Both themes.

**What is wrong:**
- Every story shot carries a display title and a muted explanatory subtitle, top-left, above a
  separate stage. Examples: LOOP 4.5, 7.5 and 10.5; FILM 4.5, 8.5, 14.0, 18.0, 21.5, 25.5, 30.5 and
  33.0.
- The type never touches the world. The one exception is the page landing in "One commit.".
- On stage, the marks are UI badges:
  - `=` and `≠` chips;
  - the `H0 ≠ / H1 ≠ / H2 =` stack;
  - `410 Gone`;
  - a clip-art document glyph.
- Reading load, **measured** (words counted from the `film.js` card strings):
  - FILM: 152 words in 42 s, **217 wpm**.
  - LOOP: 62 words in 15 s, **248 wpm**.
  - Both before the chips, labels and the proof plate.
- The LOOP's middle section is the rejected SVG's beat list (ring, H0 green flash, read, H2,
  travel) shot in perspective. The storyboard verdict warned of exactly that ("the same infographic,
  tilted").

**Why it costs:**
- A viewer who is reading at 220–250 wpm cannot also watch the plane.
- The film's claim is that almost nothing happens and the one read is loud, so it has to be seen.
  As rendered it has to be read.
- This is the main reason a Linear, Vercel, Stripe or Apple comparison fails: those films carry
  about one short line per shot and let the picture carry the rest.

**Fix:**
- One line per shot, at display scale. Drop the subtitle from every shot except where the line *is*
  the cost, and make that line the byte count. For example:
  - ask shot: `0 bytes.`;
  - decide shot: `Six decided. 0 bytes.`.
- Move the explanations (since-token, "Office re-saved it, so the bytes changed", "pure function")
  to the README body, which already has them.
- In the LOOP, cut the decide shot's four chips. Let the H0 exits be the green-then-grey ring
  alone, with one word on screen.
- Target ≤ 40 words for the LOOP, poster included.

### 3. The read, the only paid motion, is the dullest solid in its own shot

**Where:**
- LOOP 8.5–11.6 (the read close-up).
- FILM 19.6–23.2 (the read) and 28.0–32.0 (the three hashes).
- Both themes; worst in light.

**What is wrong:**
- The close-up camera (`yaw 1.4`) shows mostly the pillar's side face, which `world.js` fills with
  `mix(amber, bg, 0.45)`.
- **Measured** pixel colours:
  - dark close-up, both pillars: `#8a612a`, **3.45:1** on `#0d1117` (3.15:1 on the cell fill);
  - light close-up: `#e1ad73`, **2.01:1** on white. That is exactly the contrast of the tile outline
    `#afb8c1` (2.01:1).
- In the same frames the lifted page glyph is `ink`: **16.0:1** dark and **15.8:1** light.
- So in LOOP 10.5 and FILM 21.5 the loudest object on screen is a text-coloured icon. The amber read
  reads as mud in dark and as pale tan in light.
- This is the "brown at small size" failure that the SVG round fixed for the light chip (`#c86a00`
  instead of `#b35900`), recreated at full-frame size.

**Why it costs:** it breaks the binding rule that a read has to be the loudest thing on screen, at
the exact beat where the read is the subject.

**Fix:**
- Never darken the amber by more than about 15 %.
  - Dark: front face `#f0a33a`, side face about `mix(amber, bg, 0.15)`.
  - Light: front face `#c86a00`, side face `#b35900` (darker, not lighter, on white).
- Aim the close-up so the front face is at least half the pillar's visible width.
- Keep the lifted page muted (`#8b949e` / `#59636e`) until it starts to travel, so that it never
  outranks the pillar it came from.

### 4. For 37 % of landing moments, a 3 s glance never says what the tool does

**Where:** LOOP 3.0–11.6, both themes.

**What is wrong:**
- The governing thought is absent from 3.0 to 11.6. **Measured** from the `LOOP` edit list.
- A 3 s window contains the poster only if it starts before 3.0 or after 8.6. So windows starting
  in [3.0, 8.6], 5.6 s of 15 (**37 %**), show only the wordmark, a step card and a lane name.
- `Ask each source what changed.` does not tell a newcomer that the product keeps a `docs/` folder
  in sync with Microsoft 365.
- DIRECTION's LOOP rule ("the governing thought or one step") is met. The rubric's "knows what the
  tool does after 3 s" is not.
- The poster hold is 6.4 s contiguous (**measured** from the edit list). The 22-word poster needs
  about 5–6 s at 250 wpm (**estimated**), so any visitor who arrives mid-hold is cut off
  mid-sentence.

**Why it costs:** DIRECTION's own research says most visitors land mid-loop, and many of them glance
and scroll.

**Fix:** the wordmark is on screen at every t, so extend it, at no cost in time:
`agent-context-sync · keeps docs/ in sync with Microsoft 365, reading only what changed` in muted,
at 36 design px. Or keep `by processing only what changed.` pinned small under the wordmark in the
story shots. Optionally grow the poster hold to ≥ 8 s by trimming the story shots (see finding 2).

### 5. Honesty: the proof plate's caption misdescribes the recording it quotes

**Where:** FILM 23.2–28.0.

**What is wrong:**
- The plate's four lines are exact substrings of the recording. **Measured** against the last frame
  of `docs/media/dataless-read.webp`.
- The caption, though, says `A recorded run: the read is refused unless a step opts in.`
- The second command, `readfp default blob.bin`, is not a step opting in. The tape's own comment
  reads `# a login shell's default: on`. README lines 233–235 say "The login shell's default is on,
  so the same read downloads 2 MB". README line 243 calls this a hazard ("A stray walk from a login
  shell is a download").
- The plate also drops the `policy=off(1)` and `policy=on(2)` lines, the only evidence of why the
  two reads differ.
- A viewer comes away believing the 2 MB download shows the pipeline opting in. It shows the
  opposite default.

**Why it costs:**
- This is the one real artefact in the film, and the brief's honesty bar is highest here.
- Anyone who plays the README recording 200 lines later will see the mismatch.

**Fix:**
- Re-set the context lines: `policy=off(1)` above the refused read and `policy=on(2)` above the
  download. Both are exact substrings of the recording.
- Re-caption: `Recorded: under a launchd job's policy the read is refused. A login shell's default
  downloads 2 MB.`
- Or keep the tape's own `#` comments on the two command lines, which is the recording's own
  wording.

### 6. Consistency: `docs/ is a pure function of the source` is false for half of `docs/`

**Where:** FILM 32.0–36.0 (the publish card subtitle).

**What is wrong:** `docs/topics/` is written by the agent, not generated:
- README lines 249–251;
- design § 1 item 3 (line 15);
- design § 4.4 (line 326).

Only `docs/mirror/` is a pure function of the source.

**Why it costs:** the film overstates the design at the moment the README repeats the exact claim
correctly.

**Fix:** `docs/mirror/ is a pure function of the source, in git.` (Mono for the path), or
`Converted pages are a pure function of the source.` README line 32 uses the same wording.

### 7. Vocabulary: "page" means two things, "online-only" belongs to the other arm, and the caption omits bytes

**Where:** LOOP 9.0–11.6 and FILM 20.0–23.2 (`Two pages are read. The rest stay online-only.`); FILM
28–32 (`H2 sees the same page`).

**What is wrong:**
- "Page" means a source file on the read card, a converted markdown page on the hashes card, and a
  lifted glyph during travel. The tiles are also drawn as pages with a folded corner, and so is the
  lifted converted page. One word and one shape carry two meanings, which breaks the "no motion
  means two things" discipline.
- "Online-only" is the File Provider placeholder state (README lines 225–226, design line 66). The
  four lanes are Graph sources (README line 97), where nothing is local.
- DIRECTION's own decision ("The arms are coherent … never as a close-up of a Graph read") is broken
  by this caption, over a close-up of Graph reads.
- The card states no bytes, which violates the binding "captions state cost in bytes".

**Fix:**
- `Two files are read. Everything else: 0 bytes.`
- Reserve "page" for converted markdown.
- Draw the lifted page without the folded corner (for example a ruled sheet or a `#` heading mark),
  so the source-file shape and the output shape differ.

### 8. The caption counts eight while the picture shows four

**Where:** LOOP 6.0–8.5 (key frame 7.5). FILM 16.6–19.6 (f000037). Both themes.

**What is wrong:**
- The caption reads `H0 is equal on six of eight`.
- The frame shows **three** `=` and **one** `≠`. **Measured** by counting chips in
  `r1-key-dark/t0007.500.png`.
- The other four objects fall outside the frame, and `placeChips` hides any chip with x > 1880.

**Why it costs:** a viewer who counts finds 3 of 4 and stops trusting the numbers, in a repository
whose whole pitch is measured numbers.

**Fix:**
- Frame all eight: raise and widen the decide camera so that four lanes are visible.
- Or caption only what is shown (`Three of four: equal. 0 bytes.`).
- Or state the count without a fraction (`H0 equal: 0 bytes.`).

### 9. The green cap adds height, fires unexplained, and collides with the lift

**Where:**
- LOOP 10.65–10.95 (the cap) and 10.35–11.15 (the page lift): simultaneous, in two places.
- The poster, at every t.
- FILM 30.0–31.2.

**What is wrong:**
- **Height:** `drawPillar` stacks the cap on top: 11 + 0.9 units. **Measured** on screen, the
  capped SharePoint pillar is **245 px** and the OneDrive pillar **223 px**, although SharePoint
  stands further back.
  - Height means cost, so the free exit reads as the *bigger* read.
  - This breaks DIRECTION's "both pillars have the same height".
- **No carrier:** the LOOP card at that moment says nothing about H2. In the poster, the cap is an
  unexplained 9 × 9 CSS px green block (**measured**: 21 px wide and ≈ 20 px tall in design px).
- **Collision:** in the LOOP it fires in the same 0.3 s as the lift, so two events compete for one
  glance.
- **Rendering:** in FILM 30.0–31.5 an amber sliver (the pillar's top face) shows between the green
  cap and the body.

**Fix:**
- Recolour the top 0.9 of the 11 units green instead of adding height. Do not draw the amber top
  face when a cap is present.
- In the LOOP, either give H2 its own 1 s beat with its own word (`No-op save: no commit.`) or leave
  H2 out of the LOOP and let the FILM carry it.
- Never run the cap and the lift at the same moment.

### 10. Transient colours do not return to neutral

**Where:** FILM 13.4–16.6 (`410 Gone`). LOOP 7.0–8.5 (the green H0 exits).

**What is wrong:**
- **`410 Gone`:** it is set red at story time 1.0 and never cleared (`expired = s >= S.expire`). The
  walk ends at ≈ 16.3 s, **measured** from the edit rate, and the chip is still red at the 16.6 cut
  (f000033).
  - This breaks the binding "held until the walk ends, 400 ms out".
  - It also skips the design's own resolution: "last page: store token" (README since-token
    diagram).
- **Green exits:** in the LOOP the settle runs 8.0–8.5 on an expo-in, so the rings are still about
  50 % green at 8.45 (**estimated** from `ease.leave`). The cut arrives before the viewer sees the
  field go quiet.

**Fix:**
- When the walk finishes, crossfade the chip to a neutral `delta` (the new token is stored) over
  400 ms. This is the one image of "make expiry cheap", and it needs its resolution.
- In the LOOP, start the settle ≥ 0.7 s before the cut (story 4.5–5.0), so the green visibly goes
  back to grey.

### 11. The FILM freezes for 23 % of its running time, and play starts on the frame the viewer just left

**Where:** FILM 0.0–3.5 and 36.0–42.0.

**What is wrong:**
- Both edits are `still: true` with no `move`. That is **9.5 s of 42 (23 %)** of a fully locked
  frame (**measured** from the `FILM` edit list).
- DIRECTION says of the films studied: "never fully still (Apple, Stripe, Vercel all drift or push
  during holds), and the FILM follows them". This render does not.
- A visitor who clicks through from the README poster presses play and watches the same poster for
  another 3.5 s. It reads as a stalled player.

**Fix:**
- Keep frame 0 as the poster, but start a slow push-in or drift at frame 1.
- Cut to the hook by about 1.5 s.
- Hold the ending ≤ 3 s, with a slow drift and the lockup.

### 12. The FILM's edit rhythm does not use its own idea (tempo means cost)

**Where:** FILM 3.5–36.

**What is wrong:**
- Shot lengths, **measured** from the edit list: hook 3.5, wave 3.0, ask 3.4, expire 3.2, decide
  3.0, read 3.6, proof 4.8, hashes 4.0, publish 4.0 (s).
- Free work gets the same screen time as the paid read, and the rhythm is metronomic.

**Why it costs:**
- DIRECTION's central motion idea ("free work is instant … reading bytes is the only slow motion")
  exists only inside shots, never in the edit.
- A launch film's rhythm is its most "cinematic" property.

**Fix:**
- Cut ask, expire and decide as rapid 1.0–1.5 s shots with one word each.
- Give the read the longest unbroken shot of the film: about 4 s, the rise in real time, then a
  held beat.
- The FILM gets shorter, and the read gets louder by contrast.

### 13. The counterfactual wave spends height and "neutral" on a full re-read

**Where:** FILM 7.0–10.0.

**What is wrong:**
- Every tile rises as a hollow **neutral** box.
- In VISUAL.md, neutral means zero-byte work. Here it depicts reading everything, the costliest
  thing in the film. Height also means bytes read.
- So neutral and height each gain a second meaning.
- The cost itself, `means hours of downloads.`, is demoted to the muted subtitle, and the title
  ends without a period (`Re-reading all of it on every sync`).
- At the 838-to-player scale, the dense hollow wireframe also becomes grey moiré (**estimated** from
  f000017 at 1920 px).

**Fix:**
- Draw the counterfactual as a hollow **amber** outline at ≤ 40 % opacity, or dashed. Amber still
  means bytes, and *solid* amber stays reserved for the two real reads.
- Make it one line at display scale: `Re-reading everything: hours of downloads.`

### 14. The read close-up jumps the axis by 80°, and the geography is lost

**Where:** LOOP 8.5 (cut). FILM 19.6 (cut).

**What is wrong:**
- Every other shot looks down the lanes. The read camera has `yaw: 1.4` rad, so it looks across
  them: the lanes turn from receding to horizontal, and no lane label survives.
- The `≠` page that sat front-centre at 7.5 is now right of centre, beside a second pillar the
  viewer never saw decided (the `.xlsx` `≠` was off frame in the decide shot, finding 8).

**Why it costs:** the viewer cannot tell that these are the same objects, or which source each read
came from, at the only beat where it matters.

**Fix:**
- Keep the read camera on the decide camera's side (yaw ≤ 0.4 rad): a lower, closer 3/4 on the
  same `≠` page, with the lane still receding.
- If the `.xlsx` must share the frame, show its `≠` in the decide shot first.

### 15. In the wide shot, the lifted page is detached from its pillar

**Where:** LOOP 11.6–11.7 (f000023–24). FILM 32.0–32.5. Both themes.

**What is wrong:**
- At LOOP t = 12.0 the page hovers at ≈ (510, 678). The OneDrive pillar's top is at (255, 762).
  **Measured** from `r1-loop-dark/f000024.png`: 255 px right of it and in open air.
- Cause: `hoverPoint` projects the lift point with true perspective. `drawPillar` draws verticals
  corrected to vertical (`t0 = sy − V·h/sz`).

**Why it costs:** the commit visibly comes from nowhere, so "the read produced the commit" is lost
in the one shot that carries publishing.

**Fix:** compute the hover point in the same corrected space:
`base = cam.proj(cx, 0, cz); y = base[1] − cam.V·(PILLAR + 0.9 + up·1.3)/base[2]`.

### 16. The commit payoff is too small to see at 838 px, and it is a clip-art icon

**Where:** LOOP 11.7–12.7 (the travel). FILM 32–33.4.

**What is wrong:**
- During travel the page shrinks to k = 0.55 of its size, so it is 11–18 CSS px wide with a
  0.8 CSS px stroke. **Estimated** from `drawPage` sizes × 0.4365. That is below the ≥ 3 design px
  line floor in DIRECTION.
- It crosses about 1,200 design px in 1.0 s.
- It is also the most generic mark in the piece: a file icon with four text lines.

**Fix:**
- Make "One commit." the event: the words write in as the page lands, with a short, fast move.
- Keep the glyph ≥ 24 CSS px with ≥ 2 CSS px strokes all the way, or replace it with a typographic
  mark (a `#` heading line) that reads as markdown.

### 17. Collisions at the decisive moments

- **The label chip cuts the `≠` ring.** In LOOP 4.5 and 7.5 and FILM 17–19.5, the `OneDrive` chip's
  opaque background hides the lower arc of the `≠` ring. The one object that will be read is the one
  the label covers.
  - Fix: anchor lane labels to the lane edge, never to its centre line, in the close shots.
- **The `≠` chip covers the rise.** In LOOP 8.65–9.0 and FILM ≈ 19.75–20.1 (f000040), the `≠` chips
  still sit over the pillars as they start to rise (0.35 s fade from `S.read[0]`). The first frames
  of the only paid motion are hidden behind a neutral badge.
  - Fix: remove the chips before the rise starts.
- **The file name is unreadable.** The `proposal.docx` tag appears for ≈ 0.35 s (LOOP 8.5–8.85,
  **estimated** from the fade) and its first letter is overlapped by the ring. The verdict's floor
  for a caption is ≈ 1.5 s.
  - Fix: hold it ≥ 1.5 s clear of the ring, or cut it.

### 18. Copy that contradicts the picture, or has no noun

- **FILM 28–32: `A no-op save is free.`** It sits over the tallest amber pillar in the film. README
  line 28 uses "free", but on screen the title contradicts the colour system (amber = bytes read)
  and omits bytes.
  - Fix: `A no-op save: one read, no commit.`
- **Poster: `Two read.`** It has no noun ("two what?"), and nothing in the LOOP gives the scale
  it is two out of.
  - Fix: `Two files read. One commit.`
- **Poster: the display line starts with "by".** The largest line is a fragment
  (`by processing only what changed.`), while what the product *is* (docs/, Microsoft 365) is in
  muted grey at 20.1 CSS px. It is acceptable as a kicker and display pair, but the product nouns
  deserve ink.
  - Fix: set the first tier in `ink` at the current size, and keep the display line.

### 19. Type rises too, and cuts open on empty frames

**Where:** every card reveal. LOOP 3.0, 6.0 and 8.5; FILM 3.5, 10.0 and others.

**What is wrong:**
- Titles and subtitles rise 20 px and 12 px out of a blur. "Rise" is the byte-read motion, so the
  same motion means two things.
- The sampled frames at LOOP 3.0, 6.0 and 8.5 show the wordmark only. The title is legible about
  0.6 s after each cut (**estimated** from `pose`: inAt 0.1, stagger 0.06, `settle` with the blur
  under 1 px by x ≈ 0.3).

**Fix:** reveal type with opacity only, or cut it in with the picture on the same frame. A hard-cut
grammar suits hard-cut type.

### 20. Small defects

- **The `git log.` period.** FILM 32–36: the period is set in Mono with the words, so it reads
  `git log .`. Set the period in Sans.
- **Seam twitch (dark only).** The LOOP seam is exact in light (0 px differ, **measured**). In dark,
  975 px differ at the lane labels (bbox (178, 986)–(1266, 1031), max Δ 50/255, **measured** t = 13.5
  and t = 14.5 against t = 0). It is a sub-pixel shift of `OneDrive` and `Outlook`, so the labels
  twitch at the seam. The same story state is not rendering the same pixels.
  - Fix: round label positions to whole px, and reset label styles in `hideAll()`.
- **README alt text (line 4).** It still describes the SVG ("256 source objects in four blocks …").
  Rewrite it in the same change that ships the WebP (open since the storyboard verdict).

---

## Keep (must not be lost in revision)

- **Frame 0 is the answer.** The governing thought in full, over the outcome, legible at 838 px:
  the display line at 45.4 CSS px and the subject at 20.1 CSS px (**measured** conversion).
- **Every text colour passes, in both themes** (**measured**, WCAG):
  - Dark on `#0d1117`: ink 16.0, muted 6.15, amber 9.02, green 7.45, red 7.51 (6.80 on the red
    fill).
  - Light on `#ffffff`: ink 15.8, muted 6.11, amberText `#b35900` 4.83, green 5.08, red 5.36 (4.67
    on the red fill).
- **Identifiers in Mono.** `H0` renders with a slashed zero, not "HO"; `410 Gone` and `docs/` are
  Mono as well.
- **`0 bytes read.` set in ink** as the emphasis of the ask, expire and decide lines. That is the
  cost-in-bytes rule done right.
- **The expire shot (FILM 13.4–16.6).** A red `410 Gone` chip, a metadata walk line crossing
  SharePoint, and rings appearing where the walk passes. It is the clearest single picture of "make
  expiry cheap"; it only needs its neutral resolution (finding 10).
- **The proof plate's discipline.** It is labelled "recorded", it cites its source file, its lines
  are exact substrings of the recording, and it re-colours `bytes=2000000` amber. The recording
  itself shows the download in green, so the plate is the more correct of the two in the cost
  vocabulary. Fix only its caption (finding 5).
- **`H0 ≠ / H1 ≠ / H2 =` on the `.xlsx`.** It matches README lines 213–217 and design line 113:
  Excel re-mints `documentId`, H1 differs, and H2 is identical.
- **What was left out.** No counter, no meter, no logos, no glows. The plane carries "tens of
  thousands" by running past the horizon.
- **Grammar and grades.** A locked-off, hard-cut LOOP. One composition feeds both cuts. The light
  grade has its own tokens (`#afb8c1` outline, `#c86a00` amber), not an inverted dark grade.
- **`What changed since Tuesday is git log.`** A true, memorable line (README line 11) with no
  invented commit output.

## Verdict

The render executes the storyboard faithfully, and that is the problem. It is a correct, legible,
well-coloured *diagram in perspective*. Every story shot is a slide: display title, explanatory
subtitle, badges on a stage below (152 words in 42 s). The poster's "image" is two 9–13 CSS px
sticks standing on a row of axis labels under a headline. The one paid motion is the muddiest solid
on screen in its own close-up (3.45:1 dark, 2.01:1 light), outranked by a white file icon.

The honesty and consistency slips are few but real:
- the proof caption misdescribes the recorded login-shell download as an opt-in;
- "docs/ is a pure function" overstates the design;
- "online-only" is applied to Graph lanes;
- "six of eight" is shown as four.

**What would still make the operator say "infographic":**
- the title-plus-subtitle card on every shot;
- the four Mono lane chips under the pillars, which turn the poster into a bar chart;
- the `=` / `≠` / `H0 H1 H2` / `410` badges;
- the clip-art document glyph;
- a metronomic 3–4 s edit with a frozen 3.5 s open.

Fix findings 1–3 first (a low monolith poster, one line per shot, a loud amber read) and 4–8 next.
The storyboard verdict's picture, "two amber pillars on a plane that runs to the horizon", is still
the right image. It is not on screen yet.
