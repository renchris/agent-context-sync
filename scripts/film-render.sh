#!/usr/bin/env bash
# film-render.sh: render the launch film (film/) into its two deliverables, plus review sheets.
#
#   bash scripts/film-render.sh review   # 2 fps stills of both cuts, both themes -> contact sheets
#   bash scripts/film-render.sh loop     # the README hero: docs/media/hero-{dark,light}.webp
#   bash scripts/film-render.sh film     # the MP4 master (dark), for user-attachments
#   bash scripts/film-render.sh seam     # loop seam: frame 0 against the last frame, both themes
#
# Output goes to $OUT (default /tmp/acs-film); only the two WebPs are written into the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=${OUT:-/tmp/acs-film}
LOOP_W=${LOOP_W:-1676} # 2x the 838 CSS px README column
LOOP_H=${LOOP_H:-943}
FONT=${FONT:-/System/Library/Fonts/Menlo.ttc}
LOOP_FPS=${LOOP_FPS:-30}
FILM_FPS=${FILM_FPS:-60}
WORKERS=${WORKERS:-3}
CAP="node scripts/film-capture.mjs"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 1; }; }
need node; need ffmpeg; need magick; need img2webp; need webpinfo

sheet() { # $1 = frame dir, $2 = fps of the frames, $3 = output png, $4 = columns
  local dir=$1 fps=$2 out=$3 cols=${4:-4} i=0 labelled="$1/labelled"
  mkdir -p "$labelled"
  rm -f "$labelled"/*.png
  for f in "$dir"/f*.png; do
    local t
    t=$(awk -v i="$i" -v fps="$fps" 'BEGIN { printf "%.2f s", i / fps }')
    magick "$f" -resize 800x -font "$FONT" -gravity NorthWest -fill '#ff00ff' -undercolor '#000000c0' -pointsize 26 -annotate +10+8 "$t" "$labelled/$(basename "$f")"
    i=$((i + 1))
  done
  magick montage "$labelled"/f*.png -font "$FONT" -tile "${cols}x" -geometry +6+6 -background '#888888' "$out"
}

review() {
  for cut in loop film; do
    for theme in dark light; do
      [ "$cut" = film ] && [ "$theme" = light ] && continue # the film ships in one grade
      local dir="$OUT/review-$cut-$theme"
      $CAP --cut "$cut" --theme "$theme" --fps 2 --width 1920 --height 1080 --workers "$WORKERS" --out "$dir"
      sheet "$dir" 2 "$OUT/sheet-$cut-$theme.png" 4
      echo "  $OUT/sheet-$cut-$theme.png"
    done
  done
}

loop() {
  for theme in dark light; do
    local dir="$OUT/loop-$theme"
    $CAP --cut loop --theme "$theme" --fps "$LOOP_FPS" --width 1920 --height 1080 --workers "$WORKERS" --out "$dir"
    mkdir -p "$dir/small"
    rm -f "$dir/small"/*.png
    # One magick per frame, eight at a time: serial resizing took over 2 s a frame under load.
    # shellcheck disable=SC2016 # the single-quoted script is expanded by the inner sh, per frame
    find "$dir" -maxdepth 1 -name 'f*.png' -print0 |
      xargs -0 -P "${JOBS:-8}" -I{} sh -c 'magick "$1" -filter Lanczos -resize "$2" "$3/small/$(basename "$1")"' _ {} "${LOOP_W}x${LOOP_H}!" "$dir"
    # Per-frame durations that sum to the exact loop length (1000/30 is not an integer):
    # frame k lasts round((k+1)*1000/fps) - round(k*1000/fps) ms, so 30 fps alternates 33/33/34.
    local args=() k=0
    for f in "$dir"/small/f*.png; do
      args+=(-d "$(( ((k + 1) * 1000 + LOOP_FPS / 2) / LOOP_FPS - (k * 1000 + LOOP_FPS / 2) / LOOP_FPS ))" "$f")
      k=$((k + 1))
    done
    img2webp -loop 0 -near_lossless 40 -m 6 "${args[@]}" -o "docs/media/hero-$theme.webp" >/dev/null
    printf '  docs/media/hero-%s.webp  %s bytes, %s frames stored, %s ms\n' "$theme" "$(stat -f %z "docs/media/hero-$theme.webp")" \
      "$(webpinfo "docs/media/hero-$theme.webp" | grep -c 'Duration:' || true)" \
      "$(webpinfo "docs/media/hero-$theme.webp" | awk '/Duration:/ { s += $2 } END { print s }')"
  done
}

film() {
  local dir="$OUT/film-dark"
  $CAP --cut film --theme dark --fps "$FILM_FPS" --width 1920 --height 1080 --workers "$WORKERS" --out "$dir"
  ffmpeg -y -hide_banner -loglevel error -framerate "$FILM_FPS" -i "$dir/f%06d.png" \
    -vf "format=yuv420p" -c:v libx264 -preset slow -crf 16 -tune animation \
    -x264-params "keyint=$((FILM_FPS * 2)):bframes=4:ref=5:aq-mode=3" \
    -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 \
    -movflags +faststart "$OUT/agent-context-sync-launch.mp4"
  ffprobe -v error -select_streams v:0 -show_entries stream=width,height,avg_frame_rate,nb_frames -show_entries format=duration,size -of default=nw=1 "$OUT/agent-context-sync-launch.mp4"
}

seam() {
  for theme in dark light; do
    local frames=("$OUT/loop-$theme"/f*.png)
    local last=${frames[${#frames[@]} - 1]}
    printf '  seam %s: ' "$theme"
    magick compare -metric AE -fuzz 15% "$OUT/loop-$theme/f000000.png" "$last" null: 2>&1 || true
    echo " px differ (frame 0 vs $(basename "$last"))"
  done
}

case "${1:-review}" in
  review) review ;;
  loop) loop ;;
  film) film ;;
  seam) seam ;;
  *) echo "usage: $0 review|loop|film|seam" >&2; exit 2 ;;
esac
