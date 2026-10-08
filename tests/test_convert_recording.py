"""convert/recording.py: a meeting recording read through the fake media helper and the fake OCR helper (spec
9.3).

The fake media helper of ``tests/media_kit.py`` "shows" a script of screens; its ``frames`` writes
``FAKE-OCR:`` JPEGs that the fake OCR helper of ``tests/test_ocr.py`` reads, so every row a test puts on
screen is what OCR reads back.  Names are made up (Contoso).
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert import convert_file
from agentsync.convert import recording as rec
from agentsync.convert.cache import ConverterCache
from agentsync.convert.media import Frame, MediaEngine, MediaError, MediaInfo, Rect, Scan
from agentsync.convert.ocr import OcrLine
from agentsync.convert.pieces import PieceStore
from agentsync.convert.registry import Registry
from agentsync.errors import UnreadableSourceError
from agentsync.materialise import sha256_file
from agentsync.model import ConversionStatus, RenderedUnit
from agentsync.policy import PolicyConfig
from media_kit import calls as media_calls
from media_kit import fake_media, fake_recording, recording, row, screen
from test_convert_core import LABEL_RULES
from test_ocr import fake_engine
from test_recording_grammar import index_errors, window_errors

CFG = ConvertConfig()


class KitMedia(MediaEngine):
    """Runs the fake media helper until ``convert/media.py`` runs a helper itself (teammate "media"); the
    protocol is the one ``tests/media_kit.py`` pins.  ``first_tick`` is cut from a scan from tick 0."""

    def _run(self, args: Sequence[str], timeout: float) -> Any:
        done = subprocess.run([str(self.helper), *args], capture_output=True, timeout=timeout, check=False)
        if done.returncode:
            raise MediaError("the media helper failed")
        return json.loads(done.stdout)

    def alive(self) -> bool:
        return True

    def info(self, src: Path, *, timeout: float) -> MediaInfo:
        doc = self._run(["info", str(src)], timeout)
        return MediaInfo(
            doc["duration_ms"], doc["width"], doc["height"], doc["picture"], doc["audio"], doc["created"]
        )

    def scan(
        self,
        src: Path,
        *,
        out: Path,
        timeout: float,
        step_ms: int = 2000,
        first_tick: int = 0,
        max_ticks: int | None = None,
    ) -> Scan:
        args = ["scan", str(src), "--out", str(out), "--step-ms", str(step_ms)]
        if max_ticks is not None:
            args += ["--max-ticks", str(first_tick + max_ticks)]
        doc = self._run(args, timeout)
        grids = out / "grids.bin"
        size = 320 * 180
        grids.write_bytes(grids.read_bytes()[first_tick * size :])
        return Scan(grids, step_ms, first_tick, len(doc["ticks"]) - first_tick)

    def frames(
        self,
        src: Path,
        *,
        out: Path,
        ticks: Sequence[int],
        timeout: float,
        crop: Rect | None = None,
        crop_right: float | None = None,
        step_ms: int = 2000,
    ) -> list[Frame]:
        args = ["frames", str(src), "--out", str(out), "--ticks", ",".join(str(t) for t in ticks)]
        if crop is not None:
            args += ["--crop", ",".join(str(v) for v in crop)]
        if crop_right is not None:
            args += ["--crop-right", str(crop_right)]
        doc = self._run(args, timeout)
        return [Frame(f["tick"], f["ms"], out / f["file"], f["width"], f["height"]) for f in doc["frames"]]

    def diff(
        self,
        grids: Path,
        *,
        pairs: Sequence[tuple[int, int]],
        timeout: float,
        include: Sequence[Rect] = (),
        exclude: Sequence[Rect] = (),
        threshold: int = 12,
    ) -> list[int]:
        args = ["diff", str(grids), "--pairs", ",".join(f"{a}:{b}" for a, b in pairs)]
        for flag, rects in (("--include", include), ("--exclude", exclude)):
            for r in rects:
                args += [flag, ",".join(str(v) for v in r)]
        args += ["--threshold", str(threshold)]
        return [p["changed"] for p in self._run(args, timeout)["pairs"]]


def converter(
    tmp_path: Path, *, pieces: PieceStore | None = None, label_rule: bool = False, **media: Any
) -> rec.RecordingConverter:
    helper = fake_media(tmp_path / "helpers", **media)
    engine = KitMedia(helper, name="paper-media", helper_version="0.1.0")
    return rec.RecordingConverter(
        CFG, fake_engine(tmp_path / "helpers"), engine, pieces=pieces, label_rule=label_rule
    )


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
    media = KitMedia(helper, name="paper-media", helper_version="0.1.0")
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
    media = KitMedia(helper, name="paper-media", helper_version="0.1.0")
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
    assert not conv.outdated(conv.version(), reason), "P1 has no speech engine: the stub stands"


def test_the_registry_has_no_recording_converter_without_both_engines_and_keeps_it_under_a_label_rule(
    tmp_path: Path,
) -> None:
    ocr = fake_engine(tmp_path / "helpers")
    media = KitMedia(fake_media(tmp_path / "helpers"), name="paper-media", helper_version="0.1.0")
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
    for produced in ("1.0.0+ocr-a+media-b-s1", "1.0.1+ocr-a+media-b-s2", "2.0.0+x", "unavailable"):
        assert not conv.outdated(produced) and not conv.outdated(produced, "no text read on screen")
    assert conv.outdated_key == "1.0.0<1.0.0|-|-"
    assert conv.version() == "1.0.0+ocr-paper-vision-r2-h0.3.0-l1+media-paper-media-h0.1.0-s1"


def test_a_floor_above_a_pages_emitter_reads_it_again_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = converter(tmp_path)
    monkeypatch.setattr(rec, "_EMITTER_VERSION", "1.2.0")
    monkeypatch.setattr(rec, "_REREAD_BELOW", "1.1.0")
    assert conv.outdated("1.0.0+ocr-a+media-b-s1") and conv.outdated("1.0.9+x", "no text read on screen")
    assert not conv.outdated("1.1.0+x") and not conv.outdated("1.2.0+x"), "what a re-read wrote is current"
    assert conv.outdated_key == "1.2.0<1.1.0|-|-"


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
