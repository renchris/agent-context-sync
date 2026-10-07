"""cycle.py mechanics: recovery after crashes, cursor ordering, source isolation, the lock, dry-run,
retirement.  Crashes are simulated with a BaseException the cycle does not catch (like a SIGKILL mid-cycle:
no cleanup code runs), so the next cycle's ``recover`` sees exactly what a dead process leaves behind."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import itertools
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pptx import Presentation
from pptx.util import Inches

from agentsync import arm_local as al
from agentsync import curate, gitops, governance, loop, materialise, slug
from agentsync import cycle as cycle_mod
from agentsync.arm_local import LocalArm
from agentsync.config import Config, parse_config
from agentsync.convert import ocr
from agentsync.convert import pandoc as pandoc_mod
from agentsync.convert import pdf as pdf_mod
from agentsync.cycle import RecoveryAction, recover, run_cycle
from agentsync.errors import ConversionError, LockHeldError
from agentsync.graph.client import GraphClient
from agentsync.graph.drive import DriveArm
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, CycleReport, PassKind, RowState, ScanResult, Verdict
from agentsync.ops import doctor
from agentsync.ops.lock import SingleWriterLock, read_heartbeat
from agentsync.paths import DocsLayout
from agentsync.publish import Publisher, sidecar_rel
from conftest import config_text
from test_convert_builders import (
    build_annotated_pdf,
    build_picture_pdf,
    page_picture,
    pandoc_build,
    shade_engine,
)
from test_convert_image import picture, picture_bytes, reads, text_png
from test_e2e import (
    GRAPH_SOURCE,
    SID,
    FakeDrive,
    FakeTokens,
    clock,
    config_with,
    git,
    page,
    porcelain,
    source_report,
)
from test_ocr import calls, fake_engine, write_fake
from test_review_fixes import committed_blobs_containing


class Crash(BaseException):
    """Raised to kill a cycle mid-flight without running its except handlers."""


def run(config: Config, **kwargs: Any) -> CycleReport:
    return run_cycle(config, mode=kwargs.pop("mode", CycleMode.POLL), now=clock, **kwargs)


def crash(*_a: object, **_k: object) -> None:
    raise Crash


@pytest.fixture
def drive_env(tmp_path: Path, local_source_dir: Path, fixture_files: dict[str, Path]) -> Iterator[Any]:
    config = config_with(tmp_path, local_source_dir, GRAPH_SOURCE)
    drive = FakeDrive({"I1": ("notes.md", b"# Notes\n\nfirst version\n")})
    client = GraphClient(
        FakeTokens(),
        user_agent="NONISV|test|agentsync/0",
        transport=httpx.MockTransport(drive.handler),
        sleep=lambda _s: None,
    )
    yield config, drive, client
    client.close()


def edit_i1(drive: FakeDrive, text: bytes) -> None:
    drive.version["I1"] += 1
    drive.qx["I1"] = f"qx-I1-{drive.version['I1']}"
    drive.files["I1"] = ("notes.md", text)


def cursor(config: Config, sid: str = "drive") -> tuple[str | None, str | None]:
    with Manifest(config.state_paths.db) as m:
        c = m.get_cursor(sid)
        return (c.current, c.pending) if c is not None else (None, None)


# ---------------------------------------------------------------------------------------------------------
# cursor ordering and crash recovery
# ---------------------------------------------------------------------------------------------------------


def test_crash_after_commit_before_promotion_adopts_the_pending_cursor(
    drive_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, drive, client = drive_env
    assert run(config, client=client, only=["drive"]).exit_code == 0
    assert cursor(config)[0] is not None and cursor(config)[0].endswith("token=T1")  # type: ignore[union-attr]
    edit_i1(drive, b"# Notes\n\nsecond version\n")
    monkeypatch.setattr(Manifest, "promote_cursors", crash)
    with pytest.raises(Crash):
        run(config, client=client, only=["drive"])
    monkeypatch.undo()
    head_msg = git(config.docs_repo, "log", "-1", "--format=%B")
    assert "Agentsync-Run: 2" in head_msg  # the commit landed ...
    current, pending = cursor(config)
    assert current is not None and current.endswith("token=T1") and pending is not None  # ... unpromoted
    with Manifest(config.state_paths.db) as m:
        action = recover(config, m)
        assert action is RecoveryAction.ADOPT_PENDING
        c = m.get_cursor("drive")
        assert (
            c is not None and c.current is not None and c.current.endswith("token=T2") and c.pending is None
        )
        assert m.last_runs(1)[0][2] == "aborted"
    after = run(config, client=client, only=["drive"])
    assert after.exit_code == 0 and after.commit_sha is None  # nothing re-applied, nothing lost
    assert "second version" in page(config.docs_repo, "mirror/drive/projects/notes.md")[1]


def test_crash_before_commit_discards_the_cursor_and_the_next_cycle_commits(
    drive_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, drive, client = drive_env
    run(config, client=client, only=["drive"])
    head = git(config.docs_repo, "rev-parse", "HEAD")
    edit_i1(drive, b"# Notes\n\nthird version\n")
    monkeypatch.setattr(gitops, "commit_cycle", crash)
    with pytest.raises(Crash):
        run(config, client=client, only=["drive"])
    monkeypatch.undo()
    assert git(config.docs_repo, "rev-parse", "HEAD") == head
    assert porcelain(config.docs_repo) != ""  # published, never committed
    current, pending = cursor(config)
    assert current is not None and current.endswith("token=T1") and pending is not None
    with Manifest(config.state_paths.db) as m:
        assert recover(config, m) is RecoveryAction.DISCARD_PENDING
        c = m.get_cursor("drive")
        assert (
            c is not None and c.pending is None and c.current is not None and c.current.endswith("token=T1")
        )
    report = run(config, client=client, only=["drive"])
    assert report.exit_code == 0 and report.commit_sha is not None
    assert "third version" in page(config.docs_repo, "mirror/drive/projects/notes.md")[1]
    assert porcelain(config.docs_repo) == ""
    current, _ = cursor(config)
    assert current is not None and current.endswith("token=T3")  # the replayed round's cursor


def test_blocking_lint_commits_nothing_and_promotes_no_cursor(drive_env: Any) -> None:
    config, _drive, client = drive_env
    repo = config.docs_repo
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "topics").mkdir(parents=True, exist_ok=True)
    (repo / "topics" / "escape").symlink_to("/etc")
    report = run(config, client=client)
    assert report.exit_code == 1 and report.commit_sha is None
    assert any(f.code == "SYMLINK" and f.blocking for f in report.lint_findings)
    assert all(not s.cursor_advanced for s in report.sources)
    assert cursor(config) == (None, None)
    assert gitops.head_sha(repo) is None
    assert "BLOCKING SYMLINK" in (repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    (repo / "topics" / "escape").unlink()
    healed = run(config, client=client)
    assert healed.exit_code == 0 and healed.commit_sha is not None
    current, _ = cursor(config)
    assert current is not None


def test_head_moved_by_hand_resets_generated_paths_and_republishes(sample_config: Config) -> None:
    repo = sample_config.docs_repo
    run(sample_config)
    victim = f"mirror/{SID}/projects/sample.docx.md"
    git(repo, "rm", "-q", victim)
    git(repo, "commit", "-q", "-m", "manual: someone deleted a generated page")
    assert not (repo / victim).exists()
    with Manifest(sample_config.state_paths.db) as m:
        assert recover(sample_config, m) is RecoveryAction.RESET_GENERATED
    assert (repo / victim).is_file()  # re-published from the converter cache
    report = run(sample_config)
    assert report.exit_code == 0 and report.commit_sha is not None
    assert victim in git(repo, "ls-files")
    assert porcelain(repo) == ""


def test_archive_on_upstream_delete_survives_a_head_moved_by_hand(
    sample_config: Config, local_source_dir: Path
) -> None:
    """[governance] archive: the cycle copies a deleted page to archive/, queues no purge, skips compaction,
    and the repair path re-renders the mirror tombstone still naming the archive copy."""
    cfg = sample_config.config_path
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[governance]\narchive = true\n", encoding="utf-8")
    repo = sample_config.docs_repo
    run(sample_config)
    victim, kept = f"mirror/{SID}/projects/sample.txt.md", f"archive/{SID}/projects/sample.txt.md"
    (local_source_dir / "projects" / "sample.txt").unlink()
    run(sample_config)
    report = run(sample_config, mode=CycleMode.RECONCILE)
    assert report.exit_code == 0 and kept in git(repo, "ls-files")
    assert "\nstatus: archived\n" in (repo / kept).read_text(encoding="utf-8")
    assert "- archive on: history is kept" in (repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert governance.pending_purges(sample_config.state_paths.root) == []
    git(repo, "rm", "-q", victim)
    git(repo, "commit", "-q", "-m", "manual: someone deleted a tombstone")
    with Manifest(sample_config.state_paths.db) as m:
        assert recover(sample_config, m) is RecoveryAction.RESET_GENERATED
    assert f"kept, searchable, at {kept}" in (repo / victim).read_text(encoding="utf-8")
    assert run(sample_config).exit_code == 0 and porcelain(repo) == ""


def test_crash_between_observation_and_rename_publish_is_repaired(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = sample_config.docs_repo
    run(sample_config)
    (local_source_dir / "projects" / "acme").rename(local_source_dir / "projects" / "Acme Corp")
    monkeypatch.setattr(Publisher, "rewrite_frontmatter", crash)
    with pytest.raises(Crash):
        run(sample_config)
    monkeypatch.undo()
    # The observation transaction committed the new rel_path; the page still sits at the old one.
    assert (repo / f"mirror/{SID}/projects/acme/kickoff-notes.docx.md").is_file()
    report = run(sample_config)
    assert report.exit_code == 0 and report.commit_sha is not None
    assert (repo / f"mirror/{SID}/projects/acme-corp/kickoff-notes.docx.md").is_file()
    assert not (repo / f"mirror/{SID}/projects/acme").exists()
    assert porcelain(repo) == ""


def test_cycle_level_failure_is_reported_not_raised(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Publisher, "write_index", boom)
    report = run(sample_config)
    assert report.exit_code == 1 and report.commit_sha is None
    assert any(f.code == "CYCLE" and "disk full" in f.message for f in report.lint_findings)
    with Manifest(sample_config.state_paths.db) as m:
        assert m.last_runs(1)[0][2] == "failed"
    monkeypatch.undo()
    assert run(sample_config).commit_sha is not None


# ---------------------------------------------------------------------------------------------------------
# isolation, lock, dry run, paused/retired
# ---------------------------------------------------------------------------------------------------------


def two_source_config(tmp_path: Path, a: Path, b: Path) -> Config:
    text = config_text(tmp_path / "docs", tmp_path / "state", tmp_path / "cache", a)
    text += f'\n[[source]]\nid = "second"\nkind = "local"\npath = "{b}"\n'
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return parse_config(text, config_path=path)


def test_a_failing_source_does_not_stop_the_others(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "note.txt").write_text("second source\n", encoding="utf-8")
    config = two_source_config(tmp_path, local_source_dir, other)
    real_scan = LocalArm.scan

    def scan(self: LocalArm, cursor: str | None, *, full: bool) -> ScanResult:
        if self.source_id == "second":
            raise RuntimeError("provider exploded")
        return real_scan(self, cursor, full=full)

    monkeypatch.setattr(LocalArm, "scan", scan)
    report = run(config)
    assert report.exit_code == 1
    good = next(s for s in report.sources if s.source_id == SID)
    bad = next(s for s in report.sources if s.source_id == "second")
    assert good.errors == () and bad.errors and "provider exploded" in bad.errors[0]
    assert report.commit_sha is not None  # the healthy source still published
    assert (config.docs_repo / f"mirror/{SID}/projects/sample.docx.md").is_file()
    assert not (config.docs_repo / "mirror/second").exists()
    with Manifest(config.state_paths.db) as m:
        assert m.last_runs(1)[0][2] == "partial"
    hb = read_heartbeat(config.state_paths.heartbeat)
    assert hb[SID]["ok"] is True and hb["second"]["ok"] is False
    monkeypatch.undo()
    healed = run(config)
    assert healed.exit_code == 0 and (config.docs_repo / "mirror/second/note.txt.md").is_file()


def test_a_failing_item_is_a_row_not_a_crash(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fetch = LocalArm.fetch

    def fetch(self: LocalArm, item: Any, dest_dir: Path, budget: Any) -> Any:
        if item.name == "sample.pdf":
            raise OSError(5, "Input/output error")
        return real_fetch(self, item, dest_dir, budget)

    monkeypatch.setattr(LocalArm, "fetch", fetch)
    report = run(sample_config)
    assert report.exit_code == 0 and report.commit_sha is not None
    s = report.sources[0]
    assert s.counts.get(Verdict.ERROR) == 1 and any("sample.pdf" in e for e in s.errors)
    with Manifest(sample_config.state_paths.db) as m:
        row = m.item_by_path(SID, "projects/sample.pdf")
        assert row is not None and row.last_verdict is Verdict.ERROR
    monkeypatch.undo()
    retry = run(sample_config)
    assert [c.path for c in retry.changes] == [f"mirror/{SID}/projects/sample.pdf.md"]


def test_lock_held_raises_lock_held_error(sample_config: Config) -> None:
    lock = SingleWriterLock(sample_config.state_paths.lock, "other")
    lock.acquire()
    try:
        with pytest.raises(LockHeldError):
            run(sample_config)
    finally:
        lock.release()
    assert run(sample_config).exit_code == 0


def test_dry_run_classifies_without_writing(sample_config: Config) -> None:
    repo = sample_config.docs_repo
    report = run(sample_config, mode=CycleMode.DRY_RUN)
    assert report.exit_code == 0 and report.commit_sha is None and report.changes == ()
    assert report.sources[0].counts.get(Verdict.CREATED) == 13
    assert gitops.head_sha(repo) is None and not (repo / "mirror").exists()
    with Manifest(sample_config.state_paths.db) as m:
        assert list(m.iter_items(SID)) == [] and m.last_runs(1) == []
    assert run(sample_config).commit_sha is not None


def test_a_cycle_stores_the_empty_cloud_folders_its_walk_found(
    tmp_path: Path, tmp_docs_repo: Path, tmp_state_dir: Path
) -> None:
    """``loop.next_step`` names the folders that keep a listing incomplete from manifest meta, so status and
    STATE.md never walk the source a second time. Each real pass rewrites the list (empty once the walk
    finds none, or cannot walk); a dry run writes nothing."""
    root = Path.home() / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Bids" / "Old").mkdir(parents=True)
    (root / "README.txt").write_text("sentinel\n", encoding="utf-8")
    text = config_text(tmp_docs_repo, tmp_state_dir, tmp_path / "cache", root)
    config = parse_config(text, config_path=tmp_path / "sources.toml")
    key = cycle_mod._EMPTY_DIRS_META + SID

    def stored() -> str | None:
        with Manifest(config.state_paths.db) as m:
            return m.get_meta(key)

    report = run(config)
    assert not report.sources[0].enumeration_complete
    assert stored() == '["Bids/Old"]'
    (root / "Plans — Draft").mkdir()
    assert run(config, mode=CycleMode.DRY_RUN).exit_code == 0
    assert stored() == '["Bids/Old"]', "a dry run stores nothing"
    run(config)
    assert stored() == '["Bids/Old", "Plans — Draft"]', "sorted, names as they are"
    (root / "Bids" / "Old" / "a.txt").write_text("x\n", encoding="utf-8")
    (root / "Plans — Draft").rmdir()
    assert run(config).sources[0].enumeration_complete
    assert stored() == ""
    (root / "Bids" / "Empty").mkdir()
    run(config)
    assert stored() == '["Bids/Empty"]'
    root.rename(root.with_name("Work moved"))
    assert not run(config).sources[0].enumeration_complete
    assert stored() == "", "a walk that could not run knows no empty folder"


def test_a_cycle_makes_agent_written_paths_owner_only_and_a_dry_run_does_not(sample_config: Config) -> None:
    """Field report 2026-10-06: the baseline draft, written by an agent under umask 022, left ``_eval/``
    readable by group and other, and the next status ended on a ``docs_repo.permissions`` FAIL."""
    repo = sample_config.docs_repo
    run(sample_config)
    eval_dir, topic_dir = repo / "_eval", repo / "topics" / "contoso" / "notes"
    eval_dir.mkdir()
    topic_dir.mkdir(parents=True)
    files = [eval_dir / "questions.md", topic_dir / "scratch.txt", repo / "NOTES.txt"]
    for f in files:
        f.write_text("1. What did Contoso decide?\n", encoding="utf-8")
        f.chmod(0o644)
    script = topic_dir / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    dirs = [eval_dir, topic_dir, topic_dir.parent]
    for d in dirs:
        d.chmod(0o755)

    def modes(paths: list[Path]) -> set[int]:
        return {p.stat().st_mode & 0o777 for p in paths}

    def permissions() -> doctor.CheckResult:
        return next(r for r in doctor.run_checks(sample_config) if r.name == "docs_repo.permissions")

    assert not permissions().ok and "_eval" in permissions().detail
    assert run(sample_config, mode=CycleMode.DRY_RUN).exit_code == 0
    assert modes(files) == {0o644} and modes(dirs) == {0o755} and not permissions().ok

    assert run(sample_config).exit_code == 0
    assert modes(files) == {0o600} and modes(dirs) == {0o700} and modes([script]) == {0o700}
    assert permissions().ok, permissions().detail
    # The draft rode in the cycle's commit and a mode change dirties nothing; the stray file is not a
    # path the cycle commits.
    assert porcelain(repo) == "?? NOTES.txt\n"


def test_tightening_follows_no_symlink_and_leaves_the_mirror_walk_to_the_publisher(tmp_path: Path) -> None:
    repo, outside = tmp_path / "docs", tmp_path / "outside"
    for d in (repo / "_eval", repo / "topics", repo / "mirror" / "src", repo / "other" / "deep", outside):
        d.mkdir(parents=True)
    loose = [outside / "kept.txt", repo / "mirror" / "src" / "page.md", repo / "other" / "deep" / "f.txt"]
    for f in loose:
        f.write_text("x\n", encoding="utf-8")
        f.chmod(0o644)
    for d in (outside, repo / "mirror" / "src", repo / "other" / "deep", repo / "other", repo / "mirror"):
        d.chmod(0o755)
    (repo / "_eval" / "file-link").symlink_to(outside / "kept.txt")
    (repo / "topics" / "dir-link").symlink_to(outside, target_is_directory=True)
    (repo / "top-link").symlink_to(outside / "kept.txt")
    assert cycle_mod._tighten_agent_writes(repo) == 2  # the two top-level folders themselves, nothing else
    assert (repo / "mirror").stat().st_mode & 0o777 == (repo / "other").stat().st_mode & 0o777 == 0o700
    assert {f.stat().st_mode & 0o777 for f in loose} == {0o644}
    assert (
        outside.stat().st_mode & 0o777 == 0o755 and (repo / "mirror" / "src").stat().st_mode & 0o777 == 0o755
    )
    assert cycle_mod._tighten_agent_writes(repo) == 0
    assert cycle_mod._tighten_agent_writes(tmp_path / "missing") == 0


def test_tightening_never_follows_an_entry_swapped_for_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entries it tightens are the ones others can still write, so one can be replaced between the look
    and the change. The mode is changed through a descriptor opened without following a link: a symlink put
    there in between is an error for that path, and its target outside the docs repo keeps its mode."""
    repo, outside = tmp_path / "docs", tmp_path / "outside"
    (repo / "_eval").mkdir(parents=True)
    outside.mkdir()
    outside.chmod(0o755)
    before = tmp_path / "before.md"
    before.write_text("1. What did Contoso decide?\n", encoding="utf-8")
    before.chmod(0o644)
    swapped = repo / "_eval" / "notes.md"
    swapped.symlink_to(outside, target_is_directory=True)
    lstat = Path.lstat

    def first_look(self: Path) -> os.stat_result:  # what the entry was before the swap: a loose file
        return lstat(before) if self == swapped else lstat(self)

    monkeypatch.setattr(Path, "lstat", first_look)
    with pytest.raises(OSError):
        cycle_mod._clear_group_other(swapped)
    cycle_mod._tighten_agent_writes(repo)
    assert outside.stat().st_mode & 0o777 == 0o755


def test_paused_source_is_skipped_and_retired_source_is_tombstoned_with_banners(
    tmp_path: Path, local_source_dir: Path
) -> None:
    config = config_with(tmp_path, local_source_dir)
    run(config)
    repo = config.docs_repo
    topic = repo / "topics" / "acme" / "acme-plan.md"
    topic.parent.mkdir(parents=True)
    fm, _ = page(repo, f"mirror/{SID}/projects/sample.docx.md")
    topic.write_text(
        "---\nkind: plan\nentity: acme\nsources:\n"
        f"  - path: ../../mirror/{SID}/projects/sample.docx.md\n"
        f"    at_rendered_sha256: {fm['rendered_sha256']}\n    role: primary\n---\n# Acme plan\n\nBody.\n",
        encoding="utf-8",
    )
    assert run(config).commit_sha is not None  # the curated page + DEPENDS.tsv ride in a commit
    assert "topics/acme/acme-plan.md" in (repo / "DEPENDS.tsv").read_text(encoding="utf-8")

    paused_text = config.config_path.read_text(encoding="utf-8").replace(
        'kind = "local"', 'kind = "local"\nstate = "paused"'
    )
    paused = parse_config(paused_text, config_path=config.config_path)
    report = run(paused)
    assert report.sources[0].skipped_reason == "paused" and report.changes == ()
    if report.commit_sha is not None:  # only the surface that names the state moves (INDEX/README)
        touched = set(git(repo, "show", "--name-only", "--format=", "HEAD").split())
        assert touched <= {"INDEX.md", "README.md", "_sync/STATE.snapshot.md"}, touched
    assert run(paused).commit_sha is None

    retired_text = config.config_path.read_text(encoding="utf-8").replace(
        'kind = "local"', 'kind = "local"\nstate = "retired"\nretired_reason = "project closed"'
    )
    retired = parse_config(retired_text, config_path=config.config_path)
    report = run(retired)
    assert report.commit_sha is not None
    assert report.changes and all(c.op.value == "D" for c in report.changes)
    fm2, _ = page(repo, f"mirror/{SID}/projects/sample.docx.md")
    assert fm2["status"] == "deleted" and fm2["reason"] == "retired:project closed"
    assert "SOURCE RETIRED — project closed" in topic.read_text(encoding="utf-8")
    assert porcelain(repo) == ""


def test_git_log_holds_only_real_change(sample_config: Config, local_source_dir: Path) -> None:
    repo = sample_config.docs_repo
    for _ in range(3):
        run(sample_config)
    assert int(git(repo, "rev-list", "--count", "HEAD").strip()) == 1
    (local_source_dir / "projects" / "sample.txt").write_text("new\n", encoding="utf-8")
    run(sample_config)
    run(sample_config)
    log = git(repo, "log", "--format=%s").splitlines()
    assert len(log) == 2 and log[0] == f"sync: 0a 1m 0r 0d {SID}"
    body = git(repo, "log", "-1", "--format=%B")
    assert "Agentsync-Run: 4" in body and "Agentsync-Mode: poll" in body
    assert subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD"], check=False).returncode == 0


def test_a_walk_that_times_out_on_a_privacy_prompt_tombstones_nothing(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field report N8: a listing macOS holds for an Allow prompt ends at its time limit as an incomplete
    source with a "click Allow" alarm. Nothing under the unlisted folder is read as gone."""
    assert run(sample_config).commit_sha is not None
    with Manifest(sample_config.state_paths.db) as m:
        before = {r.stable_id: r.state for r in m.iter_items("local-fixture")}
    assert before and RowState.TOMBSTONE not in before.values()
    real = al._list_dir
    release = threading.Event()

    def held(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        if path == local_source_dir / "projects":
            release.wait(5.0)
        return real(path, dir_dataless=dir_dataless)

    monkeypatch.setattr(al, "_list_dir", held)
    monkeypatch.setattr(al, "LISTING_TIMEOUT_S", 0.2, raising=False)
    try:
        report = run(sample_config)
    finally:
        release.set()
    src = report.sources[0]
    assert not src.enumeration_complete
    assert any("click Allow" in a for a in src.alarms), src.alarms
    assert not any(c.op.value == "D" for c in report.changes), report.changes
    with Manifest(sample_config.state_paths.db) as m:
        after = {r.stable_id: r.state for r in m.iter_items("local-fixture")}
    assert after == before


def test_a_held_walk_fetches_nothing_under_the_held_root(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field N8 review: once a walk times out, a fetch would lstat and read under the root macOS is holding
    and wait as the walk did. The pass fetches nothing, the pending row stays queued, and the pass is recorded
    as held (next-step says click Allow)."""
    assert run(sample_config).commit_sha is not None
    with Manifest(sample_config.state_paths.db) as m:
        sid = next(r.stable_id for r in m.iter_items("local-fixture") if not r.is_dir)
        m.set_verdict("local-fixture", sid, Verdict.DEFERRED)
    real = al._list_dir
    release = threading.Event()
    fetched: list[Path] = []

    def held(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        if path == local_source_dir / "projects":
            release.wait(5.0)
        return real(path, dir_dataless=dir_dataless)

    def blocked(src: Path, dest: Path, budget: object) -> object:
        fetched.append(src)
        release.wait(5.0)
        raise AssertionError("fetched under a held root")

    monkeypatch.setattr(al, "_list_dir", held)
    monkeypatch.setattr(al, "materialise", blocked)
    monkeypatch.setattr(al, "LISTING_TIMEOUT_S", 0.2, raising=False)
    try:
        report = run(sample_config)
    finally:
        release.set()
    assert fetched == []
    assert any("click Allow" in a for a in report.sources[0].alarms), report.sources[0].alarms
    with Manifest(sample_config.state_paths.db) as m:
        row = m.get_item("local-fixture", sid)
        last = m.last_source_pass("local-fixture")
    assert row is not None and row.last_verdict is Verdict.DEFERRED
    assert last is not None and (last.skipped_reason or "").startswith(cycle_mod.LISTING_HELD)


def test_a_held_root_read_ends_in_the_same_click_allow_pass(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field N8 review: the root's own metadata reads (lstat, volume UUID) share the listings' time limit, so
    a prompt holding them ends in the click-Allow pass instead of a sync that never returns."""
    assert run(sample_config).commit_sha is not None
    release = threading.Event()
    real = al.volume_uuid

    def held(path: Path) -> str:
        release.wait(5.0)
        return real(path)

    monkeypatch.setattr(al, "volume_uuid", held)
    monkeypatch.setattr(al, "LISTING_TIMEOUT_S", 0.2, raising=False)
    started = time.monotonic()
    try:
        report = run(sample_config)
    finally:
        release.set()
    assert time.monotonic() - started < 4.0
    src = report.sources[0]
    assert not src.enumeration_complete and any("click Allow" in a for a in src.alarms), src.alarms
    assert not any(c.op.value == "D" for c in report.changes), report.changes


# ---------------------------------------------------------------------------------------------------------
# performance paths: H0 fast path, batched writes, the land gate on a clean tree; inbox (name, size) dedup
# ---------------------------------------------------------------------------------------------------------


def test_noop_cycle_takes_the_h0_fast_path_for_every_file(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run(sample_config).commit_sha is not None
    upserted: list[int] = []
    touched: list[int] = []
    real_many, real_touch = Manifest.upsert_observed_many, Manifest.touch_observed

    def many(self: Manifest, observations: Any, **kw: Any) -> None:
        upserted.append(len(observations))
        real_many(self, observations, **kw)

    def touch(self: Manifest, source_id: str, ids: Any, **kw: Any) -> int:
        n = real_touch(self, source_id, ids, **kw)
        touched.append(n)
        return n

    monkeypatch.setattr(Manifest, "upsert_observed_many", many)
    monkeypatch.setattr(Manifest, "touch_observed", touch)
    noop = run(sample_config)
    assert noop.exit_code == 0 and noop.commit_sha is None
    files = sum(1 for p in local_source_dir.rglob("*") if p.is_file() and not p.name.startswith("~$"))
    assert upserted == [0] and touched == [files]  # nothing decoded or rewritten, every row stamped
    with Manifest(sample_config.state_paths.db) as m:
        rows = [r for r in m.iter_items(SID) if not r.is_dir]
        run_id = m.last_runs(1)[0][0]
    assert {r.last_seen_run for r in rows} == {run_id} and {r.last_verdict for r in rows} == {
        Verdict.UNCHANGED
    }
    # an edit after the fast pass is still seen (stat tuple moved) and published
    (local_source_dir / "projects" / "sample.md").write_text("# Sample\n\nedited after a fast pass\n")
    edited = run(sample_config)
    assert edited.commit_sha is not None and upserted[-1] == 1


def test_land_gate_is_skipped_only_when_nothing_can_land(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentsync import lints  # noqa: PLC0415

    gates: list[int] = []
    real = lints.run_land_gate

    def spy(repo: Path, changed: Any, **kwargs: Any) -> Any:
        gates.append(len(changed))
        return real(repo, changed, **kwargs)

    monkeypatch.setattr(lints, "run_land_gate", spy)
    run(sample_config)
    assert len(gates) == 1  # first sync: content to land
    run(sample_config)
    assert len(gates) == 1  # clean POLL: nothing would be committed, no gate
    run(sample_config, mode=CycleMode.RECONCILE)
    assert len(gates) == 2  # RECONCILE always runs it
    (sample_config.docs_repo / "mirror" / "hand-edit.md").write_text("x\n")  # dirty tree, no change reported
    report = run(sample_config)
    assert len(gates) == 3 and report.commit_sha is None


INBOX_SOURCE = """
[[source]]
id = "inbox"
kind = "inbox"
path = "{path}"
quiescence_s = 0
"""


def test_an_atomic_re_export_converts_within_one_interactive_sync(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field N4: a writer that follows the contract (temp name, then rename) is converted by the sync that
    sees it, not the one after: an interactive sync waits once for the inbox to settle; launchd never does."""
    sleeps: list[float] = []
    start = time.time_ns()

    class FakeClockInbox(al.InboxArm):
        def __init__(self, cfg: Any) -> None:
            super().__init__(cfg)
            self._clock = lambda: start + int(sum(sleeps) * 1e9)
            self._sleep = sleeps.append

    monkeypatch.setattr(cycle_mod, "InboxArm", FakeClockInbox)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    config = config_with(
        tmp_path, local_source_dir, INBOX_SOURCE.format(path=inbox).replace("quiescence_s = 0", "")
    )
    page_rel = "mirror/inbox/export.md"

    def export(text: str) -> None:
        (inbox / "export.md.tmp").write_text(text, encoding="utf-8")
        (inbox / "export.md.tmp").rename(inbox / "export.md")
        nonlocal start
        start = time.time_ns()  # every export restarts the fake clock at the real one
        sleeps.clear()

    export("# Export\n\nfirst version\n")
    background = run(config, only=["inbox"])  # an explicit mode (launchd) never waits
    assert sleeps == []
    assert not next(s for s in background.sources if s.source_id == "inbox").enumeration_complete
    assert not (config.docs_repo / page_rel).exists()

    report = run(config, only=["inbox"], mode=None)
    assert report.exit_code == 0, report
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 60
    assert next(s for s in report.sources if s.source_id == "inbox").enumeration_complete
    assert "first version" in page(config.docs_repo, page_rel)[1]

    export("# Export\n\nsecond version\n")  # the re-export: same name, a new inode
    report = run(config, only=["inbox"], mode=None)
    assert report.exit_code == 0, report
    assert len(sleeps) == 1
    assert "second version" in page(config.docs_repo, page_rel)[1]


def test_one_interactive_sync_waits_at_most_60s_over_every_inbox(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field N4 review: the settle wait is bounded once per sync, not once per inbox, whatever quiescence_s
    is, so an agent's ``agentsync sync`` stays well inside its command timeout."""
    sleeps: list[float] = []
    start = time.time_ns()
    offset = 570 * 1_000_000_000  # files written now look 570 s old: 30 s left of a 600 s window

    class FakeClockInbox(al.InboxArm):
        def __init__(self, cfg: Any) -> None:
            super().__init__(cfg)
            self._clock = lambda: start + offset + int(sum(sleeps) * 1e9)
            self._sleep = sleeps.append

    monkeypatch.setattr(cycle_mod, "InboxArm", FakeClockInbox)
    sources = ""
    for sid in ("inbox-a", "inbox-b"):
        inbox = tmp_path / sid
        inbox.mkdir()
        (inbox / "older.md").write_text(f"# Older\n\n{sid}\n", encoding="utf-8")
        (inbox / "fresh.md").write_text("# Fresh\n\nstill being written\n", encoding="utf-8")
        os.utime(inbox / "fresh.md", ns=(start + offset, start + offset))  # changed just now
        sources += INBOX_SOURCE.format(path=inbox).replace('"inbox"\n', f'"{sid}"\n', 1)
    sources = sources.replace("quiescence_s = 0", "quiescence_s = 600")
    config = config_with(tmp_path, local_source_dir, sources)
    report = run(config, only=["inbox-a", "inbox-b"], mode=None)
    assert report.exit_code == 0, report
    assert 0 < sum(sleeps) <= 60, sleeps
    for sid in ("inbox-a", "inbox-b"):
        assert sid in page(config.docs_repo, f"mirror/{sid}/older.md")[1]
        assert not (config.docs_repo / "mirror" / sid / "fresh.md").exists()


def test_inbox_drop_matching_a_graph_file_by_name_and_size_is_refused_unread(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentsync.arm_local import InboxArm  # noqa: PLC0415
    from agentsync.model import RowState  # noqa: PLC0415

    notes = b"# Notes\n\nThe purchase order is approved.\n"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "notes (1).md").write_bytes(b"#" * len(notes))  # same size, other bytes: H1 differs
    (inbox / "NOTES - Copy.md").write_bytes(b"x" * len(notes))  # case + Windows copy suffix
    (inbox / "notes.md").write_bytes(notes + b"more")  # same name, other size: not a duplicate
    (inbox / "other.md").write_bytes(b"#" * len(notes))  # same size, other name
    config = config_with(tmp_path, local_source_dir, GRAPH_SOURCE + INBOX_SOURCE.format(path=inbox))
    drive = FakeDrive({"I1": ("notes.md", notes)})
    client = GraphClient(
        FakeTokens(),
        user_agent="NONISV|test|agentsync/0",
        transport=httpx.MockTransport(drive.handler),
        sleep=lambda _s: None,
    )
    fetched: list[str] = []
    real_fetch = InboxArm.fetch

    def spy(self: InboxArm, item: Any, dest: Path, budget: Any) -> Any:
        fetched.append(item.rel_path)
        return real_fetch(self, item, dest, budget)

    monkeypatch.setattr(InboxArm, "fetch", spy)
    try:
        report = run(config, client=client, only=["drive", "inbox"])
    finally:
        client.close()
    assert report.exit_code == 0, report
    assert sorted(fetched) == ["notes.md", "other.md"]  # the two duplicates were never read
    with Manifest(config.state_paths.db) as m:
        rows = {r.rel_path: r for r in m.iter_items("inbox")}
    want = "duplicate-of drive (mirror/drive/projects/notes.md)"
    for rel in ("notes (1).md", "NOTES - Copy.md"):
        assert (rows[rel].state, rows[rel].state_reason, rows[rel].last_verdict) == (
            RowState.QUARANTINED,
            want,
            Verdict.QUARANTINED,
        )
    assert rows["notes.md"].state is RowState.LIVE and rows["other.md"].state is RowState.LIVE
    assert "duplicate-of drive" in (config.docs_repo / "_sync" / "QUARANTINE.tsv").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------
# the materialise byte budget charges downloads only (L3)
# ---------------------------------------------------------------------------------------------------------


def _mark_online_only(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """Mock SF_DATALESS on these files (a test has no File Provider): the local arm lists them as dataless
    and materialise charges them as a download; every other file stays local."""
    from agentsync import arm_local, materialise  # noqa: PLC0415

    inos = {p.stat().st_ino for p in paths}
    real = materialise.is_dataless

    def fake(st: Any) -> bool:
        return st.st_ino in inos or real(st)

    monkeypatch.setattr(materialise, "is_dataless", fake)
    monkeypatch.setattr(arm_local, "is_dataless", fake)


def test_budget_0_converts_every_local_file_and_defers_only_online_only_ones(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``budget_bytes=0`` (install.sh's first sync): already-local files never consume the byte budget, so
    all of them are converted; only the online-only files wait for a run whose budget allows the download."""
    online = [local_source_dir / "projects" / "sample.pptx", local_source_dir / "projects" / "sample.pdf"]
    _mark_online_only(monkeypatch, *online)
    first = run(sample_config, budget_bytes=0)
    [src] = first.sources
    assert first.exit_code == 0
    assert (src.deferred, src.deferred_online_only, src.materialised_bytes) == (2, 2, 0)
    assert src.converted >= 8, "every local fixture is read and converted"
    assert not any("exceeds the per-cycle budget" in a and "sample.docx" in a for a in src.alarms)
    mirror = sample_config.docs_repo / "mirror" / SID / "projects"
    assert (mirror / "sample.docx.md").is_file() and len(list(mirror.iterdir())) >= 6
    assert not any(mirror.glob("sample.pptx*")) and not any(mirror.glob("sample.pdf*"))
    second = run(sample_config)  # the per-source budget (50MB): the two downloads fit
    [src] = second.sources
    assert (src.converted, src.deferred, src.deferred_online_only) == (2, 0, 0)
    assert src.materialised_bytes == sum(p.stat().st_size for p in online), "only downloads are charged"
    assert any(mirror.glob("sample.pptx*")) and (mirror / "sample.pdf.md").is_file()


# ---------------------------------------------------------------------------------------------------------
# a page too long for any sidecar settles as one quarantined item
# ---------------------------------------------------------------------------------------------------------

APPENDIX_ROWS = 45_000
"""Enough rows to pass ``max_page_bytes``: the page of such a file needs a ``full-text.txt`` sidecar."""
_EXPORT = "Fabrikam Totals By Quarter - FY26 Forecast - All Regions - Final Export From The Data Warehouse v2"
CRAMPED = f"Contoso Travel Forms/Charlie Photo Shoots/{_EXPORT} - Appendix With Every Row.txt"
"""A made-up path whose page is over 183 characters: no ``.files/<8 hex>.txt`` fits beside it."""
MOVES = {
    # what is renamed: (a path with room for the sidecar, one without)
    "file": ("Contoso Travel Forms/appendix.txt", CRAMPED),
    "folder": (
        f"Forms/{_EXPORT}.txt",
        f"Contoso Travel Forms And Photo Shoots - Kept For Next Year/{_EXPORT}.txt",
    ),
}


def _move(root: Path, old: str, new: str, *, folder: bool) -> None:
    """Rename the file at ``old`` to ``new``, or the folder above it. A file's own rename moves its ctime,
    so the next pass reads it again; under a renamed folder its stat holds and nothing is read."""
    src, dst = root / old, root / new
    if folder:
        src, dst = src.parent, dst.parent
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


def _capped_at(root: Path, rel: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("every row of the appendix\n" * APPENDIX_ROWS, encoding="utf-8")


def _appendix_rows(sidecar: Path) -> int:
    """How many of the file's rows ``sidecar`` holds (a count, so a failure prints no megabyte diff)."""
    return sidecar.read_bytes().count(b"every row of the appendix\n")


def _path_stub(config: Config, rel: str) -> bool:
    """True when ``rel`` is settled as the stub for a page with no room for its sidecar, and nothing else is
    beside that page."""
    repo, at = config.docs_repo, slug.mirror_rel_path(SID, rel)
    fm = page(repo, at)[0]
    row = _file_rows(config)[rel]
    return (
        (fm["status"], fm["source_path"]) == ("unreadable", rel)
        and "path too long" in fm["reason"]
        and not (repo / at).with_suffix(".files").exists()
        and (row.state, row.last_verdict) == (RowState.QUARANTINED, Verdict.QUARANTINED)
        and "path too long" in (row.state_reason or "")
    )


def test_a_capped_file_whose_page_leaves_no_room_for_a_sidecar_is_one_quarantined_item(
    sample_config: Config, local_source_dir: Path
) -> None:
    """The item gets a stub and is settled; every other file is converted and the commit lands."""
    rel = CRAMPED
    page = slug.mirror_rel_path(SID, rel)
    assert len(page) > 183
    _capped_at(local_source_dir, rel)
    first = run(sample_config)
    [src] = first.sources
    assert first.exit_code == 0 and first.commit_sha is not None, first
    assert src.errors == () and src.counts.get(Verdict.QUARANTINED) == 1
    assert not [f for f in first.lint_findings if f.code == "PATH"]
    repo = sample_config.docs_repo
    assert (repo / "mirror" / SID / "projects" / "sample.docx.md").is_file()
    assert "status: unreadable" in (repo / page).read_text(encoding="utf-8")
    assert not (repo / page).with_suffix(".files").exists()
    assert max(len(p.relative_to(repo).as_posix()) for p in (repo / "mirror").rglob("*")) <= 200
    with Manifest(sample_config.state_paths.db) as m:
        rows = {r.rel_path: r for r in m.iter_items(SID)}
    assert (rows[rel].state, rows[rel].last_verdict) == (RowState.QUARANTINED, Verdict.QUARANTINED)
    assert "path too long" in (rows[rel].state_reason or "")
    assert all(r.state is RowState.LIVE for name, r in rows.items() if name.startswith("projects/sample."))
    second = run(sample_config)  # settled: not read again, nothing to commit
    [src] = second.sources
    assert (second.exit_code, second.commit_sha, src.converted, src.errors) == (0, None, 0, ())


@pytest.mark.parametrize("renamed", ["file", "folder"])
def test_a_rename_that_leaves_no_room_for_the_sidecar_settles_as_the_stub_at_the_new_path(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch, renamed: str
) -> None:
    """The page cannot follow the file, because its sidecar has no name that fits there. The item becomes
    the stub a first conversion at that path gets: at the new path, nothing left at the old one, settled.
    A renamed folder needs no read for that."""
    roomy, cramped = MOVES[renamed]
    old, new = slug.mirror_rel_path(SID, roomy), slug.mirror_rel_path(SID, cramped)
    assert len(old) <= 183 < len(new)
    _capped_at(local_source_dir, roomy)
    assert run(sample_config).exit_code == 0
    repo = sample_config.docs_repo
    side = repo / sidecar_rel(old, "full-text.txt")
    assert page(repo, old)[0]["status"] == "current" and _appendix_rows(side) == APPENDIX_ROWS
    _move(local_source_dir, roomy, cramped, folder=renamed == "folder")
    fetched = _fetches(monkeypatch)
    second = run(sample_config)
    [src] = second.sources
    assert second.exit_code == 0 and second.commit_sha is not None and src.errors == ()
    assert fetched == ([cramped] if renamed == "file" else [])
    assert _path_stub(sample_config, cramped)
    assert not (repo / old).exists() and not side.parent.exists()
    assert not [f for f in second.lint_findings if f.code == "PATH"]
    third = run(sample_config)  # settled: not read again, nothing to commit
    assert (third.exit_code, third.commit_sha, third.sources[0].converted) == (0, None, 0)
    assert len(fetched) == (1 if renamed == "file" else 0) and porcelain(repo) == ""


@pytest.mark.parametrize("renamed", ["file", "folder"])
def test_shortening_the_path_of_a_stubbed_capped_file_publishes_it(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch, renamed: str
) -> None:
    """The stub says to shorten a folder or file name, and doing so clears it although the bytes did not
    change. A renamed file is read in the same pass; a file under a renamed folder is queued by that pass
    and read by the next one."""
    roomy, cramped = MOVES[renamed]
    _capped_at(local_source_dir, cramped)
    assert run(sample_config).exit_code == 0 and _path_stub(sample_config, cramped)
    _move(local_source_dir, cramped, roomy, folder=renamed == "folder")
    fetched = _fetches(monkeypatch)
    report = run(sample_config)
    if renamed == "folder":
        assert report.exit_code == 0 and fetched == []
        assert _file_rows(sample_config)[roomy].last_verdict is Verdict.MAYBE_CHANGED
        report = run(sample_config)
    assert report.exit_code == 0 and report.commit_sha is not None and fetched == [roomy]
    repo, new = sample_config.docs_repo, slug.mirror_rel_path(SID, roomy)
    fm = page(repo, new)[0]
    assert (fm["status"], fm["source_path"]) == ("current", roomy)
    assert _appendix_rows(repo / sidecar_rel(new, "full-text.txt")) == APPENDIX_ROWS
    assert not (repo / slug.mirror_rel_path(SID, cramped)).exists()
    row = _file_rows(sample_config)[roomy]
    assert (row.state, row.state_reason, row.last_verdict) == (RowState.LIVE, None, Verdict.UNCHANGED)
    settled = run(sample_config)
    assert (settled.exit_code, settled.commit_sha, fetched) == (0, None, [roomy])


def test_renaming_a_stubbed_drive_file_to_a_shorter_name_downloads_and_publishes_it(
    drive_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drive rename is METADATA_ONLY (the hash holds), so nothing is downloaded for it. The stub of a page
    with no room for its sidecar is the exception: the rename queues one download, and the next pass
    publishes the content. A later change that is not a rename downloads nothing."""
    config, drive, client = drive_env
    data = b"every row of the appendix\n" * APPENDIX_ROWS
    long_name = f"{_EXPORT} - Appendix With Every Row And Every Column Of Every Sheet.txt"
    drive.files["I1"] = (long_name, data)
    stub = slug.mirror_rel_path("drive", f"Projects/{long_name}")
    assert len(stub) > 183
    assert run(config, client=client, only=["drive"]).exit_code == 0
    repo = config.docs_repo
    assert "path too long" in page(repo, stub)[0]["reason"]
    fetched = _fetches(monkeypatch, DriveArm)
    drive.version["I1"] += 1  # a metadata change that is not a rename: the stub stays, nothing is read
    assert run(config, client=client, only=["drive"]).exit_code == 0 and fetched == []
    assert page(repo, stub)[0]["status"] == "unreadable"
    drive.version["I1"] += 1
    drive.files["I1"] = ("appendix.txt", data)  # renamed: same bytes, same quickXorHash
    assert run(config, client=client, only=["drive"]).exit_code == 0 and fetched == []
    [row] = _file_rows(config, "drive").values()
    assert (row.rel_path, row.last_verdict) == ("Projects/appendix.txt", Verdict.MAYBE_CHANGED)
    report = run(config, client=client, only=["drive"])
    assert report.exit_code == 0 and fetched == ["Projects/appendix.txt"]
    new = "mirror/drive/projects/appendix.txt.md"
    assert page(repo, new)[0]["status"] == "current" and not (repo / stub).exists()
    assert _appendix_rows(repo / sidecar_rel(new, "full-text.txt")) == APPENDIX_ROWS
    [row] = _file_rows(config, "drive").values()
    assert (row.state, row.state_reason, row.last_verdict) == (RowState.LIVE, None, Verdict.UNCHANGED)


# ---------------------------------------------------------------------------------------------------------
# scope: a row the config no longer covers is never work, and is retired on an incomplete walk too
# ---------------------------------------------------------------------------------------------------------

KICKOFF = "projects/acme/Kickoff Notes.docx"


def _incomplete(config: Config, *exclude: str) -> Config:
    """``config`` with ``exclude`` added to its one source and a sentinel that is not there, so every walk
    is incomplete (as on a Mac with one folder the agent cannot list)."""
    [src] = config.sources
    narrowed = dataclasses.replace(src, exclude=(*src.exclude, *exclude), sentinel="no-such-sentinel")
    return dataclasses.replace(config, sources=(narrowed,))


def _fetches(monkeypatch: pytest.MonkeyPatch, arm: type[Any] = LocalArm) -> list[str]:
    """The rel_path of every file the cycle reads through ``arm.fetch`` from now on."""
    fetched: list[str] = []
    real = arm.fetch

    def spy(self: Any, item: Any, dest: Path, budget: Any) -> Any:
        fetched.append(item.rel_path)
        return real(self, item, dest, budget)

    monkeypatch.setattr(arm, "fetch", spy)
    return fetched


def _file_rows(config: Config, sid: str = SID) -> dict[str, Any]:
    with Manifest(config.state_paths.db) as m:
        return {r.rel_path: r for r in m.iter_items(sid) if not r.is_dir}


@pytest.mark.parametrize(
    ("queued", "exclude"),
    [
        ("projects/sample.pptx", "sample.pptx"),  # a file glob
        (KICKOFF, "acme"),  # a bare folder name: no file glob matches, only the walk's folder rule
        (KICKOFF, "projects/acme"),  # an anchored folder, no trailing slash
        (KICKOFF, "acme/"),
    ],
)
def test_a_queued_row_excluded_later_is_retired_unread_when_the_walk_is_incomplete(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch, queued: str, exclude: str
) -> None:
    """An online-only file deferred by one run and excluded before the next is never fetched or converted.
    An incomplete walk prunes no row, so the queue applies the walk's own scope and the row is retired: no
    purge, and the loop stops counting it. Rows still in scope are untouched: a queued one is worked, and
    one the incomplete walk did not list stays unknown, never gone."""
    kept, absent = "projects/sample.pdf", "projects/sample.md"
    _mark_online_only(monkeypatch, local_source_dir / queued, local_source_dir / kept)
    first = run(sample_config, budget_bytes=0)
    assert first.sources[0].deferred_online_only == 2
    config = _incomplete(sample_config, exclude)
    (local_source_dir / absent).unlink()  # in scope, and not there for the next walk to list
    fetched = _fetches(monkeypatch)
    second = run(config)
    [rep] = second.sources
    assert second.exit_code == 0 and not rep.enumeration_complete and not rep.breaker_tripped
    assert fetched == [kept] and (rep.converted, rep.deferred) == (1, 0)
    assert rep.materialised_bytes == (local_source_dir / kept).stat().st_size
    assert any("1 file(s) now outside it retired" in a for a in rep.alarms), rep.alarms
    rows = _file_rows(config)
    assert (rows[queued].state, rows[queued].state_reason) == (RowState.TOMBSTONE, "retired:scope-change")
    assert [rel for rel, r in rows.items() if r.state is RowState.TOMBSTONE] == [queued]
    repo = config.docs_repo
    assert page(repo, slug.mirror_rel_path(SID, absent))[0]["status"] == "current"
    assert not (repo / slug.mirror_rel_path(SID, queued)).exists()
    assert governance.pending_purges(config.state_paths.root) == []
    step = loop.next_step(config)
    assert step.rule != 3 and not any("online-only" in line for line in step.lines()), step.lines()
    third = run(config)  # settled: nothing is read, nothing is retired twice, the absent file is still held
    assert fetched == [kept] and not any("retired" in a for a in third.sources[0].alarms)
    assert _file_rows(config)[absent].state is RowState.LIVE


@pytest.mark.parametrize("requeued", [False, True], ids=["settled", "queued-again"])
def test_a_published_file_excluded_later_is_retired_on_an_incomplete_walk_and_comes_back(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch, requeued: bool
) -> None:
    """The page of a file the config no longer covers is retired by the next pass, complete or not: marked
    retired (never deleted upstream), no purge queued, the breaker not involved. Queued again or not, the
    file is not read and the loop does not wait for it. Taking the exclude away brings the page back."""
    assert run(sample_config).exit_code == 0
    repo, rel = sample_config.docs_repo, slug.mirror_rel_path(SID, KICKOFF)
    assert page(repo, rel)[0]["status"] == "current"
    if requeued:  # on this Mac and waiting for a re-read, as after a [policy] change
        stable_id = _file_rows(sample_config)[KICKOFF].stable_id
        with Manifest(sample_config.state_paths.db) as m:
            m.set_verdict(SID, stable_id, Verdict.MAYBE_CHANGED)
    excluded = _incomplete(sample_config, "acme")
    fetched = _fetches(monkeypatch)
    second = run(excluded)
    [rep] = second.sources
    assert second.exit_code == 0 and not rep.enumeration_complete and not rep.breaker_tripped
    assert fetched == [] and [(c.op.value, c.path) for c in second.changes] == [("D", rel)]
    assert page(repo, rel)[0]["reason"] == "retired:scope-change"
    assert "[RETIRED]" in (repo / rel).read_text(encoding="utf-8")
    assert governance.pending_purges(excluded.state_paths.root) == []
    assert loop.next_step(excluded).rule != 3
    back = run(_incomplete(sample_config))  # the exclude is gone; the walk is still incomplete
    assert back.exit_code == 0 and fetched == [KICKOFF]
    assert page(repo, rel)[0]["status"] == "current"


def _repointed(config: Config, root: Path, name: str) -> Config:
    """``config`` with its one source pointed at ``root``, one folder above the tree it mirrors (``name``),
    and include and the sentinel re-anchored to keep the same files: one edit of sources.toml."""
    [src] = config.sources
    moved = dataclasses.replace(src, path=root, include=(f"{name}/**",), sentinel=f"{name}/{src.sentinel}")
    return dataclasses.replace(config, sources=(moved,))


def test_a_source_pointed_at_a_new_root_retires_no_row_it_has_not_listed_there(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored path is relative to the root it was listed under. When ``path`` moves up a folder and
    include is re-anchored in the same edit, every stored path fails the new scope, and that says nothing
    about the files. While the new root cannot be listed (a typo, a volume not mounted yet) no row is
    retired; once it can, the pages move in place and nothing is read again."""
    assert run(sample_config).exit_code == 0
    before = {rel: r.state for rel, r in _file_rows(sample_config).items()}
    assert len(before) > 5 and set(before.values()) == {RowState.LIVE}
    parent, name = local_source_dir.parent, local_source_dir.name
    fetched = _fetches(monkeypatch)
    for _pass in range(2):  # the first pass under the new root, and one after it
        absent = run(_repointed(sample_config, parent / "not-mounted-yet", name))
        [rep] = absent.sources
        assert absent.exit_code == 0 and not rep.enumeration_complete
        assert not any("retired" in a for a in rep.alarms), rep.alarms
        assert {rel: r.state for rel, r in _file_rows(sample_config).items()} == before
    there = _repointed(sample_config, parent, name)
    back = run(there)
    assert back.exit_code == 0 and back.sources[0].enumeration_complete and fetched == []
    rows = _file_rows(there)
    assert {rel: r.state for rel, r in rows.items()} == {f"{name}/{rel}": RowState.LIVE for rel in before}
    repo = there.docs_repo
    assert page(repo, slug.mirror_rel_path(SID, f"{name}/{KICKOFF}"))[0]["status"] == "current"
    assert not (repo / slug.mirror_rel_path(SID, KICKOFF)).exists()
    # Under the root it has now, an exclude is judged again on an incomplete pass.
    narrowed = _incomplete(there, "acme")
    report = run(narrowed)
    assert report.exit_code == 0 and fetched == [] and not report.sources[0].enumeration_complete
    rows = _file_rows(narrowed)
    assert [rel for rel, r in rows.items() if r.state is RowState.TOMBSTONE] == [f"{name}/{KICKOFF}"]
    assert rows[f"{name}/{KICKOFF}"].state_reason == "retired:scope-change"


def test_a_row_listed_under_the_old_root_only_is_left_to_a_complete_pass(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``path`` moves and the same edit excludes a folder. The rows under that folder are never listed under
    the new root, so their stored paths stay relative to the old one and an incomplete pass cannot judge
    them. The first complete pass retires them, as it does after any scope change."""
    assert run(sample_config).exit_code == 0
    parent, name = local_source_dir.parent, local_source_dir.name
    [src] = _repointed(sample_config, parent, name).sources
    moved = dataclasses.replace(src, exclude=(*src.exclude, "acme"))
    stopped = dataclasses.replace(sample_config, sources=(dataclasses.replace(moved, sentinel="none"),))
    fetched = _fetches(monkeypatch)
    for _pass in range(2):
        report = run(stopped)
        assert report.exit_code == 0 and not report.sources[0].enumeration_complete
        assert not any("retired" in a for a in report.sources[0].alarms)
        assert _file_rows(stopped)[KICKOFF].state is RowState.LIVE  # still under its old-root path
    complete = run(dataclasses.replace(sample_config, sources=(moved,)))
    assert complete.exit_code == 0 and complete.sources[0].enumeration_complete and fetched == []
    row = _file_rows(sample_config)[KICKOFF]
    assert (row.state, row.state_reason) == (RowState.TOMBSTONE, "retired:scope-change")
    assert governance.pending_purges(sample_config.state_paths.root) == []


@pytest.mark.parametrize("edited_in_that_run", [False, True])
def test_a_manifest_with_no_recorded_root_trusts_its_paths_unless_that_run_changed_the_scope(
    sample_config: Config, edited_in_that_run: bool
) -> None:
    """A manifest written before the root was recorded has rows under the root the source has: an exclude
    added later retires them on an incomplete pass. If sources.toml changed in the very run that makes the
    first record, ``path`` may have moved in that edit, and the rows it has not listed since are left to a
    complete pass."""
    assert run(sample_config).exit_code == 0
    with Manifest(sample_config.state_paths.db) as m:
        m._db.execute("DELETE FROM meta WHERE key = ?", (cycle_mod._SCOPE_ROOT_META + SID,))
    if not edited_in_that_run:
        assert run(_incomplete(sample_config)).exit_code == 0  # the first record, with nothing changed
    excluded = _incomplete(sample_config, "acme")
    assert run(excluded).exit_code == 0
    row = _file_rows(excluded)[KICKOFF]
    assert (row.state is RowState.TOMBSTONE) is (not edited_in_that_run), row.state
    assert run(excluded).exit_code == 0
    assert _file_rows(excluded)[KICKOFF].state is row.state  # the next incomplete pass decides the same


def test_an_inbox_file_excluded_later_is_retired_on_an_incomplete_walk(
    tmp_path: Path, local_source_dir: Path
) -> None:
    inbox = tmp_path / "inbox"
    (inbox / "old").mkdir(parents=True)
    (inbox / "keep.md").write_text("# Keep\n\nstays\n", encoding="utf-8")
    (inbox / "old" / "drop.md").write_text("# Drop\n\nleaves\n", encoding="utf-8")
    table = INBOX_SOURCE.format(path=inbox)
    assert run(config_with(tmp_path, local_source_dir, table), only=["inbox"]).exit_code == 0
    narrowed = config_with(
        tmp_path, local_source_dir, table + 'exclude = ["old"]\nsentinel = "no-such-sentinel"\n'
    )
    report = run(narrowed, only=["inbox"])
    rep = next(s for s in report.sources if s.source_id == "inbox")
    assert report.exit_code == 0 and not rep.enumeration_complete
    repo = narrowed.docs_repo
    assert page(repo, "mirror/inbox/old/drop.md")[0]["reason"] == "retired:scope-change"
    assert page(repo, "mirror/inbox/keep.md")[0]["status"] == "current"
    assert governance.pending_purges(narrowed.state_paths.root) == []


def test_a_queued_drive_file_excluded_later_is_not_downloaded_before_the_listing_reaches_it(
    drive_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drive arm tombstones a known file the new globs exclude when its listing reaches it. Until then
    the queue does not download it, and the row is left for the arm to settle."""
    config, _drive, client = drive_env
    first = run(config, client=client, only=["drive"], budget_bytes=0)
    assert next(s for s in first.sources if s.source_id == "drive").deferred == 1
    sources = tuple(
        dataclasses.replace(s, exclude=(*s.exclude, "notes.md")) if s.id == "drive" else s
        for s in config.sources
    )
    narrowed = dataclasses.replace(config, sources=sources)

    def interrupted(self: DriveArm, cursor: str | None, *, full: bool) -> ScanResult:
        return ScanResult(
            source_id=self.source_id,
            pass_kind=PassKind.FULL,
            items=(),
            new_cursor=None,
            enumeration_complete=False,
        )

    monkeypatch.setattr(DriveArm, "scan", interrupted)
    fetched = _fetches(monkeypatch, DriveArm)
    second = run(narrowed, client=client, only=["drive"])
    assert second.exit_code == 0 and fetched == []
    assert not (narrowed.docs_repo / "mirror" / "drive" / "projects" / "notes.md").exists()
    [row] = _file_rows(narrowed, "drive").values()
    assert (row.state, row.last_verdict) == (RowState.LIVE, Verdict.DEFERRED)


def test_an_arm_without_a_path_scope_has_every_queued_row_worked(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mail and Teams arms have no ``in_scope``: include/exclude are not theirs to apply, so the queue
    filters nothing for them and no row of theirs is retired for its path."""

    class NoScope:
        def __init__(self, inner: Any) -> None:
            self.source_id, self.kind, self._inner = inner.source_id, inner.kind, inner

        def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
            return self._inner.scan(cursor, full=full)  # type: ignore[no-any-return]

        def fetch(self, item: Any, dest_dir: Path, budget: Any) -> Any:
            return self._inner.fetch(item, dest_dir, budget)

    deck = "projects/sample.pptx"
    _mark_online_only(monkeypatch, local_source_dir / deck)
    assert run(sample_config, budget_bytes=0).sources[0].deferred_online_only == 1
    real = cycle_mod.build_arms
    monkeypatch.setattr(
        cycle_mod, "build_arms", lambda *a, **k: {sid: NoScope(arm) for sid, arm in real(*a, **k).items()}
    )
    fetched = _fetches(monkeypatch)
    report = run(_incomplete(sample_config, "sample.pptx"))
    assert report.exit_code == 0 and fetched == [deck]
    assert _file_rows(sample_config)[deck].state is not RowState.TOMBSTONE


# ---------------------------------------------------------------------------------------------------------
# [policy] re-screen backlog: a finished re-screen stays finished
# ---------------------------------------------------------------------------------------------------------


def test_a_finished_policy_rescreen_reports_no_backlog_for_a_file_that_waits_later(
    tmp_path: Path, local_source_dir: Path, fixture_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished re-screen stores "" as its marker. A label-capable file that waits afterwards for another
    reason (online-only, no download budget) is not re-screen backlog, and STATE.md does not say it is."""
    assert run(config_with(tmp_path, local_source_dir)).exit_code == 0
    policy = '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n'
    config = config_with(tmp_path, local_source_dir, policy)
    state_md = config.docs_repo / "_sync" / "STATE.md"
    assert run(config).exit_code == 0  # the changed policy re-screens every label-capable file in this run
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._RESCREEN_META) == ""
    assert "## Content policy" not in state_md.read_text(encoding="utf-8")
    late = local_source_dir / "projects" / "late.pdf"
    shutil.copy2(fixture_files["sample.pdf"], late)
    _mark_online_only(monkeypatch, late)
    report = run(config, budget_bytes=0)
    assert report.exit_code == 0 and report.sources[0].deferred_online_only == 1
    assert "## Content policy" not in state_md.read_text(encoding="utf-8")
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._RESCREEN_META) == ""


# ---------------------------------------------------------------------------------------------------------
# on-device OCR: the cycle looks for the engine, reads images on this Mac, within a time budget
# ---------------------------------------------------------------------------------------------------------

SITE_PLAN = "projects/Contoso Site Plan.png"


def _use_ocr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kw: Any) -> Any:
    """Every cycle from now on finds this fake OCR engine, as after an install that built the helper."""
    engine = fake_engine(tmp_path / "ocr-bin", **kw)
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, _cache_dir: engine)
    return engine


def _image_page(config: Config, rel: str, sid: str = SID) -> tuple[dict[str, Any], str]:
    return page(config.docs_repo, slug.mirror_rel_path(sid, rel))


def test_an_image_on_this_mac_is_read_once_in_the_staging_folder(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _use_ocr(monkeypatch, tmp_path)
    picture(local_source_dir / SITE_PLAN, "Loading dock", "North gate: closed")
    fetched = _fetches(monkeypatch)
    assert run(sample_config).exit_code == 0
    fm, body = _image_page(sample_config, SITE_PLAN)
    assert fm["status"] == "current" and fm["converter"].startswith("image-ocr@2.0.0+ocr-paper-vision-")
    assert fm["source_title"] == "Contoso Site Plan.png", "the unit has no title: the shown name stands in"
    assert fm["summary"] == "Image 800x600 px; OCR: 2 line(s)"
    assert body.startswith("> [UNTRUSTED CONTENT]") and body.rstrip().endswith(
        "[image · 800x600 px · text read by on-device OCR (Apple Vision)]\n\nLoading dock\nNorth gate: closed"
    )
    (call,) = calls(engine.helper)
    staging = sample_config.state_paths.staging.resolve()
    assert Path(call["cwd"]).parent == staging, "the helper works under the cycle's staging folder"
    assert Path(call["args"][-1]).parent == Path(call["cwd"]) and list(staging.iterdir()) == []
    assert SITE_PLAN in fetched
    again = run(sample_config)
    assert again.commit_sha is None and fetched.count(SITE_PLAN) == 1 and len(calls(engine.helper)) == 1


def test_the_cycle_looks_for_the_engine_once_and_never_under_a_dry_run(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked: list[Path] = []
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, cache_dir: looked.append(cache_dir))
    assert run(sample_config, mode=CycleMode.DRY_RUN).exit_code == 0 and looked == []
    assert run(sample_config).exit_code == 0 and looked == [sample_config.cache_dir]


def test_the_cycle_engine_is_what_ocr_engine_finds_and_none_when_ocr_is_off(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    helper = write_fake(ocr._helper_path(sample_config.cache_dir))
    assert cycle_mod._cycle_ocr(sample_config) is None, "AGENTSYNC_OCR=0, the suite's own switch"
    monkeypatch.delenv("AGENTSYNC_OCR")
    found = cycle_mod._cycle_ocr(sample_config)
    assert found is not None and found.helper == helper and found.spent_s == 0.0
    assert found.identity == "ocr-paper-vision-r2-h0.3.0-l1"
    off = dataclasses.replace(sample_config, convert=dataclasses.replace(sample_config.convert, ocr=False))
    assert cycle_mod._cycle_ocr(off) is None, "[convert] ocr = false"


def test_the_cycle_engine_adds_up_the_helper_time_of_reads_that_fail_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = fake_engine(tmp_path / "bin")
    metered = cycle_mod._CycleOcr(plain.helper, name=plain.name, revision=2, helper_version="0.3.0")
    ticks = iter([10.0, 12.5, 20.0, 21.0])
    monkeypatch.setattr(cycle_mod, "_ocr_clock", lambda: next(ticks))
    (frames,) = metered.read([picture(tmp_path / "a.png", "read")], work_dir=tmp_path, budget_s=60)
    assert ocr.text_lines(frames[0]) == ["read"] and metered.spent_s == 2.5
    with pytest.raises(ocr.OcrError):
        metered.read([tmp_path / "gone.png"], work_dir=tmp_path, budget_s=60)
    assert metered.spent_s == 3.5 and metered.identity == plain.identity


def test_the_ocr_budget_defers_the_images_past_it_and_a_cache_hit_costs_nothing(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder of thousands of screenshots is read over many cycles. Each read here 'takes' 100 s, so the
    180 s budget is passed by the second and the rest wait, as files past ``max_files`` do: the loop says to
    sync again."""
    assert run(sample_config).exit_code == 0
    engine = _use_ocr(monkeypatch, tmp_path)
    ticks = itertools.count(0.0, 100.0)
    monkeypatch.setattr(cycle_mod, "_ocr_clock", lambda: next(ticks))
    shots = [picture(local_source_dir / "shots" / f"shot {n}.png", f"screenshot {n}") for n in range(5)]
    pages = [sample_config.docs_repo / slug.mirror_rel_path(SID, f"shots/{p.name}") for p in shots]
    seen: list[tuple[int, int, int]] = []
    for _cycle in range(3):
        [rep] = run(sample_config).sources
        seen.append((rep.converted, rep.deferred, rep.deferred_online_only))
        assert sum(p.is_file() for p in pages) == sum(done for done, _d, _o in seen)
        assert (loop.next_step(sample_config).rule == 3) is (rep.deferred > 0)
    assert seen == [(2, 3, 0), (2, 1, 0), (1, 0, 0)]
    read = [Path(run[0]).name for run in reads(engine.helper)]
    assert sorted(read) == sorted(p.name for p in shots), "every image is read once, none twice"
    assert all(
        page(sample_config.docs_repo, slug.mirror_rel_path(SID, f"shots/{p.name}"))[0]["status"] == "current"
        for p in shots
    )
    # Three more files with bytes already converted: the cache serves them, so no helper time is spent on
    # them and none of them waits, although one read alone would pass the budget.
    monkeypatch.setattr(cycle_mod, "_OCR_BUDGET_S", 50.0)
    for n, shot in enumerate(shots[:3]):
        shutil.copyfile(shot, local_source_dir / "shots" / f"copy {n}.png")
    [rep] = run(sample_config).sources
    assert (rep.converted, rep.deferred) == (3, 0) and len(reads(engine.helper)) == 5


def test_a_scanned_pdf_page_is_read_in_the_staging_folder_and_past_the_ocr_budget_a_pdf_converts_without(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document does not wait as an image does. Each read here 'takes' 100 s, so the second PDF passes
    the 180 s budget and the third is converted as on a Mac without an engine: the page it would have
    there, under that version, which is what tells a later re-read that OCR has not read it. The next
    cycle, with its own OCR time, is that re-read."""
    assert run(sample_config).exit_code == 0
    engine = shade_engine(tmp_path / "ocr-bin", {90 + n: [f"Delivery note {n}"] for n in range(3)})
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, _cache_dir: engine)
    ticks = itertools.count(0.0, 100.0)
    monkeypatch.setattr(cycle_mod, "_ocr_clock", lambda: next(ticks))
    (local_source_dir / "scans").mkdir()
    for n in range(3):
        pages = [(["The cover page has a text layer of its own"], []), ([], [page_picture(90 + n)])]
        build_picture_pdf(local_source_dir / "scans" / f"scan {n}.pdf", pages)
    [rep] = run(sample_config).sources
    assert (rep.converted, rep.deferred, rep.errors) == (3, 0, ())
    read, unread = [], []
    for n in range(3):
        fm, body = page(sample_config.docs_repo, slug.mirror_rel_path(SID, f"scans/scan {n}.pdf"))
        assert fm["status"] == "current" and body.startswith("> [UNTRUSTED CONTENT]")
        if "+ocr-paper-vision-" in fm["converter"]:
            assert fm["summary"] == "PDF: 2 page(s), 1 read by on-device OCR"
            assert body.rstrip().endswith(f"(Apple Vision)]\n\nDelivery note {n}")
            read.append(n)
        else:
            assert fm["summary"] == "PDF: 2 page(s), 1 without a text layer (scanned; OCR not run)"
            assert body.rstrip().endswith("[scanned page: no text layer]") and "Delivery" not in body
            unread.append(n)
    assert (len(read), len(unread)) == (2, 1), "two reads pass the budget; the third file does not wait"
    staging = sample_config.state_paths.staging.resolve()
    runs = calls(engine.helper)
    assert len(runs) == 2 and all(Path(c["cwd"]).parent.parent == staging for c in runs)
    assert all(Path(c["cwd"]).name.startswith(".ocr-") for c in runs) and list(staging.iterdir()) == []
    assert loop.next_step(sample_config).rule != 3, "nothing waits on this Mac"
    again = run(sample_config)
    (late,) = unread
    fm, body = page(sample_config.docs_repo, slug.mirror_rel_path(SID, f"scans/scan {late}.pdf"))
    assert again.commit_sha is not None and "+ocr-paper-vision-" in fm["converter"]
    assert body.rstrip().endswith(f"(Apple Vision)]\n\nDelivery note {late}")
    assert again.sources[0].converted == 0, "read again: the same bytes are no new conversion"
    assert len(calls(engine.helper)) == 3 and all(
        Path(c["cwd"]).parent.parent == staging for c in calls(engine.helper)
    )
    settled = run(sample_config)
    assert settled.commit_sha is None and len(calls(engine.helper)) == 3, "no file is read a third time"
    assert loop.next_step(sample_config).rule != 3


def test_a_deck_and_a_word_document_are_read_like_a_pdf_and_an_rtf_file_is_as_it_was(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pictures of a deck and of a Word document go through the same registry as a PDF's: read under
    the staging folder, and past the OCR budget converted as on a Mac without an engine.  An .rtf file has
    one converter with or without an engine, so nothing about its page changes."""
    assert run(sample_config).exit_code == 0
    engine = _use_ocr(monkeypatch, tmp_path)
    folder, markdown = local_source_dir / "launch", "# Minutes\n\n![shot](shot.png)\n"
    folder.mkdir()
    (tmp_path / "shot.png").write_bytes(text_png("Orders by month"))
    for name in ("minutes.docx", "minutes.rtf"):
        pandoc_build(markdown, "markdown", folder / name, cwd=tmp_path)
    prs = Presentation()
    shapes = prs.slides.add_slide(prs.slide_layouts[6]).shapes
    shapes.add_picture(io.BytesIO(text_png("Units by region")), Inches(1), Inches(1))
    prs.save(str(folder / "review.pptx"))
    [rep] = run(sample_config).sources
    assert (rep.converted, rep.deferred, rep.errors) == (3, 0, ())

    def published(name: str) -> tuple[dict[str, Any], str]:
        fm, body = page(sample_config.docs_repo, slug.mirror_rel_path(SID, f"launch/{name}"))
        assert fm["status"] == "current" and body.startswith("> [UNTRUSTED CONTENT]")
        return fm, body.rstrip()

    fm, body = published("minutes.docx")
    assert fm["converter"].startswith("pandoc-gfm@1.0.0+pandoc-") and "+ocr-paper-vision-" in fm["converter"]
    assert fm["summary"] == "Word document; text of 1 picture(s) read by on-device OCR; headings: Minutes"
    assert body.endswith("read by on-device OCR (Apple Vision):]\n\nOrders by month")
    fm, body = published("review.pptx")
    assert fm["converter"].startswith("pptx-python-pptx@1.0.0+") and "+ocr-paper-vision-" in fm["converter"]
    assert body.endswith("read by on-device OCR (Apple Vision):]\nUnits by region")
    rtf, rtf_body = published("minutes.rtf")
    assert rtf["converter"].startswith("pandoc-gfm@1.0.0+pandoc-") and "ocr" not in rtf["converter"]
    assert "Orders by month" not in rtf_body
    staging = sample_config.state_paths.staging.resolve()
    runs = calls(engine.helper)
    assert len(runs) == 2 and all(Path(c["cwd"]).parent.parent == staging for c in runs)
    assert all(Path(c["cwd"]).name.startswith(".ocr-") for c in runs) and list(staging.iterdir()) == []
    assert run(sample_config).commit_sha is None and len(calls(engine.helper)) == 2
    # The cycle's OCR time is used up: a new deck and a new document do not wait, and are not read.
    monkeypatch.setattr(cycle_mod, "_OCR_BUDGET_S", 0.0)
    (tmp_path / "shot.png").write_bytes(text_png("Returns by month"))
    pandoc_build(markdown, "markdown", folder / "late.docx", cwd=tmp_path)
    pandoc_build(markdown, "markdown", folder / "late.rtf", cwd=tmp_path)
    shapes.add_picture(io.BytesIO(text_png("Returns by region")), Inches(1), Inches(3))
    prs.save(str(folder / "late.pptx"))
    [rep] = run(sample_config).sources
    assert (rep.converted, rep.deferred, rep.errors) == (3, 0, ()) and len(calls(engine.helper)) == 2
    for name in ("late.docx", "late.pptx"):
        fm, body = published(name)
        assert "ocr" not in fm["converter"] and "OCR" not in fm["summary"] + body, name
    assert published("late.rtf")[0]["converter"] == rtf["converter"]
    assert loop.next_step(sample_config).rule != 3, "nothing waits on this Mac"


def test_an_online_only_image_is_not_downloaded_for_ocr(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No image was ever downloaded, and OCR does not start it: while reading an image means a download it
    keeps the stub it has without an engine, whatever the byte budget and even when ``materialise`` names
    it. An image already on this Mac is read."""
    engine = _use_ocr(monkeypatch, tmp_path)
    local = picture(local_source_dir / SITE_PLAN, "Loading dock")
    photo = picture(local_source_dir / "projects" / "Contoso Offsite Photo.heic", "Welcome")
    rel = "projects/Contoso Offsite Photo.heic"
    online = {photo.stat().st_ino}
    real = materialise.is_dataless

    def is_dataless(st: Any) -> bool:
        return st.st_ino in online or real(st)

    monkeypatch.setattr(materialise, "is_dataless", is_dataless)
    monkeypatch.setattr(al, "is_dataless", is_dataless)
    fetched = _fetches(monkeypatch)
    [rep] = run(sample_config).sources
    assert SITE_PLAN in fetched and rel not in fetched
    assert rep.materialised_bytes == 0 and (rep.deferred, rep.deferred_online_only) == (0, 0)
    fm, _body = _image_page(sample_config, rel)
    assert (fm["status"], fm["reason"], fm["converter"]) == ("refused", "no converter for .heic", "none@0")
    assert _image_page(sample_config, SITE_PLAN)[0]["status"] == "current"
    assert [Path(run[0]).name for run in reads(engine.helper)] == [local.name]
    assert loop.next_step(sample_config).rule != 3, "nothing waits for a download budget"
    run(sample_config)
    run(sample_config, materialise_paths=[photo], budget_bytes=10_000_000)
    assert rel not in fetched and len(reads(engine.helper)) == 1
    assert _image_page(sample_config, rel)[0]["status"] == "refused"
    # The person downloads it (Finder's Download Now). That changes the inode: its flags, so its change
    # time. The walk sees the row moved, and the file, now on this Mac, is read.
    online.clear()
    photo.chmod(0o600)
    assert run(sample_config).exit_code == 0 and fetched.count(rel) == 1
    fm, body = _image_page(sample_config, rel)
    assert fm["status"] == "current" and body.rstrip().endswith("Welcome")


def test_a_graph_image_is_not_downloaded_for_ocr(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _use_ocr(monkeypatch, tmp_path)
    config = config_with(tmp_path, local_source_dir, GRAPH_SOURCE)
    drive = FakeDrive(
        {"I1": ("notes.md", b"# Notes\n"), "I2": ("Contoso Site Plan.png", picture_bytes("Loading dock"))}
    )
    with GraphClient(
        FakeTokens(),
        user_agent="NONISV|test|agentsync/0",
        transport=httpx.MockTransport(drive.handler),
        sleep=lambda _s: None,
    ) as client:
        assert run(config, client=client, only=["drive"]).exit_code == 0
    assert any(entry.endswith("/items/I1/content") for entry in drive.log)
    assert not any("I2" in entry for entry in drive.log), drive.log
    fm, _body = page(config.docs_repo, "mirror/drive/projects/contoso-site-plan.png.md")
    assert (fm["status"], fm["reason"]) == ("refused", "no converter for .png") and reads(engine.helper) == []


LABEL_RULE = '\n[policy]\nexclude_label_ids = ["00000000-0000-4000-8000-00000000c0de"]\n'


def test_under_a_label_rule_no_image_is_read_and_a_page_from_before_it_becomes_a_stub(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An image can carry a sensitivity label nothing here reads, so a label rule fails closed: no image
    converter. The [policy] re-screen covers images, so the page published before the rule does not stay."""
    engine = _use_ocr(monkeypatch, tmp_path)
    picture(local_source_dir / SITE_PLAN, "Loading dock")
    open_config = config_with(tmp_path, local_source_dir)
    assert run(open_config).exit_code == 0
    assert _image_page(open_config, SITE_PLAN)[0]["status"] == "current" and len(reads(engine.helper)) == 1
    labelled = config_with(tmp_path, local_source_dir, LABEL_RULE)
    picture(local_source_dir / "projects" / "Contoso Badge Scan.jpg", "Visitor")
    fetched = _fetches(monkeypatch)
    report = run(labelled)
    assert report.exit_code == 0 and not [rel for rel in fetched if rel.endswith((".png", ".jpg"))]
    for rel, suffix in ((SITE_PLAN, ".png"), ("projects/Contoso Badge Scan.jpg", ".jpg")):
        fm, body = _image_page(labelled, rel)
        assert (fm["status"], fm["reason"]) == ("refused", f"no converter for {suffix}")
        assert "Loading dock" not in body and "Visitor" not in body
    assert len(reads(engine.helper)) == 1, "the helper is not run under a label rule"
    assert governance.pending_purges(labelled.state_paths.root) == [], "no label was read: nothing to purge"
    with Manifest(labelled.state_paths.db) as m:
        assert m.get_meta(cycle_mod._RESCREEN_META) == ""


def test_an_image_the_helper_failed_on_gets_a_stub_with_fixed_wording_and_is_settled_like_any_failure(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cycle treats a failed OCR read as it treats every failed conversion: the next cycle reads the file
    again, finds the same bytes and an intact stub, and settles the row without converting. So an image the
    helper fails on every time costs one read, and its error is reported once; new bytes are converted."""
    failing = fake_engine(tmp_path / "failing", fail=True)
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, _cache_dir: failing)
    plan = picture(local_source_dir / SITE_PLAN, "Loading dock")
    [rep] = run(sample_config).sources
    fm, body = _image_page(sample_config, SITE_PLAN)
    assert (fm["status"], fm["reason"]) == ("unreadable", "conversion failed: on-device OCR failed")
    assert rep.errors == (f"{SITE_PLAN}: conversion failed: on-device OCR failed",)
    assert "the fake helper was told to fail" not in body + str(fm), "what the helper said stays in the log"
    again = run(sample_config)
    assert again.commit_sha is None and again.sources[0].errors == () and len(reads(failing.helper)) == 1
    assert _file_rows(sample_config)[SITE_PLAN].state is RowState.QUARANTINED
    assert loop.next_step(sample_config).rule != 3, "a failed image does not keep the loop syncing"
    healthy = _use_ocr(monkeypatch, tmp_path)
    assert run(sample_config).commit_sha is None and reads(healthy.helper) == [], "settled: not read again"
    picture(plan, "Loading dock", "East gate: open")
    assert run(sample_config).commit_sha is not None and len(reads(healthy.helper)) == 1
    fm, body = _image_page(sample_config, SITE_PLAN)
    assert fm["status"] == "current" and body.rstrip().endswith("Loading dock\nEast gate: open")


def test_an_image_without_text_is_a_settled_stub_outside_the_curation_queue(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page per logo would be curation work for ever. The stub is cached and settled: nothing reads the
    file again, and the curation queue, which lists every current page no topic cites, does not hold it."""
    engine = _use_ocr(monkeypatch, tmp_path)
    logo = "projects/Contoso logo.png"
    picture(local_source_dir / logo)
    picture(local_source_dir / SITE_PLAN, "Loading dock")
    assert run(sample_config).exit_code == 0
    fm, body = _image_page(sample_config, logo)
    assert (fm["status"], fm["reason"]) == ("unreadable", "no text found in the image by on-device OCR")
    assert "[image" not in body
    uncovered = curate.uncovered_mirror_pages(DocsLayout(root=sample_config.docs_repo), [])
    assert slug.mirror_rel_path(SID, SITE_PLAN) in uncovered
    assert slug.mirror_rel_path(SID, logo) not in uncovered
    assert _file_rows(sample_config)[logo].last_verdict is Verdict.QUARANTINED
    fetched = _fetches(monkeypatch)
    assert run(sample_config).commit_sha is None and fetched == [] and len(reads(engine.helper)) == 2
    assert loop.next_step(sample_config).rule != 3


def test_a_credential_read_from_an_image_is_quarantined_like_any_other_text(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secret scan reads every page a cycle writes, and what OCR read is in the page (the summary, which
    is front matter, holds none of it): a screenshot of a key is a ``contains a credential`` stub."""
    _use_ocr(monkeypatch, tmp_path)
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    console = "projects/Contoso console.png"
    picture(local_source_dir / console, "Access keys", f"aws key {key}")
    report = run(sample_config)
    assert report.exit_code == 0 and report.commit_sha is not None
    fm, body = _image_page(sample_config, console)
    assert fm["reason"] == "contains a credential" and key not in body + str(fm)
    assert not committed_blobs_containing(sample_config.docs_repo, key.encode())


# ---------------------------------------------------------------------------------------------------------
# re-read once: a file converted before something its converter has is read again, once (CONTRACTS 16.27)
# ---------------------------------------------------------------------------------------------------------

COMMENTS = "[comments on this page (PDF annotations):]"
REVIEW = "projects/Contoso widget review.pdf"
PLAIN_PDF = "projects/sample.pdf"


def _commented(path: Path, title: str) -> Path:
    """A one-page PDF with one reviewer's note on it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    note = f"/Subtype /Text /Rect [400 700 420 720] /T (Roe, John) /Contents (Check {title})"
    return build_annotated_pdf(path, [([title, "Totals by region"], [note])])


@contextlib.contextmanager
def _before_comments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The PDF emitter from before comments were kept: it says 2.0.0 and writes no comment."""
    with monkeypatch.context() as old:
        old.setattr(pdf_mod, "_EMITTER_VERSION", "2.0.0")
        old.setattr(pdf_mod, "_render_comments", lambda _found: [])
        yield


def _shade_png(path: Path, shade: int) -> Path:
    """A real gray PNG, every pixel ``shade``: an image file the ``shade_engine`` helper tells by it."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    path.parent.mkdir(parents=True, exist_ok=True)
    head = chunk(b"IHDR", struct.pack(">IIBBBBB", 96, 64, 8, 0, 0, 0, 0))
    pixels = chunk(b"IDAT", zlib.compress((b"\x00" + bytes([shade]) * 96) * 64))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + head + pixels + chunk(b"IEND", b""))
    return path


def _reread_record(config: Config, sid: str = SID) -> tuple[bool, list[str]]:
    """(done, tried) of the re-read record the last cycle wrote for ``sid``."""
    with Manifest(config.state_paths.db) as m:
        raw = m.get_meta(cycle_mod._REREAD_META + sid)
    assert raw is not None
    newest = json.loads(raw)[0]
    return newest["done"], newest["tried"]


def _mirror_page(config: Config, rel: str) -> tuple[dict[str, Any], str]:
    return page(config.docs_repo, slug.mirror_rel_path(SID, rel))


def _mirror_bytes(config: Config, *rels: str) -> dict[str, bytes]:
    return {rel: (config.docs_repo / slug.mirror_rel_path(SID, rel)).read_bytes() for rel in rels}


def test_a_pdf_converted_before_comments_were_kept_gains_them_and_is_read_again_once(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PDF mirrored by emitter 2.0.0 gets its reviewers' comments without its bytes changing: the next
    cycle reads it again, once.  A PDF with no comment is read too, and its page stays as it is, byte for
    byte, its 2.0.0 line included.  After that no cycle looks: none of the three queries is run."""
    _commented(local_source_dir / REVIEW, "Contoso widget review")
    fetched = _fetches(monkeypatch)
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
        fm, body = _mirror_page(sample_config, REVIEW)
        assert fm["converter"].startswith("pdf-pypdfium2@2.0.0+") and COMMENTS not in body
        del fetched[:]
        # The floor is above the emitter that runs here: what a re-read wrote would be below it too, so
        # nothing is outdated and no file is read every cycle.
        assert _reread_record(sample_config) == (True, [])
        assert run(sample_config).commit_sha is None and fetched == []
    plain = _mirror_bytes(sample_config, PLAIN_PDF)
    assert run(sample_config, mode=CycleMode.DRY_RUN).exit_code == 0 and fetched == [], (
        "a dry run reads nothing"
    )
    second = run(sample_config)
    [rep] = second.sources
    assert second.commit_sha is not None and sorted(fetched) == [REVIEW, PLAIN_PDF]
    assert (rep.converted, rep.deferred, rep.errors) == (0, 0, ()), "the same bytes are no new conversion"
    assert [c.path for c in second.changes] == [slug.mirror_rel_path(SID, REVIEW)]
    fm, body = _mirror_page(sample_config, REVIEW)
    assert fm["converter"].startswith("pdf-pypdfium2@2.1.0+") and fm["summary"].endswith(
        "1 comment(s) on 1 page(s)"
    )
    assert body.rstrip().endswith(f"{COMMENTS}\n- Note by Roe, John: Check Contoso widget review")
    assert _mirror_bytes(sample_config, PLAIN_PDF) == plain and b"pdf-pypdfium2@2.0.0+" in plain[PLAIN_PDF]
    assert _reread_record(sample_config) == (True, [])
    third = run(sample_config)
    assert third.commit_sha is None and len(fetched) == 2, "no file is read a second time"
    for query in ("produced_by", "reread_candidates", "reread_left"):
        monkeypatch.setattr(Manifest, query, crash)
    assert run(sample_config).commit_sha is None, "nothing is left, and no cycle looks"


def test_files_mirrored_before_there_was_an_engine_are_read_by_it_once(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The files OCR exists for were mirrored before it: an image refused as ``no converter``, a scan
    quarantined as ``no text layer``, a PDF whose scanned page was a marker.  With an engine each is read
    again, once.  A page the engine adds nothing to stays as it is, byte for byte, and so does the stub of a
    scan it reads nothing in."""
    scans = local_source_dir / "scans"
    scans.mkdir()
    note, agreement, blank = (
        f"scans/Contoso {n}.pdf" for n in ("delivery note", "supply agreement", "blank scan")
    )
    cover = ["The cover page has a text layer of its own"]
    _shade_png(local_source_dir / SITE_PLAN, 70)
    build_picture_pdf(local_source_dir / note, [([], [page_picture(90)])])
    build_picture_pdf(local_source_dir / agreement, [(cover, []), ([], [page_picture(91)])])
    build_picture_pdf(local_source_dir / blank, [([], [page_picture(92)])])
    assert run(sample_config).exit_code == 0
    assert _mirror_page(sample_config, SITE_PLAN)[0]["reason"] == "no converter for .png"
    for scan in (note, blank):
        fm, _body = _mirror_page(sample_config, scan)
        assert (fm["status"], fm["reason"]) == ("unreadable", pdf_mod._NO_TEXT)
    fm, body = _mirror_page(sample_config, agreement)
    assert fm["status"] == "current" and "ocr" not in fm["converter"]
    assert body.rstrip().endswith("[scanned page: no text layer]")
    assert run(sample_config).commit_sha is None and _reread_record(sample_config) == (True, [])
    documents = [PLAIN_PDF, "projects/sample.pptx", "projects/sample.docx", KICKOFF]
    before = _mirror_bytes(sample_config, blank, *documents)

    said = {70: ["Loading dock"], 90: ["Delivery note 7"], 91: ["Signed in Rotterdam"]}
    engine = shade_engine(tmp_path / "ocr-bin", said)
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, _cache_dir: engine)
    fetched = _fetches(monkeypatch)
    second = run(sample_config)
    [rep] = second.sources
    assert sorted(fetched) == sorted([SITE_PLAN, note, agreement, blank, *documents])
    assert (rep.converted, rep.deferred, rep.errors) == (1, 0, ()), (
        "the image: its bytes were never read before"
    )
    fm, body = _mirror_page(sample_config, SITE_PLAN)
    assert fm["status"] == "current" and body.rstrip().endswith("(Apple Vision)]\n\nLoading dock")
    fm, body = _mirror_page(sample_config, note)
    assert fm["status"] == "current" and "+ocr-paper-vision-" in fm["converter"]
    assert body.rstrip().endswith("(Apple Vision)]\n\nDelivery note 7")
    fm, body = _mirror_page(sample_config, agreement)
    assert "+ocr-paper-vision-" in fm["converter"] and body.rstrip().endswith("Signed in Rotterdam")
    assert _mirror_bytes(sample_config, blank, *documents) == before
    assert sorted(c.path for c in second.changes) == sorted(
        slug.mirror_rel_path(SID, rel) for rel in (SITE_PLAN, note, agreement)
    )
    rows = _file_rows(sample_config)
    assert [rows[rel].state for rel in (SITE_PLAN, note, blank)] == [
        RowState.LIVE,
        RowState.LIVE,
        RowState.QUARANTINED,
    ]
    assert _reread_record(sample_config) == (True, []) and loop.next_step(sample_config).rule != 3
    runs = len(calls(engine.helper))
    third = run(sample_config)
    assert third.commit_sha is None and len(fetched) == 8 and len(calls(engine.helper)) == runs


def test_under_a_label_rule_a_refused_image_is_not_read_and_without_the_rule_it_is(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An image can carry a label nothing here reads, so under a label rule it has no converter, engine or
    not: the stub it got before there was an engine stays, and the file is not read.  When the rule goes,
    the registry has a converter for it again, and the image is read like any file from before it."""
    picture(local_source_dir / SITE_PLAN, "Loading dock")
    assert run(config_with(tmp_path, local_source_dir)).exit_code == 0
    engine = _use_ocr(monkeypatch, tmp_path)
    labelled = config_with(tmp_path, local_source_dir, LABEL_RULE)
    fetched = _fetches(monkeypatch)
    assert run(labelled).exit_code == 0 and run(labelled).exit_code == 0
    assert SITE_PLAN not in fetched and not any(
        SITE_PLAN.endswith(Path(r[0]).name) for r in reads(engine.helper)
    )
    assert _image_page(labelled, SITE_PLAN)[0]["reason"] == "no converter for .png"
    opened = config_with(tmp_path, local_source_dir)
    assert run(opened).exit_code == 0 and fetched.count(SITE_PLAN) == 1
    fm, body = _image_page(opened, SITE_PLAN)
    assert fm["status"] == "current" and body.rstrip().endswith("Loading dock")
    assert run(opened).commit_sha is None and fetched.count(SITE_PLAN) == 1


def test_a_re_read_that_fails_keeps_the_page_and_the_file_is_not_read_a_third_time(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A converter that breaks while files are read again costs no page: nothing is published, nothing is
    committed, and the rows keep their hashes and their verdict.  Each file is tried once for what this
    install has, so a file that cannot be converted is not read every cycle.  Something new to look for
    starts over."""
    repo = sample_config.docs_repo
    _commented(local_source_dir / REVIEW, "Contoso widget review")
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
    pages = _mirror_bytes(sample_config, REVIEW, PLAIN_PDF)
    rows, head = _file_rows(sample_config), git(repo, "rev-parse", "HEAD")
    fetched = _fetches(monkeypatch)

    def broken(self: Any, src: Path, *, name: str) -> Any:
        raise RuntimeError(f"cannot open /Users/someone/Library/{name}")

    with monkeypatch.context() as fault:
        fault.setattr(pdf_mod.PdfConverter, "convert", broken)
        report = run(sample_config)
    [rep] = report.sources
    assert (report.exit_code, report.commit_sha, rep.errors) == (0, None, ())
    assert rep.alarms == (
        "2 file(s) read again for what their converter has gained could not be converted; their pages are "
        "kept as they were",
    )
    assert _mirror_bytes(sample_config, *pages) == pages
    assert git(repo, "rev-parse", "HEAD") == head and porcelain(repo) == ""
    after = _file_rows(sample_config)
    for rel in pages:
        was, now = rows[rel], after[rel]
        assert (now.state, now.state_reason, now.last_verdict) == (RowState.LIVE, None, Verdict.UNCHANGED)
        assert (now.content_sha256, now.canonical_sha256) == (was.content_sha256, was.canonical_sha256)
    done, tried = _reread_record(sample_config)
    assert done and sorted(tried) == sorted(after[rel].stable_id for rel in pages)
    assert sorted(fetched) == sorted(pages)
    # The converter works again.  The two files were tried for what this install has: they are left alone.
    assert run(sample_config).commit_sha is None and len(fetched) == 2
    assert loop.next_step(sample_config).rule != 3
    # A new floor is something new to look for, so the files are read once more.
    monkeypatch.setattr(pdf_mod.PdfConverter, "outdated_key", "2.1.0<2.1.0, and one thing more")
    again = run(sample_config)
    assert again.commit_sha is not None and len(fetched) == 4
    assert COMMENTS in _mirror_page(sample_config, REVIEW)[1] and _reread_record(sample_config) == (True, [])
    assert run(sample_config).commit_sha is None and len(fetched) == 4


def test_a_re_read_downloads_nothing_reads_nothing_out_of_scope_and_waits_for_materialise(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three PDFs from before comments were kept.  One is online-only now, one is under a folder the config
    has since excluded, one is here.  Only the last is read again, on a walk that does not complete, and not
    by the ``materialise PATH`` run before it.  Once the person downloads the first, it is read too."""
    online_rel, excluded_rel = "projects/Contoso online review.pdf", "archive/Contoso old review.pdf"
    _commented(local_source_dir / REVIEW, "Contoso widget review")
    online = _commented(local_source_dir / online_rel, "Contoso online review")
    _commented(local_source_dir / excluded_rel, "Contoso old review")
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
    with Manifest(sample_config.state_paths.db) as m:
        record = m.get_meta(cycle_mod._REREAD_META + SID)
    inos = {online.stat().st_ino}
    real = materialise.is_dataless

    def is_dataless(st: Any) -> bool:
        return st.st_ino in inos or real(st)

    monkeypatch.setattr(materialise, "is_dataless", is_dataless)
    monkeypatch.setattr(al, "is_dataless", is_dataless)
    config = _incomplete(sample_config, "archive")
    fetched = _fetches(monkeypatch)
    named = run(config, materialise_paths=[local_source_dir / "README.txt"], budget_bytes=10_000_000)
    assert named.exit_code == 0 and fetched == ["README.txt"], "materialise reads the file it names"
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._REREAD_META + SID) == record, "and looks for nothing to read again"
    second = run(config)
    [rep] = second.sources
    assert not rep.enumeration_complete and sorted(fetched[1:]) == [REVIEW, PLAIN_PDF]
    assert (rep.deferred, rep.deferred_online_only, rep.materialised_bytes) == (0, 0, 0)
    assert COMMENTS in _mirror_page(config, REVIEW)[1] and COMMENTS not in _mirror_page(config, online_rel)[1]
    rows = _file_rows(config)
    assert rows[online_rel].state is RowState.DATALESS and rows[online_rel].last_verdict is Verdict.DATALESS
    assert (rows[excluded_rel].state, rows[excluded_rel].state_reason) == (
        RowState.TOMBSTONE,
        "retired:scope-change",
    )
    step = loop.next_step(config)
    assert step.rule != 3 and not any("online-only" in line for line in step.lines()), step.lines()
    assert _reread_record(config) == (True, []), "a file that is not on this Mac is not waited for"
    assert run(config).commit_sha is None and len(fetched) == 3
    # The person downloads it (Finder's Download Now changes the inode's flags, so its change time).  The
    # walk sees the row move and reads the file as it reads any touched file; the pass after that finds
    # it unchanged and on this Mac, and reads it again.
    inos.clear()
    online.chmod(0o600)
    assert run(config).exit_code == 0 and fetched[3:] == [online_rel]
    assert COMMENTS not in _mirror_page(config, online_rel)[1] and not _reread_record(config)[0]
    assert run(config).commit_sha is not None and fetched[3:] == [online_rel, online_rel]
    assert COMMENTS in _mirror_page(config, online_rel)[1] and _reread_record(config) == (True, [])
    assert run(config).commit_sha is None and len(fetched) == 5


def test_a_file_evicted_after_the_walk_listed_it_is_not_downloaded_for_a_re_read(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk lists a file as on this Mac and the provider evicts it before it is read.  The work queue
    would download it within the byte budget; a re-read never does.  The row is not deferred, the loop has
    nothing to wait for, and the file is not remembered as tried: it is asked about again."""
    review = _commented(local_source_dir / REVIEW, "Contoso widget review")
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
    before = _mirror_bytes(sample_config, REVIEW)
    evicted, real = review.stat().st_ino, materialise.is_dataless
    monkeypatch.setattr(materialise, "is_dataless", lambda st: st.st_ino == evicted or real(st))
    report = run(sample_config, budget_bytes=10_000_000)
    [rep] = report.sources
    assert (rep.deferred, rep.deferred_online_only, rep.materialised_bytes, rep.errors) == (0, 0, 0, ())
    assert _mirror_bytes(sample_config, REVIEW) == before
    row = _file_rows(sample_config)[REVIEW]
    assert (row.state, row.state_reason, row.last_verdict) == (RowState.LIVE, None, Verdict.UNCHANGED)
    assert _reread_record(sample_config) == (False, []) and loop.next_step(sample_config).rule != 3
    monkeypatch.setattr(materialise, "is_dataless", real)  # on this Mac again
    assert run(sample_config).commit_sha is not None and COMMENTS in _mirror_page(sample_config, REVIEW)[1]


def test_a_file_that_cannot_be_read_again_is_tried_once_and_no_log_line_names_it(
    sample_config: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The file was readable when it was converted and is not now.  Its page stays, the row keeps its
    verdict (it is no pending work for every later sync to retry), and it is tried once.  What the cycle
    says about re-reads at INFO is its one line, and no line at any level holds a name or a path."""
    _commented(local_source_dir / REVIEW, "Contoso widget review")
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
    before = _mirror_bytes(sample_config, REVIEW)
    real = LocalArm.fetch
    asked: list[str] = []

    def fetch(self: Any, item: Any, dest: Path, budget: Any) -> Any:
        asked.append(item.rel_path)
        if item.rel_path == REVIEW:
            raise PermissionError(13, "Permission denied", f"/Users/someone/source/{item.rel_path}")
        return real(self, item, dest, budget)

    monkeypatch.setattr(LocalArm, "fetch", fetch)
    caplog.set_level(logging.DEBUG, logger="agentsync.cycle")
    report = run(sample_config)
    [rep] = report.sources
    assert (report.exit_code, rep.errors, rep.alarms) == (0, (), ()) and sorted(asked) == [REVIEW, PLAIN_PDF]
    assert _mirror_bytes(sample_config, REVIEW) == before
    row = _file_rows(sample_config)[REVIEW]
    assert (row.state, row.last_verdict) == (RowState.LIVE, Verdict.UNCHANGED)
    assert (
        _reread_record(sample_config) == (True, [row.stable_id]) and loop.next_step(sample_config).rule != 3
    )
    said = [r for r in caplog.records if r.name == "agentsync.cycle" and "read again" in r.getMessage()]
    assert [r.getMessage() for r in said if r.levelno >= logging.INFO] == [
        "1 file(s) converted before a capability this install has were read again; 0 of them could not be "
        "converted and keep the page they had"
    ]
    assert [r.getMessage() for r in said if r.levelno < logging.INFO] == [
        "a file could not be read again (PermissionError); its page is as it was"
    ]
    assert run(sample_config).commit_sha is None and len(asked) == 2


def test_an_error_while_a_file_is_read_again_does_not_fail_the_source(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Something other than the conversion goes wrong while a file is read again (here: its page cannot be
    written).  The source has synced and stays synced: its removals still run, the report carries one
    line with no name in it, and the cycle exits 0.  The file is tried, and as pending work the next pass
    checks its page against the manifest; the files behind it are read by that pass."""
    _commented(local_source_dir / REVIEW, "Contoso widget review")
    doomed = "projects/sample.md"
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0 and run(sample_config).commit_sha is None
    (local_source_dir / doomed).unlink()  # absent from the next two passes: the second removes it
    real = cycle_mod._Cycle._after_fetch
    read: list[str] = []

    def after_fetch(self: Any, src: Any, row: Any, fetched: Any, acc: Any, *, reread: bool = False) -> Any:
        if reread:
            read.append(row.rel_path)
            if len(read) == 1:
                raise OSError(28, "No space left on device", f"/Users/someone/docs/{row.rel_path}")
        return real(self, src, row, fetched, acc, reread=reread)

    monkeypatch.setattr(cycle_mod._Cycle, "_after_fetch", after_fetch)
    first = run(sample_config)
    [rep] = first.sources
    assert first.exit_code == 0 and rep.errors == ("reading files again stopped: OSError",)
    assert any("1 file(s) absent from this complete pass" in a for a in rep.alarms), rep.alarms
    (stopped,) = read
    row = _file_rows(sample_config)[stopped]
    assert row.last_verdict is Verdict.MAYBE_CHANGED and _reread_record(sample_config) == (
        False,
        [row.stable_id],
    )
    second = run(sample_config)
    [rep] = second.sources
    assert second.exit_code == 0 and rep.errors == () and sorted(read) == [REVIEW, PLAIN_PDF]
    assert _file_rows(sample_config)[stopped].last_verdict is Verdict.TOUCHED_NOT_CHANGED
    assert _file_rows(sample_config)[doomed].state is RowState.TOMBSTONE
    assert _reread_record(sample_config) == (True, [row.stable_id])
    assert run(sample_config).exit_code == 0 and len(read) == 2


def test_a_re_read_leaves_the_deletion_breaker_and_its_held_files_as_they_were(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files absent from a complete listing are deletion candidates, held while the breaker is tripped.
    They are not read again and not tried, the breaker counts what it counted, and nothing is removed; the
    files the pass did list are read again all the same."""
    config = config_with(
        tmp_path, local_source_dir, "\n[breaker]\nfraction = 0.2\nfloor = 2\nhold_days = 7\n"
    )
    _commented(local_source_dir / REVIEW, "Contoso widget review")
    victims = [f"reviews/Contoso review {n}.pdf" for n in range(6)]
    for n, rel in enumerate(victims):
        _commented(local_source_dir / rel, f"Contoso review {n}")
    with _before_comments(monkeypatch):
        assert run(config).commit_sha is not None
    for rel in victims:
        (local_source_dir / rel).unlink()
    fetched = _fetches(monkeypatch)
    report = run(config)
    rep = source_report(report)
    assert rep.breaker_tripped and any("breaker TRIPPED: 6 absent file(s) held" in a for a in rep.alarms)
    assert sorted(fetched) == [REVIEW, PLAIN_PDF]
    assert [c.path for c in report.changes] == [slug.mirror_rel_path(SID, REVIEW)]
    rows = _file_rows(config)
    assert all(rows[rel].state is RowState.LIVE for rel in victims)
    assert all(
        page(config.docs_repo, slug.mirror_rel_path(SID, rel))[0]["status"] == "current" for rel in victims
    )
    with Manifest(config.state_paths.db) as m:
        srow = m.get_source(SID)
        assert (
            srow is not None
            and srow.breaker_candidates == 6
            and m.breaker_active(SID, "2026-09-29T12:00:00Z")
        )
    assert _reread_record(config) == (False, []), "the held files may come back: the look stays open"
    held = run(config)
    assert source_report(held).breaker_tripped and held.changes == () and len(fetched) == 2
    assert all(_file_rows(config)[rel].state is RowState.LIVE for rel in victims)


def test_a_file_an_incomplete_pass_did_not_list_is_not_read_again(
    sample_config: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete pass holds deletions: a file it did not list is unknown, never gone.  It is not read
    again either (there may be nothing to read), and the files the pass did list are."""
    absent_rel = "projects/Contoso absent review.pdf"
    absent = _commented(local_source_dir / absent_rel, "Contoso absent review")
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
    absent.unlink()
    config = _incomplete(sample_config)
    fetched = _fetches(monkeypatch)
    report = run(config)
    [rep] = report.sources
    assert not rep.enumeration_complete and not rep.breaker_tripped and fetched == [PLAIN_PDF]
    assert report.changes == () and report.commit_sha is None, "read again, the same page: nothing to commit"
    assert _file_rows(config)[absent_rel].state is RowState.LIVE
    assert _mirror_page(config, absent_rel)[0]["status"] == "current"
    assert _reread_record(config) == (False, []), "it may be listed again: the look stays open"
    assert run(config).commit_sha is None and fetched == [PLAIN_PDF]


def test_a_file_the_engine_fails_on_is_read_again_once_and_an_engine_that_comes_back_reads_what_is_new(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OCR never fails a document: a PDF the engine fails on keeps the page it has without OCR, under the
    version without OCR.  That version is what a re-read looks for, so the file is remembered as tried and
    not read every cycle.  An engine that goes away and comes back reads the file converted meanwhile, and
    neither the one it read before nor the one it failed on."""
    scans = local_source_dir / "scans"
    scans.mkdir()

    def scan(name: str, shade: int) -> str:
        pages = [([f"{name}: the cover page has a text layer of its own"], []), ([], [page_picture(shade)])]
        build_picture_pdf(scans / f"Contoso {name}.pdf", pages)
        return f"scans/Contoso {name}.pdf"

    good, bad = scan("good scan", 91), scan("bad scan", 95)
    assert run(sample_config).exit_code == 0
    said = {91: ["Signed in Rotterdam"], 93: ["Counted in Antwerp"], 95: "fail"}
    engine = shade_engine(tmp_path / "ocr-bin", said)
    found: list[Any] = [engine]
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, _cache_dir: found[0])
    fetched = _fetches(monkeypatch)
    unread = _mirror_bytes(sample_config, bad)
    second = run(sample_config)
    assert second.exit_code == 0 and second.sources[0].errors == ()
    assert _mirror_page(sample_config, good)[1].rstrip().endswith("Signed in Rotterdam")
    assert _mirror_bytes(sample_config, bad) == unread and b"ocr" not in unread[bad]
    bad_id = _file_rows(sample_config)[bad].stable_id
    assert _reread_record(sample_config) == (True, [bad_id])
    runs = len(calls(engine.helper))
    assert run(sample_config).commit_sha is None and len(calls(engine.helper)) == runs
    assert (fetched.count(good), fetched.count(bad)) == (1, 1)
    # The engine goes away.  A scan mirrored meanwhile is converted as on a Mac without one.
    found[0] = None
    late = scan("late scan", 93)
    assert (
        run(sample_config).exit_code == 0 and "ocr" not in _mirror_page(sample_config, late)[0]["converter"]
    )
    assert run(sample_config).commit_sha is None and len(calls(engine.helper)) == runs
    # It comes back: the late scan is read again, the other two are not.
    found[0] = engine
    assert run(sample_config).commit_sha is not None
    assert _mirror_page(sample_config, late)[1].rstrip().endswith("Counted in Antwerp")
    assert (fetched.count(good), fetched.count(bad), fetched.count(late)) == (1, 1, 2)
    assert _mirror_bytes(sample_config, bad) == unread and _reread_record(sample_config) == (True, [bad_id])
    assert run(sample_config).commit_sha is None and fetched.count(late) == 2
    # A new scan the engine fails on is converted without OCR.  That leaves a file to read again, so the
    # cycle after looks, reads it once more, and remembers it too.
    worse = scan("worse scan", 95)
    assert run(sample_config).exit_code == 0 and fetched.count(worse) == 1
    assert "ocr" not in _mirror_page(sample_config, worse)[0]["converter"]
    assert _reread_record(sample_config) == (False, [bad_id])
    assert run(sample_config).commit_sha is None and fetched.count(worse) == 2
    worse_id = _file_rows(sample_config)[worse].stable_id
    assert _reread_record(sample_config) == (True, sorted([bad_id, worse_id]))
    assert run(sample_config).commit_sha is None and fetched.count(worse) == 2


def test_re_reads_stop_at_their_time_and_go_on_in_the_next_cycle_past_a_file_that_fails(
    sample_config: Config,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Six PDFs to read again, two to a transaction, each read 'taking' 25 s of a cycle's 60: three a
    cycle.  One of them fails every time it is converted.  It costs its one read and holds nobody up: the
    other five are read over two cycles, and the third cycle reads nothing.  A cycle says what it did in one
    line, a count, with no name in it."""
    reviews = [f"reviews/Contoso review {n}.pdf" for n in range(5)]
    for n, rel in enumerate(reviews):
        _commented(local_source_dir / rel, f"Contoso review {n}")
    with _before_comments(monkeypatch):
        assert run(sample_config).exit_code == 0
    stuck = _mirror_bytes(sample_config, reviews[0])
    real = pdf_mod.PdfConverter.convert

    def convert(self: Any, src: Path, *, name: str) -> Any:
        if name == "Contoso review 0.pdf":
            raise RuntimeError("this file breaks the converter")
        return real(self, src, name=name)

    monkeypatch.setattr(pdf_mod.PdfConverter, "convert", convert)
    ticks = itertools.count(0.0, 25.0)
    monkeypatch.setattr(cycle_mod, "_reread_clock", lambda: next(ticks))
    monkeypatch.setattr(cycle_mod, "_REREAD_BUDGET_S", 60.0)
    monkeypatch.setattr(cycle_mod, "_REREAD_BATCH", 2)
    fetched = _fetches(monkeypatch)
    caplog.set_level(logging.INFO, logger="agentsync.cycle")
    first = run(sample_config)
    assert first.exit_code == 0 and len(fetched) == 3 and not _reread_record(sample_config)[0]
    second = run(sample_config)
    assert second.exit_code == 0 and sorted(fetched) == sorted([*reviews, PLAIN_PDF]), "each file once"
    stuck_id = _file_rows(sample_config)[reviews[0]].stable_id
    assert _reread_record(sample_config) == (True, [stuck_id])
    assert _mirror_bytes(sample_config, reviews[0]) == stuck
    assert all(COMMENTS in _mirror_page(sample_config, rel)[1] for rel in reviews[1:])
    third = run(sample_config)
    assert third.commit_sha is None and len(fetched) == 6
    said = [
        r.getMessage()
        for r in caplog.records
        if r.name == "agentsync.cycle" and "read again" in r.getMessage()
    ]
    assert said == [
        "3 file(s) converted before a capability this install has were read again; "
        f"{kept} of them could not be converted and keep the page they had"
        for kept in ([1, 0] if reviews[0] in fetched[:3] else [0, 1])
    ]
    assert "Contoso" not in "".join(said) and "sample" not in "".join(said)


def test_a_file_a_materialise_run_converted_without_ocr_is_read_again_by_the_next_sync(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``materialise PATH`` run reads nothing again, and it can still leave a file to read again: the
    file it names, converted after the cycle's OCR time was used.  The source's record says so in that
    run, so the next sync looks although the run before it had found nothing left."""
    engine = shade_engine(tmp_path / "ocr-bin", {91: ["Signed in Rotterdam"], 93: ["Counted in Antwerp"]})
    monkeypatch.setattr(cycle_mod.ocr, "engine", lambda _convert, _cache_dir: engine)
    rel = "scans/Contoso supply agreement.pdf"
    cover = ["The cover page has a text layer of its own"]
    (local_source_dir / "scans").mkdir()
    build_picture_pdf(local_source_dir / rel, [(cover, []), ([], [page_picture(91)])])
    assert run(sample_config).exit_code == 0 and _reread_record(sample_config) == (True, [])
    assert _mirror_page(sample_config, rel)[1].rstrip().endswith("Signed in Rotterdam")
    build_picture_pdf(local_source_dir / rel, [(cover, []), ([], [page_picture(93)])])
    with monkeypatch.context() as spent:
        spent.setattr(cycle_mod, "_OCR_BUDGET_S", 0.0)
        named = run(sample_config, materialise_paths=[local_source_dir / rel], budget_bytes=10_000_000)
    fm, body = _mirror_page(sample_config, rel)
    assert named.exit_code == 0 and "ocr" not in fm["converter"] and "Antwerp" not in body
    assert _reread_record(sample_config) == (False, [])
    assert run(sample_config).commit_sha is not None and _reread_record(sample_config) == (True, [])
    assert _mirror_page(sample_config, rel)[1].rstrip().endswith("Counted in Antwerp")
    assert run(sample_config).commit_sha is None


def test_a_converter_that_cannot_run_costs_its_files_neither_a_page_nor_their_one_re_read(
    sample_config: Config, local_source_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pandoc cannot be run in the cycle that would read the Word files again.  That is no failure of those
    files: they are not read, their pages stay, and they are not remembered as tried, so the cycle in which
    pandoc runs again reads them.  The files of the other converters are read meanwhile."""
    assert run(sample_config).exit_code == 0
    words = ["projects/sample.docx", KICKOFF]
    before = _mirror_bytes(sample_config, *words)
    _use_ocr(monkeypatch, tmp_path)
    fetched = _fetches(monkeypatch)

    def missing(self: Any) -> str:
        raise ConversionError("cannot run pandoc: [Errno 2] No such file or directory")

    with monkeypatch.context() as fault:
        fault.setattr(pandoc_mod._PandocRunner, "version", missing)
        report = run(sample_config)
    [rep] = report.sources
    assert (report.exit_code, rep.errors, rep.alarms) == (0, (), ())
    assert sorted(fetched) == [PLAIN_PDF, "projects/sample.pptx"]
    assert _mirror_bytes(sample_config, *words) == before and _reread_record(sample_config) == (False, [])
    assert run(sample_config).exit_code == 0 and sorted(fetched[2:]) == sorted(words)
    assert _mirror_bytes(sample_config, *words) == before, "no picture in them: the pages stay as they are"
    assert _reread_record(sample_config) == (True, [])
    assert run(sample_config).commit_sha is None and len(fetched) == 4


def test_a_graph_file_is_never_read_again(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read of a Graph item is a download, and nothing is downloaded for a re-read: a PDF a drive
    source mirrored before comments were kept gains them when its bytes next change."""
    config = config_with(tmp_path, local_source_dir, GRAPH_SOURCE)
    data = _commented(tmp_path / "review.pdf", "Contoso widget review").read_bytes()
    drive = FakeDrive({"I1": ("Contoso widget review.pdf", data)})
    with GraphClient(
        FakeTokens(),
        user_agent="NONISV|test|agentsync/0",
        transport=httpx.MockTransport(drive.handler),
        sleep=lambda _s: None,
    ) as client:
        with _before_comments(monkeypatch):
            assert run(config, client=client, only=["drive"]).exit_code == 0
        downloads = [entry for entry in drive.log if entry.endswith("/content")]
        assert len(downloads) == 1
        assert run(config, client=client, only=["drive"]).commit_sha is None
        assert [entry for entry in drive.log if entry.endswith("/content")] == downloads
    [mirrored] = (config.docs_repo / "mirror" / "drive").rglob("*.pdf.md")
    text = mirrored.read_text(encoding="utf-8")
    assert "pdf-pypdfium2@2.0.0+" in text and COMMENTS not in text
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._REREAD_META + "drive") is None
