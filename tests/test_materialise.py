"""materialise: policy syscalls, fail-closed, THREAD-scope opt-in, budgeted copy with retry and stability."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import subprocess
import sys
import threading
import types
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from agentsync import materialise as m
from agentsync.errors import (
    BudgetExhaustedError,
    DatalessRefusedError,
    MaterialiseError,
    ProviderTimeoutError,
)
from agentsync.model import ByteBudget

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only syscalls")


def _fake_stat(flags: int) -> os.stat_result:
    return cast(os.stat_result, types.SimpleNamespace(st_flags=flags))


@pytest.fixture
def thread_policy() -> Iterator[int]:
    """Snapshot and restore this thread's materialisation policy around a test."""
    before = m.get_materialize_policy(m.IOPOL_SCOPE_THREAD)
    yield before
    m.set_materialize_policy(before, m.IOPOL_SCOPE_THREAD)


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------------------------------------
# constants and is_dataless
# ---------------------------------------------------------------------------------------------------------


def test_constants_match_macos_sdk() -> None:
    assert m.IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES == 3
    assert (m.IOPOL_SCOPE_PROCESS, m.IOPOL_SCOPE_THREAD) == (0, 1)
    assert (
        m.IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT,
        m.IOPOL_MATERIALIZE_DATALESS_FILES_OFF,
        m.IOPOL_MATERIALIZE_DATALESS_FILES_ON,
    ) == (0, 1, 2)
    assert m.SF_DATALESS == 0x40000000
    assert m.EDEADLK == errno.EDEADLK == 11
    assert m.ETIMEDOUT == errno.ETIMEDOUT == 60


def test_is_dataless_reads_the_flag_only() -> None:
    assert m.is_dataless(_fake_stat(0x40000060))  # measured flags of an evicted OneDrive file (C14 §2)
    assert not m.is_dataless(_fake_stat(0x40))
    assert not m.is_dataless(_fake_stat(0))


def test_is_dataless_false_for_a_local_file(tmp_path: Path) -> None:
    f = _write(tmp_path / "a.txt", b"x")
    assert not m.is_dataless(os.lstat(f))


# ---------------------------------------------------------------------------------------------------------
# policy syscalls
# ---------------------------------------------------------------------------------------------------------


def test_thread_policy_round_trip(thread_policy: int) -> None:
    for policy in (
        m.IOPOL_MATERIALIZE_DATALESS_FILES_OFF,
        m.IOPOL_MATERIALIZE_DATALESS_FILES_ON,
        m.IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT,
    ):
        m.set_materialize_policy(policy, m.IOPOL_SCOPE_THREAD)
        assert m.get_materialize_policy(m.IOPOL_SCOPE_THREAD) == policy


def test_process_policy_is_readable() -> None:
    assert m.get_materialize_policy() in (0, 1, 2)


def test_invalid_scope_or_policy_raise_einval() -> None:
    with pytest.raises(OSError) as e1:
        m.get_materialize_policy(7)
    assert e1.value.errno == errno.EINVAL
    with pytest.raises(OSError) as e2:
        m.set_materialize_policy(9, m.IOPOL_SCOPE_THREAD)
    assert e2.value.errno == errno.EINVAL


def test_fail_closed_sets_process_off_and_children_inherit(tmp_path: Path) -> None:
    # Run in a child so the pytest process keeps its own policy.
    code = (
        "import subprocess, sys\n"
        "from agentsync import materialise as m\n"
        "m.fail_closed()\n"
        "print(m.get_materialize_policy(m.IOPOL_SCOPE_PROCESS))\n"
        "child = subprocess.run([sys.executable, '-c', 'from agentsync import materialise as m; "
        "print(m.get_materialize_policy())'], capture_output=True, text=True, check=True)\n"
        "print(child.stdout.strip())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["1", "1"]


def test_off_platform_policy_is_enosys_and_allowed_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    m._iopolicy_api.cache_clear()
    monkeypatch.setattr(sys, "platform", "linux")
    try:
        with pytest.raises(OSError) as exc:
            m.set_materialize_policy(m.IOPOL_MATERIALIZE_DATALESS_FILES_ON, m.IOPOL_SCOPE_THREAD)
        assert exc.value.errno == errno.ENOSYS
        with m.materialize_allowed():
            pass
    finally:
        monkeypatch.undo()
        m._iopolicy_api.cache_clear()
    assert m.get_materialize_policy() in (0, 1, 2)


# ---------------------------------------------------------------------------------------------------------
# materialize_allowed
# ---------------------------------------------------------------------------------------------------------


def test_materialize_allowed_sets_thread_on_and_restores(thread_policy: int) -> None:
    m.set_materialize_policy(m.IOPOL_MATERIALIZE_DATALESS_FILES_OFF, m.IOPOL_SCOPE_THREAD)
    process_before = m.get_materialize_policy(m.IOPOL_SCOPE_PROCESS)
    with m.materialize_allowed():
        assert m.get_materialize_policy(m.IOPOL_SCOPE_THREAD) == m.IOPOL_MATERIALIZE_DATALESS_FILES_ON
        assert m.get_materialize_policy(m.IOPOL_SCOPE_PROCESS) == process_before
    assert m.get_materialize_policy(m.IOPOL_SCOPE_THREAD) == m.IOPOL_MATERIALIZE_DATALESS_FILES_OFF


def test_materialize_allowed_restores_on_exception(thread_policy: int) -> None:
    m.set_materialize_policy(m.IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT, m.IOPOL_SCOPE_THREAD)
    with pytest.raises(RuntimeError), m.materialize_allowed():
        raise RuntimeError("boom")
    assert m.get_materialize_policy(m.IOPOL_SCOPE_THREAD) == m.IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT


def test_materialize_allowed_is_thread_local(thread_policy: int) -> None:
    seen: list[int] = []
    inside = threading.Event()
    release = threading.Event()

    def other() -> None:
        inside.wait(5)
        seen.append(m.get_materialize_policy(m.IOPOL_SCOPE_THREAD))
        release.set()

    t = threading.Thread(target=other)
    t.start()
    with m.materialize_allowed():
        inside.set()
        release.wait(5)
    t.join(5)
    assert seen and seen[0] != m.IOPOL_MATERIALIZE_DATALESS_FILES_ON


def test_materialize_allowed_falls_back_to_process_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, int | None]] = []
    process = {"policy": m.IOPOL_MATERIALIZE_DATALESS_FILES_OFF}

    def fake_get(scope: int = m.IOPOL_SCOPE_PROCESS) -> int:
        calls.append(("get", scope, None))
        if scope == m.IOPOL_SCOPE_THREAD:
            raise OSError(errno.EINVAL, "thread scope unsupported")
        return process["policy"]

    def fake_set(policy: int, scope: int = m.IOPOL_SCOPE_PROCESS) -> None:
        calls.append(("set", scope, policy))
        process["policy"] = policy

    monkeypatch.setattr(m, "get_materialize_policy", fake_get)
    monkeypatch.setattr(m, "set_materialize_policy", fake_set)
    with m.materialize_allowed():
        assert process["policy"] == m.IOPOL_MATERIALIZE_DATALESS_FILES_ON
    assert process["policy"] == m.IOPOL_MATERIALIZE_DATALESS_FILES_OFF
    assert ("set", m.IOPOL_SCOPE_PROCESS, m.IOPOL_MATERIALIZE_DATALESS_FILES_ON) in calls


# ---------------------------------------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------------------------------------


def test_sha256_file_matches_hashlib_across_chunk_sizes(tmp_path: Path) -> None:
    data = os.urandom(3 * 1024 + 7)
    f = _write(tmp_path / "b.bin", data)
    want = hashlib.sha256(data).hexdigest()
    assert m.sha256_file(f) == want
    assert m.sha256_file(f, chunk=1) == want
    assert m.sha256_file(f, chunk=1000) == want
    assert m.sha256_file(_write(tmp_path / "empty", b"")) == hashlib.sha256(b"").hexdigest()


def test_sha256_file_refuses_a_dataless_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = _write(tmp_path / "p.docx", b"x")
    monkeypatch.setattr(m, "is_dataless", lambda st: True)
    with pytest.raises(DatalessRefusedError) as exc:
        m.sha256_file(f)
    assert exc.value.errno == m.EDEADLK


# ---------------------------------------------------------------------------------------------------------
# materialise: happy paths
# ---------------------------------------------------------------------------------------------------------


def test_materialise_copies_hashes_and_charges_budget(tmp_path: Path, thread_policy: int) -> None:
    data = os.urandom(3 * (1 << 20) + 123)  # spans several 1 MiB buffers
    src = _write(tmp_path / "src" / "Report.docx", data)
    before = os.lstat(src)
    dest = tmp_path / "staging" / "k" / "Report.docx"
    budget = ByteBudget(10 * (1 << 20), 5)
    res = m.materialise(src, dest, budget)
    assert res == m.MaterialiseResult(
        src=src,
        dest=dest,
        size=len(data),
        content_sha256=hashlib.sha256(data).hexdigest(),
        was_dataless=False,
        attempts=1,
    )
    assert dest.read_bytes() == data
    assert stat.S_IMODE(os.lstat(dest).st_mode) == 0o600  # corporate bytes in staging are owner-only
    assert (budget.used, budget.files_used) == (0, 1), "a local file downloads nothing: 0 bytes charged"
    after = os.lstat(src)
    assert (after.st_mtime_ns, after.st_size, after.st_ino) == (
        before.st_mtime_ns,
        before.st_size,
        before.st_ino,
    )
    assert sorted(p.name for p in dest.parent.iterdir()) == ["Report.docx"]  # no .part left behind
    assert m.get_materialize_policy(m.IOPOL_SCOPE_THREAD) == thread_policy  # policy restored


def test_materialise_empty_file(tmp_path: Path) -> None:
    src = _write(tmp_path / "empty.txt", b"")
    res = m.materialise(src, tmp_path / "out" / "empty.txt", ByteBudget(0, 1))
    assert res.size == 0
    assert res.content_sha256 == hashlib.sha256(b"").hexdigest()


def test_materialise_overwrites_an_existing_dest(tmp_path: Path) -> None:
    src = _write(tmp_path / "a.txt", b"new")
    dest = _write(tmp_path / "out" / "a.txt", b"old stale bytes")
    m.materialise(src, dest, ByteBudget(100, 2))
    assert dest.read_bytes() == b"new"


def test_materialise_reads_under_thread_policy_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "a.txt", b"abc")
    seen: list[int] = []
    real_open = os.open

    def spy_open(path: str | os.PathLike[str], flags: int, *args: int) -> int:
        if Path(path) == src:
            seen.append(m.get_materialize_policy(m.IOPOL_SCOPE_THREAD))
            assert flags & os.O_NOFOLLOW
            assert not flags & (os.O_WRONLY | os.O_RDWR)  # src is never written
        return real_open(path, flags, *args)

    monkeypatch.setattr(os, "open", spy_open)
    m.materialise(src, tmp_path / "out" / "a.txt", ByteBudget(100, 1))
    assert seen == [m.IOPOL_MATERIALIZE_DATALESS_FILES_ON]


# ---------------------------------------------------------------------------------------------------------
# materialise: refusals and errors
# ---------------------------------------------------------------------------------------------------------


def test_budget_is_charged_before_any_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "big.bin", b"x" * 100)
    monkeypatch.setattr(m, "is_dataless", lambda st: True)  # an online-only file: reading it downloads it
    monkeypatch.setattr(m, "_copy_once", lambda *a: pytest.fail("read despite exhausted budget"))
    budget = ByteBudget(99, 10)
    dest = tmp_path / "out" / "big.bin"
    with pytest.raises(BudgetExhaustedError):
        m.materialise(src, dest, budget)
    assert (budget.used, budget.files_used) == (0, 0)
    assert not dest.exists()


def test_the_byte_budget_is_charged_only_for_dataless_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3: the byte budget bounds downloads. An already-local file never consumes it (a budget of 0 still
    copies it); a file that is dataless (online-only) when materialise lstats it is charged its size."""
    local = _write(tmp_path / "local.docx", b"L" * 500)
    online = _write(tmp_path / "online.docx", b"O" * 300)
    online_ino = os.lstat(online).st_ino
    real = m.is_dataless
    monkeypatch.setattr(m, "is_dataless", lambda st: st.st_ino == online_ino or real(st))
    assert m.download_cost(os.lstat(local)) == 0 and m.download_cost(os.lstat(online)) == 300
    zero = ByteBudget(0, 10)
    res = m.materialise(local, tmp_path / "o" / "local.docx", zero)
    assert (res.size, res.was_dataless) == (500, False)
    assert (zero.used, zero.files_used) == (0, 1), "a local file never consumes the byte budget"
    monkeypatch.setattr(m, "_copy_once", lambda *a: pytest.fail("downloaded despite a budget of 0"))
    with pytest.raises(BudgetExhaustedError):
        m.materialise(online, tmp_path / "o" / "online.docx", zero)
    assert (zero.used, zero.files_used) == (0, 1) and not (tmp_path / "o" / "online.docx").exists()
    monkeypatch.undo()
    monkeypatch.setattr(m, "is_dataless", lambda st: st.st_ino == online_ino or real(st))
    enough = ByteBudget(300, 10)
    res = m.materialise(online, tmp_path / "o" / "online.docx", enough)
    assert res.was_dataless and (enough.used, enough.files_used) == (300, 1)
    m.materialise(local, tmp_path / "o" / "again.docx", enough)
    assert (enough.used, enough.files_used) == (300, 2), "the budget is spent, and a local file still fits"


def test_file_budget_is_enforced(tmp_path: Path) -> None:
    budget = ByteBudget(1000, 1)
    m.materialise(_write(tmp_path / "a", b"1"), tmp_path / "o" / "a", budget)
    with pytest.raises(BudgetExhaustedError):
        m.materialise(_write(tmp_path / "b", b"2"), tmp_path / "o" / "b", budget)


def test_missing_source_is_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        m.materialise(tmp_path / "gone.txt", tmp_path / "o" / "gone.txt", ByteBudget(10, 1))


def test_symlink_and_directory_sources_are_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "t.txt", b"t")
    link = tmp_path / "l.txt"
    link.symlink_to(target)
    budget = ByteBudget(100, 10)
    with pytest.raises(MaterialiseError, match="symlink"):
        m.materialise(link, tmp_path / "o" / "l.txt", budget)
    (tmp_path / "d").mkdir()
    with pytest.raises(MaterialiseError, match="regular"):
        m.materialise(tmp_path / "d", tmp_path / "o" / "d", budget)
    assert budget.files_used == 0


def _failing_copy(errs: list[int]) -> tuple[list[int], object]:
    """A _copy_once stand-in that raises the queued errnos, then defers to the real copy."""
    attempts: list[int] = []
    real = m._copy_once

    def fake(src: Path, tmp_fd: int, st0: os.stat_result) -> object:
        attempts.append(1)
        if errs:
            code = errs.pop(0)
            raise OSError(code, os.strerror(code), str(src))
        return real(src, tmp_fd, st0)

    return attempts, fake


def test_edeadlk_is_dataless_refused_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "p.docx", b"placeholder")
    attempts, fake = _failing_copy([errno.EDEADLK, errno.EDEADLK])
    monkeypatch.setattr(m, "_copy_once", fake)
    sleeps: list[float] = []
    dest = tmp_path / "o" / "p.docx"
    with pytest.raises(DatalessRefusedError) as exc:
        m.materialise(src, dest, ByteBudget(100, 1), sleep=sleeps.append)
    assert exc.value.errno == 11
    assert len(attempts) == 1
    assert sleeps == []
    assert not dest.parent.exists() or list(dest.parent.iterdir()) == []


def test_etimedout_retries_with_exponential_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "w.txt", b"warm")
    _, fake = _failing_copy([errno.ETIMEDOUT, errno.ETIMEDOUT])
    monkeypatch.setattr(m, "_copy_once", fake)
    sleeps: list[float] = []
    res = m.materialise(src, tmp_path / "o" / "w.txt", ByteBudget(100, 1), backoff_s=0.5, sleep=sleeps.append)
    assert res.attempts == 3
    assert sleeps == [0.5, 1.0]
    assert (tmp_path / "o" / "w.txt").read_bytes() == b"warm"


def test_etimedout_exhausts_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "w.txt", b"warm")
    attempts, fake = _failing_copy([errno.ETIMEDOUT] * 10)
    monkeypatch.setattr(m, "_copy_once", fake)
    sleeps: list[float] = []
    dest = tmp_path / "o" / "w.txt"
    with pytest.raises(ProviderTimeoutError) as exc:
        m.materialise(src, dest, ByteBudget(100, 1), retries=3, backoff_s=2.0, sleep=sleeps.append)
    assert exc.value.errno == 60
    assert len(attempts) == 4
    assert sleeps == [2.0, 4.0, 8.0]
    assert list(dest.parent.iterdir()) == []


def test_vanished_mid_read_propagates_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "v.txt", b"v")
    _, fake = _failing_copy([errno.ENOENT])
    monkeypatch.setattr(m, "_copy_once", fake)
    dest = tmp_path / "o" / "v.txt"
    with pytest.raises(FileNotFoundError):
        m.materialise(src, dest, ByteBudget(100, 1))
    assert list(dest.parent.iterdir()) == []


def test_other_errno_is_materialise_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "e.txt", b"e")
    _, fake = _failing_copy([errno.EIO])
    monkeypatch.setattr(m, "_copy_once", fake)
    with pytest.raises(MaterialiseError) as exc:
        m.materialise(src, tmp_path / "o" / "e.txt", ByteBudget(100, 1))
    assert exc.value.errno == errno.EIO
    assert not isinstance(exc.value, DatalessRefusedError | ProviderTimeoutError)


# ---------------------------------------------------------------------------------------------------------
# materialise: stability
# ---------------------------------------------------------------------------------------------------------


def _mutate_on_read(monkeypatch: pytest.MonkeyPatch, action: object) -> None:
    """Run ``action`` when the read window opens (after lstat + budget, before open)."""

    @contextlib.contextmanager
    def allowed() -> Iterator[None]:
        action()  # type: ignore[operator]
        yield

    monkeypatch.setattr(m, "materialize_allowed", allowed)


def test_size_change_during_copy_is_unstable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "grow.log", b"0123456789")
    _mutate_on_read(monkeypatch, lambda: src.write_bytes(b"0123456789-more"))
    dest = tmp_path / "o" / "grow.log"
    with pytest.raises(MaterialiseError, match="unstable"):
        m.materialise(src, dest, ByteBudget(1000, 1))
    assert list(dest.parent.iterdir()) == []


def test_mtime_only_change_during_copy_is_unstable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "same.txt", b"same-size")
    _mutate_on_read(monkeypatch, lambda: os.utime(src, ns=(1_000_000_000, 1_000_000_000)))
    with pytest.raises(MaterialiseError, match="unstable"):
        m.materialise(src, tmp_path / "o" / "same.txt", ByteBudget(1000, 1))


def test_replaced_between_lstat_and_open_is_unstable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _write(tmp_path / "doc.docx", b"v1")
    other = _write(tmp_path / "tmp~doc", b"v2")
    _mutate_on_read(monkeypatch, lambda: other.replace(src))  # Office safe-save: new inode at the same path
    with pytest.raises(MaterialiseError, match="replaced"):
        m.materialise(src, tmp_path / "o" / "doc.docx", ByteBudget(1000, 1))


def test_hydration_mtime_slack_applies_only_to_dataless_sources() -> None:
    assert m._mtime_stable(100, 100, was_dataless=False)
    assert not m._mtime_stable(100, 211, was_dataless=False)
    assert m._mtime_stable(1790712955856329330, 1790712955856329441, was_dataless=True)  # measured drift
    assert not m._mtime_stable(0, 2_000_000, was_dataless=True)
