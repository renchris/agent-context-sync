"""Arm B (local / sync-client folder) and arm C (manual inbox): T3 metadata walk, zero file opens (owner:
local).

The walk is ``os.scandir`` + ``lstat`` + one ``getattrlist`` per file (GEN_COUNT and creation time): no file
is ever opened, so an online-only (dataless) File Provider placeholder is recorded, never downloaded.  A
directory that is itself dataless (its child list not yet fetched) is listed under THREAD-scope
materialisation ON, which fetches the listing (metadata) only.  Bytes are read solely by ``fetch`` through
``materialise.materialise``.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import errno as errno_mod
import functools
import hashlib
import json
import logging
import os
import plistlib
import pwd
import queue
import re
import socket
import stat
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Protocol, TypeVar, cast

from agentsync.config import SourceConfig, always_excluded
from agentsync.errors import ConfigError, MaterialiseError
from agentsync.materialise import is_dataless, materialise, materialize_allowed, sha256_file
from agentsync.model import (
    ByteBudget,
    ExtraValue,
    FetchResult,
    PassKind,
    RemoteHashes,
    ScanResult,
    SourceItem,
    SourceKind,
)
from agentsync.paths import CLOUD_STORAGE_ROOT, _glob_regex, expand, glob_match, glob_strip, is_under

log = logging.getLogger(__name__)

_T = TypeVar("_T")

# How long one directory listing may take before agentsync stops waiting for it. A listing that never returns
# is macOS holding the read until someone clicks Allow on a privacy (TCC) prompt (field report 2026-10-05).
# A slow but working OneDrive listing of a large online-only folder (its children fetched in pages) should
# stay far below two minutes, so expiry means an unanswered prompt or a stalled provider.
LISTING_TIMEOUT_S = 120.0

# The most one interactive sync waits for settling inbox files, summed over every inbox it lists (field N4).
# An agent runs `agentsync sync` at the start of a session, and its tools stop a command at about two minutes.
INBOX_SETTLE_MAX_S = 60.0

# sys/attr.h (macOS 15 SDK)
_ATTR_BIT_MAP_COUNT = 5
_ATTR_CMN_CRTIME = 0x00000200
_ATTR_CMN_GEN_COUNT = 0x00080000
_ATTR_CMN_RETURNED_ATTRS = 0x80000000
_ATTR_VOL_UUID = 0x00040000
_ATTR_VOL_INFO = 0x80000000
_FSOPT_NOFOLLOW = 0x00000001
_FSOPT_PACK_INVAL_ATTRS = 0x00000008
_FSOPT_ATTR_CMN_EXTENDED = 0x00000020  # required for ATTR_CMN_GEN_COUNT via getattrlist (else EINVAL)

# Buffer layouts (attributes packed in bit order, 4-byte aligned; FSOPT_PACK_INVAL_ATTRS keeps offsets fixed):
#   file: u32 length | attribute_set_t (5 x u32) | CRTIME struct timespec (2 x i64) | GEN_COUNT u32
#   vol:  u32 length | attribute_set_t (5 x u32) | VOL_UUID uuid_t (16 bytes)
_HDR = 4 + 4 * _ATTR_BIT_MAP_COUNT
_FILE_BUF = _HDR + 16 + 4
_VOL_BUF = _HDR + 16

_WINDOWS_DEFAULT_HOST_RE = re.compile(r"-(?:DESKTOP|LAPTOP)-[A-Z0-9]{7}$", re.IGNORECASE)
_COPY_SUFFIX_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r" \(\d+\)$"),  # "Report (1)"
    re.compile(r" - Copy(?: \(\d+\))?$", re.IGNORECASE),  # Windows "Report - Copy", "Report - Copy (2)"
    re.compile(r" copy(?: \d+)?$", re.IGNORECASE),  # Finder "Report copy", "Report copy 2"
)

_S_IFMT = stat.S_IFMT(0o177777)
_S_IFLNK = stat.S_IFLNK
_S_IFDIR = stat.S_IFDIR
_S_IFREG = stat.S_IFREG
_NO_HASHES = RemoteHashes()  # frozen: shared by every local item (a local file has no provider hash)
_NO_EXTRA: Mapping[str, ExtraValue] = MappingProxyType({})


class _KnownStat(Protocol):
    """The stored H0 of a file row (``Manifest.observation_index`` values; ``ItemRow`` fits too)."""

    @property
    def size(self) -> int | None: ...
    @property
    def mtime_ns(self) -> int | None: ...
    @property
    def ctime_ns(self) -> int | None: ...
    @property
    def ino(self) -> int | None: ...
    @property
    def mode(self) -> int | None: ...
    @property
    def gen_count(self) -> int | None: ...
    @property
    def created_ns(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class WalkStats:
    """Counters from one walk (reported in STATE.md and the CycleReport)."""

    files: int
    dirs: int
    dataless: int
    excluded: int
    symlinks_skipped: int
    unknown_dirs: tuple[str, ...]  # zero-child dirs inside a cloud tree, and EPERM/EACCES dirs (TCC)
    sentinel_present: bool | None  # None when no sentinel is configured
    empty_cloud_dirs: tuple[str, ...] = ()  # the zero-child cloud dirs below the root (part of unknown_dirs)


# ---------------------------------------------------------------------------------------------------------
# getattrlist
# ---------------------------------------------------------------------------------------------------------


class _AttrList(ctypes.Structure):
    _fields_ = (
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    )


@dataclass(frozen=True, slots=True)
class _FileAttrs:
    gen_count: int | None
    created_ns: int | None


@functools.cache
def _getattrlist_fn() -> Callable[[bytes, object, object, int, int], int]:
    """Bind libSystem getattrlist (raises OSError(ENOSYS) off macOS)."""
    if sys.platform != "darwin":
        raise OSError(errno_mod.ENOSYS, f"getattrlist is macOS-only (platform {sys.platform})")
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.getattrlist
    fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    return fn


def _getattrlist(path: Path, al: _AttrList, size: int, options: int) -> bytes:
    """Call getattrlist and return the raw buffer; raises OSError with the path on failure."""
    fn = _getattrlist_fn()
    buf = ctypes.create_string_buffer(size)
    ctypes.set_errno(0)
    if fn(os.fsencode(path), ctypes.byref(al), buf, size, options) != 0:
        err = ctypes.get_errno() or errno_mod.EINVAL
        raise OSError(err, os.strerror(err), str(path))
    return buf.raw


def _file_attrs(path: Path) -> _FileAttrs:
    """Return (GEN_COUNT or None, creation time ns or None) for ``path`` without following or opening it."""
    al = _AttrList(
        _ATTR_BIT_MAP_COUNT, 0, _ATTR_CMN_RETURNED_ATTRS | _ATTR_CMN_CRTIME | _ATTR_CMN_GEN_COUNT, 0, 0, 0, 0
    )
    raw = _getattrlist(
        path, al, _FILE_BUF, _FSOPT_NOFOLLOW | _FSOPT_PACK_INVAL_ATTRS | _FSOPT_ATTR_CMN_EXTENDED
    )
    returned = int.from_bytes(raw[4:8], sys.byteorder)
    sec = int.from_bytes(raw[_HDR : _HDR + 8], sys.byteorder, signed=True)
    nsec = int.from_bytes(raw[_HDR + 8 : _HDR + 16], sys.byteorder, signed=True)
    gen = int.from_bytes(raw[_HDR + 16 : _HDR + 20], sys.byteorder)
    return _FileAttrs(
        gen_count=gen if returned & _ATTR_CMN_GEN_COUNT and gen != 0 else None,
        created_ns=sec * 1_000_000_000 + nsec if returned & _ATTR_CMN_CRTIME else None,
    )


def _getattrlist_volume_uuid(path: Path) -> str:
    """ATTR_VOL_UUID of the volume holding ``path``; raises OSError if not returned."""
    al = _AttrList(_ATTR_BIT_MAP_COUNT, 0, _ATTR_CMN_RETURNED_ATTRS, _ATTR_VOL_INFO | _ATTR_VOL_UUID, 0, 0, 0)
    raw = _getattrlist(path, al, _VOL_BUF, _FSOPT_PACK_INVAL_ATTRS)
    returned_vol = int.from_bytes(raw[8:12], sys.byteorder)
    value = raw[_HDR : _HDR + 16]
    if not returned_vol & _ATTR_VOL_UUID or value == bytes(16):
        raise OSError(errno_mod.ENOTSUP, "volume UUID not returned by getattrlist", str(path))
    return str(uuid.UUID(bytes=value)).upper()


def _mount_point(path: Path) -> str:
    """Return the mount point of the volume holding ``path`` (``df -P``)."""
    out = subprocess.run(
        ["/bin/df", "-P", os.fspath(path)], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    lines = out.strip().splitlines()
    if len(lines) < 2:
        raise OSError(errno_mod.ENOENT, "df printed no mount", str(path))
    cols = lines[-1].split(maxsplit=5)
    if len(cols) < 6:
        raise OSError(errno_mod.ENOENT, f"cannot parse df output {lines[-1]!r}", str(path))
    return cols[5]


def _diskutil_volume_uuid(path: Path) -> str:
    """VolumeUUID from ``diskutil info -plist <mount point>``; raises OSError when absent."""
    try:
        mount = _mount_point(path)
        proc = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", mount], capture_output=True, check=True, timeout=30
        )
        info = plistlib.loads(proc.stdout)
    except (subprocess.SubprocessError, plistlib.InvalidFileException, ValueError) as exc:
        raise OSError(errno_mod.ENOTSUP, f"diskutil fallback failed: {exc}", str(path)) from exc
    value = info.get("VolumeUUID") if isinstance(info, dict) else None
    if not isinstance(value, str) or not value:
        raise OSError(errno_mod.ENOTSUP, "diskutil reported no VolumeUUID", str(path))
    return str(uuid.UUID(value)).upper()


def volume_uuid(path: Path) -> str:
    """Return the UUID of the volume holding ``path`` (getattrlist ATTR_VOL_UUID on its mount point).

    Falls back to ``diskutil info -plist <mount>`` VolumeUUID; raises OSError if neither yields one. Stable
    across reboots, unlike st_dev.
    """
    try:
        return _getattrlist_volume_uuid(path)
    except OSError as exc:
        if exc.errno == errno_mod.ENOENT:
            raise
        log.info("getattrlist(ATTR_VOL_UUID) failed for %s (%s); trying diskutil", path, exc)
    return _diskutil_volume_uuid(path)


def gen_count(path: Path) -> int | None:
    """Return ATTR_CMN_GEN_COUNT via getattrlist (no open, no hydration); None when not returned or 0."""
    return _file_attrs(path).gen_count


def stable_id_for(volume: str, ino: int) -> str:
    """Return the local identity string ``f"{volume}:{ino}"`` (never the path)."""
    return f"{volume}:{ino}"


# ---------------------------------------------------------------------------------------------------------
# walk
# ---------------------------------------------------------------------------------------------------------


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@functools.cache
def _account_cloud_root() -> Path | None:
    """``<account home>/Library/CloudStorage`` from the password database (independent of $HOME)."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "CloudStorage"
    except KeyError:
        return None


def _cloud_roots() -> tuple[Path, ...]:
    """``~/Library/CloudStorage`` for $HOME and for the account's real home (launchd/tests may differ)."""
    roots = {expand(CLOUD_STORAGE_ROOT)}
    account = _account_cloud_root()
    if account is not None:
        roots.add(account)
    return tuple(sorted(roots))


def _is_cloud_tree(path: Path, roots: Sequence[Path]) -> bool:
    """True when ``path`` lies in a File Provider tree (``~/Library/CloudStorage/...``)."""
    return any(is_under(path, r) for r in roots)


def cloud_provider_root(path: Path) -> Path | None:
    """The File Provider folder holding ``path`` (``~/Library/CloudStorage/<provider>``); None outside one."""
    p = expand(path)
    for root in _cloud_roots():
        if root in p.parents:
            return root / p.relative_to(root).parts[0]
    return None


def _dir_excluded(rel: str, exclude: Sequence[str]) -> bool:
    """True when a directory matches an exclude glob; ``name/`` (directory-only) patterns match the dir."""
    for pattern in exclude:
        p = glob_strip(pattern)  # as glob_match does: a bare strip() turns "Icon\r" into "Icon"
        if p.endswith("/"):
            p = p.rstrip("/")
            if not p:
                continue
        if glob_match(rel, p):
            return True
    return False


def _birthtime_ns(st: os.stat_result) -> int | None:
    ns = getattr(st, "st_birthtime_ns", None)
    if isinstance(ns, int):
        return ns
    bt = getattr(st, "st_birthtime", None)
    return round(bt * 1_000_000_000) if isinstance(bt, float | int) else None


@dataclass(slots=True)
class _WalkState:
    """Mutable accumulator private to one walk() call."""

    items: list[SourceItem] = dataclasses.field(default_factory=list)
    dirs: int = 0
    excluded: int = 0
    symlinks: int = 0
    unknown: dict[str, str] = dataclasses.field(default_factory=dict)  # rel dir -> reason
    empty: list[str] = dataclasses.field(default_factory=list)  # zero-child cloud dirs below the root


class CallTimedOutError(TimeoutError):
    """:func:`call_with_timeout` gave up waiting. A subclass, because Python also raises an OS ETIMEDOUT (a
    File Provider warming up) as TimeoutError, and only an abandoned call means a prompt may be waiting."""


def call_with_timeout(fn: Callable[[], _T], timeout_s: float, *, name: str = "agentsync-timed") -> _T:
    """``fn()`` in a daemon thread, bounded by ``timeout_s``: CallTimedOutError past it, ``fn``'s own
    exception re-raised. The thread is abandoned on expiry, so a read that waits on a privacy prompt never
    holds the caller (and never holds the interpreter's exit)."""
    box: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            box.put((True, fn()))
        except BaseException as exc:
            box.put((False, exc))

    threading.Thread(target=target, name=name, daemon=True).start()
    try:
        ok, value = box.get(timeout=timeout_s)
    except queue.Empty:
        raise CallTimedOutError(f"did not finish within {timeout_s:.1f}s") from None
    if not ok:
        assert isinstance(value, BaseException)
        raise value
    return cast(_T, value)


@contextlib.contextmanager
def _listing_policy(dir_dataless: bool) -> Iterator[None]:
    """Allow the provider to fetch a dataless directory's child list (metadata only); else no change."""
    if dir_dataless:
        with materialize_allowed():
            yield
    else:
        yield


def _list_dir(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
    with _listing_policy(dir_dataless), os.scandir(path) as it:
        return list(it)


def _file_matcher(include: Sequence[str], exclude: Sequence[str]) -> Callable[[str], bool]:
    """``paths.is_included`` with each glob compiled once per walk (it recompiles and re-parses per call).

    Same regexes as ``paths.glob_match``; walk-built rel paths are already POSIX (no empty or dot parts), so
    ``PurePosixPath(rel).as_posix() == rel`` and the result is identical.
    """
    inc = _any_of(include)
    exc = _any_of(exclude)

    def included(rel: str) -> bool:
        if inc is not None and inc.match(rel) is None:
            return False
        return exc is None or exc.match(rel) is None

    return included


def _any_of(patterns: Sequence[str]) -> re.Pattern[str] | None:
    """One alternation of the globs' anchored, case-insensitive regexes (one match call per path)."""
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{_glob_regex(p).pattern})" for p in patterns), re.IGNORECASE)


def _effective_exclude(cfg: SourceConfig) -> tuple[str, ...]:
    """``cfg.exclude`` plus the globs its kind always drops: the exclude list every walk of ``cfg`` uses."""
    extra = tuple(p for p in always_excluded(cfg.kind) if p not in cfg.exclude)
    return (*cfg.exclude, *extra)


def _scope_matcher(cfg: SourceConfig) -> Callable[[str], bool]:
    """:func:`in_scope` for one source with the globs compiled once and each directory tested once, however
    many files under it are asked about."""
    exclude = _effective_exclude(cfg)
    included = _file_matcher(cfg.include, exclude)
    pruned: dict[str, bool] = {"": False}  # rel dir -> it, or a directory above it, matches an exclude glob

    def dir_pruned(rel_dir: str) -> bool:
        todo: list[str] = []
        while (known := pruned.get(rel_dir)) is None:
            todo.append(rel_dir)
            rel_dir = rel_dir.rpartition("/")[0]
        for d in reversed(todo):
            known = pruned[d] = known or _dir_excluded(d, exclude)
        return known

    def matches(rel_path: str) -> bool:
        return not dir_pruned(rel_path.rpartition("/")[0]) and included(rel_path)

    return matches


def in_scope(cfg: SourceConfig, rel_path: str) -> bool:
    """True when a walk of ``cfg`` lists a file at ``rel_path`` (POSIX, relative to the root, as ``walk``
    builds it).

    The walk's two rules as one predicate: no directory above the file matches an exclude glob
    (``_dir_excluded``: the walk never descends into it, with or without a trailing ``/`` on the glob), and
    the file passes include/exclude (``_file_matcher``).  The exclude list is the effective one:
    ``cfg.exclude`` plus ``config.always_excluded(cfg.kind)``.  ``paths.is_included`` alone is not this rule:
    with ``exclude = ["Archive"]`` it keeps ``a/Archive/x.pdf``, a file the walk never reaches.  This form
    parses the globs on every call; ``LocalArm.in_scope`` keeps them for the arm's life.
    """
    return _scope_matcher(cfg)(rel_path)


def walk(
    root: Path,
    *,
    source_id: str,
    volume: str,
    include: Sequence[str],
    exclude: Sequence[str],
    with_gen_count: bool = True,
    known: Mapping[str, _KnownStat] | None = None,
    listing_timeout_s: float = LISTING_TIMEOUT_S,
) -> tuple[list[SourceItem], WalkStats]:
    """Walk ``root`` with os.scandir + lstat: never follows symlinks, never opens or reads a file.

    Emits files only (is_dir=False), sorted by rel_path; rel_path is POSIX, NFC-normalised, relative to
    ``root``.  Directories are pruned only by ``exclude`` (``include`` applies to files).  Each item carries
    size, mtime_ns, ctime_ns, created_ns (st_birthtime), ino, mode, dataless (SF_DATALESS), gen_count.
    A directory under ~/Library/CloudStorage with zero children, or one raising EPERM/EACCES, is recorded in
    ``unknown_dirs`` and never read as empty.  Raises FileNotFoundError if ``root`` does not exist, and
    CallTimedOutError when one directory listing does not return within ``listing_timeout_s`` (macOS holds a
    read until someone clicks Allow on a privacy prompt; every later listing would wait on the same prompt, so
    the walk stops rather than reading anything as empty).

    ``known`` (stable_id -> stored H0, from ``Manifest.observation_index``) makes getattrlist conditional:
    a file whose lstat tuple ``(size, mtime_ns, ctime_ns, ino, mode)`` equals its stored row, and whose row
    holds a gen_count and creation time, reuses them instead of a per-file getattrlist.  Any write, chmod,
    rename or link change moves ctime, which APFS (and File Provider) cannot preserve, so an equal tuple
    means the inode was not modified since the row was stamped; gen_count is re-read for every file whose
    tuple moved and for every new file, where it is the change token the next pass compares.
    """
    root_st = os.lstat(root)  # FileNotFoundError propagates
    if stat.S_ISLNK(root_st.st_mode):
        raise NotADirectoryError(
            errno_mod.ENOTDIR, "source root is a symlink; configure the canonical path", str(root)
        )
    if not stat.S_ISDIR(root_st.st_mode):
        raise NotADirectoryError(errno_mod.ENOTDIR, "source root is not a directory", str(root))

    state = _WalkState()
    cloud_roots = _cloud_roots()
    included = _file_matcher(include, exclude)
    root_dev = root_st.st_dev
    known_get = known.get if known else None
    append = state.items.append
    # (absolute dir, rel prefix, lstat of the dir)
    stack: list[tuple[Path, str, os.stat_result]] = [(root, "", root_st)]
    while stack:
        dir_path, rel_dir, dir_st = stack.pop()
        shown = rel_dir or "."
        try:
            entries = call_with_timeout(
                functools.partial(_list_dir, dir_path, dir_dataless=is_dataless(dir_st)),
                listing_timeout_s,
                name="agentsync-listing",
            )
        except CallTimedOutError:
            raise CallTimedOutError(
                f"listing {shown!r} did not return within {listing_timeout_s:.0f}s"
            ) from None
        except OSError as exc:
            # EPERM/EACCES (TCC), EDEADLK/ETIMEDOUT (provider), ENOENT (vanished mid-walk), anything else:
            # the directory's contents are unknown this pass, never empty.
            code = errno_mod.errorcode.get(exc.errno or 0, "E?")
            reason = f"{code}: {exc.strerror or exc}"
            state.unknown[shown] = reason
            log.warning("%s: directory %r is unknown (%s)", source_id, shown, reason)
            continue
        state.dirs += 1
        if not entries and _is_cloud_tree(dir_path, cloud_roots):
            state.unknown[shown] = "zero children in a cloud tree" + (
                " (dataless)" if is_dataless(dir_st) else ""
            )
            if rel_dir:
                state.empty.append(rel_dir)
            # info: the scan's one alarm names these each pass; a line per folder per pass filled the logs.
            log.info(
                "%s: directory %r has zero children in a cloud tree; treated as unknown", source_id, shown
            )
            continue
        prefix = f"{rel_dir}/" if rel_dir else ""
        for entry in entries:
            name = entry.name
            if not name.isascii():
                name = _nfc(name)
                try:
                    name.encode("utf-8")
                except UnicodeEncodeError:
                    state.excluded += 1
                    log.warning("%s: skipping undecodable name %r in %r", source_id, entry.name, shown)
                    continue
            rel = prefix + name
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                log.debug("%s: %r vanished during the walk", source_id, rel)
                continue
            except OSError as exc:
                state.unknown[shown] = f"lstat {name!r}: {exc.strerror}"
                log.warning("%s: cannot lstat %r (%s); %r is unknown", source_id, rel, exc.strerror, shown)
                continue
            mode = st.st_mode
            fmt = mode & _S_IFMT
            if fmt == _S_IFLNK:
                state.symlinks += 1
                continue
            if fmt == _S_IFDIR:
                if _dir_excluded(rel, exclude):
                    state.excluded += 1
                    continue
                if st.st_dev != root_dev:
                    state.excluded += 1
                    log.warning("%s: not crossing into another volume at %r", source_id, rel)
                    continue
                stack.append((Path(entry.path), rel, st))
                continue
            if fmt != _S_IFREG or not included(rel):
                state.excluded += 1
                continue
            prior = known_get(stable_id_for(volume, st.st_ino)) if known_get is not None else None
            if with_gen_count and prior is not None and _reusable(prior, st):
                # The common no-op case: the stored gen_count/creation time stand (see the docstring).
                append(
                    SourceItem(
                        source_id=source_id,
                        stable_id=stable_id_for(volume, st.st_ino),
                        rel_path=rel,
                        name=name,
                        size=st.st_size,
                        mtime_ns=st.st_mtime_ns,
                        ctime_ns=st.st_ctime_ns,
                        dataless=is_dataless(st),
                        gen_count=prior.gen_count,
                        remote_hashes=_NO_HASHES,
                        created_ns=prior.created_ns,
                        ino=st.st_ino,
                        mode=mode,
                        extra=_NO_EXTRA,
                    )
                )
                continue
            _emit(
                state,
                entry.path,
                rel,
                name,
                st,
                source_id=source_id,
                volume=volume,
                with_gen_count=with_gen_count,
            )

    items = _dedupe_hard_links(state, source_id)
    stats = WalkStats(
        files=len(items),
        dirs=state.dirs,
        dataless=sum(1 for i in items if i.dataless),
        excluded=state.excluded,
        symlinks_skipped=state.symlinks,
        unknown_dirs=tuple(sorted(state.unknown)),
        sentinel_present=None,
        empty_cloud_dirs=tuple(sorted(state.empty)),
    )
    return items, stats


_EXCLUDE_SHOWN = 5  # folders named in one ready-to-paste exclude line; the rest are a count
_GLOB_CHARS = re.compile(r"[*?\[]")


def _unexcluded(cfg: SourceConfig, dirs: Sequence[str]) -> list[str]:
    """``dirs`` (folders of the source, as relative POSIX paths) without those its ``exclude`` prunes today,
    at the folder itself or at a folder above it: what a walk under the current sources.toml still reaches."""
    exclude = (*cfg.exclude, *(p for p in always_excluded(cfg.kind) if p not in cfg.exclude))

    def pruned(rel: str) -> bool:
        parts = rel.split("/")
        return any(_dir_excluded("/".join(parts[: i + 1]), exclude) for i in range(len(parts)))

    return [d for d in dirs if not pruned(d)]


def exclude_advice(cfg: SourceConfig, empty: Sequence[str]) -> str:
    """What to do about ``empty`` (zero-child cloud folders of the source, ``WalkStats.empty_cloud_dirs``):
    a ready-to-paste ``exclude = [...]`` line for the source's table in sources.toml. The caller passes only
    folders with no mirrored file below them: excluding a folder retires its pages. The line keeps the globs
    in force (the configured or default ``exclude``, without the always-excluded ones, which a hand-written
    list cannot drop) and adds the first five folders, each anchored at the root (``/<path>/``) with its glob
    characters made literal; more than five are a count."""
    kept = [g for g in cfg.exclude if g not in always_excluded(cfg.kind)]
    globs = [f"/{_GLOB_CHARS.sub(_glob_literal, rel)}/" for rel in empty[:_EXCLUDE_SHOWN]]
    listed = ", ".join(json.dumps(g, ensure_ascii=False) for g in (*kept, *globs))
    more = len(empty) - len(globs)
    rest = f" (+{more} more: status names them once these are excluded)" if more > 0 else ""
    return (
        f"set exclude = [{listed}] in [[source]] id = {cfg.id!r} in sources.toml{rest}; an excluded folder "
        "is not mirrored if it later gains files"
    )


def _glob_literal(m: re.Match[str]) -> str:
    """A glob character of a folder name, to match itself: ``[*]`` and ``[?]``; ``[`` as ``?`` (any one
    character), since ``[[]`` compiles to a regex Python warns about on every walk."""
    return "?" if m.group(0) == "[" else f"[{m.group(0)}]"


def _reusable(prior: _KnownStat, st: os.stat_result) -> bool:
    """True when the stored row's stat tuple equals this lstat and it holds gen_count + creation time."""
    return (
        bool(prior.gen_count)
        and prior.created_ns is not None
        and prior.ctime_ns == st.st_ctime_ns
        and prior.mtime_ns == st.st_mtime_ns
        and prior.size == st.st_size
        and prior.ino == st.st_ino
        and prior.mode == st.st_mode
    )


def _emit(
    state: _WalkState,
    path: str,
    rel: str,
    name: str,
    st: os.stat_result,
    *,
    source_id: str,
    volume: str,
    with_gen_count: bool,
) -> None:
    """Append one file read in full: gen_count and creation time from getattrlist (never opens it)."""
    stable_id = stable_id_for(volume, st.st_ino)
    gen: int | None = None
    created = _birthtime_ns(st)
    if with_gen_count:
        try:
            attrs = _file_attrs(Path(path))
        except FileNotFoundError:
            log.debug("%s: %r vanished during the walk", source_id, rel)
            return
        except OSError as exc:
            log.debug("%s: getattrlist %r failed (%s); gen_count unknown", source_id, rel, exc)
        else:
            gen = attrs.gen_count
            if attrs.created_ns is not None:
                created = attrs.created_ns
    state.items.append(
        SourceItem(
            source_id=source_id,
            stable_id=stable_id,
            rel_path=rel,
            name=name,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
            dataless=is_dataless(st),
            gen_count=gen,
            remote_hashes=_NO_HASHES,
            created_ns=created,
            ino=st.st_ino,
            mode=st.st_mode,
            extra=_NO_EXTRA,
        )
    )


def _dedupe_hard_links(state: _WalkState, source_id: str) -> list[SourceItem]:
    """Sort by rel_path and keep the first path of each inode (identity is the inode, never the path)."""
    seen: set[str] = set()
    out: list[SourceItem] = []
    for item in sorted(state.items, key=lambda i: i.rel_path):
        if item.stable_id in seen:
            state.excluded += 1
            log.warning("%s: %r is a hard link to an already-walked inode; skipped", source_id, item.rel_path)
            continue
        seen.add(item.stable_id)
        out.append(item)
    return out


# ---------------------------------------------------------------------------------------------------------
# inbox dedup
# ---------------------------------------------------------------------------------------------------------


@functools.cache
def _local_host_names() -> tuple[str, ...]:
    """This Mac's host names, as OneDrive appends them to conflict copies (``Name-<host>.ext``)."""
    names: set[str] = set()
    for raw in (socket.gethostname(), os.uname().nodename):
        base = raw.strip()
        if base.endswith(".local"):
            base = base[: -len(".local")]
        if base:
            names.add(base)
            names.add(base.split(".", 1)[0])
    return tuple(sorted((n for n in names if len(n) >= 2), key=lambda n: (-len(n), n)))


def fold_conflict_suffix(name: str) -> str:
    """Normalise a name for inbox dedup: NFC, strip ``-<COMPUTERNAME>``, `` (1)``, `` - Copy`` suffixes."""
    name = _nfc(name)
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    hosts = _local_host_names()
    changed = True
    while changed:
        changed = False
        candidates = [r.sub("", stem) for r in (*_COPY_SUFFIX_RES, _WINDOWS_DEFAULT_HOST_RE)]
        candidates += [stem[: -len(h) - 1] for h in hosts if stem.lower().endswith("-" + h.lower())]
        for new in candidates:
            if new != stem and new.strip():
                stem = new
                changed = True
                break
    return stem + ext


# ---------------------------------------------------------------------------------------------------------
# arms
# ---------------------------------------------------------------------------------------------------------


def _staging_dest(dest_dir: Path, item: SourceItem) -> Path:
    """Unique staging path per item that keeps the original file name (converters route by suffix)."""
    key = hashlib.sha256(f"{item.source_id}\0{item.stable_id}".encode()).hexdigest()[:16]
    name = item.name
    if not name or "/" in name or "\0" in name or name in (".", ".."):
        name = "file" + item.suffix
    return dest_dir / key / name


class LocalArm:
    """SourceArm for kind ``local``: every scan is a FULL enumeration (T3 is the truth tier)."""

    source_id: str
    kind: SourceKind

    def __init__(self, cfg: SourceConfig) -> None:
        """Bind to one configured local source; raises ConfigError if cfg.kind is not LOCAL/INBOX."""
        if cfg.kind not in (SourceKind.LOCAL, SourceKind.INBOX):
            raise ConfigError(f"source {cfg.id!r}: kind {cfg.kind.value!r} is not served by the local arm")
        if cfg.path is None:
            raise ConfigError(f"source {cfg.id!r}: kind {cfg.kind.value!r} needs 'path'")
        self.cfg = cfg
        self.source_id = cfg.id
        self.kind = cfg.kind
        self.root = cfg.path
        self.last_stats: WalkStats | None = None  # stats of the most recent scan (for STATE.md / reports)
        # stable_id -> stored H0 of this source (``Manifest.observation_index``), set by the cycle before a
        # scan: files whose lstat tuple still matches skip the per-file getattrlist (see ``walk``).
        self.known_h0: Mapping[str, _KnownStat] | None = None
        self.listing_timeout_s = LISTING_TIMEOUT_S  # per directory listing (see ``walk``); tests shorten it
        self._volume: str | None = None
        self._scope: Callable[[str], bool] | None = None  # compiled by the first ``in_scope`` call

    # -- helpers -------------------------------------------------------------------------------------------

    def _exclude(self) -> tuple[str, ...]:
        return _effective_exclude(self.cfg)

    def _volume_uuid(self) -> str:
        if self._volume is None:
            self._volume = volume_uuid(self.root)
        return self._volume

    def _incomplete(self, alarm: str) -> ScanResult:
        log.warning("%s: %s", self.source_id, alarm)
        return ScanResult(
            source_id=self.source_id,
            pass_kind=PassKind.FULL,
            items=(),
            new_cursor=None,
            enumeration_complete=False,
            alarms=(alarm,),
        )

    def _held(self, what: str) -> ScanResult:
        """A read past ``listing_timeout_s``. Recorded like EPERM: the source is unknown this pass, never
        empty, so nothing is tombstoned; ``listing_held`` tells the cycle not to read anything else under the
        root this pass (each read would wait on the same prompt)."""
        alarm = (
            f"walk of {self.root} stopped: {what}. macOS is most likely waiting for you to click Allow on a "
            "privacy prompt (it can sit behind other windows): click Allow, then re-run the sync "
            "(enumeration incomplete, nothing is deleted)"
        )
        return dataclasses.replace(self._incomplete(alarm), listing_held=True)

    def _sentinel_present(self, items: Sequence[SourceItem]) -> bool | None:
        sentinel = self.cfg.sentinel
        if sentinel is None:
            return None
        want = _nfc(PurePosixPath(sentinel).as_posix())
        if any(i.rel_path == want for i in items):
            return True
        try:  # a directory sentinel: present if it is a real (non-symlink) directory under the root
            return stat.S_ISDIR(os.lstat(self.root / want).st_mode)
        except OSError:
            return False

    def _known_file_count(self) -> int:
        """Files the manifest holds for this source (from ``known_h0``, the cycle's observation index)."""
        if not self.known_h0:
            return 0
        return sum(
            1
            for row in self.known_h0.values()
            if not getattr(row, "is_dir", False) and str(getattr(row, "state", "live")) != "tombstone"
        )

    def _walk_scan(self) -> tuple[list[SourceItem], WalkStats, list[str]] | ScanResult:
        """Walk the root; returns (items, stats, alarms) or an incomplete, empty ScanResult."""

        def root_reads() -> tuple[os.stat_result, str | OSError | None]:
            root_st = os.lstat(self.root)
            if not stat.S_ISDIR(root_st.st_mode):  # a symlink or a file: refused below, no volume read
                return root_st, None
            try:
                return root_st, self._volume_uuid()
            except OSError as exc:
                return root_st, exc

        # The root's own metadata reads share the listings' time limit: a privacy prompt may hold them too.
        try:
            root_st, volume = call_with_timeout(root_reads, self.listing_timeout_s, name="agentsync-root")
        except CallTimedOutError:
            return self._held(f"reading the root did not return within {self.listing_timeout_s:.0f}s")
        except FileNotFoundError:
            return self._incomplete(f"source root missing: {self.root} (walk skipped; nothing is deleted)")
        except OSError as exc:
            return self._incomplete(f"source root unreadable: {self.root}: {exc.strerror}")
        if stat.S_ISLNK(root_st.st_mode):
            return self._incomplete(f"source root is a symlink: {self.root}; configure the canonical path")
        if not stat.S_ISDIR(root_st.st_mode):
            return self._incomplete(f"source root is not a directory: {self.root}")
        if not isinstance(volume, str):
            return self._incomplete(f"volume UUID unavailable for {self.root}: {volume}")
        try:
            items, stats = walk(
                self.root,
                source_id=self.source_id,
                volume=volume,
                include=self.cfg.include,
                exclude=self._exclude(),
                known=self.known_h0,
                listing_timeout_s=self.listing_timeout_s,
            )
        except FileNotFoundError:
            return self._incomplete(f"source root vanished during the walk: {self.root}")
        except CallTimedOutError as exc:
            return self._held(str(exc))
        except OSError as exc:
            return self._incomplete(f"walk failed at {self.root}: {exc}")
        sentinel = self._sentinel_present(items)
        stats = dataclasses.replace(stats, sentinel_present=sentinel)
        alarms: list[str] = []
        known_files = self._known_file_count()
        if not items and stats.dirs <= 1 and not stats.unknown_dirs and not stats.excluded and known_files:
            # An existing but EMPTY root (an unmounted volume's mount point, a sync client that has not
            # populated it yet) while files are mirrored: a scope with zero children is unknown, never
            # "everything was deleted" (design 4.7), sentinel or not.
            stats = dataclasses.replace(stats, unknown_dirs=(".",))
            alarms.append(
                f"source root {self.root} is empty but {known_files} file(s) are mirrored "
                "(unmounted volume?): "
                "enumeration incomplete, nothing is deleted"
            )
        if sentinel is False:
            alarms.append(
                f"sentinel {self.cfg.sentinel!r} missing under {self.root}: enumeration treated as incomplete"
            )
        if stats.unknown_dirs:
            shown = ", ".join(repr(d) for d in stats.unknown_dirs[:5])
            more = f" (+{len(stats.unknown_dirs) - 5} more)" if len(stats.unknown_dirs) > 5 else ""
            alarms.append(
                f"{len(stats.unknown_dirs)} unknown dir(s) — permission denied (TCC), provider error, or "
                f"zero children in a cloud tree: {shown}{more}; enumeration incomplete, no deletions "
                "this pass (exclude a deliberately empty cloud folder, or grant the agent Files and "
                "Folders access)"
            )
        return items, stats, alarms

    # -- SourceArm -----------------------------------------------------------------------------------------

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Walk the source root; ``cursor`` and ``full`` are ignored (always FULL, new_cursor=None).

        enumeration_complete is True only if the root exists, the sentinel (if configured) is present, and
        ``unknown_dirs`` is empty; otherwise False with an alarm naming the cause.  Root missing -> an empty
        ScanResult with enumeration_complete=False (never mass deletion).
        """
        del cursor, full
        walked = self._walk_scan()
        if isinstance(walked, ScanResult):
            self.last_stats = None
            return walked
        items, stats, alarms = walked
        self.last_stats = stats
        complete = stats.sentinel_present is not False and not stats.unknown_dirs
        log.info(
            "%s: walked %d files, %d dirs (%d dataless, %d excluded, %d symlinks skipped); complete=%s",
            self.source_id,
            stats.files,
            stats.dirs,
            stats.dataless,
            stats.excluded,
            stats.symlinks_skipped,
            complete,
        )
        return ScanResult(
            source_id=self.source_id,
            pass_kind=PassKind.FULL,
            items=tuple(items),
            new_cursor=None,
            enumeration_complete=complete,
            unknown_dirs=stats.unknown_dirs,
            alarms=tuple(alarms),
        )

    def in_scope(self, rel_path: str) -> bool:
        """:func:`in_scope` for this source, compiled once per arm.  The cycle asks it about queued rows and
        about rows an incomplete pass did not list: a file the config no longer covers is not fetched, and
        its row is retired."""
        if self._scope is None:
            self._scope = _scope_matcher(self.cfg)
        return self._scope(rel_path)

    def _source_path(self, item: SourceItem) -> Path:
        rel = PurePosixPath(item.rel_path)
        if rel.is_absolute() or not rel.parts or any(p in ("", ".", "..") for p in rel.parts):
            raise MaterialiseError(item.rel_path, None, "rel_path escapes the source root")
        return self.root.joinpath(*rel.parts)

    def _check_identity(self, path: Path, item: SourceItem) -> None:
        st = os.lstat(path)  # FileNotFoundError propagates
        if item.ino is not None and st.st_ino != item.ino:
            raise FileNotFoundError(
                errno_mod.ENOENT, f"inode changed ({item.ino} -> {st.st_ino}); re-classify", str(path)
            )

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Materialise ``root/item.rel_path`` into ``dest_dir`` via ``materialise.materialise``.

        Re-lstats first: if the inode no longer matches ``item.ino`` raises FileNotFoundError (re-classify).
        """
        if item.source_id != self.source_id:
            raise ValueError(f"item of source {item.source_id!r} fetched through arm {self.source_id!r}")
        path = self._source_path(item)
        self._check_identity(path, item)
        dest = _staging_dest(dest_dir, item)
        result = materialise(path, dest, budget)
        try:
            self._check_identity(path, item)  # a safe-save between our lstat and materialise's
        except FileNotFoundError:
            with contextlib.suppress(FileNotFoundError):
                dest.unlink()
            raise
        return FetchResult(
            stable_id=item.stable_id, path=result.dest, size=result.size, content_sha256=result.content_sha256
        )


@dataclasses.dataclass
class SettleBudget:
    """The wait one interactive sync has left for settling inbox files; every inbox arm of the cycle shares
    one (field N4)."""

    remaining_s: float = INBOX_SETTLE_MAX_S


class InboxArm(LocalArm):
    """SourceArm for kind ``inbox``: LocalArm plus quiescence, lock-file ignores and max(created,
    modified)."""

    def __init__(self, cfg: SourceConfig) -> None:
        """Bind to one configured inbox source (see LocalArm)."""
        super().__init__(cfg)
        self._clock: Callable[[], int] = time.time_ns  # wall clock in ns; replaceable in tests
        self._sleep: Callable[[float], None] = time.sleep  # replaceable in tests
        # Set by the cycle for an interactive sync (field N4): when the only gap in a walk is withheld files,
        # wait until the youngest settles and list the inbox once more, drawing the wait from this budget.
        self.settle: SettleBudget | None = None

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """As LocalArm.scan, but items whose size/mtime changed within ``quiescence_s`` are withheld.

        mtime_ns is reported as max(created_ns, mtime_ns) (a copied file keeps its original mtime).  Withheld
        items make enumeration_complete False (they are neither new nor absent this pass).  With ``settle``
        set and withheld files the walk's only gap, the arm sleeps until the youngest of them leaves the
        window, at most what ``settle`` has left (nothing when even the oldest would not settle in that time;
        a future-dated file is never waited for and stays withheld), and walks once more; what is still
        withheld then stays withheld.
        """
        del cursor, full
        quiescence_ns = self.cfg.quiescence_s * 1_000_000_000
        for attempt in range(2):
            walked = self._walk_scan()
            if isinstance(walked, ScanResult):
                self.last_stats = None
                return walked
            items, stats, alarms = walked
            self.last_stats = stats
            now = self._clock()
            horizon = now - quiescence_ns
            kept: list[SourceItem] = []
            withheld: list[str] = []
            settling: list[int] = []  # withheld stamps that leave the window in time (not future-dated)
            for item in items:
                created = item.created_ns if item.created_ns is not None else item.mtime_ns
                stamp = max(item.mtime_ns, item.ctime_ns, created)
                if stamp > horizon:
                    withheld.append(item.rel_path)
                    if stamp <= now:
                        settling.append(stamp)
                    continue
                kept.append(
                    dataclasses.replace(
                        item,
                        mtime_ns=max(created, item.mtime_ns),
                        extra={"dedup_name": fold_conflict_suffix(item.name)},
                    )
                )
            budget = self.settle
            only_withheld = stats.sentinel_present is not False and not stats.unknown_dirs
            if attempt or budget is None or not settling or not only_withheld:
                break
            if (min(settling) - horizon) / 1e9 > budget.remaining_s:
                break  # not even the oldest settles in the time this sync has left
            wait_s = min((max(settling) - horizon) / 1e9, budget.remaining_s)  # until the youngest leaves
            budget.remaining_s -= wait_s
            log.warning(
                "%s: %d inbox item(s) still settling; waiting %.0fs, then listing the inbox once more",
                self.source_id,
                len(withheld),
                wait_s,
            )
            self._sleep(wait_s)
        if withheld:
            shown = ", ".join(repr(p) for p in withheld[:5])
            more = f" (+{len(withheld) - 5} more)" if len(withheld) > 5 else ""
            alarms.append(
                f"{len(withheld)} inbox item(s) withheld: changed within the last {self.cfg.quiescence_s}s "
                f"(still being written?): {shown}{more}"
            )
        complete = stats.sentinel_present is not False and not stats.unknown_dirs and not withheld
        log.info(
            "%s: inbox walk %d files (%d withheld, %d excluded); complete=%s",
            self.source_id,
            stats.files,
            len(withheld),
            stats.excluded,
            complete,
        )
        return ScanResult(
            source_id=self.source_id,
            pass_kind=PassKind.FULL,
            items=tuple(kept),
            new_cursor=None,
            enumeration_complete=complete,
            unknown_dirs=stats.unknown_dirs,
            alarms=tuple(alarms),
        )

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """As LocalArm.fetch, plus two-read agreement: hash differs across two reads -> MaterialiseError."""
        first = super().fetch(item, dest_dir, budget)
        path = self._source_path(item)
        try:
            with materialize_allowed():
                second = sha256_file(path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                first.path.unlink()
            raise
        if second != first.content_sha256:
            with contextlib.suppress(FileNotFoundError):
                first.path.unlink()
            raise MaterialiseError(
                str(path), None, "unstable: two reads disagree (still being written?); retried next cycle"
            )
        return first
