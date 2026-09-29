"""Every real converter on generated inputs: fidelity, the measured failure modes, unreadable/failed paths."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert.eml import EmlConverter
from agentsync.convert.markdown import MarkdownConverter
from agentsync.convert.pandoc import PandocConverter, _bundled_pandoc, _PandocRunner
from agentsync.convert.pdf import PdfConverter
from agentsync.convert.pptx import PptxConverter
from agentsync.convert.registry import Registry
from agentsync.convert.teams import TeamsMonthConverter, _prepare_html
from agentsync.convert.text import PlainTextConverter
from agentsync.convert.xlsx import XlsxConverter
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind
from test_convert_builders import (
    build_docx_image,
    build_docx_merged,
    build_pdf,
    build_pptx_rich,
    build_xlsx_rich,
    eml_bytes,
    make_zip,
    ole_encrypted,
    pandoc_build,
    teams_doc,
    teams_msg,
    with_cached_values,
)

CFG = ConvertConfig()


_FROM_BOB: dict[str, object] = {"from": "Bob"}


def _raw_month(month: str, messages: str) -> str:
    """A hand-written rollup JSON (to test validation of malformed documents)."""
    head = '{"schema": "agentsync.teams-month/1", "team_name": "a", "channel_name": "b"'
    return f'{head}, "month": "{month}", "messages": {messages}}}'


def _one(units: tuple[RenderedUnit, ...]) -> RenderedUnit:
    assert len(units) == 1
    u = units[0]
    assert (u.unit_id, u.kind, u.index, u.of, u.name, u.file_stem) == ("whole", UnitKind.WHOLE, 0, 1, "", "")
    assert u.body.endswith("\n") and not u.body.endswith("\n\n")
    return u


def _write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    p = tmp_path / name
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------------------------------------
# pandoc: docx / odt / rtf / html
# ---------------------------------------------------------------------------------------------------------


def test_pandoc_uses_bundled_binary_by_absolute_path() -> None:
    runner = _PandocRunner(None)
    path = runner.path()
    assert path.is_absolute() and path == _bundled_pandoc()
    assert "pypandoc" in path.parts and path.parent.name == "files"
    assert re.fullmatch(r"1\.0\.0\+pandoc-\d+(\.\d+)+", PandocConverter(CFG).version())


def test_docx_fixture_headings_table_text(fixture_files: dict[str, Path]) -> None:
    u = _one(PandocConverter(CFG).convert(fixture_files["sample.docx"], name="sample.docx"))
    assert u.body.startswith("# Quarterly Plan\n")
    assert "## Budget" in u.body
    assert re.search(r"^\| Region +\| +Spend \| +Forecast \|$", u.body, re.M)
    assert "purchase order" in u.body and "café" in u.body
    assert "- first bullet" in u.body
    assert u.title == "Quarterly Plan"
    assert u.summary == "Word document; headings: Quarterly Plan; Budget"


def test_docx_merged_cell_becomes_html_table(tmp_path: Path) -> None:
    u = _one(PandocConverter(CFG).convert(build_docx_merged(tmp_path), name="m.docx"))
    assert '<td colspan="2">West (merged)</td>' in u.body
    assert "<th>Region</th>" in u.body and "## Notes" in u.body


def test_docx_images_become_text_references_and_nothing_is_extracted(tmp_path: Path) -> None:
    src = build_docx_image(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    u = _one(PandocConverter(CFG).convert(src, name="image.docx"))
    assert "![" not in u.body and "<img" not in u.body and "<figure" not in u.body
    assert re.search(r"\[image: a chart — media/\S+\.png\]", u.body)
    assert re.search(r"Text \[image: inline — media/\S+\.png\] here\.", u.body)
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # no --extract-media output anywhere


def test_html_data_uri_images_never_reach_the_page(tmp_path: Path) -> None:
    html = (
        '<html><body><h1>T</h1><p>x <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==" alt="logo">'
        " y</p>"
        '<div class="wrap"><div><p>nested <span class="u">span</span></p></div></div>'
        "<script>alert('x')</script>"
        "<figure><img src='chart.png' alt='Chart'><figcaption>Cap</figcaption></figure>"
        '<p><img src="file:///etc/passwd"></p></body></html>'
    )
    u = _one(PandocConverter(CFG).convert(_write(tmp_path, "p.html", html), name="p.html"))
    assert "base64" not in u.body and "iVBOR" not in u.body
    assert "[image: logo]" in u.body
    assert "[image: Chart — chart.png] Cap" in u.body
    assert "<div" not in u.body and "<span" not in u.body and "alert" not in u.body
    assert "root:" not in u.body  # --sandbox: nothing read from the local filesystem
    assert "nested span" in u.body


def test_html_legacy_charset_is_decoded(tmp_path: Path) -> None:
    html = (
        '<html><head><meta charset="windows-1252"></head>'
        "<body><p>caf\xe9 \u201cquoted\u201d</p></body></html>"
    )
    src = _write(tmp_path, "legacy.htm", html.encode("cp1252"))
    u = _one(PandocConverter(CFG).convert(src, name="legacy.htm"))
    assert "café “quoted”" in u.body


@pytest.mark.parametrize("ext", [".odt", ".rtf"])
def test_odt_and_rtf(tmp_path: Path, ext: str) -> None:
    src = pandoc_build("# Minutes\n\nThe **purchase order** was signed.\n", "markdown", tmp_path / f"m{ext}")
    u = _one(PandocConverter(CFG).convert(src, name=f"m{ext}"))
    assert "Minutes" in u.body and "purchase order" in u.body
    assert "<span" not in u.body


def test_encrypted_ooxml_and_odf_are_unreadable(tmp_path: Path) -> None:
    conv = PandocConverter(CFG)
    with pytest.raises(UnreadableSourceError, match="encrypted"):
        conv.convert(ole_encrypted(tmp_path / "e.docx"), name="e.docx")
    manifest = (
        b"<manifest:manifest><manifest:file-entry><manifest:encryption-data/>"
        b"</manifest:file-entry></manifest:manifest>"
    )
    odt = make_zip(
        tmp_path / "e.odt",
        {"mimetype": b"application/vnd.oasis.opendocument.text", "META-INF/manifest.xml": manifest},
    )
    with pytest.raises(UnreadableSourceError, match="password-protected"):
        conv.convert(odt, name="e.odt")
    with pytest.raises(ConversionError, match="not a ZIP"):
        conv.convert(_write(tmp_path, "n.docx", b"plain text, not a package"), name="n.docx")


def test_corrupt_docx_is_a_conversion_error(tmp_path: Path) -> None:
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError, match="pandoc exited"):
        PandocConverter(CFG).convert(bad, name="bad.docx")


def test_configured_pandoc_path_must_exist(tmp_path: Path) -> None:
    conv = PandocConverter(replace(CFG, pandoc_path=tmp_path / "no-such-pandoc"))
    with pytest.raises(ConversionError, match="pandoc_path"):
        conv.version()
    assert conv.options()["pandoc_binary"] == "configured"


def test_empty_html_gets_a_placeholder(tmp_path: Path) -> None:
    u = _one(
        PandocConverter(CFG).convert(_write(tmp_path, "e.html", "<html><body></body></html>"), name="e.html")
    )
    assert u.body == "[empty document]\n"


# ---------------------------------------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------------------------------------


def test_xlsx_fixture_units_and_the_measured_failure_modes(fixture_files: dict[str, Path]) -> None:
    units = XlsxConverter(CFG).convert(fixture_files["sample.xlsx"], name="sample.xlsx")
    assert [(u.unit_id, u.kind, u.index, u.of, u.file_stem, u.name) for u in units] == [
        ("index", UnitKind.INDEX, 0, 3, "00-index", ""),
        ("sheet:1", UnitKind.SHEET, 1, 3, "01-Q3 Budget", "Q3 Budget"),
        ("sheet:2", UnitKind.SHEET, 2, 3, "02-Notes", "Notes"),
    ]
    index, q3, notes = units
    assert "| 1 | Q3 Budget | A1:D6 | 5 | 4 | Region, Spend, Forecast, Variance |" in index.body
    assert "| 2 | Notes | A1:B4 | 4 | 2 | Key, Value |" in index.body
    assert "- Merged note across four columns" in index.body and "- finance" in index.body
    assert q3.body.startswith("# Sheet: Q3 Budget (used range A1:D6)\n")
    assert "| 2 | North | 100 | 120 | `=C2-B2` |" in q3.body  # formula kept although no cached value
    assert "| 4 | Total | `=SUM(B2:B3)` | `=SUM(C2:C3)` |  |" in q3.body  # empty stays empty
    assert q3.body.count("Merged note across four columns") == 4  # merged anchor propagated
    assert "[no cached values: 4 of 4 formula cells" in q3.body
    for bad in ("NaN", "None", "100.0", "Unnamed"):
        assert bad not in q3.body + notes.body
    assert "| 3 | empty |  |" in notes.body and "| 4 | ratio | 0.25 |" in notes.body
    assert q3.summary.startswith("Sheet Q3 Budget: used range A1:D6, 5 rows x 4 columns; columns: Region")


def test_xlsx_cached_values_render_beside_formulas(tmp_path: Path, fixture_files: dict[str, Path]) -> None:
    src = with_cached_values(
        fixture_files["sample.xlsx"], tmp_path / "cached.xlsx", "xl/worksheets/sheet1.xml",
        {"D2": "20", "D3": "-5", "B4": "180", "C4": "195"},
    )  # fmt: skip
    q3 = XlsxConverter(CFG).convert(src, name="cached.xlsx")[1]
    assert "| 2 | North | 100 | 120 | 20 `=C2-B2` |" in q3.body
    assert "| 4 | Total | 180 `=SUM(B2:B3)` | 195 `=SUM(C2:C3)` |  |" in q3.body
    assert "no cached values" not in q3.body


def test_xlsx_rich_workbook_objects_comments_hidden_names(tmp_path: Path) -> None:
    units = XlsxConverter(CFG).convert(build_xlsx_rich(tmp_path / "r.xlsx"), name="r.xlsx")
    index, data, secret = units
    assert "| 2 | Secret (hidden) | A1:A1 |" in index.body
    assert "- TaxRate: `Data!$C$2`" in index.body
    assert '[chart 1: BarChart "Qty by item" at G2' in index.body and "Data: [chart 1" in index.body
    assert "| 2 | item-000 | 1 | 2.5 | `=B2*C2` | 3 |" in data.body  # integral float -> 3
    assert "| 3 | item-001 | 2 | 2.5 | `=B3*C3` | 2026-09-01 |" in data.body
    assert "| TRUE |" in data.body
    assert "pipe\\|text<br>second line" in data.body
    assert "- B2 (Jane): check this quantity" in data.body
    assert "## Charts, pivots and images" in data.body
    assert secret.body.startswith("# Sheet: Secret (used range A1:A1) (hidden sheet)")


def test_xlsx_row_cap_emits_csv_sidecar(tmp_path: Path) -> None:
    src = build_xlsx_rich(tmp_path / "big.xlsx", rows=30)
    units = XlsxConverter(replace(CFG, max_rows_per_sheet=5)).convert(src, name="big.xlsx")
    data = units[1]
    assert (
        "[row cap: first 5 of 31 non-empty rows shown; every row is in the sidecar `01-data.csv`]"
        in data.body
    )
    assert len(data.sidecars) == 1 and data.sidecars[0][0] == "01-data.csv"
    rows = list(csv.reader(io.StringIO(data.sidecars[0][1].decode())))
    assert rows[0] == ["Row", "A", "B", "C", "D", "E"] and len(rows) == 32
    assert rows[31][:4] == ["31", "item-029", "30", "2.5"] and rows[31][4] == "=B31*C31"


def test_xlsx_byte_cap_also_caps_rows(tmp_path: Path) -> None:
    src = build_xlsx_rich(tmp_path / "wide.xlsx", rows=400)
    data = XlsxConverter(replace(CFG, max_page_bytes=8000)).convert(src, name="wide.xlsx")[1]
    assert len(data.body.encode()) <= 8000
    assert "[row cap: first" in data.body and data.sidecars


def test_xlsx_streaming_path_above_threshold(tmp_path: Path, fixture_files: dict[str, Path]) -> None:
    conv = XlsxConverter(replace(CFG, xlsx_stream_threshold_bytes=0, max_rows_per_sheet=2))
    units = conv.convert(fixture_files["sample.xlsx"], name="sample.xlsx")
    assert [(u.unit_id, u.kind, u.file_stem) for u in units] == [
        ("index", UnitKind.INDEX, "00-index"),
        ("summary:1", UnitKind.SUMMARY, "01-Q3 Budget"),
        ("summary:2", UnitKind.SUMMARY, "02-Notes"),
    ]
    q3 = units[1]
    assert "streamed summary" in q3.body and "## Schema" in q3.body
    assert "| A | Region | text 5 | 5 |" in q3.body
    assert "## First 2 of 5 non-empty rows" in q3.body
    assert "| 2 | North | 100 | 120 | `=C2-B2` |" in q3.body
    assert q3.sidecars[0][0] == "01-q3-budget.csv"
    rows = list(csv.reader(io.StringIO(q3.sidecars[0][1].decode())))
    assert rows[1] == ["1", "Region", "Spend", "Forecast", "Variance"] and len(rows) == 6
    assert "Streamed" in units[0].body


def test_xlsx_unreadable_and_corrupt(tmp_path: Path) -> None:
    conv = XlsxConverter(CFG)
    with pytest.raises(UnreadableSourceError, match="encrypted"):
        conv.convert(ole_encrypted(tmp_path / "e.xlsx"), name="e.xlsx")
    with pytest.raises(ConversionError):
        conv.convert(make_zip(tmp_path / "c.xlsx", {"junk.txt": b"x"}), name="c.xlsx")


# ---------------------------------------------------------------------------------------------------------
# pptx
# ---------------------------------------------------------------------------------------------------------


def test_pptx_fixture_anchors_titles_bullets_notes(fixture_files: dict[str, Path]) -> None:
    u = _one(PptxConverter(CFG).convert(fixture_files["sample.pptx"], name="sample.pptx"))
    assert u.body.startswith(
        "<!-- Slide number: 1 -->\n\n## 1. Kickoff\n\n- Scope agreed\n- Pricing pending\n"
    )
    assert "### Notes:\n\nMention the renewal date." in u.body
    assert "<!-- Slide number: 2 -->\n\n## 2. Next steps" in u.body and "Owner: finance." in u.body
    assert u.title == "1. Kickoff"
    assert u.summary == "Presentation: 2 slide(s); titles: 1. Kickoff; 2. Next steps"


def test_pptx_rich_deck(tmp_path: Path) -> None:
    u = _one(PptxConverter(CFG).convert(build_pptx_rich(tmp_path / "r.pptx"), name="r.pptx"))
    body = u.body
    assert body.index("LEFT box") < body.index("RIGHT box")  # reading order, not XML order
    assert "| k | v |" in body and "| rate | 4.2% |" in body and "| merged row | merged row |" in body
    assert "[image: Company logo]" in body
    assert '[chart: "Revenue", column_clustered]' in body or "[chart: column_clustered]" in body
    assert "| Q1 | 10 |" in body and "| Q2 | 12.5 |" in body
    assert "<!-- Slide number: 2 -->\n\ninside group" in body
    assert "<!-- Slide number: 3 -->\n\n## Hidden one\n\n[hidden slide]" in body


def test_pptx_unreadable_and_corrupt(tmp_path: Path) -> None:
    with pytest.raises(UnreadableSourceError):
        PptxConverter(CFG).convert(ole_encrypted(tmp_path / "e.pptx"), name="e.pptx")
    with pytest.raises(ConversionError):
        PptxConverter(CFG).convert(make_zip(tmp_path / "c.pptx", {"x": b"y"}), name="c.pptx")


# ---------------------------------------------------------------------------------------------------------
# pdf
# ---------------------------------------------------------------------------------------------------------


def test_pdf_fixture_page_anchors(fixture_files: dict[str, Path]) -> None:
    u = _one(PdfConverter(CFG).convert(fixture_files["sample.pdf"], name="sample.pdf"))
    assert u.body == (
        "<!-- page: 1 -->\n\nQuarterly Plan - page 1\nThe purchase order was approved.\n\n"
        "<!-- page: 2 -->\n\nBudget - page 2\nNorth spend 100, forecast 120.\n"
    )
    assert u.title == "Quarterly Plan - page 1" and u.summary == "PDF: 2 page(s)"


def test_pdf_scanned_page_marker_and_markdown_neutralised(tmp_path: Path) -> None:
    src = build_pdf(
        tmp_path / "s.pdf", [["# not a heading", "<!-- page: 99 -->", "---", "enough text on this page"], []]
    )
    u = _one(PdfConverter(CFG).convert(src, name="s.pdf"))
    assert "\\# not a heading" in u.body
    assert "&lt;!-- page: 99 -->" in u.body and u.body.count("<!-- page:") == 2
    assert "\n\\---\n" in u.body
    assert "<!-- page: 2 -->\n\n[scanned page: no text layer]" in u.body
    assert "1 without a text layer" in u.summary


def test_pdf_password_protected_is_unreadable(tmp_path: Path) -> None:
    src = build_pdf(tmp_path / "locked.pdf", [["secret"]], encrypt=True)
    with pytest.raises(UnreadableSourceError, match="password-protected"):
        PdfConverter(CFG).convert(src, name="locked.pdf")


def test_pdf_garbage_is_a_conversion_error(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match="not a PDF"):
        PdfConverter(CFG).convert(_write(tmp_path, "x.pdf", b"hello"), name="x.pdf")
    with pytest.raises(ConversionError, match="pdfminer"):
        PdfConverter(CFG).convert(_write(tmp_path, "y.pdf", b"%PDF-1.4\n1 0 obj << /Broken"), name="y.pdf")


def test_pdf_cap_keeps_the_full_text_in_a_sidecar(tmp_path: Path) -> None:
    src = build_pdf(
        tmp_path / "long.pdf",
        [[f"line {i} of page {p} with some filler text" for i in range(40)] for p in range(20)],
    )
    u = _one(PdfConverter(replace(CFG, max_page_bytes=4000)).convert(src, name="long.pdf"))
    assert len(u.body.encode()) <= 4000
    assert "the full text is in the sidecar `full-text.txt`" in u.body
    assert u.sidecars[0][0] == "full-text.txt" and b"line 39 of page 19" in u.sidecars[0][1]


# ---------------------------------------------------------------------------------------------------------
# text / markdown
# ---------------------------------------------------------------------------------------------------------


def test_text_fixture_is_fenced(fixture_files: dict[str, Path]) -> None:
    u = _one(PlainTextConverter(CFG).convert(fixture_files["sample.txt"], name="sample.txt"))
    assert u.body == "```text\nPlain text notes.\nSecond line with purchase order.\n```\n"
    assert u.title == "Plain text notes." and u.summary == "Text (txt): 2 line(s)"


def test_csv_fixture_is_a_table(fixture_files: dict[str, Path]) -> None:
    u = _one(PlainTextConverter(CFG).convert(fixture_files["sample.csv"], name="sample.csv"))
    table = "| region | spend |\n|---|---|\n| North | 100 |\n| South | 80 |\n"
    assert u.body == "CSV table: 2 data rows x 2 columns\n\n" + table
    assert u.title == "CSV table: region, spend"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("héllo\n".encode("utf-16"), "héllo"),
        (b"\xef\xbb\xbfbom text\n", "bom text"),
        ("smart “quotes” caf\xe9\n".encode("cp1252"), "smart “quotes” café"),
        ("café\r\nline\r\n".encode(), "café\nline"),
    ],
)
def test_text_decoding(tmp_path: Path, data: bytes, expected: str) -> None:
    u = _one(PlainTextConverter(CFG).convert(_write(tmp_path, "t.txt", data), name="t.txt"))
    assert expected in u.body and "\r" not in u.body


def test_text_edge_cases(tmp_path: Path) -> None:
    conv = PlainTextConverter(CFG)
    with pytest.raises(ConversionError, match="NUL"):
        conv.convert(_write(tmp_path, "b.log", b"abc\x00def"), name="b.log")
    assert _one(conv.convert(_write(tmp_path, "e.txt", b""), name="e.txt")).body == "[empty file]\n"
    fenced = _one(conv.convert(_write(tmp_path, "f.json", '{"a": "```"}\n'), name="f.json"))
    assert fenced.body.startswith("````json\n") and fenced.body.endswith("\n````\n")
    tsv = _one(conv.convert(_write(tmp_path, "t.tsv", "a\tb\n1\t2|3\n"), name="t.tsv"))
    assert "| 1 | 2\\|3 |" in tsv.body
    broken = _one(conv.convert(_write(tmp_path, "x.csv", 'a,"b\n'), name="x.csv"))
    assert broken.body.startswith("```csv\n")  # not well-formed CSV: fenced, not dropped


def test_csv_row_cap_sidecar(tmp_path: Path) -> None:
    text = "id,v\n" + "".join(f"{i},{i * 2}\n" for i in range(50))
    u = _one(
        PlainTextConverter(replace(CFG, max_rows_per_sheet=10)).convert(
            _write(tmp_path, "d.csv", text), name="d.csv"
        )
    )
    assert "[row cap: first 10 of 50 rows shown; every row is in the sidecar `full-table.csv`]" in u.body
    assert u.sidecars[0][0] == "full-table.csv" and u.sidecars[0][1].decode().count("\n") == 51


def test_big_text_is_capped_with_closed_fence(tmp_path: Path) -> None:
    text = "".join(f"log line {i}\n" for i in range(5000))
    u = _one(
        PlainTextConverter(replace(CFG, max_page_bytes=2000)).convert(
            _write(tmp_path, "a.log", text), name="a.log"
        )
    )
    assert len(u.body.encode()) <= 2000
    assert u.body.count("```") == 2
    assert u.sidecars == (("full-text.txt", text.encode()),)


def test_markdown_frontmatter_is_fenced(fixture_files: dict[str, Path]) -> None:
    u = _one(MarkdownConverter(CFG).convert(fixture_files["sample.md"], name="sample.md"))
    assert u.body.startswith("## Source frontmatter\n\n```yaml\ntitle: Sample\n```\n\n# Quarterly Plan\n")
    assert u.title == "Quarterly Plan"
    assert not u.body.startswith("---")


def test_markdown_variants(tmp_path: Path) -> None:
    conv = MarkdownConverter(CFG)
    plain = _one(conv.convert(_write(tmp_path, "a.md", "Intro\r\n\r\n## Part\r\n"), name="a.md"))
    assert plain.body == "Intro\n\n## Part\n" and plain.title == "Part"
    fm_only = _one(
        conv.convert(_write(tmp_path, "b.md", "---\ntitle: 'Quoted'\n---\nno headings\n"), name="b.md")
    )
    assert fm_only.title == "Quoted"
    unterminated = _one(conv.convert(_write(tmp_path, "c.md", "---\nnot: closed\n"), name="c.md"))
    assert unterminated.body == "---\nnot: closed\n"
    empty = _one(conv.convert(_write(tmp_path, "d.md", "---\n---\n"), name="d.md"))
    assert "[empty document]" in empty.body


# ---------------------------------------------------------------------------------------------------------
# eml
# ---------------------------------------------------------------------------------------------------------


def test_eml_fixture_headers_body_attachments_no_base64(fixture_files: dict[str, Path]) -> None:
    u = _one(EmlConverter(CFG).convert(fixture_files["sample.eml"], name="sample.eml"))
    assert u.body.startswith("# Acme kickoff recap\n\n| Header | Value |\n")
    assert "| From | Jane Doe <jane.doe@example.com> |" in u.body
    assert "| Message-ID | <kickoff-recap@example.com> |" in u.body
    assert "The purchase order is approved." in u.body
    assert "| 1 | spend.csv | text/csv | 23 | " in u.body
    for leak in ("Content-Transfer-Encoding", "MIME-Version", "cmVnaW9u", "boundary="):
        assert leak not in u.body
    assert u.title == "Acme kickoff recap" and "1 attachment(s)" in u.summary


def test_eml_encoded_subject_html_only_and_alternatives(tmp_path: Path) -> None:
    conv = EmlConverter(CFG)
    html_only = eml_bytes(
        subject="Q3 déck", plain=None, html="<p>Hello <b>team</b></p><img src='cid:logo' alt='logo'>"
    )
    u = _one(conv.convert(_write(tmp_path, "h.eml", html_only), name="h.eml"))
    assert u.title == "Q3 déck" and "Hello **team**" in u.body and "[image: logo]" in u.body
    assert "| Cc | Boss <boss@example.com> |" in u.body and "| In-Reply-To | <m0@example.com> |" in u.body
    alt = eml_bytes(
        plain="plain wins\n",
        html="<p>html loses</p>",
        attachments=(("a.pdf", b"%PDF-1.4 x", "application/pdf"),),
    )
    u2 = _one(conv.convert(_write(tmp_path, "a.eml", alt), name="a.eml"))
    assert "plain wins" in u2.body and "html loses" not in u2.body
    assert "| 1 | a.pdf | application/pdf | 10 |" in u2.body and "text/html" not in u2.body
    assert "JVBER" not in u2.body


def test_eml_attached_message_is_listed_not_expanded(tmp_path: Path) -> None:
    inner = EmailMessage()
    inner["Subject"] = "Original"
    inner.set_content("inner body must not be inlined\n")
    outer = EmailMessage()
    outer["Subject"] = "Fwd"
    outer.set_content("see attached\n")
    outer.add_attachment(inner)
    u = _one(EmlConverter(CFG).convert(_write(tmp_path, "f.eml", bytes(outer)), name="f.eml"))
    assert "| 1 | Original.eml | message/rfc822 |" in u.body
    assert "inner body must not be inlined" not in u.body


def test_eml_protected_and_empty(tmp_path: Path) -> None:
    smime = (
        b"From: a@example.com\r\nSubject: secret\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\nMIAGCSqGSIb3DQEHA6CAMIACAQA=\r\n"
    )
    with pytest.raises(UnreadableSourceError, match="encrypted"):
        EmlConverter(CFG).convert(_write(tmp_path, "s.eml", smime), name="s.eml")
    irm = eml_bytes(attachments=(("message.rpmsg", b"\x00\x01", "application/x-microsoft-rpmsg-message"),))
    with pytest.raises(UnreadableSourceError, match="IRM"):
        EmlConverter(CFG).convert(_write(tmp_path, "i.eml", irm), name="i.eml")
    with pytest.raises(ConversionError, match="empty"):
        EmlConverter(CFG).convert(_write(tmp_path, "e.eml", b"  \n"), name="e.eml")


# ---------------------------------------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------------------------------------


def test_teams_fixture(fixture_files: dict[str, Path]) -> None:
    u = _one(TeamsMonthConverter(CFG).convert(fixture_files["2026-09.teams.json"], name="2026-09.teams.json"))
    assert u.body.startswith("# Acme / General — 2026-09\n")
    assert "### 2026-09-14T09:00:00Z — Jane Doe\n\n**Kickoff**\n\nKickoff is **Monday**." in u.body
    assert "> **John Roe** — 2026-09-14T09:05:00Z\n>\n> I will send the purchase order." in u.body
    assert u.title == "Acme / General — 2026-09"


def test_teams_order_deleted_orphans_emoji_edits_attachments(tmp_path: Path) -> None:
    doc = teams_doc(
        [
            teams_msg("3", "2026-09-02T10:00:00Z", "<p>second thread</p>"),
            teams_msg(
                "1",
                "2026-09-01T10:00:00Z",
                '<p>first <emoji id="like" alt="👍" title="Like"></emoji></p>',
                last_modified="2026-09-01T11:00:00Z",
                attachments=[{"name": "plan.docx", "content_url": "https://contoso.sharepoint.com/plan.docx"}],
            ),
            teams_msg("2", "2026-09-01T10:05:00Z", "<p>gone</p>", reply_to_id="1", deleted=True, **_FROM_BOB),
            teams_msg("4", "2026-09-03T10:00:00Z", "<p>late reply</p>", reply_to_id="999"),
        ]
    )  # fmt: skip
    u = _one(TeamsMonthConverter(CFG).convert(_write(tmp_path, "m.teams.json", doc), name="m.teams.json"))
    body = u.body
    assert body.index("first 👍") < body.index("second thread") < body.index("late reply")
    assert "(edited 2026-09-01T11:00:00Z)" in body
    assert "> **Bob** — 2026-09-01T10:05:00Z\n>\n> [deleted]" in body and "gone" not in body
    assert "(reply to message 999, which is not in this month)" in body
    assert "- plan.docx — <https://contoso.sharepoint.com/plan.docx>" in body
    assert "4 message(s) in 3 thread(s)" in body


def test_teams_batch_equals_per_message_conversion(tmp_path: Path) -> None:
    bodies = [
        "<p>one <b>bold</b></p>",
        "<ul><li>a</li><li>b</li></ul>",
        "<p>unclosed <i>italic",
        "<table><tr><td>x</td></tr></table>",
    ]
    conv = TeamsMonthConverter(CFG)
    batched = conv._bodies(bodies)
    single = [conv._pandoc.html_to_gfm(_prepare_html(b)).strip("\n") for b in bodies]
    assert batched == single


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (teams_doc([], schema="agentsync.teams-month/2"), "unknown schema"),
        ("{not json", "invalid JSON"),
        (
            _raw_month("2026-9", "[]"),
            "YYYY-MM",
        ),
        (
            _raw_month("2026-09", "[{}]"),
            "id",
        ),
        ("[]", "not a JSON object"),
    ],
)
def test_teams_bad_input(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ConversionError, match=match):
        TeamsMonthConverter(CFG).convert(_write(tmp_path, "x.teams.json", text), name="x.teams.json")


def test_teams_empty_month(tmp_path: Path) -> None:
    u = _one(
        TeamsMonthConverter(CFG).convert(_write(tmp_path, "e.teams.json", teams_doc([])), name="e.teams.json")
    )
    assert "[no messages this month]" in u.body


# ---------------------------------------------------------------------------------------------------------
# cross-cutting
# ---------------------------------------------------------------------------------------------------------


def test_every_body_is_nfc_lf_and_single_trailing_newline(fixture_files: dict[str, Path]) -> None:
    reg = Registry.default(CFG)
    for name, path in fixture_files.items():
        conv = reg.for_name(name)
        assert conv is not None, name
        for u in conv.convert(path, name=name):
            assert unicodedata.is_normalized("NFC", u.body), name
            assert "\r" not in u.body and u.body.endswith("\n") and not u.body.endswith("\n\n"), name
            assert u.body.strip() and u.title and u.summary, name
            assert "\n" not in u.title and "\n" not in u.summary, name


def test_zip_fixture_sanity(fixture_files: dict[str, Path]) -> None:
    for name in ("sample.docx", "sample.xlsx", "sample.pptx"):
        assert zipfile.is_zipfile(fixture_files[name])
