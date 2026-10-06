"""convert/ocr.py and the Swift helper it runs (convert/vision_ocr.swift).

Most tests run a fake helper, a Python script that speaks the helper's JSON protocol, so they are fast and
the same everywhere.  The build tests use stub xcode-select, xcrun and swiftc.  One test builds and runs the
real Apple Vision helper; it is skipped where there are no macOS developer tools.

``write_fake``, ``fake_image`` and ``fake_engine`` are for the converter tests too.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from agentsync import governance
from agentsync.config import ConvertConfig
from agentsync.convert import ocr
from agentsync.convert.cache import ConverterCache
from agentsync.ops import doctor
from test_launcher import _have_devtools

ROOT = Path(__file__).resolve().parents[1]
CFG = ConvertConfig()
DAY = 24 * 3600

FAKE_HELPER = r"""#!{python}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(sys.argv[0] + ".calls", "a") as log:
    log.write(json.dumps({"args": args, "cwd": os.getcwd(), "env": sorted(os.environ)}) + "\n")
if "--version" in args:
    print({version!r})
    sys.exit({version_exit})
if {fail}:
    sys.stderr.write("warning: about to fail\nerror: the fake helper was told to fail\n")
    sys.exit(3)
reply = {reply!r}
if reply is not None:
    print(reply)
    sys.exit(0)
frames, paths, it = 1, [], iter(args)
for a in it:
    if a in ("--languages", "--tile", "--min-px", "--max-megapixels"):
        next(it)
    elif a == "--frames":
        frames = int(next(it))
    elif a == "--":
        paths += list(it)
    else:
        paths.append(a)
results = []
for index, p in enumerate(paths):
    item = {"index": index, "frame": 0, "frames": 0, "width": 0, "height": 0, "lines": [], "skipped": False}
    data = Path(p).read_bytes() if Path(p).is_file() else None
    at = -1 if data is None else data.find(b"FAKE-OCR:")
    if at < 0:
        results.append({**item, "error": "no such file" if data is None else "not an image"})
        continue
    spec = json.loads(data[at + 9:].decode())
    item.update(width=spec["size"][0], height=spec["size"][1], frames=len(spec["frames"]))
    if spec.get("error") or spec.get("skipped"):
        results.append({**item, "error": spec.get("error"), "skipped": bool(spec.get("skipped"))})
        continue
    for n, lines in enumerate(spec["frames"][:frames]):
        results.append({**item, "frame": n, "lines": [
            {"text": t, "x": x, "y": y, "w": w, "h": h, "confidence": rest[0] if rest else 1.0}
            for t, x, y, w, h, *rest in lines]})
print(json.dumps({"results": results}))
"""
FAKE_VERSION = '{"engine": "paper-vision", "helper": "0.3.0", "revision": 2}'


def write_fake(
    path: Path,
    *,
    fail: bool = False,
    reply: str | None = None,
    version: str = FAKE_VERSION,
    version_exit: int = 0,
) -> Path:
    """The fake helper at ``path``, owner-only.  It reads what ``fake_image`` wrote and logs each call in
    ``<path>.calls``.  ``fail``: every read exits 3.  ``reply``: every read prints this instead.  ``version``
    and ``version_exit``: what ``--version`` prints and returns."""
    values = {"python": sys.executable, "fail": fail, "reply": reply, "version": version}
    text = FAKE_HELPER.replace("{version_exit}", str(version_exit))
    for key, value in values.items():
        text = text.replace("{" + key + "!r}", repr(value)).replace("{" + key + "}", str(value))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)
    return path


def calls(helper: Path) -> list[dict[str, Any]]:
    log = Path(str(helper) + ".calls")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def fake_image(
    path: Path,
    frames: list[list[list[Any]]] | None = None,
    *,
    size: tuple[int, int] = (800, 600),
    prefix: bytes = b"",
    **spec: Any,
) -> Path:
    """A file the fake helper 'reads': per frame a list of ``[text, x, y, w, h]`` or ``[..., confidence]``.
    ``error="too large"`` or ``skipped=True`` make it report that instead.  ``prefix`` goes first, for a
    caller that needs the file to be a real image as well."""
    doc = {"size": list(size), "frames": frames if frames is not None else [[]], **spec}
    path.write_bytes(prefix + b"FAKE-OCR:" + json.dumps(doc).encode())
    return path


def fake_engine(folder: Path, **kw: Any) -> ocr.OcrEngine:
    """An engine around a fake helper in ``folder`` (see ``write_fake`` for ``kw``)."""
    return ocr.OcrEngine(
        write_fake(folder / "fake-ocr", **kw), name="paper-vision", revision=2, helper_version="0.3.0"
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.fixture(autouse=True)
def _ocr_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about OCR being on: the suite's off switch is set per test where it matters."""
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")


# ---------------------------------------------------------------------------------------------------------
# reading order
# ---------------------------------------------------------------------------------------------------------

Spec = tuple[str, float, float, float, float] | tuple[str, float, float, float, float, float]


def img(*lines: Spec, width: int = 1000, height: int = 1000) -> ocr.OcrImage:
    return ocr.OcrImage(
        width=width,
        height=height,
        frames=1,
        frame=0,
        lines=tuple(
            ocr.OcrLine(text=t[0], x=t[1], y=t[2], w=t[3], h=t[4], confidence=t[5] if len(t) > 5 else 1.0)
            for t in lines
        ),
    )


def px(*lines: Spec) -> ocr.OcrImage:
    """Boxes given in pixels: in an image one pixel wide and high a fraction is a pixel count."""
    return img(*lines, width=1, height=1)


def test_reading_order_two_columns_under_a_title() -> None:
    left = [
        (f"west side, sentence {i}, long enough to count as running text", 0.06, 0.12 + i * 0.03, 0.38, 0.02)
        for i in range(8)
    ]
    right = [
        (f"east side, sentence {i}, long enough to count as running text", 0.56, 0.12 + i * 0.03, 0.38, 0.02)
        for i in range(8)
    ]
    out = ocr.text_lines(img(("A headline over the two columns", 0.06, 0.03, 0.88, 0.04), *right, *left))
    assert out[:2] == ["A headline over the two columns", ""]
    assert out[2:10] == [t[0] for t in left]
    assert out[10] == "" and out[11:] == [t[0] for t in right]


def test_reading_order_reads_a_table_row_by_row() -> None:
    cells = [
        ("Model", 0.06, 0.10),
        ("Year", 0.40, 0.10),
        ("Frame", 0.72, 0.10),
        ("Contoso Tourer", 0.06, 0.20),
        ("2025", 0.40, 0.20),
        ("54 cm", 0.72, 0.20),
    ]
    out = ocr.text_lines(img(*[(t, x, y, 0.15, 0.03) for t, x, y in cells]))
    assert out == ["Model | Year | Frame", "", "Contoso Tourer | 2025 | 54 cm"]


def test_reading_order_paragraphs_and_words_on_one_row() -> None:
    out = ocr.text_lines(
        img(
            ("Opening paragraph, first line", 0.05, 0.10, 0.5, 0.02),
            ("opening paragraph, second line", 0.05, 0.13, 0.5, 0.02),
            ("Closing paragraph", 0.05, 0.30, 0.3, 0.02),
            ("goes on", 0.36, 0.30, 0.1, 0.02),  # the same row, a word gap away: joined with a space
            ("tab\tand\nnewline", 0.05, 0.60, 0.3, 0.02),
        )
    )
    assert out == [
        "Opening paragraph, first line",
        "opening paragraph, second line",
        "",
        "Closing paragraph goes on",
        "",
        "tab and newline",
    ]
    assert ocr.text_lines(img()) == []
    assert ocr.text_lines(img(("no size reported", 0.1, 0.1, 0.5, 0.02), width=0, height=0)) == [
        "no size reported"
    ]


def test_reading_order_drops_only_short_low_confidence_lines() -> None:
    """The noise rule, at its edges: one or two characters AND confidence under 0.35."""
    specs = [("ab", 0.34), ("cd", 0.35), ("efg", 0.0), ("h", 0.34), ("i", 0.35), (" jk ", 0.1)]
    lines = [(text, 100.0, 100.0 + 100 * n, 200.0, 20.0, conf) for n, (text, conf) in enumerate(specs)]
    assert [ln for ln in ocr.text_lines(px(*lines)) if ln] == ["cd", "efg", "i"]


def test_reading_order_separates_cells_only_past_one_line_height() -> None:
    """Boxes on one row are joined with a space up to one median line height apart, with " | " beyond."""
    one_row = lambda gap: px(("left", 100, 100, 200, 20), ("right", 300 + gap, 100, 200, 20))  # noqa: E731
    assert ocr.text_lines(one_row(20)) == ["left right"]
    assert ocr.text_lines(one_row(21)) == ["left | right"]


def test_reading_order_two_short_lists_side_by_side_read_row_by_row() -> None:
    """Short texts whose rows line up are cells, not columns of prose: the row is the unit."""
    left = [(t, 100, 100 + 50 * n, 120, 30) for n, t in enumerate(("apples", "pears", "plums"))]
    right = [(t, 600, 100 + 50 * n, 120, 30) for n, t in enumerate(("red", "green", "purple"))]
    assert ocr.text_lines(px(*left, *right)) == ["apples | red", "pears | green", "plums | purple"]


@pytest.mark.parametrize("lean", [1, -1])
def test_reading_order_keeps_the_lines_of_a_tilted_scan_apart_and_in_order(lean: int) -> None:
    """Six 1950 px lines, 42 px high at a 50 px pitch, scanned 2 degrees off: each upright box is 110 px
    high and overlaps its neighbours, and the left edge drifts one way or the other.  One row per line, top
    down."""
    lines = [(f"line {i + 1}", 300 - lean * 1.75 * i, 300 + 50 * i, 1950, 110) for i in range(6)]
    assert ocr.text_lines(px(*reversed(lines))) == [f"line {i + 1}" for i in range(6)]


def test_reading_order_a_tall_label_joins_the_one_row_it_is_centred_on() -> None:
    cells = [(f"r{r}c{c}", 250 * c, 100 * r, 120, 40) for r in (1, 2, 3) for c in (1, 2, 3)]
    out = ocr.text_lines(px(("Totals", 50, 100, 100, 240), *cells))
    assert out == ["r1c1 | r1c2 | r1c3", "Totals | r2c1 | r2c2 | r2c3", "r3c1 | r3c2 | r3c3"]


def test_reading_order_does_not_depend_on_the_order_the_helper_reports() -> None:
    lines: list[Spec] = [
        (f"cell {r}.{c}", 100 + 300 * c, 100 + 60 * r, 150, 30) for r in range(4) for c in range(3)
    ]
    lines += [("A heading over it all", 100, 20, 700, 40), ("a closing remark far below", 100, 600, 500, 30)]
    want = ocr.text_lines(px(*lines))
    assert want[0] == "A heading over it all" and want[-1] == "a closing remark far below"
    for step in (3, 5, 11):  # three fixed shuffles of the fourteen
        assert ocr.text_lines(px(*[lines[(n * step) % len(lines)] for n in range(len(lines))])) == want


def test_reading_order_a_staircase_deeper_than_the_cut_loses_no_text() -> None:
    """Every box is its own block here, so the cut peels one per level and runs into its depth limit."""
    count = ocr._MAX_DEPTH + 50
    out = ocr.text_lines(px(*[(f"step {i}", 100 * i, 50 * i, 60, 20) for i in range(count)]))
    assert [ln for ln in out if ln] == [f"step {i}" for i in range(count)]


# ---------------------------------------------------------------------------------------------------------
# reading with the helper
# ---------------------------------------------------------------------------------------------------------


def test_read_runs_the_helper_in_the_work_dir_and_returns_one_tuple_per_image(tmp_path: Path) -> None:
    eng = fake_engine(tmp_path / "bin")
    work = tmp_path / "work"
    work.mkdir()
    one = fake_image(work / "one.png", [[["Alpha", 0.1, 0.1, 0.2, 0.05], ["beta", 0.5, 0.1, 0.2, 0.05, 0.4]]])
    pages = fake_image(
        work / "pages.tiff", [[["page one", 0.1, 0.1, 0.5, 0.05]], [["page two", 0.1, 0.1, 0.5, 0.05]]]
    )
    assert eng.read([], work_dir=work, budget_s=60) == [] and calls(eng.helper) == []

    (first,), (cover,) = eng.read([one, pages], work_dir=work, budget_s=60)
    assert (first.width, first.height, first.frames, first.frame) == (800, 600, 1, 0)
    assert first.lines == (
        ocr.OcrLine("Alpha", 1.0, 0.1, 0.1, 0.2, 0.05),
        ocr.OcrLine("beta", 0.4, 0.5, 0.1, 0.2, 0.05),
    )
    assert (first.error, first.skipped) == (None, False)
    assert cover.frames == 2 and ocr.text_lines(cover) == ["page one"]
    (call,) = calls(eng.helper)
    assert call["args"] == [
        *("--languages", "en-US", "--tile", "1536", "--frames", "1"),
        *("--min-px", "48", "--max-megapixels", "50", "--", str(one), str(pages)),
    ]
    assert Path(call["cwd"]) == work.resolve()

    ((_, second),) = eng.read([pages], work_dir=work, budget_s=60, frames=ocr.MAX_PAGES + 5)
    assert second.frame == 1 and ocr.text_lines(second) == ["page two"]
    assert calls(eng.helper)[-1]["args"][5] == str(ocr.MAX_PAGES)


def test_read_reports_a_file_it_cannot_read_on_its_result_not_as_an_exception(tmp_path: Path) -> None:
    eng = fake_engine(tmp_path / "bin")
    images = [
        fake_image(tmp_path / "icon.png", skipped=True, size=(32, 32)),
        fake_image(tmp_path / "poster.png", error="too large", size=(9000, 9000)),
        tmp_path / "not-an-image.png",
        fake_image(tmp_path / "fine.png", [[["still read", 0.1, 0.1, 0.5, 0.05]]]),
    ]
    images[2].write_bytes(b"plain text")
    (icon,), (poster,), (text,), (fine,) = eng.read(images, work_dir=tmp_path, budget_s=60)
    assert (icon.skipped, icon.error, icon.lines, icon.width) == (True, None, (), 32)
    assert (poster.skipped, poster.error, poster.width) == (False, "too large", 9000)
    assert (text.error, text.frames) == ("not an image", 0)
    assert ocr.text_lines(fine) == ["still read"] and ocr.text_lines(poster) == []
    # A path that is not there is the caller's mistake, never a settled "unreadable": it raises.
    with pytest.raises(ocr.OcrError, match="could not open an image it was given"):
        eng.read([tmp_path / "gone.png"], work_dir=tmp_path, budget_s=60)


def test_read_batches_images_and_shares_one_time_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eng = fake_engine(tmp_path / "bin")
    images = [
        fake_image(tmp_path / f"{n:02d}.png", [[[f"image {n}", 0.1, 0.1, 0.5, 0.05]]])
        for n in range(ocr._BATCH + 1)
    ]
    timeouts: list[float] = []
    real_run = subprocess.run

    def run(argv: list[str], **kw: Any) -> Any:
        timeouts.append(kw["timeout"])
        return real_run(argv, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    ticks = iter([100.0, 100.0, 130.0])  # the start, then before each of the two batches
    monkeypatch.setattr(ocr, "_clock", lambda: next(ticks))
    got = eng.read(images, work_dir=tmp_path, budget_s=60)
    assert [ocr.text_lines(frames[0]) for frames in got] == [[f"image {n}"] for n in range(len(images))]
    assert [len(c["args"]) - 11 for c in calls(eng.helper)] == [ocr._BATCH, 1]
    assert timeouts == [60.0, 30.0], "each batch may use what is left of the budget, no more"

    ticks = iter([100.0, 100.0, 160.0])  # the first batch used the whole budget
    with pytest.raises(ocr.OcrError, match="ran out of time"):
        eng.read(images, work_dir=tmp_path, budget_s=60)
    assert len(calls(eng.helper)) == 3, "the second batch was never started"
    ticks = iter([100.0, 100.0])
    with pytest.raises(ocr.OcrError, match="ran out of time"):
        fake_engine(tmp_path / "unused").read(images, work_dir=tmp_path, budget_s=0)
    assert calls(tmp_path / "unused" / "fake-ocr") == []


def test_read_turns_every_helper_failure_into_an_ocr_error_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = fake_image(tmp_path / "a.png")
    failing = fake_engine(tmp_path / "failing", fail=True)
    with pytest.raises(ocr.OcrError) as exited:
        failing.read([image], work_dir=tmp_path, budget_s=60)
    assert str(exited.value) == "the OCR helper exited 3: error: the fake helper was told to fail"

    gone = fake_engine(tmp_path / "gone")
    gone.helper.unlink()
    with pytest.raises(ocr.OcrError) as missing:
        gone.read([image], work_dir=tmp_path, budget_s=60)
    assert str(missing.value) == "the OCR helper cannot be checked: No such file or directory"

    slow = fake_engine(tmp_path / "slow")

    def run(argv: list[str], **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ocr.OcrError) as timed_out:
        slow.read([image], work_dir=tmp_path, budget_s=60)
    assert str(timed_out.value) == "the OCR helper ran out of time" and timed_out.value.__cause__ is None

    def refuse(argv: list[str], **kw: Any) -> Any:
        raise PermissionError(13, "Permission denied", argv[0])

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(ocr.OcrError) as refused:
        slow.read([image], work_dir=tmp_path, budget_s=60)
    assert str(refused.value) == "the OCR helper does not run: Permission denied"
    for error in (exited, missing, timed_out, refused):
        assert str(tmp_path) not in str(error.value) and "/" not in str(error.value)


TOOL_MESSAGES: dict[str, tuple[str, str]] = {
    "a quoted path with spaces and brackets": (
        "error: cannot open 'HOME/Library/Application Support/agentsync/staging/run 1/"
        "Contoso notes v3 (final).png' for reading",
        "error: cannot open '<path>' for reading",
    ),
    "a path in double quotes": (
        'error: cannot open "/Volumes/Contoso Share/page 2.png": no such file',
        'error: cannot open "<path>": no such file',
    ),
    "two quoted paths": (
        "error: cannot copy '/tmp/a b.png' to '/tmp/c d.png' at all",
        "error: cannot copy '<path>' at all",
    ),
    "an unquoted path runs to the end of the line": (
        "error: no such file or directory: HOME/Library/Application Support/agentsync/page 2.png",
        "error: no such file or directory: <path>",
    ),
    "a compiler's line and column end an unquoted path": (
        "/Applications/Xcode beta.app/Contents/Developer/a.swiftinterface:12:3: error: no module 'Vision'",
        "<path>:12:3: error: no module 'Vision'",
    ),
    "the line that says error, not the first": (
        "HOME/x y.swift:1:1: warning: unused\nmain.swift:9:5: error: expected ')' in expression / list",
        "main.swift:9:5: error: expected ')' in expression / list",
    ),
    "a slash that starts no path": (
        "error: an I/O error, and/or 3/4 of nothing",
        "error: an I/O error, and/or 3/4 of nothing",
    ),
}


@pytest.mark.parametrize(("message", "plain"), TOOL_MESSAGES.values(), ids=TOOL_MESSAGES.keys())
def test_a_tool_message_keeps_its_words_and_loses_every_path(message: str, plain: str) -> None:
    """The default state dir is under ``Application Support``: a path does not end at its first space."""
    assert ocr._plain(message.replace("HOME", str(Path.home()))) == plain
    assert ocr._plain(plain) == plain, "the reason read back from the marker is scrubbed again"


def item(**kw: Any) -> dict[str, Any]:
    size = {"width": 10, "height": 10}
    return {"index": 0, "frame": 0, "frames": 1, **size, "lines": [], "skipped": False, **kw}


def line(**kw: Any) -> dict[str, Any]:
    return {"text": "t", "confidence": 1.0, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1, **kw}


BAD_ANSWERS: dict[str, str] = {
    "not JSON": "Vision says hello",
    "a list, not an object": "[]",
    "no results": "{}",
    "results not a list": '{"results": {}}',
    "a result that is not an object": '{"results": [7]}',
    "no result for the image": '{"results": []}',
    "a result too many": json.dumps({"results": [item(), item(index=1)]}),
    "the wrong image first": json.dumps({"results": [item(index=1)]}),
    "a frame skipped": json.dumps({"results": [item(), item(frame=2)]}),
    "a frame with no first frame": json.dumps({"results": [item(frame=1)]}),
    "a size that is a string": json.dumps({"results": [item(width="10")]}),
    "a size that is true": json.dumps({"results": [item(height=True)]}),
    "a negative frame count": json.dumps({"results": [item(frames=-1)]}),
    "no frame number": json.dumps({"results": [{k: v for k, v in item().items() if k != "frame"}]}),
    "lines not a list": json.dumps({"results": [item(lines="text")]}),
    "a line that is not an object": json.dumps({"results": [item(lines=["text"])]}),
    "a line without text": json.dumps({"results": [item(lines=[line(text=None)])]}),
    "a box that is a string": json.dumps({"results": [item(lines=[line(x="0.1")])]}),
    "a box that is true": json.dumps({"results": [item(lines=[line(w=True)])]}),
    "a confidence that is not a number": json.dumps({"results": [item(lines=[line(confidence=None)])]}),
    "a box that is NaN": json.dumps({"results": [item(lines=[line(y=float("nan"))])]}),
    "a box that is infinite": json.dumps({"results": [item(lines=[line(h=float("inf"))])]}),
    "a box too large for a float": json.dumps({"results": [item(lines=[line(x=7)])]}).replace(
        "7", "7" + "0" * 400
    ),
    "skipped that is not true or false": json.dumps({"results": [item(skipped="yes")]}),
    "an error that is not text": json.dumps({"results": [item(error=5)]}),
    "an error this module does not know": json.dumps(
        {"results": [item(error="Vision failed: /Users/ada/x.png")]}
    ),
}


@pytest.mark.parametrize("reply", BAD_ANSWERS.values(), ids=BAD_ANSWERS.keys())
def test_read_refuses_an_answer_that_is_not_the_expected_json(tmp_path: Path, reply: str) -> None:
    eng = fake_engine(tmp_path / "bin", reply=reply)
    with pytest.raises(ocr.OcrError) as error:
        eng.read([fake_image(tmp_path / "a.png")], work_dir=tmp_path, budget_s=60)
    assert str(error.value) == "the OCR helper's answer is not the expected JSON"


def test_the_helper_gets_one_fixed_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVELOPER_DIR", "/somewhere/else")
    monkeypatch.setenv("CONTOSO_TOKEN", "not for a helper")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert ocr._env() == {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "HOME": os.environ["HOME"],
        "TMPDIR": str(tmp_path),
    }
    eng = fake_engine(tmp_path / "bin")
    eng.read([fake_image(tmp_path / "a.png")], work_dir=tmp_path, budget_s=60)
    seen = set(calls(eng.helper)[0]["env"])
    assert {"HOME", "PATH", "TMPDIR"} <= seen and not {
        "DEVELOPER_DIR",
        "CONTOSO_TOKEN",
        "AGENTSYNC_CONFIG",
    } & seen


def test_engine_identity_names_engine_helper_and_layout_but_no_machine(tmp_path: Path) -> None:
    eng = fake_engine(tmp_path)
    assert eng.identity == f"ocr-paper-vision-r2-h0.3.0-l{ocr._LAYOUT_REVISION}"
    assert eng.description == "paper-vision revision 2, helper 0.3.0"
    assert ocr.LANGUAGES == ("en-US",) and ocr.MAX_PAGES == 100 and ocr.MAX_MEGAPIXELS == 50


# ---------------------------------------------------------------------------------------------------------
# probe and engine: looking, never building
# ---------------------------------------------------------------------------------------------------------


def place_fake(cache: Path, **kw: Any) -> Path:
    """A fake helper where this agentsync's built helper goes."""
    return write_fake(ocr._helper_path(cache), **kw)


@pytest.fixture
def no_compiler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Developer tools that leave a trace when anything starts them; the test fails if anything did."""
    trace = tmp_path / "compiler-was-started"
    tool = tmp_path / "traced-tool"
    tool.write_text(f'#!/bin/sh\necho "$0 $*" >> "{trace}"\nexit 1\n')
    tool.chmod(0o700)
    monkeypatch.setattr(ocr, "_XCODE_SELECT", str(tool))
    monkeypatch.setattr(ocr, "_XCRUN", str(tool))
    yield trace
    assert not trace.exists(), trace.read_text()


def test_probe_and_engine_report_the_four_states(tmp_path: Path, no_compiler: Path) -> None:
    cache = tmp_path / "cache"
    assert ocr.probe(CFG, cache) == ("not-built", "the OCR helper is not built")
    assert ocr.engine(CFG, cache) is None
    assert not cache.exists(), "looking writes nothing"

    helper = place_fake(cache)
    assert ocr.probe(CFG, cache) == ("ready", "paper-vision revision 2, helper 0.3.0")
    eng = ocr.engine(CFG, cache)
    assert eng is not None and eng.helper == helper == cache / "ocr" / helper.name
    assert helper.name.startswith("agentsync-ocr-") and len(helper.name) == len("agentsync-ocr-") + 16
    assert [c["args"] for c in calls(helper)] == [["--version"], ["--version"]]

    assert ocr.probe(replace(CFG, ocr=False), cache) == ("off", "[convert] ocr = false")
    assert ocr.engine(replace(CFG, ocr=False), cache) is None
    assert len(calls(helper)) == 2, "a switched-off helper is not even asked for its version"

    helper.unlink()
    ocr._marker(helper).write_text("swiftc did not build the OCR helper (exit 1): error: no such module\n")
    assert ocr.probe(CFG, cache) == (
        "failed",
        "swiftc did not build the OCR helper (exit 1): error: no such module",
    )
    assert ocr.engine(CFG, cache) is None
    ocr._marker(helper).write_text("")
    assert ocr.probe(CFG, cache) == ("failed", "the last build of the OCR helper failed")


@pytest.mark.parametrize(
    ("value", "off"), [("0", True), ("off", True), (" OFF ", True), ("1", False), ("", False)]
)
def test_the_environment_switch_accepts_0_and_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, off: bool
) -> None:
    place_fake(tmp_path)
    monkeypatch.setenv("AGENTSYNC_OCR", value)
    want = (
        ("off", f"AGENTSYNC_OCR={value.strip().lower()}")
        if off
        else ("ready", "paper-vision revision 2, helper 0.3.0")
    )
    assert ocr.probe(CFG, tmp_path) == want
    assert (ocr.engine(CFG, tmp_path) is None) is off


def test_ocr_is_off_where_there_is_no_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    place_fake(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    assert ocr.probe(CFG, tmp_path) == ("off", "on-device OCR needs macOS")
    with pytest.raises(ocr.OcrError, match="on-device OCR needs macOS"):
        ocr.build(tmp_path)


def test_a_new_source_or_new_flags_name_a_new_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = ocr._helper_path(tmp_path)
    monkeypatch.setattr(ocr, "_MIN_MACOS", "13.0")
    flags = ocr._helper_path(tmp_path)
    monkeypatch.setattr(ocr, "_source", lambda: b"// another helper\n")
    assert len({before, flags, ocr._helper_path(tmp_path)}) == 3
    assert ocr._helper_path(Path("~/somewhere")).parent == Path.home() / "somewhere" / "ocr"


VERSION_ANSWERS: dict[str, dict[str, Any]] = {
    "exits 3": {"version_exit": 3},
    "is not JSON": {"version": "agentsync-ocr 2"},
    "is a list": {"version": "[]"},
    "has no revision": {"version": '{"engine": "paper-vision", "helper": "0.3.0"}'},
    "has a revision that is true": {
        "version": '{"engine": "paper-vision", "helper": "0.3.0", "revision": true}'
    },
    "names an engine with a path in it": {
        "version": '{"engine": "../x y", "helper": "0.3.0", "revision": 1}'
    },
    "has a helper version that is a number": {
        "version": '{"engine": "paper-vision", "helper": 2, "revision": 1}'
    },
}


@pytest.mark.parametrize("kw", VERSION_ANSWERS.values(), ids=VERSION_ANSWERS.keys())
def test_a_built_helper_whose_version_fails_is_a_failed_helper(
    tmp_path: Path, no_compiler: Path, kw: dict[str, Any]
) -> None:
    """Built and not answering is OCR that stopped working (doctor's WARN), not OCR that was never built."""
    helper = place_fake(tmp_path, **kw)
    state, detail = ocr.probe(CFG, tmp_path)
    assert state == "failed" and detail.startswith("the OCR helper") and str(tmp_path) not in detail
    assert ocr.engine(CFG, tmp_path) is None
    # With the reason of a failed rebuild beside it, that reason is the news.
    ocr._marker(helper).write_text("no Xcode or Command Line Tools (xcode-select -p names no folder)\n")
    assert ocr.probe(CFG, tmp_path) == (
        "failed",
        "no Xcode or Command Line Tools (xcode-select -p names no folder)",
    )


def test_a_helper_that_hangs_on_version_is_a_failed_helper_after_a_short_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    place_fake(tmp_path)
    waits: list[float] = []

    def run(argv: list[str], **kw: Any) -> Any:
        waits.append(kw["timeout"])
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    assert ocr.probe(CFG, tmp_path) == ("failed", "the OCR helper ran out of time")
    assert waits == [5.0], "doctor runs inside setup-report's 12 s budget"


def test_a_helper_someone_else_could_have_replaced_is_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(cache: Path) -> str:
        state, detail = ocr.probe(CFG, cache)
        assert state == "failed" and ocr.engine(CFG, cache) is None
        assert str(tmp_path) not in detail and "/" not in detail
        return detail

    link = ocr._helper_path(tmp_path / "link")
    link.parent.mkdir(parents=True)
    link.symlink_to(write_fake(tmp_path / "elsewhere" / "real"))
    assert refused(tmp_path / "link") == "the OCR helper is not a regular file this user owns"
    ocr._helper_path(tmp_path / "folder").mkdir(parents=True)
    assert refused(tmp_path / "folder") == "the OCR helper is not a regular file this user owns"

    place_fake(tmp_path / "shared").chmod(0o720)
    assert refused(tmp_path / "shared") == "the OCR helper or its folder can be written by other users"
    place_fake(tmp_path / "open").parent.chmod(0o702)
    assert refused(tmp_path / "open") == "the OCR helper or its folder can be written by other users"
    place_fake(tmp_path / "plain").chmod(0o600)
    assert refused(tmp_path / "plain") == "the OCR helper is not executable"

    eng = fake_engine(tmp_path / "later")  # the check is made before every run, not once
    eng.helper.chmod(0o722)
    with pytest.raises(ocr.OcrError, match="can be written by other users"):
        eng.read([fake_image(tmp_path / "a.png")], work_dir=tmp_path, budget_s=60)

    theirs = place_fake(tmp_path / "theirs")
    monkeypatch.setattr(os, "geteuid", lambda: theirs.stat().st_uid + 1)
    assert refused(tmp_path / "theirs") == "the OCR helper is not a regular file this user owns"
    for name in ("elsewhere/real", "later/fake-ocr"):
        assert calls(tmp_path / name) == []
    for cache in ("shared", "open", "plain", "theirs"):
        assert calls(ocr._helper_path(tmp_path / cache)) == []


def test_an_os_error_is_a_reason_never_an_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    assert ocr.probe(CFG, blocker / "cache") == (
        "failed",
        "the OCR helper cannot be looked up: Not a directory",
    )
    assert ocr.engine(CFG, blocker / "cache") is None
    with pytest.raises(ocr.OcrError) as error:
        ocr.build(blocker / "cache")
    assert str(error.value) == "the OCR helper could not be built: File exists"

    def unreadable() -> bytes:
        raise PermissionError(
            13, "Permission denied", "/Users/ada/site-packages/agentsync/convert/vision_ocr.swift"
        )

    monkeypatch.setattr(ocr, "_source", unreadable)
    assert ocr.probe(CFG, tmp_path) == ("failed", "the OCR helper cannot be looked up: Permission denied")
    assert ocr.engine(CFG, tmp_path) is None
    with pytest.raises(ocr.OcrError) as error:
        ocr.build(tmp_path)
    assert str(error.value) == "the OCR helper could not be built: Permission denied"


def test_the_converter_cache_leaves_the_helper_folder_alone(tmp_path: Path) -> None:
    """The helper lives under the converter cache's root: neither the cache's sweep nor a purge's search for
    cache entries may take it for one."""
    helper = place_fake(tmp_path)
    cache = ConverterCache(tmp_path)
    (tmp_path / "ab" / "stale-key").mkdir(parents=True)
    assert governance._cache_entries(tmp_path, {"some-key"}, {"some-hash"}) == []
    assert cache.gc([]) == 1
    assert helper.is_file() and ocr.probe(CFG, tmp_path)[0] == "ready"


def test_the_helper_source_ships_inside_the_package() -> None:
    """``uv tool install`` and a wheel carry it: the wheel takes every file under src/agentsync."""
    source = resources.files("agentsync.convert").joinpath("vision_ocr.swift")
    assert source.is_file() and b"VNRecognizeTextRequest" in source.read_bytes()
    assert source.read_bytes() == ocr._source()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/agentsync"]
    assert "--no-correction" not in source.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------
# build: stub xcode-select, xcrun and swiftc
# ---------------------------------------------------------------------------------------------------------

STUB_HELPER = """#!/bin/sh
echo '{"engine": "stub-vision", "helper": "9.9.9", "revision": 7}'
"""
SWIFTC_OK = f"""out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
[ -f main.swift ] || {{ echo "error: no main.swift in the build folder" >&2; exit 1; }}
cat > "$out" <<'EOF'
{STUB_HELPER}EOF
"""


def script(path: Path, body: str) -> Path:
    path.write_text(f'#!/bin/sh\necho "$@" >> "$0.calls"\nenv > "$0.env"\n{body}\n')
    path.chmod(0o700)
    return path


def lines_of(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


@contextlib.contextmanager
def loose_umask() -> Iterator[None]:
    """A shell's usual 022 instead of the suite's 077: a mode that is only right by accident shows."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub developer tools: ``xcode-select -p`` names a folder, ``xcrun`` finds ``swiftc`` and an SDK, and
    ``swiftc`` writes a helper that answers ``--version``.  Each logs its arguments in ``<tool>.calls``."""
    folder = tmp_path / "tools"
    (folder / "Developer").mkdir(parents=True)
    (folder / "Sdk").mkdir()
    script(folder / "xcode-select", f'echo "{folder}/Developer"')
    script(folder / "xcrun", f'case "$1" in --find) echo "{folder}/swiftc" ;; *) echo "{folder}/Sdk" ;; esac')
    script(folder / "swiftc", SWIFTC_OK)
    monkeypatch.setattr(ocr, "_XCODE_SELECT", str(folder / "xcode-select"))
    monkeypatch.setattr(ocr, "_XCRUN", str(folder / "xcrun"))
    return folder


def test_build_leaves_an_owner_only_helper_and_builds_it_once(tools: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    with loose_umask():
        helper = ocr.build(cache)
    assert helper == ocr._helper_path(cache)
    assert (mode(helper), mode(helper.parent), mode(cache)) == (0o700, 0o700, 0o700)
    assert doctor._group_other_readable(cache) == [], (
        "status would FAIL docs_repo.permissions on any other mode"
    )
    assert [p.name for p in helper.parent.iterdir()] == [helper.name], "no build folder, no marker left"
    assert ocr.probe(CFG, cache) == ("ready", "stub-vision revision 7, helper 9.9.9")
    eng = ocr.engine(CFG, cache)
    assert eng is not None and eng.identity == "ocr-stub-vision-r7-h9.9.9-l1"

    (compile_,) = lines_of(tools / "swiftc.calls")
    arch = os.uname().machine
    assert (
        compile_
        == f"-O -swift-version 5 -target {arch}-apple-macos12.0 -sdk {tools}/Sdk -o agentsync-ocr main.swift"
    )
    assert lines_of(tools / "xcrun.calls") == ["--find swiftc", "--sdk macosx --show-sdk-path"]
    assert ocr.build(cache) == helper and len(lines_of(tools / "swiftc.calls")) == 1, (
        "a working helper is kept"
    )

    helper.write_text("#!/bin/sh\nexit 1\n")  # the same name, but it no longer runs
    assert ocr.probe(CFG, cache) == ("failed", "the OCR helper exited 1: no message")
    assert ocr.build(cache) == helper and len(lines_of(tools / "swiftc.calls")) == 2
    assert ocr.probe(CFG, cache)[0] == "ready"


def test_build_runs_every_tool_in_the_fixed_environment(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVELOPER_DIR", str(tmp_path))  # would make a real xcode-select -p print this folder
    monkeypatch.setenv("CONTOSO_TOKEN", "not for a compiler")
    ocr.build(tmp_path / "cache")
    for tool in ("xcode-select", "xcrun", "swiftc"):
        names = {ln.split("=", 1)[0] for ln in lines_of(tools / f"{tool}.env")}
        assert {"HOME", "PATH"} <= names and not {"DEVELOPER_DIR", "CONTOSO_TOKEN"} & names, tool


def test_build_without_developer_tools_never_reaches_xcrun(tools: Path, tmp_path: Path) -> None:
    """The /usr/bin/xcrun shim opens the Command Line Tools install dialog on a Mac without them."""
    cache = tmp_path / "cache"
    for body in ("exit 2", f'echo "{tools}/Missing"', "echo"):
        script(tools / "xcode-select", body)
        with pytest.raises(ocr.OcrError) as error:
            ocr.build(cache)
        assert str(error.value) == "no Xcode or Command Line Tools (xcode-select -p names no folder)"
    assert not (tools / "xcrun.calls").exists() and not (tools / "swiftc.calls").exists()
    assert ocr.probe(CFG, cache) == (
        "failed",
        "no Xcode or Command Line Tools (xcode-select -p names no folder)",
    )


def test_build_names_what_the_developer_tools_lack(tools: Path, tmp_path: Path) -> None:
    script(tools / "xcrun", f'case "$1" in --find) echo "{tools}/swiftc" ;; *) echo "{tools}/NoSdk" ;; esac')
    with pytest.raises(ocr.OcrError, match=r"^no macOS SDK in the developer tools$"):
        ocr.build(tmp_path / "cache")
    script(tools / "xcrun", f'case "$1" in --find) echo "{tools}/no-swiftc" ;; *) echo "{tools}/Sdk" ;; esac')
    with pytest.raises(ocr.OcrError, match=r"^no swiftc in the developer tools$"):
        ocr.build(tmp_path / "cache")
    (tools / "swiftc").chmod(0o600)
    script(tools / "xcrun", f'case "$1" in --find) echo "{tools}/swiftc" ;; *) echo "{tools}/Sdk" ;; esac')
    with pytest.raises(ocr.OcrError, match=r"^no swiftc in the developer tools$"):
        ocr.build(tmp_path / "cache")
    script(tools / "xcrun", "exit 1")
    with pytest.raises(ocr.OcrError, match=r"^no swiftc in the developer tools$"):
        ocr.build(tmp_path / "cache")
    assert not (tools / "swiftc.calls").exists()


def test_a_failed_build_leaves_its_reason_until_a_build_works(tools: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    home = Path.home()
    script(
        tools / "swiftc",
        f'echo "{home}/Library/x.swift:1:1: warning: unused" >&2\n'
        f'echo "{cache}/ocr/.build-x/main.swift:9:5: error: cannot find Vision in {home}/My Sdk" >&2\nexit 1',
    )
    reason = "swiftc did not build the OCR helper (exit 1): <path>:9:5: error: cannot find Vision in <path>"
    with loose_umask(), pytest.raises(ocr.OcrError) as error:
        ocr.build(cache)
    helper = ocr._helper_path(cache)
    marker = ocr._marker(helper)
    assert str(error.value) == reason and marker.read_text() == reason + "\n" and mode(marker) == 0o600
    assert marker.name == helper.name + ".failed" and not helper.exists()
    assert [p.name for p in helper.parent.iterdir()] == [marker.name], "the build folder is gone"
    assert ocr.probe(CFG, cache) == ("failed", reason) and doctor._group_other_readable(cache) == []

    script(
        tools / "swiftc", SWIFTC_OK.replace("echo '{", "exit 1; echo '{")
    )  # builds a helper that does not run
    with pytest.raises(ocr.OcrError) as error:
        ocr.build(cache)
    assert str(error.value) == "the OCR helper exited 1: no message" == marker.read_text().strip()
    assert ocr.probe(CFG, cache) == ("failed", "the OCR helper exited 1: no message")

    script(tools / "swiftc", SWIFTC_OK)
    assert ocr.build(cache) == helper and not marker.exists()
    assert ocr.probe(CFG, cache)[0] == "ready"


def test_a_build_that_hangs_is_stopped_and_recorded(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = subprocess.run

    def run(argv: list[str], **kw: Any) -> Any:
        if Path(argv[0]).name == "swiftc":
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        return real_run(argv, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ocr.OcrError) as error:
        ocr.build(tmp_path)
    assert str(error.value) == "swiftc ran out of time building the OCR helper"
    assert ocr.probe(CFG, tmp_path) == ("failed", "swiftc ran out of time building the OCR helper")
    assert [p.name for p in (tmp_path / "ocr").iterdir()] == [ocr._marker(ocr._helper_path(tmp_path)).name]


def test_build_keeps_other_helpers_for_a_week_and_tightens_them(tools: Path, tmp_path: Path) -> None:
    """A cycle that started before an upgrade may still be running the previous helper.  An earlier build left
    its helper and folder readable by everyone: what stays is made owner-only."""
    folder = tmp_path / "cache" / "ocr"
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    names = {
        "agentsync-ocr-0000000000000000": 8,  # a helper from an earlier agentsync, last built 8 days ago
        "agentsync-ocr-0000000000000000.failed": 8,
        "agentsync-ocr-1111111111111111": 6,
        "agentsync-ocr-1111111111111111.failed": 6,
        "notes.txt": 30,  # not ours
    }
    for name in names:
        (folder / name).write_text("x")
        (folder / name).chmod(0o755)
    for name, days in {".build-abandoned": 8, ".build-running": 0}.items():
        (folder / name).mkdir()
        (folder / name / "main.swift").write_text("x")
        names[name] = days
    for name, days in names.items():
        os.utime(folder / name, (time.time() - days * DAY, time.time() - days * DAY))
    helper = ocr.build(tmp_path / "cache")
    left = {p.name: mode(p) for p in folder.iterdir()}
    assert left == {
        helper.name: 0o700,
        "agentsync-ocr-1111111111111111": 0o700,
        "agentsync-ocr-1111111111111111.failed": 0o700,
        ".build-running": 0o700,
        "notes.txt": 0o755,
    }
    assert mode(folder) == 0o700


def test_build_keeps_a_helper_a_cycle_resolved_this_week_however_old_its_build(
    tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The week runs from a helper's last use.  On the day of an upgrade the previous helper was built weeks
    ago, and the cycle that is running when install.sh builds the next one still needs it."""
    cache = tmp_path / "cache"
    month_ago = time.time() - 30 * DAY
    with monkeypatch.context() as earlier:  # an earlier agentsync: another source, so another helper name
        earlier.setattr(ocr, "_source", lambda: b"// the helper of an earlier agentsync\n")
        previous = place_fake(cache)
        unused = write_fake(previous.with_name("agentsync-ocr-0000000000000000"))
        for helper in (previous, unused):
            os.utime(helper, (month_ago, month_ago))
        assert ocr.probe(CFG, cache)[0] == "ready"
        assert previous.stat().st_mtime == pytest.approx(month_ago), "status looks: it stamps nothing"
        running = ocr.engine(CFG, cache)  # a cycle starts
    assert running is not None and running.helper == previous

    current = ocr.build(cache)  # scripts/install.sh, after the upgrade
    assert current != previous and previous.exists() and not unused.exists()
    (page,) = running.read(
        [fake_image(tmp_path / "a.png", [[["still read", 0.1, 0.1, 0.5, 0.05]]])],
        work_dir=tmp_path,
        budget_s=60,
    )
    assert ocr.text_lines(page[0]) == ["still read"]


# ---------------------------------------------------------------------------------------------------------
# python -m agentsync.convert.ocr: how scripts/install.sh builds
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agent-context" / "sources.toml"
    path.parent.mkdir()
    monkeypatch.setenv("AGENTSYNC_CONFIG", str(path))
    return path


def test_the_module_entry_point_builds_under_the_configured_cache_dir(
    tools: Path, tmp_path: Path, config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "my cache"
    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n')
    assert ocr._main() == 0
    assert capsys.readouterr().out == "OCR helper: ready (stub-vision revision 7, helper 9.9.9)\n"
    assert ocr._helper_path(cache).is_file()

    script(tools / "xcode-select", "exit 2")
    ocr._helper_path(cache).unlink()
    assert ocr._main() == 1
    out = capsys.readouterr().out
    assert out == "OCR helper: not built (no Xcode or Command Line Tools (xcode-select -p names no folder))\n"

    config_file.write_text("[convert]\nocr = maybe\n")
    assert ocr._main() == 1
    assert capsys.readouterr().out == "OCR helper: not built (the config cannot be read)\n"
    assert len(lines_of(tools / "swiftc.calls")) == 1


def test_the_module_entry_point_builds_nothing_when_ocr_is_off(
    tools: Path,
    tmp_path: Path,
    config_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A managed Mac can refuse the locally compiled binary before install.sh ever builds it."""
    config_file.write_text(f'[agentsync]\ncache_dir = "{tmp_path / "cache"}"\n[convert]\nocr = false\n')
    assert ocr._main() == 0
    assert capsys.readouterr().out == "OCR helper: off ([convert] ocr = false)\n"
    config_file.unlink()  # no config yet (a first install): the defaults, so the switch still decides
    monkeypatch.setenv("AGENTSYNC_OCR", "0")
    assert ocr._main() == 0
    assert capsys.readouterr().out == "OCR helper: off (AGENTSYNC_OCR=0)\n"
    assert not (tools / "xcode-select.calls").exists() and not (tmp_path / "cache").exists()
    assert not (Path.home() / "Library" / "Caches" / "agentsync").exists()


def test_what_an_earlier_build_left_is_tidied_when_nothing_is_built(
    tools: Path, tmp_path: Path, config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An earlier build left its helper and folder readable by everyone under ``cache_dir``, which status's
    ``docs_repo.permissions`` walks.  Every run of the entry point tidies them: with OCR switched off (the
    Mac that refuses the binary) and when the build fails, not only after a build that works."""
    cache = tmp_path / "cache"
    folder = cache / "ocr"
    kept, gone = "agentsync-ocr-1111111111111111", "agentsync-ocr-0000000000000000"

    def leave() -> None:
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o755)
        for name, days in ((kept, 2), (gone, 30)):
            (folder / name).write_text("x")
            (folder / name).chmod(0o755)
            os.utime(folder / name, (time.time() - days * DAY, time.time() - days * DAY))
        assert doctor._group_other_readable(cache) != []

    def tidied() -> None:
        left = {p.name: mode(p) for p in folder.iterdir() if not p.name.endswith(".failed")}
        assert left == {kept: 0o700} and mode(folder) == 0o700
        assert doctor._group_other_readable(cache) == []

    leave()
    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n[convert]\nocr = false\n')
    assert ocr._main() == 0
    assert capsys.readouterr().out == "OCR helper: off ([convert] ocr = false)\n"
    tidied()
    assert not (tools / "xcode-select.calls").exists(), "switched off, it still builds nothing"

    leave()
    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n')
    script(tools / "swiftc", 'echo "error: the stub compiler fails" >&2\nexit 1')
    assert ocr._main() == 1
    assert capsys.readouterr().out == (
        "OCR helper: not built (swiftc did not build the OCR helper (exit 1): "
        "error: the stub compiler fails)\n"
    )
    tidied()
    assert ocr.probe(CFG, cache)[0] == "failed"


def test_python_dash_m_prints_one_line_and_exits_0(tmp_path: Path) -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "AGENTSYNC_OCR": "off"}
    cp = subprocess.run(
        [sys.executable, "-m", "agentsync.convert.ocr"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (cp.returncode, cp.stdout, cp.stderr) == (0, "OCR helper: off (AGENTSYNC_OCR=off)\n", "")
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------------------------------------
# the real Apple Vision helper (macOS with developer tools only)
# ---------------------------------------------------------------------------------------------------------

FONT = "/System/Library/Fonts/Helvetica.ttc"


@pytest.fixture(scope="session")
def vision(tmp_path_factory: pytest.TempPathFactory) -> ocr.OcrEngine:
    """The real helper, built once per session into a tmp cache dir under its own HOME: a session fixture is
    set up before the function-scoped HOME isolation, and no test may run a helper from the real home."""
    if not _have_devtools() or not Path(FONT).is_file():
        pytest.skip("needs macOS with developer tools (swiftc) to build the Apple Vision helper")
    cache = tmp_path_factory.mktemp("ocr-cache")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(tmp_path_factory.mktemp("ocr-home")))
        mp.delenv("AGENTSYNC_OCR", raising=False)
        ocr.build(cache)
        found = ocr.engine(CFG, cache)
    assert found is not None
    return found


def test_the_real_helper_reads_whole_lines_across_tiles_rotations_and_frames(
    vision: ocr.OcrEngine, tmp_path: Path
) -> None:
    pil = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    font_module = pytest.importorskip("PIL.ImageFont")

    def sheet(width: int, height: int, *texts: tuple[int, int, int, str]) -> Any:
        im = pil.new("RGB", (width, height), "white")
        for x, y, size, text in texts:
            draw_module.Draw(im).text((x, y), text, fill="black", font=font_module.truetype(FONT, size))
        return im

    def said(image: ocr.OcrImage) -> list[str]:
        return [ln.text.casefold() for ln in image.lines]

    # 1. A plain image, read whole.  It is also the canary: a Mac whose Vision cannot run skips the test.
    sheet(
        1600,
        900,
        (80, 80, 44, "Quarterly planning notes"),
        (80, 400, 44, "North depot"),
        (900, 400, 44, "South depot"),
    ).save(tmp_path / "plain.png")
    # 2. Wider than one tile (1536 px): the first line ends at about x = 1640, the second at about 2150, so
    #    each is cut by a tile edge and no tile holds either from end to end.
    fox = "The quick brown fox jumps over the lazy dog near the old stone bridge today"
    long = "A much longer sentence that keeps going across the whole page so it crosses the seam twice maybe"
    sheet(2550, 700, (300, 200, 40, fox), (300, 400, 40, long)).save(tmp_path / "seam.png")
    # 3. Small labels on a large canvas: Vision scales the whole image down and misreads them.  Tiles do not.
    #    Under them one small line about 2560 px long: three tiles each see a piece of it, and nothing else
    #    reads it well.
    words = ["amber", "birch", "cedar", "delta", "ember", "frost", "grove", "haven", "ivory", "jade"]
    words += ["koala", "lemon", "maple", "noble", "olive"]
    labels = [f"{word} station" for word in words]
    ribbon = " ".join(words * 4)
    grid = [(200 + (n % 5) * 760, 300 + (n // 5) * 600, 16, t) for n, t in enumerate(labels)]
    sheet(4000, 2400, *grid, (300, 2200, 16, ribbon)).save(tmp_path / "labels.png")
    # 4. A photo stored on its side (EXIF orientation 6), large enough to be tiled.
    upright = sheet(3000, 2000, (200, 200, 80, "Upright heading text"), (2200, 1500, 40, "bottom right note"))
    exif = pil.Exif()
    exif[0x0112] = 6
    upright.transpose(pil.Transpose.ROTATE_90).save(tmp_path / "turned.jpg", exif=exif, quality=92)
    # 5. An icon, an image over the megapixel limit, a PDF named like an image, an empty file.
    sheet(32, 32, (2, 8, 12, "ok")).save(tmp_path / "icon.png")
    pil.new("1", (8000, 8000), 1).save(tmp_path / "huge.png")
    sheet(600, 400, (50, 50, 40, "a PDF, whatever its name")).save(tmp_path / "renamed.png", format="PDF")
    (tmp_path / "empty.png").write_bytes(b"")
    # 6. A three-page TIFF.
    pages = [sheet(1200, 800, (100, 100, 48, f"Scanned page number {n}")) for n in (1, 2, 3)]
    pages[0].save(tmp_path / "pages.tiff", save_all=True, append_images=pages[1:])
    # 7. Words wider than the 384 px two tiles share, small on a canvas so wide that only the tiles read
    #    them.  The first starts at x = 1090 and runs past 1536: each tile of the seam cuts it.  The second
    #    sits inside the shared 1152..1536: both tiles read all of it.
    chain, fits = "-".join(words[:11]), "-".join(words[:8])
    measure = font_module.truetype(FONT, 16).getlength
    wide_texts = [f"see {chain} for the details", fits]
    sheet(
        8000,
        1500,
        (1090 - round(measure("see ")), 400, 16, wide_texts[0]),
        (1152 + round((384 - measure(fits)) / 2), 1000, 16, fits),
    ).save(tmp_path / "wide.png")

    names = ["plain.png", "seam.png", "labels.png", "turned.jpg"]
    names += ["icon.png", "huge.png", "renamed.png", "empty.png", "wide.png"]
    results = vision.read([tmp_path / n for n in names], work_dir=tmp_path, budget_s=300)
    (plain,), (seam,), (small,), (turned,), (icon,), (huge,), (renamed,), (empty,), (wide,) = results
    if plain.error == "recognition failed":
        pytest.skip("Apple Vision text recognition does not run on this machine")

    assert vision.identity == f"ocr-apple-vision-r{vision.revision}-h2.0.0-l{ocr._LAYOUT_REVISION}"
    assert (plain.width, plain.height, plain.frames, plain.error) == (1600, 900, 1, None)
    assert ocr.text_lines(plain) == ["Quarterly planning notes", "", "North depot | South depot"]
    assert said(seam) == [fox.casefold(), long.casefold()], "each line whole, and once"
    assert sorted(t for t in said(small) if t.endswith("station")) == labels, "every label, each once"
    (stitched,) = (ln for ln in small.lines if not ln.text.endswith("station"))
    # The word count and the ends, not every letter: a seam that repeats or drops a word shows.
    joined = stitched.text.casefold().split()
    assert (len(joined), joined[0], joined[-1]) == (len(words) * 4, "amber", "olive")
    assert stitched.x < 0.08 and stitched.x + stitched.w > 0.68, "one box from end to end"
    assert (turned.width, turned.height) == (3000, 2000), "the upright size, not the stored 2000 x 3000"
    assert said(turned) == ["upright heading text", "bottom right note"]
    note = turned.lines[1]
    assert note.x > 0.7 and note.y > 0.7, "boxes are fractions of the upright image"
    assert (icon.skipped, icon.error, icon.lines, icon.width, icon.height) == (True, None, (), 32, 32)
    assert (huge.error, huge.skipped, huge.width) == ("too large", False, 8000)
    assert (renamed.error, empty.error) == ("unsupported image type", "not an image")

    def outline(text: str) -> list[tuple[int, str, str]]:
        """Each word's length and ends, not every letter: a word a seam repeats, splits or shortens shows."""
        return [(len(word), word[:3], word[-3:]) for word in text.casefold().split()]

    assert [outline(ln.text) for ln in wide.lines] == [outline(text) for text in wide_texts]

    (frames,) = vision.read([tmp_path / "pages.tiff"], work_dir=tmp_path, budget_s=300, frames=ocr.MAX_PAGES)
    assert [(f.frame, f.frames, said(f)) for f in frames] == [
        (n, 3, [f"scanned page number {n + 1}"]) for n in range(3)
    ]
    (first,) = vision.read([tmp_path / "pages.tiff"], work_dir=tmp_path, budget_s=300)
    assert len(first) == 1 and first[0].frames == 3
    with pytest.raises(ocr.OcrError, match="could not open an image it was given"):
        vision.read([tmp_path / "missing.png"], work_dir=tmp_path, budget_s=300)

    version = subprocess.run([str(vision.helper), "--version"], capture_output=True, check=True)
    assert set(json.loads(version.stdout)) == {"engine", "helper", "revision"}
    for bad in ([], ["--no-correction", "x.png"], ["--tile", "100", "x.png"], ["--frames", "0", "x.png"]):
        usage = subprocess.run([str(vision.helper), *bad], capture_output=True, check=False)
        assert usage.returncode == 64 and usage.stdout == b"", bad


# ---------------------------------------------------------------------------------------------------------
# the helper's tile joining on made-up readings (developer tools only; Vision recognises nothing here)
# ---------------------------------------------------------------------------------------------------------

SEAM_MAIN = """
struct SeamWord: Decodable { let text: String; let x0: Double; let x1: Double }
struct SeamRead: Decodable {
    let col: Int
    let confidence: Float
    let cutLeft: Bool
    let cutRight: Bool
    let words: [SeamWord]
}
struct SeamLine: Encodable { let text: String; let open: Bool }
let seamCases = try! JSONDecoder().decode(
    [[SeamRead]].self, from: FileHandle.standardInput.readDataToEndOfFile())
let seamColumns = starts(PAGE, TILE).map { (x: Double($0), width: Double(min(TILE, PAGE - $0))) }
emit(seamCases.map { readings -> [SeamLine] in
    let reads = readings.map { reading -> Read in
        let words = reading.words.map { Word(text: $0.text, x0: $0.x0, x1: $0.x1) }
        var read = Read(
            text: words.map { $0.text }.joined(separator: " "), confidence: reading.confidence,
            x0: words[0].x0, y0: 100, x1: words[words.count - 1].x1, y1: 120, leftY: 110, rightY: 110,
            lineHeight: 20, words: words)
        (read.col, read.cutLeft, read.cutRight) = (reading.col, reading.cutLeft, reading.cutRight)
        return read
    }
    return stitched(reads, columns: seamColumns).map { SeamLine(text: $0.text, open: $0.open) }
})
"""
PAGE = 4000
COLUMNS = (0, 1152, 2304, 3456)  # where the helper's 1536 px tiles start on a page 4000 px wide
GLYPH = 10
NEAR = 30  # the helper takes an end this close to an inner tile edge for a cut one (1.5 line heights of 20)


@pytest.fixture(scope="session")
def seams(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The packaged helper source with its argument handling replaced by a test main: tile readings in on
    stdin, the lines ``stitched`` makes of them out.  The joining code is the helper's own, compiled as is."""
    if not _have_devtools():
        pytest.skip("needs macOS with developer tools (swiftc) to compile the helper's tile joining")
    folder = tmp_path_factory.mktemp("ocr-seams")
    code, banner, _ = ocr._source().decode("utf-8").partition("// arguments\n")
    assert banner, "the helper source no longer has its '// arguments' section"
    main = SEAM_MAIN.replace("PAGE", str(PAGE)).replace("TILE", str(ocr._TILE_PX))
    (folder / "main.swift").write_text(code + main, encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(tmp_path_factory.mktemp("ocr-seams-home")))
        subprocess.run(
            ["/usr/bin/xcrun", "swiftc", *ocr._build_flags(), "-o", "seams", "main.swift"],
            cwd=folder,
            env=ocr._env(),
            check=True,
            capture_output=True,
            timeout=300,
        )
    return folder / "seams"


def set_line(x: int, text: str) -> list[tuple[int, str]]:
    """``(x, word)`` for each word of ``text``, set from ``x`` in glyphs 10 px wide with one between words."""
    out = []
    for word in text.split():
        out.append((x, word))
        x += GLYPH * (len(word) + 1)
    return out


def tiles_see(line: list[tuple[int, str]], *, cut: str = "") -> list[dict[str, Any]]:
    """What each tile reports for one line, the way the helper's ``readTiles`` hands it to ``stitched``.  A
    glyph a tile edge runs through is dropped, or read as ``cut``."""
    readings = []
    for col, left in enumerate(COLUMNS):
        right = min(left + ocr._TILE_PX, PAGE)
        words = []
        for x, word in line:
            cells = [(x + GLYPH * n, x + GLYPH * (n + 1), glyph) for n, glyph in enumerate(word)]
            seen = [(a, b, g) for a, b, g in cells if left <= a and b <= right]
            if not seen:
                continue
            (start, _, _), (_, end, _) = seen[0], seen[-1]
            before = cut if any(a < left < b for a, b, _ in cells) else ""
            after = cut if any(a < right < b for a, b, _ in cells) else ""
            text = before + "".join(g for _, _, g in seen) + after
            words.append({"text": text, "x0": left if before else start, "x1": right if after else end})
        if words:
            near_left = col > 0 and words[0]["x0"] - left <= NEAR
            near_right = col + 1 < len(COLUMNS) and right - words[-1]["x1"] <= NEAR
            readings.append(
                {"col": col, "confidence": 1.0, "cutLeft": near_left, "cutRight": near_right, "words": words}
            )
    return readings


def test_tile_pieces_are_joined_into_whole_lines_with_every_word_once(seams: Path) -> None:
    """Two tiles share 384 px.  The words at a seam are taken from the tile that holds them whole; a word
    wider than the seam, which each tile cuts, is put together from its two parts; a word inside the seam,
    which both tiles hold whole, is kept once."""
    chain = "-".join(
        ["amber", "birch", "cedar", "delta", "ember", "frost", "grove", "haven", "ivory", "jade"]
    )
    sentence = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
    wide = f"see {chain}-koala for the details"  # the long word is 1040..1680: cut at 1536 and at 1152
    inside = chain[:35]  # set at 1160 it ends at 1510: all of it is in both tiles, near an edge of each
    rope = f"start {chain}-{chain}-{chain} finish"  # 1060..2820: wider than a whole tile, so two seams
    dots = "Contents" + "." * 90 + "12"
    cases: dict[str, tuple[list[dict[str, Any]], str, bool]] = {
        "a sentence": (tiles_see(set_line(900, sentence)), sentence, False),
        "a word each tile cuts": (tiles_see(set_line(1000, wide)), wide, False),
        "with the cut glyphs misread": (tiles_see(set_line(1000, wide), cut="#"), wide, False),
        "a word inside the seam": (tiles_see(set_line(1160, inside)), inside, False),
        # 1147..1537: each tile cuts one glyph of it and misreads it, and the two boxes all but coincide.
        "a word a glyph wider than the seam": (
            tiles_see(set_line(1147, chain[:39]), cut="#"),
            chain[:39],
            False,
        ),
        "a word wider than a tile": (tiles_see(set_line(1000, rope)), rope, False),
        "a row of dots": (tiles_see(set_line(1000, dots)), dots, False),
    }
    # Vision splits the start of the right tile's part off as a word of its own.
    splinter = tiles_see(set_line(1000, wide), cut="#")
    part = splinter[1]["words"][0]
    splinter[1]["words"][:1] = [
        {"text": part["text"][:2], "x0": part["x0"], "x1": part["x0"] + GLYPH},
        {"text": part["text"][2:], "x0": part["x0"] + GLYPH, "x1": part["x1"]},
    ]
    cases["a splinter at the cut"] = (splinter, wide, False)
    # One tile is less sure and misread two letters of what both tiles saw: the other tile's are kept.
    for name, unsure in (("the left tile misreads", 0), ("the right tile misreads", 1)):
        misread = tiles_see(set_line(1000, wide))
        misread[unsure]["confidence"] = 0.5
        word = misread[unsure]["words"][1 - unsure]
        word["text"] = word["text"].replace("delta", "de1ta").replace("frost", "trost")
        cases[name] = (misread, wide, False)
    for name, garbled in (
        ("one misreading", inside.replace("birch", "b1rch")),
        ("no text in common", "x" * 35),
    ):
        both = tiles_see(set_line(1160, inside))
        both[0]["confidence"], both[0]["words"][0]["text"] = 0.5, garbled
        cases[f"a word inside the seam, one reading surer: {name}"] = (both, inside, False)
    # The two parts share no text: both are kept and the line stays a piece, so nothing read is lost.
    unmet = tiles_see(set_line(1000, wide))
    unmet[1]["words"][0]["text"] = "x" * len(unmet[1]["words"][0]["text"])
    head, tail = unmet[0]["words"][1]["text"], unmet[1]["words"][0]["text"]
    cases["two parts that do not meet"] = (unmet, f"see {head} {tail} for the details", True)

    answer = subprocess.run(
        [str(seams)],
        input=json.dumps([readings for readings, _, _ in cases.values()]).encode(),
        capture_output=True,
        check=True,
        timeout=60,
    )
    got = dict(zip(cases, json.loads(answer.stdout), strict=True))
    for name, (_, text, still_open) in cases.items():
        assert got[name] == [{"open": still_open, "text": text}], name
