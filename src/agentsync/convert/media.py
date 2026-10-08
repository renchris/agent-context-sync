"""The on-device media helper (``media_frames.swift``): build, trust, probe and run (spec S1, S2, S4, S6 rule
8).

Built, trusted, probed and pruned as the OCR helper is, by the same functions (``convert/ocr.py``, which take
the :data:`MEDIA` description), by ``python -I -m agentsync.convert.media`` from ``scripts/install.sh`` and
never by a sync, ``status`` or doctor.  It lives in ``<cache_dir>/media/``.  It reads a recording with
AVFoundation and answers one JSON document per call; exit 3 with a message on stderr is a failure.  The
protocol is pinned by ``tests/media_kit.py``: ``--version``, ``info``, ``scan``, ``frames``, ``diff``
(``pills`` and ``audio`` arrive with P3).

:func:`probe` and :func:`engine` only look.  A helper error, a time-out or an answer that is not the expected
JSON is a :class:`MediaError`, which is an ``OcrError``: ``convert_file`` then gives the recording the ``no
converter`` refusal it has without engines, and caches nothing.

Switches: ``[convert] recordings = false``, and everything that switches OCR off (``AGENTSYNC_OCR=0``,
``[convert] ocr = false``, no macOS), since a recording is read through OCR (spec S0 rule 1).
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert import ocr
from agentsync.convert.ocr import OcrError

log = logging.getLogger(__name__)

STEP_MS = 2000  # one tick every 2 s of media time (spec 2.2)
GRID_W, GRID_H = 320, 180  # one box-averaged luma grid per tick, 57,600 bytes
JPEG_QUALITY = 0.7

_SOURCE = "media_frames.swift"
_VERSION_TIMEOUT_S = 5.0
_GRIDS = "grids.bin"
_CREATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class MediaError(OcrError):
    """The media helper could not be built, may not be run, failed, ran out of time, or answered with
    something that is not the expected JSON.  Its text never holds a path."""


MEDIA = ocr.Helper(
    "media helper",
    "media",
    "agentsync-media",
    functools.partial(ocr._source, _SOURCE),
    MediaError,
    "the media helper needs macOS",
)
"""What the shared build, trust and run functions of ``convert/ocr.py`` need to know about this helper."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """What ``info`` read from the container: length, picture size, codec (None: no picture track), sound, and
    the container's creation time in UTC (``YYYY-MM-DDTHH:MM:SSZ``) or None."""

    duration_ms: int
    width: int
    height: int
    picture: str | None
    audio: bool
    created: str | None


@dataclass(frozen=True, slots=True)
class Scan:
    """What ``scan`` wrote: ``grids`` holds ``ticks`` grids of ``GRID_W x GRID_H`` bytes from tick ``first``,
    tick ``k`` at media time ``k x step_ms``."""

    grids: Path
    step_ms: int
    first: int
    ticks: int


@dataclass(frozen=True, slots=True)
class Frame:
    """One JPEG ``frames`` wrote: ``path`` is ``<out>/tHHMMSS.jpg`` of tick ``tick`` (media time ``ms``)."""

    tick: int
    ms: int
    path: Path
    width: int
    height: int


Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in fractions of the frame, origin top-left


def _bad() -> MediaError:
    return MediaError("the media helper's answer is not the expected JSON")


def _doc(stdout: bytes) -> dict[str, object]:
    try:
        doc = json.loads(stdout)
    except ValueError:  # also bytes that are not UTF-8
        raise _bad() from None
    if not isinstance(doc, dict):
        raise _bad()
    return doc


def _count(value: object, *, least: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise _bad()
    return value


def _list(value: object, length: int) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) != length or not all(isinstance(v, dict) for v in value):
        raise _bad()
    return value


def _hms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600:02d}{s // 60 % 60:02d}{s % 60:02d}"


def _rect(r: Rect) -> str:
    return ",".join(repr(float(v)) for v in r)


class MediaEngine:
    """A built media helper that answered ``--version``."""

    def __init__(self, helper: Path, *, name: str, helper_version: str) -> None:
        """Bind ``helper``; the rest is what its ``--version`` printed."""
        self.helper = helper
        self.name = name
        self.helper_version = helper_version

    @property
    def identity(self) -> str:
        """Producer identity for the recording converter's version: ``media-<engine>-h<helper version>``."""
        return f"media-{self.name}-h{self.helper_version}"

    @property
    def description(self) -> str:
        """One line for doctor."""
        return f"{self.name}, helper {self.helper_version}"

    def _run(self, args: Sequence[str], *, timeout: float, cwd: Path | None = None) -> dict[str, object]:
        return _doc(ocr._run_helper(self.helper, args, timeout=timeout, cwd=cwd, kind=MEDIA))

    def alive(self) -> bool:
        """True when the helper still answers ``--version`` within 5 s (spec S0 rule 8), as the same engine
        and helper version.  Never raises."""
        try:
            again = _open(self.helper)
        except OcrError:
            return False
        return (again.name, again.helper_version) == (self.name, self.helper_version)

    def info(self, src: Path, *, timeout: float) -> MediaInfo:
        """``info FILE``; raises MediaError."""
        doc = self._run(["info", str(src.absolute())], timeout=timeout)
        picture, audio, created = doc.get("picture"), doc.get("audio"), doc.get("created")
        if not (picture is None or (isinstance(picture, str) and ocr._TOKEN_RE.fullmatch(picture))):
            raise _bad()
        if not isinstance(audio, bool):
            raise _bad()
        if not (created is None or (isinstance(created, str) and _CREATED_RE.fullmatch(created))):
            raise _bad()
        return MediaInfo(
            duration_ms=_count(doc.get("duration_ms")),
            width=_count(doc.get("width")),
            height=_count(doc.get("height")),
            picture=picture,
            audio=audio,
            created=created,
        )

    def scan(
        self,
        src: Path,
        *,
        out: Path,
        timeout: float,
        step_ms: int = STEP_MS,
        first_tick: int = 0,
        max_ticks: int | None = None,
    ) -> Scan:
        """``scan FILE --out DIR [--first-tick K] [--max-ticks N]``: one grid per tick from tick
        ``first_tick`` into ``out/grids.bin`` (its first grid is tick ``first_tick``), at most ``max_ticks``
        of them; raises MediaError.  A ``first_tick`` past the end is the helper's failure."""
        args = ["scan", str(src.absolute()), "--out", str(out.absolute()), "--step-ms", str(step_ms)]
        if first_tick:
            args += ["--first-tick", str(first_tick)]
        if max_ticks is not None:
            args += ["--max-ticks", str(max_ticks)]
        doc = self._run(args, timeout=timeout, cwd=out)
        if doc.get("grid") != [GRID_W, GRID_H] or doc.get("step_ms") != step_ms or doc.get("file") != _GRIDS:
            raise _bad()
        ticks = doc.get("ticks")
        if not isinstance(ticks, list) or (max_ticks is not None and len(ticks) > max_ticks):
            raise _bad()
        for i, tick in enumerate(_list(ticks, len(ticks))):
            index = _count(tick.get("index"))
            if index != first_tick + i or _count(tick.get("ms")) != index * step_ms:
                raise _bad()
        grids = out / _GRIDS
        try:
            size = grids.stat().st_size
        except OSError:
            raise _bad() from None
        if size != len(ticks) * GRID_W * GRID_H:
            raise _bad()
        return Scan(grids=grids, step_ms=step_ms, first=first_tick, ticks=len(ticks))

    def frames(
        self,
        src: Path,
        *,
        out: Path,
        ticks: Sequence[int],
        timeout: float,
        crop: Rect | None = None,
        crop_right: float | None = None,
        step_ms: int = STEP_MS,
    ) -> list[Frame]:
        """``frames FILE --out DIR --ticks a,b``: one JPEG per tick, in the order asked; raises MediaError.
        ``crop`` keeps a rectangle of the frame, ``crop_right`` sets only its right edge."""
        if not ticks:
            return []
        args = [
            "frames",
            str(src.absolute()),
            "--out",
            str(out.absolute()),
            "--ticks",
            ",".join(map(str, ticks)),
        ]
        if crop is not None:
            args += ["--crop", _rect(crop)]
        if crop_right is not None:
            args += ["--crop-right", repr(float(crop_right))]
        args += ["--step-ms", str(step_ms)]
        doc = self._run(args, timeout=timeout, cwd=out)
        made = []
        for tick, item in zip(ticks, _list(doc.get("frames"), len(ticks)), strict=True):
            ms = tick * step_ms
            if item.get("tick") != tick or item.get("ms") != ms or item.get("file") != f"t{_hms(ms)}.jpg":
                raise _bad()
            made.append(
                Frame(
                    tick=tick,
                    ms=ms,
                    path=out / f"t{_hms(ms)}.jpg",
                    width=_count(item.get("width"), least=1),
                    height=_count(item.get("height"), least=1),
                )
            )
        return made

    def diff(
        self,
        grids: Path,
        *,
        pairs: Sequence[tuple[int, int]],
        timeout: float,
        include: Sequence[Rect] = (),
        exclude: Sequence[Rect] = (),
        threshold: int = 12,
    ) -> list[int]:
        """``diff GRIDS --pairs a:b,...``: the changed cells of each pair under the mask, in the order asked;
        raises MediaError.  ``a`` and ``b`` are positions in the grid file: after a scan from tick ``K``,
        position ``i`` is tick ``K + i``."""
        if not pairs:
            return []
        args = ["diff", str(grids.absolute()), "--pairs", ",".join(f"{a}:{b}" for a, b in pairs)]
        for flag, rects in (("--include", include), ("--exclude", exclude)):
            for r in rects:
                args += [flag, _rect(r)]
        args += ["--threshold", str(threshold)]
        doc = self._run(args, timeout=timeout, cwd=grids.parent)
        changed = []
        for (a, b), item in zip(pairs, _list(doc.get("pairs"), len(pairs)), strict=True):
            if item.get("a") != a or item.get("b") != b:
                raise _bad()
            n, cells = _count(item.get("changed")), _count(item.get("cells"))
            if n > cells or cells > GRID_W * GRID_H:
                raise _bad()
            changed.append(n)
        return changed


def _open(helper: Path) -> MediaEngine:
    """The engine for a helper that answers ``--version``; raises MediaError."""
    answer = ocr._run_helper(helper, ["--version"], timeout=_VERSION_TIMEOUT_S, kind=MEDIA)
    try:
        doc = json.loads(answer)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        name, version = doc.get("engine"), doc.get("helper")
        if (
            isinstance(name, str)
            and isinstance(version, str)
            and ocr._TOKEN_RE.fullmatch(name)
            and ocr._TOKEN_RE.fullmatch(version)
        ):  # the two go into the recording converter's version, so into page front matter
            return MediaEngine(helper, name=name, helper_version=version)
    raise MediaError("the media helper's --version answer is not the expected JSON")


def _switched_off(cfg: ConvertConfig) -> str:
    """Why the media helper is off whatever is built, or "": OCR's switches first, then ``recordings``."""
    return ocr._switched_off(cfg) or ("" if cfg.recordings else "[convert] recordings = false")


def _resolve(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str, MediaEngine | None]:
    off = _switched_off(cfg)
    if off:
        return "off", off, None
    return ocr._look(cache_dir, MEDIA, _open)


def probe(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str]:
    """(state, detail) for doctor and ``status``: ``ready``, ``off``, ``not-built`` or ``failed``.  Never
    compiles and never raises; ``off`` under ``AGENTSYNC_OCR=0``, ``[convert] ocr = false``, ``[convert]
    recordings = false`` or off macOS, and then starts nothing."""
    state, detail, _ = _resolve(cfg, cache_dir)
    return state, detail


def engine(cfg: ConvertConfig, cache_dir: Path) -> MediaEngine | None:
    """The engine a cycle should use, or None when :func:`probe` is not ``ready`` (logged).  Never compiles,
    never raises.  The helper it hands out gets a new modification time, its last use, which a later build
    goes by when it prunes old helpers."""
    state, detail, found = _resolve(cfg, cache_dir)
    if found is None:
        log.info("the media helper is %s: %s", state, detail)
    else:
        with contextlib.suppress(OSError):
            os.utime(found.helper)
    return found


def build(cache_dir: Path) -> Path:
    """Compile the helper into ``cache_dir`` (``scripts/install.sh`` only) unless a working one is there;
    raises MediaError, whose reason is also left beside the helper for :func:`probe`."""
    return ocr._build(cache_dir, MEDIA, _open)


def _main() -> int:
    """``python -m agentsync.convert.media``: what scripts/install.sh runs to build the helper.  Prints one
    ``media helper: ...`` line; exit 0 when ready or switched off, 1 when it is not built."""
    return ocr._entry(MEDIA, _switched_off, build, probe)


if __name__ == "__main__":
    sys.exit(_main())
