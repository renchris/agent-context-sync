"""Converter: plain text: decoded (utf-8, then utf-8-sig/utf-16 BOM, else latin-1) into a fenced block
(owner: convert)."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cell,
    _csv_bytes,
    _decode_text,
    _fence,
    _gfm_table,
    _join_limited,
    _python_version,
    _unicode_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
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
}


def _suffix(name: str) -> str:
    """Lower-case last extension including the dot, or ""."""
    low = name.lower()
    dot = low.rfind(".")
    return low[dot:] if dot > 0 else ""


def _parse_delimited(text: str, delimiter: str) -> list[list[str]] | None:
    """Parse CSV/TSV rows; None when the text is not well-formed delimited data (then it is fenced)."""
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        return [row for row in reader if row]
    except csv.Error:
        return None


class PlainTextConverter:
    """Plain text: decoded (utf-8, then utf-8-sig/utf-16 BOM, else latin-1) into a fenced block.

    The body is the text inside a fenced block (``csv``/``tsv`` rendered as a GFM table up to
    max_rows_per_sheet rows).  NUL bytes -> ConversionError (binary).  No file-name heading: the action key
    does not include the name and a rename keeps bodies, so output depends on the bytes and suffix only.
    """

    converter_id = "text-plain"
    extensions: tuple[str, ...] = (".txt", ".csv", ".tsv", ".log", ".json", ".xml", ".yaml", ".yml")

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
            body, sidecars, title, summary = self._table(table, suffix[1:].upper())
        else:
            lang = _FENCE_LANG.get(suffix, "text")
            body = "[empty file]\n" if not text.strip() else _fence(text, lang)
            if len(body.encode("utf-8")) > self._cfg.max_page_bytes:
                body, sidecars = self._capped(text, lang)
            first = next((ln.strip() for ln in lines if ln.strip()), "")
            title = first[:120] if first else "Empty text file"
            summary = f"Text ({suffix[1:] or 'no extension'}): {len(lines)} line(s)"
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
        self, table: list[list[str]], kind: str
    ) -> tuple[str, tuple[tuple[str, bytes], ...], str, str]:
        """Render delimited data as a GFM table (row- and byte-capped, full CSV sidecar when capped)."""
        header, rows = table[0], table[1:]
        shown = rows[: self._cfg.max_rows_per_sheet]
        out = [f"{kind} table: {len(rows)} data rows x {len(header)} columns", ""]
        table_lines = _gfm_table([_cell(h) for h in header], ([_cell(c) for c in r] for r in shown))
        budget = max(1024, self._cfg.max_page_bytes - 1024)
        used = sum(len(x.encode("utf-8")) + 1 for x in out)
        kept: list[str] = []
        for i, line in enumerate(table_lines):
            size = len(line.encode("utf-8")) + 1
            if i > 2 and used + size > budget:
                break
            kept.append(line)
            used += size
        rows_kept = max(0, len(kept) - 2)
        out += kept
        sidecars: tuple[tuple[str, bytes], ...] = ()
        if rows_kept < len(rows):
            sidecars = ((_TABLE_SIDECAR, _csv_bytes(table)),)
            out += [
                "",
                f"[row cap: first {rows_kept} of {len(rows)} rows shown; every row is in the sidecar "
                f"`{_TABLE_SIDECAR}`]",
            ]
        columns = _join_limited(header, limit=140, sep=", ")
        title = f"{kind} table: {columns}" if columns else f"{kind} table"
        summary = f"{kind} table: {len(rows)} rows x {len(header)} columns" + (
            f"; columns: {columns}" if columns else ""
        )
        return "\n".join(out), sidecars, title, summary

    def _capped(self, text: str, lang: str) -> tuple[str, tuple[tuple[str, bytes], ...]]:
        """Fence only the first lines that fit ``max_page_bytes``; the whole text rides as a sidecar."""
        raw = text.encode("utf-8")
        budget = max(0, self._cfg.max_page_bytes - 512)
        head = raw[:budget].decode("utf-8", errors="ignore")
        cut = head.rfind("\n")
        if cut > 0:
            head = head[: cut + 1]
        shown = len(head.encode("utf-8"))
        note = (
            f"[truncated: first {shown} of {len(raw)} bytes shown; the full text is in the sidecar "
            f"`{_FULL_TEXT_SIDECAR}`]"
        )
        return f"{_fence(head, lang)}\n{note}\n", ((_FULL_TEXT_SIDECAR, raw),)
