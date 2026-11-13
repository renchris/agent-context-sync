"""The opt-in ``--json`` (cli-conventions B-5): one document on stdout for status, curate and sync, an error
as one JSON line on stderr with the ladder's unchanged exit code, and ``--version --json`` (U-B4); a document
for every other command, with the inspect command as ``next`` after a mutation (U-B5)."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentsync import __version__, cli, governance
from agentsync.config import Config, load_config
from agentsync.graph import auth as graph_auth
from agentsync.graph import discover
from agentsync.graph.drive import DiscoveredScope
from agentsync.manifest import Manifest
from agentsync.model import SourceKind
from test_cli import FakeAuth, _graph_config, _hold_and_queue_dlp
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


def _human(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> int:
    rc = cli.main(list(argv))
    capsys.readouterr()
    return rc


def _doc(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str]:
    """The run's code, its one document and its stderr."""
    rc = cli.main([*argv, "--json"])
    out, err = capsys.readouterr()
    assert len(out.splitlines()) == 1, (out, err)
    return rc, json.loads(out), err


def _error(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    """The run's code and its one error line: nothing on stdout."""
    rc = cli.main([*argv, "--json"])
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1, (out, err)
    return rc, json.loads(err)


def _synced(initialised: Config, capsys: pytest.CaptureFixture[str]) -> str:
    cfg = str(initialised.config_path)
    assert _human(["sync", "--config", cfg], capsys) == cli.EXIT_OK
    return cfg


def test_hold_json_shape(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["hold", "--config", str(initialised.config_path)]
    assert _human([*base, "--list"], capsys) == cli.EXIT_OK
    assert _doc([*base, "--list"], capsys) == (cli.EXIT_OK, {"holds": [], "pagination": {"next": None}}, "")
    setting = [*base, "all", "--reason", "litigation", "--owner", "Contoso Legal"]
    assert _human(setting, capsys) == cli.EXIT_OK
    rc, doc, err = _doc(setting, capsys)
    assert rc == cli.EXIT_OK and err == ""
    assert (
        doc["outcome"] == "created"
        and doc["hold"]["scope"] == "all"
        and doc["hold"]["owner"] == "Contoso Legal"
    )
    assert "hold --list --config " in doc["next"][0]["command"] and doc["next"][0]["argv"][1:3] == [
        "hold",
        "--list",
    ]
    rc, doc, err = _doc([*base, "--list"], capsys)
    assert [h["scope"] for h in doc["holds"]] == ["all"] and doc["pagination"] == {"next": None}
    release = [*base, "all", "--release", "--owner", "Contoso Legal"]
    rc, doc, err = _doc(release, capsys)
    assert (rc, doc["outcome"], err) == (cli.EXIT_OK, "released", "")
    assert _human(release, capsys) == cli.EXIT_FAILED  # nothing left to release
    assert _error(release, capsys)[0] == cli.EXIT_FAILED
    rc, error = _error([*base, "--list", "all"], capsys)  # a handler's own usage refusal
    assert (rc, error["reason"]) == (cli.EXIT_USAGE, "usage")


def test_purge_json_shape(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _synced(initialised, capsys)
    with Manifest(initialised.state_paths.db) as m:
        sid = next(r.stable_id for r in m.iter_items("source") if r.rel_path == "projects/sample.txt")
    dry = ["purge", f"id={sid}", "--source", "source", "--dry-run", "--config", cfg]
    human = _human(dry, capsys)
    rc, doc, err = _doc(dry, capsys)
    assert rc == human == cli.EXIT_OK and err == ""
    assert doc["outcome"] == "planned" and doc["purge"]["dryRun"] is True
    assert doc["purge"]["docsPaths"] == ["mirror/source/projects/sample.txt.md"]
    rc, doc, err = _doc([*dry[:-3], "--reason", "erasure-request", "--config", cfg], capsys)  # a tmp repo
    assert (rc, doc["outcome"], doc["purge"]["verified"], err) == (cli.EXIT_OK, "purged", True, "")
    assert "status" in doc["next"][0]["argv"]
    rc, error = _error(["purge", "--config", cfg], capsys)
    assert (rc, error["reason"]) == (cli.EXIT_USAGE, "usage")


def test_purge_queue_json_shape(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _synced(initialised, capsys)
    queue = ["purge", "--queue", "--dry-run", "--config", cfg]
    empty = {"succeeded": 0, "failed": 0, "skipped": 0, "unknown": 0}
    assert _human(queue, capsys) == cli.EXIT_OK
    rc, doc, err = _doc(queue, capsys)
    assert (rc, doc["results"], doc["summary"], err) == (cli.EXIT_OK, [], empty, "")
    with Manifest(initialised.state_paths.db) as m:
        sid = next(r.stable_id for r in m.iter_items("source") if r.rel_path == "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    assert governance.enqueue_purge(initialised.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    rc, doc, err = _doc(queue, capsys)
    assert rc == cli.EXIT_OK and err == ""
    assert [r["outcome"] for r in doc["results"]] == ["planned"] and doc["summary"]["succeeded"] == 1
    assert doc["queued"] == 1 and "error" not in doc["results"][0]


def test_compact_history_json_shape(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _synced(initialised, capsys)
    argv = ["compact-history", "--dry-run", "--config", cfg]
    human = _human(argv, capsys)
    rc, doc, err = _doc(argv, capsys)
    assert rc == human == cli.EXIT_OK and err == ""
    assert doc["outcome"] == "planned" and {"cutoff", "squashed", "kept", "verified"} <= set(
        doc["compaction"]
    )


def test_a_guard_stop_is_one_json_line(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    """``_lost_state_stop``: a state folder that is a file stops compact-history before it reads anything."""
    cfg = str(initialised.config_path)
    root = initialised.state_paths.root
    shutil.rmtree(root)
    root.write_text("", encoding="utf-8")
    human = _human(["compact-history", "--config", cfg], capsys)
    rc, error = _error(["compact-history", "--config", cfg], capsys)
    assert rc == human == cli.EXIT_FAILED
    assert error["reason"] == "state_misplaced" and "status" in error["next"][0]["argv"]


def test_offboard_json_shape(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, "", "")  # the real Keychain is untouched

    monkeypatch.setattr(governance, "_run", fake_run)
    cfg = _synced(initialised, capsys)
    human = _human(["offboard", "--config", cfg], capsys)
    rc, doc, err = _doc(["offboard", "--config", cfg], capsys)  # no --confirm: a plan, nothing removed
    assert rc == human == cli.EXIT_OK and err == ""
    assert doc["outcome"] == "planned" and doc["removed"] == [] and doc["errors"] == []
    assert {"kind", "exists", "location", "action"} == set(doc["locations"][0])
    wrong = ["offboard", "--confirm", "/nope", "--config", cfg]
    assert _human(wrong, capsys) == cli.EXIT_FAILED
    rc, error = _error(wrong, capsys)
    assert (rc, error["reason"], error["status"]) == (
        cli.EXIT_FAILED,
        "confirmation_required",
        "action_required",
    )
    assert error["next"][0]["argv"][1:3] == ["offboard", "--confirm"]


def test_add_source_json_shape(
    initialised: Config, tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    again = ["add-source", str(local_source_dir), "--config", cfg]
    human = _human(again, capsys)
    rc, doc, err = _doc(again, capsys)
    assert rc == human == cli.EXIT_OK and err == ""
    assert doc["outcome"] == "unchanged" and doc["source"]["id"] == "source"
    other = tmp_path / "notes"
    other.mkdir()
    rc, doc, err = _doc(["add-source", str(other), "--config", cfg], capsys)
    assert (rc, doc["outcome"], err) == (cli.EXIT_OK, "created", "")
    assert set(doc["source"]) == {"id", "path"} and "status" in doc["next"][0]["argv"]
    missing = ["add-source", str(tmp_path / "gone"), "--config", cfg]
    assert _human(missing, capsys) == cli.EXIT_USAGE
    rc, error = _error(missing, capsys)
    assert (rc, error["reason"]) == (cli.EXIT_USAGE, "usage")


def test_accept_deletions_json_shape(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _synced(initialised, capsys)
    argv = ["accept-deletions", "source", "--config", cfg]
    human = _human(argv, capsys)
    rc, doc, err = _doc(argv, capsys)
    assert rc == human == cli.EXIT_OK and err == ""
    assert doc["source"] == {"id": "source"} and doc["outcome"] in ("committed", "unchanged")
    assert doc["run"]["mode"] == "reconcile" and "status" in doc["next"][0]["argv"]


def test_adopt_json_shape(initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = str(initialised.config_path)
    for name in ("first", "second"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name.title()}.md").write_text(
            f"# {name}\n\nWritten by hand.\n", encoding="utf-8"
        )
    human = _human(["adopt", str(tmp_path / "first"), "--config", cfg], capsys)
    rc, doc, err = _doc(["adopt", str(tmp_path / "second"), "--config", cfg], capsys)
    assert rc == human == cli.EXIT_OK and err == ""
    assert (doc["pages"], doc["outcome"]) == (["topics/Second.md"], "created")


def test_whoami_json_shape(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(graph_auth, "MsalAuth", FakeAuth)
    FakeAuth.calls, FakeAuth.blocked = [], False
    cfg = _graph_config(initialised)
    human = _human(["whoami", "--config", cfg], capsys)
    rc, doc, err = _doc(["whoami", "--config", cfg], capsys)
    assert rc == human == cli.EXIT_OK and err == ""
    assert doc["account"]["signedIn"] is True and doc["account"]["username"] == "ada@contoso.com"
    assert doc["account"]["method"] == "broker" and doc["account"]["scopes"] == ["User.Read"]


def test_discover_json_shape(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(graph_auth, "MsalAuth", FakeAuth)
    cfg = _graph_config(initialised)
    scope = DiscoveredScope(SourceKind.GRAPH_DRIVE, "OneDrive", "b!abc", None, None, "your OneDrive")
    denied = discover.DiscoveryFailure("/me/joinedTeams", 403, "Forbidden", "ask IT for Team.ReadBasic.All")

    def fake_discover(client: Any, *, known: Any = ()) -> discover.DiscoveryReport:
        return discover.DiscoveryReport((scope,), (denied,))

    monkeypatch.setattr(discover, "discover_sources", fake_discover)
    human = _human(["discover", "--config", cfg], capsys)
    rc, doc, err = _doc(["discover", "--config", cfg], capsys)
    assert rc == human == cli.EXIT_FAILED and err == ""  # incomplete: the failures ride in the document
    assert doc["scopes"][0]["driveId"] == "b!abc" and "[[source]]" in doc["toml"]
    assert doc["complete"] is False and doc["failures"][0]["status"] == 403


def _strings(value: object) -> list[str]:
    """Every string a JSON value holds, keys aside."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.mark.parametrize("case", ["a missing source folder", "a lost hold and queue"])
def test_status_document_points_at_checks_entries_by_id_c1448(
    initialised: Config, tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """B13 round 38 C1448 (ruling 128): ``status --json``'s next, waitingOnYou and warnings carried the text
    view's pointers at a [FAIL] line "below", which a document does not have. The document names the checks
    entry by its id, the person's form keeping "not a step for the agent", while the text view keeps its own
    words byte for byte."""
    cfg = str(initialised.config_path)
    if case == "a missing source folder":
        gone = tmp_path / "Contoso Board"
        shutil.copytree(local_source_dir, gone)
        assert cli.main(["add-source", str(gone), "--config", cfg]) == cli.EXIT_OK
        _synced(initialised, capsys)
        shutil.rmtree(gone)
    else:
        cfg, root, _page = _hold_and_queue_dlp(initialised, capsys)
        governance.governance_dir(root).rename(root / "governance.kept")
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_FAILED
    text = capsys.readouterr().out
    rc, doc, _err = _doc(["status", "--config", cfg], capsys)
    assert rc == cli.EXIT_FAILED
    said = _strings({k: doc["status"][k] for k in ("next", "waitingOnYou", "warnings")})
    assert said and not [s for s in said if "below" in s], said
    failed = {c["id"] for c in doc["status"]["checks"] if not c["ok"] and c["severity"] == "error"}
    lead = doc["status"]["next"][0]["when"]
    assert lead.startswith("the ") and lead.split(" ", 2)[1] in failed, lead
    persons = "its fix in its checks entry is the person's, not a step for the agent: show it to them"
    assert lead.endswith(persons), lead
    assert "[FAIL] line below is the person's, not a step for the agent" in text, text
    if case == "a lost hold and queue":
        (wait,) = doc["status"]["waitingOnYou"]
        assert wait.endswith("the governance.hold entry in checks gives the person's fix"), wait
        assert "the governance.hold [FAIL] line below gives the person's fix" in text, text


def test_a_next_command_with_a_placeholder_has_no_argv_c1444(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 38 C1444 (ruling 128): with no docs repo, status's NEXT quotes `add-source "<folder>"`, and
    the document's argv carried the placeholder as the folder, so a caller that ran ``next[0].argv`` as given
    added a folder named ``<folder>``. A command holding a ``<placeholder>`` keeps its text, with argv []."""
    cfg = str(initialised.config_path)
    shutil.rmtree(initialised.docs_repo)
    capsys.readouterr()
    cli.main(["status", "--config", cfg, "--json"])
    doc = json.loads(capsys.readouterr().out)["status"]
    (step,) = [s for s in doc["next"] if s["command"] and "add-source" in s["command"]]
    assert '"<folder>"' in step["command"] and step["argv"] == [], step
    assert cli._step("run `~/.local/bin/agentsync sync` now")["argv"][1:] == ["sync"]
    assert cli._step("no command here") == {"command": None, "argv": [], "when": "no command here"}
