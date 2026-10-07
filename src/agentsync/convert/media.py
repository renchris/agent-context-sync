"""The on-device media helper (``media_frames.swift``): build, trust, probe and run (spec S1, S2, S4, S6 rule
8).

Built, trusted, probed and pruned as the OCR helper is (``convert/ocr.py``), by ``python -I -m
agentsync.convert.media`` from ``scripts/install.sh`` and never by a sync, ``status`` or doctor.  It reads a
recording with AVFoundation and answers one JSON document per call; exit 3 with a message on stderr is a
failure. The protocol is pinned by ``tests/media_kit.py``: ``--version``, ``info``, ``scan``, ``frames``,
``diff`` (``pills`` and ``audio`` arrive with P3).

:func:`probe` and :func:`engine` only look.  A helper error, a time-out or an answer that is not the expected
JSON is a :class:`MediaError`, which is an ``OcrError``: ``convert_file`` then gives the recording the ``no
converter`` refusal it has without engines, and caches nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert.ocr import OcrError

STEP_MS = 2000  # one tick every 2 s of media time (spec 2.2)
GRID_W, GRID_H = 320, 180  # one box-averaged luma grid per tick, 57,600 bytes
JPEG_QUALITY = 0.7


class MediaError(OcrError):
    """The media helper could not be built, may not be run, failed, ran out of time, or answered with
    something that is not the expected JSON.  Its text never holds a path."""


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
    """What ``scan`` wrote: ``grids`` holds ``ticks`` grids of ``GRID_W x GRID_H`` bytes, tick ``k`` at media
    time ``k x step_ms``."""

    grids: Path
    step_ms: int
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

    def alive(self) -> bool:
        """True when the helper still answers ``--version`` within 5 s (spec S0 rule 8).  Never raises."""
        raise NotImplementedError

    def info(self, src: Path, *, timeout: float) -> MediaInfo:
        """``info FILE``; raises MediaError."""
        raise NotImplementedError

    def scan(
        self, src: Path, *, out: Path, timeout: float, step_ms: int = STEP_MS, max_ticks: int | None = None
    ) -> Scan:
        """``scan FILE --out DIR``: one grid per tick into ``out/grids.bin``; raises MediaError."""
        raise NotImplementedError

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
        """``frames FILE --out DIR --ticks a,b``: one JPEG per tick, in the order asked; raises MediaError."""
        raise NotImplementedError

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
        raises MediaError."""
        raise NotImplementedError


def probe(cfg: ConvertConfig, cache_dir: Path) -> tuple[str, str]:
    """(state, detail) for doctor and ``status``: ``ready``, ``off``, ``not-built`` or ``failed``.  Never
    compiles and never raises; ``off`` under ``AGENTSYNC_OCR=0``, ``[convert] ocr = false``, ``[convert]
    recordings = false`` or off macOS, and then starts nothing."""
    raise NotImplementedError


def engine(cfg: ConvertConfig, cache_dir: Path) -> MediaEngine | None:
    """The engine a cycle should use, or None when :func:`probe` is not ``ready``.  Never compiles, never
    raises."""
    raise NotImplementedError


def build(cache_dir: Path) -> Path:
    """Compile the helper into ``cache_dir`` (``scripts/install.sh`` only); raises MediaError."""
    raise NotImplementedError
