"""Single-writer ``flock`` lock and the outward liveness heartbeat (owner: ops).

The lock (design 4.7, CONTRACTS §14.2) is ``flock(LOCK_EX|LOCK_NB)`` on ``<state_dir>/agentsync.lock``; the
kernel drops it when the holder dies, so a *held* lock always names a live process (install.sh's reinstall
holds it through /usr/bin/lockf and writes no body: then the body is empty, or a crashed holder's).  The
file body ``<pid> <boot_time> <iso8601> <label>`` exists for the other case: a body still present when the
lock is free means the previous holder died mid-cycle (a clean release truncates it first), so its scope is
unknown and the next pass must be a FULL enumeration.

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
import enum
import errno
import fcntl
import json
import logging
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from agentsync.errors import LockHeldError, LockNotAFileError
from agentsync.model import CycleMode, PassKind

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


class Stage(enum.StrEnum):
    """The fixed words a holder records as its stage (:meth:`SingleWriterLock.beat`).  A stage that names a
    source (``source:<id>``, ``work:<id>``) is built from its id and is not one of these, so the setup report,
    which lets only these words through its scrub of quoted values, never prints a source's name (G36)."""

    ACQUIRED = "acquired"
    SECRETS = "secrets"
    CURATE = "curate"
    SURFACE = "surface"
    LAND_GATE = "land-gate"
    COMMIT = "commit"
    RETENTION = "retention"
    RECORDINGS = "recordings"


EMPTY_BODY_HOLDER = (
    "an unrecorded holder (install.sh while it reinstalls agentsync, a status or doctor check, or a cycle "
    "starting)"
)
"""A held lock with an empty body: install.sh's reinstall (lockf writes no body, for up to its wait plus the
uv run), an ``is_held`` probe from status or doctor, or a cycle between its flock and its body write or
releasing. The same fixed words (no pid, no time, so the report's Recent errors merges the WARNING) name it in
the WARNING and in doctor (B13 round 2 R62; round 3 R50 named the probe and the cycle); the stderr skip line
names it in the wait line's words, ``cycle.UNRECORDED_LOCK_HOLDER``, since B13 round 19 W14."""


def _holder_identity(body: str) -> str:
    """``LockHeldError.who``, named by the CLI's ``skipped: lock held by ...`` WARNING: the holder's label
    alone. Its pid, start, beat and stage stay on the stderr line; any of them would make each holder
    run (or each skip) a new line that the report's Recent errors cannot merge."""
    info = LockInfo.parse(body)
    if info is None:
        if not body.strip():  # install.sh's reinstall, a status or doctor probe, or a cycle starting
            return EMPTY_BODY_HOLDER
        return "an unidentified holder (unparseable lock body)"
    return info.label


# who started a cycle that holds the lock (the holder sidecar's ``started_by``, B13 round 10 L73): our
# LaunchAgent, or anything else (a terminal, an agent's shell, cron), which the environment cannot tell apart
STARTED_BY_OTHER = "other"
STARTED_BY_LAUNCHD = "launchd"
_STARTED_BY = {
    STARTED_BY_OTHER: "a sync not started by the background job",
    STARTED_BY_LAUNCHD: "the background job",
}


def _describe_holder(body: str, holder: Mapping[str, object] | None) -> str:
    """Human description of a lock holder for LockHeldError, the cycle's lock-wait WARNING and doctor.  The
    sidecar's host is left out: the lock is a local flock, so the holder is always this Mac, and its name is
    shown in the terminal and in chat for nothing (B13 round 6 G36; the sidecar keeps it).  A cycle's label is
    its pass, which a sync typed in a terminal holds as well as the background job, so it reads ``pid N (a
    sync, its pass: poll)``, as the CLI's skip WARNING already does (round 7 H33); the bare ``(poll)`` read as
    the job (B13 round 9 K37).  When the sidecar of the same pid says who started it (``started_by``, read
    from the environment, never the arguments), the line names that instead: ``the background job, its pass:
    poll`` or ``a sync not started by the background job, its pass: poll`` (B13 round 10 L73).  The body
    keeps its four fields, which an older build parses."""
    info = LockInfo.parse(body)
    if info is None:
        if not body.strip():  # held with no body: install.sh's reinstall (lockf), a probe, a starting cycle
            return EMPTY_BODY_HOLDER
        return f"{body.strip()!r} (unparseable lock body)"
    label = info.label
    if info.label in {m.value for m in CycleMode}:
        same = holder is not None and holder.get("pid") == info.pid
        started = holder.get("started_by") if holder is not None and same else None
        who = _STARTED_BY.get(started, "a sync") if isinstance(started, str) else "a sync"
        label = f"{who}, its pass: {info.label}"
    out = f"pid {info.pid} ({label}) since {info.started_at}"
    if holder is not None and holder.get("pid") == info.pid:
        beat = holder.get("last_beat_at")
        stage = holder.get("stage")
        if isinstance(beat, str):
            out += f", last beat {beat}"
            parsed = _parse_iso(beat)
            if parsed is not None:
                age = int((datetime.now(UTC) - parsed).total_seconds())
                out += f" ({age}s ago)"
        if isinstance(stage, str) and stage:
            out += f", stage {stage!r}"
    return out


UNPARSEABLE_BODY_WAIT = "an unidentified holder, its lock body unreadable"
"""The foreground lock wait's words for a holder whose lock body does not parse (B13 round 20 X55)."""


def _wait_words(body: str, holder: Mapping[str, object] | None) -> str:
    """The holder as the foreground lock wait's line names it, inside its one bracket: ``a sync not started by
    the background job, pass poll, pid N, since <time>, active Ns ago``, with no inner bracket and no stage
    (B13 round 19 W29: :func:`_describe_holder`'s ``pid N (a sync ..., its pass: poll) since ..., last beat
    ... (0s ago), stage 'acquired'`` nested its brackets inside the line's). ``active`` needs the same pid's
    sidecar; ``since`` is left out when it is the beat ``active`` already gives. An empty body keeps
    :func:`_describe_holder`'s words (the cycle's wait line words that case itself); one that does not parse
    gives :data:`UNPARSEABLE_BODY_WAIT`, with no inner bracket and no copy of the body, which came out raw and
    unbounded (B13 round 20 X55). Doctor, the skip line and the report keep that function's pid, beat and
    stage, and the raw body."""
    info = LockInfo.parse(body)
    if info is None:
        return _describe_holder(body, holder) if not body.strip() else UNPARSEABLE_BODY_WAIT
    parts = [info.label]
    if info.label in {m.value for m in CycleMode}:
        same = holder is not None and holder.get("pid") == info.pid
        started = holder.get("started_by") if holder is not None and same else None
        who = _STARTED_BY.get(started, "a sync") if isinstance(started, str) else "a sync"
        parts = [who, f"pass {info.label}"]
    parts.append(f"pid {info.pid}")
    beat = holder.get("last_beat_at") if holder is not None and holder.get("pid") == info.pid else None
    parsed = _parse_iso(beat) if isinstance(beat, str) else None
    if beat != info.started_at or parsed is None:
        parts.append(f"since {info.started_at}")
    if parsed is not None:
        parts.append(f"active {int((datetime.now(UTC) - parsed).total_seconds())}s ago")
    return ", ".join(parts)


class SingleWriterLock:
    """``flock(LOCK_EX|LOCK_NB)`` on StatePaths.lock.  The body is truncated on clean release."""

    def __init__(self, path: Path, label: str, *, started_by: str | None = None) -> None:
        """Bind to the lock path (parent dir created 0700). ``started_by`` (:data:`STARTED_BY_OTHER` or
        :data:`STARTED_BY_LAUNCHD`, a cycle's) goes to the holder sidecar only; None leaves it out."""
        if not _LABEL_RE.match(label):
            raise ValueError(f"lock label must be 1-64 non-whitespace characters, got {label!r}")
        if started_by is not None and started_by not in _STARTED_BY:
            raise ValueError(f"started_by must be one of {sorted(_STARTED_BY)}, got {started_by!r}")
        self._path = path
        self._label = label
        self._started_by = started_by
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
        try:
            fd = os.open(self._path, flags, 0o600)
        except OSError as exc:  # a folder (EISDIR), any link (ELOOP under O_NOFOLLOW), a socket (EOPNOTSUPP)
            if exc.errno in (errno.EISDIR, errno.ELOOP, errno.EOPNOTSUPP, errno.ENXIO):
                raise LockNotAFileError(exc.errno, exc.strerror, str(self._path)) from None
            raise
        if not stat.S_ISREG(
            os.fstat(fd).st_mode
        ):  # a fifo opens O_RDWR, and its flock fails (B13 round 26 E38)
            os.close(fd)
            raise LockNotAFileError(errno.EINVAL, "not a regular file", str(self._path))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                body = _read_fd(fd)
                sidecar = self.read_holder(self._path)
                raise LockHeldError(
                    _describe_holder(body, sidecar),
                    who=_holder_identity(body),
                    wait_words=_wait_words(body, sidecar),
                ) from None
            previous_body = _read_fd(fd)
            previous = LockInfo.parse(previous_body)
            current_boot = _current_boot_or_zero()
            broke_stale = bool(previous_body.strip())
            if broke_stale:
                # a sync the background job did not start prints its own "note: a stale lock from a dead run
                # was broken" line, so the WARNING repeated it on the same terminal: debug there (-vv); the
                # job keeps it for its err.log, and a caller with no started_by (exclude-empty, governance,
                # purge) has no other line (B13 round 21 Y27, as §16.143 W13 and §16.149 X16)
                level = logging.DEBUG if self._started_by == STARTED_BY_OTHER else logging.WARNING
                log.log(
                    level,
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
            self._write_holder(stage=Stage.ACQUIRED, now_iso=info.started_at)
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
        body: dict[str, object] = {
            "boot_time": info.boot_time,
            "host": self._host,
            "label": info.label,
            "last_beat_at": now_iso,
            "pid": info.pid,
            "stage": stage,
            "started_at": info.started_at,
        }
        if self._started_by is not None:  # every rewrite keeps it (B13 round 10 L73)
            body["started_by"] = self._started_by
        _atomic_write(_holder_path(self._path), _dump_json(body), 0o600)

    @staticmethod
    def read_holder(path: Path) -> Mapping[str, object] | None:
        """Return the holder sidecar of the lock at ``path`` (None if absent or unreadable)."""
        try:
            data = json.loads(read_regular_text(_holder_path(path)) or "null")  # never waits on a fifo (D46)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def read_body(path: Path) -> str:
        """Return the lock body at ``path`` ('' if absent); never takes the lock."""
        try:
            data = read_regular_bytes(path)
        except ValueError:  # a fifo or a socket there, which a plain read waited on (D46)
            return ""
        return (data or b"").decode("ascii", errors="replace")

    @staticmethod
    def is_held(path: Path) -> bool:
        """True when some process holds the lock at ``path`` (a momentary LOCK_SH|LOCK_NB probe).

        The probe holds a shared lock for microseconds; a cycle starting in exactly that window exits 75
        ("skipped: lock held") and runs at its next interval.
        """
        try:
            # O_NONBLOCK: a fifo at the lock that nothing writes hung status (B13 round 25 D46)
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False  # no flock on a fifo or a socket (EOPNOTSUPP): no process holds it
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
    counts_as_listed: bool | None = None,
) -> None:
    """Update one source's entry in heartbeat.json atomically (tmp + rename, sorted keys): last_attempt_at,
    and last_success_at only when ``ok``; also run_id, pass_kind, enumeration_complete (the pass's own),
    auth_state.

    Also kept per entry for the outward watcher (design 4.7): ``ok`` (last attempt's outcome),
    ``consecutive_failures`` and ``consecutive_incomplete`` (passes that were not listed; a skipped source,
    ``pass_kind=None``, leaves the counter unchanged). ``counts_as_listed`` (default:
    ``enumeration_complete``) decides that count alone: a Graph delta pass counts as listed though its own
    flag is false (B13 round 5 F69), and the entry keeps the flag (round 6 G53). Callers hold the
    single-writer lock, so the read-modify-write is not racy; a corrupt file is logged and replaced.
    """
    if not source_id:
        raise ValueError("source_id must be non-empty")
    if _parse_iso(now_iso) is None:
        raise ValueError(f"now_iso is not ISO-8601: {now_iso!r}")
    try:
        current: dict[str, object] = {k: dict(v) for k, v in read_heartbeat(path).items()}
        record = _hold_record(path)
    except ValueError as exc:
        log.warning("heartbeat %s is corrupt (%s); rewriting it", path, exc)
        current, record = {}, None
    if record is not None and not os.path.lexists(path.parent / HOLD_RECORD_NAME):
        # round 28's record of the holds, kept only until hold-record.json replaces it (B13 round 29 C199);
        # a source named "holds" takes its own entry below (C200, C171)
        current[HOLD_RECORD_KEY] = record
    prev_entry = current.get(source_id, {})
    prev: dict[str, object] = prev_entry if isinstance(prev_entry, dict) else {}
    prev_failures = prev.get("consecutive_failures")
    prev_incomplete = prev.get("consecutive_incomplete")
    failures = 0 if ok else (prev_failures if isinstance(prev_failures, int) else 0) + 1
    incomplete = prev_incomplete if isinstance(prev_incomplete, int) else 0
    listed = enumeration_complete if counts_as_listed is None else counts_as_listed
    if pass_kind is not None:
        incomplete = 0 if listed else incomplete + 1
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


HOLD_RECORD_KEY = "holds"
"""Round 28's key of the state hold scopes in heartbeat.json, a list beside the per-source objects (which
every reader takes alone). Read only while no :data:`HOLD_RECORD_NAME` is there, and only as a list: a source
may be named ``holds``, and its entry is an object (B13 round 29 C200, C171); a pass keeps it until then."""

HOLD_RECORD_NAME = "hold-record.json"
"""The state hold scopes' own record beside heartbeat.json (``governance.record_holds``), outside governance/
and out of the new start's move of heartbeat.json, so neither move lifts a hold (B13 round 29 C199)."""


def _hold_record(path: Path) -> list[str] | None:
    """heartbeat.json's round 28 hold record (:data:`HOLD_RECORD_KEY`, a list), or None when there is none."""
    text = read_regular_text(path)
    data = json.loads(text) if text else None
    value = data.get(HOLD_RECORD_KEY) if isinstance(data, dict) else None
    if not isinstance(value, list):
        return None
    return [s for s in value if isinstance(s, str)]


def read_regular_text(path: Path) -> str | None:
    """The UTF-8 text of :func:`read_regular_bytes`."""
    data = read_regular_bytes(path)
    return None if data is None else data.decode("utf-8")


def _is_link(path: Path) -> bool:
    """Whether ``path`` itself is a symlink, by its own lstat; False when the lstat fails."""
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError:
        return False


def read_regular_bytes(path: Path) -> bytes | None:
    """The bytes of the regular file at ``path``, or None when nothing is there; ValueError for anything else
    (a fifo, a socket, a folder), judged on the open file (``fstat``, so through a link and with no race).
    Opened O_NONBLOCK: a plain open of a fifo nothing writes waits forever, and a fifo planted at
    heartbeat.json hung status and, inside the lock, every later sync (B13 round 25 D46). ValueError, not
    OSError: a heartbeat's readers already take it as a corrupt file, and the next pass writes a regular one
    over it. A symlink loop, or a link through a file (ELOOP, ENOTDIR), at ``path`` itself is not a regular
    file too, so a pass's rename replaces the link (B13 round 27 P134: every sync ended 0 and left the link,
    and status FAILed it as a fault to report); the same errnos from a folder above ``path`` (a file or a
    loop where the state folder belongs) stay the OSError, that folder's own FAIL."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:  # a folder (EISDIR), a socket (EOPNOTSUPP on macOS, ENXIO on Linux)
        if exc.errno in (errno.EISDIR, errno.EOPNOTSUPP, errno.ENXIO):
            raise ValueError(f"{path}: not a regular file") from None
        if exc.errno in (errno.ELOOP, errno.ENOTDIR) and _is_link(path):
            raise ValueError(f"{path}: not a regular file") from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path}: not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def read_heartbeat(path: Path) -> Mapping[str, Mapping[str, object]]:
    """Return {source_id: entry}; {} if the file is missing; ValueError for one that is not a regular file
    (:func:`read_regular_text`) or not a JSON object."""
    raw = read_regular_bytes(path)
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:  # never a bare codec message that names no file (B13 round 29 C139)
        raise ValueError(f"{path}: not valid JSON (not UTF-8 text)") from None
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(data).__name__}")
    return {str(k): v for k, v in sorted(data.items()) if isinstance(v, dict)}
