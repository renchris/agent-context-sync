"""arm_local: the T3 metadata walk (zero file opens), identity, inbox rules, fetch through materialise."""

from __future__ import annotations

import builtins
import contextlib
import dataclasses
import errno
import hashlib
import io
import os
import pwd
import re
import stat
import sys
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentsync import arm_local as al
from agentsync import materialise as mat
from agentsync.config import DEFAULT_EXCLUDES, SourceConfig
from agentsync.errors import BudgetExhaustedError, ConfigError, MaterialiseError
from agentsync.materialise import SF_DATALESS, is_dataless
from agentsync.model import ByteBudget, PassKind, SourceArm, SourceItem, SourceKind

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only syscalls")

UUID_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")
VOL = "00000000-0000-0000-0000-00000000TEST"


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _walk(root: Path, **kw: object) -> tuple[list[SourceItem], al.WalkStats]:
    args: dict[str, object] = {"source_id": "s", "volume": VOL, "include": (), "exclude": ()}
    args.update(kw)
    return al.walk(root, **args)  # type: ignore[arg-type]


def _rels(items: list[SourceItem]) -> list[str]:
    return [i.rel_path for i in items]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    _write(root / "README.txt", b"sentinel\n")
    _write(root / "b.docx", b"docx bytes")
    _write(root / "a" / "z.md", b"# z\n")
    _write(root / "a" / "deep" / "y.xlsx", b"xlsx")
    _write(root / "~$b.docx", b"lock")
    _write(root / ".DS_Store", b"ds")
    return root


def _cfg(root: Path | None, **kw: object) -> SourceConfig:
    base: dict[str, object] = {"id": "local-test", "kind": SourceKind.LOCAL, "path": root}
    base.update(kw)
    return SourceConfig(**base)  # type: ignore[arg-type]


@contextlib.contextmanager
def _chmod(path: Path, mode: int) -> Iterator[None]:
    old = stat.S_IMODE(os.lstat(path).st_mode)
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(old)


# ---------------------------------------------------------------------------------------------------------
# identity: volume UUID, gen count, stable id
# ---------------------------------------------------------------------------------------------------------


def test_stable_id_is_volume_colon_inode() -> None:
    assert al.stable_id_for("F5F6B7E4-007D-4621-A8F2-3F7522194BC9", 1291683029) == (
        "F5F6B7E4-007D-4621-A8F2-3F7522194BC9:1291683029"
    )


def test_volume_uuid_is_an_uppercase_uuid_shared_by_the_volume(tmp_path: Path) -> None:
    f = _write(tmp_path / "d" / "f.txt")
    v = al.volume_uuid(tmp_path)
    assert UUID_RE.match(v)
    assert al.volume_uuid(f) == v
    assert al.volume_uuid(f.parent) == v


def test_volume_uuid_diskutil_fallback_agrees(tmp_path: Path) -> None:
    assert al._diskutil_volume_uuid(tmp_path) == al.volume_uuid(tmp_path)


def test_volume_uuid_falls_back_when_getattrlist_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path) -> str:
        raise OSError(errno.ENOTSUP, "no uuid")

    monkeypatch.setattr(al, "_getattrlist_volume_uuid", broken)
    monkeypatch.setattr(al, "_diskutil_volume_uuid", lambda p: "ABCDEF01-0000-0000-0000-000000000001")
    assert al.volume_uuid(tmp_path) == "ABCDEF01-0000-0000-0000-000000000001"


def test_volume_uuid_raises_when_neither_source_yields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path) -> str:
        raise OSError(errno.ENOTSUP, "no uuid")

    monkeypatch.setattr(al, "_getattrlist_volume_uuid", broken)
    monkeypatch.setattr(al, "_diskutil_volume_uuid", broken)
    with pytest.raises(OSError):
        al.volume_uuid(tmp_path)


def test_volume_uuid_missing_path_is_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        al.volume_uuid(tmp_path / "nope")


def test_gen_count_rises_on_data_write_not_on_chmod(tmp_path: Path) -> None:
    f = _write(tmp_path / "g.txt", b"aaaa")
    g0 = al.gen_count(f)
    assert isinstance(g0, int) and g0 > 0
    f.chmod(0o600)
    assert al.gen_count(f) == g0  # metadata-only change: counter holds (C11 / design 4.2)
    st = os.lstat(f)
    with f.open("r+b") as fh:  # same-size in-place overwrite, mtime rolled back afterwards
        fh.write(b"bbbb")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    g1 = al.gen_count(f)
    assert g1 is not None and g1 > g0
    assert os.lstat(f).st_mtime_ns == st.st_mtime_ns


def test_gen_count_zero_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = _write(tmp_path / "z.txt")
    monkeypatch.setattr(al, "_file_attrs", lambda p: al._FileAttrs(gen_count=None, created_ns=None))
    assert al.gen_count(f) is None


def test_gen_count_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        al.gen_count(tmp_path / "missing")


def test_file_attrs_creation_time_matches_birthtime(tmp_path: Path) -> None:
    f = _write(tmp_path / "c.txt")
    attrs = al._file_attrs(f)
    assert attrs.created_ns is not None
    assert abs(attrs.created_ns - round(os.lstat(f).st_birthtime * 1e9)) < 1000


# ---------------------------------------------------------------------------------------------------------
# walk
# ---------------------------------------------------------------------------------------------------------


def test_walk_emits_files_sorted_with_full_stat_tuple(tree: Path) -> None:
    items, stats = _walk(tree, exclude=("~$*", ".DS_Store"))
    assert _rels(items) == sorted(["README.txt", "a/deep/y.xlsx", "a/z.md", "b.docx"])
    for item in items:
        st = os.lstat(tree / item.rel_path)
        assert item.source_id == "s"
        assert item.stable_id == f"{VOL}:{st.st_ino}"
        assert item.name == item.rel_path.rsplit("/", 1)[-1]
        assert (item.size, item.mtime_ns, item.ctime_ns, item.ino, item.mode) == (
            st.st_size,
            st.st_mtime_ns,
            st.st_ctime_ns,
            st.st_ino,
            st.st_mode,
        )
        assert item.is_dir is False
        assert item.dataless is False
        assert isinstance(item.gen_count, int) and item.gen_count > 0
        assert item.created_ns is not None
        assert item.parent_id is None and item.etag is None and not item.deleted
    assert stats == al.WalkStats(
        files=4, dirs=3, dataless=0, excluded=2, symlinks_skipped=0, unknown_dirs=(), sentinel_present=None
    )


def test_walk_is_deterministic(tree: Path) -> None:
    assert _walk(tree) == _walk(tree)


def test_walk_never_opens_a_file(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"walk opened a file: {args!r}")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(io, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    items, _ = _walk(tree)
    assert len(items) == 6


def test_walk_does_not_touch_atime(tree: Path) -> None:
    f = tree / "b.docx"
    os.utime(f, ns=(1_000_000_000, os.lstat(f).st_mtime_ns))
    _walk(tree)
    assert os.lstat(f).st_atime_ns == 1_000_000_000


def test_walk_skips_symlinks_and_never_follows_them(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    _write(outside / "secret.docx")
    _write(root / "real.txt")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    (root / "link.txt").symlink_to(root / "real.txt")
    (root / "dangling").symlink_to(tmp_path / "nowhere")
    items, stats = _walk(root)
    assert _rels(items) == ["real.txt"]
    assert stats.symlinks_skipped == 3


def test_walk_normalises_names_to_nfc(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nfd = unicodedata.normalize("NFD", "Café Résumé.docx")
    _write(root / unicodedata.normalize("NFD", "Données") / nfd)
    items, _ = _walk(root)
    assert _rels(items) == ["Données/Café Résumé.docx"]
    assert unicodedata.is_normalized("NFC", items[0].rel_path)
    assert items[0].name == "Café Résumé.docx"


def test_exclude_prunes_directories_include_applies_to_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write(root / "keep" / "a.docx")
    _write(root / "keep" / "a.txt")
    _write(root / "build" / "b.docx")
    _write(root / "node_modules" / "c.docx")
    _write(root / "deep" / "x" / "d.docx")
    items, stats = _walk(root, include=("*.docx",), exclude=("build/", "node_modules"))
    assert _rels(items) == ["deep/x/d.docx", "keep/a.docx"]
    assert stats.excluded == 3  # build/, node_modules/, keep/a.txt


def test_default_excludes_drop_office_lock_files(tree: Path) -> None:
    items, _ = _walk(tree, exclude=DEFAULT_EXCLUDES)
    assert "~$b.docx" not in _rels(items)
    assert ".DS_Store" not in _rels(items)


def test_non_regular_files_are_excluded(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write(root / "a.txt")
    os.mkfifo(root / "pipe")
    items, stats = _walk(root)
    assert _rels(items) == ["a.txt"]
    assert stats.excluded == 1


def test_hard_links_keep_one_identity(tmp_path: Path) -> None:
    root = tmp_path / "root"
    f = _write(root / "b" / "orig.txt")
    os.link(f, root / "a-link.txt")
    items, stats = _walk(root)
    assert _rels(items) == ["a-link.txt"]  # first by rel_path
    assert stats.excluded == 1
    assert len({i.stable_id for i in items}) == len(items)


def test_permission_denied_dir_is_unknown_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write(root / "ok.txt")
    locked = root / "locked"
    _write(locked / "hidden.txt")
    with _chmod(locked, 0):
        items, stats = _walk(root)
    assert _rels(items) == ["ok.txt"]
    assert stats.unknown_dirs == ("locked",)


def test_provider_errors_on_listing_make_the_dir_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    _write(root / "ok.txt")
    _write(root / "slow" / "s.txt")
    real = al._list_dir

    def flaky(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        if path.name == "slow":
            raise OSError(errno.ETIMEDOUT, os.strerror(errno.ETIMEDOUT), str(path))
        return real(path, dir_dataless=dir_dataless)

    monkeypatch.setattr(al, "_list_dir", flaky)
    items, stats = _walk(root)
    assert _rels(items) == ["ok.txt"]
    assert stats.unknown_dirs == ("slow",)


def test_empty_dir_outside_cloud_tree_is_just_empty(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "empty").mkdir(parents=True)
    _write(root / "a.txt")
    _, stats = _walk(root)
    assert stats.unknown_dirs == ()


def test_zero_child_dir_in_cloud_tree_is_unknown(tmp_path: Path) -> None:
    home = Path(os.environ["HOME"])  # conftest isolates HOME per test
    root = home / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    (root / "never-listed").mkdir(parents=True)
    _write(root / "a.docx")
    items, stats = _walk(root)
    assert _rels(items) == ["a.docx"]
    assert stats.unknown_dirs == ("never-listed",)


def test_empty_cloud_root_is_unknown(tmp_path: Path) -> None:
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test"
    root.mkdir(parents=True)
    items, stats = _walk(root)
    assert items == []
    assert stats.unknown_dirs == (".",)


def test_excluded_empty_cloud_dir_is_not_unknown(tmp_path: Path) -> None:
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test"
    (root / "Attachments").mkdir(parents=True)
    _write(root / "a.docx")
    _, stats = _walk(root, exclude=("Attachments/",))
    assert stats.unknown_dirs == ()


def test_dataless_files_are_recorded_not_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    online_only = _write(root / "online-only.docx", b"would-be-downloaded")
    _write(root / "local.docx")
    target = os.lstat(online_only).st_ino
    monkeypatch.setattr(al, "is_dataless", lambda st: st.st_ino == target)
    items, stats = _walk(root)
    by = {i.rel_path: i for i in items}
    assert by["online-only.docx"].dataless is True
    assert by["local.docx"].dataless is False
    assert stats.dataless == 1


def test_dataless_directories_are_listed_under_thread_policy_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    sub = root / "fresh"
    _write(sub / "Document.docx")
    _write(root / "plain" / "p.txt")
    target = os.lstat(sub).st_ino
    allowed: list[str] = []

    @contextlib.contextmanager
    def spy() -> Iterator[None]:
        allowed.append("on")
        yield

    monkeypatch.setattr(al, "is_dataless", lambda st: st.st_ino == target)
    monkeypatch.setattr(al, "materialize_allowed", spy)
    items, _ = _walk(root)
    assert _rels(items) == ["fresh/Document.docx", "plain/p.txt"]
    assert allowed == ["on"]  # only the dataless directory's listing


def test_without_gen_count(tree: Path) -> None:
    items, _ = _walk(tree, with_gen_count=False)
    assert all(i.gen_count is None for i in items)
    assert all(i.created_ns is not None for i in items)


def test_file_vanishing_mid_walk_is_dropped(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = al._file_attrs

    def racy(path: Path) -> al._FileAttrs:
        if path.name == "b.docx":
            raise FileNotFoundError(errno.ENOENT, "gone", str(path))
        return real(path)

    monkeypatch.setattr(al, "_file_attrs", racy)
    items, _ = _walk(tree)
    assert "b.docx" not in _rels(items)


def test_walk_root_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _walk(tmp_path / "missing")
    f = _write(tmp_path / "file.txt")
    with pytest.raises(NotADirectoryError):
        _walk(f)
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(NotADirectoryError, match="symlink"):
        _walk(tmp_path / "link")


# ---------------------------------------------------------------------------------------------------------
# fold_conflict_suffix
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("Report (1).docx", "Report.docx"),
        ("Report (12).docx", "Report.docx"),
        ("Report - Copy.docx", "Report.docx"),
        ("Report - Copy (2).docx", "Report.docx"),
        ("Report copy.docx", "Report.docx"),
        ("Report copy 2.docx", "Report.docx"),
        ("Report (1) - Copy.docx", "Report.docx"),
        ("Budget-DESKTOP-AB12CD3.xlsx", "Budget.xlsx"),
        ("Budget-LAPTOP-5KJ3K2Q.xlsx", "Budget.xlsx"),
        ("fy26-budget.xlsx", "fy26-budget.xlsx"),
        ("Q3-plan-final.docx", "Q3-plan-final.docx"),
        ("README (1)", "README"),
        (".bashrc", ".bashrc"),
        ("(1).txt", "(1).txt"),
        (unicodedata.normalize("NFD", "Café (1).md"), "Café.md"),
    ],
)
def test_fold_conflict_suffix(name: str, want: str) -> None:
    assert al.fold_conflict_suffix(name) == want


def test_fold_conflict_suffix_strips_this_macs_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(al, "_local_host_names", lambda: ("Chris-MacBook-Pro",))
    assert al.fold_conflict_suffix("Plan-Chris-MacBook-Pro.docx") == "Plan.docx"
    assert al.fold_conflict_suffix("Plan-chris-macbook-pro (1).docx") == "Plan.docx"
    assert al.fold_conflict_suffix("Plan-Other-Mac.docx") == "Plan-Other-Mac.docx"


# ---------------------------------------------------------------------------------------------------------
# LocalArm
# ---------------------------------------------------------------------------------------------------------


def test_local_arm_rejects_non_local_kinds(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        al.LocalArm(_cfg(tmp_path, kind=SourceKind.GRAPH_DRIVE, drive_id="me"))
    with pytest.raises(ConfigError):
        al.LocalArm(_cfg(None))


def test_local_arm_satisfies_the_protocol(tree: Path) -> None:
    arm: SourceArm = al.LocalArm(_cfg(tree))
    assert (arm.source_id, arm.kind) == ("local-test", SourceKind.LOCAL)


def test_scan_is_a_complete_full_pass(tree: Path) -> None:
    arm = al.LocalArm(_cfg(tree, sentinel="README.txt"))
    res = arm.scan("ignored-cursor", full=False)
    assert res.source_id == "local-test"
    assert res.pass_kind is PassKind.FULL
    assert res.new_cursor is None
    assert res.enumeration_complete is True
    assert res.alarms == () and res.unknown_dirs == ()
    assert [i.rel_path for i in res.items] == ["README.txt", "a/deep/y.xlsx", "a/z.md", "b.docx"]
    vol = al.volume_uuid(tree)
    assert all(i.stable_id.startswith(vol + ":") and i.source_id == "local-test" for i in res.items)
    assert arm.last_stats is not None
    assert arm.last_stats.sentinel_present is True
    assert arm.last_stats.excluded == 2  # DEFAULT_EXCLUDES: ~$b.docx, .DS_Store


def test_scan_without_sentinel_reports_none(tree: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    assert arm.scan(None, full=True).enumeration_complete is True
    assert arm.last_stats is not None and arm.last_stats.sentinel_present is None


def test_missing_sentinel_makes_the_pass_incomplete(tree: Path) -> None:
    (tree / "README.txt").unlink()
    arm = al.LocalArm(_cfg(tree, sentinel="README.txt"))
    res = arm.scan(None, full=True)
    assert res.enumeration_complete is False
    assert any("sentinel" in a for a in res.alarms)
    assert len(res.items) == 3


def test_directory_sentinel(tree: Path) -> None:
    res = al.LocalArm(_cfg(tree, sentinel="a/deep")).scan(None, full=True)
    assert res.enumeration_complete is True


def test_missing_root_is_empty_and_incomplete(tmp_path: Path) -> None:
    arm = al.LocalArm(_cfg(tmp_path / "unmounted"))
    res = arm.scan(None, full=True)
    assert res.items == ()
    assert res.enumeration_complete is False
    assert res.pass_kind is PassKind.FULL
    assert any("missing" in a for a in res.alarms)
    assert arm.last_stats is None


def test_symlinked_root_is_refused(tmp_path: Path, tree: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tree, target_is_directory=True)
    res = al.LocalArm(_cfg(link)).scan(None, full=True)
    assert res.items == () and not res.enumeration_complete
    assert any("symlink" in a for a in res.alarms)


def test_unknown_dirs_make_the_pass_incomplete(tree: Path) -> None:
    with _chmod(tree / "a", 0):
        res = al.LocalArm(_cfg(tree, sentinel="README.txt")).scan(None, full=True)
    assert res.enumeration_complete is False
    assert res.unknown_dirs == ("a",)
    assert any("unknown dir" in a for a in res.alarms)


def test_volume_failure_is_incomplete(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path) -> str:
        raise OSError(errno.ENOTSUP, "no uuid")

    monkeypatch.setattr(al, "volume_uuid", broken)
    res = al.LocalArm(_cfg(tree)).scan(None, full=True)
    assert res.items == () and not res.enumeration_complete
    assert any("volume UUID" in a for a in res.alarms)


def test_rename_keeps_identity_safe_save_changes_it(tree: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    before = {i.rel_path: i for i in arm.scan(None, full=True).items}
    (tree / "b.docx").rename(tree / "renamed.docx")
    tmp = _write(tree / "a" / ".~z.md.tmp", b"# z v2\n")
    tmp.replace(tree / "a" / "z.md")  # Office-style safe-save: new inode, same path
    after = {i.rel_path: i for i in arm.scan(None, full=True).items}
    assert after["renamed.docx"].stable_id == before["b.docx"].stable_id
    assert after["a/z.md"].stable_id != before["a/z.md"].stable_id


def test_fetch_stages_exact_bytes(tree: Path, tmp_path: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    item = next(i for i in arm.scan(None, full=True).items if i.rel_path == "b.docx")
    budget = ByteBudget(1000, 10)
    staging = tmp_path / "staging"
    res = arm.fetch(item, staging, budget)
    assert res.stable_id == item.stable_id
    assert res.path.name == "b.docx"  # original name kept: converters route by suffix
    assert staging in res.path.parents
    assert res.path.read_bytes() == b"docx bytes"
    assert res.size == 10
    assert res.content_sha256 == hashlib.sha256(b"docx bytes").hexdigest()
    assert budget.used == 10
    # distinct items never share a staging path
    other = next(i for i in arm.scan(None, full=True).items if i.rel_path == "a/z.md")
    assert arm.fetch(other, staging, budget).path.parent != res.path.parent


def test_fetch_after_safe_save_is_file_not_found(tree: Path, tmp_path: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    item = next(i for i in arm.scan(None, full=True).items if i.rel_path == "b.docx")
    _write(tree / "new.tmp", b"v2").replace(tree / "b.docx")
    budget = ByteBudget(1000, 10)
    with pytest.raises(FileNotFoundError):
        arm.fetch(item, tmp_path / "staging", budget)
    assert budget.used == 0


def test_fetch_of_deleted_file_is_file_not_found(tree: Path, tmp_path: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    item = next(i for i in arm.scan(None, full=True).items if i.rel_path == "b.docx")
    (tree / "b.docx").unlink()
    with pytest.raises(FileNotFoundError):
        arm.fetch(item, tmp_path / "staging", ByteBudget(1000, 10))


def test_fetch_respects_the_budget(tree: Path, tmp_path: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    item = next(i for i in arm.scan(None, full=True).items if i.rel_path == "b.docx")
    with pytest.raises(BudgetExhaustedError):
        arm.fetch(item, tmp_path / "staging", ByteBudget(5, 10))
    assert not (tmp_path / "staging").exists() or not any((tmp_path / "staging").rglob("*.docx"))


def test_fetch_rejects_path_escapes_and_foreign_items(tree: Path, tmp_path: Path) -> None:
    arm = al.LocalArm(_cfg(tree))
    item = next(i for i in arm.scan(None, full=True).items if i.rel_path == "b.docx")
    with pytest.raises(MaterialiseError):
        arm.fetch(dataclasses.replace(item, rel_path="../outside.txt"), tmp_path / "s", ByteBudget(99, 9))
    with pytest.raises(ValueError, match="source"):
        arm.fetch(dataclasses.replace(item, source_id="other"), tmp_path / "s", ByteBudget(99, 9))


# ---------------------------------------------------------------------------------------------------------
# InboxArm
# ---------------------------------------------------------------------------------------------------------


def _inbox(root: Path, *, quiescence_s: int = 60, exclude: tuple[str, ...] = ()) -> al.InboxArm:
    return al.InboxArm(
        _cfg(root, id="inbox", kind=SourceKind.INBOX, quiescence_s=quiescence_s, exclude=exclude)
    )


def test_inbox_withholds_files_inside_the_quiescence_window(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    _write(root / "settled.pdf")
    arm = _inbox(root)
    res = arm.scan(None, full=True)  # written just now: every file is inside the window
    assert res.items == ()
    assert res.enumeration_complete is False
    assert any("withheld" in a for a in res.alarms)
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    res = arm.scan(None, full=True)
    assert [i.rel_path for i in res.items] == ["settled.pdf"]
    assert res.enumeration_complete is True
    assert res.alarms == ()


def test_inbox_reports_max_of_created_and_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "inbox"
    old_mtime = 1_000_000_000_000_000_000  # 2001: a copied file keeps its original mtime
    f = _write(root / "copied-in.docx")
    os.utime(f, ns=(old_mtime, old_mtime))
    # APFS pulls birthtime back with an older mtime, so present the copy's fresh birthtime explicitly.
    created = time.time_ns() - 300 * 1_000_000_000
    monkeypatch.setattr(al, "_file_attrs", lambda p: al._FileAttrs(gen_count=7, created_ns=created))
    arm = _inbox(root)
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    (item,) = arm.scan(None, full=True).items
    assert os.lstat(f).st_mtime_ns == old_mtime
    assert item.created_ns == created
    assert item.mtime_ns == created  # max(created, modified)
    assert item.extra == {"dedup_name": "copied-in.docx"}


def test_inbox_keeps_mtime_when_it_is_the_later_time(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    f = _write(root / "edited.docx")
    arm = _inbox(root)
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    (item,) = arm.scan(None, full=True).items
    assert item.created_ns is not None
    assert item.mtime_ns == max(item.created_ns, os.lstat(f).st_mtime_ns)


def test_inbox_always_ignores_lock_and_partial_files(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    for name in (
        "~$draft.docx",
        "draft.docx.tmp",
        ".~lock.sheet.ods#",
        "big.zip.crdownload",
        "x.part",
        "ok.docx",
    ):
        _write(root / name)
    arm = _inbox(root, exclude=())  # even with an empty configured exclude list
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    assert [i.rel_path for i in arm.scan(None, full=True).items] == ["ok.docx"]


def test_inbox_dedup_name_folds_conflict_suffixes(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    _write(root / "Plan (1).docx")
    arm = _inbox(root)
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    (item,) = arm.scan(None, full=True).items
    assert item.name == "Plan (1).docx"
    assert item.extra["dedup_name"] == "Plan.docx"


def test_inbox_fetch_two_reads_agree(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    _write(root / "a.pdf", b"%PDF-1.4 stable")
    arm = _inbox(root)
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    (item,) = arm.scan(None, full=True).items
    res = arm.fetch(item, tmp_path / "staging", ByteBudget(1000, 5))
    assert res.content_sha256 == hashlib.sha256(b"%PDF-1.4 stable").hexdigest()


def test_inbox_fetch_two_reads_disagree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "inbox"
    _write(root / "a.pdf", b"%PDF-1.4 moving")
    arm = _inbox(root)
    arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000
    (item,) = arm.scan(None, full=True).items
    monkeypatch.setattr(al, "sha256_file", lambda p: "0" * 64)
    with pytest.raises(MaterialiseError, match="two reads"):
        arm.fetch(item, tmp_path / "staging", ByteBudget(1000, 5))
    assert not any((tmp_path / "staging").rglob("a.pdf"))


# ---------------------------------------------------------------------------------------------------------
# opt-in: the live OneDrive File Provider mount, READ-ONLY
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENTSYNC_FP_TEST") != "1", reason="set AGENTSYNC_FP_TEST=1 (reads OneDrive)"
)
def test_fileprovider_walk_is_metadata_only() -> None:
    probe = (
        Path(pwd.getpwuid(os.getuid()).pw_dir)
        / "Library"
        / "CloudStorage"
        / "OneDrive-Contoso"
        / "agentsync-probe"
    )
    if not probe.is_dir():
        pytest.skip(f"File Provider probe folder not present: {probe}")

    def snapshot() -> dict[str, int]:
        out: dict[str, int] = {}
        for dirpath, _dirs, files in os.walk(probe):  # os.walk: scandir + lstat, never opens files
            for f in files:
                p = Path(dirpath) / f
                out[p.relative_to(probe).as_posix()] = os.lstat(p).st_flags
        return out

    before = snapshot()
    prior = mat.get_materialize_policy(mat.IOPOL_SCOPE_THREAD)
    mat.set_materialize_policy(
        mat.IOPOL_MATERIALIZE_DATALESS_FILES_OFF, mat.IOPOL_SCOPE_THREAD
    )  # fail closed
    try:
        vol = al.volume_uuid(probe)
        t0 = time.perf_counter()
        items, stats = al.walk(probe, source_id="fp-probe", volume=vol, include=(), exclude=())
        elapsed = time.perf_counter() - t0
    finally:
        mat.set_materialize_policy(prior, mat.IOPOL_SCOPE_THREAD)
    after = snapshot()

    print(f"\nfileprovider walk: {stats} in {elapsed:.4f}s")
    by = {i.rel_path: i for i in items}
    assert stats.unknown_dirs == ()
    assert stats.files == len(items) >= 2000
    assert {"blob.bin", "doc.docx", "note.txt", "note2.txt", "fresh/Document.docx"} <= set(by)
    assert len({i.stable_id for i in items}) == len(items)
    assert all(i.stable_id.startswith(vol + ":") for i in items)
    assert all(i.gen_count is not None for i in items)  # C14 §3: GEN_COUNT returned for every FP item
    # dataless recorded exactly as lstat reports it, and nothing was hydrated by the walk
    assert stats.dataless == sum(1 for f in before.values() if f & SF_DATALESS)
    for rel, item in by.items():
        assert item.dataless == bool(before[rel] & SF_DATALESS)
    assert {r: f & SF_DATALESS for r, f in after.items()} == {r: f & SF_DATALESS for r, f in before.items()}
    assert is_dataless(os.lstat(probe / "fresh" / "Document.docx")) == bool(
        before["fresh/Document.docx"] & SF_DATALESS
    )
