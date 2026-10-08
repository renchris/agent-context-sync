"""S8's arithmetic stages (agentsync.convert.speech_lines): one test per rule, plus a determinism run."""

from __future__ import annotations

import math
import random
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentsync.convert import speech_lines as sl


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class Segment:
    speaker: str
    start_ms: int
    end_ms: int


def pcm(*parts: tuple[int, int]) -> bytes:
    """16 kHz s16le PCM: each part is (milliseconds, constant sample value)."""
    return b"".join(struct.pack(f"<{ms * 16}h", *([value] * (ms * 16))) for ms, value in parts)


# -- rule 2: silence map -------------------------------------------------------------------------------------


def test_silence_threshold_is_minus_60_dbfs() -> None:
    # A constant amplitude's RMS is the amplitude: -60 dBFS is 32.768, so 32 is silent and 33 is not.
    assert sl.silence(pcm((1000, 32))) == ((0, 1000),)
    assert sl.silence(pcm((1000, -32))) == ((0, 1000),)
    assert sl.silence(pcm((1000, 33))) == ()


def test_silence_one_loud_sample_is_judged_by_rms() -> None:
    # One sample in a 320-sample frame: 586**2 is under 320 * 32.768**2 (343,597.4), 587**2 is not.
    def frame(peak: int) -> bytes:
        return struct.pack("<320h", peak, *([0] * 319))

    assert sl.silence(frame(586) * 50) == ((0, 1000),)
    assert sl.silence(frame(587) * 50) == ()


def test_silence_digital_zero_and_run_edge() -> None:
    assert sl.silence(pcm((1000, 1000), (480, 0), (1000, 1000))) == ()
    assert sl.silence(pcm((1000, 1000), (500, 0), (1000, 1000))) == ((1000, 1500),)
    assert sl.silence(pcm((2000, 0))) == ((0, 2000),)


def test_silence_short_last_frame_and_odd_byte() -> None:
    tail = struct.pack("<8h", *([0] * 8))  # half a millisecond past 1,000 ms
    assert sl.silence(pcm((1000, 1000), (600, 0)) + tail) == ((1000, 1600),)
    with pytest.raises(ValueError, match="odd byte count"):
        sl.silence(b"\x00\x00\x00")


def test_sound_ends_at_the_run_reaching_the_end() -> None:
    assert sl.sound_ends(((1000, 2000), (5000, 9000)), 9000) == 5000
    assert sl.sound_ends(((1000, 2000),), 9000) is None
    assert sl.sound_ends(((0, 9000),), 9000) == 0
    assert sl.sound_ends((), 9000) is None


# -- rule 5: holes -------------------------------------------------------------------------------------------


def test_hole_needs_8_s_gap_and_4_s_of_speech() -> None:
    def words(gap: int) -> list[Word]:
        return [Word("a", 0, 500), Word("b", 500 + gap, 600 + gap)]

    speech = [Segment("s1", 1000, 5000)]
    assert sl.holes(words(8000), speech) == ((500, 8500),)
    assert sl.holes(words(7999), speech) == ()
    assert sl.holes(words(8000), [Segment("s1", 1000, 4999)]) == ()
    # Two speakers over the same 3 s count once: merged, not summed.
    assert sl.holes(words(8000), [Segment("s1", 1000, 4000), Segment("s2", 1000, 4000)]) == ()
    # The gap starts at the latest end so far, not the previous word's end.
    overlapped = [Word("a", 0, 3000), Word("b", 100, 200), Word("c", 11000, 11100)]
    assert sl.holes(overlapped, [Segment("s1", 3000, 11000)]) == ((3000, 11000),)


def test_clip_of_clamps_at_both_ends() -> None:
    assert sl.clip_of((2000, 12000), 15000) == (0, 15000)
    assert sl.clip_of((20000, 30000), 60000) == (15000, 35000)


def test_splice_keeps_clip_words_by_midpoint() -> None:
    words = [Word("before", 0, 1000), Word("after", 10000, 10500)]
    clip = [
        Word("edge", 500, 1500),  # midpoint 1000: on the gap's start, not inside
        Word("in", 4000, 4600),
        Word("also", 1500, 2000),
        Word("late", 9800, 10400),  # midpoint 10100: past the gap
    ]
    out = sl.splice(words, (1000, 10000), clip)
    assert [w.text for w in out] == ["before", "also", "in", "after"]


# -- rule 6: voices ------------------------------------------------------------------------------------------


def test_voice_of_word_by_midpoint_then_nearest() -> None:
    segs = [Segment("x", 0, 1000), Segment("y", 2000, 3000), Segment("z", 900, 1200)]
    words = [
        Word("held", 400, 600),  # inside x only
        Word("both", 900, 1100),  # midpoint 1000 held by x (ends included) and z: x starts earlier
        Word("near-y", 1700, 1800),  # midpoint 1750: 550 from z, 250 from y
        Word("tie", 1550, 1650),  # midpoint 1600: 400 from z, 400 from y: z is the earlier segment
    ]
    voice_of, number = sl.numbering(words, segs)
    speaker = {v: k for k, v in number.items()}
    assert [speaker[v] for v in voice_of] == ["x", "x", "y", "z"]


def test_voice_tie_on_segment_goes_to_speaker_id() -> None:
    voice_of, number = sl.numbering([Word("w", 100, 200)], [Segment("b", 0, 500), Segment("a", 0, 500)])
    assert number == {"a": 1, "b": 2}
    assert voice_of == [1]


def test_numbering_by_first_word_then_silent_voices() -> None:
    segs = [Segment("late", 0, 1000), Segment("early", 5000, 6000), Segment("mute", 2000, 2100)]
    words = [Word("second", 5100, 5200), Word("first", 100, 200)]  # unsorted on purpose
    voice_of, number = sl.numbering(words, segs)
    assert number == {"late": 1, "early": 2, "mute": 3}
    assert voice_of == [2, 1]
    assert sl.numbering([Word("w", 0, 1)], []) == ([1], {})


# -- rule 7: lines -------------------------------------------------------------------------------------------


def texts(words: list[Word], voices: list[int]) -> list[str]:
    return [line.text for line in sl.said_lines(words, voices)]


def test_line_closes_at_change_of_voice() -> None:
    assert texts([Word("a", 0, 100), Word("b", 100, 200)], [1, 2]) == ["a", "b"]


def test_line_closes_at_a_pause_of_1_s() -> None:
    assert texts([Word("a", 0, 100), Word("b", 1099, 1200)], [1, 1]) == ["a b"]
    assert texts([Word("a", 0, 100), Word("b", 1100, 1200)], [1, 1]) == ["a", "b"]


def test_line_closes_at_first_sentence_end_after_5_s() -> None:
    early = [Word("Hi.", 0, 4999), Word("there", 5000, 5100)]
    assert texts(early, [1, 1]) == ["Hi. there"]
    words = [
        Word("one", 0, 900),
        Word("Done?", 1000, 5000),
        Word("Next!", 5100, 5200),
        Word("more", 5300, 5400),
    ]
    assert texts(words, [1] * 4) == ["one Done?", "Next! more"]


def test_line_closes_at_30_s() -> None:
    words = [Word(f"w{i}", i * 900, i * 900 + 800) for i in range(40)]  # 100 ms gaps, no sentence end
    lines = sl.said_lines(words, [1] * 40)
    assert [ln.start_ms for ln in lines] == [0, 29700]
    assert all(ln.end_ms - ln.start_ms <= sl.LONGEST_MS for ln in lines)


def test_line_text_collapses_whitespace() -> None:
    words = [Word(" New\tYork ", 0, 100), Word("  ", 100, 150), Word("City", 150, 300)]
    assert sl.said_lines(words, [3, 3, 3]) == (sl.SaidLine(3, 0, 300, "New York City"),)
    with pytest.raises(ValueError):
        sl.said_lines(words, [1])


# -- reading: stats, spans, quiet stretches ------------------------------------------------------------------


def test_reading_stats_arithmetic() -> None:
    segs = [
        Segment("a", 0, 3000),
        Segment("a", 2000, 4000),  # overlaps: a speaks 4,000 ms, not 5,000
        Segment("b", 10000, 12000),
        Segment("c", 50000, 51000),  # no word
    ]
    words = [Word("one.", 100, 400), Word("two", 3000, 3500), Word("three", 10100, 10600)]
    r = sl.reading(words, segs, (), 60000, [(20000, 30000)])
    assert r.voices == (
        sl.VoiceStats(1, 4000, 2, 100, 3500),
        sl.VoiceStats(2, 2000, 1, 10100, 10600),
        sl.VoiceStats(3, 1000, 0, 50000, 51000),
    )
    assert r.spans == {1: ((0, 4000),), 2: ((10000, 12000),), 3: ((50000, 51000),)}
    assert r.words == 3
    assert r.unrecognised == ((20000, 30000),)
    assert r.sound_ends_ms is None
    assert r.quiet == ((12000, 50000),)


def test_quiet_stops_where_sound_ends() -> None:
    segs = [Segment("a", 25000, 26000)]
    r = sl.reading([], segs, ((40000, 100000),), 100000, [])
    assert r.sound_ends_ms == 40000
    assert r.quiet == ((0, 25000),)  # 26 s to 40 s is 14 s; after 40 s there is no sound
    assert sl.reading([], [], ((70000, 100000),), 100000, []).quiet == ((0, 70000),)


def test_options_name_every_constant() -> None:
    values = sorted(v for v in sl.options().values() if isinstance(v, int))
    constants = [
        name
        for name in vars(sl)
        if name.isupper() and not name.startswith("_") and isinstance(getattr(sl, name), int)
    ]
    assert values == sorted(getattr(sl, name) for name in constants)


# -- determinism ---------------------------------------------------------------------------------------------

_RUN = """
import sys
from dataclasses import dataclass
from agentsync.convert.speech_lines import reading, silence

@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start_ms: int
    end_ms: int

@dataclass(frozen=True, slots=True)
class Segment:
    speaker: str
    start_ms: int
    end_ms: int

WORDS = [Word(f"word{i}" + "." * (i % 7 == 6), 300 + i * 410, 650 + i * 410) for i in range(45)]
WORDS += [Word(f"later{i}", 23000 + i * 600, 23000 + i * 600 + 500) for i in range(30)]
SEGMENTS = [
    Segment("spk-b", 9000, 19500), Segment("spk-a", 0, 9100), Segment("spk-c", 23000, 41000),
    Segment("spk-a", 41500, 44000), Segment("spk-z", 44000, 44800),
]

def run(pcm, reverse=False):
    segs = list(reversed(SEGMENTS)) if reverse else SEGMENTS
    words = list(reversed(WORDS)) if reverse else WORDS
    return repr(reading(words, segs, silence(pcm), len(pcm) * 1000 // 32000, [(19500, 23000)]))
"""


def _fixed_pcm() -> bytes:
    """About 60 s: seeded tone plus noise, a 2 s digital-zero gap, a 15 s silent tail."""
    rng = random.Random(20261007)
    out: list[int] = []
    for i in range(45 * 16000):
        t = i / 16000
        if 20.0 <= t < 22.0:
            out.append(0)
        else:
            out.append(int(4000 * math.sin(2 * math.pi * 220 * t)) + rng.randint(-300, 300))
    out.extend([0] * (15 * 16000))
    return struct.pack(f"<{len(out)}h", *out)


def test_speech_is_deterministic_on_a_fixed_wav(tmp_path: Path) -> None:
    data = _fixed_pcm()
    assert sl.silence(data) == ((20000, 22000), (45000, 60000))
    ns: dict[str, Any] = {"__name__": "speech_script"}
    exec(compile(_RUN, "<speech script>", "exec", dont_inherit=True), ns)  # real annotations, as in -c
    first, again, shuffled = ns["run"](data), ns["run"](data), ns["run"](data, reverse=True)
    assert first == again == shuffled
    wav = tmp_path / "fixed.pcm"
    wav.write_bytes(data)
    code = _RUN + f"\nsys.stdout.write(run(open({str(wav)!r}, 'rb').read()))\n"
    fresh = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True).stdout
    assert fresh == first.encode()
    assert "sound_ends_ms=45000" in first
