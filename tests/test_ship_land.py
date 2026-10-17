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
  "run view") echo failure ;;
esac
"""


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
