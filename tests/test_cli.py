"""The ``agentsync`` CLI: parsing, exit codes, and each subcommand against tmp dirs (HOME is isolated)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentsync import cli
from agentsync.config import Config, load_config
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
    for code in ("0 ", "1 ", "2 ", "75", "77", "78"):
        assert f"\n  {code}" in out
    for command in ("init", "sync", "status", "doctor", "reconcile", "materialise", "graph", "install-agent"):
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
    assert argv[1:4] == ("-m", "agentsync", "sync")
    args = cli.build_parser().parse_args(list(argv[3:]))
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
        assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_LOCK_HELD
        assert "skipped: lock held" in capsys.readouterr().err
        assert cli.main(["status", "--config", str(initialised.config_path)]) == cli.EXIT_OK
        assert "reconcile" in capsys.readouterr().out
    finally:
        lock.release()


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

    monkeypatch.setattr(launchd, "install", fake_install)
    monkeypatch.setattr(launchd, "uninstall", fake_uninstall)
    cfg = str(initialised.config_path)
    assert cli.main(["install-agent", "--interval", "120", "--config", cfg]) == cli.EXIT_OK
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
