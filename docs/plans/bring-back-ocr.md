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
is not read; a picture the page shows on its side (turned on purpose to fit) is read on its side (B13 N45 reads a
picture the way up the page shows it; fixed by §16.48 R82: a quarter-turned picture whose lines run down is read
once more the other way; still a limit: a picture drawn upright on a page whose /Rotate alone turns it is read as
the page shows it, because there is no other orientation to try); image types outside the raster table are stubs;
a long scan past the time limit converts without OCR.

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

## B12 The fourth bring-back: v10 ran clean (2026-10-10)

Scope (frozen): when the operator pastes the corporate Mac's v10 `~/agent-context/bring-back.md`, triage it against
main, fix what is new, rehearse before any re-paste go-ahead; redact anything identifying.

The corporate Mac ran prompt v10 at `4a42af9`. It was one command, with 0 human turns, 0 deviations and 0 errors,
and install exit 0 after 723 s. Triage:
[docs/research/corporate-bring-back-2026-10-10.md](../research/corporate-bring-back-2026-10-10.md) (run
`wf_0f0bb474-b7e`: 6 items, a verifier and a skeptic each, one critic; findings in
`~/.cache/agentsync-bring-back/v10/findings/`). Locus: L, as in B1 to B11.

- **Under the proposed stop rule:** 0 turn items. The two wrong items were latent redaction leaks, built in
  `ba5b993`: a "Last, First" full name kept its first name, and a hosted MDM server's host was printed. Everything
  else is cosmetic, known or the operator's.
- **F34, the 12-minute install:** the first speech-helper build took about 687 s on the corporate network, against
  about 101 s in the warm-cache rehearsal. It cost no turn and does not recur on this Mac. Held for v11: a
  step 2 timeout sentence that names a first speech build.
- Held, cosmetic (add to B11's list): section 1 has no speech line; the PAC path is not redacted on a new Mac;
  the IT request does not name the speech build's binary download; the speech build passes no proxy variables
  (meeting-video R8); a group kill leaves compiler processes running.

Review and rehearsal of the fix, five rounds. Each landed commit got a fresh reviewer and both scenarios, new Mac
and field-state Mac plus an old v9 copy, played on the landed commit. Runs: `wf_6e059eef-1ae`, `wf_6419e9a5-7b5`,
`wf_299a8f7e-a61`, `wf_6e4d02c8-806` and `wf_18f6ca60-02c`; reports in
`~/.cache/agentsync-bring-back/rehearsal-v10fix{,2,3,4,5}/`. Every rehearsal passed, and no finding cost a turn.
Each review found a redaction gap the last fix had opened or left, and each one was built with a test that fails
without it:
- `2dc272a`: keeping the name whole had lost its fuzzy forms ("doe", "jane-doe"); the shell fallback report
  leaked both values too.
- `d44b49e`: short comma parts ("Doe, Ed"); also the rehearsal's one wrong finding, older than this round:
  `install.sh --report-only` names no folder, so the shell fallback printed earlier runs' folder names in clear.
  It now reads them from sources.toml and install.log.
- `2964568`: a suffix ("Doe, Jane, Jr."); BSD sed stopping at a byte that is not UTF-8.
- `de3eff0`: the same for sort; pairs applied longest first, so "Plans" no longer half-redacts "Plans Archive".
- `0839f34`: the same for cut, which in de3eff0 dropped every pair after a non-ASCII folder's (review
  "high").

The shell fallback writes the report only when agentsync itself cannot run. Learning: in a shell pipeline that
carries `printf %q` output, every stage needs `LC_ALL=C`. Each review found the next stage that lacked it.

Held, not built:
- The shell fallback report stops at an install.log line that holds a `$'…'` word (any folder named with a
  curly apostrophe, an en dash or an NFD accent). This happens in every version since 1935b66. Adding
  `LC_ALL=C` alone would print the escaped name instead, so it needs the `$'…'` form redacted first.
- The fallback's Summary says "source commit -" under `--report-only`.
- `speech: off` does not say the helper was built.
- A speech build killed by a signal reads as "exit -9".
- A two-letter first part ("Li, Jane") is redacted only inside the joined forms, as CONTRACTS now says.

## B13 Build every captured item, prompt v11 (2026-10-10)

Scope (frozen): build every item in the ledger below to done (fixed with a test, or for prompt text pinned by the
digest test), or record per item the reason a build would make things worse; ship the prompt-wording items as v11;
gate green, landed, CI read back green; v11 rehearsed (new Mac, field-state Mac, old v10 copy refused) on the landed
commit before the re-paste go-ahead. Operator ruling: "Outlook and the stop rule", Ruling (2026-10-10).

Locus: L, as in B1 to B12, under the operator's "ultracode" opt-in. Why not S: the items are already triaged and
each wave's synthesis (spec against skeptic, branch against branch) is the lead's. Waves: (1) a design Workflow, a
designer and a skeptic per item plus a completeness critic that re-reads every earlier triage's held and not-built
rows and B13's not-built list; specs in `~/.cache/agentsync-bring-back/b13/design/`. (2) a build Workflow, one
builder per cluster in its own worktree (one owner per file), a fresh reviewer and a fix pass each; the lead
integrates, runs the gate, lands, reads CI. (3) the rehearsal Workflow on the landed commit. Lead context: under
50%; succession point after the land, with this section as the bridge.

| Id | Item | Cluster | Source |
|---|---|---|---|
| H1 | Step 1's Xcode and git stops log their error before the report | prompt | B11 |
| H2 | Step 3's second no-report case names both step-1 triggers | prompt | B11 |
| H3 | "the last NEXT: or WAITING ON YOU: line" has several candidates | prompt | B11 |
| H4 | A re-run whose installer NEXT says "session done" against step 3's "start the loop" | prompt | B11 |
| H5 | A session with nothing to request and the fix-request heading | prompt | B11 |
| H6 | "removing a file removes its page": the page becomes a deletion notice | prompt | B11 |
| H7 | An indented `alarm:` line is explained by a WAITING ON YOU line | prompt | B11 |
| H8 | Step 2's timeout sentence names a first speech build (up to 20 minutes) | prompt | B12 F34, E1.e |
| H9 | The IT request names the speech build's prebuilt binary download | docs | B12 C1 |
| H10 | The speech build's `swift package resolve` gets the proxy variables | installer | B12 C2, meeting-video R8 |
| H11 | A group kill leaves compiler processes running | installer | B12 E1.d |
| H12 | A speech build killed by a signal reads as "exit -9" | installer | B12 |
| H13 | `speech: off` does not say the helper was built | installer | B11, B12 |
| H14 | A SIGKILL during install leaves no end line | installer | B12 E1.d |
| H15 | The shell fallback report stops at an install.log line holding a `$'…'` word | fallback report | B12 |
| H16 | The fallback's Summary says "source commit -" under `--report-only` | fallback report | B12 |
| H17 | The "no install.sh run began" note means runs in install.log; `--report-only` is not logged there | report | B11 |
| H18 | Section 1 of the report has no speech line | report | B12 E3.e |
| H19 | A cloud proxy's PAC path is not redacted (only the URL's host) | report | B12 E4.a″ |
| H20 | A two-letter first part ("Li, Jane") is redacted only inside the joined forms | report | B12 |
| H21 | Installer-output counts span earlier attempts; attempt 1 reads "failed at step 2" under v6 rules | report | B12 E3.b, E3.h |
| H22 | Recent errors counts only the last 64 KiB of each log | report | B12 C4 |
| H23 | The removing pass prints the same `deletion_candidate=N` counter as the marking pass | sync | B11 |
| H24 | A `.url` (internet shortcut) converter | converter | B10, B11 |

Re-examined by the critic under the ruling (each either keeps its written reason or joins the ledger): skipping
the speech build until models are placed (B12 E1.f); `config.graph_company` on a WAITING ON YOU line (B12 E2); the
B11 dropped list; the report's "unexpected" and "(status: baseline complete)" labels (B10). Not in scope: the
empty-cloud-folder rule, which is decision `71d3e66ef726` (default: keep the rule, fires 2026-10-20).

### B13 design result (2026-10-11)

Runs `wf_0bcde692-839` (cut off by account `next`'s weekly limit after 9 designs) and its continuation
`wf_abafd050-e40` (134 agents, 0 errors): a designer and a skeptic for each item, one completeness critic. Specs
and skeptic corrections: `~/.cache/agentsync-bring-back/b13/design/<id>-{design,skeptic}.md` (and `.patch`
prototypes); digest `results.json`, lane map `lanes.json`. All 71 items are verdict build; where the skeptic
corrected the designer, the skeptic's design is the one built. The critic added N1 to N47 and kept 16 not-built
reasons (notes in `critic.md`); the five reasons it refuted joined the ledger as N16, N17, N18, N19 and N42.

Two are data-integrity defects, reproduced on main, and land first (lane A): N1, a purge deletes the rows and index
line of whatever live file now holds a purged path while the run says VERIFIED, after which a repair pass deletes
that live page as an orphan; N2, the purge queue runs an upstream-deleted entry whose id now resolves to a listed
file. N5 as first designed would publish plaintext passwords (gitleaks replaces the builtin patterns rather than
adding to them); the skeptic's design keeps both scans.

| Id | Item | Lane |
|---|---|---|
| N1 | A purge deletes the rows and index line of a live file that now holds a purged path | A |
| N2 | The purge queue runs an upstream-deleted entry whose id now resolves to a listed file | A |
| N3 | A re-read can close a source's scan while a file still lacks OCR | B |
| N4 | README says a given-up file is tried again "when agentsync is upgraded" | B |
| N5 | Credential quarantines are never screened again after the secret-scan rules change | B |
| N6 | install.sh reinstalls agentsync with `uv tool install --force` while a background job may run | D |
| N7 | Redaction does no Unicode normalisation (an NFD folder name escapes it) | C |
| N8 | Name forms leak for a display name with a bracketed tail or a middle part | C |
| N9 | Non-OneDrive cloud provider folders are printed verbatim in Environment | C |
| N10 | Recent errors shows the log's local-time stamps | C |
| N11 | A doctor fix copied from the report quotes `~` so a shell does not expand it | B |
| N12 | Shell fallback report edge cases (word splitting and others) | D |
| N13 | A purge by selector never dequeues the queue entries it erased | A |
| N14 | The purge wait text for queued purges | A |
| N15 | accept-deletions on an incomplete pass clears a tripped breaker and applies nothing | A |
| N16 | The Summary's doctor line calls the person's own waits "unexpected" | B |
| N17 | "N of M source(s) listed completely (status: baseline complete)" label | C |
| N18 | The Summary's wait says "in _eval" without its folder | C |
| N19 | Doctor's source-table fixes print `id = 'x'` in single quotes | B |
| N20 | "last install.sh run: commit" takes the last start line of any run | C |
| N21 | The report install.sh writes at its own exit counts one NEXT short | D |
| N22 | Status's inbox line checks every inbox-kind source, not the kept inbox | B |
| N23 | Status's per-source "quarantined N" adds refused rows, while the report splits them | B |
| N24 | The "skipped: lock held" warning drops the holder | B |
| N25 | The Summary always says "first sync:" even when the config existed | C |
| N26 | Neither the exclude wait nor the report's empty-cloud-folder part counts the mirrored files | B |
| N27 | Empty-cloud-folder texts in the mixed case | B |
| N28 | The per-directory "unknown (EPERM)" line stays a WARNING on every pass | B |
| N29 | The IT draft line prints whenever the draft exists and never says it is from an earlier run | C |
| N30 | Doctor's 500-entry permission sample is spent inside .git and the mirror | B |
| N31 | The redaction legend counts fewer replacements than the placeholders a reader sees | C |
| N32 | The Summary does not say the update's re-reads ran in background runs | C |
| N33 | A run with no end line reads "reached launcher (skipped)" when it was in helpers | C |
| N34 | The baseline NEXT names only the skill's Baseline questions section | B |
| N35 | The Quarantine line's "other N (D distinct)" gives no shape words | C |
| N36 | The dry-run headline says "cache entr(ies)" | B |
| N37 | An all-zero re-read table collapses into one line | C |
| N38 | tests/test_config.py cites a commit that is not on main | E |
| N39 | The MDM host shares the `<host>` placeholder with the Mac's own name | C |
| N40 | No doc says friction lines logged together share one timestamp | F |
| N41 | The installer's refusal friction line for an old copy has no exit code | D |
| N42 | Prompt v11: friction lines get a naming rule | F |
| N43 | Prompt v11: the agent logs a go-ahead or an answer the person gives | F |
| N44 | Prompt v11: placeholders in fix-request.md renumber across sessions | F |
| N45 | OCR reads a PDF picture stored on its side as stored | E |
| N46 | `.msg` is not converted (olefile, BSD; fails closed under label rules) | E |
| N47 | `.drawio` and `.vsdx` are refused (standard-library converter, with the `.vsdx` label screen) | E |

### B13 build lanes (2026-10-11)

Locus changed to S: the lead's account (`next4`) reached 99% of its weekly limit after the design runs, so each
lane runs as a dispatched session on an account with headroom, in its own worktree, and lands itself through
`/ship` (rebase onto main, gate, CI read back). One owner per primary file; a lane edits another lane's file only
in the hunks its own items need. CONTRACTS sections are reserved per lane (§16.38 to §16.43) so appends do not
collide. Each lane writes its result note to `~/.cache/agentsync-bring-back/b13/lanes/<lane>.md`; the lead folds
the notes into this plan. After A to F land, a rehearsal session plays v11 on the landed commit.

| Lane | Items | Primary files | Account |
|---|---|---|---|
| A purge | N1 N2 N13 N14 N15 | governance.py | next |
| B sync, status, doctor | H23 N3 N4 N5 N11 N16 N19 N22 N23 N24 N26 N27 N28 N30 N34 N36 | cycle.py, loop.py, cli.py, manifest.py, lints.py, arm_local.py, ops/doctor.py | next2 |
| C setup report | H14 H17 to H22 N7 N8 N9 N10 N17 N18 N20 N25 N29 N31 N32 N33 N35 N37 N39 | setup_report.py, install.sh's redaction | next3 |
| D installer | H11 H15 H16 N6 N12 N21 N41 | install.sh outside redaction | next |
| E converters, speech | H10 H12 H13 H24 N38 N45 N46 N47 | convert/, pyproject.toml | next2 |
| F prompt v11 | H1 to H9 N40 N42 N43 N44 | README.md prompt block, test_deploy_pack.py, docs/deploy/ | next3 |

Landed (2026-10-11), every item built, none left as not-built; each lane's note in
`~/.cache/agentsync-bring-back/b13/lanes/<lane>.md` lists per item the sha and the test that fails without it:

| Lane | Landed on main | Gate and CI |
|---|---|---|
| F | `645b607`, `aed2fbd`, `76439e7`, `b479a00` (prompt v11) | green |
| A | `b479a00..a406dbc`, 9 commits; `a406dbc` steadies a trunk test that read free disk space | green |
| C | `a406dbc..e3cf983`, 26 commits | green |
| D | `e3cf983..cd506e5`, 7 commits | green (ci on `f46fd53`, which contains them) |
| E | `5b8330a..f46fd53`, 9 commits | green |
| B | `1d0dcb0..fc3da74`, 17 commits (H23 `5540345`); review added two credential-leak fixes beside N5 (a failed re-read, the safe-save rekey) | green |

Learnings: every review caught at least one real defect in its own lane (A: two purge fixes interacting in a queue
run; C: a git-config regex that read the next section's header as a proxy; F: an empty git proxy registered as a
redaction value). Six lanes pushing to one trunk cost each later lane a rebase and a second gate; a lane silent
for an hour inside its push call was re-running the gate after losing the race, not stuck. Under the ruling the
lanes' "stated, not built" notes are captured items too: they go to the next round with the v11 rehearsal's and a
cross-lane review's findings, until a round finds nothing new.

### B13 round 2: v11 rehearsal, cross-lane review, harvest (2026-10-11)

Run `wf_97bc35ac-295` on `fc3da74` (161 agents, 0 errors): five rehearsals with the real installer in sandbox homes
(new Mac; a field-state Mac at `4a42af9` pulled to v11; v10 and v9 copies refused; a failing `git pull`; a reinstall
while a sync holds the lock, plus the new converters), four cross-lane reviewers (report, core, converters, docs), one
harvest of every lane's not-built notes, then an adversarial verifier per finding. Reports:
`~/.cache/agentsync-bring-back/b13/round2/`; per-item specs `round2/items/R<n>.md` (the verifier's corrected fix is
the one to build); lane map `round2/lanes.json`.

Every rehearsal passed with no name, login or host name in any report; every targeted check read right (the H1 stop
logs a step 1 error and reports "failed at step 1"; N6 waits for the held lock; old copies refused with
`bring-back.md` unchanged; the new converters make pages and fail closed under a label rule). 151 findings, 127
confirmed, 24 refuted: 3 turn, 23 wrong, 101 small; 10 need prompt text, so the prompt the operator pastes is v12.
For 27 the verifier judged any fix makes things worse: the lane builds a fix that does not, or records the measured
reason. The three turn items: an empty subfolder in a chosen project folder keeps its listing incomplete on every
sync (R1; every earlier rehearsal missed it because its HOME sat under the `/tmp` symlink); the git stop never tells
the person to paste the prompt again once git works (R22); a kept upstream deletion (N2) leaves a WAITING ON YOU line
that a queue run cannot clear (R49).

Round 3 lanes (dispatched sessions, CONTRACTS §16.44 to §16.49), items by id in `round2/lanes.json`:

| Lane | Count | Area | Account |
|---|---|---|---|
| A | 8 | purge, governance | next4 |
| B | 29 | cycle, loop, cli, lints, doctor | next2 |
| RA | 21 | report redaction, both reports | next2 |
| RB | 32 | setup report and installer, other | next |
| E | 16 | converters, speech, OCR | next |
| P | 21 | prompt v12, README, docs/deploy | next4 |

Changed at fire time (operator, 2026-10-11): every round 3 lane runs on account next3. The first fires of P and A
(next4) and B (next2) were halted before any edit and fired again on next3; RA, RB and E were fired there first.

### B13 round 3 landed (2026-10-12)

All six lanes landed with CI green; every one of the 127 items is built with a test that fails without it, or
carries a measured not-built reason in its lane note `~/.cache/agentsync-bring-back/b13/lanes/r3-<lane>.md` and in
its CONTRACTS section (§16.44 to §16.49). Prompt v12 is on main (lane P, `9db6ce3..f55256a`).

| Lane | Landed | Not built, with a measured reason |
|---|---|---|
| P | `9db6ce3..f55256a`, 11 commits | R133, R138 |
| RB | `f55256a..d1904e1`, 26 commits | R114, R44, R127, R148 (R60 covered by P) |
| A | `bb545be9`, `7149798e`, `369deedd` | R123, R124 |
| B | `b9bb8ee8` (25 items), `d06690bf` (R75 R76 R48 R74) | R144 (an advisory built instead); R1's exclude command (CLI freeze) |
| RA | `a044eed6..a0e1b038`, 14 commits | R135, R141 |
| E | `dd954d59..9ac78425`, 30 commits (R13 built once with R36) | R130, R140 |

Learnings: the reviews inside the lanes kept finding real regressions (RA's two fresh reviews found four); the
auto-mode classifier refused one lane's `git fetch && git rebase` after a lost push race, which the operator cleared
in that pane. R1's fix holds empty folders only in a real File Provider domain (its root xattr), so a sandbox
rehearsal must mark the provider folder to reach that wait.

Next (round 4, loop until dry): run the round-2 shape again on the landed tip with prompt v12: rehearse v12 (new
Mac; field-state Mac at `4a42af9` pulled to v12; v11 and v10 copies refused; failing `git pull`; reinstall under a
held lock plus the converters; an empty folder in a marked File Provider folder), a cross-lane review of
`9db6ce3..<tip>` in four areas, and a harvest of the round 3 notes' not-built and adjacent items; verify each finding;
build what survives; stop when a round confirms nothing new. Script to adapt: the round-2 workflow
(`wf_97bc35ac-295`, args `{head}`); its rehearsal text names v11 and must say v12.

### B13 round 4: v12 rehearsal, cross-lane review, harvest (2026-10-12)

Scope (frozen): loop until dry. Each round rehearses the current prompt on the landed tip, reviews the previous
round's diff in four areas and harvests the previous lanes' not-built and adjacent items; every finding is verified
by a skeptic; each confirmed finding is built in a dispatched lane (account next3) or carries a measured not-built
reason; repeat until a round confirms nothing new. Only then is the operator told to paste the prompt, once the
README block on origin/main carries that version.

Run `wf_500eacc6-451` on `5c8c3e18` (98 agents, 0 errors): six rehearsals (new Mac; field-state Mac from `4a42af9`;
v11 and v10 copies refused; failing `git pull`; reinstall under a held lock plus every converter including `.potx`;
an empty folder in a provider folder marked as a File Provider domain, with and without the marker, through a
symlink and under `/tmp`), four reviewers of `9db6ce3..5c8c3e18`, one harvest. Every rehearsal passed its
expectations with no name, login or host name in any report, and no finding costs the person a turn. 87 findings, 85
confirmed (15 wrong, 70 small), 2 refuted; 13 carry the verifier's "a fix makes things worse". Three need prompt text
(Q8, Q24, Q25), so the next prompt is v13. Reports and per-item specs: `~/.cache/agentsync-bring-back/b13/round4/`
(`items/Q<n>.md`, lane map `lanes.json`). Note for round 5: the shared fixture tree
`/tmp/agentsync-rehearsal-fixtures/tree` had lost its files; the rehearsals restored copies from round 2's sandboxes.

Locus: the find-and-verify round runs as a Workflow on the lead (ultracode); builds are S, one dispatched session per
lane in its own worktree, landing itself through `/ship`. Lead succession point: after all lanes land, with this
section as the bridge.

Round 4 lanes (dispatched sessions on next3, CONTRACTS §16.50 to §16.55; common brief `lanes/COMMON3.md`):

| Lane | Count | Area |
|---|---|---|
| A | 7 | purge, governance, contracts test |
| B | 17 | doctor, loop, paths, arm_local, publish, lints, skill |
| RA | 17 | setup_report.py |
| RB | 16 | install.sh (step 2 order, the shell report, the reinstall wait) |
| E | 12 | converters, speech, OCR watcher, test infrastructure |
| P | 14 | prompt v13, README, docs/deploy, older CONTRACTS lines |

The lead built Q85 (the lane briefs warn against `git stash` and the words check-names flags) and Q86 (round 3's
scratch folders, landed worktrees and merged branches; the three branches not on main go to the operator).

### B13 round 4 landed (2026-10-12)

All six lanes landed with CI green and none left an item not built; each lane note
`~/.cache/agentsync-bring-back/b13/lanes/r4-<lane>.md` lists per item the sha and the test that fails without it.
Prompt v13 is on main (lane P, `c09e66d8`).

| Lane | Landed | Note |
|---|---|---|
| P | `ffb42632..c09e66d8`, 3 commits | a new test fails CONTRACTS text that cites a test name that does not exist |
| A | `92e31ba9`..`a74d7e7f`, 6 commits | |
| RA | `3ff4e315..5d8195a6` | |
| E | `5d8195a6..4f61520e` | Q52 and Q79 built once |
| RB | `f70183ac..c9a5d359`, 10 commits | |
| B | `c9a5d359..645f54e5`, 16 commits | adds a hidden `exclude-empty <source>` command, named by the empty-folder wait (R1's turn on a real File Provider domain) |

Learnings: a lane's `git push` stopped at an "ask" permission prompt (the push line held a variable the check
could not read) and waited for the operator's Yes in its pane. Lane A's backup-branch cleanup deleted five other
sessions' `ship/backup-*` branches (refs are shared across worktrees); it recreated them at the same shas. The fire
gate (payload-lint F3) now needs the lead's registry name (`cc-notify <slug>-<pane>`) in each brief on kitty, not
the bare pane number.

Round 5: the round 4 workflow again on `645f54e5` with prompt v13 (run `wf_565db0bd-e51`, outputs
`~/.cache/agentsync-bring-back/b13/round5/`).

### B13 round 5: v13 rehearsal, cross-lane review, harvest (2026-10-12)

Run `wf_565db0bd-e51` on `645f54e5` (113 agents, 0 errors; script `round5/round5-workflow.js`, the round 4 script
with v13, base `ffb42632` and the r4 notes). Every rehearsal met its expectations with no name, login or host name
in any report; every round 4 fix the rehearsals could reach read right on real output (R1's wait now names
`exclude-empty`). 102 findings, 99 confirmed (20 wrong, 79 small, none a turn), 3 refuted; 14 carry "a fix makes
things worse". Five need prompt text (F24 to F28), so the next prompt is v14. Specs `round5/items/F<n>.md`, lane map
`round5/lanes.json`, common brief `lanes/COMMON4.md`, CONTRACTS §16.56 to §16.61.

Convergence: the confirmed count went 127 (round 2), 85 (round 4), 99 (round 5); turns 3, 0, 0; wrongs 23, 15, 20.
Most new findings are text drift created by the previous round's fixes (bare `agentsync` in new fix text, docs that
lag a converter change, wording around a new wait), so each round's diff seeds the next round's review. The loop
stays the operator's frozen scope; this trend is recorded for the stop decision.

| Lane | Count | Area |
|---|---|---|
| A | 9 | purge preview, exclude-empty lock, absent-file alarms, contracts stub test, bare commands in cycle/governance |
| B | 18 | loop waits, doctor lines, arm_local alarm, config, cli sync line |
| RA | 25 | setup_report.py (Summary, stopped report, org registration, joined-name rule) |
| RB | 14 | install.sh (shell fallback report, stopped-copy report, newer-copy message) |
| E | 14 | `.msg` stubs and their re-render, canonical hash, pptx templates, speech seed |
| P | 18 | prompt v14, docs/deploy, CONTRACTS cross-references |

The lead built F102 (round 4's seven landed worktrees and branches removed with `git worktree remove` and
`git branch -d`, no force; the three off-main branches stay with the operator's cleanup step).

### B13 round 5 landed (2026-10-12)

All six lanes landed with CI green and none left an item not built (notes `lanes/r5-<lane>.md`). Prompt v14 is on
main (lane P, digest `06eea5787a14`). The operator ran the round 3 cleanup (no b13 branch or worktree from rounds 1
to 4 remains).

| Lane | Landed |
|---|---|
| P | `065bdfe4..868ea775` |
| B | `cb4b5e80..f21974ac`, 10 commits (F96 is lane P's F89: P's fix, B's test) |
| E | `5534e047..ed1b1f96`, 7 commits |
| A | `2bfa59c6..dfd21162`, 7 commits (its widened stub test caught lane B's new `CycleReport` fields) |
| RB | `3a89922a..201134c5` (F16 also builds RA's F7) |
| RA | `ed1b1f96..87d0d090`, 16 commits |

Learning: lanes that share a file now split hunks by message through the lead before they edit (RB and RA on the
stopped-report awk, A and RA on bare commands in `setup_report.py`); no rebase conflict followed.

Round 6: the round 5 workflow again on `87d0d090` with prompt v14 (run `wf_4724e367-0da`, outputs `round6/`).

### B13 round 6: v14 rehearsal, cross-lane review, harvest (2026-10-13)

Run `wf_4724e367-0da` on `87d0d090`: every finder finished, then 10 of 83 verifiers died on next3's weekly limit
and the script's post-processing crashed on their empty slots. The lead session moved to next4 by
`/limit-recover`; the 73 finished verdicts were read from the run's journal and only the 10 lost ones re-ran
(`wf_4b842dbe-8a7`), so nothing was spent twice. Every rehearsal met its expectations with no name, login or host
name in any report. 83 findings, 79 confirmed (1 turn, 15 wrong, 63 small), 4 refuted; 13 carry "a fix makes things
worse". The turn (G15): when the installer names the checkout as the cause of a newer-copy stop, the prompt's
words send the person the other way. Eight need prompt text, so the next prompt is v15. Specs `round6/items/G<n>.md`,
lane map `round6/lanes.json`, brief `lanes/COMMON5.md`, CONTRACTS §16.62 to §16.67.

Convergence: confirmed 127, 85, 99, 79 over rounds 2, 4, 5, 6; turns 3, 0, 0, 1; wrongs 23, 15, 20, 15. Two of the
wrongs are new by construction: a stub re-rendered and then re-read in the same sync is counted twice (G7, G28).

| Lane | Count | Area |
|---|---|---|
| A | 4 | label-escalation purge, lock-wait warning, cycle alarms |
| B | 12 | loop waits, doctor, arm_local and config under symlinks, cli help text (and G43's loop.py docstring) |
| RA | 19 | setup_report.py (context-mode redaction, Summary, friction section) |
| RB | 17 | install.sh (stopped-copy and newer-copy reports, shell fallback) |
| E | 10 | stub re-render counted twice, `.msg` and `.potx` stubs, speech seed tests |
| P | 15 | prompt v15, docs/deploy, CONTRACTS lines |

Lead: G43 (the decision `71d3e66ef726` entry in `docs/research/corporate-bring-back-2026-10-08.md` gets a dated
correction; round 7's scenario F expects the exclude-empty wait, not a paste line) and G83 (round 5's last
worktree removed; the dated-line and shared-pair rules are in COMMON5).

Waiting on the account: the operator's ruling sends every lane to next3, which is at 100% of its weekly limit until
2026-10-13 12:00 UTC. The lanes fire at that reset.

CORRECTED (2026-10-13): the operator replaced the next3 ruling: "we use all of our accounts agnostic to any
functionality / config, its just for usage across the board." Every lane gets the same model and effort; the account
only decides whose quota pays. Round 6's lanes fired at 07:00 UTC on next (panes 402 to 407); from round 7 lanes
fire with `--account auto`.

### B13 round 6 landed (2026-10-13)

All six lanes landed; CI green on `52b86d93` (prompt v15). Notes `lanes/r6-<lane>.md`.

| Lane | Landed | Not built |
|---|---|---|
| A | `4c19d9e5`, `9875bb32`, `30c81f68` | none (G58 is behavior-identical, so no failing test; reason in the note) |
| E | `95eadf7e..0bda2d78` (G7 and G28 one fix; stub render version 4) | none |
| B | `f26a1368..03a9431c`, 14 commits (with G43's docstring) | none |
| P | `e2e80ed4`, `26ae33a7`, `3304993e`, `986b55de` (v15, with lane RB's words) | none |
| RA | `2af947f9..0dd77157`, 16 commits | none |
| RB | `174f5637..52b86d93`, 12 commits | G71 (lane RA's G46 made that organisation branch live again, measured; §16.65) |

Learnings: lane RA deleted every lane's `/ship` backup branch with a glob (the third round with this incident); no
work was lost, the lanes still landing were told to recreate theirs, and the lane brief (`lanes/COMMON.md`) now
forbids deleting a branch by pattern. Lanes waiting on a full gate under load look idle in their panes; read the
pane before suspecting a stall.

Round 7: the round 6 workflow again on `52b86d93` with prompt v15 (run `wf_08dc4330-aab`, outputs `round7/`); its
scenario F now expects the exclude-empty wait (G43), and its post-processing skips an empty verify slot.

### B13 round 7: v15 rehearsal, cross-lane review, harvest (2026-10-13)

Run `wf_08dc4330-aab` on `52b86d93` (90 agents, 0 errors). 79 findings, 77 confirmed (5 turn, 15 wrong, 57 small),
2 refuted; 15 carry "a fix makes things worse"; 10 need prompt text, so the next prompt is v16.

The rehearsal caught a regression round 6 shipped: on every Mac set up before round 6 (the corporate Mac included),
v15's step 1 marks no folder as already synced, so an unattended run stops at the folder question (H7, H15, H27,
H38: install.sh's synced-folder reader imports `agentsync.paths.provider_link_resolved` through the installed
agentsync, which is older and lacks it). v15 must not be pasted until this is fixed. The fifth turn (H19): a checkout
with a local commit leaves step 1 on a branch with no upstream. The lane brief (`lanes/COMMON6.md`) now requires
install.sh's Python to fall back when the installed build lacks a new name.

Convergence: confirmed 127, 85, 99, 79, 77 over rounds 2 and 4 to 7; turns 3, 0, 0, 1, 5.

| Lane | Count | Area |
|---|---|---|
| RB | 15 | install.sh: the step 1 regression first, the shell report |
| RA | 19 | setup_report.py (Summary, outcome labels, redaction) |
| B | 12 | loop waits, cycle lock-wait output, cli counts, paths under `/tmp`, STATE.md |
| E | 5 | the `.msg` re-read on a policy change, QUARANTINE.tsv under OCR, tombstone re-render, the timing skip |
| P | 10 | prompt v16 |
| D | 15 | docs/deploy and CONTRACTS cross-references |

Lanes fire with `--account auto` (CONTRACTS §16.68 to §16.73). Lead: H79 (round 6's last worktree removed; round 5
and 6 `/tmp` scratch goes to the operator, since deletion is refused to agents).

### B13 round 7 landed (2026-10-13)

All six lanes landed and none left an item not built; CI green on `a350196b` (prompt v16). Notes `lanes/r7-<lane>.md`.
The regression fix (H7) is `77151d37`: step 1 marks synced folders again with any installed agentsync from
2026-09-29 on, which covers the corporate Mac's `4a42af9` (2026-10-08). The operator ran the `/tmp` scratch cleanup
for rounds 5 and 6; round 7's scratch goes in the operator's `b13-r7` cleanup (round 8 J81).

| Lane | Landed |
|---|---|
| E | `042cbe35..ba661677` |
| RB | `77151d37` (H7), `54cb9a0c`, `7cfed4d2`, `c1a24093`, `a350196b` |
| P | `77151d37..69c873d2` (v16) |
| RA | `69c873d2..0bc75885`, 15 commits |
| D | `85d7e292..1d8d4c95`, 11 commits |
| B | `3ece9aed..265a121d` |

Learnings: every lane's `git push` stopped at the "ask" rule on `git push` and waited hours for the operator's Yes;
only the first of several queued pushes lands, the rest rebase and ask again. A load spike near 1000 killed one
lane's gate. Left in place: branch `b13/r7-rb-dev` (one commit git cannot match patch-for-patch; lane RB says its
content landed folded into `54cb9a0c`) and a pushed backup branch `origin/b13/r7-d` (lane D's pre-rebase copy).

Round 8: the round 7 workflow on `a350196b` with prompt v16 (run `wf_70c4dec6-8b1`, outputs `round8/`); scenario B
now expects step 1 to mark the field Mac's folders synced (H7).

### B13 round 8: v16 rehearsal, cross-lane review, harvest (2026-10-14)

Run `wf_70c4dec6-8b1` on `a350196b` (92 agents, 0 errors). The field Mac's path is clean: a Mac at `4a42af9` pulled
to v16 ran with no stop, step 1 marked both folders synced on the old build (H7 holds), outcome "fully one command".
81 findings, 79 confirmed (2 turn, 12 wrong, 65 small), 2 refuted; 5 need prompt text, so the next prompt is v17.
Both turns are rarer step 1 paths: an uncommitted edit on a branch with no tracking information loops (J7), and a
folder at the checkout path that is not a git checkout gets the proxy advice (J19).

Convergence: confirmed 127, 85, 99, 79, 77, 79 over rounds 2 and 4 to 8; turns 3, 0, 0, 1, 5, 2; wrongs 23, 15,
20, 15, 15, 12. The count does not fall: each round's fixes bring new text and paths that the next review finds.
The stop question is filed for the operator as a decision.

Lanes (`--account auto`, CONTRACTS §16.74 to §16.79, brief `lanes/COMMON7.md`): RA 22 (setup_report.py), RB 18
(install.sh), P 8 (prompt v17), B 7 (cli, loop, doctor), E 9 (cycle.py, publish.py, governance.py: purge and stub
re-render), D 13 (docs/deploy, CONTRACTS). Lead: J32 (round 9's scenario F expects the STATE.md pointer, not
`sync -v`), J81 (round 7's worktrees; the pushed backup branch and old scratch go to the operator).


## Outlook and the stop rule for prompt versions (2026-10-09; operator ruled 2026-10-10: build everything, see B13)

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

Update (2026-10-10, B12): the v10 paste ran clean, the second clean field run after v9. By rule 3, setup is done
unless the operator rules otherwise. That Mac now runs `4a42af9`, which has the scope-change guard, so the printed
`exclude` line is safe to paste there. Decision `085fac870dd6` is moot on that Mac (0 online-only images in three
reports). The operator's open items, in order, are in the B12 triage, "Operator actions, in order".

Ruling (operator, 2026-10-10): "why or why not are we not implementing said improvements and fixes that we've
documented and captured ... There is zero cost for us to go and implement. There is immense cost for inaction."
The proposed rule is replaced. Building costs the operator nothing; only a paste does. So:
1. Every captured item is built, or carries a stated reason it would make things worse. No item is held for want
   of a prompt version. One ledger (B13) holds every item to that end state.
2. Prompt-wording items ship together as one version (v11), rehearsed on the landed commit before the go-ahead;
   the operator pastes it once, as the update the Mac takes anyway.
3. Field rounds after v11 triage every finding the same way: build it, or record why not.
Decision `085fac870dd6` was closed as actioned 2026-10-10 (no download; moot on that Mac).

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
