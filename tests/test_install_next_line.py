"""KISS K01: install.sh's output holds exactly one NEXT line, even when its first sync is the real agentsync
(which ends an interactive sync with the loop's NEXT line) and fails.

The harness is test_install_oneshot's (a tmp HOME, a stub ``uv``, a stub ``launchctl``), except that the
installed ``agentsync`` runs the real one for ``init``, ``add-source`` and ``sync``; its ``sync`` then exits
80 (an earlier "Don't Allow"), so install.sh prints the whole sync output before its own NEXT line. Every
other subcommand is the oneshot stub. The last test runs the real ``status`` and ``sync`` too (KISS K02).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from test_install_oneshot import (
    BASH32,
    INSTALL_SH,
    ISSUE_LINK,
    STUB_AGENTSYNC,
    STUB_LAUNCHCTL,
    STUB_UV,
    xcrun_db,
)

REAL_THEN_80 = """#!/bin/bash
case "$1" in
  init|add-source) exec "$REAL_PYTHON" -m agentsync "$@" ;;
  sync) case " $* " in *" --help "*) ;; *) "$REAL_PYTHON" -m agentsync "$@"; exit 80 ;; esac ;;
esac
exec "$STUB_INNER" "$@"
"""


def _exe(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    stubbin = tmp_path / "stubbin"
    stubbin.mkdir()
    _exe(stubbin / "uv", STUB_UV)
    return {
        "HOME": str(home),
        "PATH": f"{stubbin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_AGENTSYNC": _exe(tmp_path / "agentsync-real-then-80", REAL_THEN_80),
        "STUB_INNER": _exe(tmp_path / "agentsync-stub", STUB_AGENTSYNC),
        "REAL_PYTHON": sys.executable,
        "AGENTSYNC_LAUNCHCTL": _exe(tmp_path / "launchctl-stub", STUB_LAUNCHCTL),
        "STUB_LC_DIR": str(tmp_path / "launchd"),
        "STUB_LC_EXITS": "0",
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.05",
        "STUB_ISSUE_LINK": ISSUE_LINK,
        "AGENTSYNC_TM_EXCLUDE": "0",
        "AGENTSYNC_OCR": "0",  # the real sync and status below look for no OCR helper
        "GIT_CONFIG_GLOBAL": os.environ["GIT_CONFIG_GLOBAL"],  # conftest's tmp identity
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
        "TERM_PROGRAM": "iTerm.app",
        **xcrun_db(),
    }


def test_a_failing_real_first_sync_leaves_exactly_one_next_line(tmp_path: Path) -> None:
    env = _env(tmp_path)
    folder = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("The purchase order is approved.\n", encoding="utf-8")
    wheel = tmp_path / "dist" / "agentsync-0.1.0-py3-none-any.whl"  # the stub uv never opens it
    wheel.parent.mkdir()
    wheel.write_text("")
    cp = subprocess.run(
        [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder), "--confirm-install-agent"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 1, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert any(line.strip() == "converted 1, deferred 0 online-only" for line in out), "the real sync ran"
    nexts = [line for line in out + cp.stderr.splitlines() if "NEXT:" in line]
    assert len(nexts) == 1, nexts
    assert out[-1] == nexts[0]
    assert nexts[0].startswith(
        "NEXT: this terminal app (iTerm) was denied access to files managed by OneDrive"
    )

    # The control: the same real sync outside install.sh (no AGENTSYNC_NO_NEXT_HINT) ends with a NEXT line.
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    direct = subprocess.run(
        [sys.executable, "-m", "agentsync", "sync", "--config", str(cfg)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
    )
    assert direct.returncode == 0, direct.stdout + direct.stderr
    assert [line for line in direct.stdout.splitlines() if line.startswith("NEXT:")] == [
        direct.stdout.splitlines()[-1]
    ]


REAL_DOCTOR = REAL_THEN_80.replace("  init|add-source) exec", "  init|add-source|status) exec", 1)


def test_install_reads_the_real_status(tmp_path: Path) -> None:
    """KISS K08a, K02: step 5 runs the real ``status``. A real FAIL (here: no launcher for a
    folder under ~/Library/CloudStorage) is a ``[FAIL`` line install.sh sees (step status ``fail-lines``, no
    first sync), its output carries no status or policy detail (install.out feeds setup-report), and there is
    still exactly one NEXT line."""
    env = _env(tmp_path)
    env["STUB_AGENTSYNC"] = _exe(tmp_path / "agentsync-real-doctor", REAL_DOCTOR)
    folder = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("The purchase order is approved.\n", encoding="utf-8")
    wheel = tmp_path / "dist" / "agentsync-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_text("")
    cp = subprocess.run(
        [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder), "--confirm-install-agent"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 1, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert any(line.startswith("[FAIL] launcher ") for line in out), cp.stdout
    assert not any(line.strip() == "converted 1, deferred 0 online-only" for line in out), "no first sync"
    assert "exclude_label_names" not in cp.stdout and "(run `" not in cp.stdout
    nexts = [line for line in out + cp.stderr.splitlines() if "NEXT:" in line]
    assert len(nexts) == 1 and nexts[0] == out[-1], nexts
    assert nexts[0].startswith("NEXT: fix the [FAIL] lines above")
    log = (Path(env["HOME"]) / "agent-context" / "setup" / "install.log").read_text(encoding="utf-8")
    assert " step=status " in log and "rc=1 result=done note=fail-lines" in log


REAL_LOOP = """#!/bin/bash
case "$1" in
  init|add-source|status|sync)
    case " $* " in *" --help "*) ;; *) exec "$REAL_PYTHON" -m agentsync "$@" ;; esac ;;
esac
exec "$STUB_INNER" "$@"
"""


def test_install_without_the_flag_ends_on_the_real_loops_next(tmp_path: Path) -> None:
    """KISS K02: ``install.sh --source-local X`` (no --confirm-install-agent) makes no launchctl call and
    builds no launcher, runs the real first sync (which writes the skill), and its last line is the real
    status's NEXT line, the baseline-questions step."""
    env = _env(tmp_path)
    env["STUB_AGENTSYNC"] = _exe(tmp_path / "agentsync-real-loop", REAL_LOOP)
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("The purchase order is approved.\n", encoding="utf-8")
    wheel = tmp_path / "dist" / "agentsync-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_text("")
    cp = subprocess.run(
        [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert any(line.strip() == "first sync: converted 1, deferred 0 online-only" for line in out), cp.stdout
    calls = Path(env["STUB_LOG"]).read_text(encoding="utf-8") if Path(env["STUB_LOG"]).exists() else ""
    assert "launchctl" not in calls and not (home / "Applications").exists()
    assert (home / ".claude" / "skills" / "agentsync-docs" / "SKILL.md").is_file(), "the sync wrote the skill"
    cfg = home / "agent-context" / "sources.toml"
    direct = subprocess.run(
        [sys.executable, "-m", "agentsync", "status", "--config", str(cfg)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env=env,
    )
    status_next = next(line for line in direct.stdout.splitlines() if line.startswith("NEXT: "))
    assert status_next.startswith("NEXT: draft the baseline questions"), direct.stdout
    report = home / "agent-context" / "setup-report.md"
    assert out[-1] == f"{status_next} [setup report: {report}]"
    nexts = [line for line in out + cp.stderr.splitlines() if "NEXT:" in line]
    assert nexts == [out[-1]]
    assert "exclude_label_names" not in cp.stdout + cp.stderr, "the closing status's detail is never printed"


REAL_REPORT = """#!/bin/bash
case "$1" in
  setup-report) exec "$REAL_PYTHON" -m agentsync "$@" ;;
esac
exec "$STUB_INNER" "$@"
"""


def test_the_report_of_each_install_run_expects_its_own_next_line_after_it(tmp_path: Path) -> None:
    """N21: install.sh writes the report at its exit, before its own NEXT: line. The real setup-report reads
    that run's header and not its NEXT:, and says one fewer is expected; --report-only (no header of its own)
    counts every run's. Every run still prints exactly one NEXT:."""
    env = _env(tmp_path)
    env["STUB_AGENTSYNC"] = _exe(tmp_path / "agentsync-real-report", REAL_REPORT)
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("The purchase order is approved.\n", encoding="utf-8")
    wheel = tmp_path / "dist" / "agentsync-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_text("")

    def run(*args: str) -> str:
        cp = subprocess.run(
            [BASH32, str(INSTALL_SH), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        assert cp.returncode == 0, cp.stdout + cp.stderr
        report = (home / "agent-context" / "setup-report.md").read_text(encoding="utf-8")
        return next(ln for ln in report.splitlines() if ln.startswith("- installer output: "))

    install = (str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    first = run(*install)
    assert first.startswith(
        "- installer output: 0 NEXT: lines in 1 run (exactly one per install.sh run is expected; the "
        "run writing this report prints its NEXT: after the report, so 0 expected here) and "
    ), first
    second = run(*install)
    assert second.startswith(
        "- installer output: 1 NEXT: line in 2 runs (exactly one per install.sh run is expected; the "
        "run writing this report prints its NEXT: after the report, so 1 expected here) and "
    ), second
    bring_back = (home / "agent-context" / "bring-back.md").read_text(encoding="utf-8")
    assert second in bring_back.splitlines(), "the bring-back file carries the same line"
    last = run("--report-only")
    assert last.startswith(
        "- installer output: 2 NEXT: lines in 2 runs (exactly one per install.sh run is expected) and "
    ), last
    out = (home / "agent-context" / "setup" / "install.out").read_text(encoding="utf-8").splitlines()
    assert sum(ln.startswith("# run=") for ln in out) == 2
    assert sum(ln.startswith("NEXT:") for ln in out) == 2
