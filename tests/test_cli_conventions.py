"""The CLI's copy and conventions (cli-conventions B-4.2 to B-4.6): a refusal names the runnable fix, a
result on stdout parses, a remark goes to stderr, remote text cannot drive the terminal, and init writes
sources.toml whole or not at all."""

from __future__ import annotations

import errno
import os
import shlex
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentsync import cli, governance, loop
from agentsync.config import Config, load_config
from agentsync.errors import GraphError
from agentsync.graph import auth as graph_auth
from agentsync.graph import discover
from agentsync.graph.drive import DiscoveredScope
from agentsync.model import SourceKind
from agentsync.paths import expand
from test_cli import FakeAuth, _graph_config, _synced


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


@pytest.fixture
def graph_cfg(initialised: Config, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(graph_auth, "MsalAuth", FakeAuth)
    FakeAuth.calls = []
    FakeAuth.blocked = False
    return _graph_config(initialised)


def _discover_finds(monkeypatch: pytest.MonkeyPatch, *scopes: DiscoveredScope, failed: bool = False) -> None:
    denied = discover.DiscoveryFailure("/me/joinedTeams", 403, "Forbidden", "ask IT for Team.ReadBasic.All")

    def fake_discover(client: Any, *, known: Any = ()) -> discover.DiscoveryReport:
        return discover.DiscoveryReport(scopes, (denied,) if failed else ())

    monkeypatch.setattr(discover, "discover_sources", fake_discover)


def _no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, "", "")  # the real Keychain is untouched

    monkeypatch.setattr(governance, "_run", fake_run)


def test_offboard_wrong_confirm_prints_a_runnable_command(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_keychain(monkeypatch)
    cfg = _synced(initialised)
    docs = expand(initialised.docs_repo)
    capsys.readouterr()
    argv = ["offboard", "--confirm", str(docs) + "-not", "--purge-data", "--config", cfg]
    assert cli.main(argv) == cli.EXIT_FAILED
    err = capsys.readouterr().err
    assert "confirm=" not in err, err
    assert f"    {loop.BIN} offboard --confirm {shlex.quote(str(docs))} --purge-data" in err.splitlines(), err
    assert docs.is_dir() and initialised.state_paths.root.is_dir()


def test_offboard_table_has_a_header_off_a_tty(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_keychain(monkeypatch)
    cfg = _synced(initialised)
    capsys.readouterr()
    assert cli.main(["offboard", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "kind\tstate\tlocation\taction", out
    assert "state-dir\texists" in out


def test_discover_stdout_is_valid_toml(
    graph_cfg: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scope = DiscoveredScope(SourceKind.GRAPH_DRIVE, "OneDrive", "b!abc", None, None, "your OneDrive")
    _discover_finds(monkeypatch, scope, failed=True)
    assert cli.main(["discover", "--config", graph_cfg]) == cli.EXIT_FAILED
    out, err = capsys.readouterr()
    tomllib.loads(out)
    assert "kind\tname" in err and "OneDrive\tnew\tdrive_id=b!abc" in err, err


def test_discover_strips_terminal_controls_from_remote_names(
    graph_cfg: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "Q3\x1b]0;x\x07\N{RIGHT-TO-LEFT OVERRIDE}plan\N{ZERO WIDTH SPACE}\tB"
    scope = DiscoveredScope(SourceKind.GRAPH_DRIVE, name, "b!abc", None, None, "your OneDrive")
    _discover_finds(monkeypatch, scope)
    assert cli.main(["discover", "--config", graph_cfg]) == cli.EXIT_OK
    out, err = capsys.readouterr()
    for control in ("\x1b", "\x07", "\N{RIGHT-TO-LEFT OVERRIDE}", "\N{ZERO WIDTH SPACE}"):
        assert control not in out + err, repr(out + err)
    assert "\tQ3plan\\tB\t" in err, repr(err)


def test_a_graph_error_message_is_sanitized(
    graph_cfg: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(client: Any, *, known: Any = ()) -> discover.DiscoveryReport:
        raise GraphError(400, "badRequest", "no such site\x1b[2J")

    monkeypatch.setattr(discover, "discover_sources", refused)
    assert cli.main(["discover", "--config", graph_cfg]) == cli.EXIT_FAILED
    err = capsys.readouterr().err
    assert "\x1b" not in err and "error: Graph 400 badRequest: no such site" in err, repr(err)


def test_an_empty_hold_list_is_stderr_only(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["hold", "--list", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    out, err = capsys.readouterr()
    assert out == ""
    assert "No active holds." in err


def test_init_leaves_no_partial_sources_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "ctx" / "sources.toml"

    def refuse(src: Any, dst: Any) -> None:
        raise OSError(errno.EIO, "the disk said no")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="the disk said no"):
        cli.main(["init", "--config", str(cfg)])
    assert not cfg.exists()
    assert not list(cfg.parent.glob(".sources.toml*"))


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        (["purge", "--queue", "--source", "local-fixture"], ("--queue", "--source")),
        (["purge", "--queue", "--push"], ("--queue", "--push")),
        (["hold", "all", "--release", "--owner", "legal", "--reason", "r"], ("--release", "--reason")),
    ],
)
def test_a_contradictory_command_line_is_refused_and_writes_nothing(
    argv: list[str], named: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "ctx"
    assert cli.main([*argv, "--config", str(home / "sources.toml")]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert all(option in err for option in named), err
    assert not home.exists()
