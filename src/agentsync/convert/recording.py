"""Meeting recordings (``.mp4``, ``.m4v``, ``.mov``) into an evidence package of on-screen text and keyframes.

Spec ``docs/design/meeting-video-spec.md`` S1 to S6 (probe, scan, profile and pixel gate, frames, OCR, screen
states); S9 and the index (3.3 to 3.6) are rendered by ``convert/recording_page.py`` from the :class:`Reading`
this module settles.  A recording is read in pieces of 5 minutes of its picture track (4.1), each stored in
the piece store (``convert/pieces.py``) with the state the next piece needs, so a recording read over several
cycles gives the page one pass gives.

How a piece is read.  ``scan`` writes one luma grid per tick of the piece.  The profile is decided on piece 0
from tick 0's OCR and the piece's grids, never from the file's name.  The pixel gate picks the candidate
ticks; the piece's time limit is fixed from those counts; then candidates are read in tick order, each
decision (read it, or skip it under a back-off) made as if they were read one at a time.  To keep three OCR
runs busy the reader asks for the next few candidates the current rule would read, and uses a result only
when the one-at-a-time rule reaches that tick, so what is read never depends on how the work was batched.
A piece stores every read candidate (its OCR lines, its grid, the facts the kind rule needs), the gated
ticks, and what the next piece carries on: the last candidate's grid, the back-off state, the rows of the
open state and of the last read, and the reads left.  S6 runs once every piece exists, over the stored
candidates alone, so a recording read in pieces gives the page one pass gives byte for byte.

Determinism: every limit is a count; the one time limit per piece is fixed from counts before its first
candidate is read; time can fail or defer a piece (:class:`RecordingNotFinished`), never change a page.  No
output holds a wall-clock value, a path or the file's name.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import difflib
import json
import math
import re
import shutil
import tempfile
import time
import zlib
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from agentsync.config import ConvertConfig
from agentsync.convert._common import _emitter
from agentsync.convert.base import OptionValue, options_hash
from agentsync.convert.image import _OCR_OPTIONS
from agentsync.convert.media import (
    GRID_H,
    GRID_W,
    JPEG_QUALITY,
    STEP_MS,
    Frame,
    MediaEngine,
    MediaError,
    Rect,
)
from agentsync.convert.ocr import OcrEngine, OcrError, OcrImage, OcrLine, text_rows
from agentsync.convert.pieces import PieceStore
from agentsync.errors import UnreadableSourceError
from agentsync.materialise import sha256_file
from agentsync.model import RenderedUnit

Kind = Literal["share", "camera", "other"]
Tag = Literal["SCREEN", "TILE"]

_EMITTER_VERSION = "1.0.0"
_REREAD_BELOW = "1.0.0"
"""A page or stub an emitter below this wrote is read again once (``RecordingConverter.outdated``).  Raise it
only for a change worth reading every local recording again for, never above ``_EMITTER_VERSION``."""
_SELECTION_REVISION = 1  # ``-s<n>`` of the version: the profiles, the gate, the back-offs, the kind rules

WINDOW_MS = 300_000
"""One window unit of the page (spec 3.1, 2.2); the renderer cuts the recording at it."""
MARK_BELOW = 0.60
"""A printed row read under this confidence ends in `` [?]`` (S6 rule 10); the renderer prints it."""

_MAX_TICKS = 5_400  # 3 h of 2 s ticks: past it only the first 5,400 are read (S1 rule 3)
_PIECE_TICKS = 150  # 5 minutes of media time per piece (4.1, L7)
_MAX_READS = 840  # candidate reads of a whole recording (S3 rule 6); bounds the keyframes too (L2)
_BASE_S = 60.0
_TICK_S = 0.03
_READ_S = 0.8
_OCR_RUNS = 3  # concurrent OCR runs, candidates split by index modulo 3 (S5 rule 2)
_ROUND = 12  # candidates asked of the helpers at once; only those the one-at-a-time rule reaches are used
_DIFF_PAIRS = 2_000  # grid pairs per ``diff`` call

_CELL = 12  # a cell changed: luma difference over 12
_GATE_RECOGNISED = 5  # in 10,000ths of the mask: a recognised profile's gate is over 0.05 %
_GATE_GENERIC = 20  # in 10,000ths of the frame: the generic gate is over 0.2 %
_REVISIT = 20  # in 10,000ths of the mask: a revisit differs in 0.2 % of cells or fewer
_TEAMS_CONTENT_X = 0.872
_LABEL_BOX: Rect = (0.0, 0.96, 0.10, 1.0)
_MEET_CONTENT: Rect = (0.0, 0.12, 0.75, 1.0)
_TEAMS_EDGE = 10  # the pane edge: the luma step across x = 0.872 is 10 or more above the median column step
_MEET_EDGE = 20  # the content edge: the step across x = 0.75 is 20 or more above it
_T3_LINES = 5  # T3: 5 or more long content lines, or a static pane
_T3_STATIC = 1  # in 100ths of the mask: the pane is static at a median change of 1 % or less over 3 ticks
_R4_LINES = 2  # R4: 2 or more long lines in the mask
_R4_STATIC = 5  # in 100ths of the mask: and a median change of 5 % or less over 3 ticks
_OTHER_LINES = 5  # teams: 5 or more content lines and no sharer label is ``other``
_LONG_WORDS = 2  # a long line is not name-like and has 2 or more words or 12 or more characters
_LONG_CHARS = 12
_BACKOFF_TICKS = 5  # under a back-off a candidate is read only 10 s after the last read
_CATCH_UP = 4  # skipped candidates read backwards when a back-off ends
_MOTION_READS = 5  # share reads in a row with nothing new start the motion back-off
_NOT_LEVEL = 0.20  # generic camera: a row whose width / (height x characters) is under this is not printed
_SIMILAR = 0.72  # row identity: difflib ratio
_SHORTEST_MS = 4_000  # a row or a state on screen for less is not printed / is folded into the next
_NEW_STATE = 30  # in 100ths: a state opens past 30 % of characters changed
_LABEL_CHARS = 60
_LABEL_LETTERS = 6

_NAME_RE = re.compile(r"[A-Z][a-z'.-]*(?: [A-Z][a-z'.-]*){0,3}")  # a person's name as a label shows it
_TEAMS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")
_NOT_ALNUM_RE = re.compile(r"[^a-z0-9]")
_DIGIT_RE = re.compile(r"\d")
_LETTER_RE = re.compile(r"[^\W\d_]")
_QT_ATOMS = (b"moov", b"mdat", b"wide", b"free")

# Stub reasons and wordings.  Fixed: no file name, no path, no exception text.
_NOT_RECORDING = "not a recording on-device reading supports (MP4, M4V, MOV)"
_NOTHING = "recording has no picture and no sound"
_SOUND_ONLY = "recording has no picture; its speech is not read by this version"
_UNDECODABLE = "recording's picture cannot be decoded on this Mac (VP9 or AV1)"
_NO_TEXT = "no text read on screen; speech is not read by this version"
_UNDECODABLE_CODECS = frozenset({"vp9", "av1"})
_FRAME_FAILED = "a frame of the recording could not be read by on-device OCR"
_HELPER_ANSWER = "the media helper's answer does not match what it was asked"
_NOTE_FULL_FRAME = "fewer than 5 lines read in the content area; the keyframe is the full frame"
_NOTE_GENERIC = "layout not recognised; keyframes are full frames"
_NOTE_MOTION = (
    "the picture changes at every sample without new text (a video or an animation); "
    "read every 10 s from here"
)
_REASONS = {
    "teams": "the title card and the pane edge, read from the picture",
    "meet": "the content edge at x = 0.75, read from the picture",
    "generic": "no layout recognised in the picture",
}

_clock = time.monotonic
_T = TypeVar("_T")


class RecordingNotFinished(Exception):  # noqa: N818 - a signal, not an error: never a FAILED or cached result
    """The "not finished" signal (spec 4.1, S3 rule 7): the work allowance ran out with pieces left, or a
    piece passed its deadline and was discarded.  ``convert_file`` re-raises it ahead of its ``except
    Exception``; the cycle marks the row ``RECORDING_WAITS``.  ``done_ms`` of ``total_ms`` of media time are
    read and stored."""

    def __init__(self, *, done_ms: int, total_ms: int, timed_out: bool) -> None:
        """``timed_out``: a piece passed its deadline (one failed read of that piece), not the allowance."""
        super().__init__(f"recording read to {done_ms} of {total_ms} ms")
        self.done_ms = done_ms
        self.total_ms = total_ms
        self.timed_out = timed_out


class Allowance:
    """Seconds of recording work one run may spend (spec S0 rule 5); ``None`` reads every recording to the
    end."""

    def __init__(self, seconds: float | None) -> None:
        """Start the allowance with nothing spent."""
        self.seconds = seconds
        self.spent_s = 0.0

    @property
    def used_up(self) -> bool:
        """True once ``spent_s`` reaches ``seconds``; never for an allowance of None."""
        return self.seconds is not None and self.spent_s >= self.seconds


@contextlib.contextmanager
def work_allowance(seconds: float | None) -> Iterator[Allowance]:
    """Inside this block a recording's ``convert`` works pieces until the allowance is used up, finishes the
    piece in flight, and raises :class:`RecordingNotFinished` when pieces are left.  Outside any block, and
    with ``None``, it reads to the end (``materialise PATH``, tests)."""
    allowance = Allowance(seconds)
    token = _ALLOWANCE.set(allowance)
    try:
        yield allowance
    finally:
        _ALLOWANCE.reset(token)


_ALLOWANCE: contextvars.ContextVar[Allowance | None] = contextvars.ContextVar(
    "recording_allowance", default=None
)


@dataclass(frozen=True, slots=True)
class Row:
    """One on-screen row as S6 settled it.  A row never spans a change of kind: S6 splits it there.

    ``text`` is the printed reading (S6 rule 5) as OCR read it; the renderer cleans it (S9 rule 5).  ``tag``
    is how S6 rule 3 prints it in this kind of state.  On screen from ``start_ms`` (the first candidate that
    read it) to ``end_ms`` (the first later candidate that did not; the recording's read end when none).  The
    box is in fractions of the frame and orders rows by position; ``confidence`` is the printed reading's."""

    text: str
    tag: Tag
    start_ms: int
    end_ms: int
    x: float
    y: float
    w: float
    h: float
    confidence: float


@dataclass(frozen=True, slots=True)
class Keyframe:
    """A stored picture: JPEG bytes of tick ``ms`` (named ``tHHMMSS.jpg`` by the renderer)."""

    ms: int
    data: bytes


@dataclass(frozen=True, slots=True)
class Note:
    """A ``NOTE`` with one of the fixed wordings of 3.3 rule 5, at media time ``ms``.  The renderer adds the
    continuation, carry-over and limit notes itself."""

    ms: int
    text: str


@dataclass(frozen=True, slots=True)
class State:
    """One screen state (S6 rule 7): ``number`` prints as ``sNNN``; a revisit (rule 8) names the earliest
    state it shows again and has no keyframes; ``keyframes`` are K1 and K2 (rule 9); ``notes`` lie inside the
    state."""

    number: int
    kind: Kind
    start_ms: int
    end_ms: int
    label: str | None
    revisit_of: int | None
    keyframes: tuple[Keyframe, ...]
    notes: tuple[Note, ...]


@dataclass(frozen=True, slots=True)
class Reading:
    """Everything S1 to S6 settled about one recording; the renderer's whole input (a pure function of it).

    ``read_ms`` is the media time read (``duration_ms``, or 03:00:00 past ``_MAX_TICKS``).  ``screen_read_to``
    is ``(ms, later changes not read)`` once ``_MAX_READS`` cut the reading.  ``title_card`` holds tick 0's
    rows (``teams`` only).  ``names`` holds ``(first_ms, text, reads)`` of strip and label rows read at 3 or
    more candidates.  ``unprinted_rows`` counts rows read but on screen under 4 s (S6 rule 6)."""

    duration_ms: int
    read_ms: int
    width: int
    height: int
    created: str | None
    profile: str
    profile_reason: str
    label_rule: bool
    ocr_identity: str
    media_identity: str
    title_card: tuple[Row, ...]
    states: tuple[State, ...]
    rows: tuple[Row, ...]
    names: tuple[tuple[int, str, int], ...]
    unprinted_rows: int
    screen_read_to: tuple[int, int] | None


# ---------------------------------------------------------------------------------------------------------
# profiles, regions and grids
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Profile:
    """A layout (S3 rules 1 to 3): where the strip and the sharer label are, the mask the gate counts, and
    the crop of a ``share`` keyframe.  ``recognised`` profiles gate on 0.05 % of the mask and crop."""

    name: str
    strip_x: float | None
    label: bool
    include: tuple[Rect, ...]
    exclude: tuple[Rect, ...]
    crop: Rect | None
    crop_right: float | None

    @property
    def recognised(self) -> bool:
        return self.name != "generic"


_PROFILES = {
    "teams": _Profile(
        "teams",
        strip_x=_TEAMS_CONTENT_X,
        label=True,
        include=((0.0, 0.0, _TEAMS_CONTENT_X, 1.0),),
        exclude=(_LABEL_BOX,),
        crop=None,
        crop_right=_TEAMS_CONTENT_X,
    ),
    "meet": _Profile(
        "meet",
        strip_x=_MEET_CONTENT[2],
        label=False,
        include=(_MEET_CONTENT,),
        exclude=(),
        crop=_MEET_CONTENT,
        crop_right=None,
    ),
    "generic": _Profile(
        "generic", strip_x=None, label=False, include=(), exclude=(), crop=None, crop_right=None
    ),
}

_GRID_BYTES = GRID_W * GRID_H


def _cells(rect: Rect) -> tuple[int, int, int, int]:
    """The grid cells a rectangle touches: a cell is inside when any part of it is (as the helper's ``diff``
    counts a mask)."""
    x0, y0, x1, y1 = rect
    return (
        max(0, math.floor(x0 * GRID_W)),
        max(0, math.floor(y0 * GRID_H)),
        min(GRID_W, math.ceil(x1 * GRID_W)),
        min(GRID_H, math.ceil(y1 * GRID_H)),
    )


def _spans(include: Sequence[Rect], exclude: Sequence[Rect]) -> tuple[tuple[int, int], ...]:
    """(start, end) offsets into a grid of the runs of cells inside the mask, row by row: the included
    rectangles (the whole grid when there are none) less the excluded ones."""
    inside = bytearray([0 if include else 1]) * _GRID_BYTES
    for value, rects in ((1, include), (0, exclude)):
        for rect in rects:
            cx0, cy0, cx1, cy1 = _cells(rect)
            for y in range(cy0, cy1):
                inside[y * GRID_W + cx0 : y * GRID_W + cx1] = bytes([value]) * max(0, cx1 - cx0)
    out: list[tuple[int, int]] = []
    for y in range(GRID_H):
        x = 0
        while x < GRID_W:
            if inside[y * GRID_W + x]:
                start = x
                while x < GRID_W and inside[y * GRID_W + x]:
                    x += 1
                out.append((y * GRID_W + start, y * GRID_W + x))
            else:
                x += 1
    return tuple(out)


def _changed(a: bytes, b: bytes, spans: Sequence[tuple[int, int]]) -> int:
    """Cells under ``spans`` whose luma differs by more than ``_CELL`` between grids ``a`` and ``b``."""
    count = 0
    for start, end in spans:
        left, right = a[start:end], b[start:end]
        if left != right:
            count += sum(1 for p, q in zip(left, right, strict=True) if p - q > _CELL or q - p > _CELL)
    return count


def _edge(grid: bytes, x: float, step: int) -> bool:
    """True when the luma step across column ``x`` is ``step`` or more above the median column step (mean
    over the rows; S3 rule 1, ``v3`` 5.1 and 5.3)."""
    steps = [
        sum(abs(p - q) for p, q in zip(grid[c::GRID_W], grid[c - 1 :: GRID_W], strict=True))
        for c in range(1, GRID_W)
    ]
    at = math.floor(x * GRID_W)
    median = sorted(steps)[len(steps) // 2]
    return max(steps[at - 1], steps[min(at, len(steps) - 1)]) - median >= step * GRID_H


def _pack(grid: bytes) -> str:
    return base64.b64encode(zlib.compress(grid, 9)).decode("ascii")


def _unpack(text: str) -> bytes:
    grid = zlib.decompress(base64.b64decode(text))
    if len(grid) != _GRID_BYTES:
        raise ValueError("not a grid")
    return grid


# ---------------------------------------------------------------------------------------------------------
# lines, rows and the kind of a candidate
# ---------------------------------------------------------------------------------------------------------

_Region = Literal["content", "label", "strip"]


@dataclass(frozen=True, slots=True)
class _Seen:
    """One row OCR read at one candidate, inside one region (S5 rule 3)."""

    region: _Region
    text: str
    confidence: float
    x: float
    y: float
    w: float
    h: float

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def to_json(self) -> list[Any]:
        return [self.region, self.text, self.confidence, self.x, self.y, self.w, self.h]

    @classmethod
    def from_json(cls, item: list[Any]) -> _Seen:
        region, text, confidence, x, y, w, h = item
        return cls(region, str(text), float(confidence), float(x), float(y), float(w), float(h))


def _region(profile: _Profile, x: float, y: float, w: float, h: float) -> _Region:
    """S6 rule 1: by the centre of the box, the label box first, then the strip, else content."""
    cx, cy = x + w / 2, y + h / 2
    if profile.label and cx < _LABEL_BOX[2] and cy >= _LABEL_BOX[1]:
        return "label"
    if profile.strip_x is not None and cx >= profile.strip_x:
        return "strip"
    return "content"


def _kept(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """The lines the noise rule keeps (``ocr.text_rows`` applies the same rule)."""
    return [ln for ln in lines if text_rows([ln], width=1000, height=1000)]


def _rows(profile: _Profile, image: tuple[int, int, Sequence[OcrLine]]) -> list[_Seen]:
    """Rows per region (S5 rule 3): no row spans two regions."""
    width, height, lines = image
    out: list[_Seen] = []
    for region in ("content", "label", "strip"):
        mine = [ln for ln in lines if _region(profile, ln.x, ln.y, ln.w, ln.h) == region]
        for r in text_rows(mine, width=width, height=height):
            out.append(_Seen(region, r.text, r.confidence, r.x, r.y, r.w, r.h))
    return out


def _long(text: str) -> bool:
    """A long line: not name-like, and 2 or more words or 12 or more characters (``v3`` 5.1)."""
    words = text.split()
    return not _NAME_RE.fullmatch(" ".join(words)) and (
        len(words) >= _LONG_WORDS or len(" ".join(words)) >= _LONG_CHARS
    )


def _in_mask(profile: _Profile, ln: OcrLine) -> bool:
    cx, cy = ln.x + ln.w / 2, ln.y + ln.h / 2

    def within(r: Rect) -> bool:
        return r[0] <= cx < r[2] and r[1] <= cy < r[3]

    inside = any(within(r) for r in profile.include) if profile.include else True
    return inside and not any(within(r) for r in profile.exclude)


def _kind(profile: _Profile, lines: Sequence[OcrLine], *, edge: bool, change: int, cells: int) -> Kind:
    """S6 rule 2: T3 for ``teams``, R4 for every other profile; counts are of lines after the noise rule."""
    kept = _kept(lines)
    if profile.name == "teams":
        content = [ln for ln in kept if _region(profile, ln.x, ln.y, ln.w, ln.h) == "content"]
        label = any(_region(profile, ln.x, ln.y, ln.w, ln.h) == "label" for ln in kept)
        long = sum(1 for ln in content if _long(ln.text))
        static = change * 100 <= _T3_STATIC * cells
        if label and edge and (static or long >= _T3_LINES):
            return "share"
        if len(content) >= _OTHER_LINES and not label:
            return "other"
        return "camera"
    long = sum(1 for ln in kept if _in_mask(profile, ln) and _long(ln.text))
    return "share" if long >= _R4_LINES and change * 100 <= _R4_STATIC * cells else "camera"


def _normal(text: str) -> str:
    return _NOT_ALNUM_RE.sub("", text.lower())


def _digits(text: str) -> str:
    return "".join(_DIGIT_RE.findall(text))


def _place(a: _Seen, b: _Seen) -> bool:
    """Same place: vertical centres within one line height, horizontal extents overlapping."""
    return abs(a.cy - b.cy) <= min(a.h, b.h) and min(a.x + a.w, b.x + b.w) > max(a.x, b.x)


def _similar(a: str, b: str) -> bool:
    return (
        _digits(a) == _digits(b) and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= _SIMILAR
    )


def _box_changed(a: bytes, b: bytes, r: _Seen, s: _Seen) -> bool:
    """Any cell under the union of the two rows' boxes changed between grids ``a`` and ``b``."""
    x0, y0 = min(r.x, s.x), min(r.y, s.y)
    x1, y1 = max(r.x + r.w, s.x + s.w), max(r.y + r.h, s.y + s.h)
    cx0, cy0, cx1, cy1 = _cells((x0, y0, x1, y1))
    spans = [(y * GRID_W + cx0, y * GRID_W + cx1) for y in range(cy0, cy1) if cx1 > cx0]
    return _changed(a, b, spans) > 0


def _same(a: _Seen, b: _Seen, grids: tuple[bytes, bytes] | None) -> bool:
    """S6 rule 4: the same row at two candidates.  Without ``grids`` the pixel condition is not asked
    (the state rule compares texts at one place)."""
    if a.region != b.region or not _place(a, b):
        return False
    if _normal(a.text) == _normal(b.text):
        return True
    if not _similar(a.text, b.text):
        return False
    return grids is None or not _box_changed(grids[0], grids[1], a, b)


def _matches(prev: Sequence[_Seen], cur: Sequence[_Seen], grids: tuple[bytes, bytes]) -> list[int | None]:
    """For each row of ``cur`` the index of the row of ``prev`` it continues, or None.  Greedy in reading
    order: an equal normalised text first, then the highest ratio, then the nearest centre."""
    taken: set[int] = set()
    out: list[int | None] = []
    for row in cur:
        best: tuple[int, float, float, int] | None = None
        for i, old in enumerate(prev):
            if i in taken or not _same(old, row, grids):
                continue
            equal = 1 if _normal(old.text) == _normal(row.text) else 0
            ratio = difflib.SequenceMatcher(None, old.text, row.text, autojunk=False).ratio()
            rank = (equal, ratio, -abs(old.cy - row.cy), -i)
            if best is None or rank > best:
                best = rank
        if best is None:
            out.append(None)
        else:
            taken.add(-best[3])
            out.append(-best[3])
    return out


def _change_share(a: Sequence[_Seen], c: Sequence[_Seen]) -> tuple[int, int]:
    """S6 rule 7: (characters of rows of ``a`` not at ``c`` plus those of ``c`` not in ``a``, characters of
    both sets).  Strip rows are not part of a screen state."""
    a = [r for r in a if r.region != "strip"]
    c = [r for r in c if r.region != "strip"]
    gone = sum(len(r.text) for r in a if not any(_same(r, s, None) for s in c))
    new = sum(len(s.text) for s in c if not any(_same(r, s, None) for r in a))
    return gone + new, sum(len(r.text) for r in a) + sum(len(s.text) for s in c)


def _past_new_state(a: Sequence[_Seen], c: Sequence[_Seen]) -> bool:
    changed, total = _change_share(a, c)
    return changed * 100 > _NEW_STATE * total


def _median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0


def _lines_json(lines: Sequence[OcrLine]) -> list[list[Any]]:
    return [[ln.text, ln.confidence, ln.x, ln.y, ln.w, ln.h] for ln in lines]


def _lines_of(items: list[list[Any]]) -> tuple[OcrLine, ...]:
    return tuple(
        OcrLine(str(t), float(c), float(x), float(y), float(w), float(h)) for t, c, x, y, w, h in items
    )


def _dumps(doc: Mapping[str, Any]) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


# ---------------------------------------------------------------------------------------------------------
# reading one piece (S2 to S5, the online part of S6 the back-offs need)
# ---------------------------------------------------------------------------------------------------------


class _TimedOut(Exception):  # noqa: N818 - internal: a piece or the last pass passed its deadline
    pass


@dataclass(slots=True)
class _Carry:
    """What a piece hands the next one (4.1).  JSON round trip is exact, so a stored piece and a fresh one
    carry the same state."""

    profile: str
    reads_left: int
    last_candidate: bytes | None  # the gate compares each tick with this grid
    last_tick: bytes | None  # the previous tick's grid, for the median change over 3 ticks
    changes: list[int]  # the changed mask cells of the last two ticks against the tick before each; before
    # tick 0 nothing changed
    last_read: int  # tick of the last read candidate, -1 before the first
    last_read_grid: bytes | None
    last_kind: str | None
    last_rows: list[_Seen]
    open_rows: list[_Seen]  # the rows of the open state's first candidate
    mode: str  # "normal", "camera" or "motion"
    quiet: int  # share reads in a row that held nothing new
    skipped: list[tuple[int, bytes, int]]  # candidates skipped since the last read: tick, grid, change
    stopped: int | None  # tick of the last read once ``_MAX_READS`` ran out

    def to_json(self) -> dict[str, Any]:
        def grid(g: bytes | None) -> str | None:
            return None if g is None else _pack(g)

        return {
            "profile": self.profile,
            "reads_left": self.reads_left,
            "last_candidate": grid(self.last_candidate),
            "last_tick": grid(self.last_tick),
            "changes": self.changes,
            "last_read": self.last_read,
            "last_read_grid": grid(self.last_read_grid),
            "last_kind": self.last_kind,
            "last_rows": [r.to_json() for r in self.last_rows],
            "open_rows": [r.to_json() for r in self.open_rows],
            "mode": self.mode,
            "quiet": self.quiet,
            "skipped": [[t, _pack(g), c] for t, g, c in self.skipped],
            "stopped": self.stopped,
        }

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> _Carry:
        def grid(g: str | None) -> bytes | None:
            return None if g is None else _unpack(g)

        return cls(
            profile=str(doc["profile"]),
            reads_left=int(doc["reads_left"]),
            last_candidate=grid(doc["last_candidate"]),
            last_tick=grid(doc["last_tick"]),
            changes=[int(c) for c in doc["changes"]],
            last_read=int(doc["last_read"]),
            last_read_grid=grid(doc["last_read_grid"]),
            last_kind=doc["last_kind"],
            last_rows=[_Seen.from_json(r) for r in doc["last_rows"]],
            open_rows=[_Seen.from_json(r) for r in doc["open_rows"]],
            mode=str(doc["mode"]),
            quiet=int(doc["quiet"]),
            skipped=[(int(t), _unpack(g), int(c)) for t, g, c in doc["skipped"]],
            stopped=None if doc["stopped"] is None else int(doc["stopped"]),
        )


class _Work:
    """The helpers of one ``convert`` call, its scratch folder and the deadline of the stage in flight."""

    def __init__(self, src: Path, scratch: Path, ocr: OcrEngine, media: MediaEngine) -> None:
        self.src = src
        self.scratch = scratch
        self.ocr = ocr
        self.media = media
        self.deadline = math.inf
        self._folders = 0

    def left(self) -> float:
        """Seconds to the deadline; raises :class:`_TimedOut` past it (S3 rule 7)."""
        left = self.deadline - _clock()
        if left <= 0:
            raise _TimedOut
        return left

    def call(self, run: Callable[[float], _T]) -> _T:
        """``run(seconds left)``; a helper failure past the deadline is the deadline's, not the file's."""
        try:
            return run(self.left())
        except OcrError:  # MediaError included
            if _clock() >= self.deadline:
                raise _TimedOut from None
            raise

    def folder(self, stem: str) -> Path:
        self._folders += 1
        path = self.scratch / f"{stem}-{self._folders}"
        path.mkdir()
        return path

    def read(self, ticks: Sequence[int]) -> dict[int, tuple[int, int, tuple[OcrLine, ...]]]:
        """S4 and S5: full frames of ``ticks``, read by three concurrent OCR runs under the one deadline,
        merged by tick.  A frame Vision gave up on, or one it skipped, is a MediaError."""
        if not ticks:
            return {}
        out = self.folder("frames")
        frames = self.call(lambda left: self.media.frames(self.src, out=out, ticks=ticks, timeout=left))
        if [f.tick for f in frames] != list(ticks):
            raise MediaError(_HELPER_ANSWER)
        groups = [frames[i::_OCR_RUNS] for i in range(_OCR_RUNS)]
        folders = [self.folder("ocr") for group in groups if group]

        def run(left: float) -> list[list[tuple[OcrImage, ...]]]:
            with ThreadPoolExecutor(max_workers=_OCR_RUNS) as pool:
                futures = [
                    pool.submit(self.ocr.read, [f.path for f in group], work_dir=folder, budget_s=left)
                    for group, folder in zip([g for g in groups if g], folders, strict=True)
                ]
                return [future.result() for future in futures]

        results = self.call(run)
        merged: dict[int, tuple[int, int, tuple[OcrLine, ...]]] = {}
        for group, images in zip([g for g in groups if g], results, strict=True):
            for frame, (image, *_rest) in zip(group, images, strict=True):
                if image.error or image.skipped:
                    raise MediaError(_FRAME_FAILED)
                merged[frame.tick] = (image.width, image.height, image.lines)
        shutil.rmtree(out, ignore_errors=True)
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        return dict(sorted(merged.items()))


def _profile_of(
    lines: Sequence[OcrLine], grids: Sequence[bytes], *, check: Callable[[], object]
) -> tuple[str, str]:
    """S3 rule 1, from the bytes only: ``teams`` when tick 0 holds the title card and a tick of the first
    piece shows the pane edge, ``meet`` when one shows the x = 0.75 content edge, else ``generic``."""
    texts = [" ".join(ln.text.lower().split()) for ln in lines]
    card = "microsoft teams" in texts and any(_TEAMS_RE.search(ln.text) for ln in lines)
    found = "generic"
    if card:
        for grid in grids:
            check()
            if _edge(grid, _TEAMS_CONTENT_X, _TEAMS_EDGE):
                found = "teams"
                break
    if found == "generic":
        for grid in grids:
            check()
            if _edge(grid, _MEET_CONTENT[2], _MEET_EDGE):
                found = "meet"
                break
    return found, _REASONS[found]


class _Piece:
    """Reads one piece: its scan, gate and candidates, in tick order, with the back-offs of S3 rule 5."""

    def __init__(self, work: _Work, number: int, ticks: int, carry: _Carry | None) -> None:
        self.work = work
        self.number = number
        self.first = number * _PIECE_TICKS
        self.count = min(_PIECE_TICKS, ticks - self.first)
        self.carry = carry
        self.reads: list[dict[str, Any]] = []
        self.gated: list[int] = []
        self.unread = 0
        self.cache: dict[int, tuple[int, int, tuple[OcrLine, ...]]] = {}
        self.spans: tuple[tuple[int, int], ...] = ()
        self.cells = 0

    def run(self) -> bytes:
        work, started = self.work, _clock()
        work.deadline = started + _BASE_S + _TICK_S * self.count
        out = work.folder("scan")
        scan = work.call(
            lambda left: work.media.scan(
                work.src, out=out, timeout=left, first_tick=self.first, max_ticks=self.count
            )
        )
        data = scan.grids.read_bytes()
        if scan.first != self.first or scan.ticks != self.count or len(data) != self.count * _GRID_BYTES:
            raise MediaError(_HELPER_ANSWER)
        grids = [data[i * _GRID_BYTES : (i + 1) * _GRID_BYTES] for i in range(self.count)]
        shutil.rmtree(out, ignore_errors=True)
        if self.carry is None:
            self.cache = work.read([0])
            _w, _h, lines = self.cache[0]
            found, _reason = _profile_of(lines, grids, check=work.left)
            self.carry = _Carry(
                found, _MAX_READS, None, None, [0, 0], -1, None, None, [], [], "normal", 0, [], None
            )
        carry = self.carry
        profile = _PROFILES[carry.profile]
        self.spans = _spans(profile.include, profile.exclude)
        self.cells = sum(end - start for start, end in self.spans)
        gate = _GATE_RECOGNISED if profile.recognised else _GATE_GENERIC
        facts: dict[int, tuple[bytes, int]] = {}
        for i, grid in enumerate(grids):
            work.left()
            tick = self.first + i
            if carry.last_tick is not None:
                carry.changes = [*carry.changes, _changed(carry.last_tick, grid, self.spans)][-3:]
            carry.last_tick = grid
            change = _median(carry.changes)
            if carry.last_candidate is None or _changed(carry.last_candidate, grid, self.spans) * 10_000 > (
                gate * self.cells
            ):
                carry.last_candidate = grid
                self.gated.append(tick)
                facts[tick] = (grid, change)
            carry.changes = carry.changes[-2:]
        work.deadline = (
            started + _BASE_S + _TICK_S * self.count + _READ_S * min(len(self.gated), carry.reads_left)
        )
        self._candidates(profile, facts)
        doc = {
            "piece": self.number,
            "first": self.first,
            "ticks": self.count,
            "gated": self.gated,
            "reads": self.reads,
            "unread": self.unread,
            "carry": carry.to_json(),
        }
        return _dumps(doc)

    def _wants(self, tick: int) -> bool:
        assert self.carry is not None
        return self.carry.mode == "normal" or tick - self.carry.last_read >= _BACKOFF_TICKS

    def _plan(self, pending: Sequence[int]) -> list[int]:
        """The next candidates the current rule reads, as if it held: ``_ROUND`` at most, and the reads
        left."""
        assert self.carry is not None
        room = min(_ROUND, self.carry.reads_left)
        if self.carry.mode == "normal":
            return list(pending[:room])
        out: list[int] = []
        last = self.carry.last_read
        for tick in pending:
            if len(out) >= room:
                break
            if tick - last >= _BACKOFF_TICKS:
                out.append(tick)
                last = tick
        return out

    def _image(self, tick: int, pending: Sequence[int]) -> tuple[int, int, tuple[OcrLine, ...]]:
        if tick not in self.cache:
            self.cache.update(self.work.read([t for t in self._plan(pending) if t not in self.cache]))
        return self.cache.pop(tick)

    def _candidates(self, profile: _Profile, facts: dict[int, tuple[bytes, int]]) -> None:
        carry = self.carry
        assert carry is not None
        pending = list(self.gated)
        while pending:
            self.work.left()
            tick = pending[0]
            self._due(profile, tick - 1)
            if carry.reads_left <= 0:
                self.unread += len(pending)
                break
            if not self._wants(tick):
                grid, change = facts[tick]
                carry.skipped = [*carry.skipped, (tick, grid, change)][-_CATCH_UP:]
                pending.pop(0)
                continue
            image = self._image(tick, pending)
            pending.pop(0)
            grid, change = facts[tick]
            self._read(profile, tick, image, grid, change)
        self._due(profile, self.first + self.count - 1)

    def _due(self, profile: _Profile, upto: int) -> None:
        """Under a back-off, once 10 s passed since the last read with candidates skipped, read the latest
        of them: a screen that changed on a skipped tick and then held still passes the gate no more, and
        would never be read.  Its catch-up finds where the change began."""
        carry = self.carry
        assert carry is not None
        while (
            carry.mode != "normal"
            and carry.skipped
            and carry.reads_left > 0
            and carry.last_read + _BACKOFF_TICKS <= upto
        ):
            self.work.left()
            tick, grid, change = carry.skipped.pop()
            image = self.cache.pop(tick) if tick in self.cache else self.work.read([tick])[tick]
            self._read(profile, tick, image, grid, change)

    def _record(
        self,
        profile: _Profile,
        tick: int,
        image: tuple[int, int, tuple[OcrLine, ...]],
        grid: bytes,
        change: int,
    ) -> tuple[Kind, list[_Seen], dict[str, Any]]:
        width, height, lines = image
        edge = profile.name == "teams" and _edge(grid, _TEAMS_CONTENT_X, _TEAMS_EDGE)
        kind = _kind(profile, lines, edge=edge, change=change, cells=self.cells)
        rows = _rows(profile, image)
        record = {
            "tick": tick,
            "size": [width, height],
            "lines": _lines_json(lines),
            "grid": _pack(grid),
            "edge": edge,
            "change": change,
            "cells": self.cells,
            "motion": False,
        }
        return kind, rows, record

    def _news(self, kind: Kind, rows: list[_Seen], grid: bytes) -> tuple[bool, bool]:
        """(opens a state, holds a row the last read did not), against the carried state."""
        carry = self.carry
        assert carry is not None
        if carry.last_kind is None or carry.last_kind != kind:
            return True, True
        assert carry.last_read_grid is not None
        before = [r for r in carry.last_rows if r.region != "strip"]
        mine = [r for r in rows if r.region != "strip"]
        new_row = any(m is None for m in _matches(before, mine, (carry.last_read_grid, grid)))
        return _past_new_state(carry.open_rows, rows), new_row

    def _read(
        self,
        profile: _Profile,
        tick: int,
        image: tuple[int, int, tuple[OcrLine, ...]],
        grid: bytes,
        change: int,
    ) -> None:
        """Commit the read of ``tick``, after the backward catch-up a back-off ending asks for."""
        carry = self.carry
        assert carry is not None
        kind, rows, record = self._record(profile, tick, image, grid, change)
        opens, new_row = self._news(kind, rows, grid)
        ends = (carry.mode == "camera" and kind == "share") or (carry.mode == "motion" and (opens or new_row))
        before: list[tuple[int, Kind, list[_Seen], dict[str, Any], bytes]] = []
        if ends and carry.skipped:
            back = list(reversed(carry.skipped))[: max(0, carry.reads_left - 1)]
            images = self.work.read(sorted(t for t, _g, _c in back))
            for t, g, c in back:
                k, r, rec = self._record(profile, t, images[t], g, c)
                before.append((t, k, r, rec, g))
                if carry.mode == "camera" and k != "share":
                    break
                if carry.mode == "motion" and not any(self._news(k, r, g)):
                    break
        carry.skipped = []
        for t, k, r, rec, g in sorted(before, key=lambda item: item[0]):
            self._commit(t, k, r, rec, g)
        self._commit(tick, kind, rows, record, grid)

    def _commit(self, tick: int, kind: Kind, rows: list[_Seen], record: dict[str, Any], grid: bytes) -> None:
        carry = self.carry
        assert carry is not None
        opens, new_row = self._news(kind, rows, grid)
        if opens:
            carry.open_rows = rows
        if kind != "share":
            carry.mode, carry.quiet = "camera", 0
        elif opens or new_row:
            carry.mode, carry.quiet = "normal", 0
        else:
            carry.quiet += 1
            if carry.quiet >= _MOTION_READS and carry.mode != "motion":
                carry.mode = "motion"
                record["motion"] = True
        carry.last_read, carry.last_read_grid, carry.last_kind, carry.last_rows = tick, grid, kind, rows
        carry.reads_left -= 1
        if carry.reads_left == 0:
            carry.stopped = tick
        self.reads.append(record)


# ---------------------------------------------------------------------------------------------------------
# S6 over every stored piece
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Cand:
    """One read candidate as S6 sees it, rebuilt from a stored piece."""

    tick: int
    kind: Kind
    rows: list[_Seen]
    grid: bytes
    motion: bool

    @property
    def ms(self) -> int:
        return self.tick * STEP_MS


@dataclass(slots=True)
class _Track:
    """One row followed across consecutive read candidates (S6 rule 4): indexes into the candidates and
    the row read at each."""

    region: _Region
    kind: Kind
    reads: list[tuple[int, _Seen]]


def _candidates(profile: _Profile, pieces: Sequence[dict[str, Any]]) -> list[_Cand]:
    out: dict[int, _Cand] = {}
    for piece in pieces:
        for rec in piece["reads"]:
            width, height = (int(v) for v in rec["size"])
            lines = _lines_of(rec["lines"])
            kind = _kind(
                profile, lines, edge=bool(rec["edge"]), change=int(rec["change"]), cells=int(rec["cells"])
            )
            tick = int(rec["tick"])
            rows = _rows(profile, (width, height, lines))
            out[tick] = _Cand(tick, kind, rows, _unpack(rec["grid"]), bool(rec["motion"]))
    return [out[t] for t in sorted(out)]


def _tracks(cands: Sequence[_Cand], profile: _Profile) -> list[_Track]:
    """Rows followed across consecutive candidates.  A row never spans a change of how it prints (S6 rule 3):
    under ``teams`` a content row is SCREEN in a share and TILE in a camera state, so a change of kind starts
    it again; under every other profile a content row prints SCREEN in both, so it carries across the change
    and a state folded into the next (rule 7) does not remove and re-add the same row at one tick."""
    tracks: list[_Track] = []
    live: list[_Track] = []  # the track of each row of the previous candidate
    for i, cand in enumerate(cands):
        prev = cands[i - 1] if i else None
        found: list[int | None]
        if prev is None:
            found = [None] * len(cand.rows)
        else:
            found = _matches(prev.rows, cand.rows, (prev.grid, cand.grid))
            if prev.kind != cand.kind:
                found = [
                    at
                    if at is not None
                    and _tag(profile, prev.kind, row.region) == _tag(profile, cand.kind, row.region)
                    else None
                    for row, at in zip(cand.rows, found, strict=True)
                ]
        now: list[_Track] = []
        for row, at in zip(cand.rows, found, strict=True):
            if at is None:
                track = _Track(row.region, cand.kind, [])
                tracks.append(track)
            else:
                track = live[at]
            track.reads.append((i, row))
            now.append(track)
        live = now
    return tracks


def _reading_of(track: _Track) -> tuple[str, _Seen]:
    """S6 rule 5: the most frequent exact string; ties to the higher confidence, then the earlier read.
    The row returned is the first read of that string, with the highest confidence it was read at."""
    counts = Counter(row.text for _i, row in track.reads)
    best = max(
        counts,
        key=lambda text: (
            counts[text],
            max(row.confidence for _i, row in track.reads if row.text == text),
            -min(i for i, row in track.reads if row.text == text),
        ),
    )
    first = next(row for _i, row in track.reads if row.text == best)
    confidence = max(row.confidence for _i, row in track.reads if row.text == best)
    return best, _Seen(first.region, best, confidence, first.x, first.y, first.w, first.h)


def _tag(profile: _Profile, kind: Kind, region: _Region) -> Tag | None:
    """S6 rule 3: how a row of this region prints in this kind of state; strip rows never print."""
    if region == "strip":
        return None
    if region == "label" or (profile.name == "teams" and kind != "share"):
        return "TILE"
    return "SCREEN"


def _level(row: _Seen, width: int, height: int) -> bool:
    """Generic camera rows: the box is at least ``_NOT_LEVEL`` x characters as wide as it is high."""
    return row.w * width >= _NOT_LEVEL * row.h * height * max(1, len(row.text))


def _states(cands: Sequence[_Cand], gated: set[int], read_ms: int) -> list[tuple[int, int, int]]:
    """S6 rule 7: (index of the first candidate, start ms, end ms) of each state, a state shorter than 4 s
    folded into the next one (its time joins that state; that state's first candidate stays its own)."""
    starts = [0]
    for i in range(1, len(cands)):
        first = cands[starts[-1]]
        kind_changed = cands[i].kind != cands[i - 1].kind
        if kind_changed or (
            _past_new_state(first.rows, cands[i].rows)
            and (
                cands[i].tick + 1 not in gated
                or i + 1 >= len(cands)
                or _past_new_state(first.rows, cands[i + 1].rows)
            )
        ):
            starts.append(i)
    out: list[tuple[int, int, int]] = []
    carried: int | None = None
    for k, s in enumerate(starts):
        start = carried if carried is not None else cands[s].ms
        nxt = cands[starts[k + 1]].ms if k + 1 < len(starts) else read_ms
        if k + 1 < len(starts) and nxt - cands[s].ms < _SHORTEST_MS:
            carried = start
            continue
        carried = None
        out.append((s, start, nxt))
    return [
        (s, start, out[k + 1][1] if k + 1 < len(out) else read_ms) for k, (s, start, _e) in enumerate(out)
    ]


def _label(rows: Sequence[_Seen]) -> str | None:
    """3.3 rule 3: the tallest content row read at confidence 1.0 with six or more letters, top-most on a
    tie, cut at 60 characters; a ``"`` read on screen is printed ``'``."""
    found: list[tuple[float, float, float, str]] = []
    for row in rows:
        text = " ".join(row.text.split()).replace('"', "'")[:_LABEL_CHARS].strip()
        if row.confidence >= 1.0 and len(_LETTER_RE.findall(text)) >= _LABEL_LETTERS:
            found.append((-row.h, row.y, row.x, text))
    return min(found)[3] if found else None


class _Settle:
    """S6 over every candidate of a recording, then the keyframes (S4's last call): the :class:`Reading`."""

    def __init__(
        self, work: _Work, profile: _Profile, pieces: Sequence[dict[str, Any]], read_ms: int
    ) -> None:
        self.work = work
        self.profile = profile
        self.read_ms = read_ms
        self.cands = _candidates(profile, pieces)
        self.gated = {int(t) for piece in pieces for t in piece["gated"]}
        self.spans = _spans(profile.include, profile.exclude)
        self.cells = sum(end - start for start, end in self.spans)

    def end_of(self, track: _Track) -> int:
        last = track.reads[-1][0]
        return self.cands[last + 1].ms if last + 1 < len(self.cands) else self.read_ms

    def revisits(self, states: Sequence[tuple[int, int, int]]) -> list[int | None]:
        """S6 rule 8 through the helper's ``diff``: the earliest earlier state whose first grid differs in
        0.2 % of mask cells or fewer, and whose first rows the state rule would not call changed (a revisit
        prints no rows, so two slides of one template that differ in a few glyphs must not count as one).
        The deadline is checked between states."""
        out: list[int | None] = [None] * len(states)
        if len(states) < 2:
            return out
        folder = self.work.folder("states")
        grids = folder / "grids.bin"
        grids.write_bytes(b"".join(self.cands[s].grid for s, _a, _b in states))
        pairs = [(j, i) for i in range(1, len(states)) for j in range(i)]
        counts: dict[tuple[int, int], int] = {}
        for at in range(0, len(pairs), _DIFF_PAIRS):
            chunk = pairs[at : at + _DIFF_PAIRS]

            def diff(left: float, chunk: list[tuple[int, int]] = chunk) -> list[int]:
                return self.work.media.diff(
                    grids,
                    pairs=chunk,
                    timeout=left,
                    include=self.profile.include,
                    exclude=self.profile.exclude,
                    threshold=_CELL,
                )

            found = self.work.call(diff)
            if len(found) != len(chunk):
                raise MediaError(_HELPER_ANSWER)
            counts.update(zip(chunk, found, strict=True))
        shutil.rmtree(folder, ignore_errors=True)
        for i in range(1, len(states)):
            self.work.left()
            rows = self.cands[states[i][0]].rows
            for j in range(i):
                same_text = not _past_new_state(self.cands[states[j][0]].rows, rows)
                if same_text and counts[(j, i)] * 10_000 <= _REVISIT * self.cells:
                    earlier = out[j]
                    out[i] = j if earlier is None else earlier
                    break
        return out

    def run(
        self, info_size: tuple[int, int]
    ) -> tuple[tuple[Row, ...], tuple[State, ...], tuple[Row, ...], tuple[tuple[int, str, int], ...], int]:
        width, height = info_size
        cands, profile = self.cands, self.profile
        tracks = _tracks(cands, profile)
        states = _states(cands, self.gated, self.read_ms)
        self.work.deadline = _clock() + _BASE_S + _READ_S * 2 * len(states)
        revisit = self.revisits(states)

        settled: list[tuple[_Track, Tag, _Seen, int, int]] = []  # track, tag, reading, start, end
        for track in tracks:
            tag = _tag(profile, track.kind, track.region)
            if tag is None:
                continue
            _text, reading = _reading_of(track)
            if profile.name != "teams" and track.kind == "camera" and not _level(reading, width, height):
                continue
            settled.append((track, tag, reading, cands[track.reads[0][0]].ms, self.end_of(track)))
        keyframes: dict[int, list[int]] = {}
        for k, (s, start, end) in enumerate(states):
            self.work.left()
            if revisit[k] is not None:
                continue
            ticks = [cands[s].tick]
            added = [
                cands[track.reads[0][0]].tick
                for track, _tag, _r, t0, t1 in settled
                if start <= t0 < end and t1 - t0 >= _SHORTEST_MS and track.reads[0][0] != s
            ]
            if added:
                ticks.append(max(added))
            keyframes[k] = ticks
        stored = {t for ticks in keyframes.values() for t in ticks}
        rows: list[Row] = []
        unprinted = 0
        for track, tag, reading, t0, t1 in settled:
            if t1 - t0 < _SHORTEST_MS and not any(cands[i].tick in stored for i, _r in track.reads):
                unprinted += 1
                continue
            rows.append(
                Row(reading.text, tag, t0, t1, reading.x, reading.y, reading.w, reading.h, reading.confidence)
            )
        if not rows:
            raise UnreadableSourceError(_NO_TEXT)
        pictures = self.pictures(states, keyframes)
        built: list[State] = []
        for k, (s, start, end) in enumerate(states):
            kind, seen = cands[s].kind, revisit[k]
            notes = [Note(c.ms, _NOTE_MOTION) for c in cands if c.motion and start <= c.ms < end]
            if revisit[k] is None:
                if profile.name == "generic":
                    notes.append(Note(cands[s].ms, _NOTE_GENERIC))
                elif profile.name == "teams" and kind == "camera":
                    notes.append(Note(cands[s].ms, _NOTE_FULL_FRAME))
            own = [
                reading for track, tag, reading, t0, _t1 in settled if tag == "SCREEN" and start <= t0 < end
            ]
            built.append(
                State(
                    number=k + 1,
                    kind=kind,
                    start_ms=start,
                    end_ms=end,
                    label=_label(own),
                    revisit_of=None if seen is None else seen + 1,
                    keyframes=tuple(Keyframe(t * STEP_MS, pictures[t]) for t in keyframes.get(k, [])),
                    notes=tuple(sorted(notes, key=lambda n: (n.ms, n.text))),
                )
            )
        title: tuple[Row, ...] = ()
        if profile.name == "teams" and cands and cands[0].tick == 0:
            end0 = cands[1].ms if len(cands) > 1 else self.read_ms
            title = tuple(
                Row(r.text, "SCREEN", 0, end0, r.x, r.y, r.w, r.h, r.confidence) for r in cands[0].rows
            )
        names: dict[str, tuple[int, int]] = {}
        for track in tracks:
            if track.region in {"strip", "label"}:
                text, _reading = _reading_of(track)
                first, reads = names.get(text, (cands[track.reads[0][0]].ms, 0))
                names[text] = (min(first, cands[track.reads[0][0]].ms), reads + len(track.reads))
        roster = tuple(sorted((first, text, reads) for text, (first, reads) in names.items() if reads >= 3))
        rows.sort(key=lambda r: (r.start_ms, r.y, r.x, r.text))
        return title, tuple(built), tuple(rows), roster, unprinted

    def pictures(
        self, states: Sequence[tuple[int, int, int]], keyframes: Mapping[int, list[int]]
    ) -> dict[int, bytes]:
        """S4's last call: the content crop for a ``share`` state of a recognised profile, else the full
        frame (ruling 3)."""
        cropped = sorted(
            {t for k, ticks in keyframes.items() if self.cands[states[k][0]].kind == "share" for t in ticks}
            if self.profile.recognised
            else set()
        )
        full = sorted({t for ticks in keyframes.values() for t in ticks} - set(cropped))
        out: dict[int, bytes] = {}
        for ticks, crop in ((cropped, True), (full, False)):
            if not ticks:
                continue
            folder = self.work.folder("keyframes")

            def run(
                left: float, ticks: list[int] = ticks, crop: bool = crop, folder: Path = folder
            ) -> list[Frame]:
                return self.work.media.frames(
                    self.work.src,
                    out=folder,
                    ticks=ticks,
                    timeout=left,
                    crop=self.profile.crop if crop else None,
                    crop_right=self.profile.crop_right if crop else None,
                )

            frames = self.work.call(run)
            if [f.tick for f in frames] != ticks:
                raise MediaError(_HELPER_ANSWER)
            for frame in frames:
                out[frame.tick] = frame.path.read_bytes()
            shutil.rmtree(folder, ignore_errors=True)
        return out


def _container(head: bytes, name: str) -> bool:
    """S1 rule 1: bytes 4 to 7 are ``ftyp``, or for a ``.mov`` a QuickTime top-level atom."""
    return head[4:8] == b"ftyp" or (name.lower().endswith(".mov") and head[4:8] in _QT_ATOMS)


def _piece_doc(data: bytes | None) -> dict[str, Any] | None:
    """A stored piece, or None when it cannot be read back (it is then read again)."""
    if data is None:
        return None
    try:
        doc = json.loads(data)
        _Carry.from_json(doc["carry"])
        for rec in doc["reads"]:
            _unpack(rec["grid"])
    except (ValueError, KeyError, TypeError, zlib.error):
        return None
    return doc if isinstance(doc, dict) else None


class RecordingConverter:
    """``.mp4``, ``.m4v``, ``.mov`` -> an index unit and one unit per five-minute window (spec section 3)."""

    converter_id = "recording-av"
    extensions: tuple[str, ...] = (".m4v", ".mov", ".mp4")

    def __init__(
        self,
        cfg: ConvertConfig,
        ocr: OcrEngine,
        media: MediaEngine,
        *,
        pieces: PieceStore | None = None,
        label_rule: bool = False,
    ) -> None:
        """Bind the cycle's engines; ``pieces`` None reads every recording in one pass and stores nothing;
        ``label_rule``: a ``[policy]`` label rule is active (ruling 2: the index carries its NOTE)."""
        self._cfg = cfg
        self._ocr = ocr
        self._media = media
        self._pieces = pieces
        self._label_rule = label_rule

    def version(self) -> str:
        """``<emitter>+<ocr identity>+<media identity>-s<selection revision>`` (spec section 4)."""
        return f"{_EMITTER_VERSION}+{self._ocr.identity}+{self._media.identity}-s{_SELECTION_REVISION}"

    def options(self) -> Mapping[str, OptionValue]:
        """Every constant of spec 2.2 that can change a page, the OCR options and ``max_page_bytes``."""
        return {
            "max_page_bytes": self._cfg.max_page_bytes,
            **_OCR_OPTIONS,
            "recording_step_ms": STEP_MS,
            "recording_grid": f"{GRID_W}x{GRID_H}",
            "recording_jpeg_quality": JPEG_QUALITY,
            "recording_max_ticks": _MAX_TICKS,
            "recording_piece_ticks": _PIECE_TICKS,
            "recording_window_ms": WINDOW_MS,
            "recording_profiles": ",".join(_PROFILES),
            "recording_teams_content_x": _TEAMS_CONTENT_X,
            "recording_label_box": ",".join(str(v) for v in _LABEL_BOX),
            "recording_meet_content": ",".join(str(v) for v in _MEET_CONTENT),
            "recording_teams_edge": _TEAMS_EDGE,
            "recording_meet_edge": _MEET_EDGE,
            "recording_cell": _CELL,
            "recording_gate_recognised": _GATE_RECOGNISED,
            "recording_gate_generic": _GATE_GENERIC,
            "recording_t3": f"{_T3_LINES} lines or static {_T3_STATIC}; other {_OTHER_LINES}",
            "recording_r4": f"{_R4_LINES} lines, static {_R4_STATIC}",
            "recording_long_line": f"{_LONG_WORDS} words or {_LONG_CHARS} characters, {_NAME_RE.pattern}",
            "recording_backoff": f"{_BACKOFF_TICKS} ticks, catch-up {_CATCH_UP}, motion {_MOTION_READS}",
            "recording_not_level": _NOT_LEVEL,
            "recording_similar": _SIMILAR,
            "recording_shortest_ms": _SHORTEST_MS,
            "recording_new_state": _NEW_STATE,
            "recording_revisit": _REVISIT,
            "recording_mark": MARK_BELOW,
            "recording_label": f"{_LABEL_CHARS} characters, {_LABEL_LETTERS} letters",
            "recording_keyframes": "share: profile crop; camera, other, generic: full frame",
            "recording_limit": f"{_BASE_S}+{_TICK_S}/tick+{_READ_S}/read, {_MAX_READS} reads",
            "recording_ocr_runs": _OCR_RUNS,
        }

    @property
    def outdated_key(self) -> str:
        """``<emitter><<floor>|<cue identity or ->|<speech identity or ->`` (spec section 4)."""
        return f"{_EMITTER_VERSION}<{_REREAD_BELOW}|-|-"

    def outdated(self, produced: str, reason: str | None = None) -> bool:
        """True when a page or stub made under ``produced`` is worth reading the file again for: an emitter
        below ``_REREAD_BELOW``.  P1 has no speaker cue and no speech engine, so nothing else is; never for a
        version that cannot be read or an emitter at or above the running one."""
        emitter, running, floor = _emitter(produced), _emitter(_EMITTER_VERSION), _emitter(_REREAD_BELOW)
        if emitter is None or running is None or floor is None:
            return False
        return emitter < min(floor, running)

    def _key(self) -> str:
        return f"{_PIECE_TICKS}|{self.version()}|{options_hash(self.options())}"

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """The units of section 3, or UnreadableSourceError with a fixed stub wording, MediaError (a helper
        failure, a frame Vision gave up on) or :class:`RecordingNotFinished`."""
        from agentsync.convert.recording_page import render  # noqa: PLC0415 - it imports this module

        return render(self._reading(src, name=name), max_page_bytes=self._cfg.max_page_bytes)

    def _reading(self, src: Path, *, name: str) -> Reading:
        """What S1 to S6 settle about the staged recording ``src``; ``convert`` renders it.  ``name`` is read
        for its suffix only (a ``.mov`` may open with another QuickTime atom); it reaches no output."""
        with src.open("rb") as fh:
            head = fh.read(12)
        if not _container(head, name):
            raise UnreadableSourceError(_NOT_RECORDING)
        sha = sha256_file(src)
        with tempfile.TemporaryDirectory(dir=src.parent, prefix=".media-") as tmp:
            work = _Work(src, Path(tmp), self._ocr, self._media)
            info = self._media.info(src, timeout=_BASE_S)
            if info.picture is None:
                raise UnreadableSourceError(_SOUND_ONLY if info.audio else _NOTHING)
            if info.picture.lower() in _UNDECODABLE_CODECS:
                raise UnreadableSourceError(_UNDECODABLE)
            ticks = min(info.duration_ms // STEP_MS + 1, _MAX_TICKS)
            read_ms = (
                info.duration_ms if info.duration_ms // STEP_MS + 1 <= _MAX_TICKS else _MAX_TICKS * STEP_MS
            )
            count = math.ceil(ticks / _PIECE_TICKS)
            pieces = self._read_pieces(work, sha, ticks, count, read_ms)
            carry = _Carry.from_json(pieces[-1]["carry"])
            profile = _PROFILES[carry.profile]
            try:
                title, states, rows, names, unprinted = _Settle(work, profile, pieces, read_ms).run(
                    (info.width, info.height)
                )
            except _TimedOut:
                raise RecordingNotFinished(done_ms=read_ms, total_ms=read_ms, timed_out=True) from None
            unread = sum(int(piece["unread"]) for piece in pieces)
            stopped = carry.stopped
            reading = Reading(
                duration_ms=info.duration_ms,
                read_ms=read_ms,
                width=info.width,
                height=info.height,
                created=info.created,
                profile=profile.name,
                profile_reason=_REASONS[profile.name],
                label_rule=self._label_rule,
                ocr_identity=self._ocr.identity,
                media_identity=self._media.identity,
                title_card=title,
                states=states,
                rows=rows,
                names=names,
                unprinted_rows=unprinted,
                screen_read_to=None if not unread or stopped is None else (stopped * STEP_MS, unread),
            )
        return reading

    def _read_pieces(
        self, work: _Work, sha: str, ticks: int, count: int, read_ms: int
    ) -> list[dict[str, Any]]:
        """Every piece, from the store when it holds it under this key, else read now.  Inside a
        :func:`work_allowance` a used-up allowance stops before the next piece to read; a piece past its
        deadline is discarded."""
        key, allowance = self._key(), _ALLOWANCE.get()
        out: list[dict[str, Any]] = []
        carry: _Carry | None = None
        for n in range(count):
            done_ms = min(n * _PIECE_TICKS * STEP_MS, read_ms)
            doc = _piece_doc(self._pieces.load(sha, n, key)) if self._pieces is not None else None
            if doc is None:
                if self._pieces is not None and allowance is not None and allowance.used_up:
                    raise RecordingNotFinished(done_ms=done_ms, total_ms=read_ms, timed_out=False)
                started = _clock()
                try:
                    data = _Piece(work, n, ticks, carry).run()
                except _TimedOut:
                    raise RecordingNotFinished(done_ms=done_ms, total_ms=read_ms, timed_out=True) from None
                finally:
                    if allowance is not None:
                        allowance.spent_s += _clock() - started
                doc = json.loads(data)  # the same round trip a stored piece takes
                if self._pieces is not None:
                    self._pieces.save(sha, n, key, data)
                    self._pieces.set_progress(
                        sha, done_ms=min((n + 1) * _PIECE_TICKS * STEP_MS, read_ms), total_ms=read_ms
                    )
            out.append(doc)
            carry = _Carry.from_json(doc["carry"])
        return out
