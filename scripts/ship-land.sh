#!/bin/bash
# Land HEAD on origin/main: the fetch, rebase, land gate, trunk check and push run under one lock shared by every
# worktree of this checkout, then the push's CI runs are read back.
#   scripts/ship-land.sh                    land, then wait for CI on the pushed sha
#   scripts/ship-land.sh --no-ci            skip both CI reads (the trunk check before the push, the read-back after)
#   scripts/ship-land.sh --allow-red-trunk  push even when origin/main's own CI is red (record the run id)
#   scripts/ship-land.sh --lock <path>      take this lock instead of <git-common-dir>/land.lock
# Run it exactly as `scripts/ship-land.sh`, with no env prefix and no `bash` in front: the user-level allow rule
# Bash(scripts/ship-land.sh:*) is what lets it run without a prompt, and the push it makes is a subprocess the
# Bash(git push:*) ask rule never sees. It never forces: a rejected push is fetched, rebased and gated again.
# Exit: 0 landed (or nothing to land) and CI green, 2 usage, 3 dirty tree, 4 rebase conflict (aborted), 5 gate red,
# 6 trunk CI red, 7 push refused, 8 CI red on the pushed sha.
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
# Prints the id of a red run and returns 1; returns 0 when all are green; waits up to 60 s for runs to appear.
watch_runs() {
  local sha=$1 ids='' id tries=0 conclusion workflow newest
  while [ -z "$ids" ] && [ "$tries" -lt 12 ]; do
    ids=$(gh run list --commit "$sha" --json databaseId --jq '.[].databaseId')
    [ -n "$ids" ] || { tries=$((tries + 1)); /bin/sleep 5; }
  done
  [ -n "$ids" ] || echo "== no CI runs for ${sha:0:8} after 60 s" >&2
  for id in $ids; do
    while :; do
      echo "== CI: waiting for run $id" >&2
      gh run watch "$id" --exit-status >/dev/null 2>&1 && break
      conclusion=$(gh run view "$id" --json conclusion --jq .conclusion)
      if [ "$conclusion" != cancelled ]; then
        echo "$id"
        return 1
      fi
      workflow=$(gh run view "$id" --json workflowName --jq .workflowName)
      newest=$(gh run list --branch main --workflow "$workflow" --limit 1 --json databaseId,headSha \
        --jq '.[0] | "\(.databaseId) \(.headSha)"')
      git fetch -q origin main
      if [ "${newest%% *}" = "$id" ] || ! git merge-base --is-ancestor "$sha" "${newest#* }" 2>/dev/null; then
        echo "$id"
        return 1
      fi
      echo "== run $id was cancelled by a later push; ${newest#* } contains ${sha:0:8}" >&2
      id=${newest%% *}
    done
  done
}

if [ "$locked" = 0 ]; then
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
  if ! red=$(watch_runs "$sha"); then
    echo "ship-land: CI RED on ${sha:0:8}: run $red (gh run view $red --log-failed)" >&2
    exit 8
  fi
  echo "LANDED ${sha:0:8}, CI green"
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
  if ! git rebase -q origin/main; then
    git rebase --abort
    echo "ship-land: rebasing onto origin/main conflicts; resolve it by hand, nothing pushed" >&2
    exit 4
  fi
  if ! scripts/gate.sh; then
    echo "ship-land: the land gate is red; nothing pushed" >&2
    exit 5
  fi
  if [ "$ci" = 1 ] && ! red=$(watch_runs "$(git rev-parse origin/main)"); then
    if [ "$allow_red" = 0 ]; then
      echo "ship-land: trunk is red: run $red (gh run view $red --log-failed); nothing pushed." >&2
      echo "  Land on top only when this diff does not touch those tests: rerun with --allow-red-trunk" >&2
      exit 6
    fi
    echo "== trunk is red (run $red); landing on top as --allow-red-trunk asks"
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
