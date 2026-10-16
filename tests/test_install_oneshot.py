"""scripts/install.sh as the README one-prompt (setup prompt v19) runs it: ``--version``, ``--log-start``,
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
``pending``: CANARY_OK for the first, then TCC_PENDING). STUB_LC_PATH and STUB_LC_PROGRAM add the job's
``path`` and ``program`` lines.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import plistlib
import pwd
import re
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from agentsync import lints, net, paths, setup_report
from agentsync.config import default_config_text, inbox_source_table, load_config, local_source_table
from agentsync.convert import pandoc as pandoc_converter
from agentsync.ops import doctor
from agentsync.ops.lock import SingleWriterLock

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
  if [ -n "${STUB_SYNC_LOCK:-}" ]; then  # what a sync starting now would meet, and whether the test let go
    { [ -e "$STUB_SYNC_LOCK.released" ] && echo released=yes || echo released=no
      "$STUB_PY" -c 'import sys; from pathlib import Path
from agentsync.ops.lock import SingleWriterLock as S
print("held=%s" % S.is_held(Path(sys.argv[1])))' "$STUB_SYNC_LOCK"; } >> "$STUB_LOG.lock"
  fi
  if [ -n "${STUB_UV_INSTALL_RC:-}" ]; then
    echo "error: Failed to download (stub)" >&2; exit "$STUB_UV_INSTALL_RC"
  fi
  half="${STUB_UV_HALF_REMOVE:-}"  # a tick started the tool's Python while uv removed the environment
  if [ -n "$half" ] && { [ ! -e "$STUB_LOG.half" ] || [ "$half" = always ]; }; then
    touch "$STUB_LOG.half"
    rm -f "$HOME/.local/share/uv/tools/agentsync/bin/python"
    echo "error: failed to remove directory (stub): Directory not empty (os error 66)" >&2; exit 2
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
    [ "$pos" != "${STUB_ADD_SOURCE_FAIL:-}" ] || { echo "add-source failed (stub)" >&2; exit 1; }
    setup; printf '[[source]]\\npath = "%s"\\n' "$pos" >> "$cfg"; inbox; hint "agentsync doctor"
    if [ -n "${STUB_ADD_SOURCE_SUMMARY:-}" ]; then  # the lines cli._ensure_setup ends every call on
      echo "added source '${pos##*/}'"
      [ -f "$cfg.ib" ] ||  # the call that creates the inbox
        { echo "added inbox source 'inbox' ($(dirname "$cfg")/inbox): drop files here"; : > "$cfg.ib"; }
      echo "docs repo $(dirname "$cfg")/docs (exists, no remote; scaffold up to date)"
      [ -f "$cfg.tm" ] || { echo "time machine: excluded the cache (stub)"; : > "$cfg.tm"; }  # the first call
      echo "sources: ${pos##*/}"
    fi ;;
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
    echo "quiet=${AGENTSYNC_QUIET_LOCK_WAIT:-}" >> "$STUB_LOG.quiet"
    echo "stub cycle"
    [ -z "${STUB_SYNC_OUT:-}" ] || printf '%s\\n' "$STUB_SYNC_OUT"
    [ -z "${STUB_SYNC_ERR:-}" ] || printf '%s\\n' "$STUB_SYNC_ERR" >&2
    exit "${STUB_SYNC_RC:-0}" ;;
  install-agent) exit "${STUB_AGENT_RC:-0}" ;;
  setup-report)
    [ -z "${STUB_REPORT_RC:-}" ] || { echo "stub setup-report failed" >&2; exit "$STUB_REPORT_RC"; }
    mkdir -p "$(dirname "$out")"
    # an older agentsync's report, as it is
    if [ -n "${STUB_REPORT_FILE:-}" ]; then cat "$STUB_REPORT_FILE" > "$out"
    else
    printf '# agentsync setup report\\n\\nstub, simulate=%s\\n' "${AGENTSYNC_SIMULATE_LAUNCHD:-0}" > "$out"
    [ -n "${STUB_REPORT_NO_LINK:-}" ] || printf '\\n%s\\n' "$STUB_ISSUE_LINK" >> "$out"
    fi
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
    # STUB_LC_LABELS: the colon list of the job labels loaded; unset, every label is
    if [ -n "${STUB_LC_LABELS+x}" ]; then
      case ":$STUB_LC_LABELS:" in *":${2##*/}:"*) ;; *) exit 113 ;; esac
    fi
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
    [ -z "${STUB_LC_PATH:-}" ] || printf '\\tpath = %s\\n' "$STUB_LC_PATH"
    [ -z "${STUB_LC_PROGRAM:-}" ] || printf '\\tprogram = %s\\n' "$STUB_LC_PROGRAM"
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
    _write_exe(stubbin / "scutil", "#!/bin/bash\nexit 0\n")  # no system proxy: not the developer's own
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
    """README step 1: "If the --version inside step 1's one command does not end with
    "setup-prompt-compat 19", ... stop"."""
    cp = subprocess.run([BASH32, str(INSTALL_SH), "--version"], capture_output=True, text=True, check=False)
    assert cp.returncode == 0, cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[-1] == "setup-prompt-compat 19" == f"setup-prompt-compat {COMPAT}"
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
    assert "first sync: skipped, lock busy" in cp.stdout.splitlines()
    assert ("first-sync", "skipped", "75", "lock-busy") in steps(install_log(env))
    # B13 round 7 H31: the step's own line closes it with no third copy of the news (agentsync's stderr line
    # names the holder), and the first sync is asked for the quiet lock wait, as this step ticks on its own
    assert "another agentsync cycle is running" not in cp.stdout
    assert (Path(env["STUB_LOG"] + ".quiet")).read_text(encoding="utf-8").splitlines() == ["quiet=1"]


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


_HELD_WARN = (
    "[warn] heartbeat.fy26-projects — enumeration incomplete for 3 consecutive passes (deletions held) (held "
    "at its last walk: the end of this run says whether this run's sync cleared it)"
)
_CLEARED = "heartbeat.fy26-projects: this run's sync listed it in full; deletions are no longer held"


@pytest.mark.parametrize("case", ["cleared", "still held", "fail skips the sync"])
def test_a_listing_the_first_sync_cleared_is_said_and_never_inferred(
    env: dict[str, str], folder: Path, wheel: Path, case: str
) -> None:
    """B13 round 6 G33: the status step warns that a listing was held at its last walk; when this run's sync
    clears it, the end of the run says so in a line of its own, rather than leaving the reader to infer it
    from a WAITING ON YOU line that is missing. Still held: the wait, and no such line. A [FAIL] that skips
    the sync: neither."""
    ok = "[ok  ] heartbeat.fy26-projects — last success 2026-10-13T10:00:00Z (0s ago, full_enumeration)"
    wait = "WAITING ON YOU: 1 empty cloud folder(s) keep the listing of fy26-projects incomplete (stub)"
    e = {**env, "STUB_DOCTOR_OUT": _HELD_WARN}
    if case == "cleared":
        e["STUB_STATUS_OUT"] = f"NEXT: {LOOP_NEXT}\n{ok}"
    elif case == "still held":
        e["STUB_STATUS_OUT"] = f"NEXT: {LOOP_NEXT}\n{wait}\n" + _HELD_WARN.partition(" (held")[0]
    else:
        fail = "[FAIL] docs_repo.git — /x/docs does not exist and /x is not writable (fix: mkdir -p /x/docs)"
        e |= {"STUB_DOCTOR_OUT": f"{_HELD_WARN}\n{fail}", "STUB_DOCTOR_RC": "1", "STUB_STATUS_OUT": ok}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    lines = cp.stdout.splitlines()
    assert (_CLEARED in lines) is (case == "cleared"), cp.stdout + cp.stderr
    assert (wait in lines) is (case == "still held"), cp.stdout
    if case == "cleared":
        assert lines.index(_CLEARED) < len(lines) - 1 and one_next(cp)
        assert last_line(cp).startswith(f"NEXT: {LOOP_NEXT}")


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
    """Rule 5's NEXT names the WAITING lines: they are printed above it, with one NEXT. Its phrase names no
    place (B13 round 4 Q74), so the finish, which lists the waits under the last NEXT, reads true too, and
    install.sh no longer rewrites it."""
    rule5 = "stop: the operator confirms the baseline questions (see the WAITING ON YOU lines); session done"
    waits = [
        "WAITING ON YOU: the baseline questions are a draft: in ~/agent-context/docs/_eval, keep up to "
        "about 10 in questions.md (all of them if there are fewer), correct the answers in answers.md, and "
        "change both files to status: confirmed",
        "WAITING ON YOU: 2 queued purge(s): preview with `~/.local/bin/agentsync purge --queue --dry-run` "
        "(writes nothing) before you run `~/.local/bin/agentsync purge --queue` (rewrites history)",
    ]
    out = "\n".join([f"NEXT: {rule5}", *waits, "note: a later sync lists it"])
    cp = install_sh({**env, "STUB_STATUS_OUT": out}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    at = lines.index(waits[0])
    assert lines[at : at + 2] == waits and at < len(lines) - 2
    assert last_line(cp) == (
        "NEXT: stop: the operator confirms the baseline questions (see the WAITING ON YOU lines); "
        "session done "
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
    text = report_path(env).read_text()
    link = _uncomputed_link(env, text)
    assert "&prompt_version=v4" in link, "the v4 log's Prompt: line (B13 round 10 L2)"
    assert cp.stdout.splitlines()[-2] == f"issue link (review the report first): {link}"
    assert text.rstrip("\n").splitlines()[-1] == link, "the report ends on the issue form"
    assert (report_path(env).stat().st_mode & 0o777) == 0o600
    assert _headings(text) == list(setup_report.SECTION_TITLES), "the same headings as agentsync's report"
    assert text.startswith(setup_report.REPORT_TITLE + "\n")
    assert "exit 1 (failed)" in text and "uv tool install failed" in text
    assert (
        "- background sync: com.agentsync.poll plist absent, com.agentsync.reconcile plist absent in this "
        "home (Background runs has each job's state)" in text
    ), "the plists this home holds, not an install that never ran (B13 round 4 Q6), as agentsync labels it"
    assert "simulated" not in _summary(text), "no test-mode word on a real run (B13 round 8 J5)"
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


def test_the_shell_report_shows_run_ids_without_their_process_id(env: dict[str, str]) -> None:
    """B13 round 5 F5, F15: the Python report shows install.sh's run ids without their ``-<pid>`` suffix (a
    fingerprint, not a finding); the shell report printed the install log's tail and its failed steps raw, so
    the pid reached setup-report.md and bring-back.md. Both places now cut it, and only from a run id."""
    log = Path(env["HOME"]) / "agent-context" / "setup" / "install.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2026-10-12T09:01:00Z run=20261012T090100Z-4242 start install.sh compat=12 args=--keep-4242\n"
        "2026-10-12T09:01:06Z run=20261012T090100Z-4242 step=uv seconds=6 result=failed rc=1\n"
        "2026-10-12T09:01:06Z run=20261012T090100Z-4242 end rc=1 seconds=6\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    text = report_path(env).read_text()
    installer = text.split("\n## Installer\n", 1)[1].split("\n## ", 1)[0]
    errors = text.split("\n## Recent errors\n", 1)[1].split("\n## ", 1)[0]
    assert "(run ids without their process id):" in installer
    for part in (installer, errors):
        assert "run=20261012T090100Z " in part and "-4242 " not in part, part
    assert "args=--keep-4242" in installer, "a number that is not a run id's pid is kept"
    back = (report_path(env).parent / "bring-back.md").read_text()
    assert "20261012T090100Z-4242" not in back


def test_the_shell_report_counts_cloud_folders_apart_from_links_and_files(env: dict[str, str]) -> None:
    """B13 round 5 F56: the shell report counted every CloudStorage entry as a provider, a symlink named
    OneDrive-* as a OneDrive too, where setup_report.cloud counts folders and gives links and other entries
    apart (B13 round 4 Q33)."""
    cs = Path(env["HOME"]) / "Library" / "CloudStorage"
    (cs / "OneDrive-Contoso").mkdir(parents=True)
    (cs / "OneDrive-Old").symlink_to(cs / "OneDrive-Contoso")
    (cs / "stray.txt").write_text("")
    (cs / ".hidden").write_text("")
    assert install_sh(env, "--report-only").returncode == 0
    assert (
        "- ~/Library/CloudStorage providers: 1 (OneDrive: 1) and 1 symlink(s) and 1 other entry(ies)\n"
        in report_path(env).read_text()
    )


def test_the_shell_report_counts_uv_pythons_without_uvs_hidden_folder(env: dict[str, str]) -> None:
    """uv keeps a hidden ``.temp`` folder, a ``.lock`` file and minor-version links beside its Pythons: one
    installed Python reads 1, not 2 (B13 round 2 R5)."""
    store = Path(env["HOME"]) / ".local" / "share" / "uv" / "python"
    (store / "cpython-3.11.17-macos-aarch64-none").mkdir(parents=True)
    (store / ".temp").mkdir()
    (store / ".lock").write_text("")
    (store / "cpython-3.11-macos-aarch64-none").symlink_to(store / "cpython-3.11.17-macos-aarch64-none")
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert "- uv-managed Pythons: 1\n" in report_path(env).read_text()


def test_the_shell_report_names_the_gitleaks_the_secret_scan_can_use(env: dict[str, str]) -> None:
    """B13 round 4 Q81: the shell report says which gitleaks the secret scan can use: the one
    ``remember_gitleaks`` recorded, when it is still an executable file (B13 round 5 F58), else the one on
    PATH, else the first of the places ``lints.gitleaks_lookup`` looks, else none (the builtin patterns
    only)."""
    home = Path(env["HOME"])
    places = [
        Path("/opt/homebrew/bin/gitleaks"),
        Path("/usr/local/bin/gitleaks"),
        Path("/opt/local/bin/gitleaks"),
        home / ".local/bin/gitleaks",
        home / "go/bin/gitleaks",
    ]
    assert [str(p).replace(str(home), "~") for p in places] == [str(p) for p in lints._GITLEAKS_CANDIDATES]
    on_mac = next((p for p in places if os.access(p, os.X_OK)), None)
    assert install_sh(env, "--report-only").returncode == 0
    gitleaks = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- gitleaks: ")]
    assert gitleaks == [
        f"- gitleaks: {on_mac}" if on_mac else "- gitleaks: not found (builtin patterns only)"
    ]
    stub = Path(env["PATH"].split(":", 1)[0]) / "gitleaks"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    assert install_sh(env, "--report-only").returncode == 0
    [line] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- gitleaks: ")]
    assert line.startswith("- gitleaks: /") and line.endswith("/stubbin/gitleaks"), "PATH's comes first"
    custom = home / "tools" / "gitleaks"
    custom.parent.mkdir()
    custom.write_text("#!/bin/sh\nexit 0\n")
    custom.chmod(0o755)
    record = home / "Library" / "Application Support" / "agentsync" / "gitleaks-path"
    record.parent.mkdir(parents=True)
    record.write_text(f"{custom}\n")
    with patch.dict(os.environ, {"HOME": str(home), "PATH": env["PATH"]}):
        assert lints.gitleaks_lookup() == custom, "the record is what every sync uses"
    assert install_sh(env, "--report-only").returncode == 0
    [line] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- gitleaks: ")]
    assert line == "- gitleaks: ~/tools/gitleaks", "the recorded path comes first"
    for broken in ("tools/gitleaks", str(home / "tools" / "missing")):
        record.write_text(broken)
        assert install_sh(env, "--report-only").returncode == 0
        [line] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- gitleaks: ")]
        assert line.endswith("/stubbin/gitleaks"), (broken, line)


def test_the_shell_report_only_gives_the_last_install_runs_exit(env: dict[str, str]) -> None:
    """--report-only only writes the report: its own exit 0 said "exit 0 (ok)" over an install log whose last
    run failed. The Summary gives the last install run's exit, dated, and skips a --list-folders run, as
    setup_report._install_line does (B13 round 4 Q23)."""
    log = Path(env["HOME"]) / "agent-context" / "setup" / "install.log"
    log.parent.mkdir(parents=True)

    def line() -> str:
        cp = install_sh(env, "--report-only")
        assert cp.returncode == 0, cp.stdout + cp.stderr
        [found] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- install.sh: ")]
        assert "; this --report-only run only writes the report; " in found and ", exit 0 (ok)" not in found
        return found.split("; this --report-only run only writes the report; ", 1)[1]

    assert line() == "no install log (no install run on this Mac)", "no log is not a log with no run (F29)"
    log.write_text(
        "2026-10-12T09:00:00Z run=a1 start install.sh compat=12 args=--list-folders\n"
        "2026-10-12T09:00:01Z run=a1 step=list-folders seconds=1 result=done rc=0 note=listed-4\n"
        "2026-10-12T09:00:01Z run=a1 end rc=0 seconds=1\n"
    )
    assert line() == "no install run in the install log"
    with log.open("a") as fh:
        fh.write(
            "2026-10-12T09:01:00Z run=b2 start install.sh compat=12 commit=x kind=local source=- args=\n"
            "2026-10-12T09:01:06Z run=b2 step=uv seconds=6 result=failed rc=1\n"
            "2026-10-12T09:01:06Z run=b2 end rc=1 seconds=6\n"
            "2026-10-12T09:02:00Z run=c3 start install.sh compat=12 args=--list-folders\n"
            "2026-10-12T09:02:01Z run=c3 step=list-folders seconds=1 result=done rc=0 note=listed-4\n"
            "2026-10-12T09:02:01Z run=c3 end rc=0 seconds=1\n"
        )
    assert line() == "the last install run (started 2026-10-12T09:01:00Z) exit 1 (failed)"
    with log.open("a") as fh:
        fh.write(
            "2026-10-12T09:03:00Z run=d4 start install.sh compat=12 commit=x kind=local source=- args=\n"
        )
    assert line() == (
        "the last install run (started 2026-10-12T09:03:00Z) has no end line (stopped or still running)"
    )


def test_the_shell_report_only_says_the_last_install_run_was_before_this_attempt(env: dict[str, str]) -> None:
    """B13 round 5 F30: after a step 1 stop the shell report's one status line read "exit 0 (ok)" of an
    earlier session's install run, where the Python report says "no run during this attempt". The attempt
    begins where parse_friction begins it, and a run that started before that is said to be an earlier one;
    a run of the attempt itself, or a log with no friction log beside it, keeps the plain line."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    (setup / "install.log").write_text(
        "2026-10-12T20:02:18Z run=20261012T200218Z-41 start install.sh compat=12 args=\n"
        "2026-10-12T20:04:00Z run=20261012T200218Z-41 end rc=0 seconds=102\n"
    )
    friction = setup / "friction.md"

    def line() -> str:
        cp = install_sh(env, "--report-only")
        assert cp.returncode == 0, cp.stdout + cp.stderr
        [found] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- install.sh: ")]
        return found.split("; this --report-only run only writes the report; ", 1)[1]

    plain = "the last install run (started 2026-10-12T20:02:18Z) exit 0 (ok)"
    assert line() == plain, "no friction log: nothing to scope the run by"
    friction.write_text(
        "Attempt: 2026-10-12T20:01:00Z\nPrompt: v12\nAgent: Contoso Agent\n"
        "2026-10-12T20:06:40Z | end | finished\n"
        "2026-10-12T20:18:46Z | step 1 | error | git pull failed (exit 1) | -\n"
    )
    assert line() == (
        "no install run during this attempt (began 2026-10-12T20:18:46Z); the last one, started "
        "2026-10-12T20:02:18Z, exit 0 (ok)"
    ), "a step 1 error more than ten minutes after the end line is another session's attempt"
    friction.write_text(
        "Attempt: 2026-10-12T20:01:00Z\nPrompt: v12\nAgent: Contoso Agent\n"
        "2026-10-12T20:05:00Z | step 1 | error | git pull failed (exit 1) | -\n"
    )
    assert line() == plain, "a same-session step 1 error after the run: the run is the attempt's"
    with (setup / "install.log").open("a") as fh:
        fh.write("2026-10-12T20:30:00Z run=20261012T203000Z-42 start install.sh compat=12 args=\n")
    friction.write_text("Attempt: 2026-10-12T20:29:00Z\nPrompt: v12\nAgent: Contoso Agent\n")
    assert line() == (
        "the last install run (started 2026-10-12T20:30:00Z) has no end line (stopped or still running)"
    )
    friction.write_text("Attempt: 2026-10-12T20:31:00Z\nPrompt: v12\nAgent: Contoso Agent\n")
    assert line() == (
        "no install run during this attempt (began 2026-10-12T20:31:00Z); the last one, started "
        "2026-10-12T20:30:00Z, has no end line (stopped or still running)"
    )
    # A repeated header with no Attempt: line (a v4 log) opens another attempt, as parse_friction reads it
    # (B13 round 6 G81)
    friction.write_text(
        "Prompt: v4\nAgent: Claude Code\n2026-10-12T20:10:00Z | step 2 | ok | a | -\n"
        "2026-10-12T20:20:00Z | end | finished\n"
        "Prompt: v4\nAgent: Claude Code\n2026-10-12T20:40:00Z | step 2 | ok | b | -\n"
    )
    latest = setup_report.parse_friction(friction.read_text()).latest
    assert latest is not None and latest.begin == datetime(2026, 10, 12, 20, 40, tzinfo=UTC)
    assert line() == (
        "no install run during this attempt (began 2026-10-12T20:40:00Z); the last one, started "
        "2026-10-12T20:30:00Z, has no end line (stopped or still running)"
    )


def _background_part(env: dict[str, str]) -> str:
    return report_path(env).read_text().split("\n## Background runs\n", 1)[1].split("\n## ", 1)[0]


def test_the_shell_report_names_another_installs_job_as_not_this_setups(env: dict[str, str]) -> None:
    """``launchctl print`` reads the uid's domain, not this HOME's: a job loaded from another home's plist, or
    running another home's program, is another install's, and its runs are not this setup's result; a job
    loaded from this home's plist path whose file is gone stays this setup's (B13 round 2 R6, R16)."""
    home = Path(env["HOME"])
    lc = Path(env["STUB_LC_DIR"])
    lc.mkdir()
    (lc / "runs").write_text("2183\n")
    (lc / "last").write_text("79\n")
    plist = home / "Library" / "LaunchAgents" / "com.agentsync.poll.plist"
    other = "/Users/contoso-other"
    for extra in (
        {"STUB_LC_PATH": f"{other}/Library/LaunchAgents/com.agentsync.poll.plist"},
        {"STUB_LC_PATH": str(plist), "STUB_LC_PROGRAM": f"{other}/Applications/AgentSyncLauncher.app/x"},
    ):
        cp = install_sh({**env, **extra}, "--report-only")
        assert cp.returncode == 0, cp.stderr
        part = _background_part(env)
        for label in ("com.agentsync.poll", "com.agentsync.reconcile"):
            assert (
                f"- {label}: loaded, but the job belongs to another install (its plist or ProgramArguments"
                in part
            )
        assert "runs 2183" not in part and "last exit code" not in part
    cp = install_sh(
        {**env, "STUB_LC_PATH": str(plist), "STUB_LC_LABELS": "com.agentsync.poll"}, "--report-only"
    )
    part = _background_part(env)
    assert (
        "- com.agentsync.poll: loaded, but the plist is absent (removed without `launchctl bootout`; "
        "`~/.local/bin/agentsync install-agent` repairs it): state not running, runs 2183, last exit code 79"
        in part
    ), "the full path, as the agentsync report's; its backticks printed, not run (B13 round 6 G74, G78)"
    assert "- com.agentsync.reconcile: plist absent · loaded: no" in part and "another install" not in part
    plist.parent.mkdir(parents=True)
    plist.write_text("")
    cp = install_sh(
        {**env, "STUB_LC_PATH": str(plist), "STUB_LC_LABELS": "com.agentsync.poll"}, "--report-only"
    )
    assert (
        "- com.agentsync.poll: plist present · loaded: state not running, runs 2183, last exit code 79"
        in _background_part(env)
    )


def test_the_shell_report_has_a_line_for_each_job(env: dict[str, str]) -> None:
    """The reconcile job is this setup's too: the shell report names it, each line starting with its label,
    as `agentsync setup-report` does (B13 round 4 Q5, Q15)."""
    agents = Path(env["HOME"]) / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.agentsync.reconcile.plist").write_text("")
    cp = install_sh({**env, "STUB_LC_LABELS": ""}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert _background_part(env).strip().splitlines() == [
        "- com.agentsync.poll: plist absent · loaded: no",
        "- com.agentsync.reconcile: plist present · loaded: no",
    ]
    lc = Path(env["STUB_LC_DIR"])
    lc.mkdir(exist_ok=True)
    (lc / "runs").write_text("3\n")
    (lc / "last").write_text("0\n")
    other = "/Users/contoso-other/Library/LaunchAgents/com.agentsync.reconcile.plist"
    cp = install_sh(
        {**env, "STUB_LC_LABELS": "com.agentsync.reconcile", "STUB_LC_PATH": other}, "--report-only"
    )
    part = _background_part(env)
    assert "- com.agentsync.poll: plist absent · loaded: no" in part
    assert "- com.agentsync.reconcile: loaded, but the job belongs to another install" in part
    assert "- background sync: com.agentsync.poll plist absent, " in report_path(env).read_text()


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
        "F2 | step 3 | clean | 1 | Jane\u2019s folder | -\n",
        encoding="utf-8",
    )
    # Under UTF-8 macOS awk's regex match (the whole-word test) exited at "Jane" + U+2019 (B13 H15).
    utf8 = {**env, "STUB_UV_INSTALL_RC": "2", "LC_ALL": "en_US.UTF-8"}
    cp = install_sh(utf8, "--source-local", str(folder))
    assert cp.returncode == 1
    text = report_path(env).read_text(encoding="utf-8")
    assert "<name> chose; <name> approved; <name> agreed" in text
    assert "Jane" not in text and "Doe" not in text
    assert "<name>\u2019s folder" in text and _headings(text) == list(setup_report.SECTION_TITLES)


@pytest.mark.parametrize(
    "full",
    ["Doe, Jane (Northwind)", "Jane Q. Doe", "Jane Doe, Jr.", "Dr. Jane Doe"],
    ids=["bracketed", "middle", "suffix", "title"],
)
def test_the_shell_report_redacts_the_joined_forms_of_a_display_name(
    env: dict[str, str], folder: Path, full: str
) -> None:
    """A directory display name ("Doe, Jane (Northwind)", "Jane Q. Doe"): the shell fallback forms the joined
    names without the bracketed and middle parts, as the Python report does, and a bracketed word is a lone
    name without its brackets."""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        f'#!/bin/bash\n[ "$1" = -F ] && {{ echo "{full}"; exit 0; }}\nexec /usr/bin/id "$@"\n',
    )
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v4\nF1 | step 3 | clean | 1 | jane-doe, JaneDoe, DOE_JANE and Northwind agreed | -\n"
    )
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder))
    assert cp.returncode == 1
    text = report_path(env).read_text()
    said = (
        "<name>, <name>, <name> and <name> agreed"
        if "(" in full
        else "<name>, <name>, <name> and Northwind agreed"
    )
    assert said in text
    assert "jane" not in text.lower()


def test_the_shell_report_shows_a_login_that_is_the_name_run_together_as_user(
    env: dict[str, str], folder: Path
) -> None:
    """A login that is the full name run together ("janedoe" for "Jane Doe") is <user>, as in the Python
    report: the shell's joined name forms hold "janedoe" too, so the login pair comes first. A bracketed part
    of two letters ("(JJ)") stays a name as written."""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        '#!/bin/bash\ncase "$1" in -F) echo "Jane (JJ) Doe"; exit 0 ;; -un) echo janedoe; exit 0 ;; esac\n'
        'exec /usr/bin/id "$@"\n',
    )
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text("Prompt: v4\nF1 | step 3 | clean | 1 | janedoe ran it; (JJ) and JaneDoe agreed | -\n")
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder))
    assert cp.returncode == 1
    assert "<user> ran it; <name> and <name> agreed" in report_path(env).read_text()


@pytest.mark.parametrize(
    ("gecos", "said", "shown"),
    [
        ("Li, Jane", "asked Li; Jane Li agreed", "asked <name>; <name> agreed"),
        ("Do, Jane", "Do it; Jane Do agreed", "Do it; <name> agreed"),
        ("Tanaka, Ai", "asked Ai; the AI agent agreed", "asked <name>; the AI agent agreed"),
        ("de la Cruz, Maria", "ran ls -la; Maria de la Cruz agreed", "ran ls -la; <name> agreed"),
    ],
    ids=["two-letter", "a-word", "an-abbreviation", "a-particle"],
)
def test_the_shell_report_redacts_a_two_letter_name_part(
    env: dict[str, str], folder: Path, gecos: str, said: str, shown: str
) -> None:
    """The shell fallback registers a two-letter part ("Li") alone by setup_report's rule (B13 H20)."""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        f'#!/bin/bash\n[ "$1" = -F ] && {{ echo "{gecos}"; exit 0; }}\nexec /usr/bin/id "$@"\n',
    )
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(f"Prompt: v4\nF1 | step 3 | clean | 1 | {said} | -\n")
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder))
    assert cp.returncode == 1
    text = report_path(env).read_text()
    assert shown in text, text


def test_the_shell_short_name_lists_are_the_reports() -> None:
    """install.sh's short_name holds setup_report's two lists of two-letter words, word for word."""
    body = INSTALL_SH.read_text().split("\nshort_name() {\n", 1)[1].split("\n}\n", 1)[0]
    lists = [
        set(line.strip().removesuffix(") return 1 ;;").split(" | "))
        for line in body.splitlines()
        if line.strip().endswith(") return 1 ;;") and " | " in line
    ]
    assert lists == [set(setup_report._SHORT_WORDS), set(setup_report._SHORT_CAPS)]


def test_the_shell_report_counts_the_source_excludes_beyond_agentsyncs_own(env: dict[str, str]) -> None:
    """B13 round 5 F40: setup_report's Configuration line in the shell report, from sources.toml: each local
    or inbox source's excludes beyond agentsync's own, counted by its place, never a glob's text."""
    config = Path(env["HOME"]) / "agent-context" / "sources.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '[[source]]\nid = "a"\nkind = "local"\npath = "/x"\n'
        'exclude = ["~$*", "Icon\\r", "/Empty Subfolder/", "*.bak"]\n\n'
        '[[source]]\nid = "b"\nkind = "inbox"\npath = "/y"\nexclude = [\n  "*.part",\n]\n\n'
        '[[source]]\nid = "c"\nkind = "graph-drive"\nexclude = ["/z/"]\n',
        encoding="utf-8",
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text(encoding="utf-8")
    assert (
        "- source excludes beyond agentsync's own: (source 1) 2 (1 folder glob(s)); 0 on the other 1 "
        "source(s) (globs not shown; an excluded folder is not mirrored if it later gains files)\n" in text
    ), text
    assert "Empty Subfolder" not in text and "*.bak" not in text
    # a hand-written list over several lines, with a bracket inside a glob
    config.write_text(
        '[[source]]\nid = "a"\nkind = "local"\npath = "/x"\n'
        'exclude = [\n  "[Tt]emp/",\n  "/Empty Subfolder/",\n]\n',
        encoding="utf-8",
    )
    assert install_sh(env, "--report-only").returncode == 0
    assert "- source excludes beyond agentsync's own: (source 1) 2 (1 folder glob(s)) (globs" in report_path(
        env
    ).read_text(encoding="utf-8")
    config.write_text('[[source]]\nid = "a"\nkind = "local"\npath = "/x"\n', encoding="utf-8")
    assert install_sh(env, "--report-only").returncode == 0
    assert "- source excludes beyond agentsync's own: none\n" in report_path(env).read_text(encoding="utf-8")


def test_the_shell_joined_word_list_is_the_reports() -> None:
    """install.sh's joined_word holds setup_report._JOINED_WORDS, word for word (B13 round 4 Q72)."""
    body = INSTALL_SH.read_text().split("\njoined_word() {\n", 1)[1].split("\n}\n", 1)[0]
    [line] = [ln.strip() for ln in body.splitlines() if ln.strip().endswith(") return 0 ;;")]
    assert set(line.removesuffix(") return 0 ;;").split(" | ")) == set(setup_report._JOINED_WORDS)


def test_the_shell_report_leaves_a_report_word_a_short_name_spells_alone(
    env: dict[str, str], folder: Path
) -> None:
    """B13 round 4 Q72: "Ho, Me" run together is "home": the shell report keeps "home" and "HOME" and still
    redacts the name's spaced, joined-with-a-separator and CamelCase forms."""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        '#!/bin/bash\n[ "$1" = -F ] && { echo "Ho, Me"; exit 0; }\nexec /usr/bin/id "$@"\n',
    )
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v4\nF1 | step 3 | clean | 1 | your home folder; HOME set; Ho Me, HoMe, ho_me, Me Ho agreed "
        "| -\n",
        encoding="utf-8",
    )
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(folder))
    assert cp.returncode == 1
    text = report_path(env).read_text(encoding="utf-8")
    assert "your home folder; HOME set; <name>, <name>, <name>, <name> agreed" in text, text


def test_the_shell_report_leaves_any_report_word_a_name_spells_alone(
    env: dict[str, str], folder: Path
) -> None:
    """B13 round 5 F93: the shell's joined-word rule read the 263 hand-picked words only, so "In Stall" turned
    "install" into <name>. It reads report_word: those and every word on install.sh's own lines that are not
    comments (a comment's "janedoe" is no word of the report)."""
    body = INSTALL_SH.read_text(encoding="utf-8")
    assert "janedoe" in body, "the comment this test relies on"
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        '#!/bin/bash\n[ "$1" = -F ] && { echo "In Stall"; exit 0; }\nexec /usr/bin/id "$@"\n',
    )
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v4\nF1 | step 3 | clean | 1 | the install log; INSTALL; In Stall, InStall, in_stall agreed "
        "| -\n",
        encoding="utf-8",
    )
    # Run from under HOME, as a real install is (~/src/...): the script's own path there is shown as ~/...,
    # which no awk can open, and report_word must read the file itself (a reviewer's finding)
    copy = Path(env["HOME"]) / "src" / "agent-context-sync" / "scripts" / "install.sh"
    copy.parent.mkdir(parents=True)
    shutil.copy2(INSTALL_SH, copy)
    shutil.copy2(INSTALL_SH.parents[1] / "pyproject.toml", copy.parents[1] / "pyproject.toml")
    cp = subprocess.run(
        [BASH32, str(copy), "--source-local", str(folder)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**env, "STUB_UV_INSTALL_RC": "2"},
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 1, cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    assert "the install log; INSTALL; <name>, <name>, <name> agreed" in text, text


@pytest.mark.parametrize("link", [True, False], ids=["symlink", "folder"])
def test_the_shell_report_keeps_the_word_a_one_word_organisation_spells(
    env: dict[str, str], link: bool
) -> None:
    """B13 round 5 F42, F55: OneDrive-Link registered "Link", so the shell report's text and quoted lines lost
    the word. Every entry is still registered (a symlink named after the tenant must not print it); a
    one-word organisation that is a report word is replaced only in the entry's name and its domains."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage"
    team = cloud / "OneDrive-Contoso" / "Q3 Launch"
    team.mkdir(parents=True)
    entry = cloud / "OneDrive-Link"
    if link:
        entry.symlink_to(cloud / "OneDrive-Contoso")
    else:
        entry.mkdir()
    (cloud / "OneDrive-SharedLibraries-Link").mkdir()  # one organisation, two entries: each gets its pair
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v4\nF1 | step 3 | clean | 1 | OneDrive-Link and link.sharepoint.com; the Link button; "
        "OneDrive-SharedLibraries-Link | -\n",
        encoding="utf-8",
    )
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(team))
    assert cp.returncode == 1
    text = report_path(env).read_text(encoding="utf-8")
    assert "Contoso" not in text and "OneDrive-Link" not in text and "link.sharepoint.com" not in text
    assert (
        "OneDrive-<org-2> and <org-2>.sharepoint.com; the Link button; OneDrive-SharedLibraries-<org-2>"
        in text
    ), text
    assert "Private only:" not in text, "matched only in its names, it never stands in the report's text"


def test_the_shell_report_sends_a_report_with_a_word_named_folder_privately(env: dict[str, str]) -> None:
    """B13 round 5 F92 in the shell report: a folder named like a word the report prints ("Status") is
    replaced where its placeholder reads back as the word, so the Redaction part says to send it privately."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage"
    plain = cloud / "OneDrive-Contoso" / "Q3 Launch"
    plain.mkdir(parents=True)
    assert install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(plain)).returncode == 1
    assert "Private only:" not in report_path(env).read_text(encoding="utf-8")
    word = cloud / "OneDrive-Contoso" / "Status"
    word.mkdir()
    failed = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(word))
    assert failed.returncode == 1
    text = report_path(env).read_text(encoding="utf-8")
    assert (
        "Private only: one of your folder or organization names is also a word this report prints, so where "
        "its\n" in text
    ), text
    # one spelling, the prompt's, as agentsync's report (B13 round 11 M36)
    assert "- <org-N>: the organization or team" in text and "organisation" not in text, text
    assert "capitalised" not in text
    assert (
        "\nSend it: privately (docs/deploy/setup-feedback.md in the agentsync repository), never as a public "
        "issue. Nothing is sent automatically.\n" in text
    ), "one line, as the agentsync report's (G50)"
    # No line offers the public issue the report says not to use: no issue link in the report or printed, and
    # --report-only's NEXT names bring-back.md alone (B13 round 6 G47, G65)
    assert setup_report.ISSUE_URL not in text and "issue link" not in failed.stdout, failed.stdout
    reported = install_sh(env, "--report-only")
    assert reported.returncode == 0 and "issue link" not in reported.stdout, reported.stdout
    assert last_line(reported).startswith(
        f"NEXT: review {report_path(env).parent}/bring-back.md and copy that one file back privately "
        "(nothing is sent for you) [setup report: "
    ), last_line(reported)
    # A stopped copy's report drops that one line whole, so it says nothing of sending it (G50)
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh(env, "--report-only").returncode == 0
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    _sends_nothing(side)
    stopped = side.read_text(encoding="utf-8")
    assert "Private only:" in stopped and "privately" not in stopped and "public issue" not in stopped


def test_an_agentsync_report_for_a_private_copy_gets_no_issue_link_line(
    env: dict[str, str], tmp_path: Path
) -> None:
    """An agentsync from before B13 round 7 H75 ended the report of a word-named folder with "Private only:"
    and then the issue link its Send it line says not to use; install.sh printed that link and a NEXT offering
    the issue (B13 round 6 G47, G65). The stub writes that older report, so this checks that install.sh reads
    the report's own lines and still prints no issue link and no NEXT offering the issue for a private copy. A
    current agentsync ends a private copy after its Redaction section (H75), which tests/test_setup_report.py
    covers (B13 round 8 J44, J79)."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    old = tmp_path / "report.md"
    for private in (False, True):
        old.write_text(
            "# agentsync setup report\n\n## Agent friction log\n\nPrivate only: quoted from a report\n\n"
            "## Redaction\n\n"
            + "Private only: one of your folder, library or organization names is ...\n" * private
            + f"Send it: ...\n\n{ISSUE_LINK}\n"
        )
        cp = install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only")
        assert cp.returncode == 0 and "(agentsync setup-report)" in cp.stdout, cp.stdout
        assert ("issue link (review the report first)" in cp.stdout) is not private, cp.stdout
        assert ("the issue the link above opens" in last_line(cp)) is not private, last_line(cp)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_the_shell_report_redacts_any_providers_team_and_account(env: dict[str, str], locale: str) -> None:
    """A CloudStorage folder of any provider: the part after the app's name is <org-N> in the shell fallback
    too (a Dropbox team; a Google Drive account's address, which the shell has no email pattern for), and a
    folder that names none (Box-Box) stays as it is (B13 N9)."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage"
    team = cloud / "Dropbox-Wingtip Partners" / "Q3 Launch"
    drive = cloud / "GoogleDrive-jane.doe@contoso.com" / "My Drive"
    for d in (team, drive, cloud / "Box-Box" / "Shared"):
        d.mkdir(parents=True)
    cp = install_sh(
        {**env, "LC_ALL": locale, "STUB_UV_INSTALL_RC": "2"},
        "--source-local",
        str(team),
        "--source-local",
        str(drive),
    )
    assert cp.returncode == 1
    text = report_path(env).read_text()
    for secret in ("Wingtip Partners", "Wingtip\\ Partners", "jane.doe@contoso.com"):
        assert secret not in text, secret
    assert "Dropbox-<org-" in text and "GoogleDrive-<org-" in text and "## Redaction" in text
    # The Redaction paragraph calls <org-N> the organisation, not "the name", which reads as the person's.
    assert "the organization or team after the app's name in a CloudStorage folder" in " ".join(text.split())


def test_the_shell_report_gives_a_repeated_organisation_one_number(env: dict[str, str]) -> None:
    """An organisation in two CloudStorage folders (a OneDrive and its SharePoint libraries, or two apps) took
    two numbers, so every later one was numbered past a gap. It takes one (B13 round 2 R44); so, since the
    shell's match is the Python report's fuzzy one, does another spelling of it (B13 round 10 L66)."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage"
    for name in ("Box-Contoso", "Dropbox-Contoso", "Dropbox-Wingtip Partners", "GoogleDrive-CONTOSO"):
        (cloud / name).mkdir(parents=True)
    log = friction_path(env)
    log.parent.mkdir(parents=True)
    log.write_text("Prompt: v4\nF1 | step 1 | clean | 1 | Contoso, Wingtip Partners and CONTOSO | -\n")
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert "F1 | step 1 | clean | 1 | <org-1>, <org-2> and <org-1> | -" in text, text
    assert "<org-3>" not in text


def test_usage_error_after_parsing_still_writes_the_report(env: dict[str, str], tmp_path: Path) -> None:
    cp = install_sh(env, "--source-local", str(tmp_path / "nowhere"))
    assert cp.returncode == 2
    assert "usage error: --source-local is not an existing folder" in cp.stderr
    assert report_path(env).is_file() and "usage error" in report_path(env).read_text()
    assert last_line(cp).startswith("NEXT: fix the usage error above")
    assert [c for c in calls(env) if not c.startswith(("launchctl print", "launchctl getenv"))] == [], (
        "no step ran (the report's reads of launchd only)"
    )
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
    beta, gamma, archive = org / "Client Beta", org / "Gamma Plans", org / "Gamma Plans Archive"
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'[[source]]\nid = "client-beta"\nkind = "local"\npath = "{beta}"\n', encoding="utf-8")
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)

    def esc(p: Path) -> str:
        return str(p).replace(" ", "\\ ")

    head = "run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- source=- "
    (
        setup / "install.log"
    ).write_bytes(  # a byte that is not UTF-8 inside an argument (printf %q writes them)
        f"2026-10-09T01:00:00Z {head}args=--source-local /tmp/Cafe\xcc\n".encode("latin-1")
        + f"2026-10-10T01:00:00Z {head}args=--source-local {esc(gamma)}".encode()
        + f" --source-local {esc(archive)}\n".encode()
    )
    (setup / "friction.md").write_text(
        "Prompt: v10\nF1 | step 1 | clean | 1 | Client Beta, Gamma Plans and Gamma Plans Archive | -\n"
    )
    # Under a UTF-8 locale BSD sed and sort stop at that byte; longest first, "Archive" goes with its folder.
    cp = install_sh({**env, "LC_ALL": "en_US.UTF-8"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert "(shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_bytes().decode("utf-8", "replace")  # the log tail keeps the stray byte
    for name in ("Client Beta", "Gamma Plans", "Gamma\\ Plans", "Archive", "Contoso"):
        assert name not in text, name
    # Past the stray byte too, and the issue link still follows (BSD sed in its two readers; B13 H15).
    assert _headings(text) == list(setup_report.SECTION_TITLES), "past the stray byte (BSD sed)"
    link = text.rstrip("\n").splitlines()[-1]
    assert link.startswith(f"{setup_report.ISSUE_URL}&title=")
    assert cp.stdout.splitlines()[-2] == f"issue link (review the report first): {link}"


def test_the_shell_report_keeps_every_pair_past_a_non_ascii_folder(env: dict[str, str], folder: Path) -> None:
    """A folder named with an en dash gives a %q pair that is not UTF-8; under a UTF-8 locale every pair
    sorted after it was lost, the home path and the other folders with it (review 2026-10-10)."""
    home = Path(env["HOME"])
    org = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    dash, beta = org / "Contoso \u2013 Docs", org / "Client Beta"
    for f in (dash, beta):
        f.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f'[[source]]\nid = "docs"\nkind = "local"\npath = "{dash}"\n'
        f'[[source]]\nid = "beta"\nkind = "local"\npath = "{beta}"\n',
        encoding="utf-8",
    )
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        f"Prompt: v10\nF1 | step 1 | clean | 1 | Client Beta under {home} | -\n", encoding="utf-8"
    )
    cp = install_sh({**env, "LC_ALL": "en_US.UTF-8"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    assert "<folder-" in text.split("\n## Agent friction log\n", 1)[1]
    for value in ("Client Beta", str(home), "Contoso"):
        assert value not in text, value


def _stub_id(env: dict[str, str], full: str, login: str | None = None) -> None:
    """``id -F`` (and ``id -un`` when given) answered by a stub on PATH."""
    un = f'[ "$1" = -un ] && {{ echo {shlex.quote(login)}; exit 0; }}\n' if login else ""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / "id",
        f'#!/bin/bash\n[ "$1" = -F ] && {{ echo {shlex.quote(full)}; exit 0; }}\n{un}exec /usr/bin/id "$@"\n',
    )


def test_the_shell_report_does_not_glob_a_name_part(env: dict[str, str], tmp_path: Path) -> None:
    """A part of the full name with ``*`` was expanded against the working directory, so the part itself was
    never redacted (review 2026-10-10, B13 N12)."""
    _stub_id(env, "Doe, Jane*")
    work = tmp_path / "work"
    work.mkdir()
    (work / "Janet-notes.txt").write_text("")
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text("Prompt: v10\nF1 | step 1 | clean | 1 | Jane* approved | -\n")
    cp = subprocess.run(  # install_sh() takes no working directory
        [BASH32, str(INSTALL_SH), "--report-only"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
        cwd=work,
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 0, cp.stderr
    assert "| <name> approved |" in report_path(env).read_text()


def test_the_shell_folder_list_is_the_python_reports_generic_set() -> None:
    body = INSTALL_SH.read_text().split("\ngeneric_folder() {\n", 1)[1].split("\n}\n", 1)[0]
    rows = re.findall(r"^\t(.*)\) return 0 ;;$", body, re.M)
    names = {n.strip().strip('"') for row in rows for n in row.split("|")}
    assert names == set(setup_report._GENERIC_FOLDERS)


def test_the_shell_report_redacts_a_generic_configured_folder_as_a_whole_word(env: dict[str, str]) -> None:
    """A configured ``Documents`` is redacted, as in the Python report, but no longer inside other words
    (B13 N12)."""
    home = Path(env["HOME"])
    plans = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Documents" / "Fabrikam Plans"
    plans.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[source]]\nid = "plans"\nkind = "local"\npath = "{plans}"\n')
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v10\nF1 | step 1 | clean | 1 | MyDocuments, DocumentsArchive: Documents | -\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert "| MyDocuments, DocumentsArchive: <folder-1> |" in report_path(env).read_text()


def test_the_shell_report_never_redacts_inside_a_placeholder(env: dict[str, str]) -> None:
    """A folder named ``name`` and a login ``org`` matched inside ``<name>`` and ``<org-1>``, the ones an
    earlier pair wrote, and inside the Redaction note's own words; a folder ``host`` inside a ``<mdmhost>``
    copied from a Python report (review 2026-10-10, B13 N12)."""
    _stub_id(env, "Jane Doe", login="org")
    home = Path(env["HOME"])
    org = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    for name in ("name", "host"):
        (org / name).mkdir(parents=True)
    cfg.write_text(
        "".join(f'[[source]]\nid = "{n}"\nkind = "local"\npath = "{org / n}"\n' for n in ("name", "host"))
    )
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v10\nF1 | step 1 | clean | 1 | Jane Doe under OneDrive-Contoso | -\n"
        "F2 | step 1 | clean | 1 | copied <name> and <mdmhost> | -\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert "| <name> under OneDrive-<org-1> |" in text
    assert "| copied <name> and <mdmhost> |" in text
    assert "<<" not in text
    note = text.split("\n## Redaction\n", 1)[1]
    # the legend lists the kinds the body shows: no <user>, the login "org" printed nowhere (B13 round 10 L1)
    assert "<user>" not in _legend_kinds(text)
    assert (
        "\n- ~: the home path\n- <name>: the full name and its joined forms\n- <org-N>: the organization"
        in note
    )
    assert "  Dropbox-<org-N>), and an account's email address\n- <mdmhost>: the MDM server's host\n" in note


def test_the_shell_report_reads_tilde_and_relative_folders_from_install_log(env: dict[str, str]) -> None:
    """A quoted ``~/...`` folder (install.sh expands it) and a relative one were not read from install.log,
    so ``--report-only`` showed their names; a relative one that names no CloudStorage entry is ``<path>``,
    and so is one starting ``..``, which was placed in CloudStorage and its last word redacted everywhere
    (B13 N12)."""
    home = Path(env["HOME"])
    org = home / "Library" / "CloudStorage" / "OneDrive-Contoso"
    for name in ("Fabrikam Plans", "Northwind Q1"):
        (org / name).mkdir(parents=True)
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)
    head = "run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- source=- args=--source-local "
    typed = ("~/Library/CloudStorage/OneDrive-Contoso/Fabrikam Plans", "OneDrive-Contoso/Northwind Q1")
    typed += ("Tailspin Notes", "./Tailspin Notes", "../Notes", "./../docs")
    words = subprocess.run(  # as install.sh's log_start writes them: bash 3.2 leaves a leading ~ bare
        [BASH32, "-c", 'printf "%q\\n" "$@"', "_", *typed], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert words[0].startswith("~/") and words[3] == "./Tailspin\\ Notes", words
    (setup / "install.log").write_text(
        "".join(f"2026-10-10T0{i}:00:00Z {head}{w}\n" for i, w in enumerate(words, 1))
    )
    (setup / "friction.md").write_text(
        "Prompt: v10\nF1 | step 1 | clean | 1 | Fabrikam Plans, Northwind Q1 | -\n"
        "F2 | step 1 | clean | 1 | Release Notes in ~/agent-context/docs | -\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert re.search(r"\| <folder-\d>, <folder-\d> \|", text)
    # "$cs/.." always exists: a ".." first part was placed in CloudStorage and "Notes", "docs" became pairs
    assert "| Release Notes in ~/agent-context/docs |" in text
    assert text.count("args=--source-local <path>\n") == 4, "the bare, ./, ../ and ./../ relative words"
    for name in ("Fabrikam", "Northwind", "Tailspin"):
        assert name not in text, name


@pytest.mark.parametrize("folder", ["Status", "log"])
def test_the_shell_report_never_redacts_its_own_headings(env: dict[str, str], folder: str) -> None:
    """A configured folder named like a word of a heading turned ``## Status`` into ``## <folder-1>`` and
    ``## Agent friction log`` into ``## Agent friction <folder-1>``, which a later --report-only run looks
    for (B13 N12). The Python report never redacts its headings."""
    home = Path(env["HOME"])
    path = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / folder
    path.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[source]]\nid = "s"\nkind = "local"\npath = "{path}"\n')
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert _headings(report_path(env).read_text()) == list(setup_report.SECTION_TITLES)


@pytest.mark.parametrize(
    ("name", "locale"),
    [
        ("Fabrikam\u2019s Q1", "en_US.UTF-8"),  # a curly apostrophe
        ("Fabrikam \u2013 Q1", "en_US.UTF-8"),  # an en dash
        ("Fabrikame\u0301 Q1", "en_US.UTF-8"),  # an NFD accent (NFC is no $'...' word under UTF-8)
        ("Fabrikam\u00e9 Q1", "C"),  # under C every non-ASCII name is one, every byte octal
        ("Fabrikam's \u2013 Q1", "en_US.UTF-8"),  # an ASCII apostrophe too: written escaped inside the word
    ],
)
def test_the_shell_report_reads_past_a_dollar_quoted_install_log_word(
    env: dict[str, str], name: str, locale: str
) -> None:
    """bash 3.2's ``printf %q`` writes a folder name it finds unprintable as ``$'...'``: under UTF-8 with its
    first byte raw, so the shell report stopped at that install.log line (BSD sed); under C as octal, which
    no pair matched, so the Installer section showed the name escaped. And under ``--report-only`` the
    friction log named the folder in clear: the word was never unquoted into the folders to redact
    (B13 H15)."""
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / name
    folder.mkdir(parents=True)
    loc = {**env, "LC_ALL": locale}
    word = subprocess.run(
        [BASH32, "-c", 'printf %q "$1"', "_", str(folder)], capture_output=True, env=loc, check=True
    ).stdout
    assert word.startswith(b"$'"), word  # the field's form, as install.sh's log_start writes it
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)
    head = b"run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- source=- "
    (setup / "install.log").write_bytes(
        b"2026-10-10T01:00:00Z " + head + b"args=--source-local " + word + b" --confirm-install-agent\n"
        b"2026-10-10T01:00:01Z run=20261010T010000Z-1 step=agentsync seconds=1 rc=2 result=failed\n"
    )
    (setup / "friction.md").write_text(
        f"Prompt: v10\nF1 | step 1 | clean | 1 | listed {name} | -\n", encoding="utf-8"
    )
    cp = install_sh(loc, "--report-only")
    assert cp.returncode == 0, cp.stderr
    raw = report_path(env).read_bytes()
    text = raw.decode("utf-8", "replace")
    assert _headings(text) == list(setup_report.SECTION_TITLES), "every section, past the install.log tail"
    assert "args=--source-local <path> --confirm-install-agent" in text
    assert "listed <folder-1> | -" in text.split("\n## Agent friction log\n", 1)[1]
    installer = raw.split(b"## Installer", 1)[1].split(b"## Configuration", 1)[0]
    assert name.encode() not in raw and b"Fabrikam" not in installer and b"\\" not in installer
    legend = text.split("\n## Redaction\n", 1)[1].split("\n\n")[0].strip().splitlines()
    assert max(map(len, legend)) <= 115 and legend[-1].startswith("Read the report before sending it: a")
    # one placeholder kind a list item, its own lines continued by two spaces (B13 round 9 K3, K15)
    assert legend[0] == "Placeholders used, one kind per item:"
    items = " ".join(legend[1:-1]).replace("   ", " ").split(" - ")
    assert [ln[:2] for ln in legend[1:-1]] == ["- " if ln.startswith("- ") else "  " for ln in legend[1:-1]]
    kinds = _legend_kinds(text)
    assert {"<path>", "<folder-N>"} <= set(kinds) and "<proxy-N>" not in kinds, kinds
    item = {i.split(":", 1)[0].lstrip("- "): " ".join(i.split()) for i in items}
    assert item["<path>"].endswith(
        "and every word bash wrote in its dollar-quote form, which install.log uses for a name with a curly "
        "apostrophe, an en dash or an accent"
    ), "the Redaction text is not itself redacted, and reads plainly (B13 R7)"
    assert (
        "a name every OneDrive or Google Drive has, such as Documents, only where it stands"
        in item["<folder-N>"]
    )


_LEGEND_KINDS = (
    "~",
    "<tmp>",
    "<user>",
    "<name>",
    "<org-N>",
    "<mdmhost>",
    "<proxy-N>",
    "<folder-N>",
    "<source-N>",
)
_LEGEND_KINDS += ("<path>",)


def _legend_kinds(text: str) -> list[str]:
    """The kinds the shell report's legend lists, checked against its body (B13 round 10 L1): each kind the
    body shows has its item, and no item is of a kind the body never shows (~ always: the body writes ~/
    paths of its own)."""
    body, redaction = text.split("\n## Redaction\n", 1)
    legend = redaction.split("\n\n")[0].strip().splitlines()
    kinds = [ln[2:].split(":", 1)[0] for ln in legend[1:-1] if ln.startswith("- ")]
    for kind in _LEGEND_KINDS:
        stem = kind.removesuffix("N>")
        shown = kind == "~" or (re.search(re.escape(stem) + r"\d", body) if stem != kind else kind in body)
        assert (kind in kinds) == bool(shown), (kind, kinds)
    return kinds


def test_the_shell_legend_lists_only_the_kinds_its_report_shows(env: dict[str, str]) -> None:
    """B13 round 10 L1: the legend listed all ten placeholder kinds, seven of which the plain report never
    showed, under a heading that read as a description of the shell's redaction. Like the Python report's
    "Placeholders used:" line it lists the kinds its body shows, read from the redacted body itself, since
    some placeholders (<path>, <tmp>) are written by no pair."""
    home = Path(env["HOME"])
    folder = home / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Project Alpha"
    folder.mkdir(parents=True)
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\nAgent: x\n"
        "2026-10-10T00:00:01Z | step 1 | deviation | picked OneDrive-Contoso/Project Alpha | -\n"
    )
    (friction_path(env).parent / "install.log").write_text(
        "2026-10-10T00:00:02Z run=20261010T000002Z-1 start install.sh compat=15 commit=- kind=- source=- "
        f"args=--source-local {shlex.quote(str(folder))}\n"
    )
    assert install_sh({k: v for k, v in env.items() if k != "TMPDIR"}, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "<folder-1>" in text and "<org-1>" in text
    kinds = _legend_kinds(text)
    assert {"~", "<org-N>", "<folder-N>"} <= set(kinds), kinds
    # <user> and <tmp> only where the test's own stub paths show them
    assert not {"<name>", "<mdmhost>", "<proxy-N>", "<source-N>", "<path>"} & set(kinds), kinds
    assert "Shell fallback redaction, one placeholder per kind:" not in text


def test_both_legends_say_a_proxy_number_can_be_missing(env: dict[str, str]) -> None:
    """B13 round 10 L3: the two reports share proxy numbers (B13 round 8 J78), and each shows only some of
    them: the shell names an environment proxy by its variable only, the Python report only the proxy it
    resolves. So the shell report's proxies started at <proxy-3>. The numbers stay shared; both legends say
    a number can be missing."""
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text(
        "[http]\n\tproxy = http://gitproxy.contoso.example:8080\n"
        '[url "https://mirror.contoso.example/contoso-team/"]\n\tinsteadOf = https://github.com/\n'
    )
    proxied = {
        **env,
        "HTTPS_PROXY": "http://egress.contoso.example:8080",
        "all_proxy": "http://allproxy.contoso.example:3128",
    }
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(  # an agent quotes the git proxy and the rewrite
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\nAgent: x\n2026-10-10T00:00:01Z | step 1 | deviation | "
        "saw gitproxy.contoso.example:8080 and https://mirror.contoso.example/contoso-team/x | -\n"
    )
    assert install_sh(proxied, "--report-only").returncode == 0
    text = report_path(env).read_text()
    body = text.split("\n## Redaction\n", 1)[0]
    shown = sorted({int(n) for n in re.findall(r"<proxy-(\d+)>", body)})
    assert shown and shown[0] == 3, shown  # 1 and 2 are the environment's, named by variable only
    assert "<proxy-N>" in _legend_kinds(text)
    legend = " ".join(text.split("\n## Redaction\n", 1)[1].split("\n\n")[0].split())
    assert (
        "and the system PAC URL after its scheme; numbered as in agentsync's own report, so a number can be "
        "missing here (an environment proxy this report names only by its variable keeps its number)"
    ) in legend
    assert setup_report._LEGEND["proxy"].endswith(
        "numbered as in the shell fallback report, so a number can be missing"
    )


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
    # B13 round 8 J65: a person's own kept commit brings its From: line, which the header now names
    assert " ".join(text.split("\n## 1. Setup report", 1)[0].split()).endswith(
        "review them before sending. Section 3 holds one patch per kept commit, and each starts with its "
        "author's name and email and the commit's date and message; a commit of your own carries your git "
        "name and email (or, if git never had them set, a name and address git filled in from this Mac's "
        "account), so check those lines too."
    )
    assert "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~" in text.split("## 3. Local work", 1)[1]
    assert f"bring back: {back} (one file: report, fix request, local work)" in cp.stdout

    (setup / "fix-request.md").unlink()
    (setup / "local-work" / "0001-local.patch").unlink()
    install_sh(env, "--report-only")
    text = back.read_text(encoding="utf-8")
    assert text.split("## 2. Fix request", 1)[1].split("\n\n", 2)[1] == "none"
    assert text.rstrip().endswith("none")
    head = text.split("\n## 1. Setup report", 1)[0]
    assert "author's name" not in head and "Sections 2 and 3" not in head, "B13 round 10 L4"


@pytest.mark.parametrize(
    ("fix", "patch", "review"),
    [
        (False, None, None),
        (True, None, "Section 2 is not: it names real folders and files, so review it before sending."),
        (False, "new", "Section 3 is not: it names real folders and files, so review it before sending."),
        (
            True,
            "new",
            "Sections 2 and 3 are not: they name real folders and files, so review them before sending.",
        ),
        (False, "old", None),  # a patch not repeated: one line, no author line, nothing named
    ],
)
def test_the_bring_back_header_names_only_what_the_file_holds(
    env: dict[str, str], fix: bool, patch: str | None, review: str | None
) -> None:
    """B13 round 10 L4: the header said sections 2 and 3 name real folders and files, and that section 3's
    patches start with author lines, when both sections read "none" or section 3 held only a "not repeated"
    line. The review sentence names only the sections that hold something, and the author sentence comes
    only with a patch embedded."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    if fix:
        (setup / "fix-request.md").write_text("- OCR scanned PDFs\n", encoding="utf-8")
    if patch == "old":
        _two_attempts(env)
        _keep_patch(env, "2026-10-06T16:40:00Z")
    elif patch == "new":
        _keep_patch(env, "2026-10-07T16:00:00Z")
    assert install_sh(env, "--report-only").returncode == 0
    text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    head = " ".join(text.split("\n## 1. Setup report", 1)[0].split())
    assert head.split(
        " Private: copy this file back as it is, and never paste it into the public issue form. "
    )[1].startswith("Section 1 is redacted." + (f" {review}" if review else ""))
    for sentence in (
        "Sections 2 and 3 are not",
        "Section 2 is not",
        "Section 3 is not",
    ):
        assert (sentence in head) == (review is not None and review.startswith(sentence)), sentence
    assert ("Section 3 holds one patch per kept commit" in head) == (patch == "new")
    assert ("not repeated: a patch file" in text) == (patch == "old")


def test_the_bring_back_file_fences_each_file_it_embeds(env: dict[str, str]) -> None:
    """B13 round 5 F14: the embedded report's H1 and H2s and the fix request's H1 outranked or matched the
    file's own numbered sections. Each embedded file sits in a tilde fence longer than any tilde run that
    starts one of its lines, so outside the fences the only headings are the title and the three sections,
    and the files come back byte for byte between them (section 2's triage diff still works)."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    report = "# agentsync setup report\\n\\n## Summary\\n\\n~~~text\\n# a log line\\n~~~\\n"
    stub = STUB_AGENTSYNC.replace(
        "printf '# agentsync setup report\\n\\nstub, simulate=%s\\n' \"${AGENTSYNC_SIMULATE_LAUNCHD:-0}\"",
        f"printf '{report}'",
    )
    assert stub != STUB_AGENTSYNC
    _write_exe(home / ".local" / "bin" / "agentsync", stub)
    setup = home / "agent-context" / "setup"
    (setup / "local-work").mkdir(parents=True)
    fix = "# Fix request: Contoso folders\n\n## Not used\n\n~~~~~~~~~~~~~~\n## quoted\n~~~~~~~~~~~~~~\nlast"
    (setup / "fix-request.md").write_text(fix, encoding="utf-8")
    (setup / "local-work" / "0001-local.patch").write_text(" ~~~~~~~~~~~~\n+x\n", encoding="utf-8")
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    outside: list[str] = []
    fence = ""
    for ln in text.splitlines():
        if not fence and re.match(r"~{3,}", ln):
            fence = re.match(r"~+", ln).group(0)  # type: ignore[union-attr]
        elif fence and ln == fence:
            fence = ""
        elif not fence:
            outside.append(ln)
    assert not fence, "every fence is closed"
    assert [ln for ln in outside if re.match(r"#{1,2} ", ln)] == [
        "# agentsync: the one file to bring back",
        "## 1. Setup report",
        "## 2. Fix request (~/agent-context/setup/fix-request.md)",
        "## 3. Local work kept by setup prompt step 1 (~/agent-context/setup/local-work)",
    ]
    body = report_path(env).read_text(encoding="utf-8")
    assert f"## 1. Setup report\n\n~~~~~~~~~~markdown\n{body}~~~~~~~~~~\n" in text
    assert f"\n\n~~~~~~~~~~~~~~~markdown\n{fix}\n~~~~~~~~~~~~~~~\n" in text, "longer than its 14 tildes"
    assert "~~~~~~~~~~~~~~diff\n ~~~~~~~~~~~~\n+x\n~~~~~~~~~~~~~~\n" in text, "a context line's tildes"


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


def test_bring_back_names_patch_authors_only_when_section_3_sends_a_patch(env: dict[str, str]) -> None:
    """B13 round 10 L25: every patch was in an earlier report, so section 3 held only "not repeated:" lines,
    yet the header said "Section 3 holds one patch per kept commit, and each starts with its author's name
    ... so check those lines too", lines that were not there. The header now says what section 3 holds."""
    authors = "Section 3 holds one patch per kept commit, and each starts with its author's name"

    def header() -> str:
        cp = install_sh(env, "--report-only")
        assert cp.returncode == 0, cp.stderr
        text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
        return " ".join(text.split("\n## 1. Setup report", 1)[0].split())

    _two_attempts(env)
    old = _keep_patch(env, "2026-10-06T16:42:39Z", "+old = 1\n", "0001-old.patch")
    head = header()
    assert authors not in head, head
    assert head.endswith(
        "Section 3 sends no patch this time: each was in an earlier report and is listed by its hash."
    ), head
    assert _local_work(env) == _not_repeated(old, "2026-10-06T16:42:39Z")
    _keep_patch(env, "2026-10-07T15:58:27Z", "+new = 2\n", "0002-new.patch")
    assert authors in header()
    for path in (old, old.parent / "0002-new.patch"):
        path.unlink()
    head = header()
    assert authors not in head and "Section 3 sends" not in head, head
    # nothing in sections 2 and 3: no review sentence either (B13 round 10 L4, lane RB)
    assert head.endswith("Section 1 is redacted.") and "Sections 2 and 3" not in head, head
    # a symlinked patch is no patch the report counts, so section 3 does not send it (a reviewer's find)
    target = Path(env["HOME"]) / "elsewhere.patch"
    target.write_text("+linked = 3\n", encoding="utf-8")
    (old.parent / "0003-linked.patch").symlink_to(target)
    assert _local_work(env) == "none\n"
    assert authors not in header()


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


STEP1_STOP = "2026-10-06T17:16:11Z | step 1 | error | xcode-select -p printed no path (exit 2) | -\n"
"""What setup prompt v11 has an agent log at step 1's Xcode stop, 30 minutes after ``ENDED``: a step 1 error
of a session whose step 1 stopped before ``--log-start`` wrote a header (``parse_friction``'s header-less
attempt)."""


def test_bring_back_does_not_repeat_local_work_after_a_step_1_stop_logged_with_no_header(
    env: dict[str, str],
) -> None:
    """B13 H1: since v11 step 1's Xcode and git stops log the failed command as a step 1 error before the
    report. That line, more than NEW_SESSION_GAP after the last end line, is another session, as an
    ``Attempt:`` line would be: the attempt before it reached its report, so section 3 does not send its patch
    again (the shell now reads the attempt boundary the report's Summary reads). No end line is added for a
    header-less attempt, and work the stopping session kept is still sent."""
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: x\n{ENDED} | end | finished\n{STEP1_STOP}",
        encoding="utf-8",
    )
    written = "2026-10-06T16:42:39Z"
    patch = _keep_patch(env, written, "+old = 1\n", "0001-old.patch")
    assert _local_work(env) == _not_repeated(patch, written)
    assert friction_path(env).read_text(encoding="utf-8").endswith(STEP1_STOP), "no end line for it"
    _keep_patch(env, "2026-10-06T17:15:00Z", "+new = 2\n", "0002-new.patch")
    assert _local_work(env) == "~~~~~~~~~~diff\n+new = 2\n~~~~~~~~~~\n" + _not_repeated(patch, written)


@pytest.mark.parametrize(
    ("logged", "sent"), [("2026-10-06T16:56:11Z", True), ("2026-10-06T16:56:12Z", False)]
)
def test_a_step_1_error_within_ten_minutes_of_the_end_line_is_the_same_attempts(
    env: dict[str, str], logged: str, sent: bool
) -> None:
    """The boundary is parse_friction's: a step 1 error at most NEW_SESSION_GAP (600 s) after the end line is
    a late line of that attempt, whose own end line never counts, so its patch is sent as before; one second
    later it is another session, and the patch is not repeated."""
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: x\n{ENDED} | end | finished\n"
        f"{logged} | step 1 | error | git pull --ff-only exited 1 | -\n",
        encoding="utf-8",
    )
    written = "2026-10-06T16:42:39Z"
    patch = _keep_patch(env, written)
    expected = "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n" if sent else _not_repeated(patch, written)
    assert _local_work(env) == expected


@pytest.mark.parametrize("ended", [True, False], ids=["finished", "open"])
def test_a_prefixed_step_1_error_after_a_headed_attempt_is_another_sessions(
    env: dict[str, str], ended: bool
) -> None:
    """B13 round 5 F23 (probes P8 and P3b): the "prompt vN, <agent>:" prefix is written only before
    --log-start, which writes the header, so after a header it is always another session's, whatever the gap
    and in the same words. Its attempt is a new one: the finished attempt's end line is then an earlier end,
    and its patch is not sent again; an open attempt has no end line, so nothing is held back."""
    agent = "Claude Code, claude-opus-5-5"
    end = f"{ENDED} | end | finished\n" if ended else ""
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: {agent}\n{end}"
        f"2026-10-06T16:4{'8' if ended else '3'}:11Z | step 1 | error | prompt v{COMPAT}, {agent}: git pull "
        "failed | -\n",
        encoding="utf-8",
    )
    attempts = setup_report.parse_friction(friction_path(env).read_text(encoding="utf-8")).attempts
    assert len(attempts) == 2 and attempts[1].stated == {"Prompt": f"v{COMPAT}", "Agent": agent}
    written = "2026-10-06T16:42:39Z"
    patch = _keep_patch(env, written)
    sent = "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"
    assert _local_work(env) == (_not_repeated(patch, written) if ended else sent)


def _awk_attempts(log: str) -> int:
    """How many attempts install.sh's FRICTION_AWK opens in ``log``: one per ``Attempt:`` line (or one for a
    log that starts without one), one per line its track() calls another session's, and one per second
    ``Prompt:`` line in an attempt (NH)."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [body] = re.findall(r"^FRICTION_AWK='(.*?)'$", script, re.MULTILINE | re.DOTALL)
    [s1] = re.findall(r"^STEP1_ERROR_RE='([^']*)'$", script, re.MULTILINE)
    program = body + "{ n += track($0); if (NH) { n++; NH = 0 } } END { print n }"
    cp = subprocess.run(
        ["/usr/bin/awk", "-v", "gap=600", "-v", f"s1={s1}", program],
        input=log,
        capture_output=True,
        text=True,
        check=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    lines = log.splitlines()
    return (
        int(cp.stdout)
        + sum(ln.startswith("Attempt:") for ln in lines)
        + (not lines[0].startswith("Attempt:"))
    )


def test_the_installer_and_the_report_split_the_same_attempts_after_any_header() -> None:
    """B13 round 5 F23 (a reviewer's finding): parse_friction counts every header line it keeps (Prompt,
    Agent, Outcome, Run), and a Prompt: line with no number; install.sh's awk set its header flag on
    Attempt: and Prompt: only and tested it after the version and agent. Each log gives both the same
    attempts."""
    err = "| step 1 | error | prompt v12, Claude Code: git pull failed | -\n"
    logs = [
        f"2026-10-06T10:00:00Z {err}Agent: Claude Code\n2026-10-06T10:02:00Z {err}",
        f"2026-10-06T10:00:00Z {err}Outcome: stopped\n2026-10-06T10:02:00Z {err}",
        f"2026-10-06T10:00:00Z {err}- Run: 1\n2026-10-06T10:02:00Z {err}",
        f"Attempt: 2026-10-06T10:00:00Z\nPrompt: the one in the README\n2026-10-06T10:01:00Z {err}",
        f"Attempt: 2026-10-06T10:00:00Z\n2026-10-06T10:01:00Z {err}",
        f"Attempt: 2026-10-06T10:00:00Z\nPrompt: v12\nAgent: Claude Code\n2026-10-06T10:00:30Z {err}",
        f"2026-10-06T10:00:00Z {err}2026-10-06T10:00:30Z {err}",
        "Prompt: v4\nAgent: Claude Code\n2026-10-01T10:00:00Z | step 2 | ok | a | -\n"
        "2026-10-01T10:05:00Z | end | finished\nPrompt: v4\nAgent: Claude Code\n"
        "2026-10-02T10:00:00Z | step 2 | ok | b | -\n",
        f"Attempt: 2026-10-06T10:00:00Z\nPrompt: v12\nPrompt: v12\n2026-10-06T10:01:00Z {err}",
    ]
    for log in logs:
        assert _awk_attempts(log) == len(setup_report.parse_friction(log).attempts), log


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
    which the shell report's redaction has never seen. An agent may log that line: agentsync's own globs are
    shown as written and every other entry as <path>, as agentsync setup-report shows it (B13 round 4 Q80),
    and the list is <path> when the line was cut inside it."""
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
        'status said: set exclude = ["~$*", "/<path>/", "/<path>/"] in [[source]] id = "one" in '
        "~/agent-context/sources.toml | -\n" in text
    )
    assert "and then: set exclude = [<path>]\n" in text
    assert "Fabrikam" not in text and "Tailspin" not in text


def _shell_exclude_scrub(lines: list[str]) -> list[str]:
    """install.sh's scrub_exclude_lists, with its SHIPPED_EXCLUDES line, run on ``lines``."""
    text = INSTALL_SH.read_text()
    [const] = re.findall(r"(?m)^SHIPPED_EXCLUDES='.*'$", text)
    [fn] = re.findall(r"(?ms)^scrub_exclude_lists\(\) \{\n.*?^\}$", text)
    cp = subprocess.run(
        [BASH32, "-c", f"{const}\n{fn}\nscrub_exclude_lists"],
        input="".join(f"{ln}\n" for ln in lines).encode(),
        capture_output=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"},
    )
    return cp.stdout.decode().splitlines()


def test_the_shell_exclude_allowlist_is_the_reports() -> None:
    """The shell's twin of setup_report._SHIPPED_EXCLUDES, each entry as an exclude line writes it: a glob
    agentsync adds and the shell lacks would read as <path>, one it drops would still show (B13 round 4
    Q80)."""
    [const] = re.findall(r"(?m)^SHIPPED_EXCLUDES='(.*)'$", INSTALL_SH.read_text())
    assert set(const.split(" ")) == {
        json.dumps(e, ensure_ascii=False) for e in setup_report._SHIPPED_EXCLUDES
    }


def test_the_shell_exclude_scrub_is_the_reports() -> None:
    """The shell report's exclude lines read as the Python report's, line for line (B13 round 4 Q80)."""
    lines = [
        'set exclude = ["~$*", "*.tmp", ".DS_Store", "Icon\\r", "._*"] in [[source]] id = "one"',
        'set exclude = ["~$*", "*.tmp", ".DS_Store", "/Contoso Board/", "Payroll"] in [[source]] id = "one"',
        'status: set exclude = ["/Plans/Tailspin [[]old]/", "*.part"] in [[source]] id = "two" in x.toml',
        'set exclude = ["x\\", \\"*.tmp", "*.tmp"] in [[source]] id = "one"',
        'set exclude = ["Résumés de l\u2019équipe", "*.download"] in [[source]] id = "one"',
        'set exclude = ["/a/", "//", "/"] in [[source]] id = "one"',
        'and then: set exclude = ["~$*", "/Fabrikam Bi',
        'set exclude = [~$*, *.tmp] in [[source]] id = "one"',
        'set exclude = ["*.tmp",  "x"] in [[source]] id = "one"',
        'set exclude = ["*.tmp"] in [[source]] then exclude = ["Payroll"] in [[source]] id = "one"',
        "set exclude = [] in [[source]] and exclude = [ ] in [[source]]",
        'set exclude = ["*.tmp", ] in [[source]] id = "one"',
        "no list here",
    ]
    want = [setup_report._EXCLUDE_LIST_RE.sub(setup_report._exclude_placeholder, ln) for ln in lines]
    assert _shell_exclude_scrub(lines) == want
    assert want[3] == 'set exclude = ["<path>", "*.tmp"] in [[source]] id = "one"', "an escaped quote"
    assert want[0] == lines[0], "only shipped globs: shown as written"


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
    assert friction.strip() == "~~~text\nOutcome: failed at step 2\n~~~", "fenced (B13 round 6 G6)"
    assert install_sh(env, "--report-only").returncode == 0
    assert report_path(env).read_text().count("~~~text\nOutcome: failed at step 2\n~~~\n") == 1, "no new pair"


def test_the_shell_report_fences_the_friction_log_once(env: dict[str, str]) -> None:
    """The Python report fences the friction log; the shell fallback printed it raw, so its lines ran into one
    paragraph and a placeholder read as an HTML tag a renderer drops (B13 round 6 G6). Read back from an
    earlier report of either writer, the old fence is left out, so the new one neither nests (which would
    leave every later section inside a code block) nor grows a pair a run; a fence in the log is broken up."""
    log = friction_path(env)
    log.parent.mkdir(parents=True)
    log.write_text("Prompt: v10\nF1 | step 1 | clean | 1 | listed a folder | -\n~~~\n```\n")
    assert install_sh(env, "--report-only").returncode == 0
    friction = report_path(env).read_text().split("\n## Agent friction log\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert (
        friction == "~~~text\nPrompt: v10\nF1 | step 1 | clean | 1 | listed a folder | -\n~ ~ ~\n` ` `\n~~~"
    )
    log.unlink()
    report_path(env).write_text(
        "# agentsync setup report\n\n## Agent friction log\n\nThe agent's own log.\n\n~~~text\n"
        "  1  Prompt: v10\n  2  F1 | step 1 | clean | 1 | listed a folder | -\n~~~\n\n## Environment\n\n- x\n"
    )
    for _ in range(2):
        assert install_sh(env, "--report-only").returncode == 0
        text = report_path(env).read_text()
        part = text.split("\n## Agent friction log\n", 1)[1].split("\n## Environment\n", 1)[0]
        assert part.count("~~~text") == 1 and part.count("\n~~~\n") == 1, part
        assert text.count("~~~text") == text.count("\n~~~\n"), "## Environment sits outside every fence"
    # A folder named "text" is redacted in the opener too; the read-back still drops it
    word = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "text"
    word.mkdir(parents=True)
    config = report_path(env).parent / "sources.toml"
    config.write_text(f'[[source]]\nid = "text"\nkind = "local"\npath = "{word}"\n')
    for _ in range(2):
        assert install_sh(env, "--report-only").returncode == 0
        part = report_path(env).read_text().split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
        assert part.count("\n~~~") == 2 and "~ ~ ~" not in part, part
    # B13 round 7 H41: so is a source id "text", which is no folder's name: the opener read "~~~<source-1>",
    # the read-back kept it, and the section grew by a line a run. The opener is now passed through as
    # written, and the read-back drops every fence line, one an earlier report holds in that form too.
    notes = Path(env["HOME"]) / "Documents" / "notes"
    notes.mkdir(parents=True)
    (word.parent / "tex").mkdir()
    config.write_text(
        f'[[source]]\nid = "text"\nkind = "local"\npath = "{notes}"\n\n'
        f'[[source]]\nid = "tex"\nkind = "local"\npath = "{word.parent / "tex"}"\n'
    )
    text = report_path(env).read_text()
    report_path(env).write_text(text.replace("\n~~~text\n", "\n~~~<source-1>\n", 1))
    for _ in range(3):
        assert install_sh(env, "--report-only").returncode == 0
        part = report_path(env).read_text().split("\n## Agent friction log\n", 1)[1].split("\n## ", 1)[0]
        assert part.count("\n~~~") == 2 and "~ ~ ~" not in part, part
        assert "\n~~~text\n" in part and "<source-" not in part, part


def test_the_shell_report_says_how_to_send_it_before_its_link(env: dict[str, str]) -> None:
    """B13 round 7 H4: the shell report that may be sent ended on a bare link: no private route, no "Nothing
    is sent automatically" and no word that the form opens empty, all of which the agentsync report says.
    Its Redaction part now ends on the same one-line "Send it: " advice, in that report's words for a link
    that fills no field, with the legend's last sentence still closing its own paragraph."""
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    # no friction log: the link fills the run type alone (B13 round 10 L2), in the Python report's words
    assert setup_report.issue_link_fields(text.splitlines()[-1]) == ["Run type"]
    opens = 'a new "Setup report" issue with the Run type field filled in (nothing else)'
    assert text.splitlines()[-5:] == [
        "Read the report before sending it: anything else confidential is yours to remove.",
        "",
        f"Send it: the link on the last line opens {opens}; paste this report into its Setup report field, "
        "or send it privately (docs/deploy/setup-feedback.md in the agentsync repository). Nothing is sent "
        "automatically.",
        "",
        _uncomputed_link(env, text),
    ]


NOT_COMPUTED = (
    "- outcome: not computed (this shell fallback computes none for this attempt; the Outcome is yours to "
    "choose from the friction log's step lines; the link fills Run type, and Prompt version and Agent where "
    "the latest attempt states them)"
)
"""The shell report's Summary line where it computes no outcome (B13 round 7 H24; round 9 K8 names where each
field comes from; round 10 L2: the link fills what the shell can read)."""


def _summary(report: str) -> list[str]:
    return report.split("\n## Summary\n\n", 1)[1].split("\n\n## ", 1)[0].splitlines()


def test_the_shell_report_states_a_step_1_failure_and_fills_its_link(env: dict[str, str]) -> None:
    """B13 round 7 H24: a session whose step 1 failed before agentsync was installed has a shell report whose
    one friction line states the prompt version and the agent, yet its Summary had no outcome, prompt or
    agent and its link was bare, while docs/deploy/setup-feedback.md says to copy the values from the
    Summary. In that one case (the latest attempt's only event is a step 1 error that states both, and the
    attempt has no install run) the Summary has the line the agentsync report computes, and the link is the
    one ``build_issue_url`` writes for the same values. The agent is taken from the redacted line, so a folder
    it names is that folder's placeholder in the link too. Since B13 round 8 J22 the line and the link carry
    the run type the agentsync report computes: this test's HOME is under a temporary folder, a Sandbox."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    (cloud / "Fabrikam Bridge").mkdir(parents=True)
    _write_config(env, local_source_table("fabrikam-bridge", cloud / "Fabrikam Bridge"))
    said = "prompt v15, Northwind Agent 2.1 (Fabrikam Bridge): git pull failed: could not resolve host"
    assert install_sh(env, "--log", "1", "error", said, "check the network").returncode == 0
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert _summary(text)[2] == (
        "- outcome: failed at step 1 (computed: no install.sh run; step 1 logged an error) · prompt: v15 "
        "(from the step 1 error line) · run: Sandbox (computed: HOME is under a temporary folder) · agent: "
        "Northwind Agent 2.1 (<folder-1>)"
    )
    # what the agentsync report computes from the same friction log, and the link it builds
    attempt = setup_report.parse_friction(friction_path(env).read_text()).latest
    outcome = setup_report.compute_outcome(attempt, [])
    assert (outcome.text, outcome.why) == ("failed at step 1", ("no install.sh run; step 1 logged an error",))
    assert setup_report._prompt_version(attempt) == "v15"
    assert attempt is not None and attempt.said("Agent") == "Northwind Agent 2.1 (Fabrikam Bridge)"
    link = setup_report.build_issue_url(
        outcome=outcome.form_label,
        title_outcome=outcome.title_label,
        run_type=setup_report.ISSUE_RUN_TYPES[setup_report.compute_run_type([], env["HOME"])],
        prompt="v15",
        agent="Northwind Agent 2.1 (<folder-1>)",
    )
    lines = text.splitlines()
    assert lines[-1] == link and setup_report.issue_link(text) == link
    assert setup_report.issue_link_fields(link) == [
        "Outcome",
        "Run type",
        "Prompt version",
        "Agent tool and model",
    ]
    assert lines[-3].startswith(
        'Send it: the link on the last line opens a new "Setup report" issue with the Outcome, Run type, '
        "Prompt version, and Agent tool and model fields filled in (nothing else); paste this report into "
    )
    assert "Fabrikam" not in text and f"issue link (review the report first): {link}" in cp.stdout


def test_the_shell_temporary_roots_are_the_reports() -> None:
    """The shell's twin of paths.TEMPORARY_ROOTS, by which both reports call a run a Sandbox (B13 round 8
    J22)."""
    [roots] = re.findall(r'(?m)^TEMPORARY_ROOTS="(.*)"$', INSTALL_SH.read_text())
    assert tuple(roots.split(" ")) == paths.TEMPORARY_ROOTS


@pytest.mark.parametrize(
    ("home", "kind"),
    [
        ("/tmp", "sandbox"),
        ("/private/var/folders/xy/T/contoso/home", "sandbox"),
        ("/tmpcontoso/home", "real mac"),
        ("/Users/contoso-no-such-home", "real mac"),
    ],
)
def test_the_shell_run_type_is_the_reports(home: str, kind: str) -> None:
    """home_run_kind gives what setup_report.compute_run_type gives for an attempt with no install run: a root
    itself or a path below one, never a name that only starts like one."""
    fn = _shell_function("home_run_kind")
    [roots] = re.findall(r'(?m)^TEMPORARY_ROOTS=".*"$', INSTALL_SH.read_text())
    cp = subprocess.run(
        [BASH32, "-c", f"{roots}\n{fn}\nhome_run_kind"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": home, "LC_ALL": "C"},
    )
    assert cp.stdout == kind
    assert setup_report.compute_run_type([], home) == kind.split(" ", maxsplit=1)[0]


def test_the_shell_run_type_reads_the_folder_home_resolves_to(tmp_path: Path) -> None:
    """A HOME reached by a link is judged by the folder it resolves to as well, as is_temporary does: the
    roots are this test's own folder here, which the link's path does not start with."""
    (tmp_path / "root" / "home").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "root", target_is_directory=True)
    fn = _shell_function("home_run_kind")
    cp = subprocess.run(
        [BASH32, "-c", f'TEMPORARY_ROOTS="{tmp_path}/root"\n{fn}\nhome_run_kind'],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "alias" / "home"), "LC_ALL": "C"},
    )
    assert cp.stdout == "sandbox"


@pytest.mark.parametrize(
    "agent",
    [
        unicodedata.normalize("NFC", "Contoso Agént 2"),
        unicodedata.normalize("NFD", "Contoso Agént 2"),
        "Contoso\udcffBot 中 x",  # one byte that is not UTF-8 (written as it is)
        "Contoso\tAgent  — Code",
        "Contoso\u00a0Agent\u2009Code",  # spaces Python splits on
        "Contoso Agént 中\udce4\udcb8",  # a last character cut short
    ],
)
def test_the_shell_reports_link_keeps_an_agents_letters_outside_ascii(
    env: dict[str, str], agent: str
) -> None:
    """B13 round 8 J77: the shell link's agent filter kept ASCII only, so "Cláude" read "Clude", while
    ``_issue_link`` keeps every letter and digit. Both now give the same field, for a name with an accent
    written either way, a byte that is not UTF-8 (which the agentsync report reads as U+FFFD, no letter) and a
    run of blanks."""
    said = f"prompt v15, {agent}: git pull failed"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    link = report_path(env).read_bytes().decode(errors="replace").splitlines()[-1]
    got = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)["agent"]
    read = agent.encode(errors="surrogateescape").decode(errors="replace")  # as the friction log is read
    assert got == [re.sub(r"[^\w .:/()+,@<>-]", "", " ".join(read.split()))[:100].strip()]
    assert re.sub(r"[^\w .:/()+,@<>-]", "", "x") == "x" and "[^\\w .:/()+,@<>-]" in Path(
        setup_report.__file__
    ).read_text(encoding="utf-8"), "the filter _issue_link applies"


@pytest.mark.parametrize("ahead", [1, 0, -1])
def test_the_shell_report_says_a_stated_prompt_is_newer_than_this_installer(
    env: dict[str, str], ahead: int
) -> None:
    """B13 round 8 J21: a step 1 error line that states a prompt newer than this installer's got no note in
    the shell report, while the agentsync report adds one (H23). The note sits in the line's own parenthesis
    and names this installer, which writes the report; the link's version, title and agent stay as they were,
    and a version at or below this installer's gets none."""
    said = f"prompt v{COMPAT + ahead}, Contoso Agent: git pull --ff-only failed"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    note = f"; newer than this installer, which is for prompt v{COMPAT}"  # no cause (B13 round 9 K26)
    outcome = next(ln for ln in _summary(text) if ln.startswith("- outcome: "))
    assert outcome.startswith(
        f"- outcome: failed at step 1 (computed: no install.sh run; step 1 logged an error) · prompt: "
        f"v{COMPAT + ahead} (from the step 1 error line{note if ahead > 0 else ''}) · "
    ), outcome
    assert outcome.endswith(" · agent: Contoso Agent")
    link = text.splitlines()[-1]
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert fields["prompt_version"] == [f"v{COMPAT + ahead}"] and fields["agent"] == ["Contoso Agent"]
    assert fields["title"][0].endswith(f" · v{COMPAT + ahead}")


@pytest.mark.parametrize(
    "logged",
    [
        [],  # no friction log
        [("1", "error", "git pull failed: could not resolve host")],  # states no prompt or agent
        [("2", "error", "prompt v15, Contoso Agent: install.sh failed")],  # not step 1
        # B13 round 9 K14: a line of another step follows the stated line (round 10 L26: a step 1 line before
        # it no longer leaves the outcome uncomputed, in either reader:
        # test_a_stopping_sessions_earlier_step_1_lines_are_its_own_in_both_readers)
        [("1", "error", "prompt v15, Contoso Agent: git pull failed"), ("2", "error", "install.sh failed")],
        [("2", "prompt", "the person ran it"), ("1", "error", "prompt v15, Contoso Agent: git pull failed")],
    ],
)
def test_the_shell_report_computes_no_other_outcome(
    env: dict[str, str], logged: list[tuple[str, ...]]
) -> None:
    """H24's rule is narrow on purpose: a wider copy of ``compute_outcome`` in shell would drift from it.
    Every other attempt has a Summary line that says no outcome is computed, and a link with no outcome that
    fills the run type and what the latest attempt states (B13 round 10 L2: it was bare), named on the Send
    it line."""
    for step, kind, what in logged:
        assert install_sh(env, "--log", step, kind, what, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert _summary(text)[2] == NOT_COMPUTED
    link = text.splitlines()[-1]
    assert link == _uncomputed_link(env, text) and "outcome=" not in link
    fields = setup_report.issue_link_fields(link)
    names = " and ".join(fields) if len(fields) < 3 else ", ".join(fields[:-1]) + ", and " + fields[-1]
    assert f"issue with the {names} field" in text.splitlines()[-3]


@pytest.mark.parametrize(
    "later",
    [
        [("1", "error", "git pull failed again")],
        [("1", "prompt", "the person said to go on")],
        [("1", "question", "which folder?")],
        [("1", "deviation", "asked"), ("1", "error", "prompt v15, Contoso Agent: still no network")],
    ],
)
def test_the_shell_report_computes_a_step_1_failure_past_its_first_line(
    env: dict[str, str], later: list[tuple[str, ...]]
) -> None:
    """B13 round 9 K14: the outcome was computed only when the stated step 1 error was the attempt's only
    line, so a second step 1 line left it "not computed" and the link bare, while the agentsync report
    computes "failed at step 1" from the same log. Every line a step 1 line and the first the stated error
    now computes, with the first line's version and agent only, as compute_outcome and Attempt.said read
    them."""
    said = "prompt v15, Contoso Agent: git pull failed: could not resolve host"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    for step, kind, what in later:
        assert install_sh(env, "--log", step, kind, what, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    outcome = _summary(text)[2]
    attempt = setup_report.parse_friction(friction_path(env).read_text()).latest
    assert attempt is not None
    computed = setup_report.compute_outcome(attempt, [])
    assert computed.text == "failed at step 1", "every line the attempt holds"
    assert len(attempt.events) == 1 + len(later)
    assert setup_report._prompt_version(attempt) == "v15" and attempt.said("Agent") == "Contoso Agent"
    assert outcome.startswith(
        "- outcome: failed at step 1 (computed: no install.sh run; step 1 logged an error) · prompt: v15 "
        "(from the step 1 error line) · "
    ), outcome
    assert outcome.endswith(" · agent: Contoso Agent"), outcome
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(text.splitlines()[-1]).query)
    assert fields["prompt_version"] == ["v15"] and fields["agent"] == ["Contoso Agent"]


@pytest.mark.parametrize(
    ("log", "own", "begin"),
    [
        (  # probe a2: a header-less deviation, then the stated error
            "2026-10-14T17:40:00Z | step 1 | deviation | prompt v18, Contoso Agent: asked first | -\n"
            "2026-10-14T17:42:00Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            None,
            "2026-10-14T17:40:00Z",
        ),
        (  # a step 1 deviation past the gap after a finished attempt, then the stated error
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:10:00Z | step 2 | deviation | a | -\n2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:40:00Z | step 1 | deviation | waited | -\n"
            "2026-10-14T17:42:00Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["deviation", "finished"],
            "2026-10-14T17:40:00Z",
        ),
        (  # a step 1 prompt line with no prefix past the gap, then the stated error
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:10:00Z | step 2 | deviation | a | -\n2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:31:00Z | step 1 | prompt | the pull asked to install tools | -\n"
            "2026-10-14T17:32:00Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["deviation", "finished"],
            "2026-10-14T17:31:00Z",
        ),
        (  # a "Step 1" line install.sh's awk does not read as step 1's stays put in both (a reviewer's find)
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:31:00Z | Step 1 | deviation | waited | -\n"
            "2026-10-14T17:42:00Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["finished", "deviation"],
            "2026-10-14T17:42:00Z",
        ),
        (  # a late step 1 line inside the gap stays with the finished attempt; the error still opens its own
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:25:00Z | step 1 | click | late | -\n"
            "2026-10-14T17:42:00Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["finished", "click"],
            "2026-10-14T17:42:00Z",
        ),
    ],
)
def test_a_stopping_sessions_earlier_step_1_lines_are_its_own_in_both_readers(
    env: dict[str, str], log: str, own: list[str] | None, begin: str
) -> None:
    """B13 round 10 L26: a stopping session may log a deviation or a prompt line before its stated step 1
    error. Both readers took the version and agent only from an error that was the attempt's first line, so
    the attempt read "prompt: not stated" (the shell computed no outcome); and the lines before the error,
    logged after the earlier attempt finished, were counted in that finished attempt. The words come from the
    first stated step 1 error at any place in a header-less attempt, and the unbroken run of step 1 lines
    before it, each past the gap after the end line, moves with it."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    (setup / "install.log").write_text(
        "2026-10-14T16:50:00Z run=20261014T165000Z-41 start install.sh compat=18 args=\n"
        "2026-10-14T16:51:00Z run=20261014T165000Z-41 end rc=0 seconds=60\n"
    )
    friction_path(env).write_text(log, encoding="utf-8")
    fr = setup_report.parse_friction(log)
    latest = fr.latest
    assert latest is not None and latest.stated == {"Prompt": "v18", "Agent": "Contoso Agent"}, latest
    assert setup_report.compute_outcome(latest, []).text == "failed at step 1"
    assert latest.begin is not None and latest.begin.strftime("%Y-%m-%dT%H:%M:%SZ") == begin
    assert all(e.step == 1 for e in latest.events)
    if own is not None:  # the finished attempt counts only its own lines
        assert len(fr.attempts) == 2 and [e.kind for e in fr.attempts[0].events] == own, fr.attempts[0]
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    outcome = _summary(text)[2]
    assert outcome.startswith(
        "- outcome: failed at step 1 (computed: no install.sh run; step 1 logged an error) · prompt: v18 "
        "(from the step 1 error line) · "
    ), outcome
    assert outcome.endswith(" · agent: Contoso Agent"), outcome
    [line] = [ln for ln in text.splitlines() if ln.startswith("- install.sh: ")]
    assert f"no install run during this attempt (began {begin})" in line, line


def _run_line(text: str) -> str:
    return next(ln for ln in _summary(text) if ln.startswith("- run: "))


def _uncomputed_link(env: dict[str, str], text: str) -> str:
    """B13 round 10 L2: with no computed outcome the shell report's link fills the run type of its run line,
    and the Prompt version and Agent the friction log's latest attempt states, read as ``_issue_link`` reads
    them (``Attempt.said``), with no outcome; it was bare."""
    path = friction_path(env)
    att = setup_report.parse_friction(path.read_text()).latest if path.is_file() else None
    agent = att.said("Agent") if att is not None else ""
    agent = re.sub(r"[^\w .:/()+,@<>-]", "", " ".join(agent.split()))[:100].strip()  # as _issue_link
    return setup_report.build_issue_url(
        outcome=None,
        run_type=_run_line(text).removeprefix("- run: ").split(" (computed: ", 1)[0],
        prompt=setup_report._prompt_version(att),
        agent=agent or None,
    )


@pytest.mark.parametrize(
    ("friction", "prompt", "agent"),
    [
        (  # two attempts: the latest one's header, not the first's
            "Attempt: 2026-10-10T00:00:00Z\nPrompt: v16\nAgent: Fabrikam Bot\n"
            "2026-10-10T00:00:05Z | step 2 | error | install.sh failed | -\n"
            "2026-10-10T00:01:00Z | end | finished\n"
            "Attempt: 2026-10-10T01:00:00Z\n**Prompt:** setup prompt v18 (README)\n"
            "- Agent: Contoso  Agent (model 2)\n"
            "2026-10-10T01:00:05Z | step 2 | error | install.sh failed | -\n",
            "v18",
            "Contoso Agent (model 2)",
        ),
        (  # a header-less attempt its step 1 error opened after the gap: the words that line states
            "Attempt: 2026-10-10T00:00:00Z\nPrompt: v16\nAgent: Fabrikam Bot\n"
            "2026-10-10T00:01:00Z | end | finished\n"
            "2026-10-10T02:00:00Z | step 1 | error | prompt v17, Northwind Agent: git pull failed | -\n"
            "2026-10-10T02:00:05Z | step 2 | error | install.sh failed | -\n",
            "v17",
            "Northwind Agent",
        ),
        (  # a header-less attempt the gap opened takes its first stated step 1 error's words, not the
            # earlier attempt's header (review of L2; B13 round 10 L26 for the place of that line)
            "Attempt: 2026-10-10T00:00:00Z\nPrompt: v12\nAgent: Fabrikam Bot\n"
            "2026-10-10T00:01:00Z | end | finished\n"
            "2026-10-10T02:00:00Z | step 1 | error | git missing | -\n"
            "2026-10-10T02:00:05Z | step 1 | error | prompt v13, Northwind Agent: stopped | -\n"
            "2026-10-10T02:00:09Z | step 2 | error | install.sh failed | -\n",
            "v13",
            "Northwind Agent",
        ),
        (  # a "## " line inside the log is no heading of the report: the latest attempt still wins
            "Attempt: 2026-10-10T00:00:00Z\nPrompt: v12\nAgent: Fabrikam Bot\n## second try\n"
            "Attempt: 2026-10-10T01:00:00Z\nPrompt: v14\nAgent: Northwind Agent\n"
            "2026-10-10T01:00:05Z | step 2 | error | install.sh failed | -\n",
            "v14",
            "Northwind Agent",
        ),
        (  # an attempt with no Prompt: or Agent: line and no stated words: no field from it
            "Attempt: 2026-10-10T00:00:00Z\nPrompt: v16\nAgent: Fabrikam Bot\n"
            "Attempt: 2026-10-10T01:00:00Z\n2026-10-10T01:00:05Z | step 2 | error | install.sh failed | -\n",
            None,
            None,
        ),
    ],
)
def test_the_shell_reports_link_fills_the_latest_attempts_version_and_agent(
    env: dict[str, str], friction: str, prompt: str | None, agent: str | None
) -> None:
    """B13 round 10 L2: with no computed outcome the shell report's link was bare, though the run type was
    computed and the friction log stated the version and the agent. It fills those of the latest attempt
    only, as _issue_link reads them (Attempt.said: the header, else a header-less attempt's stated words), and
    names them on the Send it line; never an earlier attempt's, a wrong prefill being worse than none."""
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(friction)
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    link = text.splitlines()[-1]
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert fields.get("prompt_version") == ([prompt] if prompt else None)
    assert fields.get("agent") == ([agent] if agent else None)
    assert "outcome" not in fields and fields["run_type"] == ["Sandbox"]
    assert fields["title"] == [f"Setup report: Sandbox{f' · {prompt}' if prompt else ''}"]
    assert link == _uncomputed_link(env, text), "the agentsync report's reading of the same log"
    want = "Run type, Prompt version, and Agent tool and model fields" if prompt else "Run type field"
    assert f'opens a new "Setup report" issue with the {want} filled in (nothing else); ' in text


@pytest.mark.parametrize(
    ("start", "sim", "label", "why"),
    [
        (
            "00:01:00",
            " launchd=simulated",
            "Sandbox with simulated launchd",
            "install.log says launchd=simulated; HOME is also under a temporary folder",
        ),
        ("00:01:00", "", "Sandbox", "HOME is under a temporary folder"),
        (
            "00:00:00",
            " launchd=simulated",
            "Sandbox",
            "HOME is under a temporary folder",
        ),  # an earlier attempt's
    ],
)
def test_the_shell_reports_run_line_is_the_run_type_the_agentsync_report_computes(
    env: dict[str, str], start: str, sim: str, label: str, why: str
) -> None:
    """B13 round 9 K8: an outcome the shell does not compute left the form's fields to the friction log, which
    holds no run type. The Summary now has a run line of its own with the type compute_run_type gives: the
    attempt's last install run saying launchd=simulated makes it Sandbox with simulated launchd. The link
    fills that type (B13 round 10 L2: it stayed bare)."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    (setup / "friction.md").write_text(
        "Attempt: 2026-10-10T00:00:30Z\nPrompt: v15\nAgent: Contoso Agent\n"
        "2026-10-10T00:00:31Z | step 1 | deviation | picked a folder | -\n"
    )
    rid = f"run=20261010T{start.replace(':', '')}Z-1"
    (setup / "install.log").write_text(
        f"2026-10-10T{start}Z {rid} start install.sh compat=15 commit=- kind=checkout source=-{sim} args=\n"
        f"2026-10-10T{start[:-2]}09Z {rid} end rc=1 seconds=9\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert _summary(text)[2] == NOT_COMPUTED
    assert _run_line(text) == f"- run: {label} (computed: {why})"
    fr = setup_report.parse_friction((setup / "friction.md").read_text())
    runs = setup_report.runs_for_attempt(
        fr, len(fr.attempts) - 1, setup_report.read_install_runs(setup / "install.log")
    )
    assert setup_report.ISSUE_RUN_TYPES[setup_report.compute_run_type(runs, env["HOME"])] == label
    assert text.splitlines()[-1] == _uncomputed_link(env, text)
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(text.splitlines()[-1]).query)
    assert fields["run_type"] == [label] and fields["prompt_version"] == ["v15"]


def test_the_shell_reports_newer_prompt_note_names_no_pull(env: dict[str, str]) -> None:
    """B13 round 9 K26: the note blamed step 1's pull, but a step 1 error can come from the Xcode stop, before
    any pull ran; it names no cause, as _newer_than_me_note does."""
    said = f"prompt v{COMPAT + 1}, Contoso Agent: xcode-select -p printed no path"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    outcome = _summary(report_path(env).read_text())[2]
    inside = outcome.split(" (from the step 1 error line", 1)[1].split(") · ", 1)[0]
    assert inside == f"; newer than this installer, which is for prompt v{COMPAT}", outcome
    assert "pull" not in inside


def test_a_stopped_copys_shell_report_computes_no_outcome_and_has_no_link(env: dict[str, str]) -> None:
    """The line --log-start writes for a copy it stopped is a step 1 error too, and the attempt's only event:
    H24's rule leaves it out, and the stopped copy's report still ends with no link."""
    assert install_sh(env, "--log-start", f"prompt v{COMPAT + 1}, Contoso Agent").returncode == 2
    assert install_sh(env, "--report-only").returncode == 0
    text = (friction_path(env).parent / "stopped-copy-report.md").read_text()
    assert NOT_COMPUTED in _summary(text) and setup_report.ISSUE_URL not in text and "Send it: " not in text


def test_the_shell_report_computes_no_outcome_once_the_attempt_ran_install(env: dict[str, str]) -> None:
    """The attempt of H24's case, then an install run that fails before agentsync is installed: the agentsync
    report would judge that run, so the shell report computes nothing, in the run's own report and in a
    --report-only after it."""
    said = "prompt v15, Contoso Agent: git pull failed: could not resolve host"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh({**env, "STUB_UV_INSTALL_RC": "2"}).returncode == 1
    for again in (False, True):
        if again:
            assert install_sh(env, "--report-only").returncode == 0
        text = report_path(env).read_text()
        assert NOT_COMPUTED in _summary(text) and text.splitlines()[-1] == _uncomputed_link(env, text), text


def test_the_shell_report_keeps_an_earlier_friction_log_past_a_stray_byte(env: dict[str, str]) -> None:
    """With no friction.md the shell report takes the friction log from the earlier report. macOS awk's
    regex match exits under UTF-8 at a byte that is not UTF-8 on its own, and every later line was lost
    (B13 H15)."""
    report_path(env).parent.mkdir(parents=True)
    report_path(env).write_bytes(
        b"# agentsync setup report\n\n## Agent friction log\n\n"
        b"F1 | step 1 | clean | 1 | saw Cafe\xcc | -\nF2 | step 2 | clean | 1 | later | -\n"
    )
    cp = install_sh({**env, "LC_ALL": "en_US.UTF-8"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    friction = report_path(env).read_bytes().split(b"\n## Agent friction log\n", 1)[1].split(b"\n## ", 1)[0]
    assert b"| later |" in friction


PIDLESS = "(run ids without their process id):"


@pytest.mark.parametrize(
    ("lines", "head"),
    [
        (15, "All 15 line(s) of ~/agent-context/setup/install.log " + PIDLESS),
        (61, "The last 60 of 61 lines of ~/agent-context/setup/install.log " + PIDLESS),
        (0, "~/agent-context/setup/install.log is empty."),
    ],
)
def test_the_shell_reports_installer_part_counts_the_lines_it_shows(
    env: dict[str, str], lines: int, head: str
) -> None:
    """B13 round 8 J4: the Installer part said "The last 60 lines" above a 15-line install.log, while the
    agentsync report says "the last 2 of 2". Its header now counts what the fence shows: all of a log of 60
    lines or fewer, the last 60 of a longer one, and no empty fence for an empty log. The last line has no
    newline, which tail still prints."""
    install_log_path(env).parent.mkdir(parents=True)
    install_log_path(env).write_text("\n".join(f"note {i}" for i in range(1, lines + 1)))
    assert install_sh(env, "--report-only").returncode == 0
    part = report_path(env).read_text().split("\n## Installer\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert part.splitlines()[0] == head
    shown = part.split("~~~\n", 1)[1].rsplit("\n~~~", 1)[0].splitlines() if lines else []
    assert shown == [f"note {i}" for i in range(max(1, lines - 59), lines + 1)]


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
    assert "- background sync: launchd: simulated (no LaunchAgent of this setup ran)" in _summary(text)
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


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
def test_the_shell_report_says_why_a_copy_has_no_commit(env: dict[str, str]) -> None:
    """B13 round 9 K17, K29: a copy of the installer that is no git checkout gave "source commit -" with no
    reason. The Summary says why; install.log's commit= field, a machine word the install stamp and the
    reports read, stays "-"."""
    src = Path(env["HOME"]) / "src" / "contoso-copy"
    shown = "~/src/contoso-copy"  # the home redacted, as everywhere in the report
    (src / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, src / "scripts" / "install.sh")
    (src / "pyproject.toml").write_text("[project]\nname = 'agentsync'\n")

    def run(*args: str, **extra: str) -> str:
        subprocess.run(
            [BASH32, str(src / "scripts" / "install.sh"), *args],
            capture_output=True,
            text=True,
            check=False,
            env={**env, **extra},
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        [line] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- install.sh: ")]
        return line

    assert run("--report-only").startswith(
        f"- install.sh: setup-prompt-compat {COMPAT}, source commit none ({shown} has no .git: not a git "
        "checkout); this --report-only run only writes the report"
    )
    assert f"source commit none ({shown} has no .git: not a git checkout), exit 1 " in run(
        STUB_UV_INSTALL_RC="2"
    )
    log = (Path(env["HOME"]) / "agent-context" / "setup" / "install.log").read_text()
    assert " start install.sh compat=" in log and " commit=- " in log, log
    (src / ".git").mkdir()  # a .git no git and no file read can say anything of
    assert f"source commit unknown (git cannot read {shown}/.git);" in run("--report-only")
    # B13 round 10 L29: a real run whose SOURCE git refuses as another account's says so
    (src / ".git").rmdir()
    _git_checkout(src)
    refused = (
        f"source commit unknown (git refuses {shown}/.git: the folder belongs to another user account), "
    )
    assert refused in run(STUB_UV_INSTALL_RC="2", GIT_TEST_ASSUME_DIFFERENT_OWNER="1")


def test_the_shell_report_only_names_this_checkouts_commit(env: dict[str, str], tmp_path: Path) -> None:
    """--report-only stops before the run reads its SOURCE, so the shell fallback's Summary said
    "source commit -" (B12 held, H16). It names this installer's checkout, as --version does."""
    src = tmp_path / "checkout"
    (src / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, src / "scripts" / "install.sh")
    git = _git_checkout(src)
    sha = subprocess.run(
        [*git, "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    def summary(extra: dict[str, str] | None = None) -> str:
        cp = subprocess.run(
            [BASH32, str(src / "scripts" / "install.sh"), "--report-only"],
            capture_output=True,
            text=True,
            check=False,
            env={**env, **(extra or {})},
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        assert cp.returncode == 0, cp.stdout + cp.stderr
        assert "(shell fallback: agentsync is not installed)" in cp.stdout
        [line] = [ln for ln in report_path(env).read_text().splitlines() if ln.startswith("- install.sh: ")]
        return line

    assert summary() == (
        f"- install.sh: setup-prompt-compat {COMPAT}, source commit {sha}; this --report-only run only "
        "writes the report; no install log (no install run on this Mac)"
    )
    with (src / "scripts" / "install.sh").open("a") as fh:
        fh.write("# a local change\n")
    assert f"source commit {sha}-dirty tree={_fingerprint(git)};" in summary()
    no_tools = {"DEVELOPER_DIR": str(tmp_path / "none")}
    assert f"source commit {sha} (local changes not checked);" in summary(no_tools)
    # B13 round 10 L29: git's refusal of a folder another account owns is named, never that account
    assert (
        f"source commit {sha} (local changes not checked: git refuses this folder, which belongs to another "
        "user account);" in summary({"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"})
    )
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    trace = tmp_path / "git.trace"
    cp = subprocess.run(
        [BASH32, str(src / "scripts" / "install.sh"), "--report-only"],
        capture_output=True,
        text=True,
        check=False,
        env={**env, "GIT_TRACE": str(trace)},
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert cp.returncode == 0 and "(agentsync setup-report)" in cp.stdout, cp.stdout + cp.stderr
    assert not trace.exists(), "a report agentsync writes runs no git"


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


def test_a_checkout_under_home_is_named_with_a_tilde(env: dict[str, str]) -> None:
    """A checkout under this home is named ~/..., as the README's approval rules write it, so an agent that
    copies the NEXT command matches them; the ~ is outside the shell quoting, so the command still runs
    (B13 round 4 Q7). A checkout elsewhere keeps the absolute form (every other test)."""
    home = Path(env["HOME"])
    (home / "src").mkdir()
    (home / "src" / "agent-context-sync").symlink_to(REPO)
    (_cloud(env) / "OneDrive-Contoso" / "Project Alpha").mkdir(parents=True)
    script = home / "src" / "agent-context-sync" / "scripts" / "install.sh"
    cp = subprocess.run(
        [BASH32, str(script), "--list-folders"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    tilde = "~/src/agent-context-sync/scripts/install.sh"
    assert f'then run: {tilde} --source-local "<folder>"' in last_line(cp)
    run = subprocess.run(
        [BASH32, "-c", f"{tilde} --version"], capture_output=True, text=True, check=False, env=env
    )
    assert run.returncode == 0 and f"setup-prompt-compat {COMPAT}" in run.stdout, run.stdout + run.stderr


@pytest.mark.parametrize("parent", ["Développement", "Contoso\u2019s files", "My files"])
def test_a_tilde_command_runs_whatever_bytes_the_checkout_path_holds(
    env: dict[str, str], parent: str
) -> None:
    """B13 round 5 F53: %q writes a name with a byte it finds unprintable (an accent under C, a curly
    apostrophe) as $'..', and "~$'/...'" holds the slash inside the quotes, where bash 3.2 does not expand
    the ~: every NEXT and re-run command of such a checkout failed. The slash stays outside the quoting."""
    home = Path(env["HOME"])
    (home / parent).mkdir()
    (home / parent / "agent-context-sync").symlink_to(REPO)
    (_cloud(env) / "OneDrive-Contoso" / "Project Alpha").mkdir(parents=True)
    script = home / parent / "agent-context-sync" / "scripts" / "install.sh"
    cp = subprocess.run(
        [BASH32, str(script), "--list-folders"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    command = last_line(cp).split("then run: ", 1)[1].split(" --source-local ", 1)[0]
    assert command.startswith("~/"), command
    run = subprocess.run(
        [BASH32, "-c", f"{command} --version"], capture_output=True, text=True, check=False, env=env
    )
    assert run.returncode == 0 and f"setup-prompt-compat {COMPAT}" in run.stdout, (command, run.stderr)


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


def test_list_folders_marks_a_synced_folder_stored_through_a_link_in_cloud_storage(
    env: dict[str, str],
) -> None:
    """B13 round 6 G39: a source stored through a link in CloudStorage that names the provider folder is the
    folder the list shows by its real name. Compared as text, the list printed the link form as a synced
    folder outside it and left the real one unmarked, so step 1 offered it again. Folders are compared by
    identity, and by their real path for the inside mark."""
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "Project Alpha" / "Inner").mkdir(parents=True)
    (cs / "OneDrive-Contoso" / "Project Beta").mkdir(parents=True)
    (cs / "OneDrive-Link").symlink_to(cs / "OneDrive-Contoso", target_is_directory=True)
    _write_config(env, local_source_table("project-alpha", cs / "OneDrive-Link" / "Project Alpha"))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below)",
        f"[synced] {cs}/OneDrive-Contoso/Project Alpha",
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Project Alpha/Inner",
        f"{cs}/OneDrive-Contoso/Project Beta",
    ]


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
    # a new Mac's exit and NEXT, which adds that a config is there (B13 round 8 J17)
    assert cp.returncode == 3 and cp.stdout.splitlines() == [
        _unread_next(env, head=only, failed="ConfigError")
    ]
    assert cp.stderr.startswith(f"warning: could not read which folders {cfg} already syncs ")
    # B13 round 7 H17, H28; and why, as the class of the reader's error (H8, H16)
    unread = r" result=failed note=(not-signed-in|no-folders) synced=unreadable marks=failed:ConfigError$"
    assert re.search(unread, install_log(env)[-2]), install_log(env)[-2]


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


def _unread_next(
    env: dict[str, str], *, timeout: bool = False, head: str = LIST_NEXT, failed: str | None = None
) -> str:
    """The NEXT of a list whose reader could not read the config: ``head``, the one it would be on a new Mac,
    and what it adds since B13 round 8 J17. ``failed``: the class the installed agentsync raised, whose
    clause claims nothing is kept (B13 round 10 L11)."""
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    if failed is not None:
        return (
            f"{head}; this Mac has a config ({cfg}) whose folders could not be read: the installed "
            f"agentsync failed while reading it{f' (it raised {failed})' if failed else ''}, and "
            "~/.local/bin/agentsync status prints why. Step 2 reinstalls agentsync first, and if that "
            "version cannot load it either, step 2 stops at its config step and nothing is kept or added "
            "until the config is fixed"
        )
    also = f"; or re-run {INSTALL_SH} --list-folders to mark them" if timeout else ""
    return (
        f"{head}; this Mac has a config ({cfg}) whose folders could not be read: if it was set up before, "
        f"{INSTALL_SH} with no --source-local keeps whatever that config syncs, and each --source-local only "
        f"adds{also}"
    )


@pytest.mark.parametrize("broken", ["no installed agentsync", "a config that does not load"])
def test_list_folders_marks_nothing_when_the_config_cannot_be_read(env: dict[str, str], broken: str) -> None:
    """A config nobody can read is not a reason to guess: the list is the new Mac's, where the person is
    asked, and one warning says why no folder is marked. The NEXT adds that the config is there and that a
    run with no folder keeps whatever it syncs, with no count, since none was read (B13 round 8 J17)."""
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    cfg = _write_config(env, local_source_table("fy26", cs / "OneDrive-Contoso" / "FY26 Projects"))
    if broken == "a config that does not load":
        _tool_python(env)
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[source]]\nid = \n", encoding="utf-8")
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stderr
    # B13 round 10 L11: a config the installed agentsync failed on is not said to be kept by step 2
    failed = "ConfigError" if broken == "a config that does not load" else None
    assert cp.stdout == f"{cs}/OneDrive-Contoso/FY26 Projects\n{_unread_next(env, failed=failed)}\n"
    assert failed is None or "keeps" not in cp.stdout
    # B13 round 7 H14, H16 and H27: the warning says which of the two it was, in fixed words; round 10 L37:
    # words that blame neither the config nor agentsync, and the class
    why = {
        "no installed agentsync": "no agentsync is installed",
        "a config that does not load": (
            "the installed agentsync failed while reading it and raised ConfigError"
        ),
    }[broken]
    assert cp.stderr == (
        f"warning: could not read which folders {cfg} already syncs ({why}), so none is marked below\n"
    )
    # no count, since it was not read, but the log says it could not be, apart from a new Mac's list: the
    # report then knows the folder question was agentsync's doing (B13 round 7 H17, H28)
    # and why, in a fixed word with at most the class of the reader's error (H8, H16)
    marks = {"no installed agentsync": "no-tool", "a config that does not load": "failed:ConfigError"}[broken]
    assert install_log(env)[-2].endswith(f" result=done note=listed-1 synced=unreadable marks={marks}")


@pytest.mark.parametrize("case", ["a duplicate source id", "an older agentsync that rejects a key"])
def test_step_1_says_nothing_is_kept_of_a_config_the_installed_agentsync_failed_on(
    real_env: dict[str, str], wheel: Path, case: str
) -> None:
    """B13 round 10 L11: for marks=failed step 1 said a run with no --source-local keeps whatever the config
    syncs, and that each --source-local only adds. A config that cannot load stops step 2 at its config step,
    where neither holds; one an older agentsync rejected (a newer key) loads once step 2 reinstalls. The NEXT
    names the class and status, and says both outcomes; the two step 2 runs show each."""
    env = real_env
    alpha = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / "Alpha"
    alpha.mkdir(parents=True)
    first = install_sh(env, str(wheel), "--source-local", str(alpha))
    assert first.returncode == 0, first.stdout + first.stderr
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    before = cfg.read_text(encoding="utf-8")
    if case == "a duplicate source id":
        cfg.write_text(before + "\n[[source]]" + before.split("[[source]]")[-1], encoding="utf-8")
    else:  # the installed build's load_config rejects the config, as one from before a key it holds would
        py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
        shim = (
            "import sys, agentsync.config as _c\n"
            'def _old(*a, **k):\n    raise _c.ConfigError("unknown key(s) in [convert]: ocr")\n'
            '_c.load_config = _old\nexec(compile(sys.argv.pop(1), "<string>", "exec"))\n'
        )
        py.unlink()
        _write_exe(
            py,
            "#!/bin/bash\n"
            '[ "$1" = -I ] && [ "$2" = -c ] || exec "' + sys.executable + '" "$@"\n'
            'code="$3"\nshift 3\n'
            f'exec "{sys.executable}" -I -c \'{shim}\' "$code" "$@"\n',
        )
    cp = install_sh(env, "--list-folders")
    assert install_log(env)[-2].endswith(" synced=unreadable marks=failed:ConfigError"), install_log(env)[-2]
    assert last_line(cp).endswith(
        "whose folders could not be read: the installed agentsync failed while reading it (it raised "
        "ConfigError), and ~/.local/bin/agentsync status prints why. Step 2 reinstalls agentsync first, "
        "and if that version cannot load it either, step 2 stops at its config step and nothing is kept "
        "or added until the config is fixed"
    ), last_line(cp)
    assert "keeps whatever" not in cp.stdout and "only adds" not in cp.stdout
    second = install_sh(env, str(wheel))
    if case == "a duplicate source id":
        assert second.returncode == 1, second.stdout + second.stderr
        assert "duplicate source id" in second.stdout + second.stderr
    else:
        assert second.returncode == 0, second.stdout + second.stderr
        assert cfg.read_text(encoding="utf-8") == before, "the reinstalled agentsync keeps the config"


def test_list_folders_with_no_provider_says_an_unread_config_is_there(env: dict[str, str]) -> None:
    """B13 round 8 J17: with nothing listed (OneDrive signed out), a set-up Mac whose reader failed was told
    to sign in and exit 3, as a new Mac is, where a readable one is told a re-run keeps its folders. The
    sign-in NEXT is unchanged and adds what an unread config means."""
    _write_config(env, local_source_table("fy26", _cloud(env) / "OneDrive-Contoso" / "FY26 Projects"))
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    head = f"NEXT: OneDrive is not signed in on this Mac: sign in to OneDrive, then re-run: {INSTALL_SH} "
    head += "--list-folders"
    assert last_line(cp) == _unread_next(env, head=head)


def _older_tool_python(env: dict[str, str], *missing: str) -> Path:
    """:func:`_tool_python` as a Mac set up earlier has it: an agentsync whose ``agentsync.paths`` lacks the
    names ``missing`` (they are removed before the ``-c`` code install.sh passes is run, so its import of one
    raises ImportError, as it does on that Mac)."""
    py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    drop = "".join(f"del _p.{name}\n" for name in missing)
    run = 'exec(compile(sys.argv.pop(1), "<string>", "exec"))'
    shim = f"import sys, agentsync.paths as _p\n{drop}del _p\n{run}\n"
    return _write_exe(
        py,
        "#!/bin/bash\n"
        '[ "$1" = -I ] && [ "$2" = -c ] || exec "' + sys.executable + '" "$@"\n'
        'code="$3"\nshift 3\n'
        f'exec "{sys.executable}" -I -c \'{shim}\' "$code" "$@"\n',
    )


def test_list_folders_marks_the_synced_folders_with_an_agentsync_from_before_round_6(
    env: dict[str, str],
) -> None:
    """B13 round 7 H7 (H15, H27 and H38 are the same defect): step 1 runs the reader with the agentsync an
    earlier setup installed, and step 2 is what updates it. Round 6 had the reader import
    ``agentsync.paths.provider_link_resolved``, which no agentsync installed before 2026-10-13 has: on every
    such Mac the import failed, no folder was marked, no "already synced" line was printed, and an unattended
    agent stopped at the folder question. The import is guarded, and the older agentsync gets the same single
    readlink: a folder stored by its plain path and one stored through a provider link are both marked."""
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "Project Alpha" / "Inner").mkdir(parents=True)
    (cs / "OneDrive-Contoso" / "Project Beta").mkdir(parents=True)
    (cs / "OneDrive-Contoso" / "Project Gamma").mkdir(parents=True)
    (cs / "OneDrive-Link").symlink_to(cs / "OneDrive-Contoso", target_is_directory=True)
    _write_config(
        env,
        local_source_table("project-alpha", cs / "OneDrive-Link" / "Project Alpha"),
        local_source_table("project-beta", cs / "OneDrive-Contoso" / "Project Beta"),
        _inbox(env),
    )
    _older_tool_python(env, "provider_link_resolved")
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 2 folder(s) (marked [synced] below)",
        f"[synced] {cs}/OneDrive-Contoso/Project Alpha",
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Project Alpha/Inner",
        f"[synced] {cs}/OneDrive-Contoso/Project Beta",
        f"{cs}/OneDrive-Contoso/Project Gamma",
    ]
    assert last_line(cp).startswith("NEXT: this Mac already syncs 2 folder(s), and a re-run keeps them: ")
    assert install_log(env)[-2].endswith(" result=done note=listed-4 synced=2")
    _tool_python(env)  # step 2 updated it: the list is the same with agentsync's own function
    again = install_sh(env, "--list-folders")
    assert (again.returncode, again.stdout, again.stderr) == (0, cp.stdout, "")


def test_list_folders_marks_a_folder_stored_through_a_link_under_a_linked_home_with_an_older_agentsync(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 8 J38 (J49 and J72 are the same defect): the fallback copy of ``provider_link_resolved`` that
    step 1 gives an agentsync from before 2026-10-13 returned a link's target in the form the link was written
    in, as paths.py did before H34. Under a home reached through a link (a /tmp sandbox is /private/tmp), a
    link written in the unresolved form then named a path no listed folder matched: the synced folder was
    offered again and printed first, outside the list. The copy now returns the form of the root that held
    the path, as paths.py does, and the list is the same as with agentsync's own function."""
    (tmp_path / "real" / "home").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    env = {**env, "HOME": str(tmp_path / "alias" / "home")}
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "Project Alpha" / "Inner").mkdir(parents=True)
    (cs / "OneDrive-Contoso" / "Project Beta").mkdir(parents=True)
    (cs / "OneDrive-Link").symlink_to(cs / "OneDrive-Contoso", target_is_directory=True)  # the alias form
    _write_config(
        env, local_source_table("project-alpha", cs / "OneDrive-Link" / "Project Alpha"), _inbox(env)
    )
    _older_tool_python(env, "provider_link_resolved")
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder(s) (marked [synced] below)",
        f"[synced] {cs}/OneDrive-Contoso/Project Alpha",
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Project Alpha/Inner",
        f"{cs}/OneDrive-Contoso/Project Beta",
    ]
    _tool_python(env)
    again = install_sh(env, "--list-folders")
    assert (again.returncode, again.stdout, again.stderr) == (0, cp.stdout, "")


def test_list_folders_says_why_an_installed_agentsync_could_not_run_the_reader(env: dict[str, str]) -> None:
    """B13 round 7 H14, H16 and H27 (one defect): the reader's stderr was thrown away and the warning said
    "no installed agentsync loads it", which is false on a Mac that has one. An agentsync older than the
    reader supports fails on an import: the warning says it is older and names what it lacks, and nothing
    else of the error line (which holds a path) is printed. One that runs past its 10 s is said to."""
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "Project Alpha").mkdir(parents=True)
    cfg = _write_config(env, local_source_table("project-alpha", cs / "OneDrive-Contoso" / "Project Alpha"))
    head = f"warning: could not read which folders {cfg} already syncs ("
    unmarked = f"{cs}/OneDrive-Contoso/Project Alpha\n{_unread_next(env)}\n"
    _older_tool_python(env, "provider_link_resolved", "is_under")
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stdout) == (0, unmarked)
    assert cp.stderr == (
        f"{head}the installed agentsync is older than this installer: it has no is_under), so none is "
        "marked below\n"
    )
    assert "((" not in cp.stderr and "))" not in cp.stderr, "no bracket inside the bracket (B13 round 8 J27)"
    assert install_log(env)[-2].endswith(" note=listed-1 synced=unreadable marks=older:is_under")
    py = _tool_python(env)
    _write_exe(py, "#!/bin/sh\nexec sleep 30\n")
    cp = install_sh(env, "--list-folders")
    # a retry of the list is the cheap fix for a loaded Mac (B13 round 8 J17)
    assert (cp.returncode, cp.stdout) == (
        0,
        unmarked.replace(_unread_next(env), _unread_next(env, timeout=True)),
    )
    assert cp.stderr == f"{head}the reader took over 10 s), so none is marked below\n"
    assert install_log(env)[-2].endswith(" note=listed-1 synced=unreadable marks=timeout")
    # what the report makes of the first of those lines (it reads the log these runs wrote)
    runs = setup_report.read_install_runs(Path(env["HOME"]) / "agent-context" / "setup" / "install.log")
    assert setup_report.unread_why(runs) == (
        "the installed agentsync was older than the installer and had no is_under"
    )


def _install_sh_python(function: str) -> ast.Module:
    """The Python that install.sh's shell function ``function`` passes to ``-c``, parsed."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text[text.index(f"\n{function}() {{") :]
    code = body[body.index("-c '\n") + len("-c '\n") :]
    return ast.parse(code[: code.index("\n' ")])


BASELINE_AGENTSYNC_NAMES = {"agentsync.config": {"canonical_source_root", "load_config"}}
"""What install.sh's step 1 reader may import from agentsync with no fallback: the names every agentsync
since 2026-09-29 has. Step 1 runs it with the agentsync an earlier setup left."""


def test_the_synced_folder_reader_imports_no_agentsync_name_an_older_build_lacks() -> None:
    """B13 round 7 H7: the next name gets the same guard. Every ``from agentsync...`` import at the top of
    the reader is a baseline name; any other sits in a ``try`` whose ``except ImportError`` defines every
    name it imports, with nothing from agentsync."""
    tree = _install_sh_python("synced_folders")
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agentsync"):
            extra = {a.name for a in node.names} - BASELINE_AGENTSYNC_NAMES.get(node.module or "", set())
            assert not extra, f"unguarded import of {sorted(extra)} from {node.module}"
            seen |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("agentsync") for a in node.names), ast.unparse(node)
        elif isinstance(node, ast.Try):
            imported = {
                a.asname or a.name for n in node.body if isinstance(n, ast.ImportFrom) for a in n.names
            }
            if not imported:
                continue
            [handler] = node.handlers
            assert isinstance(handler.type, ast.Name) and handler.type.id == "ImportError", "ImportError only"
            defined = {n.name for n in handler.body if isinstance(n, ast.FunctionDef)}
            assert imported <= defined, f"no fallback for {sorted(imported - defined)}"
            assert "agentsync" not in ast.unparse(handler), "a fallback uses the standard library alone"
            seen |= imported
    assert seen == {"canonical_source_root", "load_config", "provider_link_resolved"}
    nested = [
        n
        for top in tree.body
        if not isinstance(top, ast.Try)
        for n in ast.walk(top)
        if n is not top and isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert not nested, "an import inside a function would not be seen here"


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
    assert added[2] < out.index(repo) < out.index(sources), "beside the sources line (B13 round 8 J13)"
    [inbox] = [ln for ln in out if ln.startswith("added inbox source ")]
    assert added[2] < out.index(inbox) < out.index(repo), "held with it, not between two folders (round 9 K7)"
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


def test_several_folders_print_the_docs_repo_line_beside_the_sources_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 8 J13: the first call's ``docs repo`` line (and its ``time machine:`` lines) came out where
    that call printed it, between the first and the second folder's lines, and read as the first folder's
    result. It is held and printed directly above the last call's ``sources:`` line. A later call that fails
    still has it printed, before its error, as it was before; one folder holds nothing."""
    other = folder.parent / "Other"
    other.mkdir()
    e = {**env, "STUB_ADD_SOURCE_SUMMARY": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--source-local", str(other))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    first = out.index("added source 'FY26 Projects'")
    assert out[first : first + 6] == [
        "added source 'FY26 Projects'",
        "added source 'Other'",
        f"added inbox source 'inbox' ({Path(env['HOME'])}/agent-context/inbox): drop files here",
        f"docs repo {Path(env['HOME'])}/agent-context/docs (exists, no remote; scaffold up to date)",
        "time machine: excluded the cache (stub)",
        "sources: Other",
    ], out
    assert sum(ln.startswith(("docs repo ", "time machine: ")) for ln in out) == 2

    failed = install_sh(
        {**e, "STUB_ADD_SOURCE_FAIL": str(other)},
        str(wheel),
        "--source-local",
        str(folder),
        "--source-local",
        str(other),
    )
    assert failed.returncode == 1, failed.stdout + failed.stderr
    out = failed.stdout.splitlines()
    assert sum(ln.startswith("docs repo ") for ln in out) == 1 and out.index(
        "added source 'FY26 Projects'"
    ) < (
        out.index(
            f"docs repo {Path(env['HOME'])}/agent-context/docs (exists, no remote; scaffold up to date)"
        )
    )
    assert not any(ln.startswith("sources: ") for ln in out)
    assert f"error: agentsync add-source {other} failed (see the error above)" in failed.stderr

    one = install_sh(e, str(wheel), "--source-local", str(folder))
    out = one.stdout.splitlines()
    assert one.returncode == 0 and out[out.index("added source 'FY26 Projects'") + 1].startswith(
        "docs repo "
    ), out


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
    assert found.endswith(" (fix: ~/.local/bin/agentsync sync (it makes these owner-only))"), (
        "a command an agent may run"
    )

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
    assert (
        f"note: {env['HOME']}/.local/bin is not on PATH; nothing to change: this setup and its NEXT lines "
        "call agentsync by its full path, ~/.local/bin/agentsync"
    ) in cp.stdout, "the form the NEXT lines print (B13 round 5 F51)"
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
    # At least one progress line comes before status's own lines; a late tick may land between them and the
    # pipeline's end, a race, not a wrong order (B13 round 2 R98).
    assert waits and waits[0] < out.index("[warn] launchd.poll — not loaded (fix: agentsync install-agent)")
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


def test_a_signal_exit_is_named_by_its_signal() -> None:
    """``rc_meaning`` names any signal's exit (128 + N) as ``stopped by SIG<NAME> (signal N)``, the phrase of
    ``ocr._ended``: a first sync jetsam killed read "exit 137". 129, 130 and 143 keep their words, and a code
    that is no signal's stays ``exit N`` (B13 round 2 R81)."""
    fn = re.search(r"^rc_meaning\(\) \{\n.*?^\}\n", INSTALL_SH.read_text(encoding="utf-8"), re.M | re.S)
    assert fn is not None
    codes = ["137", "134", "141", "143", "130", "200", "1", "64"]
    cp = subprocess.run(
        [BASH32, "-c", fn.group(0) + 'for c in "$@"; do rc_meaning "$c"; echo; done', "-", *codes],
        capture_output=True,
        text=True,
        check=True,
    )
    assert cp.stdout.splitlines() == [
        "stopped by SIGKILL (signal 9)",
        "stopped by SIGABRT (signal 6)",
        "stopped by SIGPIPE (signal 13)",
        "stopped by SIGTERM (a tool timeout?)",
        "stopped by SIGINT",
        "exit 200",
        "failed",
        "the launcher refused the job: its program is not the one it starts, or an option is wrong",
    ]


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


SECOND_REMEDY = (
    f'; or, if the first line of the prompt says "setup prompt v{COMPAT}": run step 1\'s command again once, '
    "exactly as the prompt writes it"
)
"""The stop line's second remedy, after its first (B13 round 9 K18); the NEXT carries only the first. Its
cause is in the error column alone (B13 round 10 L20)."""


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
        # a newer copy's fix depends on this checkout's origin and upstream (the tests of newer_copy_fix)
        newer_copy = why == "this checkout is older than the prompt"
        cp = install_sh(env, "--log-start", value)
        assert cp.returncode == 2 and cp.stdout == "", (value, cp.stdout, cp.stderr)
        assert cp.stderr.startswith(
            f"error: the pasted setup prompt is {said} and this installer is for setup prompt v{COMPAT}: "
        ), cp.stderr
        assert why in cp.stderr and cp.stderr.count("\n") == 1, "one line"
        told = (
            f"Stop here and run no other step of that prompt. Tell the person to copy the prompt again from "
            f'{PROMPT_SOURCE} ("Set up on a new Mac: one prompt") and paste it into a new session. If the '
            f'first line of your prompt says "setup prompt v{COMPAT}", the --log-start value was changed: '
            "run step 1's command again once, exactly as the prompt writes it.\n"
        )
        assert newer_copy or told in cp.stderr
        assert "Stop here and run no other step of that prompt. Tell the person to " in cp.stderr
        assert f"prompt v{COMPAT}," not in cp.stderr.replace(value, ""), "no value that passes is shown"
        attempt = setup_report.parse_friction(friction_path(env).read_text()).attempts[-1]
        assert attempt.header == {"Prompt": said, "Agent": agent}
        event, closed = attempt.events
        assert (event.step, event.kind) == (1, "error") and why in event.what
        assert event.what.endswith(f"; setup stopped (exit {cp.returncode})"), "the line gives the real exit"
        assert setup_report._PROMPT_STOP_RE.match(event.what), "the report still reads the line as the stop"
        assert newer_copy or event.fix == f"copy the prompt again from {PROMPT_SOURCE}{SECOND_REMEDY}"
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


def _checkout_following(tmp_path: Path, origin: str | None, upstream: bool | str) -> Path:
    """A git checkout of install.sh whose origin is ``origin`` and whose main tracks origin/main when
    ``upstream``, or main of a remote named upstream on the repository itself when it is that word; its
    install.sh. "gone": main tracks origin/main, whose ref is then deleted; "detached": no branch."""
    src = tmp_path / "checkout"
    (src / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, src / "scripts" / "install.sh")
    git = _git_checkout(src)
    subprocess.run([*git, "branch", "-M", "main"], check=True, capture_output=True)
    if origin is not None:
        subprocess.run([*git, "remote", "add", "origin", origin], check=True, capture_output=True)
        subprocess.run(
            [*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True, capture_output=True
        )
    if upstream == "upstream":
        canon = "https://github.com/renchris/agent-context-sync.git"
        subprocess.run([*git, "remote", "add", "upstream", canon], check=True, capture_output=True)
        subprocess.run(
            [*git, "update-ref", "refs/remotes/upstream/main", "HEAD"], check=True, capture_output=True
        )
        subprocess.run([*git, "branch", "-u", "upstream/main"], check=True, capture_output=True)
    elif upstream:
        subprocess.run([*git, "branch", "-u", "origin/main"], check=True, capture_output=True)
    if upstream == "gone":
        subprocess.run(
            [*git, "update-ref", "-d", "refs/remotes/origin/main"], check=True, capture_output=True
        )
    elif upstream == "detached":
        subprocess.run([*git, "checkout", "-q", "--detach"], check=True, capture_output=True)
    return src / "scripts" / "install.sh"


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
@pytest.mark.parametrize(
    ("origin", "upstream", "kind"),
    [
        ("https://github.com/renchris/agent-context-sync.git", True, "copy-again"),
        ("git@github.com:renchris/agent-context-sync", True, "copy-again"),
        ("https://example.invalid/contoso/agent-context-sync.git", True, "checkout"),
        ("https://jdoe:secret-token@example.invalid/contoso/agent-context-sync", False, "checkout"),
        ("https://github.com/renchris/agent-context-sync", False, "no-upstream"),
        ("https://github.com/renchris/agent-context-sync", "gone", "both"),
        ("https://github.com/renchris/agent-context-sync", "detached", "no-branch"),
        ("https://example.invalid/contoso/agent-context-sync.git", "detached", "checkout"),
        (None, False, "both"),
        ("https://example.invalid/contoso/agent-context-sync.git", "upstream", "copy-again"),
    ],
)
def test_a_copy_newer_than_the_checkout_is_told_what_fixes_it(
    env: dict[str, str], tmp_path: Path, origin: str | None, upstream: bool | str, kind: str
) -> None:
    """B13 round 5 F17: a copy newer than the installer was told to copy the prompt again from the README;
    when this Mac's checkout does not follow main of the repository (a fork or a stale mirror as origin), the
    fresh copy is the same newer one and stops here again. The installer reads its own checkout's origin
    and upstream, read-only: following main there, the copy came from another branch or a fork, and copying
    it again fixes it; following anything else, the checkout is the cause and is named; when git cannot say,
    both. The stderr, the friction line's fix and the stopped report's NEXT say the same."""
    script = _checkout_following(tmp_path, origin, upstream)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH32, str(script), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=env,
            stdin=subprocess.DEVNULL,
        )

    cp = run("--log-start", f"prompt v{COMPAT + 1}, Contoso Agent")
    assert cp.returncode == 2 and cp.stderr.count("\n") == 1, cp.stderr
    [event, _closed] = setup_report.parse_friction(friction_path(env).read_text()).attempts[-1].events
    copy_again = f'copy the prompt again from {PROMPT_SOURCE} ("Set up on a new Mac: one prompt")'
    if kind == "copy-again":
        assert f"Tell the person to {copy_again} and paste it into a new session: " in cp.stderr
        assert "another branch or a fork" in cp.stderr and "point this Mac's checkout" not in cp.stderr
    elif kind == "checkout":
        shown = "https://example.invalid/contoso/agent-context-sync"
        assert "Tell the person to point this Mac's checkout (" in cp.stderr and shown in cp.stderr
        assert "copy the prompt again from" not in cp.stderr, "a fresh copy would stop here again"
        assert ("it follows origin/main of " in cp.stderr) == (upstream is True)
        if not upstream or upstream == "detached":
            on = "no upstream branch" if not upstream else "on no branch"
            assert f" ({on}), so a fresh copy" in cp.stderr, cp.stderr
        assert "secret-token" not in cp.stderr + friction_path(env).read_text() and "jdoe" not in cp.stderr
    else:
        assert f"Tell the person to {copy_again} and paste it into a new session; if that copy stops" in (
            cp.stderr
        )
        # what git says of the checkout: no upstream is an answer; a configured one it cannot read is not
        # (B13 round 6 G21)
        said = {
            "both": "git could not say which branch it follows",
            "no-upstream": "it follows no upstream branch",
            "no-branch": "it is on no branch",
        }[kind]
        assert f"main of https://github.com/renchris/agent-context-sync ({said}): point it" in cp.stderr
    told = cp.stderr.split("Tell the person to ", 1)[1]
    tell = told.split('. If the first line of your prompt says "', 1)[0]
    if kind == "copy-again":
        assert event.fix == tell + SECOND_REMEDY, "the friction line's fix is what the agent was told"
    else:  # the same without the checkout's path or remote URL: the friction log reaches the report
        assert str(script.parents[1]) not in event.fix and "example.invalid" not in event.fix, event.fix
        first = event.fix.removesuffix(SECOND_REMEDY)
        assert first.rsplit(", ", 1)[1] == tell.rsplit(", ", 1)[1], (event.fix, tell)
    reported = run("--report-only")
    assert reported.returncode == 0, reported.stdout + reported.stderr
    assert last_line(reported) == (
        f"{STOPPED_NEXT}, which is for setup prompt v{COMPAT}, so do not send it or bring it back: "
        f"{event.fix.removesuffix(SECOND_REMEDY)} "
        f"[setup report: {friction_path(env).parent / 'stopped-copy-report.md'}]"
    ), last_line(reported)
    side = friction_path(env).parent / "stopped-copy-report.md"
    assert "example.invalid" not in reported.stdout + side.read_text(), "nor in what the report holds"


def test_a_stopped_newer_copys_next_says_its_version_once_and_of_the_installer(env: dict[str, str]) -> None:
    """B13 round 6 G9, G19: a newer copy's NEXT ended "... from another branch or a fork (this installer is
    for setup prompt v14) (<path> was not touched: ...)": two brackets in a row, the first read as part of the
    clause before it. The version is said once, after "this installer", and one bracket follows the fix."""
    back = report_path(env).parent / "bring-back.md"
    back.parent.mkdir(parents=True)
    back.write_text("")
    assert install_sh(env, "--log-start", f"prompt v{COMPAT + 1}, Contoso Agent").returncode == 2
    next_line = last_line(install_sh(env, "--report-only"))
    assert next_line.startswith(f"{STOPPED_NEXT}, which is for setup prompt v{COMPAT}, so do not send it")
    assert ") (" not in next_line and next_line.count("setup prompt v") == 1, next_line
    # B13 round 7 H18: the bring-back bracket stands after "bring it back", so the fix is the last clause
    kept = f"bring it back ({back} was not touched and still holds the report of the last attempt that was "
    assert f"{kept}not stopped): " in next_line, next_line
    fix = next_line.split("not stopped): ", 1)[1]
    # the fix's own last words depend on what this checkout follows; no bracket of the bring-back's follows it
    assert fix.startswith("copy the prompt again from ") and "was not touched" not in fix
    assert fix.endswith(f" [setup report: {friction_path(env).parent / 'stopped-copy-report.md'}]"), next_line
    assert not fix.rsplit(" [setup report: ", 1)[0].endswith(")"), next_line


def test_a_stopped_newer_copy_is_not_called_out_of_date(env: dict[str, str]) -> None:
    """B13 round 5 F18: a newer copy's report said "an out-of-date copy" in its first line and its NEXT, while
    its Summary said the copy is newer than the installer's. Both say the copy does not match this installer,
    true of an older, an unstated and a newer copy alike, and still say not to send the report."""
    assert install_sh(env, "--log-start", f"prompt v{COMPAT + 1}, Contoso Agent").returncode == 2
    reported = install_sh(env, "--report-only")
    side = friction_path(env).parent / "stopped-copy-report.md"
    first = side.read_text().splitlines()[0]
    for text in (last_line(reported), first):
        assert "out-of-date" not in text and "does not match this installer" in text, text
        assert "do not send it or bring it back" in text


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


STOPPED_NEXT = (
    "NEXT: this report is of an attempt started by a copy of the setup prompt that does not match this "
    "installer"
)


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
    latest = setup_report.parse_friction(friction_path(env).read_text(encoding="utf-8")).latest
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
    exists keeps its bytes, and none is made where there was none. The report itself is still written, to
    setup/stopped-copy-report.md, so setup-report.md keeps its bytes too; its first line says not to send it
    (B13 round 2 R17), and its NEXT names bring-back.md only where one exists, as untouched (R18)."""
    back = report_path(env).parent / "bring-back.md"
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    warning = (
        "> This report is of an attempt started by a copy of the setup prompt that does not match this "
        "installer: do not send"
    )

    def invites(cp: subprocess.CompletedProcess[str]) -> list[str]:
        return [
            ln.split(":", 1)[0]
            for ln in cp.stdout.splitlines()
            if ln.startswith(("bring back:", "issue link"))
        ]

    assert install_sh(env, "--log-start", "x").returncode == 2
    stopped = install_sh(env, "--report-only")
    assert stopped.returncode == 0 and last_line(stopped).startswith(STOPPED_NEXT), stopped.stdout
    assert invites(stopped) == [] and not report_path(env).exists() and not back.exists()
    assert "bring-back.md" not in last_line(stopped) and last_line(stopped).endswith(
        f" [setup report: {side}]"
    )
    first = side.read_text().splitlines()[0]
    assert first.startswith(warning) and "not stopped is" not in first
    _sends_nothing(side)
    _backdate(env, 11)
    assert install_sh(env, "--log", "1", "error", "git pull failed (exit 1)", "-").returncode == 0
    dry = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, "--report-only")
    assert dry.returncode == 0 and side.exists(), "a dry run writes no report, so it leaves the side file"
    real = install_sh(env, "--report-only")
    assert not side.exists(), "the stopped copy's report is stale once a real one is written (Q20)"
    assert last_line(real).startswith("NEXT: review ") and invites(real) == [
        "bring back",
        "issue link (review the report first)",
    ]
    kept, kept_report = back.read_bytes(), report_path(env).read_bytes()
    assert install_sh(env, "--log-start", "x").returncode == 2
    stopped = install_sh(env, "--report-only")
    assert last_line(stopped).startswith(STOPPED_NEXT) and invites(stopped) == [], stopped.stdout
    assert back.read_bytes() == kept and report_path(env).read_bytes() == kept_report
    assert (
        f"bring it back ({back} was not touched and still holds the report of the last attempt that was not "
        "stopped): " in (last_line(stopped))
    )
    first = side.read_text().splitlines()[0]
    assert first.startswith(warning) and first.endswith(
        " The report of the last attempt that was not stopped is ~/agent-context/setup-report.md."
    )
    assert env["HOME"] not in side.read_text()
    _sends_nothing(side)
    text = report_path(env).read_text()
    assert text.rstrip("\n").splitlines()[-1].startswith(setup_report.ISSUE_URL), (
        "a real report keeps its link"
    )


def _sends_nothing(side: Path) -> None:
    """A stopped copy's report ends saying it is not for sending: no "Send it:" line and no issue link,
    whichever writer wrote it (B13 round 4 Q10, Q18), not even an earlier run's quoted in it, and its legend
    does not say "before sending it" (B13 round 5 F16, F7)."""
    text = side.read_text()
    assert "Send it:" not in text and "issues/new" not in text, text[-600:]
    assert "Read the report before sending it" not in text and "is yours to remove." in text
    assert not [ln for ln in text.splitlines() if ln.startswith("bring back: ")]
    assert text.endswith("\n\nNot for sending: see the note at the top of this report.\n"), (
        "a paragraph of its own, not the Redaction part's last line (B13 round 6 G18)",
        text[-300:],
    )


def _old_renderer_report(summary: list[str], tail: list[str]) -> str:
    """A stopped copy's report as an agentsync older than F20 (B13 round 5) rendered it: the install.out
    embed's summary always ends "(what the agent saw)". ``summary`` is its Summary's installer lines,
    ``tail`` the install.out lines it quotes."""
    return "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            *summary,
            "",
            "## Installer",
            "",
            f"<details><summary>the last {len(tail)} line(s) of ~/agent-context/setup/install.out (what the "
            "agent saw)</summary>",
            "",
            "~~~text",
            *tail,
            "~~~",
            "",
            "</details>",
            "",
            "## Redaction",
            "",
            "Read the report before sending it: anything else confidential is yours to remove.",
            "Send it: the link on the last line opens a blank issue form. Nothing is sent automatically.",
            "",
            setup_report.ISSUE_URL,
            "",
        ]
    )


def _embed(side: Path) -> tuple[str, list[str]]:
    """The install.out embed's summary line and the lines its fence holds."""
    lines = side.read_text().splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("<details><summary>the last "))
    start = lines.index("~~~text", at)
    return lines[at], lines[start + 1 : lines.index("~~~", start)]


@pytest.mark.parametrize(
    ("summary", "label"),
    [
        (
            [
                "- install.sh: no run during this attempt (1 earlier in the install log)",
                "- installer output: no run of this attempt in the last 3 line(s) of install.out",
            ],
            "earlier attempts' install.sh output: this attempt ran no install.sh",
        ),
        (
            [
                "- install.sh: exit 0 (finished)",
                "- installer output: no run of this attempt in the last 3 line(s) of install.out",
            ],
            "earlier attempts' install.sh output: none of this attempt's runs is in it",
        ),
        (
            ["- install.sh: exit 0 (finished)", "- installer output: 1 NEXT: line(s) in 1 run(s)"],
            "what the agent saw",
        ),
    ],
)
def test_a_stopped_copy_rendered_by_an_older_agentsync_says_whose_install_out_it_quotes(
    env: dict[str, str], tmp_path: Path, summary: list[str], label: str
) -> None:
    """A stopped copy stops before step 2 reinstalls agentsync, so the first one after an update is rendered
    by the agentsync from before it, whose embed always reads "(what the agent saw)" (B13 round 6 G17). The
    installer gives it F20's label by the report's own Summary, as the renderer's condition is: an attempt
    whose run is in the tail keeps "(what the agent saw)"."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    old = tmp_path / "old-report.md"
    old.write_text(
        _old_renderer_report(summary, ["# run=20261012T090000Z-41 install.sh", "done", "NEXT: stop"])
    )
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", "x").returncode == 2
    cp = install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only")
    assert cp.returncode == 0 and "(agentsync setup-report)" in cp.stdout, cp.stdout
    head, fenced = _embed(side)
    assert (
        head
        == f"<details><summary>the last 3 line(s) of ~/agent-context/setup/install.out ({label})</summary>"
    )
    assert len(fenced) == 3
    _sends_nothing(side)


def test_the_shell_stopped_copy_warn_is_the_reports() -> None:
    [w] = re.findall(r'(?m)^STOPPED_COPY_WARN="(.*)" #', INSTALL_SH.read_text())
    assert w == setup_report._STOPPED_COPY_WARN


def test_the_shell_stopped_copy_beyond_is_the_reports() -> None:
    """B13 round 10 L39: the shim's Doctor header ends in K24's words, kept equal to the report's."""
    [b] = re.findall(r'(?m)^STOPPED_COPY_BEYOND="(.*)" #', INSTALL_SH.read_text())
    assert b == setup_report._STOPPED_COPY_BEYOND


STOPPED_WARN = setup_report._STOPPED_COPY_WARN
_COMMIT_WARN = (
    "[warn] install.commit — installed from 0123456789ab, but the checkout at ~/src/agent-context-sync is at "
    "89abcdef0123 (fix: re-run install.sh (~/src/agent-context-sync/scripts/install.sh))"
)
_DOCTOR_HEAD = "42 check(s): 39 ok, 0 info, 3 warn, 0 FAIL. Only the lines that are not ok:"
_EXPECTED_NOTE = (
    " expected warn(s) end in (expected: <why>) instead of their fix: nothing for this setup to do."
)
_STOPPED_NOTE = _EXPECTED_NOTE.removesuffix(".") + setup_report._STOPPED_COPY_BEYOND + "."
"""The header beside a stopped-copy reason, in K24's words (B13 round 10 L19, L39)."""


def _old_doctor_report(summary: str, head: str, fenced: list[str]) -> str:
    return "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Doctor",
            "",
            head,
            "",
            "~~~text",
            *fenced,
            "~~~",
            "",
            "39 ok: config",
            "",
            "## Redaction",
            "",
            "Read the report before sending it: anything else confidential is yours to remove.",
            "",
        ]
    )


@pytest.mark.parametrize(
    ("summary", "head", "fenced", "want_summary", "want_head", "want_fenced"),
    [
        (  # a lone install.commit warn
            "- doctor: 0 FAIL, 1 warn (42 checks) · unexpected 1: install.commit warn",
            _DOCTOR_HEAD,
            [_COMMIT_WARN],
            "- doctor: 0 FAIL, 1 warn (42 checks) · expected 1: install.commit "
            f"({STOPPED_WARN}) · unexpected 0",
            _DOCTOR_HEAD + " 1" + _STOPPED_NOTE,
            [_COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})"],
        ),
        (  # beside an ad hoc launcher's expected warn and another unexpected one
            "- doctor: 0 FAIL, 3 warn (42 checks) · expected 1: launcher.signature (ad hoc launcher) · "
            "unexpected 2: install.commit warn, launchd.poll warn",
            _DOCTOR_HEAD + " 1" + _EXPECTED_NOTE,
            [
                "[warn] launcher.signature — ad hoc (expected: ad hoc launcher)",
                _COMMIT_WARN,
                "[warn] launchd.poll — x",
            ],
            "- doctor: 0 FAIL, 3 warn (42 checks) · expected 2: launcher.signature (ad hoc launcher); "
            f"install.commit ({STOPPED_WARN}) · unexpected 1: launchd.poll warn",
            _DOCTOR_HEAD + " 2" + _STOPPED_NOTE,
            [
                "[warn] launcher.signature — ad hoc (expected: ad hoc launcher)",
                _COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})",
                "[warn] launchd.poll — x",
            ],
        ),
        (  # a renderer with J18 already, before K24: only its header gains K24's words (B13 round 10 L19)
            "- doctor: 0 FAIL, 1 warn (42 checks) · expected 1: install.commit "
            f"({STOPPED_WARN}) · unexpected 0",
            _DOCTOR_HEAD + " 1" + _EXPECTED_NOTE,
            [_COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})"],
            None,
            _DOCTOR_HEAD + " 1" + _STOPPED_NOTE,
            None,
        ),
        (  # a renderer with K24 already: left as it is
            "- doctor: 0 FAIL, 1 warn (42 checks) · expected 1: install.commit "
            f"({STOPPED_WARN}) · unexpected 0",
            _DOCTOR_HEAD + " 1" + _STOPPED_NOTE,
            [_COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})"],
            None,
            None,
            None,
        ),
        (  # another expected warn only: no stopped-copy reason, so no K24 words
            "- doctor: 0 FAIL, 1 warn (42 checks) · expected 1: launcher.signature (ad hoc launcher)",
            _DOCTOR_HEAD + " 1" + _EXPECTED_NOTE,
            ["[warn] launcher.signature — ad hoc (expected: ad hoc launcher)"],
            None,
            None,
            None,
        ),
        (  # another install.commit warn
            "- doctor: 0 FAIL, 1 warn (42 checks) · unexpected 1: install.commit warn",
            _DOCTOR_HEAD,
            ["[warn] install.commit — could not read the install stamp (fix: re-run install.sh)"],
            None,
            None,
            None,
        ),
    ],
)
def test_a_stopped_copy_rendered_before_j18_calls_its_commit_warn_expected(
    env: dict[str, str],
    tmp_path: Path,
    summary: str,
    head: str,
    fenced: list[str],
    want_summary: str | None,
    want_head: str | None,
    want_fenced: list[str] | None,
) -> None:
    """B13 round 9 K21: J18 is in the agentsync report only, and a stopped copy is rendered by the installed
    agentsync, which step 2 never updated: an older one still listed install.commit as unexpected, with
    doctor's "re-run install.sh" fix, under a NEXT that says to copy the prompt again. The installer, which
    runs from the current checkout, gives that report J18's words; any other wording is left as it is."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    old = tmp_path / "old-report.md"
    old.write_text(_old_doctor_report(summary, head, fenced))
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    lines = side.read_text().splitlines()
    doctor = lines[lines.index("## Doctor") :]
    got_fenced = doctor[doctor.index("~~~text") + 1 : doctor.index("~~~")]
    assert [ln for ln in lines if ln.startswith("- doctor: ")] == [want_summary or summary]
    assert doctor[2] == (want_head or head)
    assert got_fenced == (want_fenced or fenced)


_OLDER = f"older than the installer's v{COMPAT}: the pasted copy was not the current README"
_CHANGED = ", or the --log-start value was changed"


@pytest.mark.parametrize(
    ("line", "want"),
    [
        (f"- prompt: v{COMPAT - 1} ({_OLDER})", f"- prompt: v{COMPAT - 1} ({_OLDER}{_CHANGED})"),
        (
            f"- prompt: v{COMPAT + 1} (newer than the installer's v{COMPAT}: it follows main)",
            f"- prompt: v{COMPAT + 1} (newer than the installer's v{COMPAT}: it follows main{_CHANGED})",
        ),
        (
            f"- prompt: v{COMPAT + 1} (newer than the installer's v{COMPAT})",
            f"- prompt: v{COMPAT + 1} (newer than the installer's v{COMPAT}{_CHANGED})",
        ),
        (  # a renderer that joins its notes with "; "
            f"- prompt: v{COMPAT - 1} (from the step 1 line; {_OLDER})",
            f"- prompt: v{COMPAT - 1} (from the step 1 line; {_OLDER}{_CHANGED})",
        ),
        (f"- prompt: v{COMPAT - 1} ({_OLDER}{_CHANGED})", None),  # a renderer with K22 already
        ("- prompt: v7 or older (the installer stopped this copy: it named no version)", None),
    ],
)
def test_an_older_renderers_stopped_report_gives_both_reasons_and_whose_version(
    env: dict[str, str], tmp_path: Path, line: str, want: str | None
) -> None:
    """B13 round 10 L18 and L24: a stopped copy is rendered by the installed agentsync, which step 2 never
    updated. One older than K22 gave its Summary prompt line one reason for the stop, beside the stop line's
    two, and its Run metadata named its own prompt version beside the Summary's comparison with this
    installer's. The installer gives the prompt line the second reason, and says whose version the metadata
    names; a line in K22's words, the no-version form, a prompt line outside the Summary and a matching
    version are left."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    meta_old = f"- agentsync: 0.1.0, setup prompt v{COMPAT - 1} (python 3.11.9, /usr/bin/python3)"
    meta_same = f"- agentsync: 0.1.0, setup prompt v{COMPAT} (python 3.11.9, /usr/bin/python3)"
    old = tmp_path / "old-report.md"
    old.write_text(
        _old_doctor_report(line, _DOCTOR_HEAD, ["[warn] launchd.poll — x"]).replace(
            "## Redaction", f"### Run metadata\n\n{meta_old}\n{meta_same}\n{line}\n\n## Redaction"
        )
    )
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    lines = side.read_text().splitlines()
    meta = lines.index("### Run metadata")
    assert lines[lines.index("## Summary") + 2] == (want or line)
    assert lines[meta + 4] == line, "a prompt line outside the Summary is left as it is"
    assert lines[meta + 2 : meta + 4] == [
        f"- agentsync: 0.1.0, setup prompt v{COMPAT - 1} (the installed agentsync, which renders this "
        "report; the checkout's installer, which stopped this copy, is for setup prompt "
        f"v{COMPAT}, and the fresh copy's step 2 reinstalls agentsync) (python 3.11.9, /usr/bin/python3)",
        meta_same,
    ]


def test_a_stopped_lines_fix_names_the_remedy_of_both_readings(env: dict[str, str]) -> None:
    """B13 round 9 K18: the line --log-start writes for a copy it stops names two causes (an older or newer
    copy, or a changed --log-start value), but its fix column gave only the first one's remedy; the second was
    in the message to the agent only. Both remedies now, the first one's first, where the report reads it."""
    clause = SECOND_REMEDY
    for said, first in (
        (f"prompt v{COMPAT - 1}, Contoso Agent", "copy the prompt again from "),
        (f"prompt v{COMPAT + 1}, Contoso Agent", ""),  # newer_copy_fix's remedy, which this checkout decides
        ("Contoso Agent", "copy the prompt again from "),
    ):
        assert install_sh(env, "--log-start", said).returncode == 2
        [stop] = [ln for ln in friction_path(env).read_text().splitlines() if " | step 1 | error | " in ln][
            -1:
        ]
        fix = stop.split(" | ")[-1]
        assert fix.startswith(first) and fix.endswith(clause) and not fix.startswith("; or"), (said, fix)
        # the cause is in the error column, once: the fix column named it again (B13 round 10 L20)
        assert stop.count("the --log-start value was changed") == 1, stop


def test_a_stopped_copys_embed_counts_the_lines_it_leaves_out(env: dict[str, str], tmp_path: Path) -> None:
    """The earlier run's "bring back:" and issue link lines leave the install.out embed for one marker line,
    so its fence held 59 lines under "the last 60 line(s)" (B13 round 6 G16). The summary says how many are
    left out, so its count is the fence's install.out lines plus those; a summary in another wording, an
    older agentsync's, is left as it is."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    tail = [f"filler {n}" for n in range(56)] + [
        "report: ~/agent-context/setup-report.md",
        "bring back: ~/agent-context/bring-back.md (one file: report, fix request, local work)",
        f"issue link (review the report first): {setup_report.ISSUE_URL}&title=Contoso",
        "NEXT: stop: the operator confirms the baseline questions in Contoso Docs; session done",
    ]
    old = tmp_path / "old-report.md"
    old.write_text(_old_renderer_report(["- install.sh: exit 0 (finished)"], tail))
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    head, fenced = _embed(side)
    gone = "(an earlier run's bring-back and issue link lines are left out of this report)"
    found = re.fullmatch(
        r"<details><summary>the last (\d+) line\(s\) of \S+, (\d+) of them left out \(.*\)</summary>", head
    )
    assert found is not None, head
    assert (int(found[1]), int(found[2])) == (60, 2) and fenced.count(gone) == 1
    assert int(found[1]) == len(fenced) - 1 + int(found[2]), "the marker is no install.out line"
    _sends_nothing(side)
    old.write_text(
        _old_renderer_report(["- install.sh: exit 0 (finished)"], tail).replace(" line(s) of ", " lines of ")
    )
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    head, fenced = _embed(side)
    assert "left out" not in head and len(fenced) == 59, (
        "a summary that does not end as F20's is not rewritten"
    )


def test_the_real_report_of_a_stopped_copy_invites_no_bring_back(real_env: dict[str, str]) -> None:
    """The same with the real agentsync's setup-report, whose Redaction part ends on a "Send it:" line and a
    filled issue link (the rehearsal's file, B13 round 4 Q18); a later report that was not stopped keeps
    both and removes the stopped copy's (Q20)."""
    home = Path(real_env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(
        home / ".local" / "bin" / "agentsync",
        f'#!/bin/sh\nexec "{real_env["REAL_PYTHON"]}" -m agentsync "$@"\n',
    )
    side = report_path(real_env).parent / "setup" / "stopped-copy-report.md"
    # An earlier full install run's end, as install.out keeps it: the stopped copy's report quotes its tail
    # (B13 round 5 F16, F7: every field Mac has one). install.out never holds a --report-only run, so the NEXT
    # is step 2's own (B13 round 6 G14)
    out = side.parent / "install.out"
    out.parent.mkdir(parents=True, mode=0o700)
    out.write_text(
        '# run=20261012T090000Z-41 2026-10-12T09:00:00Z install.sh --source-local "Contoso Docs"\n'
        "report: ~/agent-context/setup-report.md\n"
        "bring back: ~/agent-context/bring-back.md (one file: report, fix request, local work)\n"
        f"issue link (review the report first): {setup_report.ISSUE_URL}&title=Contoso\n"
        "NEXT: stop: the operator confirms the baseline questions in Contoso Docs; session done "
        "[setup report: ~/agent-context/setup-report.md]\n"
    )
    out.chmod(0o600)
    assert install_sh(real_env, "--log-start", "x").returncode == 2
    stopped = install_sh(real_env, "--report-only")
    assert stopped.returncode == 0 and "(agentsync setup-report)" in stopped.stdout, stopped.stdout
    assert "## Redaction" in side.read_text()
    _sends_nothing(side)
    gone = "(an earlier run's bring-back and issue link lines are left out of this report)"
    lines = side.read_text().splitlines()
    assert lines.count(gone) == 1, side.read_text()
    assert lines[lines.index(gone) + 1].startswith("NEXT: stop: "), (
        "the earlier run's NEXT is kept, and the line above it does not say otherwise (B13 round 6 G14)"
    )
    head, fenced = _embed(side)
    found = re.search(r"the last (\d+) line\(s\) of \S+, (\d+) of them left out \(", head)
    assert found is not None and int(found[1]) == len(fenced) - 1 + int(found[2]) == 5, (head, fenced)
    _backdate(real_env, 11)
    assert install_sh(real_env, "--log", "1", "error", "git pull failed (exit 1)", "-").returncode == 0
    real = install_sh(real_env, "--report-only")
    assert real.returncode == 0 and not side.exists()
    text = report_path(real_env).read_text()
    assert "\nSend it: " in text and text.rstrip("\n").splitlines()[-1].startswith(setup_report.ISSUE_URL)
    assert "\nbring back: ~/agent-context/bring-back.md (one file:" in text and "&title=Contoso" in text, (
        "a report that was not stopped still quotes both lines"
    )
    assert gone not in text and "Read the report before sending it: a" in text


def test_a_stopped_copy_is_read_as_stopped_whatever_bytes_a_late_line_holds(env: dict[str, str]) -> None:
    """B13 H1 (skeptic C3): macOS awk in a UTF-8 locale aborts on a line that is not UTF-8 ("towc: multibyte
    conversion failure"), and the friction readers swallowed that, so a stopped copy with one such late line
    read as not stopped: its report wrote bring-back.md and the normal NEXT. They run under LC_ALL=C. The
    fixture's own locale is C, which is why no test saw it: this one runs in en_US.UTF-8."""
    assert install_sh(env, "--log-start", "x").returncode == 2
    with friction_path(env).open("ab") as f:
        f.write(b"2026-10-06T16:50:00Z | step 1 | deviation | ran it again \xff | -\n")
    cp = subprocess.run(
        [BASH32, str(INSTALL_SH), "--report-only"],
        capture_output=True,
        check=False,
        timeout=120,
        env={**env, "LC_ALL": "en_US.UTF-8"},
        stdin=subprocess.DEVNULL,
    )
    out = cp.stdout.decode("utf-8", errors="replace")
    assert cp.returncode == 0 and out.splitlines()[-1].startswith(STOPPED_NEXT), out
    assert not (report_path(env).parent / "bring-back.md").exists()


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
    contoso = stop.replace("Agent: x", "Agent: Contoso Agent")

    def late(what: str) -> str:
        return f"2026-10-06T16:44:00Z | step 1 | error | {what} | -\n"

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
        # B13 round 4 Q21: a line that states another version or agent is another session's, within the gap
        "a v12 paste two minutes after the stop": stop
        + end
        + "2026-10-06T16:44:00Z | step 1 | error | prompt v12, Claude Code: git pull exited 1 | -\n",
        "another agent's line, the version the same": stop
        + end
        + "2026-10-06T16:44:00Z | step 1 | error | 'prompt v7, Contoso Agent: git pull exited 1' | -\n",
        "the stopped session's own words": stop
        + end
        + "2026-10-06T16:44:00Z | step 1 | error | prompt v7, X: git pull exited 1 | -\n",
        # B13 round 5 F54: both read the line at ASCII, and both drop curly quotes and cap the agent
        "its own agent in curly quotes": contoso + end + late("prompt v7, \u201cContoso Agent\u201d: x"),
        "a v13 line in curly single quotes": stop + end + late("\u2018prompt v13, Contoso Agent: x\u2019"),
        "an accented capital in the header, small in the line": stop.replace("Agent: x", "Agent: \u00c9quipe")
        + end
        + late("prompt v7, \u00e9quipe: x"),
        "a no-break space in the agent": contoso + end + late("prompt v7, Contoso\u00a0Agent: x"),
        "an agent over 100 characters at v12": stop + end + late("prompt v12, " + "Contoso " * 13 + ": x"),
        "an agent of 100 accented characters at v12": stop
        + end
        + late("prompt v12, " + "\u00e9" * 100 + ": x"),
        "a version in Arabic-Indic digits": stop + end + late("prompt v\u0661\u0662, Contoso Agent: x"),
        "a no-break space after the header's agent": stop.replace("Agent: x", "Agent: Contoso Agent\u00a0")
        + end
        + late("prompt v7, Contoso Agent: x"),
        "a header version in Arabic-Indic digits": contoso.replace("Prompt: v7 or older", "Prompt: v\u0667")
        + end
        + late("prompt v12, Contoso Agent: x"),
    }
    answers = {}
    for name, text in logs.items():
        friction_path(env).parent.mkdir(parents=True, exist_ok=True)
        friction_path(env).write_text(text, encoding="utf-8")
        said = last_line(install_sh(env, "--report-only")).startswith(STOPPED_NEXT)
        assert said == _stopped_in_the_report(env), name
        answers[name] = said
    assert [name for name, said in answers.items() if not said] == [
        "a step 1 error a second past the gap",
        "a step 1 error the next day",
        "a step 1 error over a month end",
        "another session, then its own lines",
        "a current attempt after it",
        "a v12 paste two minutes after the stop",
        "another agent's line, the version the same",
        # B13 round 5 F23: after a header, a line that states its words is another session's, in any words
        "the stopped session's own words",
        "its own agent in curly quotes",
        "a v13 line in curly single quotes",
        "an accented capital in the header, small in the line",
        "a no-break space in the agent",
        "an agent of 100 accented characters at v12",
        "a no-break space after the header's agent",
        "a header version in Arabic-Indic digits",
    ]


def test_friction_options_touch_nothing_else(env: dict[str, str]) -> None:
    for argv in (["--log-start", started("Copilot CLI")], ["--log", "1", "click", "x", "-"]):
        assert install_sh(env, *argv).returncode == 0
    setup = friction_path(env).parent
    assert sorted(p.name for p in setup.iterdir()) == ["friction.md"], "no install.log, no install.out"
    assert not report_path(env).exists() and calls(env) == [], "no report, no uv, agentsync or launchctl"
    assert (setup.stat().st_mode & 0o777) == 0o700 and (setup.parent.stat().st_mode & 0o777) == 0o700
    assert (friction_path(env).stat().st_mode & 0o777) == 0o600


@pytest.mark.timing
def test_friction_options_are_fast(env: dict[str, str]) -> None:
    """The friction options do almost nothing: measured in the CPU time bash and its children use (B13 round
    2 R98), which a loaded Mac stretches less than the wall clock but still stretches: at 20 to 70 runnable
    processes per core it measured 0.27 to 0.43 s against the 0.2 s bound, so it is a ``timing`` test (B13
    round 4 Q59). That they start no uv, agentsync or launchctl is
    test_friction_options_touch_nothing_else's."""
    for argv in (["--log-start", started("a")], ["--log", "2", "approval", "x", "-"]):
        best = min(_cpu(env, argv) for _ in range(3))
        assert best < 0.2, f"{argv[0]} used {best:.3f}s of CPU"


def _cpu(env: dict[str, str], argv: list[str]) -> float:
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    assert install_sh(env, *argv).returncode == 0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)


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


def test_every_friction_time_is_the_second_of_the_call_that_wrote_it(env: dict[str, str]) -> None:
    """N40: the header, each --log line and the end line carry the UTC second of the install.sh call that
    wrote them, not a time the agent gives (setup-feedback.md section 1 says so)."""
    calls = [
        ("--log-start", started("Contoso Agent")),
        ("--log", "1", "question", "a", "-"),
        ("--log", "2", "prompt", "b", "c"),
        ("--report-only",),
    ]
    windows = []
    local = {**env, "TZ": "EST5"}  # a Mac's own zone is not UTC: the stamp must still be
    for argv in calls:
        before = datetime.now(UTC).replace(microsecond=0)
        cp = install_sh(local, *argv)
        assert cp.returncode == 0, cp.stderr
        windows.append((before, datetime.now(UTC)))
    lines = friction_path(env).read_text().splitlines()
    stamped = [lines[0].removeprefix("Attempt: "), *(line.split(" | ", 1)[0] for line in lines[3:])]
    assert len(stamped) == len(calls), lines
    for text, (before, after) in zip(stamped, windows, strict=True):
        at = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert before <= at <= after, (text, before, after)


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
        f"{STOPPED_NEXT}, so do not "
        f'send it or bring it back: copy the prompt again from {PROMPT_SOURCE} ("Set up on a new Mac: one '
        f'prompt"; this installer is for setup prompt v{COMPAT}) and paste it into a new session '
        "[setup report: "
    )
    assert sum(ln.endswith(" | end | finished") for ln in friction_path(env).read_text().splitlines()) == 1
    # The report is written beside the friction log, not over setup-report.md (B13 round 2 R17).
    assert (friction_path(env).parent / "stopped-copy-report.md").is_file() and not report_path(env).exists()


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


@pytest.mark.parametrize("said", ["now in the count above", "so also counted as converted"])  # round 10 L16
def test_first_sync_note_reads_the_read_again_count_past_the_first_read_bracket(
    env: dict[str, str], folder: Path, wheel: Path, said: str
) -> None:
    """B13 round 9 K34 reworded the first-read bracket after ``, read again R``: the note's awk adds the
    number after each bare ``converted``, ``deferred`` and ``read again``, so the new words, with the split
    after them, still log ``-reread-R`` and nothing else."""
    line = (
        "converted 3, deferred 0 online-only, read again 2 (2 of them a first read: a stub whose bytes had "
        f"never been read, {said}); of the converted: 1 unreadable, 0 failed, 0 refused"
    )
    cp = install_sh({**env, "STUB_SYNC_OUT": line}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"first sync: {line}" in cp.stdout.splitlines()
    assert ("first-sync", "done", "0", "converted-3-deferred-0-reread-2") in steps(install_log(env))


def test_first_sync_note_reads_the_count_after_the_first_read_head(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 10 L34: when every file read again was a first read, the sync's head is ``, first read R``,
    with no "read again"; the note's awk adds the number after "first read" too, so it still logs
    ``-reread-R`` and the setup report reads the same note."""
    line = (
        "converted 3, deferred 0 online-only, first read 2 (stubs whose bytes had never been read, so also "
        "counted as converted); of the converted: 1 unreadable, 0 failed, 0 refused"  # round 10 L16's words
    )
    cp = install_sh({**env, "STUB_SYNC_OUT": line}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"first sync: {line}" in cp.stdout.splitlines()
    assert ("first-sync", "done", "0", "converted-3-deferred-0-reread-2") in steps(install_log(env))


# ---- the on-device OCR helper is built in the helpers step -------------------------------------------------

STUB_TOOL_PYTHON = """#!/bin/bash
case "$*" in  # the lock path step 2 asks for, unlogged: the other tests' python call counts stay as they were
*state_paths.lock*" ${STUB_JOB_CONFIG:-/none}")  # a job's own config names its own lock
	[ -z "${STUB_JOB_LOCK:-}" ] || printf '%s\\n' "$STUB_JOB_LOCK"; exit 0 ;;
*state_paths.lock*) [ -z "${STUB_SYNC_LOCK:-}" ] || printf '%s\\n' "$STUB_SYNC_LOCK"; exit 0 ;;
esac
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

    placed = (
        "speech: off (the speech helper is built; the speech models are not placed: parakeet-tdt-0.6b-v3/ "
        "and speaker-diarization/)"
    )
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
    placed = (
        "speech: off (the speech helper is built; the speech models are not placed: parakeet-tdt-0.6b-v3/ "
        "and speaker-diarization/)"
    )
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
def test_helpers_step_is_skipped_when_the_config_cannot_be_read(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 11 M25: with a sources.toml it could not read, each helper printed ``not built (the config
    cannot be read)`` and install.log said ``helpers ... done``.  The OCR module exits 78 (EX_CONFIG) then;
    the media and speech modules read the same config, so they are not run, their lines say why, and the step
    is skipped, note config-unreadable.  Exit 1, any other helper failure, is still done."""
    tool_py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    unread = "OCR helper: not built (the config cannot be read)"
    cp = install_sh(
        {**env, "STUB_OCR_OUT": unread, "STUB_OCR_RC": "78"}, str(wheel), "--source-local", str(folder)
    )
    assert _helpers_row(steps(install_log(env))) == ("helpers", "skipped", "0", "config-unreadable")
    out = cp.stdout.splitlines()
    at = out.index(unread)
    assert out[at + 1 : at + 3] == [
        "media helper: not built (the config cannot be read)",
        "speech: not built (the config cannot be read)",
    ], cp.stdout
    ran = [c for c in calls(env) if c.startswith("python -I -m agentsync.convert.")]
    assert [c.split(" config=")[0] for c in ran] == ["python -I -m agentsync.convert.ocr"], ran


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


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (the helpers are built only with them)")
def test_a_run_killed_during_the_helper_builds_reads_as_killed(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """H14: no trap sees a SIGKILL, so the run keeps the step lines that had ended and has no end line; the
    helpers step under way has none. The report reads the run id's pid (install.sh's $$) and says the
    process is gone instead of "interrupted, or still running"."""
    tool_py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    e = {**env, "AGENTSYNC_PROGRESS_SECONDS": "2", "STUB_SPEECH_SLEEP": "3"}
    proc = subprocess.Popen(
        [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder)],
        env=e,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not any(c.startswith("python -I -m agentsync.convert.speech") for c in calls(env)):
            assert proc.poll() is None and time.monotonic() < deadline, "the speech build never started"
            time.sleep(0.05)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)  # reaps the pid: an unreaped zombie still exists and reads as "in use"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    lines = install_log(env)
    names = [s[0] for s in steps(lines)]
    assert "launcher" in names and "helpers" not in names, lines
    assert not any(" end rc=" in ln for ln in lines), lines
    (run,) = setup_report.read_install_runs(install_log_path(env))
    assert run.run_id.endswith(f"-{proc.pid}"), "the run id carries the pid a SIGKILL ends"
    assert setup_report._open_state(run) == (
        "its process is gone: killed, the Mac restarted, or its end line could not be written"
    )


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
    legend = text.split("\n## Redaction\n", 1)[1]
    assert "\n- ~: the home path\n- <tmp>: this account's temporary folder ($TMPDIR)\n" in legend, (
        "the legend names the placeholder its redaction writes, the $ kept literal (B13 round 4 Q40)"
    )


# ---- the shell report's MDM host has its own placeholder (N39) ---------------------------------------------

MDM_URL = "https://contosoltd.mdm.example:443/mdm/ServerURL"
SHELL_REDACTION_FNS = re.compile(r"(?ms)^(?:redaction_pairs|redact_stream)\(\) \{\n.*?^\}$")


@pytest.mark.parametrize(
    ("login", "path"), [("jdoe", "/mdm/ServerURL"), ("mdm", "/<user>/ServerURL"), ("host", "/mdm/ServerURL")]
)
def test_the_shell_report_redacts_the_mdm_host_as_mdmhost(tmp_path: Path, login: str, path: str) -> None:
    """The shell fallback registers the MDM server's host as <mdmhost>, the Python report's kind, not the
    Mac's <host>; one word, so a login named mdm or host (a later, whole-word pair) cannot match inside it.
    `/usr/bin/profiles` is an absolute path no PATH stub reaches, so the real redaction_pairs and
    redact_stream run with with_timeout answering for it, and id answering for the login and full name."""
    funcs = "\n".join(m.group(0) for m in SHELL_REDACTION_FNS.finditer(INSTALL_SH.read_text()))
    assert funcs.startswith("redaction_pairs() {") and "\nredact_stream() {" in funcs
    harness = "\n".join(
        [
            "with_timeout() {",
            "\tshift",
            '\t[ "$1" = /usr/bin/profiles ] || return 0',
            f"\tprintf 'MDM enrollment: Yes\\nMDM server: %s\\n' '{MDM_URL}'",
            "}",
            f'id() {{ case "$1" in -un) echo {login} ;; -F) echo "Jane Doe" ;; esac; }}',
            "FOLDERS=() CONFIG=/nonexistent SETUP_LOG=/nonexistent",
            funcs,
            f"printf '%s\\n' 'MDM server: {MDM_URL}' | redact_stream",
        ]
    )
    home = tmp_path / "home"
    home.mkdir()
    cp = subprocess.run(
        [BASH32, "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
    )
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == f"MDM server: https://<mdmhost>:443{path}\n"


# ---- the shell report redacts a value in either Unicode form (N7) ------------------------------------------


@pytest.mark.parametrize("locale", ["en_US.UTF-8", "C"])
@pytest.mark.parametrize(("on_disk", "typed"), [("NFD", "NFC"), ("NFC", "NFD")])
def test_the_shell_report_redacts_a_folder_in_either_unicode_form(
    env: dict[str, str], on_disk: str, typed: str, locale: str
) -> None:
    """A Finder-made name is NFD on disk and an agent types it NFC, or the reverse: the shell fallback
    registers both forms, as the Python report does (rehearsal 2026-10-10: "Café Plans" in clear)."""
    home = Path(env["HOME"])
    cafe = (
        home / "Library" / "CloudStorage" / "OneDrive-Contoso" / unicodedata.normalize(on_disk, "Café Plans")
    )
    cafe.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text(f'[[source]]\nid = "cafe"\nkind = "local"\npath = "{cafe}"\n', encoding="utf-8")
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir()
    said = unicodedata.normalize(typed, "Café Plans")
    friction.write_text(f"Prompt: v10\nF1 | step 1 | clean | 1 | picked {said} | -\n", encoding="utf-8")
    cp = install_sh({**env, "LC_ALL": locale}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert "(shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_text(encoding="utf-8")
    assert "picked <folder-1>" in text.split("\n## Agent friction log\n", 1)[1]
    for form in ("NFC", "NFD"):
        assert unicodedata.normalize(form, "Café") not in text, form


# ---- the shell report redacts the PAC URL and every proxy host (H19) ---------------------------------------


def test_the_shell_report_redacts_the_pac_url_and_proxy_hosts(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """status's network.proxy lines name the system PAC URL and the proxy host; a cloud proxy's PAC path can
    carry the customer's domain, and a git error names git's proxy. The shell fallback redacts the PAC URL
    after its scheme, its host and every proxy host with a dot (scutil, the environment, [network] proxy and
    git's config files, read as files), as the Python report does (B13 H19)."""
    pac = "https://pac.cloudproxy.example/fabrikam.com/proxy.pac"
    stubbin = Path(env["PATH"].split(":")[0])
    _write_exe(
        stubbin / "scutil",
        '#!/bin/bash\n[ "$1" = --proxy ] || exit 1\n'
        "printf '<dictionary> {\\n  HTTPSProxy : proxy\\n  ProxyAutoConfigEnable : 1\\n'\n"
        f"printf '  ProxyAutoConfigURLString : {pac}\\n}}\\n'\n",
    )
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text(
        '[http]\n\tProxy = "http://gitproxy.fabrikam.example:3128" ; IT\n\tproxy =\n'
    )
    # The checkout the prompt clones: setup_report._git_proxy_urls reads its .git/config too (B13 H1).
    clone = home / "src" / "agent-context-sync" / ".git"
    clone.mkdir(parents=True)
    (clone / "config").write_text(
        '[http "https://github.com/"]\n\tproxy = clone.fabrikam.example:8080\n[user]\n'
    )
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text('[network]\nproxy = "cfgproxy.fabrikam.example" # set by "IT"\n')
    setup = home / "agent-context" / "setup"
    setup.mkdir()
    (setup / "friction.md").write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v10\n"
        f"2026-10-10T00:00:01Z | step 1 | error | PAC {pac.split('://', 1)[1]} "
        "on host pac.cloudproxy.example | -\n"
        "2026-10-10T00:00:02Z | step 1 | error | Could not resolve proxy: gitproxy.fabrikam.example | -\n"
        "2026-10-10T00:00:03Z | step 1 | error | the config names cfgproxy.fabrikam.example | -\n"
        "2026-10-10T00:00:04Z | step 1 | error | git pull: Could not resolve proxy: clone.fabrikam.example"
        " | -\n"
    )
    lines = (
        f"[info] network.proxy — system proxy uses PAC {pac}; PAC/WPAD is unsupported\n"
        "[info] network.proxy.job — the LaunchAgent resolves http://proxy.fabrikam.example:8080 (source: env)"
    )
    e = {
        **env,
        "STUB_DOCTOR_OUT": lines,
        "STUB_REPORT_RC": "2",
        "HTTPS_PROXY": "http://user:pw@proxy.fabrikam.example:8080/a@b",
    }
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert "shell fallback" in cp.stdout, cp.stdout
    text = report_path(env).read_text()
    assert "system proxy uses PAC https://<proxy-" in text and "resolves http://<proxy-" in text, text
    assert re.search(r"\| PAC <proxy-\d+> on host <proxy-\d+> \|", text), text
    assert re.search(r"\| Could not resolve proxy: <proxy-\d+> \|", text), text
    assert re.search(r"\| the config names <proxy-\d+> \|", text), text
    assert re.search(r"\| git pull: Could not resolve proxy: <proxy-\d+> \|", text), text
    assert "fabrikam" not in text.lower() and "cloudproxy" not in text.lower(), text
    assert "- proxy environment: yes" in text, "a host with no dot is not registered"


@pytest.mark.parametrize("org", ["context", "agent"])
def test_the_shell_report_keeps_agentsyncs_own_names_for_an_organisation_named_like_them(
    env: dict[str, str], org: str
) -> None:
    """B13 round 8 J71 (J45 is the same defect), the shell twin of H74: a one-word organisation named
    ``context`` or ``agent`` is replaced everywhere, and cut ``~/agent-<org-1>/setup``, ``~/<org-1>-context``,
    ``<org-1>sync is not installed`` and ``com.<org-1>sync.poll``, which the Python report keeps whole. The
    shell now holds those fixed names, as it holds the repository locator, except where a longer pair covers
    them: a folder named ``agent-context Fabrikam`` or ``agentsync Fabrikam`` is still replaced whole, not cut
    after its first word with the rest in clear. The report stays private only, as before."""
    provider = _cloud(env) / f"OneDrive-{org}"
    own = [provider / "agent-context Fabrikam", provider / "agentsync Fabrikam"]
    for d in own:
        d.mkdir(parents=True)
    args = [a for d in own for a in ("--source-local", str(d))]
    assert install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, *args).returncode == 1
    text = report_path(env).read_text()
    assert "Fabrikam" not in text, [ln for ln in text.splitlines() if "Fabrikam" in ln]
    cut = re.findall(r".*(?:agent-<org-\d+>|<org-\d+>-context|<org-\d+>sync).*", text)
    assert not cut, cut
    assert "~/agent-context/setup/install.log" in text and "com.agentsync.poll" in text
    assert "agentsync is not installed" in text and "OneDrive-<org-1>" in text
    assert "Private only:" in text


def test_a_folder_named_agentsync_is_redacted_in_the_shell_report(env: dict[str, str]) -> None:
    """A pair of the held word itself, a folder named ``agentsync``, is not held off: the folder is
    ``<folder-N>``, as the Python report has it (B13 round 8 J71)."""
    named = _cloud(env) / "OneDrive-Contoso" / "agentsync"
    named.mkdir(parents=True)
    assert install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(named)).returncode == 1
    text = report_path(env).read_text()
    assert "OneDrive-<org-1>/agentsync" not in text and re.search(r"OneDrive-<org-1>/<folder-\d+>", text), (
        text
    )


def test_the_shell_report_redacts_a_git_url_rewrite_host_and_path(env: dict[str, str]) -> None:
    """B13 round 7 H20, the shell twin of the Python report's: v15's git stop names a URL rewrite as a cause,
    and git's error names the rewrite's host. The bases of ``[url "<base>"] insteadOf`` are read as files (no
    git process), an https base and an scp base by host and by base, never GitHub's own host; the
    Environment part counts them."""
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text(
        '[url "https://jdoe@mirror.northwind.invalid:8443/northwind-team/"]\n'
        "\tinsteadOf = https://github.com/\n"
        '[url "git@scp.northwind-two.invalid:northwind-scp/"]\n'
        "\tInsteadOf = https://github.com/someone/\n"
        '[url "git@github.com:"]\n'
        "\tinsteadOf = https://github.com/\n"
        '[url "https://push.northwind-three.invalid/"]\n'
        "\tpushInsteadOf = https://github.com/\n"
        '[URL "https://Mirror.Northwind-Four.invalid/git"]\n'  # a generic path, a host in mixed case
        "insteadof=https://github.com/x/\n"
        '[url "https://mirror.northwind-five.invalid/docs/"]\n'  # a report word: no value (round 8 J20)
        "\tinsteadOf = https://github.com/y/\n"
        # B13 round 9 K46: a base that ends at a repository name; K77 (round 10 L69): a path of generic words
        '[url "https://mirror.northwind-six.invalid/northwind-mirror"]\n\tinsteadOf = https://github.com/z/\n'
        '[url "https://dev.example.invalid/gh/"]\n\tinsteadOf = https://github.com/w/\n'
    )
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)
    (setup / "friction.md").write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n"
        "2026-10-10T00:00:01Z | step 1 | error | git clone https://mirror.northwind.invalid:8443/northwind-team/"
        "someone/agent-context-sync.git/: Could not resolve host: mirror.northwind.invalid | -\n"
        "2026-10-10T00:00:02Z | step 1 | error | git pull git@scp.northwind-two.invalid:northwind-scp/x.git: "
        "Could not resolve hostname scp.northwind-two.invalid | -\n"
        "2026-10-10T00:00:03Z | step 1 | error | Could not resolve host: mirror.northwind-four.invalid; "
        "/usr/bin/git and https://github.com/astral-sh/uv stay | -\n"
        # B13 round 8 J37: the scp path after another host; its pair was a whole word, which a host's last
        # letter before the ":" always refused
        "2026-10-10T00:00:04Z | step 1 | error | git pull "
        "git@mirror2.example.invalid:northwind-scp/x.git | -\n"
        "2026-10-10T00:00:05Z | step 1 | error | saw git@mirror3.example.invalid:northwind-scp.git and "
        "x:northwind-scp-tools | -\n"
        "2026-10-10T00:00:04Z | step 1 | deviation | read ~/agent-context/docs/index.md | -\n"
        "2026-10-10T00:00:06Z | step 1 | error | saw "
        "https://mirror.northwind-six.invalid/northwind-mirror.git and .../northwind-mirror-tools and "
        "/northwind-mirror_x | -\n"
        "2026-10-10T00:00:07Z | step 1 | error | https://dev.example.invalid/gh/tools.git/; "
        "/opt/homebrew/bin/gh auth status; ~/.config/gh/hosts.yml; gh run list; high; /gh/x | -\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert "northwind" not in text.lower(), [ln for ln in text.splitlines() if "northwind" in ln.lower()]
    # B13 round 8 J1, J20: the host is one placeholder in the URL and in git's resolver error, numbered as
    # the Python report numbers it, so both write this line the same way
    assert (
        "git clone https://<proxy-1>:8443/<proxy-2>/someone/agent-context-sync.git/: Could not resolve host: "
        "<proxy-1> |" in text
    ), text
    assert re.search(r"git pull git@<proxy-\d+>/x\.git: Could not resolve hostname <proxy-\d+> \|", text), (
        text
    )
    assert "/usr/bin/git and https://github.com/astral-sh/uv stay" in text, "a generic path is no value"
    assert re.search(r"git pull git@mirror2\.example\.invalid:<proxy-\d+>/x\.git \|", text), text
    assert re.search(
        r"saw git@mirror3\.example\.invalid:<proxy-\d+>\.git and x:<proxy-\d+>-tools \|", text
    ), text
    assert "read ~/agent-context/docs/index.md |" in text and "agent-context<proxy" not in text, (
        "a report-word path is no value"
    )
    # B13 round 9 K46: .git, -tools and _x may follow a rewrite path; K77, built in round 10 as L69: gh is a
    # generic path word, so a /gh/ path is no value and a local tool path stays in clear
    assert re.search(
        r"saw https://<proxy-(\d+)>/<proxy-(\d+)>\.git and \.\.\./<proxy-\2>-tools and /<proxy-\2>_x \|", text
    ), text
    assert re.search(
        r"\| https://<proxy-\d+>/gh/tools\.git/; /opt/homebrew/bin/gh auth status; "
        r"~/\.config/gh/hosts\.yml; gh run list; high; /gh/x \|",
        text,
    ), text
    assert "- URL rewrite in git's config: set (7)\n" in text
    (home / ".gitconfig").unlink()
    assert install_sh(env, "--report-only").returncode == 0
    assert "- URL rewrite in git's config: not set\n" in report_path(env).read_text()


def test_a_rewrite_paths_placeholder_keeps_its_separator_in_both_reports(env: dict[str, str]) -> None:
    """B13 round 9 K2, K27: a rewrite path was matched with its leading "/" or ":" and replaced with it, so
    host and path ran together (``https://<proxy-1><proxy-2>/...``). The separator is still matched, which
    anchors the start and keeps a two-letter path over the shell's 3-character floor, and is written back
    before the placeholder, in both reports alike."""
    home = Path(env["HOME"])
    # a two-letter path that is no generic word (gh is one since B13 round 10 L69), over the shell's floor
    bases = ("https://mirror.contoso-git.invalid/cx/", "git@scp.contoso-two.invalid:contoso-scp/")
    (home / ".gitconfig").write_text(
        "".join(f'[url "{b}"]\n\tinsteadOf = https://github.com/\n' for b in bases)
    )
    said = (
        "git clone https://mirror.contoso-git.invalid/cx/example/agent-context-sync.git/: Could not resolve "
        "host: mirror.contoso-git.invalid; git pull git@scp.contoso-two.invalid:contoso-scp/x.git; "
        "a high mark"
    )
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    line = next(ln for ln in report_path(env).read_text().splitlines() if "| step 1 | error |" in ln)
    shown = line.split(" | ")[3]
    assert shown == (
        "git clone https://<proxy-1>/<proxy-2>/example/agent-context-sync.git/: Could not resolve host: "
        "<proxy-1>; git pull git@<proxy-4>/x.git; a high mark"
    ), shown
    # the agentsync report registers the same values in the same order and writes the same URL
    red = setup_report.Redactor()
    for base in bases:
        found = setup_report._rewrite_values(base)
        assert found is not None
        host, value, tail = found
        setup_report._add_proxy_host(red, host)
        if value.lower() != host.lower():
            red.add("proxy", value, ignore_case=True)
        if tail:
            red.add("proxy", tail, ignore_case=True, keep_lead=True)
    assert red.redact(said.split("; git pull", maxsplit=1)[0]) == shown.split("; git pull", maxsplit=1)[0]
    assert red.redact("git pull git@scp.contoso-two.invalid:contoso-scp/x.git") == (
        "git pull <email-1>:<proxy-5>/x.git"
    ), "git@host is an email address first; the scp path keeps its colon"
    assert red.redact("a high mark; x/cxs; /CX/y") == "a high mark; x/cxs; /<proxy-2>/y"


def test_a_rewrite_path_of_tool_words_is_no_value_in_both_reports(env: dict[str, str]) -> None:
    """B13 round 10 L69 (round 9 K77 built as a word list): a ``/gh/`` rewrite path redacted the tool path
    ``/opt/homebrew/bin/gh``. Tool and host-kind words are generic path words in both readers, one list; a
    path of them alone registers nothing, and a path with one company word is still registered and
    redacted."""
    from agentsync import setup_report  # noqa: PLC0415

    listed = re.search(
        r'^\s*b=" ([^"]*) " # setup_report\._GENERIC_PATH_WORDS$', INSTALL_SH.read_text(), re.M
    )
    assert listed is not None and set(listed.group(1).split()) == setup_report._GENERIC_PATH_WORDS
    assert "nexus" not in setup_report._GENERIC_PATH_WORDS, "a common company-name word"
    for base, tail in (
        ("https://a.contoso-git.invalid/gh/", ""),
        ("https://b.contoso-git.invalid/homebrew/", ""),
        ("https://c.contoso-git.invalid/contoso-eng/gh/", "/contoso-eng/gh"),
        ("https://d.contoso-git.invalid/gh/contoso/", "/gh/contoso"),
    ):
        found = setup_report._rewrite_values(base)
        assert found is not None and found[2] == tail, (base, found)
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text(
        '[url "https://a.contoso-git.invalid/gh/"]\n\tinsteadOf = https://github.com/a/\n'
        '[url "https://c.contoso-git.invalid/contoso-eng/gh/"]\n\tinsteadOf = https://github.com/c/\n'
    )
    said = "/opt/homebrew/bin/gh auth status; ~/.config/gh/hosts.yml; https://c.contoso-git.invalid/contoso-eng/gh/x"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "contoso" not in text.lower(), [ln for ln in text.splitlines() if "contoso" in ln.lower()]
    assert re.search(
        r"/opt/homebrew/bin/gh auth status; ~/\.config/gh/hosts\.yml; https://<proxy-\d+>/<proxy-\d+>/x \|",
        text,
    ), text


SCUTIL_PROXY = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
  }
  HTTPEnable : 0
  HTTPPort : 8080
  HTTPProxy : off.contoso.example
  HTTPSEnable : 1
  HTTPSPort : 8443
  HTTPSProxy : sysproxy.contoso.example
  ProxyAutoConfigEnable : 1
  ProxyAutoConfigURLString : http://pac.contoso.example/contoso-pac/proxy.pac
  __SCOPED__ : <dictionary> {
    en0 : <dictionary> {
      ProxyAutoConfigURLString : http://scoped.contoso.example/scoped.pac
    }
  }
}
"""
"""A system proxy as ``scutil --proxy`` prints it: an HTTPS proxy that is on, an HTTP one that is off, and a
PAC URL with a path."""


def test_the_two_reports_number_each_proxy_value_alike(
    env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 8 J78: the shell numbered a PAC or rewrite value before its host, read scutil before the
    environment, took HTTP before HTTPS and a proxy whose Enable flag is 0, and counted case variants apart,
    so one Mac's two reports gave 3 of 4 values different ``<proxy-N>``. It now registers in the Python
    report's order, one number for a value in any case, and a disabled system proxy last, still redacted."""
    home = Path(env["HOME"])
    _write_exe(Path(env["PATH"].split(":")[0]) / "scutil", f"#!/bin/bash\ncat <<'EOF'\n{SCUTIL_PROXY}EOF\n")
    cfg = _write_config(env, _inbox(env))
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + '\n[network]\nproxy = "http://cfgproxy.contoso.example:3128"\n',
        encoding="utf-8",
    )
    (home / ".gitconfig").write_text(
        "[http]\n\tproxy = http://gitproxy.contoso.example:8080\n"
        '[url "https://mirror.contoso.example/contoso-team/"]\n\tinsteadOf = https://github.com/\n'
        '[url "git@scp.contoso.example:contoso-scp/"]\n\tinsteadOf = https://github.com/someone/\n'
    )
    quoted = [
        "egress.contoso.example",  # HTTPS_PROXY
        "EGRESS.contoso.example",  # https_proxy, the same host in another case
        "sysproxy.contoso.example",
        "pac.contoso.example",
        "pac.contoso.example/contoso-pac/proxy.pac",
        "cfgproxy.contoso.example",
        "gitproxy.contoso.example",
        "mirror.contoso.example",
        "mirror.contoso.example/contoso-team",
        "scp.contoso.example",
        "x:contoso-scp/y",
        "off.contoso.example",
        "scoped.contoso.example/scoped.pac",
    ]
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    line = "2026-10-10T00:00:01Z | step 1 | error | saw " + " ; ".join(quoted) + " | -"
    (setup / "friction.md").write_text(f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n{line}\n")
    proxied = {
        **env,
        "HTTPS_PROXY": "http://egress.contoso.example:8080",
        "https_proxy": "http://EGRESS.contoso.example:8080",
        "ALL_PROXY": "direct",  # names no proxy: no number (B13 round 8 J78)
    }
    assert install_sh(proxied, "--report-only").returncode == 0
    [shell] = [ln for ln in report_path(env).read_text().splitlines() if "| step 1 | error | saw " in ln]

    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "HOME"):
        monkeypatch.setenv(key, proxied[key])
    for key in ("all_proxy", "HTTP_PROXY", "http_proxy", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(net, "system_proxy", lambda: net.parse_scutil_proxy(SCUTIL_PROXY))
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    python = red.redact(line)

    def numbers(text: str) -> list[str]:
        return re.findall(r"<proxy-\d+>", text.split(" | error | saw ", 1)[1])

    assert numbers(python) == numbers(shell)[:-2], (shell, python)
    assert numbers(shell)[:4] == ["<proxy-1>", "<proxy-1>", "<proxy-2>", "<proxy-3>"], shell
    for kept in ("off.contoso.example", "scoped.contoso.example"):  # Python never reads them
        assert kept in python and kept not in shell, ("the shell keeps it redacted", kept)
    shared = max(int(n[7:-1]) for n in numbers(python))
    assert min(int(n[7:-1]) for n in numbers(shell)[-2:]) > shared, "numbered after every shared value"


def _fuzzy_report(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, folders: list[str], said: str, org: str = "Contoso"
) -> tuple[str, str]:
    """The friction line ``said`` as the shell report and the Python report redact it, with ``folders``
    configured under OneDrive-<org> (B13 round 10 L66)."""
    lib = _cloud(env) / f"OneDrive-{org}"
    tables = []
    for n, name in enumerate(folders):
        (lib / name).mkdir(parents=True)
        tables.append(local_source_table(f"s{n}", lib / name))
    cfg = _write_config(env, *tables)
    line = f"2026-10-10T00:00:01Z | step 1 | deviation | saw {said} | -"
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n{line}\n")
    assert install_sh(env, "--report-only").returncode == 0
    [shell] = [ln for ln in report_path(env).read_text().splitlines() if " | step 1 | deviation | saw " in ln]
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    return shell.split(" | saw ", 1)[1].removesuffix(" | -"), red.redact(line).split(" | saw ", 1)[
        1
    ].removesuffix(" | -")


def test_the_shell_report_redacts_every_spelling_of_a_folder_or_organisation(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 10 L66: the shell matched a folder or organisation byte for byte, so "kestrel-ridge",
    "KESTREL RIDGE" or "contoso" printed in clear beside "<folder-2>", with no Private only line, where the
    Python report's fuzzy mode redacts every case and separator spelling, joined ones too. It matches them as
    that mode does, with the same numbers (and K4's test (b): a spelling only the friction log holds)."""
    spellings = (
        "Kestrel Ridge, kestrel-ridge, kestrel ridge, KESTREL RIDGE, Kestrel_Ridge, KestrelRidge, "
        "kestrelridge; "
        "quillon works, QUILLON WORKS, Quillon_Works, quillon-works; Contoso, contoso, CONTOSO; q3-plans"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Quillon Works", "Kestrel Ridge", "Q3 Plans"], spellings)
    assert shell == python, (shell, python)
    assert shell == (
        "<folder-2>, <folder-2>, <folder-2>, <folder-2>, <folder-2>, <folder-2>, <folder-2>; <folder-1>, "
        "<folder-1>, <folder-1>, <folder-1>; <org-1>, <org-1>, <org-1>; <folder-3>"
    ), shell


@pytest.mark.parametrize(
    ("folder", "said", "want"),
    [
        # a short name matches as a word only, as Python's lead and trail: never inside "stops" or "drops"
        (
            "Ops",
            "the job stops and drops; OPS ran; ops ran",
            "the job stops and drops; <folder-1> ran; <folder-1> ran",
        ),
        # a name whose words run together spell a report word: with a separator or a CamelCase boundary only
        (
            "Ho Me",
            "home, HOME, Home, ho-me, Ho Me, HoMe",
            "home, HOME, Home, <folder-1>, <folder-1>, <folder-1>",
        ),
    ],
)
def test_the_shell_reports_fuzzy_match_keeps_words_whole(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, folder: str, said: str, want: str
) -> None:
    """B13 round 10 L66: the fuzzy match is bounded as the Python report's is, so a case-folded pair does not
    garble the report's words (from which the name could be read back)."""
    shell, python = _fuzzy_report(env, monkeypatch, [folder], said)
    assert shell == want and python == want, (shell, python)


@pytest.mark.parametrize(
    ("folders", "said"),
    [
        (["CaféPlans"], "Café Plans and café-plans"),  # a CamelCase split after a letter outside ASCII
        (["agent-context Fabrikam"], "~/agent-context fabrikam/x and agentsync"),  # longer than the held name
    ],
)
def test_the_shell_reports_fuzzy_match_is_the_python_reports_past_ascii_and_held_names(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, folders: list[str], said: str
) -> None:
    """Review of B13 round 10 L66: the CamelCase split read only an ASCII letter before a capital, and the
    test that holds agentsync's own names whole compared a fuzzy pair byte for byte, so "café-plans" and
    "fabrikam" printed in clear where the Python report redacts them."""
    shell, python = _fuzzy_report(env, monkeypatch, folders, said)
    assert shell == python and "<folder-1>" in shell and "fabrikam" not in shell.lower(), (shell, python)


def test_another_spelling_of_an_organisation_shares_its_number_and_is_redacted(env: dict[str, str]) -> None:
    """Review of B13 round 10 L66: an organisation spelled with an accent or other separators in a second
    CloudStorage folder took the first one's number and no pair, and the first one's pattern does not match
    it, so it printed in clear. Each spelling keeps a pair, under the one number."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage"
    for name in ("Box-Societe", "OneDrive-Soci\u00e9t\u00e9", "Dropbox-Contoso", "GoogleDrive-Con-Toso"):
        (cloud / name).mkdir(parents=True)
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(
        "Prompt: v4\nF1 | step 1 | clean | 1 | OneDrive-Soci\u00e9t\u00e9, Societe, Con-Toso, Contoso | -\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "| OneDrive-<org-1>, <org-1>, <org-2>, <org-2> |" in text, text
    assert "<org-3>" not in text


def test_a_context_organisation_keeps_the_prose_word(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 10 L66: an organisation that is a word the report prints ("Link") keeps its context pairs
    (B13 round 6 G46): no fuzzy match, so the word "link" in prose stays."""
    shell, _python = _fuzzy_report(
        env, monkeypatch, ["Plans"], "the link to OneDrive-Link and LINK", org="Link"
    )
    assert shell.startswith("the link to OneDrive-<org-1>"), shell


def test_the_shell_report_says_step_1_kept_local_work_in_counts_only(env: dict[str, str]) -> None:
    """B13 round 9 K30, the shell twin: the line setup_report._local_work_line writes, from the same files
    (the checkout's ref files and the setup folder's patches), with no patch name, subject or author.  B13
    round 10 L8, L25, L40, L58: word for word the Python line's new form: the patches at the top of local-work
    (what bring-back.md section 3 reads), a bracket that claims nothing about an earlier report, no branch at
    main's or origin/main's own commit, and no "kept" for branches with no patch."""
    from agentsync import setup_report  # noqa: PLC0415

    home = Path(env["HOME"])
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    assert install_sh(env, "--report-only").returncode == 0
    assert "local work kept" not in report_path(env).read_text()
    # two keeps (one loose branch, one packed) whose second patch overwrote the first's (rehearsal D4)
    git = home / "src" / "agent-context-sync" / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "local-work-20261014-005444").write_text("a" * 40 + "\n")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{'b' * 40} refs/heads/local-work-20261014-010203\n"
        f"{'c' * 40} refs/heads/main\n"
    )
    patch = setup / "local-work" / "0001-Northwind-pricing-notes.patch"
    patch.parent.mkdir()
    patch.write_text(
        "From: Jane Fabrikam <jane@fabrikam.example>\nSubject: [PATCH] Northwind pricing notes\n"
    )
    os.utime(patch, (1791950084, 1791950084))
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert (
        "- local work kept by setup prompt step 1: 1 patch file(s) in the setup folder's local-work, newest "
        f"written 2026-10-14T03:54:44Z ({setup_report.LOCAL_WORK_WHERE}); 2 local-work branch(es) in the "
        "checkout\n" in text
    ), text
    assert f'LOCAL_WORK_WHERE="{setup_report.LOCAL_WORK_WHERE}"\n' in INSTALL_SH.read_text(), "one wording"
    for word in ("Northwind", "Fabrikam", "pricing", "0001"):
        assert word not in text, word
    # a nested patch is not counted, nor a branch at main's or origin/main's own commit
    nested = patch.parent / "older" / patch.name
    nested.parent.mkdir()
    patch.rename(nested)
    neutral = "- local-work branch(es) in the checkout: {}, no patch file in the setup folder's local-work "
    neutral += "(a keep with nothing to keep writes none)\n"
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert neutral.format(2) in text and "local work kept" not in text, text
    (git / "refs" / "heads" / "local-work-20261014-005444").write_text("c" * 40 + "\n")
    (git / "refs" / "remotes" / "origin").mkdir(parents=True)
    (git / "refs" / "remotes" / "origin" / "main").write_text("b" * 40 + "\n")
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "local work kept" not in text and "local-work branch" not in text, text
    (git / "refs" / "heads" / "main").write_text("d" * 40 + "\n")  # a loose main overrides its packed line
    assert install_sh(env, "--report-only").returncode == 0
    assert neutral.format(1) in report_path(env).read_text()


def test_the_shell_report_registers_the_folders_step_1s_list_printed(env: dict[str, str]) -> None:
    """B13 round 8 J69, the shell twin: the shell report never lists CloudStorage, so an unconfigured folder
    that step 1's list showed and the agent quoted printed in clear beside the configured one's <folder-N>.
    It registers the folders install.out's --list-folders runs printed, read from the file, whatever mark the
    list put before one (B13 round 9 K45: a folder inside a synced one stayed in clear)."""
    home = Path(env["HOME"])
    cs = home / "Library" / "CloudStorage"
    (cs / "OneDrive-Contoso" / "Q3 Launch").mkdir(parents=True)
    (cs / "OneDrive-Contoso" / "Northwind Merger").mkdir()
    _write_config(env, local_source_table("q3-launch", cs / "OneDrive-Contoso" / "Q3 Launch"))
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    (setup / "friction.md").write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v16\n"
        "2026-10-10T00:00:01Z | step 1 | deviation | the person picked Q3 Launch, not Northwind Merger | -\n"
        "2026-10-10T00:00:02Z | step 1 | deviation | Fabrikam Bid is inside it | -\n"
    )
    out = setup / "install.out"
    out.write_text(
        "# run=20261010T000000Z-1 2026-10-10T00:00:00Z install.sh --list-folders\n"
        f"{cs}/OneDrive-Contoso/Northwind Merger\n[synced] {cs}/OneDrive-Contoso/Q3 Launch\n"
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Q3 Launch/Fabrikam Bid\n"
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert "Northwind" not in text, [ln for ln in text.splitlines() if "Northwind" in ln]
    assert "Fabrikam" not in text, [ln for ln in text.splitlines() if "Fabrikam" in ln]
    assert re.search(r"picked <folder-\d+>, not <folder-\d+> \|", text), text
    # the legend names the source it was read from (B13 round 9 K3)
    legend = " ".join(text.split("\n## Redaction\n", 1)[1].split("\n\n")[0].split())
    assert "sources.toml, install.log or step 1's folder list in install.out name" in legend, legend
    # a run that is not a list names no folder of its own
    out.write_text(out.read_text().replace(" --list-folders", " --source-local x"))
    assert install_sh(env, "--report-only").returncode == 0
    assert "not Northwind Merger" in report_path(env).read_text(), "only a list run's paths are read"


def test_the_shell_agent_words_are_the_python_reports() -> None:
    """agent_words_name's words are setup_report._AGENT_WORDS (B13 round 9 K1, K47)."""
    body = INSTALL_SH.read_text().split("\nagent_words_name() {\n", 1)[1].split("\n}\n", 1)[0]
    [row] = re.findall(r"^\t\t(claude .*)\) any=1 ;;$", body, re.M)
    assert {w.strip() for w in row.split("|")} == set(setup_report._AGENT_WORDS)


@pytest.mark.parametrize(
    "name",
    [
        "Copilot",
        "github-copilot",
        "GitHub_Copilot",
        "Claude  Code",
        "Codex\\Cursor",
        "copilot-",
        "Copilot Notes",
        "",
        "-",
    ],
)
def test_the_shell_agent_words_test_is_the_python_reports(name: str) -> None:
    fn = _shell_function("agent_words_name")
    cp = subprocess.run(
        [BASH32, "-c", f'{fn}\nagent_words_name "$1"', "-", name],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    assert (cp.returncode == 0) == setup_report._agent_words(name), (name, cp.stderr)


def _listed(cs: Path, *paths: str) -> str:
    return "# run=20261010T000000Z-1 2026-10-10T00:00:00Z install.sh --list-folders\n" + "".join(
        f"{cs}/{p}\n" for p in paths
    )


def test_the_shell_report_keeps_a_listed_coding_agent_or_generic_folder_name(env: dict[str, str]) -> None:
    """B13 round 9 K1, K47: a folder only step 1's list printed, named with coding-agent words only
    ("Copilot") or a name every drive has ("Documents"), is not registered, as in the Python report: the
    Agent of "GitHub Copilot CLI" read "GitHub <folder-N> CLI" in the Summary and in the link's agent field.
    A configured folder of that name is still redacted."""
    home = Path(env["HOME"])
    cs = home / "Library" / "CloudStorage"
    (cs / "OneDrive-Contoso" / "Q3 Launch").mkdir(parents=True)
    _write_config(env, local_source_table("q3-launch", cs / "OneDrive-Contoso" / "Q3 Launch"))
    out = home / "agent-context" / "setup" / "install.out"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _listed(
            cs, "OneDrive-Contoso/Copilot", "OneDrive-Contoso/Documents", "OneDrive-Contoso/Documents/Copilot"
        )
        + f"[synced] {cs}/OneDrive-Contoso/Q3 Launch\n"
    )
    said = f"prompt v{COMPAT}, GitHub Copilot CLI: git pull failed in Documents beside Q3 Launch"
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    outcome = next(ln for ln in _summary(text) if ln.startswith("- outcome: "))
    assert outcome.endswith(" · agent: GitHub Copilot CLI"), outcome
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(text.splitlines()[-1]).query)
    assert fields["agent"] == ["GitHub Copilot CLI"]
    assert "git pull failed in Documents beside <folder-1> |" in text, text
    # the agentsync report reads the same agent from the same line
    attempt = setup_report.parse_friction(friction_path(env).read_text()).latest
    assert attempt is not None and attempt.said("Agent") == "GitHub Copilot CLI"
    # a configured folder named so is still redacted, as in the Python report
    (cs / "OneDrive-Contoso" / "Copilot").mkdir()
    _write_config(env, local_source_table("copilot", cs / "OneDrive-Contoso" / "Copilot"))
    assert install_sh(env, "--report-only").returncode == 0
    outcome = next(ln for ln in _summary(report_path(env).read_text()) if ln.startswith("- outcome: "))
    assert re.search(r" · agent: GitHub <folder-\d+> CLI$", outcome), outcome


def test_the_shell_report_gives_each_folder_one_number(env: dict[str, str]) -> None:
    """B13 round 9 K4: every place a folder was found took a new number, so six names read as <folder-1> to
    <folder-10>. One number per folder now, its other spellings (setup_report._norm) sharing it, each still
    redacted, and a configured folder's slug id still reads as its folder's <folder-N>."""
    home = Path(env["HOME"])
    cs = home / "Library" / "CloudStorage"
    for name in ("Alpha", "Beta"):
        (cs / "OneDrive-Contoso" / name).mkdir(parents=True)
    _write_config(
        env,
        local_source_table("alpha", cs / "OneDrive-Contoso" / "Alpha"),
        local_source_table("beta", cs / "OneDrive-Contoso" / "Beta"),
    )
    out = home / "agent-context" / "setup" / "install.out"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _listed(
            cs,
            "OneDrive-Contoso/Alpha",
            "OneDrive-Contoso/Alpha/Q3 Plans",
            "OneDrive-Contoso/Beta",
            "OneDrive-Contoso/Beta/Drafts",
            "OneDrive-Contoso/Gamma",
            "OneDrive-Contoso/Delta",
            "OneDrive-Fabrikam/Q3-Plans",
        )
    )
    said = "saw Alpha, Q3 Plans, Beta, Drafts, Gamma, Delta and Q3-Plans; alpha is the id"
    assert install_sh(env, "--log", "1", "deviation", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    line = next(ln for ln in text.splitlines() if "| deviation |" in ln)
    m = re.search(
        r"saw (<folder-\d>), (<folder-\d>), (<folder-\d>), (<folder-\d>), (<folder-\d>), (<folder-\d>) "
        r"and (<folder-\d>); (<folder-\d>) is the id",
        line,
    )
    assert m, line
    got = m.groups()
    assert sorted(set(got)) == [f"<folder-{i}>" for i in range(1, 7)], got
    assert got[6] == got[1], "another spelling of Q3 Plans shares its number"
    assert got[7] == got[0], "the configured folder's id reads as its folder"
    assert not re.search(r"<folder-([7-9]|\d\d)>", text)


def test_the_shell_report_reads_every_git_config_file_in_a_utf8_locale(
    env: dict[str, str], tmp_path: Path
) -> None:
    """The shell fallback reads the git config files ``_git_proxy_urls`` reads: the XDG config and the
    ``.git/config`` of the checkout the script runs from, as well as ``~/.gitconfig``. A Latin-1 byte in a
    config file stops BSD sed in a UTF-8 locale before it reaches the proxy line; the git reader runs under
    ``LC_ALL=C`` (B13 H19, H1). The fixture's locale is C, so this test runs in en_US.UTF-8."""
    src = tmp_path / "checkout"
    (src / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, src / "scripts" / "install.sh")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("[http]\n\tproxy = http://repo.fabrikam.example:3128\n")
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "config").write_text("[http]\n\tproxy = xdg.fabrikam.example:8080\n")
    home = Path(env["HOME"])
    (home / ".gitconfig").write_bytes(
        b"[user]\n\tname = Jos\xe9\n[http]\n\tproxy = http://home.fabrikam.example:1\n"
    )
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    hosts = ("repo", "xdg", "home")
    friction.write_text(
        "Prompt: v10\n"
        + "".join(f"F1 | step 1 | error | Could not resolve proxy: {h}.fabrikam.example | -\n" for h in hosts)
    )
    cp = subprocess.run(
        [BASH32, str(src / "scripts" / "install.sh"), "--report-only"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**env, "LC_ALL": "en_US.UTF-8", "XDG_CONFIG_HOME": str(xdg)},
        stdin=subprocess.DEVNULL,
    )
    assert cp.returncode == 0, cp.stderr
    assert "(shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_text(encoding="utf-8", errors="replace")
    for h in hosts:
        assert f"{h}.fabrikam" not in text, h
    assert len(re.findall(r"Could not resolve proxy: <proxy-\d+> \|", text)) == len(hosts), text


def test_the_shell_report_reads_a_linked_worktrees_git_config(env: dict[str, str]) -> None:
    """The prompt's clone may be a linked worktree, whose ``.git`` is a file: the shell fallback reads the
    common dir's config and the worktree's ``config.worktree``, as ``_git_proxy_urls`` does (B13 round 2
    R110)."""
    home = Path(env["HOME"])
    main = home / "src" / "contoso-main"
    gitdir = main / ".git" / "worktrees" / "agent-context-sync"
    gitdir.mkdir(parents=True)
    (main / ".git" / "config").write_text("[http]\n\tproxy = http://common.fabrikam.example:3128\n")
    (gitdir / "commondir").write_text("../..\n")
    (gitdir / "config.worktree").write_text("[http]\n\tproxy = http://own.fabrikam.example:8080\n")
    clone = home / "src" / "agent-context-sync"
    clone.mkdir()
    (clone / ".git").write_text("gitdir: ../contoso-main/.git/worktrees/agent-context-sync\n")
    friction = home / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    hosts = ("common", "own")
    friction.write_text(
        "Prompt: v10\n"
        + "".join(f"F1 | step 1 | error | Could not resolve proxy: {h}.fabrikam.example | -\n" for h in hosts)
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text(encoding="utf-8", errors="replace")
    for h in hosts:
        assert f"{h}.fabrikam" not in text, h
    assert len(re.findall(r"Could not resolve proxy: <proxy-\d+> \|", text)) == len(hosts), text


# ---- step 2 holds the background sync's lock (N6) ----------------------------------------------------------

REINSTALL_TICK = r"agentsync: reinstalling after the other sync ends, still running, \d+s"
REINSTALL_WAITS = (
    "agentsync: another sync is running (the background job, or one started in a terminal); the reinstall "
    "waits for it to end (at most {}s)"
)
"""The reinstall's wait line: the lock does not say who started the sync it waits for (B13 round 7 H33)."""


def _held_sync(env: dict[str, str]) -> tuple[dict[str, str], Path]:
    """A re-run's setting: the tool and its config exist, and the tool names <state_dir>/agentsync.lock."""
    home = Path(env["HOME"])
    tool_py = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("# stub\n")
    lock = home / "Library" / "Application Support" / "agentsync" / "agentsync.lock"
    lock.parent.mkdir(parents=True)
    return {**env, "STUB_SYNC_LOCK": str(lock), "STUB_PY": sys.executable}, lock


def _step_seconds(env: dict[str, str], step: str) -> int:
    [secs] = [int(m) for ln in install_log(env) for m in re.findall(rf"step={step} seconds=(\d+) ", ln)]
    return secs


def _lock_log(env: dict[str, str]) -> list[str]:
    return Path(env["STUB_LOG"] + ".lock").read_text().splitlines()


def test_the_reinstall_waits_for_a_running_background_sync_and_holds_its_lock(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """With --force uv rebuilds the tool environment the background sync runs from (2026-10-08 U7): the
    reinstall waits for a running sync, with progress lines, and holds the sync's lock while uv runs, so a
    sync starting then is skipped (exit 75).  The lock's body stays empty: the next sync breaks no stale lock.
    The test lets go once the first progress line is out, not after a fixed sleep."""
    e, lock = _held_sync(env)
    e = {**e, "AGENTSYNC_PROGRESS_SECONDS": "1", "AGENTSYNC_REINSTALL_WAIT_SECONDS": "60"}
    held = SingleWriterLock(lock, "poll")
    held.acquire()
    early: list[str] = []
    proc: subprocess.Popen[str] | None = None
    try:
        try:
            proc = subprocess.Popen(
                [BASH32, str(INSTALL_SH), str(wheel), "--source-local", str(folder)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=e,
                stdin=subprocess.DEVNULL,
            )
            assert proc.stdout is not None
            deadline = time.monotonic() + 50
            for raw in proc.stdout:
                early.append(raw.rstrip("\n"))
                if re.fullmatch(REINSTALL_TICK, early[-1]) or time.monotonic() > deadline:
                    break
            Path(f"{lock}.released").touch()  # before the release: uv can only start after it
        finally:
            held.release()
        # The rest is read through the same text wrapper (communicate would read the raw fd and drop what
        # the wrapper already buffered); the timer keeps communicate's 120 s bound.
        killer = threading.Timer(120, proc.kill)
        killer.start()
        try:
            rest = proc.stdout.read()
        finally:
            killer.cancel()
        proc.wait(timeout=10)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
    lines = early + rest.splitlines()
    out = "\n".join(lines)
    assert proc.returncode == 0, out
    assert REINSTALL_WAITS.format(60) in lines
    ticks = [ln for ln in lines if ln.startswith("agentsync: reinstalling")]
    assert ticks and all(re.fullmatch(REINSTALL_TICK, t) for t in ticks), out
    assert "still running after" not in out
    assert _lock_log(e) == ["released=yes", "held=True"]
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 1
    assert _step_seconds(e, "agentsync") >= 1
    # install.log says why the step took its seconds (B13 round 2 R34).
    assert ("agentsync", "done", "0", "waited-for-a-background-sync") in steps(install_log(e))
    assert lock.read_text() == "" and lock.stat().st_mode & 0o777 == 0o600


def test_a_sync_still_running_after_the_wait_leaves_the_reinstall_as_before(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e, lock = _held_sync(env)
    e = {**e, "AGENTSYNC_REINSTALL_WAIT_SECONDS": "1"}
    with SingleWriterLock(lock, "reconcile"):
        cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "agentsync: the other sync is still running after 1s; reinstalling without waiting longer" in (
        cp.stdout.splitlines()
    )
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 1
    assert ("agentsync", "done", "0", "wait-ran-out-after-1s") in steps(install_log(e)), "B13 round 2 R34"
    # The first sync then waits for the same lock: its note says so, after the counts when the sync prints
    # them (the stub prints none; setup_report's test reads the joined note) (B13 round 4 Q29).
    assert ("first-sync", "done", "0", "waited-for-the-lock") in steps(install_log(e))
    # B13 round 6 G37: that second wait was said only in agentsync's WARNING lines, which the prompt never
    # names, so an agent logged it as a deviation. A line of the installer's own says it first, in the words
    # step 2 of the prompt quotes; this Mac's config existed, so the step is "sync", not "first sync".
    out = cp.stdout.splitlines()
    wait = "sync: another sync is still running; waiting for it to end, at most 10 minutes"
    assert wait in out, cp.stdout
    assert out.index(wait) > out.index(
        "agentsync: the other sync is still running after 1s; reinstalling without waiting longer"
    )


def test_no_lock_file_means_no_wait_and_none_is_created(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    e, lock = _held_sync(env)
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not [ln for ln in cp.stdout.splitlines() if "background sync" in ln or "reinstalling" in ln]
    assert "another sync is still running" not in cp.stdout
    assert not lock.exists()
    assert _lock_log(e) == ["released=no", "held=False"]
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 1


def test_a_free_lock_file_is_held_while_uv_runs_without_a_wait_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The common re-run: no sync running, the lock file free.  uv still runs under the lock, so a sync that
    starts during the rebuild is skipped, and nothing says a sync was waited for."""
    e, lock = _held_sync(env)
    lock.write_text("")
    lock.chmod(0o600)
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not [ln for ln in cp.stdout.splitlines() if "background sync" in ln]
    assert _lock_log(e) == ["released=no", "held=True"]
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 1
    assert lock.read_text() == "" and lock.stat().st_mode & 0o777 == 0o600
    assert ("agentsync", "done", "0", "") in steps(install_log(e)), "no wait: no note"
    assert ("first-sync", "done", "0", "") in steps(install_log(e)), "a free lock: no wait to name"


def test_a_stale_body_is_kept_for_the_next_sync(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """A crashed cycle's body survives the reinstall (lockf writes nothing), so the next sync still breaks
    it and runs FULL passes."""
    e, lock = _held_sync(env)
    body = "99999 1 2026-10-10T00:00:00Z poll\n"
    lock.write_text(body)
    lock.chmod(0o600)
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _lock_log(e) == ["released=no", "held=True"]
    assert lock.read_text() == body
    nxt = SingleWriterLock(lock, "poll")
    try:
        assert nxt.acquire().broke_stale
    finally:
        nxt.release()


def test_the_reinstall_holds_the_lock_of_the_config_a_job_runs_with(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The jobs run with the --config fixed in their plist, which can differ from this run's config: the
    reinstall probes and holds that config's lock too (B13 round 2 R114)."""
    e, lock = _held_sync(env)
    lock.write_text("")
    home = Path(env["HOME"])
    job_cfg = home / "contoso-job" / "sources.toml"
    job_cfg.parent.mkdir()
    job_cfg.write_text("# stub\n")
    job_lock = home / "contoso-job" / "agentsync.lock"
    job_lock.write_text("")
    plist = home / "Library" / "LaunchAgents" / "com.agentsync.poll.plist"
    plist.parent.mkdir(parents=True)
    argv = ["/x/agentsync-launcher", "--", "/x/python", "-m", "agentsync", "sync", "--config", str(job_cfg)]
    plist.write_bytes(plistlib.dumps({"Label": "com.agentsync.poll", "ProgramArguments": argv}))
    e = {**e, "STUB_JOB_CONFIG": str(job_cfg), "STUB_JOB_LOCK": str(job_lock)}
    e["AGENTSYNC_REINSTALL_WAIT_SECONDS"] = "1"
    with SingleWriterLock(job_lock, "poll"):
        cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert REINSTALL_WAITS.format(1) in cp.stdout.splitlines()
    assert ("agentsync", "done", "0", "wait-ran-out-after-1s") in steps(install_log(e))


def test_the_reinstall_waits_once_however_many_locks_are_busy(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """The run's config and a job's config each have a running sync. Nested lockf timers each started when
    the lock before was taken, so the caps added up: 3.8 s for the first lock and 4 more for the second
    against a promised 4. The wait has one deadline (B13 round 4 Q39, Q63)."""
    e, lock = _held_sync(env)
    lock.write_text("")
    home = Path(env["HOME"])
    job_cfg = home / "contoso-job" / "sources.toml"
    job_cfg.parent.mkdir()
    job_cfg.write_text("# stub\n")
    job_lock = home / "contoso-job" / "agentsync.lock"
    job_lock.write_text("")
    plist = home / "Library" / "LaunchAgents" / "com.agentsync.poll.plist"
    plist.parent.mkdir(parents=True)
    argv = ["/x/agentsync-launcher", "--", "/x/python", "-m", "agentsync", "sync", "--config", str(job_cfg)]
    plist.write_bytes(plistlib.dumps({"Label": "com.agentsync.poll", "ProgramArguments": argv}))
    e = {**e, "STUB_JOB_CONFIG": str(job_cfg), "STUB_JOB_LOCK": str(job_lock)}
    e["AGENTSYNC_REINSTALL_WAIT_SECONDS"] = "4"
    first = SingleWriterLock(lock, "poll")
    first.acquire()
    timer = threading.Timer(3.8, first.release)
    try:
        with SingleWriterLock(job_lock, "poll"):
            timer.start()
            cp = install_sh(e, str(wheel), "--source-local", str(folder))
    finally:
        timer.cancel()
        timer.join()
        if first.held:
            first.release()
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "agentsync: the other sync is still running after 4s; reinstalling without waiting longer" in (
        cp.stdout.splitlines()
    )
    assert ("agentsync", "done", "0", "wait-ran-out-after-4s") in steps(install_log(e))
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 1, "the reinstall still runs"
    assert _step_seconds(e, "agentsync") <= 6, "one 4 s deadline, not 3.8 s plus 4"


def test_a_locked_install_that_meets_a_half_removed_environment_runs_once_more(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """A tick that starts the tool's Python while uv removes the environment fails uv and leaves no
    interpreter: the install runs once more under the lock, and a second failure says so instead of blaming
    the network (B13 round 2 R114)."""
    e, lock = _held_sync(env)
    lock.write_text("")
    tool_py = Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    cp = install_sh({**e, "STUB_UV_HALF_REMOVE": "1"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 2
    assert "agentsync: uv left the tool environment half removed" in cp.stdout
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    cp = install_sh({**e, "STUB_UV_HALF_REMOVE": "always"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1
    assert "left the tool environment without its interpreter" in cp.stderr
    assert "network, proxy or TLS" not in cp.stderr


def test_a_bad_reinstall_wait_is_a_usage_error(env: dict[str, str]) -> None:
    cp = install_sh({**env, "AGENTSYNC_REINSTALL_WAIT_SECONDS": "2m"})
    assert cp.returncode == 2 and "AGENTSYNC_REINSTALL_WAIT_SECONDS must be a whole number" in cp.stderr


def test_the_shell_context_org_word_list_is_the_reports() -> None:
    """B13 round 6 G46: install.sh's context_org_word holds setup_report._CONTEXT_ORG_WORDS, word for word."""
    body = INSTALL_SH.read_text().split("\ncontext_org_word() {\n", 1)[1].split("\n}\n", 1)[0]
    [line] = [ln.strip() for ln in body.splitlines() if ln.strip().endswith(") return 0 ;;")]
    assert set(line.removesuffix(") return 0 ;;").split(" | ")) == set(setup_report._CONTEXT_ORG_WORDS)


def test_the_shell_report_replaces_a_one_word_organisation_outside_the_generic_list_everywhere(
    env: dict[str, str],
) -> None:
    """B13 round 6 G46: every word install.sh's code holds was a context word, so a tenant named "Status" or
    "Probe" printed bare in a sentence, and the Python report disagreed on a code-only word. Only the generic
    list is matched in names alone; any other one-word organisation is replaced everywhere, and a report word
    then makes the report private only, as the Python report does."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage"
    team = cloud / "OneDrive-Contoso" / "Q3 Launch"
    team.mkdir(parents=True)
    for org in ("Status", "Probe"):
        (cloud / f"OneDrive-{org}").mkdir()
    friction = Path(env["HOME"]) / "agent-context" / "setup" / "friction.md"
    friction.parent.mkdir(parents=True)
    friction.write_text(
        "Prompt: v4\nF1 | step 3 | clean | 1 | the Status tenant; the Probe tenant; the Contoso tenant | -\n",
        encoding="utf-8",
    )
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, "--source-local", str(team))
    assert cp.returncode == 1
    text = report_path(env).read_text(encoding="utf-8")
    for org in ("Status", "Probe", "Contoso"):
        assert f"the {org} tenant" not in text and f"OneDrive-{org}" not in text, org
    assert "Contoso" not in text
    assert re.search(r"the <org-\d> tenant; the <org-\d> tenant; the <org-\d> tenant", text), text
    assert "Private only:" in text, "status is a word the shell report prints"


def test_the_shell_report_leaves_a_report_with_a_generic_folder_public(env: dict[str, str]) -> None:
    """B13 round 6 G48: a configured Downloads (a word install.sh prints) made only the shell report private,
    and a Documents only the Python one. A name every OneDrive has gives nothing away: neither is private."""
    cloud = Path(env["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    folders = [cloud / "Documents", cloud / "Downloads"]
    for folder in folders:
        folder.mkdir(parents=True)
    args = [a for f in folders for a in ("--source-local", str(f))]
    assert install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, *args).returncode == 1
    text = report_path(env).read_text(encoding="utf-8")
    assert "Contoso" not in text and "Private only:" not in text, text
