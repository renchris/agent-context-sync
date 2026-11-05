"""A caller's deadline for the programs agentsync starts, and the registry that ends them (B13 round 35 C955,
ruling 106).

A leaf module: it imports nothing of agentsync, so ``gitops``, ``governance``, ``ops.launchd`` and
``ops.doctor`` can all reach it (``gitops`` cannot import ``doctor``, which imports ``gitops``). The
setup report's call sets :data:`RUN_DEADLINE` in the context its hook runs in; every program a hook starts
through :func:`run` or ``doctor._run`` then ends by that deadline, and :func:`end_children` ends any still
running when the report gives up on the call. Unset, as for a sync, standalone ``agentsync doctor`` and
every other command, :func:`run` is exactly ``subprocess.run``.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Any

RUN_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "doctor_run_deadline", default=None
)
"""A caller's deadline (a :func:`time.monotonic` value) for every program started through :func:`run` or
``doctor._run`` in its context, the setup report's time limit among them: a program still running then times
out, its check's own time-out line, instead of outliving the call that gave up on it. Once it has passed,
``doctor.run_checks`` stops with ``TimeoutError`` (:func:`stop_past_deadline`) rather than run on after the
caller has gone (B13 round 35 C952, ruling 105). Unset, as standalone ``agentsync doctor`` runs, each
program keeps its own limit and every check runs (B13 round 34 C804). ``doctor.RUN_DEADLINE`` is this
same object."""

_LIVE_CHILDREN: dict[int, bool] = {}
"""Each program still running under a caller's deadline: its pid, and whether it leads a process group of
its own (``doctor._run``'s watcher, ended with its group) or stays in the caller's (:func:`run`'s, ended
alone)."""
_LIVE_LOCK = threading.Lock()


def past_deadline() -> bool:
    """Whether a caller's :data:`RUN_DEADLINE` is set and has passed (never, standalone)."""
    deadline = RUN_DEADLINE.get()
    return deadline is not None and time.monotonic() >= deadline


def stop_past_deadline() -> None:
    """Raise ``TimeoutError`` once the caller's :data:`RUN_DEADLINE` has passed: the setup report gave up on
    this doctor then, so the check it was in stays the one named and no later check runs, names itself or
    hands back a fix (B13 round 35 C952, C953, C978; ruling 105). ``TimeoutError``, never
    ``subprocess.TimeoutExpired``: the raise can reach the report's call before its own wait ends, and the
    report's Doctor part catches only ``TimeoutError``. No margin: the deadline is the call's own limit, so
    no program starts after the call's ``end_children``. Called outside each check's ``try``, so no check
    turns it into a line. Standalone doctor sets no deadline and never stops."""
    if past_deadline():
        raise TimeoutError("the caller's deadline passed")


def register(pid: int, *, group: bool) -> None:
    """Record a running program for :func:`end_children`: ``group`` when it leads its own process group."""
    with _LIVE_LOCK:
        _LIVE_CHILDREN[pid] = group


def unregister(pid: int) -> None:
    """Forget a program once its wait has returned."""
    with _LIVE_LOCK:
        _LIVE_CHILDREN.pop(pid, None)


def end(pid: int, *, group: bool) -> None:
    """SIGKILL ``pid``: with its whole process group when it leads one (a shim's own git goes too), else
    alone, since a program left in the caller's group shares it with agentsync itself."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if group:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)


def end_children() -> int:
    """SIGKILL each program still running under a caller's deadline and return how many: a call a caller
    abandoned (the setup report's time limit runs its hooks in a thread it then leaves) keeps its child
    otherwise, which the interpreter's exit never stops (B13 round 34 C804, round 35 C955)."""
    with _LIVE_LOCK:
        live = sorted(_LIVE_CHILDREN.items())
    for pid, group in live:
        end(pid, group=group)
    return len(live)


def run(argv: Sequence[str], *, timeout: float, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run(argv, timeout=timeout, **kwargs)`` under a caller's :data:`RUN_DEADLINE`: with none,
    exactly that call. With one, the limit is ``min(timeout, deadline - now)``, ``TimeoutExpired`` is raised
    before any spawn once it has passed, and the program is registered for :func:`end_children` while it runs.
    It stays in the caller's process group, so a signal to that group still reaches it, and it is ended with
    ``os.kill``, never ``killpg`` (B13 round 35 C955, ruling 106)."""
    deadline = RUN_DEADLINE.get()
    if deadline is None:
        return subprocess.run(list(argv), timeout=timeout, **kwargs)  # noqa: PLW1510 - the caller's check
    left = deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired(list(argv), 0.0)
    timeout = min(timeout, left)
    data = kwargs.pop("input", None)
    check = bool(kwargs.pop("check", False))
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    if data is not None:
        kwargs["stdin"] = subprocess.PIPE
    with subprocess.Popen(list(argv), **kwargs) as proc:
        register(proc.pid, group=False)
        try:
            try:
                out, err = proc.communicate(data, timeout=timeout)
            except subprocess.TimeoutExpired:
                end(proc.pid, group=False)
                proc.wait()  # as subprocess.run does on POSIX: the program, not its pipes
                raise
            except BaseException:
                end(proc.pid, group=False)
                raise
        finally:
            unregister(proc.pid)
    done = subprocess.CompletedProcess(list(argv), proc.returncode, out, err)
    if check:
        done.check_returncode()
    return done
