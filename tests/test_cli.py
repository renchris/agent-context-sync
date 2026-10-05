"""The ``agentsync`` CLI: parsing, exit codes, and each subcommand against tmp dirs (HOME is isolated)."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentsync import cli, gitops, governance, lints, net, policy
from agentsync.config import Config, load_config
from agentsync.cycle import run_cycle
from agentsync.errors import AuthError, GitError, LockHeldError
from agentsync.graph import auth as graph_auth
from agentsync.graph import discover
from agentsync.graph.auth import AuthStatus
from agentsync.graph.drive import DiscoveredScope
from agentsync.graph.errors import AuthBlockedError
from agentsync.manifest import MANIFEST_SCHEMA_VERSION, Manifest
from agentsync.model import CycleMode, SourceKind
from agentsync.ops import launchd
from agentsync.ops.lock import SingleWriterLock


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    rc = cli.main(
        [
            "init",
            "--config",
            str(cfg),
            "--docs-repo",
            str(tmp_path / "ctx" / "docs"),
            "--source-local",
            str(local_source_dir),
        ]
    )
    assert rc == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


def test_help_documents_every_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for code in ("0 ", "1 ", "2 ", "75", "77", "78", "79"):
        assert f"\n  {code}" in out
    for command in (
        "init", "sync", "status", "doctor", "reconcile", "materialise", "graph", "install-agent", "purge",
        "compact-history", "hold", "offboard", "policy",
    ):  # fmt: skip
        assert command in out
    assert cli.main(["sync", "--help"]) == 0
    assert "75  skipped" in capsys.readouterr().out


def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_USAGE
    assert cli.main(["no-such-command"]) == cli.EXIT_USAGE
    assert cli.main(["sync", "--mode", "sometimes"]) == cli.EXIT_USAGE
    capsys.readouterr()


def test_parser_accepts_the_launchd_argv(initialised: Config) -> None:
    argv = launchd.program_arguments(initialised, "poll")
    assert argv[1:7] == ("-I", "-X", "utf8", "-m", "agentsync", "sync")
    args = cli.build_parser().parse_args(list(argv[6:]))
    assert args.mode == "poll" and args.config == initialised.config_path


def test_init_writes_a_valid_config_repo_and_scaffold(initialised: Config, local_source_dir: Path) -> None:
    assert initialised.config_path.stat().st_mode & 0o777 == 0o600
    (src,) = initialised.sources
    assert src.id == "source" and src.path == local_source_dir and src.is_live
    assert (initialised.docs_repo / ".git").is_dir()
    assert (initialised.docs_repo / "README.md").is_file() and (
        initialised.docs_repo / "mirror/CLAUDE.md"
    ).is_file()


def test_init_refuses_to_overwrite_without_force(initialised: Config, tmp_path: Path) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["init", "--config", cfg, "--source-local", str(tmp_path)]) == cli.EXIT_USAGE
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_OK  # idempotent re-init


def test_sync_twice_status_lint_refresh_queue(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    repo = initialised.docs_repo
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    first = capsys.readouterr().out
    assert "commit " in first and "source: full_enumeration · complete" in first
    assert cli.main(["--config", cfg, "sync"]) == cli.EXIT_OK  # --config before the subcommand too
    assert "commit none" in capsys.readouterr().out
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    status = capsys.readouterr().out
    assert "source (local, live): baseline complete" in status and "lock: free" in status
    assert cli.main(["lint", "--config", cfg]) == cli.EXIT_OK
    assert "0 blocking" in capsys.readouterr().out
    assert cli.main(["refresh-queue", "--config", cfg]) == cli.EXIT_OK
    assert cli.main(["sync", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    assert "mode dry_run" in capsys.readouterr().out
    assert cli.main(["reconcile", "--config", cfg]) == cli.EXIT_OK
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"


def test_lint_fails_on_a_hand_edited_mirror_page(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    cli.main(["sync", "--config", cfg])
    page = initialised.docs_repo / "mirror" / "source" / "projects" / "sample.txt.md"
    page.write_text(page.read_text(encoding="utf-8") + "hand edit\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["lint", "--config", cfg]) == cli.EXIT_FAILED
    assert "FRONTMATTER" in capsys.readouterr().out
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED  # the land gate blocks the commit


def test_lint_exits_1_on_a_wrong_pin_or_a_typoed_source(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K10: a well-formed but wrong pin (STALE) and a missing source are checkpoint blockers, so lint
    fails on them; before, both passed with exit 0."""
    cfg = str(initialised.config_path)
    cli.main(["sync", "--config", cfg])
    topics = initialised.docs_repo / "topics"
    page = (
        "---\nentity: acme\npurpose: Terms.\nsources:\n  - path: {src}\n    at_rendered_sha256: {pin}\n"
        "    role: primary\n---\n# A\n"
    )
    (topics / "a.md").write_text(
        page.format(src="mirror/source/projects/sample.txt.md", pin="ab" * 32), encoding="utf-8"
    )
    (topics / "b.md").write_text(
        page.format(src="mirror/source/projects/typo.md", pin="ab" * 32), encoding="utf-8"
    )
    capsys.readouterr()
    assert cli.main(["lint", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "ERROR STALE topics/a.md" in out and "ERROR SOURCE-MISSING topics/b.md" in out
    assert "hand-written" not in out


def test_refresh_queue_exit_codes(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["refresh-queue", "--config", cfg]) == 2  # no DEPENDS.tsv yet
    cli.main(["sync", "--config", cfg])
    repo = initialised.docs_repo
    topic = repo / "topics" / "a.md"
    topic.write_text(
        "---\nentity: acme\nsources:\n  - path: ../mirror/source/projects/sample.txt.md\n"
        f"    at_rendered_sha256: {'ab' * 32}\n    role: primary\n---\n# A\n",
        encoding="utf-8",
    )
    cli.main(["sync", "--config", cfg])
    capsys.readouterr()
    assert cli.main(["refresh-queue", "--config", cfg]) == cli.EXIT_FAILED
    assert "STALE\ttopics/a.md" in capsys.readouterr().out


def test_curate_queue_lists_stale_then_uncovered(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    cli.main(["sync", "--config", cfg])
    capsys.readouterr()
    assert cli.main(["curate-queue", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "UNCOVERED\tmirror/source/projects/sample.txt.md" in out
    assert out.rstrip().endswith("uncovered mirror page(s)")
    repo = initialised.docs_repo
    (repo / "topics" / "a.md").write_text(
        "---\nentity: acme\nsources:\n  - path: ../mirror/source/projects/sample.txt.md\n"
        f"    at_rendered_sha256: {'ab' * 32}\n    role: primary\n---\n# A\n",
        encoding="utf-8",
    )
    cli.main(["sync", "--config", cfg])
    capsys.readouterr()
    assert cli.main(["curate-queue", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "STALE\ttopics/a.md" in out and "UNCOVERED\tmirror/source/projects/sample.txt.md" not in out


def _topic(
    config: Config, rel: str, mirror: str = "mirror/source/projects/sample.txt.md", pin: str | None = None
) -> None:
    """Write a lint-clean topic page citing ``mirror`` at its current rendered_sha256, or at ``pin``."""
    if pin is None:
        text = (config.docs_repo / mirror).read_text(encoding="utf-8")
        found = re.search(r"\nrendered_sha256: ([0-9a-f]{64})\n", text)
        assert found is not None
        pin = found.group(1)
    page = config.docs_repo / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\nentity: acme\npurpose: Terms; not pricing.\nsources:\n  - path: {mirror}\n"
        f"    at_rendered_sha256: {pin}\n    role: primary\n---\n# A\n\nclaim.\n",
        encoding="utf-8",
    )


def _curated(repo: Path) -> str | None:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "curated^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() or None


def test_sync_records_the_checkpoint_once_the_session_pages_are_clean(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K06: no manual checkpoint.  A sync that commits session topic pages with no checkpoint blocker
    moves ``curated`` to the pre-run HEAD, so what that same sync brought in is still listed ADDED."""
    cfg = str(initialised.config_path)
    repo = initialised.docs_repo
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "checkpoint" not in capsys.readouterr().out
    assert _curated(repo) is None  # the first-ever sync creates no tag
    cli.main(["curate-queue", "--config", cfg])
    assert "no build-session checkpoint yet" in capsys.readouterr().out
    first = git(repo, "rev-parse", "HEAD").strip()

    _topic(initialised, "topics/a.md")
    (local_source_dir / "projects" / "new.txt").write_text("new\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert f"checkpoint advanced: curated at {first[:12]}" in capsys.readouterr().out
    assert _curated(repo) == first  # the pre-run HEAD, not the new commit
    assert git(repo, "cat-file", "-t", "curated").strip() == "tag"  # annotated: dated when the session ended
    assert git(repo, "ls-files", "--", "topics/a.md").strip() == "topics/a.md"
    cli.main(["curate-queue", "--config", cfg])
    out = capsys.readouterr().out
    assert "ADDED\tmirror/source/projects/new.txt.md" in out
    assert "since the last build session (" in out and first[:12] in out

    (local_source_dir / "projects" / "sample.txt").write_text("edited\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # only a STALE banner lands on topics/a.md
    assert "checkpoint" not in capsys.readouterr().out
    assert "STALE" in (repo / "topics" / "a.md").read_text(encoding="utf-8")
    assert _curated(repo) == first

    # a.md's committed STALE banner is its only change since `curated`: its verdict holds nothing.
    banner_head = git(repo, "rev-parse", "HEAD").strip()
    _topic(initialised, "topics/d.md", "mirror/source/projects/new.txt.md")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert f"checkpoint advanced: curated at {banner_head[:12]}" in capsys.readouterr().out
    assert _curated(repo) == banner_head
    assert "STALE" in (repo / "topics" / "a.md").read_text(encoding="utf-8")

    _topic(initialised, "topics/c.md", "mirror/source/projects/new.txt.md", pin="ab" * 32)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # a held checkpoint never fails the sync
    out = capsys.readouterr().out
    assert "checkpoint held: 1 curation error(s)" in out and "(every sync retries it)" in out
    assert "ERROR STALE topics/c.md" in out and "topics/a.md" not in out
    assert _curated(repo) == banner_head
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # c.md is committed now; still retried
    assert "checkpoint held: 1 curation error(s)" in capsys.readouterr().out

    held_head = git(repo, "rev-parse", "HEAD").strip()
    _topic(initialised, "topics/a.md")
    _topic(initialised, "topics/c.md", "mirror/source/projects/new.txt.md")
    assert cli.main(["checkpoint", "--config", cfg]) == cli.EXIT_OK  # the hidden alias runs sync
    assert f"checkpoint advanced: curated at {held_head[:12]}" in capsys.readouterr().out
    assert _curated(repo) == held_head
    cli.main(["curate-queue", "--config", cfg])
    assert "0 added, 0 changed, 0 removed" in capsys.readouterr().out

    # The same run banners an existing page and commits a clean new one: the banner alone holds nothing.
    pre = git(repo, "rev-parse", "HEAD").strip()
    (local_source_dir / "projects" / "sample.txt").write_text("edited again\n", encoding="utf-8")
    _topic(initialised, "topics/e.md", "mirror/source/projects/new.txt.md")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert f"checkpoint advanced: curated at {pre[:12]}" in capsys.readouterr().out
    assert "STALE" in (repo / "topics" / "a.md").read_text(encoding="utf-8")


def test_a_checkpoint_tagging_failure_never_fails_the_landed_sync(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _synced(initialised)
    _topic(initialised, "topics/a.md")

    def boom(repo: Path, sha: str) -> None:
        raise GitError(["tag"], 128, "fatal: cannot lock ref")

    repo = initialised.docs_repo
    first = git(repo, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(gitops, "tag_curated", boom)
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "checkpoint not recorded (the sync itself landed; the next sync retries it): " in out
    assert git(repo, "ls-files", "--", "topics/a.md").strip() == "topics/a.md"
    assert _curated(repo) is None

    # The failed sync committed the session's page, so nothing is dirty now: the pending marker retries it.
    monkeypatch.undo()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert f"checkpoint advanced: curated at {first[:12]}" in capsys.readouterr().out
    assert _curated(repo) == first
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # advanced: nothing pending any more
    assert "checkpoint" not in capsys.readouterr().out


def test_the_first_ever_sync_records_no_checkpoint(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K06: a topic page already on disk at the first sync is committed, but there is no pre-run HEAD
    to tag: no ``curated`` tag, no snapshot tag, no checkpoint line, and nothing left pending."""
    cfg = _archive_on(initialised)
    repo = initialised.docs_repo
    (repo / "topics" / "a.md").write_text(
        "---\nentity: acme\npurpose: Notes; not pricing.\nprovenance: hand-written\n---\n# A\n\nclaim.\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "checkpoint" not in capsys.readouterr().out
    assert git(repo, "ls-files", "--", "topics/a.md").strip() == "topics/a.md"
    assert _curated(repo) is None
    assert git(repo, "tag", "--list", "snapshot/*").strip() == ""
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "checkpoint" not in capsys.readouterr().out
    assert _curated(repo) is None


def test_config_errors_exit_78(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sync", "--config", str(tmp_path / "missing.toml")]) == cli.EXIT_CONFIG
    bad = tmp_path / "bad.toml"
    bad.write_text("[agentsync]\nnope = 1\n", encoding="utf-8")
    assert cli.main(["status", "--config", str(bad)]) == cli.EXIT_CONFIG
    assert "configuration error" in capsys.readouterr().err


def test_unknown_source_and_graph_without_client_id_exit_78(initialised: Config) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg, "--source", "nope"]) == cli.EXIT_CONFIG
    assert cli.main(["graph", "whoami", "--config", cfg]) == cli.EXIT_CONFIG
    assert cli.main(["whoami", "--config", cfg]) == cli.EXIT_CONFIG


def test_lock_held_exits_75(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    lock = SingleWriterLock(initialised.state_paths.lock, "reconcile")
    lock.acquire()
    try:
        cfg = str(initialised.config_path)
        assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_LOCK_HELD
        assert "skipped: lock held" in capsys.readouterr().err
        assert cli.main(["status", "--config", str(initialised.config_path)]) == cli.EXIT_OK
        assert "reconcile" in capsys.readouterr().out
    finally:
        lock.release()


def test_sync_without_mode_waits_for_a_running_sync(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = SingleWriterLock(initialised.state_paths.lock, "poll")
    lock.acquire()
    timer = threading.Timer(2.0, lock.release)
    timer.start()
    try:
        assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    finally:
        timer.join()
        lock.release()
    assert "waiting for it to finish" in capsys.readouterr().err


def test_interactive_lock_wait_is_injectable_and_ends_in_lock_held(initialised: Config) -> None:
    lock = SingleWriterLock(initialised.state_paths.lock, "poll")
    lock.acquire()
    try:
        with pytest.raises(LockHeldError, match="waited"):
            run_cycle(initialised, mode=None, lock_wait_s=0.3)
    finally:
        lock.release()


def test_interactive_sync_behind_a_launchd_reconcile_runs_a_poll(initialised: Config) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    _age_runs(initialised)  # a reconcile is due, but the one holding the lock is it, still 'running'
    lock = SingleWriterLock(initialised.state_paths.lock, "reconcile")
    lock.acquire()
    timer = threading.Timer(1.0, lock.release)
    timer.start()
    try:
        report = run_cycle(initialised, mode=None, lock_wait_s=30.0)
    finally:
        timer.join()
        lock.release()
    assert report.mode is CycleMode.POLL
    assert _last_mode(initialised) == "poll"


def _last_mode(config: Config) -> str:
    with Manifest(config.state_paths.db) as manifest:
        return manifest.last_runs(1)[0][1]


def _sql(db: Path, statement: str, *params: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _age_runs(config: Config) -> None:
    _sql(config.state_paths.db, "UPDATE runs SET started_at = ?", "2020-01-01T00:00:00Z")


def test_sync_without_mode_runs_a_due_reconcile(initialised: Config) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert _last_mode(initialised) == "poll"  # the first run is a full pass anyway
    _age_runs(initialised)
    assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_OK
    assert _last_mode(initialised) == "poll"  # an explicit --mode is never overridden
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert _last_mode(initialised) == "reconcile"
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert _last_mode(initialised) == "poll"  # the reconcile just ran


def test_due_reconcile_follows_reconcile_interval_with_a_frozen_clock(initialised: Config) -> None:
    later = datetime.now(UTC) + timedelta(seconds=initialised.reconcile_interval_s + 60)
    assert run_cycle(initialised, mode=None).mode is CycleMode.POLL
    assert run_cycle(initialised, mode=CycleMode.POLL, now=lambda: later).mode is CycleMode.POLL
    assert run_cycle(initialised, mode=None, now=lambda: later).mode is CycleMode.RECONCILE
    sooner = datetime.now(UTC) + timedelta(seconds=initialised.reconcile_interval_s - 60)
    assert run_cycle(initialised, mode=None, now=lambda: sooner).mode is CycleMode.POLL


def test_interactive_syncs_keep_compaction_ok_without_a_launch_agent(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "ctx" / "sources.toml"
    for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(var, "2026-07-01T00:00:00Z")
    init = ["init", "--config", str(cfg), "--docs-repo", str(tmp_path / "ctx" / "docs")]
    assert cli.main([*init, "--source-local", str(local_source_dir)]) == cli.EXIT_OK
    assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    (local_source_dir / "projects" / "old.txt").write_text("ancient\n", encoding="utf-8")
    for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(var, "2026-07-02T00:00:00Z")
    assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.delenv(var)
    config = load_config(cfg)
    gov = governance.load_governance(config.config_path)
    assert governance.compaction_state(config.docs_repo, gov)[0] == "overdue"
    (local_source_dir / "projects" / "fresh.txt").write_text("fresh\n", encoding="utf-8")
    _age_runs(config)
    assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    assert _last_mode(config) == "reconcile"
    assert governance.compaction_state(config.docs_repo, gov)[0] == "ok"


def test_old_manifest_migrates_through_status_and_sync(initialised: Config) -> None:
    cfg = str(initialised.config_path)
    db = initialised.state_paths.db
    backup = db.with_name(f"manifest.sqlite.pre-v{MANIFEST_SCHEMA_VERSION}")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    _sql(db, "UPDATE meta SET value = '0' WHERE key = 'key_schema_version'")
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    assert backup.is_file()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # the next committed cycle drops the copy
    assert not backup.exists()
    _sql(db, "UPDATE meta SET value = '0' WHERE key = 'key_schema_version'")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert backup.is_file()  # the migrating cycle keeps its own copy for one more cycle
    with Manifest(db) as manifest:
        assert manifest.get_meta("key_schema_version") != "0"
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert not backup.exists()


def test_migrate_command_keeps_a_pre_migration_copy(initialised: Config) -> None:
    """install.sh step 4 runs `agentsync migrate` before anything else opens the manifest."""
    cfg = str(initialised.config_path)
    db = initialised.state_paths.db
    backup = db.with_name(f"manifest.sqlite.pre-v{MANIFEST_SCHEMA_VERSION}")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    _sql(db, "UPDATE meta SET value = '0' WHERE key = 'key_schema_version'")
    assert cli.main(["migrate", "--config", cfg]) == cli.EXIT_OK
    assert backup.is_file()
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'key_schema_version'").fetchone()[0] == "0"
    finally:
        conn.close()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert not backup.exists()


def test_materialise_paths(initialised: Config, local_source_dir: Path, tmp_path: Path) -> None:
    cfg = str(initialised.config_path)
    new = local_source_dir / "projects" / "late.txt"
    new.write_text("arrived late\n", encoding="utf-8")
    assert cli.main(["materialise", "--config", cfg, "--budget", "1MB", str(new)]) == cli.EXIT_OK
    assert (initialised.docs_repo / "mirror/source/projects/late.txt.md").is_file()
    # only the named file was processed: the rest of the tree is still pending work
    assert not (initialised.docs_repo / "mirror/source/projects/sample.docx.md").exists()
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x", encoding="utf-8")
    assert cli.main(["materialise", "--config", cfg, str(outside)]) == cli.EXIT_CONFIG
    assert cli.main(["materialise", "--config", cfg, "--budget", "lots"]) == cli.EXIT_CONFIG


def test_reconcile_accept_deletions_needs_a_source(initialised: Config) -> None:
    assert cli.main(["reconcile", "--accept-deletions", "--config", str(initialised.config_path)]) == 2


def test_adopt_and_migrate(initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = str(initialised.config_path)
    notes = tmp_path / "old-notes"
    notes.mkdir()
    (notes / "Plan.md").write_text("# Plan\n\nWritten by hand.\n", encoding="utf-8")
    assert cli.main(["adopt", str(notes), "--config", cfg]) == cli.EXIT_OK
    assert "adopted topics/Plan.md" in capsys.readouterr().out
    assert "provenance: hand-written" in (initialised.docs_repo / "topics" / "Plan.md").read_text(
        encoding="utf-8"
    )
    assert cli.main(["migrate", "--config", cfg]) == cli.EXIT_OK
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "topics/Plan.md" in git(initialised.docs_repo, "ls-files")


def test_install_and_uninstall_agent_call_launchd(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed: list[launchd.AgentSpec] = []
    removed: list[str] = []

    def fake_install(spec: launchd.AgentSpec, **_k: Any) -> Path:
        installed.append(spec)
        return Path("/nonexistent") / f"{spec.label}.plist"

    def fake_uninstall(label: str, **_k: Any) -> bool:
        removed.append(label)
        return True

    excluded: list[Config] = []
    monkeypatch.setattr(launchd, "install", fake_install)
    monkeypatch.setattr(launchd, "uninstall", fake_uninstall)
    monkeypatch.setattr(
        governance, "apply_time_machine_exclusions", lambda c, **_k: excluded.append(c) or ["excluded x"]
    )
    cfg = str(initialised.config_path)
    assert cli.main(["install-agent", "--interval", "120", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "time machine: excluded x" in out and len(excluded) == 1  # C15 req 42, once at install
    assert f"launchd runs: {installed[0].program_arguments[0]}" in out
    assert [(s.label, s.start_interval_s) for s in installed] == [
        ("com.agentsync.poll", 120),
        ("com.agentsync.reconcile", 3600),
    ]
    assert installed[0].materialize_dataless_files is False
    assert installed[0].program_arguments[-4:] == ("--mode", "poll", "--config", cfg)
    assert cli.main(["uninstall-agent", "--config", cfg]) == cli.EXIT_OK
    assert removed == ["com.agentsync.poll", "com.agentsync.reconcile"]


def test_doctor_runs(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["doctor", "--config", str(initialised.config_path)])
    out = capsys.readouterr().out
    assert "[ok  ] git" in out and "launchd.poll" in out
    assert rc in (cli.EXIT_OK, cli.EXIT_FAILED)


def test_python_dash_m_entry_point(initialised: Config) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentsync",
            "sync",
            "--mode",
            "poll",
            "--config",
            str(initialised.config_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr
    assert "run 1 · mode poll · commit " in proc.stdout


# ---------------------------------------------------------------------------------------------------------
# hardening wiring (C15 section 9): trust store, sign-in ladder, discovery, governance, policy, doctor
# ---------------------------------------------------------------------------------------------------------

_IMPORT_SPY = """
import importlib.abc, json, ssl, sys
seen = {}
class Spy(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in ("msal", "requests", "urllib3", "httpx") and name not in seen:
            seen[name] = ssl.SSLContext.__module__
        return None
sys.meta_path.insert(0, Spy())
import %s
print(json.dumps(seen, sort_keys=True))
"""


def _import_order(module: str) -> dict[str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_SPY % module], capture_output=True, text=True, check=True, timeout=120
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_trust_store_is_injected_before_msal_and_requests_are_imported() -> None:
    """C15 req 32: the CLI entry point injects truststore before msal/requests/urllib3/httpx load."""
    seen = _import_order("agentsync.cli")
    assert {"msal", "requests", "urllib3", "httpx"} <= set(seen)
    assert all(v.startswith("truststore") for v in seen.values()), seen
    # control: without the CLI entry point the same libraries load against the stdlib SSLContext
    control = _import_order("agentsync.cycle")
    assert control["msal"] == "ssl" and control["requests"] == "ssl"


def _graph_config(initialised: Config) -> str:
    path = initialised.config_path
    text = path.read_text(encoding="utf-8")
    text = text.replace('tenant = "organizations"', 'tenant = "contoso.onmicrosoft.com"', 1)
    text = text.replace(
        '# client_id = "00000000-0000-0000-0000-000000000000"',
        'client_id = "00000000-0000-0000-0000-000000000001"',
        1,
    )
    path.write_text(text, encoding="utf-8")
    return str(path)


class FakeAuth:
    """MsalAuth stand-in: never touches MSAL, the broker, a browser or the Keychain."""

    calls: ClassVar[list[str]] = []
    blocked: ClassVar[bool] = False

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.last_token_source: str | None = None

    def _status(self) -> AuthStatus:
        return AuthStatus(True, "ada@contoso.com", "tid-1", "keychain", ("User.Read",), "broker")

    def login(self, emit: Callable[[str], None]) -> AuthStatus:
        FakeAuth.calls.append("ladder")
        if FakeAuth.blocked:
            raise AuthBlockedError(
                "blocked: consent",
                "AADSTS65001",
                "REAUTH_REQUIRED (blocked: consent): ask IT to grant consent",
            )
        emit("signing in through the macOS broker")
        self.last_token_source = "broker"
        return self._status()

    def login_device_code(self, emit: Callable[[str], None]) -> AuthStatus:
        FakeAuth.calls.append("device-code")
        raise AuthError("device-code sign-in is off: set [graph] allow_device_code = true")

    def status(self) -> AuthStatus:
        return self._status()

    def logout(self) -> None:
        FakeAuth.calls.append("logout")

    def get_token(self) -> str:
        return "token"


@pytest.fixture
def fake_auth(monkeypatch: pytest.MonkeyPatch) -> type[FakeAuth]:
    monkeypatch.setattr(graph_auth, "MsalAuth", FakeAuth)
    FakeAuth.calls = []
    FakeAuth.blocked = False
    return FakeAuth


def test_login_runs_the_sign_in_ladder_and_reports_the_method(
    initialised: Config, fake_auth: type[FakeAuth], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _graph_config(initialised)
    assert cli.main(["graph", "login", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert fake_auth.calls == ["ladder"]  # broker -> loopback -> device code lives in MsalAuth.login
    assert "signed in as ada@contoso.com" in out and "method broker" in out and "token source broker" in out
    assert cli.main(["whoami", "--config", cfg]) == cli.EXIT_OK
    assert "method broker" in capsys.readouterr().out
    # --device-code goes straight to the last rung, which MsalAuth refuses unless allowed
    assert cli.main(["login", "--device-code", "--config", cfg]) == cli.EXIT_FAILED
    assert fake_auth.calls[-1] == "device-code"
    assert "allow_device_code" in capsys.readouterr().err


def test_blocked_sign_in_exits_77_naming_the_state(
    initialised: Config, fake_auth: type[FakeAuth], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_auth.blocked = True
    assert cli.main(["login", "--config", _graph_config(initialised)]) == cli.EXIT_REAUTH
    err = capsys.readouterr().err
    assert (
        "sign-in blocked (blocked: consent, AADSTS65001)" in err and "run `agentsync graph login`" not in err
    )


def test_multi_tenant_authority_is_a_config_error(initialised: Config, fake_auth: type[FakeAuth]) -> None:
    cfg = _graph_config(initialised)
    path = Path(cfg)
    path.write_text(
        path.read_text(encoding="utf-8").replace("contoso.onmicrosoft.com", "common"), encoding="utf-8"
    )
    assert cli.main(["login", "--config", cfg]) == cli.EXIT_CONFIG  # AADSTS50194, never tried
    assert fake_auth.calls == []


def test_discover_prints_the_snippet_and_fails_when_incomplete(
    initialised: Config,
    fake_auth: type[FakeAuth],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _graph_config(initialised)
    scope = DiscoveredScope(SourceKind.GRAPH_DRIVE, "OneDrive", "b!abc", None, None, "your OneDrive")
    denied = discover.DiscoveryFailure("/me/joinedTeams", 403, "Forbidden", "ask IT for Team.ReadBasic.All")
    seen: list[str] = []

    def fake_discover(client: Any, *, known: Any = ()) -> discover.DiscoveryReport:
        seen.append("all")
        return discover.DiscoveryReport((scope,), (denied,))

    def fake_resolve(client: Any, url: str, *, known: Any = ()) -> discover.DiscoveryReport:
        seen.append(url)
        return discover.DiscoveryReport((scope,))

    monkeypatch.setattr(discover, "discover_sources", fake_discover)
    monkeypatch.setattr(discover, "resolve_url", fake_resolve)
    assert cli.main(["discover", "--config", cfg]) == cli.EXIT_FAILED
    captured = capsys.readouterr()
    assert "[[source]]" in captured.out and 'drive_id = "b!abc"' in captured.out
    assert "DISCOVERY INCOMPLETE" in captured.out
    assert "discovery incomplete: /me/joinedTeams -> HTTP 403" in captured.err
    url = "https://contoso.sharepoint.com/sites/finance"
    assert cli.main(["graph", "discover", "--toml", "--url", url, "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("# agentsync discover") and seen == ["all", url]


def _synced(initialised: Config) -> str:
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    return cfg


def _stable_id(config: Config, rel_path: str) -> str:
    with Manifest(config.state_paths.db) as m:
        return next(r.stable_id for r in m.iter_items("source") if r.rel_path == rel_path)


def test_purge_removes_every_blob_and_the_item_never_comes_back(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """C15 req 37 + 41 through the CLI, then the cycle's suppression list keeps it purged."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    blob = git(repo, "rev-parse", f"HEAD:{page}").strip()
    sid = _stable_id(initialised, "projects/sample.txt")
    capsys.readouterr()
    assert cli.main(["purge", f"id={sid}", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    assert "dry run" in capsys.readouterr().out and (repo / page).is_file()
    rc = cli.main(
        ["purge", f"id={sid}", "--source", "source", "--reason", "erasure-request", "--config", cfg]
    )
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK, out
    assert "VERIFIED" in out and page in out
    gone = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", blob], capture_output=True, check=False)
    assert gone.returncode != 0  # the blob is unreadable afterwards
    audit = governance.read_audit(initialised.state_paths.root)
    assert any(a.get("reason") == "erasure-request" for a in audit)
    assert "sample.txt" not in json.dumps(audit)  # hashes only, never the path or content
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # the source file still exists upstream
    assert not (repo / page).exists()
    assert "sample.txt.md" not in git(repo, "log", "--all", "--name-only", "--format=")
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_OK
    assert cli.main(["purge", "--config", cfg]) == cli.EXIT_USAGE


def _archive_on(config: Config) -> str:
    path = config.config_path
    path.write_text(path.read_text(encoding="utf-8") + "\n[governance]\narchive = true\n", encoding="utf-8")
    return str(path)


def test_archive_keeps_a_deleted_page_and_snapshots_each_checkpoint(
    initialised: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """[governance] archive: a deleted page stays searchable in archive/, each sync that advances the
    checkpoint leaves a permanent snapshot tag on its new commit, compaction is off, and a manual purge still
    erases the archive copy from all history."""
    cfg = _archive_on(initialised)
    repo = initialised.docs_repo
    page, kept = "mirror/source/projects/sample.txt.md", "archive/source/projects/sample.txt.md"
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    live_body = (repo / page).read_text(encoding="utf-8").split("\n---\n", 1)[1]
    capsys.readouterr()
    _topic(initialised, "topics/a.md", "mirror/source/projects/sample.csv.md")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    found = re.search(r"snapshot (snapshot/\d{4}-\d{2}-\d{2}T\d{6}Z): ", capsys.readouterr().out)
    assert found is not None
    first = found.group(1)
    assert git(repo, "cat-file", "-t", first).strip() == "tag"
    before = git(repo, "rev-parse", f"{first}^{{commit}}").strip()
    assert before == git(repo, "rev-parse", "HEAD").strip()  # the new commit: it holds the session's page
    git(repo, "cat-file", "-e", f"{first}:topics/a.md")

    (local_source_dir / "projects" / "sample.txt").unlink()
    for _ in range(2):  # a local file must be absent from two complete passes
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    text = (repo / kept).read_text(encoding="utf-8")
    assert "\nstatus: archived\n" in text and "\ndeleted_at: " in text and "\nlast_commit: " in text
    assert text.endswith(live_body) and policy.UNTRUSTED_BANNER in text
    assert f"kept, searchable, at {kept}" in (repo / page).read_text(encoding="utf-8")
    assert git(repo, "ls-files", "--", kept).strip() == kept  # committed with the tombstone
    assert governance.pending_purges(initialised.state_paths.root) == []  # archive queues no purge
    assert not [f for f in lints.lint_mirror_frontmatter(repo) if f.blocking]
    state_md = (repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "- archive on: history is kept" in state_md
    capsys.readouterr()
    cli.main(["curate-queue", "--config", cfg])
    assert f"REMOVED\t{page}\t{kept}\n" in capsys.readouterr().out
    assert cli.main(["compact-history", "--keep-days", "0", "--config", cfg]) == cli.EXIT_FAILED
    assert "archive on: history is kept" in capsys.readouterr().err

    monkeypatch.setattr("agentsync.cycle._Cycle.now", lambda self: datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC))
    _topic(initialised, "topics/b.md", "mirror/source/projects/sample.csv.md")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "snapshot snapshot/2030-01-02T030405Z: " in capsys.readouterr().out
    assert git(repo, "rev-parse", f"{first}^{{commit}}").strip() == before  # never moved

    blob = git(repo, "rev-parse", f"HEAD:{kept}").strip()
    sid = _stable_id(initialised, "projects/sample.txt")
    purge = ["purge", f"id={sid}", "--source", "source", "--reason", "erasure-request"]
    rc = cli.main([*purge, "--config", cfg])
    assert rc == cli.EXIT_OK, capsys.readouterr().out
    assert not (repo / kept).exists() and not (repo / page).exists()
    gone = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", blob], capture_output=True, check=False)
    assert gone.returncode != 0  # the archive copy is erased from every commit
    assert "sample.txt.md" not in git(repo, "log", "--all", "--name-only", "--format=")
    for tag in (first, "snapshot/2030-01-02T030405Z"):  # the rewrite remapped the snapshot tags
        assert git(repo, "cat-file", "-t", tag).strip() == "tag"
        git(repo, "rev-parse", "--verify", f"{tag}^{{commit}}")


def test_hold_suspends_purge_and_compaction_and_shows_in_status(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _synced(initialised)
    assert cli.main(["hold", "all", "--reason", "litigation 42", "--config", cfg]) == cli.EXIT_USAGE  # owner
    assert cli.main(["hold", "all", "--reason", "litigation 42", "--owner", "legal", "--config", cfg]) == 0
    capsys.readouterr()
    sid = _stable_id(initialised, "projects/sample.txt")
    assert cli.main(["purge", f"id={sid}", "--config", cfg]) == cli.EXIT_FAILED
    assert "suspended by legal/records hold" in capsys.readouterr().err
    assert cli.main(["compact-history", "--keep-days", "0", "--config", cfg]) == cli.EXIT_FAILED
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    assert "HOLD: hold all (state): litigation 42" in capsys.readouterr().out
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    state_md = (initialised.docs_repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "HOLD `all`: litigation 42" in state_md  # C15 req 40: shown until released
    assert cli.main(["hold", "--list", "--config", cfg]) == cli.EXIT_OK
    assert "1 active hold(s)" in capsys.readouterr().out
    assert cli.main(["hold", "all", "--release", "--owner", "legal", "--config", cfg]) == cli.EXIT_OK
    assert cli.main(["compact-history", "--dry-run", "--config", cfg]) == cli.EXIT_OK


def test_offboard_is_a_dry_run_unless_confirmed(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))  # `security dump-keychain` lists nothing: the real Keychain is untouched
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    monkeypatch.setattr(governance, "_run", fake_run)
    monkeypatch.setattr(launchd, "uninstall", lambda label: False)
    cfg = _synced(initialised)
    capsys.readouterr()
    assert cli.main(["offboard", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "state-dir\texists" in out and "dry run: nothing removed" in out
    assert initialised.state_paths.root.is_dir()
    assert all("delete-generic-password" not in c for c in calls)
    rc = cli.main(["offboard", "--confirm", str(initialised.docs_repo), "--config", cfg])
    assert rc == cli.EXIT_OK
    assert not initialised.state_paths.root.exists() and initialised.docs_repo.is_dir()  # docs kept


def test_policy_show_and_a_broken_policy_is_a_config_error(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    guid = "2096f6a2-d2f7-48be-b329-b73aaa526e5d"
    with initialised.config_path.open("a", encoding="utf-8") as fh:
        fh.write(f'\n[policy]\nexclude_label_ids = ["{guid.upper()}"]\n')
    assert cli.main(["policy", "show", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"exclude_label_ids: {guid}" in out and "labels_active: true" in out
    (initialised.config_path.parent / "policy.toml").write_text("[policy]\nnope = 1\n", encoding="utf-8")
    assert cli.main(["policy", "show", "--config", cfg]) == cli.EXIT_CONFIG
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_CONFIG  # a broken policy never means "allow"


def test_remote_on_the_docs_repo_is_refused(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C15 req 36: no remote unless [governance] names a tenant-owned one."""
    cfg = str(initialised.config_path)
    git(initialised.docs_repo, "remote", "add", "origin", "https://github.com/someone/docs.git")
    monkeypatch.setattr(launchd, "install", lambda spec: pytest.fail("installed despite a remote"))
    assert cli.main(["install-agent", "--config", cfg]) == cli.EXIT_FAILED
    assert "allow_remote = false" in capsys.readouterr().err
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_FAILED


def test_doctor_reports_network_broker_governance_and_policy(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    probes: list[str] = []

    def fake_probe(url: str, settings: net.ProxySettings, **_k: Any) -> net.Reachability:
        probes.append(url)
        return net.Reachability(net.POLICY_TLS, "certificate verify failed", "direct")

    monkeypatch.setattr(net, "probe_reachability", fake_probe)
    cfg = str(initialised.config_path)
    rc = cli.main(["doctor", "--network", "--config", cfg])
    out = capsys.readouterr().out
    assert "network.proxy" in out and "governance.remote" in out and "] policy" in out
    assert "failed: network-policy: TLS" in out and rc == cli.EXIT_FAILED  # TLS is failed, never skipped
    assert probes == [initialised.graph.base_url]
    git(initialised.docs_repo, "remote", "add", "origin", "https://github.com/someone/docs.git")
    cli.main(["doctor", "--config", cfg])
    assert "[FAIL] governance.remote" in capsys.readouterr().out


def test_status_surfaces_the_launchers_tcc_tokens(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    log_dir = initialised.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "com.agentsync.poll.err.log").write_text(
        "2026-09-29T10:00:00Z agentsync-launcher[1]: CANARY_OK path=/x\n"
        "2026-09-29T10:00:10Z agentsync-launcher[1]: TCC_PENDING reason=canary path=/y\n",
        encoding="utf-8",
    )
    assert cli.main(["status", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    assert (
        "launcher: poll: 2026-09-29T10:00:10Z agentsync-launcher[1]: TCC_PENDING" in capsys.readouterr().out
    )


# ---- add-source ------------------------------------------------------------------------------------------


def test_add_source_appends_a_live_local_source_and_is_idempotent(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    before = initialised.config_path.read_text(encoding="utf-8")
    folder = tmp_path / "Shared" / "FY26 Projects"
    folder.mkdir(parents=True)
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "added source 'fy26-projects'" in out and '[[source]]\nid = "fy26-projects"\nkind = "local"' in out
    after = initialised.config_path.read_text(encoding="utf-8")
    assert after.startswith(before)  # every existing byte, comments included, is kept
    assert "# ---- sources ----" in after and "# A manual drag-and-drop inbox:" in after
    config = load_config(initialised.config_path)
    added = config.source("fy26-projects")
    assert added.kind is SourceKind.LOCAL and added.is_live and added.path == folder.resolve()
    assert added.exclude == initialised.sources[0].exclude  # the same defaults init --source-local writes
    assert added.max_materialise_bytes == initialised.sources[0].max_materialise_bytes
    assert initialised.config_path.stat().st_mode & 0o777 == 0o600

    # Same folder again, spelled differently (trailing slash, a symlink): nothing is written.
    link = tmp_path / "link-to-projects"
    link.symlink_to(folder)
    for spelling in (f"{folder}/", str(link)):
        assert cli.main(["add-source", spelling, "--config", cfg]) == cli.EXIT_OK
        assert "already configured: source 'fy26-projects'" in capsys.readouterr().out
    assert cli.main(["add-source", str(initialised.sources[0].path), "--config", cfg]) == cli.EXIT_OK
    assert "already configured: source 'source'" in capsys.readouterr().out
    assert initialised.config_path.read_text(encoding="utf-8") == after


def test_add_source_inbox_creates_the_drop_folder_beside_the_docs_repo_and_is_idempotent(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    inbox = initialised.docs_repo.parent / "inbox"
    assert not inbox.exists()
    assert cli.main(["add-source", "--inbox", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '[[source]]\nid = "inbox"\nkind = "inbox"' in out
    assert inbox.is_dir() and inbox.stat().st_mode & 0o777 == 0o700
    added = load_config(initialised.config_path).source("inbox")
    assert added.kind is SourceKind.INBOX and added.is_live and added.path == inbox.resolve()
    after = initialised.config_path.read_text(encoding="utf-8")
    assert cli.main(["add-source", "--inbox", "--config", cfg]) == cli.EXIT_OK
    assert "already configured: source 'inbox'" in capsys.readouterr().out
    assert initialised.config_path.read_text(encoding="utf-8") == after
    assert cli.main(["add-source", "--config", cfg]) == cli.EXIT_USAGE  # no PATH and no --inbox


def test_add_source_derives_a_unique_id_and_honours_id(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    other = tmp_path / "elsewhere" / "source"  # same folder name as the initialised source ("source")
    other.mkdir(parents=True)
    assert cli.main(["add-source", str(other), "--config", cfg]) == cli.EXIT_OK
    assert [s.id for s in load_config(initialised.config_path).sources] == ["source", "source-2"]
    third = tmp_path / "third"
    third.mkdir()
    assert cli.main(["add-source", str(third), "--id", "source", "--config", cfg]) == cli.EXIT_USAGE
    assert cli.main(["add-source", str(third), "--id", "Not An Id", "--config", cfg]) == cli.EXIT_USAGE
    assert cli.main(["add-source", str(third), "--id", "team-notes", "--config", cfg]) == cli.EXIT_OK
    assert [s.id for s in load_config(initialised.config_path).sources] == [
        "source",
        "source-2",
        "team-notes",
    ]
    capsys.readouterr()


def test_add_source_refuses_bad_paths_and_writes_nothing(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    before = initialised.config_path.read_text(encoding="utf-8")
    a_file = tmp_path / "a-file.txt"
    a_file.write_text("x", encoding="utf-8")
    docs = initialised.docs_repo
    for bad in (tmp_path / "no-such-folder", a_file, docs, docs / "mirror", docs.parent):
        assert cli.main(["add-source", str(bad), "--config", cfg]) == cli.EXIT_USAGE, bad
    err = capsys.readouterr().err
    assert "no such folder" in err and "not a directory" in err and "must not contain each other" in err
    assert initialised.config_path.read_text(encoding="utf-8") == before
    # A missing or broken sources.toml is a config error (78), never a new file.
    missing = tmp_path / "none" / "sources.toml"
    assert cli.main(["add-source", str(tmp_path), "--config", str(missing)]) == cli.EXIT_CONFIG
    assert not missing.exists()
    capsys.readouterr()


def test_sync_materialise_budget_0_converts_local_files_and_defers_online_only_ones(
    initialised: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``sync --materialise-budget 0`` (install.sh's first sync, K15 and L3): the byte budget is charged only
    for files that are dataless (online-only) when read, so every local file is converted now and only the
    online-only ones wait for a run whose budget allows the download. Each run ends with one line
    ``converted N, deferred M online-only``."""
    from agentsync import arm_local, materialise  # noqa: PLC0415

    online = {(local_source_dir / "projects" / n).stat().st_ino for n in ("sample.pptx", "sample.pdf")}
    real = materialise.is_dataless

    def fake(st: object) -> bool:  # mocked SF_DATALESS: no File Provider in a test
        return getattr(st, "st_ino", None) in online or real(st)  # type: ignore[arg-type]

    monkeypatch.setattr(materialise, "is_dataless", fake)
    monkeypatch.setattr(arm_local, "is_dataless", fake)
    cfg = str(initialised.config_path)
    mirror = initialised.docs_repo / "mirror/source/projects"
    assert cli.main(["sync", "--once", "--materialise-budget", "0", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "source: full_enumeration · complete · 2 deferred" in out
    assert "exceeds the per-cycle budget" not in out, "no per-file alarm at budget 0"
    assert re.search(
        r"^    2 online-only file\(s\) left for a later run \(over this run's --materialise-budget of 0 "
        r"bytes\)$",
        out,
        re.MULTILINE,
    )
    last = out.rstrip("\n").splitlines()[-1]
    m = re.fullmatch(r"converted (\d+), deferred 2 online-only", last)
    assert m and int(m.group(1)) >= 8, last
    assert not re.search(r"(?im)^\s*(next|run|fix):", out)
    assert (mirror / "sample.docx.md").is_file(), "a local file is converted by the budget-0 run"
    assert not (mirror / "sample.pdf.md").exists(), "an online-only file is not downloaded"
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert (mirror / "sample.pdf.md").is_file() and "left for a later run" not in out
    assert out.rstrip("\n").splitlines()[-1] == "converted 2, deferred 0 online-only"
    assert cli.main(["sync", "--materialise-budget", "lots", "--config", cfg]) == cli.EXIT_CONFIG
    parser = cli.build_parser()
    sync = parser.parse_args(["sync", "--materialise-budget", "200MB"])
    assert sync.materialise_budget == "200MB"
    capsys.readouterr()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "Only online-only files are charged" in text and "every local file is converted" in text


def test_no_next_hint_env_silences_init_add_source_and_setup_report(
    tmp_path: Path,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """install.sh exports AGENTSYNC_NO_NEXT_HINT=1 so that its own NEXT: is the only next step (K5)."""
    from agentsync import setup_report  # noqa: PLC0415

    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: (113, "not loaded"))
    cfg = tmp_path / "ctx" / "sources.toml"
    other = tmp_path / "Other Folder"
    other.mkdir()
    argv_init = ["init", "--config", str(cfg), "--docs-repo", str(tmp_path / "ctx" / "docs")]
    report = ["setup-report", "--out", str(tmp_path / "r.md"), "--config", str(cfg)]
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    assert cli.main([*argv_init, "--source-local", str(local_source_dir)]) == cli.EXIT_OK
    assert cli.main(["add-source", str(other), "--config", str(cfg)]) == cli.EXIT_OK
    assert cli.main(report) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert not re.search(r"(?im)^\s*next:", out), out
    assert (
        out.rstrip("\n")
        .splitlines()[-1]
        .startswith(f"issue link (review the report first): {setup_report.ISSUE_URL}&")
    )
    monkeypatch.delenv(cli.NO_NEXT_HINT_ENV)
    third = tmp_path / "Third"
    third.mkdir()
    assert cli.main([*argv_init, "--force", "--source-local", str(local_source_dir)]) == cli.EXIT_OK
    assert cli.main(["add-source", str(third), "--config", str(cfg)]) == cli.EXIT_OK
    assert cli.main(report) == cli.EXIT_OK
    hints = re.findall(r"(?m)^next: .*$", capsys.readouterr().out)
    assert hints == [
        "next: agentsync doctor · agentsync sync --once · agentsync install-agent",
        "next: agentsync doctor · agentsync sync --once",
    ], "without the variable: init's and add-source's hints, and none from setup-report"
