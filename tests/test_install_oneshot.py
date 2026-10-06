"""scripts/install.sh as the README one-prompt (setup prompt v7) runs it: ``--version``, ``--log-start``,
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
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from agentsync import setup_report

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"
BASH32 = "/bin/bash"
UID = os.getuid()
ACTION = (
    "ACTION: macOS is asking whether agentsync-launcher may access files managed by OneDrive. Click Allow."
)
LOOP_NEXT = "draft the baseline questions (stub)"  # the stub status's NEXT, which install.sh lifts (KISS K02)
TCC_CLICK = "turn on agentsync-launcher in System Settings > Privacy & Security > Files and Folders"

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
  add-source) setup; printf '[[source]]\\npath = "%s"\\n' "$pos" >> "$cfg"; inbox; hint "agentsync doctor" ;;
  doctor|status)
    if [ "$sub" = status ] && [ "${AGENTSYNC_NO_NEXT_HINT:-}" != 1 ]; then  # install.sh's closing status
      printf '%s\\n' "${STUB_STATUS_OUT:-NEXT: draft the baseline questions (stub)}"
      echo "  exclude_label_names: Stub Secret Label"
      exit "${STUB_STATUS_RC:-0}"
    fi
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
    return subprocess.run(
        [BASH32, str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
        stdin=subprocess.DEVNULL,
    )


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
    """README step 1: "If --version does not end with "setup-prompt-compat 7" or higher ..."."""
    cp = subprocess.run([BASH32, str(INSTALL_SH), "--version"], capture_output=True, text=True, check=False)
    assert cp.returncode == 0, cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[-1] == "setup-prompt-compat 7"
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
    assert "install.sh --log-start '<agent>'" in block and "install.sh --report-only" in block
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
    assert "start install.sh compat=7 commit=- kind=wheel" in log[0] and "launchd=simulated" not in log[0]
    assert steps(log) == [
        ("uv", "skipped", "0", "present"),
        ("agentsync", "done", "0", ""),
        ("launcher", "skipped", "0", "no-launcher"),
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


def test_doctor_tcc_pending_alone_does_not_stop_the_run(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    doctor_out = "[ok  ] config — ok\n[FAIL] tcc.fy26-projects — TCC_PENDING: did not answer within 15s"
    e = {**env, "STUB_DOCTOR_OUT": doctor_out, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("ACTION:")] == [ACTION]
    assert ("status", "done", "1", "tcc-pending") in steps(install_log(env))
    assert ("first-sync", "done", "0", "") in steps(install_log(env))


def test_doctor_failure_skips_first_sync_and_agent(env: dict[str, str], folder: Path, wheel: Path) -> None:
    e = {**env, "STUB_DOCTOR_OUT": "[FAIL] docs_repo — not writable", "STUB_DOCTOR_RC": "1"}
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
    assert steps(install_log(env))[4:6] == [("status", "done", "0", ""), ("first-sync", "done", "0", "")]
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
    fail = "[FAIL] tcc.fy26-projects — TCC_PENDING: did not answer within 15s"
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
        "WAITING ON YOU: the baseline questions are a draft: change both files to status: confirmed",
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
    fail = "[FAIL] tcc.fy26-projects — TCC_PENDING: did not answer within 15s"
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
    assert "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~" in text.split("## 3. Local work", 1)[1]
    assert f"bring back: {back} (one file: report, fix request, local work)" in cp.stdout

    (setup / "fix-request.md").unlink()
    (setup / "local-work" / "0001-local.patch").unlink()
    install_sh(env, "--report-only")
    text = back.read_text(encoding="utf-8")
    assert text.split("## 2. Fix request", 1)[1].split("\n\n", 2)[1] == "none"
    assert text.rstrip().endswith("none")


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
    said = 'set exclude = ["~$*", "/Fabrikam Bids/", "/Plans/Tailspin [[]old]/"] in [[source]] id = \'one\''
    (setup / "friction.md").write_text(
        "Attempt: 2026-09-29T09:58:00Z\nPrompt: v7\nAgent: x\n"
        f"2026-09-29T10:00:00Z | step 2 | deviation | status said: {said} in sources.toml | -\n"
        '2026-09-29T10:00:01Z | step 2 | deviation | and then: set exclude = ["~$*", "/Fabrikam Bi\n',
        encoding="utf-8",
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert f"report: {report_path(env)} (shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_text(encoding="utf-8")
    assert "status said: set exclude = [<path>] in [[source]] id = 'one' in sources.toml | -\n" in text
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
    assert " start install.sh compat=7 " in log[0] and log[0].endswith(" args=--list-folders")
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


def test_setup_dir_and_files_are_made_owner_only(env: dict[str, str], folder: Path, wheel: Path) -> None:
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    setup.chmod(0o755)
    for name in ("install.log", "friction.md", "install.out"):
        (setup / name).write_text("earlier\n")
        (setup / name).chmod(0o644)
    cp = install_sh(env, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (setup.stat().st_mode & 0o777) == 0o700
    for name in ("install.log", "friction.md", "install.out"):
        assert ((setup / name).stat().st_mode & 0o777) == 0o600, name


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
    start = install_sh(env, "--log-start", "Claude Code, claude-opus-5-5")
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
    assert lines[1:3] == ["Prompt: v7", "Agent: Claude Code, claude-opus-5-5"]
    assert re.fullmatch(rf"{t} \| step 1 \| question \| asked which terminal app \| -", lines[3])
    assert re.fullmatch(rf"{t} \| step 2 \| error \| install.sh exited 3 \| a longer wait", lines[4])
    assert re.fullmatch(rf"{t} \| end \| finished", lines[5]) and len(lines) == 6
    parsed = setup_report.parse_friction(friction_path(env).read_text())
    (attempt,) = parsed.attempts
    assert attempt.header["Prompt"] == "v7" and attempt.version == 7 and attempt.finished
    assert [(e.step, e.kind, e.what, e.fix) for e in attempt.events] == [
        (1, "question", "asked which terminal app", ""),
        (2, "error", "install.sh exited 3", "a longer wait"),
        (None, "finished", "finished", ""),
    ]


def test_friction_options_touch_nothing_else(env: dict[str, str]) -> None:
    for argv in (["--log-start", "Copilot CLI"], ["--log", "1", "click", "x", "-"]):
        assert install_sh(env, *argv).returncode == 0
    setup = friction_path(env).parent
    assert sorted(p.name for p in setup.iterdir()) == ["friction.md"], "no install.log, no install.out"
    assert not report_path(env).exists() and calls(env) == [], "no report, no uv, agentsync or launchctl"
    assert (setup.stat().st_mode & 0o777) == 0o700 and (setup.parent.stat().st_mode & 0o777) == 0o700
    assert (friction_path(env).stat().st_mode & 0o777) == 0o600


def test_friction_options_are_fast(env: dict[str, str]) -> None:
    for argv in (["--log-start", "a"], ["--log", "2", "approval", "x", "-"]):
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
    assert install_sh(e, "--log-start", "a").returncode == 0
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
    assert install_sh(env, "--log-start", "a").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    again = install_sh(env, "--report-only")
    assert again.returncode == 0 and "friction log:" not in again.stdout
    assert [ln.split(" | ", 1)[-1] for ln in f.read_text().splitlines()][3:] == ["end | finished"]
    assert install_sh(env, "--log-start", "b").returncode == 0
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
    assert install_sh(env, "--log-start", "a").returncode == 0
    before = friction_path(env).read_bytes()
    cp = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, "--report-only")
    assert cp.returncode == 0, cp.stderr
    assert friction_path(env).read_bytes() == before and not report_path(env).exists()
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("NEXT:")] == [
        "NEXT: re-run without AGENTSYNC_INSTALL_DRY_RUN=1 to write the report"
    ]


def test_a_saved_v6_prompt_still_closes_and_reports(env: dict[str, str]) -> None:
    """K17 review: a saved v6 prompt passes the "6 or higher" gate against compat 7, so its step 3
    (``--log-end && --report-only``) still works: the hidden --log-end closes the attempt once and exits 0."""
    assert install_sh(env, "--log-start", "a").returncode == 0
    end = install_sh(env, "--log-end")
    assert end.returncode == 0 and end.stdout == f"friction log: attempt finished in {friction_path(env)}\n"
    assert install_sh(env, "--report-only").returncode == 0
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


# ---- the on-device OCR helper is built in the launcher step ------------------------------------------------

STUB_TOOL_PYTHON = """#!/bin/bash
echo "python $* config=${AGENTSYNC_CONFIG:-}" >> "$STUB_LOG"
[ -z "${STUB_OCR_ERR:-}" ] || printf '%s\\n' "$STUB_OCR_ERR" >&2
[ -z "${STUB_OCR_OUT:-}" ] || printf '%s\\n' "$STUB_OCR_OUT"
exit "${STUB_OCR_RC:-0}"
"""


def test_launcher_step_builds_the_ocr_helper_and_a_failure_is_only_a_line(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """Decision D4: install.sh is the one place the helper is compiled (python -I -m agentsync.convert.ocr
    with the tool's interpreter), with or without background sync.  OCR is optional: whatever the build
    does, the run's exit status and its install.log steps are those of a run without it.  Without developer
    tools nothing is tried and one line says so: status's ocr line sends the reader to this script."""
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
        assert (cp.returncode, got) == (plain.returncode, plain_steps), cp.stdout + cp.stderr
        assert one_next(cp)
        ran = [c for c in calls(env) if c.startswith("python ")]
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
    assert [name for name, *_ in plain_steps][:4] == ["uv", "agentsync", "launcher", "config"]
    Path(env["STUB_LOG"]).unlink()
    dry = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, str(wheel), "--source-local", str(folder))
    planned = [ln for ln in dry.stdout.splitlines() if "agentsync.convert.ocr" in ln]
    assert planned == ([f"[dry-run] {tool_py} -I -m agentsync.convert.ocr"] if _have_git() else []), (
        dry.stdout
    )
    assert said(dry) == ([] if _have_git() else [no_tools])
    assert not any(c.startswith("python ") for c in calls(env)), "a dry run builds nothing"


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
