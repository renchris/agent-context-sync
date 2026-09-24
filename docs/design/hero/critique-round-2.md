# Round 2 critique (rendered frames, a new critic blind to round 1)

**What I looked at.** README lines 1–35, `docs/design/VISUAL.md`, `DIRECTION.md` up to "## Decisions", `scripts/render-hero.mjs` (for coordinates and beats), and the paused frames in `/tmp/hero-review-r2/`. I did not open any other file in `docs/design/hero/`.

**Units and sources.** Every coordinate is in the SVG's 830 × 440 viewBox, which is the 830-px display scale. Beats come from `BEAT`/`FOCUS` in the script, and I checked each one against the sheets.

- Contrast ratios are **measured**: WCAG relative-luminance formula over the palette hexes, computed in a node one-off.
- Text widths are **measured**: opentype.js on the repo's Geist Sans/Mono 400 at the stated size.
- Phone sizes are **computed**: the 360/830 = 0.434 scale.
- Anything I call "estimated" is geometry or judgement, and I say which.

---

## Verdict in one paragraph

The answer-first structure works. The governing thought and the four steps are fully legible at t = 0, t = 3 and every t between. The type is disciplined and the stage sits on a real grid. The core beat is right: two cells fill slowly with amber while everything else stays grey and still. It is not yet memorable, for three reasons:

1. **Frame choice.** The frame on screen longest, and the one reduced-motion users get, is the least informative: 256 identical grey cells and "0 of 256 read".
2. **Three step claims have no picture.** "Make expiry cheap", "only on purpose" and "a pure function of the source" are shown weakly or not at all.
3. **The outcome is ambiguous.** Of the two read cells, one was a free no-op and one became a commit. The only thing that tells them apart is a 1.5-px ring hue sitting against its own fill at 1.21:1.

Two of the changes below are MUST. Six are SHOULD.

---

## 1. Answer-first: t = 0 and t = 3, desktop and phone

**Desktop (830 px): yes, fully.** In `full-{dark,light}-t000` and `-t003`:

- The headline is Geist Sans 600 at 28 px, full contrast (15.8:1 light), in three phrase-broken lines ending at x = 673 / 576 / 474 (measured).
- The steps are 20 px regular, all at full opacity.
- t = 3 is identical to t = 0: the focus fade starts at 3.0 and only reaches its dim value at 3.3 (`sheet-*-0`, t = 3.00 vs t = 3.50).
- At every later t the governing thought stays at full contrast. The dimmed steps clear 4.5:1: 5.42:1 dark and 4.98:1 light (measured). A viewer who arrives mid-loop still gets the answer.

**Phone (360 px): the governing thought yes, the steps with effort, the stage no.**

- **Text.** The headline renders at 12.1 px and reads cleanly (`phone-*-t000`). The steps are 8.7 px and their numerals 7.8 px. That is readable on a high-DPI phone, but below a comfortable reading size (estimated: about 11 px is where small UI text stops being effortful).
- **Stage.** Everything that explains the stage is 5.2–5.6 px and illegible: the source labels, "410", the captions and "+1 commit". The cells are 3.9 px and the rings 0.65 px, so on a phone the story collapses to "two dots turn orange" (`phone-*-t020`). The meter, at 8.7 px, is the one stage element that survives.

For a README whose readers are mostly on desktop this is acceptable, but it is not "answer at every size".

## 2. The story a first-time viewer takes from one loop

| t (s) | What is on screen | What a first-time viewer takes away | Does the picture carry the lit step? |
|---|---|---|---|
| 0–3.0 | Text, four 8×8 grids, "0 of 256 read", a `docs/` line with 4 commits | Four sources, 256 things, nothing read yet | n/a (rest) |
| 3.5–3.9 | Six text-coloured rings pop in OneDrive, Outlook and Teams. No caption. | "These changed" | **Step 1, ask: yes.** The rings are the answer. |
| 4.0–5.9 | SharePoint's dot turns red and says `410`. Caption: "token expired: walking metadata". A 1.5-px line sweeps the block from 4.5 to 5.5, and two rings appear behind it. | "SharePoint's token died, so it scanned instead" | **"Make expiry cheap": weak.** Expiry and recovery are shown, but not their cost. The only evidence is the meter still reading "0", 150–400 px away, and nothing ties the two together. The red lasts 1.9 s and starts 0.5 s after rings in three other blocks have pulled the eye away. |
| 5.5–7.8 | "8 of 256 reported" | A count | n/a |
| 7.5–10.5 | Six rings flash green for 0.7 s and fade back into grey. Caption: "`H0` equal: 6 unchanged". The meter stays "0". | "Six were dismissed" | **"Before reading a byte": mostly.** It works if the eye visits the meter. The caption never mentions bytes. "Unchanged" straight after "reported" reads as a contradiction to a newcomer, and they never learn what H0 is. |
| 11.0–14.2 | The two remaining ringed cells fill with amber, one after the other, at a constant slow rate. The meter turns amber: 1, then 2 of 256. **The caption slot is empty from 10.8 to 15.5.** | "The two real changes get read, slowly and expensively" | **"Read bytes": yes, and it is the best beat in the loop. "Only on purpose": no.** Nothing shows a default refusal or a deliberate opt-in. The dashed "placeholder" outlines on exactly these two cells at t = 0 are invisible at 830 px (1-px dashes at 1.92:1, see the 4× crop of `full-dark-t000`) and never explained. What the viewer takes away is step 2's consequence ("changed things get read"), not step 3's claim. |
| 15.5–17.0 | SharePoint's amber cell gains a green ring. Caption: "`H2` equal: no-op save, no commit". | "That one was read for nothing, but it stopped there" | Shown under **step 4**, although H2 is one of step 2's three hashes (README line 27). The viewer files it under the wrong step. |
| 17.0–18.4 | A text-coloured 8-px square leaves the OneDrive cell, streaks about 750 px to the git line, and becomes a fifth commit with "+1 commit". | "One change was published to `docs/` in git" | **"In git": yes. "A pure function of the source": no.** Nothing says determinism or provenance. The one piece of evidence for it, same input giving the same page and so no commit, was captioned as a no-op save. |
| 18.5–23.0 | All steps relit. Outcome held. | "2 of 256 read, 1 commit" | Summary. H0's six free exits have left no trace on screen. |
| 23.0–25.0 | Four-beat unwind. At **t = 24.0 the meter still says "2 of 256 read" over an empty field** (`sheet-*-2`). | A rewind | Contradictory for about 0.3 s |

**Takeaway sentence (estimated, from the above):** *"Of 256 things only two were read, and one change reached git."* That is close to DIRECTION's target. What the viewer does not take away is why the expiry was cheap, why the reads were deliberate, or why the output is reproducible.

## 3. Craft

### Typography

- **Hierarchy.** It is clean. The headline is Sans 600 at 28 px (36-px leading, −0.01 em). The steps are Sans 400 at 20 px (30-px leading), with Mono 18 numerals hanging at x = 24 and the text at x = 54. The inline Mono 600 `docs/` at 26 px is spaced correctly: about 8 px of ink gap on both sides at a 4× crop.
- **The phrase break works.** "by processing only what changed." sits alone directly above the field.
- **The meter is under-sized for its role.** "0 of 256 read" is Mono 20 px, the same size as the step text, so the stage has no focal point. It is the stage's headline and should be the second-loudest type in the frame.
- **The captions are the weakest type carrying the most specific information:** 13 px, muted (6.15:1 dark, 6.11:1 light). The source labels at Mono 12 px are fine as labels.

### Spacing, grid, alignment

- **Vertical rhythm is consistent.** Headline-to-steps and steps-to-labels are both 48 px baseline to baseline (122→170 and 260→308).
- **Margins.** Left and right are 24 px, set by the headline, the field and the right edge of "+1 commit" at x = 806. The top is about 30 px to the cap top, the bottom 26.5 px from the field bottom at 413.5.
- **The grid is real.** The blocks sit at x = 24/160/296/432: 93 px wide with 43-px gutters. The right column at x = 568 is the fifth block slot (24 + 4 × 136), so its gutter matches.
- **Right-column alignment.** The meter's cap top (334 − 0.71 × 20 ≈ 320) sits on the field top at y = 320. The git line at y = 408 sits on the bottom-row centre at 408.5. Good.
- **A happy accident.** Step 1 ends at x = 568.4 (measured), exactly where the right column starts. Keep it if the copy is ever edited.
- **Imbalance.** The text block ends at x = 673 and the stage at x = 806, leaving a dead 133 × 300 px area top-right. That is tolerable for a left-reading README hero and needs no fix.
- **Travel path.** The page mark flies in a straight line from (53, 337) to (800, 388). Between 17.0 and 17.8 it crosses rows 1–3 of the SharePoint, Outlook and Teams blocks, so for a few frames it reads as one of *their* cells moving. An arc routed through y ≈ 425 (below the field, above the canvas edge at 440) would keep it off other sources' objects.

### Colour, both themes

- **Dark works.** At `full-dark-t020` the amber chip (8.24:1 on the cell fill) reads as the one warm event in a cold field.
- **Light has a hierarchy inversion on the stage.** The "reported" ring is drawn in text colour, 14.84:1 on the cell fill. The amber chip `#c86a00` is 3.58:1. So from 3.5 to 11 s the free, zero-byte ring is the heaviest mark in the field. At the outcome the committed cell reads as a rust square in a black frame (`full-light-t020`), not as the loud thing. VISUAL.md's rule is that the read is the loudest thing, and in light it isn't.
- **Two ambers in light.** The chip is `#c86a00`, while the meter numerals and the DIRECTION text (§ Type and colour: "`#f0a33a` / `#b35900`") use `#b35900`. `#c86a00` is not in VISUAL.md's table. It may be justified under "Decisions", which I was told not to read, but the rendered frame shows two different oranges 500 px apart.
- **Green on amber.** The H2 no-op cell is a green ring around an amber chip: 1.21:1 dark and 1.33:1 light between ring and fill. The ring's inner edge is 0.75 px from the chip on two sides and overlaps it by 0.25 px on the other two (from the script geometry). So the ring reads as the chip's border and the cell as a single olive-brown blob at thumbnail scale (`sheet-light-2`, t = 20.0). Telling "free at H2" from "committed" depends on hue alone, which fails for red-green colour vision.

### Density of the right-hand column (x 568–806, y ≈ 320–412)

Its space is under-filled and its time is over-filled.

- **At rest (t = 0)** it has the meter at the top, the git line at the bottom, and a 60-px hole between them (y ≈ 340–400, the empty caption slot). It looks unfinished.
- **During the story** one 13-px line is swapped five times, at 4.0, 5.5, 7.5, 15.5 and 17.0. Each version holds 1.5–3 s and sits 370–520 px (estimated) from the 9-px cell it explains. So the eye has to ping-pong across the stage to read the colour.
- **Gaps and loss.** The caption is empty for the whole of step 3. At the outcome it keeps only the last fact ("`H2`: 1 no-op save, 1 page updated"), so the funnel (8 reported, 6 free at H0, 2 read, 1 free at H2, 1 commit) is never on screen at once.

## 4. Restraint: what to cut or quiet

- **Cut the two dashed placeholder cells.** They are invisible at 830 px, unexplained, and they say the wrong thing (see §6).
- **Quiet the reported ring** from text colour to muted. A zero-cost mark should not outrank the only paid one.
- **Collapse the four-beat reset** (23.0, 23.5, 24.0, 24.5). It is a two-second rewind that means nothing and produces the t = 24.0 contradiction. Change 3 below makes it a single "next sync" beat.
- **Replace the churn with one ledger.** Five swapped captions are busier than two lines that accumulate.
- **Everything else earns its place.** That covers the since-token dots, the walk line, the git conveyor, the four history dots, and "+1 commit". Nothing on screen is decorative in the gradient-and-glow sense. The discipline is good.

## 5. The poster frame

**The frame people see is t = 0.**

- It is on screen from 25.0 to 3.0 across the seam, which is 8 s of every 30.
- It is the whole of what reduced-motion users get.
- It is what anyone scrolling past in the first 3 s sees.

It is legible and calm, but mute. The stage says "0 of 256 read" over 256 identical cells, with a hole where the caption goes. Seen still, "0 of 256 read" is a riddle, not a claim.

**The frame people remember is t ≈ 20 (held 18.5–23.0, 4.5 s).** It has two amber cells, "2 of 256 read" in amber, and "+1 commit". The idea is strong, but the picture is not yet:

- The two chips cover about 200 px² of a 365,200 px² canvas, about 0.05% (estimated from geometry). That smallness is the argument, but it means the meter has to carry the frame, and at 20 px it is no bigger than a step line.
- The two chips look alike.
- The six free H0 exits have vanished.
- The caption forgets them.

It should be the rest frame, and it needs the funnel on screen (changes 3, 4 and 8).

## 6. Against the README's content

1. **Placeholders are inverted.** README line 20 says the corpus is "mostly online-only on the laptop", and step 3 says "an online-only file is a placeholder". The hero draws 254 of 256 files as solid (local) and marks *only the two files that will be read* as dashed placeholders. That reads as "placeholders are what gets read", which is the opposite of the refusal-by-default claim. Either draw the field as placeholders or drop the mark. Dropping it is right.
2. **H2 appears under the wrong step.** Step 2 says "three hashes … H2 whether anything downstream changed … H2 is what makes that save free". The hero shows H0 under step 2 but H2 under step 4 (focus moves to step 4 at 15.0, and the cutoff fires at 15.5). The execution order is right: H2 does run after the read. But the label maps it to the wrong line. The fix is not to move it, but to caption it in step 4's terms (same input, same page, nothing to commit). That is exactly the "pure function" consequence.
3. **"`H0` equal: 6 unchanged."** It is mechanically correct: reported by the delta, but the content identity is equal. It needs the cost ("0 bytes") to land as step 2's "before reading a byte".
4. **No mechanism is misdescribed.** These are all correct:
   - SharePoint 410 leads to a metadata walk that reads zero bytes, and the walk finds the two SharePoint changes as it passes (5.0 and 5.5).
   - The no-op `.xlsx` is correctly read *and then* stopped at H2. A no-op save changes bytes, so it cannot exit at H0.
   - The reads are serial (11.0–12.6, then 12.6–14.2), which fits "one budgeted step".
   - The git conveyor is honest.

   H1 never appears. That was an accepted cut, but it leaves "three hashes" illustrated by two.

---

## Changes, in priority order

1. **MUST: give step 3 a carrier and cut the misleading placeholders.**
   - Add a caption at 11.0–15.2: "`H0` differs: 2 budgeted reads" (170 px at 13 px, fits the 238-px column).
   - Delete the dashed-cell branch (`render-hero.mjs` lines 301–305).
   - *Reason:* the slot is empty for the entire 4.7 s that "Read bytes only on purpose" is lit, and the dashes both vanish at 1× and contradict README line 20.
2. **MUST: make "free at H2" and "committed" differ in shape, not only hue.**
   - Fade the committed cell's text ring out at 18.0 (0.3 s, `leave`) when its page lands. Its "reported" job is done and it is in git.
   - Keep the green ring on the no-op cell at 2 px.
   - The outcome then reads: bare amber = paid and published; framed amber = paid, then free.
   - *Reason:* the outcome's key distinction is currently a 1.5-px hue difference at 1.21:1 (dark) / 1.33:1 (light) against the fill, which is lost on red-green colour vision and on phones.
3. **SHOULD: make the outcome the rest frame.**
   - Hold the outcome from 18.5 to 30 and from 0 to 2.0 (13.5 s across the seam).
   - At 2.0–2.5, slide the git belt one spacing (40 px, `travel`). At 2.5–2.9, fade the chips, rings and ledger and return the meter to "0 of 256". That is one "next sync" beat.
   - The story then runs from 3.5 unchanged, and the 23.0–25.0 unwind is deleted.
   - *Reason:* the 8-s still and the reduced-motion frame currently show the least informative state. They should show "2 of 256 read".
4. **SHOULD: turn the outcome caption into a two-line ledger at 14 px.**
   - Line 1 at baseline 358: "8 reported · 6 free at `H0`" (153 px at 14 px).
   - Line 2 at baseline 377: "1 no-op save, free at `H2`" (151 px). Keep "+1 commit" on the git line.
   - *Reason:* the poster frame should hold the whole funnel. Today it forgets the six H0 exits, which are the majority of the argument, and leaves a 60-px hole at rest.
5. **SHOULD: state the cost of expiry, and hold it for the whole step.**
   - Caption at 4.0–7.0: "`410`: metadata walk, 0 bytes read", with 410 in red Mono (203 px at 13 px, 218 at 14). Keep the red dot's 4.0–5.5 lifetime (red means failure, and the walk is the recovery).
   - Fold "8 of 256 reported" into step 2's caption, "`H0` equal on 6 of 8: 0 bytes read" (192 px), at 7.5–10.5.
   - *Reason:* "make expiry cheap" and "before reading a byte" are both claims about bytes, and neither caption says bytes.
6. **SHOULD: caption the H2 cutoff at 15.5–17.0 in step 4's language: "`H2` equal: same page, no commit" (195 px).**
   - *Reason:* it is the only picture of "a pure function of the source" in the loop, and it plays while step 4 is lit. "No-op save" moves to the ledger (change 4).
7. **SHOULD: draw the reported ring in muted, and use one amber in light.**
   - Ring colour: `#8b949e` dark, `#59636e` light. That is 5.62:1 and 5.74:1 on the cell fill, still far above the field's 1.92:1 and 2.01:1.
   - Keep text colour for the page mark only.
   - Use one light amber for both chip and meter: either `#b35900` everywhere, or `#c86a00` everywhere with it written into VISUAL.md as a hero token.
   - *Reason:* in light the zero-cost ring (14.84:1) is currently four times the contrast of the paid chip (3.58:1), which inverts "colour means cost".
8. **SHOULD: set the meter at Mono 24 px, baseline 337, so its cap top stays on y = 320.**
   - "2 of 256 read" becomes 187 px wide (measured), inside the 238-px column.
   - *Reason:* it is the stage's headline and the poster's hook, and at 20 px it is no bigger than a step line.

**Not in the list, noted:**

- **Phone.** A narrow `<picture>` variant (`<source media="(max-width: …) and (prefers-color-scheme: dark)">`) would rescue the stage on phones. I could not confirm that GitHub's sanitizer keeps width media queries on `<source>`, so test that before building anything (unverified).
- **Travel path.** Route the page mark's path below the field (§3).

## Would I ship it as is?

No. It is close, and nothing in it embarrasses the project: the answer is legible at every t, the type and grid are disciplined, and the read beat is right. But two things fail the brief's own standard, and I would fix both first:

- Step 3 has no picture, and its only intended carrier contradicts the README.
- The outcome's one distinction (free at H2 versus committed) depends on a sub-2-px hue difference.

Changes 3 and 4 are what would make the frame people remember also be the frame they see first.
