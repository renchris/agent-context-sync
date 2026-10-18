"""scripts/ship-land.sh against a bare remote, with a stub land gate and a stub gh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SHIP_LAND = REPO / "scripts" / "ship-land.sh"

STUB_GATE = '#!/bin/bash\necho ran >>"$STUB_GATE_LOG"\nexit "${STUB_GATE_RC:-0}"\n'
# Every origin/main run is red: `run list` names run 101, `run watch` fails, `run view` says failure.
STUB_GH = """#!/bin/bash
case "$1 $2" in
  "run list") echo 101 ;;
  "run watch") exit 1 ;;
  "run view") echo completed failure ;;
esac
"""
# The read-backs' waits, cut for a test: 3 polls, no sleep (B13 round 15 S59, S63)
QUICK = {"SHIP_LAND_RUN_POLLS": "3", "SHIP_LAND_RUN_SLEEP": "0"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    e = dict(os.environ)
    e.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
        STUB_GATE_LOG=str(tmp_path / "gate.log"),
    )
    return e


@pytest.fixture
def remote(tmp_path: Path, env: dict[str, str]) -> Path:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "scripts").mkdir()
    shutil.copy(SHIP_LAND, seed / "scripts" / "ship-land.sh")
    (seed / "scripts" / "gate.sh").write_text(STUB_GATE)
    (seed / "scripts" / "gate.sh").chmod(0o755)
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=seed, check=True, env=env)
    subprocess.run(["git", "push", "-q", str(bare), "main"], cwd=seed, check=True, env=env)
    return bare


def _clone(remote: Path, dest: Path, env: dict[str, str], commit: str | None = None) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, env=env)
    if commit:
        (dest / f"{commit}.txt").write_text(commit)
        subprocess.run(["git", "add", "-A"], cwd=dest, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", commit], cwd=dest, check=True, env=env)
    return dest


def _with_gh(tmp_path: Path, env: dict[str, str], body: str) -> dict[str, str]:
    """``env`` with a stub gh running ``body`` first on PATH; it logs each call to ``gh.log``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/bash\necho "$*" >>"{tmp_path / "gh.log"}"\n{body}')
    gh.chmod(0o755)
    return {**env, **QUICK, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}


def _land(clone: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["scripts/ship-land.sh", *args],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_lands_head_on_main(remote: Path, tmp_path: Path, env: dict[str, str]) -> None:
    clone = _clone(remote, tmp_path / "a", env, "one")
    cp = _land(clone, env, "--no-ci")
    assert cp.returncode == 0, cp.stderr
    assert _git(remote, "rev-parse", "main") == _git(clone, "rev-parse", "HEAD")
    assert (tmp_path / "gate.log").read_text() == "ran\n"


def test_rebases_onto_a_moved_main_and_gates_the_result(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    clone = _clone(remote, tmp_path / "a", env, "one")
    other = _clone(remote, tmp_path / "b", env, "two")
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=other, check=True, env=env)
    cp = _land(clone, env, "--no-ci")
    assert cp.returncode == 0, cp.stderr
    assert _git(remote, "log", "--format=%s", "main") == "one\ntwo\nseed"


def test_a_red_gate_pushes_nothing(remote: Path, tmp_path: Path, env: dict[str, str]) -> None:
    clone = _clone(remote, tmp_path / "a", env, "one")
    before = _git(remote, "rev-parse", "main")
    cp = _land(clone, {**env, "STUB_GATE_RC": "1"}, "--no-ci")
    assert cp.returncode == 5
    assert _git(remote, "rev-parse", "main") == before


def test_a_dirty_tree_is_refused_before_the_gate(remote: Path, tmp_path: Path, env: dict[str, str]) -> None:
    clone = _clone(remote, tmp_path / "a", env, "one")
    (clone / "loose.txt").write_text("x")
    cp = _land(clone, env, "--no-ci")
    assert cp.returncode == 3
    assert not (tmp_path / "gate.log").exists()


def test_nothing_to_land_runs_no_gate(remote: Path, tmp_path: Path, env: dict[str, str]) -> None:
    clone = _clone(remote, tmp_path / "a", env)
    cp = _land(clone, env, "--no-ci")
    assert cp.returncode == 0, cp.stderr
    assert "nothing to land" in cp.stdout
    assert not (tmp_path / "gate.log").exists()


def test_a_red_trunk_stops_the_push_unless_allowed(remote: Path, tmp_path: Path, env: dict[str, str]) -> None:
    clone = _clone(remote, tmp_path / "a", env, "one")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(STUB_GH)
    (bin_dir / "gh").chmod(0o755)
    env = {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"}
    before = _git(remote, "rev-parse", "main")
    cp = _land(clone, env)
    assert cp.returncode == 6
    assert "run 101" in cp.stderr
    assert _git(remote, "rev-parse", "main") == before
    # Allowed onto a red trunk, the push lands and the read-back then reports its own run red.
    cp = _land(clone, env, "--allow-red-trunk")
    assert cp.returncode == 8
    assert _git(remote, "rev-parse", "main") == _git(clone, "rev-parse", "HEAD")


def test_a_gh_that_answers_nothing_is_ci_not_read_and_pushes_nothing(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 15 S63, S59: every gh call failing (a TLS timeout under load) read as "no CI runs" and then
    as green, so the trunk check passed and the read-back printed "LANDED <sha>, CI green" with no run read.
    Not read is its own outcome: exit 9 before the push, with nothing pushed."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    before = _git(remote, "rev-parse", "main")
    cp = _land(clone, _with_gh(tmp_path, env, 'echo "TLS handshake timeout" >&2\nexit 1\n'))
    assert cp.returncode == 9, cp.stdout + cp.stderr
    assert (
        "could not read trunk CI (gh run list failed 3 times" in cp.stderr and "nothing pushed" in cp.stderr
    )
    assert "CI green" not in cp.stdout
    assert _git(remote, "rev-parse", "main") == before
    cp = _land(clone, _with_gh(tmp_path, env, "exit 1\n"), "--allow-red-trunk")
    assert cp.returncode == 9, "not read is not red: --allow-red-trunk does not cover it"
    assert _git(remote, "rev-parse", "main") == before


def test_a_run_whose_watch_and_view_fail_is_not_read_not_red(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 15 S63, S59: `run list` answered and `run watch`/`run view` failed, and the empty conclusion
    read as red: exit 6 before the push, or 8 after it, for a run that may be green."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    before = _git(remote, "rev-parse", "main")
    body = 'case "$1 $2" in\n  "run list") echo 111 ;;\n  *) exit 1 ;;\nesac\n'
    cp = _land(clone, _with_gh(tmp_path, env, body))
    assert cp.returncode == 9, cp.stdout + cp.stderr
    assert "could not read trunk CI (gh run view 111 failed 3 times)" in cp.stderr
    assert _git(remote, "rev-parse", "main") == before


def test_a_watch_that_breaks_off_reads_the_runs_state(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 15 S63 (c): a watch that failed once read every conclusion but cancelled as red, success too.
    The run's state decides: one that completed green is green, and one still running is watched again."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    count = tmp_path / "views"
    body = (
        'case "$1 $2" in\n'
        '  "run list") echo 111 ;;\n'
        '  "run watch") exit 1 ;;\n'
        f'  "run view") echo x >>"{count}"; [ "$(wc -l <"{count}")" -gt 1 ] && echo completed success'
        " || echo in_progress ;;\n"
        "esac\n"
    )
    cp = _land(clone, _with_gh(tmp_path, env, body))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "CI green" in cp.stdout
    assert _git(remote, "rev-parse", "main") == _git(clone, "rev-parse", "HEAD")


def test_a_pushed_sha_with_no_run_says_landed_and_ci_not_read(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 15 S59: a pushed sha whose runs never appeared printed "LANDED <sha>, CI green". It says the
    push landed and that its CI was not read (exit 9), and a run still going after the bounded watches is not
    read either, never a wait without end."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    trunk = _git(remote, "rev-parse", "main")
    body = (
        'case "$1 $2" in\n'
        f'  "run list") if [ "$4" = {trunk} ]; then echo 101; fi ;;\n'
        '  "run watch") exit 0 ;;\n'
        "esac\n"
    )
    cp = _land(clone, _with_gh(tmp_path, env, body))
    sha = _git(clone, "rev-parse", "HEAD")
    assert cp.returncode == 9, cp.stdout + cp.stderr
    assert f"LANDED {sha[:8]}, CI NOT READ: no CI run for {sha[:8]} after 0 s" in cp.stdout
    assert "CI green" not in cp.stdout and _git(remote, "rev-parse", "main") == sha

    other = _clone(remote, tmp_path / "b", env, "two")
    body = (
        'case "$1 $2" in\n'
        '  "run list") echo 101 ;;\n'
        '  "run watch") exit 1 ;;\n'
        '  "run view") echo in_progress ;;\n'
        "esac\n"
    )
    cp = _land(other, _with_gh(tmp_path, env, body))
    assert cp.returncode == 9, cp.stdout + cp.stderr
    assert "could not read trunk CI (run 101 is still in_progress after 3 watches)" in cp.stderr
