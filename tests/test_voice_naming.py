"""Voice naming S8b (spec S8b, section 5, C3 to C7): the stream rule, the turn veto, the margin band, the floors,
and what the speech engine may never store or use (a voice vector, a FluidAudio build older than 04e363c)."""

from __future__ import annotations

# --- S8b rules: agentsync.convert.naming (teammate: naming) -----------------------------------------------------
import random
from collections.abc import Sequence

import pytest

from agentsync.convert import naming
from agentsync.convert.naming import Naming, Voice, name_voices, vetoed, voice_text
from test_recording_grammar import INDEX_ONLY_NOTES, VOICE_FORMS, WINDOW_NOTES

STEP = 2000
ROOM = "Contoso Room 4"
DANA = "Dana Okafor (Contoso)"


def recording(*runs: tuple[int, str | None, int]) -> tuple[list[Voice], dict[int, list[str]]]:
    """Voices and lit labels from runs of ``(voice, lit label or None, ticks)``: one tick each, in order, the
    voice speaking at it and the label (if any) the only one lit."""
    spans: dict[int, list[tuple[int, int]]] = {}
    lit: dict[int, list[str]] = {}
    tick = 0
    for voice, label, count in runs:
        for _ in range(count):
            spans.setdefault(voice, []).append((tick * STEP, tick * STEP + 1500))
            if label is not None:
                lit[tick] = [label]
            tick += 1
    return [Voice(v, tuple(s)) for v, s in sorted(spans.items())], lit


def by_number(namings: Sequence[Naming]) -> dict[int, Naming]:
    return {n.number: n for n in namings}


@pytest.fixture
def checked(monkeypatch: pytest.MonkeyPatch) -> str:
    """A layout profile whose names a listen has checked."""
    monkeypatch.setattr(naming, "CHECKED_PROFILES", frozenset({"teams"}))
    return "teams"


def test_a_voice_takes_a_label_only_when_the_stream_is_one_voice_and_the_voice_sits_under_it(
    checked: str,
) -> None:
    voices, lit = recording((1, DANA, 30), (1, ROOM, 1), (2, ROOM, 2))
    v1 = by_number(name_voices(voices, lit, profile=checked))[1]
    assert (v1.form, v1.label, v1.seen, v1.lit, v1.percent) == ("named", DANA, 30, 31, "100")
    assert voice_text(v1) == f"v1 · {DANA} · seen: 30 of 31 lit samples; 100 % of the label's lit speech"

    # A second voice holding 5 of 41 lit samples: the stream is not one voice, so nobody takes it.
    voices, lit = recording((1, DANA, 36), (2, DANA, 5), (2, ROOM, 30))
    assert [n.form for n in name_voices(voices, lit, profile=checked)] == ["shared", "mixed"]

    # The label's audio is one voice, but not this one: a voice that does not hold s(L) takes nothing.
    voices, lit = recording((1, DANA, 190), (2, DANA, 10))
    named = by_number(name_voices(voices, lit, profile=checked))
    assert (named[1].form, named[2].form) == ("named", "unidentified")
    assert named[1].percent == "95"


def test_a_shared_stream_prints_shared_audio_and_never_the_name(checked: str) -> None:
    voices, lit = recording((1, ROOM, 12), (2, ROOM, 11), (3, ROOM, 10))
    for profile in (checked, "zoom"):
        namings = name_voices(voices, lit, profile=profile)
        assert [n.form for n in namings] == ["shared", "shared", "shared"]
        assert [voice_text(n) for n in namings] == [
            f"v{v} · shared audio of {ROOM}, 3 voices" for v in (1, 2, 3)
        ]
        assert not any(n.gated or n.held_back for n in namings)


def test_a_turn_whose_own_lit_samples_point_elsewhere_stays_unnamed(checked: str) -> None:
    voices, lit = recording((1, DANA, 40), (2, ROOM, 0))
    v1 = name_voices(voices, lit, profile=checked)[0]
    assert v1.form == "named"
    line = {50: [ROOM], 51: [ROOM], 52: [DANA], 53: [DANA, ROOM]}
    assert vetoed(v1, 100_000, 106_000, line)  # ticks 50-52: two show the room
    assert not vetoed(v1, 103_000, 108_000, line)  # ticks 52-53: one lit sample, the voice's own label
    assert not vetoed(v1, 107_000, 108_000, line)  # tick 53 has two labels lit: no lit sample
    assert not vetoed(v1, 109_000, 112_000, line)  # nothing lit
    unidentified = Naming(1, "unidentified", DANA, 40, 40, "100", 1, held_back=False, gated=True)
    assert not vetoed(unidentified, 100_000, 106_000, line)
    note = naming.veto_note(12)
    assert note == "v12 is not named on this line: its lit samples show another label"
    assert any(p.fullmatch(note) for p in WINDOW_NOTES)


def test_a_value_in_the_margin_band_holds_the_name_back_with_a_note(checked: str) -> None:
    # s(L) = 23 / 25 = 0.92
    voices, lit = recording((1, DANA, 23), (2, DANA, 2), (2, ROOM, 30))
    v1 = name_voices(voices, lit, profile=checked)[0]
    assert (v1.form, v1.held_back, v1.gated) == ("unidentified", True, False)
    assert voice_text(v1) == "v1 · unidentified"
    # p_v = 46 / 50 = 0.92
    voices, lit = recording((1, DANA, 46), (1, ROOM, 4), (2, ROOM, 30))
    assert name_voices(voices, lit, profile=checked)[0].held_back
    # 0.95 is outside the band: 38 / 40 under the label.
    voices, lit = recording((1, DANA, 38), (1, ROOM, 2), (2, ROOM, 30))
    assert name_voices(voices, lit, profile=checked)[0].form == "named"
    assert naming.HELD_BACK_NOTE == "name held back: a share inside the margin band"
    assert any(p.fullmatch(naming.HELD_BACK_NOTE) for p in INDEX_ONLY_NOTES)


def test_too_few_lit_samples_names_nobody(checked: str) -> None:
    # 9 lit samples of the voice; then a label with 19 lit samples, under the stream minimum.
    for runs in ([(1, DANA, 9), (1, None, 40)], [(1, DANA, 19), (1, None, 40)]):
        v1 = name_voices(*recording(*runs), profile=checked)[0]
        assert (v1.form, v1.held_back) == ("unidentified", False)
    v1 = name_voices(*recording((1, None, 40)), profile=checked)[0]
    assert (v1.form, v1.label, v1.lit) == ("unidentified", None, 0)
    assert name_voices([], {3: [DANA]}, profile=checked) == ()


def test_the_thresholds_are_in_the_options_and_the_version_carries_n() -> None:
    assert naming.options() == {
        "naming_p_min": 0.90,
        "naming_n_min": 10,
        "naming_s_min": 0.90,
        "naming_stream_min": 20,
        "naming_band": 0.05,
        "naming_checked_profiles": "",
    }
    assert naming.identity() == "n1"


def test_an_unchecked_layout_prints_a_voice_that_takes_its_label_as_unidentified_with_one_note() -> None:
    assert not naming.CHECKED_PROFILES
    voices, lit = recording((1, DANA, 40), (2, ROOM, 0))
    v1 = name_voices(voices, lit, profile="teams")[0]
    assert (v1.form, v1.gated, v1.held_back, v1.label) == ("unidentified", True, False, DANA)
    assert voice_text(v1) == "v1 · unidentified"
    assert any(p.fullmatch(naming.GATED_NOTE) for p in INDEX_ONLY_NOTES)
    assert not any(p.fullmatch(naming.GATED_NOTE) for p in WINDOW_NOTES)


def test_one_account_carried_by_one_voice_and_one_shared_by_four_names_one_voice(checked: str) -> None:
    # v3 A.2's shape: s(presenter) = 99 / 100; s(room) = 33 / 100.
    voices, lit = recording(
        (1, DANA, 99), (5, DANA, 1), (2, ROOM, 33), (3, ROOM, 30), (4, ROOM, 27), (5, ROOM, 10)
    )
    texts = [voice_text(n) for n in name_voices(voices, lit, profile=checked)]
    assert texts == [
        f"v1 · {DANA} · seen: 99 of 99 lit samples; 99 % of the label's lit speech",
        *(f"v{v} · shared audio of {ROOM}, 4 voices" for v in (2, 3, 4, 5)),
    ]


def test_a_voice_spread_over_labels_is_mixed(checked: str) -> None:
    voices, lit = recording((1, DANA, 6), (1, ROOM, 5), (2, ROOM, 30))
    v1 = name_voices(voices, lit, profile=checked)[0]
    assert (v1.form, v1.label, v1.seen, v1.lit) == ("mixed", DANA, 6, 11)
    assert voice_text(v1) == "v1 · mixed"


def test_a_tick_inside_two_voices_speech_counts_for_neither(checked: str) -> None:
    voices = [Voice(1, ((0, 100_000),)), Voice(2, ((0, 10_000),))]
    lit = {k: [ROOM if k < 5 else DANA] for k in range(50)}
    v1, v2 = name_voices(voices, lit, profile=checked)
    assert (v1.form, v1.seen, v1.lit, v1.voices_on_label) == ("named", 45, 45, 1)
    assert (v2.form, v2.lit) == ("unidentified", 0)


def test_ties_are_broken_by_count_then_label_text_whatever_the_input_order(checked: str) -> None:
    voices, lit = recording((1, "Bravo Contoso", 10), (1, "Alpha Contoso", 10), (2, ROOM, 30))
    first = name_voices(voices, lit, profile=checked)
    assert (first[0].form, first[0].label) == ("mixed", "Alpha Contoso")
    shuffled = list(lit.items())
    random.Random(7).shuffle(shuffled)
    assert name_voices(list(reversed(voices)), dict(shuffled), profile=checked) == first
    v1 = name_voices(*recording((1, "Bravo Contoso", 40)), profile=checked)[0]
    assert vetoed(v1, 0, 4000, {0: ["Bravo Contoso"], 1: ["Alpha Contoso"]})


def test_every_voice_line_is_one_of_the_grammars_four_forms(checked: str) -> None:
    voices, lit = recording(
        (1, DANA, 99), (2, ROOM, 33), (3, ROOM, 30), (4, "Mei Tanaka", 6), (4, ROOM, 5), (5, None, 3)
    )
    texts = [
        voice_text(n) for profile in (checked, "zoom") for n in name_voices(voices, lit, profile=profile)
    ]
    assert {t.split(" · ")[1].split(" ")[0] for t in texts} >= {
        "Dana",
        "shared",
        "mixed",
        "unidentified",
    }
    for text in texts:
        assert any(p.fullmatch(text) for p in VOICE_FORMS), text


# --- end S8b rules ---------------------------------------------------------------------------------------------






# --- the pin and the privacy rule: agentsync.convert.speech (teammate: speech) ---------------------------------






# --- end the pin and the privacy rule --------------------------------------------------------------------------
