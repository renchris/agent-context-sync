# Launch film, direction A: the plane

**One image.** A company's knowledge is a dark plane of documents that runs to the horizon, four
regions wide, one per source. Reading bytes is the only thing on it that has **height**. After a
sync, two amber pillars stand in a flat field of 12,160 tiles. Colour means cost, as everywhere in
this repo, and now height means cost too. The flat plane is the free work.

**Deliverables.** `FILM`: an MP4 master, 1920 × 1080, 60 fps, dark grade, about 40 s, played from
a user-attachments link. `LOOP`: the inline hero, an animated WebP of about 13 s, one dark and one
light grade behind `<picture>`, at the 838 px README column.

## FILM (≈ 40 s)

| t (s) | Shot | On screen | Motion (and what it means) |
|---|---|---|---|
| 0.0–3.0 | **Poster**, wide, camera still | Wordmark `agent-context-sync` (Mono). The governing thought in two tiers: the subject line in muted Sans 50, then **by processing only what changed.** in Sans 600 at 112, tracked −0.045 em. The plane below, with two amber pillars (one wearing a 2 px green frame: paid, then free at H2). Meter `2 of 12,160 read` in amber Mono. Region labels on the near edge | None. It is the answer, held. |
| 3.0–3.6 | Reset | The headline dims to 30 %, the pillars sink into the plane, the meter clears | `leave`: the outcome clears for the next sync |
| 3.6–7.0 | Push-in, then pull-back | One tile fills the frame as a page (`forecast.xlsx`), then the camera pulls back through the plane until all 12,160 tiles are in view. Card: **Tens of thousands of files.** / *Changing in place, under the same name.* | `glide` dolly. The scale is the hook |
| 7.0–11.0 | Wide, still | Card: **Re-reading all of it on every sync** / *means hours of downloads.* An amber wave raises every tile into a pillar, left to right, at a constant rate. The meter runs to `12,160 read` | `walk`: bytes are metered. The frame fills with amber: this is what the naive sync costs |
| 11.0–11.6 | Snap | Every pillar drops flat at once | `leave`, 0.4 s. Card: **Or read two.** |
| 11.6–16.0 | Low tracking shot along the near edge | Card **1 · Ask each source what changed.** Four since-token markers (`delta`, `delta`, `delta`, `delta`) each ask: muted rings appear on 8 tiles. SharePoint's marker turns red, `410`; a 1.5 px line walks its region | Ring = reported, metadata only. Walk = a metadata listing, 0 bytes. Red only on the expiry |
| 16.0–20.0 | Medium, still | Card **2 · Decide with three hashes before reading a byte.** `H0` labels (Mono) over the 8 rings; 6 flash green and fall back to grey | Exit = free. Transient colour returns to neutral |
| 20.0–25.0 | Close on the two, then an insert | Card **3 · Read bytes only on purpose.** The 2 tiles rise slowly into amber pillars (1.6 s, the only slow motion). Insert (2.5 s): the real `dataless-read` recording, framed, showing the read refused on a placeholder | Rise = bytes read. The insert is the proof, a real run |
| 25.0–30.0 | Medium | Card **4 · Publish a pure function of the source, in git.** A text-coloured page lifts off one pillar and travels to the `docs/` git line at the plane's edge; a commit dot lands, `+1 commit`. The other pillar gets its 2 px green frame: `H2 equal: no-op save` | Travel = converted markdown going to git |
| 30.0–34.0 | Crane up to the wide | The plane, two pillars, meter `2 of 12,160 read` | The payoff |
| 34.0–40.0 | **Poster** | Identical to 0.0 | Held. The film ends on the frame it opened with |

## LOOP (≈ 13 s, the README hero)

| t (s) | Beat |
|---|---|
| 0.0–3.0 | Poster, held (frame 0, reduced-motion frame, cached-resume frame). |
| 3.0–3.4 | Pillars sink; headline stays, dimmed to 70 % (it is on screen at every t). |
| 3.4–6.0 | Caption `re-reading everything: 12,160 reads` — the amber wave raises the whole field. |
| 6.0–6.4 | Snap flat; caption `this design: 2 reads`. |
| 6.4–10.0 | Rings on 8 (`1 ask`), 6 green flashes (`2 decide`), 2 amber rises (`3 read`), page to `docs/`, `+1 commit` (`4 publish`). |
| 10.0–13.0 | Poster returns and holds across the seam. |

**Bytes.** Camera still for the whole loop; the only whole-frame change is the 2.6 s wave
(measured: a still shot costs ~25–70 KB/s, a camera move ~1 MB/s lossy at 1280 × 720).

**Risks.** The amber wave is loud; it must read as *cost*, not celebration. A 3D plane may look like
a generic "data landscape" unless the pillars carry the meaning from the first frame.
