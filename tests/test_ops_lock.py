"""agentsync.ops.lock: flock single-writer lock, stale detection, holder sidecar, per-source heartbeat."""

from __future__ import annotations

import dataclasses
import fcntl
import json
import logging
import os
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agentsync.config import Config
from agentsync.errors import LockHeldError
from agentsync.model import CycleMode, PassKind
from agentsync.ops import launchd
from agentsync.ops import lock as lockmod
from agentsync.ops.lock import (
    EMPTY_BODY_HOLDER,
    STARTED_BY_LAUNCHD,
    STARTED_BY_OTHER,
    LockAcquisition,
    LockInfo,
    SingleWriterLock,
    boot_time,
    pid_alive,
    read_heartbeat,
    write_heartbeat,
)

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _child(code: str) -> subprocess.Popen[str]:
    """Start a Python child with agentsync importable; it prints READY once its setup is done."""
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _wait_ready(proc: subprocess.Popen[str]) -> str:
    assert proc.stdout is not None
    line: str = proc.stdout.readline()
    if "READY" not in line:
        err = proc.stderr.read() if proc.stderr else ""
        raise AssertionError(f"child not ready: {line!r} {err}")
    return line


# ------------------------------------------------------------------------------------------------ LockInfo


def test_lockinfo_render_parse_roundtrip() -> None:
    info = LockInfo(pid=4242, boot_time=1789594110, started_at="2026-09-29T10:00:00Z", label="poll")
    assert info.render() == "4242 1789594110 2026-09-29T10:00:00Z poll"
    assert LockInfo.parse(info.render()) == info
    assert LockInfo.parse(info.render() + "\n") == info
    assert LockInfo.parse("  " + info.render() + "  \n\n") == info


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n",
        "4242 1789594110 2026-09-29T10:00:00Z",  # 3 fields
        "4242 1789594110 2026-09-29T10:00:00Z poll extra",  # 5 fields
        "abc 1789594110 2026-09-29T10:00:00Z poll",
        "0 1789594110 2026-09-29T10:00:00Z poll",
        "-5 1789594110 2026-09-29T10:00:00Z poll",
        "4242 -1 2026-09-29T10:00:00Z poll",
        "4242 1789594110 not-a-time poll",
        "\x00\x00garbage",
    ],
)
def test_lockinfo_parse_rejects_malformed(text: str) -> None:
    assert LockInfo.parse(text) is None


# ------------------------------------------------------------------------------------------------ primitives


def test_boot_time_is_stable_and_in_the_past() -> None:
    a, b = boot_time(), boot_time()
    assert a > 1_000_000_000
    assert abs(a - b) <= 1
    assert a <= int(time.time())


@pytest.mark.skipif(sys.platform != "darwin", reason="kern.boottime via sysctl is macOS")
def test_boot_time_matches_sysctl_command() -> None:
    assert abs(lockmod._boot_time_sysctlbyname() - lockmod._boot_time_sysctl_cmd()) <= 1


def test_boot_time_falls_back_when_first_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> int:
        raise OSError("nope")

    monkeypatch.setattr(lockmod, "_boot_time_sysctlbyname", broken)
    monkeypatch.setattr(lockmod, "_boot_time_proc_stat", broken)
    monkeypatch.setattr(lockmod, "_boot_time_sysctl_cmd", lambda: 1234567890)
    if sys.platform == "darwin":
        assert boot_time() == 1234567890
    monkeypatch.setattr(lockmod, "_boot_time_sysctl_cmd", broken)
    with pytest.raises(OSError, match="cannot read the boot time"):
        boot_time()


def test_pid_alive() -> None:
    assert pid_alive(os.getpid())
    assert pid_alive(1)  # launchd/init: EPERM counts as alive
    assert not pid_alive(0)
    assert not pid_alive(-1)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not pid_alive(proc.pid)


# ------------------------------------------------------------------------------------------------ lock


def test_acquire_fresh_writes_body_and_creates_private_parent(tmp_path: Path) -> None:
    path = tmp_path / "new" / "state" / "agentsync.lock"
    lk = SingleWriterLock(path, "poll")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    acq = lk.acquire()
    try:
        assert acq == LockAcquisition(broke_stale=False, previous=None)
        assert lk.held
        info = LockInfo.parse(path.read_text())
        assert info is not None
        assert info.pid == os.getpid()
        assert info.label == "poll"
        assert abs(info.boot_time - boot_time()) <= 1
        assert info.started_at.endswith("Z")
        assert lk.info == info
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text().endswith("\n")
    finally:
        lk.release()


def test_release_truncates_keeps_file_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "agentsync.lock"
    lk = SingleWriterLock(path, "cli")
    lk.acquire()
    sidecar = path.with_name("agentsync.lock.json")
    assert sidecar.exists()
    lk.release()
    assert path.exists(), "the lock file itself is never unlinked"
    assert path.read_text() == ""
    assert not sidecar.exists()
    assert not lk.held
    lk.release()  # idempotent
    # and it can be re-acquired cleanly
    assert lk.acquire() == LockAcquisition(broke_stale=False, previous=None)
    lk.release()


def test_context_manager(tmp_path: Path) -> None:
    path = tmp_path / "agentsync.lock"
    with SingleWriterLock(path, "reconcile") as acq:
        assert acq.broke_stale is False
        assert "reconcile" in path.read_text()
    assert path.read_text() == ""


def test_context_manager_releases_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "agentsync.lock"
    with pytest.raises(RuntimeError, match="boom"), SingleWriterLock(path, "poll"):
        raise RuntimeError("boom")
    assert path.read_text() == ""
    assert not SingleWriterLock.is_held(path)


def test_second_instance_in_same_process_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "agentsync.lock"
    first = SingleWriterLock(path, "reconcile")
    first.acquire()
    try:
        with pytest.raises(LockHeldError) as ei:
            SingleWriterLock(path, "poll").acquire()
        assert f"pid {os.getpid()} (a sync, its pass: reconcile)" in ei.value.holder
        assert "last beat" in ei.value.holder
        assert str(ei.value).startswith("lock held: ")
        assert ei.value.who == "reconcile"  # N24: the CLI's WARNING names the holder by its label alone
        # the refused attempt must not have disturbed the holder's body
        info = LockInfo.parse(path.read_text())
        assert info is not None and info.label == "reconcile"
    finally:
        first.release()


def test_a_holder_with_no_lock_body_yet_is_named_as_unidentified(tmp_path: Path) -> None:
    """N24: between a holder's flock and its body write (or its truncate and unlock) the body is empty, and
    for the whole of install.sh's reinstall (lockf writes none; B13 round 2 R62) or a status or doctor probe.
    The CLI's WARNING and the stderr line then name the same unrecorded holder as doctor (round 3 R50), the
    same text at every such skip; a body that does not parse keeps its own."""
    path = tmp_path / "agentsync.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(LockHeldError) as ei:
            SingleWriterLock(path, "poll").acquire()
        assert ei.value.who == EMPTY_BODY_HOLDER
        assert str(ei.value) == f"lock held: {ei.value.who}", "the stderr line and the WARNING agree"
        os.pwrite(fd, b"not a lock body\n", 0)
        with pytest.raises(LockHeldError) as ei:
            SingleWriterLock(path, "poll").acquire()
        assert ei.value.who == "an unidentified holder (unparseable lock body)"
    finally:
        os.close(fd)


def test_the_lock_wait_names_a_holder_whose_body_does_not_parse_in_fixed_words(tmp_path: Path) -> None:
    """B13 round 20 X55: a body that does not parse gave the wait line _describe_holder's words, a nested
    bracket and the raw body (``another sync is running ('contoso garbage' (unparseable lock body));
    ...``), unbounded. It gets fixed words with no bracket and no copy of the body; doctor and the skip line
    keep it."""
    from agentsync import cycle  # noqa: PLC0415

    path = tmp_path / "agentsync.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.pwrite(fd, b"contoso garbage\n" * 40, 0)
        with pytest.raises(LockHeldError) as ei:
            SingleWriterLock(path, "poll").acquire()
    finally:
        os.close(fd)
    assert ei.value.wait_words == lockmod.UNPARSEABLE_BODY_WAIT
    assert "(" not in ei.value.wait_words and "contoso" not in ei.value.wait_words
    assert "contoso garbage" in ei.value.holder  # doctor's and the skip line's words keep the body
    for quiet in (False, True):
        line = cycle._wait_beat(ei.value, 5, 600, quiet=quiet)
        assert f"({lockmod.UNPARSEABLE_BODY_WAIT})" in line and "contoso" not in line, line
    assert lockmod._wait_words("", None) == EMPTY_BODY_HOLDER  # the empty case keeps its own line


def test_double_acquire_on_one_instance_raises(tmp_path: Path) -> None:
    lk = SingleWriterLock(tmp_path / "agentsync.lock", "poll")
    lk.acquire()
    try:
        with pytest.raises(RuntimeError, match="already acquired"):
            lk.acquire()
    finally:
        lk.release()


@pytest.mark.parametrize("label", ["", "has space", "tab\there", "x" * 65])
def test_bad_labels_rejected(tmp_path: Path, label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        SingleWriterLock(tmp_path / "agentsync.lock", label)


def test_lock_path_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text("")
    link = tmp_path / "agentsync.lock"
    link.symlink_to(target)
    with pytest.raises(OSError):
        SingleWriterLock(link, "poll").acquire()


def test_other_process_holding_then_killed_is_broken_as_stale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "agentsync.lock"
    proc = _child(
        f"""
        import sys, time
        from pathlib import Path
        from agentsync.ops.lock import SingleWriterLock
        lk = SingleWriterLock(Path({str(path)!r}), "reconcile")
        lk.acquire()
        lk.beat("fetch local-fixture")
        print("READY", flush=True)
        time.sleep(60)
        """
    )
    try:
        _wait_ready(proc)
        assert SingleWriterLock.is_held(path)
        with pytest.raises(LockHeldError) as ei:
            SingleWriterLock(path, "poll").acquire()
        assert f"pid {proc.pid} (a sync, its pass: reconcile)" in ei.value.holder
        assert "stage 'fetch local-fixture'" in ei.value.holder
        holder = SingleWriterLock.read_holder(path)
        assert holder is not None and holder["pid"] == proc.pid and holder["label"] == "reconcile"
        assert isinstance(holder["host"], str) and holder["host"]
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait()
    assert not SingleWriterLock.is_held(path), "the kernel drops a flock when its holder dies"
    caplog.set_level(logging.WARNING, logger="agentsync.ops.lock")
    lk = SingleWriterLock(path, "poll")
    acq = lk.acquire()
    try:
        assert acq.broke_stale is True
        assert acq.previous is not None and acq.previous.pid == proc.pid
        assert acq.previous.label == "reconcile"
        assert f"pid {proc.pid} is dead" in caplog.text
        assert "FULL" in caplog.text
        info = LockInfo.parse(path.read_text())
        assert info is not None and info.pid == os.getpid() and info.label == "poll"
    finally:
        lk.release()


def test_lock_fd_is_not_inherited_by_grandchildren(tmp_path: Path) -> None:
    """A git/pandoc child spawned mid-cycle must not keep the lock alive after the cycle dies (O_CLOEXEC)."""
    path = tmp_path / "agentsync.lock"
    pidfile = tmp_path / "grandchild.pid"
    proc = _child(
        f"""
        import os, subprocess
        from pathlib import Path
        from agentsync.ops.lock import SingleWriterLock
        SingleWriterLock(Path({str(path)!r}), "poll").acquire()
        g = subprocess.Popen(["/bin/sleep", "60"], close_fds=False)
        Path({str(pidfile)!r}).write_text(str(g.pid))
        print("READY", flush=True)
        os._exit(0)  # die without releasing, grandchild keeps running
        """
    )
    _wait_ready(proc)
    proc.wait()
    grandchild = int(pidfile.read_text())
    try:
        assert pid_alive(grandchild)
        lk = SingleWriterLock(path, "reconcile")
        acq = lk.acquire()  # would raise LockHeldError if the grandchild had inherited the descriptor
        assert acq.broke_stale is True
        lk.release()
    finally:
        os.kill(grandchild, signal.SIGKILL)


def test_body_from_previous_boot_is_stale(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "agentsync.lock"
    old = LockInfo(pid=os.getpid(), boot_time=1_000_000, started_at="2026-01-01T00:00:00Z", label="poll")
    path.write_text(old.render() + "\n")
    caplog.set_level(logging.WARNING, logger="agentsync.ops.lock")
    with SingleWriterLock(path, "poll") as acq:
        assert acq.broke_stale is True
        assert acq.previous == old
    assert "previous boot" in caplog.text


def test_body_naming_live_pid_same_boot_is_still_stale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """We hold the flock, so whoever wrote the body is gone: pid reuse or an unclean release."""
    path = tmp_path / "agentsync.lock"
    old = LockInfo(pid=1, boot_time=boot_time(), started_at="2026-01-01T00:00:00Z", label="reconcile")
    path.write_text(old.render() + "\n")
    caplog.set_level(logging.WARNING, logger="agentsync.ops.lock")
    with SingleWriterLock(path, "poll") as acq:
        assert acq.broke_stale is True
        assert acq.previous == old
    assert "alive but does not hold the lock" in caplog.text


def test_malformed_body_is_stale_with_no_previous(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "agentsync.lock"
    path.write_text("this is not a lock body at all\n")
    caplog.set_level(logging.WARNING, logger="agentsync.ops.lock")
    with SingleWriterLock(path, "poll") as acq:
        assert acq == LockAcquisition(broke_stale=True, previous=None)
    assert "unparseable body" in caplog.text


@pytest.mark.parametrize(
    ("started_by", "warned"), [(STARTED_BY_OTHER, False), (STARTED_BY_LAUNCHD, True), (None, True)]
)
def test_a_stale_lock_is_a_warning_only_where_no_other_line_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, started_by: str | None, warned: bool
) -> None:
    """B13 round 21 Y27: a foreground sync that broke a stale lock printed a timestamped WARNING quoting the
    raw body beside its own "note: a stale lock from a dead run was broken" line. A sync the background job
    did not start (STARTED_BY_OTHER, the one whose report prints the note) logs it at debug; the job keeps
    the WARNING for its err.log, and a caller with no started_by (exclude-empty, governance) has no note."""
    path = tmp_path / "agentsync.lock"
    path.write_text("not a lock body\n")
    caplog.set_level(logging.DEBUG, logger="agentsync.ops.lock")
    with SingleWriterLock(path, "poll", started_by=started_by) as acq:
        assert acq.broke_stale is True
    (record,) = [r for r in caplog.records if "breaking stale lock" in r.getMessage()]
    assert record.levelno == (logging.WARNING if warned else logging.DEBUG), record


def test_boot_time_failure_records_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> int:
        raise OSError("no sysctl")

    monkeypatch.setattr(lockmod, "boot_time", broken)
    path = tmp_path / "agentsync.lock"
    with SingleWriterLock(path, "poll"):
        info = LockInfo.parse(path.read_text())
        assert info is not None and info.boot_time == 0


def test_beat_updates_sidecar_and_keeps_stage(tmp_path: Path) -> None:
    path = tmp_path / "agentsync.lock"
    lk = SingleWriterLock(path, "poll")
    lk.beat("ignored: not held")  # no-op
    assert SingleWriterLock.read_holder(path) is None
    lk.acquire()
    try:
        h0 = SingleWriterLock.read_holder(path)
        assert h0 is not None
        assert h0["stage"] == "acquired"
        assert h0["last_beat_at"] == h0["started_at"]
        lk.beat("convert", now_iso="2030-01-01T00:00:00Z")
        h1 = SingleWriterLock.read_holder(path)
        assert h1 is not None and h1["stage"] == "convert" and h1["last_beat_at"] == "2030-01-01T00:00:00Z"
        lk.beat(now_iso="2030-01-01T00:00:05Z")
        h2 = SingleWriterLock.read_holder(path)
        assert h2 is not None and h2["stage"] == "convert" and h2["last_beat_at"] == "2030-01-01T00:00:05Z"
        raw = path.with_name("agentsync.lock.json").read_text()
        assert list(json.loads(raw)) == sorted(json.loads(raw))
        assert stat.S_IMODE(path.with_name("agentsync.lock.json").stat().st_mode) == 0o600
        assert set(h2) == {"boot_time", "host", "label", "last_beat_at", "pid", "stage", "started_at"}
    finally:
        lk.release()


def test_is_held_and_describe(tmp_path: Path) -> None:
    path = tmp_path / "agentsync.lock"
    assert SingleWriterLock.is_held(path) is False  # no file
    assert SingleWriterLock.read_body(path) == ""
    lk = SingleWriterLock(path, "poll")
    lk.acquire()
    try:
        assert SingleWriterLock.is_held(path) is True
        assert f"pid {os.getpid()} (a sync, its pass: poll)" in SingleWriterLock.describe(path)
        # the probe must not steal or disturb the lock
        assert lk.held and SingleWriterLock.is_held(path)
    finally:
        lk.release()
    assert SingleWriterLock.is_held(path) is False
    path.write_text("junk")
    assert "unparseable" in SingleWriterLock.describe(path)


def test_describe_names_an_empty_body_held_by_install_sh(tmp_path: Path) -> None:
    """install.sh's reinstall holds the lock through /usr/bin/lockf (a read-only fd, LOCK_EX) and writes no
    body: the holder is unrecorded, not a corrupt lock (N6)."""
    path = tmp_path / "agentsync.lock"
    path.write_text("")
    path.chmod(0o600)
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        text = SingleWriterLock.describe(path)
        assert "install.sh" in text and "unparseable" not in text, text
        with pytest.raises(LockHeldError, match="unrecorded holder"):
            SingleWriterLock(path, "poll").acquire()
    finally:
        os.close(fd)
    assert path.read_text() == ""


# ------------------------------------------------------------------------------------------------ heartbeat


def _hb(path: Path, sid: str, **kw: object) -> None:
    args: dict[str, object] = {
        "run_id": 1,
        "pass_kind": PassKind.FULL,
        "enumeration_complete": True,
        "ok": True,
        "auth_state": "OK",
        "now_iso": "2026-09-29T10:00:00Z",
    }
    args.update(kw)
    write_heartbeat(path, sid, **args)  # type: ignore[arg-type]


def test_heartbeat_roundtrip_and_file_shape(tmp_path: Path) -> None:
    path = tmp_path / "state" / "heartbeat.json"
    assert read_heartbeat(path) == {}
    _hb(path, "local-fixture")
    data = read_heartbeat(path)
    assert data == {
        "local-fixture": {
            "auth_state": "OK",
            "consecutive_failures": 0,
            "consecutive_incomplete": 0,
            "enumeration_complete": True,
            "last_attempt_at": "2026-09-29T10:00:00Z",
            "last_success_at": "2026-09-29T10:00:00Z",
            "ok": True,
            "pass_kind": "full_enumeration",
            "run_id": 1,
        }
    }
    raw = path.read_text()
    assert raw.endswith("\n")
    assert raw == json.dumps(json.loads(raw), sort_keys=True, indent=2) + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in path.parent.iterdir()) == ["heartbeat.json"], "no temp files left"


def test_heartbeat_failure_keeps_last_success_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    _hb(path, "s1", now_iso="2026-09-29T10:00:00Z")
    _hb(path, "s1", run_id=2, ok=False, now_iso="2026-09-29T10:05:00Z", auth_state="REAUTH_REQUIRED")
    _hb(path, "s1", run_id=3, ok=False, now_iso="2026-09-29T10:10:00Z", auth_state="REAUTH_REQUIRED")
    e = read_heartbeat(path)["s1"]
    assert e["last_success_at"] == "2026-09-29T10:00:00Z"
    assert e["last_attempt_at"] == "2026-09-29T10:10:00Z"
    assert e["ok"] is False
    assert e["consecutive_failures"] == 2
    assert e["auth_state"] == "REAUTH_REQUIRED"
    assert e["run_id"] == 3
    _hb(path, "s1", run_id=4, now_iso="2026-09-29T10:15:00Z")
    e = read_heartbeat(path)["s1"]
    assert e["consecutive_failures"] == 0 and e["last_success_at"] == "2026-09-29T10:15:00Z"


def test_heartbeat_first_failure_has_no_success(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    _hb(path, "s1", ok=False, pass_kind=None)
    e = read_heartbeat(path)["s1"]
    assert e["last_success_at"] is None
    assert e["pass_kind"] is None
    assert e["consecutive_failures"] == 1


def test_heartbeat_incomplete_counter_ignores_skipped_passes(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    _hb(path, "g", pass_kind=PassKind.DELTA, enumeration_complete=False)
    _hb(path, "g", pass_kind=PassKind.FULL, enumeration_complete=False)
    _hb(path, "g", pass_kind=None, enumeration_complete=False, ok=True)  # skipped: network down
    assert read_heartbeat(path)["g"]["consecutive_incomplete"] == 2
    _hb(path, "g", pass_kind=PassKind.DELTA, enumeration_complete=True)
    assert read_heartbeat(path)["g"]["consecutive_incomplete"] == 0


def test_heartbeat_keeps_other_sources_sorted(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    _hb(path, "zeta")
    _hb(path, "alpha", run_id=7)
    _hb(path, "zeta", run_id=8)
    data = read_heartbeat(path)
    assert list(data) == ["alpha", "zeta"]
    assert data["alpha"]["run_id"] == 7 and data["zeta"]["run_id"] == 8
    assert list(json.loads(path.read_text())) == ["alpha", "zeta"]


def test_heartbeat_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for p in (a, b):
        _hb(p, "s1")
        _hb(p, "s2", ok=False)
    assert a.read_bytes() == b.read_bytes()


def test_heartbeat_corrupt_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "heartbeat.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        read_heartbeat(path)
    caplog.set_level(logging.WARNING, logger="agentsync.ops.lock")
    _hb(path, "s1")
    assert "corrupt" in caplog.text
    assert list(read_heartbeat(path)) == ["s1"]
    path.write_text("[1, 2]")
    with pytest.raises(ValueError, match="expected a JSON object"):
        read_heartbeat(path)


def test_heartbeat_ignores_non_object_entries(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"s1": {"ok": True}, "junk": 3}))
    assert read_heartbeat(path) == {"s1": {"ok": True}}


def test_heartbeat_validates_arguments(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    with pytest.raises(ValueError, match="source_id"):
        _hb(path, "")
    with pytest.raises(ValueError, match="ISO-8601"):
        _hb(path, "s1", now_iso="yesterday")
    assert not path.exists()


def test_the_holder_description_names_no_host() -> None:
    """B13 round 6 G36: the lock is a local flock, so its holder is always this Mac. The description the
    lock-wait WARNING, LockHeldError and doctor print leaves the sidecar's host out (the sidecar keeps it)."""
    body = LockInfo(123, 1, "2026-10-05T08:59:00Z", "poll").render()
    holder = {"pid": 123, "host": "contoso-mbp", "last_beat_at": "2026-10-05T08:59:30Z", "stage": "acquired"}
    out = lockmod._describe_holder(body, holder)
    assert out.startswith(
        "pid 123 (a sync, its pass: poll) since 2026-10-05T08:59:00Z, last beat 2026-10-05T08:59:30Z"
    ), out
    assert out.endswith(", stage 'acquired'") and "contoso-mbp" not in out and " on " not in out, out


def test_a_cycle_holder_reads_as_a_sync_and_its_pass() -> None:
    """B13 round 9 K37: a sync typed in a terminal holds the lock as ``poll``, as the background job does, so
    the bare ``pid N (poll)`` on the skip line, the lock-wait WARNING and doctor read as the job. A cycle's
    label is named as a sync's pass, as the CLI's skip WARNING already says it (round 7 H33); another
    holder's label, and the four-field body an older build parses, are as they were."""
    for mode in CycleMode:
        body = LockInfo(123, 1, "2026-10-05T08:59:00Z", mode.value).render()
        assert len(body.split()) == 4 and LockInfo.parse(body) is not None
        out = lockmod._describe_holder(body, None)
        assert out == f"pid 123 (a sync, its pass: {mode.value}) since 2026-10-05T08:59:00Z", out
        assert lockmod._holder_identity(body) == mode.value, "the report's Recent errors merge on the label"
    for label in ("exclude-empty", "governance"):
        body = LockInfo(123, 1, "2026-10-05T08:59:00Z", label).render()
        assert lockmod._describe_holder(body, None) == f"pid 123 ({label}) since 2026-10-05T08:59:00Z"


def test_the_holder_line_names_who_started_a_cycle_only_from_its_own_sidecar() -> None:
    """B13 round 10 L73: ``pid N (a sync, its pass: poll)`` could not tell a sync typed in a terminal from
    the background job. The holder sidecar of the same pid now says whether the job started it, and the line
    names it ("not started by the background job" holds for a terminal, an agent's shell and cron alike);
    with no key, an unknown value or another pid's sidecar it keeps the neutral words. The lock body and
    the skip WARNING's label stay bare."""
    body = LockInfo(123, 1, "2026-10-05T08:59:00Z", "poll").render()
    since = " since 2026-10-05T08:59:00Z"
    for started, who in (
        (STARTED_BY_OTHER, "a sync not started by the background job"),
        (STARTED_BY_LAUNCHD, "the background job"),
    ):
        out = lockmod._describe_holder(body, {"pid": 123, "started_by": started})
        assert out == f"pid 123 ({who}, its pass: poll){since}", out
    for holder in ({"pid": 123}, {"pid": 123, "started_by": "cron"}, {"pid": 456, "started_by": "launchd"}):
        assert lockmod._describe_holder(body, holder) == f"pid 123 (a sync, its pass: poll){since}", holder
    assert lockmod._holder_identity(body) == "poll"
    other = LockInfo(123, 1, "2026-10-05T08:59:00Z", "exclude-empty").render()
    assert lockmod._describe_holder(other, {"pid": 123, "started_by": "other"}).startswith(
        "pid 123 (exclude-empty)"
    )


def test_a_lock_keeps_who_started_it_on_every_beat(tmp_path: Path) -> None:
    """B13 round 10 L73: ``started_by`` is written on acquire and kept by each heartbeat rewrite; a lock made
    without it (governance, exclude-empty) writes no such key, and an unknown value is refused."""
    path = tmp_path / "agentsync.lock"
    lk = SingleWriterLock(path, "poll", started_by=STARTED_BY_OTHER)
    lk.acquire()
    try:
        assert SingleWriterLock.read_holder(path)["started_by"] == "other"  # type: ignore[index]
        lk.beat("convert")
        assert SingleWriterLock.read_holder(path)["started_by"] == "other"  # type: ignore[index]
        assert (
            f"pid {os.getpid()} (a sync not started by the background job, its pass: poll)"
            in SingleWriterLock.describe(path)
        )
    finally:
        lk.release()
    with SingleWriterLock(path, "governance"):
        assert "started_by" not in (SingleWriterLock.read_holder(path) or {})
    with pytest.raises(ValueError, match="started_by"):
        SingleWriterLock(path, "poll", started_by="cron")


def test_only_our_launch_agent_reads_as_the_background_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """B13 round 10 L73: launchd sets XPC_SERVICE_NAME to the job's label; only ours start with the prefix."""
    for value, ours in (
        ("com.agentsync.poll", True),
        ("com.agentsync.reconcile", True),
        ("com.agentsyncx.poll", False),
        ("application.com.apple.Terminal.1234", False),
        ("", False),
    ):
        monkeypatch.setenv("XPC_SERVICE_NAME", value)
        assert launchd.in_our_job("com.agentsync") is ours, value
    monkeypatch.delenv("XPC_SERVICE_NAME")
    assert launchd.in_our_job("com.agentsync") is False


def test_a_cycle_records_who_started_it_from_the_environment_not_its_mode(
    sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 10 L73: a terminal ``reconcile`` passes a mode and no wait_for_lock, as the job's ``sync
    --mode`` does, so the arguments cannot say who started it: the environment does."""
    from agentsync import cycle  # noqa: PLC0415

    seen: list[str | None] = []

    class Recording(SingleWriterLock):
        def __init__(self, path: Path, label: str, *, started_by: str | None = None) -> None:
            seen.append(started_by)
            super().__init__(path, label, started_by=started_by)

    monkeypatch.setattr(cycle, "SingleWriterLock", Recording)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    cycle.run_cycle(sample_config, mode=CycleMode.RECONCILE)
    monkeypatch.setenv("XPC_SERVICE_NAME", f"{sample_config.launchd_label_prefix}.poll")
    cycle.run_cycle(sample_config, mode=CycleMode.POLL)
    # a job installed under the default prefix before the config changed it is still ours (agents_installed)
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.agentsync.poll")
    cycle.run_cycle(
        dataclasses.replace(sample_config, launchd_label_prefix="org.contoso"), mode=CycleMode.POLL
    )
    assert seen == [STARTED_BY_OTHER, STARTED_BY_LAUNCHD, STARTED_BY_LAUNCHD]


def test_a_socket_or_folder_at_a_state_file_reads_as_not_a_regular_file(tmp_path: Path) -> None:
    """B13 round 25 D46 (lane B's reviewer): a socket's open fails with EOPNOTSUPP on macOS (ENXIO on Linux)
    and a folder's with EISDIR; both read as ValueError, which a heartbeat's readers take as a corrupt file,
    never a raw OSError."""
    import socket  # noqa: PLC0415

    folder = tmp_path / "heartbeat.json"
    folder.mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        lockmod.read_heartbeat(folder)
    here = Path.cwd()
    os.chdir(tmp_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind("s.json")  # relative: a tmp path is longer than AF_UNIX takes
    finally:
        os.chdir(here)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            lockmod.read_regular_bytes(tmp_path / "s.json")
    finally:
        sock.close()
    assert lockmod.read_regular_bytes(tmp_path / "missing.json") is None
