"""Voice naming (spec S8b, ruling 6): which voice, if any, is the one voice an on-screen account carried.

Pure: counts and comparisons over the voices of S8 and the lit labels of S7, no I/O.

- Tick ``k`` is at ``k x step_ms`` of media time.  A tick has speech for voice ``v`` when it falls inside one
  of ``v``'s spans; a span ``(start, end)`` covers ``start <= t < end`` in ms.  A tick inside two voices'
  spans is assigned to neither and counted nowhere: cross-talk says nothing about whose audio a label carried.
- A *lit sample* is a tick with speech at which exactly one label is lit (the S8b preamble).  ``lit`` maps a
  tick to the label texts drawn as speaking there, as read; a label lit twice at one tick is one label.
- Rule 1, stream table: ``s(L)`` is the largest share one voice holds of ``L``'s lit samples, over at least
  ``STREAM_MIN`` of them; under that, ``s(L)`` is undefined and ``L`` cannot be taken.  ``L`` is shared when
  ``s(L) < S_MIN``.
- Rule 2, voice rule: ``n_v`` is ``v``'s lit samples and ``p_v`` the share of them under its most frequent
  label ``L``.  ``v`` takes ``L`` when ``n_v >= N_MIN``, ``p_v >= P_MIN``, ``s(L) >= S_MIN`` and ``v`` is the
  voice that holds ``s(L)``: another voice under a one-voice label is not that voice and stays unidentified.
- Rule 3: ``p_v < P_MIN`` is ``mixed``.
- Rule 4: a voice whose label is shared prints the shared form, with ``k`` the distinct voices among ``L``'s
  lit samples.  It names no voice, so the naming gate does not hold it.
- Rule 5, turn veto: ``vetoed`` decides it per speech line; ``SAID vN:`` lines do not change, the line gets
  the window ``NOTE`` of ``veto_note``.
- Rule 6, margin band: when ``s(L)`` or ``p_v`` lies in ``[S_MIN, S_MIN + BAND)`` or ``[P_MIN, P_MIN + BAND)``
  a voice that would take ``L`` is held back: it prints as unidentified and its ``VOICE`` line is followed by
  ``HELD_BACK_NOTE``.
- A voice with fewer than ``N_MIN`` lit samples is unidentified, whatever its label: too few to say anything.
- Naming gate (plan P3): a voice that takes a label prints as named only when the recording's layout profile
  is in ``CHECKED_PROFILES``; otherwise it prints as unidentified with ``gated`` set, and the index's Voices
  block carries ``GATED_NOTE`` once.  The band hold comes first, so a voice is never both held back and gated.

Ties are broken by count, then label text (and the voice holding ``s(L)`` by count, then the lower number), so
the output is a function of the input.  Every share is compared as an exact fraction, never a float.
"""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from agentsync.convert.base import OptionValue

NAMING_REVISION = 1  # the -n<revision> of the recording converter's version (spec 4, C7)

# Rule 7: floors, never tuned down.
P_MIN = 0.90  # share of a voice's lit samples under its label
N_MIN = 10  # lit samples a voice needs before it is judged
S_MIN = 0.90  # share one voice holds of a label's lit samples
STREAM_MIN = 20  # lit samples a label needs before s(L) is defined
BAND = 0.05  # the margin band above P_MIN and S_MIN

CHECKED_PROFILES: frozenset[str] = frozenset()  # layouts whose names a listen has checked (B.3 items 1-2)

HELD_BACK_NOTE = "name held back: a share inside the margin band"
GATED_NOTE = "names held back: this layout's names are not yet checked against a listen"

Form = Literal["named", "shared", "mixed", "unidentified"]


@dataclass(frozen=True, slots=True)
class Voice:
    """Voice ``vN`` of S8: its number and its speech, as ``(start, end)`` spans in ms."""

    number: int
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class Naming:
    """What S8b made of one voice.

    ``label`` is the voice's most frequent label (``None`` without lit samples); ``seen`` its lit samples
    under that label, of ``lit`` (``n_v``); ``percent`` its share of the label's lit samples, floored to a
    whole percent so it never rounds up across a threshold; ``voices_on_label`` the distinct voices among the
    label's lit samples (``k`` of the shared form).
    """

    number: int
    form: Form
    label: str | None
    seen: int
    lit: int
    percent: str
    voices_on_label: int
    held_back: bool
    gated: bool


def _exact(value: float) -> Fraction:
    """``value`` as the decimal it is written as (0.9 is 9/10, not the float's binary fraction)."""
    return Fraction(repr(value))


def _top_label(counts: Counter[str]) -> str:
    """The most frequent label; ties by count, then label text."""
    return min(counts, key=lambda label: (-counts[label], label))


class _Speech:
    """Which voice, if exactly one, has speech at a time."""

    def __init__(self, voices: Sequence[Voice]) -> None:
        self._voices = [(v.number, _merged(v.spans)) for v in voices]

    def voice_at(self, at_ms: int) -> int | None:
        found = None
        for number, (starts, ends) in self._voices:
            i = bisect.bisect_right(starts, at_ms)
            if i and ends[i - 1] > at_ms:
                if found is not None:
                    return None
                found = number
        return found


def _merged(spans: Sequence[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """A voice's spans as disjoint sorted runs (starts, ends), so one bisect finds the run covering a time."""
    starts: list[int] = []
    ends: list[int] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if ends and start <= ends[-1]:
            ends[-1] = max(ends[-1], end)
        else:
            starts.append(start)
            ends.append(end)
    return starts, ends


def _one_label(labels: Sequence[str]) -> str | None:
    distinct = set(labels)
    return next(iter(distinct)) if len(distinct) == 1 else None


def name_voices(
    voices: Sequence[Voice], lit: Mapping[int, Sequence[str]], *, profile: str, step_ms: int = 2000
) -> tuple[Naming, ...]:
    """One ``Naming`` per voice, in voice-number order, by rules 1 to 6 and the gate (module docstring)."""
    if step_ms <= 0:
        raise ValueError(f"not a tick length: {step_ms!r}")
    numbers = [v.number for v in voices]
    if len(set(numbers)) != len(numbers):
        raise ValueError(f"a voice number twice: {sorted(numbers)!r}")
    speech = _Speech(voices)
    by_label: dict[str, Counter[int]] = {}
    by_voice: dict[int, Counter[str]] = {number: Counter() for number in numbers}
    for tick in sorted(lit):
        label = _one_label(lit[tick])
        voice = speech.voice_at(tick * step_ms) if label is not None else None
        if label is None or voice is None:
            continue
        by_label.setdefault(label, Counter())[voice] += 1
        by_voice[voice][label] += 1
    return tuple(_name(n, by_voice[n], by_label, profile) for n in sorted(numbers))


def _name(number: int, mine: Counter[str], by_label: Mapping[str, Counter[int]], profile: str) -> Naming:
    n_v = sum(mine.values())
    if not n_v:
        return Naming(number, "unidentified", None, 0, 0, "0", 0, held_back=False, gated=False)
    label = _top_label(mine)
    stream = by_label[label]
    total = sum(stream.values())
    seen = mine[label]

    def naming(form: Form, *, held_back: bool = False, gated: bool = False) -> Naming:
        percent = str(seen * 100 // total)
        return Naming(number, form, label, seen, n_v, percent, len(stream), held_back=held_back, gated=gated)

    p_v = Fraction(seen, n_v)
    if n_v < N_MIN:
        return naming("unidentified")
    if p_v < _exact(P_MIN):
        return naming("mixed")
    if total < STREAM_MIN:
        return naming("unidentified")
    holder = min(stream, key=lambda v: (-stream[v], v))
    s_l = Fraction(stream[holder], total)
    if s_l < _exact(S_MIN):
        return naming("shared")
    if holder != number:
        return naming("unidentified")
    if s_l < _exact(S_MIN) + _exact(BAND) or p_v < _exact(P_MIN) + _exact(BAND):
        return naming("unidentified", held_back=True)
    if profile not in CHECKED_PROFILES:
        return naming("unidentified", gated=True)
    return naming("named")


def vetoed(
    naming: Naming, start_ms: int, end_ms: int, lit: Mapping[int, Sequence[str]], *, step_ms: int = 2000
) -> bool:
    """Rule 5 for one speech line of ``naming``'s voice over ``[start_ms, end_ms)``: True when the voice is
    named, the line holds one or more lit samples (ticks inside it with exactly one label lit; the line is the
    voice's speech) and their most frequent label (ties as in ``name_voices``) is not the voice's label."""
    if step_ms <= 0:
        raise ValueError(f"not a tick length: {step_ms!r}")
    if naming.form != "named":
        return False
    first = -(-start_ms // step_ms)
    counts: Counter[str] = Counter()
    for tick in range(first, -(-end_ms // step_ms)):
        label = _one_label(lit.get(tick, ()))
        if label is not None:
            counts[label] += 1
    return bool(counts) and _top_label(counts) != naming.label


def veto_note(number: int) -> str:
    """The window ``NOTE`` at the time of a vetoed speech line of voice ``number``."""
    return f"v{number} is not named on this line: its lit samples show another label"


def voice_text(n: Naming) -> str:
    """The text after ``VOICE: `` in its 3.3 form."""
    if n.form == "named":
        return (
            f"v{n.number} · {n.label} · seen: {n.seen} of {n.lit} lit samples; "
            f"{n.percent} % of the label's lit speech"
        )
    if n.form == "shared":
        return f"v{n.number} · shared audio of {n.label}, {n.voices_on_label} voices"
    return f"v{n.number} · {n.form}"


def identity() -> str:
    """S8b's part of the recording converter's version: ``n<revision>``."""
    return f"n{NAMING_REVISION}"


def options() -> dict[str, OptionValue]:
    """Every floor, the band and the checked profiles (comma-joined, sorted), for options_hash."""
    return {
        "naming_p_min": P_MIN,
        "naming_n_min": N_MIN,
        "naming_s_min": S_MIN,
        "naming_stream_min": STREAM_MIN,
        "naming_band": BAND,
        "naming_checked_profiles": ",".join(sorted(CHECKED_PROFILES)),
    }
