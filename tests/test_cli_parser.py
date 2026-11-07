"""The command line's usage layer (CONTRACTS §16.300.2): prefixes, conflicts first, legacy names, hints."""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentsync import cli, governance
from agentsync.config import Config, load_config
from agentsync.paths import expand

_ALLOWED_HINTS = {
    "<BYTES>",
    "<DOCS_REPO>",
    "<NUMBER>",
    "<PATH>",
    "<SCOPE>",
    "<SELECTOR>",
    "<SOURCE>",
    "<TIME>",
    "<URL>",
}


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


def _synced(config: Config, capsys: pytest.CaptureFixture[str]) -> str:
    cfg = str(config.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    return cfg


def _parsers(parser: argparse.ArgumentParser) -> Iterator[argparse.ArgumentParser]:
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            yield from action.choices.values()


@pytest.mark.parametrize("argv", [["offboard", "--purge"], ["purge", "x", "--pu"], ["hold", "all", "--rel"]])
def test_option_prefixes_are_usage_errors(
    initialised: Config, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert cli.main([*argv, "--config", str(initialised.config_path)]) == cli.EXIT_USAGE
    assert "unrecognized arguments" in capsys.readouterr().err
    if argv[0] == "offboard":
        assert expand(initialised.docs_repo).exists()


def test_full_option_names_still_parse() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["offboard", "--purge-data"]).purge_data is True
    assert parser.parse_args(["-vv", "status"]).verbose == 2


def test_purge_usage_conflict_writes_nothing(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _synced(initialised, capsys)
    db = expand(initialised.state_paths.db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE meta SET value = '0' WHERE key = 'key_schema_version'"
    )  # as an older build leaves it
    conn.commit()
    conn.close()
    before = sorted((p.name, p.stat().st_mtime_ns) for p in db.parent.iterdir())
    assert cli.main(["purge", "id=x", "--forget", "id=y", "--config", cfg]) == cli.EXIT_USAGE
    assert "purge: --forget goes with --queue" in capsys.readouterr().err
    assert not list(db.parent.glob(db.name + ".pre-v*"))
    assert sorted((p.name, p.stat().st_mtime_ns) for p in db.parent.iterdir()) == before


def test_hold_list_with_release_is_usage(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["hold", "all", "--list", "--release", "--owner", "o", "--config", str(initialised.config_path)]
    assert cli.main(argv) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "--list" in err and "--release" in err
    assert governance._state_holds(expand(initialised.state_paths.root)) == []


def test_usage_beats_an_unloadable_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = tmp_path / "sources.toml"
    cfg.write_text("this is [not toml\n", encoding="utf-8")
    assert cli.main(["purge", "--queue", "id=x", "--config", str(cfg)]) == cli.EXIT_USAGE
    assert "purge: pass SELECTOR or --queue, not both" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "replacement"),
    [
        (["checkpoint"], ["sync"]),
        (["install-skill"], ["sync"]),
        (["migrate"], ["status"]),
        (["doctor"], ["status"]),
        (["policy", "show"], ["status"]),
    ],
    ids=["checkpoint", "install-skill", "migrate", "doctor", "policy-show"],
)
def test_hidden_legacy_names_warn_once(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    replacement: list[str],
) -> None:
    """``doctor`` runs without the hint: under it test_status_honours_no_next_hint pins it silent."""
    cfg = _synced(initialised, capsys)
    if argv == ["doctor"]:
        monkeypatch.delenv(cli.NO_NEXT_HINT_ENV, raising=False)
    else:
        monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    expected = cli.main([*replacement, "--config", cfg])
    capsys.readouterr()
    assert cli.main([*argv, "--config", cfg]) == expected
    err = capsys.readouterr().err
    assert err.count("deprecated") + err.count("renamed: run") == 1, err


def test_help_documents_agentsync_config_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "AGENTSYNC_CONFIG" in out
    assert "AGENTSYNC_NO_NEXT_HINT" not in out
    block = out.split("environment:\n", 1)[1].split("\n\n", 1)[0]
    assert "AGENTSYNC_CONFIG" in block
    assert all(len(line) <= 80 for line in ["environment:", *block.splitlines()]), block


def test_keep_days_hint_is_number(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["compact-history", "--help"]) == cli.EXIT_OK
    assert "--keep-days <NUMBER>" in capsys.readouterr().out


def test_visible_hints_come_from_the_allowed_list() -> None:
    seen = set()
    for parser in _parsers(cli.build_parser()):
        for action in parser._actions:
            hidden = action.help == argparse.SUPPRESS
            if hidden or action.metavar is None or isinstance(action, argparse._SubParsersAction):
                continue
            seen.add(action.metavar)
            assert action.metavar in _ALLOWED_HINTS, (parser.prog, action.dest, action.metavar)
    assert seen == _ALLOWED_HINTS


def test_purge_help_names_the_erasure(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["purge", "--help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "permanently delete" in out and "remove items" not in out
