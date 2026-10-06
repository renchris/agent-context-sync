"""cycle.py mechanics: recovery after crashes, cursor ordering, source isolation, the lock, dry-run,
retirement.  Crashes are simulated with a BaseException the cycle does not catch (like a SIGKILL mid-cycle:
no cleanup code runs), so the next cycle's ``recover`` sees exactly what a dead process leaves behind."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentsync import arm_local as al
from agentsync import cycle as cycle_mod
from agentsync import gitops, governance, loop, slug
from agentsync.arm_local import LocalArm
from agentsync.config import Config, parse_config
from agentsync.cycle import RecoveryAction, recover, run_cycle
from agentsync.errors import LockHeldError
from agentsync.graph.client import GraphClient
from agentsync.graph.drive import DriveArm
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, CycleReport, PassKind, RowState, ScanResult, Verdict
from agentsync.ops.lock import SingleWriterLock, read_heartbeat
from agentsync.publish import Publisher
from conftest import config_text
from test_e2e import GRAPH_SOURCE, SID, FakeDrive, FakeTokens, clock, config_with, git, page, porcelain


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


def test_a_capped_file_whose_page_leaves_no_room_for_a_sidecar_is_one_quarantined_item(
    sample_config: Config, local_source_dir: Path
) -> None:
    """The item gets a stub and is settled; every other file is converted and the commit lands."""
    rel = (
        "Contoso Working Sessions/Document Repository/Regional Sales Summary - FY26 Q3 Review Pack - "
        "All Regions - Final Export From The Data Warehouse v2 - Appendix With Every Row.txt"
    )
    page = slug.mirror_rel_path(SID, rel)
    assert len(page) > 183  # no ``.files/<8 hex>.txt`` fits beside it
    big = local_source_dir / rel
    big.parent.mkdir(parents=True)
    big.write_text("every row of the appendix\n" * 45_000, encoding="utf-8")  # past max_page_bytes
    first = run(sample_config)
    [src] = first.sources
    assert first.exit_code == 0 and first.commit_sha is not None, first
    assert src.errors == () and src.counts.get(Verdict.QUARANTINED) == 1
    assert not [f for f in first.lint_findings if f.lint_id == "PATH"]
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
