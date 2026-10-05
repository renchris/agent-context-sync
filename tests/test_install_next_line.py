"""KISS K01: install.sh's output holds exactly one NEXT line, even when its first sync is the real agentsync
(which ends an interactive sync with the loop's NEXT line) and fails.

The harness is test_install_oneshot's (a tmp HOME, a stub ``uv``, a stub ``launchctl``), except that the
installed ``agentsync`` runs the real one for ``init`` and ``sync``; its ``sync`` then exits 80 (an earlier
"Don't Allow"), so install.sh prints the whole sync output before its own NEXT line. Every other subcommand is
the oneshot stub.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from test_install_oneshot import BASH32, INSTALL_SH, ISSUE_LINK, STUB_AGENTSYNC, STUB_LAUNCHCTL, STUB_UV

REAL_THEN_80 = """#!/bin/bash
case "$1" in
  init) exec "$REAL_PYTHON" -m agentsync "$@" ;;
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
        "GIT_CONFIG_GLOBAL": os.environ["GIT_CONFIG_GLOBAL"],  # conftest's tmp identity
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
        "TERM_PROGRAM": "iTerm.app",
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
