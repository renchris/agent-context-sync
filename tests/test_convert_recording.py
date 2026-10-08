"""convert/recording.py: a meeting recording read through the fake media helper and the fake OCR helper (spec
9.3).

The fake media helper of ``tests/media_kit.py`` "shows" a script of screens; its ``frames`` writes
``FAKE-OCR:`` JPEGs that the fake OCR helper of ``tests/test_ocr.py`` reads, so every row a test puts on
screen is what OCR reads back.  Names are made up (Contoso).
"""

from __future__ import annotations

import contextlib
import itertools
import json
import random
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert import convert_file
from agentsync.convert import naming as naming_mod
from agentsync.convert import recording as rec
from agentsync.convert.cache import ConverterCache
from agentsync.convert.media import MediaEngine, MediaError
from agentsync.convert.ocr import OcrLine
from agentsync.convert.pieces import PieceStore
from agentsync.convert.registry import Registry
from agentsync.convert.speech import SpeechEngine, SpeechError
from agentsync.errors import UnreadableSourceError
from agentsync.materialise import sha256_file
from agentsync.model import ConversionStatus, RenderedUnit
from agentsync.policy import PolicyConfig
from media_kit import calls as media_calls
from media_kit import fake_media, fake_recording, recording, row, screen
from speech_kit import FAKE_PIN, fake_speech, place_models
from speech_kit import calls as speech_calls
from test_convert_core import LABEL_RULES
from test_ocr import fake_engine
from test_recording_grammar import index_errors, window_errors

CFG = ConvertConfig()


def converter(
    tmp_path: Path,
    *,
    pieces: PieceStore | None = None,
    label_rule: bool = False,
    speech: SpeechEngine | None = None,
    **media: Any,
) -> rec.RecordingConverter:
    helper = fake_media(tmp_path / "helpers", **media)
    engine = MediaEngine(helper, name="paper-media", helper_version="0.1.0")
    return rec.RecordingConverter(
        CFG, fake_engine(tmp_path / "helpers"), engine, pieces=pieces, label_rule=label_rule, speech=speech
    )


def hearing(tmp_path: Path, script: dict[str, Any] | None = None, **kw: Any) -> SpeechEngine:
    """A speech engine over the fake speech helper of ``tests/speech_kit.py``: ``script`` is what the sound
    holds."""
    return SpeechEngine(
        fake_speech(tmp_path / "helpers", script, **kw),
        place_models(tmp_path / "speech"),
        name="paper-speech",
        helper_version="0.1.0",
        fluidaudio=FAKE_PIN,
        model_digest=DIGEST,
    )


DIGEST = "0123456789ab" + "c" * 52


def talk(*turns: tuple[str, int, str]) -> dict[str, Any]:
    """A speech script: per turn ``(speaker, start_ms, text)`` one word every 400 ms, and one diarizer segment
    over the turn's words."""
    words: list[list[Any]] = []
    segments: list[list[Any]] = []
    for speaker, start, text in turns:
        said = text.split()
        words += [[w, start + 400 * i, start + 400 * (i + 1)] for i, w in enumerate(said)]
        segments.append([speaker, start, start + 400 * len(said)])
    return {"words": words, "segments": segments}


def staged(tmp_path: Path, script: dict[str, Any], name: str = "meeting.mp4") -> Path:
    folder = tmp_path / "staging"
    folder.mkdir(parents=True, exist_ok=True)
    return fake_recording(folder / name, script)


def reading(tmp_path: Path, script: dict[str, Any], **kw: Any) -> rec.Reading:
    src = staged(tmp_path, script)
    return converter(tmp_path, **kw)._reading(src, name="meeting.mp4")


# ---------------------------------------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------------------------------------

STRIP = (0.872, 0.0, 1.0, 1.0, 120)  # the teams strip of camera tiles beside the pane
LABEL = row("Avery Chen", 0.01, 0.965, 0.08, 0.03)  # the sharer label, bottom left of the pane
CARD = (
    row("Microsoft Teams", 0.30, 0.20, 0.40, 0.05),
    row("Contoso storage capacity review", 0.20, 0.35, 0.60, 0.06),
    row("2026-10-02 14:03 UTC", 0.30, 0.50, 0.40, 0.04),
    row("Recorded by: Avery Chen", 0.30, 0.60, 0.40, 0.04),
)


def slide(title: str, *bullets: str) -> list[list[Any]]:
    """A slide in the teams pane: a tall title and bullet lines, each long enough to count."""
    out = [row(title, 0.05, 0.10, 0.60, 0.08)]
    out += [row(text, 0.05, 0.25 + 0.08 * i, 0.60, 0.04) for i, text in enumerate(bullets)]
    return out


TILE = row("Blake Ortiz", 0.89, 0.30, 0.08, 0.03)  # a camera tile's name in the teams strip
FIRST = slide(
    "Demand forecast by region",
    "North region grows by a tenth",
    "South region is flat this year",
    "West region needs two new racks",
    "East region moves to the new hall",
    "Status: approved by finance",
)
SECOND = slide(
    "Rack plan for the next quarter",
    "Order eight racks in November",
    "Install them in hall B by January",
    "Retire the old arrays in March",
    "Budget is 1.4 million dollars",
    "Owner: the platform team",
)
THIRD = slide(
    "Risks and open questions",
    "Power in hall B is not confirmed",
    "Vendor lead times may slip",
    "Network change window is unclear",
    "Two staff on leave in December",
    "Decision needed from the board",
)


def share(at: int, rows: list[list[Any]], *extra: list[Any], **kw: Any) -> dict[str, Any]:
    """A teams share from tick ``at``: the slide's rows in the pane, the sharer label, a tile in the strip."""
    return screen(at, *rows, LABEL, TILE, *extra, paint=[STRIP, *kw.pop("paint", ())], **kw)


def camera(start: int, end: int, *rows: list[Any], strip: bool = True) -> list[dict[str, Any]]:
    """A camera picture from tick ``start`` to ``end``: a moving face changes its pixels at every tick."""
    paint = [STRIP] if strip else []
    return [
        screen(k, *rows, paint=[*paint, (0.10, 0.10, 0.80, 0.90, 40 + (k * 37) % 150)])
        for k in range(start, end)
    ]


def teams(*screens: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """A teams recording: the title card at tick 0, then ``screens``; 60 s unless ``duration_ms``."""
    return recording(screen(0, *CARD), *screens, **kw)


def stored(store: PieceStore) -> list[dict[str, Any]]:
    """Every piece in the store, past the header line ``PieceStore.save`` writes."""
    return [
        json.loads(path.read_bytes().split(b"\n", 1)[1]) for path in sorted(store.root.glob("*/piece-*.bin"))
    ]


def reads_of(store: PieceStore) -> list[int]:
    """The ticks read, from the stored pieces."""
    return sorted(r["tick"] for doc in stored(store) for r in doc["reads"])


def gated_of(store: PieceStore) -> list[int]:
    return sorted(t for doc in stored(store) for t in doc["gated"])


def picture_size(data: bytes) -> list[int]:
    """The size the fake media helper wrote into a keyframe."""
    return json.loads(data[data.index(b"FAKE-OCR:") + 9 :])["size"]  # type: ignore[no-any-return]


def texts(got: rec.Reading, tag: str | None = None) -> list[str]:
    return [r.text for r in got.rows if tag is None or r.tag == tag]


def pages(
    tmp_path: Path, script: dict[str, Any], name: str = "meeting.mp4", **kw: Any
) -> tuple[RenderedUnit, ...]:
    """The units of the guarded registry, as the cycle gets them."""
    helper = fake_media(tmp_path / "helpers")
    media = MediaEngine(helper, name="paper-media", helper_version="0.1.0")
    registry = Registry.default(CFG, ocr=fake_engine(tmp_path / "helpers"), media=media, **kw)
    conv = registry.for_name(name)
    assert conv is not None
    return tuple(conv.convert(staged(tmp_path, script, name), name=name))


# ---------------------------------------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------------------------------------


def test_a_recording_gives_an_index_and_one_unit_per_five_minute_window(tmp_path: Path) -> None:
    got = reading(tmp_path, teams(share(3, FIRST), share(160, SECOND), duration_ms=660_000))
    assert (got.duration_ms, got.read_ms, got.width, got.height) == (660_000, 660_000, 1920, 1080)
    assert got.created == "2026-10-02T14:03:12Z" and got.profile == "teams"
    units = pages(tmp_path / "pages", teams(share(3, FIRST), share(160, SECOND), duration_ms=660_000))
    assert [(u.unit_id, u.index, u.of) for u in units] == [
        ("index", 0, 4),
        ("window:1", 1, 4),
        ("window:2", 2, 4),
        ("window:3", 3, 4),
    ]
    assert [u.file_stem for u in units] == ["00-index", "01-t000000", "02-t000500", "03-t001000"]


def test_every_line_is_the_banner_the_title_a_heading_a_tagged_line_or_a_footer(tmp_path: Path) -> None:
    script = teams(
        share(3, FIRST),
        share(15, SECOND),
        *camera(30, 60, row("Blake Ortiz", 0.05, 0.85, 0.20, 0.04)),
        share(70, FIRST),
        duration_ms=400_000,
    )
    units = pages(tmp_path, script)
    index, windows = units[0], units[1:]
    assert index_errors(index.body) == []
    for unit in windows:
        assert window_errors(unit.body) == [], unit.unit_id


def test_an_unchanged_screen_is_read_once(tmp_path: Path) -> None:
    store = PieceStore(tmp_path / "recordings")
    got = reading(tmp_path, teams(share(3, FIRST)), pieces=store)
    assert reads_of(store) == [0, 3]
    assert [(s.kind, s.start_ms, [k.ms for k in s.keyframes]) for s in got.states] == [
        ("camera", 0, [0]),
        ("share", 6_000, [6_000]),
    ]
    assert next((r.text, r.start_ms, r.end_ms) for r in got.rows if r.tag == "SCREEN") == (
        "Demand forecast by region",
        6_000,
        60_000,
    )


def test_the_teams_profile_needs_the_title_card_and_masks_the_strip_and_the_label(tmp_path: Path) -> None:
    no_card = recording(share(0, FIRST), share(15, SECOND))
    assert reading(tmp_path / "a", no_card).profile == "generic"
    no_pane = recording(screen(0, *CARD), screen(3, *FIRST, LABEL))
    assert reading(tmp_path / "b", no_pane).profile == "generic"
    store = PieceStore(tmp_path / "recordings")
    other_label = row("Casey Diaz", 0.01, 0.965, 0.08, 0.03)
    other_tile = row("Drew Evans", 0.89, 0.30, 0.08, 0.03)
    script = teams(share(3, FIRST), screen(10, *FIRST, other_label, other_tile, paint=[STRIP]))
    got = reading(tmp_path / "c", script, pieces=store)
    assert (
        got.profile == "teams"
        and got.profile_reason == "the title card and the pane edge, read from the picture"
    )
    assert reads_of(store) == [0, 3], "a new name in the strip or the label box is no candidate"


def test_a_profile_is_decided_from_the_picture_never_the_file_name(tmp_path: Path) -> None:
    script = teams(share(3, FIRST))
    for i, name in enumerate(["GMT20261002-140312_Recording_1920x1080.mp4", "Teams Meeting Recording.mp4"]):
        src = staged(tmp_path / str(i), script, name)
        assert converter(tmp_path / str(i))._reading(src, name=name).profile == "teams"
    plain = recording(screen(0, *FIRST))
    src = staged(tmp_path / "x", plain, "Microsoft Teams meeting-20261002_140312-Meeting Recording.mp4")
    assert converter(tmp_path / "x")._reading(src, name=src.name).profile == "generic"


def test_meet_is_found_by_its_content_edge_and_anything_else_is_generic(tmp_path: Path) -> None:
    rows = [
        row("Quarterly storage review agenda", 0.05, 0.20, 0.60, 0.05),
        row("Capacity is at eighty percent", 0.05, 0.30, 0.60, 0.04),
    ]
    tiles = (0.75, 0.0, 1.0, 1.0, 130)
    got = reading(tmp_path / "meet", recording(screen(0, *rows, paint=[tiles])))
    assert (got.profile, got.profile_reason) == (
        "meet",
        "the content edge at x = 0.75, read from the picture",
    )
    assert [s.kind for s in got.states] == ["share"]
    assert picture_size(got.states[0].keyframes[0].data) == [1440, 951], (
        "the content crop, x < 0.75, y > 0.12"
    )
    plain = reading(tmp_path / "generic", recording(screen(0, *rows)))
    assert (plain.profile, plain.profile_reason) == ("generic", "no layout recognised in the picture")


def _line(text: str, x: float, y: float, w: float = 0.5, h: float = 0.04) -> OcrLine:
    return OcrLine(text, 1.0, x, y, w, h)


def test_teams_share_is_t3_and_every_other_profile_is_r4() -> None:
    teams_p, generic, meet = rec._PROFILES["teams"], rec._PROFILES["generic"], rec._PROFILES["meet"]
    cells = 50_000
    label = _line("Avery Chen", 0.01, 0.965, 0.08, 0.03)
    one = [label, _line("Rack plan for the next quarter", 0.05, 0.2)]
    five = [
        label,
        *(_line(f"Long content line number {w}", 0.05, 0.2 + 0.05 * i) for i, w in enumerate("abcde")),
    ]

    def kind(profile: Any, lines: list[OcrLine], *, edge: bool = True, change: int = 0) -> str:
        return rec._kind(profile, lines, edge=edge, change=change, cells=cells)

    assert kind(teams_p, one) == "share", "T3: label, pane edge and a static pane"
    assert kind(teams_p, one, change=cells // 20) == "camera", "a moving pane with one line"
    assert kind(teams_p, five, change=cells // 20) == "share", "a moving pane with 5 long lines"
    assert kind(teams_p, five, edge=False) == "camera", "no pane edge"
    assert kind(teams_p, five[1:]) == "other", "5 content lines and no label"
    two = [
        _line("Rack plan for the next quarter", 0.05, 0.2),
        _line("Order eight racks in November", 0.05, 0.3),
    ]
    assert kind(generic, two) == "share", "R4: 2 long lines and a still picture"
    assert kind(generic, two, change=cells // 10) == "camera", "R4: the picture moves"
    assert kind(generic, [*two[:1], label]) == "camera", "a name is not a long line; no label rule off teams"
    tiles = [
        _line("Rack plan for the next quarter", 0.80, 0.2, 0.15),
        _line("Order eight racks", 0.80, 0.3, 0.15),
    ]
    assert kind(meet, tiles) == "camera", "R4 counts lines in the mask only"


def test_a_candidate_is_share_camera_or_other_by_lines_and_label(tmp_path: Path) -> None:
    gallery = [
        row(name, 0.05 + 0.27 * (i % 3), 0.30 + 0.30 * (i // 3), 0.20, 0.04)
        for i, name in enumerate(
            ["Avery Chen", "Blake Ortiz", "Casey Diaz", "Drew Evans", "Emery Fox", "Finley Gray"]
        )
    ]
    script = teams(
        share(3, FIRST),
        screen(10, *gallery, paint=[STRIP]),
        screen(20, row("Blake Ortiz", 0.05, 0.85, 0.2, 0.04), paint=[STRIP, (0.1, 0.1, 0.8, 0.8, 90)]),
    )
    got = reading(tmp_path, script)
    assert [(s.kind, s.start_ms) for s in got.states] == [
        ("camera", 0),
        ("share", 6_000),
        ("other", 20_000),
        ("camera", 40_000),
    ]


def test_camera_time_is_read_every_ten_seconds_and_a_share_start_is_exact(tmp_path: Path) -> None:
    store = PieceStore(tmp_path / "recordings")
    got = reading(tmp_path, teams(*camera(1, 13), share(13, FIRST)), pieces=store)
    assert reads_of(store) == [0, 5, 10, 12, 13], "every 10 s on camera; the share's first tick caught up"
    shared = [s for s in got.states if s.kind == "share"]
    assert [(s.start_ms, s.keyframes[0].ms) for s in shared] == [(26_000, 26_000)]


def test_a_row_on_screen_under_four_seconds_is_counted_not_printed(tmp_path: Path) -> None:
    popup = row("Saved to OneDrive", 0.60, 0.80, 0.20, 0.03)
    got = reading(tmp_path, teams(share(3, FIRST), share(10, FIRST, popup), share(11, FIRST)))
    assert "Saved to OneDrive" not in texts(got) and got.unprinted_rows == 1
    assert len([s for s in got.states if s.kind == "share"]) == 1


def test_a_changed_cell_gives_a_removed_and_an_added_line_never_a_merged_one(tmp_path: Path) -> None:
    changed = [
        r if r[0] != "Status: approved by finance" else ["Status: approved by finance staff", *r[1:]]
        for r in FIRST
    ]
    got = reading(tmp_path, teams(share(3, FIRST), share(12, changed)))
    old = next(r for r in got.rows if r.text == "Status: approved by finance")
    new = next(r for r in got.rows if r.text == "Status: approved by finance staff")
    assert (old.start_ms, old.end_ms, new.start_ms) == (6_000, 24_000, 24_000)


def test_two_reads_with_different_digits_are_two_rows(tmp_path: Path) -> None:
    grid = bytes(320 * 180)
    a = rec._Seen("content", "Budget is 1.4 million dollars", 1.0, 0.05, 0.5, 0.7, 0.04)
    assert not rec._same(
        a, rec._Seen("content", "Budget is 1.5 million dollars", 1.0, 0.05, 0.5, 0.7, 0.04), (grid, grid)
    )
    assert rec._same(
        a, rec._Seen("content", "Budget is 1.4 milion dollars", 0.5, 0.05, 0.5, 0.7, 0.04), (grid, grid)
    )
    changed = [
        r if r[0] != "Budget is 1.4 million dollars" else ["Budget is 1.5 million dollars", *r[1:]]
        for r in SECOND
    ]
    got = reading(tmp_path, teams(share(3, SECOND), share(12, changed)))
    assert {"Budget is 1.4 million dollars", "Budget is 1.5 million dollars"} <= set(texts(got, "SCREEN"))


def test_a_state_opens_past_thirty_percent_changed_and_held_four_seconds(tmp_path: Path) -> None:
    def shares(script: dict[str, Any], folder: str) -> list[tuple[int, int]]:
        got = reading(tmp_path / folder, script)
        return [(s.start_ms, s.keyframes[0].ms) for s in got.states if s.kind == "share"]

    assert shares(teams(share(3, FIRST), share(15, SECOND)), "new") == [(6_000, 6_000), (30_000, 30_000)]
    one_line = [
        r if r[0] != "Status: approved by finance" else ["Status: sent back to finance", *r[1:]]
        for r in FIRST
    ]
    assert shares(teams(share(3, FIRST), share(15, one_line)), "small") == [(6_000, 6_000)]
    assert shares(teams(share(3, FIRST), share(15, THIRD), share(16, FIRST)), "blip") == [(6_000, 6_000)]
    folded = shares(teams(share(3, FIRST), share(15, THIRD), share(16, SECOND)), "short")
    assert folded == [(6_000, 6_000), (30_000, 32_000)], "a 2 s state joins the next; its first frame is K1"


def test_a_screen_shown_again_points_to_the_first_keyframe_and_prints_no_rows(tmp_path: Path) -> None:
    got = reading(tmp_path, teams(share(3, FIRST), share(15, SECOND), share(25, FIRST)))
    assert [(s.number, s.kind, s.start_ms, s.revisit_of, len(s.keyframes)) for s in got.states] == [
        (1, "camera", 0, None, 1),
        (2, "share", 6_000, None, 1),
        (3, "share", 30_000, None, 1),
        (4, "share", 50_000, 2, 0),
    ]
    assert got.states[3].label == got.states[1].label == "Demand forecast by region"


def test_a_camera_gallery_or_generic_state_stores_the_full_frame(tmp_path: Path) -> None:
    got = reading(tmp_path / "teams", teams(share(3, FIRST)))
    assert got.states[0].kind == "camera" and picture_size(got.states[0].keyframes[0].data) == [1920, 1080]
    assert [n.text for n in got.states[0].notes] == [rec._NOTE_FULL_FRAME]
    plain = reading(tmp_path / "generic", recording(screen(0, *FIRST)))
    assert [(s.kind, picture_size(s.keyframes[0].data)) for s in plain.states] == [("share", [1920, 1080])]
    assert [n.text for n in plain.states[0].notes] == [rec._NOTE_GENERIC]


def test_a_teams_share_keyframe_is_the_content_column(tmp_path: Path) -> None:
    got = reading(tmp_path, teams(share(3, FIRST)))
    (keyframe,) = got.states[1].keyframes
    assert picture_size(keyframe.data) == [1674, 1080], "columns 0 to floor(0.872 x 1920) - 1"
    assert got.states[1].notes == ()


def _many(count: int) -> dict[str, Any]:
    slides = [
        share(
            3 + 3 * i,
            slide(f"Section {i} overview", f"Page {i} of the deck in detail", f"Owner of part {i} speaks"),
        )
        for i in range(count)
    ]
    return teams(*slides, duration_ms=(3 * count + 6) * 2_000)


def test_keyframes_are_bounded_by_the_read_limit_and_have_no_cap_of_their_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = reading(tmp_path / "all", _many(40))
    first_window = [k for s in got.states for k in s.keyframes if k.ms < 300_000]
    assert len(first_window) >= 41, "no cap per window or per recording"
    monkeypatch.setattr(rec, "_MAX_READS", 10)
    store = PieceStore(tmp_path / "recordings")
    cut = reading(tmp_path / "cut", _many(40), pieces=store)
    assert len(reads_of(store)) == 10 and sum(len(s.keyframes) for s in cut.states) <= 10


def test_a_keyframe_has_its_rows_on_the_page(tmp_path: Path) -> None:
    (_index, window) = pages(tmp_path / "pages", teams(share(3, FIRST), share(15, SECOND)))
    pictures = sorted(name for name, _data in window.sidecars if name.endswith(".jpg"))
    assert pictures == ["t000000.jpg", "t000006.jpg", "t000030.jpg"]
    for line in [r[0] for r in (*CARD, *FIRST, *SECOND)]:
        assert re.search(rf"^\[\d\d:\d\d:\d\d\] (SCREEN|TILE): {re.escape(line)}$", window.body, re.M), line
    got = reading(tmp_path, teams(share(3, FIRST), share(15, SECOND)))
    for state in got.states:
        for keyframe in state.keyframes:
            shown = {r.text for r in got.rows if r.start_ms <= keyframe.ms < r.end_ms}
            on_picture = {
                r[0] for r in (CARD if keyframe.ms == 0 else FIRST if keyframe.ms < 30_000 else SECOND)
            }
            assert on_picture <= shown, keyframe.ms


def test_neither_a_body_a_title_nor_a_summary_holds_the_files_name(tmp_path: Path) -> None:
    name = "Fabrikam merger secret plan-20261002_140312-Meeting Recording.mp4"
    units = pages(tmp_path, teams(share(3, FIRST)), name=name)
    for unit in units:
        for text in (unit.body, unit.title, unit.summary):
            assert "Fabrikam" not in text and "secret plan" not in text and "20261002_140312" not in text


def test_a_second_cold_conversion_is_byte_identical(tmp_path: Path) -> None:
    script = teams(share(3, FIRST), *camera(10, 20), share(20, SECOND))
    assert reading(tmp_path / "one", script) == reading(tmp_path / "two", script)
    first = pages(tmp_path / "three", script)
    assert first == pages(tmp_path / "four", script)


def test_running_out_of_time_gives_no_page_and_the_refusal_a_reread_looks_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A piece past its deadline is discarded: nothing is stored or cached, and ``convert_file`` re-raises
    the signal for the cycle, which lets the recording wait; it is never a FAILED result."""
    clock = itertools.count(0.0, 1_000.0)
    monkeypatch.setattr(rec, "_clock", lambda: next(clock))
    store = PieceStore(tmp_path / "recordings")
    src = staged(tmp_path, teams(share(3, FIRST)))
    with pytest.raises(rec.RecordingNotFinished) as caught:
        converter(tmp_path, pieces=store)._reading(src, name="meeting.mp4")
    assert (caught.value.done_ms, caught.value.total_ms, caught.value.timed_out) == (0, 60_000, True)
    assert stored(store) == [] and store.pending() == []
    cache = ConverterCache(tmp_path / "cache")
    with pytest.raises(rec.RecordingNotFinished):
        convert_file(
            src,
            name="meeting.mp4",
            content_sha256="0" * 64,
            canonical_sha256="0" * 64,
            registry=_registry(tmp_path),
            cache=cache,
        )
    assert not any((tmp_path / "cache").rglob("*.json"))


def _registry(tmp_path: Path, **kw: Any) -> Registry:
    helper = fake_media(tmp_path / "helpers")
    media = MediaEngine(helper, name="paper-media", helper_version="0.1.0")
    return Registry.default(CFG, ocr=fake_engine(tmp_path / "helpers", **kw), media=media)


def test_a_file_that_is_not_mp4_m4v_or_mov_or_has_no_tracks_is_a_cached_stub(tmp_path: Path) -> None:
    conv = converter(tmp_path)
    folder = tmp_path / "staging"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"not a recording at all")
    with pytest.raises(
        UnreadableSourceError, match=r"^not a recording on-device reading supports \(MP4, M4V, MOV\)$"
    ):
        conv._reading(folder / "clip.mp4", name="clip.mp4")
    cache = ConverterCache(tmp_path / "cache")
    for _ in range(2):
        got = convert_file(
            folder / "clip.mp4",
            name="clip.mp4",
            content_sha256="1" * 64,
            canonical_sha256="1" * 64,
            registry=_registry(tmp_path),
            cache=cache,
        )
        assert got.status is ConversionStatus.UNREADABLE
    assert got.from_cache
    for script, reason in (
        (recording(picture=None, audio=False), "recording has no picture and no sound"),
        (
            recording(picture=None, audio=True),
            "recording has no picture; its speech is not read by this version",
        ),
    ):
        with pytest.raises(UnreadableSourceError, match=f"^{re.escape(reason)}$"):
            conv._reading(fake_recording(folder / "meeting.mp4", script), name="meeting.mp4")
    quicktime = folder / "meeting.mov"
    quicktime.write_bytes(
        b"\x00\x00\x00\x08moov" + b"FAKE-MEDIA:" + json.dumps(teams(share(3, FIRST))).encode()
    )
    assert conv._reading(quicktime, name="meeting.mov").profile == "teams"
    with pytest.raises(UnreadableSourceError):
        conv._reading(quicktime, name="meeting.mp4")


@pytest.mark.parametrize("codec", ["vp9", "av1"])
def test_a_vp9_or_av1_picture_is_a_stub_until_speech_and_then_a_speech_only_page(
    tmp_path: Path, codec: str
) -> None:
    conv = converter(tmp_path)
    src = staged(tmp_path, recording(picture=codec))
    reason = "recording's picture cannot be decoded on this Mac (VP9 or AV1)"
    with pytest.raises(UnreadableSourceError, match=f"^{re.escape(reason)}$"):
        conv._reading(src, name="meeting.mp4")
    assert not conv.outdated(conv.version(), reason), "no speech engine: the stub stands"
    heard = converter(tmp_path, speech=hearing(tmp_path, talk(("spk-a", 3_000, "Contoso budget review"))))
    assert heard.outdated(conv.version(), reason) and not heard.outdated(heard.version(), reason)
    got = heard._reading(src, name="meeting.mp4")
    assert got.picture_unread == reason and got.profile == "generic"
    assert (got.states, got.rows, got.names, got.speaking) == ((), (), (), None)
    assert got.speech is not None and [ln.text for ln in got.speech.reading.lines] == [
        "Contoso budget review"
    ]
    assert not any(
        c["args"][0] in {"scan", "frames"} for c in media_calls(tmp_path / "helpers" / "fake-media")
    )
    index, *windows = pages(tmp_path / "pages", recording(picture=codec), speech=heard._speech)
    assert "picture not read: recording's picture cannot be decoded on this Mac (VP9 or AV1); " in index.body
    assert "[00:00:03] SAID v1: Contoso budget review" in windows[0].body
    _grammar(index, *windows)
    with pytest.raises(UnreadableSourceError, match=f"^{re.escape(reason)}$"):
        heard._reading(staged(tmp_path, recording(picture=codec, audio=False)), name="meeting.mp4")


def test_the_registry_has_no_recording_converter_without_both_engines_and_keeps_it_under_a_label_rule(
    tmp_path: Path,
) -> None:
    ocr = fake_engine(tmp_path / "helpers")
    media = MediaEngine(fake_media(tmp_path / "helpers"), name="paper-media", helper_version="0.1.0")
    assert Registry.default(CFG).for_name("x.mp4") is None
    assert Registry.default(CFG, ocr=ocr).for_name("x.mp4") is None
    assert Registry.default(CFG, media=media).for_name("x.mp4") is None
    assert Registry.default(ConvertConfig(recordings=False), ocr=ocr, media=media).for_name("x.mp4") is None
    reg = Registry.default(CFG, ocr=ocr, media=media)
    for name in ("a.mp4", "b.M4V", "c.mov"):
        assert reg.for_name(name).converter_id == "recording-av"  # type: ignore[union-attr]
    assert reg.without_ocr is not None and reg.without_ocr.for_name("a.mp4") is None
    for rule in LABEL_RULES.values():
        labelled = Registry.default(CFG, policy=rule, ocr=ocr, media=media)
        conv = labelled.for_name("a.mp4")
        assert conv is not None and conv.inner._label_rule is True  # type: ignore[attr-defined]
        assert labelled.for_name("scan.png") is None, "images keep D8"
    rule = PolicyConfig(exclude_label_names=("Secret",))
    (index, *_windows) = pages(tmp_path / "pages", teams(share(3, FIRST)), policy=rule)
    assert (
        "label rule is set; this recording's own label cannot be read on a Mac and was not checked"
        in index.body
    )
    assert reg.for_name("a.mp4").inner._label_rule is False  # type: ignore[union-attr]


CONSTANTS = {
    "_MAX_TICKS": 10,
    "_PIECE_TICKS": 30,
    "_MAX_READS": 12,
    "_BASE_S": 61.0,
    "_TICK_S": 0.05,
    "_READ_S": 0.9,
    "_OCR_RUNS": 2,
    "_CELL": 13,
    "_GATE_RECOGNISED": 6,
    "_GATE_GENERIC": 21,
    "_REVISIT": 19,
    "_TEAMS_CONTENT_X": 0.8,
    "_LABEL_BOX": (0.0, 0.95, 0.10, 1.0),
    "_MEET_CONTENT": (0.0, 0.10, 0.75, 1.0),
    "_TEAMS_EDGE": 11,
    "_MEET_EDGE": 21,
    "_T3_LINES": 4,
    "_T3_STATIC": 2,
    "_R4_LINES": 3,
    "_R4_STATIC": 6,
    "_OTHER_LINES": 6,
    "_LONG_WORDS": 3,
    "_LONG_CHARS": 13,
    "_BACKOFF_TICKS": 4,
    "_CATCH_UP": 3,
    "_MOTION_READS": 6,
    "_NOT_LEVEL": 0.25,
    "_SIMILAR": 0.8,
    "_SHORTEST_MS": 6_000,
    "_NEW_STATE": 40,
    "_LABEL_CHARS": 50,
    "_LABEL_LETTERS": 5,
    "WINDOW_MS": 600_000,
    "MARK_BELOW": 0.5,
    "STEP_MS": 1_000,
    "JPEG_QUALITY": 0.8,
}


@pytest.mark.parametrize("name", CONSTANTS)
def test_every_constant_that_shapes_a_page_is_in_the_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    conv = converter(tmp_path)
    before = dict(conv.options())
    assert before["max_page_bytes"] == CFG.max_page_bytes and before["ocr_languages"] == "en-US"
    monkeypatch.setattr(rec, name, CONSTANTS[name])
    assert dict(conv.options()) != before, name


def test_a_recording_past_the_tick_limit_is_read_to_the_limit_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "_MAX_TICKS", 10)
    got = reading(tmp_path, teams(share(3, FIRST), share(15, SECOND)))
    assert (got.duration_ms, got.read_ms) == (60_000, 20_000)
    assert [s.start_ms for s in got.states] == [0, 6_000] and got.states[-1].end_ms == 20_000
    scans = [c["args"] for c in media_calls(tmp_path / "helpers" / "fake-media") if c["args"][0] == "scan"]
    assert all(int(a[a.index("--max-ticks") + 1]) <= 10 for a in scans)
    units = pages(tmp_path / "pages", teams(share(3, FIRST), share(15, SECOND)))
    note = "NOTE: recording read to 00:00:20 of 00:01:00 (limit)"
    assert note in units[0].body and note in units[-1].body


def test_candidates_past_the_limit_are_counted_not_read_and_the_page_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "_MAX_READS", 3)
    store = PieceStore(tmp_path / "recordings")
    script = teams(share(3, FIRST), share(9, SECOND), share(15, THIRD), share(21, FIRST), share(27, SECOND))
    got = reading(tmp_path, script, pieces=store)
    assert reads_of(store) == [0, 3, 9]
    assert got.screen_read_to == (18_000, 3)
    units = pages(tmp_path / "pages", script)
    assert "screen text read to 00:00:18; 3 later changes not read (limit)" in units[0].body


def test_the_time_limit_is_fixed_from_counts_before_the_frames_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "_clock", lambda: 1_000.0)
    seen: list[tuple[list[int], float]] = []
    read = rec._Work.read

    def spy(self: rec._Work, ticks: Sequence[int]) -> Any:
        seen.append((list(ticks), self.deadline))
        return read(self, ticks)

    monkeypatch.setattr(rec._Work, "read", spy)
    store = PieceStore(tmp_path / "recordings")
    reading(tmp_path, teams(share(3, FIRST), share(9, SECOND), share(15, THIRD)), pieces=store)
    candidates = len(gated_of(store))
    assert seen[0] == ([0], 1_000.0 + 60 + 0.03 * 31), "tick 0, for the profile, under the scan's limit"
    limit = 1_000.0 + 60 + 0.03 * 31 + 0.8 * candidates
    assert [deadline for _ticks, deadline in seen[1:]] == [limit] * (len(seen) - 1)


def test_three_reads_share_one_deadline_and_merge_by_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "_clock", lambda: 1_000.0)
    store = PieceStore(tmp_path / "recordings")
    conv = converter(tmp_path, pieces=store)
    calls: list[tuple[list[str], Path, float]] = []
    read = conv._ocr.read

    def spy(images: Sequence[Path], *, work_dir: Path, budget_s: float, frames: int = 1) -> Any:
        calls.append(([p.name for p in images], work_dir, budget_s))
        return read(images, work_dir=work_dir, budget_s=budget_s, frames=frames)

    monkeypatch.setattr(conv._ocr, "read", spy)
    small = [
        screen(
            k,
            *(
                row(f"{word} step {k} of the build", 0.05, 0.2 + 0.05 * i, 0.3, 0.02)
                for i, word in enumerate(["Compile", "Test", "Package"])
            ),
        )
        for k in range(7)
    ]
    got = conv._reading(staged(tmp_path, recording(*small)), name="meeting.mp4")
    rounds = calls[1:]
    assert calls[0][0] == ["t000000.jpg"], "tick 0 first, alone: it decides the profile"
    assert [names for names, _dir, _budget in rounds] == [
        ["t000002.jpg", "t000008.jpg"],
        ["t000004.jpg", "t000010.jpg"],
        ["t000006.jpg", "t000012.jpg"],
    ], "one round of six candidates in three runs, split by index modulo 3"
    assert len({d for _n, d, _b in rounds}) == 3 and len({b for _n, _d, b in rounds}) == 1
    assert [s.kind for s in got.states] == ["share"] and got.unprinted_rows == 18
    seen = {r["tick"]: [line[0] for line in r["lines"]] for doc in stored(store) for r in doc["reads"]}
    assert seen == {
        k: [f"{w} step {k} of the build" for w in ("Compile", "Test", "Package")] for k in range(7)
    }


@pytest.mark.parametrize("ending", ["page", "stub", "helper failure", "time-out"])
def test_nothing_is_left_beside_the_staged_file_when_convert_returns_or_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    script = teams(share(3, FIRST)) if ending != "stub" else recording(picture=None, audio=False)
    src = staged(tmp_path, script)
    conv = converter(
        tmp_path, fail={"scan": "the picture cannot be decoded"} if ending == "helper failure" else None
    )
    if ending == "time-out":
        clock = itertools.count(0.0, 1_000.0)
        monkeypatch.setattr(rec, "_clock", lambda: next(clock))
    try:
        conv._reading(src, name="meeting.mp4")
    except (UnreadableSourceError, MediaError, rec.RecordingNotFinished) as exc:
        assert ending != "page", exc
    else:
        assert ending == "page"
    assert sorted(p.name for p in src.parent.iterdir()) == ["meeting.mp4"]


@pytest.mark.parametrize("ending", ["page", "speech failure", "time-out", "allowance stop"])
def test_nothing_is_left_beside_the_staged_file_when_speech_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    """The sound is decoded into the recording's scratch folder (S2), so it goes with it however the read
    ends: a page, a SpeechError, a time-out, or an allowance used up before the speech piece."""
    src = staged(tmp_path, teams(share(3, FIRST), sound=[(0, 20_000)]))
    engine = hearing(
        tmp_path,
        talk(("spk-a", 3_000, "Contoso budget review")),
        fail={"voices": "voice separation failed"} if ending == "speech failure" else None,
    )
    conv = converter(tmp_path, speech=engine, pieces=PieceStore(tmp_path / "pieces"))
    if ending == "time-out":
        clock = itertools.count(0.0, 1_000.0)
        monkeypatch.setattr(rec, "_clock", lambda: next(clock))
    allowance = rec.work_allowance(1e-9) if ending == "allowance stop" else contextlib.nullcontext()
    try:
        with allowance:
            conv._reading(src, name="meeting.mp4")
    except (SpeechError, rec.RecordingNotFinished) as exc:
        assert ending != "page", exc
    else:
        assert ending == "page"
    assert sorted(p.name for p in src.parent.iterdir()) == ["meeting.mp4"]


def test_a_frame_vision_gave_up_on_gives_no_page(tmp_path: Path) -> None:
    gave_up = json.dumps(
        {
            "results": [
                {
                    "index": 0,
                    "frame": 0,
                    "frames": 1,
                    "width": 1920,
                    "height": 1080,
                    "lines": [],
                    "error": "recognition failed",
                    "skipped": False,
                }
            ]
        }
    )
    conv = converter(tmp_path / "a")
    conv._ocr = fake_engine(tmp_path / "a" / "gave-up", reply=gave_up)
    src = staged(tmp_path / "a", teams(share(3, FIRST)))
    with pytest.raises(MediaError, match="could not be read by on-device OCR"):
        conv._reading(src, name="meeting.mp4")
    cache = ConverterCache(tmp_path / "cache")
    got = convert_file(
        src,
        name="meeting.mp4",
        content_sha256="2" * 64,
        canonical_sha256="2" * 64,
        registry=_registry(tmp_path / "b", reply=gave_up),
        cache=cache,
    )
    assert (got.status, got.reason, got.from_cache) == (
        ConversionStatus.REFUSED,
        "no converter for .mp4",
        False,
    )
    assert not any((tmp_path / "cache").rglob("*.json"))


def test_a_strip_name_level_with_a_content_line_is_not_one_row(tmp_path: Path) -> None:
    level = row("Morgan Lee", 0.89, 0.25, 0.08, 0.04)
    script = teams(
        *(share(k, FIRST, level, paint=[(0.70, 0.90, 0.80, 0.95, 40 + 9 * k)]) for k in (3, 10, 17))
    )
    got = reading(tmp_path, script)
    assert "North region grows by a tenth" in texts(got, "SCREEN")
    assert not any("Morgan Lee" in t for t in texts(got)), "a strip row is never printed in a window"
    assert any(text == "Morgan Lee" and reads >= 3 for _first, text, reads in got.names)


def test_a_gallery_of_name_labels_is_other_and_stores_no_image(tmp_path: Path) -> None:
    """Ruling 3 replaced "no image": a gallery keeps a full frame.  Its names are ``TILE`` lines only."""
    names = ["Avery Chen", "Blake Ortiz", "Casey Diaz", "Drew Evans", "Emery Fox", "Finley Gray"]
    gallery = [row(n, 0.05 + 0.27 * (i % 3), 0.30 + 0.30 * (i // 3), 0.20, 0.04) for i, n in enumerate(names)]
    got = reading(tmp_path, teams(screen(3, *gallery, paint=[STRIP])))
    state = got.states[-1]
    assert state.kind == "other" and picture_size(state.keyframes[0].data) == [1920, 1080]
    inside = [r for r in got.rows if r.start_ms >= state.start_ms]
    assert [r.text for r in inside] == [" | ".join(names[:3]), " | ".join(names[3:])], "cells of one row"
    assert {r.tag for r in inside} == {"TILE"}


def test_a_row_read_at_a_stored_keyframe_is_always_printed(tmp_path: Path) -> None:
    loading = row("Loading preview", 0.60, 0.85, 0.20, 0.03)
    got = reading(tmp_path, teams(share(3, FIRST), share(15, SECOND, loading), share(16, SECOND)))
    state = next(s for s in got.states if s.start_ms == 30_000)
    assert [k.ms for k in state.keyframes] == [30_000]
    row_ = next(r for r in got.rows if r.text == "Loading preview")
    assert (row_.start_ms, row_.end_ms) == (30_000, 32_000), "on screen 2 s, read at K1: printed"


def test_a_recording_without_a_screen_share_is_a_page_with_full_frames(tmp_path: Path) -> None:
    name = row("Blake Ortiz", 0.05, 0.85, 0.20, 0.04)
    got = reading(tmp_path, teams(*camera(1, 31, name)))
    assert {s.kind for s in got.states} == {"camera"} and "Blake Ortiz" in texts(got, "TILE")
    assert all(picture_size(k.data) == [1920, 1080] for s in got.states for k in s.keyframes)


def test_a_video_in_a_share_is_read_every_ten_seconds_and_noted(tmp_path: Path) -> None:
    store = PieceStore(tmp_path / "recordings")
    video = [share(k, FIRST, paint=[(0.75, 0.50, 0.85, 0.70, 30 + (k * 41) % 180)]) for k in range(3, 30)]
    got = reading(tmp_path, teams(*video), pieces=store)
    assert reads_of(store)[:8] == [0, 3, 4, 5, 6, 7, 8, 13]
    later = reads_of(store)[6:]
    assert all(b - a >= 5 for a, b in itertools.pairwise(later))
    assert [(n.ms, n.text) for s in got.states for n in s.notes if n.text == rec._NOTE_MOTION] == [
        (16_000, rec._NOTE_MOTION)
    ]


def test_the_index_title_is_empty_and_its_source_title_is_the_items_name(tmp_path: Path) -> None:
    units = pages(tmp_path, teams(share(3, FIRST)))
    assert units[0].title == "" and units[0].unit_id == "index"
    assert all(u.title.startswith("Recording ") for u in units[1:])


def test_a_new_emitter_alone_reads_no_recording_again(tmp_path: Path) -> None:
    conv = converter(tmp_path)
    for produced in (
        "1.0.0+ocr-a+media-b-s1+cue-r1",
        "1.0.1+ocr-a+media-b-s2+cue-r1",
        "2.0.0+x",
        "unavailable",
    ):
        assert not conv.outdated(produced) and not conv.outdated(produced, "no text read on screen")
    assert conv.outdated_key == "1.1.0<1.0.0|cue-r1|-"
    assert conv.version() == "1.1.0+ocr-paper-vision-r2-h0.3.0-l1+media-paper-media-h0.1.0-s2+cue-r1"


def test_a_floor_above_a_pages_emitter_reads_it_again_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = converter(tmp_path)
    monkeypatch.setattr(rec, "_EMITTER_VERSION", "1.2.0")
    monkeypatch.setattr(rec, "_REREAD_BELOW", "1.1.0")
    assert conv.outdated("1.0.0+ocr-a+media-b-s1") and conv.outdated("1.0.9+x", "no text read on screen")
    assert not conv.outdated("1.1.0+x+cue-r1") and not conv.outdated("1.2.0+x+cue-r1"), (
        "what a re-read wrote is current"
    )
    assert conv.outdated_key == "1.2.0<1.1.0|cue-r1|-"


# ---------------------------------------------------------------------------------------------------------
# pieces
# ---------------------------------------------------------------------------------------------------------


def _long_meeting() -> dict[str, Any]:
    """Ten minutes, so three pieces (ticks 0-149, 150-299, 300): states and a camera stretch over the
    joins."""
    return teams(
        share(3, FIRST),
        share(148, SECOND),
        share(151, THIRD),
        *camera(200, 262, row("Blake Ortiz", 0.05, 0.85, 0.20, 0.04)),
        share(262, FIRST),
        *[share(k, SECOND, paint=[(0.75, 0.50, 0.85, 0.70, 30 + (k * 41) % 180)]) for k in range(280, 301)],
        duration_ms=600_000,
    )


def test_a_recording_read_over_three_calls_under_an_allowance_equals_one_call(tmp_path: Path) -> None:
    whole = reading(tmp_path / "whole", _long_meeting())
    store = PieceStore(tmp_path / "recordings")
    src = staged(tmp_path / "parts", _long_meeting())
    conv = converter(tmp_path / "parts", pieces=store)
    for done in (150, 300):
        with rec.work_allowance(1e-9), pytest.raises(rec.RecordingNotFinished) as caught:
            conv._reading(src, name="meeting.mp4")
        assert (caught.value.done_ms, caught.value.total_ms, caught.value.timed_out) == (
            done * 2_000,
            600_000,
            False,
        )
    with rec.work_allowance(1e-9) as allowance:
        parts = conv._reading(src, name="meeting.mp4")
    assert allowance.spent_s > 0 and parts == whole
    assert store.progress(sha256_file(src)) == (600_000, 600_000) and len(stored(store)) == 3
    with rec.work_allowance(0.0):
        assert conv._reading(src, name="meeting.mp4") == whole, "every piece stored: nothing to read"


def test_outside_an_allowance_or_without_a_store_a_recording_is_read_to_the_end(tmp_path: Path) -> None:
    src = staged(tmp_path, _long_meeting())
    with rec.work_allowance(0.0):
        got = converter(tmp_path)._reading(src, name="meeting.mp4")
    assert got.read_ms == 600_000 and len(got.states) > 3
    with rec.work_allowance(None) as allowance:
        assert not allowance.used_up


def test_a_row_printed_the_same_way_carries_across_a_change_of_kind() -> None:
    """A moving desktop under ``generic`` flips between share and camera; a content row prints SCREEN in
    both, so it stays one row and a short state folded into the next does not remove and re-add it at one
    tick.  Under ``teams`` it is SCREEN in a share and TILE in a camera state, so it starts again."""
    seen = rec._Seen("content", "Contoso storage budget, FY27", 1.0, 0.1, 0.2, 0.5, 0.03)
    grid = bytes(rec.GRID_W * rec.GRID_H)
    cands = [
        rec._Cand(0, "share", [seen], grid, False),
        rec._Cand(1, "camera", [seen], grid, False),
        rec._Cand(2, "share", [seen], grid, False),
    ]
    assert len(rec._tracks(cands, rec._PROFILES["generic"])) == 1
    assert len(rec._tracks(cands, rec._PROFILES["teams"])) == 3


def test_a_moving_desktop_full_of_text_is_read_on_while_new_rows_appear(tmp_path: Path) -> None:
    """Under ``generic`` a scrolled desktop changes more than 5 % of the frame and reads as camera (R4); the
    camera back-off would read it every 10 s and lose a traceback shown for 4 s between two reads.  A camera
    read with 2 or more long rows goes under the motion back-off instead, which reads on while rows change."""
    store = PieceStore(tmp_path / "recordings")
    frames = []
    for k in range(1, 20):
        lines = [
            row(f"cell {k}: forecast = load_forecast('Contoso west', quarter={k})", 0.05, 0.20, 0.70, 0.03),
            row(f"cell {k}: budget = forecast.total() * {k + 100}", 0.05, 0.30, 0.70, 0.03),
        ]
        if k in (7, 8):
            lines.append(row("NameError: name 'quarterly_total' is not defined", 0.05, 0.50, 0.70, 0.03))
        frames.append(screen(k, *lines, paint=[(0.0, 0.0, 1.0, 1.0, 20 + (k * 53) % 200)]))
    got = reading(tmp_path, recording(screen(0), *frames, duration_ms=40_000), pieces=store)
    assert "NameError: name 'quarterly_total' is not defined" in texts(got)
    assert {7, 8} <= set(reads_of(store))


def test_a_row_that_scrolled_is_the_same_row_when_its_text_is_unique() -> None:
    """S6 rule 4 as built: an equal text held once in its region at both reads continues the row wherever it
    moved, so a scrolled traceback is one row of 4 s, not two rows of 2 s the 4 s rule drops.  A text held
    twice, or a merely similar text, still needs the same place."""
    grid = bytes(rec.GRID_W * rec.GRID_H)
    error = "Traceback (most recent call last)"
    before = [rec._Seen("content", error, 1.0, 0.05, 0.60, 0.40, 0.03)]
    scrolled = [rec._Seen("content", error, 1.0, 0.05, 0.30, 0.40, 0.03)]
    assert rec._matches(before, scrolled, (grid, grid)) == [0]
    twice = [rec._Seen("content", "import json", 1.0, 0.05, y, 0.20, 0.03) for y in (0.60, 0.70)]
    moved = [rec._Seen("content", "import json", 1.0, 0.05, y, 0.20, 0.03) for y in (0.20, 0.30)]
    assert rec._matches(twice, moved, (grid, grid)) == [None, None]
    near = [rec._Seen("content", "Traceback (most recent call first)", 1.0, 0.05, 0.30, 0.40, 0.03)]
    assert rec._matches([*before], near, (grid, grid)) == [None]


def test_the_allowance_is_charged_for_the_info_call_and_the_settle_as_well_as_the_pieces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec S0 rule 5: the allowance is recording work of every kind, not only the pieces: the helper's
    ``info`` call and S6's settle over every piece are charged too."""
    now = [1_000.0]
    monkeypatch.setattr(rec, "_clock", lambda: now[0])
    conv = converter(tmp_path, pieces=PieceStore(tmp_path / "recordings"))
    real_info, real_settle = conv._media.info, rec._Settle.run

    def info(src: Path, *, timeout: float) -> Any:
        now[0] += 7.0
        return real_info(src, timeout=timeout)

    def settle(self: rec._Settle, info_size: tuple[int, int]) -> Any:
        now[0] += 50.0
        return real_settle(self, info_size)

    monkeypatch.setattr(conv._media, "info", info)
    monkeypatch.setattr(rec._Settle, "run", settle)
    with rec.work_allowance(1_000.0) as allowance:
        conv._reading(staged(tmp_path, _long_meeting()), name="meeting.mp4")
    assert allowance.spent_s == 57.0, "the pieces took no time on this clock"


def test_a_settle_that_times_out_says_the_last_pieces_media_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec S0 rule 6: a recording whose settle passed its deadline waits with every piece stored, but it is
    not read: the signal stops at the start of the last piece of media time, never "N of N minutes"."""

    def timed_out(self: rec._Settle, info_size: tuple[int, int]) -> Any:
        raise rec._TimedOut

    monkeypatch.setattr(rec._Settle, "run", timed_out)
    store = PieceStore(tmp_path / "recordings")
    with pytest.raises(rec.RecordingNotFinished) as caught:
        converter(tmp_path, pieces=store)._reading(staged(tmp_path, _long_meeting()), name="meeting.mp4")
    assert (caught.value.done_ms, caught.value.total_ms, caught.value.timed_out) == (300_000, 600_000, True)
    assert len(stored(store)) == 3, "the pieces are kept"


# ---------------------------------------------------------------------------------------------------------
# P3: the speaker cue (S7), speech and voices (S8), voice naming (S8b)
# ---------------------------------------------------------------------------------------------------------


def _grammar(index: RenderedUnit, *windows: RenderedUnit) -> None:
    assert index_errors(index.body) == []
    for unit in windows:
        assert window_errors(unit.body) == [], unit.unit_id


def _lines(unit: RenderedUnit) -> list[str]:
    return [line for line in unit.body.split("\n") if line.startswith("[")]


def test_words_become_said_lines_in_their_window_and_voices_are_numbered_by_first_word(
    tmp_path: Path,
) -> None:
    sound = talk(
        ("spk-b", 2_000, "Good morning from Contoso."),
        ("spk-a", 6_000, "Thanks, the forecast is ready."),
        ("spk-b", 310_000, "Last item for today."),
    )
    script = teams(share(3, FIRST), duration_ms=400_000)
    index, first, second = pages(tmp_path, script, speech=hearing(tmp_path, sound))
    assert "[00:00:02] SAID v1: Good morning from Contoso." in _lines(first)
    assert "[00:00:06] SAID v2: Thanks, the forecast is ready." in _lines(first)
    assert "[00:05:10] SAID v1: Last item for today." in _lines(second)
    assert "[00:00:02] VOICE: v1 · unidentified" in index.body
    assert "[00:00:06] VOICE: v2 · unidentified" in index.body
    _grammar(index, first, second)


def test_a_hole_is_read_again_as_a_clip_and_one_left_empty_says_so(tmp_path: Path) -> None:
    sound = talk(
        ("spk-a", 1_000, "Opening words here."), ("spk-a", 20_000, "Back again."), ("spk-b", 50_000, "Done.")
    )
    sound["segments"] += [["spk-b", 3_000, 15_000], ["spk-b", 30_000, 45_000]]
    sound["hidden"] = [["recovered", 8_000, 8_400], ["words", 8_400, 8_800]]
    engine = hearing(tmp_path, sound)
    got = reading(tmp_path, teams(share(3, FIRST)), speech=engine)
    assert got.speech is not None
    said = got.speech.reading
    assert [ln.text for ln in said.lines] == [
        "Opening words here.",
        "recovered words",
        "Back again.",
        "Done.",
    ]
    assert said.unrecognised == ((20_800, 50_000),)
    clips = [c["args"] for c in speech_calls(engine.helper) if "--from" in c["args"]]
    assert [a[a.index("--from") + 1 : a.index("--from") + 4 : 2] for a in clips] == [
        ["0", "25000"],
        ["15800", "55000"],
    ]
    index, window = pages(tmp_path / "pages", teams(share(3, FIRST)), speech=engine)
    assert "[00:00:20] NOTE: speech detected, no words recognised until 00:00:50" in _lines(window)
    _grammar(index, window)


def test_silence_says_where_sound_ends(tmp_path: Path) -> None:
    script = teams(share(3, FIRST), sound=[(0, 20_000)])
    engine = hearing(tmp_path, talk(("spk-a", 2_000, "Contoso review starts.")))
    index, window = pages(tmp_path, script, speech=engine)
    assert "[00:00:20] NOTE: no sound from here to the end of the recording" in _lines(window)
    assert "- sound ends at 00:00:20" in index.body
    _grammar(index, window)


def test_the_cue_writes_speaking_only_where_one_label_is_lit(tmp_path: Path) -> None:
    script = teams(
        share(3, FIRST),
        share(10, FIRST, lit=["Avery Chen"]),
        share(14, FIRST, lit=["Avery Chen", "Blake Ortiz"]),
        share(18, FIRST, lit=["Blake Ortiz"]),
        share(22, FIRST),
    )
    got = reading(tmp_path, script)
    assert got.speaking == ((20_000, "Avery Chen"), (36_000, "Blake Ortiz")) and got.cue_identity == "cue-r1"
    assert got.speech is None and got.no_speech is None, "no speech engine: speech is not mentioned"
    index, window = pages(tmp_path / "pages", script)
    assert [ln for ln in _lines(window) if "SPEAKING" in ln] == [
        "[00:00:20] SPEAKING: Avery Chen",
        "[00:00:36] SPEAKING: Blake Ortiz",
    ]
    _grammar(index, window)
    meet = reading(tmp_path / "generic", recording(screen(3, *FIRST, lit=["Status: approved by finance"])))
    assert (meet.profile, meet.speaking, meet.cue_identity) == ("generic", None, None)


def test_naming_names_nobody_under_the_gate_and_prints_shared_audio(tmp_path: Path) -> None:
    """``Blake Ortiz`` carries one voice (v1 takes it, but no layout is checked yet: unidentified and the
    gate's NOTE); ``Avery Chen`` carries two (shared audio, no name)."""
    script = teams(
        share(3, FIRST, lit=["Blake Ortiz"]), share(30, FIRST, lit=["Avery Chen"]), duration_ms=120_000
    )
    sound = talk(
        ("spk-a", 1_000, "Contoso first."), ("spk-b", 61_000, "Second voice."), ("spk-c", 91_000, "Third.")
    )
    sound["segments"] = [["spk-a", 0, 60_000], ["spk-b", 60_000, 90_000], ["spk-c", 90_000, 120_000]]
    engine = hearing(tmp_path, sound)
    got = reading(tmp_path, script, speech=engine)
    assert got.speech is not None
    assert [(n.number, n.form, n.label, n.gated) for n in got.speech.namings] == [
        (1, "unidentified", "Blake Ortiz", True),
        (2, "shared", "Avery Chen", False),
        (3, "shared", "Avery Chen", False),
    ]
    index, window = pages(tmp_path / "pages", script, speech=engine)
    assert "[00:00:01] VOICE: v1 · unidentified" in index.body
    assert "[00:01:01] VOICE: v2 · shared audio of Avery Chen, 2 voices" in index.body
    assert "[00:00:00] NOTE: names held back: this layout's names are not yet checked against a listen" in (
        index.body
    )
    assert "Blake Ortiz ·" not in index.body
    _grammar(index, window)


def test_a_line_whose_lit_samples_show_another_label_is_not_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(naming_mod, "CHECKED_PROFILES", frozenset({"teams"}))
    script = teams(
        share(3, FIRST, lit=["Blake Ortiz"]), share(50, FIRST, lit=["Avery Chen"]), duration_ms=240_000
    )
    sound = talk(
        ("spk-a", 1_000, "Contoso budget is on track."),
        ("spk-b", 101_000, "Racks arrive in November."),
        ("spk-a", 180_000, "One more point."),
    )
    sound["segments"] = [["spk-a", 0, 100_000], ["spk-b", 100_000, 180_000], ["spk-a", 180_000, 184_000]]
    engine = hearing(tmp_path, sound)
    got = reading(tmp_path, script, speech=engine)
    assert got.speech is not None
    assert [(n.number, n.form, n.label) for n in got.speech.namings] == [
        (1, "named", "Blake Ortiz"),
        (2, "named", "Avery Chen"),
    ]
    assert got.speech.vetoed == ((1, 180_000),)
    index, window = pages(tmp_path / "pages", script, speech=engine)
    assert "[00:00:01] VOICE: v1 · Blake Ortiz · seen: 47 of 49 lit samples" in index.body
    assert _lines(window)[-2:] == [
        "[00:03:00] SAID v1: One more point.",
        "[00:03:00] NOTE: v1 is not named on this line: its lit samples show another label",
    ]
    _grammar(index, window)


def test_a_recording_without_picture_but_with_sound_is_a_speech_only_page(tmp_path: Path) -> None:
    script = recording(picture=None, size=(0, 0), sound=[(0, 60_000)])
    engine = hearing(tmp_path, talk(("spk-a", 4_000, "Contoso audio only call.")))
    got = reading(tmp_path, script, speech=engine)
    assert got.picture_unread == "recording has no picture; its speech is not read by this version"
    assert got.states == () and got.speech is not None and got.no_speech is None
    index, window = pages(tmp_path / "pages", script, speech=engine)
    assert "[00:00:00] NOTE: picture not read: the recording has no picture track" in index.body
    assert _lines(window) == ["[00:00:04] SAID v1: Contoso audio only call."]
    _grammar(index, window)
    with pytest.raises(
        UnreadableSourceError, match=r"^no text read on screen and no speech in the recording$"
    ):
        reading(tmp_path / "silent", script, speech=hearing(tmp_path / "silent"))


def test_text_or_speech_makes_a_page_and_neither_is_the_stub_of_a_speech_engine(tmp_path: Path) -> None:
    blank = recording(screen(0), screen(5, paint=[(0.1, 0.1, 0.9, 0.9, 90)]))
    with pytest.raises(
        UnreadableSourceError, match=r"^no text read on screen; speech is not read by this version$"
    ):
        reading(tmp_path / "plain", blank)
    with pytest.raises(
        UnreadableSourceError, match=r"^no text read on screen and no speech in the recording$"
    ):
        reading(tmp_path / "quiet", blank, speech=hearing(tmp_path / "quiet"))
    with pytest.raises(
        UnreadableSourceError, match=r"^no text read on screen and no speech in the recording$"
    ):
        reading(tmp_path / "mute", {**blank, "audio": False}, speech=hearing(tmp_path / "mute"))
    engine = hearing(tmp_path / "talk", talk(("spk-a", 3_000, "Contoso call with no slides.")))
    got = reading(tmp_path / "talk", blank, speech=engine)
    assert got.rows == () and got.states != () and got.speech is not None
    index, window = pages(tmp_path / "pages", blank, speech=engine)
    assert "[00:00:03] SAID v1: Contoso call with no slides." in _lines(window)
    _grammar(index, window)
    mute = reading(tmp_path / "text", {**teams(share(3, FIRST)), "audio": False}, speech=engine)
    assert mute.speech is None and mute.no_speech == "the recording has no sound"
    index, *windows = pages(
        tmp_path / "text-pages", {**teams(share(3, FIRST)), "audio": False}, speech=engine
    )
    assert "- speech not read: the recording has no sound" in index.body
    _grammar(index, *windows)


def test_version_options_and_outdated_key_carry_the_cue_speech_and_naming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = converter(tmp_path)
    heard = converter(tmp_path, speech=hearing(tmp_path))
    picture = "1.1.0+ocr-paper-vision-r2-h0.3.0-l1+media-paper-media-h0.1.0-s2"
    asr = f"asr-parakeet-0123456789ab-f{FAKE_PIN[:7]}-h0.1.0-d1"
    assert plain.version() == f"{picture}+cue-r1"
    assert heard.version() == f"{picture}+cue-r1+{asr}-n1"
    assert heard.outdated_key == f"1.1.0<1.0.0|cue-r1|{asr}"
    assert plain._key() == heard._key(), "a picture piece is the same with or without speech"
    mine, theirs = dict(heard.options()), dict(plain.options())
    assert {"cue_lit_at", "cue_teams"} <= theirs.keys() <= mine.keys()
    assert {
        "speech_hole_gap_ms",
        "speech_diarizer_threshold",
        "naming_p_min",
        "naming_checked_profiles",
        "recording_speech_limit",
    } <= (mine.keys() - theirs.keys())
    monkeypatch.setattr(rec, "_SPEECH_S", 0.08)
    assert dict(heard.options()) != mine


def test_what_a_speech_engine_reads_again(tmp_path: Path) -> None:
    plain = converter(tmp_path)
    heard = converter(tmp_path, speech=hearing(tmp_path))
    picture = "1.0.0+ocr-a+media-b-s2"
    no_text = "no text read on screen; speech is not read by this version"
    sound_only = "recording has no picture; its speech is not read by this version"
    for conv in (plain, heard):
        assert conv.outdated(picture), "made before the cue: read again once"
        assert not conv.outdated(conv.version()) and not conv.outdated(conv.version(), no_text)
    assert not plain.outdated(f"{picture}+cue-r1") and not plain.outdated(f"{picture}+cue-r1", no_text)
    assert heard.outdated(f"{picture}+cue-r1"), "made without speech"
    assert heard.outdated(f"{picture}+cue-r1+asr-parakeet-x-f1-d1"), "made before naming"
    assert not heard.outdated(f"{picture}+cue-r1+asr-parakeet-x-f1-d1-n1"), (
        "another model reads nothing again"
    )
    for reason in (no_text, sound_only, "recording's picture cannot be decoded on this Mac (VP9 or AV1)"):
        assert heard.outdated(f"{picture}+cue-r1+asr-parakeet-x-f1-d1-n1", reason), reason
    assert not heard.outdated(
        heard.version(), "recording's picture cannot be decoded on this Mac (VP9 or AV1)"
    )
    assert not heard.outdated(f"{picture}+cue-r1+asr-x-n1", "recording has no picture and no sound")


def test_a_speech_failure_keeps_the_screens_and_is_read_again_once_speech_works(tmp_path: Path) -> None:
    store = PieceStore(tmp_path / "recordings")
    helper = fake_media(tmp_path / "helpers")
    media = MediaEngine(helper, name="paper-media", helper_version="0.1.0")
    failing = hearing(tmp_path, talk(("spk-a", 2_000, "Contoso.")), fail={"voices": "the diarizer failed"})
    registry = Registry.default(
        CFG, ocr=fake_engine(tmp_path / "helpers"), media=media, pieces=store, speech=failing
    )
    assert registry.without_speech is not None and Registry.default(CFG).without_speech is None
    src = staged(tmp_path, teams(share(3, FIRST)))
    got = convert_file(
        src,
        name="meeting.mp4",
        content_sha256="2" * 64,
        canonical_sha256="2" * 64,
        registry=registry,
        cache=ConverterCache(tmp_path / "cache"),
    )
    assert got.status is ConversionStatus.OK and "+cue-r1" in got.converter_version
    assert "+asr-" not in got.converter_version and not any("] SAID v" in u.body for u in got.units)
    assert any("Status: approved by finance" in u.body for u in got.units), "the screens stand"
    conv = registry.for_name("meeting.mp4")
    assert conv is not None and conv.inner.outdated(got.converter_version)  # type: ignore[attr-defined]
    scans = [c for c in media_calls(helper) if c["args"][0] == "scan"]
    assert len(scans) == 1, "the picture pieces the failed read stored are read back, not read again"


def test_a_speech_piece_past_its_deadline_waits_and_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "_SPEECH_S", -1.0)
    store = PieceStore(tmp_path / "recordings")
    conv = converter(tmp_path, pieces=store, speech=hearing(tmp_path, talk(("spk-a", 2_000, "Contoso."))))
    with pytest.raises(rec.RecordingNotFinished) as caught:
        conv._reading(staged(tmp_path, teams(share(3, FIRST), duration_ms=400_000)), name="meeting.mp4")
    assert (caught.value.done_ms, caught.value.total_ms, caught.value.timed_out) == (300_000, 400_000, True)
    assert len(stored(store)) == 2, "the picture pieces are kept, the speech piece is not stored"


def fixed_pcm(path: Path, seconds: int) -> Path:
    """A fixed 16 kHz mono s16le sound made by code: seeded noise, a run of exact zeros every 7th second (as
    Teams writes them), and 30 s of silence at the end."""
    rng = random.Random(20261008)
    chunks = [
        bytes(32_000) if second % 7 == 6 or second >= seconds - 30 else rng.randbytes(32_000)
        for second in range(seconds)
    ]
    path.write_bytes(b"".join(chunks))
    return path


def test_speech_is_deterministic_on_a_fixed_wav(tmp_path: Path) -> None:
    """The goal test (spec S8 Determinism): one fixed sound and one fixed engine script give byte-identical
    units and sidecars in two cold conversions, and the same bytes when the recording is worked over several
    cycles of pieces; the speech piece is stored and heard once."""
    pcm = fixed_pcm(tmp_path / "fixed.pcm", 400)
    script = teams(
        share(3, FIRST, lit=["Avery Chen"]),
        share(160, SECOND, lit=["Blake Ortiz"]),
        duration_ms=400_000,
        pcm=pcm,
    )
    sound = talk(
        ("spk-a", 2_000, "Good morning, this is the Contoso review."),
        ("spk-b", 9_000, "Thanks. The forecast is ready."),
        ("spk-a", 200_000, "Racks arrive in November."),
        ("spk-b", 330_000, "That closes the review."),
    )
    sound["segments"].append(["spk-b", 30_000, 40_000])
    sound["hidden"] = [["Contoso", 31_000, 31_400]]

    def cold(tree: Path) -> tuple[RenderedUnit, ...]:
        return converter(tree, speech=hearing(tree, sound)).convert(staged(tree, script), name="meeting.mp4")

    one = cold(tmp_path / "one")
    assert one == cold(tmp_path / "two")
    assert any("SAID v1: Good morning" in u.body for u in one) and any(u.sidecars for u in one)
    assert any("SAID v2: Contoso" in u.body for u in one), "the hole was read again"
    assert any("NOTE: no sound from here to the end of the recording" in u.body for u in one)
    tree = tmp_path / "three"
    engine = hearing(tree, sound)
    conv = converter(tree, pieces=PieceStore(tree / "recordings"), speech=engine)
    src = staged(tree, script)
    cycles = 0
    while True:
        cycles += 1
        with rec.work_allowance(1e-9):
            try:
                parts = conv.convert(src, name="meeting.mp4")
                break
            except rec.RecordingNotFinished:
                continue
    assert cycles == 3 and parts == one
    heard = len(speech_calls(engine.helper))
    with rec.work_allowance(0.0):
        assert conv.convert(src, name="meeting.mp4") == one
    assert len(speech_calls(engine.helper)) == heard, "the speech piece is stored: nothing is heard again"
