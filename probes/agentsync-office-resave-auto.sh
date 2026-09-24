#!/bin/bash
# agentsync-office-resave-auto.sh — §9 probe 6, fully scripted (2026-09-23, after Office was activated).
# For each format: open a fresh copy with `open -a` (LaunchServices grants the sandbox extension, so no
# "Grant File Access" dialog), make Office write it (b), make Office re-write it with no net content
# change (c), then compare b vs c part by part. Office is the writer both times.
set -u
DIR="$HOME/Documents/agentsync-probe"; OUT=/tmp/agentsync-office-resave-result.txt
HFS="Macintosh HD:Users:$(id -un):Documents:agentsync-probe:"
PY="${PY:-python3}"   # any Python 3; the comparator uses only zipfile + hashlib
as() { osascript -e 'with timeout of 60 seconds' "$@" -e 'end timeout'; }
: > "$OUT"
wait_change() { local f="$1" s0="$2" i; for i in $(seq 1 60); do [ "$(stat -f '%m:%i:%z' "$f")" != "$s0" ] && { sleep 2; return 0; }; sleep 1; done; return 1; }
run() {  # $1=app $2=ext $3=applescript that dirties+saves the active document (no net content change on round c)
  local app="$1" ext="$2" body="$3" f="$DIR/w.$2" s round
  cp "$DIR/a.$ext" "$f"; open -a "$app" "$f"; sleep 6
  for round in b c; do
    s=$(stat -f '%m:%i:%z' "$f")
    as -e "tell application \"$app\"" -e "$body" -e 'end tell' >/dev/null 2>&1 || { echo "$ext: $app save command failed (round $round)" | tee -a "$OUT"; return 1; }
    wait_change "$f" "$s" || { echo "$ext: $app did not rewrite the file (round $round)" | tee -a "$OUT"; return 1; }
    cp "$f" "$DIR/$round.$ext"; echo "$ext: round $round captured ($(stat -f %z "$f") bytes)"
    sleep 2
  done
}
run "Microsoft Excel" xlsx 'set value of range "F10" of worksheet 1 of active workbook to 1
save active workbook'
run "Microsoft Word" docx 'set saved of active document to false
save active document'
run "Microsoft PowerPoint" pptx 'set content of text range of text frame of shape 1 of slide 1 of active presentation to "Deck"
save active presentation'
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
