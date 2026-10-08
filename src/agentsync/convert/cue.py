"""The speaker cue (spec S7): which participant's label is lit, as pure functions over the media helper's
``pills`` answers.

No I/O and no helper calls here.  The recording converter asks :func:`requests` which boxes to send per tick,
runs ``MediaEngine.pills`` on them, turns the answer into lit sets with :func:`lit_sets` (kept for S8b) and
into ``SPEAKING`` lines with :func:`speaking`.

Only ``teams`` has a cue in P3: the fill of the label boxes OCR read, nowhere else, since the label colour
matched other content on 61 ticks.  A label is lit when the median ``B - R`` of its box's dark pixels (``R + G
+ B < 600``), the box widened by 4 px left and right and 2 px above and below, is 50 or more: lit labels read
60 to 80 and unlit ones 40 or less.  ``meet``'s cue (spec C18) lands with P4's Meet example, as every P4
profile's cue lands with its own example; until then a ``meet`` recording has no cue.

The boxes of a tick are those of the last read candidate at or before it, so a strip that re-arranges without
a content change gives stale boxes (spec S7 limits), and a lit label OCR never read writes nothing.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentsync.convert.base import OptionValue

CUE_REVISION = 1  # ``+cue-r<n>`` of the recording converter's version
LIT_AT = 50  # teams: a label is lit when its value is 50 or more

_WIDEN_PX = (4, 2)  # what the helper adds to a box: left and right, above and below
_DARK_BELOW = 600  # the helper counts a pixel when R + G + B is below this
_CUES = {"teams": "the fill of label boxes: median B - R of their dark pixels"}


@dataclass(frozen=True, slots=True)
class Label:
    """One label row OCR read on a candidate: its text as read and its box (x, y, w, h in fractions of the
    frame, origin top-left)."""

    text: str
    box: tuple[float, float, float, float]


def has_cue(profile: str) -> bool:
    """True when ``profile`` has a speaker cue (P3: ``teams`` only)."""
    return profile in _CUES


def requests(
    profile: str, labels: Mapping[int, Sequence[Label]], ticks: Sequence[int]
) -> list[tuple[int, tuple[Label, ...]]]:
    """Per tick of ``ticks``, in their order, the labels of the last read candidate at or before it
    (``labels`` is keyed by candidate tick).  A tick before every candidate, or whose candidate read no label,
    is left out; a profile without a cue asks for nothing."""
    if not has_cue(profile):
        return []
    read = sorted(labels)
    asked = []
    for tick in ticks:
        at = bisect.bisect_right(read, tick)
        if at and labels[read[at - 1]]:
            asked.append((tick, tuple(labels[read[at - 1]])))
    return asked


def lit_sets(
    asked: Sequence[tuple[int, Sequence[Label]]], values: Mapping[int, Sequence[int]]
) -> dict[int, tuple[str, ...]]:
    """Per asked tick, the texts of the labels whose value is :data:`LIT_AT` or more, in box order.
    ``values`` is ``MediaEngine.pills``' answer to ``asked``: one value per label."""
    return {
        tick: tuple(label.text for label, value in zip(labels, values[tick], strict=True) if value >= LIT_AT)
        for tick, labels in asked
    }


def speaking(lit: Mapping[int, Sequence[str]]) -> list[tuple[int, str]]:
    """``(tick, label text)`` in tick order: a ``SPEAKING`` line where exactly one label is lit and its text
    differs from the last ``SPEAKING`` line's.  Two lit labels, or none, write nothing and forget nothing."""
    lines: list[tuple[int, str]] = []
    for tick in sorted(lit):
        texts = lit[tick]
        if len(texts) == 1 and (not lines or lines[-1][1] != texts[0]):
            lines.append((tick, texts[0]))
    return lines


def identity() -> str:
    """The cue's mark in the recording converter's version: ``cue-r<revision>``."""
    return f"cue-r{CUE_REVISION}"


def options() -> dict[str, OptionValue]:
    """Every constant that can change a ``SPEAKING`` line: the lit threshold, the widening, the dark-pixel
    bound and the cue per profile."""
    return {
        "cue_revision": CUE_REVISION,
        "cue_lit_at": LIT_AT,
        "cue_widen_px": ",".join(map(str, _WIDEN_PX)),
        "cue_dark_below": _DARK_BELOW,
        **{f"cue_{profile}": cue for profile, cue in sorted(_CUES.items())},
    }
