# Launch-film storyboards A / B / C: verdict (fresh-context critic)

**Inputs.** README.md; docs/design/agent-context-sync.md (§4.1 line 113, §4.3 lines 241–266, the 410
state machine at lines 150–157); docs/design/VISUAL.md; docs/design/hero/DIRECTION.md (motion
vocabulary); critique-round-1.md, critique-round-2.md and storyboard-verdict.md (skimmed);
film-storyboard-a/b/c.md; the look-frames look-a/b/c.png and their 1920 × 1080 sources; the look
generators film/look/a.js, b.js and c.js; /tmp/hero-launch/research/github-medium.md ("Implications")
and readme-heroes.md ("Rules"); the rubric /tmp/hero-launch/critic-rubric.md.

**How the numbers were produced.**
- **Measured**:
  - Ink boxes and pillar sizes: a PIL/numpy scan of look-a-dark/t0000.000.png, scaled to the README
    column by 838 / 1920 = 0.4365.
  - Contrast: the WCAG 2.x formula over the THEMES hexes in film/lib.js.
  - WebP bytes: `stat` and `webpinfo` on /tmp/hero-launch/medium-*.webp. All are 61 frames at 33 ms,
    so about 2.0 s each.
  - "12,160" appears nowhere in README.md, the design doc, VISUAL.md or DIRECTION.md (`grep`, no
    match).
- **Read from code**: font sizes, grid dimensions and thumbnail geometry in film/look/*.js.
- **Estimated**, with the method named each time: anything a look-frame does not show, all byte
  budgets, and phone sizes. Phone sizes use a 360 px column, a scale of 0.1875.

Two of the three look-frames do not show frame 0. look-b is card 2, and look-c is a mid-wipe frame
with a different headline. So the posters for B and C are judged from the storyboard text, and their
sizes are estimates.

---

## Scores (0–5, weight applied)

| # | Criterion | wt | A: the plane | B: the sentence | C: Tuesday and today |
|---|---|---|---|---|---|
| 1 | Answer at t = 0 | ×2 | 4 → **8** | 5 → **10** | 3 → **6** |
| 2 | Answer at t = 3 s | ×2 | 4 → **8** | 2 → **4** | 3 → **6** |
| 3 | One idea | ×1 | 3 → **3** | 2 → **2** | 2 → **2** |
| 4 | Meaning per motion | ×1 | 3 → **3** | 2 → **2** | 2 → **2** |
| 5 | Cinematic quality | ×2 | 3 → **6** | 2 → **4** | 2 → **4** |
| 6 | Restraint | ×1 | 2 → **2** | 2 → **2** | 1 → **1** |
| 7 | Both themes | ×1 | 3 → **3** | 5 → **5** | 4 → **4** |
| 8 | Memorability | ×2 | 4 → **8** | 1 → **2** | 3 → **6** |
| 9 | Feasibility in the medium | ×1 | 3 → **3** | 5 → **5** | 4 → **4** |
| | **Total (of 65)** | | **44** | **36** | **35** |

## Ranking

1. **A, the plane: 44 / 65.** It is the only board with a picture that is the argument: the only
   thing with height is what was read. It also has the only real arc (hook → tension → snap → payoff
   → lockup). It carries the most honesty debt: an invented denominator and a counterfactual that
   out-shouts the real read. Both are fixable without touching the core image.
2. **B, the sentence: 36 / 65.** It has the best frame 0 and the cheapest bytes. It has no image, and
   it drops the brand and the subject for most of its loop. Its errors are in the copy and are
   fixable. What it cannot fix is that it is a slide deck.
3. **C, Tuesday and today: 35 / 65.** Its central image contradicts the design. It shows content
   visibly changing, then waves six of those changes through at H0 as "same". The error is in the
   premise, not the polish, and that is why C ranks below B on a near-tie.

---

## A: the plane

### Mechanism and honesty errors

1. **"12,160" is a layout artefact presented as a result.**
   - It equals `COLS 40 × ROWS 76 × 4 regions` in film/look/a.js. The lead's pre-draft said
     "12,288": the number moved when the grid moved. It is in no design document.
   - It appears as an amber Mono meter on frame 0 (0.0–3.0 and 34.0–40.0; LOOP 0.0–3.0 and
     10.0–13.0), which is also the reduced-motion frame and the MP4 player's still. The meter ticks
     "to 12,160 read" at 7.0–11.0.
   - It is captioned "re-reading everything: 12,160 reads" / "this design: 2 reads" (LOOP 3.4–6.4).
   - A precise, non-round number in a counter's typeface reads as a benchmark from a tool that
     README line 14 says does not exist.
   - "this design: 2 reads" is a performance claim.
2. **The proof insert belongs to a different arm from the pillars it cuts from.**
   - 11.6–16.0 gives all four regions a `delta` marker. That is the Graph arm, which is correct for
     OneDrive, SharePoint, Outlook and Teams (README line 97).
   - The 20.0–25.0 insert is the File Provider placeholder refusal. That is the local sync-folder arm
     (README lines 98 and 225–237).
   - a.js places the two reads in the SharePoint and Outlook regions (`reads`: r 1 and r 2). An
     Outlook message is never a dataless placeholder.
   - Cutting from a pillar to that recording tells the viewer that this read was a placeholder read.
3. **"Paid, then free at H2" (0.0–3.0, 25.0–30.0) is right, but the README contradicts it on the same
   page.**
   - Design §4.1 (line 113) says "H2 — not H1 — is what makes a no-op save free", and README lines
     27–28 agree. Critique round 2 §6 confirms that a no-op save changes bytes, so it cannot exit at
     H0.
   - The README's own classifier diagram labels H1-equal "TOUCHED, NOT CHANGED, no-op save: stop"
     (line 182, alt text at line 166). Design pseudocode line 266 says the same.
   - storyboard-verdict.md flagged this, and it is still open. Fix the docs first, or the hero
     contradicts a diagram about 30 lines further down the page.
4. **"Three hashes" is shown as two.** H0 appears at 16.0–20.0 and H2 at 25.0–30.0. H1 never
   appears. The order is correct: only H0 runs before the read (README lines 159–161). But the step
   card promises three.
5. **"The only slow motion" is false within the board.**
   - The 20.0–25.0 rise is 1.6 s.
   - The 7.0–11.0 amber wave is a 4 s constant-rate rise over the whole field, and in the LOOP it runs
     2.6 s (3.4–6.0).
   - The counterfactual read is therefore slower, larger and louder than the real one. That breaks
     DIRECTION's "tempo means cost" and the rubric's "the one read is the loud thing".
6. **The two pillars have unequal heights (a.js: 5.5 and 4.0).** Height now means cost, so the board
   claims two reads of different cost with no source. Perspective adds a second, meaningless size
   difference: the near pillar is 52.8 CSS px tall and the far one 32.7 (measured).

### Other findings

- **The anti-thesis window.**
  - LOOP 3.4–6.0 is 2.6 s of 13, 20 % of the loop, estimated from the table. Throughout it, the frame
    fills with amber under the headline "by processing only what changed."
  - Visitors land mid-loop (readme-heroes §3: the clock starts at page load and the hero sits at a
    median 2,062 px down). One in five sees the opposite of the claim.
  - It is also the most expensive stretch of the loop (see Feasibility).
- **One motion carries two meanings.**
  - "Pillars sink" at 3.0–3.6 means the next sync. "Every pillar drops flat" at 11.0–11.6 means the
    counterfactual is withdrawn.
  - The sink also drains paid bytes, which DIRECTION's read row forbids ("bytes, once paid for, never
    drain").
- **The LOOP's step section cannot fit its own vocabulary.**
  - 6.4–10.0 packs four steps into 3.6 s.
  - Estimated from DIRECTION's timings, run serially: ring 0.2 + exit 1.5 + read 1.6 + travel 1.0 +
    write 0.6 = 4.9 s.
  - The captions `1 ask` … `4 publish` each get under 1 s.
- **Marks vanish at 838 px** (estimated, × 0.4365):
  - the 2 px green frame, which is the whole "paid then free" distinction, becomes 0.87 CSS px on a
    pillar 10.9–16.6 CSS px wide (measured);
  - the 1.5 px walk line becomes 0.65 CSS px;
  - far tiles are about 2 CSS px apart, so they turn to moiré under lossy WebP.
- **The light-theme meter fails AA.** `#c86a00` on white measures 3.81:1. At 34 design px (14.8 CSS
  px) that is normal-size text, which needs 4.5:1. VISUAL.md's own exemption was for a 24 px meter.
- **The insert is unreadable.** dataless-read.tape records at 17 px in a 1060 px frame. Framed inside
  a 1920 film and played in GitHub's 836 px player, its text is about 7–8 CSS px (estimated), on
  screen for 2.5 s (20.0–22.5).
- **The FILM's middle is the README read aloud.** 11.6–30.0 is 18.4 s of 40 (46 %), made of four
  numbered cards (`1 ·` … `4 ·`) over a diagram. The README lists the same four steps just below the
  hero.
- **The look risks a generic "data landscape".** The board flags this itself, and the look-frame
  confirms it: a fogged perspective grid in which the amber is 0.21 % of the frame (measured).

### Scores, with timecodes

1. **Answer at t = 0 (4).**
   - The poster at 0.0–3.0 states the full sentence, with the headline at 48.9 CSS px and the subject
     at 21.8.
   - Minus: the invented meter is the second-loudest element, and the green frame is invisible.
2. **Answer at t = 3 s (4).**
   - The two pillars carry "only what changed" from 0.0, and the headline stays at 70 % in every
     window.
   - Minus: the 3.4–6.0 anti-thesis window.
3. **One idea (3).** The pillars at 20.0–25.0 are the idea. The 7.0–11.0 wave makes the naive cost
   the loudest thing.
4. **Meaning per motion (3).**
   - Plus: height and hue together are a strong shape-and-hue encoding.
   - Minus: the sink and the snap share one motion (3.0 / 11.0), the wave is slower than the read,
     and the frame is sub-pixel.
5. **Cinematic quality (3).**
   - The FILM has a real grammar: the 3.6–7.0 dolly, the 11.6 tracking shot, the 30.0 crane, and the
     11.0 snap to "Or read two.".
   - The LOOP, the hero people actually see, is a locked-off captioned diagram, and 46 % of the FILM
     is step cards.
6. **Restraint (2).** Against it:
   - the invented 12,160 (0.0, 7.0–11.0, LOOP 3.4–6.4);
   - "this design: 2 reads";
   - a fake `forecast.xlsx` page (3.6–7.0).

   For it: the real insert at 20.0–25.0, and no glow or particles.
7. **Both themes (3).** The look works on #0d1117 and #ffffff. The light meter fails AA at its size.
8. **Memorability (4).** "Two amber pillars on a flat plane of documents" can be described a day
   later.
9. **Feasibility (3).**
   - The LOOP is locked off, which is good.
   - The wave and snap (3.4–6.4) change most of the plane. Estimated at 0.3–1.0 MB/s, between the
     measured two-pillar rise (117,890 B over 2.0 s) and the measured dolly (2,084,364 B over
     2.0 s).
   - Estimated LOOP total: about 1.2–3.3 MB at 1280 × 720, and × 1.71 at the 1676 × 943 the 838 px
     column needs.
   - The wave is most of the bytes.

---

## B: the sentence

### Mechanism and honesty errors

1. **Card 2 puts H1 and H2 before any read (11.0–15.5).**
   - The card reads "Three hashes decide / before a byte is read." over chips H0, H1 and H2, and
     look-b adds "0 bytes read" beside all three.
   - H1 is a canonical content hash and H2 is the hash of the converter's output (README lines
     160–161). Both need the bytes, and the classifier colours them amber.
   - Only H0 decides before the read (README line 159).
   - The card plays before card 3's read, so the running order asserts the error too. Oddly, B's
     LOOP copy at 5.0–7.0, "Decide before reading a byte.", is the correct form.
2. **Fabricated tool output (21.0–26.0).**
   - `git log --since=tuesday -- docs/` types out, and "one commit line appears". That is the output
     of a sync that has never run (README line 14).
   - It appears in the same terminal idiom as the real recording 6 s earlier (15.5–21.0), so the fake
     inherits the receipt's credibility.
3. **The no-op save is missing.**
   - The design's measured headline finding is absent: Office never re-saves byte for byte, and H2
     makes the save free (README lines 210–219).
   - "Two files read. / One commit." (26.0–30.0) leaves the second read with no fate.
4. **"walk" has two meanings in one card (7.0–11.0).** It is a since-token chip, the manual-drop row
   in README line 99, and also the expiry fallback, "Walk the metadata. 0 bytes." (README lines
   101–103). This one is minor.

### Other findings

- **There is no image.** The board says so itself under Risks. The poster at 0.0–3.0 is type, so
  nothing visual carries "only what changed" by t = 3.
- **The loop loses its subject.** Across LOOP 3.0–10.5 (7.5 s of 12) neither the governing thought nor
  the wordmark is on screen; look-b shows only "2 / 4". A visitor landing at 5 s reads "Decide before
  reading a byte." and does not know whose tool this is or what it is for (readme-heroes rules 3–4).
- **The poster headline does not fit its frame.**
  - At 150 px, "by processing only what changed." is about 2,200 px wide. Estimated by scaling the
    measured 1,665 px ink width at 112 px in look-a, and correcting for the −0.05 em tracking.
  - About 1,700 px is available, so the line must break in two. The poster becomes a 4–5-line block
    of type.
- **Some motion means nothing.**
  - The per-word 9 → 0 px blur-in with a 70 ms stagger (4.2–7.0) is the stock keynote preset.
  - The dive into the period (3.0–4.2) is decoration.
- **Two more problems:**
  - The amber progress bar labelled `2 files` (15.5–21.0) is UI chrome. A bar of two has no scale.
  - Card 1 (7.0–11.0) packs four text beats into 4 s.

### Scores, with timecodes

1. **Answer at t = 0 (5).** At 0.0–3.0 the full sentence is at display scale: 65.5 CSS px headline
   and 24.4 CSS px subject (estimated, × 0.4365). It is the most legible poster of the three.
2. **Answer at t = 3 s (2).** It is words only until 7.0. The LOOP drops the subject at 3.0–10.5.
3. **One idea (2).** It is four cards of equal weight (7.0–26.0), which makes a list, not an
   argument.
4. **Meaning per motion (2).** The blur-in (4.2) and the dive (3.0) mean nothing, and H1/H2 appear
   before the read (11.0).
5. **Cinematic quality (2).** Hard cuts between cards of words (LOOP 3.0–10.5), which is the board's
   own "slide deck" risk.
6. **Restraint (2).** The fake `git log` output (21.0–26.0), the progress bar (15.5), and decorative
   type motion.
7. **Both themes (5).** Flat type. Both halves of look-b are clean.
8. **Memorability (1).** "Big words", in the board's own words.
9. **Feasibility (5).** Flat fields and one keyframe per cut. The cheapest of the three.

---

## C: Tuesday and today

### Mechanism and honesty errors

1. **C shows the design skipping real edits.**
   - At 6.5–11.0, eight thumbnails differ visibly: "a spreadsheet cell changes, a mail arrives, a chat
     line appears".
   - At 15.0–19.5, six of those eight exit at H0 as "same".
   - H0 equal means the provider content hash or the stat tuple is unchanged (README line 159).
     Design lines 241 and 248 put it precisely: provider hash equal means UNCHANGED, and an etag that
     moved while size and mtime hold means METADATA_ONLY (label, rename or move).
   - A visibly edited cell changes quickXorHash. A new mail is a new id, which the classifier sends
     to a read (README line 191, "moved, or new id").
   - As drawn, C depicts a correctness bug.
2. **"6 same (a no-op touch)" at H0 (15.0–19.5) is the wrong exit.** The design's
   touched-not-changed state is the Office no-op save. It is decided after the read (design line 266;
   README line 182) and made free at H2 (design line 113). A no-op save changes bytes, so it cannot
   exit at H0.
3. **The metadata comparison shows content, and it draws the fallback as the normal path.**
   - The wipe is labelled "a metadata comparison, 0 bytes" (6.5–11.0), yet it reveals today's
     content: the viewer watches the pipeline see bytes it has not read.
   - A comparison across the whole corpus is the full enumeration: the 410 resync or the scheduled
     reconcile (design lines 115, 153–154 and 257).
   - The normal path is a since-token that returns only the changes (README lines 92–93).
   - At 11.0–15.0, "a second, thinner line" draws the same meaning again for the 410.
4. **The placeholders are inverted** (critique round 2 §6.1 applies unchanged). A wall of readable
   documents says the corpus is local and viewable. README lines 20 and 225 say it is mostly
   online-only placeholders that must not be opened.
5. **"2 of 12,160 read" (30.0–33.0) is not even C's own number.** C draws 60 thumbnails with "a
   thousand more implied" (3.0–6.5). 12,160 is A's grid count, copied across.
6. **The second read has no fate.** There is no H2 exit anywhere in 19.5–30.0.
7. **The look-frame contradicts the board.**
   - It labels Tuesday on the left and today on the right. The board's left-to-right sweep makes the
     swept left side today.
   - Its amber item is a chat thread (c.js: `kinds[31 % 4]`), while step 3's proof insert is a file
     placeholder.

### Other findings

- **The wipe inverts tempo.** The zero-cost sweep is the longest motion in the film: 4.5 s at
  6.5–11.0, and 3.5 s at LOOP 3.5–7.0. The reads take 1.5 s at LOOP 8.5–10.0. DIRECTION requires a
  walk to be faster than a read.
- **The wipe is also the loudest mark.** A 3 px, full-contrast, full-height ink line is the brightest
  element in look-c, and it costs nothing.
- **The detail turns to texture at 838 px** (read from c.js, × 0.4365):
  - thumbnails are 57.6 × 74 CSS px;
  - their interior strokes are 2.2 CSS px;
  - a spreadsheet cell is 10.5 × 5.2 CSS px.

  The "eight differ" beat is below perception, estimated from those sizes.
- **The poster does not fit.** C's wall alone is 5 × 194 = 970 px tall (c.js), and a two-tier
  governing thought needs about 300 px (measured in look-a, wordmark top to headline bottom, y 106–405). The look-frame gave up and set "Only the
  difference is processed." at 72 px (31 CSS px). That is a paraphrase, not the governing thought.
- **The wipe itself is a trope.** A before/after slider is a product-comparison device.

### Scores, with timecodes

1. **Answer at t = 0 (3).** At 0.0–3.0 the governing thought is squeezed above a 60-thumbnail wall.
   Estimated sizes are ≤ 31 CSS px for the headline and ≤ 17 for the subject.
2. **Answer at t = 3 s (3).** The poster's two amber outlines carry "only what changed" weakly,
   against texture. The comparison, the board's idea, does not start until 3.5.
3. **One idea (2).** "Look, the documents differ" argues against "decided without reading a byte".
4. **Meaning per motion (2).** Two lines draw one meaning (6.5 and 11.0). The wipe both reveals
   content and means "0 bytes". The loudest mark is free.
5. **Cinematic quality (2).** The pan (3.0–6.5) and pull-back (30.0–33.0) are real camera moves. The
   rest is 19 s of step cards (11.0–30.0) and a slider wipe.
6. **Restraint (1).** A wall of invented content (6.5–11.0), plus an invented number that
   contradicts the picture (30.0).
7. **Both themes (4).** Both halves of look-c hold up. In light, the thumbnail fill `#f6f8fa` is
   1.06:1 on white, so the wall lives on a 2.01:1 hairline.
8. **Memorability (3).** "A line sweeps a wall of documents" is describable, and generic.
9. **Feasibility (4).** The LOOP is locked off, and the changed rectangles are small.

---

## Winner A: the changes it needs before build

The must-fix items come first. Each item that borrows from a loser names the source.

1. **Delete "12,160" everywhere** (0.0–3.0, 7.0–11.0, 30.0–40.0, LOOP 0.0–3.0, 3.4–6.4, 10.0–13.0).
   - Let the plane's horizon carry "tens of thousands of files" (README line 19) without a count.
   - Replace the Mono counter with **B's payoff line, trimmed to "Two read. One commit."** (B 26.0–30.0 reads "Two files read. / One commit."), set in
     Sans on the poster. It is a sentence about one illustrated sync, not a tool's meter.
   - Drop "this design: 2 reads". No Mono number ticks anywhere.
2. **Take the amber wave out of the LOOP. In the FILM, draw it so it cannot be mistaken for this
   design's reads.**
   - In the FILM, shorten it to ≤ 2 s and draw it as hollow neutral outlines rising, not solid amber.
     Solid amber height then stays exclusive to the two real reads, and "the only slow motion" (20.0)
     becomes true.
   - This removes the 20 % anti-thesis window and most of the LOOP's bytes. Estimated LOOP size
     without it: under about 0.5 MB at 1676 × 943.
3. **Replace the sink and the snap with cuts (B's cut grammar).**
   - Remove the reset sink (3.0–3.6) and the counterfactual snap (11.0–11.6).
   - The "next sync" becomes a hard cut to the pre-sync plane, then a hard cut back. In an animated
     WebP a cut costs one keyframe, and a held shot costs ~25–70 KB/s (measured medium-still /
     medium-rise).
   - No motion then means "drain".
4. **Put the cinema in the LOOP, not only in the click-to-play MP4.**
   - Use B's hard-cut grammar with locked-off shots: wide poster (0–3) → cut to a low close on one
     region for ask and decide → cut to a close on one pillar rising (the 1.6 s read, the only slow
     motion) → cut back to the wide poster, held across the seam.
   - Re-time the loop to about 15 s so DIRECTION's own durations fit. As drawn, 6.4–10.0 needs about
     4.9 s of serial motion in 3.6 s.
5. **Make the arms coherent.**
   - Mark the OneDrive region's token `FSEvents` (README line 98, the synced-folder arm), and put the
     read that the insert cuts from in that region.
   - Or present the insert as a separate proof plate ("recorded run: the read is refused"), not as a
     close-up of a pillar.
   - Never cut from an Outlook or Teams item to the placeholder recording.
6. **Make "paid, then free at H2" readable, in shape as well as hue.**
   - Draw the frame at ≥ 5 design px (≥ 2 CSS px), or give the H2 pillar a flat green cap.
   - Make that pillar `forecast.xlsx`, the hook page at 3.6–7.0. README line 216 is the reason: an
     `.xlsx` re-mints `documentId` on every save while H2 stays identical, so the hook pays off.
   - Give both pillars the same height, and put them at similar depth.
7. **Fix the README's H1/H2 contradiction before rendering anything that says "H2".** Change
   classifier.mmd so that H1-equal no longer reads as "no-op save: stop" (README line 182, alt text
   at line 166). Otherwise the hero is wrong against its own page. Either show H1 once, or stop
   promising three hashes on the step-2 beat.
8. **Stop the hook page showing content (borrowed from C's comparison mechanic, used honestly).**
   - At 3.6–7.0, show the page as the pipeline sees it: name · size · modified · online-only (README
     lines 20 and 225). That is metadata, not a spreadsheet.
   - At 16.0–20.0, give "decide" C's comparison as a picture, but at H0 and on metadata: the stored
     hash meets the reported hash (`=`), then the exit turns green and fades.
   - C diffed visible content, which the design never does. This version diffs what the design does
     diff.
9. **Tie the publish beat to C's "Tuesday".**
   - Replace the step card at 25.0–30.0 with the README's own sentence: "What changed since Tuesday
     is `git log`." (README line 11). That is a claim about the design, not output.
   - Do not use B's typed `git log` with an invented commit line.
10. **Drop the numbered cards.**
    - 11.6–30.0 re-reads the README's list. Use B's short declaratives instead, one per shot at
      display scale, with "0 bytes" on the free beats: "Ask it." / "Tokens expire. Walk the
      metadata. 0 bytes." / "Read on purpose." (B 7.0–21.0).
    - Keep A's pictures under them.
11. **Crop the insert to the refusal.** The 2.5 s insert (20.0–22.5) cannot be read at 17 px.
    - Pull out the `Resource deadlock avoided (errno 11)` / `0 bytes` moment.
    - Scale it to ≥ 32 design px, and hold it ≥ 2 s. The full recording already sits further down the
      README.
12. **Set type for 838 px.**
    - The wordmark goes from 30 to ≥ 36 design px (15.7 CSS px).
    - Region labels are ≥ 32 design px.
    - The walk line is ≥ 4 design px.
    - Amber text in light is either ≥ 55 design px (24 CSS px, large text, where 3.81:1 passes 3:1)
      or uses `#b35900` (4.83:1, measured).
    - Author the LOOP at 1676 × 943, not 1280 × 720 (readme-heroes rule 4).
13. **Route visitors to the film, and update the alt text.**
    - Wrap the `<picture>` in a `<div>` with `<a href>` to the MP4, plus a `<sub>` caption
      (github-medium.md: a lone `<a><img>` is replaced by the player).
    - Otherwise the 40 s film exists only as a click-to-play card.
    - Rewrite the hero alt text, README line 4, which still describes 256 objects in four blocks.

## The single memorable image, and whether frame 0 carries the governing thought

**The image:** two amber pillars standing on a flat, dark plane of documents that runs to the horizon.
Nothing else on it has height. Colour and height both mean bytes read. A viewer could describe it a
day later.

**The frame-0 poster at 838 px.** It carries the governing thought legibly. Measured from
look-a-dark/t0000.000.png and scaled by 0.4365:

| Element | Font size | x-height |
|---|---|---|
| "by processing only what changed." | 48.9 CSS px | ≈ 26 px |
| Subject line | 21.8 CSS px | ≈ 11 px |
| Meter (smallest non-brand text) | 14.8 CSS px | ≈ 7.4 px |
| Wordmark (smallest text) | 13.1 CSS px | ≈ 6.5 px, muted `#8b949e`, 6.15:1 |

- **Readable:** all of it, but the wordmark and meter sit below GitHub's 16 px body text.
- **Not visible:** the green H2 frame, at 0.87 CSS px.
- **Not yet designed:** the region labels. The look-frame has none, and they must be ≥ 32 design px.

On a 360 px phone column (estimated at × 0.1875):

| Element | Size |
|---|---|
| Headline | 21 px (reads) |
| Subject | 9.4 px |
| Meter | 6.4 px |
| Wordmark | 5.6 px |

So on a phone the headline reads, and the rest does not.

## What would still make the operator say "still an infographic"

- **The loop has the old SVG's structure.** A locked-off field with rings, green H0 flashes, two amber
  cells, a meter and a git line is the rejected hero's beat list (DIRECTION's ring / walk / expire /
  exit / read / travel / write), now drawn in perspective. If the cinema lives only in the
  click-to-play MP4, the README shows "the same infographic, tilted".
- **Numbered step cards.** `1 ·` … `4 ·` repeat the list that sits directly below the hero.
- **A dashboard stat.** "2 of 12,160 read" in Mono is a meter, not a film title.
- **Mono labels on the stage.** `H0`, `410`, `delta` × 4, `+1 commit`, `H2 equal: no-op save`. One
  label per shot at display scale is film; six small ones is a diagram.
- **Anything under about 14 CSS px, and any caption held under about 1.5 s.**
- **A boxed MP4 player as the only film.** The player carries a filename header and a 640 px height
  cap, and it opens on frame 0 with no autoplay.

In the losers' terms: B is "big words in slides", and C is an icon grid with a slider.

## Verdict

Build A, but not as written. A is the only direction with an image that is the argument: a flat plane
of free work and two pillars of paid reads, where height and amber both mean bytes. It is also the
only one with a real arc. As written, it spends its loudest, slowest and most expensive shot on the
counterfactual. It stamps a grid-derived "12,160" on frame 0 as if it were a benchmark, cuts from Graph
reads to a File Provider recording, and keeps all its cinema in an MP4 that GitHub will never
autoplay. Fix those four things:

- delete the number and use B's "Two read. One commit.";
- take the amber wave out of the loop, and draw the FILM's naive read hollow;
- make the arms coherent;
- bring B's hard-cut grammar into the inline loop.

Take C's "since Tuesday" framing and its comparison mechanic, applied to H0 metadata rather than
visible content. Then A becomes a launch film whose frame 0 states the governing thought at 49 CSS
px, above a picture people will remember. B stays the best-typeset poster and the weakest film. C
should not be built: its central shot depicts the design missing real edits.
