"""Every real converter on generated inputs: fidelity, the measured failure modes, unreadable/failed paths."""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
import zipfile
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert import pdf as pdf_mod
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
    build_annotated_pdf,
    build_commented_pdf,
    build_docx_image,
    build_docx_merged,
    build_pdf,
    build_pptx_rich,
    build_xlsx_rich,
    eml_bytes,
    make_zip,
    ole_encrypted,
    pandoc_build,
    pdf_text,
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
    with pytest.raises(UnreadableSourceError, match=r"^encrypted-pdf \(password-protected\)$"):
        PdfConverter(CFG).convert(src, name="locked.pdf")


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _owner_only_encrypted_pdf(path: Path) -> Path:
    """A PDF with /Encrypt (Standard, R2, 40-bit RC4) whose USER password is empty: it opens without a
    password (the permission-restricted "no copy" statement case), yet the trailer names /Encrypt.

    Built from ``build_pdf(encrypt=True)`` by replacing its placeholder /O and /U strings (same lengths, so
    the xref stays valid) with values computed per ISO 32000-1 algorithms 3.2-3.4 for owner "owner", user "".
    """
    import hashlib  # noqa: PLC0415

    pad = bytes.fromhex("28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a")
    file_id = bytes.fromhex("ab" * 16)  # build_pdf's /ID
    owner = _rc4(hashlib.md5((b"owner" + pad)[:32]).digest()[:5], pad)
    perms = (-4).to_bytes(4, "little", signed=True)  # build_pdf's /P -4
    key = hashlib.md5(pad + owner + perms + file_id).digest()[:5]
    user = _rc4(key, pad)
    body = build_pdf(path, [["readable only with the owner's leave"]], encrypt=True).read_bytes()
    body = body.replace(b"/O <" + b"11" * 32 + b">", b"/O <" + owner.hex().encode() + b">")
    body = body.replace(b"/U <" + b"22" * 32 + b">", b"/U <" + user.hex().encode() + b">")
    path.write_bytes(body)
    return path


def test_pdf_with_encrypt_but_empty_user_password_is_still_refused(tmp_path: Path) -> None:
    import pypdfium2  # type: ignore[import-untyped]  # noqa: PLC0415

    src = _owner_only_encrypted_pdf(tmp_path / "restricted.pdf")
    doc = pypdfium2.PdfDocument(str(src))  # PDFium opens it: no password is needed ...
    doc.close()
    with pytest.raises(UnreadableSourceError, match=r"^encrypted-pdf \(/Encrypt in the trailer\)$"):
        PdfConverter(CFG).convert(src, name="restricted.pdf")  # ... but /Encrypt is refused (C15 item 25)


def test_pdf_garbage_is_a_conversion_error(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match="not a PDF"):
        PdfConverter(CFG).convert(_write(tmp_path, "x.pdf", b"hello"), name="x.pdf")
    # PDFium cannot load it either, so the pdfminer fallback runs and reports the damage.
    with pytest.raises(ConversionError, match="pdfminer"):
        PdfConverter(CFG).convert(_write(tmp_path, "y.pdf", b"%PDF-1.4\n1 0 obj << /Broken"), name="y.pdf")


def test_pdf_is_pypdfium2_and_says_so() -> None:
    conv = PdfConverter(CFG)
    assert conv.converter_id == "pdf-pypdfium2"
    assert Registry.default(CFG).for_name("Statement.PDF") is not None
    assert Registry.default(CFG).for_name("Statement.PDF").converter_id == "pdf-pypdfium2"  # type: ignore[union-attr]
    assert re.fullmatch(r"2\.1\.0\+pypdfium2-[\d.]+\+pdfium-[\d.]+\+pdfminer\.six-\S+", conv.version())
    opts = conv.options()
    assert opts["engine"] == "pypdfium2:text-range" and opts["fallback"] == "pdfminer.six"
    assert not any("comment" in key for key in opts)  # the emitter version alone moves the action key


def _pdfium_cannot_load(_src: Path, _name: str) -> tuple[list[str], dict[int, list[object]]]:
    raise pdf_mod._EngineUnavailableError("PDFium cannot load the PDF: Data format error")


def test_pdf_falls_back_to_pdfminer_only_when_pdfium_cannot_load(
    fixture_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = _one(PdfConverter(CFG).convert(fixture_files["sample.pdf"], name="sample.pdf"))
    monkeypatch.setattr(pdf_mod, "_pdfium_pages", _pdfium_cannot_load)
    fallback = _one(PdfConverter(CFG).convert(fixture_files["sample.pdf"], name="sample.pdf"))
    assert fallback.body == primary.body  # same page anchors and text on a simple born-digital PDF
    assert fallback.summary == (
        "PDF: 2 page(s); text by the pdfminer.six fallback (PDFium could not load it); comments not read"
    )
    assert "fallback" not in primary.summary


def test_pdf_without_importable_pypdfium2_uses_pdfminer(
    fixture_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "pypdfium2", None)  # import raises ImportError
    conv = PdfConverter(CFG)
    assert "pypdfium2-unavailable" in conv.version()
    u = _one(conv.convert(fixture_files["sample.pdf"], name="sample.pdf"))
    assert "Budget - page 2" in u.body and "pdfminer.six fallback" in u.summary


def test_pdf_with_no_text_on_any_page_is_unreadable_not_empty(tmp_path: Path) -> None:
    with pytest.raises(UnreadableSourceError, match="no text layer"):
        PdfConverter(CFG).convert(build_pdf(tmp_path / "scan.pdf", [[], []]), name="scan.pdf")
    with pytest.raises(UnreadableSourceError, match="no pages"):
        PdfConverter(CFG).convert(build_pdf(tmp_path / "none.pdf", []), name="none.pdf")


def test_pdf_page_text_cleaning() -> None:
    from agentsync.convert.pdf import _clean  # noqa: PLC0415

    assert _clean("co\x02operate\r\nnext\x0cpage\rend\x00") == "cooperate\nnext\npage\nend"
    assert _clean("Café") == "Café"  # NFC


def test_pdf_conversion_is_deterministic_x3(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    """C15 item 35's determinism half: three conversions, one sha256 (the receipt run is separate)."""
    import hashlib  # noqa: PLC0415

    src = build_pdf(
        tmp_path / "t.pdf", [[f"row {i}: North {i * 3} South {i * 7}" for i in range(30)] for _ in range(3)]
    )
    for pdf in (fixture_files["sample.pdf"], src):
        digests = {
            hashlib.sha256(_one(PdfConverter(CFG).convert(pdf, name=pdf.name)).body.encode()).hexdigest()
            for _ in range(3)
        }
        assert len(digests) == 1


def test_pdf_cap_keeps_the_full_text_in_a_sidecar(tmp_path: Path) -> None:
    src = build_pdf(
        tmp_path / "long.pdf",
        [[f"line {i} of page {p} with some filler text" for i in range(40)] for p in range(20)],
    )
    u = _one(PdfConverter(replace(CFG, max_page_bytes=4000)).convert(src, name="long.pdf"))
    assert len(u.body.encode()) <= 4000
    assert "the full text is in the sidecar `full-text.txt`" in u.body
    assert u.sidecars[0][0] == "full-text.txt" and b"line 39 of page 19" in u.sidecars[0][1]


_COMMENTS_HEAD = "[comments on this page (PDF annotations):]"
_COMMENTED_PDF_BODY = """\
<!-- page: 1 -->

Contoso widget overview
Quarterly totals by region
Draft wording of the summary

[comments on this page (PDF annotations):]
- Highlight by Roe, John on “Contoso widget overview”: Use the Q3 figures here
- Note by Doe, Jane Q: Add units → revenue split by region
  - reply by Roe, John: Agreed
- Underline by Doe, Jane Q on “Quarterly totals by region”
- Insert by Roe, John: Final
  - Strikethrough by Roe, John on “Draft”
- Text box: Typed note: # not a heading - reply by Roe, John: approved

<!-- page: 2 -->

Second page: nothing on it is a comment.

<!-- page: 3 -->

Alpha gyp line above, jq.
Middle TARGET line, ok. a_b
Omega line below

[comments on this page (PDF annotations):]
- Highlight by Doe, Jane Q on “Middle TARGET line, ok. a_b”
- Squiggly underline by Roe, John on “Middle TARGET line, ok. a_b Omega line below”: tighten this
"""


def test_pdf_comments_follow_their_page_text(tmp_path: Path) -> None:
    """Each page's comments sit under its own anchor, one line each, top to bottom; a page whose
    annotations are all skipped (page 2: a link, a hidden note, a no-view highlight, an empty note) gets no
    block.  ``build_commented_pdf`` says which case each line is."""
    u = _one(PdfConverter(CFG).convert(build_commented_pdf(tmp_path / "c.pdf"), name="c.pdf"))
    assert u.body == _COMMENTED_PDF_BODY
    # Comments are counted, not lines of their text: the text box holds a line shaped like a reply.
    assert u.summary == "PDF: 3 page(s); 9 comment(s) on 2 page(s)"
    assert u.title == "Contoso widget overview"


def test_pdf_page_with_only_comments_is_not_refused(tmp_path: Path) -> None:
    """Comments drawn on a scan: no text on any page, yet there is something to read.  A markup that marks
    no text and says nothing (the underline, the strikethrough) is dropped."""
    src = build_commented_pdf(tmp_path / "scan.pdf", text=False)
    u = _one(PdfConverter(CFG).convert(src, name="scan.pdf"))
    assert u.body == (
        f"<!-- page: 1 -->\n\n[scanned page: no text layer]\n\n{_COMMENTS_HEAD}\n"
        "- Highlight by Roe, John: Use the Q3 figures here\n"
        "- Note by Doe, Jane Q: Add units → revenue split by region\n"
        "  - reply by Roe, John: Agreed\n"
        "- Insert by Roe, John: Final\n"
        "- Text box: Typed note: # not a heading - reply by Roe, John: approved\n\n"
        "<!-- page: 2 -->\n\n[scanned page: no text layer]\n\n"
        f"<!-- page: 3 -->\n\n[scanned page: no text layer]\n\n{_COMMENTS_HEAD}\n"
        "- Highlight by Doe, Jane Q: Middle TARGET line, ok. a_b\n"
        "- Squiggly underline by Roe, John: tighten this\n"
    )
    assert u.summary == (
        "PDF: 3 page(s), 3 without a text layer (scanned; OCR not run); 7 comment(s) on 2 page(s)"
    )
    assert u.title == "Untitled PDF"
    # Annotations that are all skipped are not comments: such a file is still refused.
    unseen = "/Subtype /Text /Rect [400 700 420 720] /F 2 /Contents (no viewer shows this)"
    with pytest.raises(UnreadableSourceError, match="no text layer"):
        PdfConverter(CFG).convert(build_annotated_pdf(tmp_path / "h.pdf", [([], [unseen])]), name="h.pdf")


def test_pdf_marked_text_is_chosen_by_character_centre(tmp_path: Path) -> None:
    """On single-spaced text the box a viewer draws overlaps the lines above and below.  PDFium's bounded
    read of that box returns their glyphs too; the quote is the marked line, punctuation included."""
    import pypdfium2  # noqa: PLC0415

    src = build_commented_pdf(tmp_path / "c.pdf")
    doc = pypdfium2.PdfDocument(str(src))
    try:
        textpage = doc[2].get_textpage()
        bounded = str(textpage.get_text_bounded(left=70, bottom=705.4, right=300, top=719.1))
    finally:
        doc.close()
    # The fixture is the hard case.  If a PDFium upgrade makes this fail, its bounded read no longer leaks.
    assert "Middle TARGET line, ok. a_b" in bounded and bounded.strip() != "Middle TARGET line, ok. a_b"
    body = _one(PdfConverter(CFG).convert(src, name="c.pdf")).body
    assert "- Highlight by Doe, Jane Q on “Middle TARGET line, ok. a_b”\n" in body
    # A rectangle that ends inside the line (no quads) quotes the part it covers.
    assert "  - Strikethrough by Roe, John on “Draft”\n" in body


def test_pdf_comments_no_viewer_shows_are_skipped(tmp_path: Path) -> None:
    """Hidden and NoView annotations are left out: a page that listed them would present text no reviewer
    saw as a colleague's comment.  Other flags do not hide a comment; an answer to a skipped one stands
    alone."""
    at = "/Subtype /Text /Rect [400 {y} 420 {top}]"
    annots = [
        at.format(y=700, top=720) + " /F 2 /T (Roe, John) /Contents (hidden instruction)",
        at.format(y=660, top=680) + " /F 32 /T (Roe, John) /Contents (no-view instruction)",
        at.format(y=620, top=640) + " /F 34 /T (Roe, John) /Contents (both flags)",
        at.format(y=580, top=600) + " /F 28 /T (Doe, Jane Q) /Contents (printed, no zoom, no rotate)",
        at.format(y=700, top=720) + " /T (Doe, Jane Q) /IRT {0} /Contents (answer to the hidden one)",
    ]
    src = build_annotated_pdf(tmp_path / "f.pdf", [(["Enough text on this page to count."], annots)])
    u = _one(PdfConverter(CFG).convert(src, name="f.pdf"))
    assert u.body.split(f"{_COMMENTS_HEAD}\n")[1] == (
        "- Note by Doe, Jane Q: answer to the hidden one\n"
        "- Note by Doe, Jane Q: printed, no zoom, no rotate\n"
    )
    assert u.summary == "PDF: 1 page(s); 2 comment(s) on 1 page(s)"


def test_pdf_comment_cannot_leave_its_line(tmp_path: Path) -> None:
    """A comment is one list item whatever line breaks it carries, so text inside it cannot pose as a
    reply, a heading, a rule, a code fence or a page anchor, and the summary counts it once."""
    text = (
        "Fix these:\r- add X\n- reply by Roe, John: approved\r\n# h\x0c<!-- page: 9 -->\x85---"
        "\N{LINE SEPARATOR}```\N{PARAGRAPH SEPARATOR}1. z"
    )
    author = pdf_text("Doe, Jane <!-- page: 8 -->")
    note = f"/Subtype /Text /Rect [400 700 420 720] /T {author} /Contents {pdf_text(text)}"
    src = build_annotated_pdf(tmp_path / "n.pdf", [(["Enough text on this page to count."], [note])])
    u = _one(PdfConverter(CFG).convert(src, name="n.pdf"))
    block = u.body.split(f"{_COMMENTS_HEAD}\n")[1]
    assert block == (
        "- Note by Doe, Jane &lt;!-- page: 8 -->: Fix these: - add X - reply by Roe, John: approved # h "
        "&lt;!-- page: 9 --> --- ``` 1. z\n"
    )
    assert len(block.splitlines()) == 1 and u.body.count("<!-- page:") == 1
    assert u.summary == "PDF: 1 page(s); 1 comment(s) on 1 page(s)"


def test_pdf_comment_text_is_one_line() -> None:
    raw = (
        " a\rb\nc\x0cd\x85e\N{LINE SEPARATOR}f\N{PARAGRAPH SEPARATOR}g\x0bh\x1ci \t\xa0 j "
        "co\x02operate Cafe\N{COMBINING ACUTE ACCENT} "
    )
    assert pdf_mod._one_line(raw) == "a b c d e f g h i j cooperate Café"
    assert pdf_mod._one_line(" \r\n\x0c ") == ""


def test_pdf_shapes_and_stamps_with_text_are_comments(tmp_path: Path) -> None:
    """Every comment subtype has its word; a form field, a redaction or a sound with text is not one."""
    kinds = [
        ("Line", "Line"),
        ("Square", "Box"),
        ("Circle", "Circle"),
        ("Polygon", "Polygon"),
        ("PolyLine", "Polyline"),
        ("Stamp", "Stamp"),
        ("Ink", "Drawing"),
        ("FileAttachment", "Attachment"),
    ]
    annots = [
        f"/Subtype /{subtype} /Rect [100 {700 - 20 * i} 120 {715 - 20 * i}] /Contents (see the {word})"
        for i, (subtype, word) in enumerate(kinds)
    ]
    annots += [
        f"/Subtype /{subtype} /Rect [300 700 320 715] /Contents (not a comment)"
        for subtype in ("Widget", "Redact", "Sound", "Watermark")
    ]
    src = build_annotated_pdf(tmp_path / "k.pdf", [(["Enough text on this page to count."], annots)])
    block = _one(PdfConverter(CFG).convert(src, name="k.pdf")).body.split(f"{_COMMENTS_HEAD}\n")[1]
    assert block.splitlines() == [f"- {word}: see the {word}" for _subtype, word in kinds]


def test_pdf_comment_kinds_are_the_pdfium_subtypes() -> None:
    """The subtype and flag numbers in the converter are PDFium's (fpdf_annot.h)."""
    import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]  # noqa: PLC0415

    words = {
        "TEXT": "Note",
        "FREETEXT": "Text box",
        "LINE": "Line",
        "SQUARE": "Box",
        "CIRCLE": "Circle",
        "POLYGON": "Polygon",
        "POLYLINE": "Polyline",
        "HIGHLIGHT": "Highlight",
        "UNDERLINE": "Underline",
        "SQUIGGLY": "Squiggly underline",
        "STRIKEOUT": "Strikethrough",
        "STAMP": "Stamp",
        "CARET": "Insert",
        "INK": "Drawing",
        "FILEATTACHMENT": "Attachment",
    }
    assert {getattr(pdfium_c, f"FPDF_ANNOT_{name}"): word for name, word in words.items()} == (
        pdf_mod._COMMENT_KINDS
    )
    markup = ("HIGHLIGHT", "UNDERLINE", "SQUIGGLY", "STRIKEOUT")
    assert {getattr(pdfium_c, f"FPDF_ANNOT_{name}") for name in markup} == pdf_mod._TEXT_MARKUP
    assert pdfium_c.FPDF_ANNOT_FLAG_HIDDEN | pdfium_c.FPDF_ANNOT_FLAG_NOVIEW == pdf_mod._UNSEEN_FLAGS


def _comment(
    index: int, parent: int | None = None, *, kind: str = "Note", text: str | None = None, quote: str = ""
) -> pdf_mod._Comment:
    """A comment ``c<index>``; a lower index sits higher on the page."""
    body = f"c{index}" if text is None else text
    return pdf_mod._Comment(
        index=index, kind=kind, author="", text=body, quote=quote, top=-float(index), left=0.0, parent=parent
    )


def test_pdf_comment_threads_keep_every_comment() -> None:
    render = pdf_mod._render_comments
    # Answers nest under their comment in file order; roots run top to bottom.
    thread = [_comment(0), _comment(1), _comment(2, 0), _comment(3, 0), _comment(4, 2)]
    assert render(thread) == ["- Note: c0", "  - reply: c2", "    - reply: c4", "  - reply: c3", "- Note: c1"]
    # Two comments answering each other: no root reaches them, yet both come out, once each.
    assert render([_comment(0, 1), _comment(1, 0)]) == ["- Note: c0", "  - reply: c1"]
    # A parent that is not a comment on this page, and a comment answering itself, stand alone.
    assert render([_comment(0, 7), _comment(1, 1)]) == ["- Note: c0", "- Note: c1"]
    # A chain deeper than the interpreter's recursion limit: every comment, the indent capped at 4 levels.
    chain = render([_comment(i, i - 1 if i else None) for i in range(1200)])
    assert len(chain) == 1200 and chain[:2] == ["- Note: c0", "  - reply: c1"]
    assert chain[4] == "        - reply: c4" and chain[-1] == "        - reply: c1199"


def test_pdf_comment_line_labels_quote_cap_and_repeated_text() -> None:
    render = pdf_mod._render_comments
    # Only a note under another comment is a reply: the strikethrough of a replace-text pair keeps its word.
    pair = [
        _comment(0, kind="Insert", text="new"),
        _comment(1, 0, kind="Strikethrough", text="", quote="old"),
    ]
    assert render(pair) == ["- Insert: new", "  - Strikethrough on “old”"]
    # A tool that copies the marked text into the comment: said once, whatever the quote's length.
    quote = ("word " * 80).strip()
    assert render([_comment(0, kind="Highlight", text="same words", quote="same words")]) == [
        "- Highlight on “same words”"
    ]
    capped = "- Highlight on “" + ("word " * 60).strip() + "…”"
    assert render([_comment(0, kind="Highlight", text=quote, quote=quote)]) == [capped]
    assert render([_comment(0, kind="Highlight", text="why", quote=quote)]) == [capped + ": why"]


@pytest.mark.parametrize(
    ("kind", "failing", "blocks_lost", "summary"),
    [("pdfium", 1, 1, "PDF: 3 page(s); 2 comment(s) on 1 page(s)"), ("other", 3, 2, "PDF: 3 page(s)")],
)
def test_pdf_comment_failure_keeps_the_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: str,
    failing: int,
    blocks_lost: int,
    summary: str,
) -> None:
    """Comments that cannot be read cost a page its comments, not the file its PDFium text: a PdfiumError
    here must not send the file to the pdfminer fallback.  One log line per file, however many pages."""
    import pypdfium2  # noqa: PLC0415

    error: Exception = RuntimeError("boom")
    if kind == "pdfium":
        error = pypdfium2.PdfiumError("Failed to load annotation.")
    real = pdf_mod._page_comments
    seen: list[object] = []

    def first_pages_fail(pdfium_c: object, page: object, textpage: object) -> list[pdf_mod._Comment]:
        seen.append(page)
        if len(seen) <= failing:
            raise error
        return real(pdfium_c, page, textpage)

    monkeypatch.setattr(pdf_mod, "_page_comments", first_pages_fail)
    with caplog.at_level(logging.WARNING, logger="agentsync.convert.pdf"):
        u = _one(PdfConverter(CFG).convert(build_commented_pdf(tmp_path / "c.pdf"), name="c.pdf"))
    block = rf"\n{re.escape(_COMMENTS_HEAD)}\n(?:.+\n)+"  # a page's block and the blank line before it
    assert u.body == re.sub(block, "", _COMMENTED_PDF_BODY, count=blocks_lost)
    assert u.summary == summary
    assert [r.getMessage() for r in caplog.records] == [
        f"c.pdf: comments not read on {failing} page(s), first on page 1: {type(error).__name__}: {error}"
    ]


def test_pdf_fallback_reads_no_comments_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_mod, "_pdfium_pages", _pdfium_cannot_load)
    u = _one(PdfConverter(CFG).convert(build_commented_pdf(tmp_path / "c.pdf"), name="c.pdf"))
    assert "Contoso widget overview" in u.body and "Omega line below" in u.body
    assert "comments on this page" not in u.body and "Agreed" not in u.body
    assert u.summary == (
        "PDF: 3 page(s); text by the pdfminer.six fallback (PDFium could not load it); comments not read"
    )


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
    assert "Attachments are listed, not converted; save one into the inbox to convert it." in u.body
    assert "second phase" not in u.body
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


@pytest.mark.parametrize(
    "content_type", ["text/plain; charset=utf-8", "text/plain", "text/plain; charset=iso-8859-1"]
)
def test_eml_cp1252_body_mislabelled_or_unlabelled_keeps_quotes_and_accents(
    tmp_path: Path, content_type: str
) -> None:
    body = "\u201cCaf\u00e9 menu\u201d is final \u2013 Ren\u00e9e\n".encode("cp1252")
    raw = (
        b"From: a@example.com\r\nSubject: menu\r\nMIME-Version: 1.0\r\n"
        + f"Content-Type: {content_type}\r\n".encode()
        + b"Content-Transfer-Encoding: 8bit\r\n\r\n"
        + body
    )
    u = _one(EmlConverter(CFG).convert(_write(tmp_path, "c.eml", raw), name="c.eml"))
    assert "\u201cCaf\u00e9 menu\u201d is final \u2013 Ren\u00e9e" in u.body
    assert "\ufffd" not in u.body


def test_eml_other_declared_charset_still_decodes_by_its_label(tmp_path: Path) -> None:
    raw = (
        b"From: a@example.com\r\nSubject: menu\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=koi8-r\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
        + "\u041f\u0440\u0438\u0432\u0435\u0442\n".encode("koi8-r")
    )
    u = _one(EmlConverter(CFG).convert(_write(tmp_path, "k.eml", raw), name="k.eml"))
    assert "\u041f\u0440\u0438\u0432\u0435\u0442" in u.body


@pytest.mark.parametrize(
    ("label", "body", "kept"),
    [
        # Superset bytes under the legacy label: GBK under gb2312, cp932 under shift_jis, and so on.
        (
            "gb2312",
            "\u6731\u9555\u57fa \u8bf4 \u4f60\u597d\n".encode("gbk"),
            "\u6731\u9555\u57fa \u8bf4 \u4f60\u597d",
        ),
        (
            "shift_jis",
            "\u2460 \u4f1a\u8b70\u306f\u660e\u65e5\u3067\u3059\n".encode("cp932"),
            "\u4f1a\u8b70\u306f\u660e\u65e5\u3067\u3059",
        ),
        (
            "euc-kr",
            "\uac02 \uc548\ub155\ud558\uc138\uc694\n".encode("cp949"),
            "\uc548\ub155\ud558\uc138\uc694",
        ),
        ("big5", "\u7881 \u4f60\u597d\u4e16\u754c\n".encode("cp950"), "\u4f60\u597d\u4e16\u754c"),
        # A byte no codepage of the label maps costs one replacement, not the whole body.
        (
            "euc-kr",
            "\uc548\ub155 ".encode("euc-kr") + b"\xff" + "\uc138\uc694\n".encode("euc-kr"),
            "\uc548\ub155",
        ),
    ],
)
def test_eml_cjk_label_keeps_its_text_past_an_out_of_label_byte(
    tmp_path: Path, label: str, body: bytes, kept: str
) -> None:
    raw = (
        b"From: a@example.com\r\nSubject: memo\r\nMIME-Version: 1.0\r\n"
        + f"Content-Type: text/plain; charset={label}\r\n".encode()
        + b"Content-Transfer-Encoding: 8bit\r\n\r\n"
        + body
    )
    u = _one(EmlConverter(CFG).convert(_write(tmp_path, "j.eml", raw), name="j.eml"))
    assert kept in u.body
    assert not any(ch in u.body for ch in "\u00d6\u00c4\u00e4\u2030\u2021")  # no cp1252/latin-1 mojibake


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
