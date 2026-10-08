"""convert/media.py, the media helper's build, trust, probe and protocol (spec section 9.3).

The protocol tests run the fake media helper of ``tests/media_kit.py``; the build tests use the stub
xcode-select, xcrun and swiftc of ``tests/test_ocr.py``'s kind.  One test builds the real AVFoundation helper
and runs it on a two-second clip a test main makes; it is skipped where there are no macOS developer tools.
Every name and every row is made up (Contoso).
"""

from __future__ import annotations

import array
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert import cue, media, ocr
from agentsync.convert.media import Audio, MediaEngine, MediaError, Scan
from media_kit import GRID_H, GRID_W, calls, fake_media, fake_recording, grids, recording, row, screen
from test_launcher import _have_devtools
from test_ocr import loose_umask, mode, script, write_fake

CFG = ConvertConfig()
DAY = 24 * 3600
TITLE = row("FY27 storage budget.xlsx - Excel", 0.05, 0.10, 0.50, 0.03)
TOTAL = row("Total | 412 | 4,355,000", 0.05, 0.40, 0.40, 0.03)
NEW_TOTAL = row("Total | 412 | 4,425,000", 0.05, 0.40, 0.40, 0.03)
STRIP = row("Dana Okafor", 0.90, 0.20, 0.08, 0.02)
LABEL = row("Luis Fernandez", 0.01, 0.97, 0.08, 0.02, 0.5)
STUB_VERSION = '{"engine": "stub-media", "helper": "9.9.9"}'
SWIFTC_OK = f"""cp main.swift "$0.source"
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
cat > "$out" <<'EOF'
#!/bin/sh
echo '{STUB_VERSION}'
EOF
"""


@pytest.fixture(autouse=True)
def _media_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about the helper being on: the suite's off switch is set per test where it matters."""
    monkeypatch.delenv("AGENTSYNC_OCR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    out = tmp_path / "work"
    out.mkdir()
    return out


def engine_of(folder: Path, script_: dict[str, Any] | None = None, **kw: Any) -> MediaEngine:
    """An engine around a fake media helper in ``folder`` (see ``fake_media`` for ``kw``)."""
    return MediaEngine(fake_media(folder / "bin", script_, **kw), name="paper-media", helper_version="0.1.0")


def place_fake(cache: Path, **kw: Any) -> Path:
    """A fake media helper where this agentsync's built one goes."""
    helper = ocr._helper_path(cache, media.MEDIA)
    fake_media(helper.parent, **kw).replace(helper)
    return helper


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub developer tools whose ``swiftc`` keeps the source it was given (``swiftc.source``) and writes a
    helper that answers ``--version`` as a media helper.  Each logs its arguments in ``<tool>.calls``."""
    folder = tmp_path / "tools"
    (folder / "Developer").mkdir(parents=True)
    (folder / "Sdk").mkdir()
    script(folder / "xcode-select", f'echo "{folder}/Developer"')
    script(folder / "xcrun", f'case "$1" in --find) echo "{folder}/swiftc" ;; *) echo "{folder}/Sdk" ;; esac')
    script(folder / "swiftc", SWIFTC_OK)
    monkeypatch.setattr(ocr, "_XCODE_SELECT", str(folder / "xcode-select"))
    monkeypatch.setattr(ocr, "_XCRUN", str(folder / "xcrun"))
    return folder


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


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agent-context" / "sources.toml"
    path.parent.mkdir()
    monkeypatch.setenv("AGENTSYNC_CONFIG", str(path))
    return path


def lines_of(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


# ---------------------------------------------------------------------------------------------------------
# build, trust, probe, prune
# ---------------------------------------------------------------------------------------------------------


def test_the_media_helper_is_built_trusted_probed_and_pruned_as_the_ocr_helper_is(
    tools: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    folder = cache / "media"
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    old, recent, foreign = "agentsync-media-0000000000000000", "agentsync-media-1111111111111111", "notes.txt"
    for name, days in ((old, 8), (recent, 6), (foreign, 30)):
        (folder / name).write_text("x")
        (folder / name).chmod(0o755)
        os.utime(folder / name, (time.time() - days * DAY, time.time() - days * DAY))

    with loose_umask():
        helper = media.build(cache)
    assert helper == ocr._helper_path(cache, media.MEDIA) and helper.parent == folder
    assert helper.name.startswith("agentsync-media-") and len(helper.name) == len("agentsync-media-") + 16
    assert (mode(helper), mode(folder), mode(cache)) == (0o700, 0o700, 0o700)
    assert {p.name: mode(p) for p in folder.iterdir()} == {helper.name: 0o700, recent: 0o700, foreign: 0o755}
    assert not (cache / "ocr").exists(), "OCR's folder is OCR's"

    (compile_,) = lines_of(tools / "swiftc.calls")
    arch = os.uname().machine
    assert (
        compile_ == f"-O -swift-version 5 -target {arch}-apple-macos12.0 -sdk {tools}/Sdk -o agentsync-media "
        "main.swift"
    )
    source = resources.files("agentsync.convert").joinpath("media_frames.swift").read_bytes()
    assert (tools / "swiftc.source").read_bytes() == source and b"AVAssetImageGenerator" in source

    assert media.probe(CFG, cache) == ("ready", "stub-media, helper 9.9.9")
    os.utime(helper, (time.time() - 30 * DAY, time.time() - 30 * DAY))
    media.probe(CFG, cache)
    assert helper.stat().st_mtime < time.time() - 29 * DAY, "probe looks: it stamps nothing"
    eng = media.engine(CFG, cache)
    assert eng is not None and eng.helper == helper and eng.identity == "media-stub-media-h9.9.9"
    assert helper.stat().st_mtime > time.time() - DAY, "a helper handed to a cycle is stamped as used"
    assert eng.alive()
    assert media.build(cache) == helper and len(lines_of(tools / "swiftc.calls")) == 1, (
        "a working one is kept"
    )

    helper.chmod(0o722)  # the check is made before every run, not once
    assert media.probe(CFG, cache) == (
        "failed",
        "the media helper or its folder can be written by other users",
    )
    with pytest.raises(MediaError, match=r"^the media helper or its folder can be written by other users$"):
        eng.info(tmp_path / "a.mp4", timeout=5)
    assert not eng.alive()
    helper.write_text("#!/bin/sh\nexit 1\n")
    helper.chmod(0o700)  # the same name, and it no longer runs
    assert media.probe(CFG, cache) == ("failed", "the media helper exited 1: no message")
    assert media.build(cache) == helper and len(lines_of(tools / "swiftc.calls")) == 2
    assert media.probe(CFG, cache)[0] == "ready"


def test_a_media_build_that_fails_leaves_ocr_ready(
    tools: Path, tmp_path: Path, config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    ocr_helper = write_fake(ocr._helper_path(cache))
    home = Path.home()
    script(
        tools / "swiftc",
        f'echo "{cache}/media/.build-x/main.swift:3:8: error: no such module in {home}/X" >&2\nexit 1',
    )
    reason = "swiftc did not build the media helper (exit 1): <path>:3:8: error: no such module in <path>"
    with pytest.raises(MediaError) as error:
        media.build(cache)
    assert str(error.value) == reason
    assert [p.name for p in (cache / "ocr").iterdir()] == [ocr_helper.name], "OCR's folder is left alone"
    assert media.probe(CFG, cache) == ("failed", reason)
    assert ocr.probe(CFG, cache) == ("ready", "paper-vision revision 2, helper 0.3.0")

    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n')
    assert media._main() == 1
    assert capsys.readouterr().out == f"media helper: not built ({reason})\n"
    assert ocr._main() == 0
    assert capsys.readouterr().out == "OCR helper: ready (paper-vision revision 2, helper 0.3.0)\n"


def test_the_module_entry_point_prints_one_media_helper_line(
    tools: Path, tmp_path: Path, config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n[convert]\nrecordings = false\n')
    assert media._main() == 0
    assert capsys.readouterr().out == "media helper: off ([convert] recordings = false)\n"
    assert not (tools / "xcode-select.calls").exists() and not (cache / "media").exists()

    config_file.write_text(f'[agentsync]\ncache_dir = "{cache}"\n')
    assert media._main() == 0
    assert capsys.readouterr().out == "media helper: ready (stub-media, helper 9.9.9)\n"
    script(tools / "xcode-select", "exit 2")
    ocr._helper_path(cache, media.MEDIA).unlink()
    assert media._main() == 1
    assert capsys.readouterr().out == (
        "media helper: not built (no Xcode or Command Line Tools (xcode-select -p names no folder))\n"
    )


def test_probe_and_status_never_compile(
    tmp_path: Path, no_compiler: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    assert media.probe(CFG, cache) == ("not-built", "the media helper is not built")
    assert media.engine(CFG, cache) is None
    assert not cache.exists(), "looking writes nothing"

    helper = place_fake(cache)
    assert media.probe(CFG, cache) == ("ready", "paper-media, helper 0.1.0")
    assert [c["args"] for c in calls(helper)] == [["--version"]]
    for cfg, detail in (
        (replace(CFG, recordings=False), "[convert] recordings = false"),
        (replace(CFG, ocr=False), "[convert] ocr = false"),
    ):
        assert media.probe(cfg, cache) == ("off", detail)
        assert media.engine(cfg, cache) is None
    monkeypatch.setenv("AGENTSYNC_OCR", "0")
    assert media.probe(CFG, cache) == ("off", "AGENTSYNC_OCR=0")
    monkeypatch.delenv("AGENTSYNC_OCR")
    monkeypatch.setattr(sys, "platform", "linux")
    assert media.probe(CFG, cache) == ("off", "on-device OCR needs macOS")
    with pytest.raises(MediaError, match=r"^the media helper needs macOS$"):
        media.build(cache)
    assert len(calls(helper)) == 1, "a switched-off helper is not even asked for its version"

    monkeypatch.setattr(sys, "platform", "darwin")
    helper.unlink()
    ocr._marker(helper).write_text("")
    assert media.probe(CFG, cache) == ("failed", "the last build of the media helper failed")


# ---------------------------------------------------------------------------------------------------------
# the protocol
# ---------------------------------------------------------------------------------------------------------

INFO = {"duration_ms": 4000, "width": 1920, "height": 1080, "picture": "h264", "audio": True, "created": None}
SCAN = {"grid": [320, 180], "step_ms": 2000, "file": "grids.bin", "ticks": [{"index": 0, "ms": 0}]}
FRAMES = {"frames": [{"tick": 0, "ms": 0, "file": "t000000.jpg", "width": 1920, "height": 1080}]}
DIFF = {"pairs": [{"a": 0, "b": 1, "changed": 3, "cells": 57600}]}
PILLS = {"pills": [{"tick": 0, "values": [70]}]}
AUDIO = {"file": "audio.pcm", "sample_rate": 16000, "channels": 1, "samples": 0}
BAD_REPLIES: dict[str, tuple[str, str]] = {
    "info that is not JSON": ("info", "not json"),
    "info that is a list": ("info", "[]"),
    "info without sound": ("info", json.dumps({**INFO, "audio": None})),
    "info with a negative length": ("info", json.dumps({**INFO, "duration_ms": -1})),
    "info with a width that is true": ("info", json.dumps({**INFO, "width": True})),
    "info with a codec holding a path": ("info", json.dumps({**INFO, "picture": "../x y"})),
    "info with a local time": ("info", json.dumps({**INFO, "created": "2026-10-02 14:03"})),
    "scan of another grid": ("scan", json.dumps({**SCAN, "grid": [160, 90]})),
    "scan into another file": ("scan", json.dumps({**SCAN, "file": "../grids.bin"})),
    "scan that skips a tick": ("scan", json.dumps({**SCAN, "ticks": [{"index": 1, "ms": 2000}]})),
    "scan at another step": ("scan", json.dumps({**SCAN, "ticks": [{"index": 0, "ms": 1}]})),
    "scan that wrote no grids": ("scan", json.dumps(SCAN)),
    "frames into another folder": (
        "frames",
        json.dumps({"frames": [{**FRAMES["frames"][0], "file": "../t.jpg"}]}),
    ),
    "frames of another tick": ("frames", json.dumps({"frames": [{**FRAMES["frames"][0], "tick": 1}]})),
    "frames short of one": ("frames", json.dumps({"frames": []})),
    "frames of no width": ("frames", json.dumps({"frames": [{**FRAMES["frames"][0], "width": 0}]})),
    "diff of another pair": ("diff", json.dumps({"pairs": [{**DIFF["pairs"][0], "b": 2}]})),
    "diff past its cells": ("diff", json.dumps({"pairs": [{**DIFF["pairs"][0], "changed": 9, "cells": 8}]})),
    "diff that is not JSON": ("diff", "\xff"),
    "pills of another tick": ("pills", json.dumps({"pills": [{"tick": 1, "values": [70]}]})),
    "pills short of a value": ("pills", json.dumps({"pills": [{"tick": 0, "values": []}]})),
    "pills of a value past 255": ("pills", json.dumps({"pills": [{"tick": 0, "values": [256]}]})),
    "pills of a value below -255": ("pills", json.dumps({"pills": [{"tick": 0, "values": [-256]}]})),
    "pills of a value that is true": ("pills", json.dumps({"pills": [{"tick": 0, "values": [True]}]})),
    "pills of a fraction": ("pills", json.dumps({"pills": [{"tick": 0, "values": [50.5]}]})),
    "pills short of a tick": ("pills", json.dumps({"pills": []})),
    "audio into another file": ("audio", json.dumps({**AUDIO, "file": "../audio.pcm"})),
    "audio at another rate": ("audio", json.dumps({**AUDIO, "sample_rate": 48000})),
    "audio in stereo": ("audio", json.dumps({**AUDIO, "channels": 2})),
    "audio of channels that is true": ("audio", json.dumps({**AUDIO, "channels": True})),
    "audio that wrote no file": ("audio", json.dumps({**AUDIO, "samples": 3})),
    "audio of negative samples": ("audio", json.dumps({**AUDIO, "samples": -1})),
}


def ask(eng: MediaEngine, command: str, clip: Path, work: Path) -> object:
    if command == "info":
        return eng.info(clip, timeout=60)
    if command == "scan":
        return eng.scan(clip, out=work, timeout=60)
    if command == "frames":
        return eng.frames(clip, out=work, ticks=[0], timeout=60)
    if command == "pills":
        return eng.pills(clip, [(0, [(0.1, 0.1, 0.2, 0.05)])], work=work, timeout=60)
    if command == "audio":
        return eng.audio(clip, work, timeout=60)
    return eng.diff(work / "grids.bin", pairs=[(0, 1)], timeout=60)


@pytest.mark.parametrize("answer", BAD_REPLIES.values(), ids=BAD_REPLIES.keys())
def test_an_answer_that_is_not_the_expected_json_is_a_media_error_without_a_path(
    tmp_path: Path, work: Path, answer: tuple[str, str]
) -> None:
    command, text = answer
    eng = engine_of(tmp_path, reply={command: text})
    with pytest.raises(MediaError) as error:
        ask(eng, command, fake_recording(tmp_path / "a.mp4"), work)
    assert str(error.value) == "the media helper's answer is not the expected JSON"


def test_a_helper_failure_or_time_out_is_a_media_error_without_a_path(
    tmp_path: Path, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = fake_recording(tmp_path / "Contoso board.mp4")
    eng = engine_of(tmp_path, fail={"info": f"cannot open '{clip}'"})
    with pytest.raises(MediaError) as error:
        eng.info(clip, timeout=60)
    assert str(error.value) == "the media helper exited 3: error: cannot open '<path>'"
    assert isinstance(error.value, ocr.OcrError) and str(tmp_path) not in str(error.value)
    with pytest.raises(MediaError, match="past the end"):
        engine_of(tmp_path / "plain").scan(clip, out=work, timeout=60, first_tick=31)

    versions = ("garbage", '{"engine": "../x y", "helper": "1"}', '{"engine": "x", "helper": 1}')
    for n, version in enumerate(versions):
        place_fake(tmp_path / f"cache{n}", version=version)
        assert media.probe(CFG, tmp_path / f"cache{n}") == (
            "failed",
            "the media helper's --version answer is not the expected JSON",
        )

    waits: list[float] = []

    def hang(argv: list[str], **kw: Any) -> Any:
        waits.append(kw["timeout"])
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(MediaError, match=r"^the media helper ran out of time$"):
        eng.scan(clip, out=work, timeout=42)
    assert not eng.alive()
    assert waits == [42, 5.0], "alive() gives --version 5 s"


def test_scan_writes_one_grid_per_two_second_tick_into_the_work_dir(tmp_path: Path, work: Path) -> None:
    eng = engine_of(tmp_path)
    script_ = recording(screen(0, TITLE), screen(3, TITLE, TOTAL), duration_ms=10_000)
    clip = fake_recording(tmp_path / "a.mp4", script_)
    scan = eng.scan(clip, out=work, timeout=60)
    assert scan == Scan(grids=work / "grids.bin", step_ms=2000, first=0, ticks=6)
    whole = grids(scan.grids)
    assert len(whole) == 6 and all(len(g) == GRID_W * GRID_H for g in whole)
    assert whole[0] == whole[2] != whole[3]

    piece = eng.scan(clip, out=work, timeout=60, first_tick=3, max_ticks=2)
    assert piece == Scan(grids=work / "grids.bin", step_ms=2000, first=3, ticks=2)
    assert grids(piece.grids) == whole[3:5], "the grid file holds the piece's ticks only"
    first, second = calls(eng.helper)
    assert first["args"] == ["scan", str(clip), "--out", str(work), "--step-ms", "2000"]
    assert second["args"][-4:] == ["--first-tick", "3", "--max-ticks", "2"]
    assert {Path(c["cwd"]).resolve() for c in (first, second)} == {work.resolve()}
    assert eng.scan(clip, out=work, timeout=60, max_ticks=1).ticks == 1


def test_frames_are_named_by_media_time_and_cropped_only_when_asked(tmp_path: Path, work: Path) -> None:
    eng = engine_of(tmp_path)
    script_ = recording(screen(0, TITLE, STRIP, LABEL), screen(1_800, NEW_TOTAL), duration_ms=3_700_000)
    clip = fake_recording(tmp_path / "a.mp4", script_)
    made = eng.frames(clip, out=work, ticks=[1800, 0, 74], timeout=60)
    assert [(f.tick, f.ms, f.path, f.width, f.height) for f in made] == [
        (1800, 3_600_000, work / "t010000.jpg", 1920, 1080),
        (0, 0, work / "t000000.jpg", 1920, 1080),
        (74, 148_000, work / "t000228.jpg", 1920, 1080),
    ]
    assert all(f.path.read_bytes().startswith(b"\xff\xd8\xff\xe0") for f in made)
    assert not {"--crop", "--crop-right"} & set(calls(eng.helper)[0]["args"])

    (teams,) = eng.frames(clip, out=work, ticks=[0], timeout=60, crop_right=0.872)
    assert (teams.width, teams.height) == (1674, 1080)
    assert calls(eng.helper)[-1]["args"][-4:-2] == ["--crop-right", "0.872"]
    (meet,) = eng.frames(clip, out=work, ticks=[0], timeout=60, crop=(0, 0.12, 0.75, 1))
    assert (meet.width, meet.height) == (1440, 951)
    assert eng.frames(clip, out=work, ticks=[], timeout=60) == [] and len(calls(eng.helper)) == 3
    with pytest.raises(MediaError):
        eng.frames(clip, out=work, ticks=[1851], timeout=60)


def test_diff_counts_changed_cells_under_the_mask(tmp_path: Path, work: Path) -> None:
    eng = engine_of(tmp_path)
    script_ = recording(
        screen(0, TITLE, LABEL),
        screen(1, TITLE, LABEL, paint=[(0.0, 0.96, 0.10, 1.0, 200)]),
        screen(2, TITLE, NEW_TOTAL),
        duration_ms=6_000,
    )
    scan = eng.scan(fake_recording(tmp_path / "a.mp4", script_), out=work, timeout=60)
    changed = eng.diff(
        scan.grids,
        pairs=[(0, 1), (0, 2), (1, 1)],
        timeout=60,
        include=[(0.0, 0.0, 0.872, 1.0)],
        exclude=[(0.0, 0.96, 0.10, 1.0)],
    )
    assert changed[0] == 0, "the label box is masked"
    assert changed[1] > 0 and changed[2] == 0
    assert eng.diff(scan.grids, pairs=[(0, 1)], timeout=60) != [0], "unmasked, the label box counts"
    assert eng.diff(scan.grids, pairs=[(0, 2)], timeout=60, threshold=255) == [0]
    assert calls(eng.helper)[-1]["args"][-2:] == ["--threshold", "255"]
    assert eng.diff(scan.grids, pairs=[], timeout=60) == []
    with pytest.raises(MediaError):
        eng.diff(scan.grids, pairs=[(0, 4)], timeout=60)


def test_pills_answers_one_value_per_box_in_request_order(tmp_path: Path, work: Path) -> None:
    eng = engine_of(tmp_path)
    script_ = recording(
        screen(0, TITLE, STRIP, LABEL, lit=["Dana Okafor"]),
        screen(2, TITLE, STRIP, LABEL, lit=["Luis Fernandez"]),
        duration_ms=6_000,
    )
    clip = fake_recording(tmp_path / "a.mp4", script_)
    strip, label = (tuple(r[1:5]) for r in (STRIP, LABEL))
    request = [(2, [strip, label]), (0, [label, strip]), (1, [])]
    assert eng.pills(clip, request, work=work, timeout=60) == {2: (20, 70), 0: (20, 70), 1: ()}
    (call,) = calls(eng.helper)
    asked = work / "pills.json"
    assert call["args"] == ["pills", str(clip), "--request", str(asked), "--step-ms", "2000"]
    assert Path(call["cwd"]).resolve() == work.resolve()
    assert json.loads(asked.read_text()) == [
        {"tick": 2, "boxes": [list(strip), list(label)]},
        {"tick": 0, "boxes": [list(label), list(strip)]},
        {"tick": 1, "boxes": []},
    ]
    assert eng.pills(clip, [], work=work, timeout=60) == {} and len(calls(eng.helper)) == 1
    with pytest.raises(ValueError, match="twice"):
        eng.pills(clip, [(0, [strip]), (0, [label])], work=work, timeout=60)
    with pytest.raises(MediaError):
        eng.pills(clip, [(4, [strip])], work=work, timeout=60)


def test_audio_writes_sixteen_kilohertz_mono_pcm_into_the_work_dir(tmp_path: Path, work: Path) -> None:
    eng = engine_of(tmp_path)
    clip = fake_recording(tmp_path / "a.mp4", recording(duration_ms=3_000, sound=[(1_000, 2_000)]))
    sound = eng.audio(clip, work, timeout=60)
    assert sound == Audio(path=work / "audio.pcm", samples=48_000)
    pcm = sound.path.read_bytes()
    assert len(pcm) == 96_000 and not any(pcm[:32_000]) and any(pcm[32_000:64_000]) and not any(pcm[64_000:])
    (call,) = calls(eng.helper)
    assert call["args"] == ["audio", str(clip), "--out", str(work)]
    assert Path(call["cwd"]).resolve() == work.resolve()

    silent = fake_recording(tmp_path / "Contoso standup.mp4", recording(audio=False))
    with pytest.raises(MediaError, match=r"^the media helper exited 3: error: the recording has no sound$"):
        eng.audio(silent, work, timeout=60)
    with pytest.raises(MediaError) as error:
        engine_of(tmp_path / "failing", fail={"audio": f"cannot open '{silent}'"}).audio(
            silent, work, timeout=60
        )
    assert str(error.value) == "the media helper exited 3: error: cannot open '<path>'"


# ---------------------------------------------------------------------------------------------------------
# the real helper
# ---------------------------------------------------------------------------------------------------------

CLIP_MAIN = r"""
import AVFoundation
import CoreVideo

// Two seconds at 16 frames a second, 320x240: the top half light, the bottom half dark, and from the second
// second on a light box in the bottom-left quarter.
let w = 320, h = 240
let writer = try! AVAssetWriter(outputURL: URL(fileURLWithPath: CommandLine.arguments[1]), fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h,
])
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil)
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)
for i in 0..<32 {
    while !input.isReadyForMoreMediaData { usleep(1000) }
    var made: CVPixelBuffer?
    CVPixelBufferCreate(nil, w, h, kCVPixelFormatType_32BGRA, nil, &made)
    let buffer = made!
    CVPixelBufferLockBaseAddress(buffer, [])
    let base = CVPixelBufferGetBaseAddress(buffer)!.assumingMemoryBound(to: UInt8.self)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    for y in 0..<h {
        for x in 0..<w {
            let light = y < h / 2 || (i >= 16 && y >= h * 3 / 4 && x < w / 4)
            let p = base + y * stride + x * 4
            p[0] = light ? 235 : 16; p[1] = p[0]; p[2] = p[0]; p[3] = 255
        }
    }
    CVPixelBufferUnlockBaseAddress(buffer, [])
    adaptor.append(buffer, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: 16))
}
input.markAsFinished()
writer.endSession(atSourceTime: CMTime(value: 2, timescale: 1))
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
exit(writer.status == .completed ? 0 : 1)
"""


SOUND_CLIP_MAIN = r"""
import AVFoundation
import CoreVideo

// Two seconds at 16 frames a second, 320x240 on a dark grey ground: a blue box at x 40 to 119, y 180 to 209
// and a light grey one at x 200 to 279 on the same rows; and a 440 Hz tone, 48 kHz stereo, all the way.
let w = 320, h = 240, rate = 48_000, chunk = 3_000  // one 16th of a second of sound per frame
let writer = try! AVAssetWriter(outputURL: URL(fileURLWithPath: CommandLine.arguments[1]), fileType: .mp4)
let picture = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h,
])
let sound = AVAssetWriterInput(mediaType: .audio, outputSettings: [
    AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: rate, AVNumberOfChannelsKey: 2,
    AVEncoderBitRateKey: 128_000,
])
let adaptor = AVAssetWriterInputPixelBufferAdaptor(
    assetWriterInput: picture, sourcePixelBufferAttributes: nil)
writer.add(picture)
writer.add(sound)
var asbd = AudioStreamBasicDescription(
    mSampleRate: Float64(rate), mFormatID: kAudioFormatLinearPCM,
    mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 4,
    mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2, mBitsPerChannel: 16, mReserved: 0)
var format: CMAudioFormatDescription?
CMAudioFormatDescriptionCreate(
    allocator: nil, asbd: &asbd, layoutSize: 0, layout: nil, magicCookieSize: 0, magicCookie: nil,
    extensions: nil, formatDescriptionOut: &format)
let blueRGB: (UInt8, UInt8, UInt8) = (60, 70, 200), greyRGB: (UInt8, UInt8, UInt8) = (150, 150, 150)
let groundRGB: (UInt8, UInt8, UInt8) = (40, 40, 40)
writer.startWriting()
writer.startSession(atSourceTime: .zero)
for i in 0..<32 {
    while !picture.isReadyForMoreMediaData { usleep(1000) }
    var made: CVPixelBuffer?
    CVPixelBufferCreate(nil, w, h, kCVPixelFormatType_32BGRA, nil, &made)
    let buffer = made!
    CVPixelBufferLockBaseAddress(buffer, [])
    let base = CVPixelBufferGetBaseAddress(buffer)!.assumingMemoryBound(to: UInt8.self)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    for y in 0..<h {
        for x in 0..<w {
            let row = y >= 180 && y < 210
            let blue = row && x >= 40 && x < 120, grey = row && x >= 200 && x < 280
            let (r, g, b) = blue ? blueRGB : grey ? greyRGB : groundRGB
            let p = base + y * stride + x * 4
            p[0] = b; p[1] = g; p[2] = r; p[3] = 255
        }
    }
    CVPixelBufferUnlockBaseAddress(buffer, [])
    adaptor.append(buffer, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: 16))

    while !sound.isReadyForMoreMediaData { usleep(1000) }
    var samples = [Int16](repeating: 0, count: chunk * 2)
    for n in 0..<chunk {
        let v = Int16(8000 * sin(2 * Double.pi * 440 * Double(i * chunk + n) / Double(rate)))
        samples[2 * n] = v; samples[2 * n + 1] = v
    }
    var block: CMBlockBuffer?
    CMBlockBufferCreateWithMemoryBlock(
        allocator: nil, memoryBlock: nil, blockLength: chunk * 4, blockAllocator: nil, customBlockSource: nil,
        offsetToData: 0, dataLength: chunk * 4, flags: 0, blockBufferOut: &block)
    samples.withUnsafeBytes {
        _ = CMBlockBufferReplaceDataBytes(
            with: $0.baseAddress!, blockBuffer: block!, offsetIntoDestination: 0, dataLength: chunk * 4)
    }
    var sample: CMSampleBuffer?
    CMAudioSampleBufferCreateReadyWithPacketDescriptions(
        allocator: nil, dataBuffer: block!, formatDescription: format!, sampleCount: chunk,
        presentationTimeStamp: CMTime(value: CMTimeValue(i * chunk), timescale: CMTimeScale(rate)),
        packetDescriptions: nil, sampleBufferOut: &sample)
    sound.append(sample!)
}
picture.markAsFinished()
sound.markAsFinished()
writer.endSession(atSourceTime: CMTime(value: 2, timescale: 1))
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
exit(writer.status == .completed ? 0 : 1)
"""


def made_clip(tmp_path: Path, main: str) -> Path:
    """``tmp_path/clip.mp4`` as the Swift program ``main`` writes it; skips without developer tools."""
    if not _have_devtools():
        pytest.skip("needs macOS with developer tools (swiftc) to build the media helper")
    maker = tmp_path / "maker"
    maker.mkdir()
    (maker / "main.swift").write_text(main, encoding="utf-8")
    subprocess.run(
        ["/usr/bin/xcrun", "swiftc", *ocr._build_flags(), "-o", "clip", "main.swift"],
        cwd=maker,
        env=ocr._env(),
        check=True,
        capture_output=True,
        timeout=300,
    )
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [str(maker / "clip"), str(clip)], env=ocr._env(), check=True, capture_output=True, timeout=120
    )
    assert clip.read_bytes()[4:8] == b"ftyp"
    return clip


def test_the_real_media_helper_reads_a_two_second_clip_it_made(tmp_path: Path) -> None:
    clip = made_clip(tmp_path, CLIP_MAIN)
    eng = media._open(media.build(tmp_path / "cache"))
    assert (eng.name, eng.helper_version) == ("avfoundation", "1.1.0")
    info = eng.info(clip, timeout=60)
    assert (info.width, info.height, info.picture, info.audio) == (320, 240, "h264", False)
    assert info.duration_ms == 2_000
    ticks = 2  # tick 1 is the end of the clip: the last frame is on display

    runs = []
    for n in range(2):
        out = tmp_path / f"run{n}"
        out.mkdir()
        scan = eng.scan(clip, out=out, timeout=120)
        made = eng.frames(clip, out=out, ticks=[ticks - 1, 0], timeout=120, crop_right=0.5)
        runs.append([scan.grids.read_bytes(), *(f.path.read_bytes() for f in made)])
    assert runs[0] == runs[1], "the same file gives the same bytes"
    assert scan.ticks == ticks and [(f.tick, f.width, f.height) for f in made] == [
        (ticks - 1, 160, 240),
        (0, 160, 240),
    ]
    jpeg = made[0].path.read_bytes()
    assert jpeg.startswith(b"\xff\xd8\xff\xe0") and b"Exif" not in jpeg and b"Photoshop" not in jpeg

    first = grids(scan.grids)[0]
    assert first[0] > 200 and first[(GRID_H - 1) * GRID_W + GRID_W - 1] < 50, (
        "row 0 is the top of the picture"
    )
    box = (0.0, 0.7, 0.3, 1.0)  # the last tick shows the box
    changed, masked = (eng.diff(scan.grids, pairs=[(0, 1)], timeout=60, exclude=e) for e in ((), (box,)))
    assert changed[0] > 2_000 and masked[0] < 100


def test_the_real_media_helper_reads_the_pills_and_the_sound_of_a_clip_it_made(tmp_path: Path) -> None:
    clip = made_clip(tmp_path, SOUND_CLIP_MAIN)
    eng = media._open(media.build(tmp_path / "cache"))
    assert eng.info(clip, timeout=60).audio
    blue, grey, ground = (0.125, 0.75, 0.25, 0.125), (0.625, 0.75, 0.25, 0.125), (0.0, 0.0, 0.1, 0.1)

    runs = []
    for n in range(2):
        out = tmp_path / f"run{n}"
        out.mkdir()
        values = eng.pills(clip, [(1, [blue, grey, ground]), (0, [grey, blue])], work=out, timeout=120)
        sound = eng.audio(clip, out, timeout=120)
        runs.append((values, sound.samples, sound.path.read_bytes()))
    assert runs[0][:2] == runs[1][:2], "the same file gives the same pill values and the same length"
    # Apple's AAC decoder may move a sample by one step between runs (spec R11; CONTRACTS §16.35): the sound
    # is the same to within that, and audio.pcm is never a cache key.
    once, again = (array.array("h", run[2]) for run in runs)
    assert max(abs(a - b) for a, b in zip(once, again, strict=True)) <= 1

    values, samples, pcm = runs[0]
    assert values[1][0] >= cue.LIT_AT > values[1][1] and values[1][1:] == (0, 0), values
    assert values[0] == values[1][1::-1], "the frame of each tick, in box order"
    assert samples == 32_000 and len(pcm) == 64_000, "2 s at 16 kHz, read from the start"
    tone = array.array("h", pcm[16_000:48_000])  # the middle second, 16-bit little-endian
    rms = math.sqrt(sum(v * v for v in tone) / len(tone))
    assert 5_300 < rms < 6_000, f"the 8,000-amplitude tone, averaged over both channels: rms {rms:.0f}"
