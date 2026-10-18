"""tests/_gate_journal.py, the gate's pytest plugin, run on a suite of its own in a subprocess."""

from __future__ import annotations

import json
import os
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
