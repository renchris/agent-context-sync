#!/bin/bash
# launchd-run.sh <command> [args...] — run ONE command as a launchd job instead of as a child of this
# shell, wait for it to finish, print its output, and remove the job. This is how the probes observe
# the context a scheduled sync really runs in: launchd starts a job with its own default I/O policy,
# and on File Provider that default does not download online-only files (receipts/verify/C14 §2).
# Relative paths that exist are made absolute first, because launchd starts the job in /.
#
# CORRECTED (2026-09-29): the harness used to wait at most a hard 15 s for non-empty output, then remove the
# job and exit with cat's status (0), so a hang, a failed `launchctl submit`, a crashed job and a job that
# printed nothing all looked like "no output, success" (and a silent job could be restarted by submit's
# KeepAlive before removal). It now: checks `launchctl submit`; polls `launchctl print` until launchd records
# the first run's exit (not until output appears); removes the job at once, before the 10 s throttle can bring
# it back; exits with the job's own exit status (128+N if it died on signal N); and on timeout prints
# "TIMEOUT after Ns" and exits 124. The timeout is `-t <seconds>` (default 60, or LAUNCHD_RUN_TIMEOUT); the old
# 15 s cap was shorter than the >20 s open() block recorded below in README.md. Shell builtins are no longer
# resolved to $PWD/<name>: the program is looked up with `type -P`. Output on the success path is unchanged.
set -u
usage() { echo "usage: launchd-run.sh [-t seconds] <command> [args...]" >&2; exit 2; }
timeout=${LAUNCHD_RUN_TIMEOUT:-60}
if [ "${1:-}" = "-t" ]; then [ $# -ge 2 ] || usage; timeout=$2; shift 2; fi
case "$timeout" in ''|*[!0-9]*) usage ;; esac
[ $# -ge 1 ] || usage
prog=$(type -P "$1") || { echo "launchd-run.sh: $1: not found (or not an executable file)" >&2; exit 2; }
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
launchctl submit -l "$label" -o "$out" -e "$out" -- "${args[@]}" \
  || { echo "launchd-run.sh: launchctl submit failed" >&2; exit 125; }
# `submit` restarts a job that exits, so collect the first run and remove the job before its
# 10 s throttle interval brings it back. `launchctl print` reports "runs = N" and, once a run has ended,
# "last exit code = N" or "last terminating signal = <name>: N" (measured 2026-09-29, macOS 15.7).
domain="gui/$(id -u)"
launchctl print "$domain/$label" >/dev/null 2>&1 || domain="user/$(id -u)"
status="" runs=""
for ((tick = 0; tick < timeout * 10; tick++)); do
  info=$(launchctl print "$domain/$label" 2>/dev/null)
  runs=$(sed -n 's/^[[:space:]]*runs = \([0-9][0-9]*\)$/\1/p' <<<"$info" | head -1)
  code=$(sed -n 's/^[[:space:]]*last exit code = \(-\{0,1\}[0-9][0-9]*\).*/\1/p' <<<"$info" | head -1)
  sig=$(sed -n 's/^[[:space:]]*last terminating signal = .*: \([0-9][0-9]*\)$/\1/p' <<<"$info" | head -1)
  if [ -n "$sig" ]; then status=$((128 + sig)); break; fi
  if [ -n "$code" ]; then status=$code; break; fi
  sleep 0.1
done
launchctl remove "$label" 2>/dev/null
cat "$out"
if [ -z "$status" ]; then
  echo "launchd-run.sh: TIMEOUT after ${timeout}s: job $label had not exited (runs=${runs:-?}); removed it" >&2
  exit 124
fi
[ "${runs:-1}" -le 1 ] || echo "launchd-run.sh: warning: launchd started the job $runs times; output above may hold more than one run" >&2
[ "$status" -eq 0 ] || echo "launchd-run.sh: job exited with status $status" >&2
exit "$status"
