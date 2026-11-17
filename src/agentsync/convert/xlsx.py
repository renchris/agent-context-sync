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
    GUARD_RESERVE,
    _ascii_slug,
    _cell,
    _check_ooxml_container,
    _check_ooxml_family,
    _csv_bytes,
    _dist_version,
    _escape_line,
    _footer_bytes,
    _gfm_table,
    _join_limited,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind
from agentsync.text import plural

_EMITTER_VERSION = "1.0.3"  # B13 round 41 C2043, C2064: a sheet page's lists stay within the page cap
_INDEX_TERM_LIMIT = 400  # distinct terms kept on 00-index (most frequent first, then listed sorted)
_TERM_MAX_CHARS = 80  # a text cell up to this long is one term; longer text contributes its words
_HEADER_LIMIT = 30  # column headers listed per sheet on 00-index
_HEADER_CHARS = 600  # a sheet's headers cell on 00-index, at least, cut before escaping (B13 round 40 C1848)
_FIRST_ROW_CHARS = (
    200  # a streamed Schema's first-row value, at least, cut before escaping (B13 round 40 C1848)
)
_LABEL_CHARS = 200  # a chart title or pivot name on a sheet page, cut before escaping (B13 round 41 C2046)
_SHEET_RESERVE = 4096  # a sheet page's room under the cap for its head, notes, banner and footers
_INDEX_RESERVE = 1024  # room under the cap for the index's cut notes, its term list and the banner
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
    comments: list[str] = field(default_factory=list)  # page lines
    objects: list[str] = field(default_factory=list)  # page lines, a long chart title or pivot name cut
    comments_whole: list[str] = field(default_factory=list)  # each comment uncut and unescaped (its sidecar)
    objects_whole: list[str] = field(default_factory=list)  # each object's label uncut (its sidecar)
    labels_cut: bool = False  # a chart title or pivot name on the page was cut (B13 round 41 C2046)
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
        comment = None if fcell is None else getattr(fcell, "comment", None)
        if comment is not None and str(comment.text or "").strip():  # an empty cell's too (round 41 C2088)
            author = str(comment.author or "").strip()
            text = _WS_RE.sub(" ", str(comment.text)).strip()
            who = f" ({author})" if author else ""
            sheet.comments.append(f"- {_ref(*key)}{who}: {_escape_line(text)}")
            sheet.comments_whole.append(f"{_ref(*key)}{who}: {text}")
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
        where = f" at {w}" if (w := _anchor_ref(chart)) else ""
        kind = type(chart).__name__
        named, whole = (f' "{_label(title)}"', f' "{title}"') if title else ("", "")
        sheet.labels_cut |= len(title) > _LABEL_CHARS
        sheet.objects.append(
            f"- [chart {i}: {kind}{named}{where} — no markdown form; open the workbook to view]"
        )
        sheet.objects_whole.append(f"chart {i}: {kind}{whole}{where}")
    for pivot in getattr(wsf, "_pivots", []):
        name = str(getattr(pivot, "name", "") or "pivot")
        loc = getattr(getattr(pivot, "location", None), "ref", "") or ""
        at = f" at {loc}" if loc else ""
        sheet.labels_cut |= len(name) > _LABEL_CHARS
        sheet.objects.append(
            f"- [pivot table: {_label(name)}{at} — no markdown form; open the workbook to view]"
        )
        sheet.objects_whole.append(f"pivot table: {name}{at}")
    for i, image in enumerate(getattr(wsf, "_images", []), start=1):
        where = _anchor_ref(image)
        sheet.objects.append(f"- [image {i}" + (f" at {where}" if where else "") + "]")
        sheet.objects_whole.append(f"image {i}" + (f" at {where}" if where else ""))
    return sheet


def _label(text: str) -> str:
    """A chart title or pivot name for a sheet page's object line: cut to :data:`_LABEL_CHARS` characters
    ending ``…``, then escaped, so the line stays short and a cut never splits an escape; the whole name
    rides in the sheet's ``.objects.txt`` sidecar (B13 round 41 C2046)."""
    return _escape_line(_cut_value(text, _LABEL_CHARS))


# ---------------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------------


def _sheet_stem(number: int, width: int, title: str) -> str:
    """``NN-<sheet name>`` (publish slugs it)."""
    return f"{number:0{width}d}-{title}"


def _sidecar_name(number: int, width: int, title: str, kind: str = "csv") -> str:
    """Portable sidecar name for a sheet: its rows' ``.csv``, or its ``.comments.txt`` or ``.objects.txt``
    list (B13 round 41 C2043), which never collides with the rows' CSV."""
    return f"{number:0{width}d}-{_ascii_slug(title, fallback='sheet')}.{kind}"


def _table_lines(
    sheet: _Sheet, rows: list[int], cells: Mapping[tuple[int, int], str], budget: int, max_rows: int
) -> tuple[list[str], int, list[tuple[int, int]]]:
    """Render up to ``max_rows`` rows within ``budget`` bytes; return (lines, rows shown, cut rows).

    Every row counts against the budget, the first included, and a header that does not fit leaves no
    table, so the caller's sidecar holds every row (B13 round 39 C1676).  A row too long for the table by
    itself is shown with its cells cut, never dropped: rebuilt from ``sheet.plain``, each cell cut to the
    same number of characters ending ``…`` before :func:`_cell` escapes it, so no cut splits an escape or a
    formula's code span: an even share of the room left, halved until the row fits, kept as (row, characters)
    in the cut rows (B13 round 41 C2022).  The rows shown stay a
    run from the top: a row that fits the table but not the room left, or a cut row that still does not
    fit, ends it."""
    assert sheet.bounds is not None
    _r0, c0, _r1, c1 = sheet.bounds
    header = ["Row", *(_col_letter(c) for c in range(c0, c1 + 1))]
    lines = _gfm_table(header, [])
    used = start = sum(len(ln.encode("utf-8")) + 1 for ln in lines)
    if used > budget:
        return [], 0, []
    shown = 0
    cut: list[tuple[int, int]] = []
    for r in rows:
        if shown >= max_rows:
            break
        line = _gfm_table(header, [[str(r), *(cells.get((r, c), "") for c in range(c0, c1 + 1))]])[2]
        size = len(line.encode("utf-8")) + 1
        if used + size > budget:
            if start + size <= budget:
                break
            fitted = _cut_row(sheet, header, r, budget - used)
            if fitted is None:
                break
            line, limit = fitted
            size = len(line.encode("utf-8")) + 1
            cut.append((r, limit))
        lines.append(line)
        used += size
        shown += 1
    return lines, shown, cut


def _cut_row(sheet: _Sheet, header: list[str], r: int, room: int) -> tuple[str, int] | None:
    """Row ``r`` rebuilt from ``sheet.plain`` with each cell cut to one number of characters, an even share
    of ``room`` bytes (its line end included) halved until the row fits, with that number; None when one
    character a cell does not fit (B13 round 41 C2022)."""
    assert sheet.bounds is not None
    _r0, c0, _r1, c1 = sheet.bounds
    ncols = c1 - c0 + 1
    limit = max(1, (room - 5 - len(str(r)) - 3 * ncols) // ncols)
    while True:
        plain = [_cell(_cut_value(sheet.plain.get((r, c), ""), limit)) for c in range(c0, c1 + 1)]
        line = _gfm_table(header, [[str(r), *plain]])[2]
        if len(line.encode("utf-8")) + 1 <= room:
            return line, limit
        if limit == 1:
            return None
        limit //= 2


def _cut_value(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters ending ``…``; cut before :func:`_cell` escapes it, so a cut never
    splits a ``\\|`` escape, a ``<br>`` or a character (B13 round 40 C1848)."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fit(out: list[str], lines: list[str], room: int) -> int:
    """Append ``lines`` to ``out`` while the page so far stays within ``room`` bytes; return how many fit."""
    used = sum(len(x.encode("utf-8")) + 1 for x in out)
    kept = 0
    for line in lines:
        size = len(line.encode("utf-8")) + 1
        if used + size > room:
            break
        out.append(line)
        used += size
        kept += 1
    return kept


def _fit_table(out: list[str], table: list[str], room: int) -> int:
    """Append a GFM ``table`` to ``out`` within ``room`` bytes, a row at a time; return the rows listed.  A
    header or delimiter that does not fit leaves no table at all."""
    kept = _fit(out, table, room)
    if kept < 2:
        del out[len(out) - kept :]
        return 0
    return kept - 2


def _listed(out: list[str], heading: str, lines: list[str], *, room: int, what: str, where: str = "") -> int:
    """One list under ``heading``, its lines within ``room`` bytes, a cut note naming the count; return how
    many lines it lists."""
    out += [heading, ""]
    kept = _fit(out, lines, room)
    if kept < len(lines):
        out.append(f"[{what} truncated at the page size cap: {kept} of {len(lines)} listed{where}]")
    out.append("")
    return kept


def _cut_rows_note(cut: list[tuple[int, int]]) -> str:
    """The clause naming the rows shown with their cells cut, for the row-cap note (B13 round 41 C2022)."""
    if len(cut) == 1:
        return f"row {cut[0][0]}'s cells cut to {plural(cut[0][1], 'character')}"
    most = max(limit for _r, limit in cut)
    return f"the cells of {len(cut)} rows cut to {plural(most, 'character')} or fewer, row {cut[0][0]} first"


def _sheet_lists(
    out: list[str], sheet: _Sheet, *, width: int, cap: int, names: list[str]
) -> list[tuple[str, bytes]]:
    """Fit the sheet's comment and object lists in the room left under ``cap``; return their sidecars.

    Each list is fitted line by line with :func:`_listed` after the table, in the page's room less the
    guard's banner and footer lines (``names`` holds every sidecar the page may carry) and its own cut note,
    so the page stays within the cap.  A cut list's note names its sidecar,
    ``<NN>-<sheet-slug>.comments.txt`` or ``.objects.txt``, which holds every comment or object whole (a
    cut chart title or pivot name too), one a line (B13 round 41 C2043, C2064, C2046)."""
    room = cap - GUARD_RESERVE - sum(_footer_bytes(n) for n in names)
    sidecars: list[tuple[str, bytes]] = []
    lists = (
        ("## Comments", sheet.comments, sheet.comments_whole, "comment", False),
        ("## Charts, pivots and images", sheet.objects, sheet.objects_whole, "object", sheet.labels_cut),
    )
    shown = [entry for entry in lists if entry[1]]
    notes: list[str] = []  # each list's longest cut note: every one of its lines cut
    for _heading, lines, _whole, one, _labels_cut in shown:
        name = _sidecar_name(sheet.number, width, sheet.title, f"{one}s.txt")
        where = f"; every {one} is whole in the sidecar `{name}`"
        notes.append(
            f"[{one} list truncated at the page size cap: {len(lines)} of {len(lines)} listed{where}]"
        )
    # a list's fixed bytes: its heading and blank line, its note and the blank line after it
    fixed = [len(h.encode()) + len(note.encode()) + 4 for (h, *_rest), note in zip(shown, notes, strict=True)]
    for n, (heading, lines, whole, one, labels_cut) in enumerate(shown):
        name = _sidecar_name(sheet.number, width, sheet.title, f"{one}s.txt")
        where = f"; every {one} is whole in the sidecar `{name}`"
        room_left = room - len(notes[n].encode()) - 2 - sum(fixed[n + 1 :])  # a later list keeps its room
        kept = _listed(out, heading, lines, room=room_left, what=f"{one} list", where=where)
        if kept == len(lines) and labels_cut:  # the list fits, a long name in it does not (C2046)
            out[-1:] = [f"[a chart title or pivot name cut to {_LABEL_CHARS} characters{where}]", ""]
        if kept < len(lines) or labels_cut:
            sidecars.append((name, ("\n".join(whole) + "\n").encode("utf-8")))
    return sidecars


def _sheet_body(
    sheet: _Sheet, *, total: int, width: int, cfg: ConvertConfig
) -> tuple[str, tuple[tuple[str, bytes], ...]]:
    """Markdown for one sheet unit plus its sidecars: the rows' CSV when rows were capped or cut, and a
    list's ``.comments.txt`` or ``.objects.txt`` when that list was cut.

    The comment and object sections are measured first: the table's budget is the cap less
    :data:`_SHEET_RESERVE` less their real bytes, up to a quarter of the cap, with no guess per item and no
    floor, so a sheet with many short comments keeps its rows and one with long ones keeps a share for them;
    each list then fits the room left (:func:`_sheet_lists`), so the page stays within any cap the config
    accepts (B13 round 41 C2043, C2088, C2064)."""
    hidden = " (hidden sheet)" if sheet.hidden else ""
    out = [f"# Sheet: {_cut_value(sheet.title, _LABEL_CHARS)} (used range {sheet.used_range}){hidden}", ""]
    rows = sheet.rows()
    ncols = 0 if sheet.bounds is None else sheet.bounds[3] - sheet.bounds[1] + 1
    out.append(
        f"Sheet {sheet.number} of {total} in this workbook · {plural(len(rows), 'non-empty row')} x "
        f"{plural(ncols, 'column')}"
    )
    out.append("")
    cap = cfg.max_page_bytes
    sc = _sidecar_name(sheet.number, width, sheet.title)
    sidecars: list[tuple[str, bytes]] = []
    if not rows:
        out += ["[empty sheet]", ""]
    else:
        sections = [*(["## Comments", "", *sheet.comments, ""] if sheet.comments else [])]
        sections += ["## Charts, pivots and images", "", *sheet.objects, ""] if sheet.objects else []
        tail = sum(len(x.encode("utf-8")) + 1 for x in sections)
        budget = max(0, cap - _SHEET_RESERVE - min(tail, cap // 4))  # no floor (B13 round 40 C1886)
        lines, shown, cut = _table_lines(sheet, rows, sheet.shown, budget, cfg.max_rows_per_sheet)
        out += lines
        out.append("")
        if shown < len(rows) or cut:
            assert sheet.bounds is not None
            _r0, c0, _r1, c1 = sheet.bounds
            header = ["Row", *(_col_letter(c) for c in range(c0, c1 + 1))]
            data = [[str(r), *(sheet.plain.get((r, c), "") for c in range(c0, c1 + 1))] for r in rows]
            sidecars.append((sc, _csv_bytes([header, *data])))
            out += [
                f"[row cap: first {shown} of {plural(len(rows), 'non-empty row')} shown"
                + (f", {_cut_rows_note(cut)}; " if cut else "; ")
                + f"every row is in the sidecar `{sc}`]",
                "",
            ]
    if sheet.missing_cache:
        out += [
            f"[no cached values: {_uncached(sheet)} no stored "
            "result (the workbook was last written by a tool that does not calculate, e.g. a script "
            f"or BI export); {_uncached_cells(sheet)} the formula only, never a guessed number]",
            "",
        ]
    names = [
        sc,
        *(_sidecar_name(sheet.number, width, sheet.title, k) for k in ("comments.txt", "objects.txt")),
    ]
    sidecars += _sheet_lists(out, sheet, width=width, cap=cap, names=names)
    return "\n".join(out), tuple(sidecars)


def _index_body(
    sheets: list[_Sheet],
    *,
    chartsheets: list[str],
    defined: list[tuple[str, str]],
    streamed: bool,
    max_bytes: int,
) -> str:
    """The workbook's grep-recall surface: every sheet, its range and headers, names, objects, terms.

    Each list counts against one running total within ``max_bytes`` less :data:`_INDEX_RESERVE`, and a
    sheet's headers cell is cut before escaping to :data:`_HEADER_CHARS` characters or, when more, a fourth
    of the room past 2,048 bytes shared by the sheets (B13 round 41 C2045), so the page stays within the
    cap with any number of sheets; a list past it ends in a note, the sheet list's naming each sheet's own
    page or its CSV sidecar, one of which holds its headers (B13 round 40 C1848, round 41 C2047).  A list
    under the budget whose headers cells are within that limit renders byte for byte as before; a longer
    cell ends ``…`` at any cap, its whole text on the sheet's page or in its sidecar."""
    out = ["# Workbook index", ""]
    room = max_bytes - _INDEX_RESERVE
    shares = max(1, len(sheets))  # 200 bytes a row for its other cells and pipes, then 4 bytes a character
    head_chars = max(_HEADER_CHARS, (room - 2048 - 200 * shares) // (4 * shares))
    mode = (
        " Streamed (above the size threshold): each sheet page is a schema + sample summary."
        if streamed
        else ""
    )
    out += [
        f"Workbook with {plural(len(sheets), 'worksheet')}; each sheet is its own page next to this index."
        f"{mode}",
        "",
    ]
    rows = []
    for s in sheets:
        heads = _join_limited(s.headers()[:_HEADER_LIMIT], limit=head_chars, sep=", ")
        title = s.title + (" (hidden)" if s.hidden else "")
        ncols = 0 if s.bounds is None else s.bounds[3] - s.bounds[1] + 1
        rows.append([str(s.number), _cell(title), s.used_range, str(len(s.rows())), str(ncols), _cell(heads)])
    if rows:
        table = _gfm_table(["#", "Sheet", "Used range", "Rows", "Columns", "Column headers"], rows)
        listed = _fit_table(out, table, room)
        if listed < len(rows):
            sheets_n = plural(len(rows), "sheet")
            out.append(
                f"[sheet list truncated at the page size cap: {listed} of {sheets_n} listed; "
                "each sheet's own page or its CSV sidecar has its headers]"
            )
        out.append("")
    if chartsheets:
        charts = [f"- [chartsheet: {_escape_line(c)} — open the workbook to view]" for c in chartsheets]
        _listed(out, "## Chart sheets", charts, room=room, what="chart sheet list")
    if defined:
        names = [f"- {n}: {_code(v)}" for n, v in defined]
        _listed(out, "## Defined names", names, room=room, what="defined-name list")
    objects = [f"- {s.title}: {line[2:]}" for s in sheets for line in s.objects]
    if objects:
        where = "; each sheet's own page lists its own"
        _listed(out, "## Charts, pivots and images", objects, room=room, what="object list", where=where)
    counter: Counter[str] = Counter()
    for s in sheets:
        counter.update(s.terms)
    if counter:
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].casefold(), kv[0]))[:_INDEX_TERM_LIMIT]
        terms = sorted((t for t, _ in ranked), key=lambda t: (t.casefold(), t))
        dropped = len(counter) - len(terms)
        note = f" ({plural(dropped, 'rarer term')} omitted)" if dropped > 0 else ""  # B13 round 17 U75
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
    return f"Workbook: {plural(len(sheets), 'sheet')}: " + _join_limited(parts, limit=170)


def _index_title(sheets: list[_Sheet]) -> str:
    """Name-independent title for 00-index (the workbook's own file name lives in source_path)."""
    names = [s.title for s in sheets]
    return (
        "Workbook: " + _join_limited(names, limit=150, sep=", ") if names else "Workbook without worksheets"
    )


def _uncached(sheet: _Sheet) -> str:
    """``1 of 1 formula cell carries`` or ``2 of 5 formula cells carry``: the count of formula cells with no
    stored result, its noun from the formula count and its verb from its own (B13 round 16 T61)."""
    carry = "carries" if sheet.missing_cache == 1 else "carry"
    return f"{sheet.missing_cache} of {plural(sheet.formulas, 'formula cell')} {carry}"


def _uncached_cells(sheet: _Sheet) -> str:
    """``that cell shows`` or ``those cells show``: the cells with no stored result, by their own count like
    :func:`_uncached`'s verb, not by the formula count (B13 round 17 U74)."""
    return "that cell shows" if sheet.missing_cache == 1 else "those cells show"


def _sheet_summary(sheet: _Sheet) -> str:
    """Converter-derived summary of one sheet."""
    ncols = 0 if sheet.bounds is None else sheet.bounds[3] - sheet.bounds[1] + 1
    nrows = len(sheet.rows())
    size = f"{plural(nrows, 'row')} x {plural(ncols, 'column')}"
    base = f"Sheet {sheet.title}: used range {sheet.used_range}, {size}"
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
                sheet.plain[(r, c)] = row_plain[c]  # a sampled row cut to fit is rebuilt from it (C2022)
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
    cols = max(1, len(types))  # the page's head and 128 bytes a row besides, then 4 bytes a character (C2045)
    first_chars = max(_FIRST_ROW_CHARS, (cfg.max_page_bytes - 4096 - 1024 - 128 * cols) // (4 * cols))
    schema_rows = []
    for c in sorted(types):
        tlabel = ", ".join(f"{k} {n}" for k, n in sorted(types[c].items(), key=lambda kv: (-kv[1], kv[0])))
        first = _cell(_cut_value(heads.get(c, ""), first_chars))
        schema_rows.append([_col_letter(c), first, tlabel, str(nonempty[c])])
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

    def outdated(self, produced: str, reason: str | None = None) -> bool:
        """Never: an unchanged workbook is not converted again. A stub an older build wrote a staging or home
        path into is a failed conversion's, which the cycle's one-shot re-read reaches (``cycle._retries``,
        B13 round 29 C181; this rule, B13 round 28 I39's, never saw one)."""
        return False

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
        _check_ooxml_family(src, "spreadsheet", "a workbook")
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
            body, sidecars = _sheet_body(s, total=total, width=width, cfg=self._cfg)
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
            title = _cut_value(sheet.title, _LABEL_CHARS)  # openpyxl keeps a title past Excel's 31 (C2043)
            out = [f"# Sheet: {title} (used range {sheet.used_range}; streamed summary){hidden}", ""]
            out += [
                f"Sheet {sheet.number} of {total} in this workbook · {plural(total_rows, 'non-empty row')} · "
                "streamed "
                "because the file is above the size threshold: merged cells, comments and charts are "
                "not analysed; "
                f"every row is in the sidecar `{sc}`",
                "",
            ]
            if schema:  # within the cap less the rows' reserve; the rows' budget is what is left (C1848)
                out += ["## Schema", ""]
                columns = len(schema) - 2
                listed = _fit_table(out, schema, self._cfg.max_page_bytes - 4096)
                if listed < columns:
                    out.append(
                        f"[schema cut at the page size cap: {listed} of {plural(columns, 'column')} listed; "
                        f"every cell is in the sidecar `{sc}`]"
                    )
                out.append("")
            rows = sheet.rows()
            if rows:
                used = sum(len(x.encode("utf-8")) + 1 for x in out)
                budget = max(0, self._cfg.max_page_bytes - 4096 - used)
                lines, shown, cut = _table_lines(
                    sheet, rows, sheet.shown, budget, self._cfg.max_rows_per_sheet
                )
                if shown:
                    out += [f"## First {shown} of {plural(total_rows, 'non-empty row')}", "", *lines, ""]
                    if cut:  # a row too long for the page shows with its cells cut (B13 round 41 C2022)
                        out += [
                            f"[{_cut_rows_note(cut)}; every row is in the sidecar `{sc}`]",
                            "",
                        ]
                else:  # the header is past the page cap (B13 round 39 C1676)
                    out += [f"[no row fits the page; every row is in the sidecar `{sc}`]", ""]
            else:
                out += ["[empty sheet]", ""]
            if sheet.missing_cache:
                out += [
                    f"[no cached values: {_uncached(sheet)} "
                    f"no stored result; {_uncached_cells(sheet)} the formula only]",
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
