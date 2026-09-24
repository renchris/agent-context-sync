#!/bin/bash
# agentsync-office-resave.sh — §9 probe 6 of docs/research/agent-context-sync-2026-09-21.md:
# does a REAL Microsoft Office no-op Save keep the OOXML content parts byte-stable?
#
# v2 (2026-09-23) — v1 captured nothing: all three apps sat their full 10-min window with no save
# registered (lock files stamped exactly 10 min apart), and afterwards Word ignored an AppleScript
# close, which is what a modal dialog does. v2 therefore:
#   * closes any leftover probe document first, and refuses to rebuild the folder while one is held;
#   * waits 30 min per round, with a heartbeat every minute saying whether the doc is still OPEN;
#   * detects a save by mtime OR inode (Office saves by atomic replace), prints the moment it lands;
#   * detects a Save As by watching for ANY new file in the probe folder, ~/Documents, ~/Desktop
#     and ~/Downloads, and captures that file instead;
#   * accepts keys while waiting: Enter = status now, s = skip this app;
#   * gives per-app steps that avoid Excel's cell-edit trap (⌘S does nothing mid-edit).
# One human step per app, twice, because scripted saves are refused by all three apps
# (AppleScript -50 / -1708 / -1712, receipt verify/C12). Safe to re-run.
set -u
DIR="$HOME/Documents/agentsync-probe"; OUT=/tmp/agentsync-office-resave-result.txt
WAIT=${WAIT:-1800}
say_line() { echo "$*" | tee -a "$OUT"; }
if ( : </dev/tty ) 2>/dev/null; then TTY=1; else TTY=0; fi
# Pick a python3 that HAS openpyxl/python-docx/python-pptx — a terminal tab's PATH can resolve
# /usr/bin/python3 instead (measured 2026-09-23: ModuleNotFoundError from a kitty-launched tab).
PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 /opt/homebrew/bin/python3 "$(command -v python3)" /usr/bin/python3; do
  [ -x "$c" ] && "$c" -c 'import openpyxl, docx, pptx' 2>/dev/null && { PY="$c"; break; }
done
[ -n "$PY" ] || { echo "No python3 with openpyxl + python-docx + python-pptx found — tell Claude."; exit 2; }

# --- 0. close leftovers, then refuse to proceed while anything still holds the folder ---
osascript -e "with timeout of 10 seconds" -e 'tell application "Microsoft Excel" to close (every workbook whose name starts with "a.") saving no' -e "end timeout" >/dev/null 2>&1
osascript -e "with timeout of 10 seconds" -e 'tell application "Microsoft Word" to close (every document whose name starts with "a.") saving no' -e "end timeout" >/dev/null 2>&1
osascript -e "with timeout of 10 seconds" -e 'tell application "Microsoft PowerPoint" to close (every presentation whose name starts with "a.") saving no' -e "end timeout" >/dev/null 2>&1
sleep 2
# lsof alone is not enough: Word can keep a document open while holding no fd on it (measured
# 2026-09-23 — lsof empty, Word still listing a.docx), so also ask each app for its open paths.
held=$( { lsof +D "$DIR" 2>/dev/null | awk 'NR>1{print $1, $NF}'
  for q in 'Microsoft Excel:full name of every workbook' 'Microsoft Word:full name of every document' \
           'Microsoft PowerPoint:full name of every presentation'; do
    app=${q%%:*}; pgrep -xq "$app" || continue
    osascript -e "with timeout of 10 seconds" -e "tell application \"$app\" to get ${q#*:}" -e "end timeout" 2>/dev/null \
      | tr ',' '\n' | grep -F 'agentsync-probe' | sed "s/^ */$app: /"
  done; } | sort -u)
if [ -n "$held" ]; then
  echo "A probe document is still open and could not be closed by script:"
  echo "$held" | sed 's/^/   /'
  echo "Switch to that app — it is probably showing a dialog — dismiss it WITHOUT saving, close the document, then re-run."
  exit 3
fi
rm -rf "$DIR"; mkdir -p "$DIR"; : > "$OUT"
"$PY" - "$DIR" <<'PY' || { echo "fixture generation failed (python-docx/openpyxl/python-pptx missing?)"; exit 2; }
import sys, os
D=sys.argv[1]
import openpyxl; wb=openpyxl.Workbook(); ws=wb.active; ws.title="Q3 Budget"
ws.append(["Region","Spend","Forecast","Total"]); ws.append(["West",100,200,"=B2+C2"]); ws.append(["East",50,75,"=B3+C3"])
wb.create_sheet("Notes").append(["note","stable text"]); wb.save(os.path.join(D,"a.xlsx"))
import docx; d=docx.Document(); d.add_heading("Scope",1); d.add_paragraph("Stable paragraph."); t=d.add_table(rows=2,cols=2); t.cell(0,0).text="k"; t.cell(1,0).text="v"; d.save(os.path.join(D,"a.docx"))
import pptx; p=pptx.Presentation(); s=p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text="Deck"; s.placeholders[1].text="bullet"; p.save(os.path.join(D,"a.pptx"))
print("fixtures written")
PY
bar() { printf '\n\033[1;97;44m %-100s \033[0m\n' "$*"; }
pause() { printf '\n\033[1;33m   ⏎  %s\033[0m' "$*"; [ "$TTY" = 1 ] && read -r _ </dev/tty; echo; }
bar "Office re-save probe — 6 short steps (Excel, Word, PowerPoint × 2). About 5 minutes."
echo "   Each step: a document opens, you make one tiny edit and press ⌘S. The script does the rest."
echo "   Everything happens in a throwaway folder (~/Documents/agentsync-probe). Nothing else is touched."
pause "Press Return to begin."

sig() { stat -f '%m:%i' "$1" 2>/dev/null; }
new_files() {  # $1=ext $2=marker — files newer than the marker wherever a Save As lands
  find "$DIR" "$HOME/Documents" "$HOME/Desktop" "$HOME/Downloads" -maxdepth 3 -type f -newer "$2" \
    \( -name "*.$1" -o -name "*.$1?" \) ! -name '~$*' ! -name 'b.*' ! -name 'c.*' 2>/dev/null | sort -u
}
wait_for_save() {  # $1=path $2=ext $3=label ; sets SAVED_FILE; 0 = saved, 1 = timeout, 2 = skipped
  local f="$1" ext="$2" s0 s1 i k got lock marker nf state
  lock="$DIR/~\$${1##*/}"; marker=$(mktemp); s0=$(sig "$f"); SAVED_FILE=""
  for ((i=1; i<=WAIT; i++)); do
    k=""; got=1
    if [ "$TTY" = 1 ]; then read -r -s -n 1 -t 1 k </dev/tty 2>/dev/null && got=0; else sleep 1; fi
    s1=$(sig "$f")
    if [ -n "$s1" ] && [ "$s1" != "$s0" ]; then
      sleep 2; SAVED_FILE="$f"; say_line "    SAVE DETECTED ($3, ${i}s): $f rewritten"; rm -f "$marker"; return 0
    fi
    if [ $((i % 5)) -eq 0 ]; then
      nf=$(new_files "$ext" "$marker" | grep -vxF "$f" | head -1)
      if [ -n "$nf" ]; then
        sleep 2; SAVED_FILE="$nf"; say_line "    SAVE-AS DETECTED ($3, ${i}s): Office wrote a NEW file $nf"; rm -f "$marker"; return 0
      fi
    fi
    if [ "$got" = 0 ] && [ "$k" = "s" ]; then say_line "    SKIPPED by operator ($3)"; rm -f "$marker"; return 2; fi
    if { [ "$got" = 0 ] && [ -z "$k" ]; } || [ $((i % 60)) -eq 0 ]; then
      if [ -e "$lock" ]; then state="document OPEN"; else state="document NOT open (closed, or never opened)"; fi
      echo "    …${i}s, no save seen yet — $state. (Enter = status, s = skip this app)"
    fi
  done
  say_line "TIMEOUT ($((WAIT/60)) min) waiting for $3 save — lock file $( [ -e "$lock" ] && echo present || echo absent )"
  rm -f "$marker"; return 1
}
steps() {
  case "$1" in
    xlsx) printf '%s\n' "      1. Click the EMPTY cell F10." "      2. Type 1 and press Return." \
                        "      3. Click F10 again and press Delete (the cell is empty again)." "      4. Press ⌘S." ;;
    docx) printf '%s\n' "      1. Click at the very end of the line 'Stable paragraph.'" \
                        "      2. Type a space, then press Backspace." "      3. Press ⌘S." ;;
    pptx) printf '%s\n' "      1. Click just after the word 'Deck' in the slide title." \
                        "      2. Type a space, then press Backspace." "      3. Click an empty part of the slide." "      4. Press ⌘S." ;;
  esac
}
close_doc() {  # $1=app $2=ext — close the probe doc for the operator; ⌘W is only needed if this fails
  local cls; case "$2" in xlsx) cls=workbook;; docx) cls=document;; pptx) cls=presentation;; esac
  osascript -e "with timeout of 10 seconds" -e "tell application \"$1\" to close (every $cls whose name starts with \"a.\") saving no" -e "end timeout" >/dev/null 2>&1
  sleep 1; [ ! -e "$DIR/~\$a.$2" ]
}
N=0
probe() {  # $1=app $2=ext
  local app="$1" ext="$2" f="$DIR/a.$2" round rc
  for round in b c; do
    N=$((N+1))
    bar "Step $N of 6 — $app ($([ $round = b ] && echo first || echo second) save)"
    pause "Press Return and $app will open the test document in front of you."
    open -a "$app" "$f" || { say_line "cannot open $app"; return 1; }
    echo "   In $app:"; steps "$ext"
    echo; echo "   If a box pops up (Save As, file format, compatibility): keep the format, keep the name 'a', keep this folder,"
    echo "   and click Save / Replace. Then come back here — this window notices the save by itself."
    echo "   (Stuck? press Return here for a status line, or s to skip $app.)"
    wait_for_save "$f" "$ext" "$app round $round"; rc=$?
    [ $rc -ne 0 ] && return 1
    cp "$SAVED_FILE" "$DIR/$round.$ext"
    [ "$SAVED_FILE" != "$f" ] && cp "$SAVED_FILE" "$f"   # next round re-opens the Office-written bytes
    if close_doc "$app" "$ext"; then printf '   \033[1;32m✓ saved and captured — I closed the document for you.\033[0m\n'
    else
      printf '   \033[1;32m✓ saved and captured.\033[0m Now press ⌘W in %s to close the document (click "Don'"'"'t Save" if asked).\n' "$app"
      local w=0; while [ -e "$DIR/~\$a.$ext" ] && [ $w -lt 600 ]; do sleep 1; w=$((w+1)); done
      [ -e "$DIR/~\$a.$ext" ] && echo "   (still open after 10 min — carrying on anyway)" || echo "   ✓ closed."
    fi
  done
}
probe "Microsoft Excel" xlsx; probe "Microsoft Word" docx; probe "Microsoft PowerPoint" pptx
echo; echo "=== comparison: b (first Office save) vs c (second Office save, no edits) ===" | tee -a "$OUT"
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
bar "All done — thank you. You can close this window."
echo "   The result is saved in $OUT and Claude has been pinged; there is nothing else to do."
${NOTIFY:-true} "agentsync office resave probe FINISHED — result in $OUT: $(tr '\n' ' ' < "$OUT" | cut -c1-600)" >/dev/null 2>&1 \
  || echo "   (Could not ping Claude automatically — just tell it: done.)"
