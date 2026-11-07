"""holds.json's read, change and replace run under one flock (§16.300.3), in real parallel processes."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from agentsync import governance as gv
from agentsync.ops.lock import SingleWriterLock

_WAIT_S = 600  # a process waiting on a gate slot on a loaded machine, never the test's point

_WORKER = """
import sys
from pathlib import Path
from agentsync import governance as gv

state, verb = Path(sys.argv[1]), sys.argv[2]
sys.stdout.write("ready\\n")
sys.stdout.flush()
sys.stdin.readline()  # both processes are up before either writes
for scope in sys.argv[3:]:
    if verb == "set":
        gv.set_hold(state, scope, reason="matter 7", owner="Contoso Legal")
    else:
        gv.release_hold(state, scope, owner="Contoso Legal")
"""


def _run_together(state: Path, *runs: tuple[str, list[str]]) -> None:
    """Start one process per (verb, scopes), let them go at one moment, and wait for each to end clean."""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(state), verb, *scopes],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for verb, scopes in runs
    ]
    for proc in procs:
        assert proc.stdout is not None
        assert proc.stdout.readline() == "ready\n"
    for proc in procs:
        assert proc.stdin is not None
        proc.stdin.write("go\n")
        proc.stdin.flush()
    for proc in procs:
        _out, err = proc.communicate(timeout=_WAIT_S)
        assert proc.returncode == 0, err


def _audit(state: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in gv.audit_path(state).read_text(encoding="utf-8").splitlines()]


def test_parallel_sets_keep_every_hold(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = [f"first-{i}" for i in range(25)]
    second = [f"second-{i}" for i in range(25)]

    _run_together(state, ("set", first), ("set", second))

    assert {h.scope for h in gv.state_holds(state)} == set(first) | set(second)
    assert sum(1 for rec in _audit(state) if rec["action"] == "hold-set") == 50


def test_a_release_racing_a_set_is_never_undone(tmp_path: Path) -> None:
    state = tmp_path / "state"
    scope = "racing"

    _run_together(state, ("set", [scope] * 25), ("release", [scope] * 25))

    listed = scope in {h.scope for h in gv.state_holds(state)}
    last = [rec["action"] for rec in _audit(state) if rec["scope"] == scope][-1]
    assert listed == (last == "hold-set"), (listed, last)


def test_a_hold_is_not_blocked_by_the_sync_lock(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    lock = SingleWriterLock(state / "agentsync.lock", "reconcile")
    lock.acquire()
    try:
        thread = threading.Thread(
            target=gv.set_hold, args=(state, "all"), kwargs={"reason": "matter 7", "owner": "Contoso Legal"}
        )
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
    finally:
        lock.release()
    assert [h.scope for h in gv.state_holds(state)] == ["all"]
