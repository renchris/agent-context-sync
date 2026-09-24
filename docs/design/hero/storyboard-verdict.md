# Storyboard verdict (fresh-context critic)

**Inputs.** README.md lines 1–30, VISUAL.md, DIRECTION.md, and storyboard-a/b/c.md.

**Deviation from the brief.** To check the mechanism claims I also read README §2 (the hash table and
classifier diagram), the since-token diagram, and design doc §4.1 item 2 (line 113). I cite them
under "Errors".

**How the numbers were produced.**

- Contrast ratios are **measured**: WCAG 2.x relative luminance, computed with a Python script over
  the VISUAL.md hex values against `#0d1117` and `#ffffff`.
- Phone sizes are **computed** at a 340 px column (scale 0.41).
- Loop percentages are **computed** from each board's own beat table.
- Text widths are **estimated** at about 0.52–0.56 em average advance.

## Scores

### A: the field goes quiet (T = 40 s)

| # | Criterion | Score | Evidence |
|---|---|---|---|
| 1 | Answer-first legibility | 3 | The headline and all four steps are at full contrast at t = 0, and by t = 3 s only the focus dim has changed. But the key line is 14 px (5.7 px on a phone), squeezed beside a 256-cell texture, and its four steps share rows with the four source bands, so the eye pairs OneDrive with "Ask" and Outlook with "Read bytes". |
| 2 | One idea | 4 | One field and one set of objects carried through all four steps, with amber on 2 of 256 cells, is "tempo and colour mean cost" as a picture. The git conveyor and three changing captions are side threads. |
| 3 | Meaning-per-motion | 3 | Four slips. The token dots "brighten" to mean *asked*, which gives the ring motion a second meaning. The no-op cell's amber *drains*, which is not in the vocabulary and says the bytes were un-read. The object that travels to git is an *amber* copy, so amber moves outside a read. The 2.0 s walk is slower than a 1.6 s read, so free work gets the slowest motion. |
| 4 | Restraint | 3 | Amber is rare in space but not in time: it is on screen from 16.0 s to 33.0 s plus a 2.5 s fade (43–49 % of the loop). The caption and the meter both say "0 B read". The 33.0–35.5 s reset fades rings, ticks, amber, meter and caption *and* slides the git line, which breaks the board's own "at most 3 simultaneous" check. |
| 5 | Loop seam | 4 | 4.5 s of rest plus a self-similar conveyor gives a still-to-still join. But the slide pushes the oldest commit dot left into the `docs/` label, and the dot swap is invisible only if dot 1 and dot 5 exchange opacity exactly at 100 % → 0 %. |
| 6 | Both themes | 2 | The field's neutral stroke is 1.43:1 on white and 1.92:1 on dark (measured), so in light mode the field nearly disappears, and on a phone its 7 px dashed cells become about 2.9 px of grey fuzz. If the "rising amber" uses the amber *fill* token it is 1.11:1 on white and 1.21:1 on dark, which is invisible. |
| 7 | Memorability | 4 | "A quiet grid where two cells went amber" is the only image of the three that a developer could describe the next day, and it is the "only what changed" argument itself. It loses a point because the amber starts at 16 s, after most 3–15 s glances have ended. |

Sum 23/35.

### B: the ledger (T = 36 s)

| # | Criterion | Score | Evidence |
|---|---|---|---|
| 1 | Answer-first legibility | 2 | t = 0 is a complete table, but at 3.0–4.0 s the board clears its own hash and outcome cells, so from 3 s to 10 s the frame says less than the answer frame did (the board admits this). The 12.5 px Mono is 5.1 px on a phone. |
| 2 | One idea | 3 | "The manifest fills in" is one coherent device, but it is a table-filling idea, not the tempo-means-cost idea: column fills and outcome text carry the meaning, and every row moves. |
| 3 | Meaning-per-motion | 2 | Five problems. The 3.0 s clear-out has no vocabulary meaning. Exit is drawn as "dim to 55 %", which is the *focus* motion. The meter underline uses *write* (a git commit) "to draw the eye". The read is a bar, not a placeholder filling. The no-op bar drains. |
| 4 | Restraint | 2 | About 60 extra Mono glyphs, four hash and outcome columns, a meter and a git label compete with the headline, and every row changes, so nothing on the stage stays still. |
| 5 | Loop seam | 4 | Frame 0 *is* the finished ledger, so the seam is exact by construction. But the 30–32 s reset undims rows, drains bars and crossfades the meter in the same 2 s. |
| 6 | Both themes | 2 | The cost colours ride on 12.5 px text and thin bars, about 5 px on a phone, and the key-line column is row-aligned with the ledger, so step 1 reads as "roadmap.docx". |
| 7 | Memorability | 1 | It reads as a spreadsheet. All four of its rows change (three rung by delta, one by the walk), so it shows 4 of 4 objects processed and cannot make anyone feel "only what changed". |

Sum 16/35.

### C: four sentences (T = 34 s)

| # | Criterion | Score | Evidence |
|---|---|---|---|
| 1 | Answer-first legibility | 5 | Pure type at 30/38 and 16/24 with nothing competing, and at t = 3 s only the focus dim has changed. Its phone sizes are the best of the three (headline 12.3 px), though the list at 6.6 px is still too small to read comfortably. |
| 2 | One idea | 2 | Four unconnected vignettes. The dot that passes H2 is not the placeholder that gets read, and nothing carries from one glyph to the next, so it is four motion ideas in four 44 × 28 boxes. |
| 3 | Meaning-per-motion | 4 | Almost every motion is one vocabulary term used once. Ring never appears, the walk comes "out of" a pill instead of crossing a source, and glyph 2 makes content-hash decisions before any read (see Errors). |
| 4 | Restraint | 4 | One motion at a time and one amber glyph. The 8 px `H0 H1 H2` labels (3.3 px on a phone) should be cut. |
| 5 | Loop seam | 5 | 6 s of rest, a self-similar conveyor in glyph 4, and every glyph back at rest by 28.0 s. |
| 6 | Both themes | 4 | Type-first survives both backgrounds, but all colour sits in 44 × 28 glyphs that shrink to 18 × 11 px on a phone, where the red, green and amber events become specks. |
| 7 | Memorability | 2 | By its own admission it is "a list with animated bullets". It has no scale, so "processing only what changed" is stated but never felt, and a glancer may not see a glyph move at all. |

Sum 26/35.

## Ranked verdict

1. **A**
2. **C**
3. **B**

**Deciding reason.** A is the only board whose picture *is* the argument (two amber cells in a still
field of 256). Every defect it has is a parameter (type size, colour token, timing, caption count)
that can be fixed without touching the concept. C's defect, four vignettes with no scale, is
structural, and B erases its own answer at t = 3 s.

**Why not rank by the sum.** C out-scores A on the sum (26 vs 23). The sum is not the ranking.
Criteria 1 and 5 are table stakes that any static typographic header passes, and A reaches them with
changes 1 and 2 below. The job of a *moving* hero is criteria 2 and 7. C cannot reach those without
growing a stage, and C with a stage is A.

## Changes to A before building

1. **Stack the layout answer-first, and stop pairing sources with steps.** Canvas 830 × 440.
   - **Headline.** Geist SemiBold 28/36 at x = 24, baselines y = 50 / 86 / 122. Break it on C's
     phrase boundaries: `Keep an agent-readable docs/ folder in sync with` /
     `OneDrive, SharePoint, Outlook and Teams` / `by processing only what changed.` (stolen from C),
     so the last phrase sits on its own line, directly above the picture of it. The longest line is
     an estimated 740–760 px: measure it after outlining, and drop to 27 px if it passes 782 px.
   - **Key line.** Four single lines in Geist Regular 20 px, baselines y = 170 / 200 / 230 / 260.
     Mono numerals at x = 24 and text at x = 54. The longest step is an estimated 530 px.
   - **Field.** Four source blocks side by side, not stacked bands. Each block is 8 × 8 cells, 9 × 9 px
     on a 12 px pitch (96 × 96 px), at x = 24 / 160 / 296 / 432 and y = 308–404. Above each block goes a
     Mono 12 muted source label with its token dot.
   - **Meter and git line.** The meter goes at x = 572, y = 330, and the git line at x = 572–806,
     y = 380.
   - **Phone sizes** (computed at scale 0.41): headline 11.5 px, key line 8.2 px, cells 3.7 px. No step
     shares a row with a source any more.
2. **Retime to T = 25 s.** The read then lands inside a 15 s glance, and every 0.5 s beat is exactly
   2 % of the loop.

   | t (s) | Key line | Stage |
   |---|---|---|
   | 0 – 3 | all bright | rest (answer frame) |
   | 3.0 | focus 1 | |
   | 3.5 | | ring ×6 (2 OneDrive, 2 Outlook, 2 Teams), 200 ms |
   | 4.0 | | expire: SharePoint dot red, `410` |
   | 4.5 – 6.0 | | walk across the SharePoint block, 1.5 s linear; 2 cells ring as it crosses them |
   | 6.0 | | dot recovers (the hold runs until the walk ends, not 1 s); caption `8 of 256 reported` |
   | 6.5 – 8.3 | focus 2 | exit ×6 at H0, starting 6.5 / 6.8 / 7.1 / 7.4 / 7.7 / 8.0; caption `H0 equal: 6 unchanged` |
   | 8.5 – 10.1 | focus 3 | read: OneDrive `.docx` cell; meter `read 1 / 256` turns amber at 10.1 |
   | 10.1 – 11.7 | | read: SharePoint `.xlsx` cell; meter `2 / 256` |
   | 12.0 – 12.3 | focus 4 | exit: the `.xlsx` cell turns green; caption `H2 equal: no-op save, no commit` |
   | 12.5 – 13.0 | | travel: a page mark from the `.docx` cell to the git line |
   | 13.0 – 13.6 | | write: fifth commit dot and `+1 page` |
   | 13.6 – 18.0 | | hold |
   | 18.0 / 18.5 / 19.0 / 19.5 | | reset in four 0.5 s beats, at most two things each: greens and rings; amber and outlines; meter and caption; conveyor slide |
   | 20.0 | all bright | |
   | 20.5 – 25.0 | | rest. The still frame across the seam runs 20.5 → 25 → 3.0, 7.5 s in all |

   The walk (1.5 s) is now shorter than each read (1.6 s), and the two serial reads make 3.2 s, the
   longest continuous event. The commit is on screen by 13.6 s.
3. **Fix the mechanism and the amber.**
   - **Where the no-op save exits.** It exits at **H2**, not H1, under focus 4, where H2 decides
     whether anything is published. Use an `.xlsx`, because the README's measured table says Excel
     re-mints a GUID on every save.
   - **Amber stays.** Both read cells keep their amber until the reset. Bytes, once paid for, do not
     drain.
   - **What travels to git.** A 6 × 6 page mark in the *text* colour, not an amber copy, because what
     is committed is converted markdown, not source bytes.
   - **Account for every ringed cell.** As drawn, A rings 8, exits 5, reads 2, and leaves a third
     ringed cell with no outcome through the whole hold. Exit 6 at H0.
   - **Caption wording.** Use `reported`, not `changed`.
4. **Clean up the vocabulary and the counters.**
   - **Ask.** Delete the token-dot pulse. The rings are the answer to the ask.
   - **Exit in the field.** The ring outline is replaced by a *green outline* (300 ms, `leave`). Use no
     tick glyph, which is illegible at 9 px, and no translate, because the cell stays in the corpus.
   - **Meter.** One meter in one unit, `read 0 / 256` → `1 / 256` → `2 / 256`, turning amber at the
     first read. Captions carry no byte figures.
   - **Captions.** At most three states, swapped on beats.
5. **Tune colour per theme (measured).**
   - **Field outline.** `#3d444d` on dark (1.92:1), and `#afb8c1` on light (2.01:1) instead of
     `#d1d9e0` (1.43:1). Update VISUAL.md for this hero-only neutral.
   - **Solid and dashed cells.** All cells are solid except the two that will be read, which get a
     2/1.5 dash. Outlook and Teams items are not placeholders anyway.
   - **Rising amber.** Draw it in the *stroke* tone, `#f0a33a` (9.02:1) or `#b35900` (4.83:1), never
     the fill tokens (1.21:1, 1.11:1).
   - **Focus dim.** 55 % on dark (5.42:1) but **65 % on light** (4.98:1). At 55 % the light theme
     measures 3.65:1.
   - **Key-line numerals.** Use the text colour, not muted, because muted at 55 % is 2.67:1 on dark
     and 2.37:1 on light.
   - **Delivery.** Ship the dark and light pair behind `<picture>`, like the diagrams.
6. **Make the conveyor seam mechanical.**
   - **Clip the git dots.** Wrap them in a *static* `clipPath` whose left edge is 12 px right of the
     `docs/` label, so the oldest dot slides under the clip, not into the text.
   - **Clear the label.** The `+1 page` label must reach opacity 0 by 19.0 s.
   - **Static attributes.** Every element's static attributes equal its 0 % keyframe.
   - **Check before shipping.** Render t = 0 and t = 24.9 s in a headless browser (negative
     `animation-delay`) and pixel-diff them.

**Stolen from the losers.** From C: the phrase-broken headline, the larger type, and one-motion-per-beat
discipline, applied to the reset. B has nothing A lacks: its best element, the meter held at zero
through two whole steps, is already in A.

## Factual and logical errors against the README

### A

- **No-op save at the wrong hash.** The caption reads `H1 equal → no-op save`. README line 22 says
  "H2 is what makes that save free". Design §4.1 says "H2 — not H1 — is what makes a no-op save free",
  because Excel and Word re-mint ids inside content parts on every save.
- **Amber drains on the no-op save.** The bytes were already read and paid for. A no-op save is free
  *downstream* (no conversion, no commit), not after the fact.
- **A ringed cell with no outcome.** 8 are reported, 5 exit and 2 are read, which leaves one ringed
  cell with no outcome through the whole outcome hold.
- **Captions contradict each other.** `8 of 256 changed` is followed by `5 unchanged`.
- **The meter changes unit.** It is labelled "bytes read" and shows `0 B`, then switches to an object
  count, `2 of 256 read`.
- **Every cell is a placeholder.** All cells are dashed online-only placeholders, including the
  Outlook and Teams bands. Mail and chat are not File Provider placeholders.
- **Sources pair with steps.** The source bands are row-aligned with the numbered steps, so
  OneDrive = step 1 and Outlook = step 3 by position.

### B

- **Frame 0 contradicts itself.** The answer frame is the finished ledger, with two rows that needed
  reads, `docs/ +1` and `git +1 page`, sitting beside a meter reading `0 B`. It claims zero bytes
  read while showing reads, and the 30–32 s reset crossfades the meter back to `0 B` to force it.
- **The one wrong file type.** `pricing.xlsx  ≠ =  no-op save` exits an Excel no-op save at H1. The
  README's measured table shows Excel re-mints `documentId` in `xl/workbook.xml` on every save, and
  line 22 says H2 is what makes the save free. B picked the one file type the README uses to show why
  H2 is needed.
- **No unchanged majority.** All four rows change, so the board processes 4 of 4 objects and cannot
  illustrate "only what changed".
- **Mail shown as a byte read.** `Re: renewal` (Outlook mail) is shown as a byte read, but step 3 is
  about online-only file placeholders.

### C

- **Hashes before the read.** Glyph 2 exits a dot at H1 and passes one through H2 during step 2,
  before glyph 3 has read anything. H1 is a canonical content hash and H2 is the hash of the
  converter's output, and both need the bytes, which the classifier reads *between* H0 and H1. C
  animates the misreading that the step-2 title invites: "before reading a byte" holds for H0 only.
- **No H2 exit.** No object exits at H2, so the early cutoff that README line 22 names never appears.
- **Step 1 shows only failure.** Glyph 1 shows only expiry, never an ordinary ask that returns
  changes. The loop's first colour event is a red failure, and the main half of step 1 has no motion.

### Shared: decide these in the docs before the build

- **The README contradicts itself.** The classifier diagram labels `H1 equal` "TOUCHED, NOT CHANGED,
  no-op save: stop", while line 22 and design §4.1 say H2 makes the no-op save free. A and B copied the
  diagram. Fix the diagram label, or the hero will contradict one of them.
- **DIRECTION contradicts itself on timing.** It calls the read "the only slow one" but sets walk at
  ≥ 1.5 s. It gives expire a 1 s hold, although the token logically stays expired until the walk's
  last page (`last page: store token` in the since-token diagram). The expire row should say "hold
  until the walk ends".
- **DIRECTION's focus dim fails its own rule.** The 55 % dim fails its own 4.5:1 requirement in the
  light theme (3.65:1, measured).
- **VISUAL.md still says no hero.** Its "What was cut" says "No hero banner … The README's one
  memorable element is the recording." The hero reverses that decision. Update VISUAL.md, or the hero
  and the recording will compete for the one memorable slot.
