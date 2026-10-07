"""tests/media_kit.py, the fake media helper the recording converter's tests run (spec section 9.3).

The kit is checked against what the converter will rely on: one grid per 2 s tick, frames named by media time,
crops in the profile's own fractions, and a frame the fake OCR helper reads back exactly as the fake media
helper "showed" it.  Every name and every row is made up (Contoso).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from media_kit import (
    FAKE_MEDIA_VERSION,
    GRID_H,
    GRID_W,
    calls,
    fake_media,
    fake_recording,
    grids,
    recording,
    row,
    screen,
)
from test_ocr import fake_engine

TITLE = row("FY27 storage budget.xlsx - Excel", 0.05, 0.10, 0.50, 0.03)
TOTAL = row("Total | 412 | 4,355,000", 0.05, 0.40, 0.40, 0.03)
NEW_TOTAL = row("Total | 412 | 4,425,000", 0.05, 0.40, 0.40, 0.03)
STRIP = row("Dana Okafor", 0.90, 0.20, 0.08, 0.02)
LABEL = row("Luis Fernandez", 0.01, 0.97, 0.08, 0.02, 0.5)


def run(helper: Path, *args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(helper), *map(str, args)], capture_output=True, text=True, check=False, cwd=cwd, timeout=60
    )


def answer(helper: Path, *args: str | Path) -> Any:
    done = run(helper, *args)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    out = tmp_path / "work"
    out.mkdir()
    return out


def test_version_and_info_answer_from_the_recordings_own_script(tmp_path: Path) -> None:
    helper = fake_media(tmp_path / "bin", recording(duration_ms=10_000))
    assert answer(helper, "--version") == json.loads(FAKE_MEDIA_VERSION)
    own = fake_recording(tmp_path / "a.mp4", recording(duration_ms=7_595_000, size=(1280, 720), audio=False))
    info = answer(helper, "info", own)
    assert info == {
        "duration_ms": 7_595_000,
        "width": 1280,
        "height": 720,
        "picture": "h264",
        "audio": False,
        "created": "2026-10-02T14:03:12Z",
    }
    assert own.read_bytes()[4:8] == b"ftyp"
    assert answer(helper, "info", fake_recording(tmp_path / "b.mp4"))["duration_ms"] == 10_000
    assert [c["args"][0] for c in calls(helper)] == ["--version", "info", "info"]


def test_scan_writes_one_grid_per_two_second_tick_and_is_byte_identical_twice(
    tmp_path: Path, work: Path
) -> None:
    helper = fake_media(tmp_path / "bin")
    clip = fake_recording(tmp_path / "a.mp4", recording(screen(0, TITLE), duration_ms=9_999))
    first = answer(helper, "scan", clip, "--out", work, "--step-ms", "2000")
    assert first["ticks"] == [{"index": k, "ms": k * 2000} for k in range(5)]
    assert first["grid"] == [GRID_W, GRID_H] and first["file"] == "grids.bin"
    once = (work / "grids.bin").read_bytes()
    assert len(once) == 5 * 57_600
    answer(helper, "scan", clip, "--out", work)
    assert (work / "grids.bin").read_bytes() == once
    capped = answer(helper, "scan", clip, "--out", work, "--max-ticks", "3")
    assert len(capped["ticks"]) == 3 and len(grids(work / "grids.bin")) == 3


def test_a_changed_row_changes_pixels_and_an_unchanged_screen_does_not(tmp_path: Path, work: Path) -> None:
    helper = fake_media(tmp_path / "bin")
    script = recording(screen(0, TITLE, TOTAL), screen(3, TITLE, NEW_TOTAL), duration_ms=10_000)
    answer(helper, "scan", fake_recording(tmp_path / "a.mp4", script), "--out", work)
    g = grids(work / "grids.bin")
    assert g[0] == g[1] == g[2]
    assert g[3] != g[2] and g[3] == g[4] == g[5]


def test_frames_are_named_by_media_time_and_read_back_by_the_fake_ocr_helper(
    tmp_path: Path, work: Path
) -> None:
    helper = fake_media(tmp_path / "bin")
    script = recording(
        screen(0, TITLE, TOTAL, STRIP, LABEL), screen(1_800, TITLE, NEW_TOTAL), duration_ms=3_700_000
    )
    made = answer(
        helper, "frames", fake_recording(tmp_path / "a.mp4", script), "--out", work, "--ticks", "0,74,1800"
    )
    assert [f["file"] for f in made["frames"]] == ["t000000.jpg", "t000228.jpg", "t010000.jpg"]
    assert [(f["width"], f["height"]) for f in made["frames"]] == [(1920, 1080)] * 3
    paths = [work / f["file"] for f in made["frames"]]
    assert all(p.read_bytes().startswith(b"\xff\xd8\xff\xe0FAKE-OCR:") for p in paths)
    read = fake_engine(tmp_path / "ocr").read(paths, work_dir=work, budget_s=60)
    texts = [[line.text for line in image[0].lines] for image in read]
    assert texts[0] == texts[1] == [TITLE[0], TOTAL[0], STRIP[0], LABEL[0]]
    assert texts[2] == [TITLE[0], NEW_TOTAL[0]]
    assert read[0][0].lines[3].confidence == 0.5


def test_a_crop_keeps_the_content_column_in_its_own_fractions(tmp_path: Path, work: Path) -> None:
    helper = fake_media(tmp_path / "bin")
    clip = fake_recording(tmp_path / "a.mp4", recording(screen(0, TITLE, STRIP, LABEL)))
    made = answer(helper, "frames", clip, "--out", work, "--ticks", "0", "--crop-right", "0.872")
    assert (made["frames"][0]["width"], made["frames"][0]["height"]) == (1674, 1080)
    image = fake_engine(tmp_path / "ocr").read([work / "t000000.jpg"], work_dir=work, budget_s=60)[0][0]
    assert (image.width, image.height) == (1674, 1080)
    assert [line.text for line in image.lines] == [TITLE[0], LABEL[0]]
    assert image.lines[0].x == pytest.approx(0.05 * 1920 / 1674)
    assert image.lines[0].w == pytest.approx(0.50 * 1920 / 1674)
    meet = answer(helper, "frames", clip, "--out", work, "--ticks", "0", "--crop", "0,0.12,0.75,1")
    assert (meet["frames"][0]["width"], meet["frames"][0]["height"]) == (1440, 951)


def test_diff_counts_changed_cells_under_the_mask(tmp_path: Path, work: Path) -> None:
    helper = fake_media(tmp_path / "bin")
    script = recording(
        screen(0, TITLE, LABEL),
        screen(1, TITLE, LABEL, paint=[(0.0, 0.96, 0.10, 1.0, 200)]),
        screen(2, TITLE, NEW_TOTAL),
        duration_ms=6_000,
    )
    answer(helper, "scan", fake_recording(tmp_path / "a.mp4", script), "--out", work)
    teams_mask = ["--include", "0,0,0.872,1", "--exclude", "0,0.96,0.10,1"]
    pairs = answer(helper, "diff", work / "grids.bin", "--pairs", "0:1,0:2,1:1", *teams_mask)["pairs"]
    changed = {(p["a"], p["b"]): p["changed"] for p in pairs}
    assert changed[(0, 1)] == 0, "the label box is masked"
    assert changed[(0, 2)] > 0 and changed[(1, 1)] == 0
    assert pairs[0]["cells"] == 280 * 180 - 32 * 8, "a cell is in a rectangle when any of it is"
    whole = answer(helper, "diff", work / "grids.bin", "--pairs", "0:1")["pairs"][0]
    assert whole["changed"] > 0 and whole["cells"] == GRID_W * GRID_H


def test_pills_light_only_the_label_drawn_as_speaking(tmp_path: Path, work: Path) -> None:
    helper = fake_media(tmp_path / "bin")
    other = row("Mei Tanaka", 0.90, 0.30, 0.08, 0.02)
    script = recording(
        screen(0, STRIP, other, lit=["Dana Okafor"]), screen(2, STRIP, other), duration_ms=6_000
    )
    request = work / "request.json"
    boxes = [STRIP[1:5], other[1:5]]
    request.write_text(json.dumps([{"tick": 0, "boxes": boxes}, {"tick": 2, "boxes": boxes}]))
    pills = answer(helper, "pills", fake_recording(tmp_path / "a.mp4", script), "--request", request)["pills"]
    assert pills == [{"tick": 0, "values": [70, 20]}, {"tick": 2, "values": [20, 20]}]


def test_audio_is_sixteen_khz_mono_and_silent_outside_the_sound_spans(tmp_path: Path, work: Path) -> None:
    helper = fake_media(tmp_path / "bin")
    clip = fake_recording(tmp_path / "a.mp4", recording(duration_ms=2_000, sound=[(500, 1_000)]))
    out = answer(helper, "audio", clip, "--out", work)
    assert out == {"file": "audio.pcm", "sample_rate": 16000, "channels": 1, "samples": 32_000}
    pcm = (work / "audio.pcm").read_bytes()
    assert len(pcm) == 64_000
    assert not any(pcm[: 500 * 32]) and not any(pcm[1_000 * 32 :]) and any(pcm[500 * 32 : 1_000 * 32])
    silent = fake_recording(tmp_path / "b.mp4", recording(audio=False))
    assert run(helper, "audio", silent, "--out", work).returncode == 3


def test_a_picture_that_cannot_be_decoded_fails_the_scan(tmp_path: Path, work: Path) -> None:
    helper = fake_media(tmp_path / "bin")
    vp9 = fake_recording(tmp_path / "a.mp4", recording(picture="vp9"))
    assert answer(helper, "info", vp9)["picture"] == "vp9"
    done = run(helper, "scan", vp9, "--out", work)
    assert done.returncode == 3 and "cannot be decoded" in done.stderr
    assert (
        answer(helper, "info", fake_recording(tmp_path / "b.mp4", recording(picture=None)))["picture"] is None
    )


def test_failures_replies_and_version_are_set_per_helper(tmp_path: Path, work: Path) -> None:
    clip = fake_recording(tmp_path / "a.mp4", recording(duration_ms=4_000))
    helper = fake_media(
        tmp_path / "bin",
        fail={"scan": "the fake media helper was told to fail"},
        reply={"info": "not json"},
        version="garbage",
        version_exit=2,
    )
    failed = run(helper, "scan", clip, "--out", work)
    assert failed.returncode == 3 and "told to fail" in failed.stderr
    assert run(helper, "info", clip).stdout == "not json\n"
    version = run(helper, "--version")
    assert (version.returncode, version.stdout) == (2, "garbage\n")
    plain = fake_media(tmp_path / "other")
    assert run(plain, "frames", clip, "--out", work, "--ticks", "3").returncode == 3
    assert run(plain, "nonsense").returncode == 3
    assert run(plain, "info", tmp_path / "missing.mp4").returncode == 3
    assert calls(plain)[0]["cwd"] == str(Path.cwd())
