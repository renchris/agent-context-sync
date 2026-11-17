"""B13 round 41 lane E: every page a converter writes stays within max_page_bytes, measured on the guarded
page's real bytes (ruling 143), and the round's other converter items (C2001, C2048, C2049)."""

from __future__ import annotations

import csv
import io
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment

from agentsync import policy, setup_report
from agentsync.config import ConvertConfig
from agentsync.convert import _common, registry
from agentsync.convert import text as text_mod
from agentsync.convert._common import GUARD_RESERVE, _cap_body, _fence
from agentsync.convert.base import make_unit
from agentsync.convert.pandoc import PandocConverter
from agentsync.convert.registry import Registry, _bannered, _with_sidecar_digests
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind
from test_convert_builders import (
    build_drawio,
    build_pdf,
    eml_bytes,
    make_zip,
    msg_bytes,
    teams_doc,
    teams_msg,
)

CFG = ConvertConfig()


def _pages(src: Path, cap: int, **kw: object) -> tuple[RenderedUnit, ...]:
    """Every guarded page of ``src`` through Registry.default at ``cap``."""
    cfg = replace(CFG, max_page_bytes=cap, **kw)  # type: ignore[arg-type]
    return Registry.default(cfg).for_name(src.name).convert(src, name=src.name)  # type: ignore[union-attr]


def _size(unit: RenderedUnit) -> int:
    return len(unit.body.encode("utf-8"))


def _filler(n: int) -> str:
    return ("Contoso filler line of plain words\n" * (n // 35 + 1))[:n]


# ---------------------------------------------------------------------------------------------------------
# C2042 with C2087: a fence a backtick or tilde run outgrows
# ---------------------------------------------------------------------------------------------------------

_LONG = "Contoso line of text\n" * 60_000  # past 1,000,000 bytes, so both caps cut


@pytest.mark.parametrize("cap", [8192, 20_000, 1_000_000])
@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("first.txt", "`" * 2000 + "\n" + _LONG),
        ("later.log", "Contoso\n" * 10 + "`" * 3000 + "\n" + _LONG),
        ("open.md", "# Contoso\n\n" + "`" * 3000 + "\n" + _LONG),
        ("tilde.md", "# Contoso\n\n" + "~" * 3000 + "\n" + _LONG),
        ("one.txt", "`" * 10_000),
        ("abc.txt", "abc\n" + "`" * 9000),
        ("three.json", ("`" * 3000 + "\n") * 3),
        ("mixed.txt", ("~" * 2000 + "\n" + "`" * 2500 + "\n") * 3),
    ],
    ids=["first", "later", "open-fence", "tilde-fence", "one-line", "abc", "three-json", "mixed"],
)
def test_a_long_backtick_run_never_pushes_a_page_past_the_cap_c2042(
    tmp_path: Path, cap: int, name: str, text: str
) -> None:
    """B13 round 41 C2042, C2087: `_capped` fenced its head with a fence one backtick longer than its longest
    run outside its 512-byte reserve, and `_cap_body` closed an open fence with the opener's whole marker, so
    a 2,000-backtick first line gave a 12,014-byte page at 8,192 and 1,003,809 at 1,000,000. The head is cut
    again by the excess, so every guarded page, banner and footer included, stays within the cap, and a
    one-line run longer than the room still leaves a page that says what it holds."""
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    (unit,) = _pages(src, cap)
    assert _size(unit) <= cap
    assert unit.body.strip()
    if len(text.encode()) + 64 > cap:
        assert "[truncated: first " in unit.body and unit.sidecars


def test_a_capped_page_with_no_backtick_run_keeps_its_bytes_c2042(tmp_path: Path) -> None:
    """B13 round 41 C2042: the recut only runs for a head with a run of 3 or more backticks, so an ordinary
    capped text page keeps the bytes 512-byte reserve gave it."""
    cap = 8192
    src = tmp_path / "plain.txt"
    src.write_text(_LONG, encoding="utf-8")
    raw = _LONG.encode()
    head = raw[: cap - 512].decode()
    head = head[: head.rfind("\n") + 1]
    note = (
        f"[truncated: first {len(head.encode())} of {len(raw)} bytes shown; the full text is in the sidecar "
        "`full-text.txt`]"
    )
    unit = text_mod.PlainTextConverter(replace(CFG, max_page_bytes=cap)).convert(src, name="plain.txt")[0]
    assert unit.body == f"{_fence(head, 'text')}\n{note}\n"


# ---------------------------------------------------------------------------------------------------------
# C2044: the guard's banner and footer come off every fit test
# ---------------------------------------------------------------------------------------------------------


def test_the_guard_reserve_holds_the_banner_and_one_footer_line_c2044() -> None:
    """B13 round 41 C2044: GUARD_RESERVE is what the guard adds to a page with one sidecar, less the
    sidecar's name, and each other sidecar adds `_footer_bytes(name)`; the footer's line head is
    registry's own."""
    assert registry.SIDECAR_DIGEST_PREFIX == _common._DIGEST_LINE_HEAD
    for others in ((), ("t00m01s.jpg", "t00m02s.jpg")):
        body = "Contoso"  # no line end: the banner's page adds one
        sidecars = (("full-text.txt", b"x"), *((n, b"\xff") for n in others))
        unit = make_unit(
            unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="", title="t",
            summary="s", body=body, sidecars=sidecars,
        )  # fmt: skip
        guarded = _with_sidecar_digests(_bannered(unit))
        added = _size(guarded) - len(body)
        want = GUARD_RESERVE + len("full-text.txt") + sum(_common._footer_bytes(n) for n in others)
        assert want - 1 <= added <= want  # the reserve counts a line end the footer's rstrip takes back


@pytest.mark.parametrize("cap", [8192, 20_000])
def test_a_cap_body_page_with_other_sidecars_stays_within_the_cap_c2044(cap: int) -> None:
    """B13 round 41 C2044: `_cap_body` (every converter's but text's and xlsx's: eml, msg, pdf, pptx, vtt,
    diagram, a recording window, image, markdown, pandoc, teams) takes the guard's room, and a recording
    window's keyframe footers, off both its fit test and its cut, so a body within 1 to 150 bytes of the cap
    is cut and the guarded page stays within it, uncut or cut."""
    keyframes = ("t00m01s.jpg", "t00m02s.jpg", "t00m03s.jpg")
    for others in ((), keyframes):
        for delta in (-150, -75, -1, 1, 75, 150):
            body = _filler(cap + delta)
            capped, full = _cap_body(body, cap, sidecar_name="full-text.txt", others=others)
            sidecars = (*full, *((n, b"\xff\xd8") for n in others))
            unit = make_unit(
                unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="", title="t",
                summary="s", body=capped, sidecars=sidecars,
            )  # fmt: skip
            assert _size(_with_sidecar_digests(_bannered(unit))) <= cap, (others, delta)


def test_every_cap_body_caller_passes_the_configured_cap() -> None:
    """B13 round 41 C2044: the reserve lives in `_cap_body`, so it holds for a converter only while that
    converter hands `_cap_body` the configured cap, not one it has already cut."""
    src = Path(_common.__file__).parent
    calls = {p.stem: p.read_text(encoding="utf-8") for p in src.glob("*.py") if "_cap_body(" in p.read_text()}
    callers = {k for k in calls if k != "_common"}
    named = "diagram eml image markdown msg pandoc pdf pptx recording_page teams vtt"
    assert sorted(callers) == named.split()
    for stem in callers:
        args = [
            calls[stem][m.end() :].split("sidecar_name=")[0] for m in re.finditer(r"_cap_body\(", calls[stem])
        ]
        assert args and all("max_page_bytes" in a for a in args), stem


def _maker(kind: str) -> tuple[str, Callable[[int], bytes]]:
    if kind == "txt":
        return "near.txt", lambda n: _filler(n).encode()
    if kind == "md":
        return "near.md", lambda n: ("# Contoso\n\n" + _filler(n)).encode()
    if kind == "vtt":
        return "near.vtt", lambda n: ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + _filler(n)).encode()
    if kind == "eml":
        return "near.eml", lambda n: eml_bytes(plain=_filler(n) or "x")
    if kind == "msg":
        return "near.msg", lambda n: msg_bytes(plain=_filler(n) or "x")
    if kind == "teams":
        return "near.teams.json", lambda n: teams_doc(
            [teams_msg("1", "2026-09-01T09:00:00Z", "<p>" + _filler(n).replace("\n", "</p><p>") + "</p>")]
        ).encode()
    if kind == "pdf":
        return "near.pdf", lambda n: _pdf_bytes(_filler(n))
    model = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="2" value="{}" vertex="1" parent="1"/></root></mxGraphModel>'  # noqa: E501
    return "near.drawio", lambda n: _drawio_bytes(model.format(_filler(n).replace("\n", " ")))


def _pdf_bytes(text: str) -> bytes:
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        return build_pdf(
            Path(d) / "p.pdf", [text.split("\n")[i : i + 40] for i in range(0, 4000, 40)]
        ).read_bytes()


def _drawio_bytes(model: str) -> bytes:
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        return build_drawio(Path(d) / "d.drawio", model=model).read_bytes()


@pytest.mark.parametrize("cap", [8192, 20_000])
@pytest.mark.parametrize("kind", ["txt", "md", "vtt", "eml", "msg", "teams", "pdf", "drawio"])
def test_a_body_within_150_bytes_of_the_cap_leaves_the_guard_its_room_c2044(
    tmp_path: Path, kind: str, cap: int
) -> None:
    """B13 round 41 C2044: a body the converter sized at 1 to 150 bytes under the cap, or just over it, was
    98 to 138 bytes past the cap once the guard added the banner and the digest footer. Through
    Registry.default every such guarded page now stays within the cap."""
    name, make = _maker(kind)
    src = tmp_path / name
    inner = Registry.default(replace(CFG, max_page_bytes=10**9)).for_name(name).inner  # type: ignore[union-attr]

    def length(n: int) -> int:
        src.write_bytes(make(n))
        return _size(inner.convert(src, name=name)[0])

    for delta in (-150, -100, -50, -10, 1, 75, 150):
        a, b = 0, 3 * cap
        while a < b:  # the smallest filler whose unguarded body reaches cap + delta
            mid = (a + b) // 2
            a, b = (mid + 1, b) if length(mid) < cap + delta else (a, mid)
        assert cap + delta <= length(a) <= cap + delta + 40, (kind, delta, length(a))
        pages = _pages(src, cap)
        assert [_size(p) for p in pages if _size(p) > cap] == [], (kind, delta)


# ---------------------------------------------------------------------------------------------------------
# xlsx: C2043 with C2088, C2064 and C2046; C2022; C2045; C2047
# ---------------------------------------------------------------------------------------------------------


def _book(
    path: Path,
    *,
    rows: int,
    comments: int = 0,
    text: Callable[[int], str] = lambda i: f"check {i}",
    chart_title: str | None = None,
    empty_cells: bool = False,
) -> Path:
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    for i in range(rows):
        ws.append([f"North {i}", i])
    for i in range(comments):
        ws.cell(row=i + 1, column=3 if empty_cells else 1).comment = Comment(text(i), "Ana")
    if chart_title is not None:
        chart = BarChart()
        chart.title = chart_title
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3))
        ws.add_chart(chart, "D2")
    book.save(path)
    return path


def _rows_shown(body: str) -> int:
    return sum(1 for ln in body.split("\n") if ln.startswith("| ") and not ln.startswith("| Row "))


def _sidecar_text(unit: RenderedUnit, suffix: str) -> str:
    found = [data for name, data in unit.sidecars if name.endswith(suffix)]
    assert len(found) == 1, [n for n, _ in unit.sidecars]
    return found[0].decode("utf-8")


@pytest.mark.parametrize(
    ("cap", "rows", "comments", "least"), [(8192, 41, 20, 26), (1_000_000, 5001, 5000, 26)]
)
def test_short_comments_keep_the_rows_c2043(
    tmp_path: Path, cap: int, rows: int, comments: int, least: int
) -> None:
    """B13 round 41 C2043, C2088: the table's reserve grew 200 bytes a comment with no floor, so 20 short
    comments at 8,192 showed 1 row of 41 (26 before round 40's floor went) and 5,000 at 1,000,000 showed none.
    The comment and object sections now cost their real bytes, up to a quarter of the cap, so the rows come
    back, every comment is listed and the page stays within the cap."""
    index, sheet = _pages(_book(tmp_path / "c.xlsx", rows=rows, comments=comments), cap)
    assert _size(index) <= cap and _size(sheet) <= cap
    assert _rows_shown(sheet.body) >= least
    assert sheet.body.count("\n- A") == comments and "comments.txt" not in sheet.body


@pytest.mark.parametrize("cap", [8192, 20_000])
@pytest.mark.parametrize(
    ("label", "kw"),
    [
        ("one-long", {"rows": 5, "comments": 1, "text": lambda i: "Contoso " * 3750}),
        ("many", {"rows": 210, "comments": 200, "text": lambda i: f"{i:04d} Contoso " + "x" * 127}),
        (
            "empty-cells",
            {"rows": 0, "comments": 60, "text": lambda i: f"{i:02d} " + "note " * 100, "empty_cells": True},
        ),
    ],
)
def test_a_sheets_comment_list_stays_within_the_cap_and_rides_whole_in_its_sidecar_c2064(
    tmp_path: Path, cap: int, label: str, kw: dict[str, object]
) -> None:
    """B13 round 41 C2064, C2088: `_sheet_body` appended every comment whole after a table it had budgeted
    alone, so one 30,000-character comment gave a 30,397-byte page and 198 comments of 140 characters a
    30,745-byte one at 8,192, with no sidecar for them; a comment on an empty cell was dropped. The list now
    fits the room left, its cut note names `01-contoso.comments.txt`, which holds every comment whole, and
    the table keeps rows."""
    src = _book(tmp_path / f"{label}.xlsx", **kw)  # type: ignore[arg-type]
    index, sheet = _pages(src, cap)
    assert _size(index) <= cap and _size(sheet) <= cap
    found = re.search(r"\[comment list truncated at the page size cap: (\d+) of (\d+) listed; every comment "
                      r"is whole in the sidecar `01-contoso\.comments\.txt`\]", sheet.body)  # fmt: skip
    assert found and int(found.group(2)) == kw["comments"]
    whole = _sidecar_text(sheet, ".comments.txt")
    text = kw["text"]
    assert callable(text)
    for i in range(int(found.group(2))):
        assert f": {' '.join(str(text(i)).split())}\n" in whole
    assert "01-contoso.csv" not in [n for n, _ in sheet.sidecars] or "[row cap: first" in sheet.body
    if label == "many":
        assert _rows_shown(sheet.body) > 26
    if label == "empty-cells":
        assert "[empty sheet]" in sheet.body


@pytest.mark.parametrize("cap", [8192, 20_000])
def test_a_long_chart_title_is_cut_on_the_sheet_page_and_whole_in_its_sidecar_c2046(
    tmp_path: Path, cap: int
) -> None:
    """B13 round 41 C2046: a chart title went whole into the object line, so a 20,000-character one gave a
    30,319-byte sheet page at any cap. It is cut to 200 characters and escaped, the page says so, and the
    objects sidecar holds it whole."""
    title = "<!--Contoso " + "x" * 20_000
    index, sheet = _pages(_book(tmp_path / "chart.xlsx", rows=5, chart_title=title), cap)
    assert _size(index) <= cap and _size(sheet) <= cap
    assert '"&lt;!--Contoso ' + "x" * 187 + '…"' in sheet.body and "<!--" not in sheet.body
    assert (
        "[a chart title or pivot name cut to 200 characters; every object is whole in the sidecar "
        in sheet.body
    )
    assert title in _sidecar_text(sheet, ".objects.txt")


def test_a_sheet_under_the_cap_keeps_its_lists_whole_with_no_list_sidecar(tmp_path: Path) -> None:
    """B13 round 41 C2043: a list that fits is listed whole as before, with no note and no list sidecar."""
    _index, sheet = _pages(
        _book(tmp_path / "small.xlsx", rows=5, comments=3, chart_title="Contoso Q3"), 1_000_000
    )
    assert "- A1 (Ana): check 0" in sheet.body and '"Contoso Q3" at D2' in sheet.body
    assert sheet.sidecars == () and "truncated" not in sheet.body and "cut to" not in sheet.body


@pytest.mark.filterwarnings("ignore:Title is more than 31 characters")
def test_a_second_list_and_a_long_sheet_title_keep_the_page_within_the_cap_c2043(tmp_path: Path) -> None:
    """B13 round 41 C2043 (its review): the comment list filled the room the object list's heading and note
    then passed, so 2,000 rows, 400 short comments and two charts gave an 8,218-byte page at 8,192, and a
    6,000-character sheet title an 8,915-byte one. Each list keeps the room the lists after it need, and the
    heading's title is cut to 200 characters."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "C"
    for i in range(2000):
        ws.append([f"North {i}", i])
    for i in range(400):
        ws.cell(row=i + 1, column=1).comment = Comment(f"{i:03d} Contoso check", "A")
    for anchor in ("D2", "D20"):
        chart = BarChart()
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3))
        ws.add_chart(chart, anchor)
    two = tmp_path / "two.xlsx"
    book.save(two)
    _index, sheet = _pages(two, 8192)
    assert _size(sheet) <= 8192 and "## Charts, pivots and images" in sheet.body
    long_title = tmp_path / "title.xlsx"
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso " + "t" * 6000
    ws.append(["North", 1])
    book.save(long_title)
    for streamed in (False, True):
        pages = _pages(
            long_title, 8192, xlsx_stream_threshold_bytes=0 if streamed else CFG.xlsx_stream_threshold_bytes
        )
        assert [_size(p) for p in pages if _size(p) > 8192] == []
        assert "# Sheet: Contoso " + "t" * 191 + "… (used range" in pages[1].body


def test_a_streamed_first_row_of_wide_characters_still_lists_its_column_c2045(tmp_path: Path) -> None:
    """B13 round 41 C2045 (its review): the scaled limit gave a cell the whole room at 4 bytes a character, so
    one column of emoji left its own Schema row no room. The limit keeps room for the page's head and each
    row's other cells first."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append(["\U0001f600" * 2000])
    ws.append(["x"])
    src = tmp_path / "wide.xlsx"
    book.save(src)
    _index, summary = _pages(src, 8192, xlsx_stream_threshold_bytes=0)
    assert (
        _size(summary) <= 8192 and "| A | \U0001f600" in summary.body and "0 of 1 column" not in summary.body
    )


def _wide_book(path: Path, *, sheets: int = 4) -> Path:
    book = openpyxl.Workbook()
    for n in range(sheets):
        ws = book.active if n == 0 else book.create_sheet()
        assert ws is not None
        ws.title = f"Contoso {n}"
        ws.append([f"H{c:02d} " + "h" * 596 for c in range(30)])
        for r in range(5):
            ws.append([f"r{r}c{c}" for c in range(30)])
    book.save(path)
    return path


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
def test_an_oversized_first_row_is_shown_with_its_cells_cut_c2022(tmp_path: Path, streamed: bool) -> None:
    """B13 round 41 C2022: a first row past the table's budget was dropped, so four sheets of thirty
    600-character headers showed no row and no header at 8,192, though the five short rows after it fit. It
    is rebuilt from the cells' plain text, each cut to one length ending `…` before escaping, its note names
    the cut, the later rows follow and the sidecar holds every row whole."""
    src = _wide_book(tmp_path / "wide.xlsx")
    pages = _pages(src, 8192, xlsx_stream_threshold_bytes=0 if streamed else CFG.xlsx_stream_threshold_bytes)
    assert len(pages) == 5 and [p.unit_id for p in pages if _size(p) > 8192] == []
    for page in pages[1:]:
        if streamed:  # thirty first-row values fill the Schema's room, so the rows get none (C1676)
            assert "[schema cut at the page size cap: " in page.body and "[no row fits the page;" in page.body
            continue
        assert re.search(r"\| 1 \| H00 h+… \| H01 h+… \|", page.body), page.body[:600]
        assert "| 6 | r4c0 | r4c1 |" in page.body
        assert re.search(
            r"row 1's cells cut to \d+ characters; every row is in the sidecar `0\d-contoso-\d\.csv`",
            page.body,
        )
        rows = list(csv.reader(io.StringIO(_sidecar_text(page, ".csv").split("\n\n", 1)[-1])))
        assert rows[1][1] == "H00 " + "h" * 596 and len(rows) == 7


def test_xlsx_first_row_past_the_budget_is_cut_and_the_short_row_follows_c2022(tmp_path: Path) -> None:
    """B13 round 41 C2022, on C1676's own workbook: the 20,008-character first cell is shown cut, the short
    row after it is shown, and the page stays within the cap."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso notes"
    ws.append(["Contoso " + "x" * 20_000, "a"])
    ws.append(["short", "b"])
    src = tmp_path / "long.xlsx"
    book.save(src)
    _index, data = _pages(src, 8192)
    assert _size(data) <= 8192
    assert "[row cap: first 2 of 2 non-empty rows shown, row 1's cells cut to " in data.body
    assert "| 1 | Contoso xxx" in data.body and "x…" in data.body and "| 2 | short | b |" in data.body
    _index, summary = _pages(src, 8192, xlsx_stream_threshold_bytes=0)
    assert _size(summary) <= 8192 and "## First 2 of 2 non-empty rows" in summary.body
    assert "| 1 | Contoso xxx" in summary.body and "| 2 | short | b |" in summary.body
    assert (
        "[row 1's cells cut to " in summary.body
        and "every row is in the sidecar `01-contoso-notes.csv`]" in summary.body
    )


def test_thirty_short_headers_are_all_listed_on_the_index_at_the_default_cap_c2045(tmp_path: Path) -> None:
    """B13 round 41 C2045: the index's headers cell was cut at 600 characters at any cap, so thirty headers
    of 27 characters (868 with their separators) listed 21. The cut stays (one huge cell must never push
    every sheet off the index) but scales with the room, so at the default cap all 30 are listed; a streamed
    first-row value of 258 characters stays whole too."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    heads = [f"Contoso column header {c:04d}" for c in range(30)]
    ws.append(heads)
    ws.append(["Contoso " + "v" * 250] + ["x"] * 29)
    src = tmp_path / "heads.xlsx"
    book.save(src)
    index = _pages(src, 1_000_000)[0].body
    assert ", ".join(heads) + " |" in index and "…" not in index
    summary = _pages(src, 1_000_000, xlsx_stream_threshold_bytes=0)[1].body
    assert "| A | Contoso column header 0000 |" in summary
    small = _pages(src, 8192)[0].body
    assert _size(_pages(src, 8192)[0]) <= 8192 and "Contoso column header 0029" in small


def test_the_index_note_names_the_sheet_page_or_its_csv_sidecar_c2047(tmp_path: Path) -> None:
    """B13 round 41 C2047: the cut sheet list's note said each sheet's own page has its headers, but a sheet
    page whose first row is past its budget shows none of them; the CSV sidecar does."""
    src = _wide_book(tmp_path / "many.xlsx", sheets=24)
    for streamed in (False, True):
        index = _pages(src, 8192, xlsx_stream_threshold_bytes=0 if streamed else 20 * 1000**2)[0].body
        assert re.search(
            r"\[sheet list truncated at the page size cap: \d+ of 24 sheets listed; each sheet's own page or "
            r"its CSV sidecar has its headers\]",
            index,
        )


# ---------------------------------------------------------------------------------------------------------
# C2001: a zero-byte file's own reason
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Contoso plan.docx",
        "Contoso.pdf",
        "Contoso.eml",
        "Contoso.vtt",
        "c.teams.json",
        "c.msg",
        "c.drawio",
        "c.odt",
    ],
)
def test_a_zero_byte_file_says_it_holds_zero_bytes_and_stays_empty_c2001(tmp_path: Path, name: str) -> None:
    """B13 round 41 C2001: a zero-byte file's stub said `no text after stripping whitespace and form feeds`,
    which describes text it never had. Its reason now says it holds 0 bytes, keeping the `empty-output`
    prefix, so the report still classes it `empty`."""
    src = tmp_path / name
    src.write_bytes(b"")
    with pytest.raises(UnreadableSourceError) as caught:
        _pages(src, CFG.max_page_bytes)
    assert str(caught.value) == policy.ZERO_BYTES_REASON == "empty-output (the file holds 0 bytes)"
    assert setup_report.quarantine_class(policy.ZERO_BYTES_REASON) == "empty"


def test_a_file_with_bytes_and_no_text_keeps_the_empty_output_reason_and_blank_text_stays_a_page_c2001(
    tmp_path: Path,
) -> None:
    """B13 round 41 C2001: only the zero-byte branches take the new reason. A PDF with a page and no text
    keeps `EMPTY_OUTPUT_REASON`, and a 0-byte or blank `.txt` stays a page (C930), never a stub."""

    class _Blank:
        converter_id = "blank"
        extensions = (".blank",)

        def version(self) -> str:
            return "1"

        def options(self) -> dict[str, str]:
            return {}

        def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
            body = "\x0c\n"  # a page that holds only a form feed, as pdfminer's empty page does
            return (make_unit(unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="",
                              title="t", summary="s", body=body),)  # fmt: skip

    blank = tmp_path / "a.blank"
    blank.write_bytes(b"x\n")
    with pytest.raises(UnreadableSourceError) as caught:
        Registry([_Blank()], banner=True).for_name("a.blank").convert(blank, name="a.blank")  # type: ignore[union-attr]
    assert str(caught.value) == policy.EMPTY_OUTPUT_REASON
    for data in (b"", b"  \n\f\n"):
        src = tmp_path / "blank.txt"
        src.write_bytes(data)
        (page,) = _pages(src, CFG.max_page_bytes)
        assert "[empty file]" in page.body


# ---------------------------------------------------------------------------------------------------------
# C2048: pandoc's crash and exit lines from its own lines only
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "said"),
    [
        (
            "echo \"[WARNING] Duplicate identifier 'contoso-q3' at line 1 column 63\" >&2\n"
            'echo "pandoc: heap overflow" >&2\nkill -ABRT $$\n',
            "pandoc was stopped by SIGABRT (signal 6): pandoc: heap overflow",
        ),
        (
            'echo "[WARNING] Could not convert TeX math \\\\frac{contoso}, rendering as TeX:" >&2\n'
            'echo "unexpected error: contoso-secret" >&2\nkill -SEGV $$\n',
            "pandoc was stopped by SIGSEGV (signal 11)",
        ),
        ("echo \"[WARNING] Duplicate identifier 'contoso-q3'\" >&2\nexit 64\n", "pandoc exited 64"),
        (
            'echo "[WARNING] Could not convert TeX math, rendering as TeX:" >&2\n'
            'echo "  pandoc: Contoso secret" >&2\nkill -SEGV $$\n',
            "pandoc was stopped by SIGSEGV (signal 11)",
        ),
        (
            "echo \"[WARNING] Duplicate identifier 'contoso-q3'\" >&2\n"
            'echo "pandoc: /Users/ana/Contoso/x.docx: openBinaryFile: does not exist" >&2\nexit 1\n',
            "pandoc exited 1: pandoc: <path>",
        ),
    ],
    ids=["abrt-after-warning", "segv-warning-only", "exit-warning-only", "segv-indented", "exit-own-line"],
)
def test_pandocs_crash_and_exit_lines_come_from_its_own_lines_only_c2048(
    tmp_path: Path, script: str, said: str
) -> None:
    """B13 round 41 C2048: the crash line was pandoc's first stderr line, often a `[WARNING]` that quotes the
    document, and a non-zero exit quoted it too. Only lines that start `pandoc:` are read now, paths taken
    out; with none, the line names the signal or the exit code alone."""
    pandoc = tmp_path / "pandoc"
    pandoc.write_text('#!/bin/sh\n[ "$1" = --version ] && { echo "pandoc 3.1.2"; exit 0; }\n' + script)
    pandoc.chmod(0o700)
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == said
    assert "contoso" not in str(caught.value).lower()


# ---------------------------------------------------------------------------------------------------------
# C2049: the csv field limit's read and raise are one step
# ---------------------------------------------------------------------------------------------------------


def test_two_parses_on_two_threads_never_lower_the_csv_field_limit_c2049(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 41 C2049: `_parse_delimited` read the limit and then raised it in two calls, so a parse of a
    shorter text on another thread could set its lower limit after a longer one's, and the longer CSV came
    back as fenced text. The read and the raise run under one lock now: one thread's pair is never split by
    the other's, and the limit ends at the longer text's."""
    real = csv.field_size_limit
    events: list[str] = []
    first_read = threading.Event()

    def limit(*new: int) -> int:
        who = threading.current_thread().name
        events.append(f"{who}:{'set' if new else 'read'}")
        if not new and who == "A" and not first_read.is_set():
            first_read.set()
            threading.Event().wait(0.3)  # room for B to read in between, were there no lock
        return real(*new)

    monkeypatch.setattr(text_mod.csv, "field_size_limit", limit)
    real(131_072)
    try:
        short, long_ = "a," + "x" * 200_000 + "\n1,2\n", "a," + "y" * 500_000 + "\n1,2\n"
        got: dict[str, object] = {}
        a = threading.Thread(name="A", target=lambda: got.update(A=text_mod._parse_delimited(short, ",")))
        b = threading.Thread(name="B", target=lambda: got.update(B=text_mod._parse_delimited(long_, ",")))
        a.start()
        first_read.wait(5)
        b.start()
        a.join(10)
        b.join(10)
        pairs = [e.split(":")[0] for e in events]
        assert pairs in (["A", "A", "B", "B"], ["A", "A", "B"]), events
        assert got["A"] and got["B"] and real() > 500_000
    finally:
        real(131_072)
