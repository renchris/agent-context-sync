---
status: open
---

# Plan — meeting-video understanding: recordings into evidence, meeting pages, speech and names, every platform

Scope (frozen): a meeting recording that reaches the Mac (local or online-only) becomes a deterministic, on-device
evidence package (on-screen text, keyframes, speech, voices named where that is accurate) that the agent reads and
curates into a cited meeting page, on every platform that has a real example. Built on `main` per
[`docs/design/meeting-video-spec.md`](../design/meeting-video-spec.md): the acceptance harness first, four complete
phases, each its own dispatched session with an adversarial review before it lands, then a field check on the
corporate Mac. Nothing in the repo names the tenant, a participant, or what was said.

Research inputs stay outside the repository and are cited by file name: `v2-spec.md` (the reviewed spec, now the body
of the build spec), `v3-synthesis.md` (speaker names and platforms, changes C1 to C19), `v3-*.md`,
`decisions/d1..d6` and `decisions/skeptic.md`. The test recording (one real Teams recording, about 2 h 06 m) stays on
one Mac; its counts may be cited, never its content.

## Phase 0 — Agent Team Orchestration

- **Execution locus per wave:** H, P1, P2, P3 and P4 are each **S**, a dispatched session
  (`~/.claude/scripts/handoff-fire.sh --prompt-file <brief> --worktree <branch> --notify-back <lead> --goal '…'`)
  that leads its own Agent Team and runs its own adversarial review before landing. F is **L** for the lead's part:
  the build runs on the corporate Mac through the bring-back loop, and the lead only triages the returned file, as in
  [`bring-back-ocr.md`](bring-back-ocr.md) B7 and B8.
- **Task size per unit:** one task per teammate, 40 to 150K output tokens (about 30 to 75 minutes, 3 to 6 files);
  never below 20K; split at 500 lines of new code or 10 files. The rosters below are written to that band.
- **Rosters (inside each dispatched session):**
  - H: one teammate (harness scripts and tests). Small; may be the session itself.
  - P1: four teammates. *media* (`media_frames.swift`, `media.py`, install and doctor lines); *recording*
    (`recording.py` S3 to S6, S9, the profiles); *cycle* (`cycle.py` recording pass, downloads, pieces, `loop.py`
    notes); *publish* (piece store in the cache, `publish.py`, `lints.py`, `governance.py`, CONTRACTS, README). The
    *recording* teammate is the one most likely to pass 500 lines; split S9 render into a fifth if the brief says so.
  - P2: two teammates. *lint* (`curate.py`, `cli.py`); *skill* (`skill.py`, `publish.py` rubrics, CONTRACTS).
  - P3: four teammates. *speech* (speech helper, `speech.py`, the pinned FluidAudio build); *cue* (`pills`, S7 per
    profile); *naming* (`naming.py`, S8b, its options and version mark); *vtt* (`vtt.py`, `text.py:67`).
  - P4: one teammate per platform profile whose example has arrived, plus one for the section 13 hard cases.
  - Each session adds fresh-context reviewers after its teammates merge (a skeptic per finding), then a fix pass.
- **Dependency graph:** H blocks everything. P1 and P2 both need only H (P2 depends on P1's grammar, which H pins in
  `tests/test_recording_grammar.py`), so they run side by side. P3 needs P1. P4 needs P1 and P3 and the operator's
  recordings. F needs P1 to P4 landed; an early F run after P1 is allowed for R2 and R3 only.
- **Worktrees and branches:** `mv/h-harness`, `mv/p1-screens`, `mv/p2-meeting-page`, `mv/p3-speech-names`,
  `mv/p4-platforms`, each under `../.worktrees/`. Land smallest diff first; rebase the other.
- **Shared files, one owner per wave:** `docs/design/CONTRACTS.md` sections are numbered here so appends do not
  collide: P1 §16.34 (§16.29 and §16.30 went to the bring-back waves before P1 landed), P2 §16.31, P3 §16.35
  (§16.32 went to the v9 rehearsal before P3 landed), P4 §16.33.
  `cycle.py`: P1 owns the recording pass; P3 touches only the speech identity in `_capabilities`. `README.md`: each
  wave owns its own subsection. `config.py`: P1 only.
- **Spawn order:** H → (P1 ∥ P2) → P3 → P4 → F.
- **Every wave:** the gate in `CLAUDE.md` § Gate; `/ship`; CI read back green. Before every commit the staged diff is
  checked with `~/.cache/agentsync-meeting-video/denycheck.sh <files>`, which prints counts only and must print
  `total=0`. Tests use Contoso and made-up people. No fixture recording is committed.
- **Lead context budget:** the lead keeps at least 50 % of its window for deciding; wave output lands in the child
  sessions and returns as one ping. **Succession point:** the lead recycles after each wave lands, with this plan as
  the bridge.

## Decisions

### Operator rulings, 2026-10-07

They override the v2 spec wherever they disagree. The spec's section 0.0 and 15.4 show where each is applied.

| # | Ruling | What the build does |
|---|---|---|
| R1 | **Reversed: download online-only recordings.** "Online-only" is the OneDrive Files On-Demand placeholder of the same file | A download allowance separate from the 1 GiB document budget: one recording per cycle, at most 4 GiB, newest first, only in the reconcile job or `materialise PATH`, a deadline of 60 s + size / 500,000 B/s, and 2 x size + 5 GiB of free disk. A download that fails with `errno 89`, is refused, or passes its deadline becomes a counted `note:` telling the person to choose "Always Keep on This Device" in Finder. Never rule 3. Spec S0 rule 3 |
| R2 | **Reversed: convert recordings under a `[policy]` label rule.** Processing stays local | The converter stays registered; the rule adds a `status` clause and an index `NOTE` only. D8 ([`bring-back-ocr.md:47`](bring-back-ocr.md)) still refuses images; recordings depart from it on purpose, and the spec's section 6 states why (`d2`'s facts stand, so the note says the label was not checked) |
| R3 | **Keyframes and intermediate output may go into the local docs repo**; faces are no longer a blocker | Every non-revisit state keeps K1 and K2: the content crop for a recognised share layout, the full frame for camera, gallery and unrecognised layouts. About 58 MB for the test recording; 2.3 / 9.4 GB live in HEAD at 5 / 20 one-hour meetings a week (spec 3.6) |
| R4 | Accepted as recommended (`d4`) | Never hold the sync; resumable pieces in a piece store; "N of M minutes" note; a time-out leaves the recording waiting |
| R5 | Accepted as recommended | Only background sync and `agentsync materialise <file>` read recordings; an interactive `sync` reads none and prints the count |
| R6 | **Changed: name speakers when it can be done accurately** | `v3` section A: community-1 diarizer at FluidAudio `04e363c` or later; S8b's stream rule, turn veto and margin band; closed-set names only through the one-person check; a per-platform gate before a platform's names ship |
| R7 | Accepted as recommended (`d5`) | Parakeet via FluidAudio in P3, off until the operator places the weights (483 MB and 22 MB); a `.vtt` leads wording; on-screen text wins spellings |
| R8 | **Changed: every platform, each made reliable from a real example** | `v3` section B: readiness tiers; profiles from the bytes only, never the file name; a profile lands with its example. Unrecognised layouts: full frames plus on-screen text |

### Lead decisions, 2026-10-07

| # | Decision | Why |
|---|---|---|
| L1 | `[convert] recordings` defaults **on**; the key turns it off. | The operator's rule is to capture everything relevant. The limit constants ship from M1 estimates and are re-set in F (R3). |
| L2 | No 24-per-window keyframe cap and no per-recording cap of their own; `_MAX_READS` (840) bounds them. | Ruling 3 lifts the storage objection; every keyframe is a read candidate, so the count limit already bounds a recording at about 212 MB worst case. |
| L3 | Keyframes stay JPEG 0.7 through ImageIO. | WebP would be a third of the bytes but needs Pillow (a product non-goal); ImageIO cannot write WebP (`d3` 7.1). |
| L4 | Intermediate output: commit the keyframes and the `full-text.txt` sidecar only; the piece store stays in `<cache_dir>/recordings/`. | Ruling 3 permits more; a piece is reproducible, not evidence, and `purge` and `gc` already own the cache folder. |
| L5 | Frame selector: the 2 s content-region pixel gate, then OCR. | Measured on four Teams recordings (spec S3). |
| L6 | A second Swift helper (`media_frames.swift`), built, trusted and probed like the OCR helper. | AVFoundation is not in the OCR helper's job; one switch, two helpers, as the spec's section 11 says. |
| L7 | Piece length: 5 minutes of the picture track; decode, speech and voices whole-file. | `d4`: time-cut speech changes 3.9 to 4.6 % of words; picture pieces with carried state were byte-identical to one pass. |
| L8 | Build order: H, P1, P2 (beside P1), P3 (speech, cue, S8b and `.vtt`), P4 (platforms and hard cases), F. | The agreed order. v2's P2b, P3 and P4 fold into P3; its P5 into P4. |
| L9 | Background downloads only in the reconcile job and `materialise PATH`; pieces in any background cycle under 180 s of recording work. | A poll job's watchdog is 1,800 s; a 2 h recording at 5 Mbit/s downloads in 26 minutes (`d1` option B). |

### Ruling pending: `v3` section D

Each runs on its recommendation as the working default until the operator rules. Spec section 11, O18 to O22.

| # | Question | Working default | Conviction |
|---|---|---|---|
| D1 (O18) | May agentsync enroll voiceprints (the operator's own, or colleagues' with opt-in) to name people? | Not in this build; later at most the operator's own voice, opt-in. Consent of the person identified governs, not where the model runs | 70 % |
| D2 (O19) | On shared audio, print the account owner's label beside other people's voices, or leave it out? | Print it as `voice N, on shared audio of <label>`; lint `CITE-SHARED` warns on a name beside someone else's words | 65 % |
| D3 (O20) | VP9 and AV1 pictures: a speech-only page plus H.264 copies, or a decoder dependency such as ffmpeg? | Speech-only page plus H.264 copies; re-test on the corporate M4 Max first | 60 % |
| D4 (O21) | Will the operator ask organizers for the `.vtt` and attendance `.csv` of meetings that matter, starting with the test recording, and do a 10-minute listen? | Yes | 80 % |
| D5 (O22) | Keep the 27 public YouTube and Loom excerpts as local test fixtures? | Local only, never committed, deleted after the build | 70 % |

#### Research pass, 2026-10-08 (researcher plus skeptic per decision)

Still pending the operator; these replace the working defaults above. Reports (local, outside the repo):
`~/.claude/research-artifacts/meeting-video-2026-10-06/decisions-v3/d1..d5-*.md` and their `-skeptic.md` files.

| # | Working default now | Conviction | What would flip it |
|---|---|---|---|
| D1 | No enrollment and no voice vector. "You" is named by a non-biometric `voice N is me` basis the operator confirms per recording, and on Teams by the operator's own Teams voice profile. A local owner template is at most a P4 fallback, off by default, needing a fixed cosine threshold of 0.62 or more plus a runner-up margin, a self-test on 10+ of the operator's recordings with 0 wrong-"you" clusters, and the employer privacy owner's yes. Colleagues are never enrolled | 78 % | The operator will not confirm `voice N is me` and a third or more of their speaking time stays unnamed |
| D2 | Keep the label. A 3-run-per-arm blind-reader test gave 0 of 18 owner attributions with the label and 0 of 18 without, and readers without it still took the owner's name from the SPEAKING lines. P3's index "How to read" line carries "never means X spoke"; lint rule 8 becomes label-independent: no person named on a shared, mixed or unidentified basis, and every action-item Owner needs a People row with a naming basis | 80 % | A 30-run-per-arm curation test showing the label raises owner attribution by 10 points or more |
| D3 | Neither option as posed: the media helper registers VideoToolbox's supplemental VP9 and AV1 decoders (VP9 then decoded 4 of 4 files on the M1 Max, frame-identical to a software decode; AV1 0 of 2 there) and stubs a recording only when one trial frame fails to decode (`isDecodable` lies for 4:4:4 VP9). What still fails gets the speech-only page with H.264-copy advice. Time a 4K VP9 file in R3 | 85 % | The corporate M4 Max cannot decode a 1080p VP9 MP4 frame after registration from the background job |
| D4 | Yes, cheapest first: check the recording's Stream page for a transcript (2 min); listen to a machine-cut clip kit aimed at the risky V1 segments, with 5 known-Presenter control clips and a longer re-play for every "different" vote; ask the organizer "who spoke from your laptop?", adding the `.vtt` only if Stream had none. If no transcript exists, measure R14 and R28 in a Teams meeting the operator organizes | 82 % | The operator calls 2 or more control clips "different" after the re-play: drop the listen and hold names until B.3 item 3 |
| D5 | Keep, local only, never on the corporate Mac or anywhere synced or pushed, deleted after P4. Done 2026-10-08: the research scratch (fixtures, `gt.py`, the v2 Teams probe inputs) moved out of `/tmp` (deleted there after about 96 h) to `~/.cache/agentsync-meeting-video/research-2026-10-06/`, linked from the old path and excluded from Time Machine. F's R30 uses open-licensed VP9 and AV1 files | 75 % | A need to carry fixtures off this Mac, or a rights-holder notice: then replace with the open-licensed matches plus the operator's recordings |

### Recordings the operator supplies (`v3` B.3)

All stay local and are never committed. For each, the operator writes down the order in which people spoke.

| # | Recording | Needed by |
|---|---|---|
| 1 | The test recording's Teams `.vtt` and attendance `.csv` (organizer or co-organizer download) | P3 naming gate (R12, R28); P3 `vtt-turns` fixture shape (R14); names for "+N" people |
| 2 | A 10-minute listen to the test recording: 5 lines per voice, counts only | P3 naming gate on Teams (R12) |
| 3 | Teams, the operator's tenant, about 10 minutes: scripted speaker order, 4+ people on own accounts, 2 on one laptop, a Teams Room if any; a window share, PowerPoint Live, a slide with under 5 lines, a spotlighted camera; the `.vtt`, `.csv` and invite `.eml` | P3 S8b on a second recording; P4 T3 on special shares; R4, R12, R13, R26 |
| 4 | Teams, about 5 minutes: Together mode, large gallery, standout presenter mode | P4 Teams special modes |
| 5 | Zoom cloud recording, 5 to 10 minutes, its `.vtt`; if allowed a local recording with per-participant audio | P4 `zoom` profile and names |
| 6 | Google Meet, 5 to 10 minutes, both layouts, transcript Doc as `.docx` | P4 `meet` names (screens ship in P1) |
| 7 | Webex, recorded by the Webex app, 5 to 10 minutes, a grid and a share, its `.vtt` | P4 `webex` profile |
| 8 | One conference talk as received, with where it came from; an H.264 copy if from YouTube | P4 filmed-slides rule; D3 |
| opt. | A 2-minute phone video of a meeting screen; one Loom MP4 | P4 filmed-screen state; `loom` profile |

## Phases

Line targets are on `main` at `4e65376`. The spec cites the repo at `02ee347`; every cited symbol still exists.
Unchanged since then: `convert/`, `curate.py`, `cli.py`, `publish.py`, `lints.py`, `policy.py`, `governance.py`,
`skill.py`, `config.py`, `ops/launchd.py`, `README.md`, `tests/`. Shifted: `cycle.py` (+5 near the top, +13 near
line 1000, +105 to +116 past line 1800), `loop.py` (+12, then +75 to +85), `manifest.py` (+20), `CONTRACTS.md` (+8,
then +9), `setup_report.py` (+1,466), `scripts/install.sh` (+21). Re-check each target with `grep -n` before editing.

### H — Acceptance harness (done, 2026-10-07)

Shipped the checks before the code they check (spec section 10, row H). Commits: `7cbb5a8` (harness), `9f12dfd`
(fixes from the fresh-context adversarial review: 9 findings, every one fixed or written down below).

- **What landed.** `tests/media_kit.py` (`fake_media(folder, script)`, `fake_recording`, `recording`, `screen`,
  `row`, `grids`, `calls`); `tests/test_recording_grammar.py` (`window_errors`, `index_errors`, which P1 imports for
  `test_every_line_is_the_banner_the_title_a_heading_a_tagged_line_or_a_footer`); `scripts/meeting-eval/score.py`,
  `layout.py`, `run.py`; their tests in `tests/test_media_kit.py` and `tests/test_meeting_eval.py`.
- **Proof.** The two H test files: 0 failed. `score.py <fixture> --marks <VERDICT.md>` on both v2 Zoom fixtures:
  B 16.5 and 16.5, B SPEECH 6 of 6, B SCREEN plus CROSS 10.5, B minus A +7.5 and +9.5, C minus B +1.5 and +1.5,
  wrong and confident 0, B quotes all found (51 of 51, 55 of 55 pieces of 4 characters or more), B lenient
  citation 18 of 18 on both;
  strict citation A 9, B 15, C 16 on the slide call, as its verdict counted. Exit 0: every 9.2 mark holds.
- **Media helper protocol, pinned for P1's `media_frames.swift`.** `info FILE`; `scan FILE --out DIR [--step-ms]
  [--max-ticks]` writing `grids.bin` (57,600 B a tick); `frames FILE --out DIR --ticks a,b [--crop X0,Y0,X1,Y1 |
  --crop-right F]` writing `tHHMMSS.jpg`; `diff GRIDS --pairs a:b --include R --exclude R [--threshold]`; `pills
  FILE --request REQ.json`; `audio FILE --out DIR` (16 kHz mono s16le). One JSON document out, exit 3 on failure. A
  grid cell is inside a mask rectangle when any part of it is (`teams` content mask: 280 of 320 columns).
- **Grammar choices the spec left open, now pinned.** Window units are also held to S9 rules 1, 2 and 4 (time
  order with the equal-time tag order, every line inside its window and its state, the continuation `NOTE` on a
  state begun earlier), S6 rule 8 (a revisit prints no `SCREEN` row and its `KEYFRAME` names the revisited state)
  and S10 (sorted footer, one per keyframe, `full-text.txt` on a cut page). The index has one `## <block>` per 3.5
  block in 3.5's order; Facts are `- <key>: <value>` lines with fixed keys; How to read and Gaps and bounds are
  `- ` lines; the three count tables have fixed columns and per-column cell patterns, so picture text cannot sit
  in a cell. `VOICE` forms follow the 3.3 tag table (`v3 · shared audio of <label>, k voices`), not the wording
  of S8b rule 4; P3 changes both together if it changes one.
- **Scorer rules.** Correctness is the judge's (marks JSON, or the verdict's per-question table in either v2
  layout); citation, quotes and wrong-and-confident are computed. Lenient citation reads every time, range and
  `NNNNN.jpg` frame (1 fps) named in `verification` when gold has no `times` list; new fixtures should carry
  `times`. The "three known B misses" row stays a judge's reading.
- **Layout scorer.** Ticks are grouped by the platform the excerpt was recorded on (`PLATFORM` in `gt.py`, else
  the name's first word), never by the detected profile, so a Teams recording taken for `generic` still meets
  95 %; a labelled excerpt without a prediction fails the run (9.2 requires `oct`). The labels file is parsed
  (`ast`, literals, `+` and `*` only), never executed.
- **Learnings, open for the spec.** (1) 9.2's "Quoted pieces found, B" mixes units (the slide call's verdict
  counted answers, 18 of 18; the demo call's counted pieces, 56 of 56); the scorer counts pieces of 4 characters
  or more and requires every credited answer to quote. (2) The v2 gold carries one `time`; B's strict citation
  on the slide call is 15, below the 17 mark, so the lenient rule decides that row, and it accepts times the
  verification names as distractors. The scorer warns; P1 should add a `times` list to both gold files before
  9.2 is used as a gate. (3) Spec S8b rule 4 (`voice N, on shared audio of <label>`) disagrees with the 3.3 tag
  table (`vN · shared audio of <label>, k voices`); the grammar follows the table, and P3 settles both.

### P1 — Screens (done, 2026-10-08)

Spec S0 to S6, S9, S10, 3.1 to 3.6, 4, 4.1, 6; tests 9.3. A lead-written interface skeleton (`MediaEngine`,
`PieceStore`, `RecordingNotFinished`, `work_allowance`, the `Reading` model, `render`), then six teammates (*media*,
*recording*, *render*, *cycle*, *loop*, *publish*), two fresh-context reviewers (the recording pass; the grammar
against forged screen text), two fix teammates and two lead fixes found by the 9.2 run. Commits: `git log --oneline
--grep '(recording)\|(media)\|(cycle)\|(loop)\|(pieces)\|(meeting-eval)'` over the P1 land.

- **What landed.** `convert/media_frames.swift` + `convert/media.py` (AVFoundation helper: `info`, `scan
  --first-tick`, `frames`, `diff`; built, trusted, probed and pruned through the OCR plumbing, now shared as
  `ocr.Helper`); `convert/recording.py` (`recording-av`, S1 to S6, 5-minute pieces, `work_allowance`);
  `convert/recording_page.py` (S9 and the index); `convert/pieces.py` (`<cache_dir>/recordings/<sha>/`); `ocr.OcrRow`
  and `ocr.text_rows`; the recording pass, downloads and `RECORDING_WAITS` / `RECORDING_PROGRESS_META` in
  `cycle.py`; the waiting and Finder notes in `loop.py`; the `status` label clause; text-only secret and token
  lints; `*.jpg binary`; purge of pieces; doctor `media` line; installer build line; `[convert] recordings`
  (default on); `UnitKind.WINDOW`; `scripts/meeting-eval/splice.py`; CONTRACTS §16.34; README section.
- **Proof.** The gate in `CLAUDE.md` § Gate green; 9.2 on both Zoom fixtures through `scripts/meeting-eval/`
  (convert with the real helpers, `splice.py` with the experiment's transcript, three blind readers, a judge,
  `score.py`), every mark held: k8s1080 B 16.5, B SCREEN plus CROSS 10.5, B minus A +8, C minus B +0.5, B citation
  17, wrong and confident 0; jup1080 B 16, 10, +9, +0, 18, 0. Score sheets and reader runs:
  `~/.cache/agentsync-meeting-video/eval-2026-10-07/` (outside the repo).
- **Spec items from H, settled.** 9.2 quote units (pieces of 4 or more characters, every credited answer quotes)
  and the gold `times` lists (added to both fixtures, distractors left out) are in the spec under 9.2.
- **Choices the spec left open or got wrong (CONTRACTS §16.34).**
  - The 9.2 run on the demo call failed first (B 15, wrong and confident 1). Two causes, both fixed with tests:
    under `generic` a scrolled desktop fails R4's 5 % change test and reads as camera, so the camera back-off read
    it every 10 s; a camera read that still holds 2 or more long rows now goes under the motion back-off (S3 rule
    5; selection revision `-s2`). And S6 rule 4's place condition made every row of a scrolled page a new 2 s row
    the 4 s rule dropped; a row whose normalised text is held once in its region at both reads now continues
    wherever it moved. A row also carries across a change of kind when it prints the same way.
  - Grammar, after the forged-text review: the index needs a blank line after each table (a label line under the
    Windows table rendered as a table row in GFM); `TEXT` and `LABEL` exclude C1, lone surrogates and Unicode Cf.
    The renderer escapes every `<` as `&lt;` and `![` as `!\[`, applies NFC before any cut, drops Cf, tag and
    filler characters, rewrites a forged trailing `[?]` to `(?)`. P2's lint and skill pass unchanged.
  - Recording-pass review: an unfinished read's hash is never taken as its page's; the stopped-twice count is per
    piece; the staged hash is kept in the recording meta for prune and purge; the allowance is charged for the
    info call, the settle and a Graph recording's whole read; `agentsync reconcile` and `accept-deletions` read and
    download no recording (only `sync --mode` and `materialise PATH` do); a Graph recording is read to the end in
    the run that downloads it.
  - Undefined terms taken from `v3-platform-layouts` §5 (pane edge, static pane, long line); "name-like" is 1 to 4
    capitalised words. A revisit also needs matching text. A gallery keeps a full frame (ruling 3), although the
    9.3 test name says "stores no image".
- **Items for later waves.**
  - P3: the Voices table needs the blank line too (the grammar already requires it); settle the `VOICE` wording
    (H learning 3).
  - P4 / F: a new version of a recording that is already online-only when the walk first sees it keeps the old
    page until it is downloaded in Finder (`classifier.py` settles online-only files as DATALESS; the manifest keeps
    the old stat). Text volume on a scrolling demo is about 4 times the experiment's (593 KB against 146 KB of
    screen text, R5); a row read at one tick only is still dropped by the 4 s rule (the demo call's `NameError`
    line). Pieces passed their deadline at load averages near 100 and resumed; R3 re-sets the limit constants.
  - P2's drift test reads the tag set from the grammar, which the converter is held to page by page
    (`test_every_line_is_the_banner_the_title_a_heading_a_tagged_line_or_a_footer`), so it stays as it is.

### P2 — Meeting page skill, rubrics, citation lint (done, 2026-10-07)

Spec 7.1 to 7.4, section 8, the C11 basis forms. Two teammates (*lint*, *skill*), then three fresh-context
reviewers (the skill as a reader sees it, the lint's correctness, its robustness) and two fix passes.
Commits: `ebf21b0`, `555d5ca` (skill, rubrics, §16.31); `a99a017`, `6d8175a`, `f5d96d4` (lint); `ff7812f`, `7b4330f`,
`6765532`, `6ee63bf` (review fixes).

- **What landed.** `skill.MEETINGS`, step 7 of `procedure()` (the skill, the root CLAUDE.md and AGENTS.md), plus a
  `CITE-*` row in step 6. `publish.RUBRICS`: `_rubrics/meeting-page.md` (the 7.1 template) and the four 7.3
  sweeps, written by `ensure_scaffold` as fixed files; `gitops.COMMIT_PATHSPECS` gains `_rubrics`.
  `curate.lint_meeting_citations` and `curate.CITE_CODES`, run only by `agentsync curate` as `warn` lines, never
  from `generate_depends`, `checkpoint_blockers` or the sync. CONTRACTS §16.31.
- **Proof.** `tests/test_curate.py` has one test per 7.4 rule, each with a passing and a failing page, plus the five
  named 9.3 tests and a hostile-input test. A 100 KB row lints in under 1 s. The drift test
  `test_the_skill_names_every_tag_the_converter_emits_and_no_other` reads the tag set from the pinned grammar
  (`tests/test_recording_grammar.py`), because the converter is P1's.
- **Choices the spec left open (CONTRACTS §16.31 lists them all).**
  - A `heard` tag falls back only to a page whose `converter` is `vtt-turns@…`. Before this, a forged `SAID` line
    in any mirror page made an uncited decision pass.
  - Rule 5 accepts `chat` or `file` with a quote, and an action row whose evidence is `inferred` plus an existing
    `D<n>`, as the action-item rubric asks.
  - Rule 6 takes the last keyframe at or before the time, across a continuation.
  - The skill adds TERM and NOTE to spec 8's block, case-insensitive channel searches, numbers spoken as words,
    the revisit keyframe path, the window number as the key, and the one-person check written out.
- **Items for later waves.**
  - P1: the converter must emit exactly the grammar's tags, or the drift test and the lint drift with it. Retarget
    the drift test at the converter's own tag list once `convert/recording.py` lands.
  - P3: `vtt.py` must use the converter id `vtt-turns`, or `heard` tags stop resolving against transcripts. Settle
    the `VOICE` wording (H learning 3) in the skill too.
  - F / R20: measure how often the lint fails a correct citation before any `CITE-*` becomes an ERROR.

### P3 — Speech plus voice naming S8b (done, 2026-10-08)

Spec S7, S8, S8b, section 5, the `.vtt` turns, C15's speech-only page. A lead skeleton (`3745cb7`: slots in
CONTRACTS §16.35 and the test file), then wave A of five teammates (*speech*, *cue*, *naming*, *lines*, *vtt*), the
`Reading` interface (`d4bdd5b`), wave B (*render*, then *wire* once *cue* landed), three fresh-context reviews
(privacy of the modules, privacy of the wiring, determinism) and lead fixes. Commits `3745cb7` to `52a5007`
(32, `git log --oneline c7bfdb8..52a5007`).

- **What landed.** `convert/speech.py` + `convert/speech_helper/` (SwiftPM, FluidAudio pinned at `04e363c29d9a`; words
  and voices; offline mode; models only from `<cache_dir>/speech/`, digests pinned; build, probe, doctor `speech:`
  line, installer line); `convert/speech_lines.py` (S8: silence map, holes and splice, voice of a word, `SAID`
  lines); `convert/cue.py` + `media_frames.swift` 1.1.0 `pills` and `audio` (S7, `teams` only); `convert/naming.py`
  (S8b, gate `CHECKED_PROFILES` empty); `convert/vtt.py` (`vtt-turns`, `.vtt` out of `text-plain`); the wiring in
  `recording.py` (speech is one stored piece; S7 in the last pass; speech-only pages for no picture and VP9/AV1;
  `without_speech` so a SpeechError keeps the screens); the renderer's `SAID`, `SPEAKING`, Voices block and notes;
  README "Placing the speech models"; CONTRACTS §16.35.
- **Proof.** The goal command (`pytest tests/test_voice_naming.py tests/test_convert_recording.py
  tests/test_convert_formats.py`): 305 passed, 0 failed; `test_speech_is_deterministic_on_a_fixed_wav` passes (seeded
  PCM, two cold trees and three allowance cycles byte-identical; the reviewer repeated it under three hash seeds).
  A real helper build (offline, against the local `04e363c` checkout) gave byte-identical `words` and `voices` twice
  on a two-voice clip, each turn on the right voice, and exit 3 with no download when a model folder was missing.
- **Choices and corrections (CONTRACTS §16.35 lists them).**
  - VOICE wording settled (H learning 3): the 3.3 forms stay (`vN · shared audio of <label>, k voices`); the curated
    basis stays `voice N, on shared audio of <label>`. New fixed NOTEs: the naming gate, the turn veto (a window
    NOTE after the vetoed `SAID` line, since `SAID vN` lines never change) and the two `picture not read` wordings.
  - S8b: only the voice that holds `s(L)` can take `L` (a literal rule 2 let a minor voice take a one-voice label);
    shares compared as exact fractions. Speech-only windows have no `## ` heading (grammar and spec 3.3 changed).
  - Existing `.vtt` pages are read again once: `VttConverter.replaces = ("text-plain",)` and an `outdated_key` that
    moves the re-read record's capability digest (the version alone never would).
  - A pills failure is the S2 path (no page without the cue); spec S7 Failure asked for a page under a version
    without the cue mark. `_EMITTER_VERSION` 1.1.0, floor 1.0.0; every P1 page is read again once for `+cue-`.
  - Apple's AAC decode is not byte-stable (8 runs of a 10-minute file all differed, ±1 LSB): the helper resamples
    itself, nothing hashes `audio.pcm`, and the speech piece is the one result (R11 row added to spec section 4).
  - Reviews: FluidAudio's `[Profiling]` stderr lines silenced (they could reach a SpeechError text); the speech
    identity gained `-h<helper version>`, the speech piece key the media identity and the diarizer threshold; a
    test clip maker that deadlocked AVAssetWriter under load now feeds each input on its own queue.
- **Items for later waves.**
  - **Naming gate:** add `teams` to `naming.CHECKED_PROFILES` only after B.3 items 1 and 2 show 0 names the listen
    contradicts; each P4 platform the same way with its recording.
  - P4: `meet`'s cue lands with the Meet example (B.3 item 6); Zoom/Webex `Name: text` transcript lines without
    `<v>` tags are not read as names (R14); the index byte budget is tight with many voices (an hour with 12
    named voices and 30 quiet stretches is 8,679 of 9,000 bytes).
  - F: build the speech helper on the corporate Mac (R8; the SwiftPM fetch passes no proxy variables and relies on
    `~/.gitconfig`); check the placed model folders match the pinned digests; R11 on a real recording (does the
    decode noise change a transcript); the Core ML cache in `~/Library/Caches/agentsync-speech/` (57 MB) sits
    outside `cache_dir`, so purge and gc never see it; holes before the first word or after the last are not
    flagged; `test_status_shows_the_policy_and_a_broken_policy_fails` fails under heavy load only (pre-existing).

### P4 — Hard cases and platforms (upcoming)

Spec S3 rule 2 regions, section 13 rows 2 to 15g, P4 row of section 10.

- One profile per platform as its B.3 recording arrives: detection from the picture, crop and masks, share rule R4,
  cue. Pass marks per profile: T3 95 % on Teams pane ticks including `oct`; R4 90 % elsewhere; S8b 0 contradicted
  names on each operator recording.
- Hard cases: video in a share (R23), two shares, PowerPoint Live and the Teams special modes, a filmed-screen state,
  whiteboards (R26). Contact sheets only if R20 asks; one OCR process with `_MAX_READS` 390 only if R24 fails.
- CONTRACTS §16.33.
- Done when (goal): `scripts/meeting-eval/` prints each shipped profile at or above its pass mark on the local
  fixture set, and the section 13 tests print 0 failed; do not commit a fixture recording.

### F — Field check on the corporate Mac (upcoming)

The bring-back loop carries the build to the corporate Mac; the triage lands as a research note and fixes as their
own waves. Measures R1 to R3 (container facts, the helper builds, one recording-hour timed and the four limit
constants re-set), R7, R8 (FluidAudio builds there), R10, R14 to R19, R24, R25, R30 (VP9 and AV1 on the M4 Max) and
R31 (an agentsync-started download completes, fails with `errno 89`, or hangs).

## Known risks carried

Spec section 12 is the register. The ones that decide the plan: R3 (no timing on the corporate Mac, yet the default
is on), R8 (if FluidAudio does not build there, P3 falls back to Whisper from a wheel with no voices, `d5`), R12 and
R29 (names are checked against accounts, not people, until the operator's recordings and a listen arrive), R31 (if
agentsync-started downloads fail there, ruling 1 rests on the Finder note).
