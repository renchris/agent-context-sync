"""Opt-in performance budget: a no-op cycle costs at most 3 s per 100k local files on this Mac.

Slow (it first syncs the whole generated tree), so it runs only with ``AGENTSYNC_PERF=1``.  The tree size is
``AGENTSYNC_PERF_FILES`` (default 100000).  For a tree smaller than 100k the budget is the 100k budget (3 s),
since the cycle's fixed costs (git subprocesses, the scaffold, curation) do not shrink with the tree; above
100k it scales linearly.  The measured value is the minimum over ``AGENTSYNC_PERF_RUNS`` (default 3) no-op
cycles after one warm-up, so a briefly loaded machine does not fail the budget; a machine that is loaded for
the whole run can (the message prints the load average and the CPU time, which load does not inflate).
"""

from __future__ import annotations

import os
import resource
import subprocess
import time
from pathlib import Path

import pytest

from agentsync.config import parse_config
from agentsync.cycle import run_cycle
from agentsync.model import CycleMode, Verdict
from test_e2e import clock

slow = pytest.mark.skipif(
    os.environ.get("AGENTSYNC_PERF") != "1", reason="set AGENTSYNC_PERF=1 to run the performance budget"
)

BUDGET_S_PER_100K = 3.0
FILES = int(os.environ.get("AGENTSYNC_PERF_FILES", "100000"))
RUNS = int(os.environ.get("AGENTSYNC_PERF_RUNS", "3"))


def _tree(root: Path, n: int) -> None:
    """``n`` small text files, 100 per directory, 100 directories per parent (like a synced library)."""
    for i in range(n):
        d = root / f"d{i // 10_000:03d}" / f"e{i // 100:05d}"
        if i % 100 == 0:
            d.mkdir(parents=True, exist_ok=True)
        (d / f"file-{i:06d}.txt").write_text(f"hello {i}\n", encoding="utf-8")
    (root / "README.txt").write_text("sentinel\n", encoding="utf-8")


def _cpu() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


@slow
def test_noop_cycle_costs_at_most_3s_per_100k_files(tmp_path: Path) -> None:
    source, repo, state = tmp_path / "source", tmp_path / "agent-context" / "docs", tmp_path / "state"
    _tree(source, FILES)
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    state.mkdir(mode=0o700)
    text = f"""
[agentsync]
docs_repo = "{repo}"
state_dir = "{state}"
cache_dir = "{tmp_path / "cache"}"
log_dir = "{state / "logs"}"

[[source]]
id = "perf"
kind = "local"
path = "{source}"
sentinel = "README.txt"
max_materialise_bytes = "10GB"
max_files = 10000000
"""
    config = parse_config(text, config_path=tmp_path / "agent-context" / "sources.toml")
    started = time.perf_counter()
    first = run_cycle(config, mode=CycleMode.POLL, now=clock)
    initial_s = time.perf_counter() - started
    assert first.exit_code == 0 and first.commit_sha is not None, first
    warm = run_cycle(config, mode=CycleMode.POLL, now=clock)
    assert warm.exit_code == 0, warm
    walls: list[float] = []
    cpus: list[float] = []
    for _ in range(RUNS):
        cpu0, t0 = _cpu(), time.perf_counter()
        report = run_cycle(config, mode=CycleMode.POLL, now=clock)
        walls.append(time.perf_counter() - t0)
        cpus.append(_cpu() - cpu0)
        assert report.exit_code == 0 and report.commit_sha is None, report  # a no-op commits nothing
        assert dict(report.sources[0].counts) == {Verdict.UNCHANGED: FILES + 1}
    budget = BUDGET_S_PER_100K * max(1.0, FILES / 100_000)
    wall, cpu = min(walls), min(cpus)
    summary = (
        f"{FILES} files: initial sync {initial_s:.1f}s; no-op min wall {wall:.3f}s (runs "
        f"{', '.join(f'{w:.3f}' for w in walls)}), min CPU incl. git {cpu:.3f}s; "
        f"per 100k {wall * 100_000 / FILES:.2f}s wall; budget {budget:.1f}s; load {os.getloadavg()}"
    )
    print(summary)
    assert wall <= budget, summary
