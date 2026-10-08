"""convert/cue.py, the speaker cue (spec S7) as pure functions.  Every name is made up (Contoso)."""

from __future__ import annotations

from agentsync.convert import cue
from agentsync.convert.cue import LIT_AT, Label, lit_sets, requests, speaking

DANA = Label("Dana Okafor", (0.90, 0.20, 0.08, 0.02))
LUIS = Label("Luis Fernandez", (0.90, 0.30, 0.08, 0.02))
MOVED_DANA = Label("Dana Okafor", (0.90, 0.40, 0.08, 0.02))


def test_each_tick_asks_for_the_boxes_of_the_last_read_candidate_at_or_before_it() -> None:
    labels = {3: [DANA, LUIS], 7: [MOVED_DANA], 9: []}
    assert requests("teams", labels, [0, 2, 3, 4, 6, 7, 8, 9, 12]) == [
        (3, (DANA, LUIS)),
        (4, (DANA, LUIS)),
        (6, (DANA, LUIS)),
        (7, (MOVED_DANA,)),
        (8, (MOVED_DANA,)),
    ], "before the first candidate, and after one that read no label, nothing is asked"
    assert requests("teams", labels, [8, 4]) == [(8, (MOVED_DANA,)), (4, (DANA, LUIS))], "in the order asked"
    assert requests("teams", {}, [0, 1]) == []


def test_a_profile_without_a_cue_asks_nothing() -> None:
    assert cue.has_cue("teams")
    for profile in ("meet", "zoom", "webex", "jitsi", "unknown", ""):
        assert not cue.has_cue(profile)
        assert requests(profile, {0: [DANA]}, [0, 1]) == []


def test_a_label_is_lit_at_fifty_and_not_at_forty_nine() -> None:
    assert LIT_AT == 50
    asked = [(1, (DANA, LUIS)), (2, (DANA, LUIS)), (3, (LUIS, DANA)), (4, ())]
    values = {1: (49, 50), 2: (80, 70), 3: (20, 255), 4: ()}
    assert lit_sets(asked, values) == {
        1: ("Luis Fernandez",),
        2: ("Dana Okafor", "Luis Fernandez"),
        3: ("Dana Okafor",),
        4: (),
    }, "in box order"


def test_speaking_writes_one_line_per_change_of_the_single_lit_label() -> None:
    lit = {
        9: ("Luis Fernandez",),
        1: ("Dana Okafor",),
        2: ("Dana Okafor",),
        3: ("Dana Okafor", "Luis Fernandez"),
        4: (),
        5: ("Dana Okafor",),
        6: ("Luis Fernandez",),
        7: (),
        8: ("Luis Fernandez", "Dana Okafor"),
    }
    assert speaking(lit) == [(1, "Dana Okafor"), (6, "Luis Fernandez")], (
        "two lit or none write nothing and do not reset the last line; tick order, not insertion order"
    )
    assert speaking({}) == [] and speaking({1: (), 2: ("A", "B")}) == []


def test_identity_and_options_carry_every_constant() -> None:
    assert cue.identity() == f"cue-r{cue.CUE_REVISION}" == "cue-r1"
    assert cue.options() == {
        "cue_revision": 1,
        "cue_lit_at": 50,
        "cue_widen_px": "4,2",
        "cue_dark_below": 600,
        "cue_teams": "the fill of label boxes: median B - R of their dark pixels",
    }
