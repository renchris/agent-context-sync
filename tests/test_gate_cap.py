"""scripts/gate.sh's machine-wide test-gate slot (cc-gate-slot), taken from the script and run over a stub CLI
that logs its argv and prints a chosen verdict line or exits a chosen status."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

import exec_stub

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "scripts" / "gate.sh"
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"

# A stand-in cc-gate-slot: every call is logged; acquire prints STUB_LINE and exits STUB_RC, after sleeping
# STUB_SLEEP seconds in a wait a TERM interrupts (logged); held answers STUB_HELD_RC, or 1 under a test
# marker, as the real CLI's in-test guard does.
STUB = """#!/bin/bash
echo "$*" >>"$STUB_LOG"
case $1 in
  acquire)
    if [ -n "${STUB_SLEEP:-}" ]; then
      trap 'echo TERM >>"$STUB_LOG"; kill "$nap" 2>/dev/null; exit 143' TERM
      /bin/sleep "$STUB_SLEEP" &
      nap=$!
      echo queued >>"$STUB_LOG"
      wait "$nap"
    fi
    [ -z "${STUB_LINE:-}" ] || printf '%s\\n' "$STUB_LINE"
    exit "${STUB_RC:-0}"
    ;;
  held)
    if [ -n "${PYTEST_CURRENT_TEST:-}" ]; then
      echo in-test >&2
      exit 1
    fi
    exit "${STUB_HELD_RC:-0}"
    ;;
esac
exit 0
"""

TOKEN = "/pool|000000000001|4242|Thu Oct 29 10:00:00 2026"


def _line(verdict: str = "granted", grant: str = "2", owned: str = "1", token: str = TOKEN) -> str:
    return (
        f"verdict={verdict} grant={grant} want=4 min=2 cap=6 class=normal waited_s=0 ticket=000000000001 "
        f"clamped=0 owned={owned} token={token}"
    )


def _script() -> str:
    return GATE.read_text(encoding="utf-8")


def _function(name: str) -> str:
    found = re.search(rf"(?ms)^{name}\(\) \{{.*?^\}}$", _script())
    assert found is not None, f"gate.sh's {name}"
    return found.group(0)


def _acquire_block() -> str:
    """The slot block: everything between ``full_set()`` and the mode dispatch."""
    found = re.search(r'(?ms)^full_set\(\) \{.*?^\}$\n(.*?)^if \[ "\$mode" = affected \]', _script())
    assert found is not None, "gate.sh's slot block, between full_set and the mode dispatch"
    return found.group(1)


def _traps() -> str:
    """gate.sh's top-level ``trap`` lines and the ``SCAN`` arrays ``full_set`` reads."""
    lines = _script().splitlines()
    return "\n".join(ln for ln in lines if ln.startswith(("trap ", "SCAN=", "SCAN_IGNORE=")))


def _stub(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    return exec_stub.write(bin_dir / "cc-gate-slot", STUB)  # a Mach-O run by path (B13 round 23 A97)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The parent env: a stub CLI, and a width already set that a gate acting on no grant leaves alone."""
    env = {k: v for k, v in os.environ.items() if k != "CC_GATE_SLOT_TOKEN"}
    env.update(
        CC_GATE_SLOT_BIN=str(_stub(tmp_path)),
        STUB_LOG=str(tmp_path / "stub.log"),
        PYTEST_XDIST_AUTO_NUM_WORKERS="7",
    )
    env.update(extra)
    return env


def _harness(tmp_path: Path, mode: str) -> str:
    """gate.sh from its traps to the dispatch, with the pytest parts stubbed to print their argv and width."""
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return (
        "set -euo pipefail\n"
        f"mode={mode} tree=t0 salt=s0 skipped_timing=0 resumed=0 common='{tmp_path}' tmp='{tmp_path}/tmp'\n"
        f"{_traps()}\n{_function('finish')}\n"
        "union_check() { :; }\n"
        'run_part() { echo "PART $* W=${PYTEST_XDIST_AUTO_NUM_WORKERS-unset}"; }\n'
        f"{_function('gate_n')}\n{_function('full_set')}\n"
        f"{_acquire_block()}\n"
        'echo "TOKEN=${CC_GATE_SLOT_TOKEN-unset}"\n'
        "full_set\n"
    )


def _gate(tmp_path: Path, env: dict[str, str], mode: str = "land") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", _harness(tmp_path, mode)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _widths(stdout: str) -> dict[str, str]:
    """Each part's effective width: its ``-n`` value, or for ``-n auto`` the env xdist reads."""
    widths = {}
    for ln in stdout.splitlines():
        if ln.startswith("PART "):
            words = ln.split()
            n = words[words.index("-n") + 1]
            widths[words[1]] = words[-1].removeprefix("W=") if n == "auto" else n
    return widths


def _log(tmp_path: Path) -> list[str]:
    log = tmp_path / "stub.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_the_slot_is_taken_after_every_static_step_and_before_the_tests() -> None:
    """A queued gate has already run the cheap checks; no pytest part (union_check's collect included) starts
    before the grant sizes it."""
    script = _script()
    last_static = script.index("step make -C probes check")
    acquire = script.index("acquire --holder-pid")
    dispatch = script.index('if [ "$mode" = affected ]')
    assert last_static < acquire < dispatch
    assert script.count("acquire --holder-pid") == 1


@pytest.mark.parametrize(("mode", "klass"), [("land", "land"), ("lane", "normal"), ("affected", "normal")])
def test_a_grant_of_2_gives_every_part_two_workers(tmp_path: Path, mode: str, klass: str) -> None:
    """A grant narrower than a part's fixed width cuts it; ``-n auto`` takes it through the env. The token is
    exported because the gate owns it, and released when the gate exits."""
    done = _gate(tmp_path, _env(tmp_path, STUB_LINE=_line(grant="2")), mode)
    assert done.returncode == 0, done.stderr
    assert _widths(done.stdout) == {"scan": "2", "deploy_pack": "2", "rest": "2"}, done.stdout
    assert f"== gate slot: {_line(grant='2')}" in done.stdout
    assert f"TOKEN={TOKEN}" in done.stdout
    acquire, release = _log(tmp_path)
    assert acquire.startswith("acquire --holder-pid ")
    assert f" --want 4 --min 2 --class {klass} --label acs gate {mode} {tmp_path.name}" in acquire
    assert release == f"release --token {TOKEN}"


def test_a_wide_grant_leaves_the_fixed_widths(tmp_path: Path) -> None:
    done = _gate(tmp_path, _env(tmp_path, STUB_LINE=_line(verdict="nested", grant="6", owned="0")))
    assert done.returncode == 0, done.stderr
    assert _widths(done.stdout) == {"scan": "2", "deploy_pack": "4", "rest": "6"}, done.stdout
    assert "TOKEN=unset" in done.stdout, (
        "a nested gate's token is its parent's: never exported, never released"
    )
    assert [ln.split()[0] for ln in _log(tmp_path)] == ["acquire"]


def test_an_absent_helper_leaves_the_gate_uncapped(tmp_path: Path) -> None:
    env = _env(tmp_path, PATH="/usr/bin:/bin")
    del env["CC_GATE_SLOT_BIN"]
    done = _gate(tmp_path, env)
    assert done.returncode == 0, done.stderr
    assert "== gate slot: helper absent, uncapped" in done.stdout
    assert _widths(done.stdout) == {"scan": "2", "deploy_pack": "4", "rest": "7"}, done.stdout
    assert not (tmp_path / "stub.log").exists()


@pytest.mark.parametrize(
    ("verdict", "grant"),
    [("off", "4"), ("fail-open", "4"), ("in-test", "4"), ("granted", ""), ("granted", "x"), ("granted", "0")],
)
def test_a_verdict_without_a_usable_grant_leaves_the_width_as_it_was(
    tmp_path: Path, verdict: str, grant: str
) -> None:
    done = _gate(tmp_path, _env(tmp_path, STUB_LINE=_line(verdict=verdict, grant=grant, owned="0")))
    assert done.returncode == 0, done.stderr
    assert _widths(done.stdout) == {"scan": "2", "deploy_pack": "4", "rest": "7"}, done.stdout
    assert "TOKEN=unset" in done.stdout


@pytest.mark.parametrize("rc", [64, 127])
def test_a_helper_fault_leaves_the_gate_uncapped(tmp_path: Path, rc: int) -> None:
    """A usage error or a missing command is the helper's fault and never refuses a gate."""
    done = _gate(tmp_path, _env(tmp_path, STUB_RC=str(rc), STUB_LINE=""))
    assert done.returncode == 0, done.stderr
    assert f"== gate slot: helper error rc={rc}, uncapped" in done.stdout
    assert _widths(done.stdout) == {"scan": "2", "deploy_pack": "4", "rest": "7"}, done.stdout


def test_a_helper_path_that_is_gone_leaves_the_gate_uncapped(tmp_path: Path) -> None:
    """A CC_GATE_SLOT_BIN naming no file: bash 3.2 under ``set -e`` ends that job 1, not 127 (measured)."""
    env = _env(tmp_path, CC_GATE_SLOT_BIN=str(tmp_path / "missing" / "cc-gate-slot"))
    done = _gate(tmp_path, env)
    assert done.returncode == 0, done.stderr
    assert "== gate slot: helper error rc=" in done.stdout
    assert _widths(done.stdout) == {"scan": "2", "deploy_pack": "4", "rest": "7"}, done.stdout


def test_a_helper_killed_while_queued_ends_the_gate_with_its_status(tmp_path: Path) -> None:
    done = _gate(tmp_path, _env(tmp_path, STUB_RC="143", STUB_LINE=""))
    assert done.returncode == 143, (done.stdout, done.stderr)
    assert "PART " not in done.stdout, "nothing runs"


def test_a_term_while_queued_ends_the_gate_and_takes_the_acquire_out_of_the_queue(tmp_path: Path) -> None:
    """A ``$( )`` acquire would hold the TERM until the grant; a waited background job lets it land, and
    finish() TERMs the still-queued CLI. A ceiling, not a latency: a loaded Mac delivers a TERM late."""
    env = _env(tmp_path, STUB_SLEEP="60", STUB_LINE=_line())
    out = tmp_path / "gate.out"
    with out.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [BASH, "-c", _harness(tmp_path, "land")], cwd=tmp_path, env=env, stdout=sink, stderr=sink
        )
        try:
            deadline = time.monotonic() + 30
            while "queued" not in _log(tmp_path) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert "queued" in _log(tmp_path), "the stub acquire never started"
            proc.terminate()
            assert proc.wait(timeout=30) == 143
        finally:
            if proc.poll() is None:
                proc.kill()
    deadline = time.monotonic() + 30
    while "TERM" not in _log(tmp_path) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert "TERM" in _log(tmp_path), _log(tmp_path)
    assert "PART " not in out.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("owned", "token", "released"), [("1", TOKEN, True), ("1", "", False), ("0", TOKEN, False)]
)
def test_finish_releases_only_a_slot_this_gate_owns(
    tmp_path: Path, owned: str, token: str, released: bool
) -> None:
    (tmp_path / "tmp").mkdir()
    harness = (
        "set -euo pipefail\n"
        f"mode=land tree=t0 salt=s0 skipped_timing=0 resumed=0 common='{tmp_path}' tmp='{tmp_path}/tmp'\n"
        f"gate_slot_bin='{_stub(tmp_path)}' gate_slot_owned={owned} gate_slot_token='{token}'\n"
        f"{_function('finish')}\n"
        "trap finish EXIT\n"
    )
    done = subprocess.run(
        [BASH, "-c", harness], env=_env(tmp_path), capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert _log(tmp_path) == ([f"release --token {TOKEN}"] if released else [])
    assert " rc=0 " in (tmp_path / "gate-verdicts.log").read_text(encoding="utf-8")
