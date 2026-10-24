"""The ``agentsync`` CLI: parsing, exit codes, and each subcommand against tmp dirs (HOME is isolated)."""

from __future__ import annotations

import dataclasses
import errno
import functools
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from agentsync import (
    __version__,
    arm_local,
    cli,
    curate,
    gitops,
    governance,
    it_request,
    lints,
    loop,
    net,
    paths,
    policy,
    setup_report,
    skill,
)
from agentsync import cycle as cycle_mod
from agentsync import manifest as manifest_mod
from agentsync.config import RETIRE_ADVICE, Config, canonical_source_root, inbox_source_table, load_config
from agentsync.cycle import HYDRATION_REFUSED, RECORDING_WAITS, run_cycle
from agentsync.errors import (
    AuthError,
    GitError,
    LockHeldError,
    LockNotAFileError,
    ManifestOpenError,
    ManifestPathError,
)
from agentsync.graph import auth as graph_auth
from agentsync.graph import discover
from agentsync.graph.auth import AuthStatus
from agentsync.graph.drive import DiscoveredScope
from agentsync.graph.errors import AuthBlockedError
from agentsync.manifest import LOST_MANIFEST, MANIFEST_SCHEMA_VERSION, Manifest
from agentsync.model import (
    CycleMode,
    CycleReport,
    LintFinding,
    PassKind,
    RowState,
    SourceKind,
    SourceReport,
    Verdict,
)
from agentsync.ops import doctor, launchd
from agentsync.ops import lock as lock_mod
from agentsync.ops.lock import EMPTY_BODY_HOLDER, SingleWriterLock, read_heartbeat, write_heartbeat
from agentsync.paths import expand
from agentsync.text import plural
from conftest import zone


def test_a_new_file_is_counted_once_under_its_final_verdict(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 11 M48: phase 1 counted a new file as created and phase 2 as changed, so one new file read
    ``changed=1, created=1`` beside ``1 change``. A file a later phase settles counts once, under its
    final verdict; one phase 1 settles (unchanged) keeps its count."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    (local_source_dir / "projects" / "New Memo.txt").write_text("A new memo for Project Alpha.\n")
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    (line,) = [x for x in out if x.startswith("  source: ")]
    assert "changed=1 (1 new)" in line and "created=" not in line and "unchanged=" in line, line
    assert any(" · 1 change" in x for x in out), out
    # B13 round 12 N39: the count says the file is new; an edited file is changed and not new
    (local_source_dir / "projects" / "New Memo.txt").write_text("An edited memo for Project Alpha.\n")
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source: ")]
    assert re.search(r"\bchanged=1\b", line) and "new" not in line, line


def test_a_new_file_publish_quarantines_reads_quarantined_and_not_changed(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 12 N30, N50: a new file settled CHANGED and then quarantined by ``_publish`` (here a
    credential re-stub) read ``changed=1, quarantined=1`` beside ``1 change``: one file counted twice."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    first = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source: ")]
    (local_source_dir / "projects" / "keys.txt").write_text("aws key AKIA" + "ABCDEFGHIJKLMNOP\n")
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source: ")]
    assert "quarantined=1" in line and not re.search(r"\bchanged=", line), line

    def total(text: str) -> int:
        return sum(int(m) for m in re.findall(r"\b[a-z_]+=(\d+)", text.rsplit(" · ", 1)[-1]))

    assert total(line) == total(first[0]) + 1, (first, line)


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
    for code in ("0 ", "1 ", "2 ", "65", "75", "77", "78", "79"):
        assert f"\n  {code}" in out
    for command in ("add-source", "sync", "status", "accept-deletions", "purge", "hold", "offboard"):
        assert command in out
    assert "install-agent" not in out and "uninstall-agent" not in out  # KISS K11a: hidden, still parse
    assert cli.main(["sync", "--help"]) == 0
    assert "75  skipped" in capsys.readouterr().out


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_curates_help_says_it_exits_1_on_a_state_file_this_account_cannot_open(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 21 Y75: curate's help said "rc 1 only on a blocking finding", while the exit-code list on the
    same screen says 1 is also a state file this account cannot open, which curate exits 1 on."""
    assert cli.main(["--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())  # argparse wraps help text
    assert "only on a blocking finding" not in out
    # the words are lane D's Y76 (one defect, built once there): its test pins them
    assert "rc 1 on a blocking finding or a state file this account cannot open, never on rows" in out
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    db.chmod(0o400)
    try:
        assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    finally:
        db.chmod(0o600)


def test_help_text_names_every_command_to_run_by_its_full_path(capsys: pytest.CaptureFixture[str]) -> None:
    """B13 round 6 G79: F95 named every printed command by ~/.local/bin/agentsync, which works with that
    folder off PATH, but left the --help epilog, the hidden commands' help and the sources.toml header bare;
    a renamed alias's help said "run agentsync status" while its stderr gave the full path."""
    from agentsync.config import default_config_text  # noqa: PLC0415

    bin_ = "~/.local/bin/agentsync"
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert f"`{bin_} graph login`" in out and f"`{bin_} status`" in out
    assert "`agentsync " not in out
    for argv, said in (
        (["doctor", "--help"], f"renamed: run {bin_} status"),
        (["policy", "--help"], f"renamed: run {bin_} status"),
        (["curate-queue", "--help"], f"renamed: run {bin_} curate"),
        (["reconcile", "--help"], f"old spelling of {bin_} accept-deletions SOURCE"),
        (["reconcile", "--help"], f"(`{bin_} sync` runs one when due)"),
    ):
        assert cli.main(argv) == 0, argv
        assert said in " ".join(capsys.readouterr().out.split()), argv  # argparse wraps help text
    first = default_config_text().splitlines()[0]
    assert first.startswith("# agentsync sources.toml") and f"`{bin_} add-source <folder>`" in first


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


def test_init_names_a_config_it_cannot_load_by_its_error_alone(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 11 M26: init printed "using existing <config>" before loading it, and in a pipe stdout is
    block-buffered, so the line landed after the configuration error, as if init went on. It loads first."""
    path = initialised.config_path
    path.chmod(0)
    try:
        assert cli.main(["init", "--config", str(path)]) == cli.EXIT_CONFIG
    finally:
        path.chmod(0o600)
    out = capsys.readouterr()
    assert "using existing" not in out.out and "cannot read" in out.err, out
    assert cli.main(["init", "--config", str(path)]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith(f"using existing {path}")


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


@pytest.mark.parametrize("command", ["init", "add-source"])
def test_setup_makes_what_is_inside_its_folders_owner_only_before_status_looks(
    initialised: Config, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field report 2026-10-07: install.sh runs this command, then status, and stops on a
    ``docs_repo.permissions`` FAIL before the sync that clears it. The command made its folders owner-only
    already; it now does what a sync does for what is inside them (an ``_eval`` folder an agent's file tool
    wrote under umask 022, a cache folder, a log), so status has no such FAIL to stop on."""
    docs, log_dir = initialised.docs_repo, initialised.log_dir
    eval_dir, old_build = docs / "_eval", initialised.cache_dir / "old-build"
    for d in (eval_dir, old_build, log_dir):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o755)
    files = [eval_dir / "questions.md", log_dir / "poll.out.log"]
    for f in files:
        f.write_text("1. What did Contoso decide?\n", encoding="utf-8")
        f.chmod(0o644)
    [found] = doctor._check_permissions(initialised)
    assert not found.ok and found.fix == "run ~/.local/bin/agentsync sync, which makes these owner-only"

    argv = ["init"] if command == "init" else ["add-source", str(initialised.sources[0].path)]
    assert cli.main([*argv, "--config", str(initialised.config_path)]) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert f"tightened {log_dir} to 0700 (was 0755): it holds tenant data" in out
    assert "tightened 4 paths inside the docs repo, the cache or the logs: they hold tenant data" in out
    assert {f.stat().st_mode & 0o777 for f in files} == {0o600}
    assert {d.stat().st_mode & 0o777 for d in (eval_dir, old_build, log_dir)} == {0o700}
    assert doctor._check_permissions(initialised)[0].ok
    assert cli.main([*argv, "--config", str(initialised.config_path)]) == cli.EXIT_OK
    assert "tightened" not in capsys.readouterr().out, "nothing left to say on the next run"


@pytest.mark.parametrize(
    ("key", "spelled"),
    [("log_dir", "{home}"), ("log_dir", "{home}/x/.."), ("cache_dir", "{home}"), ("state_dir", "{home}")],
)
def test_setup_leaves_a_folder_that_is_the_home_folder(
    tmp_path: Path, key: str, spelled: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review, 2026-10-07: a config may name any folder for its cache, its logs and its state, and one that
    is the home folder is not agentsync's alone. The sync left a cache or log folder there as it was, but
    setup runs first and had no such rule: ``tightened <home> to 0700 (was 0755)``, which ends sharing from
    that home's Public folder. Written as ``~/x/..`` the folder passed the sync's rule too, and what is in
    the home folder was made owner-only as well."""
    home = Path.home()
    mine = home / "notes.txt"
    (home / "x").mkdir()
    mine.write_text("x\n", encoding="utf-8")
    loose = {home: 0o755, home / "x": 0o755, mine: 0o644}
    for path, bits in loose.items():
        path.chmod(bits)
    cfg = tmp_path / "ctx" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text(f'[agentsync]\n{key} = "{spelled.format(home=home)}"\n', encoding="utf-8")
    assert cli.main(["init", "--config", str(cfg)]) == cli.EXIT_OK
    assert "tightened" not in capsys.readouterr().out
    assert {path: path.stat().st_mode & 0o777 for path in loose} == loose


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


def test_a_commit_with_no_mirror_change_says_what_its_zero_counts(capsys: pytest.CaptureFixture[str]) -> None:
    """``N change(s)`` counts the mirror pages sources changed. A commit beside 0 holds other docs-repo files
    (a baseline flip in INDEX.md, the agent's _eval drafts), and the header says so; with no commit it reads
    as before."""
    committed = CycleReport(
        run_id=6, mode=CycleMode.POLL, sources=(), changes=(), commit_sha="0efe0129bbe4" * 3
    )
    cli._print_report(committed)
    assert capsys.readouterr().out.splitlines()[0] == (
        "run 6 · mode poll · commit 0efe0129bbe4 · 0 changes (no source changed a mirror page; the commit "
        "holds other docs-repo files)"
    )
    cli._print_report(dataclasses.replace(committed, commit_sha=None))
    assert (
        capsys.readouterr().out.splitlines()[0]
        == "run 6 · mode poll · commit none (no content change) · 0 changes"
    )


def test_the_summary_line_counts_the_files_read_again_apart_from_those_converted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A re-read of the same bytes for what its converter has gained is never in ``converted``; the summary
    line says ``, read again R`` after it when R > 0, and nothing when no file was read again."""
    report = CycleReport(run_id=3, mode=CycleMode.POLL, sources=(), changes=(), commit_sha=None, read_again=2)
    cli._print_report(report)
    assert capsys.readouterr().out.splitlines()[-1] == "converted 0, deferred 0 online-only, read again 2"
    cli._print_report(dataclasses.replace(report, read_again=0))
    assert capsys.readouterr().out.splitlines()[-1] == "converted 0, deferred 0 online-only"
    # B13 round 4 Q28: a no-converter stub's re-read is the first read of its bytes, so it is in N as well
    # as in R; the line says so, keeps R right after "read again" and adds no bare "converted" token.
    cli._print_report(dataclasses.replace(report, read_first=1))
    line = capsys.readouterr().out.splitlines()[-1]
    assert line == (
        "converted 0, deferred 0 online-only, read again 2 (1 of them a first read: a stub whose bytes had "
        "never been read, so also counted as converted)"
    )  # B13 round 10 L16: it names the count it is in ("now in the count above" named none)
    # one bare "converted" field, as install.sh's awk splits the line; "converted)" is none
    assert line.split().count("converted") == 1 and len(re.findall(r"\bconverted \d", line)) == 1
    # B13 round 5 F34: with the split as well, one parenthesis per subject: "; of the converted" follows
    # the Q28 clause, never a second parenthesis beside it; no other field is a bare "converted".
    unread = SourceReport("work", SourceKind.LOCAL, PassKind.FULL, True, converted=3, unreadable=1)
    cli._print_report(dataclasses.replace(report, sources=(unread,), read_first=1))
    line = capsys.readouterr().out.splitlines()[-1]
    assert line == (
        "converted 3, deferred 0 online-only, read again 2 (1 of them a first read: a stub whose bytes had "
        "never been read, so also counted as converted); of the converted: 1 unreadable, 0 failed, 0 refused"
    )
    assert ") (" not in line and line.split().count("converted") == 1
    # B13 round 10 L34: when every one was a first read, "read again 2 (2 of them a first read ...)" said
    # both things of the same files: the head says "first read" instead, R right after it, and the split
    # still follows with "; "
    cli._print_report(dataclasses.replace(report, sources=(unread,), read_first=2))
    line = capsys.readouterr().out.splitlines()[-1]
    assert line == (
        "converted 3, deferred 0 online-only, first read 2 (stubs whose bytes had never been read, so also "
        "counted as converted); of the converted: 1 unreadable, 0 failed, 0 refused"
    )
    assert "again" not in line and ") (" not in line and line.split().count("converted") == 1
    # B13 round 11 M11: one stub is "a stub", not "stubs"
    cli._print_report(dataclasses.replace(report, read_again=1, read_first=1))
    assert capsys.readouterr().out.splitlines()[-1] == (
        "converted 0, deferred 0 online-only, first read 1 (a stub whose bytes had never been read, so also "
        "counted as converted)"
    )
    cli._print_report(dataclasses.replace(report, sources=(unread,), read_again=0))
    assert capsys.readouterr().out.splitlines()[-1] == (
        "converted 3, deferred 0 online-only (of the converted: 1 unreadable, 0 failed, 0 refused)"
    )
    # B13 round 6 G35: failed conversions alone print the split too (it was built only for unreadable
    # ones), and files refused after their read are named in it, after "failed" for an older parser.
    failed = SourceReport("work", SourceKind.LOCAL, PassKind.FULL, True, converted=4, conversion_failed=1)
    cli._print_report(dataclasses.replace(report, sources=(failed,), read_again=0))
    assert capsys.readouterr().out.splitlines()[-1] == (
        "converted 4, deferred 0 online-only (of the converted: 0 unreadable, 1 failed, 0 refused)"
    )
    refused = SourceReport("work", SourceKind.LOCAL, PassKind.FULL, True, converted=4, refused=3)
    cli._print_report(dataclasses.replace(report, sources=(refused,), read_again=0))
    line = capsys.readouterr().out.splitlines()[-1]
    assert line == "converted 4, deferred 0 online-only (of the converted: 0 unreadable, 0 failed, 3 refused)"
    assert line.split().count("converted") == 1


def test_the_run_lines_docstring_gives_each_count_its_own_letter() -> None:
    """B13 round 19 W47: round 18's V13 named the converter-waiting count W in ``_print_report``'s docstring,
    whose next sentences use W for the withheld-unread count ("with R and W the line's refusals add up"), a
    false sum for the converter-waiting files, which are never refused. It is C."""
    doc = " ".join((cli._print_report.__doc__ or "").split())
    assert (
        "``; C waiting for its converter (it could not start; read once it does)`` when the converter of C"
        in doc
    )
    assert "W waiting for its converter" not in doc and "converter of W files" not in doc


def test_the_sync_line_counts_the_files_refused_without_a_read(capsys: pytest.CaptureFixture[str]) -> None:
    """B13 round 7 H32: a source line's ``refused=`` counts every refused stub, the closing split only those
    refused after a read, so a file a rule withheld by its name (refused=1) was in neither the split nor N:
    the line said 4 refused against 1 + 4. It now adds the files refused without a read, after the split or
    alone, so its refusals add up to the sources'; with none it is as before."""
    alpha = SourceReport(
        "project-alpha", SourceKind.LOCAL, PassKind.FULL, True, counts={Verdict.REFUSED: 1}, refused_unread=1
    )
    beta = SourceReport(
        "project-beta",
        SourceKind.LOCAL,
        PassKind.FULL,
        True,
        counts={Verdict.CHANGED: 10, Verdict.REFUSED: 4},
        converted=14,
        refused=4,
    )
    report = CycleReport(run_id=9, mode=CycleMode.POLL, sources=(alpha, beta), changes=(), commit_sha=None)
    cli._print_report(report)
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == (
        "converted 14, deferred 0 online-only (of the converted: 0 unreadable, 0 failed, 4 refused); 1 "
        "refused without a read (no converter for the type, or a rule that needs no bytes)"
    )
    said = sum(int(n) for n in re.findall(r"(\d+) refused\b", out[-1]))
    assert said == sum(int(n) for ln in out for n in re.findall(r"\brefused=(\d+)", ln)) == 5
    assert out[-1].split().count("converted") == 1 and "read again" not in out[-1]
    cli._print_report(dataclasses.replace(report, sources=(alpha,)))
    assert capsys.readouterr().out.splitlines()[-1] == (
        "converted 0, deferred 0 online-only; 1 refused without a read (no converter for the type, or a rule "
        "that needs no bytes)"
    )
    cli._print_report(dataclasses.replace(report, sources=(beta,)))
    assert "without a read" not in capsys.readouterr().out.splitlines()[-1], "nothing when there are none"


def test_the_sync_line_names_the_label_rule_for_a_file_it_withheld_unread(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 round 8 J29: an image a label rule withheld (its page says "withheld by label rule") was counted
    under "refused without a read (no converter for the type, or a rule that needs no bytes)", whose one
    cause a reader knows is false for it: OCR converts a .png. Those files are counted apart and their
    clause names the rule; each clause is printed only when its count is above 0, and the refusals still add
    up to the sources'."""
    alpha = SourceReport(
        "project-alpha",
        SourceKind.LOCAL,
        PassKind.FULL,
        True,
        counts={Verdict.REFUSED: 3},
        refused_unread=1,
        withheld_unread=2,
    )
    report = CycleReport(run_id=9, mode=CycleMode.POLL, sources=(alpha,), changes=(), commit_sha=None)
    cli._print_report(report)
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == (
        "converted 0, deferred 0 online-only; 2 withheld unread by the label rule (a file whose label cannot "
        "be read: an image, or a .msg not on this Mac); 1 refused without a read (no converter "
        "for the type, or a rule that needs no bytes)"
    )
    said = sum(int(n) for n in re.findall(r"(\d+) (?:refused|withheld)\b", out[-1]))
    assert said == sum(int(n) for ln in out for n in re.findall(r"\brefused=(\d+)", ln)) == 3
    assert out[-1].split().count("converted") == 1 and "deferred" not in out[-1].split("online-only", 1)[1]
    cli._print_report(dataclasses.replace(report, sources=(dataclasses.replace(alpha, refused_unread=0),)))
    assert capsys.readouterr().out.splitlines()[-1] == (
        "converted 0, deferred 0 online-only; 2 withheld unread by the label rule (a file whose label cannot "
        "be read: an image, or a .msg not on this Mac)"
    )


def test_a_type_with_no_converter_is_counted_refused_without_a_read_and_no_rule_is_named(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 7 H32: with no label rule set, a type no converter takes is refused by its name; the sync
    line counts it as refused without a read and names no label rule as its cause."""
    (local_source_dir / "archive.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    capsys.readouterr()
    assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    (summary,) = [ln for ln in out if ln.startswith("converted ")]
    assert "; 1 refused without a read (" in summary and "label" not in summary, summary
    assert any("refused=1" in ln for ln in out if ln.startswith("  source: ")), out


def test_status_counts_the_files_held_between_the_marking_pass_and_the_removing_pass(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A local file must be absent from two complete passes before it is removed. Between the two, status
    ends the source's line with how many files wait; after the removal (the tombstone keeps its mark) it
    does not."""
    cfg = str(initialised.config_path)
    # B13 round 4 Q16: words that hold for any count, one file included
    # B13 round 5 F64: N counts files, and a file of several pages becomes several notices
    held = (
        " · absent 1 file (its page(s) become deletion notice(s) on a later complete pass if still missing)"
    )
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    (local_source_dir / "projects" / "acme" / "Kickoff Notes.docx").unlink()
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source (local, live): ")]
    assert line.endswith(held), line
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert (
        "    alarm: 1 file still absent from this complete pass: its page(s) are now deletion notice(s) "
        "(deleted upstream; purge queued)\n"
    ) in out
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    status = capsys.readouterr().out
    assert "source (local, live): baseline complete" in status and " · absent " not in status


def test_the_sync_line_tells_the_marking_pass_from_the_removing_pass(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``deletion_candidate=1`` is on the source's line of both passes (the classifier counts the absence
    before its outcome is known). The line says which pass it is: the first marks the file absent and keeps
    it, the second removes it."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    (local_source_dir / "projects" / "acme" / "Kickoff Notes.docx").unlink()
    capsys.readouterr()
    lines = []
    for _ in range(3):
        assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
        (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source: ")]
        lines.append(line)
    marking, removing, after = lines
    assert "deletion_candidate=1" in marking and "deletion_candidate=1" in removing
    assert " · 1 file marked absent · " in marking and "deletion notice" not in marking, marking
    removed = " · 1 file removed upstream (its page(s) are now deletion notice(s)) · "
    assert removed in removing and "marked absent" not in removing, removing
    assert "absent" not in after and "removed" not in after, after


def test_the_sync_line_counts_a_removed_file_of_several_pages_as_one_file(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F64 (B13 round 5): the removing pass's count is of files (stable ids), but the line called it
    "page(s) turned into deletion notice(s)": a workbook of three pages read "1 page(s)" beside "3 changes".
    The line names files, so a removed file of several pages reads one file."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    (local_source_dir / "projects" / "sample.xlsx").unlink()
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out.splitlines()
    changes = int(re.search(r" · (\d+) changes?\b", out[0]).group(1))  # type: ignore[union-attr]
    (line,) = [x for x in out if x.startswith("  source: ")]
    assert changes > 1, out[0]
    assert " · 1 file removed upstream (its page(s) are now deletion notice(s)) · " in line, line
    assert "page(s) turned into" not in line, line


def test_the_sync_line_prints_marked_absent_and_removed_only_when_there_are_any(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two counts sit after the breaker bit and before the cursor bit, each only when nonzero, so a pass
    with no absent file prints the line it always did."""
    src = SourceReport(
        source_id="src",
        kind=SourceKind.LOCAL,
        pass_kind=PassKind.FULL,
        enumeration_complete=True,
        counts={Verdict.DELETION_CANDIDATE: 2, Verdict.UNCHANGED: 1},
        absent_marked=2,
    )
    report = CycleReport(run_id=4, mode=CycleMode.POLL, sources=(src,), changes=(), commit_sha=None)
    cli._print_report(report)
    assert capsys.readouterr().out.splitlines()[1] == (
        "  src: full_enumeration · complete · 2 files marked absent · cursor held · "
        "deletion_candidate=2, unchanged=1"
    )
    both = dataclasses.replace(src, absent_marked=1, absent_removed=3)
    cli._print_report(dataclasses.replace(report, sources=(both,)))
    assert capsys.readouterr().out.splitlines()[1] == (
        "  src: full_enumeration · complete · 1 file marked absent · 3 files removed upstream (their "
        "page(s) are now deletion notice(s)) · cursor held · deletion_candidate=2, unchanged=1"
    )
    neither = dataclasses.replace(src, absent_marked=0)
    cli._print_report(dataclasses.replace(report, sources=(neither,)))
    assert capsys.readouterr().out.splitlines()[1] == (
        "  src: full_enumeration · complete · cursor held · deletion_candidate=2, unchanged=1"
    )


def test_status_names_no_marked_file_while_the_deletion_breaker_holds_the_source(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tripped breaker removes nothing until accept-deletions, so the marked file is not "removed by a
    later complete pass": status leaves the count to the breaker's own field."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    (local_source_dir / "projects" / "acme" / "Kickoff Notes.docx").unlink()
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    now = datetime.now(UTC)
    with Manifest(initialised.state_paths.db) as manifest:
        assert manifest.absent_counts() == {"source": 1}
        manifest.trip_breaker(
            "source", candidates=31, tripped_at=now.isoformat(), until=(now + timedelta(days=7)).isoformat()
        )
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source (local, live): ")]
    assert "breaker TRIPPED until " in line and "(31 candidates)" in line and " · absent " not in line, line


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
        out.splitlines()[-1] == f"NEXT: 1 blocking finding above: fix every ERROR, then run `{loop.BIN} sync`"
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
    assert "0 findings, 0 blocking" in out
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
    fix = f"NEXT: 1 blocking finding above: fix every ERROR, then run `{loop.BIN} sync`"
    assert out.splitlines()[-1] == fix
    # Committed by hand, nothing is pending or dirty, so rule 7 counts 0: NEXT still names the ERROR, never
    # the queued rows or "session done".
    git(initialised.docs_repo, "add", "topics/orders.md")
    git(initialised.docs_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "by hand")
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "ERROR UNLISTED topics/orders.md" in out and out.splitlines()[-1] == fix
    assert "queued" not in out and "session done" not in out


def _meeting_with_a_cite_finding(initialised: Config) -> str:
    """A synced repo past the baseline hold and one uncommitted ``kind: meeting`` page, pinned to a real
    mirror page, whose one tag resolves to no recording: a CITE-UNRESOLVED and nothing that blocks."""
    cfg = _synced(initialised)
    _before(initialised)
    sample = "mirror/source/projects/sample.txt.md"
    pin = _mirror_sha(initialised, sample)
    (initialised.docs_repo / "topics" / "meetings").mkdir(parents=True)
    (initialised.docs_repo / "topics" / "meetings" / "m.md").write_text(
        f"---\nkind: meeting\nentity: contoso-review\npurpose: what the review decided\nsources:\n"
        f"  - {{path: {sample}, at_rendered_sha256: {pin}, role: primary}}\n---\n"
        '# Review\n\n- Opening: `heard 00:01:10` "let us start".\n',
        encoding="utf-8",
    )
    return cfg


def test_a_cite_finding_is_a_warn_line_and_never_holds_the_checkpoint(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _meeting_with_a_cite_finding(initialised)
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "warn  CITE-UNRESOLVED topics/meetings/m.md: `heard 00:01:10`" in out
    assert "1 finding, 0 blocking" in out and "ERROR" not in out
    assert curate.checkpoint_blockers(initialised.docs_repo) == []


def test_next_is_not_rule_7_for_a_cite_finding(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """The page is uncommitted, so rule 7 counts checkpoint blockers: a CITE finding is not one."""
    cfg = _meeting_with_a_cite_finding(initialised)
    assert not [f for f in loop.checkpoint_findings(initialised) if f.code.startswith("CITE-")]
    step = loop.next_step(initialised)
    assert step.rule != 7 and "CITE" not in step.step
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    assert step.lines()[0] in capsys.readouterr().out.splitlines()


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


def test_curate_old_names_are_hidden_aliases(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    """One synced repo serves all three names: ``curate`` only reads, so each alias sees the same state."""
    cfg = _synced(initialised)
    _before(initialised)
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_OK
    want = capsys.readouterr()
    assert cli.main(["--help"]) == cli.EXIT_OK
    usage = capsys.readouterr().out
    for old in ("curate-queue", "lint", "refresh-queue"):
        assert cli.main([old, "--config", cfg]) == cli.EXIT_OK, old
        got = capsys.readouterr()
        assert got.out == want.out and "UNCOVERED\t" in got.out, old
        assert got.err == want.err + "renamed: run ~/.local/bin/agentsync curate\n", old
        assert f"    {old} " not in usage, old


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
    out, err = capsys.readouterr()
    assert "checkpoint held: 1 curation error; fix it" in out and "(every sync retries it)" in out
    # B13 round 16 T56: its WARNING is not said again on stderr beside that line (the job's err.log keeps it)
    assert "WARNING agentsync.cycle: checkpoint held" not in err, err
    assert "ERROR STALE topics/c.md" in out and "topics/a.md" not in out
    assert _curated(repo) == banner_head
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # c.md is committed now; still retried
    assert "checkpoint held: 1 curation error" in capsys.readouterr().out

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
    assert (
        "run `~/.local/bin/agentsync add-source <folder>` to create it" in capsys.readouterr().err
    )  # KISS K14
    assert cli.main(["status", "--config", str(bad)]) == cli.EXIT_CONFIG
    assert "configuration error" in capsys.readouterr().err


def test_unknown_source_and_graph_without_client_id_exit_78(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["reconcile", "--config", cfg, "--source", "nope"]) == cli.EXIT_CONFIG
    capsys.readouterr()
    assert cli.main(["graph", "whoami", "--config", cfg]) == cli.EXIT_CONFIG
    assert cli.main(["whoami", "--config", cfg]) == cli.EXIT_CONFIG
    # field N11: the message says first that local sources need nothing from IT
    lead = (
        "configuration error: Graph not configured ([graph] client_id unset): local sources work without IT"
    )
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 2 and all(line.startswith(lead) for line in err), err


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


def test_lock_held_warning_names_the_holder_and_repeats_verbatim(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """N24 (field 2026-10-08 D10.d): the report's Recent errors keeps only lines with a level word, so the
    holder goes in the WARNING itself, by its label alone (no pid, start, beat, age or stage), so that the
    skips behind every run of one kind read the same and the report merges them. B13 round 19 W13, W30: only
    the background job's run logs it (its err.log is what Recent errors reads); the next test pins the
    foreground run's one line."""
    monkeypatch.setenv("XPC_SERVICE_NAME", f"{initialised.launchd_label_prefix}.poll")
    lock = SingleWriterLock(initialised.state_paths.lock, "reconcile")
    lock.acquire()
    cfg = str(initialised.config_path)
    texts: list[str] = []
    try:
        for stage in ("source:contoso-docs", "commit"):
            lock.beat(stage)
            assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_LOCK_HELD
            err = capsys.readouterr().err.splitlines()
            texts += [ln.split(" WARNING ", 1)[1] for ln in err if " WARNING agentsync.cli: skipped" in ln]
            # the detail that changes from skip to skip stays on the stderr line, which has no level word
            (skip,) = [ln for ln in err if ln.startswith("skipped: lock held")]
            assert skip.startswith("skipped: lock held by pid ") and f"stage {stage!r}" in skip, err
            # B13 round 8 J11, J25: the line says "lock held" once, not "lock held (lock held: pid ...)"
            assert skip.count("lock held") == 1, skip
    finally:
        lock.release()
    # B13 round 7 H33: a cycle's label is its pass, not who started it (a sync typed in a terminal holds the
    # lock as poll or reconcile too); any other holder is named as it is
    assert texts == ["agentsync.cli: skipped: lock held by another sync (its pass: reconcile)"] * 2
    assert cli._lock_holder("poll") == "another sync (its pass: poll)"
    for other in (EMPTY_BODY_HOLDER, "an unidentified holder (unparseable lock body)", "governance"):
        assert cli._lock_holder(other) == other


@pytest.mark.parametrize("holder", ["reconcile", "no body"])
def test_a_foreground_skip_is_said_once_in_the_wait_lines_words(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    holder: str,
) -> None:
    """B13 round 19 W13, W30: a sync typed in a terminal that gave up on the lock said its skip twice, the
    second time as a timestamped ``WARNING agentsync.cli:`` line, which reaches no store from a terminal: only
    the background job's run logs it (its err.log feeds the report's Recent errors), with the same words. W14:
    a lock held with an empty body was waited for as ``the lock is held by install.sh while ...`` and skipped
    as ``an unrecorded holder (install.sh while ...)``; the skip now uses the wait line's words, and the
    WARNING keeps ``EMPTY_BODY_HOLDER``, so Recent errors still merges the old and new skips."""
    import fcntl  # noqa: PLC0415

    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    monkeypatch.setattr(cli, "run_cycle", functools.partial(run_cycle, lock_wait_s=0.3))
    path = initialised.state_paths.lock
    lock = SingleWriterLock(path, "reconcile")
    fd: int | None = None
    if holder == "no body":
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, 0)
        fcntl.flock(fd, fcntl.LOCK_EX)  # held, no body: what install.sh's lockf does
    else:
        lock.acquire()
    cfg = str(initialised.config_path)
    try:
        for argv, waited in ((["sync", "--mode", "poll"], False), (["sync"], True)):
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_LOCK_HELD
            err = capsys.readouterr().err.splitlines()
            (skip,) = [ln for ln in err if "skipped: lock held" in ln]
            assert skip.startswith("skipped: lock held by "), err
            assert not [ln for ln in err if " WARNING " in ln], err
            if holder == "no body":
                tail = r"; waited \d+s" if waited else ""
                words = re.escape(cycle_mod.UNRECORDED_LOCK_HOLDER)
                assert re.fullmatch(rf"skipped: lock held by {words}{tail}", skip), err
                assert "unrecorded holder" not in "\n".join(err) and "((" not in "\n".join(err), err
        monkeypatch.setenv("XPC_SERVICE_NAME", f"{initialised.launchd_label_prefix}.poll")
        assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_LOCK_HELD
        err = capsys.readouterr().err.splitlines()
    finally:
        if fd is not None:
            os.close(fd)
        lock.release()
    named = EMPTY_BODY_HOLDER if holder == "no body" else "another sync (its pass: reconcile)"
    warned = [ln.split(" WARNING ", 1)[1] for ln in err if " WARNING " in ln]
    assert warned == [f"agentsync.cli: skipped: lock held by {named}"], err


def test_log_lines_carry_their_time_in_utc(capsys: pytest.CaptureFixture[str]) -> None:
    """B13 round 8 J26: the stderr handler (the background job's err.log, and the terminal) wrote local time
    with no zone, while the lock holder's "since" inside the same line, and the launcher's lines beside it,
    are UTC: one line showed two clocks, a day apart. It writes UTC with a Z, in the shape the setup report
    already gave older lines, with the milliseconds kept."""
    # B13 round 9 K55: the old TZ is put back before tzset runs, not after the test with none behind it
    with zone("America/Chicago"):
        before = datetime.now(UTC).replace(microsecond=0)
        cli._setup_logging(0)
        logging.getLogger("agentsync.cycle").warning(
            "another sync is running (lock held: pid 4242 (a sync, its pass: poll))"
        )
        after = datetime.now(UTC)
    (line,) = [ln for ln in capsys.readouterr().err.splitlines() if "another sync is running" in ln]
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.\d{3}Z WARNING agentsync\.cycle: another .*", line)
    assert m is not None, line
    assert before <= datetime.fromisoformat(m[1]).replace(tzinfo=UTC) <= after, (line, before, after)
    # the report's own reader takes the line as it is: already UTC, nothing to convert
    assert setup_report._utc_log_times([line]) == [line]
    assert setup_report._LOG_TIME_RE.fullmatch(line) is not None


def test_a_zone_puts_the_old_tz_back_before_its_tzset() -> None:
    """B13 round 9 K55: the UTC log test above set TZ with monkeypatch and, on the way out, deleted it and ran
    ``tzset``; monkeypatch put the old TZ back after the test, with no ``tzset`` behind it, so ``time``'s
    cached zone stayed the system's under the restored value. ``conftest.zone`` restores, then runs it."""
    with zone("UTC"):
        with zone("America/Chicago"):
            assert time.tzname == ("CST", "CDT")
        assert os.environ["TZ"] == "UTC" and time.tzname == ("UTC", "UTC") and time.timezone == 0


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
        with pytest.raises(LockHeldError, match="waited") as ei:
            run_cycle(initialised, mode=None, lock_wait_s=0.3)
    finally:
        lock.release()
    # §16.39 N24: the re-raise keeps the holder's label for the WARNING and does not double the prefix
    assert ei.value.who == "poll"
    assert str(ei.value).count("lock held: ") == 1, str(ei.value)


def test_the_quiet_lock_wait_logs_once_and_leaves_the_skip_to_its_stderr_line(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 round 7 H31: install.sh's first sync prints its own wait line and a ticker, and agentsync's lock
    wait added a WARNING every 30 s and two skip lines more. Under AGENTSYNC_QUIET_LOCK_WAIT=1 (install.sh's
    first sync only) the wait sends every beat to DEBUG (round 16 T33: the first too, a timestamped WARNING
    under install.sh's own wait line), and the skip is said once, on the stderr line that names the holder. A
    run without it keeps a beat every interval: it has no ticker. A library caller (no ``say``) logs them at
    WARNING; the CLI's plain lines are pinned in the next test (B13 round 17 U105). Round 17 U68: a quiet wait
    install.sh did not announce logs its first beat once."""
    monkeypatch.setattr(cycle_mod, "_LOCK_PROGRESS_S", 0.2)
    lock = SingleWriterLock(initialised.state_paths.lock, "poll")
    lock.acquire()

    beat = re.compile(r"another sync is (still )?running")

    def beats() -> list[int]:
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=cycle_mod.__name__), pytest.raises(LockHeldError):
            run_cycle(initialised, mode=None, lock_wait_s=2.0)  # a retry every 0.5 s
        return [r.levelno for r in caplog.records if beat.match(r.getMessage())]

    def debug_beats() -> list[str]:
        said = [r for r in caplog.records if r.levelno == logging.DEBUG]
        return [r.getMessage() for r in said if beat.match(r.getMessage())]

    try:
        default = beats()
        assert len(default) >= 3 and set(default) == {logging.WARNING}, default
        monkeypatch.setenv(cycle_mod.QUIET_LOCK_WAIT_ENV, "1")
        monkeypatch.setenv(cycle_mod.LOCK_WAIT_ANNOUNCED_ENV, "1")
        quiet = beats()
        # B13 round 16 T33: the first beat too, since install.sh's own line already says it waits
        assert len(quiet) >= 3 and set(quiet) == {logging.DEBUG}, quiet
        # B13 round 19 W46: each DEBUG record keeps its elapsed time; the quiet words have none
        assert all(m.endswith("s of at most 2s)") for m in debug_beats()), debug_beats()
        monkeypatch.setenv(cycle_mod.LOCK_WAIT_ANNOUNCED_ENV, "0")
        raced = beats()  # install.sh's probe saw the lock free: the first beat is said, once (U68)
        assert len(raced) >= 3 and raced[0] == logging.WARNING and set(raced[1:]) == {logging.DEBUG}, raced
        assert all(m.endswith("s of at most 2s)") for m in debug_beats()), debug_beats()
        capsys.readouterr()
        assert cli.main(["sync", "--mode", "poll", "--config", str(initialised.config_path)]) == 75
        err = capsys.readouterr().err.splitlines()
        assert [ln for ln in err if ln.startswith("skipped: lock held by ")] and not [
            ln for ln in err if "WARNING agentsync.cli: skipped" in ln
        ], err
    finally:
        lock.release()


@pytest.mark.parametrize("env", ["plain", "quiet-announced", "quiet-unannounced"])
def test_the_clis_lock_wait_says_plain_lines_and_marks_a_wait_install_sh_did_not_announce(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: str,
) -> None:
    """B13 round 17 U105: a foreground ``agentsync sync`` that waits for the lock says each beat as a plain
    stderr line (``_lock_say``), never as a timestamped ``WARNING agentsync.cycle:`` logger line. Round 17
    U68: under install.sh's quiet wait every beat is DEBUG while install.sh announced the wait (its probe
    found the lock busy); a wait its probe missed says its first beat once, in plain words, and marks stdout
    with ``LOCK_WAITED_MARK``, which install.sh reads to book the wait in install.log."""
    monkeypatch.setattr(cycle_mod, "_LOCK_PROGRESS_S", 0.2)
    if env != "plain":
        monkeypatch.setenv(cycle_mod.QUIET_LOCK_WAIT_ENV, "1")
        monkeypatch.setenv(cycle_mod.LOCK_WAIT_ANNOUNCED_ENV, "1" if env == "quiet-announced" else "0")
    lock = SingleWriterLock(initialised.state_paths.lock, "poll")
    lock.acquire()
    timer = threading.Timer(1.2, lock.release)
    timer.start()
    try:
        capsys.readouterr()
        assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    finally:
        timer.join()
        lock.release()
    cap = capsys.readouterr()
    err, out = cap.err.splitlines(), cap.out.splitlines()
    said = [ln for ln in err if re.match(r"another sync is (still )?running \(", ln)]
    assert not [ln for ln in err if "agentsync.cycle:" in ln], err
    assert not [ln for ln in said if "lock held: " in ln or "((" in ln], err  # B13 round 18 V40
    # B13 round 19 W29: the holder in one bracket level, with no stage: ``pid N (a sync, its pass: poll) since
    # ..., last beat ... (0s ago), stage 'acquired'`` nested its brackets inside the line's
    for ln in said:
        holder = ln.split("running (", 1)[1].split(")", 1)[0]
        assert "(" not in holder and "stage" not in holder and "last beat" not in holder, ln
        # ``active`` needs the holder's sidecar (``lock._wait_words``), and release unlinks it before it
        # empties the body, so a line read in between says only ``since`` (CI 39809607845, a slow runner)
        assert re.fullmatch(r"a sync, pass poll, pid \d+(, since \S+Z)?(, active \d+s ago)?", holder), ln
        assert "since" in holder or "active" in holder, ln
    if env == "plain":
        assert len(said) >= 2 and all(ln.endswith("s of at most 600s)") for ln in said), err
        assert all(ln.startswith("another sync is running (a sync, pass poll, pid ") for ln in said), err
        assert cli.LOCK_WAITED_MARK not in out
    elif env == "quiet-announced":
        assert not said and cli.LOCK_WAITED_MARK not in out, (err, out)
    else:
        # install.sh's own words, which the setup prompt quotes as an expected wait
        assert [ln.split(" (", 1)[0] for ln in said] == ["another sync is still running"], err
        assert [ln.split("); ", 1)[1] for ln in said] == ["waiting for it to end, at most 10 minutes"], err
        assert out.count(cli.LOCK_WAITED_MARK) == 1, out


@pytest.mark.parametrize("env", ["plain", "quiet-unannounced"])
def test_a_lock_held_with_no_body_is_waited_for_in_one_bracket_never_as_another_sync(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: str,
) -> None:
    """B13 round 18 V40: the foreground wait's line put ``str(exc)`` in its bracket, so it read
    ``another sync is running (lock held: an unrecorded holder (install.sh ..., a status or doctor check, or a
    cycle starting))``: brackets nested two deep, and a status check called a sync. A lock held with an empty
    body (install.sh's lockf, a status probe) now gets its own line, one bracket deep; the quiet wait keeps
    the opening the setup prompt quotes and says what else holds the lock."""
    import fcntl  # noqa: PLC0415

    monkeypatch.setattr(cycle_mod, "_LOCK_PROGRESS_S", 0.2)
    if env != "plain":
        monkeypatch.setenv(cycle_mod.QUIET_LOCK_WAIT_ENV, "1")
        monkeypatch.setenv(cycle_mod.LOCK_WAIT_ANNOUNCED_ENV, "0")
    path = initialised.state_paths.lock
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.ftruncate(fd, 0)
    fcntl.flock(fd, fcntl.LOCK_EX)  # held, no body: what install.sh's lockf does

    def release() -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    timer = threading.Timer(1.2, release)
    timer.start()
    try:
        capsys.readouterr()
        assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    finally:
        timer.join()
        os.close(fd)
    err = capsys.readouterr().err.splitlines()
    said = [ln for ln in err if "waiting for it" in ln]
    assert said, err
    for ln in said:
        assert "((" not in ln and "))" not in ln and "lock held: " not in ln, ln
    if env == "plain":
        assert all(ln.startswith(f"{cycle_mod.UNRECORDED_LOCK_WAIT} (") for ln in said), said
        assert not [ln for ln in said if ln.startswith("another sync")], said
    else:
        assert said == [
            "another sync is still running (or install.sh or a check holds the lock); waiting for it to end, "
            "at most 10 minutes"
        ], said


def test_a_missing_bundled_pandocs_error_keeps_its_action_whatever_the_home_folders_length(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 18 V41: a sync's per-file error line for a missing bundled pandoc put the path first, so
    under a long home folder the 200-character reason was cut in the middle of "(is pypandoc_binary
    installed?); set [convert] pandoc_path". The action now comes before the path, so a cut falls inside the
    path, and the second run publishes nothing new (each stub stands: its cut reason is the same)."""
    from agentsync.convert import pandoc as pandoc_mod  # noqa: PLC0415

    deep = Path.home() / ("h" * 45) / ".local/share/uv/tools/agentsync/lib/python3.13/site-packages/pypandoc"
    monkeypatch.setattr(pandoc_mod, "_wheel_pandoc", lambda: deep / "files" / "pandoc")
    cfg = str(initialised.config_path)
    capsys.readouterr()
    cli.main(["sync", "--config", cfg])
    errors = [ln.strip() for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("error: ")]
    pandoc = [ln for ln in errors if "bundled pandoc not found" in ln]
    assert pandoc, errors
    for ln in pandoc:
        # B13 round 19 W33: the action is the agent's re-run, as doctor's [FAIL] line says first; the
        # [convert] pandoc_path edit is the person's, which that line gives them after it
        assert f"run {skill.INSTALL_SH} again" in ln and "pandoc_path" not in ln, ln
    cli.main(["sync", "--config", cfg])
    out = capsys.readouterr().out
    assert " · commit none (no content change) · 0 changes" in out.splitlines()[0], out


def test_a_missing_bundled_pandocs_reason_names_the_re_run_and_not_the_persons_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B13 round 19 W33: the reason named only "set [convert] pandoc_path", the person's step, while doctor's
    [FAIL] line for the same state names the agent's install.sh re-run first."""
    from agentsync.convert import pandoc as pandoc_mod  # noqa: PLC0415
    from agentsync.errors import ConversionError  # noqa: PLC0415

    monkeypatch.setattr(pandoc_mod, "_wheel_pandoc", lambda: tmp_path / "pypandoc" / "files" / "pandoc")
    with pytest.raises(ConversionError) as exc:
        pandoc_mod.resolved_pandoc(None)
    action, _, looked = str(exc.value).partition("; looked at ")
    assert action == (
        f"bundled pandoc not found; run {skill.INSTALL_SH} again (it reinstalls agentsync with its pandoc)"
    )
    assert "pandoc_path" not in action and looked.endswith("pypandoc/files/pandoc")


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


def _assert_fix_parses(fix: str | None) -> None:
    """A fix naming an agentsync command names one the CLI still parses (KISS K13b; the twin of
    test_ops_doctor.assert_fix_parses). The text after ``;`` and any parenthetical are prose. The command may
    be named by its full path, as ``loop.BIN`` names it (B13 round 2 R12)."""
    assert fix is not None and fix.startswith(("agentsync ", f"{loop.BIN} ")), fix
    command = (
        re.sub(r"\([^)]*\)", "", fix.split(";")[0]).removeprefix(f"{loop.BIN} ").removeprefix("agentsync ")
    )
    try:
        cli.build_parser().parse_args(shlex.split(command))
    except SystemExit:
        pytest.fail(f"the fix names a command the CLI rejects: {fix}")


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


def test_the_time_machine_fix_names_agentsync_by_its_full_path(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """~/.local/bin may not be on PATH: doctor's runnable fixes name agentsync by its full path, as the loop's
    waits do (B13 round 2 R12)."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("AGENTSYNC_TM_EXCLUDE", "1")
    state = initialised.state_paths.root
    monkeypatch.setattr(governance, "time_machine_status", lambda c: [(state, False)])
    (tm,) = [c for c in cli._governance_checks(initialised) if c.name == "governance.time_machine"]
    assert tm.fix == f"~/.local/bin/agentsync sync (or: tmutil addexclusion {state})"


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="getxattr(2) and file modes")
def test_a_state_file_this_account_cannot_open_is_not_checked_never_not_excluded(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 20 X12, X27: getxattr on an excluded manifest at mode 0000 fails with EACCES, which read as
    "no exclusion", so doctor warned "not excluded from Time Machine" with a sync as the fix: the exclusion
    was there, and the sync exits 1 on that file. Such a path is not checked, an [info] line pointing at the
    state_dir lines, never a warn or a sync; each cycle no longer re-excludes it; and a path with no
    exclusion still warns."""
    from agentsync import tm_exclude  # noqa: PLC0415

    monkeypatch.setenv("AGENTSYNC_TM_EXCLUDE", "1")
    assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    paths = [p for p in governance.time_machine_exclusions(initialised) if p.exists()]
    for p in paths:
        assert tm_exclude.set_exclusion(p), p
    db = expand(initialised.state_paths.db)
    db.chmod(0)
    calls: list[object] = []
    monkeypatch.setattr(governance, "_set_tm_exclusion", lambda p: calls.append(p) or True)
    monkeypatch.setattr(governance, "_run", lambda argv, **kw: calls.append(argv))
    try:
        assert tm_exclude.xattr_state(db, tm_exclude.TM_EXCLUDE_XATTR) == "Permission denied"
        assert not tm_exclude.has_xattr(db, tm_exclude.TM_EXCLUDE_XATTR)
        assert dict(governance.time_machine_status(initialised))[db] == "Permission denied"
        checks = cli._time_machine_checks(initialised)
        assert governance.ensure_time_machine_exclusions(initialised) == []
    finally:
        db.chmod(0o600)
    assert calls == [], "a path this account cannot read was excluded again"
    (tm,) = checks
    assert not tm.ok and tm.severity is doctor.Severity.INFO and tm.fix is None, tm
    assert tm.detail == (
        f"not checked: this account cannot read its exclusion: {db} (Permission denied); "
        "the state_dir lines say why"
    ), tm.detail
    assert "not excluded" not in tm.detail and "agentsync sync" not in tm.detail
    plain = initialised.config_path.parent / "plain.txt"
    plain.write_text("Contoso\n", encoding="utf-8")
    assert tm_exclude.xattr_state(plain, tm_exclude.TM_EXCLUDE_XATTR) is False
    monkeypatch.setattr(governance, "time_machine_status", lambda c: [(plain, False), (db, True)])
    (warn,) = cli._time_machine_checks(initialised)
    assert warn.severity is doctor.Severity.WARN and warn.detail == f"not excluded from Time Machine: {plain}"


@pytest.mark.skipif(sys.platform != "darwin", reason="setxattr(2)")
@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_the_time_machine_line_over_a_shut_state_folder_says_not_checked_once(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 21 Y51: over a state folder this account cannot search, governance.time_machine listed every
    state path as "Permission denied", a teams/ that does not exist included, while governance.hold and
    purge_queue said STATE_FOLDER_UNCHECKED. The state folder's paths get that one line; the paths outside it
    are still judged, and "tenant-data paths excluded" never prints over a folder nobody checked."""
    from agentsync import tm_exclude  # noqa: PLC0415

    monkeypatch.setenv("AGENTSYNC_TM_EXCLUDE", "1")
    assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    root = expand(initialised.state_paths.root)
    outside = [
        p
        for p in governance.time_machine_exclusions(initialised)
        if p.exists() and not (p == root or p.is_relative_to(root))
    ]
    assert outside, "the docs paths are outside the state folder"
    for p in governance.time_machine_exclusions(initialised):
        if p.exists():
            assert tm_exclude.set_exclusion(p), p
    root.chmod(0o600)
    try:
        checks = cli._time_machine_checks(initialised)
    finally:
        root.chmod(0o700)
    assert [(c.ok, c.severity, c.detail) for c in checks] == [
        (False, doctor.Severity.INFO, doctor.STATE_FOLDER_UNCHECKED)
    ], checks
    assert not any("teams" in c.detail or "Permission denied" in c.detail for c in checks), checks
    # a path outside the folder with no exclusion still warns beside the line
    plain = initialised.config_path.parent / "plain.txt"
    plain.write_text("Contoso\n", encoding="utf-8")
    monkeypatch.setattr(governance, "time_machine_status", lambda c: [(plain, False), (root / "teams", "x")])
    monkeypatch.setattr(doctor, "state_folder_shut", lambda c: True)
    info, warn = sorted(
        cli._time_machine_checks(initialised), key=lambda c: c.severity is doctor.Severity.WARN
    )
    assert info.detail == doctor.STATE_FOLDER_UNCHECKED, info
    assert warn.severity is doctor.Severity.WARN and warn.detail == f"not excluded from Time Machine: {plain}"


@pytest.mark.skipif(sys.platform != "darwin", reason="setxattr(2)")
def test_staging_keeps_its_time_machine_exclusion_through_a_sync_that_stops_on_the_state_file(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 20 X4, X13: every cycle emptied staging by rmtree and mkdir, so the folder lost its Time
    Machine exclusion until the end of a run that exited 0 or 1; a sync that stopped on the state file (65)
    left tenant downloads' folder unexcluded and doctor warned with a sync as the fix, which exits 65 again.
    Staging is emptied in place and excluded at once."""
    from agentsync import tm_exclude  # noqa: PLC0415

    monkeypatch.setenv("AGENTSYNC_TM_EXCLUDE", "1")
    staging = expand(initialised.state_paths.staging)
    cycle_mod._clear_staging(staging)  # a new folder: excluded at once
    assert tm_exclude.has_xattr(staging, tm_exclude.TM_EXCLUDE_XATTR)
    (staging / "left").mkdir()
    (staging / "left" / "page.pdf").write_bytes(b"x")
    (staging / "loose.txt").write_text("x\n", encoding="utf-8")
    cycle_mod._clear_staging(staging)
    assert list(staging.iterdir()) == [] and tm_exclude.has_xattr(staging, tm_exclude.TM_EXCLUDE_XATTR)
    cfg = str(initialised.config_path)
    [src] = [s for s in initialised.sources if s.kind is SourceKind.LOCAL]
    assert src.path is not None
    bulk = expand(src.path) / "bulk"
    bulk.mkdir()
    for i in range(160):
        (bulk / f"note-{i:03d}.txt").write_text(f"Contoso note {i}.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    _damage_table_leaf(db, "outputs", last=True)
    for i in range(160):
        (bulk / f"note-{i:03d}.txt").write_text(f"Contoso {i} v2.\n", encoding="utf-8")
    monkeypatch.setattr(governance, "ensure_time_machine_exclusions", lambda *a, **k: [])  # no backstop
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_STATE
    assert tm_exclude.has_xattr(staging, tm_exclude.TM_EXCLUDE_XATTR)
    assert dict(governance.time_machine_status(initialised))[staging] is True


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
    compaction = {c.name: c for c in cli._governance_checks(config)}["governance.compaction"]
    assert compaction.fix == "~/.local/bin/agentsync sync (its next full pass compacts); release any hold"
    _assert_fix_parses(compaction.fix)
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
    # B13 round 5 F95: by the binary's full path (~/.local/bin is not on PATH)
    assert "run ~/.local/bin/agentsync accept-deletions SOURCE" in capsys.readouterr().err


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
    assert "BREAKER TRIPPED" in tripped and "run `~/.local/bin/agentsync accept-deletions source`" in tripped
    assert "--accept-deletions" not in tripped
    repo = initialised.docs_repo
    assert (repo / "mirror/source/projects/sample.csv.md").is_file()
    with Manifest(initialised.state_paths.db) as m:
        assert m.breaker_active("source", datetime.now(UTC).isoformat())
    assert cli.main(["accept-deletions", "source", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "mode reconcile" in out and plural(len(victims), "change") in out and "BREAKER" not in out
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
    assert cap.out.startswith("NEXT: ") and "renamed: run ~/.local/bin/agentsync status" in cap.err
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


@pytest.mark.parametrize(
    ("dry_run", "count", "tail"),
    [
        (True, 0, "0 cache entries would be removed"),
        (True, 1, "1 cache entry would be removed"),
        (True, 2, "2 cache entries would be removed"),
        (False, 1, "1 cache entry removed"),
        (False, 3, "3 cache entries removed"),
    ],
)
def test_purge_headline_spells_the_cache_count(
    dry_run: bool, count: int, tail: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The purge headline's cache count is "1 cache entry" or "N cache entries", never "entr(ies)" (rehearsal
    v10, finding 4); the dry run still names no commit count, the real run still does."""
    rep = governance.PurgeReport(
        selector="stable-id",
        reason=governance.PurgeReason.UPSTREAM_DELETED,
        dry_run=dry_run,
        items=(("contoso", "x"),),
        docs_paths=("mirror/contoso/plan.md",),
        commits_rewritten=0 if dry_run else 3,
        blobs_targeted=2,
        cache_entries_removed=count,
    )
    cli._print_purge(rep)
    (head,) = [line for line in capsys.readouterr().out.splitlines() if line.startswith("purge ")]
    assert head.endswith(f"2 blobs targeted · {tail}"), head
    assert "entr(ies)" not in head
    assert ("rewritten" in head) is not dry_run


@pytest.mark.parametrize("dry_run", [True, False])
def test_purge_marks_a_taken_path_after_its_path_line(
    dry_run: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 R94: a path another file's live page holds now is printed as a ``taken:`` line right after its
    ``path:`` line, in the preview as in the run, so the operator reads it as a page that stays."""
    rep = governance.PurgeReport(
        selector="stable-id",
        reason=governance.PurgeReason.UPSTREAM_DELETED,
        dry_run=dry_run,
        items=(("contoso", "x"),),
        docs_paths=("mirror/contoso/plan.md",),
        taken_paths=("mirror/contoso/plan.md",),
        blobs_targeted=1,
        cache_entries_removed=0,
    )
    cli._print_purge(rep)
    lines = capsys.readouterr().out.splitlines()
    taken = (
        "  taken: mirror/contoso/plan.md (another file's live page is there now: its manifest row and index "
        "lines stay)"
    )
    assert lines.count(taken) == 1
    assert lines.index(taken) > lines.index("  path: mirror/contoso/plan.md")


def test_accept_deletions_help_says_an_incomplete_pass_applies_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 R54: the command list's line for ``accept-deletions`` says what its docstring and the incomplete
    alarm say: the removals apply only after a pass that lists the source in full."""
    assert cli.main(["--help"]) == cli.EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert (
        "apply its removals (a pass that does not list SOURCE in full applies none and keeps the breaker)"
        in out
    )


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
    out = capsys.readouterr().out
    assert "dry run" in out and (repo / page).is_file()
    assert "  taken: " not in out  # only a path another file's live page holds is marked taken
    # A dry run changed nothing, so its headline says what would go and names no commit count
    (head,) = [line for line in out.splitlines() if line.startswith("purge ")]
    assert head.endswith(" · 1 cache entry would be removed") and "rewritten" not in head
    assert "note: up to " in out and " would be rewritten" in out and "commit(s)" not in out  # U71
    rc = cli.main(
        ["purge", f"id={sid}", "--source", "source", "--reason", "erasure-request", "--config", cfg]
    )
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK, out
    assert "VERIFIED" in out and page in out
    assert " 1 commit rewritten · " in out and re.search(r" · 1 cache entry removed$", out, re.M)
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
    assert "dry run" in out and "1 queued purge previewed" in out and "; 1 queued" in out
    assert git(repo, "rev-parse", "HEAD").strip() == head and (repo / page).is_file()
    assert len(governance.pending_purges(initialised.state_paths.root)) == 1


def test_purge_queue_check_names_no_command_under_no_next_hint(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bring-back S16: install.sh prints the loop's WAITING ON YOU line for queued purges, so its status
    check carries a note and no second ``fix:``; by hand the fix is the command."""
    selector = governance.PurgeSelector(source_id="source", path_glob="*")
    assert governance.enqueue_purge(initialised.state_paths.root, selector, governance.PurgeReason.OPERATOR)

    def check() -> doctor.CheckResult:
        (found,) = [c for c in cli._governance_checks(initialised) if c.name == "governance.purge_queue"]
        return found

    by_hand = check()
    assert by_hand.severity is doctor.Severity.WARN and by_hand.fix == (
        "~/.local/bin/agentsync purge --queue --dry-run (a preview that writes nothing); "
        "then ~/.local/bin/agentsync purge --queue"
    )
    _assert_fix_parses(by_hand.fix)
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    quiet = check()
    assert not quiet.ok and quiet.severity is doctor.Severity.WARN and quiet.detail == by_hand.detail
    assert quiet.fix is None and quiet.note == "yours: see WAITING ON YOU"
    assert doctor.format_results([quiet]).endswith("(yours: see WAITING ON YOU)")


def test_purge_queue_skips_a_held_source_and_keeps_it_queued(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field N1: `purge --queue` skips a queued purge on a held source; it stays queued, nothing written."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    assert governance.enqueue_purge(initialised.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    assert cli.main(["hold", "source", "--reason", "matter 7", "--owner", "legal", "--config", cfg]) == 0
    head = git(repo, "rev-parse", "HEAD").strip()
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "0 queued purges run; 1 still queued"
    # R96: the held entry has a report line with its reason, as a kept one does; Q47: "not run", which is
    # what happened, not NOT VERIFIED and run counts of zero
    assert (
        "purge stable-id (operator): not run\n  note: stays queued: purge suspended by legal/records hold: "
        in out
    )
    assert "NOT VERIFIED" not in out and "0 items" not in out and "remote:" not in out
    assert sid not in out
    assert git(repo, "rev-parse", "HEAD").strip() == head and (repo / page).is_file()
    assert [q.selector for q in governance.pending_purges(initialised.state_paths.root)] == [selector]


def test_purge_queue_keeps_an_upstream_deletion_of_a_live_file_and_does_not_count_it_as_run(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """C8: an upstream deletion of a file the manifest lists again stays queued with its reason; the last line
    counts only the entries that ran.  No run clears it while the file is listed, so it is no wait, no doctor
    WARN and no failed exit, only a note (B13 round 2, R49: a WAITING ON YOU line sent the operator to a run
    that exited 1 and cleared nothing, on every sync)."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    reason = governance.PurgeReason.UPSTREAM_DELETED
    assert governance.enqueue_purge(initialised.state_paths.root, selector, reason)
    head = git(repo, "rev-parse", "HEAD").strip()
    kept = governance.kept_purges(1)

    step = loop.next_step(initialised)
    assert not any("queued purge" in w for w in step.waits) and "governance.purge_queue" not in step.checks
    assert kept in step.notes
    lines = cli._status_lines(initialised)
    said = "queued purges kept: 1 (the governance.purge_queue line says why)"  # P118: the sentence once
    assert said in lines and not any(x.startswith("queued purges: ") for x in lines)
    (check,) = [c for c in cli._governance_checks(initialised) if c.name == "governance.purge_queue"]
    assert check.ok and check.severity is doctor.Severity.INFO and check.fix is None and check.detail == kept
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "0 queued purges previewed (dry run, nothing written); 1 queued"
    # Q47: a preview of a kept entry says it is skipped, not "dry run · ... would be removed"
    assert "purge stable-id (upstream-deleted): not run\n  note: stays queued: " in out
    assert "dry run ·" not in out and "would be removed" not in out
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "purge stable-id (upstream-deleted): not run\n  note: stays queued: " in out
    assert "NOT VERIFIED" not in out and "0 items" not in out
    assert out.splitlines()[-1] == "0 queued purges run; 1 stays queued while its file is listed"
    assert sid not in out and "sample" not in out
    assert git(repo, "rev-parse", "HEAD").strip() == head and (repo / page).is_file()
    assert [q.selector for q in governance.pending_purges(initialised.state_paths.root)] == [selector]


def test_a_kept_upstream_deletion_beside_a_runnable_purge_leaves_a_wait_for_the_runnable_one(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """R49, the mixed case: one kept upstream deletion and one held operator purge.  The wait, status and
    doctor count only the entry a run can clear; the run exits 1 for it and names both."""
    cfg = _synced(initialised)
    sid = _stable_id(initialised, "projects/sample.txt")
    root = initialised.state_paths.root
    live = governance.PurgeSelector(stable_id=sid, source_id="source")
    assert governance.enqueue_purge(root, live, governance.PurgeReason.UPSTREAM_DELETED)
    other = governance.PurgeSelector(source_id="source", path_glob="nothing/*")
    assert governance.enqueue_purge(root, other, governance.PurgeReason.OPERATOR)
    assert cli.main(["hold", "source", "--reason", "matter 7", "--owner", "legal", "--config", cfg]) == 0

    step = loop.next_step(initialised)
    assert next(w for w in step.waits if "queued purge" in w).startswith("1 queued purge: ")
    assert governance.kept_purges(1) in step.notes
    lines = cli._status_lines(initialised)
    assert any(x.startswith("queued purges: 1 (") for x in lines) and any(
        x.startswith("queued purges kept: 1 ") for x in lines
    )
    (check,) = [c for c in cli._governance_checks(initialised) if c.name == "governance.purge_queue"]
    assert not check.ok and check.severity is doctor.Severity.WARN
    assert check.detail.startswith("1 purge request queued") and check.detail.endswith(
        governance.kept_purges(1)
    )
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "  note: stays queued: purge suspended by legal/records hold: " in out
    assert out.splitlines()[-1] == "0 queued purges run; 2 still queued (1 of them while its file is listed)"


def test_purge_queue_runs_an_entry_a_purge_of_the_run_already_did_once_and_exits_ok(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """R95: an operator purge then an erasure request for one file.  One purge runs, the second entry is a
    note on its report, the queue ends empty and the run exits 0.  (R95 queued a label escalation first;
    since G30 one of a live file is kept, so an operator purge, never judged, stands in.)"""
    cfg = _synced(initialised)
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    for reason in (governance.PurgeReason.OPERATOR, governance.PurgeReason.ERASURE_REQUEST):
        assert governance.enqueue_purge(initialised.state_paths.root, selector, reason)
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.count(": VERIFIED") == 1
    assert "  note: also did the queued erasure-request purge of the same item: taken off the queue" in out
    assert out.splitlines()[-1] == "1 queued purge run; 0 still queued"
    assert governance.pending_purges(initialised.state_paths.root) == []


def test_purge_queue_preview_counts_a_covered_entry_as_the_run_does(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """Q61 (B13 round 4): the preview previewed an entry an earlier purge of the run covers as a purge of its
    own (2 previewed) while the run ran it once (1 run), against R52's "the same count the real run prints".
    The preview now folds it into the covering report as the run does, and writes nothing."""
    cfg = _synced(initialised)
    root = initialised.state_paths.root
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    for reason in (governance.PurgeReason.OPERATOR, governance.PurgeReason.ERASURE_REQUEST):
        assert governance.enqueue_purge(root, selector, reason)
    queue = governance.pending_purges(root)
    head = git(initialised.docs_repo, "rev-parse", "HEAD").strip()
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "1 queued purge previewed (dry run, nothing written); 2 queued"
    (line,) = [x for x in out.splitlines() if x.startswith("purge ")]
    assert line.startswith("purge stable-id (operator): dry run · ")
    note = "would also do the queued erasure-request purge of the same item: the run takes it off the queue"
    assert f"  note: {note}" in out
    assert governance.pending_purges(root) == queue
    assert git(initialised.docs_repo, "rev-parse", "HEAD").strip() == head
    assert not any(a["action"] == "purge-covered" for a in governance.read_audit(root))
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_OK
    assert capsys.readouterr().out.splitlines()[-1] == "1 queued purge run; 0 still queued"


def test_purge_queue_preview_does_not_keep_a_covered_upstream_deletion(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """F80 (B13 round 5): an upstream deletion of a live file queued behind a label escalation of the same
    file read, in the preview, both "would also do ... the run takes it off the queue" and "not run ... stays
    queued", because the kept check ran before the covered skip on a manifest the preview left as it was.  The
    run takes it off; the preview now says only that.  Since B13 round 6 G30 a label escalation of a live file
    is kept queued itself, so an operator purge (never judged) is the entry ahead of it here."""
    cfg = _synced(initialised)
    root = initialised.state_paths.root
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    for reason in (governance.PurgeReason.OPERATOR, governance.PurgeReason.UPSTREAM_DELETED):
        assert governance.enqueue_purge(root, selector, reason)
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    note = "would also do the queued upstream-deleted purge of the same item: the run takes it off the queue"
    assert f"  note: {note}" in out
    assert "not run" not in out
    assert "stays queued" not in out
    assert out.splitlines()[-1] == "1 queued purge previewed (dry run, nothing written); 2 queued"
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_OK
    assert capsys.readouterr().out.splitlines()[-1] == "1 queued purge run; 0 still queued"
    assert governance.pending_purges(root) == []


def test_purge_queue_preview_does_not_count_a_kept_upstream_deletion_as_previewed(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 R52: the preview counts an entry the run would keep queued (an upstream deletion of a live file) as
    the run does: not previewed, still queued."""
    cfg = _synced(initialised)
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    assert governance.enqueue_purge(
        initialised.state_paths.root, selector, governance.PurgeReason.UPSTREAM_DELETED
    )
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "  note: stays queued: " in out
    assert out.splitlines()[-1] == "0 queued purges previewed (dry run, nothing written); 1 queued"


def test_setup_feedback_reads_the_purge_preview_last_line_as_the_cli_prints_it() -> None:
    """B13 R58: the reviewer's guide quotes the preview's and the run's last lines as cli._cmd_purge prints
    them, and says the preview's queued count is the whole queue (a preview leaves it as it is)."""
    page = " ".join((Path(__file__).parents[1] / "docs/deploy/setup-feedback.md").read_text("utf-8").split())
    assert "the last line counts what stays queued" not in page
    assert "`N queued purges previewed (dry run, nothing written); M queued`" in page
    assert "M is every queued entry" in page
    assert "`N queued purges run; M still queued`" in page


def test_purge_queue_dry_run_previews_a_held_source_and_writes_nothing(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field N1: `purge --queue --dry-run` with a held source previews nothing, exits 0, keeps the queue."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    sid = _stable_id(initialised, "projects/sample.txt")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    assert governance.enqueue_purge(initialised.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    assert cli.main(["hold", "source", "--reason", "matter 7", "--owner", "legal", "--config", cfg]) == 0
    head = git(repo, "rev-parse", "HEAD").strip()
    capsys.readouterr()
    assert cli.main(["purge", "--queue", "--dry-run", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "0 queued purges previewed (dry run, nothing written); 1 queued"
    assert "  note: stays queued: purge suspended by legal/records hold: " in out and sid not in out
    assert git(repo, "rev-parse", "HEAD").strip() == head and (repo / page).is_file()
    assert [q.selector for q in governance.pending_purges(initialised.state_paths.root)] == [selector]


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
    assert "1 active hold" in capsys.readouterr().out
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
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """KISS K08a: status prints the effective policy; ``policy show`` is a hidden alias of status; a broken
    policy is a FAIL there (rc 1) and still a configuration error for sync."""
    # the two outputs are compared whole, and the disk lines print free space to 0.1 GiB: other work on the
    # machine moves the real figure between the two calls
    monkeypatch.setattr(doctor, "_disk_free", lambda _anchor: 8 << 30)
    cfg = str(initialised.config_path)
    guid = "2096f6a2-d2f7-48be-b329-b73aaa526e5d"
    with initialised.config_path.open("a", encoding="utf-8") as fh:
        fh.write(f'\n[policy]\nexclude_label_ids = ["{guid.upper()}"]\n')
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"  exclude_label_ids: {guid}" in out and "  labels_active: true" in out
    assert cli.main(["policy", "show", "--config", cfg]) == cli.EXIT_OK
    cap = capsys.readouterr()

    # the free-space figure moves between the two calls when other work writes to the disk
    def free(text: str) -> str:
        return re.sub(r"\d+\.\d GiB", "N GiB", text)

    assert free(cap.out) == free(out) and "renamed: run ~/.local/bin/agentsync status" in cap.err
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


def _docs_repo_line(out: str) -> str:
    (line,) = [ln for ln in out.splitlines() if ln.startswith("docs repo ")]
    return line


def test_the_docs_repo_line_names_the_remotes_git_has(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 17 U14, U17: init's line said "(exists, no remote; ...)" one line above the refusal of that
    repo's remote, and also over a remote [governance] allows. The clause is read from git: the remote's
    name, no URL, and whether [governance] refuses or allows it; a repo with none keeps "no remote"."""
    cfg = str(initialised.config_path)
    repo = initialised.docs_repo
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_OK
    assert "(exists, no remote; scaffold up to date)" in _docs_repo_line(capsys.readouterr().out)
    git(repo, "remote", "add", "origin", "https://git.contoso.example/docs.git")
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_FAILED
    cap = capsys.readouterr()
    line = _docs_repo_line(cap.out)
    assert "(exists, remote origin, refused by [governance]; scaffold up to date)" in line, line
    assert "no remote" not in line and "contoso.example" not in line, line
    assert "allow_remote = false" in cap.err
    git(repo, "remote", "add", "backup", "https://git.contoso.example/backup.git")
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_FAILED
    assert "(exists, remotes backup, origin, refused by [governance];" in _docs_repo_line(
        capsys.readouterr().out
    )
    git(repo, "remote", "remove", "backup")
    text = initialised.config_path.read_text(encoding="utf-8")
    initialised.config_path.write_text(
        text
        + '\n[governance]\nallow_remote = true\nremote_url_prefixes = ["https://git.contoso.example/"]\n',
        encoding="utf-8",
    )
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_OK
    cap = capsys.readouterr()
    assert "(exists, remote origin, allowed by [governance]; " in _docs_repo_line(cap.out), cap.out
    assert "governance:" not in cap.err
    # B13 round 18 V58: one remote allowed, one refused: the line names each, never both as refused
    git(repo, "remote", "set-url", "origin", "https://git.contoso.example/team/docs.git")
    git(repo, "remote", "add", "backup", "https://backup.contoso.example/docs.git")
    text = initialised.config_path.read_text(encoding="utf-8")
    initialised.config_path.write_text(
        text.replace('["https://git.contoso.example/"]', '["https://git.contoso.example/team/"]'), "utf-8"
    )
    assert cli.main(["init", "--config", cfg]) == cli.EXIT_FAILED
    cap = capsys.readouterr()
    line = _docs_repo_line(cap.out)
    assert "(exists, remotes backup, origin: backup refused by [governance], origin allowed; " in line, line
    assert "contoso.example" not in line, line
    assert "remote backup " in cap.err and "remote origin " not in cap.err, cap.err


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
    monkeypatch.setattr(
        doctor, "run_checks", lambda config, *, tcc_canary=True, loop_checks=None, loop_waits=None: list(fake)
    )
    monkeypatch.setattr(cli, "_extra_checks", lambda config, *, offline=False: [])
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED
    assert [ln for ln in out if ln.startswith("[")] == doctor.format_results(fake).splitlines()
    assert out[0] == "NEXT: the fake.check check failed: do what the fix on its [FAIL] line below says"
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda config, *, tcc_canary=True, loop_checks=None, loop_waits=None: [fake[0], fake[2]],
    )
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
    monkeypatch.setattr(
        doctor, "run_checks", lambda config, *, tcc_canary=True, loop_checks=None, loop_waits=None: list(fake)
    )
    rc, out = _status(cfg, capsys)
    nexts = [ln for ln in out if ln.startswith(("NEXT:", "WAITING ON YOU:"))]
    # the sign-in the agent runs comes before the person's sentinel (B13 round 14 Q1)
    assert rc == cli.EXIT_FAILED and nexts[0].startswith("NEXT: the graph.auth check failed: ")
    assert not any("/" in ln or re.search(r"(^|[\s`])agentsync ", ln) for ln in nexts), nexts
    assert any(ln.endswith(f"(fix: {sentinel})") for ln in out), "the [FAIL] line still carries the fix"


@pytest.mark.parametrize("blocking", [True, False])
def test_status_under_install_says_a_fail_stops_the_sync_a_held_listing_waits_for(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], blocking: bool
) -> None:
    """B13 round 14 Q10: under install.sh (AGENTSYNC_NO_NEXT_HINT=1) a held listing's [warn] said "the end of
    this run says whether this run's sync cleared it" while a [FAIL] beside it skipped that sync, and the end
    said nothing. With a [FAIL] that stops the sync the note says so; the launcher's TCC_PENDING, which does
    not, keeps the note; the setup report reads its own note for both."""
    cfg = _synced(initialised)
    capsys.readouterr()
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    warn = doctor.CheckResult(
        "heartbeat.x",
        False,
        "incomplete; deletions held",
        doctor.Severity.WARN,
        note=doctor._LISTING_YOURS_NOTE,
    )
    fail = (
        doctor.CheckResult(
            "source.x.listable", False, "gone", doctor.Severity.ERROR, fix='point [[source]] "x"'
        )
        if blocking
        else doctor.CheckResult(
            "tcc.x", False, "TCC_PENDING: waiting", doctor.Severity.ERROR, fix="click Allow"
        )
    )
    monkeypatch.setattr(doctor, "run_checks", lambda config, **_: [warn, fail])
    monkeypatch.setattr(cli, "_extra_checks", lambda config, *, offline=False: [])
    rc, out = _status(cfg, capsys)
    [line] = [ln for ln in out if ln.startswith("[warn] heartbeat.x")]
    note = doctor._LISTING_SKIPPED_NOTE if blocking else doctor._LISTING_YOURS_NOTE
    assert rc == cli.EXIT_FAILED and line.endswith(f"({note})"), line
    assert ("whether it cleared it" in line) is not blocking
    # B13 round 15 S28: the [FAIL] may be about another folder, so no claim of what releases it, one "held"
    assert line.count("held") == 1 and "until" not in line, line
    # the report's own path, with the [FAIL] beside it (B13 round 15 S43: no faked _status_checks)
    [line] = [ln for ln in cli._report_hooks().doctor(initialised) if ln.startswith("[warn] heartbeat.x")]
    assert line.endswith(f"({doctor.LISTING_YOURS_REPORT_NOTE})"), line


@pytest.mark.parametrize("kind", ["missing", "file"])
@pytest.mark.parametrize("job", [False, True])
def test_a_sync_logs_an_alarm_its_report_prints_only_in_the_background_job(
    initialised: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    job: bool,
) -> None:
    """B13 round 15 S2, S13: a sync over a missing source root printed a timestamped "WARNING
    agentsync.arm_local: <id>: source root missing: ..." above its run line, the same text as that source's
    alarm line, which the prompt does not describe. A run that prints its report drops it from stderr; the
    LaunchAgent's job keeps it, since its stderr is the err.log whose WARNING lines the setup report's
    Recent errors lists and no out.log alarm line matches."""
    cfg = _synced(initialised)
    capsys.readouterr()
    moved = local_source_dir.with_name("moved")
    local_source_dir.rename(moved)
    if kind == "file":  # another of _incomplete's alarms
        local_source_dir.write_text("not a folder\n", encoding="utf-8")
    alarm = "source root missing: " if kind == "missing" else "source root is not a directory: "
    if job:
        monkeypatch.setenv("XPC_SERVICE_NAME", f"{initialised.launchd_label_prefix}.sync")
    cli.main(["sync", "--config", cfg] + (["--mode", "poll"] if job else []))
    out, err = capsys.readouterr()
    assert any(ln.startswith(f"    alarm: {alarm}") for ln in out.splitlines()), out
    warned = [ln for ln in err.splitlines() if " WARNING agentsync.arm_local: " in ln and alarm in ln]
    assert len(warned) == int(job), err
    if job:
        logs = expand(initialised.log_dir)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"{initialised.launchd_label_prefix}.sync.err.log").write_text(err, encoding="utf-8")
        run = setup_report._Run(initialised.config_path, setup_report.ReportHooks(), 30.0)
        assert any(alarm in ln for ln in setup_report._recent_errors(run)), "Recent errors lists it"


@pytest.mark.parametrize("job", [False, True])
def test_a_folder_a_sync_cannot_list_is_warned_only_in_the_background_job(
    initialised: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    job: bool,
) -> None:
    """B13 round 16 T37: a foreground sync over a folder it cannot list (chmod 000) printed a timestamped
    "WARNING agentsync.arm_local: <id>: 1 folder could not be listed: ..." on stderr beside the alarm line
    naming the same folder with the same reason. The report's alarm carries it, so a run that prints the
    report holds the WARNING back; the LaunchAgent's job keeps it in its err.log."""
    locked = local_source_dir / "Locked"
    locked.mkdir()
    (locked / "inner.txt").write_text("x\n", encoding="utf-8")
    cfg = str(initialised.config_path)
    capsys.readouterr()
    if job:
        monkeypatch.setenv("XPC_SERVICE_NAME", f"{initialised.launchd_label_prefix}.sync")
    locked.chmod(0)
    try:
        cli.main(["sync", "--config", cfg] + (["--mode", "poll"] if job else []))
    finally:
        locked.chmod(0o755)
    out, err = capsys.readouterr()
    assert any("could not be read: 'Locked' (EACCES" in ln for ln in out.splitlines()), out
    warned = [
        ln for ln in err.splitlines() if "WARNING agentsync.arm_local" in ln and "could not be listed" in ln
    ]
    assert len(warned) == int(job), err


@pytest.mark.parametrize(("printed", "job"), [(True, False), (False, False), (True, True)])
def test_a_cycle_warning_the_report_prints_is_held_back_only_when_it_prints_it(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    printed: bool,
    job: bool,
) -> None:
    """B13 round 16 T33, T56: a foreground sync printed raw "<time> WARNING agentsync.cycle: ..." lines for
    the OCR or media helper going down, a held checkpoint and a [policy] re-screen, beside the alarm, the
    "checkpoint held" line and the run line's re-screen count saying the same. Each is held back when the
    report prints its words, and logged when it does not (an OCR helper whose alarm no source got, a
    re-screen still waiting); the LaunchAgent's job keeps them all in its err.log."""
    cfg = str(initialised.config_path)
    marked = {
        cycle_mod._OCR_DOWN: "alarm",
        cycle_mod._MEDIA_DOWN: "alarm",
        "checkpoint held: 2 curation errors": "checkpoint",
        "[policy] changed: 3 label-capable files queued for a re-screen": "rescreen",
    }

    def fake(config: Config, **_: object) -> CycleReport:
        for message, when in marked.items():
            logging.getLogger(cycle_mod.__name__).warning("%s", message, extra={cycle_mod.ECHOED_WHEN: when})
        alarms = (cycle_mod._OCR_DOWN, cycle_mod._MEDIA_DOWN) if printed else ()
        src = SourceReport("x", SourceKind.LOCAL, PassKind.FULL, True, alarms=alarms)
        return CycleReport(
            1,
            CycleMode.POLL,
            (src,),
            (),
            None,
            checkpoint="held" if printed else None,
            # a held checkpoint always has a blocker (cycle.py holds it only then), as many as the logged
            # line says: "0 curation errors" was a line no run prints (B13 round 17 U70)
            checkpoint_blockers=tuple(
                LintFinding("FRONTMATTER", f"topics/contoso-{n}.md", "no title") for n in (1, 2)
            )
            if printed
            else (),
            rescreened=3 * printed,
        )

    monkeypatch.setattr(cli, "run_cycle", fake)
    if job:
        monkeypatch.setenv("XPC_SERVICE_NAME", f"{initialised.launchd_label_prefix}.sync")
    capsys.readouterr()
    cli.main(["sync", "--config", cfg] + (["--mode", "poll"] if job else []))
    out, err = capsys.readouterr()
    warned = [ln for ln in err.splitlines() if " WARNING agentsync.cycle: " in ln]
    if printed:
        assert f"    alarm: {cycle_mod._OCR_DOWN}" in out.splitlines(), out
        assert (
            "checkpoint held: 2 curation errors; fix them" in out
            and "; re-screened 3 for a rule change" in out
        )
    assert len(warned) == (4 if job or not printed else 0), err
    assert all(any(ln.endswith(m) for ln in warned) for m in marked) is (job or not printed), err
    # a re-screen count that is other files' (a secret-rule re-screen) while the [policy] change's files still
    # wait says nothing of them: the [policy] line is logged (review)
    record = logging.LogRecord(cycle_mod.__name__, logging.WARNING, "", 0, "x", None, None)
    setattr(record, cycle_mod.ECHOED_WHEN, "rescreen")
    report = CycleReport(1, CycleMode.POLL, (), (), None, rescreened=2, rescreen_left=40)
    assert not cli._printed(record, report)
    assert cli._printed(record, dataclasses.replace(report, rescreen_left=0))


def test_a_cycle_that_raises_still_logs_the_alarms_it_held(
    initialised: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 round 15 S2, S13: the held WARNING is dropped only because the report prints its alarm; a cycle
    that raises prints no report, so the record is logged after all."""
    cfg = _synced(initialised)
    capsys.readouterr()
    shutil.rmtree(local_source_dir)

    def boom(config: Config, **kwargs: object) -> CycleReport:
        logging.getLogger("agentsync.arm_local").warning(
            "x: source root missing: gone", extra={arm_local.ECHOED_ALARM: True}
        )
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_cycle", boom)
    with pytest.raises(RuntimeError):
        cli.main(["sync", "--config", cfg])
    assert "WARNING agentsync.arm_local: x: source root missing: gone" in capsys.readouterr().err


# no catalog of kinds since B13 round 23 A31: the [FAIL] line below states the one fix
PERSONS_STEP = "its fix on the [FAIL] line below is the person's, not a step for the agent: show it to them"


def test_the_persons_kinds_name_every_state_refusal_no_chmod_fixes(tmp_path: Path) -> None:
    """B13 round 22 Z65, Z83: an ACL on this account's own 0600 manifest got a NEXT whose kinds named only "a
    state file that is damaged or another account's", above a fix for neither. Each state fix round 21 made
    the person's (an ACL or a privacy setting on a file or on the folder, a folder above it that refuses a
    search, a read-only volume) is the person's, as is a manifest that is not a file, and the NEXT's kinds
    name the state folder and each of them (lane B's words)."""
    kind = (
        "a state file that is damaged, missing after earlier syncs, another account's or not a file, a "
        "state folder that is not a folder, or a state file or folder that an ACL, a privacy setting, a "
        "folder above it or a read-only volume refuses"
    )
    assert kind in doctor.PERSONS_KINDS
    root = tmp_path / "state"
    root.mkdir()
    for fix in (
        doctor.MANIFEST_FIX,
        doctor._search_fix(root / "manifest.sqlite", folder=False),
        doctor._search_fix(root),
        doctor._parent_fix(root),
        doctor.read_only_fix(root),
    ):
        assert doctor.is_persons_fix(fix), fix
        step = cli._fail_step(doctor.CheckResult("state_dir", False, "refused", fix=fix))
        assert step == f"the state_dir check failed: {PERSONS_STEP}", step  # no kinds in the NEXT (A31)


def test_a_docs_repo_remote_governance_does_not_allow_is_the_persons_in_status(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 17 U2, U16: a remote added to the docs repo that [governance] does not allow got status's
    "do what the fix on its [FAIL] line below says", and the line ended "run `git -C ... remote remove
    origin`": the agent was sent to delete a remote the person may have set up on purpose. The fix is the
    person's (``doctor.is_persons_fix``), the NEXT names it so, and no line tells anyone to "run" the
    removal."""
    cfg = str(initialised.config_path)
    git(initialised.docs_repo, "remote", "add", "origin", "https://git.contoso.example/team/kb.git")
    assert doctor.is_persons_fix(cli.REMOTE_FIX)
    findings = cli._governance_checks(initialised)
    [remote] = [c for c in findings if c.name == "governance.remote"]
    assert not remote.ok and remote.fix == cli.REMOTE_FIX
    assert cli._fail_step(remote).startswith(f"the governance.remote check failed: {PERSONS_STEP}")
    cli.main(["status", "--config", cfg])
    out = capsys.readouterr().out
    assert "[FAIL] governance.remote" in out and "the person removes it (git -C " in out, out
    assert "run `git" not in out and "do what the fix on its [FAIL] line below says" not in out, out


@pytest.mark.parametrize(
    ("name", "where", "table", "detail"),
    [
        (
            "policy",
            "policy.toml",
            '[policy]\nrefuse_unlabelled = "yes"\n',
            "refuse_unlabelled must be true or false",
        ),
        ("governance.config", "sources.toml", "[governance]\nhold = true\n", "hold = true needs hold_reason"),
    ],
)
def test_a_policy_or_governance_value_that_does_not_load_is_the_persons_in_status(
    initialised: Config,
    capsys: pytest.CaptureFixture[str],
    name: str,
    where: str,
    table: str,
    detail: str,
) -> None:
    """B13 round 16 T2, T9: a [policy] value in the compliance-owned policy.toml or a [governance] value in
    sources.toml that does not load failed status with "fix: fix [policy] / policy.toml" or "fix: fix
    [governance]", and status's NEXT sent the agent to do it, while step 2's NEXT for the same config error
    says it is the person's. The fix now starts with the person's words, so status's NEXT names it as
    theirs. (A bad [policy] table in sources.toml never reaches this check: the config does not load.)"""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    target = initialised.config_path.parent / where
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(f"{old}\n{table}", encoding="utf-8")
    capsys.readouterr()
    rc = cli.main(["status", "--config", cfg])
    cap = capsys.readouterr()
    out = cap.out.splitlines()
    fails = [ln for ln in out if ln.startswith(f"[FAIL] {name} ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1, (out, cap.err)
    assert f"{where}: [" in fails[0] and detail in fails[0], fails
    assert fails[0].endswith(f"(fix: {cli.POLICY_FIX if name == 'policy' else cli.GOVERNANCE_FIX})"), fails
    assert out[0] == f"NEXT: the {name} check failed: {PERSONS_STEP}", out[0]
    assert "do what the fix" not in "\n".join(out)


def test_a_manifest_that_is_no_database_fails_status_and_stops_setup_on_one_line(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 16 T12: with manifest.sqlite overwritten by bytes that are no database, status and doctor
    exited 0 with every check [ok] and "NEXT: cannot read the state: DatabaseError: ...", while init,
    add-source and sync ended on a traceback (exit 1). Doctor's state_dir.manifest check now fails, the
    person's, so status exits 1 and its NEXT names that check; the commands print one line naming the file
    and its fix and exit 65, which install.sh reads as the person's."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    db.write_bytes(os.urandom(4096))
    for name in ("-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    fails = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1, out
    assert fails[0].startswith("[FAIL] state_dir.manifest "), fails
    assert f" — {db} cannot be read: file is not a database (fix: " in fails[0], fails
    assert fails[0].endswith(f"(fix: {doctor.MANIFEST_FIX})") and doctor.is_persons_fix(doctor.MANIFEST_FIX)
    assert out[0] == f"NEXT: the state_dir.manifest check failed: {PERSONS_STEP}", out[0]
    assert cli.main(["doctor", "--config", cfg]) == cli.EXIT_FAILED
    capsys.readouterr()
    second = tmp_path / "beta"
    second.mkdir()
    # B13 round 17 U66: exclude-empty, curate and purge, which no _state_readable wraps, printed only "error:
    # DatabaseError: file is not a database" and exited 1, while --help says 65 names the file and the fix
    src_id = initialised.sources[0].id
    for argv in (
        ["init"],
        ["add-source", str(second)],
        ["sync"],
        ["exclude-empty", src_id],
        ["curate"],
        [
            "purge",
            "path=*",
        ],
    ):
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_STATE, (argv, capsys.readouterr())
        err = capsys.readouterr().err
        line = f"state: {db} cannot be read: file is not a database (fix: {doctor.MANIFEST_FIX})"
        assert line in err.splitlines() and "Traceback" not in err, (argv, err)


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file of any mode")
def test_a_manifest_this_account_cannot_open_fails_state_dir_files_and_the_report_points_at_it(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 19 W64: with manifest.sqlite at mode 0000 status exited 0 with ``[ok] state_dir.files —
    manifest.sqlite are 0600`` and no state_dir.manifest line (it judges only a file that is no database or a
    damaged one), while the setup report sent the person to "Doctor's state_dir.manifest line". The file
    check fails on this account's access, status exits 1 and its NEXT names that check, and the report's
    pointer names lines status printed."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    for name in ("-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    db.chmod(0)
    try:
        capsys.readouterr()
        rc = cli.main(["status", "--config", cfg])
        said = capsys.readouterr()
        out = said.out.splitlines()
        # B13 round 20 X16, X30: no timestamped WARNING above NEXT repeats what the FAIL line says
        assert "WARNING" not in said.err and "cannot read the loop state" not in said.err, said.err
        assert out[0].startswith("NEXT: the state_dir.files check failed"), out[0]
        # B13 round 20 X47: the reason is kept
        reason = "status: cannot read the state: a file cannot be opened: Permission denied; "
        reason += "the state_dir lines say why"
        assert reason in out, out
        fails = [ln for ln in out if ln.startswith("[FAIL] ")]
        assert rc == cli.EXIT_FAILED and len(fails) == 1, out
        assert fails[0].startswith("[FAIL] state_dir.files "), fails
        assert "this account cannot read and write it: manifest.sqlite (0000)" in fails[0], fails
        assert fails[0].endswith(f"(fix: chmod 600 {doctor._shell_path(db)})"), fails
        assert "state_dir.files" in out[0] and out[0].startswith("NEXT: "), out[0]
        assert not any("are 0600" in ln or "is 0600" in ln for ln in out), out
        printed = {m.group(1) for ln in out if (m := re.match(r"\[(?:ok  |FAIL|warn|info)\] (\S+)", ln))}
        capsys.readouterr()
        text, _red = setup_report.build_report(initialised.config_path, hooks=cli._report_hooks())
        err = capsys.readouterr().err
        assert "cannot read the loop state" not in err, err  # X16, X30: not even once
        pointers = re.findall(r"see Doctor's (state_dir(?:\.\w+)?) lines?\b", text)
        assert pointers and "state_dir.manifest" not in pointers, pointers
        assert all(any(name.startswith(p) for name in printed) for p in pointers), (pointers, printed)
    finally:
        db.chmod(0o600)
    rc, out = _status(cfg, capsys)
    assert any(re.match(r"\[ok  \] state_dir\.files +— manifest\.sqlite is 0600", ln) for ln in out), out


def test_a_failed_loop_read_with_no_fail_still_logs_its_warning(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other side of X16, X30: with no [FAIL] line to say why, a loop read that fails is logged as before,
    once: it is the only explanation of a missing NEXT."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK

    def unreadable(*_a: object, **_k: object) -> loop.NextStep:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(loop, "next_step", unreadable)
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    err = capsys.readouterr().err
    assert err.count("WARNING agentsync.loop: next step: cannot read the loop state") == 1, err


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
@pytest.mark.parametrize("hint", ["by hand", "install.sh"])
def test_a_listing_warn_over_a_state_file_this_account_cannot_open_points_at_no_waiting_line(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], hint: str
) -> None:
    """B13 round 20 X15: with manifest.sqlite at 0000, status's incomplete-listing warn said "each WAITING ON
    YOU line about <source> says ..." (by hand) or "a [FAIL] line stops this run's sync ...; status names it"
    (install.sh) while status printed no WAITING ON YOU line and named nothing: W11 keyed only a damaged
    file. Keyed on the loop read's own error, the warn gets the shut-state note and no fix; a state_dir.files
    FAIL beside a loop read that succeeded keeps the warn's own words."""
    if hint == "install.sh":
        monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    else:
        monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    beats = initialised.state_paths.heartbeat
    data = json.loads(beats.read_text(encoding="utf-8"))
    sid = _local_id(initialised)
    data[sid]["consecutive_incomplete"] = 3
    beats.write_text(json.dumps(data), encoding="utf-8")
    db = initialised.state_paths.db
    for name in ("-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    db.chmod(0)
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
    finally:
        db.chmod(0o600)
    assert rc == cli.EXIT_FAILED, out
    assert next(ln for ln in out if ln.startswith("[FAIL] ")).startswith("[FAIL] state_dir.files "), out
    (warn,) = [ln for ln in out if ln.startswith(f"[warn] heartbeat.{sid} ")]
    assert warn.endswith(f"({doctor.LISTING_STATE_SHUT_NOTE})"), warn
    assert "WAITING ON YOU" not in warn and "status names it" not in warn and "(fix: " not in warn, warn
    # a loose mode FAILs state_dir.files too, but the loop reads the file (its own open sets the mode back
    # to 0600 before the checks run, so only the function shows it): the warn keeps its words
    loose = doctor.CheckResult(
        "state_dir.files", False, "readable by others: manifest.sqlite (0644)", fix="chmod"
    )
    listing = doctor.CheckResult(
        f"heartbeat.{sid}", False, f"{doctor.LISTING_INCOMPLETE}3 consecutive passes"
    )
    assert cli._listings_unread([loose, listing], None) == [loose, listing]


def test_the_manifest_fix_says_legal_holds_are_kept(initialised: Config) -> None:
    """B13 round 17 U73: MANIFEST_FIX said moving the manifest aside forgets "the deletions, holds and archive
    it recorded", but legal holds live in governance/holds.json, outside the manifest, and survive a new one;
    what it loses is the deletion breaker's suspended removals. The fix says both, and stays the person's."""
    fix = doctor.MANIFEST_FIX
    holds = governance._holds_file(expand(initialised.state_dir))
    assert holds.parent.parent == expand(initialised.state_paths.db).parent  # beside the manifest, not in it
    assert f"legal holds are kept, in {holds.parent.name}/{holds.name}" in fix, fix
    assert "the deletion breaker's suspended removals" in fix and "deletions, holds and" not in fix, fix
    assert doctor.is_persons_fix(fix)


def _raise_sqlite(kind: str, where: Path) -> None:
    """Raise a real sqlite3 error of ``kind`` from a scratch database at ``where`` (not the manifest)."""
    conn = sqlite3.connect(where, isolation_level=None, timeout=0)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY)")
    if kind == "locked":
        holder = sqlite3.connect(where, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            conn.execute("SELECT count(*) FROM t")
        finally:
            holder.execute("ROLLBACK")
            holder.close()
    elif kind == "integrity":
        conn.execute("INSERT OR IGNORE INTO t VALUES ('a')")
        conn.execute("INSERT INTO t VALUES ('a')")
    else:
        conn.execute("SELECT missing FROM t")


@pytest.mark.parametrize("kind", ["locked", "integrity", "no-such-column"])
def test_only_a_locked_or_busy_database_is_one_line_other_sqlite_errors_keep_their_traceback(
    initialised: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    """B13 round 17 U72: round 16 T12's catch-all printed every sqlite3 error as one line and exit 1, its
    traceback at DEBUG only, so a code defect (an IntegrityError, a missing column) left no stack in a
    LaunchAgent's err.log. Only a locked or busy database (SQLITE_BUSY, SQLITE_LOCKED) is one line now; any
    other error propagates with its traceback, as before T12."""
    scratch = tmp_path / "scratch.sqlite"

    def handler(_args: object) -> int:
        _raise_sqlite(kind, scratch)
        return cli.EXIT_OK

    monkeypatch.setattr(cli, "_cmd_curate", handler)
    argv = ["curate", "--config", str(initialised.config_path)]
    if kind == "locked":
        assert cli.main(argv) == cli.EXIT_FAILED
        err = capsys.readouterr().err.splitlines()
        assert err == ["error: OperationalError: database is locked"], err
    else:
        with pytest.raises(sqlite3.IntegrityError if kind == "integrity" else sqlite3.OperationalError):
            cli.main(argv)


@pytest.mark.parametrize(
    ("code", "said"),
    [
        (sqlite3.SQLITE_FULL, "database or disk is full"),
        (sqlite3.SQLITE_READONLY, "attempt to write a readonly database"),
        (sqlite3.SQLITE_IOERR, "disk I/O error"),
        (sqlite3.SQLITE_CANTOPEN, "unable to open database file"),
        (sqlite3.SQLITE_IOERR | (13 << 8), "disk I/O error"),  # an extended code: IOERR_FSYNC
    ],
    ids=["full", "readonly", "ioerr", "cantopen", "ioerr-extended"],
)
def test_a_full_read_only_or_failing_disk_is_one_line_with_no_traceback(
    initialised: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: int,
    said: str,
) -> None:
    """B13 round 18 V59: U72 kept every sqlite3 error but a locked or busy one as a traceback, so a full disk,
    a read-only or failing volume, or a file that will not open escaped main as a raw traceback. Those codes
    (the Mac's, not the code's: an allowlist, extended codes included) are one ``error:`` line and exit 1,
    the traceback at -vv; a code no one listed still raises (the integrity and missing-column cases above)."""

    def handler(_args: object) -> int:
        exc = sqlite3.OperationalError(said)
        exc.sqlite_errorcode = code  # type: ignore[attr-defined]
        raise exc

    monkeypatch.setattr(cli, "_cmd_curate", handler)
    assert cli.main(["curate", "--config", str(initialised.config_path)]) == cli.EXIT_FAILED
    err = capsys.readouterr().err.splitlines()
    assert err == [f"error: OperationalError: {said}"], err
    assert doctor.environmental(sqlite3.OperationalError()) is False
    misuse = sqlite3.OperationalError("bad parameter or other API misuse")
    misuse.sqlite_errorcode = sqlite3.SQLITE_MISUSE  # type: ignore[attr-defined]
    assert not doctor.environmental(misuse), "a code no one listed keeps its traceback"


def test_status_calls_a_locked_state_file_busy_as_the_report_does(tmp_path: Path) -> None:
    """B13 round 18 V60: status's words for a locked manifest were "the state file cannot be read (database is
    locked)" while the report said busy. Both test the code (``doctor.contended``), and status names no sync:
    the holder can be another status migrating the schema."""
    from agentsync import setup_report  # noqa: PLC0415

    scratch = tmp_path / "scratch.sqlite"
    with pytest.raises(sqlite3.OperationalError) as locked:
        _raise_sqlite("locked", scratch)
    assert locked.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert cli._unread_words(locked.value) == (
        "the state file is busy: another agentsync run holds it; try again when it ends"
    )
    coded = sqlite3.OperationalError("a message that names no lock")
    coded.sqlite_errorcode = sqlite3.SQLITE_LOCKED  # type: ignore[attr-defined]
    assert cli._unread_words(coded).startswith("the state file is busy: ")
    assert setup_report._why_not_read(coded).startswith("the state file is busy")
    malformed = sqlite3.DatabaseError("database disk image is malformed")
    malformed.sqlite_errorcode = sqlite3.SQLITE_CORRUPT  # type: ignore[attr-defined]
    assert cli._unread_words(malformed) == "the state file cannot be read: database disk image is malformed"


def _damage_table_root(db: Path, table: str) -> int:
    """Overwrite the first 64 bytes of ``table``'s root page with 0xff, after a checkpoint, leaving the header
    and the schema intact; returns the page number."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        (root,) = conn.execute("SELECT rootpage FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        (size,) = conn.execute("PRAGMA page_size").fetchone()
    finally:
        conn.close()
    for name in ("-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    with db.open("r+b") as f:
        f.seek((root - 1) * size)
        f.write(b"\xff" * 64)
    return int(root)


def _damage_table_leaf(db: Path, table: str, *, last: bool = False) -> int:
    """Write junk over a leaf page of ``table`` that is not its root, after a checkpoint, leaving the header,
    the schema and the root intact; returns the page number. The root must be an interior page (enough
    rows); its first child is found by reading the b-tree page itself, as the macOS sqlite3 has no dbstat
    (B13 round 18 V42: the root-page damage above never reached a leaf below an interior page). ``last``:
    the right-most leaf instead, which a read of the table's first row never reaches (B13 round 19 W41)."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        (root,) = conn.execute("SELECT rootpage FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        (size,) = conn.execute("PRAGMA page_size").fetchone()
    finally:
        conn.close()
    for name in ("-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    data = db.read_bytes()
    page = int(root)
    while True:  # down the left-most child pointers to a leaf
        at = (page - 1) * size + (100 if page == 1 else 0)
        kind = data[at]
        if kind == 0x0D:  # a table leaf
            break
        assert kind == 0x05, f"page {page}: not a table b-tree page ({kind:#x})"
        if last:  # the interior header's right-most pointer
            page = int.from_bytes(data[at + 8 : at + 12], "big")
            continue
        first = int.from_bytes(data[at + 12 : at + 14], "big")  # the first cell's offset in its page
        page = int.from_bytes(data[(page - 1) * size + first : (page - 1) * size + first + 4], "big")
    assert page != root, "the table fits on its root page: no leaf below it"
    with db.open("r+b") as f:
        f.seek((page - 1) * size)
        f.write(os.urandom(size))
    return page


@pytest.mark.parametrize("where", ["root", "leaf", "last leaf"])
def test_a_manifest_damaged_under_a_table_status_reads_fails_status(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """B13 round 17 U64, U95: with the items table's root page damaged under an intact header and schema,
    status exited 0 with every check [ok] and "NEXT: cannot read the state: DatabaseError: database disk
    image is malformed", and so did install.sh's doctor, while the sync then exited 65. Status's own read of
    the loop's state hits the damage: its SQLITE_CORRUPT now FAILs state_dir.manifest, the person's, once.
    Round 18 V42: the same for a leaf page below an interior root (enough files that items needs one), which
    the root-page case never reached; the sync exits 65 on it too. B13 round 20 X51: since round 19 W41's
    probe reads each table's first row, those two pages FAIL through the probe, and the loop read's own FAIL
    (``cli._with_damaged_manifest``) had no test: the last leaf is one the probe misses and status's loop read
    hits, with no sync run between, so that FAIL is the loop read's alone."""
    cfg = str(initialised.config_path)
    if where != "root":
        [src] = [s for s in initialised.sources if s.kind is SourceKind.LOCAL]
        assert src.path is not None
        bulk = expand(src.path) / "bulk"
        bulk.mkdir()
        for i in range(160):
            (bulk / f"note-{i:03d}.txt").write_text(f"Contoso note {i}.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    if where != "root":
        _damage_table_leaf(db, "items", last=where == "last leaf")
    else:
        assert _damage_table_root(db, "items") > 1
    if where == "last leaf":  # only the loop read can find it: no probe and no record says it
        assert doctor.manifest_unreadable(db) is None and doctor.recorded_unreadable(db) is None
    else:  # B13 round 19 W41: the probe reads each table's first row, so it no longer reads past either page
        assert doctor.manifest_unreadable(db) is not None
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    fails = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1, out
    assert fails[0].startswith("[FAIL] state_dir.manifest ") and f" — {db} cannot be read: " in fails[0], (
        fails
    )
    assert fails[0].endswith(f"(fix: {doctor.MANIFEST_FIX})"), fails
    assert out[0] == f"NEXT: the state_dir.manifest check failed: {PERSONS_STEP}", out[0]
    assert "cannot read the state" not in out[0]
    names = [ln.split("]", 1)[1].split()[0] for ln in out if ln.startswith("[")]
    assert names.index("state_dir.manifest") == names.index("state_dir.files") + 1, names
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")  # install.sh's call
    assert cli.main(["doctor", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr().out.splitlines()
    assert len([ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")]) == 1, out
    if where != "root":
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_STATE


def test_the_setup_reports_doctor_fails_damage_only_its_loop_read_reaches(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 20 X51: the report's Doctor hook goes through ``_with_damaged_manifest`` too; on the last
    leaf of items, which the probe misses, its one FAIL and the Loop NEXT are that read's."""
    from agentsync import setup_report  # noqa: PLC0415

    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: (113, "not loaded"))
    cfg = str(initialised.config_path)
    [src] = [s for s in initialised.sources if s.kind is SourceKind.LOCAL]
    assert src.path is not None
    bulk = expand(src.path) / "bulk"
    bulk.mkdir()
    for i in range(160):
        (bulk / f"note-{i:03d}.txt").write_text(f"Contoso note {i}.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    _damage_table_leaf(db, "items", last=True)
    assert doctor.manifest_unreadable(db) is None and doctor.recorded_unreadable(db) is None
    out = tmp_path / "r.md"
    assert cli.main(["setup-report", "--out", str(out), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    text = out.read_text(encoding="utf-8").splitlines()
    (summary,) = [ln for ln in text if ln.startswith("- doctor: ")]
    assert summary.startswith("- doctor: 1 FAIL"), summary
    (loop_line,) = [ln for ln in text if ln.startswith("- Loop: ")]
    assert "NEXT: the state_dir.manifest check failed" in loop_line, loop_line


def test_the_loop_reads_damage_fail_goes_after_the_state_dir_checks_once(initialised: Config) -> None:
    """B13 round 20 X51: ``_with_damaged_manifest`` itself: a coded SQLITE_CORRUPT with no state_dir.manifest
    entry inserts exactly one FAIL after the last state_dir check; SQLITE_BUSY, or a FAIL already there,
    adds nothing."""
    cfg = initialised
    checks = [
        doctor.CheckResult("python", True, "3.11"),
        doctor.CheckResult("state_dir", True, "ok"),
        doctor.CheckResult("state_dir.files", True, "ok"),
        doctor.CheckResult("sources", True, "ok"),
    ]
    corrupt = sqlite3.DatabaseError("database disk image is malformed")
    corrupt.sqlite_errorcode = sqlite3.SQLITE_CORRUPT  # type: ignore[attr-defined]
    got = cli._with_damaged_manifest(cfg, checks, corrupt)
    assert [r.name for r in got] == [
        "python",
        "state_dir",
        "state_dir.files",
        "state_dir.manifest",
        "sources",
    ]
    assert cli._with_damaged_manifest(cfg, got, corrupt) == got
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY  # type: ignore[attr-defined]
    assert cli._with_damaged_manifest(cfg, checks, busy) == checks
    assert cli._with_damaged_manifest(cfg, checks, None) == checks


_MANIFEST_TABLES = (
    "meta",
    "sources",
    "items",
    "runs",
    "run_sources",
    "cache",
    "outputs",
    "cursors",
    "depends",
    "item_aliases",
    "redacted_items",
    "run_changes",
    "schema_migrations",
    "tombstones",
)


@pytest.mark.parametrize(
    "table, where",
    [(t, "root") for t in _MANIFEST_TABLES] + [("cache", "last leaf"), ("outputs", "last leaf")],
)
def test_damage_under_any_table_fails_status_and_stops_the_sync(
    initialised: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    table: str,
    where: str,
) -> None:
    """B13 round 19 W41: damage under the cache or outputs table passed status (exit 0, no FAIL), the report
    pointed at a Doctor state_dir.manifest line it never printed, and the sync failed the source, exit 1,
    with a traceback and "run sync again": the cycle's catches made SQLITE_CORRUPT a source's failure. Status
    now reads each table's first row (``NOT INDEXED``: an index answers ``count(*)``), every command a
    damaged table stops says the one state line and exits 65, and damage deeper in a table that only a sync
    reaches is kept for status while the file is the same."""
    from agentsync import setup_report  # noqa: PLC0415

    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: (113, "not loaded"))
    cfg = str(initialised.config_path)
    [src] = [s for s in initialised.sources if s.kind is SourceKind.LOCAL]
    assert src.path is not None
    if where != "root":
        bulk = expand(src.path) / "bulk"
        bulk.mkdir()
        for i in range(160):
            (bulk / f"note-{i:03d}.txt").write_text(f"Contoso note {i}.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    report = tmp_path / "r.md"
    if where == "root":
        _damage_table_root(db, table)
    else:
        _damage_table_leaf(db, table, last=True)
        assert doctor.manifest_unreadable(db) is None  # a first-row read misses the last leaf
        # before any sync reaches it, the report's evidence read does: its Doctor FAILs for the pointer
        assert cli.main(["setup-report", "--out", str(report), "--config", cfg]) == cli.EXIT_OK
        text = report.read_text(encoding="utf-8").splitlines()
        assert [ln for ln in text if "see Doctor's state_dir.manifest line" in ln], text
        assert len([ln for ln in text if ln.startswith("[FAIL] state_dir.manifest ")]) == 1, text
        assert [ln for ln in text if ln.startswith("- doctor: 1 FAIL")], text
        # B13 round 20 X80: the Loop line's NEXT is the loop's own, which the damage does not reach; it says
        # beside it that the report's read found the file damaged
        (loop_line,) = [ln for ln in text if ln.startswith("- Loop: ")]
        assert (
            " (the loop's own read; the report's read of the state file found it damaged: the "
            "state_dir.manifest [FAIL] line under Doctor says what to do first)" in loop_line
        ), loop_line
        for i in range(160):  # every file changes, so the sync reads every row
            (expand(src.path) / "bulk" / f"note-{i:03d}.txt").write_text(
                f"Contoso {i} v2.\n", encoding="utf-8"
            )
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_STATE
    err = capsys.readouterr().err
    assert "Traceback" not in err and "BLOCKING CYCLE" not in err and "sync again" not in err, err
    (state,) = [ln for ln in err.splitlines() if ln.startswith("state: ")]
    assert state.startswith(f"state: {db} cannot be read: ") and state.endswith(
        f"(fix: {doctor.MANIFEST_FIX})"
    )
    rc, out = _status(cfg, capsys)
    fails = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1 and fails[0].startswith("[FAIL] state_dir.manifest "), (
        out
    )
    assert cli.main(["setup-report", "--out", str(report), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    text = report.read_text(encoding="utf-8").splitlines()
    (summary,) = [ln for ln in text if ln.startswith("- doctor: ")]
    assert summary.startswith("- doctor: 1 FAIL"), summary
    if any("see Doctor's state_dir.manifest line" in ln for ln in text):
        assert [ln for ln in text if ln.startswith("[FAIL] state_dir.manifest ")], text


def test_a_state_file_unopenable_is_one_in_the_state_folder(tmp_path: Path) -> None:
    """X15's reviewer: a permission error from any path read as "this account cannot open the state file", a
    docs repo the loop cannot search included. Only the manifest's open error or a path in the state folder
    is."""
    root = tmp_path / "state"
    inside = PermissionError(errno.EACCES, "Permission denied", str(root / "manifest.sqlite"))
    outside = PermissionError(errno.EACCES, "Permission denied", str(tmp_path / "docs" / ".git"))
    assert doctor.state_unopenable(inside, root) and not doctor.state_unopenable(outside, root)
    assert not doctor.state_unopenable(inside) and not doctor.state_unopenable(None)
    assert doctor.state_unopenable(ManifestOpenError("x cannot be opened", errno.EACCES))
    assert not doctor.state_unopenable(ManifestPathError("x cannot be opened", errno.EISDIR))


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a folder of any mode")
@pytest.mark.parametrize("mode", [0o000, 0o500, 0o400])
def test_staging_left_at_a_bad_mode_is_repaired_not_a_failed_cycle(tmp_path: Path, mode: int) -> None:
    """X4's reviewer: emptying staging in place listed it before its chmod, so a staging folder left at 000,
    0500 or 0400 failed every cycle where rmtree-then-chmod had repaired it. The mode is set first and a child
    that will not go is left, never a failed cycle."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "left.pdf").write_bytes(b"x")
    staging.chmod(mode)
    cycle_mod._clear_staging(staging)
    assert stat.S_IMODE(staging.stat().st_mode) == 0o700 and list(staging.iterdir()) == []


def test_a_listing_warn_over_damage_only_a_sync_read_makes_no_claim_about_the_loop(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 20 X46, X14: damage on the last leaf of outputs, which the read-only probe misses, stopped a
    sync (exit 65, its record kept), and status's listing warn then said "the loop cannot read the state file"
    under a loop line read from that file; the report's Summary said the same of the warn and filed the
    record's state_dir.manifest FAIL as unexpected. The note says the state file cannot be read and no sync
    lists the source until it can (still no fix: a sync exits 65), and the Summary files the FAIL as the
    person's beside the warn."""
    from agentsync import setup_report  # noqa: PLC0415

    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: (113, "not loaded"))
    cfg = str(initialised.config_path)
    [src] = [s for s in initialised.sources if s.kind is SourceKind.LOCAL]
    assert src.path is not None
    bulk = expand(src.path) / "bulk"
    bulk.mkdir()
    for i in range(160):
        (bulk / f"note-{i:03d}.txt").write_text(f"Contoso note {i}.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    _damage_table_leaf(db, "outputs", last=True)
    assert doctor.manifest_unreadable(db) is None
    for i in range(160):
        (bulk / f"note-{i:03d}.txt").write_text(f"Contoso {i} v2.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_STATE
    assert doctor.recorded_unreadable(db) is not None
    beats = initialised.state_paths.heartbeat
    data = json.loads(beats.read_text(encoding="utf-8"))
    sid = _local_id(initialised)
    data[sid]["consecutive_incomplete"] = 5
    beats.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED, out
    assert any(ln.startswith("loop: skill ") for ln in out), out  # the loop read the file
    (warn,) = [ln for ln in out if ln.startswith(f"[warn] heartbeat.{sid} ")]
    assert warn.endswith(f"({doctor.LISTING_STATE_UNREADABLE_NOTE})"), warn
    assert "the loop cannot read" not in warn and "(fix: " not in warn, warn
    report = tmp_path / "r.md"
    assert cli.main(["setup-report", "--out", str(report), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    (summary,) = [ln for ln in report.read_text(encoding="utf-8").splitlines() if ln.startswith("- doctor: ")]
    assert "state_dir.manifest FAIL (the person's; see Doctor)" in summary, summary
    assert "the loop cannot read" not in summary, summary
    unexpected = summary.split("unexpected", 1)[1] if "unexpected" in summary else ""
    assert "state_dir.manifest" not in unexpected, summary


_MANIFEST_INDEXES = (
    "sqlite_autoindex_meta_1",
    "sqlite_autoindex_sources_1",
    "sqlite_autoindex_items_1",
    "items_by_path",
    "items_by_parent",
    "items_by_canonical",
    "items_by_verdict",
    "sqlite_autoindex_cursors_1",
    "sqlite_autoindex_cache_1",
    "sqlite_autoindex_outputs_1",
    "sqlite_autoindex_outputs_2",
    "outputs_by_item",
    "outputs_by_key",
    "sqlite_autoindex_tombstones_1",
    "sqlite_autoindex_run_sources_1",
    "sqlite_autoindex_depends_1",
    "depends_by_source",
    "sqlite_autoindex_item_aliases_1",
    "item_aliases_by_item",
    "sqlite_autoindex_redacted_items_1",
    "sqlite_autoindex_run_changes_1",
)


def test_the_index_list_is_the_manifests(tmp_path: Path) -> None:
    """Every index of a new manifest is in :data:`_MANIFEST_INDEXES`, so a new one is damaged below too."""
    Manifest(tmp_path / "m.sqlite").close()
    conn = sqlite3.connect(tmp_path / "m.sqlite")
    try:
        found = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND rootpage > 0")
        }
    finally:
        conn.close()
    assert found == set(_MANIFEST_INDEXES)


@pytest.mark.parametrize("index", _MANIFEST_INDEXES)
def test_damage_under_an_index_root_fails_status_before_any_sync(
    initialised: Config, capsys: pytest.CaptureFixture[str], index: str
) -> None:
    """B13 round 20 X50: damage under an index root (items_by_path, items_by_canonical, ...) passed status,
    exit 0 with no FAIL, while install.sh's step 1 read it and the next sync exited 65; only that sync's
    record then made status FAIL. The read-only probe reads each index's first entry (``INDEXED BY``) as
    install.sh does: status FAILs state_dir.manifest at once, and the sync stops before it runs, exit 65."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    _damage_table_root(db, index)
    assert doctor.manifest_unreadable(db) is not None
    assert doctor.recorded_unreadable(db) is None  # no sync has read it
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    fails = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1 and fails[0].startswith("[FAIL] state_dir.manifest "), (
        out
    )
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_STATE
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    (state,) = [ln for ln in err.splitlines() if ln.startswith("state: ")]
    assert state.startswith(f"state: {db} cannot be read: ") and state.endswith(
        f"(fix: {doctor.MANIFEST_FIX})"
    )


def _record_beside(db: Path) -> Path:
    return db.with_name(doctor._UNREADABLE_RECORD)


def test_a_record_of_damage_goes_once_a_command_reads_the_manifest_written_since(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 20 X33: manifest-unreadable.json stayed beside a manifest restored or written since, its
    name saying the manifest is unreadable. A command that reads the manifest whole removes a record that no
    longer matches it; status, which writes nothing, never does; a record that still matches stays."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    doctor.record_unreadable(db, sqlite3.DatabaseError("database disk image is malformed"), "sync")
    assert doctor.recorded_unreadable(db) is not None
    with cli._state_readable(initialised):  # (c) the same file read whole: the record still matches, stays
        pass
    assert _record_beside(db).exists()
    conn = sqlite3.connect(db)  # the file is written since: the record is stale
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('x33', '1')")
    conn.commit()
    conn.close()
    assert doctor.recorded_unreadable(db) is None
    capsys.readouterr()
    rc, _out = _status(cfg, capsys)  # (b) status writes nothing
    assert rc == cli.EXIT_OK and _record_beside(db).exists()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK  # (a) a sync that reads it removes it
    assert not _record_beside(db).exists()


def test_the_recorded_fail_names_the_command_that_read_the_damage(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 20 X54: the recorded state_dir.manifest FAIL always said "(a sync read it)", though purge,
    curate and exclude-empty write the record too. It names the command, and a record from an older build,
    with none, says "an agentsync command"."""
    cfg = str(initialised.config_path)
    [src] = [s for s in initialised.sources if s.kind is SourceKind.LOCAL]
    assert src.path is not None
    bulk = expand(src.path) / "bulk"
    bulk.mkdir()
    for i in range(160):
        (bulk / f"note-{i:03d}.txt").write_text(f"Contoso note {i}.\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    _damage_table_leaf(db, "items", last=True)
    assert doctor.manifest_unreadable(db) is None
    capsys.readouterr()
    assert cli.main(["purge", "path=bulk/*", "--dry-run", "--config", cfg]) == cli.EXIT_STATE
    rc, out = _status(cfg, capsys)
    (fail,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")]
    assert rc == cli.EXIT_FAILED and "(an agentsync purge read it)" in fail and "a sync" not in fail, fail
    record = json.loads(_record_beside(db).read_text(encoding="utf-8"))
    del record["command"]  # an older build's record
    _record_beside(db).write_text(json.dumps(record), encoding="utf-8")
    rc, out = _status(cfg, capsys)
    (fail,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")]
    assert "(an agentsync command read it)" in fail, fail


@pytest.mark.parametrize("hint", ["by hand", "install.sh"])
def test_the_setup_reports_doctor_fails_a_manifest_damaged_under_a_table_as_status_does(
    initialised: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hint: str,
) -> None:
    """B13 round 18 V14, V52, V56, V71: on the manifest U64's test damages, status FAILed state_dir.manifest
    and the setup report said "doctor: 0 FAIL" and "NEXT: none", and its "see Doctor's state_dir.manifest
    line" pointers named a line Doctor never printed. The report's Doctor hook reads the loop first, as status
    does: one FAIL, filed under the state file that cannot be read (with the listing warn beside it, not
    judged), and the Loop NEXT is rule 1's, the one status prints. B13 round 19 W11: that listing warn pointed
    at the Loop line for a WAITING ON YOU line (under install.sh) or at the WAITING ON YOU lines (by hand),
    which a state the loop cannot read never has; it says so, in the report and in status."""
    from agentsync import setup_report  # noqa: PLC0415

    if hint == "install.sh":
        monkeypatch.setenv(doctor.NO_NEXT_HINT_ENV, "1")
    else:
        monkeypatch.delenv(doctor.NO_NEXT_HINT_ENV, raising=False)

    monkeypatch.setattr(setup_report, "_launchctl_print", lambda run, label: (113, "not loaded"))
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    beats = initialised.state_paths.heartbeat
    data = json.loads(beats.read_text(encoding="utf-8"))
    sid = _local_id(initialised)
    data[sid]["consecutive_incomplete"] = 3  # the listing warn of an emptied folder (TD's)
    beats.write_text(json.dumps(data), encoding="utf-8")
    _damage_table_root(initialised.state_paths.db, "items")
    out = tmp_path / "r.md"
    assert cli.main(["setup-report", "--out", str(out), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    fails = [ln for ln in text.splitlines() if ln.startswith("[FAIL] ")]
    assert len(fails) == 1 and fails[0].startswith("[FAIL] state_dir.manifest "), fails
    (summary,) = [ln for ln in text.splitlines() if ln.startswith("- doctor: ")]
    assert summary.startswith("- doctor: 1 FAIL, 1 warn "), summary
    assert (
        "from the state file that cannot be read (2 checks): state_dir.manifest FAIL (the person's; see "
        f"Doctor), heartbeat.{sid} warn (not judged: the state file cannot be read)" in summary
    ), summary
    assert "unexpected 0" in summary, summary
    (loop_line,) = [ln for ln in text.splitlines() if ln.startswith("- Loop: ")]
    assert "NEXT: the state_dir.manifest check failed: " in loop_line, loop_line
    assert "NEXT: none" not in loop_line, loop_line
    assert "the report's read of the state file found it damaged" not in loop_line, "doctor's own FAIL (X80)"
    assert "WAITING ON YOU:" not in loop_line, loop_line
    (warn,) = [ln for ln in text.splitlines() if ln.startswith(f"[warn] heartbeat.{sid} ")]
    assert warn.endswith(f"({doctor.LISTING_STATE_UNREADABLE_NOTE})"), warn
    pointing = [ln for ln in text.splitlines() if "WAITING ON YOU line" in ln and ln != summary]
    assert pointing == [], pointing
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_FAILED
    (warn,) = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(f"[warn] heartbeat.{sid} ")]
    assert warn.endswith(f"({doctor.LISTING_STATE_UNREADABLE_NOTE})"), warn


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
@pytest.mark.parametrize("what", ["file", "folder"])
def test_a_manifest_this_account_cannot_open_is_one_line_and_kept(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    """B13 round 19 W65: a manifest this account cannot open (a root-owned file one sudo run left, a folder
    made read-only) ended ``sync`` and ``curate`` on a raw PermissionError traceback from ``os.open``, exit 1:
    V59 covered only sqlite's errors. It is one ``error: state:`` line naming the file and its fix, exit 1:
    not 65, whose move-aside fix would throw away a manifest that is not damaged.

    B13 round 21 Y31, Y69: that fix was W65's generic "owner and mode 600" while status named ``chmod 600
    <file>`` (or ``chmod 700 <folder>``, or another account's file the person's chown), and the sync printed
    no NEXT, so the setup prompt sent the agent to the report past its own chmod. The fix is
    ``doctor.state_path_fix``'s, status's own, for sync, curate and add-source; an interactive sync adds a
    NEXT naming status, never the fix itself (another account's is the person's sudo)."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    root = Path(os.path.realpath(expand(initialised.state_paths.root)))
    folder = str(initialised.sources[0].path)  # type: ignore[union-attr]
    before = db.read_bytes()
    target = db
    if what == "folder":  # no file yet, in a folder this account cannot write: the open that creates it fails
        db.rename(db.with_name("kept.sqlite"))
        for side in db.parent.glob(db.name + "-*"):
            side.unlink()
        target = db.parent
    target.chmod(0o500 if what == "folder" else 0o000)
    try:
        own = f"chmod {'700' if what == 'folder' else '600'} {doctor._shell_path(target)}"
        assert doctor.state_path_fix(root, Path(os.path.realpath(db))) == own
        if what == "file":  # status's state_dir.files FAIL names the same fix
            rc, out = _status(cfg, capsys)
            (files,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.files ")]
            assert rc == cli.EXIT_FAILED and files.endswith(f"(fix: {own})"), files
        # curate opens no manifest that is not there yet
        commands = (["sync"], ["sync", "--mode", "poll"], ["add-source", folder])
        for argv in (*commands, *([["curate"]] if what == "file" else [])):
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
            captured = capsys.readouterr()
            err = captured.err
            assert "Traceback" not in err and "PermissionError" not in err, err
            (line,) = [ln for ln in err.splitlines() if ln.startswith("error: state: ")]
            # B13 round 22 Z53: no manifest yet, so nothing is there to be damaged
            fix = f"{own}; the file itself is not damaged" if what == "file" else own
            # B13 round 22 Z15: the folder's fix says the folder refused, not the file the line names
            why = ": its folder refuses this account" if what == "folder" else ""
            assert line == f"error: state: {db} cannot be opened: Permission denied{why} (fix: {fix})", line
            assert doctor.MANIFEST_FIX not in err and cli._ACCESS_FIX not in err, err
            nexts = [ln for ln in captured.out.splitlines() if ln.startswith("NEXT: ")]
            assert nexts == ([f"NEXT: {cli.STATE_REFUSED_STEP}"] if argv == ["sync"] else []), (argv, nexts)
        # install.sh's run names its own NEXT
        monkeypatch.setenv("AGENTSYNC_NO_NEXT_HINT", "1")
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        assert "NEXT: " not in capsys.readouterr().out
        monkeypatch.delenv("AGENTSYNC_NO_NEXT_HINT")
        # another account's file (a sudo sync's): the person's chown, and the NEXT still names only status
        me = os.getuid()
        monkeypatch.setattr(doctor.os, "getuid", lambda: me + 1)
        capsys.readouterr()
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        captured = capsys.readouterr()
        (line,) = [ln for ln in captured.err.splitlines() if ln.startswith("error: state: ")]
        kept = "; the file itself is not damaged" if what == "file" else ""
        assert line.endswith(f"(fix: {doctor._persons_chown(root)}{kept})"), line
        assert doctor.is_persons_fix(line.split(" (fix: ", 1)[1])
        (step,) = [ln for ln in captured.out.splitlines() if ln.startswith("NEXT: ")]
        assert "sudo" not in step and "chown" not in step and "chmod" not in step, step
        monkeypatch.undo()
    finally:
        target.chmod(0o700 if what == "folder" else 0o600)
    if what == "folder":
        assert not db.exists()
        db.with_name("kept.sqlite").rename(db)
    assert db.read_bytes() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_the_syncs_next_over_its_own_state_file_promises_no_owner_status_does_not_name(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 22 Z37: over this account's own manifest at 0000 the sync's NEXT said status's "NEXT says
    whose it is", but status's NEXT for the agent's own chmod is an imperative that names no owner. The sync's
    NEXT sends the agent to do what status's NEXT says, and promises no owner; status's is unchanged."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    for side in ("-wal", "-shm"):
        db.with_name(db.name + side).unlink(missing_ok=True)
    db.chmod(0)
    try:
        capsys.readouterr()
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        nexts = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("NEXT: ")]
        _rc, out = _status(cfg, capsys)
    finally:
        db.chmod(0o600)
    assert nexts == [f"NEXT: {cli.STATE_REFUSED_STEP}"], nexts
    assert "whose" not in cli.STATE_REFUSED_STEP and "do what its NEXT says" in cli.STATE_REFUSED_STEP
    assert (
        out[0] == "NEXT: the state_dir.files check failed: do what the fix on its [FAIL] line below says"
    ), out


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file of any mode")
def test_a_wal_and_shm_this_account_cannot_write_name_both_files_one_fix_and_a_next(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 22 Z80: with WAL frames left in manifest.sqlite-wal (a run killed before its checkpoint) and
    the -wal and -shm at 0400, a sync's write raised SQLITE_READONLY, and the CLI printed only ``error:
    OperationalError: attempt to write a readonly database``, no fix and no NEXT, while status named ``chmod
    600`` of both. The line names every refused file with one fix for them all (one chmod per run was a
    trap) and the NEXT names status; a READONLY with no refused file keeps sqlite's line."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    root = db.parent
    leave = (
        "import os, sqlite3, sys\n"
        "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "c.execute('PRAGMA wal_autocheckpoint=0')\n"
        "c.execute(\"UPDATE sources SET kind = kind || 'x'\")\n"
        "c.execute('UPDATE sources SET kind = substr(kind, 1, length(kind) - 1)')\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-I", "-c", leave, str(db)], check=True)
    sides = [db.with_name(db.name + side) for side in ("-wal", "-shm")]
    assert all(p.exists() and p.stat().st_size > 0 for p in sides), sides
    for p in sides:
        p.chmod(0o400)
    try:
        capsys.readouterr()
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        said = capsys.readouterr()
        assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_FAILED
        polled = capsys.readouterr()
        _rc, out = _status(cfg, capsys)
    finally:
        for p in sides:
            p.chmod(0o600)
    fix = "chmod 600 " + " ".join(doctor._shell_path(root / p.name) for p in sides)
    assert said.err.strip() == (
        f"error: state: {sides[0]}, {sides[1]} cannot be opened for writing by this account (fix: {fix})"
    ), said.err
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.STATE_REFUSED_STEP}"
    ]
    assert "NEXT: " not in polled.out and polled.err.strip().endswith(f"(fix: {fix})"), polled
    (files,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.files ")]
    assert files.endswith(f"(fix: {fix})"), files  # the same fix status gives
    # a READONLY with no refused file (a folder it cannot write in): sqlite's own line, as before
    readonly = sqlite3.OperationalError("attempt to write a readonly database")
    readonly.sqlite_errorcode = sqlite3.SQLITE_READONLY  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "_cmd_sync", lambda args: (_ for _ in ()).throw(readonly))
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    said = capsys.readouterr()
    assert said.err.strip() == "error: OperationalError: attempt to write a readonly database", said.err
    assert "NEXT: " not in said.out


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_manifest_behind_a_folder_that_refuses_a_search_is_still_not_damaged(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 22 Z53, the verifier's trap: a manifest that is there, in a folder at 0600 (read, no
    search), cannot be seen by ``exists()``, which raises on Python 3.11 (a second error over the first) or
    reads False. The guard is dropped only when the file is known to be absent, so it stays here, with no
    traceback."""
    import argparse  # noqa: PLC0415

    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    root = db.parent
    args = argparse.Namespace(config=cfg, command="sync")

    def refused(path: Path) -> ManifestOpenError:
        made = ManifestOpenError(f"{path} cannot be opened: Permission denied", errno.EACCES)
        made.__cause__ = PermissionError(errno.EACCES, "Permission denied", str(path))
        return made

    root.chmod(0o600)
    try:
        there = cli._manifest_open_fix(args, refused(db))
        gone = cli._manifest_open_fix(args, refused(root / "absent.sqlite"))
    finally:
        root.chmod(0o700)
    assert there == f"chmod 700 {doctor._shell_path(root)}; the file itself is not damaged", there
    assert gone.endswith("; the file itself is not damaged"), gone  # unknown behind the folder: kept
    # in an open folder, the same absent file is known to be absent: nothing is there to be damaged
    assert not cli._manifest_open_fix(args, refused(root / "absent.sqlite")).endswith(" not damaged")


@pytest.mark.parametrize("present", [True, False])
def test_a_manifest_on_a_read_only_volume_gets_the_persons_volume_fix_from_every_command(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, present: bool
) -> None:
    """B13 round 22 Z47, Z78: on a read-only volume ``os.open`` fails with EROFS, a plain OSError, so sync,
    curate and add-source gave the generic "owner and mode 600" words, the agent's chmod, which fails there,
    while status gave the person's volume fix. They give status's fix (``doctor.read_only_fix``) now, keyed on
    the errno; with no manifest yet, no "not damaged" (Z53)."""
    from agentsync import manifest as manifest_mod  # noqa: PLC0415

    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    if not present:
        db.rename(db.with_name("kept.sqlite"))
        for side in db.parent.glob(db.name + "-*"):
            side.unlink()
        # no manifest yet, not a lost one (B13 round 23 A29)
        expand(initialised.state_paths.heartbeat).unlink()
    real_open = manifest_mod.os.open

    def read_only(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if os.fsdecode(path) == str(db):
            raise OSError(errno.EROFS, "Read-only file system", str(db))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(manifest_mod.os, "open", read_only)
    fix = doctor.read_only_fix(Path(os.path.realpath(db)))
    want = f"{fix}; the file itself is not damaged" if present else fix
    folder = str(initialised.sources[0].path)  # type: ignore[union-attr]
    for argv in (["sync"], ["add-source", folder], *([["curate"]] if present else [])):
        capsys.readouterr()
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
        err = capsys.readouterr().err
        (line,) = [ln for ln in err.splitlines() if ln.startswith("error: state: ")]
        assert line == f"error: state: {db} cannot be opened: Read-only file system (fix: {want})", line
        assert doctor.is_persons_fix(line.split(" (fix: ", 1)[1]) and cli._ACCESS_FIX not in err, err
    monkeypatch.undo()
    if not present:
        db.with_name("kept.sqlite").rename(db)
    # a read-only volume outside the state folder: still the volume's fix, never a chmod (lane B's reviewer)
    import argparse  # noqa: PLC0415

    elsewhere = Path(os.path.realpath(initialised.config_path.parent)) / "other.sqlite"
    made = ManifestOpenError(f"{elsewhere} cannot be opened: Read-only file system", errno.EROFS)
    made.__cause__ = OSError(errno.EROFS, "Read-only file system", str(elsewhere))
    fix = cli._manifest_open_fix(argparse.Namespace(config=cfg, command="sync"), made)
    assert fix == doctor.read_only_fix(elsewhere), fix


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a mode-000 folder")
@pytest.mark.parametrize("what", ["folder", "above", "file"])
def test_the_syncs_state_error_says_a_folder_refused_when_a_folder_did(
    initialised: Config, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    """B13 round 22 Z15 (round 22 ruling 3): with the state folder at 0000, or a folder above it refusing a
    search, the sync's line named manifest.sqlite and its NEXT "a state file", while the fix was the
    folder's. The path stays the one the system refused; the line says the folder refused it, and the NEXT
    says a state file or its folder. A file of this account's at 0000 keeps the file's words."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    target = {"folder": db.parent, "above": db.parent.parent, "file": db}[what]
    was = target.stat().st_mode & 0o777
    target.chmod(0o000)
    try:
        capsys.readouterr()
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        captured = capsys.readouterr()
    finally:
        target.chmod(was)
    (line,) = [ln for ln in captured.err.splitlines() if ln.startswith("error: state: ")]
    why = {
        "folder": ": its folder refuses this account (fix: chmod 700 ",
        "above": ": a folder above it refuses this account a search (fix: the person gives this account ",
        "file": "Permission denied (fix: chmod 600 ",
    }[what]
    assert why in line and "Traceback" not in captured.err, line
    assert f"NEXT: {cli.STATE_REFUSED_STEP}" in captured.out
    assert cli.STATE_REFUSED_STEP.startswith("this account cannot open a state file or its folder, ")


@pytest.mark.parametrize(
    ("code", "opened"),
    [
        (sqlite3.SQLITE_CANTOPEN, True),
        (sqlite3.SQLITE_PERM, True),
        (sqlite3.SQLITE_PERM | 256, True),  # an extended form of the same primary code
        (sqlite3.SQLITE_READONLY, True),
        (23, False),  # SQLITE_AUTH: only an authorizer callback raises it, and none is set
    ],
)
def test_the_codes_of_a_state_file_this_account_cannot_open_are_named(code: int, opened: bool) -> None:
    """B13 round 20 X48: status keyed "cannot be opened" on (8, 14, 23) under a comment naming SQLITE_PERM,
    which is 3; 23 is SQLITE_AUTH. A real SQLITE_PERM got the damaged-file words. The set is named once, in
    doctor, and status reads it."""
    exc = sqlite3.OperationalError("unable to open database file")
    exc.sqlite_errorcode = code  # type: ignore[attr-defined]
    assert doctor.cannot_open(exc) is opened
    words = cli._unread_words(exc)
    if opened:
        assert (
            words
            == "the state file cannot be opened: unable to open database file; the state_dir lines say why"
        )
    else:
        assert words == "the state file cannot be read: unable to open database file", words


def test_a_manifest_this_account_cannot_open_keeps_its_reason(tmp_path: Path) -> None:
    """B13 round 20 X47: W65's ManifestOpenError set no strerror, so status lost the reason ("a file cannot be
    opened; the state_dir lines say why", where fba3908a said "...: Permission denied"). The reason is its own
    attribute: setting strerror would turn ``str()`` into "[Errno 13] Permission denied", which the one-line
    ``error: state:`` must not print."""
    from agentsync import manifest as manifest_mod  # noqa: PLC0415

    made = ManifestOpenError(
        "/x/manifest.sqlite cannot be opened: Permission denied", errno.EACCES, "Permission denied"
    )
    assert str(made) == "/x/manifest.sqlite cannot be opened: Permission denied"
    assert (
        cli._unread_words(made) == "a file cannot be opened: Permission denied; the state_dir lines say why"
    )
    assert cli._unread_words(ManifestOpenError("x", errno.EACCES)) == (
        "a file cannot be opened; the state_dir lines say why"
    )
    assert cli._unread_words(ManifestPathError("x", errno.EISDIR, "Is a directory")) == (
        "a file cannot be opened: Is a directory"
    )
    if os.geteuid() != 0:  # root opens a mode-000 file
        db = tmp_path / "state" / "manifest.sqlite"
        db.parent.mkdir()
        db.write_bytes(b"")
        db.chmod(0)
        try:
            with pytest.raises(ManifestOpenError) as raised:
                manifest_mod._connect(db)
        finally:
            db.chmod(0o600)
        assert raised.value.reason == "Permission denied" and raised.value.strerror is None
        assert str(raised.value) == f"{db} cannot be opened: Permission denied"


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
@pytest.mark.parametrize("shut", ["state folder", "folder above", "manifest"])
def test_status_names_the_state_folder_when_the_folder_refuses_and_a_file_when_a_file_does(
    initialised: Config, capsys: pytest.CaptureFixture[str], shut: str
) -> None:
    """B13 round 22 Z5: with the state folder at 0000, or a folder above it that refuses a search, status
    said "cannot read the state: a file cannot be opened", since its first read was refused at the lock, a
    file, while every other line named the folder. Keyed on the folder (``doctor.state_folder_shut``), the
    words name it; a manifest at 0000 in a folder at 0700 is still a file."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = expand(initialised.state_paths.root)
    db = expand(initialised.state_paths.db)
    for side in ("-wal", "-shm"):
        db.with_name(db.name + side).unlink(missing_ok=True)
    target = {"state folder": root, "folder above": root.parent, "manifest": db}[shut]
    mode = stat.S_IMODE(target.stat().st_mode)
    capsys.readouterr()
    target.chmod(0)
    try:
        _rc, out = _status(cfg, capsys)
        lock = expand(initialised.state_paths.lock)
        words = cli._unread_words(
            PermissionError(errno.EACCES, "Permission denied", str(lock)), config=initialised
        )
        nameless = cli._unread_words(PermissionError(errno.EACCES, "Permission denied"), config=initialised)
    finally:
        target.chmod(mode)
    (line,) = [ln for ln in out if ln.startswith("status: cannot read the state: ")]
    what = "a file" if shut == "manifest" else "the state folder"
    want = f"{what} cannot be opened: Permission denied; the state_dir lines say why"
    assert line == f"status: cannot read the state: {want}", line
    if shut != "manifest":  # the lock, a file, refused because of the folder
        assert words == want, words
        # an error that names no path is no proof the folder refused it (lane B's reviewer)
        assert nameless.startswith("a file cannot be opened"), nameless


@pytest.mark.parametrize("layout", ["manifest is a folder", "state folder is a file"])
def test_a_manifest_path_that_is_no_access_matter_gets_no_access_fix(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str], layout: str
) -> None:
    """B13 round 20 X49: W65 gave every OSError from opening the manifest the access fix ("owner and mode 600;
    the file itself is not damaged"), a folder where the file belongs (EISDIR) and a file where its folder
    belongs (EEXIST) included. Those stay one line with no traceback, and name no owner or mode."""
    from agentsync import manifest as manifest_mod  # noqa: PLC0415

    cfg = str(initialised.config_path)
    db = initialised.state_paths.db
    if layout == "manifest is a folder":
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
        db.rename(db.with_name("kept.sqlite"))
        for side in db.parent.glob(db.name + "-*"):
            side.unlink()
        db.mkdir()
        capsys.readouterr()
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        err = capsys.readouterr().err
        assert "Traceback" not in err and "IsADirectoryError" not in err, err
        (line,) = [ln for ln in err.splitlines() if ln.startswith("error: ")]
        # B13 round 22 Z51: the words and fix of the line that FAILs it, state_dir.manifest's, the person's
        want = f"error: state: {db} is a folder, not the state file (fix: {doctor.MANIFEST_FIX})"
        assert line == want, line
        assert cli._ACCESS_FIX not in err, err
        target = db
    else:  # a sync's lock opens the state folder first; the manifest's own open is what this range covers
        folder = tmp_path / "x49-state"
        folder.write_text("not a folder\n", encoding="utf-8")
        target = folder / "manifest.sqlite"
    with pytest.raises(ManifestPathError) as raised:
        manifest_mod._connect(target)
    assert not isinstance(raised.value, ManifestOpenError)
    assert raised.value.errno in (errno.EISDIR, errno.EEXIST, errno.ENOTDIR), raised.value.errno


def test_a_folder_where_the_manifest_belongs_fails_status(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 21 Y47: manifest.sqlite as a 0700 folder made every sync exit 1 (``cannot be opened: Is a
    directory``) while status read ``[ok] state_dir.files — manifest.sqlite (0700): this account's alone``,
    exited 0 and printed no NEXT. state_dir.manifest FAILs it, with the person's fix, before its owner and
    mode are read, so status exits 1 and its NEXT names that line."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    db.rename(db.with_name("kept.sqlite"))
    for side in db.parent.glob(db.name + "-*"):
        side.unlink()
    db.mkdir(mode=0o700)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED, out
    (fail,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")]
    assert f"{db} is a folder, not the state file" in fail and fail.endswith(f"(fix: {doctor.MANIFEST_FIX})")
    assert not any(ln.startswith("[ok  ] state_dir.files ") and "manifest.sqlite" in ln for ln in out), out
    assert out[0].startswith("NEXT: the state_dir.manifest check failed"), out[0]
    named = {r.name: r for r in doctor.run_checks(initialised)}
    assert doctor.is_persons_fix(named["state_dir.manifest"].fix)


def test_a_state_path_that_is_not_where_or_what_it_should_be_names_its_line_fix_and_next(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 22 Z51: with a folder where manifest.sqlite belongs, status FAILed state_dir.manifest, but
    its ``cannot read the state`` line pointed nowhere, and sync, poll and curate printed ``error: <db> cannot
    be opened: Is a directory`` with no fix and no NEXT; with a file where the state folder belongs, a sync's
    lock ended on a raw FileExistsError traceback. The pointer, the words, the fix and the NEXT follow the
    path and errno refused: the line that FAILs it; a full disk (ENOSPC), which no line FAILs, gets none of
    them."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = expand(initialised.state_paths.db)
    db.rename(db.with_name("kept.sqlite"))
    for side in db.parent.glob(db.name + "-*"):
        side.unlink()
    db.mkdir(mode=0o700)
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    # B13 round 24 B11: the person's words, as for a file where the state folder belongs, not the system's
    assert (
        "status: cannot read the state: the state file is not a file; the state_dir.manifest line says why"
    ) in out, out
    assert not any("Is a directory" in ln for ln in out), out
    for argv in (["sync"], ["sync", "--mode", "poll"], ["curate"]):
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
        said = capsys.readouterr()
        assert "Traceback" not in said.err, said.err
        (line,) = [ln for ln in said.err.splitlines() if ln.startswith("error: ")]
        want = f"error: state: {db} is a folder, not the state file (fix: {doctor.MANIFEST_FIX})"
        assert line == want and "cannot be opened" not in line, line
        nexts = [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")]
        # B13 round 25 D44: curate gets the command's NEXT as well (it printed none); a poll none
        want_next = {"sync": cli.STATE_MISPLACED_STEP, "curate": cli.STATE_MISPLACED_COMMAND_STEP}
        assert nexts == ([f"NEXT: {want_next[argv[0]]}"] if argv != ["sync", "--mode", "poll"] else []), nexts
        assert "cannot open" not in cli.STATE_MISPLACED_STEP
    # a full disk: no line FAILs it, so no pointer, no fix and no NEXT
    full = ManifestPathError(
        f"{db} cannot be opened: No space left on device", errno.ENOSPC, "No space left on device"
    )
    full.__cause__ = OSError(errno.ENOSPC, "No space left on device", str(db))
    assert cli._unread_words(full, config=initialised) == "a file cannot be opened: No space left on device"
    monkeypatch.setattr(cli, "_cmd_sync", lambda args: (_ for _ in ()).throw(full))
    db.rmdir()
    db.with_name("kept.sqlite").rename(db)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    said = capsys.readouterr()
    assert said.err.strip() == f"error: {full}" and "NEXT: " not in said.out, said
    monkeypatch.undo()
    # a file where the state folder belongs: state_dir's fix, never the lock's traceback
    root = expand(initialised.state_paths.root)
    root.rename(root.with_name("kept-state"))
    root.write_text("not a folder\n", encoding="utf-8")
    try:
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        said = capsys.readouterr()
    finally:
        root.unlink()
        root.with_name("kept-state").rename(root)
    assert "Traceback" not in said.err and "FileExistsError" not in said.err, said.err
    (line,) = [ln for ln in said.err.splitlines() if ln.startswith("error: ")]
    moved = f"the person moves {doctor._shell_path(root)} aside, keeping a copy"  # round 23 ruling 4
    assert line == f"error: state: {root} is a file, not a folder (fix: {moved})", line
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.STATE_MISPLACED_STEP}"
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
@pytest.mark.parametrize(
    "state", ["manifest folder", "state folder file", "state folder 0000", "readable", "lost manifest"]
)
def test_status_keeps_the_baseline_wait_when_it_cannot_read_the_state(
    initialised: Config, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    """B13 round 25 D11, D29 (one defect): with the baseline questions a draft, a folder where the manifest
    belongs, a file where the state folder belongs or a state folder this account cannot search dropped
    status's WAITING ON YOU line (the loop built it only after its first state read), while the loop's line
    still said "baseline draft"; the setup report's Loop line lost it with it. The wait reads the docs repo
    alone: it follows the NEXT, once; a readable state with a FAIL prints it once too."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    root, db = expand(sp.root), expand(sp.db)
    evals = expand(initialised.docs_repo) / "_eval"
    evals.mkdir(exist_ok=True)
    for name in ("questions.md", "answers.md"):
        (evals / name).write_text("---\nstatus: draft\n---\n", encoding="utf-8")
        (evals / name).chmod(0o600)
    (want,) = loop.docs_waits(initialised)
    if state == "manifest folder":
        for name in ("", "-wal", "-shm"):
            db.with_name(db.name + name).unlink(missing_ok=True)
        db.mkdir(mode=0o700)
    elif state == "state folder file":
        root.rename(root.with_name(root.name + ".kept"))
        root.write_text("not a folder\n", encoding="utf-8")
    elif state == "state folder 0000":
        root.chmod(0)
    elif state == "lost manifest":  # the loop reads past it: the FAIL leads, the loop's own waits stay (D43)
        for name in ("", "-wal", "-shm"):
            db.with_name(db.name + name).unlink(missing_ok=True)
    else:
        (evals / "answers.md").chmod(0o644)  # a FAIL (docs_repo.permissions) over a state that reads
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
    finally:
        if state == "state folder 0000":
            root.chmod(0o700)
    assert rc == cli.EXIT_FAILED, out
    assert out[0].startswith("NEXT: "), out
    waits = [ln for ln in out if ln.startswith("WAITING ON YOU: ")]
    assert waits == [loop.WAIT_PREFIX + want], out
    if state != "readable":
        assert out[1] == loop.WAIT_PREFIX + want, out


def test_the_reports_loop_keeps_the_baseline_wait_when_a_busy_state_has_no_fail(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 26 E44: with the loop state busy (an OperationalError, no FAIL to name) and the baseline
    questions a draft, the report's Loop hook fell back to nothing and its Loop line dropped the baseline wait
    status prints. The wait reads the docs repo alone, so the hook still gives it, and no NEXT of its own."""
    _synced(initialised)
    evals = expand(initialised.docs_repo) / "_eval"
    evals.mkdir(exist_ok=True)
    for name in ("questions.md", "answers.md"):
        (evals / name).write_text("---\nstatus: draft\n---\n", encoding="utf-8")
        (evals / name).chmod(0o600)
    (want,) = loop.docs_waits(initialised)

    def busy(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(loop, "next_step", busy)
    monkeypatch.setattr(loop, "next_lines", busy)
    assert cli._report_hooks().loop_next(initialised) == [loop.WAIT_PREFIX + want]


@pytest.mark.parametrize("where", ["the state folder", "a folder above it"])
def test_a_file_where_the_state_folder_or_a_folder_above_it_belongs_is_one_fail_the_persons(
    initialised: Config, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """B13 round 23 A7, A22, A33, A58 (round 23 ruling 4): a file where the state folder belongs gave status a
    state_dir FAIL whose ``move <root> aside`` was read as the agent's, a heartbeat FAIL with a generic
    "report it", a governance "check crashed: GovernanceError ... [Errno 20]" and a "cannot read the state:
    GovernanceError" summary; a file where ``~/Library/Application Support`` belongs gave state_dir itself the
    generic fix. It is one FAIL now, state_dir's, naming the first path that is not a folder with the person's
    move aside, and every check that reads the state folder says it was not checked."""
    cfg = _synced(initialised)
    capsys.readouterr()
    root = expand(initialised.state_paths.root)
    that = root if where == "the state folder" else root.parent
    that.rename(that.with_name(that.name + ".kept"))
    that.write_text("Contoso\n", encoding="utf-8")
    try:
        rc, out = _status(cfg, capsys)
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        said = capsys.readouterr()
    finally:
        that.unlink()
        that.with_name(that.name + ".kept").rename(that)
    moved = f"(fix: the person moves {doctor._shell_path(that)} aside, keeping a copy)"
    text = "\n".join(out)
    assert rc == cli.EXIT_FAILED and [ln for ln in out if ln.startswith("[FAIL] ")] == [
        ln for ln in out if ln.startswith("[FAIL] state_dir ")
    ], text
    (fail,) = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert f"{that} is a file, not a folder" in fail and fail.endswith(moved), fail
    for name in ("lock", "heartbeat", "governance.hold", "governance.purge_queue", "config.vanished_sources"):
        (line,) = [ln for ln in out if ln.startswith(f"[info] {name} ")]
        assert line.endswith(f"— {doctor.STATE_FOLDER_NOT_A_FOLDER}"), line
    for words in ("report it", "check crashed", "GovernanceError", "Errno", "cannot look inside"):
        assert words not in text, (words, text)
    assert (
        "status: cannot read the state: the state folder is not a folder; the state_dir line says why" in out
    )
    (step,) = [ln for ln in out if ln.startswith("NEXT: ")]
    assert step.startswith("NEXT: the state_dir check failed: ") and "not a step for the agent" in step, step
    (line,) = [ln for ln in said.err.splitlines() if ln.startswith("error: ")]
    assert line.endswith(moved) and "report it" not in said.err, said.err
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.STATE_MISPLACED_STEP}"
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_state_folder_this_account_cannot_look_inside_is_one_line_with_the_folders_fix(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 20 X73: with the state folder at mode 0000, status gave generic "could not read ... report
    it" FAILs and a governance crash, and sync, poll and curate ended on raw PermissionError tracebacks (the
    manifest's lstat, the lock's open, curate's look for the manifest): W65's guard is in the manifest's
    open, which none reached. Status FAILs the folder's mode with its ``chmod 700``; the commands say one
    ``error: state:`` line with the same fix and exit 1."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = initialised.state_paths.root
    fix = f"chmod 700 {doctor._shell_path(root)}"
    root.chmod(0)
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        fails = [ln for ln in out if ln.startswith("[FAIL] ")]
        assert rc == cli.EXIT_FAILED, out
        assert fails and fails[0].startswith("[FAIL] state_dir ") and fails[0].endswith(f"(fix: {fix})"), (
            fails
        )
        assert not any("check crashed" in ln for ln in out), out
        stopped = [
            ln for ln in fails if "could not read" in ln and not re.match(r"\[FAIL\] (logs|permissions) ", ln)
        ]
        assert stopped == [], stopped
        assert any(
            ln.startswith("[info] governance.hold ") and doctor.STATE_FOLDER_UNCHECKED in ln for ln in out
        )
        for argv in (["sync"], ["sync", "--mode", "poll"], ["curate"]):
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
            err = capsys.readouterr().err
            assert "Traceback" not in err and "PermissionError" not in err, (argv, err)
            (line,) = [ln for ln in err.splitlines() if ln.startswith("error: ")]
            # B13 round 22 Z15: a path in it says the folder refused it; the folder itself needs no word
            assert line.startswith("error: state: ") and line.endswith(
                (
                    f"cannot be opened: Permission denied: its folder refuses this account (fix: {fix})",
                    f"{root} cannot be opened: Permission denied (fix: {fix})",
                )
            ), (argv, line)
    finally:
        root.chmod(0o700)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_config_hold_shows_over_a_state_folder_status_cannot_open(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 21 Y44: with the state folder shut, status printed only governance.hold's not-checked line,
    hiding a legal hold sources.toml sets, which needs no state file; so did holds.json at 0000. The config
    hold is shown first either way, and only the state holds are not checked (or FAIL)."""
    cfg = str(initialised.config_path)
    path = initialised.config_path
    text = '\n[governance]\nhold = true\nhold_reason = "Contoso matter 7"\nhold_owner = "Contoso legal"\n'
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = expand(initialised.state_paths.root)
    shown = "[ok  ] governance.hold "
    held = "hold all (config): Contoso matter 7"
    _rc, out = _status(cfg, capsys)
    assert [ln for ln in out if ln.startswith(shown) and held in ln], out
    root.chmod(0)
    try:
        _rc, out = _status(cfg, capsys)
    finally:
        root.chmod(0o700)
    holds = [ln for ln in out if ln.startswith(("[ok  ] governance.hold ", "[info] governance.hold "))]
    assert len(holds) == 2 and held in holds[0] and holds[0].startswith(shown), holds
    assert holds[1].startswith("[info] governance.hold ") and doctor.STATE_FOLDER_UNCHECKED in holds[1], holds
    state = governance._holds_file(root)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"holds": []}\n', encoding="utf-8")
    state.chmod(0)
    try:
        _rc, out = _status(cfg, capsys)
    finally:
        state.chmod(0o600)
    assert [ln for ln in out if ln.startswith(shown) and held in ln], out
    assert [ln for ln in out if ln.startswith("[FAIL] governance.hold ")], out


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_a_folder_above_the_state_folder_that_refuses_a_search_is_one_fail_the_persons(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 22 Z3, Z9, Z26, Z34, Z46, Z60, Z73, one defect built once: with ~/Library/Application
    Support at 0000, ``Path.exists`` and ``is_dir`` raise PermissionError on Python 3.11, so disk
    (``_nearest_existing``) and governance (the Time Machine check's backups lookup) each gave a generic
    "could not read ... report it" FAIL, read as the agent's, which took status's NEXT from state_dir's, the
    person's; governance's crash hid the config hold and governance.remote too. One FAIL is left, state_dir's,
    and the NEXT names it as the person's; disk is measured at the folder that refuses, and governance is
    still judged."""
    if sys.platform == "darwin":
        monkeypatch.setenv("AGENTSYNC_TM_EXCLUDE", "1")  # every test sets 0; a Mac runs it (Z46)
    cfg = str(initialised.config_path)
    path = initialised.config_path
    text = '\n[governance]\nhold = true\nhold_reason = "Contoso matter 7"\nhold_owner = "Contoso legal"\n'
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = expand(initialised.state_paths.root)
    above = root.parent
    assert root.is_relative_to(Path.home()) and above != Path.home(), root
    capsys.readouterr()
    above.chmod(0)
    try:
        rc, out = _status(cfg, capsys)
        named = {r.name: r for r in doctor.run_checks(initialised)}
    finally:
        above.chmod(0o700)
    fails = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1, fails
    assert re.match(r"\[FAIL\] state_dir +— cannot reach ", fails[0]), fails
    assert f"{root}: Permission denied (fix: the person " in fails[0], fails
    assert out[0].startswith(
        "NEXT: the state_dir check failed: its fix on the [FAIL] line below is the person's"
    )
    assert not any("could not read" in ln or "report it" in ln for ln in out), out
    for disk in ("disk.state", "disk.docs"):
        assert named[disk].ok, named[disk]
    assert named["disk.state"].detail.endswith(f" free at {above}"), named["disk.state"]
    assert [ln for ln in out if ln.startswith("[ok  ] governance.remote ")], out
    assert [ln for ln in out if ln.startswith("[ok  ] governance.hold ") and "Contoso matter 7" in ln], out
    if sys.platform == "darwin":
        assert [
            ln
            for ln in out
            if ln.startswith("[info] governance.time_machine ") and doctor.STATE_FOLDER_UNCHECKED in ln
        ], out
    # the state folder itself at 0000 (its parent searchable) is still measured there, as before
    root.chmod(0)
    try:
        named = {r.name: r for r in doctor.run_checks(initialised)}
    finally:
        root.chmod(0o700)
    assert named["disk.state"].ok and named["disk.state"].detail.endswith(f" free at {root}"), named[
        "disk.state"
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_library_refusing_a_search_or_a_linked_state_folder_is_still_one_fail_the_persons(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 22 ruling 2, lane B's reviewer: with ~/Library at 0000, the default cache and log folders
    beside the state folder are behind the same refusing folder, and the permissions and logs checks gave
    generic "report it" FAILs that took status's NEXT; they say not checked. And a state folder that is a
    symlink: the parent fix and the disk check read through the link, to the folder that refuses."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = expand(initialised.state_paths.root)
    library = Path.home() / "Library"
    assert root.is_relative_to(library), root
    capsys.readouterr()
    library.chmod(0)
    try:
        rc, out = _status(cfg, capsys)
    finally:
        library.chmod(0o700)
    fails = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert rc == cli.EXIT_FAILED and len(fails) == 1 and re.match(r"\[FAIL\] state_dir +— ", fails[0]), fails
    assert out[0].startswith(
        "NEXT: the state_dir check failed: its fix on the [FAIL] line below is the person's"
    )
    assert not any("could not read" in ln or "report it" in ln for ln in out), out
    # a state folder that links to another folder whose parent refuses a search
    vol = tmp_path / "vol"
    vol.mkdir()
    root.rename(vol / "agentsync")
    root.symlink_to(vol / "agentsync")
    vol.chmod(0)
    try:
        fix = doctor.state_folder_fix(root)
        named = {r.name: r for r in doctor._check_disk(initialised)}
    finally:
        vol.chmod(0o700)
        root.unlink()
        (vol / "agentsync").rename(root)
    assert f"search access to {doctor._shell_path(vol)}, a folder above" in fix, fix
    assert named["disk.state"].detail.endswith(f" free at {vol}"), named["disk.state"]


@pytest.mark.parametrize("where", ["the state folder", "a folder above it"])
def test_disk_state_over_a_file_where_a_folder_belongs_names_the_folder_that_holds_it(
    initialised: Config, where: str
) -> None:
    """B13 round 24 B28: with a file where the state folder (or a folder above it) belongs, disk.state said
    "<free> free at <that file>", beside a state_dir FAIL calling the same path a file, not a folder. It stays
    measured (round 23 ruling 4; the same volume) and names the folder that holds the file."""
    root = expand(initialised.state_paths.root)
    if root.exists():
        root.rename(root.with_name(root.name + ".kept"))
    file = root if where == "the state folder" else root.parent
    if where == "a folder above it":
        file.rename(file.with_name(file.name + ".kept"))
    file.write_text("not a folder\n", encoding="utf-8")
    disk = {r.name: r for r in doctor._check_disk(initialised)}["disk.state"]
    assert disk.ok, disk
    assert f" free at {file.parent}, the folder that holds {file}, which is not a folder (see state_dir)" in (
        disk.detail
    ), disk.detail
    assert not disk.detail.endswith(str(file)), disk.detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a folder of any mode")
def test_two_sources_over_a_shut_state_folder_give_status_one_fail(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 21 Y11, Y26: X73's status test had one source, so the second, generic FAIL that
    ``sources.shared_content`` gave over a shut state folder (``could not read <db> ... report it``) never
    showed. With two sources status says it is not checked, and the only state FAIL is state_dir's."""
    cfg = str(initialised.config_path)
    second = tmp_path / "contoso-b"
    second.mkdir()
    (second / "notes.txt").write_text("Contoso notes\n", encoding="utf-8")
    assert cli.main(["add-source", str(second), "--config", cfg]) == cli.EXIT_OK
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = initialised.state_paths.root
    root.chmod(0)
    try:
        capsys.readouterr()
        _rc, out = _status(cfg, capsys)
    finally:
        root.chmod(0o700)
    assert not any(ln.startswith("[FAIL] sources.shared_content ") for ln in out), out
    (shared,) = [ln for ln in out if ln.startswith("[info] sources.shared_content ")]
    assert doctor.STATE_FOLDER_UNCHECKED in shared, shared
    fails = [
        ln for ln in out if ln.startswith("[FAIL] ") and not re.match(r"\[FAIL\] (logs|permissions) ", ln)
    ]
    assert not any("could not read" in ln for ln in fails), fails


def test_a_permission_error_outside_the_state_folder_keeps_its_traceback(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of X73: only a path in the state folder is the state's one line; any other
    PermissionError is no state of agentsync's and keeps its traceback."""
    root = tmp_path / "state"
    assert cli._in_folder(str(root / "manifest.sqlite"), root) == root / "manifest.sqlite"
    assert cli._in_folder(str(root), root) == root
    assert cli._in_folder(str(tmp_path / "elsewhere"), root) is None
    assert cli._in_folder(None, root) is None
    elsewhere = PermissionError(errno.EACCES, "Permission denied", str(tmp_path / "elsewhere"))

    def refused(*_a: object, **_k: object) -> list[str]:
        raise elsewhere

    monkeypatch.setattr(cli, "_status_lines", refused)
    monkeypatch.setattr(cli, "_build_status", refused)
    with pytest.raises(PermissionError):
        cli.main(["status", "--config", str(initialised.config_path)])


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_file_in_the_state_folder_this_account_cannot_open_gets_its_own_fix(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """X73's reviewer: the PermissionError arm gave the folder's ACL fix ("its owner and mode are right") for
    a refused file in a healthy folder, a lock at 0000. The fix names what refused it: ``chmod 600`` on the
    file. And holds.json at 0000 crashed the whole governance check and ended a sync on a Python-style
    "[Errno 13]" line: it FAILs governance.hold with the same fix, and the sync says one ``error: state:``
    line."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    lock = expand(initialised.state_paths.lock)
    holds = governance._holds_file(expand(initialised.state_paths.root))
    holds.parent.mkdir(parents=True, exist_ok=True)
    holds.write_text("[]\n", encoding="utf-8")
    for target, argv in ((lock, ["sync"]), (holds, ["hold", "--list"])):
        target.chmod(0)
        try:
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, target
            err = capsys.readouterr().err
            assert "Traceback" not in err and "[Errno" not in err, err
            (line,) = [ln for ln in err.splitlines() if ln.startswith("error: ")]
            fix = f"(fix: chmod 600 {doctor._shell_path(target)})"
            assert line.endswith(f"cannot be opened: Permission denied {fix}"), line
            if target == holds:
                rc, out = _status(cfg, capsys)
                assert rc == cli.EXIT_FAILED and not any("check crashed" in ln for ln in out), out
                (hold,) = [ln for ln in out if ln.startswith("[FAIL] governance.hold ")]
                assert hold.endswith(fix), hold
        finally:
            target.chmod(0o600)


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
@pytest.mark.parametrize("mode", [0o000, 0o400])
def test_an_audit_trail_this_account_cannot_append_to_stops_a_hold_and_its_release_and_status_fails_it(
    initialised: Config, capsys: pytest.CaptureFixture[str], mode: int
) -> None:
    """B13 round 23 A79: audit.jsonl at 0000 (or 0400): ``hold all`` and ``hold all --release`` exited 1 yet
    took effect, holds.json written first and the audit line refused, so a release removed protection with
    no record; and no status check read the file, so status said 0 FAIL. The audit trail is opened first now:
    a refused one changes nothing, and status FAILs governance.audit with the agent's chmod, which the report
    files as the agent's, not as unexpected."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    holds, audit = governance._holds_file(root), governance.audit_path(root)
    owner = ["--owner", "Contoso Legal", "--config", cfg]
    assert cli.main(["hold", "source", "--reason", "matter 7", *owner]) == cli.EXIT_OK
    before = (holds.read_bytes(), audit.read_bytes())
    fix = f"(fix: chmod 600 {doctor._shell_path(audit)})"
    audit.chmod(mode)
    try:
        for argv in (["hold", "all", "--reason", "matter 8"], ["hold", "source", "--release"]):
            capsys.readouterr()
            assert cli.main([*argv, *owner]) == cli.EXIT_FAILED, argv
            err = capsys.readouterr().err
            (line,) = [ln for ln in err.splitlines() if ln.startswith("error: ")]
            assert line.endswith(f"audit.jsonl cannot be opened: Permission denied {fix}"), line
            assert holds.read_bytes() == before[0], argv  # nothing changed
        rc, out = _status(cfg, capsys)
        assert rc == cli.EXIT_FAILED, out
        (failed,) = [ln for ln in out if ln.startswith("[FAIL] governance.audit ")]
        assert failed.endswith(fix), failed
        assert setup_report._refused_files(initialised).get("governance.audit") == setup_report._AGENTS
    finally:
        audit.chmod(0o600)
    assert audit.read_bytes() == before[1]
    rc, out = _status(cfg, capsys)
    assert not any("governance.audit" in ln for ln in out), out
    assert cli.main(["hold", "source", "--release", *owner]) == cli.EXIT_OK
    assert governance.state_holds(root) == ()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a folder of any mode")
@pytest.mark.parametrize("mode", [0o500, 0o100, 0o000])
def test_a_governance_folder_this_account_cannot_write_into_fails_status_with_its_chmod(
    initialised: Config, capsys: pytest.CaptureFixture[str], mode: int
) -> None:
    """B13 round 24 B61: governance/ at 0500 refused ``hold all`` and ``hold source --release`` (their temp
    file), while status read holds.json there and passed with 0 FAIL. Status FAILs governance.hold with the
    folder's chmod 700, keeps the holds it can read, and the report files it as the agent's; a fixed folder
    and a missing one say nothing."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    gov = governance.governance_dir(root)
    owner = ["--owner", "Contoso Legal", "--config", cfg]
    assert cli.main(["hold", "source", "--reason", "matter 7", *owner]) == cli.EXIT_OK
    fix = f"(fix: chmod 700 {doctor._shell_path(Path(os.path.realpath(gov)))})"
    gov.chmod(mode)
    try:
        capsys.readouterr()
        assert cli.main(["hold", "all", "--reason", "matter 8", *owner]) == cli.EXIT_FAILED
        rc, out = _status(cfg, capsys)
        whose = setup_report._refused_files(initialised).get("governance.hold")
    finally:
        gov.chmod(0o700)
    assert rc == cli.EXIT_FAILED, out
    (failed,) = [ln for ln in out if ln.startswith("[FAIL] governance.hold ")]  # its fix said once
    assert failed.endswith(fix), failed
    assert "refuses this account a write" in failed or mode == 0o000, failed
    if mode == 0o500:  # the holds in force stay visible: a 0500 folder still reads
        assert any(ln.startswith("[ok  ] governance.hold ") and "matter 7" in ln for ln in out), out
    assert whose == setup_report._AGENTS, whose
    rc, out = _status(cfg, capsys)
    assert not any(ln.startswith("[FAIL] governance.hold ") for ln in out), out
    assert cli.main(["hold", "source", "--release", *owner]) == cli.EXIT_OK
    for p in sorted(gov.iterdir()):
        p.unlink()
    gov.rmdir()
    rc, out = _status(cfg, capsys)
    assert not any(ln.startswith("[FAIL] governance.") for ln in out), out


@pytest.mark.parametrize("sticky", [False, True])
def test_another_accounts_governance_folder_this_account_can_write_passes_unless_sticky(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, sticky: bool
) -> None:
    """B13 round 25 D45: governance.hold FAILed "refuses this account a write" with the person's sudo chown
    for another account's governance/ folder this account can write (0777), where a hold works. Keyed on what
    a hold does: a writable folder passes whoever owns it; a sticky one over another account's holds.json,
    which no rename replaces, FAILs in its own words."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    gov = governance.governance_dir(root)
    owner = ["--owner", "Contoso Legal", "--config", cfg]
    assert cli.main(["hold", "source", "--reason", "matter 7", *owner]) == cli.EXIT_OK
    queue = governance._queue_file(root)
    if not queue.exists():
        queue.write_text("{}\n", encoding="utf-8")
    gov.chmod(0o1777 if sticky else 0o777)
    try:
        me = os.getuid()
        with monkeypatch.context() as other:  # this account is another one: the files are not its own
            other.setattr(cli.os, "getuid", lambda: me + 1)
            checks = cli._governance_folder_checks(initialised)
            refused = setup_report._refused_files(initialised)
            whose = refused.get("governance.hold")
            queued = refused.get("governance.purge_queue")
        if not sticky:
            assert cli.main(["hold", "all", "--reason", "matter 8", *owner]) == cli.EXIT_OK
    finally:
        gov.chmod(0o700)
    capsys.readouterr()
    if sticky:
        (fail,) = checks
        holds = governance._holds_file(root).name
        assert (
            fail.detail
            == f"{gov} is a sticky folder and {holds} is another account's: a hold cannot replace it"
        )
        assert fail.fix is not None and doctor.is_persons_fix(fail.fix) and whose is not None, (fail, whose)
    else:
        assert checks == [] and whose is None and queued is None, (checks, whose, queued)
    # a sticky folder this account owns: its owner replaces any file in it, another account's included
    gov.chmod(0o1777)
    try:
        with monkeypatch.context() as other:
            other.setattr(governance, "_holds_file", lambda _root: Path("/etc/hosts"))  # root's file
            assert governance.sticky_blocker(root) is None
            other.setattr(governance.os, "getuid", lambda: Path("/etc/hosts").stat().st_uid + 1)
            assert governance.sticky_blocker(root) is not None  # another account's sticky folder
    finally:
        gov.chmod(0o700)


def test_a_fifo_where_the_audit_trail_belongs_fails_status_and_stops_a_hold_without_a_hang(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 24 B63: a fifo at governance/audit.jsonl hung status (A79's write-only open waits for a
    reader that never comes), and ``hold`` with it. Status FAILs governance.audit with the words and the
    person's fix state_dir.files gives a state file that is not a file; a hold stops at once with one error
    line and changes nothing."""
    import signal  # noqa: PLC0415

    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    audit = governance.audit_path(root)
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.unlink(missing_ok=True)
    os.mkfifo(audit)

    def hung(*_: object) -> None:
        raise TimeoutError("hung on the fifo")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(20)
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        held = cli.main(["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg])
        err = capsys.readouterr().err
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert rc == cli.EXIT_FAILED, out
    (failed,) = [ln for ln in out if ln.startswith("[FAIL] governance.audit ")]
    moved = f"(fix: the person moves {doctor._shell_path(audit)} aside, keeping a copy)"
    assert f"{audit} is a fifo, not a file: the audit trail belongs there {moved}" in failed, failed
    assert held == cli.EXIT_FAILED
    (line,) = [ln for ln in err.splitlines() if ln.startswith("error: ")]
    # status's (D41), as every state file that is not a file is said (B13 round 26 E18, E43; ruling 13)
    assert line == f"error: state: {audit} is a fifo, not a file: the audit trail belongs there {moved}", line
    assert governance.state_holds(root) == ()
    audit.unlink()


@pytest.mark.parametrize("what", ["a socket", "a folder", "a symlink loop", "a symlink to a folder"])
def test_anything_not_a_file_at_the_audit_trail_stops_a_hold_with_statuss_line_not_a_traceback(
    initialised: Config, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    """B13 round 25 D41: B63 refused only the fifo's ENXIO, so a folder (EISDIR) or a socket (EOPNOTSUPP) at
    governance/audit.jsonl ended ``hold`` and its release on a traceback. Each is refused, before holds.json
    changes, with the line status's governance.audit FAIL gives, word for word."""
    import socket  # noqa: PLC0415

    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    audit = governance.audit_path(root)
    hold = ["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg]
    assert cli.main(hold) == cli.EXIT_OK
    held = governance.state_holds(root)
    audit.unlink()
    sock = None
    if what == "a socket":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        here = Path.cwd()
        os.chdir(audit.parent)  # a tmp path is longer than AF_UNIX takes
        try:
            sock.bind(audit.name)
        finally:
            os.chdir(here)
    elif what == "a folder":
        audit.mkdir()
    elif what == "a symlink loop":
        audit.symlink_to(audit)
    else:
        (root / "elsewhere").mkdir()
        audit.symlink_to(root / "elsewhere")
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        (failed,) = [ln for ln in out if ln.startswith("[FAIL] governance.audit ")]
        released = cli.main(["hold", "all", "--release", "--owner", "Contoso Legal", "--config", cfg])
        err = capsys.readouterr().err
    finally:
        if sock is not None:
            sock.close()
    moved = f"(fix: the person moves {doctor._shell_path(audit)} aside, keeping a copy)"
    words = f"{audit} is {what}, not a file: the audit trail belongs there {moved}"
    assert rc == cli.EXIT_FAILED and failed.endswith(f" — {words}"), failed
    assert released == cli.EXIT_FAILED and err.splitlines() == [f"error: state: {words}"], err
    assert governance.state_holds(root) == held  # nothing changed
    # the list reads holds.json alone: a broken audit trail never hides the holds (B13 round 26 E43)
    assert cli.main(["hold", "--list", "--config", cfg]) == cli.EXIT_OK
    assert "1 active hold" in capsys.readouterr().out


def test_a_link_to_a_file_at_the_audit_trail_still_appends(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 25 D41's verifier: a symlink at audit.jsonl to a regular file works, and status passes it;
    refusing it would be a regression."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    audit = governance.audit_path(root)
    audit.parent.mkdir(parents=True, exist_ok=True)
    real = root / "audit-real.jsonl"
    real.write_text("", encoding="utf-8")
    real.chmod(0o600)
    audit.unlink(missing_ok=True)
    audit.symlink_to(real)
    assert cli.main(["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg]) == 0
    assert real.read_text(encoding="utf-8").count("\n") >= 1
    capsys.readouterr()


@pytest.mark.parametrize("where", ["heartbeat", "lock", "holds", "heartbeat, no manifest", "lock holder"])
def test_a_fifo_at_a_state_file_status_reads_never_hangs_it(
    initialised: Config, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """B13 round 25 D46: B63 hardened only the audit trail; a fifo at heartbeat.json (read by manifest_lost
    with no manifest, by read_heartbeat with one), at the lock (is_held's probe) or at governance/holds.json
    hung status on a plain blocking open, and inside the lock every later sync. Status returns, and a
    completed pass writes a regular heartbeat.json over the fifo."""
    import signal  # noqa: PLC0415

    cfg = _synced(initialised)
    sp = initialised.state_paths
    root = expand(sp.root)
    target = {
        "heartbeat": expand(sp.heartbeat),
        "heartbeat, no manifest": expand(sp.heartbeat),
        "lock": expand(sp.lock),
        "holds": governance._holds_file(root),
        "lock holder": lock_mod._holder_path(expand(sp.lock)),
    }[where]
    if where == "heartbeat, no manifest":
        db = expand(sp.db)
        for name in ("", "-wal", "-shm"):
            db.with_name(db.name + name).unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    os.mkfifo(target)

    def hung(*_: object) -> None:
        raise TimeoutError(f"hung on the fifo at {where}")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(30)
    try:
        capsys.readouterr()
        rc = cli.main(["status", "--config", cfg])
        capsys.readouterr()
        if where == "heartbeat":
            with pytest.raises(ValueError, match="not a regular file"):
                read_heartbeat(target)
            write_heartbeat(
                target,
                "contoso",
                run_id=9,
                pass_kind=None,
                enumeration_complete=True,
                ok=True,
                auth_state="ok",
                now_iso="2026-10-23T00:00:00Z",
            )
            assert stat.S_ISREG(target.lstat().st_mode) and read_heartbeat(target)["contoso"]["run_id"] == 9
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert rc in (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_STATE), rc
    target.unlink(missing_ok=True)


def _make_not_a_file(path: Path, what: str) -> Any:
    """Put ``what`` (status's words for it) at ``path``; a socket's object, to close, or None."""
    import socket  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    if what == "a folder":
        path.mkdir()
    elif what == "a fifo":
        os.mkfifo(path)
    elif what == "a socket":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        here = Path.cwd()
        os.chdir(path.parent)  # a tmp path is longer than AF_UNIX takes
        try:
            sock.bind(path.name)
        finally:
            os.chdir(here)
        return sock
    elif what == "a symlink to nothing":
        path.symlink_to(path.parent / "gone" / path.name)
    elif what == "a symlink to a file":
        real = path.with_name(path.name + ".real")
        real.write_text("", encoding="utf-8")
        path.symlink_to(real)
    elif what == "a symlink loop":
        path.symlink_to(path)
    elif what == "a link through a file":
        afile = path.parent / "afile"
        afile.write_text("", encoding="utf-8")
        path.symlink_to(afile / path.name)
    else:
        raise AssertionError(what)
    return None


@pytest.mark.parametrize(
    ("where", "what"),
    [
        ("lock", "a folder"),
        ("lock", "a fifo"),
        ("lock", "a socket"),
        ("lock", "a symlink to nothing"),
        ("lock", "a symlink to a file"),
        ("lock", "a symlink loop"),
        ("heartbeat", "a folder"),
        ("holds", "a folder"),
        ("holds", "a fifo"),
        ("queue", "a folder"),
        ("suppressed", "a fifo"),
        # B13 round 27 P131, P132, P133 (ruling 15): every link kind at every governance file
        ("holds", "a symlink to nothing"),
        ("queue", "a symlink to nothing"),
        ("suppressed", "a symlink to nothing"),
        ("audit", "a symlink to nothing"),
        ("holds", "a link through a file"),
        ("queue", "a link through a file"),
        ("suppressed", "a link through a file"),
        ("audit", "a link through a file"),
        ("holds", "a symlink loop"),
        ("queue", "a symlink loop"),
        ("suppressed", "a symlink loop"),
        ("audit", "a symlink loop"),
    ],
)
def test_a_state_file_that_is_not_a_file_is_the_persons_in_status_and_every_command(
    initialised: Config, capsys: pytest.CaptureFixture[str], where: str, what: str
) -> None:
    """B13 round 26 E38, E39, E43 (ruling 13): a folder, fifo or link at the lock read "lock — free" while
    every sync ended on a traceback; a folder at heartbeat.json was a warn promising the next pass rewrites
    it, which no rename can; a fifo or folder at holds.json crashed the whole governance check as "a fault
    in agentsync". Each is the person's: status FAILs it in the words every command that opens it refuses
    with (sync, curate, hold, its release, purge), with its fix and a NEXT, exit 1, no traceback, no wait on
    a fifo, and nothing written; status's other lines still print. Following the fix leads to a clean sync."""
    import signal  # noqa: PLC0415

    cfg = _synced(initialised)
    sp = initialised.state_paths
    root = expand(sp.root)
    gov = governance.governance_dir(root)
    path, role, check = {
        "lock": (expand(sp.lock), "the lock", "lock"),
        "heartbeat": (expand(sp.heartbeat), "the heartbeat", "heartbeat"),
        "holds": (gov / "holds.json", "the hold list", "governance.hold"),
        "queue": (gov / "purge-queue.json", "the purge queue", "governance.purge_queue"),
        "suppressed": (gov / "suppressed.json", "the suppression list", "governance.suppressed"),
        "audit": (gov / "audit.jsonl", "the audit trail", "governance.audit"),
    }[where]
    sock = _make_not_a_file(path, what)
    if what == "a link through a file":  # a file where a folder on its way belongs: no target (P132)
        what = "a symlink to nothing"
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)

    def hung(*_: object) -> None:
        raise TimeoutError(f"hung on {what} at {where}")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(60)
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        words = f"{path} is {what}, not a file: {role} belongs there"
        (failed,) = [ln for ln in out if ln.startswith(f"[FAIL] {check} ")]
        assert words in failed and "keeping a copy)" in failed, failed
        assert rc == cli.EXIT_FAILED, out
        assert not [ln for ln in out if "check crashed" in ln or "Error" in ln], out
        assert any(ln.startswith("last runs: ") for ln in out), out  # the rest still prints
        if where == "lock":  # never "free" (E38)
            assert not [ln for ln in out if ln.endswith("— free") or ln == "lock: free"], out
        assert not [ln for ln in out if "report it" in ln or "unreadable" in ln], out
        line = "error: state: " + failed.split(" — ", 1)[1]
        commands = [
            ["sync"],
            ["curate"],
            ["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal"],
            ["hold", "all", "--release", "--owner", "Contoso Legal"],
            ["purge", "--queue"],
        ]
        if where == "holds":
            commands.append(["hold", "--list"])
        for argv in commands:
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
            said = capsys.readouterr()
            assert said.err.splitlines() == [line], (argv, said.err)
            assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")], (argv, said.out)
        assert governance.state_holds(root) == () if where != "holds" else True
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
        if sock is not None:
            sock.close()
    file = {"lock": "lock", "heartbeat": "heartbeat", "holds": "holds"}.get(where, "governance")
    (stop,) = stops.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(rf"\S+ sync-stop file={file} rc=1 owner=person kind=misplaced", stop), stop
    path.rename(path.with_name(path.name + ".kept"))  # the person's fix, followed
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()


@pytest.mark.parametrize("dangles", ["into a gone folder", "into a folder that is there"])
def test_a_hold_list_behind_a_link_to_nothing_is_never_read_as_no_holds(
    initialised: Config, capsys: pytest.CaptureFixture[str], dangles: str
) -> None:
    """B13 round 27 P131 (ruling 15): with a hold on all and a DLP purge queued, holds.json moved aside and
    a link to nothing put in its place passed status, `hold --list` showed nothing, and `purge --queue`
    erased the page under the hold, history and all. The link is the person's: status FAILs it, and
    `hold --list`, `purge --queue` and `purge docs=` refuse in status's words with a NEXT; the page and its
    commit stay."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    hold = ["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg]
    assert cli.main(hold) == cli.EXIT_OK
    dlp = governance.PurgeSelector.parse(f"docs={page}", source_id=None)
    assert governance.enqueue_purge(root, dlp, governance.PurgeReason.DLP_REMEDIATION)
    holds = governance._holds_file(root)
    holds.rename(root / "holds.kept")
    there = holds.parent / "elsewhere"
    if dangles == "into a folder that is there":
        there.mkdir()
    holds.symlink_to(there / "holds.json")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    words = f"{holds} is a symlink to nothing, not a file: the hold list belongs there"
    (failed,) = [ln for ln in out if ln.startswith("[FAIL] governance.hold ")]
    assert rc == cli.EXIT_FAILED and words in failed and "aside, keeping a copy)" in failed, out
    assert not [ln for ln in out if "purge --queue" in ln], "no purge is invited over the hold list"
    line = "error: state: " + failed.split(" — ", 1)[1]
    for argv in (["hold", "--list"], ["purge", "--queue"], ["purge", f"docs={page}"]):
        capsys.readouterr()
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
        said = capsys.readouterr()
        assert said.err.splitlines() == [line], (argv, said.err)
        assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")], (argv, said.out)
    assert (repo / page).is_file() and git(repo, "log", "--all", "--oneline", "--", page).strip()
    assert not there.joinpath("holds.json").exists(), "nothing created through the link"


@pytest.mark.parametrize("what", ["a regular file", "a link to a regular file", "nothing"])
def test_a_working_hold_list_still_passes(
    initialised: Config, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    """P131's verifier: the judge of a link to nothing never newly FAILs a working setup."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    assert cli.main(["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg]) == 0
    holds = governance._holds_file(root)
    if what == "a link to a regular file":
        holds.rename(root / "holds.real")
        holds.symlink_to(root / "holds.real")
    elif what == "nothing":
        holds.unlink()
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if ln.startswith("[FAIL] governance")], out
    assert cli.main(["hold", "--list", "--config", cfg]) == cli.EXIT_OK


def test_a_loop_at_the_governance_folder_never_sends_the_person_to_a_file_under_it(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """P133's verifier: with governance/ itself a symlink loop every file under it opens with ELOOP; no
    line may tell the person to move holds.json, purge-queue.json or suppressed.json aside, a move that
    fails under a looping folder."""
    cfg = _synced(initialised)
    gov = governance.governance_dir(expand(initialised.state_paths.root))
    if gov.exists():
        gov.rename(gov.with_name("governance.kept"))
    gov.symlink_to(gov)
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    said = list(out)
    for argv in (["sync"], ["hold", "--list"], ["purge", "--queue"]):
        cli.main([*argv, "--config", cfg])
        both = capsys.readouterr()
        said += both.out.splitlines() + both.err.splitlines()
    for name in ("holds.json", "purge-queue.json", "suppressed.json", "audit.jsonl"):
        assert not [ln for ln in said if f"{name} aside" in ln], (name, said)


@pytest.mark.parametrize("what", ["a folder", "a fifo"])
def test_the_lock_refuses_anything_not_a_file_with_its_path_never_a_traceback(
    tmp_path: Path, what: str
) -> None:
    """B13 round 26 E38: the acquire's open of a folder raised IsADirectoryError and a fifo's flock
    OSError(EOPNOTSUPP), each a traceback in any command no guard covers; both are LockNotAFileError, an
    OSError naming the lock, which the CLI words as status's lock FAIL."""
    path = tmp_path / "agentsync.lock"
    _make_not_a_file(path, what)
    with pytest.raises(LockNotAFileError) as caught:
        lock_mod.SingleWriterLock(path, "poll").acquire()
    assert caught.value.filename == str(path)


def test_a_link_to_a_folder_at_heartbeat_json_stays_a_warn_the_next_pass_clears(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 26 E39's verifier: a pass's rename replaces a link to a folder at heartbeat.json (the link,
    not its target), so its warn's promise is true there, and only a real folder FAILs."""
    cfg = _synced(initialised)
    hb = expand(initialised.state_paths.heartbeat)
    hb.unlink()
    (hb.parent / "elsewhere").mkdir()
    hb.symlink_to(hb.parent / "elsewhere")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_OK, out
    assert any(
        ln.startswith("[warn] heartbeat ") and "the next completed pass rewrites it" in ln for ln in out
    )
    assert not [ln for ln in out if ln.startswith("[FAIL] heartbeat ")], out
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert stat.S_ISREG(hb.lstat().st_mode)
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if ln.startswith(("[warn] heartbeat ", "[FAIL] heartbeat "))], out


@pytest.mark.parametrize("what", ["a symlink loop", "a link through a file"])
def test_a_link_at_heartbeat_json_that_reaches_no_file_is_rewritten_by_the_next_pass(
    initialised: Config, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    """B13 round 27 P134 (ruling 15): a loop or a link through a file at heartbeat.json was a status FAIL
    "could not read ... report it" for good, and every sync ended 0 leaving the link. It is the warn whose
    promise is true: the next completed pass rewrites it, a regular file, and status passes it."""
    cfg = _synced(initialised)
    hb = expand(initialised.state_paths.heartbeat)
    hb.unlink()
    _make_not_a_file(hb, what)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_OK, out
    assert any(
        ln.startswith("[warn] heartbeat ") and ln.endswith("the next completed pass rewrites it")
        for ln in out
    ), out
    assert not [ln for ln in out if ln.startswith("[FAIL] heartbeat ") or "report it" in ln], out
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert stat.S_ISREG(hb.lstat().st_mode)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_OK and not [
        ln for ln in out if ln.startswith(("[warn] heartbeat ", "[FAIL] heartbeat "))
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 folder")
@pytest.mark.parametrize("where", ["heartbeat", "holds", "queue", "suppressed", "audit"])
def test_a_shut_folder_at_a_state_file_takes_its_chmod_then_the_move_in_status_and_every_command(
    initialised: Config, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """B13 round 27 P135 (ruling 15): a 0000 folder at heartbeat.json or a governance file got status's
    agent chmod 700 alone (the open's EACCES came before any kind), after which the next sync still stopped,
    and the governance commands the person's move aside with no chmod first, which macOS refuses. Status and
    every command say the same: the chmod first, then the move aside; followed, the next sync runs clean."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    gov = governance.governance_dir(expand(sp.root))
    path, check = {
        "heartbeat": (expand(sp.heartbeat), "heartbeat"),
        "holds": (gov / "holds.json", "governance.hold"),
        "queue": (gov / "purge-queue.json", "governance.purge_queue"),
        "suppressed": (gov / "suppressed.json", "governance.suppressed"),
        "audit": (gov / "audit.jsonl", "governance.audit"),
    }[where]
    _make_not_a_file(path, "a folder")
    path.chmod(0o000)
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        (failed,) = [ln for ln in out if ln.startswith(f"[FAIL] {check} ")]
        first = f"the person runs chmod 700 {doctor._shell_path(path)} first, which is reversible"
        assert rc == cli.EXIT_FAILED and first in failed and "aside, keeping a copy" in failed, failed
        line = "error: state: " + failed.split(" — ", 1)[1]
        for argv in (["sync"], ["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal"]):
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
            assert capsys.readouterr().err.splitlines() == [line], argv
        path.chmod(0o700)  # the words, followed: the chmod, then the move aside
        path.rename(path.with_name(path.name + ".kept"))
    finally:
        if path.exists():
            path.chmod(0o700)
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()


@pytest.mark.parametrize(
    "argv",
    [["checkpoint"], ["accept-deletions", "source"], ["reconcile"], ["materialise"]],
    ids=["checkpoint", "accept-deletions", "reconcile", "materialise"],
)
@pytest.mark.parametrize("where", ["heartbeat folder", "holds folder", "holds fifo"])
def test_every_cycle_command_runs_syncs_guard_of_a_state_file_that_is_not_a_file(
    initialised: Config, capsys: pytest.CaptureFixture[str], argv: list[str], where: str
) -> None:
    """B13 round 27 P43, P136 (one defect; ruling 15): reconcile, accept-deletions, materialise and the
    checkpoint alias ran their cycle over a folder at heartbeat.json or a folder or fifo at holds.json and
    exited 0 with WARNINGs, where sync refuses. Each refuses with status's line and the command's NEXT, exit
    1, records no sync stop and leaves the folder."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    path = expand(sp.heartbeat) if where == "heartbeat folder" else governance._holds_file(expand(sp.root))
    _make_not_a_file(path, where.split()[1].replace("folder", "a folder").replace("fifo", "a fifo"))
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    before = stops.read_text(encoding="utf-8") if stops.exists() else ""
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    (failed,) = [ln for ln in out if ln.startswith(("[FAIL] heartbeat ", "[FAIL] governance.hold "))]
    capsys.readouterr()
    assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
    said = capsys.readouterr()
    assert said.err.splitlines() == ["error: state: " + failed.split(" — ", 1)[1]], said.err
    assert said.out.splitlines()[-1] == loop.NEXT_PREFIX + cli.STATE_MISPLACED_COMMAND_STEP, said.out
    assert (stops.read_text(encoding="utf-8") if stops.exists() else "") == before
    assert path.is_dir() if "folder" in where else stat.S_ISFIFO(path.lstat().st_mode)


@pytest.mark.parametrize("started_by", ["other", "launchd", None])
def test_a_stale_lock_names_a_crash_log_only_where_one_is(
    initialised: Config, started_by: str | None
) -> None:
    """B13 round 26 E12: after an interactive sync was killed, the stale-lock warn told the person to read
    Library/Logs/agentsync/*.err.log, which did not exist. The fix comes from the holder sidecar of the same
    pid: the job's own err.log when it is there; none for a sync started elsewhere; with no sidecar, the log
    folder's err.logs only when there are any."""
    lock = expand(initialised.state_paths.lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    info = lock_mod.LockInfo(pid=999_999, boot_time=1, started_at="2026-10-23T00:00:00Z", label="poll")
    lock.write_text(info.render() + "\n", encoding="utf-8")
    if started_by is not None:
        holder = {"pid": 999_999, "label": "poll", "started_by": started_by}
        lock_mod._holder_path(lock).write_text(json.dumps(holder), encoding="utf-8")
    log_dir = expand(initialised.log_dir)
    assert not log_dir.exists() or not list(log_dir.glob("*.err.log"))
    (stale,) = doctor._check_lock(initialised)
    assert not stale.ok and "ended without releasing it" in stale.detail, stale
    assert stale.fix is None, stale  # no log there to read
    if started_by == "other":
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    err = log_dir / f"{initialised.launchd_label_prefix}.poll.err.log"
    err.write_text("Traceback\n", encoding="utf-8")
    (stale,) = doctor._check_lock(initialised)
    want = f"read the crash in {err}" if started_by == "launchd" else f"read the crash in {log_dir}/*.err.log"
    assert stale.fix == want, stale


def test_a_state_path_another_account_owns_gets_the_persons_chown(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X73's reviewer: a lock or heartbeat file still another account's after a chown that was not
    recursive needs the person's ``sudo chown -R`` of the folder."""
    root = expand(sample_config.state_paths.root)
    lock = root / "agentsync.lock"
    lock.write_text("", encoding="utf-8")
    me = os.getuid()
    monkeypatch.setattr(doctor.os, "getuid", lambda: me + 1)
    assert doctor.state_path_fix(root, lock) == doctor.state_folder_fix(root) == doctor._persons_chown(root)


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_lock_or_heartbeat_this_account_cannot_open_names_its_own_fix(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 21 Y43: agentsync.lock or heartbeat.json at 0000 gave status the generic "could not read ...
    report it" FAIL, while sync named the lock's ``chmod 600``; the trailing ``status: cannot read the state``
    line said "the state_dir lines say why" over state_dir lines that read [ok]; and the heartbeat added a
    timestamped WARNING. Each FAILs with ``doctor.state_path_fix``'s fix (another account's file: the
    person's chown), the pointer names the line that judges the file, and status logs no WARNING."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = Path(os.path.realpath(expand(initialised.state_paths.root)))
    for name, target in (
        ("lock", expand(initialised.state_paths.lock)),
        ("heartbeat", expand(initialised.state_paths.heartbeat)),
    ):
        assert target.exists(), target
        target.chmod(0)
        try:
            for theirs in (False, True):
                capsys.readouterr()
                with monkeypatch.context() as other:
                    if theirs:  # another account's file (a sudo sync's): the person's chown of the folder
                        me = os.getuid()
                        other.setattr(doctor.os, "getuid", lambda me=me: me + 1)
                    rc = cli.main(["status", "--config", cfg])
                    want = doctor.state_path_fix(root, Path(os.path.realpath(target)))
                    mine = f"chmod 600 {doctor._shell_path(target)}"
                    assert want == (doctor._persons_chown(root) if theirs else mine), want
                captured = capsys.readouterr()
                out = captured.out.splitlines()
                assert rc == cli.EXIT_FAILED, out
                (line,) = [ln for ln in out if ln.startswith(f"[FAIL] {name} ")]
                assert line.endswith(f"cannot be opened: Permission denied (fix: {want})"), line
                assert not any("report it" in ln for ln in out if ln.startswith("[FAIL] ")), out
                assert "WARNING" not in captured.err, captured.err
                state_fails = [ln for ln in out if re.match(r"\[FAIL\] state_dir(\.files)? ", ln)]
                assert state_fails or not any("state_dir lines say why" in ln for ln in out), out
                said = [ln for ln in out if "cannot read the state" in ln]
                assert said and all(f"the {name} [FAIL] line says why" in ln for ln in said), out
                # B13 round 25 D12: the file by its name, never "a file", beside a state file that reads
                named = f"{target.name} cannot be opened: Permission denied; the {name} [FAIL] line says why"
                assert all(ln.endswith(named) and "a file cannot be opened" not in ln for ln in said), said
        finally:
            target.chmod(0o600)


def test_source_statuses_warns_a_refused_heartbeat_only_for_a_caller_that_records_none(
    initialised: Config, caplog: pytest.LogCaptureFixture
) -> None:
    """B13 round 25 D47: no current caller takes the WARNING path (the cycle passes ``refused_said=True``,
    status ``quiet=True``); it is the default, kept so a new caller that records nothing fails loud. The
    docstring says so, and the default still warns."""
    _synced(initialised)
    beat = expand(initialised.state_paths.heartbeat)
    doc = cycle_mod.source_statuses.__doc__ or ""
    assert "which no current caller takes" in " ".join(doc.split()), doc
    beat.chmod(0)
    try:
        with Manifest(initialised.state_paths.db) as manifest, caplog.at_level(logging.DEBUG, "agentsync"):
            now = datetime.now(UTC)
            cycle_mod.source_statuses(initialised, manifest, now=now)
            loud = [r.levelno for r in caplog.records if "heartbeat unreadable" in r.getMessage()]
            caplog.clear()
            cycle_mod.source_statuses(initialised, manifest, now=now, refused_said=True)
            said = [r.levelno for r in caplog.records if "heartbeat unreadable" in r.getMessage()]
    finally:
        beat.chmod(0o600)
    assert loud == [logging.WARNING] and said == [logging.DEBUG], (loud, said)


def test_fail_steps_docstring_gives_no_stale_count_of_the_persons_kinds() -> None:
    """B13 round 25 D48: "the whole list (447 characters)" went stale when v33 made it 476; no count."""
    assert "447 characters" not in (cli._fail_step.__doc__ or "")
    assert len(doctor.PERSONS_KINDS) != 447


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-400 file")
def test_a_lock_this_account_can_read_but_not_write_fails_status_with_the_fix_sync_names(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 24 B58: agentsync.lock at 0400 passed status ("lock — free", exit 0), whose reads open it
    read-only, while every sync's read-write open stopped on it and its NEXT sent the agent to a [FAIL] line
    status never printed. Status makes the sync's open (no create, no flock) and FAILs it with the fix sync's
    error: state: line names; another account's lock this account can write still passes."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = Path(os.path.realpath(expand(initialised.state_paths.root)))
    lock = expand(initialised.state_paths.lock)
    lock.chmod(0o400)
    before = (lock.stat().st_ino, lock.stat().st_mtime_ns)
    try:
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        said = capsys.readouterr()
        whose = setup_report._refused_files(initialised).get("lock")  # not an "unexpected" FAIL in the report
        with monkeypatch.context() as other:  # another account's (a sudo sync's): the person's chown
            me = os.getuid()
            other.setattr(doctor.os, "getuid", lambda me=me: me + 1)
            theirs = {r.name: r for r in doctor._check_lock(initialised)}["lock"]
            chown = doctor._persons_chown(root)
    finally:
        lock.chmod(0o600)
    fix = f"chmod 600 {doctor._shell_path(lock)}"
    assert rc == cli.EXIT_FAILED, out
    (line,) = [ln for ln in out if ln.startswith("[FAIL] lock ")]
    assert line.endswith(f"cannot be opened: Permission denied (fix: {fix})"), line
    assert f"(fix: {fix})" in said.err, said.err
    assert whose == setup_report._AGENTS, whose
    assert not theirs.ok and theirs.fix == chown, theirs
    # nothing locked, replaced or written: "st_size == 0 or" was always true, as the release empties the lock,
    # so a status that leaked a flock passed (B13 round 25 D49); an O_CREAT on a lock already there shows
    # nothing, so none is claimed
    assert SingleWriterLock.is_held(lock) is False
    assert (lock.stat().st_ino, lock.stat().st_mtime_ns) == before
    with monkeypatch.context() as other:  # another account's lock that this one can write: sync runs
        me = os.getuid()
        other.setattr(doctor.os, "getuid", lambda me=me: me + 1)
        assert {r.name: r for r in doctor._check_lock(initialised)}["lock"].ok
        # nor does the report list it, or another account's heartbeat a sync reads and replaces (lane P's
        # B105): by the open a sync makes, never the owner alone
        expand(initialised.state_paths.heartbeat).chmod(0o644)
        listed = setup_report._refused_files(initialised)
        assert "lock" not in listed and "heartbeat" not in listed, listed


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_governance_file_this_account_cannot_open_is_named_in_words_with_its_check(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 21 Y45: holds.json (or purge-queue.json) at 0000 made status say ``cannot read the state:
    GovernanceError: ...``, a Python class name the setup report copied into its Loop line: ``_unread_words``
    missed an AgentSyncError caused by a PermissionError. It says the file's name and the system's words, and
    points at the check whose line FAILs, never one that passed."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    root = expand(initialised.state_paths.root)
    for check, target, body in (
        ("governance.hold", governance._holds_file(root), '{"holds": []}\n'),
        ("governance.purge_queue", governance._queue_file(root), '{"queue": []}\n'),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        target.chmod(0)
        try:
            capsys.readouterr()
            rc, out = _status(cfg, capsys)
        finally:
            target.chmod(0o600)
        assert rc == cli.EXIT_FAILED, out
        said = [ln for ln in out if "cannot read the state" in ln]
        want = f"{target.name} cannot be opened: Permission denied; the {check} [FAIL] line says why"
        assert said and all(ln.endswith(want) for ln in said), said
        assert not any("Error:" in ln for ln in said), said
        (fail,) = [ln for ln in out if ln.startswith(f"[FAIL] {check} ")]
        assert fail.endswith(f"(fix: chmod 600 {doctor._shell_path(Path(os.path.realpath(target)))})"), fail
        target.unlink()


def test_a_foreground_sync_that_breaks_a_stale_lock_says_it_once(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 21 Y27: a sync over a lock file holding "not a lock body" printed a timestamped ``WARNING
    agentsync.ops.lock: ... breaking stale lock left by 'not a lock body'`` line and then its own note saying
    the same; the note alone is left."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    expand(initialised.state_paths.lock).write_text("not a lock body\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "WARNING agentsync.ops.lock" not in captured.err and "not a lock body" not in captured.err, (
        captured
    )
    assert "  note: a stale lock from a dead run was broken; every source ran a full pass" in captured.out


def test_a_foreground_sync_that_breaks_a_stale_lock_and_then_stops_says_it_once(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 22 Z89: a foreground sync that broke a stale lock and then raised said nothing of the break
    below -vv: the note is in the report, which a raise never prints, the lock's own line is DEBUG for a run
    the job did not start, and release empties the file. It says so once on stderr, never "every source ran
    a full pass" (none may have run); the job's run, whose err.log keeps the WARNING, adds nothing."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    lock = expand(initialised.state_paths.lock)

    real_manifest = cycle_mod.Manifest

    def stop(db: Path) -> Manifest:  # each sync's second open, the cycle's, under the lock
        opened.append(db)
        if len(opened) % 2 == 0:
            raise KeyboardInterrupt
        return real_manifest(db)

    opened: list[Path] = []
    monkeypatch.setattr(cycle_mod, "Manifest", stop)
    note = "note: a stale lock from a dead run was broken; this sync stopped before it finished"
    lock.write_text("not a lock body\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == 130
    captured = capsys.readouterr()
    assert captured.err.count(note) == 1 and "interrupted" in captured.err, captured.err
    assert "every source ran a full pass" not in captured.err + captured.out, captured
    assert "WARNING agentsync.ops.lock" not in captured.err, captured.err
    # no stale lock, no note
    assert cli.main(["sync", "--config", cfg]) == 130
    assert note not in capsys.readouterr().err
    # the job's run: its err.log keeps the lock's WARNING, so no note
    monkeypatch.setattr(cli.launchd, "started_by_our_job", lambda c: True)
    lock.write_text("not a lock body\n", encoding="utf-8")
    assert cli.main(["sync", "--config", cfg]) == 130
    assert note not in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a mode-000 folder")
def test_a_raise_after_the_lock_is_taken_releases_it_and_an_unsearchable_log_folder_syncs(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 23 A53, A81: the log rotation and the client's setup ran after the lock was taken but outside
    the try whose finally releases it, so ~/Library/Logs at 0000 ended a sync on a raw traceback and left the
    lock's body to the next sync, which broke it as stale and ran every source as a full pass."""
    cfg = str(initialised.config_path)
    lock = expand(initialised.state_paths.lock)
    above = expand(initialised.log_dir).parent
    above.mkdir(parents=True, exist_ok=True)
    above.chmod(0)
    try:
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    finally:
        above.chmod(0o700)
    assert lock.read_text(encoding="utf-8") == ""
    capsys.readouterr()
    stale = "a stale lock from a dead run was broken"
    for step in ("rotate_logs", "_make_client"):

        def broke(*_args: object, _step: str = step, **_kwargs: object) -> None:
            raise RuntimeError(f"{_step} broke")

        monkeypatch.setattr(cycle_mod, step, broke)
        with pytest.raises(RuntimeError, match=f"{step} broke"):
            cycle_mod.run_cycle(initialised, mode=CycleMode.POLL)
        monkeypatch.undo()
        assert lock.read_text(encoding="utf-8") == "", step
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
        captured = capsys.readouterr()
        assert stale not in captured.err + captured.out, (step, captured)


def test_a_manifest_a_sync_holds_is_no_fail(initialised: Config, capsys: pytest.CaptureFixture[str]) -> None:
    """The other side of T12: a manifest another process holds in a write transaction is no state_dir.manifest
    [FAIL]; neither is a healthy one. In WAL mode (the manifest's) a writer does not block the probe's read
    at all, so a rollback-journal copy under the same lock proves the busy half: its read raises SQLITE_BUSY
    (code 5), which is still no FAIL (B13 round 17 U70: the WAL case alone never saw a busy manifest)."""
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    db = initialised.state_paths.db
    assert doctor.manifest_unreadable(db) is None
    holder = sqlite3.connect(db, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        assert doctor.manifest_unreadable(db) is None
        assert doctor._check_manifest_readable(db) == []
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    copy = db.with_name("busy.sqlite")
    src, dst = sqlite3.connect(db), sqlite3.connect(copy)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        src.close()
        dst.close()
    holder = sqlite3.connect(copy, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        probe = sqlite3.connect(f"file:{copy}?mode=ro", uri=True, timeout=0.5)
        try:
            with pytest.raises(sqlite3.OperationalError) as busy:
                probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            probe.close()
        assert busy.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY, busy.value  # the lock is real
        assert doctor.manifest_unreadable(copy) is None
        assert doctor._check_manifest_readable(copy) == []
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if "state_dir.manifest" in ln], out
    damaged = db.with_name("damaged.sqlite")
    damaged.write_bytes(db.read_bytes()[:100] + bytes(4096 - 100))  # the header, then no schema page
    exc = doctor.manifest_unreadable(damaged)
    assert exc is not None and exc.sqlite_errorcode == 11, exc
    empty = db.with_name("empty.sqlite")
    empty.write_bytes(b"")
    assert doctor.manifest_unreadable(empty) is None
    # an intact header over a damaged schema page: the probe reads the schema, as every open does (review)
    page_one = db.with_name("page-one.sqlite")
    broken = bytearray(db.read_bytes())
    broken[100:400] = b"\xff" * 300
    page_one.write_bytes(bytes(broken))
    exc = doctor.manifest_unreadable(page_one)
    assert exc is not None and doctor.unreadable_code(exc), exc


def _drop_block(cfg: Path, sid: str, retire: bool = False) -> None:
    """Delete (or retire, with a reason) the [[source]] block of ``sid`` in sources.toml, as a hand edit."""
    text = cfg.read_text(encoding="utf-8")
    head, *blocks = text.split("[[source]]")
    kept = []
    for b in blocks:
        if f'id = "{sid}"' not in b:
            kept.append(b)
        elif retire:
            kept.append(
                b.replace(
                    f'id = "{sid}"\n', f'id = "{sid}"\nstate = "retired"\nretired_reason = "moved"\n', 1
                )
            )
    cfg.write_text("[[source]]".join([head, *kept]), encoding="utf-8")


@pytest.mark.parametrize("retire", [False, True])
def test_a_mirrored_source_whose_block_is_deleted_fails_status_as_the_persons(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str], retire: bool
) -> None:
    """B13 round 15 S7: with a mirrored source's [[source]] block deleted from sources.toml, status and doctor
    reported no [FAIL] and status's NEXT sent the agent to a sync, while every sync refused the config (exit
    78) and printed no NEXT. Doctor's config.vanished_sources check fails in the sync's own words, and its fix
    is the person's, so status's NEXT says so. A block set to retired, before the sync that retires it, is
    still configured: no [FAIL], and that sync runs and tombstones its pages."""
    second = tmp_path / "beta"
    (second / "notes").mkdir(parents=True)
    (second / "notes" / "plan.txt").write_text("The plan.\n", encoding="utf-8")
    cfg = str(initialised.config_path)
    assert cli.main(["add-source", str(second), "--config", cfg]) == cli.EXIT_OK
    [beta] = [
        s.id for s in load_config(initialised.config_path).sources if s.path and Path(s.path).name == "beta"
    ]
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    _drop_block(initialised.config_path, beta, retire=retire)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    fails = [ln for ln in out if ln.startswith("[FAIL] config.vanished_sources")]
    if retire:
        assert not fails, out
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()
        with Manifest(initialised.state_paths.db) as m:
            assert not m.present_counts().get(beta), "the retiring sync tombstoned its pages"
        return
    assert rc == cli.EXIT_FAILED and len(fails) == 1, out
    assert f"source '{beta}' (1 file) is mirrored but no longer in sources.toml" in fails[0], fails
    # B13 round 17 U9, U25, U46: its folder is still here and add-source derives the same id for it
    root = canonical_source_root(second)
    add_back = f'; or add its folder again, under the same id: {loop.BIN} add-source "{root}"'
    assert fails[0].endswith(f"(fix: {cycle_mod.VANISHED_FIX}{add_back})"), fails
    assert out[0] == f"NEXT: the config.vanished_sources check failed: {PERSONS_STEP}", out[0]
    assert cli.main(["doctor", "--config", cfg]) == cli.EXIT_FAILED
    assert any(ln.startswith("[FAIL] config.vanished_sources") for ln in capsys.readouterr().out.splitlines())
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert (
        f"source '{beta}' (1 file) is mirrored but no longer in sources.toml; retirement is explicit: " in err
    )
    assert f"put the [[source]] block back with {RETIRE_ADVICE}, or restore it" in err, err


@pytest.mark.parametrize("case", ["default-id", "custom-id", "root-gone", "renamed-id", "unreadable"])
def test_the_vanished_sources_fix_names_the_re_add_only_where_it_adds_the_same_id(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """B13 round 17 U9, U25, U46: step 1's NEXT offers to add a vanished source's folder again (one
    --source-local, which runs add-source and restores the same id), while step 2's [FAIL] fix named only
    putting the [[source]] block back. The fix now ends with the add-source command for the folder, by the
    rule install.sh's vanished_folders uses: the manifest's recorded root is a folder here and add-source
    derives the vanished id for it. A custom id (add-source would derive another, mirroring the folder twice
    and leaving the refusal) or a root that is gone keeps the block-only fix. The person's start stays.
    Review: an id renamed in sources.toml leaves its folder configured (add-source would only say so), and a
    root under a folder this process may not read must not drop the FAIL itself: both keep the plain fix."""
    gate = tmp_path / "gate"
    folder = gate / "Project Delta"
    (folder / "notes").mkdir(parents=True)
    (folder / "notes" / "plan.txt").write_text("The plan.\n", encoding="utf-8")
    cfg = str(initialised.config_path)
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_OK
    [sid] = [s.id for s in load_config(initialised.config_path).sources if s.path == folder]
    if case == "custom-id":
        text = initialised.config_path.read_text(encoding="utf-8")
        initialised.config_path.write_text(text.replace(f'id = "{sid}"', 'id = "delta-x"'), encoding="utf-8")
        sid = "delta-x"
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    if case == "renamed-id":
        text = initialised.config_path.read_text(encoding="utf-8")
        initialised.config_path.write_text(text.replace(f'id = "{sid}"', 'id = "delta-y"'), encoding="utf-8")
    else:
        _drop_block(initialised.config_path, sid, retire=False)
    if case == "root-gone":
        shutil.rmtree(folder)
    capsys.readouterr()
    if case == "unreadable":
        gate.chmod(0)
    try:
        rc, out = _status(cfg, capsys)
    finally:
        gate.chmod(0o700)
    [fail] = [ln for ln in out if ln.startswith("[FAIL] config.vanished_sources")]
    fix = fail.split(" (fix: ", 1)[1]
    assert rc == cli.EXIT_FAILED and fix.startswith("put the [[source]] block back "), fail
    assert doctor.is_persons_fix(fix)
    # B13 round 18 V7: the sync's refusal ends with the same add-back as doctor's fix, or with none
    if case != "unreadable":
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_CONFIG
        err = capsys.readouterr().err.strip()
        assert err.startswith("configuration error: ") and cycle_mod.VANISHED_ADVICE in err, err
        assert err.endswith(cycle_mod.VANISHED_ADVICE + fix.removeprefix(cycle_mod.VANISHED_FIX)[:-1]), err
    if case != "default-id":
        assert fix == f"{cycle_mod.VANISHED_FIX})" and "add-source" not in fix, fail
        return
    assert sid == "project-delta"
    cmd = f'{loop.BIN} add-source "{canonical_source_root(folder)}"'
    assert fix == f"{cycle_mod.VANISHED_FIX}; or add its folder again, under the same id: {cmd})", fail
    assert err.endswith(f"or restore it; or add its folder again, under the same id: {cmd}"), err
    # the command it names clears the refusal: the same id comes back, and the sync runs
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_OK
    assert sid in [s.id for s in load_config(initialised.config_path).sources]
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if "config.vanished_sources" in ln and ln.startswith("[FAIL]")], out
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK


def test_the_vanished_sources_fix_offers_each_folder_and_says_which_when_not_all_have_one() -> None:
    """The several-source forms of the add-back clause (B13 round 17 U46: one --source-local per folder)."""
    a, b = Path("/Users/x/Project Delta"), Path("/Users/x/Gamma")
    both = doctor.vanished_fix([a, b], every=True)
    assert both.endswith(
        f'; or add their folders again, under the same id: {loop.BIN} add-source "{a}", '
        f'then {loop.BIN} add-source "{b}"'
    ), both
    assert doctor.vanished_fix([a], every=False).startswith(
        f"{cycle_mod.VANISHED_FIX}; or add the folder still here again, under the same id: "
    )
    assert doctor.vanished_fix([], every=False) == cycle_mod.VANISHED_FIX


def test_under_install_sh_the_vanished_sources_add_back_is_one_source_local_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 18 V7, V8: under install.sh (AGENTSYNC_NO_NEXT_HINT=1) the add-back names install.sh's own
    --source-local, as step 1's NEXT does, one run for every folder, each shown as install.sh's shown_arg
    shows it, so step 1's NEXT and the [FAIL] line beside it name one command; elsewhere add-source stays."""
    a, b = Path("/Users/x/Project Delta"), Path("/Users/x/Gamma")
    monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
    assert doctor.vanished_fix([a, b], every=True) == (
        f"{cycle_mod.VANISHED_FIX}; or add their folders again, under the same id: {loop.INSTALL_SH} "
        f'--source-local "{a}" --source-local "{b}"'
    )
    monkeypatch.delenv(cli.NO_NEXT_HINT_ENV)
    assert f'{loop.BIN} add-source "{a}"' in doctor.vanished_fix([a], every=True)


@pytest.mark.parametrize(
    "folder",
    ['/Users/x/Q$1 `id` \\ "Plans"', "/Users/x/Plain Folder", "/Users/x/Hi! there", "/Users/x/T\tab"],
)
def test_the_add_backs_folder_is_shown_as_install_shs_shown_arg_shows_it(folder: str) -> None:
    """B13 round 18 V7, V8 (lane P's parity test): ``cycle._shown_arg`` is install.sh's ``shown_arg``, read
    from install.sh and run under /bin/bash: the same text, which a shell reads back as the folder."""
    text = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^shown_arg\(\) \{\n.*?^\}\n", text, re.M | re.S)
    assert m is not None
    shown = subprocess.run(
        ["/bin/bash", "-c", m.group(0) + 'shown_arg "$1"', "bash", folder],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    ).stdout
    if "!" not in folder and folder.isprintable():
        assert cycle_mod._shown_arg(folder) == shown
    for form in (shown, cycle_mod._shown_arg(folder)):
        back = subprocess.run(
            ["/bin/bash", "-c", f"printf %s {form}"], capture_output=True, text=True, check=True
        ).stdout
        assert back == folder, form


def test_the_vanished_source_check_says_nothing_before_the_first_sync(
    initialised: Config, tmp_path: Path
) -> None:
    """B13 round 15 S7: no manifest yet, no line (the manifest checks report one that cannot be read)."""
    fresh = dataclasses.replace(initialised, state_dir=tmp_path / "no-state")
    assert not fresh.state_paths.db.exists()
    assert doctor._check_vanished_sources(fresh) == []


def test_status_names_a_persons_fail_fix_as_theirs_and_sync_keeps_its_next(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 14 Q1, Q9, Q33: a synced folder renamed away made status's NEXT "do what the fix on its
    [FAIL] line below says" for a fix of "fix path in [[source]] ...", a sources.toml edit that v22 step 2 and
    install.sh's NEXT leave to the person. Its NEXT now names the fix as the person's; sync's NEXT is not
    status's (it mirrors the folders that are there and names the gone one in a WAITING ON YOU line)."""
    cfg = _synced(initialised)
    src = initialised.sources[0]
    assert src.path is not None
    root = Path(src.path).expanduser()
    root.rename(root.with_name(root.name + " renamed"))
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED
    fix = f'(fix: fix path in [[source]] id = "{src.id}"'
    assert any(fix in ln for ln in out if ln.startswith("[FAIL]"))
    assert out[0] == f"NEXT: the source.{src.id}.listable check failed: {PERSONS_STEP}", out[0]
    assert not any("do what the fix" in ln for ln in out)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    synced = capsys.readouterr().out.splitlines()
    assert not any("check failed" in ln for ln in synced if ln.startswith("NEXT: "))
    assert any(ln.startswith("WAITING ON YOU: ") and src.id in ln for ln in synced)


def test_status_puts_the_agents_own_fail_fix_before_a_persons(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 14 Q1: NEXT names only the first FAIL, so a check the agent fixes by a re-run (here one that
    ran out of time) comes before a person's fix in either order, and keeps "do what the fix ... says"."""
    cfg = _synced(initialised)
    capsys.readouterr()
    again = doctor.CheckResult(
        "pandoc", False, "timed out", doctor.Severity.ERROR, fix="run the same install.sh command again"
    )
    person = doctor.CheckResult(
        "source.x.listable", False, "gone", doctor.Severity.ERROR, fix='fix path in [[source]] id = "x"'
    )
    monkeypatch.setattr(cli, "_extra_checks", lambda config, *, offline=False: [])
    for fake in ([again], [person, again], [again, person]):

        def run(
            config: Config, *, fake: list[doctor.CheckResult] = fake, **_: object
        ) -> list[doctor.CheckResult]:
            return list(fake)

        monkeypatch.setattr(doctor, "run_checks", run)
        rc, out = _status(cfg, capsys)
        assert rc == cli.EXIT_FAILED
        assert out[0] == "NEXT: the pandoc check failed: do what the fix on its [FAIL] line below says", fake
    monkeypatch.setattr(
        doctor, "run_checks", lambda config, *, tcc_canary=True, loop_checks=None, loop_waits=None: [person]
    )
    assert _status(cfg, capsys)[1][0] == f"NEXT: the source.x.listable check failed: {PERSONS_STEP}"


@pytest.mark.parametrize(
    "case", ["rosetta", "native", "native-bad-cpu", "native-timeout", "configured", "not-absolute", "wheel"]
)
def test_status_names_the_pandoc_fix_that_is_the_persons_as_theirs(
    case: str, initialised: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 15 S53, S47: the bundled pandoc on a Mac without Rosetta failed with "Rosetta is yours to
    install, not a setup step ...", and the operator's native copy with "replace or remove <that copy> ...",
    yet status's NEXT said "do what the fix on its [FAIL] line below says": an install the setup prompt does
    not let the agent make, and a delete of a program the operator installed. Both are the person's now. A
    native copy that ran out of time keeps the agent's re-run first, and its line names the copy as theirs
    only once the same line comes back. B13 round 17 U79, U80: a configured or not-absolute pandoc_path's
    fix is a [convert] edit only, the person's; the bundled pandoc's starts with the agent's install.sh
    re-run and names the [convert] edit as the person's after it, so it stays the agent's step."""
    cfg = _synced(initialised)
    copy = Path.home() / ".local/share/agentsync/pandoc/3.9-arm64/bin/pandoc"
    copy.parent.mkdir(parents=True)
    copy.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    copy.chmod(0o755)
    kind = {"rosetta": "wheel", "wheel": "wheel", "configured": "configured", "not-absolute": "configured"}
    route = (Path("bin/pandoc") if case == "not-absolute" else copy, kind.get(case, "native"))
    monkeypatch.setattr(doctor, "_pandoc_route", lambda config: route)
    real_run = doctor._run

    def run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        if argv[0] != str(copy):
            return real_run(argv, timeout)
        if case == "native-timeout":
            raise subprocess.TimeoutExpired(list(argv), timeout)
        if case in ("native", "configured", "wheel"):
            return subprocess.CompletedProcess(list(argv), 3, "", "broken")
        raise OSError(doctor._EBADARCH, "Bad CPU type in executable")

    monkeypatch.setattr(doctor, "_run", run)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    [line] = [ln for ln in out if ln.startswith("[FAIL] pandoc")]
    assert rc == cli.EXIT_FAILED
    if case == "native-timeout":
        assert out[0] == "NEXT: the pandoc check failed: do what the fix on its [FAIL] line below says", line
        assert "if this line comes back: show the person this line: the native pandoc copy at " in line
        assert "is theirs to replace or remove" in line
    elif case == "wheel":
        assert out[0] == "NEXT: the pandoc check failed: do what the fix on its [FAIL] line below says", line
        assert f"(fix: run {doctor.INSTALL_SH} again (" in line
        assert "; if this line comes back after that, show the person this line: setting [convert] " in line
    else:
        assert out[0] == f"NEXT: the pandoc check failed: {PERSONS_STEP}", (out[0], line)
        assert not any("do what the fix" in ln for ln in out)


def test_a_refused_governance_value_leaves_the_loop_lines_archive_not_read_with_no_warning(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 18 V20: with a [governance] value that does not pass, status printed a raw timestamped
    ``WARNING agentsync.loop: loop: cannot read archive: ...`` line and the loop line's ``archive ?``. The
    [FAIL] governance.config line gives the words: the part says why it is not read, and no warning comes."""
    cfg = _synced(initialised)
    path = initialised.config_path
    path.write_text(path.read_text(encoding="utf-8") + '\n[governance]\nhistory_days = "thirty"\n', "utf-8")
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_FAILED
    got = capsys.readouterr()
    [line] = [ln for ln in got.out.splitlines() if ln.startswith("loop: ")]
    assert line.endswith(" · archive not read (the configuration is refused)"), line
    assert "WARNING agentsync.loop" not in got.err and "archive ?" not in got.out, got.err


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
        said = (
            "status: cannot read the state: the configuration is refused: the governance.config [FAIL] line"
        )
        assert any(ln.startswith(said) for ln in out)
        # B13 round 18 V10: only the lines that need [governance] go; the manifest's still print
        assert any(ln.startswith("last runs: ") for ln in out) and not any(
            ln.startswith("retention:") for ln in out
        )
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
    queued = governance.QueuedPurge(
        governance.PurgeSelector(path_glob="*"), governance.PurgeReason.OPERATOR, ""
    )
    monkeypatch.setattr(governance, "pending_purges", lambda root: [queued])
    _status(cfg, capsys)
    for argv, env in ((["doctor"], None), (["doctor"], "1"), (["status"], "1")):
        if env is None:
            monkeypatch.delenv(loop.NO_NEXT_HINT_ENV, raising=False)
        else:
            monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, env)
        cli.main([*argv, "--config", cfg])
        out = capsys.readouterr().out
        assert "exclude_label_names" not in out and "queued purges: " not in out, (argv, env)
        assert "(run " not in out, (argv, env)
        assert "[ok  ] " in out and "loop: skill " in out
    monkeypatch.delenv(loop.NO_NEXT_HINT_ENV, raising=False)
    cli.main(["policy", "show", "--config", cfg])
    out = capsys.readouterr().out
    assert "  exclude_label_names: -" in out
    assert "queued purges: 1 (preview with `~/.local/bin/agentsync purge --queue --dry-run` before" in out


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
    assert init_out[-1] == (
        "sources: no folder yet besides the inbox; run ~/.local/bin/agentsync add-source <folder>"
    )
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

    def spy(
        config: Config,
        *,
        tcc_canary: bool = True,
        loop_checks: Sequence[str] | None = None,
        loop_waits: Mapping[str, int] | None = None,
    ) -> list[doctor.CheckResult]:
        seen.append(tcc_canary)
        return real(config, tcc_canary=tcc_canary, loop_checks=loop_checks, loop_waits=loop_waits)

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

    def spy(
        config: Config,
        *,
        tcc_canary: bool = True,
        loop_checks: Sequence[str] | None = None,
        loop_waits: Mapping[str, int] | None = None,
    ) -> list[doctor.CheckResult]:
        seen.append(tcc_canary)
        return real(config, tcc_canary=False, loop_checks=loop_checks, loop_waits=loop_waits)

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


def test_add_source_takes_another_case_or_unicode_form_for_the_same_folder(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """macOS takes a name in any case, and in either Unicode form, for the same folder. add-source compared
    the bytes, so a path typed back in lower case, or with the é an agent types (one code point) where the
    disk holds an e and an accent, was appended as a second live source on the same folder, and everything
    in it was mirrored twice (review, 2026-10-07). The file system has the last word: on a case-sensitive
    volume two folders that differ only in case are two folders, and each is added."""
    cfg = str(initialised.config_path)
    on_disk, typed = (unicodedata.normalize(form, "Présentations") for form in ("NFD", "NFC"))
    folder = tmp_path / "Shared" / on_disk
    folder.mkdir(parents=True)
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    added = load_config(initialised.config_path).sources[-1]
    assert added.path == folder.resolve()
    after = initialised.config_path.read_text(encoding="utf-8")
    others = [tmp_path / "Shared" / typed, tmp_path / "shared" / on_disk.upper()]
    same = [p for p in others if p.is_dir()]  # the spellings this volume takes for that folder
    for spelling in same:
        assert cli.main(["add-source", str(spelling), "--config", cfg]) == cli.EXIT_OK, spelling
        said = f"already configured: source {added.id!r} (local, live) has path {added.path} in "
        assert said in capsys.readouterr().out, spelling
    assert initialised.config_path.read_text(encoding="utf-8") == after, "no second source on that folder"
    if sys.platform == "darwin":
        assert others[0] in same, "APFS and HFS+ take either Unicode form"
    for spelling in (p for p in others if p not in same):  # another folder on this volume: added as one
        spelling.mkdir(parents=True)
        assert cli.main(["add-source", str(spelling), "--config", cfg]) == cli.EXIT_OK, spelling
        assert "added source " in capsys.readouterr().out, spelling
        assert load_config(initialised.config_path).sources[-1].path == spelling.resolve()


def test_add_source_takes_a_cloud_folder_stored_through_a_link_as_the_same_folder(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 6 G39: a folder added through a link in ~/Library/CloudStorage that names a provider folder
    is stored with the link kept; added again by its real name, it became a second source, and every file in
    it was mirrored twice. The file system is asked when the two paths are under CloudStorage, in either
    order, and the stored path is left as it is."""
    cloud = Path.home() / "Library" / "CloudStorage"
    real = cloud / "OneDrive-Contoso" / "Project Alpha"
    real.mkdir(parents=True)
    (cloud / "OneDrive-Link").symlink_to(cloud / "OneDrive-Contoso", target_is_directory=True)
    linked = cloud / "OneDrive-Link" / "Project Alpha"
    cfg = str(initialised.config_path)
    assert cli.main(["add-source", str(linked), "--config", cfg]) == cli.EXIT_OK
    added = load_config(initialised.config_path).sources[-1]
    assert "OneDrive-Link" in str(added.path), "the stored path keeps the link"
    after = initialised.config_path.read_text(encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["add-source", str(real), "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"already configured: source {added.id!r} (local, live) has path {added.path} in " in out
    # B13 round 8 J35: the line says why a path other than the one typed is that folder, naming no one cause
    assert out.splitlines()[0].endswith(f": {real} is the same folder by another name or path"), out
    # typed as stored, the line names no other path
    assert cli.main(["add-source", str(added.path), "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("already configured: ") and "same folder" not in out.splitlines()[0], out
    assert initialised.config_path.read_text(encoding="utf-8") == after, "no second source on that folder"
    # only a provider entry's own link is read: one out of CloudStorage is not followed, nor is a link inside
    # a domain, and a folder outside CloudStorage is not the same folder
    assert not cli._same_folder(Path.home() / "Elsewhere" / "Project Alpha", real)
    (Path.home() / "Elsewhere").mkdir()
    (cloud / "OneDrive-Out").symlink_to(Path.home() / "Elsewhere", target_is_directory=True)
    assert paths.provider_link_resolved(cloud / "OneDrive-Out" / "x") == cloud / "OneDrive-Out" / "x"
    (real / "Inner").symlink_to(real, target_is_directory=True)
    assert paths.provider_link_resolved(real / "Inner" / "x") == real / "Inner" / "x"


def test_a_cloud_link_written_in_a_linked_homes_other_form_is_the_same_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 7 H34: under a home reached through a link (``/tmp`` is ``/private/tmp``), a provider link
    whose target is written in the unresolved form was read through to that form, while the stored path and
    the real folder are in the resolved one: the two never matched, so the folder was added a second time
    and mirrored twice. The result keeps the form of the root that held the path."""
    (tmp_path / "real" / "home").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias / "home"))
    cloud = alias / "home" / "Library" / "CloudStorage"
    real = cloud / "OneDrive-Contoso" / "Project Alpha"
    real.mkdir(parents=True)
    (cloud / "OneDrive-Link").symlink_to(cloud / "OneDrive-Contoso", target_is_directory=True)
    linked = cloud / "OneDrive-Link" / "Project Alpha"
    stored, direct = canonical_source_root(linked), canonical_source_root(real)
    assert paths.provider_link_resolved(stored) == direct
    assert cli._same_folder(stored, direct) and cli._same_folder(direct, stored)
    cfg = str(tmp_path / "ctx" / "sources.toml")
    assert cli.main(["add-source", str(linked), "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["add-source", str(real), "--config", cfg]) == cli.EXIT_OK
    assert "already configured: source " in capsys.readouterr().out
    assert sum(1 for s in load_config(Path(cfg)).sources if s.kind is SourceKind.LOCAL) == 1


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


def test_the_inbox_line_names_the_stored_path_beside_the_typed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A home reached through a link: the inbox is stored resolved (canonical_source_root), while the docs
    repo and config lines print the home as given. The inbox line leads with that form and names what
    sources.toml holds, which is unchanged (B13 round 4 Q36)."""
    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "home-link"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    folder = link / "Notes"
    folder.mkdir()
    cfg = link / "agent-context" / "sources.toml"
    assert cli.main(["add-source", str(folder), "--config", str(cfg)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    typed, stored = link / "agent-context" / "inbox", real.resolve() / "agent-context" / "inbox"
    assert (
        f"added inbox source 'inbox' ({typed}, stored as {stored}): drop files you save by hand here\n" in out
    ), out
    assert [s.path for s in load_config(cfg).sources if s.kind is SourceKind.INBOX] == [stored]


def test_the_added_source_line_names_the_stored_path_beside_the_typed_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 5 F49: a folder typed through a link is stored resolved; the ``added source`` line keeps the
    config path and its colon, and adds the typed and the stored forms, as the inbox line does (Q36). A folder
    typed as stored gets no note."""
    real = tmp_path / "OneDrive-Contoso"
    (real / "Project Alpha").mkdir(parents=True)
    (tmp_path / "OneDrive-Link").symlink_to(real)
    (tmp_path / "Plain").mkdir()
    cfg = tmp_path / "agent-context" / "sources.toml"
    typed = tmp_path / "OneDrive-Link" / "Project Alpha"
    stored = real.resolve() / "Project Alpha"
    assert cli.main(["add-source", str(typed), "--config", str(cfg)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"added source 'project-alpha' to {cfg} ({typed}, stored as {stored}):\n" in out, out
    plain = (tmp_path / "Plain").resolve()
    assert cli.main(["add-source", str(plain), "--config", str(cfg)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"added source 'plain' to {cfg}:\n" in out and "stored as" not in out.split("added source")[1], out


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
    assert "sources: no folder yet besides the inbox" in capsys.readouterr().out


def test_init_counts_and_names_every_inbox_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """B13 round 11 M13: with three hand-added inbox sources beside the inbox, init said "none yet besides the
    inbox". It counts and names them, and still points at add-source. B13 round 12 N13: after a colon, so the
    names' bracket and the pointer's no longer sit side by side; the pointer follows a "; "."""
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    tables = ""
    for name in ("contoso-bridge", "contoso-bridge-mail"):
        (tmp_path / name).mkdir()
        tables += inbox_source_table(name, tmp_path / name)
    cfg.write_text(cfg.read_text(encoding="utf-8") + tables, encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["init", "--config", str(cfg)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "sources: no folder yet besides 3 inbox sources: inbox, contoso-bridge, contoso-bridge-mail; "
        "run ~/.local/bin/agentsync add-source <folder>"
    )


def test_init_under_the_installer_names_no_second_command_to_add_a_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 13 O17: under install.sh (AGENTSYNC_NO_NEXT_HINT=1, which it sets for every call) step 2's
    sources line still said "run ~/.local/bin/agentsync add-source <folder>", beside a NEXT line naming the
    installer's own re-run with --source-local, and the setup prompt says not to use add-source. The line
    ends at what is synced there; a bare init keeps the pointer (the tests above)."""
    monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, "1")
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "sources: no folder yet besides the inbox"
    cfg.write_text(cfg.read_text(encoding="utf-8").split("[[source]]")[0], encoding="utf-8")
    config = load_config(cfg)
    assert not config.sources
    monkeypatch.setattr(cli, "_ensure_inbox", lambda c: c)  # an inbox that could not be added
    assert cli._ensure_setup(config) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "sources: none yet" and "add-source" not in out, out


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
    # bring-back S15: the scaffold count carries its verb, so the second call reads as a no-op
    first, second = [ln for ln in out.splitlines() if ln.startswith("docs repo ")]
    assert re.search(r"\(created, no remote; [1-9]\d* scaffold files written\)$", first), first
    assert second.endswith("(exists, no remote; scaffold up to date)"), second
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
        r"^    2 online-only files left for a later run \(over this run's --materialise-budget of 0 "
        r"bytes\)$",
        out,
        re.MULTILINE,
    )
    lines = out.rstrip("\n").splitlines()
    assert lines[-3:-1] == [
        lines[-3],
        "NEXT: draft the baseline questions: follow step 1 (Draft) of the agentsync-docs skill's \"Baseline "
        'questions" section (also in ~/agent-context/docs/AGENTS.md), then run `~/.local/bin/agentsync sync`',
    ], "the online-only deferrals do not hold the loop at 'sync again'"
    m = re.fullmatch(r"converted (\d+), deferred 2 online-only", lines[-3])
    assert m and int(m.group(1)) >= 8, lines[-3]
    assert lines[-1] == (
        "note: 2 online-only files in source wait for a later sync's download budget; they do not block "
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


# ---- recordings: the status clause, the materialise line, the lines that name commands -------------------


def _local_id(config: Config) -> str:
    return next(s.id for s in config.sources if s.kind is SourceKind.LOCAL)


def _mark(config: Config, rel_path: str, state: RowState, reason: str | None) -> str:
    """Set the row at ``rel_path`` (the local source) to an unconverted ``state`` with ``reason``, as the
    cycle leaves it; its stable id."""
    sid = _local_id(config)
    with Manifest(config.state_paths.db) as manifest:
        row = manifest.item_by_path(sid, rel_path)
        assert row is not None, rel_path
        manifest.set_verdict(sid, row.stable_id, Verdict.DEFERRED)
        manifest.set_state(sid, row.stable_id, state, reason)
        return row.stable_id


def test_a_label_rule_converts_recordings_adds_the_index_note_and_the_status_clause(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ruling 2 (spec S0 rule 1), the status half: while a [policy] label rule is active and [convert]
    recordings is on, the line stating the label rule gains the fixed clause; with no rule, or recordings
    off, it does not. (The index NOTE half is the recording converter's.)"""
    cfg = str(initialised.config_path)
    clause = "; recordings are converted on this Mac under it (a recording's label cannot be read)"
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "  labels_active: false\n" in out and clause not in out
    with initialised.config_path.open("a", encoding="utf-8") as fh:
        fh.write('\n[policy]\nexclude_label_names = ["Contoso Secret"]\n')
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"  labels_active: true{clause}\n" in out and out.count(clause) == 1
    with initialised.config_path.open("a", encoding="utf-8") as fh:
        fh.write("\n[convert]\nrecordings = false\n")
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "  labels_active: true\n" in out and clause not in out


def test_materialise_names_an_online_only_recording_that_could_not_be_downloaded(
    initialised: Config,
    local_source_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec S0 rule 3: an online-only recording whose download failed in the run gets one line sending the
    person to Finder.  A refusal mark an earlier run left (this run skipped the download, say for low disk)
    gets none, and neither does a refused document."""
    cfg = str(initialised.config_path)
    folder = local_source_dir / "projects" / "meetings"
    folder.mkdir()
    for name in ("standup.mp4", "review.mov"):
        (folder / name).write_bytes(b"\x00\x00\x00\x18ftypmp42 not a real recording")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    real = cli.run_cycle

    def run_cycle(config: Config, **kwargs: Any) -> Any:  # the cycle's part: this run refused standup.mp4
        report = real(config, **kwargs)
        for rel in ("projects/meetings/standup.mp4", "projects/meetings/review.mov", "projects/sample.docx"):
            _mark(config, rel, RowState.DATALESS, HYDRATION_REFUSED)
        alarm = cycle_mod._RECORDING_NOT_DOWNLOADED.format(path="projects/meetings/standup.mp4")
        first, *rest = report.sources
        first = dataclasses.replace(first, alarms=(*first.alarms, alarm))
        return dataclasses.replace(report, sources=(first, *rest))

    monkeypatch.setattr(cli, "run_cycle", run_cycle)
    capsys.readouterr()
    paths = [folder / "standup.mp4", folder / "review.mov", local_source_dir / "projects" / "sample.docx"]
    assert cli.main(["materialise", "--config", cfg, *map(str, paths), str(folder)]) == cli.EXIT_OK
    tail = "an online-only recording could not be downloaded; in Finder choose Always Keep on This Device"
    assert [line for line in capsys.readouterr().out.splitlines() if tail in line] == [
        f"    alarm: projects/meetings/standup.mp4: {tail}"
    ]


def test_status_prints_the_recording_lines_and_their_commands_parse(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``status`` prints the recording wait (no background job is installed in a test) and the Finder note
    beside the other loop lines; every ``agentsync`` command they name is one the CLI parses."""
    cfg = str(initialised.config_path)
    for name in ("standup.mp4", "review.mov"):
        (local_source_dir / "projects" / name).write_bytes(b"\x00\x00\x00\x18ftypmp42 not a real recording")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    waiting = _mark(initialised, "projects/standup.mp4", RowState.LIVE, RECORDING_WAITS)
    _mark(initialised, "projects/review.mov", RowState.DATALESS, HYDRATION_REFUSED)
    key = f"{loop.RECORDING_PROGRESS_META}{_local_id(initialised)}:{waiting}"
    with Manifest(initialised.state_paths.db) as manifest:
        manifest.set_meta(key, "300000 1800000")
    capsys.readouterr()
    cli.main(["status", "--config", cfg])
    out = capsys.readouterr().out.splitlines()
    sid = _local_id(initialised)
    (wait,) = [line for line in out if line.startswith(f"WAITING ON YOU: 1 recording in {sid} ")]
    assert "(5 of 30 minutes)" in wait and "--confirm-install-agent" in wait
    assert any(line.startswith(f"note: 1 online-only recording in {sid} (0.0 GB)") for line in out)
    named = [
        span[span.index("agentsync ") :] for span in re.findall(r"`([^`]+)`", wait) if "agentsync " in span
    ]
    assert named == ["agentsync materialise <file>"]
    for command in named:
        _assert_fix_parses(command)


def test_status_counts_refused_files_apart_from_quarantined_ones(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field report 2026-10-07 (U7): status's ``quarantined 11`` summed 0 quarantined and 11 refused rows,
    while the sync line and the report's Quarantine part split them. Status prints each state's own count."""
    projects = local_source_dir / "projects"
    (projects / "drawing.xyzq").write_bytes(b"\x00\x01")  # no converter: refused
    (projects / "model.dwg").write_bytes(b"\x00\x02")  # no converter: refused
    (projects / "locked.pdf").write_bytes(
        b"%PDF-1.7\n1 0 obj<</Encrypt 2 0 R>>endobj\ntrailer<</Encrypt 2 0 R>>\n%%EOF\n"
    )  # encrypted: quarantined
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--once", "--config", cfg]) == cli.EXIT_OK
    with Manifest(initialised.state_paths.db) as manifest:
        by_state, _ = manifest.state_counts("source")
    quarantined, refused = by_state[RowState.QUARANTINED], by_state[RowState.REFUSED]
    assert quarantined >= 1 and refused >= 2
    capsys.readouterr()
    assert cli.main(["status", "--config", cfg]) == cli.EXIT_OK
    (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("  source (local, live): ")]
    assert f" · quarantined {quarantined} · refused {refused} · deferred " in line, line


def test_no_one_word_condition_is_wrapped_only_for_its_trailing_comment() -> None:
    """B13 round 9 K57: J69's ``if red.private_only:`` read ``if (`` / ``red.private_only`` / ``):  # ...``,
    since ruff's formatter wraps a condition whose trailing comment runs past the line length. Such a comment
    goes on its own line above the ``if``; a condition that is multi-line on its own keeps its parentheses."""
    wrapped = re.compile(r"^[ \t]*(?:el)?if \(\n[ \t]+[\w.]+\n[ \t]*\):[ \t]*#", re.MULTILINE)
    src = Path(__file__).parents[1] / "src" / "agentsync"
    hits = [
        f"{p.relative_to(src)}: {m[0]!r}"
        for p in sorted(src.rglob("*.py"))
        for m in wrapped.finditer(p.read_text("utf-8"))
    ]
    assert hits == []


def test_an_audit_trail_on_a_read_only_volume_or_odd_suppressions_keep_the_other_governance_lines(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 23 A79's reviewer: the audit check caught only PermissionError, so EROFS on a read-only
    volume escaped and the whole governance group became one line, the legal hold lines with it; and a
    suppression list of valid JSON but the wrong shape did the same. Each is its own line now."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    owner = ["--owner", "Contoso Legal", "--config", cfg]
    assert cli.main(["hold", "source", "--reason", "matter 7", *owner]) == cli.EXIT_OK
    governance._suppress_file(root).write_text("[]\n", encoding="utf-8")
    audit = str(governance.audit_path(root))
    real_open = os.open

    def readonly(path: Any, flags: int, *args: Any) -> int:
        if os.fspath(path) == audit and flags & os.O_WRONLY:
            raise OSError(errno.EROFS, "Read-only file system", audit)
        return real_open(path, flags, *args)

    monkeypatch.setattr(cli.os, "open", readonly)
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert any(" governance.hold " in ln and "matter 7" in ln for ln in out), out
    assert any(ln.startswith("[FAIL] governance.audit ") for ln in out), out
    assert any(ln.startswith("[FAIL] governance.suppressed ") for ln in out), out
    assert not any(ln.startswith("[FAIL] governance ") for ln in out), out


def test_a_folder_where_the_manifest_belongs_is_not_judged_for_time_machine(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 23 A10: with a folder at manifest.sqlite, governance.time_machine warned "not excluded" with
    an ``agentsync sync`` fix the sync cannot reach (it stops on the state file first), and the report filed
    it as unexpected. The manifest is not judged there now: an [info] line points at state_dir.manifest, which
    FAILs it, and every other path not excluded still warns."""
    db = expand(initialised.state_paths.db)
    mirror = expand(initialised.docs_repo) / "mirror"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    db.mkdir(mode=0o700)
    monkeypatch.setattr(cli.governance, "time_machine_status", lambda config: [(db, False), (mirror, False)])
    out = cli._time_machine_checks(initialised)
    infos = [r for r in out if r.severity is doctor.Severity.INFO]
    warns = [r for r in out if r.severity is doctor.Severity.WARN]
    assert [r.detail for r in infos] == [
        "not checked: the state file is not a file; the state_dir.manifest line says why"
    ], out
    assert len(warns) == 1 and str(mirror) in warns[0].detail and str(db) not in warns[0].detail, warns
    assert any(r.name == "state_dir.manifest" and not r.ok for r in doctor._check_state_dir(initialised))


@pytest.mark.parametrize(
    "fix",
    [
        doctor.MANIFEST_FIX,
        "the person gives this account read and write access to the file ~/'state/manifest.sqlite'; its "
        "owner and mode are right, so an ACL or a privacy setting refuses it",
        "the person gives this account search access to ~/Library, a folder above the state folder",
    ],
)
def test_statuss_next_for_a_persons_fail_names_no_catalog_of_kinds(fix: str) -> None:
    """B13 round 23 A31: status's NEXT for a person's FAIL was a sentence of about 540 characters that listed
    all of doctor.PERSONS_KINDS, not the one kind its [FAIL] line names (round 24 B68). The line below states
    the fix; the NEXT says whose it is, and keeps "not a step for the agent", which the setup report counts a
    person's stop by."""
    step = cli._fail_step(doctor.CheckResult("state_dir", False, "x", doctor.Severity.ERROR, fix=fix))
    assert doctor.is_persons_fix(fix)
    assert step == (
        "the state_dir check failed: its fix on the [FAIL] line below is the person's, not a step for the "
        "agent: show it to them"
    )
    assert len(step) < 200 and setup_report._PERSONS_STEP in step


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a folder of any mode")
def test_a_state_folder_at_0500_names_the_folders_fix_and_a_next_not_sqlites_bare_line(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 23 A59: with the state folder at 0500 and every file in it 0600, a sync ended on sqlite's
    bare ``error: OperationalError: attempt to write a readonly database``, no fix and no NEXT, while status
    named ``chmod 700``: Z80's line named only a refused file. The folder's own line now, with status's fix
    for it and the state NEXT; the LaunchAgent's run (--mode) prints no NEXT."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    for side in ("-wal", "-shm"):
        root.joinpath(initialised.state_paths.db.name + side).unlink(missing_ok=True)
    capsys.readouterr()
    root.chmod(0o500)
    try:
        rc = cli.main(["sync", "--config", cfg])
        said = capsys.readouterr()
        polled = cli.main(["sync", "--mode", "poll", "--config", cfg])
        background = capsys.readouterr()
        status_rc, out = _status(cfg, capsys)
    finally:
        root.chmod(0o700)
    (state,) = [ln for ln in out if ln.startswith("[FAIL] state_dir ")]
    fix = f"chmod 700 {doctor._shell_path(root)}"
    assert state.endswith(f"(fix: {fix})"), state
    assert rc == cli.EXIT_FAILED and "OperationalError" not in said.err, said.err
    (line,) = [ln for ln in said.err.splitlines() if ln.startswith("error: ")]
    assert line == cli._folder_unwritable_line(root, fix), (
        line
    )  # B13 round 24 B102: the report's row reads it
    assert line == f"error: state: the state folder {root} cannot be written by this account (fix: {fix})"
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.STATE_REFUSED_STEP}"
    ]
    assert polled == cli.EXIT_FAILED and "NEXT: " not in background.out, background
    assert status_rc == cli.EXIT_FAILED


def test_the_folder_refuses_clause_reads_the_folder_a_fix_names_up_to_its_semicolon(tmp_path: Path) -> None:
    """B13 round 23 A54: lane B's Z72 fix for an ACL on the state folder itself (``read-attributes, read,
    write and search access to the folder <f>; ...``) matched no prefix, so a line naming the manifest got no
    ``: its folder refuses this account``; and the ACL branch looked for the folder followed by a space, while
    doctor writes ``<f>;``. Both prefixes now, the folder read up to ``;`` and compared with the line's path
    as written and as resolved."""
    root = tmp_path / "state"
    root.mkdir()
    db = root / "manifest.sqlite"
    for fix in (
        doctor._search_fix(Path(os.path.realpath(root))),
        doctor._parent_fix(Path(os.path.realpath(root))),
    ):
        assert fix.startswith("the person gives this account "), fix
        assert cli._folder_refuses(db, fix) == ": its folder refuses this account", fix
        assert cli._folder_refuses(root, fix) == "", fix  # the line names the folder itself


@pytest.mark.skipif(sys.platform != "darwin" or os.geteuid() == 0, reason="macOS ACLs; root ignores them")
def test_an_acl_that_denies_the_state_folder_readattr_says_whether_the_line_names_the_folder(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 23 A54, measured: with ``deny readattr`` on the state folder, curate's line names the
    manifest and the fix names the folder, so it says the folder refuses; sync's line names the folder itself,
    so it adds nothing."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    user = os.environ.get("USER") or os.getlogin()
    capsys.readouterr()
    subprocess.run(["/bin/chmod", "+a", f"user:{user} deny readattr", str(root)], check=True)
    try:
        lines = {}
        for command in ("curate", "sync"):
            assert cli.main([command, "--config", cfg]) == cli.EXIT_FAILED, command
            err = capsys.readouterr().err
            (lines[command],) = [ln for ln in err.splitlines() if ln.startswith("error: state: ")]
    finally:
        subprocess.run(["/bin/chmod", "-N", str(root)], check=True)
    head = "(fix: the person gives this account read-attributes, read, write and search access to the folder"
    assert ": its folder refuses this account (fix: " in lines["curate"] and head in lines["curate"], lines
    assert ": its folder refuses this account" not in lines["sync"] and head in lines["sync"], lines


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_heartbeat_this_account_cannot_open_is_said_once_and_an_interactive_sync_exits_1(
    initialised: Config, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 23 A1, A34, A84 (round 23 ruling 5): with heartbeat.json at 0000 a sync exited 0 with four
    raw WARNING lines (two reads, one write per source) and the loop's NEXT, so the setup loop never met the
    agent's chmod that status's [FAIL] heartbeat line names. An interactive sync says it once, as an ``error:
    state:`` line with status's fix, its NEXT leads to status (the sync ran: not STATE_REFUSED_STEP's "did not
    run"), it exits 1 and records the stop beside the setup log (A9); a --mode run and install.sh's keep their
    exit and say one WARNING; a chmod 600 gives the loop back; a corrupt heartbeat keeps today's warning."""
    cfg = _synced(initialised)
    hb = expand(initialised.state_paths.heartbeat)
    setup = setup_report.default_setup_log().parent
    setup.mkdir(parents=True, exist_ok=True)
    fix = f"(fix: chmod 600 {doctor._shell_path(hb)})"
    capsys.readouterr()
    hb.chmod(0)
    try:
        rc = cli.main(["sync", "--config", cfg])
        said = capsys.readouterr()
        status_rc, out = _status(cfg, capsys)
        polled = cli.main(["sync", "--mode", "poll", "--config", cfg])
        background = capsys.readouterr()
        monkeypatch.setenv(cli.NO_NEXT_HINT_ENV, "1")
        installed = cli.main(["sync", "--config", cfg])
        under_install = capsys.readouterr()
        monkeypatch.delenv(cli.NO_NEXT_HINT_ENV)
    finally:
        hb.chmod(0o600)
    assert rc == cli.EXIT_FAILED, said
    (line,) = [ln for ln in said.err.splitlines() if ln.strip()]  # no WARNING lines beside it
    assert line == f"error: state: {hb} cannot be opened: Permission denied {fix}", line
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.HEARTBEAT_REFUSED_STEP}"
    ]
    assert "did not run" not in cli.HEARTBEAT_REFUSED_STEP
    (failed,) = [ln for ln in out if ln.startswith("[FAIL] heartbeat ")]
    assert status_rc == cli.EXIT_FAILED and failed.endswith(fix), failed
    (stop,) = (setup / cli.SYNC_STOPS_LOG).read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ sync-stop file=heartbeat rc=1 owner=agent kind=refused", stop
    ), stop
    for run_rc, got in ((polled, background), (installed, under_install)):
        assert run_rc == cli.EXIT_OK and "NEXT: " not in got.out, got
        warned = [ln for ln in got.err.splitlines() if "heartbeat" in ln]
        assert len(warned) == 1 and warned[0].endswith(fix) and " WARNING " in warned[0], got.err
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    again = capsys.readouterr()
    assert "error: " not in again.err and cli.HEARTBEAT_REFUSED_STEP not in again.out, again
    assert any(ln.startswith("NEXT: ") for ln in again.out.splitlines())
    hb.write_text("{not json", encoding="utf-8")  # corrupt: doctor's WARN, which the next pass rewrites
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert cli.HEARTBEAT_REFUSED_STEP not in capsys.readouterr().out


def test_add_source_over_a_folder_at_the_manifest_says_it_stopped_before_it_finished(
    initialised: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 26 E42: D44's NEXT said "so this command did not run" after add-source, which had already
    added the source before it stopped on a folder at the manifest. The NEXT says the command stopped before
    it finished, true of add-source and init as of curate and hold."""
    cfg = str(initialised.config_path)
    db = expand(initialised.state_paths.db)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    db.mkdir()
    folder = tmp_path / "contoso-more"
    folder.mkdir()
    (folder / "notes.txt").write_text("Contoso notes\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["add-source", str(folder), "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr()
    assert "contoso-more" in load_config(Path(cfg)).config_path.read_text(encoding="utf-8"), (
        "the source is added"
    )
    assert [ln for ln in out.out.splitlines() if ln.startswith("NEXT")] == [
        f"NEXT: {cli.STATE_MISPLACED_COMMAND_STEP}"
    ], out
    assert "did not run" not in cli.STATE_MISPLACED_COMMAND_STEP
    assert "stopped before it finished" in cli.STATE_MISPLACED_COMMAND_STEP


@pytest.mark.parametrize("where", ["the state folder", "a folder above it"])
def test_curate_and_the_governance_commands_over_a_state_folder_that_is_a_file_say_syncs_words(
    initialised: Config, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """B13 round 24 B88, B89: with a file where the state folder (or a folder above it) belongs, ``curate``
    exited 0 with a "cannot read the loop state ... fix or remove it by hand" WARNING and "(see NEXT)" with no
    NEXT, and ``hold --list`` named a holds.json that does not exist. Each says sync's line, state_dir's words
    and the person's move, with sync's NEXT, and exits 1; an ENOTDIR under a healthy state folder keeps its
    own words."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    args = cli.build_parser().parse_args(["hold", "--list", "--config", cfg])
    elsewhere = governance.GovernanceError("x: unreadable")
    elsewhere.__cause__ = NotADirectoryError(errno.ENOTDIR, "Not a directory", str(root.parent / "y" / "z"))
    assert cli._non_folder_said(args, elsewhere) is None  # the control: the state folder is a folder
    file = root if where == "the state folder" else root.parent
    file.rename(file.with_name(file.name + ".kept"))
    file.write_text("not a folder\n", encoding="utf-8")
    for argv in (["curate"], ["hold", "--list"], ["accept-deletions", _local_id(initialised)]):
        capsys.readouterr()
        rc = cli.main([*argv, "--config", cfg])
        said = capsys.readouterr()
        text = said.out + said.err
        assert rc == cli.EXIT_FAILED, (argv, text)
        (line,) = [ln for ln in said.err.splitlines() if ln.strip()]
        assert line.startswith("error: state: ") and "is a file, not a folder" in line, line
        assert "(fix: the person moves " in line, line
        assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
            f"NEXT: {cli.STATE_MISPLACED_COMMAND_STEP}"  # no sync ran: not "this sync" (B13 round 25 D44)
        ], (argv, said.out)
        assert "this sync" not in cli.STATE_MISPLACED_COMMAND_STEP
        assert "fix or remove it by hand" not in text and "(see NEXT)" not in text, (argv, text)
        assert "holds.json" not in text and "Errno" not in text, (argv, text)


@pytest.mark.parametrize("body", ["{not json", "[]"])
def test_status_over_a_corrupt_heartbeat_keeps_every_line_and_names_no_python_class(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path, body: str
) -> None:
    """B13 round 24 B88, B90: over a heartbeat.json that is not valid JSON (or not an object) status printed
    "status: cannot read the state: ValueError: ..." in place of its config, docs repo, lock, last runs and
    per-source lines: its own second read had no guard. The [warn] heartbeat line says why; every line stays,
    and a row's last success is unknown, never "never", over a file that cannot be parsed."""
    cfg = _synced(initialised)
    expand(initialised.state_paths.heartbeat).write_text(body, encoding="utf-8")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_OK, out
    assert not any("ValueError" in ln or "cannot read the state" in ln for ln in out), out
    assert any(ln.startswith("lock: ") for ln in out) and any(ln.startswith("last runs: ") for ln in out), out
    rows = [ln for ln in out for s in initialised.sources if ln.startswith(f"  {s.id} (")]
    assert len(rows) == len(initialised.sources), out
    assert all("last success unknown (heartbeat.json is not valid" in ln for ln in rows), rows
    report = tmp_path / "r.md"
    assert cli.main(["setup-report", "--out", str(report), "--config", cfg]) == cli.EXIT_OK
    written = report.read_text(encoding="utf-8")
    assert "ValueError" not in written and "last success unknown (heartbeat.json is not valid" in written


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_heartbeat_this_account_cannot_open_is_said_when_no_source_writes_one(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 24 B71: with every source paused no heartbeat write is tried, so a heartbeat.json at 0000
    went unsaid: the sync exited 0 with an empty stderr (its read was logged at DEBUG, on the word that the
    CLI says it), while status FAILed it. The cycle's read is the test then: an interactive sync says it once
    and exits 1, as when a write is refused; a --mode run exits 0 with one WARNING ending in the fix."""
    cfg = _synced(initialised)
    path = initialised.config_path
    text = path.read_text(encoding="utf-8")
    for kind in ('kind = "local"', 'kind = "inbox"'):
        text = text.replace(kind, f'{kind}\nstate = "paused"')
    path.write_text(text, encoding="utf-8")
    hb = expand(initialised.state_paths.heartbeat)
    fix = f"(fix: chmod 600 {doctor._shell_path(hb)})"
    capsys.readouterr()
    hb.chmod(0)
    try:
        rc = cli.main(["sync", "--config", cfg])
        said = capsys.readouterr()
        polled = cli.main(["sync", "--mode", "poll", "--config", cfg])
        background = capsys.readouterr()
    finally:
        hb.chmod(0o600)
    assert rc == cli.EXIT_FAILED, said
    (line,) = [ln for ln in said.err.splitlines() if ln.strip()]
    assert line == f"error: state: {hb} cannot be opened: Permission denied {fix}", line
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.HEARTBEAT_REFUSED_STEP}"
    ]
    assert polled == cli.EXIT_OK and "NEXT: " not in background.out, background
    warned = [ln for ln in background.err.splitlines() if "heartbeat" in ln]
    assert len(warned) == 1 and warned[0].endswith(fix) and " WARNING " in warned[0], background.err


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_foreground_sync_that_stops_on_a_state_file_records_its_role_beside_the_setup_log(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 23 A9 (cli's half; lane RA reads it): a step-3 sync that stopped on a state file the agent's
    chmod then fixed left no trace, so the report said "fully one command" with no Items. A foreground sync
    appends ``<UTC> sync-stop file=<role> rc=1 owner=<agent|person>`` beside install.log, no path, a line the
    install.log reader never takes for an install run; only when the setup folder is there, and never for a
    LaunchAgent's run."""
    cfg = _synced(initialised)
    lock = expand(initialised.state_paths.lock)
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    lock.chmod(0)
    try:
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED  # no setup folder: nothing written
        assert not stops.parent.exists()
        stops.parent.mkdir(parents=True)
        assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_FAILED
        assert not stops.exists()
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    finally:
        lock.chmod(0o600)
    capsys.readouterr()
    (line,) = stops.read_text(encoding="utf-8").splitlines()
    assert line.endswith(" sync-stop file=lock rc=1 owner=agent kind=refused") and str(lock) not in line, line
    assert setup_report._INSTALL_LINE_RE.match(line) is None
    assert stat.S_IMODE(stops.stat().st_mode) == 0o600
    # B13 round 24 B66: the writer's line through the report's own reader, so a renamed file, a timestamp the
    # reader does not parse or a role or owner word it does not take turns this red, not the report silent
    assert cli.SYNC_STOPS_LOG == setup_report._SYNC_STOPS_NAME
    since = datetime(2000, 1, 1, tzinfo=UTC)
    (read,) = setup_report._read_sync_stops(stops.parent / "install.log", since, None)
    assert (read.file, read.owner, read.kind, read.ended_ok) == ("lock", "agent", "refused", None)


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a mode-600 folder")
def test_a_sync_stop_its_folder_refused_is_recorded_on_the_folder_not_the_file(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 24 B16 (lane RA's reader, cli's writer): a sync refused by its state folder, not by a file in
    it, wrote the file's role (``file=manifest`` or ``file=lock``) though its line says the folder refuses and
    its fix is the folder's chmod 700. The line names the folder."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)
    capsys.readouterr()
    root.chmod(0o600)
    try:
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    finally:
        root.chmod(0o700)
    said = capsys.readouterr()
    assert ": its folder refuses this account (fix: chmod 700 " in said.err, said.err
    (line,) = stops.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r"\S+ sync-stop file=state-folder rc=1 owner=agent kind=refused", line), line


def test_a_sync_stop_on_a_state_folder_that_is_a_file_is_recorded_as_misplaced_not_refused(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 24 B82: a sync stopped by a file where the state folder belongs wrote ``file=state-folder
    rc=1 owner=person``, which the report read as "which this account could not open": nothing was refused.
    The line carries its kind, ``misplaced``, which the report's reader takes (its words for it: lane RA)."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)
    root.rename(root.with_name(root.name + ".kept"))
    root.write_text("not a folder\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    said = capsys.readouterr()
    assert "is a file, not a folder" in said.err and f"NEXT: {cli.STATE_MISPLACED_STEP}" in said.out, said
    (line,) = stops.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r"\S+ sync-stop file=state-folder rc=1 owner=person kind=misplaced", line), line
    since = datetime(2000, 1, 1, tzinfo=UTC)
    (read,) = setup_report._read_sync_stops(stops.parent / "install.log", since, None)
    assert (read.file, read.owner, read.kind) == ("state-folder", "person", "misplaced")


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a folder of any mode")
@pytest.mark.parametrize(
    # a fifo at the -shm: sqlite's IOERR_LOCK; at the -wal a sync with nothing to write ends 0 (the pipe takes
    # its write), while status FAILs it: no stop to word there
    ("side", "mode"),
    [("-wal", 0o700), ("-shm", 0o700), ("-shm", 0o000), ("-shm", None)],
)
def test_a_side_file_that_is_a_folder_stops_every_command_on_statuss_line_and_the_persons_next(
    initialised: Config, capsys: pytest.CaptureFixture[str], side: str, mode: int | None
) -> None:
    """B13 round 25 D2, D8, D21, D70 (ruling 12): a folder at the manifest's -wal or -shm made sync, poll,
    curate and init print "error: OperationalError: unable to open database file" (or "attempt to write a
    readonly database"), exit 1, with no fix, no NEXT and no sync-stops.log line, while status FAILed it with
    the person's move aside. Each prints status's own state_dir.files line as ``error: state: <detail> (fix:
    <fix>)`` and exits 1; a foreground sync gives the misplaced NEXT and records ``file=manifest-<side>
    owner=person kind=misplaced``; poll prints the line alone. Status reads the cause in not-a-file words."""
    cfg = _synced(initialised)
    db = expand(initialised.state_paths.db)
    there = db.with_name(db.name + side)
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)
    there.unlink(missing_ok=True)
    if mode is None:  # a fifo: sqlite's IOERR, not CANTOPEN (lane B's reviewer)
        os.mkfifo(there)
    else:
        there.mkdir(mode=mode)
    (want,) = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.files" and not r.ok]
    said = f"error: state: {want.detail} (fix: {want.fix})"
    role = doctor.SIDE_ROLES[side]
    kind = "a fifo" if mode is None else "a folder"
    assert want.detail == f"{there} is {kind}, not a file: {role} belongs there", want
    assert want.fix is not None and want.fix.endswith(
        f"moves {doctor._shell_path(there)} aside, keeping a copy"
    )
    assert ("chmod 700" in want.fix) == (mode == 0), want.fix  # a shut folder is opened first (D72)
    capsys.readouterr()
    try:
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        out = capsys.readouterr()
        assert out.err.splitlines() == [said], out.err
        assert [x for x in out.out.splitlines() if x.startswith("NEXT")] == [
            f"NEXT: {cli.STATE_MISPLACED_STEP}"
        ]
        (line,) = stops.read_text(encoding="utf-8").splitlines()
        assert re.fullmatch(rf"\S+ sync-stop file=manifest{side} rc=1 owner=person kind=misplaced", line), (
            line
        )
        assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_FAILED
        out = capsys.readouterr()
        assert out.err.splitlines() == [said] and "NEXT" not in out.out, out
        assert len(stops.read_text(encoding="utf-8").splitlines()) == 1
        for command in ("init", "curate"):
            # a 0700 folder at the -shm let both read past it and exit 0 with no words, curate sending the
            # agent to a sync that stops on it: both judge the sides before any open (B13 round 26 E22, E65)
            rc = cli.main([command, "--config", cfg])
            out = capsys.readouterr()
            assert rc == cli.EXIT_FAILED and said in out.err.splitlines(), (command, out)
            assert "0 findings" not in out.out and "sync` again" not in out.out, (command, out)
            assert f"NEXT: {cli.STATE_MISPLACED_COMMAND_STEP}" in out.out.splitlines(), (command, out)
            assert "OperationalError" not in out.err + out.out, (command, out)
        assert cli.main(["status", "--config", cfg]) == cli.EXIT_FAILED
        status = capsys.readouterr().out
        if (side, mode) != ("-shm", 0o700):  # status's read-only open reads past a 0700 folder there
            assert f"cannot read the state: {role} is not a file; the state_dir.files line says why" in status
        assert "this account cannot open the state file" not in status, status
        assert "the state file cannot be opened" not in status, status
    finally:
        if mode is not None:
            there.chmod(0o700)


def _manifest_gone(config: Config, *, heartbeat: bool) -> Path:
    """The manifest and its sides removed after a sync, heartbeat.json kept or moved aside; the db path."""
    sp = config.state_paths
    db = expand(sp.db)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    if not heartbeat:
        expand(sp.heartbeat).rename(expand(sp.heartbeat).with_name("heartbeat.kept.json"))
    return db


@pytest.mark.parametrize(
    ("link", "heartbeat"),
    [
        ("into a missing folder", False),
        ("to itself", False),
        ("to itself", True),
        ("through a file", False),
        ("through a file", True),
        ("into a usable folder", False),
    ],
)
def test_a_manifest_link_a_sync_stops_on_stops_curate_and_never_reads_as_a_new_start(
    initialised: Config, capsys: pytest.CaptureFixture[str], link: str, heartbeat: bool
) -> None:
    """B13 round 26 E40, E41, E45: with the manifest a link to nothing in a missing folder (no heartbeat), a
    link loop (with or without one) or a link through a regular file, curate said "0 findings", exit 0, and
    sent the agent to a sync that stops on it; status said "the next sync starts a new one over N pages"
    beside the FAIL that sync stops on; and the link through a file got a false ACL line and a sync line with
    no fix. Each stops curate and the sync on status's own state_dir.manifest line, with a NEXT, and status's
    manifest line says the next sync stops on it. A link into a folder this account can use is a first
    start: curate runs, and status says the next sync starts a new manifest."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    db = _manifest_gone(initialised, heartbeat=heartbeat)
    if link == "into a missing folder":
        db.symlink_to(root / "gone" / "manifest.sqlite")
    elif link == "to itself":
        db.symlink_to(db)
    elif link == "through a file":
        (root / "afile").write_text("", encoding="utf-8")
        db.symlink_to(root / "afile" / "manifest.sqlite")
    else:
        (root / "usable").mkdir(mode=0o700)
        db.symlink_to(root / "usable" / "manifest.sqlite")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    fails = [ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")]
    assert not [ln for ln in out if "by an ACL" in ln], out  # never the link's own mode (E45)
    curated = cli.main(["curate", "--config", cfg])
    said = capsys.readouterr()
    if link == "into a usable folder":
        assert not fails and curated == cli.EXIT_OK, (out, said)
        assert any(ln.startswith("manifest: none yet (the next sync starts a new one over ") for ln in out), (
            out
        )
        return
    (failed,) = fails
    line = "error: state: " + failed.split(" — ", 1)[1]
    lost = heartbeat and link == "through a file"  # manifest_lost reads ENOTDIR as a missing file (E45)
    manifest = (
        "manifest: missing (earlier syncs recorded; the person decides: see state_dir.manifest)"
        if lost
        else "manifest: none (the next sync stops on it; the person decides: see state_dir.manifest)"
    )
    assert rc == cli.EXIT_FAILED and manifest in out, out
    assert not [ln for ln in out if ln.startswith("manifest: none yet")], out
    assert curated == cli.EXIT_FAILED and said.err.splitlines() == [line], said
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT")] == [
        f"NEXT: {cli.STATE_MISPLACED_COMMAND_STEP}"
    ] and "0 findings" not in said.out, said.out
    if link == "to itself":
        assert "is a symlink loop, not the state file" in line, line
    else:
        assert "whose folder does not exist or refuses a write" in line, line
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    said = capsys.readouterr()
    assert said.err.splitlines() == [line], said.err
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT")] == [
        f"NEXT: {cli.STATE_MISPLACED_STEP}"
    ]


@pytest.mark.parametrize("side", ["-wal", "-shm"])
@pytest.mark.parametrize(
    "link", ["to nothing in a usable folder", "to a file", "to nothing in a shut folder"]
)
def test_a_side_file_link_is_no_file_sqlite_opens_and_its_fix_leads_to_a_clean_sync(
    initialised: Config, capsys: pytest.CaptureFixture[str], side: str, link: str
) -> None:
    """B13 round 26 E62, E46, E75: sqlite opens a -wal or -shm only where it is, never through a link, so a
    link there to nothing in a writable folder, or to a regular file, stopped every sync on "unable to open
    database file" while status passed it (or FAILed a link's own 0755 with a chmod 600 that follows the link
    and never clears). Any link at a side is status's state_dir.files FAIL in not-a-file words with the
    person's move aside, the words its unread line and listing note already use (E46); sync stops on that
    line; doing what it says ends in a clean sync. Beside a manifest that is itself a link, a side link is
    harmless (sqlite keeps its sides by the target): no FAIL."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    db = expand(initialised.state_paths.db)
    there = db.with_name(db.name + side)
    there.unlink(missing_ok=True)
    old = os.umask(0o022)
    try:
        if link == "to a file":
            (root / "elsewhere").write_text("", encoding="utf-8")
            there.symlink_to(root / "elsewhere")
        elif link == "to nothing in a usable folder":
            there.symlink_to(root / "x")
        else:
            (root / "shut").mkdir(mode=0o500)
            there.symlink_to(root / "shut" / "x")
    finally:
        os.umask(old)
    kind = "a symlink to a file" if link == "to a file" else "a symlink to nothing"
    words = f"{there} is {kind}, not a file: {doctor.SIDE_ROLES[side]} belongs there"
    fix = f"the person moves {doctor._shell_path(there)} aside, keeping a copy"
    try:
        assert doctor.side_not_a_file_at(db) == there
        assert setup_report._dangling_state(initialised) == [], "a side link is the side's bucket (E75)"
        capsys.readouterr()
        rc, out = _status(cfg, capsys)
        (failed,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.files ")]
        assert rc == cli.EXIT_FAILED and failed.endswith(f" — {words} (fix: {fix})"), out
        assert not [ln for ln in out if "readable by others" in ln or "by an ACL" in ln], out
        assert not [ln for ln in out if "points the link" in ln], "one fix, the move aside (E46)"
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
        said = capsys.readouterr()
        assert said.err.splitlines() == [f"error: state: {words} (fix: {fix})"], said.err
    finally:
        if (root / "shut").exists():
            (root / "shut").chmod(0o700)
    there.rename(there.with_name(there.name + ".kept"))  # the person's fix, followed
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()


def test_a_side_link_beside_a_manifest_link_is_harmless_and_a_linked_manifest_reads_its_targets_mode(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 26 E62 (a side link beside a manifest link: sqlite keeps the sides by the target, so no FAIL)
    and E63: a manifest linked to a 0600 file FAILed "readable by others: manifest.sqlite (0755)", the link's
    own mode under umask 022, with a chmod 600 that follows the link and never clears. It is judged by its
    target: 0600 passes; a 0644 target FAILs, and the chmod named clears it."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    db = expand(initialised.state_paths.db)
    real = root / "real.sqlite"
    for name in ("", "-wal", "-shm"):
        here = db.with_name(db.name + name)
        if here.exists():
            here.rename(real.with_name(real.name + name))
    old = os.umask(0o022)
    try:
        db.symlink_to(real)
        db.with_name(db.name + "-wal").symlink_to(root / "nowhere")  # stale, beside a manifest link
    finally:
        os.umask(old)
    assert stat.S_IMODE(db.lstat().st_mode) == 0o755 and stat.S_IMODE(real.stat().st_mode) == 0o600
    files = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.files"]
    assert [r.ok for r in files] == [True] and "0600" in files[0].detail, files
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()
    real.chmod(0o644)
    (loose,) = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.files"]
    assert not loose.ok and "(0644)" in loose.detail and loose.fix == f"chmod 600 {doctor._shell_path(db)}"
    db.chmod(0o600)  # what the fix runs: chmod follows the link
    # status's read-only probe of the 0644 target made its -shm 0644 too (X78), by the target, where sqlite
    # keeps it: judged there since B13 round 27 P42, and its own chmod clears it
    sides = doctor.manifest_sides(db)
    named = {doctor._shell_path(x) for x in sides}
    for r in doctor._check_state_dir(initialised):
        if r.name == "state_dir.files" and not r.ok:
            assert r.fix is not None and r.fix.startswith("chmod 600 ~/"), r
            assert all(f"{x}" in r.fix for x in named if x.split("/")[-1].rstrip("'") in r.detail), r
            for x in sides:
                if x.exists():
                    x.chmod(0o600)
    assert [r.ok for r in doctor._check_state_dir(initialised) if r.name == "state_dir.files"] == [True]


def test_a_dangling_manifest_link_followed_by_its_fix_ends_in_a_clean_sync(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 25 D71: a link to nothing at manifest.sqlite, into a folder that does not exist, over earlier
    passes: with heartbeat.json alone moved aside the sync said only "error: <db> cannot be opened: No such
    file or directory", no fix and no NEXT. It gives status's state_dir.manifest line, the misplaced NEXT and
    a stop record; the fix followed (remove the link, move heartbeat.json aside) ends in a sync that exits
    0."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    db, beat = expand(sp.db), expand(sp.heartbeat)
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    db.symlink_to(db.parent / "gone" / "manifest.sqlite")
    (fail,) = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.manifest" and not r.ok]
    assert fail.fix is not None and fail.fix.startswith(
        f"the person removes the link first: rm {doctor._shell_path(db)}"
    )
    beat.rename(beat.with_name("heartbeat.kept.json"))  # the lost guard's move alone: the link still stops it
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr()
    (err,) = out.err.splitlines()
    assert (
        err.startswith(f"error: state: {db} is a symlink to ") and "(fix: the person points the link" in err
    ), err
    assert f"NEXT: {cli.STATE_MISPLACED_STEP}" in out.out.splitlines(), out
    (line,) = stops.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r"\S+ sync-stop file=manifest rc=1 owner=person kind=misplaced", line), line
    db.unlink()  # and the link removed, as the fix says
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()


@pytest.mark.parametrize("where", ["manifest", "-wal"])
def test_an_incomplete_listing_beside_a_state_file_that_is_not_a_file_says_so(
    initialised: Config, where: str
) -> None:
    """B13 round 25 D23 (lane RA's item, this file) and D8: beside a folder where the manifest belongs the
    incomplete-listing warn said "the state file cannot be read", and beside a folder at the -wal "this
    account cannot open the state file", while every other line says it is not a file. Each gets its own
    note, the person's, with the line that says what to do."""
    cfg = _synced(initialised)
    assert cfg
    db = expand(initialised.state_paths.db)
    for name in ("-wal", "-shm") if where == "-wal" else ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    db.with_name(db.name + ("" if where == "manifest" else where)).mkdir(mode=0o700)
    warn = doctor.CheckResult(
        name="heartbeat.contoso",
        ok=False,
        detail=doctor.LISTING_INCOMPLETE + "3 passes",
        severity=doctor.Severity.WARN,
        fix="sync again",
    )
    exc = sqlite3.OperationalError("unable to open database file")
    exc.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN  # type: ignore[attr-defined]
    (out,) = cli._listings_unread([warn], exc, db.parent)
    side = db.with_name(db.name + "-wal")  # named by its role since B13 round 27 P16, P146
    note = (
        doctor.LISTING_STATE_NOT_FILE_NOTE if where == "manifest" else doctor.listing_state_side_note([side])
    )
    assert out.note == note and out.fix is None, out
    assert "cannot be read" not in note and "cannot open" not in note


@pytest.mark.parametrize("link", ["into a missing folder", "a loop"])
def test_a_manifest_link_status_fails_stops_the_sync_on_its_line_and_status_names_it_first(
    initialised: Config, capsys: pytest.CaptureFixture[str], link: str
) -> None:
    """B13 round 25 D43: status FAILed a manifest link into a missing folder (B64) or a loop there, while the
    sync printed only "error: <db> cannot be opened: No such file or directory" (or "Too many levels of
    symbolic links"), no fix, no NEXT and no stop record; and status's own NEXT was the loop's "run sync",
    which led back to that stop. The sync gives status's line, the misplaced NEXT and a stop record; status
    leads with the FAIL's fix."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    db = expand(sp.db)
    expand(sp.heartbeat).rename(expand(sp.heartbeat).with_name("heartbeat.kept.json"))
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    db.symlink_to(db if link == "a loop" else db.parent / "gone" / "manifest.sqlite")
    (fail,) = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.manifest" and not r.ok]
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr()
    assert out.err.splitlines() == [f"error: state: {fail.detail} (fix: {fail.fix})"], out.err
    assert [ln for ln in out.out.splitlines() if ln.startswith("NEXT")] == [
        f"NEXT: {cli.STATE_MISPLACED_STEP}"
    ]
    (line,) = stops.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r"\S+ sync-stop file=manifest rc=1 owner=person kind=misplaced", line), line
    rc, lines = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED and lines[0].startswith("NEXT: the state_dir.manifest check failed"), lines
    report = setup_report.default_setup_log().parent / "r.md"
    assert cli.main(["setup-report", "--out", str(report), "--config", cfg]) == cli.EXIT_OK
    (loop_line,) = [ln for ln in report.read_text(encoding="utf-8").splitlines() if ln.startswith("- Loop: ")]
    assert "NEXT: the state_dir.manifest check failed" in loop_line, (
        loop_line
    )  # status's NEXT, the report's too
    capsys.readouterr()
    agent = doctor.CheckResult(
        name="skill", ok=False, detail="x", severity=doctor.Severity.ERROR, fix="chmod 700 y"
    )
    (manifest,) = [
        r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.manifest" and not r.ok
    ]
    assert cli._manifest_first([agent, manifest]) == cli._fail_step(
        manifest
    )  # never the agent's fix sorted first
    exc = ManifestPathError(f"{db} cannot be opened", errno.ELOOP if link == "a loop" else errno.ENOENT)
    exc.filename = str(db)
    words = cli._unread_words(exc, config=initialised)  # a link to nothing is no "not a file"
    if link == "a loop":
        assert words == "the state file is not a file; the state_dir.manifest line says why", words
    else:
        assert (
            words
            == "the state file's link points into a missing folder; the state_dir.manifest line says why"
        )


def test_a_new_manifest_names_each_page_it_removed_and_did_not_write_back(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 25 D40: B37's quiet reset logged each orphan page at DEBUG, assuming the run wrote every one
    back; a page whose source file had gone was deleted and committed with no line above DEBUG. Each page
    not written back is a WARNING that names it; the note and the rebuild of the rest stay as B37 made
    them."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    mirror = initialised.layout.mirror
    before = {
        p.relative_to(initialised.docs_repo).as_posix() for p in mirror.rglob("*.md") if p.name != "CLAUDE.md"
    }
    gone = sorted(p for p in local_source_dir.rglob("*") if p.is_file() and not p.name.startswith("."))[0]
    gone.unlink()
    db = expand(sp.db)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    expand(sp.heartbeat).rename(expand(sp.heartbeat).with_name("heartbeat.kept.json"))
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    rebuilt = capsys.readouterr()
    after = {
        p.relative_to(initialised.docs_repo).as_posix() for p in mirror.rglob("*.md") if p.name != "CLAUDE.md"
    }
    removed = sorted(before - after)
    assert removed, (before, after)
    warned = [ln for ln in rebuilt.err.splitlines() if " WARNING " in ln]
    assert len(warned) == len(removed), rebuilt.err
    for rel in removed:
        assert any(
            f"removed orphan mirror page {rel} (not written back by this run)" in ln for ln in warned
        ), warned


@pytest.mark.parametrize("lose_one", [True, False], ids=["one-file-gone", "every-file-back"])
def test_a_new_start_counts_each_page_it_removed_and_continues_the_run_numbers(
    initialised: Config, local_source_dir: Path, capsys: pytest.CaptureFixture[str], lose_one: bool
) -> None:
    """B13 round 26 E9, E10 (ruling 14): the person's new start (manifest gone, heartbeat.json aside) removed
    the page of a file that had gone, and its commit said "0d", its CHANGELOG nothing and the note "every
    file is converted again"; and the new manifest numbered its run 1 again, so append_changelog found the
    old "run 1" heading of the same day and skipped the section, and the commit's Agentsync-Run trailer
    repeated the old one. The new start continues the run numbers, its commit and CHANGELOG count each page
    removed as a deletion, and the note says how many came back; with every file back it says 0d, as
    before."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    sp = initialised.state_paths
    month = initialised.layout.changelog_dir / f"{datetime.now(UTC):%Y-%m}.md"
    heading = re.compile(r"^## \d{4}-\d{2}-\d{2} · run (\d+) · ", re.MULTILINE)
    (old_run,) = [int(n) for n in heading.findall(month.read_text(encoding="utf-8"))]
    old_trailer = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%(trailers:key=Agentsync-Run,valueonly)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    pages = sorted(
        p.relative_to(repo).as_posix()
        for p in initialised.layout.mirror.rglob("*.md")
        if p.name != "CLAUDE.md"
    )
    files = sorted(p for p in local_source_dir.rglob("*") if p.is_file() and not p.name.startswith("."))
    if lose_one:
        files[0].unlink()
    (local_source_dir / "contoso-notes.txt").write_text("Contoso planning notes\n", encoding="utf-8")
    db = expand(sp.db)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    expand(sp.heartbeat).rename(expand(sp.heartbeat).with_name("heartbeat.kept.json"))
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    out = capsys.readouterr().out
    after = {
        p.relative_to(repo).as_posix()
        for p in initialised.layout.mirror.rglob("*.md")
        if p.name != "CLAUDE.md"
    }
    removed = sorted(set(pages) - after)
    assert len(removed) == (1 if lose_one else 0), (pages, after)
    (note,) = [ln for ln in out.splitlines() if ln.startswith("note: a new manifest was started over ")]
    over = plural(len(pages), "existing mirror page")
    if lose_one:
        assert note == (
            f"note: a new manifest was started over {over}; {len(pages) - 1} written back, 1 removed "
            "(its file is gone or was not converted)"
        ), note
    else:
        assert note == f"note: a new manifest was started over {over}; every file is converted again", note
    show = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show",
            "--stat",
            "--format=%s%n%(trailers:key=Agentsync-Run,valueonly)",
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    subject, trailer = show.splitlines()[:2]
    assert f" {len(removed)}d " in f"{subject} ", subject
    assert trailer.strip() and trailer.strip() != old_trailer, (trailer, old_trailer)
    assert f"CHANGELOG/{month.name}" in show, show
    text = month.read_text(encoding="utf-8")
    runs = [int(n) for n in heading.findall(text)]
    assert len(runs) == 2 and runs[0] == old_run and runs[1] > old_run, runs
    assert f"run {runs[1]} · mode " in out and int(trailer) == runs[1], out
    section = text[text.rindex("\n## ") :]
    for rel in removed:
        assert f"- D `{rel}`" in section, section


def test_a_new_start_the_background_job_ran_is_said_by_the_next_foreground_sync_once(
    initialised: Config,
    local_source_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 26 E74: when the LaunchAgent's poll was the first run after the person's new start, the
    new-start note and the "not written back" WARNINGs went to the poll log alone. The job keeps them in the
    manifest; status counts them and leaves them; the next foreground sync says them once (a page a later
    poll wrote back is not named) and clears them; a second sync says nothing."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    gone = sorted(p for p in local_source_dir.rglob("*") if p.is_file() and not p.name.startswith("."))[0]
    gone.unlink()
    db = _manifest_gone(initialised, heartbeat=False)
    monkeypatch.setenv("XPC_SERVICE_NAME", f"{initialised.launchd_label_prefix}.poll")
    capsys.readouterr()
    assert cli.main(["sync", "--mode", "poll", "--config", cfg]) == cli.EXIT_OK
    capsys.readouterr()
    monkeypatch.delenv("XPC_SERVICE_NAME")
    with Manifest(db) as m:
        saved = json.loads(m.get_meta(cycle_mod.NEW_START_UNSAID_META) or "{}")
        (missing,) = saved["orphans"]
        back = next(
            p.relative_to(repo).as_posix()
            for p in initialised.layout.mirror.rglob("*.md")
            if p.name != "CLAUDE.md"
        )
        saved["orphans"] = sorted([missing, back])  # one a later poll wrote back
        m.set_meta(cycle_mod.NEW_START_UNSAID_META, json.dumps(saved))
    assert not (repo / missing).exists()
    _rc, out = _status(cfg, capsys)
    assert (
        "new start: the background sync started a new manifest and removed 1 page it did not write back; "
        "the next sync names them" in out
    ), out
    with Manifest(db) as m:
        assert m.get_meta(cycle_mod.NEW_START_UNSAID_META), "status leaves the key"
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    said = capsys.readouterr()
    over = plural(saved["pages"], "existing mirror page")
    # one bracket, the job in the sentence (P47)
    back = saved["pages"] - 1
    want = (
        f"note: a new manifest was started over {over} by the background sync; {back} written back, 1 removed"
    )
    assert f"{want} (its file is gone or was not converted)" in said.out.splitlines(), said.out
    warned = [ln for ln in said.err.splitlines() if "removed orphan mirror page" in ln]
    assert len(warned) == 1 and missing in warned[0] and "by the background sync" in warned[0], said.err
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    again = capsys.readouterr()
    assert "a new manifest was started" not in again.out and "removed orphan" not in again.err, again
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if ln.startswith("new start: ")], out


def test_curate_over_a_lost_manifest_says_syncs_line_and_never_to_sync_again(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 25 D77: with the manifest lost after earlier syncs, curate exited 0 with "0 findings" and
    "NEXT: 2 sources not fully listed or converted yet ...: run sync again", whose sync stops on the lost
    manifest. Curate gives sync's line with the person's fix, one NEXT, exit 1, and starts no manifest."""
    cfg = _synced(initialised)
    db = expand(initialised.state_paths.db)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    capsys.readouterr()
    assert cli.main(["curate", "--config", cfg]) == cli.EXIT_FAILED
    out = capsys.readouterr()
    assert out.err.splitlines() == [
        f"error: state: {db} {LOST_MANIFEST} (fix: {doctor.LOST_MANIFEST_FIX})"
    ], out
    assert [ln for ln in out.out.splitlines() if ln.startswith("NEXT")] == [
        f"NEXT: {cli.STATE_MISPLACED_COMMAND_STEP}"
    ], out.out
    assert "sync` again" not in out.out and not db.exists(), out.out


def test_an_incomplete_listing_beside_a_lost_manifest_says_the_state_file_is_missing(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 24 B36 (ruling 7): beside a manifest lost from a Mac that has synced, the incomplete-listing
    warn said "the state file cannot be read, so no sync lists it again until it can", a damaged manifest's
    note: the file is missing, and what lists it again is the person's restore or new start. Its own note,
    with no parentheses inside the warn's one bracket; a damaged manifest keeps "cannot be read"."""
    cfg = _synced(initialised)
    db = expand(initialised.state_paths.db)
    beats = expand(initialised.state_paths.heartbeat)
    data = json.loads(beats.read_text(encoding="utf-8"))
    sid = _local_id(initialised)
    data[sid]["consecutive_incomplete"] = 5
    beats.write_text(json.dumps(data), encoding="utf-8")
    for side in ("", "-wal", "-shm"):
        db.with_name(db.name + side).unlink(missing_ok=True)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED, out
    (warn,) = [ln for ln in out if ln.startswith(f"[warn] heartbeat.{sid} ")]
    assert warn.endswith(f"({doctor.LISTING_STATE_LOST_NOTE})"), warn
    assert "the state file is missing" in warn and "cannot be read" not in warn, warn
    assert "(" not in doctor.LISTING_STATE_LOST_NOTE and ")" not in doctor.LISTING_STATE_LOST_NOTE
    assert "cannot be read" in doctor.LISTING_STATE_UNREADABLE_NOTE


def test_a_manifest_lost_from_a_mac_that_has_synced_is_the_persons_and_no_sync_starts_over(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 23 A29: with manifest.sqlite gone and heartbeat.json recording passes, status said "no
    manifest yet" and "no sync has run" and exited 0, and the sync it pointed to quietly started over: every
    page removed as an orphan and converted again, the empty-folder alarm and the deletion hold forgotten.
    Status FAILs state_dir.manifest, the person's (restore it, or move heartbeat.json aside to confirm a new
    start); no command starts a new manifest over that history; once heartbeat.json is aside the next sync
    does. A Mac with no heartbeat (a new one, or a docs repo restored onto it) still reads "no manifest yet".
    """
    from agentsync import manifest as manifest_mod  # noqa: PLC0415

    cfg = _synced(initialised)
    db = expand(initialised.state_paths.db)
    hb = expand(initialised.state_paths.heartbeat)
    for side in ("", "-wal", "-shm"):
        db.with_name(db.name + side).unlink(missing_ok=True)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert rc == cli.EXIT_FAILED, out
    (fail,) = [ln for ln in out if ln.startswith("[FAIL] ")]
    assert fail.startswith("[FAIL] state_dir.manifest ") and f"{db} {manifest_mod.LOST_MANIFEST}" in fail, (
        fail
    )
    assert fail.endswith(f"(fix: {doctor.LOST_MANIFEST_FIX})") and doctor.is_persons_fix(
        doctor.LOST_MANIFEST_FIX
    )
    text = "\n".join(out)
    assert "no sync has run" not in text and "no manifest yet" not in text, text
    assert "manifest: missing (earlier syncs recorded; the person decides: see state_dir.manifest)" in out
    (step,) = [ln for ln in out if ln.startswith("NEXT: ")]
    assert "not a step for the agent" in step, step
    report = initialised.config_path.parent / "report.md"
    assert cli.main(["setup-report", "--out", str(report), "--config", cfg]) == cli.EXIT_OK
    written = report.read_text(encoding="utf-8")
    assert "no sync has run" not in written and "- manifest: missing (earlier syncs recorded" in written
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    said = capsys.readouterr()
    (line,) = [ln for ln in said.err.splitlines() if ln.strip()]  # no "repair: removing orphan" WARNINGs
    assert line == f"error: state: {db} {manifest_mod.LOST_MANIFEST} (fix: {doctor.LOST_MANIFEST_FIX})", line
    assert [ln for ln in said.out.splitlines() if ln.startswith("NEXT: ")] == [
        f"NEXT: {cli.STATE_MISPLACED_STEP}"
    ]
    assert not db.exists()
    # B13 round 24 B27, B62, B82, B100 (ruling 7): its stop is the manifest's, of its own kind, not
    # file=state-folder read as a refusal; recorded once the setup folder is there
    stops = setup_report.default_setup_log().parent / cli.SYNC_STOPS_LOG
    stops.parent.mkdir(parents=True, exist_ok=True)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    capsys.readouterr()
    (stop,) = stops.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r"\S+ sync-stop file=manifest rc=1 owner=person kind=lost", stop), stop
    since = datetime(2000, 1, 1, tzinfo=UTC)
    (read,) = setup_report._read_sync_stops(stops.parent / "install.log", since, None)
    assert (read.file, read.owner, read.kind) == ("manifest", "person", "lost")
    assert read.what == "agentsync's state file"
    hb.rename(hb.with_name("heartbeat.json.kept"))  # the person's confirmation of a new start
    rc, out = _status(cfg, capsys)
    pages = sorted(p for p in initialised.layout.mirror.rglob("*.md") if p.name != "CLAUDE.md")
    assert pages
    # B13 round 25 D26: not "no sync has run" over the pages earlier syncs wrote; the same count as the
    # sync's note below, and doctor's ok line says the next sync starts one over them
    over = plural(len(pages), "existing mirror page")
    assert (
        f"manifest: none yet (the next sync starts a new one over {over}; every file is converted again)"
        in out
    )
    assert "manifest: none yet (no sync has run)" not in out and not any(
        "state_dir.manifest" in ln for ln in out
    )
    assert any(ln.endswith(f"no manifest yet (the next sync starts one, 0600, over {over})") for ln in out), (
        out
    )
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK and db.exists()
    # B13 round 24 B37: the rebuild said one WARNING per page it removed as an orphan and wrote back, and no
    # plain line; it says one note of its own, before the summary line, and no WARNING
    rebuilt = capsys.readouterr()
    assert " WARNING agentsync." not in rebuilt.err, rebuilt.err
    noted = [ln for ln in rebuilt.out.splitlines() if ln.startswith("note: a new manifest")]
    assert noted == [
        f"note: a new manifest was started over {plural(len(pages), 'existing mirror page')}; every file is "
        "converted again"
    ], rebuilt.out
    lines = rebuilt.out.splitlines()
    assert lines[lines.index(noted[0]) + 1].startswith("run "), rebuilt.out
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "note: a new manifest" not in capsys.readouterr().out


def test_cli_imports_graph_auth_once_as_a_module_never_lazily() -> None:
    """B13 round 24 B67 (A91's class, which doctor fixed): cli imports agentsync.graph.auth, and so msal, at
    its top, yet kept two function-level imports of it whose stated reasons ("msal only when used", "imports
    pymsalruntime only when Graph is configured") were false. One module import serves both, so a test's patch
    of ``graph_auth.MsalAuth`` still takes effect; no name of it is bound at import time but TokenProvider."""
    import ast  # noqa: PLC0415

    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    lazy = [
        node.lineno
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.ImportFrom)
        and (
            node.module == "agentsync.graph.auth"
            or (node.module == "agentsync.graph" and node.names[0].name == "auth")
        )
    ]
    assert lazy == [], lazy
    assert cli.graph_auth is graph_auth


# ---- B13 round 27 ruling 17: the new start's queued purges, a lost manifest beside an unread heartbeat ----


def _new_start(config: Config, tmp: Path) -> None:
    """The person's new start: the manifest, its sides and heartbeat.json moved aside, keeping a copy."""
    sp = config.state_paths
    db = expand(sp.db)
    aside = tmp / "aside"
    aside.mkdir(exist_ok=True)
    for side in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm"), expand(sp.heartbeat)):
        if os.path.lexists(side):
            side.rename(aside / side.name)


def _queue_upstream(config: Config, rel: str | None, stable_id: str | None = None) -> governance.QueuedPurge:
    """Queue an upstream deletion as a cycle does, a few minutes ago (before a new start the test makes)."""
    import datetime as dt  # noqa: PLC0415

    sid = stable_id or _stable_id(config, rel or "")
    selector = governance.PurgeSelector(stable_id=sid, source_id="source")
    ago = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    reason = governance.PurgeReason.UPSTREAM_DELETED
    assert governance.enqueue_purge(config.state_paths.root, selector, reason, now=ago)
    (q,) = [q for q in governance.pending_purges(config.state_paths.root) if q.selector == selector]
    return q


def test_after_the_new_start_a_queued_purge_waits_for_the_next_sync_to_judge_it(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """B13 round 27 P40 (ruling 17): after the person's new start (heartbeat.json aside), an upstream
    deletion queued for a file still listed read runnable, status invited `purge --queue`, and the run erased
    the page and its history. It is "not judged yet" until the next sync builds a manifest: no wait, no
    invitation, the run leaves it and the repo as they are; then it stays queued, its file listed."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    page = "mirror/source/projects/sample.txt.md"
    _queue_upstream(initialised, "projects/sample.txt")
    ((_q, why),) = governance.partition_purges(initialised.state_paths)[1]
    assert why.startswith("stays queued: the manifest lists the file as live"), why
    _new_start(initialised, tmp_path)
    runnable, kept = governance.partition_purges(initialised.state_paths)
    assert not runnable and [w for _q, w in kept] == [governance.NEW_START_NOTE], kept
    step = loop.next_step(initialised)
    assert not [w for w in step.waits if "purge" in w], step.waits
    assert governance.new_start_purges(1) in step.notes, step.notes
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if "purge --queue --dry-run" in ln], out
    qfile = governance._queue_file(expand(initialised.state_paths.root))
    before = (
        qfile.read_bytes(),
        git(repo, "rev-parse", "HEAD^{tree}"),
        git(repo, "log", "--all", "--", page),
    )
    assert cli.main(["purge", "--queue", "--config", cfg]) == cli.EXIT_OK
    assert "1 still queued until the next sync builds the manifest" in capsys.readouterr().out
    assert (
        qfile.read_bytes(),
        git(repo, "rev-parse", "HEAD^{tree}"),
        git(repo, "log", "--all", "--", page),
    ) == before
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    ((_q, why),) = governance.partition_purges(initialised.state_paths)[1]
    assert why.startswith("stays queued: the manifest lists the file as live"), why


def test_a_link_to_nothing_at_the_manifest_after_the_new_start_claims_no_fail(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """B13 round 27 P46: over a link to nothing in a folder the next sync creates the file through, with
    heartbeat.json aside, the queued-purge words named a state_dir.manifest FAIL status never printed and said
    `purge --queue` refuses, which ran. It is the new start's: the next sync judges it."""
    cfg = _synced(initialised)
    _queue_upstream(initialised, "projects/sample.txt")
    _new_start(initialised, tmp_path)
    db = expand(initialised.state_paths.db)
    db.symlink_to(db.with_name("gone.sqlite"))
    _runnable, kept = governance.partition_purges(initialised.state_paths)
    assert [w for _q, w in kept] == [governance.NEW_START_NOTE], kept
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if "state_dir.manifest [FAIL]" in ln], out


def test_a_purge_queued_under_a_replaced_manifest_that_names_nothing_is_kept_never_verified(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """B13 round 27 P112 (ruling 17): a purge queued before the new start, for an id the new manifest holds
    nothing of (a re-keyed item whose alias only the lost manifest had), targeted 0 items, read VERIFIED and
    was dequeued while the page's history stayed; status and the loop invited it. It is "not resolvable":
    kept, no invitation, never VERIFIED, and its note names the by-hand erase."""
    cfg = _synced(initialised)
    q = _queue_upstream(initialised, None, stable_id="contoso-volume:retired-0042")
    _new_start(initialised, tmp_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    runnable, kept = governance.partition_purges(initialised.state_paths)
    assert not runnable and [w for _q, w in kept] == [governance.unresolved_note(q)], kept
    assert "purge docs=<page path>" in kept[0][1] and "mirror/source/" in kept[0][1]
    step = loop.next_step(initialised)
    assert not [w for w in step.waits if "purge" in w] and governance.unresolved_purges(1) in step.notes
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if "purge --queue --dry-run" in ln], out
    for argv in (["purge", "--queue", "--dry-run"], ["purge", "--queue"]):
        capsys.readouterr()
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_OK, argv
        said = capsys.readouterr().out
        assert "VERIFIED" not in said and governance.pending_purges(initialised.state_paths.root) == [q], said


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
@pytest.mark.parametrize("dlp", [False, True], ids=["kept only", "with a DLP entry"])
def test_over_a_manifest_this_account_cannot_open_purge_refuses_and_names_the_fail_that_is_there(
    initialised: Config, capsys: pytest.CaptureFixture[str], dlp: bool
) -> None:
    """B13 round 27 P24, P46: over a 0000 manifest the queued-purge words named a state_dir.manifest FAIL
    status never printed (its FAIL is state_dir.files) and said `purge --queue` refuses, while it exited 0
    with the entry skipped, and stopped only when a DLP entry was queued too. They name state_dir.files, and
    the queue, its dry run and a selector each stop on the state file's error, whatever the queue holds."""
    cfg = _synced(initialised)
    repo = initialised.docs_repo
    _queue_upstream(initialised, "projects/sample.txt")
    root = expand(initialised.state_paths.root)
    if dlp:
        sel = governance.PurgeSelector.parse("docs=mirror/source/README.txt.md", source_id=None)
        assert governance.enqueue_purge(root, sel, governance.PurgeReason.DLP_REMEDIATION)
    db = expand(initialised.state_paths.db)
    qfile = governance._queue_file(root)
    before = (qfile.read_bytes(), git(repo, "rev-parse", "HEAD^{tree}"))
    db.chmod(0o000)
    try:
        capsys.readouterr()
        _rc, out = _status(cfg, capsys)
        assert any(ln.startswith("[FAIL] state_dir.files ") for ln in out), out
        assert not [ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")], out
        said = [ln for ln in out if "queued purge waits on agentsync's state file" in ln]
        assert said and all("the state_dir.files [FAIL] line says what to do first" in ln for ln in said), out
        assert not [ln for ln in out if "state_dir.manifest" in ln], out
        for argv in (["purge", "--queue"], ["purge", "--queue", "--dry-run"], ["purge", "docs=mirror/x.md"]):
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
            err = capsys.readouterr().err.splitlines()
            assert len([ln for ln in err if ln.startswith("error: state: ")]) == 1, (argv, err)
    finally:
        db.chmod(0o600)
    assert (qfile.read_bytes(), git(repo, "rev-parse", "HEAD^{tree}")) == before


@pytest.mark.parametrize("n", [1, 2])
def test_the_queued_purge_words_count_with_agreement_and_status_says_the_sentence_at_most_twice(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path, n: int
) -> None:
    """B13 round 27 P118: over a lost manifest status printed "1 queued purge(s) wait on ..." three times
    (the loop's note, the governance.purge_queue line, the queued-purges-kept line). The count agrees, no
    "(s)", and the full sentence is said at most twice: the kept line gives the count and points at it."""
    cfg = _synced(initialised)
    _queue_upstream(initialised, "projects/sample.txt")
    if n == 2:
        _queue_upstream(initialised, "projects/acme/Kickoff Notes.docx")
    db = expand(initialised.state_paths.db)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    words = "1 queued purge waits on agentsync's state file" if n == 1 else "2 queued purges wait on"
    assert any(words in ln for ln in out), out
    assert not [ln for ln in out if "(s)" in ln], out
    assert sum(1 for ln in out if "queued purge" in ln and "wait" in ln and "state file" in ln) <= 2, out
    assert f"queued purges kept: {n} (the governance.purge_queue line says why)" in out, out
    assert governance.kept_purges(1).startswith("1 upstream deletion stays queued while its file")
    assert governance.kept_escalations(2).startswith("2 label-escalation purges stay queued")


@pytest.mark.parametrize("what", ["damaged JSON", "a fifo", "a folder"])
def test_a_lost_manifest_beside_a_heartbeat_that_cannot_be_read_is_still_lost(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path, what: str
) -> None:
    """B13 round 27 P111 (ruling 17): with manifest.sqlite gone and heartbeat.json damaged or a fifo, the
    heartbeat read as no record, so the sync started a new manifest over the mirror with no word to the
    person. It is still lost, in its own words and the lost manifest's fix; moving heartbeat.json aside, as
    the fix says, starts the new manifest."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    db, hb = expand(sp.db), expand(sp.heartbeat)
    for name in ("", "-wal", "-shm"):
        db.with_name(db.name + name).unlink(missing_ok=True)
    if what == "damaged JSON":
        hb.write_bytes(hb.read_bytes()[:20] + b"\x00garbage")
    else:
        hb.unlink()
        _make_not_a_file(hb, what)
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    (failed,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.manifest ")]
    assert rc == cli.EXIT_FAILED and f"{db} {manifest_mod.LOST_MANIFEST_UNREAD}" in failed, failed
    assert doctor.LOST_MANIFEST_FIX in failed, failed
    assert not [ln for ln in out if "the next sync starts a new one" in ln], out
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    err = capsys.readouterr().err
    if what != "a folder":  # a folder stops the sync on its own move aside first, the person's too
        assert manifest_mod.LOST_MANIFEST_UNREAD in err, err
    assert not db.exists(), "no new manifest over earlier passes"
    hb.rename(tmp_path / "heartbeat.kept")  # the fix, followed
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert db.exists()


def test_a_damaged_heartbeat_beside_a_manifest_still_heals_on_the_next_pass(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """P111's verifier: the new rule is about a missing manifest only; beside one that is there, a damaged
    heartbeat.json is rewritten by the next pass, as before."""
    cfg = _synced(initialised)
    hb = expand(initialised.state_paths.heartbeat)
    hb.write_bytes(b"{not json")
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert isinstance(json.loads(hb.read_text(encoding="utf-8")), dict)


# ---- B13 round 27 ruling 18: a linked manifest's sides are its target's (P42); P41 ----


def _link_the_manifest(config: Config) -> tuple[Path, Path]:
    """manifest.sqlite moved to real.sqlite in the state folder, sides with it, and a link in its place."""
    root = expand(config.state_paths.root)
    db = expand(config.state_paths.db)
    real = root / "real.sqlite"
    for name in ("", "-wal", "-shm"):
        here = db.with_name(db.name + name)
        if here.exists():
            here.rename(real.with_name(real.name + name))
    db.symlink_to(real)
    return db, real


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_beside_a_linked_manifest_a_folder_at_its_targets_side_is_the_persons_not_one_beside_the_link(
    initialised: Config, capsys: pytest.CaptureFixture[str], suffix: str
) -> None:
    """B13 round 27 P42 (ruling 18): sqlite keeps a linked manifest's -wal and -shm by the target. A folder
    at real.sqlite-shm passed status while every sync stopped on "attempt to write a readonly database", and
    a harmless folder at manifest.sqlite-shm beside the link FAILed status and blocked curate. The target's
    side is judged (status FAILs it, sync stops on that line, following the fix ends in a clean sync); the
    one beside the link is not."""
    cfg = _synced(initialised)
    db, real = _link_the_manifest(initialised)
    beside = db.with_name(db.name + suffix)
    beside.mkdir()  # a leftover beside the link: none of sqlite's business
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    assert not [ln for ln in out if ln.startswith("[FAIL] state_dir")], out
    for argv in (["sync"], ["curate"]):
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_OK, (argv, capsys.readouterr())
    side = real.with_name(real.name + suffix)
    side.unlink(missing_ok=True)
    side.mkdir()
    assert doctor.manifest_sides(db) == (real.with_name("real.sqlite-wal"), real.with_name("real.sqlite-shm"))
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    role = doctor.SIDE_ROLES[suffix]
    (failed,) = [ln for ln in out if ln.startswith("[FAIL] state_dir.files ")]
    assert rc == cli.EXIT_FAILED and f"{side} is a folder, not a file: {role} belongs there" in failed, out
    assert "aside, keeping a copy" in failed, failed
    capsys.readouterr()
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_FAILED
    assert capsys.readouterr().err.splitlines() == ["error: state: " + failed.split(" — ", 1)[1]]
    side.rename(side.with_name(side.name + ".kept"))  # the fix, followed
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK, capsys.readouterr()


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_linked_manifests_side_this_account_cannot_open_is_named_by_its_own_folder(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """P42's verifier: the refused-file list and status's chmod name the target's side, by its own path."""
    _synced(initialised)
    _db, real = _link_the_manifest(initialised)
    wal = real.with_name("real.sqlite-wal")
    wal.write_bytes(b"")
    wal.chmod(0o000)
    try:
        assert wal in doctor.refused_state_files(initialised)
        shut = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.files" and not r.ok]
        assert any(r.fix == f"chmod 600 {doctor._shell_path(wal)}" for r in shut), shut
    finally:
        wal.chmod(0o600)


def test_a_hold_over_a_lost_manifest_link_to_nothing_in_a_usable_folder_is_not_refused(
    initialised: Config, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """B13 round 27 P41: with heartbeat.json recording passes and manifest.sqlite a link to nothing in a
    usable folder, hold and its release were refused as a file that is not a file, while over a plain lost
    manifest they are not (E25: they change no page, and a hold blocks purges). Both run; curate and purge
    still stop on the lost line; nothing is created through the link; a link into a missing folder still
    refuses the hold."""
    cfg = _synced(initialised)
    db = expand(initialised.state_paths.db)
    root = expand(initialised.state_paths.root)
    for name in ("", "-wal", "-shm"):
        here = db.with_name(db.name + name)
        if here.exists():
            here.rename(tmp_path / here.name)
    db.symlink_to(root / "gone.sqlite")
    hold = ["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg]
    capsys.readouterr()
    assert cli.main(hold) == cli.EXIT_OK, capsys.readouterr()
    assert governance.state_holds(root)
    assert cli.main(["hold", "all", "--release", "--owner", "Contoso Legal", "--config", cfg]) == cli.EXIT_OK
    for argv in (["curate"], ["purge", "--queue"]):
        capsys.readouterr()
        assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
        assert manifest_mod.LOST_MANIFEST in capsys.readouterr().err, argv
    assert not (root / "gone.sqlite").exists()
    db.unlink()
    db.symlink_to(root / "missing" / "gone.sqlite")
    capsys.readouterr()
    assert cli.main(hold) == cli.EXIT_FAILED
    assert "whose folder does not exist or refuses a write" in capsys.readouterr().err


# ---- B13 round 27: status, doctor and the commands after round 26 ----


def _stale_skill() -> None:
    for path in skill.skill_paths():
        path.write_text("an older skill\n", encoding="utf-8")


@pytest.mark.parametrize("where", ["lock", "heartbeat", "holds"])
def test_a_state_file_a_sync_stops_on_leads_status_ahead_of_the_skill_step(
    initialised: Config, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """B13 round 27 P23: with the skill stale and a folder at the lock or heartbeat.json, status's NEXT was
    the skill's "run sync", whose sync stops on the folder and sends the agent back to status. The FAIL a sync
    stops on leads, in status and in the setup report's Loop hook alike."""
    cfg = _synced(initialised)
    sp = initialised.state_paths
    path, check = {
        "lock": (expand(sp.lock), "lock"),
        "heartbeat": (expand(sp.heartbeat), "heartbeat"),
        "holds": (governance._holds_file(expand(sp.root)), "governance.hold"),
    }[where]
    _stale_skill()
    _make_not_a_file(path, "a folder")
    capsys.readouterr()
    rc, out = _status(cfg, capsys)
    want = f"NEXT: the {check} check failed: its fix on the [FAIL] line below is the person's"
    assert rc == cli.EXIT_FAILED and out[0].startswith(want), out[0]
    hooks = cli._report_hooks()
    assert hooks.doctor is not None and hooks.loop_next is not None
    hooks.doctor(initialised)
    said = hooks.loop_next(initialised)
    lines = said.lines() if isinstance(said, loop.NextStep) else said
    assert lines[0] == out[0], (lines, out[0])


def test_a_fail_the_sync_reads_past_keeps_the_skill_step_first(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """P23's verifier: a loose mode on the manifest stops no sync, so it never leads over the skill's sync;
    with nothing a sync stops on, the skill's step still leads."""
    cfg = _synced(initialised)
    _stale_skill()
    loose = doctor.CheckResult(
        "state_dir.files",
        False,
        "readable by others: manifest.sqlite (0644)",
        doctor.Severity.ERROR,
        "chmod 600 x",
    )
    assert cli._state_stop_first(initialised, [loose]) is None
    capsys.readouterr()
    _rc, out = _status(cfg, capsys)
    assert out[0].startswith("NEXT: the agentsync-docs skill is missing or out of date"), out[0]


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_queue_run_over_a_manifest_it_cannot_open_warns_once_in_words(
    initialised: Config, caplog: pytest.LogCaptureFixture
) -> None:
    """B13 round 27 P25: a queue run over a manifest this account cannot open logged two WARNINGs per entry,
    one with a PurgeSelector repr and the full path. One WARNING, counted, with its cause in words."""
    _synced(initialised)
    root = expand(initialised.state_paths.root)
    for n in range(3):
        sel = governance.PurgeSelector(source_id="source", stable_id=f"contoso-{n}")
        assert governance.enqueue_purge(root, sel, governance.PurgeReason.LABEL_ESCALATION)
    db = expand(initialised.state_paths.db)
    db.chmod(0o000)
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="agentsync.governance"):
            reps = governance.run_purge_queue(initialised, dry_run=True)
    finally:
        db.chmod(0o600)
    assert [bool(r.not_run) for r in reps] == [True, True, True]
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1, warned
    assert "3 queued purges" in warned[0] and "permission denied" in warned[0], warned
    assert "PurgeSelector(" not in warned[0] and str(Path.home()) not in warned[0], warned


def test_status_keeps_the_holds_it_read_beside_a_queue_or_hold_list_that_is_not_a_file(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 27 P44: one try read the holds and the queue, so a folder at purge-queue.json dropped the
    HOLD lines status had read, and a folder at holds.json dropped sources.toml's own hold. Each read keeps
    its own line."""
    cfg = _synced(initialised)
    root = expand(initialised.state_paths.root)
    assert cli.main(["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal", "--config", cfg]) == 0
    queue = governance._queue_file(root)
    _make_not_a_file(queue, "a folder")
    lines = cli._status_lines(initialised)
    assert any(ln.startswith("HOLD: hold all (state): matter 8") for ln in lines), lines
    assert any(ln.startswith("status: cannot read the state: the purge queue is not a file") for ln in lines)
    queue.rmdir()
    text = initialised.config_path.read_text(encoding="utf-8")
    initialised.config_path.write_text(
        text + '\n[governance]\nhold = true\nhold_reason = "matter 9"\nhold_owner = "Contoso Legal"\n',
        encoding="utf-8",
    )
    governance._holds_file(root).unlink()
    _make_not_a_file(governance._holds_file(root), "a folder")
    lines = cli._status_lines(load_config(initialised.config_path))
    assert any(ln.startswith("HOLD: hold all (config)") for ln in lines), lines


def test_a_fifo_at_the_wal_this_account_cannot_open_stops_every_command_first(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 27 P49: the -wal fifo exemption (sqlite removes a fifo it can open) also passed one this
    account cannot open, so curate ran its work before stopping and hold ran over it. That fifo stops at the
    guard, as a folder does; one it can open is still left to sqlite."""
    cfg = _synced(initialised)
    db = expand(initialised.state_paths.db)
    wal = db.with_name(db.name + "-wal")
    wal.unlink(missing_ok=True)
    os.mkfifo(wal, 0o600)
    assert doctor.state_not_a_file(initialised) is None
    wal.chmod(0o000)
    try:
        found = doctor.state_not_a_file(initialised)
        assert found is not None and found[0] == wal, found
        for argv in (["curate"], ["hold", "all", "--reason", "matter 8", "--owner", "Contoso Legal"]):
            capsys.readouterr()
            assert cli.main([*argv, "--config", cfg]) == cli.EXIT_FAILED, argv
            said = capsys.readouterr()
            assert said.err.startswith(f"error: state: {wal} is a fifo"), said.err
            assert "finding" not in said.out, said.out
    finally:
        wal.unlink()


def test_a_stale_lock_of_a_job_under_the_default_prefix_names_its_log(initialised: Config) -> None:
    """B13 round 27 P50: with the config's label prefix changed and the job still under the default one, the
    stale-lock fix named no log though com.agentsync.poll.err.log was there. The config's prefix first, then
    the default's."""
    config = dataclasses.replace(initialised, launchd_label_prefix="com.contoso.agentsync")
    lock = expand(config.state_paths.lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    info = lock_mod.LockInfo(pid=999_999, boot_time=1, started_at="2026-10-24T00:00:00Z", label="poll")
    lock.write_text(info.render() + "\n", encoding="utf-8")
    holder = {"pid": 999_999, "label": "poll", "started_by": "launchd"}
    lock_mod._holder_path(lock).write_text(json.dumps(holder), encoding="utf-8")
    log_dir = expand(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    old = log_dir / "com.agentsync.poll.err.log"
    old.write_text("Traceback\n", encoding="utf-8")
    (stale,) = doctor._check_lock(config)
    assert stale.fix == f"read the crash in {old}", stale
    new = log_dir / "com.contoso.agentsync.poll.err.log"
    new.write_text("Traceback\n", encoding="utf-8")
    (stale,) = doctor._check_lock(config)
    assert stale.fix == f"read the crash in {new}", "the current job's log first"


def test_only_doctor_has_a_state_not_a_file() -> None:
    """B13 round 27 P51: governance had a state_not_a_file that built words beside doctor's that finds the
    file; the builder is governance._not_a_file_words."""
    assert not hasattr(governance, "state_not_a_file") and callable(doctor.state_not_a_file)
    detail, fix = governance._not_a_file_words(Path("/x/holds.json"), "a fifo", "the hold list")
    assert detail == "/x/holds.json is a fifo, not a file: the hold list belongs there" and "aside" in fix


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 folder")
@pytest.mark.parametrize("how", ["mode 0000", "another account", "an ACL"])
def test_a_shut_state_folder_names_the_one_check_that_fails_it(
    initialised: Config, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """B13 round 27 P122: over a shut state folder every not-checked line and status's tail said "the
    state_dir lines say why" over one line. They name the one state_dir check that FAILs: state_dir for its
    mode or another account's folder, state_dir.files when the owner bits are open and it still refuses (an
    ACL)."""
    _synced(initialised)
    root = expand(initialised.state_paths.root)
    real_stat = Path.stat
    if how == "mode 0000":
        root.chmod(0o000)
    else:  # its own lstat still refuses, as an ACL or another account's folder does
        me = os.getuid()

        def refused(self: Path, *a: Any, **k: Any) -> os.stat_result:
            if self != root and (self.parent == root or root in self.parents):
                raise PermissionError(errno.EACCES, "Permission denied", str(self))
            st = real_stat(self, *a, **k)
            if self == root and how == "another account":
                fields = list(st)
                fields[4] = me + 1
                return os.stat_result(fields)
            return st

        monkeypatch.setattr(Path, "stat", refused)
        monkeypatch.setattr(Path, "lstat", lambda self: refused(self, follow_symlinks=False))
    try:
        assert doctor.state_folder_shut(initialised)
        line = doctor.state_folder_shut_line(initialised)
        assert line == ("state_dir.files" if how == "an ACL" else "state_dir"), line
        words = doctor.state_folder_unchecked(initialised)
        said = (
            f"not checked: this account cannot look inside the state folder; the {line} [FAIL] line says why"
        )
        assert words == said, words
    finally:
        monkeypatch.undo()
        root.chmod(0o700)


@pytest.mark.parametrize("where", [("-wal",), ("-shm",), ("-wal", "-shm")], ids=["wal", "shm", "both"])
def test_the_listing_note_names_each_side_that_is_not_a_file_by_its_role(
    initialised: Config, where: tuple[str, ...]
) -> None:
    """B13 round 27 P16, P146 (one defect): beside a -wal or -shm that is not a file, the incomplete-listing
    note said "a side file of the state file" while the state_dir.files lines beside it named them. It names
    each by its role, both when both are, and keeps the tail readers key on."""
    _synced(initialised)
    db = expand(initialised.state_paths.db)
    for suffix in where:
        side = db.with_name(db.name + suffix)
        side.unlink(missing_ok=True)
        side.mkdir(mode=0o700)
    warn = doctor.CheckResult(
        name="heartbeat.contoso",
        ok=False,
        detail=doctor.LISTING_INCOMPLETE + "3 passes",
        severity=doctor.Severity.WARN,
        fix="sync again",
    )
    exc = sqlite3.OperationalError("unable to open database file")
    exc.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN  # type: ignore[attr-defined]
    (out,) = cli._listings_unread([warn], exc, db.parent)
    assert out.note is not None and "a side file of the state file" not in out.note, out
    assert out.note.endswith(doctor.LISTING_STATE_SIDE_TAIL), out.note
    if len(where) == 2:
        assert (
            "write-ahead log and shared-memory file are not files" in out.note
            and "moves them aside" in out.note
        )
    else:
        assert f"{doctor.SIDE_ROLES[where[0]]} is not a file" in out.note and "moves it aside" in out.note


# ---- B13 round 27 lane B's reviewer ----


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches a mode-000 folder")
def test_a_linked_manifest_in_a_folder_this_account_cannot_search_gets_that_folders_line(
    initialised: Config, tmp_path: Path
) -> None:
    """P42's reviewer: a link into a folder this account cannot search gave manifest_sides no sides, so the
    target folder's own state_dir.files line could never print."""
    _synced(initialised)
    db = expand(initialised.state_paths.db)
    other = tmp_path / "other"
    other.mkdir()
    for name in ("", "-wal", "-shm"):
        here = db.with_name(db.name + name)
        if here.exists():
            here.rename(other / f"real.sqlite{name}")
    db.symlink_to(other / "real.sqlite")
    other.chmod(0o000)
    try:
        assert doctor.manifest_sides(db) == (other / "real.sqlite-wal", other / "real.sqlite-shm")
        lines = [r for r in doctor._check_state_dir(initialised) if r.name == "state_dir.files" and not r.ok]
        assert any(r.detail.startswith(f"cannot look inside {other}: ") for r in lines), lines
    finally:
        other.chmod(0o700)


def test_a_queued_purge_stamped_with_no_zone_is_judged_never_a_traceback() -> None:
    """P112's reviewer: a naive enqueued_at against a "...Z" first run raised TypeError through status."""
    sel = governance.PurgeSelector(source_id="source", stable_id="contoso-1")
    q = governance.QueuedPurge(sel, governance.PurgeReason.UPSTREAM_DELETED, "2026-01-01T00:00:00")
    assert governance._queued_before(q, "2026-02-01T00:00:00Z") is True
    assert governance._queued_before(q, "2025-12-01T00:00:00Z") is False


def test_a_hold_list_of_the_wrong_shape_invites_no_purge_and_never_crashes_the_loop(
    initialised: Config,
) -> None:
    """P131's reviewer: the loop read holds.json for every queued purge, and a hold list of the wrong shape
    (valid JSON) raised out of next_step; it is a hold list that cannot be read, so no purge is invited."""
    _synced(initialised)
    root = expand(initialised.state_paths.root)
    sel = governance.PurgeSelector.parse("docs=mirror/source/README.txt.md", source_id=None)
    assert governance.enqueue_purge(root, sel, governance.PurgeReason.DLP_REMEDIATION)
    holds = governance._holds_file(root)
    holds.write_text("[]\n", encoding="utf-8")
    assert governance.hold_list_unread(root)
    step = loop.next_step(initialised)
    assert not [w for w in step.waits if "purge" in w], step.waits


def test_the_unjudged_words_over_a_side_that_is_not_a_file_name_the_files_line(initialised: Config) -> None:
    """P24's reviewer: over a readable manifest whose -wal is a folder the note named a state_dir.manifest
    line; status FAILs it under state_dir.files."""
    _synced(initialised)
    db = expand(initialised.state_paths.db)
    wal = db.with_name(db.name + "-wal")
    wal.unlink(missing_ok=True)
    wal.mkdir()
    assert governance._unjudged_check(db) == "state_dir.files"
