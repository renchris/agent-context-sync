"""The opt-in ``--json`` (cli-conventions B-5, U-B4): one document on stdout for status, curate and sync, an
error as one JSON line on stderr with the ladder's unchanged exit code, and ``--version --json``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentsync import __version__, cli
from agentsync.config import Config, load_config
from test_contracts import CLI_SURFACE

_POSITIONALS = {"add-source": ["src"], "accept-deletions": ["src"], "adopt": ["src"], "purge": ["src"]}


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


def test_status_json_is_one_document(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = str(initialised.config_path)
    human = cli.main(["status", "--config", cfg])
    capsys.readouterr()
    rc = cli.main(["status", "--config", cfg, "--json"])
    out, err = capsys.readouterr()
    assert rc == human
    assert "status" in json.loads(out)
    assert err == ""
    doc = json.loads(out)["status"]
    assert {"checks", "next", "waitingOnYou", "warnings"} <= set(doc)
    assert all(set(c) == {"id", "ok", "severity", "message", "fix"} for c in doc["checks"])
    assert all({"command", "argv", "when"} <= set(step) for step in doc["next"])


def test_a_config_error_is_one_json_line_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "sources.toml"
    cfg.write_text("this is [not toml\n", encoding="utf-8")
    rc = cli.main(["status", "--config", str(cfg), "--json"])
    out, err = capsys.readouterr()
    assert rc == 78
    assert out == ""
    assert len(err.splitlines()) == 1
    assert json.loads(err)["reason"] == "config_invalid"
    assert cli.main(["status", "--config", str(cfg)]) == 78  # the text run's code, and its line
    assert capsys.readouterr().err.startswith("configuration error: ")


@pytest.mark.parametrize("verb", sorted(CLI_SURFACE["visible"]))
def test_every_command_accepts_json(verb: str) -> None:
    args = cli.build_parser().parse_args([verb, *_POSITIONALS.get(verb, []), "--json"])
    assert args.json is True
    assert cli.build_parser().parse_args(["--json", verb, *_POSITIONALS.get(verb, [])]).json is True


def test_version_json_is_one_document(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version", "--json"]) == cli.EXIT_OK
    out, err = capsys.readouterr()
    assert json.loads(out) == {"version": __version__} and len(out.splitlines()) == 1
    assert err == ""
    assert cli.main(["--version", "--json", "--help"]) == cli.EXIT_OK  # --help wins (R086)
    assert capsys.readouterr().out.startswith("usage: agentsync")
    assert cli.main(["--version"]) == cli.EXIT_OK
    assert capsys.readouterr().out == f"agentsync {__version__}\n"


@pytest.mark.parametrize("verb", ["status", "curate", "sync"])
def test_no_ansi_and_clean_streams_in_json(
    verb: str, initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main([verb, "--config", str(initialised.config_path), "--json"])
    out, err = capsys.readouterr()
    assert "\x1b" not in out and "\x1b" not in err
    assert len(out.splitlines()) == 1
    doc = json.loads(out)
    assert rc == cli.EXIT_OK, err
    assert err == ""
    if verb == "sync":
        assert doc["outcome"] in ("committed", "unchanged")
        assert {"id", "mode", "commit", "sources"} <= set(doc["run"])
    if verb == "curate":
        assert isinstance(doc["findings"], list)
