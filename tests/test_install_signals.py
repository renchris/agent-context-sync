"""install.sh's stops (plan B-10): a signal that ends the run also stops the tool its wait ran, a tool that
outlives the stop is named in the NEXT, SIGINT says the person stopped waiting, and the wait for the first
background run names a refused kickstart and a ``launchctl print`` it could not read.

The harness is test_install_oneshot's (a tmp HOME, a stub ``uv``, a stub ``launchctl``). A stopped run starts
under a Python that puts SIGINT and SIGHUP back to their defaults, then execs bash: a detached gate passes
them on ignored, and bash can trap no signal ignored when it starts.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import exec_stub
from test_install_oneshot import (
    BASH32,
    INSTALL_SH,
    STUB_AGENTSYNC,
    _shell_function,
    calls,
    env,  # noqa: F401 - the fixture
    folder,  # noqa: F401 - the fixture
    install_sh,
    report_path,
    rerun,
    wheel,  # noqa: F401 - the fixture
)

DEFAULT_SIGNALS = (
    "import os, signal, sys\n"
    "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    "os.execv(sys.argv[1], sys.argv[1:])\n"
)

# The first sync (AGENTSYNC_NO_NEXT_HINT=1) records its pid and hangs; with STUCK_IGNORE_TERM it ignores TERM.
STUCK = """#!/bin/bash
case " $* " in *" --help "*) exec "$STUB_INNER" "$@" ;; esac
if [ "$1" = sync ] && [ "${AGENTSYNC_NO_NEXT_HINT:-}" = 1 ]; then
  [ -z "${STUCK_IGNORE_TERM:-}" ] || trap '' TERM
  printf '%s' "$$" > "$STUCK_PID"
  exec /bin/sleep 30
fi
exec "$STUB_INNER" "$@"
"""


def _exe(path: Path, text: str) -> str:
    return str(exec_stub.write(path, text))


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _stuck(base: dict[str, str], tmp_path: Path, ignore_term: bool) -> tuple[dict[str, str], Path]:
    pid = tmp_path / "stuck.pid"
    e = {
        **base,
        "STUB_AGENTSYNC": _exe(tmp_path / "agentsync-stuck", STUCK),
        "STUB_INNER": _exe(tmp_path / "agentsync-inner", STUB_AGENTSYNC),
        "STUCK_PID": str(pid),
    }
    if ignore_term:
        e["STUCK_IGNORE_TERM"] = "1"
    return e, pid


def _stopped_run(
    e: dict[str, str], pid_file: Path, sig: signal.Signals, *args: str
) -> tuple[int, str, str, int, float]:
    """install.sh ARGS --confirm-install-agent, sent SIG to its own pid alone once the first sync runs: its
    code, stdout, stderr, the sync's pid and when the signal was sent. The sync is killed afterwards, whatever
    the run left."""
    argv = [sys.executable, "-c", DEFAULT_SIGNALS, BASH32, str(INSTALL_SH), *args, "--confirm-install-agent"]
    proc = subprocess.Popen(
        argv,
        env=e,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    pid = 0
    try:
        deadline = time.monotonic() + 60
        while not (pid_file.exists() and pid_file.read_text().strip()):
            assert proc.poll() is None and time.monotonic() < deadline, "the first sync never started"
            time.sleep(0.05)
        pid = int(pid_file.read_text())
        sent = time.monotonic()
        os.kill(proc.pid, sig)
        stdout, stderr = proc.communicate(timeout=60)
    finally:
        _kill_left(proc.pid, pid)
    return proc.returncode, stdout, stderr, pid, sent


def _kill_left(group: int, pid: int) -> None:
    """KILL what a stopped run left: its process group and the first sync. macOS returns EPERM for a group
    whose members have all exited but are not yet reaped, which is gone too (B13 round 38 C1462)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)
    if pid:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def test_a_stopped_runs_cleanup_takes_eperm_from_a_group_already_gone_c1462(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B13 round 38 C1462: a stopped run's cleanup let the PermissionError macOS raises for a group whose
    members exited unreaped escape its finally, failing a test whose run had passed; a re-run passed it, so
    the gate read a race as load. The cleanup takes EPERM as it takes ESRCH."""

    def refused(*_args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", refused)
    monkeypatch.setattr(os, "kill", refused)
    _kill_left(2**22 + 1, 2**22 + 3)


def _gone_by(pid: int, deadline: float) -> bool:
    while not _gone(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _last(stdout: str) -> str:
    return stdout.rstrip("\n").splitlines()[-1]


# ---- B-10.1: a stop reaches the running tool ----


def test_term_stops_the_first_sync_child(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    e, pid_file = _stuck(env, tmp_path, ignore_term=False)
    rc, stdout, stderr, pid, sent = _stopped_run(
        e, pid_file, signal.SIGTERM, str(wheel), "--source-local", str(folder)
    )
    assert rc == 143, stdout + stderr
    assert _gone_by(pid, sent + 5), "the first sync outlived the stop"
    assert _last(stdout) == (
        "NEXT: this run was stopped before it finished (stopped by SIGTERM, perhaps a tool timeout); it is "
        f"safe to re-run: {rerun(wheel)} [setup report: {report_path(env)}]"
    )


def test_a_child_that_ignores_term_is_named_in_next(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    e, pid_file = _stuck(env, tmp_path, ignore_term=True)
    rc, stdout, stderr, pid, _ = _stopped_run(
        e, pid_file, signal.SIGTERM, str(wheel), "--source-local", str(folder)
    )
    assert rc == 143, stdout + stderr
    assert _last(stdout) == (
        "NEXT: this run was stopped before it finished (stopped by SIGTERM, perhaps a tool timeout); it is "
        f"safe to re-run: {rerun(wheel)}; first sync (pid {pid}) may still be running, and the re-run waits "
        f"for its lock [setup report: {report_path(env)}]"
    )


def test_int_says_stopped_waiting(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    e, pid_file = _stuck(env, tmp_path, ignore_term=False)
    rc, stdout, stderr, pid, sent = _stopped_run(
        e, pid_file, signal.SIGINT, str(wheel), "--source-local", str(folder)
    )
    assert rc == 130, stdout + stderr
    assert "Stopped waiting. install.sh received SIGINT in step first-sync.\n" in stderr, stderr
    assert "error: install.sh was stopped by a signal" not in stderr, stderr
    assert _gone_by(pid, sent + 5), "SIGINT never reaches a background child: the run stops it with TERM"
    assert _last(stdout).startswith(
        "NEXT: this run was stopped before it finished (stopped by SIGINT); it is "
    )
    assert "Stopped waiting" not in stdout, stdout


# ---- B-10.2: the wait names what it could not see ----


def _launchctl(base: dict[str, str], tmp_path: Path, body: str) -> dict[str, str]:
    """``base`` whose launchctl runs BODY first (it may exit), then the oneshot stub."""
    text = f'#!/bin/bash\n{body}\nexec "$STUB_LC_INNER" "$@"\n'
    return {
        **base,
        "STUB_LC_INNER": base["AGENTSYNC_LAUNCHCTL"],
        "AGENTSYNC_LAUNCHCTL": _exe(tmp_path / "launchctl-wrap", text),
        "AGENTSYNC_WAIT_SECONDS": "1",
    }


def test_a_refused_kickstart_is_named_when_the_wait_ends(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    body = (
        'if [ "$1" = kickstart ]; then\n'
        '  echo "launchctl $*" >> "$STUB_LOG"\n'
        '  printf "Could not kickstart service (stub)\\nsecond line\\n" >&2\n'
        "  exit 37\n"
        "fi"
    )
    e = _launchctl(env, tmp_path, body)
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode != 0, cp.stdout + cp.stderr
    assert (
        "error: no background run exited 0 within 1s (last exit: none; state: not running); "
        "launchctl kickstart exited 37: Could not kickstart service (stub)\n"
    ) in cp.stderr, cp.stderr
    assert "second line" not in cp.stderr
    assert sum(c.startswith("launchctl kickstart ") for c in calls(env)) == 1


@pytest.mark.parametrize(
    ("code", "state"), [("5", "unread (launchctl print exited 5)"), ("113", "not loaded")]
)
def test_an_unreadable_launchd_print_is_not_reported_as_not_loaded(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
    code: str,
    state: str,
) -> None:
    e = {**_launchctl(env, tmp_path, '[ "$1" != print ] || exit "$PRINT_CODE"'), "PRINT_CODE": code}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode != 0, cp.stdout + cp.stderr
    assert f"error: no background run exited 0 within 1s (last exit: none; state: {state})\n" in cp.stderr, (
        cp.stderr
    )


@pytest.mark.parametrize(
    ("command", "want", "least", "most"),
    [
        ("exec sleep 30", 143, 1, 12),  # a plain child: the TERM ends it at the limit
        ("trap '' TERM; sleep 3; exit 0", 143, 2, 14),  # swallows the TERM and exits 0 in the grace
        ("trap '' TERM; exec sleep 60", 143, 15, 45),  # never ends: KILLed after the grace
        ("exit 7", 7, 0, 5),  # done before its limit: its own status
    ],
)
def test_with_timeout_bounds_a_child_that_outlives_its_term_and_reports_it_stopped_c1494(
    command: str, want: int, least: float, most: float
) -> None:
    """B13 round 38 C1494 (makes worse True for a kill of the descendant tree): with_timeout sent one TERM
    at its limit and then waited with no bound, and returned the child's own status, so a child that ignored
    the TERM ran on and one that swallowed it and exited 0 read as a success. Its watchdog now gives the pid
    15 s after the TERM, then KILLs that pid alone, and a limit that fired returns 143 whatever the child then
    exited with."""
    script = "\n".join(
        [
            _shell_function("watchdog"),
            _shell_function("with_timeout"),
            f"with_timeout 1 /bin/sh -c {shlex.quote(command)} >/dev/null 2>&1",
            'echo "rc=$?"',
        ]
    )
    t0 = time.monotonic()
    cp = subprocess.run([BASH32, "-c", script], capture_output=True, text=True, check=False, timeout=90)
    took = time.monotonic() - t0
    assert cp.stdout.strip() == f"rc={want}", cp.stdout + cp.stderr
    assert least <= took <= most, took
