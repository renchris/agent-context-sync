#!/bin/bash
# agentsync-office-resave-auto.sh — §9 probe 6, fully scripted (2026-09-23, after Office was activated).
# For each format: open a fresh copy with `open -a` (LaunchServices grants the sandbox extension, so no
# "Grant File Access" dialog), make Office write it (b), make Office re-write it with no net content
# change (c), then compare b vs c part by part. Office is the writer both times.
#
# CORRECTED (2026-09-29): this script used to ignore a failed `cp` or `open` and then drive AppleScript
# against Office's *active* workbook/document/presentation, so on a Mac where the fixtures were missing (it
# never created them) it set F10 of the user's own active workbook to 1, replaced the text of shape 1 on
# slide 1 of the user's active presentation, and saved both. It now stops when cp or open fails, addresses
# the probe copies by name (workbook "w.xlsx", document "w.docx", presentation "w.pptx"), polls until that
# named document is open (up to 120 s) instead of a fixed `sleep 6`, and refuses to start without the
# a.xlsx/a.docx/a.pptx fixtures, which agentsync-office-resave.sh generates. Other corrections: the result
# file is no longer a fixed, predictable path in world-writable /tmp but lives in the per-user $TMPDIR (or a
# mktemp file); the probe folder can be moved with AGENTSYNC_PROBE_DIR and the script refuses to run when it
# resolves into a cloud-synced folder (for example ~/Documents redirected by OneDrive Known Folder Move), where
# fixtures would sync to the tenant and cloud AutoSave would change the save path being measured. On the
# success path the Office actions and the comparison are unchanged.
set -u
DIR="${AGENTSYNC_PROBE_DIR:-$HOME/Documents/agentsync-probe}"
# Per-user result file: $TMPDIR is per-user (0700) on macOS; fall back to an unpredictable mktemp name.
if [ -n "${TMPDIR:-}" ] && [ -d "$TMPDIR" ] && [ -O "$TMPDIR" ]; then OUT="${TMPDIR%/}/agentsync-office-resave-result.txt"
else OUT=$(mktemp /tmp/agentsync-office-resave-result.XXXXXX) || exit 2; fi
[ -L "$OUT" ] && { echo "refusing to write through a symlink at $OUT" >&2; exit 2; }
PY="${PY:-python3}"   # any Python 3; the comparator uses only zipfile + hashlib
# Refuse a probe folder that resolves into a cloud-synced location (OneDrive KFM, iCloud Desktop & Documents).
resolve_dir() { local d="$1" rest=""; while [ ! -d "$d" ]; do rest="/${d##*/}$rest"; d=$(dirname "$d"); done; echo "$(cd "$d" && pwd -P)$rest"; }
real_dir=$(resolve_dir "$DIR"); real_home=$(cd "$HOME" && pwd -P)
case "$real_dir" in
  "$real_home/Library/CloudStorage/"*|"$real_home/Library/Mobile Documents/"*)
    echo "refusing to run: $DIR resolves to $real_dir, a cloud-synced folder. Set AGENTSYNC_PROBE_DIR to a local folder." >&2; exit 2 ;;
esac
for ext in xlsx docx pptx; do
  [ -f "$DIR/a.$ext" ] || { echo "missing fixture $DIR/a.$ext — run agentsync-office-resave.sh once to generate a.xlsx/a.docx/a.pptx (it needs openpyxl, python-docx, python-pptx), or set AGENTSYNC_PROBE_DIR" >&2; exit 2; }
done
as() { osascript -e 'with timeout of 60 seconds' "$@" -e 'end timeout'; }
: > "$OUT"
wait_change() { local f="$1" s0="$2"; for _ in $(seq 1 60); do [ "$(stat -f '%m:%i:%z' "$f")" != "$s0" ] && { sleep 2; return 0; }; sleep 1; done; return 1; }
# $1=app $2=AppleScript class (workbook/document/presentation) $3=name — 0 once that named document is open
wait_open() { local _i; for _i in $(seq 1 120); do [ "$(as -e "tell application \"$1\" to exists $2 \"$3\"" 2>/dev/null)" = true ] && { sleep 2; return 0; }; sleep 1; done; return 1; }
run() {  # $1=app $2=ext $3=applescript that dirties+saves the named probe copy w.$2 (no net content change on round c)
  local app="$1" ext="$2" body="$3" f="$DIR/w.$2" s round cls
  case "$ext" in xlsx) cls=workbook;; docx) cls=document;; pptx) cls=presentation;; esac
  # a copy left open by an earlier run would be overwritten under the app: close it (only w.<ext>) first
  as -e "tell application \"$app\" to close (every $cls whose name is \"w.$ext\") saving no" >/dev/null 2>&1
  cp "$DIR/a.$ext" "$f" || { echo "$ext: cannot copy $DIR/a.$ext to $f" | tee -a "$OUT"; return 1; }
  open -a "$app" "$f" || { echo "$ext: open -a \"$app\" failed" | tee -a "$OUT"; return 1; }
  wait_open "$app" "$cls" "w.$ext" || { echo "$ext: $app did not open w.$ext within 120 s" | tee -a "$OUT"; return 1; }
  for round in b c; do
    s=$(stat -f '%m:%i:%z' "$f")
    as -e "tell application \"$app\"" -e "$body" -e 'end tell' >/dev/null 2>&1 || { echo "$ext: $app save command failed (round $round)" | tee -a "$OUT"; return 1; }
    wait_change "$f" "$s" || { echo "$ext: $app did not rewrite the file (round $round)" | tee -a "$OUT"; return 1; }
    cp "$f" "$DIR/$round.$ext"; echo "$ext: round $round captured ($(stat -f %z "$f") bytes)"
    sleep 2
  done
}
failed=0
run "Microsoft Excel" xlsx 'set value of range "F10" of worksheet 1 of workbook "w.xlsx" to 1
save workbook "w.xlsx"' || failed=1
run "Microsoft Word" docx 'set saved of document "w.docx" to false
save document "w.docx"' || failed=1
run "Microsoft PowerPoint" pptx 'set content of text range of text frame of shape 1 of slide 1 of presentation "w.pptx" to "Deck"
save presentation "w.pptx"' || failed=1
as -e 'tell application "Microsoft Excel" to close (every workbook whose name is "w.xlsx") saving no' >/dev/null 2>&1
as -e 'tell application "Microsoft Word" to close (every document whose name is "w.docx") saving no' >/dev/null 2>&1
as -e 'tell application "Microsoft PowerPoint" to close (every presentation whose name is "w.pptx") saving no' >/dev/null 2>&1
echo "=== comparison: b (first Office save) vs c (second Office save, no net edit) ===" | tee -a "$OUT"
"$PY" - "$DIR" <<'PY' | tee -a "$OUT"
import zipfile, hashlib, os, sys
D=sys.argv[1]
def parts(p):
    z=zipfile.ZipFile(p); return {i.filename:(hashlib.sha256(z.read(i)).hexdigest(), i.date_time) for i in z.infolist()}
for ext in ("xlsx","docx","pptx"):
    b,c=os.path.join(D,f"b.{ext}"),os.path.join(D,f"c.{ext}")
    if not (os.path.exists(b) and os.path.exists(c)): print(f"{ext}: NOT CAPTURED"); continue
    hb=hashlib.sha256(open(b,'rb').read()).hexdigest()[:12]; hc=hashlib.sha256(open(c,'rb').read()).hexdigest()[:12]
    pb,pc=parts(b),parts(c); names=sorted(set(pb)|set(pc))
    diff=[n for n in names if pb.get(n,("",))[0]!=pc.get(n,("",))[0]]
    ts=sum(1 for n in names if n in pb and n in pc and pb[n][1]!=pc[n][1])
    ex=lambda n: n.startswith("docProps/")
    rb=hashlib.sha256("".join(f"{n}:{pb[n][0]}" for n in sorted(pb) if not ex(n)).encode()).hexdigest()[:12]
    rc=hashlib.sha256("".join(f"{n}:{pc[n][0]}" for n in sorted(pc) if not ex(n)).encode()).hexdigest()[:12]
    print(f"{ext}: whole-file equal={hb==hc} ({hb} vs {hc}) | parts={len(names)} content-differing={diff} zip-timestamp-differing={ts} | rollup-excluding-docProps equal={rb==rc}")
PY
[ "$failed" = 0 ] || { echo "at least one format was not captured; see $OUT" >&2; exit 1; }
