"""Fail-closed hydration discipline and the ONE place a File Provider download may happen (owner: local).

Constants are from the macOS 15 SDK ``sys/resource.h`` / ``sys/stat.h``.  Every function here is macOS-only;
on another platform ``set_materialize_policy`` raises OSError(ENOSYS).

Measured on this Mac (macOS 15.7.9, OneDrive File Provider, see ``verify/C14-file-provider.md``): a login
shell's default policy is ON, a launchd job's is OFF; with the policy OFF ``open()`` of a dataless file
succeeds and ``read()`` fails EDEADLK (11); right after sign-in a read can fail ETIMEDOUT (60).  A
THREAD-scope ON overrides a PROCESS-scope OFF for the calling thread only (measured 2026-09-29 on
OneDrive-Contoso: a dataless file downloaded with the process policy OFF), which is what
:func:`materialize_allowed` uses.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno as errno_mod
import functools
import hashlib
import logging
import os
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from agentsync.errors import DatalessRefusedError, MaterialiseError, ProviderTimeoutError
from agentsync.model import ByteBudget

log = logging.getLogger(__name__)

IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES = 3
IOPOL_SCOPE_PROCESS = 0
IOPOL_SCOPE_THREAD = 1
IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT = 0
IOPOL_MATERIALIZE_DATALESS_FILES_OFF = 1
IOPOL_MATERIALIZE_DATALESS_FILES_ON = 2

SF_DATALESS = 0x40000000

EDEADLK = 11  # macOS errno: materialisation refused for this context
ETIMEDOUT = 60  # macOS errno: provider warming up; retry with backoff

_VALID_POLICIES = frozenset(
    {
        IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT,
        IOPOL_MATERIALIZE_DATALESS_FILES_OFF,
        IOPOL_MATERIALIZE_DATALESS_FILES_ON,
    }
)
_VALID_SCOPES = frozenset({IOPOL_SCOPE_PROCESS, IOPOL_SCOPE_THREAD})
_POLICY_NAMES = {0: "default", 1: "off", 2: "on"}
_BUF_SIZE = 1 << 20  # copy buffer for materialise()
# Hydrating a dataless OneDrive file rewrote st_mtime_ns by 111 ns (measured 2026-09-29 on OneDrive-Contoso:
# ...856329330 -> ...856329441, a double-precision round-trip), so a hydrated file is "stable" within 1 ms.
_HYDRATION_MTIME_SLACK_NS = 1_000_000


@dataclass(frozen=True, slots=True)
class MaterialiseResult:
    """Outcome of copying one source file into staging."""

    src: Path
    dest: Path
    size: int
    content_sha256: str
    was_dataless: bool
    attempts: int


# ---------------------------------------------------------------------------------------------------------
# policy syscalls
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _IoPolicyApi:
    """The two libSystem entry points, bound once with argtypes."""

    get: Callable[[int, int], int]
    set: Callable[[int, int, int], int]


@functools.cache
def _iopolicy_api() -> _IoPolicyApi:
    """Bind getiopolicy_np/setiopolicy_np from libSystem (raises OSError(ENOSYS) off macOS)."""
    if sys.platform != "darwin":
        raise OSError(errno_mod.ENOSYS, f"setiopolicy_np is macOS-only (platform {sys.platform})")
    libc = ctypes.CDLL(None, use_errno=True)
    get = libc.getiopolicy_np
    get.argtypes = [ctypes.c_int, ctypes.c_int]
    get.restype = ctypes.c_int
    set_ = libc.setiopolicy_np
    set_.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    set_.restype = ctypes.c_int
    return _IoPolicyApi(get=get, set=set_)


def _raise_errno(what: str) -> None:
    err = ctypes.get_errno() or errno_mod.EINVAL
    raise OSError(err, f"{what}: {os.strerror(err)}")


def _check_scope(scope: int) -> None:
    if scope not in _VALID_SCOPES:
        raise OSError(errno_mod.EINVAL, f"invalid iopolicy scope {scope!r}")


def is_dataless(st: os.stat_result) -> bool:
    """True when ``st.st_flags`` carries SF_DATALESS (an online-only placeholder; lstat does not hydrate)."""
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def download_cost(st: os.stat_result) -> int:
    """The bytes reading this file costs the materialise byte budget: its size when it is dataless
    (online-only: reading it downloads it), else 0 (already local: no download)."""
    return max(st.st_size, 0) if is_dataless(st) else 0


def get_materialize_policy(scope: int = IOPOL_SCOPE_PROCESS) -> int:
    """Return getiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, scope); raises OSError."""
    _check_scope(scope)
    api = _iopolicy_api()
    ctypes.set_errno(0)
    value = api.get(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, scope)
    if value < 0:
        _raise_errno("getiopolicy_np")
    return value


def set_materialize_policy(policy: int, scope: int = IOPOL_SCOPE_PROCESS) -> None:
    """Call setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, scope, policy) via ctypes; raises
    OSError."""
    _check_scope(scope)
    if policy not in _VALID_POLICIES:
        raise OSError(errno_mod.EINVAL, f"invalid materialize policy {policy!r}")
    api = _iopolicy_api()
    ctypes.set_errno(0)
    if api.set(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, scope, policy) != 0:
        _raise_errno("setiopolicy_np")


def fail_closed() -> None:
    """Set the PROCESS policy to OFF (inherited by children): any accidental read of a placeholder -> EDEADLK.

    The CLI calls this before any walk, subprocess or conversion.
    """
    before = get_materialize_policy(IOPOL_SCOPE_PROCESS)
    set_materialize_policy(IOPOL_MATERIALIZE_DATALESS_FILES_OFF, IOPOL_SCOPE_PROCESS)
    log.debug("materialize policy (process): %s -> off", _POLICY_NAMES.get(before, str(before)))


@contextlib.contextmanager
def _materialize_allowed() -> Iterator[None]:
    try:
        prev_thread = get_materialize_policy(IOPOL_SCOPE_THREAD)
        set_materialize_policy(IOPOL_MATERIALIZE_DATALESS_FILES_ON, IOPOL_SCOPE_THREAD)
    except OSError as exc:
        if exc.errno == errno_mod.ENOSYS:
            # Not macOS: there is no dataless state to materialise; nothing to toggle.
            log.debug("materialize_allowed: %s; no-op", exc)
            yield
            return
        log.warning("thread-scope materialize policy unavailable (%s); toggling process scope", exc)
        prev_process = get_materialize_policy(IOPOL_SCOPE_PROCESS)
        set_materialize_policy(IOPOL_MATERIALIZE_DATALESS_FILES_ON, IOPOL_SCOPE_PROCESS)
        try:
            yield
        finally:
            set_materialize_policy(prev_process, IOPOL_SCOPE_PROCESS)
        return
    try:
        yield
    finally:
        set_materialize_policy(prev_thread, IOPOL_SCOPE_THREAD)


def materialize_allowed() -> AbstractContextManager[None]:
    """Temporarily set the THREAD policy to ON (restoring the previous thread policy on exit).

    If thread scope proves insufficient on a given macOS build, the implementation may instead toggle process
    scope ON/OFF around the read; the launchd plist keeps ``MaterializeDatalessFiles=false`` regardless.
    """
    return _materialize_allowed()


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Return the hex sha256 of a file's bytes (callers must only pass non-dataless or staged files)."""
    st = path.stat()
    if is_dataless(st):
        raise DatalessRefusedError(
            str(path), EDEADLK, "refusing to hash a dataless placeholder; use materialise()"
        )
    digest = hashlib.sha256()
    buf = bytearray(max(1, chunk))
    view = memoryview(buf)
    with path.open("rb", buffering=0) as fh:
        while True:
            n = fh.readinto(buf)
            if not n:
                break
            digest.update(view[:n])
    return digest.hexdigest()


# ---------------------------------------------------------------------------------------------------------
# materialise
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Copied:
    size: int
    sha256: str


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _mtime_stable(before: int, after: int, *, was_dataless: bool) -> bool:
    """Exact mtime equality, except a dataless source may drift by < 1 ms when the provider hydrates it."""
    if not was_dataless:
        return before == after
    return abs(after - before) < _HYDRATION_MTIME_SLACK_NS


def _copy_once(src: Path, tmp_fd: int, st0: os.stat_result) -> _Copied:
    """Copy src into the (truncated) tmp fd under THREAD policy ON, hashing; raises OSError on I/O errors."""
    was_dataless = is_dataless(st0)
    os.ftruncate(tmp_fd, 0)
    os.lseek(tmp_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    buf = bytearray(_BUF_SIZE)
    view = memoryview(buf)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with materialize_allowed():
        fd = os.open(src, flags)
        try:
            opened = os.fstat(fd)
            if (opened.st_ino, opened.st_dev) != (st0.st_ino, st0.st_dev) or not stat.S_ISREG(opened.st_mode):
                raise MaterialiseError(str(src), None, "unstable: replaced between lstat and open")
            with os.fdopen(fd, "rb", buffering=0, closefd=False) as fh:
                while True:
                    n = fh.readinto(buf)
                    if not n:
                        break
                    chunk = view[:n]
                    digest.update(chunk)
                    written = 0
                    while written < n:
                        written += os.write(tmp_fd, chunk[written:])
                    total += n
            after = os.fstat(fd)
        finally:
            os.close(fd)
    if (
        after.st_size != st0.st_size
        or total != st0.st_size
        or not _mtime_stable(st0.st_mtime_ns, after.st_mtime_ns, was_dataless=was_dataless)
    ):
        raise MaterialiseError(
            str(src),
            None,
            f"unstable: changed during copy (size {st0.st_size}->{after.st_size}, read {total} bytes, "
            f"mtime_ns {st0.st_mtime_ns}->{after.st_mtime_ns})",
        )
    return _Copied(size=total, sha256=digest.hexdigest())


def materialise(
    src: Path,
    dest: Path,
    budget: ByteBudget,
    *,
    retries: int = 3,
    backoff_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> MaterialiseResult:
    """Copy ``src`` to ``dest`` (tmp + rename) hashing on the way, under ``materialize_allowed()``.

    Pre: ``src`` is a regular file (lstat, not a symlink). Charges ``budget`` BEFORE reading (raises
    BudgetExhaustedError without reading): one file always, and lstat().st_size bytes only when ``src`` is
    dataless (online-only, SF_DATALESS) at that lstat. The byte budget bounds downloads: an already-local file
    reads no provider bytes and never consumes it, so a budget of 0 still copies every local file. EDEADLK ->
    DatalessRefusedError (no retry); ETIMEDOUT -> retry ``retries`` times with exponential backoff, then
    ProviderTimeoutError; ENOENT -> FileNotFoundError propagates (vanished between walk and read: re-classify
    next cycle); size or mtime changed during the copy -> MaterialiseError("unstable"), dest removed. Post:
    dest holds exactly the bytes hashed into ``content_sha256``; src is never written."""
    st0 = os.lstat(src)  # FileNotFoundError propagates
    if stat.S_ISLNK(st0.st_mode):
        raise MaterialiseError(str(src), None, "refusing to read a symlink (never followed)")
    if not stat.S_ISREG(st0.st_mode):
        raise MaterialiseError(str(src), None, "not a regular file")
    was_dataless = is_dataless(st0)
    budget.charge(download_cost(st0))  # BudgetExhaustedError: nothing read

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name[:64]}.", suffix=".part", dir=dest.parent)
    tmp = Path(tmp_name)
    attempts = 0
    try:
        try:
            while True:
                attempts += 1
                try:
                    copied = _copy_once(src, tmp_fd, st0)
                    break
                except OSError as exc:
                    code = exc.errno
                    if code == errno_mod.ENOENT:
                        raise FileNotFoundError(
                            errno_mod.ENOENT, "vanished before it could be read", str(src)
                        ) from exc
                    if code == EDEADLK:
                        raise DatalessRefusedError(
                            str(src),
                            code,
                            "materialisation refused (EDEADLK): policy is OFF for this context",
                        ) from exc
                    if code == ETIMEDOUT:
                        if attempts > retries:
                            raise ProviderTimeoutError(
                                str(src),
                                code,
                                f"File Provider timed out (ETIMEDOUT) after {attempts} attempt(s)",
                            ) from exc
                        delay = backoff_s * (2 ** (attempts - 1))
                        log.info("%s: ETIMEDOUT on attempt %d; retrying in %.1fs", src, attempts, delay)
                        sleep(delay)
                        continue
                    if code == errno_mod.ELOOP:
                        raise MaterialiseError(
                            str(src), code, "became a symlink before it could be read"
                        ) from exc
                    raise MaterialiseError(str(src), code, f"read failed: {exc.strerror or exc}") from exc
        finally:
            os.close(tmp_fd)
        try:
            st1 = os.lstat(src)
        except FileNotFoundError as exc:
            raise FileNotFoundError(errno_mod.ENOENT, "vanished during the copy", str(src)) from exc
        if (st1.st_ino, st1.st_size) != (st0.st_ino, st0.st_size) or not _mtime_stable(
            st0.st_mtime_ns, st1.st_mtime_ns, was_dataless=was_dataless
        ):
            raise MaterialiseError(str(src), None, "unstable: replaced or modified during the copy")
        tmp.replace(dest)
    except BaseException:
        _unlink_quietly(tmp)
        raise
    log.debug(
        "materialised %s -> %s (%d bytes, dataless=%s, attempts=%d)",
        src,
        dest,
        copied.size,
        was_dataless,
        attempts,
    )
    return MaterialiseResult(
        src=src,
        dest=dest,
        size=copied.size,
        content_sha256=copied.sha256,
        was_dataless=was_dataless,
        attempts=attempts,
    )
