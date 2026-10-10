---
status: done
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

## B8 Prompt v9 and the second bring-back (2026-10-07)

The corporate Mac ran prompt v8 at `41cf618` and brought back a second file (kept outside the repo). Triage:
[docs/research/corporate-bring-back-2026-10-07.md](../research/corporate-bring-back-2026-10-07.md) (run
`wf_6e7fa11d-13e`: 14 items, a skeptic each, one critic). OCR works there: every image, PDF, deck and Word file
on that Mac carries an OCR identity and the one-time re-read finished. Two stops each cost the operator a turn;
both are fixed, with eleven smaller items, in 36 commits (`4e65376..2760d51`; gate 2771 passed). Runs
`wf_b278f496-8dc`, `wf_ebb35cb1-57d`, `wf_c5f4f31e-1d8`. CONTRACTS §16.29 and §16.30.

- **Prompt v9.** `--list-folders` marks folders already synced; a re-run that cannot ask keeps them and adds
  none; a new Mac still stops and waits. `fix-request.md` gets a boundary per session.
- **A setup run heals its own permissions.** `init` and `add-source`, which the installer runs before its health
  check, make agentsync's folders owner-only with the same function the sync uses, so an agent-written folder no
  longer stops the run; the status fix names `agentsync sync` when a sync clears it.
- **The report is honest about a helped run.** A failed install run before the one that ended 0 reads "worked
  with help". It names the file types with no converter, looks a queued purge with no row up as an alias, and
  counts NEXT lines over whole runs.
- **The bring-back sends local work once.** The v8 file re-sent the first patch byte for byte.
- **Guard.** With HOME under a temporary folder agentsync does not write its skill copy to
  `$CLAUDE_CONFIG_DIR/skills` (a build agent's scratch run did that to the operator's real folder).

Left for the operator: the two filed decisions. The empty-folder evidence (5 of 5 materialised and empty, no
mirrored file below) favors counting them as empty, but only with a companion change: on that source's first
complete pass the breaker and the two-pass check are both skipped. The 14 queued purges have no manifest row;
preview with `agentsync purge --queue --dry-run` before running the queue.

## B9 Rehearsal before the next real run (2026-10-07)

The operator asked how many more rounds to expect. Both v8 stops were findable without the corporate Mac, so an
agent now plays the unattended setup agent through the whole prompt in sandbox homes before a prompt is handed
over: a new Mac (must stop at the folder question), a Mac already set up and left in the field state (must finish
with no stop), and an old prompt copy (must be refused). v9 passed all three with the real installer. The
rehearsal found one defect that could cost a turn and three small ones, fixed in 12 commits ending `fd120b2`
(gate 2925 passed; run `wf_5538ff00-563`; CONTRACTS §16.32):

- The bundled pandoc's first start after an update is slow (30 to 67 s measured under load, against the health
  check's 60 s). The installer now waits it out itself before status, with progress lines, and a check that runs
  out of time names a fix an agent may follow. A test fails any `[FAIL]` line that names no fix.
- A refused old copy is no longer reported as having asked a question; a first install prints no stray warning;
  the draft-baseline wait says where its files are.

Held for the next prompt revision (it needs prompt wording, which would force v10): at the new-Mac folder stop the
prompt says both "stop and wait" and "then the report, always". Built in v10 (B10).

Not exercised by a sandbox: File Provider behavior, the macOS privacy prompt, launchd, real volumes, the
corporate network. A third-round surprise would come from there.

Known limits, recorded in CONTRACTS §16.26–16.27: a scanned page under a stamped header of 20 or more characters
is not read; a picture drawn rotated is read as stored; image types outside the raster table are stubs; a long
scan past the time limit converts without OCR.

Open operator decisions (filed, each with a default that keeps what is built): empty cloud folders
(`71d3e66ef726`) and online-only images (`085fac870dd6`).

## B10 The third bring-back and prompt v10 (2026-10-08)

Scope (frozen): when the operator pastes the corporate Mac's v9 `~/agent-context/bring-back.md`, triage it against
main, fix what is new, rehearse before any re-paste go-ahead; redact anything identifying.

The corporate Mac ran prompt v9 at `c7bfdb8` and finished with no stop: attempt 5, install exit 0 in 20 s, 0 human
turns, "fully one command". Triage:
[docs/research/corporate-bring-back-2026-10-08.md](../research/corporate-bring-back-2026-10-08.md) (run
`wf_a17b719b-547`: 12 items, a skeptic each, one critic). Locus: L, an in-process Workflow as in B1 to B9 (run
`wf_e1be1605-1f3`: four builders in four worktrees, a fresh reviewer and a fix pass each). CONTRACTS §16.36.

- **The advice the tool gave was unsafe, and is now guarded.** On a source that never completed a pass, pasting the
  `exclude` line the WAITING ON YOU text advises made the next pass retire every held deletion at once, ahead of
  the breaker and the two-pass rule. A scope change now retires only what left scope; the rest take the ordinary
  path. This is the companion change the 2026-10-07 triage asked for before either answer to `71d3e66ef726`.
  The reviewer found the first build let files new in that pass raise the breaker limit; fixed, with a test for
  the field case (an exclude that covers no mirrored file).
- **Sync:** the stub of an online-only file no converter claims is published once, not in every pass.
- **Purge:** a dry run of an entry that targets nothing says so; the queue file is written after each verified
  entry, so a run that stops part-way leaves no purged entry queued.
- **Report:** Recent errors merges a line repeated in a row (the field window was 37 of 40 lines of one warning;
  the first build merged before the path scrub and so merged nothing in the field case, caught in review); the
  residue check sees a word joined to a placeholder and skips a generic home folder; no padding from a redacted
  id; the folder legend; purge counts from the audit trail; a media-helper line.
- **Prompt v10:** step 2 names one inbox (the folder beside the docs repo, not every `kind = "inbox"` source),
  step 3 names the two stops with no report (the folder wait and a copy that is not the installer's), and `hold`
  is glossed. README, the deploy guide and doctor's drop-here line follow the same rule. v9 ran clean, so v10
  needs no run of its own.
- Not built, on purpose: counting an empty materialised cloud folder as listed (the operator's decision; narrow
  design in the triage), a `.url` converter (small value), the report's "unexpected 5" and "(status: baseline
  complete)" labels (contract text, left).

Rehearsal of v10 before it landed (run `wf_988b2b0c-a88`, four sandbox homes with the real installer; reports kept
outside the repo): a new Mac stopped at the folder question with no report, then ran to the end once answered; a Mac
in the field state finished with no stop and named one inbox; v9 and v8 copies were refused in step 1; and the
`exclude` line retired 4 absent in-scope files at once on `c7bfdb8` and none on this build, where they took the
two-pass rule. No finding cost a turn. Three were fixed before landing (run `wf_8709384e-297`): step 3 names both
stops with no report (the folder wait and a copy that is not the installer's), the report of a stopped copy no
longer rewrites `bring-back.md` or prints an issue link, and the `exclude` advice prints the source id as
`sources.toml` writes it, with the file's path. Left, all small: the report's "unexpected" label for a wait that
is the operator's, the `alarm:` line the prompt does not name, and no status line between the pass that marks a
file absent and the pass that removes it. Not exercised by a sandbox, as before: File Provider, the privacy
prompt, launchd, real volumes, the corporate network.

Learnings: both review catches were places where a builder's own note said the opposite of the code's effect
("can only hold more", "merge before the scrub"); the reviewer measured instead of reading. The field agent did
not follow the wrong inbox sentence, so a prompt defect can stay invisible in the outcome line: read the friction
log, not only the outcome.

## B11 The v10 rehearsal's leftovers, with no prompt text (2026-10-08)

Scope (grown): +build the small rehearsal findings that need no prompt text, as one reviewed batch. The operator
asked whether the work was 100% of what is reachable; these were what was left. The prompt stays v10.

Locus: L, two in-process Workflows as in B1 to B10. Verify (run `wf_5571913d-f44`): a verifier and a skeptic for
each of 7 candidates, read-only; notes in `~/.cache/agentsync-bring-back/b11/findings/`. Build (run
`wf_4c3c1952-ee1`): four builders in four worktrees, a fresh reviewer and a fix pass each; the lead integrated
the four branches on `bb/b11`, folded in one review nit and wrote CONTRACTS §16.37.

Built (13 commits):
- **Report:** a report written more than 10 minutes after the last attempt finished, with no run since, says no
  attempt was started since and that a later session that stopped in step 1 before logging is not in it (the
  rehearsal's one "wrong" finding). The skeptic refuted the verifier's fix, which had the installer log a step 1
  error: a report written again by hand would then read "failed at step 1" and the local work would be sent
  again. A session that stopped before `--log-start` counts no folder question and no click. The Loop line lists
  every wait (no "(+N more)"). "not logged as a question". The first-sync count says it is read when the report
  is written.
- **Installer:** the helper builds are the install.log step `helpers` (the rehearsal billed about 100 s of speech
  build to a skipped `launcher` step) and print `helpers: still running` lines; the builds stay in the
  foreground, so a stopped run never leaves a second build racing the re-run.
- **Sync and status:** status counts files marked absent before the pass that removes them; the removing pass
  says how many; a commit beside "0 change(s)" says what it holds; the summary adds "read again R".
- **Alarm and purge:** an alarm whose unknown folders are all empty cloud folders points at the WAITING ON YOU
  line, not at Files and Folders access; a purge dry run's headline is in the conditional.

Dropped, by design (verifier and skeptic agreed): the Summary's "unexpected" label for the person's own waits
(refuted a second time: doctor's `heartbeat.<id>` warns the same way for a stopped background sync, so a
"yours" group would hide the next C11; the 2026-10-08 triage had dropped it too); "in _eval" without its
folder; placeholder numbers that skip; a refused attempt with no install.log line; doctor's other fixes in single
quotes (they name no line to search for); the stopped copy's `setup-report.md` (an old copy's own finish line
names that file).

Held for the next prompt revision (do not build without one): step 1's Xcode and git stops should log their
error before the report (the report note above is the stopgap); step 3's second no-report case names only "not
the installer's" while step 1 has two triggers; "the last NEXT: or WAITING ON YOU: line" has several candidates;
a re-run whose installer NEXT already says "session done" against step 3's "start the loop"; a session with
nothing to request and the fix-request heading; "removing a file removes its page" (the page becomes a deletion
notice); an indented `alarm:` line is explained by a WAITING ON YOU line. Still the operator's choice, not built:
a `.url` converter.

Rehearsal on the landed `a086c2f` (run `wf_0dd2de06-dc0`, reports in `~/.cache/agentsync-bring-back/rehearsal-b11/`):
new Mac and field-state Mac both PASS, the old v9 copy refused with `bring-back.md` unchanged; every B11 change read
right on real output (`read again 2`, `absent 1` then the removal alarm, both waits on the Loop line, the new alarm
text). No finding cost a turn or was wrong. Two small ones were built after it (run `wf_d921990c-2f0`, fresh
review): the report's first-sync clause carries the installer's read-again count (install.log note
`-reread-R`), and the helpers ticker names the helper it waits for (the speech build was about 3 minutes of
`helpers: still running` ending in `speech: off`). Both scenarios were played again on that landed commit before
the go-ahead.

Final rehearsal on `1a3a27d` (run `wf_3870484c-942`, reports in `~/.cache/agentsync-bring-back/rehearsal-b11b/`):
both PASS; the follow-ups read right (`read again 2` in the report and `-reread-2` in install.log; eight
`helpers: building the speech helper` ticks); the "no attempt was started since" note appeared on a report written
10 minutes after the finish. No finding cost a turn or was wrong. Left on purpose, all cosmetic, because each
changes installer output and would need yet another rehearsal: the `speech: off` line does not say the helper
was built; the note's "no install.sh run began" means runs in install.log (`--report-only` is not logged there);
the removing pass prints the same `deletion_candidate=N` counter as the marking pass (its alarm tells them apart).
The prompt block on origin/main is byte-identical to `69694db`'s: v10 stays, and a saved v10 copy needs no recopy.

## Outlook and the stop rule for prompt versions (2026-10-09; proposed, operator ruling pending)

The operator, 2026-10-09: "we will do the prompt back and forth as many times as we need, but hoping that it can be
a few significant ones rather than forever back and forth. we are at v10 now ... i dont want to do this forever
until v100." Field rounds so far: v6/v7 found 19 items (several cost a turn), v8 found 11 (two stops, both findable
without the Mac), v9 ran clean (one command, 0 turns, 0 errors; one prompt finding that did no harm). v10 exists
for three wording fixes, not a failure: since B7 any wording change bumps the version so an old copy is visible.

Proposed rule (lead's conviction about 80% that v10 is the last setup version the operator pastes):
1. v10 is frozen. Prompt-wording findings go to the held list in B11 and are not built.
2. v11 only for a field finding that costs the person a turn or gives a wrong result; it then carries the whole
   held list in one revision. Cosmetic ("small") findings, in the field or in a rehearsal, never make a version
   and are not built round by round.
3. Setup is done after one more clean field run (one command, 0 turns, nothing wrong): v9 did it once, a clean v10
   run makes two. Triage of the next bring-back builds only "turn" and "wrong" items.

The next paste of v10 on the corporate Mac is an update, not a test: that Mac runs `c7bfdb8`, which lacks the
scope-change guard that makes the `exclude` line safe. A surprise can still come only from what a sandbox cannot
reach (File Provider, the privacy prompt, launchd, real volumes, the corporate network); v9 already passed through
them once there. The bottleneck is no longer setup: three sessions ended waiting on the operator confirming the
baseline questions (1,455 items to curate behind it) and on decisions `71d3e66ef726` (empty cloud folders) and
`085fac870dd6` (online-only images).

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
