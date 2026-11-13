"""install.sh's bounds (plan B-9): step 1's download is bounded and leaves no temp folder; the first sync and
both status runs stop at a stated limit; the launcher is swapped in by renames, so a failed copy or a stop
keeps the old one; and any exit set -e takes still ends on one NEXT line.

The harness is test_install_oneshot's (a tmp HOME, a stub ``uv``, a stub ``launchctl``). The hung children are
stubs that ``exec`` a sleep, so the pid install.sh's limit kills is the sleep itself, and the limits are 2 s.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import exec_stub
from test_install_oneshot import (
    STUB_AGENTSYNC,
    env,  # noqa: F401 - the fixture
    folder,  # noqa: F401 - the fixture
    install_log,
    install_sh,
    last_line,
    one_next,
    wheel,  # noqa: F401 - the fixture
)

BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
CURL_FLAGS = [
    "-LsSf",
    "--connect-timeout",
    "20",
    "--max-time",
    "120",
    "--retry",
    "3",
    "--retry-max-time",
    "300",
]
URL = "https://astral.sh/uv/0.12.17/install.sh"


def _exe(path: Path, text: str) -> str:
    return str(exec_stub.write(path, text))


def _have_devtools() -> bool:
    if sys.platform != "darwin" or not Path("/usr/bin/xcode-select").exists():
        return False
    cp = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, check=False)
    return cp.returncode == 0 and Path(cp.stdout.strip()).is_dir()


needs_devtools = pytest.mark.skipif(not _have_devtools(), reason="install.sh builds the launcher only then")


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


# ---- B-9.1: step 1's download ----


def _no_uv(base: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """``base`` with no uv to find: a PATH of the system folders and the scutil stub alone."""
    if shutil.which("uv", path=BASE_PATH):
        pytest.skip("a system uv is on the base PATH")
    bin_dir = tmp_path / "nouv"
    bin_dir.mkdir()
    _exe(bin_dir / "scutil", "#!/bin/bash\nexit 0\n")
    return {**base, "PATH": f"{bin_dir}:{BASE_PATH}"}


def _failing_curl(tmp_path: Path) -> tuple[str, Path]:
    """A curl that writes part of its -o file, records its argv one word a line, and fails as curl does."""
    argv = tmp_path / "curl.argv"
    body = (
        "#!/bin/bash\n"
        f'printf "%s\\n" "$@" > "{argv}"\n'
        'while [ "$#" -gt 0 ]; do [ "$1" != -o ] || { printf "#!/bin/sh\\n" > "$2"; break; }; shift; done\n'
        'echo "curl: (22) The requested URL returned error: 503" >&2\n'
        "exit 22\n"
    )
    return _exe(tmp_path / "curl-stub", body), argv


def test_uv_download_failure_removes_its_temp_dir(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    e = _no_uv(env, tmp_path)
    curl, argv = _failing_curl(tmp_path)
    cp = install_sh({**e, "__AGENTSYNC_CURL": curl})
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert f"error: could not download {URL} (its error is above); install uv by hand" in cp.stderr
    assert one_next(cp), cp.stdout + cp.stderr
    words = argv.read_text().splitlines()
    out = Path(words[words.index("-o") + 1])
    assert out.name == "uv-install.sh", words
    assert not out.parent.exists(), "the download folder outlived the failed download"


def test_dry_run_prints_the_real_curl_flags(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    e = _no_uv(env, tmp_path)
    dry = install_sh({**e, "AGENTSYNC_INSTALL_DRY_RUN": "1"})
    assert dry.returncode == 0, dry.stdout + dry.stderr
    [line] = [ln for ln in dry.stdout.splitlines() if ln.startswith("[dry-run] /usr/bin/curl ")]
    assert line.split() == ["[dry-run]", "/usr/bin/curl", *CURL_FLAGS, URL, "-o", "\\<tmp\\>/uv-install.sh"]
    # the real run's argv is the same, word for word, but for the folder
    curl, argv = _failing_curl(tmp_path)
    install_sh({**e, "__AGENTSYNC_CURL": curl})
    words = argv.read_text().splitlines()
    assert words[:-1] == [*CURL_FLAGS, URL, "-o"], words
    assert words[-1].endswith("/uv-install.sh")


# ---- B-9.2: the first sync and the two status runs stop at their limits ----

HUNG = """#!/bin/bash
sub="$1"
case " $* " in *" --help "*) exec "$STUB_INNER" "$@" ;; esac
if [ "$sub" = "$HUNG_SUB" ] && [ "${AGENTSYNC_NO_NEXT_HINT:-}" = "$HUNG_HINT" ]; then
  printf '%s' "$$" > "$HUNG_PID"
  exec /bin/sleep 30
fi
exec "$STUB_INNER" "$@"
"""


def _hung(base: dict[str, str], tmp_path: Path, sub: str, hint: str) -> tuple[dict[str, str], Path]:
    """``base`` whose agentsync hangs in ``sub`` when AGENTSYNC_NO_NEXT_HINT is ``hint`` ("1": step 5's
    status and the first sync; "": the closing status); every other call is the oneshot stub."""
    pid = tmp_path / "hung.pid"
    e = {
        **base,
        "STUB_AGENTSYNC": _exe(tmp_path / "agentsync-hung", HUNG),
        "STUB_INNER": _exe(tmp_path / "agentsync-inner", STUB_AGENTSYNC),
        "HUNG_SUB": sub,
        "HUNG_HINT": hint,
        "HUNG_PID": str(pid),
        "__AGENTSYNC_FIRST_SYNC_LIMIT": "2",
        "__AGENTSYNC_STATUS_LIMIT": "2",
    }
    return e, pid


def _step_line(e: dict[str, str], step: str) -> str:
    return next(ln for ln in install_log(e) if f" step={step} " in ln)


def _python_report(e: dict[str, str], tmp_path: Path) -> str:
    """The setup report this checkout's agentsync renders from ``e``'s install.log (B13 round 38 C1434)."""
    out = tmp_path / "python-report.md"
    cfg = Path(e["HOME"]) / "agent-context" / "sources.toml"
    cp = subprocess.run(
        [sys.executable, "-m", "agentsync", "setup-report", "--out", str(out), "--config", str(cfg)],
        capture_output=True,
        text=True,
        check=False,
        env={**e, "AGENTSYNC_OCR": "0", "AGENTSYNC_TM_EXCLUDE": "0"},
        timeout=300,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return out.read_text(encoding="utf-8")


def test_a_hung_first_sync_stops_at_its_limit(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    e, pid = _hung(env, tmp_path, "sync", "1")
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    agentsync = f"{env['HOME']}/.local/bin/agentsync"
    assert f"first sync: stopped at its limit of 2s; {agentsync} status shows where it got to" in cp.stderr
    assert re.search(r" rc=143 result=failed note=timeout$", _step_line(e, "first-sync")), install_log(e)
    assert _gone(int(pid.read_text())), "the hung sync outlived its limit"
    assert one_next(cp) and last_line(cp).startswith("NEXT: the first sync did not finish within 2s"), (
        cp.stdout
    )
    assert not re.search(r"Terminated: 15|Killed: 9", cp.stderr), cp.stderr
    report = _python_report(e, tmp_path)  # B13 round 38 C1434: its own limit, never an outside signal
    assert re.search(r"stopped at its limit after \d+s, rc 143", report), report
    assert "from outside install.sh" not in report, report


@pytest.mark.parametrize("which", ["step 5", "closing"])
def test_a_hung_status_stops_at_its_limit(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
    which: str,
) -> None:
    e, pid = _hung(env, tmp_path, "status", "1" if which == "step 5" else "")
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    agentsync = f"{env['HOME']}/.local/bin/agentsync"
    label = "status" if which == "step 5" else "closing status"
    assert f"{label}: stopped at its limit of 2s; {agentsync} status shows where it got to" in cp.stderr
    assert _gone(int(pid.read_text())), "the hung status outlived its limit"
    assert one_next(cp), cp.stdout + cp.stderr
    assert not re.search(r"Terminated: 15|Killed: 9", cp.stderr), cp.stderr
    if which == "step 5":
        assert cp.returncode == 1, cp.stdout + cp.stderr
        assert re.search(r" rc=143 result=failed note=timeout$", _step_line(e, "status")), install_log(e)
        assert last_line(cp).startswith("NEXT: agentsync status did not finish within 2s"), cp.stdout
        report = _python_report(e, tmp_path)  # B13 round 38 C1434
        # the step's own seconds, which run past its 2 s limit on a slow runner (CI 43457821779 read 3)
        assert re.search(r"stopped at its limit after \d+s, rc 143", report), report
        assert "from outside install.sh" not in report, report
    else:  # the sync itself finished: the exit stays 0, and the report line keeps the rc (P14)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        assert _step_line(e, "report").endswith(" closing_status=143"), install_log(e)
        assert last_line(cp).startswith("NEXT: "), cp.stdout


# ---- B-9.3: the launcher swap and the default NEXT ----

BUILD_SH = """#!/bin/bash
if [ "${1:-}" = --print-source-sha256 ]; then
  [ -z "${BUILD_SHA_RC:-}" ] || exit "$BUILD_SHA_RC"
  echo 0123456789abcdef
  exit 0
fi
printf "%s" "$OUT_DIR" > "$HOME/../build.out-dir"
mkdir -p "$OUT_DIR/AgentSyncLauncher.app/Contents/MacOS"
echo new > "$OUT_DIR/AgentSyncLauncher.app/Contents/MacOS/agentsync-launcher"
"""


def _checkout(base: dict[str, str], tmp_path: Path) -> tuple[Path, Path, bytes]:
    """A checkout with a stand-in build.sh (which records its OUT_DIR), and an old installed launcher."""
    src = tmp_path / "checkout"
    (src / "launcher").mkdir(parents=True)
    (src / "pyproject.toml").write_text('[project]\nname = "agentsync"\n')
    build = src / "launcher" / "build.sh"
    build.write_text(BUILD_SH)
    build.chmod(0o755)
    old = (
        Path(base["HOME"])
        / "Applications"
        / "AgentSyncLauncher.app"
        / "Contents"
        / "MacOS"
        / "agentsync-launcher"
    )
    old.parent.mkdir(parents=True)
    old.write_bytes(b"the old launcher\n")
    return src, old, old.read_bytes()


def _leftovers(e: dict[str, str], tmp_path: Path) -> list[Path]:
    apps = Path(e["HOME"]) / "Applications"
    out = tmp_path / "build.out-dir"  # the build folder install.sh made: gone with the run
    return [*apps.glob(".AgentSyncLauncher.*"), *[d for d in [Path(out.read_text())] if d.exists()]]


@needs_devtools
def test_a_failed_copy_keeps_the_old_launcher(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    src, old, before = _checkout(env, tmp_path)
    ditto = _exe(
        tmp_path / "ditto-stub",
        '#!/bin/bash\nmkdir -p "$2/Contents"\necho "ditto: No space left" >&2\nexit 1\n',
    )
    e = {**env, "__AGENTSYNC_DITTO": ditto}
    cp = install_sh(e, str(src), "--confirm-install-agent")
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert "could not copy the launcher into" in cp.stderr and "was not changed" in cp.stderr
    assert old.read_bytes() == before
    assert one_next(cp), cp.stdout + cp.stderr
    assert _leftovers(e, tmp_path) == []


@needs_devtools
def test_an_interrupted_swap_restores_the_old_bundle(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    """A stop after the old bundle was moved aside and before the new one was moved in: the stub copies, does
    install_app's first rename itself, then sends SIGTERM to install.sh, which on_exit answers."""
    src, old, before = _checkout(env, tmp_path)
    ditto = _exe(
        tmp_path / "ditto-stub",
        "#!/bin/bash\n"
        '/usr/bin/ditto "$1" "$2"\n'
        'mv "$HOME/Applications/AgentSyncLauncher.app" "$(dirname "$2")/old.app"\n'
        'kill -TERM "$PPID"\n',
    )
    e = {**env, "__AGENTSYNC_DITTO": ditto}
    cp = install_sh(e, str(src), "--confirm-install-agent")
    assert cp.returncode == 143, cp.stdout + cp.stderr
    assert old.read_bytes() == before, "the old launcher was not put back"
    assert one_next(cp), cp.stdout + cp.stderr
    assert _leftovers(e, tmp_path) == []


@needs_devtools
def test_any_set_e_exit_ends_on_one_next(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    """The source hash read (``want="$(...)"``) has no ``|| fail``: set -e ended the run with no NEXT."""
    src, old, before = _checkout(env, tmp_path)
    cp = install_sh({**env, "BUILD_SHA_RC": "1"}, str(src), "--confirm-install-agent")
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert "error: install.sh stopped in step launcher: a command exited 1" in cp.stderr
    assert one_next(cp), cp.stdout + cp.stderr
    assert last_line(cp).startswith("NEXT: fix the error above, then re-run: "), cp.stdout
    assert old.read_bytes() == before
