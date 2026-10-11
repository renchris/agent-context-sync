"""Converter: diagrams read with the standard library: draw.io (``.drawio``) and Visio (``.vsdx``) (owner:
convert).

One WHOLE unit: per page a ``## <page name>`` heading, the text of each shape that has text (one line, in the
file's order), then ``Connections:`` with one ``- <from> -> <to>: <label>`` line per connector that has a
labelled end or a label of its own.  Pictures, shape data, links, layer names and a Visio master's default
text are not read; text on a hidden layer is read like any other.  The title is left empty, so the page takes
the file's name; the summary counts pages and names them.  No XML part is inflated past ``_MAX_PART_BYTES``
and no file is read past ``_MAX_TOTAL_BYTES`` of XML in all, so a deflate bomb or an oversized file is a
``conversion failed: diagram … too large`` stub, never a hang.
"""

from __future__ import annotations

import base64
import binascii
import html
import posixpath
import re
import urllib.parse
import zipfile
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _check_ooxml_container,
    _escape_line,
    _join_limited,
    _python_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
_MAX_PART_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_V = "{http://schemas.microsoft.com/office/visio/2012/main}"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_PAGES_PART = "visio/pages/pages.xml"
# Each tag pattern is linear in the label: a tag's tail holds no ``<`` (so a run of ``<`` without ``>`` is not
# rescanned to the end from every one), and the blanks around a closing ``/`` can be split one way only.
_BREAK_RE = re.compile(
    r"<\s*(?:/\s*)?(?:br|div|p|li|tr|ul|ol|table|blockquote|pre|hr|h[1-6])\b[^<>]*>", re.IGNORECASE
)  # draw.io's editor writes a new line as ``<br>`` or as ``<div>…</div>``: both open and close are a break
_CELL_RE = re.compile(r"<\s*(?:/\s*)?t[dh]\b[^<>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^<>]*>")
_WS_RE = re.compile("[ \t\f\v\u00a0]+")
_LINE_SEPS = str.maketrans({"\r": "\n", "\u2028": "\n", "\u2029": "\n", "\x0b": "\n"})


@dataclass
class _Page:
    name: str
    shapes: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    background: bool = False


def _one_line(text: str) -> str:
    """Shape text on one line: each line's blanks collapsed, empty lines dropped, the rest joined by
    `` / ``."""
    lines = (_WS_RE.sub(" ", ln).strip() for ln in text.translate(_LINE_SEPS).split("\n"))
    return " / ".join(ln for ln in lines if ln)


def _html_text(label: str) -> str:
    """The text of a draw.io HTML label: line breaks kept, tags dropped, entities decoded."""
    return html.unescape(_TAG_RE.sub("", _CELL_RE.sub(" ", _BREAK_RE.sub("\n", label))))


def _parse(data: bytes, what: str) -> ET.Element:
    """Parse one XML part; a malformed one fails the file with what it was and where.  The reason is the
    parser's fixed wording for its error code and the position, never its message: that can quote the file
    (an undefined entity's name, an unknown encoding's)."""
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        line, column = exc.position
        raise ConversionError(
            f"{what} is not well-formed XML: {expat.ErrorString(exc.code)}: line {line}, column {column}"
        ) from exc
    except (LookupError, ValueError) as exc:  # an encoding declaration Python cannot decode
        raise ConversionError(f"{what} is not well-formed XML: its encoding is not supported") from exc


class _Budget:
    """Inflated bytes left for this file."""

    def __init__(self) -> None:
        self.left = _MAX_TOTAL_BYTES

    def spend(self, n: int) -> None:
        """Count ``n`` inflated bytes; past the allowance the file is too large."""
        self.left -= n
        if self.left < 0:
            raise ConversionError(f"diagram too large: over {_MAX_TOTAL_BYTES} bytes of XML")


# --- draw.io ---------------------------------------------------------------------------------------------


def _inflate_diagram(text: str, budget: _Budget) -> bytes:
    """A compressed draw.io page: base64 of raw deflate of the URI-encoded model XML."""
    try:
        raw = base64.b64decode("".join(text.split()), validate=True)
        inflater = zlib.decompressobj(-15)
        data = inflater.decompress(raw, _MAX_PART_BYTES + 1)
    except (binascii.Error, ValueError, zlib.error) as exc:
        raise ConversionError(f"a compressed draw.io page cannot be read: {type(exc).__name__}") from exc
    if len(data) > _MAX_PART_BYTES or inflater.unconsumed_tail:
        raise ConversionError(f"diagram page too large: over {_MAX_PART_BYTES} bytes of XML")
    budget.spend(len(data))
    return urllib.parse.unquote(data.decode("latin-1"), encoding="utf-8", errors="replace").encode("utf-8")


def _drawio_label(el: ET.Element, cell: ET.Element) -> str:
    label = el.get("label") if el is not cell else cell.get("value")
    if not label:
        return ""
    # draw.io renders a label as HTML only when the cell's style says html=1; otherwise ``<`` is a character
    html_label = "html=1" in (cell.get("style") or "").split(";")
    return _one_line(_html_text(label) if html_label else label)


def _drawio_page(name: str, model: ET.Element) -> _Page:
    page = _Page(name)
    root = model.find("root")
    cells: list[tuple[str, ET.Element, str]] = []  # (id, mxCell, text)
    for el in [] if root is None else list(root):
        cell = el if el.tag == "mxCell" else el.find("mxCell")
        if cell is None:
            continue
        cells.append((el.get("id") or cell.get("id") or "", cell, _drawio_label(el, cell)))
    text = {cid: t for cid, _, t in cells if cid}  # a cell without an id is no edge's end
    edges = {cid for cid, cell, _ in cells if cell.get("edge") == "1"}
    edge_labels: dict[str, list[str]] = {}
    for _cid, cell, t in cells:
        if cell.get("vertex") == "1" and cell.get("parent") in edges:
            if t:
                edge_labels.setdefault(cell.get("parent") or "", []).append(t)
        elif cell.get("vertex") == "1" and t:
            page.shapes.append(t)
    for cid, cell, t in cells:
        if cid not in edges:
            continue
        label = " / ".join([t, *edge_labels.get(cid, [])] if t else edge_labels.get(cid, []))
        a, b = text.get(cell.get("source") or "", ""), text.get(cell.get("target") or "", "")
        if a or b or label:
            page.links.append(_link(a, b, label))
    return page


def _drawio_pages(src: Path) -> list[_Page]:
    budget = _Budget()
    try:
        with src.open("rb") as fh:
            data = fh.read(_MAX_TOTAL_BYTES + 1)
    except OSError as exc:
        raise ConversionError(f"cannot read: {exc}") from exc
    if len(data) > _MAX_TOTAL_BYTES:  # parsed whole, so bounded like an inflated part (about 10x in memory)
        raise ConversionError(f"diagram too large: over {_MAX_TOTAL_BYTES} bytes of XML")
    top = _parse(data, "the draw.io file")
    if top.tag == "mxGraphModel":
        return [_drawio_page("Page 1", top)]
    if top.tag != "mxfile":
        raise ConversionError("not a draw.io diagram (no <mxfile> or <mxGraphModel> root element)")
    pages: list[_Page] = []
    for n, diagram in enumerate(top.iter("diagram"), start=1):
        name = _one_line(diagram.get("name") or "") or f"Page {n}"
        model = diagram.find("mxGraphModel")
        if model is None and (diagram.text or "").strip():
            model = _parse(_inflate_diagram(diagram.text or "", budget), f"page {n}")
        pages.append(_Page(name) if model is None else _drawio_page(name, model))
    return pages


# --- Visio -----------------------------------------------------------------------------------------------


def _part(zf: zipfile.ZipFile, name: str, budget: _Budget) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    with zf.open(info) as fh:
        data = fh.read(_MAX_PART_BYTES + 1)
    if len(data) > _MAX_PART_BYTES:
        raise ConversionError(f"diagram page too large: over {_MAX_PART_BYTES} bytes of XML")
    budget.spend(len(data))
    return data


def _vsdx_shapes(shapes: ET.Element | None) -> Iterator[ET.Element]:
    """Every shape, a group's members after the group, in the page's order."""
    for shape in [] if shapes is None else shapes.findall(f"{_V}Shape"):
        yield shape
        yield from _vsdx_shapes(shape.find(f"{_V}Shapes"))


def _vsdx_page(page: _Page, contents: ET.Element) -> _Page:
    shapes: list[tuple[str, str]] = []  # (ID, text): each shape keeps its own text, whatever its ID
    for s in _vsdx_shapes(contents.find(f"{_V}Shapes")):
        t = s.find(f"{_V}Text")
        shapes.append((s.get("ID") or "", "" if t is None else _one_line("".join(t.itertext()))))
    named = {sid: t for sid, t in shapes if sid}  # a shape without an ID is no connector's end
    ends: dict[str, dict[str, str]] = {}
    for c in contents.iter(f"{_V}Connect"):
        # Only a 1-D shape's BeginX/EndX glue makes it a connector; a callout or a shape glued to a guide
        # (Controls.*, PinX) stays a shape.
        from_cell = c.get("FromCell") or ""
        side = "a" if from_cell.startswith("Begin") else "b" if from_cell.startswith("End") else ""
        if side:
            ends.setdefault(c.get("FromSheet") or "", {})[side] = c.get("ToSheet") or ""
    for sid, label in shapes:
        if sid and sid in ends:
            a, b = named.get(ends[sid].get("a", ""), ""), named.get(ends[sid].get("b", ""), "")
            if a or b or label:
                page.links.append(_link(a, b, label))
        elif label:
            page.shapes.append(label)
    return page


def _vsdx_pages(src: Path) -> list[_Page]:
    _check_ooxml_container(src)
    budget = _Budget()
    try:
        with zipfile.ZipFile(src) as zf:
            index = _part(zf, _PAGES_PART, budget)
            if index is None:
                raise ConversionError("not a Visio drawing (no visio/pages/pages.xml)")
            rels = _part(zf, "visio/pages/_rels/pages.xml.rels", budget)
            targets = (
                {}
                if rels is None
                else {
                    # A target is relative to visio/pages/ or, from some writers, absolute ("/visio/…")
                    r.get("Id"): posixpath.normpath(
                        posixpath.join("visio/pages", r.get("Target") or "")
                    ).lstrip("/")
                    for r in _parse(rels, "the page list's relationships").iter(_RELS)
                    if r.get("TargetMode") != "External"
                }
            )
            pages: list[_Page] = []
            for n, el in enumerate(_parse(index, "the page list").iter(f"{_V}Page"), start=1):
                name = _one_line(el.get("Name") or el.get("NameU") or "") or f"Page {n}"
                page = _Page(name, background=el.get("Background") == "1")
                rel = el.find(f"{_V}Rel")
                part = targets.get(None if rel is None else rel.get(_R_ID))
                data = None if part is None else _part(zf, part, budget)
                pages.append(page if data is None else _vsdx_page(page, _parse(data, f"page {n}")))
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
        raise ConversionError(f"corrupt Visio package: {type(exc).__name__}: {exc}") from exc
    return pages


# --- both ------------------------------------------------------------------------------------------------


def _link(a: str, b: str, label: str) -> str:
    out = f"{a or '(no text)'} -> {b or '(no text)'}"
    return f"{out}: {label}" if label else out


class DiagramConverter:
    """``.drawio`` (draw.io / diagrams.net, plain or compressed pages) and ``.vsdx`` (Visio 2013+) as text."""

    converter_id = "diagram-stdlib"
    extensions: tuple[str, ...] = (".drawio", ".vsdx")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + Python, whose zlib, base64 and XML parser read the file)."""
        return f"{_EMITTER_VERSION}+python-{_python_version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {
            "max_page_bytes": self._cfg.max_page_bytes,
            "max_part_bytes": _MAX_PART_BYTES,
            "max_total_bytes": _MAX_TOTAL_BYTES,
        }

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        if name.lower().endswith(".vsdx"):
            pages, kind = _vsdx_pages(src), "Visio drawing"
        else:
            pages, kind = _drawio_pages(src), "draw.io diagram"
        out: list[str] = []
        for page in pages:
            head = page.name.replace("<!--", "&lt;!--") + (" (background page)" if page.background else "")
            out += [f"## {head}", ""]
            out += [f"- {_escape_line(s)}" for s in page.shapes] or ["[no text on this page]"]
            if page.links:
                out += ["", "Connections:", "", *(f"- {_escape_line(x)}" for x in page.links)]
            out.append("")
        has_text = any(p.shapes or p.links for p in pages)
        body, sidecars = _cap_body(
            "\n".join(out) if has_text else "", self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR
        )
        summary = f"{kind}: {len(pages)} page(s)"
        if pages:
            summary += "; pages: " + _join_limited([p.name for p in pages], limit=170)
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title="",
                summary=summary,
                body=body,
                sidecars=sidecars,
            ),
        )
