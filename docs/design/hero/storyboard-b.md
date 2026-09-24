# Storyboard B: the ledger

**Concept.** The design's heart is a manifest row with three hash slots. The hero is that
manifest, four rows long. Frame 0 shows the **last** sync's finished ledger, which is a complete
static explanation: every row's outcome is visible, and most outcomes are free. The loop then runs
the **next** sync: outcome cells clear, and the columns fill left to right, one step at a time.
At T the ledger is finished again. The byte meter at the foot is the protagonist: it reads `0 B`
through two whole steps.

**T = 36 s.** Canvas 830 × 400. Transparent background.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ Keep an agent-readable docs/ folder in sync with OneDrive,                      │  Geist SemiBold 25/34
│ SharePoint, Outlook and Teams by processing only what changed.                  │
│                                                                                 │
│ 1  Ask each source what changed,  │ since  object          H0  H1  H2           │  ledger: Geist Mono 12.5,
│    and make expiry cheap          │ ●      roadmap.docx    =           unchanged │  header muted
│ 2  Decide with three hashes       │ ●      pricing.xlsx    ≠   =       no-op save│
│    before reading a byte          │ ●      Re: renewal     ≠   ≠   ≠   docs/ +1  │
│ 3  Read bytes only on purpose     │ ●      #launch         =           unchanged │
│ 4  Publish a pure function of     │ ─────────────────────────────────────────── │
│    the source, in git             │ bytes read  0 B              git  +1 page    │
└────────────────────────────────────────────────────────────────────────────────┘
```

Rows are sources in order (OneDrive, SharePoint, Outlook, Teams). A row's outcome cell carries the
cost colour: green for `unchanged` and `no-op save`, and neutral-bright for `docs/ +1`.

## Beats

| t (s) | Key line | Ledger |
|---|---|---|
| 0 – 3 | all bright | **Rest, the answer frame.** The last sync's finished ledger. |
| 3.0 – 4.0 | | **New cycle.** The hash and outcome cells fade out, leaving object names and token dots. |
| 4.0 | **focus 1** | |
| 4.0 – 5.0 | | **Ask.** The token dots brighten in turn, 0.25 s apart. The rows for OneDrive, Outlook and Teams ring. |
| 5.0 | | **Expire.** SharePoint's dot turns red with a Mono `410`. |
| 5.5 – 7.5 | | **Walk.** A neutral line crosses SharePoint's row at constant speed, and the row rings when it arrives. |
| 7.5 – 8.0 | | The dot recovers. |
| 10.0 | **focus 2** | |
| 10.0 – 11.0 | | The H0 column fills: `=`, `≠`, `≠`, `=`. |
| 11.0 – 12.0 | | **Exit ×2.** roadmap.docx and #launch get green `unchanged` and dim to 55 %. |
| 12.0 – 16.0 | | Hold. The meter is still `0 B`, and its underline is written once to draw the eye. |
| 16.0 | **focus 3** | |
| 16.0 – 17.6 | | **Read.** An amber bar grows under pricing.xlsx. |
| 17.6 – 19.2 | | **Read.** An amber bar grows under Re: renewal. The meter turns amber: `2 objects`. |
| 19.5 – 20.5 | | H1 fills with `=` and `≠`. **Exit:** pricing.xlsx gets green `no-op save`, its amber bar drains, and it dims. |
| 23.0 | **focus 4** | |
| 23.0 – 24.0 | | H2 fills with `≠` for Re: renewal. |
| 24.0 – 24.6 | | **Write.** `docs/ +1` is written in, and `git +1 page` draws at the foot. |
| 24.6 – 30.0 | | Hold. |
| 30.0 – 32.0 | | **Reset to the answer frame.** Dimmed rows return to full, the amber meter crossfades back to `0 B`, and the amber bars drain. |
| 32.0 | all bright | |
| 32 – 36 | | **Rest** (the seam). The final frame equals frame 0 by construction, since frame 0 IS the finished ledger. |

## Checks

- **t = 0:** a complete, self-explaining table, and the answer.
- **t = 3 s:** it begins clearing. For about 5 s, less information is on screen than at t = 0. That
  is this board's main risk to answer-first legibility.
- **Amber:** 16.0 – 32.0 s, on two bars and the meter.
- **At most 2 simultaneous motions.**
- **Risk:** it is text-heavy. It is roughly 60 extra Mono glyphs, and at phone width
  (0.43×) 12.5 px Mono becomes about 5 px. It reads as a spreadsheet, which is precise but not
  memorable.
