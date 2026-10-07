"""A recording's pages from what S1 to S6 settled: S9 fusion and render, the index of spec 3.5.

:func:`render` is a pure function of a :class:`~agentsync.convert.recording.Reading`: one index unit
(``00-index``) and one unit per five-minute window (``NN-tHHMMSS``), each body in the line grammar of spec
3.3 that ``tests/test_recording_grammar.py`` pins.  Keyframes become the window's sidecars; the registry
guard adds the banner and the ``Sidecar file`` footer lines.  Every string read from the picture is cleaned
(S9 rule 5) and printed only after a time and a tag, never in a table cell and never at the start of a line.
"""

from __future__ import annotations

from agentsync.convert.recording import Reading
from agentsync.model import RenderedUnit


def render(reading: Reading, *, max_page_bytes: int) -> tuple[RenderedUnit, ...]:
    """The index unit and the window units of ``reading``, sorted by index; a window body past
    ``max_page_bytes`` is cut with its whole text in a ``full-text.txt`` sidecar (S9 rule 6)."""
    raise NotImplementedError
