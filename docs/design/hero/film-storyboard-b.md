# Launch film, direction B: the sentence

**One image.** The governing thought is the film. It opens as one sentence, then the camera travels
*into* its last words, and "only what changed" opens into the four steps: one line per card, at
display scale, each with a single small glyph that shows the step's cost. No field, no landscape:
type carries everything, the way an Apple feature film carries a product with one sentence on a
field of colour.

**Deliverables.** `FILM`: an MP4 master, 1920 × 1080, 60 fps, dark grade, about 36 s. `LOOP`: an
animated WebP of about 12 s, dark and light grades behind `<picture>`.

## FILM (≈ 36 s)

| t (s) | Card | On screen | Motion (and what it means) |
|---|---|---|---|
| 0.0–3.0 | **Poster** | The full governing thought, left-aligned, two tiers (subject line muted at 56, **by processing only what changed.** at 150, Sans 600, −0.05 em). Wordmark `agent-context-sync` in Mono above | Held |
| 3.0–4.2 | Dive | The camera scales into "changed." until the period fills the frame and becomes the frame's dark field | `glide` zoom, 1.2 s |
| 4.2–7.0 | Hook | **Tens of thousands of files.** Then, under it, *Most of them never change.* | Line rises 24 px with a 9 → 0 px blur-in, per word, 70 ms stagger |
| 7.0–11.0 | 1 | **Every source keeps a change log.** / **Ask it.** Chips: `delta` · `FSEvents` · `walk`. Then **Tokens expire.** with `410` in red, and **Walk the metadata. 0 bytes.** | Chip = a since-token (neutral). Red only on 410 |
| 11.0–15.5 | 2 | **Three hashes decide** / *before a byte is read.* Chips `H0` `H1` `H2`; `H0 =` turns green: **6 of 8 stop here.** | Green = free exit; it returns to neutral as the card leaves |
| 15.5–21.0 | 3 | **Online-only files stay online.** Insert: the real `dataless-read` recording, the read refused. Then **Read on purpose.** with an amber bar filling left to right, `2 files` | Amber fill = bytes; the only slow motion |
| 21.0–26.0 | 4 | **Publish to git.** A Mono line types: `git log --since=tuesday -- docs/` and one commit line appears | Type-on = a commit being written |
| 26.0–30.0 | Payoff | **Two files read.** / **One commit.** | Hard cut in, held |
| 30.0–36.0 | **Poster** | Identical to 0.0 | Held |

## LOOP (≈ 12 s)

| t (s) | Beat |
|---|---|
| 0.0–3.0 | Poster, held. |
| 3.0–5.0 | **Ask each source what changed.** + since-token chips, `410` red on one. |
| 5.0–7.0 | **Decide before reading a byte.** + `H0 =` green on 6 of 8. |
| 7.0–9.0 | **Read bytes only on purpose.** + amber bar, `2 files`. |
| 9.0–10.5 | **Publish to git.** + `+1 commit`. |
| 10.5–12.0 | Poster returns, held across the seam. |

**Bytes.** Cheapest of the three: flat type on a flat field, hard cuts (each cut is one keyframe).

**Risks.** It can read as a slide deck. Type has to do cinematic work on its own: scale changes,
the dive into the period, blur-in reveals. There is no single image a viewer could describe
afterwards other than "big words".
