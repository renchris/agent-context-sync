#!/bin/bash
# The CLAUDE.md "Gate" list as one script.
#   scripts/gate.sh             the land gate: every step, the full test set; never reads the journal
#   scripts/gate.sh --lane      a lane's full run; resumes a killed run of the same tree from its journal
#   scripts/gate.sh --affected  a lane pre-gate: every static step, then the tests the change can turn red
# Each run appends one line to <git-common-dir>/gate-verdicts.log. Nothing is skipped because a tree passed
# before: the journal only resumes a run that was killed part way.
set -euo pipefail

mode=land
case "${1:-}" in
  "") ;;
  --lane) mode=lane ;;
  --affected) mode=affected ;;
  *) echo "usage: scripts/gate.sh [--lane | --affected]" >&2; exit 2 ;;
esac

cd "$(git rev-parse --show-toplevel)"
common=$(cd "$(git rev-parse --git-common-dir)" && pwd)
tmp=$(mktemp -d)
tree='?' salt='?' skipped_timing=0 resumed=0

finish() {
  local rc=$?
  printf '%s mode=%s tree=%s salt=%s rc=%s skipped_timing=%s resumed=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" "$tree" "$salt" "$rc" "$skipped_timing" "$resumed" \
    >>"$common/gate-verdicts.log"
  [ -z "${holder:-}" ] || [ "$(cat "$holder" 2>/dev/null)" != "$$" ] || rm -f "$holder"
  rm -rf "$tmp"
}
trap finish EXIT
# A signal exits with its own status, so the verdict line is not rc=0: bash 3.2 enters the EXIT trap with $?=0
# when a SIGTERM kills it while a pipeline runs, and the log read a killed run as green (B13 round 18 V93)
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

step() {
  echo "== $*"
  "$@"
}

# The tree under test: the work tree with unstaged and untracked files, written through a copy of the
# index so the real one is never touched.
cp "$(git rev-parse --git-path index)" "$tmp/index"
GIT_INDEX_FILE="$tmp/index" git add -A
tree=$(GIT_INDEX_FILE="$tmp/index" git write-tree)

step uv sync --locked
salt=$({
  cat uv.lock
  uv run --locked python --version
  env | LC_ALL=C grep '^AGENTSYNC_' | LC_ALL=C sort || true
} | git hash-object --stdin | cut -c1-12)

step uv run --locked ruff check src tests
step uv run --locked ruff format --check src tests
step uv run --locked mypy src
step uv run --locked --only-group lint shellcheck probes/*.sh scripts/*.sh launcher/*.sh \
  docs/design/receipts/review/scripts/*.sh
step uvx ruff@0.15.9 check --isolated scripts
for f in scripts/*.mjs; do step node --check "$f"; done
step npm run diagrams:check
step make -C probes CFLAGS='-O2 -Wall -Wextra -Werror'
step make -C probes check

pytest_q() { uv run --locked pytest -q -p no:cacheprovider "$@"; }

# The land gate's count: the -ra summary's "SKIPPED [n] <where>: load N per core: ..." lines.
count_timing_skips() {
  local n
  n=$(sed -n 's/^SKIPPED \[\([0-9]*\)\] .*: load [0-9]* per core: a time bound cannot be judged$/\1/p' "$1" |
    awk '{ s += $1 } END { print s + 0 }')
  skipped_timing=$((skipped_timing + n))
}
journal_cli() { uv run --locked python tests/_gate_journal.py "$@"; }

# The whole-tree scanners that run first, so a red one stops the land gate in seconds, before the 20-odd
# minutes of the rest: all three red land gates of round 17 were tests/test_contracts.py (B13 round 18 V93).
# test_governance.py, about a minute alone, stays in the rest.
SCAN=(tests/test_contracts.py tests/test_lints.py)
SCAN_IGNORE=(--ignore=tests/test_contracts.py --ignore=tests/test_lints.py)

# Every test runs in exactly one of the three parts: the parts' nodeids must union to the full set's, and their
# counts add up to its count, so no test runs in two.
union_check() {
  local part pids=""
  for part in full scan pack rest; do
    case $part in
      full) set -- ;;
      scan) set -- "${SCAN[@]}" ;;
      pack) set -- tests/test_deploy_pack.py ;;
      rest) set -- --ignore=tests/test_deploy_pack.py "${SCAN_IGNORE[@]}" ;;
    esac
    { pytest_q --collect-only "$@" | LC_ALL=C grep '::' | LC_ALL=C sort >"$tmp/$part.ids"; } &
    pids="$pids $!"
  done
  for part in $pids; do wait "$part"; done
  LC_ALL=C sort -u "$tmp/scan.ids" "$tmp/pack.ids" "$tmp/rest.ids" >"$tmp/union.ids"
  local full scan pack rest
  full=$(wc -l <"$tmp/full.ids" | tr -d ' ')
  scan=$(wc -l <"$tmp/scan.ids" | tr -d ' ')
  pack=$(wc -l <"$tmp/pack.ids" | tr -d ' ')
  rest=$(wc -l <"$tmp/rest.ids" | tr -d ' ')
  echo "== collect: full $full, scan $scan, deploy_pack $pack, rest $rest"
  if ! cmp -s "$tmp/full.ids" "$tmp/union.ids" || [ $((scan + pack + rest)) != "$full" ]; then
    echo "GATE RED: scan + deploy_pack + rest does not collect the full set, each test once:" >&2
    diff "$tmp/full.ids" "$tmp/union.ids" | head -20 >&2
    exit 1
  fi
}

# One pytest part. In the lane modes it journals to <tree>-<salt>.<part>.jsonl; a journal with no summary
# line whose holder (the gate.sh that wrote it) is gone was killed, and its passed tests are deselected.
# Its output, stderr too, goes to <tree>-<salt>.<part>.out beside the journal, appended on a resume so a
# killed run's text stays; a green part removes it, so a failed or killed part keeps its whole log, which
# the journal's cut tails do not (B13 round 17 U106). Journals are never removed.
run_part() {
  local part=$1
  shift
  if [ "$mode" = land ]; then
    step pytest_q "$@" | tee "$tmp/$part.out"
    count_timing_skips "$tmp/$part.out"
    return
  fi
  local dir="$common/gate-journal" journal out
  journal="$dir/$tree-$salt.$part.jsonl"
  out="$dir/$tree-$salt.$part.out"
  holder="$dir/$tree-$salt.holder"
  mkdir -p "$dir"
  local pid
  pid=$(cat "$holder" 2>/dev/null || true)
  if [ -n "$pid" ] && [ "$pid" != "$$" ] && ps -o pid= -p "$pid" >/dev/null; then
    echo "== gate $pid is running this tree: no journal for this run"
    step pytest_q "$@" | tee "$tmp/$part.out"
    count_timing_skips "$tmp/$part.out"
    return
  fi
  echo "$$" >"$holder"
  local deselect=""
  if [ -s "$journal" ] && ! journal_cli finished "$journal"; then
    journal_cli passed "$journal" >"$tmp/$part.passed"
    local n
    n=$(LC_ALL=C grep -c . "$tmp/$part.passed" || true)
    resumed=$((resumed + n))
    echo "== resuming $part: $n tests passed in a killed run of this tree"
    deselect=$tmp/$part.passed
    echo "== resumed by $$" >>"$out"
  else
    : >"$journal"
    : >"$out"
  fi
  local rc=0
  # pipefail: rc is pytest's; unbuffered, so a SIGKILL leaves no tail in Python's buffer
  step env PYTHONUNBUFFERED=1 "GATE_DESELECT=$deselect" "GATE_JOURNAL=$journal" uv run --locked pytest -q \
    -p no:cacheprovider -p _gate_journal "$@" 2>&1 | tee -a "$out" || rc=$?
  skipped_timing=$((skipped_timing + $(journal_cli skipped-timing "$journal")))
  if [ "$rc" = 5 ] && [ -n "$deselect" ]; then rc=0; fi # every test had passed already
  [ "$rc" != 0 ] || rm -f "$out"
  return "$rc"
}

full_set() {
  union_check
  run_part scan -n 2 "${SCAN[@]}"
  run_part deploy_pack -n 4 tests/test_deploy_pack.py
  run_part rest -n auto --ignore=tests/test_deploy_pack.py "${SCAN_IGNORE[@]}"
}

if [ "$mode" = affected ]; then
  base=$(git merge-base origin/main HEAD)
  { git diff --name-only --no-renames "$base"; git ls-files --others --exclude-standard; } |
    LC_ALL=C sort -u >"$tmp/touched"
  journal_cli affected <"$tmp/touched" >"$tmp/selected"
  echo "== touched since $(git rev-parse --short "$base"): $(tr '\n' ' ' <"$tmp/touched")"
  if LC_ALL=C grep -q '^FULL ' "$tmp/selected"; then
    echo "== the full test set: $(cut -c6- "$tmp/selected")"
    full_set
  else
    echo "== selected $(wc -l <"$tmp/selected" | tr -d ' ') test files: $(tr '\n' ' ' <"$tmp/selected")"
    IFS=$'\n' read -r -d '' -a selected <"$tmp/selected" || true
    run_part affected -n auto "${selected[@]}"
  fi
else
  full_set
fi
echo "GATE GREEN at $(git rev-parse --short HEAD)"
