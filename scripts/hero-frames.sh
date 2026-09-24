#!/usr/bin/env bash
# Verify the hero loop by looking at it: pause the animation at fixed times,
# screenshot each frame at 2x in both colour schemes, and build contact sheets.
#
# Usage:  scripts/hero-frames.sh [out-dir] [step-seconds] [extra times...]
# Needs:  agent-browser (drives Chromium), ImageMagick (montage, compare)
#
# Every frame is taken with all animations paused and seeked, never by waiting,
# so frame t is exactly what a viewer sees t seconds into the loop. The seam
# frames (t = 0, T - 0.05 and T) and a reduced-motion frame are always taken.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:-/tmp/hero-frames}
STEP=${2:-0.5}
shift $(( $# > 2 ? 2 : $# ))
EXTRA=("$@")

T=$(grep -oE 'animation-duration:[0-9.]+s' docs/media/hero-dark.svg | head -1 | tr -dc '0-9.')
read -r VW VH < <(grep -oE 'viewBox="0 0 [0-9]+ [0-9]+"' docs/media/hero-dark.svg | head -1 | tr -dc '0-9 ' | awk '{print $3, $4}')
[ -n "$T" ] && [ -n "$VW" ] || { echo "cannot read T or viewBox from docs/media/hero-dark.svg" >&2; exit 1; }
SW=$((VW * 2)); SH=$((VH * 2))
mkdir -p "$OUT"
rm -f "$OUT"/*.png

AB() { agent-browser --session hero-frames "$@" >/dev/null; }
times=$(awk -v T="$T" -v S="$STEP" 'BEGIN { for (t = 0; t < T - 1e-9; t += S) printf "%.2f\n", t; printf "%.2f\n%.2f\n", T - 0.05, T }')
times=$(printf '%s\n' "$times" "${EXTRA[@]}" | awk 'NF' | sort -n -u)

for theme in dark light; do
  bg=$([ "$theme" = dark ] && echo '#0d1117' || echo '#ffffff')
  AB set media "$theme"
  AB open "file://$PWD/docs/media/hero-$theme.svg"
  AB set viewport $((SW + 40)) $((SH + 40))
  AB eval "const s = document.documentElement; s.setAttribute('width', $SW); s.setAttribute('height', $SH); s.style.background = '$bg'; 0"
  n=0
  for t in $times; do
    ms=$(awk -v t="$t" 'BEGIN { printf "%d", t * 1000 }')
    AB eval "document.getAnimations().forEach(a => { a.pause(); a.currentTime = $ms }); 0"
    f=$(printf '%s/%s-%06.2f.png' "$OUT" "$theme" "$t")
    agent-browser --session hero-frames screenshot "$f" >/dev/null
    magick "$f" -crop "${SW}x${SH}+0+0" +repage "$f"
    n=$((n + 1))
  done
  # Reduced motion must freeze on the answer frame (t = 0).
  AB set media "$theme" reduced-motion
  AB open "file://$PWD/docs/media/hero-$theme.svg"
  AB eval "const s = document.documentElement; s.setAttribute('width', $SW); s.setAttribute('height', $SH); s.style.background = '$bg'; 0"
  agent-browser --session hero-frames screenshot "$OUT/$theme-reduced.png" >/dev/null
  magick "$OUT/$theme-reduced.png" -crop "${SW}x${SH}+0+0" +repage "$OUT/$theme-reduced.png"
  AB set media "$theme"
  echo "$theme: $n frames"
done
agent-browser --session hero-frames close >/dev/null 2>&1 || true

# Seam and reduced-motion checks: pixel difference against frame 0.
for theme in dark light; do
  zero=$(printf '%s/%s-%06.2f.png' "$OUT" "$theme" 0)
  end=$(printf '%s/%s-%06.2f.png' "$OUT" "$theme" "$T")
  near=$(printf '%s/%s-%06.2f.png' "$OUT" "$theme" "$(awk -v T="$T" 'BEGIN{printf "%.2f", T-0.05}')")
  for pair in "seam(T):$end" "seam(T-0.05):$near" "reduced-motion:$OUT/$theme-reduced.png"; do
    label=${pair%%:*}; file=${pair#*:}
    # Anti-aliasing differs slightly between an animated (composited) and a
    # static element, so count only pixels more than 15% apart.
    px=$(magick compare -metric AE -fuzz 15% "$zero" "$file" null: 2>&1 | awk '{print $1}' || true)
    # A seam must be exact; reduced motion may differ only by compositor
    # anti-aliasing on text that is animated in the other frame.
    limit=$([ "$label" = reduced-motion ] && echo 120 || echo 20)
    verdict=$([ "${px%.*}" -le "$limit" ] 2>/dev/null && echo SAME || echo DIFFERENT)
    echo "$theme $label vs t=0: $verdict ($px px beyond 15% fuzz)"
  done
done

# Contact sheets: 20 frames each, labelled with t.
LABEL_FONT=$(ls /System/Library/Fonts/Supplemental/Arial.ttf /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf 2>/dev/null | head -1 || true)
for theme in dark light; do
  bg=$([ "$theme" = dark ] && echo '#0d1117' || echo '#ffffff')
  fg=$([ "$theme" = dark ] && echo '#8b949e' || echo '#59636e')
  frames=()
  while IFS= read -r f; do frames+=("$f"); done < <(ls "$OUT/$theme"-[0-9]*.png | sort)
  i=0; sheet=0
  while [ $i -lt ${#frames[@]} ]; do
    chunk=("${frames[@]:$i:20}")
    args=()
    for f in "${chunk[@]}"; do
      t=$(basename "$f" .png); t=${t#"$theme"-}
      args+=(-label "t=$(awk -v t="$t" 'BEGIN{printf "%.2f", t}')s" "$f")
    done
    montage "${args[@]}" -tile 4x -geometry "$((SW / 4))x$((SH / 4))+6+6" \
      -background "$bg" -fill "$fg" ${LABEL_FONT:+-font "$LABEL_FONT"} -pointsize 14 "$OUT/sheet-$theme-$sheet.png"
    # The stage (below the key line) at half size, two to a row, where the
    # motion lives and a full-frame thumbnail is too small to judge.
    sargs=()
    for f in "${chunk[@]}"; do
      t=$(basename "$f" .png); t=${t#"$theme"-}
      crop="$OUT/.stage-$(basename "$f")"
      magick "$f" -crop "${SW}x$((SH * 36 / 100))+0+$((SH * 64 / 100))" +repage -resize 50% "$crop"
      sargs+=(-label "t=$(awk -v t="$t" 'BEGIN{printf "%.2f", t}')s" "$crop")
    done
    montage "${sargs[@]}" -tile 2x -geometry "+6+6" \
      -background "$bg" -fill "$fg" ${LABEL_FONT:+-font "$LABEL_FONT"} -pointsize 14 "$OUT/stage-$theme-$sheet.png"
    rm -f "$OUT"/.stage-*.png
    i=$((i + 20)); sheet=$((sheet + 1))
  done
done
ls "$OUT"/sheet-*.png "$OUT"/stage-*.png
