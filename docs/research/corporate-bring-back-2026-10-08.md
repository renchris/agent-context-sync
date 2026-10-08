# Corporate Mac bring-back file, third round, triaged (2026-10-08)

Scope (frozen): triage the third bring-back file from the corporate Mac (prompt v9, main `c7bfdb8`) against main;
for each item record result, evidence and status: already fixed (commit), expected by design, new with the
smallest fix, or the operator's decision; record what moved for the two open operator decisions and the 14 queued
purges.

Method: Workflow run `wf_a17b719b-547`, 25 agents (12 verifiers, 12 skeptics, 1 critic): one verifier and one
skeptic per item (D1 to D12) and one completeness critic, whose findings are rows U1 to U11. The agents were
read-only on main `c7bfdb8` and ran no test suite; a measurement of theirs was a throwaway script under a
temporary home folder. Where a skeptic corrected a verdict, the corrected status, root cause and fix are the ones
recorded here. The fixes were built afterwards on branches cut from `c7bfdb8`; the plan
(`docs/plans/bring-back-ocr.md`, section B10) records their shas once they are on main.

Names: the paste's sections 2 and 3 are unredacted, and section 1 still shows four name tokens in two old
friction lines and one product label in the MDM line (D3). This doc uses only the report's own placeholders
(`<source-1>`, `<folder-1>`, `<org-1>`). Evidence lines are quoted from the redacted section, never from those
three lines. The paste, the patch and the full findings stay outside the repository. The earlier triages are
[corporate-bring-back-2026-10-06.md](corporate-bring-back-2026-10-06.md) and
[corporate-bring-back-2026-10-07.md](corporate-bring-back-2026-10-07.md).

## Verdict

- **Prompt v9 ran as one command.** Attempt 5 of 5 finished with no stop in 1m24s, with 0 human turns, 0
  deviations and 0 errors. It really was v9 at `c7bfdb876642`, which is origin/main. The folder question was not
  asked again, which was the first stop of the second round, and `install.sh` exited 0 after 20 s.
- **The loop did not move.** Attempts 3, 4 and 5 all end on the same wait: the baseline questions are still a
  draft. "Fully one command" measures what the session cost a person, not progress. 1,455 items now wait to be
  curated, against 1,346 a round ago (U1).
- **Every fix of the second round that this run could exercise works in the field** (§ Status per item, and the
  table of main shas under it). Two were not exercised: several `--source-local` in one run, and the permissions
  heal, for which nothing was loose.
- **One new prompt finding, and it did no harm** (F29, D2, D12.a). Step 2 says the inbox is the folder of each
  inbox-kind source. This Mac has 14, and one of them is the drop folder. The agent announced only the right
  one. The sentence is corrected in prompt v10, with two held riders (D12.b, D12.c).
- **Prompt v10 needs no new run on the corporate Mac.** v9 ran clean and no v10 change alters what it did
  there. The next time the prompt is used it must be copied again from the README: a saved v9 copy stops at step
  1.
- **The hazard the second triage found in today's `exclude` advice is measured, and its guard is built this
  round** (D7.b, D7.c). On a throwaway home, pasting the line the tool itself prints retired an in-scope file of
  the same source in one pass, with no purge queued. Until the field Mac has the guard, the line must not be
  pasted.
- **The 14 queued purges no longer point at live pages by an alias** (D6.a). The report now looks each id up as
  an alias, and all 14 have no row and are no alias. A run of the queue is expected to erase nothing and empty
  the queue, on one condition that only the preview can show (§ The purge queue). It is still not free.
- **Online-only images still cost nothing on this Mac** (D8). 0 images are not on this Mac in two reports
  running, while the image count rose from 16 to 27. The one online-only refused file is a `.potx`, not an image.
- **The paste holds no background run of the new build** (D5.b, D8). Runs 1263 and 1264 predate the update by
  about a minute, and runs 1265 to 1267 are the session's own. Recordings are read only by the background jobs,
  so the first thing to do on the field Mac is to look, before anything is changed there.
- **Section 1 still shows the four name tokens** in friction lines 12 and 14, byte for byte as in the last file.
  The operator's edit has not been made. The residue check flagged 0 of them and its 3 hits are all false.
- **Counts.** Of the 107 rows below (96 sub-items of D1 to D12, and U1 to U11): 14 are already fixed on main,
  21 are expected by design, 35 are readings that need no code, 17 are built this round, 6 are not built, 6 are
  the operator's decision, 6 are the operator's action and 2 are refuted. The prompt's text changed, so it is
  v10.

## Inputs and version

| Fact | Value | Source |
|---|---|---|
| Report | `agentsync setup-report`, generated 2026-10-08T04:36:52Z in 1.4 s of its 12 s; macOS 15.7.3, arm64, MDM-enrolled | Run metadata, Environment |
| Setup prompt | v9, attempt 5 of 5. Earlier: attempt 1 v6 failed at step 2, attempt 2 with no header failed at step 1, attempt 3 v7 fully one command, attempt 4 v8 worked with help | Summary, friction log |
| install.sh commit | `c7bfdb876642`, the checkout's HEAD and origin/main (the skeptic checked the live remote too) | Run metadata, install.log |
| Agent | GitHub Copilot CLI, the name whole in the Summary and the issue link | Summary |
| Computed outcome / run type | fully one command / Real Mac. True for this attempt | Summary |
| Attempt 5 | started 04:35:27Z, ended 04:36:51Z. Two `install.sh` runs: `--list-folders` exit 0 after 0 s (listed 24, synced 2); install exit 0 after 20 s (started 04:35:38Z). Then three syncs: runs 1265 (the installer's), 1266 and 1267 (step 3's) | Installer, Status |
| Config | 16 sources, all live: 2 local under `~/Library/CloudStorage`, 14 inbox-kind. Kept the 2 folders already synced, added none | Configuration, Summary |
| Doctor | 97 checks: 89 ok, 1 info, 7 warn, 0 FAIL. The new check is `media`. The same 2 expected and 5 unexpected warns as a round ago | Doctor |
| Background | both jobs loaded. Poll: 695 runs, last exit 0. Reconcile: 60 runs, last exit 0 | Background runs |
| Between the two reports | 11 h 40 min (42,032 s). Run ids 1175 to 1267: +92 = 82 poll launches + 7 reconcile launches + the session's 3 syncs (measured by subtraction, U2) | Status, Background runs |
| Fix request | the v7 and v8 parts byte for byte as in the last file (120 lines), 38 lines appended for v9: a status of the earlier items, one new item (8) and a "Not used" section | section 2 |
| Local work | one line: the patch is not repeated. Its line count (3,587) and hash match the patch the intake holds | section 3 |

Six corrections to how the paste reads:

- "poll last exit 0" is a run of the previous build. The new build was installed at about 04:35:40Z, and the last
  background run started at 04:34:33Z (D5.b).
- Recent errors is stale, not noisy: 39 of its 40 lines predate the update of 2026-10-07 or were in the last
  file's window. One line is new (D10).
- "4 fix:" is counted over 5 runs. This run printed one, inside a warn (U9).
- F29 says 15 inbox-kind sources, 14 of them bridges. The report's own Configuration line says 14, so 13 (D1.f).
- The agent calls the incomplete source `<folder-2>`. The report calls it `<source-1>`. They are one source, as
  a round ago.
- The fix request's session heading is in local time, a calendar day behind the report's UTC (D11.n).

## 1. What the field measured about OCR, against the 2026-10-07 table

Only what moved, or what a reader would expect to have moved. Every other row of the second triage's table reads
the same. The numbers are the report's own unless marked.

| Measure | 2026-10-07 | 2026-10-08 | Delta |
|---|---|---|---|
| Images | 16 files, 16 with a page | 27 files: 25 with a page, 2 with a no-text stub | +11 files, +9 pages, +2 stubs |
| Images that are not on this Mac | 0 files, 0.0 MB | 0 files, 0.0 MB | none |
| PDF files with a page | 99 with an OCR identity, 0 without | 100, 0 | +1 |
| Deck files with a page | 185, 0 | 188, 0 | +3 |
| Word files with a page | 140, 0 | 140, 0 | none |
| Pages with an engine identity | 440, one identity | 453, one identity (100 + 188 + 140 + 0 + 25) | +13 |
| One-time re-read | finished in 16 of 16; 0 left; 0 given up; 1 failed once | finished in 16 of 16; 0 left; 0 given up; 0 failed once | failed once 1 to 0 |
| Runs that had an engine, of the last 200 | 6 | 98 | +92 |
| Helper time | 576 s | 949 s | +373 s |
| Runs out of OCR time / helper not working | 0 / 0 | 0 / 0 | none |
| Reads again | 442 | 442 | none |
| Conversions that left pictures past the picture limit unread | 3 | 3 | none |
| Engine failures; conversions without OCR; past the page limit | 0; 0; 0 | 0; 0; 0 | none |
| The installer's first sync | 128 s, converted 6 | 2 s, converted 0, deferred 0 online-only | -126 s |
| Converter cache reuses | 373 | 383 | +10 |
| Repeat conversions | 0 of 6 runs | 0 of 98 runs | none |
| Quarantine totals | 2 quarantined, 22 refused | 4 quarantined, 23 refused | +2 no-text images, +1 `.zip` |

What this measures:

- **OCR keeps working, and the figures add up.** 1 + 3 + 0 + 9 = 13 = 453 - 440. No OCR line contradicts another
  (D8.b).
- **The backlog is gone and the steady state is cheap.** The +373 s fell on a few runs with a changed file: the
  five runs shown used 12.0 s once and 0.0 s four times. At least 15 files were newly converted in those 92
  runs, so at most about 25 s a file (estimated by division).
- **The first engine run was run 1170.** 1267 - 98 + 1 = 1170, and every run from 1176 to 1267 had an engine. It
  is the run the second triage could only estimate as "not shown".
- **The new build has not yet read one file with the engine in the field.** Runs 1176 to 1264, with all of the
  +373 s, are the previous build's. The new build's runs are 1265 to 1267, each with 0.0 s of helper time and 0
  files converted. The helper probe says ready.
- **"Failed once: 1" from the last round cannot be answered, and has no effect.** The line that would name the
  file prints only for the current record, and that count is 0 now. Probable cause (inferred): the list of
  converted suffixes grew with the recording types, which starts a new record. The paste cannot tell that from
  the id having been dropped. 0 engine failures, 0 given up, and every page has an identity.
- **Three numbers will leave the report, and one of them is not kept anywhere else.** The sums are over the last
  200 runs. Runs 1170 to 1175 leave that window when the newest run reaches 1375, about 13.1 to 13.7 hours after
  this report (estimated from 92 runs in 11.68 h), longer by any night the Mac sleeps. Then 576 of the 949 s go,
  "reads again" falls from 442 to 0 and the picture limit from 3 to 0. That will be the window moving, not a
  loss. But the picture-limit count is the report's only count of those conversions, and their pages keep the
  note and are not read again. So it is recorded here: three conversions, in runs 1173 and 1174, left pictures
  past the picture limit unread.
- **Recordings: nothing in the report, and nothing to read on this Mac** (U3). `c7bfdb8` is the first field build
  that reads meeting recordings. The paste says one word about it: `media` is among the ok checks, which is ok
  for a ready helper and for one that is off. All 23 refused files are typed and none is a recording. So the
  plan's field check of the media helper cannot be answered from this file. The report gains a media-helper line
  this round.

## 2. What happened in the run

One session, 84 s, no person in it.

- 04:35:27Z: step 1 lists 24 folders and finds 2 already synced. The operator is away, so none is added and
  nothing is logged, as prompt v9 says. The install starts 11 s later.
- 04:35:38Z: `install.sh` reinstalls the tool (2 s: the commit changed, 103 commits after the last one) and
  builds the new media helper inside the `launcher` step (7 s; which helper compiled is inferred). Status takes 4
  s with 0 FAIL, the first sync 2 s. Exit 0 after 20 s. The step seconds, the report step included, add up to 17
  of those 20: the closing `agentsync status` that fetches the loop's NEXT took 2 s and is timed by no step.
- Step 3: one plain sync and one that downloads nothing, 1 of the 12 allowed. The agent writes F29, its part of
  the fix request and the report.
- The last NEXT is the one of the last two rounds: "NEXT: stop: the operator confirms the baseline questions
  (WAITING ON YOU above); session done".

The other 64 s of the session are the agent's, not the installer's.

## 3. Status per item

Result is one of worked, worked with friction, failed, informational. A quoted line is from the redacted
section 1. "Measured" and "inferred" are the notes' own words.

| Id | Result | Evidence | Status |
|---|---|---|---|
| D1.a | worked | "install source: checkout ~/src/agent-context-sync @ c7bfdb876642 · ... · on origin/main: yes" | **Reading only.** Identity true: prompt v9, `c7bfdb876642`, on origin/main, also against the live remote. Three commits changed the block's text under the name v9 after the bump, all before the field run; from here a text change is v10. |
| D1.b | worked | "Runs with no end line (stopped early, or still running): 0" | **Reading only.** Finished, no stop. Both runs of the attempt end `rc=0`. |
| D1.c | worked | "outcome: fully one command (computed: install.sh exit 0; no turn beyond the unavoidable ones)" | **Reading only.** True by its definition. The label does not say that the loop did not move (U1). |
| D1.d | worked | "attempt: 5 of 5 (earlier: attempt 1 failed at step 2, ..., attempt 4 worked with help)" | **Already fixed**: `82ff572` "fix(setup-report): an install run that failed before the one that ended 0 is worked with help". Attempt 4 read "fully one command" a round ago. Attempt 1's "failed at step 2" beside an exit-0 run is by design: CONTRACTS keeps the v5 and v6 rules, and it will repeat in every report from this Mac. |
| D1.e | worked | "human turns: 0 (0 questions; 0 clicks beyond the announced Allow click (not logged); approvals: not observable)" | **Reading only.** True. The Summary cannot tell "asked and said no" from "could not be asked"; by the prompt's design. |
| D1.f | informational | "agent friction: 0 deviation, 1 prompt, 0 error (F29)" | **Reading only.** Counts exact. F29's own number is one too high: the report says "by kind: inbox 14, local 2". |
| D1.g | worked | "time: session 1m24s (first to last timestamp)" | **Reading only.** 84 s. The clock starts at `--log-start`, after `git pull`. The Loop line shows the wait NEXT points at: `2b76245` working. |
| D1.h | informational | "15 of 16 source(s) listed completely (status: baseline complete)" | **Not built.** Count true, wording misleading for the third time. CONTRACTS pins the words. If wanted, the skeptic's reword is "N of M source(s) with a complete baseline (status)": dropping the parenthesis, as first proposed, would lose which of status's two fields is counted. |
| D1.i | worked | "5 NEXT: line(s) in 5 run(s) (exactly one per install.sh run is expected)" | **Already fixed**: `ecfc5e0` "fix(setup-report): NEXT lines and runs in install.out are counted over the same whole runs". It read 6 in 5 a round ago. Still every run read, by choice. |
| D1.j | informational | "unexpected 5: config.graph_company warn, launchd.poll warn, launchd.reconcile warn, heartbeat.<source-1> warn, governance.purge_queue warn" | **Not built.** By contract: every warn that is not one of two named states is "unexpected". All five are known operator items. The proposed third reason keyed on doctor's "yours" notes, which exist only under `install.sh`: the same Mac would read differently by hand, and on a new Mac whose folder cannot be listed it would file the one useful line under "expected". If a relabel is ever wanted, the launchd pair only. Default: leave it. |
| D1.k | informational | "launcher 7s"; "220 replacement(s) of 29 value(s)" | **Reading only.** The 7 s is helper build time under the launcher's name (D4.a). 220 is the redactor's substitution count, smaller than what a reader sees: 206 `<source-N>` are visible against the legend's 150. |
| D2.a | worked with friction | F29: "Step 2 says the inbox is the folder of each kind = "inbox" source ... so only the one with id = "inbox" (~/agent-context/inbox) was announced as the drop folder" | **Built this round** (prompt v10, with the README and deploy README wording and doctor's line). The older meaning lived in five places: the prompt sentence, the two README sentences, doctor's ok line, which told every empty inbox-kind folder "(drop files you save by hand here)", and status's `inbox on`. Root cause: the first field lesson offered two fixes and both shipped; the later decision that one kept folder is the inbox was applied to the guides only. Status's `inbox on` still counts every inbox-kind source. |
| D2.b | informational | "[ok  ] source.inbox.listable — ~/agent-context/inbox is listable" | **Reading only.** `install.sh` shows the path on every run that reaches status, in doctor's per-source line. The dedicated "added inbox source" line prints only on the run that adds it. |
| D2.c | worked | fix request only: item 2 is reported fixed | **Already fixed**: `41469c9` "fix(publish): the guides name the kept inbox, not the first inbox source by id", for the guide line only. The kept-folder expression is written in four places. |
| D2.d | informational | measured on a throwaway home | **Reading only.** The agent's wording, "the source with id = inbox", is wrong too: a synced folder named Inbox takes the id `inbox` and the kept inbox becomes `inbox-2`. The inbox is kept by path. |
| D2.e | worked with friction | as D2.a | **Built this round**: prompt v10. An output or doc change cannot retract a prompt sentence. The block's digest is pinned, so any reword is a new version; the version stands in the block four times. |
| D2.f | informational | "none of these changes the outcome by itself" | **Operator decision**, taken: v10 this round, with the riders. |
| D2.g | informational | code and contract | **Expected by design.** A Mac with no source of id `inbox`: the guide already decides all four cases (kept folder under another id, one inbox elsewhere, several, none). |
| D2.h | informational | "sources: 16 (by kind: inbox 14, local 2; by state: live 16)" | **Reading only.** 14 inbox-kind sources, 13 of them not the kept one. The same miscount the first triage recorded. |
| D3.a | failed | not quoted | **Expected by design**, and the operator's action is still open. CONTRACTS: a name agentsync has never seen in the agent's own words, such as an abbreviation of a source id, is not covered. Both lines are byte for byte the last file's. Attempt 5's own lines hold no name. |
| D3.b | failed | "Residue check: 3 capitalised word(s) next to a placeholder" | **Built this round.** The check missed all four tokens. It can catch one: a word of any case joined to a placeholder by `-` or `_`. Measured on this paste: +1 true hit, 0 false; on the v7 report 28 hits, each a piece of a half-redacted id. The skeptic's guard for `<org-N>-my` is part of it. |
| D3.c | worked with friction | "(Agent friction log: GitHub, CLI; Installer: Development)" | **Built this round.** 3 hits, 3 false. "Development" is new only because the 60-line tail moved over three home-level sources. A generic home folder before a placeholder is no longer a hit, nested ones included. GitHub and CLI stay until the operator edits friction lines 5 and 13. |
| D3.d | informational | not quoted | **Not built**: the operator's choice, recorded in C12. One line. Nothing reads the MDM host. The operator's own names list holds the product label, so a report sent as generated fails the operator's own check. If wanted: register the host under the existing `<host>` kind. |
| D3.e | informational | not quoted | **Not built**: the operator's choice, recorded in C12. The PAC file name and port, 3 lines. If wanted: register the URL's host and path as a proxy value, two lines and a test. |
| D3.f | failed | the doctor lines of the install.out tail | **Built this round** (the padding). Doctor pads each name to the longest real one and the report replaced the id afterwards, so the tail gave the length of 15 ids, as C12 said. The report now collapses the padding. The other half is **not built**: Recent errors keeps the log's local times, and there is no one-line fix that does not mix old and new lines in one log. |
| D4.a | informational | "step=launcher seconds=7 rc=0 result=skipped note=not-requested" | **Expected by design.** The helpers are built inside the `launcher` step by contract, with no step of their own. One new helper source is one compile, once: 6 s a round ago for the OCR helper, 7 s now, 0 s on the run between. |
| D4.b | informational | "start install.sh compat=9 commit=- kind=- source=- args=--list-folders" | **Expected by design.** A list run has no source. Latent, **not built** (optional): the report's "last install.sh run: commit" takes the last start line of any kind, so a session that ends on a list run would read "commit -". One condition and one test. |
| D4.c | worked | "step=agentsync seconds=2 rc=0 result=done" | **Expected by design.** The commit changed, so the tool was reinstalled. |
| D4.d | informational | "step 1 --list-folders 0s · step 2 install 20s" beside "session 1m24s" | **Expected by design.** Two clocks. Not looked at before: the step seconds add up to 17 of the run's 20, and the Summary lists 16 of them. |
| D4.e | worked | "[info] skill — stale: the next sync writes it" | **Expected by design.** Doctor runs before the first sync of a new build; the sync 4 s later wrote it, and the report reads "skill current". |
| D4.f | informational | "1210 consecutive passes" in the tail, "1213" in the Doctor part | **Reading only.** Three passes in between: runs 1265 to 1267. Each session adds at least 3. |
| D4.g | worked | "background sync: already installed by an earlier run (com.agentsync.poll); this run left it as it is" | **Expected by design**, word for word in CONTRACTS. Not acted on in three sessions. |
| D5.a | informational | "ProgramArguments: 21 installed, 25 in this build"; "canary paths: 1 installed, 3 in this build, 1 in both" | **Reading only.** The whole drift is two `--canary PATH` pairs. The third path is inferred to be `<source-1>`'s sentinel. |
| D5.b | informational | "com.agentsync.poll: plist present · loaded: state = not running · runs = 695 · last exit code = 0 (ok)" | **Reading only.** The installed plist works with the new build by construction, not by measurement: the paste holds no background run of `c7bfdb8`. Both protected sources sit in one provider folder, which the installed job already probes, so the refresh adds no detection on this Mac. |
| D5.c | worked | "by class: launcher same · interpreter same" | **Already fixed**: `e919fee` "fix(launchd): a job names its interpreter as the launcher pins it". A round ago this read "interpreter differs". `install-agent` from this build cannot give exit 64 on this Mac. |
| D5.d | informational | "(fix: delete line 22 of ~/agent-context/sources.toml)" | **Operator action**, carried over twice. Correct, and operator-only: the fix is a hand edit of the config. |
| D5.e | informational | as D1.j | **Expected by design.** None of the five becomes "expected". The detail of the launchd warn is the same sentence for harmless drift (this round) and for the hazardous one (C11): an "expected" class on it would have hidden C11. The optional "yours N" group is dropped. |
| D5.f | informational | the five warns | **Operator action**: the corrected list is § Operator actions. |
| D5.g | informational | measured: `git merge-base --is-ancestor` | **Reading only.** The second triage's commit table is pre-rebase: none of its shas is on main. The main shas are in the table under this one. |
| D6.a | worked | "no trace: 14 queued id(s) have no row and are no alias. A re-key leaves an alias, so what took such a row away is a purge that already ran, or an erasure." | **Already fixed**: `fba11fa` "fix(setup-report): a queued purge whose id has no row is looked up as an alias". The alias cause is excluded for these 14. |
| D6.b | informational | measured on a fixture | **Reading only**: § The purge queue. A run that targets nothing rewrites no commit and leaves HEAD as it is. The verifier's "it cannot touch another id" is wrong once a blob is targeted. |
| D6.c | informational | the same line | **Reading only.** The sentence is right for every row removal the code performs: four sites, each leaves an alias or deletes it with the row. Not covered: a manifest replaced or rebuilt outside the code. Unlikely here: the run ids reach 1267. |
| D6.d | worked with friction | measured: "note: 7 commit(s) would be rewritten" on an entry that targets nothing | **Built this round.** The dry run's note counted every commit in the repo whether or not anything was targeted. It now says so when nothing is targeted, and "up to N" otherwise. The skeptic tightened the condition: nothing targeted means no blob, no docs path, no id and no path. |
| D6.e | informational | "queued purges: 14 (run `~/.local/bin/agentsync purge --queue`)" | **Operator action**: § The purge queue, with the skeptic's five corrections. |
| D6.f | informational | measured: a second entry that raises leaves the first off the manifest and still queued | **Built this round.** The queue file was written once, after the last entry. It is now written after each entry. It is the least likely cause of this picture: a run that stopped part-way would leave the later entries with their rows. |
| D6.g | informational | proposed: the "no trace" line names the preview | **Refuted.** CONTRACTS: the report's purge lines state facts and name no command, because the prompt forbids the setup agent every purge and the report is the last thing it reads. The operator's step is already in `docs/deploy/setup-feedback.md`. |
| D6.h | informational | "WAITING ON YOU: 14 queued purge(s): run `~/.local/bin/agentsync purge --queue`" | **Expected by design**: recorded in CONTRACTS as deliberately not built. The wait is five texts built from the queue's length. Right here, with all 14 "no trace"; still wrong for a queue with a "still listed" entry. |
| D7.a | informational | "<source-1>: 5 unknown: 0 dataless, 5 materialised-and-empty (5 of them with a link count of 2: no entry by the folder's own metadata) ... 5 of 5 checked" | **Reading only.** The same five counts in two reports 11 h 40 min and 92 passes apart. The source is otherwise alive: live 316 to 318. The 37 "zero children" lines in Recent errors are old log, from before the update. |
| D7.b | failed | measured on a throwaway home; "baseline INCOMPLETE · complete no · live 318 (dataless 0) · quarantined 11" | **Built this round**: the scope-change retire guard. On `c7bfdb8` the first complete pass of a source with a scope change pending retires every file that pass did not list, ahead of the breaker and the two-pass check, with no purge queued. CONTRACTS promises that only for "files now outside it", so it is a defect, not a decision. The flag is very probably set on `<source-1>` (inferred). What that pass would release: 0 to 329 rows (inferred from live 318 + quarantined 11); the breaker trips at 64. |
| D7.c | failed | "if they are meant to be empty, set exclude = [<path>] in [[source]] id = '<source-1>' in sources.toml" | **Built this round**: the same guard. Measured: the advised line, pasted as printed, retired an in-scope file of the same source that was not under the excluded folder, in one pass, with the alarm calling it "now outside it". The control, with the empty folder removed instead, took two passes and queued a purge. The agent's own fix request also points the operator at the paste. |
| D7.d | informational | design only | **Operator decision** `71d3e66ef726`. Not built: § Open operator decisions has the skeptic's smaller design. |
| D7.e | informational | the decision packet | **Operator decision**: § Open operator decisions. |
| D8.a | worked | § 1 | **Reading only.** The delta table. Two line slips in the notes, no effect. |
| D8.b | worked | "engine identities on pages: ocr-apple-vision-r3-h2.0.0-l1 (453 page(s))" | **Reading only.** All sums check. Run ids are contiguous: +92 runs and +92 incomplete passes. |
| D8.c | worked | "images that are not on this Mac: 0 file(s), 0.0 MB (0 online-only, 0 of a Graph source; ..." | **Reading only.** The question is still without cost here. "Every source reads dataless 0" is no evidence about images; the evidence is this line and the by-type lines. |
| D8.d | informational | the OCR part | **Expected by design.** No sentence is wrong. The wording is on main as `61b2de1` "fix(setup-report): the OCR part says what read again, its hidden runs and a failed-once file are". "Failed once" 1 to 0: § 1. The optional collapse of the all-zero table is dropped: its columns are contract text. |
| D8.e | informational | "of the last 200 run(s), 98 recorded these counts" | **Reading only.** The window moves: § 1. |
| D8.f | informational | the decision packet | **Operator decision** `085fac870dd6`: § Open operator decisions. |
| D9.a | informational | ".msg 1" in each of two sources | **Expected by design.** CONTRACTS: `.msg` is not routed and becomes a refused stub until a permissive parser is chosen. A converter is a new dependency or a hand-written reader. |
| D9.b | informational | ".potx 1 (1 online-only)" | **Expected by design.** Not one line: measured, the deck library raises on a template, so routing it would turn a quiet refused stub into a failed conversion. |
| D9.c | informational | ".url 2" in each of two sources | **Not built**: small value, the operator's choice. The only cheap one: the text converter already reads the bytes. The skeptic's list for it is longer than first given: the suffix, the report's path-scrub pattern, two pinned tests and a contract amendment. 2 distinct link files, and the page title would be the literal section header. |
| D9.d | informational | ".drawio 3 · ... · .vsdx 2" | **Expected by design.** `.drawio` may hold its diagram as a compressed blob; `.vsdx` needs a real parser. |
| D9.e | informational | ".loop 1"; ".zip 1" | **Expected by design.** A cloud placeholder and an archive. The `.zip` is new since the last file. |
| D9.f | informational | "other 2 (1 distinct)" | **Reading only.** Two files with one unlisted tail, in each of two sources. Printing the tail was rejected at C7. § Still wanted. |
| D9.g | informational | the same no-converter set in `<source-1>` and `<source-7>`, bar the online-only `.potx` | **Expected by design**, and a design gap. Duplicate refusal exists only for an inbox drop against a live Graph row, and this Mac has no Graph source. The design lists "the same document mirrored twice" as a failure mode. No fix: widening it would swap about 250 pages for duplicate stubs on an inference. "Most of that library twice" is unproven. |
| D9.h | informational | "<source-3> · quarantined · credential · 2 · 0 · 2026-10-02" | **Expected by design.** The credential pair is the second triage's U6, and its "one possible defect still unverified" is still open: three rounds on a build with the join-link fix (`de081ae`) have not re-read the two files, and no re-read ever targets a credential stub. The two no-text images are settled stubs. § Still wanted. |
| D9.i | worked with friction | "<source-1> · refused · no converter · 11 · 1 · 2026-10-02 to 2026-10-08" | **Built this round.** An online-only file that no converter claims has no content hash, so it is pending work in every pass, and its stub was published again every pass. Measured: its build run was 1, 2, 3, ... 7 over 7 passes while the on-disk stubs stayed at 1. No byte changes and no commit; the visible effect is that this column always ends "today". A stub that is already there, intact, is no longer published again. Rejected: dropping such rows from the pending work, which is how the file is fetched once its type gains a converter. |
| D9.j | worked | "files whose row carries a reason: 4 quarantined, 23 refused" | **Already fixed**: `f2f4552` "feat(setup-report): Quarantine names the file types no converter reads". The second triage's open sentence, which types the 22 are, is answered. |
| D10.a | informational | "<source-1>: <path>: <path>: unstable: changed during copy" | **Expected by design.** Two different fields, the relative and the absolute path of one file; the scrub replaces a whole segment that names a path, pinned by a test. The words "fetch of ... failed" are lost. |
| D10.b | worked | "(size 1829477->1821952, read 1821952 bytes, mtime_ns ...)" | **Reading only.** Handled correctly: no half file, the item is retried, the run still exits 0. The file had been changed upstream 187 s before the read (measured from the line's own stamp). A later pass read it (inferred from the absence of a second line). |
| D10.c | worked | 37 "zero children" lines, the last at 11:26:52 local | **Already fixed**: `ddaa8b1` "fix(doctor): empty cloud folders are named, with the exclude line to paste". The last such line is 64 s before the install that brought the fix. None after. |
| D10.d | worked | "WARNING agentsync.cli: skipped: lock held", twice | **Expected by design.** Exit 75. Both lines are from the day before. The holder of the first is on the Mac, one line above the warning in the same log; the report drops that line because it has no level word. § Still wanted. |
| D10.e | worked with friction | "The last 40 of 466 WARNING/ERROR line(s)" | **Built this round**: Recent errors merges repeats. Not new: the unbuilt half of first triage I11. Prototyped on this paste: 40 lines become 3 entries (37, 2, 1). One premise was wrong: the stale lines do not stay "for a long time". The count fell from 521 to 466 in 11.7 h, because the launcher writes a line per run that pushes old lines out of the 64 KiB tail; the poll log sheds them in at most about 4 days (estimated). |
| D11.a | informational | section 2: 120 unchanged lines, 38 appended | **Expected by design.** The fix request is cumulative by contract; a cut on the Mac is the rejected design. The agent obeyed: 0 deviations, against two a round ago. |
| D11.b | informational | the triage rule | **Reading only.** By its letter the rule starts one session too early for a session that only appends. Harmless: it over-reads an unchanged part. The proposed clause is unsafe as worded and is dropped: it would skip the tail of a part its own session rewrote. |
| D11.c | informational | not quoted | **Operator action.** The v7 part holds real names and returns with every file. § Operator actions. |
| D11.d | worked | section 3: one line | **Already fixed**: `a9a5dbc` "fix(install): the bring-back file sends local work once". Its first field run. Section 3 is 1 line against 3,587. |
| D11.e | worked | "agent: GitHub Copilot CLI claude-opus-5.5" | **Already fixed**: `ea77262` "fix(setup-report): a folder named like a coding agent no longer redacts the agent name". |
| D11.f | worked | fix request only | **Already fixed**: `41469c9` (D2.c). The agent's sentence is the field evidence; section 1 does not show the guide. It answers the second triage's last "Still wanted". |
| D11.g | informational | "args=" (empty) | **Already fixed**: `fb128f0` "fix(install): several --source-local print the docs repo and sources lines once". Not exercised: no `--source-local` this run. |
| D11.h | worked | "0 FAIL"; `docs_repo.permissions` among the ok checks | **Already fixed**: `41f2484` "fix(setup): add-source and init clear the permissions a sync would, before install.sh asks status", with `015bcdc`. Not shown rather than exercised: nothing was loose, and the config step's output is above the 60-line tail. |
| D11.i | informational | fix request only: "holds for now" | **Already fixed**: `949cde9` "fix(install): every run leaves the setup folder and what is in it owner-only". The agent understated it. No report line shows the folder's modes. |
| D11.j | worked | "attempt 4 worked with help" | **Already fixed**: `82ff572` (D1.d). |
| D11.k | informational | "enumeration incomplete for 1213 consecutive passes (deletions held)" | **Operator decision** `71d3e66ef726` (D7). |
| D11.l | worked | "to curate 1455"; "kept=2 added=0" | **Reading only.** The "Not used" list is accurate against the report. The prompt's nine commands are exactly the nine visible ones. |
| D11.m | informational | fix request only | **Reading only.** `hold` read as pausing a source for the third time. A second cause is likely (inferred): the cumulative file puts the earlier "Not used" section in front of the agent, and two of its lines are byte for byte the last session's. The gloss is D12.c. |
| D11.n | informational | "generated at: 2026-10-08T04:36:52Z" | **Reading only.** The session heading's time is local, a day behind. It still marks one session, which is all the contract asks. |
| D12.a | worked with friction | F29 | **Built this round**: prompt v10, the inbox sentence. The inbox is one folder, the kept one, and any other inbox-kind source is a folder the person added. The notes rule out naming it by id (D2.d), and a test keeps the literal default path out of the block. |
| D12.b | informational | friction line 19 (attempt 4): "stopped before step 2 without choosing folders, as step 1 says" | **Built this round**: prompt v10, the folder-stop sentence. Step 1 says stop and wait for the folder answer; step 3 says the report, always. The skeptic refused the first fix as too big: a Rules-wide "where a step says to stop, run nothing after it" would switch the report off at three stops where the design wants it, and collides with the loop's own "NEXT: stop:". The built sentence carves out the one wait. The pair was exercised: 2 of 2 observed agents (attempt 4 and the rehearsal) stopped and ran no report. |
| D12.c | informational | fix request only, three sessions | **Built this round**: prompt v10, the hold gloss. `hold` is a legal or records hold, not a pause. Held since the first triage. |
| D12.d | informational | fix request only | **Refuted.** UTC in the fix request's heading is not built. It is by design: the time is there so that the heading marks one session, and nothing parses it. The proposed "in UTC" can cost an approval for a `date -u` that is no command of the block (inferred). |
| D12.e | informational | "(+2 more)" | **Reading only.** "The last NEXT: or WAITING ON YOU: line" picks one of three by position. Held in the second triage, still with no field evidence: the paste has no final message. |
| D12.f | informational | "1 of the 12 allowed" (fix request) | **Expected by design.** Dead weight checked: the "Not used" list, step 3's plain sync (the only one of the session that downloads), the 12-sync cap, the inbox formats. No change. |
| D12.g | informational | "NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU above); session done" | **Reading only.** The installer's output does not contradict the prompt. The v9 run printed one `fix:` line, a hand edit the Rules forbid the agent, not run. |
| D12.h | informational | measured with grep on main | **Reading only.** How the bump is wired: the block's four numbers, three constants, the digest pin and the literals in four test files. |
| D12.i | informational | the notes' recommendation | **Operator decision**, taken: v10 this round. The notes advised against a bump for wording alone (conviction 75%) and named "this round's build, if it is rehearsed anyway" as a case for it. Shipped: a, b and c. Not d. |
| U1 | informational | "loop: skill current · inbox on · baseline draft · topics 0 · checkpoint never · to curate 1455 · archive off" | **Operator action.** No item listed it: the baseline is still a draft, three sessions running. To curate 1,346 to 1,455 (+109); live files 910 to 1,006 (+96). Nothing past the baseline has a field run or a rehearsal. § Operator actions. |
| U2 | informational | "runs = 695"; "runs = 60"; "last runs: 1267 poll ok" | **Reading only.** 82 + 7 + 3 = 92: no background launch between the reports is without a run row. The Mac was awake about 6.8 to 7 of the 11.7 hours (estimated). In the interval before, 2 run rows came from something else: syncs are also typed by hand or by other tools on that Mac (inferred). |
| U3 | informational | `media` among "89 ok" | **Built this round**: a media-helper line in the report. § 1. |
| U4 | failed | "<folder-N> folder under ~/Library/CloudStorage" beside "~/Development/<folder-26> is listable" | **Built this round**: the folder legend. Two lines of one report contradicted: the class also takes configured project folders. |
| U5 | informational | the Purge queue part | **Built this round**: purge counts from the audit trail. "Did a purge already run" has been asked by hand in three triages, and the report read no audit trail. Counts only: the trail's lines carry source ids in clear. |
| U6 | informational | 1267 - 1213 = 54; 1175 - 1121 = 54 | **Reading only**, in D7. The incomplete streak is one unbroken streak since about run 55, in all three reports. With "baseline INCOMPLETE", the source has never finished a pass. |
| U7 | informational | "step=agentsync seconds=2 rc=0 result=done" with both jobs loaded | **Not built**: a recorded risk. `install.sh` reinstalls the tool with no lock and waits for no running job. This run's window was 2 s. A job that starts inside it can fail or mix builds (inferred; not seen in three rounds). The window of "a job is running" grows with recordings. No fix proposed. |
| U8 | informational | the notes' table | **Reading only.** What the report could not tell this round. Three causes cover most of it: ok checks carry no detail, the tail is 60 lines while one run's doctor is 97, and nothing records the agent's own sync output. |
| U9 | informational | "4 other instruction-like line(s) (4 fix:)" | **Reading only**, in D12.g. Counted over 5 runs. |
| U10 | informational | Status, both reports | **Reading only.** Counts that moved and are in no item, all explained. The growth is in project bridge folders: `<source-3>` 153 to 214, `<source-6>` 9 to 33. Engine pages rose by 13, so about 83 new files are types OCR does not touch (computed). "To curate" rose 10 more than the new files explain; no part of the report splits it. |
| U11 | informational | measured with the intake's own word list | **Operator action**, on the development Mac, not the field one: four throwaway files of the agents and one throwaway home folder hold identifying strings and were left in the shared temporary folder, readable by others. For the lead to remove. |

### The second triage's commits, as they are on main

The second triage cited each commit by its sha on the v9 branch, before the rebase. None of those shas is on
main. These are the ones this round cites, each checked with `git merge-base --is-ancestor` against `c7bfdb8`.

| Item | On main | Subject |
|---|---|---|
| C1 | `41f2484`, `015bcdc` | fix(setup): add-source and init clear the permissions a sync would, before install.sh asks status; fix(status): docs_repo.permissions names a sync as its fix when a sync clears it |
| C2 | `82ff572` | fix(setup-report): an install run that failed before the one that ended 0 is worked with help |
| C4 | `a9a5dbc` | fix(install): the bring-back file sends local work once |
| C5 | `fb128f0` | fix(install): several --source-local print the docs repo and sources lines once |
| C6 | `949cde9` | fix(install): every run leaves the setup folder and what is in it owner-only |
| C7 | `f2f4552` | feat(setup-report): Quarantine names the file types no converter reads |
| C8 | `fba11fa` | fix(setup-report): a queued purge whose id has no row is looked up as an alias |
| C9 | `61b2de1` | fix(setup-report): the OCR part says what read again, its hidden runs and a failed-once file are |
| C11 | `e919fee` | fix(launchd): a job names its interpreter as the launcher pins it |
| C13 | `ecfc5e0` | fix(setup-report): NEXT lines and runs in install.out are counted over the same whole runs |
| C14 | `2b76245` | fix(setup-report): the Loop line shows the wait the loop stopped on, not the first one |

## Built this round

None adds a command, flag, installer option, config key or environment variable. The commit column reads
"branch" until the work is on main: the plan's section B10 records the shas after landing.

| Item | What it does | Commit |
|---|---|---|
| D7.b, D7.c | The scope-change retire guard. A complete pass after a scope change retires at once only what the config no longer covers. Every other absent file of a local or inbox source goes through the breaker and the two-pass check, and queues its purge, as on any complete pass | branch |
| D9.i | An online-only file that no converter reads keeps the stub it has: it is no longer published again every pass | branch |
| D10.e | Recent errors merges consecutive repeats of one line into one entry with a count, so 40 entries show more than one stale warning | branch |
| D3.b, D3.c | The residue check flags a word of any case joined to a placeholder by `-` or `_`, and no longer flags a generic home folder that stands before one | branch |
| D3.f | A doctor line in the report loses its padding after redaction, so the spaces no longer give an id's length | branch |
| U4 | The folder legend says that a `<folder-N>` is a folder under `~/Library/CloudStorage` or a configured source's folder | branch |
| U5 | The Purge queue part counts the audit trail's purge actions since the oldest queued day. Numbers only | branch |
| U3 | The report says what the media helper is, beside the OCR helper's line | branch |
| D6.d | The dry run's note says that nothing is targeted when nothing is, in place of a count of every commit | branch |
| D6.f | A queue run writes the queue file after each entry, so an entry that was purged does not stay queued when a later one fails | branch |
| D2, D12.a | Prompt v10: the inbox sentence names the one kept folder. With it, the README and deploy README body wording, and doctor's "drop files you save by hand here", which now goes to the kept folder only | branch |
| D12.b | Prompt v10: the folder-stop sentence. No report is run while the agent waits for the folder answer of step 1 | branch |
| D12.c | Prompt v10: `hold` carries its gloss, a legal or records hold and not a pause | branch |

The prompt's text changed, so it is v10. v9 ran clean, so no new run on the corporate Mac is needed for it. The
next time the prompt is used it must be copied again from the README.

Two limits of the retire guard, from the notes, for the contract:

- Graph sources keep the branch that retires every candidate, so the same release exists for a drive source after
  any scope change. No Graph source is live on the field Mac.
- A row last listed before the source's recorded root still retires unchecked. That does not apply on the field
  Mac (inferred): its root record was first written in a run whose scope did not change. It applies where the
  first run on a build that keeps that record also changes the scope: an upgrade straight from a build before
  the always-on excludes of 2026-10-05, or a config edit in that same run.

## Not built and why

| What | From | Why not built |
|---|---|---|
| A materialised, empty cloud folder counts as listed | D7.d | The operator's decision `71d3e66ef726`. Its companion change is now built, which removes the reason it was unsafe (§ Open operator decisions) |
| A converter for `.url` | D9.c | Small value: 2 distinct link files, and a page titled by the literal section header. The operator's choice, since it widens the routed set in the contract |
| "15 of 16 source(s) listed completely (status: baseline complete)" reworded | D1.h | By contract: the words are pinned. Left for the third time |
| "unexpected 5" relabelled | D1.j, D5.e | By design and by contract. A relabel keyed on doctor's notes would read differently by hand, and would have hidden C11 |
| The report's "last install.sh run: commit" skips a list run | D4.b | Optional. Latent: only for a session that ends on a list run |
| The "no trace" line names the preview | D6.g | Refuted: the report's purge lines name no command, by contract |
| UTC in the fix request's session heading | D12.d | Refuted: by design, and it can cost an approval (inferred) |
| A guard for a reinstall under loaded jobs | U7 | A recorded risk with no fix proposed. Not seen in three rounds |
| The MDM host labels and the PAC file name | D3.d, D3.e | The operator's choice, recorded in C12: keep, shorten or replace |
| Recent errors in UTC | D3.f | No one-line fix: old and new lines would be mixed in one log |
| The purge wait names the preview | D6.h | Recorded in CONTRACTS as deliberately not built: five texts with their pins |
| A collapse of the all-zero re-read table | D8.d | Its columns are contract text. A contract amendment for a cosmetic gain |

## Open operator decisions

Both are filed as class B: open, with a default that changes nothing and fires at 2026-10-20T17:00:00Z. They are
recorded here, not decided.

**`71d3e66ef726` — May a cloud folder that is on the Mac and still empty ever count as empty?**

What moved:

- **The evidence is the same, twice.** `<source-1>` has 5 unknown folders, 5 of 5 checked: 0 dataless, 5
  materialised and empty, all 5 with a link count of 2, and the mirror holds 0 files below them. The same five
  counts in two reports 11 h 40 min and 92 passes apart.
- **The streak is one unbroken streak since about run 55** (U6): 1,213 consecutive incomplete passes at run
  1267 and 1,121 at run 1175, a difference of 54 both times, and 55 in the first round's figures (measured by
  subtraction). The source has never finished a pass. So what a first complete pass would release dates from
  the source's first days, not from one update. The bound is 0 to 329 rows (inferred), and nothing in the
  report narrows it.
- **The five folders are still no control.** They have been listed over 1,200 times, so "not dataless" says
  nothing about a folder macOS never listed.
- **The hazard in today's advice is measured** (D7.c), and both the tool and the field agent's own write-up
  point the operator at the `exclude` line. **The guard is built this round** (D7.b). With it, pasting the line
  retires at once only what lies under the excluded folders, which is nothing here.
- **A tripped breaker does not fail a setup re-run**: doctor has no breaker check. So the guard cannot turn a
  re-run into a failed one.

The narrow design, if the operator picks it (the skeptic's smaller version; `arm_local.py` only, 15 to 20 lines
estimated, no new surface):

- A zero-child cloud folder counts as listed, and is put on neither the unknown list nor the stored list of empty
  folders, when all of these hold: it is below the source's root; the walk was handed an index of the mirror;
  the folder is not dataless and has a link count of 2 both before and after the listing; and no present
  mirrored file lies below it.
- A walk with no index stays unknown (fail closed). An empty index means no mirrored file.
- No "seen in N passes" test. The verifier's two-pass form kept such a folder in the stored list, and two other
  readers take that list to mean "folders that hold the listing": the loop's wait and the report's Empty cloud
  folders part would then both say something untrue. The second triage had already judged the extra pass
  unneeded.
- It frees only folders that never held a mirrored file: all 5 here. A folder that was mirrored and then emptied
  in the cloud keeps today's wait.
- Existing tests invert and must be rewritten with it: the loop test that asserts "another sync does not clear
  it", and the cycle test whose first run now completes.
- What the field Mac would then do: the first sync after the update completes the source, and either marks the
  absent files or trips the breaker. The second sync tombstones the marked files and queues one purge each, so
  the purge queue grows by that number. The next report reads "none" for `<source-1>` in the Empty cloud folders
  part: a field confirmation with no new report line.

Conviction, as the notes give it:

- The packet: 75 for its default, keep the rule.
- The verifier: 85 percent that an empty materialised cloud folder may count as empty, for the narrow form and
  only after the guard. 25 percent for the wide form (any empty folder that is not dataless, mirrored files
  below or not).
- The skeptic: 85 is reasonable; not above 90 without one field pass.

Options in operator terms:

- (a) Keep the rule (the default). The operator pastes one `exclude` line. An excluded folder is never mirrored
  if it later gains files. Safe only on a build with the guard.
- (b) The guard, then the narrow form. No paste and no lost future files. Deletions held since the stuck period
  show up the normal way: a few after two passes with a purge queued, many as a tripped breaker.
- (c) The guard only, and decide the narrow form later. This is what the round built.

**`085fac870dd6` — Are online-only images downloaded so they can be read by OCR?**

Recommendation from the notes: leave the decision at its default, do not download. Conviction 88% (the packet's
own is 70).

- Two field reports in a row show 0 files and 0.0 MB that the other option would fetch, while the image count
  rose from 16 to 27.
- The one unknown the second triage kept is closed. The online-only refused file is a `.potx`, a deck template
  that no converter reads. No other type cell carries an online-only note.
- The default changes nothing, so leaving it costs nothing and builds nothing. There is no verb to close a
  packet: the operator can action it, or do nothing and let the default fire. The report keeps measuring the
  number every round, in files and megabytes.

Why not higher, with the skeptic's correction:

- The verifier's "a Mac with all folders kept on the device" is wrong: the paste shows one online-only file in
  `<source-1>`, so on-demand storage is in use there. What is true is that the online-only population of the two
  cloud sources is one file, and agentsync never downloads it.
- The 27 images are not split by source. 14 of the 16 sources are local folders where no file can be online-only
  (inferred), and the cloud sources hold 335 of the 1,006 live files (computed). So "0 of 27" may rest on few
  cloud images, or none.
- The same rule refuses every image of a Graph source. Graph is off here. Leaving the default settles that case
  too, and "of a Graph source" is the cell most likely to rise first.

If the packet is actioned, two lines still call it open: `docs/plans/bring-back-ocr.md` and the CONTRACTS
paragraph on the report's image line.

## The purge queue

What the report now says:

- 14 purges are queued for `<source-13>`, an inbox source: reason `upstream-deleted`, by stable id, all queued
  on 2026-10-05. All 14 are under `no row`, 0 under every other fate and 0 "not looked up".
- "no trace: 14 queued id(s) have no row and are no alias." The purge resolves an id the same way the report
  now does. So the second triage's worse cause, that the ids are aliases of live files and a run erases 14 live
  pages, is excluded for these 14.
- What took the rows away is not decidable from the paste: a queue run whose purges were not verified, a purge
  by selector, or an erasure by content match. The audit trail on the Mac decides it, and the report counts it
  from the next build on (U5).

What a run of the queue does to the 14:

- **Expected: it erases nothing and empties the queue.** For an id with no row and no alias, a run rewrites no
  commit and leaves HEAD as it is, unless a page of `<source-13>` somewhere in the docs repo's history still
  carries that id. Measured on a fixture: "VERIFIED · 0 item(s) · 0 docs path(s) · 0 commit(s) rewritten · 0
  blob(s) targeted", and the entry leaves the queue.
- **It is not free.** Measured on the same fixture, for one entry that targets nothing: the stash list went from
  2 entries to 0, the HEAD reflog from 7 entries to 0, a remote-tracking ref was deleted, and the staging folder
  and a temporary cache folder were emptied. The run also prunes unreachable objects and compacts the manifest.
  14 entries do all of that 14 times.
- **It takes minutes, not seconds** (estimated from the code: each entry walks the whole history). The writer
  lock is held throughout, so a background poll that fires meanwhile exits 75.
- **Two cases outside the code would erase something, and the preview shows both.** A manifest that was replaced
  or rebuilt by hand can have no row and no alias for an id that a page in HEAD still names: the run then erases
  that page. And once a page is targeted, its docs path is cleaned up for whoever holds it now: measured, a live
  file at the same path lost its index line and its output row while the run still said VERIFIED. Neither is
  expected here, where the run ids reach 1267. Both print a `path:` line in the preview.

The operator's order (the verifier's, with the skeptic's corrections):

1. Nothing is urgent. Leaving the queue costs one doctor warn and one WAITING ON YOU line per run.
2. Check the docs repo first: `git status --porcelain` empty, `git stash list` empty, nothing wanted from the
   reflog, and no sync running. The preview does not show a dirty tree, and a real run then prints nothing for
   the entry and keeps it.
3. `agentsync purge --queue --dry-run`. Expect 14 blocks that each read `0 item(s)`, `0 docs path(s)` and `0
   blob(s) targeted`, with no `path:` line, and a last line that counts 14 previewed and 14 queued. The cache
   count in that line may be above 0. On the field Mac's build, ignore the "commit(s) would be rewritten" note:
   it counts every commit.
4. All 14 zero: run `agentsync purge --queue` straight after, and compare the counts in the two last lines:
   both 14. A run purges whatever is queued at that moment, not what was previewed, and any sync can add an
   entry in between. Fewer than 14 previewed means a hold kept some.
5. Any entry prints a `path:` line: stop, and bring the output back. No command drops one queue entry.
6. A run that ends NOT VERIFIED or with entries still queued: run it again once the cause is cleared. If the
   same entries are not verified a second time, it will repeat every time: bring the output back.
7. Counting the audit trail's purge lines on the Mac is fine. A pasted line needs redaction: it carries the
   source id in clear.
8. Not the setup agent. The prompt keeps every purge with the operator, and three sessions obeyed.

The precondition is met: `--dry-run` with `--queue` is honoured from `cace1d9` on, and the Mac's build contains
it.

## Operator actions on the field Mac

Not decisions. The order is the D5 skeptic's.

1. **First, look, and change nothing.** `~/.local/bin/agentsync status`: "last runs" should show poll runs on
   the 5-minute cadence, started after 2026-10-08T04:35:40Z and ending ok. The paste has no background run of
   the new build, and recordings are read only by the background jobs. A refresh done before this look would
   confound a problem of the new build with a problem of the refresh.
2. **Confirm the baseline questions** (U1). A third session ended waiting on them, with 1,455 items to curate
   behind it: keep about 10 in `_eval/questions.md`, correct `_eval/answers.md`, and change both to `status:
   confirmed`. Both files are in the docs repo. The other side, for the operator to weigh: the next session is
   then the first to curate, about 1,455 items, unrehearsed at that size.
3. **Delete line 22 of `~/agent-context/sources.toml`**, after checking that it is the `company =` line under
   `[graph]` (carried over twice). It clears one warn and the last `fix:` line the installer prints. A broken
   TOML stops every run, background ones too, until it is corrected.
4. **Optional: `~/.local/bin/agentsync install-agent`**, once, from the `c7bfdb8` build, right after a poll has
   ended. It clears two warns. It is no detection gain on this Mac. Low risk: a job that is mid-run gets a
   SIGTERM; one job ends 75 and may show it for up to an hour; a failed bootstrap leaves that job unloaded until
   the command is run again; no exit 64; no privacy prompt expected. Never from a build before `e919fee`, and not
   `install.sh --confirm-install-agent`, which can replace the launcher. The drift comes back by design each time
   a protected source or sentinel is added.
5. **Do not paste the `exclude` line for `<source-1>`** on the `c7bfdb8` build (D7.c). On a build with the
   guard it is safe, and decision `71d3e66ef726` says whether it is needed at all.
6. **The 14 queued purges**: preview only, in the order of § The purge queue.
7. **Edit lines 12 and 14 of `~/agent-context/setup/friction.md` in place**, replacing the four name tokens
   with roles (C12, still there). Editing the text of a line moves no F id and is safe for a re-run. While
   there, lines 5 and 13 hold placeholders the agent typed (`<folder-3>`, `<folder-4>`): replace them by words,
   and the Residue line reads clean, so that a later hit is a signal.
8. **The fix-request file's old sessions** (D11.c). The v7 part holds real names and returns with every file.
   Clearing the file costs the next session the items its status paragraph is written against. The narrower
   hand edit keeps them: replace the names in the v7 part by placeholders. "Leave an earlier session's text as
   it is" binds the agent, not the operator.
9. Nothing for the setup agent. The prompt keeps all of these with the operator.

When the prompt is next used on any Mac, copy it again from the README: it is v10.

## Still wanted

Only what no report can say, all read locally on the field Mac. None blocks anything.

- The two `credential` stubs in `<source-3>` (D9.h, U6 of the second triage): read the two stub pages or
  `_sync/QUARANTINE.tsv`. Are they meeting invites whose join link was taken for a credential? They were built
  on 2026-10-02, before that fix, and nothing re-reads them.
- The type behind "other 2 (1 distinct)" (D9.f): if it is a real file type, it is one word added to the
  report's list.
- Whether `<source-7>` really is a copy of the library that `<source-1>` syncs (D9.g), before either source is
  dropped or excluded. Either remedy tombstones that source's pages.
- The holder of the lock at 2026-10-07T16:31:56Z (D10.d): the line above that warning in the poll's error log
  names it.
- For `71d3e66ef726`, the control the second triage asked for is wanted only for the wide form now: is a cloud
  folder that this Mac never listed dataless at its first `lstat`? The narrow form does not depend on it.

The first report from a build with this round answers the rest: whether a purge ran since 2026-10-05 (U5), what
the media helper is (U3), and whether Recent errors hid anything behind its repeats (D10.e).
