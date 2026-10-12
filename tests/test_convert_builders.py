"""Builders for converter test inputs (generated at test time; nothing binary is committed).

Imported by the other ``test_convert_*`` modules; the two tests at the bottom only check the builders.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import struct
import subprocess
import zipfile
import zlib
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any, NamedTuple

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from agentsync.convert.ocr import OcrEngine
from agentsync.convert.pandoc import _bundled_pandoc
from test_ocr import fake_engine

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def png_bytes() -> bytes:
    """A valid 1x1 RGB PNG (no tIME chunk, so byte-stable)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def pandoc_build(text: str, src_fmt: str, out: Path, *, cwd: Path | None = None) -> Path:
    """Write ``out`` from ``text`` with the bundled pandoc (the writer is chosen by the out suffix)."""
    subprocess.run(
        [str(_bundled_pandoc()), "-f", src_fmt, "-s", "-o", str(out)],
        input=text.encode("utf-8"),
        check=True,
        cwd=str(cwd or out.parent),
        capture_output=True,
    )
    return out


def build_docx_image(dest: Path) -> Path:
    """A docx with a figure (alt text) and an inline image."""
    (dest / "pic.png").write_bytes(png_bytes())
    md = "# Doc\n\n![a chart](pic.png)\n\nText ![inline](pic.png) here.\n"
    return pandoc_build(md, "markdown", dest / "image.docx", cwd=dest)


def build_docx_merged(dest: Path) -> Path:
    """A docx whose table has a merged (gridSpan) cell (design C13 fixture shape)."""
    html = (
        "<h1>Scope</h1><table><thead><tr><th>Region</th><th>Spend</th><th>Total</th></tr></thead><tbody>"
        '<tr><td colspan="2">West (merged)</td><td>300</td></tr><tr><td>East</td><td>5</td><td>6</td></tr>'
        "</tbody></table><h2>Notes</h2><p>closing paragraph</p>"
    )
    return pandoc_build(html, "html", dest / "merged.docx")


def make_zip(
    path: Path, parts: dict[str, bytes], *, date: tuple[int, int, int, int, int, int] | None = None
) -> Path:
    """Write a ZIP with exactly ``parts`` (insertion order), optionally stamping every entry's date."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            info = zipfile.ZipInfo(name, date_time=date or (2026, 9, 1, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


def ole_encrypted(path: Path) -> Path:
    """Bytes shaped like an Office-encrypted package: OLE header + an EncryptedPackage directory entry."""
    body = OLE_MAGIC + b"\x00" * 504 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 1024
    path.write_bytes(body)
    return path


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(path: Path, pages: list[list[str]], *, encrypt: bool = False) -> Path:
    """A minimal born-digital PDF (Helvetica) with a correct xref; ``encrypt`` adds a Standard security
    handler whose user password is not empty (so opening needs a password)."""
    objs: list[bytes] = []
    n_pages = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
        content += [f"({_pdf_escape(line)}) Tj T*" for line in lines]
        content.append("ET")
        stream = "\n".join(content).encode()
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    if encrypt:
        objs.append(
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -4 "
            b"/O <" + b"11" * 32 + b"> /U <" + b"22" * 32 + b"> >>"
        )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    extra = ""
    if encrypt:
        extra = f" /Encrypt {len(objs)} 0 R /ID [<{'ab' * 16}> <{'ab' * 16}>]"
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R{extra} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def pdf_text(s: str) -> str:
    """A PDF text string: a literal when printable ASCII, else UTF-16BE with a BOM in hex (how a viewer
    writes a comment that has an arrow, an accent or a line break in it)."""
    if s.isascii() and s.isprintable():
        return f"({_pdf_escape(s)})"
    return f"<FEFF{s.encode('utf-16-be').hex().upper()}>"


def build_annotated_pdf(path: Path, pages: list[tuple[list[str], list[str]]], *, leading: int = 12) -> Path:
    """A born-digital PDF whose pages carry annotations.

    A page is ``(text lines, annotation dictionary bodies)``.  Text is 12 pt Helvetica from x = 72; line
    ``i`` has its baseline at y = 720 - leading * i, and PDFium's loose character box runs from 2.5 below
    the baseline to 10.9 above it.  The default leading is single spacing, where a marked line touches its
    neighbours.  ``{N}`` in a body is the reference to annotation ``N`` of the same page (/IRT, /Popup).
    """
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, written once the page numbers are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids: list[str] = []
    for lines, annots in pages:
        page_no = len(objs) + 1
        refs = [f"{page_no + 2 + i} 0 R" for i in range(len(annots))]
        ops = ["BT", "/F1 12 Tf", "72 720 Td", f"{leading} TL"]
        ops += [f"({_pdf_escape(line)}) Tj T*" for line in lines]
        stream = "\n".join([*ops, "ET"]).encode()
        kids.append(f"{page_no} 0 R")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_no + 1} 0 R /Annots [{' '.join(refs)}] >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objs += [f"<< /Type /Annot {body.format(*refs)} >>".encode() for body in annots]
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def pdf_line_quad(line: int, *, left: int = 70, right: int = 300) -> str:
    """``/Rect`` and ``/QuadPoints`` of a text markup over line ``line`` of a ``build_annotated_pdf`` page
    (single spacing), from x = ``left`` to ``right``: the box a viewer draws, a little taller than the
    font, so it overlaps the lines above and below."""
    low, high = 720 - 12 * line - 2.6, 720 - 12 * line + 11.1
    return (
        f"/Rect [{left} {low:.1f} {right} {high:.1f}] "
        f"/QuadPoints [{left} {high:.1f} {right} {high:.1f} {left} {low:.1f} {right} {low:.1f}]"
    )


def build_commented_pdf(path: Path, *, text: bool = True) -> Path:
    """Three single-spaced pages for the PDF comment tests; ``text=False`` leaves every page without text
    (comments drawn on a scan).

    Page 1 has each case the converter keeps: a note with a reply, a highlight with a comment, an underline
    without one, a typed text box of several lines, and a replace-text pair (an insert with the
    strikethrough grouped under it); and each it skips: the note's popup, a link, a drawing without text.
    Page 2 has only annotations that are skipped.  Page 3 has a highlight whose comment repeats the marked
    line and a squiggly underline over two lines.
    """
    doe, roe = "/T (Doe, Jane Q)", "/T (Roe, John)"
    typed = "Typed note:\r# not a heading\r- reply by Roe, John: approved"
    page1 = [
        f"/Subtype /Text /Rect [400 700 420 720] /F 4 {doe} /Popup {{1}} "
        f"/Contents {pdf_text('Add units → revenue split by region')}",
        "/Subtype /Popup /Rect [420 600 600 700] /Parent {0} /Contents (popup copy of the note)",
        f"/Subtype /Highlight {pdf_line_quad(0)} {roe} /Contents (Use the Q3 figures here)",
        f"/Subtype /FreeText /Rect [72 500 300 540] /DA (/Helv 12 Tf 0 g) /Contents {pdf_text(typed)}",
        f"/Subtype /Text /Rect [400 700 420 720] {roe} /IRT {{0}} /Contents (Agreed)",
        "/Subtype /Link /Rect [72 590 200 610] /Contents (a link is not a comment) "
        "/A << /S /URI /URI (https://example.com) >>",
        "/Subtype /Ink /Rect [300 300 350 350] /InkList [[300 300 350 350]]",
        f"/Subtype /Underline {pdf_line_quad(1)} {doe}",
        f"/Subtype /Caret /Rect [99 693.4 103 707.1] {roe} /Contents (Final)",
        # No /QuadPoints: the rectangle marks the text.  It ends in the space after "Draft" (x = 98 to
        # 101.3), 4.7 pt from the centre of the letter on either side, so the quote does not hang on the
        # last decimal of a font metric.
        f"/Subtype /StrikeOut /Rect [70 693.4 101 707.1] {roe} /IRT {{8}} /RT /Group",
    ]
    page2 = [
        "/Subtype /Link /Rect [72 700 200 732] /A << /S /URI /URI (https://example.com) >>",
        f"/Subtype /Text /Rect [400 700 420 720] /F 2 {doe} /Contents (hidden: no viewer shows this)",
        f"/Subtype /Highlight {pdf_line_quad(0)} /F 32 {doe} /Contents (not shown on screen)",
        f"/Subtype /Text /Rect [400 600 420 620] {roe}",
    ]
    marked = "Middle TARGET line, ok. a_b"
    two_lines = "/QuadPoints [70 719.1 300 719.1 70 705.4 300 705.4 70 707.1 300 707.1 70 693.4 300 693.4]"
    page3 = [
        f"/Subtype /Highlight {pdf_line_quad(1)} {doe} /Contents ({marked})",
        f"/Subtype /Squiggly /Rect [70 693.4 300 719.1] {two_lines} {roe} /Contents (tighten this)",
    ]
    lines = [
        ["Contoso widget overview", "Quarterly totals by region", "Draft wording of the summary"],
        ["Second page: nothing on it is a comment."],
        ["Alpha gyp line above, jq.", marked, "Omega line below"],
    ]
    pages = [(ln if text else [], annots) for ln, annots in zip(lines, (page1, page2, page3), strict=True)]
    return build_annotated_pdf(path, pages)


class PdfPicture(NamedTuple):
    """One picture of a ``build_picture_pdf`` page: an 8-bit gray image whose every pixel is ``shade``.

    ``matrix`` is the PDF matrix it is drawn with (``"a b c d e f"``: a wide and d high from (e, f) when b
    and c are 0).  ``px`` is its stored size.  With ``form`` it is drawn inside a form XObject that is itself
    placed with that matrix.  ``pixels``, when given, are its gray pixels row by row in place of the one
    shade (a real picture of text, for the real helper).  Pictures of one shade, size and pixels are one
    image object."""

    shade: int
    matrix: str
    px: tuple[int, int] = (96, 64)
    form: str = ""
    pixels: bytes = b""


PICTURE_PAGE = (300, 200)
"""The page size of ``build_picture_pdf``, in points: small, so a page renders fast at 300 dpi."""


def page_picture(shade: int) -> PdfPicture:
    """A picture that covers a whole ``build_picture_pdf`` page: what a scanner writes."""
    return PdfPicture(shade, f"{PICTURE_PAGE[0]} 0 0 {PICTURE_PAGE[1]} 0 0")


def build_picture_pdf(
    path: Path, pages: list[tuple[list[str], list[PdfPicture]]], *, rotate: int = 0
) -> Path:
    """A PDF whose pages hold text and pictures, for the OCR tests.  With ``rotate`` every page has that
    /Rotate (degrees clockwise), which a viewer applies.

    A page is ``(text lines, pictures)``, ``PICTURE_PAGE`` points in size.  Text is 12 pt Helvetica from
    x = 20; line ``i`` has its baseline at y = 180 - 14 * i.  A page with no text line and a ``page_picture``
    is a scanned page.  Rendered, such a page is the picture's shade from corner to corner, and a page with
    no picture is white: the fake OCR helper of the converter tests tells images apart by that."""
    width, height = PICTURE_PAGE
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, written once the page numbers are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    images: dict[tuple[int, tuple[int, int], bytes], int] = {}
    for _lines, pictures in pages:
        for pic in pictures:
            if (pic.shade, pic.px, pic.pixels) not in images:
                data = zlib.compress(pic.pixels or bytes([pic.shade]) * (pic.px[0] * pic.px[1]))
                objs.append(
                    f"<< /Type /XObject /Subtype /Image /Width {pic.px[0]} /Height {pic.px[1]} "
                    f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                    f"/Length {len(data)} >>\nstream\n".encode()
                    + data
                    + b"\nendstream"
                )
                images[pic.shade, pic.px, pic.pixels] = len(objs)
    kids: list[str] = []
    for lines, pictures in pages:
        ops = ["BT", "/F1 12 Tf", "20 180 Td", "14 TL"]
        ops += [f"({_pdf_escape(line)}) Tj T*" for line in lines]
        ops.append("ET")
        xobjects: list[str] = []
        for n, pic in enumerate(pictures):
            image = images[pic.shade, pic.px, pic.pixels]
            if pic.form:  # the picture is the form's content; the form is what the page draws
                inner = f"q {pic.matrix} cm /Im Do Q".encode()
                head = (
                    f"<< /Type /XObject /Subtype /Form /BBox [0 0 {width} {height}] "
                    f"/Resources << /XObject << /Im {image} 0 R >> >> /Length {len(inner)} >>"
                )
                objs.append(head.encode() + b"\nstream\n" + inner + b"\nendstream")
                image, matrix = len(objs), pic.form
            else:
                matrix = pic.matrix
            xobjects.append(f"/X{n} {image} 0 R")
            ops.append(f"q {matrix} cm /X{n} Do Q")
        stream = "\n".join(ops).encode()
        page_no = len(objs) + 1
        kids.append(f"{page_no} 0 R")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /Rotate {rotate} /MediaBox [0 0 {width} {height}] "
            "/Resources << /Font "
            f"<< /F1 3 0 R >> /XObject << {' '.join(xobjects)} >> >> /Contents {page_no + 1} 0 R >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


# The fake OCR helper of tests/test_ocr.py reads what a test wrote into an image file.  A page PDFium rendered
# and a picture it decoded hold only pixels, so this hook tells images apart by the gray of their first
# pixel and answers from a table beside the helper.
SHADE_HOOK = """\
if "--" in args:
    import struct, zlib
    table = json.loads(Path(sys.argv[0] + ".shades").read_text())
    results = []
    for index, p in enumerate(args[args.index("--") + 1:]):
        data = Path(p).read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        said = table.get(str(zlib.decompressobj().decompress(data[data.index(b"IDAT") + 4:], 2)[1]), [])
        if said == "fail":
            sys.exit(3)
        item = {"index": index, "frame": 0, "frames": 1, "width": width, "height": height, "lines": [],
                "skipped": False}
        if isinstance(said, dict):
            item.update(said)
        else:
            item["lines"] = [{"text": t, "confidence": 1.0, "x": 0.1, "y": 0.1 + 0.06 * n, "w": 0.6,
                              "h": 0.05} for n, t in enumerate(said)]
        results.append(item)
    print(json.dumps({"results": results}))
    sys.exit(0)
"""


def shade_engine(folder: Path, said: Mapping[int, Any]) -> OcrEngine:
    """An OCR engine for ``build_picture_pdf`` files: its fake helper 'reads' a PNG by the shade of its
    first pixel.  ``said`` maps a shade to the lines read there, to the fields of an image the helper
    reports instead (``{"error": "recognition failed"}``, ``{"skipped": True}``), or to ``"fail"``, which
    makes a run that holds such an image exit 3.  A shade it does not name has no text.  255 is a page with
    no picture on it."""
    engine = fake_engine(folder)
    text = engine.helper.read_text(encoding="utf-8")
    at = 'if "--version" in args:'
    assert at in text
    engine.helper.write_text(text.replace(at, SHADE_HOOK + at, 1), encoding="utf-8")
    Path(str(engine.helper) + ".shades").write_text(json.dumps(said), encoding="utf-8")
    return engine


def build_xlsx_rich(path: Path, *, rows: int = 10) -> Path:
    """Workbook with formulas (no cached values), a comment, a chart, a hidden sheet, a defined name, odd
    values (integral float, date, bool, pipe/newline text) and ``rows`` data rows."""

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Data"
    ws.append(["Item", "Qty", "Price", "Total", "Misc"])
    for i in range(rows):
        r = i + 2
        ws.append([f"item-{i:03d}", i + 1, 2.5, f"=B{r}*C{r}", None])
    ws["E2"] = 3.0
    ws["E3"] = dt.datetime(2026, 9, 1)
    ws["E4"] = True
    ws["E5"] = "pipe|text\nsecond line"
    ws["B2"].comment = Comment("check this quantity", "Jane")
    chart = BarChart()
    chart.title = "Qty by item"
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=rows + 1), titles_from_data=True)
    ws.add_chart(chart, "G2")
    hidden = wb.create_sheet("Secret")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "hidden value"
    wb.defined_names.add(DefinedName("TaxRate", attr_text="Data!$C$2"))
    wb.save(path)
    return path


def with_cached_values(src: Path, dest: Path, sheet_part: str, values: dict[str, str]) -> Path:
    """Copy an openpyxl workbook, inserting ``<v>`` cached results for the given formula cells (what Excel
    writes on save; openpyxl never does)."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == sheet_part:
                text = data.decode("utf-8")
                for ref, value in values.items():
                    pattern = re.compile(rf'(<c r="{ref}"[^>]*>)(<f>[^<]*</f>)(<v\s*/>|<v></v>)?')
                    text, n = pattern.subn(lambda m, v=value: f"{m.group(1)}{m.group(2)}<v>{v}</v>", text)
                    assert n == 1, f"formula cell {ref} not found in {sheet_part}"
                data = text.encode("utf-8")
            zout.writestr(info, data)
    return dest


def build_pptx_rich(path: Path) -> Path:
    """Deck: text boxes out of XML order, a table with a merged cell, a picture with alt text, a chart, a
    group shape, a hidden slide."""

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    assert s1.shapes.title is not None
    s1.shapes.title.text = "Numbers"
    right = s1.shapes.add_textbox(Inches(5), Inches(2), Inches(2), Inches(1))
    right.text_frame.text = "RIGHT box"
    left = s1.shapes.add_textbox(Inches(1), Inches(2), Inches(2), Inches(1))
    left.text_frame.text = "LEFT box"
    table = s1.shapes.add_table(3, 2, Inches(1), Inches(3), Inches(4), Inches(1.5)).table
    for (r, c), text in {
        (0, 0): "k",
        (0, 1): "v",
        (1, 0): "rate",
        (1, 1): "4.2%",
        (2, 0): "merged row",
    }.items():
        table.cell(r, c).text = text
    table.cell(2, 0).merge(table.cell(2, 1))
    pic = s1.shapes.add_picture(io.BytesIO(png_bytes()), Inches(8), Inches(5))
    pic._element.nvPicPr.cNvPr.set("descr", "Company logo")
    data = CategoryChartData()
    data.categories = ["Q1", "Q2"]
    data.add_series("Revenue", (10.0, 12.5))
    s1.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(5), Inches(4), Inches(2), data)
    s2 = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    group = s2.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "inside group"
    s3 = prs.slides.add_slide(prs.slide_layouts[1])
    assert s3.shapes.title is not None
    s3.shapes.title.text = "Hidden one"
    s3._element.set("show", "0")
    prs.save(str(path))
    return path


PPTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
POTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"


def build_potx(path: Path, title: str = "Contoso Quarterly Template") -> Path:
    """A PowerPoint template: a one-slide deck whose main part has the template content type, as PowerPoint
    saves a ``.potx`` (python-pptx refuses to open it as it is)."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    assert slide.shapes.title is not None
    slide.shapes.title.text = title
    deck = io.BytesIO()
    prs.save(deck)
    with zipfile.ZipFile(deck) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "[Content_Types].xml":
                assert PPTX_MAIN.encode() in data
                data = data.replace(PPTX_MAIN.encode(), POTX_MAIN.encode())
            zout.writestr(info, data)
    return path


def eml_bytes(
    *,
    subject: str = "Hello",
    plain: str | None = "Body text.\n",
    html: str | None = None,
    attachments: tuple[tuple[str, bytes, str], ...] = (),
) -> bytes:
    """An RFC 822 message with optional plain/html alternatives and attachments."""
    msg = EmailMessage()
    msg["From"] = "Jane Doe <jane.doe@example.com>"
    msg["To"] = "Team <team@example.com>"
    msg["Cc"] = "Boss <boss@example.com>"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(dt.datetime(2026, 9, 14, 9, 12, tzinfo=dt.UTC))
    msg["Message-ID"] = "<m1@example.com>"
    msg["In-Reply-To"] = "<m0@example.com>"
    msg["References"] = "<m0@example.com>"
    if plain is not None:
        msg.set_content(plain)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    elif html is not None:
        msg.set_content(html, subtype="html")
    for filename, data, ctype in attachments:
        maintype, subtype = ctype.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return bytes(msg)


def teams_doc(messages: list[dict[str, object]], *, schema: str = "agentsync.teams-month/1") -> str:
    """A TEAMS_MONTH_SCHEMA JSON document."""

    doc = {
        "schema": schema,
        "team_id": "team-1",
        "team_name": "Acme",
        "channel_id": "19:abc@thread.tacv2",
        "channel_name": "General",
        "month": "2026-09",
        "messages": messages,
    }
    return json.dumps(doc, sort_keys=True, indent=1) + "\n"


def teams_msg(mid: str, created: str, body: str, **kw: object) -> dict[str, object]:
    """One message dict with defaults."""
    msg: dict[str, object] = {
        "id": mid,
        "reply_to_id": None,
        "created": created,
        "last_modified": created,
        "from": "Jane Doe",
        "subject": None,
        "body_html": body,
        "deleted": False,
        "attachments": [],
    }
    msg.update(kw)
    return msg


def test_builders_produce_valid_containers(tmp_path: Path) -> None:
    assert zipfile.is_zipfile(build_docx_image(tmp_path))
    assert zipfile.is_zipfile(build_docx_merged(tmp_path))
    assert zipfile.is_zipfile(build_xlsx_rich(tmp_path / "r.xlsx"))
    assert zipfile.is_zipfile(build_pptx_rich(tmp_path / "r.pptx"))
    assert build_pdf(tmp_path / "a.pdf", [["x"]]).read_bytes().startswith(b"%PDF-1.4")
    logo = PdfPicture(40, "96 0 0 64 150 20")
    pictured = build_picture_pdf(tmp_path / "p.pdf", [(["x"], [logo]), ([], [logo, page_picture(90)])])
    assert pictured.read_bytes().startswith(b"%PDF-1.6") and pictured.read_bytes().count(b"/Image") == 2
    commented = build_commented_pdf(tmp_path / "c.pdf").read_bytes()
    assert commented.startswith(b"%PDF-1.6") and commented.count(b"/Type /Annot") == 16
    assert (
        b") Tj" in commented
        and b") Tj" not in build_commented_pdf(tmp_path / "s.pdf", text=False).read_bytes()
    )


def test_png_is_stable() -> None:
    assert png_bytes() == png_bytes()
    assert png_bytes().startswith(b"\x89PNG")


_CFB_FREE, _CFB_END, _CFB_FATSECT, _CFB_NONE = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
_CFB_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def _cfb_sorted(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: (len(n), n.upper()))


def cfb_bytes(streams: Mapping[str, bytes]) -> bytes:
    """A compound file (MS-CFB version 3) holding ``streams`` ("storage/…/stream" -> bytes); storages implied.

    Streams under 4096 bytes go in the mini stream, as Outlook writes them.  Siblings are a right-leaning
    chain, all black: enough for olefile, which walks the tree without checking its balance."""
    tree: dict[tuple[str, ...], list[str]] = {(): []}
    for path in streams:
        parts = tuple(path.split("/"))
        for depth in range(1, len(parts) + 1):
            siblings = tree.setdefault(parts[: depth - 1], [])
            if parts[depth - 1] not in siblings:
                siblings.append(parts[depth - 1])
    order: list[tuple[str, ...]] = [()]

    def walk(node: tuple[str, ...]) -> None:
        for name in _cfb_sorted(tree.get(node, [])):
            order.append((*node, name))
            walk((*node, name))

    walk(())
    sid = {node: i for i, node in enumerate(order)}
    mini, minifat, starts = bytearray(), [], {}
    big = [(path, data) for path, data in streams.items() if len(data) >= 4096]
    for path, data in streams.items():
        if len(data) >= 4096:
            continue
        n = -(-len(data) // 64)
        starts[path] = len(minifat) if n else _CFB_END
        minifat += [len(minifat) + i + 1 for i in range(n - 1)] + ([_CFB_END] if n else [])
        mini += data + b"\0" * (n * 64 - len(data))

    def sectors(size: int) -> int:
        return -(-size // 512)

    n_minifat, n_dir, n_mini = sectors(len(minifat) * 4), sectors(len(order) * 128), sectors(len(mini))
    n_big = sum(sectors(len(data)) for _path, data in big)
    n_fat = 1
    while n_fat * 128 < n_fat + n_minifat + n_dir + n_mini + n_big:
        n_fat += 1
    assert n_fat <= 109, "a header-only FAT list: no DIFAT sector is written"
    fat: list[int] = [_CFB_FATSECT] * n_fat

    def chain(n: int) -> int:
        if n == 0:
            return _CFB_END
        first = len(fat)
        fat.extend([first + i + 1 for i in range(n - 1)] + [_CFB_END])
        return first

    minifat_start, dir_start, mini_start = chain(n_minifat), chain(n_dir), chain(n_mini)
    for path, data in big:
        starts[path] = chain(sectors(len(data)))
    fat += [_CFB_FREE] * (n_fat * 128 - len(fat))
    entries = bytearray()
    for node in order:
        path = "/".join(node)
        is_stream = path in streams
        kids = [] if is_stream else _cfb_sorted(tree.get(node, []))
        siblings = _cfb_sorted(tree[node[:-1]]) if node else []
        nxt = siblings.index(node[-1]) + 1 if node else 0
        right = sid[(*node[:-1], siblings[nxt])] if node and nxt < len(siblings) else _CFB_NONE
        child = sid[(*node, kids[0])] if kids else _CFB_NONE
        name = (node[-1] if node else "Root Entry").encode("utf-16-le")
        kind = 2 if is_stream else (1 if node else 5)
        if is_stream:
            start, size = starts[path], len(streams[path])
        elif node:
            start, size = 0, 0
        else:
            start, size = (mini_start if mini else _CFB_END), len(mini)
        entries += struct.pack(
            "<64sHBBIII16sIQQIQ",
            name,
            len(name) + 2,
            kind,
            1,
            _CFB_NONE,
            right,
            child,
            b"\0" * 16,
            0,
            0,
            0,
            start,
            size,
        )
    entries += b"\0" * (n_dir * 512 - len(entries))
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII",
        _CFB_MAGIC,
        b"\0" * 16,
        0x3E,
        3,
        0xFFFE,
        9,
        6,
        b"\0" * 6,
        0,
        n_fat,
        dir_start,
        0,
        4096,
        minifat_start,
        n_minifat,
        _CFB_END,
        0,
    ) + struct.pack("<109I", *(list(range(n_fat)) + [_CFB_FREE] * (109 - n_fat)))
    body = bytearray(struct.pack(f"<{len(fat)}I", *fat))
    body += struct.pack(f"<{len(minifat)}I", *minifat) + b"\xff" * (n_minifat * 512 - len(minifat) * 4)
    body += entries + mini + b"\0" * (n_mini * 512 - len(mini))
    for _path, data in big:
        body += data + b"\0" * (sectors(len(data)) * 512 - len(data))
    return header + bytes(body)


def _msg_props(header: int, fixed: Mapping[int, tuple[int, int]]) -> bytes:
    """A ``__properties_version1.0`` stream: ``header`` bytes, then a 16-byte entry per id: (type, value)."""
    out = bytearray(header)
    for pid, (ptype, value) in sorted(fixed.items()):
        out += struct.pack("<IIQ", (pid << 16) | ptype, 6, value)
    return bytes(out)


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


MsgAttachment = tuple[str, "bytes | Mapping[str, bytes]", str]
"""(file name, content, MIME tag). Content that is bytes is a file by value; a mapping is the streams of the
attachment's object storage: an attached message when it holds a properties stream, else an OLE object."""


def msg_bytes(
    *,
    subject: str = "Hello",
    plain: str | None = "Body text.\n",
    html: str | None = None,
    message_class: str = "IPM.Note",
    attachments: tuple[MsgAttachment, ...] = (),
    sent: dt.datetime = dt.datetime(2026, 9, 14, 9, 12, tzinfo=dt.UTC),
    representing: tuple[str, str] | None = None,
    display_names: Mapping[int, str] | None = None,
) -> bytes:
    """An Outlook .msg (MS-OXMSG, Unicode properties), the shape of ``eml_bytes``: one To, one Cc recipient.

    The sender is Jane Doe; ``representing`` is the (name, address) the message was sent on behalf of.
    ``display_names`` maps an attachment's index to its display name (0x3001), which Outlook sets on an
    attached message to that message's subject."""
    filetime = int((sent - dt.datetime(1601, 1, 1, tzinfo=dt.UTC)).total_seconds() * 10**7)
    s: dict[str, bytes] = {
        "__properties_version1.0": _msg_props(32, {0x0039: (0x0040, filetime)}),
        "__substg1.0_001A001F": _utf16(message_class),
        "__substg1.0_0037001F": _utf16(subject),
        "__substg1.0_0C1A001F": _utf16("Jane Doe"),
        "__substg1.0_5D01001F": _utf16("jane.doe@example.com"),
        "__substg1.0_0E04001F": _utf16("Team"),
        "__substg1.0_0E03001F": _utf16("Boss"),
        "__substg1.0_1035001F": _utf16("<m1@example.com>"),
        "__substg1.0_1042001F": _utf16("<m0@example.com>"),
        "__substg1.0_1039001F": _utf16("<m0@example.com>"),
        "__nameid_version1.0/__substg1.0_00020102": b"",
    }
    if representing is not None:
        s["__substg1.0_0042001F"] = _utf16(representing[0])
        s["__substg1.0_5D02001F"] = _utf16(representing[1])
    for i, (name, addr, kind) in enumerate(
        (("Team", "team@example.com", 1), ("Boss", "boss@example.com", 2))
    ):
        r = f"__recip_version1.0_#{i:08X}/"
        s[r + "__properties_version1.0"] = _msg_props(8, {0x0C15: (0x0003, kind)})
        s[r + "__substg1.0_3001001F"] = _utf16(name)
        s[r + "__substg1.0_39FE001F"] = _utf16(addr)
    if plain is not None:
        s["__substg1.0_1000001F"] = _utf16(plain)
    if html is not None:
        s["__substg1.0_10130102"] = html.encode("utf-8")
    for i, (filename, content, ctype) in enumerate(attachments):
        a = f"__attach_version1.0_#{i:08X}/"
        if isinstance(content, bytes):
            method = 1
            s[a + "__substg1.0_37010102"] = content
        else:
            method = 5 if "__properties_version1.0" in content else 6
            for inner, data in content.items():
                s[f"{a}__substg1.0_3701000D/{inner}"] = data
        s[a + "__properties_version1.0"] = _msg_props(8, {0x3705: (0x0003, method)})
        if filename:
            s[a + "__substg1.0_3707001F"] = _utf16(filename)
        if ctype:
            s[a + "__substg1.0_370E001F"] = _utf16(ctype)
        if display_names and i in display_names:
            s[a + "__substg1.0_3001001F"] = _utf16(display_names[i])
    return cfb_bytes(s)


def test_a_built_msg_is_a_compound_file_olefile_reads_back() -> None:
    import olefile  # noqa: PLC0415

    big = bytes(range(256)) * 40
    data = msg_bytes(attachments=(("deck.bin", big, "application/octet-stream"),))
    assert data == msg_bytes(attachments=(("deck.bin", big, "application/octet-stream"),))
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        assert ole.openstream("__substg1.0_0037001F").read() == _utf16("Hello")
        assert ole.openstream(["__attach_version1.0_#00000000", "__substg1.0_37010102"]).read() == big


DRAWIO_MODEL = (
    '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="2" value="Contoso API" style="rounded=1;" vertex="1" parent="1"/>'
    '<mxCell id="3" value="&lt;b&gt;Billing&lt;/b&gt;&lt;br&gt;service" style="html=1;" '
    'vertex="1" parent="1"/>'
    '<mxCell id="4" value="calls" edge="1" source="2" target="3" parent="1"/>'
    '<mxCell id="6" value="nightly" style="edgeLabel;html=1;" vertex="1" parent="4"/>'
    '<UserObject label="Fabrikam gateway" link="https://contoso.example/gw" id="5">'
    '<mxCell style="" vertex="1" parent="1"/></UserObject>'
    '<mxCell id="7" value="# not a heading" vertex="1" parent="1"/>'
    "</root></mxGraphModel>"
)
"""One draw.io page: two plain shapes, an HTML label, an edge with its own and a child label, a UserObject."""


def build_drawio(path: Path, *, compressed: bool = False, model: str = DRAWIO_MODEL) -> Path:
    """A ``.drawio`` with the page ``Overview`` holding ``model`` (compressed: base64 of raw deflate of the
    URI-encoded XML, as draw.io writes it) and an empty page ``Notes``."""
    import base64  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415

    page = model
    if compressed:
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        raw = packer.compress(urllib.parse.quote(model, safe="").encode()) + packer.flush()
        page = base64.b64encode(raw).decode()
    path.write_text(
        f'<mxfile host="app.diagrams.net"><diagram name="Overview" id="a1">{page}</diagram>'
        '<diagram name="Notes" id="a2"><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram>'
        "</mxfile>",
        encoding="utf-8",
    )
    return path


_VISIO_NS = "http://schemas.microsoft.com/office/visio/2012/main"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def build_vsdx(
    path: Path, *, extra: Mapping[str, bytes] | None = None, root_rels: str = "", absolute: bool = False
) -> Path:
    """A minimal Visio package: page ``Site plan`` (a shape, a group with a member holding a field, a
    connector between two shapes) listed before its background page ``Frame``, whose relationship comes
    first. ``absolute``: the page relationships name their targets ``/visio/pages/…``, as some writers do."""
    ns, r = _VISIO_NS, "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    at = "/visio/pages/" if absolute else ""
    parts: dict[str, bytes] = {
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": (
            f'<Relationships xmlns="{_PKG_RELS}"><Relationship Id="rId1" '
            'Type="http://schemas.microsoft.com/visio/2010/relationships/document" '
            'Target="visio/document.xml"/>'
            f"{root_rels}</Relationships>"
        ).encode(),
        "visio/document.xml": f'<VisioDocument xmlns="{ns}"/>'.encode(),
        "visio/pages/pages.xml": (
            f'<Pages xmlns="{ns}" xmlns:r="{r}"><Page ID="0" NameU="Page-1" Name="Site plan">'
            '<Rel r:id="rId2"/></Page><Page ID="4" NameU="Background-1" Name="Frame" Background="1">'
            '<Rel r:id="rId1"/></Page></Pages>'
        ).encode(),
        "visio/pages/_rels/pages.xml.rels": (
            f'<Relationships xmlns="{_PKG_RELS}">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" '
            f'Target="{at}page2.xml"/><Relationship Id="rId2" '
            f'Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="{at}page1.xml"/>'
            "</Relationships>"
        ).encode(),
        "visio/pages/page1.xml": (
            f'<PageContents xmlns="{ns}"><Shapes>'
            '<Shape ID="1"><Text><cp IX="0"/>Contoso router</Text></Shape>'
            '<Shape ID="2" Type="Group"><Text>Rack A</Text><Shapes><Shape ID="3"><Text><pp IX="0"/>Server '
            '<fld IX="0">42</fld></Text></Shape></Shapes></Shape><Shape ID="5"><Text>uplink</Text></Shape>'
            '</Shapes><Connects><Connect FromSheet="5" FromCell="BeginX" ToSheet="1"/>'
            '<Connect FromSheet="5" FromCell="EndX" ToSheet="3"/></Connects></PageContents>'
        ).encode(),
        "visio/pages/page2.xml": (
            f'<PageContents xmlns="{ns}"><Shapes><Shape ID="1"><Text>Contoso internal</Text></Shape></Shapes>'
            "</PageContents>"
        ).encode(),
        **(extra or {}),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for name in sorted(parts):
            out.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), parts[name])
    return path
