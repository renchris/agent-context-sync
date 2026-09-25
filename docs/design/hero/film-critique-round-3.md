# Launch film: critique, round 3 (fresh context)

Reviewed build: the round-3 world (`film/cards.js`, `film/world.js`, three.js), rendered on 2026-09-24,
before the fixes below. The critic was briefed with the operator's verdict on round 2 as its top
criterion and never saw rounds 1 or 2. What was adopted and declined is in
[`DIRECTION.md` § Decisions (film)](DIRECTION.md#decisions-film), under Round 3.

**L** = LOOP (15 s), **F** = FILM (25 s). Legibility is judged at 838 CSS px (0.44 of 1920).

## 1. Verdicts

**(a) Concreteness: PARTLY. The objects are concrete; the actions are still symbols.**
The objects are fixed: named cards (proposal.docx "Northwind renewal", roadmap.pptx) at L0.0, four source lanes named in type at L4.0–7.0, and a docs/ lane of .md pages with `docs/mirror/onedrive/proposal.md` at L11.5–12.9 and F21.5. Still to decode:
- the H0/H1/H2 chips (L5.5–7.0, L10.0; F11.5–12.5, F16.5–19.0)
- a green slab (L0.0–2.5, L10.4–15.0; F17.8–25.0)
- a grey rod with a blob for git (L11.8–13.3; F21–23)
- **no agent anywhere**: F20.9–22.4 says "Your agent reads docs/." over a picture with no reading in it.

**(b) Continuous 3D transitions: FIXED.**
- LOOP: four flights (2.5–3.5, 7.1–8.0, 10.4–11.4, 12.9–13.9) through one periodic world, with no cuts. The seam holds: L13.9–15.0 is the L0.0 pose, one stretch on.
- FILM: one unbroken camera path from 0 to 25 s.
- Residual: the 1 s LOOP flights blur to mush (L2.9–3.1, L10.8–11.0, L13.3–13.5). They read as a whoosh, not as geography, so the viewer never learns where docs/ sits.

## 2. Findings (ranked)

1. **F6.2–9.7, the amber wall: blocker.** 3.5 s with no caption, the whole field in the bytes-read colour at full fill (F8.0, F9.0). It is the counterfactual, but nothing says so, and it renders solid even though world.js:287 says "hollow and dimmer". The film's biggest image shows the opposite of what the tool does.
   **Fix:** caption F6.4–9.5 with "Re-reading all of it: hours, every sync." and draw the wall as amber outlines at about 35%, no fill.
2. **L10.1–10.4, the no-op save: major.** "H2 =" and the cap arrive at L10.1. The flight leaves at L10.4 and the chips are gone by L10.5: the loop's only free exit is on screen for 0.3 s.
   **Fix:** hold the read L8.0–11.0; cut ask and decide to 1.5 s each to pay for it.
3. **The green slab: major.** At L0.0 it overhangs forecast.xlsx and hides the filename, and the LOOP never says what it means.
   **Fix:** a flat green band across the card *below* the name, reading "no real change". Keep the name visible.
4. **docs/ duplicates: major (concreteness).** At L12.0 and F21.5 the landed proposal.md sits next to another proposal.md, with 3 more in view. forecast.md and roadmap.md appear 3 times each. A real folder cannot look like that. cards.js:51 has only 8 names.
   **Fix:** about 48 unique names (budget-fy27.md, offsite-agenda.md…), and proposal.md only in the landing slot.
5. **Header collisions: major (legibility).**
   - The painted "docs/" label sits under the tagline at L11.5–13.0 and F20.5–22.4 (x≈1460–1690, y≈70–110).
   - Cards run under both docs headlines.
   - At L11.0 a card edge strikes through "sync with Mi".
   **Fix:** a background-colour scrim from 0% to 85% over y 0–300 px, and docs pose cy 600 → ~680.
6. **No agent: major (criterion a).** The agent line (F20.9–22.4) lasts 1.5 s, 0.8 s of it fade.
   **Fix:** hold 2.5 s and set the question "What changed since Tuesday?" (README l.18) in type. The HEAD commit dot and proposal.md brighten as the answer. This is concrete and not fake output.
7. **Hash jargon: major.** "H0 =" (L5.5–7.0) means nothing on first view.
   **Fix:** "H0 = · skip" (green), "H0 ≠ · read", "H1 ≠ · bytes differ", "H2 = · same page". Still about 13 px at 838.
8. **Light grade: major.** Amber turns burnt orange (~#C86400) and the cap forest green (L0.0, L10.5). The field is near-white on white, and the change rings at L4.0–5.0 almost vanish at 838 px.
   **Fix:** card outline and fill two steps darker (#C4CAD3 / #F1F3F6), double the ring stroke, and keep the dark grade's amber hue.
9. **F12.7–16.2, the read has no caption: minor.** F13.0–16.0 has no line.
   **Fix:** add `line: 'read'` to that edit.
10. **Git line: minor.** At L11.8–13.3 it reads as a matchstick; the previous commit is out of frame.
    **Fix:** neutral commit dots every 4 rows, with HEAD the only white one.

**Honesty:** nothing reads as tool output. The chips label world objects, and the FILM poster carries "no implementation yet". The only exposure is L0.0 ("Two files read. One commit." without the status line), and the README caption directly under it covers that.

## 3. The remembered image, and the first 3 seconds

**Remembered image.** LOOP: two amber pages standing out of a flat grey field of thousands (L0.0, L9.5). That is the message. FILM: the amber wall (F8.0), which today says the wrong thing (finding 1).

**Do the LOOP's first 3 seconds say what the tool does?** In the words, yes: L0.0–2.5 holds the full sentence plus the payoff, with the headline about 52 px tall at 838. The picture only says "two files were read": the lane labels under the reads are flattened to illegibility (y≈695), and there is no docs/ and no commit in shot.
**Fix:** on the poster only, add a muted 28 px label under each amber card, "OneDrive" and "SharePoint".
