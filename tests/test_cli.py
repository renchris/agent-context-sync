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
import yaml

from agentsync import __version__, cli, gitops, governance, it_request, lints, loop, net, policy, skill
from agentsync.config import Config, inbox_source_table, load_config
from agentsync.cycle import run_cycle
from agentsync.errors import AuthError, GitError, LockHeldError
from agentsync.graph import auth as graph_auth
from agentsync.graph import discover
from agentsync.graph.auth import AuthStatus
from agentsync.graph.drive import DiscoveredScope
from agentsync.graph.errors import AuthBlockedError
from agentsync.manifest import MANIFEST_SCHEMA_VERSION, Manifest
from agentsync.model import CycleMode, SourceKind
from agentsync.ops import doctor, launchd
from agentsync.ops.lock import SingleWriterLock


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


def test_help_documents_every_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for code in ("0 ", "1 ", "2 ", "75", "77", "78", "79"):
        assert f"\n  {code}" in out
    for command in ("add-source", "sync", "status", "accept-deletions", "purge", "hold", "offboard"):
        assert command in out
    assert "install-agent" not in out and "uninstall-agent" not in out  # KISS K11a: hidden, still parse
    assert cli.main(["sync", "--help"]) == 0
    assert "75  skipped" in capsys.readouterr().out


def test_graph_verbs_it_request_and_config_are_hidden_but_parse(capsys: pytest.CaptureFixture[str]) -> None:
    """KISS K18: graph, its four top-level aliases and it-request are hidden from help, as are the global
    --config and graph login --device-code; each still parses."""
    assert cli.main(["--help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for name in ("graph", "login", "logout", "whoami", "discover", "it-request"):
        assert not re.search(rf"(?m)^    {name}\b", out), f"{name} is hidden from help"
    assert "--config" not in out
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["graph", "login", "--help"])
    assert "--device-code" not in capsys.readouterr().out
    parser = cli.build_parser()
    args = parser.parse_args(["--config", "x.toml", "graph", "login", "--device-code"])
    assert (args.action, args.device_code, args.config) == ("login", True, Path("x.toml"))
    for name in ("login", "logout", "whoami", "discover"):
        assert parser.parse_args([name, "--config", "x.toml"]).action == name
    assert parser.parse_args(["login", "--device-code"]).device_code is True
    assert parser.parse_args(["it-request"]).out == Path(it_request.DEFAULT_OUT)


def test_sync_takes_no_visible_option_and_the_hidden_maintenance_verbs_still_parse(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K13b: sync drops --dry-run and --source and hides --once, --mode and --materialise-budget;
    materialise, migrate and compact-history are hidden, migrate is a no-op, and compact-history refuses
    --keep-days below 1."""
    cfg = str(initialised.config_path)
    assert cli.main(["--help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for name in ("materialise", "migrate", "compact-history"):
        assert not re.search(rf"(?m)^    {name}\b", out), f"{name} is hidden from help"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["sync", "--help"])
    out = capsys.readouterr().out
    for flag in ("--once", "--mode", "--materialise-budget", "--dry-run", "--source"):
        assert flag not in out, flag
    for dropped in (["--dry-run"], ["--source", "source"]):
        assert cli.main(["sync", *dropped, "--config", cfg]) == cli.EXIT_USAGE, dropped
    parser = cli.build_parser()
    hidden = parser.parse_args(["sync", "--once", "--mode", "reconcile", "--materialise-budget", "0"])
    assert hidden.once and hidden.mode == "reconcile" and hidden.materialise_budget == "0"
    for argv in (["materialise", "--budget", "1MB"], ["migrate"], ["compact-history", "--keep-days", "1"]):
        assert parser.parse_args(argv).command == argv[0]
    capsys.readouterr()
    assert cli.main(["migrate", "--config", cfg]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("migration is automatic")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    head = git(initialised.docs_repo, "rev-parse", "HEAD").strip()
    capsys.readouterr()
    for days in ("0", "-1"):
        assert cli.main(["compact-history", "--keep-days", days, "--config", cfg]) == cli.EXIT_FAILED
        assert "keep_days must be >= 1" in capsys.readouterr().err
    assert git(initialised.docs_repo, "rev-parse", "HEAD").strip() == head  # nothing squashed


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


def test_add_source_writes_a_valid_config_repo_and_scaffold(
    initialised: Config, local_source_dir: Path
) -> None:
    assert initialised.config_path.stat().st_mode & 0o777 == 0o600
    src, box = initialised.sources
    assert src.id == "source" and src.path == local_source_dir and src.is_live
    inbox = initialised.docs_repo.parent / "inbox"  # KISS K05: add-source keeps the inbox
    assert box.id == "inbox" and box.kind is SourceKind.INBOX and box.is_live and box.path == inbox.resolve()
    assert inbox.stat().st_mode & 0o777 == 0o700
    assert (initialised.docs_repo / ".git").is_dir()
    assert (initialised.docs_repo / "README.md").is_file() and (
        initialised.docs_repo / "mirror/CLAUDE.md"
    ).is_file()


def test_init_is_hidden_idempotent_and_takes_no_options(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K14: init stays as a hidden, flagless alias of add-source's setup; it never rewrites a config."""
    cfg = str(initialised.config_path)
    before = initialised.config_path.read_text(encoding="utf-8")
    for flags in (["--source-local", str(tmp_path)], ["--docs-repo", str(tmp_path)], ["--force"]):
        assert cli.main(["init", "--config", cfg, *flags]) == cli.EXIT_USAGE, flags
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_OK  # idempotent re-init
    assert initialised.config_path.read_text(encoding="utf-8") == before
    assert cli.main(["add-source", str(tmp_path), "--id", "x", "--config", cfg]) == cli.EXIT_USAGE  # no --id
    capsys.readouterr()
    assert cli.main(["--help"]) == cli.EXIT_OK
    assert not re.search(r"(?m)^    init\b", capsys.readouterr().out), "init is hidden from help"


def test_sync_twice_status_curate(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
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
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    assert "0 blocking" in capsys.readouterr().out
    assert cli.main(["sync", "--mode", "dry_run", "--config", cfg]) == cli.EXIT_OK  # hidden, still parses
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
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "FRONTMATTER" in out
    # The land gate is invisible to the loop's rules: NEXT names the ERROR, not the baseline step.
    assert (
        out.splitlines()[-1]
        == f"NEXT: 1 blocking finding(s) above: fix every ERROR, then run `{loop.BIN} sync`"
    )
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED  # the land gate blocks the commit


def test_curate_exits_1_on_a_wrong_pin_or_a_typoed_source(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K10: a well-formed but wrong pin (STALE) and a missing source are checkpoint blockers, so curate
    (was lint) fails on them; before, both passed with exit 0."""
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
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "ERROR STALE topics/a.md" in out and "ERROR SOURCE-MISSING topics/b.md" in out
    assert "hand-written" not in out


def _before(config: Config) -> None:
    """A 'before' baseline result: the baseline hold is over, so curate lists rows."""
    results = config.docs_repo / "_eval" / "results-2026-10-04-before.md"
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("1. Finance. (1 search, 1 file)\n", encoding="utf-8")


def _mirror_sha(config: Config, rel: str) -> str:
    text = (config.docs_repo / rel).read_text(encoding="utf-8")
    found = re.search(r"\nrendered_sha256: ([0-9a-f]{64})\n", text)
    assert found is not None
    return found.group(1)


def _row_entry(out: str, word: str, rel: str) -> dict[str, str]:
    """The ready-made sources: entry on ``word``'s row for ``rel``, parsed as YAML."""
    (line,) = [ln for ln in out.splitlines() if ln.startswith(f"{word}\t{rel}\t")]
    entry = yaml.safe_load(line.split("\t")[2])
    assert isinstance(entry, dict)
    return entry


def test_curate_rows_exit_0_and_carry_a_ready_sources_entry(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K09: work is not failure.  UNCOVERED, ADDED and STALE rows exit 0; each ADDED and UNCOVERED row
    carries a sources: entry that, pasted into a page, is lint-clean; the output ends with NEXT."""
    cfg = str(initialised.config_path)
    sample, new = "mirror/source/projects/sample.txt.md", "mirror/source/projects/new.txt.md"
    _before(initialised)
    cli.main(["sync", "--config", cfg])
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    entry = _row_entry(out, "UNCOVERED", sample)
    pin = _mirror_sha(initialised, sample)
    assert entry == {"path": sample, "at_rendered_sha256": pin, "role": "primary"}
    assert "0 finding(s), 0 blocking" in out
    assert out.splitlines()[-1] == loop.next_step(initialised).lines()[0]

    line = next(ln for ln in out.splitlines() if ln.startswith(f"UNCOVERED\t{sample}\t"))
    page = initialised.docs_repo / "topics" / "a.md"
    page.write_text(
        f"---\nentity: acme\npurpose: Terms; not pricing.\nsources:\n  - {line.split(chr(9))[2]}\n---\n# A\n",
        encoding="utf-8",
    )
    (local_source_dir / "projects" / "new.txt").write_text("new\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "checkpoint advanced" in capsys.readouterr().out
    (local_source_dir / "projects" / "sample.txt").write_text("edited\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # a.md gets a STALE banner, no blocker
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert _row_entry(out, "ADDED", new)["at_rendered_sha256"] == _mirror_sha(initialised, new)
    assert f"CHANGED\t{sample}\n" in out
    assert f"STALE\ttopics/a.md\t{sample}" in out and "ERROR" not in out
    assert out.index("ADDED\t") < out.index("STALE\t") < out.index("UNCOVERED\t")
    assert out.splitlines()[-1].startswith("NEXT: ")


def test_curate_runs_every_whole_repo_lint_and_exits_1_only_on_a_blocking_finding(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _synced(initialised)
    _before(initialised)
    ran: list[str] = []
    names = (
        "lint_no_symlinks", "lint_mirror_frontmatter", "lint_paths", "lint_no_cache_in_git", "lint_no_tokens",
        "lint_index_budget",
    )  # fmt: skip
    for name in names:
        real = getattr(lints, name)
        monkeypatch.setattr(lints, name, lambda repo, _n=name, _f=real: (ran.append(_n), _f(repo))[1])
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK  # an UNCOVERED row only
    assert ran == list(names)
    assert "UNCOVERED\t" in capsys.readouterr().out
    (initialised.docs_repo / "topics" / "orders.md").write_text(
        "---\nentity: orders\npurpose: who approves orders\nsources: []\n---\nbody\n", encoding="utf-8"
    )
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "ERROR UNLISTED topics/orders.md" in out and "1 blocking" in out
    fix = f"NEXT: 1 blocking finding(s) above: fix every ERROR, then run `{loop.BIN} sync`"
    assert out.splitlines()[-1] == fix
    # Committed by hand, nothing is pending or dirty, so rule 7 counts 0: NEXT still names the ERROR, never
    # the queued rows or "session done".
    git(initialised.docs_repo, "add", "topics/orders.md")
    git(initialised.docs_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "by hand")
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "ERROR UNLISTED topics/orders.md" in out and out.splitlines()[-1] == fix
    assert "queued" not in out and "session done" not in out


def test_curate_baseline_hold_lists_no_rows(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    """No curated page and no 'before' results: no rows, the baseline NEXT, exit 0."""
    cfg = _synced(initialised)
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "UNCOVERED" not in out and "refresh-queue row(s)" not in out and "checkpoint" not in out
    assert "no curation rows yet" in out
    step = loop.next_step(initialised)
    assert step.rule == 4 and out.splitlines()[-1] == step.lines()[0]


@pytest.mark.parametrize("old", ["curate-queue", "lint", "refresh-queue"])
def test_curate_old_names_are_hidden_aliases(
    old: str, initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _synced(initialised)
    _before(initialised)
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    want = capsys.readouterr()
    assert cli.main([old, "--config", cfg]) == cli.EXIT_OK
    got = capsys.readouterr()
    assert got.out == want.out and "UNCOVERED\t" in got.out
    assert got.err == want.err + "renamed: run agentsync curate\n"
    assert cli.main(["--help"]) == cli.EXIT_OK
    assert f"    {old} " not in capsys.readouterr().out


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
    _before(initialised)  # past the baseline hold, so curate lists rows
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "checkpoint" not in capsys.readouterr().out
    assert _curated(repo) is None  # the first-ever sync creates no tag
    cli.main(["curate", "--config", cfg])
    assert "no build-session checkpoint yet" in capsys.readouterr().out
    first = git(repo, "rev-parse", "HEAD").strip()

    _topic(initialised, "topics/a.md")
    (local_source_dir / "projects" / "new.txt").write_text("new\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert f"checkpoint advanced: curated at {first[:12]}" in capsys.readouterr().out
    assert _curated(repo) == first  # the pre-run HEAD, not the new commit
    assert git(repo, "cat-file", "-t", "curated").strip() == "tag"  # annotated: dated when the session ended
    assert git(repo, "ls-files", "--", "topics/a.md").strip() == "topics/a.md"
    cli.main(["curate", "--config", cfg])
    out = capsys.readouterr().out
    assert "ADDED\tmirror/source/projects/new.txt.md\t{path: " in out
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
    cli.main(["curate", "--config", cfg])
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
    assert "run `agentsync add-source <folder>` to create it" in capsys.readouterr().err  # KISS K14
    assert cli.main(["status", "--config", str(bad)]) == cli.EXIT_CONFIG
    assert "configuration error" in capsys.readouterr().err


def test_unknown_source_and_graph_without_client_id_exit_78(initialised: Config) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["reconcile", "--config", cfg, "--source", "nope"]) == cli.EXIT_CONFIG
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
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
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


def test_migrate_is_a_no_op_and_the_next_sync_migrates(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hidden `migrate` (install.sh ran it before KISS K14) is a no-op since KISS K13b: it opens nothing,
    and the next sync migrates the manifest with its pre-v<N> copy."""
    cfg = str(initialised.config_path)
    db = initialised.state_paths.db
    backup = db.with_name(f"manifest.sqlite.pre-v{MANIFEST_SCHEMA_VERSION}")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    _sql(db, "UPDATE meta SET value = '0' WHERE key = 'key_schema_version'")
    capsys.readouterr()
    assert cli.main(["migrate", "--config", cfg]) == cli.EXIT_OK
    assert "migration is automatic" in capsys.readouterr().out
    assert not backup.exists()
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'key_schema_version'").fetchone()[0] == "0"
    finally:
        conn.close()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert backup.is_file()


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


def test_reconcile_accept_deletions_needs_a_source(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["reconcile", "--accept-deletions", "--config", str(initialised.config_path)]) == 2
    assert "agentsync accept-deletions SOURCE" in capsys.readouterr().err


def test_accept_deletions_clears_a_tripped_breaker(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K13a: the breaker alarm names ``accept-deletions SOURCE``, which clears the breaker and applies
    the held removals; ``reconcile`` is a hidden alias that still parses."""
    cfg_path = initialised.config_path
    text = cfg_path.read_text(encoding="utf-8")
    if "\n[breaker]" in text:  # the init template carries the defaults: lower the floor so 5 deletions trip
        assert "\nfloor = 25\n" in text
        text = text.replace("\nfloor = 25\n", "\nfloor = 2\n", 1)
    else:
        text += "\n[breaker]\nfraction = 0.2\nfloor = 2\nhold_days = 7\n"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = str(cfg_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    victims = ["sample.csv", "sample.txt", "sample.html", "sample.md", "sample.pdf"]
    for name in victims:
        (local_source_dir / "projects" / name).unlink()
    capsys.readouterr()
    assert cli.main(["sync", "--mode", "reconcile", "--config", cfg]) == cli.EXIT_OK
    tripped = capsys.readouterr().out
    assert "BREAKER TRIPPED" in tripped and "run `agentsync accept-deletions source`" in tripped
    assert "--accept-deletions" not in tripped
    repo = initialised.docs_repo
    assert (repo / "mirror/source/projects/sample.csv.md").is_file()
    with Manifest(initialised.state_paths.db) as m:
        assert m.breaker_active("source", datetime.now(UTC).isoformat())
    assert cli.main(["accept-deletions", "source", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "mode reconcile" in out and f"{len(victims)} change(s)" in out and "BREAKER" not in out
    with Manifest(initialised.state_paths.db) as m:
        assert not m.breaker_active("source", datetime.now(UTC).isoformat())
        row = m.get_source("source")
        assert row is not None and row.breaker_tripped_at is None
    assert cli.main(["accept-deletions", "no-such-source", "--config", cfg]) == cli.EXIT_CONFIG
    assert cli.main(["accept-deletions", "--config", cfg]) == cli.EXIT_USAGE
    capsys.readouterr()
    assert cli.main(["--help"]) == cli.EXIT_OK
    listing = capsys.readouterr().out
    assert "accept-deletions" in listing and "\n    reconcile " not in listing


def test_accept_deletions_waits_for_a_running_sync(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """launchd never re-runs an operator's accept-deletions, so it waits for the lock like interactive sync
    instead of exiting 75 'skipped: lock held'."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    lock = SingleWriterLock(initialised.state_paths.lock, "poll")
    lock.acquire()
    timer = threading.Timer(2.0, lock.release)
    timer.start()
    try:
        assert cli.main(["accept-deletions", "source", "--config", cfg]) == cli.EXIT_OK
    finally:
        timer.join()
        lock.release()
    assert "waiting for it to finish" in capsys.readouterr().err


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
    for flag in (["--interval", "120"], ["--reconcile-interval", "60"], ["--no-backup-exclusions"]):
        assert cli.main(["install-agent", *flag, "--config", cfg]) == cli.EXIT_USAGE  # KISS K11a: deleted
    assert installed == [] and excluded == []
    capsys.readouterr()
    assert cli.main(["install-agent", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "time machine: excluded x" in out and len(excluded) == 1  # C15 req 42, always, once at install
    assert f"launchd runs: {installed[0].program_arguments[0]}" in out
    assert [(s.label, s.start_interval_s) for s in installed] == [  # the intervals come from sources.toml
        ("com.agentsync.poll", 300),
        ("com.agentsync.reconcile", 3600),
    ]
    assert installed[0].materialize_dataless_files is False
    assert installed[0].program_arguments[-4:] == ("--mode", "poll", "--config", cfg)
    assert cli.main(["uninstall-agent", "--config", cfg]) == cli.EXIT_OK
    assert removed == ["com.agentsync.poll", "com.agentsync.reconcile"]


def test_doctor_runs(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    """``doctor`` is a hidden alias of ``status`` (install.sh still calls it until W5)."""
    rc = cli.main(["doctor", "--config", str(initialised.config_path)])
    cap = capsys.readouterr()
    assert "[ok  ] git" in cap.out and "launchd.poll" in cap.out
    assert cap.out.startswith("NEXT: ") and "renamed: run agentsync status" in cap.err
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
    text = path.read_text(encoding="utf-8")  # the template has no [graph] since KISS K15: append one
    text += (
        '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000001"\ntenant = "contoso.onmicrosoft.com"\n'
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


def test_graph_company_is_ignored_and_status_names_its_line(
    initialised: Config,
    fake_auth: type[FakeAuth],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """KISS K15: an old config's ``[graph] company`` still loads, never reaches the User-Agent, and status
    prints one WARN naming the line to delete."""
    cfg = _graph_config(initialised)
    path = Path(cfg)
    path.write_text(path.read_text(encoding="utf-8") + 'company = "Acme Corp"\n', encoding="utf-8")
    line = path.read_text(encoding="utf-8").splitlines().index('company = "Acme Corp"') + 1
    agents: list[str] = []

    def fake_discover(client: Any, *, known: Any = ()) -> discover.DiscoveryReport:
        agents.append(client._http.headers["User-Agent"])
        return discover.DiscoveryReport(())

    monkeypatch.setattr(discover, "discover_sources", fake_discover)
    cli.main(["discover", "--config", cfg])
    assert agents == [f"NONISV|agentsync|agentsync/{__version__}"]
    capsys.readouterr()
    cli.main(["status", "--config", cfg])
    warns = [ln for ln in capsys.readouterr().out.splitlines() if "config.graph_company" in ln]
    assert len(warns) == 1 and warns[0].startswith("[warn]")
    assert f"(fix: delete line {line} of {path})" in warns[0]
    path.write_text(path.read_text(encoding="utf-8").replace('company = "Acme Corp"\n', ""), encoding="utf-8")
    cli.main(["status", "--config", cfg])
    assert "config.graph_company" not in capsys.readouterr().out


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


def test_purge_queue_dry_run_previews_and_writes_nothing(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field report 2026-10-05: `purge --queue --dry-run` executed the queued purges."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    assert governance.enqueue_purge(initialised.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    head = git(repo, "rev-parse", "HEAD").strip()
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "dry run" in out and "1 queued purge(s) previewed" in out and "; 1 queued" in out
    assert git(repo, "rev-parse", "HEAD").strip() == head and (repo / page).is_file()
    assert len(governance.pending_purges(initialised.state_paths.root)) == 1


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
    cli.main(["curate", "--config", cfg])
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
    assert cli.main(["compact-history", "--keep-days", "1", "--config", cfg]) == cli.EXIT_FAILED
    assert "hold" in capsys.readouterr().err
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


def test_status_shows_the_policy_and_a_broken_policy_fails(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K08a: status prints the effective policy; ``policy show`` is a hidden alias of status; a broken
    policy is a FAIL there (rc 1) and still a configuration error for sync."""
    cfg = str(initialised.config_path)
    guid = "2096f6a2-d2f7-48be-b329-b73aaa526e5d"
    with initialised.config_path.open("a", encoding="utf-8") as fh:
        fh.write(f'\n[policy]\nexclude_label_ids = ["{guid.upper()}"]\n')
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"  exclude_label_ids: {guid}" in out and "  labels_active: true" in out
    assert cli.main(["policy", "show", "--config", cfg]) == cli.EXIT_OK
    cap = capsys.readouterr()
    assert cap.out == out and "renamed: run agentsync status" in cap.err
    (initialised.config_path.parent / "policy.toml").write_text("[policy]\nnope = 1\n", encoding="utf-8")
    assert cli.main(["policy", "show", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "[FAIL] policy " in out and "policy: invalid (see the [FAIL] policy line)" in out
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
    folder = initialised.sources[0].path
    assert folder is not None
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_FAILED  # the same refusal


def test_doctor_reports_network_broker_governance_and_policy(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    probes: list[str] = []

    def fake_probe(url: str, settings: net.ProxySettings, **_k: Any) -> net.Reachability:
        probes.append(url)
        return net.Reachability(net.POLICY_TLS, "certificate verify failed", "direct")

    monkeypatch.setattr(net, "probe_reachability", fake_probe)
    cfg = str(initialised.config_path)
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    assert probes == [], "no live Graph source: no probe"
    assert cli.main(["doctor", "--network", "--config", cfg]) == cli.EXIT_USAGE, "--network is deleted"
    capsys.readouterr()
    text = initialised.config_path.read_text(encoding="utf-8")
    initialised.config_path.write_text(
        text
        + '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000001"\n'
        + '\n[[source]]\nid = "mail"\nkind = "graph_mail"\nfolder = "inbox"\n',
        encoding="utf-8",
    )
    rc = cli.main(["status", "--config", cfg])
    out = capsys.readouterr().out
    assert "network.proxy" in out and "governance.remote" in out and "] policy" in out
    assert "failed: network-policy: TLS" in out and rc == cli.EXIT_FAILED  # TLS is failed, never skipped
    assert probes == [initialised.graph.base_url], "a live Graph source: the probe runs by itself"
    git(initialised.docs_repo, "remote", "add", "origin", "https://github.com/someone/docs.git")
    cli.main(["status", "--config", cfg])
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


# ---- status: the single read-only check (KISS K08a) --------------------------------------------------------


def _status(cfg: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    rc = cli.main(["status", "--config", cfg])
    return rc, capsys.readouterr().out.splitlines()


def test_status_starts_with_next_then_the_loop_line_then_the_checks(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _synced(initialised)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    want = loop.next_step(initialised).lines()
    assert rc == cli.EXIT_OK and want[0].startswith("NEXT: ") and out[: len(want)] == want
    assert out[len(want)].startswith("loop: skill current · inbox on · baseline missing · topics 0 · ")
    assert re.match(r"\[(ok  |info|warn|FAIL)\] ", out[len(want) + 1]), "then the checks"
    assert "  source (local, live): baseline complete" in "\n".join(out) and "  labels_active: false" in out


def test_status_fail_lines_are_doctors_byte_for_byte_and_exit_1(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _synced(initialised)
    capsys.readouterr()
    fake = [
        doctor.CheckResult("python", True, "3.11.9"),
        doctor.CheckResult("fake.check", False, "broken (twice)", doctor.Severity.ERROR, fix="do the thing"),
        doctor.CheckResult("fake.warn", False, "degraded", doctor.Severity.WARN, fix="later"),
    ]
    monkeypatch.setattr(doctor, "run_checks", lambda config, *, tcc_canary=True: list(fake))
    monkeypatch.setattr(cli, "_extra_checks", lambda config, *, offline=False: [])
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED
    assert [ln for ln in out if ln.startswith("[")] == doctor.format_results(fake).splitlines()
    assert out[0] == "NEXT: the fake.check check failed: do what the fix on its [FAIL] line below says"
    monkeypatch.setattr(doctor, "run_checks", lambda config, *, tcc_canary=True: [fake[0], fake[2]])
    assert _status(cfg, capsys)[0] == cli.EXIT_OK, "a warn alone is not a failure"


def test_status_next_never_copies_a_fail_fix(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fix may name a mirror path, a file or a bare ``agentsync`` (CONTRACTS §16.20): NEXT points at it."""
    cfg = _synced(initialised)
    capsys.readouterr()
    sentinel = "restore /Users/me/Library/CloudStorage/OneDrive-Org/Folder/.agentsync-sentinel"
    fake = [
        doctor.CheckResult("source.x.sentinel", False, "gone", doctor.Severity.ERROR, fix=sentinel),
        doctor.CheckResult("graph.auth", False, "REAUTH", doctor.Severity.ERROR, fix="agentsync login"),
    ]
    monkeypatch.setattr(doctor, "run_checks", lambda config, *, tcc_canary=True: list(fake))
    rc, out = _status(cfg, capsys)
    nexts = [ln for ln in out if ln.startswith(("NEXT:", "WAITING ON YOU:"))]
    assert rc == cli.EXIT_FAILED and nexts[0].startswith("NEXT: the source.x.sentinel check failed: ")
    assert not any("/" in ln or re.search(r"(^|[\s`])agentsync ", ln) for ln in nexts), nexts
    assert any(ln.endswith(f"(fix: {sentinel})") for ln in out), "the [FAIL] line still carries the fix"


@pytest.mark.parametrize("breakage", ["governance", "schema"])
def test_status_prints_every_check_when_its_state_cannot_be_read(
    breakage: str, initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken [governance] or a newer-schema manifest is a FAIL line, never an empty status."""
    cfg = _synced(initialised)
    if breakage == "governance":
        path = initialised.config_path
        path.write_text(path.read_text(encoding="utf-8") + '\n[governance]\nhistory_days = "x"\n', "utf-8")
    else:
        with sqlite3.connect(initialised.state_paths.db) as conn:
            conn.execute("UPDATE meta SET value = '999' WHERE key = 'manifest_schema_version'")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    checks = [ln for ln in out if re.match(r"\[(ok  |info|warn|FAIL)\] ", ln)]
    assert len(checks) > 15 and any(ln.startswith("loop: ") for ln in out), out
    if breakage == "governance":
        assert rc == cli.EXIT_FAILED and any(ln.startswith("[FAIL] governance.config") for ln in checks)
        assert any(ln.startswith("status: cannot read the state: ConfigError") for ln in out)
    monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, "1")
    assert cli.main(["doctor", "--config", cfg]) == rc
    alias = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[")]
    assert [ln.split(" — ")[0] for ln in alias] == [ln.split(" — ")[0] for ln in checks], "ages may tick"


def test_the_doctor_alias_prints_no_status_or_policy_detail(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """install.sh copies doctor's output into install.out, whose tail setup-report embeds: no label names."""
    cfg = _synced(initialised)
    capsys.readouterr()
    monkeypatch.setattr(governance, "pending_purges", lambda root: ["queued"])
    _status(cfg, capsys)
    for argv, env in ((["doctor"], None), (["doctor"], "1"), (["status"], "1")):
        if env is None:
            monkeypatch.delenv(loop.NO_NEXT_HINT_ENV, raising=False)
        else:
            monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, env)
        cli.main([*argv, "--config", cfg])
        out = capsys.readouterr().out
        assert "exclude_label_names" not in out and "(run " not in out, (argv, env)
        assert "[ok  ] " in out and "loop: skill " in out
    monkeypatch.delenv(loop.NO_NEXT_HINT_ENV, raising=False)
    cli.main(["policy", "show", "--config", cfg])
    out = capsys.readouterr().out
    assert "  exclude_label_names: -" in out and "queued purges: 1 (run `~/.local/bin/agentsync purge" in out


def test_a_failed_skill_write_is_a_fail(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_OK and any(
        re.match(r"\[info\] skill +— missing: the next sync writes it", ln) for ln in out
    )
    _synced(initialised)
    (path,) = skill.skill_paths()
    path.write_text("an older skill\n", encoding="utf-8")
    path.parent.chmod(0o500)
    try:
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, "a skill write failure never fails a sync"
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        assert rc == cli.EXIT_FAILED
        assert out[0].startswith("NEXT: the agentsync-docs skill is missing or out of date")
        (fail,) = [ln for ln in out if ln.startswith("[FAIL] skill ")]
        assert "stale although a sync ran with this build" in fail and fail.endswith(
            "(fix: make that folder writable, then run `~/.local/bin/agentsync sync`)"
        )
        later = datetime.now(UTC) + timedelta(hours=1)
        monkeypatch.setattr(cli, "_build_installed_at", lambda: later)
        rc, out = _status(cfg, capsys)
        assert rc == cli.EXIT_OK, "a build installed after the last sync: that sync could not write it"
        assert any(re.match(r"\[info\] skill +— stale: the next sync writes it", ln) for ln in out)
    finally:
        path.parent.chmod(0o700)


def test_a_skill_copy_no_sync_wrote_is_never_a_fail(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Synced without CLAUDE_CONFIG_DIR, checked with it set: that copy is the next sync's to write."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    cfg = _synced(initialised)
    capsys.readouterr()
    extra = tmp_path / "other-config"
    extra.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(extra))
    monkeypatch.setattr(cli, "_build_installed_at", lambda: datetime.now(UTC) - timedelta(days=1))
    rc, out = _status(cfg, capsys)
    (line,) = [ln for ln in out if " skill " in ln and ln.startswith("[")]
    assert rc == cli.EXIT_OK and line.startswith("[info] skill "), line
    assert f"missing {extra / 'skills' / skill.SKILL_NAME / 'SKILL.md'}" in line
    assert str(skill.skill_paths()[0]) not in line, "only the copy that is off is named"


def test_status_with_no_folder_source_is_a_next_line_and_exit_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    skill.write_skill(load_config(cfg).docs_repo)
    init_out = capsys.readouterr().out.splitlines()
    assert init_out[-1] == "sources: none yet besides the inbox (run agentsync add-source <folder>)"
    rc, out = _status(str(cfg), capsys)
    assert rc == cli.EXIT_OK and not any(ln.startswith("[FAIL]") for ln in out)
    assert out[0].startswith("NEXT: no folder is synced yet: ") and "add-source" in out[0]
    assert not [ln for ln in out if "source.inbox" in ln and not ln.startswith("[ok  ]")], out


def test_a_fresh_empty_inbox_raises_no_warning_in_status_or_doctor(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K05: the inbox every config now has is empty until a file is dropped in; after a complete
    sync neither status nor the doctor alias reports it as anything but OK."""
    cfg = _synced(initialised)
    capsys.readouterr()
    for verb in ("status", "doctor"):
        rc = cli.main([verb, "--config", cfg])
        out = capsys.readouterr().out.splitlines()
        (line,) = [ln for ln in out if ln.startswith("[") and " source.inbox.listable " in ln]
        assert line.startswith("[ok  ]") and "is empty" in line, (verb, line)
        assert rc == cli.EXIT_OK, (verb, out)


def test_status_warns_when_the_checkout_moved_past_the_installed_commit(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git" / "refs" / "heads").mkdir(parents=True)
    (checkout / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (checkout / ".git" / "packed-refs").write_text(
        f"# pack-refs\n{'b' * 40} refs/heads/main\n", encoding="utf-8"
    )
    stamp = tmp_path / "stamp"
    stamp.write_text(f"commit={'a' * 12} source={checkout}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_install_stamp", lambda: stamp)
    cfg = str(initialised.config_path)
    rc, out = _status(cfg, capsys)
    (line,) = [ln for ln in out if " install.commit " in ln]
    assert rc == cli.EXIT_OK and line.startswith("[warn] install.commit ")
    assert f"installed from {'a' * 12}, but the checkout at {checkout} is at {'b' * 12}" in line
    assert line.endswith(f"(fix: re-run install.sh ({checkout}/scripts/install.sh))")
    (checkout / ".git" / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    (line,) = [ln for ln in _status(cfg, capsys)[1] if " install.commit " in ln]
    assert line.startswith("[ok  ] install.commit ")
    monkeypatch.setattr(cli, "_install_stamp", lambda: tmp_path / "none")
    assert not [ln for ln in _status(cfg, capsys)[1] if " install.commit " in ln], "no stamp: no check"


def test_status_runs_the_tcc_canary_only_after_a_tcc_event_or_before_any_run(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[bool] = []
    real = doctor.run_checks

    def spy(config: Config, *, tcc_canary: bool = True) -> list[doctor.CheckResult]:
        seen.append(tcc_canary)
        return real(config, tcc_canary=tcc_canary)

    monkeypatch.setattr(doctor, "run_checks", spy)
    cfg = str(initialised.config_path)

    def canary() -> bool:
        _status(cfg, capsys)
        return seen[-1]

    assert canary() is False, "no LaunchAgent installed: nothing runs the launcher (KISS K11a)"
    stray = launchd.plist_path("com.agentsync.old-job")
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"")
    assert canary() is True, "nothing has run since install"
    initialised.log_dir.mkdir(parents=True, exist_ok=True)
    poll = initialised.log_dir / "com.agentsync.poll.err.log"
    poll.write_text(
        "2026-09-29T10:00:00Z agentsync-launcher[1]: CANARY_OK path=/x\n"
        "2026-09-29T10:00:05Z agentsync-launcher[1]: CHILD_EXIT rc=0\n",
        encoding="utf-8",
    )
    assert canary() is False, "the last run got past TCC"
    with poll.open("a", encoding="utf-8") as fh:
        fh.write("2026-09-29T11:00:00Z agentsync-launcher[2]: TCC_PENDING reason=canary path=/y\n")
    assert canary() is True
    (initialised.log_dir / "com.agentsync.reconcile.err.log").write_text(
        "2026-09-29T12:00:00Z agentsync-launcher[3]: CHILD_EXIT rc=0\n", encoding="utf-8"
    )
    assert canary() is False, "the newest event of either job decides"
    plist = launchd.plist_path("com.agentsync.poll")
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(b"")
    assert canary() is True, "installed after every logged event: nothing has run since install"


def test_status_exits_0_for_a_cloud_source_without_launchagents(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K11a: a OneDrive source, no LaunchAgent plist, no launcher and a shell-only HTTPS_PROXY:
    background sync is optional, so ``status`` exits 0, runs no canary and never names install-agent."""
    folder = Path.home() / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    folder.mkdir(parents=True)
    (folder / "a.docx").write_bytes(b"x")
    cfg = str(initialised.config_path)
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_OK
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:8080")
    monkeypatch.setattr(net, "system_proxy", lambda runner=None: net.SystemProxy())
    monkeypatch.setattr(doctor, "_launcher_canary", lambda *a: pytest.fail("the canary ran"))
    config = load_config(initialised.config_path)
    assert launchd.launcher_required(config) and not launchd.agents_installed(config)
    rc, out = _status(cfg, capsys)
    text = "\n".join(out)
    assert rc == cli.EXIT_OK, text
    assert "install-agent" not in text and "network.proxy.job" not in text and "[FAIL]" not in text


def test_a_denied_path_is_not_cleared_by_another_paths_canary_ok(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The launcher logs one canary line per path and keeps going after TCC_DENIED."""
    seen: list[bool] = []
    real = doctor.run_checks

    def spy(config: Config, *, tcc_canary: bool = True) -> list[doctor.CheckResult]:
        seen.append(tcc_canary)
        return real(config, tcc_canary=False)

    monkeypatch.setattr(doctor, "run_checks", spy)
    stray = launchd.plist_path("com.agentsync.old-job")  # background sync installed (KISS K11a)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"")
    initialised.log_dir.mkdir(parents=True, exist_ok=True)
    poll = initialised.log_dir / "com.agentsync.poll.err.log"
    poll.write_text(
        '2026-09-29T09:00:00Z agentsync-launcher[1]: TCC_DENIED path="/b" errno=1\n'
        '2026-09-29T10:00:00Z agentsync-launcher[2]: TCC_DENIED path="/a" errno=1 error="x"\n'
        '2026-09-29T10:00:01Z agentsync-launcher[2]: CANARY_OK path="/b"\n',
        encoding="utf-8",
    )
    _, out = _status(str(initialised.config_path), capsys)
    assert seen[-1] is True
    launcher = [ln for ln in out if ln.startswith("launcher: ")]
    assert len(launcher) == 1 and 'TCC_DENIED path="/a"' in launcher[0], launcher
    with poll.open("a", encoding="utf-8") as fh:
        fh.write('2026-09-29T11:00:00Z agentsync-launcher[3]: CANARY_OK path="/a"\n')
        fh.write('2026-09-29T11:00:01Z agentsync-launcher[3]: CANARY_OK path="/b"\n')
    _, out = _status(str(initialised.config_path), capsys)
    assert seen[-1] is False and not [ln for ln in out if ln.startswith("launcher: ")]


def test_status_honours_no_next_hint(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """install.sh exports AGENTSYNC_NO_NEXT_HINT=1 and still calls ``doctor``: its NEXT stays the only one."""
    cfg = _synced(initialised)
    capsys.readouterr()
    monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, "1")
    assert cli.main(["doctor", "--config", cfg]) == cli.EXIT_OK
    cap = capsys.readouterr()
    assert cap.out.startswith("loop: skill current") and "renamed" not in cap.err
    assert not re.search(r"(?im)^\s*(next|waiting on you|note):", cap.out)


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
    assert "# [governance]" in after and "# sentinel = " in after  # the template's and the table's comments
    config = load_config(initialised.config_path)
    added = config.source("fy26-projects")
    assert added.kind is SourceKind.LOCAL and added.is_live and added.path == folder.resolve()
    assert added.exclude == initialised.sources[0].exclude  # the same defaults as the first folder's
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


def test_init_and_add_source_keep_exactly_one_inbox(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K05: init and add-source add the inbox to a config that has none, once; there is no --inbox."""
    cfg = str(initialised.config_path)
    box = initialised.source("inbox")
    assert box.path is not None
    old = initialised.config_path.read_text(encoding="utf-8").removesuffix(
        inbox_source_table("inbox", box.path)
    )
    folder = tmp_path / "Notes"
    folder.mkdir()
    for argv in (["add-source", str(folder)], ["init"]):
        initialised.config_path.write_text(old, encoding="utf-8")  # a config from before K05
        assert load_config(initialised.config_path).sources == initialised.sources[:1]
        for _ in range(2):
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert out.count("added inbox source 'inbox'") == 1, argv
        kinds = [s.kind for s in load_config(initialised.config_path).sources]
        assert kinds.count(SourceKind.INBOX) == 1, argv
        assert kinds.count(SourceKind.LOCAL) == len(argv), argv  # add-source added its folder once
        ids = ", ".join(s.id for s in load_config(initialised.config_path).sources)
        assert out.rstrip("\n").splitlines()[-1] == f"sources: {ids}", argv  # a folder: no "none yet" hint
    assert cli.main(["add-source", "--inbox", "--config", cfg]) == cli.EXIT_USAGE  # deleted
    assert cli.main(["add-source", "--config", cfg]) == cli.EXIT_USAGE  # PATH is required


def test_add_source_on_the_inbox_folder_of_an_inbox_less_config_keeps_it_the_inbox(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K05 after K14: the inbox is ensured before the PATH lookup, so add-source on the inbox folder of a
    config from before K05 adds it as kind "inbox", never as a local folder install.sh would count."""
    box = initialised.source("inbox")
    assert box.path is not None and box.path.is_dir()
    cfg = initialised.config_path
    cfg.write_text(cfg.read_text(encoding="utf-8").removesuffix(inbox_source_table("inbox", box.path)))
    assert cli.main(["add-source", str(box.path), "--config", str(cfg)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "added inbox source 'inbox'" in out and "already configured: source 'inbox' (inbox, live)" in out
    on_box = [s for s in load_config(cfg).sources if s.path == box.path]
    assert [s.kind for s in on_box] == [SourceKind.INBOX]
    fresh = initialised.config_path.parent.parent / "fresh" / "sources.toml"  # no sources.toml yet
    assert cli.main(["add-source", str(box.path), "--config", str(fresh)]) == cli.EXIT_OK
    assert [(s.kind, s.path) for s in load_config(fresh).sources] == [(SourceKind.INBOX, box.path)]
    assert "sources: none yet besides the inbox" in capsys.readouterr().out


def test_add_source_warns_when_the_inbox_cannot_be_added(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = initialised.config_path
    box = initialised.source("inbox")
    assert box.path is not None
    cfg.write_text(cfg.read_text(encoding="utf-8").removesuffix(inbox_source_table("inbox", box.path)))
    box.path.rmdir()
    box.path.write_text("a file, not a folder", encoding="utf-8")
    folder = tmp_path / "Notes"
    folder.mkdir()
    assert cli.main(["add-source", str(folder), "--config", str(cfg)]) == cli.EXIT_OK
    cap = capsys.readouterr()
    assert "inbox: not added: " in cap.err and "added source 'notes'" in cap.out
    assert [s.id for s in load_config(cfg).sources] == ["source", "notes"]


def test_add_source_derives_a_unique_id(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    other = tmp_path / "elsewhere" / "source"  # same folder name as the initialised source ("source")
    other.mkdir(parents=True)
    assert cli.main(["add-source", str(other), "--config", cfg]) == cli.EXIT_OK
    assert [s.id for s in load_config(initialised.config_path).sources] == ["source", "inbox", "source-2"]
    third = tmp_path / "Team Notes"
    third.mkdir()
    assert cli.main(["add-source", str(third), "--id", "team", "--config", cfg]) == cli.EXIT_USAGE  # no --id
    assert cli.main(["add-source", str(third), "--config", cfg]) == cli.EXIT_OK
    assert [s.id for s in load_config(initialised.config_path).sources] == [
        "source",
        "inbox",
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
    # A bad PATH writes no new sources.toml either; a broken one is a config error (78), left as it is.
    missing = tmp_path / "none" / "sources.toml"
    assert cli.main(["add-source", str(tmp_path / "no-such"), "--config", str(missing)]) == cli.EXIT_USAGE
    assert not missing.parent.exists()
    broken = tmp_path / "broken.toml"
    broken.write_text("[agentsync]\nnope = 1\n", encoding="utf-8")
    assert cli.main(["add-source", str(tmp_path), "--config", str(broken)]) == cli.EXIT_CONFIG
    assert broken.read_text(encoding="utf-8") == "[agentsync]\nnope = 1\n"
    capsys.readouterr()


def test_add_source_on_a_fresh_home_creates_everything_once(
    local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K14: add-source is the one setup verb: on a fresh HOME it writes sources.toml and creates the docs
    repo, its scaffold, the inbox (0700) and the state dir; a second run changes nothing."""
    ctx = Path.home() / "agent-context"
    for _ in range(2):
        assert cli.main(["add-source", str(local_source_dir)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.count("wrote ") == 1 and out.count("added source 'source'") == 1
    assert "already configured: source 'source'" in out
    cfg = ctx / "sources.toml"
    assert cfg.stat().st_mode & 0o777 == 0o600
    config = load_config(cfg)
    assert [(s.id, s.kind) for s in config.sources] == [
        ("source", SourceKind.LOCAL),
        ("inbox", SourceKind.INBOX),
    ]
    assert cfg.read_text(encoding="utf-8").count("\n[[source]]\n") == 2
    assert (ctx / "inbox").stat().st_mode & 0o777 == 0o700
    assert config.docs_repo == ctx / "docs" and (config.docs_repo / ".git").is_dir()
    assert (config.docs_repo / "README.md").is_file() and (config.docs_repo / "mirror/CLAUDE.md").is_file()
    assert config.state_dir.is_dir() and config.state_paths.db.is_file()


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
    lines = out.rstrip("\n").splitlines()
    assert lines[-3:-1] == [
        lines[-3],
        "NEXT: draft the baseline questions: follow step 1 (Draft) of the agentsync-docs skill's \"Baseline "
        'questions" section, then run `~/.local/bin/agentsync sync`',
    ], "the online-only deferrals do not hold the loop at 'sync again'"
    m = re.fullmatch(r"converted (\d+), deferred 2 online-only", lines[-3])
    assert m and int(m.group(1)) >= 8, lines[-3]
    assert lines[-1] == (
        "note: 2 online-only file(s) in source wait for a later sync's download budget; they do not block "
        "the next step"
    )
    assert not re.search(r"(?m)^\s*(next|run|fix):", out)
    assert (mirror / "sample.docx.md").is_file(), "a local file is converted by the budget-0 run"
    assert not (mirror / "sample.pdf.md").exists(), "an online-only file is not downloaded"
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert (mirror / "sample.pdf.md").is_file() and "left for a later run" not in out
    assert out.rstrip("\n").splitlines()[-2] == "converted 2, deferred 0 online-only"
    assert cli.main(["sync", "--materialise-budget", "lots", "--config", cfg]) == cli.EXIT_CONFIG
    parser = cli.build_parser()
    sync = parser.parse_args(["sync", "--materialise-budget", "200MB"])
    assert sync.materialise_budget == "200MB"


def test_sync_without_mode_ends_with_the_summary_then_one_next_line(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K01: an interactive sync (no --mode) ends with its summary line and then exactly one NEXT line
    from loop.next_step; a LaunchAgent's explicit --mode, and install.sh's AGENTSYNC_NO_NEXT_HINT=1, print
    none."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert re.fullmatch(r"converted \d+, deferred 0 online-only", lines[-2]), lines[-2]
    assert lines[-1] == "NEXT: " + loop.next_step(load_config(initialised.config_path)).step
    assert sum(line.startswith("NEXT:") for line in lines) == 1
    assert not any(line.startswith(("WAITING ON YOU:", "note:")) for line in lines)
    assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_OK
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert re.fullmatch(r"converted \d+, deferred 0 online-only", lines[-1]), lines[-1]
    assert not any(line.startswith("NEXT:") for line in lines)
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert re.fullmatch(r"converted \d+, deferred 0 online-only", lines[-1]), lines[-1]
    assert not any(line.startswith("NEXT:") for line in lines)


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
    report = ["setup-report", "--out", str(tmp_path / "r.md"), "--config", str(cfg)]
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
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
    assert cli.main(["init", "--config", str(cfg)]) == cli.EXIT_OK
    assert cli.main(["add-source", str(third), "--config", str(cfg)]) == cli.EXIT_OK
    assert cli.main(report) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert not re.search(r"(?im)^\s*next:", out), "KISS K01: the static hints are gone (sync says NEXT)"
