#!/bin/bash
# smoke.sh [bin-dir] — build-free smoke test of the probes on a throwaway local APFS tree: exit codes, counts,
# and the fail-closed paths (bad arguments, missing paths, unreadable subdirectories). Needs no File Provider,
# no Office and no launchd; CI runs it on a macOS runner after `make -C probes`. Exits non-zero on the first
# unexpected result. Added 2026-09-29 with the probe exit-status corrections (see probes/README.md).
set -u
bin=${1:-$(cd "$(dirname "$0")" && pwd)/bin}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/acs-smoke.XXXXXX") || exit 2
cleanup() { chmod -R u+rwx "$tmp" 2>/dev/null; rm -rf "$tmp"; }
trap cleanup EXIT
fails=0
expect() {  # $1=expected exit code  $2=label  $3...=command; captures stdout in $got
  local want=$1 label=$2 rc; shift 2
  got=$("$@" 2>/dev/null); rc=$?
  if [ "$rc" = "$want" ]; then echo "ok    $label (exit $rc)"; else echo "FAIL  $label: exit $rc, expected $want"; fails=$((fails + 1)); fi
}
contains() {  # $1=label $2=needle — checks the last $got
  case "$got" in *"$2"*) echo "ok    $1" ;; *) echo "FAIL  $1: output lacks '$2': $got"; fails=$((fails + 1)) ;; esac
}

# 20 directories x 100 files, the shape of the tree in probes/README.md
mkdir -p "$tmp/walk"
for d in $(seq -w 1 20); do mkdir "$tmp/walk/d$d"; for f in $(seq 1 100); do echo "$f" > "$tmp/walk/d$d/f$f"; done; done
expect 0 "walkfp tree" "$bin/walkfp" "$tmp/walk"
contains "walkfp counts" "files=2000 dirs=21 gen_count_returned=2000 gen_count_nonzero=2000 dataless=0 "
expect 2 "walkfp no argument" "$bin/walkfp"
expect 1 "walkfp missing root" "$bin/walkfp" "$tmp/nope"
mkdir -p "$tmp/perm/locked"; touch "$tmp/perm/f1" "$tmp/perm/locked/a"; chmod 000 "$tmp/perm/locked"
if [ "$(id -u)" != 0 ]; then
  expect 1 "walkfp unreadable subdirectory" "$bin/walkfp" "$tmp/perm"
  contains "walkfp reports errors" "errors=1 INCOMPLETE"
fi
chmod 755 "$tmp/perm/locked"
d="$tmp/deep"; p=$d; for _ in $(seq 1 200); do p=$p/d; done; mkdir -p "$p"; touch "$p/leaf"
expect 0 "walkfp 200 levels deep" "$bin/walkfp" "$d"
contains "walkfp deep counts" "files=1 dirs=201 "

f="$tmp/walk/d01/f1"; ino=$(stat -f %i "$f")
expect 0 "gen1 file" "$bin/gen1" "$f"
contains "gen1 fileid matches stat" "fileid=$ino "
contains "gen1 GEN_COUNT returned" "gen_returned=1"
expect 2 "gen1 no argument" "$bin/gen1"
expect 1 "gen1 missing path" "$bin/gen1" "$tmp/nope"

for m in on off default; do expect 0 "readfp $m" "$bin/readfp" "$m" "$f"; contains "readfp $m read" "read=ok  bytes=2 "; done
expect 2 "readfp unknown policy word" "$bin/readfp" of "$f"
expect 2 "readfp no argument" "$bin/readfp"
expect 1 "readfp missing path" "$bin/readfp" default "$tmp/nope"
contains "readfp reports stat error" "before: stat="

expect 1 "evict on local APFS (not a ubiquitous item)" "$bin/evict" "$f"
expect 2 "evict no argument" "$bin/evict"
expect 2 "fswatch no argument" "$bin/fswatch"
expect 2 "fswatch out-file only" "$bin/fswatch" "$tmp/out.tsv"
expect 1 "fswatch out-file in a missing directory" "$bin/fswatch" "$tmp/nope/out.tsv" "$tmp"

[ "$fails" = 0 ] && echo "smoke: all passed" || echo "smoke: $fails failed"
[ "$fails" = 0 ]
