"""A fake media helper for the recording converter's tests (spec 9.3), in the style of the fake OCR helper.

``fake_media(folder, script)`` writes a Python script that speaks the media helper's protocol and logs each
call. ``script`` says what the recording "shows": its length, picture size and sound, and per 2 s tick the
rows on screen, which label is lit, and any painted rectangles.  A recording file made by ``fake_recording``
may carry a script of its own, which then wins, so one helper can serve several recordings.

What the helper answers (one JSON document on stdout; exit 3 with a message on stderr on failure):

- ``--version``: ``{"engine": ..., "helper": ...}``.
- ``info FILE``: ``duration_ms``, ``width``, ``height``, ``picture`` (``h264``, ``vp9``, ``av1`` or null when
  the file has no picture), ``audio``, ``created`` (UTC or null).
- ``scan FILE --out DIR [--step-ms 2000] [--first-tick K] [--max-ticks N]``: writes ``DIR/grids.bin``, one
  320x180 luma grid per tick from tick ``K`` (default 0), at most ``N`` of them, and prints the tick list,
  whose indexes and times stay absolute.  Tick ``k`` is media time ``k x step``; there are ``floor(duration
  / step) + 1``.  A ``K`` past the end fails.  The recording converter reads a recording in pieces this way.
- ``frames FILE --out DIR --ticks 0,3,9 [--crop X0,Y0,X1,Y1 | --crop-right F]``: one ``tHHMMSS.jpg`` per tick,
  named by media time.  Each holds ``\\xff\\xd8\\xff\\xe0`` + ``FAKE-OCR:`` + the rows of that tick inside the
  crop, in the crop's own fractions, so the fake OCR helper of ``tests/test_ocr.py`` reads exactly what this
  one showed.
- ``diff GRIDS --pairs A:B,C:D [--include R]... [--exclude R]... [--threshold 12]``: changed cells per pair
  under the mask (the included rectangles, whole grid when none, less the excluded ones), counted for real.
- ``pills FILE --request REQ.json``: ``REQ`` is ``[{"tick": k, "boxes": [[x, y, w, h], ...]}]``; each box gets
  70 when its centre lies in the box of a row the tick lists as lit, else 20 (the ``teams`` cue lights at 50).
- ``audio FILE --out DIR``: ``DIR/audio.pcm``, 16 kHz mono 16-bit, silent except the script's ``sound`` spans.

Rows are ``[text, x, y, w, h]`` or ``[text, x, y, w, h, confidence]``, fractions of the frame, origin
top-left, as ``OcrLine`` boxes are.  A row paints its box into the grid with a luma taken from its text, so a
changed row changes pixels and the pixel gate sees it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

GRID_W, GRID_H = 320, 180
FAKE_MEDIA_VERSION = '{"engine": "paper-media", "helper": "0.1.0"}'

FAKE_MEDIA = r"""#!{python}
import json, math, os, sys, zlib
from pathlib import Path

GW, GH = 320, 180
args = sys.argv[1:]
with open(sys.argv[0] + ".calls", "a") as log:
    log.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
if "--version" in args:
    print({version!r})
    sys.exit({version_exit})
command, rest = (args[0], args[1:]) if args else ("", [])
failures, replies = {fail!r}, {reply!r}
if command in failures:
    sys.stderr.write("error: " + failures[command] + "\n")
    sys.exit(3)
if command in replies:
    print(replies[command])
    sys.exit(0)

def die(message):
    sys.stderr.write("error: " + message + "\n")
    sys.exit(3)

def options(names):
    found, plain, it = {}, [], iter(rest)
    for a in it:
        if a in names:
            found.setdefault(a, []).append(next(it))
        else:
            plain.append(a)
    return found, plain

def script_of(path):
    data = Path(path).read_bytes() if Path(path).is_file() else None
    if data is None:
        die("cannot open the file")
    at = data.find(b"FAKE-MEDIA:")
    return json.loads(data[at + 11:].decode()) if at >= 0 else {script!r}

def screens(script, count):
    marks = sorted(script.get("ticks", []), key=lambda t: t["at"])
    out, current = [], {}
    for k in range(count):
        while marks and marks[0]["at"] <= k:
            current = marks.pop(0)
        out.append(current)
    return out

def tick_count(script, step):
    return script["duration_ms"] // step + 1

def cells(box):
    x0, y0, x1, y1 = box
    return (max(0, math.floor(x0 * GW)), max(0, math.floor(y0 * GH)),
            min(GW, math.ceil(x1 * GW)), min(GH, math.ceil(y1 * GH)))

def grid(screen):
    g = bytearray([screen.get("luma", 16)]) * (GW * GH)
    paints = [(p[:4], p[4]) for p in screen.get("paint", [])]
    paints += [((r[1], r[2], r[1] + r[3], r[2] + r[4]), 64 + zlib.crc32(r[0].encode()) % 160)
               for r in screen.get("rows", [])]
    for box, luma in paints:
        cx0, cy0, cx1, cy1 = cells(box)
        for y in range(cy0, cy1):
            g[y * GW + cx0:y * GW + cx1] = bytes([luma]) * max(0, cx1 - cx0)
    return bytes(g)

def hms(ms):
    s = ms // 1000
    return "%02d%02d%02d" % (s // 3600, s // 60 % 60, s % 60)

def rect(text):
    return tuple(float(v) for v in text.split(","))

if command == "info":
    s = script_of(rest[0])
    w, h = s.get("size", [1920, 1080])
    print(json.dumps({"duration_ms": s["duration_ms"], "width": w, "height": h,
                      "picture": s.get("picture", "h264"), "audio": bool(s.get("audio", True)),
                      "created": s.get("created")}))
elif command == "scan":
    found, plain = options({"--out", "--step-ms", "--first-tick", "--max-ticks"})
    s = script_of(plain[0])
    if s.get("picture", "h264") not in ("h264", "hevc"):
        die("the picture cannot be decoded")
    step = int(found.get("--step-ms", ["2000"])[0])
    count = tick_count(s, step)
    first = int(found.get("--first-tick", ["0"])[0])
    if not 0 <= first < count:
        die("tick %d is past the end" % first)
    if "--max-ticks" in found:
        count = min(count, first + int(found["--max-ticks"][0]))
    out = Path(found["--out"][0])
    with open(out / "grids.bin", "wb") as f:
        for screen in screens(s, count)[first:]:
            f.write(grid(screen))
    print(json.dumps({"grid": [GW, GH], "step_ms": step, "file": "grids.bin",
                      "ticks": [{"index": k, "ms": k * step} for k in range(first, count)]}))
elif command == "frames":
    found, plain = options({"--out", "--ticks", "--crop", "--crop-right", "--step-ms"})
    s = script_of(plain[0])
    step = int(found.get("--step-ms", ["2000"])[0])
    count = tick_count(s, step)
    w, h = s.get("size", [1920, 1080])
    x0, y0, x1, y1 = rect(found["--crop"][0]) if "--crop" in found else (0.0, 0.0, 1.0, 1.0)
    if "--crop-right" in found:
        x1 = float(found["--crop-right"][0])
    left, top, right, bottom = math.floor(x0 * w), math.floor(y0 * h), math.floor(x1 * w), math.floor(y1 * h)
    cw, ch = right - left, bottom - top
    all_screens = screens(s, count)
    out, made = Path(found["--out"][0]), []
    for k in [int(t) for t in found["--ticks"][0].split(",") if t]:
        if not 0 <= k < count:
            die("tick %d is past the end" % k)
        rows = []
        for r in all_screens[k].get("rows", []):
            text, rx, ry, rw, rh = r[:5]
            if left <= (rx + rw / 2) * w < right and top <= (ry + rh / 2) * h < bottom:
                box = [(rx * w - left) / cw, (ry * h - top) / ch, rw * w / cw, rh * h / ch]
                rows.append([text, *box, *r[5:]])
        name = "t" + hms(k * step) + ".jpg"
        doc = {"size": [cw, ch], "frames": [rows]}
        (out / name).write_bytes(b"\xff\xd8\xff\xe0" + b"FAKE-OCR:" + json.dumps(doc).encode())
        made.append({"tick": k, "ms": k * step, "file": name, "width": cw, "height": ch})
    print(json.dumps({"frames": made}))
elif command == "diff":
    found, plain = options({"--pairs", "--include", "--exclude", "--threshold"})
    data = Path(plain[0]).read_bytes()
    threshold = int(found.get("--threshold", ["12"])[0])
    mask = bytearray([0 if found.get("--include") else 1]) * (GW * GH)
    for value, key in ((1, "--include"), (0, "--exclude")):
        for text in found.get(key, []):
            cx0, cy0, cx1, cy1 = cells(rect(text))
            for y in range(cy0, cy1):
                mask[y * GW + cx0:y * GW + cx1] = bytes([value]) * max(0, cx1 - cx0)
    size, pairs = GW * GH, []
    for pair in found["--pairs"][0].split(","):
        a, b = (int(v) for v in pair.split(":"))
        ga, gb = data[a * size:(a + 1) * size], data[b * size:(b + 1) * size]
        if len(ga) < size or len(gb) < size:
            die("tick past the end of the grid file")
        changed = sum(1 for i in range(size) if mask[i] and abs(ga[i] - gb[i]) > threshold)
        pairs.append({"a": a, "b": b, "changed": changed, "cells": sum(mask)})
    print(json.dumps({"pairs": pairs}))
elif command == "pills":
    found, plain = options({"--request", "--step-ms"})
    s = script_of(plain[0])
    step = int(found.get("--step-ms", ["2000"])[0])
    all_screens = screens(s, tick_count(s, step))
    answer = []
    for item in json.loads(Path(found["--request"][0]).read_text()):
        screen = all_screens[item["tick"]]
        lit = [r for r in screen.get("rows", []) if r[0] in screen.get("lit", [])]
        values = []
        for bx, by, bw, bh in item["boxes"]:
            cx, cy = bx + bw / 2, by + bh / 2
            on = any(r[1] <= cx <= r[1] + r[3] and r[2] <= cy <= r[2] + r[4] for r in lit)
            values.append(70 if on else 20)
        answer.append({"tick": item["tick"], "values": values})
    print(json.dumps({"pills": answer}))
elif command == "audio":
    found, plain = options({"--out"})
    s = script_of(plain[0])
    if not s.get("audio", True):
        die("the recording has no sound")
    samples = s["duration_ms"] * 16
    pcm = bytearray(samples * 2)
    for start, end in s.get("sound", []):
        for i in range(start * 16, min(end * 16, samples)):
            pcm[2 * i:2 * i + 2] = (3000 if i // 16 % 2 else -3000).to_bytes(2, "little", signed=True)
    (Path(found["--out"][0]) / "audio.pcm").write_bytes(bytes(pcm))
    print(json.dumps({"file": "audio.pcm", "sample_rate": 16000, "channels": 1, "samples": samples}))
else:
    die("unknown command " + repr(command))
"""


def fake_media(
    folder: Path,
    script: dict[str, Any] | None = None,
    *,
    fail: dict[str, str] | None = None,
    reply: dict[str, str] | None = None,
    version: str = FAKE_MEDIA_VERSION,
    version_exit: int = 0,
) -> Path:
    """The fake media helper at ``folder/fake-media``, owner-only.  ``script`` is what a recording without a
    script of its own shows (see ``recording``).  ``fail``: commands that exit 3 with that message.
    ``reply``: commands that print this text instead.  ``version`` and ``version_exit``: what ``--version``
    prints and returns.  Each call is logged in ``<helper>.calls`` (``calls``)."""
    values = {
        "python": sys.executable,
        "script": script if script is not None else recording(),
        "fail": fail or {},
        "reply": reply or {},
        "version": version,
    }
    text = FAKE_MEDIA.replace("{version_exit}", str(version_exit))
    for key, value in values.items():
        text = text.replace("{" + key + "!r}", repr(value)).replace("{" + key + "}", str(value))
    path = folder / "fake-media"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)
    return path


def calls(helper: Path) -> list[dict[str, Any]]:
    """Every call the fake helper logged, oldest first."""
    log = Path(str(helper) + ".calls")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def row(text: str, x: float, y: float, w: float = 0.4, h: float = 0.03, confidence: float = 1.0) -> list[Any]:
    """One row on screen: its text and box in fractions of the frame, origin top-left."""
    return [text, x, y, w, h, confidence]


def screen(
    at: int,
    *rows: list[Any],
    lit: Sequence[str] = (),
    luma: int = 16,
    paint: Sequence[tuple[float, float, float, float, int]] = (),
) -> dict[str, Any]:
    """What is on screen from tick ``at`` until the next screen: ``rows``, the texts of rows drawn as speaking
    (``lit``), the background luma, and rectangles ``(x0, y0, x1, y1, luma)`` painted under the rows."""
    return {"at": at, "rows": list(rows), "lit": list(lit), "luma": luma, "paint": [list(p) for p in paint]}


def recording(
    *screens: dict[str, Any],
    duration_ms: int = 60_000,
    size: tuple[int, int] = (1920, 1080),
    picture: str | None = "h264",
    audio: bool = True,
    created: str | None = "2026-10-02T14:03:12Z",
    sound: Sequence[tuple[int, int]] = (),
) -> dict[str, Any]:
    """A script: the screens in tick order, the length, the picture's size and codec (None: no picture),
    whether there is sound, the container's creation time, and the spans ``(from_ms, to_ms)`` that are not
    silent."""
    return {
        "duration_ms": duration_ms,
        "size": list(size),
        "picture": picture,
        "audio": audio,
        "created": created,
        "ticks": list(screens) or [screen(0)],
        "sound": [list(s) for s in sound],
    }


def fake_recording(path: Path, script: dict[str, Any] | None = None, *, brand: bytes = b"mp42") -> Path:
    """A file shaped like an MP4 (bytes 4 to 7 are ``ftyp``) that the fake helper reads ``script`` from; with
    no script the helper falls back to its own."""
    head = b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x02\x00isommp42"
    path.write_bytes(head + (b"FAKE-MEDIA:" + json.dumps(script).encode() if script is not None else b""))
    return path


def grids(path: Path) -> list[bytes]:
    """The grids a ``scan`` wrote, one bytes object of 320 x 180 luma values per tick."""
    data = path.read_bytes()
    size = GRID_W * GRID_H
    return [data[i : i + size] for i in range(0, len(data), size)]
