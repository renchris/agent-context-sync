"""Machine gate slots for a bare multi-worker pytest run; not a test file, loaded from ``tests/conftest.py``.

A ``pytest -n 6`` started outside scripts/gate.sh asks ``cc-gate-slot acquire`` for workers before xdist
turns ``numprocesses`` into its ``tx`` list (xdist ``plugin.py`` ``pytest_cmdline_main``, ``tryfirst``): a
hookwrapper's code before its ``yield`` runs ahead of every plain implementation. The run then starts as many
workers as the machine granted, and the slots are released at ``pytest_unconfigure`` when this run owns
them. Under gate.sh the CLI answers ``nested`` (the env carries the gate's token) and the width is only
clamped to the gate's grant.

Exempt: an xdist worker (``PYTEST_XDIST_WORKER``: xdist calls ``pytest_cmdline_main`` in every worker too,
and a worker that widened itself would start workers of its own), ``--collect-only``, a run of fewer than
two workers, and a machine with no ``cc-gate-slot`` (CI). The helper is ``CC_GATE_SLOT_BIN``, else the one
on PATH, else ``~/.claude/bin/cc-gate-slot``. A helper killed while queued (rc 129/130/137/143) ends the run
with that code and no test run, so a cut queue never reads green; any other non-zero rc warns once and runs
uncapped. Contract: claude-infrastructure ``docs/plans/GATE_CONCURRENCY_CAP.md`` D12, §3.4 and §3.6.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

KILLED_WHILE_QUEUED = frozenset({129, 130, 137, 143})
_FIELD = re.compile(r"(?:^| )(verdict|grant|owned)=(\S*)")
_GRANT = re.compile(r"[1-9][0-9]*")
_RELEASE_TIMEOUT_S = 300
_HELD = pytest.StashKey[tuple[str, str]]()


def _warn(message: str) -> None:
    sys.stderr.write(f"gate slot: {message}\n")
    sys.stderr.flush()


def workers(config: pytest.Config) -> int:
    """The worker count xdist will start, resolving ``auto``/``logical`` as xdist does (0: xdist is off)."""
    count = getattr(config.option, "numprocesses", None)
    if count in ("auto", "logical"):
        if config.getoption("usepdb", False):
            return 0  # xdist runs --pdb serially
        env = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS", "").strip()
        return int(env) if env.isdigit() else os.cpu_count() or 1
    return count if isinstance(count, int) else 0


def helper() -> str | None:
    """The ``cc-gate-slot`` to ask, or None when there is none (an explicit ``CC_GATE_SLOT_BIN`` is final)."""
    named = os.environ.get("CC_GATE_SLOT_BIN")
    if named:
        return shutil.which(named)
    return shutil.which("cc-gate-slot") or shutil.which(str(Path.home() / ".claude" / "bin" / "cc-gate-slot"))


def parse_verdict(line: str) -> dict[str, str]:
    """The fields of the CLI's one stdout line. ``token=`` comes last and holds spaces (the holder's
    lstart), so it is everything after the last `` token=``, never a whitespace split (§A.4 item 3)."""
    head, sep, token = line.rpartition(" token=")
    if not sep:
        head, token = line, ""
        if line.startswith("token="):
            head, token = "", line.removeprefix("token=")
    fields = dict(_FIELD.findall(head))
    fields["token"] = token
    return fields


def _replay(previous: Any, deferred: list[int], *, defer: bool) -> None:
    """Put SIGINT's handler back and, if an interrupt came while it was away, raise it now
    (:func:`_acquire`)."""
    if not defer:
        return
    signal.signal(signal.SIGINT, previous)
    if deferred:
        signal.raise_signal(signal.SIGINT)


def _acquire(config: pytest.Config) -> None:
    if "PYTEST_XDIST_WORKER" in os.environ or config.getoption("collectonly", False):
        return
    want = workers(config)
    if want < 2:
        return
    cli = helper()
    if cli is None:
        return
    argv = [cli, "acquire", "--holder-pid", str(os.getpid()), "--want", str(want), "--min", "2"]
    argv += ["--label", f"pytest -n {want} {Path.cwd()}"]
    # A SIGINT while Popen waits for the exec (the helper may already be queued) raised before ``proc`` was
    # bound, so nothing terminated the helper and its children held the run's stderr. The interrupt is only
    # recorded around Popen, then replayed in the try that terminates it (B13 round 38 C1461). The signal
    # mask is never touched: a mask is inherited across exec, which would leave the helper's INT trap dead; a
    # caught disposition resets to SIG_DFL there, and SIG_IGN or SIG_DFL is left as it is.
    previous = signal.getsignal(signal.SIGINT)
    deferred: list[int] = []
    defer = callable(previous) and threading.current_thread() is threading.main_thread()
    if defer:
        signal.signal(signal.SIGINT, lambda signum, _frame: deferred.append(signum))
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, text=True
        )  # stderr passes through: QUEUED lines
    except OSError as error:
        _replay(previous, deferred, defer=defer)
        _warn(f"{cli} could not start ({error}); running {want} workers uncapped")
        return
    except BaseException:  # any other failure puts the handler back too: Ctrl-C never stays dead
        _replay(previous, deferred, defer=defer)
        raise
    try:
        _replay(previous, deferred, defer=defer)
        out, _ = proc.communicate()
    except KeyboardInterrupt:
        # CPython's own path SIGKILLs the child 0.25 s later, which leaves its trap nothing to clean.
        proc.terminate()
        proc.wait()
        raise
    rc = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    if rc in KILLED_WHILE_QUEUED:
        # Not pytest.exit: pytest's main() does not catch an Exit raised from pytest_cmdline_main, so the
        # run would end rc 1 with a traceback instead of the CLI's code (measured, pytest 8.4.2).
        _warn("killed while queued")
        raise SystemExit(rc)
    if rc != 0:
        _warn(f"cc-gate-slot acquire exited {rc}; running {want} workers uncapped")
        return
    lines = out.strip().splitlines()
    fields = parse_verdict(lines[-1]) if lines else {}
    if fields.get("owned") == "1" and fields.get("token"):
        config.stash[_HELD] = (cli, fields["token"])
    grant = fields.get("grant", "")
    if not _GRANT.fullmatch(grant):
        return
    if fields.get("verdict") == "granted":
        config.option.numprocesses = int(grant)
    elif fields.get("verdict") == "nested":
        config.option.numprocesses = min(want, int(grant))


@pytest.hookimpl(hookwrapper=True)
def pytest_cmdline_main(config: pytest.Config) -> Iterator[None]:
    """Take the slots before xdist reads ``numprocesses``."""
    _acquire(config)
    yield


def pytest_unconfigure(config: pytest.Config) -> None:
    """Release the slots this run owns (``owned=1``); a nested, off or fail-open run owns nothing (§3.6)."""
    held = config.stash.get(_HELD, None)
    if held is None:
        return
    del config.stash[_HELD]
    cli, token = held
    try:
        done = subprocess.run(
            [cli, "release", "--token", token],
            stdout=subprocess.DEVNULL,
            check=False,
            timeout=_RELEASE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _warn(f"release did not finish ({error}); the reaper frees the slots once this run exits")
        return
    if done.returncode != 0:
        _warn(f"cc-gate-slot release exited {done.returncode}")
