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
