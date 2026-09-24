#!/usr/bin/env python3
"""film-verify.py: check the SHIPPED hero loop, decoded from the WebP, not the frames it was made from.

    python3 scripts/film-verify.py docs/media/hero-dark.webp /tmp/acs-film/verify-dark [step] [source-dir]

Writes into the output directory:
  sheet.png       every 0.5 s of the loop as decoded, labelled with its time, 4 per row
  poster-838.png  frame 0 at the README column's width (838 CSS px)
and prints: stored frames, total duration (must be the loop length), and the seam, i.e. how many
pixels of the last frame differ from frame 0 by more than 15 % (0 means the loop point is invisible).
Given the directory of source frames the WebP was encoded from (30 fps, f000000.png ...), it also
checks every stored frame against its source: a pixel whose 5x5 source neighbourhood is flat must
decode within 2/255. Encoder rounding that survives a cut shows up here as a ghost of the last shot.

Frame durations come from webpinfo, because Pillow mis-reports merged durations. Needs python3 with
Pillow and numpy, plus webpinfo (libwebp).
"""

import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

webp, out = Path(sys.argv[1]), Path(sys.argv[2])
step = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
src = Path(sys.argv[4]) if len(sys.argv) > 4 else None
out.mkdir(parents=True, exist_ok=True)

info = subprocess.run(
    ["webpinfo", str(webp)], capture_output=True, text=True, check=True
).stdout
durs = [int(d) for d in re.findall(r"Duration: (\d+)", info)]
starts = np.cumsum([0] + durs[:-1])
total = sum(durs)
print(f"{webp}: {len(durs)} stored frames, {total} ms, {webp.stat().st_size} bytes")

im = Image.open(webp)


def frame_at(ms):
    k = int(np.searchsorted(starts, ms, side="right") - 1)
    im.seek(max(0, k))
    return im.convert("RGB")


first = frame_at(0)
first.resize((838, round(838 * first.height / first.width)), Image.LANCZOS).save(
    out / "poster-838.png"
)
last = frame_at(total - 1)
a = np.asarray(first, dtype=np.int16)
b = np.asarray(last, dtype=np.int16)
seam = int((np.abs(a - b).max(axis=2) > 0.15 * 255).sum())
print(f"seam: {seam} px of the last frame differ from frame 0 by more than 15 %")

if src:
    from PIL import ImageFilter

    ghosted, worst = 0, (0, "none")
    for k, start in enumerate(starts):
        f = src / f"f{round(start * 30 / 1000):06d}.png"
        if not f.exists():
            continue
        im.seek(k)
        dec = np.asarray(im.convert("RGB"), dtype=np.int16)
        ref = Image.open(f).convert("RGB")
        grey = ref.convert("L")
        flat = np.asarray(grey.filter(ImageFilter.MaxFilter(5))) == np.asarray(
            grey.filter(ImageFilter.MinFilter(5))
        )
        n = int((np.abs(dec - np.asarray(ref, dtype=np.int16)).max(axis=2)[flat] >= 3).sum())
        ghosted += n > 0
        worst = max(worst, (n, f.name))
    print(
        f"ghost: {ghosted} of {len(starts)} stored frames have flat pixels off their source by >= 3/255"
        f" (worst {worst[0]} px, {worst[1]})"
    )

try:
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
except OSError:
    font = ImageFont.load_default()
tiles = []
t = 0.0
while t * 1000 < total:
    f = frame_at(t * 1000).resize(
        (560, round(560 * first.height / first.width)), Image.LANCZOS
    )
    ImageDraw.Draw(f).text((8, 6), f"{t:.1f}", fill=(255, 0, 255), font=font)
    tiles.append(f)
    t += step
cols = 4
w, h = tiles[0].size
sheet = Image.new(
    "RGB", (cols * (w + 4), -(-len(tiles) // cols) * (h + 4)), (136, 136, 136)
)
for n, tile in enumerate(tiles):
    sheet.paste(tile, ((n % cols) * (w + 4), (n // cols) * (h + 4)))
sheet.save(out / "sheet.png")
print(f"wrote {out / 'sheet.png'} ({len(tiles)} frames) and {out / 'poster-838.png'}")
