"""Single-writer ``flock`` lock and the outward liveness heartbeat (owner: ops).

The lock (design 4.7, CONTRACTS §14.2) is ``flock(LOCK_EX|LOCK_NB)`` on ``<state_dir>/agentsync.lock``; the
kernel drops it when the holder dies, so a *held* lock always names a live process.  The file body
``<pid> <boot_time> <iso8601> <label>`` exists for the other case: a body still present when the lock is
free means the previous holder died mid-cycle (a clean release truncates it first), so its scope is unknown
and the next pass must be a FULL enumeration.

Next to the lock sits a holder sidecar ``agentsync.lock.json`` (pid, host, boot_time, label, started_at,
last_beat_at, stage), rewritten atomically on acquire and on every :meth:`SingleWriterLock.beat`, removed on
release.  It lets ``doctor`` and :class:`LockHeldError` say how long a holder has been silent.

The per-source liveness store ``heartbeat.json`` (:func:`write_heartbeat`) is separate: it is written after
every completed pass and read by an outward watcher, never by the cycle itself.

The lock file itself is never unlinked (unlinking a flock'd file lets a second process lock a new inode at the
same path).  Its descriptor is opened ``O_CLOEXEC`` so a child process (git, pandoc) can never inherit it.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from agentsync.errors import LockHeldError
from agentsync.model import PassKind

log = logging.getLogger(__name__)

_BOOT_TOLERANCE_S = (
    60  # kern.boottime moves by a few seconds after clock corrections; reboots differ by far more
)
_LABEL_RE = re.compile(r"^\S{1,64}$")
_SYSCTL = "/usr/sbin/sysctl"
_BOOTTIME_RE = re.compile(r"sec\s*=\s*(\d+)")


def _utc_now_iso() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` accepted); None if malformed.  Naive values are taken as UTC."""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00") if text.endswith("Z") else text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LockInfo:
    """Body of the lock file: ``<pid> <boot_time> <iso8601> <label>``."""

    pid: int
    boot_time: int  # kern.boottime seconds
    started_at: str  # UTC ISO-8601
    label: str  # e.g. "poll", "reconcile", "cli"

    def render(self) -> str:
        """Return the one-line body."""
        return f"{self.pid} {self.boot_time} {self.started_at} {self.label}"

    @classmethod
    def parse(cls, text: str) -> LockInfo | None:
        """Parse a body; None if empty or malformed."""
        parts = text.strip().split()
        if len(parts) != 4:
            return None
        pid_s, boot_s, started_at, label = parts
        if not (pid_s.isdigit() and boot_s.isdigit()):
            return None
        pid, boot = int(pid_s), int(boot_s)
        if pid <= 0 or _parse_iso(started_at) is None:
            return None
        return cls(pid=pid, boot_time=boot, started_at=started_at, label=label)


@dataclass(frozen=True, slots=True)
class LockAcquisition:
    """Result of a successful acquire."""

    broke_stale: bool  # previous body named a dead pid or another boot: force a FULL pass this cycle
    previous: LockInfo | None


class _Timeval(ctypes.Structure):
    """``struct timeval`` as returned by ``sysctlbyname("kern.boottime")``."""

    _fields_ = (("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long))


def _boot_time_sysctlbyname() -> int:
    """kern.boottime via libc ``sysctlbyname`` (macOS / BSD)."""
    libc = ctypes.CDLL(None, use_errno=True)
    tv = _Timeval()
    size = ctypes.c_size_t(ctypes.sizeof(tv))
    rc = libc.sysctlbyname(b"kern.boottime", ctypes.byref(tv), ctypes.byref(size), None, ctypes.c_size_t(0))
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"sysctlbyname(kern.boottime): {os.strerror(err)}")
    if tv.tv_sec <= 0:
        raise OSError(errno.EINVAL, "sysctlbyname(kern.boottime) returned no time")
    return int(tv.tv_sec)


def _boot_time_sysctl_cmd() -> int:
    """kern.boottime via ``/usr/sbin/sysctl -n kern.boottime`` (``{ sec = N, usec = M } ...``)."""
    out = subprocess.run(
        [_SYSCTL, "-n", "kern.boottime"], capture_output=True, text=True, check=True, timeout=10
    ).stdout
    m = _BOOTTIME_RE.search(out)
    if not m:
        raise OSError(errno.EINVAL, f"unparseable kern.boottime: {out.strip()!r}")
    return int(m.group(1))


def _boot_time_proc_stat() -> int:
    """Boot time from ``/proc/stat`` ``btime`` (Linux, used only off macOS)."""
    for line in Path("/proc/stat").read_text(encoding="ascii").splitlines():
        if line.startswith("btime "):
            return int(line.split()[1])
    raise OSError(errno.ENOENT, "no btime in /proc/stat")


def boot_time() -> int:
    """Return kern.boottime seconds (sysctl) — distinguishes a pid reused across reboots."""
    errors: list[str] = []
    probes = (
        (_boot_time_sysctlbyname, _boot_time_sysctl_cmd)
        if sys.platform == "darwin"
        else (_boot_time_proc_stat, _boot_time_sysctlbyname)
    )
    for probe in probes:
        try:
            return probe()
        except (OSError, AttributeError, subprocess.SubprocessError, ValueError) as exc:
            errors.append(f"{probe.__name__}: {exc}")
    raise OSError(errno.ENOSYS, "cannot read the boot time: " + "; ".join(errors))


def pid_alive(pid: int) -> bool:
    """True if ``pid`` exists (os.kill(pid, 0); EPERM counts as alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _same_boot(a: int, b: int) -> bool:
    """True when two kern.boottime readings belong to the same boot (tolerates clock-correction drift)."""
    return abs(a - b) <= _BOOT_TOLERANCE_S


def _current_boot_or_zero() -> int:
    """boot_time(), or 0 (logged) when it cannot be read; 0 never matches a real boot."""
    try:
        return boot_time()
    except OSError as exc:
        log.warning("lock: %s; recording boot_time 0", exc)
        return 0


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file + fsync + rename, then fsync the dir."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fchmod(fh.fileno(), mode)
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    except OSError:  # some filesystems refuse fsync on a directory; the rename already happened
        pass
    finally:
        os.close(dfd)


def _holder_path(lock_path: Path) -> Path:
    """Return the holder sidecar path (``<lock>.json``)."""
    return lock_path.with_name(lock_path.name + ".json")


def _describe_holder(body: str, holder: Mapping[str, object] | None) -> str:
    """Human description of a lock holder for LockHeldError and doctor."""
    info = LockInfo.parse(body)
    if info is None:
        text = body.strip() or "<empty body>"
        return f"{text!r} (unparseable lock body)"
    out = f"pid {info.pid} ({info.label}) since {info.started_at}"
    if holder is not None and holder.get("pid") == info.pid:
        host = holder.get("host")
        beat = holder.get("last_beat_at")
        stage = holder.get("stage")
        if isinstance(host, str) and host:
            out += f" on {host}"
        if isinstance(beat, str):
            out += f", last beat {beat}"
            parsed = _parse_iso(beat)
            if parsed is not None:
                age = int((datetime.now(UTC) - parsed).total_seconds())
                out += f" ({age}s ago)"
        if isinstance(stage, str) and stage:
            out += f", stage {stage!r}"
    return out


class SingleWriterLock:
    """``flock(LOCK_EX|LOCK_NB)`` on StatePaths.lock.  The body is truncated on clean release."""

    def __init__(self, path: Path, label: str) -> None:
        """Bind to the lock path (parent dir created 0700)."""
        if not _LABEL_RE.match(label):
            raise ValueError(f"lock label must be 1-64 non-whitespace characters, got {label!r}")
        self._path = path
        self._label = label
        self._fd: int | None = None
        self._info: LockInfo | None = None
        self._host = socket.gethostname()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @property
    def path(self) -> Path:
        """The lock file."""
        return self._path

    @property
    def held(self) -> bool:
        """True while this instance holds the lock."""
        return self._fd is not None

    @property
    def info(self) -> LockInfo | None:
        """The body this instance wrote on acquire (None when not held)."""
        return self._info

    def acquire(self) -> LockAcquisition:
        """Take the lock or raise LockHeldError naming the holder's body.  A non-empty body left by a dead pid
        or another boot is 'stale': logged, overwritten, reported as broke_stale=True."""
        if self._fd is not None:
            raise RuntimeError(f"{self._path}: already acquired by this instance")
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(self._path, flags, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                body = _read_fd(fd)
                raise LockHeldError(_describe_holder(body, self.read_holder(self._path))) from None
            previous_body = _read_fd(fd)
            previous = LockInfo.parse(previous_body)
            current_boot = _current_boot_or_zero()
            broke_stale = bool(previous_body.strip())
            if broke_stale:
                log.warning(
                    "lock %s: breaking stale lock left by %s (%s); this cycle runs FULL passes",
                    self._path,
                    previous.render() if previous else repr(previous_body.strip()[:200]),
                    _stale_reason(previous, current_boot),
                )
            info = LockInfo(os.getpid(), current_boot, _utc_now_iso(), self._label)
            os.ftruncate(fd, 0)
            os.pwrite(fd, (info.render() + "\n").encode("ascii"), 0)
            os.fsync(fd)
        except BaseException:
            os.close(fd)  # closing the descriptor releases a flock taken on it
            raise
        self._fd = fd
        self._info = info
        try:
            self._write_holder(stage="acquired", now_iso=info.started_at)
        except OSError as exc:  # the sidecar is diagnostic only; the flock is what excludes writers
            log.warning("lock %s: cannot write holder sidecar: %s", self._path, exc)
        return LockAcquisition(broke_stale=broke_stale, previous=previous)

    def beat(self, stage: str | None = None, *, now_iso: str | None = None) -> None:
        """Record progress in the holder sidecar (last_beat_at, optional stage); no-op when not held."""
        if self._fd is None:
            return
        try:
            self._write_holder(stage=stage, now_iso=now_iso or _utc_now_iso())
        except OSError as exc:
            log.warning("lock %s: heartbeat write failed: %s", self._path, exc)

    def release(self) -> None:
        """Truncate the body and release (idempotent)."""
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        self._info = None
        try:
            with contextlib.suppress(FileNotFoundError):
                _holder_path(self._path).unlink()
            os.ftruncate(fd, 0)
            os.fsync(fd)
        except OSError as exc:
            log.warning("lock %s: truncate on release failed (%s); next cycle will run FULL", self._path, exc)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __enter__(self) -> LockAcquisition:
        """acquire()."""
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """release()."""
        self.release()

    def _write_holder(self, *, stage: str | None, now_iso: str) -> None:
        """Rewrite the holder sidecar atomically (sorted keys)."""
        info = self._info
        if info is None:
            return
        previous = self.read_holder(self._path)
        if stage is None and previous is not None and previous.get("pid") == info.pid:
            prev_stage = previous.get("stage")
            stage = prev_stage if isinstance(prev_stage, str) else None
        body = {
            "boot_time": info.boot_time,
            "host": self._host,
            "label": info.label,
            "last_beat_at": now_iso,
            "pid": info.pid,
            "stage": stage,
            "started_at": info.started_at,
        }
        _atomic_write(_holder_path(self._path), _dump_json(body), 0o600)

    @staticmethod
    def read_holder(path: Path) -> Mapping[str, object] | None:
        """Return the holder sidecar of the lock at ``path`` (None if absent or unreadable)."""
        try:
            data = json.loads(_holder_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def read_body(path: Path) -> str:
        """Return the lock body at ``path`` ('' if absent); never takes the lock."""
        try:
            return path.read_text(encoding="ascii", errors="replace")
        except FileNotFoundError:
            return ""

    @staticmethod
    def is_held(path: Path) -> bool:
        """True when some process holds the lock at ``path`` (a momentary LOCK_SH|LOCK_NB probe).

        The probe holds a shared lock for microseconds; a cycle starting in exactly that window exits 75
        ("skipped: lock held") and runs at its next interval.
        """
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    @staticmethod
    def describe(path: Path) -> str:
        """Describe the holder recorded at ``path`` (body + sidecar), for messages."""
        return _describe_holder(SingleWriterLock.read_body(path), SingleWriterLock.read_holder(path))


def _read_fd(fd: int) -> str:
    """Read a small lock body from ``fd`` without moving its offset."""
    data = os.pread(fd, 4096, 0)
    return data.decode("ascii", errors="replace")


def _stale_reason(previous: LockInfo | None, current_boot: int) -> str:
    """Why a leftover body is stale (for the log line)."""
    if previous is None:
        return "unparseable body"
    if not _same_boot(previous.boot_time, current_boot):
        return f"written in a previous boot (boot_time {previous.boot_time} != {current_boot})"
    if not pid_alive(previous.pid):
        return f"pid {previous.pid} is dead"
    return (
        f"pid {previous.pid} is alive but does not hold the lock (pid reused, or released without truncating)"
    )


def _dump_json(obj: object) -> bytes:
    """Deterministic JSON bytes: sorted keys, 2-space indent, trailing newline."""
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_heartbeat(
    path: Path,
    source_id: str,
    *,
    run_id: int,
    pass_kind: PassKind | None,
    enumeration_complete: bool,
    ok: bool,
    auth_state: str,
    now_iso: str,
) -> None:
    """Update one source's entry in heartbeat.json atomically (tmp + rename, sorted keys): last_attempt_at,
    and last_success_at only when ``ok``; also run_id, pass_kind, enumeration_complete, auth_state.

    Also kept per entry for the outward watcher (design 4.7): ``ok`` (last attempt's outcome),
    ``consecutive_failures`` and ``consecutive_incomplete`` (passes with enumeration_complete false; a skipped
    source, ``pass_kind=None``, leaves the counter unchanged).  Callers hold the single-writer lock, so the
    read-modify-write is not racy; a corrupt file is logged and replaced.
    """
    if not source_id:
        raise ValueError("source_id must be non-empty")
    if _parse_iso(now_iso) is None:
        raise ValueError(f"now_iso is not ISO-8601: {now_iso!r}")
    try:
        current: dict[str, dict[str, object]] = {k: dict(v) for k, v in read_heartbeat(path).items()}
    except ValueError as exc:
        log.warning("heartbeat %s is corrupt (%s); rewriting it", path, exc)
        current = {}
    prev = current.get(source_id, {})
    prev_failures = prev.get("consecutive_failures")
    prev_incomplete = prev.get("consecutive_incomplete")
    failures = 0 if ok else (prev_failures if isinstance(prev_failures, int) else 0) + 1
    incomplete = prev_incomplete if isinstance(prev_incomplete, int) else 0
    if pass_kind is not None:
        incomplete = 0 if enumeration_complete else incomplete + 1
    entry: dict[str, object] = {
        "auth_state": auth_state,
        "consecutive_failures": failures,
        "consecutive_incomplete": incomplete,
        "enumeration_complete": enumeration_complete,
        "last_attempt_at": now_iso,
        "last_success_at": now_iso if ok else prev.get("last_success_at"),
        "ok": ok,
        "pass_kind": pass_kind.value if pass_kind is not None else None,
        "run_id": run_id,
    }
    current[source_id] = entry
    _atomic_write(path, _dump_json(current), 0o600)


def read_heartbeat(path: Path) -> Mapping[str, Mapping[str, object]]:
    """Return {source_id: entry}; {} if the file is missing."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(data).__name__}")
    return {str(k): v for k, v in sorted(data.items()) if isinstance(v, dict)}
