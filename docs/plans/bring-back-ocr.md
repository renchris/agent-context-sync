---
status: in-progress
---

# Plan — the corporate Mac's bring-back file: field fixes, riders, PDF comments, on-device OCR

Scope (frozen): when the operator pastes the corporate Mac's `~/agent-context/bring-back.md`, triage it and rebuild
its OCR/converter work and fix requests properly on main; redact anything identifying.

Triage: [docs/research/corporate-bring-back-2026-10-06.md](../research/corporate-bring-back-2026-10-06.md). The
paste, the patch and the full review findings stay outside the repository (`~/.cache/agentsync-bring-back/`):
sections 2 and 3 of the paste name the tenant, people and real files.

## Phase 0 — Agent Team Orchestration

- **Execution locus per wave:** B1–B6 each run as an in-process **Workflow** (L, with workflow agents). Why not S:
  the operator opted this session into workflows by keyword, and KISS W1/W2 set the precedent (sequential build
  agents in one worktree per wave, a fresh reviewer per change, a fix pass, the full gate, the lead lands).
- **Order:** (B1 ∥ B2 ∥ B3 ∥ B4), each in its own worktree off `main`; land smallest diff first, rebasing the rest.
  Then B5 (needs B3's `pdf.py` and B4's engine), then B6 (needs B2's scope predicate, B3's 2.1.0 and B5).
- **Shared files, one owner per function:** `docs/design/CONTRACTS.md` gets one new section per wave, numbered
  here so appends do not collide: B1 §16.22, B2 §16.23, B3 §16.24, B4 §16.25, B5 §16.26, B6 §16.27.
  `ops/doctor.py`: B1 owns wording of existing checks, B4 adds `_check_ocr`. `cycle.py`: B1 owns the permission
  self-heal, B2 the work filter, B6 the re-read. `publish.py`: B1 owns `root_guide`, B2 `sidecar_rel`.
- **Lead:** this session. Context budget: under 50% for deciding; the findings live in files, not in context.
  Succession point: after the first four waves land (recycle with this plan as the bridge).
- **Every wave:** the gate in `CLAUDE.md` § Gate before landing; `/ship`; CI read back green.
- **Names:** before every commit, the staged diff is searched for the identifying strings listed in
  `~/.cache/agentsync-bring-back/identifying.txt`. The patch's tests are re-typed with Contoso and made-up names;
  `c34e07c` is never cherry-picked and its branch `scratch/bring-back-v6` is never pushed.

## Decisions (lead, 2026-10-06)

The patch was applied with `git am` at `35c2b5f` (prompt v6) on `scratch/bring-back-v6`; its own tests pass there
(219 passed, 85 s, including the real Apple Vision test). It is rebuilt, not merged: a review of eight units, each
checked by a skeptic (Workflow `wf_44ecd2de-039`), confirmed defects in every unit.

| # | Decision | Why |
|---|---|---|
| D1 | No `agentsync ocr` command, no `--json`/`--max-pages`. | The fix request asks for none; it bypasses the policy screen and the banner; the CLI freeze makes a tenth command a deliberate act and nothing in the loop needs it. |
| D2 | One new config key: `[convert] ocr = true \| false` (default true). Languages and page caps are constants. | A managed Mac must be able to refuse a locally compiled binary, and the LaunchAgent does not see a shell's environment. Two more keys bought nothing the field asked for. |
| D3 | One environment switch, `AGENTSYNC_OCR=0`, for the test suite. No `AGENTSYNC_OCR_HELPER`. | The helper override let an unattended agent run any executable named in its environment. Tests inject a fake engine. |
| D4 | Only `install.sh` builds the helper (inside its launcher step, `python -m agentsync.convert.ocr`). `status`, `doctor`, setup-report, a dry run and the LaunchAgent never compile; they say "not built: run install.sh again". | `status` is the read-only check; a first build takes about 15 s and up to 600 s on a bad toolchain, under the writer lock. Upgrades already go through `install.sh`. |
| D5 | The helper is 0700, under `<cache_dir>/ocr`, and is run only when it is a regular file owned by the user with no group/other write on it or its folder. | The patch wrote it 0755 inside the folder `docs_repo.permissions` requires owner-only: every install would end on a FAIL. |
| D6 | Converter versions gain no `+ocr-off` suffix. With an engine the version ends `+ocr-<engine>-r<revision>-h<helper>-l<layout>`; no macOS build. | The suffix changed the version of every docx, pptx, pdf, rtf and html page on every Mac, with or without OCR. |
| D7 | An image with no legible text is `UnreadableSourceError` (a cached stub), not a page. The body and title carry no file name. The summary carries facts only. | A page per logo is curation work for ever; the converter cache key has no name in it, so a name in the body is wrong for the second file with the same bytes; summaries are converter-derived, never document text. |
| D8 | With any `[policy]` label rule active, images are not converted. | Images can carry a sensitivity label the screen cannot read; fail closed. |
| D9 | Online-only images are not downloaded for OCR; images already on the Mac are read. Open operator decision in the triage. | Today no image is ever downloaded. Hydrating a photo library is a bandwidth and disk choice that is the operator's. |
| D10 | OCR never fails a document that converts without it. A helper failure gives the no-OCR result under the no-OCR version, so the one-shot re-read picks it up later. Failure text never enters a page. | Main has no "OK but do not cache" result; exception text carried a home path. |
| D11 | A re-read that fails keeps the existing page. Re-reads select only rows the current pass saw unchanged and that are on disk, per source, in small batches under a time bound, and never during `materialise PATH`. | The patch re-marked the same 200 rows every cycle, forced re-downloads, and could replace a good page with a stub. |
| D12 | PDF comment authors are kept. | Same as xlsx comments and mail senders on main. |
| D13 | PDF pages are rasterised in-process by PDFium (accepted); page renders go under the cycle's staging folder, not `$TMPDIR`. | Staging is 0700, Time-Machine-excluded and wiped every cycle. |

## Result (2026-10-06)

All six waves are on `main`: 101 commits, `5aae95d..d7921ad`. Gate on the final tip: ruff, format, mypy, shellcheck,
diagrams and probes clean; pytest 2570 passed, 2 skipped. Runs: review `wf_44ecd2de-039` (112 agents), build
`wf_e0ff11e4-75a` (B1–B4, 28 agents) and `wf_df2cbc13-5b1` (B5–B6, 11 agents). Every wave had a full gate, fresh
reviewers and a fix pass before it was stacked; no slot returned empty. Surface added: the key `[convert] ocr`,
the doctor line `ocr`, the converter id `image-ocr`. The CLI freeze pins did not change.

| Wave | Landed | What is now true |
|---|---|---|
| B1 field fixes | 22 commits, in `46a102f` | Triage items I1–I19: the report keeps an agent's product name and no longer leaks escaped folder names, nested names in log lines, source ids or project paths; a late friction line or a late step-2 error no longer sets the outcome; the guides name the kept inbox; sync makes agent-written docs-repo paths owner-only; installer and status wording. |
| B2 riders | 11 commits | One-pass chart values; sidecar names shortened under the path cap (archive path included); one scope predicate shared by the walk and the work queue, out-of-scope rows retired on incomplete passes; the re-screen guard. |
| B3 PDF comments | 7 commits | `pdf-pypdfium2` 2.1.0 keeps reviewer comments, one line each, replies nested, hidden ones skipped. CONTRACTS §16.24. |
| B4 OCR engine | 15 commits | Helper and driver per D2–D6; only `install.sh` builds; `status` never compiles. CONTRACTS §16.25. |
| B5 OCR in converters | in `d7921ad` | Images, scanned PDF pages, pictures on PDF pages, in decks and in Word/ODT files. CONTRACTS §16.26. |
| B6 re-read once | in `d7921ad` | Files converted before a capability existed are re-read once, on disk only; README and design text. CONTRACTS §16.27. |

Changed from the decisions above while building (each reviewed):
- **D10/D11:** a document the engine fails on gets the no-OCR page, is retried once, then kept until its bytes or
  the capabilities change. A Graph file is never downloaded a second time for a re-read, so it gains OCR only when
  its bytes change. Each re-read is its own transaction (up to two commits a file).
- **Page cap:** `MAX_PAGES` is 40, not 100, and the per-document time limit grows with the page count (longest
  reading 900 s), to stay under the launcher's 1800 s watchdog. Budgets: 180 s of OCR per cycle, 120 s of re-reads.
  None of these was timed on the corporate Mac.
- **Pillow** is not declared: nothing in `src` imports it.
- **Pages the field patch wrote** (version ending `+ocr-off`) count as outdated and are re-read once.
- **Empty-folder text (I10):** the WAITING line carries the ready-to-paste `exclude` line with the folder names,
  and the setup report shows that list as `<path>` (tested), which keeps `loop.py`'s rule for everything else.
- **CI:** the real-Vision test first failed on GitHub's virtual Mac, which reads about half of the 16 px labels.
  It now checks whole-and-once everywhere and seam stitching where Vision reads every label (`582c9e8`).

## B7 The next bring-back is the last one (2026-10-07)

Added after the operator asked whether the round was exhaustive: it was not. The setup report said nothing about
what OCR did, the triage still asked for eight facts by hand, and wording-only prompt edits had kept the version at
v7, so an old copy could not be told from the current one. 27 commits, `02ee347..63a4f2e`; gate 2660 passed.
Runs `wf_f74fb161-ff0` and `wf_18e5a3df-d6c`. CONTRACTS §16.28.

- **Evidence in the report:** six parts under Status (OCR results and time per run, quarantine by reason, purge
  queue by reason and age, overlapping sources, empty cloud folders as dataless or materialised-and-empty, repeat
  conversions), plus which plist arguments differ and installer runs with no end line. Counts and fixed words only.
- **Prompt v8:** the version is bumped on any change to the prompt's text (a test pins the block's digest to its
  number). The prompt hands its version to the installer inside the `--log-start` value; a copy that is not the
  installer's version is stopped in step 1 with a message to copy the prompt again from the README on `main`.
- **One round:** `sync` prints `note: sync again: N file(s) ...` while the one-time re-read is not finished, and
  prompt step 3 syncs again while that note shows, up to 12 more times, downloading nothing extra and running no
  destructive step, before it writes the bring-back file.
- Not measured: how many syncs a real mirror needs. A mirror heavy in scans can hit the cap of 12; the report's
  Loop line and per-run table then say how much is left.

## B8 Prompt v9 and the second bring-back (2026-10-07) — IN FLIGHT

The corporate Mac ran prompt v8 at `41cf618` and brought back a second file (saved outside the repo under
`~/.cache/agentsync-bring-back/v8/`). OCR works there: every image, PDF, deck and Word file carries OCR text and
the one-time re-read finished in five background runs with no budget hit. Two stops cost the operator a turn each
and are being fixed on branch `bb/b8-configured-folders` (worktree `../.worktrees/bb-b8`), not yet landed:

1. **Folder question on a Mac already set up** (built, reviewed, gate green, run `wf_b278f496-8dc`):
   `--list-folders` marks folders already synced; an unattended re-run keeps them and adds none. Prompt v9.
2. **Installer stops on a permissions FAIL the first sync would heal** (building, run `wf_ebb35cb1-57d`): the
   installer tightens agentsync-owned folders before its health check.
3. **Triage of the v8 file** (running, read-only, run `wf_6e7fa11d-13e`, 14 items with a skeptic each; result
   lands in `~/.cache/agentsync-bring-back/wf7.json`). Known items to build after it, on the same branch, all
   part of v9: the outcome must not read "fully one command" when an earlier install run of the attempt failed
   and a manual fix was needed; the summary block prints once for several folders; `~/agent-context/setup` is
   made owner-only; `fix-request.md` holds one session's request; the bring-back includes local work only when
   it is new (the v8 file re-sent the first patch byte for byte); the quarantine part names file suffixes; a
   queued purge whose row is gone is reported as such. Evidence for the empty-folder decision
   (`71d3e66ef726`): 5 of 5 folders are materialised and empty, none dataless, no mirrored file below them.

Order to finish: collect both workflows, build the triage's new items on the branch (build, fresh review, fix,
gate), write `docs/research/corporate-bring-back-2026-10-07.md`, rebase onto `main`, full gate, push, read CI and
the README back from `origin/main`, then tell the operator "prompt v9 at <sha>". Until then v8 is current.

Known limits, recorded in CONTRACTS §16.26–16.27: a scanned page under a stamped header of 20 or more characters
is not read; a picture drawn rotated is read as stored; image types outside the raster table are stubs; a long
scan past the time limit converts without OCR.

Open operator decisions (filed, each with a default that keeps what is built): empty cloud folders
(`71d3e66ef726`) and online-only images (`085fac870dd6`).

## B1 Field fixes from the bring-back report

Items I1–I19 in the triage § 3 (setup report redaction and attempt accounting, doctor and installer wording,
the AGENTS.md inbox path, the `_eval` permissions self-heal, the empty-cloud-folder alarm, WAITING and doctor texts (folder names only in the sync alarm; `loop.py`'s rule keeps them out of wait lines), prompt wording).
No command, flag or config key. Files: `setup_report.py`, `ops/doctor.py`, `cli.py`, `publish.py` (`root_guide`),
`cycle.py` (permission self-heal), `arm_local.py` (log level), `loop.py`, `scripts/install.sh`, `README.md` prompt,
their tests, CONTRACTS §16.22. Detail per item: `findings/claims.md` (outside the repo), ids as in the triage.

DoD: every "new" row of the triage § 3 is fixed with a test or recorded there as not done with the reason.

## B2 Riders the patch carried

1. `pptx.py` `_series_values`: one pass over a series' points, ignoring indexes at or above `ptCount` so output is
   byte-identical and the emitter version stays. Test without a clock (make the quadratic accessor raise).
2. `publish.sidecar_rel`: a name that would pass `slug.MAX_PATH_CHARS` is shortened to `<stem>-<8 hex><ext>`,
   measured against the archive path too; `rewrite_frontmatter` maps sidecars through it on a rename; a page too
   long for any sidecar settles as one item's refusal, never an over-long file. Test page lengths 150–200.
3. Scope: one predicate shared with the walk (`LocalArm.in_scope`, built from the walk's matcher, the effective
   exclude list and the ancestor-directory rule; `DriveArm.in_scope`). The work queue skips out-of-scope rows, and
   local/inbox rows out of scope are retired `retired:scope-change` on incomplete passes too, so `loop` does not
   count them as pending for ever.
4. `cycle._rescreen_lines`: the guard treats the stored `""` as "no re-screen pending".

Detail: `findings/unit-riders-and-docs.md`, `findings/unit-requeue-cycle-manifest.md` (scope filter defects).

## B3 PDF comments

`pdf-pypdfium2` 2.1.0: each page's reviewer comments follow its text under
`[comments on this page (PDF annotations):]`. Rebuilt in `pdf.py` only, with no OCR import: comments are read
inside `_pdfium_pages` on the open page; one line per comment; marked text selected by character centre; replies
nested with an explicit stack, orphans and cycles still emitted; hidden and no-view annotations skipped; quote
capped; a page with comments but no text is not refused. No `comments@1` option. Builders and tests use made-up
names. Detail: `findings/unit-pdf-comments.md`.

## B4 OCR engine

`convert/vision_ocr.swift` and `convert/ocr.py` rebuilt per D2–D6: tile-merge and row-banding fixes, oriented
sizes, a megapixel cap and a minimum-size rule in the helper, an ImageIO type allowlist, validated JSON, a
persisted failure marker, no sibling deletion, error text without paths. `[convert] ocr`, doctor's `ocr` line
(never builds), the `install.sh` build, the test switch in `conftest.py` and in hand-built subprocess
environments. Tests run a fake engine; one test builds the real helper where developer tools exist.
Detail: `findings/unit-ocr-core.md`, `unit-surface-cli-config-doctor.md`, `unit-security-privacy.md`.

## B5 OCR in the converters

`convert/image.py` (`image-ocr`, D7–D9), scanned PDF pages and pictures on PDF pages, pictures in decks and Word
documents (D10, D13): streamed and bounded media reads, one shared OCR option set, text escaped so it cannot
break out of a page, a per-document time limit shared by both helper calls, a per-cycle OCR budget, every new
suffix known to the setup report's log scrub, Pillow declared. `Registry.default(cfg, *, policy=None, ocr=None)`:
the cycle resolves the engine once and passes it in. Detail: `findings/unit-image-converter.md`,
`unit-embedded-pictures.md`.

## B6 Re-read once

Files converted before a capability existed are re-read once (D11): refused "no converter" rows whose suffix now
has one, scanned PDFs quarantined as "no text layer", pages produced without OCR, PDFs produced before 2.1.0.
Gated by a stored fingerprint so the scan does not run every cycle. README and design text for B3–B6 land here.
Detail: `findings/unit-requeue-cycle-manifest.md`.

## Close

`cc-backlog done 3ab8e1a02a41`; one line to the operator on whether the corporate Mac re-pastes the prompt.
