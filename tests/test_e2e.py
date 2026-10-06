"""End-to-end: real fixture files -> run_cycle -> a real docs git repo (brief W2 items a-h).

Every test drives ``cycle.run_cycle`` against tmp dirs (conftest isolates HOME); the Graph test drives the
real GraphClient + DriveArm over an httpx.MockTransport, so nothing touches the network or the Keychain.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentsync import arm_local, gitops, lints, materialise
from agentsync.arm_local import LocalArm
from agentsync.config import Config, parse_config
from agentsync.cycle import run_cycle
from agentsync.errors import AuthRequiredError
from agentsync.frontmatter import parse_frontmatter, parse_mirror_page
from agentsync.graph.client import GraphClient
from agentsync.manifest import Manifest, cursor_fingerprint
from agentsync.model import ChangeOp, CycleMode, CycleReport, OutputStatus, PageStatus, RowState, Verdict
from conftest import config_text

SID = "local-fixture"
NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def run(config: Config, **kwargs: Any) -> CycleReport:
    return run_cycle(config, mode=kwargs.pop("mode", CycleMode.POLL), now=clock, **kwargs)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit_count(repo: Path) -> int:
    return int(git(repo, "rev-list", "--count", "HEAD").strip())


def porcelain(repo: Path) -> str:
    return git(repo, "status", "--porcelain")


def page(repo: Path, rel: str) -> tuple[dict[str, Any], str]:
    return parse_frontmatter((repo / rel).read_text(encoding="utf-8"))


def mirror(rel: str) -> str:
    return f"mirror/{SID}/{rel}"


def counts(report: CycleReport, sid: str = SID) -> dict[Verdict, int]:
    return dict(next(s for s in report.sources if s.source_id == sid).counts)


def source_report(report: CycleReport, sid: str = SID) -> Any:
    return next(s for s in report.sources if s.source_id == sid)


def config_with(tmp_path: Path, source_dir: Path, extra: str = "") -> Config:
    repo = tmp_path / "agent-context" / "docs"
    text = config_text(repo, tmp_path / "state", tmp_path / "cache", source_dir) + extra
    path = tmp_path / "agent-context" / "sources.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return parse_config(text, config_path=path)


@pytest.fixture
def synced(sample_config: Config) -> Config:
    """sample_config after one committed first sync."""
    report = run(sample_config)
    assert report.exit_code == 0, report
    assert report.commit_sha is not None
    return sample_config


def rewrite_zip(path: Path, edit: Callable[[str, bytes], bytes], *, date: tuple[int, ...]) -> None:
    """Rewrite an OOXML container in place, passing each part through ``edit`` and re-stamping zip times."""
    with zipfile.ZipFile(path) as src:
        parts = [(info.filename, src.read(info.filename)) for info in src.infolist()]
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in parts:
            info = zipfile.ZipInfo(name, date_time=date)  # type: ignore[arg-type]
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, edit(name, data))
    path.write_bytes(tmp.read_bytes())  # same inode: an in-place save
    tmp.unlink()


# ---------------------------------------------------------------------------------------------------------
# (a) first sync commits mirror pages + INDEX; a second sync is a no-op
# ---------------------------------------------------------------------------------------------------------


def test_a_first_sync_commits_mirror_and_index_then_noop(sample_config: Config) -> None:
    repo = sample_config.docs_repo
    report = run(sample_config)
    assert report.exit_code == 0
    assert report.commit_sha is not None and commit_count(repo) == 1
    subject = git(repo, "log", "-1", "--format=%s")
    assert subject.startswith("sync: ") and SID in subject
    for rel in (
        "projects/sample.docx.md",
        "projects/sample.xlsx.d/00-index.md",
        "projects/sample.xlsx.d/01-q3-budget.md",
        "projects/sample.pptx.md",
        "projects/sample.pdf.md",
        "projects/sample.md",
        "projects/acme/kickoff-notes.docx.md",
    ):
        fm, body = parse_mirror_page((repo / mirror(rel)).read_text(encoding="utf-8"))
        assert fm.status is PageStatus.CURRENT and fm.source_id == SID, rel
        assert body.strip(), rel
    assert not (repo / mirror("projects/~$lockfile.docx.md")).exists()  # default exclude
    index = (repo / "INDEX.md").read_text(encoding="utf-8")
    assert f"[{SID}](mirror/{SID}/)" in index and "baseline complete" in index
    assert (repo / "_manifest" / f"{SID}.jsonl").is_file()
    assert (repo / "CHANGELOG.md").is_file()
    assert (repo / "_sync" / "STATE.md").is_file()
    assert "_sync/STATE.md" not in git(repo, "ls-files")
    assert porcelain(repo) == ""
    assert not [f for f in lints.run_land_gate(repo, []) if f.blocking]
    assert git(repo, "rev-parse", "published").strip() == report.commit_sha

    second = run(sample_config)
    assert second.exit_code == 0
    assert second.commit_sha is None and second.changes == ()
    assert commit_count(repo) == 1 and porcelain(repo) == ""
    assert set(counts(second)) == {Verdict.UNCHANGED}


# ---------------------------------------------------------------------------------------------------------
# (b) touch-only: H0 differs, H1 equal -> no new commit content
# ---------------------------------------------------------------------------------------------------------


def test_b_touch_only_reads_bytes_but_commits_nothing(synced: Config, local_source_dir: Path) -> None:
    repo = synced.docs_repo
    head = git(repo, "rev-parse", "HEAD")
    target = local_source_dir / "projects" / "sample.docx"
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns - 7_000_000_000))  # a past mtime: not racily clean
    report = run(synced)
    assert report.exit_code == 0
    assert counts(report).get(Verdict.MAYBE_CHANGED) == 1
    assert counts(report).get(Verdict.TOUCHED_NOT_CHANGED) == 1
    assert report.commit_sha is None and report.changes == ()
    assert git(repo, "rev-parse", "HEAD") == head and porcelain(repo) == ""
    with Manifest(synced.state_paths.db) as m:
        row = m.item_by_path(SID, "projects/sample.docx")
        assert row is not None and row.mtime_ns == st.st_mtime_ns - 7_000_000_000  # H0 re-stamped
        assert row.last_verdict is Verdict.TOUCHED_NOT_CHANGED
    third = run(synced)
    assert set(counts(third)) == {Verdict.UNCHANGED}


# ---------------------------------------------------------------------------------------------------------
# (c) no-op re-save: docProps-only -> H1 cutoff; styles-only -> H1 differs, H2 early cutoff
# ---------------------------------------------------------------------------------------------------------


def test_c_noop_resave_docprops_is_touched_not_changed(synced: Config, local_source_dir: Path) -> None:
    repo = synced.docs_repo
    head = git(repo, "rev-parse", "HEAD")
    target = local_source_dir / "projects" / "sample.docx"
    before = target.read_bytes()

    def edit(name: str, data: bytes) -> bytes:
        if name == "docProps/core.xml":
            text = data.decode("utf-8")
            if "<dcterms:modified" in text:
                return re.sub(r"(<dcterms:modified[^>]*>)[^<]*", r"\g<1>2031-01-02T03:04:05Z", text).encode()
            return text.replace(
                "</cp:coreProperties>", "<cp:revision>9</cp:revision></cp:coreProperties>"
            ).encode()
        return data

    rewrite_zip(target, edit, date=(2031, 1, 2, 3, 4, 6))
    assert target.read_bytes() != before
    # The same cut-off for a second file in the same cycle: the fixture workbook as Office re-saves it,
    # copied over the original (different bytes, same canonical content).
    projects = local_source_dir / "projects"
    (projects / "sample.xlsx").write_bytes((projects / "sample-resaved.xlsx").read_bytes())
    report = run(synced)
    assert counts(report).get(Verdict.TOUCHED_NOT_CHANGED) == 2
    assert Verdict.CHANGED not in counts(report)
    assert report.commit_sha is None and git(repo, "rev-parse", "HEAD") == head and porcelain(repo) == ""
    with Manifest(synced.state_paths.db) as m:
        for rel in ("projects/sample.docx", "projects/sample.xlsx"):
            row = m.item_by_path(SID, rel)
            assert row is not None and row.last_verdict is Verdict.TOUCHED_NOT_CHANGED, rel


def test_c_styles_only_resave_is_h2_early_cutoff(synced: Config, local_source_dir: Path) -> None:
    repo = synced.docs_repo
    head = git(repo, "rev-parse", "HEAD")
    target = local_source_dir / "projects" / "sample.xlsx"
    edited: list[str] = []

    def edit(name: str, data: bytes) -> bytes:
        if name == "xl/styles.xml":
            text = data.decode("utf-8")
            new = re.sub(r'<sz val="(\d+)"', lambda m: f'<sz val="{int(m.group(1)) + 1}"', text, count=1)
            if new != text:
                edited.append(name)
            return new.encode("utf-8")
        return data

    rewrite_zip(target, edit, date=(2031, 1, 2, 3, 4, 6))
    assert edited == ["xl/styles.xml"]
    report = run(synced)
    c = counts(report)
    assert c.get(Verdict.OUTPUT_UNCHANGED) == 1, c  # H1 moved (a real part changed), every H2 held
    assert Verdict.TOUCHED_NOT_CHANGED not in c
    assert report.changes == () and report.commit_sha is None
    assert git(repo, "rev-parse", "HEAD") == head and porcelain(repo) == ""
    with Manifest(synced.state_paths.db) as m:
        row = m.item_by_path(SID, "projects/sample.xlsx")
        assert row is not None and row.last_verdict is Verdict.OUTPUT_UNCHANGED
        keys = {o.action_key for o in m.outputs_for(SID, row.stable_id)}
        assert len(keys) == 1 and keys <= m.live_action_keys()  # the new cache key is a GC root


# ---------------------------------------------------------------------------------------------------------
# (d) edit -> one changed page, one commit
# ---------------------------------------------------------------------------------------------------------


def test_d_edit_changes_one_page_in_one_commit(synced: Config, local_source_dir: Path) -> None:
    repo = synced.docs_repo
    target = local_source_dir / "projects" / "sample.txt"
    target.write_text(
        target.read_text(encoding="utf-8") + "A third line about the invoice.\n", encoding="utf-8"
    )
    report = run(synced)
    assert report.exit_code == 0 and report.commit_sha is not None
    assert [(c.op, c.path) for c in report.changes] == [(ChangeOp.MODIFIED, mirror("projects/sample.txt.md"))]
    assert commit_count(repo) == 2
    assert git(repo, "log", "-1", "--format=%s").strip() == f"sync: 0a 1m 0r 0d {SID}"
    changed = sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert mirror("projects/sample.txt.md") in changed
    assert all(not p.startswith("mirror/") or p == mirror("projects/sample.txt.md") for p in changed)
    _, body = page(repo, mirror("projects/sample.txt.md"))
    assert "third line about the invoice" in body
    assert porcelain(repo) == ""


# ---------------------------------------------------------------------------------------------------------
# (e) rename -> same stable id, the page moves (R), never a duplicate
# ---------------------------------------------------------------------------------------------------------


def test_e_rename_moves_the_page_under_the_same_stable_id(synced: Config, local_source_dir: Path) -> None:
    repo = synced.docs_repo
    with Manifest(synced.state_paths.db) as m:
        row = m.item_by_path(SID, "projects/sample.pptx")
        assert row is not None
        stable, n_outputs = row.stable_id, len(list(m.iter_outputs()))
    old_page = mirror("projects/sample.pptx.md")
    old_body = page(repo, old_page)[1]
    (local_source_dir / "projects" / "sample.pptx").rename(
        local_source_dir / "projects" / "acme" / "Q3 Deck.pptx"
    )
    report = run(synced)
    assert report.exit_code == 0 and report.commit_sha is not None
    new_page = mirror("projects/acme/q3-deck.pptx.md")
    assert [(c.op, c.path, c.prev_path) for c in report.changes] == [(ChangeOp.RENAMED, new_page, old_page)]
    assert not (repo / old_page).exists()
    fm, body = page(repo, new_page)
    assert fm["stable_id"] == stable and fm["source_path"] == "projects/acme/Q3 Deck.pptx"
    assert body == old_body
    with Manifest(synced.state_paths.db) as m:
        assert [o.output_path for o in m.outputs_for(SID, stable)] == [new_page]
        assert len(list(m.iter_outputs())) == n_outputs
        moved = m.get_item(SID, stable)
        assert moved is not None and moved.prev_path == "projects/sample.pptx"
    status = git(repo, "show", "-M", "--name-status", "--format=", "HEAD")
    assert re.search(r"^R\d*\t" + re.escape(old_page) + "\t" + re.escape(new_page), status, re.M)
    assert porcelain(repo) == ""


def test_e_directory_rename_moves_every_page_without_reading_bytes(
    synced: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = synced.docs_repo
    fetched: list[str] = []
    real_fetch = LocalArm.fetch

    def spy(self: LocalArm, item: Any, dest_dir: Path, budget: Any) -> Any:
        fetched.append(item.rel_path)
        return real_fetch(self, item, dest_dir, budget)

    monkeypatch.setattr(LocalArm, "fetch", spy)
    (local_source_dir / "projects" / "acme").rename(local_source_dir / "projects" / "Acme Corp")
    report = run(synced)
    assert fetched == []  # a directory rename leaves the file's own stat alone: METADATA_ONLY
    assert [(c.op, c.path) for c in report.changes] == [
        (ChangeOp.RENAMED, mirror("projects/acme-corp/kickoff-notes.docx.md"))
    ]
    assert not (repo / mirror("projects/acme")).exists()


# ---------------------------------------------------------------------------------------------------------
# (f) delete -> tombstone; mass delete -> breaker trips, no commit
# ---------------------------------------------------------------------------------------------------------


def test_f_delete_writes_a_tombstone(synced: Config, local_source_dir: Path) -> None:
    repo = synced.docs_repo
    (local_source_dir / "projects" / "sample.csv").unlink()
    first = run(synced)  # a local file must be absent from two complete passes (an Office save in flight)
    assert first.changes == () and any("absent" in a for a in source_report(first).alarms)
    report = run(synced)
    rel = mirror("projects/sample.csv.md")
    assert [(c.op, c.path) for c in report.changes] == [(ChangeOp.DELETED, rel)]
    assert report.commit_sha is not None
    fm, body = page(repo, rel)
    assert fm["status"] == "deleted" and fm["deleted_at"] == "2026-09-29"
    assert body.startswith("# [DELETED UPSTREAM]") and "git show " in body
    with Manifest(synced.state_paths.db) as m:
        tomb = m.get_tombstone(rel)
        assert tomb is not None and tomb.reason == "deleted-upstream"
        row = next(r for r in m.iter_items(SID) if r.rel_path == "projects/sample.csv")
        assert row.state is RowState.TOMBSTONE
    assert porcelain(repo) == ""
    again = run(synced)
    assert again.commit_sha is None


def test_f_mass_delete_trips_the_breaker_and_commits_nothing(tmp_path: Path, local_source_dir: Path) -> None:
    config = config_with(
        tmp_path, local_source_dir, "\n[breaker]\nfraction = 0.2\nfloor = 2\nhold_days = 7\n"
    )
    first = run(config)
    assert first.commit_sha is not None
    repo = config.docs_repo
    head = git(repo, "rev-parse", "HEAD")
    victims = ["sample.csv", "sample.txt", "sample.html", "sample.md", "sample.pdf"]
    for name in victims:
        (local_source_dir / "projects" / name).unlink()
    report = run(config)
    rep = source_report(report)
    assert rep.breaker_tripped and any("breaker TRIPPED" in a for a in rep.alarms)
    assert counts(report)[Verdict.DELETION_CANDIDATE] == len(victims)
    assert report.changes == () and report.commit_sha is None
    assert git(repo, "rev-parse", "HEAD") == head and porcelain(repo) == ""
    assert page(repo, mirror("projects/sample.csv.md"))[0]["status"] == "current"
    with Manifest(config.state_paths.db) as m:
        assert m.breaker_active(SID, "2026-09-29T12:00:00Z")
    state_md = (repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "breaker: TRIPPED" in state_md

    held = run(config)  # still held on the next cycle: nothing removed
    assert held.changes == () and source_report(held).breaker_tripped

    accepted = run(config, mode=CycleMode.RECONCILE, only=[SID], accept_deletions=[SID])
    assert sorted(c.op for c in accepted.changes) == [ChangeOp.DELETED] * len(victims)
    assert accepted.commit_sha is not None
    with Manifest(config.state_paths.db) as m:
        assert not m.breaker_active(SID, "2026-09-29T12:00:00Z")


# ---------------------------------------------------------------------------------------------------------
# (g) dataless: st_flags mocked (tmpfs/APFS tmp dirs cannot hold a real placeholder)
# ---------------------------------------------------------------------------------------------------------


def _mock_dataless(monkeypatch: pytest.MonkeyPatch, inodes: set[int]) -> None:
    real = materialise.is_dataless

    def fake(st: os.stat_result) -> bool:
        return st.st_ino in inodes or real(st)

    monkeypatch.setattr(arm_local, "is_dataless", fake)


def _fetch_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []
    real_fetch = LocalArm.fetch

    def spy(self: LocalArm, item: Any, dest_dir: Path, budget: Any) -> Any:
        fetched.append(item.rel_path)
        return real_fetch(self, item, dest_dir, budget)

    monkeypatch.setattr(LocalArm, "fetch", spy)
    return fetched


def test_g_evicted_file_is_never_read_and_commits_nothing(
    synced: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = synced.docs_repo
    head = git(repo, "rev-parse", "HEAD")
    target = local_source_dir / "projects" / "sample.docx"
    _mock_dataless(monkeypatch, {target.stat().st_ino})
    fetched = _fetch_spy(monkeypatch)
    report = run(synced)
    assert counts(report).get(Verdict.DATALESS) == 1
    assert fetched == []
    assert report.commit_sha is None and git(repo, "rev-parse", "HEAD") == head and porcelain(repo) == ""
    with Manifest(synced.state_paths.db) as m:
        row = m.item_by_path(SID, "projects/sample.docx")
        assert row is not None and row.state is RowState.DATALESS and row.dataless
    assert page(repo, mirror("projects/sample.docx.md"))[0]["status"] == "current"
    shard = (repo / "_manifest" / f"{SID}.jsonl").read_text(encoding="utf-8")
    assert '"state":"dataless"' not in shard  # eviction is local cache state, never a commit


def test_g_never_materialised_placeholder_is_deferred_then_materialised(
    synced: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch, fixture_files: dict[str, Path]
) -> None:
    repo = synced.docs_repo
    new = local_source_dir / "projects" / "Board Pack.docx"
    shutil.copy2(fixture_files["sample.docx"], new)
    _mock_dataless(monkeypatch, {new.stat().st_ino})
    fetched = _fetch_spy(monkeypatch)
    report = run(synced, budget_bytes=0)
    rep = source_report(report)
    assert rep.deferred == 1 and counts(report).get(Verdict.DEFERRED) == 1
    assert fetched == []
    assert not (repo / mirror("projects/board-pack.docx.md")).exists()
    with Manifest(synced.state_paths.db) as m:
        row = m.item_by_path(SID, "projects/Board Pack.docx")
        assert row is not None and row.last_verdict is Verdict.DEFERRED and row.content_sha256 is None
    state_md = (repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "deferred: 1" in state_md
    # The shard now lists the not-yet-converted file, and the commit subject names its source.
    assert report.commit_sha is not None
    assert git(repo, "log", "-1", "--format=%s").strip() == f"sync: 0a 0m 0r 0d {SID}"

    done = run(synced, materialise_paths=[new], budget_bytes=10_000_000)
    assert fetched == ["projects/Board Pack.docx"]
    assert [(c.op, c.path) for c in done.changes] == [(ChangeOp.ADDED, mirror("projects/board-pack.docx.md"))]
    assert done.commit_sha is not None
    assert page(repo, mirror("projects/board-pack.docx.md"))[0]["status"] == "current"


@pytest.mark.parametrize("name", ["clip.mp4", "diagram.png"])
def test_g_unknown_type_is_refused_without_reading_it(
    synced: Config, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A video has no converter. Nor has an image on a Mac with no OCR engine (here: the suite's switch),
    and there it is refused exactly as it was before OCR existed."""
    repo = synced.docs_repo
    (local_source_dir / "projects" / name).write_bytes(b"\0" * 4096)
    fetched = _fetch_spy(monkeypatch)
    report = run(synced)
    assert fetched == []  # no converter -> REFUSED stub, zero bytes read (never hydrate a video to refuse it)
    fm, _ = page(repo, mirror(f"projects/{name}.md"))
    assert fm["status"] == "refused" and fm["reason"] == f"no converter for {Path(name).suffix}"
    assert fm["converter"] == "none@0"
    assert report.commit_sha is not None
    tsv = (repo / "_sync" / "QUARANTINE.tsv").read_text(encoding="utf-8")
    assert f"projects/{name}" in tsv
    assert run(synced).commit_sha is None


# ---------------------------------------------------------------------------------------------------------
# (h) Graph drive arm with a mocked GraphClient through the full cycle, incl. 410 -> full enumeration
# ---------------------------------------------------------------------------------------------------------


class FakeTokens:
    """TokenProvider stand-in (never touches MSAL or the Keychain)."""

    def __init__(self) -> None:
        self.fail = False

    def get_token(self) -> str:
        if self.fail:
            raise AuthRequiredError("invalid_grant: refresh token expired")
        return "test-access-token"


class FakeDrive:
    """A tiny Graph drive: /me/drive, /root, /root/delta (token-less, token, 410) and item content."""

    BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, files: dict[str, tuple[str, bytes]]) -> None:
        self.files = dict(files)  # id -> (name, bytes)
        self.qx = {i: f"qx-{i}-1" for i in files}
        self.version = dict.fromkeys(files, 1)
        self.tokens = 0
        self.gone_next = False
        self.deleted: set[str] = set()
        self.snapshots: dict[str, tuple[dict[str, int], set[str]]] = {}  # token -> (versions, deleted)
        self.log: list[str] = []

    def raw(self, ident: str) -> dict[str, Any]:
        name, data = self.files[ident]
        v = self.version[ident]
        return {
            "id": ident,
            "name": name,
            "size": len(data),
            "eTag": f'"{{{ident}}},{v}"',
            "cTag": f'"c:{{{ident}}},{v}"',
            "file": {"mimeType": "application/octet-stream", "hashes": {"quickXorHash": self.qx[ident]}},
            "parentReference": {"id": "F1", "driveId": "D1"},
            "lastModifiedDateTime": f"2026-09-0{v}T10:00:00Z",
            "createdDateTime": "2026-09-01T09:00:00Z",
            "webUrl": f"https://contoso.example/Projects/{name}",
        }

    def all_items(self) -> list[dict[str, Any]]:
        folder = {
            "id": "F1",
            "name": "Projects",
            "folder": {"childCount": len(self.files)},
            "parentReference": {"id": "ROOT", "driveId": "D1"},
            "eTag": '"{F1},1"',
            "lastModifiedDateTime": "2026-09-01T10:00:00Z",
        }
        root = {"id": "ROOT", "name": "root", "root": {}, "folder": {"childCount": 1}}
        return [root, folder, *(self.raw(i) for i in sorted(self.files) if i not in self.deleted)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.log.append(f"{request.method} {url.host}{url.path}")
        if url.host == "download.example.test":
            assert "authorization" not in request.headers  # pre-authenticated: the bearer never leaves Graph
            return httpx.Response(200, content=self.files[url.path.rsplit("/", 1)[-1]][1])
        assert request.headers.get("authorization") == "Bearer test-access-token"
        path = url.path
        if path == "/v1.0/me/drive":
            return httpx.Response(200, json={"id": "D1", "driveType": "business"})
        if path == "/v1.0/drives/D1/root":
            return httpx.Response(200, json={"id": "ROOT"})
        if path == "/v1.0/drives/D1/root/delta":
            token = url.params.get("token")
            if token and self.gone_next:
                self.gone_next = False
                return httpx.Response(410, json={"error": {"code": "resyncRequired", "message": "expired"}})
            if token:  # changes since that token, as Graph serves them (a replay re-serves them)
                versions, deleted = self.snapshots[token]
                value = [
                    self.raw(i)
                    for i in sorted(self.files)
                    if i not in self.deleted and versions.get(i) != self.version[i]
                ]
                value += [{"id": i, "deleted": {"state": "deleted"}} for i in sorted(self.deleted - deleted)]
            else:
                value = self.all_items()
            self.tokens += 1
            self.snapshots[f"T{self.tokens}"] = (dict(self.version), set(self.deleted))
            return httpx.Response(
                200,
                json={
                    "value": value,
                    "@odata.deltaLink": f"{self.BASE}/drives/D1/root/delta?token=T{self.tokens}",
                },
            )
        m = re.fullmatch(r"/v1\.0/drives/D1/items/([^/]+)/content", path)
        if m:
            return httpx.Response(302, headers={"Location": f"https://download.example.test/{m.group(1)}"})
        return httpx.Response(404, json={"error": {"code": "itemNotFound", "message": path}})


GRAPH_SOURCE = """
[graph]
client_id = "00000000-0000-0000-0000-000000000000"

[[source]]
id = "drive"
kind = "graph_drive"
drive_id = "me"
"""


@pytest.fixture
def graph_env(tmp_path: Path, local_source_dir: Path, fixture_files: dict[str, Path]) -> Iterator[Any]:
    config = config_with(tmp_path, local_source_dir, GRAPH_SOURCE)
    drive = FakeDrive(
        {
            "I1": ("notes.md", b"# Notes\n\nThe purchase order is approved.\n"),
            "I2": ("budget.xlsx", fixture_files["sample.xlsx"].read_bytes()),
        }
    )
    tokens = FakeTokens()
    client = GraphClient(
        tokens,
        user_agent="NONISV|test|agentsync/0",
        transport=httpx.MockTransport(drive.handler),
        sleep=lambda _s: None,
    )
    yield config, drive, tokens, client
    client.close()


def test_h_graph_drive_full_cycle_410_resync_delta_edit_and_delete(graph_env: Any) -> None:
    config, drive, _tokens, client = graph_env
    repo = config.docs_repo
    first = run(config, client=client, only=["drive"])
    assert first.exit_code == 0 and first.commit_sha is not None
    rep = source_report(first, "drive")
    assert (
        rep.pass_kind is not None and rep.pass_kind.value == "full_enumeration" and rep.enumeration_complete
    )
    assert rep.cursor_advanced
    fm, body = page(repo, "mirror/drive/projects/notes.md")
    # audit design-correctness-01: the eTag lives in the manifest, never on the page (a no-op Office save
    # moves it without changing content)
    assert "source_etag" not in fm and fm["source_web_url"].endswith("/notes.md")
    with Manifest(config.state_paths.db) as m:
        notes = m.get_item("drive", "I1")
        assert notes is not None and notes.etag == '"{I1},1"'
    assert "purchase order" in body
    assert (repo / "mirror/drive/projects/budget.xlsx.d/00-index.md").is_file()
    with Manifest(config.state_paths.db) as m:
        cur = m.get_cursor("drive")
        assert cur is not None and cursor_fingerprint(cur.current) == cursor_fingerprint(
            f"{FakeDrive.BASE}/drives/D1/root/delta?token=T1"
        )
    for f in (repo / "_sync" / "STATE.md", repo / "_manifest" / "drive.jsonl", repo / "INDEX.md"):
        assert "token=" not in f.read_text(encoding="utf-8")  # cursor values never reach docs/

    drive.gone_next = True  # the stored cursor expired: 410 -> token-less full enumeration
    second = run(config, client=client, only=["drive"])
    rep2 = source_report(second, "drive")
    assert any("410" in a for a in rep2.alarms)
    assert (
        rep2.pass_kind is not None
        and rep2.pass_kind.value == "full_enumeration"
        and rep2.enumeration_complete
    )
    assert set(counts(second, "drive")) <= {Verdict.UNCHANGED}
    assert second.commit_sha is None and second.changes == ()
    with Manifest(config.state_paths.db) as m:
        cur = m.get_cursor("drive")
        assert cur is not None and cur.current is not None and cur.current.endswith("token=T2")

    drive.version["I1"] = 2
    drive.qx["I1"] = "qx-I1-2"
    drive.files["I1"] = ("notes.md", b"# Notes\n\nThe purchase order is REJECTED.\n")
    drive.deleted.add("I2")
    third = run(config, client=client, only=["drive"])
    assert source_report(third, "drive").pass_kind is not None
    assert source_report(third, "drive").pass_kind.value == "delta"
    ops = sorted((c.op.value, c.path) for c in third.changes)
    assert ("M", "mirror/drive/projects/notes.md") in ops
    assert {p for o, p in ops if o == "D"} >= {"mirror/drive/projects/budget.xlsx.d/00-index.md"}
    assert "REJECTED" in page(repo, "mirror/drive/projects/notes.md")[1]
    assert page(repo, "mirror/drive/projects/budget.xlsx.d/00-index.md")[0]["status"] == "deleted"
    assert third.commit_sha is not None and porcelain(repo) == ""


def test_h_reauth_required_holds_the_graph_cursor_but_local_sources_publish(graph_env: Any) -> None:
    config, _drive, tokens, client = graph_env
    tokens.fail = True
    report = run(config, client=client)
    assert report.auth_required and report.exit_code == 77
    drive_rep = source_report(report, "drive")
    assert not drive_rep.cursor_advanced and any("REAUTH_REQUIRED" in e for e in drive_rep.errors)
    assert report.commit_sha is not None  # the local source still published
    assert (config.docs_repo / mirror("projects/sample.docx.md")).is_file()
    with Manifest(config.state_paths.db) as m:
        src = m.get_source("drive")
        assert src is not None and src.auth_state == "REAUTH_REQUIRED"
        cur = m.get_cursor("drive")
        assert cur is None or (cur.current is None and cur.pending is None)
    state_md = (config.docs_repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "auth: REAUTH_REQUIRED" in state_md and "auth_required: yes" in state_md
    tokens.fail = False
    healed = run(config, client=client)
    assert healed.exit_code == 0 and not healed.auth_required
    with Manifest(config.state_paths.db) as m:
        src = m.get_source("drive")
        assert src is not None and src.auth_state == "ok"


def test_outputs_rows_match_pages_after_the_whole_story(synced: Config, local_source_dir: Path) -> None:
    """Every outputs row points at a file that hashes to its page_sha256 (the repair invariant)."""
    (local_source_dir / "projects" / "sample.txt").write_text("changed\n", encoding="utf-8")
    (local_source_dir / "projects" / "sample.pdf").rename(local_source_dir / "projects" / "renamed.pdf")
    (local_source_dir / "projects" / "sample.csv").unlink()
    run(synced)
    run(synced)  # a deletion is tombstoned by the cycle that confirms it, the second one
    repo = synced.docs_repo
    seen = set()
    with Manifest(synced.state_paths.db) as m:
        for out in m.iter_outputs():
            data = (repo / out.output_path).read_bytes()
            assert hashlib.sha256(data).hexdigest() == out.page_sha256, out.output_path
            seen.add(out.status)
            if out.status is OutputStatus.TOMBSTONE:
                assert m.get_tombstone(out.output_path) is not None
    assert OutputStatus.TOMBSTONE in seen, "the story includes a tombstone row, or the check above is dead"
    assert gitops.has_changes(repo) is False


# ---------------------------------------------------------------------------------------------------------
# hardening wiring (C15 section 9) through run_cycle: network gate, blocked sign-in, labels, governance
# ---------------------------------------------------------------------------------------------------------

LABEL = "2096f6a2-d2f7-48be-b329-b73aaa526e5d"
SITE = "72f988bf-86f1-41af-91ab-2d7cd011db47"


def label_ooxml(src: Path, dest: Path, label_id: str = LABEL, name: str = "Highly Confidential") -> Path:
    """Copy an OOXML package and add a docProps/custom.xml carrying an MSIP label (the pre-LabelInfo form)."""
    fmtid = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
    props = "".join(
        f'<property fmtid="{fmtid}" pid="{i}" name="MSIP_Label_{label_id}_{k}">'
        f"<vt:lpwstr>{v}</vt:lpwstr></property>"
        for i, (k, v) in enumerate((("Enabled", "true"), ("SiteId", SITE), ("Name", name)), start=2)
    )
    custom = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        + props
        + "</Properties>"
    )
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename != "docProps/custom.xml":
                zout.writestr(info, zin.read(info.filename))
        zout.writestr("docProps/custom.xml", custom)
    return dest


def test_label_policy_refuses_a_labelled_file_and_converts_the_rest(
    tmp_path: Path, local_source_dir: Path, fixture_files: dict[str, Path]
) -> None:
    """C15 req 27 end to end: [policy] exclude_label_ids -> a REFUSED stub, row REFUSED, never converted."""
    label_ooxml(fixture_files["sample.docx"], local_source_dir / "projects" / "Board Pack.docx")
    config = config_with(tmp_path, local_source_dir, f'\n[policy]\nexclude_label_ids = ["{LABEL}"]\n')
    report = run(config)
    assert report.exit_code == 0, report
    assert counts(report).get(Verdict.REFUSED) == 1
    fm, body = page(config.docs_repo, mirror("projects/board-pack.docx.md"))
    assert fm["status"] == "refused" and "Highly Confidential" not in body.split("refused:")[0]
    assert "excluded by [policy]" in body and "Kickoff" not in body
    fm_ok, body_ok = page(config.docs_repo, mirror("projects/sample.docx.md"))
    assert fm_ok["status"] != "refused" and len(body_ok) > 200  # the same content, unlabelled, converts
    with Manifest(config.state_paths.db) as m:
        row = next(r for r in m.iter_items(SID) if r.rel_path == "projects/Board Pack.docx")
        assert row.state is RowState.REFUSED
    assert run(config).commit_sha is None  # settled: the refusal is not retried every cycle


class _GateAuth:
    """MsalAuth stand-in for the cycle's own client (no MSAL, no Keychain)."""

    def __init__(self, settings: Any) -> None:
        self.last_token_source: str | None = None

    def get_token(self) -> str:
        return "token"

    def status(self) -> Any:
        return type("S", (), {"sign_in_method": "loopback"})()


@pytest.mark.parametrize(
    ("state", "failed"), [("network-policy: TLS", True), ("network-policy: proxy", True), ("offline", False)]
)
def test_reachability_gate_fails_on_network_policy_and_skips_when_offline(
    tmp_path: Path, local_source_dir: Path, monkeypatch: pytest.MonkeyPatch, state: str, failed: bool
) -> None:
    """C15 req 34: a certificate/proxy failure is ``failed: network-policy (...)``, never ``skipped``."""
    from agentsync import cycle, net  # noqa: PLC0415

    config = config_with(
        tmp_path, local_source_dir, GRAPH_SOURCE.replace("[graph]\n", '[graph]\ntenant = "contoso.com"\n')
    )
    probes: list[str] = []

    def fake_probe(url: str, settings: net.ProxySettings) -> net.Reachability:
        probes.append(url)
        return net.Reachability(state, "certificate verify failed" if failed else "ConnectError", "direct")

    monkeypatch.setattr(cycle, "MsalAuth", _GateAuth)
    monkeypatch.setattr(net, "probe_reachability", fake_probe)
    report = run(config)
    drive = source_report(report, "drive")
    assert probes == [config.graph.base_url]
    assert drive.pass_kind is None and not drive.cursor_advanced
    assert report.commit_sha is not None  # the local source still published
    if failed:
        assert report.exit_code == 1 and any(e.startswith(f"failed: {state}") for e in drive.errors)
    else:
        assert report.exit_code == 0 and drive.skipped_reason is not None
        assert drive.skipped_reason.startswith("offline") and not drive.errors
    state_md = (config.docs_repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "## Graph sign-in" in state_md and "sign_in_method: loopback" in state_md
    assert (
        "token_source: none this run" in state_md
        and f"network: {'failed' if failed else 'offline'}" in state_md
    )


def drive_errors(report: CycleReport) -> tuple[str, ...]:
    return tuple(source_report(report, "drive").errors)


def test_blocked_sign_in_is_named_in_the_manifest_and_state(graph_env: Any) -> None:
    """C15 1.5: a tenant decision is REAUTH_REQUIRED (blocked: ...) and holds the cursor (exit 77)."""
    from agentsync.graph.errors import AuthBlockedError  # noqa: PLC0415

    config, _drive, tokens, client = graph_env

    def blocked() -> str:
        raise AuthBlockedError("blocked: device", "AADSTS53000", "REAUTH_REQUIRED (blocked: device): enrol")

    tokens.get_token = blocked
    report = run(config, client=client, only=["drive"])
    assert report.exit_code == 77 and report.auth_required
    with Manifest(config.state_paths.db) as m:
        src = m.get_source("drive")
        assert src is not None and src.auth_state == "REAUTH_REQUIRED"
    assert any(
        e.startswith("auth REAUTH_REQUIRED (blocked: device, AADSTS53000)") for e in drive_errors(report)
    )
    state_md = (config.docs_repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert "auth: REAUTH_REQUIRED" in state_md
    assert "error: `auth REAUTH_REQUIRED (blocked: device, AADSTS53000)" in state_md


def test_confirmed_deletion_queues_a_purge_that_removes_the_tombstone(
    synced: Config, local_source_dir: Path
) -> None:
    """C15 req 38: a confirmed upstream deletion enqueues a purge; running it leaves no page, no blob and no
    `git show` recovery hint for the item, and the queue is empty afterwards."""
    from agentsync import governance  # noqa: PLC0415

    repo = synced.docs_repo
    rel = mirror("projects/sample.csv.md")
    blob = git(repo, "rev-parse", f"HEAD:{rel}").strip()
    (local_source_dir / "projects" / "sample.csv").unlink()
    assert run(synced).changes == ()  # first absence: held one pass
    assert governance.pending_purges(synced.state_paths.root) == []
    report = run(synced)
    assert [(c.op, c.path) for c in report.changes] == [(ChangeOp.DELETED, rel)]
    queued = governance.pending_purges(synced.state_paths.root)
    assert [(q.selector.source_id, q.reason) for q in queued] == [
        (SID, governance.PurgeReason.UPSTREAM_DELETED)
    ]
    assert "## Queued purges" in (repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    (rep,) = governance.run_purge_queue(synced, now=NOW)
    assert rep.verified and rel in rep.docs_paths
    assert governance.pending_purges(synced.state_paths.root) == []
    assert not (repo / rel).exists()
    gone = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", blob], capture_output=True, check=False)
    assert gone.returncode != 0
    assert "sample.csv" not in git(repo, "log", "--all", "-p")
    assert run(synced).exit_code == 0 and porcelain(repo) == ""
