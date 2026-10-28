"""scripts/ship-land.sh against a bare remote, with a stub land gate and a stub gh."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import exec_stub

REPO = Path(__file__).resolve().parents[1]
SHIP_LAND = REPO / "scripts" / "ship-land.sh"

STUB_GATE = '#!/bin/bash\necho ran >>"$STUB_GATE_LOG"\nexit "${STUB_GATE_RC:-0}"\n'
# Every origin/main run is red: `run list` names run 101, `run watch` fails, `run view` says failure.
STUB_GH = """case "$1 $2" in
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
    exec_stub.write(gh, f'#!/bin/bash\necho "$*" >>"{tmp_path / "gh.log"}"\n{body}')  # a Mach-O (B13 r23 A97)
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
    env = _with_gh(tmp_path, env, STUB_GH)  # a Mach-O, not a #! exec (B13 round 24 B91)
    before = _git(remote, "rev-parse", "main")
    cp = _land(clone, env)
    assert cp.returncode == 6
    assert "run 101" in cp.stderr
    assert _git(remote, "rev-parse", "main") == before
    # Allowed onto a red trunk, the push lands and the read-back then reports its own run red.
    cp = _land(clone, env, "--allow-red-trunk")
    assert cp.returncode == 8
    assert _git(remote, "rev-parse", "main") == _git(clone, "rev-parse", "HEAD")


def test_a_trunk_already_red_stops_before_the_gate(remote: Path, tmp_path: Path, env: dict[str, str]) -> None:
    """B13 round 27 P140: trunk's CI was read only after the gate, so two lanes ran 35-minute land gates
    under the shared lock on a trunk whose run had already ended red, each to exit 6. A run already ended red
    stops the land before the gate; --allow-red-trunk still gates and lands."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    gh_env = _with_gh(tmp_path, env, STUB_GH)
    cp = _land(clone, gh_env)
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert "trunk is red: run 101" in cp.stderr and "(before the gate)" in cp.stderr
    assert not (tmp_path / "gate.log").exists()
    assert "run watch" not in (tmp_path / "gh.log").read_text(), "the read before the gate never waits"
    cp = _land(clone, gh_env, "--allow-red-trunk")
    assert cp.returncode == 8, cp.stdout + cp.stderr
    assert (tmp_path / "gate.log").read_text() == "ran\n"


def test_a_cancelled_or_running_trunk_run_still_gates(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 27 P140 (P139's rules): the read before the gate stops only on a run already ended
    failure, timed_out or startup_failure. A cancelled run (perhaps superseded), one in progress, or a gh that
    fails goes on to the gate, and the check after it decides as before."""
    for n, view in enumerate(("completed cancelled", "in_progress", "")):
        clone = _clone(remote, tmp_path / f"c{n}", env, f"c{n}")
        log = tmp_path / "gate.log"
        log.unlink(missing_ok=True)
        answer = f"echo {view}" if view else "exit 1"
        body = (
            'case "$1 $2" in\n  "run list") echo 101 ;;\n  "run watch") exit 1 ;;\n'
            f'  "run view") {answer} ;;\nesac\n'
        )
        cp = _land(clone, _with_gh(tmp_path, env, body))
        assert cp.returncode != 0 and "(before the gate)" not in cp.stderr, cp.stdout + cp.stderr
        assert log.read_text() == "ran\n", view


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


def _ci_listed_late(trunk: str, ci_run: str) -> str:
    """A gh whose pushed sha lists a green diagrams run (202) and, for ``--workflow ci``, ``ci_run`` (none if
    empty); run 303 is red. Trunk's run 101 is green either way, to a watch and to the early read."""
    return (
        'case "$1 $2" in\n'
        '  "run list")\n'
        f'    if [ "$4" = {trunk} ]; then echo 101\n'
        f'    elif [ "$5 $6" = "--workflow ci" ]; then [ -z "{ci_run}" ] || echo {ci_run}\n'
        "    else echo 202; fi ;;\n"
        '  "run watch") [ "$3" != 303 ] ;;\n'
        '  "run view") if [ "$3" = 303 ]; then echo completed failure; else echo completed success; fi ;;\n'
        "esac\n"
    )


def test_a_diagrams_run_listed_before_ci_is_not_read_as_ci_green(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 23: the read-back took the first run list that named any run, and diagrams is often listed
    before ci, so a land printed "CI green" from the diagrams run alone. The list counts only once it holds a
    ci run: with none it is not read (exit 9), and a red ci run beside a green diagrams run is red
    (exit 8)."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    cp = _land(clone, _with_gh(tmp_path, env, _ci_listed_late(_git(remote, "rev-parse", "main"), "")))
    sha = _git(clone, "rev-parse", "HEAD")
    assert cp.returncode == 9, cp.stdout + cp.stderr
    assert f"LANDED {sha[:8]}, CI NOT READ: no CI run for {sha[:8]}" in cp.stdout
    assert "CI green" not in cp.stdout

    other = _clone(remote, tmp_path / "b", env, "two")
    cp = _land(other, _with_gh(tmp_path, env, _ci_listed_late(_git(remote, "rev-parse", "main"), "303")))
    sha = _git(other, "rev-parse", "HEAD")
    assert cp.returncode == 8, cp.stdout + cp.stderr
    assert f"CI RED on {sha[:8]}: run 303" in cp.stderr


def test_no_ci_skips_every_ci_read_as_the_header_says(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 28 I80: round 27 P140 added a third CI read, trunk's runs before the gate, and `--no-ci`
    skips it too, but the header still said "skip both CI reads". It names all three, and a red trunk's gh is
    never called under `--no-ci`."""
    header = SHIP_LAND.read_text(encoding="utf-8").split("\nset -euo pipefail\n", 1)[0]
    [line] = [line for line in header.splitlines() if "--no-ci " in line]
    assert (
        "skip every CI read (trunk's runs before the gate and after it, the read-back after the push)" in line
    )
    assert "both CI reads" not in SHIP_LAND.read_text(encoding="utf-8")
    assert "either CI read" not in SHIP_LAND.read_text(encoding="utf-8")
    clone = _clone(remote, tmp_path / "a", env, "one")
    cp = _land(clone, _with_gh(tmp_path, env, STUB_GH), "--no-ci")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not (tmp_path / "gh.log").exists(), "no CI read under --no-ci"


# A trunk run that ended failure because its hosted runner was lost: `run view --json jobs` answers the
# classifier's `lost`, every other view `completed failure` (B13 round 31 C569; a made-up run id).
STUB_GH_LOST = """case "$1 $2" in
  "run list") echo 101 ;;
  "run watch") exit 1 ;;
  "run view") if [ "$5" = jobs ]; then echo lost; else echo completed failure; fi ;;
esac
"""


def test_a_trunk_run_that_lost_its_runner_is_not_read_and_never_landed_past(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 31 C569: run 40523387625's first attempt ended failure with no step failed (the hosted runner
    lost communication), both CI readers took it for a red trunk (exit 6), and a lane landed past it with
    --allow-red-trunk. A lost runner is not read: exit 9 before the gate, nothing pushed, the rerun named,
    with --allow-red-trunk too; a failure the classifier does not call lost, or cannot read, stays red."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    before = _git(remote, "rev-parse", "main")
    for args in ((), ("--allow-red-trunk",)):
        cp = _land(clone, _with_gh(tmp_path, env, STUB_GH_LOST), *args)
        assert cp.returncode == 9, cp.stdout + cp.stderr
        assert "trunk CI not read: run 101 lost its hosted runner" in cp.stderr
        assert "gh run rerun 101 --failed" in cp.stderr and "nothing pushed (before the gate)" in cp.stderr
        assert "--no-ci" not in cp.stderr and "--allow-red-trunk" not in cp.stderr
        assert not (tmp_path / "gate.log").exists()
        assert _git(remote, "rev-parse", "main") == before
    # the jobs read fails or answers anything else: red, as before (fail closed)
    for jobs in ("exit 1", "echo red", "echo"):
        body = (
            'case "$1 $2" in\n  "run list") echo 101 ;;\n  "run watch") exit 1 ;;\n'
            f'  "run view") if [ "$5" = jobs ]; then {jobs}; else echo completed failure; fi ;;\nesac\n'
        )
        cp = _land(clone, _with_gh(tmp_path, env, body))
        assert cp.returncode == 6 and "trunk is red: run 101" in cp.stderr, jobs


def test_a_run_that_loses_its_runner_during_the_gate_is_rerun_not_landed_past(
    remote: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """B13 round 31 C569: the read after the gate takes the same classifier. Trunk's run is in progress before
    the gate and lost its runner after it: exit 9, nothing pushed, the rerun named and no --no-ci offered,
    under --allow-red-trunk too. The pushed sha's own run that lost its runner is LANDED, CI not read."""
    clone = _clone(remote, tmp_path / "a", env, "one")
    before = _git(remote, "rev-parse", "main")
    gate = tmp_path / "gate.log"
    body = (
        'case "$1 $2" in\n  "run list") echo 101 ;;\n  "run watch") exit 1 ;;\n'
        f'  "run view") if [ "$5" = jobs ]; then echo lost; elif [ -f "{gate}" ];'
        " then echo completed failure;"
        " else echo in_progress; fi ;;\nesac\n"
    )
    for args in ((), ("--allow-red-trunk",)):
        gate.unlink(missing_ok=True)
        cp = _land(clone, _with_gh(tmp_path, env, body), *args)
        assert cp.returncode == 9, cp.stdout + cp.stderr
        assert (
            "trunk CI not read: run 101 lost its hosted runner" in cp.stderr and "nothing pushed" in cp.stderr
        )
        assert "gh run rerun 101 --failed" in cp.stderr and "--no-ci" not in cp.stderr
        assert gate.read_text() == "ran\n"
        assert _git(remote, "rev-parse", "main") == before

    trunk = _git(remote, "rev-parse", "main")
    body = (
        'case "$1 $2" in\n'
        f'  "run list") if [ "$4" = {trunk} ]; then echo 101; else echo 303; fi ;;\n'
        '  "run watch") [ "$3" != 303 ] ;;\n'
        '  "run view") if [ "$5" = jobs ]; then echo lost; elif [ "$3" = 303 ]; then echo completed failure;'
        " else echo completed success; fi ;;\n"
        "esac\n"
    )
    cp = _land(clone, _with_gh(tmp_path, env, body))
    sha = _git(clone, "rev-parse", "HEAD")
    assert cp.returncode == 9, cp.stdout + cp.stderr
    assert f"LANDED {sha[:8]}, CI NOT READ: run 303 lost its hosted runner" in cp.stdout
    assert "CI RED" not in cp.stderr and _git(remote, "rev-parse", "main") == sha


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq to run ship-land's own classifier")
def test_the_lost_runner_classifier_calls_lost_only_a_failed_job_left_unfinished() -> None:
    """B13 round 31 C569: run_lost's jq, run over job lists shaped as `gh run view --json jobs` gives them:
    lost only when every failed job has no failed step and a step left uncompleted (run 40523387625's first
    attempt); a failed step, a job that timed out with its step cancelled, or no failed job is red."""
    [program] = re.findall(r"--json jobs --jq '([^']*)'", SHIP_LAND.read_text(encoding="utf-8"))

    def step(status: str, conclusion: str | None) -> dict[str, object]:
        return {"name": "s", "status": status, "conclusion": conclusion}

    def verdict(*jobs: tuple[str, list[dict[str, object]]]) -> str:
        data = {"jobs": [{"conclusion": c, "steps": steps} for c, steps in jobs]}
        cp = subprocess.run(
            ["jq", "-r", program], input=json.dumps(data), capture_output=True, text=True, check=True
        )
        return cp.stdout.strip()

    done = step("completed", "success")
    assert verdict(("failure", [done, step("in_progress", None), step("pending", None)])) == "lost"
    assert verdict(("failure", [done, step("completed", "failure")])) == "red"
    assert verdict(("failure", [done, step("completed", "cancelled")])) == "red"
    assert verdict(("success", [done]), ("failure", [step("completed", "failure")])) == "red"
    assert verdict(("success", [done])) == "red"


def test_claude_md_and_the_lane_brief_say_when_allow_red_trunk_is_right() -> None:
    """B13 round 31 C569: CLAUDE.md and the lane brief gave no rule for --allow-red-trunk, and a lane landed
    past a trunk run that had only lost its runner. Both say it is for a run red on tests the diff does not
    touch and the lane does not own, with the run id recorded, never for exit 9, and that a lost runner is
    rerun; the script's header says exit 9 covers it."""
    guide = " ".join((REPO / "CLAUDE.md").read_text(encoding="utf-8").split())
    lanes = " ".join((REPO / "docs" / "lanes" / "COMMON.md").read_text(encoding="utf-8").split())
    assert (
        "Exit 9 is CI it could not read (gh failed, no run appeared, or a run lost its hosted runner)"
        in guide
    )
    assert (
        "is rerun, `gh run rerun <id> --failed`, and the land run again once the rerun is green: it is never"
        " landed past, so neither `--allow-red-trunk` nor `--no-ci` is for it." in guide
    )
    assert (
        "`--allow-red-trunk` is only for a trunk run that ended red on tests"
        " (`gh run view <id> --log-failed`) the"
        " diff does not touch and the lane does not own: record the run id" in guide
    )
    assert (
        "Pass `--allow-red-trunk` only for a run that ended red on tests your diff does not touch and your"
        " lane does not own, with the run id in your commit body or result note and the owner messaged; never"
        " for exit 9: a run that lost its hosted runner is rerun (`gh run rerun <id> --failed`) and waited"
        " for, never landed"
        " past (round 31 C569)." in lanes
    )
    header = SHIP_LAND.read_text(encoding="utf-8").split("\nset -euo pipefail\n", 1)[0]
    assert "9 CI not read (gh failed, no run appeared, or a run lost its hosted runner)" in header
