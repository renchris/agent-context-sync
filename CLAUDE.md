# agent-context-sync

## Session Close

Trunk is `main`. Landing costs nothing; `/ship` lands green work.

### Gate

Run these before a push. They are the CI steps, copied from `.github/workflows/ci.yml` and
`.github/workflows/diagrams.yml`; when a workflow step changes, change this list in the same commit.

```sh
uv sync --locked
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked mypy src
uv run --locked pytest -q -p no:cacheprovider -n auto
uv run --locked --only-group lint shellcheck probes/*.sh scripts/*.sh launcher/*.sh docs/design/receipts/review/scripts/*.sh
uvx ruff@0.15.9 check --isolated scripts
for f in scripts/*.mjs; do node --check "$f"; done
npm run diagrams:check
make -C probes CFLAGS='-O2 -Wall -Wextra -Werror' && make -C probes check
```

`.github/workflows/download-hosts.yml` is not in this list: it follows the installer's live download redirects
weekly, so the network, not a commit, decides its result.

`scripts/gate.sh` runs exactly this list and is the land gate: the same checks and the same full test set
(the whole-tree scanners `tests/test_contracts.py` and `tests/test_lints.py` first, so a red one stops it in seconds,
then `tests/test_deploy_pack.py`, then the rest, after a check that the three parts collect the full set, each test
once), ending
`GATE GREEN at <sha>`. It never reads the journal below. Lanes pre-gate with `scripts/gate.sh --affected`: every
static step, then the test files the diff from `origin/main` can turn red (the files it touches, their direct
importers, the readers of a touched doc, and the whole-tree scanners); a change to `tests/conftest.py`,
`pyproject.toml`, `uv.lock` or an unmapped path runs the full set. `scripts/gate.sh --lane` is a lane's full run.
The two lane modes journal each test to `<git-common-dir>/gate-journal/`, and a run killed part way resumes on the
same tree without the tests that passed. Every run appends its verdict to `<git-common-dir>/gate-verdicts.log`.

pytest runs one worker per core (`-n auto`, pytest-xdist): about 2 minutes on a busy 10-core machine against 11
serial. Every
test keeps its own HOME and tmp tree (`tests/conftest.py::_isolate_home`), which is what makes that safe; a new
test that writes outside `tmp_path` breaks it. To debug one test, drop `-n auto`.

Run tools through `uv run --locked`, not from PATH. shellcheck is pinned in `uv.lock` (the `lint` group)
because the versions disagree: 0.9.0 reports SC2015 where 0.11.0 does not.

### Landing

Land with `scripts/ship-land.sh`, typed exactly so (no env prefix, no `bash` in front). It takes one lock shared by
every worktree, fetches, reads trunk's CI once without waiting and stops there (exit 6, no gate run) when a run on
`origin/main` has already ended failure, timed_out or startup_failure, rebases onto `origin/main`, runs
`scripts/gate.sh`, waits out and checks trunk's CI, pushes `HEAD:main` (never forced; a refused push is rebased and
gated again), then reads the push's CI back as below. The user-level allow rule `Bash(scripts/ship-land.sh:*)` runs
it with no prompt; a bare `git push` still stops at the operator's `Bash(git push:*)` ask. Exit codes are in its
header. Exit 9 is CI it could not read (gh failed, no run appeared, or a run lost its hosted runner): before the
push it says `nothing pushed`; after it the line says `LANDED`, and the runs are read back by hand with the loop
below. A run that lost its hosted runner (a failed job with no failed step and a step left unfinished, which both
of ship-land's trunk reads tell from a red run) is rerun, `gh run rerun <id> --failed`, and the land run again once
the rerun is green: it is never landed past, so neither `--allow-red-trunk` nor `--no-ci` is for it.
`--allow-red-trunk` is only for a trunk run that ended red on tests (`gh run view <id> --log-failed`) the diff does
not touch and the lane does not own: record the run id in the commit body or the lane's note, and message the
tests' owner.

### CI is part of the gate

A green local gate does not prove CI. The macOS runners have System Integrity Protection off and the Linux
image carries its own tool versions, and both workflows were red for their first 30 runs while every local
gate passed. So a push to `main` is not done until its runs are read back:

```sh
for id in $(gh run list --commit "$(git rev-parse HEAD)" --json databaseId --jq '.[].databaseId'); do
  gh run watch "$id" --exit-status || echo "CI RED: run $id"
done
```

The list can be empty for a few seconds after the push; retry. `ci` takes about 18 to 60 minutes once its
job starts (measured October 2026; 24 at 5120 tests, and the same tests and source ran 24 and then 21 minutes on
two runs; the eight jobs from eaed1034 to fcafd734 took 18:05 to 28:34; the nine from fcafd734 to 7a54f938 took
19:29 to 31:21; the thirteen from ed901cd3 to b1f4a087 took 24:34 to 32:18; the ten from c93fcd96 to 3ec66a92
took 30:06 to 36:24, 5685 tests on the slowest and pytest's step 35:46; the nine from f04c61db to d1f72d6f took
26:53 to 40:01 but one, 49:59 on d1f72d6f, a docs-only commit, 6050 tests and pytest's step 49:26; the seven after
it, from 71388552 to b2724d9c, took 30:19 to 44:27, the slowest on 3f1dee54, 6191 tests and pytest's step 43:58;
the nine after those, from 673a72a0 to fa32c452, took 35:36 to 51:10, six past 45, the slowest on b2b88e5d, 6345
tests and pytest's step 50:28; the sixteen after those, from ccf696b7 to c8a4298b, took 37:56 to 59:43, four past
50, the slowest on 2919a7b9, 6468 tests and pytest's step 59:08, and one attempt lost its runner and its rerun was
green; the twelve after those, from 7f132615 to 357f6d5b, took 43:35 to 57:05, all green on attempt 1, the slowest
on 76dc4e8f, 6631 tests and pytest's step 56:38; one run took 34 on a runner loaded 5 to 8 per core), plus any wait
for a macOS runner, counted from the run appearing to its job starting (one waited 63 minutes, on ef12f28f, another
50, and earlier ones 48 and 46); the job's limit is 80 minutes (`timeout-minutes` in ci.yml), and a wait does not
count against it.

Before starting work, check trunk: `gh run list --branch main --limit 5`. A red run on `main` is fixed
before unrelated work lands on top of it. Read the failure with `gh run view <id> --log-failed`.

### Escalation surfaces

Stop and ask before: rewriting published history, installing a LaunchAgent, or committing anything that
names the tenant (use Contoso in examples).
