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
| E | `042cbe35^..ba661677`, 6 commits |
| RB | `77151d37` (H7), `54cb9a0c`, `7cfed4d2`, `c1a24093`, `a350196b` |
| P | `77151d37..69c873d2` (v16) |
| RA | `69c873d2..0bc75885`, 15 commits |
| D | `85d7e292^..1d8d4c95`, 11 commits |
| B | `3ece9aed^..265a121d`, 10 commits |

Ranges are git `A..B`, A excluded; corrected by round 9 K71.

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

### B13 round 8 landed (2026-10-14)

All six lanes landed; CI green on `e440422c` (prompt v17). Notes `lanes/r8-<lane>.md`. One sub-part is not built,
with a measured reason: J69's shell friction-line fallback (the prompt has not logged the folder question since v6).
Lead items: J32 is in round 9's scenario F; J81 corrected the round 7 table (`7cceecbc`) and added the pipefail line
to `lanes/COMMON.md`.

| Lane | Landed |
|---|---|
| P | `b5798d0d..b9f4c98b`, 2 commits (v17) |
| D | `b9f4c98b..27f9c1a0`, 11 commits |
| B | `3ed3541e^..1713a687`, 6 commits |
| RB | `1713a687..affa5833`, 15 commits |
| E | `affa5833..8285d7c5`, 8 commits |
| RA | `aa809aa7^..e440422c`, 11 commits |

Ranges are git `A..B`, A excluded (round 9 K71).

Learnings: a lane whose push waits at the "ask" prompt is rejected once another lane lands first, then rebases,
re-runs the gate and asks again, so each queued push after the first costs the operator another Yes. Left in place: branch
`b13/r7-rb-dev` and `origin/b13/r7-d` (the operator's `b13-r7` cleanup deletes the remote one).

Round 9: the round 8 workflow on `e440422c` with prompt v17 (run `wf_0f6ec280-91a`, outputs `round9/`); scenario F
expects the wait to point at `_sync/STATE.md`, never at `sync -v`, plus a six-folder variant for the alarm (J32).

### B13 round 9: v17 rehearsal, cross-lane review, harvest (2026-10-14)

Run `wf_0f6ec280-91a` on `e440422c` (93 agents, 0 errors). All six scenarios pass their EXPECTED; the field Mac's
path (scenario B) is clean again: v10 build pulled to v17, step 1 marked both folders synced, no stop, "fully one
command". 82 findings, 80 confirmed (4 turn, 10 wrong, 66 small), 2 refuted; 9 need prompt text, so the next prompt
is v18. The turns are two defects: an unattended agent still stops for a folder answer when step 1 cannot read an
existing config (K9, K20 and K32, one defect), and a checkout whose `.git` is empty gets the proxy advice (K25).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80 over rounds 2 and 4 to 9; turns 3, 0, 0, 1, 5, 2, 4; wrongs 23,
15, 20, 15, 15, 12, 10. Wrongs fall slowly; the total does not. Neither paste rule in the operator's open stop
question is met this round (it has turns and wrongs).

Lanes (`--account auto`, CONTRACTS §16.80 to §16.85, brief `lanes/COMMON8.md`): RA 20 (setup_report.py), RB 16
(install.sh), P 11 (prompt v18), B 9 (cli, loop, lock, doctor), E 8 (stubs, front matter, purge), D 12 (docs/deploy,
CONTRACTS). Lead: K39 and K44 (round 10's scenario E and F wording), K71 (exact ranges in the landed tables), K81
(round 7's local branch; round 8's scratch goes to the operator).

### B13 round 9 landed (2026-10-14)

All six lanes landed; CI green on `3ee72827` (prompt v18). Notes `lanes/r9-<lane>.md`. Not built, each with a
measured reason: K77 (the tool-path skip would leak `/opt/<company>/...` paths the old code redacted), K27's form
of the rewrite-path fix (it leaks two-letter paths under the shell's floor; K2's form is built) and K79 (kept, with a
pinning test `086ccb8a`). Lead: K39 and K44 are in round 10's scenarios E and F; K71 fixed the ranges (`f753c3b7`);
K81's branch delete was refused for the agent and is in the operator's `b13-r8` cleanup.

| Lane | Landed |
|---|---|
| P | `bc773369`, 1 commit (v18) |
| B | `f753c3b7..a84e7186`, 7 commits |
| D | `bc773369..7d35f97d`, 11 commits |
| RB | `f247ea4d^..da658e5b`, 9 commits |
| E | `7d35f97d..e88d24fd`, 8 commits |
| RA | `da658e5b..3ee72827`, 17 commits |

Learnings: four lanes reached their push within the same half hour and the prompts waited about six hours for the
operator; once answered, the later lanes rebased onto the earlier ones before landing. Lane D's widened dated-line test
(K80) caught a missing dated line in lane P's section; it now covers §16.68 to §16.85.

Round 10: the round 9 workflow on `3ee72827` with prompt v18 (run `wf_cc5fd9e4-b48`, outputs `round10/`).

### B13 round 10: v18 rehearsal, cross-lane review, harvest (2026-10-14)

Run `wf_cc5fd9e4-b48` on `3ee72827` (87 agents, 0 errors). All six scenarios pass their EXPECTED; the field Mac's
path is clean (v10 build pulled to v18, no stop, "fully one command"), and round 9's turn group (K9, K20, K32) holds:
an unreadable config now runs unattended with 0 turns. 76 findings, 74 confirmed (1 turn, 11 wrong, 62 small), 2
refuted; 11 need prompt text, so the next prompt is v19. The turn: a working checkout whose `.git` is a file (a
separate git dir or a worktree) fails step 1's `.git/HEAD` test (L27). The worst wrong: in a home that is itself a
git repo, a checkout with a broken `.git/HEAD` lets git climb to the home repo, and the no-tracking command then
commits the home's untracked files there (L6).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74 over rounds 2 and 4 to 10; turns 3, 0, 0, 1, 5, 2, 4, 1;
wrongs 23, 15, 20, 15, 15, 12, 10, 11. Neither paste rule in the operator's open stop question is met.

Lanes (`--account auto`, CONTRACTS §16.86 to §16.91, brief `lanes/COMMON9.md`): RA 19 (setup_report.py), RB 15
(install.sh), P 10 (prompt v19), B 12 (loop, cli, arm_local, lock, doctor), E 5 (OCR timing, stubs, recordings), D 12
(docs/deploy, CONTRACTS). Lead: L76 (round 9's scratch and the v17 sandboxes go to the operator, `b13-r9` cleanup).

### B13 round 10 landed (2026-10-14)

All six lanes landed; CI green on `12147e30` (prompt v19). Notes `lanes/r10-<lane>.md`. Not built, with a measured
reason: L61's `--git-dir` form (it skips git's ownership check; L6's ceiling is built instead). Lane RB sent L11's
prompt words, built in v19. Trunk was red for about an hour between `93cedc51` and `12147e30`: two v19 tests passed
locally but not on the CI runner's git; lane P fixed the tests.

| Lane | Landed (lane commits; ranges can hold another lane's) |
|---|---|
| E | `54be463a^..e44f41ea`, 5 commits |
| D | `2ad3579f^..1991d6a2`, 8 commits |
| B | `340f048a^..761d6b59`, 7 commits |
| RA | `5a7b7e5b^..eb7cf226`, 12 commits |
| RB | `db842d45^..88e62d5c`, 8 commits |
| P | `93cedc51` (v19), `12147e30` (CI test fix) |

Learnings: a hook prompt on lane P flagged `git reset --hard` from text inside a heredoc; the lane had about 200
lines uncommitted, so the operator answered No and the lane committed first. A lane's tests can pass locally and
fail on the CI runner's git, so the lane that lands a test owns the read-back of CI on main.

Round 11: the round 10 workflow on `12147e30` with prompt v19 (run `wf_5c8991f7-17f`, outputs `round11/`).

### B13 round 11: v19 rehearsal, cross-lane review, harvest (2026-10-15)

Run `wf_5c8991f7-17f` on `12147e30` (98 agents, 0 errors). All six scenarios pass their EXPECTED; the field Mac's
path is clean (v10 build pulled to v19, no stop, "fully one command"). 87 findings, all 87 confirmed (1 turn, 17
wrong, 69 small); 11 need prompt text, so the next prompt is v20. The turn: on a Mac that already syncs folders, a
signed-out OneDrive sends the agent to the folder question (M4). Most wrongs sit on probe paths the lanes asked for: a
config the installed agentsync fails on, a checkout whose `.git` is a file, and a proxy host from an earlier friction
line reaching the public report (M29). Rehearsal safety: building the v10 before-state wrote SwiftPM caches into the
real home (M20); round 12's scenario B copies built helpers instead.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87 over rounds 2 and 4 to 11; turns 3, 0, 0, 1, 5, 2, 4, 1,
1; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17. Turns have stayed at one for two rounds; the total has not fallen.
Neither paste rule in the operator's open stop question is met.

Lanes (`--account auto`, CONTRACTS §16.92 to §16.97, brief `lanes/COMMON10.md`, which adds a pre-push read of
trunk's CI, M87): RA 22 (setup_report.py), RB 20 (install.sh), P 10 (prompt v20), B 15 (arm_local, loop, cli,
doctor), E 8 (recordings, speech, OCR, publish), D 9 (docs/deploy, CONTRACTS). Lead: M20 (round 12's scenario B
method), M86 (one operator cleanup, `b13-r10`, replacing the round 7 and round 9 ones), M87 (COMMON10).

### B13 round 11 landed (2026-10-16)

All six lanes landed; CI green on `8814baa0` (prompt v20). Notes `lanes/r11-<lane>.md`. Not built, with a measured
reason: M44's part b (install.sh parses "(deletions held)" and cli.py swaps that note by equality, so one bracket
would break both). The operator's stop question (`eb3dbe1e5e94`) expired unanswered on 2026-10-15, so its default
holds: keep looping until a round confirms nothing.

| Lane | Landed (lane commits; ranges can hold another lane's) |
|---|---|
| E | `f85bd80d..453e2b42`, 5 commits |
| B | `5ffecbbe^..93b42c42`, 6 commits |
| RA | `93b42c42..7c41eaba`, 6 commits |
| P | `0189665d` (v20, with lane RB's M4 words) |
| RB | `0189665d..e3c09b12`, 17 commits |
| D | `e3c09b12..8814baa0`, 5 commits |

Learnings: kitty crashed at 00:01 on 2026-10-15 before any round 11 lane had fired; the lead came back as window
20, so the briefs and `--notify-back` had to name the new window, and `cc-await-ping` needs the window id as its
first argument. The classifier refused lane RB's rebase and cherry-pick until the operator typed an approval into
the lane's own pane. A queued push was rejected each time an earlier lane landed, as in rounds 8 to 10.

Round 12: the round 11 workflow on `8814baa0` with prompt v20 (run `wf_af97f220-5f1`, outputs `round12/`); scenario B
copies built helpers instead of building the v10 ones (M20).

### B13 round 12: v20 rehearsal, cross-lane review, harvest (2026-10-16)

Run `wf_af97f220-5f1` on `8814baa0` (91 agents, 0 errors). All six scenarios pass their EXPECTED, and the field
Mac's path is clean (v10 build with copied helpers, pulled to v20, no stop, "fully one command"). 80 findings, 76
confirmed (3 turn, 14 wrong, 59 small), 4 refuted; 8 need prompt text, so the next prompt is v21. All three turns are
one regression from round 11's new sign-in exit (M4): it fires on a Mac that is signed in when a synced folder was
only renamed or moved, or when the installed build is current and the config is unreadable, and v20 then asks for
a sign-in (N29, N41, N61; N53 is the same in doctor). Rehearsal safety: scenario E's v10 build also wrote the real
home's SwiftPM cache (N77), and a real-home mtime check cannot tell scenarios apart (N19); round 13's kit copies
helpers for every pre-`8b47c900` build and checks each sandbox's own TMPDIR instead.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76 over rounds 2 and 4 to 12; turns 3, 0, 0, 1, 5, 2, 4,
1, 1, 3; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14. The total has held between 74 and 99 for nine rounds. The
operator's stop question expired unanswered, so the loop goes on to a round that confirms nothing.

Lanes (`--account auto`, CONTRACTS §16.98 to §16.103, brief `lanes/COMMON11.md`, which adds a both-states test rule
for any new stop or exit): RA 19 (setup_report.py), RB 14 (install.sh, the sign-in exit), P 6 (prompt v21), B 14
(loop, arm_local, cli, doctor), E 7 (cycle counts, speech, publish), D 13 (docs/deploy, CONTRACTS). Lead: N19 and
N77 (round 13's kit), N80 (one operator cleanup, `b13-r11`, replacing the `b13-r10` one).

### B13 round 12 landed (2026-10-16)

All six lanes landed; CI green on `2551fd70` (prompt v21). Notes `lanes/r12-<lane>.md`. No item is recorded as making
things worse; a few optional extras the verifiers offered were left out with a reason in the notes (N6's no-report
variant, N53's "Add shortcut to My files"). The sign-in exit regression (N29, N41, N61) is fixed in install.sh and
v21, with guide and pin follow-ons for lane D's new text. N5 is split: prompt half in v21 (`2c583924`), report half
by lane RA (`f2a97b10`).

| Lane | Landed (lane commits; ranges can hold another lane's) |
|---|---|
| B | `48ca7e9c..c3b1915b`, 7 commits |
| D | `0089aa9f^..579261da`, 7 commits |
| P | `2c583924` (v21, with lane RB's words) |
| RB | `2c583924..6dc84f79`, 13 commits |
| E | `e5fd9f5d^..c2130fdb`, 4 commits |
| RA | `c2130fdb..2551fd70`, 19 commits |

Learnings: lane D's new completeness test (every dated line from §16.92 on must be claimed or pinned) made each later
lane add pins at rebase; relaying it to the lanes still building kept their gates green. A lane that finishes early
cannot take a later sha from another lane, so cross-lane splits are recorded here.

Round 13: the round 12 workflow on `2551fd70` with prompt v21 (run `wf_54651985-298`, outputs `round13/`); scenarios B
and E copy built helpers for every pre-`8b47c900` build and check their own TMPDIR (N19, N77).

### B13 round 13: v21 rehearsal, cross-lane review, harvest (2026-10-17)

Run `wf_54651985-298` on `2551fd70` (85 agents, 0 errors). All six scenarios pass their EXPECTED with no turn on any
rehearsed path; the field Mac's path is clean (v10 build with copied helpers, pulled to v21, no stop, "fully one
command"), and the kit's own-TMPDIR check found no SwiftPM run in B or E. 74 findings, all 74 confirmed (1 turn, 20
wrong, 53 small); 6 need prompt text, so the next prompt is v22. The turn is from code review: `status` reads every
cloud source's provider folder before its timed listing, so a slow provider can stall it (O47). Most wrongs are one
cluster around round 12's sign-in stop: both reports call any stop "OneDrive is not signed in" whatever the app
(O1, O5, O29, O39, O42, O58, O66), a SharePoint library that is only unsynced reads as signed out (O41, O48, O52,
O57), and which click counts as the sign-in (O40, O43, O59).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74 over rounds 2 and 4 to 13; turns 3, 0, 0, 1, 5, 2,
4, 1, 1, 3, 1; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20. First round with no turn on any rehearsed path.

Lanes (`--account auto`, CONTRACTS §16.104 to §16.109, brief `lanes/COMMON12.md`): RA 24 (setup_report.py, most of
them the sign-in wording cluster), RB 19 (install.sh), P 6 (prompt v22), B 11 (status, loop, arm_local, doctor), E 5
(deferred counts, speech, converters, policy), D 8 (docs/deploy, CONTRACTS). Lead: O74 (one operator cleanup,
`b13-r12`, replacing the `b13-r11` one).

### B13 round 13 landed (2026-10-17)

All six lanes landed; CI green on `dd06f1ed` (prompt v22). Notes `lanes/r13-<lane>.md`. Every item is built; the
parts left out are optional extras the verifiers offered, each with a reason in the lane's note (O9's Summary
clause, O15's NEXT-side variant, O24's `--log-start` end-line guard, O53's fallback, O57's optional 6). O73's
load-timing flakes got a test-only fix; no permanent test reproduces a load-dependent flake.

| Lane | Landed (lane commits; ranges can hold another session's) |
|---|---|
| E | `fd8455e5^..7b51f3cf`, 5 commits |
| RB | `ec7a22c0^..44360797`, 17 commits |
| P | `b28a873d` (v22) |
| B | `b28a873d..eabb67fc`, 4 commits |
| D | `02a68add`, `6916db07` |
| RA | `5b1f10f1..dd06f1ed`, 13 commits |

Outside the wave, another session landed `e72a536d..5b1f10f1` (perf work and `scripts/gate.sh`, the land gate with
`--affected` and `--lane` modes); the round 13 brief now lives in the repo as `docs/lanes/COMMON.md`.

Learnings: three lanes running the full `pytest -n auto` at once (about 30 workers, load about 350) filled swap, and
lane D's pytest controller was SIGKILLed twice (exit 137). From the second half of round 13, lanes rebase and gate
under one lock (`lockf /tmp/b13-r13-land.lock …`); the push stays a separate command, outside the lock, so the
operator's `git push` ask rule still sees it. The lock narrows the race but does not close it: RA's push was rejected
twice, the second time by lane D, which landed seconds after RA's locked gate. With several lanes at push prompts,
the lead names the one whose branch fast-forwards. The operator was away overnight, so lane RA waited about 6 hours at
a prompt.

Round 14: the round 13 workflow on `dd06f1ed` with prompt v22 (ids Q<n>, outputs `round14/`).

### B13 round 14: v22 rehearsal, cross-lane review, harvest (2026-10-17)

Run `wf_4e8ebde4-285` on `dd06f1ed` (96 agents, 0 errors). All six scenarios pass their EXPECTED with no turn on any
rehearsed path; the field Mac's path is clean again (v10 build with copied helpers, pulled to v22, no stop, "fully
one command"). 85 findings, 84 confirmed (0 turn, 17 wrong, 67 small), 1 refuted; 9 need prompt text, so the next
prompt is v23. The review also covered the perf commits another session landed in the range (`e72a536d..5b1f10f1`).
Most wrongs sit on two cross-lane seams of round 13: lane B's new empty-SharePoint-library doctor fix is not a
person's fix to lane P's `persons_fails`, so step 2 tells the agent to fix it and re-run (Q45, Q54, Q63); and
`status`'s NEXT on a person's [FAIL] tells the reader to do the fix and differs from a sync's, though v22 step 3 says
they print the same line (Q1, Q9, Q33). Others: the converters run the native pandoc while doctor, status and
install.sh check and warm the wheel's copy (Q56, Q60, Q76), the native route never runs under the test gate (Q61),
and the guide still keeps a late step 1 line in the finished attempt (Q64, Q77).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84 over rounds 2 and 4 to 14; turns 3, 0, 0, 1,
5, 2, 4, 1, 1, 3, 1, 0; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17. Second round in a row with no turn on a
rehearsed path, and the first with no turn anywhere.

Lanes (`--account auto`, CONTRACTS §16.110 to §16.115, brief `lanes/COMMON13.md`, mirrored in `docs/lanes/COMMON.md`,
which adds the land lock): RA 20 (setup_report.py), RB 18 (install.sh, persons_fails), P 7 (prompt v23), B 17
(status NEXT, waits, alarm counts, doctor), E 6 (native pandoc in doctor and tests, the load-timing test), D 14
(docs/deploy, README Install, CONTRACTS). Lead: Q84 (round 15's kit copies helpers for every build before
`7b51f3cf` and fails on any real-home SwiftPM lock) and Q85 (one operator cleanup, `b13-r13`, replacing the `b13-r12`
one).

### B13 round 14 landed (2026-10-18)

All six lanes landed; CI green on `ba3d1f10` (prompt v23). Notes `lanes/r14-<lane>.md`. Every item is built; parts
left out are optional extras with a reason in the note (Q2's second option, Q30's public pointer, Q17's separate
not-on-disk clause, lane B's "ran no sync" end line). Lane B records one residual for round 15's rehearsal: under
`--confirm-install-agent`, a first sync that exits 0 followed by a failed background wait exits 1 with no closing
status, so the heartbeat note's "the end of this run says" gets no answer. CI flaked once on lane RB's pandoc
first-start test (Q40; run `39081380800`, attempt 2 green). The lead's Q85 cleanup ran (operator, 2026-10-17): every
round 5 to 13 leftover and `origin/b13/r7-d` are gone.

| Lane | Landed (lane commits; ranges can hold another session's) |
|---|---|
| RB | `c57cfa7c..7dec0347`, 12 commits |
| RA | `7dec0347..37a4f567`, 15 commits |
| E | `37a4f567..696e37f7`, 4 commits |
| P | `e24212be`, `326d3083` (v23) |
| D | `326d3083..f675585a`, 8 commits |
| B | `f675585a..ba3d1f10`, 6 commits |

Outside the wave, another session landed `06733df7`: `scripts/ship-land.sh`, one locked fetch, rebase, gate, trunk
CI check and push that a user-level allow rule runs with no prompt. From round 15 lanes land with it
(`--lock /tmp/b13-r15-land.lock`), which ends the push-prompt waits.

Learnings: the lockf around the gate alone did not stop rejected pushes, because each push ran after the lock was
released; a mkdir mutex held from fetch through the push did, once every lane took it, but each push still waited on
the operator (lane D about 4 hours). The auto-mode classifier refused `git -C … push` and one locked rebase-and-gate
retry; each cleared only when the operator typed an approval in the lane's pane. Load reached about 435, and a gate
was killed once more.

Round 15: the round 14 workflow with prompt v23 on `06733df7`, BASE `c57cfa7c` (ids S<n>, outputs `round15/`); every
scenario copies helpers for any build before `7b51f3cf` and fails on a real-home SwiftPM lock (Q84).

### B13 round 15: v23 rehearsal, cross-lane review, harvest (2026-10-18)

Run `wf_c57f23f2-473` on `06733df7` (82 agents, 0 errors; a first launch died on an unescaped `${P}` in the script).
All six scenarios pass their EXPECTED with no turn and no wrong on any rehearsed path; the field Mac's path is clean
(v10 build with copied helpers, pulled to v23, "fully one command"), and no sandbox holds a lock named for the real
home (Q84's kit rule). 71 findings, 68 confirmed (0 turn, 10 wrong, 58 small), 3 refuted; 5 need prompt text, so the
next prompt is v24. The wrongs come from probes and review, not the rehearsed paths: a status that exits non-zero with
no [FAIL] line still gets install.sh's "fix the [FAIL] lines above" (S33, S58, S62), a patch made readable inside one
attempt is sent again (S34), status with a mirrored source's block deleted (S7), the Summary's folders line reading
only the last install run (S9), a SharePoint library gone because OneDrive is signed out (S41), v23 treating the first
sync's refused config as one that does not load (S8, S55), the guide's old attempt split (S54), and ship-land.sh reading
a gh failure as a CI result (S63). The lead's pandoc CI flake passed 3 of 3 at load about 650.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68 over rounds 2 and 4 to 15; turns 3, 0, 0,
1, 5, 2, 4, 1, 1, 3, 1, 0, 0; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10. The lowest total and the fewest
wrongs of any round, and the first with no wrong on a rehearsed path.

Lanes (`--account auto`, CONTRACTS §16.116 to §16.121, brief `lanes/COMMON14.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r15-land.lock`): RA 10 (setup_report.py), RB 18
(install.sh), P 5 (prompt v24), B 16 (loop, cli, arm_local, cycle, doctor), E 10 (pandoc route, ship-land.sh's CI reads,
landed last), D 8 (docs/deploy, CONTRACTS). Lead: S71 (one operator cleanup, `b13-r14`, replacing the `b13-r13` one).

### B13 round 15 landed (2026-10-18)

All six lanes landed; CI green on `9ad94d42` (ci `39140575013`, diagrams `39140575047`; prompt v24). Notes
`lanes/r15-<lane>.md`; the round's worktrees and branches are removed.

| Lane | Landed (lane commits; ranges can hold another session's) |
|---|---|
| P | `88ddf164`, `4a87ea23` (v24) |
| RA | `563ba24e..0c8e0b86`, 6 commits, plus the test fix `1df9a9ac` |
| D | `83112288`, `d6807332` |
| RB | `dd369f24..3a6dab63`, 13 commits |
| B | `4bece992..fc10508d`, 12 commits |
| E | `dfebaefc..9ad94d42`, 6 commits (landed last; `scripts/ship-land.sh` now exits 9 when it could not read CI) |

Learnings: every lane landed with `scripts/ship-land.sh` and no lane waited on a push prompt. The busy-machine gate
refused some fires, and re-running the fire script fired the missing lanes. Lane B's back-channel was rewired from
window 20 to 377 after the lead's restart. RA's counted-noun sweep turned trunk red once on a test that pinned a
timeout's words only; RA fixed it in `1df9a9ac`.

Three items go to round 16's harvest (lead notes `round15/lead-notes.md`): the Q40 pandoc first-start CI flake, seen
3 times, with S70's start-log output in ci run `39132985876` attempt 1; the CI timeout of `install.sh --list-folders`
in `test_a_folder_list_that_could_not_read_the_config_goes_on_unattended` (run `39105995103` attempt 1); and
install.sh's `config_failed` saying "does not load" for a config that loads but the sync refuses (lane P).

Round 16: the round 15 workflow with prompt v24 on this section's landed tip, BASE `61474fdf` (ids T<n>, outputs
`round16/`); scenario C also plays the v23 and v22 blocks.

### B13 round 16: v24 rehearsal, cross-lane review, harvest (2026-10-18)

Run `wf_992b6eef-81f` on `791d9d4b` (103 agents, 0 errors). All six scenarios pass their EXPECTED with no turn; the
field Mac's path is clean (v10 build with copied helpers, pulled to v24, "fully one command"), every v23 and v22 copy is
refused in step 1 with exit 2, and no sandbox holds a lock named for the real home. 92 findings, 90 confirmed (0 turn,
22 wrong, 68 small), 2 refuted (T78, T91); 5 need prompt text, so the next prompt is v25. The count rose because the
finders converged on the same seams: the 22 wrongs are 11 defects. They come from lane P's refused-config probes and
review, not the plain rehearsed paths: install.sh's config_failed and v24 call a config that loads but a sync refuses
"does not load" (T1, T10, T29, T45, T67, T81: six finders); status hands the person's policy.toml and [governance]
fixes to the agent (T2, T9); a policy error's label name leaks into the report (T3); the report after a step 1 stop on
the field Mac is rendered by the installed v10 build (T11); an unreadable manifest passes status and doctor (T12); an
unreadable patch an earlier report carried counts as missing (T24); step 1 says "none is needed" for a mirrored
folder whose block is gone (T30); the new config.vanished_sources [FAIL] shows a source id in clear (T42, T53, T66);
the stopped copy's count fix-up misreads counts of 10 or more (T43); doctor's bundled-pandoc fix is a bare
`uv tool upgrade` without install.sh's lock and TLS settings (T59, T65). The harvest found the causes of the lead's two
CI flakes: the pandoc ticker's TERM trap exits 1, not 3, under `set -e`, which drops "pandoc: started after Ns" under
load (T41, T79, the Q40 flake); and install.sh can sit silent for a watchdog's whole limit (T80, the `--list-folders`
timeout).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90 over rounds 2 and 4 to 16; turns 3, 0,
0, 1, 5, 2, 4, 1, 1, 3, 1, 0, 0, 0; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22 (11 distinct). The third
round in a row with no turn.

Lanes (`--account auto`, CONTRACTS §16.122 to §16.127, brief `lanes/COMMON15.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r16-land.lock`): RA 20 (setup_report.py), RB 17
(install.sh: bring-back.md's patches, the stopped copy, the re-run command), P 10 (prompt v25, config_failed, the
deleted block in step 1), B 20 (status's person's fixes, the CLI's counts and WARNING lines, loop), E 13 (pandoc fix,
ticker and wait, `with_timeout`, converters, the gate journal; lands last), D 9 (docs/deploy, CONTRACTS). Lead: T92
(one operator cleanup, `b13-r15`, replacing the `b13-r14` one).

### B13 round 16 landed (2026-10-19)

All six lanes landed; CI green on `a49acdba` (ci `39224535079`, diagrams `39224535086`; prompt v25). Notes
`lanes/r16-<lane>.md`, lead notes `round16/lead-notes.md`. The operator ran T92's cleanup: 0 round 15 scratch entries
left.

| Lane | Landed (lane commits) |
|---|---|
| D | `2be5e2ab` |
| P | `ee9c71ac..3b75cefa`, 4 commits (v25) |
| RA | `43d5d94b..caa74374`, 11 commits |
| B | `8334a6e4..b8f8c0b8`, 13 commits |
| RB | `497121e5..302d782d`, 8 commits |
| E | `b7a70bb3..a49acdba`, 7 commits (landed last) |

Not built, each with a measured reason in its lane's note: lane P's T29 reorder in `cli.py`, lane RA's broad T4 bucket
(it breaks the O13 and Q36 pins; T14's bucket covers T4), lane RB's T26 `.sent` begin field (T16's precedence covers
it), and lane E's T61 carry-over note, T65's `uv pip` one-liner and two optional extras.

Learnings: the Mac rebooted at 16:34 mid-round. Every lane session resumed with its transcript and its commits, the
lead moved from window 377 to 10 and the lanes to 13 to 18 (pings to 377 were forwarded), and `/tmp` was emptied, which
took the fire briefs and the lead's brief generator with it; any land in flight died and was re-run. Round inputs now
live under `round<n>/`, not `/tmp`. The two CI flakes recurred on lanes B and RB before lane E's fixes landed (each
green on a re-run) and stayed green on lane E's first attempt. Lane E's land failed twice on lost xdist workers at a
load of 725 and 805 from other projects' jobs, with 0 failures, and landed on the third try at about 300.

For round 17's harvest (lead notes): the `governance.remote` [FAIL] prints the docs repo's remote URL unredacted (lane
RA); `install.sh`'s `report_word` turns any lower-case code word into a report word, so an organization of that name is
not redacted (lane RB); and two prompt phrasings deferred past v25 (lane RB).

Round 17: the round 16 workflow with prompt v25 on this section's landed tip, BASE `dd5d55d5` (ids U<n>, outputs
`round17/`); scenario C plays the v24 and v23 blocks.

### B13 round 17: v25 rehearsal, cross-lane review, harvest (2026-10-19)

Run `wf_9a91496d-dbd` on `3274f9a2` (123 agents, 0 errors). Rehearsals C and D sat about five hours on unanswered
permission prompts for their sandbox `git clone`; the run was stopped and resumed (finished agents replayed from
cache), C went through, and D's prompt came back once more before it cleared. All six scenarios pass their EXPECTED
with no turn: the field Mac is "fully one command", every v24 and v23 copy is refused in step 1 with exit 2, and no
sandbox holds a lock named for the real home. 112 findings, 107 confirmed (0 turn, 25 wrong, 82 small), 5 refuted
(U59, U98, U99, U100, U110: the three round 16 not-built reasons hold); 6 need prompt text, so the next prompt is v26.
The 25 wrongs are 17 defects, all from probes of states the rehearsed paths do not reach and from review: the
governance.remote [FAIL] puts the docs repo's remote URL in the report (U1, U15, U56, U93); a disallowed docs remote is
handed to the agent to delete (U2, U16) after step 2 says the repo has no remote (U17); a converter missing at sync
time makes a permanent "unreadable" stub (U3); install.sh's report word list unredacts an organization (U4, U94) and the
shell fallback shows a policy label (U54); the company-mirror git message (U32); --report-only closes the previous
session's attempt (U33); the older build's git-stop and step 1 reports (U34, U53, U81); add-source prints "failed" after
writing a valid block (U40); a corrupt manifest's report (U41); the first report drops its Doctor section (U48); the
report's person's-fix kinds (U52); exit 65 has no first-sync branch (U55, U65); a damaged table page passes status
(U64, U95); and v25 makes the bundled pandoc's [FAIL] the person's (U80). Neither CI flake has come back since lane E's
fixes (U101).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107 over rounds 2 and 4 to 17; turns 3,
0, 0, 1, 5, 2, 4, 1, 1, 3, 1, 0, 0, 0, 0; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22, 25 (17
distinct). Four rounds with no turn, but the totals have risen two rounds running: each round's builds open seams
(the new exit 65, the remote-host redaction, the older build's rewrite) that the next round's probes reach.

Lanes (`--account auto`, CONTRACTS §16.128 to §16.133, brief `lanes/COMMON16.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r17-land.lock`; scratch goes under `round17/`, not
`/tmp`): RA 26 (setup_report.py: the remote URL, the person's-fix kinds, a vanished source's labels), RB 21 (install.sh:
the report word list and shell fallback, the older build's rewrite, the hash wording, exit 65's first sync), P 14
(prompt v26, config_failed and the add-source loop), B 22 (vanished-source fix, damaged manifest, the CLI's docs-repo
line, the quiet lock wait, "(s)"), E 14 (the bundled pandoc's owner and path, converters; lands last if it changes the
gate), D 9 (docs/deploy, CONTRACTS, CLAUDE.md's CI time). Lead: U111, built (the seven round 16 files deleted by exact
path).

### B13 round 17 landed (2026-10-19)

All six lanes landed; CI green on `c90b0808` (ci `39338574076`; prompt v26). Notes `lanes/r17-<lane>.md`, lead notes
`round17/lead-notes.md`; the round's worktrees and branches, and round 16's lane E one, are removed.

| Lane | Landed (lane commits) |
|---|---|
| P | `0ede5567..a41d6ed0`, 7 commits (v26) |
| B | `09e6e67c..b11cf991`, 8 commits |
| D | `ea35a2bd..b450ca4b`, 5 commits |
| E | `0c6753b0..59e14533`, 6 commits, plus the gate change `efd8e92b` |
| RA | `92f0fd78..8beaed06`, 12 commits |
| RB | `369d9eaa..162d15aa`, 7 commits, plus `c90b0808` (U54's parity cases, after RA) |

Built differently or not built, each measured in its lane's note: RB built U4/U94 as "the shell's report words are the
words install.sh prints" rather than a closed list pinned to Python's (that pin would break on every string change),
and left U34's outcome-cause shim out; E's U101 needed no code (no flake in three clean runs); P's U63 is contract-only.

Learnings: rehearsals C and D waited about five hours on unanswered permission prompts for their sandbox clone; a
stop-and-resume of the workflow (finished agents replay from cache) cleared C, and D needed the operator. Three land
gates failed before the first push (one a dead test helper), and with every lane queued on the one lock the first land
reached trunk about two hours after the fire. Lane E's gate change landed before RB's last item; it touches only the
lane and affected modes, so the order cost nothing. RA moved the person's-fix kinds into `doctor.PERSONS_KINDS`, with a
copy in the report and a test pinning them equal, after which P's and E's new kinds landed in all three places.

Round 18: the round 17 workflow with prompt v26 on this section's landed tip, BASE `e5c5b0e2` (ids V<n>, outputs
`round18/`); scenario C plays the v25 and v24 blocks.

### B13 round 18: v26 rehearsal, cross-lane review, harvest (2026-10-19)

Run `wf_38924dc9-d24` on `4a55fe0f` (111 agents, 0 errors, 56 minutes). The new sandbox rule (small single-purpose
setup commands) held: no agent waited on a permission prompt. All six scenarios pass their EXPECTED with no turn: the
new Mac and the field Mac are "fully one command", every v25 and v24 copy is refused in step 1 with exit 2, the git
stop and the reinstall during a sync run as the prompt says, and the empty-folder alarm holds at every count. 100
findings, 91 confirmed (0 turn, 24 wrong, 67 small), 9 refused (V2, V23, V83, V92, V94, V96, V98, V99, V100: the
round 17 not-built reasons for U34's shim and U63 hold); 7 need prompt text, so the next prompt is v27.
The 24 wrongs are 17 defects, again from probes and review rather than the rehearsed paths: the report's own doctor
never runs lane B's table-damage check, so status FAILs a damaged manifest and the report does not (V14, V52, V56,
V71); the doctor cut-off seconds are the time left, not the time doctor ran (V3, V49); a word the reports print decides
redaction both ways, leaving an organization named like a report word in clear (V1, V84, V85) and redacting a printed
word that names nothing (V86); the shell fallback prints a [governance] type error's value (V44, V72) and a vanished
source's re-add folder (V46); the `<value>` match swallows U58's refusal (V45); the Summary's evidence line misleads
twice (V15, V35); a later sync is labelled "first sync" (V48); add-source's "failed" after a valid block is back on
exit 1 (V47); the older build's step 1 stop, git stop and finished-attempt reports (V16, V30, V31); v26's git quote
promises an `origin: other` the shell report never prints (V29); and U3's retry record (V57, V65).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91 over rounds 2 and 4 to 18; turns
0 for five rounds; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22, 25, 24 (17 distinct). The total fell
for the first time in three rounds, but each round's builds still open seams the next round's review reaches.

Lanes (`--account auto`, CONTRACTS §16.134 to §16.139, brief `lanes/COMMON17.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r18-land.lock`; scratch under `round18/`): RA 24
(setup_report.py: the cut-off seconds, the report-word rule, the `<value>` match, the evidence and first-sync words),
RB 13 (install.sh: report_word, the shell fallback's governance value and re-add folder, the older build's rewrite),
P 16 (prompt v27, step 1's folder tails, the config step's order, add-source's exit 1), B 16 (the report's doctor
hook, the speech helper's fix, the lock-wait line, the CLI's words, a mid-table damage test), E 12 (U3's retry record
and its texts, the gate's scanners; lands last if it changes the gate), D 8 (docs/deploy and CONTRACTS
cross-references). Lead: V95 (round 17's /tmp leftovers, removed by exact name) and V97 (no flake recurrence; U101
closed, reopen if either test fails on an attempt-1 ci run); V42's method (damage a manifest in place with
`python3 -I`, not `dd`) goes into round 19's script.

### B13 round 18 landed (2026-10-20)

All six lanes landed; CI green on `a7cd9146` (ci `39398610939`, diagrams `39398610954`; prompt v27). Notes
`lanes/r18-<lane>.md`, lead notes `round18/lead-notes.md`. Every item is built; none carries a not-built reason (lane
E left two optional halves out, named in its note). V8 moved to lane B's shared vanished-source tail, and B's V7 merged
with RA's V18.

| Lane | Landed (lane commits) |
|---|---|
| D | `dd5b5108..c20d2c3c`, 2 commits |
| RA | `8b9c1730..bd916c67`, 10 commits |
| E | `d404cb60..25584dfa`, 5 commits, plus the gate change `a7cd9146` (landed last) |
| RB | `d879ce8b..b7c1b941`, 10 commits, plus the CI fix `c342bb2e` |
| B | `782e4b16..4d090c1a`, 13 commits |
| P | `59514c3b..dbb22f4d`, 5 commits (v27) |

Learnings: the rehearsals' new small-command rule held, so the workflow ran in 56 minutes with no permission stall.
Lane RB's land turned trunk CI red on its own new test, which passed here only because this Mac has gitleaks and the CI
runner does not; P and B waited on the lock about 90 minutes for RB's fix. All six lanes queued on one lock at a load
of 150 to 350: the first land reached trunk about 90 minutes after the fire and the last about seven hours after it.
Lane B's last note to lane RA (what the report hook now returns) arrived after RA had retired; round 19's harvest
checks it. Round 18's own agents left about 70 entries in /tmp, removed with round 17's by exact name.

Round 19: the round 18 workflow with prompt v27 on this section's landed tip, BASE `ba85a3ff` (ids W<n>, outputs
`round19/`); scenario C plays the v26 and v25 blocks.

### B13 round 19: v27 rehearsal, cross-lane review, harvest (2026-10-20)

Run `wf_34c555f0-f44` on `6e7ae244` (94 agents, 0 errors, 105 minutes; the harvest measured a recording test under
throttling for about 40 of them). No agent waited on a permission prompt. All six scenarios pass their EXPECTED with no
turn: the new Mac and the field Mac are "fully one command" (the new Mac's final report kept its Doctor section at a
load of 99), every v26 and v25 copy is refused in step 1 with exit 2, and the git stop, the reinstall during a sync and
the empty-folder alarm run as the prompt says. 83 findings, 76 confirmed (0 turn, 8 wrong, 68 small), 7 refused (W36,
W43, W76, W77, W79, W82, W83); 2 need prompt text, so the next prompt is v28.
The 8 wrongs are 5 defects: the shell fallback report shows a vanished source's re-add folder in clear, because lane
B moved the tail into cycle.py and install.sh's parser never learned it (W34, W40, W56, W63); the older build's
git-stop report says no install.sh run has a first-sync step (W21); under install.sh the speech time-out fix points
at a NEXT line that does not name it (W19); damage under the cache or outputs table leaves status healthy while the
sync exits 1 (W41); and a manifest this account cannot open sends the person to a Doctor line that is not there
(W64).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76 over rounds 2 and 4 to 19;
turns 0 for six rounds; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22, 25, 24, 8 (5 distinct, the
fewest of the wave). Two rounds of falling totals; the wrongs are again seams the previous round's builds opened.

Lanes (`--account auto`, CONTRACTS §16.140 to §16.145, brief `lanes/COMMON18.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r19-land.lock`; scratch under `round19/`): RA 12 (the
report: an unopenable manifest, the converter-waiting clause, the cut-off words, redaction counts), RB 19 (install.sh:
the re-add folder's parser, the older build's rewrite, the shell legend and install source line, a gitleaks-absent
census guard), P 7 (prompt v28, step 1's damaged-table check, the step 2 patch line, add-source's held stderr), B 12
(table damage outside items, the lock-wait residues, the speech fix's words, permission errors), E 15 (the
converter-waiting count, the speech marker, a blank draw.io diagram, the recording tests' time bound), D 9 (guides,
CONTRACTS cross-references, the launcher's exit 71, CLAUDE.md's CI time). Lead: W71, built before the fire
(`cddd3689`: the dated-line scans read a line of any date, so lines dated 2026-10-20 are held), and W81 (round 17 to
19 /tmp leftovers removed by exact name; `lp-verify`, not ours, left alone).

### B13 round 19 landed (2026-10-20)

All six lanes landed; CI green on `17f85136` (ci `39464216658`, diagrams `39464216858`; prompt v28). Notes
`lanes/r19-<lane>.md`, lead notes `round19/lead-notes.md`; the round's worktrees and branches are removed. Every item is
built; none carries a not-built reason. W54's code came with lane E's W49 (RA landed its test), and W45 is a docstring
with no test that could fail.

| Lane | Landed (lane commits) |
|---|---|
| D | `11c6774b..454b8bad`, 4 commits (CI's limit raised to 40 minutes) |
| P | `019d4e21..64c64ba4`, 4 commits (v28) |
| RB | `3f27c70d..c3e4e407`, 14 commits |
| E | `cdc79b01..5df8ac8d`, 11 commits |
| B | `1c9ac010..d361f452`, 7 commits |
| RA | `9bf7f9b4..17f85136`, 9 commits |

Learnings: landing W71 before the fire let every lane date its lines 2026-10-20 without touching the scans. Three
seams were settled by message rather than by the next round: lane D's W62 and lane B's W41 both reworded the
table-damage lines (B, landing second, added the dated lines); lane RA's W64 edited lane B's doctor.py (and
CONTRACTS.md, which lane D also edited), and lane RA's W52/W68 (`49e03a64`) edited the table in lane D's
setup-feedback.md that lane D's W57/W58 (`11c6774b`) had edited (both told to rebase; corrected 2026-10-20 by round
20's X77: W64 itself does not touch the guide); lane P told B which step 1 tails keep their table word. Lane B's W66 landed after
lane RB had retired, so RB's census guard (W78) never saw it; round 20's harvest checks it. Land to land took about
three hours, half of round 18's, at a load of about 70.

Round 20: the round 19 workflow with prompt v28 on this section's landed tip, BASE `fba3908a` (ids X<n>, outputs
`round20/`); scenario C plays the v27 and v26 blocks.

### B13 round 20: v28 rehearsal, cross-lane review, harvest (2026-10-20)

Run `wf_c0d52805-e81` on `5c35bfff` (99 agents, 0 errors, 59 minutes); no agent waited on a permission prompt. All six
scenarios pass their EXPECTED with no turn: the new Mac and the field Mac are "fully one command" (step 2 took 722 s at
a load of about 200, most of it the speech helper), every v27 and v26 copy is refused in step 1 with exit 2, and the
git stop, the reinstall during a sync and the empty-folder alarm run as the prompt says. 88 findings, 84 confirmed (0
turn, 21 wrong, 63 small), 4 refused (X31, X52, X87, X88: CI on main from `fba3908a` was green on attempt 1 with no
job past 30 minutes); 3 need prompt text, so the next prompt is v29.
The 21 wrongs are about 13 defects, most on one seam round 19 opened: a manifest this account cannot open, which W64
handled in the report but which install.sh still treats as an ordinary error (X9), doctor calls "not excluded from Time
Machine" (X27), the report files as "unexpected" (X28, X35), and doctor answers with a sudo chown handed to the agent
or a generic fix for a folder it cannot search (X73, X74). The others: a damaged index root, where step 1 promises a
step 2 stop that never comes (X1, X25, X72) and the guide repeats it (X59); the Repeat conversions note puts waiting
files in Quarantine (X2, X10); W17's agent words (X38, X39, X40); a one-line URL rewrite left in clear by both reports
(X8, X41); a sync that stops on the state file leaves staging without its Time Machine exclusion (X4); W3's NEXT
blames the time limit when it did not cut the report (X37); a plain install.sh after a stopped old copy reports "fully
one command" (X19); and a folder name with a TAB in clear in the shell report (X75).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84 over rounds 2 and 4 to
20; turns 0 for seven rounds; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22, 25, 24, 8, 21 (about 13
distinct). The fall stopped: round 19's W64 and W41
reached five readers each, and this round's probes found the readers they missed.

Lanes (`--account auto`, CONTRACTS §16.146 to §16.151, brief `lanes/COMMON19.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r20-land.lock`; scratch under `round20/`): RA 14 (the
report: the unopenable manifest's bucket, the Repeat conversions note, agent words, the URL rewrite, the sqlite
code), RB 17 (install.sh: the URL rewrite, the stopped copy's outcome, W3's NEXT, the TAB folder name, the shell's
agent words and legend, the upgrade note in the first sync), P 9 (prompt v29, step 1's state-file tails for an index
root and an unopenable manifest, step 2's NEXT for it), B 24 (an unopenable manifest in doctor, status and sync,
index-root damage, staging's exclusion on exit 65, helper fixes, the lock wait), E 7 (the upgrade's retry note,
converter error lines, helper docstrings), D 12 (guides and CONTRACTS cross-references). Lead: X86 (round 19 and 20
/tmp leftovers and round 18's v26 sandboxes, about 43 GB, removed by exact name).

### B13 round 20 landed (2026-10-20)

All six lanes landed; CI green on `e499db95` (ci `39529771141`; prompt v29). Notes `lanes/r20-<lane>.md`, lead notes
`round20/lead-notes.md`. One item is not built: X14's first fix, which lane B measured to point the person at a sync
that cannot help, so it built X46's words for the same line instead. X11 is lane RB's X85, X58 is lane D's X61, and
X77 is the lead's plan correction (`e7acd35e`).

| Lane | Landed (lane commits) |
|---|---|
| D | `a1b06e16..d828c17c`, 4 commits |
| RA | `125a8619..f9eee326`, 11 commits |
| P | `e1f0fce6..15fa35bc`, 3 commits (v29) |
| RB | `cac19f28..64380e08`, 14 commits |
| B | `f000244f..cc908fee`, 6 commits (with dated lines at lane D's paragraphs) |
| E | `c4f4ae10..e499db95`, 5 commits |

Learnings: the first fire aborted because the pane-id lint read the all-digit short sha `34818253` in the briefs as a
truncated session id; the briefs now carry the full sha. The unopenable-manifest work crossed four lanes (P's
person's-fix phrase, B's `doctor.cannot_open` and its codes, RA's Summary bucket, B's Time Machine line), so the
lead relayed seven seams before the lanes could message each other; giving every brief the other lanes' session ids
at the fire would remove that relay. A lane's CLI probe rewrote the real agentsync-docs skill for a moment
(scratch HOME, CLAUDE_CONFIG_DIR still set); it was restored and the lane's memory rule now covers it. Lane P's X72
(index-root clause) is true only with lane B's X50, and its X3 prompt clause keys on lane RB's X37 NEXT: round 21
checks both.

Round 21: the round 20 workflow with prompt v29 on this section's landed tip, BASE `34818253` (ids Y<n>, outputs
`round21/`); scenario C plays the v28 and v27 blocks.

### B13 round 21: v29 rehearsal, cross-lane review, harvest (2026-10-20)

Run `wf_2a854d28-267` on `309cafab` (91 agents, 0 errors, 49 minutes); no agent waited on a permission prompt. All six
scenarios pass their EXPECTED: the new Mac and the field Mac are "fully one command", every v28 and v27 copy is
refused in step 1 with exit 2, and the git stop, the reinstall during a sync and the empty-folder alarm run as the
prompt says. 82 findings, 80 after dedupe, 77 confirmed (1 turn, 15 wrong, 61 small), 3 refused (Y29, Y65, Y67: every
reader agrees on a damaged index or table root, and lane B's reason for not building X14's first fix holds); 3 need
prompt text, so the next prompt is v30.
The turn and most wrongs are one defect seen from every reader: a state file or folder this account owns but cannot
open because of its own mode. Step 1's tail and status give its `chmod` to the agent, the report's Summary files it
as the person's (Y3, Y9, Y21, Y25, Y30, Y34), and v29's step 2 forbids the agent the chmod step 1 just gave it (Y24,
the turn). Lead ruling for round 21: this account's own chmod is the agent's, run once and then the re-run; another
account's file, an ACL deny or an unsearchable parent stays the person's `sudo chown`. The others: a git proxy or
remote written on its section's header line (Y1, Y36, Y64); a TAB folder name in add-source's escaped form (Y2); a
shut state folder's second FAIL (Y26); the graph check missing from 'not checked' (Y42); a lock or heartbeat file
this account cannot open (Y43); and the guide's Repeat conversions row (Y58).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77 over rounds 2 and
4 to 21; turns 0 for seven rounds, then 1; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22, 25, 24, 8,
21, 15 (about 8 distinct). Each round's new state (round 19's unopenable manifest, round 20's index root and own-mode
state) reaches five readers; the ownership rule is stated once in COMMON20 so that this round's lanes build one answer.

Lanes (`--account auto`, CONTRACTS §16.152 to §16.157, brief `lanes/COMMON20.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r21-land.lock`; scratch under `round21/`): RA 32 (the
report's owner for this account's chmod, the header-line git config, the TAB folder, the shared cannot-open codes,
dead interim code, exit 71, the stopped copy's words), RB 7 (install.sh's header-line git config, the report header,
on_exit under SIGTERM, the Private-only loop), P 6 (prompt v30 and install.sh's NEXT and tail for this account's
chmod), B 16 (state_path_fix in sync's and curate's errors, a shut state folder, lock and holds.json words, the graph
check), E 4 (the convert retry clause, helper docstrings, rescreened stubs), D 10 (guides, CONTRACTS citations,
CLAUDE.md's CI time). Lead: Y79 (round 20's /tmp leftovers and the v27 and v28 sandboxes, about 57 GB, by exact name)
and Y80 (after the fire, every lane gets all six session ids).

### B13 round 21 landed (2026-10-21)

All six lanes landed; CI green on `b0fdb681` (ci `39588432621`, diagrams `39588432586`; prompt v30). Notes
`lanes/r21-<lane>.md`, lead notes `round21/lead-notes.md`. All 77 items are built (75 in lanes, Y79 and Y80 by the
lead). One corrected fix was built another way: Y10's restore of u+rw inside agentsync would widen a mode the person
set, which install.sh promises never to do, so lane P built Y10 as Y24's NEXT (the agent's own chmod, per the
ownership rule). Lane B's review residual stays as its Y27 verifier accepted: a foreground sync that breaks a stale
lock and then raises says so only at `-vv` (§16.155). Duplicates built once: Y11/Y26 and Y31/Y69 (B), Y7+Y72 (RB),
Y75 as lane D's Y76.

| Lane | Landed (lane commits) |
|---|---|
| D | `d6949daf..eaed1034`, 2 commits (ci limit 45) |
| RB | `a0382751..d84fbd7a`, 7 commits |
| P | `a85a3e8f`, 1 commit (v30) |
| E | `a29ed578..b0dffb26`, 5 commits |
| B | `bc6196e5..e0287cae`, 17 commits (with lane P's ownership-rule seams) |
| RA | `6b08d573..b0fdb681`, 13 commits |

Learnings: sending every lane all six session ids at the fire (Y80) let lanes settle their own seams (RB told RA
about the Y64 parity test; D told RA about Y48 and Y71); the lead relayed only the two seams known at the fire. The
shared land lock serialized six lands over about four hours (D first at 02:42, RA last at 06:26), and four land gates
went red as lanes rebased onto each other's changes; each lane fixed its own. One ci run took 34 minutes on a loaded
runner, so lane D raised the job's limit to 45. The lead's recycle killed its ping watcher; the mailbox hook armed a
new one for the pane before the successor's first turn, so no ping was lost.

Round 22: the round 21 workflow with prompt v30 on this section's landed tip, BASE `20612f1f` (ids Z<n>, outputs
`round22/`); scenario C plays the v29 and v28 blocks.

### B13 round 22: v30 rehearsal, cross-lane review, harvest (2026-10-21)

Run `wf_b85edcf6-a9f` on `f84c8c01` (102 agents, 0 errors, 55 minutes). All six scenarios pass their EXPECTED: the
new Mac and the field Mac are "fully one command", every v29 and v28 copy is refused in step 1 with exit 2, and the
git stop, the reinstall during a sync and the empty-folder alarm run as the prompt says; every round 21 fix a
rehearsal re-checked holds on real output, and v30's chmod clause clears a manifest, the state folder, a configured
state_dir and a -wal at 0000 or 0400. 91 findings, none merged, 88 confirmed (2 turns, 29 wrong, 57 small), 3
refused (Z16 is not a defect; Z85 and Z87 report that readers agree). The lead confirmed Z86 against its verifier's
"not real": that verdict refuted the finder's "no defect" while measuring a Y7 regression. Seven need prompt text, so
the next prompt is v31.
The turns and most wrongs are round 21's ownership rule left short in two places. v30 grants the chmod for the
manifest and the state folder, but a lock, heartbeat or governance file at 0000 still stops step 2 (Z8, Z59, the
turns). A folder ABOVE the state folder that refuses a search crashes doctor's disk and governance checks, sends
status's NEXT to disk's generic fix and replaces the report's Configuration with a raw PermissionError (Z3, Z9, Z10,
Z26, Z27, Z34, Z40, Z41, Z46, Z60, Z61, Z73, Z74). Lead rulings for round 22 (in `lanes/COMMON21.md`): (1) the agent's
chmod covers every file agentsync keeps under its state folder, and a 0000 folder holding a 0000 file takes both
chmods in one pass before the re-run; (2) an unsearchable parent is one FAIL, the person's, with every check that
needs the state folder "not checked" and nothing raised; (3) the words name the folder when a folder refuses. The
others: a removed outside-CloudStorage --source-local folder in clear in the shell report (Z2, Z13, Z29, Z42, Z77),
the shell report's git readers (Z1, Z25, Z75, Z76), a configured state_dir name in clear (Z21, Z28), a folder where
the manifest belongs (Z11, Z12, Z51, Z53), a read-only volume (Z47, Z78, Z79), a product-word organisation made
public by a site library of the same word (Z86) and a refused purge queue's traceback (Z54, older than round 21).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88 over rounds 2
and 4 to 22; turns 0 for seven rounds, then 1, then 2; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22,
25, 24, 8, 21, 15, 29 (about 8 distinct). Round 21's rule was built for the cases its findings named; each reader
missed the neighbouring ones, so this round states the rule for every state file and for the parent.

Lanes (`--account auto`, CONTRACTS §16.158 to §16.163, brief `lanes/COMMON21.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r22-land.lock`; scratch under `round22/`): RA 28 (the
report under an unsearchable parent, folder words, a manifest folder, the state_dir name, a stopped copy's time and
folders lines, the buckets' owners, [remote.origin], Z86), RB 12 (the shell report's removed --source-local folder,
its git readers, the --report-only NEXT, the fallback's state stop, Y66's guard test), P 10 (prompt v31, the chmod of
every state file, the person's kinds, a manifest folder and a read-only volume in step 1 and step 2), B 26 (doctor's
disk and governance under an unsearchable parent, the person's kinds, cli's state errors for a manifest folder, a
read-only volume and a -wal, nested brackets, the purge queue's traceback, the stale-lock note), E 5 (the upgrade
retry note, dead retry code and its docstrings), D 7 (guides, CONTRACTS citations, CLAUDE.md's CI time). Lead: Z91
(round 21's /tmp leftovers, the round 22 harvest's scratch and the v29 sandboxes, 87 names, removed with none
skipped).

### B13 round 22 landed (2026-10-21)

All six lanes landed; CI green on `be9605d9` (ci `39716390210`, diagrams `39716390215`; prompt v31 in `131ddf6f`).
Notes `lanes/r22-<lane>.md`, lead notes `round22/lead-notes.md`. All 88 items are built (87 in lanes, Z91 by the
lead); none carries a not-built reason. Built once for several ids: Z17, Z48 and Z83 are lane P's `537dddd7` (every
PERSONS_KINDS copy in one commit, in lane B's words, because four files pin them to each other), Z67+Z88 (D), and
Z76 has a shell half (RB) and a Python half (RA).

| Lane | Landed (lane commits) |
|---|---|
| D | `a20ef0c4..547db6c4`, 2 commits |
| RB | `cb9a7973..fe706a76`, 6 commits |
| E | `90877dc9..bef0623a`, 4 commits |
| B | `46963aae..b1d6704a`, 16 commits |
| RA | `20c13d16..a96dfc7b`, 12 commits (the last fixes the red run below) |
| P | `5b93ee9a..be9605d9`, 9 commits (v31) |

Learnings: lanes P and B both claimed the PERSONS_KINDS copies; the lead ruled that one lane changes every copy in
one commit, since a parity test between any two of them goes red between two lands. Trunk went red once (ci
`39706973640` on `d4540b52`): a word pin in a timing-marked test still read RA's old Z33 words, and the local gate
skips timing-marked tests above a load of 4 per core (`tests/conftest.py`, unless `AGENTSYNC_TIMING=1`), so CI was
its first run; RA fixed it in `a96dfc7b` and round 23 checks every such pin. The Mac rebooted at 14:06; every session resumed, but panes were renumbered (the lead
moved from 10 to 16) and two lands died before they pushed, so the lead resent the lanes' new ids and RA and RB
re-queued. The capacity gate turned four fires away once (8 sessions mid-turn) and admitted them on the second try.

Round 23: the round 22 workflow with prompt v31 on this section's landed tip, BASE `fcafd734` (ids A<n>, outputs
`round23/`); scenario C plays the v30 and v29 blocks.

### B13 round 23: v31 rehearsal, cross-lane review, harvest (2026-10-22)

Run `wf_efe34813-590` on `7a54f938` (107 agents, 0 errors, 102 minutes). All six scenarios pass their EXPECTED: the
new Mac and the field Mac are "fully one command", every v30 and v29 copy is refused in step 1 with exit 2, and the
git stop, the reinstall during a sync and the empty-folder alarm run as the prompt says. Every round 22 fix a
rehearsal re-checked holds on real output, and v31's one-pass chmod clears a 0000 state folder, a 0000 folder
holding a 0000 manifest and a 0000 lock (round 22's turn). 96 findings, none merged, 93 confirmed (0 turns, 25 wrong,
68 small), 3 refused (A14: the two reports read different inputs; A40: a rehearsal's own refused command; A71: the
guide's row already holds the case). One needs prompt text, so the next prompt is v32.
No turn this round. Most wrongs sit on two states round 22's rulings did not name: a FILE where the state folder (or a
folder above it) belongs, which install.sh hands to the agent as a chmod 700 of a file while status calls it the
person's and governance crashes (A2, A6, A7, A21, A22, A23, A32, A33, A80); and a heartbeat this account cannot
write, where a sync ends 0 with "session done" so the agent never meets its chmod (A1, A34, A84). Lead rulings for
round 23 (in `lanes/COMMON22.md`): (4) anything that is not a folder where the state folder or a folder above it
belongs is the person's to move aside, never a chmod, with one FAIL and the rest "not checked"; (5) a heartbeat this
account cannot write is reported once, and an interactive sync exits 1 with its fix and a NEXT to status; (6) lane P
lands every person's-kinds copy in one commit, as in round 22. The others: the report's words after the one-pass
chmod and for an ACL on the state folder itself (A5, A13, A38, A43, A69, A88), the purge queue's words after round
22's Z54 (A52, A55, A56, A57), rotate_logs outside the lock's try (A53, A81), the shell report's own moved folders
in clear (A41, A42), a hold's free-text reason in clear (A8) and the Z76 guard left in a test (A48, A76, A89).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93 over rounds
2 and 4 to 23; turns 0 for seven rounds, then 1, 2, 0; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20, 17, 10, 22, 25,
24, 8, 21, 15, 29, 25. The count holds near 90 because each ruling names the cases its round found and the next
rehearsal probes the neighbours; this round the neighbours were the not-a-folder state root and the heartbeat.

Lanes (`--account auto`, CONTRACTS §16.164 to §16.169, brief `lanes/COMMON22.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r23-land.lock`; scratch under `round23/`; back-channel
pane 16 since the reboot): RA 31 (the report's not-a-folder state, Items and outcome words, a stopped copy's lines,
two url keys, the <proxy-N> legend, a hold's reason, the timing tests' word pins), RB 12 (the shell report's
redaction, NEXT relay and git readers, the Z76 guard, the oneshot helper's timeout), P 15 (prompt v32, a not-a-folder
state root at steps 1 and 2, step 1's tails for a lock, heartbeat or governance stop, every person's-kinds copy), B
23 (a not-a-folder state root and an unwritable heartbeat in doctor and cli, the purge queue's words, rotate_logs,
audit.jsonl), E 8 (the upgrade's settled count, doctor's convert imports, §16.162's stale line), D 6 (guides,
CONTRACTS records, CLAUDE.md's CI time). Lead: A96 (nine orphaned round 22 install.sh processes killed by pid and the
three round 22 worktrees removed; its test-helper part is lane RB's) and A39 (the fixture tree frozen under
`~/.cache/agentsync-bring-back/fixtures/v1/` with a checksum manifest; round 24's rehearsals copy and check it and
never write to it).

### B13 round 23 landed (2026-10-22)

All seven lanes landed; CI green on `9b8471d4` (ci `39865991577`, diagrams `39865991687`; prompt v32 in `a29ff3b3`).
Notes `lanes/r23-<lane>.md`, lead notes `round23/lead-notes.md`. All 94 items (93 confirmed plus A97) are built or
carry a measured reason: A27 has no copy in the agentsync report to change, A46's report side is unchanged (its
verifier; lane B landed its test rows in `8269ab2b`, its install.sh half is P's `5d8e8c36`), and A97 leaves
`launcher/build.sh` on shasum (an edit changes the launcher's source hash, which costs a privacy re-approval), finds
no test that runs the console script by path, and refutes the launchctl poll premise (18 getenv and 2 print calls a
run, not polls). A24, A25 and A27 were split RA and RB; A8's shell half is RB's `88d99e17`, at RA's request.

| Lane | Landed (lane commits) |
|---|---|
| D | `95ddf881..4395a7e9`, 2 commits |
| P | `5d8e8c36..7c8dd2fe`, 6 commits (v32) |
| E | `99d9aa96..97c8b6b3`, 7 commits |
| RA | `3cf85b11..01600571`, 24 commits, and `64406b25` |
| RB | `c58daebe..88d99e17`, 11 commits (with RA's `96134549`), and `193c4e5d` (the red run below) |
| B | `46880b9e` (the red run below) and `75cccb68..8269ab2b`, 15 commits and RA's A46 rows |
| K | `eb0938ee..9b8471d4`, 6 commits; the tests' #! execs for its subset 5,309 before, 141 after |
| Lead | `1031e03e`, `cb50a3af` (in RA's land), `38727270` (in RB's), `11bf13c5` |

Learnings: every round 23 brief named round 22's CONTRACTS section (the briefs tool's section map was not moved on;
lane B caught it, the lead sent the right map mid-round); round 24's tool maps §16.171 on. `ship-land.sh` printed
"CI green" after reading back only the diagrams run, which gh can list before ci (lane P): it now waits for the ci run
(`1031e03e`). The Mac ran at a load of 15 to 25 per core all day, and five land gates in a row went red on tests whose
time limit is not what they check: the real media helper tests and the real codesign read are now timing tests
(`11bf13c5`, `38727270`), and at the time `codesign --verify /bin/ls` hung with no CPU for 180 s, which no test change
fixes. Trunk went red once (ci `39809607845` on `38727270`): CI's macOS git keeps a url list an empty `url =` clears
on this Mac (`193c4e5d`), and the lock-wait test raced a holder's release (`46880b9e`). Lane K's session died at 03:17
mid-run and kitty's remote control stopped answering from about 04:30, so no pane could open: the lead resumed K's
session headless (`claude -p --resume`, same account and worktree) and it landed. Each land took about 40 minutes, and
the land lock is not first come, first served.

Round 24: the round 23 workflow with prompt v32 on this section's landed tip, BASE `ed901cd3` (ids B<n>, outputs
`round24/`); scenario C plays the v31 and v30 blocks. Its harvest carries lane B's seams for lane P (install.sh's
config_failed NEXT for a lost manifest, its manifest-is-a-folder words) and lane K's #! census.

### B13 round 24: v32 rehearsal, cross-lane review, harvest (2026-10-22)

Run `wf_b7fb09c0-c69` on `b1f4a087` (116 agents, 0 errors, 89 minutes). All six scenarios pass their EXPECTED: the
new Mac and the field Mac are "fully one command", every v31 and v30 copy is refused in step 1 with exit 2, and the
git stop, the reinstall during a sync, the converters and the empty-folder alarm run as the prompt says; no name
leaks. Every round 23 fix a rehearsal re-checked holds on real output. 105 findings, none merged, 102 confirmed (2
turns, 31 wrong, 69 small), 3 refused (B29: the local-work tail counts what it describes; B46: four real installs at
a load of 146, not a product defect; B94: the A9 outcome ruling holds). Seven need prompt text (one build), so the
next prompt is v33.
Both turns and most wrongs sit on round 23's A29 lost-manifest refusal, which landed in cli and doctor with no
installer or report half: a folder where manifest.sqlite belongs is moved aside as install.sh says, and the next run
stops again on the lost-manifest guard (B7, B21), whose step 2 NEXT is the generic "fix the error above" and which the
report files as unexpected. The rest: v32's step 3 says no record of a sync stop is kept, while round 23's A9 keeps
one, so the report counts one stop twice (B1 and seven duplicates); and the rehearsal brief's fixture check ran from
the wrong folder (B6 and five duplicates; the lead's, fixed in `round24/round24-workflow.js`). Lead rulings for round
24 (in `lanes/COMMON23.md`): (7) a lost manifest is the person's (restore it, or move heartbeat.json aside so the next
sync starts a new manifest) in every reader, its own kind, never "unexpected"; (8) a folder where manifest.sqlite
belongs is moved aside together with heartbeat.json, in doctor.MANIFEST_FIX's words, so following the words leads to
no second stop; (9) a step 3 stop is recorded by agentsync and still logged by the agent, and the report pairs the two
one to one by order, counting it once.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102 over
rounds 2 and 4 to 24; turns 0 for seven rounds, then 1, 2, 0, 0, 2; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17, 14, 20,
17, 10, 22, 25, 24, 8, 21, 15, 29, 25, 31. The turns again come from a round's new stop whose words no other reader
had yet (A29), which ruling 8's test (follow the words and see where they lead) is meant to catch before a land.

Lanes (`--account auto`, CONTRACTS §16.171 to §16.177, brief `lanes/COMMON23.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r24-land.lock`; scratch under `round24/`; back-channel
pane 16): P 30 (prompt v33, the lost manifest and a folder at the manifest in install.sh, its other state tails), RA
24 (the report's lost-manifest words, one step 3 stop counted once, its other words), B 22 (the lost manifest's kind
in status, doctor and the sync, round 23's seams), RB 6 (the shell report's hold reason and sync-stops.log, harness
guards), E 5 (the speech helper's reason, the timing marker, the waiting stubs), D 5 (guides, CONTRACTS, CLAUDE.md's
CI time), K 4 (the suite's remaining #! execs: 4,735 by the harvest's census). Lead: B6, B19, B30, B38, B47, B87 (the
fixture check runs from `<root>/fixture`).

### B13 round 24 landed (2026-10-23)

All seven lanes landed; CI green on `8900fb45` (ci `39953300563`; prompt v33 in `357dc812`). Notes
`lanes/r24-<lane>.md`, lead notes `round24/lead-notes.md`. 101 of the 102 items are built; B17 is not (status's NEXT
for a person's FAIL at step 3: lane B measured that a change makes status worse for every other caller, and its
words went into v33's step 3 instead), and B75 is a wording fix only, as its verifier said. Lane B also built lane
RA's B16 writer half and lane P's B105 report half; lane RB built B101's shell half for lane RA; lane D's alias half of
B84 duplicated RB's B68 and was dropped at its rebase. Lane K cut the whole suite's #! execs from 4,762 to 315.

| Lane | Landed (lane commits) |
|---|---|
| E | `b1c31b59..63f7b95d`, 4 commits |
| P | `a37be5dd..7b3bda87`, 6 commits (v33) |
| RB | `c5a97bfb..54af8be5`, 8 commits |
| K | `cc66adbb..7ed8f649`, 3 commits |
| B | `a23be87c..197ab3d4`, 18 commits |
| D | `4f22ceff..58183bb5`, 5 commits, and `8900fb45` (after RA, whose words its test reads) |
| RA | `fd810089..625312aa`, 10 commits |

Learnings: trunk stayed green all round, and every lane's last land read back its ci run (round 23's `1031e03e`). The
load ran 10 to 27 per core; each land took about 40 minutes. The capacity gate turned lanes D and K away once (nine
sessions mid-turn) and admitted them on the second try; lane RB's `/goal` was not armed (a dialog was open in its
pane at arm time) and it built and landed from its brief alone. The lead's pane moved from 16 to 134 when its session
was resumed, so round 24's briefs name `agent-context-sync-134`; its self-recycle was refused twice (a stale draft in
its input box, then an `/exit` it could not read back), and the lead stayed in this context. Lane RA's lesson: a new
word in any `setup_report.py` string literal joins `report_words()` and stops a one-word value equal to it from being
redacted (B55 hit it); round 25's harvest measures it.

Round 25: the round 24 workflow with prompt v33 on this section's landed tip, BASE `c93fcd96` (ids D<n>, since C has
`§16.11 C15`; outputs `round25/`); scenario C plays the v32 and v31 blocks.

### B13 round 25: v33 rehearsal, cross-lane review, harvest (2026-10-23)

Run `wf_fa07ce82-7fc` on `3ec66a92` (96 agents, 0 errors, 43 minutes). All six scenarios pass their EXPECTED: the
new Mac and the field Mac are "fully one command", every v32 and v31 copy is refused in step 1 with exit 2, and the
git stop, the reinstall during a sync, the converters and the empty-folder alarm run as the prompt says; no name
leaks; following round 24's folder-at-the-manifest words leads to no second stop (B21 holds). 85 findings, 81
confirmed (0 turns, 19 wrong, 62 small), 4 refused (D13, D37, D52, D82). None needs prompt text, so v33 stays the
prompt to paste.
Most wrongs sit on two edges round 24 opened: v33's new step 3 path for a person's FAIL, which the report calls
"worked with help" and counts twice (its pairing claimed only stops a later sync fixed), and side files of the
manifest that are not files (a folder at -wal or -shm gives sync a raw sqlite error with no fix). The rest are seams
on round 24's own fixes (B37's silent mirror deletion, B39's URL cut after an apostrophe, B101's lost empty
insteadOf, B63's fifo-only guard). Lead rulings for round 25 (in `lanes/COMMON24.md`), all code-only so the prompt
stays v33: (10) a step 3 that ends on the person's unfixed FAIL is "failed" at the report step, reversing A36's
"never failed" for that case only; (11) every recorded stop pairs one to one with a chmod-naming step 3 error, up to
the stop after next, and an unfixed stop's paired error still counts as a reason (the verifier's v34 prompt change is
not built); (12) anything not a file at manifest.sqlite-wal or -shm, a 0000 folder or a dangling link at the
manifest, is the person's in sync, curate and init too, in status's own words.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81
over rounds 2 and 4 to 25; turns 0 for seven rounds, then 1, 2, 0, 0, 2, 0; wrongs 23, 15, 20, 15, 15, 12, 10, 11, 17,
14, 20, 17, 10, 22, 25, 24, 8, 21, 15, 29, 25, 31, 19.

Lanes (`--account auto`, CONTRACTS §16.178 to §16.182, brief `lanes/COMMON24.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r25-land.lock`; scratch under `round25/`; back-channel
pane 134): RA 27 (rulings 10 and 11, the side files' report words, the report's other words), B 26 (ruling 12 in
cli and doctor, round 24's status, curate, governance and lock seams), RB 7 (all of install.sh this round: the
shell report's legend and stop reader, manifest_lost's inlined copies), E 9 (the speech and OCR reasons, the
timing pin), D 9 (guides, CONTRACTS records, CLAUDE.md's CI time). No lane P or K: no prompt text and no #! item.
Lead: D14, D17, D30 (the rehearsal kit's chmod order and docs/_eval timing, the scenario C wording; fixed in
`round25/round25-workflow.js`).

### B13 round 25 landed (2026-10-23)

All five lanes landed; CI green on `a34d3aaf` (ci `40017991870`, diagrams `40017991908`); the prompt is still v33.
Notes `lanes/r25-<lane>.md`, lead notes `round25/lead-notes.md`. All 81 items are built, each with a test that fails
without it, and none carries a not-built reason: D81 needed no lane B change (round 24's B17 reason holds) and its
residual landed in lane RA's `7d68f56e`; lane RA built lane B's D26 report half, and lane RB the install.sh seam of
lane B's D45 and a test that the shell and agentsync reports pair step 3 stops alike.

| Lane | Landed (lane commits) |
|---|---|
| E | `8db0fb22..5e4ac8ea`, 5 commits |
| D | `d6f846bd..e5d0f03f`, 7 commits (ci.yml's job limit is 50 minutes now) |
| RA | `7d68f56e..ca0d3dd9`, 13 commits |
| B | `9d187497..3081d7cf`, 9 commits |
| RB | `41794734..a34d3aaf`, 10 commits |

Learnings: ruling 11 as first written (only a chmod-naming line pairs) could never pair a person's stop, since v33's
person's-FAIL path runs no chmod; lane RA caught it at its first read, and the lead refined it mid-round (an agent's
stop claims a chmod line, a person's stop a line naming the sync's non-zero exit). Lane D's new test pins every
round's dated lines to its heading's date, so the lead forwarded the rule to the lanes still to land. Trunk stayed
green; five lanes kept the capacity gate quiet. The lead's self-recycle was held a second time (the typed `/exit`
could not be read back from its input box), so it led this round in the same context.

Round 26: the round 25 workflow with prompt v33 on this section's landed tip, BASE `b4c61127` (ids E<n>, unused in
§16 lines; outputs `round26/`); the prompt did not change, so scenario C still plays the v32 and v31 blocks.

### B13 round 26: v33 rehearsal, cross-lane review, harvest (2026-10-23)

Run `wf_06180c62-224` on `dd5639cb` (92 agents, 0 errors, 72 minutes). All six scenarios pass their EXPECTED, and
scenario F found nothing at all; no name leaks; round 25's rulings hold on real output where the rehearsals played
them (a person's step 3 stop reads "failed at step 3" and is one Items line; following the -wal and manifest fixes
leads to no second stop). 81 findings, 77 confirmed (0 turns, 19 wrong, 58 small), 4 refused (E13, E64, E73, E80).
None needs prompt text: v33 stays the prompt to paste.
The wrongs sit on round 25's own new paths: a person's step 3 stop still pairs only when the agent's line has the
words the pairing looks for ("no chmod was run" and the person's own chmod quoted do not pair); state files that are
not files beyond the manifest's side files (a folder or fifo at the lock passes status as free while every sync
tracebacks; a folder at heartbeat.json is a warn that promises a rewrite no pass can make); and the lost manifest's
new start, where `purge --queue` still runs with no manifest and erases current pages (E25, data integrity), its
orphan-page removals are in no count, and the report's D26 half never fired. Lead rulings for round 26 (in
`lanes/COMMON25.md`), all code-only: (11, refined again) a person's stop claims the next step 3 line that is not a
lock-held skip, whatever its chmod words; (13) anything not a regular file where any state file belongs is the
person's in every reader, never "free", never a block on a fifo, never a traceback; (14) with the manifest lost, no
command deletes or rewrites a mirror page (lane B builds E25 first), and the new start's removals are counted.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81,
77 over rounds 2 and 4 to 26; turns 0 for seven rounds, then 1, 2, 0, 0, 2, 0, 0; wrongs 23, 15, 20, 15, 15, 12, 10,
11, 17, 14, 20, 17, 10, 22, 25, 24, 8, 21, 15, 29, 25, 31, 19, 19. Two rounds now with no turn and no prompt item;
each round's wrongs are the edges of the last round's new stops.

Lanes (`--account auto`, CONTRACTS §16.183 to §16.187, brief `lanes/COMMON25.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r26-land.lock`; scratch under `round26/`; back-channel
pane 134): RA 25 (ruling 11's pairing, the new start's pages, the side files' report words, its other words), B 22
(ruling 14 first, ruling 13 in status, doctor and every command, the new start's counts, round 25's seams), RB 14 (all
of install.sh: its inlined lost-manifest copies, tails and the shell report's pairing and stops), E 5 (the speech fix
for an old toolchain, the OCR URL cut), D 10 (guides, CONTRACTS records, deploy-pack tests). Lead: E71 (the cache
brief's stale CI line; round 26's brief is built from the repo copy, which carries lane D's).

### B13 round 26 landed (2026-10-24)

All five lanes landed; CI green on `905df01b` (ci `40062449895`); the prompt is still v33. Notes `lanes/r26-<lane>.md`,
lead notes `round26/lead-notes.md`. All 77 items are built, each with a test that fails without it, and none carries
a not-built reason. Lane B built E25 first: `purge --queue` and every governance write now refuse over a lost
manifest. Lane RA built one more at lane B's ask (`c98ea001`: the person's bucket for a lock, heartbeat or governance
file that is not a file), and lane RB the install.sh halves of lane D's E55 and lane B's E62.

| Lane | Landed (lane commits) |
|---|---|
| E | `68be7e8d..45203314`, 2 commits |
| B | `a41923ed..ac5b0daf`, 6 commits |
| RA | `1c38ca58..df65185e`, 18 commits |
| RB | 16 commits, ending `69cd15b1` |
| D | 7 commits, `54982c6c` to `905df01b` |

Learnings: lane RA read ruling 11 refined and built it at once, and trunk stayed green all round. Before round 27 the
operator asked whether the loop ever ends; the lead's measured answer (one page,
`~/.cache/agentsync-bring-back/b13/report/b13-value-by-version.pdf`): the person's normal path has finished in one
command with no turn in every rehearsal since v22, while confirmed findings stay near 80 a round (2,055 built over
24 rounds, no downward trend), because each round's fixes give the next round's probes new paths. Two operator
decisions are open before any round 27: `e869fac7eb34` (stop the loop, paste v33, and adopt a stop rule a round can
meet; lead 85% for yes) and `290215743799` (send the committed code, as a /tmp export, to OpenAI through Codex for a
second-vendor reviewer; lead 75% for yes; measured: Codex weekly meter at 0% on plan "prolite", Gemini through
Antigravity offers only 3.8 Flash and no Gemini 4 Argon to this login).

Round 27, only if `e869fac7eb34` is answered no: the round 26 workflow on this section's landed tip, BASE `081db13b`.

### B13 round 27: v33 rehearsal, cross-lane review, harvest (2026-10-24)

The stop decision was re-filed as `b4780d7bb60e` (default: keep looping, deadline 2026-10-31), so round 27 ran on
`62a90bca` in two runs: `wf_e887b970-e18`, then `wf_db01d499-e70`, which re-ran rehearsals A, B and F and the harvest
after an operator message sent mid-run had led them to answer it instead of their tasks (their 22 off-topic findings
are set aside; every prompt now opens with a guard line). All six scenarios pass their EXPECTED; no name leaks. 97
findings, 91 confirmed (2 turns, 25 wrong, 64 small), 6 refused. None needs prompt text: v33 stays the prompt to paste.
The two turns and most wrongs sit on round 26's own new paths: ruling 13 named link kinds at the governance files and
the heartbeat that no reader caught (a link to nothing at holds.json read as no holds, and `purge --queue` erased a
page under a hold on `all`: data integrity); step 2's NEXT over a lock or heartbeat that is not a file is the generic
re-run, not the person's fix (the two turns, P22 and P124); and after the person's new start, a purge queued before
the manifest was lost is still invited blind (P40, P112). Lead rulings for round 27 (in `lanes/COMMON26.md`), all
code-only: (15) every link kind at a governance file or the heartbeat is the person's, and a hold list that cannot be
read is never an empty one; (16) step 2's NEXT gives the person's fix for a lock, heartbeat or governance file that is
not a file, and step 1 predicts it, through the installed agentsync's own judge; (17) the new start's queued purges
wait for the next sync's manifest, then read as not resolvable, and are never invited blind; (18) beside a linked
manifest, the -wal and -shm are judged beside its target.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81,
77, 91 over rounds 2 and 4 to 27; turns 0 for seven rounds, then 1, 2, 0, 0, 2, 0, 0, 2; wrongs as before, then 25.

Lanes (`--account auto`, CONTRACTS §16.188 to §16.192, brief `lanes/COMMON26.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r27-land.lock`; scratch under `round27/`; back-channel
pane 271): RA 28 (the report: the lost link's cases, a too-old Swift, earlier attempts, the new start's counts), B 26
(ruling 15 first, then 17 and 18's helper, status, doctor and the commands), RB 18 (all of install.sh: ruling 16 and
the two turns first), E 8 (cycle.py's new start and restored run numbers, the speech docstrings, the OCR URL cut), D 9
(ship-land's early read of trunk's CI, guides, CONTRACTS records). Lead: P130 (the harvest's umbrella note, built as
P131 to P138) and P139 (not built, measured: a check of only trunk's last completed run saves nothing in round 26 and
would cancel trunk's runs; lane D's P140 builds the safe early read).

### B13 round 27 landed (2026-10-25)

All five lanes landed; CI green on `07b43cc5` (ci `40185998162`); the prompt is still v33. Notes `lanes/r27-<lane>.md`,
lead notes `round27/lead-notes.md`. 89 of the 91 items are built, each with a test that fails without it; the lead's
P130 was the harvest's umbrella note, built as P131 to P138, and P139 is not built (measured, above). Two built items
leave a measured half unbuilt: P54's code (a fix makes the URL cut worse; its docs and pins are built, lane E) and
P112's step 3 (storing page paths at queue time and purging them after the new start runs the very purge ruling 17
says must wait, and a path purge erases every blob ever at that path; lane B). Lane B built ruling 15 first: a link
to nothing at holds.json is now the person's FAIL and never an empty hold list. P111 landed in two halves: lane B's
rule shipped switched off behind `manifest._UNREAD_IS_LOST`, and lane RB's install.sh commit turned it on together
with its inlined copies, since RB's test pins those copies to `manifest_lost`.

| Lane | Landed (lane commits) |
|---|---|
| D | `c0787278`, `2a1b5d9c`, 2 commits |
| B | `763ae518..a3aecbbb`, 10 commits |
| E | `24f6e8da..14fe8e75`, 8 commits |
| RB | `1d9d0df7..c99da160` and `c93fabd6..b75e5016`, 11 commits |
| RA | `4314c464..07b43cc5`, 23 commits |

Learnings: the Mac ran at a load of 270 to 1,000 for most of the round (CPU not saturated: blocked processes), so a
land gate took about 1 h 50 min and five lands took 14 hours; RB's land went red once on a load-only 5-second probe
and once on a two-letter word, and a timing mark (`c99da160`) ended the first. A hook's `git restore` guard matched
the word "restore" in a lane's pytest `-k` filter and held lane E at a prompt for about two hours until the operator
answered it. Lanes finished building within one to three hours; the rest was the land queue.

Residuals the lanes named for round 28's harvest (in their notes): a manifest restored from a copy older than a
queued purge still dequeues a purge that targets nothing (lane B); install.sh's `misplaced_words` still calls
holds.json "agentsync's governance file holds.json" where the report says "the hold list" (lanes RB and RA); P111's
report half, now that the rule is on (lane RA).

Round 28: the round 27 workflow (`round27/round27-claude-only.js` through `round28/tools/edit.py`, with the guard
line first in every prompt) on this section's landed tip, BASE `f04c61db`, ids I<n> (no §16 line uses the letter),
outputs `round28/`; scenario C keeps the v32 block at `c93fcd96` and the v31 block at `ed901cd3`, since no lane
changed the prompt.

### B13 round 28: v33 rehearsal, cross-lane review, harvest (2026-10-25)

Run `wf_1ea50d66-9f0` on `519b847c` (120 agents, 0 errors, 84 minutes; every prompt opened with the guard line, and
every finder's report is its own). All six scenarios pass their EXPECTED; no name leaks. 109 findings, 104 confirmed
(1 turn, 26 wrong, 77 small), 5 refused. None needs prompt text: v33 stays the prompt to paste. Most wrongs sit on
round 27's own new paths. The hold list still has readers ruling 15 did not reach: governance/ itself as a link to
nothing, a damaged or wrong-shaped hold list, and holds the audit trail records but holds.json lacks; in two probes a
page under a hold on `all` was erased (I87, I88: data integrity). An empty manifest made after the new start reopens
P40's blind purge (I61, I92). P111, now live, is still worded as "heartbeat.json records earlier passes" in every
reader. A `.url` whose URL carries a password is published with it (I40). The one turn (I52) is the ownership rule
at a linked manifest's target. Lead rulings for round 28 (in `lanes/COMMON27.md`), all code-only: (19) a hold is
never lost to a reader; (20) no blind purge from a manifest that knows nothing; (21) the lost-unread heartbeat is
said as that, with its own fix, everywhere; (22) a secret in a file's text is never published, each governance file
has one name, and the ownership rule holds at a linked manifest's target.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81,
77, 91, 104 over rounds 2 and 4 to 28; turns 0 for seven rounds, then 1, 2, 0, 0, 2, 0, 0, 2, 1; wrongs as before,
then 25, 26.

Lanes (`--account auto`, CONTRACTS §16.193 to §16.197, brief `lanes/COMMON27.md` mirrored in `docs/lanes/COMMON.md`;
every lane lands with `scripts/ship-land.sh --lock /tmp/b13-r28-land.lock`; scratch under `round28/`; back-channel
pane 271 or its successor): RA 29 (the report: ruling 21's half, the purge queue over a new start, the settling
pages), B 23 (ruling 19 first, then 20 and 21's doctor words), RB 24 (I52 first, one name per governance file,
ruling 21's tails), E 10 (I40 and I39 first, the new start's runs and kept pages, Finder order), D 17 (the guides,
CONTRACTS records, ship-land's header). Lead: I97 (the harvest's umbrella note, built as I87 to I96).

### B13 round 28 landed (2026-10-25)

All five lanes landed; CI green on `3f1dee54` (ci `40260160828`); the prompt is still v33. Notes `lanes/r28-<lane>.md`,
lead notes `round28/lead-notes.md`. All 103 lane items are built, each with a test that fails without it (I67 is a
test-only fix, measured on a mutant); I97 was the harvest's umbrella note, built as I87 to I96. Lane B built ruling 19
first: a hold is never lost to a reader, and no page under a hold is erased (I87, I88). Lanes B and RB landed as one
push at lane B's request, since RB's install.sh seams for rulings 19 and 20 need B's code and B's branch alone failed
two of RB's tests. Lane D landed I99 first: run `40200999412` (d1f72d6f, docs only) took 49:59 against the job's
limit of 50, which is now 60.

Optional halves left unbuilt, each measured in its lane's note: I53's `words=unread` field in sync-stops.log (the
writer is lane B's `cli.py`, not the report; RA); I90's `hold --repair` (the CLI command set is frozen, and I87's
audit replay keeps the lost holds; B); I16's listing of both sides in the sync error (the guard now stops on the -shm
first; B); I40's converter mask of the userinfo (masking the `.url` page publishes it instead of withholding it; E);
I71's code guard (the page is true now; D); the step 2 file-first order a verifier asked for in I50 (steps 1 and 2
already agree; RB).

| Lane | Landed (lane commits) |
|---|---|
| E | `4301c7c2..71388552`, 8 commits |
| D | `396b16b6..03d0329f` and `221aa943..3f1dee54`, 15 commits |
| RA | `5f816fdf..a0bf93d4`, 19 commits |
| B | `ee12b040..f1086507`, 15 commits, in one push with RB |
| RB | `a41aae72..be3d027d`, 13 commits |

Learnings: the lanes built in half an hour (E) to two and a quarter hours (RB) and the rest was the land queue,
again; at a load of 80 to 250 the round took seven and a half hours from the fire to the last CI read. A hook's
prompt on `git push --delete` (lane B removing its own share branch) held lane B between one and two hours (seen at
16:10 and 17:00, gone by 18:07) until the operator answered it. A lane branch that another lane rebases is on trunk by
content but not by ancestry, so `round28/tools/remove-worktrees.sh` now checks content (`git cherry`, then a
same-subject trunk commit whose `git range-diff` changes no diff line) and deletes with `-D`.

Residuals the lanes named for round 29's harvest (in their notes): the report's buckets for lane B's ruling 19 FAILs
(governance/ not a folder, a damaged hold list, a lost hold, the held-queue warn), which needed B's words on trunk
first (RA, RB); a hold set before any sync wrote heartbeat.json has no record outside governance/ until the next pass
(B).

Round 29: the round 28 workflow (`round28/round28-workflow.js` through `round29/tools/edit.py`, with the guard line
first in every prompt and the harvest list in `round29/tools/harvest-lead.txt`) on this section's landed tip, BASE
`d1f72d6f`, ids C101 and up (every single capital is used in CONTRACTS §16, and C's ids stop at C18), outputs
`round29/`; scenario C keeps the v32 block at `c93fcd96` and the v31 block at `ed901cd3`, since no lane changed the
prompt. Codex reviewers run beside the Claude finders and verifiers, through `~/.claude/scripts/xvendor-review.sh`.

### B13 round 29: v33 rehearsal, cross-lane review, harvest, Codex beside Claude (2026-10-26)

Run `wf_bcf590be-db1` on `911987c0` (132 agents, 0 errors, 99 minutes; the guard line first in every prompt). All six
scenarios pass their EXPECTED; no name leaks. 116 findings, 111 confirmed (0 turns, 39 wrongs, 72 small), 5 refused
(C109, C117, C127, C136, C153). None needs prompt text: v33 stays the prompt to paste. 19 of the wrongs are data
integrity, most on round 28's own new paths: the hold record that heartbeat.json carries leaves with the very file a
fix moves aside, and twice a page under a hold on `all` was erased (C199, C170); a queued purge whose source no sync
has listed since the new start is run blind (C201); a lost hold's owner prints in clear in both reports (C101); two
link shapes beside a missing manifest start a new one unasked (C118, C202); a label fragment in RFC 2047 words
publishes a labelled mail (C144). Codex ran once of five calls: its report review (C214 to C216) finished at 23:51, and
Codex's own self-update at 23:50 moved its binary into `~/.codex/packages/`, where the wrapper's read-confined
profile cannot launch it, so the core, convert and docs reviews and the second opinion failed (a launch failure, not
a permission refusal; the wrapper's fix is dispatched to claude-infrastructure). A triage workflow (`wf_8429f764-be8`)
placed every item once and drafted the rulings, which the lead took with two changes (C198 and C213 to the lead).

Lead rulings for round 29 (in `lanes/COMMON28.md`, mirrored in `docs/lanes/COMMON.md`), all code-only: (23) a
private value never prints in clear in either report; (24) a hold's record outside governance/ survives every move a
fix asks for; (25) after a new start a queued purge is judged per source; (26) a purge that finds nothing leaves the
queue only by an audited act (a `purge --queue` flag: §16.10 lets an option be added by editing its pinned list);
(27) ruling 19's governance states are the person's in every reader; (28) no governance file's fix is a bare move
aside; (29) a lost manifest is judged once, by `manifest.lost_words`; (30) the heartbeat's fix comes first only where
it has a fix of its own; (31) ruling 22 holds for every conversion reason and mail label; (32) nothing invites
`purge --queue` for an entry a hold keeps.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81,
77, 91, 104, 111 over rounds 2 and 4 to 29; turns as before, then 0; wrongs as before, then 39.

Lanes (`--account auto`, CONTRACTS §16.198 to §16.202, land lock `/tmp/b13-r29-land.lock`, scratch under `round29/`,
back-channel pane 3 or its successor): RA 37 (ruling 23 first, then the report halves of 25, 27 and 30), B 41
(rulings 24, 28, 25, 29 and 26's reason first: data integrity; one lane, since its items share governance.py and
cli.py; ruling 29 lands with RB as one push), RB 12 (C160 first, then the install.sh halves), E 10 (ruling 31 first),
D 9 (CI's times first; the guides after the lanes). Lead: C198 (the harvest's umbrella note, built as C199 to C206)
and C213 (the two lane branches round 28 left: content-checked and deleted, `b13/r27-ra` and `b13/r28-rb`). The
rehearsal kit's fix for macOS's midnight `/tmp` cleaner, which deleted the fixture's empty folder in three
sandboxes (C149, C150), goes into round 30's script.

### B13 round 29 landed (2026-10-26)

All five lanes landed; the prompt is still v33. Notes `lanes/r29-<lane>.md`, lead notes `round29/lead-notes.md`. All
114 items are built (111 from the run, and C221, C222 and C224 from the Codex follow-up `wf_ab84c1cf-f57`), each with a
test that fails without it, measured on the pre-lane source or a mutant; C198 was the harvest's umbrella note, built
as C199 to C206. Ruling 29's two halves landed in one push: lane B's `manifest.py` commit rode on lane RB's branch
(`2cb94212` in `7d46666a`), after lane B's own first land went red on RB's parity test (nothing pushed). Corrections
the lanes measured: C181's verifier fix never runs alone (a stub whose retries are used up is settled and never
fetched again), so lane E added a once-per-manifest re-read; the verifier's pin for `ß` in C212 is not Finder's order
on this Mac; C160 reproduces only under the Mac's UTF-8 locale (`[A-Z]` matches lowercase letters there under
/bin/bash 3.2), which is why Codex, run in the C locale, could not reproduce it.

One optional half left unbuilt, measured in lane RB's note: C145's (3), showing every per-source `alarm:` line at
install. The reason, as corrected in round 30 (C391; the first text said v33 had no `alarm:` rule at all): v33's step
3 has an `alarm:` rule (show it, do not act on it, do not log it; README.md:569-573), but step 2, where install.sh's
output arrives, has none, and a DLP alarm's text names `purge --queue`, so printing alarms at install waits for a
prompt whose step 2 names `alarm:` lines as the person's (never run their commands, never purge), rehearsed with a
published Contoso page the new rules match. The round is code-only with v33 frozen. Under v33 the person learns the
count from the install summary's "held back: its page contained a credential" clause and, for a page already
published, from the queued purge (status, and step 3's WAITING ON YOU line) and the WARNING in the install.out tail;
they do not learn the stubbed page's name at install.

| Lane | Landed (lane commits) |
|---|---|
| E | `0d7dd246..673a72a0`, 9 commits |
| RA | `19b8f77b..011195c6` and `2d245389..e8145527`, 18 commits |
| RB | `57ff9fd2..7d46666a`, 14 commits, lane B's `manifest.py` among them |
| B | `9ff886fe..5f7aba15` and `2ecc4d4a`, 14 commits |
| D | `c29e522c..5f6f423b` and `6d72772b..b2b88e5d`, 9 commits |

Learnings: from the fire (03:20) to the last land (17:50) took fourteen and a half hours; the lanes built for one to
three and a half hours, and the rest was the land queue (eight lands, gates of 35 to 60 minutes) and CI (pytest's
step up to 49 minutes, and one 40-minute wait for a macOS runner). A shared commit in a joint push wants one owner:
lane B rebased its cherry-pick of a commit lane RB had since changed, so the `install.sh` half dropped and its
CONTRACTS paragraph applied a second time (`a4e8a94a`, removed by `2ecc4d4a`). The classifier refused lane B's `kill`
of its own land gate; letting the gate fail cost one gate run under the lock. A cross-vendor reviewer must run in the
Mac's locale, or a locale defect reads as not reproducible (C160).

Residuals the lanes named for round 30's harvest (in their notes): a refused audit append after a queue run's purge
makes the sync refuse rather than publish, and a lost path-glob is flagged only when the list holds no glob at all
(B, left as fail-closed); a regular heartbeat.json of damaged JSON keeps the record words in the shell report (RB);
run shellcheck after every edit to a program inlined in install.sh, since two apostrophes in such comments broke the
shell parse while the tests that read the programs passed (RB).

Round 30: the round 29 workflow (`round29/round29-workflow.js` through `round30/tools/edit.py`, the guard line first
in every prompt, the harvest list in `round30/tools/harvest-lead.txt`, the rehearsal kit's touch after every copy for
macOS's `/tmp` cleaner) on this section's landed tip, BASE `b2724d9c`, ids C301 and up, outputs `round30/`; scenario C
keeps the v32 block at `c93fcd96` and the v31 block at `ed901cd3`, since no lane changed the prompt. Codex reviewers
run beside the Claude finders and verifiers, with the Mac's locale set.

### B13 round 30: v33 rehearsal, cross-lane review, harvest, Codex beside Claude (2026-10-26)

Run `wf_3fba4411-29b` on `fa32c452` (123 agents, 0 errors, 83 minutes; the guard line first in every prompt). All six
scenarios pass their EXPECTED; no name leaks; every fixture folder survived (the kit's touch). 107 findings, 100
confirmed (0 turns, 27 wrongs, 73 small), 7 refuted (C330, C354, C375, C384, C388, C393, C406). None needs prompt
text: v33 stays the prompt to paste. 16 of the wrongs are data integrity, most on round 29's own new paths: a
suppression list that lost part of a purge passes the loss check, so purged content can be published again (C383,
C401 to C403, C344); the records governance/ keeps do not survive the folder's loss, and the hold record's own fix
erases a page under a hold on `all` (C381, C382, C351); a queued purge the queue lost cannot be redone from anything
agentsync prints (C370, C312, C350); the setup report migrates and rewrites the manifest it reads (C394); the secret
scan prints a key carried in a file's name (C328); a labelled mail an older reader let through stays published
(C404, C360); a matter name used as a hold scope prints in a public-ready report (C386). Codex's four reviews ran
(the locale fix held) and gave 13 of the confirmed items; its second opinion was refused by the classifier as data
exfiltration: its prompt has no DIFF line, yet the slot's step 1 told its agent to add one, and it pasted 348 KB of
diffs. The round stays Claude-only there (no re-ask); round 31's script writes a prompt with no DIFF line as it
stands (`round31/tools/edit.py`). A triage workflow (`wf_41258a5e-941`) placed every item once and drafted the
rulings, which the lead took with C391 moved to the lead.

Lead rulings for round 30 (in `lanes/COMMON29.md`, mirrored in `docs/lanes/COMMON.md`), all code-only: (33) a
suppression list that lost part of what a purge suppressed is lost, and only an audited act clears it (`purge
--accept-lost <at>`); (34) what governance/ keeps survives the folder's loss (`purge-record.json` beside
hold-record.json); (35) a queued purge the queue lost can be redone from what agentsync prints, and only a purge of
that item closes it; (36) writing the setup report never writes the manifest; (37) a page the secret scan stubbed is
announced once, by its alarm; (38) a mail an older label reader let through is screened again by the new one; (39)
ruling 23 holds for every hold value and every one of agentsync's own words; (40) a governance list that lost what
the audit trail records is the person's in every reader; (41) the hold record is named as itself in every reader;
(42) ruling 28's governance fixes name what they mean, from what is on disk; (43) every purge-queue line says only
what is true of the entries it counts; (44) both reports name a lost stop's kind one way, install.sh following
Python; (45) ruling 31 holds for every stored reason, every spelling and its one-shot re-read. Not built by the
lead's ruling: C371's optional (4), `--forget` running its own probe (`--forget` is the audited exit that erases
nothing, and `purge --queue` already looks each entry up).

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81,
77, 91, 104, 111, 100 over rounds 2 and 4 to 30; turns as before, then 0; wrongs as before, then 27.

Lanes (`--account auto`, CONTRACTS §16.203 to §16.207, land lock `/tmp/b13-r30-land.lock`, scratch under `round30/`,
back-channel pane 3 or its successor): RA 31 (ruling 39's hold scope first, then the report halves of rulings 40,
41, 36 and 43), B 37 (rulings 33 to 37 first: data integrity; one lane, since 33 to 35 all change governance.py's
loss readers), RB 10 (ruling 39's install.sh half first, then ruling 44), E 8 (ruling 38 first, then 45), D 13 (CI's
times first; the guides after the lanes; C380's /tmp note, whose kit half goes into round 31's script). Lead: C391,
the corrected not-built reason for C145 (3) in "B13 round 29 landed" above.

### B13 round 30 landed (2026-10-27)

All five lanes landed; the prompt is still v33. Notes `lanes/r30-<lane>.md`, lead notes `round29/lead-notes.md`. All
100 confirmed items are built: 99 in the lanes (RA 31, B 37, RB 10, E 8, D 13) and C391 by the lead. Three optional
halves are not built, each with a measured reason: C371's (4), `--forget` running its own probe (the lead's ruling:
`--forget` is the audited exit that erases nothing, and `purge --queue` already looks each entry up); C377's flip of
the Python reader (ruling 44: C396 measured that it reopens ruling 21's mismatch; its cross-reader test is built);
and the report's hold bucket taking status's own lost-hold wait line (lane RA: no report reader ever meets that
line). Lane B's first land gate went red on lane RA's C302 test (four new audit-trail clauses were not fixed
phrases; nothing pushed), so lane B reworded them to the report's existing phrases (`741c6717`; `47527de2` records
that no new phrase is needed). Ruling 35's first build printed a rebuilt purge id's file number in clear; lane RA cut
it in Python (`e4945ad5`) and lane RB in the shell report (`28b87af5`, grown scope).

| Lane | Landed (lane commits) |
|---|---|
| E | `029256fc..42996106`, 6 commits |
| RB | `e3846370..5f2040b8`, `2919a7b9` and `28b87af5`, 10 commits |
| D | `85ea5288..5b612905`, `f413839f..79d712fc`, `2e52bb87` and `3890ebd2..a67420d4`, 11 commits |
| RA | `016a5b29..c89051b3`, `f5bfa77f`, `e4945ad5` and `9ce92725..70cc2dd4`, 30 commits |
| B | `174fde81..741c6717` and `aa2fafec..fc307fcf`, 24 commits |

Learnings: from the fire (22:30 on 2026-10-26) to the last land (15:30 the next day) took 17 hours. Lane B was the
critical path: rulings 33 to 35 change governance.py's loss readers, and lanes RA, RB and D each held a last batch
for its land. Land gates took 50 minutes to just under 2 hours (5,960 tests at a load of 65 to 130). Lane B's
account hit its weekly limit mid-round, and the recovery tool never moves a lead whose teammates are live, so the
lane waited about an hour for the reset. One CI run lost its hosted runner (40523387625, attempt 1: the pytest step
never reported); its re-run passed, and lane B landed its second batch with `--allow-red-trunk` past that run. A
whole-tree test owned by one lane (C302's fixed-phrase scan) can turn another lane's gate red: a lane that adds
printed words runs that test before it lands.

Residuals the lanes named for round 31's harvest (in their notes): neither report cuts a `--reason` value in
queue_lost's lines (RB); the hold reasons `legal`, `review`, `records` and `record` print as report words, the same
class as C302 (RA); lane B's notes for lane RA (C326's class, C357's pin, C321's bucket, C318's words) and lane D
(setup-feedback.md's roles).

Round 31: the round 30 workflow (`round30/round30-workflow.js` through `round31/tools/edit.py`: the Codex second
opinion's prompt is written as it stands, with no diff added; the rehearsal kit reruns the `shasum -c` check of every
fixture copy before step 2 and at each act's end, keeps no copy in `/tmp` past 3 days and never touches the files,
since agentsync reads ctime) with the harvest list in `round31/tools/harvest-lead.txt`, on this section's landed tip,
BASE `ccf696b7`, ids C501 and up, outputs `round31/`; scenario C keeps the v32 block at `c93fcd96` and the v31 block
at `ed901cd3`, since no lane changed the prompt.

### B13 round 31: v33 rehearsal, cross-lane review, harvest, Codex beside Claude (2026-10-28)

Run `wf_79385220-d33` on `c8a4298b` (99 agents, 0 errors, 8 hours 44 minutes). All six scenarios pass their
EXPECTED; no name leaks; every fixture copy matched its folder list and its `shasum` check. 84 findings, 83 after
dedupe, 77 confirmed (0 turns, 19 wrongs, 58 small), 6 refuted (C509, C555, C568, C570, C572, C574). None needs
prompt text: v33 stays the prompt to paste. 7 of the wrongs are data integrity, most on round 30's own new paths: a
lost erasure hidden behind an ok line (C539); the staging path kept in a page, QUARANTINE.tsv and the manifest (C546,
C547); a project name behind a link printed in a public-ready report (C529); install.sh's governance-folder fix
moving a kept folder aside and dropping its holds (C521); round 28's hold record lost with heartbeat.json (C580); and
more suppressions accepted than meant (C582, C553). Codex's four reviews ran and gave 9 findings, and its second
opinion ran this time (round 31's script adds no diff to a prompt with no DIFF line): 19 opinions, none disputed. A
triage workflow (`wf_70d91859-298`) placed every item once and drafted the rulings.

Lead rulings for round 31 (in `lanes/COMMON30.md`, mirrored in `docs/lanes/COMMON.md`), all code-only: (46) a queued
purge the queue lost is always shown, and closes only when the queue's own run would close it; (47) `--accept-lost`
and every line that names it say exactly what accepting clears and gives up; (48) ruling 45 holds for every stored
reason already written and every spelling; (49) a typed folder is cut wherever a report prints it; (50) install.sh's
governance-folder fix says a folder was kept when the hold record names a hold; (51) the records page's lost-manifest
steps keep every hold; (52) ruling 36 holds for every manifest read a report makes, side files included; (53) a
re-screen the mail label reader queued is counted and named as the reader's; (54) ruling 45's re-read says what each
row came to, on every upgrade path; (55) ruling 39 holds for every hold word (record, records, legal, label, review);
(56) the `<source-N>` legend names what C386 registers; (57) install.sh's sync-stop kinds are
setup_report._SYNC_STOP_KINDS, every one; (58) a signal stop is named by its signal in every reader, never in a
nested bracket; (59) the label-unreadable share of the refused count reads apart from the withheld share; (60) beside
a governance stop, every reader gives that stop's own fix and wait; (61) the rehearsal kit checks each fixture copy
mechanically; (62) the lead's calls: C565 is built, which reverses round 30's not-built ruling on C371's (4)
(`--forget` running its own probe: the verifier measured that without it, forgetting one entry forces every runnable
queued erasure to run first), and C571 raises ci.yml's limit from 70 to 80 minutes (the slowest round 30 job took
59:43; round 29 raised 60 to 70 at the same margin). The kit halves of C503, C504, C528 and C573 are the lead's, in
round 32's script.

Convergence: confirmed 127, 85, 99, 79, 77, 79, 80, 74, 87, 76, 74, 84, 68, 90, 107, 91, 76, 84, 77, 88, 93, 102, 81,
77, 91, 104, 111, 100, 77 over rounds 2 and 4 to 31; turns as before, then 0; wrongs as before, then 19.

Lanes (`--account auto`, CONTRACTS §16.208 to §16.212, land lock `/tmp/b13-r31-land.lock`, scratch under `round31/`,
back-channel pane 3 or its successor): RA 16 (ruling 49's typed folder first, then ruling 55's hold words in one
commit with install.sh's list), B 17 (ruling 46 first: data integrity; then 47, 52, 59, 60 and C565), RB 9 (ruling
50's kept folder first, then 57 and 58), E 19 (ruling 48 first: data integrity; then 53 and 54), D 16 (rulings 51 and
47's pages first; CI's limit; the guides after the lanes).


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
