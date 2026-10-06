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

pytest runs one worker per core (`-n auto`, pytest-xdist): about 2 minutes on a busy 10-core machine against 11
serial. Every
test keeps its own HOME and tmp tree (`tests/conftest.py::_isolate_home`), which is what makes that safe; a new
test that writes outside `tmp_path` breaks it. To debug one test, drop `-n auto`.

Run tools through `uv run --locked`, not from PATH. shellcheck is pinned in `uv.lock` (the `lint` group)
because the versions disagree: 0.9.0 reports SC2015 where 0.11.0 does not.

### CI is part of the gate

A green local gate does not prove CI. The macOS runners have System Integrity Protection off and the Linux
image carries its own tool versions, and both workflows were red for their first 30 runs while every local
gate passed. So a push to `main` is not done until its runs are read back:

```sh
for id in $(gh run list --commit "$(git rev-parse HEAD)" --json databaseId --jq '.[].databaseId'); do
  gh run watch "$id" --exit-status || echo "CI RED: run $id"
done
```

The list can be empty for a few seconds after the push; retry. `ci` takes about 10 minutes.

Before starting work, check trunk: `gh run list --branch main --limit 5`. A red run on `main` is fixed
before unrelated work lands on top of it. Read the failure with `gh run view <id> --log-failed`.

### Escalation surfaces

Stop and ask before: rewriting published history, installing a LaunchAgent, or committing anything that
names the tenant (use Contoso in examples).
