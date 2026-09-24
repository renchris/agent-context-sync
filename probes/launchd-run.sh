#!/bin/bash
# launchd-run.sh <command> [args...] — run ONE command as a launchd job instead of as a child of this
# shell, wait for it to finish, print its output, and remove the job. This is how the probes observe
# the context a scheduled sync really runs in: launchd starts a job with its own default I/O policy,
# and on File Provider that default does not download online-only files (receipts/verify/C14 §2).
# Relative paths that exist are made absolute first, because launchd starts the job in /.
set -u
[ $# -ge 1 ] || { echo "usage: launchd-run.sh <command> [args...]" >&2; exit 2; }
prog=$(command -v "$1") || { echo "launchd-run.sh: $1: not found" >&2; exit 2; }
case "$prog" in /*) ;; *) prog="$PWD/$prog" ;; esac
shift
args=("$prog")
for a in "$@"; do
  case "$a" in
    /*) args+=("$a") ;;
    *) if [ -e "$a" ]; then args+=("$PWD/$a"); else args+=("$a"); fi ;;
  esac
done
label="dev.agent-context-sync.probe.$$"
out=$(mktemp /tmp/launchd-run.XXXXXX)
trap 'launchctl remove "$label" 2>/dev/null; rm -f "$out"' EXIT
launchctl submit -l "$label" -o "$out" -e "$out" -- "${args[@]}"
# `submit` restarts a job that exits, so collect the first run and remove the job before its
# 10 s throttle interval brings it back.
for _ in $(seq 1 150); do
  if [ -s "$out" ] && ! launchctl list "$label" 2>/dev/null | grep -q '"PID"'; then break; fi
  sleep 0.1
done
launchctl remove "$label" 2>/dev/null
cat "$out"
