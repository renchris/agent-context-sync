"""B13 round 41 lane E: every page a converter writes stays within max_page_bytes, measured on the guarded
page's real bytes (ruling 143), and the round's other converter items (C2001, C2048, C2049)."""

from __future__ import annotations

import csv
import io
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment

from agentsync import policy, setup_report
from agentsync.config import ConvertConfig
from agentsync.convert import _common, registry, xlsx
from agentsync.convert import text as text_mod
from agentsync.convert._common import GUARD_RESERVE, _cap_body, _fence
from agentsync.convert.base import make_unit
from agentsync.convert.pandoc import PandocConverter
from agentsync.convert.registry import Registry, _bannered, _with_sidecar_digests
from agentsync.convert.xlsx import _record_escape
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.frontmatter import MirrorFrontmatter, render_mirror_page
from agentsync.model import PageStatus, RenderedUnit, UnitKind
from test_convert_builders import (
    build_drawio,
    build_pdf,
    eml_bytes,
    make_zip,
    msg_bytes,
    teams_doc,
    teams_msg,
    with_cached_values,
)

CFG = ConvertConfig()


def _pages(src: Path, cap: int, **kw: object) -> tuple[RenderedUnit, ...]:
    """Every guarded page of ``src`` through Registry.default at ``cap``."""
    cfg = replace(CFG, max_page_bytes=cap, **kw)  # type: ignore[arg-type]
    return Registry.default(cfg).for_name(src.name).convert(src, name=src.name)  # type: ignore[union-attr]


def _size(unit: RenderedUnit) -> int:
    return len(unit.body.encode("utf-8"))


def _filler(n: int) -> str:
    return ("Contoso filler line of plain words\n" * (n // 35 + 1))[:n]


# ---------------------------------------------------------------------------------------------------------
# C2042 with C2087: a fence a backtick or tilde run outgrows
# ---------------------------------------------------------------------------------------------------------

_LONG = "Contoso line of text\n" * 60_000  # past 1,000,000 bytes, so both caps cut


@pytest.mark.parametrize("cap", [8192, 20_000, 1_000_000])
@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("first.txt", "`" * 2000 + "\n" + _LONG),
        ("later.log", "Contoso\n" * 10 + "`" * 3000 + "\n" + _LONG),
        ("open.md", "# Contoso\n\n" + "`" * 3000 + "\n" + _LONG),
        ("tilde.md", "# Contoso\n\n" + "~" * 3000 + "\n" + _LONG),
        ("one.txt", "`" * 10_000),
        ("abc.txt", "abc\n" + "`" * 9000),
        ("three.json", ("`" * 3000 + "\n") * 3),
        ("mixed.txt", ("~" * 2000 + "\n" + "`" * 2500 + "\n") * 3),
    ],
    ids=["first", "later", "open-fence", "tilde-fence", "one-line", "abc", "three-json", "mixed"],
)
def test_a_long_backtick_run_never_pushes_a_page_past_the_cap_c2042(
    tmp_path: Path, cap: int, name: str, text: str
) -> None:
    """B13 round 41 C2042, C2087: `_capped` fenced its head with a fence one backtick longer than its longest
    run outside its 512-byte reserve, and `_cap_body` closed an open fence with the opener's whole marker, so
    a 2,000-backtick first line gave a 12,014-byte page at 8,192 and 1,003,809 at 1,000,000. The head is cut
    again by the excess, so every guarded page, banner and footer included, stays within the cap, and a
    one-line run longer than the room still leaves a page that says what it holds."""
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    (unit,) = _pages(src, cap)
    assert _size(unit) <= cap
    assert unit.body.strip()
    if len(text.encode()) + 64 > cap:
        assert "[truncated: first " in unit.body and unit.sidecars


def test_a_capped_page_with_no_backtick_run_keeps_its_bytes_c2042(tmp_path: Path) -> None:
    """B13 round 41 C2042: the recut only runs for a head with a run of 3 or more backticks, so an ordinary
    capped text page keeps the bytes 512-byte reserve gave it."""
    cap = 8192
    src = tmp_path / "plain.txt"
    src.write_text(_LONG, encoding="utf-8")
    raw = _LONG.encode()
    head = raw[: cap - 512].decode()
    head = head[: head.rfind("\n") + 1]
    note = (
        f"[truncated: first {len(head.encode())} of {len(raw)} bytes shown; the full text is in the sidecar "
        "`full-text.txt`]"
    )
    unit = text_mod.PlainTextConverter(replace(CFG, max_page_bytes=cap)).convert(src, name="plain.txt")[0]
    assert unit.body == f"{_fence(head, 'text')}\n{note}\n"


@pytest.mark.parametrize("cap", [8192, 20_000])
def test_a_capped_text_head_grows_line_by_line_while_its_fence_fits_c2208(tmp_path: Path, cap: int) -> None:
    """B13 round 42 C2208: a 9,000-backtick second line shrank the recut to 0 bytes, so the page showed an
    empty fence though `x` before it fits easily. The cut grows a whole line at a time while the longer head
    and its own fence fit: `x` shows, and a head of two long runs never takes a line whose fence would pass
    the cap (cutting at the run's line start gave 9,104 bytes at 8,192)."""
    src = tmp_path / "ticks.txt"
    src.write_text("x\n" + "`" * 9000 + "\nmore\n", encoding="utf-8")
    (unit,) = _pages(src, cap)
    assert _size(unit) <= cap
    assert "```text\nx\n```\n" in unit.body  # the run's 9,001-backtick fence leaves its line no room
    assert "[truncated: first 2 of 9008 bytes shown;" in unit.body
    two = tmp_path / "two.txt"
    two.write_text("`" * 3000 + "\n" + "`" * 4000 + "\n" + "y" * 3000 + "\n", encoding="utf-8")
    (page,) = _pages(two, cap)
    assert _size(page) <= cap
    body = text_mod.PlainTextConverter(replace(CFG, max_page_bytes=cap)).convert(two, name="two.txt")[0].body
    assert len(body.encode()) <= cap - GUARD_RESERVE
    if cap == 20_000:  # room for both runs and their fence: every line shows
        assert "y" * 3000 in page.body


# ---------------------------------------------------------------------------------------------------------
# C2044: the guard's banner and footer come off every fit test
# ---------------------------------------------------------------------------------------------------------


def test_the_guard_reserve_holds_the_banner_and_one_footer_line_c2044() -> None:
    """B13 round 41 C2044: GUARD_RESERVE is what the guard adds to a page with one sidecar, less the
    sidecar's name, and each other sidecar adds `_footer_bytes(name)`; the footer's line head is
    registry's own."""
    assert registry.SIDECAR_DIGEST_PREFIX == _common._DIGEST_LINE_HEAD
    for others in ((), ("t00m01s.jpg", "t00m02s.jpg")):
        body = "Contoso"  # no line end: the banner's page adds one
        sidecars = (("full-text.txt", b"x"), *((n, b"\xff") for n in others))
        unit = make_unit(
            unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="", title="t",
            summary="s", body=body, sidecars=sidecars,
        )  # fmt: skip
        guarded = _with_sidecar_digests(_bannered(unit))
        added = _size(guarded) - len(body)
        want = GUARD_RESERVE + len("full-text.txt") + sum(_common._footer_bytes(n) for n in others)
        assert want - 1 <= added <= want  # the reserve counts a line end the footer's rstrip takes back


@pytest.mark.parametrize("cap", [8192, 20_000])
def test_a_cap_body_page_with_other_sidecars_stays_within_the_cap_c2044(cap: int) -> None:
    """B13 round 41 C2044: `_cap_body` (every converter's but text's and xlsx's: eml, msg, pdf, pptx, vtt,
    diagram, a recording window, image, markdown, pandoc, teams) takes the guard's room, and a recording
    window's keyframe footers, off both its fit test and its cut, so a body within 1 to 150 bytes of the cap
    is cut and the guarded page stays within it, uncut or cut."""
    keyframes = ("t00m01s.jpg", "t00m02s.jpg", "t00m03s.jpg")
    for others in ((), keyframes):
        for delta in (-150, -75, -1, 1, 75, 150):
            body = _filler(cap + delta)
            capped, full = _cap_body(body, cap, sidecar_name="full-text.txt", others=others)
            sidecars = (*full, *((n, b"\xff\xd8") for n in others))
            unit = make_unit(
                unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="", title="t",
                summary="s", body=capped, sidecars=sidecars,
            )  # fmt: skip
            assert _size(_with_sidecar_digests(_bannered(unit))) <= cap, (others, delta)


def _guarded(body: str, cap: int, others: tuple[str, ...] = ()) -> tuple[str, int]:
    """``body`` capped at ``cap`` and its guarded page's size, ``others`` as keyframe sidecars."""
    capped, full = _cap_body(body, cap, sidecar_name="full-text.txt", others=others)
    sidecars = (*full, *((n, b"\xff\xd8") for n in others))
    unit = make_unit(
        unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="", title="t",
        summary="s", body=capped, sidecars=sidecars,
    )  # fmt: skip
    return capped, _size(_with_sidecar_digests(_bannered(unit)))


_ROOM_8192 = 8192 - GUARD_RESERVE - 2 * len("full-text.txt") - 200  # _cap_body's budget at 8,192


@pytest.mark.parametrize(
    ("shape", "body", "kept"),
    [
        ("heading", "## Page 1\n\n" + "Contoso plan words " * 900, "Contoso plan words Contoso"),
        ("json", "```json\n" + '{"k": "Contoso value"} ' * 600 + "\n```\n", '{"k": "Contoso value"}'),
        (
            "long-fence",
            "`" * 4000 + "python\n" + "print('Contoso')\n" * 600 + "`" * 4000 + "\n",
            "print('Contoso')",
        ),
    ],
    ids=["heading", "json", "long-fence"],
)
def test_a_cut_past_a_long_line_or_a_long_fence_shows_half_its_room_c2272(
    shape: str, body: str, kept: str
) -> None:
    """B13 round 42 C2272: `_line_head` kept the last line end whatever it cost, so a heading before one
    16 KB line showed 10 bytes and a fenced one-line JSON 7 (the opener), and a 4,000-backtick fence's opener
    and close left its content no room. The cut keeps a line end only when it keeps half the budget, else
    cuts at a blank in the budget's second half or a character, and an open fence longer than half the
    budget gives way to a short one, its info string kept: each shows at least half its room."""
    capped, size = _guarded(body, 8192)
    assert size <= 8192, shape
    head = capped.split("\n\n[truncated: first ")[0]
    assert 2 * len(head.encode()) >= _ROOM_8192 and kept in head, (shape, head[:200])
    shown = int(capped.split("[truncated: first ")[1].split(" ")[0])
    assert shown <= len(body.encode()) and 2 * shown >= _ROOM_8192
    if shape == "long-fence":
        assert head.startswith("```python\n") and head.endswith("\n```") and "````" not in head
    if shape == "json":
        assert head.startswith("```json\n") and head.endswith("\n```")


def test_a_mid_line_cut_never_ends_inside_an_escape_c2272() -> None:
    """B13 round 42 C2272: a mid-line cut drops a trailing lone backslash, so it never ends inside `\\*`."""
    for pad in range(4):
        body = "Contoso\n" + "x" * pad + "\\*" * 9000
        head = _cap_body(body, 8192, sidecar_name="full-text.txt")[0].split("\n\n[truncated")[0]
        assert (len(head) - len(head.rstrip("\\"))) % 2 == 0, pad


@pytest.mark.parametrize("count", [80, 90, 150])
def test_a_window_with_many_keyframes_keeps_its_text_and_passes_only_by_their_footers_c2254(
    count: int,
) -> None:
    """B13 round 42 C2254: past about 78 keyframes their footer lines left a window's text no room, so the
    page kept 0 bytes of text and still passed the cap. The room is worked out again without them: a small
    body comes back whole, a larger one keeps the text a keyframe-free page shows, and the page passes the
    cap by the keyframe footer lines only. Returning it uncut instead had no bound."""
    others = tuple(f"t00{n // 60:02d}{n % 60:02d}{n:02d}.jpg" for n in range(count))
    footers = sum(_common._footer_bytes(n) for n in others)
    small = "KEYFRAME: Contoso slide\n" * 90  # 2,250 bytes
    capped, size = _guarded(small, 8192, others)
    assert capped == small and size <= 8192 + footers
    large = "KEYFRAME: Contoso slide with its words\n" * 500  # 19,500 bytes
    capped, size = _guarded(large, 8192, others)
    head = capped.split("\n\n[truncated: first ")[0]
    assert 2 * len(head.encode()) >= _ROOM_8192 and size <= 8192 + footers


def test_the_cap_bounds_the_guarded_body_and_the_words_say_the_front_matter_is_outside_c2220(
    tmp_path: Path,
) -> None:
    """B13 round 42 C2220 with C2306: the config's comment and the ConvertConfig row said every page stays
    within the cap, but a page file is the front matter then the guarded body, and only the body is sized
    (a 400-row CSV's file was 8,321 bytes at 8,192). Budgeting the front matter makes things worse (it
    holds the path, URL and the body's own hash), so the words say what the cap bounds: the guarded body,
    the front matter outside it. C2269: §16.258's C2055 line names the two exceptions its sources name."""
    src = tmp_path / "contoso.csv"
    src.write_text("id,name\n" + "".join(f"{i},Contoso row {i}\n" for i in range(400)), encoding="utf-8")
    (unit,) = _pages(src, 8192)
    assert _size(unit) <= 8192 and [n for n, _ in unit.sidecars] == ["full-table.csv"]
    config_py = Path(setup_report.__file__).with_name("config.py").read_text(encoding="utf-8")
    comment = " ".join(config_py.replace("    # ", " ").split())
    assert "the cap bounds the guarded body of every page converted under any cap it accepts" in comment
    words = (
        "the front matter above it (paths, URL, digests, title, summary), sized by the item's metadata "
        "rather than its bytes (often under 1 KB, about 2 KB with a long path, URL and summary), sits "
        "outside the cap, never budgeted"
    )
    assert words + ", so a page file passes the cap by its front matter." in comment
    assert "an edit made before the first sync that recorded [convert] re-reads nothing" in comment
    contracts = " ".join(Path("docs/design/CONTRACTS.md").read_text(encoding="utf-8").split())
    assert "the cap bounds each page's guarded body (a window past its keyframes keeps its text" in contracts
    assert (
        words + "; a [convert] edit re-reads" in contracts and "1 KB, sits outside the cap" not in contracts
    )
    assert (
        "say every page stays within any cap the config accepts (a recording's index and a window with more "
        "keyframes than its footer lines fit aside, C2044's residuals at §16.263; §16.268 C2269)."
        in contracts
    )
    # B13 round 43 C2532: "about 1 KB" is an example, not a bound: the front matter follows the item's
    # metadata, and a long path, URL and summary (the title and summary at their character caps) pass 1 KB.
    folder = "/".join(f"Contoso Programme Office {n}" for n in range(8))
    fm = MirrorFrontmatter(
        source_kind="graph",
        source_id="contoso-sharepoint",
        stable_id="b!contoso:01CONTOSO",
        source_path=f"Sites/Contoso/{folder}/Contoso Quarterly Operating Plan.xlsx",
        status=PageStatus.CURRENT,
        source_web_url="https://contoso.sharepoint.com/sites/Contoso/"
        + "Shared%20Documents/" * 20
        + "Plan.xlsx",
        content_sha256="a" * 64,
        canonical_sha256="b" * 64,
        rendered_sha256="c" * 64,
        part_kind="sheet",
        part_name="Q3",
        unit_index=1,
        unit_of=2,
        converter="xlsx-openpyxl@1.0.4",
        options_hash="sha256:" + "d" * 64,
        source_title="Contoso quarterly plan " * 9,
        summary="Contoso budget lines by quarter, owner and region. " * 10,
    )
    page = render_mirror_page(fm, "# Q3\n")
    head = page[: page.index("# Q3\n")]
    assert len(head.encode("utf-8")) > 1024, len(head.encode("utf-8"))


def test_every_cap_body_caller_passes_the_configured_cap() -> None:
    """B13 round 41 C2044: the reserve lives in `_cap_body`, so it holds for a converter only while that
    converter hands `_cap_body` the configured cap, not one it has already cut."""
    src = Path(_common.__file__).parent
    calls = {p.stem: p.read_text(encoding="utf-8") for p in src.glob("*.py") if "_cap_body(" in p.read_text()}
    callers = {k for k in calls if k != "_common"}
    named = "diagram eml image markdown msg pandoc pdf pptx recording_page teams vtt"
    assert sorted(callers) == named.split()
    for stem in callers:
        args = [
            calls[stem][m.end() :].split("sidecar_name=")[0] for m in re.finditer(r"_cap_body\(", calls[stem])
        ]
        assert args and all("max_page_bytes" in a for a in args), stem


def _maker(kind: str) -> tuple[str, Callable[[int], bytes]]:
    if kind == "txt":
        return "near.txt", lambda n: _filler(n).encode()
    if kind == "md":
        return "near.md", lambda n: ("# Contoso\n\n" + _filler(n)).encode()
    if kind == "vtt":
        return "near.vtt", lambda n: ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + _filler(n)).encode()
    if kind == "eml":
        return "near.eml", lambda n: eml_bytes(plain=_filler(n) or "x")
    if kind == "msg":
        return "near.msg", lambda n: msg_bytes(plain=_filler(n) or "x")
    if kind == "teams":
        return "near.teams.json", lambda n: teams_doc(
            [teams_msg("1", "2026-09-01T09:00:00Z", "<p>" + _filler(n).replace("\n", "</p><p>") + "</p>")]
        ).encode()
    if kind == "pdf":
        return "near.pdf", lambda n: _pdf_bytes(_filler(n))
    model = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="2" value="{}" vertex="1" parent="1"/></root></mxGraphModel>'  # noqa: E501
    return "near.drawio", lambda n: _drawio_bytes(model.format(_filler(n).replace("\n", " ")))


def _pdf_bytes(text: str) -> bytes:
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        return build_pdf(
            Path(d) / "p.pdf", [text.split("\n")[i : i + 40] for i in range(0, 4000, 40)]
        ).read_bytes()


def _drawio_bytes(model: str) -> bytes:
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as d:
        return build_drawio(Path(d) / "d.drawio", model=model).read_bytes()


@pytest.mark.parametrize("cap", [8192, 20_000])
@pytest.mark.parametrize("kind", ["txt", "md", "vtt", "eml", "msg", "teams", "pdf", "drawio"])
def test_a_body_within_150_bytes_of_the_cap_leaves_the_guard_its_room_c2044(
    tmp_path: Path, kind: str, cap: int
) -> None:
    """B13 round 41 C2044: a body the converter sized at 1 to 150 bytes under the cap, or just over it, was
    98 to 138 bytes past the cap once the guard added the banner and the digest footer. Through
    Registry.default every such guarded page now stays within the cap."""
    name, make = _maker(kind)
    src = tmp_path / name
    inner = Registry.default(replace(CFG, max_page_bytes=10**9)).for_name(name).inner  # type: ignore[union-attr]

    def length(n: int) -> int:
        src.write_bytes(make(n))
        return _size(inner.convert(src, name=name)[0])

    for delta in (-150, -100, -50, -10, 1, 75, 150):
        a, b = 0, 3 * cap
        while a < b:  # the smallest filler whose unguarded body reaches cap + delta
            mid = (a + b) // 2
            a, b = (mid + 1, b) if length(mid) < cap + delta else (a, mid)
        assert cap + delta <= length(a) <= cap + delta + 40, (kind, delta, length(a))
        pages = _pages(src, cap)
        assert [_size(p) for p in pages if _size(p) > cap] == [], (kind, delta)


# ---------------------------------------------------------------------------------------------------------
# xlsx: C2043 with C2088, C2064 and C2046; C2022; C2045; C2047
# ---------------------------------------------------------------------------------------------------------


def _book(
    path: Path,
    *,
    rows: int,
    comments: int = 0,
    text: Callable[[int], str] = lambda i: f"check {i}",
    chart_title: str | None = None,
    empty_cells: bool = False,
) -> Path:
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    for i in range(rows):
        ws.append([f"North {i}", i])
    for i in range(comments):
        ws.cell(row=i + 1, column=3 if empty_cells else 1).comment = Comment(text(i), "Ana")
    if chart_title is not None:
        chart = BarChart()
        chart.title = chart_title
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3))
        ws.add_chart(chart, "D2")
    book.save(path)
    return path


def _rows_shown(body: str) -> int:
    return sum(1 for ln in body.split("\n") if ln.startswith("| ") and not ln.startswith("| Row "))


def _sidecar_text(unit: RenderedUnit, suffix: str) -> str:
    found = [data for name, data in unit.sidecars if name.endswith(suffix)]
    assert len(found) == 1, [n for n, _ in unit.sidecars]
    return found[0].decode("utf-8")


@pytest.mark.parametrize(
    ("cap", "rows", "comments", "least"), [(8192, 41, 20, 26), (1_000_000, 5001, 5000, 26)]
)
def test_short_comments_keep_the_rows_c2043(
    tmp_path: Path, cap: int, rows: int, comments: int, least: int
) -> None:
    """B13 round 41 C2043, C2088: the table's reserve grew 200 bytes a comment with no floor, so 20 short
    comments at 8,192 showed 1 row of 41 (26 before round 40's floor went) and 5,000 at 1,000,000 showed none.
    The comment and object sections now cost their real bytes, up to a quarter of the cap, so the rows come
    back, every comment is listed and the page stays within the cap."""
    index, sheet = _pages(_book(tmp_path / "c.xlsx", rows=rows, comments=comments), cap)
    assert _size(index) <= cap and _size(sheet) <= cap
    assert _rows_shown(sheet.body) >= least
    assert sheet.body.count("\n- A") == comments and "comments.txt" not in sheet.body


@pytest.mark.parametrize("cap", [8192, 20_000])
@pytest.mark.parametrize(
    ("label", "kw"),
    [
        ("one-long", {"rows": 5, "comments": 1, "text": lambda i: "Contoso " * 3750}),
        ("many", {"rows": 210, "comments": 200, "text": lambda i: f"{i:04d} Contoso " + "x" * 127}),
        (
            "empty-cells",
            {"rows": 0, "comments": 60, "text": lambda i: f"{i:02d} " + "note " * 100, "empty_cells": True},
        ),
    ],
)
def test_a_sheets_comment_list_stays_within_the_cap_and_rides_whole_in_its_sidecar_c2064(
    tmp_path: Path, cap: int, label: str, kw: dict[str, object]
) -> None:
    """B13 round 41 C2064, C2088: `_sheet_body` appended every comment whole after a table it had budgeted
    alone, so one 30,000-character comment gave a 30,397-byte page and 198 comments of 140 characters a
    30,745-byte one at 8,192, with no sidecar for them; a comment on an empty cell was dropped. The list now
    fits the room left, its cut note names `01-contoso.comments.txt`, which holds every comment whole, and
    the table keeps rows."""
    src = _book(tmp_path / f"{label}.xlsx", **kw)  # type: ignore[arg-type]
    index, sheet = _pages(src, cap)
    assert _size(index) <= cap and _size(sheet) <= cap
    found = re.search(r"\[comment list truncated at the page size cap: (\d+) of (\d+) listed; every comment "
                      r"is whole in the sidecar `01-contoso\.comments\.txt`\]", sheet.body)  # fmt: skip
    assert found and int(found.group(2)) == kw["comments"]
    whole = _sidecar_text(sheet, ".comments.txt")
    text = kw["text"]
    assert callable(text)
    for i in range(int(found.group(2))):  # each comment escaped, nothing stripped (round 42 C2301)
        assert f": {_record_escape(str(text(i)))}\n" in whole
    assert "01-contoso.csv" not in [n for n, _ in sheet.sidecars] or "[row cap: first" in sheet.body
    if label == "many":
        assert _rows_shown(sheet.body) > 26
    if label == "empty-cells":
        assert "[empty sheet]" in sheet.body


@pytest.mark.parametrize("cap", [8192, 20_000])
def test_a_long_chart_title_is_cut_on_the_sheet_page_and_whole_in_its_sidecar_c2046(
    tmp_path: Path, cap: int
) -> None:
    """B13 round 41 C2046: a chart title went whole into the object line, so a 20,000-character one gave a
    30,319-byte sheet page at any cap. It is cut to 200 characters and escaped, the page says so, and the
    objects sidecar holds it whole."""
    title = "<!--Contoso " + "x" * 20_000
    index, sheet = _pages(_book(tmp_path / "chart.xlsx", rows=5, chart_title=title), cap)
    assert _size(index) <= cap and _size(sheet) <= cap
    assert '"&lt;!--Contoso ' + "x" * 187 + '…"' in sheet.body and "<!--" not in sheet.body
    assert (
        "[a chart title or pivot name cut to 200 characters; every object is whole in the sidecar "
        in sheet.body
    )
    assert title in _sidecar_text(sheet, ".objects.txt")


def test_a_sheet_under_the_cap_keeps_its_lists_whole_with_no_list_sidecar(tmp_path: Path) -> None:
    """B13 round 41 C2043: a list that fits is listed whole as before, with no note and no list sidecar."""
    _index, sheet = _pages(
        _book(tmp_path / "small.xlsx", rows=5, comments=3, chart_title="Contoso Q3"), 1_000_000
    )
    assert "- A1 (Ana): check 0" in sheet.body and '"Contoso Q3" at D2' in sheet.body
    assert sheet.sidecars == () and "truncated" not in sheet.body and "cut to" not in sheet.body


@pytest.mark.filterwarnings("ignore:Title is more than 31 characters")
def test_a_second_list_and_a_long_sheet_title_keep_the_page_within_the_cap_c2043(tmp_path: Path) -> None:
    """B13 round 41 C2043 (its review): the comment list filled the room the object list's heading and note
    then passed, so 2,000 rows, 400 short comments and two charts gave an 8,218-byte page at 8,192, and a
    6,000-character sheet title an 8,915-byte one. Each list keeps the room the lists after it need, and the
    heading's title is cut to 200 characters."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "C"
    for i in range(2000):
        ws.append([f"North {i}", i])
    for i in range(400):
        ws.cell(row=i + 1, column=1).comment = Comment(f"{i:03d} Contoso check", "A")
    for anchor in ("D2", "D20"):
        chart = BarChart()
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3))
        ws.add_chart(chart, anchor)
    two = tmp_path / "two.xlsx"
    book.save(two)
    _index, sheet = _pages(two, 8192)
    assert _size(sheet) <= 8192 and "## Charts, pivots and images" in sheet.body
    long_title = tmp_path / "title.xlsx"
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso " + "t" * 6000
    ws.append(["North", 1])
    book.save(long_title)
    for streamed in (False, True):
        pages = _pages(
            long_title, 8192, xlsx_stream_threshold_bytes=0 if streamed else CFG.xlsx_stream_threshold_bytes
        )
        assert [_size(p) for p in pages if _size(p) > 8192] == []
        assert "# Sheet: Contoso " + "t" * 191 + "… (used range" in pages[1].body


def test_a_streamed_first_row_of_wide_characters_still_lists_its_column_c2045(tmp_path: Path) -> None:
    """B13 round 41 C2045 (its review): the scaled limit gave a cell the whole room at 4 bytes a character, so
    one column of emoji left its own Schema row no room. The limit keeps room for the page's head and each
    row's other cells first."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append(["\U0001f600" * 2000])
    ws.append(["x"])
    src = tmp_path / "wide.xlsx"
    book.save(src)
    _index, summary = _pages(src, 8192, xlsx_stream_threshold_bytes=0)
    assert (
        _size(summary) <= 8192 and "| A | \U0001f600" in summary.body and "0 of 1 column" not in summary.body
    )


def _wide_book(path: Path, *, sheets: int = 4) -> Path:
    book = openpyxl.Workbook()
    for n in range(sheets):
        ws = book.active if n == 0 else book.create_sheet()
        assert ws is not None
        ws.title = f"Contoso {n}"
        ws.append([f"H{c:02d} " + "h" * 596 for c in range(30)])
        for r in range(5):
            ws.append([f"r{r}c{c}" for c in range(30)])
    book.save(path)
    return path


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
def test_an_oversized_first_row_is_shown_with_its_cells_cut_c2022(tmp_path: Path, streamed: bool) -> None:
    """B13 round 41 C2022: a first row past the table's budget was dropped, so four sheets of thirty
    600-character headers showed no row and no header at 8,192, though the five short rows after it fit. It
    is rebuilt from the cells' plain text, each cut to one length ending `…` before escaping, its note names
    the cut, the later rows follow and the sidecar holds every row whole."""
    src = _wide_book(tmp_path / "wide.xlsx")
    pages = _pages(src, 8192, xlsx_stream_threshold_bytes=0 if streamed else CFG.xlsx_stream_threshold_bytes)
    assert len(pages) == 5 and [p.unit_id for p in pages if _size(p) > 8192] == []
    for page in pages[1:]:
        if streamed:  # the Schema keeps its 16 columns, and the rows get the real room left (C2268)
            assert "[schema cut at the page size cap: 16 of 30 columns listed;" in page.body
            assert "## First 6 of 6 non-empty rows" in page.body and "[no row fits the page;" not in page.body
        assert re.search(r"\| 1 \| H00 h+… \| H01 h+… \|", page.body), page.body[:600]
        assert "| 6 | r4c0 | r4c1 |" in page.body
        assert re.search(
            r"row 1's cells cut to \d+ characters; every row is in the sidecar `0\d-contoso-\d\.csv`",
            page.body,
        )
        rows = list(csv.reader(io.StringIO(_sidecar_text(page, ".csv").split("\n\n", 1)[-1])))
        assert rows[1][1] == "H00 " + "h" * 596 and len(rows) == 7


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
def test_a_long_cell_beside_short_ones_takes_the_room_the_later_rows_leave_c2268(
    tmp_path: Path, streamed: bool
) -> None:
    """B13 round 42 C2268 (3): an even share of the room, halved until the row fit, cut one 20,008-character
    cell beside 29 one-character cells to 124 characters on an 870-byte page at 8,192 (83 streamed). The
    later rows that fit uncut keep their bytes, and the cut row takes the largest length whose rendered
    line fits the rest, searched on the rendered bytes."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append(["Contoso " + "x" * 20_000, *"abcdefghijklmnopqrstuvwxyzABC"])
    for n in range(2, 7):
        ws.append([f"r{n}", *"abcdefghijklmnopqrstuvwxyzABC"])
    src = tmp_path / "long.xlsx"
    book.save(src)
    _index, page = _pages(src, 8192, **({"xlsx_stream_threshold_bytes": 0} if streamed else {}))
    assert _size(page) <= 8192
    found = re.search(r"row 1's cells cut to (\d+) characters", page.body)
    assert found and int(found.group(1)) > 2000, page.body[-400:]
    assert "| 6 | r6 | a | b |" in page.body and f"| 1 | Contoso {'x' * 1990}" in page.body


def _cut_sheet(rows: dict[int, list[str]]) -> xlsx._Sheet:
    """A sheet of plain text cells, row ``r``'s list from column A (B13 round 43 C2516)."""
    sheet = xlsx._Sheet(number=1, title="Contoso", hidden=False)
    for r, texts in rows.items():
        for c, text in enumerate(texts, start=1):
            sheet.plain[(r, c)] = text
            sheet.shown[(r, c)] = _common._cell(text)
    width = max(len(texts) for texts in rows.values())
    sheet.bounds = (min(rows), 1, max(rows), width)
    return sheet


def _line_bytes(line: str) -> int:
    return len(line.encode("utf-8")) + 1


def test_a_cut_row_takes_a_longer_cut_that_costs_less_c2516() -> None:
    """B13 round 43 C2516: cut to one character, "ab" and "cd" each became the 3-byte `…`, so the row cost
    24 bytes at one character and 23 at two, and a 23-byte room returned no cut. The cut drops where a
    cell's `…` goes, so the segments between those lengths are searched from the top."""
    sheet = _cut_sheet({1: ["ab", "cd", "x" * 40]})
    header = ["Row", "A", "B", "C"]
    costs = [_line_bytes(xlsx._cut_line(sheet, header, 1, n)) for n in range(1, 6)]
    assert costs == [24, 23, 24, 25, 26]
    assert xlsx._cut_row(sheet, header, 1, 23) == ("| 1 | ab | cd | x… |", 2)
    assert xlsx._cut_row(sheet, header, 1, 22) is None
    assert xlsx._cut_row(sheet, header, 1, 25) == ("| 1 | ab | cd | xxx… |", 4)


def test_later_rows_are_reserved_against_the_cheapest_cut_c2516() -> None:
    """B13 round 43 C2516: _table_lines reserved the later rows against the row cut to one character (24
    bytes), not its cheapest cut (23 bytes at two), so a page with room for the cut row and the uncut row
    after it showed the cut row alone."""
    sheet = _cut_sheet({1: ["ab", "cd", "x" * 40], 2: ["e", "f", "g"]})
    header_bytes = _line_bytes("| Row | A | B | C |") + _line_bytes("|---|---|---|---|")
    budget = header_bytes + 23 + _line_bytes("| 2 | e | f | g |")
    lines, shown, cut = xlsx._table_lines(sheet, [1, 2], sheet.shown, budget, 10)
    assert (shown, cut) == (2, [(1, 2)])
    assert lines[2:] == ["| 1 | ab | cd | x… |", "| 2 | e | f | g |"]
    assert sum(_line_bytes(x) for x in lines) == budget


@pytest.mark.parametrize(
    ("streamed", "cap", "both"),
    [
        (False, 6423, False),
        (False, 8023, True),
        (True, 16850, False),
        (True, 18450, True),
    ],  # C2476: no flat 4,096
    ids=["full-cut", "full-next-row", "streamed-cut", "streamed-next-row"],
)
def test_a_cut_row_costing_less_than_one_character_is_shown_c2529(
    tmp_path: Path, streamed: bool, cap: int, both: bool
) -> None:
    """B13 round 43 C2529: a 20,008-character cell beside 400 cells of "ab" costs 2,412 bytes cut to one
    character and about 400 fewer cut to two, where each "ab" is whole again. A room between the two
    showed no row (`[no row fits the page;` streamed), and a room that also held row 2 after the cheapest
    cut showed row 1 alone. Row 1 is now shown cut, row 2 follows when it fits, and the page stays within
    the cap."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append(["Contoso " + "x" * 20_000, *(["ab"] * 400)])
    ws.append(["r2", *(["c"] * 400)])
    src = tmp_path / "wide.xlsx"
    book.save(src)
    _index, page = _pages(src, cap, **({"xlsx_stream_threshold_bytes": 0} if streamed else {}))
    assert _size(page) <= cap
    assert "[no row fits the page;" not in page.body and "first 0 of 2" not in page.body
    assert re.search(r"\| 1 \| C[^|]*… \| ab \| ab \|", page.body), page.body[-600:]
    found = re.search(r"row 1's cells cut to (\d+) characters", page.body)
    assert found and int(found.group(1)) >= 2, page.body[-600:]
    assert ("| 2 | r2 | c | c |" in page.body) is both
    shown = "2 of 2" if both else "1 of 2"
    assert (f"## First {shown} non-empty rows" if streamed else f"first {shown} non-empty rows") in page.body


def _count_cut_cells(monkeypatch: pytest.MonkeyPatch, most: int) -> list[int]:
    """Count the cut cells xlsx renders (:func:`xlsx._cut_cell`), failing at once past ``most``, so a
    quadratic search fails in a second instead of running for minutes."""
    calls: list[int] = []
    real = xlsx._cut_cell

    def counted(sheet: xlsx._Sheet, key: tuple[int, int], limit: int) -> str:
        calls.append(limit)
        assert len(calls) <= most, f"more than {most} cut cells rendered"
        return real(sheet, key, limit)

    monkeypatch.setattr(xlsx, "_cut_cell", counted)
    return calls


def test_a_wide_row_is_cut_without_rendering_each_length_c2516(monkeypatch: pytest.MonkeyPatch) -> None:
    """B13 round 43 C2516: a row's bytes at each length are summed from its texts' own, so a row of 2,000
    cells of varied lengths beside a 30,000-character cell renders its cut cells once, for the line it
    shows, not once a length or once a breakpoint, and still takes the largest cut that fits. (The count was
    of whole-line renders, which the costing never made: it rendered each cut cell at each breakpoint.)"""
    texts = ["ab" if n % 3 else "Contoso"[: n % 7 + 1] * (n % 11 + 1) for n in range(2000)]
    sheet = _cut_sheet({1: ["x" * 30_000, *texts]})
    header = ["Row", *(xlsx._col_letter(c) for c in range(1, 2002))]
    room = xlsx._RowCuts(sheet, header, 1).cheapest(10**9)
    assert room is not None and room < _line_bytes(xlsx._cut_line(sheet, header, 1, 1))
    room += 500
    calls = _count_cut_cells(monkeypatch, 2001)
    fitted = xlsx._cut_row(sheet, header, 1, room)
    assert fitted is not None and len(calls) == 2001, len(calls)
    line, limit = fitted
    monkeypatch.undo()
    assert _line_bytes(line) <= room < _line_bytes(xlsx._cut_line(sheet, header, 1, limit + 1))


def test_a_row_of_many_cut_lengths_is_costed_in_one_sweep_c2516(monkeypatch: pytest.MonkeyPatch) -> None:
    """B13 round 43 C2516 (review): cells "a" and 1 to 4,000 spaces cost 5 bytes cut ("a …") and 1 whole, so
    every length is a breakpoint, and the cheapest cut rendered each cell still cut at each one: 4,000
    cells took 100 seconds. The cheapest cut and the cut row now render the 4,000 cut cells once."""
    n = 4000
    sheet = _cut_sheet({1: ["a" + " " * k for k in range(1, n + 1)]})
    header = ["Row", *(xlsx._col_letter(c) for c in range(1, n + 1))]
    calls = _count_cut_cells(monkeypatch, n)
    costs = xlsx._RowCuts(sheet, header, 1)
    floor = costs.cheapest(10**9)
    assert calls == []
    fitted = xlsx._cut_row(sheet, header, 1, floor or 0, costs)
    assert fitted is not None and fitted[1] == n + 1 and _line_bytes(fitted[0]) == floor
    assert len(calls) == n


def _formula_sheet(value: str) -> xlsx._Sheet:
    """Row 2 a formula cell `=1` whose cached value is ``value``, row 3 a cell "x" (B13 round 43 C2516)."""
    sheet = xlsx._Sheet(number=1, title="Contoso", hidden=False)
    sheet.formula[(2, 1)] = "=1"
    sheet.values[(2, 1)] = sheet.plain[(2, 1)] = value
    sheet.shown[(2, 1)] = f"{_common._cell(value)} `=1`"
    sheet.plain[(3, 1)] = sheet.values[(3, 1)] = sheet.shown[(3, 1)] = "x"
    sheet.bounds = (2, 1, 3, 1)
    return sheet


def test_a_formula_shorter_than_its_value_is_a_breakpoint_c2516() -> None:
    """B13 round 43 C2516 (review): the breakpoints were each cell's longest text, so the formula `=1`
    beside the value " abcdef" left out length 2, where the formula is whole and the row costs 17 bytes,
    not 18: the cheapest cut read 18 and a 17-byte room returned no cut."""
    sheet = _formula_sheet(" abcdef")
    header = ["Row", "A"]
    costs = [_line_bytes(xlsx._cut_line(sheet, header, 2, n)) for n in range(1, 8)]
    assert costs == [18, 17, 18, 19, 20, 21, 20]
    assert xlsx._RowCuts(sheet, header, 2).cheapest(10**9) == 17
    assert xlsx._cut_row(sheet, header, 2, 17) == ("| 2 | … `=1` |", 2)
    assert xlsx._cut_row(sheet, header, 2, 16) is None


def test_later_rows_are_reserved_against_a_formula_s_cheapest_cut_c2516() -> None:
    """B13 round 43 C2516 (review): with the formula's length left out of the breakpoints, _table_lines
    reserved row 3 against an 18-byte cut of row 2, not its 17-byte cut at two characters, and showed row
    2 alone on a page that held both."""
    sheet = _formula_sheet(" " + "a" * 40)
    header_bytes = _line_bytes("| Row | A |") + _line_bytes("|---|---|")
    budget = header_bytes + 17 + _line_bytes("| 3 | x |")
    lines, shown, cut = xlsx._table_lines(sheet, [2, 3], sheet.shown, budget, 10)
    assert (shown, cut) == (2, [(2, 2)])
    assert lines[2:] == ["| 2 | … `=1` |", "| 3 | x |"]


def _reserve_book(path: Path, *, lists: bool) -> Path:
    """6,000 rows under a 200-character title, a formula with no cached value, a long cell cut on row 3, and
    with ``lists`` three comments and a chart."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    with pytest.warns(UserWarning, match="31 characters"):
        ws.title = "Contoso " + "t" * 192
    for i in range(6000):
        ws.append([f"North {i}", i, f"Contoso value {i}"])
    ws["D1"] = "=B1*2"
    ws["C3"] = "Contoso long words " * 1200
    if lists:
        for i in range(3):
            ws.cell(row=i + 1, column=1).comment = Comment(f"Contoso check {i}", "Ana")
        chart = BarChart()
        chart.title = "Contoso chart"
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3))
        ws.add_chart(chart, "F2")
    book.save(path)
    return path


@pytest.mark.parametrize(
    ("cap", "lists", "old"),
    [(8192, True, 86), (20_000, True, 328), (8192, False, 91), (20_000, False, 332)],
    ids=["8192-lists", "20000-lists", "8192-no-lists", "20000-no-lists"],
)
def test_the_full_path_budgets_the_real_bytes_around_its_table_c2476(
    tmp_path: Path, cap: int, lists: bool, old: int
) -> None:
    """B13 round 43 C2476: the full path kept a flat 4,096 bytes for its head, notes, banner and footers,
    so a page showed 86 rows at 8,192 where the cap left room for more (``old``, each case's count under
    the flat reserve). The budget is now the real head, the widest row-cap note, the no-cached-values note,
    the guard and each sidecar's footer, and the lists' share keeps their cut notes' room, so they list
    whole: the guarded page stays within the cap, shows more rows than before, and, on a sheet with no
    list, as many as the streamed path shows."""
    src = _reserve_book(tmp_path / "c.xlsx", lists=lists)
    _index, full = _pages(src, cap)
    _index, streamed = _pages(src, cap, xlsx_stream_threshold_bytes=0)
    assert _size(full) <= cap and _size(streamed) <= cap
    assert "row 3's cells cut to " in full.body and "[no cached values: 1 of 1" in full.body
    if lists:
        assert "Contoso check 2" in full.body and "Contoso chart" in full.body
        assert "list truncated" not in full.body and "## Charts, pivots and images" in full.body
    shown = _rows_shown(full.body)
    assert shown > old
    assert f"[row cap: first {shown} of 6000 non-empty rows shown" in full.body
    if not lists:
        assert shown >= _rows_shown(streamed.body)


def test_xlsx_first_row_past_the_budget_is_cut_and_the_short_row_follows_c2022(tmp_path: Path) -> None:
    """B13 round 41 C2022, on C1676's own workbook: the 20,008-character first cell is shown cut, the short
    row after it is shown, and the page stays within the cap."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso notes"
    ws.append(["Contoso " + "x" * 20_000, "a"])
    ws.append(["short", "b"])
    src = tmp_path / "long.xlsx"
    book.save(src)
    _index, data = _pages(src, 8192)
    assert _size(data) <= 8192
    assert "[row cap: first 2 of 2 non-empty rows shown, row 1's cells cut to " in data.body
    assert "| 1 | Contoso xxx" in data.body and "x…" in data.body and "| 2 | short | b |" in data.body
    _index, summary = _pages(src, 8192, xlsx_stream_threshold_bytes=0)
    assert _size(summary) <= 8192 and "## First 2 of 2 non-empty rows" in summary.body
    assert "| 1 | Contoso xxx" in summary.body and "| 2 | short | b |" in summary.body
    assert (
        "[row 1's cells cut to " in summary.body
        and "every row is in the sidecar `01-contoso-notes.csv`]" in summary.body
    )


_LONG_FORMULA = "=" + "+".join(["1"] * 3000)  # 6,000 characters, cached 3000


def _formula_book(tmp_path: Path) -> Path:
    """Row 1: a 20,008-character cell beside a short and a long formula, each with a cached value; row 2: a
    short formula past a one-row cap."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append(["Contoso " + "x" * 20_000, "=SUM(C1:C2)", 1, _LONG_FORMULA])
    ws.append(["short", "=C2*3", 1, "d"])
    plain = tmp_path / "plain.xlsx"
    book.save(plain)
    cached = {"B1": "2", "D1": "3000", "B2": "3"}
    return with_cached_values(plain, tmp_path / "contoso.xlsx", "xl/worksheets/sheet1.xml", cached)


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
@pytest.mark.parametrize("cap_rows", [False, True], ids=["all-rows", "one-row"])
def test_a_cut_row_keeps_its_formulas_on_the_page_or_whole_in_a_sidecar_c2300(
    tmp_path: Path, streamed: bool, cap_rows: bool
) -> None:
    """B13 round 42 C2300: a cut row was rebuilt from the CSV's values, so `=SUM(C1:C2)` beside a cached 2
    vanished from the page whenever another cell made the row too wide, and the CSV holds values only. A
    cut row now cuts each cell's value and formula apart, never splitting the code span, and every formula
    the page does not show whole (cut, or in a row past the rows shown) rides whole in
    `NN-<slug>.formulas.txt`, which the page's note names."""
    src = _formula_book(tmp_path)
    kw: dict[str, object] = {"xlsx_stream_threshold_bytes": 0} if streamed else {}
    if cap_rows:
        kw["max_rows_per_sheet"] = 1
    _index, page = _pages(src, 8192, **kw)
    assert _size(page) <= 8192
    assert "| 1 | Contoso xxx" in page.body and "| 2 `=SUM(C1:C2)` |" in page.body, page.body[-900:]
    formulas = _sidecar_text(page, ".formulas.txt").split("\n\n", 1)[-1].split("\n")
    assert f"D1: {_LONG_FORMULA}" in formulas and "B1: =SUM(C1:C2)" not in formulas
    assert ("B2: =C2*3" in formulas) == cap_rows
    assert "every formula not shown whole is in the sidecar `01-contoso.formulas.txt`" in page.body
    assert re.search(r"\| 3000 `=1\+1\+[1+]*…` \|", page.body), page.body[-900:]


def test_a_row_cap_with_no_formula_writes_no_formulas_sidecar_c2300(tmp_path: Path) -> None:
    """B13 round 42 C2300: a sheet with no formula past its shown rows writes no `.formulas.txt`."""
    _index, page = _pages(_book(tmp_path / "contoso.xlsx", rows=40), 8192, max_rows_per_sheet=5)
    assert not [n for n, _ in page.sidecars if n.endswith(".formulas.txt")] and "formula" not in page.body


_BREAK_IDS = {"\n": "lf", "\r": "cr"}


def _lossy_book(tmp_path: Path, brk: str, rows: int = 1) -> Path:
    """Row 1: `1` beside `=IF(A1=1,7,"line one<brk>line two")` with a cached 7, then ``rows - 1`` short rows.
    A CR is written as `&#13;`, as Excel does: XML reads a raw CR back as LF."""
    import zipfile  # noqa: PLC0415

    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append([1, '=IF(A1=1,7,"line oneBRKMARKline two")'])
    for n in range(2, rows + 1):
        ws.append([n, f"Contoso {n}"])
    plain = tmp_path / "plain.xlsx"
    book.save(plain)
    marked = tmp_path / "marked.xlsx"
    with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(marked, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b"BRKMARK", b"&#13;" if brk == "\r" else brk.encode())
            zout.writestr(info, data)
    return with_cached_values(marked, tmp_path / "contoso.xlsx", "xl/worksheets/sheet1.xml", {"B1": "7"})


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
@pytest.mark.parametrize("brk", list(_BREAK_IDS), ids=list(_BREAK_IDS.values()))
def test_a_formula_the_page_flattens_rides_whole_in_the_formulas_sidecar_c2531(
    tmp_path: Path, brk: str, streamed: bool
) -> None:
    """B13 round 43 C2531: `_code` flattened a formula's LF on the page and left a CR raw, which split the
    table row, and `_formulas_left` skipped every row shown uncut, so a short formula holding a line break
    was nowhere whole. The page shows it on one line, and the sheet's `.formulas.txt` holds it escaped, with
    a note of its own on a sheet that fits."""
    src = _lossy_book(tmp_path, brk)
    kw: dict[str, object] = {"xlsx_stream_threshold_bytes": 0} if streamed else {}
    _index, page = _pages(src, 8192, **kw)
    assert _size(page) <= 8192
    assert '| 1 | 1 | 7 `=IF(A1=1,7,"line one line two")` |' in page.body, page.body
    assert not [line for line in page.body.splitlines() if line.startswith('line two")')]
    formulas = _sidecar_text(page, ".formulas.txt").split("\n\n", 1)[-1].split("\n")
    assert formulas == [f'B1: =IF(A1=1,7,"line one{_record_escape(brk)}line two")', ""]
    assert "[every formula not shown whole is in the sidecar `01-contoso.formulas.txt`]" in page.body


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
@pytest.mark.parametrize("brk", list(_BREAK_IDS), ids=list(_BREAK_IDS.values()))
def test_a_capped_sheet_lists_a_flattened_formula_on_a_row_shown_uncut_c2531(
    tmp_path: Path, brk: str, streamed: bool
) -> None:
    """B13 round 43 C2531: under a row cap, the formula on a shown, uncut row was left out of
    `.formulas.txt` though the page flattened it. It is listed, and the row-cap note names the sidecar."""
    src = _lossy_book(tmp_path, brk, rows=3)
    kw: dict[str, object] = {"max_rows_per_sheet": 1}
    if streamed:
        kw["xlsx_stream_threshold_bytes"] = 0
    _index, page = _pages(src, 8192, **kw)
    assert _size(page) <= 8192
    assert '| 1 | 1 | 7 `=IF(A1=1,7,"line one line two")` |' in page.body, page.body
    formulas = _sidecar_text(page, ".formulas.txt").split("\n\n", 1)[-1].split("\n")
    assert formulas == [f'B1: =IF(A1=1,7,"line one{_record_escape(brk)}line two")', ""]
    assert "every formula not shown whole is in the sidecar `01-contoso.formulas.txt`" in page.body
    if not streamed:
        assert "[row cap: first 1 of 3 non-empty rows shown; every row is in the sidecar" in page.body


@pytest.mark.parametrize("streamed", [False, True], ids=["full", "streamed"])
def test_a_sheet_with_no_flattened_formula_writes_no_formulas_sidecar_c2531(
    tmp_path: Path, streamed: bool
) -> None:
    """B13 round 43 C2531: a formula the page shows whole on a sheet that fits still writes no
    `.formulas.txt` and no note."""
    src = _lossy_book(tmp_path, " ")
    kw: dict[str, object] = {"xlsx_stream_threshold_bytes": 0} if streamed else {}
    _index, page = _pages(src, 8192, **kw)
    assert '| 1 | 1 | 7 `=IF(A1=1,7,"line one line two")` |' in page.body, page.body
    assert not [n for n, _ in page.sidecars if n.endswith(".formulas.txt")]
    assert "every formula not shown whole" not in page.body


def test_the_index_header_formula_says_what_the_code_computes_c2314(tmp_path: Path) -> None:
    """B13 round 42 C2314: the contract said a headers cell gets "a fourth of each sheet's share of the room
    past 2,048 bytes", which reads as the cap and gives 1,486 characters at 8,192 with one sheet; the code
    takes the index's 1,024-byte reserve off first and gives 1,230. The words now name the cap less 3,072
    bytes and the 200-byte row, and the sentence's number is the one a headers cell is cut to."""
    contracts = " ".join(Path("docs/design/CONTRACTS.md").read_text(encoding="utf-8").split())
    said = "each sheet's share of the cap less 3,072 bytes (the index's 1,024-byte reserve and 2,048)"
    assert contracts.count(said) >= 2 and "share of the room past 2,048" not in contracts
    assert xlsx._INDEX_RESERVE + 2048 == 3072
    cap, chars = 20_000, (20_000 - 3072 - 200) // 4  # one sheet: 4,182 characters
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    ws.append(["Contoso " + "h" * 9000, "b"])
    src = tmp_path / "contoso.xlsx"
    book.save(src)
    index = _pages(src, cap)[0].body
    assert "Contoso " + "h" * (chars - 9) + "…" in index and "Contoso " + "h" * (chars - 8) + "…" not in index


def test_thirty_short_headers_are_all_listed_on_the_index_at_the_default_cap_c2045(tmp_path: Path) -> None:
    """B13 round 41 C2045: the index's headers cell was cut at 600 characters at any cap, so thirty headers
    of 27 characters (868 with their separators) listed 21. The cut stays (one huge cell must never push
    every sheet off the index) but scales with the room, so at the default cap all 30 are listed; a streamed
    first-row value of 258 characters stays whole too. Round 42 C2255: that value sat on row 2, which the
    Schema never reads, so the test passed with the old 200-character cut; it is now a second sheet's first
    row."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Contoso"
    heads = [f"Contoso column header {c:04d}" for c in range(30)]
    ws.append(heads)
    ws.append(["x"] * 30)
    ws2 = book.create_sheet("Contoso long")
    ws2.append(["Contoso " + "v" * 250] + ["x"] * 29)
    ws2.append(["y"] * 30)
    src = tmp_path / "heads.xlsx"
    book.save(src)
    index = _pages(src, 1_000_000)[0].body
    assert ", ".join(heads) + " |" in index and "…" not in index
    pages = _pages(src, 1_000_000, xlsx_stream_threshold_bytes=0)
    assert "| A | Contoso column header 0000 |" in pages[1].body
    assert "| A | Contoso " + "v" * 250 + " |" in pages[2].body
    small = _pages(src, 8192)[0].body
    assert _size(_pages(src, 8192)[0]) <= 8192 and "Contoso column header 0029" in small


def test_the_index_note_names_the_sheet_page_or_its_csv_sidecar_c2047(tmp_path: Path) -> None:
    """B13 round 41 C2047: the cut sheet list's note said each sheet's own page has its headers, but a sheet
    page whose first row is past its budget shows none of them; the CSV sidecar does."""
    src = _wide_book(tmp_path / "many.xlsx", sheets=24)
    for streamed in (False, True):
        index = _pages(src, 8192, xlsx_stream_threshold_bytes=0 if streamed else 20 * 1000**2)[0].body
        assert re.search(
            r"\[sheet list truncated at the page size cap: \d+ of 24 sheets listed; each sheet's own page or "
            r"its CSV sidecar has its headers\]",
            index,
        )


# ---------------------------------------------------------------------------------------------------------
# C2001: a zero-byte file's own reason
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Contoso plan.docx",
        "Contoso.pdf",
        "Contoso.eml",
        "Contoso.vtt",
        "c.teams.json",
        "c.msg",
        "c.drawio",
        "c.odt",
    ],
)
def test_a_zero_byte_file_says_it_holds_zero_bytes_and_stays_empty_c2001(tmp_path: Path, name: str) -> None:
    """B13 round 41 C2001: a zero-byte file's stub said `no text after stripping whitespace and form feeds`,
    which describes text it never had. Its reason now says it holds 0 bytes, keeping the `empty-output`
    prefix, so the report still classes it `empty`."""
    src = tmp_path / name
    src.write_bytes(b"")
    with pytest.raises(UnreadableSourceError) as caught:
        _pages(src, CFG.max_page_bytes)
    assert str(caught.value) == policy.ZERO_BYTES_REASON == "empty-output (the file holds 0 bytes)"
    assert setup_report.quarantine_class(policy.ZERO_BYTES_REASON) == "empty"


def test_a_file_with_bytes_and_no_text_keeps_the_empty_output_reason_and_blank_text_stays_a_page_c2001(
    tmp_path: Path,
) -> None:
    """B13 round 41 C2001: only the zero-byte branches take the new reason. A PDF with a page and no text
    keeps `EMPTY_OUTPUT_REASON`, and a 0-byte or blank `.txt` stays a page (C930), never a stub."""

    class _Blank:
        converter_id = "blank"
        extensions = (".blank",)

        def version(self) -> str:
            return "1"

        def options(self) -> dict[str, str]:
            return {}

        def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
            body = "\x0c\n"  # a page that holds only a form feed, as pdfminer's empty page does
            return (make_unit(unit_id="whole", kind=UnitKind.WHOLE, index=0, of=1, name="", file_stem="",
                              title="t", summary="s", body=body),)  # fmt: skip

    blank = tmp_path / "a.blank"
    blank.write_bytes(b"x\n")
    with pytest.raises(UnreadableSourceError) as caught:
        Registry([_Blank()], banner=True).for_name("a.blank").convert(blank, name="a.blank")  # type: ignore[union-attr]
    assert str(caught.value) == policy.EMPTY_OUTPUT_REASON
    for data in (b"", b"  \n\f\n"):
        src = tmp_path / "blank.txt"
        src.write_bytes(data)
        (page,) = _pages(src, CFG.max_page_bytes)
        assert "[empty file]" in page.body


# ---------------------------------------------------------------------------------------------------------
# C2048: pandoc's crash and exit lines from its own lines only
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "said"),
    [
        (
            "echo \"[WARNING] Duplicate identifier 'contoso-q3' at line 1 column 63\" >&2\n"
            'echo "pandoc: heap overflow" >&2\nkill -ABRT $$\n',
            "pandoc was stopped by SIGABRT (signal 6): pandoc: heap overflow",
        ),
        (
            'echo "[WARNING] Could not convert TeX math \\\\frac{contoso}, rendering as TeX:" >&2\n'
            'echo "unexpected error: contoso-secret" >&2\nkill -SEGV $$\n',
            "pandoc was stopped by SIGSEGV (signal 11)",
        ),
        ("echo \"[WARNING] Duplicate identifier 'contoso-q3'\" >&2\nexit 64\n", "pandoc exited 64"),
        (
            'echo "[WARNING] Could not convert TeX math, rendering as TeX:" >&2\n'
            'echo "  pandoc: Contoso secret" >&2\nkill -SEGV $$\n',
            "pandoc was stopped by SIGSEGV (signal 11)",
        ),
        (
            "echo \"[WARNING] Duplicate identifier 'contoso-q3'\" >&2\n"
            'echo "pandoc: /Users/ana/Contoso/x.docx: openBinaryFile: does not exist" >&2\nexit 1\n',
            "pandoc exited 1: pandoc: <path>",
        ),
    ],
    ids=["abrt-after-warning", "segv-warning-only", "exit-warning-only", "segv-indented", "exit-own-line"],
)
def test_pandocs_crash_and_exit_lines_come_from_its_own_lines_only_c2048(
    tmp_path: Path, script: str, said: str
) -> None:
    """B13 round 41 C2048: the crash line was pandoc's first stderr line, often a `[WARNING]` that quotes the
    document, and a non-zero exit quoted it too. Only lines that start `pandoc:` are read now, paths taken
    out; with none, the line names the signal or the exit code alone."""
    pandoc = tmp_path / "pandoc"
    pandoc.write_text('#!/bin/sh\n[ "$1" = --version ] && { echo "pandoc 3.1.2"; exit 0; }\n' + script)
    pandoc.chmod(0o700)
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == said
    assert "contoso" not in str(caught.value).lower()


def _stub_pandoc(tmp_path: Path, stderr: bytes, end: str, *, version_too: bool = False) -> Path:
    """A pandoc that writes ``stderr``'s exact bytes and then ends with ``end`` (``exit 1``, ``kill -SEGV
    $$``): on --version as well when ``version_too``, else --version answers 3.1.2."""
    said = tmp_path / "said.bin"
    said.write_bytes(stderr)
    pandoc = tmp_path / "pandoc"
    head = "" if version_too else '[ "$1" = --version ] && { echo "pandoc 3.1.2"; exit 0; }\n'
    pandoc.write_text(f"#!/bin/sh\n{head}cat '{said}' >&2\n{end}\n")
    pandoc.chmod(0o700)
    return pandoc


_SEPARATORS = ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", chr(0x2028), chr(0x2029)]


@pytest.mark.parametrize("sep", _SEPARATORS, ids=[f"U+{ord(c):04X}" for c in _SEPARATORS])
@pytest.mark.parametrize(
    ("end", "said"),
    [("kill -SEGV $$", "pandoc was stopped by SIGSEGV (signal 11)"), ("exit 1", "pandoc exited 1")],
    ids=["crash", "exit"],
)
def test_a_line_break_inside_a_pandoc_warning_opens_no_line_of_its_own_c2299(
    tmp_path: Path, sep: str, end: str, said: str
) -> None:
    """B13 round 42 C2299: _own_lines split pandoc's stderr with str.splitlines(), which also breaks at CR,
    VT, FF, 0x1C to 0x1E, U+0085, U+2028 and U+2029, so a heading id holding one before `pandoc: <secret>`
    inside a `[WARNING]` record came back as pandoc's own line and the secret reached the error. Records are
    split at LF only and tested on bytes: the warning stays one record and nothing of it is quoted."""
    warning = f"[WARNING] Duplicate identifier 'alpha{sep}pandoc: contoso-secret' at input line 1 column 82\n"
    pandoc = _stub_pandoc(tmp_path, warning.encode("utf-8"), end)
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == said


@pytest.mark.parametrize(
    ("end", "said"),
    [
        ("kill -ABRT $$", "pandoc was stopped by SIGABRT (signal 6): pandoc: heap overflow"),
        ("exit 1", "pandoc exited 1: pandoc: heap overflow"),
    ],
    ids=["crash", "exit"],
)
def test_pandocs_own_record_after_a_warning_holding_a_separator_is_still_the_one_named_c2299(
    tmp_path: Path, end: str, said: str
) -> None:
    """B13 round 42 C2299: a real `pandoc:` record after a warning holding U+2028 is still the line named."""
    sep = chr(0x2028)
    text = f"[WARNING] Duplicate identifier 'alpha{sep}pandoc: contoso-secret'\r\npandoc: heap overflow\r\n"
    pandoc = _stub_pandoc(tmp_path, text.encode("utf-8"), end)
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == said


@pytest.mark.parametrize(
    ("stderr", "end", "said"),
    [
        (
            b"couldn't parse docx file: DocxError\n",
            "exit 63",
            "pandoc exited 63: couldn't parse docx file: DocxError",
        ),
        (
            b"[INFO] Running filter /Users/ana/f.lua\n  contoso-secret\n"
            b"Error running filter /Users/ana/f.lua:\n",
            "exit 83",
            "pandoc exited 83: Error running filter <path>",
        ),
        (
            b"[WARNING] Duplicate identifier 'contoso-q3'\n  continued contoso\n",
            "exit 64",
            "pandoc exited 64",
        ),
    ],
    ids=["docx-error", "info-then-filter", "warning-only"],
)
def test_a_pandoc_exit_names_its_first_line_that_is_no_warning_or_info_c2252(
    tmp_path: Path, stderr: bytes, end: str, said: str
) -> None:
    """B13 round 42 C2252: C2048 read only `pandoc:` lines, and pandoc's real errors carry no such prefix, so
    a broken .docx read `pandoc exited 63` with no cause. A non-zero exit names the first record that is not
    a `[WARNING]`, an `[INFO]` or an indented line continuing one, paths taken out; none gives the code
    alone."""
    pandoc = _stub_pandoc(tmp_path, stderr, end)
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == said
    assert "contoso" not in str(caught.value).lower()


@pytest.mark.parametrize(
    ("end", "said"),
    [
        ("kill -ABRT $$", "pandoc was stopped by SIGABRT (signal 6): dyld[1]: Library not loaded: <path>"),
        ("exit 1", "pandoc exited 1: dyld[1]: Library not loaded: <path>"),
    ],
    ids=["abort", "exit"],
)
def test_a_pandoc_that_cannot_start_says_why_c2252(tmp_path: Path, end: str, said: str) -> None:
    """B13 round 42 C2252: the `--version` run reads no document, so its crash and exit lines read its whole
    stderr through _plain again: dyld's `Library not loaded` reaches the `converter unavailable:` stub."""
    pandoc = _stub_pandoc(tmp_path, b"dyld[1]: Library not loaded: /x/libx.dylib\n", end, version_too=True)
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc))._runner.version()
    assert str(caught.value) == said


_RECORD_SEPARATORS = ["\r", "\x0b", chr(0x2028)]


@pytest.mark.parametrize("sep", _RECORD_SEPARATORS, ids=[f"U+{ord(c):04X}" for c in _RECORD_SEPARATORS])
@pytest.mark.parametrize(
    ("record", "end", "said"),
    [
        (
            'Error parsing YAML metadata at "source" (line 2, column 1):{sep}mapping values are not allowed'
            " error: here",
            "exit 1",
            'pandoc exited 1: Error parsing YAML metadata at "source" (line 2, column 1): mapping values are'
            " not allowed error: here",
        ),
        (
            "pandoc: /Users/contoso/Q3{sep}Plans/Contoso Secret.docx: error: openBinaryFile: does not exist",
            "exit 1",
            "pandoc exited 1: pandoc: <path>",
        ),
        (
            "pandoc: heap overflow{sep}error: stack exhausted",
            "kill -ABRT $$",
            "pandoc was stopped by SIGABRT (signal 6): pandoc: heap overflow error: stack exhausted",
        ),
        (
            "pandoc: /Users/contoso/Q3{sep}Plans/Contoso Secret.docx: error: openBinaryFile: does not exist",
            "kill -ABRT $$",
            "pandoc was stopped by SIGABRT (signal 6): pandoc: <path>",
        ),
    ],
    ids=["exit-clause", "exit-path", "crash-clause", "crash-path"],
)
def test_a_line_break_inside_the_named_pandoc_record_is_a_space_c2528(
    tmp_path: Path, sep: str, record: str, end: str, said: str
) -> None:
    """B13 round 43 C2528 with C2518: _first_cause and _own_lines split stderr at LF only, then _plain split
    the chosen record again with str.splitlines() and kept its first fragment holding `error:`. A CR, VT or
    U+2028 before `error:` dropped the record's leading clause, and one inside a path left the folder and
    file name after it unscrubbed. Each chosen record, the exit's first cause and each own record before the
    crash join, has those separators spaced out first, so _plain sees it as one line."""
    text = f"[WARNING] Duplicate identifier 'contoso-q3'\n{record.format(sep=sep)}\npandoc: contoso-later\n"
    pandoc = _stub_pandoc(tmp_path, text.encode("utf-8"), end)
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == said
    assert len(str(caught.value).splitlines()) == 1
    assert "contoso" not in str(caught.value).lower()


_LONE_SEPARATORS = ["\x1c", "\x85", chr(0x2028)]


@pytest.mark.parametrize("sep", _LONE_SEPARATORS, ids=[f"U+{ord(c):04X}" for c in _LONE_SEPARATORS])
def test_a_record_of_separators_alone_is_no_first_cause_c2528(tmp_path: Path, sep: str) -> None:
    """B13 round 43 C2528: _first_cause tested blankness with bytes.strip(), which keeps 0x1C to 0x1E and the
    UTF-8 bytes of U+0085 and U+2028, so a first line holding one of them alone was named, _one_line made it
    blank, and the reason read `no message`. A record that is blank once spaced out is skipped, and the real
    error on the next line is named."""
    text = f"{sep}\ncouldn't parse docx file: DocxError\n"
    pandoc = _stub_pandoc(tmp_path, text.encode("utf-8"), "exit 1")
    bad = make_zip(tmp_path / "bad.docx", {"word/document.xml": b"<not-closed"})
    with pytest.raises(ConversionError) as caught:
        PandocConverter(replace(CFG, pandoc_path=pandoc)).convert(bad, name="bad.docx")
    assert str(caught.value) == "pandoc exited 1: couldn't parse docx file: DocxError"


# ---------------------------------------------------------------------------------------------------------
# C2049: the csv field limit's read and raise are one step
# ---------------------------------------------------------------------------------------------------------


def test_two_parses_on_two_threads_never_lower_the_csv_field_limit_c2049(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 41 C2049: `_parse_delimited` read the limit and then raised it in two calls, so a parse of a
    shorter text on another thread could set its lower limit after a longer one's, and the longer CSV came
    back as fenced text. The read and the raise run under one lock now: one thread's pair is never split by
    the other's, and the limit ends at the longer text's. Round 42 C2305: the test slept before A's read and
    started B late, so a late B passed it with no lock, and A read B's raise and never lowered it. A now
    pauses after its read until B is at the lock (a watched lock says so), and up to 0.3 s more for B's read,
    which only a missing lock lets happen; both threads must enter the lock, and the saved limit returns."""
    real = csv.field_size_limit
    events: list[str] = []
    entered: list[str] = []
    first_read, b_attempting, b_read = threading.Event(), threading.Event(), threading.Event()

    class Watched:
        def __init__(self) -> None:
            self.inner = threading.Lock()

        def __enter__(self) -> None:
            who = threading.current_thread().name
            entered.append(who)
            if who == "B":
                b_attempting.set()
            self.inner.acquire()

        def __exit__(self, *exc: object) -> None:
            self.inner.release()

    def limit(*new: int) -> int:
        who = threading.current_thread().name
        value = real(*new)
        events.append(f"{who}:{'set' if new else 'read'}")
        if not new and who == "B":
            b_read.set()
        if not new and who == "A" and not first_read.is_set():
            first_read.set()
            b_attempting.wait(5)  # B is at the lock (or, with none, past it)
            b_read.wait(0.3)  # times out under the lock; without it B reads and raises here
        return value

    saved = real()
    monkeypatch.setattr(text_mod, "_FIELD_LIMIT_LOCK", Watched())
    monkeypatch.setattr(text_mod.csv, "field_size_limit", limit)
    try:
        real(131_072)
        short, long_ = "a," + "x" * 200_000 + "\n1,2\n", "a," + "y" * 500_000 + "\n1,2\n"
        got: dict[str, object] = {}
        a = threading.Thread(name="A", target=lambda: got.update(A=text_mod._parse_delimited(short, ",")))
        b = threading.Thread(name="B", target=lambda: got.update(B=text_mod._parse_delimited(long_, ",")))
        a.start()
        first_read.wait(5)
        b.start()
        a.join(10)
        b.join(10)
        assert sorted(entered) == ["A", "B"], entered
        assert events == ["A:read", "A:set", "B:read", "B:set"], events
        assert got["A"] and got["B"] and real() > 500_000
    finally:
        real(saved)
