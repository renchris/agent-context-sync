"""Converter: plain text: decoded (UTF-8/UTF-16 BOM first, then strict UTF-8, then cp1252, else latin-1)
into a fenced block (owner: convert)."""

from __future__ import annotations

import csv
import io
import re
import threading
from collections.abc import Mapping
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    GUARD_RESERVE,
    _cell,
    _csv_bytes,
    _decode_text,
    _fence,
    _gfm_table,
    _join_limited,
    _longest_run,
    _python_version,
    _unicode_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind
from agentsync.text import plural

_EMITTER_VERSION = (
    "1.0.4"  # B13 round 42 C2302, ruling 153: a CSV row is one record, a capped page shows its room
)
_TABLE_SIDECAR = "full-table.csv"
_FENCE_LANG: dict[str, str] = {
    ".txt": "text",
    ".log": "text",
    ".json": "json",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".tsv": "tsv",
    ".url": "ini",
}
_SHORTCUT_SECTION = "[internetshortcut]"
_FIELD_LIMIT_LOCK = threading.Lock()  # the csv field limit's read and raise (B13 round 41 C2049)


def _suffix(name: str) -> str:
    """Lower-case last extension including the dot, or ""."""
    low = name.lower()
    dot = low.rfind(".")
    return low[dot:] if dot > 0 else ""


def _shortcut_target(lines: list[str]) -> str:
    """The ``URL=`` value of an internet shortcut's ``[InternetShortcut]`` section, stripped; "" when it has
    none.  Keys and the section name are matched without case, as Windows reads them."""
    section = ""
    for line in lines:
        text = line.strip()
        key, eq, value = text.partition("=")
        if text.startswith("[") and text.endswith("]"):
            section = text.casefold()
        elif section == _SHORTCUT_SECTION and eq and key.strip().casefold() == "url":
            return value.strip()
    return ""


def _parse_delimited(text: str, delimiter: str) -> list[list[str]] | None:
    """Parse CSV/TSV rows; None when the text is not well-formed delimited data (then it is fenced).

    No field is longer than the text, so the csv module's field limit (131,072 by default, process-wide) is
    raised to at least ``len(text) + 1`` first: a well-formed file with a long field is a table, not text.  It
    is never lowered and never ``sys.maxsize``, which can pass a C long (B13 round 40 C1807, C1822).  The
    read and the raise are one step under :data:`_FIELD_LIMIT_LOCK`, so two parses on two threads cannot
    undo each other here (code elsewhere that sets the limit itself still can); the parse runs outside it,
    so parses never wait on each other (B13 round 41 C2049)."""
    with _FIELD_LIMIT_LOCK:
        if csv.field_size_limit() <= len(text):
            csv.field_size_limit(len(text) + 1)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        return [row for row in reader if row]
    except csv.Error:
        return None


def _raw_head(text: str, lang: str, room: int) -> str:
    """A fenced head of the delimited ``text`` within ``room`` bytes: cut at the last line end that fits,
    else at a character boundary (B13 round 39 C1676).  The fence is sized from a first cut, never from
    backticks further down, which the head does not hold."""
    raw = text.encode("utf-8")

    def cut(ticks: int) -> str:  # both fence lines, the language and the line ends
        return raw[: max(0, room - 2 * ticks - len(lang) - 3)].decode("utf-8", errors="ignore")

    first = cut(3)
    head = cut(max(3, max((len(m.group(0)) for m in re.finditer(r"`+", first)), default=0) + 1))
    end = head.rfind("\n")
    if end > 0:
        head = head[: end + 1]
    return _fence(head, lang).rstrip("\n")


class PlainTextConverter:
    """Plain text: decoded (UTF-8/UTF-16 BOM first, then strict UTF-8, then cp1252, else latin-1) into a
    fenced block.

    The body is the text inside a fenced block (``csv``/``tsv`` rendered as a GFM table up to
    max_rows_per_sheet rows).  NUL bytes -> ConversionError (binary).  No file-name heading: the action key
    does not include the name and a rename keeps bodies, so output depends on the bytes and suffix only.
    A ``.url`` internet shortcut is fenced as ``ini``; its title is left empty so the page takes the file's
    name, and its summary names the ``URL=`` target.
    """

    converter_id = "text-plain"
    extensions: tuple[str, ...] = (".txt", ".csv", ".tsv", ".log", ".json", ".xml", ".yaml", ".yml", ".url")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+python-{_python_version()}+unicode-{_unicode_version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {
            "decode": "bom,utf-8,cp1252,latin-1",
            "max_page_bytes": self._cfg.max_page_bytes,
            "max_rows_per_sheet": self._cfg.max_rows_per_sheet,
        }

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise ConversionError(f"cannot read: {exc}") from exc
        text = _decode_text(data)
        suffix = _suffix(name)
        lines = text.split("\n")
        if text.endswith("\n"):
            lines = lines[:-1]
        sidecars: tuple[tuple[str, bytes], ...] = ()
        table = None
        if suffix in (".csv", ".tsv") and text.strip():
            table = _parse_delimited(text, "," if suffix == ".csv" else "\t")
        if table:
            body, sidecars, title, summary = self._table(table, suffix[1:].upper(), text)
        else:
            lang = _FENCE_LANG.get(suffix, "text")
            body = "[empty file]\n" if not text.strip() else _fence(text, lang)
            if len(body.encode("utf-8")) > self._cfg.max_page_bytes - GUARD_RESERVE:  # B13 round 41 C2044
                body, sidecars = self._capped(text, lang)
            first = next((ln.strip() for ln in lines if ln.strip()), "")
            title = first[:120] if first else "Empty text file"
            summary = f"Text ({suffix[1:] or 'no extension'}): {plural(len(lines), 'line')}"
            if suffix == ".url":
                target = _shortcut_target(lines)
                title = ""
                summary = f"Internet shortcut to {target}" if target else "Internet shortcut with no URL"
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

    def _table(
        self, table: list[list[str]], kind: str, text: str
    ) -> tuple[str, tuple[tuple[str, bytes], ...], str, str]:
        """Render delimited data as a GFM table (row- and byte-capped, full CSV sidecar when capped).

        Every line counts against the budget, the header, its delimiter and the first row included: when
        those three alone do not fit, no GFM table is shown (a cut inside one can split a ``\\|`` escape, a
        ``<br>`` or a character), only a fenced head of the raw text cut at a line or character boundary,
        and the sidecar holds every row (B13 round 39 C1676); its note counts no rows, since the head of a
        wide table of short fields can hold whole records (B13 round 40 C1849).  The budget leaves 1,024
        bytes for the note, the banner and the sidecar digest footer, so a CSV or TSV page stays within any
        ``max_page_bytes`` the config accepts (8 KiB or more, ``config.MIN_PAGE_BYTES``); a ConvertConfig
        built below that gets a shorter head, never a floor (B13 round 40 C1886, C1851)."""
        header, rows = table[0], table[1:]
        shown = rows[: self._cfg.max_rows_per_sheet]
        out = [f"{kind} table: {plural(len(rows), 'data row')} x {plural(len(header), 'column')}", ""]
        table_lines = _gfm_table([_cell(h) for h in header], ([_cell(c) for c in r] for r in shown))
        budget = max(0, self._cfg.max_page_bytes - 1024)
        used = sum(len(x.encode("utf-8")) + 1 for x in out)
        sizes = [len(line.encode("utf-8")) + 1 for line in table_lines]
        cut = used + sum(sizes[:3]) > budget
        kept: list[str] = []
        if cut:
            out.append(_raw_head(text, kind.lower(), budget - used))
        else:
            for line, size in zip(table_lines, sizes, strict=True):
                if len(kept) > 2 and used + size > budget:
                    break
                kept.append(line)
                used += size
        rows_kept = max(0, len(kept) - 2)
        out += kept
        sidecars: tuple[tuple[str, bytes], ...] = ()
        if cut and not rows:
            sidecars = ((_TABLE_SIDECAR, _csv_bytes(table)),)
            out += ["", f"[header past the page cap: the whole header is in the sidecar `{_TABLE_SIDECAR}`]"]
        elif cut:  # the raw head may hold whole records; no count of them (B13 round 40 C1849)
            sidecars = ((_TABLE_SIDECAR, _csv_bytes(table)),)
            out += [
                "",
                "[table past the page cap: the raw head above shows its start; every row is in the sidecar "
                f"`{_TABLE_SIDECAR}`]",
            ]
        elif rows_kept < len(rows):
            sidecars = ((_TABLE_SIDECAR, _csv_bytes(table)),)
            out += [
                "",
                f"[row cap: first {rows_kept} of {plural(len(rows), 'row')} shown; every row is in the "
                f"sidecar `{_TABLE_SIDECAR}`]",
            ]
        columns = _join_limited(header, limit=140, sep=", ")
        title = f"{kind} table: {columns}" if columns else f"{kind} table"
        summary = f"{kind} table: {plural(len(rows), 'row')} x {plural(len(header), 'column')}" + (
            f"; columns: {columns}" if columns else ""
        )
        return "\n".join(out), sidecars, title, summary

    def _capped(self, text: str, lang: str) -> tuple[str, tuple[tuple[str, bytes], ...]]:
        """Fence only the first lines that fit ``max_page_bytes``; the whole text rides as a sidecar.

        The 512-byte reserve holds the note, the banner, the sidecar digest footer and a three-backtick fence
        on both sides.  A head whose longest backtick run R is 3 or more needs a fence of R + 1, so it is cut
        again 2 * (R + 1 - 3) bytes shorter: a prefix of the first cut, whose run, and so whose fence, is no
        longer, and the page stays within the cap.  A head with no such run keeps its bytes (B13 round 41
        C2042, C2087)."""
        raw = text.encode("utf-8")
        budget = max(0, self._cfg.max_page_bytes - 512)
        head = raw[:budget].decode("utf-8", errors="ignore")
        ticks = _longest_run(head, "`")
        if ticks >= 3:
            head = raw[: max(0, budget - 2 * (ticks + 1 - 3))].decode("utf-8", errors="ignore")
        cut = head.rfind("\n")
        if cut > 0:
            head = head[: cut + 1]
        shown = len(head.encode("utf-8"))
        note = (
            f"[truncated: first {shown} of {len(raw)} bytes shown; the full text is in the sidecar "
            f"`{_FULL_TEXT_SIDECAR}`]"
        )
        return f"{_fence(head, lang)}\n{note}\n", ((_FULL_TEXT_SIDECAR, raw),)
