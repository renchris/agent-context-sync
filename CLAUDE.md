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
(`tests/test_deploy_pack.py` first, then the rest, after a check that the two parts collect the full set), ending
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
every worktree, fetches, rebases onto `origin/main`, runs `scripts/gate.sh`, waits out and checks trunk's CI, pushes
`HEAD:main` (never forced; a refused push is rebased and gated again), then reads the push's CI back as below. The
user-level allow rule `Bash(scripts/ship-land.sh:*)` runs it with no prompt; a bare `git push` still stops at the
operator's `Bash(git push:*)` ask. Exit codes are in its header.

### CI is part of the gate

A green local gate does not prove CI. The macOS runners have System Integrity Protection off and the Linux
image carries its own tool versions, and both workflows were red for their first 30 runs while every local
gate passed. So a push to `main` is not done until its runs are read back:

```sh
for id in $(gh run list --commit "$(git rev-parse HEAD)" --json databaseId --jq '.[].databaseId'); do
  gh run watch "$id" --exit-status || echo "CI RED: run $id"
done
```

The list can be empty for a few seconds after the push; retry. `ci` takes about 6 minutes.

Before starting work, check trunk: `gh run list --branch main --limit 5`. A red run on `main` is fixed
before unrelated work lands on top of it. Read the failure with `gh run view <id> --log-failed`.

### Escalation surfaces

Stop and ask before: rewriting published history, installing a LaunchAgent, or committing anything that
names the tenant (use Contoso in examples).
