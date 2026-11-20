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
