"""Converter: python-pptx emitter: one WHOLE unit with slide anchors (owner: convert)."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _cell,
    _check_ooxml_container,
    _dist_version,
    _escape_line,
    _first_line,
    _gfm_table,
    _join_limited,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
_ROW_TOLERANCE_EMU = (
    45_720  # 0.05 in: shapes whose tops differ by less are on one visual row (Docling's rule)
)
_WS_RE = re.compile(r"[ \t\f]+")
_BULLET_PLACEHOLDERS = frozenset({"BODY", "OBJECT"})


def _norm(text: str) -> str:
    """Slide text: vertical tabs (soft breaks) to spaces, runs of blanks collapsed, trimmed."""
    return _WS_RE.sub(" ", text.replace("\x0b", " ").replace("\v", " ")).strip()


def _pos(shape: Any) -> tuple[int, int]:
    """(top, left) in EMU; unknown positions sort first (0), keeping XML order via the stable sort."""
    top = getattr(shape, "top", None)
    left = getattr(shape, "left", None)
    return (int(top) if top is not None else 0, int(left) if left is not None else 0)


def _reading_order(shapes: Iterable[Any]) -> list[Any]:
    """Top-to-bottom rows (tops within 0.05 in are one row), left-to-right within a row; stable."""
    items = sorted(enumerate(shapes), key=lambda it: (_pos(it[1])[0], it[0]))
    rows: list[list[tuple[int, Any]]] = []
    row_top: int | None = None
    for i, shape in items:
        top = _pos(shape)[0]
        if row_top is None or top - row_top > _ROW_TOLERANCE_EMU:
            rows.append([])
            row_top = top
        rows[-1].append((i, shape))
    out: list[Any] = []
    for row in rows:
        out.extend(s for _i, s in sorted(row, key=lambda it: (_pos(it[1])[1], it[0])))
    return out


def _alt_text(shape: Any) -> str:
    """The picture's alt text (``descr`` on cNvPr), or ""."""
    try:
        for el in shape.element.iter():
            tag = str(el.tag)
            if tag.endswith("}cNvPr"):
                return _norm(str(el.get("descr") or ""))
    except AttributeError:
        return ""
    return ""


def _placeholder_type(shape: Any) -> str:
    """Placeholder type name (``BODY``, ``TITLE`` ...) or "" for a non-placeholder."""
    if not getattr(shape, "is_placeholder", False):
        return ""
    try:
        ptype = shape.placeholder_format.type
    except (AttributeError, ValueError):
        return ""
    return str(getattr(ptype, "name", ptype) or "")


def _text_frame_lines(shape: Any) -> list[str]:
    """Paragraphs of a text frame: bullets (with level indent) for body placeholders, else plain lines."""
    bullets = _placeholder_type(shape) in _BULLET_PLACEHOLDERS
    lines: list[str] = []
    for para in shape.text_frame.paragraphs:
        text = _norm(str(para.text))
        if not text:
            continue
        if bullets:
            level = int(getattr(para, "level", 0) or 0)
            lines.append("  " * level + "- " + text.replace("<!--", "&lt;!--"))
        else:
            lines.append(_escape_line(text))
    return lines


def _table_lines(table: Any) -> list[str]:
    """A pptx table as GFM (first row as header); merged cells carry the origin cell's text."""
    rows = list(table.rows)
    ncols = len(table.columns)
    grid: list[list[str]] = [["" for _ in range(ncols)] for _ in rows]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row.cells):
            if c >= ncols:
                break
            if getattr(cell, "is_spanned", False):
                continue
            text = _cell(str(cell.text))
            grid[r][c] = text
            if getattr(cell, "is_merge_origin", False):
                for dr in range(int(cell.span_height)):
                    for dc in range(int(cell.span_width)):
                        if r + dr < len(grid) and c + dc < ncols:
                            grid[r + dr][c + dc] = text
    if not grid:
        return []
    return _gfm_table(grid[0], grid[1:])


def _chart_lines(chart: Any) -> list[str]:
    """A chart as a stub line plus its category/series data as a GFM table when python-pptx exposes it."""
    title = ""
    try:
        if chart.has_title:
            title = _norm(str(chart.chart_title.text_frame.text))
    except (AttributeError, ValueError, KeyError):
        title = ""
    kind = ""
    try:
        kind = str(chart.chart_type.name)
    except (AttributeError, ValueError, KeyError):
        kind = ""
    label = ", ".join(x for x in (f'"{title}"' if title else "", kind.lower()) if x)
    lines = [f"[chart: {label}]" if label else "[chart]"]
    try:
        plot = chart.plots[0]
        categories = [str(c) for c in plot.categories]
        series = list(plot.series)
        if categories and series:
            header = ["Category", *(_cell(str(s.name or f"Series {i}")) for i, s in enumerate(series, 1))]
            columns = [_series_values(s, len(categories)) for s in series]
            data = []
            for i, cat in enumerate(categories):
                row = [_cell(cat)]
                for values in columns:
                    v = values[i]
                    row.append("" if v is None else _fmt_number(v))
                data.append(row)
            lines += ["", *_gfm_table(header, data)]
    except (AttributeError, IndexError, KeyError, ValueError, TypeError, NotImplementedError):
        pass
    return lines


def _series_values(series: Any, n: int) -> list[float | None]:
    """The series' first ``n`` cached values, read in ONE pass over its points (None where a point is absent).

    python-pptx's ``series.values`` runs one XPath per point, and calling it per table cell made a large
    chart take hours.  The result is what ``values`` returns: the first ``c:pt`` of each index below
    ``c:ptCount`` counts, and a point at or above it is ignored.
    """
    out: list[float | None] = [None] * n
    val = series._element.val
    if val is None:
        return out
    count = val.ptCount_val
    seen: set[int] = set()
    for pt in val.xpath(".//c:pt"):
        try:
            idx = int(pt.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < count or idx in seen:
            continue
        seen.add(idx)
        value = pt.value  # read past ``n`` too: a point with no value drops the table, as ``values`` did
        if idx < n:
            out[idx] = value
    return out


def _fmt_number(v: object) -> str:
    """Chart values: integral floats print as integers (no ``100.0``)."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def _shape_blocks(shape: Any, title_id: int | None) -> list[list[str]]:
    """Markdown blocks for one shape (groups recurse in reading order)."""
    if title_id is not None and getattr(shape, "shape_id", None) == title_id:
        return []
    blocks: list[list[str]] = []
    type_name = type(shape).__name__
    if type_name == "GroupShape":
        for child in _reading_order(shape.shapes):
            blocks += _shape_blocks(child, title_id)
        return blocks
    if getattr(shape, "has_table", False):
        lines = _table_lines(shape.table)
        if lines:
            blocks.append(lines)
        return blocks
    if getattr(shape, "has_chart", False):
        blocks.append(_chart_lines(shape.chart))
        return blocks
    if type_name in ("Picture", "PlaceholderPicture"):
        alt = _alt_text(shape)
        blocks.append([f"[image: {alt}]" if alt else "[image]"])
        return blocks
    if type_name == "Movie":
        blocks.append(["[media]"])
        return blocks
    if getattr(shape, "has_text_frame", False):
        lines = _text_frame_lines(shape)
        if lines:
            bullets = _placeholder_type(shape) in _BULLET_PLACEHOLDERS
            if bullets:
                blocks.append(lines)
            else:
                blocks.extend([ln] for ln in lines)
    return blocks


class PptxConverter:
    """Python-pptx emitter: one WHOLE unit with slide anchors.

    ``<!-- Slide number: N -->`` anchor per slide, title as ``## ``, text frames in reading order, tables as
    GFM, speaker notes under ``### Notes:``.  Pictures become ``[image: <alt text>]`` lines.
    """

    converter_id = "pptx-python-pptx"
    extensions: tuple[str, ...] = (".pptx",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+python-pptx-{_dist_version('python-pptx')}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {"max_page_bytes": self._cfg.max_page_bytes, "row_tolerance_emu": _ROW_TOLERANCE_EMU}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        _check_ooxml_container(src)
        from pptx import Presentation  # noqa: PLC0415 - heavy import, only when a deck is converted

        try:
            prs = Presentation(str(src))
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise ConversionError(f"python-pptx cannot read the deck: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # PackageNotFoundError, lxml XMLSyntaxError
            raise ConversionError(f"python-pptx cannot read the deck: {type(exc).__name__}: {exc}") from exc
        out: list[str] = []
        titles: list[str] = []
        try:
            slides = list(prs.slides)
            for n, slide in enumerate(slides, start=1):
                out += self._slide(n, slide, titles)
        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError(f"cannot read slide content: {type(exc).__name__}: {exc}") from exc
        if not slides:
            out = ["[empty presentation: no slides]"]
        body = "\n".join(out)
        title = next((t for t in titles if t), "") or _first_line(body) or "Untitled presentation"
        named = [t for t in titles if t]
        summary = f"Presentation: {len(slides)} slide(s)"
        if named:
            summary += "; titles: " + _join_limited(named, limit=170)
        body, sidecars = _cap_body(body, self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR)
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title=title,
                summary=summary,
                body=body,
                sidecars=sidecars,
            ),
        )

    def _slide(self, n: int, slide: Any, titles: list[str]) -> list[str]:
        """Markdown lines for one slide (anchor, title, blocks, notes)."""
        out = [f"<!-- Slide number: {n} -->", ""]
        hidden = str(slide.element.get("show", "1")) in ("0", "false")
        title_shape = slide.shapes.title
        title = (
            _norm(str(title_shape.text_frame.text))
            if title_shape is not None and title_shape.has_text_frame
            else ""
        )
        titles.append(title)
        if title:
            out += [f"## {title.replace('<!--', '&lt;!--')}", ""]
        if hidden:
            out += ["[hidden slide]", ""]
        title_id = None if title_shape is None else int(title_shape.shape_id)
        for shape in _reading_order(slide.shapes):
            for block in _shape_blocks(shape, title_id):
                out += [*block, ""]
        if slide.has_notes_slide:
            frame = slide.notes_slide.notes_text_frame
            notes = [] if frame is None else [_escape_line(_norm(str(p.text))) for p in frame.paragraphs]
            notes = [x for x in notes if x]
            if notes:
                out += ["### Notes:", "", *notes, ""]
        return out
