# Storyboard C: four sentences

**Concept.** There is no stage. The hero is the top of the pyramid set as type: the governing thought
large, and the four supports beneath it as the README's numbered list. Each support has a 44 × 28
glyph at its left margin, and in its turn that glyph performs the step in miniature. That is the
whole animation. It is the most legible and most restrained board, and it risks being "a list
with animated bullets".

**T = 34 s.** Canvas 830 × 360. Transparent background.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ Keep an agent-readable docs/ folder in sync with                                │  Geist SemiBold 30/38,
│ OneDrive, SharePoint, Outlook and Teams                                         │  broken on phrase
│ by processing only what changed.                                                │  boundaries
│                                                                                 │
│ [ ◖Δ◗ ]  1  Ask each source what changed, and make expiry cheap                 │  Geist 16/24,
│ [ •⋮⋮⋮ ]  2  Decide with three hashes before reading a byte                      │  numbers Mono muted
│ [ ┆▯┆ ]   3  Read bytes only on purpose                                          │
│ [ ─●─● ]  4  Publish a pure function of the source, in git                       │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Glyphs

1. **Token pill.** A neutral pill. It **expires**: it turns red and shows `410`. A dotted line then
   **walks** out of it at constant speed, and the pill recovers to neutral.
2. **Three gates.** Three short vertical ticks labelled `H0 H1 H2` in 8 px Mono. Five dots
   **travel** in from the left. Three **exit** green at H0 and one at H1, and one passes H2.
3. **Placeholder.** A dashed page outline. Amber **reads** up through it over 1.6 s and the outline
   turns solid. This is the only amber on screen.
4. **Git line.** Three dots on a line. A fourth is **written** at the right end, then the line
   slides left one spacing (a conveyor), so the glyph is self-similar at the seam.

## Beats

| t (s) | Key line | Glyphs |
|---|---|---|
| 0 – 3 | all bright | **Rest, the answer frame.** Glyphs static in their resting pose. |
| 3.0 | **focus 1** | |
| 3.5 – 4.0 | | Glyph 1 **expires**: red, `410`. |
| 4.5 – 6.5 | | Glyph 1 **walks**: a dotted line at constant speed. |
| 6.5 – 7.0 | | Glyph 1 recovers. |
| 9.5 | **focus 2** | |
| 10.0 – 11.5 | | Glyph 2: five dots **travel** in. |
| 11.5 – 13.5 | | Three **exit** green at H0, one at H1, and one passes H2. |
| 16.0 | **focus 3** | |
| 16.5 – 18.1 | | Glyph 3 **reads**: amber rises, and the outline turns solid. |
| 18.1 – 21.0 | | Hold amber. |
| 22.5 | **focus 4** | |
| 23.0 – 23.6 | | Glyph 4 **writes** a new dot. |
| 24.0 – 25.0 | | Glyph 4's line slides left one spacing. |
| 26.0 – 28.0 | | All glyphs return to rest. Glyph 3's amber drains. |
| 28.0 | all bright | |
| 28 – 34 | | **Rest** (the seam). |

## Checks

- **t = 0 and t = 3 s:** pure typography, the most legible of the three boards.
- **Amber:** 16.5 – 28.0 s, on one 20 × 26 glyph.
- **One motion at a time,** always.
- **Risk:** the motion is small and marginal. A visitor skimming may never see a glyph move, and
  "processing only what changed" is stated but never *felt*. The scale (thousands of files, one
  read) is absent.
