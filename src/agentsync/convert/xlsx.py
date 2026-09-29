"""Converter: openpyxl emitter: one unit per sheet plus a 00-index unit (owner: convert)."""

from __future__ import annotations

import datetime as dt
import math
import re
import warnings
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _ascii_slug,
    _cell,
    _check_ooxml_container,
    _csv_bytes,
    _dist_version,
    _escape_line,
    _gfm_table,
    _join_limited,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
_INDEX_TERM_LIMIT = 400  # distinct terms kept on 00-index (most frequent first, then listed sorted)
_TERM_MAX_CHARS = 80  # a text cell up to this long is one term; longer text contributes its words
_HEADER_LIMIT = 30  # column headers listed per sheet on 00-index
_WORD_RE = re.compile(r"[^\W\d_][\w'\u2019-]{2,}", re.UNICODE)
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------------------------------------
# Cell formatting
# ---------------------------------------------------------------------------------------------------------


def _col_letter(col: int) -> str:
    """1 -> A, 27 -> AA (Excel column letters)."""
    out = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


def _ref(row: int, col: int) -> str:
    """A1-style reference."""
    return f"{_col_letter(col)}{row}"


def _fmt_value(v: object) -> str:
    """Render a cached cell value: integers stay integers, empty stays empty, never ``NaN``."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isfinite(v) and v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return repr(v)
    if isinstance(v, dt.datetime):
        if v.tzinfo is None and v.time() == dt.time(0, 0):
            return v.date().isoformat()
        return v.isoformat()
    if isinstance(v, dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    return str(v)


def _formula_text(v: object) -> str | None:
    """Formula of a cell from the ``data_only=False`` workbook, or None when the cell holds a constant."""
    if isinstance(v, str):
        return v if v.startswith("=") and len(v) > 1 else None
    kind = type(v).__name__
    if kind == "ArrayFormula":
        text = getattr(v, "text", None)
        return f"{{{text}}}" if text else None
    if kind == "DataTableFormula":
        return "=TABLE()"
    return None


def _code(text: str) -> str:
    """Inline code span safe inside a GFM table cell (pipes escaped, backtick fences widened)."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    inner = text.replace("|", "\\|").replace("\n", " ")
    pad = " " if longest else ""
    return f"{ticks}{pad}{inner}{pad}{ticks}"


def _chart_title(chart: Any) -> str:
    """Best-effort plain title of an openpyxl chart (rich-text runs joined), or ""."""
    try:
        title = chart.title
        if title is None:
            return ""
        if isinstance(title, str):
            return title
        runs: list[str] = []
        for para in title.tx.rich.p:
            for run in para.r or []:
                runs.append(str(run.t or ""))
        return "".join(runs).strip()
    except AttributeError:
        return ""


def _anchor_ref(obj: Any) -> str:
    """Top-left cell of a drawing anchor, or "" when not anchored to a cell."""
    try:
        frm = obj.anchor._from
        return _ref(int(frm.row) + 1, int(frm.col) + 1)
    except AttributeError:
        return ""


# ---------------------------------------------------------------------------------------------------------
# Sheet model
# ---------------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Sheet:
    """One worksheet's content as the emitter sees it (built once, rendered into sheet + index units)."""

    number: int
    title: str
    hidden: bool
    shown: dict[tuple[int, int], str] = field(default_factory=dict)  # table cell markdown
    plain: dict[tuple[int, int], str] = field(default_factory=dict)  # CSV value (value, else formula)
    values: dict[tuple[int, int], str] = field(default_factory=dict)  # cached value text only
    bounds: tuple[int, int, int, int] | None = None  # min_row, min_col, max_row, max_col
    missing_cache: int = 0  # formula cells with no cached value (non-Excel writer)
    formulas: int = 0
    comments: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    terms: Counter[str] = field(default_factory=Counter)

    @property
    def used_range(self) -> str:
        """``A1:D6`` or ``none``."""
        if self.bounds is None:
            return "none"
        r0, c0, r1, c1 = self.bounds
        return f"{_ref(r0, c0)}:{_ref(r1, c1)}"

    def rows(self) -> list[int]:
        """Row numbers with any content, ascending."""
        return sorted({r for r, _c in self.shown})

    def headers(self) -> list[str]:
        """Values of the first non-empty row (the de-facto column headers), left to right."""
        rows = self.rows()
        if not rows or self.bounds is None:
            return []
        first = rows[0]
        _r0, c0, _r1, c1 = self.bounds
        out = [self.values.get((first, c), "") for c in range(c0, c1 + 1)]
        return [h for h in out if h.strip()]


def _add_terms(counter: Counter[str], text: str) -> None:
    """Count one text value's grep-recall terms (whole short values, else their words)."""
    t = _WS_RE.sub(" ", text).strip()
    if not t or not any(ch.isalpha() for ch in t):
        return
    if len(t) <= _TERM_MAX_CHARS:
        counter[t] += 1
    else:
        for w in _WORD_RE.findall(t):
            counter[w] += 1


def _load(src: Path, *, data_only: bool, read_only: bool) -> Any:
    """Open a workbook with openpyxl, mapping its errors to ConversionError."""
    from openpyxl import load_workbook  # noqa: PLC0415 - heavy import, only when an xlsx is converted

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_workbook(src, read_only=read_only, data_only=data_only, keep_links=False)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, TypeError, AttributeError) as exc:
        raise ConversionError(f"openpyxl cannot read the workbook: {type(exc).__name__}: {exc}") from exc
    except Exception as exc:  # openpyxl raises InvalidFileException and lxml errors too
        raise ConversionError(f"openpyxl cannot read the workbook: {type(exc).__name__}: {exc}") from exc


def _build_sheet(number: int, ws: Any, wsf: Any) -> _Sheet:
    """Collect one worksheet from the values (``ws``) and formulas (``wsf``) workbooks."""
    sheet = _Sheet(number=number, title=str(ws.title), hidden=str(ws.sheet_state) != "visible")
    vcells: dict[tuple[int, int], Any] = ws._cells
    fcells: dict[tuple[int, int], Any] = wsf._cells
    for key in sorted(set(vcells) | set(fcells)):
        vcell = vcells.get(key)
        fcell = fcells.get(key)
        value = None if vcell is None else vcell.value
        formula = None if fcell is None else _formula_text(fcell.value)
        vtext = _fmt_value(value)
        if not vtext.strip() and formula is None:
            continue
        if formula is not None:
            sheet.formulas += 1
            if vtext == "":
                sheet.missing_cache += 1
                shown = _code(formula)
            else:
                shown = f"{_cell(vtext)} {_code(formula)}"
        else:
            shown = _cell(vtext)
        sheet.shown[key] = shown
        sheet.plain[key] = vtext if vtext != "" else (formula or "")
        if vtext:
            sheet.values[key] = vtext
        if isinstance(value, str):
            _add_terms(sheet.terms, value)
        comment = None if fcell is None else getattr(fcell, "comment", None)
        if comment is not None and str(comment.text or "").strip():
            author = str(comment.author or "").strip()
            text = _WS_RE.sub(" ", str(comment.text)).strip()
            who = f" ({author})" if author else ""
            sheet.comments.append(f"- {_ref(*key)}{who}: {_escape_line(text)}")
    if sheet.shown:
        rows = [r for r, _ in sheet.shown]
        cols = [c for _, c in sheet.shown]
        sheet.bounds = (min(rows), min(cols), max(rows), max(cols))
        r0, c0, r1, c1 = sheet.bounds
        # Merged ranges: propagate the anchor into every covered cell, clipped to the content bounds.
        for rng in sorted(ws.merged_cells.ranges, key=lambda m: (m.min_row, m.min_col, m.max_row, m.max_col)):
            anchor = (rng.min_row, rng.min_col)
            if anchor not in sheet.shown:
                continue
            for r in range(max(rng.min_row, r0), min(rng.max_row, r1) + 1):
                for c in range(max(rng.min_col, c0), min(rng.max_col, c1) + 1):
                    if (r, c) == anchor:
                        continue
                    sheet.shown[(r, c)] = sheet.shown[anchor]
                    sheet.plain[(r, c)] = sheet.plain[anchor]
                    if anchor in sheet.values:
                        sheet.values[(r, c)] = sheet.values[anchor]
    for i, chart in enumerate(getattr(wsf, "_charts", []), start=1):
        title = _chart_title(chart)
        where = _anchor_ref(chart)
        label = (
            f"{type(chart).__name__}" + (f' "{title}"' if title else "") + (f" at {where}" if where else "")
        )
        sheet.objects.append(f"- [chart {i}: {label} — no markdown form; open the workbook to view]")
    for pivot in getattr(wsf, "_pivots", []):
        name = str(getattr(pivot, "name", "") or "pivot")
        loc = getattr(getattr(pivot, "location", None), "ref", "") or ""
        at = f" at {loc}" if loc else ""
        sheet.objects.append(f"- [pivot table: {name}{at} — no markdown form; open the workbook to view]")
    for i, image in enumerate(getattr(wsf, "_images", []), start=1):
        where = _anchor_ref(image)
        sheet.objects.append(f"- [image {i}" + (f" at {where}" if where else "") + "]")
    return sheet


# ---------------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------------


def _sheet_stem(number: int, width: int, title: str) -> str:
    """``NN-<sheet name>`` (publish slugs it)."""
    return f"{number:0{width}d}-{title}"


def _sidecar_name(number: int, width: int, title: str) -> str:
    """Portable CSV sidecar name for a sheet."""
    return f"{number:0{width}d}-{_ascii_slug(title, fallback='sheet')}.csv"


def _table_lines(
    sheet: _Sheet, rows: list[int], cells: Mapping[tuple[int, int], str], budget: int, max_rows: int
) -> tuple[list[str], int]:
    """Render up to ``max_rows`` rows within ``budget`` bytes; return (lines, rows shown)."""
    assert sheet.bounds is not None
    _r0, c0, _r1, c1 = sheet.bounds
    header = ["Row", *(_col_letter(c) for c in range(c0, c1 + 1))]
    lines = _gfm_table(header, [])
    used = sum(len(ln.encode("utf-8")) + 1 for ln in lines)
    shown = 0
    for r in rows:
        if shown >= max_rows:
            break
        line = _gfm_table(header, [[str(r), *(cells.get((r, c), "") for c in range(c0, c1 + 1))]])[2]
        size = len(line.encode("utf-8")) + 1
        if used + size > budget and shown > 0:
            break
        lines.append(line)
        used += size
        shown += 1
    return lines, shown


def _sheet_body(sheet: _Sheet, *, total: int, width: int, cfg: ConvertConfig) -> tuple[str, bytes | None]:
    """Markdown for one sheet unit plus the CSV sidecar bytes when rows were capped."""
    hidden = " (hidden sheet)" if sheet.hidden else ""
    out = [f"# Sheet: {sheet.title} (used range {sheet.used_range}){hidden}", ""]
    rows = sheet.rows()
    ncols = 0 if sheet.bounds is None else sheet.bounds[3] - sheet.bounds[1] + 1
    out.append(
        f"Sheet {sheet.number} of {total} in this workbook · {len(rows)} non-empty rows x {ncols} columns"
    )
    out.append("")
    sidecar: bytes | None = None
    if not rows:
        out += ["[empty sheet]", ""]
    else:
        reserve = 4096 + 200 * (len(sheet.comments) + len(sheet.objects))
        budget = max(1024, cfg.max_page_bytes - reserve)
        lines, shown = _table_lines(sheet, rows, sheet.shown, budget, cfg.max_rows_per_sheet)
        out += lines
        out.append("")
        if shown < len(rows):
            assert sheet.bounds is not None
            _r0, c0, _r1, c1 = sheet.bounds
            header = ["Row", *(_col_letter(c) for c in range(c0, c1 + 1))]
            data = [[str(r), *(sheet.plain.get((r, c), "") for c in range(c0, c1 + 1))] for r in rows]
            sidecar = _csv_bytes([header, *data])
            sc = _sidecar_name(sheet.number, width, sheet.title)
            out += [
                f"[row cap: first {shown} of {len(rows)} non-empty rows shown; "
                f"every row is in the sidecar `{sc}`]",
                "",
            ]
    if sheet.missing_cache:
        out += [
            f"[no cached values: {sheet.missing_cache} of {sheet.formulas} formula cells carry no stored "
            "result (the workbook was last written by a tool that does not calculate, e.g. a script "
            "or BI export); "
            "those cells show the formula only, never a guessed number]",
            "",
        ]
    if sheet.comments:
        out += ["## Comments", "", *sheet.comments, ""]
    if sheet.objects:
        out += ["## Charts, pivots and images", "", *sheet.objects, ""]
    return "\n".join(out), sidecar


def _index_body(
    sheets: list[_Sheet],
    *,
    chartsheets: list[str],
    defined: list[tuple[str, str]],
    streamed: bool,
    max_bytes: int,
) -> str:
    """The workbook's grep-recall surface: every sheet, its range and headers, names, objects, terms."""
    out = ["# Workbook index", ""]
    mode = (
        " Streamed (above the size threshold): each sheet page is a schema + sample summary."
        if streamed
        else ""
    )
    out += [
        f"Workbook with {len(sheets)} worksheet(s); each sheet is its own page next to this index.{mode}",
        "",
    ]
    rows = []
    for s in sheets:
        heads = ", ".join(s.headers()[:_HEADER_LIMIT])
        title = s.title + (" (hidden)" if s.hidden else "")
        ncols = 0 if s.bounds is None else s.bounds[3] - s.bounds[1] + 1
        rows.append([str(s.number), _cell(title), s.used_range, str(len(s.rows())), str(ncols), _cell(heads)])
    if rows:
        out += _gfm_table(["#", "Sheet", "Used range", "Rows", "Columns", "Column headers"], rows)
        out.append("")
    if chartsheets:
        out += [
            "## Chart sheets",
            "",
            *(f"- [chartsheet: {_escape_line(c)} — open the workbook to view]" for c in chartsheets),
            "",
        ]
    if defined:
        out += ["## Defined names", "", *(f"- {n}: {_code(v)}" for n, v in defined), ""]
    objects = [f"- {s.title}: {line[2:]}" for s in sheets for line in s.objects]
    if objects:
        out += ["## Charts, pivots and images", "", *objects, ""]
    counter: Counter[str] = Counter()
    for s in sheets:
        counter.update(s.terms)
    if counter:
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].casefold(), kv[0]))[:_INDEX_TERM_LIMIT]
        terms = sorted((t for t, _ in ranked), key=lambda t: (t.casefold(), t))
        dropped = len(counter) - len(terms)
        note = f" ({dropped} rarer terms omitted)" if dropped > 0 else ""
        out += [f"## Terms{note}", ""]
        used = sum(len(x.encode("utf-8")) + 1 for x in out)
        for t in terms:
            line = f"- {_escape_line(t)}"
            used += len(line.encode("utf-8")) + 1
            if used > max_bytes - 256:
                out.append("- [term list truncated at the page size cap]")
                break
            out.append(line)
        out.append("")
    return "\n".join(out)


def _index_summary(sheets: list[_Sheet]) -> str:
    """Converter-derived summary of the workbook."""
    parts = [f"{s.title} ({s.used_range})" for s in sheets]
    return f"Workbook: {len(sheets)} sheet(s): " + _join_limited(parts, limit=170)


def _index_title(sheets: list[_Sheet]) -> str:
    """Name-independent title for 00-index (the workbook's own file name lives in source_path)."""
    names = [s.title for s in sheets]
    return (
        "Workbook: " + _join_limited(names, limit=150, sep=", ") if names else "Workbook without worksheets"
    )


def _sheet_summary(sheet: _Sheet) -> str:
    """Converter-derived summary of one sheet."""
    ncols = 0 if sheet.bounds is None else sheet.bounds[3] - sheet.bounds[1] + 1
    nrows = len(sheet.rows())
    base = f"Sheet {sheet.title}: used range {sheet.used_range}, {nrows} rows x {ncols} columns"
    heads = sheet.headers()
    return base + ("; columns: " + _join_limited(heads, limit=120, sep=", ") if heads else "")


def _defined_names(wb: Any) -> list[tuple[str, str]]:
    """Workbook-scoped defined names, sorted."""
    out: list[tuple[str, str]] = []
    try:
        items = list(wb.defined_names.items())
    except AttributeError:
        return out
    for key, dn in items:
        value = str(getattr(dn, "attr_text", "") or getattr(dn, "value", "") or "")
        if not str(key).startswith("_xlnm."):
            out.append((str(key), value))
    return sorted(out)


# ---------------------------------------------------------------------------------------------------------
# Streaming (read_only) path for workbooks above the size threshold
# ---------------------------------------------------------------------------------------------------------


def _type_name(v: object) -> str:
    """Coarse type label for the schema table."""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float" if not v.is_integer() else "int"
    if isinstance(v, dt.datetime | dt.date | dt.time | dt.timedelta):
        return "date/time"
    return "text"


def _stream_sheet(number: int, ws: Any, wsf: Any, cfg: ConvertConfig) -> tuple[_Sheet, list[str], bytes, int]:
    """Stream one sheet: (sheet with the sampled rows, schema table lines, full CSV, sampled row count)."""
    sheet = _Sheet(
        number=number, title=str(ws.title), hidden=str(getattr(ws, "sheet_state", "visible")) != "visible"
    )
    csv_rows: list[list[str]] = []
    types: dict[int, Counter[str]] = {}
    nonempty: Counter[int] = Counter()
    min_c = max_c = 0
    min_r = max_r = 0
    total_rows = 0
    sampled = 0
    frows: Iterator[tuple[Any, ...]] = wsf.iter_rows(values_only=True)
    for r, vrow in enumerate(ws.iter_rows(values_only=True), start=1):
        frow = next(frows, ())
        row_plain: dict[int, str] = {}
        for c in range(1, max(len(vrow), len(frow)) + 1):
            v = vrow[c - 1] if c <= len(vrow) else None
            f = _formula_text(frow[c - 1]) if c <= len(frow) else None
            vtext = _fmt_value(v)
            if not vtext.strip() and f is None:
                continue
            if f is not None:
                sheet.formulas += 1
                if vtext == "":
                    sheet.missing_cache += 1
            row_plain[c] = vtext if vtext != "" else (f or "")
            types.setdefault(c, Counter())[
                "formula" if (f is not None and vtext == "") else _type_name(v)
            ] += 1
            nonempty[c] += 1
            if sampled < cfg.max_rows_per_sheet:
                sheet.shown[(r, c)] = (
                    _cell(vtext) if f is None else (_code(f) if vtext == "" else f"{_cell(vtext)} {_code(f)}")
                )
                if vtext:
                    sheet.values[(r, c)] = vtext
                if isinstance(v, str):
                    _add_terms(sheet.terms, v)
        if not row_plain:
            continue
        total_rows += 1
        if sampled < cfg.max_rows_per_sheet:
            sampled += 1
        lo, hi = min(row_plain), max(row_plain)
        min_c = lo if min_c == 0 else min(min_c, lo)
        max_c = max(max_c, hi)
        min_r = r if min_r == 0 else min_r
        max_r = r
        csv_rows.append([str(r), *(row_plain.get(c, "") for c in range(1, hi + 1))])
    if total_rows:
        sheet.bounds = (min_r, min_c, max_r, max_c)
    width = max((len(row) for row in csv_rows), default=1)
    header = ["Row", *(_col_letter(c) for c in range(1, width))]
    csv_data = _csv_bytes([header, *csv_rows])
    heads = {c: sheet.values.get((min_r, c), "") for c in range(min_c, max_c + 1)} if total_rows else {}
    schema_rows = []
    for c in sorted(types):
        tlabel = ", ".join(f"{k} {n}" for k, n in sorted(types[c].items(), key=lambda kv: (-kv[1], kv[0])))
        schema_rows.append([_col_letter(c), _cell(heads.get(c, "")), tlabel, str(nonempty[c])])
    schema = (
        _gfm_table(["Column", "First-row value", "Types (count)", "Non-empty"], schema_rows)
        if schema_rows
        else []
    )
    return sheet, schema, csv_data, total_rows


class XlsxConverter:
    """Openpyxl emitter: one unit per sheet plus a 00-index unit.

    Opens twice (data_only=True for values, False for formulas); cells render ``value `=FORMULA```; merged
    ranges propagate the anchor; integers stay integers (no ``100.0``), empty stays empty (no ``NaN``);
    heading carries the used range; pivots/charts become stub lines.  Units: ``index`` (00-index: title,
    every sheet's name, used range, column headers, distinctive terms) then ``sheet:<n>`` (file_stem
    ``NN-<sheet name>``).  Files above ``xlsx_stream_threshold_bytes`` use read_only streaming and emit
    ``summary`` units (schema + first ``max_rows_per_sheet`` rows) with a CSV sidecar.
    """

    converter_id = "xlsx-openpyxl"
    extensions: tuple[str, ...] = (".xlsx", ".xlsm")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+openpyxl-{_dist_version('openpyxl')}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {
            "max_page_bytes": self._cfg.max_page_bytes,
            "max_rows_per_sheet": self._cfg.max_rows_per_sheet,
            "xlsx_stream_threshold_bytes": self._cfg.xlsx_stream_threshold_bytes,
            "index_term_limit": _INDEX_TERM_LIMIT,
        }

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        _check_ooxml_container(src)
        if src.stat().st_size > self._cfg.xlsx_stream_threshold_bytes:
            return self._convert_streaming(src)
        wb = _load(src, data_only=True, read_only=False)
        wbf = _load(src, data_only=False, read_only=False)
        try:
            sheets = [_build_sheet(n, ws, wbf[ws.title]) for n, ws in enumerate(wb.worksheets, start=1)]
            chartsheets = [str(cs.title) for cs in wb.chartsheets]
            defined = _defined_names(wbf)
        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError(f"cannot read workbook content: {type(exc).__name__}: {exc}") from exc
        finally:
            wb.close()
            wbf.close()
        total = len(sheets)
        width = max(2, len(str(total)))
        of = total + 1
        units = [
            make_unit(
                unit_id="index",
                kind=UnitKind.INDEX,
                index=0,
                of=of,
                name="",
                file_stem="00-index",
                title=_index_title(sheets),
                summary=_index_summary(sheets),
                body=_index_body(
                    sheets,
                    chartsheets=chartsheets,
                    defined=defined,
                    streamed=False,
                    max_bytes=self._cfg.max_page_bytes,
                ),
            )
        ]
        for s in sheets:
            body, csv_data = _sheet_body(s, total=total, width=width, cfg=self._cfg)
            sidecars = () if csv_data is None else ((_sidecar_name(s.number, width, s.title), csv_data),)
            units.append(
                make_unit(
                    unit_id=f"sheet:{s.number}",
                    kind=UnitKind.SHEET,
                    index=s.number,
                    of=of,
                    name=s.title,
                    file_stem=_sheet_stem(s.number, width, s.title),
                    title=f"Sheet: {s.title}",
                    summary=_sheet_summary(s),
                    body=body,
                    sidecars=sidecars,
                )
            )
        return tuple(units)

    def _convert_streaming(self, src: Path) -> tuple[RenderedUnit, ...]:
        """Read-only streaming path: index + one ``summary:<n>`` unit per sheet, each with a CSV sidecar."""
        wb = _load(src, data_only=True, read_only=True)
        wbf = _load(src, data_only=False, read_only=True)
        streamed: list[tuple[_Sheet, list[str], bytes, int]] = []
        try:
            for n, ws in enumerate(wb.worksheets, start=1):
                streamed.append(_stream_sheet(n, ws, wbf[ws.title], self._cfg))
            chartsheets = [str(cs.title) for cs in wb.chartsheets]
            defined = _defined_names(wbf)
        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError(f"cannot stream workbook content: {type(exc).__name__}: {exc}") from exc
        finally:
            wb.close()
            wbf.close()
        sheets = [s for s, _schema, _csv, _n in streamed]
        total = len(sheets)
        width = max(2, len(str(total)))
        of = total + 1
        units = [
            make_unit(
                unit_id="index",
                kind=UnitKind.INDEX,
                index=0,
                of=of,
                name="",
                file_stem="00-index",
                title=_index_title(sheets),
                summary=_index_summary(sheets),
                body=_index_body(
                    sheets,
                    chartsheets=chartsheets,
                    defined=defined,
                    streamed=True,
                    max_bytes=self._cfg.max_page_bytes,
                ),
            )
        ]
        for sheet, schema, csv_data, total_rows in streamed:
            sc = _sidecar_name(sheet.number, width, sheet.title)
            hidden = " (hidden sheet)" if sheet.hidden else ""
            out = [f"# Sheet: {sheet.title} (used range {sheet.used_range}; streamed summary){hidden}", ""]
            out += [
                f"Sheet {sheet.number} of {total} in this workbook · {total_rows} non-empty rows · streamed "
                "because the file is above the size threshold: merged cells, comments and charts are "
                "not analysed; "
                f"every row is in the sidecar `{sc}`",
                "",
            ]
            if schema:
                out += ["## Schema", "", *schema, ""]
            rows = sheet.rows()
            if rows:
                budget = max(1024, self._cfg.max_page_bytes - 4096 - sum(len(x) + 1 for x in out))
                lines, shown = _table_lines(sheet, rows, sheet.shown, budget, self._cfg.max_rows_per_sheet)
                out += [f"## First {shown} of {total_rows} non-empty rows", "", *lines, ""]
            else:
                out += ["[empty sheet]", ""]
            if sheet.missing_cache:
                out += [
                    f"[no cached values: {sheet.missing_cache} of {sheet.formulas} formula cells carry "
                    "no stored result; those cells show the formula only]",
                    "",
                ]
            units.append(
                make_unit(
                    unit_id=f"summary:{sheet.number}",
                    kind=UnitKind.SUMMARY,
                    index=sheet.number,
                    of=of,
                    name=sheet.title,
                    file_stem=_sheet_stem(sheet.number, width, sheet.title),
                    title=f"Sheet: {sheet.title}",
                    summary=_sheet_summary(sheet) + " (streamed summary)",
                    body="\n".join(out),
                    sidecars=((sc, csv_data),),
                )
            )
        return tuple(units)
