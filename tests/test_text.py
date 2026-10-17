"""A known count's noun follows the count (B13 round 14 Q39)."""

from __future__ import annotations

import pytest

from agentsync import setup_report
from agentsync.convert.image import _pictures_read
from agentsync.text import plural


@pytest.mark.parametrize(
    ("n", "word", "many", "said"),
    [
        (1, "folder", None, "1 folder"),
        (2, "folder", None, "2 folders"),
        (0, "folder", None, "0 folders"),
        (1, "excluded id", None, "1 excluded id"),
        (3, "entry", "entries", "3 entries"),
    ],
)
def test_a_count_of_one_is_singular_and_any_other_plural(
    n: int, word: str, many: str | None, said: str
) -> None:
    assert plural(n, word, many) == said


def test_the_lines_q39_names_take_their_noun_from_the_count() -> None:
    """Round 14 Q39: known counts read "listed 1 folder(s)", "text of 1 picture(s) read" and the like."""
    assert setup_report._plural is plural, "one helper, setup_report's before it"
    assert setup_report._list_folders_note("listed-1") == "listed 1 folder"
    assert setup_report._list_folders_note("listed-6") == "listed 6 folders"
    assert _pictures_read(1) == "text of 1 picture read by on-device OCR"
    assert _pictures_read(2) == "text of 2 pictures read by on-device OCR"
