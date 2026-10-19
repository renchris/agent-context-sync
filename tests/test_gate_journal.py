"""tests/_gate_journal.py, the gate's pytest plugin, run on a suite of its own in a subprocess."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import _gate_journal

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
    script = (Path(__file__).parents[1] / "scripts" / "gate.sh").read_text(encoding="utf-8")
    found = re.search(r"(?ms)^run_part\(\) \{.*?^\}$", script)
    assert found is not None, "gate.sh's run_part"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "uv").write_text(FAKE_UV, encoding="utf-8")
    (bin_dir / "uv").chmod(0o755)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    harness = (
        "set -euo pipefail\n"
        f"mode=lane common='{tmp_path}' tree=t0 salt=s0 tmp='{tmp_path}/tmp' resumed=0 skipped_timing=0\n"
        'step() { echo "== $*"; "$@"; }\n'
        "journal_cli() { case $1 in finished) return 1 ;; skipped-timing) echo 0 ;; *) : ;; esac; }\n"
        f"{found.group(0)}\n"
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
