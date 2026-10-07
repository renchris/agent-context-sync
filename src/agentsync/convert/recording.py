"""Meeting recordings (``.mp4``, ``.m4v``, ``.mov``) into an evidence package of on-screen text and keyframes.

Spec ``docs/design/meeting-video-spec.md`` S1 to S6 (probe, scan, profile and pixel gate, frames, OCR, screen
states); S9 and the index (3.3 to 3.6) are rendered by ``convert/recording_page.py`` from the :class:`Reading`
this module settles.  A recording is read in pieces of 5 minutes of its picture track (4.1), each stored in
the piece store (``convert/pieces.py``) with the state the next piece needs, so a recording read over several
cycles gives the page one pass gives.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentsync.config import ConvertConfig
from agentsync.convert.base import OptionValue
from agentsync.convert.media import MediaEngine
from agentsync.convert.ocr import OcrEngine
from agentsync.convert.pieces import PieceStore
from agentsync.model import RenderedUnit

Kind = Literal["share", "camera", "other"]
Tag = Literal["SCREEN", "TILE"]


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
    raise NotImplementedError
    yield allowance  # type: ignore[unreachable]


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
        raise NotImplementedError

    def options(self) -> Mapping[str, OptionValue]:
        """Every constant of spec 2.2 that can change a page, the OCR options and ``max_page_bytes``."""
        raise NotImplementedError

    @property
    def outdated_key(self) -> str:
        """``<emitter><<floor>|<cue identity or ->|<speech identity or ->`` (spec section 4)."""
        raise NotImplementedError

    def outdated(self, produced: str, reason: str | None = None) -> bool:
        """True when a page or stub made under ``produced`` is worth reading the file again for."""
        raise NotImplementedError

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """The units of section 3, or UnreadableSourceError with a fixed stub wording, MediaError (a helper
        failure, a frame Vision gave up on) or :class:`RecordingNotFinished`."""
        raise NotImplementedError
