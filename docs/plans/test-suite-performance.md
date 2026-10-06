---
status: done
---

# Plan: the fastest test suite that still earns its place

Scope (frozen, 2026-10-06): make the pre-push gate in CLAUDE.md as fast as it can be while it still catches what
it exists to catch, by parallelising, pruning or removing tests on measured evidence; keep CI and CLAUDE.md's gate
list in step.

## Phase 0: Agent Team Orchestration

- Execution locus: **S**, this dispatched session (fired from the KISS lead, pane 137). It may use read-only
  subagents or Workflow slots for the audit; one writer (this session) for code.
- Lead budget: stay under 50% context; recycle at a natural seam if past 35% while waiting on a gate.

## Facts measured before this plan (2026-10-05/06, KISS session)

- `uv run --locked pytest -q -p no:cacheprovider`: 2089 passed, 2 skipped; ~10 min on an idle machine,
  25-30 min at load average ~25 (10 cores). Serial: pyproject has no pytest-xdist; `addopts = "-ra"`.
- 1504 `def test_` functions across tests/. Many build real git repos in tmp and run install.sh under bash 3.2.
- Over 8 full runs this session the full suite caught nothing the touched files' own tests missed; the only red
  was a runtime-budget flake under load (`test_setup_report.py::test_headings_redaction_and_runtime`).
- CI (`.github/workflows/ci.yml`, ~13 min, macOS and Linux) runs the same suite after every push, and
  CLAUDE.md says a push is not done until CI is read back. So the local full run mostly duplicates CI; what it
  buys is that `main` is never red between push and CI.
- Isolation: `tests/conftest.py::_isolate_home` (own MonkeyPatch.context) keeps HOME and CLAUDE_CONFIG_DIR in
  tmp; a past leak wrote `~/.claude/skills/agentsync-docs`, so check it never exists after a run.

## Work

1. Measure: `pytest --durations=50` and per-file wall time on this machine; record in this plan.
2. Parallelise: add pytest-xdist (dev group, locked), try `-n auto`; fix any order or shared-state failures at
   their cause; run the full suite at least 3 times in parallel green before adopting it.
3. Prune: apply the test-audit skill to the slowest files; delete tests that re-assert source, duplicate a
   contract, or prove only their own stub. Each deletion names what still covers that behaviour.
4. Gate shape: decide, with numbers, the local pre-push gate (full parallel suite vs. touched-file tests with CI
   as the full proof). Update CLAUDE.md's gate list and `.github/workflows/ci.yml` in the same commit.
5. Removing the suite entirely, or more than ~25% of tests, is the operator's call: write the evidence here and
   file a decision (`cc-decide open --class C`) instead of doing it.

## Measurements (2026-10-06, this session; 10-core Apple Silicon)

The machine was never idle: other sessions held the load average at 20 or more throughout, and this session's
own audit agents pushed it past 90 during the parallel runs. So every figure below is a loaded figure, and the
ratio matters more than the absolute.

| Run | pytest wall | Load average at start | Result |
|---|---|---|---|
| Serial (the old gate) | 661 s | 21 | 2089 passed, 2 skipped |
| `-n auto`, run 1 | 137 s | 37 | 2089 passed, 2 skipped |
| `-n auto --dist worksteal` | 114 s | 130 | same |
| `-n auto --dist load` (the default) | 118 s | 119 | same |
| `-n auto --dist worksteal` | 139 s | 93 | same |

The other nine gate steps together take 13 s (mypy 5.7, `make -C probes` 3.6, shellcheck 2.4, the rest under 1 s
each), before and after.

Where the serial 649 s of test time goes (from `--junitxml`, per file):

| File | Seconds | Tests | Share, cumulative |
|---|---|---|---|
| test_cli | 139 | 80 | 21% |
| test_install_oneshot | 81 | 85 | 34% |
| test_setup_report | 59 | 59 | 43% |
| test_review_fixes | 53 | 65 | 51% |
| test_governance | 49 | 32 | 59% |
| test_e2e | 49 | 22 | 66% |
| test_cycle | 45 | 22 | 73% |
| test_launcher | 29 | 49 | 78% |
| test_loop | 27 | 26 | 82% |
| test_publish | 24 | 64 | 85% |
| test_lints, test_gitops | 31 | 87 | 90% |
| the other 32 files | 63 | 1500 | 100% |

229 tests take 1 s or more and hold 474 s (73%); 1742 tests take under 0.5 s and hold 87 s. A profile of three
typical slow tests (`test_sync_twice_status_curate`, `test_git_log_holds_only_real_change`,
`test_f_delete_writes_a_tombstone`) shows the time is the product's own work: 13 real `run_cycle` calls made
265 `git` subprocesses (5.9 of 11 s) and 18 pandoc calls (1.5 s). There is no fixed per-test overhead to
remove; these tests are slow because they run the real sync against a real repo, which is the point of them.

## Decisions

- **Parallel, `-n auto`, default distribution** (commit 43551fd). It passed first time and four times in a row
  under heavy load, with no order or shared-state failure, because `_isolate_home` already gives every test its
  own HOME and tmp tree. `--dist worksteal` was not measurably different at this noise level, so the default
  stays. `-n auto` sits in the gate command, not in `addopts`, so a single-test debug run stays serial.
- **The local gate stays the full suite.** At about 2 minutes the full run is cheap enough that a touched-file
  gate would save little and would let `main` go red between push and CI. Work item 4's alternative is dropped.

## Audit of the slow files (2026-10-06)

A read-only Workflow ran one auditor per file over the 12 slowest files (90% of serial time) plus one
cross-file lane, each followed by a skeptic told to refute every delete, fold or speed-up (26 agents, the
test-audit skill's value bar restated for pytest). It returned 79 candidates. The skeptics refuted 30. Only a
candidate that both the auditor and the skeptic rated 90% or more was applied.

**Finding: the suite earns its place.** No file and no group of tests is junk. The slow tests are slow because
they run the real sync, the real `install.sh` under bash 3.2 and the real launcher, and almost every one is
the only test of a behaviour in `docs/design/CONTRACTS.md` or a named review finding. Nothing here approaches
the 25% line, so no operator decision was filed. 11 of 2093 tests went (0.5%), each folded into a test that runs
the same command, with its assertions moved, not dropped (commit 1496b2d):

| Removed | Its assertions now live in |
|---|---|
| test_publish `test_scaffold_upgrades_an_unedited_earlier_topics_seed` (patched the seed set, then proved the patch) | `test_the_k09b_topics_seed_upgrades_to_the_pointer_and_an_edited_one_is_kept`, against the real set; it gained the "not upgraded twice" assert |
| test_cli `test_curate_old_names_are_hidden_aliases`, 2 of 3 rows | the same test, one sync, a loop over the three aliases |
| test_loop `test_rule_5_the_operator_confirms_a_draft`, 2 of 3 rows | the same test, one sync, a loop over the three states |
| test_e2e `test_c_fixture_resaved_workbook_copied_over_is_free` | `test_c_noop_resave_docprops_is_touched_not_changed` (both files re-saved in one cycle, each row checked by name); the byte-level proof stays in test_convert_determinism `test_office_style_resave_is_free` |
| test_launcher `test_install_sh_network_failure_exits_1_with_a_next_line` | `test_install_sh_setup_log_records_the_failed_step` |
| test_launcher `test_install_sh_dry_run_writes_no_setup_log` | `test_install_sh_dry_run_changes_nothing`; the default-path case stays in `test_install_sh_dry_run_installs_uv_when_absent` |
| test_install_oneshot `test_the_shell_report_ends_with_the_issue_form_and_prints_it` | `test_failure_without_agentsync_writes_the_shell_report` |
| test_install_oneshot `test_a_failed_first_sync_shows_everything_it_printed` | `test_first_sync_failure_fails_the_run_before_the_agent` |
| test_install_oneshot `test_dry_run_and_report_only_write_no_install_out` | `test_report_only_keeps_an_earlier_friction_log` and `test_dry_run_writes_nothing` |

test_launcher's shellcheck test also stopped checking `install.sh`, which test_deploy_pack
`test_scripts_pass_shellcheck[install.sh]` already does; it still checks `build.sh` and both scripts under
bash 3.2.

Cheaper with the same proof: a session-scoped `docs_repo_template` in conftest (one `ensure_repo`, copied per
test) for test_publish and test_lints; test_governance's `all_blobs` asks git for every object type in one
process instead of one per object; three tests stopped building a repo or running a sync they never read; the
launcher's hung-child test waits 0.3 s. Serial time of test_governance, test_publish and test_lints went from
88.5 s to 42.8 s.

Four assertions were weaker than the test's name and were made real: `test_run_git_raises_git_error_with_argv_and_stderr`
never read stderr; `test_long_paths_are_blocking` never checked `blocking`; the sg10 hold test compared commit
counts as text; `test_outputs_rows_match_pages_after_the_whole_story` checked tombstone rows that its single
cycle never produced.

Watch items, not applied (below the 90% bar, refuted, or not worth the risk):

- `Publisher.check_deletions` (src/agentsync/publish.py) has no caller outside tests/test_publish.py; the cycle
  uses the classifier's breaker. Deleting its test means deleting the method and its CONTRACTS.md entry, which
  is a product change, not a test change.
- The three lock-wait tests in test_cli sleep 5 s in total on real timers. A skeptic showed that patching the
  sleep in all three would remove the only real-time run of the wait loop, so they stay.
- `_canary_env` in test_install_oneshot waits 2 s; 1 s was rated safe for two of its four users, but the other
  two were not examined and a shorter wait is the kind of change that flakes under parallel load.
- test_launcher `test_setup_report_reads_the_install_sh_log` could fold into
  `test_report_step_line_comes_before_the_end_line` (1.5 s); position-sensitive, left alone.
- Shared synced-repo fixtures for test_cli, test_e2e, test_loop and test_setup_report were refuted: the local
  arm keys items by inode and the manifest by absolute source path, so a copied tree reads as a mass delete.
- `convert/pandoc.py` `version()` ran 15 times in a 3-test profile (0.7 s); caching it would be a product
  change with a product benefit, outside this plan.

The full audit output is in the session's workflow journal (`wf_e5ca04b8-a9e`); everything acted on is above.

## Before and after (the DoD numbers)

Same machine, same night, load average never under 20.

| Full gate, all ten steps | Wall | Load average at start |
|---|---|---|
| Before: serial pytest (661 s) plus the other nine steps (13 s) | **674 s** | 21 |
| After, run 1 | **128 s** (pytest 122 s) | 45 |
| After, run 2 | **138 s** (pytest 135 s) | 91 |
| After, run 3 | **130 s** (pytest 126 s) | 85 |

About 5 times faster under heavier load than the baseline had. All seven parallel full-suite runs this session
were green (four before the audit edits, three after), and `~/.claude/skills/agentsync-docs` did not exist after
any pytest run.

CI, on the 3-core `macos-15` runner: the pytest step went from 10 min 55 s (run 37422459338, the commit before)
to 5 min 29 s (run 37427410960), and the whole `ci` run from 11 min 26 s to 6 min 18 s.

## DONE (2026-10-06)

Landed on `main` as 455c67a (43551fd parallel gate, 1496b2d test edits, d44fe78 and 455c67a this plan); `ci`
and `diagrams` both green for that sha. All five work items are closed: measured, parallelised, pruned on
evidence, gate shape decided (full suite, parallel), and no removal large enough to need the operator.
