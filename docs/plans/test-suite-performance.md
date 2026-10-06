---
status: open
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

- **Parallel, `-n auto`, default distribution** (commit b971083). It passed first time and four times in a row
  under heavy load, with no order or shared-state failure, because `_isolate_home` already gives every test its
  own HOME and tmp tree. `--dist worksteal` was not measurably different at this noise level, so the default
  stays. `-n auto` sits in the gate command, not in `addopts`, so a single-test debug run stays serial.
- **The local gate stays the full suite.** At about 2 minutes the full run is cheap enough that a touched-file
  gate would save little and would let `main` go red between push and CI. Work item 4's alternative is dropped.
