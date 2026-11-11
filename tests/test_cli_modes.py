"""The CLI's output modes as they are today (cli-conventions C-4, kept under K1; CONTRACTS 16.300.11).

C-4's stream moves are not built, so these pins hold today's layout: `sync`, `status` and `hold --list`
print their whole transcript on stdout and nothing on stderr, the transcript does not depend on the
terminal's width, a long Unicode folder name is printed whole on one line, and no color variable makes
agentsync print an escape byte. A closed pipe and a signal are tests/test_cli_streams.py's."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pytest

from agentsync import cli, loop
from agentsync.config import Config, load_config
from agentsync.ops import doctor

LONG_FOLDER = "Ünïcödé Projektordner — 東京オフィス共有ドキュメント 2026 — Ελληνικά αρχεία και σημειώσεις"
WIDTHS = ("40", "80", "120")
COMMANDS = ("sync", "status", "hold --list")
_RUN_NUMBER = re.compile(r"^run \d+ ", re.MULTILINE)
_AGE = re.compile(r"\b\d+[smhd] ago\b")  # status's "(0s ago, ...)" ticks between the three calls


@pytest.fixture
def unicode_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    """A local source in a folder whose name is longer than 40 columns and not ASCII, synced once and held."""
    src = tmp_path / LONG_FOLDER
    (src / "Überprüfung 審査").mkdir(parents=True)
    (src / "Überprüfung 審査" / "Notizen — 会議メモ.md").write_text(
        "# Notizen\n\nInhalt.\n", encoding="utf-8"
    )
    (src / "readme.txt").write_text("plain text\n", encoding="utf-8")
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(src), "--config", str(cfg)]) == cli.EXIT_OK
    config = load_config(cfg)
    assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    source_id = config.sources[0].id
    held = ["hold", source_id, "--reason", LONG_FOLDER, "--owner", "legal", "--config", str(cfg)]
    assert cli.main(held) == cli.EXIT_OK
    capsys.readouterr()
    return config


def _run(command: str, config: Config, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    rc = cli.main([*command.split(), "--config", str(config.config_path)])
    said = capsys.readouterr()
    return rc, said.out, said.err


@pytest.mark.parametrize("command", COMMANDS)
def test_the_transcript_is_the_same_at_40_80_and_120_columns(
    command: str, unicode_tree: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """R401: agentsync wraps no output to the terminal today, so each stream reads the same at every width."""
    # status prints live free disk space, which moves between the runs (B13 round 38 C1462)
    monkeypatch.setattr(doctor, "_disk_free", lambda _anchor: 8 << 30)
    transcripts = []
    for width in WIDTHS:
        monkeypatch.setenv("COLUMNS", width)
        rc, out, err = _run(command, unicode_tree, capsys)
        assert rc == cli.EXIT_OK, (width, out, err)
        transcripts.append((_AGE.sub("N ago", _RUN_NUMBER.sub("run N ", out)), err))
    for (out, err), width in zip(transcripts[1:], WIDTHS[1:], strict=True):
        assert out == transcripts[0][0], (
            width,
            [*difflib.unified_diff(transcripts[0][0].splitlines(), out.splitlines())],
        )
        assert err == transcripts[0][1], width


def test_sync_prints_its_report_on_stdout_and_nothing_on_stderr(
    unicode_tree: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    rc, out, err = _run("sync", unicode_tree, capsys)
    assert rc == cli.EXIT_OK
    assert err == ""
    lines = out.splitlines()
    assert re.match(r"run \d+ · mode poll · commit ", lines[0]), lines
    assert f"  {unicode_tree.sources[0].id}: full_enumeration · complete · cursor held · unchanged=2" in lines
    assert "converted 0, deferred 0 online-only" in lines
    assert lines[-1].startswith(loop.NEXT_PREFIX), lines


def test_status_prints_the_long_folder_whole_on_one_stdout_line(
    unicode_tree: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    rc, out, err = _run("status", unicode_tree, capsys)
    assert rc == cli.EXIT_OK
    assert err == ""
    source = unicode_tree.sources[0]
    listable = [line for line in out.splitlines() if f"source.{source.id}.listable" in line]
    assert len(listable) == 1 and listable[0].endswith(f"— {source.path} is listable"), listable
    assert f"HOLD: hold {source.id} (state): {LONG_FOLDER}" in out


def test_hold_list_prints_each_hold_and_the_count_on_stdout(
    unicode_tree: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    rc, out, err = _run("hold --list", unicode_tree, capsys)
    assert rc == cli.EXIT_OK
    assert err == ""
    lines = out.splitlines()
    assert len(lines) == 2, lines
    assert lines[0].startswith(
        f"hold {unicode_tree.sources[0].id} (state): {LONG_FOLDER} — set by legal since "
    )
    assert lines[1] == "1 active hold"


@pytest.mark.parametrize(("name", "value"), [("FORCE_COLOR", "1"), ("TERM", "dumb"), ("NO_COLOR", "1")])
def test_no_color_variable_puts_an_escape_byte_in_the_output(
    name: str,
    value: str,
    unicode_tree: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R404: agentsync prints no color today, and FORCE_COLOR does not start it. An error and help count."""
    monkeypatch.setenv(name, value)
    for command in (*COMMANDS, "hold --release"):
        rc, out, err = _run(command, unicode_tree, capsys)
        assert (rc == cli.EXIT_OK) == (command in COMMANDS), (
            command,
            rc,
        )  # a bare --release is a usage error
        assert "\x1b" not in out and "\x1b" not in err, (command, out, err)
    assert cli.main(["sync", "--help"]) == cli.EXIT_OK
    said = capsys.readouterr()
    assert said.out and "\x1b" not in said.out + said.err
