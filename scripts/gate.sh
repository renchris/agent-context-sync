#!/bin/bash
# The CLAUDE.md "Gate" list as one script.
#   scripts/gate.sh             the land gate: every step, the full test set; never reads the journal
#   scripts/gate.sh --lane      a lane's full run; resumes a killed run of the same tree from its journal
#   scripts/gate.sh --affected  a lane pre-gate: every static step, then the tests the change can turn red
# Each run appends one line to <git-common-dir>/gate-verdicts.log. Nothing is skipped because a tree passed
# before: the journal only resumes a run that was killed part way. In every mode a pytest part that ends 1 with 1 to
# 20 failed tests re-runs exactly those once, alone (-n 0), and is red only when the re-run is: the machine's load is
# by design and a failure under it is "unknown, retry" (operator ruling 2026-10-20; B13 round 33 C800). The land gate
# reads only the journal it wrote in this run, and the verdict line counts the tests so passed (rerun_passed=).
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
tree='?' salt='?' skipped_timing=0 resumed=0 rerun_passed=0

finish() {
  local rc=$?
  printf '%s mode=%s tree=%s salt=%s rc=%s skipped_timing=%s resumed=%s rerun_passed=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" "$tree" "$salt" "$rc" "$skipped_timing" "$resumed" "${rerun_passed:-0}" \
    >>"$common/gate-verdicts.log"
  # A still-queued acquire leaves the queue; a slot is released only when this gate owns it, never a parent's
  [ -z "${gate_slot_pid:-}" ] || kill -TERM "$gate_slot_pid" 2>/dev/null || true
  [ "${gate_slot_owned:-0}" != 1 ] || [ -z "${gate_slot_token:-}" ] ||
    "${gate_slot_bin:-false}" release --token "$gate_slot_token" >/dev/null 2>&1 || true
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
# node_modules is gitignored, so a fresh worktree has none, and diagrams:check cannot start without it. Only when it
# is missing: npm ci deletes the folder first, and a lock-free --affected gate may be reading it (B13 round 33 C770)
[ -d node_modules ] || step npm ci
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

# A part a signal stopped never reported on its tests: it says so, with no verdict on them, and the gate still exits
# with the part's status, so the land stays refused. A status no signal names says nothing here and stays red. A
# SIGKILL can come from the diff itself (a test that kills its own process group, memory growth), so a stop is never
# called green or not red (B13 round 33 C769).
part_stopped() {
  local name
  name=$(kill -l "$(($2 - 128))" 2>/dev/null) || return 0
  echo "== $1 stopped by SIG${name#SIG} (rc $2) before it reported a verdict"
}

# A part's failed tests, re-run once, alone, on the same tree (B13 round 33 C800). The machine runs at load 600 to
# 1000 on 10 cores by design (operator ruling 2026-10-20: load never falls, no gate may wait for it, and a timeout
# we impose is "unknown, retry", never a negative verdict). From 2026-11-01 every land gate went red on 1 to 9 tests
# that each passed alone, a different set each run, none in the diff: subprocess timeouts and lost workers, which
# conftest's load skip does not cover. When pytest ends a part 1 and its journal names 1 to 20 failed tests (call,
# setup and teardown failures, and the test a lost worker was running), they run once more with -n 0, no workers of
# our own competing, into a journal of their own. That run green makes the part green; a test that fails alone
# convicts, and more than 20, another exit code or a failed set that cannot be read leave the part red as before.
# A re-run a signal stopped convicts nothing: it says so through part_stopped and returns its own status, so the gate
# exits with it; a re-run status of 1 to 128 stays plain red (B13 round 34 C877).
rerun_alone() {
  local part=$1 journal=$2 again=$3 out=$4 n rc=0
  local ids=()
  if ! journal_cli failed "$journal" >"$tmp/$part.failed"; then
    echo "== $part: its failed tests cannot be read from its journal; not re-run"
    return 1
  fi
  n=$(LC_ALL=C grep -c . "$tmp/$part.failed" || true)
  if [ "$n" -lt 1 ] || [ "$n" -gt 20 ]; then
    echo "== $part: $n failed; only 1 to 20 are re-run"
    return 1
  fi
  IFS=$'\n' read -r -d '' -a ids <"$tmp/$part.failed" || true
  : >"$again"
  echo "== $part: $n failed; re-running them once, alone"
  step env PYTHONUNBUFFERED=1 "GATE_JOURNAL=$again" uv run --locked pytest -q -p no:cacheprovider -p _gate_journal \
    -n 0 "${ids[@]}" 2>&1 | tee -a "$out" || rc=$?
  if [ "$rc" != 0 ]; then
    [ "$rc" -le 128 ] || { part_stopped "$part" "$rc"; return "$rc"; }
    echo "== $part: the re-run alone is red too (rc $rc)"
    return 1
  fi
  skipped_timing=$((skipped_timing + $(journal_cli skipped-timing "$again")))
  rerun_passed=$((rerun_passed + n))
  echo "== $part: $n failed under load and passed alone: $(tr '\n' ' ' <"$tmp/$part.failed" | sed 's/ $//')"
}

# One pytest part, run into a journal: the land gate's in $tmp, so it never reads one from an earlier run and never
# resumes. A part pytest ends 1 re-runs its failed tests alone (rerun_alone). Its output goes to $out.
part_once() {
  local part=$1 journal=$2 out=$3 rc=0
  shift 3
  : >"$journal"
  step env "GATE_JOURNAL=$journal" uv run --locked pytest -q -p no:cacheprovider -p _gate_journal "$@" |
    tee "$out" || rc=$?
  [ "$rc" -le 128 ] || part_stopped "$part" "$rc"
  if [ "$rc" = 1 ]; then
    rc=0
    rerun_alone "$part" "$journal" "$journal.rerun" "$out" || rc=$?
  fi
  [ "$rc" = 0 ] || return "$rc"
  skipped_timing=$((skipped_timing + $(journal_cli skipped-timing "$journal")))
}

# One pytest part. In the lane modes it journals to <tree>-<salt>.<part>.jsonl; a journal with no summary
# line whose holder (the gate.sh that wrote it) is gone was killed, and its passed tests are deselected.
# Its output, stderr too, goes to <tree>-<salt>.<part>.out beside the journal, appended on a resume so a
# killed run's text stays; a green part removes it, so a failed or killed part keeps its whole log, which
# the journal's cut tails do not (B13 round 17 U106). Journals are never removed. A re-run of the part's failed
# tests (rerun_alone) journals to <tree>-<salt>.<part>.rerun.jsonl, never into the part's, so a resume stays exact,
# and appends to the .out; a green re-run removes the .out as a green part does.
run_part() {
  local part=$1 rc=0
  shift
  if [ "$mode" = land ]; then
    part_once "$part" "$tmp/$part.jsonl" "$tmp/$part.out" "$@"
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
    echo "== gate $pid is running this tree: no shared journal for this run"
    part_once "$part" "$tmp/$part.jsonl" "$tmp/$part.out" "$@"
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
  # pipefail: rc is pytest's; unbuffered, so a SIGKILL leaves no tail in Python's buffer
  step env PYTHONUNBUFFERED=1 "GATE_DESELECT=$deselect" "GATE_JOURNAL=$journal" uv run --locked pytest -q \
    -p no:cacheprovider -p _gate_journal "$@" 2>&1 | tee -a "$out" || rc=$?
  [ "$rc" -le 128 ] || part_stopped "$part" "$rc"
  skipped_timing=$((skipped_timing + $(journal_cli skipped-timing "$journal")))
  if [ "$rc" = 5 ] && [ -n "$deselect" ]; then rc=0; fi # every test had passed already
  if [ "$rc" = 1 ]; then
    rc=0
    rerun_alone "$part" "$journal" "$dir/$tree-$salt.$part.rerun.jsonl" "$out" || rc=$?
  fi
  [ "$rc" != 0 ] || rm -f "$out"
  return "$rc"
}

# A part's fixed width, cut to the machine slot's grant when the grant is smaller.
gate_n() {
  if [ -n "${gate_slot_grant:-}" ] && [ "$gate_slot_grant" -lt "$1" ]; then echo "$gate_slot_grant"; else echo "$1"; fi
}

full_set() {
  union_check
  run_part scan -n "$(gate_n 2)" "${SCAN[@]}"
  run_part deploy_pack -n "$(gate_n 4)" tests/test_deploy_pack.py
  run_part rest -n auto --ignore=tests/test_deploy_pack.py "${SCAN_IGNORE[@]}"
}

# The machine-wide test-gate cap: one slot from cc-gate-slot sizes every pytest part's width, the `-n auto` ones
# through PYTEST_XDIST_AUTO_NUM_WORKERS. Taken after every static step, so a queued gate has done the cheap checks.
# A background job and `wait`, not `$( )`, which would hold a TERM until the grant; finish() then TERMs the queued
# acquire. A missing or faulty helper leaves the gate uncapped, as before: it must never refuse every gate.
gate_slot_bin=${CC_GATE_SLOT_BIN:-}
[ -n "$gate_slot_bin" ] || gate_slot_bin=$(command -v cc-gate-slot || true)
[ -n "$gate_slot_bin" ] || [ ! -x "$HOME/.claude/bin/cc-gate-slot" ] || gate_slot_bin=$HOME/.claude/bin/cc-gate-slot
gate_slot_grant=''
if [ -z "$gate_slot_bin" ]; then
  echo "== gate slot: helper absent, uncapped"
else
  "$gate_slot_bin" acquire --holder-pid $$ --want "${CC_GATE_SLOT_WANT:-4}" --min 2 \
    --class "$(if [ "$mode" = land ]; then echo land; else echo normal; fi)" \
    --label "acs gate $mode $(basename "$PWD")" >"$tmp/gate-slot" &
  gate_slot_pid=$!
  gate_slot_rc=0
  wait "$gate_slot_pid" || gate_slot_rc=$?
  gate_slot_pid=
  case $gate_slot_rc in
    0)
      gate_slot_out=$(sed -n 1p "$tmp/gate-slot")
      # token= is last and holds spaces (the holder's start time), so it is everything after " token="
      gate_slot_token=$(printf '%s\n' "$gate_slot_out" | sed -n 's/^.* token=//p')
      gate_slot_verdict=$(printf ' %s\n' "$gate_slot_out" | sed -n -e 's/ token=.*//' -e 's/^.* verdict=\([^ ]*\).*/\1/p')
      gate_slot_grant=$(printf ' %s\n' "$gate_slot_out" | sed -n -e 's/ token=.*//' -e 's/^.* grant=\([^ ]*\).*/\1/p')
      gate_slot_owned=$(printf ' %s\n' "$gate_slot_out" | sed -n -e 's/ token=.*//' -e 's/^.* owned=\([^ ]*\).*/\1/p')
      # Only a granted or nested width is acted on; off, fail-open, in-test or a malformed grant run as before
      case $gate_slot_verdict:$gate_slot_grant in
        granted:* | nested:*) ;;
        *) gate_slot_grant='' ;;
      esac
      case $gate_slot_grant in
        '' | 0* | *[!0-9]*) gate_slot_grant='' ;;
        *) export PYTEST_XDIST_AUTO_NUM_WORKERS="$gate_slot_grant" ;;
      esac
      [ "$gate_slot_owned" != 1 ] || export CC_GATE_SLOT_TOKEN="$gate_slot_token"
      echo "== gate slot: $gate_slot_out"
      ;;
    129 | 130 | 137 | 143) exit "$gate_slot_rc" ;; # killed while queued: nothing has run
    *) echo "== gate slot: helper error rc=$gate_slot_rc, uncapped" ;;
  esac
fi

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
