"""arm_local: the T3 metadata walk (zero file opens), identity, inbox rules, fetch through materialise."""

from __future__ import annotations

import builtins
import contextlib
import dataclasses
import errno
import hashlib
import io
import logging
import os
import re
import stat
import sys
import threading
import time
import tomllib
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from agentsync import arm_local as al
from agentsync import cycle, manifest, model
from agentsync import materialise as mat
from agentsync.config import DEFAULT_EXCLUDES, SourceConfig
from agentsync.errors import BudgetExhaustedError, ConfigError, MaterialiseError
from agentsync.materialise import SF_DATALESS, is_dataless
from agentsync.model import ByteBudget, PassKind, SourceArm, SourceItem, SourceKind
from conftest import file_provider_domain

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


def test_a_listing_held_by_a_privacy_prompt_times_out_as_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Field report N8: macOS holds a listing until someone clicks Allow. The walk stops at its time limit and
    # the scan is incomplete with a "click Allow" alarm, never an empty folder.
    root = tmp_path / "root"
    _write(root / "ok.txt")
    _write(root / "held" / "h.txt")
    real = al._list_dir
    release = threading.Event()

    def held(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        if path.name == "held":
            release.wait(5.0)
        return real(path, dir_dataless=dir_dataless)

    monkeypatch.setattr(al, "_list_dir", held)
    monkeypatch.setattr(al, "LISTING_TIMEOUT_S", 0.2)
    arm = al.LocalArm(_cfg(root))
    try:
        started = time.monotonic()
        with pytest.raises(al.CallTimedOutError, match="'held' did not return"):
            _walk(root, listing_timeout_s=0.2)
        scan = arm.scan(None, full=True)
        assert time.monotonic() - started < 4.0
    finally:
        release.set()
    assert not scan.enumeration_complete and scan.items == ()
    assert any("click Allow" in a and "nothing is deleted" in a for a in scan.alarms), scan.alarms


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


@pytest.mark.skipif(sys.platform != "darwin", reason="off macOS the attribute read fails: a domain")
@pytest.mark.usefixtures("real_domain_check")
def test_an_empty_folder_in_a_plain_provider_folder_is_just_empty() -> None:
    """b13 R1: a provider folder with no File Provider attribute (a sandbox's mkdir, not a domain) lists its
    zero-child folders as empty, so the listing is complete; the rule stays for a real domain."""
    provider = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    root = provider / "Project Alpha"
    (root / "Empty Subfolder").mkdir(parents=True)
    _write(root / "a.txt")
    items, stats = _walk(root)
    assert _rels(items) == ["a.txt"]
    assert stats.unknown_dirs == () and stats.empty_cloud_dirs == ()
    res = al.LocalArm(_cfg(root)).scan(None, full=True)
    assert res.enumeration_complete and res.alarms == ()
    file_provider_domain(provider)
    assert _walk(root)[1].empty_cloud_dirs == ("Empty Subfolder",), "a domain keeps the rule"


@pytest.mark.skipif(sys.platform != "darwin", reason="off macOS the attribute read fails: a domain")
@pytest.mark.usefixtures("real_domain_check")
def test_a_provider_folder_reached_through_a_symlink_or_dot_dot_keeps_the_rule() -> None:
    """b13 R1 review: a provider entry that is a symlink to a domain carries no attribute of its own, and a
    ``..`` in the path names a provider folder it is not in (``~/Library``): both keep the rule, never read
    a folder as known-empty."""
    cloud = Path(os.environ["HOME"]) / "Library" / "CloudStorage"
    real = file_provider_domain(cloud / "OneDrive-Contoso")
    (real / "Proj" / "Empty").mkdir(parents=True)
    _write(real / "Proj" / "a.txt")
    (cloud / "OneDrive-Link").symlink_to(real, target_is_directory=True)
    assert _walk(real / "Proj")[1].empty_cloud_dirs == ("Empty",)
    assert _walk(cloud / "OneDrive-Link" / "Proj")[1].empty_cloud_dirs == ("Empty",)
    dot_dot = cloud / ".." / "CloudStorage" / "OneDrive-Contoso" / "Proj"
    assert _walk(dot_dot)[1].empty_cloud_dirs == ("Empty",)
    # B13 round 6 G40: a link is judged by the provider folder its target sits in. One to a subfolder of a
    # domain keeps the rule (only the domain root carries the marker, so reading the target's own would call
    # it a plain folder and its zero-child folders known-empty); one to a plain folder does not.
    (real / "Team" / "Proj" / "Empty").mkdir(parents=True)
    _write(real / "Team" / "Proj" / "a.txt")
    (cloud / "OneDrive-Team").symlink_to(real / "Team", target_is_directory=True)
    assert _walk(cloud / "OneDrive-Team" / "Proj")[1].empty_cloud_dirs == ("Empty",)
    plain = cloud / "Plain"
    (plain / "Proj" / "Empty").mkdir(parents=True)
    _write(plain / "Proj" / "a.txt")
    (cloud / "OneDrive-Plain").symlink_to(plain, target_is_directory=True)
    assert _walk(plain / "Proj")[1].empty_cloud_dirs == ()
    assert _walk(cloud / "OneDrive-Plain" / "Proj")[1].empty_cloud_dirs == ()
    # a chain of links ends at that folder; a link to the cloud root itself, or one that does not resolve,
    # keeps the rule
    (cloud / "OneDrive-Chain").symlink_to(cloud / "OneDrive-Plain", target_is_directory=True)
    assert _walk(cloud / "OneDrive-Chain" / "Proj")[1].empty_cloud_dirs == ()
    (cloud / "OneDrive-Root").symlink_to(cloud, target_is_directory=True)
    assert al._is_domain(cloud / "OneDrive-Root")
    (cloud / "OneDrive-Loop").symlink_to(cloud / "OneDrive-Loop")
    assert al._is_domain(cloud / "OneDrive-Loop")


@pytest.mark.usefixtures("real_domain_check")
def test_a_home_reached_through_a_symlink_keeps_the_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """b13 R1 review: a source path is stored resolved, so with HOME reached through a symlink (``/tmp`` is
    ``/private/tmp``) the cloud folder's resolved path counts as a cloud tree too; a zero-child folder in a
    domain read as known-empty (why earlier rehearsals never showed the empty-folder wait)."""
    real = tmp_path / "real-home"
    (tmp_path / "home-link").symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home-link"))
    provider = file_provider_domain(real / "Library" / "CloudStorage" / "OneDrive-Contoso")
    (provider / "Proj" / "Empty").mkdir(parents=True)
    _write(provider / "Proj" / "a.txt")
    assert _walk(provider / "Proj")[1].empty_cloud_dirs == ("Empty",)


@pytest.mark.usefixtures("real_domain_check")
def test_a_provider_folder_whose_attributes_cannot_be_read_keeps_the_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """b13 R1, fail-safe: any error reading the provider folder's attributes (EPERM, ENOSYS off macOS) reads
    as a domain, and the marker is read once per provider folder per walk, not once per empty folder."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Projects"
    for name in ("One", "Two", "Three"):
        (root / name).mkdir(parents=True)
    read: list[Path] = []

    def refused(path: Path) -> str | None:
        read.append(path)
        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(path))

    monkeypatch.setattr(al, "file_provider_marker", refused)
    _, stats = _walk(root)
    assert stats.empty_cloud_dirs == ("One", "Three", "Two")
    assert read == [root.parent]


def test_excluded_empty_cloud_dir_is_not_unknown(tmp_path: Path) -> None:
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test"
    (root / "Attachments").mkdir(parents=True)
    _write(root / "a.docx")
    _, stats = _walk(root, exclude=("Attachments/",))
    assert stats.unknown_dirs == ()


def test_zero_child_cloud_dirs_log_at_info_and_the_scan_alarm_names_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bring-back S19: one WARNING per empty folder per poll filled the logs. The scan's one alarm (logged
    and printed once a pass) names them; the per-folder line is info."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    for name in ("One", "Two"):
        (root / name).mkdir(parents=True)
    _write(root / "a.docx")
    with caplog.at_level(logging.INFO, logger=al.__name__):
        res = al.LocalArm(_cfg(root)).scan(None, full=True)
    per_dir = [r for r in caplog.records if "has zero children in a cloud tree" in r.getMessage()]
    assert len(per_dir) == 2 and {r.levelno for r in per_dir} == {logging.INFO}
    assert res.unknown_dirs == ("One", "Two") and not res.enumeration_complete
    (alarm,) = res.alarms
    assert "2 unknown dirs" in alarm and "'One', 'Two'" in alarm
    assert alarm == (
        "2 unknown dirs — 2 empty cloud folders, zero children in a cloud tree: 'One', 'Two'; enumeration "
        "incomplete, no deletions this pass (each WAITING ON YOU line about empty cloud folders in "
        "local-test at the end of a sync's output says what to do, and `~/.local/bin/agentsync status` "
        "prints each one too)"
    )
    # B13 round 9 K41: both folders are on the one exclude-empty wait, so "prints them" told of lines that
    # are not there; "each ... line ... says" and "prints each one" are true for one line or three
    assert "prints them too" not in alarm


_TODAY = (
    "{n} unknown {dirs} — permission denied (TCC), provider error, or zero children in a cloud tree: "
    "{names}; enumeration incomplete, no deletions this pass ({advice})"
)


_DENIED = f"EACCES: {os.strerror(errno.EACCES)}"


def _row(rel: str, state: str = "live") -> object:
    """A stored row of ``known_h0`` with a path and a state."""
    return dataclasses.make_dataclass("Row", ["rel_path", "is_dir", "state"])(rel, False, state)


_MODE = "the folder's own permissions do not let this account read it: check its owner and mode with `ls -ld`"
# ... after "for the folder it could not read," (B13 round 13 O35, O37: "a folder's own permissions" there)
_MODE_NAMED = "its own permissions do not let this account read it: check its owner and mode with `ls -ld`"
_MODES = "their own permissions do not let this account read them: check their owner and mode with `ls -ld`"
"""``_MODE`` for two or more folders (B13 round 12 N36, N37)."""


def test_an_unreadable_folder_with_no_empty_cloud_folder_names_its_reason() -> None:
    """b13 N27: with no empty cloud folder in the walk, the alarm is the only line naming the cause. B13 round
    11 M47: it gave the three-way cause ("... or zero children in a cloud tree") and offered to exclude an
    empty folder, though the walk knew the folder failed with EACCES; it names the reason the walk recorded.
    M42: EACCES is the folder's own mode, which no Files and Folders grant overrides."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    _write(root / "Locked" / "b.docx")
    _write(root / "a.docx")
    with _chmod(root / "Locked", 0):
        res = al.LocalArm(_cfg(root)).scan(None, full=True)
    (alarm,) = res.alarms
    assert alarm == (
        f"1 unknown dir — could not be read: 'Locked' ({_DENIED}); enumeration incomplete, no deletions "
        f"this pass ({_MODE})"
    )
    assert "Files and Folders" not in alarm and "empty" not in alarm
    # the exclude is offered only when it retires nothing: no mirrored file below the folder (B13 round 11
    # review); with no stored rows to tell (known_h0 None, above) it is not offered
    for known, offered in (({}, True), ({"x": _row("Locked/b.docx")}, False)):
        arm = al.LocalArm(_cfg(root))
        arm.known_h0 = known
        with _chmod(root / "Locked", 0):
            (alarm,) = arm.scan(None, full=True).alarms
        assert alarm.endswith(f"({_MODE}, or exclude it)" if offered else f"({_MODE})"), (known, alarm)


def test_a_walk_stats_with_no_recorded_reason_keeps_the_old_alarm(tmp_path: Path) -> None:
    """B13 round 11 M47: a folder with no recorded reason (a WalkStats built elsewhere) keeps the three-way
    cause, byte for byte, since its cause is not known."""
    arm = al.LocalArm(_cfg(tmp_path))
    stats = al.WalkStats(1, 1, 0, 0, 0, unknown_dirs=("Locked",), sentinel_present=None)
    assert arm._unknown_alarm(stats, []) == _TODAY.format(
        dirs="dir",
        n=1,
        names="'Locked'",
        advice="exclude a deliberately empty cloud folder, or grant the agent Files and Folders access",
    )
    root = dataclasses.replace(stats, unknown_dirs=(".",))
    assert arm._unknown_alarm(root, []).endswith(
        "'.'; enumeration incomplete, no deletions this pass (grant the agent Files and Folders access)"
    )


def _refusing(monkeypatch: pytest.MonkeyPatch, codes: dict[str, int]) -> None:
    """``_list_dir`` raising ``codes[name]`` for a folder of that name (EPERM is what TCC returns)."""
    real = al._list_dir

    def refuse(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        if (code := codes.get(path.name)) is not None:
            raise PermissionError(code, os.strerror(code), str(path))
        return real(path, dir_dataless=dir_dataless)

    monkeypatch.setattr(al, "_list_dir", refuse)


def test_a_privacy_refusal_keeps_the_grant_and_a_mixed_one_names_both_fixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 11 M42: EPERM (TCC) keeps the Files and Folders grant; EPERM beside EACCES gives each its
    own fix, and a provider error is the sync app's (no retry promised: the loop's wait says another sync does
    not clear it)."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    for name in ("Private", "Shut", "Slow"):
        _write(root / name / "b.docx")
    _write(root / "a.docx")
    _refusing(monkeypatch, {"Private": errno.EPERM})
    (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    assert alarm == (
        f"1 unknown dir — could not be read: 'Private' (EPERM: {os.strerror(errno.EPERM)}); enumeration "
        "incomplete, no deletions this pass (grant the agent Files and Folders access)"
    )
    _refusing(monkeypatch, {"Private": errno.EPERM, "Shut": errno.EACCES, "Slow": errno.EDEADLK})
    (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    # B13 round 12 N36, N37: one folder of each kind, so each fix is singular
    assert alarm.endswith(
        "(grant the agent Files and Folders access for the one refused with EPERM; for the one refused with "
        "EACCES, its own permissions do not let this account read it: check its owner and mode with "
        "`ls -ld`; check that the other one opens in Finder and that its sync app is running)"
    ), alarm
    assert f"'Shut' ({_DENIED})" in alarm and "'Slow' (EDEADLK: " in alarm


def test_the_read_advice_follows_how_many_folders_have_each_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """B13 round 12 N36, N37: the number followed how many kinds there were, not how many folders: two
    mode-000 folders read "a folder's own permissions do not let this account read it", and one EACCES folder
    beside one EPERM folder read "for those refused with EACCES, ... them". Each fix now takes the number of
    the folders of its kind, with the exclude offered or not as before."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    for name in ("Locked A", "Locked B", "Private", "Slow"):
        _write(root / name / "b.docx")
    _write(root / "a.docx")
    with _chmod(root / "Locked A", 0), _chmod(root / "Locked B", 0):
        for known, offered in ((None, False), ({}, True), ({"x": _row("Locked A/b.docx")}, False)):
            arm = al.LocalArm(_cfg(root))
            arm.known_h0 = known
            (alarm,) = arm.scan(None, full=True).alarms
            assert alarm.endswith(f"({_MODES}, or exclude them)" if offered else f"({_MODES})"), alarm
    codes = {"Locked A": errno.EACCES, "Private": errno.EPERM}
    _refusing(monkeypatch, codes)  # one patch, its codes changed in place: a second would wrap the first
    (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    assert alarm.endswith(
        "(grant the agent Files and Folders access for the one refused with EPERM; for the one refused with "
        "EACCES, its own permissions do not let this account read it: check its owner and mode with `ls -ld`)"
    ), alarm
    codes.clear()
    codes.update({"Locked A": errno.EACCES, "Locked B": errno.EACCES, "Slow": errno.EDEADLK})
    (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    assert alarm.endswith(
        f"(for those refused with EACCES, {_MODES}; check that the other one opens in Finder and that its "
        "sync app is running)"
    ), alarm
    codes.clear()
    codes["Slow"] = errno.EDEADLK
    (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    assert alarm.endswith("(check that it opens in Finder and that its sync app is running)"), alarm


def test_an_unreadable_folder_behind_five_empty_ones_is_named_in_its_own_clause() -> None:
    """B13 round 11 M5, M45: the alarm named the first five of one sorted list, so six empty folders hid
    'Zeta Locked' in "+2 more" while the could-not-be-listed wait said the alarm names what could not be
    listed, up to five. Each kind has its own clause, capped at five with its own pointer; the mirror case
    (six unreadable, one empty) names the empty one. STATE.md keeps the alarm line, so it names it too."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    for n in "ABCDEF":
        (root / f"Empty {n}").mkdir(parents=True)
    _write(root / "Zeta Locked" / "b.docx")
    _write(root / "a.docx")
    with _chmod(root / "Zeta Locked", 0):
        (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    pointer = "(+1 more; `~/.local/bin/agentsync sync -v` logs each one)"
    assert alarm.startswith(
        f"7 unknown dirs — 1 could not be read: 'Zeta Locked' ({_DENIED}); 6 empty cloud folders, zero "
        f"children in a cloud tree: 'Empty A', 'Empty B', 'Empty C', 'Empty D', 'Empty E' {pointer}; "
    ), alarm
    for n in "ABCDEF":
        (root / f"Empty {n}").rmdir()
    for n in range(1, 7):
        _write(root / f"Locked {n}" / "b.docx")
    (root / "Empty A").mkdir()
    with contextlib.ExitStack() as locked:
        for n in range(1, 7):
            locked.enter_context(_chmod(root / f"Locked {n}", 0))
        locked.enter_context(_chmod(root / "Zeta Locked", 0))
        (alarm,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    assert "; 1 empty cloud folder, zero children in a cloud tree: 'Empty A'; " in alarm, alarm
    assert "7 could not be read: 'Locked 1' (" in alarm and "'Locked 5' (" in alarm and "(+2 more; " in alarm


def test_an_unreadable_folder_beside_an_empty_cloud_folder_offers_no_exclude() -> None:
    """b13 N27 (a): the loop's wait names only the empty folders, so the alarm keeps the privacy advice for
    the others; it no longer offers to exclude an empty one, which the wait warns against when the folder
    held mirrored files (excluding it retires those pages unchecked)."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    (root / "One").mkdir(parents=True)
    _write(root / "Locked" / "b.docx")
    _write(root / "a.docx")
    with _chmod(root / "Locked", 0):
        res = al.LocalArm(_cfg(root)).scan(None, full=True)
    (alarm,) = res.alarms
    assert alarm == (
        # B13 round 12 N36, N37: singular throughout for one folder of each
        f"2 unknown dirs — 1 could not be read: 'Locked' ({_DENIED}); 1 empty cloud folder, zero "
        "children in a cloud tree: 'One'; enumeration incomplete, no deletions this pass (for the folder "
        f"it could not read, {_MODE_NAMED}; for the empty one, the WAITING ON YOU line about the empty cloud "
        "folder in local-test at the end of a sync's output says what to do, and "
        "`~/.local/bin/agentsync status` prints it too)"
    )
    # B13 round 11 M46, M56: the loop prints a could-not-be-listed wait about local-test too, so the line is
    # named by its subject, not only by its source
    assert "the WAITING ON YOU line about local-test" not in alarm


def test_one_unreadable_folder_beside_an_empty_one_is_named_once() -> None:
    """B13 round 13 O35, O37: one EACCES folder beside an empty cloud folder read "for the folder it could
    not read, a folder's own permissions do not let this account read it", naming the folder twice; it is
    "its own permissions" there, with the exclude only when no mirrored file is known below it, and the
    alarm of that folder alone says "the folder's" (test_an_unreadable_folder_with_no_empty_cloud_folder_
    names_its_reason)."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    (root / "One").mkdir(parents=True)
    _write(root / "Locked" / "b.docx")
    _write(root / "a.docx")
    mirrored = {i.stable_id: i for i in _walk(root)[0]}
    for known, tail in ((mirrored, ""), ({}, ", or exclude it")):
        arm = al.LocalArm(_cfg(root))
        arm.known_h0 = known
        with _chmod(root / "Locked", 0):
            (alarm,) = arm.scan(None, full=True).alarms
        assert f"(for the folder it could not read, {_MODE_NAMED}{tail}; for the empty one, " in alarm, alarm
        assert "a folder's" not in alarm and "the folder's" not in alarm, alarm


def _waits_alarm(root: Path, known: Sequence[SourceItem]) -> str:
    arm = al.LocalArm(_cfg(root))
    arm.known_h0 = {i.stable_id: i for i in known}
    (alarm,) = arm.scan(None, full=True).alarms
    return alarm


def test_the_empty_folder_alarm_counts_the_waits_by_the_loops_rule() -> None:
    """B13 round 10 L15: six empty folders that all land on the one exclude-empty wait read "each WAITING ON
    YOU line ... prints each one too", though there is one such line. The alarm counts the waits by the
    loop's own rule (``split_empty`` over the files the manifest holds): one kind of wait is "the line", two
    kinds are "each"; with no stored files to count by (``known_h0`` None) it keeps "each", true for any
    count."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    _write(root / "a.docx")
    for n in range(1, 7):
        (root / f"Empty {n}").mkdir(parents=True)
    known = _walk(root)[0]
    one = (
        "the WAITING ON YOU line about the empty cloud folders in local-test at the end of a sync's output "
        "says what to do, and "
    )
    alarm = _waits_alarm(root, known)
    assert alarm.endswith(f"({one}`~/.local/bin/agentsync status` prints it too)"), alarm
    (unknown,) = al.LocalArm(_cfg(root)).scan(None, full=True).alarms
    each = "(each WAITING ON YOU line about empty cloud folders in local-test"
    assert each in unknown and "prints each one too" in unknown
    # a [ folder whose exclude would also prune a folder holding files: its own wait, so two
    _write(root / "Qx1]" / "b.docx")
    (root / "Q[1]").mkdir()
    alarm = _waits_alarm(root, _walk(root)[0])
    assert each in alarm and "prints each one too" in alarm, alarm


def test_the_alarm_counts_the_waits_over_the_files_this_walk_listed() -> None:
    """B13 round 11 M55: on a first sync ``known_h0`` is empty, so the alarm saw no file below or beside the
    empty folders and said "the line", while the loop, reading the rows the cycle wrote for this walk, put
    'Q[1]' on the `[` collision wait beside the exclude-empty one: two lines. The count takes this walk's
    files too, on a first sync and when the colliding file is new in this walk."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    _write(root / "a.docx")
    (root / "Plain").mkdir(parents=True)
    (root / "Q[1]").mkdir()
    before = _walk(root)[0]
    _write(root / "Qx1]" / "b.docx")
    for known in ((), before):
        alarm = _waits_alarm(root, known)
        assert "(each WAITING ON YOU line about empty cloud folders in" in alarm, alarm
        assert alarm.endswith("prints each one too)"), alarm


def test_an_empty_folder_that_held_files_is_its_own_wait_in_the_alarm() -> None:
    """B13 round 10 L15: a folder that held mirrored files lands on the held-files wait, not the exclude-empty
    one, so beside a plain empty folder the alarm says "each" line."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    _write(root / "Was Full" / "old.docx")
    (root / "Plain").mkdir(parents=True)
    known = _walk(root)[0]
    (root / "Was Full" / "old.docx").unlink()
    alarm = _waits_alarm(root, known)
    assert "'Plain', 'Was Full'" in alarm and "prints each one too" in alarm, alarm
    (root / "Plain").rmdir()
    alarm = _waits_alarm(root, known)
    assert alarm.endswith("prints it too)"), alarm


def test_the_alarm_counts_rows_by_the_one_present_state_tuple() -> None:
    """B13 round 11 M63: the walk's alarm, the cycle and the manifest read one tuple of present-row states, so
    a state added to one copy only cannot make the alarm's count of waits drift from the loop's. A quarantined
    row under an emptied folder is a file the mirror still holds (the held-files wait); a tombstone is not."""
    assert cycle._PRESENT is model.PRESENT_STATES and manifest._PRESENT_STATES is model.PRESENT_STATES
    assert frozenset(s.value for s in model.PRESENT_STATES) == al._PRESENT_STATES
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    _write(root / "a.docx")
    (root / "Was Full").mkdir(parents=True)
    (root / "Plain").mkdir()
    for state, ending in (("quarantined", "prints each one too)"), ("tombstone", "prints it too)")):
        arm = al.LocalArm(_cfg(root))
        row = dataclasses.make_dataclass("Row", ["rel_path", "is_dir", "state"])(
            "Was Full/old.docx", False, state
        )
        arm.known_h0 = {"x": row}
        (alarm,) = arm.scan(None, full=True).alarms
        assert alarm.endswith(ending), (state, alarm)


def test_an_inbox_empty_cloud_folder_alarm_points_at_the_waits() -> None:
    """b13 N27 (b): an inbox's empty cloud folders get a local source's WAITING ON YOU lines, and so its
    alarm. One empty folder lands on one wait, so the pointer is singular (B13 round 5 F11), and status is
    named by its full path, since ~/.local/bin may be off PATH (F44)."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Inbox"
    (root / "One").mkdir(parents=True)
    res = al.InboxArm(_cfg(root, kind=SourceKind.INBOX)).scan(None, full=True)
    (alarm,) = res.alarms
    assert " agentsync status" not in alarm
    assert alarm == (
        "1 unknown dir — 1 empty cloud folder, zero children in a cloud tree: 'One'; enumeration "
        "incomplete, no deletions this pass (the WAITING ON YOU line about the empty cloud folder in "
        "local-test at the end of a sync's output says what to do, and `~/.local/bin/agentsync status` "
        "prints it too)"
    )


def test_an_empty_root_alarm_never_advises_an_exclude(tmp_path: Path, tree: Path) -> None:
    """b13 R103: an empty root (plain or cloud) gets the empty-root alarm alone; it gave a second alarm
    advising to exclude a deliberately empty cloud folder, and a root cannot be excluded. A root that cannot
    be listed gets the access advice only."""
    mirrored = {i.stable_id: i for i in _walk(tree)[0]}
    cloud = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    plain = tmp_path / "plain"
    for root in (cloud, plain):
        root.mkdir(parents=True)
        arm = al.LocalArm(_cfg(root))
        arm.known_h0 = mirrored
        res = arm.scan(None, full=True)
        assert res.unknown_dirs == (".",) and not res.enumeration_complete
        assert res.alarms == (
            f"source root {root} is empty but {len(mirrored)} file(s) are mirrored (unmounted volume?): "
            "enumeration incomplete, nothing is deleted",
        )
    (alarm,) = al.LocalArm(_cfg(cloud)).scan(None, full=True).alarms  # nothing mirrored yet
    assert alarm == (
        f"source root {cloud} has zero children in a cloud tree: enumeration incomplete, nothing is deleted"
    )
    _write(plain / "a.docx")
    with _chmod(plain, 0):
        res = al.LocalArm(_cfg(plain)).scan(None, full=True)
    assert res.unknown_dirs == (".",)
    assert [a for a in res.alarms if "exclude" in a] == []
    # B13 round 11 M42, M47: a mode-000 root fails with EACCES, which no grant overrides; its reason is named
    assert (
        f"1 unknown dir — could not be read: '.' ({_DENIED}); enumeration incomplete, no deletions this "
        "pass (the source folder's own permissions do not let this account read it: check its owner and mode "
        "with `ls -ld`)"
    ) in res.alarms


def test_unlistable_folders_log_at_info_and_one_warning_names_them_with_their_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N28: one WARNING per unreadable folder per poll filled the job's log. The per-folder line is info, as
    for an empty cloud folder; one warning a walk names each folder whose listing failed, with its reason,
    even when five empty folders sort before it and fill the alarm's names. The alarm is unchanged."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    for n in "ABCDE":
        (root / f"Empty {n}").mkdir(parents=True)
    _write(root / "Zeta" / "b.docx")
    _write(root / "Shut" / "c.docx")
    _write(root / "a.docx")
    denied = f"EACCES: {os.strerror(errno.EACCES)}"
    with (
        _chmod(root / "Zeta", 0),
        _chmod(root / "Shut", 0),
        caplog.at_level(logging.INFO, logger=al.__name__),
    ):
        res = al.LocalArm(_cfg(root)).scan(None, full=True)
    per_dir = [r for r in caplog.records if " is unknown (" in r.getMessage()]
    assert len(per_dir) == 2 and {r.levelno for r in per_dir} == {logging.INFO}
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warned == [f"local-test: 2 folders could not be listed: 'Shut' ({denied}), 'Zeta' ({denied})"]
    (alarm,) = res.alarms
    # B13 round 11 M5, M45: the alarm names them too, in their own clause, not behind the five empty ones
    assert alarm == (
        # B13 round 12 N36, N37: two folders refused with EACCES take the plural advice
        f"7 unknown dirs — 2 could not be read: 'Shut' ({denied}), 'Zeta' ({denied}); 5 empty cloud "
        "folders, zero children in a cloud tree: 'Empty A', 'Empty B', 'Empty C', 'Empty D', 'Empty E'; "
        f"enumeration incomplete, no deletions this pass (for the folders it could not read, {_MODES}; for "
        "the empty ones, each WAITING ON YOU line about empty cloud folders in local-test at the end of a "
        "sync's output says what to do, and `~/.local/bin/agentsync status` prints each one too)"
    )


def test_a_held_listing_still_warns_about_the_folders_it_could_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """N28: a walk that stops at a held listing still logs, at warning, the folder that failed before it."""
    root = tmp_path / "root"
    for name in ("one", "two"):
        _write(root / name / "x.txt")
    real = al._list_dir
    release = threading.Event()
    failed: list[str] = []

    def flaky(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        if path == root:
            return real(path, dir_dataless=dir_dataless)
        if not failed:
            failed.append(path.name)
            raise OSError(errno.EDEADLK, os.strerror(errno.EDEADLK), str(path))
        release.wait(5.0)
        return real(path, dir_dataless=dir_dataless)

    monkeypatch.setattr(al, "_list_dir", flaky)
    try:
        with caplog.at_level(logging.WARNING, logger=al.__name__), pytest.raises(al.CallTimedOutError):
            _walk(root, listing_timeout_s=0.2)
    finally:
        release.set()
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    edeadlk = f"EDEADLK: {os.strerror(errno.EDEADLK)}"
    assert [r.getMessage() for r in warned] == [f"s: 1 folder could not be listed: {failed[0]!r} ({edeadlk})"]
    # B13 round 16 T37: the held listing's alarm names only the folder that stalled, so this record is the
    # only line naming the one refused before it: never held back from a foreground run's stderr
    assert not getattr(warned[0], al.ECHOED_ALARM, False)
    caplog.clear()
    monkeypatch.setattr(al, "_list_dir", real)
    with _chmod(root / "two", 0), caplog.at_level(logging.WARNING, logger=al.__name__):
        _walk(root)
    [end] = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "1 folder could not be listed: 'two'" in end.getMessage() and getattr(end, al.ECHOED_ALARM), end


def test_the_walk_records_its_zero_child_cloud_folders(tmp_path: Path) -> None:
    """Bring-back S11: the folders status names are the ones the walk itself found with zero children in a
    cloud tree (no second listing of the source): under its excludes, never the root, never a folder that is
    unknown for another reason, and none outside CloudStorage."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    for rel in ("Bids", "Plans/Old [2019]", "Skipped", "Locked/inner"):
        (root / rel).mkdir(parents=True)
    _write(root / "Plans" / "a.docx")
    (root / "link").symlink_to(root / "Bids", target_is_directory=True)
    with _chmod(root / "Locked", 0):
        _, stats = _walk(root, exclude=("Skipped/",))
    assert stats.empty_cloud_dirs == ("Bids", "Plans/Old [2019]")
    assert stats.unknown_dirs == ("Bids", "Locked", "Plans/Old [2019]")
    _, stats = _walk(root / "Bids")
    assert stats.unknown_dirs == (".",) and stats.empty_cloud_dirs == (), "an empty root is not excluded"
    plain = tmp_path / "plain"
    (plain / "empty").mkdir(parents=True)
    assert _walk(plain)[1].empty_cloud_dirs == ()
    arm = al.LocalArm(_cfg(root, exclude=("Skipped/", "Locked/")))
    arm.scan(None, full=True)
    assert arm.last_stats is not None and arm.last_stats.empty_cloud_dirs == ("Bids", "Plans/Old [2019]")


def test_exclude_advice_is_a_line_that_clears_the_folders_it_names() -> None:
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Test" / "Projects"
    names = ["Bids", "Plans/Old [2019]", "Q*", *(f"Empty {n}" for n in range(1, 6))]
    for rel in names:
        (root / rel).mkdir(parents=True)
    (root / "Keep" / "Bids").mkdir(parents=True)  # same name deeper: the anchored glob leaves it alone
    _write(root / "Keep" / "Bids" / "a.docx")
    cfg = _cfg(root, exclude=("Archive/", ".DS_Store"))
    empty = _walk(root, exclude=cfg.exclude)[1].empty_cloud_dirs
    assert len(empty) == 8
    where = "~/agent-context/sources.toml"
    advice = al.exclude_advice(cfg, empty, where)
    assert advice == (
        'set exclude = ["Archive/", "/Bids/", "/Empty 1/", "/Empty 2/", "/Empty 3/", "/Empty 4/"] in '
        '[[source]] id = "local-test" in ~/agent-context/sources.toml (+3 more: status names them once these '
        "are excluded); an excluded folder is not mirrored if it later gains files"
    )
    # Pasted, round after round, the line empties the list: the stored folders the new exclude prunes drop
    # out before any walk (status names the next ones at once), and the walk has no unknown folder left.
    for _ in range(2):
        advice = al.exclude_advice(cfg, al._unexcluded(cfg, empty), where)
        line = advice.removeprefix("set ").split(" in [[source]]")[0]
        cfg = _cfg(root, exclude=tuple(tomllib.loads(line)["exclude"]))
    assert al._unexcluded(cfg, empty) == []
    items, stats = _walk(root, exclude=cfg.exclude)
    assert stats.unknown_dirs == () and _rels(items) == ["Keep/Bids/a.docx"]
    assert al._unexcluded(_cfg(root, exclude=("Plans/",)), ["Plans/Old [2019]", "Bids"]) == ["Bids"], (
        "a folder below an excluded one is pruned with it"
    )


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


@pytest.mark.parametrize("exclude", [DEFAULT_EXCLUDES, ("drafts/",)], ids=["defaults", "custom"])
def test_custom_exclude_still_drops_os_junk(tmp_path: Path, exclude: tuple[str, ...]) -> None:
    root = tmp_path / "src"
    _write(root / "notes.md")
    _write(root / "Icon\r")
    _write(root / "sub" / ".DS_Store")
    _write(root / "sub" / "._notes.md")
    _write(root / "sub" / "plan.docx")
    _write(root / "drafts" / "wip.docx")
    _write(root / "Brand" / "Icon" / "guide.md")  # a folder named Icon is not the icon file
    rels = [i.rel_path for i in al.LocalArm(_cfg(root, exclude=exclude)).scan(None, full=True).items]
    assert "Icon\r" not in rels
    assert "Brand/Icon/guide.md" in rels
    assert "sub/.DS_Store" not in rels and "sub/._notes.md" not in rels
    assert {"notes.md", "sub/plan.docx"} <= set(rels)
    assert ("drafts/wip.docx" in rels) is (exclude == DEFAULT_EXCLUDES)


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
    assert (budget.used, budget.files_used) == (0, 1), "a local file downloads nothing: 0 bytes charged (L3)"
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


def test_fetch_respects_the_budget(tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arm = al.LocalArm(_cfg(tree))
    item = next(i for i in arm.scan(None, full=True).items if i.rel_path == "b.docx")
    ino = (tree / "b.docx").stat().st_ino
    real = mat.is_dataless
    # online-only (a test has no File Provider): only a download is charged against the byte budget (L3)
    monkeypatch.setattr(mat, "is_dataless", lambda st: st.st_ino == ino or real(st))
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


def _settling(arm: al.InboxArm) -> list[float]:
    """Give ``arm`` a settle budget and a fake clock that only its sleep moves; returns the sleeps taken."""
    start = time.time_ns()
    sleeps: list[float] = []
    arm.settle = al.SettleBudget()
    arm._clock = lambda: start + int(sum(sleeps) * 1e9)
    arm._sleep = sleeps.append
    return sleeps


def test_inbox_settle_once_waits_for_an_atomic_rename_and_lists_it(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    _write(root / "export.eml.tmp")
    (root / "export.eml.tmp").rename(root / "export.eml")  # the writer contract: temp name, then rename
    arm = _inbox(root)
    sleeps = _settling(arm)
    res = arm.scan(None, full=True)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 60
    assert [i.rel_path for i in res.items] == ["export.eml"]
    assert res.enumeration_complete is True
    assert res.alarms == ()


def test_inbox_settle_once_lists_once_more_and_never_waits_past_the_window(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    _write(root / "growing.eml")
    arm = _inbox(root)
    sleeps = _settling(arm)
    real_walk = arm._walk_scan
    walks: list[int] = []

    def walk_and_touch() -> Any:
        walks.append(1)
        if len(walks) == 2:  # still being written when the inbox is listed again
            _write(root / "growing.eml", b"xy")
            os.utime(root / "growing.eml", ns=(arm._clock(), arm._clock()))
        return real_walk()

    arm._walk_scan = walk_and_touch  # type: ignore[method-assign]
    res = arm.scan(None, full=True)
    assert len(walks) == 2 and len(sleeps) == 1  # one more listing, never a third
    assert res.items == () and res.enumeration_complete is False

    future = time.time_ns() + 3600 * 1_000_000_000  # a future-dated file cannot leave the window in time
    os.utime(root / "growing.eml", ns=(future, future))
    sleeps.clear()
    walks.clear()
    res = arm.scan(None, full=True)
    assert sleeps == [] and len(walks) == 1
    assert res.enumeration_complete is False


def test_inbox_settle_waits_for_a_fresh_file_beside_a_future_dated_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A zip from a later time zone extracts with mtimes hours ahead: that file stays withheld, and the
    fresh export beside it is still waited for and listed, with the wait shown at the default log level."""
    root = tmp_path / "inbox"
    _write(root / "fresh.eml")
    ahead = time.time_ns() + 7 * 3600 * 1_000_000_000
    os.utime(_write(root / "fromzip.eml"), ns=(ahead, ahead))
    arm = _inbox(root)
    sleeps = _settling(arm)
    with caplog.at_level(logging.WARNING, logger=al.__name__):
        res = arm.scan(None, full=True)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 60
    assert [i.rel_path for i in res.items] == ["fresh.eml"]
    assert res.enumeration_complete is False and any("fromzip.eml" in a for a in res.alarms)
    assert any("still settling" in r.getMessage() for r in caplog.records)


def test_inbox_settle_never_waits_past_the_budget_left(tmp_path: Path) -> None:
    """The wait comes out of the budget the cycle shares across inboxes; when even the oldest withheld file
    would not settle in what is left, the arm does not sleep at all."""
    root = tmp_path / "inbox"
    _write(root / "export.eml")
    arm = _inbox(root, quiescence_s=600)
    sleeps = _settling(arm)
    assert arm.settle is not None
    arm.settle.remaining_s = 30.0
    res = arm.scan(None, full=True)
    assert sleeps == [] and res.items == () and arm.settle.remaining_s == 30.0
    real_clock = arm._clock
    arm._clock = lambda: real_clock() + 590 * 1_000_000_000  # about 10 s left of the 600 s window
    res = arm.scan(None, full=True)
    assert len(sleeps) == 1 and sleeps[0] <= 30 and arm.settle.remaining_s == 30.0 - sleeps[0]
    assert [i.rel_path for i in res.items] == ["export.eml"]


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
    from conftest import FILEPROVIDER_ROOT  # noqa: PLC0415 — the shared mount discovery

    probe = FILEPROVIDER_ROOT / "agentsync-probe"
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


# ---------------------------------------------------------------------------------------------------------
# performance paths: getattrlist only where needed, globs compiled once (results must not move)
# ---------------------------------------------------------------------------------------------------------


def _count_attrs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = al._file_attrs

    def spy(path: Path) -> object:
        calls.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(al, "_file_attrs", spy)
    return calls


def test_walk_with_known_h0_skips_getattrlist_only_for_unmoved_stat_tuples(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = _walk(tree, exclude=DEFAULT_EXCLUDES)
    known = {i.stable_id: i for i in first}  # a SourceItem carries every field a stored row does
    calls = _count_attrs(monkeypatch)
    again, _ = _walk(tree, exclude=DEFAULT_EXCLUDES, known=known)
    assert again == first and calls == []  # identical items, zero getattrlist calls
    time.sleep(0.01)
    (tree / "b.docx").write_bytes(b"docx bytes, edited")  # stat tuple moves
    _write(tree / "new.txt", b"new")  # unknown id
    stale = dict(known)
    readme = next(i for i in first if i.rel_path == "README.txt")
    stale[readme.stable_id] = dataclasses.replace(readme, gen_count=None)  # stored gen unknown: re-read
    calls.clear()
    third, _ = _walk(tree, exclude=DEFAULT_EXCLUDES, known=stale)
    assert sorted(calls) == ["README.txt", "b.docx", "new.txt"]
    fresh, _ = _walk(tree, exclude=DEFAULT_EXCLUDES)
    assert third == fresh  # the reused values are exactly what getattrlist returns


def test_walk_known_values_are_reused_verbatim(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, _ = _walk(tree)
    target = first[0]
    known = {target.stable_id: dataclasses.replace(target, gen_count=4242, created_ns=17)}
    _count_attrs(monkeypatch)
    again, _ = _walk(tree, known=known)
    got = next(i for i in again if i.stable_id == target.stable_id)
    assert (got.gen_count, got.created_ns) == (4242, 17)
    assert _walk(tree, known=known, with_gen_count=False)[0] == _walk(tree, with_gen_count=False)[0]


def test_local_arm_scan_passes_its_known_index_to_the_walk(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm = al.LocalArm(_cfg(tree))
    first = arm.scan(None, full=True).items
    arm.known_h0 = {i.stable_id: i for i in first}
    calls = _count_attrs(monkeypatch)
    assert arm.scan(None, full=True).items == first and calls == []


@pytest.mark.parametrize(
    ("include", "exclude"),
    [
        ((), DEFAULT_EXCLUDES),
        (("*.md", "a/**"), ()),
        (("docs/", "**/*.XLSX"), ("~$*", "a/deep/")),
        (("a/*.md",), ("*.tmp", "._*", "b*")),
    ],
)
def test_compiled_matcher_equals_paths_is_included(
    include: tuple[str, ...], exclude: tuple[str, ...]
) -> None:
    from agentsync.paths import is_included  # noqa: PLC0415

    rels = [
        "README.txt", "b.docx", "a/z.md", "a/deep/y.xlsx", "a/deep/Y.XLSX", "~$b.docx", "x/~$b.docx",
        ".DS_Store", "docs/a.txt", "sub/docs/a.txt", "a.tmp", "._x", "é/ü.md", "Icon\r", "a/b/c/d.md",
        "bb/x.md",
    ]  # fmt: skip
    match = al._file_matcher(include, exclude)
    assert [match(r) for r in rels] == [is_included(r, include, exclude) for r in rels]


# ---------------------------------------------------------------------------------------------------------
# in_scope: the walk's scope as one predicate
# ---------------------------------------------------------------------------------------------------------

_SCOPE_TREE = (
    "README.txt", "b.docx", "notes.tmp", "a/z.md", "a/Archive/x.pdf", "a/Archive/old/y.docx",
    "Archive/top.md", "a/b/Archive.md", "a/deep/y.xlsx", "x/Archive", "drafts/wip.docx", "x/drafts/n.md",
    "x/y/Draft.md", "sub/.DS_Store", "sub/plan.docx", "drop/report.pdf", "drop/report.pdf.part",
)  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "include", "exclude"),
    [
        (SourceKind.LOCAL, (), ("Archive",)),  # a bare name: the folder at any depth, and a file of that name
        (SourceKind.LOCAL, (), ("Archive/",)),  # folders only
        (SourceKind.LOCAL, (), ("**/Archive",)),
        (SourceKind.LOCAL, (), ("a/Archive",)),  # anchored, no trailing slash
        (SourceKind.LOCAL, (), ("archive",)),  # globs ignore case
        (SourceKind.LOCAL, (), ("a/*",)),  # every folder and file directly under a/
        (SourceKind.LOCAL, ("*.docx", "*.md"), ("drafts", "x/y/")),
        (SourceKind.LOCAL, ("a/",), ()),  # include never prunes a folder
        (SourceKind.INBOX, (), ()),  # the inbox's always-on ignores
        (SourceKind.INBOX, (), ("drop",)),
    ],
)
def test_in_scope_keeps_exactly_the_files_the_walk_lists(
    tmp_path: Path, kind: SourceKind, include: tuple[str, ...], exclude: tuple[str, ...]
) -> None:
    root = tmp_path / "src"
    for rel in _SCOPE_TREE:
        _write(root / rel)
    cfg = _cfg(root, kind=kind, include=include, exclude=exclude)
    arm = al.InboxArm(cfg) if kind is SourceKind.INBOX else al.LocalArm(cfg)
    if isinstance(arm, al.InboxArm):
        arm._clock = lambda: time.time_ns() + 120 * 1_000_000_000  # past the quiescence window
    listed = {i.rel_path for i in arm.scan(None, full=True).items}
    assert listed and listed < set(_SCOPE_TREE)  # every case keeps a file and drops one
    assert {rel for rel in _SCOPE_TREE if al.in_scope(cfg, rel)} == listed
    assert {rel for rel in _SCOPE_TREE if arm.in_scope(rel)} == listed


def test_in_scope_applies_the_folder_rule_that_plain_is_included_lacks(tmp_path: Path) -> None:
    from agentsync.paths import is_included  # noqa: PLC0415

    cfg = _cfg(tmp_path, exclude=("Archive",))
    assert is_included("a/Archive/x.pdf", cfg.include, cfg.exclude)  # the file's own name matches no glob
    assert not al.in_scope(cfg, "a/Archive/x.pdf")  # but the walk never enters a/Archive
    assert al.in_scope(cfg, "a/b/Archive.md") and al.in_scope(cfg, "top.md")
    inbox = _cfg(tmp_path, kind=SourceKind.INBOX, exclude=())
    assert al.in_scope(inbox, "drop/report.pdf") and not al.in_scope(inbox, "drop/report.pdf.part")
