# Corporate Mac bring-back file, second round, triaged (2026-10-07)

Scope (frozen): triage the second bring-back file from the corporate Mac (prompt v8, main `41cf618`) against main
and the v9 branch; for each item record result, evidence and status: fixed on the v9 branch (commit), expected by
design, not built, or the operator's decision. Record what the field measured about OCR and the new evidence for
the two open operator decisions.

Method: Workflow run `wf_6e7fa11d-13e`, 29 agents (14 verifiers, 14 skeptics, 1 critic): one verifier and one
skeptic per item (C1 to C14) and one completeness critic, whose real findings are rows U1 to U18. Where the
skeptic refuted a verdict (C2, C3, C4, C8, C10, C14), the corrected status, root cause and fix are the ones
recorded here. Line numbers were read on main `41cf618`. The agents were read-only and ran no test suite. The
fixes were built afterwards on the v9 branch (`bb/b8-configured-folders`, cut from `41cf618`). A commit is cited
by the sha it has on that branch, with its subject. None is on main yet.

Names: the paste's sections 2 and 3 are unredacted, and section 1 still shows four name tokens in two old
friction lines (C12). This doc uses only the report's own placeholders (`<source-1>`, `<folder-1>`, `<org-1>`).
Evidence lines are quoted from the redacted section. The paste, the patch and the full findings stay outside the
repository. The first triage is [corporate-bring-back-2026-10-06.md](corporate-bring-back-2026-10-06.md).

## Verdict

- **OCR works on the field Mac.** The helper is ready. All 16 images have a page. 99 PDFs, 185 decks and 140 Word
  files carry an OCR identity, and none is without one. The one-time re-read finished in all 16 sources: 442
  reads in six runs, 576 s of helper time, no run out of OCR time, no helper or engine failure (§ 1). The three
  limits (180 s of OCR, 120 s of re-reads, 12 syncs) are now timed on a real mirror and stay as they are.
- **The setup was not one command, and the report said it was.** Two stops each cost the operator a turn, about
  29 and 22 minutes of a 57m47s session: the folder question asked again on a Mac that already syncs its folders,
  and `install.sh` exiting 1 on a permissions FAIL that agentsync clears by itself (§ 2). The Summary still read
  "fully one command" (C2). All three are fixed on the v9 branch.
- **Every request in the v8 fix request is built on the v9 branch:** items 5, 6 and 7, and the open half of
  item 4. None adds a command, flag, installer option, config key or environment variable. Rejected, because
  they add surface or make the read-only check write: a `doctor --fix-permissions`, status or doctor tightening
  paths, a warn in place of the FAIL, a prompt sentence that allows a `chmod`, and a new outcome name.
- **One claim in the fix request is wrong** (U2). It says the background job "ran pre-update code". The report
  shows two background runs of the new build between the failed run and the hand-run `chmod`. So that `chmod`
  was very probably not needed (inferred: no status ran in between).
- **An action the first triage gave the operator would have broken background sync** (C11). `agentsync
  install-agent` from the updated tool would have written an interpreter path the launcher refuses, exit 64 at
  every run. Nobody ran it. It is fixed on the v9 branch and must not be run from an earlier build.
- **The empty-folder evidence does not settle O1.** 5 of 5 folders are materialised and empty, none is dataless,
  and no mirrored file lies below them. But the skeptic found that the first complete pass of that source would
  release every held deletion at once, past the breaker and the two-pass check. The same holds for the `exclude`
  line the tool advises today. Either choice needs a companion change, which is not built (§ Open operator
  decisions).
- **Online-only images cost nothing on this Mac today** (O2): 0 images are not on this Mac.
- **The 14 queued purges are to be previewed, not run** (C8). All 14 read "no row", which has two causes with
  opposite answers. One of them erases 14 live pages and their history (§ The purge queue).
- **Section 1 still shows four name tokens**, in two friction lines written under v7 (C12). The residue check
  flagged none of them. The smallest fix is the operator's: edit those two lines on the field Mac.
- **Counts.** Of the 14 items, 12 have a commit on the v9 branch (C1 to C9, C11, C13, C14). C10 is the operator's
  decision. C12 is a recorded limit with one new small defect, not built. The critic added 18 rows: one is the
  first stop (U5), six fold into the items above, and eleven are readings of the report that need no new code.
  The prompt's text changed, so it is v9.

## Inputs and version

| Fact | Value | Source |
|---|---|---|
| Report | `agentsync setup-report`, generated 2026-10-07T16:56:20Z in 1.6 s of its 12 s; macOS 15.7.3, arm64, MDM-enrolled | Run metadata, Environment |
| Setup prompt | v8, attempt 4 of 4. Earlier: attempt 1 v6 (2026-09-30), attempt 2 with no header (2026-10-06T02:16Z), attempt 3 v7 (2026-10-06T16:42Z) | friction log |
| install.sh commit | `41cf61807403`, the checkout's HEAD and on origin/main | Run metadata, install.log |
| Agent | GitHub Copilot CLI 1.0.92. The name is no longer redacted: first triage I1 holds in the field | Summary |
| Computed outcome / run type | fully one command / Real Mac. Wrong for this attempt (C2) | Summary |
| Attempt 4 | started 15:58:32Z. Three `install.sh` runs: `--list-folders` exit 0 after 1 s; install exit 1 after 19 s (16:27:56Z); install exit 0 after 141 s (16:50:04Z). `--report-only` ran twice | Installer, friction lines 16-25 |
| Config | 16 sources, all live: 2 cloud folders and 14 inbox-kind | Configuration |
| Doctor | 96 checks: 88 ok, 1 info, 7 warn, 0 FAIL. 2 warns are expected (ad hoc launcher), 5 are not | Doctor |
| Background | both jobs loaded. Poll: 613 runs, last exit 75. Reconcile: 53 runs, last exit 0 | Background runs |
| Fix request | status of the four v7 requests, three new requests (items 5 to 7), a 12-bullet "Not used" section. 53 of its 122 lines are the v7 request again (C3) | section 2 |
| Local work | the patch of the first round, byte for byte: 3,587 lines, 72% of the file's bytes. This attempt kept nothing (C4) | section 3 |

Four corrections to how the paste reads:

- The background job ran the new build after the update, not "pre-update code" (U2).
- The OCR table's five rows are not five background runs, and they do not add up to the sums above them (§ 1).
- Step 3 ran two syncs, not one. One of them counts against the cap of 12 (C14).
- The agent calls the incomplete source `<folder-2>`. The report calls it `<source-1>`. They are one source, and
  the report gives no mapping.

## 1. What the field measured about OCR

Every number is the report's own (its OCR part, Summary and Repeat conversions part), unless marked.

| Measure | Value |
|---|---|
| Helper | ready: apple-vision revision 3, helper 2.0.0. Label rule in `[policy]`: off |
| Images | 16 files, 16 with a page |
| Images that are not on this Mac | 0 files, 0.0 MB: 0 online-only, 0 of a Graph source |
| PDF files with a page | 99 with an OCR identity, 0 without |
| Deck files with a page | 185 with, 0 without |
| Word files with a page | 140 with, 0 without |
| OpenDocument files with a page | 0 |
| Scanned PDFs left as a stub | 0 OCR not run, 0 OCR found no text, 0 over the page limit |
| Pages with an engine identity | 440, all one identity (99 + 185 + 140 + 16) |
| One-time re-read | scan finished in 16 of 16 sources; 0 files left from before OCR; 0 given up; 1 failed once, in one inbox source |
| Reads again | 442, over the 6 runs that had an engine; 0 of them could not be converted |
| Helper time | 576 s over those 6 runs |
| Runs that used up the 180 s of OCR time | 0 of 6 |
| Runs that ended with the helper not working | 0 of 6 |
| Conversions without OCR | 0 for time, 0 for a stopped helper |
| Engine failures | 0 |
| Conversions that left pages past the page limit unread | 0 |
| Conversions that left pictures past the picture limit unread | 3 |
| Files waiting for a later cycle's OCR | 0 after the newest run; 0 waits in the 200 runs read |
| The installer's first sync | 128 s, of which 110.2 s was helper time; converted 6, deferred 0 online-only |
| Repeat conversions | 0 of 6 recording runs converted a file again from the same bytes. Converter cache: 373 reuses, 153 of them by run 1174 |

The six runs. The report shows five. The sixth is by subtraction from the sums (estimated). "Started by" is
inferred from the times, install.log and the fix request: every row says mode `poll`.

| Run | Started (UTC) | Started by | Took | Helper time | Read again | Left to read again |
|---|---|---|---|---|---|---|
| not shown | before 16:37:00Z | unknown, probably a background job | not shown | about 121.9 s (576 - 454.1) | 31 (442 - 411) | 411 |
| 1171 | 16:37:00Z | the background poll | 2m06s | 97.7 s | 35 | 376 |
| 1172 | 16:44:10Z | the background poll | 2m24s | 95.4 s | 64 | 312 |
| 1173 | 16:50:13Z | `install.sh`, its first sync | 2m08s | 110.2 s | 65 | 247 |
| 1174 | 16:52:34Z | the agent's `sync` in step 3 | 2m14s | 106.9 s | 239 | 8 |
| 1175 | 16:54:53Z | the agent's `sync --materialise-budget 0` | 53 s | 43.9 s | 8 | 0 |

What this measures:

- **The backlog was about 442 files and took six syncs** (estimated: 411 left before run 1171, plus the 31 of
  the run not shown). All six ran within 28 minutes of the update: the tool was replaced at 16:27:57Z, the build
  before it had no OCR, and the last success is 16:55:46Z.
- **The 120 s re-read limit ended five of the six runs, not the OCR time.** The four shown took 126 to 144 s
  and left files, and so did the one not shown. The 180 s of OCR time was never used up. The table's "of
  180s" column did not name the limit that stopped the run.
- **A run under that limit read again 31 to 65 files** (31 is the run not shown, estimated). Run 1174 read
  239, because 153 of its conversions were cache hits on the same bytes.
- **The cap of 12 was not near.** The agent used 1 of the 12 extra syncs. At this Mac's rate 12 of them cover
  roughly 585 to 1,040 files (estimated).
- **"With an OCR identity" is a manifest fact.** A file read again whose text comes out the same keeps its page
  file. So 442 reads are not 442 changed pages. How many pages changed is not in this paste.
- **Unexplained:** 442 reads against 440 pages. And "failed once: 1" beside a finished scan, 0 engine failures
  and 0 files left: the count is a stale id in the source's record, and which file it is cannot be read from the
  paste.

The plan's in-flight note on main `4e65376` (`docs/plans/bring-back-ocr.md`, section B8) reads these numbers
three ways this section corrects: "carries OCR text", "five background runs" and "no budget hit".

The report now says all of this by itself: `96487e9` on the v9 branch, "fix(setup-report): the OCR part says
what read again, its hidden runs and a failed-once file are". It rewords the sum, adds a column of pages added
and changed per run, says how many runs the table leaves out, names the 120 s limit, says that `mode` is the
kind of pass and not who started it, and adds a line that says what the manifest holds of a failed-once file.

## 2. The two stops that each cost the operator a turn

**Stop 1: the folder question on a Mac that is already set up** (F19; critic U5).

- What happened: step 1's `--list-folders` listed 24 folders at 15:58:32Z. Prompt v8 then asks which to sync.
  The tool ran unattended, the question came back unanswered, and the agent stopped and waited, as step 1 says
  (logged at 15:58:45Z). The install started at 16:27:56Z, 29m11s later.
- Why it was wrong: this Mac already synced two folders. The choice was in the config, and the list did not say
  so. Stopping is right on a new Mac, where what is synced is the person's decision.
- Fix, on the v9 branch, ten commits (table below): `--list-folders` starts with an "already synced on this
  Mac:" line and marks each such folder `[synced]`; `install.sh` with no `--source-local` keeps them; prompt v9
  asks only whether to add one, and an agent that cannot ask adds none and goes on to step 2.
- Now true: a re-run on a set-up Mac needs no answer. A Mac that syncs no folder yet still stops and waits. The
  report counts no folder question on a set-up Mac and has a `folders:` line with what was kept and added.

**Stop 2: `install.sh` exited 1 on a permissions FAIL that agentsync clears by itself** (C1; F20, F21, F24;
critic U14).

- What happened: the run of 16:27:56Z ended `status rc=1 note=fail-lines`, `first-sync skipped
  note=status-failed`, exit 1 after 19 s. The FAIL named one path, the docs repo's `_eval` folder, which the
  earlier session's agent had written under its own umask. The fix on that line was a `chmod -R go-rwx` over six
  folders and a `git config`. The prompt lets the agent run only `install.sh` and `agentsync` commands, so it
  went to the report. The person approved the printed fix. The same command was run again at 16:50:04Z, 21m49s
  after the failure, and ended 0 after 141 s.
- Root cause: `install.sh` asks status before its first sync (`scripts/install.sh:1605-1608`, `:1636-1637`),
  and only a sync made agent-written paths owner-only (`cycle.py:1060`). That sync fix was not in the build
  attempt 3 installed, and status ran about 10 s after the update, before any cycle of the new build.
- It is wider than that race. On a Mac with no background sync, anything written under `_eval/` or `topics/`
  after a session's last sync is still loose at the next session's status. The loop asks for exactly that: its
  WAITING ON YOU line tells the operator to edit the baseline files (inferred from the code and the prompt).
- The six-folder `chmod` was a template, not six loose paths. One path was loose.
- Fix, on the v9 branch: `c678565` "fix(setup): add-source and init clear the permissions a sync would, before
  install.sh asks status"; `0f12c29` "fix(status): docs_repo.permissions names a sync as its fix when a sync
  clears it"; `55898c2` "fix(cycle): sync makes the cache, the logs and the agent-context folder owner-only
  too"; and four review fixes (table below).
- Now true: `install.sh` runs `init` or `add-source` before status, and both clear what a sync would. On the
  field state the run has no such FAIL, syncs and exits 0. When a sync clears every path the check found, its
  fix reads `agentsync sync`, which the agent may run. A path agentsync does not change (inside `mirror/` or
  `.git`, or another user's) still stops the run, with the `chmod` as the person's step.
- Rejected: status or doctor changing modes (status is the one read-only check); a warn in place of the FAIL;
  `doctor --fix-permissions` (new surface); `install.sh` syncing over the FAIL and asking status again; and
  F21's prompt sentence, which would not have matched, since the printed line also named folders outside
  `~/agent-context`.

## 3. What happened

| Id | Event | Result | Evidence (the redacted section, or as marked) |
|---|---|---|---|
| C1 | `install.sh` exited 1 on a `docs_repo.permissions` FAIL the first sync would clear (fix request 5) | failed | "install.sh exited 1: doctor [FAIL] docs_repo.permissions (group/other can read <docs>/_eval)" |
| C2 | The outcome and the issue link read "fully one command" for that attempt (fix request 7) | failed | "outcome: fully one command (computed: install.sh exit 0; no turn beyond the unavoidable ones)" beside "run 20261007T162756Z: exit 1 after 19s" |
| C3 | `fix-request.md` held the v7 request; the agent appended to it, then rewrote its own part (F22, F25) | worked with friction | "appended the v8 items and a new ## Not used section after it instead of replacing it (the prompt forbids deleting)" |
| C4 | Section 3 sent the first round's patch again | worked with friction | measured outside the paste: the two patch files are the same bytes, 3,587 lines |
| C5 | Two `--source-local` still print the summary block twice (fix request 4, "still") | worked with friction | fix request only: "now "2 scaffold file(s) written" then "scaffold up to date"" |
| C6 | Agent-written files in `~/agent-context/setup` are readable by group and other (fix request 6) | informational | fix request only: "`setup/fix-request.md` is -rw-r--r-- and `setup/local-work/` is drwxr-xr-x" |
| C7 | 22 files refused `no converter` in three sources, and nothing says which file types | worked with friction | "files whose row carries a reason: 2 quarantined, 22 refused"; rows of 11 (1 online-only), 10 and 1 |
| C8 | 14 queued purges, every one `no row` | informational | Purge queue part: `<source-13>`, `upstream-deleted`, `stable-id`, queued 2026-10-05, 14 purges, 14 under `no row` |
| C9 | OCR results | worked | § 1 |
| C10 | Empty cloud folders, measured | informational | "<source-1>: 5 unknown: 0 dataless, 5 materialised-and-empty (5 of them with a link count of 2: no entry by the folder's own metadata) ... the mirror still holds 0 file(s) below 0 of the checked folder(s)" |
| C11 | Both installed jobs differ from this build; poll last exit 75 | worked with friction | "ProgramArguments: 21 installed, 25 in this build"; "interpreter differs (the same file: yes; the installed one exists: yes)" |
| C12 | Redaction of section 1 | worked with friction | "Residue check: 2 capitalised word(s) next to a placeholder in this report (Agent friction log: GitHub, CLI); check them." |
| C13 | The installer-output count | informational | "6 NEXT: line(s) in 5 run(s) (exactly one per install.sh run is expected) and 11 other instruction-like line(s) (11 fix:)" |
| C14 | Loop state and the "Not used" list | informational | "loop: skill current · inbox on · baseline draft · topics 0 · checkpoint never · to curate 1346 · archive off" |
| U1 | Four name tokens in friction lines 12 and 14; placeholders the agent typed collide with the report's numbering | failed | not quoted |
| U2 | The fix request says the poll "ran pre-update code" | informational | runs 1171 and 1172 recorded OCR time, which only the new build records |
| U3 | F24 and F25 were logged after `end \| finished` and did not count | informational | friction lines 23 to 25 |
| U4 | "human turns: 1" beside a logged go-ahead | informational | "human turns: 1 (1 question; 0 clicks beyond the announced Allow click (not logged); approvals: not observable)" |
| U5 | The folder question was asked again on a set-up Mac (F19) | failed | "The folder question came back unanswered (tool in autopilot, user away); stopped before step 2 without choosing folders, as step 1 says" |
| U6 | Two files quarantined, class `credential`, in `<source-3>` | informational | Quarantine part: `<source-3>`, quarantined, `credential`, 2 files, 0 online-only, stub built 2026-10-02 |
| U7 | Status and the Quarantine part use different words for the same rows | informational | "quarantined 11" in Status; `refused`, 11 files, in the part |
| U8 | "failed once: 1" contradicts the columns beside it | informational | § 1 |
| U9 | The five rows do not add up to the sums; three were not background runs | informational | § 1 |
| U10 | Overlapping sources: 11 pairs under two outer inbox sources | informational | "the outer source's exclude list prunes the inner folder: yes", 11 times |
| U11 | Repeat conversions: clean | worked | "0 of those converted at least one file again from the bytes its page was made from" |
| U12 | The fix request's status of the v7 requests | informational | "item 1 looks fixed"; "Item 2 was not checked this session" |
| U13 | The friction log and install.log are cumulative too | informational | "25 line(s)", "4 attempt(s)"; "7 install.sh run(s)" |
| U14 | The printed fix was much wider than the fault | informational | one path named; six folders in the `chmod` |
| U15 | Two syncs in step 3; 1,346 to curate against the request's 1,344 | informational | fix request "Not used", first bullet |
| U16 | The warn `config.graph_company` is in no item | informational | "[graph] company is ignored ... (fix: delete line 22 of ~/agent-context/sources.toml)" |
| U17 | A lock was held at 16:31:56Z by an unknown holder | informational | "WARNING agentsync.cli: skipped: lock held" |
| U18 | Smaller readings of the Summary | informational | "15 of 16 source(s) listed completely (status: baseline complete)" |

## 4. Status per item

| Id | Status | Detail |
|---|---|---|
| C1 | **Fixed on the v9 branch**: `c678565` "fix(setup): add-source and init clear the permissions a sync would, before install.sh asks status", with `0f12c29` and `55898c2` | § 2, stop 2. Still a FAIL, by design: a loose path inside `mirror/`, `.git` or below another top-level folder of the docs repo, and one of another user's. |
| C2 | **Fixed on the v9 branch**: `2fedd8b` "fix(setup-report): an install run that failed before the one that ended 0 is worked with help" | `compute_outcome` judged only the attempt's last install run (`setup_report.py:1191-1195`), and the line that recorded the hand fix was a `deviation`, which never changes the outcome. Since v7 a run that failed before the attempt's first exit-0 run is a retry: the outcome is "worked with help", the Summary names the run, and the attempt's line and the issue link follow. The skeptic narrowed the first proposal, which counted every run but the last. Not counted: a run after the first success (the operator's own later `--confirm-install-agent` run exits 3 while macOS waits), a run the agent's tool stopped (no end line, or exit 129, 130, 143) and a `--list-folders` run. A click that comes only after the install command failed now counts, because install.log cannot tell it from another failure. The rule goes by install runs, so it does not depend on F24, which was logged after the closing line (U3). Rejected: a new outcome name (a new option in the issue form). |
| C3 | **Fixed on the v9 branch**: `cb210d4` "fix(setup): prompt v9 gives the fix request one part per session" | Nothing gave the file a session boundary, and the prompt forbids deleting. The file stays cumulative: each session writes under its own heading (the prompt's version and the date and time) and rewrites only its own part, with one `## Not used` section per session. Rejected (the skeptic refuted it): the installer setting an earlier request aside at `--log-start`. The Mac cannot know what was copied back, every reporting exit rewrites `bring-back.md`, and a request never copied back would be dropped. Limit: the earlier parts, real names included, come back with every file until someone clears the file by hand on that Mac. Triage compares section 2 with the last file received from that Mac and reads from the session heading above the first line that differs: a part nobody copied back can sit above the last heading. |
| C4 | **Fixed on the v9 branch**: `dd7abb2` "fix(install): the bring-back file sends local work once" | Section 3 was every patch in `setup/local-work`, written again by every report (`scripts/install.sh:997-1005`). The patch was 165,021 of the file's 229,695 bytes, and 40 of its lines match the identifying list (measured). Now a patch last written at or before an earlier attempt's report is left out, and one line gives its time, its line count and 12 hex digits of its SHA-256, so the receiver can check it against the patch they hold. Every doubt sends the patch. The skeptic's two holes are closed: the mtime is read with `/usr/bin/stat` and taken only in the exact UTC shape (a GNU `stat` on `PATH` made the first version leave out every patch), and the line claims nothing about what was carried. Limit, kept: a session that keeps work and reports, nobody copies the file, and the prompt is pasted again. The next file leaves the patch out, and the hash shows it. That costs a round and loses nothing. |
| C5 | **Fixed on the v9 branch**: `beaa7d7` "fix(install): several --source-local print the docs repo and sources lines once" | The unbuilt second half of first triage I14, not a regression: `install.sh` calls `add-source` once per folder (`scripts/install.sh:1508-1511`). The first call keeps its `docs repo` line and the last its `sources:` line; the call's exit status is still the step's. `add-source` by hand prints both, as before. The "2 scaffold file(s) written" were very probably the two root guides, the only scaffold text that changed between the two builds (inferred). |
| C6 | **Fixed on the v9 branch**: `3e77c4f` "fix(install): every run leaves the setup folder and what is in it owner-only" | Latent, not reachable: two 0700 folders stood in front of the files. The installer closed only its own three files (`scripts/install.sh:595-603`), and `--report-only`, the prompt's last command, changed no mode. Every run that is not a dry run now ends by clearing group and other access on what is in the default setup folder, and so do `--log-start` and `--log`. Doctor is unchanged on purpose: a FAIL there would stop `install.sh` on something the prompt's own steps cause. C1's heal does not cover this folder: it is a sync's, and no sync runs after the agent's last write. |
| C7 | **Fixed on the v9 branch** (the report): `2195aec` "feat(setup-report): Quarantine names the file types no converter reads" | A design gap, not a malfunction: the reason class kept the reason and dropped the suffix it carries (`setup_report.py:2522`). One line per source now counts those files by type, from a fixed list of some 260 type suffixes. Anything else is `other N (D distinct)` and is never printed. Rejected: printing the stored suffix or a pattern of it, since a name with a dot and no real extension would print a piece of the name. Which types the 22 are is still unknown from this paste. "None is an image" holds only for the ten image types OCR reads. |
| C8 | **Fixed on the v9 branch** (the report): `01f0194` "fix(setup-report): a queued purge whose id has no row is looked up as an alias". **Operator action** | § The purge queue. The report asked the items table only and could not tell a purge that already ran from a file that came back and was re-keyed. The skeptic replaced the proposed new column, which would have called an alias of a deleted file a live file: an alias is judged by the row it points at. |
| C9 | **Works.** Wording **fixed on the v9 branch**: `96487e9` "fix(setup-report): the OCR part says what read again, its hidden runs and a failed-once file are" | § 1. The cycle did what CONTRACTS §16.25 to §16.28 say. Four things read as faults and were wording. The cycle, its limits and the run record are unchanged. The skeptic refused two proposals: the table's lead sentence must not say a failed-once file "needs no re-read" (the scan's count leaves out online-only files), and the cycle must not drop a failed-once id when a scan finishes (a file that kills the cycle in its read could then never be given up). |
| C10 | **Operator decision** (O1). Not built | § Open operator decisions. The hold is by design: a zero-child folder in a cloud tree is unknown, never empty (`arm_local.py:515-525`), and a pass with an unknown folder is not complete. |
| C11 | **Fixed on the v9 branch**: `56f9d0c` "fix(launchd): a job names its interpreter as the launcher pins it". The drift itself is **expected by design** | Background sync works: the jobs ran the new build (U2). The 21 against 25 arguments are the config's: one `--canary PATH` pair per protected path, and the config gained two. New: `install-agent` wrote the running interpreter's path (`ops/launchd.py:117-122`). The updated tool runs as `bin/python3`; the launcher is built for `bin/python` and compares the path as text. So the refresh that doctor and the first triage named would have written a child the launcher refuses. This is wider than this Mac: any run that reinstalls the tool updates its environment in place (read from uv's source, not reproduced). Now a job names the launcher's pin when it is the running interpreter under another name in the same folder, a refused job gets a doctor warn, and exit 64 has a meaning in the report and the installer. Exit 75 is expected: that poll fired during the installer's own sync. Kept: plain `install.sh` refreshes no LaunchAgent. |
| C12 | **Expected by design**, plus one small defect that is **new**. Not built. **Operator action** | The four tokens are abbreviations and shortened forms of registered names, in the agent's own words: a recorded limit, since the Redactor replaces whole values it knows. The lines were written under v7 and are embedded again because the friction log is append-only. Attempt 4's own ten lines hold none. But the lines are in the report, the one file the public route takes, and the residue check flagged 0 of the 4. Its 2 hits are both false: words beside placeholders the agent typed in older rounds. New, recorded nowhere: doctor pads every check name to the longest one, and the report replaces the id inside the padded name, so the spaces give the length of each of 15 redacted ids. Also new: Recent errors keeps the log's local times beside UTC everywhere else, which gives the Mac's UTC offset. Never filed: the MDM server's host labels (1 line) and the PAC file name and port (3 lines). Working as designed: a listed folder named like the agent's product keeps its name ("1 listed ... 0 of the configured source folders"), and the four leaks first triage I2 closed stay closed. Rejected: extending the prompt's "role or placeholder" sentence to friction lines. It invites the agent to type placeholders, which is where both false hits came from. |
| C13 | **Fixed on the v9 branch**: `ebcb526` "fix(setup-report): NEXT lines and runs in install.out are counted over the same whole runs" | No run printed two `NEXT:` lines. The report reads the last 64 KiB of install.out, which began inside the second-oldest run: that run's `NEXT:` was counted and its header was not (`setup_report.py:2195-2197`). Of the 11 `fix:` lines about 8 are older installers' output still in the tail, and the v8 runs printed 3 (both estimated). The last 60 lines of the run that ended 0 hold 0 `fix:` lines, against 4 in the v7 report: the first triage's rewording worked. Not changed, by choice: the count still spans every run read (first triage I19). |
| C14 | **Expected by design**. One gap **fixed on the v9 branch**: `91af5ae` "fix(setup-report): the Loop line shows the wait the loop stopped on, not the first one" | The loop stops on a draft baseline before any curation, by design. 2 of 2 loop sessions ended there, with 0 topics and 1,346 items to curate (1,265 in the v7 report). The draft had waited about 24 hours when the report was written. Nothing in the "Not used" list is surface to cut, as in the first triage. The Summary's Loop line showed the purge wait and "(+2 more)", and hid the baseline wait its own NEXT points at (`setup_report.py:4312-4315`: the first wait, and the loop prints queued purges first). It now shows the wait the loop stopped on. This reverses first triage row 16. `hold` was read again as pausing a source (first triage N8). |
| U1 | In C12 | The critic called these leaks "not by design". Ruling: the limit is recorded and the report says to read it before sending, but nothing protected these four tokens, so C12 carries an operator action and two unbuilt edits. |
| U2 | **Not a defect.** The fix request's reading is wrong | Runs 1171 and 1172 started at 16:37:00Z and 16:44:10Z, on the poll's cadence, while the agent was idle, and recorded counters only the new build writes. Both came after the update (16:27:57Z) and before the `chmod` (16:50:04Z), and every cycle of that build makes agent-written paths owner-only at its start. The plist's "the same file: yes" agrees. So a plain re-run of step 2 would very probably have passed (inferred). The retry still counts under C2. |
| U3 | In C2 | A line after the closing line stays in its attempt and cannot stop the run. The second run wrote no second closing line. |
| U4 | **Expected by design**; the misleading half is fixed by C2 | A go-ahead logged as a `deviation` is not a turn the report can count. With `2fedd8b` the Summary no longer says "no turn beyond the unavoidable ones" for this log: it names the retried run. With the stop-1 commits the unlogged folder question is not counted on a set-up Mac. Not done, by choice: telling the agent to log a go-ahead as a `question` changes the prompt's text. |
| U5 | **Fixed on the v9 branch**: `dd81c4f` "fix(setup): prompt v9 does not ask a Mac already set up for its folders again", with nine more | § 2, stop 1. |
| U6 | **Expected by design**; one possible defect still **unverified** | A quarantine is shown in status, `_sync/STATE.md` and `_sync/QUARANTINE.tsv`, and gets no WAITING line. Both stubs were built on 2026-10-02, before the 2026-10-05 fix for a false credential match on a meeting invite's join link (`de081ae` on main). A quarantine is sticky for an unchanged file, so these may be that false match, never read since (first triage row 26). The paste cannot tell. |
| U7 | **Expected by design** | Status's `quarantined` counts refused rows and failed conversions too (first triage row 26): 11 + 2 + 10 + 1 there is the part's 2 quarantined and 22 refused. The one online-only refused file does not show in its source's "dataless 0", which counts files in that state and not refused ones (read from the manifest's schema, not traced). Wording not changed. |
| U8 | In C9 | `96487e9` rewords the lead and adds the line that says what the manifest holds of the file. |
| U9 | In C9 | § 1 has the corrected numbers. |
| U10 | **Works as designed.** It answers first triage row 36 | Two outer inbox sources hold 5 and 6 inner ones, 2 folder levels down, and the outer `exclude` prunes the inner folder in all 11 pairs. So an outer source with live 0 has nothing outside its inner sources. The source with the 14 queued purges is an inner one. |
| U11 | **Works.** It answers first triage row 28 | The 6 files of the first sync were not converted again: runs 1174 and 1175 converted 0. No loop. |
| U12 | Request 1 **fixed in the field**; request 2 **unverified** there | Request 1: the agent's name is whole in the Summary and the link. Request 2 (the guides name the kept inbox) is on main and was not checked by the session (§ Still wanted). |
| U13 | **Expected by design** | Both logs are append-only, and the report embeds the friction log whole. That is why the two v7 lines of C12 return in every report. |
| U14 | In C1 | The fix request's alternatives are ruled on in § 2. The `chmod` stays a six-folder template for a path no sync clears. |
| U15 | In C14 | Run 1174 is the plain `sync` and read 239 files again. Run 1175 is the one counted against the cap. Where the request's 1,344 came from is unknown; possibly the first of the two reports. |
| U16 | **Expected by design**. Operator action, carried over | First triage row 10: the one-time notice for an ignored config key. The line is still there. It is also the one `fix:` line each install run still prints on this Mac. |
| U17 | Informational | The holder is probably the run not shown in § 1. The second lock line is the installer's own sync and explains exit 75. No "zero children" warning follows the update because the new build logs that line at info (first triage I11, working). |
| U18 | Informational | The `launcher` step took 6 s while "skipped (not-requested)" in the run that reinstalled the tool. "done (add-source)" for folders already configured now has counts beside it (`78dbc92`). "listed completely (status: baseline complete)" reads as complete while one source is not. The session's 57m47s includes about 50 minutes of waiting for the operator (estimated). F20 is cut short in the Summary and whole in the friction table. The PAC note and the IT draft line are as in the first triage. Apart from those counts, nothing here is changed. |

### What the first triage still wanted

Six of its eight asks are answered by this report. Two are half answered.

| Ask | What the report says now |
|---|---|
| The queued purges: reason, source, time | 14 in one inbox source, `upstream-deleted`, by stable id, all queued on 2026-10-05, all `no row`. Renamed or really deleted: not yet (C8) |
| Quarantine by source and reason | 2 quarantined (`credential`, one source), 22 refused (`no converter`, three sources). Which file types: not yet (C7) |
| Is the folder named like the agent's product configured or only listed | "1 listed under ~/Library/CloudStorage ... 0 of the configured source folders" |
| Which `ProgramArguments` differ | 21 installed against 25; interpreter differs, the same file; canary paths 1 installed, 3 in this build (C11) |
| The fourth `install.sh` run | Attempt 1's `--list-folders`, exit 0 after 0 s. All 7 runs have an end line |
| A source root inside another source's | 11 pairs, each pruned by the outer source's `exclude` (U10) |
| Are the same 6 files converted every time | No (U11) |
| Does a folder that was never opened carry the dataless flag | Half: the five folders are not dataless, but they are no control (O1) |

### Commits on the v9 branch

Shas as on the branch before it is rebased onto main.

| Item | Commit | Subject |
|---|---|---|
| Stop 1 | `eb5a7a6` | feat(install): --list-folders marks the folders this Mac already syncs |
| Stop 1 | `78dbc92` | feat(install): the config step says how many folders it kept and added |
| Stop 1 | `01b511c` | feat(setup-report): no folder question on a Mac already set up, and a folders line |
| Stop 1 | `dd81c4f` | fix(setup): prompt v9 does not ask a Mac already set up for its folders again |
| Stop 1 | `e1b7c1c` | fix(install): --list-folders marks a folder inside or around a synced one |
| Stop 1 | `5abd619` | fix(install): a folder named in another case or Unicode form is the same folder |
| Stop 1 | `d4736eb` | fix(install): --list-folders prints every synced folder, the unlisted ones first |
| Stop 1 | `da8d3a5` | fix(install): --list-folders marks what it listed while a provider waits for a click |
| Stop 1 | `280a26a` | fix(install): --list-folders keeps a set-up Mac that has nothing to list |
| Stop 1 | `f5f8770` | fix(setup-report): a finished list says by itself whether the folder question was asked |
| C1 | `55898c2` | fix(cycle): sync makes the cache, the logs and the agent-context folder owner-only too |
| C1 | `0f12c29` | fix(status): docs_repo.permissions names a sync as its fix when a sync clears it |
| C1 | `c678565` | fix(setup): add-source and init clear the permissions a sync would, before install.sh asks status |
| C1 | `a276a49` | docs: CONTRACTS 16.29 and setup feedback, a re-run does not stop on a permission agentsync clears |
| C1 | `805cb21` | test: the permission tests loosen a converted page in mirror/, not the mirror's guide |
| C1, review | `9dbee32` | fix(cycle): the permissions heal opens each entry from the folder that holds it |
| C1, review | `71fc7a8` | fix(setup): a folder that is the home folder is left as it is, however it is spelled |
| C1, review | `b84c424` | fix(status): docs_repo.permissions names the chmod for a path its owner may not read |
| C1, review | `9bf21f0` | fix(cycle): a folder above where the home folder really is holds it too |
| C1, review | `c99b589` | docs: CONTRACTS 16.29 and setup feedback, three review fixes to the permissions heal |
| C2 | `2fedd8b` | fix(setup-report): an install run that failed before the one that ended 0 is worked with help |
| C13 | `ebcb526` | fix(setup-report): NEXT lines and runs in install.out are counted over the same whole runs |
| C14 | `91af5ae` | fix(setup-report): the Loop line shows the wait the loop stopped on, not the first one |
| C9 | `96487e9` | fix(setup-report): the OCR part says what read again, its hidden runs and a failed-once file are |
| C7 | `2195aec` | feat(setup-report): Quarantine names the file types no converter reads |
| C8 | `01f0194` | fix(setup-report): a queued purge whose id has no row is looked up as an alias |
| C3 | `cb210d4` | fix(setup): prompt v9 gives the fix request one part per session |
| C4 | `dd7abb2` | fix(install): the bring-back file sends local work once |
| C5 | `beaa7d7` | fix(install): several --source-local print the docs repo and sources lines once |
| C6 | `3e77c4f` | fix(install): every run leaves the setup folder and what is in it owner-only |
| C11 | `56f9d0c` | fix(launchd): a job names its interpreter as the launcher pins it |
| not from the paste | `9b437c3` | fix(skill): a temporary home writes no skill into a real CLAUDE_CONFIG_DIR |

The last one came from the build, not the field: a hand run with a temporary home folder wrote the skill into a
real config folder. The rules are in CONTRACTS §16.29 and §16.30 and in `docs/deploy/setup-feedback.md` (K20 to
K26).

### Not built on the v9 branch

| What | From | Why it matters | Why not built |
|---|---|---|---|
| After a scope change, a local or inbox source's first complete pass retires every absent file at once | C10, skeptic | Data safety. It skips the breaker and the two-pass check and queues no purge (`cycle.py:2374-2379`), where CONTRACTS promises this only for "files now outside it". Pasting the advised `exclude` line reaches it today | Found by this triage. It is a defect whatever O1 decides (§ Open operator decisions) |
| A materialised, empty cloud folder counts as empty | C10 | It would let `<source-1>` complete | The operator's decision (O1) |
| Two counts in the Empty cloud folders part: is a scope change pending, and how many mirrored files the last walk did not see | C10, skeptic | They size what a first complete pass would release. The paste cannot | Wanted only once O1 is decided |
| The purge wait names the preview; `purge --queue` does not run an entry whose id is a live file; the dry run's note is honest when nothing is targeted | C8 | The wait says "run `purge --queue`" from the queue's length alone | The wait is five texts with their pins. The guard needs care: an entry whose file came back refused or quarantined must still run, since it is the only erasure of the earlier text |
| The re-read counts what is left against the list it took before reading | C9, skeptic | Latent: an engine failure on a page from an older converter version stores the scan as finished, and the file is never tried again | Not hit here: 0 engine failures |
| A given-up file "is tried again when agentsync is upgraded" | C9 | The README says so. The code goes by a version number that has not changed since the first build, so a pull restarts nothing unless a converter, engine or suffix changes | Not hit here: 0 given up. A choice between correcting the text and adding the install commit |
| Redaction: collapse doctor's padding in the report; flag a word joined to a placeholder by `-` or `_`; write Recent errors in UTC | C12 | Id lengths, one missed name and the UTC offset | Small and optional. The operator's edit of two lines removes the names now |
| The MDM host labels and the PAC file name | C12 | Shown in 1 and 3 lines of every report | The operator's choice, never filed: keep, shorten or replace |
| Installer-output counts for this attempt only | C13; first triage I19 | About 8 of the 11 `fix:` lines are older attempts' | By choice: the tail under Installer shows the last run |
| The draft wait says where the docs repo is; the prompt's final message shows every wait; a gloss for `hold` | C14 | The operator is told what to confirm, not where | No field evidence for the first two. A path in a wait breaks the loop's no-path rule. The last two change the prompt's text |
| Shape words for `other` in the Quarantine line | C7 | They would tell an unlisted type from a dotted name | Optional |
| The report `install.sh` writes at its own exit reads one `NEXT:` short | C13, skeptic | Only for a session that stops before step 3 | Not seen in the field |

## Open operator decisions

These are recorded, not decided. The plan files them as `71d3e66ef726` and `085fac870dd6`, each with a default
that keeps what is built.

**O1 — May a cloud folder that is on the Mac and still empty ever count as empty?**

New evidence, from the report's Empty cloud folders part:

- `<source-1>` has 5 unknown folders, and 5 of 5 were checked: 0 dataless, 5 materialised and empty, all 5 with
  a link count of 2 (no entry by the folder's own metadata), 0 gone, 0 not readable, 0 excluded in the config.
- The mirror holds 0 files below them. So nothing would be tombstoned for lying under one of them.
- The source has been incomplete for 1,121 consecutive passes, about 95 hours (estimated: one pass per 304 s).
  Its deletions are held, its baseline is INCOMPLETE and it has 316 live files.

What the evidence does not show:

- **The five folders are no control.** The walk has listed them 1,121 times, and a folder that was listed is
  not dataless, whichever way it started. The first triage asked whether a folder that was never opened carries
  the dataless flag. The platform's documentation says it does. No receipt measured it on this provider.
- **The size of the release.** The report shows neither whether a scope change is pending for the source nor
  how many mirrored files its last walk did not see.

The safety hole, stated plainly (skeptic, C10):

- "Count as empty" was judged safe because a released deletion still passes two checks. The breaker holds a
  deletion of more than 63 files (computed: the default breaker on 316 live files, `config.py:165-167`). And a
  file is tombstoned only after it is absent from two complete passes.
- **Neither check runs on this source's first complete pass.** While a source has a scope change pending, the
  cycle retires every file that pass did not see as "scope change": at once, exempt from the breaker, with no
  second pass and no purge queued (`cycle.py:2374-2379`, ahead of the breaker and the two-pass check).
- The flag is set when the source's scope fingerprint changes (`cycle.py:1054-1056`) and cleared only by a
  complete full pass (`:2404-2405`). On 2026-10-05 a commit that is in both field builds (`1cc5287` on main)
  added the always-on excludes to every local and inbox fingerprint (`manifest.py:606-607`). `<source-1>` has
  had no complete pass since before that. So its flag is very probably still set (inferred from code and dates).
- So with "count as empty" built by itself, the first complete pass on the field Mac tombstones every mirrored
  file missing from that one walk. Three things then go wrong. A document caught in the middle of a save is
  retired and comes back as a new page. A bulk disappearance of more than 63 files is retired, where the
  breaker would have held it for the operator. And files really deleted upstream are labelled retired, so no
  purge is queued for them.
- The sources this option frees are the ones stuck incomplete, which are the ones most likely to carry an old
  flag.

**The same hole is in today's advice.** Pasting the `exclude` line the WAITING text prints changes the
fingerprint (`manifest.py:603`), which sets the flag on any Mac. The next complete pass then releases every held
deletion of the whole source at once, unchecked. The WAITING text says so for a folder that held mirrored files,
and not for the line it tells the operator to paste.

The companion change either option needs, about 10 to 15 lines in `cycle.py` (estimated), with no new surface:

- For a local or inbox source with a scope change pending, retire as "scope change" only what the config no
  longer covers: a path that fails the source's scope test, or a row last listed under an earlier root. That is
  the test the incomplete pass already uses (`cycle.py:1935-1949`).
- Send every other absent file through the breaker and the two-pass check, as on any complete pass. Graph
  sources keep today's branch.
- Tests: a local source whose fingerprint changed while it was incomplete and that then completes. An in-scope
  absent file is tombstoned only on the second complete pass and queues its purge. More absent files than the
  breaker's threshold are held. An out-of-scope file still retires at once.

Options:

- (a) Keep the rule (the build default). The operator pastes the printed `exclude` line. Measured cost: 1,121
  incomplete passes and a baseline that never completed. An excluded folder is never mirrored if it later gains
  files. Without the companion change the paste releases the source's held deletions unchecked.
- (b) A folder counts as empty when it is below the root, not dataless and has a link count of 2 before and
  after a listing that returned nothing, when the walk listed at least one file, and when no mirrored file lies
  below it. About 30 lines in `arm_local.py` (estimated), no new surface. All 5 field folders qualify. The
  verifier recommended it. The skeptic's check stands: it is not safe to ship without the companion change, and
  the control below comes before or with it.
- (c) As (b), only after N passes. It adds stored state per folder, and the two-pass check already delays the
  only destructive effect by one pass.

Neither (b) nor the companion change is built on the v9 branch.

**O2 — Are online-only images downloaded so they can be read by OCR?**

- Build default: no. Only images already on the Mac are read.
- New evidence: "images that are not on this Mac: 0 file(s), 0.0 MB (0 online-only, 0 of a Graph source ...)".
  All 16 images are on the Mac and all 16 have a page. Every source reads `dataless 0` and `deferred 0`.
- So the default leaves no image unread on this Mac, and a download would fetch nothing. **The question has no
  cost on this Mac today.** It can wait for a report that shows a count above 0. The report measures it every
  round, in files and megabytes, which is the number the decision needs.
- Limit: that line counts the ten image types OCR reads. One of the 22 refused files is online-only. Whether it
  is an image of a type OCR does not claim is unknown until a report from a v9 build names the refused types.

## The purge queue

The finding (C8):

- 14 purges are queued for one inbox source: reason `upstream-deleted`, by stable id, all on UTC day
  2026-10-05. For all 14 the manifest holds no row under the queued id. The source shows 59 live files, 1 stub
  and 0 tombstones.
- "No row" cannot mean "never a row". An entry is queued as its row is made a tombstone, and a tombstone keeps
  its row. A row goes away in three places only: a purge, an erasure, and a re-key, which moves the row to a
  new id and keeps the old one as an alias.
- So there are two causes, and they need opposite answers:
  1. A purge already ran and its entries stayed queued. A run of the queue erases nothing in the mirror and
     empties the queue.
  2. The files came back and were saved again, so they were re-keyed. Each queued id is then an alias of a live
     file, a purge follows the alias, and a run of the queue erases 14 live pages and their whole history.
- The report asked the items table only (`setup_report.py:3228-3233`), so the paste cannot tell them apart.
  The second is the better supported. No document records a real queue run of 14: the one of 2026-10-05 ran
  two entries. Both later sessions list purge as not used. The 2026-10-05
  feedback records re-exported files and a moved source path. And the source grew from 37 to 59 live files in a
  day (an estimate: the two reports' rows matched by position).

What the operator should do:

- **Preview first:** `agentsync purge --queue --dry-run`. On this build it writes nothing. It takes the writer
  lock, so a background poll that fires during it exits 75, and a preview started during a sync must be run
  again. Its note "N commit(s) would be rewritten" counts every commit even when nothing is targeted: ignore it.
- **Read each entry.** One with 0 items, 0 docs paths and 0 blobs is only taken off the queue by a real run. One
  that prints the path of a file that is still there must not be run.
- **Or leave them queued.** That costs one doctor warn and one WAITING ON YOU line per run. `archive = true`
  stops new queueing only.
- **Or wait for one report from a v9 build.** Its Purge queue part says how many queued ids are an earlier id
  of a file listed now, and how many the manifest holds no trace of.

What the operator should not do:

- **Do not run `agentsync purge --queue` on the WAITING ON YOU line's word.** That line is built from the
  queue's length alone and knows nothing about what the entries name.
- **Do not take a run that targets nothing as free.** It still expires every reflog in the docs repo (which
  empties any stash there), prunes unreachable objects, compacts the manifest, and deletes the manifest's
  pre-migration copies, the staging folder's contents and any remote-tracking refs.
- **Do not hand it to the setup agent.** The prompt forbids it every purge.

## Operator actions on the field Mac

Not decisions.

- Paste prompt v9 once it is on main. It is a re-run: it keeps the two folders and asks at most whether to add
  one.
- Do not run `agentsync install-agent` from a build before v9 (C11). After v9, one refresh gives the jobs the
  two new canary paths. Expect them to be reloaded with one immediate run.
- Preview the 14 queued purges before anything else is done with them (§ The purge queue).
- Do not paste the `exclude` line for `<source-1>` before knowing what it releases (O1).
- Confirm the baseline questions. The draft has waited since 2026-10-06T16:46Z, and 1,346 items to curate wait
  on it: keep about 10 in `_eval/questions.md`, correct `_eval/answers.md`, and change both to `status:
  confirmed`. Both files are in the docs repo.
- Edit lines 12 and 14 of `~/agent-context/setup/friction.md` in place, replacing the four name tokens with
  roles (C12). The prompt's "do not delete" binds the agent, not the person, and the F ids are line numbers, so
  none moves.
- Delete line 22 of `~/agent-context/sources.toml` (U16; carried over from the first triage).
- Review section 2 before sending a bring-back file. The v7 part still holds real names and comes back every
  time until the file is cleared by hand on that Mac (C3).

## Still wanted

Only what no report can say.

- For O1, one measurement, on any Mac with the same cloud provider: make a folder with one file on the web, in
  a place that Mac never listed, and read the folder with one `lstat` before anything lists it. Is it dataless?
- From the field Mac: the sentence in `~/agent-context/docs/AGENTS.md` that says where mail drops go. Does it
  name `~/agent-context/inbox` (first triage I13)? The session did not check, and the report does not print the
  guide.

Everything else the first triage asked for by hand is in this report, or will be in the first report from a v9
build: the refused file types (C7), whether the queued purge ids are aliases (C8), what the failed-once file is
and how many pages each run changed (C9).
