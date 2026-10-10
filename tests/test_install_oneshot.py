"""scripts/install.sh as the README one-prompt (setup prompt v10) runs it: ``--version``, ``--log-start``,
``--list-folders``, then one command that installs, syncs once, installs the LaunchAgents, starts the poll job
and waits for its first run to pass the macOS access check or exit 0 (with a progress line at least every
15 s), then writes the setup report at every exit; ``--log`` and ``--report-only`` keep the friction log.

Nothing real is touched: HOME is a tmp dir, ``uv``/``agentsync`` are logging stubs on PATH, and launchd is a
stub ``launchctl`` named by AGENTSYNC_LAUNCHCTL (every harness run sets it, so no test can reach
/bin/launchctl and the real com.agentsync.poll job). The stub plays launchd: ``kickstart`` starts a run that
ends at once with the next exit code of STUB_LC_EXITS (the last one repeats), and ``print`` shows
``state``, ``runs``, ``last exit code`` and ``stderr path`` like ``launchctl print`` does; an exit 79 also
appends the launcher's TCC_PENDING line to that stderr log. With STUB_LC_CANARIES (paths joined by ``:``)
``print`` also shows ``pid`` while running and the launcher's ``arguments`` with one ``--canary`` per path,
and ``kickstart`` logs the launcher's canary lines as STUB_LC_CANARY_LOG says (``ok``: CANARY_OK for each;
``pending``: CANARY_OK for the first, then TCC_PENDING).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pwd
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentsync import setup_report
from agentsync.config import default_config_text, inbox_source_table, load_config, local_source_table
from agentsync.convert import pandoc as pandoc_converter
from agentsync.ops import doctor

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"
BASH32 = "/bin/bash"
UID = os.getuid()
ACTION = (
    "ACTION: macOS is asking whether agentsync-launcher may access files managed by OneDrive. Click Allow."
)
LOOP_NEXT = "draft the baseline questions (stub)"  # the stub status's NEXT, which install.sh lifts (KISS K02)
COMPAT = int(
    re.findall(r"^SETUP_PROMPT_COMPAT=(\d+) ", INSTALL_SH.read_text(encoding="utf-8"), re.MULTILINE)[0]
)
"""The installer's setup prompt version, which the README prompt and ``setup_report.PROMPT_VERSION`` equal
(``test_readme_prompt_version_matches_the_installer_constant``, tests/test_deploy_pack.py)."""
PROMPT_SOURCE = "README.md on the main branch of https://github.com/renchris/agent-context-sync"


def started(agent: str) -> str:
    """``--log-start``'s value as the README prompt writes it: its own version, then the tool and model."""
    return f"prompt v{COMPAT}, {agent}"


TCC_CLICK = "turn on agentsync-launcher in System Settings > Privacy & Security > Files and Folders"
TCC_PENDING_FAIL = (
    "[FAIL] tcc.fy26-projects — TCC_PENDING: /x did not answer within 15s; macOS is asking (or asked) the "
    "privacy prompt naming agentsync-launcher (fix: click Allow on the privacy prompt naming "
    "agentsync-launcher while logged in, then run agentsync status again)"
)
"""The launcher's own pending click, as status prints it (the stub's lines carry a fix, as every real FAIL
does)."""

STUB_UV = """#!/bin/bash
echo "uv $*" >> "$STUB_LOG"
echo "UV_TOOL_BIN_DIR=${UV_TOOL_BIN_DIR:-}" >> "$STUB_LOG.uv"
if [ "$1 $2" = "tool install" ]; then
  if [ -n "${STUB_UV_INSTALL_RC:-}" ]; then
    echo "error: Failed to download (stub)" >&2; exit "$STUB_UV_INSTALL_RC"
  fi
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/uv/tools/agentsync/bin"
  cp "$STUB_AGENTSYNC" "$HOME/.local/bin/agentsync"
  chmod 755 "$HOME/.local/bin/agentsync"
  echo "Installed 1 executable: agentsync" >&2
  b="$HOME/.local/bin"
  printf 'warning: `%s` is not on your PATH. To use installed tools, run ' "$b" >&2
  printf '`export PATH="%s:$PATH"` or `uv tool update-shell`.\n' "$b" >&2
elif [ "$1 $2 ${3:-}" = "tool dir --bin" ]; then
  echo "$HOME/.local/bin"
elif [ "$1 $2" = "tool dir" ]; then
  echo "$HOME/.local/share/uv/tools"
fi
"""

STUB_AGENTSYNC = """#!/bin/bash
echo "agentsync $*" >> "$STUB_LOG"
printf '%s no_next=%s pending=%s\\n' "$1" "${AGENTSYNC_NO_NEXT_HINT:-}" "${AGENTSYNC_AGENT_STEP_PENDING:-}" \\
  >> "$STUB_LOG.env"
hint() { [ "${AGENTSYNC_NO_NEXT_HINT:-}" = 1 ] || echo "next: $*"; }  # what agentsync's hints honour
sub="$1"
shift
cfg="" out="" pos="" help=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config) cfg="$2"; shift ;;
    --out) out="$2"; shift ;;
    --help) help=1 ;;
    --*) ;;
    *) pos="$1" ;;
  esac
  shift
done
# Like the real ones (KISS K14): init and add-source create a missing config, and both keep one inbox.
setup() { [ -f "$cfg" ] || { mkdir -p "$(dirname "$cfg")"; echo "# stub" > "$cfg"; }; }
inbox() { grep -q "^kind = .inbox." "$cfg" || printf '[[source]]\\nkind = "inbox"\\n' >> "$cfg"; }
case "$sub" in
  init) setup; inbox; hint "agentsync doctor" ;;
  add-source)
    [ -z "${STUB_ADD_SOURCE_RC:-}" ] || { echo "add-source failed (stub)" >&2; exit "$STUB_ADD_SOURCE_RC"; }
    setup; printf '[[source]]\\npath = "%s"\\n' "$pos" >> "$cfg"; inbox; hint "agentsync doctor" ;;
  doctor|status)
    if [ "$sub" = status ] && [ "${AGENTSYNC_NO_NEXT_HINT:-}" != 1 ]; then  # install.sh's closing status
      printf '%s\\n' "${STUB_STATUS_OUT:-NEXT: draft the baseline questions (stub)}"
      echo "  exclude_label_names: Stub Secret Label"
      exit "${STUB_STATUS_RC:-0}"
    fi
    [ -z "${STUB_DOCTOR_SLEEP:-}" ] || sleep "$STUB_DOCTOR_SLEEP"
    [ -z "${STUB_DOCTOR_OUT:-}" ] || printf '%s\\n' "$STUB_DOCTOR_OUT"
    if [ "${AGENTSYNC_AGENT_STEP_PENDING:-}" = 1 ]; then
      echo "[warn] launchd.poll — not loaded (installed by the agent step below)"
    else
      echo "[warn] launchd.poll — not loaded (fix: agentsync install-agent)"
    fi
    exit "${STUB_DOCTOR_RC:-0}" ;;
  sync)
    if [ -n "$help" ]; then
      echo "usage: agentsync sync"
      exit 0
    fi
    [ -z "${STUB_SYNC_SLEEP:-}" ] || sleep "$STUB_SYNC_SLEEP"
    echo "stub cycle"
    [ -z "${STUB_SYNC_OUT:-}" ] || printf '%s\\n' "$STUB_SYNC_OUT"
    [ -z "${STUB_SYNC_ERR:-}" ] || printf '%s\\n' "$STUB_SYNC_ERR" >&2
    exit "${STUB_SYNC_RC:-0}" ;;
  install-agent) exit "${STUB_AGENT_RC:-0}" ;;
  setup-report)
    [ -z "${STUB_REPORT_RC:-}" ] || { echo "stub setup-report failed" >&2; exit "$STUB_REPORT_RC"; }
    mkdir -p "$(dirname "$out")"
    printf '# agentsync setup report\\n\\nstub, simulate=%s\\n' "${AGENTSYNC_SIMULATE_LAUNCHD:-0}" > "$out"
    [ -n "${STUB_REPORT_NO_LINK:-}" ] || printf '\\n%s\\n' "$STUB_ISSUE_LINK" >> "$out"
    echo "wrote $out (0 value(s) redacted)"
    hint "review it" ;;
esac
exit 0
"""

ISSUE_LINK = setup_report.ISSUE_URL + "&title=Setup%20report%3A%20stub"
LINK_LINE = f"issue link (review the report first): {ISSUE_LINK}"

STUB_LAUNCHCTL = """#!/bin/bash
echo "launchctl $*" >> "$STUB_LOG"
d="$STUB_LC_DIR"
mkdir -p "$d"
runs="$(cat "$d/runs" 2>/dev/null || echo 0)"
case "$1" in
  print)
    echo "$2 = {"
    if [ -n "${STUB_LC_RUNNING:-}" ] && [ "$runs" -gt 0 ]; then
      printf '\\tstate = running\\n'
      [ -z "${STUB_LC_CANARIES:-}" ] || printf '\\tpid = %s\\n' "${STUB_LC_PID:-4242}"
    else
      printf '\\tstate = not running\\n'
    fi
    if [ -n "${STUB_LC_CANARIES:-}" ]; then
      printf '\\targuments = {\\n\\t\\t/x/AgentSyncLauncher.app/Contents/MacOS/agentsync-launcher\\n'
      printf '\\t\\t--canary-timeout\\n\\t\\t15\\n'
      IFS=: read -r -a cans <<< "$STUB_LC_CANARIES"
      for c in "${cans[@]}"; do printf '\\t\\t--canary\\n\\t\\t%s\\n' "$c"; done
      printf '\\t\\t--\\n\\t\\t/x/python\\n\\t}\\n'
    fi
    printf '\\tstderr path = %s\\n' "$d/poll.err.log"
    printf '\\truns = %s\\n' "$runs"
    printf '\\tlast exit code = %s\\n' "$(cat "$d/last" 2>/dev/null || echo '(never exited)')"
    echo "}" ;;
  kickstart)
    runs=$((runs + 1))
    echo "$runs" > "$d/runs"
    set -- $STUB_LC_EXITS
    code="$1"
    i=1
    while [ "$i" -lt "$runs" ] && [ $# -gt 1 ]; do shift; i=$((i + 1)); code="$1"; done
    echo "$code" > "$d/last"
    if [ "$code" = 79 ]; then
      echo "2026-09-30T00:00:00Z agentsync-launcher[42]: TCC_PENDING reason=canary" >> "$d/poll.err.log"
    fi
    if [ -n "${STUB_LC_CANARY_LOG:-}" ]; then
      now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      tag="$now agentsync-launcher[${STUB_LC_LOG_PID:-4242}]:"
      IFS=: read -r -a cans <<< "$STUB_LC_CANARIES"
      for c in "${cans[@]}"; do
        echo "$tag CANARY_OK path=\\"$c\\"" >> "$d/poll.err.log"
        if [ "$STUB_LC_CANARY_LOG" = pending ]; then
          echo "$tag TCC_PENDING reason=canary path=\\"$c\\"" >> "$d/poll.err.log"
          break
        fi
      done
    fi ;;
  *) exit 64 ;;
esac
"""


def _write_exe(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A tmp HOME, stub uv/agentsync on PATH and a stub launchctl; a 0.05 s wait poll."""
    home = tmp_path / "home"
    home.mkdir()
    stubbin = tmp_path / "stubbin"
    stubbin.mkdir()
    _write_exe(stubbin / "uv", STUB_UV)
    return {
        "HOME": str(home),
        "PATH": f"{stubbin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_AGENTSYNC": str(_write_exe(tmp_path / "agentsync-stub", STUB_AGENTSYNC)),
        "AGENTSYNC_LAUNCHCTL": str(_write_exe(tmp_path / "launchctl-stub", STUB_LAUNCHCTL)),
        "STUB_LC_DIR": str(tmp_path / "launchd"),
        "STUB_LC_EXITS": "0",
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.05",
        "STUB_ISSUE_LINK": ISSUE_LINK,
        "LC_ALL": "C",
    }


@pytest.fixture
def folder(env: dict[str, str]) -> Path:
    f = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    f.mkdir(parents=True)
    return f


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    w = tmp_path / "dist" / "agentsync-0.1.0-py3-none-any.whl"  # the stub uv never opens it
    w.parent.mkdir()
    w.write_text("")
    return w


def install_sh(env: dict[str, str], *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    """One install.sh run. Its NEXT says of the [FAIL] lines above it that "each names its fix", so every
    run of every test is held to that: the real agentsync's lines, and the stub's, which are written as the
    real ones are."""
    cp = subprocess.run(
        [BASH32, str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    bare = [ln for ln in cp.stdout.splitlines() if ln.startswith("[FAIL] ") and " (fix: " not in ln]
    assert not bare, f"[FAIL] line(s) that name no fix: {bare}"
    return cp


def calls(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def index_of(lines: list[str], prefix: str) -> int:
    return next(i for i, c in enumerate(lines) if c.startswith(prefix))


def last_line(cp: subprocess.CompletedProcess[str]) -> str:
    return cp.stdout.rstrip("\n").splitlines()[-1]


def one_next(cp: subprocess.CompletedProcess[str]) -> bool:
    """install.sh prints exactly one NEXT line on every path, across stdout and stderr."""
    return sum("NEXT:" in ln for ln in cp.stdout.splitlines() + cp.stderr.splitlines()) == 1


def report_path(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / "agent-context" / "setup-report.md"


def install_log(env: dict[str, str]) -> list[str]:
    return (Path(env["HOME"]) / "agent-context" / "setup" / "install.log").read_text().splitlines()


def steps(lines: list[str]) -> list[tuple[str, str, str, str]]:
    """(step, result, rc, note) of the setup-log step lines."""
    out = []
    for line in lines:
        kv = dict(re.findall(r"(\w+)=(\S+)", line))
        if "step" in kv:
            out.append((kv["step"], kv["result"], kv["rc"], kv.get("note", "")))
    return out


def rerun(wheel: Path, folder: Path) -> str:
    """The re-run command a NEXT line names (absolute script path, the same flags, shell-quoted)."""
    quoted = str(folder).replace(" ", "\\ ")
    return f"{INSTALL_SH} {wheel} --source-local {quoted} --confirm-install-agent"


# ---- --version and the README prompt -----------------------------------------------------------------------


def test_version_prints_the_prompt_compat_line_last() -> None:
    """README step 1: "If --version does not end with "setup-prompt-compat 10", ... stop"."""
    cp = subprocess.run([BASH32, str(INSTALL_SH), "--version"], capture_output=True, text=True, check=False)
    assert cp.returncode == 0, cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[-1] == "setup-prompt-compat 10" == f"setup-prompt-compat {COMPAT}"
    assert len(lines) == 2 and re.fullmatch(
        r"source commit: ([0-9a-f]{12}"
        r"( dirty [0-9a-f]{12} \(local changes in this checkout; setup prompt step 1 keeps them on a local"
        r" branch before it updates\))?|unknown)",
        lines[0],
    )


def test_readme_prompt_version_matches_the_installer_constant() -> None:
    """Bumping the prompt's "setup prompt vN" without SETUP_PROMPT_COMPAT (or the reverse) fails here."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"^SETUP_PROMPT_COMPAT=(\d+) ", script, flags=re.MULTILINE)
    assert m, "install.sh has no SETUP_PROMPT_COMPAT constant"
    n = m.group(1)
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    block = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1].split("\n## ", 1)[0]
    assert re.findall(r"setup prompt v(\d+)", block) == [n]
    assert f'"setup-prompt-compat {n}"' in block and "install.sh --version" in block
    assert f"install.sh --log-start 'prompt v{n}, <agent>'" in block, "the copy names its version"
    assert "install.sh --report-only" in block
    assert "--log-end" not in block, "KISS K17: --report-only closes the attempt"
    assert "install.sh --log '<step>' '<kind>'" in block, "the prompt logs through the installer (L4)"
    assert '"Prompt: v$SETUP_PROMPT_COMPAT"' in script, "--log-start writes the same version"


# ---- the one-shot run --------------------------------------------------------------------------------------


def test_one_shot_installs_syncs_starts_and_waits(env: dict[str, str], folder: Path, wheel: Path) -> None:
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    got = calls(env)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    order = [
        f"agentsync add-source {folder} --config {cfg}",
        f"agentsync status --config {cfg}",
        f"agentsync sync --once --materialise-budget 0 --config {cfg}",
        f"agentsync install-agent --config {cfg}",
        f"launchctl kickstart gui/{UID}/com.agentsync.poll",
        f"agentsync setup-report --out {report_path(env)} --config {cfg}",
    ]
    idx = [index_of(got, c) for c in order]
    assert idx == sorted(idx), got
    closing = max(i for i, c in enumerate(got) if c == f"agentsync status --config {cfg}")
    assert idx[4] < closing < idx[5], "KISS K02: status runs again after the wait, for the loop's NEXT"
    assert sum(c.startswith("launchctl kickstart") for c in got) == 1
    assert "ACTION:" not in cp.stdout
    assert "wait: the first background run exited 0 (ok)" in cp.stdout
    assert cp.stdout.splitlines()[-2] == LINK_LINE, "the report's issue link, the last line before NEXT"
    assert last_line(cp) == f"NEXT: background sync: ok; {LOOP_NEXT} [setup report: {report_path(env)}]"
    assert "Stub Secret Label" not in cp.stdout + cp.stderr, "the closing status's detail is never printed"
    assert sum(line.startswith("NEXT:") for line in cp.stdout.splitlines()) == 1
    assert report_path(env).read_text().startswith("# agentsync setup report")
    log = install_log(env)
    assert f"start install.sh compat={COMPAT} commit=- kind=wheel" in log[0]
    assert "launchd=simulated" not in log[0]
    assert steps(log) == [
        ("uv", "skipped", "0", "present"),
        ("agentsync", "done", "0", ""),
        ("launcher", "skipped", "0", "no-launcher"),
        ("helpers", "skipped", "0", "no-interpreter" if _have_git() else "no-devtools"),
        ("config", "done", "0", "created"),
        ("status", "done", "0", ""),
        ("first-sync", "done", "0", ""),
        ("agent", "done", "0", ""),
        ("wait", "done", "0", ""),
        ("report", "done", "0", "agentsync"),
    ]
    assert re.search(r" end rc=0 seconds=\d+$", log[-1]) and " step=report " in log[-2]


def test_tcc_pending_prints_one_action_line_then_succeeds(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(
        {**env, "STUB_LC_EXITS": "79 79 0"},
        str(wheel),
        "--source-local",
        str(folder),
        "--confirm-install-agent",
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("ACTION:")] == [ACTION]
    assert sum(c.startswith("launchctl kickstart") for c in calls(env)) == 3, "started again after each 79"
    assert "exited 79 (TCC pending: macOS is waiting for Allow); starting it again" in cp.stdout
    assert last_line(cp).startswith(f"NEXT: background sync: ok; {LOOP_NEXT} ")


def test_tcc_still_pending_at_the_timeout_exits_3(env: dict[str, str], folder: Path, wheel: Path) -> None:
    e = {**env, "STUB_LC_EXITS": "79", "AGENTSYNC_WAIT_SECONDS": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert cp.stdout.count("ACTION:") == 1
    assert "no background run exited 0 within 1s (last exit: 79, TCC pending" in cp.stderr
    assert last_line(cp) == (
        "NEXT: macOS is still waiting for Allow for agentsync-launcher: click Allow if the prompt is "
        f"showing, or {TCC_CLICK}, then re-run: {rerun(wheel, folder)} [setup report: {report_path(env)}]"
    )
    assert ("wait", "failed", "79", "tcc") in steps(install_log(env))
    assert re.search(r" end rc=3 seconds=\d+$", install_log(env)[-1])


def test_a_run_still_going_at_the_timeout_exits_3(env: dict[str, str], folder: Path, wheel: Path) -> None:
    e = {**env, "STUB_LC_RUNNING": "1", "AGENTSYNC_WAIT_SECONDS": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "ACTION:" not in cp.stdout
    assert last_line(cp).startswith("NEXT: the first background run did not exit 0 within 1s")
    assert rerun(wheel, folder) in last_line(cp)


def test_a_failed_background_run_is_decoded_and_exits_1(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(
        {**env, "STUB_LC_EXITS": "77"}, str(wheel), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 1
    assert "the first background run exited 77 (sign-in required)" in cp.stderr
    assert last_line(cp).startswith("NEXT: the first background run exited 77 (sign-in required)")


def test_a_background_run_the_launcher_refused_says_so(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """Exit 64 is the launcher's: it refused the job, most often because the job names an interpreter the
    launcher was not built for. It had no meaning here and read "exited 64 (exit 64)"."""
    cp = install_sh(
        {**env, "STUB_LC_EXITS": "64"}, str(wheel), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 1
    said = "the first background run exited 64 (the launcher refused the job: its program is not the one it"
    assert said in cp.stderr and last_line(cp).startswith(f"NEXT: {said} starts, or an option is wrong); ")


def test_doctor_tcc_pending_alone_does_not_stop_the_run(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    doctor_out = f"[ok  ] config — ok\n{TCC_PENDING_FAIL}"
    e = {**env, "STUB_DOCTOR_OUT": doctor_out, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("ACTION:")] == [ACTION]
    assert ("status", "done", "1", "tcc-pending") in steps(install_log(env))
    assert ("first-sync", "done", "0", "") in steps(install_log(env))


def test_doctor_failure_skips_first_sync_and_agent(env: dict[str, str], folder: Path, wheel: Path) -> None:
    fail = "[FAIL] docs_repo.git — /x/docs does not exist and /x is not writable (fix: mkdir -p /x/docs)"
    e = {**env, "STUB_DOCTOR_OUT": fail, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 1
    got = calls(env)
    assert not any(c.startswith(("agentsync sync", "agentsync install-agent", "launchctl")) for c in got)
    assert last_line(cp).startswith(
        f"NEXT: fix the [FAIL] lines above (each names its fix), then re-run: {INSTALL_SH}"
    )
    assert ("first-sync", "skipped", "0", "status-failed") in steps(install_log(env))


LISTING_HELD_FAIL = (
    "[FAIL] source.fy26-projects.listable — /x/TCC_PENDING: listing did not return within 120s; macOS is "
    "most likely waiting for you to click Allow on a privacy prompt (fix: click Allow on the macOS prompt, "
    "then re-run)"
)


@pytest.mark.parametrize("flag", [(), ("--confirm-install-agent",)])
def test_a_non_tcc_fail_exits_1_without_a_first_sync(
    env: dict[str, str], folder: Path, wheel: Path, flag: tuple[str, ...]
) -> None:
    """KISS K02 and field N8b: a [FAIL] other than the launcher's own TCC_PENDING (here a listing macOS holds
    for an Allow click in this terminal, whose path even holds the token) stops the run, with or without
    background sync."""
    e = {**env, "STUB_DOCTOR_OUT": LISTING_HELD_FAIL, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), *flag)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert not any(
        c.startswith(("agentsync sync", "agentsync install-agent", "launchctl")) for c in calls(env)
    )
    assert "ACTION:" not in cp.stdout
    assert last_line(cp).startswith("NEXT: fix the [FAIL] lines above (each names its fix), then re-run: ")
    assert ("status", "done", "1", "fail-lines") in steps(install_log(env))


def test_first_sync_failure_fails_the_run_before_the_agent(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e = {**env, "STUB_SYNC_RC": "78", "STUB_SYNC_OUT": "  alpha: converted 0, deferred 0 online-only"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 1
    # a failed first sync shows everything it printed, not a summary
    assert "stub cycle" in cp.stdout and "  alpha: converted 0, deferred 0 online-only" in cp.stdout
    got = calls(env)
    assert not any(c.startswith(("agentsync install-agent", "launchctl")) for c in got)
    assert "the first sync (agentsync sync --once) exited 78 (configuration invalid)" in cp.stderr
    assert last_line(cp) == (
        f"NEXT: fix what the first sync printed above (configuration invalid), then re-run: "
        f"{rerun(wheel, folder)} [setup report: {report_path(env)}]"
    )
    assert got[-1].startswith("agentsync setup-report --out"), "the report is still written"
    assert steps(install_log(env))[-2:] == [
        ("first-sync", "failed", "78", ""),
        ("report", "done", "0", "agentsync"),
    ]


def test_a_rerun_on_a_configured_mac_says_sync_and_that_background_sync_is_installed(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """Bring-back S22 and S24: on a Mac whose sources.toml already existed the step's lines say "sync", not
    "first sync" (install.log keeps the step name), and a run not asked for the agent step says so when an
    earlier install left the LaunchAgents, from the plist alone: no launchctl call."""
    e = {**env, "STUB_SYNC_OUT": "  alpha: converted 3, deferred 2 online-only"}
    first = install_sh(e, str(wheel), "--source-local", str(folder))
    assert first.returncode == 0, first.stdout + first.stderr
    assert "first sync: alpha: converted 3, deferred 2 online-only" in first.stdout.splitlines()
    assert "background sync:" not in first.stdout, "no LaunchAgent plist: nothing to say"
    plist = Path(env["HOME"]) / "Library" / "LaunchAgents" / "com.agentsync.poll.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")
    Path(env["STUB_LOG"]).unlink()
    second = install_sh(e, str(wheel), "--source-local", str(folder))
    assert second.returncode == 0, second.stdout + second.stderr
    out = second.stdout.splitlines()
    assert "sync: alpha: converted 3, deferred 2 online-only" in out
    assert not any(ln.startswith("first sync") for ln in out), second.stdout
    [line] = [ln for ln in out if ln.startswith("background sync: ")]
    assert "already installed by an earlier run (com.agentsync.poll)" in line
    assert line.endswith("/.local/bin/agentsync install-agent refreshes it")
    assert not any(c.startswith(("launchctl", "agentsync install-agent")) for c in calls(env))
    assert one_next(second)
    done = steps(install_log(env))
    assert done.count(("first-sync", "done", "0", "converted-3-deferred-2")) == 2
    assert done.count(("agent", "skipped", "0", "not-requested")) == 2


def test_first_sync_lock_busy_is_skipped_not_failed(env: dict[str, str], folder: Path, wheel: Path) -> None:
    cp = install_sh(
        {**env, "STUB_SYNC_RC": "75"}, str(wheel), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "first sync: skipped, lock busy" in cp.stdout
    assert ("first-sync", "skipped", "75", "lock-busy") in steps(install_log(env))


def test_confirm_install_agent_without_any_source_exits_1(env: dict[str, str], wheel: Path) -> None:
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    for again in (False, True):  # a fresh install, then a re-run over a config holding only the inbox
        if again:  # the real init's table (KISS K05), with a commented kind line and a literal string
            cfg.write_text(
                '# x\n[agentsync]\n\n[[source]]\nid = "inbox"\n# kind = "local"\n'
                "kind = 'inbox'   # mail\npath = \"/x/inbox\"\n\n[governance]\narchive = true\n"
            )
        cp = install_sh(env, str(wheel), "--confirm-install-agent")
        assert cp.returncode == (1 if again else 2), "KISS K02: a created config with no folder exits 2"
        assert len(re.findall(r"(?m)^kind = .inbox.", cfg.read_text())) == 1, "exactly one inbox"
        assert f"agentsync init --config {cfg}" in calls(env), "the flagless init ensures the inbox"
        assert ("config: " in cp.stdout and "exists (inbox ensured)" in cp.stdout) is again
        assert not any(
            c.startswith(("agentsync sync", "agentsync install-agent", "launchctl")) for c in calls(env)
        )
        if again:
            assert last_line(cp).startswith(
                f"NEXT: choose a folder to sync, then re-run: {INSTALL_SH} {wheel} --confirm-install-agent "
                '--source-local "<folder>"'
            )
        else:
            assert last_line(cp).startswith(
                f"NEXT: no folder to sync yet: list them with {INSTALL_SH} --list-folders"
            )
    cfg.write_text(cfg.read_text() + '\n[[source]]\nid = "work"\nkind = "local"\npath = "/x/work"\n')
    cp = install_sh(env, str(wheel), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr  # a folder source besides the inbox is a source


# ---- the loop's NEXT and the exit codes (KISS K02) ---------------------------------------------------------


def test_no_install_sh_line_says_nothing_is_left() -> None:
    assert "nothing is left" not in INSTALL_SH.read_text(encoding="utf-8")


def test_without_the_flag_the_first_sync_runs_and_ends_on_the_loops_next(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(env, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    got = calls(env)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    assert f"agentsync sync --once --materialise-budget 0 --config {cfg}" in got
    assert not any(c.startswith(("launchctl", "agentsync install-agent")) for c in got), got
    assert not (Path(env["HOME"]) / "Applications").exists(), "no launcher built or copied"
    assert last_line(cp) == f"NEXT: {LOOP_NEXT} [setup report: {report_path(env)}]"
    assert sum("NEXT:" in ln for ln in cp.stdout.splitlines() + cp.stderr.splitlines()) == 1
    assert "Stub Secret Label" not in cp.stdout + cp.stderr
    assert steps(install_log(env))[5:7] == [("status", "done", "0", ""), ("first-sync", "done", "0", "")]
    seen = _env_calls(env)
    assert [ln for ln in seen if not ln.startswith("status no_next=1 ")][-2:] == [
        "status no_next= pending=",
        "setup-report no_next=1 pending=",
    ], "the closing status alone runs with its NEXT line on"


def test_a_sourceless_first_run_exits_2_and_a_rerun_exits_0(env: dict[str, str], wheel: Path) -> None:
    first = install_sh(env, str(wheel))
    assert first.returncode == 2, first.stdout + first.stderr
    assert last_line(first) == (
        f"NEXT: no folder to sync yet: list them with {INSTALL_SH} --list-folders, choose the folders to "
        f'sync, then run: {INSTALL_SH} {wheel} --source-local "<folder>" (one --source-local per folder) '
        f"[setup report: {report_path(env)}]"
    )
    assert not any(c.startswith("agentsync sync") for c in calls(env))
    assert ("first-sync", "skipped", "0", "no-sources") in steps(install_log(env))
    assert one_next(first)
    again = install_sh(env, str(wheel))  # an existing (inbox-only) config: status's NEXT, exit 0
    assert again.returncode == 0, again.stdout + again.stderr
    assert last_line(again) == f"NEXT: {LOOP_NEXT} [setup report: {report_path(env)}]"
    assert one_next(again)
    assert not any(c.startswith("agentsync sync") for c in calls(env))


def test_the_loops_next_falls_back_when_status_prints_none(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(
        {**env, "STUB_STATUS_OUT": "loop: synced", "STUB_STATUS_RC": "2"},
        str(wheel),
        "--source-local",
        str(folder),
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert last_line(cp) == (
        f"NEXT: run {env['HOME']}/.local/bin/agentsync sync and follow its NEXT line "
        f"[setup report: {report_path(env)}]"
    )
    assert one_next(cp)


def test_a_listing_held_after_the_first_sync_exits_1_and_names_the_click(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """Field N8b: the first sync's walk can time out on a read macOS holds for an Allow click in this terminal
    even when status's one-entry listing returned; the closing status's WAITING line then is the NEXT."""
    held = (
        "macOS held the listing of fy26-projects for a privacy prompt: click Allow on the macOS prompt (it "
        "can sit behind other windows), then run `~/.local/bin/agentsync sync`"
    )
    out = f"NEXT: {LOOP_NEXT}\nWAITING ON YOU: {held}"
    cp = install_sh({**env, "STUB_STATUS_OUT": out}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert last_line(cp) == f"NEXT: {held} [setup report: {report_path(env)}]"
    assert one_next(cp) and "WAITING ON YOU:" not in cp.stdout, "the held line is the NEXT, not printed twice"


def test_a_held_listing_beside_the_launchers_tcc_pending_still_exits_1(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The launcher's TCC_PENDING [FAIL] does not block, so it cannot hide a listing held in the terminal."""
    fail = TCC_PENDING_FAIL
    held = "macOS held the listing of fy26-projects for a privacy prompt: click Allow on the macOS prompt"
    out = f"NEXT: {LOOP_NEXT}\nWAITING ON YOU: {held}\n{fail}"
    cp = install_sh(
        {**env, "STUB_STATUS_OUT": out, "STUB_STATUS_RC": "1"}, str(wheel), "--source-local", str(folder)
    )
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert fail in cp.stdout.splitlines()
    assert last_line(cp) == f"NEXT: {held} [setup report: {report_path(env)}]"
    assert one_next(cp)


def test_the_closing_statuss_waits_are_printed_above_its_next(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """Rule 5's NEXT names the WAITING lines: they are printed (above it, so it says so), with one NEXT."""
    rule5 = "stop: the operator confirms the baseline questions (WAITING ON YOU below); session done"
    waits = [
        "WAITING ON YOU: the baseline questions are a draft: in ~/agent-context/docs/_eval, keep about 10 in "
        "questions.md, correct the answers in answers.md, and change both files to status: confirmed",
        "WAITING ON YOU: 2 queued purge(s): run `~/.local/bin/agentsync purge --queue`",
    ]
    out = "\n".join([f"NEXT: {rule5}", *waits, "note: a later sync lists it"])
    cp = install_sh({**env, "STUB_STATUS_OUT": out}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    at = lines.index(waits[0])
    assert lines[at : at + 2] == waits and at < len(lines) - 2
    assert last_line(cp) == (
        "NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU above); session done "
        f"[setup report: {report_path(env)}]"
    )
    assert one_next(cp) and "a later sync lists it" not in cp.stdout
    assert all(w in install_out(env).read_text() for w in waits), "the setup report's install.out has them"


def test_the_launchers_tcc_pending_alone_in_the_closing_status_exits_0(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    fail = TCC_PENDING_FAIL
    out = f"NEXT: the tcc check failed: do what the fix on its [FAIL] line below says\n{fail}"
    cp = install_sh(
        {**env, "STUB_STATUS_OUT": out, "STUB_STATUS_RC": "1"}, str(wheel), "--source-local", str(folder)
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert fail in cp.stdout.splitlines()
    assert last_line(cp).startswith("NEXT: fix the [FAIL] lines above (each names its fix), then run ")
    assert one_next(cp)


def test_a_fail_in_the_closing_status_is_printed_and_named(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    fail = "[FAIL] skill — the agentsync-docs skill is stale (fix: make ~/.claude/skills writable)"
    out = f"NEXT: the skill check failed: do what the fix on its [FAIL] line below says\n{fail}"
    cp = install_sh(
        {**env, "STUB_STATUS_OUT": out, "STUB_STATUS_RC": "1"}, str(wheel), "--source-local", str(folder)
    )
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert fail in cp.stdout.splitlines()
    assert last_line(cp).startswith(
        "NEXT: fix the [FAIL] lines above (each names its fix), then run "
        f"{env['HOME']}/.local/bin/agentsync sync and follow its NEXT line"
    )
    assert "below" not in last_line(cp)
    assert one_next(cp)


def test_uv_installs_the_binary_into_local_bin(env: dict[str, str], folder: Path, wheel: Path) -> None:
    cp = install_sh({**env, "UV_TOOL_BIN_DIR": "/elsewhere/bin"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    seen = set(Path(env["STUB_LOG"] + ".uv").read_text().splitlines())
    assert seen == {f"UV_TOOL_BIN_DIR={env['HOME']}/.local/bin"}, "pinned for every uv call"


# ---- the report at every exit ------------------------------------------------------------------------------


def _headings(text: str) -> list[str]:
    return [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]


def test_failure_without_agentsync_writes_the_shell_report(env: dict[str, str], folder: Path) -> None:
    home = Path(env["HOME"])
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(f"Prompt: v4\nF1 | step 3 | clean | 1 | listed {folder} | -\n")
    cp = install_sh(
        {**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 1
    assert f"report: {report_path(env)} (shell fallback: agentsync is not installed)" in cp.stdout
    assert last_line(cp).startswith("NEXT: fix the error above")
    assert last_line(cp).endswith(f" [setup report: {report_path(env)}]")
    assert cp.stdout.splitlines()[-2] == f"issue link (review the report first): {setup_report.ISSUE_URL}"
    text = report_path(env).read_text()
    assert text.rstrip("\n").splitlines()[-1] == setup_report.ISSUE_URL, "the report ends on the issue form"
    assert (report_path(env).stat().st_mode & 0o777) == 0o600
    assert _headings(text) == list(setup_report.SECTION_TITLES), "the same headings as agentsync's report"
    assert text.startswith(setup_report.REPORT_TITLE + "\n")
    assert "exit 1 (failed)" in text and "uv tool install failed" in text and "- launchd: real" in text
    assert "step=agentsync" in text and "result=failed" in text, "the install.log lines are embedded"
    assert (
        "- macOS: " in text
        and "- arch: " in text
        and "MDM enrollment" in text
        and "- proxy environment: " in text
    )
    assert "- ~/Library/CloudStorage providers: 1 (OneDrive: 1)" in text
    friction_part = text.split("\n## Agent friction log\n", 1)[1]
    assert (
        "Prompt: v4" in friction_part
        and "listed ~/Library/CloudStorage/OneDrive-<org-1>/<folder-" in friction_part
    )
    for secret in (env["HOME"], "Contoso", "FY26 Projects", pwd.getpwuid(os.getuid()).pw_name):
        assert secret not in text, secret
    full_name = subprocess.run(["id", "-F"], capture_output=True, text=True, check=False).stdout.strip()
    if len(full_name) >= 3:
        assert full_name not in text


def test_the_shell_report_redacts_a_full_name_written_last_comma_first(
    env: dict[str, str], folder: Path
) -> None:
    """macOS keeps the comma of "Doe, Jane": the shell fallback splits the name there too (field report
    2026-10-10)."""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        '#!/bin/bash\n[ "$1" = -F ] && { echo "Doe, Jane"; exit 0; }\nexec /usr/bin/id "$@"\n',
    )
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v4\nF1 | step 3 | clean | 1 | Jane chose; Doe, Jane approved; Doe agreed | -\n"
    )
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder))
    assert cp.returncode == 1
    text = report_path(env).read_text()
    assert "<name> chose; <name> approved; <name> agreed" in text
    assert "Jane" not in text and "Doe" not in text


def test_usage_error_after_parsing_still_writes_the_report(env: dict[str, str], tmp_path: Path) -> None:
    cp = install_sh(env, "--source-local", str(tmp_path / "nowhere"))
    assert cp.returncode == 2
    assert "usage error: --source-local is not an existing folder" in cp.stderr
    assert report_path(env).is_file() and "usage error" in report_path(env).read_text()
    assert last_line(cp).startswith("NEXT: fix the usage error above")
    assert [c for c in calls(env) if not c.startswith("launchctl print")] == [], "no step ran"
    assert re.search(r" end rc=2 seconds=\d+$", install_log(env)[-1])


def test_a_parse_error_writes_no_report(env: dict[str, str]) -> None:
    cp = install_sh(env, "--bogus")
    assert cp.returncode == 2 and "NEXT:" not in cp.stdout
    assert list(Path(env["HOME"]).iterdir()) == []


def test_report_only_runs_just_the_report(env: dict[str, str]) -> None:
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    out = home / "elsewhere" / "report.md"
    cp = install_sh({**env, "AGENTSYNC_SETUP_REPORT": str(out)}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    cfg = home / "agent-context" / "sources.toml"
    assert calls(env) == [f"agentsync setup-report --out {out} --config {cfg}"]
    setup = home / "agent-context" / "setup"
    assert out.is_file() and not (setup / "install.log").exists(), "no install.log for --report-only"
    assert cp.stdout.splitlines()[-2] == LINK_LINE
    assert last_line(cp) == (
        f"NEXT: review {out.parent}/bring-back.md and copy that one file back privately, or paste the setup "
        f"report into the issue the link above opens (nothing is sent for you) [setup report: {out}]"
    )


def test_the_shell_report_only_redacts_the_folders_earlier_runs_added(
    env: dict[str, str], folder: Path
) -> None:
    """``--report-only`` names no folder, so the shell fallback reads them from sources.toml and from
    install.log's args (rehearsal 2026-10-10: the folder names were in clear)."""
    home = Path(env["HOME"])
    org = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    beta, gamma = org / "Client Beta", org / "Gamma Plans"
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'[[source]]\nid = "client-beta"\nkind = "local"\npath = "{beta}"\n', encoding="utf-8")
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)
    escaped = str(gamma).replace(" ", "\\ ")
    head = "run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- "
    (setup / "install.log").write_bytes(
        f"2026-10-09T01:00:00Z {head}source=~/src/Cafe\xcc args=--source-local /tmp/x\n".encode("latin-1")
        + f"2026-10-10T01:00:00Z {head}source=- args=--source-local {escaped}\n".encode()
    )
    (setup / "friction.md").write_text(
        "Prompt: v10\nF1 | step 1 | clean | 1 | Client Beta and Gamma Plans | -\n"
    )
    cp = install_sh({**env, "LC_ALL": "en_US.UTF-8"}, "--report-only")  # BSD sed stops at a bad byte there
    assert cp.returncode == 0, cp.stderr
    assert "(shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_bytes().decode("utf-8", "replace")  # the log tail keeps the stray byte
    for name in ("Client Beta", "Gamma Plans", "Gamma\\ Plans", "Contoso"):
        assert name not in text, name


def test_report_only_writes_one_bring_back_file(env: dict[str, str]) -> None:
    """Field report 2026-10-05: one file comes back, not three. bring-back.md holds the redacted report, the
    fix request and step 1's local-work patch, 0600 beside the report."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    setup = home / "agent-context" / "setup"
    (setup / "local-work").mkdir(parents=True)
    (setup / "fix-request.md").write_text(
        "- OCR scanned PDFs: they convert to empty pages\n", encoding="utf-8"
    )
    (setup / "local-work" / "0001-local.patch").write_text("+def ocr(): ...\n", encoding="utf-8")
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    back = report_path(env).parent / "bring-back.md"
    text = back.read_text(encoding="utf-8")
    assert back.stat().st_mode & 0o777 == 0o600
    assert text.index("## 1. Setup report") < text.index(report_path(env).read_text(encoding="utf-8")[:30])
    assert "- OCR scanned PDFs: they convert to empty pages" in text.split("## 2. Fix request", 1)[1]
    # Field report 2026-10-06: the file's own headings named the setup folder by the home path, which holds
    # the login name, and its first line promised more than "private".
    assert "## 2. Fix request (~/agent-context/setup/fix-request.md)\n" in text
    assert "## 3. Local work kept by setup prompt step 1 (~/agent-context/setup/local-work)\n" in text
    assert not [ln for ln in text.splitlines() if ln.startswith("#") and env["HOME"] in ln]
    assert text.splitlines()[2] == (
        "Private: copy this file back as it is, and never paste it into the public issue form."
    )
    assert "Sections 2 and 3 are not: they name real folders and files" in text
    assert "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~" in text.split("## 3. Local work", 1)[1]
    assert f"bring back: {back} (one file: report, fix request, local work)" in cp.stdout

    (setup / "fix-request.md").unlink()
    (setup / "local-work" / "0001-local.patch").unlink()
    install_sh(env, "--report-only")
    text = back.read_text(encoding="utf-8")
    assert text.split("## 2. Fix request", 1)[1].split("\n\n", 2)[1] == "none"
    assert text.rstrip().endswith("none")


# ---- section 3 of the bring-back file: local work is sent once ---------------------------------------------

PATCH_NAME = "0001-local-changes-kept-before-update.patch"
"""The file step 1's keep command writes: its commit subject is constant, so a second keep overwrites it."""
ENDED = "2026-10-06T16:46:11Z"
"""When the earlier attempt of these tests reached its report (its ``end | finished`` line)."""


def _stamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def _keep_patch(
    env: dict[str, str], written: str, body: str = "+def ocr(): ...\n", name: str = PATCH_NAME
) -> Path:
    """A patch in the setup folder's local-work, last written at ``written`` (UTC)."""
    patch = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / name
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(body, encoding="utf-8")
    os.utime(patch, (_stamp(written), _stamp(written)))
    return patch


def _two_attempts(env: dict[str, str], *, ended: str = f"{ENDED} | end | finished", extra: str = "") -> None:
    """A friction log of an attempt that reached its report (``ended``, its closing line) and a later one
    that is still open."""
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: x\n{extra}{ended}\n"
        f"Attempt: 2026-10-07T15:58:32Z\nPrompt: v{COMPAT}\nAgent: x\n",
        encoding="utf-8",
    )


def _local_work(env: dict[str, str]) -> str:
    """Section 3 of the bring-back file a ``--report-only`` run writes, without its heading."""
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    heading = "## 3. Local work kept by setup prompt step 1 (~/agent-context/setup/local-work)\n\n"
    return text.split(heading, 1)[1]


def _not_repeated(patch: Path, written: str, ended: str = ENDED) -> str:
    """The one line section 3 has for a patch it does not send again."""
    data = patch.read_bytes()
    lines, sha = data.count(b"\n"), hashlib.sha256(data).hexdigest()[:12]
    return (
        f"not repeated: a patch file last written {written}, at or before an earlier attempt's report "
        f"({ended}): {lines} lines, sha256 {sha}; still in ~/agent-context/setup/local-work\n"
    )


def test_bring_back_does_not_repeat_local_work_an_earlier_attempt_reported(env: dict[str, str]) -> None:
    """Field report 2026-10-07: the bring-back file carried the same 3,587-line patch a second time, a round
    after it had been rebuilt, because section 3 was every patch in the folder. A patch last written at or
    before an earlier attempt reached its report is not repeated. One line gives its time, its line count
    and the start of its SHA-256, so whoever receives the file can check it against what they hold, and says
    where it still is. Nothing is deleted or moved."""
    _two_attempts(env)
    written = (
        "2026-10-06T16:42:39Z"  # the field's order: 5 s before the Attempt: line of the attempt that kept it
    )
    patch = _keep_patch(env, written, "+def ocr(): ...\n+    return None\n")
    before = (patch.read_bytes(), patch.stat().st_mtime_ns)
    section = _local_work(env)
    assert section == _not_repeated(patch, written)
    assert ": 2 lines, sha256 " in section
    assert "~~~~~~~~~~" not in section and "def ocr" not in section, "the patch itself is not sent again"
    assert env["HOME"] not in section, "the line names the folder with ~: the home path holds the login name"
    assert "carried" not in section and "sent" not in section, "this Mac cannot know what was copied back"
    assert (patch.read_bytes(), patch.stat().st_mtime_ns) == before
    assert _local_work(env) == section, "a second report of the same attempt says the same"

    _keep_patch(env, ENDED)
    assert _local_work(env) == _not_repeated(patch, ENDED), "written in the report's own second: before it"


def test_bring_back_sends_local_work_kept_since_the_earlier_report(env: dict[str, str]) -> None:
    """Step 1's keep command runs before the command that logs the ``Attempt:`` line, so a fresh patch is a
    few seconds OLDER than the attempt that kept it. It is still new: it was written after the earlier
    attempt's report. A second report in the same attempt sends it again, since the attempt's own end line
    never counts (the field ran the report twice in one attempt)."""
    _two_attempts(env)
    _keep_patch(env, "2026-10-07T15:58:27Z")
    sent = "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"
    assert _local_work(env) == sent
    assert friction_path(env).read_text().splitlines()[-1].endswith(" | end | finished"), "now closed"
    assert _local_work(env) == sent


def test_bring_back_names_older_local_work_beside_the_new(env: dict[str, str]) -> None:
    """One patch from before the earlier report and one kept since: only the new one is fenced, and the
    older one has its line after the fence."""
    _two_attempts(env)
    old = _keep_patch(env, "2026-10-06T16:42:39Z", "+old = 1\n", "0001-old.patch")
    _keep_patch(env, "2026-10-07T15:58:27Z", "+new = 2\n", "0002-new.patch")
    assert _local_work(env) == (
        "~~~~~~~~~~diff\n+new = 2\n~~~~~~~~~~\n" + _not_repeated(old, "2026-10-06T16:42:39Z")
    )


@pytest.mark.parametrize(
    ("log", "why"),
    [
        (None, "no friction log: nothing says an earlier attempt reported"),
        (
            f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: x\n{ENDED} | end | finished\n",
            "one attempt",
        ),
        (
            f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: x\nyesterday | end | finished\n"
            f"Attempt: 2026-10-07T15:58:32Z\nPrompt: v{COMPAT}\nAgent: x\n",
            "an end line with no time is no moment to compare with",
        ),
        (
            f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: x\n"
            f"Attempt: 2026-10-07T15:58:32Z\nPrompt: v{COMPAT}\nAgent: x\n",
            "the earlier attempt never reached its report",
        ),
    ],
)
def test_bring_back_sends_local_work_whenever_it_cannot_tell(
    env: dict[str, str], log: str | None, why: str
) -> None:
    """Every doubt sends the patch, as before this rule: leaving out work nobody received costs a round."""
    if log is not None:
        friction_path(env).parent.mkdir(parents=True)
        friction_path(env).write_text(log, encoding="utf-8")
    _keep_patch(env, "2026-10-06T16:42:39Z")
    assert _local_work(env) == "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n", why


def test_bring_back_does_not_count_the_report_of_a_stopped_copy(env: dict[str, str]) -> None:
    """An attempt ``--log-start`` stopped (a copy of the prompt that is not this installer's) closes itself,
    and its report's NEXT says not to bring it back. So its end line is no report that took the patch."""
    stop = install_sh(env, "--log-start", "Claude Code, claude-opus-5-5")
    assert stop.returncode == 2, stop.stderr
    stopped = friction_path(env).read_text(encoding="utf-8")
    assert stopped.splitlines()[-1].endswith(" | end | finished")
    friction_path(env).write_text(
        re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ENDED, stopped)
        + f"Attempt: 2026-10-07T15:58:32Z\nPrompt: v{COMPAT}\nAgent: x\n",
        encoding="utf-8",
    )
    _keep_patch(env, "2026-10-06T16:42:39Z")
    assert _local_work(env) == "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"


def test_bring_back_names_local_work_whose_first_report_nobody_copied(env: dict[str, str]) -> None:
    """The rule's known limit, held as documented: this Mac cannot know what was copied back. A session
    keeps work and reports, nobody copies that file, and a second session starts. Its bring-back file
    replaces the first and does not hold the patch. The line is what shows it: the receiver holds no patch
    with that hash, and the file is still where the line says."""
    first = install_sh(env, "--log-start", started("Claude Code, claude-opus-5-5"))
    assert first.returncode == 0, first.stderr
    patch = _keep_patch(env, "2026-10-06T10:00:00Z")
    assert _local_work(env) == "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n", "the first report sends it"
    second = install_sh(env, "--log-start", started("Claude Code, claude-opus-5-5"))
    assert second.returncode == 0, second.stderr
    ended = next(
        ln[:20] for ln in friction_path(env).read_text().splitlines() if ln.endswith(" | end | finished")
    )
    assert _local_work(env) == _not_repeated(patch, "2026-10-06T10:00:00Z", ended)
    assert patch.read_text(encoding="utf-8") == "+def ocr(): ...\n", "still on this Mac"


def test_report_only_falls_back_when_setup_report_fails(env: dict[str, str]) -> None:
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    cp = install_sh({**env, "STUB_REPORT_RC": "2"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert "shell fallback: agentsync setup-report failed with exit 2" in cp.stdout
    assert "agentsync setup-report failed (usage error)" in cp.stderr
    assert report_path(env).read_text().startswith("# agentsync setup report\n")


def test_the_shell_report_shows_an_exclude_list_as_path(env: dict[str, str]) -> None:
    """The loop's wait for an empty cloud folder prints an exclude line naming folders below a source root,
    which the shell report's redaction has never seen. An agent may log that line: the list is shown as
    <path>, as agentsync setup-report shows it, also when the line was cut inside the list."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    said = (
        'set exclude = ["~$*", "/Fabrikam Bids/", "/Plans/Tailspin [[]old]/"] in [[source]] id = "one" in '
        "~/agent-context/sources.toml"
    )
    (setup / "friction.md").write_text(
        f"Attempt: 2026-09-29T09:58:00Z\nPrompt: v{COMPAT}\nAgent: x\n"
        f"2026-09-29T10:00:00Z | step 2 | deviation | status said: {said} | -\n"
        '2026-09-29T10:00:01Z | step 2 | deviation | and then: set exclude = ["~$*", "/Fabrikam Bi\n',
        encoding="utf-8",
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert f"report: {report_path(env)} (shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_text(encoding="utf-8")
    assert (
        'status said: set exclude = [<path>] in [[source]] id = "one" in ~/agent-context/sources.toml | -\n'
        in text
    )
    assert "and then: set exclude = [<path>]\n" in text
    assert "Fabrikam" not in text and "Tailspin" not in text


def test_report_only_keeps_an_earlier_friction_log(env: dict[str, str]) -> None:
    report_path(env).parent.mkdir(parents=True)
    report_path(env).write_text(
        "# agentsync setup report\n\n## Agent friction log\n\nOutcome: failed at step 2\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert not install_out(env).exists(), "K17: --report-only writes no install.out"
    text = report_path(env).read_text()
    friction = text.split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
    assert friction.strip() == "Outcome: failed at step 2"


# ---- the sandbox seam and the dry run (AGENTSYNC_INSTALL_DRY_RUN=1) ----------------------------------------


def test_simulated_launchd_touches_no_launchctl(env: dict[str, str], folder: Path, wheel: Path) -> None:
    e = {**env, "AGENTSYNC_SIMULATE_LAUNCHD": "1", "STUB_REPORT_RC": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    got = calls(env)
    assert not any(c.startswith(("launchctl", "agentsync install-agent")) for c in got), got
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    assert f"agentsync sync --once --materialise-budget 0 --config {cfg}" in got
    simulated = [ln for ln in cp.stdout.splitlines() if ln.startswith("SIMULATED:")]
    assert len(simulated) == 4 and "no launchctl call" in simulated[2]
    assert last_line(cp).startswith(
        f"NEXT: background sync: simulated (AGENTSYNC_SIMULATE_LAUNCHD=1, no job runs); {LOOP_NEXT} "
    )
    log = install_log(env)
    assert " launchd=simulated args=" in log[0]
    assert ("agent", "done", "0", "simulated") in steps(log) and ("wait", "done", "0", "simulated") in steps(
        log
    )
    text = report_path(env).read_text()  # the shell report (the stub setup-report failed)
    assert "- launchd: simulated" in text and "AGENTSYNC_SIMULATE_LAUNCHD=1" in text
    assert not (Path(env["HOME"]) / "Library" / "LaunchAgents").exists()


def test_dry_run_writes_nothing(env: dict[str, str], folder: Path, wheel: Path) -> None:
    home = Path(env["HOME"])
    before = sorted(home.rglob("*"))
    dry = {**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}
    cp = install_sh(dry, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stderr
    assert sorted(home.rglob("*")) == before
    assert calls(env) == ["uv tool dir --bin", "uv tool dir"], "only read-only uv queries run"
    out = cp.stdout
    for planned in (
        " sync --once --materialise-budget 0 --config ",
        " install-agent --config ",
        f" kickstart gui/{UID}/com.agentsync.poll",
        f" setup-report --out {report_path(env)} ",
    ):
        assert any(ln.startswith("[dry-run]") and planned in ln for ln in out.splitlines()), planned
    assert last_line(cp) == "NEXT: re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to apply the steps above"


# ---- the re-run skips an agentsync that is already current -------------------------------------------------


def _have_git() -> bool:
    cp = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, check=False)
    return cp.returncode == 0 and Path(cp.stdout.strip()).is_dir()


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
def test_rerun_skips_reinstalling_the_same_clean_commit(env: dict[str, str], tmp_path: Path) -> None:
    src = tmp_path / "checkout"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "agentsync"\nversion = "0.1.0"\n')
    git = ["/usr/bin/git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for argv in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run([*git, *argv], check=True, capture_output=True)
    first = install_sh(env, str(src))
    assert first.returncode == 2, first.stderr  # KISS K02: it created the config and has no folder to sync
    second = install_sh(env, str(src))
    assert second.returncode == 0, second.stderr
    assert sum(c.startswith("uv tool install") for c in calls(env)) == 1
    assert "(current: installed from commit " in second.stdout
    assert ("agentsync", "skipped", "0", "current") in steps(install_log(env))
    (src / "pyproject.toml").write_text(
        '[project]\nname = "agentsync"\nversion = "0.1.1"\n'
    )  # dirty: reinstall
    third = install_sh(env, str(src))
    assert third.returncode == 0 and sum(c.startswith("uv tool install") for c in calls(env)) == 2


# ---- --version names local changes (J20) -------------------------------------------------------------------


def _git_checkout(src: Path) -> list[str]:
    """A committed git checkout at ``src``; the git argv prefix for it (read-only env is the caller's)."""
    git = ["/usr/bin/git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for argv in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run([*git, *argv], check=True, capture_output=True)
    return git


def _fingerprint(git: list[str]) -> str:
    """What a maintainer runs to match a report: `git diff HEAD | shasum -a 256 | cut -c1-12`."""
    diff = subprocess.run([*git, "diff", "HEAD", "--"], check=True, capture_output=True).stdout
    assert diff, "the checkout has local changes"
    return hashlib.sha256(diff).hexdigest()[:12]


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
def test_version_of_a_dirty_checkout_names_its_diff(tmp_path: Path) -> None:
    src = tmp_path / "checkout"
    (src / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, src / "scripts" / "install.sh")
    git = _git_checkout(src)
    sha = subprocess.run(
        [*git, "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    )
    script = [BASH32, str(src / "scripts" / "install.sh"), "--version"]
    clean = subprocess.run(script, capture_output=True, text=True, check=True)
    assert clean.stdout.splitlines()[0] == f"source commit: {sha.stdout.strip()}"
    with (src / "scripts" / "install.sh").open("a") as fh:
        fh.write("# a local change\n")
    dirty = subprocess.run(script, capture_output=True, text=True, check=True)
    assert dirty.stdout.splitlines()[0] == (
        f"source commit: {sha.stdout.strip()} dirty {_fingerprint(git)} "
        "(local changes in this checkout; setup prompt step 1 keeps them on a local branch before it updates)"
    )


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
def test_install_log_start_line_names_the_diff_of_a_dirty_source(env: dict[str, str], tmp_path: Path) -> None:
    src = tmp_path / "checkout"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "agentsync"\nversion = "0.1.0"\n')
    git = _git_checkout(src)
    sha = subprocess.run(
        [*git, "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    )
    (src / "pyproject.toml").write_text('[project]\nname = "agentsync"\nversion = "0.1.1"\n')
    index = src / ".git" / "index"
    before = index.stat().st_mtime_ns
    cp = install_sh(env, str(src))
    assert cp.returncode == 2, cp.stdout + cp.stderr  # KISS K02: a created config with no folder to sync
    start = install_log(env)[0]
    assert f" commit={sha.stdout.strip()}-dirty tree={_fingerprint(git)} kind=checkout " in start
    fields = dict(re.findall(r"(\w+)=(\S+)", start))
    assert fields["commit"] == f"{sha.stdout.strip()}-dirty" and fields["tree"] == _fingerprint(git), (
        "every start-line field is key=value (K6)"
    )
    assert index.stat().st_mtime_ns == before, "install.sh ran git read-only (GIT_OPTIONAL_LOCKS=0)"


# ---- --list-folders (J9) -----------------------------------------------------------------------------------


def _cloud(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / "Library" / "CloudStorage"


def test_list_folders_prints_depth_2_and_3_folders_sorted(env: dict[str, str]) -> None:
    cs = _cloud(env)
    for d in (
        "OneDrive-Contoso/FY26 Projects/Alpha/too deep",
        "OneDrive-Contoso/Documents",
        "OneDrive-Contoso/.Trash/x",
        "OneDrive-Contoso/Documents/.hidden",
        "SharedLibraries-Contoso/Team Site - Docs",
    ):
        (cs / d).mkdir(parents=True)
    (cs / "OneDrive-Contoso" / "Budget.xlsx").write_text("not a folder")
    (cs / ".DS_Store").write_text("")
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [
        f"{cs}/OneDrive-Contoso/Documents",
        f"{cs}/OneDrive-Contoso/FY26 Projects",
        f"{cs}/OneDrive-Contoso/FY26 Projects/Alpha",
        f"{cs}/SharedLibraries-Contoso/Team Site - Docs",
    ]
    assert lines[-1] == (
        "NEXT: choose the folders to sync from the list above (project folders rather than a whole library), "
        f'then run: {INSTALL_SH} --source-local "<folder>" (one --source-local per folder)'
    ), "KISS K11b: the setup path never passes --confirm-install-agent"
    assert calls(env) == [], "no uv, agentsync or launchctl: names only"
    assert not report_path(env).exists(), "no report for --list-folders"
    log = install_log(env)
    assert f" start install.sh compat={COMPAT} " in log[0] and log[0].endswith(" args=--list-folders")
    assert steps(log) == [("list-folders", "done", "0", "listed-4")]
    assert re.search(r" end rc=0 seconds=\d+$", log[-1])


def test_list_folders_caps_the_list_at_200(env: dict[str, str]) -> None:
    lib = _cloud(env) / "OneDrive-Contoso"
    for i in range(205):
        (lib / f"P{i:03d}").mkdir(parents=True)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stderr
    lines = cp.stdout.splitlines()
    assert len(lines) == 202 and lines[199] == f"{lib}/P199" and lines[200] == "(5 more)"
    assert steps(install_log(env)) == [("list-folders", "done", "0", "listed-205")]


@pytest.mark.parametrize("cloud_storage", ["absent", "empty"])
def test_list_folders_without_a_provider_says_onedrive_is_not_signed_in(
    env: dict[str, str], cloud_storage: str
) -> None:
    if cloud_storage == "empty":
        (_cloud(env) / ".hidden").mkdir(parents=True)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 3
    assert cp.stdout.splitlines() == [
        "NEXT: OneDrive is not signed in on this Mac: sign in to OneDrive, then re-run: "
        f"{INSTALL_SH} --list-folders"
    ]
    assert steps(install_log(env)) == [("list-folders", "failed", "3", "not-signed-in")]
    assert not report_path(env).exists()


STUB_FIND_EPERM = """#!/bin/bash
for a in "$@"; do
  case "$a" in
    */OneDrive-*) echo "find: $a: Operation not permitted" >&2; exit 1 ;;
  esac
done
exec /usr/bin/find "$@"
"""


def test_list_folders_names_the_click_when_this_terminal_was_denied(env: dict[str, str]) -> None:
    """macOS answers a denied terminal with EPERM ("Operation not permitted"): not "not signed in"."""
    (_cloud(env) / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    (_cloud(env) / "Dropbox" / "Shared").mkdir(parents=True)
    _write_exe(Path(env["PATH"].split(":")[0]) / "find", STUB_FIND_EPERM)
    cp = install_sh({**env, "TERM_PROGRAM": "Apple_Terminal"}, "--list-folders")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [f"{_cloud(env)}/Dropbox/Shared"], "what can be read is still listed"
    assert lines[-1] == (
        "NEXT: this terminal app (Terminal) was denied access to files managed by OneDrive: allow it in "
        "System Settings > Privacy & Security > Files and Folders (turn on OneDrive under this terminal app "
        f"(Terminal); a click, not a command), then re-run: {INSTALL_SH} --list-folders"
    )
    assert "not signed in" not in cp.stdout
    assert steps(install_log(env)) == [("list-folders", "failed", "4", "denied")]


def test_list_folders_stops_waiting_on_a_prompt_nobody_answers(env: dict[str, str]) -> None:
    (_cloud(env) / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    _write_exe(Path(env["PATH"].split(":")[0]) / "find", "#!/bin/bash\nexec sleep 30\n")
    cp = install_sh({**env, "AGENTSYNC_LIST_TIMEOUT": "1"}, "--list-folders", timeout=20)
    assert cp.returncode == 4
    assert last_line(cp).startswith(
        "NEXT: macOS is asking whether this terminal app may access files managed by OneDrive: click Allow"
    )
    assert steps(install_log(env)) == [("list-folders", "failed", "4", "tcc-pending")]


def test_list_folders_takes_no_other_option(env: dict[str, str], folder: Path) -> None:
    cp = install_sh(env, "--list-folders", "--source-local", str(folder))
    assert cp.returncode == 2 and "--list-folders takes no other option" in cp.stderr
    assert calls(env) == [] and not report_path(env).exists()


# ---- --list-folders on a Mac that already syncs folders (field report 2026-10-07) --------------------------


def _tool_python(env: dict[str, str]) -> Path:
    """The installed agentsync's interpreter, where install.sh looks for it (uv's tool folder): a wrapper
    that starts the Python running these tests, which has agentsync."""
    py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    return _write_exe(py, f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')


def _write_config(env: dict[str, str], *tables: str) -> Path:
    """sources.toml as ``agentsync add-source`` writes it: the template, then one table per source."""
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(default_config_text() + "".join(tables), encoding="utf-8")
    return cfg


def _inbox(env: dict[str, str]) -> str:
    return inbox_source_table("inbox", Path(env["HOME"]) / "agent-context" / "inbox")


LIST_NEXT = (
    "NEXT: choose the folders to sync from the list above (project folders rather than a whole library), "
    f'then run: {INSTALL_SH} --source-local "<folder>" (one --source-local per folder)'
)
"""--list-folders' NEXT: on a Mac that syncs no folder yet."""


def test_list_folders_without_a_synced_folder_prints_what_it_always_did(env: dict[str, str]) -> None:
    """A new Mac is asked which folders to sync, so its list is byte for byte what it was before a set-up
    Mac's folders were marked: with no config (the installed agentsync is not even asked), and with a
    config that holds the inbox alone."""
    cs = _cloud(env)
    for d in ("OneDrive-Contoso/FY26 Projects/Alpha", "OneDrive-Contoso/Documents"):
        (cs / d).mkdir(parents=True)
    listed = [
        f"{cs}/OneDrive-Contoso/Documents",
        f"{cs}/OneDrive-Contoso/FY26 Projects",
        f"{cs}/OneDrive-Contoso/FY26 Projects/Alpha",
    ]
    before = "\n".join([*listed, LIST_NEXT]) + "\n"
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stdout, cp.stderr) == (0, before, "")
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stdout, cp.stderr) == (0, before, "")
    assert calls(env) == [], "no config: uv is not asked where the installed agentsync is"
    assert steps(install_log(env))[-1] == ("list-folders", "done", "0", "listed-3")
    assert " synced=" not in install_log(env)[-2]
    _write_config(env, _inbox(env))
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stdout, cp.stderr) == (0, before, "")
    assert install_log(env)[-2].endswith(" result=done note=listed-3 synced=0"), "the config was read"


def test_list_folders_marks_the_folders_this_mac_already_syncs(env: dict[str, str]) -> None:
    """Field report 2026-10-07: a Mac that already ran setup was asked for its folders again, and an
    unattended agent stopped there. The folders are in the config, so the list says which they are: one
    line first, and a mark before each. A folder counts by the path agentsync syncs it under (a source
    written through OneDrive's own link in the home folder is the folder under CloudStorage). A paused
    source and the inbox are not folders it syncs, and a source deeper than the list goes is counted and
    said to be outside it.

    A sync reads the whole tree under a synced folder. So a listed folder inside one is synced too, and
    one that holds one would be read twice if it were added: each has a mark of its own, and only a folder
    no sync reads is left unmarked (the list's NEXT names those as the ones to add)."""
    cs = _cloud(env)
    for d in (
        "OneDrive-Contoso/FY26 Projects/Alpha/Deep",
        "OneDrive-Contoso/FY26 Projects/Beta",
        "OneDrive-Contoso/Documents/Plans",
        "SharedLibraries-Contoso/Team Site - Docs",
    ):
        (cs / d).mkdir(parents=True)
    link = Path(env["HOME"]) / "OneDrive - Contoso"
    link.symlink_to(cs / "OneDrive-Contoso")
    projects = cs / "OneDrive-Contoso" / "FY26 Projects"
    paused = local_source_table("beta", projects / "Beta").replace(
        'kind = "local"\n', 'kind = "local"\nstate = "paused"\n'
    )
    _write_config(
        env,
        local_source_table("alpha", projects / "Alpha"),
        local_source_table("documents", link / "Documents"),
        paused,
        _inbox(env),
    )
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    keep = (
        f"NEXT: this Mac already syncs 2 folder(s), and a re-run keeps them: run {INSTALL_SH}, with one "
        '--source-local "<folder>" for each unmarked folder to add from the list above (none is needed)'
    )
    assert cp.stdout.splitlines() == [
        "already synced on this Mac: 2 folder(s) (marked [synced] below)",
        f"[synced] {cs}/OneDrive-Contoso/Documents",
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Documents/Plans",
        f"[contains a synced folder] {cs}/OneDrive-Contoso/FY26 Projects",
        f"[synced] {cs}/OneDrive-Contoso/FY26 Projects/Alpha",
        f"{cs}/OneDrive-Contoso/FY26 Projects/Beta",
        f"{cs}/SharedLibraries-Contoso/Team Site - Docs",
        keep,
    ]
    assert calls(env) == ["uv tool dir"], "uv says where the installed agentsync is; nothing is installed"
    assert steps(install_log(env)) == [("list-folders", "done", "0", "listed-6")]
    assert install_log(env)[-2].endswith(" result=done note=listed-6 synced=2")
    assert not report_path(env).exists()
    # A synced folder the list does not reach is printed too, so the reader can name every one: first,
    # under the line that counts them, with the same mark and the path the config has.
    notes = Path(env["HOME"]) / "Documents" / "notes"
    notes.mkdir(parents=True)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + local_source_table("deep", projects / "Alpha" / "Deep")
        + local_source_table("notes", notes),
        encoding="utf-8",
    )
    listed = cp.stdout.splitlines()[1:-1]
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines() == [
        "already synced on this Mac: 4 folder(s) (marked [synced] below: first the 2 outside the list, then "
        "the list)",
        f"[synced] {notes}",
        f"[synced] {cs}/OneDrive-Contoso/FY26 Projects/Alpha/Deep",
        *listed,
        keep.replace("syncs 2 folder(s)", "syncs 4 folder(s)"),
    ]
    assert sum(ln.startswith("[synced] ") for ln in cp.stdout.splitlines()) == 4, "each one, once"
    assert install_log(env)[-2].endswith(" result=done note=listed-6 synced=4")


def test_list_folders_names_a_synced_folder_when_the_list_reaches_none(env: dict[str, str]) -> None:
    """A Mac whose only synced folder is outside ~/Library/CloudStorage: the line said "1 folder(s) (0
    marked [synced] below; 1 not in this list)" over a list with no mark, so nobody could say which folder
    was kept without reading sources.toml (review, 2026-10-07). It is printed, with its mark."""
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    notes = Path(env["HOME"]) / "Documents" / "notes"
    notes.mkdir(parents=True)
    _write_config(env, local_source_table("notes", notes), _inbox(env))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below: first the 1 outside the list, then "
        "the list)",
        f"[synced] {notes}",
        f"{cs}/OneDrive-Contoso/FY26 Projects",
    ]
    assert last_line(cp).startswith("NEXT: this Mac already syncs 1 folder(s), and a re-run keeps them: ")


def test_list_folders_marks_a_synced_folder_written_in_another_case_or_unicode_form(
    env: dict[str, str],
) -> None:
    """macOS takes a name in any case, and in either Unicode form, for the same folder, and so does a sync.
    A config that names a folder in lower case, or with the é an agent types (one code point) where the
    disk holds an e and an accent, still syncs that folder: it is marked, not offered as one to add."""
    cs = _cloud(env)
    on_disk, typed = (unicodedata.normalize(form, "Présentations") for form in ("NFD", "NFC"))
    assert on_disk != typed
    for d in ("OneDrive-Contoso/Documents/Plans", f"OneDrive-Contoso/{on_disk}"):
        (cs / d).mkdir(parents=True)
    lower = Path(env["HOME"]) / "library" / "cloudstorage" / "onedrive-contoso" / "documents"
    _write_config(
        env,
        local_source_table("documents", lower),
        local_source_table("slides", cs / "OneDrive-Contoso" / typed),
    )
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 2 folder(s) (marked [synced] below)",
        f"[synced] {cs}/OneDrive-Contoso/Documents",
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Documents/Plans",
        f"[synced] {cs}/OneDrive-Contoso/{on_disk}",
    ]
    assert install_log(env)[-2].endswith(" result=done note=listed-3 synced=2")


@pytest.mark.parametrize("cloud_storage", ["absent", "no folder yet"])
def test_list_folders_keeps_a_set_up_mac_that_has_nothing_to_list(
    env: dict[str, str], cloud_storage: str
) -> None:
    """A Mac that is set up, with nothing to list: OneDrive is signed out, or its folder is still empty,
    and the config syncs a folder outside ~/Library/CloudStorage. The config was read only when there was a
    line to mark, so this Mac got the new Mac's exit 3 and "sign in to OneDrive". The prompt then goes to
    the report without step 2: no update and no sync, though install.sh alone keeps that folder and syncs
    it (review, 2026-10-07). The list now says what is synced, exits 0 and names that command. A Mac that
    syncs no folder still gets exit 3 and the line it always got."""
    notes = Path(env["HOME"]) / "Documents" / "notes"
    notes.mkdir(parents=True)
    why = "OneDrive is not signed in on this Mac"
    if cloud_storage == "no folder yet":
        (_cloud(env) / "OneDrive-Contoso").mkdir(parents=True)
        why = f"no folders are synced yet in {_cloud(env)}"
    cfg = _write_config(env, local_source_table("notes", notes), _inbox(env))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines() == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below: first the 1 outside the list, then "
        "the list)",
        f"[synced] {notes}",
        f"NEXT: this Mac already syncs 1 folder(s), and a re-run keeps them: run {INSTALL_SH} (no folder to "
        f"add is listed: {why})",
    ]
    assert steps(install_log(env)) == [("list-folders", "done", "0", "listed-0")]
    assert install_log(env)[-2].endswith(" result=done note=listed-0 synced=1")
    assert not report_path(env).exists()
    _write_config(env, _inbox(env))  # the inbox alone: no folder is synced, so this is a new Mac
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (3, ""), cp.stdout + cp.stderr
    [only] = cp.stdout.splitlines()
    assert only.startswith("NEXT: ") and only.endswith(f"then re-run: {INSTALL_SH} --list-folders")
    assert re.search(r" result=failed note=(not-signed-in|no-folders) synced=0$", install_log(env)[-2])
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[source]]\nid = \n", encoding="utf-8")
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 3 and cp.stdout.splitlines() == [only], "a config nobody can read: a new Mac"
    assert cp.stderr.startswith(f"warning: could not read which folders {cfg} already syncs ")
    assert re.search(r" result=failed note=(not-signed-in|no-folders)$", install_log(env)[-2])


def test_list_folders_names_the_synced_folders_when_the_only_provider_was_denied(
    env: dict[str, str],
) -> None:
    """Nothing could be listed, and the click is still the NEXT (exit 4). The folders the config syncs are
    printed all the same, so the "already synced" line is there for the prompt's exception."""
    projects = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects"
    projects.mkdir(parents=True)
    _write_config(env, local_source_table("fy26", projects), _inbox(env))
    _tool_python(env)
    _write_exe(Path(env["PATH"].split(":")[0]) / "find", STUB_FIND_EPERM)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below: first the 1 outside the list, then "
        "the list)",
        f"[synced] {projects}",
    ]
    assert last_line(cp).startswith("NEXT: this terminal app was denied access to files managed by OneDrive")
    assert install_log(env)[-2].endswith(" result=failed note=denied synced=1")


def test_list_folders_prints_a_synced_folder_past_its_cap_first(env: dict[str, str]) -> None:
    lib = _cloud(env) / "OneDrive-Contoso"
    for i in range(205):
        (lib / f"P{i:03d}").mkdir(parents=True)
    _write_config(env, local_source_table("p001", lib / "P001"), local_source_table("p203", lib / "P203"))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[0] == (
        "already synced on this Mac: 2 folder(s) (marked [synced] below: first the 1 outside the list, then "
        "the list)"
    )
    assert lines[1] == f"[synced] {lib}/P203", "past the cap of 200: not in the list, so printed first"
    assert lines[3] == f"[synced] {lib}/P001" and lines[201] == f"{lib}/P199" and lines[202] == "(5 more)"
    assert len(lines) == 204 and sum(ln.startswith("[synced] ") for ln in lines) == 2


@pytest.mark.parametrize("broken", ["no installed agentsync", "a config that does not load"])
def test_list_folders_marks_nothing_when_the_config_cannot_be_read(env: dict[str, str], broken: str) -> None:
    """A config nobody can read is not a reason to guess: the list is the new Mac's, where the person is
    asked, and one warning says why no folder is marked."""
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    cfg = _write_config(env, local_source_table("fy26", cs / "OneDrive-Contoso" / "FY26 Projects"))
    if broken == "a config that does not load":
        _tool_python(env)
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[source]]\nid = \n", encoding="utf-8")
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == f"{cs}/OneDrive-Contoso/FY26 Projects\n{LIST_NEXT}\n"
    assert cp.stderr == (
        f"warning: could not read which folders {cfg} already syncs (no installed agentsync loads it), so "
        "none is marked below\n"
    )
    assert install_log(env)[-2].endswith(" result=done note=listed-1"), "no synced= count: it was not read"


STUB_FIND_HELD = """#!/bin/bash
for a in "$@"; do
  case "$a" in
    */Dropbox) HELD ;;
  esac
done
exec /usr/bin/find "$@"
"""
"""find with one provider (Dropbox) that macOS holds: HELD is what it does there."""


@pytest.mark.parametrize(
    ("held", "note", "click"),
    [
        (
            'echo "find: $a: Operation not permitted" >&2; exit 1',
            "denied",
            "NEXT: this terminal app was denied access to files managed by Dropbox: allow it in ",
        ),
        (
            "exec sleep 30",
            "tcc-pending",
            "NEXT: macOS is asking whether this terminal app may access files managed by Dropbox: click ",
        ),
    ],
)
def test_list_folders_marks_what_it_listed_while_another_provider_waits_for_a_click(
    env: dict[str, str], held: str, note: str, click: str
) -> None:
    """A Mac that syncs OneDrive folders, and a second provider this terminal app was denied or never
    allowed. The list still ends on that click, exit 4. It used to print no mark and no "already synced"
    line then, so an unattended agent saw folder paths without the line, took the Mac for a new one and
    stopped at the folder question: the lost round again (review, 2026-10-07). The marks need only the
    config and the lines that were listed, so they are printed, and the log line has the count."""
    cs = _cloud(env)
    for d in ("OneDrive-Contoso/FY26 Projects", "OneDrive-Contoso/Documents", "Dropbox/Shared"):
        (cs / d).mkdir(parents=True)
    _write_config(env, local_source_table("fy26", cs / "OneDrive-Contoso" / "FY26 Projects"), _inbox(env))
    _tool_python(env)
    _write_exe(Path(env["PATH"].split(":")[0]) / "find", STUB_FIND_HELD.replace("HELD", held))
    cp = install_sh({**env, "AGENTSYNC_LIST_TIMEOUT": "1"}, "--list-folders", timeout=20)
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below)",
        f"{cs}/OneDrive-Contoso/Documents",
        f"[synced] {cs}/OneDrive-Contoso/FY26 Projects",
    ]
    assert last_line(cp).startswith(click) and last_line(cp).endswith(f"re-run: {INSTALL_SH} --list-folders")
    assert steps(install_log(env)) == [("list-folders", "failed", "4", note)]
    assert install_log(env)[-2].endswith(f" result=failed note={note} synced=1")


def test_list_folders_names_a_synced_folder_of_the_provider_that_was_denied(env: dict[str, str]) -> None:
    """The provider this terminal app was denied holds the synced folder, so no line lists it: it is
    printed first, as every synced folder the list does not reach is. Without a config the denied list is
    what it always was (``test_list_folders_names_the_click_when_this_terminal_was_denied``)."""
    cs = _cloud(env)
    for d in ("OneDrive-Contoso/FY26 Projects", "Dropbox/Shared"):
        (cs / d).mkdir(parents=True)
    _write_config(env, local_source_table("fy26", cs / "OneDrive-Contoso" / "FY26 Projects"))
    _tool_python(env)
    _write_exe(Path(env["PATH"].split(":")[0]) / "find", STUB_FIND_EPERM)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below: first the 1 outside the list, then "
        "the list)",
        f"[synced] {cs}/OneDrive-Contoso/FY26 Projects",
        f"{cs}/Dropbox/Shared",
    ]
    assert last_line(cp).startswith("NEXT: this terminal app was denied access to files managed by OneDrive")
    assert install_log(env)[-2].endswith(" result=failed note=denied synced=1")


# ---- a re-run on a Mac that is already set up, with the real agentsync (field report 2026-10-07) ----------

STUB_UV_REAL = """#!/bin/bash
echo "uv $*" >> "$STUB_LOG"
if [ "$1 $2" = "tool install" ]; then
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/uv/tools/agentsync/bin"
  printf '#!/bin/sh\\nexec "%s" "$@"\\n' "$REAL_PYTHON" > "$HOME/.local/share/uv/tools/agentsync/bin/python"
  printf '#!/bin/sh\\nexec "%s" -m agentsync "$@"\\n' "$REAL_PYTHON" > "$HOME/.local/bin/agentsync"
  chmod 755 "$HOME/.local/share/uv/tools/agentsync/bin/python" "$HOME/.local/bin/agentsync"
elif [ "$1 $2 ${3:-}" = "tool dir --bin" ]; then
  echo "$HOME/.local/bin"
elif [ "$1 $2" = "tool dir" ]; then
  echo "$HOME/.local/share/uv/tools"
fi
"""
"""uv for a run with the real agentsync: its ``tool install`` puts, where uv would, launchers for the Python
running these tests (which has agentsync) as the tool's interpreter and as ``agentsync``."""


@pytest.fixture
def real_env(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """``env`` with the real agentsync behind the uv stub: its init, add-source, status, sync and
    setup-report run in the tmp HOME, with no OCR helper, no tmutil and a git identity of its own."""
    _write_exe(Path(env["PATH"].split(":")[0]) / "uv", STUB_UV_REAL)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = agentsync-test\n\temail = test@localhost\n[init]\n\tdefaultBranch = main\n"
    )
    return {
        **env,
        "REAL_PYTHON": sys.executable,
        "AGENTSYNC_OCR": "0",
        "AGENTSYNC_TM_EXCLUDE": "0",
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _config_line(env: dict[str, str]) -> str:
    """The config step's line of the last run in the setup log."""
    return [ln for ln in install_log(env) if " step=config " in ln][-1]


def test_a_rerun_with_no_folder_keeps_what_the_mac_already_syncs(
    real_env: dict[str, str], wheel: Path
) -> None:
    """What install.sh does with no --source-local on a Mac that is already set up, checked against the
    real agentsync because a re-run now ends there: it updates the tool, leaves sources.toml byte for byte
    as it was, runs status and a sync, writes the report and exits 0 on the loop's NEXT. The config step
    says what became of the folders, in counts: one line for the reader, two fields in the setup log. A
    folder named again is not added twice."""
    env = real_env
    alpha, beta = (_cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / name for name in ("Alpha", "Beta"))
    for d in (alpha, beta):
        d.mkdir(parents=True)
        (d / "plan.txt").write_text(f"a made-up plan for {d.name}\n", encoding="utf-8")
    first = install_sh(env, str(wheel), "--source-local", str(alpha))
    assert first.returncode == 0, first.stdout + first.stderr
    # A first install's status runs before any sync, on a docs repo with no DEPENDS.tsv yet: that is the
    # normal state, and no log line of agentsync's reaches the output for it (v9 rehearsal, 2026-10-07).
    assert not re.search(r"\b(WARNING|ERROR) agentsync\.", first.stdout + first.stderr), first.stderr
    assert "folders: 1 added (none was synced before)" in first.stdout.splitlines()
    assert "first sync: converted 1, deferred 0 online-only" in first.stdout.splitlines()
    assert _config_line(env).endswith(" result=done note=created kept=0 added=1")
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    before = cfg.read_bytes()

    # The setup prompt again, on this Mac: step 1 starts an attempt and lists, step 2 has no folder to add.
    assert install_sh(env, "--log-start", started("Test Agent (model-1)")).returncode == 0
    listing = install_sh(env, "--list-folders")
    assert listing.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below)",
        f"[contains a synced folder] {alpha.parent}",
        f"[synced] {alpha}",
        str(beta),
    ]
    Path(env["STUB_LOG"]).unlink()
    report_path(env).unlink()
    again = install_sh(env, str(wheel))
    assert again.returncode == 0, again.stdout + again.stderr
    assert cfg.read_bytes() == before, "every source is kept and none is added"
    out = again.stdout.splitlines()
    assert f"config: {cfg} exists (inbox ensured)" in out
    assert "folders: kept the 1 already synced (none added)" in out
    assert "sync: converted 0, deferred 0 online-only" in out, "a sync ran; the one file was converted before"
    assert calls(env)[2].startswith("uv tool install --force --reinstall-package agentsync "), calls(env)
    assert last_line(again).startswith("NEXT: ") and one_next(again)
    assert last_line(again).endswith(f" [setup report: {report_path(env)}]")
    assert report_path(env).read_text(encoding="utf-8").startswith(setup_report.REPORT_TITLE)
    assert steps(install_log(env))[-10:] == [
        ("uv", "skipped", "0", "present"),
        ("agentsync", "done", "0", ""),
        ("launcher", "skipped", "0", "not-requested"),
        ("helpers", "done", "0", "") if _have_git() else ("helpers", "skipped", "0", "no-devtools"),
        ("config", "skipped", "0", "exists"),
        ("status", "done", "0", ""),
        ("first-sync", "done", "0", "converted-0-deferred-0"),
        ("agent", "skipped", "0", "not-requested"),
        ("wait", "skipped", "0", "not-requested"),
        ("report", "done", "0", "agentsync"),
    ]
    assert _config_line(env).endswith(" result=skipped note=exists kept=1 added=0")
    # Its report reads those counts: nobody had to be asked for a folder, and none was added.
    summary = report_path(env).read_text(encoding="utf-8").split("\n## Summary\n", 1)[1].split("\n## ", 1)[0]
    assert (
        "- folders: kept the 1 already synced (none added) · 0 named with --source-local (install.log)"
        in summary.splitlines()
    )
    assert (
        "- expected turns: no folder question (1 folder already synced: step 1 asks at most whether to add "
        "one; not logged) · " in summary
    )
    assert "- human turns: 0 (0 questions; " in summary
    assert "- install.sh: 1 install run (+1 --list-folders) during this attempt; the last exit 0" in summary

    more = install_sh(env, str(wheel), "--source-local", str(beta), "--source-local", str(alpha))
    assert more.returncode == 0, more.stdout + more.stderr
    assert "folders: 1 added to the 1 already synced" in more.stdout.splitlines()
    assert _config_line(env).endswith(" result=done note=add-source kept=1 added=1")
    after = cfg.read_bytes()
    assert after.startswith(before) and after.count(b'kind = "local"') == 2, "Beta added, Alpha not twice"


def test_a_set_up_mac_with_nothing_to_list_is_kept_by_the_run_its_list_names(
    real_env: dict[str, str], wheel: Path
) -> None:
    """The list's NEXT on a Mac whose one synced folder is outside ~/Library/CloudStorage, with OneDrive
    signed out, names install.sh alone. With the real agentsync: that run keeps the folder, syncs it and
    exits 0, where step 1 used to end at exit 3 and the prompt went to the report without it."""
    env = real_env
    notes = Path(env["HOME"]) / "Documents" / "notes"
    notes.mkdir(parents=True)
    (notes / "plan.txt").write_text("a made-up plan\n", encoding="utf-8")
    first = install_sh(env, str(wheel), "--source-local", str(notes))
    assert first.returncode == 0, first.stdout + first.stderr
    listing = install_sh(env, "--list-folders")
    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert listing.stdout.splitlines()[1] == f"[synced] {notes}"
    assert last_line(listing).startswith(
        f"NEXT: this Mac already syncs 1 folder(s), and a re-run keeps them: run {INSTALL_SH} (no folder "
    )
    again = install_sh(env, str(wheel))
    assert again.returncode == 0, again.stdout + again.stderr
    assert "folders: kept the 1 already synced (none added)" in again.stdout.splitlines()
    assert "sync: converted 0, deferred 0 online-only" in again.stdout.splitlines()
    assert _config_line(env).endswith(" result=skipped note=exists kept=1 added=0")


def _summary_lines(cp: subprocess.CompletedProcess[str]) -> tuple[list[str], list[str]]:
    """The two lines add-source ends every call on, as a run printed them: ``docs repo ...`` and
    ``sources: ...``."""
    out = cp.stdout.splitlines()
    return (
        [ln for ln in out if ln.startswith("docs repo ")],
        [ln for ln in out if ln.startswith("sources: ")],
    )


def test_several_folders_print_the_docs_repo_and_sources_lines_once(
    real_env: dict[str, str], wheel: Path
) -> None:
    """Field reports 2026-10-06 and 2026-10-07: install.sh calls add-source once per --source-local, and
    every call ends on a ``docs repo ...`` line and a ``sources: ...`` line, so two folders printed the block
    twice (16 source ids each time on the field Mac). With several folders the run now prints it once: the
    first call's ``docs repo`` line, the one that can say "created", and the last call's ``sources:`` line,
    the one that lists every folder. Checked against the real agentsync: a stub's lines would still pass if
    the tool's own words changed and the filter stopped matching them."""
    env = real_env
    cloud = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects"
    alpha, beta, gamma = (cloud / name for name in ("Alpha", "Beta", "Gamma"))
    for d in (alpha, beta, gamma):
        d.mkdir(parents=True)
        (d / "plan.txt").write_text(f"a made-up plan for {d.name}\n", encoding="utf-8")
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"

    fresh = install_sh(
        env,
        str(wheel),
        "--source-local",
        str(alpha),
        "--source-local",
        str(beta),
        "--source-local",
        str(gamma),
    )
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    out = fresh.stdout.splitlines()
    [repo], [sources] = _summary_lines(fresh)
    assert re.search(r" \(created, no remote; [1-9]\d* scaffold file\(s\) written\)$", repo), repo
    ids = [s.id for s in load_config(cfg).sources]
    assert len(ids) == 4, "three folders and the inbox"
    assert sources == f"sources: {', '.join(ids)}", "the last call's line: every folder is in it"
    added = [i for i, ln in enumerate(out) if ln.startswith("added source ")]
    assert len(added) == 3, "every other line of every call is still printed"
    assert added[0] < out.index(repo) < added[1] and added[2] < out.index(sources)
    assert "folders: 3 added (none was synced before)" in out

    # The field's command: two folders, both already in the config. One block, where there were two.
    again = install_sh(env, str(wheel), "--source-local", str(alpha), "--source-local", str(beta))
    assert again.returncode == 0, again.stdout + again.stderr
    [repo], [sources] = _summary_lines(again)
    assert repo.endswith(" (exists, no remote; scaffold up to date)"), repo
    assert sources == f"sources: {', '.join(ids)}"
    assert sum(ln.startswith("already configured: source ") for ln in again.stdout.splitlines()) == 2

    # One folder: nothing is dropped, so the run prints both lines as add-source does by hand.
    one = install_sh(env, str(wheel), "--source-local", str(gamma))
    assert one.returncode == 0, one.stdout + one.stderr
    assert [len(lines) for lines in _summary_lines(one)] == [1, 1]


def test_a_failed_add_source_still_fails_the_run_through_the_filter(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The summary filter reads add-source's output through a pipe. The call's exit status is still the
    step's: a failed add-source on the first of two folders stops the run with its error, and the setup log
    has the call's own exit code, not the filter's 0."""
    other = folder.parent / "Other"
    other.mkdir()
    cp = install_sh(
        {**env, "STUB_ADD_SOURCE_RC": "78"},
        str(wheel),
        "--source-local",
        str(folder),
        "--source-local",
        str(other),
    )
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert f"error: agentsync add-source {folder} failed (see the error above)" in cp.stderr
    assert "add-source failed (stub)" in cp.stderr, "the tool's own error is not filtered"
    assert sum(c.startswith("agentsync add-source ") for c in calls(env)) == 1, (
        "the second folder is not tried"
    )
    assert ("config", "failed", "78", "") in steps(install_log(env))
    assert one_next(cp) and last_line(cp).startswith("NEXT: fix the error above, then re-run: ")


def _mode(path: Path) -> int:
    return path.lstat().st_mode & 0o777


def test_a_rerun_clears_the_permissions_agentsync_owns_before_status_can_stop_on_them(
    real_env: dict[str, str], wheel: Path
) -> None:
    """Field report 2026-10-07, with the real agentsync. On a Mac that already ran it, an earlier session's
    agent had written the docs repo's _eval folder under its own umask. Step 2's install.sh printed
    ``[FAIL] docs_repo.permissions``, skipped the sync and exited 1 on a chmod the agent may not run: a
    round lost, though a sync clears that folder. The config step now clears what a sync would (and a
    cache folder and a log left the same way) before status looks, so the run prints no [FAIL], syncs and
    exits 0. A page inside mirror/ is not a path agentsync changes: there the run still stops at status,
    on the [FAIL] and its chmod."""
    env = real_env
    home = Path(env["HOME"])
    alpha, beta = (_cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / name for name in ("Alpha", "Beta"))
    for d in (alpha, beta):
        d.mkdir(parents=True)
        (d / "plan.txt").write_text(f"a made-up plan for {d.name}\n", encoding="utf-8")
    command = (str(wheel), "--source-local", str(alpha), "--source-local", str(beta))
    first = install_sh(env, *command)
    assert first.returncode == 0, first.stdout + first.stderr

    def permissions() -> str:
        """The check's line in ``agentsync status``, as a person or an agent running it sees it."""
        agentsync = str(home / ".local" / "bin" / "agentsync")
        cp = subprocess.run([agentsync, "status"], capture_output=True, text=True, check=False, env=env)
        [line] = [ln for ln in cp.stdout.splitlines() if re.match(r"\[.{4}\] docs_repo\.permissions ", ln)]
        return line

    def git_config(*args: str) -> str:
        cp = subprocess.run(
            ["git", "-C", str(docs), "config", "--local", *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        return cp.stdout.strip()

    # The field layout: what an agent's file tool, a shell and an earlier build left under umask 022.
    docs = home / "agent-context" / "docs"
    eval_dir, topic_dir = docs / "_eval", docs / "topics" / "contoso"
    old_build = home / "Library" / "Caches" / "agentsync" / "old-build"
    log_dir = home / "Library" / "Logs" / "agentsync"
    dirs = [eval_dir, topic_dir, old_build, log_dir]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    files = [eval_dir / "questions.md", eval_dir / "answers.md", topic_dir / "scratch.txt"]
    files.append(log_dir / "poll.out.log")
    for f in files:
        f.write_text("1. What did Contoso decide?\n", encoding="utf-8")
        f.chmod(0o644)
    for d in dirs:
        d.chmod(0o755)
    git_config("--unset", "core.sharedRepository")  # a docs repo from before the setting
    found = permissions()
    assert found.startswith("[FAIL] docs_repo.permissions ") and str(eval_dir) in found
    assert found.endswith(" (fix: agentsync sync (it makes these owner-only))"), "a command an agent may run"

    again = install_sh(env, *command)
    assert again.returncode == 0, again.stdout + again.stderr
    out = again.stdout.splitlines()
    assert not [ln for ln in out if ln.startswith("[FAIL]")], again.stdout
    assert any(ln.startswith("[ok  ] docs_repo.permissions ") for ln in out), "the status step saw it clean"
    assert "sync: converted 0, deferred 0 online-only" in out
    assert f"tightened {log_dir} to 0700 (was 0755): it holds tenant data" in out
    assert "tightened 7 path(s) inside the docs repo, the cache or the logs: they hold tenant data" in out
    assert last_line(again).startswith("NEXT: ") and one_next(again)
    assert {_mode(f) for f in files} == {0o600} and {_mode(d) for d in dirs} == {0o700}
    assert git_config("core.sharedRepository") == "0600"
    assert permissions().startswith("[ok  ] docs_repo.permissions ")
    assert steps(install_log(env))[-6:] == [
        ("config", "done", "0", "add-source"),
        ("status", "done", "0", ""),
        ("first-sync", "done", "0", "converted-0-deferred-0"),
        ("agent", "skipped", "0", "not-requested"),
        ("wait", "skipped", "0", "not-requested"),
        ("report", "done", "0", "agentsync"),
    ]

    # No false green: a page inside mirror/ is the publisher's, and neither setup nor a sync walks that tree.
    page = next(p for p in sorted((docs / "mirror").rglob("*.md")) if p.parent != docs / "mirror")
    page.chmod(0o644)
    eval_dir.chmod(0o755)
    Path(env["STUB_LOG"]).unlink()
    stopped = install_sh(env, *command)
    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    [fail] = [ln for ln in stopped.stdout.splitlines() if ln.startswith("[FAIL]")]
    assert fail.startswith("[FAIL] docs_repo.permissions ") and str(page) in fail
    assert str(eval_dir) not in fail and _mode(eval_dir) == 0o700, "what agentsync owns was still cleared"
    assert " (fix: chmod -R go-rwx " in fail and "agentsync sync" not in fail
    assert _mode(page) == 0o644
    assert last_line(stopped).startswith(
        f"NEXT: fix the [FAIL] lines above (each names its fix), then re-run: {INSTALL_SH}"
    )
    assert ("first-sync", "skipped", "0", "status-failed") in steps(install_log(env))[-5:]


SLOW_FIRST_PANDOC = """#!/bin/bash
who="$(ps -o command= -p "$PPID" 2>/dev/null | tr '\\n' ' ')"
if [ -e "$0.started" ]; then
  echo "quick $who" >> "$0.log"
else
  : > "$0.started"
  echo "slow $who" >> "$0.log"
  sleep "${STUB_PANDOC_FIRST_START:-3}"
fi
echo "pandoc 9.9"
"""
"""A pandoc whose first start is slow and whose later ones are not, as each new copy of the bundled one is
on Apple silicon (an Intel program, which macOS prepares at its first start). Every start is logged beside
the file: how it went, and the command of the process that started it."""

PANDOC_PROGRESS = (
    r"pandoc: still running, \d+s \(the first start after an install can take a minute; later ones are "
    r"quick\)"
)


def test_a_slow_first_start_of_pandoc_is_the_installers_wait_and_not_the_status_checks(
    real_env: dict[str, str], wheel: Path, tmp_path: Path
) -> None:
    """The v9 rehearsal (2026-10-07), with the real agentsync: a newly installed pandoc took longer to
    start than the 60 s the status check gives it, so the run printed a [FAIL], skipped the sync and exited
    1, and the same command run again passed. install.sh now starts that pandoc itself before status, with
    progress lines, so the slow start is the installer's and the check meets a quick one: no [FAIL], a
    sync, exit 0. A run after that says nothing about pandoc."""
    env = {**real_env, "AGENTSYNC_PROGRESS_SECONDS": "1", "STUB_PANDOC_FIRST_START": "2.5"}
    (tmp_path / "tools").mkdir()
    pandoc = _write_exe(tmp_path / "tools" / "pandoc", SLOW_FIRST_PANDOC)
    _write_config(env, f'\n[convert]\npandoc_path = "{pandoc}"\n')  # the pandoc status checks on this Mac
    alpha = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / "Alpha"
    alpha.mkdir(parents=True)
    (alpha / "plan.txt").write_text("a made-up plan for Alpha\n", encoding="utf-8")
    command = (str(wheel), "--source-local", str(alpha))

    cp = install_sh(env, *command)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    waits = [i for i, ln in enumerate(out) if re.fullmatch(PANDOC_PROGRESS, ln)]
    [started] = [i for i, ln in enumerate(out) if re.fullmatch(r"pandoc: started after \d+s", ln)]
    [checked] = [i for i, ln in enumerate(out) if ln.startswith("[ok  ] pandoc ")]
    assert waits and waits[-1] < started < checked, cp.stdout
    assert out[checked].endswith(f"— pandoc 9.9 at {pandoc}"), "the check ran this pandoc, and it answered"
    assert not [ln for ln in out if ln.startswith("[FAIL]")], cp.stdout
    assert "sync: converted 1, deferred 0 online-only" in out
    assert last_line(cp).startswith("NEXT: ") and one_next(cp)
    assert steps(install_log(env))[-5:-3] == [
        ("status", "done", "0", ""),
        ("first-sync", "done", "0", "converted-1-deferred-0"),
    ]
    starts = Path(f"{pandoc}.log").read_text(encoding="utf-8").splitlines()
    assert starts[0].startswith("slow ") and str(INSTALL_SH) in starts[0], "install.sh made the slow start"
    assert all(ln.startswith("quick ") for ln in starts[1:]), starts
    assert any(" -m agentsync status " in ln for ln in starts[1:]), "status's check then met a quick one"

    # At the default interval (at the 1 s one above a line is printed whenever the clock's second turns):
    again = install_sh(real_env, *command)
    assert again.returncode == 0, again.stdout + again.stderr
    assert not [ln for ln in again.stdout.splitlines() if ln.startswith("pandoc: ")], again.stdout
    assert any(ln.startswith("[ok  ] pandoc ") for ln in again.stdout.splitlines())


def _shell_function(name: str) -> str:
    """install.sh's function ``name`` as the script has it: from ``name() {`` to the brace that closes it."""
    found = re.search(rf"(?ms)^{name}\(\) \{{$.*?^\}}$", INSTALL_SH.read_text(encoding="utf-8"))
    assert found is not None, name
    return found.group(0)


def test_a_pandoc_that_does_not_answer_is_stopped_at_the_installers_own_limit(tmp_path: Path) -> None:
    """The installer's wait for pandoc has a limit of its own, above the status check's 60 s: the 300 s one
    conversion gives pandoc. Past it the start is stopped, the run goes on and status reports what it
    finds. The function itself is run here, with a limit of 2 s in place of the script's 300."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [limit] = re.findall(r"(?m)^PANDOC_START_SECONDS=(\d+)$", script)
    assert int(limit) == pandoc_converter._TIMEOUT_S == 300 and int(limit) > doctor._PANDOC_TIMEOUT_S

    hung = _write_exe(tmp_path / "pandoc", '#!/bin/bash\necho "$$" > "$0.pid"\nexec sleep 600\n')
    lines = [
        "set -euo pipefail",
        "say() { printf '%s\\n' \"$*\"; }",
        f"status_pandoc() {{ printf '%s' {shlex.quote(str(hung))}; }}",
        "PROGRESS_EVERY=1",
        "PANDOC_START_SECONDS=2",
        _shell_function("start_pandoc_once"),
        "start_pandoc_once",
        "echo the run goes on",
    ]
    cp = subprocess.run(
        [BASH32, "-c", "\n".join(lines)], capture_output=True, text=True, check=False, timeout=120
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert out[-2:] == ["pandoc: no answer after 2s; status checks it next", "the run goes on"], cp.stdout
    assert all(re.fullmatch(PANDOC_PROGRESS, ln) for ln in out[:-2]), cp.stdout
    with pytest.raises(ProcessLookupError):
        os.kill(int(Path(f"{hung}.pid").read_text()), 0)

    # No pandoc to start (a config that does not load, a path that is no program): silent, and no failure.
    for answer in ("", str(tmp_path / "nothing-here")):
        lines[2] = f"status_pandoc() {{ printf '%s' {shlex.quote(answer)}; }}"
        cp = subprocess.run(
            [BASH32, "-c", "\n".join(lines)], capture_output=True, text=True, check=False, timeout=120
        )
        assert (cp.returncode, cp.stdout, cp.stderr) == (0, "the run goes on\n", "")


def test_a_quiet_pandoc_start_and_the_status_wait_keep_one_progress_clock(tmp_path: Path) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): step 5 could print nothing for about twice the
    progress interval. A pandoc start shorter than the interval prints nothing, and the status wait after
    it began its own count at zero, so a 13 s start and a status that waits were 27 s of silence at the
    default 15 s. The status wait now counts its first interval from where the pandoc start began: a line
    is printed once the two have lasted one interval together, though neither did alone. The two functions
    are run here as the script has them, in step 5's order, and the wait after those two counts from its
    own start again."""
    quiet = _write_exe(tmp_path / "pandoc", "#!/bin/bash\nsleep 2.5\n")
    lines = [
        "set -euo pipefail",
        "say() { printf '%s\\n' \"$*\"; }",
        f"status_pandoc() {{ printf '%s' {shlex.quote(str(quiet))}; }}",
        "PROGRESS_EVERY=5",
        "PANDOC_START_SECONDS=300",
        _shell_function("with_progress"),
        _shell_function("start_pandoc_once"),
        "start_pandoc_once",
        "with_progress status sleep 4",
        "echo status is done",
        "with_progress 'first sync' sleep 1",
        "echo the run goes on",
    ]
    cp = subprocess.run(
        [BASH32, "-c", "\n".join(lines)], capture_output=True, text=True, check=False, timeout=120
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert out[-2:] == ["status is done", "the run goes on"], "the next wait began a count of its own"
    said = out[:-2]
    assert said, "neither wait lasted an interval, the two together did: a line says the run is alive"
    progress = rf"status: still running, \d+s|{PANDOC_PROGRESS}|pandoc: started after \d+s"
    assert all(re.fullmatch(progress, ln) for ln in said), cp.stdout


def test_the_config_step_logs_no_count_nobody_took(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """The counts are the installed agentsync's. With no interpreter to ask (the stub uv installs none) the
    step's line is what it was, and no folders: line is printed."""
    for args in ((str(wheel), "--source-local", str(folder)), (str(wheel),)):
        cp = install_sh(env, *args)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        assert "folders:" not in cp.stdout
        assert re.search(r" result=\w+ note=(created|exists)$", _config_line(env)), _config_line(env)


# ---- nothing competes with NEXT: (J8) ----------------------------------------------------------------------


def test_no_shell_profile_advice_and_no_uv_path_hint(env: dict[str, str], folder: Path, wheel: Path) -> None:
    assert f"{env['HOME']}/.local/bin" not in env["PATH"].split(":")
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout + cp.stderr
    assert "Installed 1 executable: agentsync" in cp.stderr, "uv's other output passes through"
    for stray in ("not on your PATH", "update-shell", "shell profile", "export PATH"):
        assert stray not in out, stray
    agentsync = f"{env['HOME']}/.local/bin/agentsync"
    assert (
        f"note: {env['HOME']}/.local/bin is not on PATH; nothing to change: this setup and its NEXT lines "
        f"use the full path {agentsync}"
    ) in cp.stdout
    code = [ln for ln in INSTALL_SH.read_text().splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "shell profile" in ln], "no printed line advises a shell profile edit"


# ---- the click for a denied or unanswered launcher (J16) ---------------------------------------------------


def test_background_exit_80_names_the_files_and_folders_click(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(
        {**env, "STUB_LC_EXITS": "80"}, str(wheel), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 1
    assert "the first background run exited 80 (TCC denied" in cp.stderr
    assert last_line(cp) == (
        "NEXT: macOS denied agentsync-launcher access to files managed by OneDrive "
        f"""(an earlier "Don't Allow"): {TCC_CLICK}, then re-run: {rerun(wheel, folder)} """
        f"[setup report: {report_path(env)}]"
    )
    assert ("wait", "failed", "80", "") in steps(install_log(env))


def test_first_sync_exit_80_names_the_terminal_click(env: dict[str, str], folder: Path, wheel: Path) -> None:
    cp = install_sh(
        {**env, "STUB_SYNC_RC": "80", "TERM_PROGRAM": "iTerm.app"},
        str(wheel),
        "--source-local",
        str(folder),
        "--confirm-install-agent",
    )
    assert cp.returncode == 1
    assert not any(c.startswith(("agentsync install-agent", "launchctl kickstart")) for c in calls(env))
    assert last_line(cp).startswith(
        "NEXT: this terminal app (iTerm) was denied access to files managed by OneDrive: allow it in System "
        "Settings > Privacy & Security > Files and Folders"
    )
    assert ("first-sync", "failed", "80", "tcc-denied") in steps(install_log(env))


# ---- progress lines and a stopped run (J11) ----------------------------------------------------------------


def test_first_sync_and_wait_print_progress_lines(env: dict[str, str], folder: Path, wheel: Path) -> None:
    e = {
        **env,
        "AGENTSYNC_PROGRESS_SECONDS": "1",
        "STUB_SYNC_SLEEP": "2.5",
        "STUB_LC_RUNNING": "1",
        "AGENTSYNC_WAIT_SECONDS": "3",
    }
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    sync = [ln for ln in out if ln.startswith("first sync: still running, ")]
    assert len(sync) >= 2, cp.stdout
    wait = [ln for ln in out if re.fullmatch(r"wait: \d+s of 3s, .*", ln)]
    assert len(wait) >= 2 and "the background run is running" in wait[0], cp.stdout
    assert any("(AGENTSYNC_WAIT_SECONDS, default 180)" in ln for ln in out)


def test_the_status_step_prints_progress_lines_too(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """Step 5 printed nothing while status ran, and status prints its lines only at its end: a minute of
    silence in the v9 rehearsal, two for a folder macOS holds for a click. It now prints progress lines as
    the first sync does, above status's own lines, and its exit status is still the step's."""
    e = {**env, "AGENTSYNC_PROGRESS_SECONDS": "1", "STUB_DOCTOR_SLEEP": "2.5"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    waits = [i for i, ln in enumerate(out) if re.fullmatch(r"status: still running, \d+s", ln)]
    assert waits and waits[-1] < out.index("[warn] launchd.poll — not loaded (fix: agentsync install-agent)")
    assert ("status", "done", "0", "") in steps(install_log(env))

    failing = {**e, "STUB_DOCTOR_OUT": LISTING_HELD_FAIL, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(failing, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1 and LISTING_HELD_FAIL in cp.stdout.splitlines(), cp.stdout + cp.stderr
    assert ("status", "done", "1", "fail-lines") in steps(install_log(env))


def test_default_progress_interval_is_15_seconds() -> None:
    script = INSTALL_SH.read_text()
    assert 'PROGRESS_SECONDS="${AGENTSYNC_PROGRESS_SECONDS:-15}"' in script
    assert 'WAIT_SECONDS="${AGENTSYNC_WAIT_SECONDS:-180}"' in script
    assert "default 180 s, 3 minutes" in script and "at least every 15 s" in script, "--help documents both"


def test_a_stopped_run_logs_its_end_and_names_the_rerun(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e = {**env, "STUB_SYNC_SLEEP": "20"}
    argv = [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder), "--confirm-install-agent"]
    proc = subprocess.Popen(
        argv,
        env=e,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not any(c.startswith("agentsync sync --once") for c in calls(env)):
            assert proc.poll() is None and time.monotonic() < deadline, "the first sync never started"
            time.sleep(0.05)
        os.kill(proc.pid, signal.SIGTERM)  # what a coding tool's timeout sends
        rc = proc.wait(timeout=30)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # the stub sync the stopped run leaves behind
    assert proc.stdout is not None and proc.stderr is not None
    stdout, stderr = proc.stdout.read(), proc.stderr.read()
    assert rc == 143, stdout + stderr
    assert stdout.rstrip("\n").splitlines()[-1] == (
        "NEXT: this run was stopped before it finished (stopped by SIGTERM (a tool timeout?)); it is safe to "
        f"re-run: {rerun(wheel, folder)} [setup report: {report_path(env)}]"
    )
    log = install_log(env)
    assert ("first-sync", "failed", "143", "") in steps(log)
    assert re.search(r" end rc=143 seconds=\d+$", log[-1]) and " step=report " in log[-2]
    assert stdout.rstrip("\n").splitlines()[-1] in install_out(env).read_text(), "install.out has the end too"


# ---- the report's issue link before NEXT (K3) --------------------------------------------------------------


def test_report_only_without_a_link_prints_no_link_line(env: dict[str, str]) -> None:
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    cp = install_sh({**env, "STUB_REPORT_NO_LINK": "1"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert "issue link" not in cp.stdout
    back = report_path(env).parent / "bring-back.md"
    assert cp.stdout.splitlines()[-3] == f"report: {report_path(env)} (agentsync setup-report)"
    assert cp.stdout.splitlines()[-2].startswith(f"bring back: {back}")
    assert last_line(cp) == (
        f"NEXT: review {back} and copy that one file back privately (nothing is sent for you) "
        f"[setup report: {report_path(env)}]"
    )


# ---- one instruction: no agentsync "next:" hints, doctor knows the agent step (K5) -------------------------


def _env_calls(env: dict[str, str]) -> list[str]:
    return Path(env["STUB_LOG"] + ".env").read_text().splitlines()


def test_agentsync_hints_are_off_and_doctor_knows_the_agent_step(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert len([ln for ln in cp.stdout.splitlines() if re.match(r"(?i)\s*next:", ln)]) == 1, cp.stdout
    assert "fix: agentsync install-agent" not in cp.stdout
    assert "launchd.poll — not loaded (installed by the agent step below)" in cp.stdout
    seen = _env_calls(env)
    assert seen[-2] == "status no_next= pending=", "KISS K02: the closing status alone has its NEXT on"
    seen = seen[:-2] + seen[-1:]
    assert seen and all(" no_next=1 " in ln for ln in seen), seen
    assert [ln.split()[0] for ln in seen if ln.endswith(" pending=1")] == ["status"]
    assert "setup-report no_next=1 pending=" in seen, "the report describes what is installed by then"


def test_without_confirm_install_agent_doctor_names_its_own_fix(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(env, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "status no_next=1 pending=" in _env_calls(env)
    assert len([ln for ln in cp.stdout.splitlines() if re.match(r"(?i)\s*next:", ln)]) == 1


# ---- the first sync downloads nothing and converts what is local (K15, L3) ---------------------------------


def test_first_sync_runs_with_materialisation_off(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """KISS K13b: --materialise-budget 0 is passed unconditionally; sync --help hides the flag, so the old
    `sync --help` probe would have silently dropped it."""
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    syncs = [c for c in calls(env) if c.startswith("agentsync sync ")]
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    assert syncs == [f"agentsync sync --once --materialise-budget 0 --config {cfg}"]  # no `sync --help` call
    assert (
        "(downloads nothing: the files already on this Mac are converted now; each later sync downloads and "
        "converts the online-only ones)"
    ) in cp.stdout
    assert "stub cycle" in cp.stdout, "no summary line: all the sync printed is shown"
    assert ("first-sync", "done", "0", "") in steps(install_log(env))


# ---- --list-folders fits a 2-minute tool timeout (K8) ------------------------------------------------------


def test_list_folders_timeouts_fit_a_two_minute_command() -> None:
    script = INSTALL_SH.read_text()
    assert 'LIST_TIMEOUT="${AGENTSYNC_LIST_TIMEOUT:-90}"' in script
    assert 'LIST_TOTAL="${AGENTSYNC_LIST_TOTAL_SECONDS:-100}"' in script


def test_list_folders_caps_the_wait_across_providers(env: dict[str, str]) -> None:
    for p in ("OneDrive-Contoso/FY26 Projects", "Dropbox/Shared", "GoogleDrive-x/Team"):
        (_cloud(env) / p).mkdir(parents=True)
    _write_exe(Path(env["PATH"].split(":")[0]) / "find", "#!/bin/bash\nexec sleep 30\n")
    e = {**env, "AGENTSYNC_LIST_TIMEOUT": "2", "AGENTSYNC_LIST_TOTAL_SECONDS": "3"}
    t0 = time.monotonic()
    cp = install_sh(e, "--list-folders", timeout=30)
    elapsed = time.monotonic() - t0
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert elapsed < 6, f"three providers waited {elapsed:.1f}s past a 3 s cap"
    assert last_line(cp).startswith(
        "NEXT: macOS is asking whether this terminal app may access files managed by Dropbox, Google Drive, "
        "OneDrive: click Allow"
    )


def test_list_folders_total_must_be_a_number(env: dict[str, str]) -> None:
    cp = install_sh({**env, "AGENTSYNC_LIST_TOTAL_SECONDS": "soon"}, "--list-folders")
    assert cp.returncode == 2 and "AGENTSYNC_LIST_TOTAL_SECONDS must be a whole number" in cp.stderr


# ---- setup files are owner-only (K13) ----------------------------------------------------------------------


def _agent_writes(env: dict[str, str]) -> dict[Path, int]:
    """What a coding agent leaves in the setup folder under its own umask (022): the fix request its file
    tool wrote, the folder and patch of step 1's keep command, a folder below that, and a file with a name
    nobody planned. Each path with the mode it has to end at; the setup folder itself is left at 0755."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    folders = [setup / "local-work", setup / "local-work" / "notes"]
    files = [
        setup / "fix-request.md",
        setup / "scratch notes.txt",
        setup / "local-work" / PATCH_NAME,
        setup / "local-work" / "notes" / "deep.md",
    ]
    folders[-1].mkdir(parents=True, exist_ok=True)
    for f in files:
        f.write_text("- a made-up request\n", encoding="utf-8")
        f.chmod(0o644)
    for d in (setup, *folders):
        d.chmod(0o755)
    return {setup: 0o700, **dict.fromkeys(folders, 0o700), **dict.fromkeys(files, 0o600)}


def _modes(paths: Iterable[Path]) -> dict[Path, int]:
    return {p: _mode(p) for p in paths}


def test_setup_dir_and_files_are_made_owner_only(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """The installer's own three files, and since the second bring-back (2026-10-07) everything else in the
    setup folder: what the agent wrote there was readable by group and other."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    setup.chmod(0o755)
    for name in ("install.log", "friction.md", "install.out"):
        (setup / name).write_text("earlier\n")
        (setup / name).chmod(0o644)
    want = _agent_writes(env)
    cp = install_sh(env, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (setup.stat().st_mode & 0o777) == 0o700
    for name in ("install.log", "friction.md", "install.out"):
        assert ((setup / name).stat().st_mode & 0o777) == 0o600, name
    assert _modes(want) == want
    assert f"# run={install_log(env)[-1].split(' run=')[1].split()[0]} " in install_out(env).read_text(), (
        "the run's output is still copied to install.out"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--report-only"],
        ["--list-folders"],
        ["--log-start", "prompt v{n}, Test Agent"],
        ["--log", "3", "deviation", "wrote the fix request", "-"],
        ["--source-local", "/no/such/folder/for/this/test"],
    ],
    ids=["report-only", "list-folders", "log-start", "log", "usage-error"],
)
def test_every_run_leaves_what_the_agent_wrote_in_the_setup_folder_owner_only(
    env: dict[str, str], argv: list[str]
) -> None:
    """Field report 2026-10-07: fix-request.md and local-work/ were readable by group and other. The agent
    writes them under its own umask, and no installer command looked at them. ``--report-only`` matters
    most: it is the prompt's last command, right after the agent's last write to the fix request, and it
    logs nothing, so it used to change no mode at all. Now every command that gets as far as the setup
    folder leaves all of it owner-only, whatever its exit status."""
    want = _agent_writes(env)
    cp = install_sh(env, *(a.format(n=COMPAT) for a in argv))
    assert _modes(want) == want, cp.stdout + cp.stderr
    if argv == ["--report-only"]:
        text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
        assert "- a made-up request\n" in text.split("## 2. Fix request", 1)[1].split("## 3. Local work")[0]
        assert "~~~~~~~~~~diff\n- a made-up request\n~~~~~~~~~~" in text.split("## 3. Local work", 1)[1]


def test_the_setup_folder_walk_follows_no_symlink_out_of_it(env: dict[str, str], tmp_path: Path) -> None:
    """Only regular files and folders inside the setup folder change: a link in it to a file or a folder
    somewhere else leaves that file, that folder and what is in it as they were."""
    want = _agent_writes(env)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shared.txt").write_text("not agentsync's\n", encoding="utf-8")
    for p, mode in ((outside / "shared.txt", 0o644), (outside, 0o755)):
        p.chmod(mode)
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    (setup / "link-to-file").symlink_to(outside / "shared.txt")
    (setup / "local-work" / "link-to-folder").symlink_to(outside, target_is_directory=True)
    assert install_sh(env, "--report-only").returncode == 0
    assert _modes(want) == want
    assert _modes([outside, outside / "shared.txt"]) == {outside: 0o755, outside / "shared.txt": 0o644}

    # The setup folder itself a link to a folder somewhere else: nothing there is changed either.
    elsewhere = tmp_path / "elsewhere"
    setup.rename(elsewhere)
    setup.symlink_to(elsewhere, target_is_directory=True)
    loose = _agent_writes(env)  # written through the link, so these are the files in ``elsewhere``
    assert install_sh(env, "--report-only").returncode == 0
    assert all(_mode(p) in (0o644, 0o755) for p in loose if p != setup), _modes(loose)


def test_a_setup_log_somewhere_else_is_not_walked(
    env: dict[str, str], folder: Path, wheel: Path, tmp_path: Path
) -> None:
    """``AGENTSYNC_SETUP_LOG`` may name a folder agentsync did not make. The installer still closes that
    folder and its own files in it, as before, but walks nothing else there."""
    elsewhere = tmp_path / "a shared folder"
    (elsewhere / "sub").mkdir(parents=True)
    (elsewhere / "theirs.txt").write_text("not agentsync's\n", encoding="utf-8")
    for p, mode in ((elsewhere / "theirs.txt", 0o644), (elsewhere / "sub", 0o755)):
        p.chmod(mode)
    cp = install_sh(
        {**env, "AGENTSYNC_SETUP_LOG": str(elsewhere / "install.log")},
        str(wheel),
        "--source-local",
        str(folder),
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _mode(elsewhere / "install.log") == 0o600
    assert _modes([elsewhere / "theirs.txt", elsewhere / "sub"]) == {
        elsewhere / "theirs.txt": 0o644,
        elsewhere / "sub": 0o755,
    }


@pytest.mark.parametrize(
    "argv", [["--report-only"], ["--log", "3", "deviation", "x", "-"]], ids=["report", "log"]
)
def test_a_dry_run_changes_no_mode_in_the_setup_folder(env: dict[str, str], argv: list[str]) -> None:
    want = _agent_writes(env)
    cp = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, *argv)
    assert cp.returncode == 0, cp.stderr
    assert all(_mode(p) in (0o644, 0o755) for p in want), _modes(want)


# ---- install.out: what the agent saw (K17) -----------------------------------------------------------------


def install_out(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / "agent-context" / "setup" / "install.out"


def test_install_out_holds_stdout_and_stderr_of_each_run(env: dict[str, str], folder: Path) -> None:
    cp = install_sh(
        {**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 1
    listing = install_sh(env, "--list-folders")
    assert listing.returncode == 0, listing.stderr
    out = install_out(env)
    assert (out.stat().st_mode & 0o777) == 0o600
    text = out.read_text()
    heads = [ln for ln in text.splitlines() if ln.startswith("# run=")]
    quoted = str(folder).replace(" ", "\\ ")  # printf %q
    assert len(heads) == 2
    assert heads[0].endswith(f" install.sh --source-local {quoted} --confirm-install-agent")
    assert heads[1].endswith(" install.sh --list-folders")
    assert "error: uv tool install failed" in text, "stderr is copied"
    assert "error: Failed to download (stub)" in text
    assert last_line(cp) in text and last_line(listing) in text, "the NEXT lines are copied"
    assert "error: uv tool install failed" in cp.stderr and "error:" not in cp.stdout, "streams stay apart"


def test_install_out_keeps_the_last_2000_lines(env: dict[str, str], folder: Path, wheel: Path) -> None:
    out = install_out(env)
    out.parent.mkdir(parents=True)
    out.write_text("".join(f"old {i}\n" for i in range(2500)))
    cp = install_sh(env, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = out.read_text().splitlines()
    assert len(lines) == 2000 and lines[-1] == last_line(cp)
    assert "old 2499" in lines and "old 0" not in lines


def test_a_process_group_stop_still_ends_the_run(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """A tool that stops the whole process group (the tees too) still gets the log's end, the report and
    NEXT: the tees ignore the signal and end when the run closes its output."""
    e = {**env, "STUB_SYNC_SLEEP": "20"}
    argv = [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder), "--confirm-install-agent"]
    proc = subprocess.Popen(
        argv,
        env=e,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not any(c.startswith("agentsync sync --once") for c in calls(env)):
            assert proc.poll() is None and time.monotonic() < deadline, "the first sync never started"
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGTERM)
        stdout, _stderr = proc.communicate(timeout=30)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    assert proc.returncode == 143, stdout
    assert stdout.rstrip("\n").splitlines()[-1].startswith("NEXT: this run was stopped before it finished")
    log = install_log(env)
    assert re.search(r" end rc=143 seconds=\d+$", log[-1]) and " step=report " in log[-2]
    assert "NEXT: this run was stopped before it finished" in install_out(env).read_text()


# ---- the friction log through the installer (L4: no `>> ~/...` redirect in the prompt) ---------------------


def friction_path(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"


def test_log_start_log_and_report_only_write_the_friction_log(env: dict[str, str]) -> None:
    """KISS K17: --report-only, not a separate --log-end, appends the attempt's end line."""
    start = install_sh(env, "--log-start", started("Claude Code, claude-opus-5-5"))
    assert start.returncode == 0, start.stderr
    assert start.stdout == f"friction log: attempt started in {friction_path(env)}\n"
    one = install_sh(env, "--log", "1", "question", "asked which terminal app", "-")
    assert one.returncode == 0, one.stderr
    assert one.stdout == f"friction log: question logged in {friction_path(env)}\n"
    two = install_sh(env, "--log", "step 2", "error", "install.sh exited 3", "a longer wait")
    assert two.returncode == 0, two.stderr
    end = install_sh(env, "--report-only")
    assert end.returncode == 0, end.stderr
    assert f"friction log: attempt finished in {friction_path(env)}\n" in end.stdout
    lines = friction_path(env).read_text().splitlines()
    t = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
    assert re.fullmatch(rf"Attempt: {t}", lines[0])
    assert lines[1:3] == [f"Prompt: v{COMPAT}", "Agent: Claude Code, claude-opus-5-5"], (
        "the version is not the agent's"
    )
    assert re.fullmatch(rf"{t} \| step 1 \| question \| asked which terminal app \| -", lines[3])
    assert re.fullmatch(rf"{t} \| step 2 \| error \| install.sh exited 3 \| a longer wait", lines[4])
    assert re.fullmatch(rf"{t} \| end \| finished", lines[5]) and len(lines) == 6
    parsed = setup_report.parse_friction(friction_path(env).read_text())
    (attempt,) = parsed.attempts
    assert attempt.header["Prompt"] == f"v{COMPAT}" and attempt.version == COMPAT and attempt.finished
    assert [(e.step, e.kind, e.what, e.fix) for e in attempt.events] == [
        (1, "question", "asked which terminal app", ""),
        (2, "error", "install.sh exited 3", "a longer wait"),
        (None, "finished", "finished", ""),
    ]


def test_log_start_stops_a_copy_of_the_prompt_that_is_not_the_installers(env: dict[str, str]) -> None:
    """The README prompt hands ``--log-start`` its own version. A copy whose version is not the installer's
    is not the README's of this checkout: older (a saved copy; one from before v8 names no version) or newer
    (the checkout did not update). Either is logged with the version it gave and a step 1 error line, told
    to stop and to copy the prompt again from the README on the main branch, and exits 2, which stops step
    1's command. A value that does not start with a version is a copy that names none.

    The agent is told to run no other step, so the stop closes the attempt itself. And the message names no
    value that passes: an old copy's agent that read "prompt v8, " there used it, was logged as v8, and ran
    its old wording after all. Its last sentence is for a current copy whose agent changed the value."""
    older, newer = COMPAT - 1, COMPAT + 1
    cases = [
        (
            "Claude Code, claude-opus-5-5",
            "v7 or older",
            "Claude Code, claude-opus-5-5",
            "the pasted copy is not",
        ),
        (
            f"prompt v{older}, Copilot CLI",
            f"v{older}",
            "Copilot CLI",
            "the pasted copy is not the current one",
        ),
        (
            f"prompt v{newer}, Copilot CLI",
            f"v{newer}",
            "Copilot CLI",
            "this checkout is older than the prompt",
        ),
        (
            f"PROMPT V{newer}; Copilot CLI",
            f"v{newer}",
            "Copilot CLI",
            "this checkout is older than the prompt",
        ),
        (f"Copilot CLI (prompt v{COMPAT})", "v7 or older", f"Copilot CLI (prompt v{COMPAT})", "is not"),
        (f"prompt v{COMPAT}x, y", "v7 or older", f"prompt v{COMPAT}x, y", "is not"),
        ("prompt v1234, x", "v7 or older", "prompt v1234, x", "the pasted copy is not the current one"),
    ]
    for value, said, agent, why in cases:
        cp = install_sh(env, "--log-start", value)
        assert cp.returncode == 2 and cp.stdout == "", (value, cp.stdout, cp.stderr)
        assert cp.stderr.startswith(
            f"error: the pasted setup prompt is {said} and this installer is for setup prompt v{COMPAT}: "
        ), cp.stderr
        assert why in cp.stderr and cp.stderr.count("\n") == 1, "one line"
        assert (
            f"Stop here and run no other step of that prompt. Tell the person to copy the prompt again from "
            f'{PROMPT_SOURCE} ("Set up on a new Mac: one prompt") and paste it into a new session. If the '
            f'first line of your prompt says "setup prompt v{COMPAT}", the --log-start value was changed: '
            "run step 1's command again exactly as the prompt writes it.\n"
        ) in cp.stderr
        assert f"prompt v{COMPAT}," not in cp.stderr.replace(value, ""), "no value that passes is shown"
        attempt = setup_report.parse_friction(friction_path(env).read_text()).attempts[-1]
        assert attempt.header == {"Prompt": said, "Agent": agent}
        event, closed = attempt.events
        assert (event.step, event.kind) == (1, "error") and why in event.what
        assert event.fix == f"copy the prompt again from {PROMPT_SOURCE}"
        assert closed.kind == "finished" and attempt.finished, "the stop closes its own attempt"
    assert len(setup_report.parse_friction(friction_path(env).read_text()).attempts) == len(cases)
    ok = install_sh(env, "--log-start", f"prompt v{COMPAT},Copilot CLI")
    assert ok.returncode == 0 and ok.stderr == "", "the space after the comma is not required"
    newest = setup_report.parse_friction(friction_path(env).read_text()).attempts[-1]
    assert newest.header == {"Prompt": f"v{COMPAT}", "Agent": "Copilot CLI"} and not newest.events
    assert sorted(p.name for p in friction_path(env).parent.iterdir()) == ["friction.md"]
    reported = install_sh(env, "--report-only")
    assert reported.returncode == 0 and last_line(reported).startswith("NEXT: review "), (
        "the newest attempt is the current prompt's: its report is the one to bring back"
    )


@pytest.mark.parametrize(
    ("value", "agent"),
    [
        ("Prompt v{n}, Claude Code", "Claude Code"),
        ("prompt v{n} - Claude Code", "Claude Code"),
        ("prompt v{n}: Claude Code", "Claude Code"),
        ("prompt v{n} , Claude Code", "Claude Code"),
        ("PROMPT V{n};Claude Code: a-model", "Claude Code: a-model"),
        ("prompt v{n}", "unknown"),
    ],
)
def test_log_start_reads_the_current_version_in_the_shape_an_agent_gave_it(
    env: dict[str, str], value: str, agent: str
) -> None:
    """A current copy whose agent wrote the value with a capital, a colon or a dash was logged as "v7 or
    older", stopped, and told to have the person copy the prompt again; the fresh copy was the same text,
    and the same agent failed the same way. The version still has to lead the value, and its number is
    read whatever stands between it and the tool."""
    cp = install_sh(env, "--log-start", value.format(n=COMPAT))
    assert cp.returncode == 0 and cp.stderr == "", (value, cp.stderr)
    [attempt] = setup_report.parse_friction(friction_path(env).read_text()).attempts
    assert attempt.header == {"Prompt": f"v{COMPAT}", "Agent": agent} and not attempt.events


STOPPED_NEXT = "NEXT: this report is of an attempt that an out-of-date copy of the setup prompt started"


def _backdate(env: dict[str, str], minutes: int) -> None:
    """Move every time in the friction log ``minutes`` back, as if its lines had been logged then."""

    def earlier(found: re.Match[str]) -> str:
        at = datetime.strptime(found.group(0), "%Y-%m-%dT%H:%M:%SZ") - timedelta(minutes=minutes)
        return at.strftime("%Y-%m-%dT%H:%M:%SZ")

    log = friction_path(env)
    log.write_text(re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", earlier, log.read_text()))


def _stopped_in_the_report(env: dict[str, str]) -> bool:
    """Whether the attempt ``agentsync setup-report`` judges (the log's last one) is one ``--log-start``
    stopped: what the Summary of that report is about."""
    latest = setup_report.parse_friction(friction_path(env).read_text()).latest
    assert latest is not None
    return any("install.sh --log-start: the pasted setup prompt is" in e.what for e in latest.events)


def test_report_only_says_copy_again_only_for_an_attempt_log_start_stopped(env: dict[str, str]) -> None:
    """The NEXT of ``--report-only`` says "copy the prompt again" for the attempt ``--log-start`` stopped, by
    that attempt's own error line. An attempt an earlier installer logged (its ``Prompt:`` line is older, and
    nothing stopped it) keeps the usual NEXT.

    The attempt ends where the report ends it, so the NEXT and the Summary are of the same attempt. A line
    the stopped session logs after the stop is still its own: an old copy's text says "log it and go to
    step 3's report", and that report must not read "bring it back". A step 1 error more than ten minutes
    later is another session's, whose step 1 failed before ``--log-start``: its report is brought back."""
    log = friction_path(env)
    log.parent.mkdir(parents=True)
    log.write_text(
        "Attempt: 2026-10-06T16:42:00Z\nPrompt: v7\nAgent: x\n2026-10-06T17:10:00Z | end | finished\n"
    )
    assert last_line(install_sh(env, "--report-only")).startswith("NEXT: review "), "nothing stopped it"
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert last_line(install_sh(env, "--report-only")).startswith(STOPPED_NEXT)
    assert last_line(install_sh(env, "--report-only")).startswith(STOPPED_NEXT), "the same on a second run"
    assert sum(ln.endswith(" | end | finished") for ln in log.read_text().splitlines()) == 2, "one each"
    assert install_sh(env, "--log", "1", "error", "install.sh --log-start exited 2", "-").returncode == 0
    assert install_sh(env, "--log", "3", "deviation", "wrote the report after the stop", "-").returncode == 0
    assert last_line(install_sh(env, "--report-only")).startswith(STOPPED_NEXT), "its own late lines"
    assert _stopped_in_the_report(env)
    _backdate(env, 11)
    assert install_sh(env, "--log", "1", "error", "git pull failed (exit 1)", "-").returncode == 0
    assert last_line(install_sh(env, "--report-only")).startswith("NEXT: review "), "another session's line"
    assert not _stopped_in_the_report(env)


def test_the_report_of_a_stopped_copy_invites_no_bring_back(env: dict[str, str]) -> None:
    """The v10 rehearsal (2026-10-08): the ``--report-only`` of an attempt ``--log-start`` stopped printed
    "bring back:" and an issue link above "NEXT: ... do not bring it back", and wrote bring-back.md over the
    one of the last real session. That run prints neither line and writes no bring-back file: one that
    exists keeps its bytes, and none is made where there was none. The report itself is still written."""
    back = report_path(env).parent / "bring-back.md"

    def invites(cp: subprocess.CompletedProcess[str]) -> list[str]:
        return [
            ln.split(":", 1)[0]
            for ln in cp.stdout.splitlines()
            if ln.startswith(("bring back:", "issue link"))
        ]

    assert install_sh(env, "--log-start", "x").returncode == 2
    stopped = install_sh(env, "--report-only")
    assert stopped.returncode == 0 and last_line(stopped).startswith(STOPPED_NEXT), stopped.stdout
    assert invites(stopped) == [] and report_path(env).is_file() and not back.exists()
    _backdate(env, 11)
    assert install_sh(env, "--log", "1", "error", "git pull failed (exit 1)", "-").returncode == 0
    real = install_sh(env, "--report-only")
    assert last_line(real).startswith("NEXT: review ") and invites(real) == [
        "bring back",
        "issue link (review the report first)",
    ]
    kept = back.read_bytes()
    assert install_sh(env, "--log-start", "x").returncode == 2
    stopped = install_sh(env, "--report-only")
    assert last_line(stopped).startswith(STOPPED_NEXT) and invites(stopped) == [], stopped.stdout
    assert back.read_bytes() == kept


def test_a_session_after_a_stopped_copy_that_wrote_no_report_is_not_called_out_of_date(
    env: dict[str, str],
) -> None:
    """An old copy is stopped and, as told, runs no other step: no ``--report-only`` closes its attempt.
    The person pastes the current prompt in a new session, whose step 1 fails before ``--log-start`` (git
    pull through a proxy). Its error line joined the attempt left open, and its report said "an out-of-date
    copy started this, do not bring it back": the real failure never came back. The stop closes its own
    attempt, so the line follows an end line and is judged as the report judges it."""
    assert install_sh(env, "--log-start", "Claude Code, some-model").returncode == 2
    _backdate(env, 11)
    assert install_sh(env, "--log", "1", "error", "git pull failed (exit 1)", "-").returncode == 0
    out = install_sh(env, "--report-only")
    assert out.returncode == 0 and last_line(out).startswith("NEXT: review "), last_line(out)
    assert not _stopped_in_the_report(env)
    first, second = setup_report.parse_friction(friction_path(env).read_text()).attempts
    assert first.finished and [e.kind for e in second.events] == ["error"] and not second.header


def test_the_installer_and_the_report_end_a_stopped_attempt_at_the_same_line(env: dict[str, str]) -> None:
    """``install.sh --report-only`` words its NEXT from the friction log in awk, and ``agentsync
    setup-report`` words the Summary from ``parse_friction``. Both go by one rule, with one number: a step
    1 error more than ``_NEW_SESSION_GAP`` after an attempt's end line starts another session's attempt, and
    any other late line stays. Each log below gets the same answer from both."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [gap] = re.findall(r"^NEW_SESSION_GAP=(\d+) ", script, re.MULTILINE)
    assert int(gap) == setup_report._NEW_SESSION_GAP.total_seconds()
    stop = (
        "Attempt: 2026-10-06T16:42:00Z\nPrompt: v7 or older\nAgent: x\n"
        "2026-10-06T16:42:00Z | step 1 | error | install.sh --log-start: the pasted setup prompt is v7 or "
        "older and this installer is for setup prompt v8: the pasted copy is not the current one; setup "
        "stopped | copy the prompt again\n"
    )
    end = "2026-10-06T16:42:00Z | end | finished\n"
    logs = {
        "the stop alone": stop + end,
        "a stop an older installer left open": stop,
        "a late step 1 error, ten minutes on": stop + end + "2026-10-06T16:52:00Z | step 1 | error | x | -\n",
        "a step 1 error a second past the gap": stop
        + end
        + "2026-10-06T16:52:01Z | step 1 | error | x | -\n",
        "a step 1 error the next day": stop + end + "2026-10-07T00:00:00Z | step 1 | error | x | -\n",
        "a step 1 error over a month end": stop.replace("10-06T16:42", "10-31T23:55")
        + end.replace("10-06T16:42", "10-31T23:55")
        + "2026-11-01T00:06:00Z | step 1 | error | x | -\n",
        "a late line that is no step 1 error": stop
        + end
        + "2026-10-07T09:00:00Z | step 3 | deviation | x | -\n",
        "a late click in step 1": stop + end + "2026-10-07T09:00:00Z | step 1 | click | x | -\n",
        "another session, then its own lines": stop
        + end
        + "2026-10-07T09:00:00Z | step 1 | error | x | -\n"
        + "2026-10-07T09:00:05Z | step 3 | deviation | y | -\n",
        "a current attempt after it": stop
        + end
        + f"Attempt: 2026-10-07T09:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n",
        "a stopped attempt after a finished one": "Attempt: 2026-10-05T10:00:00Z\nPrompt: v7\nAgent: x\n"
        "2026-10-05T10:30:00Z | end | finished\n" + stop + end,
    }
    answers = {}
    for name, text in logs.items():
        friction_path(env).parent.mkdir(parents=True, exist_ok=True)
        friction_path(env).write_text(text)
        said = last_line(install_sh(env, "--report-only")).startswith(STOPPED_NEXT)
        assert said == _stopped_in_the_report(env), name
        answers[name] = said
    assert [name for name, said in answers.items() if not said] == [
        "a step 1 error a second past the gap",
        "a step 1 error the next day",
        "a step 1 error over a month end",
        "another session, then its own lines",
        "a current attempt after it",
    ]


def test_friction_options_touch_nothing_else(env: dict[str, str]) -> None:
    for argv in (["--log-start", started("Copilot CLI")], ["--log", "1", "click", "x", "-"]):
        assert install_sh(env, *argv).returncode == 0
    setup = friction_path(env).parent
    assert sorted(p.name for p in setup.iterdir()) == ["friction.md"], "no install.log, no install.out"
    assert not report_path(env).exists() and calls(env) == [], "no report, no uv, agentsync or launchctl"
    assert (setup.stat().st_mode & 0o777) == 0o700 and (setup.parent.stat().st_mode & 0o777) == 0o700
    assert (friction_path(env).stat().st_mode & 0o777) == 0o600


def test_friction_options_are_fast(env: dict[str, str]) -> None:
    for argv in (["--log-start", started("a")], ["--log", "2", "approval", "x", "-"]):
        best = min(_timed(env, argv) for _ in range(3))
        assert best < 0.2, f"{argv[0]} took {best:.3f}s"


def _timed(env: dict[str, str], argv: list[str]) -> float:
    t0 = time.monotonic()
    assert install_sh(env, *argv).returncode == 0
    return time.monotonic() - t0


def test_log_writes_its_arguments_literally(env: dict[str, str], tmp_path: Path) -> None:
    """Backticks and $( ) are text, never run; a typographic apostrophe (U+2019) is kept; a line break becomes
    a space."""
    marker = tmp_path / "ran"
    what = f"ran `touch {marker}` and $(touch {marker}) then ${{HOME}} it\u2019s \\n done"
    fix = "wrap it in 'single quotes'\nnext line"
    cp = install_sh(env, "--log", "3", "deviation", what, fix)
    assert cp.returncode == 0, cp.stderr
    assert not marker.exists(), "nothing in an argument was run"
    (line,) = friction_path(env).read_text().splitlines()
    assert line.endswith(f" | step 3 | deviation | {what} | wrap it in 'single quotes' next line")
    assert "it\u2019s" in line and "${HOME}" in line and env["HOME"] not in line


def test_log_with_a_bad_kind_exits_2_and_logs_an_error_line(env: dict[str, str]) -> None:
    cp = install_sh(env, "--log", "2", "worked-around", "reran the install", "-")
    assert cp.returncode == 2
    assert (
        "usage error: --log kind 'worked-around' is not question, click, approval, deviation, error or "
        in (cp.stderr)
    )
    assert cp.stdout == "" and "NEXT:" not in cp.stderr
    (line,) = friction_path(env).read_text().splitlines()
    assert line.endswith(
        " | step 2 | error | install.sh --log: unknown kind 'worked-around' (not question, click, approval, "
        "deviation, error or prompt); the event was: reran the install | -"
    )
    (event,) = setup_report.parse_friction(line + "\n").attempts[0].events
    assert (event.step, event.kind) == (2, "error")


def test_log_with_a_wrong_argument_count_exits_2_and_keeps_what_was_given(env: dict[str, str]) -> None:
    cp = install_sh(env, "--log", "2", "error", "exit 3")
    assert cp.returncode == 2 and "usage error: --log takes four arguments" in cp.stderr
    (line,) = friction_path(env).read_text().splitlines()
    assert line.endswith(
        " | error | install.sh --log got 3 argument(s), not 4 (STEP KIND WHAT FIX, each in single quotes): "
        "2 / error / exit 3 | -"
    )


def test_log_step_forms(env: dict[str, str]) -> None:
    assert install_sh(env, "--log", "Step 2", "prompt", "a", "b").returncode == 0
    assert install_sh(env, "--log", "-", "prompt", "c", "d").returncode == 0
    assert install_sh(env, "--log", "2b", "prompt", "e", "f").returncode == 0
    lines = friction_path(env).read_text().splitlines()
    assert lines[0].endswith(" | step 2 | prompt | a | b")
    assert re.fullmatch(r"\S+ \| prompt \| c \| d", lines[1])
    assert re.fullmatch(r"\S+ \| prompt \| \(step 2b\) e \| f", lines[2])


def test_friction_options_are_exclusive(env: dict[str, str]) -> None:
    for argv in (
        ["--log-start", "a", "--report-only"],
        ["--log-start"],
        ["--log-end", "--report-only"],
        ["--report-only", "--log-end"],
        ["--report-only", "--log-start", "a"],
    ):
        cp = install_sh(env, *argv)
        assert cp.returncode == 2, argv
        assert "usage error:" in cp.stderr and "NEXT:" not in cp.stdout, argv
    assert list(Path(env["HOME"]).iterdir()) == [], "nothing written"


def test_friction_log_path_follows_the_env_and_appends(env: dict[str, str], tmp_path: Path) -> None:
    f = tmp_path / "elsewhere" / "f.md"
    f.parent.mkdir()
    f.parent.chmod(0o755)
    f.write_text("earlier line without a newline")
    e = {**env, "AGENTSYNC_FRICTION_LOG": str(f), "AGENTSYNC_SETUP_REPORT": str(tmp_path / "report.md")}
    assert install_sh(e, "--log-start", started("a")).returncode == 0
    assert install_sh(e, "--report-only").returncode == 0
    lines = f.read_text().splitlines()
    assert lines[0] == "earlier line without a newline" and lines[1].startswith("Attempt: ")
    assert lines[-1].endswith(" | end | finished") and len(lines) == 5
    assert (f.stat().st_mode & 0o777) == 0o600
    assert (f.parent.stat().st_mode & 0o777) == 0o755, "a folder it did not create keeps its mode"
    assert not (Path(env["HOME"]) / "agent-context").exists()


def test_report_only_closes_the_current_attempt_once(env: dict[str, str]) -> None:
    """KISS K17: --report-only appends the end line only when the last attempt has none: never twice, never
    without an attempt, and a later attempt is closed on its own."""
    f = friction_path(env)
    assert install_sh(env, "--report-only").returncode == 0
    assert not f.exists(), "no friction log, no attempt to close"
    assert install_sh(env, "--log-start", started("a")).returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    again = install_sh(env, "--report-only")
    assert again.returncode == 0 and "friction log:" not in again.stdout
    assert [ln.split(" | ", 1)[-1] for ln in f.read_text().splitlines()][3:] == ["end | finished"]
    assert install_sh(env, "--log-start", started("b")).returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    attempts = setup_report.parse_friction(f.read_text()).attempts
    assert [a.finished for a in attempts] == [True, True]
    assert sum(ln.endswith(" | end | finished") for ln in f.read_text().splitlines()) == 2


def test_a_dry_run_writes_no_friction_line(env: dict[str, str]) -> None:
    """K17 review: AGENTSYNC_INSTALL_DRY_RUN=1 covers the friction options: one "dry run:" line, exit 0."""
    dry = {**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}
    for argv in (["--log-start", "a"], ["--log", "1", "click", "x", "-"], ["--log-end"]):
        cp = install_sh(dry, *argv)
        assert cp.returncode == 0, cp.stderr
        assert cp.stdout == (
            f"dry run: {argv[0]} would write to the friction log {friction_path(env)}; nothing is written\n"
        )
    assert list(Path(env["HOME"]).iterdir()) == [], "nothing written"


def test_a_dry_run_report_only_leaves_the_attempt_open(env: dict[str, str]) -> None:
    """K17 review: the dry run of --report-only neither closes the attempt nor writes the report."""
    assert install_sh(env, "--log-start", started("a")).returncode == 0
    before = friction_path(env).read_bytes()
    cp = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert friction_path(env).read_bytes() == before and not report_path(env).exists()
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("NEXT:")] == [
        "NEXT: re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to write the report"
    ]


def test_a_saved_v6_prompt_still_closes_and_reports(env: dict[str, str]) -> None:
    """K17 review: a saved v6 prompt passes its own "6 or higher" gate. Since v8 the installer stops it at
    step 1 (its ``--log-start`` names no version) and closes the attempt, and its text then goes to its step
    3, ``--log-end && --report-only``, which still works: the hidden --log-end exits 0 and adds no second
    end line, and the report's NEXT says to copy the prompt again instead of bringing that report back."""
    assert install_sh(env, "--log-start", "a").returncode == 2
    end = install_sh(env, "--log-end")
    assert end.returncode == 0 and end.stdout == "", "the stop closed the attempt: nothing is left to close"
    reported = install_sh(env, "--report-only")
    assert reported.returncode == 0
    assert last_line(reported).startswith(
        "NEXT: this report is of an attempt that an out-of-date copy of the setup prompt started, so do not "
        f'bring it back: copy the prompt again from {PROMPT_SOURCE} ("Set up on a new Mac: one prompt"; this '
        f"installer is for setup prompt v{COMPAT}) and paste it into a new session [setup report: "
    )
    assert sum(ln.endswith(" | end | finished") for ln in friction_path(env).read_text().splitlines()) == 1
    assert report_path(env).is_file()


def test_no_report_is_ignored(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """K17 review: the old baseline prompt's ``install.sh --no-report && ... install-skill`` still runs."""
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--no-report")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert sum(ln.startswith("NEXT:") for ln in cp.stdout.splitlines()) == 1
    assert "--no-report" not in last_line(cp)


def test_the_readme_prompt_logs_only_through_the_installer() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Set up on a new Mac: one prompt\n", 1)[1]
    block = section.split("```text\n", 1)[1].split("\n```", 1)[0]
    assert ">>" not in block, "no redirect to a ~ path: Claude Code asks for every one (L4)"


# ---- the report step comes before the run's end line (V4) --------------------------------------------------


def test_report_step_line_comes_before_the_end_line(env: dict[str, str], folder: Path, wheel: Path) -> None:
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    log = install_log(env)
    assert " step=report " in log[-2] and re.search(r" end rc=0 seconds=\d+$", log[-1])
    assert sum(" end rc=" in ln for ln in log) == 1, "the provisional end line was replaced, not repeated"
    (run,) = setup_report.read_install_runs(install_log_path(env))
    assert run.rc == 0 and run.step("report") is not None
    assert (install_log_path(env).stat().st_mode & 0o777) == 0o600


def test_report_sees_a_finished_run(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """The report is written while the provisional end line is in the log: it reads the run's exit status."""
    home = Path(env["HOME"])
    seen = home / "seen.log"
    stub = STUB_AGENTSYNC.replace(
        "  setup-report)\n", f'  setup-report)\n    cp "$HOME/agent-context/setup/install.log" {seen}\n'
    )
    _write_exe(Path(env["STUB_AGENTSYNC"]), stub)
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    at_report = seen.read_text().splitlines()
    assert re.search(r" end rc=0 seconds=\d+$", at_report[-1]) and "step=report" not in seen.read_text()


def install_log_path(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / "agent-context" / "setup" / "install.log"


# ---- the wait ends once the first background run is past the macOS access check (L2) -----------------------


def _canary_env(env: dict[str, str], folder: Path, log: str = "ok") -> dict[str, str]:
    sentinel = folder / ".agentsync-sentinel"
    return {
        **env,
        "STUB_LC_RUNNING": "1",
        "STUB_LC_CANARIES": f"{folder}:{sentinel}",
        "STUB_LC_CANARY_LOG": log,
        "AGENTSYNC_WAIT_SECONDS": "2",
    }


def test_a_running_first_run_past_its_canaries_succeeds(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    cp = install_sh(
        _canary_env(env, folder), str(wheel), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (
        "background sync: running (first run converts online-only files; it continues after this command)"
        in cp.stdout.splitlines()
    )
    assert "ACTION:" not in cp.stdout
    assert last_line(cp) == f"NEXT: background sync: running; {LOOP_NEXT} [setup report: {report_path(env)}]"
    assert ("wait", "done", "0", "running") in steps(install_log(env))


def test_a_running_run_still_waiting_for_allow_is_not_past_the_check(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e = _canary_env(env, folder, "pending")
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "background sync: running" not in cp.stdout
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("ACTION:")] == [ACTION]
    assert ("wait", "failed", "3", "tcc") in steps(install_log(env))


def test_canary_lines_of_another_run_do_not_count(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """Lines from another pid, or logged before this install's agent step, are not this run's check."""
    e = {**_canary_env(env, folder), "STUB_LC_LOG_PID": "999"}
    lc = Path(env["STUB_LC_DIR"])
    lc.mkdir()
    sentinel = folder / ".agentsync-sentinel"
    (lc / "poll.err.log").write_text(
        "".join(
            f'2020-01-01T00:00:00Z agentsync-launcher[4242]: CANARY_OK path="{p}"\n'
            for p in (folder, sentinel)
        )
    )
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "background sync: running" not in cp.stdout
    assert last_line(cp).startswith("NEXT: the first background run did not exit 0 within 2s")


def test_a_run_that_exits_0_still_succeeds_with_canaries(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e = {**_canary_env(env, folder), "STUB_LC_RUNNING": ""}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "wait: the first background run exited 0 (ok)" in cp.stdout
    assert ("wait", "done", "0", "") in steps(install_log(env))


# ---- the first sync's own summary line (L3) ----------------------------------------------------------------


def test_first_sync_prints_its_converted_and_deferred_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e = {
        **env,
        "STUB_SYNC_OUT": (
            "  alpha: converted 3, deferred 2 online-only\n  beta: converted 1, deferred 0 online-only"
        ),
        "STUB_SYNC_ERR": "warning: from stderr",
    }
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert "first sync: alpha: converted 3, deferred 2 online-only" in out
    assert "first sync: beta: converted 1, deferred 0 online-only" in out
    assert "stub cycle" not in out, "only the summary line(s) of a sync that succeeded"
    assert "warning: from stderr" in cp.stderr, "stderr passes through"
    assert ("first-sync", "done", "0", "converted-4-deferred-2") in steps(install_log(env))


def test_first_sync_note_carries_the_read_again_count(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """A sync that read files again prints ``, read again R``; the step's note then ends ``-reread-R``, so the
    setup report's first-sync clause carries it.  R = 0 leaves the note as it was (the test above)."""
    e = {**env, "STUB_SYNC_OUT": "converted 0, deferred 1 online-only, read again 2"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "first sync: converted 0, deferred 1 online-only, read again 2" in cp.stdout.splitlines()
    assert ("first-sync", "done", "0", "converted-0-deferred-1-reread-2") in steps(install_log(env))


# ---- the on-device OCR helper is built in the helpers step -------------------------------------------------

STUB_TOOL_PYTHON = """#!/bin/bash
echo "python $* config=${AGENTSYNC_CONFIG:-}" >> "$STUB_LOG"
case "$*" in
*agentsync.convert.media*) err="${STUB_MEDIA_ERR:-}" out="${STUB_MEDIA_OUT:-}" rc="${STUB_MEDIA_RC:-0}" ;;
*agentsync.convert.speech*)
	err="${STUB_SPEECH_ERR:-}" out="${STUB_SPEECH_OUT:-}" rc="${STUB_SPEECH_RC:-0}"
	[ -z "${STUB_SPEECH_SLEEP:-}" ] || { sleep "$STUB_SPEECH_SLEEP"; echo "speech finished" >>"$STUB_LOG"; }
	;;
*) err="${STUB_OCR_ERR:-}" out="${STUB_OCR_OUT:-}" rc="${STUB_OCR_RC:-0}" ;;
esac
[ -z "$err" ] || printf '%s\\n' "$err" >&2
[ -z "$out" ] || printf '%s\\n' "$out"
exit "$rc"
"""


def _others(rows: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """The install.log steps other than ``helpers``."""
    return [r for r in rows if r[0] != "helpers"]


def _helpers_row(rows: list[tuple[str, str, str, str]]) -> tuple[str, str, str, str]:
    [row] = [r for r in rows if r[0] == "helpers"]
    return row


def test_helpers_step_builds_the_ocr_helper_and_a_failure_is_only_a_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """Decision D4: install.sh is the one place the helper is compiled (python -I -m agentsync.convert.ocr
    with the tool's interpreter), with or without background sync.  OCR is optional: whatever the build
    does, the run's exit status and its install.log steps other than ``helpers`` are those of a run without
    it, and ``helpers`` is logged right after ``launcher``.  Without developer tools nothing is tried and one
    line says so: status's ocr line sends the reader to this script."""
    home = Path(env["HOME"])
    cfg = home / "agent-context" / "sources.toml"
    tool_py = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    no_tools = "OCR helper: not built (no Xcode or Command Line Tools)"

    def run(**stub: str) -> tuple[subprocess.CompletedProcess[str], list[tuple[str, str, str, str]]]:
        for leftover in (Path(env["STUB_LOG"]), cfg, home / "agent-context" / "setup" / "install.log"):
            leftover.unlink(missing_ok=True)  # every run is a first run
        cp = install_sh({**env, **stub}, str(wheel), "--source-local", str(folder))
        return cp, steps(install_log(env))

    def said(cp: subprocess.CompletedProcess[str]) -> list[str]:
        assert "OCR helper" not in cp.stderr
        return [ln for ln in cp.stdout.splitlines() if "OCR helper" in ln]

    plain, plain_steps = run()  # the tool environment has no interpreter yet: nothing to run
    assert not any(c.startswith("python ") for c in calls(env))
    assert said(plain) == ([] if _have_git() else [no_tools])
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)

    ready = "OCR helper: ready (paper-vision revision 2, helper 0.3.0)"
    failed = "OCR helper: not built (swiftc did not build the OCR helper (exit 1): error: stub)"
    crash = {"STUB_OCR_RC": "1", "STUB_OCR_ERR": "Traceback (most recent call last): stub"}
    for stub, line in (
        ({"STUB_OCR_OUT": ready}, ready),
        ({"STUB_OCR_OUT": failed + "\nsecond line", "STUB_OCR_RC": "1"}, failed),
        (crash, "OCR helper: not built (the build did not run)"),
        # xcode-select -p answers with DEVELOPER_DIR: a folder that is not there is a Mac without the tools.
        ({"STUB_OCR_OUT": ready, "DEVELOPER_DIR": str(home / "no-developer-tools")}, no_tools),
    ):
        cp, got = run(**stub)
        assert (cp.returncode, _others(got)) == (plain.returncode, _others(plain_steps)), (
            cp.stdout + cp.stderr
        )
        assert _helpers_row(got) == (
            ("helpers", "skipped", "0", "no-devtools")
            if line == no_tools or not _have_git()
            else ("helpers", "done", "0", "")
        )
        assert one_next(cp)
        # -m: the build. The config step also asks this interpreter how many folders are synced (-c).
        ran = [c for c in calls(env) if c.startswith("python -I -m agentsync.convert.ocr")]
        if line == no_tools or not _have_git():  # nothing is tried, and the one line says why
            assert ran == [] and said(cp) == [no_tools]
            continue
        assert ran == [f"python -I -m agentsync.convert.ocr config={cfg}"], ran
        assert said(cp) == [line]
        assert "Traceback" not in cp.stderr and "second line" not in cp.stdout
        log = calls(env)
        assert (
            index_of(log, "uv tool install")
            < index_of(log, "python ")
            < index_of(log, "agentsync add-source")
        )
    assert [name for name, *_ in plain_steps][:5] == ["uv", "agentsync", "launcher", "helpers", "config"]
    assert _helpers_row(plain_steps) == (
        "helpers",
        "skipped",
        "0",
        "no-interpreter" if _have_git() else "no-devtools",
    )
    Path(env["STUB_LOG"]).unlink()
    dry = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, str(wheel), "--source-local", str(folder))
    planned = [ln for ln in dry.stdout.splitlines() if "agentsync.convert.ocr" in ln]
    assert planned == ([f"[dry-run] {tool_py} -I -m agentsync.convert.ocr"] if _have_git() else []), (
        dry.stdout
    )
    assert said(dry) == ([] if _have_git() else [no_tools])
    assert not any(c.startswith("python ") for c in calls(env)), "a dry run builds nothing"


def test_helpers_step_builds_the_media_helper_after_ocr_and_a_failure_is_only_a_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The media helper is built where the OCR helper is, right after it, by the same interpreter, on its own
    line.  Whatever either build does, the run's exit status and its install.log steps other than ``helpers``
    are those of a run without them.  Without developer tools nothing is tried and one line says so."""
    home = Path(env["HOME"])
    cfg = home / "agent-context" / "sources.toml"
    tool_py = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    no_tools = "media helper: not built (no Xcode or Command Line Tools)"

    def run(**stub: str) -> tuple[subprocess.CompletedProcess[str], list[tuple[str, str, str, str]]]:
        for leftover in (Path(env["STUB_LOG"]), cfg, home / "agent-context" / "setup" / "install.log"):
            leftover.unlink(missing_ok=True)  # every run is a first run
        cp = install_sh({**env, **stub}, str(wheel), "--source-local", str(folder))
        return cp, steps(install_log(env))

    def said(cp: subprocess.CompletedProcess[str]) -> list[str]:
        assert "media helper" not in cp.stderr
        return [ln for ln in cp.stdout.splitlines() if "media helper" in ln]

    plain, plain_steps = run()  # the tool environment has no interpreter yet: nothing to run
    assert said(plain) == ([] if _have_git() else [no_tools])
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)

    ready = "media helper: ready (avfoundation, helper 1.0.0)"
    off = "media helper: off ([convert] recordings = false)"
    failed = "media helper: not built (swiftc did not build the media helper (exit 1): error: stub)"
    ocr_failed = {"STUB_OCR_OUT": "OCR helper: not built (stub)", "STUB_OCR_RC": "1"}
    crash = {"STUB_MEDIA_RC": "1", "STUB_MEDIA_ERR": "Traceback (most recent call last): stub"}
    for stub, line in (
        ({"STUB_MEDIA_OUT": ready, **ocr_failed}, ready),
        ({"STUB_MEDIA_OUT": off}, off),
        ({"STUB_MEDIA_OUT": failed + "\nsecond line", "STUB_MEDIA_RC": "1"}, failed),
        (crash, "media helper: not built (the build did not run)"),
        ({"STUB_MEDIA_OUT": ready, "DEVELOPER_DIR": str(home / "no-developer-tools")}, no_tools),
    ):
        cp, got = run(**stub)
        assert (cp.returncode, _others(got)) == (plain.returncode, _others(plain_steps)), (
            cp.stdout + cp.stderr
        )
        assert _helpers_row(got) == (
            ("helpers", "skipped", "0", "no-devtools")
            if line == no_tools or not _have_git()
            else ("helpers", "done", "0", "")
        )
        assert one_next(cp)
        ran = [c for c in calls(env) if c.startswith("python -I -m ")]
        if line == no_tools or not _have_git():
            assert ran == [] and said(cp) == [no_tools]
            continue
        assert ran == [
            f"python -I -m agentsync.convert.ocr config={cfg}",
            f"python -I -m agentsync.convert.media config={cfg}",
            f"python -I -m agentsync.convert.speech config={cfg}",
        ], ran
        assert said(cp) == [line]
        assert "Traceback" not in cp.stderr and "second line" not in cp.stdout
        out = cp.stdout.splitlines()
        assert out.index(line) == next(i for i, ln in enumerate(out) if "OCR helper" in ln) + 1
    Path(env["STUB_LOG"]).unlink()
    dry = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, str(wheel), "--source-local", str(folder))
    planned = [ln for ln in dry.stdout.splitlines() if "agentsync.convert." in ln]
    assert planned == (
        [f"[dry-run] {tool_py} -I -m agentsync.convert.{m}" for m in ("ocr", "media", "speech")]
        if _have_git()
        else []
    ), dry.stdout
    assert said(dry) == ([] if _have_git() else [no_tools])
    assert not any(c.startswith("python ") for c in calls(env)), "a dry run builds nothing"


def test_helpers_step_builds_the_speech_helper_after_media_and_a_failure_is_only_a_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The speech helper is built right after the media helper, by the same interpreter, on its own
    ``speech: ...`` line.  Whatever its build does (an older FluidAudio refused, a crash), the run's exit
    status and its install.log steps other than ``helpers`` are those of a run without it.  Without developer
    tools nothing is tried and one line says so."""
    home = Path(env["HOME"])
    cfg = home / "agent-context" / "sources.toml"
    tool_py = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    no_tools = "speech: not built (no Xcode or Command Line Tools)"

    def run(**stub: str) -> tuple[subprocess.CompletedProcess[str], list[tuple[str, str, str, str]]]:
        for leftover in (Path(env["STUB_LOG"]), cfg, home / "agent-context" / "setup" / "install.log"):
            leftover.unlink(missing_ok=True)  # every run is a first run
        cp = install_sh({**env, **stub}, str(wheel), "--source-local", str(folder))
        return cp, steps(install_log(env))

    def said(cp: subprocess.CompletedProcess[str]) -> list[str]:
        assert "speech:" not in cp.stderr
        return [ln for ln in cp.stdout.splitlines() if ln.startswith("speech:")]

    plain, plain_steps = run()
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)

    placed = "speech: off (the speech models are not placed: parakeet-tdt-0.6b-v3/ and speaker-diarization/)"
    older = (
        "speech: not built (FluidAudio 1111111 does not descend from the build floor 04e363c; the speech "
        "helper is not built)"
    )
    for stub, line in (
        (
            {
                "STUB_SPEECH_OUT": placed,
                "STUB_MEDIA_OUT": "media helper: not built (stub)",
                "STUB_MEDIA_RC": "1",
            },
            placed,
        ),
        ({"STUB_SPEECH_OUT": older + "\nsecond line", "STUB_SPEECH_RC": "1"}, older),
        (
            {"STUB_SPEECH_RC": "1", "STUB_SPEECH_ERR": "Traceback (most recent call last): stub"},
            "speech: not built (the build did not run)",
        ),
        ({"STUB_SPEECH_OUT": placed, "DEVELOPER_DIR": str(home / "no-developer-tools")}, no_tools),
    ):
        cp, got = run(**stub)
        assert (cp.returncode, _others(got)) == (plain.returncode, _others(plain_steps)), (
            cp.stdout + cp.stderr
        )
        assert _helpers_row(got) == (
            ("helpers", "skipped", "0", "no-devtools")
            if line == no_tools or not _have_git()
            else ("helpers", "done", "0", "")
        )
        if line == no_tools or not _have_git():
            assert said(cp) == [no_tools]
            continue
        assert [c for c in calls(env) if c.startswith("python -I -m ")][-1] == (
            f"python -I -m agentsync.convert.speech config={cfg}"
        )
        assert said(cp) == [line]
        assert "Traceback" not in cp.stderr and "second line" not in cp.stdout
        out = cp.stdout.splitlines()
        assert out.index(line) == next(i for i, ln in enumerate(out) if "media helper" in ln) + 1


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (the helpers are built only with them)")
def test_helpers_step_prints_progress_lines_while_the_builds_run(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The speech helper's first build took about 100 s in the 2026-10-08 rehearsal and printed nothing.  A
    ticker beside each build prints ``helpers: building the <helper>, still running, <N>s`` at each interval:
    the ticks between the media line and the speech line name the speech helper (the 2026-10-08 v10
    rehearsals read three minutes of "helpers" after both lines said ready), none starts with ``speech:``, and
    the ticker stops before the helper's line."""
    tool_py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    placed = "speech: off (the speech models are not placed: parakeet-tdt-0.6b-v3/ and speaker-diarization/)"
    e = {**env, "AGENTSYNC_PROGRESS_SECONDS": "2", "STUB_SPEECH_SLEEP": "3.5", "STUB_SPEECH_OUT": placed}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    ticks = [i for i, ln in enumerate(out) if ln.startswith("helpers: ")]
    media = next(i for i, ln in enumerate(out) if ln.startswith("media helper: "))
    assert ticks and all(media < i < out.index(placed) for i in ticks), cp.stdout
    for i in ticks:
        assert re.fullmatch(r"helpers: building the speech helper, still running, \d+s", out[i]), out[i]
    assert sum(ln.startswith("speech:") for ln in out) == 1
    assert last_line(cp).startswith("NEXT: ") and one_next(cp)
    assert ("helpers", "done", "0", "") in steps(install_log(env))


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (the helpers are built only with them)")
def test_a_run_stopped_during_the_helper_builds_lets_the_build_end_first(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The builds run in the foreground: a SIGTERM to the installer's pid alone lets the build under way
    finish before the run exits, so the re-run its NEXT line names never meets a second build in the same
    tree.  The ticker beside the builds prints nothing after NEXT."""
    tool_py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    e = {**env, "AGENTSYNC_PROGRESS_SECONDS": "2", "STUB_SPEECH_SLEEP": "3"}
    proc = subprocess.Popen(
        [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder)],
        env=e,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not any(c.startswith("python -I -m agentsync.convert.speech") for c in calls(env)):
            assert proc.poll() is None and time.monotonic() < deadline, "the speech build never started"
            time.sleep(0.05)
        time.sleep(1)
        os.kill(proc.pid, signal.SIGTERM)  # the installer's pid alone
        rc = proc.wait(timeout=30)
        finished = "speech finished" in calls(env)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    assert proc.stdout is not None and proc.stderr is not None
    stdout, stderr = proc.stdout.read(), proc.stderr.read()
    assert rc == 143, stdout + stderr
    assert finished, "the build under way ended before the installer exited"
    assert stdout.rstrip("\n").splitlines()[-1].startswith("NEXT: this run was stopped before it finished"), (
        stdout
    )
    assert sum(ln.startswith("NEXT:") for ln in stdout.splitlines()) == 1
    assert ("helpers", "failed", "143", "") in steps(install_log(env))


# ---- the shell report redacts the per-user temp folder (L6) ------------------------------------------------


def test_shell_report_redacts_tmpdir(env: dict[str, str], folder: Path, tmp_path: Path) -> None:
    tmpdir = tmp_path / "var-folders" / "xy" / "T"
    tmpdir.mkdir(parents=True)
    friction = friction_path(env)
    friction.parent.mkdir(parents=True)
    friction.write_text(
        f"Attempt: 2026-09-30T00:00:00Z\n2026-09-30T00:00:01Z | step 2 | error | {tmpdir}/x | -\n"
    )
    e = {**env, "STUB_UV_INSTALL_RC": "2", "TMPDIR": f"{tmpdir}/"}
    cp = install_sh(e, "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 1
    text = report_path(env).read_text()
    assert "| <tmp>/x |" in text and str(tmpdir) not in text
