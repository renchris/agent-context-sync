"""tests/_gate_slot.py, the bare-run gate-slot plugin, run on a suite of its own against a stub cc-gate-slot.

Each case starts a fresh pytest in its own session (so a case that fails leaves no worker or stub behind)
with the plugin wired from that suite's conftest exactly as tests/conftest.py wires it, and counts the
xdist workers by the distinct ``[gwN]`` ids of ``-v`` output. The stub logs its argv, one invocation a line.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import _gate_slot

TESTS = Path(_gate_slot.__file__).parent
TOKEN = "/pool|000000000001|{pid}|Thu Oct 29 11:00:00 2026"
INHERITED = "/pool|000000000007|1|Thu Oct 29 10:00:00 2026"
CEILING_S = 300
SEP = "\x1f"

SUITE = """
import time

import pytest


@pytest.mark.parametrize("i", range(8))
def test_spread(i):
    time.sleep(0.2)
"""

CONFTEST = "from _gate_slot import pytest_cmdline_main, pytest_unconfigure  # noqa: F401\n"

STUB = r"""#!/bin/bash
for a in "$@"; do printf '%s\037' "$a"; done >> "$STUB_LOG"
echo >> "$STUB_LOG"
[ "$1" = release ] && exit 0
if [ -n "${CC_GATE_SLOT_TOKEN:-}" ]; then
  printf '%s%s\n' "verdict=nested grant=${CC_GATE_SLOT_GRANT} want=$5 min=2 cap=6 class=normal" \
    " waited_s=0 ticket= clamped=0 owned=0 token=${CC_GATE_SLOT_TOKEN}"
  exit 0
fi
case "${STUB_MODE:-grant}" in
  rc) exit "$STUB_RC" ;;
  forever)
    echo "cc-gate-slot: QUEUED ticket=000000000001 (run_in_background)" >&2
    trap 'kill "$nap" 2>/dev/null; : > "$STUB_LOG.term"; exit 143' TERM
    : > "$STUB_LOG.queued"
    sleep 600 & nap=$!
    wait "$nap"
    exit 0 ;;
esac
printf '%s%s\n' "verdict=granted grant=${STUB_GRANT} want=$5 min=2 cap=6 class=normal waited_s=0" \
  " ticket=000000000001 clamped=0 owned=1 dir=/pool token=/pool|000000000001|$3|Thu Oct 29 11:00:00 2026"
"""


class Inner:
    """One inner pytest run's suite, stub and environment."""

    def __init__(self, tmp_path: Path) -> None:
        self.suite = tmp_path / "suite"
        self.suite.mkdir()
        (self.suite / "conftest.py").write_text(CONFTEST, encoding="utf-8")
        (self.suite / "test_spread.py").write_text(SUITE, encoding="utf-8")
        self.stub = tmp_path / "cc-gate-slot"
        self.stub.write_text(STUB, encoding="utf-8")
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "stub.log"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CC_GATE_SLOT_", "PYTEST_"))}
        self.env |= {
            "CC_GATE_SLOT_BIN": str(self.stub),
            "STUB_LOG": str(self.log),
            "PYTHONPATH": str(TESTS),
            "STUB_GRANT": "2",
        }

    def start(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-v", *args],
            cwd=self.suite,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    def run(self, *args: str) -> tuple[int, str]:
        proc = self.start(*args)
        with _reaped(proc):
            out, _ = proc.communicate(timeout=CEILING_S)
        return proc.returncode, out

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [line.split(SEP)[:-1] for line in self.log.read_text(encoding="utf-8").splitlines()]


@contextlib.contextmanager
def _reaped(proc: subprocess.Popen[str]) -> Iterator[None]:
    """Kill the inner run's whole session on the way out, whatever the case did."""
    try:
        yield
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=CEILING_S)


def _workers(out: str) -> set[str]:
    return set(re.findall(r"\[(gw\d+)\]", out))


def _passed(out: str) -> bool:
    return re.search(r"^=+ .*\bpassed\b", out, re.MULTILINE) is not None


def _wait_for(path: Path, proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + CEILING_S
    while not path.exists():
        assert proc.poll() is None, f"the run ended before {path.name}: rc {proc.returncode}"
        assert time.monotonic() < deadline, f"no {path.name} in {CEILING_S}s"
        time.sleep(0.1)


@pytest.fixture
def inner(tmp_path: Path) -> Inner:
    return Inner(tmp_path)


@pytest.mark.parametrize("i", range(12))
def test_width_probe(i: int) -> None:
    """Enough short tests for a worker-count check of this file's own run to see the workers spread."""
    time.sleep(0.2)


def test_parse_verdict_takes_the_whole_token_after_the_last_token_key() -> None:
    line = (
        "verdict=granted grant=4 want=6 min=2 cap=6 class=normal waited_s=3 ticket=000000000012 clamped=1 "
        "owned=1 dir=/a b token=/a b|000000000012|501|Thu Oct 29 11:00:00 2026"
    )
    fields = _gate_slot.parse_verdict(line)
    assert fields == {
        "verdict": "granted",
        "grant": "4",
        "owned": "1",
        "token": "/a b|000000000012|501|Thu Oct 29 11:00:00 2026",
    }
    assert _gate_slot.parse_verdict("verdict=fail-open grant=6 owned=0 token=")["token"] == ""


def test_a_grant_of_two_turns_six_workers_into_two_and_is_released(inner: Inner) -> None:
    rc, out = inner.run("-n", "6")
    assert rc == 0, out
    assert _workers(out) == {"gw0", "gw1"}, out
    acquire, release = inner.calls()
    pid = acquire[acquire.index("--holder-pid") + 1]
    assert acquire == [
        "acquire",
        "--holder-pid",
        pid,
        "--want",
        "6",
        "--min",
        "2",
        "--label",
        f"pytest -n 6 {inner.suite.resolve()}",
    ]
    assert release == ["release", "--token", TOKEN.format(pid=pid)]


def test_auto_resolves_from_the_env_as_xdist_does(inner: Inner) -> None:
    inner.env |= {"PYTEST_XDIST_AUTO_NUM_WORKERS": "4", "STUB_GRANT": "3"}
    rc, out = inner.run("-n", "auto")
    assert rc == 0, out
    assert _workers(out) == {"gw0", "gw1", "gw2"}, out
    acquire = inner.calls()[0]
    assert acquire[acquire.index("--want") + 1] == "4"


def test_a_nested_run_clamps_to_the_inherited_grant_and_releases_nothing(inner: Inner) -> None:
    inner.env |= {"CC_GATE_SLOT_TOKEN": INHERITED, "CC_GATE_SLOT_GRANT": "1"}
    rc, out = inner.run("-n", "6")
    assert rc == 0, out
    assert _workers(out) == {"gw0"}, out
    assert [call[0] for call in inner.calls()] == ["acquire"]


@pytest.mark.parametrize("args", [(), ("-n", "0"), ("-n", "1"), ("--collect-only", "-n", "4")])
def test_serial_and_collect_only_runs_ask_for_nothing(inner: Inner, args: tuple[str, ...]) -> None:
    rc, out = inner.run(*args)
    assert rc == 0, out
    assert inner.calls() == []


def test_a_worker_process_asks_for_nothing(inner: Inner) -> None:
    inner.env["PYTEST_XDIST_WORKER"] = "gw0"
    rc, out = inner.run("-n", "2")
    assert rc == 0, out
    assert _workers(out) == {"gw0", "gw1"}, out
    assert inner.calls() == []


def test_no_helper_leaves_the_run_untouched(inner: Inner, tmp_path: Path) -> None:
    del inner.env["CC_GATE_SLOT_BIN"]
    inner.env |= {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path / "home")}
    rc, out = inner.run("-n", "2")
    assert rc == 0, out
    assert _workers(out) == {"gw0", "gw1"}, out
    assert "gate slot" not in out
    assert inner.calls() == []


def test_a_usage_error_from_the_helper_runs_uncapped_and_green(inner: Inner) -> None:
    inner.env |= {"STUB_MODE": "rc", "STUB_RC": "64"}
    rc, out = inner.run("-n", "2")
    assert rc == 0, out
    assert _workers(out) == {"gw0", "gw1"}, out
    assert out.count("gate slot: cc-gate-slot acquire exited 64; running 2 workers uncapped") == 1, out
    assert [call[0] for call in inner.calls()] == ["acquire"]


def test_a_helper_killed_while_queued_ends_the_run_with_its_code(inner: Inner) -> None:
    inner.env |= {"STUB_MODE": "rc", "STUB_RC": "143"}
    rc, out = inner.run("-n", "2")
    assert rc == 143, out
    assert "gate slot: killed while queued" in out
    assert not _passed(out), out
    assert _workers(out) == set(), out


def test_a_run_cut_while_queued_never_reads_green(inner: Inner) -> None:
    """``timeout 2 pytest -n 3`` against a helper that queues forever (review C6): ``timeout`` signals its
    whole process group. The signal is sent once the stub reports itself queued, not at a fixed 2 s, so a
    slow start under load cannot cut the run before it reaches the queue and pass this case vacuously."""
    inner.env["STUB_MODE"] = "forever"
    proc = inner.start("-n", "3")
    with _reaped(proc):
        _wait_for(Path(f"{inner.log}.queued"), proc)
        time.sleep(2)
        os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=CEILING_S)
    assert proc.returncode != 0, out
    assert not _passed(out), out


def test_an_interrupt_while_queued_terminates_the_helper(inner: Inner) -> None:
    """Review M5: on Ctrl-C the plugin TERMs the helper and waits for it, so the CLI's trap runs (CPython's
    own path would SIGKILL it 0.25 s later). The interrupt goes to pytest alone, not to its group."""
    inner.env["STUB_MODE"] = "forever"
    proc = inner.start("-n", "3")
    with _reaped(proc):
        _wait_for(Path(f"{inner.log}.queued"), proc)
        os.kill(proc.pid, signal.SIGINT)
        out, _ = proc.communicate(timeout=CEILING_S)
    assert Path(f"{inner.log}.term").exists(), out
    assert proc.returncode != 0, out
    assert not _passed(out), out
