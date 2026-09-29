"""agentsync.ops.lock: flock single-writer lock, stale detection, holder sidecar, per-source heartbeat."""

from __future__ import annotations

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

from agentsync.errors import LockHeldError
from agentsync.model import PassKind
from agentsync.ops import lock as lockmod
from agentsync.ops.lock import (
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
        assert f"pid {os.getpid()} (reconcile)" in ei.value.holder
        assert "last beat" in ei.value.holder
        assert str(ei.value).startswith("lock held: ")
        # the refused attempt must not have disturbed the holder's body
        info = LockInfo.parse(path.read_text())
        assert info is not None and info.label == "reconcile"
    finally:
        first.release()


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
        assert f"pid {proc.pid} (reconcile)" in ei.value.holder
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
        assert f"pid {os.getpid()} (poll)" in SingleWriterLock.describe(path)
        # the probe must not steal or disturb the lock
        assert lk.held and SingleWriterLock.is_held(path)
    finally:
        lk.release()
    assert SingleWriterLock.is_held(path) is False
    path.write_text("junk")
    assert "unparseable" in SingleWriterLock.describe(path)


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
