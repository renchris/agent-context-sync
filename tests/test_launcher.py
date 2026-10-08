"""launcher/ (agentsync-launcher) and scripts/install.sh, exercised for real on this Mac.

The launcher is built once per session with launcher/build.sh (native arch, ad hoc) into a tmp dir and run
against /bin/echo, /bin/sh and /bin/sleep children: exit codes, environment, signals, the wall-clock watchdog
and the canaries.  A canary that must *block* uses a FIFO (open(O_RDONLY) waits for a writer), which stands in
for an unanswered TCC prompt without touching ~/Library/CloudStorage or raising any dialog.  launchd is never
touched: install.sh runs with a tmp HOME, stub ``uv``/``agentsync`` executables and a stub ``launchctl``
(AGENTSYNC_LAUNCHCTL) (its setup log, which ``agentsync setup-report`` reads, is checked the same way);
tests/test_install_oneshot.py covers the one-shot run, the wait and the setup report in depth.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentsync import setup_report
from agentsync.config import Config
from agentsync.ops import doctor, launchd

REPO = Path(__file__).resolve().parents[1]
BUILD_SH = REPO / "launcher" / "build.sh"
INSTALL_SH = REPO / "scripts" / "install.sh"
BASH32 = "/bin/bash"


def _have_devtools() -> bool:
    if sys.platform != "darwin" or not Path("/usr/bin/xcode-select").exists():
        return False
    cp = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, check=False)
    return cp.returncode == 0 and Path(cp.stdout.strip()).is_dir()


needs_build = pytest.mark.skipif(
    not _have_devtools(), reason="needs macOS developer tools (swiftc, codesign)"
)


@pytest.fixture(scope="session")
def launcher_app(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """AgentSyncLauncher.app built by launcher/build.sh (native arch, ad hoc) into a session tmp dir."""
    if not _have_devtools():
        pytest.skip("needs macOS developer tools (swiftc, codesign)")
    out = tmp_path_factory.mktemp("launcher-build")
    env = {
        **os.environ,
        "OUT_DIR": str(out),
        "ARCHS": os.uname().machine,
        "SIGN_IDENTITY": "-",
        "ALLOW_ANY_PROGRAM": "1",  # the tests spawn /bin/echo, /bin/sh ...; the pin has its own tests below
    }
    cp = subprocess.run([str(BUILD_SH)], capture_output=True, text=True, check=False, env=env, timeout=240)
    assert cp.returncode == 0, cp.stderr
    fields = dict(line.split(": ", 1) for line in cp.stdout.strip().splitlines())
    assert set(fields) == {"app", "source_sha256", "designated_requirement"}
    app = Path(fields["app"])
    assert app == out / "AgentSyncLauncher.app"
    return app


@pytest.fixture
def exe(launcher_app: Path) -> Path:
    return launchd.launcher_executable(launcher_app)


def run(
    exe: Path, *args: str, timeout: float = 30, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(exe), *args], capture_output=True, text=True, check=False, timeout=timeout, env=env
    )


def tokens(stderr: str) -> list[str]:
    """The event tokens the launcher logged (``<iso> agentsync-launcher[pid]: TOKEN k=v``)."""
    out = []
    for line in stderr.splitlines():
        if "agentsync-launcher[" in line and "]: " in line:
            out.append(line.split("]: ", 1)[1].split(" ", 1)[0])
    return out


# ------------------------------------------------------------------------------------------------ build


@needs_build
def test_bundle_signature_identifier_and_info_plist(launcher_app: Path) -> None:
    info = plistlib.loads((launcher_app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == launchd.LAUNCHER_IDENTIFIER
    assert info["CFBundleExecutable"] == launchd.LAUNCHER_EXECUTABLE
    assert info["LSBackgroundOnly"] is True
    env = {**os.environ, "ARCHS": os.uname().machine, "SIGN_IDENTITY": "-", "ALLOW_ANY_PROGRAM": "1"}
    assert info["AgentSyncAllowAnyProgram"] is True and info["AgentSyncAllowedProgram"] == ""
    sha = subprocess.run(
        [str(BUILD_SH), "--print-source-sha256"], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()
    assert info["AgentSyncSourceSHA256"] == sha
    verify = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(launcher_app)], capture_output=True, check=False
    )
    assert verify.returncode == 0
    sig = doctor._codesign_info(launcher_app)
    assert sig.valid and sig.identifier == "com.agentsync.launcher"
    assert sig.adhoc and sig.hardened_runtime and sig.team_id is None
    assert sig.requirement is not None and sig.requirement.startswith("cdhash H")


@needs_build
def test_source_hash_is_stable_and_tracks_the_identity() -> None:
    def sha(**env: str) -> str:
        e = {**os.environ, **env}
        return subprocess.run(
            [str(BUILD_SH), "--print-source-sha256"], capture_output=True, text=True, check=True, env=e
        ).stdout.strip()

    a = sha(SIGN_IDENTITY="-")
    assert len(a) == 64 and a == sha(SIGN_IDENTITY="-")
    assert a != sha(SIGN_IDENTITY="Developer ID Application: Example (ABCDE12345)")
    assert a != sha(SIGN_IDENTITY="-", BUNDLE_ID="com.example.other")


def test_build_script_usage_errors() -> None:
    cp = subprocess.run([str(BUILD_SH), "--bogus"], capture_output=True, text=True, check=False)
    assert cp.returncode == 64 and "unknown argument" in cp.stderr
    cp = subprocess.run([str(BUILD_SH), "--help"], capture_output=True, text=True, check=False)
    assert cp.returncode == 0 and "SIGN_IDENTITY" in cp.stdout


# ------------------------------------------------------------------------------------------------ run


@needs_build
def test_version_and_help(exe: Path) -> None:
    cp = run(exe, "--version")
    assert cp.returncode == 0 and cp.stdout.strip() == "agentsync-launcher 1.0.0"
    cp = run(exe, "--help")
    assert cp.returncode == 0 and "--canary" in cp.stdout


@needs_build
def test_spawns_echo_with_arguments(exe: Path) -> None:
    cp = run(exe, "--", "/bin/echo", "hello", "two words", "--timeout")
    assert cp.returncode == 0
    assert cp.stdout == "hello two words --timeout\n"
    assert cp.stderr == "", "a clean run logs nothing"


@needs_build
@pytest.mark.parametrize("code", [0, 1, 7, 75, 77, 78, 255])
def test_exit_status_is_the_childs(exe: Path, code: int) -> None:
    assert run(exe, "--", "/bin/sh", "-c", f"exit {code}").returncode == code


@needs_build
def test_environment_passes_through_without_loader_and_python_overrides(exe: Path) -> None:
    env = {"PATH": launchd.LAUNCHD_PATH, "AGENTSYNC_PROBE": "a b=c"}
    # dyld acts on DYLD_* in the launcher itself, before main() and so before the scrub under test. With SIP
    # on it ignores them for the hardened launcher. GitHub's macOS runners have SIP off, and there this test
    # got an empty stdout for as long as DYLD_INSERT_LIBRARIES named a missing file: dyld ends a process
    # whose inserted dylib cannot be loaded, unless library validation is in force. So the inserted library
    # is one that always loads, the search paths name an absent directory, and a launcher that dies says why.
    hostile = {
        "PYTHONUTF8": "1",
        "PYTHONPATH": "/tmp/evil",
        "DYLD_INSERT_LIBRARIES": "/usr/lib/libSystem.B.dylib",
        "DYLD_LIBRARY_PATH": "/tmp/evil",
        "DYLD_FRAMEWORK_PATH": "/tmp/evil",
    }
    cp = run(exe, "--", "/usr/bin/env", env={**env, **hostile})
    assert cp.returncode == 0, cp.stderr
    got = dict(line.split("=", 1) for line in cp.stdout.splitlines())
    assert got == env


@needs_build
def test_loader_overrides_the_launcher_received_never_reach_an_unrestricted_child(
    launcher_app: Path, tmp_path: Path
) -> None:
    """The test above cannot fail for DYLD_* on a Mac with SIP on: dyld removes them from the hardened
    launcher before main(), and again from /usr/bin/env.  Here a copy re-signed to honour them (test only)
    starts this interpreter, which prints whatever it was given."""
    code = "import os; print(sorted(k for k in os.environ if k.startswith(('DYLD_', 'PYTHON'))))"
    hostile = {
        "PYTHONPATH": "/tmp/evil",
        "DYLD_INSERT_LIBRARIES": "/usr/lib/libSystem.B.dylib",
        "DYLD_LIBRARY_PATH": "/tmp/evil",
    }
    env = {"PATH": launchd.LAUNCHD_PATH, **hostile}
    direct = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env
    )
    if direct.stdout.strip() != str(sorted(hostile)):
        pytest.skip("this interpreter does not show DYLD_* (a SIP-protected or hardened python)")
    app = tmp_path / "HonoursDyld.app"
    shutil.copytree(launcher_app, app, symlinks=True)
    entitlements = tmp_path / "allow-dyld-env.plist"
    entitlements.write_bytes(plistlib.dumps({"com.apple.security.cs.allow-dyld-environment-variables": True}))
    sign = ["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", launchd.LAUNCHER_IDENTIFIER]
    sign += ["--options", "runtime", "--timestamp=none", "--entitlements", str(entitlements), str(app)]
    subprocess.run(sign, check=True, capture_output=True)
    cp = run(launchd.launcher_executable(app), "--", sys.executable, "-c", code, env=env)
    assert (cp.returncode, cp.stdout.strip(), cp.stderr) == (0, "[]", "")


def _pinned_copy(launcher_app: Path, tmp_path: Path, program: str) -> Path:
    """The test bundle with Info.plist pinned to ``program`` (the signature no longer matches the edited
    plist: irrelevant here, the launcher reads its own plist either way)."""
    app = tmp_path / "Pinned.app"
    shutil.copytree(launcher_app, app, symlinks=True)
    info = app / "Contents" / "Info.plist"
    data = plistlib.loads(info.read_bytes())
    data.update(AgentSyncAllowedProgram=program, AgentSyncAllowAnyProgram=False)
    info.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
    return launchd.launcher_executable(app)


@needs_build
def test_pinned_launcher_refuses_any_other_program(launcher_app: Path, tmp_path: Path) -> None:
    """deploy-ops-launcher-arbitrary-program-trampoline: a launcher grant is not a grant for /bin/sh."""
    shim = tmp_path / "python"
    shim.write_text('#!/bin/sh\necho ran "$@"\n')
    shim.chmod(0o700)
    exe = _pinned_copy(launcher_app, tmp_path, str(shim))
    refused = run(exe, "--", "/usr/bin/id", "-un")
    assert refused.returncode == 64 and "PROGRAM_REFUSED" in tokens(refused.stderr)
    assert refused.stdout == ""
    wrong_args = run(exe, "--", str(shim), "-c", "import os")
    assert wrong_args.returncode == 64
    ok = run(exe, "--", str(shim), *launchd.CHILD_PREFIX, "--mode", "poll")
    assert ok.returncode == 0 and ok.stdout.startswith("ran -I -X utf8 -m agentsync sync --mode poll")


@needs_build
def test_the_job_a_python3_build_writes_starts_under_a_launcher_pinned_to_python(
    launcher_app: Path, tmp_path: Path, sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second bring-back (2026-10-07), with the real launcher. install.sh builds it for ``<tool>/bin/python``.
    On the field Mac the updated tool ran as ``<tool>/bin/python3``, the same file. The launcher compares
    the path as text: the job ``install-agent`` writes from that environment named ``python3``, which is
    refused, exit 64, on every run. ``job_arguments`` now names the pin, and that job starts."""
    tool = tmp_path / "tool" / "bin"
    tool.mkdir(parents=True)
    shim = tool / "python"
    shim.write_text('#!/bin/sh\necho ran "$@"\n')
    shim.chmod(0o700)
    (tool / "python3").symlink_to("python")
    exe = _pinned_copy(launcher_app, tmp_path, str(shim))
    monkeypatch.setattr(sys, "executable", str(tool / "python3"))
    argv = list(launchd.job_arguments(sample_config, "poll", 300, exe))
    sep = argv.index("--")
    assert argv[0] == str(exe) and argv[sep + 1] == str(shim)
    started = run(exe, *argv[1:])
    assert started.returncode == 0, started.stderr
    assert (
        started.stdout
        == f"ran -I -X utf8 -m agentsync sync --mode poll --config {sample_config.config_path}\n"
    )
    # The spelling the build wrote before: the same file, and the launcher refuses it.
    before = [*argv[1 : sep + 1], str(tool / "python3"), *argv[sep + 2 :]]
    refused = run(exe, *before)
    assert refused.returncode == 64 and "PROGRAM_REFUSED" in tokens(refused.stderr)
    assert refused.stdout == ""


@needs_build
def test_unpinned_release_build_runs_nothing(launcher_app: Path, tmp_path: Path) -> None:
    exe = _pinned_copy(launcher_app, tmp_path, "")
    cp = run(exe, "--", "/bin/echo", "x")
    assert cp.returncode == 64 and "PROGRAM_REFUSED" in tokens(cp.stderr)


@needs_build
@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ((), "missing --"),
        (("--", "echo", "x"), "absolute path"),
        (("--bogus", "--", "/bin/echo"), "unknown option"),
        (("--timeout", "-1", "--", "/bin/echo"), "seconds"),
        (("--timeout", "soon", "--", "/bin/echo"), "seconds"),
        (("--canary", "relative", "--", "/bin/echo"), "absolute path"),
        (("--canary-only",), "at least one --canary"),
        (("--canary-only", "--canary", "/tmp", "--", "/bin/echo"), "takes no program"),
    ],
)
def test_usage_errors_exit_64(exe: Path, args: tuple[str, ...], needle: str) -> None:
    cp = run(exe, *args)
    assert cp.returncode == 64
    assert "USAGE" in tokens(cp.stderr) and needle in cp.stderr


@needs_build
def test_spawn_failure_exits_71(exe: Path, tmp_path: Path) -> None:
    cp = run(exe, "--", str(tmp_path / "no-such-program"))
    assert cp.returncode == 71 and tokens(cp.stderr) == ["SPAWN_FAILED"]
    assert "No such file or directory" in cp.stderr


@needs_build
def test_watchdog_stops_a_hung_child_with_tcc_pending(exe: Path) -> None:
    t0 = time.monotonic()
    cp = run(exe, "--timeout", "0.3", "--grace", "5", "--", "/bin/sleep", "60")
    elapsed = time.monotonic() - t0
    assert cp.returncode == launchd.EXIT_TCC_PENDING == 79
    assert tokens(cp.stderr) == ["TCC_PENDING", "CHILD_EXIT"]
    assert "reason=watchdog" in cp.stderr and "timeout_s=0.3" in cp.stderr
    assert elapsed < 5, f"SIGTERM ended sleep at once (measured {elapsed:.2f}s)"


@needs_build
def test_watchdog_sigkills_a_child_that_ignores_sigterm(exe: Path, tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    script = f'trap "" TERM; : > "{ready}"; while :; do /bin/sleep 0.1; done'
    t0 = time.monotonic()
    cp = run(exe, "--timeout", "1", "--grace", "1", "--", "/bin/sh", "-c", script)
    elapsed = time.monotonic() - t0
    assert ready.exists()
    assert cp.returncode == 79
    assert tokens(cp.stderr) == ["TCC_PENDING", "WATCHDOG_KILL", "CHILD_EXIT"]
    assert "status=9" in cp.stderr
    assert 1.8 < elapsed < 6


@needs_build
def test_no_watchdog_when_timeout_is_zero(exe: Path) -> None:
    cp = run(exe, "--timeout", "0", "--", "/bin/sh", "-c", "/bin/sleep 1.2; exit 3")
    assert cp.returncode == 3 and cp.stderr == ""


@needs_build
def test_sigterm_is_forwarded_and_reraised(exe: Path) -> None:
    proc = subprocess.Popen(
        [str(exe), "--", "/bin/sleep", "60"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)
    _out, err = proc.communicate(timeout=10)
    assert proc.returncode == -signal.SIGTERM, "launchd sees the same signal death as the child's"
    assert tokens(err) == ["FORWARD", "CHILD_SIGNALED"]


@needs_build
def test_child_signal_death_is_mirrored(exe: Path) -> None:
    cp = run(exe, "--", "/bin/sh", "-c", "kill -TERM $$")
    assert cp.returncode == -signal.SIGTERM
    cp = run(exe, "--", "/bin/sh", "-c", "kill -SEGV $$")
    assert cp.returncode == 128 + signal.SIGSEGV, "a crash signal is reported, not re-raised on the launcher"
    assert "CHILD_SIGNALED signal=11" in cp.stderr


# ------------------------------------------------------------------------------------------------ canaries


@needs_build
def test_blocked_canary_is_tcc_pending_and_never_runs_the_child(exe: Path, tmp_path: Path) -> None:
    fifo = tmp_path / "prompt-pending"
    os.mkfifo(fifo)
    marker = tmp_path / "child-ran"
    t0 = time.monotonic()
    cp = run(
        exe,
        "--canary-timeout",
        "1",
        "--canary",
        str(tmp_path),
        "--canary",
        str(fifo),
        "--",
        "/usr/bin/touch",
        str(marker),
    )
    elapsed = time.monotonic() - t0
    assert cp.returncode == 79
    assert tokens(cp.stderr) == ["CANARY_OK", "TCC_PENDING"]
    assert "reason=canary" in cp.stderr and str(fifo) in cp.stderr
    assert "wants to access files managed by" in cp.stderr
    assert not marker.exists()
    assert elapsed < 4


@needs_build
def test_canary_results_in_run_mode_still_run_the_child(exe: Path, tmp_path: Path) -> None:
    denied = tmp_path / "denied"
    denied.mkdir()
    denied.chmod(0)
    try:
        sentinel = tmp_path / "Sentinel.txt"
        sentinel.write_text("x")
        cp = run(
            exe,
            *("--canary", str(tmp_path), "--canary", str(sentinel)),
            *("--canary", str(tmp_path / "gone"), "--canary", str(denied)),
            "--",
            "/bin/echo",
            "ran",
        )
    finally:
        denied.chmod(0o700)
    assert cp.returncode == 0 and cp.stdout == "ran\n"
    assert tokens(cp.stderr) == ["CANARY_OK", "CANARY_OK", "CANARY_MISSING", "TCC_DENIED"]


@needs_build
def test_canary_only_exit_codes(exe: Path, tmp_path: Path) -> None:
    assert run(exe, "--canary-only", "--canary", str(tmp_path)).returncode == 0
    assert run(exe, "--canary-only", "--canary", str(tmp_path / "gone")).returncode == 66
    denied = tmp_path / "denied"
    denied.mkdir()
    denied.chmod(0)
    try:
        cp = run(exe, "--canary-only", "--canary", str(denied), "--canary", str(tmp_path / "gone"))
    finally:
        denied.chmod(0o700)
    assert cp.returncode == launchd.EXIT_TCC_DENIED == 80
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    cp = run(exe, "--canary-only", "--canary-timeout", "0.5", "--canary", str(fifo))
    assert cp.returncode == 79


@needs_build
def test_canary_reads_no_content(exe: Path, tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("content")
    os.utime(f, (1_000_000_000, 1_000_000_000))
    before = f.stat()
    assert run(exe, "--canary-only", "--canary", str(f)).returncode == 0
    after = f.stat()
    assert (after.st_atime, after.st_mtime, after.st_size) == (
        before.st_atime,
        before.st_mtime,
        before.st_size,
    )


@needs_build
def test_self_responsible_probe_runs(exe: Path, tmp_path: Path) -> None:
    """--self-responsible re-spawns the launcher disclaimed (dlsym finds the call on macOS 15)."""
    cp = run(exe, "--self-responsible", "--canary-only", "--canary", str(tmp_path))
    assert cp.returncode == 0, cp.stderr
    assert tokens(cp.stderr) == ["CANARY_OK"]
    cp = run(
        exe,
        "--self-responsible",
        "--",
        "/bin/sh",
        "-c",
        'echo "m=${AGENTSYNC_LAUNCHER_DISCLAIMED:-}"; exit 4',
    )
    assert cp.returncode == 4 and cp.stdout == "m=\n", "the marker never reaches the real child"


@needs_build
def test_doctor_probe_through_the_real_launcher(exe: Path, tmp_path: Path) -> None:
    real = doctor._launcher_canary
    assert real(exe, tmp_path, 5.0)[0] == 0
    assert real(exe, tmp_path / "gone", 5.0)[0] == launchd.EXIT_CANARY_MISSING


@needs_build
def test_launchd_job_argv_runs_agentsync_through_the_launcher(
    sample_config: Config, launcher_app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ProgramArguments install-agent writes: launcher -> python -m agentsync sync, same exit code."""
    monkeypatch.setenv(launchd.LAUNCHER_ENV, str(launcher_app))
    spec = launchd.poll_spec(sample_config)
    assert spec.program_arguments[0] == str(launchd.launcher_executable(launcher_app))
    argv = list(launchd.job_arguments(sample_config, "dry_run", 300, launchd.find_launcher()))
    env = {**dict(spec.environment), "HOME": os.environ["HOME"], "AGENTSYNC_OCR": "0"}
    via = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120, env=env)
    direct = subprocess.run(
        argv[argv.index("--") + 1 :], capture_output=True, text=True, check=False, timeout=120, env=env
    )
    assert via.returncode == direct.returncode, via.stderr + direct.stderr
    assert via.returncode in (0, 1)


# ------------------------------------------------------------------------------------------------ install.sh


def test_install_sh_is_shellcheck_and_bash32_clean() -> None:
    for script in (INSTALL_SH, BUILD_SH):
        assert os.access(script, os.X_OK)
        assert subprocess.run([BASH32, "-n", str(script)], check=False).returncode == 0
    sc = shutil.which("shellcheck")
    if sc is None:
        pytest.skip("shellcheck not installed")
    # install.sh's shellcheck run is test_deploy_pack.py::test_scripts_pass_shellcheck[install.sh]
    cp = subprocess.run([sc, str(BUILD_SH)], capture_output=True, text=True, check=False)
    assert cp.returncode == 0, cp.stdout


STUB_UV = """#!/bin/bash
echo "uv $*" >> "$STUB_LOG"
if [ "$1 $2" = "tool install" ]; then
  if [ -n "${STUB_UV_INSTALL_RC:-}" ]; then
    echo "error: Failed to download (stub)" >&2; exit "$STUB_UV_INSTALL_RC"
  fi
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/uv/tools/agentsync/bin"
  cp "$STUB_AGENTSYNC" "$HOME/.local/bin/agentsync"
  chmod 755 "$HOME/.local/bin/agentsync"
elif [ "$1 $2 ${3:-}" = "tool dir --bin" ]; then
  echo "$HOME/.local/bin"
elif [ "$1 $2" = "tool dir" ]; then
  echo "$HOME/.local/share/uv/tools"
fi
"""

STUB_AGENTSYNC = """#!/bin/bash
echo "agentsync $*" >> "$STUB_LOG"
cfg="" out="" pos=""
sub="$1"
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --config) cfg="$2"; shift ;;
    --out) out="$2"; shift ;;
    --*) ;;
    *) pos="$1" ;;
  esac
  shift
done
# Like the real ones (KISS K14): init and add-source create a missing config, and both keep one inbox.
setup() { [ -f "$cfg" ] || { mkdir -p "$(dirname "$cfg")"; echo "# stub" > "$cfg"; }; }
inbox() { grep -q '^kind = "inbox"' "$cfg" || printf '[[source]]\\nkind = "inbox"\\n' >> "$cfg"; }
case "$sub" in
  init) setup; inbox ;;
  add-source) [ -z "$pos" ] || { setup; echo "[[source]]" >> "$cfg"; inbox; } ;;
  doctor|status) exit "${STUB_DOCTOR_RC:-0}" ;;
  setup-report) mkdir -p "$(dirname "$out")"; echo "# agentsync setup report" > "$out" ;;
esac
exit 0
"""

STUB_LAUNCHCTL = """#!/bin/bash
echo "launchctl $*" >> "$STUB_LOG"
n="$(cat "$STUB_LOG.runs" 2>/dev/null || echo 0)"
case "$1" in
  print) printf '%s = {\\n\\tstate = not running\\n\\truns = %s\\n\\tlast exit code = 0\\n}\\n' "$2" "$n" ;;
  kickstart) echo $((n + 1)) > "$STUB_LOG.runs" ;;
  *) exit 64 ;;
esac
"""


@pytest.fixture
def stubs(tmp_path: Path) -> dict[str, str]:
    """A tmp HOME and PATH whose ``uv`` and ``agentsync`` are logging stubs (nothing real is installed)."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(STUB_UV)
    (bin_dir / "uv").chmod(0o755)
    agentsync = tmp_path / "agentsync-stub"
    agentsync.write_text(STUB_AGENTSYNC)
    agentsync.chmod(0o755)
    launchctl = tmp_path / "launchctl-stub"  # every run gets it: no test can reach /bin/launchctl
    launchctl.write_text(STUB_LAUNCHCTL)
    launchctl.chmod(0o755)
    return {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_AGENTSYNC": str(agentsync),
        "AGENTSYNC_LAUNCHCTL": str(launchctl),
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.05",
        "LC_ALL": "C",
    }


def install_sh(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH32, str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def calls(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def test_install_sh_help_and_usage_errors(stubs: dict[str, str], tmp_path: Path) -> None:
    cp = install_sh(stubs, "--help")
    assert cp.returncode == 0 and "--confirm-install-agent" in cp.stdout
    assert "AGENTSYNC_INSTALL_DRY_RUN=1" in cp.stdout
    assert "AGENTSYNC_REBUILD_LAUNCHER=1" in cp.stdout  # KISS K11b: a developer variable, not an option
    for gone in (
        "--dry-run",
        "--config",
        "--no-report",
        "--log-end",
        "--source",
        "SOURCE",
        "--rebuild-launcher",
    ):
        assert f"{gone} " not in cp.stdout, f"KISS K17/K11b: --help still names {gone}"
    assert install_sh(stubs, "--bogus").returncode == 2
    # --no-report and --log-end are hidden arms, still parsed (test_install_oneshot.py)
    for gone in (["--dry-run"], ["--config", "x.toml"], ["--rebuild-launcher"]):
        cp = install_sh(stubs, *gone)
        assert cp.returncode == 2 and f"unknown option {gone[0]}" in cp.stderr, gone
    assert install_sh(stubs, str(tmp_path / "nowhere")).returncode == 2
    assert install_sh(stubs, str(tmp_path / "missing.whl")).returncode == 2
    assert install_sh(stubs, "--launcher").returncode == 2
    cp = install_sh(stubs, "--launcher", str(tmp_path))  # KISS K11b: the launcher is for background sync only
    assert cp.returncode == 2 and "--launcher only applies with --confirm-install-agent" in cp.stderr
    # no step ran; the setup report written at a post-parse usage error only reads launchd
    assert [c for c in calls(stubs) if not c.startswith("launchctl print ")] == []


def test_install_sh_dry_run_changes_nothing(stubs: dict[str, str], tmp_path: Path) -> None:
    log = tmp_path / "dry" / "install.log"
    env = {**stubs, "AGENTSYNC_SETUP_LOG": str(log), "AGENTSYNC_INSTALL_DRY_RUN": "1"}
    cp = install_sh(env, "--confirm-install-agent")
    assert cp.returncode == 0, cp.stderr
    home = Path(stubs["HOME"])
    assert list(home.iterdir()) == [], "dry run wrote under HOME"
    assert not log.parent.exists(), "a dry run writes no setup log, wherever the log is pointed"
    assert calls(stubs) == ["uv tool dir --bin", "uv tool dir"], "only read-only uv queries run"
    out = cp.stdout
    assert "[dry-run]" in out and "tool install --force --reinstall-package agentsync --python 3.11" in out
    for step in ("agentsync init --config", "agentsync status --config", "agentsync install-agent --config"):
        assert step in out
    next_line = "NEXT: re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to apply the steps above"
    assert out.strip().splitlines()[-1] == next_line
    assert sum(line.startswith("NEXT:") for line in out.splitlines()) == 1


def test_install_sh_dry_run_installs_uv_when_absent(stubs: dict[str, str]) -> None:
    env = {**stubs, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "AGENTSYNC_INSTALL_DRY_RUN": "1"}
    if shutil.which("uv", path=env["PATH"]):
        pytest.skip("a system uv is on the base PATH")
    cp = install_sh(env)
    assert cp.returncode == 0, cp.stderr
    assert "https://astral.sh/uv/install.sh" in cp.stdout
    assert "UV_NO_MODIFY_PATH=1" in cp.stdout and f"UV_INSTALL_DIR={stubs['HOME']}/.local/bin" in cp.stdout
    assert list(Path(stubs["HOME"]).iterdir()) == []


@needs_build
def test_install_sh_full_run_is_idempotent(stubs: dict[str, str], launcher_app: Path) -> None:
    home = Path(stubs["HOME"])
    dest = home / "Applications" / "AgentSyncLauncher.app"
    cfg = home / "agent-context" / "sources.toml"
    report = f" [setup report: {home}/agent-context/setup-report.md]"

    first = install_sh(stubs)
    assert first.returncode == 2, first.stderr  # KISS K02: it created the config and has no folder to sync
    assert not dest.exists(), "KISS K11b: no launcher without --confirm-install-agent"
    assert cfg.is_file()
    log1 = calls(stubs)
    assert log1[:2] == ["uv tool dir --bin", "uv tool dir"], "read before the install (is it current?)"
    assert log1[2].startswith("uv tool install --force --reinstall-package agentsync --python 3.11 ")
    assert log1[2].endswith(str(REPO))
    assert f"agentsync init --config {cfg}" in log1 and f"agentsync status --config {cfg}" in log1
    assert not any("install-agent" in c for c in log1), "no LaunchAgent without --confirm-install-agent"
    assert not any(c.startswith("launchctl") for c in log1), "no launchctl call without the flag"
    assert first.stdout.strip().splitlines()[-1] == (
        f"NEXT: no folder to sync yet: list them with {INSTALL_SH} --list-folders, choose the folders to "
        f'sync, then run: {INSTALL_SH} --source-local "<folder>" (one --source-local per folder){report}'
    )

    second = install_sh(stubs)
    assert second.returncode == 0, second.stderr
    assert f"config: {cfg} exists (inbox ensured)" in second.stdout
    log2 = calls(stubs)[len(log1) :]
    assert f"agentsync init --config {cfg}" in log2, "a re-run's flagless init ensures the inbox"
    assert cfg.read_text().count('kind = "inbox"') == 1
    assert second.stdout.strip().splitlines()[-1] == (
        f"NEXT: run {home}/.local/bin/agentsync sync and follow its NEXT line{report}"
    ), "KISS K11b: never a re-run with --confirm-install-agent; K02: the stub status prints no NEXT"

    no_source = install_sh(stubs, "--launcher", str(launcher_app), "--confirm-install-agent")
    assert no_source.returncode == 1, "asked for background sync of nothing"
    assert not any("install-agent" in c for c in calls(stubs))
    assert (
        no_source.stdout.strip().splitlines()[-1].startswith("NEXT: choose a folder to sync, then re-run: ")
    )
    assert (dest / "Contents" / "MacOS" / "agentsync-launcher").is_file(), "the flag builds or copies it"
    assert (
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(dest)], check=False).returncode == 0
    )
    assert "designated requirement: cdhash H" in no_source.stdout
    mtime = (dest / "Contents" / "MacOS" / "agentsync-launcher").stat().st_mtime_ns

    folder = home / "Projects"
    folder.mkdir()
    third = install_sh(
        stubs, "--launcher", str(launcher_app), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert third.returncode == 0, third.stderr
    assert "is up to date" in third.stdout
    assert (dest / "Contents" / "MacOS" / "agentsync-launcher").stat().st_mtime_ns == mtime, "not re-copied"
    log3 = calls(stubs)
    assert f"agentsync sync --once --materialise-budget 0 --config {cfg}" in log3  # KISS K13b: no probe
    assert f"agentsync install-agent --config {cfg}" in log3
    assert f"launchctl kickstart gui/{os.getuid()}/com.agentsync.poll" in log3
    assert "wants to access files managed by" in third.stdout
    assert third.stdout.strip().splitlines()[-1].startswith("NEXT: background sync: ok; run ")

    # From this checkout (developer tools present): a build whose sources match is kept, unless the developer
    # variable AGENTSYNC_REBUILD_LAUNCHER=1 (which replaced --rebuild-launcher, KISS K11b) forces a rebuild.
    native = {**stubs, "ARCHS": os.uname().machine}
    built = install_sh(native, "--confirm-install-agent")
    assert built.returncode == 0, built.stderr
    kept = install_sh(native, "--confirm-install-agent")
    assert kept.returncode == 0 and "launcher: " in kept.stdout and "is up to date (sources " in kept.stdout
    mtime = (dest / "Contents" / "MacOS" / "agentsync-launcher").stat().st_mtime_ns
    forced = install_sh({**native, "AGENTSYNC_REBUILD_LAUNCHER": "1"}, "--confirm-install-agent")
    assert forced.returncode == 0, forced.stderr
    assert "is up to date" not in forced.stdout and f"launcher: replacing {dest}" in forced.stdout
    assert (dest / "Contents" / "MacOS" / "agentsync-launcher").stat().st_mtime_ns != mtime, "rebuilt"


@needs_build
def test_install_sh_doctor_failure_blocks_the_agent(stubs: dict[str, str], launcher_app: Path) -> None:
    cfg = Path(stubs["HOME"]) / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("# existing\n[[source]]\n")
    env = {**stubs, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(env, "--launcher", str(launcher_app), "--confirm-install-agent")
    assert cp.returncode == 1  # asked for the agents, did not get them: an MDM script must see a failure
    assert not any(
        c.startswith(("agentsync sync", "agentsync install-agent", "launchctl")) for c in calls(env)
    )
    assert "not installing the LaunchAgents" in cp.stderr
    assert cp.stdout.strip().splitlines()[-1].startswith("NEXT: fix the [FAIL] lines above")
    assert "--confirm-install-agent" in cp.stdout.strip().splitlines()[-1]  # the NEXT line repeats the flags
    text = cfg.read_text()  # every existing byte kept; the flagless init only ensured the inbox (KISS K14)
    assert text.startswith("# existing\n[[source]]\n") and text.count('kind = "inbox"') == 1


def test_install_sh_rejects_an_unsigned_launcher(stubs: dict[str, str], tmp_path: Path) -> None:
    fake = tmp_path / "AgentSyncLauncher.app"
    (fake / "Contents" / "MacOS").mkdir(parents=True)
    cp = install_sh(stubs, "--launcher", str(fake), "--confirm-install-agent")
    assert cp.returncode == 1 and "codesign --verify" in cp.stderr
    assert not (Path(stubs["HOME"]) / "Applications").exists()


# ---- the setup log `agentsync setup-report` reads -------------------------------------------------------

_LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ run=(?P<run>\d{8}T\d{6}Z-\d+) (?P<rest>.*)$")


def _setup_log_lines(path: Path) -> list[tuple[str, str]]:
    """(run id, the rest) per line; every line must match the documented shape."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LOG_LINE.match(line)
        assert m, f"malformed setup log line: {line!r}"
        out.append((m["run"], m["rest"]))
    return out


def _steps(lines: list[tuple[str, str]]) -> list[tuple[str, str, str, str]]:
    """(step, result, rc, note) of the step lines."""
    got = []
    for _run, rest in lines:
        kv = dict(re.findall(r"(\w+)=(\S+)", rest))
        if "step" in kv:
            assert kv["seconds"].isdigit()
            got.append((kv["step"], kv["result"], kv["rc"], kv.get("note", "")))
    return got


def _wheel(tmp_path: Path) -> Path:
    wheel = tmp_path / "agentsync-0.1.0-py3-none-any.whl"  # the stub uv never opens it
    wheel.write_text("")
    return wheel


def test_install_sh_writes_one_setup_log_line_per_step(stubs: dict[str, str], tmp_path: Path) -> None:
    home = Path(stubs["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    folder.mkdir(parents=True)
    wheel = _wheel(tmp_path)
    cp = install_sh(stubs, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stderr
    assert "install.log" not in cp.stdout + cp.stderr, "the setup log is never printed"
    assert (
        cp.stdout.strip()
        .splitlines()[-1]
        .startswith(f"NEXT: run {home}/.local/bin/agentsync sync and follow its NEXT line")
    ), "KISS K11b: no launcher dead end without --confirm-install-agent"
    assert not any(c.startswith("launchctl") for c in calls(stubs)), "no launchctl call without the flag"
    log = home / "agent-context" / "setup" / "install.log"
    assert (log.parent.stat().st_mode & 0o777) == 0o700 and (log.stat().st_mode & 0o777) == 0o600
    assert (log.parent.parent.stat().st_mode & 0o777) == 0o700, "a ~/agent-context it creates is 0700"
    lines = _setup_log_lines(log)
    assert len({run for run, _ in lines}) == 1 and len(lines) == 12
    start, end = lines[0][1], lines[-1][1]
    head = f"start install.sh compat=10 commit=- kind=wheel source={wheel} args={wheel} --source-local "
    assert start.startswith(head)
    assert start.endswith("FY26\\ Projects"), "folder paths are kept (setup-report redacts them)"
    assert re.fullmatch(r"end rc=0 seconds=\d+", end)
    assert _steps(lines) == [
        ("uv", "skipped", "0", "present"),
        ("agentsync", "done", "0", ""),
        ("launcher", "skipped", "0", "not-requested"),
        ("helpers", "skipped", "0", "no-interpreter" if _have_devtools() else "no-devtools"),
        ("config", "done", "0", "created"),
        ("status", "done", "0", ""),
        ("first-sync", "done", "0", ""),  # KISS K02: the first sync runs without the flag
        ("agent", "skipped", "0", "not-requested"),
        ("wait", "skipped", "0", "not-requested"),
        ("report", "done", "0", "agentsync"),  # before the end line (the report read a provisional one)
    ]

    again = install_sh({**stubs, "STUB_DOCTOR_RC": "1"}, str(wheel), "--source-local", str(folder))
    assert again.returncode == 1, again.stderr  # KISS K02: a status FAIL exits 1
    lines = _setup_log_lines(log)
    assert len(lines) == 24 and len({run for run, _ in lines}) == 2, "appended, one run id per run"
    assert ("config", "done", "0", "add-source") in _steps(lines[12:])
    assert ("status", "done", "1", "fail-lines") in _steps(lines[12:])


def test_install_sh_setup_log_records_the_failed_step(stubs: dict[str, str], tmp_path: Path) -> None:
    """deploy-ops-install-sh-exit-status: a failed uv tool install is a failed step (1), not a usage error;
    it ends on one NEXT line and the setup log records the step."""
    log = tmp_path / "elsewhere" / "install.log"
    report = tmp_path / "elsewhere" / "setup-report.md"
    env = {
        **stubs,
        "STUB_UV_INSTALL_RC": "2",
        "AGENTSYNC_SETUP_LOG": str(log),
        "AGENTSYNC_SETUP_REPORT": str(report),
        "AGENTSYNC_CONFIG": str(Path(stubs["HOME"]) / "x.toml"),
    }
    cp = install_sh(env)
    assert cp.returncode == 1, cp.stderr
    assert "uv tool install failed" in cp.stderr
    last = cp.stdout.strip().splitlines()[-1]
    assert last.startswith("NEXT: ") and "re-run: " in last
    assert "--config" not in last, "KISS K17: AGENTSYNC_CONFIG carries the config; no NEXT names --config"
    lines = _setup_log_lines(log)
    assert not (Path(stubs["HOME"]) / "agent-context").exists(), "AGENTSYNC_SETUP_LOG moves the log"
    assert report.is_file(), "the shell report (agentsync never got installed)"
    start = lines[0][1]
    commit = "-"
    if _have_devtools():
        git = ["/usr/bin/git", "-C", str(REPO)]
        ro = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # read-only: the index's stat cache stays as it is
        sha = subprocess.run(
            [*git, "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, check=True, env=ro
        ).stdout.strip()
        diff = subprocess.run([*git, "diff", "HEAD", "--"], capture_output=True, check=True, env=ro).stdout
        # a dirty checkout names its local changes: the first 12 hex of the SHA-256 of `git diff HEAD`
        commit = f"{sha}-dirty tree={hashlib.sha256(diff).hexdigest()[:12]}" if diff else sha
    assert (
        start.startswith(f"start install.sh compat=10 commit={commit} kind=checkout")
        and f"kind=checkout source={REPO}" in start
    )
    assert _steps(lines) == [
        ("uv", "skipped", "0", "present"),
        ("agentsync", "failed", "2", ""),
        ("report", "done", "0", "fallback"),
    ]
    assert lines[-1][1].startswith("end rc=1 ") and lines[-2][1].startswith("step=report ")


def test_setup_report_reads_the_install_sh_log(
    stubs: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = Path(stubs["HOME"]) / "Projects"
    folder.mkdir()
    cp = install_sh(stubs, str(_wheel(tmp_path)), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stderr
    monkeypatch.setenv("HOME", stubs["HOME"])
    text, _red = setup_report.build_report(Path(stubs["HOME"]) / "agent-context" / "sources.toml")
    installer = text.split("\n## Installer\n", 1)[1].split("\n## ", 1)[0]
    assert "1 install.sh run(s)" in installer and "exit 0 after" in installer
    assert "step=launcher" in installer and "step=report" in installer
