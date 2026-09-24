# Round 1 critique (rendered frames, fresh-context critic)

**Inputs.** README.md (the hero is now inserted above the H1 in the working tree, so the bold
governing thought sits at line 9, not line 3), VISUAL.md (working-tree version), DIRECTION.md,
storyboard-a.md, storyboard-verdict.md, and every frame in `/tmp/hero-review-r1/`. To get exact
positions and timings I also read `scripts/render-hero.mjs`, the source of both SVGs.

**How the numbers were produced.**

- **Ink positions and gaps** are *measured*. I ran an in-memory PIL row scan over
  `full-dark-t00.png` (2x, with ink defined as a summed RGB distance from `#0d1117` above 60) and
  halved the results to the 830 px display scale.
- **Element coordinates and beat times** are *read* from `render-hero.mjs`.
- **Glyph widths and metrics** are *measured* with opentype.js on the repository's Geist WOFFs,
  in memory.
- **Contrast ratios** are *computed* (WCAG 2.x) from the hex values in `render-hero.mjs`.
- **Phone sizes** are *computed* at 360 / 830 = 0.434.
- **Motion speeds** are *computed* from distance and duration. The visual-angle figures are
  *estimated* for a 13-inch laptop viewed from about 50 cm.

No files were written except this one.

**Verdict.** The answer frame is solid: every word of the message is legible at
t = 0 and t = 3 on desktop, and the headline is legible on a phone. The stage never delivers the
picture the whole concept rests on. The storyboard's title is "the field goes quiet", but the field
gets louder: by t = 9.5 it carries eight lit cells, six of them green, and it keeps them to t = 23.
The one memorable image, two amber cells in a quiet field of 256, never appears on screen.

---

## 1. Answer-first

- **Desktop, t = 0 and t = 3: yes.** `full-dark-t03.png` is pixel-identical to `full-dark-t00.png`
  (*measured*: PIL `ImageChops.difference(...).getbbox()` returns `None`). The headline is Geist
  SemiBold 28/36 and the steps are Regular 20/30, both in full text colour. The focus dim starts at
  3.0 s and has settled by 3.3 s (sheet t=3.50). At that point the dimmed steps still clear 4.5:1:
  5.42:1 dark at 55 % and 4.98:1 light at 65 % (*computed*). The governing thought is static, so it
  is legible at every t, which is what DIRECTION asks.
- **Phone (360 px column), t = 0 and t = 20: the headline, yes; the steps, only just; the stage, no.**
  All sizes below are *computed* at a scale of 0.434.

  | Element | Phone size | Note |
  |---|---|---|
  | Headline | 12.1 px | Reads cleanly in `phone-*-t00.png` |
  | Steps | 8.7 px | Readable in the 1x raster, but below a comfortable ~11 px |
  | Source labels, `docs/`, `+1 page` | 5.2 px | Illegible at any DPR |
  | Meter and caption | 5.6 px | Illegible at any DPR |
  | Cells | 3.9 px on a 5.2 px pitch | |

  In `phone-dark-t20.png` the amber and green survive as specks, but `read 2 of 256`, the one
  number that proves the headline, is a smudge.

## 2. Does each motion read as its meaning?

**The story I can reconstruct from the frames.**

- **Step 1 (focus at t = 3.0).**
  - At t=4.00, six cells carry a text-colour ring: two each in OneDrive, Outlook and Teams.
  - At t=4.50, SharePoint's dot is red with a red Mono `410`, and a 1.5 px muted line stands at the
    block's left edge.
  - At t=5.00 and t=5.50, the line has crossed the block, and two SharePoint cells ring as it passes
    them.
  - At t=6.00, the red has gone and the caption reads `8 of 256 reported`.
  - *What reads:* "the sources flagged eight things; SharePoint needed a scan". The walk is the
    clearest motion in the piece.
  - *What does not read:* **expiry** and **cheap**. `410` is an HTTP status code, and nothing on
    screen says *expired* or *token*. The red is up for 1.9 s. The only sign that the scan cost
    nothing is a 13 px meter, 330 px away, that does not change.
- **Step 2 (focus at t = 7.0).**
  - From t=7.00 to t=7.50 the caption line is blank. The first caption was up for only 1.5 s.
  - From t=8.00 to t=9.50, the rings turn green from left to right, and the caption reads
    `H0 equal: 6 unchanged`. In every frame of both themes it reads **"HO equal"** (see §3).
  - *What reads:* "six passed a check".
  - *What does not read:* **before reading a byte**. Nothing ties the green decisions to the meter,
    which sits at 0 far away. The greens also never leave the flow, so they look like a second
    highlight state, not objects dropping out of the work.
- **Step 3 (focus at t = 10.5).**
  - From t=11.50 to t=12.50, amber rises in OneDrive's row 1, column 2 cell.
  - At t=13.00, the meter turns amber at `1 of 256`.
  - The SharePoint cell (row 4, column 3) follows, and at t=14.50 the meter reads `2 of 256`.
  - *What reads:* this is the best beat in the loop, because the chip and the meter change colour
    together.
  - *What does not read:* **only on purpose**. There is no placeholder, refusal or budget, and the
    two cells look like any other ringed cell until they fill.
  - *The slowness barely registers:* the fill climbs 9 px in 1.6 s, which is 5.6 px/s (*computed*).
    So "the only slow motion" reads as "a cell turned orange".
- **Step 4 (focus at t = 15.0).**
  - At t=16.00 the `.xlsx` has a green outline over its amber, and the caption reads
    `H2 equal: no-op save, no commit`. A white 6 px square sits inside the `.docx` chip.
  - At t=16.50 the square is already at x ≈ 800, past the last git dot, where no line is drawn
    yet.
  - At t=17.00 the fifth dot appears, and at t=17.50 `+1 page` appears.
  - *What reads:* "one was fine; there is a new commit".
  - *What does not read:* **which cell became the commit.** The only link is a 0.5 s flight over
    about 750 px. That is a mean of 1,500 px/s, and about 4,300 px/s at the peak, because the
    `travel` curve's slope at its midpoint is 2.86 (*computed*). In angle that is roughly 34°/s
    mean and 95°/s peak (*estimated*), well past comfortable smooth pursuit, so it registers as a
    flicker.
- **Outcome hold (17.1–23.0) and reset (23.0–25.0).** The reset is clean: one kind of thing per
  beat (t=23.50, 24.00, 24.50, 25.00), and the conveyor keeps the git line self-similar.

**Motion by motion.**

| Motion | Where | Reads as | Verdict |
|---|---|---|---|
| focus | 3.0, 7.0, 10.5, 15.0, 18.0 | "this step now" | Works. It is the only bridge between the text and the stage, and it is quiet enough. |
| ring | 3.5–3.9, and during the walk | "these changed" | Works. It is the brightest mark on stage (16:1), although it means *free*. See §3, colour. |
| expire | 4.0–5.9 | "SharePoint errored" | Half works. The recovery is visible, but the meaning (*expired, by design*) is not. |
| walk | 4.5–5.5 | "scan" | Works. It is 1.0 s, shorter than a read, which is the correct tempo relation. |
| exit | 7.5–9.3 | "passed" | Fails its definition. DIRECTION says "then the object drops out of the flow", but here the green stays for up to 15.9 s. |
| read | 11.0–14.2 | "turned amber" | The colour works. The tempo is lost at 9 px, and the placeholder half of the definition is missing. |
| travel | 16.0–16.5 | a flicker | Fails. The start and end frames are the only evidence it happened. |
| write | 16.5–17.1 | "a commit" | Works, but it arrives after the page mark has landed on empty space. |

## 3. Craft

### Typography

- **The headline breaks are right. Keep them.**
  - Measured line widths are 656 / 552 / 450 px, a clean descending rag.
  - Line 2 is exactly the four sources, and the four block labels repeat it.
  - The alternative, `…docs/ folder` / `in sync with OneDrive…Teams` / `by processing…`, measures
    493 / 715 / 450: a bulge in the middle, with a longer line 2.
  - The 36 px leading gives even ink gaps of 11 / 11.5 px between the lines (*measured*).
- **`H0` reads as "HO".**
  - The caption is set in Geist Sans. Its zero is an unslashed oval 564 units wide, against 650 for
    the capital O (*measured*, opentype), so at 13 px the two differ by about 1 px of width.
  - DIRECTION names `H0` as a Mono identifier. Geist Mono has a slashed zero.
- **`docs/` in the headline has a hole after it.**
  - In Sans SemiBold, the slash's foot leaves a 16.5 px empty run at the baseline before `folder`.
    Word gaps elsewhere on that line measure 7–9 px.
  - DIRECTION lists `docs/` as an identifier to set in Mono, and README sets it as code.
  - Geist Mono 600 at 26 px is 78.0 px wide against 79.4 px now (*measured*), so it is a drop-in
    replacement.
- **Hierarchy.**
  - Headline 28 SemiBold, steps 20 Regular, stage text 12–13. That is three clear tiers.
  - The stage's most important string, the meter, sits in the lowest tier.
- **Numerals.** The Mono 18 numerals beside Sans 20 text sit slightly light (cap height 12.8 vs
  14.2 px, *computed*). That is acceptable as a subordinate column.

### Alignment and spacing (measured, 830 px scale)

| Band | Ink y | Gap to the next band |
|---|---|---|
| Headline (3 lines) | 29.0 – 126.5 | **29.0** |
| Steps (4 lines) | 155.5 – 263.5 | **23.5** |
| Source labels | 287.0 – 296.5 | 11.5 |
| Field | 308.0 – 402.0 | 38.0 to the canvas edge |

- **The spacing hierarchy is inverted.** The picture sits closer to the list (23.5 px) than the
  list sits to the headline (29 px). So the stage reads as a fifth row of the list, not as its own
  zone. The bottom margin (38 px) is also larger than the top margin (29 px), which makes the whole
  frame feel top-heavy.
- **The right column is on no grid.**
  - The meter's cap top is at about y = 313, between cell rows 1 (308–317) and 2 (320–329).
  - The git line is at y = 388, which is neither row 7's centre (384.5) nor row 8's (396.5).
  - The column's x = 572 is 47 px right of the Teams block, while the blocks are 43 px apart. That
    is a near-miss. Placing it at x = 568 (= 24 + 4 × 136) would make it the fifth column of the
    block rhythm.
- **The ring outline eats the gutter.** It is a 12 × 12 rect with a 1.5 px stroke around a 9 px
  cell, so its outer edge comes within 0.75 px of each neighbour (*computed* from the source). On
  a phone it turns into a 5.9 px blob. Tolerable on desktop.
- **The right column is empty at rest.** The rightmost ink is the fourth git dot at x = 765
  (*measured*), leaving a 65 px right margin against the 24 px left one. Only between 17.1 and
  24.5 s does ink reach 806. That is acceptable for a left-aligned layout, and it is not a defect.

### Colour

**Dark theme.** The palette is faithful. The field (`#3d444d`, 1.92:1) is correctly quiet. Amber
(`#f0a33a`, 9.02:1) is the most saturated mark. But the *free* ring is `#e6edf3` (16:1), the most
luminous mark on stage, so for 3.5–7.5 s the loudest thing on screen is the thing that cost
nothing. DIRECTION prescribes that. I would leave it, provided the exits clear (change 1).

**Light theme.**

- **Ring to exit is a weak change.** It goes from `#1f2328` to `#1a7f37`, only 3.11:1 apart
  (*computed*), so at thumbnail scale in `sheet-light-0` (t=8.0–9.5) the exits are hard to tell
  from rings. Making exits *leave* (change 1) moves the meaning into motion and fixes this for free.
- **The amber chip reads brown.** A `#b35900` fill in a 9 px chip reads rust-brown, and with the
  near-black ring around the `.docx` it becomes a dark blob (`full-light-t20`, at 48, 320). This is
  optional: fill the chip only with `#c86a00`, which clears the 3:1 non-text minimum at 3.81:1 on
  white and 3.58:1 on `#f6f8fa` (*computed*). Keep `#b35900` for the meter's text (4.83:1).

## 4. Restraint

- **Quiet the six H0 greens.** They are the largest block of colour on stage from 9.3 to 23.4 s
  (`full-dark-t20`: seven green outlines against two amber chips). Colour-means-cost says amber is
  the only loud colour, and here green outvotes it 7 to 2.
- **Close the caption gaps.** The blank at 7.0–7.5 s after a 1.5 s caption is a flash with no
  meaning.
- **Keep the token dots, the `docs/` label and the focus dim.** Each one earns its place.
- **Do not add a tick glyph, a trail, a glow or labels on cells.** None survives 9 px, and none
  means a cost.
- **Outside the SVG, the README now says everything twice on the first screen.**
  - The order is hero (headline and four steps), then `# agent-context-sync`, then the same bold
    sentence again, then the same four steps again in the numbered list below the note.
  - Put the H1 above the hero, so the page opens on the project's name.
  - Drop the bold repeat of the headline, and keep its two follow-on sentences, which say what the
    hero does not. The `alt` text already carries the sentence for screen readers.

## 5. The single biggest weakness

**The field never goes quiet, so the picture never makes the argument.** The concept, the verdict's
reason for choosing A, and VISUAL.md's own summary ("a field of 256 source objects of which two are
ever read") all rest on one image: 254 grey cells and two amber ones. In the render, colour
accumulates instead:

| t | Marked cells |
|---|---|
| 4.0 | 6 |
| 5.5 | 8 |
| 9.5 | 8 (6 of them green) |
| 20.0 | 8 (7 green outlines, 1 white ring, 2 amber chips) |

The frame a returning visitor is most likely to land on is the 17–23 s hold, and it says "lots of
cells lit up and most went green". The thesis number sits beside it in 13 px muted Mono.

**What makes it memorable:**

- Let every free exit flash green and then fall back into the grey field.
- Leave the two reads as the only colour, and make the meter's count the stage's own headline.
- Slow the one long move, so the viewer sees the read cell become the commit.

The poster frame then becomes: **256 grey cells, two amber cells, "2 of 256 read" in amber, and one
bright new commit.** A developer could describe that the next day.

---

## Prioritised changes

Coordinates are in 830 px display units, and times are within the 30 s loop.

**1. MUST: make free exits drop out, so the field goes quiet.**

- For each H0 exit at t_i ∈ {7.5, 7.8, 8.1, 8.4, 8.7, 9.0}:
  - Keep the green outline's rise from t_i to t_i + 0.3 (`settle`).
  - Hold it to t_i + 1.0.
  - Fade it from 1 to 0 over t_i + 1.0 → t_i + 1.5 (`leave`).
- The last one is neutral by 10.5 s, which is exactly focus 3. So the reads start in a field of 254
  grey cells plus the two white-ringed candidates.
- The `.xlsx` H2 green (15.5 s) and the `.docx` ring stay until the 23.0 reset.
- At most 3 exit animations overlap (8.5–9.0 s), which keeps the board's own limit.

*Reason.* DIRECTION defines exit as "a green tick appears, **then the object drops out of the
flow**". The build keeps six greens for up to 15.9 s, so at t=20 green outvotes amber 7 to 2, and
the concept's quiet field never appears.

**2. MUST: set `H0` and `H2` in the captions in Geist Mono 400, 13 px.**

- Keep the rest of each caption in Sans 13, muted, at the same baseline.
- `H0` in Mono is 15.6 px wide against 17.7 px in Sans (*measured*), so nothing reflows.

*Reason.* In both themes, frames t=8.0–10.5 read "HO equal: 6 unchanged". Geist Sans' zero is an
unslashed oval about 1 px narrower than O at this size. DIRECTION makes H0 a Mono identifier.

**3. MUST: give the stage its own zone, and put the right column on the field's grid.**

Move the stage down 12 px:

| Element | Now | Change to |
|---|---|---|
| Source labels and `410` baseline | 296 | 308 |
| Token dots cy | 292 | 304 |
| Field top | 308 | 320 (rows at 320 + 12r; the last row is 404–413) |
| Right column x | 572 | 568 |
| Meter baseline | 322 | 334 (Mono 20 cap top on the field top at 320; 329 if the meter stays at 13 px) |
| Caption baseline | 346 | 358 |
| Git line y | 388 | 408 (the last row's centre) |
| `docs/` baseline | 392 | 412 |
| `+1 page` baseline | 376 | 396 |
| Git clip rect y | 376–400 | 396–420 |

The walk line and the page-mark target follow. The canvas stays 830 × 440.

*Reason, measured.* The list-to-stage gap is 23.5 px, smaller than the headline-to-list gap of
29 px, so the picture reads as part of the list. The change gives 35.5 px, a bottom margin of 26 px
(against 29 px at the top), and a meter and git line that line up with cell rows instead of
floating between them.

**4. MUST: show the placeholder that step 3 is about.**

- At rest, give the two cells that will be read a dashed neutral outline (`stroke-dasharray="2 1.5"`,
  same colour as the other cells). They are OneDrive row 1, column 2 at (48, 320) and SharePoint
  row 4, column 3 at (196, 356), in current coordinates, plus 12 in y after change 3.
- The existing amber rise then fills *through* the dashes. By 12.6 and 14.2 s the placeholder has
  visibly become a solid chip.
- This needs no extra element.

*Reason.* DIRECTION's read is "amber rises through a **dashed placeholder**, and the outline turns
solid". README step 3 says "An online-only file is a placeholder". Verdict change 5 asked for
exactly this, and it was not applied: the SVG's only `stroke-dasharray` is the git segment, and
all 256 cells reuse one solid `#cell` (checked with grep). Without it, step 3 says "read bytes",
never "only on purpose".

**5. SHOULD: make the page's trip to git visible, and make it land on the line.**

- Travel from 16.0 to 17.0 s (1.0 s, `travel`), with the page mark enlarged from 6 × 6 to 8 × 8
  (rx 1.5, text colour).
- Draw the git segment from x = 760 to 800 over 16.4–17.0 (`settle`), so the line arrives with the
  mark.
- Crossfade the mark into the new dot over 17.0–17.15, and bring in `+1 page` over 17.1–17.4.
- Shift the hold start by +0.5 s. The reset times are unchanged.

*Reason.* About 750 px in 0.5 s is a mean of 1,500 px/s and a peak of about 4,300 px/s
(*computed*): a flicker. At t=16.50 the mark already sits on empty space, past the last dot, before
the line exists. This move is the only link between the `.docx` and the commit. DIRECTION's 500 ms
suits short hops, and this is the longest move in the scene. At 1.0 s the peak roughly halves.

**6. SHOULD: make the meter the stage's headline.**

- Change it from `read 0 of 256` to `0 of 256 read`.
- Set it in Geist Mono 20 at x = 568, baseline 334 (after change 3). It measures 156 px, so it
  spans x = 568–724.
- Put the count in text colour, turning amber-fg at the first read (`#f0a33a` dark / `#b35900`
  light), with `read` in muted.

*Reason.* This is the number that proves "processing only what changed", and today it is 13 px
muted Mono: 5.6 px on a phone and illegible in `phone-*-t20.png`. At 20 px it is 8.7 px on a phone,
the same as the steps. Putting the number first also turns an imperative ("read 0…") into a
statement.

**7. SHOULD: say "expired", and remove the caption blanks.**

Run four caption states that crossfade (0.3 s, `travel`) with no empty beats:

| Time | Caption | Width |
|---|---|---|
| 4.0–5.5 | `token expired: walking metadata`, with `expired` in red-fg (`#ff7b72` / `#cf222e`) and the rest muted | 197.8 px, *measured* |
| 5.5–7.5 | `8 of 256 reported` | |
| 7.5–10.5 | `H0 equal: 6 unchanged` | |
| 15.5–24.0 | `H2 equal: no-op save, no commit` | |

Keep `410` beside the dot.

*Reason.* `410` is the only sign of expiry, so "make expiry cheap", half of step 1, is not on screen
for a first-time viewer. `8 of 256 reported`, the one count that literally says "only what
changed", is up for just 1.5 s and followed by a 0.5 s blank. This adds a fourth caption state
beyond the verdict's cap of three. The cap was there to stop swapping noise; crossfades on beats
keep it calm.

**8. SHOULD: set `docs/` in the headline in Geist Mono 600, 26 px, on the Sans baseline.**

- It measures 78.0 px against 79.4 px now, so the line does not reflow.

*Reason.* The Sans slash leaves a 16.5 px baseline hole before `folder`, against 7–9 px word gaps on
the same line (*measured* in `full-dark-t00.png`). DIRECTION makes `docs/` a Mono identifier, and
the README sets it as code. It is the only typographic flaw visible in the answer frame at every t.

## Doc drift to reconcile (not SVG changes)

The build deviates from DIRECTION.md in four places. In every case the build is the better choice,
so DIRECTION should be updated to match it.

| Topic | DIRECTION says | The build does |
|---|---|---|
| Walk duration | ≥ 1.5 s | 1.0 s. Correct, because the walk must be shorter than a 1.6 s read. |
| Expire hold | 1 s | Holds until the walk ends. |
| Focus dim | 55 % | 55 % dark, 65 % light. At 55 % light is 3.65:1, which fails the 4.5:1 rule. |
| T | Not stated | 30 s |
