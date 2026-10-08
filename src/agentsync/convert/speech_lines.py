"""S8's arithmetic stages: silence map, hole finding and splice, voice numbering, speech lines (spec S8).

Pure functions over the speech engine's words and diarizer segments (``agentsync.convert.speech``), typed
by the ``WordLike`` and ``SegmentLike`` protocols so this module never imports the engine or runs a helper;
``recording.py`` calls the engine and feeds the results here.  Every time is an integer ms and every list
has a total order: words by ``(start_ms, end_ms, text)``, segments by ``(start_ms, end_ms, speaker)``, so
the result never depends on the order the engine returned them in.
"""

from __future__ import annotations

import operator
import sys
from array import array
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from agentsync.convert.base import OptionValue

SILENT_FRAME_MS = 20  # rule 2: frame length
SILENT_DBFS = -60  # rule 2: a frame whose RMS is under this is silent
SILENT_RUN_MS = 500  # rule 2: shortest silent run kept
HOLE_GAP_MS = 8_000  # rule 5: a gap between words this long ...
HOLE_SPEECH_MS = 4_000  # ... holding this much diarized speech is re-read
HOLE_MARGIN_MS = 5_000  # rule 5: margin each side of the re-read clip
PAUSE_MS = 1_000  # rule 7: a pause this long closes a line
SENTENCE_MS = 5_000  # rule 7: the first sentence end this long after the line's start closes it
LONGEST_MS = 30_000  # rule 7: a line never spans more than this (a single longer word aside)
QUIET_MS = 20_000  # 3.5 Gaps and bounds: a stretch this long without speech is listed

_FULL_SCALE_SQ = 32768 * 32768
_DBFS_DIV = 10 ** (-SILENT_DBFS // 10)  # (10 ** (dBFS / 20)) ** 2 == 1 / _DBFS_DIV


class WordLike(Protocol):
    """A recognised word: ``agentsync.convert.speech.Word`` or anything with these fields."""

    @property
    def text(self) -> str:
        """The word as the engine wrote it."""
        ...

    @property
    def start_ms(self) -> int:
        """Start, integer ms."""
        ...

    @property
    def end_ms(self) -> int:
        """End, integer ms."""
        ...


class SegmentLike(Protocol):
    """A diarizer segment: ``agentsync.convert.speech.Segment`` or anything with these fields."""

    @property
    def speaker(self) -> str:
        """The diarizer's cluster id."""
        ...

    @property
    def start_ms(self) -> int:
        """Start, integer ms."""
        ...

    @property
    def end_ms(self) -> int:
        """End, integer ms."""
        ...


_W = TypeVar("_W", bound=WordLike)


@dataclass(frozen=True, slots=True)
class SaidLine:
    """One ``SAID vN:`` line: voice number, first word's start, last word's end, the words' text."""

    voice: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class VoiceStats:
    """One row of the Voices table: merged segment time, line count, first and last speech."""

    voice: int
    speaking_ms: int
    lines: int
    first_ms: int
    last_ms: int


@dataclass(frozen=True, slots=True)
class SpeechReading:
    """Everything S8 prints after hole repair, plus each voice's merged spans for the naming stage (S8b)."""

    lines: tuple[SaidLine, ...]
    voices: tuple[VoiceStats, ...]
    spans: dict[int, tuple[tuple[int, int], ...]]
    unrecognised: tuple[tuple[int, int], ...]
    sound_ends_ms: int | None
    quiet: tuple[tuple[int, int], ...]
    words: int


def _word_key(w: WordLike) -> tuple[int, int, str]:
    return (w.start_ms, w.end_ms, w.text)


def _seg_key(s: SegmentLike) -> tuple[int, int, str]:
    return (s.start_ms, s.end_ms, s.speaker)


def _merge(spans: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """``spans`` as sorted, disjoint intervals; touching or overlapping ones become one."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


def _overlap(spans: Sequence[tuple[int, int]], a: int, b: int) -> int:
    """Milliseconds of the disjoint ``spans`` that lie inside ``[a, b)``."""
    return sum(max(0, min(b, y) - max(a, x)) for x, y in spans)


def silence(pcm: bytes, *, sample_rate: int = 16_000) -> tuple[tuple[int, int], ...]:
    """Silent runs of ``SILENT_RUN_MS`` or more in 16-bit little-endian mono ``pcm``, as ``(from_ms, to_ms)``.

    A ``SILENT_FRAME_MS`` frame is silent when every sample is zero or its RMS is under ``SILENT_DBFS``,
    tested in integers: ``sum of squares * _DBFS_DIV < samples * 32768**2``.  A short last frame is tested
    on its own samples and ends at ``samples * 1000 // sample_rate``.  An odd trailing byte is refused."""
    if len(pcm) % 2:
        raise ValueError(f"16-bit PCM has an odd byte count: {len(pcm)}")
    samples = array("h", pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    frame = sample_rate * SILENT_FRAME_MS // 1000
    runs: list[tuple[int, int]] = []
    run_from: int | None = None
    total = len(samples)
    for i in range(0, total, frame):
        chunk = samples[i : i + frame]
        n = len(chunk)
        quiet = chunk.count(0) == n or sum(map(operator.mul, chunk, chunk)) * _DBFS_DIV < n * _FULL_SCALE_SQ
        t = i * 1000 // sample_rate
        if quiet and run_from is None:
            run_from = t
        elif not quiet and run_from is not None:
            runs.append((run_from, t))
            run_from = None
    if run_from is not None:
        runs.append((run_from, total * 1000 // sample_rate))
    return tuple(r for r in runs if r[1] - r[0] >= SILENT_RUN_MS)


def sound_ends(silent: Sequence[tuple[int, int]], duration_ms: int) -> int | None:
    """Where sound ends: the start of the silent run that reaches ``duration_ms``, or None when none does."""
    ends = [a for a, b in silent if b >= duration_ms]
    return min(ends) if ends else None


def holes(words: Sequence[WordLike], segments: Sequence[SegmentLike]) -> tuple[tuple[int, int], ...]:
    """Rule 5: gaps of ``HOLE_GAP_MS`` or more between consecutive words (from the latest end so far to the
    next start) that hold ``HOLE_SPEECH_MS`` or more of diarized speech, all speakers' segments merged.  Only
    gaps between two words count: the stretches before the first word and after the last are not holes."""
    speech = _merge([(s.start_ms, s.end_ms) for s in segments])
    out: list[tuple[int, int]] = []
    last_end: int | None = None
    for w in sorted(words, key=_word_key):
        if (
            last_end is not None
            and w.start_ms - last_end >= HOLE_GAP_MS
            and _overlap(speech, last_end, w.start_ms) >= HOLE_SPEECH_MS
        ):
            out.append((last_end, w.start_ms))
        last_end = w.end_ms if last_end is None else max(last_end, w.end_ms)
    return tuple(out)


def clip_of(hole: tuple[int, int], duration_ms: int) -> tuple[int, int]:
    """The clip re-read for ``hole``: ``HOLE_MARGIN_MS`` each side, clamped to ``[0, duration_ms]``."""
    return (max(0, hole[0] - HOLE_MARGIN_MS), min(duration_ms, hole[1] + HOLE_MARGIN_MS))


def splice(words: Sequence[_W], hole: tuple[int, int], clip_words: Sequence[_W]) -> tuple[_W, ...]:
    """``words`` plus the ``clip_words`` whose midpoint lies strictly inside ``hole``, sorted by
    ``(start_ms, end_ms, text)``.  ``clip_words`` carry recording time: the caller adds the clip's start."""
    a2, b2 = 2 * hole[0], 2 * hole[1]
    kept = [w for w in clip_words if a2 < w.start_ms + w.end_ms < b2]
    return tuple(sorted([*words, *kept], key=_word_key))


def _voice_segment(
    m2: int, segs: Sequence[SegmentLike], starts2: list[int], reach2: list[int]
) -> SegmentLike:
    """The segment for a word whose doubled midpoint is ``m2``: least ``(distance, start, end, speaker)``,
    distance 0 for a segment holding the midpoint (ends included)."""
    best: tuple[int, tuple[int, int, str], SegmentLike] | None = None
    i = bisect_right(starts2, m2)
    if i < len(segs):  # the right side's best is the first segment starting after the midpoint
        best = (starts2[i] - m2, _seg_key(segs[i]), segs[i])
    j = i - 1
    while j >= 0 and (best is None or max(0, m2 - reach2[j]) <= best[0]):
        s = segs[j]
        cand = (max(0, m2 - 2 * s.end_ms), _seg_key(s), s)
        if best is None or cand[:2] < best[:2]:
            best = cand
        j -= 1
    assert best is not None
    return best[2]


def numbering(words: Sequence[WordLike], segments: Sequence[SegmentLike]) -> tuple[list[int], dict[str, int]]:
    """Rule 6: each word's voice (parallel to ``words``) and each speaker's voice number.

    A word's segment is the one holding its midpoint, else the nearest; ties go to the earlier segment, then
    the speaker id.  Speakers are numbered 1, 2, ... by their first word in time order; speakers with segments
    but no word follow, by first segment start, then id.  With no segment at all every word is voice 1 and
    the map is empty."""
    segs = sorted(segments, key=_seg_key)
    if not segs:
        return [1] * len(words), {}
    starts2 = [2 * s.start_ms for s in segs]
    reach2: list[int] = []
    for s in segs:
        reach2.append(max(2 * s.end_ms, reach2[-1] if reach2 else 2 * s.end_ms))
    speaker_of = [_voice_segment(w.start_ms + w.end_ms, segs, starts2, reach2).speaker for w in words]
    number: dict[str, int] = {}
    for k in sorted(range(len(words)), key=lambda k: (*_word_key(words[k]), speaker_of[k])):
        number.setdefault(speaker_of[k], len(number) + 1)
    for s in segs:
        number.setdefault(s.speaker, len(number) + 1)
    return [number[sp] for sp in speaker_of], number


def _sentence_end(text: str) -> bool:
    return text.rstrip().endswith((".", "?", "!"))


def said_lines(words: Sequence[WordLike], voice_of: Sequence[int]) -> tuple[SaidLine, ...]:
    """Rule 7: consecutive words of one voice (``voice_of`` parallel to ``words``), in word order.

    A line closes at a change of voice, before a word starting ``PAUSE_MS`` or more after the line's latest
    end, after the first word ending in ``.``, ``?`` or ``!`` whose end is ``SENTENCE_MS`` or more after the
    line's start, and before a word that would end more than ``LONGEST_MS`` after the line's start.  Text is
    the words joined by one space, whitespace inside a word collapsed."""
    if len(voice_of) != len(words):
        raise ValueError(f"{len(voice_of)} voices for {len(words)} words")
    order = sorted(range(len(words)), key=lambda k: (*_word_key(words[k]), voice_of[k]))
    out: list[SaidLine] = []
    cur: list[WordLike] = []
    voice = 0
    end = 0

    def close() -> None:
        if cur:
            out.append(
                SaidLine(voice, cur[0].start_ms, end, " ".join(t for w in cur for t in w.text.split()))
            )
            cur.clear()

    for k in order:
        w, v = words[k], voice_of[k]
        if cur and (v != voice or w.start_ms - end >= PAUSE_MS or w.end_ms - cur[0].start_ms > LONGEST_MS):
            close()
        if not cur:
            voice, end = v, w.end_ms
        cur.append(w)
        end = max(end, w.end_ms)
        if _sentence_end(w.text) and w.end_ms - cur[0].start_ms >= SENTENCE_MS:
            close()
    close()
    return tuple(out)


def _quiet(
    busy: Sequence[tuple[int, int]], sound_ends_ms: int | None, duration_ms: int
) -> tuple[tuple[int, int], ...]:
    """Gaps of ``QUIET_MS`` or more in ``busy`` inside ``[0, sound end)``."""
    limit = duration_ms if sound_ends_ms is None else sound_ends_ms
    out: list[tuple[int, int]] = []
    at = 0
    for a, b in [*_merge(busy), (limit, limit)]:
        stop = min(a, limit)
        if stop - at >= QUIET_MS:
            out.append((at, stop))
        at = max(at, b)
        if at >= limit:
            break
    return tuple(out)


def reading(
    words: Sequence[WordLike],
    segments: Sequence[SegmentLike],
    silent: Sequence[tuple[int, int]],
    duration_ms: int,
    unrecognised: Sequence[tuple[int, int]],
) -> SpeechReading:
    """Everything after hole repair: lines, the Voices table, each voice's spans, where sound ends, quiet
    stretches and the word count.  ``unrecognised`` is the flagged gaps still empty after repair.

    Speaking time is the sum of the voice's merged segment spans; first and last are its first word's start
    and its latest word end (its first segment start and latest segment end when it has no word).  Quiet
    stretches are gaps of ``QUIET_MS`` or more in the union of segments and words, before where sound ends
    (the whole recording when it never does)."""
    ws = sorted(words, key=_word_key)
    voice_of, number = numbering(ws, segments)
    lines = said_lines(ws, voice_of)
    voices = sorted({*voice_of, *number.values()})
    spans: dict[int, tuple[tuple[int, int], ...]] = dict.fromkeys(voices, ())
    for sp, v in number.items():
        spans[v] = _merge([(s.start_ms, s.end_ms) for s in segments if s.speaker == sp])
    stats: list[VoiceStats] = []
    for v in voices:
        mine = [w for w, wv in zip(ws, voice_of, strict=True) if wv == v]
        first, last = (
            (mine[0].start_ms, max(w.end_ms for w in mine)) if mine else (spans[v][0][0], spans[v][-1][1])
        )
        speaking = sum(b - a for a, b in spans[v])
        stats.append(VoiceStats(v, speaking, sum(1 for ln in lines if ln.voice == v), first, last))
    ends = sound_ends(silent, duration_ms)
    busy = [(s.start_ms, s.end_ms) for s in segments] + [(w.start_ms, w.end_ms) for w in ws]
    return SpeechReading(
        lines=lines,
        voices=tuple(stats),
        spans=spans,
        unrecognised=tuple(sorted(unrecognised)),
        sound_ends_ms=ends,
        quiet=_quiet(busy, ends, duration_ms),
        words=len(ws),
    )


def options() -> dict[str, OptionValue]:
    """Every constant of this module, for the recording converter's options hash."""
    return {
        "speech_silent_frame_ms": SILENT_FRAME_MS,
        "speech_silent_dbfs": SILENT_DBFS,
        "speech_silent_run_ms": SILENT_RUN_MS,
        "speech_hole_gap_ms": HOLE_GAP_MS,
        "speech_hole_speech_ms": HOLE_SPEECH_MS,
        "speech_hole_margin_ms": HOLE_MARGIN_MS,
        "speech_pause_ms": PAUSE_MS,
        "speech_sentence_ms": SENTENCE_MS,
        "speech_longest_ms": LONGEST_MS,
        "speech_quiet_ms": QUIET_MS,
    }
