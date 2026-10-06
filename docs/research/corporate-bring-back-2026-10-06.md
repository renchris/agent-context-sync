# Corporate Mac bring-back file, triaged (2026-10-06)

Scope (frozen): triage the bring-back file from the corporate Mac (prompt v7, main `1ea5fc4`) against main; for
each item record result, kind, evidence, whether already fixed (commit), expected by design, or new with the
smallest fix.

Method: Workflow run `wf_44ecd2de-039`, 112 agents: one verifier and one skeptic per claim (47 claims), eight
patch units each with a skeptic, one completeness critic. Where the skeptic corrected a verdict, the corrected
status, root cause and fix are the ones recorded here. Where two verdicts disagreed, the lead ruled; each ruling
names the rejected alternative. Line numbers were read on main `1ea5fc4`. The verifiers ran no test suite.

Names: the paste's sections 2 and 3 are unredacted and section 1 leaks (I2). This doc uses the report's own
placeholders (`<folder-1>`, `<source-1>`, `<org-1>`) or made-up names. Evidence lines are quoted with any real
name replaced. The paste, the patch and the full findings stay outside the repository.

## Verdict

- **The computed outcome, "failed at step 2", is wrong for this run.** `install.sh` exited 0 in 14 s and the loop
  reached "baseline drafted". The outcome flipped because the agent logged a redaction complaint as kind `error`,
  with text that begins "Exit 0, no failure" (I5). The earlier attempt's "failed at step 1" is also wrong (I4).
- **Section 1 is labelled redacted and is not safe to post.** Four paths leak real folder, directory, project and
  source names (I2), and the tool's own section headings print the home path (I3). Nothing was sent
  automatically.
- **Every v7 run that reaches the baseline draft ends on a doctor FAIL the agent never sees.** The agent writes
  `_eval/` with its shell's default mode, `docs_repo.permissions` fails, and `sync` still says "session done"
  (I6).
- **One cloud source can never finish a listing.** Five deliberately empty cloud folders kept `<source-1>`
  incomplete for 906 passes, and none of the three guidance texts says what to do (I10, I11). Whether an empty
  cloud folder may ever clear by itself is an open operator decision (O1).
- **All four fix requests are real and new.** Each is fixed without a new command, flag or config key (I1, I13,
  I10, I14). Two requested changes are rejected because they add surface or weaken redaction: a per-source
  `allow_empty` key, and exempting the agent string from redaction.
- **19 new items (I1 to I19), none adds surface.** Of the 47 claims: 21 new (they fold into 18 items, plus one
  small defect found inside an expected-by-design claim), 1 already fixed (`ebc0e1d`), 25 expected by design.
  Nothing in the report contradicts the KISS end state.
- **The patch is rebuilt, not merged** (§ The patch). Two questions are the operator's (§ Open operator
  decisions).

## Inputs and version

| Fact | Value | Source |
|---|---|---|
| Report | `agentsync setup-report`, generated 2026-10-06T16:46:11Z, macOS 15.7.3, arm64, MDM-enrolled | report § Run metadata, Environment |
| Setup prompt | v7 (attempt 2). Attempt 1 was v6 on 2026-09-30 | friction log headers |
| install.sh commit | `1ea5fc438db4`, the checkout's HEAD and on origin/main; that is main at triage time | `install.commit`, install.log |
| Agent | GitHub Copilot CLI (shown in the report with "Copilot" replaced by a folder placeholder: I1) | fix request 1 |
| Computed outcome / run type | failed at step 2 / Real Mac | Summary |
| Attempts | "2 of 2". Really three sessions: 2026-09-30 (v6, `35c2b5f`, `--confirm-install-agent`, config created, exit 0 after 76 s); 2026-10-06T02:16Z (no header, stopped on `git pull`); 2026-10-06T16:42Z (v7) | install.log, friction lines 1-15 |
| Config | 16 sources, all live: 2 cloud folders and 14 inbox-kind (13 older bridges plus the generic `inbox` this run added) | Configuration, Status |
| Doctor | 96 checks: 85 ok, 2 info, 8 warn, 1 FAIL | Doctor |
| Fix request | received: 4 requests and a 12-bullet "Not used" section | section 2 |
| Local work | one patch, 22 files, 3,587 lines, committed by step 1's keep-local-work command (`ebc0e1d`) | section 3 |

One correction to how the paste reads. The "Mac that already runs agentsync" was created by attempt 1 of this
same setup: its install created the config and turned background sync on. The 13 inbox bridges and the second
cloud folder were added by hand between 2026-09-30 and 2026-10-06, outside any logged attempt. So the 14 queued
purges and the 903 incomplete passes are at most six days old.

Friction kinds: lines the agent logged keep its kind (`deviation`, `prompt`, `error`). Every other kind below was
assigned during triage.

## 1. What happened

| # | Claim | Event | Result | Kind | Evidence line (names replaced) |
|---|---|---|---|---|---|
| 1 | S1 | Report and issue link redact the agent's product name | worked with friction | redaction | "agent: GitHub <folder-4> CLI claude-opus-5.5" |
| 2 | S2 | Nobody could answer the folder question; the agent chose folders itself, in both attempts | worked with friction | deviation | "ask_user returned unavailable); agent chose the two project folders <folder-1> and <folder-2> itself" |
| 3 | S3 | `git pull --ff-only` refused a checkout with local edits; the agent stopped | failed | error | "local uncommitted changes ... would be overwritten by merge; stopped since stash/reset is not allowed" |
| 4 | A1 | That session had no `Attempt:` header, so its line was folded into attempt 1 and set its outcome | worked with friction | inconsistency | "attempt 1 (lines 1-7 ...): failed at step 1" although that run was "exit 0 after 76s" |
| 5 | S4 | The Mac already ran agentsync; the prompt does not say what to expect | worked with friction | prompt | "install reported already configured and kept them, so the first sync was not a first sync" |
| 6 | S5 | Baseline questions drafted with three parallel read-only sub-agents | worked with friction | deviation | "because the mirror holds about 1,290 pages" |
| 7 | A2 | Friction lines 12, 13 and 14 share one timestamp | informational | inconsistency | three lines at "2026-10-06T16:44:34Z" for step 2, step 2 and step 3 |
| 8 | S6 | Outcome "failed at step 2" beside "install.sh exit 0"; the two issue links disagree | worked with friction | inconsistency | "F13 · step 2 · error · Exit 0, no failure: ..."; one link says "Fully one command", the other "Failed at step 2" |
| 9 | S7 | doctor FAIL: group/other can read `_eval`; it becomes the report's NEXT | worked with friction | error | "[FAIL] docs_repo.permissions — group/other can read tenant data: ~/agent-context/docs/_eval" |
| 10 | S8 | doctor warn: an ignored config key | informational | error | "[graph] company is ignored ... (fix: delete line 22 of ~/agent-context/sources.toml)" |
| 11 | S9 | doctor warn: no sentinel on a source the installer added | worked with friction | error | "no sentinel: a TCC-hidden or unenumerated tree cannot be told apart from an empty one" |
| 12 | S10 | Both launchd jobs: installed plist differs; the installer skipped the agent step | worked with friction | error | "installed plist differs from this config/interpreter (ProgramArguments) (fix: agentsync install-agent)" |
| 13 | S11 | `<source-1>` incomplete for 903 to 906 passes; no guidance clears it (fix request 3) | failed | error | "enumeration incomplete for 906 consecutive passes (deletions held)" |
| 14 | S19 | The same five WARNING lines on every 5-minute poll | worked with friction | error | "The last 40 of 524 WARNING/ERROR line(s)", all "<source-1>: <path>" |
| 15 | S12 | 14 queued purges shown as WAITING ON YOU | informational | error | "WAITING ON YOU: 14 queued purge(s): run `~/.local/bin/agentsync purge --queue`" |
| 16 | A3 | Summary says "(+2 more)" waits; install.out printed two | informational | inconsistency | "WAITING ON YOU: 14 queued purge(s) ... (+2 more)" |
| 17 | S13 | `network.proxy` printed twice with the same PAC fact, no Graph source | worked with friction | error | two "[info] network.proxy" lines, each ending "(only Graph sources use the proxy, and none is live)" |
| 18 | S14 | AGENTS.md sends mail drops to another project's inbox folder (fix request 2) | failed | error | "save them as files ... into" a folder that belongs to a different project's bridge |
| 19 | S15 | Two `--source-local` print the summary twice (fix request 4) | worked with friction | error | "once with "6 scaffold file(s)" and once with "0 scaffold file(s)"" |
| 20 | S16 | Installer output carries operator-only `fix:` lines | informational | info | "4 NEXT: line(s) in 4 run(s) ... and 8 other instruction-like line(s) (8 fix:)" |
| 21 | S17 | Redaction leaks in the section labelled redacted | failed | redaction | "args=--source-local ~/Library/CloudStorage/OneDrive-<org-1>/<real folder name, spaces escaped>" |
| 22 | A4 | Sections 2 and 3 are unredacted and map section 1's placeholders to real values | informational | redaction | "Review before sending: section 1 is redacted; sections 2 and 3 are not." |
| 23 | S18 | doctor says the skill is missing; status says "skill current" | informational | inconsistency | "[info] skill — missing: the next sync writes it" |
| 24 | S20 | "queue 1265" with no unit, four lines above "queued purges: 14" | informational | info | "baseline draft · topics 0 · checkpoint never · queue 1265 · archive off" |
| 25 | A5 | "baseline" means two things in adjacent lines | informational | inconsistency | "baseline draft" then "baseline complete" and "baseline INCOMPLETE" |
| 26 | A6 | 24 quarantined items appear only in status | informational | error | "quarantined 11", "quarantined 2", "quarantined 10", "quarantined 1" |
| 27 | S21 | An `[ok]` doctor line whose text is an instruction | worked with friction | inconsistency | "[ok  ] tcc — ... grant Full Disk Access to ~/Applications/AgentSyncLauncher.app" |
| 28 | S22 | "first sync" on a Mac at run 958 | worked with friction | inconsistency | "first sync: converted 6, deferred 0 online-only" |
| 29 | S24 | Background sync called "not turned on" while both jobs are loaded | worked with friction | inconsistency | "com.agentsync.poll: plist present · loaded ... runs = 418 · last exit code = 0 (ok)" |
| 30 | S25 | "human turns: 1 (1 question" though nobody answered | informational | inconsistency | "human turns: 1 (1 question; 0 clicks beyond the announced Allow click (not logged)" |
| 31 | A7 | The two ad hoc launcher warns are classed as expected | worked | info | "expected 2: launcher.signature, launcher.requirement (ad hoc launcher)" |
| 32 | A8 | The mechanical steps all exited 0 | worked | info | "exit 0 after 1s ... done (listed-24)"; "exit 0 after 14s"; "took: 1.5s (time limit 12s)" |
| 33 | A9 | IT draft line on a Mac where nothing needs IT | informational | info | "IT draft: ~/agent-context/it-request-draft.md exists; 2 field(s) left for you" |
| 34 | A10 | `sync` said "session done"; the report's NEXT named a FAIL | worked with friction | inconsistency | "NEXT: the docs_repo.permissions check failed: do what the fix on its [FAIL] line below says" |
| 35 | critic | A fourth `install.sh` run is counted and never shown | informational | info | "4 install.sh run(s) ... 91s in total (runs with an end line); the last 3" |
| 36 | critic | One source's root lies inside another's | informational | info | two doctor lines whose paths are a folder and a sub-folder of it |
| 37 | critic | The Summary copy of F12 is cut mid-word | informational | info | "doctor raised 14 qu…" |
| 38 | critic | The residue check finds three leaked words and misses the rest | informational | redaction | "Residue check: 5 capitalised word(s) next to a placeholder ... check them." |
| 39 | critic | Counts disagree inside the paste | informational | inconsistency | "14 extra sources" / "one of 15" / "inbox 14, local 2"; "958-run history" / run 960 / "runs = 418", "runs = 36" |
| 40 | critic | `heartbeat.inbox` and status disagree about the new inbox | informational | inconsistency | "no completed pass recorded yet" against "baseline complete · complete yes" |

## 2. Status per item

| # | Status on main `1ea5fc4` | Detail |
|---|---|---|
| 1 | **New** (I1) | A listed cloud folder is named with the agent's product word. Every folder name under `~/Library/CloudStorage` at depth 2-3 is registered as a whole-word, case-insensitive literal for any text (`setup_report.py:1637-1647`, `:531-554`), and the agent string is never exempt (`:2417`, `:2683-2687`, `:2837-2838`). The first triage never saw this: the 2026-09-30 line sat in `friction.md` for six days. The one `<folder-3>` in the log is text the agent typed, not a second defect. |
| 2 | **New** (I7) | Prompt step 1 has no branch for an unanswered question (`README.md:186-188`); the only fallback covers a failed command (`:164-165`). In attempt 2 the pick added nothing new: both folders were already configured. |
| 3 | **Already fixed**: `ebc0e1d` | Line 7 was written at 02:16:01Z; `ebc0e1d` was committed at 02:28:24Z. The fix then ran in attempt 2: the patch is dated 5 seconds before that attempt's header, and install.log shows a clean checkout. The line is carried forward by the append-only friction log and does not count against `1ea5fc4`. The rescue command is deliberately not pre-approved (`tests/test_deploy_pack.py:388-392`). |
| 4 | **New** (I4) | `parse_friction` opens an attempt only on a header (`setup_report.py:785-848`), so an event after `end | finished` joins the closed attempt, and `stopping_error` (`:998-1021`) picks it. `ebc0e1d` removes only the trigger this Mac hit: any other failure before `--log-start` in step 1's chain (`README.md:175`) folds the same way. |
| 5 | **New** (I8) | Prompt v7 is written for a first run (`README.md:152-225`). Everything the code did was as designed: `add-source` prints "already configured" and writes nothing (`cli.py:590-595`); status and doctor report inherited state. |
| 6 | **Expected by design** | The Draft step says what to read and write, not how (`skill.py:102-107`). The agent logged a deviation because the prompt defines one that way. No change on a single report. The page figure cannot be confirmed from the paste. |
| 7 | **Expected by design** | `install.sh --log` stamps the time of the call (`scripts/install.sh:196`), and v7 takes no step time from friction lines. The stamp is not inert: it decides whether a later exit-0 install run resolves an `error` line (row 8). "session 3m27s" is derived from these stamps. Optional one-sentence doc note. |
| 8 | **New** (I5) | `stopping_error` resolves an install-step error only by an exit-0 install run started at or after the error's log time (`setup_report.py:1014-1018`). No step lies between install step 2 and report step 3, so `went_on` (`:1013`) can never rescue it. Any `--log 2 error` written after the run flips the outcome, whatever its text. The first link is stale: it was printed by the install run, before F13. |
| 9 | **New** (I6) | The agent's file tool creates `_eval/` outside the CLI's umask wrapper. The only tightening pass is top-level, at init and add-source (`cli.py:509-513`); the check walks the whole worktree (`ops/doctor.py:1256-1312`). The check samples 500 entries, so what it names is not exhaustive. Separate and optional: redaction turns the fix's quoted paths into `'~/...'`, which a shell does not expand. |
| 10 | **Expected by design** | The KISS K15 one-time notice (`e49e212`; `docs/plans/kiss-simplification.md:150-151`). Attempt 1's install wrote line 22 from the old template. Operator action: delete the line. Do not class it as an expected warn: that would strip the fix text. |
| 11 | **New** (I12) | `local_source_table` writes the sentinel only as a comment, on purpose (`config.py:649-657`; K14 rejected auto-picking one), and doctor warns on exactly that state (`ops/doctor.py:634-642`). The walker already holds deletions for an empty or unlistable cloud folder (`arm_local.py:470-475`, `:814-823`). Why `<source-1>`'s sentinel check is ok is unknown (probably set by hand). |
| 12 | **Expected by design**; the installer wording is **new** (I9) | Plain `install.sh` skips launcher, agent and wait since K11b (`c60faa0`). The argument list grew after the only install-agent run: one `--canary` per protected live source (`ops/launchd.py:263-275`, `:290-310`), and sources were added by hand. Which arguments differ is unknown; doctor prints only the key name. Operator action: run `agentsync install-agent` once. |
| 13 | **New** (I10); relaxing the rule is an **operator decision** (O1) | The hold is by design: a zero-child cloud directory is unknown, never empty (`arm_local.py:470-477`, `:855`; `docs/design/CONTRACTS.md:474`). The defect is three texts. The doctor fix claims another sync clears it (`ops/doctor.py:1230`). The WAITING line is one sentence for every cause, offers access although `listable` is ok, and points at an alarm the installer's first sync does not print (`loop.py:323-328`). The alarm names at most five folders and gives no exclude syntax (`arm_local.py:829-835`). Read-side effect, as designed while incomplete: INDEX says the source's negative answers are unreliable. |
| 14 | **New** (I11) | One WARNING per zero-child cloud directory on every full walk, with no once-only guard (`arm_local.py:474-476`, from `502295d`). The pass alarm already reports the same folders once (`:827-836`). Recent errors shows the last 40 without collapsing repeats, so the section carries no information. "524" is bounded by a 64 KiB tail, not a total. Log times are local; the report is UTC. |
| 15 | **Expected by design** | The queue is durable state that only the operator drains (`cycle.py:1595-1603`; `cli.py:1668-1683`). It is surfaced in four places already. Covered by I8's sentence. Do not class it as an expected warn: it is a pending destructive action. Operator action below. |
| 16 | **Expected by design** | Two moments. The third wait is the baseline-draft one (`loop.py:349-353`), which did not exist at install time (inferred from Status). Showing the first wait plus a count is pinned (`setup_report.py:2608-2611`). Optional doc sentence in `docs/deploy/setup-feedback.md:154-160`. |
| 17 | **New** (I15) | One PAC fact is built as both a warning and a policy error (`net.py:277-279`, `:302-306`), and `_network_checks` prints both under one name (`cli.py:1025-1026`, `:1031-1032`). The first triage's N13 changed severity only. |
| 18 | **New** (I13) | `Publisher.root_guide` takes the first live inbox source by id (`publish.py:665-669`), a K07 choice made when one inbox was assumed. A bridge id that sorts before `inbox` wins. Also found: `README.md:54` says every sync keeps the drop folder, but no sync path calls `ensure_inbox`. |
| 19 | **New** (I14) | The installer calls `add-source` once per folder (`scripts/install.sh:1377-1381`), and each call ends in `_ensure_setup`, which always prints both lines (`cli.py:516-519`, `:524-528`). Nothing was undone: the first call rewrote 6 scaffold files, the second found nothing to write. The count has no verb. The block is evidenced only by the fix request. |
| 20 | **New** (I19), informational | The installer tees the whole check list into install.out. Doctor rewords only two fixes there, so the drift and purge-queue lines print raw `fix:` commands that are the operator's (`ops/doctor.py:1088-1095`, `:1138-1147`). The count spans every run in the tail, not this run (`setup_report.py:1987-1988`). No harm here: the agent followed neither. |
| 21 | **New** (I2) | Four paths, none touched by the first triage's N2 fix (`bef87c1`). (1) Shell-escaped spaces in install.log `args=` defeat the folder match (`setup_report.py:359-360`). (2) The install.out tail is not passed through the item-path scrub (`:1993-2000`; only caller `:2337`). (3) Only CloudStorage-derived source ids are registered (`:1610-1615`, `:1648-1649`). (4) A source path outside CloudStorage registers nothing (`:1526-1534`). Lesser: the MDM server's host prefix and the PAC file name. Agent free text is uncovered by design. One id shows as `<folder-1>` and the other as `<source-1>`: cosmetic, the value is hidden either way. |
| 22 | **Expected by design**, plus one small defect that is **new** (I3) | Concatenating the raw fix request and patch is deliberate (`scripts/install.sh:877-903`, `0061ba3`). Not covered by that design: the two section headings print the expanded home path (`:887`, `:889`), and the prompt gives the agent no rule about names in the fix request. |
| 23 | **Expected by design** | Two moments of one run: the status step ran before the first sync wrote the skill (`cycle.py:724`; `docs/design/CONTRACTS.md:5662-5666`). The file was absent because attempt 1's build had no skill writer (`90bd05b` added it). Optional: the NEXT text could also name AGENTS.md for agents that do not load the skill. |
| 24 | **New** (I17), informational | The bare label `queue` (`loop.py:479`) counts curation rows, and the same output prints `queued purges`. The line also ignores the curation hold, so it showed 1265 while `curate` would list none. |
| 25 | **New** (I18), informational | One word labels the per-source first listing (`cli.py:784-786`) and the baseline questions (`loop.py:476`). Wording only. |
| 26 | **Expected by design**; one possible defect **unverified** | Quarantine is surfaced in status, `_sync/STATE.md` and `_sync/QUARANTINE.tsv` by design. The count mixes settled quarantines, refusals and failed conversions (`cycle.py:2174`). Possible stale subset: quarantine is sticky for an unchanged file, so the first triage's join-link fix (`de081ae`) does not re-read rows quarantined before it. Whether that explains the 2 mail items is unknown (§ Still wanted). |
| 27 | **New** (I16) | The `tcc` ok line always appends the Full Disk Access instruction and a pointer to `tcc.<source>` lines (`ops/doctor.py:694-699`, from `239bda3`). It checks neither the launcher's state nor whether those lines will print. Here the launcher demonstrably had access, and the report's instruction counter misses the line. |
| 28 | **Expected by design**; the label is **new** (I8) | "first sync" is a fixed step name (`scripts/install.sh:1536`, `:1544-1557`). "converted 6" is files read and converted across the 16 sources (`cycle.py:1801`), not the 6 scaffold files of row 19. Which 6, and whether the same 6 are reconverted every pass, is unknown. |
| 29 | **New** (I9), wording only | The tool's output is correct. The agent's line follows the prompt's "background sync is mine to turn on later" (`README.md:192-193`), which is false on a Mac where attempt 1 turned it on. The installer's agent step says only `skipped (not-requested)` (`scripts/install.sh:1582-1584`). Run 960 against 418 + 36 launchd runs: launchd counts since the last load, so the jobs were reloaded at least once. A manual sync is also recorded as "poll". |
| 30 | **Expected by design** | v6 and v7 do not log the folder question, so one question is assumed (`setup_report.py:2459`; pinned by `tests/test_setup_report.py:1382-1384`). The report cannot tell an unanswered question from an answered one. The announced click is the terminal's, in step 1, not the launcher's. Optional relabel; I7 is the substantive fix. |
| 31 | **Works** as designed | `expected_warn` (`setup_report.py:1168-1176`). The counts add up: 85 + 2 + 8 + 1 = 96. |
| 32 | **Works**, with limits | Both installer runs exited 0 and the report took 1.5 s. But one of the two requested folders is the incomplete source, the background exit 0 belongs to attempt 1's jobs, and the 15-question draft is the agent's own account. |
| 33 | **Expected by design** | The line prints whenever the draft file exists (`setup_report.py:2730`; `docs/design/CONTRACTS.md:4969-4970`). Which run wrote the draft is unknown. Optional one-line gate; or the operator deletes the leftover file. |
| 34 | **New** (I6) | `sync` computes NEXT without check results (`cli.py:624-626`); status and the setup report pass check FAILs as rule 1 (`cli.py:873-878`, `:1314-1324`). So the README's "status prints the same NEXT: line" is false whenever an ERROR-severity check fails. I6 removes this trigger; other ERROR checks can still diverge. |
| 35 | **Unknown** | The three runs shown sum to 91 s, so the fourth has no end line or took 0 s. Which run, and when, is not in the paste (§ Still wanted). |
| 36 | **Unknown** | Neither `config.py` nor `ops/doctor.py` checks for one source root inside another. The parent shows live 0 and the child live 37. Why is unknown; it may be the operator's excludes. One question to the operator, not a fix. |
| 37 | **By design, unclaimed** | The Summary shortens long items (`setup_report.py:1520`); the full text is in the friction table. No verdict covers it and no fix is proposed. |
| 38 | **Known gap**, folded into I2 | The residue check sees only a capitalised word directly after a placeholder (`setup_report.py:364`, `:2772`). It cannot see lower-case ids or a name after a `/`. The second half of the first triage's N2 ("flag any unredacted name that follows a source id") is still not implemented. Its "5" is five distinct words, listed as seven. |
| 39 | **Not a defect** | The computed figures are right: 14 inbox sources, of which 13 are older bridges. The fix request's 15 and "14 older" are the agent's miscount. |
| 40 | **Unclaimed** | Probably the same two moments as row 23, since this run added the inbox. Not verified. |

### "Not used" bullets (N1 to N12)

| # | Part | Status | Outcome |
|---|---|---|---|
| N1 | sync | expected by design | Nothing to cut. It was used. Three passes ran in the session (903 to 906); the agent's "three times" lists two, and the third was probably a background poll. |
| N2 | curate | expected by design | Nothing to cut. The baseline hold stops a setup session before curation (`loop.py:390-393`). |
| N3 | status | **new** behind the stated reason | Nothing to cut, no nudge. The agent skipped it because "sync printed the same NEXT line", which is how it missed the FAIL: I6. |
| N4 | add-source | expected by design | Nothing to cut. The installer called it; the step note `add-source` records the branch that ran, not what changed. |
| N5 | accept-deletions | expected by design | Nothing to cut. `<source-1>` can never reach the state where it applies: I10. Latent, not seen in the field: on an incomplete source it clears a tripped breaker while applying nothing (`cycle.py:1578-1581`). |
| N6 | adopt | expected by design | Nothing to cut. Operator-run import; the pinned surface keeps it. |
| N7 | purge | expected by design | Nothing to cut, no stronger nudge. The queue is already shown in four places, and a stronger nudge would push toward erasing content. Operator action below. |
| N8 | hold | expected by design | Nothing to cut. The agent read it as "pause a source"; it is a legal or records hold. A gloss can ride the next prompt revision. |
| N9 | offboard | expected by design | Nothing to cut. |
| N10 | inbox | expected by design | Nothing to cut. The wrong folder in AGENTS.md is I13. |
| N11 | baseline questions | expected by design | Nothing to cut. 15 drafted is what the skill asks for; the operator keeps about 10. The draft is what triggers I6. |
| N12 | background sync | **new**, wording | Real nudge: the installer should say that background sync is already installed (I9). |

The four operator-only commands (adopt, purge, hold, offboard) will read "not used, out of scope" on every healthy
run. That is low signal by construction and harmless.

## 3. New items: smallest fix

Every item adds no command, flag, config key or environment variable. Ownership: the plan
(`docs/plans/bring-back-ocr.md`, wave I1 "Field fixes") owns every file below. This doc proposes and does not edit.

| Id | Item | Root cause on main | Smallest fix | Proof | Adds surface |
|---|---|---|---|---|---|
| I1 | Agent name redacted (fix request 1) | `setup_report.py:1643-1645` skips only names in `_GENERIC_FOLDERS` (`:315-334`) | Lead ruling: add agent product words to the generic-folder skip, compared case-insensitively. "Copilot" is the only word the field evidences. Rejected: exempting the free-text agent string (it reaches a public issue URL, and `:2838` scrubs it on purpose). Cautions from the skeptic: the set has a second consumer, `residue()` (`:2781`), and its docstring (`:336`) would become false, so a small separate set checked only at `:1644` keeps both honest; other agent names are also first names or plausible folder names. Limits: a configured folder with that name, or a multi-word folder name, is still redacted. | `tests/test_setup_report.py`: a listed folder named Copilot plus that agent string keeps "Copilot" in the Summary and the link; a listed folder named Contoso in the agent string is still redacted | no |
| I2 | Redaction leaks in section 1 | `setup_report.py:359-360`; `:1993-2000`; `:1610-1615`, `:1648-1649`; `:1526-1534` | (1) Allow a backslash in `_SEP_RE` and `_FUZZY_SEP`. This also covers the escaped arguments in install.out's run header and NEXT lines. (2) Scrub item paths in the install.out tail, only on Python logging lines (`WARNING|ERROR|CRITICAL agentsync.`), so `error:` and `fatal:` lines keep their path. (3) Register every configured source id, skipping `inbox`, ids with no separator and a small generic set. (4) For a source outside CloudStorage, register the first non-generic directory below home as a folder value. Optional, operator's call: show only "present" for the MDM server and only the host for the PAC URL | six cases in `tests/test_setup_report.py`, made-up names: an escaped `args=` line; a nested-directory WARNING in install.out; an `error:` line that keeps its path; a hyphenated inbox id becomes `<source-N>`; a source with `id = "mail"` leaves `graph_mail` intact; a project-path source | no |
| I3 | Bring-back headings print the home path; the header over-promises | `scripts/install.sh:887`, `:889` print the expanded `$SETUP_DIR`; `:884` | Print the heading paths with `~`. Reword the header: the whole file is private, never for the public form, and sections 2 and 3 name real folders. One prompt clause (`README.md:160-162`): in `fix-request.md`, use the report's placeholders or roles, not real names, unless the name is the bug. Name `bring-back.md` in the private route (`docs/deploy/setup-feedback.md:63`). Rejected: redacting sections 2 and 3 (it would corrupt the patch and hide over-redaction bugs such as I1) | one assertion that the home path is absent from both headings | no |
| I4 | A line after `end | finished` is folded into the closed attempt | `setup_report.py:785-848` (`:842-843`); `:998-1021` | (a) `stopping_error` (`:1005`) and `_unresolved_error` (`:988`) consider only events before the attempt's `finished` event. (b) Optional: start a header-less attempt on a post-finish event only when a later `Attempt:` header follows it; that needs the `:2415` wording changed. Rejected: the unguarded split, which turns a late line in the same session into a phantom latest attempt, and having `--log` write a header. Land with I5: alone, attempt 1 would still read "failed at step 2" | `tests/test_setup_report.py`: an event dated after `end | finished` does not change the closed attempt's outcome; with (b), a trailing post-finish line does not become the latest attempt | no |
| I5 | A late step-2 `error` line fails a run that exited 0 | `setup_report.py:1012-1018` | For a v7 attempt, any exit-0 install run of the attempt resolves a step-2 error, not only one started after the error's log time. Leave v5 and v6 alone. Update the docstring (`:999-1003`) and `docs/deploy/setup-feedback.md:135-137`, keeping the phrases `tests/test_deploy_pack.py:1544-1545` pins. Rejected: keeping the rule and rewording the prompt only | one v7 test with this report's shape: exit-0 install run, then `step 2 | error`, then a `step 3` line at the same timestamp; no "failed at step 2" | no |
| I6 | The agent-written `_eval` folder fails the permissions check, and `sync` never says so | nothing in a cycle tightens agent-written trees; `cycle.py:723`; `cli.py:509-513`; `ops/doctor.py:1256-1312` | Lead ruling: one fix, in the sync cycle. Before it commits, the cycle clears group/other bits on the docs-repo trees agents write (`_eval/`, `topics/`, top-level files), next to `ensure_scaffold()` (`cycle.py:723`), skipping symlinks. A sync ran after the draft, so this would have cleared the FAIL before the report. Rejected: making `sync` run the status checks and pass FAILs into its NEXT; status stays the one checker, and sync prints no `[FAIL]` line for NEXT to point at. Also rejected: an `_eval` mkdir in the scaffold or an owner-only sentence in the skill, which only changes which path the FAIL names | a 0755 `_eval/` holding a 0644 file is owner-only after one cycle, `docs_repo.permissions` is ok, and `sync` and `status` give the same NEXT (model: `tests/test_review_fixes.py:563-569`) | no |
| I7 | The folder question has no branch for "nobody answered" | `README.md:186-188`, `:164-165` | Lead ruling: one sentence at the end of step 1. If you cannot ask me, do not choose folders for me: log a deviation, tell me the folder question is waiting, stop and wait. Reason: what gets synced is tenant data scope, and that is the person's call. Rejected: a sanctioned default ("the project folders"), a judgment that changed between the two attempts. Keep the pinned phrase "ask which to sync". Check first what outcome the report computes for an attempt that stops at step 1 with no install run (unknown) | one assertion in `tests/test_deploy_pack.py` | no |
| I8 | A Mac that already runs agentsync | `README.md:192-194`; `scripts/install.sh:1536`, `:1552` | Lead ruling: one sentence in prompt step 2, and `install.sh` says "sync:" instead of "first sync:" when the config already existed. Sentence (skeptic's wording): "If this Mac already runs agentsync, its sources, history and any background sync are kept, and [warn] or WAITING ON YOU: lines about them (queued purges, a LaunchAgent that differs) may predate this session: show them to me and do not run their commands." Not "change nothing": NEXT may still ask for a fix. Rejected: prompt wording alone with the label left as is. The skeptic counted up to 10 pinned test lines for the label and noted `config note=add-source` cannot tell "added" from "already there", so key the label on the config state (`:1373`), not that note | `tests/test_deploy_pack.py::test_readme_prompt_carries_the_field_lines` (`:850`) asserts the sentence; one installer test for the label on an existing config | no |
| I9 | Launchd jobs that already exist | `scripts/install.sh:1582-1584` logs only `skipped (not-requested)` | Lead ruling: when the installer skips the agent step and the poll plist exists, it says "background sync is already installed (run agentsync install-agent to refresh it)". No prompt change. Test only that the plist file exists; calling `launchctl` breaks `tests/test_launcher.py:771` and `tests/test_install_next_line.py:175`. Keep the `not-requested` note (pinned). Rejected: prompt wording, and rewording the doctor drift line (three claims proposed three wordings of it) | one case in `tests/test_install_next_line.py`: plist pre-created in the temp HOME, the line is printed, no `launchctl` call | no |
| I10 | Empty cloud folders: nothing tells the operator what to do (fix request 3) | `ops/doctor.py:1230`; `loop.py:323-328`; `arm_local.py:829-835` | Lead ruling: the sync alarm names the folders and prints a ready-to-paste `exclude = [...]` line; the WAITING text and the doctor fix carry no folder name and point at `agentsync sync -v`. Details: the doctor fix branches on source kind and stops claiming another sync clears it, keeping a parseable command before the first `;`. The alarm reports per reason, lists every folder (not the first five), gives access advice only for permission errors, and escapes glob characters (`_dir_excluded`, `arm_local.py:304-314`, matches a glob). Rejected: a per-source `allow_empty` key (new surface; `exclude` already does the job). Why the names stay in the alarm: the wait line's own rule is "counts and source ids, never a mirror path or file name" (`loop.py:20-21`), and the wait line is embedded in the report's Summary. In either text I2 item 2 lands first or with it | update `tests/test_ops_doctor.py:692` and add a Graph-kind case; update `tests/test_loop.py:270`; an alarm test with six empty folders, one with `[` in its name | no |
| I11 | Five WARNING lines per poll for the same empty folders | `arm_local.py:474-476` | Demote the per-directory line from warning to info. The pass alarm (`:827-836`) still reports the folders once per pass, and `-v` still shows the detail. No test pins the message. Optional and separate: collapse identical lines in Recent errors (`setup_report.py:2338`); UTC log times; the sibling permission-error line at `:467` has the same shape | none pinned today; one assertion that the default log level carries no per-directory line | no |
| I12 | Sentinel warn on a source the installer added | `ops/doctor.py:634-642` | Demote to ok with the text "no sentinel configured (optional: an empty or unlistable cloud folder already holds deletions)". Rejected: marking it expected in the report only (status would still print the warn and a hand-edit fix), and auto-picking a sentinel (rejected in K14). Note: the new text describes the same hold I10 is about | change the assertion at `tests/test_ops_doctor.py:396` to expect ok | no |
| I13 | AGENTS.md names another project's inbox (fix request 2) | `publish.py:665-669` | Prefer the live inbox source whose path is the generic `inbox` folder beside the docs repo, in canonical form; else fall back to first by id. Update the docstring (`:663-664`) and `docs/plans/kiss-simplification.md:127`, which states the old rule. Rejected: listing every inbox path | a two-inbox test beside `tests/test_publish.py:1268` with made-up ids and paths | no |
| I14 | Two folders print the summary twice (fix request 4) | `cli.py:516-519`, `:611`; `scripts/install.sh:1377-1381` | Give the count a verb: "N scaffold file(s) written", or "scaffold up to date" when none. If the block must also print once, the installer keeps the `docs repo` line from the first call and the `sources:` line from the last, preserving exit status and the other lines. Rejected: a multi-path `add-source` or a quiet flag (new surface) | one test for both wordings; a filter test needs the stub at `tests/test_install_oneshot.py:88` extended | no |
| I15 | `network.proxy` printed twice | `cli.py:1031-1032` | Guard the warning list with `if not proxy.policy_error`. Leave the live-Graph ERROR plus WARN pair alone (pinned at `tests/test_review_fixes.py:1002-1003`) | `assert len(lines) == 1` after `tests/test_review_fixes.py:990` | no |
| I16 | An `[ok]` line tells the reader to grant Full Disk Access | `ops/doctor.py:694-699` | Drop the instruction from the ok line and use a pointer that holds in every branch: "... needs its own (the launcher and tcc.* lines below report it)". In the same commit, give the disclaim-unavailable warn its own fix (`:985-993`), or that case loses its only instruction | `assert "grant Full Disk Access" not in r["tcc"].detail` at `tests/test_ops_doctor.py:398` | no |
| I17 | `queue 1265` has no unit | `loop.py:479` | Rename the label to `curation queue`, the phrase the rule 9 NEXT line already uses. Update the docstring (`:449`), `docs/design/CONTRACTS.md:5648` and `tests/test_loop.py:461` | the updated pin | no |
| I18 | "baseline" means two things | `loop.py:476`; `setup_report.py:406` parses that line | Rename the loop-line label only (for example `eval`, matching `_eval/`), with the docstring, the parser regex and the pins (`tests/test_cli.py:1366`, `tests/test_loop.py:461`, `tests/test_setup_report.py:1482`, `CONTRACTS.md:5647`). Optional: drop "(status: baseline complete)" from the Summary line (`setup_report.py:2569`). Rejected: renaming the per-source sense, which changes the INDEX and STATE.md text answering agents read. Same function and pin as I17: one commit | the updated pins | no |
| I19 | Operator-only `fix:` lines in installer output; the count spans every run | `setup_report.py:1966-1973`, `:1987-1988`; `ops/doctor.py:1138-1147` | Say in the sentence that the count spans all runs in the tail. Optional: under the installer's existing no-hint mode, reword the launchd drift fix as a note with no `fix:` token and add it to `_DOCTOR_NOTES` (`setup_report.py:1179-1182`). Rejected (skeptic refuted it): reusing `AGENTSYNC_AGENT_STEP_PENDING`, which also changes which checks are requirements | one doctor test beside `tests/test_ops_doctor.py:1053-1087` if the reword is taken | no |

Prompt version: I3, I7 and I8 change prompt wording only. The rule at `scripts/install.sh:112-116` bumps the
version only when the prompt starts to depend on new installer or agentsync behaviour, so none of them bumps it.
Several verdicts said a wording change "would be v8"; that reading is rejected. The cost of not bumping is that a
later report's "Prompt: v7" line cannot tell old wording from new.

Also found, no item: `README.md:54` promises that every sync keeps the drop folder, which no sync path does
(row 18). The doctor permission fix chmods six trees when one folder is loose, and its 500-entry sample can be
used up by `.git` and `mirror` before agent-written files are reached (row 9). A fix to a secret-scan false
positive is forward-only for unchanged files (row 26). Line references in the first triage's D1 have drifted
(`cycle.py:1555-1563` is now `:1595-1603`; `loop.py:243-244` is now `:245-247`).

### Fixes that share a function

These must be one change or land in a fixed order.

- `setup_report.py:1643-1649` (the redactor's registration loop): I1 and I2 item 3. A source with id `copilot`
  would redact the agent name again unless I2's guard knows I1's words.
- `stopping_error` and `parse_friction`: I4 and I5.
- `loop.status_line` (`loop.py:449-482`) and the pin at `tests/test_loop.py:461`: I17 and I18.
- `arm_local.py:470-475` and `:827-836`: I10 and I11. `loop.next_step` (`loop.py:323-328`): I10 only.
- `scripts/install.sh`: the add-source loop (`:1373-1392`) for I14 and I8's label; the agent-step skip
  (`:1582-1584`) for I9.
- The README prompt: step 1 (`:188`) for I7, step 2 (`:192-195`) for I8, `:160-162` for I3. Pins:
  `tests/test_deploy_pack.py:467-494` and `:850`.
- The launchd drift line (`ops/doctor.py:1090-1095`): only I19's optional reword touches it.

## The patch

Section 3 is one commit, "local changes kept before update", made by step 1's keep-local-work command. It adds
on-device OCR for images, scanned PDF pages and pictures inside decks and Word documents, PDF reviewer comments,
and three riders (a faster chart reader, shorter sidecar names, a scope filter on the work queue).

- It applies at `35c2b5f`, the commit attempt 1 installed, and its own 219 tests pass there (measured).
- The converter files it edits did not change on main since (`git diff --stat 35c2b5f main -- src/agentsync/convert`
  lists only `eml.py`, which the patch does not touch). Outside the converters main moved a great deal, and one
  `cli.py` hunk anchors on a function that no longer exists.
- It is being rebuilt, not merged, per `docs/plans/bring-back-ocr.md`. The review confirmed defects in every unit.
  The five that matter most:
  1. The OCR helper is written 0755 inside the folder the permissions check requires to be owner-only, so every
     install would end on a FAIL.
  2. `status` and `doctor` compile a binary: the read-only check builds the Swift helper.
  3. An image with no legible text becomes a page, which is curation work for ever.
  4. The file name is written into a cached body whose cache key has no name in it, so it is wrong for a second
     file with the same bytes.
  5. The one-time re-read never reaches the files OCR was added for: a scanned PDF is already quarantined, and
     the re-read selects only pages that converted.
- It adds a command (`agentsync ocr`) and three `[convert]` keys against the KISS freeze.
- Its tests carry real person, client and file names. They are re-typed with made-up names; the commit is never
  cherry-picked.
- The scope-filter rider bears on I10. Without it, rows queued before an `exclude` is added are still worked on
  an incomplete walk. The plan rebuilds it as its own item.

## Open operator decisions

These are recorded, not decided. No conviction is stated for either.

**O1 — May a cloud folder that is on the Mac and still empty ever count as empty?** (fix request 3, option c)

Today a zero-child directory in a cloud tree is "unknown, never empty", so the source stays incomplete and
deletions are held. The request: once the File Provider reports the directory as materialised (not dataless) and
it is still empty, treat it as empty after N consecutive passes.

- Risk: this is a data-safety rule. A folder that was never browsed can read as empty. If such a folder were
  taken as empty, its mirrored files would be tombstoned. Nothing measured whether a never-listed directory
  always carries the dataless flag; that needs a field measurement first.
- Cost of not doing it (measured in this report): one `exclude` line per empty folder, by hand; five such folders
  here. Until they are written, `<source-1>` holds deletions (906 passes so far) and its INDEX entry says negative
  answers are unreliable. An excluded folder is not mirrored if it later gains files.
- Options: (a) keep the rule and ship I10's text (the build default); (b) treat a materialised zero-child
  directory as empty, after the field measurement; it inverts `tests/test_arm_local.py:348-355`; (c) as (b), only
  after N passes, which adds persisted per-directory state.

**O2 — Are online-only images downloaded so they can be read by OCR?**

- Build default: no. Only images already on the Mac are read.
- Why it is the operator's: today no image is ever downloaded. Downloading a photo library is a bandwidth and
  disk choice. Cost of the default: an online-only screenshot or scan stays unread until something else brings it
  onto the Mac. No count of online-only images on the field Mac was measured.

Operator actions on the field Mac (not decisions):

- Delete line 22 of `~/agent-context/sources.toml` (row 10).
- Run `agentsync install-agent` once to refresh the two jobs (row 12). Expect them to be reloaded with one
  immediate run.
- Preview the 14 queued purges with `agentsync purge --queue --dry-run` before deciding (row 15). It writes
  nothing since `cace1d9`. `archive = true` stops future queueing only; the 14 stay queued.
- Write the five exclude lines for `<source-1>` once I10 prints them, or decide O1.

## Still wanted from the operator

- The `purge-enqueued` lines of `~/Library/Application Support/agentsync/governance/audit.jsonl` (reason,
  source id, time), before the queue is run. 14 purges beside 13 bridges may be renamed exports, not real
  deletions; the paste cannot tell.
- `_sync/QUARANTINE.tsv` aggregated by source id and reason only, no paths (row 26).
- Whether the folder named like the agent's product is only listed or also a configured source path (I1's limit).
- Which `ProgramArguments` differ in the two installed plists (row 12).
- The fourth `install.sh` run in `install.log`: its start line and whether it has an end line (row 35).
- Whether the source whose root sits inside another source's root is intended, and why the parent shows live 0
  (row 36).
- Two `agentsync sync -v` runs in a row: are the same 6 files converted both times (row 28)?
- For O1: on the five empty folders, whether a directory that was never opened in Finder carries the dataless
  flag.
