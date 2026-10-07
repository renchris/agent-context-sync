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
  collide: P1 §16.30, P2 §16.31, P3 §16.32, P4 §16.33; §16.29 is left to the bring-back B8 wave in flight.
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

### H — Acceptance harness (upcoming)

Ships the checks before the code they check (spec section 10, row H).

- `tests/media_kit.py`: `fake_media(folder, script)` answering `--version`, `info`, `scan`, `frames`, `diff`, `pills`,
  `audio`; frames written as `\xff\xd8\xff\xe0` + `FAKE-OCR:` + rows, so the fake OCR helper reads what the fake media
  helper showed. Model: `FAKE_HELPER`, `write_fake`, `fake_image`, `fake_engine` at `tests/test_ocr.py:39, :88, :114,
  :130`; `AGENTSYNC_OCR=0` default at `tests/conftest.py:79`.
- `tests/test_recording_grammar.py`: spec 3.3 rule 1 over hand-written window and index pages, every tag including
  `VOICE`, picture text never in a table cell, a quote mark cannot close a heading label.
- `scripts/meeting-eval/`: the blind-reader runner and scorer of spec 9.1 and 9.2 (readers A, B, C; correctness,
  citation, wrong-and-confident), and the layout scorer of C19 (T3 and R4 against `gt.py` hand labels). Fixture
  folders are arguments, never paths in the repo.
- Done when (goal): `uv run --locked pytest -q tests/test_recording_grammar.py tests/test_media_kit.py` prints
  0 failed, and the scorer reproduces the v2 figures on the two Zoom fixtures (16.5 and 16.5 for reader B) from
  the saved answers; do not add product code.

### P1 — Screens (upcoming)

Spec S0 to S6, S9, S10, 3.1 to 3.6, 4, 4.1, 6; tests 9.3. Profiles `teams`, `meet`, `generic`.

- **Intake and registry.** `Registry.default` `src/agentsync/convert/registry.py:178-219` (label gate at `:211`:
  the recording converter is registered regardless, ruling 2); `for_name` `:240`, `extensions` `:254`,
  `without_ocr` `:226`. Refusal text `src/agentsync/convert/__init__.py:123-141`; `MediaError` and the "not
  finished" signal around `:207-221`. Suffix tuple pattern: `ImageConverter.extensions` `convert/image.py:382-383`.
- **Cycle.** The recording pass after the source loop `src/agentsync/cycle.py:1070-1073`, before
  `_quarantine_secrets` at `:1082`. `_process` `:2555-2565`; `_no_converter` `:2483-2502` (gains the recording
  converter for pre-P1 stubs); `_keeps_page` `:2524-2543`; `_ocr_waits` `:2510-2522`; `_converting` `:2545-2553`.
  Budgets: `_OCR_BUDGET_S` `:137` (add `_RECORDING_BUDGET_S` beside it, kept apart from `_CycleOcr.spent_s` at
  `:857-876`); `_REREAD_ATTEMPTS` `:172`; `_WORK_BATCH` `:132`; the per-source `ByteBudget` `:1894-1896` (recordings
  never spend it). `HYDRATION_REFUSED` `:197-200` (beside it, `RECORDING_WAITS`); `_reread_batch` `:2184-2217` (the
  `reading` mark shape); `_reread_targets` `:2041-2057` (leaves recording suffixes to the pass); `_capabilities`
  `:2002-2019`; `_publish` sidecars into `ok_pages` `:2890-2895` (text sidecars only); dry run `:986-988`;
  `_LABEL_CAPABLE` `:133` (unchanged: recordings do not join it).
- **Download allowance.** `SourceConfig.max_materialise_bytes` `src/agentsync/config.py:216`; `ByteBudget`
  `src/agentsync/model.py:254`; `_download_cost` `cycle.py:261`; staging guard `src/agentsync/materialise.py:268-274`.
  Watchdogs `src/agentsync/ops/launchd.py:56-57, :285-287`; intervals `config.py:256-257`.
- **Piece store.** `<cache_dir>/recordings/<sha256>/`, outside `ConverterCache.gc`'s reach
  (`src/agentsync/convert/cache.py:121-194`); purge via `_cache_entries` `src/agentsync/governance.py:1749-1771`.
- **Loop.** `_unpublished` `src/agentsync/loop.py:172-196` (a `recording` bucket); notes beside `NOTE_PREFIX` `:70`;
  `next_step` `:339`; rule 7 `:548-555` untouched.
- **Helper.** `media_frames.swift` and `convert/media.py` on the model of `convert/ocr.py`: `_HELPER_SOURCE` `:78`,
  `_compile` `:488-520`, `_BUILD_TIMEOUT_S` `:75`, `_untrusted` `:160`, `_run_helper` `:180`, `_switched_off`
  `:389-398`, `probe` `:428`, `engine` `:441`, `__main__` `:778`. Installer build line beside
  `scripts/install.sh:1475-1490`. Doctor `media` line on the model of `_check_ocr` `src/agentsync/ops/doctor.py:413-437`.
- **OCR plumbing.** Rows per region: `_rows` `convert/ocr.py:697-715`, `text_lines` `:769-776` (output and
  `_LAYOUT_REVISION` `:66` unchanged); `OcrEngine.read` `:299-332`; `identity` `:288-292`.
- **Cache identity.** `action_key` `convert/cache.py:34-52`; `_identity` `convert/__init__.py:89-96`; guard options
  `registry.py:116-125`; `outdated_key` model `convert/image.py:398-412`; `_OCR_OPTIONS` `:120-127`.
- **Publish and lints.** `GITATTRIBUTES` `src/agentsync/publish.py:151` (`*.jpg binary`); multi-unit `:366-367`,
  `:869-875`; `allocate_path` `:773-803`. `_read_text` `src/agentsync/lints.py:144-146`, `_builtin_secret_scan`
  `:432-448`, `lint_no_tokens` `:379` (text files only). `policy` status clause in `src/agentsync/cli.py` (the
  `_cmd_status` checks from `:886`).
- **Contracts.** `UnitKind.WINDOW` in `src/agentsync/model.py`; `[convert] recordings` in `_CONVERT_KEYS`
  `config.py:117-118` and the `ConvertConfig` docstring `:198`; CONTRACTS §16.30; `tests/test_contracts.py:37-47`
  public names; CLI and install pins `:153-162, :231-242` unchanged.
- **Review before landing:** fresh reviewers on the recording pass (kill, time-out, download refusal, a document
  beside a recording) and on the grammar against forged screen text.
- Done when (goal): the gate passes and `uv run --locked pytest -q tests/test_media.py
  tests/test_convert_recording.py tests/test_cycle.py tests/test_loop.py` prints 0 failed, the 9.2 thresholds hold on
  both Zoom fixtures through `scripts/meeting-eval/`, and `git ls-tree origin/main src/agentsync/convert/recording.py`
  lists the file; do not name the tenant or commit a recording.

### P2 — Meeting page skill, rubrics, citation lint (upcoming)

Spec 7.1 to 7.4, section 8, the C11 basis forms.

- `lint_meeting_citations` in `src/agentsync/curate.py` beside `generate_depends` `:428` and `_finding` `:332`; never
  called from `checkpoint_blockers` `:860-884`. Pages under `topics/` only: `iter_topic_pages` `:157-170`; parser
  `_parse_topic_text` `:245-271`; limits `:73-74`; `uncovered_mirror_pages` `:762-788`.
- `CITE-*` findings as `warn` lines in `_curate_findings` `src/agentsync/cli.py:1457-1471`, beside TOPIC-BUDGET
  `:1469`; `_cmd_curate` `:1474-1503`. The sync's `_curate` (`cycle.py:3022-3025`) does not run the lint.
- `MEETINGS` block in `procedure` `src/agentsync/skill.py:56-93`; five baseline questions in `BASELINE` `:96-116`.
  `_rubrics/*.md` written with the scaffold, `ensure_scaffold` `src/agentsync/publish.py:725`.
- CONTRACTS §16.31.
- Review before landing: a reader-side review (does the skill text match every tag the converter emits) and the
  drift test of spec section 8.
- Done when (goal): `uv run --locked pytest -q tests/test_curate.py tests/test_skill.py tests/test_publish.py`
  prints 0 failed with one passing and one failing page per lint rule, and a `CITE-*` finding never holds the
  checkpoint; do not make any finding blocking.

### P3 — Speech plus voice naming S8b (upcoming)

Spec S7, S8, S8b, section 5, the `.vtt` turns, C15's speech-only page.

- Speech helper from a FluidAudio checkout at `04e363c` or later; installer and doctor refuse an older pin. Owner-only
  `<cache_dir>/speech/` and build tree (`docs_repo.permissions`). Model digests pinned; agentsync downloads nothing.
- `convert/naming.py` (S8b): thresholds in `options()`, `-n<revision>` in the version, `outdated()` for pages without
  it. `recording.py`: speech identity in `outdated_key`; `_capabilities` `cycle.py:2002-2019` reopens records when
  the model folder appears.
- `convert/vtt.py` (`vtt-turns`); remove `.vtt` from `src/agentsync/convert/text.py:67`; decide how existing `.vtt`
  pages are re-read (`_reread_targets` matches by converter id, `cycle.py:2053-2056`).
- `media_frames.swift` gains `pills` and `audio`; the cue per profile (C18).
- CONTRACTS §16.32. README: placing the model folders.
- **Naming gate:** `VOICE` lines name nobody on a platform until B.3 item 1 and 2 (Teams) have been checked: 0 names
  the listen contradicts. The build lands without it; the gate holds the names.
- Review before landing: a privacy review (no voice vector written; no name outside S8b and the closed set) and a
  determinism review on a fixed WAV.
- Done when (goal): `uv run --locked pytest -q tests/test_voice_naming.py tests/test_convert_recording.py
  tests/test_convert_formats.py` prints 0 failed and the speech determinism test passes on a fixed WAV; do not store
  a voice vector or download a model.

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
