"""tests/_gate_journal.py, the gate's pytest plugin, run on a suite of its own in a subprocess."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import _gate_journal
import exec_stub

SUITE = """
import os
import sys


def test_passes():
    assert True


def test_fails_with_its_reason_on_stderr():
    print("contoso step 4 stopped: the lock was held", file=sys.stderr)
    assert 1 + 1 == 3, "the sum the gate read"


def test_takes_its_worker_down():
    os._exit(1)
"""


def _journal(tmp_path: Path, *args: str) -> list[dict[str, object]]:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_sample.py").write_text(SUITE, encoding="utf-8")
    journal = tmp_path / "journal.jsonl"
    env = {
        **os.environ,
        "GATE_JOURNAL": str(journal),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(_gate_journal.__file__).parent),
    }
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "_gate_journal", *args, "."],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


def test_the_journal_keeps_a_failures_text_and_a_lost_workers_error(tmp_path: Path) -> None:
    """B13 round 16 T90: lane E's pre-gate flake was an xdist worker crash, recorded as `when: "???"`, and
    the journal kept only each test's outcome, so neither it nor round 14's SIGHUP flake could be read once
    the gate's output was gone. A failed test's line now keeps the end of its report and of its stderr, and
    a worker that goes down appends the error xdist gives, the only text a crash leaves; resume still reads
    the same records (``_last``)."""
    records = _journal(tmp_path, "-n", "2")
    by_id = {r["nodeid"]: r for r in records if "nodeid" in r}
    failed = by_id["test_sample.py::test_fails_with_its_reason_on_stderr"]
    assert failed["outcome"] == "failed" and "the sum the gate read" in str(failed["tail"])
    assert len(str(failed["tail"]).splitlines()) <= _gate_journal._TAIL_LINES
    assert "contoso step 4 stopped: the lock was held" in str(failed["stderr"])
    passed = by_id["test_sample.py::test_passes"]
    assert passed["outcome"] == "passed" and "tail" not in passed and "stderr" not in passed
    crashed = by_id["test_sample.py::test_takes_its_worker_down"]
    assert (crashed["when"], crashed["outcome"]) == ("???", "failed")
    downs = [r for r in records if "node_down" in r]
    assert downs and all(str(r["node_down"]).startswith("gw") and r["error"] for r in downs), records
    last, summary = _gate_journal._last(tmp_path / "journal.jsonl")
    assert summary and set(last) == set(by_id), "the new records change nothing a resume reads"


def test_the_journal_loads_without_xdist(tmp_path: Path) -> None:
    """The worker-down hook is xdist's: a run without xdist loads the plugin and journals as before."""
    records = _journal(tmp_path, "-p", "no:xdist", "-k", "not worker_down")
    assert [r.get("outcome") for r in records if "nodeid" in r] == ["passed", "failed"]
    assert "summary" in records[-1] and not [r for r in records if "node_down" in r]


FAKE_UV = """#!/bin/sh
echo "contoso part: collected 3 items"
echo "E   the whole traceback, past the journal's tail" >&2
exit "${FAKE_RC:-0}"
"""


def _lane_part(tmp_path: Path, rc: int) -> tuple[int, Path]:
    """scripts/gate.sh's ``run_part``, taken from the script, run in lane mode over a stand-in ``uv`` that
    prints a line to each stream and exits ``rc``; the journal CLI answers as for a fresh or killed run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exec_stub.write(bin_dir / "uv", FAKE_UV)  # a Mach-O run by path, not a #! exec (B13 round 23 A97)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    harness = (
        "set -euo pipefail\n"
        f"mode=lane common='{tmp_path}' tree=t0 salt=s0 tmp='{tmp_path}/tmp' resumed=0 skipped_timing=0\n"
        'step() { echo "== $*"; "$@"; }\n'
        "journal_cli() { case $1 in finished) return 1 ;; skipped-timing) echo 0 ;; *) : ;; esac; }\n"
        f"{_functions('part_stopped', 'rerun_alone', 'part_once', 'run_part')}\n"
        "rc=0; run_part lane -n auto tests || rc=$?; exit $rc\n"
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_RC": str(rc), "HOME": str(tmp_path)}
    done = subprocess.run(
        ["bash", "-c", harness], env=env, capture_output=True, text=True, check=False, timeout=60
    )
    return done.returncode, tmp_path / "gate-journal" / "t0-s0.lane.out"


def test_a_lane_part_that_fails_keeps_its_whole_output_beside_its_journal(tmp_path: Path) -> None:
    """B13 round 17 U106: in the lane modes pytest's output went only to the caller's terminal, and the
    journal keeps a failure's text cut to its last lines, so a crash or a long traceback was lost once the
    terminal was. A lane part's output, stderr too, goes to <tree>-<salt>.<part>.out beside its journal: a
    failed part keeps it, a resumed one appends to the killed run's text, and a green part leaves none."""
    rc, out = _lane_part(tmp_path, 1)
    assert rc == 1, "the part's exit status is pytest's, through the tee"
    text = out.read_text(encoding="utf-8")
    assert "contoso part: collected 3 items" in text
    assert "the whole traceback, past the journal's tail" in text, "stderr too"
    (tmp_path / "gate-journal" / "t0-s0.lane.jsonl").write_text('{"nodeid": "x", "outcome": "passed"}\n')
    rc, out = _lane_part(tmp_path, 1)
    again = out.read_text(encoding="utf-8")
    assert rc == 1 and again.startswith(text) and "== resumed by " in again, "a resume keeps the killed run's"
    rc, out = _lane_part(tmp_path, 0)
    assert rc == 0 and not out.exists(), "a green part leaves no log"


def _functions(*names: str) -> str:
    """The named functions of scripts/gate.sh, as text."""
    script = (Path(__file__).parents[1] / "scripts" / "gate.sh").read_text(encoding="utf-8")
    parts = []
    for name in names:
        found = re.search(rf"(?ms)^{name}\(\) \{{.*?^\}}$", script)
        assert found is not None, f"gate.sh's {name}"
        parts.append(found.group(0))
    return "\n".join(parts)


def _gate_functions(*names: str) -> str:
    """The named functions of scripts/gate.sh, and its top-level ``trap`` and ``SCAN`` lines, as text."""
    script = (Path(__file__).parents[1] / "scripts" / "gate.sh").read_text(encoding="utf-8")
    parts = [ln for ln in script.splitlines() if ln.startswith(("trap ", "SCAN=", "SCAN_IGNORE="))]
    return "\n".join([*parts, _functions(*names)])


def test_a_gate_a_signal_ends_logs_a_failed_verdict(tmp_path: Path) -> None:
    """B13 round 18 V93: under /bin/bash 3.2 a gate.sh that SIGTERM ended while a pipeline ran entered its
    EXIT trap with ``$?`` 0, so gate-verdicts.log read a killed run as green (lane B's pre-gate of round 17:
    rc=0 over a journal with no summary line and a failed test). A signal now exits with its own status."""
    harness = (
        "set -euo pipefail\n"
        f"mode=lane tree=t0 salt=s0 skipped_timing=0 resumed=0 common='{tmp_path}' tmp='{tmp_path}/tmp'\n"
        f"mkdir -p '{tmp_path}/tmp'\n"
        f"{_gate_functions('finish')}\n"
        f"echo started > '{tmp_path}/started'\n"
        "sleep 30 | cat\n"
    )
    bash = "/bin/bash" if Path("/bin/bash").exists() else "bash"
    proc = subprocess.Popen([bash, "-c", harness], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if (tmp_path / "started").exists():
                break
            subprocess.run(["sleep", "0.05"], check=True)
        proc.terminate()
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    [verdict] = (tmp_path / "gate-verdicts.log").read_text(encoding="utf-8").splitlines()
    assert " rc=143 " in verdict, verdict


FAKE_COLLECT = """#!/bin/sh
# a stand-in `uv run --locked pytest -q -p no:cacheprovider --collect-only ARGS`: ids by what ARGS select
shift 6
all="tests/test_contracts.py::a tests/test_lints.py::b tests/test_deploy_pack.py::c tests/test_cycle.py::d"
for id in $all; do
  file=${id%%::*}
  if [ $# -eq 0 ]; then echo "$id"; continue; fi
  picked=no named=no
  for a in "$@"; do
    case $a in
      --ignore=*) [ "${a#--ignore=}" = "$file" ] && picked=out ;;
      tests/*) named=yes; [ "$a" = "$file" ] && [ "$picked" != out ] && picked=yes ;;
    esac
  done
  if [ "$named" = yes ]; then [ "$picked" = yes ] && echo "$id"; else [ "$picked" != out ] && echo "$id"; fi
done
[ -n "${FAKE_TWICE:-}" ] && [ "$*" = "${FAKE_TWICE}" ] && echo "tests/test_cycle.py::d"
exit 0
"""


def test_the_scanners_run_first_and_every_test_runs_in_one_part(tmp_path: Path) -> None:
    """B13 round 18 V93: all three red land gates of round 17 failed in tests/test_contracts.py, which ran in
    the last part, after about 25 minutes of the rest, so each held the shared land lock that long. The
    cheap whole-tree scanners now run first, and the union check takes three parts: their ids union to the
    full set's and their counts add up to its count, so no test is run twice or not at all."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(FAKE_COLLECT, encoding="utf-8")
    (bin_dir / "uv").chmod(0o755)
    (tmp_path / "tmp").mkdir()
    harness = (
        "set -euo pipefail\n"
        f"tmp='{tmp_path}/tmp'\n"
        'pytest_q() { uv run --locked pytest -q -p no:cacheprovider "$@"; }\n'
        'run_part() { echo "PART $*"; }\n'
        "finish() { :; }\n"
        f"{_gate_functions('gate_n', 'union_check', 'full_set')}\n"
        "full_set\n"
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}

    def gate(**extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", harness],
            env={**env, **extra},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    done = gate()
    assert done.returncode == 0, done.stderr
    parts = [ln.split()[1] for ln in done.stdout.splitlines() if ln.startswith("PART ")]
    assert parts == ["scan", "deploy_pack", "rest"], done.stdout
    assert "== collect: full 4, scan 2, deploy_pack 1, rest 1" in done.stdout
    twice = gate(FAKE_TWICE="--collect-only tests/test_contracts.py tests/test_lints.py")
    assert twice.returncode == 1 and "each test once" in twice.stderr, (twice.stdout, twice.stderr)


# B13 round 33 C800: scripts/gate.sh's run_part over a real pytest and the real journal plugin, run on a suite
# of its own. `uv run --locked pytest ARGS` is a stand-in that runs this Python's pytest. Each test in a suite
# appends its name to $RERUN_RUNS when it runs, so a test reads how many times each ran; FLAKY fails on its
# first run only.
PART_UV = """#!/bin/sh
shift 2
exec "$PART_PYTHON" -m "$@"
"""
RUNS = """
import os
from pathlib import Path

RUNS = Path(os.environ["RERUN_RUNS"])


def ran(name):
    \"\"\"How many times ``name`` ran before this run, which it records.\"\"\"
    before = RUNS.read_text().split().count(name) if RUNS.exists() else 0
    with RUNS.open("a") as f:
        f.write(name + "\\n")
    return before
"""
FLAKY = """
import pytest
from runs import ran


@pytest.mark.parametrize("i", range({n}))
def test_flaky(i):
    assert ran(f"flaky{{i}}"), "the contoso import timed out under load"
"""


def _rerun_part(
    tmp_path: Path, suite: dict[str, str], *args: str, mode: str = "land"
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    """run_part (with finish as its EXIT trap) over ``suite`` in ``mode``: the result, each test's runs, and
    the gate-verdicts.log line."""
    work = tmp_path / "suite"
    work.mkdir()
    (work / "runs.py").write_text(RUNS, encoding="utf-8")
    for name, text in suite.items():
        (work / name).write_text(text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exec_stub.write(bin_dir / "uv", PART_UV)
    (tmp_path / "tmp").mkdir()
    journal_py = Path(_gate_journal.__file__)
    harness = (
        "set -euo pipefail\n"
        f"mode={mode} tree=t0 salt=s0 skipped_timing=0 resumed=0 rerun_passed=0 common='{tmp_path}'"
        f" tmp='{tmp_path}/tmp'\n"
        'step() { echo "== $*"; "$@"; }\n'
        f'journal_cli() {{ "$PART_PYTHON" \'{journal_py}\' "$@"; }}\n'
        f"{_functions('finish', 'part_stopped', 'rerun_alone', 'part_once', 'run_part')}\n"
        "trap finish EXIT\n"
        f"run_part rest {' '.join(args)}\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PART_PYTHON": sys.executable,
        "PYTHONPATH": f"{journal_py.parent}:{work}",
        "RERUN_RUNS": str(tmp_path / "runs"),
    }
    done = subprocess.run(
        ["bash", "-c", harness], cwd=work, env=env, capture_output=True, text=True, check=False, timeout=600
    )
    runs = (tmp_path / "runs").read_text(encoding="utf-8").split() if (tmp_path / "runs").exists() else []
    [verdict] = (tmp_path / "gate-verdicts.log").read_text(encoding="utf-8").splitlines()
    return done, runs, verdict


@pytest.mark.parametrize("n", [1, 20])
def test_a_part_whose_tests_fail_under_load_and_pass_alone_is_green(tmp_path: Path, n: int) -> None:
    """B13 round 33 C800: every land gate from 2026-11-01 went red on 1 to 9 tests that passed alone, a
    different set each run, at the load the machine runs at by design. A part pytest ends 1 with 1 to 20
    failed tests re-runs exactly those once, alone, and is green when they pass; the verdict counts them."""
    done, runs, verdict = _rerun_part(tmp_path, {"test_s.py": FLAKY.format(n=n)}, "-n", "2", ".")
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(runs) == sorted([f"flaky{i}" for i in range(n)] * 2), "each failed test ran once more"
    ids = " ".join(f"test_s.py::test_flaky[{i}]" for i in range(n))
    assert f"== rest: {n} failed under load and passed alone: " in done.stdout
    assert all(f"test_s.py::test_flaky[{i}]" in done.stdout for i in range(n)), ids
    assert " -n 0 test_s.py::test_flaky[" in done.stdout, "alone, by node id"
    assert " rc=0 " in verdict and verdict.endswith(f" rerun_passed={n}"), verdict


def test_a_test_that_fails_alone_too_convicts(tmp_path: Path) -> None:
    """C800: the re-run is a second chance, not a pass: a test that fails alone leaves the part red."""
    suite = "from runs import ran\n\n\ndef test_always():\n    ran('always')\n    assert False\n"
    done, runs, verdict = _rerun_part(tmp_path, {"test_s.py": suite}, "-n", "2", ".")
    assert done.returncode == 1, done.stdout + done.stderr
    assert runs == ["always", "always"]
    assert "== rest: the re-run alone is red too (rc 1)" in done.stdout and "passed alone" not in done.stdout
    assert " rc=1 " in verdict and verdict.endswith(" rerun_passed=0"), verdict


def test_more_than_twenty_failures_are_not_re_run(tmp_path: Path) -> None:
    """C800: 21 failed tests are a real break, not load: the part is red with each run once."""
    done, runs, verdict = _rerun_part(tmp_path, {"test_s.py": FLAKY.format(n=21)}, "-n", "2", ".")
    assert done.returncode == 1, done.stdout + done.stderr
    assert sorted(runs) == sorted(f"flaky{i}" for i in range(21))
    assert "== rest: 21 failed; only 1 to 20 are re-run" in done.stdout
    assert " -n 0 " not in done.stdout and verdict.endswith(" rerun_passed=0")


EXIT_2 = FLAKY.format(n=1) + "\n\ndef test_interrupts():\n    raise KeyboardInterrupt\n"
EXIT_3 = FLAKY.format(n=1)
EXIT_3_CONFTEST = """
def pytest_runtest_logfinish(nodeid, location):
    raise RuntimeError("contoso plugin bug")
"""


@pytest.mark.parametrize(
    ("rc", "suite"), [(2, {"test_s.py": EXIT_2}), (3, {"test_s.py": EXIT_3, "conftest.py": EXIT_3_CONFTEST})]
)
def test_a_part_that_ends_other_than_1_is_not_re_run(tmp_path: Path, rc: int, suite: dict[str, str]) -> None:
    """C800: only exit 1 (tests failed) has a failed set to re-run; an interrupted run (2) or an internal
    error (3) is red as before, though its journal names a failed test."""
    done, runs, verdict = _rerun_part(tmp_path, suite, "-p", "no:xdist", ".")
    assert done.returncode == rc, done.stdout + done.stderr
    assert runs == ["flaky0"] and "re-run" not in done.stdout and " -n 0 " not in done.stdout
    assert f" rc={rc} " in verdict and verdict.endswith(" rerun_passed=0")


def test_a_lost_workers_test_is_re_run(tmp_path: Path) -> None:
    """C800: one round 33 land gate lost an xdist worker to memory. xdist reports the test that worker was
    running as failed (``when: "???"``), so the journal's failed set names it and it runs again alone."""
    suite = "import os\nfrom runs import ran\n\n\ndef test_lost():\n"
    suite += "    if not ran('lost'):\n        os._exit(1)\n"
    done, runs, verdict = _rerun_part(tmp_path, {"test_s.py": suite}, "-n", "2", ".")
    assert done.returncode == 0, done.stdout + done.stderr
    assert runs == ["lost", "lost"]
    assert "== rest: 1 failed under load and passed alone: test_s.py::test_lost" in done.stdout
    assert verdict.endswith(" rerun_passed=1"), verdict


def test_a_lane_parts_re_run_journals_beside_the_parts_and_a_green_one_drops_the_log(tmp_path: Path) -> None:
    """C800 in the lane modes: the re-run journals to <tree>-<salt>.<part>.rerun.jsonl, never into the part's
    journal, so a resume of a killed run reads the part's own records; a green re-run removes the .out as a
    green part does."""
    done, _runs, verdict = _rerun_part(
        tmp_path, {"test_s.py": FLAKY.format(n=1)}, "-n", "2", ".", mode="lane"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    jdir = tmp_path / "gate-journal"
    last, summary = _gate_journal._last(jdir / "t0-s0.rest.jsonl")
    assert summary and last["test_s.py::test_flaky[0]"]["outcome"] == "failed", "the part's own record"
    again, _ = _gate_journal._last(jdir / "t0-s0.rest.rerun.jsonl")
    assert again["test_s.py::test_flaky[0]"]["outcome"] == "passed"
    assert not (jdir / "t0-s0.rest.out").exists() and verdict.endswith(" rerun_passed=1")


def test_the_failed_verb_reads_a_finished_journal_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C800: ``failed`` lists the nodeids whose last record failed, a lost worker's ``???`` too, and exits 1
    on a journal with no summary line, whose failed set is not known."""
    journal = tmp_path / "j.jsonl"
    lines = [
        {"nodeid": "t.py::a", "when": "call", "outcome": "failed"},
        {"nodeid": "t.py::b", "when": "call", "outcome": "passed"},
        {"nodeid": "t.py::b", "when": "teardown", "outcome": "failed"},
        {"nodeid": "t.py::c", "when": "???", "outcome": "failed"},
        {"nodeid": "t.py::d", "when": "setup", "outcome": "skipped"},
    ]
    journal.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    assert _gate_journal.main(["failed", str(journal)]) == 1
    with journal.open("a", encoding="utf-8") as f:
        f.write('{"summary": 1}\n')
    capsys.readouterr()
    assert _gate_journal.main(["failed", str(journal)]) == 0
    assert capsys.readouterr().out.split() == ["t.py::a", "t.py::b", "t.py::c"]
