"""B13 round 42 ruling 152 for the converters: a record ends only at the byte its writer ends it with, so a
reader splits on that byte and a writer escapes or quotes every line break inside a record (C2302, C2301)."""

from __future__ import annotations

import csv
import io
import sys
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest

from agentsync.config import ConvertConfig
from agentsync.convert._common import _csv_bytes
from agentsync.convert.registry import Registry
from agentsync.convert.xlsx import _build_sheet, _Sheet
from agentsync.model import RenderedUnit

CFG = ConvertConfig()

_BREAKS = ["\r", "\n", "\r\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", chr(0x2028), chr(0x2029)]


def _pages(src: Path, cap: int, **kw: object) -> tuple[RenderedUnit, ...]:
    """Every guarded page of ``src`` through Registry.default at ``cap``."""
    cfg = replace(CFG, max_page_bytes=cap, **kw)  # type: ignore[arg-type]
    return Registry.default(cfg).for_name(src.name).convert(src, name=src.name)  # type: ignore[union-attr]


def _records(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


# ---------------------------------------------------------------------------------------------------------
# C2302: a CSV row holding a CR is one record
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("brk", _BREAKS, ids=[repr(b) for b in _BREAKS])
def test_a_csv_field_holding_a_line_break_reads_back_as_one_record_c2302(brk: str) -> None:
    """B13 round 42 C2302: on Python 3.11 (the project's) QUOTE_MINIMAL quotes only the line terminator's
    characters, so `alpha<CR>beta` went out bare and read back as two records. A row with a CR goes through a
    QUOTE_ALL writer; every field reads back byte for byte, one record a row."""
    rows = [["Row", "A"], ["1", f"alpha{brk}beta"], ["2", "plain"]]
    assert _records(_csv_bytes(rows)) == rows


def test_rows_without_a_cr_keep_their_bytes_c2302() -> None:
    """B13 round 42 C2302: only a row holding a CR is quoted whole; every other row keeps today's bytes. The
    split needs a 3.11 patch release as old as 3.11.4 (the verifier's); 3.11.16 quotes a lone CR itself, so
    these exact bytes, the same on every Python, are what fail without the fix here."""
    rows = [["Row", "A", "B"], ["1", "alpha\rbeta", "x"], ["2", "a,b", 'say "hi"'], ["3", "line\nbreak", "7"]]
    got = _csv_bytes(rows)
    assert got == (b'Row,A,B\n"1","alpha\rbeta","x"\n2,"a,b","say ""hi"""\n3,"line\nbreak",7\n'), got
    assert sys.version_info >= (3, 11)


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
def test_an_xlsx_cell_holding_a_lone_cr_stays_one_record_in_its_sidecar_c2302(
    tmp_path: Path, streamed: bool
) -> None:
    """B13 round 42 C2302: a workbook cell `alpha<CR>beta` split the row-capped sidecar's record and the
    streamed one's in two. Each sidecar holds one record per non-empty row, plus its header."""
    book = openpyxl.Workbook()
    ws = book.active
    ws.append(["Name"])
    ws.append(["alpha\rbeta"])
    for n in range(3, 8):
        ws.append([f"r{n}"])
    src = tmp_path / "contoso.xlsx"
    book.save(src)
    kw = {"xlsx_stream_threshold_bytes": 0} if streamed else {"max_rows_per_sheet": 2}
    sheet = _pages(src, 8192, **kw)[1]
    (name, data), *_rest = [sc for sc in sheet.sidecars if sc[0].endswith(".csv")]
    records = _records(data)
    assert len(records) == 1 + 7, records
    assert records[2] == ["2", "alpha\rbeta"], name


# ---------------------------------------------------------------------------------------------------------
# C2301: each comment in the comment sidecar escaped, one record a line
# ---------------------------------------------------------------------------------------------------------


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append({"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}[text[i + 1]])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def test_each_comment_in_the_comment_sidecar_is_one_escaped_record_c2301(tmp_path: Path) -> None:
    """B13 round 42 C2301: the sidecar flattened every whitespace run and stripped the ends, so a multi-line
    or tabbed comment lost its shape; writing it raw would let `injected<LF>A9 (Mallory): fake` forge another
    cell's record. Each comment is escaped (backslash, newline, CR, tab), nothing stripped: one line a
    comment, and un-escaping a line's text gives the comment back byte for byte. The page's line stays
    flattened."""
    from openpyxl.comments import Comment  # noqa: PLC0415

    texts = [
        "line one\nline two\t\ttab   three  spaces ",
        "injected\nA9 (Mallory): fake record",
        "  lead \\n literal\r\nwin",
        "breaks \x85" + chr(0x2028) + chr(0x2029) + "\r end",  # an XML-legal break of each kind
        "Contoso long " + "x" * 9000,
    ]
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    for i, text in enumerate(texts, start=1):
        ws.cell(row=i, column=1, value=f"North {i}").comment = Comment(text, "Ana")
    src = tmp_path / "contoso.xlsx"
    book.save(src)
    read = openpyxl.load_workbook(src).active
    assert read is not None
    texts = [
        str(read.cell(row=i, column=1).comment.text) for i in range(1, len(texts) + 1)
    ]  # as openpyxl reads
    assert "\n" in texts[0] and "\t" in texts[0] and chr(0x2028) in texts[3]
    sheet = _pages(src, 8192)[1]
    whole = [data for name, data in sheet.sidecars if name.endswith(".comments.txt")]
    assert len(whole) == 1, [n for n, _ in sheet.sidecars]
    banner, records_text = whole[0].decode("utf-8").split("\n\n", 1)  # the guard's banner line first
    assert banner.startswith("> [UNTRUSTED CONTENT]") and "\n" not in banner
    records = records_text.split("\n")
    assert records[-1] == "" and len(records) == len(texts) + 1, records
    for i, (record, text) in enumerate(zip(records, texts, strict=False), start=1):
        head = f"A{i} (Ana): "
        assert record.startswith(head)
        assert _unescape(record[len(head) :]) == text
    assert "- A1 (Ana): line one line two tab three spaces" in sheet.body


# ---------------------------------------------------------------------------------------------------------
# C2519 with C2530 and C2452: a comment author, chart title, pivot name and location stay raw in the sidecar
# and are flattened on the page
# ---------------------------------------------------------------------------------------------------------


def _rich_title(*runs: str) -> object:
    """A chart title of rich-text ``runs``, as Excel writes a formatted one."""
    from openpyxl.chart.text import RichText, Text  # noqa: PLC0415
    from openpyxl.chart.title import Title  # noqa: PLC0415
    from openpyxl.drawing.text import Paragraph, RegularTextRun  # noqa: PLC0415

    return Title(tx=Text(rich=RichText(p=[Paragraph(r=[RegularTextRun(t=t) for t in runs])])))


def _built(src: Path) -> _Sheet:
    """``src``'s first sheet as :func:`agentsync.convert.xlsx._build_sheet` collects it."""
    values = openpyxl.load_workbook(src, data_only=True)
    formulas = openpyxl.load_workbook(src)
    return _build_sheet(1, values.worksheets[0], formulas.worksheets[0])


def _labelled_book(tmp_path: Path, authors: list[str], title: object) -> Path:
    from openpyxl.chart import BarChart, Reference  # noqa: PLC0415
    from openpyxl.comments import Comment  # noqa: PLC0415

    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    for i, author in enumerate(authors, start=1):
        ws.cell(row=i, column=1, value=i)
        ws.cell(row=i, column=1).comment = Comment(f"note {i}", author)
    chart = BarChart()
    chart.title = title  # type: ignore[assignment]
    chart.add_data(Reference(ws, min_col=1, min_row=1, max_row=len(authors)))
    ws.add_chart(chart, "C2")
    src = tmp_path / "contoso.xlsx"
    book.save(src)
    return src


def test_a_padded_author_and_chart_title_stay_raw_in_their_sidecar_records_c2530(tmp_path: Path) -> None:
    """B13 round 43 C2530: the author and a rich chart title were stripped before `_record_escape`, so the
    sidecar lost their padding though its contract says nothing is stripped. Un-escaping each record gives
    the raw string back; the page shows them trimmed."""
    author = "  Contoso Reviewer\t"
    src = _labelled_book(tmp_path, [author], _rich_title("  Revenue ", "2026\t "))
    sheet = _built(src)
    (record,) = sheet.comments_whole
    assert _unescape(record[len("A1 (") : record.index("): note 1")]) == author
    (chart,) = sheet.objects_whole
    assert chart.startswith('chart 1: BarChart "') and chart.endswith('" at C2'), chart
    assert _unescape(chart[len('chart 1: BarChart "') : -len('" at C2')]) == "  Revenue 2026\t "
    body = _pages(src, 8192)[1].body
    assert "- A1 (Contoso Reviewer): note 1" in body
    assert 'BarChart "Revenue 2026" at C2' in body


def test_a_multi_line_author_never_forges_a_page_line_c2519(tmp_path: Path) -> None:
    """B13 round 43 C2519: the author went onto the comment's page line raw, so `Contoso<LF># Fake heading`
    wrote a real heading. The page flattens it; the sidecar keeps it, one record a comment."""
    author = "Contoso\n# Fake heading"
    src = _labelled_book(tmp_path, ["Ana", author], "Contoso Q3")
    sheet = _built(src)
    assert len(sheet.comments_whole) == 2
    record = sheet.comments_whole[1]
    assert "\n" not in record and _unescape(record[len("A2 (") : record.index("): note 2")]) == author
    body = _pages(src, 8192)[1].body
    assert not [line for line in body.splitlines() if line.startswith("# Fake")], body
    assert "- A2 (Contoso # Fake heading): note 2" in body


def test_a_whitespace_only_author_or_title_adds_nothing_c2519(tmp_path: Path) -> None:
    """B13 round 43 C2519 with C2530: presence is decided on the flattened value, so a whitespace-only
    author or chart title adds no `( )` or `""` to the page or the sidecar."""
    src = _labelled_book(tmp_path, [" \t "], _rich_title("  ", "\t"))
    sheet = _built(src)
    assert sheet.comments_whole == ["A1: note 1"]
    assert sheet.comments == ["- A1: note 1"]
    assert sheet.objects_whole == ["chart 1: BarChart at C2"]
    assert sheet.objects == ["- [chart 1: BarChart at C2 — no markdown form; open the workbook to view]"]


def test_a_pivot_name_and_location_holding_a_line_break_stay_one_record_and_one_line_c2452(
    tmp_path: Path,
) -> None:
    """B13 round 43 C2452: the pivot location went raw into the objects sidecar and the page, and the name
    raw onto the page, so an LF in either forged a second record or a page heading. The sidecar escapes
    both; the page flattens both before escaping."""
    from types import SimpleNamespace  # noqa: PLC0415

    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.append([1])
    src = tmp_path / "contoso.xlsx"
    book.save(src)
    values, formulas = openpyxl.load_workbook(src, data_only=True), openpyxl.load_workbook(src)
    wsf = formulas.worksheets[0]
    name, loc = "Contoso\n# Forged heading", "A3:B5\nA9 (Mallory): fake record"
    wsf._pivots = [SimpleNamespace(name=name, location=SimpleNamespace(ref=loc))]
    sheet = _build_sheet(1, values.worksheets[0], wsf)
    (record,) = sheet.objects_whole
    assert "\n" not in record and record.startswith("pivot table: ")
    got_name, got_loc = record[len("pivot table: ") :].split(" at ", 1)
    assert (_unescape(got_name), _unescape(got_loc)) == (name, loc)
    page = "\n".join(sheet.objects).splitlines()
    assert page == [
        "- [pivot table: Contoso # Forged heading at A3:B5 A9 (Mallory): fake record — no markdown form; "
        "open the workbook to view]"
    ]


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
def test_a_sheet_chartsheet_or_defined_name_holding_a_line_break_is_one_page_line_c2519(
    tmp_path: Path, streamed: bool
) -> None:
    """B13 round 43 C2519 (first build's review): a sheet title, a chartsheet title and a defined name went
    onto the 00-index and the sheet's `# Sheet:` heading raw, so an LF in any of them forged a heading. Each
    page flattens them before escaping."""
    from openpyxl.chart import BarChart, Reference  # noqa: PLC0415
    from openpyxl.workbook.defined_name import DefinedName  # noqa: PLC0415

    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso\n# Fake sheet heading"
    ws.append(["Region", "Total"])
    ws.append(["West", 3])
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=2))
    ws.add_chart(chart, "D2")
    cs = book.create_chartsheet("CS\n# Fake chartsheet")
    other = BarChart()
    other.add_data(Reference(ws, min_col=2, min_row=1, max_row=2))
    cs.add_chart(other)
    book.defined_names["N1\n# Fake name"] = DefinedName("N1\n# Fake name", attr_text="Contoso!$A$1")
    src = tmp_path / "contoso.xlsx"
    book.save(src)
    kw = {"xlsx_stream_threshold_bytes": 0} if streamed else {}
    index, page = _pages(src, 8192, **kw)[:2]
    for body in (index.body, page.body):
        assert not [line for line in body.splitlines() if line.startswith("# Fake")], body
    assert "- [chartsheet: CS # Fake chartsheet — open the workbook to view]" in index.body
    assert "- N1 # Fake name: `Contoso!$A$1`" in index.body
    if not streamed:  # the streamed path does not list a sheet's objects
        assert "- Contoso # Fake sheet heading: [chart 1: BarChart at D2" in index.body
    (heading,) = [line for line in page.body.splitlines() if line.startswith("# Sheet:")]
    assert heading.startswith("# Sheet: Contoso # Fake sheet heading (used range A1:B2"), heading
