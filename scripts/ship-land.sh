#!/bin/bash
# Land HEAD on origin/main: the fetch, rebase, land gate, trunk check and push run under one lock shared by every
# worktree of this checkout, then the push's CI runs are read back.
#   scripts/ship-land.sh                    land, then wait for CI on the pushed sha
#   scripts/ship-land.sh --no-ci            skip every CI read (trunk's runs before the gate and after it, the read-back after the push)
#   scripts/ship-land.sh --allow-red-trunk  push even when origin/main's own CI is red (record the run id)
#   scripts/ship-land.sh --lock <path>      take this lock instead of <git-common-dir>/land.lock
# Run it exactly as `scripts/ship-land.sh`, with no env prefix and no `bash` in front: the user-level allow rule
# Bash(scripts/ship-land.sh:*) is what lets it run without a prompt, and the push it makes is a subprocess the
# Bash(git push:*) ask rule never sees. It never forces: a rejected push is fetched, rebased and gated again.
# Exit: 0 landed (or nothing to land) and CI green, 2 usage, 3 dirty tree, 4 rebase conflict (aborted), 5 gate red,
# 6 trunk CI red (before the gate when a run on origin/main has already ended red, else after it), 7 push refused, 8 CI red on the pushed sha, 9 CI not read (gh failed, no run appeared, or a run lost its hosted runner): before
# the push nothing is pushed, after it the line says LANDED and the runs are read back by hand. A run that lost its
# runner is rerun (gh run rerun <id> --failed) and waited for, never landed past, with or without --allow-red-trunk
# (B13 round 31 C569). 10 refused: holding a gate slot, before the lock is taken (nothing fetched or pushed).
set -euo pipefail

usage() {
  echo "usage: scripts/ship-land.sh [--no-ci] [--allow-red-trunk] [--lock <path>]" >&2
  exit 2
}

self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
args=("$@")
ci=1 allow_red=0 lock='' locked=0
while [ $# -gt 0 ]; do
  case $1 in
    --no-ci) ci=0 ;;
    --allow-red-trunk) allow_red=1 ;;
    --lock)
      [ $# -ge 2 ] || usage
      lock=$2
      shift
      ;;
    --locked) locked=1 ;; # internal: this process already holds the lock
    *) usage ;;
  esac
  shift
done

cd "$(git rev-parse --show-toplevel)"
[ -n "$lock" ] || lock="$(cd "$(git rev-parse --git-common-dir)" && pwd)/land.lock"

if [ -n "$(git status --porcelain)" ]; then
  echo "ship-land: the tree has uncommitted or untracked files; the gate would test what the push leaves out" >&2
  git status --short >&2
  exit 3
fi

# Waits for every run on a full sha. A cancelled run was superseded by a later push to main (ci cancels in-progress
# runs there): the newest run of that workflow on main stands in for it once it contains the sha.
# Three outcomes, never one read as another (B13 round 15 S59, S63): 0 every run green; 1 a run red, its id printed;
# 2 not read, the reason printed: gh failed (a TLS timeout under load), no run appeared, or a run ended neither way.
# A gh error is told from an answer by gh's exit status. `gh run watch --exit-status` fails both on a red run and on
# a network error, so the run's state is read with `gh run view`, retried; a watch that broke off before the run
# completed is watched again. Every wait is bounded: SHIP_LAND_RUN_POLLS polls (24) and re-reads, each
# SHIP_LAND_RUN_SLEEP s apart (5), so runs get 2 minutes to appear; the two are for tests.
polls=${SHIP_LAND_RUN_POLLS:-24} pause=${SHIP_LAND_RUN_SLEEP:-5}

# Whether run $1, which ended failure, lost its hosted runner rather than failed a step (B13 round 31 C569: run
# 40523387625's first attempt ended failure at 46:01 with no step failed, pytest's still in progress, and the
# annotation "The hosted runner lost communication with the server"; both readers called it red, exit 6). Lost
# only when every failed job has no failed step and a step left uncompleted, so a timeout or a cancelled step
# stays red; a gh error or any other answer is red too (fail closed). Both readers below use it.
run_lost() {
  local verdict
  # shellcheck disable=SC2016 # a jq program: $f is jq's, not the shell's
  verdict=$(gh run view "$1" --json jobs --jq '[.jobs[]|select(.conclusion=="failure")] as $f | if ($f|length)>0 and all($f[]; ([.steps[]|select(.conclusion=="failure")]|length)==0 and any(.steps[]; .status!="completed")) then "lost" else "red" end' 2>/dev/null) || return 1
  [ "$verdict" = lost ]
}
# The words for a run that lost its runner: not read, never red, and rerun rather than landed past.
lost_words() {
  echo "run $1 lost its hosted runner (rerun it: gh run rerun $1 --failed)"
}

watch_runs() {
  local sha=$1 ids='' req all id tries=0 answered=0 state passes reads workflow newest
  while [ -z "$ids" ] && [ "$tries" -lt "$polls" ]; do
    # A sha's runs are listed one at a time, and diagrams can be listed before ci: the list counts only once it
    # holds a ci run, which every push to main starts (B13 round 23: a land read back diagrams alone as CI green).
    if req=$(gh run list --commit "$sha" --workflow ci --json databaseId --jq '.[].databaseId' 2>/dev/null) &&
      all=$(gh run list --commit "$sha" --json databaseId --jq '.[].databaseId' 2>/dev/null); then
      answered=1
      ids=''
      [ -z "$req" ] || ids=$(printf '%s\n' "$req" "$all" | awk 'NF && !seen[$0]++')
    else
      ids=''
    fi
    [ -n "$ids" ] || { tries=$((tries + 1)); /bin/sleep "$pause"; }
  done
  if [ -z "$ids" ]; then
    if [ "$answered" = 1 ]; then
      echo "no CI run for ${sha:0:8} after $((polls * pause)) s"
    else
      echo "gh run list failed $polls times for ${sha:0:8}"
    fi
    return 2
  fi
  for id in $ids; do
    passes=0
    while :; do
      echo "== CI: waiting for run $id" >&2
      gh run watch "$id" --exit-status >/dev/null 2>&1 && break
      state='' reads=0
      until state=$(gh run view "$id" --json status,conclusion --jq '"\(.status) \(.conclusion)"' 2>/dev/null); do
        reads=$((reads + 1))
        if [ "$reads" -ge 3 ]; then
          echo "gh run view $id failed 3 times"
          return 2
        fi
        /bin/sleep "$pause"
      done
      case $state in
        "completed success" | "completed skipped" | "completed neutral") break ;;
        "completed failure" | "completed timed_out" | "completed startup_failure")
          if [ "$state" = "completed failure" ] && run_lost "$id"; then
            lost_words "$id"
            return 2
          fi
          echo "$id"
          return 1
          ;;
        "completed cancelled") ;;
        completed*)
          echo "run $id ended ${state#completed }"
          return 2
          ;;
        *)
          passes=$((passes + 1))
          if [ "$passes" -ge "$polls" ]; then
            echo "run $id is still ${state%% *} after $polls watches"
            return 2
          fi
          /bin/sleep "$pause"
          continue # the watch broke off before the run completed
          ;;
      esac
      if ! workflow=$(gh run view "$id" --json workflowName --jq .workflowName 2>/dev/null) ||
        ! newest=$(gh run list --branch main --workflow "$workflow" --limit 1 --json databaseId,headSha \
          --jq '.[0] | "\(.databaseId) \(.headSha)"' 2>/dev/null); then
        echo "gh failed reading which run superseded the cancelled run $id"
        return 2
      fi
      git fetch -q origin main || {
        echo "git fetch failed reading which run superseded the cancelled run $id"
        return 2
      }
      if [ "${newest%% *}" = "$id" ] || ! git merge-base --is-ancestor "$sha" "${newest#* }" 2>/dev/null; then
        echo "$id"
        return 1
      fi
      echo "== run $id was cancelled by a later push; ${newest#* } contains ${sha:0:8}" >&2
      id=${newest%% *} passes=0
    done
  done
}

# One read of a sha's runs, with no wait, before the gate (B13 round 27 P140): 1, the run's id printed, only when a
# run has already ended failure, timed_out or startup_failure; 2, its words printed, when that failure lost its
# hosted runner (run_lost; B13 round 31 C569); 0 in every other case (gh failed, no run yet, queued, in progress,
# cancelled), which the check after the gate decides as before. A gate on a trunk already known red
# would end in exit 6 anyway, about 35 minutes later and holding the land lock.
trunk_known_red() {
  local sha=$1 req all id state
  req=$(gh run list --commit "$sha" --workflow ci --json databaseId --jq '.[].databaseId' 2>/dev/null) || return 0
  all=$(gh run list --commit "$sha" --json databaseId --jq '.[].databaseId' 2>/dev/null) || return 0
  [ -n "$req" ] || return 0
  for id in $(printf '%s\n' "$req" "$all" | awk 'NF && !seen[$0]++'); do
    state=$(gh run view "$id" --json status,conclusion --jq '"\(.status) \(.conclusion)"' 2>/dev/null) || continue
    case $state in
      "completed failure" | "completed timed_out" | "completed startup_failure")
        if [ "$state" = "completed failure" ] && run_lost "$id"; then
          lost_words "$id"
          return 2
        fi
        echo "$id"
        return 1
        ;;
    esac
  done
  return 0
}

if [ "$locked" = 0 ]; then
  # The one deadlock the machine-wide gate cap can make: a slot holder waiting here while the land gate inside the
  # lock waits for a slot. `held` answers 1 under a test marker, so the fixture lands in tests are not refused.
  gate_slot_bin=${CC_GATE_SLOT_BIN:-}
  [ -n "$gate_slot_bin" ] || gate_slot_bin=$(command -v cc-gate-slot || true)
  [ -n "$gate_slot_bin" ] || [ ! -x "$HOME/.claude/bin/cc-gate-slot" ] || gate_slot_bin=$HOME/.claude/bin/cc-gate-slot
  if [ -n "${CC_GATE_SLOT_TOKEN:-}" ] && [ -n "$gate_slot_bin" ] && "$gate_slot_bin" held >/dev/null 2>&1; then
    echo "ship-land: refusing to wait for the land lock while holding a gate slot (lock order is land lock → gate slot); run it outside cc-gate-slot" >&2
    exit 10
  fi
  echo "== waiting for the land lock $lock"
  rc=0
  if command -v lockf >/dev/null; then
    lockf -k "$lock" "$self" --locked ${args[@]+"${args[@]}"} || rc=$?
  else
    flock "$lock" "$self" --locked ${args[@]+"${args[@]}"} || rc=$?
  fi
  [ "$rc" = 0 ] || exit "$rc"
  [ "$ci" = 1 ] || exit 0
  sha=$(git rev-parse HEAD)
  rc=0
  out=$(watch_runs "$sha") || rc=$?
  case $rc in
    0) echo "LANDED ${sha:0:8}, CI green" ;;
    1)
      echo "ship-land: CI RED on ${sha:0:8}: run $out (gh run view $out --log-failed)" >&2
      exit 8
      ;;
    *)
      echo "LANDED ${sha:0:8}, CI NOT READ: $out; read it back with the gh run watch loop in CLAUDE.md"
      exit 9
      ;;
  esac
  exit 0
fi

attempt=0
while :; do
  attempt=$((attempt + 1))
  git fetch -q origin main
  if git merge-base --is-ancestor HEAD origin/main; then
    echo "== HEAD is on origin/main already; nothing to land"
    exit 0
  fi
  # Every attempt reads the head just fetched, so a re-gate after origin/main moved checks the new head too. A run
  # that lost its runner stops the land under --allow-red-trunk too: it is rerun, not landed past (C569).
  rc=0
  [ "$ci" = 0 ] || out=$(trunk_known_red "$(git rev-parse origin/main)") || rc=$?
  if [ "$rc" = 2 ]; then
    echo "ship-land: trunk CI not read: $out; land again once the rerun is green; nothing pushed (before the gate)." >&2
    exit 9
  fi
  if [ "$rc" = 1 ] && [ "$allow_red" = 0 ]; then
    echo "ship-land: trunk is red: run $out (gh run view $out --log-failed); nothing pushed (before the gate)." >&2
    echo "  Land on top only when this diff does not touch those tests: rerun with --allow-red-trunk" >&2
    exit 6
  fi
  if ! git rebase -q origin/main; then
    git rebase --abort
    echo "ship-land: rebasing onto origin/main conflicts; resolve it by hand, nothing pushed" >&2
    exit 4
  fi
  if ! scripts/gate.sh; then
    echo "ship-land: the land gate is red; nothing pushed" >&2
    exit 5
  fi
  rc=0
  [ "$ci" = 0 ] || out=$(watch_runs "$(git rev-parse origin/main)") || rc=$?
  if [ "$rc" = 1 ]; then
    if [ "$allow_red" = 0 ]; then
      echo "ship-land: trunk is red: run $out (gh run view $out --log-failed); nothing pushed." >&2
      echo "  Land on top only when this diff does not touch those tests: rerun with --allow-red-trunk" >&2
      exit 6
    fi
    echo "== trunk is red (run $out); landing on top as --allow-red-trunk asks"
  elif [ "$rc" != 0 ]; then
    # Not read is not red: --allow-red-trunk does not cover it, and --no-ci lands without any CI read, except a
    # run that lost its runner, which is rerun and waited for (C569)
    case $out in
      *" lost its hosted runner ("*)
        echo "ship-land: trunk CI not read: $out; land again once the rerun is green; nothing pushed" >&2
        ;;
      *) echo "ship-land: could not read trunk CI ($out); nothing pushed; retry, or pass --no-ci to land without it" >&2 ;;
    esac
    exit 9
  fi
  git fetch -q origin main
  if ! git merge-base --is-ancestor origin/main HEAD; then
    echo "== origin/main moved during the gate; rebasing and gating again"
    continue
  fi
  git push -q origin HEAD:main && break
  git fetch -q origin main
  if git merge-base --is-ancestor origin/main HEAD || [ "$attempt" -ge 5 ]; then
    echo "ship-land: the push to main was refused; nothing landed" >&2
    exit 7
  fi
  echo "== another push reached main first; rebasing and gating again"
done
echo "== pushed $(git rev-parse --short HEAD) to main"
