# Storyboard A: the field goes quiet

**Concept.** The organisation's knowledge is a field of 256 dim placeholder cells in four bands,
one per source. Over one cycle, eight cells light up as changed, five of them leave for free, two
are read (the only amber), and one becomes a commit. The scale is the message: amber touches 2 of
256 cells. The key line sits at the right as the README's numbered list, and the step being shown
stays bright while the others dim.

**T = 40 s.** Canvas 830 × 420. Transparent background.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ Keep an agent-readable docs/ folder in sync with OneDrive,                      │  Geist SemiBold 25/34
│ SharePoint, Outlook and Teams by processing only what changed.                  │
│                                                                                 │
│ OneDrive    ● ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆   1  Ask each source what changed,    │  cells 7×7 at a 10 px pitch,
│               ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆      and make expiry cheap            │  dashed = online-only
│ SharePoint  ● ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆   2  Decide with three hashes         │  placeholder
│               ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆      before reading a byte            │
│ Outlook     ● ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆   3  Read bytes only on purpose       │  key line: Geist 14/20,
│               ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆                                       │  numbers in Mono
│ Teams       ● ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆   4  Publish a pure function of       │
│               ┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆┆      the source, in git               │
│                                                                                 │
│ docs/ ──●────●────●────●                        bytes read  0 B                 │  git line + meter, Mono 12
└────────────────────────────────────────────────────────────────────────────────┘
```

## Beats

| t (s) | Key line | Stage |
|---|---|---|
| 0 – 3 | all bright | **Rest, the answer frame.** Every cell dashed and neutral, four token dots neutral, the git line holds four commits, and the meter reads `0 B`. |
| 3.0 | **focus 1** | |
| 3.0 – 4.0 | | **Ask.** The token dots brighten briefly in turn: OneDrive, SharePoint, Outlook, Teams, 0.25 s apart. |
| 4.0 – 4.5 | | **Ring.** Six cells ring: 2 OneDrive, 2 Outlook, 2 Teams. |
| 4.5 | | **Expire.** SharePoint's dot turns red, and a Mono `410` appears beside it. |
| 5.0 – 7.0 | | **Walk.** A 1 px neutral line crosses SharePoint's band at constant speed. Two cells ring as it passes them. |
| 7.0 – 7.5 | | SharePoint's dot recovers to neutral, and `410` fades. |
| 7.5 – 9.5 | | Hold. A Mono caption under the field: `8 of 256 changed · 0 B read`. |
| 10.0 | **focus 2** | |
| 10.5 – 12.5 | | **Exit ×5.** Five ringed cells get a green tick and drop their ring, staggered 0.3 s apart. Caption: `H0 equal → 5 unchanged · 0 B read`. |
| 12.5 – 15.5 | | Hold. Three cells stay ringed, and the meter still reads `0 B`. |
| 16.0 | **focus 3** | |
| 16.0 – 17.6 | | **Read.** Amber rises through the first cell and its outline turns solid. |
| 17.6 – 19.2 | | **Read.** The second cell does the same. The meter turns amber: `2 of 256 read`. |
| 19.5 – 20.5 | | **Exit.** The second cell's amber drains, and it gets a green tick. Caption: `H1 equal → no-op save`. |
| 20.5 – 24.5 | | Hold. |
| 25.0 | **focus 4** | |
| 25.0 – 25.5 | | **Travel.** A copy of the remaining amber cell moves down to the git line. |
| 25.5 – 26.1 | | **Write.** The git line extends and a fifth commit dot appears: `docs/mirror  +1 page`. |
| 26.5 – 33.0 | | Hold on the outcome: 256 cells, one commit. |
| 33.0 – 35.5 | | **Reset.** The rings, ticks and amber fade out. The git line slides left by one commit spacing, so the oldest dot leaves and the new one takes its place. That makes the frame identical to t = 0: a conveyor where every cycle adds one commit. |
| 35.5 | all bright | |
| 36 – 40 | | **Rest** (the seam). |

## Checks

- **t = 0 and t = 3 s:** full headline, full key line, a quiet field. The answer is legible and
  nothing has moved yet.
- **Amber:** 16.0 – 33.0 s, on 2 of 256 cells. The meter is the only other amber element.
- **At most 3 simultaneous motions.**
- **Seam:** reset ends at 35.5 s, followed by 4.5 s of rest. The conveyor shift makes the git line
  self-similar.
- **Risk:** 256 dashed 7 px cells can shimmer at 1× and become noise on a phone (about 3 px each).
  They may need to be solid, low-contrast cells, with "dashed" reserved for the cells in focus.
