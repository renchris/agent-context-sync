# Launch film, direction C: Tuesday and today

**One image.** A wall of real-looking documents (Word pages, spreadsheets, mail, chat threads) seen
twice, Tuesday's and today's, divided by a wipe line. As the line crosses the wall, almost nothing
differs. The few thumbnails that did change are the only ones that react, and the pipeline takes
only those. The mechanic is comparison, the thing the design actually does: it diffs a listing
against a manifest.

**Deliverables.** `FILM`: an MP4 master, 1920 × 1080, 60 fps, dark grade, about 38 s. `LOOP`: an
animated WebP of about 13 s, dark and light grades behind `<picture>`.

## FILM (≈ 38 s)

| t (s) | Shot | On screen | Motion (and what it means) |
|---|---|---|---|
| 0.0–3.0 | **Poster** | The governing thought across the top in two tiers. Below it, a wall of 60 document thumbnails, the wipe line parked at the right edge ("today"), two thumbnails outlined amber and one page docked in `docs/` at the far right | Held |
| 3.0–6.5 | Hook, slow pan across the wall | Card: **This is Tuesday.** The wall at rest; a thousand more thumbnails implied beyond the frame edges | `glide` pan |
| 6.5–11.0 | The wipe | Card: **This is today.** A 3 px ink line sweeps left to right; thumbnails behind it are *today's*. Eight thumbnails differ: a spreadsheet cell changes, a mail arrives, a chat line appears. Each gets a muted ring as the line passes | The line = a metadata comparison, 0 bytes. Ring = reported |
| 11.0–15.0 | 1 | **Ask each source what changed.** The four sources label the wall's columns; one label flips red `410`, and a second, thinner line re-walks its columns | Walk = a listing, 0 bytes |
| 15.0–19.5 | 2 | **Decide with three hashes before reading a byte.** `H0` on the 8 rings: 6 same (a no-op touch), green, then back to neutral | Exit = free |
| 19.5–25.0 | 3 | **Read bytes only on purpose.** The two remaining thumbnails fill amber from the bottom; insert: the real refused read on a placeholder | Fill = bytes |
| 25.0–30.0 | 4 | **Publish to git.** One thumbnail converts: its page flips into a markdown page and slides into a `docs/` stack at the right edge; a commit line appears under it | Travel = converted markdown |
| 30.0–33.0 | Payoff | Pull back: the whole wall, two amber, one page in `docs/`. **2 of 12,160 read.** | Held |
| 33.0–38.0 | **Poster** | Identical to 0.0 | Held |

## LOOP (≈ 13 s)

| t (s) | Beat |
|---|---|
| 0.0–3.0 | Poster, held. |
| 3.0–3.5 | The wipe line jumps back to the left edge (Tuesday); outlines clear. |
| 3.5–7.0 | The line sweeps to "today"; 8 thumbnails ring as it passes. |
| 7.0–8.5 | 6 flash green at `H0` and return to neutral. |
| 8.5–10.0 | 2 fill amber. |
| 10.0–11.0 | One page slides into `docs/`, `+1 commit`. |
| 11.0–13.0 | Poster, held across the seam. |

**Bytes.** Moderate: the wall is detailed, so every changed rectangle is expensive, but the camera is
still in the loop.

**Risks.** Thumbnails of "real documents" are fake content, and at 838 px they turn to texture. A
wipe is a common transition; it has to read as the diff, not as an effect.
