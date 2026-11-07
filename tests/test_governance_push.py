"""A failed ``purge --push`` keeps the purge's record (§16.300.3), on a tmp repo and a bare tmp remote."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from agentsync import cli
from agentsync import governance as gv
from agentsync.config import Config
from agentsync.manifest import Manifest


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo_with_one_page(tmp_path: Path) -> Config:
    """A docs repo with one committed page, its manifest row, and a remote that refuses every push."""
    repo = tmp_path / "docs"
    (repo / "mirror" / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@contoso.example")
    (repo / "mirror" / "src" / "secret.md").write_text("SECRET\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sync: 1a")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    config_path = tmp_path / "agent-context" / "sources.toml"
    config_path.parent.mkdir()
    config_path.write_text("", encoding="utf-8")
    config = Config(
        config_path=config_path,
        docs_repo=repo,
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        launchd_label_prefix="com.agentsync-test",
    )
    db = config.state_paths.db
    db.parent.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(db)
    manifest.set_tree_sha(_git(repo, "rev-parse", "HEAD^{tree}"))
    manifest.close()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sources (source_id, kind, config_state, config_fingerprint) "
        "VALUES ('src', 'local', 'live', 'x')"
    )
    conn.execute(
        "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, last_seen_run, "
        "canonical_sha256) VALUES ('src', 'S1', 'secret.md', 'hr/secret.md', 'live', 1, 1, 'CANON')"
    )
    conn.execute(
        "INSERT INTO outputs (output_path, source_id, stable_id, unit_id, action_key, rendered_sha256, "
        "status, built_run) VALUES ('mirror/src/secret.md', 'src', 'S1', 'whole', ?, 'r', 'ok', 1)",
        ("a" * 64,),
    )
    conn.commit()
    conn.close()
    return config


def test_a_refused_push_keeps_the_suppressions_and_the_audit_line(tmp_path: Path) -> None:
    config = _repo_with_one_page(tmp_path)
    remote = tmp_path / "remote.git"
    before = _git(remote, "rev-parse", "main")
    gov = gv.GovernanceConfig(allow_remote=True, remote_url_prefixes=(str(tmp_path),))

    report = gv.purge(
        config, gv.PurgeSelector(stable_id="S1"), reason=gv.PurgeReason.OPERATOR, gov=gov, push=True
    )

    assert report.remote.startswith("push failed, remote state unknown: "), report.remote
    assert report.push_failed is True
    assert report.verified  # the local erasure is done, and the report says so
    assert _git(remote, "rev-parse", "main") == before  # the refusal was real

    state = config.state_paths.root
    assert gv.lost_suppressions(state) == []
    assert ("src", "S1") in gv.load_suppressions(state).items
    actions = [
        json.loads(line)["action"] for line in gv.audit_path(state).read_text(encoding="utf-8").splitlines()
    ]
    assert "purge" in actions and "purge-summary" in actions


def test_a_refused_push_exits_1_and_says_inspect_never_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _repo_with_one_page(tmp_path)
    config.config_path.write_text(
        "[agentsync]\n"
        f"docs_repo = {json.dumps(str(config.docs_repo))}\n"
        f"state_dir = {json.dumps(str(config.state_dir))}\n"
        f"cache_dir = {json.dumps(str(config.cache_dir))}\n"
        f"log_dir = {json.dumps(str(config.log_dir))}\n"
        "[governance]\n"
        "allow_remote = true\n"
        f"remote_url_prefixes = [{json.dumps(str(tmp_path))}]\n",
        encoding="utf-8",
    )

    code = cli.main(["purge", "id=S1", "--source", "src", "--push", "--config", str(config.config_path)])

    assert code == cli.EXIT_FAILED == 1
    captured = capsys.readouterr()
    assert "remote state unknown" in captured.err
    assert "error: " in captured.err
    assert "ls-remote origin" in captured.err
    assert "may be refused" in captured.err
    assert "retry" not in captured.err
    assert "remote: push failed, remote state unknown" in captured.out
