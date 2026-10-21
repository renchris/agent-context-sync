"""scripts/install.sh as the README one-prompt (setup prompt v29) runs it: ``--version``, ``--log-start``,
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
import base64
import calendar
import contextlib
import dataclasses
import fcntl
import hashlib
import itertools
import json
import os
import plistlib
import pwd
import re
import resource
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from agentsync import cli, lints, net, paths, setup_report
from agentsync.config import (
    Config,
    SourceConfig,
    canonical_source_root,
    default_config_text,
    derive_source_id,
    inbox_source_table,
    load_config,
    local_source_table,
)
from agentsync.convert import pandoc as pandoc_converter
from agentsync.cycle import VANISHED_ADVICE, VANISHED_FIX
from agentsync.cycle import vanished_add_back_tail as cycle_vanished_add_back_tail
from agentsync.cycle import vanished_detail as cycle_vanished_detail
from agentsync.manifest import Manifest
from agentsync.ops import doctor
from agentsync.ops.lock import SingleWriterLock
from agentsync.paths import expand

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

FAKE_WHEEL_PANDOC = b"#!/bin/sh\necho 'pandoc 9.9'\n"
STUB_PYPANDOC = (
    'sp="$HOME/.local/share/uv/tools/agentsync/lib/python3.11/site-packages"\n'
    'mkdir -p "$sp/pypandoc/files" "$sp/pypandoc_binary-1.17.dist-info"\n'
    "printf '%s\\n' '#!/bin/sh' \"echo 'pandoc 9.9'\" > \"$sp/pypandoc/files/pandoc\"\n"
    'chmod 755 "$sp/pypandoc/files/pandoc"\n'
    "printf '' > \"$sp/pypandoc/__init__.py\"\n"
    "printf 'pypandoc/__init__.py,sha256=%s,0\\npypandoc/files/pandoc,sha256=%s,%s\\n' "
    + base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).decode().rstrip("=")
    + " "
    + base64.urlsafe_b64encode(hashlib.sha256(FAKE_WHEEL_PANDOC).digest()).decode().rstrip("=")
    + f" {len(FAKE_WHEEL_PANDOC)}"
    + ' > "$sp/pypandoc_binary-1.17.dist-info/RECORD"\n'
)
"""A tool environment's pypandoc_binary as pip installs it, for install.sh's step 2 to find whole
(``bundled_pandoc_intact``): its pandoc, and the RECORD line with that file's sha256 and size."""

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
  while [ $# -gt 0 ]; do  # the constraints file, as the install read it
    [ "$1" != -c ] || cat "$2" >> "$STUB_LOG.pins"
    shift
  done
  half="${STUB_UV_HALF_REMOVE:-}"  # a tick started the tool's Python while uv removed the environment
  if [ -n "$half" ] && { [ ! -e "$STUB_LOG.half" ] || [ "$half" = always ]; }; then
    touch "$STUB_LOG.half"
    rm -f "$HOME/.local/share/uv/tools/agentsync/bin/python"
    echo "error: failed to remove directory (stub): Directory not empty (os error 66)" >&2; exit 2
  fi
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/uv/tools/agentsync/bin"
  cp "$STUB_AGENTSYNC" "$HOME/.local/bin/agentsync"
  chmod 755 "$HOME/.local/bin/agentsync"
@PYPANDOC@  echo "Installed 1 executable: agentsync" >&2
  b="$HOME/.local/bin"
  printf 'warning: `%s` is not on your PATH. To use installed tools, run ' "$b" >&2
  printf '`export PATH="%s:$PATH"` or `uv tool update-shell`.\n' "$b" >&2
elif [ "$1 $2 ${3:-}" = "tool dir --bin" ]; then
  echo "$HOME/.local/bin"
elif [ "$1 $2" = "tool dir" ]; then
  echo "$HOME/.local/share/uv/tools"
elif [ "$1" = export ]; then  # uv.lock's pins, when the test gives some
  [ -n "${STUB_UV_PINS:-}" ] || exit "${STUB_UV_EXPORT_RC:-0}"
  while [ $# -gt 0 ]; do
    [ "$1" != -o ] || printf '%s\n' "$STUB_UV_PINS" > "$2"
    shift
  done
fi
""".replace("@PYPANDOC@", STUB_PYPANDOC)

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
  init)
    [ -z "${STUB_INIT_RC:-}" ] ||
      { echo "configuration error: $cfg: not valid TOML (stub)" >&2; exit "$STUB_INIT_RC"; }
    setup; inbox; hint "agentsync doctor" ;;
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
    echo "announced=${AGENTSYNC_LOCK_WAIT_ANNOUNCED:-}" >> "$STUB_LOG.announced"
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


def xcrun_db() -> dict[str, str]:
    """conftest's xcrun cache, if set, for an env built without os.environ (load root cause 2026-10-17 X3)."""
    v = os.environ.get("xcrun_db")  # noqa: SIM112 # xcrun reads this lowercase name
    return {"xcrun_db": v} if v else {}


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
        **xcrun_db(),
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


def install_sh(
    env: dict[str, str],
    *args: str,
    timeout: float = 120,
    script: Path = INSTALL_SH,
    wrap: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """One install.sh run (``script``: a copy in another checkout; ``wrap``: the argv it runs under, such as
    ``sandbox-exec -p <profile>``). Its NEXT says of the [FAIL] lines above it that "each names its fix", so
    every run of every test is held to that: the real agentsync's lines, and the stub's, which are written as
    the real ones are."""
    cp = subprocess.run(
        [*wrap, BASH32, str(script), *args],
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


def rerun(wheel: Path, folder: Path | None = None) -> str:
    """The re-run command a NEXT line names (absolute script path, the same flags, a folder in double
    quotes: B13 round 16 T35). After the config step it names no folder: each is in the config (T22)."""
    named = "" if folder is None else f' --source-local "{folder}"'
    return f"{INSTALL_SH} {wheel}{named} --confirm-install-agent"


# ---- --version and the README prompt -----------------------------------------------------------------------


def test_version_prints_the_prompt_compat_line_last() -> None:
    """README step 1: "If the --version inside step 1's one command does not end with
    "setup-prompt-compat 29", ... stop"."""
    cp = subprocess.run([BASH32, str(INSTALL_SH), "--version"], capture_output=True, text=True, check=False)
    assert cp.returncode == 0, cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[-1] == "setup-prompt-compat 29" == f"setup-prompt-compat {COMPAT}"
    assert len(lines) == 2 and re.fullmatch(
        r"source commit: ([0-9a-f]{12}"
        r"( dirty [0-9a-f]{12} \(local changes in this checkout; setup prompt step 1 keeps them on a local"
        r" branch before it updates\))?|unknown \(.+\))",
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
    e = {
        **env,
        "STUB_LC_EXITS": "79",
        "AGENTSYNC_WAIT_SECONDS": "1",
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.5",  # it waits the whole time out: a slower poll
    }
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert cp.stdout.count("ACTION:") == 1
    assert "no background run exited 0 within 1s (last exit: 79, TCC pending" in cp.stderr
    assert last_line(cp) == (
        "NEXT: macOS is still waiting for Allow for agentsync-launcher: click Allow if the prompt is "
        f"showing, or {TCC_CLICK}, then re-run: {rerun(wheel)} [setup report: {report_path(env)}]"
    )
    assert ("wait", "failed", "79", "tcc") in steps(install_log(env))
    assert re.search(r" end rc=3 seconds=\d+$", install_log(env)[-1])


def test_a_run_still_going_at_the_timeout_exits_3(env: dict[str, str], folder: Path, wheel: Path) -> None:
    e = {
        **env,
        "STUB_LC_RUNNING": "1",
        "AGENTSYNC_WAIT_SECONDS": "1",
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.5",  # it waits the whole time out: a slower poll
    }
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "ACTION:" not in cp.stdout
    assert last_line(cp).startswith("NEXT: the first background run did not exit 0 within 1s")
    assert rerun(wheel) in last_line(cp)


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


def test_a_background_run_that_cannot_read_the_state_names_the_persons_fix(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 17 U55: a LaunchAgent run that ended on exit 65 (a manifest it cannot read) read "exited 65
    (exit 65); fix what agentsync status and the err log show", though status passes damage under a table
    and the fix loses history: it is the person's, as the first sync's 65 is."""
    cp = install_sh(
        {**env, "STUB_LC_EXITS": "65"}, str(wheel), "--source-local", str(folder), "--confirm-install-agent"
    )
    assert cp.returncode == 1
    said = (
        "the first background run exited 65 (the state file (manifest.sqlite) cannot be read: the person's "
    )
    said += "to fix)"
    assert said in cp.stderr, cp.stderr
    head = f"NEXT: {said}: agentsync's state file cannot be read (the error in "
    assert last_line(cp).startswith(head), last_line(cp)
    owner = "fixing it is the person's, not a step for the agent, so show them the error and go on to the "
    owner += f"report; once it is fixed, the next run is: {rerun(wheel)}"
    assert owner in last_line(cp), last_line(cp)
    assert "fix what" not in last_line(cp)


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


def test_doctor_tcc_pending_with_no_agent_asked_promises_no_wait(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 15 S35: an earlier install's LaunchAgents bring status a TCC_PENDING [FAIL] to a run with no
    --confirm-install-agent (prompt v23's step 2), whose wait is skipped, yet the Installer table said the
    Allow was one "which the wait step asks for". Its words promise no wait."""
    doctor_out = f"[ok  ] config — ok\n{TCC_PENDING_FAIL}"
    e = {**env, "STUB_DOCTOR_OUT": doctor_out, "STUB_DOCTOR_RC": "1"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not [ln for ln in cp.stdout.splitlines() if ln.startswith("ACTION:")]
    logged = steps(install_log(env))
    assert ("status", "done", "1", "tcc-pending") in logged and (
        "wait",
        "skipped",
        "0",
        "not-requested",
    ) in logged
    rows = [(step, {"result": res, "rc": rc, "note": note, "seconds": "0"}) for step, res, rc, note in logged]
    table = "\n".join(setup_report._step_table(rows))
    assert (
        "| done, rc 1 (its only [FAIL] lines were a macOS Allow still to be clicked (the [FAIL] line" in table
    )
    assert "wait step asks" not in table and "wait step asks" not in INSTALL_SH.read_text(encoding="utf-8")


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


def test_a_status_that_fails_with_no_fail_line_logs_status_error(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 14 Q50: install.sh logged ``note=fail-lines`` for any non-zero status exit, so a status that
    crashed (a traceback, no [FAIL] line) read "its status step reported [FAIL] lines". It logs
    ``status-error`` then, and the run still stops before its first sync."""
    cp = install_sh({**env, "STUB_DOCTOR_RC": "1"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert not any(c.startswith("agentsync sync") for c in calls(env))
    assert ("status", "done", "1", "status-error") in steps(install_log(env))
    # B13 round 15 S33, S58, S62: no [FAIL] line to fix, and a re-run fails the same way, so the NEXT sends
    # the agent to the report with what status printed, as the config-error NEXT does
    nxt = last_line(cp)
    assert nxt.startswith("NEXT: agentsync status exited 1 (failed) with no [FAIL] line: "), nxt
    assert "not a step for the agent, so show them those lines and go on to the report" in nxt, nxt
    assert "/agentsync status prints it again; " not in nxt and "/agentsync status prints it again)" in nxt
    assert "fix the [FAIL] lines" not in cp.stdout


def test_a_failed_step_2_names_no_folder_the_config_step_added(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 16 T22 (RT): after "added source ..." and "folders: 1 added to the 2 already synced", a
    status that stopped the run named the folder again in its re-run, where v24's step 2 says to name only
    folders to add. Every add-source failure stops the run, so once the config step ends each folder is in
    the config and the re-run names none; a failure before it still names the folder."""
    cp = install_sh({**env, "STUB_DOCTOR_RC": "1"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert last_line(cp).endswith(
        f"once fixed, the next run is: {INSTALL_SH} {wheel} [setup report: {report_path(env)}]"
    ), last_line(cp)
    logged = f"--source-local {str(folder).replace(' ', chr(92) + ' ')}"
    assert any(ln.endswith(f"args={wheel} {logged}") for ln in install_log(env)), "args= as logged"
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1
    assert last_line(cp).startswith(
        f'NEXT: fix the error above, then re-run: {INSTALL_SH} {wheel} --source-local "{folder}" ['
    ), last_line(cp)
    # B13 round 17 U63: a failure inside the config step's add-source loop keeps every folder, the one added
    # before it too: add-source says "already configured" and exits 0 on it, and a followed retry must read as
    # "the same command" in the report (setup_report._again_words), not one "with other folders"
    other = folder.parent / "Other"
    other.mkdir()
    e = {**env, "STUB_ADD_SOURCE_FAIL": str(other)}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--source-local", str(other))
    assert cp.returncode == 1
    assert last_line(cp).startswith(
        f'NEXT: fix the error above, then re-run: {INSTALL_SH} {wheel} --source-local "{folder}" '
        f'--source-local "{other}" ['
    ), last_line(cp)


@pytest.mark.parametrize(
    "name", ["Project Gamma - Shared", 'Notes "Q3" $HOME `x` \\ end', "Café Notes", "Tab\tName", "Wow! Notes"]
)
def test_a_re_run_names_a_folder_in_double_quotes(env: dict[str, str], name: str) -> None:
    """B13 round 16 T35 (Q22's residue): a re-run escaped a spaced folder with backslashes (printf %q), not
    the double quotes a person types. The NEXT shows the folder in double quotes, with \\ " $ and ` escaped,
    so it reads back as the same name; one %q writes as $'..' (a byte it cannot show: an accent under
    LC_ALL=C, a tab) or one with "!" (an interactive zsh expands it in double quotes) keeps the %q form.
    install.log's args=, which both reports parse, keeps %q."""
    named = Path(env["HOME"]) / "Documents" / name
    named.mkdir(parents=True)
    e = {**env, "STUB_UV_INSTALL_RC": "2", "LC_ALL": "C"}
    cp = install_sh(e, "--source-local", str(named))
    assert cp.returncode == 1
    shown = last_line(cp).split(f"then re-run: {INSTALL_SH} ", 1)[1].rsplit(" [setup report: ", 1)[0]
    assert shown.startswith("--source-local "), shown
    word = shown.removeprefix("--source-local ")
    plain = name.isascii() and "\t" not in name and "!" not in name
    assert word.startswith('"') == plain, word
    back = subprocess.run(
        [BASH32, "-c", f"printf %s {word}"], capture_output=True, check=True, env={"LC_ALL": "C"}
    ).stdout
    assert back == str(named).encode(), (word, back)
    logged = subprocess.run(
        [BASH32, "-c", 'printf %q "$1"', "-", str(named)],
        capture_output=True,
        check=True,
        env={"LC_ALL": "C"},
    ).stdout.decode()
    assert any(ln.endswith(f"args=--source-local {logged}") for ln in install_log(env)), "args= keeps %q"


def _source_fails(cfg: Config, src: SourceConfig) -> list[str]:
    """The [FAIL] lines doctor's checks of one source print, as status prints them."""
    out = doctor.format_results(doctor._check_local_source(cfg, src, Path(sys.executable)))
    return [ln for ln in out.splitlines() if ln.startswith("[FAIL] ")]


def _persons_fail_lines(cfg: Config) -> dict[str, list[str]]:
    """Every kind of source [FAIL] whose fix is the person's, from doctor itself: a synced folder renamed in a
    signed-in OneDrive (probe RN), a SharePoint library not synced here, one there but empty beside its
    signed-in organization (B13 round 14 Q45, Q54, Q63), a stale or missing provider folder,
    one missing beside another account's, a plain folder gone, a file where a folder belongs, a sentinel gone
    and a [[source]] with no path."""
    cloud = Path.home() / "Library" / "CloudStorage"
    org = cloud / "OneDrive-Contoso"
    base = dataclasses.replace(cfg.sources[0], id="team-site", sentinel=None)
    renamed = dataclasses.replace(base, path=org / "Team Site - General")
    (org / "Team Site - Archive").mkdir(parents=True)
    cases = {"renamed": _source_fails(cfg, renamed)}
    library = cloud / "OneDrive-SharedLibraries-Contoso" / "Team Site - Documents"
    cases["library"] = _source_fails(cfg, dataclasses.replace(base, path=library))
    # Gone again before the signed-out case: the folder's name splits to the app OneDrive, so left there it
    # would read as another account's folder.
    shared = library.parent
    shared.mkdir()
    (shared / ".hidden").write_text("x\n", encoding="utf-8")
    cases["library-empty"] = _source_fails(cfg, dataclasses.replace(base, path=library))
    assert all("is empty while OneDrive-Contoso" in ln for ln in cases["library-empty"]), cases[
        "library-empty"
    ]
    (shared / ".hidden").unlink()
    shared.rmdir()
    (org / "Team Site - Archive").rmdir()
    cases["stale"] = _source_fails(cfg, renamed)
    org.rmdir()
    cases["signed-out"] = _source_fails(cfg, renamed)
    (cloud / "OneDrive-Fabrikam").mkdir()
    cases["other-account"] = _source_fails(cfg, renamed)
    cases["plain"] = _source_fails(cfg, dataclasses.replace(base, path=cfg.config_path.parent / "gone"))
    afile = cfg.config_path.parent / "afile"
    afile.write_text("x\n", encoding="utf-8")
    cases["not-a-folder"] = _source_fails(cfg, dataclasses.replace(base, path=afile))
    cases["sentinel"] = _source_fails(cfg, dataclasses.replace(cfg.sources[0], sentinel="nope.txt"))
    cases["no-path"] = _source_fails(cfg, dataclasses.replace(base, path=None))
    # B13 round 15 S7: a source still mirrored whose [[source]] block is gone; its fix is the sync's own
    vanished = doctor.CheckResult(
        "config.vanished_sources",
        False,
        f"{cycle_vanished_detail({'team-site': 3})}, so every sync refuses this config (exit 78)",
        doctor.Severity.ERROR,
        fix=VANISHED_FIX,
    )
    cases["vanished"] = doctor.format_results([vanished]).splitlines()
    # B13 round 17 U9, U25, U46: the same fix with the add-back command after it
    add_back = doctor.vanished_fix([cfg.config_path.parent / "Project Delta"], every=True)
    with_add_back = dataclasses.replace(vanished, fix=add_back)
    cases["vanished-add-back"] = doctor.format_results([with_add_back]).splitlines()
    # B13 round 16 T2, T9: a [policy] value in policy.toml, or a [governance] one, that does not load
    policy = cfg.config_path.parent / "policy.toml"
    policy.write_text('[policy]\nrefuse_unlabelled = "yes"\n', encoding="utf-8")
    cases["policy"] = doctor.format_results(cli._policy_check(cfg)).splitlines()
    policy.unlink()
    governed = cfg.config_path.parent / "governed.toml"
    governed.write_text("[governance]\nhold = true\n", encoding="utf-8")
    cases["governance"] = doctor.format_results(
        cli._governance_checks(dataclasses.replace(cfg, config_path=governed))
    ).splitlines()
    # B13 round 17 U2, U16: a docs-repo remote [governance] does not allow, the person's to remove or allow
    remote = doctor.CheckResult(
        "governance.remote", False, "remote origin (<url>) exists", doctor.Severity.ERROR, fix=cli.REMOTE_FIX
    )
    cases["remote"] = doctor.format_results([remote]).splitlines()
    # B13 round 20 X74: a state folder and manifest another account owns (a first sudo sync): the chown is a
    # sudo, the person's, never a step for the agent, whose prompt says no sudo
    db = expand(cfg.state_paths.db)
    made = not db.exists()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.touch()
    with patch.object(doctor.os, "getuid", return_value=os.getuid() + 1):
        owned = [r for r in doctor._check_state_dir(cfg) if not r.ok]
    if made:
        db.unlink()
    assert [r.name for r in owned] == ["state_dir", "state_dir.files"], owned
    cases["state-owner"] = doctor.format_results(owned).splitlines()
    assert all(cases.values()), cases
    assert all(ln.startswith("[FAIL] ") for k in ("policy", "governance", "remote") for ln in cases[k]), cases
    return cases


def _pandoc_fail_lines(cfg: Config) -> dict[str, str]:
    """The pandoc check's [FAIL] lines, from doctor itself (B13 round 15 S47, S53): the bundled pandoc on an
    Apple silicon Mac without Rosetta, the operator's native copy that exits non-zero or that macOS will not
    start (the person's fixes), and the native copy and the bundled pandoc that ran out of time (the agent's
    re-run first). B13 round 17 U79, U80: the bundled pandoc that exits non-zero (the agent's re-run first,
    the [convert] edit the person's after it), and a configured pandoc_path that fails or is not absolute (a
    [convert] edit only, the person's); a configured one that ran out of time keeps the agent's re-run
    first."""
    copy = Path.home() / ".local/share/agentsync/pandoc/3.9-arm64/bin/pandoc"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    copy.chmod(0o755)

    def bad_cpu(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        raise OSError(doctor._EBADARCH, "Bad CPU type in executable")

    def silent(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(argv), timeout)

    def exits(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 3, "", "broken")

    lines: dict[str, str] = {}
    for case, kind, run in (
        ("rosetta", "wheel", bad_cpu),
        ("native", "native", exits),
        ("native-bad-cpu", "native", bad_cpu),
        ("native-timeout", "native", silent),
        ("wheel-timeout", "wheel", silent),
        ("wheel", "wheel", exits),
        ("configured", "configured", exits),
        ("configured-timeout", "configured", silent),
        ("not-absolute", "configured", exits),
    ):
        path = Path("bin/pandoc") if case == "not-absolute" else copy
        with (
            patch.object(doctor, "_pandoc_route", lambda config, k=kind, p=path: (p, k)),
            patch.object(doctor, "_run", run),
        ):
            lines[case] = doctor.format_results(doctor._check_pandoc(cfg)).strip()
        assert lines[case].startswith("[FAIL] pandoc") and " (fix: " in lines[case], lines[case]
    return lines


PANDOC_PERSONS = ("rosetta", "native", "native-bad-cpu", "configured", "not-absolute")


PERSONS_NEXT = (
    "NEXT: the [FAIL] lines above are the person's to fix (each names its fix: a folder or file to put back "
    "or sync, a [[source]] path, a [[source]] block to put back, a [policy] or [governance] value, a "
    "docs-repo remote to remove or allow, a state file that is damaged or another account's, a sign-in, a "
    "[convert] pandoc_path, Rosetta to install or a native pandoc copy), not a step for the agent, so show "
    "them those "
    "lines and go on to the report; once fixed, "
)


def test_a_fail_whose_fix_is_the_persons_sends_the_agent_to_the_report(
    env: dict[str, str], folder: Path, wheel: Path, sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 13 O11, O30: a synced folder renamed in a signed-in OneDrive (probe RN) failed step 2 with
    "fix: fix path in [[source]] ..., or sync that folder in OneDrive" and "NEXT: fix the [FAIL] lines above
    ..., then re-run: install.sh", which read three ways: a re-run that fails the same way, an edit of the
    person's sources.toml that chooses a folder for them, or the report. When every [FAIL] that stops the run
    is the person's (a path, a sentinel, a folder to put back or sync, a sign-in), the NEXT names the owner,
    as the config error's does, in step 2 and in the closing status."""
    monkeypatch.setenv("AGENTSYNC_NO_NEXT_HINT", "1")
    cases = _persons_fail_lines(sample_config)
    for case, fails in cases.items():
        e = {**env, "STUB_DOCTOR_OUT": "\n".join(fails), "STUB_DOCTOR_RC": "1"}
        cp = install_sh(e, str(wheel), "--source-local", str(folder))
        assert cp.returncode == 1, (case, cp.stdout + cp.stderr)
        assert last_line(cp).startswith(f"{PERSONS_NEXT}the next run is: {INSTALL_SH} "), (
            case,
            last_line(cp),
        )
        assert "fix the [FAIL] lines" not in last_line(cp) and one_next(cp)
        assert ("first-sync", "skipped", "0", "status-failed") in steps(install_log(env))
        Path(env["STUB_LOG"]).unlink(missing_ok=True)
    # B13 round 15 S53, S47: the pandoc check's person's fixes, alone and beside a source's
    pandoc = _pandoc_fail_lines(sample_config)
    for fails in ([pandoc["rosetta"]], [pandoc["native"]], [*cases["renamed"], pandoc["native-bad-cpu"]]):
        e = {**env, "STUB_DOCTOR_OUT": "\n".join(fails), "STUB_DOCTOR_RC": "1"}
        cp = install_sh(e, str(wheel), "--source-local", str(folder))
        assert cp.returncode == 1, cp.stdout + cp.stderr
        assert last_line(cp).startswith(f"{PERSONS_NEXT}the next run is: {INSTALL_SH} "), (
            fails,
            last_line(cp),
        )
        Path(env["STUB_LOG"]).unlink(missing_ok=True)
    out = "NEXT: the source check failed: do what the fix on its [FAIL] line below says\n" + "\n".join(
        cases["renamed"]
    )
    cp = install_sh(
        {**env, "STUB_STATUS_OUT": out, "STUB_STATUS_RC": "1"}, str(wheel), "--source-local", str(folder)
    )
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert last_line(cp).startswith(
        f"{PERSONS_NEXT}run {env['HOME']}/.local/bin/agentsync sync and follow its NEXT line"
    )


def test_the_reports_tcc_pending_pattern_is_install_shs_blocking_fails() -> None:
    """B13 round 16 T48: the report leaves a TCC_PENDING [FAIL] out of a run's stopping [FAIL] lines by its
    own copy of install.sh's ``blocking_fails`` pattern, and no test tied the two. They are one pattern, and
    it reads the line doctor writes for that check (padded name, ``TCC_PENDING: `` detail) and no other."""
    m = re.search(
        r"blocking_fails\(\) \{\n\tgrep '\^\\\[FAIL' \"\$1\" 2>/dev/null \| grep -Ev '([^']*)' \|\| true",
        INSTALL_SH.read_text(encoding="utf-8"),
    )
    assert m, "blocking_fails's pattern"
    assert m.group(1) == setup_report._TCC_PENDING_FAIL_RE.pattern
    pending = doctor.CheckResult("tcc.contoso-plans", False, "TCC_PENDING: the launcher waits for Allow")
    other = doctor.CheckResult("source.contoso-plans.listable", False, "TCC_PENDING: not this check")
    lines = doctor.format_results([pending, other]).splitlines()
    assert setup_report._TCC_PENDING_FAIL_RE.match(lines[0]), lines
    assert not setup_report._TCC_PENDING_FAIL_RE.match(lines[1]), lines


def test_doctors_persons_fix_words_are_install_shs(sample_config: Config) -> None:
    """B13 round 14 Q1, Q9, Q33: status's NEXT names a [FAIL] fix as the person's by
    ``doctor.PERSONS_FIX_RE``, and install.sh's persons_fails by its own copy (it runs the older installed
    build). The two are one list,
    and they agree on every person's fix doctor writes and on the fixes the agent makes."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"grep -Ev ' \\\(fix: (\(.*\))' >/dev/null", text)
    assert m, "persons_fails's pattern"
    assert m.group(1) == doctor.PERSONS_FIX_RE.pattern
    for case, fails in _persons_fail_lines(sample_config).items():
        for line in fails:
            fix = line.split(" (fix: ", 1)[1]
            assert doctor.is_persons_fix(fix), (case, line)
    timeout = subprocess.TimeoutExpired(["/x/pandoc", "--version"], 60)
    for result in (doctor._timed_out("pandoc", timeout),):
        assert result.fix and not doctor.is_persons_fix(result.fix)
    # B13 round 15 S53, S47: Rosetta to install and the operator's native pandoc copy are the person's; a
    # pandoc that ran out of time keeps the agent's re-run first, the native copy's too.
    for case, line in _pandoc_fail_lines(sample_config).items():
        assert doctor.is_persons_fix(line.split(" (fix: ", 1)[1]) is (case in PANDOC_PERSONS), (case, line)
    assert not doctor.is_persons_fix(LISTING_HELD_FAIL.split(" (fix: ", 1)[1])
    assert not doctor.is_persons_fix(None) and not doctor.is_persons_fix("")
    # B13 round 16 T5, T9: the kinds status's NEXT lists are PERSONS_FAILS's, word for word
    kinds = f"(each names its fix: {cli.PERSONS_KINDS}), not a step for the agent"
    assert f"PERSONS_FAILS=\"the [FAIL] lines above are the person's to fix {kinds}" in text
    # B13 round 18 V53: --help prints the header's list, in the same kinds and order (plus "or sentinel")
    shown = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--help"], capture_output=True, text=True, check=True
    ).stdout
    header = cli.PERSONS_KINDS.replace("a [[source]] path,", "a [[source]] path or sentinel,")
    assert f"is the person's ({header}), the NEXT says so" in " ".join(shown.split())


def test_a_fail_the_agent_fixes_by_a_re_run_keeps_the_re_run_next(
    env: dict[str, str], folder: Path, wheel: Path, sample_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of O11 and O30: a check that ran out of time is fixed by running the same install.sh
    command again (its fix says so, for a setup agent), and a listing held for an Allow click by that click,
    so a set with one of them, alone or beside a person's [FAIL], keeps "fix the [FAIL] lines above ...,
    then re-run" and never sends the agent to the report."""
    monkeypatch.setenv("AGENTSYNC_NO_NEXT_HINT", "1")
    timeout = subprocess.TimeoutExpired(["/x/pandoc", "--version"], 60)
    again = doctor.format_results([doctor._timed_out("pandoc", timeout)])
    # B13 round 19 W19, W48: the command by what it is, not "the same command ... (its NEXT line names it)"
    assert again.startswith("[FAIL] pandoc") and doctor.AGAIN_UNDER_INSTALL_SH in again
    renamed = _persons_fail_lines(sample_config)["renamed"]
    native = _pandoc_fail_lines(sample_config)["native-timeout"]  # B13 round 15 S47: the re-run first
    for fails in (
        [again],
        [*renamed, again],
        [LISTING_HELD_FAIL],
        [*renamed, LISTING_HELD_FAIL],
        [native],
        [*renamed, native],
    ):
        e = {**env, "STUB_DOCTOR_OUT": "\n".join(fails), "STUB_DOCTOR_RC": "1"}
        cp = install_sh(e, str(wheel), "--source-local", str(folder))
        assert cp.returncode == 1, cp.stdout + cp.stderr
        assert last_line(cp).startswith(
            f"NEXT: fix the [FAIL] lines above (each names its fix), then re-run: {INSTALL_SH}"
        ), fails
        assert "the report" not in last_line(cp)


@pytest.mark.parametrize("rc", ["78", "65", "1"])
def test_first_sync_failure_fails_the_run_before_the_agent(
    env: dict[str, str], folder: Path, wheel: Path, rc: str
) -> None:
    """B13 round 14 Q22: an exit 78 at the first sync is a config the sync cycle refuses (a source still
    mirrored but gone from sources.toml), which loads, so the config step passed: "fix what the first sync
    printed above ..., then re-run" sent the agent to a re-run that fails the same way, or to an edit of the
    person's sources.toml. Its NEXT names the owner, as the config error's does; another code keeps the
    re-run. B13 round 17 U55, U65: an exit 65 is a manifest damaged under a table, which status passes; it
    fell to "fix what the first sync printed above (exit 65)", handing the agent a fix that loses history."""
    e = {**env, "STUB_SYNC_RC": rc, "STUB_SYNC_OUT": "  alpha: converted 0, deferred 0 online-only"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 1
    # a failed first sync shows everything it printed, not a summary
    assert "stub cycle" in cp.stdout and "  alpha: converted 0, deferred 0 online-only" in cp.stdout
    got = calls(env)
    assert not any(c.startswith(("agentsync install-agent", "launchctl")) for c in got)
    meaning = {
        "78": "configuration invalid",
        "65": "the state file (manifest.sqlite) cannot be read: the person's to fix",
        "1": "failed",
    }[rc]
    assert f"the first sync (agentsync sync --once) exited {rc} ({meaning})" in cp.stderr
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    if rc == "78":
        assert last_line(cp) == (
            f"NEXT: the first sync refused the config {cfg} (the configuration error above; "
            "~/.local/bin/agentsync sync prints it again): fixing it is the person's, not a step for the "
            "agent, so show them the error and go on to the report; once it is fixed, the next run is: "
            f"{rerun(wheel)} [setup report: {report_path(env)}]"
        ), last_line(cp)
        assert "fix what the first sync printed above" not in cp.stdout
    elif rc == "65":
        assert last_line(cp) == (
            "NEXT: agentsync's state file cannot be read (the first sync's error above, with its fix; "
            "~/.local/bin/agentsync sync prints it again): fixing it is the person's, not a step for the "
            "agent, so show them the error and go on to the report; once it is fixed, the next run is: "
            f"{rerun(wheel)} [setup report: {report_path(env)}]"
        ), last_line(cp)
        assert "fix what the first sync printed above" not in cp.stdout + cp.stderr
    else:
        assert last_line(cp) == (
            f"NEXT: fix what the first sync printed above ({meaning}), then re-run: "
            f"{rerun(wheel)} [setup report: {report_path(env)}]"
        )
    assert got[-1].startswith("agentsync setup-report --out"), "the report is still written"
    assert steps(install_log(env))[-2:] == [
        ("first-sync", "failed", rc, "state-unreadable" if rc == "65" else ""),
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


def test_the_first_sync_shows_the_upgrades_retry_note(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """B13 round 20 X85: on exit 0 show_first_sync printed only the summary lines, so the note agentsync's
    sync prints when it gives an older build's settled failures their retries (B13 round 19 W18) stayed in
    the captured output, on the very run that moves them: an upgrade's first sync. It is shown after the
    summary with the step's label, and stays out of install.log's note, which the report reads by its fields.
    The note is in lane E's round 20 X53 words; install.sh keys on "an older build gave up on" alone."""
    note = (
        "  note: 1 conversion an older build gave up on is tried again, up to 2 times in the syncs of its "
        "source from this one on; its error may show again until then"
    )
    e = {**env, "STUB_SYNC_OUT": f"  alpha: converted 1, deferred 0 online-only\n{note}"}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    shown = "first sync: " + note.strip()
    assert out.count(shown) == 1, cp.stdout
    assert out.index("first sync: alpha: converted 1, deferred 0 online-only") < out.index(shown)
    assert ("first-sync", "done", "0", "converted-1-deferred-0") in steps(install_log(env))


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
    # B13 round 17 U68: its probe found no lock busy, so agentsync is told the wait was not announced
    assert (Path(env["STUB_LOG"] + ".announced")).read_text(encoding="utf-8").splitlines() == ["announced=0"]


def test_a_lock_wait_the_probe_missed_is_booked_from_agentsyncs_stdout_mark(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 17 U68: a sync that takes the lock after install.sh's probe found it free waits with no wait
    line of install.sh's; agentsync then says the wait itself and marks stdout (cli.LOCK_WAITED_MARK), and the
    install.log note books the wait, not conversion (round 4 Q29), with no wait line of the step's own."""
    cp = install_sh({**env, "STUB_SYNC_OUT": cli.LOCK_WAITED_MARK}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert ("first-sync", "done", "0", "waited-for-the-lock") in steps(install_log(env))
    assert "another sync is still running" not in cp.stdout
    # the literal install.sh greps for is the CLI's constant
    assert f'grep -qx "{cli.LOCK_WAITED_MARK}" "$SYNC_OUT"' in INSTALL_SH.read_text(encoding="utf-8")


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


_HELD_WARN = doctor.format_results(
    [
        doctor.CheckResult(
            "heartbeat.fy26-projects",
            False,
            "enumeration incomplete for 3 consecutive passes; deletions held",
            doctor.Severity.WARN,
            note=doctor._LISTING_YOURS_NOTE,
        )
    ]
).rstrip("\n")
"""Step 5's warn on a held listing, as doctor prints it today (B13 round 14 Q51: a hand-typed copy let the O36
check below pass whatever doctor printed). An installed agentsync from before B13 round 12 N17 bracketed the
"(deletions held)", and one from before round 13 O36 began its note "held at", which install.sh's anchor
matches too: those two are typed out, since they stand for older builds and must not follow today's doctor."""
_HELD_WARN_BEFORE_O36 = (
    "[warn] heartbeat.fy26-projects — enumeration incomplete for 3 consecutive passes; deletions held (held "
    "at its last walk: the end of this run says whether this run's sync cleared it)"
)
_HELD_WARN_BEFORE_N17 = (
    "[warn] heartbeat.fy26-projects — enumeration incomplete for 3 consecutive passes (deletions held) (held "
    "at its last walk: the end of this run says whether this run's sync cleared it)"
)
_CLEARED = "heartbeat.fy26-projects: this run's sync listed it in full; deletions are no longer held"


@pytest.mark.parametrize(
    "warn",
    [_HELD_WARN, _HELD_WARN_BEFORE_O36, _HELD_WARN_BEFORE_N17],
    ids=["one bracket", "before O36", "before N17"],
)
@pytest.mark.parametrize("case", ["cleared", "still held", "fail skips the sync", "lock busy"])
def test_a_listing_the_first_sync_cleared_is_said_and_never_inferred(
    env: dict[str, str], folder: Path, wheel: Path, case: str, warn: str
) -> None:
    """B13 round 6 G33: the status step warns that a listing was held at its last walk; when this run's sync
    clears it, the end of the run says so in a line of its own, rather than leaving the reader to infer it
    from a WAITING ON YOU line that is missing. Still held: the wait, and no such line. A [FAIL] that skips
    the sync: neither. A busy lock that skips it: neither, though the lock's holder listed it in full, which
    this run's sync did not (B13 round 14 Q35)."""
    ok = "[ok  ] heartbeat.fy26-projects — last success 2026-10-13T10:00:00Z (0s ago, full_enumeration)"
    wait = "WAITING ON YOU: 1 empty cloud folder(s) keep the listing of fy26-projects incomplete (stub)"
    e = {**env, "STUB_DOCTOR_OUT": warn}
    if case in ("cleared", "lock busy"):
        e["STUB_STATUS_OUT"] = f"NEXT: {LOOP_NEXT}\n{ok}"
        if case == "lock busy":
            e["STUB_SYNC_RC"] = "75"
    elif case == "still held":
        e["STUB_STATUS_OUT"] = f"NEXT: {LOOP_NEXT}\n{wait}\n" + re.sub(
            r" \((as of|held at) its last walk.*", "", warn
        )
    else:
        fail = "[FAIL] docs_repo.git — /x/docs does not exist and /x is not writable (fix: mkdir -p /x/docs)"
        e |= {"STUB_DOCTOR_OUT": f"{warn}\n{fail}", "STUB_DOCTOR_RC": "1", "STUB_STATUS_OUT": ok}
    cp = install_sh(e, str(wheel), "--source-local", str(folder))
    lines = cp.stdout.splitlines()
    assert (_CLEARED in lines) is (case == "cleared"), cp.stdout + cp.stderr
    assert (wait in lines) is (case == "still held"), cp.stdout
    if case == "cleared":
        assert lines.index(_CLEARED) < len(lines) - 1 and one_next(cp)
        assert last_line(cp).startswith(f"NEXT: {LOOP_NEXT}")
    # B13 round 13 O36: "deletions held (held at its last walk ..." used the word in two senses
    if warn == _HELD_WARN:
        assert [ln.count("held") for ln in lines if ln.startswith("[warn] heartbeat.fy26-projects")] == [1]


_UNANSWERED = (
    "heartbeat.fy26-projects: this run ended before its closing status, so it does not say whether its sync "
    "cleared it: "
)


@pytest.mark.parametrize(
    "case",
    [
        "sync 1",
        "sync 80",
        "sync 78",
        "wait timeout",
        "lock busy, wait fails",
        "dry run",
        "fail skips",
        "early",
    ],
)
def test_a_run_that_ends_before_its_closing_status_says_a_held_listing_has_no_answer(
    env: dict[str, str], folder: Path, wheel: Path, case: str
) -> None:
    """B13 round 15 S64, S11: step 5's held-listing note says the end of this run answers a sync that
    succeeds, but a first sync that exits 1, 80 or 78, or a background wait that fails after a sync that
    exited 0, ends the run before its closing status, and nothing at the end named the listing. Such a run
    says so per listing, before the issue link and NEXT, and in install.out; a run whose sync listed nothing
    (a busy lock, a dry run, a [FAIL] that skips it) or that ended before step 6 prints no such line."""
    wait_env = {"STUB_LC_RUNNING": "1", "AGENTSYNC_WAIT_SECONDS": "1", "AGENTSYNC_WAIT_POLL_SECONDS": "0.5"}
    e = {**env, "STUB_DOCTOR_OUT": _HELD_WARN}
    args = [str(wheel), "--source-local", str(folder)]
    if case.startswith("sync "):
        e["STUB_SYNC_RC"] = case.removeprefix("sync ")
    elif case == "wait timeout":
        e |= wait_env
        args.append("--confirm-install-agent")
    elif case == "lock busy, wait fails":
        e |= {**wait_env, "STUB_SYNC_RC": "75"}
        args.append("--confirm-install-agent")
    elif case == "dry run":
        e["AGENTSYNC_INSTALL_DRY_RUN"] = "1"
    elif case == "fail skips":
        fail = "[FAIL] docs_repo.git — /x/docs does not exist and /x is not writable (fix: mkdir -p /x/docs)"
        e |= {"STUB_DOCTOR_OUT": f"{_HELD_WARN}\n{fail}", "STUB_DOCTOR_RC": "1"}
    else:
        e["STUB_UV_INSTALL_RC"] = "2"
    cp = install_sh(e, *args)
    lines = cp.stdout.splitlines()
    said = [i for i, ln in enumerate(lines) if ln.startswith(_UNANSWERED)]
    assert one_next(cp) and last_line(cp).startswith("NEXT: "), cp.stdout + cp.stderr
    assert not any(ln.startswith("heartbeat.fy26-projects: this run's sync listed it") for ln in lines)
    if case in ("lock busy, wait fails", "dry run", "fail skips", "early"):
        assert not said, cp.stdout
        assert "unbound variable" not in cp.stderr and "command not found" not in cp.stderr, cp.stderr
        return
    assert cp.returncode != 0 and len(said) == 1, cp.stdout + cp.stderr
    assert lines[said[0]].endswith("/.local/bin/agentsync status does"), lines[said[0]]  # the full path
    assert said[0] < len(lines) - 2, "before the issue link and NEXT"
    assert _UNANSWERED in install_out(env).read_text(), "install.out holds it, so the report does"


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
    assert re.search(r"^- macOS: \d+(\.\d+)+ \(\w+\)$", text, re.M), (
        "version and build, as agentsync's report prints them (B13 round 12 N26, round 13 O72)"
    )
    assert "- arch: " in text and "MDM enrollment" in text and "- proxy environment: " in text
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
        "- ~/Library/CloudStorage providers: 1 (OneDrive: 1) and 1 symlink and 1 other entry\n"
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
        "- source excludes beyond agentsync's own: (source 1) 2 (1 folder glob); 0 on the other 1 "
        "source (globs not shown; an excluded folder is not mirrored if it later gains files)\n" in text
    ), text
    assert "Empty Subfolder" not in text and "*.bak" not in text
    # a hand-written list over several lines, with a bracket inside a glob
    config.write_text(
        '[[source]]\nid = "a"\nkind = "local"\npath = "/x"\n'
        'exclude = [\n  "[Tt]emp/",\n  "/Empty Subfolder/",\n]\n',
        encoding="utf-8",
    )
    assert install_sh(env, "--report-only").returncode == 0
    assert "- source excludes beyond agentsync's own: (source 1) 2 (1 folder glob) (globs" in report_path(
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


ORIGIN_URLS = (
    "https://github.com/renchris/agent-context-sync.git",
    "https://github.com/renchris/agent-context-sync",
    "https://github.com/renchris/agent-context-sync/",
    "git@github.com:renchris/agent-context-sync",
    "git@github.com:renchris/agent-context-sync.git",
    "ssh://git@github.com/Renchris/Agent-Context-Sync/",
    "git://github.com/renchris/agent-context-sync.git",
    "https://github.com/someone/agent-context-sync.git",
    "https://GitHub.com/renchris/agent-context-sync.git",
    "https://git.contoso-mirror.invalid/contoso/agent-context-sync.git",
    "github.com:renchris/agent-context-sync",
    "",
)


def test_the_shell_reports_origin_label_is_the_agentsync_reports() -> None:
    """B13 round 18 V29: install.sh's origin_label gives the label setup_report.origin_label gives, URL for
    URL (https, ssh and scp forms, a fork, a mirror, a host in capitals, no URL)."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    func = re.search(r"^origin_label\(\) \{.*?^\}\n", text, re.M | re.S)
    assert func
    for url in ORIGIN_URLS:
        shell = subprocess.run(
            [BASH32, "-c", func.group(0) + 'origin_label "$1"', "-", url],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert shell == setup_report.origin_label(url), url


@pytest.mark.parametrize(
    "origin",
    [
        "mirror",
        "github",
        "no-url",
        "no-checkout",
        "rewrite-mirror",
        "rewrite-one-line",
        "rewrite-empty",
        "rewrite-same",
        "rewrite-longest",
        "push-rewrite",
    ],
)
def test_the_shell_report_has_the_install_source_line_the_git_quote_promises(
    env: dict[str, str], origin: str
) -> None:
    """B13 round 18 V29 (probes am, remsh): the setup prompt's git quote says the report's install source line
    says ``origin: other`` when the checkout's remote is not GitHub, and only the agentsync report had that
    line: on a new Mac with a mirror checkout and no agentsync, the shell report said nothing of it. It now
    has the line, read from the checkout's own config files (no git process), never the URL.

    B13 round 19 W7, W23: a URL rewrite (insteadOf) that sends a GitHub origin to a mirror is said beside the
    label, as the agentsync report says it; one that keeps the label (git@github.com:) or only rewrites a
    push says nothing; the mirror's host never shows."""
    home = Path(env["HOME"])
    dot = home / "src" / "agent-context-sync" / ".git"
    url = {
        "mirror": "https://git.contoso-mirror.invalid/contoso/agent-context-sync.git",
        "github": "git@github.com:renchris/agent-context-sync.git",
    }.get(origin, "")
    if origin.startswith("rewrite") or origin == "push-rewrite":
        url = "https://github.com/renchris/agent-context-sync.git"
        rules = {
            "rewrite-mirror": '[url "https://git.contoso-mirror.invalid/m/"]\n\tinsteadOf = https://github.com/\n',
            # a section and its key on one line, and an empty value, which git applies to every URL
            "rewrite-one-line": '[url "https://git.contoso-mirror.invalid/m/"] insteadOf = https://github.com/\n',
            "rewrite-empty": '[url "https://git.contoso-mirror.invalid/m/"]\n\tinsteadOf =\n',
            "rewrite-same": '[url "git@github.com:"]\n\tinsteadOf = https://github.com/\n',
            # git takes the longest insteadOf value that starts the URL: here the one that keeps GitHub
            "rewrite-longest": '[url "https://git.contoso-mirror.invalid/m/"]\n\tinsteadOf = https://github.com/\n'
            '[url "git@github.com:renchris/"]\n\tinsteadOf = https://github.com/renchris/\n',
            "push-rewrite": '[url "https://git.contoso-mirror.invalid/"]\n\tpushInsteadOf = https://github.com/\n',
        }[origin]
        (home / ".gitconfig").write_text(rules, encoding="utf-8")
    if origin != "no-checkout":
        dot.mkdir(parents=True)
        remote = f'[remote "origin"]\n\turl = {url}\n' if url else ""
        (dot / "config").write_text(f"[core]\n\tbare = false\n{remote}", encoding="utf-8")
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "(shell fallback: agentsync is not installed)" in cp.stdout
    text = report_path(env).read_text(encoding="utf-8")
    summary = text.split("\n## Summary\n", 1)[1].split("\n## ", 1)[0]
    label = {"mirror": "other (redacted)", "github": "github.com/renchris/agent-context-sync"}.get(
        origin, "github.com/renchris/agent-context-sync" if url else "none"
    )
    if origin in ("rewrite-mirror", "rewrite-one-line", "rewrite-empty"):
        label += " (a URL rewrite sends it elsewhere)"
    if origin == "no-checkout":
        assert "- install source: no checkout at ~/src/agent-context-sync\n" in summary, text
    else:
        assert f"- install source: checkout ~/src/agent-context-sync · origin: {label}\n" in summary, text
    assert "contoso-mirror" not in text


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
    # B13 round 12 N16: the body shows OneDrive-<org-1> and no email address, so the item names no other
    assert (
        "- <org-N>: the organization or team after the app's name in a CloudStorage folder "
        "(OneDrive-<org-N>)\n- <mdmhost>: the MDM server's host\n"
    ) in note


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
        "and every word bash wrote in its dollar-quote form ($'...'), which install.log uses for a name with "
        "a tab or another control character, a curly apostrophe, an en dash or an accent (under the C "
        "locale, any character outside ASCII)"
    ), "the Redaction text is not itself redacted, and reads plainly (B13 R7)"

    # B13 round 11 M3: no name every drive has is in this report, so its rule is not explained
    assert "such as Documents" not in item["<folder-N>"], item["<folder-N>"]


def test_the_path_legend_says_only_a_relative_argument_is_one(env: dict[str, str]) -> None:
    """B13 round 21 Y8: the <path> item said "a --source-local argument in install.log that names no
    CloudStorage folder", but a ``~/`` or absolute argument is never <path> (a configured one shows as
    ``~/Projects/<folder-N>``): only a relative one is, and a TAB name's ``$'...'`` word was in no example."""
    home = Path(env["HOME"])
    ledger = home / "Projects" / "Contoso Ledger"
    ledger.mkdir(parents=True)
    cfg = home / "agent-context" / "sources.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[[source]]\nid = "s"\nkind = "local"\npath = "{ledger}"\n')
    typed = ("Tailspin Notes", "~/Projects/Contoso Ledger", "~/Projects/Zephyr\tQuarry")
    words = subprocess.run(  # as install.sh's log_start writes them: bash 3.2 leaves a leading ~ bare
        [BASH32, "-c", 'printf "%q\\n" "$@"', "_", *typed], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert words[2].startswith("$'"), words
    setup = home / "agent-context" / "setup"
    setup.mkdir(parents=True)
    head = "run=20261010T010000Z-1 start install.sh compat=10 commit=- kind=- source=- args=--source-local "
    (setup / "install.log").write_text(
        "".join(f"2026-10-10T0{i}:00:00Z {head}{w}\n" for i, w in enumerate(words, 1))
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text()
    assert text.count("args=--source-local <path>\n") == 2, "the relative word and the TAB word"
    assert re.search(r"args=--source-local ~/Projects/<folder-\d>\n", text), text
    for name in ("Tailspin", "Ledger", "Zephyr"):
        assert name not in text, name
    legend = text.split("\n## Redaction\n", 1)[1].split("\n\n")[0].strip().splitlines()
    assert max(map(len, legend)) <= 115, legend
    item = " ".join(" ".join(legend).split(" - <path>: ", 1)[1].split(" - ", 1)[0].split())
    assert item.startswith(
        "a relative --source-local argument in install.log (which records no working directory) whose first "
        "part is not a CloudStorage entry, and every word bash wrote in its dollar-quote form ($'...'),"
    ), item
    assert "tab or another control character" in item and "names no CloudStorage folder" not in item, item


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
    assert (
        "numbered as in the shell fallback report, so a number can be missing;"
        in setup_report._LEGEND["proxy"]
    )


def test_both_legends_say_a_resolver_errors_host_need_not_be_a_proxy(env: dict[str, str]) -> None:
    """B13 round 12 N47: since round 11 M29 both reports number a host a friction line's resolver error names
    ("Could not resolve host: ...", "Failed to connect to ... port N") as a `<proxy-N>`, and the pattern also
    catches a company git server that is no proxy. Both legends called every `<proxy-N>` a proxy host."""
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(
        "Attempt: 2026-10-16T00:00:00Z\nPrompt: v20\nAgent: x\n2026-10-16T00:00:01Z | step 1 | failure | "
        "fatal: unable to access: Could not resolve host: git.contoso.example | -\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "git.contoso.example" not in text and "<proxy-" in text.split("\n## Redaction\n", 1)[0]
    legend = " ".join(text.split("\n## Redaction\n", 1)[1].split("\n\n")[0].split())
    # B13 round 16 T88: and a git remote's host
    said = (
        "also a host a friction line's resolver error names, or a git remote's host (neither need be a proxy)"
    )
    assert said in legend, legend
    assert setup_report._LEGEND["proxy"].endswith(f"so a number can be missing; {said}")


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
    assert "Sections 2 and 3 are not redacted: they may name a real folder, file or person" in text
    # B13 round 8 J65: a person's own kept commit brings its From: line, which the header now names
    assert " ".join(text.split("\n## 1. Setup report", 1)[0].split()).endswith(
        "review them before sending. Section 3 sends one patch; it starts with its "
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
        (
            True,
            None,
            "Section 2 is not redacted: it may name a real folder, file or person, so review it before "
            "sending.",
        ),
        (
            False,
            "new",
            "Section 3 is not redacted: it may name a real folder, file or person, so review it before "
            "sending.",
        ),
        (
            True,
            "new",
            "Sections 2 and 3 are not redacted: they may name a real folder, file or person, so review them "
            "before sending.",
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
    only with a patch embedded. B13 round 11 M1: the sentence said a section "names real folders and files",
    though the prompt has the agent name things by role, so it says the section "may name" one."""
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
    assert ("Section 3 sends one patch; it starts with its author's name" in head) == (patch == "new")
    assert ("not repeated: a patch file" in text) == (patch == "old")


def _failing_tool(env: dict[str, str], tool: str, pattern: str) -> None:
    """A ``tool`` first on PATH that fails for any argument holding ``pattern`` and is /bin's otherwise."""
    _write_exe(
        Path(env["PATH"].split(":")[0]) / tool,
        f'#!/bin/bash\nfor a in "$@"; do case "$a" in *{pattern}*) exit 1 ;; esac; done\n'
        f'exec /bin/{tool} "$@"\n',
    )


@pytest.mark.parametrize(
    ("tool", "pattern"),
    [("mv", "/.bring-back."), ("chmod", "/.bring-back."), ("mv", "/.setup-report.")],
)
def test_a_report_writer_that_cannot_finish_leaves_no_temp_file(
    env: dict[str, str], tool: str, pattern: str
) -> None:
    """B13 round 10 L4 removed bring-back.md's temp file when its chmod or mv fails: it holds the unredacted
    fix request and patches. Its note said a test "cannot be forced in a sandbox"; a PATH stub forces it
    (B13 round 11 M84). The shell report's own temp file (redacted) is removed the same way."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True)
    (setup / "fix-request.md").write_text("- OCR scanned PDFs\n", encoding="utf-8")
    _keep_patch(env, "2026-10-07T16:00:00Z")
    _failing_tool(env, tool, pattern)
    install_sh(env, "--report-only")
    left = [p.name for p in report_path(env).parent.iterdir() if p.name.startswith(pattern[1:-1])]
    assert left == [], left
    if pattern == "/.bring-back.":
        assert not (report_path(env).parent / "bring-back.md").exists()


@pytest.mark.parametrize("earlier", [False, True])
@pytest.mark.parametrize("private", [False, True])
def test_a_bring_back_file_that_cannot_be_written_is_not_named_as_the_one_to_review(
    env: dict[str, str], tmp_path: Path, earlier: bool, private: bool
) -> None:
    """B13 round 12 N71: write_bring_back's failure was a warning only, and the NEXT still said "review
    ~/agent-context/bring-back.md and copy that one file back", a file that was missing or an earlier
    attempt's. It names what to copy back instead: the setup report, with the issue only where a link was
    printed, and the fix request and kept work as private; an earlier file is said to be that, and left as
    it is."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    old = tmp_path / "report.md"
    old.write_text(
        "# agentsync setup report\n\n## Redaction\n\n"
        + "Private only: one of your folder, library or organization names is ...\n" * private
        + f"Send it: ...\n\n{ISSUE_LINK}\n"
    )
    back = report_path(env).parent / "bring-back.md"
    if earlier:
        back.parent.mkdir(parents=True, exist_ok=True)
        back.write_text("an earlier attempt's bring-back\n")
    _failing_tool(env, "mv", "/.bring-back.")
    cp = install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    # B13 round 14 Q19: mv printed its own error, and the warning says so
    assert f"warning: could not write {back}: chmod or mv failed (its error is above)\n" in cp.stderr
    setup = back.parent / "setup"
    # B13 round 13 O14: the earlier-file note is inside the one bracket, and the path is printed once
    held = (
        "; the one there now is an earlier attempt's file, not this one's, so do not send it"
        if earlier
        else ""
    )
    link = "" if private else ", or paste it into the issue the link above opens"
    assert last_line(cp) == (
        f"NEXT: {back} could not be written (see the warning above{held}): copy {report_path(env)} back "
        f"privately{link}, and if {setup}/fix-request.md or {setup}/local-work holds anything, copy it back "
        "privately too (it is not redacted); or fix the cause and run this command again (nothing is sent "
        f"for you) [setup report: {report_path(env)}]"
    ), last_line(cp)
    assert "review " not in last_line(cp) and ") (" not in last_line(cp)
    assert last_line(cp).count(str(back)) == 1
    assert back.exists() == earlier and (
        not earlier or back.read_text() == "an earlier attempt's bring-back\n"
    )


@pytest.mark.parametrize("shape", ["a folder", "a link to a folder"])
def test_a_folder_at_the_bring_back_path_takes_no_file(
    env: dict[str, str], tmp_path: Path, shape: str
) -> None:
    """B13 round 13 O7, O26 (one defect): mv onto a folder moves the file into it and exits 0, so the run
    printed "bring back: .../bring-back.md" and the usual NEXT, and the unredacted file sat in the folder as a
    hidden .bring-back.* file. A folder there, or a link to one, is now a bring-back.md that could not be
    written: N71's NEXT, with no earlier-file clause, and no temp file in it or beside it."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    back = report_path(env).parent / "bring-back.md"
    setup = back.parent / "setup"
    setup.mkdir(parents=True)
    (setup / "fix-request.md").write_text("- OCR scanned PDFs\n", encoding="utf-8")
    _keep_patch(env, "2026-10-07T16:00:00Z")
    inside = back if shape == "a folder" else tmp_path / "elsewhere"
    inside.mkdir()
    if shape != "a folder":
        back.symlink_to(inside)
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    # B13 round 14 Q19: the warning names the cause the NEXT says to fix
    assert (
        f"warning: could not write {back}: a folder, or a link to a folder, is at that path; move it away\n"
        in cp.stderr
    ), cp.stderr
    assert "bring back: " not in cp.stdout
    assert last_line(cp).startswith(f"NEXT: {back} could not be written (see the warning above): copy "), (
        last_line(cp)
    )
    assert "review " not in last_line(cp) and "earlier attempt" not in last_line(cp)
    assert list(inside.iterdir()) == [], "nothing is moved into the folder"
    assert [p.name for p in back.parent.iterdir() if p.name.startswith(".bring-back")] == []


def test_a_stopped_copy_note_that_cannot_finish_leaves_no_temp_file(env: dict[str, str]) -> None:
    """B13 round 11 M84: stopped_report_note's mv had no cleanup, so a failed one left a .stopped-report.*
    copy beside the report it was to replace."""
    assert install_sh(env, "--log-start", "x").returncode == 2
    _failing_tool(env, "mv", "/.stopped-report.")
    install_sh(env, "--report-only")
    setup = report_path(env).parent / "setup"
    assert (setup / "stopped-copy-report.md").exists()
    assert not [p.name for p in setup.iterdir() if p.name.startswith(".stopped-report.")]


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


def test_step_2_says_a_patch_this_account_cannot_read_is_the_persons(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 18 V25 (probe O25b): step 2's run writes bring-back.md too, and printed the report's
    ``warning: 1 patch file ... could not be read`` line, a form the setup prompt's step 2 does not place. It
    is a WAITING ON YOU: line there, the person's, with its fix and no file name; step 2's NEXT, which drives
    the setup, is as without it. The report's run keeps the warning its NEXT points to. B13 round 19 W8, W12:
    it said the report "sends" the patch, against the report's own "nothing is sent for you"; it puts it in
    bring-back.md, named by the path this run writes."""
    before = install_sh(env, str(wheel), "--source-local", str(folder))
    assert before.returncode == 0, before.stdout + before.stderr
    patch = _keep_patch(env, "2026-10-07T16:00:00Z", body="+def contoso(): ...\n", name="0002-x.patch")
    patch.chmod(0)
    try:
        cp = install_sh(env, str(wheel))
    finally:
        patch.chmod(0o600)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (
        "WAITING ON YOU: 1 patch file in ~/agent-context/setup/local-work cannot be read by this account; "
        "make it readable (chmod u+r), and the setup report at the end of this setup puts it in "
        "~/agent-context/bring-back.md for you to copy back\n"
    ) in cp.stdout, cp.stdout
    assert "could not be read" not in cp.stderr and "0002-x" not in cp.stdout + cp.stderr, cp.stderr
    assert " sends " not in cp.stdout, cp.stdout
    assert last_line(cp) == last_line(install_sh(env, str(wheel))), "the NEXT is step 2's own"
    # two patches, and a report path of its own: the line names the file this run writes
    other = Path(env["HOME"]) / "reports"
    other.mkdir()
    second = _keep_patch(env, "2026-10-07T17:00:00Z", body="+y = 2\n", name="0003-y.patch")
    for p in (patch, second):
        p.chmod(0)
    try:
        cp = install_sh({**env, "AGENTSYNC_SETUP_REPORT": str(other / "setup-report.md")}, str(wheel))
    finally:
        for p in (patch, second):
            p.chmod(0o600)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (
        "WAITING ON YOU: 2 patch files in ~/agent-context/setup/local-work cannot be read by this account; "
        "make them readable (chmod u+r), and the setup report at the end of this setup puts them in "
        "~/reports/bring-back.md for you to copy back\n"
    ) in cp.stdout, cp.stdout


@pytest.mark.parametrize(("readable", "locked_n"), [(False, 1), (True, 1), (False, 2), (True, 2)])
def test_a_patch_this_account_cannot_read_is_counted_not_sent(
    env: dict[str, str], readable: bool, locked_n: int
) -> None:
    """B13 round 13 O25: a mode-000 patch opened section 3's fence and was counted before its cat failed
    unseen, so bring-back.md held an empty diff block under a header saying section 3 holds one patch per
    kept commit, with no warning. Such a patch is now counted on a "not sent:" line (no file name: it
    carries the commit's subject) and in a warning; a readable one beside it is still sent in full.
    B13 round 15 S14: the count is known, so the lines say "1 patch file ... it" or "2 patch files ... them",
    never "(s)"."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    locked = [
        _keep_patch(env, "2026-10-07T16:00:00Z", body="+def contoso(): ...\n", name=f"000{n}-x.patch")
        for n in range(2, 2 + locked_n)
    ]
    if readable:
        _keep_patch(env, "2026-10-07T16:00:00Z")
    for path in locked:
        path.chmod(0)
    try:
        cp = install_sh(env, "--report-only")
    finally:
        for path in locked:
            path.chmod(0o600)
    assert cp.returncode == 0, cp.stderr
    shown = "~/agent-context/setup/local-work"
    setup = home / "agent-context" / "setup"
    files, them = ("1 patch file", "it") if locked_n == 1 else (f"{locked_n} patch files", "them")
    assert (
        f"warning: {files} in {setup}/local-work could not be read, so "
        f"{report_path(env).parent / 'bring-back.md'} does not hold {them}"
    ) in cp.stderr
    text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    head, section3 = text.split("\n## 1. Setup report", 1)[0], text.split("## 3. Local work", 1)[1]
    assert section3.rstrip("\n").endswith(
        f"not sent: {files} in {shown} this account cannot read; make {them} readable (chmod u+r) and "
        "run ~/src/agent-context-sync/scripts/install.sh --report-only again"
    ), section3
    assert "contoso" not in section3 and "0002-x" not in section3 and "(s)" not in text.split("## 1.")[0]
    kept = "1 kept patch file" if locked_n == 1 else f"{locked_n} kept patch files"
    assert (
        f"does not hold {kept} this account cannot read (see the warning above): make {them} readable "
        "(chmod u+r on the file" in last_line(cp)
    ), last_line(cp)
    if readable:
        assert "~diff\n+def ocr(): ...\n~~~" in section3 and section3.count("~diff\n") == 1
        assert "Section 3 sends one patch; it starts with its author's name" in " ".join(head.split())
        # B13 round 14 Q29: the remedy is in the header beside a patch that was sent, too
        assert (
            f"Section 3 does not hold {files} this account cannot read; make {them} readable "
            "(chmod u+r) and run the report again (install.sh --report-only), as its last line says."
            in " ".join(head.split())
        ), head
    else:
        assert "diff" not in section3 and "\nnone\n" not in section3
        assert "Section 3 sends one patch" not in " ".join(head.split())
        assert (
            f"Section 3 sends no patch: {files} could not be read; make {them} readable (chmod u+r) "
            "and run the report again (install.sh --report-only), as its last line says."
            in " ".join(head.split())
        ), head
        assert "Section 3 is not redacted" not in " ".join(head.split())


def _keep_patch(
    env: dict[str, str], written: str, body: str = "+def ocr(): ...\n", name: str = PATCH_NAME
) -> Path:
    """A patch in the setup folder's local-work, last written at ``written`` (UTC)."""
    patch = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / name
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(body, encoding="utf-8")
    os.utime(patch, (_stamp(written), _stamp(written)))
    return patch


@pytest.mark.parametrize("beside", ["one sent before", "two sent before", "a long tilde line"])
def test_an_unreadable_patch_leaves_the_rest_of_section_3_right(env: dict[str, str], beside: str) -> None:
    """Review of B13 round 13 O25: beside a patch skipped by its hash, the header still said section 3's
    patch was in an earlier report, though one was unread; and the unreadable file stopped fence_for's awk,
    so the fence fell back to 40 tildes, which a readable patch's own 40-tilde line closed early.
    B13 round 15 S24: the remedy and the patches listed by their hash were one sentence of two colons, which
    said "the others ... their hash" of one patch; the hash sentence is its own now, counted."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    if beside.endswith("sent before"):
        _two_attempts(env)
        _keep_patch(env, "2026-10-06T16:00:00Z")
        if beside == "two sent before":
            _keep_patch(env, "2026-10-06T16:00:00Z", name="0001-older.patch")
    else:
        _keep_patch(env, "2026-10-07T16:00:00Z", body="+" + "~" * 39 + "\n" + "~" * 40 + "\n")
    locked = _keep_patch(env, "2026-10-07T16:00:00Z", name="0002-x.patch")
    locked.chmod(0)
    try:
        text = _local_work(env)
        whole = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    finally:
        locked.chmod(0o600)
    head = " ".join(whole.split("\n## 1. Setup report", 1)[0].split())
    if beside.endswith("sent before"):
        assert "Section 3 sends no patch this time" not in head, head
        others = (
            "The one other patch was in an earlier report and is listed by its hash."
            if beside == "one sent before"
            else "The other 2 patches were in an earlier report and are listed by their hash."
        )
        assert head.endswith(
            "Section 3 sends no patch: 1 patch file could not be read; make it readable (chmod u+r) "
            "and run the report again (install.sh --report-only), as its last line says. " + others
        ), head
        assert "not repeated: " in text and "not sent: 1 patch file in" in text
    else:
        fence = text.split("diff\n", 1)[0]
        assert set(fence) == {"~"} and len(fence) == 41, fence
        assert text.count(fence + "\n") == 1 and "~" * 40 + "\n" in text


def _busy_evidence_line() -> str:
    """The Summary's evidence line when its one gap is a state file a sync held (B13 round 20 X37)."""
    r = setup_report._Run.__new__(setup_report._Run)
    r.facts = setup_report._Facts()
    r.facts.evidence = 1
    r.facts.evidence_why["the state file is busy: a sync holds it"] += 1
    return setup_report._evidence_line(r)


@pytest.mark.parametrize("cause", ["slow doctor", "busy state file"])
@pytest.mark.parametrize("locked", [False, True])
def test_a_report_the_time_limit_cut_off_says_so_in_its_next_line_beside_the_review(
    env: dict[str, str], tmp_path: Path, locked: bool, cause: str
) -> None:
    """B13 round 19 W3: a report whose doctor call the time limit cut off asked for the report again only
    inside it (``setup_report._IDLE_AGAIN``), while the NEXT the agent relays was the plain review line.
    The NEXT says so too, as advice beside the review (a doctor that is always slow must not hold the
    bring-back), naming the command; with an unreadable kept patch it comes before that remedy's words.

    B13 round 20 X37: the NEXT said "the report's time limit cut part of it off", but the marker is also in
    an evidence line whose only gap is a state file a sync held, and round 19 W4 calls a slow doctor's cut
    "the wait the report gives a slow doctor", not the time limit. The clause names no cause; the Summary
    does. Keying on the cut-off words alone would drop the advice a busy state file needs."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    old = tmp_path / "report.md"
    summary = (
        "- doctor: cut off after 20.0s, the wait the report gives "
        f"a slow doctor (see Doctor) · {setup_report._IDLE_AGAIN}"
        if cause == "slow doctor"
        else _busy_evidence_line()
    )
    assert setup_report._IDLE_AGAIN in summary and "time" not in summary.replace("this Mac", ""), summary
    old.write_text(
        f"# agentsync setup report\n\n## Summary\n\n{summary}\n\n## Redaction\n\n"
        "Private only: one of your folder, library or organization names is ...\n"
    )
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-07T15:58:32Z\nPrompt: v{COMPAT}\nAgent: x\n")
    patch = _keep_patch(env, "2026-10-07T15:58:27Z") if locked else None
    back = report_path(env).parent / "bring-back.md"
    if patch is not None:
        patch.chmod(0)
    try:
        cp = install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only")
    finally:
        if patch is not None:
            patch.chmod(0o600)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    head = (
        "NEXT: part of the report was cut off or not measured (its Summary says which, and why): if this Mac "
        "was busy, run ~/src/agent-context-sync/scripts/install.sh --report-only again once it is idle; "
        "either way, "
    )
    rest = (
        f"{back} does not hold 1 kept patch file" if locked else f"review {back} and copy that one file back"
    )
    assert last_line(cp).startswith(head + rest), last_line(cp)
    assert " (nothing is sent for you)" in last_line(cp), last_line(cp)
    assert "time limit" not in last_line(cp), last_line(cp)
    old.write_text(old.read_text().replace(setup_report._IDLE_AGAIN, "nothing cut off"))
    cp = install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only")
    assert "cut off or not measured" not in last_line(cp), "a report nothing cut off keeps its NEXT"


def test_two_unreadable_patches_are_counted_by_their_number(env: dict[str, str]) -> None:
    """B13 round 15 S65: the warning beside a known count read "1 patch file(s) ... does not hold them"; it
    takes the noun and pronoun of its count, two here (one is the test above)."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    locked = [
        _keep_patch(env, "2026-10-07T16:00:00Z", body="+def contoso(): ...\n", name=f"000{i}-x.patch")
        for i in (2, 3)
    ]
    for kept in locked:
        kept.chmod(0)
    try:
        cp = install_sh(env, "--report-only")
    finally:
        for kept in locked:
            kept.chmod(0o600)
    setup = home / "agent-context" / "setup"
    back = report_path(env).parent / "bring-back.md"
    assert (
        f"warning: 2 patch files in {setup}/local-work could not be read, so {back} does not hold them"
        in (cp.stderr)
    ), cp.stderr
    assert last_line(cp).startswith(
        f"NEXT: {back} does not hold 2 kept patch files this account cannot read (see the warning above): "
        f"make them readable (chmod u+r on the files in {setup}/local-work)"
    ), last_line(cp)


@pytest.mark.parametrize("private", [False, True])
def test_a_patch_unreadable_at_a_report_is_sent_once_it_is_readable(
    env: dict[str, str], tmp_path: Path, private: bool
) -> None:
    """B13 round 14 Q20, Q29 (probe bw2): with a mode-000 patch the NEXT still said "review bring-back.md and
    copy that one file back", with the issue link and without one. Made readable and reported in a later
    attempt, the patch was "not repeated: ... at or before an earlier attempt's report", which had not carried
    it, so it was never sent. The NEXT gives the remedy first, and the later report sends the patch."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    old = tmp_path / "report.md"
    old.write_text(
        "# agentsync setup report\n\n## Redaction\n\n"
        + "Private only: one of your folder, library or organization names is ...\n" * private
        + f"Send it: ...\n\n{ISSUE_LINK}\n"
    )
    e = {**env, "STUB_REPORT_FILE": str(old)}
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-07T15:58:32Z\nPrompt: v{COMPAT}\nAgent: x\n")
    patch = _keep_patch(env, "2026-10-07T15:58:27Z")
    back, setup = report_path(env).parent / "bring-back.md", report_path(env).parent / "setup"
    link = "" if private else ", or paste the setup report into the issue the link above opens"
    patch.chmod(0)
    try:
        cp = install_sh(e, "--report-only")
    finally:
        patch.chmod(0o600)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert last_line(cp).startswith(
        f"NEXT: {back} does not hold 1 kept patch file this account cannot read (see the warning above): "
        f"make it readable (chmod u+r on the file in {setup}/local-work) and run "
        f"~/src/agent-context-sync/scripts/install.sh --report-only again, then review {back} and copy that "
        f"one file back privately{link} (nothing is sent for you)"
    ), last_line(cp)
    # B13 round 18 V17, V79: relayed, "this command" named none; still no file name (it carries a subject)
    assert patch.name not in cp.stdout + cp.stderr and "this command" not in last_line(cp)
    # that report's own time, and its attempt's end, as if it ran a day before this one: every run here is
    # in one second, and the record and the end are compared by the second
    notsent = setup / "local-work" / ".not-sent"
    [record] = notsent.read_text().splitlines()
    notsent.write_text(record.split(" ")[0] + " 2026-10-07T16:00:05Z\n")
    log = re.sub(
        r"(?m)^\S+ \| end \| finished$",
        "2026-10-07T16:00:00Z | end | finished",
        friction_path(env).read_text(),
    )
    friction_path(env).write_text(log + f"Attempt: 2026-10-08T09:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n")
    cp = install_sh(e, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert last_line(cp).startswith(f"NEXT: review {back} and copy that one file back privately"), last_line(
        cp
    )
    text = back.read_text(encoding="utf-8")
    assert "~diff\n+def ocr(): ...\n~~~" in text and "not repeated: " not in text, text
    assert "earlier report" not in " ".join(text.split("\n## 1. Setup report", 1)[0].split())
    assert _local_work(e) == "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n", "each report of that attempt"
    with friction_path(env).open("a", encoding="utf-8") as f:
        f.write(f"Attempt: 2026-10-09T09:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n")
    assert _local_work(e).startswith("not repeated: "), "the attempt that sent it carried it"
    patch.unlink()
    _local_work(e)
    assert not (setup / "local-work" / ".not-sent").exists(), "a patch gone leaves the list"


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


def _end_at(env: dict[str, str], stamp: str) -> None:
    """Set the friction log's last ``end | finished`` line (a ``--report-only``'s) to ``stamp``."""
    lines = friction_path(env).read_text(encoding="utf-8").splitlines(keepends=True)
    [last] = [i for i, ln in enumerate(lines) if ln.endswith(" | end | finished\n")][-1:]
    lines[last] = f"{stamp} | end | finished\n"
    friction_path(env).write_text("".join(lines), encoding="utf-8")


def _new_attempt(env: dict[str, str], stamp: str) -> None:
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    with friction_path(env).open("a", encoding="utf-8") as f:
        f.write(f"Attempt: {stamp}\nPrompt: v{COMPAT}\nAgent: x\n")


@pytest.mark.parametrize("clock", ["real", "hand-set"])
def test_a_patch_made_readable_in_the_attempt_that_could_not_read_it_is_sent_once(
    env: dict[str, str], clock: str
) -> None:
    """B13 round 15 S34: --report-only writes the end line before bring-back.md, so an unreadable patch's
    record is at or after its own attempt's end. Made readable and reported again in that attempt (Q20's
    remedy), it was sent, and its record kept its time, so the next attempt, whose earlier end is that end
    line, sent it again: the 2026-10-07 field repeat. The record now says which attempt sent it; once that
    attempt has ended the record goes and the time rule lists the patch by its hash. Within the attempt each
    report still sends it (the same section 3)."""
    _two_attempts(env)
    patch = _keep_patch(env, "2026-10-07T15:58:27Z")
    notsent = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / ".not-sent"
    sent = "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"
    patch.chmod(0)
    try:
        assert _local_work(env).startswith("not sent: 1 patch file in ")
    finally:
        patch.chmod(0o600)
    if clock == "hand-set":  # the finder's measure: the end and the record 3 s apart
        _end_at(env, "2026-10-07T16:00:00Z")
        [record] = notsent.read_text().splitlines()
        notsent.write_text(record.split(" ")[0] + " 2026-10-07T16:00:03Z\n")
    assert _local_work(env) == sent, "made readable: sent in the attempt that could not read it"
    assert _local_work(env) == sent, "a second report of the attempt writes the same section 3"
    _new_attempt(env, "2026-10-09T09:00:00Z")
    section = _local_work(env)
    assert section.startswith("not repeated: a patch file last written 2026-10-07T15:58:27Z"), section
    assert "+def ocr" not in section
    assert not notsent.exists(), "its sending attempt has ended: the record goes"


def test_a_patch_an_earlier_report_carried_is_not_listed_as_not_sent(env: dict[str, str]) -> None:
    """B13 round 15 S22 (probe bw3): a patch an earlier attempt's report carried, unreadable at a later
    report, went into .not-sent, so once readable again it was sent a second time. It is listed only when that
    report would have sent it; a new patch unreadable at its report is still sent in each report once
    readable. B13 round 16 T24: the carried one is not counted as not sent either; its line has no hash."""
    _new_attempt(env, "2026-10-06T10:00:00Z")
    old = _keep_patch(env, "2026-10-06T09:59:00Z", "+old = 1\n", "0001-old.patch")
    assert _local_work(env) == "~~~~~~~~~~diff\n+old = 1\n~~~~~~~~~~\n", "attempt 1 sends it"
    _end_at(env, "2026-10-06T10:05:00Z")
    _new_attempt(env, "2026-10-07T10:00:00Z")
    assert _local_work(env) == _not_repeated(old, "2026-10-06T09:59:00Z", "2026-10-06T10:05:00Z")
    _end_at(env, "2026-10-07T10:05:00Z")
    _new_attempt(env, "2026-10-08T10:00:00Z")
    new = _keep_patch(env, "2026-10-08T10:01:00Z", "+new = 2\n", "0002-new.patch")
    for path in (old, new):
        path.chmod(0)
    try:
        section = _local_work(env)
    finally:
        for path in (old, new):
            path.chmod(0o600)
    carried, rest = section.split("\n", 1)
    assert carried == (
        "not repeated: a patch file last written 2026-10-06T09:59:00Z, at or before an earlier attempt's "
        "report (2026-10-07T10:05:00Z); this account cannot read it now, so no line count or hash; still in "
        "~/agent-context/setup/local-work"
    ), section
    assert rest.startswith("not sent: 1 patch file in "), section
    _end_at(env, "2026-10-08T10:05:00Z")
    _new_attempt(env, "2026-10-09T10:00:00Z")
    expected = "~~~~~~~~~~diff\n+new = 2\n~~~~~~~~~~\n" + _not_repeated(
        old, "2026-10-06T09:59:00Z", "2026-10-08T10:05:00Z"
    )
    assert _local_work(env) == expected, "the carried patch is not sent again, the new one is"
    assert _local_work(env) == expected, "in each report of the attempt"


def test_a_patch_unreadable_at_two_finished_reports_is_sent_once_readable(env: dict[str, str]) -> None:
    """B13 round 15 S22: a patch no report carried stays listed while it is unreadable, through any number of
    finished attempts, so it is sent once it can be read."""
    _new_attempt(env, "2026-10-06T10:00:00Z")
    patch = _keep_patch(env, "2026-10-06T09:59:00Z")
    patch.chmod(0)
    try:
        for n, day in enumerate(("06", "07")):
            if n:
                _new_attempt(env, f"2026-10-{day}T10:00:00Z")
            assert _local_work(env).startswith("not sent: 1 patch file in ")
            _end_at(env, f"2026-10-{day}T10:05:00Z")
    finally:
        patch.chmod(0o600)
    _new_attempt(env, "2026-10-08T10:00:00Z")
    assert _local_work(env) == "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"


def _stated_stop(env: dict[str, str], stamp: str) -> None:
    """A step 1 git stop logged with the prompt's stated words and no header (an attempt step 1 ended)."""
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    with friction_path(env).open("a", encoding="utf-8") as f:
        f.write(
            f"{stamp} | step 1 | error | prompt v{COMPAT}, Contoso agent: git pull --ff-only exited 1 | -\n"
        )


def _carried_not_repeated(patch: Path, held: str) -> str:
    """The line section 3 has for a patch an earlier attempt's report embedded (``.sent``)."""
    data = patch.read_bytes()
    lines, sha = data.count(b"\n"), hashlib.sha256(data).hexdigest()[:12]
    return (
        f"not repeated: a patch file an earlier attempt's report held ({held}): {lines} lines, sha256 {sha}; "
        "still in ~/agent-context/setup/local-work\n"
    )


@pytest.mark.parametrize("before", ["no earlier attempt", "a finished attempt"])
def test_a_report_of_an_attempt_with_no_header_is_counted_as_carrying_its_patches(
    env: dict[str, str], before: str
) -> None:
    """B13 round 15 S21 (Variant R, probe bw3): an attempt step 1 stopped writes no header and so no end
    line, so the time rule never saw its report, and the next session's report sent its patches again.
    local-work/.sent keeps each embedded patch's identity and the first report that embedded it; a later
    attempt's report lists such a patch by its hash. Two reports in one attempt both send it."""
    if before == "a finished attempt":
        _new_attempt(env, "2026-10-07T06:40:00Z")
        with friction_path(env).open("a", encoding="utf-8") as f:
            f.write("2026-10-07T06:45:00Z | end | finished\n")
    _stated_stop(env, "2026-10-07T10:00:00Z")
    one = _keep_patch(env, "2026-10-07T09:59:00Z", "+one = 1\n", "0001-one.patch")
    two = _keep_patch(env, "2026-10-07T09:59:00Z", "+two = 2\n", "0002-two.patch")
    both = "~~~~~~~~~~diff\n+one = 1\n+two = 2\n~~~~~~~~~~\n"
    assert _local_work(env) == both
    assert _local_work(env) == both, "a second report of the same attempt sends them again"
    ledger = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / ".sent"
    assert ledger.stat().st_mode & 0o777 == 0o600
    # the report's time, as if it ran a minute after the stop: every run here is in one second
    ledger.write_text(re.sub(r" \S+$", " 2026-10-07T10:01:00Z", ledger.read_text(), flags=re.MULTILINE))
    _stated_stop(env, "2026-10-07T10:13:00Z")
    held = "2026-10-07T10:01:00Z"
    assert _local_work(env) == _carried_not_repeated(one, held) + _carried_not_repeated(two, held)
    assert friction_path(env).read_text(encoding="utf-8").endswith("exited 1 | -\n"), "still no end line"
    # a patch kept since is sent beside them, and the ledger keeps the first report's time
    three = _keep_patch(env, "2026-10-07T10:12:00Z", "+three = 3\n", "0003-three.patch")
    section = _local_work(env)
    assert section.startswith("~~~~~~~~~~diff\n+three = 3\n~~~~~~~~~~\n"), section
    assert section.count(f"({held})") == 2
    assert len(ledger.read_text().splitlines()) == 3
    three.unlink()
    one.write_text("+one = 1, changed\n", encoding="utf-8")
    assert _local_work(env).startswith("~~~~~~~~~~diff\n+one = 1, changed\n~~~~~~~~~~\n"), "changed: sent"
    assert len(ledger.read_text().splitlines()) == 2, "a patch gone or changed leaves the ledger"


def test_a_patch_unreadable_after_a_report_with_no_header_embedded_it_is_not_listed(
    env: dict[str, str],
) -> None:
    """B13 round 15 S21 with S22: a patch an earlier header-less attempt's report embedded and unreadable at a
    later report is not put in .not-sent, so once readable it is listed by its hash, not sent again."""
    _stated_stop(env, "2026-10-07T10:00:00Z")
    patch = _keep_patch(env, "2026-10-07T09:59:00Z")
    assert _local_work(env) == "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"
    ledger = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / ".sent"
    ledger.write_text(re.sub(r" \S+$", " 2026-10-07T10:01:00Z", ledger.read_text(), flags=re.MULTILINE))
    _stated_stop(env, "2026-10-07T10:13:00Z")
    patch.chmod(0)
    try:  # B13 round 16 T24: not "not sent" either, as the rerun once readable sends nothing
        assert _local_work(env) == (
            "not repeated: a patch file an earlier attempt's report held (2026-10-07T10:01:00Z); this "
            "account cannot read it now, so no line count or hash; still in "
            "~/agent-context/setup/local-work\n"
        )
    finally:
        patch.chmod(0o600)
    assert not (ledger.parent / ".not-sent").exists()
    assert _local_work(env) == _carried_not_repeated(patch, "2026-10-07T10:01:00Z")


def _ledger_time(env: dict[str, str], stamp: str) -> None:
    """Set every ``.sent`` entry's report time to ``stamp``: every run here is in one second."""
    ledger = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / ".sent"
    ledger.write_text(re.sub(r"(?m)^(\S+) \S+", rf"\1 {stamp}", ledger.read_text()))


SENT = "~~~~~~~~~~diff\n+def ocr(): ...\n~~~~~~~~~~\n"
"""Section 3 of these tests when it sends the one patch."""


def test_a_patch_made_readable_in_its_attempt_names_the_report_that_carried_it(env: dict[str, str]) -> None:
    """B13 round 16 T16 (O25's three reports): a patch the first report of an attempt could not read, sent by
    a second report once readable, was later listed as "at or before an earlier attempt's report (<the first
    report's end>)", the report that could not read it. .sent holds the report that carried it, and its words
    come first."""
    _two_attempts(env)
    patch = _keep_patch(env, "2026-10-07T15:58:27Z")
    notsent = Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / ".not-sent"
    patch.chmod(0)
    try:
        assert _local_work(env).startswith("not sent: 1 patch file in "), "report 1"
    finally:
        patch.chmod(0o600)
    _end_at(env, "2026-10-07T16:00:00Z")
    [record] = notsent.read_text().splitlines()
    notsent.write_text(record.split(" ")[0] + " 2026-10-07T16:00:00Z\n")
    assert _local_work(env) == SENT, "report 2, made readable"
    _ledger_time(env, "2026-10-07T16:00:22Z")
    _new_attempt(env, "2026-10-09T09:00:00Z")
    assert _local_work(env) == _carried_not_repeated(patch, "2026-10-07T16:00:22Z"), "report 3"


def test_a_patch_an_earlier_attempt_sent_names_the_report_that_held_it(env: dict[str, str]) -> None:
    """B13 round 16 T16: where the time rule and .sent agree (a patch sent in attempt 1, reported in attempt
    2), .sent's words are the ones written: both are true, and .sent's names a report that held it."""
    _new_attempt(env, "2026-10-06T10:00:00Z")
    patch = _keep_patch(env, "2026-10-06T09:59:00Z")
    assert _local_work(env) == SENT
    _end_at(env, "2026-10-06T10:05:00Z")
    _ledger_time(env, "2026-10-06T10:01:00Z")
    _new_attempt(env, "2026-10-07T10:00:00Z")
    assert _local_work(env) == _carried_not_repeated(patch, "2026-10-06T10:01:00Z")


def test_a_patch_sent_late_is_not_listed_against_a_report_that_did_not_hold_it(env: dict[str, str]) -> None:
    """B13 round 16 T26 (probe bw3): a headed attempt could not read a patch; the next attempt, which step 1
    stopped (no header, so no end line), sent it. The attempts after it listed the patch "at or before an
    earlier attempt's report (<the headed attempt's end>)", a report that did not hold it. They name the
    report that sent it (.sent), as T16 does."""
    _new_attempt(env, "2026-10-07T10:00:00Z")
    patch = _keep_patch(env, "2026-10-07T09:59:00Z")
    patch.chmod(0)
    try:
        assert _local_work(env).startswith("not sent: 1 patch file in "), "A3, headed"
    finally:
        patch.chmod(0o600)
    _end_at(env, "2026-10-07T10:05:00Z")
    _stated_stop(env, "2026-10-08T10:00:00Z")
    assert _local_work(env) == SENT, "A4, no header: sent"
    _ledger_time(env, "2026-10-08T10:01:00Z")
    held = _carried_not_repeated(patch, "2026-10-08T10:01:00Z")
    for n, stamp in enumerate(("2026-10-08T10:13:00Z", "2026-10-08T10:26:00Z"), 5):
        _stated_stop(env, stamp)
        assert _local_work(env) == held, f"A{n}"


def test_a_patch_an_earlier_report_carried_is_not_called_missing_when_unreadable(env: dict[str, str]) -> None:
    """B13 round 16 T24 (probe bw5): a patch an earlier attempt's report carried, unreadable at a later
    report, was counted unread: the warning, the NEXT's chmod remedy, the header, section 3's "not sent:" and
    section 1's "unreadable and not in bring-back.md", though the rerun once readable sends nothing. It is a
    not-repeated line with no hash; a never-carried unreadable patch beside it keeps the full remedy."""
    _new_attempt(env, "2026-10-06T10:00:00Z")
    old = _keep_patch(env, "2026-10-06T09:59:00Z", "+old = 1\n", "0001-old.patch")
    assert _local_work(env) == "~~~~~~~~~~diff\n+old = 1\n~~~~~~~~~~\n", "attempt 1 sends it"
    _end_at(env, "2026-10-06T10:05:00Z")
    _ledger_time(env, "2026-10-06T10:01:00Z")
    _new_attempt(env, "2026-10-07T10:00:00Z")
    where = "still in ~/agent-context/setup/local-work"
    nohash = (
        "not repeated: a patch file an earlier attempt's report held (2026-10-06T10:01:00Z); this account "
        f"cannot read it now, so no line count or hash; {where}\n"
    )
    back = report_path(env).parent / "bring-back.md"
    old.chmod(0)
    try:
        cp = install_sh(env, "--report-only")
        text = back.read_text(encoding="utf-8")
        shell = report_path(env).read_text(encoding="utf-8")  # the shell fallback: no agentsync here
    finally:
        old.chmod(0o600)
    assert cp.returncode == 0, cp.stderr
    head = " ".join(text.split("\n## 1. Setup report", 1)[0].split())
    assert text.split("(~/agent-context/setup/local-work)\n\n", 1)[1] == nohash
    assert "could not be read" not in cp.stderr and "chmod" not in last_line(cp), cp.stderr + last_line(cp)
    assert head.endswith(
        "Section 3 sends no patch this time: its one patch was in an earlier report and is listed there."
    ), head
    assert "unreadable" not in text, "section 1 (test_setup_report.py has the agentsync reader's)"
    assert (
        "- local work kept by setup prompt step 1: 1 patch file in the setup folder's local-work, newest"
        in shell
    )
    assert _local_work(env) == _carried_not_repeated(old, "2026-10-06T10:01:00Z"), "readable: its hash"
    new = _keep_patch(env, "2026-10-07T10:01:00Z", "+new = 2\n", "0002-new.patch")
    for path in (old, new):
        path.chmod(0)
    try:
        cp = install_sh(env, "--report-only")
        text = back.read_text(encoding="utf-8")
    finally:
        for path in (old, new):
            path.chmod(0o600)
    head = " ".join(text.split("\n## 1. Setup report", 1)[0].split())
    assert text.split("(~/agent-context/setup/local-work)\n\n", 1)[1] == nohash + (
        "not sent: 1 patch file in ~/agent-context/setup/local-work this account cannot read; make it "
        "readable (chmod u+r) and run ~/src/agent-context-sync/scripts/install.sh --report-only again\n"
    )
    assert "warning: 1 patch file in " in cp.stderr and "1 kept patch file this account cannot" in last_line(
        cp
    )
    assert head.endswith(
        "Section 3 sends no patch: 1 patch file could not be read; make it readable (chmod u+r) and run the "
        "report again (install.sh --report-only), as its last line says. The one other patch was in an "
        "earlier report and is listed there."
    ), head
    assert "1 of them unreadable and not in bring-back.md" in text.split("## 1. Setup report", 1)[1]


@pytest.mark.parametrize("unread", [0, 1])
@pytest.mark.parametrize("skipped", [1, 2])
def test_a_report_that_sends_a_patch_counts_the_ones_listed_by_their_hash(
    env: dict[str, str], skipped: int, unread: int
) -> None:
    """B13 round 16 T25: with one patch sent and others listed by their hash, the header said only "Section 3
    holds one patch per kept commit". It adds the sentence the unread arm has (S24), counted, after the
    remedy line when a patch could not be read."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    _two_attempts(env)
    for n in range(skipped):
        _keep_patch(env, "2026-10-06T16:00:00Z", f"+old = {n}\n", f"000{n}-old.patch")
    _keep_patch(env, "2026-10-07T16:00:00Z", name="0008-new.patch")
    locked = [_keep_patch(env, "2026-10-07T16:00:00Z", "+x\n", "0009-x.patch")] if unread else []
    for path in locked:
        path.chmod(0)
    try:
        _local_work(env)
        text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    finally:
        for path in locked:
            path.chmod(0o600)
    head = " ".join(text.split("\n## 1. Setup report", 1)[0].split())
    others = (
        "The one other patch was in an earlier report and is listed by its hash."
        if skipped == 1
        else "The other 2 patches were in an earlier report and are listed by their hash."
    )
    remedy = (
        "Section 3 does not hold 1 patch file this account cannot read; make it readable (chmod u+r) and run "
        "the report again (install.sh --report-only), as its last line says. "
    )
    # B13 round 17 U37: the header counts the one patch it sends, not "one per kept commit, and each"
    assert "Section 3 sends one patch; it starts with its author's name" in head
    assert "holds one patch per kept commit" not in head and "each starts" not in head
    assert head.endswith("so check those lines too. " + remedy * unread + others), head


@pytest.mark.parametrize("unread", [0, 1])
@pytest.mark.parametrize("skipped", [0, 1])
def test_a_report_that_sends_two_patches_counts_them(env: dict[str, str], skipped: int, unread: int) -> None:
    """B13 round 17 U37: the header said "Section 3 holds one patch per kept commit, and each starts with its
    author's name" whatever it sent, beside patches only listed by their hash or not readable. It counts the
    patches it sends, and says "one per kept commit" only when it sends every kept patch."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True)
    _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
    _two_attempts(env)
    for n in range(skipped):
        _keep_patch(env, "2026-10-06T16:00:00Z", f"+old = {n}\n", f"000{n}-old.patch")
    _keep_patch(env, "2026-10-07T16:00:00Z", name="0007-new.patch")
    _keep_patch(env, "2026-10-07T16:00:00Z", "+y\n", name="0008-new.patch")
    locked = [_keep_patch(env, "2026-10-07T16:00:00Z", "+x\n", "0009-x.patch")] if unread else []
    for path in locked:
        path.chmod(0)
    try:
        _local_work(env)
        text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    finally:
        for path in locked:
            path.chmod(0o600)
    head = " ".join(text.split("\n## 1. Setup report", 1)[0].split())
    first = (
        "Section 3 sends 2 patches; each starts"
        if skipped or unread
        else "Section 3 sends 2 patches, one per kept commit; each starts"
    )
    assert first + " with its author's name and email and the commit's date and message;" in head, head
    assert "holds one patch per kept commit" not in head


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
    authors = "with its author's name and email"

    def header() -> str:
        cp = install_sh(env, "--report-only")
        assert cp.returncode == 0, cp.stderr
        text = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
        return " ".join(text.split("\n## 1. Setup report", 1)[0].split())

    _two_attempts(env)
    old = _keep_patch(env, "2026-10-06T16:42:39Z", "+old = 1\n", "0001-old.patch")
    head = header()
    assert authors not in head, head
    # B13 round 11 M18: one patch is "its one patch", not "each"
    assert head.endswith(
        "Section 3 sends no patch this time: its one patch was in an earlier report and is listed by its "
        "hash."
    ), head
    assert _local_work(env) == _not_repeated(old, "2026-10-06T16:42:39Z")
    second = _keep_patch(env, "2026-10-06T16:42:40Z", "+old = 3\n", "0001-older.patch")
    assert header().endswith(
        "Section 3 sends no patch this time: each was in an earlier report and is listed by its hash."
    )
    second.unlink()
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
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", **xcrun_db()},
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
        # B13 round 14 Q28 (probe prx): two sessions that each stopped at step 1, 4m36s apart, after a
        # finished attempt are two attempts, not one
        "Attempt: 2026-10-17T16:40:00Z\nPrompt: v22\nAgent: Claude Code\n"
        f"2026-10-17T16:58:00Z | end | finished\n2026-10-17T17:03:50Z {err}2026-10-17T17:08:26Z {err}",
        # a loosely written stated line, which the awk does not read as a step 1 error, stays in both
        f"2026-10-06T10:00:00Z {err}- `2026-10-06T10:00:30Z` | Step 1 | Error | prompt v12, Claude Code: "
        "x | -\n",
    ]
    for log in logs:
        assert _awk_attempts(log) == len(setup_report.parse_friction(log).attempts), log
    assert len(setup_report.parse_friction(logs[-2]).attempts) == 3


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
    # B13 round 16 T16: .sent's words when the report that sent it came before the second attempt began (a
    # loaded machine puts seconds between them), the time rule's when both fell in one second
    held = (Path(env["HOME"]) / "agent-context" / "setup" / "local-work" / ".sent").read_text().split()[1]
    begin = [
        ln.split(": ", 1)[1]
        for ln in friction_path(env).read_text().splitlines()
        if ln.startswith("Attempt: ")
    ]
    want = (
        _carried_not_repeated(patch, held)
        if held < begin[-1]
        else _not_repeated(patch, "2026-10-06T10:00:00Z", ended)
    )
    assert _local_work(env) == want
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


@pytest.mark.parametrize("why", ["agentsync is not installed", "agentsync setup-report failed with exit 2"])
def test_the_shell_report_says_it_did_not_check_whether_the_config_loads(
    env: dict[str, str], why: str
) -> None:
    """B13 round 12 N25: with a broken last table the shell report printed "- [[source]] tables: 2" and no
    loads line, so the prompt's finish, which keys "does not load" on the report's "loads: no", sent the agent
    to parse a file agentsync rejects. The shell checks no TOML (a syntax-only check reads yes for a schema
    error): it says the file was not checked, and why, and that the count reads headers only."""
    home = Path(env["HOME"])
    cs = _cloud(env)
    tables = [local_source_table(n, cs / "OneDrive-Contoso" / n) for n in ("alpha", "beta")]
    cfg = _write_config(env, *tables)
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[source]\nid = \n", encoding="utf-8")
    e = env
    if why != "agentsync is not installed":
        (home / ".local" / "bin").mkdir(parents=True)
        _write_exe(home / ".local" / "bin" / "agentsync", STUB_AGENTSYNC)
        e = {**env, "STUB_REPORT_RC": "2"}
    cp = install_sh(e, "--report-only")
    assert cp.returncode == 0 and f"(shell fallback: {why})" in cp.stdout, cp.stdout + cp.stderr
    section = report_path(env).read_text().split("\n## Configuration\n\n", 1)[1].split("\n\n", 1)[0]
    assert section.splitlines()[:3] == [
        "- config: ~/agent-context/sources.toml (exists)",
        f"- loads: not checked ({why})",
        "- [[source]] table headers (not parsed): 2",
    ], section


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
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8", **xcrun_db()},
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


NO_STEP_LINE = (
    "- outcome: not computed (this shell fallback computes none for this attempt, and the friction log has "
    "no step line for it; the Outcome is yours to choose{}; the link fills Run type, and Prompt version and "
    "Agent where the latest attempt states them)"
)
"""NOT_COMPUTED for an attempt with no step line, or no friction log (B13 round 11 M15); the braces take
" from the Installer section, ..." when the attempt has an install run."""


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
        env={"PATH": "/usr/bin:/bin", "HOME": home, "LC_ALL": "C", **xcrun_db()},
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
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "alias" / "home"), "LC_ALL": "C", **xcrun_db()},
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
    assert _summary(text)[2] == (NOT_COMPUTED if logged else NO_STEP_LINE.format(""))  # B13 round 11 M15
    link = text.splitlines()[-1]
    assert link == _uncomputed_link(env, text) and "outcome=" not in link
    fields = setup_report.issue_link_fields(link)
    names = " and ".join(fields) if len(fields) < 3 else ", ".join(fields[:-1]) + ", and " + fields[-1]
    assert f"issue with the {names} field" in text.splitlines()[-3]


@pytest.mark.parametrize(
    ("said", "late", "want"),
    [
        (
            "prompt v3, Contoso Agent 1",
            False,
            "prompt: v3 (older than the installer's v{c}: the pasted copy was not the current README, or the "
            "--log-start value was changed)",
        ),
        (  # its cause depends on the branch this checkout follows: the Python twin below pins it
            "prompt v99, Contoso Agent 1",
            False,
            "prompt: v99 (newer than the installer's v{c}",
        ),
        (
            "Contoso Agent 1",
            False,
            "prompt: v7 or older (older than the installer's v{c}: the pasted copy was not the current "
            "README, or the --log-start value was changed)",
        ),
        ("prompt v3, Contoso Agent 1", True, None),
    ],
)
def test_the_shell_report_computes_a_stopped_copys_failure(
    env: dict[str, str], said: str, late: bool, want: str | None
) -> None:
    """B13 round 11 M14: a copy --log-start stopped has a header and a stop line that states nothing, so the
    shell report left its outcome "not computed" while the agentsync report computes "failed at step 1" from
    the same log. The version and the agent now come from the attempt's Prompt: and Agent: lines; a late line
    of another step still leaves the outcome to the agentsync report."""
    assert install_sh(env, "--log-start", said).returncode == 2
    if late:
        assert install_sh(env, "--log", "3", "error", "the report did not open", "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = (friction_path(env).parent / "stopped-copy-report.md").read_text()
    outcome = _summary(text)[2]
    if want is None:
        assert outcome == NOT_COMPUTED, outcome
        return
    assert outcome.startswith(
        "- outcome: failed at step 1 (computed: no install.sh run; step 1 logged an error) · "
        + want.format(c=COMPAT)
    ), outcome
    assert outcome.endswith(" · agent: Contoso Agent 1"), outcome
    # B13 round 12 N44: the clause is the agentsync report's prompt note word for word, for an older copy, a
    # newer one (with the cause its stop line's fix names) and one that gave no version. B13 round 13 O18: the
    # whole bracket, with no "from the attempt's Prompt line" label the agentsync report does not print
    attempt = setup_report.parse_friction(friction_path(env).read_text()).latest
    note = setup_report._prompt_line_note(attempt)
    older, _, clause = note.partition(" (")
    version = f"{setup_report._prompt_version(attempt)}{older}"
    assert f" · prompt: {version} ({clause} · run: " in outcome, (note, outcome)
    assert "from the attempt's Prompt line" not in outcome


@pytest.mark.parametrize("ran", [False, True])
def test_an_uncomputed_outcome_names_step_lines_only_when_the_attempt_has_them(
    env: dict[str, str], ran: bool
) -> None:
    """B13 round 11 M15: with a friction log of a header and an end line only, the uncomputed outcome still
    said to choose "from the friction log's step lines". It says the attempt has none, and names the
    Installer section when the attempt has an install run."""
    setup = friction_path(env).parent
    setup.mkdir(parents=True)
    friction_path(env).write_text(
        "Attempt: 2026-10-14T10:00:00Z\nPrompt: v19\nAgent: Northwind Agent 2.1\n"
        "2026-10-14T10:05:00Z | end | finished\n"
    )
    if ran:
        (setup / "install.log").write_text(
            "2026-10-14T10:01:00Z run=20261014T100100Z-1 start install.sh compat=19 commit=- kind=- source=- "
            "args=\n2026-10-14T10:01:01Z run=20261014T100100Z-1 step=uv seconds=0 rc=0 result=skipped\n"
        )
    assert install_sh(env, "--report-only").returncode == 0
    outcome = _summary(report_path(env).read_text())[2]
    where = " from the Installer section, which has this attempt's install runs" if ran else ""
    assert outcome == NO_STEP_LINE.format(where), outcome


def test_a_step_1_line_before_a_new_attempts_header_is_that_attempts_in_the_shell_report(
    env: dict[str, str],
) -> None:
    """B13 round 13 O24, the shell twin: a new session's step 1 line logged after the finished attempt's end
    line and before its own Attempt: header was read as the finished attempt's, so the latest attempt had "no
    step line". The run moves to the next header, as parse_friction moves it; with no end line it stays."""
    setup = friction_path(env).parent
    setup.mkdir(parents=True)
    head = "Attempt: 2026-10-16T15:50:00Z\nPrompt: v20\nAgent: Contoso Agent\n"
    end = "2026-10-16T15:52:00Z | step 2 | start | x | -\n2026-10-16T15:57:18Z | end | finished\n"
    asked = "2026-10-16T15:58:29Z | step 1 | question | asked which folders | -\n"
    new = "Attempt: 2026-10-16T15:59:00Z\nPrompt: v21\nAgent: Contoso Agent\n"
    for log, has in ((head + end + asked + new, True), (head + end + new, False)):
        friction_path(env).write_text(log)
        assert install_sh(env, "--report-only").returncode == 0
        outcome = _summary(report_path(env).read_text())[2]
        assert ("the Outcome is yours to choose from the friction log's step lines" in outcome) is has, (
            outcome
        )


@pytest.mark.parametrize(
    "later",
    [
        [("1", "error", "git pull failed again")],
        [("1", "prompt", "the person said to go on")],
        [("1", "question", "which folder?")],
        # a session writes the prefix once (B13 round 14 Q28: a second prefixed line is another session's)
        [("1", "deviation", "asked"), ("1", "error", "still no network")],
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
        (  # B13 round 12 N23: a step 1 line inside the gap moves with the error too, whatever the gap: the
            # finished session runs nothing after its --report-only (it stayed with the finished attempt)
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:25:00Z | step 1 | click | late | -\n"
            "2026-10-14T17:42:00Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["finished"],
            "2026-10-14T17:25:00Z",
        ),
        (  # N23's devfirst: a stated step 1 deviation 71 s after the end line, then the stated error
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:10:00Z | step 2 | deviation | a | -\n2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:21:11Z | step 1 | deviation | prompt v18, Contoso Agent: waited | -\n"
            "2026-10-14T17:21:12Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["deviation", "finished"],
            "2026-10-14T17:21:11Z",
        ),
        (  # and devplain: the deviation with no prefix
            "Attempt: 2026-10-14T17:00:00Z\nPrompt: v18\nAgent: Contoso Agent\n"
            "2026-10-14T17:10:00Z | step 2 | deviation | a | -\n2026-10-14T17:20:00Z | end | finished\n"
            "2026-10-14T17:21:11Z | step 1 | deviation | waited | -\n"
            "2026-10-14T17:21:12Z | step 1 | error | prompt v18, Contoso Agent: git pull failed | -\n",
            ["deviation", "finished"],
            "2026-10-14T17:21:11Z",
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


def test_a_stopped_copys_shell_report_has_no_link(env: dict[str, str]) -> None:
    """The line --log-start writes for a copy it stopped is a step 1 error too, and the attempt's only event:
    H24's rule left it out; since B13 round 11 M14 its outcome is computed from the attempt's header, as the
    agentsync report computes it, and the stopped copy's report still ends with no link."""
    assert install_sh(env, "--log-start", f"prompt v{COMPAT + 1}, Contoso Agent").returncode == 2
    assert install_sh(env, "--report-only").returncode == 0
    text = (friction_path(env).parent / "stopped-copy-report.md").read_text()
    assert _summary(text)[2].startswith("- outcome: failed at step 1 (computed: "), _summary(text)
    assert setup_report.ISSUE_URL not in text and "Send it: " not in text


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


def _n21_run(stamp: str, pid: int, kind: str) -> str:
    """One install.log run started at ``stamp`` (``20261016T155217Z``) by process ``pid``: a list or an
    install."""
    at = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    run = f"run={stamp}-{pid}"
    args, step = (
        ("--list-folders", "step=list-folders seconds=0 rc=0 result=done note=listed-6")
        if kind == "list"
        else ("--source-local x", "step=config seconds=1 rc=0 result=done note=created")
    )
    return (
        f"{at} {run} start install.sh compat=20 commit=0123456789ab kind=checkout source=- args={args}\n"
        f"{at} {run} {step}\n{at} {run} end rc=0 seconds=0\n"
    )


@pytest.mark.parametrize("window", [False, True], ids=["both-shown", "only-the-second-shown"])
def test_runs_started_in_one_second_are_numbered_alike_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, window: bool
) -> None:
    """B13 round 12 N21: step 1's --list-folders and the install started in the same second, and with the
    process id stripped both read "run 20261016T155217Z": two Installer headings, two Every-run rows and the
    raw lines under one id, in both reports. Runs that share a second are numbered over the whole log, the
    first keeping the bare stamp, so a label does not change as the window of shown runs slides; no pid
    returns."""
    stamp = "20261016T155217Z"
    log = _n21_run(stamp, 30077, "list") + _n21_run(stamp, 30586, "install")
    if (
        window
    ):  # the first of the pair out of both windows: the Python report's last 3 runs, the shell's 60 lines
        log = _n21_run(stamp, 30077, "list") + "".join(f"note {i}\n" for i in range(70))
        log += _n21_run(stamp, 30586, "install")
        log += _n21_run("20261016T160000Z", 11, "install") + _n21_run("20261016T160100Z", 12, "install")
    install_log_path(env).parent.mkdir(parents=True)
    install_log_path(env).write_text(log)
    assert install_sh(env, "--report-only").returncode == 0
    shell = report_path(env).read_text().split("\n## Installer\n", 1)[1].split("\n## ", 1)[0]
    monkeypatch.setenv("HOME", env["HOME"])
    runs = setup_report.read_install_runs(install_log_path(env))
    assert [run.display_id for run in runs][:2] == [stamp, f"{stamp}.2"]
    text, _red = setup_report.build_report(None, hooks=setup_report.ReportHooks())
    python = text.split("\n## Installer\n", 1)[1].split("\n## ", 1)[0]
    for part in (shell, python):
        assert "30077" not in part and "30586" not in part, part
        assert f"run={stamp}.2 " in part, part
        assert (f"run={stamp} " in part) is not window, part
    assert f"run {stamp}.2: exit 0" in python and f"| {stamp}.2 " in python, python
    if not window:
        assert f"run {stamp}: exit 0" in python and f"| {stamp} " in python, python
    for part in (shell, python):  # the lead-in says what ".2" is
        assert "(run ids without their process id, numbered .2 on for runs started in one second):" in part


@pytest.mark.parametrize("order", ["list-first", "install-first", "own-second"])
def test_a_run_named_by_its_start_time_carries_its_label_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, order: str
) -> None:
    """B13 round 13 O22: a Summary line naming an earlier run by its start time ("the install run of <time>",
    the shell's "the last one, started <time>") did not carry the ``.2`` label the Installer section gives
    it, so with a list and an install run started in one second the time named two runs. Both readers add
    ", run <label>" when another run started in that second, the bare stamp included; a run alone in its
    second is named by its time only."""
    stamp, at = "20261016T155217Z", "2026-10-16T15:52:17Z"
    install = _n21_run(stamp, 30586, "install").replace(
        " end rc=0",
        " step=first-sync seconds=9 rc=0 result=done note=converted-0-deferred-0\n"
        f"{at} run={stamp}-30586 end rc=0",
    )
    log = {
        "list-first": _n21_run(stamp, 30077, "list") + install,
        "install-first": install + _n21_run(stamp, 30077, "list"),
        "own-second": _n21_run("20261016T155200Z", 30077, "list") + install,
    }[order]
    label = {"list-first": f", run {stamp}.2", "install-first": f", run {stamp}", "own-second": ""}[order]
    install_log_path(env).parent.mkdir(parents=True)
    install_log_path(env).write_text(log)
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text("Attempt: 2026-10-16T16:30:00Z\nPrompt: v21\nAgent: x\n")
    assert install_sh(env, "--report-only").returncode == 0
    shell = report_path(env).read_text()
    assert f"; the last one, started {at}{label}, exit 0" in shell, shell
    monkeypatch.setenv("HOME", env["HOME"])
    text, _red = setup_report.build_report(None, hooks=setup_report.ReportHooks())
    summary = text.split("\n## Summary\n", 1)[1].split("\n## ", 1)[0]
    assert f"(install.log: the install run of {at}{label}, before this attempt; " in summary, summary
    if label:  # the label is the Installer section's
        assert f"run {label.removeprefix(', run ')}: exit 0" in text.split("\n## Installer\n", 1)[1]


@pytest.mark.parametrize(
    ("lines", "head"),
    [
        (15, "All 15 lines of ~/agent-context/setup/install.log " + PIDLESS),
        (1, "The one line of ~/agent-context/setup/install.log " + PIDLESS),  # B13 round 15 S31
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


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
@pytest.mark.parametrize(
    "damage", ["whole", "missing", "changed", "not executable", "no RECORD", "its Python changed"]
)
def test_a_rerun_reinstalls_a_bundled_pandoc_that_is_not_the_one_pip_installed(
    env: dict[str, str], tmp_path: Path, damage: str
) -> None:
    """B13 round 16 T54, T59, T60, T65: doctor's fix for the bundled pandoc was a bare `uv tool upgrade`,
    with none of the sync locks, TLS setting or stamp install.sh's reinstall has, because a re-run skipped
    step 2 whenever the stamp named this commit. Step 2 now skips only while the tool environment's pandoc is
    the file pypandoc_binary's RECORD lists (executable, its size and sha256), so the re-run doctor names
    reinstalls one that is missing or changed, and says why. Step 1's word on it:
    test_step_1_says_whether_step_2_reinstalls_before_a_config_it_cannot_load."""
    checkout, head = _committed_checkout(tmp_path)
    script = checkout / "scripts" / "install.sh"
    assert install_sh(env, script=script).returncode == 2  # it created the config and has no folder to sync
    site = Path(env["HOME"]) / ".local/share/uv/tools/agentsync/lib/python3.11/site-packages"
    pandoc, record = site / "pypandoc/files/pandoc", site / "pypandoc_binary-1.17.dist-info/RECORD"
    assert pandoc.read_bytes() == FAKE_WHEEL_PANDOC and record.is_file(), "the stub installed it whole"
    if damage == "missing":
        pandoc.unlink()
    elif damage == "changed":  # the same size, other bytes
        pandoc.write_bytes(FAKE_WHEEL_PANDOC.replace(b"9.9", b"0.0"))
    elif damage == "not executable":
        pandoc.chmod(0o644)
    elif damage == "no RECORD":
        record.unlink()
    elif damage == "its Python changed":  # doctor's "pypandoc_binary is not importable" case
        (site / "pypandoc" / "__init__.py").write_text("raise ImportError\n")
    again = install_sh(env, script=script)
    assert again.returncode == 0, again.stdout + again.stderr
    installs = sum(c.startswith("uv tool install") for c in calls(env))
    said = "agentsync: the bundled pandoc in "
    if damage == "whole":
        assert installs == 1 and ("agentsync", "skipped", "0", "current") in steps(install_log(env))
        assert f"(current: installed from commit {head})" in again.stdout and said not in again.stdout
    else:
        assert installs == 2, calls(env)
        assert any(
            ln.startswith(said)
            and ln.endswith(" is missing or not the file pypandoc_binary installed; reinstalling")
            for ln in again.stdout.splitlines()
        ), again.stdout
        assert pandoc.read_bytes() == FAKE_WHEEL_PANDOC and os.access(pandoc, os.X_OK), "reinstalled whole"


@pytest.mark.parametrize("lock", ["exports", "export fails", "no uv.lock"])
def test_the_reinstall_keeps_uv_locks_versions(env: dict[str, str], tmp_path: Path, lock: str) -> None:
    """B13 round 16 T60, T65: `uv tool install --force` resolves every dependency afresh, to the newest
    version pyproject.toml allows (measured with uv 0.12.17: a tool's two dependencies moved from 1.0 to
    1.1 at a reinstall), so a reinstall, the one doctor's pandoc fix names included, could bring converter
    libraries no test ran and a pypandoc_binary whose pandoc convert.pandoc does not know. Step 2 passes the
    versions of the checkout's uv.lock as constraints when uv exports them, and installs as before when it
    cannot; the file is removed after."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "agentsync"\n')
    if lock != "no uv.lock":
        (checkout / "uv.lock").write_text("version = 1\n")
    e = {**env, "STUB_UV_PINS": "contoso-dep==1.0\npypandoc-binary==1.17"}
    if lock == "export fails":
        e = {**env, "STUB_UV_EXPORT_RC": "2"}
    cp = install_sh(e, str(checkout))
    assert cp.returncode == 2, cp.stdout + cp.stderr
    [install] = [c for c in calls(e) if c.startswith("uv tool install ")]
    exported = [c for c in calls(e) if c.startswith("uv export ")]
    pins = Path(f"{e['STUB_LOG']}.pins")
    if lock == "exports":
        assert exported == [
            f"uv export --project {checkout} --locked --no-hashes --no-emit-project --no-default-groups "
            f"--quiet -o {install.split(' -c ')[1].split(' ')[0]}"
        ]
        assert install.startswith("uv tool install --force --reinstall-package agentsync --python 3.11 -c ")
        assert (
            install.endswith(f" {checkout}")
            and pins.read_text() == "contoso-dep==1.0\npypandoc-binary==1.17\n"
        )
        assert not Path(install.split(" -c ")[1].split(" ")[0]).exists(), "the constraints file is removed"
    else:
        assert install == f"uv tool install --force --reinstall-package agentsync --python 3.11 {checkout}"
        assert not pins.exists() and len(exported) == (lock == "export fails")


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
def test_a_runs_id_and_its_start_line_name_one_second(env: dict[str, str], tmp_path: Path) -> None:
    """B13 round 13 O4: the run id was stamped when install.sh loaded and the start line later, after the
    commit read, so a slow git put the Installer section's id and start seconds apart, and start plus
    `seconds` fell after the end line. One clock read now gives both. git is slowed by a core.fsmonitor hook
    (install.sh calls /usr/bin/git by its path, so a stub on PATH would not be run)."""
    src = tmp_path / "checkout"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "agentsync"\nversion = "0.1.0"\n')
    git = _git_checkout(src)
    hook = _write_exe(tmp_path / "slow-fsmonitor", "#!/bin/bash\nsleep 2\nexit 1\n")
    subprocess.run([*git, "config", "core.fsmonitor", str(hook)], check=True, capture_output=True)
    (src / "pyproject.toml").write_text('[project]\nname = "agentsync"\nversion = "0.1.1"\n')
    install_sh(env, str(src))
    log = install_log(env)
    [start] = [ln for ln in log if " start install.sh " in ln]
    [end] = [ln for ln in log if " end rc=" in ln]
    stamp = re.search(r" run=(\d{8}T\d{6}Z)-\d+ ", start)
    assert stamp, start
    at = datetime.strptime(stamp.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    assert start.split()[0] == at.strftime("%Y-%m-%dT%H:%M:%SZ"), (start, "the start line is the id's second")
    seconds = int(re.search(r" seconds=(\d+)", end).group(1))  # type: ignore[union-attr]
    ended = datetime.strptime(end.split()[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs((at + timedelta(seconds=seconds) - ended).total_seconds()) <= 1, (start, end)
    assert seconds >= 2, "git was slowed, so a later stamp would have shown"


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
def test_a_checkout_git_cannot_read_takes_no_commit_from_a_repository_around_it(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 11 M51: in a home that is itself a git repository, a checkout whose .git holds only HEAD made
    git search upward, so --version, the shell report and install.log named the home's commit. git_ro sets
    the checkout's parent as git's ceiling; a healthy checkout and a linked worktree still read their own."""
    home = Path(env["HOME"])
    (home / "notes.txt").write_text("Contoso notes\n")
    outer = subprocess.run(
        [*_git_checkout(home), "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    src = home / "src" / "agent-context-sync"
    (src / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, src / "scripts" / "install.sh")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    script = src / "scripts" / "install.sh"
    version = install_sh(env, "--version", script=script)
    # B13 round 12 N75: with the reason the shell report gives, the folder as ~/...
    assert version.stdout.splitlines()[0] == (
        "source commit: unknown (git cannot read ~/src/agent-context-sync/.git)"
    ), version.stdout
    assert install_sh(env, "--report-only", script=script).returncode == 0
    text = report_path(env).read_text()
    assert outer not in version.stdout + text, "the home's commit"
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, main / "scripts" / "install.sh")
    git = _git_checkout(main)
    subprocess.run([*git, "worktree", "add", "-q", str(tmp_path / "wt")], check=True, capture_output=True)
    sha = subprocess.run(
        [*git, "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    )
    for checkout in (main, tmp_path / "wt"):
        cp = install_sh(env, "--version", script=checkout / "scripts" / "install.sh")
        assert cp.stdout.splitlines()[0] == f"source commit: {sha.stdout.strip()}", cp.stdout


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
        f"NEXT: this Mac already syncs 2 folders, and a re-run keeps them: run {INSTALL_SH}, with one "
        '--source-local "<folder>" for each unmarked folder to add from the list above (none is needed)'
    )
    assert cp.stdout.splitlines() == [
        "already synced on this Mac: 2 folders (marked [synced] below)",
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
        "already synced on this Mac: 4 folders (marked [synced] below: first the 2 outside the list, then "
        "the list)",
        f"[synced] {notes}",
        f"[synced] {cs}/OneDrive-Contoso/FY26 Projects/Alpha/Deep",
        *listed,
        keep.replace("syncs 2 folders", "syncs 4 folders"),
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
        "already synced on this Mac: 1 folder (marked [synced] below: first the 1 outside the list, then "
        "the list)",
        f"[synced] {notes}",
        f"{cs}/OneDrive-Contoso/FY26 Projects",
    ]
    assert last_line(cp).startswith("NEXT: this Mac already syncs 1 folder, and a re-run keeps it: ")
    # B13 round 15 S10: one outside the list and one gone from disk inside the listed OneDrive folder are
    # counted apart: a folder that is gone is not "outside the list"
    gone = cs / "OneDrive-Contoso" / "Project Beta - General"
    _write_config(env, local_source_table("notes", notes), local_source_table("beta", gone), _inbox(env))
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:3] == [
        "already synced on this Mac: 2 folders (marked [synced] below: first the 1 outside the list and the "
        "1 not on disk, then the list)",
        f"[synced] {notes}",
        f"[synced] {gone}",
    ]
    assert "1 of them is no longer on disk although its app is signed in" in last_line(cp)
    # B13 round 16 T46: a folder missing outside ~/Library/CloudStorage is no "not on disk" folder: no NEXT
    # clause or gone= names it (the gone check decides only CloudStorage ones), so it stays outside the list
    shutil.rmtree(notes)
    _write_config(env, local_source_table("notes", notes), _inbox(env))
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[0] == (
        "already synced on this Mac: 1 folder (marked [synced] below: first the 1 outside the list, then "
        "the list)"
    ), cp.stdout
    assert "not on disk" not in cp.stdout
    listed = [ln for ln in install_log(env) if "step=list-folders" in ln]
    assert listed and "gone=" not in listed[-1] and "gone=1" in listed[-2], listed


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
        "already synced on this Mac: 2 folders (marked [synced] below)",
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
        "already synced on this Mac: 1 folder (marked [synced] below)",
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
    syncs no folder still gets exit 3 and the line it always got. B13 round 11 M9: with no list to follow,
    the first line no longer says "first the 1 outside the list, then the list"."""
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
        "already synced on this Mac: 1 folder (marked [synced] below)",  # B13 round 11 M9: no list follows
        f"[synced] {notes}",
        f"NEXT: this Mac already syncs 1 folder, and a re-run keeps it: run {INSTALL_SH} (no folder to "
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
    # B13 round 13 O5: with no provider folder the stop is OneDrive's, logged as the other sign-in stop is
    assert re.search(
        r" result=failed note=(not-signed-in apps=onedrive|no-folders) synced=0$", install_log(env)[-2]
    ), install_log(env)[-2]
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[source]]\nid = \n", encoding="utf-8")
    cp = install_sh(env, "--list-folders")
    # a new Mac's exit and NEXT, which adds that a config is there (B13 round 8 J17)
    assert cp.returncode == 3 and cp.stdout.splitlines() == [
        _unread_next(env, head=only, failed="ConfigError")
    ]
    assert cp.stderr.startswith(f"warning: could not read which folders {cfg} already syncs ")
    # B13 round 7 H17, H28; and why, as the class of the reader's error (H8, H16)
    unread = (
        r" result=failed note=(not-signed-in apps=onedrive|no-folders) synced=unreadable "
        r"marks=failed:ConfigError$"
    )
    assert re.search(unread, install_log(env)[-2]), install_log(env)[-2]


@pytest.mark.parametrize(
    "layout",
    [
        "no CloudStorage",
        "an empty CloudStorage",
        "an empty provider folder",
        "a provider folder with hidden entries only",
        "notes beside them",
    ],
)
def test_list_folders_asks_for_the_sign_in_when_synced_folders_are_missing(
    env: dict[str, str], layout: str
) -> None:
    """B13 round 11 M4: OneDrive signed out on a Mac that syncs two of its folders. Step 1 said "a re-run
    keeps them" and exited 0, step 2's doctor then failed on each missing folder, and the person had to sign
    in and paste the prompt again. A synced folder under CloudStorage that is not on disk now gets the new
    Mac's exit 3 and a sign-in NEXT, with the [synced] lines still printed. Keyed on missing folders, not on
    no provider: a stale empty provider folder fails step 2 the same way."""
    cs = _cloud(env)
    lib = cs / "OneDrive-Contoso"
    tables = [
        local_source_table(n.lower().replace(" ", "-"), lib / n) for n in ("Project Alpha", "Project Beta")
    ]
    notes = Path(env["HOME"]) / "Documents" / "notes"
    if layout == "notes beside them":
        notes.mkdir(parents=True)
        tables.append(local_source_table("notes", notes))
    elif layout == "an empty CloudStorage":
        cs.mkdir(parents=True)
    elif layout == "an empty provider folder":
        lib.mkdir(parents=True)
    elif (
        layout == "a provider folder with hidden entries only"
    ):  # the list shows nothing there (B13 round 12 N61)
        (lib / ".tmp.drivedownload").mkdir(parents=True)
        (lib / ".849C9593.marker").write_text("")
    _write_config(env, *tables, _inbox(env))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (3, ""), cp.stdout + cp.stderr
    synced = len(tables)
    marked = [ln for ln in cp.stdout.splitlines() if ln.startswith("[synced] ")]
    assert sorted(marked) == sorted(
        [f"[synced] {lib}/Project Alpha", f"[synced] {lib}/Project Beta"]
        + ([f"[synced] {notes}"] if layout == "notes beside them" else [])
    )
    assert cp.stdout.startswith(f"already synced on this Mac: {synced} folders (marked [synced] below)\n")
    assert last_line(cp) == (
        "NEXT: OneDrive is not signed in on this Mac, so it is missing 2 of the folders this Mac already "
        f"syncs: sign in to OneDrive (or let it finish setting up), then re-run: {INSTALL_SH} "
        "--list-folders; the folders marked [synced] are kept"
    )
    assert steps(install_log(env)) == [("list-folders", "failed", "3", "not-signed-in")]
    # B13 round 13 O5: the app by a fixed word, and how many of the synced folders are missing
    assert install_log(env)[-2].endswith(
        f" result=failed note=not-signed-in apps=onedrive missing=2 synced={synced}"
    ), install_log(env)[-2]
    for name in ("Project Alpha", "Project Beta"):  # signed in: every synced folder is there, so exit 0
        (lib / name).mkdir(parents=True, exist_ok=True)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "a re-run keeps them" in last_line(cp)


@pytest.mark.parametrize("synced", [2, 1])
@pytest.mark.parametrize(
    "layout", ["renamed in a signed-in provider", "a symlinked provider folder", "another account of the app"]
)
def test_list_folders_leaves_a_folder_gone_from_a_signed_in_app_to_step_2(
    env: dict[str, str], tmp_path: Path, layout: str, synced: int
) -> None:
    """B13 round 12 N41, N61: M4's sign-in exit fired on a signed-in Mac whose synced folder was renamed or
    deleted in OneDrive, and every re-run of the list stopped the same way. The provider folder decides, by
    doctor's rule: one that lists other folders (a symlink too, as lexists reads it), or another folder of
    the same app beside a missing one, is no signed-out app, so the list keeps exit 0 and names the folder,
    and step 2's doctor names its fix. Only a folder whose provider folder is there is said to have a
    signed-in app. B13 round 13 O6: the clause said "nothing syncs until" then, but a sync still mirrors every
    other folder (test_e2e.py::test_a_sync_with_one_source_root_gone_still_mirrors_the_others): only step 2's
    first sync waits. B13 round 15 S36: with one synced folder the clause is the folder's own ("it", no other
    folder to mirror). S10: the header counts it as not on disk, not outside the list."""
    cs = _cloud(env)
    lib = cs / "OneDrive-Contoso"
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    gone = lib / "Project Beta"
    if layout == "another account of the app":  # the Contoso folder is gone while a Fabrikam one is there
        kept = cs / "OneDrive-Fabrikam" / "Project Alpha"
        kept.mkdir(parents=True)
        clause = (
            "; 1 of them is not on disk because its provider folder is missing while another folder of "
            f"that app is there: {gone}. Step 2's status fails on it and names the fix (sign in to that app "
            f"with the account it belongs to, or fix its [[source]] path in {cfg}), so step 2 runs no first "
            "sync until then; a sync still mirrors the folders that are there"
        )
        alone = clause.replace("; 1 of them is", "; it is").removesuffix(
            "; a sync still mirrors the folders that are there"
        )
    else:
        real = tmp_path / "real-provider" if layout == "a symlinked provider folder" else lib
        for n in ("Project Alpha", "Project Gamma"):
            (real / n).mkdir(parents=True)
        if real != lib:
            cs.mkdir(parents=True)
            lib.symlink_to(real)
        kept = lib / "Project Alpha"
        clause = (
            "; 1 of them is no longer on disk although its app is signed in (renamed, moved or deleted "
            f"there, or not synced on this Mac): {gone}. Step 2's status fails on it and names the fix, so "
            "step 2 runs no first sync until the folder is back under that name, synced to this Mac again in "
            f"its app, or its [[source]] path in {cfg} is fixed; a sync still mirrors the folders that are "
            "there, and that folder's pages are kept"
        )
        alone = clause.replace("; 1 of them is", "; it is").replace(
            "; a sync still mirrors the folders that are there, and that folder's pages", "; its pages"
        )
    tables = [local_source_table("project-beta", gone), _inbox(env)]
    if synced == 2:
        tables.insert(0, local_source_table("project-alpha", kept))
    _write_config(env, *tables)
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert f"[synced] {gone}" in cp.stdout.splitlines()
    sn = "2 folders" if synced == 2 else "1 folder"
    # a symlinked provider folder lists nothing, so the line promises no list (B13 round 11 M9)
    first = "" if layout == "a symlinked provider folder" else ": first the 1 not on disk, then the list"
    assert cp.stdout.splitlines()[0] == f"already synced on this Mac: {sn} (marked [synced] below{first})"
    head = f"NEXT: this Mac already syncs {sn}, and a re-run keeps {'them' if synced == 2 else 'it'}: "
    assert last_line(cp).startswith(head)
    assert last_line(cp).endswith(clause if synced == 2 else alone), last_line(cp)
    assert ("1 of them" in last_line(cp)) == (synced == 2) and "nothing syncs" not in last_line(cp)
    assert steps(install_log(env))[-1][:3] == ("list-folders", "done", "0")
    assert install_log(env)[-2].endswith(f" synced={synced} gone=1")
    cp = install_sh(env, "--list-folders")  # no loop: a re-run gives the same exit 0
    assert cp.returncode == 0, cp.stdout + cp.stderr


@pytest.mark.parametrize(
    ("layout", "want"),
    [
        ("gone, no other folder of the app", 0),
        ("gone beside another account's folder", 2),
        ("there and empty", 0),
        ("there with hidden entries only", 0),
        ("there with a folder", 1),
        ("there with a dangling symlink only", 1),
        ("a dangling symlink itself", 1),
        ("there and unreadable", 1),
    ],
)
def test_signed_out_reads_a_provider_folder_as_doctor_does(tmp_path: Path, layout: str, want: int) -> None:
    """B13 round 12 N61, review: the list's sign-in exit and doctor's fix follow one rule. A symlink, dangling
    or not, is there (doctor's lexists and iterdir), and a folder this account cannot read is not shown empty
    (doctor's OSError keeps the path fix), so neither asks a signed-in person to sign in."""
    cs = tmp_path / "CloudStorage"
    p = cs / "OneDrive-Contoso"
    cs.mkdir()
    if layout == "gone beside another account's folder":
        (cs / "OneDrive-Fabrikam").mkdir()
    elif layout.startswith("there"):
        p.mkdir()
        if layout == "there with hidden entries only":
            (p / ".tmp.drivedownload").mkdir()
        elif layout == "there with a folder":
            (p / "Project Alpha").mkdir()
        elif layout == "there with a dangling symlink only":
            (p / "Project Alpha").symlink_to(tmp_path / "nowhere")
        elif layout == "there and unreadable":
            (p / "Project Alpha").mkdir()
            p.chmod(0)
    elif layout == "a dangling symlink itself":
        p.symlink_to(tmp_path / "nowhere")
    try:
        cp = subprocess.run(
            [BASH32, "-c", f'{_shell_function("signed_out")}\nsigned_out "$1" "$2"', "-", str(cs), p.name],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", **xcrun_db()},
        )
    finally:
        if p.is_dir() and not p.is_symlink():
            p.chmod(0o700)
    assert (cp.returncode, cp.stderr) == (want, ""), layout


@pytest.mark.parametrize(
    ("library", "beside", "there", "want"),
    [
        ("OneDrive-SharedLibraries-Contoso", "OneDrive-Contoso", False, 3),
        ("OneDrive-SharedLibraries-Contoso ", "OneDrive-Contoso", False, 3),  # org_of trims the organization
        ("OneDrive-SharedLibraries-Contoso", "OneDrive-Contoso", True, 3),  # there and empty (O52)
        ("OneDrive-SharedLibraries-Contoso", "OneDrive-Fabrikam", False, 2),
        ("OneDrive-SharedLibraries-Contoso", "OneDrive-Fabrikam", True, 0),
        ("OneDrive-SharedLibraries-Contoso", None, False, 0),
        ("OneDrive-SharedLibraries-Contoso", None, True, 0),
        ("OneDrive-SharedLibraries-Personal", "OneDrive-Personal", False, 2),  # org_of: no organization
        ("OneDrive-SharedLibraries-", "OneDrive-", False, 2),
    ],
)
def test_signed_out_reads_a_sharepoint_library_as_doctor_does(
    tmp_path: Path, library: str, beside: str | None, there: bool, want: int
) -> None:
    """B13 round 13 O41, O48, O57 (one defect; O52's list half): a SharePoint library folder gone beside its
    own organization's OneDrive folder returned 2, so the list told a signed-in person to sign in with another
    account, the fix doctor withdrew in round 12 N53. It is 3 now, and so is the library folder there and
    showing nothing beside it (O52: doctor names the library's Sync there too). The organization is org_of's:
    trimmed, never empty or Personal, which fall through to the other rules as they do in doctor."""
    cs = tmp_path / "CloudStorage"
    cs.mkdir()
    if beside is not None:
        (cs / beside).mkdir()
    if there:
        (cs / library).mkdir()
    cp = subprocess.run(
        [BASH32, "-c", f'{_shell_function("signed_out")}\nsigned_out "$1" "$2"', "-", str(cs), library],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", **xcrun_db()},
    )
    assert (cp.returncode, cp.stderr) == (want, ""), (library, beside, there)


@pytest.mark.parametrize(("libraries", "alone"), [(1, False), (2, False), (1, True)])
def test_list_folders_names_a_sharepoint_library_not_synced_as_doctor_does(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, libraries: int, alone: bool
) -> None:
    """B13 round 13 O41, O48, O57: with OneDrive-Contoso there and the source under
    OneDrive-SharedLibraries-Contoso gone, the list's exit-0 NEXT said "sign in to that app with the account
    it belongs to", which v21 can read as a sign-in to wait for. It now says, as doctor does, to sync the
    library with Sync on its SharePoint page, with no sign-in words, and gone= still counts it."""
    cs = _cloud(env)
    kept = cs / "OneDrive-Contoso" / "Project Alpha"
    kept.mkdir(parents=True)
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    gone = [cs / "OneDrive-SharedLibraries-Contoso" / "Team Site - Documents"]
    if libraries == 2:
        gone.append(cs / "OneDrive-SharedLibraries-Fabrikam" / "Finance - Documents" / "Budget")
        (cs / "OneDrive-Fabrikam").mkdir()
    tables = [local_source_table(f"lib-{n}", g) for n, g in enumerate(gone)]
    if not alone:  # B13 round 15 S36: alone, the library is the one synced folder
        tables.insert(0, local_source_table("project-alpha", kept))
    _write_config(env, *tables, _inbox(env))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    paths = "; ".join(str(g) for g in gone)
    tail = (
        f"path in {cfg}), so step 2 runs no first sync until then; a sync "
        "still mirrors the folders that are there"
    )
    clause = (
        "; 1 of them is not on disk because its SharePoint library is not synced on this Mac (its "
        f"organization's OneDrive folder is there): {paths}. Step 2's status fails on it and names the fix "
        "(sync the SharePoint library to this Mac with Sync on its SharePoint page, or fix its [[source]] "
        if libraries == 1
        else "; 2 of them are not on disk because their SharePoint libraries are not synced on this Mac "
        f"(each organization's OneDrive folder is there): {paths}. Step 2's status fails on each and names "
        "the fix (sync each SharePoint library to this Mac with Sync on its SharePoint page, or fix its "
        "[[source]] "
    ) + tail
    if alone:
        clause = clause.replace("; 1 of them is", "; it is").removesuffix(
            "; a sync still mirrors the folders that are there"
        )
    assert last_line(cp).endswith(clause), last_line(cp)
    assert "sign in" not in last_line(cp).lower()
    assert install_log(env)[-2].endswith(f" synced={libraries + (not alone)} gone={libraries}")
    monkeypatch.setenv("HOME", env["HOME"])
    config = load_config(cfg)
    first = 0 if alone else 1
    for source in config.sources[first : first + libraries]:
        fix = doctor._missing_source_fix(source, source.path)
        assert "Sync on its SharePoint page" in fix and "sign in" not in fix, fix


@pytest.mark.parametrize(
    ("provider", "app"),
    [("Dropbox", "Dropbox"), ("GoogleDrive-someone@contoso.com", "Google Drive"), ("Box-Box", "Box")],
)
def test_list_folders_names_the_app_whose_folder_is_signed_out(
    env: dict[str, str], provider: str, app: str
) -> None:
    """B13 round 12 N41, N61: a missing folder of another app read "OneDrive is not signed in" on a Mac
    whose OneDrive is signed in and full. The NEXT names the app of the missing folder's provider."""
    cs = _cloud(env)
    alpha = cs / "OneDrive-Contoso" / "Project Alpha"
    alpha.mkdir(parents=True)
    team = cs / provider / "Team"
    _write_config(
        env, local_source_table("project-alpha", alpha), local_source_table("team", team), _inbox(env)
    )
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (3, ""), cp.stdout + cp.stderr
    assert last_line(cp) == (
        f"NEXT: {app} is not signed in on this Mac, so it is missing 1 of the folders this Mac already "
        f"syncs: sign in to {app} (or let it finish setting up), then re-run: {INSTALL_SH} "
        "--list-folders; the folders marked [synced] are kept"
    )
    assert "OneDrive" not in last_line(cp)
    token = {"Dropbox": "dropbox", "Google Drive": "googledrive", "Box": "box"}[app]
    assert install_log(env)[-2].endswith(f" result=failed note=not-signed-in apps={token} missing=1 synced=2")


@pytest.mark.parametrize("n", [3, 2])
def test_list_folders_names_every_app_whose_folder_is_signed_out(env: dict[str, str], n: int) -> None:
    """B13 round 15 S40: the apps are joined as the report joins them ("A, B and C"), and two read as
    plural."""
    cs = _cloud(env)
    cs.mkdir(parents=True)
    tables = [
        local_source_table(i, cs / p / "Team")
        for i, p in (("a", "OneDrive-Contoso"), ("b", "Dropbox"), ("c", "ProtonDrive-Contoso"))[:n]
    ]
    _write_config(env, *tables, _inbox(env))
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    apps = "Dropbox, OneDrive and ProtonDrive" if n == 3 else "Dropbox and OneDrive"
    assert last_line(cp).startswith(
        f"NEXT: {apps} are not signed in on this Mac, so this Mac is missing {n} of the folders this Mac "
        f"already syncs: sign in to {apps} (or let it finish setting up)"
    ), last_line(cp)
    # B13 round 13 O5: fixed words in the list's order, an app outside them as "other", never its folder name
    tokens = "dropbox,onedrive,other" if n == 3 else "dropbox,onedrive"
    assert install_log(env)[-2].endswith(
        f" result=failed note=not-signed-in apps={tokens} missing={n} synced={n}"
    ), install_log(env)[-2]


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
        "already synced on this Mac: 1 folder (marked [synced] below)",  # B13 round 11 M9: no list follows
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
        "already synced on this Mac: 2 folders (marked [synced] below: first the 1 outside the list, then "
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


def _committed_checkout(tmp_path: Path) -> tuple[Path, str]:
    """A clean git checkout holding a copy of install.sh, and its commit as install.sh reads it."""
    checkout = tmp_path / "src" / "agent-context-sync"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(INSTALL_SH, checkout / "scripts" / "install.sh")
    (checkout / "pyproject.toml").write_text('[project]\nname = "agentsync"\n')
    git = _git_checkout(checkout)
    head = subprocess.run(
        [*git, "rev-parse", "--short=12", "HEAD"], check=True, capture_output=True, text=True
    )
    return checkout, head.stdout.strip()


@pytest.mark.skipif(not _have_git(), reason="needs developer tools (git)")
@pytest.mark.parametrize(
    "stamp", ["this checkout's", "an older commit's", "a changed tree", "this checkout's, a changed pandoc"]
)
def test_step_1_says_whether_step_2_reinstalls_before_a_config_it_cannot_load(
    env: dict[str, str], tmp_path: Path, stamp: str
) -> None:
    """B13 round 11 M37: step 1 said "Step 2 reinstalls agentsync first" for a config the installed agentsync
    failed on, though step 2 skips the reinstall when the install stamp names this clean checkout's commit;
    then the config itself is at fault. Both read one test (install_is_current), and step 2's own result
    agrees."""
    checkout, head = _committed_checkout(tmp_path)
    script = checkout / "scripts" / "install.sh"
    cs = _cloud(env)
    (cs / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    cfg = _write_config(env, local_source_table("fy26", cs / "OneDrive-Contoso" / "FY26 Projects"))
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[source]]\nid = \n", encoding="utf-8")
    py = _tool_python(env)
    bin_ = Path(env["HOME"]) / ".local" / "bin"
    bin_.mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], bin_ / "agentsync")
    commit = head if stamp != "an older commit's" else "0123456789ab"
    (py.parent.parent / ".agentsync-install-source").write_text(f"commit={commit} source={checkout}")
    subprocess.run([BASH32, "-c", STUB_PYPANDOC], env=env, check=True)
    if stamp == "this checkout's, a changed pandoc":  # B13 round 16 T54: step 2 reinstalls it, and says so
        site = py.parent.parent / "lib" / "python3.11" / "site-packages"
        (site / "pypandoc" / "files" / "pandoc").write_bytes(FAKE_WHEEL_PANDOC.replace(b"9.9", b"0.0"))
    if stamp == "a changed tree":
        (checkout / "pyproject.toml").write_text('[project]\nname = "agentsync"\nversion = "0"\n')
    cp = install_sh(env, "--list-folders", script=script)
    assert install_log(env)[-2].endswith(" marks=failed:ConfigError"), install_log(env)[-2]
    opening = f"this Mac has a config ({cfg}) whose folders could not be read: the installed agentsync failed"
    assert opening in last_line(cp), "the words the prompt's step 1 exception reads"
    current = stamp == "this checkout's"
    assert last_line(cp).endswith(
        f"The installed agentsync is already this checkout's build (commit {head}), so step 2 (run from this "
        "checkout) does not reinstall it: step 2 stops at its config step, and nothing is kept or added "
        "until the person fixes the config"
        if current
        else "Step 2 reinstalls agentsync first, and if that version cannot load it either, step 2 stops at "
        "its config step and nothing is kept or added until the config is fixed"
    ), last_line(cp)
    # B13 round 12 N29: with no reinstall, every --source-local fails at add-source, so the head asks for
    # none; the other two can still load the config after step 2's reinstall, so they ask as before
    assert last_line(cp).startswith(
        f"NEXT: no folder can be added until the config is fixed: run {script} with no --source-local, which "
        f"shows the error; {opening}"
        if current
        else "NEXT: choose the folders to sync from the list above"
    ), last_line(cp)
    assert ("choose the folders" in last_line(cp)) != current
    install_sh(env, script=script)
    assert (("agentsync", "skipped", "0", "current") in steps(install_log(env))) == current


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
        "already synced on this Mac: 2 folders (marked [synced] below)",
        f"[synced] {cs}/OneDrive-Contoso/Project Alpha",
        f"[inside a synced folder] {cs}/OneDrive-Contoso/Project Alpha/Inner",
        f"[synced] {cs}/OneDrive-Contoso/Project Beta",
        f"{cs}/OneDrive-Contoso/Project Gamma",
    ]
    assert last_line(cp).startswith("NEXT: this Mac already syncs 2 folders, and a re-run keeps them: ")
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
        "already synced on this Mac: 1 folder (marked [synced] below)",
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


VANISHED_BASELINE_NAMES = {
    "agentsync.config": {"canonical_source_root", "derive_source_id", "load_config"},
    "agentsync.paths": {"expand"},
}
"""What install.sh's vanished-source reader may import from agentsync with no fallback (every agentsync since
2026-09-29 has them); ``PRESENT_STATES`` (2026-10-15) only behind a guard."""


def test_the_vanished_source_reader_imports_no_agentsync_name_an_older_build_lacks() -> None:
    """B13 round 16 T6, T30: step 1's vanished-source reader runs the agentsync an earlier setup left, like
    the synced-folder reader. Its unguarded imports are baseline names, ``PRESENT_STATES`` falls back to the
    standard library's list of the same states, and its query is the manifest's ``present_counts`` (the one
    ``cycle.vanished_sources`` and the report read), on a connection opened read-only."""
    from agentsync import manifest  # noqa: PLC0415
    from agentsync.model import PRESENT_STATES  # noqa: PLC0415

    tree = _install_sh_python("vanished_folders")
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agentsync"):
            extra = {a.name for a in node.names} - VANISHED_BASELINE_NAMES.get(node.module or "", set())
            assert not extra, f"unguarded import of {sorted(extra)} from {node.module}"
            seen |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("agentsync") for a in node.names), ast.unparse(node)
        elif isinstance(node, ast.Try) and any(isinstance(n, ast.ImportFrom) for n in node.body):
            [handler] = node.handlers
            assert isinstance(handler.type, ast.Name) and handler.type.id == "ImportError", "ImportError only"
            assert "agentsync" not in ast.unparse(handler), "a fallback uses the standard library alone"
            [fallback] = [n for n in handler.body if isinstance(n, ast.Assign)]
            assert ast.literal_eval(fallback.value) == [st.value for st in PRESENT_STATES]
            seen |= {a.name for n in node.body if isinstance(n, ast.ImportFrom) for a in n.names}
    assert seen == {"canonical_source_root", "derive_source_id", "expand", "load_config", "PRESENT_STATES"}
    code = ast.unparse(tree)
    present = Path(manifest.__file__).read_text(encoding="utf-8")
    for words in ("SELECT source_id, COUNT(*) FROM items WHERE is_dir = 0 ", "AND state IN ("):
        assert words in code and words in present, words
    assert "?mode=ro" in code and "Manifest" not in code


def _state_db(env: dict[str, str]) -> Path:
    """The manifest path of the config in ``env``'s home, as install.sh's agentsync expands it there."""
    with patch.dict(os.environ, {"HOME": env["HOME"]}):
        return paths.expand(load_config(Path(env["HOME"]) / "agent-context" / "sources.toml").state_paths.db)


def _manifest_rows(
    env: dict[str, str], *rows: tuple[str, str, int], roots: dict[str, Path] | None = None
) -> None:
    """A manifest at the config's state path holding ``rows`` (source id, state, count) of files and the root
    each source of ``roots`` was synced under (meta ``scope_root:<id>``, as a sync records it): only what step
    1's vanished-source reader reads."""
    db = _state_db(env)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    with contextlib.closing(conn), conn:
        conn.execute("CREATE TABLE items (source_id TEXT, is_dir INTEGER, state TEXT)")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for sid, state, n in rows:
            conn.executemany("INSERT INTO items VALUES (?, 0, ?)", [(sid, state)] * n)
            conn.execute("INSERT INTO items VALUES (?, 1, ?)", (sid, state))
        for sid, root in (roots or {}).items():
            conn.execute(
                "INSERT INTO meta VALUES (?, ?)", (f"scope_root:{sid}", f"3:{canonical_source_root(root)}")
            )


def _tool_python_without(env: dict[str, str], module: str, name: str) -> Path:
    """:func:`_tool_python` whose ``module`` lacks ``name`` when install.sh's ``-c`` code runs: an older
    agentsync's, or one that cannot run a reader."""
    py = _tool_python(env)
    run = 'exec(compile(sys.argv.pop(1), "<string>", "exec"))'
    shim = f"import sys, {module} as _m\ndel _m.{name}\ndel _m\n{run}\n"
    return _write_exe(
        py,
        "#!/bin/bash\n"
        '[ "$1" = -I ] && [ "$2" = -c ] || exec "' + sys.executable + '" "$@"\n'
        'code="$3"\nshift 3\n'
        f'exec "{sys.executable}" -I -c \'{shim}\' "$code" "$@"\n',
    )


def _vanished_list(env: dict[str, str]) -> tuple[Path, Path, Path]:
    """CloudStorage with the folders Project Alpha and Project Beta, a config that syncs Alpha alone (the
    inbox beside it): the state of probe CF, Beta's block deleted."""
    cs = _cloud(env)
    alpha, beta = (cs / "OneDrive-Contoso" / name for name in ("Project Alpha", "Project Beta"))
    for d in (alpha, beta):
        d.mkdir(parents=True)
    cfg = _write_config(env, local_source_table("project-alpha", alpha), _inbox(env))
    return cfg, alpha, beta


@pytest.mark.parametrize("older", [False, True])
def test_list_folders_names_a_mirrored_folder_whose_block_is_gone(env: dict[str, str], older: bool) -> None:
    """B13 round 16 T6, T30 (probe CF): a folder still mirrored whose [[source]] block was deleted from
    sources.toml was listed unmarked and the NEXT said "(none is needed)", though step 2's status FAILs on it
    (config.vanished_sources) and every sync refuses the config. Step 1 reads the manifest read-only and says
    which listed folder one --source-local adds back under the same id, or that the block is put back; it
    logs only the count. An agentsync from before 2026-10-15 (no ``agentsync.model.PRESENT_STATES``, as on a
    Mac set up earlier) reads the same."""
    cfg, alpha, beta = _vanished_list(env)
    _manifest_rows(
        env,
        ("project-alpha", "live", 3),
        ("project-beta", "live", 14),
        ("project-beta", "dataless", 2),
        roots={"project-alpha": alpha, "project-beta": beta},
    )
    if older:
        _tool_python_without(env, "agentsync.model", "PRESENT_STATES")
    else:
        _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert cp.stdout.splitlines()[:-1] == [
        "already synced on this Mac: 1 folder (marked [synced] below)",
        f"[synced] {alpha}",
        f"{beta}",
    ]
    assert last_line(cp) == (
        f"NEXT: this Mac already syncs 1 folder, and a re-run keeps it: run {INSTALL_SH}, with one "
        '--source-local "<folder>" for each unmarked folder to add from the list above; 1 folder in the list '
        f"above is still mirrored but has no [[source]] block in {cfg}: {beta}. Step 2's status fails on it "
        f'until it is added again (one --source-local "{beta}") or its [[source]] block is put back, as it '
        "was or retired"
    )
    assert install_log(env)[-2].endswith(" result=done note=listed-2 synced=1 vanished=1")
    assert "project-beta" not in "\n".join(install_log(env))


def test_list_folders_names_a_mirrored_folder_to_add_back_as_a_next_shows_a_folder(
    env: dict[str, str],
) -> None:
    """B13 round 17 U88: the vanished folder's command, ``(one --source-local "<folder>")``, put the raw
    path in double quotes, the one --source-local in a NEXT that skipped shown_arg (round 16 T35). A
    OneDrive name with a ``$`` or a backtick then expands when pasted: ``Q$1`` loses ``$1`` and a backtick
    runs a command. It is shown as every NEXT shows a folder, escaped; the listing before it stays the
    list's own text, and the report keeps the name out in either form."""
    cs = _cloud(env)
    alpha = cs / "OneDrive-Contoso" / "Project Alpha"
    odd = cs / "OneDrive-Contoso" / "Q$1 Plans `id`"
    for d in (alpha, odd):
        d.mkdir(parents=True)
    _write_config(env, local_source_table("project-alpha", alpha), _inbox(env))
    sid = derive_source_id(canonical_source_root(odd), {"project-alpha", "inbox"})
    _manifest_rows(
        env, ("project-alpha", "live", 3), (sid, "live", 4), roots={"project-alpha": alpha, sid: odd}
    )
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    shown = str(odd).replace("$", "\\$").replace("`", "\\`")
    assert f": {odd}. Step 2's status fails on it" in last_line(cp), last_line(cp)
    assert f'(one --source-local "{shown}") or its [[source]] block' in last_line(cp), last_line(cp)
    back = subprocess.run(
        ["/bin/bash", "-c", f'printf %s "{shown}"'], capture_output=True, text=True, check=True
    ).stdout
    assert back == str(odd), back
    assert install_sh(env, "--report-only").returncode == 0  # its Installer output holds step 1's NEXT
    for name in ("setup-report.md", "bring-back.md"):
        text = (report_path(env).parent / name).read_text(encoding="utf-8")
        assert "Q$1 Plans" not in text and "Q\\$1 Plans" not in text, name


def _config_step_2_stops_on(env: dict[str, str], state: str) -> Path:
    """Probe PT, GV or MF of round 17's rehearsal A on a Mac that syncs Project Alpha (Project Beta listed
    beside it): a policy.toml with a [policy] value that is not valid, a [governance] remote prefix that is no
    URL (a made-up host), or a manifest that is no database. load_config reads the config all the same."""
    cs = _cloud(env)
    alpha = cs / "OneDrive-Contoso" / "Project Alpha"
    for d in (alpha, cs / "OneDrive-Contoso" / "Project Beta"):
        d.mkdir(parents=True)
    cfg = _write_config(env, local_source_table("project-alpha", alpha), _inbox(env))
    if state == "PT":
        (cfg.parent / "policy.toml").write_text('[policy]\nrefuse_unlabelled = "yes"\n', encoding="utf-8")
    elif state == "GV":
        gov = '[governance]\nallow_remote = true\nremote_url_prefixes = ["git.contoso.invalid/team/x"]\n\n'
        cfg.write_text(gov + cfg.read_text(encoding="utf-8"), encoding="utf-8")
    elif state == "MF":
        db = _state_db(env)
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"Contoso junk, no database\n" * 64)
    elif state in ("GR", "GR-allowed"):  # B13 round 18 V9, V36: a docs-repo remote, refused or allowed
        docs = Path(env["HOME"]) / "agent-context" / "docs"
        docs.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(docs)], check=True)
        url = "https://git.contoso.example/team/kb.git"
        subprocess.run(["git", "-C", str(docs), "remote", "add", "origin", url], check=True)
        if state == "GR-allowed":
            gov = '[governance]\nallow_remote = true\nremote_url_prefixes = ["https://git.contoso.example/team"]\n\n'
            cfg.write_text(gov + cfg.read_text(encoding="utf-8"), encoding="utf-8")
    elif state == "TD":  # B13 round 18 V21: the schema reads, a table's first page does not
        _manifest_rows(env, ("project-alpha", "live", 3))
        db = _state_db(env)
        conn = sqlite3.connect(db)
        with contextlib.closing(conn):
            [(root,)] = conn.execute("SELECT rootpage FROM sqlite_master WHERE name = 'items'").fetchall()
            [(size,)] = conn.execute("PRAGMA page_size").fetchall()
        with db.open("r+b") as fh:
            fh.seek((root - 1) * size)
            fh.write(b"Contoso junk page" * 4)
    elif state.startswith("TD-") or state == "clean-real":  # B13 round 19 W1, W10: the real schema
        roots = {"TD-canonical": "items_by_canonical", "TD-path": "items_by_path", "clean-real": None}
        tree = roots.get(state, "items")
        _real_manifest_damaged(_state_db(env), tree)
    return cfg


def _real_manifest_damaged(db: Path, tree: str | None) -> None:
    """A manifest with agentsync's own schema (items has four indexes and its key's) holding 7 items of
    project-alpha, out of WAL, with the root page of b-tree ``tree`` overwritten when it is given."""
    db.parent.mkdir(parents=True, exist_ok=True)
    Manifest(db).close()
    conn = sqlite3.connect(db)
    with contextlib.closing(conn):
        with conn:
            conn.execute(
                "INSERT INTO sources (source_id, kind, config_state, config_fingerprint) "
                "VALUES ('project-alpha', 'local', 'active', 'x')"
            )
            conn.executemany(
                "INSERT INTO items (source_id, stable_id, name, rel_path, state, first_seen_run, "
                "last_seen_run, canonical_sha256) VALUES ('project-alpha', ?, ?, ?, 'live', 1, 1, ?)",
                [(str(i), f"f{i}.docx", f"f{i}.docx", f"{i:064x}") for i in range(7)],
            )
        conn.execute("PRAGMA journal_mode=DELETE").fetchall()
        if tree is None:
            return
        [(root,)] = conn.execute("SELECT rootpage FROM sqlite_master WHERE name = ?", (tree,)).fetchall()
        [(size,)] = conn.execute("PRAGMA page_size").fetchall()
    with db.open("r+b") as fh:
        fh.seek((root - 1) * size)
        fh.write(b"Contoso junk page" * 4)


CONFIG_STOP = {
    "PT": "an invalid [policy] in policy.toml",
    "GV": "an invalid [governance] value in {cfg}",
    "MF": "agentsync's state file, which cannot be read",
    # B13 round 19 W41: this build's probe reads each table's first row and add-source stops on it, so a
    # damaged table page is the config step's stop; an older installed build still says "state-table"
    "TD": "agentsync's state file, which cannot be read",
    "TD-items": "agentsync's state file, which cannot be read",
    # B13 round 20 X50: the probe reads each index's first entry too (INDEXED BY), so a damaged index root is
    # the config step's stop as well
    "TD-canonical": "agentsync's state file, which cannot be read",
    "TD-path": "agentsync's state file, which cannot be read",
    "GR": "a git remote on the docs repo that [governance] does not allow",
}


@pytest.mark.parametrize(
    "state",
    [
        "PT",
        "GV",
        "MF",
        "GR",
        "TD",
        "TD-items",
        "TD-canonical",
        "TD-path",
        "TD-items-older",
        "GR-allowed",
        "clean",
        "clean-real",
    ],
)
def test_list_folders_says_step_2_stops_on_a_config_it_refuses(env: dict[str, str], state: str) -> None:
    """B13 round 17 U12 (probes PT, GV, MF): step 1's readers call load_config alone, which reads neither
    policy.toml nor [governance], and a damaged manifest read as no vanished source, so the NEXT ended "(none
    is needed)" and step 2 then stopped at its config step on an error that was already there. A third reader
    asks policy.load_policy, governance.load_governance and doctor's read-only manifest probe, and the NEXT
    says what step 2 stops on, the person's to fix, in fixed words: never the error's text (the host, the
    label), in the NEXT or in install.log. The installed agentsync here is not this checkout's build, so it
    says step 2 reinstalls first. A clean config says "(none is needed)" as before.
    B13 round 18 V9, V21, V36 (probes GR, TD): a docs-repo remote [governance] does not allow, which init and
    add-source exit 1 on, and a damaged table page under a schema that reads, which step 2's status or first
    sync stops on, also ended "(none is needed)". Both are named now, never the remote's host; a remote the
    table allows is no stop.
    B13 round 19 W1, W10 (probe TD on agentsync's own schema): SELECT 1 from items is answered from its
    covering index items_by_canonical, so a damaged items root page, or a damaged items_by_path, still ended
    "(none is needed)". Each table is read NOT INDEXED and each index INDEXED BY; a clean manifest of the
    same schema is no stop. TD-items-older: an installed build without doctor.manifest_unreadable reads the
    schema alone, so the damaged items page is this reader's to name.
    B13 round 20 X1, X25, X71, X72: the "state-table" tail's "(at its status or its first sync)" was false for
    TD-canonical and TD-path (step 2 passed: nothing it ran read the index) and named the wrong step for
    TD-items-older (step 2's reinstalled build stops at its config step). Every damaged root page now gives
    the config step's state clause."""
    cfg = _config_step_2_stops_on(env, state)
    if state.endswith("-older"):
        _tool_python_without(env, "agentsync.ops.doctor", "manifest_unreadable")
    else:
        _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    head = f"NEXT: this Mac already syncs 1 folder, and a re-run keeps it: run {INSTALL_SH}, with one "
    assert last_line(cp).startswith(head), last_line(cp)
    logged = install_log(env)[-2]
    for text in (last_line(cp), "\n".join(install_log(env))):
        assert "contoso.example" not in text and "Contoso junk" not in text, text
    if state in ("clean", "clean-real", "GR-allowed"):
        assert last_line(cp).endswith("(none is needed)") and "config_err" not in logged, logged
        return
    # B13 round 20 X1, X25, X71, X72: "state-table" said step 2 stops "at its status or its first sync",
    # false for an index root (a status and a sync with nothing new read none, and step 2 passed), and the
    # wrong step for a table root under an older build (this checkout's add-source stops on it). This
    # checkout's agentsync reads every root page (round 19 W41, round 20 X50), and step 2 reinstalls it, so
    # the word gives the config step's state clause; the logged word is the installed build's own reading
    cause = CONFIG_STOP.get(state, CONFIG_STOP["TD"]).format(cfg=cfg)
    assert last_line(cp).endswith(
        "; step 2 reinstalls agentsync first, and if this checkout's agentsync rejects it too, step 2 stops "
        f"at its config step on {cause}: fixing that is the person's, and ~/.local/bin/agentsync status "
        "prints the error"
    ), last_line(cp)
    assert "(none is needed)" not in last_line(cp)
    word = {
        "PT": "policy",
        "GV": "governance",
        "MF": "state",
        "TD": "state",
        "TD-items": "state",
        "GR": "remote",
        "TD-items-older": "state-table",
    }.get(state)
    if word is None:  # TD-canonical, TD-path: "state" once this build's probe reads index roots (X50)
        word = "state" if doctor.manifest_unreadable(_state_db(env)) is not None else "state-table"
    assert "at its status or its first sync" not in last_line(cp), last_line(cp)
    assert logged.endswith(f" note=listed-2 synced=1 config_err={word}"), logged
    for text in (last_line(cp), "\n".join(install_log(env))):
        assert "contoso.invalid" not in text and "refuse_unlabelled" not in text and "junk" not in text


def test_list_folders_names_the_config_step_2_stops_on_when_the_build_is_this_checkouts(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 17 U12: when the installed agentsync is already this clean checkout's build, step 2 does
    not reinstall it, so the clause says plainly that step 2 stops (round 11 M37's rule). An older build
    that lacks one of the loaders reads nothing for it: the list is as before."""
    checkout, head = _committed_checkout(tmp_path)
    script = checkout / "scripts" / "install.sh"
    _config_step_2_stops_on(env, "PT")
    py = _tool_python(env)
    bin_ = Path(env["HOME"]) / ".local" / "bin"
    bin_.mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], bin_ / "agentsync")
    (py.parent.parent / ".agentsync-install-source").write_text(f"commit={head} source={checkout}")
    subprocess.run([BASH32, "-c", STUB_PYPANDOC], env=env, check=True)
    cp = install_sh(env, "--list-folders", script=script)
    assert last_line(cp).endswith(
        "; step 2 stops at its config step on an invalid [policy] in policy.toml: fixing that is the "
        "person's, and ~/.local/bin/agentsync status prints the error"
    ), last_line(cp)
    _tool_python_without(env, "agentsync.policy", "load_policy")
    cp = install_sh(env, "--list-folders", script=script)
    assert cp.returncode == 0 and last_line(cp).endswith("(none is needed)"), last_line(cp)


def _tool_python_as_another_account(env: dict[str, str]) -> Path:
    """:func:`_tool_python` whose ``os.getuid`` answers another account's uid when install.sh's ``-c`` code
    runs, so every file the test made reads as another account's (a test cannot chown)."""
    py = _tool_python(env)
    run = 'exec(compile(sys.argv.pop(1), "<string>", "exec"))'
    shim = f"import os, sys\n_uid = os.getuid() + 1\nos.getuid = lambda: _uid\n{run}\n"
    return _write_exe(
        py,
        "#!/bin/bash\n"
        '[ "$1" = -I ] && [ "$2" = -c ] || exec "' + sys.executable + '" "$@"\n'
        'code="$3"\nshift 3\n'
        f'exec "{sys.executable}" -I -c \'{shim}\' "$code" "$@"\n',
    )


STATE_OPEN_TAIL = {
    "state-access": (
        "; step 2 stops at its config step on agentsync's state file, which this account cannot open (a "
        "mode, not damage): the chmod that fixes it is the agent's to run, and ~/.local/bin/agentsync status "
        "prints it on its state_dir lines"
    ),
    "state-owner": (
        "; step 2 stops at its config step on agentsync's state file, which another account owns, so this "
        "one cannot open it (not damage): fixing that is the person's (a sudo chown; the agent runs no "
        "sudo), and ~/.local/bin/agentsync status prints it on its state_dir lines"
    ),
}
"""Step 1's tails for a state file this account cannot open (B13 round 20 X9, X29)."""


@pytest.mark.parametrize("state", ["PM", "PM-wal", "PM-folder", "PM-owner", "PM-fixed"])
def test_list_folders_says_step_2_stops_on_a_state_file_this_account_cannot_open(
    env: dict[str, str], state: str
) -> None:
    """B13 round 20 X9, X29 (probes PM, 51): with manifest.sqlite at mode 0000, step 1's manifest probes
    failed to open it (CANTOPEN, which neither reads as damage) and the NEXT ended "(none is needed)", while
    step 2 then stopped at its config step ("cannot be opened: Permission denied"). Step 1 now reads the
    owner and modes of the state folder, the manifest, its -wal and its -shm, as doctor's state_dir lines
    do, and names that stop: this account's own file is a chmod the agent runs (X26), another account's a
    sudo chown, the person's. Neither is worded as damage. Put back at 0600, the clause is gone."""
    _config_step_2_stops_on(env, "clean-real")
    db = _state_db(env)
    wal = db.with_name(db.name + "-wal")
    word = "state-owner" if state == "PM-owner" else "state-access"
    try:
        if state == "PM-owner":
            _tool_python_as_another_account(env)
        else:
            _tool_python(env)
        if state in ("PM", "PM-fixed"):
            db.chmod(0)
        elif state == "PM-wal":
            wal.write_bytes(b"")
            wal.chmod(0o400)
        elif state == "PM-folder":
            db.parent.chmod(0)
        if state == "PM-fixed":
            db.chmod(0o600)
        cp = install_sh(env, "--list-folders")
    finally:
        db.parent.chmod(0o700)
        for f in (db, wal):
            if f.exists():
                f.chmod(0o600)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    logged = install_log(env)[-2]
    if state == "PM-fixed":
        assert last_line(cp).endswith("(none is needed)") and "config_err" not in logged, logged
        return
    assert last_line(cp).endswith(STATE_OPEN_TAIL[word]), last_line(cp)
    assert "(none is needed)" not in last_line(cp) and "reinstalls" not in last_line(cp), last_line(cp)
    assert "damaged" not in last_line(cp) and "cannot be read" not in last_line(cp), last_line(cp)
    assert logged.endswith(f" note=listed-2 synced=1 config_err={word}"), logged


def test_list_folders_names_a_mirrored_source_that_is_no_listed_folder_without_its_id(
    env: dict[str, str],
) -> None:
    """B13 round 16 T6, T30: a vanished source no listed folder adds back (a custom id, another kind) gets
    the put-back alone, by count, never its id; tombstones and an inbox step 2 adds back are not counted.
    A listed folder of the same name is no match unless the manifest recorded it as that source's root (a
    folder named Project Beta elsewhere is another folder), nor is one with no recorded root. With no live
    folder left, the folder question's NEXT carries the same words."""
    cfg, _alpha, beta = _vanished_list(env)
    cfg.write_text(default_config_text(), encoding="utf-8")  # no inbox, no folder
    elsewhere = Path(env["HOME"]) / "Documents" / "Project Beta"
    _manifest_rows(
        env,
        ("contoso-notes", "live", 3),
        ("contoso-mail", "refused", 1),
        ("old-source", "tombstone", 5),
        ("inbox", "live", 2),
        ("project-beta", "live", 4),
        ("project-alpha", "live", 1),
        roots={"project-beta": elsewhere},
    )
    _tool_python(env)
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    assert last_line(cp) == (
        f"{LIST_NEXT}; 4 sources that no folder in the list above adds back are still mirrored but have no "
        f"[[source]] block in {cfg}. Step 2's status fails on each until its [[source]] block is put back, "
        "as it was or retired"
    )
    assert str(beta) not in last_line(cp)
    assert install_log(env)[-2].endswith(" result=done note=listed-2 synced=0 vanished=4")
    assert "contoso" not in cp.stdout + "\n".join(install_log(env))


@pytest.mark.parametrize("manifest", ["none", "not a database", "a reader that fails"])
def test_list_folders_is_as_before_when_the_vanished_source_reader_reads_nothing(
    env: dict[str, str], manifest: str
) -> None:
    """B13 round 16 T6, T30: the reader is allowed to fail. No manifest, one it cannot read, and an installed
    agentsync it cannot run (one without derive_source_id) leave the list, its marks and "(none is needed)"
    as they were. B13 round 17 U12: a manifest that is no database is what step 2's config step stops on, so
    that NEXT says so (config_errors, not the vanished reader) in place of "(none is needed)"."""
    _cfg, alpha, beta = _vanished_list(env)
    _tool_python(env)
    if manifest == "not a database":
        db = _state_db(env)
        db.parent.mkdir(parents=True)
        db.write_bytes(b"not a database, " * 64)
    elif manifest == "a reader that fails":
        _manifest_rows(env, ("project-beta", "live", 14), roots={"project-beta": beta})
        _tool_python_without(env, "agentsync.config", "derive_source_id")
    cp = install_sh(env, "--list-folders")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    tail, logged = " (none is needed)", ""
    if manifest == "not a database":
        tail = (
            "; step 2 reinstalls agentsync first, and if this checkout's agentsync rejects it too, step 2 "
            f"stops at its config step on {CONFIG_STOP['MF']}: fixing that is the person's, and "
            "~/.local/bin/agentsync status prints the error"
        )
        logged = " config_err=state"
    assert cp.stdout.splitlines() == [
        "already synced on this Mac: 1 folder (marked [synced] below)",
        f"[synced] {alpha}",
        f"{beta}",
        f"NEXT: this Mac already syncs 1 folder, and a re-run keeps it: run {INSTALL_SH}, with one "
        f'--source-local "<folder>" for each unmarked folder to add from the list above{tail}',
    ]
    assert install_log(env)[-2].endswith(f" result=done note=listed-2 synced=1{logged}")


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
        "already synced on this Mac: 1 folder (marked [synced] below)",
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
        "already synced on this Mac: 1 folder (marked [synced] below: first the 1 outside the list, then "
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
@PYPANDOC@elif [ "$1 $2 ${3:-}" = "tool dir --bin" ]; then
  echo "$HOME/.local/bin"
elif [ "$1 $2" = "tool dir" ]; then
  echo "$HOME/.local/share/uv/tools"
fi
""".replace("@PYPANDOC@", STUB_PYPANDOC)
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


def test_a_mirrored_folder_whose_block_is_gone_is_named_in_step_1_and_added_back_in_step_2(
    real_env: dict[str, str], wheel: Path
) -> None:
    """B13 round 16 T6, T30 (probe CF), with the real agentsync: a folder synced once, then its [[source]]
    block deleted from sources.toml while its pages stay mirrored. Step 1 named no such folder and said
    "(none is needed)"; step 2 then stopped on [FAIL] config.vanished_sources. Step 1 now names the folder
    and the one --source-local that adds it back: step 2 with no folder still stops on that [FAIL], and with
    that folder it exits 0 with sources.toml byte for byte as before the block was deleted."""
    env = real_env
    projects = _cloud(env) / "OneDrive-Contoso"
    alpha, beta = (projects / name for name in ("Project Alpha", "Project Beta"))
    for d in (alpha, beta):
        d.mkdir(parents=True)
        (d / "plan.txt").write_text(f"a made-up plan for {d.name}\n", encoding="utf-8")
    first = install_sh(env, str(wheel), "--source-local", str(alpha), "--source-local", str(beta))
    assert first.returncode == 0, first.stdout + first.stderr
    cfg = Path(env["HOME"]) / "agent-context" / "sources.toml"
    whole = cfg.read_text(encoding="utf-8")
    head, *tables = whole.split("\n[[source]]\n")
    kept = [t for t in tables if not t.startswith('id = "project-beta"\n')]
    assert len(kept) == len(tables) - 1
    cfg.write_text("\n[[source]]\n".join([head, *kept]), encoding="utf-8")
    listed = install_sh(env, "--list-folders")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "(none is needed)" not in listed.stdout
    assert last_line(listed).endswith(
        f"; 1 folder in the list above is still mirrored but has no [[source]] block in {cfg}: {beta}. "
        f'Step 2\'s status fails on it until it is added again (one --source-local "{beta}") or its '
        "[[source]] block is put back, as it was or retired"
    )
    bare = install_sh(env, str(wheel))
    assert bare.returncode == 1 and "[FAIL] config.vanished_sources" in bare.stdout, bare.stdout + bare.stderr
    # B13 round 18 V7, V8: step 2's [FAIL] names the command step 1's NEXT does, install.sh's own
    [fail] = [ln for ln in bare.stdout.splitlines() if "[FAIL] config.vanished_sources" in ln]
    assert fail.endswith(f'under the same id: {cli.loop.INSTALL_SH} --source-local "{beta}")'), fail
    again = install_sh(env, str(wheel), "--source-local", str(beta))
    assert again.returncode == 0, again.stdout + again.stderr
    assert cfg.read_text(encoding="utf-8") == whole


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
        "already synced on this Mac: 1 folder (marked [synced] below)",
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
        f"NEXT: this Mac already syncs 1 folder, and a re-run keeps it: run {INSTALL_SH} (no folder "
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
    assert re.search(r" \(created, no remote; [1-9]\d* scaffold files written\)$", repo), repo
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
    # a config this run created is no config the person wrote: the generic NEXT (B13 round 11 M23, M39)
    assert one_next(cp) and last_line(cp).startswith("NEXT: fix the error above, then re-run: ")


@pytest.mark.parametrize(
    ("call", "knob", "rc", "loads"),
    [
        ("init", "STUB_INIT_RC", "78", False),
        ("add-source", "STUB_ADD_SOURCE_RC", "78", False),
        ("init", "STUB_INIT_RC", "78", True),
        ("add-source", "STUB_ADD_SOURCE_RC", "78", True),
        ("init", "STUB_INIT_RC", "1", True),
        ("add-source", "STUB_ADD_SOURCE_RC", "2", True),
    ],
)
def test_a_config_that_does_not_load_is_the_persons_to_fix(
    env: dict[str, str], folder: Path, wheel: Path, call: str, knob: str, rc: str, loads: bool
) -> None:
    """B13 round 11 M23, M39: a config that was there and does not load (agentsync's exit 78) got fail's
    "fix the error above, then re-run: install.sh", which the prompt's step 2 rule reads two ways (re-run,
    which fails the same way, or edit the person's sources.toml). Its NEXT names the config and its owner.
    Any other exit (add-source's 2 for a folder the agent typed, a failed init) keeps the generic line.
    B13 round 16 T1, T10, T29, T45, T67, T81: an exit 78 on a config the installed agentsync loads is no
    config that "does not load" (the words the prompt skips the inbox on): it loads, but agentsync refuses
    it. The installed agentsync, not the exit code, says which."""
    _tool_python(env)  # the installed agentsync, asked whether the config loads (no helper is built with it)
    cfg = _write_config(env, _inbox(env), "" if loads else "\n[[source]\n")
    args = [str(wheel), "--source-local", str(folder)] if call == "add-source" else [str(wheel)]
    cp = install_sh({**env, knob: rc, "AGENTSYNC_OCR": "0"}, *args)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert ("config", "failed", rc, "") in steps(install_log(env))
    assert one_next(cp)
    status = "~/.local/bin/agentsync status prints it again"
    tail = (
        "fixing it is the person's, not a step for the agent, so show them the error and go on to the report"
    )
    if rc == "78" and not loads:
        assert last_line(cp).startswith(
            f"NEXT: the config {cfg} does not load with this agentsync (the configuration error above; "
            f"{status}): {tail}; once it loads, the next run is: {INSTALL_SH} "
        )
    elif rc == "78":
        assert last_line(cp).startswith(
            f"NEXT: the config {cfg} loads, but agentsync refuses a value in it or in policy.toml beside it "
            f"(the configuration error above names the file; {status}): {tail}; once it is fixed, the next "
            f"run is: {INSTALL_SH} "
        )
        assert "does not load" not in cp.stdout + cp.stderr
    else:
        assert last_line(cp).startswith("NEXT: fix the error above, then re-run: ")


@pytest.mark.parametrize(
    ("state", "call"),
    [
        ("policy.toml", "init"),
        ("[governance] history_days", "init"),
        ("[governance] archive", "add-source"),
        ("policy.toml, a new config", "init"),
    ],
)
def test_a_config_that_loads_but_agentsync_refuses_is_no_config_that_does_not_load(
    real_env: dict[str, str], wheel: Path, state: str, call: str
) -> None:
    """B13 round 16 T1, T10, T29, T45, T67, T81, with the real agentsync: init and add-source exit 78 on a
    config that loads when policy.toml beside it or a [governance] value is not valid, after they wrote the
    inbox or the folder's block. Step 2's NEXT said the config "does not load", so the prompt's step 2 skipped
    the inbox the config holds, and the report said "loads: yes, but a sync refuses it". The NEXT now says
    so too, and keeps the person's stop: shown, not a step for the agent, the report next. A config this run
    created with a bad policy.toml beside it is the same: it loads, and policy.toml is the person's."""
    env = real_env
    home = Path(env["HOME"])
    ctx = home / "agent-context"
    folder = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / "Alpha"
    folder.mkdir(parents=True)
    (folder / "plan.txt").write_text("a made-up plan\n", encoding="utf-8")
    cfg = ctx / "sources.toml"
    if state != "policy.toml, a new config":
        first = install_sh(env, str(wheel), "--source-local", str(folder))
        assert first.returncode == 0, first.stdout + first.stderr
    ctx.mkdir(parents=True, exist_ok=True)
    if state.startswith("policy.toml"):
        (ctx / "policy.toml").write_text('[policy]\nrefuse_unlabelled = "yes"\n', encoding="utf-8")
    else:
        bad = 'history_days = "thirty"' if "history_days" in state else 'archive = "yes"'
        cfg.write_text(cfg.read_text(encoding="utf-8") + f"\n[governance]\n{bad}\n", encoding="utf-8")
    other = folder.parent / "Beta"
    other.mkdir()
    args = [str(wheel), "--source-local", str(other)] if call == "add-source" else [str(wheel)]
    cp = install_sh(env, *args)
    out = cp.stdout + cp.stderr
    assert cp.returncode == 1, out
    assert "configuration error: " in cp.stderr, out
    assert one_next(cp) and last_line(cp).startswith(
        f"NEXT: the config {cfg} loads, but agentsync refuses a value in it or in policy.toml beside it (the "
        "configuration error above names the file; ~/.local/bin/agentsync status prints it again): fixing it "
        "is the person's, not a step for the agent, so show them the error and go on to the report; once it "
        "is fixed, the next run is: "
    ), out
    assert "does not load" not in out
    # B13 round 17 U20: a policy.toml error is not sources.toml's; the NEXT no longer says agentsync refuses
    # the config it names, and names policy.toml beside it (the error above names which)
    assert "agentsync refuses it (" not in last_line(cp)
    if state.startswith("policy.toml"):
        assert "policy.toml: [policy]" in cp.stderr and "or in policy.toml beside it" in last_line(cp), out
    # the config holds the inbox the prompt's step 2 then tells the person about
    assert 'kind = "inbox"' in cfg.read_text(encoding="utf-8")
    assert "- loads: yes, but a sync refuses it: " in report_path(env).read_text(encoding="utf-8")


@pytest.mark.parametrize("more", [False, True])
def test_a_folder_added_before_agentsync_refuses_the_config_is_no_failed_add(
    real_env: dict[str, str], wheel: Path, more: bool
) -> None:
    """B13 round 17 U40, with the real agentsync: add-source appends the folder's block and prints "added
    source", then refuses a [governance] value (exit 78); the block stays. install.sh said "agentsync
    add-source <folder> failed" right after the block, logged no add, and once the person fixed the value the
    re-run said "already configured" and the report "kept ... (none added)". Now the error says the folder was
    added and the config refused, a later folder was not added, and the log counts the add, which the report
    names. A refusal with nothing written keeps "failed"
    (test_a_failed_add_source_still_fails_the_run_through_the_filter)."""
    env = real_env
    ctx = Path(env["HOME"]) / "agent-context"
    cfg = ctx / "sources.toml"
    folder = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / "Alpha"
    folder.mkdir(parents=True)
    (folder / "plan.txt").write_text("a made-up plan\n", encoding="utf-8")
    first = install_sh(env, str(wheel), "--source-local", str(folder))
    assert first.returncode == 0, first.stdout + first.stderr
    whole = cfg.read_text(encoding="utf-8")
    cfg.write_text(whole + '\n[governance]\narchive = "yes"\n', encoding="utf-8")
    beta, gamma = folder.parent / "Beta", folder.parent / "Gamma"
    beta.mkdir()
    gamma.mkdir()
    args = [str(wheel), "--source-local", str(beta)] + (["--source-local", str(gamma)] if more else [])
    refused = cfg.read_text(encoding="utf-8")
    cp = install_sh(env, *args)
    out = cp.stdout + cp.stderr
    assert cp.returncode == 1, out
    assert "added source 'beta'" in cp.stdout and "configuration error: " in cp.stderr, out
    said = (
        f"error: the folder {beta} was added to {cfg}, but agentsync refuses the config (the configuration "
        "error just above)"
    )
    assert said + ("; the 1 folder after it was not added" if more else "") + "\n" in cp.stderr, out
    assert "failed (see the error above)" not in out and "folder(s)" not in out, out
    assert ("config", "failed", "78", "added-then-refused") in steps(install_log(env)), install_log(env)
    [logged] = [ln for ln in install_log(env) if "step=config " in ln and "rc=78" in ln]
    assert logged.endswith(" note=added-then-refused kept=1 added=1"), logged
    assert last_line(cp).startswith(f"NEXT: the config {cfg} loads, but agentsync refuses a value in it"), out
    assert f'path = "{beta}"' in cfg.read_text(encoding="utf-8") and str(gamma) not in cfg.read_text("utf-8")
    # B13 round 18 V26: in one stream, as a terminal shows it, the error came before the "added source" block
    # (awk's piped stdout is buffered until it exits); the call's stderr is now printed after the block
    after = cfg.read_text(encoding="utf-8")
    cfg.write_text(refused, encoding="utf-8")
    one = subprocess.run(
        [BASH32, str(INSTALL_SH), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=120,
        env=env,
        stdin=subprocess.DEVNULL,
    ).stdout
    assert one.index("added source 'beta'") < one.index("configuration error: ") < one.index(said), one
    assert cfg.read_text(encoding="utf-8") == after
    if more:
        return
    # the person fixes the value; the same command keeps the block (the report's count of that add:
    # test_setup_report.py::test_the_folders_line_counts_what_was_kept_added_and_named, U40's case)
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('\n[governance]\narchive = "yes"\n', "\n"), "utf-8"
    )
    again = install_sh(env, *args)
    assert again.returncode == 0 and "already configured" in again.stdout, again.stdout + again.stderr


@pytest.mark.parametrize("call", ["init", "add-source"])
def test_a_docs_repo_remote_governance_does_not_allow_is_the_persons_to_settle(
    real_env: dict[str, str], wheel: Path, call: str
) -> None:
    """B13 round 17 U2, U16, with the real agentsync: a remote added to the docs repo after setup, which
    [governance] does not allow (allow_remote = false by default). init and add-source exit 1 after a
    "governance: remote origin (...)" line that ended "run `git -C ... remote remove origin`", and
    config_failed had no NEXT for exit 1, so fail's "fix the error above" sent the agent to delete a remote
    the person may have set up on purpose. The NEXT now names the remote as the person's, shown and not
    touched, and nothing tells the agent to run the removal. The remote stays."""
    env = real_env
    home = Path(env["HOME"])
    folder = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / "Alpha"
    folder.mkdir(parents=True)
    (folder / "plan.txt").write_text("a made-up plan\n", encoding="utf-8")
    first = install_sh(env, str(wheel), "--source-local", str(folder))
    assert first.returncode == 0, first.stdout + first.stderr
    docs = home / "agent-context" / "docs"
    url = "https://git.contoso.example/team/kb.git"
    subprocess.run(["git", "-C", str(docs), "remote", "add", "origin", url], check=True)
    other = folder.parent / "Beta"
    other.mkdir()
    args = [str(wheel), "--source-local", str(other)] if call == "add-source" else [str(wheel)]
    cp = install_sh(env, *args)
    out = cp.stdout + cp.stderr
    assert cp.returncode == 1, out
    assert "governance: remote origin (" in cp.stderr and "the person removes it (git -C " in cp.stderr, out
    if call == "init":
        assert ("config", "failed", "1", "") in steps(install_log(env))
    else:
        # B13 round 18 V47: add-source wrote Beta's block, then exited 1 on the remote; U40's branch read
        # only exit 78, so this said "add-source <folder> failed" and logged no add, which the report dropped
        assert f'path = "{other}"' in (home / "agent-context" / "sources.toml").read_text(encoding="utf-8")
        [logged] = [ln for ln in install_log(env) if "step=config " in ln and "rc=1 " in ln]
        assert logged.endswith(" result=failed note=added-then-refused kept=1 added=1"), logged
        assert "add-source" not in cp.stderr.replace("added source", ""), cp.stderr
        assert (
            f"error: the folder {other} was added to {home / 'agent-context' / 'sources.toml'}, but the docs "
            "repo has a git remote [governance] does not allow (the governance: line above)\n"
        ) in cp.stderr, cp.stderr
    assert one_next(cp) and last_line(cp).startswith(
        "NEXT: the docs repo has a git remote the [governance] policy does not allow (the governance: line "
        "above; ~/.local/bin/agentsync status prints it again): removing it or allowing it in [governance] "
        "is the person's, not a step for the agent, so show them the error and go on to the report; once it "
        f"is settled, the next run is: {INSTALL_SH} {wheel}"
    ), out
    assert "run `git" not in out and "fix the error above" not in out, out
    assert (
        subprocess.run(
            ["git", "-C", str(docs), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == url
    )
    # with the remote gone, the same run passes: the NEXT was keyed on the remote, not on exit 1
    subprocess.run(["git", "-C", str(docs), "remote", "remove", "origin"], check=True)
    again = install_sh(env, *args)
    assert "the docs repo has a git remote" not in again.stdout + again.stderr


@pytest.mark.parametrize(
    ("knob", "created"), [("STUB_INIT_RC", False), ("STUB_ADD_SOURCE_RC", False), ("STUB_INIT_RC", True)]
)
def test_a_state_file_that_cannot_be_read_is_the_persons_to_fix(
    env: dict[str, str], folder: Path, wheel: Path, knob: str, created: bool
) -> None:
    """B13 round 16 T12: with manifest.sqlite overwritten (no database), init and add-source ended on a
    traceback and fail's "fix the error above, then re-run: install.sh", a re-run that fails the same way,
    whose real fix (moving the file aside) loses history the agent must not choose to lose. agentsync now
    exits 65 for it, with one line naming the file and the person's fix, and the NEXT names the owner, as
    for a config that does not load; also when this run wrote the config, since the state was there.
    B13 round 17 U57, U67, U87, U96: the re-run is $RERUN_ARGS, as the exit-78 NEXTs beside it name it: a
    --source-local folder in double quotes (shown_arg, round 16 T35), never %q's backslashes. The folder
    fixture's name has a space, and the whole tail is matched, so a return to $ORIG_ARGS fails."""
    if not created:
        _write_config(env, _inbox(env))
    args = [str(wheel), "--source-local", str(folder)] if knob == "STUB_ADD_SOURCE_RC" else [str(wheel)]
    cp = install_sh({**env, knob: "65"}, *args)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert ("config", "failed", "65", "") in steps(install_log(env))
    assert one_next(cp)
    assert last_line(cp).startswith(
        "NEXT: agentsync's state file cannot be read (the error above, with its fix; ~/.local/bin/agentsync "
        "status prints it again): fixing it is the person's, not a step for the agent, so show them the "
        f"error and go on to the report; once it is fixed, the next run is: {INSTALL_SH} "
    ), last_line(cp)
    rerun = f"the next run is: {INSTALL_SH} {wheel}" + (
        f' --source-local "{folder}"' if knob == "STUB_ADD_SOURCE_RC" else ""
    )
    assert " " in folder.name and f"{rerun} [setup report: " in last_line(cp), last_line(cp)
    assert "\\ " not in last_line(cp), last_line(cp)


@pytest.mark.parametrize("owner", ["another account", "this account"])
def test_a_state_file_another_account_owns_is_the_persons_to_fix(
    env: dict[str, str], wheel: Path, owner: str
) -> None:
    """B13 round 20 X9, X26: a state file this account cannot open makes init and add-source exit 1
    ("cannot be opened: Permission denied"), and config_failed had no NEXT for it, so fail's "fix the error
    above" handed the agent a sudo chown when another account owned it, though the agent runs no sudo. That
    one is the person's now, keyed on the installed agentsync finding the owner, never on exit 1 or the
    error's words. This account's own file keeps "fix the error above": its fix is a chmod the agent runs,
    and the damaged-file NEXT would put an intact manifest under a fix that moves it aside (X26)."""
    if owner == "another account":
        _tool_python_as_another_account(env)
    else:
        _tool_python(env)
    _write_config(env, _inbox(env))
    db = _state_db(env)
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    cp = install_sh({**env, "STUB_INIT_RC": "1", "AGENTSYNC_OCR": "0"}, str(wheel))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert ("config", "failed", "1", "") in steps(install_log(env))
    assert one_next(cp)
    if owner == "this account":
        assert last_line(cp).startswith("NEXT: fix the error above, then re-run: "), last_line(cp)
        return
    assert last_line(cp).startswith(
        "NEXT: agentsync's state file or its folder is owned by another account, so this one cannot open "
        "it (the error above names the file; ~/.local/bin/agentsync status prints the sudo chown that fixes "
        "it): fixing its owner (a sudo chown, which the agent does not run) is the person's, not a step for "
        "the agent, so show them the error and go on to the report; once it is fixed, the next run is: "
        f"{INSTALL_SH} {wheel}"
    ), last_line(cp)


def test_a_manifest_this_account_cannot_open_stops_step_2_where_step_1_says(
    real_env: dict[str, str], wheel: Path
) -> None:
    """B13 round 20 X9, X26, X29 (probes PM, 51, 53), with the real agentsync: a manifest at mode 0000.
    Step 1 says step 2 stops at its config step on it, a chmod the agent runs; step 2 stops there with the
    error's own fix and fail's "fix the error above"; after the chmod, step 2 passes."""
    env = real_env
    folder = _cloud(env) / "OneDrive-Contoso" / "FY26 Projects" / "Alpha"
    folder.mkdir(parents=True)
    (folder / "plan.txt").write_text("a made-up plan\n", encoding="utf-8")
    first = install_sh(env, str(wheel), "--source-local", str(folder))
    assert first.returncode == 0, first.stdout + first.stderr
    db = _state_db(env)
    db.chmod(0)
    try:
        listed = install_sh(env, "--list-folders")
        assert listed.returncode == 0, listed.stdout + listed.stderr
        assert last_line(listed).endswith(STATE_OPEN_TAIL["state-access"]), last_line(listed)
        cp = install_sh(env, str(wheel))
    finally:
        db.chmod(0o600)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert ("config", "failed", "1", "") in steps(install_log(env))
    assert "cannot be opened: Permission denied" in cp.stderr, cp.stderr
    assert last_line(cp).startswith("NEXT: fix the error above, then re-run: "), last_line(cp)
    again = install_sh(env, str(wheel))
    assert again.returncode == 0, again.stdout + again.stderr


def test_no_next_names_the_percent_q_args() -> None:
    """B13 round 17 U57, U67, U87, U96: config_failed's exit-65 NEXT ended $SELF$ORIG_ARGS, %q's form, after
    round 16 T35 moved every re-run NEXT to $RERUN_ARGS (a folder in double quotes). ORIG_ARGS stays for
    install.log's args= and --list-folders' guard; no NEXT, FAIL_NEXT or next assignment names it."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    names = re.compile(r"\b(NEXT_MSG|FAIL_NEXT|NEXT|next)=")
    assert [ln for ln in code if names.search(ln) and "ORIG_ARGS" in ln] == []
    assert any(names.search(ln) and "$SELF$RERUN_ARGS" in ln for ln in code)


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
    assert "tightened 7 paths inside the docs repo, the cache or the logs: they hold tenant data" in out
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
    r"pandoc: still running, \d+s \(a new copy's first start can take a minute, and any start can on a busy "
    r"Mac\)"
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
    # B13 round 15 S70: a bare unpack failed on CI (run 39081380800) with no output to tell why; the run's
    # lines and the pandoc start log go with every assert, so the next failure shows who started pandoc
    started_at = [i for i, ln in enumerate(out) if re.fullmatch(r"pandoc: started after \d+s", ln)]
    checked_at = [i for i, ln in enumerate(out) if ln.startswith("[ok  ] pandoc ")]
    pandoc_log = Path(f"{pandoc}.log")
    starts_seen = pandoc_log.read_text(encoding="utf-8") if pandoc_log.exists() else "(no pandoc log)"
    why = f"{cp.stdout}\n--- stderr ---\n{cp.stderr}\n--- pandoc starts ---\n{starts_seen}"
    assert len(started_at) == 1 and len(checked_at) == 1, why
    started, checked = started_at[0], checked_at[0]
    assert waits and waits[-1] < started < checked, why
    assert out[checked].endswith(f"— pandoc 9.9 at {pandoc}"), "the check ran this pandoc, and it answered"
    assert not [ln for ln in out if ln.startswith("[FAIL]")], cp.stdout
    assert "sync: converted 1, deferred 0 online-only" in out
    assert last_line(cp).startswith("NEXT: ") and one_next(cp)
    assert steps(install_log(env))[-5:-3] == [
        ("status", "done", "0", ""),
        ("first-sync", "done", "0", "converted-1-deferred-0"),
    ]
    starts = Path(f"{pandoc}.log").read_text(encoding="utf-8").splitlines()
    assert starts[0].startswith("slow ") and str(INSTALL_SH) in starts[0], (
        f"install.sh made the slow start: {starts}"
    )
    assert all(ln.startswith("quick ") for ln in starts[1:]), starts
    assert any(" -m agentsync status " in ln for ln in starts[1:]), (
        f"status's check then met a quick one: {starts}"
    )

    # B13 round 15 S17: a re-run that installs nothing can still meet a slow start (a run stopped in the wait,
    # a busy Mac): the same words, and none says it is the first start after an install
    checkout, head = _committed_checkout(tmp_path)
    stamp = (
        Path(env["HOME"]) / ".local" / "share" / "uv" / "tools" / "agentsync" / ".agentsync-install-source"
    )
    stamp.write_text(f"commit={head} source={checkout}")
    Path(f"{pandoc}.started").unlink()
    slow = install_sh(env, "--source-local", str(alpha), script=checkout / "scripts" / "install.sh")
    assert slow.returncode == 0, slow.stdout + slow.stderr
    assert ("agentsync", "skipped", "0", "current") in steps(install_log(env)), "step 2 installed nothing"
    assert any(re.fullmatch(PANDOC_PROGRESS, ln) for ln in slow.stdout.splitlines()), slow.stdout
    assert any(re.fullmatch(r"pandoc: started after \d+s", ln) for ln in slow.stdout.splitlines())
    assert "after an install" not in slow.stdout and "later ones are quick" not in slow.stdout

    # At the default interval (at the 1 s one above a line is printed whenever the clock's second turns):
    again = install_sh(real_env, *command)
    assert again.returncode == 0, again.stdout + again.stderr
    assert not [ln for ln in again.stdout.splitlines() if ln.startswith("pandoc: ")], again.stdout
    assert any(ln.startswith("[ok  ] pandoc ") for ln in again.stdout.splitlines())


@pytest.mark.parametrize(
    ("what", "said"),
    [
        ("prompt v27,,: Xcode stopped | -", ("v27", ",")),
        ("prompt v27,,: Xcode stopped | run: xcode-select --install", ("v27", ",")),
        ("prompt v27, Contoso Agent stopped | run: xcode-select --install", None),
        ("prompt v27, Contoso Agent: git pull exited 1 | -", ("v27", "Contoso Agent")),
        ("prompt v1234, Contoso: x | -", None),
        ("prompt v12_, Contoso: x | -", None),
    ],
)
def test_the_shells_stated_words_are_the_reports(what: str, said: tuple[str, str] | None) -> None:
    """B13 round 20 X83: FRICTION_AWK's stated() took the separator run after the version greedily, so
    ``prompt v27,,: x`` stated nothing where _STEP1_STATED_RE backtracks to agent ","; and it read the
    what-text up to the fix column, so ``prompt v27, Contoso Agent stopped | run: x`` stated an agent ending
    in the fix where parse_friction, which cuts the fix at the last "|", states nothing. friction_latest_said
    gives what the report's Attempt.stated gives, for a header-less attempt that one step 1 error opens."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [body] = re.findall(r"^FRICTION_AWK='(.*?)'$", script, re.MULTILINE | re.DOTALL)
    [s1] = re.findall(r"^STEP1_ERROR_RE='([^']*)'$", script, re.MULTILINE)
    shell = "\n".join(
        [
            f"FRICTION_AWK={shlex.quote(body)}",
            f"STEP1_ERROR_RE={shlex.quote(s1)}",
            "NEW_SESSION_GAP=600",
            _shell_function("friction_latest_said"),
            "friction_latest_said",
        ]
    )
    log = f"2026-10-20T10:00:00Z | step 1 | error | {what}\n"
    cp = subprocess.run([BASH32, "-c", shell], input=log, capture_output=True, text=True, check=True)
    stated = setup_report.parse_friction(log).attempts[-1].stated
    want = {"Prompt": said[0], "Agent": said[1]} if said else {}
    assert stated == want, stated
    assert cp.stdout == ("\t".join(said) if said else "\t") + "\n", cp.stdout


def _shell_function(name: str) -> str:
    """install.sh's function ``name`` as the script has it: from ``name() {`` to the brace that closes it."""
    found = re.search(rf"(?ms)^{name}\(\) \{{(?: #[^\n]*)?$.*?^\}}$", INSTALL_SH.read_text(encoding="utf-8"))
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
        _shell_function("watchdog"),
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


def test_the_wait_for_pandoc_forks_a_few_sleeps_not_five_a_second(tmp_path: Path) -> None:
    """B13 round 14 Q40: start_pandoc_once polled pandoc with an external sleep 0.2 (bash 3.2 has no builtin
    sleep), five forks a second for up to 300 s under load (the pattern with_progress dropped for load root
    cause 2026-10-17 X11). It waits on pandoc, and its ticker and limit each wait on their own sleeps: a
    6 s start at a 5 s interval forks about 10 sleeps, not 30, and still prints its lines."""
    shim = tmp_path / "bin"
    shim.mkdir()
    count = tmp_path / "sleeps"
    _write_exe(shim / "sleep", f'#!/bin/bash\necho "$1" >> {shlex.quote(str(count))}\nexec /bin/sleep "$@"\n')
    slow = _write_exe(tmp_path / "pandoc", "#!/bin/bash\n/bin/sleep 6\n")
    lines = [
        "set -euo pipefail",
        "say() { printf '%s\\n' \"$*\"; }",
        f"status_pandoc() {{ printf '%s' {shlex.quote(str(slow))}; }}",
        "PROGRESS_EVERY=5",
        "PANDOC_START_SECONDS=300",
        _shell_function("watchdog"),
        _shell_function("start_pandoc_once"),
        "start_pandoc_once",
        "echo the run goes on",
    ]
    env = {**os.environ, "PATH": f"{shim}:/usr/bin:/bin"}
    cp = subprocess.run(
        [BASH32, "-c", "\n".join(lines)], capture_output=True, text=True, check=False, timeout=120, env=env
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    out = cp.stdout.splitlines()
    assert re.fullmatch(PANDOC_PROGRESS, out[0]) and re.fullmatch(r"pandoc: started after [67]s", out[-2])
    assert out[-1] == "the run goes on", cp.stdout
    sleeps = count.read_text().split()
    # B13 round 16 T80: the limit sleeps in 1 s slices while pandoc runs, about 6 for a 6 s start
    assert "300" not in sleeps and sleeps.count("1") <= 8 and len(sleeps) <= 22, sleeps


def _trap_line(function: str) -> str:
    """The TERM trap of install.sh's ``function`` (for start_pandoc_once, its ticker's), as written."""
    [line] = [ln.strip() for ln in _shell_function(function).splitlines() if ln.strip().startswith("trap ")]
    return line


def test_a_ticker_trap_whose_kill_fails_still_says_whether_it_printed() -> None:
    """B13 round 16 T41, T79, the Q40 CI flake: the pandoc ticker's trap was `kill "$s" 2>/dev/null; exit
    $((said ? 3 : 0))` under install.sh's set -e. A TERM that landed after the ticker's sleep was reaped
    made the kill fail, errexit ended the ticker with 1 before `exit 3`, and "pandoc: started after Ns" was
    not printed (2 of 70 runs under load; ci 39132985876 attempt 1). Each trap of that shape is run here as
    the script has it, with a kill that fails (no sleep to kill), under set -euo pipefail."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    traps = re.findall(r"(?m)^\s*trap '([^']*)' TERM$", script)
    killers = [t for t in traps if t.startswith('kill "$s" ')]
    assert len(killers) == 4 and all(t.startswith('kill "$s" 2>/dev/null || :; exit ') for t in killers), (
        traps
    )
    for function, said, want in (
        ("start_pandoc_once", 1, 3),
        ("start_pandoc_once", 0, 0),
        ("watchdog", 1, 0),
    ):
        harness = "\n".join(
            [
                "set -euo pipefail",
                "ready=$(mktemp)",
                'rm -f "$ready"',
                f'( s="" said={said}',
                f"  {_trap_line(function)}",
                '  : > "$ready"',
                "  while :; do :; done ) &",
                "p=$!",
                'while [ ! -e "$ready" ]; do /bin/sleep 0.05; done',
                'rm -f "$ready"',
                'kill -TERM "$p"',
                'rc=0; wait "$p" || rc=$?',
                'echo "rc=$rc"',
            ]
        )
        cp = subprocess.run([BASH32, "-c", harness], capture_output=True, text=True, check=False, timeout=60)
        assert cp.stdout.split() == [f"rc={want}"], (function, said, cp.stdout + cp.stderr)


# A kill that never reaches the watchdog or the ticker: what bash 3.2 does to a TERM that lands in the child
# before its trap is set (B13 round 16 T80: 1 in 16000 calls under load). The callers' own kills of WD and
# WAIT_TICKER are dropped; every other kill is the builtin's.
_LOST_TERM = (
    "kill() {\n"
    '\tcase " $* " in *" ${wd:-none} "* | *" ${WAIT_TICKER:-none} "*) return 0 ;; esac\n'
    '\tbuiltin kill "$@"\n'
    "}"
)


def test_a_watchdog_whose_term_is_lost_ends_with_its_command() -> None:
    """B13 round 16 T80, the CI timeout of `install.sh --list-folders` (ci 39105995103): with_timeout's
    watchdog slept the whole limit in one sleep, and bash 3.2 holds a TERM that lands just before `wait`
    until that sleep ends, or drops one that lands before the child's trap is set; the caller's `wait` on
    the watchdog then waited out the limit with nothing printed (21 of 16000 calls at a 5 s limit under
    load). The watchdog now sleeps in 1 s slices and stops once its command has ended, so even a lost TERM
    costs at most a slice. Run here with the caller's kill of the watchdog dropped."""
    harness = "\n".join(
        [
            "set -euo pipefail",
            _LOST_TERM,
            _shell_function("watchdog"),
            _shell_function("with_timeout"),
            "t0=$SECONDS",
            "with_timeout 8 true",
            'echo "took=$((SECONDS - t0))"',
            "rc=0; t0=$SECONDS; with_timeout 2 /bin/sleep 30 || rc=$?",
            'echo "killed=$rc took=$((SECONDS - t0))"',
        ]
    )
    cp = subprocess.run([BASH32, "-c", harness], capture_output=True, text=True, check=False, timeout=60)
    took, killed = cp.stdout.splitlines()
    assert int(took.removeprefix("took=")) <= 3, cp.stdout + cp.stderr
    rc, total = killed.removeprefix("killed=").split(" took=")
    # whole 1 s slices, counted: never before the limit, as a deadline on the whole-second clock could be
    assert rc == "143" and 2 <= int(total) <= 4, "the limit still stops a command that runs on, not early"


def test_a_pandoc_ticker_whose_term_is_lost_ends_with_pandoc(tmp_path: Path) -> None:
    """B13 round 16 T80: start_pandoc_once's ticker looped `while :` with no other way out than its TERM, so
    one bash 3.2 dropped left it printing and the run waiting on it for ever (10 of 18000 such loops). It
    now ends when pandoc does, with the status that says whether it printed a line, and the limit ends with
    pandoc too: "pandoc: started after Ns" still follows a slow start. Run here with the caller's kills of
    both dropped."""
    slow = _write_exe(tmp_path / "pandoc", "#!/bin/bash\n/bin/sleep 2.5\n")
    harness = "\n".join(
        [
            "set -euo pipefail",
            "say() { printf '%s\\n' \"$*\"; }",
            f"status_pandoc() {{ printf '%s' {shlex.quote(str(slow))}; }}",
            "PROGRESS_EVERY=1",
            "PANDOC_START_SECONDS=300",
            _LOST_TERM,
            _shell_function("watchdog"),
            _shell_function("start_pandoc_once"),
            "t0=$SECONDS",
            "start_pandoc_once",
            'echo "took=$((SECONDS - t0))"',
        ]
    )
    cp = subprocess.run([BASH32, "-c", harness], capture_output=True, text=True, check=False, timeout=60)
    out = cp.stdout.splitlines()
    assert cp.returncode == 0 and out and re.fullmatch(r"pandoc: started after [234]s", out[-2]), cp.stdout
    assert int(out[-1].removeprefix("took=")) <= 5, cp.stdout


_STATUS_PANDOC_SHIM = """import importlib.metadata
import sys
from pathlib import Path

from agentsync.convert import pandoc

mode = sys.argv.pop(1)
[record] = [
    e.hash.value
    for e in importlib.metadata.distribution("pypandoc_binary").files or ()
    if e.as_posix() == "pypandoc/files/pandoc" and e.hash is not None
]
sys.platform = "darwin"  # an Apple silicon Mac whose wheel maps to 3.9, whatever the host
pandoc._WHEEL_PANDOC_VERSIONS = {record: "3.9"}
if mode == "older":  # a tool installed before resolved_pandoc
    del pandoc.resolved_pandoc
elif mode == "no-wheel-binary":
    pandoc._wheel_pandoc = lambda: Path("/nonexistent/pypandoc/files/pandoc")
assert sys.argv[1:3] == ["-I", "-c"], sys.argv
code, sys.argv = sys.argv[3], ["-c", *sys.argv[4:]]
exec(compile(code, "<string>", "exec"), {"__name__": "__main__"})
"""


def test_the_pandoc_the_installer_starts_is_the_one_the_converters_run(
    sample_config: Config, tmp_path: Path
) -> None:
    """B13 round 14 Q60: with the operator's native arm64 copy installed, the converters run it, but
    status_pandoc named the wheel's Intel pandoc, so every install still paid Rosetta's first start of a
    binary nothing then ran. It names the copy now. A tool installed before the resolver has none, and its
    converters run the wheel's pandoc, which is then the one named; a missing wheel binary names none, for
    status to report. Run by bash 3.2 as the script has it, through the tool interpreter's -I -c."""
    copy = Path.home() / ".local/share/agentsync/pandoc/3.9-arm64/bin/pandoc"
    copy.parent.mkdir(parents=True)
    _write_exe(copy, "#!/bin/sh\necho 'pandoc 3.9'\n")
    shim = tmp_path / "shim.py"
    shim.write_text(_STATUS_PANDOC_SHIM, encoding="utf-8")
    import pypandoc  # noqa: PLC0415

    wheel = Path(pypandoc.__file__).parent / "files" / "pandoc"
    said = {}
    for mode in ("current", "older", "no-wheel-binary"):
        tool = _write_exe(
            tmp_path / f"python-{mode}",
            f'#!/bin/bash\nexec {shlex.quote(sys.executable)} -I {shlex.quote(str(shim))} {mode} "$@"\n',
        )
        lines = [
            "set -euo pipefail",
            f"TOOL_PY={shlex.quote(str(tool))}",
            f"CONFIG={shlex.quote(str(sample_config.config_path))}",
            _shell_function("watchdog"),
            _shell_function("with_timeout"),
            _shell_function("status_pandoc"),
            'printf "<%s>" "$(status_pandoc)"',
        ]
        cp = subprocess.run(
            [BASH32, "-c", "\n".join(lines)], capture_output=True, text=True, check=False, timeout=120
        )
        assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
        said[mode] = cp.stdout
    assert said == {"current": f"<{copy}>", "older": f"<{wheel}>", "no-wheel-binary": "<>"}


def test_a_quiet_pandoc_start_and_the_status_wait_keep_one_progress_clock(tmp_path: Path) -> None:
    """Review of the v9 rehearsal fixes (2026-10-07): step 5 could print nothing for about twice the
    progress interval. A pandoc start shorter than the interval prints nothing, and the status wait after
    it began its own count at zero, so a 13 s start and a status that waits were 27 s of silence at the
    default 15 s. The status wait now counts its first interval from where the pandoc start began: a line
    is printed once the two have lasted one interval together, though neither did alone. The two functions
    are run here as the script has them, in step 5's order, and the wait after those two counts from its
    own start again.

    B13 round 20 X36: a start silent for most of an interval left the status wait's first line to print
    "status: still running, 1s" after 14 s of silence (status's own time). A start silent for half an
    interval or more ends with its own "pandoc: started after Ns" line, so the status line comes a full
    interval after it and no status count is smaller than the silence before it."""
    quiet = _write_exe(tmp_path / "pandoc", "#!/bin/bash\nsleep 2.5\n")
    lines = [
        "set -euo pipefail",
        "say() { printf '%s\\n' \"$*\"; }",
        f"status_pandoc() {{ printf '%s' {shlex.quote(str(quiet))}; }}",
        "PROGRESS_EVERY=5",
        "PANDOC_START_SECONDS=300",
        _shell_function("with_progress"),
        _shell_function("watchdog"),
        _shell_function("start_pandoc_once"),
        "start_pandoc_once",
        "with_progress status sleep 7",
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
    assert re.fullmatch(r"pandoc: started after [23]s", said[0]), cp.stdout
    beats = [int(m) for ln in said for m in re.findall(r"^status: still running, (\d+)s$", ln)]
    assert beats and min(beats) >= 4, cp.stdout  # one interval (5 s) after the pandoc line, a second's slack


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
        f"""(an earlier "Don't Allow"): {TCC_CLICK}, then re-run: {rerun(wheel)} """
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
        "NEXT: this run was stopped before it finished (stopped by SIGTERM, perhaps a tool timeout); it is "
        f"safe to re-run: {rerun(wheel)} [setup report: {report_path(env)}]"
    )
    log = install_log(env)
    assert ("first-sync", "failed", "143", "") in steps(log)
    assert re.search(r" end rc=143 seconds=\d+$", log[-1]) and " step=report " in log[-2]
    assert stdout.rstrip("\n").splitlines()[-1] in install_out(env).read_text(), "install.out has the end too"
    # B13 round 20 X7: 143's words held their own brackets, so the error and NEXT lines nested two deep
    for line in (stdout + stderr).splitlines():
        depth = deepest = 0
        for c in line:
            depth += (c == "(") - (c == ")")
            deepest = max(deepest, depth)
        assert deepest <= 1, line


@pytest.mark.parametrize("group", [False, True])
def test_a_signal_inside_add_source_keeps_its_held_error(
    env: dict[str, str], folder: Path, wheel: Path, tmp_path: Path, group: bool
) -> None:
    """B13 round 19 W39: add-source's stderr is held in a file until its pipeline ends (round 18 V26), and a
    signal there (a coding tool's time-out) ran on_signal's exit before the loop printed it: the error was in
    none of stderr, install.out or the report, and the file, with the error in it, stayed in TMPDIR beside
    the mark. on_exit prints what is held, after the stopped-by-a-signal error, and removes both. The SIGTERM
    goes to the installer's pid alone (bash runs the trap once the call ends) or to its whole group."""
    stub = _write_exe(
        tmp_path / "agentsync-slow-add",
        "#!/bin/bash\n"
        'if [ "$1" = add-source ]; then\n'
        '  echo "agentsync $*" >> "$STUB_LOG"\n'
        '  echo "configuration error: a made-up Contoso value (stub)" >&2\n'
        '  : > "$STUB_LOG.adding"\n'
        "  sleep 2\n"
        "  exit 78\n"
        "fi\n"
        f'exec "{env["STUB_AGENTSYNC"]}" "$@"\n',
    )
    tmp = tmp_path / "tmpdir"
    tmp.mkdir()
    e = {**env, "STUB_AGENTSYNC": str(stub), "TMPDIR": str(tmp)}
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
        while not Path(env["STUB_LOG"] + ".adding").exists():
            assert proc.poll() is None and time.monotonic() < deadline, "add-source never started"
            time.sleep(0.05)
        if group:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            os.kill(proc.pid, signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    assert proc.returncode == 143, stdout + stderr
    error = "configuration error: a made-up Contoso value (stub)\n"
    assert stderr.count(error) == 1, stderr
    assert stderr.index("error: install.sh was stopped by a signal") < stderr.index(error), stderr
    assert error in install_out(env).read_text(encoding="utf-8"), "the report reads install.out for it"
    assert [p.name for p in tmp.iterdir()] == [], "the held stderr and the mark are removed"


# ---- the report's issue link before NEXT (K3) --------------------------------------------------------------


def test_a_signal_exit_is_named_by_its_signal() -> None:
    """``rc_meaning`` names any signal's exit (128 + N) as ``stopped by SIG<NAME> (signal N)``, the phrase of
    ``ocr._ended``: a first sync jetsam killed read "exit 137". 129, 130 and 143 keep their words, and a code
    that is no signal's stays ``exit N`` (B13 round 2 R81)."""
    fn = re.search(r"^rc_meaning\(\) \{\n.*?^\}\n", INSTALL_SH.read_text(encoding="utf-8"), re.M | re.S)
    assert fn is not None
    codes = ["137", "134", "141", "143", "130", "200", "1", "64", "65"]
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
        "stopped by SIGTERM, perhaps a tool timeout",  # B13 round 20 X7: no brackets in the brackets
        "stopped by SIGINT",
        "exit 200",
        "failed",
        "the launcher refused the job: its program is not the one it starts, or an option is wrong",
        "the state file (manifest.sqlite) cannot be read: the person's to fix",  # B13 round 17 U55, U65
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
    # B13 round 12 N78: per-list timeout > bound > cap + headroom. With a 2 s timeout the bound sat at the
    # uncapped 6 s and failed under load (6.4 s, 7.1 s), and a first list not cut to the cap passed. Now a
    # working cap takes about 3.3 s against a bound of 12; no total cap takes 60 s, no cut about 20 s.
    e = {**env, "AGENTSYNC_LIST_TIMEOUT": "20", "AGENTSYNC_LIST_TOTAL_SECONDS": "3"}
    t0 = time.monotonic()
    cp = install_sh(e, "--list-folders", timeout=90)
    elapsed = time.monotonic() - t0
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert elapsed < 12, f"three providers waited {elapsed:.1f}s past a 3 s cap"
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
        ("rewrite", True, "copy-again"),
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
    both. The stderr, the friction line's fix and the stopped report's NEXT say the same. B13 round 18 V70:
    "rewrite" is GitHub's origin under a URL rewrite (insteadOf), which ``remote get-url`` applies: it read as
    a checkout that follows another remote; the configured URL is read now."""
    rewrite = origin == "rewrite"
    if rewrite:
        origin = "https://github.com/renchris/agent-context-sync.git"
    script = _checkout_following(tmp_path, origin, upstream)
    if rewrite:
        subprocess.run(
            [
                "git",
                "-C",
                str(script.parents[1]),
                "config",
                "url.https://example.invalid/mirror/.insteadOf",
                "https://github.com/renchris/",
            ],
            check=True,
        )

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


def test_an_install_run_after_a_stopped_copy_invites_no_bring_back(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 20 X19: only ``--report-only`` routed a stopped copy's report, so a plain install.sh after
    the stop (its Doctor tells the person to run it again for a helper) judged the stopped attempt by its own
    exit 0: "fully one command", bring-back.md rewritten, the stopped copy's report removed, and an issue link
    titled for the old copy. Any run that writes a report after such an attempt, with no --log-start since,
    routes it as the stopped copy's: no "bring back:" or link line, bring-back.md and setup-report.md keep
    their bytes, and the run's own NEXT stays, with one clause on why nothing is to be sent."""
    back = report_path(env).parent / "bring-back.md"
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", f"prompt v{COMPAT}, Contoso Agent (model-1)").returncode == 0
    assert install_sh(env, str(wheel), "--source-local", str(folder)).returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    kept, kept_report = back.read_bytes(), report_path(env).read_bytes()
    assert install_sh(env, "--log-start", f"prompt v{COMPAT - 2}, Old Agent (model-0)").returncode == 2
    assert install_sh(env, "--report-only").returncode == 0 and side.exists()
    cp = install_sh(env, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not [ln for ln in cp.stdout.splitlines() if ln.startswith(("bring back:", "issue link"))], (
        cp.stdout
    )
    assert "issues/new" not in cp.stdout and f"prompt_version=v{COMPAT - 2}" not in cp.stdout
    assert back.read_bytes() == kept and report_path(env).read_bytes() == kept_report
    assert side.read_text().startswith(
        "> This report is of an attempt started by a copy of the setup prompt that does not match this "
        "installer"
    )
    text = side.read_text()  # the stub agentsync wrote it: no legend to check, as _sends_nothing does
    assert "issues/new" not in text and text.endswith(
        "\n\nNot for sending: see the note at the top of this report.\n"
    )
    clause = (
        "; the last setup attempt was a copy of the prompt this installer stopped at step 1; its report is "
        "not to be sent, so paste the current prompt into a new session to report a setup"
    )
    assert last_line(cp).startswith("NEXT: ") and f"{clause} [setup report: {side}]" in last_line(cp)
    # a new attempt of the current prompt is reported as before
    assert install_sh(env, "--log-start", f"prompt v{COMPAT}, Contoso Agent (model-1)").returncode == 0
    again = install_sh(env, str(wheel), "--source-local", str(folder))
    assert again.returncode == 0 and clause not in last_line(again) and not side.exists(), again.stdout


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
    # B13 round 16 T23: the summary is the renderer's words, so its "(s)" goes by its count
    assert (
        head == f"<details><summary>the last 3 lines of ~/agent-context/setup/install.out ({label})</summary>"
    )
    assert len(fenced) == 3
    _sends_nothing(side)


def test_a_stopped_copy_rendered_by_an_older_agentsync_drops_its_quiet_friction_count(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 13 O20: round 12 N20 left out the Summary's "- friction (attempt N): ..." line that counts
    only the problem kinds and the closing line, but a stopped copy is rendered by the older installed
    agentsync, which still wrote "1 error, 1 finished" beside "failed at step 1". The shim drops such a line,
    by the renderer's own rule; one that counts another kind, or legacy lines, stays, and so does the friction
    log's count."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    dropped = [
        "- friction (attempt 2): 2 event line(s): 1 error, 1 finished",
        "- friction (attempt 3): 3 event line(s): 1 deviation, 1 error, 1 finished",
    ]
    kept = [
        "- friction (attempt 4): 2 event line(s): 1 question, 1 finished",
        "- friction (attempt 5): 2 event line(s): 1 error, 1 finished; 1 legacy v4 line(s)",
    ]
    log_line = "- attempt 3 (2026-10-16T23:25:12Z): failed at step 1; 2 event line(s): 1 error, 1 finished"
    old = tmp_path / "old-report.md"
    old.write_text(
        _old_renderer_report(
            ["- outcome: failed at step 1", *dropped, *kept], ["# run=20261012T090000Z-41 install.sh", "done"]
        ).replace("## Redaction", f"## Agent friction log\n\n{log_line}\n\n## Redaction")
    )
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    lines = side.read_text().splitlines()
    # B13 round 16 T23: what stays takes the noun of its count, after quiet() read the old "(s)" form
    assert [ln for ln in lines if ln.startswith("- friction (attempt ")] == [
        "- friction (attempt 4): 2 event lines: 1 question, 1 finished",
        "- friction (attempt 5): 2 event lines: 1 error, 1 finished; 1 legacy v4 line",
    ]
    assert (
        log_line.replace("event line(s)", "event lines") in lines and "- outcome: failed at step 1" in lines
    )
    _sends_nothing(side)


def test_the_shell_stopped_copy_quiet_kinds_are_the_reports() -> None:
    """B13 round 13 O20: the kinds whose count line the shim drops are the renderer's."""
    [k] = re.findall(r'(?m)^STOPPED_COPY_QUIET_KINDS="(.*)" #', INSTALL_SH.read_text())
    assert k.split() == [*setup_report.PROBLEM_KINDS, "finished"]


def _one_second_runs(env: dict[str, str]) -> str:
    """install.log and install.out of step 1's list (-2525, 3 lines) and the install (-2759, 12 lines, its end
    rc 1, its status step logged) started in one second; the stamp they share."""
    st = "20261016T232512Z"
    at = "2026-10-16T23:25:12Z"
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    listed = [
        f"{at} run={st}-2525 {t}"
        for t in (
            "start install.sh args=--list-folders",
            "step=list-folders seconds=0 rc=0 result=done",
            "end rc=0 seconds=0",
        )
    ]
    steps_ = [
        "uv",
        "agentsync",
        "launcher",
        "helpers",
        "config",
        "status",
        "first-sync",
        "agent",
        "wait",
        "report",
    ]
    install = [f"{at} run={st}-2759 start install.sh args=--source-local x"]
    install += [f"{at} run={st}-2759 step={n} seconds=0 rc=0 result=done" for n in steps_]
    install += [f"{at} run={st}-2759 end rc=1 seconds=195"]
    (setup / "install.log").write_text("\n".join(listed + install) + "\n")
    (setup / "install.out").write_text(
        f"# run={st}-2525 {at} install.sh --list-folders\nlisted\n# run={st}-2759 {at} install.sh x\ndone\n"
    )
    return st


def _old_report_of_one_second_runs(st: str) -> str:
    """A stopped copy's report as an agentsync older than 3b47e3e7 rendered it: one bare id for both runs."""
    at = "2026-10-16T23:25:12Z"
    raw = [
        f"{at} run={st} start install.sh args=--list-folders",
        f"{at} run={st} step=list-folders",
        f"{at} run={st} end rc=0 seconds=0",
        *(f"{at} run={st} step={n}" for n in range(11)),
        f"{at} run={st} end rc=1 seconds=195",
    ]
    return "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            f"- install.sh · step 2 · run {st} exited 1; the same command was run again and ended 0",
            f"- doctor: 9 check(s) · install.sh's doctor before its first sync (run {st}): 1 FAIL not in "
            "this one: x",
            "",
            "## Installer",
            "",
            "2 install.sh run(s) in ~/agent-context/setup/install.log, 195s in total (runs with an end "
            "line); the last 2 (run ids without their process id):",
            "",
            f"run {st}: exit 0 after 0s",
            "",
            f"run {st}: exit 1 after 195s",
            "",
            "Every run, oldest first (the last 2 of 2; run ids without their process id):",
            "",
            "| run | started (UTC) | kind | step lines | last step logged | end |",
            "|---|---|---|---|---|---|",
            f"| {st} | {at} | list-folders | 1 | list-folders (done) | exit 0 after 0s |",
            f"| {st} | {at} | install | 10 | report (done) | exit 1 after 195s |",
            "",
            "<details><summary>raw install.log lines of these runs</summary>",
            "",
            "~~~text",
            *raw,
            "~~~",
            "",
            "</details>",
            "",
            "<details><summary>the last 4 line(s) of ~/agent-context/setup/install.out (what the agent saw)"
            "</summary>",
            "",
            "~~~text",
            f"# run={st} {at} install.sh --list-folders",
            "listed",
            f"# run={st} {at} install.sh x",
            "done",
            "~~~",
            "",
            "</details>",
            "",
            "## Redaction",
            "",
            "Read the report before sending it: anything else confidential is yours to remove.",
            "",
            setup_report.ISSUE_URL,
            "",
        ]
    )


def test_a_stopped_copy_rendered_by_an_older_agentsync_numbers_runs_started_in_one_second(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 13 O21: round 12 N21 numbered runs that share a second (<stamp>.2) in both readers, but a
    stopped copy is rendered by the older installed agentsync, whose report gave both runs one id: two
    "run <stamp>:" headings, two Every-run rows and 15 raw lines alike, and a retry or doctor sentence that
    pointed at the first run. The shim labels each by install.log, and leaves a report already numbered as it
    is."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    assert install_sh(env, "--log-start", "x").returncode == 2
    st = _one_second_runs(env)
    old = tmp_path / "old-report.md"
    old.write_text(_old_report_of_one_second_runs(st))
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    lines = side.read_text().splitlines()
    assert [ln for ln in lines if ln.startswith("run ")] == [
        f"run {st}: exit 0 after 0s",
        f"run {st}.2: exit 1 after 195s",
    ]
    assert [ln.split(" | ")[0] for ln in lines if ln.startswith(f"| {st}")] == [f"| {st}", f"| {st}.2"]
    raw = [m[1] for ln in lines if (m := re.search(r" run=(\S+) ", ln + " ")) and not ln.startswith("# ")]
    assert raw == [st] * 3 + [f"{st}.2"] * 12, raw
    assert [ln.split()[1] for ln in lines if ln.startswith("# run=")] == [f"run={st}", f"run={st}.2"]
    clause = ", numbered .2 on for runs started in one second):"
    assert sum(ln.endswith("run ids without their process id" + clause) for ln in lines) == 2
    assert f"- install.sh · step 2 · run {st}.2 exited 1; the same command was run again and ended 0" in lines
    assert any(f"doctor before its first sync (run {st}.2): " in ln for ln in lines)
    # a report already numbered (a renderer from 3b47e3e7 on) is left as it is
    numbered = _old_report_of_one_second_runs(st).replace(f"run {st}: exit 1", f"run {st}.2: exit 1")
    old.write_text(numbered)
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    lines = side.read_text().splitlines()
    assert [ln for ln in lines if ln.startswith("run ")] == [
        f"run {st}: exit 0 after 0s",
        f"run {st}.2: exit 1 after 195s",
    ]
    assert not any("numbered .2 on" in ln for ln in lines)
    assert f"- install.sh · step 2 · run {st} exited 1; the same command was run again and ended 0" in lines


def _stopped_run_labels(tmp_path: Path, log: list[str], report: str) -> str:
    """install.sh's ``stopped_run_labels`` run alone under /bin/bash on ``report``, with ``log`` as
    install.log (and no install.out)."""
    (tmp_path / "install.log").write_text("\n".join(log) + "\n")
    cp = subprocess.run(
        [BASH32, "-c", f"set -euo pipefail\n{_shell_function('stopped_run_labels')}\nstopped_run_labels"],
        input=report,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "SETUP_LOG": str(tmp_path / "install.log"),
            "INSTALL_OUT": str(tmp_path / "none"),
        },
    )
    return cp.stdout


@pytest.mark.parametrize(
    "shape",
    [
        "one second",
        "a second apart",
        "an unrelated pair",
        "already numbered",
        "already named",
        "both clauses",
    ],
)
def test_a_stopped_copy_by_an_older_agentsync_names_the_install_run_of_its_sync_line(
    tmp_path: Path, shape: str
) -> None:
    """B13 round 14 Q26: round 13 O22 added ", run <label>" to the Summary's "the install run of <start>"
    when that run's stamp is shared, in the renderer only, so a stopped copy rendered by the older installed
    agentsync named its first sync's run by a start time two runs share (or, a second apart, a time no row's
    id shows). The shim labels it on the path that numbers the rest: the last run of that start time that
    logged a first sync (install.log's clause) or that is no list (install.out's).
    B13 round 15 S19: a renderer from 3b47e3e7 up to 878ebc7e (e2a8e8df, the v21 field build) numbers the
    runs but names none, and the shim left such a report as it was, so the fix never reached the renderer its
    finding came from: its sync line gains the label now, and every other line is left as it is (a relabel
    would number the list run twice)."""
    st, at, later = "20261017T170253Z", "2026-10-17T17:02:53Z", "2026-10-17T17:02:54Z"
    start = later if shape == "a second apart" else at
    named = "20261017T160000Z" if shape == "an unrelated pair" else st
    if shape == "an unrelated pair":
        start = "2026-10-17T16:00:00Z"
    log = [
        f"{at} run={st}-581 start install.sh args=--list-folders",
        f"{at} run={st}-581 end rc=0 seconds=0",
        f"{start} run={named}-582 start install.sh args=--source-local x",
        f"{start} run={named}-582 step=first-sync seconds=2 rc=0 result=done",
        f"{start} run={named}-582 end rc=0 seconds=9",
    ]
    if shape == "an unrelated pair":
        log += [
            f"{at} run={st}-583 start install.sh args=--list-folders",
            f"{at} run={st}-583 end rc=0 seconds=0",
        ]
    sync = f"- sync: done in 2s (install.log: the install run of {start}, before this attempt; this attempt "
    sync += "ran none)"
    if shape == "both clauses":
        sync += f" (install.out: the install run of {start}, before this attempt)"
    rows = [f"| {st} | {at} | list-folders |", f"| {st} | {start} | install |"]
    heads: list[str] = []
    if shape.startswith("already"):  # an e2a8e8df render's shape: numbered headings and rows
        rows[1] = rows[1].replace(f"| {st} |", f"| {st}.2 |")
        heads = [f"run {st}: exit 0 after 0s", "", f"run {st}.2: exit 0 after 9s", ""]
        if shape == "already named":  # a renderer from 878ebc7e on
            sync = sync.replace(f"run of {start},", f"run of {start}, run {st}.2,")
    report = "\n".join(
        ["# agentsync setup report", "", "## Summary", "", sync, "", "## Installer", "", *heads, *rows, ""]
    )
    out = _stopped_run_labels(tmp_path, log, report)
    [line] = [ln for ln in out.splitlines() if ln.startswith("- sync: ")]
    if shape == "already numbered":
        labelled = sync.replace(f"run of {start},", f"run of {start}, run {st}.2,")
        assert out == report.replace(sync, labelled), "the sync line is named, every other line as it was"
    elif shape in ("an unrelated pair", "already named"):
        assert line == sync, line
        if shape == "already named":
            assert out == report, "a report whose sync line is named already is left as it is"
    else:
        assert line == sync.replace(f"run of {start},", f"run of {start}, run {st}.2,"), line
        assert line.count(f", run {st}.2,") == (2 if shape == "both clauses" else 1), line


_OLD_STEPS = [
    ("uv", {"result": "skipped", "note": "present", "seconds": "0"}),
    ("launcher", {"result": "skipped", "note": "not-requested", "seconds": "0"}),
    ("config", {"result": "done", "note": "exists", "seconds": "1"}),
    ("agent", {"result": "skipped", "rc": "3", "note": "status-failed", "seconds": "0"}),
    ("wait", {"result": "skipped", "note": "not-requested", "seconds": "0"}),
    ("caf\u00e9", {"result": "done", "note": "d\u00e9j\u00e0", "seconds": "12"}),
    ("report", {"result": "done", "note": "agentsync", "seconds": "4"}),
]
"""Installer steps whose notes round 13 O3 put into words (one with an rc before its note), and a made-up
step with non-ASCII cells, which the table pads by characters."""


def test_a_stopped_copy_by_an_older_agentsync_words_its_table_and_its_held_listing(
    env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 14 Q27: two more round 13 renderer-only fixes did not reach a stopped copy, which the older
    installed agentsync renders: the Installer table's raw install.log tokens ("skipped (not-requested)",
    "done (agentsync)", round 13 O3) and doctor's "deletions held (held at its last walk ... still holds it)"
    (O36). The shim maps each known token and pads the table again as _step_table does, and replaces the old
    bracket; a line already in the new words is left alone."""
    new = setup_report._step_table(_OLD_STEPS)
    monkeypatch.setattr(setup_report, "_STEP_NOTE_WORDS", {"helpers": setup_report._HELPERS_NOTE_WORDS})
    old = setup_report._step_table(_OLD_STEPS)
    monkeypatch.undo()
    assert old != new and "| skipped (not-requested) " in "\n".join(old)
    held = "[warn] heartbeat.fy26-projects — enumeration incomplete for 3 consecutive passes; deletions held "
    old_held = (
        held
        + "(held at its last walk: the Summary's Loop line says whether a WAITING ON YOU line still holds it)"
    )
    new_held = held + f"({doctor.LISTING_YOURS_REPORT_NOTE})"
    report = "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            "- install.sh · step 2 · run 20261017T170253Z exited 0",
            "",
            "## Installer",
            "",
            "run 20261017T170253Z: exit 0 after 17s",
            "",
            *old,
            "",
            "## Doctor",
            "",
            "~~~text",
            old_held,
            new_held.replace("fy26-projects", "q3-launch"),
            "~~~",
            "",
            "## Redaction",
            "",
            "Read the report before sending it: anything else confidential is yours to remove.",
            "",
        ]
    )
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    assert install_sh(env, "--log-start", "x").returncode == 2
    stub = tmp_path / "old-report.md"
    stub.write_text(report, encoding="utf-8")
    assert install_sh({**env, "STUB_REPORT_FILE": str(stub)}, "--report-only").returncode == 0
    lines = (report_path(env).parent / "setup" / "stopped-copy-report.md").read_text().splitlines()
    assert [ln for ln in lines if ln.startswith("|")] == new, "\n".join(
        ln for ln in lines if ln.startswith("|")
    )
    assert new_held in lines and old_held not in lines
    assert new_held.replace("fy26-projects", "q3-launch") in lines


_NOT_BUILT = [
    "not-built-" + "+".join(names)
    for n in (1, 2, 3)
    for names in itertools.combinations(("ocr", "media", "speech"), n)
]
"""Every helpers-step note of a run with a helper not built (B13 round 18 V6)."""


def test_a_helper_not_built_is_named_in_the_installer_row_of_either_renderer(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 18 V6: a speech build that ran out of time logged a bare ``done``, and the Installer row read
    ``done``. The step stays done (an optional helper is never a failed step), noted not-built-<names>: the
    report names the helpers and the install.out lines that say why, and a stopped copy an older renderer
    wrote, whose table shows the raw note, takes the same words (older_report_rewrite's not_built)."""
    rows = [("helpers", {"result": "done", "note": note, "seconds": "1220"}) for note in _NOT_BUILT]
    new = setup_report._step_table(rows)
    assert new[2].split(" | ")[2].rstrip(" |") == (
        'done (not ready: the OCR helper; its "OCR helper:" line in install.out says why)'
    )
    assert "| done (not ready: the OCR helper, the media helper and the speech helper; their " in new[-1]
    assert '"OCR helper:", "media helper:" and "speech:" lines in install.out say why) |' in new[-1]
    assert setup_report._helpers_note("not-built-wifi") == "not-built-wifi", "an unknown name stays a token"
    old = [
        "| step    | seconds | result |",
        "|---------|--------:|--------|",
        *(f"| helpers |    1220 | done ({note}) |" for note in _NOT_BUILT),
    ]
    report = "\n".join(["# agentsync setup report", "", "## Installer", "", *old, ""])
    lines = _stopped_copy_of(env, tmp_path, report).splitlines()
    assert [ln for ln in lines if ln.startswith("|")] == new
    # B13 round 19 W38: "not ready", since a helper built that then fails its probe is noted so too; a copy
    # round 18's renderer wrote, "not built: ...", takes the same words
    assert not [ln for ln in new if "not built" in ln]
    v27 = [ln.replace("(not ready: ", "(not built: ") for ln in new]
    report = "\n".join(["# agentsync setup report", "", "## Installer", "", *v27, ""])
    lines = _stopped_copy_of(env, tmp_path, report).splitlines()
    assert [ln for ln in lines if ln.startswith("|")] == new


_OLD_WAIT = (
    "WAITING ON YOU: {n} empty cloud folder(s) keep the listing of <folder-{k}> incomplete: an empty "
    "folder in a "
    "cloud folder is expected and loses nothing, but while the listing is incomplete, deletions in "
    "<folder-{k}> "
    "are held, not applied, and another sync does not complete it; if the folder(s) are meant to be empty, "
    "run "
    "`~/.local/bin/agentsync exclude-empty <folder-{k}>` (it adds them to that source's exclude in the "
    "config "
    "file sources.toml; an excluded folder is not mirrored if it later gains files); the docs repo's "
    "_sync/STATE.md keeps the alarm line for <folder-{k}>, which names the folder(s), up to five, and past "
    "five "
    "how to list the rest (a plain `~/.local/bin/agentsync sync` prints it too)"
)
"""An exclude-empty wait as an e2a8e8df renderer (the v21 field build) wrote it in the Summary's Loop line."""


@pytest.mark.parametrize("renderer", ["e2a8e8df", "c57cfa7c"])
def test_a_stopped_copy_by_an_older_agentsync_counts_its_nouns(
    env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, renderer: str
) -> None:
    """B13 round 15 S20 (rehearsal C3 to C6): round 14's renderer-only count words (Q39's "2 of 3 sources" and
    "listed 6 folders", the policy line's nouns, Q42/Q43's exclude-empty clauses, Q41's "up to five" clause
    only past five, O38's Status pronouns) did not reach a stopped copy, which the older installed agentsync
    renders (c57cfa7c, v22; e2a8e8df, v21). The shim rewrites each by the number in its own line, outside
    every <details> block, and leaves a line already in the new words as it is."""
    from agentsync import setup_report  # noqa: PLC0415

    step = [("list-folders", {"result": "done", "note": "listed-6", "synced": "2", "seconds": "1"})]
    new_table = setup_report._step_table(step)
    monkeypatch.setattr(setup_report, "_list_folders_note", lambda note: "listed 6 folder(s)")
    old_table = setup_report._step_table(step)
    monkeypatch.undo()
    assert old_table != new_table and "(listed 6 folder(s): 2 already synced)" in old_table[2]
    one, seven = _OLD_WAIT.format(n=1, k=1), _OLD_WAIT.format(n=7, k=2)
    if renderer == "c57cfa7c":  # Q43 and O35 were in it, Q41 and Q42 were not
        one = one.replace("folder(s) keep the", "folder keeps the").replace(
            "the folder(s) are", "the folder is"
        )
        one = one.replace("adds them to", "adds it to").replace("names the folder(s),", "names the folder,")
        seven = seven.replace("folder(s) keep", "folders keep").replace(
            "the folder(s) are", "the folders are"
        )
        seven = seven.replace("names the folder(s),", "names the folders,")
    want_one = (
        _OLD_WAIT.format(n=1, k=1)
        .replace("folder(s) keep the", "folder keeps the")
        .replace("the folder(s) are", "the folder is")
        .replace("adds them to", "adds the folder to")
        .replace("names the folder(s), up to five, and past five how to list the rest", "names the folder")
    )
    want_seven = (
        _OLD_WAIT.format(n=7, k=2)
        .replace("folder(s) keep", "folders keep")
        .replace("the folder(s) are", "the folders are")
        .replace("names the folder(s),", "names the folders,")
    )
    loop = (
        "- Loop: baseline drafted; NEXT: stop: the operator confirms the baseline questions; also waiting: "
    )
    sync = (
        "- first sync: done in 20s (install.log: the install run of 2026-10-18T05:13:49Z, before this "
        "attempt; "
        "this attempt ran none) · converted 8 · {} with a complete baseline (status, read when this report "
        "was "
        "written)"
    )
    policy = "- [policy] label rule: on ({}, {}, refuse_unlabelled=false)"
    status = (
        "- <folder-1>: 1 unknown: 0 dataless, 1 materialised-and-empty ({}); 1 of 1 checked; {} in "
        "sources.toml "
        "now; the mirror holds no file below {}; its last walk listed all 4 files the mirror holds for it"
    )
    old_status = status.format(
        "1 of them still empty on this Mac, by their link count of 2", "none of them excluded", "them"
    )
    new_status = status.format("still empty on this Mac, by its link count of 2", "it is not excluded", "it")
    report = "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            loop + one + "; " + seven,
            sync.format("2 of 3 source(s)"),
            "",
            "## Configuration",
            "",
            policy.format("1 excluded id(s)", "2 name(s)"),
            "",
            "## Installer",
            "",
            *old_table,
            "",
            "<details><summary>the last 2 line(s) of ~/agent-context/setup/install.out (what the agent "
            "saw)</summary>",
            "",
            "~~~text",
            _OLD_WAIT.format(n=1, k=1),
            "listed 6 folder(s), 2 of 3 source(s) with a complete baseline",
            "~~~",
            "",
            "</details>",
            "",
            "## Status",
            "",
            old_status,
            new_status.replace("<folder-1>", "<folder-2>"),
            "",
        ]
    )
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    assert install_sh(env, "--log-start", "x").returncode == 2
    stub = tmp_path / "old-report.md"
    stub.write_text(report, encoding="utf-8")
    assert install_sh({**env, "STUB_REPORT_FILE": str(stub)}, "--report-only").returncode == 0
    text = (report_path(env).parent / "setup" / "stopped-copy-report.md").read_text()
    lines = text.splitlines()
    assert loop + want_one + "; " + want_seven in lines, [ln for ln in lines if ln.startswith("- Loop")]
    assert sync.format(f"2 of {setup_report._plural(3, 'source')}") in lines
    assert policy.format(setup_report._plural(1, "excluded id"), setup_report._plural(2, "name")) in lines
    assert [ln for ln in lines if ln.startswith("|")] == new_table
    assert new_status in lines and old_status not in lines
    assert new_status.replace("<folder-1>", "<folder-2>") in lines, "already in the new words: as it is"
    held = text.split("<details><summary>the last 2 line", 1)[1].split("</details>", 1)[0]
    assert _OLD_WAIT.format(n=1, k=1) in held and "listed 6 folder(s), 2 of 3 source(s)" in held, "history"


def _old_wait_now(n: int, k: int) -> str:
    """:data:`_OLD_WAIT` in the current renderer's words for ``n`` folders."""
    one = n == 1
    clause = ", up to five, and past five how to list the rest" if n > 5 else ""
    return (
        _OLD_WAIT.format(n=n, k=k)
        .replace("folder(s) keep the", "folder keeps the" if one else "folders keep the")
        .replace("the folder(s) are", "the folder is" if one else "the folders are")
        .replace("adds them to", "adds the folder to" if one else "adds them to")
        .replace(
            "names the folder(s), up to five, and past five how to list the rest",
            ("names the folder" if one else "names the folders") + clause,
        )
    )


def _stopped_copy_of(env: dict[str, str], tmp_path: Path, report: str) -> str:
    """The stopped copy install.sh writes of ``report``, rendered by the installed (stub) agentsync."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    assert install_sh(env, "--log-start", "x").returncode == 2
    stub = tmp_path / "old-report.md"
    stub.write_text(report, encoding="utf-8")
    assert install_sh({**env, "STUB_REPORT_FILE": str(stub)}, "--report-only").returncode == 0
    return (report_path(env).parent / "setup" / "stopped-copy-report.md").read_text()


def test_a_stopped_copy_with_an_undated_last_run_keeps_its_left_out_count(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 18 (lane RB's review): the rule that names the last install run on v10's "last install.sh
    run: commit -" line wrote its tail character into the awk variable k, the count of install.out lines a
    stopped copy leaves out, so the tail's summary lost "N of them left out" or read "; ; of them left
    out"."""
    report = "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            "- outcome: x",
            "",
            "### Run metadata",
            "",
            "- agentsync: checkout ~/nowhere @ 0123456789ab; last install.sh run: commit -; took 2s",
            "",
            "## Installer",
            "",
            "<details><summary>the last 3 lines of ~/agent-context/setup/install.out (what the agent "
            "saw)</summary>",
            "",
            "~~~text",
            "report: ~/agent-context/setup-report.md",
            "bring back: ~/agent-context/bring-back.md (one file: report, fix request, local work)",
            "NEXT: stop",
            "~~~",
            "",
            "</details>",
            "",
        ]
    )
    text = _stopped_copy_of(env, tmp_path, report)
    assert (
        "(what the agent saw; 1 of them left out: the bring-back and issue link lines)</summary>" in text
    ), text


def test_a_stopped_copys_waits_of_ten_or_more_folders_keep_their_count(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 16 T43: the wait fix-up looked for the next wait from the count's second character, so "12
    empty cloud folder(s)" was cut at its "2", read as 2 folders, and lost its "up to five" clause, even in
    text that was already right. Counts of 10, 12 and 21, in old and current words, and one line of
    several."""
    loop = (
        "- Loop: baseline drafted; NEXT: stop: the operator confirms the baseline questions; also waiting: "
    )
    old = [loop + _OLD_WAIT.format(n=n, k=1) for n in (10, 12, 21)]
    now = [loop + _old_wait_now(n, 1) for n in (10, 12, 21)]
    several = loop + "; ".join(_OLD_WAIT.format(n=n, k=k) for k, n in enumerate((1, 12, 3), 1))
    report = "\n".join(["# agentsync setup report", "", "## Summary", "", *old, *now, several, ""])
    lines = _stopped_copy_of(env, tmp_path, report).splitlines()
    assert [ln for ln in lines if ln.startswith("- Loop")] == [
        *now,
        *now,
        loop + "; ".join(_old_wait_now(n, k) for k, n in enumerate((1, 12, 3), 1)),
    ]


def test_a_stopped_copy_by_the_v10_agentsync_takes_its_one_folder_words(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 16 T18: v10's loop.py wrote "if they are meant to be empty", which the S20 clause rule did
    not
    read, so one folder read "1 empty cloud folder keeps ...: if they are meant"; and v10's Status wrote "(1
    of
    them with a link count of 2: ...)" of one folder."""
    loop = "- Loop: baseline drafted; NEXT: stop: the operator confirms; also waiting: "
    v10 = [
        loop + _OLD_WAIT.format(n=n, k=1).replace("if the folder(s) are meant", "if they are meant")
        for n in (1, 2)
    ]
    status = (
        "- <folder-1>: 1 unknown: 0 dataless, 1 materialised-and-empty (1 of them with a link count of 2: no "
        "entry by the folder's own metadata); 0 gone; 1 of 1 checked; none of them excluded in sources.toml "
        "now; the mirror still holds 0 file(s) below 1 of the checked folder(s)"
    )
    report = "\n".join(
        ["# agentsync setup report", "", "## Summary", "", *v10, "", "## Status", "", status, ""]
    )
    lines = _stopped_copy_of(env, tmp_path, report).splitlines()
    assert [ln for ln in lines if ln.startswith("- Loop")] == [loop + _old_wait_now(n, 1) for n in (1, 2)]
    assert (
        "- <folder-1>: 1 unknown: 0 dataless, 1 materialised-and-empty (still empty on this Mac, by its "
        "link count "
        "of 2); 0 gone; 1 of 1 checked; it is not excluded in sources.toml now; the mirror still holds 0 "
        "files "
        "below 1 of the checked folder"
    ) in lines, [ln for ln in lines if ln.startswith("- <folder-1>")]


_OLD_RENDER_FIXTURE = [
    "# agentsync setup report",
    "",
    "## Summary",
    "",
    "- outcome: failed at step 1 (computed: no install run: only install.sh --list-folders ran (step 1))",
    "- installer output: no run of this attempt in the last 146 line(s) of install.out (the 2 earlier "
    "run(s) of Contoso's setup)",
    "- redaction: 87 replacement(s) of 7 value(s) (legend under Redaction)",
    "- friction (attempt 2): 1 event line(s): 1 finished",
    "- friction (attempt 3): 2 event line(s): 1 question, 1 finished",
    "- first sync: done in 20s · converted 8 · 5 of 6 source(s) with a complete baseline",
    "- [policy] label rule: on (0 excluded id(s), 2 name(s), refuse_unlabelled=false)",
    "",
    "## Agent friction log",
    "",
    "9 line(s) in 2 attempt(s):",
    "",
    "~~~text",
    "2026-10-18T10:00:01Z | step 1 | question | saw 2 file(s) in Contoso Docs | -",
    "~~~",
    "",
    "## Environment",
    "",
    "- provider folders: 1 provider folder(s) under ~/Library/CloudStorage",
    "",
    "## Installer",
    "",
    "2 install.sh run(s) in this attempt.",
    "",
    "| step | seconds | result |",
    "|------|--------:|--------|",
    "| list-folders | 1 | done (listed 6 folder(s): 2 already synced) |",
    "| report | 0 | done (agentsync) |",
    "",
    "Installer output: 2 NEXT: line(s) in 2 run(s), 0 other instruction-like line(s). An instruction-like "
    "line "
    "other than NEXT: is a hint the agent may act on before install.sh's own next step. agentsync's own "
    "WARNING "
    "and ERROR log lines, and a sync's alarm and error lines, show item paths and document names as "
    "<path>, as "
    "in Recent errors, and a log line's local time is shown in UTC.",
    "",
    "<details><summary>the last 2 line(s) of ~/agent-context/setup/install.out (what the agent "
    "saw)</summary>",
    "",
    "~~~text",
    "done: 3 file(s) for Contoso",
    "listed 6 folder(s)",
    "~~~",
    "",
    "</details>",
    "",
    "## Doctor",
    "",
    "42 check(s): 39 ok, 0 info, 3 warn, 0 FAIL. Only the lines that are not ok: 1 expected warn(s) end in "
    "(expected: <why>) instead of their fix: nothing for this setup to do.",
    "",
    "~~~text",
    "[warn] launcher.signature — ad hoc (expected: ad hoc launcher); 2 file(s)",
    "~~~",
    "",
    "## Status",
    "",
    "- images: 1 file(s) (6 page(s)), 0 wait(s), 0 conversion(s), 1 other entry(ies)",
    "- conversions: 1 conversion(s) were used again; 2 queued id(s) have no file; 1 queued purge(s) name a "
    "gone file",
    "- <folder-1>: 0 unknown; its last walk listed all 4 file(s) the mirror holds for it",
    "- <folder-2>: 0 unknown; its last walk listed all 1 file(s) the mirror holds for it",
    "- sources: 3 local or inbox source(s) (3 source(s) with a folder) of the last 4 run(s), 37 day(s)",
    "- images: 2 files (6 pages), already in the current words",
    "- a label rule names no count: id(s) and name(s)",
    "",
    "## Redaction",
    "",
    "- 87 replacement(s) of 7 value(s)",
    "",
    "Read the report before sending it: anything else confidential is yours to remove.",
    "",
]
"""A stopped copy as an agentsync from before B13 round 15 S26 (61474fdf, v23) words it, with made-up names:
"(s)" beside every known count, counts of 0, 1 and 2, a plural verb after a count of 1, and the same forms in
the install.out <details> block and the friction log's fence, which hold history."""


def test_a_stopped_copy_by_an_older_agentsync_words_every_count(env: dict[str, str], tmp_path: Path) -> None:
    """B13 round 16 T23, T84, T18 (one defect, built once; rehearsal C3 to C8, SC): a stopped copy rendered by
    an older installed agentsync kept 24 to 28 "(s)" beside a known count, outside Summary lines S20 named:
    the friction log's header, Environment, Installer (its table too), Doctor, Status and Redaction. Every
    "word(s)" outside <details> and fences takes its count's noun, a plural verb after a count of 1 agrees,
    and the Installer paragraph gains ba5e253f's sentence; the history blocks stay as written, quiet() still
    drops a quiet-only friction line, and the table is padded again."""
    text = _stopped_copy_of(env, tmp_path, "\n".join(_OLD_RENDER_FIXTURE))
    lines = text.splitlines()
    outside, det, fence = [], False, False
    for ln in lines:
        if ln.startswith("<details>"):
            det = True
        if not det and ln.startswith("~~~"):
            fence = not fence
        elif not det and not fence:
            outside.append(ln)
        if ln == "</details>":
            det = False
    counted = re.compile(r"(?:^|[^A-Za-z0-9.])[0-9]+ (?:\S+ ){0,3}[A-Za-z]+\((?:s|ies)\)")
    assert [ln for ln in outside if counted.search(ln)] == [], "no (s) beside a count outside history"
    for want in (
        "- installer output: no run of this attempt in the last 146 lines of install.out (the 2 earlier "
        "runs of "
        "Contoso's setup)",
        "- redaction: 87 replacements of 7 values (legend under Redaction)",
        "- friction (attempt 3): 2 event lines: 1 question, 1 finished",
        "- first sync: done in 20s · converted 8 · 5 of 6 sources with a complete baseline",
        "- [policy] label rule: on (0 excluded ids, 2 names, refuse_unlabelled=false)",
        "9 lines in 2 attempts:",
        "- provider folders: 1 provider folder under ~/Library/CloudStorage",
        "2 install.sh runs in this attempt.",
        "<details><summary>the last 2 lines of ~/agent-context/setup/install.out (earlier attempts' "
        "install.sh "
        "output: none of this attempt's runs is in it)</summary>",
        "- images: 1 file (6 pages), 0 waits, 0 conversions, 1 other entry",
        "- conversions: 1 conversion was used again; 2 queued ids have no file; 1 queued purge names a "
        "gone file",
        "- <folder-1>: 0 unknown; its last walk listed every one of the 4 files the mirror holds for it",
        "- <folder-2>: 0 unknown; its last walk listed the one file the mirror holds for it",
        "- sources: 3 local or inbox sources (3 sources with a folder) of the last 4 runs, 37 days",
        "- images: 2 files (6 pages), already in the current words",
        "- a label rule names no count: id(s) and name(s)",
        "- 87 replacements of 7 values",
    ):
        assert want in lines, (want, [ln for ln in lines if ln[:12] == want[:12]])
    assert "- friction (attempt 2):" not in text, "quiet() read the old form first"
    doctor = lines[lines.index("## Doctor") + 2]
    assert doctor.startswith(
        "42 checks: 39 ok, 0 info, 3 warn, 0 FAIL. Only the lines that are not ok: 1 "
        "expected warn ends in (expected: <why>)"
    ), doctor
    [installer] = [ln for ln in lines if ln.startswith("Installer output: ")]
    assert installer.startswith(
        "Installer output: 2 NEXT: lines in 2 runs, 0 other instruction-like lines. An instruction-like line "
        "other than NEXT: is a hint the agent may act on before install.sh's own next step; "
        f"{_stopped_fix_line()} agentsync's own WARNING"
    ), installer
    table = [ln for ln in lines if ln.startswith("|")]
    assert "| list-folders | 1 | done (listed 6 folders: 2 already synced) |" in [
        " ".join(ln.split()) for ln in table
    ], table
    assert len({len(ln) for ln in table}) == 1, table
    history = text.split("runs is in it)</summary>", 1)[1].split("</details>", 1)[0]
    assert "done: 3 file(s) for Contoso\nlisted 6 folder(s)" in history
    assert "saw 2 file(s) in Contoso Docs" in text and "ad hoc launcher); 2 file(s)" in text, (
        "fences as written"
    )


def _stopped_fix_line() -> str:
    [line] = re.findall(r'(?m)^STOPPED_FIX_LINE="(.*)" #', INSTALL_SH.read_text())
    return line


def test_the_shell_stopped_copy_fix_line_is_the_reports() -> None:
    """B13 round 16 T23: the sentence the shim adds to an older Installer paragraph is the renderer's own
    (ba5e253f), after the same "next step" words."""
    source = (Path(__file__).parents[1] / "src" / "agentsync" / "setup_report.py").read_text(encoding="utf-8")
    texts = [
        part.value
        for node in ast.walk(ast.parse(source))
        for part in (node.values if isinstance(node, ast.JoinedStr) else [node])
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    ]
    [nxt] = re.findall(r'(?m)^STOPPED_NEXT_STEP="(.*)"$', INSTALL_SH.read_text())
    want = nxt.removesuffix(". ") + "; " + _stopped_fix_line() + " "
    assert any(want in t for t in texts), want


def test_no_renderer_line_writes_s_beside_a_count() -> None:
    """B13 round 16 T23 (S20's guard): since round 15 S26 every renderer line takes its noun from a known
    count, which is what lets the stopped copy's rewrite (install.sh plural_all) put every "(s)" an older
    renderer wrote into the current words. An f-string of the renderer that writes "(s)" or "(ies)" right
    after a value, at most three words on, would bring the form back unrewritten. Other modules' lines reach
    the report only inside a fence (the Doctor and install.out blocks), which the rewrite leaves as
    written."""
    found = []
    for path in [Path(__file__).parents[1] / "src" / "agentsync" / "setup_report.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.JoinedStr):
                continue
            for before, part in itertools.pairwise(node.values):
                if (
                    isinstance(before, ast.FormattedValue)
                    and isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and re.match(r"\s?(?:\S+\s){0,3}\S*\((?:s|ies)\)", part.value)
                ):
                    found.append(f"{path.name}:{part.lineno}: {part.value[:60]}")
    assert found == []


_DOT = "\u00b7"
_V10_DOCTOR = [
    "[FAIL] docs_repo.permissions \u2014 group/other can read tenant data: ~/agent-context/docs/_eval (fix: "
    "agentsync sync (it makes these owner-only))",
    *(
        line
        for n in (1, 2)
        for line in (  # v10's words: the volume line's path comes after its first clause
            f"[FAIL] source.<folder-{n}>.listable \u2014 ~/Library/CloudStorage/OneDrive-<org-1>/"
            f"<folder-{n}> does not exist (fix: fix path in [[source]], or sign in to the sync client)",
            f"[FAIL] source.<folder-{n}>.volume \u2014 File Provider root UUID not checked: "
            f"~/Library/CloudStorage/OneDrive-<org-1>/<folder-{n}> is missing (fix: fix path in [[source]], "
            "or sign in to the sync client)",
        )
    ),
    "[FAIL] source.<folder-3>.listable \u2014 ~/Documents/<folder-3> does not exist (fix: fix path in "
    "[[source]])",
    "[FAIL] source.<folder-4>.listable \u2014 ~/Library/CloudStorage/Dropbox/<folder-4> does not exist (fix: "
    "fix path in [[source]], or sign in to the sync client)",
    "[warn] install.commit \u2014 installed from aaaaaaaaaaaa, but the checkout at "
    "~/src/agent-context-sync is "
    "at bbbbbbbbbbbb (fix: re-run install.sh (~/src/agent-context-sync/scripts/install.sh))",
]
_V10_FAILS = [
    "docs_repo.permissions FAIL",
    *(f"source.<folder-{n}>.{c} FAIL" for n in (1, 2) for c in ("listable", "volume")),
    "source.<folder-3>.listable FAIL",
    "source.<folder-4>.listable FAIL",
]


def _v10_step1_report(summary: list[str], tail: Sequence[str] = ()) -> str:
    """A report as the v10 build (4a42af9) renders a step 1 sign-in stop, with made-up names; ``tail``: the
    sections after Doctor."""
    return "\n".join(
        [
            "# agentsync setup report",
            "",
            "## Summary",
            "",
            *summary,
            "",
            "## Doctor",
            "",
            "54 check(s): 46 ok, 1 info, 1 warn, 7 FAIL. Only the lines that are not ok:",
            "",
            "~~~text",
            *_V10_DOCTOR,
            "~~~",
            "",
            *tail,
        ]
    )


_V10_ITEMS = ["", "Items that were not one command (attempt 2):", "", "- none"]
_STOP_ITEM = (
    "- install.sh \u00b7 step 1 \u00b7 the folder list stopped: OneDrive is not signed in, so the 2 "
    "folders this Mac "
    "already syncs are missing (not logged: an unattended run goes to the report)"
)
"""The Items list's entry the current renderer writes for M1o's stop (_sign_in_stop_item; B13 round 17 U26,
U107)."""
_NOW_ITEMS = [*_V10_ITEMS[:-1], _STOP_ITEM]
_V10_TAIL = [
    "## Run metadata",
    "",
    "- agentsync: checkout ~/src/agent-context-sync @ 0123456789ab \u00b7 on origin/main: yes (as of the "
    "checkout's last fetch); last install.sh run: commit -",
    "",
    "## Agent friction log",
    "",
    "Embedded from ~/agent-context/setup/friction.md as this report read it: 7 line(s), last modified "
    "2026-10-18T10:00:05Z (a line written after that is not in this report: run setup-report again). "
    "Redacted "
    "with the same placeholders as the rest of the report.",
    "",
    "2 attempt(s):",
    "- attempt 1 (lines 1-4; started 2026-10-18T09:00:00Z, prompt v10, agent Contoso Agent, beta): worked; 1 "
    "event line(s): 1 finished; finished",
    "- attempt 2 (lines 5-5; started 2026-10-18T10:00:00Z, prompt v25, agent x): failed at step 1; 0 event "
    'line(s): none; no "end | finished" line',
    "",
]
"""v10's words the rewrite misses (B13 round 17 U27): the checkout line's undated fetch and the --list-folders
run's commit, the friction header's rerun, the attempt bits joined by ", " and a "finished" tail."""


def _now_tail(fetch: str) -> list[str]:
    """:data:`_V10_TAIL` in the current renderer's words, the fetch as ``fetch`` words it."""
    return [
        "## Run metadata",
        "",
        "- agentsync: checkout ~/src/agent-context-sync @ 0123456789ab \u00b7 on origin/main: yes "
        f"({fetch}); "
        "last install.sh run: commit 0123456789ab",
        "",
        "## Agent friction log",
        "",
        "Embedded from ~/agent-context/setup/friction.md as this report read it: 7 lines, last modified "
        "2026-10-18T10:00:05Z (a line written after that is not in this report: run "
        f"`{setup_report.INSTALL_SH} --report-only` again). Redacted with the same placeholders as the "
        "rest of "
        "the report.",
        "",
        "2 attempts:",
        "- attempt 1 (lines 1-4; started 2026-10-18T09:00:00Z; prompt v10; agent Contoso Agent, beta): "
        "worked; "
        "1 event line: 1 finished",
        "- attempt 2 (line 5; started 2026-10-18T10:00:00Z; prompt v25; agent x): failed at step 1; 0 event "
        'lines: none; no "end | finished" line',
        "",
    ]


_V10_SUMMARY = [
    "- **outcome: failed at step 1** (computed: no install run: only install.sh --list-folders ran (step 1))",
    "- Loop: baseline drafted; NEXT: the docs_repo.permissions check failed: do what the fix on its [FAIL] "
    "line below says",
    "- expected turns: no folder question (2 folders already synced: step 1 asks at most whether to add one; "
    "not logged) \u00b7 Allow clicks: none possible (sandbox)",
    "- friction (attempt 2): 1 event line(s): 1 finished",
    "- installer output: 2 NEXT: line(s) in 2 run(s) (exactly one per install.sh run is expected) and 0 "
    "other "
    "instruction-like line(s) in the last 139 line(s) of install.out",
    f"- doctor: 7 FAIL, 1 warn (54 checks) {_DOT} unexpected 8: "
    + ", ".join([*_V10_FAILS, "install.commit warn"]),
]
_NOW_SUMMARY = [  # B13 round 17 U53, U81: the renderer's "ran, in step 1" and "(N checks)", T20's
    "- **outcome: failed at step 1** (computed: no install run: only install.sh --list-folders ran, in step "
    "1; it "
    "stopped: OneDrive is not signed in)",
    _V10_SUMMARY[1],
    "- expected turns: none (step 1's list stopped: OneDrive is not signed in, so the 2 folders this Mac "
    "already "
    "syncs are missing; they are kept: no folder question and no Allow click)",
    "- installer output: 2 NEXT: lines in 2 runs (exactly one per install.sh run is expected) and 0 other "
    "instruction-like lines in the last 139 lines of install.out",
    f"- doctor: 7 FAIL, 1 warn (54 checks) {_DOT} expected 1: install.commit (step 1 stopped this attempt "
    f"before step 2 reinstalls agentsync) {_DOT} from the sign-in stop (4 checks): "
    + ", ".join(_V10_FAILS[1:5])
    + f" {_DOT} unexpected 3: "
    + ", ".join([_V10_FAILS[0], *_V10_FAILS[5:]]),
]


def _step1_stop_home(
    env: dict[str, str],
    tmp_path: Path,
    report: str,
    *,
    last: str = "list",
    listed: str = "note=not-signed-in apps=onedrive missing=2 synced=2",
    friction: str = "",
    before: Sequence[str] = (),
) -> str:
    """M1o's state (rehearsal B): an install by an older build, then this attempt's step 1, which stopped on
    OneDrive's sign-in, then --report-only, whose report the installed build renders (``report``); the
    report as install.sh leaves it. ``last="install"``: an install run after the list; ``listed``: the list
    step's tokens; ``friction``: lines this attempt logged; ``before``: install.log lines before its list."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        "Attempt: 2026-10-18T09:00:00Z\nPrompt: v10\nAgent: x\n2026-10-18T09:05:00Z | end | finished\n"
        f"Attempt: 2026-10-18T10:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n{friction}",
        encoding="utf-8",
    )
    log = [
        "2026-10-18T09:00:10Z run=20261018T090010Z-41 start install.sh compat=10 commit=0123456789ab kind=- "
        "source=- args=--source-local x",
        "2026-10-18T09:00:20Z run=20261018T090010Z-41 step=first-sync seconds=4 rc=0 result=done",
        "2026-10-18T09:00:30Z run=20261018T090010Z-41 end rc=0 seconds=20",
        *before,
        "2026-10-18T10:00:10Z run=20261018T100010Z-42 start install.sh compat=24 commit=- kind=- source=- "
        "args=--list-folders",
        "2026-10-18T10:00:11Z run=20261018T100010Z-42 step=list-folders seconds=1 rc=3 result=failed "
        f"{listed}",
        "2026-10-18T10:00:11Z run=20261018T100010Z-42 end rc=3 seconds=1",
    ]
    if last == "install":
        log += [
            "2026-10-18T10:10:10Z run=20261018T101010Z-43 start install.sh compat=24 commit=- kind=- "
            "source=- "
            "args=",
            "2026-10-18T10:10:30Z run=20261018T101010Z-43 end rc=1 seconds=20",
        ]
    (home / "agent-context" / "setup" / "install.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    stub = tmp_path / "rendered.md"
    stub.write_text(report, encoding="utf-8")
    cp = install_sh({**env, "STUB_REPORT_FILE": str(stub)}, "--report-only")
    assert cp.returncode == 0 and "(agentsync setup-report)" in cp.stdout, cp.stdout + cp.stderr
    assert not (report_path(env).parent / "setup" / "stopped-copy-report.md").exists(), "not a stopped copy"
    return report_path(env).read_text(encoding="utf-8")


def test_a_step_1_stop_rendered_by_the_older_installed_agentsync_takes_this_checkouts_words(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 16 T11 (rehearsal B, M1o): after the current prompt's step 1 stopped on a sign-in, the field
    Mac's report is rendered by the installed v10 build (step 2 never reinstalled it): it named no cause,
    called the sign-in stop's FAILs and install.commit unexpected, kept the dropped friction line and wrote
    "(s)" beside its counts. The installer keeps that renderer (one build in doctor) and rewrites only the
    renderer-only words and the stop's three lines; the Loop NEXT and the Doctor part stay as it wrote them,
    a source FAIL of another app or outside CloudStorage stays unexpected, and a report already in the
    current words passes unchanged."""
    text = _step1_stop_home(env, tmp_path, _v10_step1_report(_V10_SUMMARY))
    summary = text.split("## Summary\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert summary == _NOW_SUMMARY, summary
    doctor = text.split("## Doctor\n\n", 1)[1]
    assert doctor.startswith("54 checks: 46 ok, 1 info, 1 warn, 7 FAIL. Only the lines that are not ok:\n")
    assert "\n".join(_V10_DOCTOR) in doctor, "the Doctor part as the installed build wrote it"
    bring_back = (report_path(env).parent / "bring-back.md").read_text(encoding="utf-8")
    assert _NOW_SUMMARY[0] in bring_back, "bring-back.md embeds the rewritten report"
    now = _v10_step1_report(_NOW_SUMMARY).replace("54 check(s):", "54 checks:")
    assert _step1_stop_home(env, tmp_path, now) == now, "the current words pass unchanged"


def test_a_step_1_stop_of_the_older_build_names_the_earlier_first_sync(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 18 V16 (rehearsal B, M1o): v10 read only this attempt's runs, the --list-folders one, and
    wrote "no install.sh run with a first-sync step (install.log)" beside install.log's earlier first sync,
    with install.out's counts untagged, as if this attempt's. The rewrite gives the renderer's words for a
    list-only attempt and tags the counts as the earlier install run's; the current words pass unchanged."""
    sync = "- first sync: {} \u00b7 converted 7, deferred 0 online-only (install.out{}) \u00b7 2 of 3 sources"
    old = sync.format("no install.sh run with a first-sync step (install.log)", "")
    now = sync.format(
        "this attempt ran only install.sh --list-folders, no first sync (install.log)",
        ": the install run of 2026-10-18T09:00:10Z, before this attempt",
    )
    text = _step1_stop_home(env, tmp_path, _v10_step1_report([*_V10_SUMMARY, old]))
    assert _summary(text)[-1] == now, text
    assert "no install.sh run with a first-sync step" not in text
    current = _v10_step1_report([*_NOW_SUMMARY, now]).replace("54 check(s):", "54 checks:")
    assert _step1_stop_home(env, tmp_path, current) == current, "the current words pass unchanged"


_V10_LINE_NUMBERS = "Each line is shown with its line number: the F<n> ids in the Summary are these numbers."
"""The v10 renderer's friction-section line, written whether or not the Summary cites an F<n> id."""


@pytest.mark.parametrize("mode", ["step1", "copy", "step1, an F id cited"])
def test_the_older_builds_line_numbers_line_names_f_ids_only_when_the_summary_cites_one(
    env: dict[str, str], tmp_path: Path, mode: str
) -> None:
    """B13 round 19 W15 (rehearsal B, M1o and SC): the v10 renderer always wrote "the F<n> ids in the Summary
    are these numbers", and the rewrite passed it on beside a Summary with no F id (agent friction 0/0/0);
    the renderer since round 6 G10 says it only where the Summary cites one. The rewrite does the same, in
    both modes, reading its own rewritten Summary."""
    summary = list(_V10_SUMMARY)
    if mode.endswith("cited"):
        summary.append("- agent friction: 1 deviation, 0 prompt, 0 error (F8)")
    report = _v10_step1_report(summary, [*_V10_TAIL, _V10_LINE_NUMBERS, ""])
    if mode == "copy":
        text = _stopped_copy_of(env, tmp_path, report)
    else:
        text = _step1_stop_home(env, tmp_path, report)
    lines = text.splitlines()
    if mode.endswith("cited"):
        assert _V10_LINE_NUMBERS in lines, text
    else:
        assert "Each line is shown with its line number." in lines and "F<n> ids" not in text, text


@pytest.mark.parametrize("mode", ["step1", "copy"])
def test_the_older_builds_macos_line_gives_the_version_and_build(
    env: dict[str, str], tmp_path: Path, mode: str
) -> None:
    """B13 round 19 W16 (rehearsal B, M1o and SC): a renderer from before round 12 N26 wrote
    "- macOS: macOS 15.7.9 (24G830)", the product name under a label that says it already, and the rewrite
    passed it on. Both modes give the version and build, as the current renderer and the shell report do,
    "?" for a version the old build did not read; a line in the current words passes unchanged."""

    def environment(*lines: str) -> str:
        tail = [*_V10_TAIL, "## Environment", "", *lines, "- Python: 3.11.9", ""]
        report = _v10_step1_report(_V10_SUMMARY, tail)
        text = (
            _stopped_copy_of(env, tmp_path, report)
            if mode == "copy"
            else _step1_stop_home(env, tmp_path, report)
        )
        return text.split("\n## Environment\n\n", 1)[1].split("\n\n", 1)[0].rstrip("\n")

    assert environment("- macOS: macOS 15.5 (24F74)") == "- macOS: 15.5 (24F74)\n- Python: 3.11.9"
    assert environment("- macOS: macOS (24F74)") == "- macOS: ? (24F74)\n- Python: 3.11.9"
    assert environment("- macOS: macOS") == "- macOS: ?\n- Python: 3.11.9", "neither version nor build"
    assert environment("- macOS: 15.5 (24F74)") == "- macOS: 15.5 (24F74)\n- Python: 3.11.9"


def test_a_step_1_stop_of_the_older_build_says_its_fix_for_one_expected_warn(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 18 V27: an older renderer writes its own one-warn note with "their fix" (an ad hoc
    launcher's expected warn); the step 1 rewrite gives it the current renderer's "its fix", as the copy's
    does, and leaves the rest of the header."""
    head = "54 check(s): 46 ok, 1 info, 1 warn, 7 FAIL. Only the lines that are not ok:"
    note = " 1 expected warn(s) end in (expected: <why>) instead of their fix: nothing for this setup to do."
    text = _step1_stop_home(env, tmp_path, _v10_step1_report(_V10_SUMMARY).replace(head, head + note))
    doctor = text.split("## Doctor\n\n", 1)[1].splitlines()
    assert doctor[0] == (
        "54 checks: 46 ok, 1 info, 1 warn, 7 FAIL. Only the lines that are not ok: 1 expected warn ends in "
        "(expected: <why>) instead of its fix: nothing for this setup to do."
    )


def _items(text: str) -> list[str]:
    """The Summary's Items list, heading first."""
    summary = text.split("## Summary\n\n", 1)[1].split("\n\n## ", 1)[0]
    return summary.split("\n\nItems that were not one command", 1)[1].splitlines()


@pytest.mark.parametrize(
    "state", ["none", "an entry", "no folders", "a friction stop", "an Allow click first"]
)
def test_a_step_1_stop_of_the_older_build_lists_the_stop_as_an_item(
    env: dict[str, str], tmp_path: Path, state: str
) -> None:
    """B13 round 17 U26, U107 (one defect from two finders): the rewrite left v10's Items list, "- none",
    beside "failed at step 1 ... it stopped". The stop is the list's first entry, as the current renderer's
    _sign_in_stop_item words it, under its guards: an attempt with a friction stop (an error line) keeps the
    list as rendered, and a list that already names the stop passes unchanged."""
    entry = "- question \u00b7 step 2 \u00b7 the agent asked which folder to add (F7)"
    items = [*_V10_ITEMS[:-1], entry] if state == "an entry" else _V10_ITEMS
    listed = "note=no-folders apps= synced=0" if state == "no folders" else ""
    friction = (
        "2026-10-18T10:00:30Z | step 1 | error | git pull failed | -\n" if state == "a friction stop" else ""
    )
    # a list of the attempt that ended on the Allow click makes it that click's (_list_stopped): no item
    clicked = [
        "2026-10-18T10:00:02Z run=20261018T100002Z-40 start install.sh compat=24 commit=- kind=- source=- "
        "args=--list-folders",
        "2026-10-18T10:00:03Z run=20261018T100002Z-40 step=list-folders seconds=1 rc=4 result=failed "
        "note=denied",
        "2026-10-18T10:00:03Z run=20261018T100002Z-40 end rc=4 seconds=1",
    ]
    text = _step1_stop_home(
        env,
        tmp_path,
        _v10_step1_report([*_V10_SUMMARY, *items]),
        friction=friction,
        before=clicked if state == "an Allow click first" else (),
        **({"listed": listed} if listed else {}),
    )
    got = _items(text)
    item = (
        "- install.sh \u00b7 step 1 \u00b7 the folder list stopped: no folders are synced yet, so no "
        "folder was "
        "listed (not logged: on this stop step 1 goes to the report)"
        if state == "no folders"
        else _STOP_ITEM
    )
    want = {
        "none": [" (attempt 2):", "", item],
        "an entry": [" (attempt 2):", "", item, entry],
        "no folders": [" (attempt 2):", "", item],
        "a friction stop": [" (attempt 2):", "", "- none"],
        "an Allow click first": [" (attempt 2):", "", "- none"],
    }[state]
    assert got == want, got
    if state == "none":
        now = _v10_step1_report([*_NOW_SUMMARY, *_NOW_ITEMS]).replace("54 check(s):", "54 checks:")
        assert _step1_stop_home(env, tmp_path, now) == now, "the current words pass unchanged"


def test_a_step_1_stop_of_the_older_build_takes_the_current_friction_and_checkout_words(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 17 U27 (M1o): v10's words the step 1 rewrite missed: the friction header's "run setup-report
    again", the attempt bits joined by ", " (an agent's own comma kept), "lines 5-5" and a "finished" tail
    beside "1 finished", the checkout's undated "(as of the checkout's last fetch)", and "last install.sh run:
    commit -", which v10 took from the --list-folders run: the renderer names the last run that reached an
    install step, from install.log. A report in the current words passes unchanged. B13 round 18 V91: the date
    is the FETCH_HEAD of the checkout the line names (here one in the test's home, not install.sh's own); a
    named folder that is not here keeps the undated words, which are true."""
    fetch = Path(env["HOME"]) / "src" / "agent-context-sync" / ".git" / "FETCH_HEAD"
    fetch.parent.mkdir(parents=True)
    fetch.write_text("0123456789ab\t\tbranch 'main' of https://github.com/contoso/agentsync\n")
    when = calendar.timegm((2026, 5, 6, 7, 8, 9, 0, 0, 0))
    os.utime(fetch, (when, when))
    text = _step1_stop_home(env, tmp_path, _v10_step1_report(_V10_SUMMARY, _V10_TAIL))
    tail = "## Run metadata" + text.split("## Run metadata", 1)[1]
    assert tail == "\n".join(_now_tail("as of the checkout's last fetch, 2026-05-06T07:08:09Z")), tail
    shutil.rmtree(fetch.parent.parent)
    text = _step1_stop_home(env, tmp_path, _v10_step1_report(_V10_SUMMARY, _V10_TAIL))
    tail = "## Run metadata" + text.split("## Run metadata", 1)[1]
    assert tail == "\n".join(_now_tail("as of the checkout's last fetch")), tail
    now = _v10_step1_report(_NOW_SUMMARY, _now_tail("as of the checkout's last fetch, 2026-10-18T09:59:00Z"))
    now = now.replace("54 check(s):", "54 checks:")
    assert _step1_stop_home(env, tmp_path, now) == now, "the current words pass unchanged"


_STEP1_STOP_WARN = "step 1 stopped this attempt before step 2 reinstalls agentsync"
_GIT_STOP_REPORT = [
    "# agentsync setup report",
    "",
    "## Summary",
    "",
    "- **outcome: failed at step 1** (computed: no install.sh run; step 1 logged an error)",
    "- expected turns: no folder question (2 folders already synced: step 1 asks at most whether to add one; "
    "not logged) \u00b7 Allow clicks: none possible (sandbox)",
    "- friction (attempt 1): 2 event line(s): 1 error, 1 finished",
    f"- doctor: 0 FAIL, 1 warn (54 checks) {_DOT} unexpected 1: install.commit warn",
    "",
    "## Doctor",
    "",
    "54 check(s): 53 ok, 0 info, 1 warn, 0 FAIL. Only the lines that are not ok:",
    "",
    "~~~text",
    "[warn] install.commit \u2014 installed from 4a42af9abcde, but the checkout at "
    "~/src/agent-context-sync is "
    "at 0123456789ab (fix: re-run install.sh (~/src/agent-context-sync/scripts/install.sh))",
    "~~~",
    "",
]
"""rehearsal D's oldr: a git stop of the current prompt's step 1 command, rendered by the v10 build."""


@pytest.mark.parametrize(
    "after", [False, True, "attempt"], ids=["git stop", "an install run after it", "a later attempt"]
)
def test_a_git_stop_rendered_by_the_older_installed_agentsync_takes_this_checkouts_words(
    env: dict[str, str], tmp_path: Path, after: bool | str
) -> None:
    """B13 round 17 U34 (rehearsal D, oldr): a step 1 command's git stop, before --log-start, starts no
    install.log run, so the T11 rewrite (keyed on a failed list run) left the older build's report as
    rendered: install.commit unexpected, a folder-question turns line, "(s)" beside counts. A second fact
    reader keys on the stated step 1 error with no install run after it: install.commit moves to expected,
    the turns line is the renderer's for a command that stopped before --log-start, and the counts take
    their nouns; no cause is added to the outcome, which the renderer does not word. An install run after the
    error leaves the report as rendered, and the current words pass unchanged."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-18T09:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n"
        f"2026-10-18T10:00:30Z | step 1 | error | prompt v{COMPAT}, Claude Code test-agent: git -C "
        "~/src/agent-context-sync pull --ff-only failed | -\n"
        # another session's attempt after the stop (its list ended on the Allow click): not the git stop's
        + (f"Attempt: 2026-10-18T11:00:00Z\nPrompt: v{COMPAT}\nAgent: y\n" if after == "attempt" else ""),
        encoding="utf-8",
    )
    log = [
        "2026-10-18T09:00:10Z run=20261018T090010Z-41 start install.sh compat=24 commit=- kind=- source=- "
        "args=--list-folders",
        "2026-10-18T09:00:11Z run=20261018T090010Z-41 step=list-folders seconds=1 rc=0 result=done "
        "note=listed-6",
        "2026-10-18T09:00:11Z run=20261018T090010Z-41 end rc=0 seconds=1",
    ]
    if after is True:
        log.append(
            "2026-10-18T10:05:00Z run=20261018T100500Z-42 start install.sh compat=24 commit=- kind=- "
            "source=- args="
        )
    (home / "agent-context" / "setup" / "install.log").write_text("\n".join(log) + "\n", encoding="utf-8")

    def rendered(report: str) -> str:
        stub = tmp_path / "rendered.md"
        stub.write_text(report, encoding="utf-8")
        cp = install_sh({**env, "STUB_REPORT_FILE": str(stub)}, "--report-only")
        assert cp.returncode == 0, cp.stdout + cp.stderr
        return report_path(env).read_text(encoding="utf-8")

    report = "\n".join(_GIT_STOP_REPORT)
    text = rendered(report)
    if after:
        assert text == report
        return
    summary = text.split("## Summary\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
    now = [
        _GIT_STOP_REPORT[4],
        "- expected turns: none (step 1's command stopped before install.sh --log-start, before the folder "
        "list: no folder question and no Allow click)",
        # an error and the end line alone are the quiet kinds the renderer drops (STOPPED_COPY_QUIET_KINDS)
        f"- doctor: 0 FAIL, 1 warn (54 checks) {_DOT} expected 1: install.commit ({_STEP1_STOP_WARN}) {_DOT} "
        "unexpected 0",
    ]
    assert summary == now, summary
    assert "(s)" not in text.split("~~~text", 1)[0], text
    current = "\n".join([*_GIT_STOP_REPORT[:4], *now, *_GIT_STOP_REPORT[8:]]).replace(
        "54 check(s):", "54 checks:"
    )
    assert rendered(current) == current, "the current words pass unchanged"


_GIT_ERROR = (
    f"2026-10-18T10:00:30Z | step 1 | error | prompt v{COMPAT}, Claude Code test-agent: git -C "
    "~/src/agent-context-sync pull --ff-only failed | -\n"
)
_EARLIER_LIST = [
    "2026-10-18T09:00:10Z run=20261018T090010Z-41 start install.sh compat=24 commit=- kind=- source=- "
    "args=--list-folders",
    "2026-10-18T09:00:11Z run=20261018T090010Z-41 step=list-folders seconds=1 rc=0 result=done note=listed-6",
    "2026-10-18T09:00:11Z run=20261018T090010Z-41 end rc=0 seconds=1",
]
_LATER_LIST = [line.replace("T09:00:1", "T10:01:1").replace("090010Z", "100110Z") for line in _EARLIER_LIST]
"""An rc-0 --list-folders run after the git stop's error, which the renderer's stopping_error reads as
resolving it."""
_V10_TIME = (
    "- time: session 18s (first to last timestamp); install.sh (install.log): step 1 --list-folders 2s"
)
_ONE_STAMP = "- time: session: one timestamp (2026-10-18T10:00:30Z), no duration"


def _git_stop_report(env: dict[str, str], tmp_path: Path, friction: str, log: list[str], report: str) -> str:
    """rehearsal D's git stop: ``friction`` and install.log ``log``, then --report-only, whose report the
    installed v10 build renders (``report``); the report as install.sh leaves it."""
    home = Path(env["HOME"])
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(env["STUB_AGENTSYNC"], home / ".local" / "bin" / "agentsync")
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-18T09:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n{friction}")
    (home / "agent-context" / "setup" / "install.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    stub = tmp_path / "rendered.md"
    stub.write_text(report, encoding="utf-8")
    cp = install_sh({**env, "STUB_REPORT_FILE": str(stub)}, "--report-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return report_path(env).read_text(encoding="utf-8")


def _summary(text: str) -> list[str]:
    return text.split("## Summary\n\n", 1)[1].split("\n\n", 1)[0].splitlines()


def test_a_git_stop_with_no_end_line_rendered_by_the_older_build_is_not_called_stale(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 18 V30 (rehearsal D, oldr): since round 17 U33 --report-only appends no end line to a
    session that logged only step 1's git error, and the v10 build, which does not split that session off,
    warned the report "may be stale" and told the agent to run `agentsync setup-report` again (not on PATH),
    with a session time across both sessions. The rewrite gives the renderer's note on the missing end line
    and its one-timestamp time line; the other shims hold and a second pass changes nothing."""
    warning = (
        '- WARNING: attempt 1 has no "end | finished" line (install.sh --report-only), so this report may be '
        "stale: it was written before prompt step 3, or the agent stopped early. Run `agentsync "
        "setup-report` again after step 3."
    )
    friction = (
        "- agent friction: 0 deviation, 0 prompt, 1 error (F4); F4 stopped the run (the outcome says so)"
    )
    report = "\n".join(
        [*_GIT_STOP_REPORT[:5], warning, _GIT_STOP_REPORT[5], friction, _V10_TIME, *_GIT_STOP_REPORT[7:]]
    )
    text = _git_stop_report(env, tmp_path, _GIT_ERROR, _EARLIER_LIST, report)
    assert _summary(text) == [
        _GIT_STOP_REPORT[4],
        "- note: the step 1 error (F4) was logged by another session after the earlier session's `install.sh "
        "--log-start`, which has no end line: this session stopped before `install.sh --log-start`, so "
        "`install.sh --report-only` had no attempt to close.",
        "- expected turns: none (step 1's command stopped before install.sh --log-start, before the folder "
        "list: no folder question and no Allow click)",
        friction,
        _ONE_STAMP,
        f"- doctor: 0 FAIL, 1 warn (54 checks) {_DOT} expected 1: install.commit ({_STEP1_STOP_WARN}) {_DOT} "
        "unexpected 0",
    ], text
    assert _git_stop_report(env, tmp_path, _GIT_ERROR, _EARLIER_LIST, text) == text, "a second pass"
    # a --list-folders run after the error that failed is in the renderer's attempt too: a span, not one stamp
    failed = [ln.replace("rc=0", "rc=3").replace("result=done", "result=failed") for ln in _LATER_LIST]
    text = _git_stop_report(env, tmp_path, _GIT_ERROR, [*_EARLIER_LIST, *failed], report)
    assert _V10_TIME in _summary(text) and _ONE_STAMP not in text, text


@pytest.mark.parametrize("resolved", [False, True], ids=["git stop", "a list run after it ended rc 0"])
def test_a_git_stop_after_a_finished_attempt_lists_the_stop_as_an_item(
    env: dict[str, str], tmp_path: Path, resolved: bool
) -> None:
    """B13 round 18 V31 (rehearsal D, oldr2): after an attempt that has its end line, the v10 build filed the
    git stop's error under "Agent friction (...; it does not change the outcome by itself)" and said "none of
    these changes the outcome by itself" beside "failed at step 1". The renderer says F<n> stopped the run and
    lists it as an item, with one timestamp; the rewrite does the same. A --list-folders run that ended rc 0
    after the error resolves it in the renderer, so that report is left as rendered. Round 19 W24: the
    renderer's note that the stop was logged after that attempt finished is added once."""
    friction = (
        "- agent friction: 0 deviation, 0 prompt, 1 error (F5); none of these changes the outcome by itself"
    )
    item = (
        "- F5 \u00b7 step 1 \u00b7 error \u00b7 prompt v26, x: git pull failed \u2192 a terminal that can "
        "reach GitHub"
    )
    tail = ["", "Agent friction (attempt 1; it does not change the outcome by itself):", "", item]
    prompt = f"- prompt: v{COMPAT}"
    head = [*_GIT_STOP_REPORT[:5], prompt, _GIT_STOP_REPORT[5], friction, _V10_TIME, _GIT_STOP_REPORT[7]]
    report = "\n".join(
        [
            *head,
            "",
            "Items that were not one command (attempt 1):",
            "",
            "- none",
            *tail,
            *_GIT_STOP_REPORT[8:],
        ]
    )
    log = [*_EARLIER_LIST, *(_LATER_LIST if resolved else [])]
    text = _git_stop_report(
        env, tmp_path, "2026-10-18T09:05:00Z | end | finished\n" + _GIT_ERROR, log, report
    )
    summary = text.split("## Summary\n\n", 1)[1].split("\n\n## Doctor", 1)[0]
    if resolved:
        assert friction in summary and "\n".join(tail) in summary and _V10_TIME in summary, summary
        assert "stopped the run" not in summary and "- note: " not in summary
        return
    # B13 round 19 W24: and notes, right after the prompt line, that attempt 1 holds both sessions
    assert summary.splitlines()[1:3] == [
        prompt,
        "- note: the step 1 error (F5) was logged by another session after attempt 1 finished: this session "
        "stopped before `install.sh --log-start`, so this report's attempt 1 holds both sessions.",
    ], summary
    assert summary.splitlines()[4:] == [
        "- agent friction: 0 deviation, 0 prompt, 1 error (F5); F5 stopped the run (the outcome says so)",
        _ONE_STAMP,
        f"- doctor: 0 FAIL, 1 warn (54 checks) {_DOT} expected 1: install.commit ({_STEP1_STOP_WARN}) {_DOT} "
        "unexpected 0",
        "",
        "Items that were not one command (attempt 1):",
        "",
        item,
    ], summary
    assert (
        _git_stop_report(env, tmp_path, "2026-10-18T09:05:00Z | end | finished\n" + _GIT_ERROR, log, text)
        == text
    )


@pytest.mark.parametrize(
    "state",
    [
        "a step 2 line before the end line",
        "a step 1 line after the end line",
        "a step 1 line moved before a bare Attempt: line",
        "an unstated step 1 error past the gap",
    ],
)
def test_a_git_stops_one_timestamp_counts_from_the_attempt_the_renderer_splits(
    env: dict[str, str], tmp_path: Path, state: str
) -> None:
    """B13 round 19 W74: the shim counted the attempt's event lines from its last Attempt: line, while
    parse_friction splits a stated step 1 error after an end line into an attempt of its own, with the run
    of step 1 lines dated after that end line that late_run moves along. An earlier session's step 2 line
    before its end line kept the older build's session span beside an attempt that is the error alone; a
    step 1 line logged after the end line goes with the error, so that attempt has two lines, no one
    timestamp; and a step 1 line moved before a bare Attempt: line is that attempt's too."""
    friction = {
        "a step 2 line before the end line": "2026-10-18T09:02:00Z | step 2 | deviation | slow | -\n"
        "2026-10-18T09:05:00Z | end | finished\n",
        "a step 1 line after the end line": "2026-10-18T09:05:00Z | end | finished\n"
        "2026-10-18T10:00:10Z | step 1 | deviation | retried the pull | -\n",
        "a step 1 line moved before a bare Attempt: line": "2026-10-18T09:05:00Z | end | finished\n"
        "2026-10-18T10:00:10Z | step 1 | deviation | retried the pull | -\nAttempt:\n",
        # a gap split opens an attempt with no header, and the stated error after it stays in it (the review)
        "an unstated step 1 error past the gap": "2026-10-18T09:05:00Z | end | finished\n"
        "2026-10-18T09:40:00Z | step 1 | error | git failed | -\n",
    }[state]
    report = "\n".join([*_GIT_STOP_REPORT[:7], _V10_TIME, *_GIT_STOP_REPORT[7:]])
    summary = _summary(_git_stop_report(env, tmp_path, friction + _GIT_ERROR, _EARLIER_LIST, report))
    if state == "a step 2 line before the end line":
        assert _ONE_STAMP in summary and _V10_TIME not in summary, summary
    else:
        assert _V10_TIME in summary and _ONE_STAMP not in summary, summary


_FIRST_SYNC_RUN = [
    "2026-10-18T08:00:00Z run=20261018T080000Z-40 start install.sh compat=10 commit=4a42af9abcde kind=- "
    "source=- args=--source-local x",
    "2026-10-18T08:00:20Z run=20261018T080000Z-40 step=first-sync seconds=4 rc=0 result=done "
    "note=converted-7-deferred-0",
    "2026-10-18T08:00:30Z run=20261018T080000Z-40 end rc=0 seconds=30",
]
"""rehearsal D's v10 install, whose first sync is install.log's, before the git stop's attempt."""


@pytest.mark.parametrize("state", ["no end line", "after a finished attempt", "no first sync in install.log"])
def test_a_git_stop_of_the_older_build_names_the_earlier_first_sync(
    env: dict[str, str], tmp_path: Path, state: str
) -> None:
    """B13 round 19 W21 (rehearsal D, oldr and oldr2): round 18 V16 gave a list stop the renderer's words for
    install.log's earlier first sync, but a git stop sets no S1_NOTE, so the older build's "no install.sh run
    with a first-sync step (install.log)" stayed beside install.log's first-sync step, with install.out's
    counts untagged. It now names that earlier run, never "ran no install.sh" (the attempt can hold a
    --list-folders run, as its install.sh line says); with no first sync in install.log the line is true and
    stays."""
    runs = "- install.sh: 0 install runs (+1 --list-folders) during this attempt"
    sync = "- first sync: {} \u00b7 converted 7, deferred 0 online-only (install.out{}) \u00b7 2 of 3 sources"
    old = sync.format("no install.sh run with a first-sync step (install.log)", "")
    report = "\n".join([*_GIT_STOP_REPORT[:6], runs, old, *_GIT_STOP_REPORT[6:]])
    log = [*([] if state == "no first sync in install.log" else _FIRST_SYNC_RUN), *_EARLIER_LIST]
    friction = (
        "2026-10-18T09:05:00Z | end | finished\n" if state == "after a finished attempt" else ""
    ) + _GIT_ERROR
    summary = _summary(_git_stop_report(env, tmp_path, friction, log, report))
    if state == "no first sync in install.log":
        assert old in summary, summary
        return
    assert (
        sync.format(
            "this attempt ran no first sync (install.log: the install run of 2026-10-18T08:00:00Z, before "
            "this attempt)",
            ": the install run of 2026-10-18T08:00:00Z, before this attempt",
        )
        in summary
    ), summary
    assert runs in summary and not [ln for ln in summary if "with a first-sync step" in ln], summary
    text = "\n".join(summary)
    assert _git_stop_report(env, tmp_path, friction, log, report).count("before this attempt") == 2, text


_LONG_GIT_WHAT = (
    "git -C ~/src/agent-context-sync pull --ff-only failed (exit 1): fatal: unable to access "
    "'https://github.com/contoso/agent-context-sync.git/': Failed to connect to github.com port 443 after 1 "
    "ms: Couldn't connect to server"
)
"""rehearsal D's git error, longer than the 220 characters the older build cut its Items line to."""


@pytest.mark.parametrize(
    "state",
    [
        "no end line",
        "after a finished attempt",
        "another wording",
        "a what-text holding a bar",
        "a short error",
    ],
)
def test_the_older_builds_cut_git_error_item_takes_the_renderers_cut(
    env: dict[str, str], tmp_path: Path, state: str
) -> None:
    """B13 round 19 W25 (rehearsal D, oldr and oldr2): round 18 V34 gave the renderer's Items line room for
    git's reason (320 characters, the command cut to 80), but the older build cut the line at 220 with its
    "prompt vN, <agent>: " prefix, before the reason, and the rewrite placed that cut line as it was. The
    line is rebuilt from the report's own friction row as setup_report._item_what words it, keeping its fix;
    a row whose what-text holds " | " cannot be split and is left, and so is a line that was not cut."""
    what = _LONG_GIT_WHAT if state != "a short error" else "git pull failed (exit 1): fatal: no route"
    if state == "a what-text holding a bar":
        what = what.replace("Failed to connect", "a | b Failed to connect")
    stated = f"prompt v{COMPAT}, Claude Code test-agent: {what}"
    if state == "another wording":  # setup_report._FAILED_EXIT_RE's and _STEP1_STATED_RE's other forms
        what = what.replace("failed (exit 1):", "exited with exit code 1:")
        stated = f"'Prompt V{COMPAT} - Claude Code test-agent: {what}"
    fix = "a terminal that can reach GitHub (the network, or a proxy setting for git)"
    finished = state == "after a finished attempt"
    n = 5 if finished else 4
    error = f"2026-10-18T10:00:30Z | step 1 | error | {stated} | {fix}"
    rows = ["Attempt: 2026-10-18T09:00:00Z", f"Prompt: v{COMPAT}", "Agent: x"]
    rows += [*(["2026-10-18T09:05:00Z | end | finished"] if finished else []), error]
    old = f"- F{n} \u00b7 step 1 \u00b7 error \u00b7 {setup_report._shorten(stated)} \u2192 {fix}"
    now = f"- F{n} \u00b7 step 1 \u00b7 error \u00b7 {setup_report._item_what(what, 'error')} \u2192 {fix}"
    if finished:  # V31's shape: the older build files it under agent friction
        summary = [
            *_GIT_STOP_REPORT[4:6],
            f"- agent friction: 0 deviation, 0 prompt, 1 error (F{n}); none of these changes the outcome by "
            "itself",
            _V10_TIME,
            _GIT_STOP_REPORT[7],
            "",
            "Items that were not one command (attempt 1):",
            "",
            "- none",
            "",
            "Agent friction (attempt 1; it does not change the outcome by itself):",
            "",
            old,
        ]
    else:
        summary = [*_GIT_STOP_REPORT[4:8], "", "Items that were not one command (attempt 1):", "", old]
    report = "\n".join(
        [
            *_GIT_STOP_REPORT[:4],
            *summary,
            *_GIT_STOP_REPORT[8:],
            "",
            "## Agent friction log",
            "",
            "~~~text",
            *(f"{i:3d}  {row}" for i, row in enumerate(rows, 1)),
            "~~~",
            "",
        ]
    )
    friction = "\n".join(rows[3:]) + "\n"
    text = _git_stop_report(env, tmp_path, friction, _EARLIER_LIST, report)
    items = [ln for ln in _summary(text) + text.split("\n\n") if ln.startswith(f"- F{n} ")]
    if state in ("a what-text holding a bar", "a short error"):
        assert old in text.splitlines(), text
        return
    assert now != old and "Couldn't connect to server" in now
    assert now in text.splitlines() and old not in text.splitlines(), (items, text)
    assert _git_stop_report(env, tmp_path, friction, _EARLIER_LIST, text) == text, "a second pass"


def test_a_stopped_copy_of_an_attempt_that_ran_no_install_names_the_earlier_first_sync(
    env: dict[str, str], tmp_path: Path
) -> None:
    """B13 round 17 U28 (rehearsal B, SC): a stopped copy whose attempt ran no install.sh printed the earlier
    run's first sync as "done in 4s (install.log), converted 7, deferred 0 online-only (install.log)", as if
    this attempt's; the renderer since c774962d names that run "before this attempt; this attempt ran none".
    Only beside the report's own "no run during this attempt" line."""
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    setup.mkdir(parents=True, exist_ok=True)
    (setup / "install.log").write_text(
        "2026-10-18T09:00:10Z run=20261018T090010Z-41 start install.sh compat=24 commit=0123456789ab kind=- "
        "source=- args=--source-local x\n"
        "2026-10-18T09:00:20Z run=20261018T090010Z-41 step=first-sync seconds=4 rc=0 result=done "
        "note=converted-7-deferred-0\n"
        "2026-10-18T09:00:30Z run=20261018T090010Z-41 end rc=0 seconds=20\n",
        encoding="utf-8",
    )
    norun = "- install.sh: no run during this attempt (1 earlier in the install log)"
    sync = f"- first sync: done in 4s (install.log) {_DOT} converted 7, deferred 0 online-only ({{}}) "
    sync += f"{_DOT} 5 of 6 sources"
    head = ["# agentsync setup report", "", "## Summary", ""]
    text = _stopped_copy_of(env, tmp_path, "\n".join([*head, norun, sync.format("install.log"), ""]))
    assert (
        "- first sync: done in 4s (install.log: the install run of 2026-10-18T09:00:10Z, before this "
        "attempt; "
        "this attempt ran none) \u00b7 converted 7, deferred 0 online-only (install.log: that earlier "
        "run) \u00b7 5 of 6 "
        "sources\n" in text
    ), text
    text = _stopped_copy_of(env, tmp_path, "\n".join([*head, norun, sync.format("install.out"), ""]))
    assert (
        "deferred 0 online-only (install.out: an earlier run's; this attempt ran none) \u00b7 5 of 6" in text
    ), text
    text = _stopped_copy_of(env, tmp_path, "\n".join([*head, sync.format("install.log"), ""]))
    assert sync.format("install.log") + "\n" in text, "an attempt that ran install.sh keeps its words"


@pytest.mark.parametrize("state", ["an install run after the list", "no install.commit warn"])
def test_a_report_that_is_no_step_1_stop_of_an_older_build_is_left_as_rendered(
    env: dict[str, str], tmp_path: Path, state: str
) -> None:
    """B13 round 16 T11: the rewrite fires only for this attempt's own step 1 stop rendered by an installed
    build older than the checkout (its install.commit warn says so)."""
    report = _v10_step1_report(_V10_SUMMARY)
    if state == "no install.commit warn":
        report = report.replace(", but the checkout at ", ", the checkout at ")
    last = "install" if state.startswith("an install") else "list"
    assert _step1_stop_home(env, tmp_path, report, last=last) == report


def test_the_shell_stopped_copy_words_are_the_reports() -> None:
    """B13 round 14 Q27: the shim's step words are setup_report._STEP_NOTE_WORDS (less the helpers step,
    which the stopped copy's renderer already words), and its held-listing bracket is doctor's report note."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [block] = re.findall(r'(?ms)^STOPPED_STEP_WORDS="(.*?)" #', script)
    words = {tuple(ln.split(" ", 2)) for ln in block.splitlines()}
    want = {
        (step, token, said)
        for step, notes in setup_report._STEP_NOTE_WORDS.items()
        if step != "helpers"
        for token, said in notes.items()
    }
    assert words == want
    [now] = re.findall(r'(?m)^STOPPED_HELD_NOW="(.*)" #', script)
    assert now == f"({doctor.LISTING_YOURS_REPORT_NOTE})"


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
_NEW_HEAD = "42 checks: 39 ok, 0 info, 3 warn, 0 FAIL. Only the lines that are not ok:"
"""The Doctor header a renderer from B13 round 15 S26 on writes: the noun by the count."""


def _new_note(n: int, stopped: bool = False) -> str:
    """That renderer's expected-warn note, noun, verb and pronoun by ``n`` (B13 round 17 U31: "its fix" for
    one), with K24's words when ``stopped``; a merged "its" reads "their" by the sum."""
    said = " 1 expected warn ends in" if n == 1 else f" {n} expected warns end in"
    tail = setup_report._STOPPED_COPY_BEYOND if stopped else ""
    its = "its" if n == 1 else "their"
    return f"{said} (expected: <why>) instead of {its} fix: nothing for this setup to do{tail}."


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
            _DOCTOR_HEAD + _new_note(1, stopped=True),  # the note the installer adds is in its own words
            [_COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})"],
        ),
        (  # B13 round 15 S26: a renderer that writes the noun by the count, a lone install.commit warn
            "- doctor: 0 FAIL, 1 warn (42 checks) · unexpected 1: install.commit warn",
            _NEW_HEAD,
            [_COMMIT_WARN],
            "- doctor: 0 FAIL, 1 warn (42 checks) · expected 1: install.commit "
            f"({STOPPED_WARN}) · unexpected 0",
            _NEW_HEAD + _new_note(1, stopped=True),
            [_COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})"],
        ),
        (  # B13 round 15 S26: that renderer's "1 expected warn ends in" merged into "2 expected warns end in"
            "- doctor: 0 FAIL, 3 warn (42 checks) · expected 1: launcher.signature (ad hoc launcher) · "
            "unexpected 2: install.commit warn, launchd.poll warn",
            _NEW_HEAD + _new_note(1),
            [
                "[warn] launcher.signature — ad hoc (expected: ad hoc launcher)",
                _COMMIT_WARN,
                "[warn] launchd.poll — x",
            ],
            "- doctor: 0 FAIL, 3 warn (42 checks) · expected 2: launcher.signature (ad hoc launcher); "
            f"install.commit ({STOPPED_WARN}) · unexpected 1: launchd.poll warn",
            _NEW_HEAD + _new_note(2, stopped=True),
            [
                "[warn] launcher.signature — ad hoc (expected: ad hoc launcher)",
                _COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})",
                "[warn] launchd.poll — x",
            ],
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
        (  # B13 round 18 V27: a renderer (e5c5b0e2, dd5d55d5) that marks the warn expected itself, "their"
            "- doctor: 0 FAIL, 1 warn (42 checks) · expected 1: install.commit "
            f"({STOPPED_WARN}) · unexpected 0",
            _NEW_HEAD
            + " 1 expected warn ends in (expected: <why>) instead of their fix: nothing for this setup to do"
            + setup_report._STOPPED_COPY_BEYOND
            + ".",
            [_COMMIT_WARN.split(" (fix: ", maxsplit=1)[0] + f" (expected: {STOPPED_WARN})"],
            None,
            _NEW_HEAD + _new_note(1, stopped=True),
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
    # B13 round 16 T23: the header's counts take their noun and verb, after the merge read the old forms
    want = re.sub(
        r"(\d+) expected warn\(s\) end in",
        lambda m: f"{m[1]} expected warn{' ends' if m[1] == '1' else 's end'} in",
        (want_head or head).replace("42 check(s):", "42 checks:"),
    ).replace(  # B13 round 18 V27: one warn is "its fix", whatever wrote the note
        " 1 expected warn ends in (expected: <why>) instead of their fix",
        " 1 expected warn ends in (expected: <why>) instead of its fix",
    )
    assert doctor[2] == want
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
    version are left. B13 round 11 M27, M52: that clause was a second bracket right before the renderer's
    "(python ...)" one; it opens inside it, joined by "; ", and a line that has it already is left."""
    (Path(env["HOME"]) / ".local" / "bin").mkdir(parents=True)
    shutil.copy(env["STUB_AGENTSYNC"], Path(env["HOME"]) / ".local" / "bin" / "agentsync")
    meta_old = f"- agentsync: 0.1.0, setup prompt v{COMPAT - 1} (python 3.11.9, /usr/bin/python3)"
    meta_same = f"- agentsync: 0.1.0, setup prompt v{COMPAT} (python 3.11.9, /usr/bin/python3)"
    meta_new = (
        f"- agentsync: 0.1.0, setup prompt v{COMPAT - 1} (the installed agentsync, which renders this "
        "report; the checkout's installer, which stopped this copy, is for setup prompt "
        f"v{COMPAT}, and the fresh copy's step 2 reinstalls agentsync; python 3.11.9, /usr/bin/python3)"
    )
    old = tmp_path / "old-report.md"
    old.write_text(
        _old_doctor_report(line, _DOCTOR_HEAD, ["[warn] launchd.poll — x"]).replace(
            "## Redaction", f"### Run metadata\n\n{meta_old}\n{meta_same}\n{line}\n{meta_new}\n\n## Redaction"
        )
    )
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    lines = side.read_text().splitlines()
    meta = lines.index("### Run metadata")
    assert lines[lines.index("## Summary") + 2] == (want or line)
    assert lines[meta + 4] == line, "a prompt line outside the Summary is left as it is"
    assert lines[meta + 2 : meta + 4] == [meta_new, meta_same]
    assert lines[meta + 5] == meta_new, "a line that has the clause already gets it once"


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
    end = [
        "report: ~/agent-context/setup-report.md",
        "bring back: ~/agent-context/bring-back.md (one file: report, fix request, local work)",
        f"issue link (review the report first): {setup_report.ISSUE_URL}&title=Contoso",
        "NEXT: stop: the operator confirms the baseline questions in Contoso Docs; session done",
    ]
    tail = [f"filler {n}" for n in range(56)] + end
    old = tmp_path / "old-report.md"
    side = report_path(env).parent / "setup" / "stopped-copy-report.md"
    gone = "(earlier runs' bring-back and issue link lines are left out of this report)"
    # B13 round 12 N22: the count sits at the end of the renderer's bracket, which says whose output all the
    # lines are, so the bracket is not read as why they were left out; two earlier runs in the tail give 4
    for runs, k in ((tail, 2), ([f"filler {n}" for n in range(52)] + end + end, 4)):
        old.write_text(_old_renderer_report(["- install.sh: exit 0 (finished)"], runs))
        assert install_sh(env, "--log-start", "x").returncode == 2
        assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
        head, fenced = _embed(side)
        found = re.fullmatch(
            r"<details><summary>the last (\d+) lines of \S+ \(.*; (\d+) of them left out: the bring-back "
            r"and issue link lines\)</summary>",
            head,
        )
        assert found is not None, head
        assert (int(found[1]), int(found[2])) == (60, k) and fenced.count(gone) == 1
        # B13 round 13 O70: one marker stands for the lines of every earlier run, so it is number-neutral
        assert "an earlier run's" not in "\n".join(fenced)
        assert int(found[1]) == len(fenced) - 1 + int(found[2]), "the marker is no install.out line"
        assert "an earlier run's" not in head and head.count(" (") == 1, head
    _sends_nothing(side)
    # B13 round 15 S26, S31: a renderer that takes the noun from the count writes "the last 60 lines of"
    old.write_text(
        _old_renderer_report(["- install.sh: exit 0 (finished)"], tail).replace(" line(s) of ", " lines of ")
    )
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    head, fenced = _embed(side)
    assert re.fullmatch(
        r"<details><summary>the last 60 lines of \S+ \(.*; 2 of them left out: the bring-back and issue link "
        r"lines\)</summary>",
        head,
    ), head
    # B13 round 16 T49: a renderer that says "the last line of" for one line gets the note too
    old.write_text(
        _old_renderer_report(["- install.sh: exit 0 (finished)"], end[1:2]).replace(
            " 1 line(s) of ", " line of "
        )
    )
    assert install_sh(env, "--log-start", "x").returncode == 2
    assert install_sh({**env, "STUB_REPORT_FILE": str(old)}, "--report-only").returncode == 0
    head, fenced = _embed(side)
    assert re.fullmatch(
        r"<details><summary>the last line of \S+ \(.*; 1 of them left out: the bring-back and issue link "
        r"lines\)</summary>",
        head,
    ), head
    old.write_text(
        _old_renderer_report(["- install.sh: exit 0 (finished)"], tail).replace(" line(s) of ", " rows of ")
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
    gone = "(earlier runs' bring-back and issue link lines are left out of this report)"
    lines = side.read_text().splitlines()
    assert lines.count(gone) == 1, side.read_text()
    assert lines[lines.index(gone) + 1].startswith("NEXT: stop: "), (
        "the earlier run's NEXT is kept, and the line above it does not say otherwise (B13 round 6 G14)"
    )
    head, fenced = _embed(side)
    found = re.search(
        r"the last (\d+) lines? of \S+ \(.*; (\d+) of them left out: the bring-back and issue ", head
    )
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


@pytest.mark.parametrize("shape", ["stated", "open", "attempt-only", "no-agent"])
def test_report_only_closes_only_the_attempt_the_report_reads_as_latest(
    env: dict[str, str], shape: str
) -> None:
    """B13 round 17 U33: after this session's header, a step 1 error stating "prompt vN, <agent>:" opens
    another session's header-less attempt (a git or Xcode stop before --log-start), yet --report-only closed
    the headed attempt's "Attempt:", so the end line went to the stopped session and the report said
    --report-only had no attempt to close. It closes the attempt parse_friction reads as latest, and only an
    open one: a plain open attempt, an "Attempt:" line alone before a stated error (G51: no header), and a
    stated line with no agent (no "<agent>:", so no new attempt) are each closed once."""
    agent = "Claude Code, claude-opus-5-5"
    header = {
        "stated": f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: {agent}\n",
        "open": f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: {agent}\n",
        "attempt-only": "Attempt: 2026-10-06T16:42:44Z\n",
        "no-agent": f"Attempt: 2026-10-06T16:42:44Z\nPrompt: v{COMPAT}\nAgent: {agent}\n",
    }[shape]
    what = {
        "stated": f"prompt v{COMPAT}, {agent}: git pull failed",
        "open": "git pull failed",
        "attempt-only": f"prompt v{COMPAT}, {agent}: git pull failed",
        "no-agent": f"prompt v{COMPAT}, git pull failed",
    }[shape]
    f = friction_path(env)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"{header}2026-10-06T16:43:11Z | step 1 | error | {what} | -\n", encoding="utf-8")
    before = f.read_text(encoding="utf-8")
    attempts = setup_report.parse_friction(before).attempts
    assert len(attempts) == (2 if shape == "stated" else 1)
    assert install_sh(env, "--report-only").returncode == 0
    after = f.read_text(encoding="utf-8")
    if shape == "stated":
        assert after == before, "the stopped session's attempt is not closed"
        attempts = setup_report.parse_friction(after).attempts
        assert [a.finished for a in attempts] == [False, False]
    else:
        assert after.startswith(before) and after[len(before) :].endswith(" | end | finished\n")
        assert setup_report.parse_friction(after).attempts[-1].finished
    assert install_sh(env, "--report-only").returncode == 0
    assert f.read_text(encoding="utf-8") == after, "a second --report-only adds nothing"


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
    e = {
        **_canary_env(env, folder, "pending"),
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.5",  # it waits the whole time out: a slower poll
    }
    cp = install_sh(e, str(wheel), "--source-local", str(folder), "--confirm-install-agent")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "background sync: running" not in cp.stdout
    assert [ln for ln in cp.stdout.splitlines() if ln.startswith("ACTION:")] == [ACTION]
    assert ("wait", "failed", "3", "tcc") in steps(install_log(env))


def test_canary_lines_of_another_run_do_not_count(env: dict[str, str], folder: Path, wheel: Path) -> None:
    """Lines from another pid, or logged before this install's agent step, are not this run's check."""
    e = {
        **_canary_env(env, folder),
        "STUB_LC_LOG_PID": "999",
        "AGENTSYNC_WAIT_POLL_SECONDS": "0.5",  # it waits the whole time out: a slower poll
    }
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
    after them, still log ``-reread-R``; since B13 round 12 N35 ``-first-F`` follows, the first reads among
    R."""
    line = (
        "converted 3, deferred 0 online-only, read again 2 (2 of them a first read: a stub whose bytes had "
        f"never been read, {said}); of the converted: 1 unreadable, 0 failed, 0 refused"
    )
    cp = install_sh({**env, "STUB_SYNC_OUT": line}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"first sync: {line}" in cp.stdout.splitlines()
    assert ("first-sync", "done", "0", "converted-3-deferred-0-reread-2-first-2") in steps(install_log(env))


def test_first_sync_note_reads_the_count_after_the_first_read_head(
    env: dict[str, str], folder: Path, wheel: Path
) -> None:
    """B13 round 10 L34: when every file read again was a first read, the sync's head is ``, first read R``,
    with no "read again"; the note's awk adds the number after "first read" too, so it still logs
    ``-reread-R`` and the setup report reads the same note; ``-first-R`` follows since B13 round 12 N35."""
    line = (
        "converted 3, deferred 0 online-only, first read 2 (stubs whose bytes had never been read, so also "
        "counted as converted); of the converted: 1 unreadable, 0 failed, 0 refused"  # round 10 L16's words
    )
    cp = install_sh({**env, "STUB_SYNC_OUT": line}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"first sync: {line}" in cp.stdout.splitlines()
    assert ("first-sync", "done", "0", "converted-3-deferred-0-reread-2-first-2") in steps(install_log(env))


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


def _not_built(stub: dict[str, str]) -> str:
    """The helpers step's note for a run whose builds exit by ``stub``'s STUB_<HELPER>_RC: the helpers that
    exited non-zero, as ``not-built-<names>`` (B13 round 18 V6), else none."""
    names = [n for n in ("ocr", "media", "speech") if stub.get(f"STUB_{n.upper()}_RC", "0") != "0"]
    return f"not-built-{'+'.join(names)}" if names else ""


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
            else ("helpers", "done", "0", _not_built(stub))
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
            else ("helpers", "done", "0", _not_built(stub))
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
            else ("helpers", "done", "0", _not_built(stub))
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
    unread = "OCR helper: not checked (the config cannot be read)"  # B13 round 11 M8
    cp = install_sh(
        {**env, "STUB_OCR_OUT": unread, "STUB_OCR_RC": "78"}, str(wheel), "--source-local", str(folder)
    )
    assert _helpers_row(steps(install_log(env))) == ("helpers", "skipped", "0", "config-unreadable")
    out = cp.stdout.splitlines()
    at = out.index(unread)
    assert out[at + 1 : at + 3] == [
        "media helper: not checked (the config cannot be read)",
        "speech: not checked (the config cannot be read)",
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
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path), **xcrun_db()},
    )
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == f"MDM server: https://<mdmhost>:443{path}\n"


# ---- the shell report redacts a vanished source's id (T42) -------------------------------------------------

SHELL_HELPER_FNS = re.compile(
    r"(?ms)^(?:generic_folder|generic_home_dir|agent_words_name|id_key|listed_folder_lines"
    r"|config_path_values)\(\) \{\n.*?^\}$"
)


def test_the_shell_report_redacts_the_id_of_a_source_whose_block_is_gone(tmp_path: Path) -> None:
    """B13 round 16 T42: doctor's config.vanished_sources [FAIL] and the sync's refusal quote the id of a
    source whose [[source]] block is gone, which no table of sources.toml names, so the shell fallback report
    showed it in clear. The ids those lines quote are now pairs, numbered after the tables, the older
    ``file(s)`` wording too; a configured id keeps its own number and one of agentsync's own words is kept."""
    text = INSTALL_SH.read_text()
    funcs = "\n".join(m.group(0) for m in SHELL_REDACTION_FNS.finditer(text))
    helpers = "\n".join(m.group(0) for m in SHELL_HELPER_FNS.finditer(text))
    generic = "\n".join(ln for ln in text.splitlines() if ln.startswith("GENERIC_SOURCE_IDS="))
    home = tmp_path / "home"
    (home / "notes").mkdir(parents=True)
    config = tmp_path / "sources.toml"
    config.write_text(f'[[source]]\nid = "contoso-alpha"\nkind = "local"\npath = "{home / "notes"}"\n')
    doctor_log = tmp_path / "doctor.log"
    doctor_log.write_text(
        "[FAIL] config.vanished_sources — sources 'zeta-ledger' (3 files), 'mail' (1 file) are mirrored but "
        "no longer in sources.toml, so every sync refuses this config (exit 78) (fix: put the [[source]] "
        "block back)\n"
    )
    out = tmp_path / "install.out"
    out.write_text(
        "configuration error: source(s) 'omega-plans' (1 file(s)) are mirrored but no longer in "
        "sources.toml\n"
    )
    said = (
        "[FAIL] config.vanished_sources — sources 'zeta-ledger' (3 files), 'mail' (1 file) are mirrored\n"
        "configuration error: source(s) 'omega-plans' (1 file(s)) and 'contoso-alpha' (2 files)\n"
    )
    lines = tmp_path / "lines.txt"
    lines.write_text(said)
    harness = "\n".join(
        [
            "with_timeout() { return 0; }",
            "repo_slug() { echo contoso/agentsync; }",
            'id() { case "$1" in -un) echo jdoe ;; -F) echo "Jane Doe" ;; esac; }',
            generic,
            f"FOLDERS=() CONFIG={config} SETUP_LOG=/nonexistent DOCTOR_LOG={doctor_log} INSTALL_OUT={out}",
            helpers,
            funcs,
            f"redact_stream <{lines}",
        ]
    )
    cp = subprocess.run(
        [BASH32, "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path), **xcrun_db()},
    )
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout == (
        "[FAIL] config.vanished_sources — sources '<source-3>' (3 files), 'mail' (1 file) are mirrored\n"
        "configuration error: source(s) '<source-2>' (1 file(s)) and '<source-1>' (2 files)\n"
    ), cp.stdout + cp.stderr


@pytest.mark.parametrize("form", ["install", "cli", "round17"])
def test_the_shell_report_redacts_a_project_folder_and_a_vanished_sources_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    """B13 round 18 V46: doctor's config.vanished_sources fix names the folder add-source adds back (round 17
    U46), and the shell fallback registered only the id, so the folder printed in clear; a configured folder
    outside CloudStorage did too. Both are registered as setup_report._project_values does (the path from
    its first part that names something, and that part, in any case, whole words), a vanished cloud folder by
    its parts; a container such as "Projects" and agentsync's own inbox stay in clear.

    Round 19 W34: the fix and the sync's refusal end with cycle.vanished_add_back_tail, which writes
    ``install.sh --source-local "<folder>"`` under install.sh (``AGENTSYNC_NO_NEXT_HINT=1``, which install.sh
    exports for the status run and the sync) and ``add-source "<folder>"`` elsewhere, double-quoted with its
    escapes, single-quoted for a name holding "!"; the parser read only round 17's single-quoted add-source,
    so the folder printed in clear. Each line is built by the writer itself; "round17" keeps the older
    form."""
    text = INSTALL_SH.read_text()
    funcs = "\n".join(m.group(0) for m in SHELL_REDACTION_FNS.finditer(text))
    helpers = "\n".join(m.group(0) for m in SHELL_HELPER_FNS.finditer(text))
    home = tmp_path / "home"
    live = home / "Projects" / "Contoso Live"
    live.mkdir(parents=True)
    config = tmp_path / "sources.toml"
    config.write_text(
        f'[[source]]\nid = "contoso-live"\nkind = "local"\npath = "~/Projects/Contoso Live"\n'
        f'[[source]]\nid = "inbox"\nkind = "local"\npath = "{home / "agent-context" / "inbox"}"\n'
    )
    bids = home / "Projects" / "Contoso Bids" / "Q3"
    cloud = home / "Library" / "CloudStorage" / "OneDrive-Fabrikam" / "Launch Plans"
    # a name holding " $ and ' (the escapes), and one holding ! (shlex.quote's single quotes)
    odd = home / "Projects" / 'Delta "Q4" $plan\'s'
    bang = home / "Projects" / "Wow! Bids"
    if form == "round17":
        fix = (
            "put the [[source]] block back; or add their folders again, under the same id: "
            f"~/.local/bin/agentsync add-source {shlex.quote(str(bids))}, then "
            f"~/.local/bin/agentsync add-source {shlex.quote(str(cloud))}"
        )
        tail = (
            "; or add their folders again, under the same id: ~/.local/bin/agentsync add-source "
            f"{shlex.quote(str(odd))}, then ~/.local/bin/agentsync add-source {shlex.quote(str(bang))}"
        )
    else:
        if form == "install":
            monkeypatch.setenv("AGENTSYNC_NO_NEXT_HINT", "1")
        else:
            monkeypatch.delenv("AGENTSYNC_NO_NEXT_HINT", raising=False)
        fix = doctor.vanished_fix([bids, cloud], every=True)
        tail = cycle_vanished_add_back_tail([odd, bang], every=True)
        assert (" --source-local " if form == "install" else " add-source ") in fix + tail
    doctor_line = (
        "[FAIL] config.vanished_sources \u2014 sources 'bids-q3' (3 files), 'launch-plans' (1 file) are "
        f"mirrored but no longer in sources.toml (fix: {fix})"
    )
    doctor_log = tmp_path / "doctor.log"
    doctor_log.write_text(doctor_line + "\n")
    detail = cycle_vanished_detail({"delta-q4": 1, "wow-bids": 2})
    out_line = f"configuration error: {detail}; {VANISHED_ADVICE}{tail}"
    out = tmp_path / "install.out"
    out.write_text(out_line + "\n")
    said = tmp_path / "lines.txt"
    said.write_text(
        f"add-source '{bids}' and '{cloud}'\n"
        "configured: ~/Projects/Contoso Live; contoso live; CONTOSO BIDS/Q3; launch plans\n"
        "Projects, Contoso and the inbox stay: ~/agent-context/inbox\n"
        f"{odd.name} | {bang.name}\n" + doctor_line + "\n" + out_line + "\n"
    )
    harness = "\n".join(
        [
            "with_timeout() { return 0; }",
            "repo_slug() { echo contoso/agentsync; }",
            'id() { case "$1" in -un) echo jdoe ;; -F) echo "Jane Doe" ;; esac; }',
            *(ln for ln in text.splitlines() if ln.startswith(("GENERIC_SOURCE_IDS=", "GOVERNANCE_TYPED"))),
            f"FOLDERS=() CONFIG={config} SETUP_LOG=/nonexistent DOCTOR_LOG={doctor_log}",
            f"INSTALL_OUT={out}",
            f"SELF_FILE={INSTALL_SH}",
            helpers,
            *(
                m.group(0)
                for m in re.finditer(r"(?ms)^(?:gov_value_sed|report_word|joined_word)\(\) \{\n.*?^\}$", text)
            ),
            funcs,
            f"redact_stream <{said}",
        ]
    )
    cp = subprocess.run(
        [BASH32, "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path), **xcrun_db()},
    )
    assert cp.returncode == 0, cp.stderr
    # numbered as the Python report numbers them: the configured one, then the vanished roots in byte order
    shown = cp.stdout.splitlines()
    assert shown[:4] == [
        "add-source '~/Projects/<folder-3>' and '~/Library/CloudStorage/OneDrive-Fabrikam/<folder-2>'",
        "configured: ~/Projects/<folder-1>; <folder-1>; <folder-3>; <folder-2>",
        "Projects, Contoso and the inbox stay: ~/agent-context/inbox",
        "<folder-5> | <folder-6>",
    ], cp.stdout + cp.stderr
    # the writer's own lines: every folder registered, in each quoting, so none shows
    assert "~/Projects/<folder-3>" in shown[4] and "/OneDrive-Fabrikam/<folder-2>" in shown[4], shown[4]
    assert "~/Projects/<folder-5>" in shown[5] and "~/Projects/<folder-6>" in shown[5], shown[5]
    for clear in ("Bids", "Launch", "Plans", "Delta", "Q4", "plan", "Wow"):
        assert clear not in "\n".join(shown[4:]), cp.stdout
    # docs_repo is read from its [agentsync] table: an inbox beside a hand-set docs repo is agentsync's own
    (home / "Documents" / "inbox").mkdir(parents=True)
    config.write_text(
        '[agentsync]\ndocs_repo = "~/Documents/agent-docs"\n\n'
        f'[[source]]\nid = "inbox"\nkind = "local"\npath = "{home / "Documents" / "inbox"}"\n'
    )
    doctor_log.write_text("")
    said.write_text("the inbox stays: ~/Documents/inbox\n")
    cp = subprocess.run(
        [BASH32, "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path), **xcrun_db()},
    )
    assert cp.stdout == "the inbox stays: ~/Documents/inbox\n", cp.stdout + cp.stderr


def test_the_two_reports_number_a_vanished_sources_re_add_folders_in_one_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 19 W73: the shell report numbers the re-add folders doctor's fix names in byte order
    (``LC_ALL=C sort -u``), and the Python report numbered the manifest's roots in Path order, which puts
    "Contoso/Q1" before "Contoso Bids/Q3" and "a/b" before "a-b": the two reports swapped <folder-N>. Python
    now sorts the strings, which is byte order, so each root gets the same number in both."""
    text = INSTALL_SH.read_text()
    funcs = "\n".join(m.group(0) for m in SHELL_REDACTION_FNS.finditer(text))
    helpers = "\n".join(m.group(0) for m in SHELL_HELPER_FNS.finditer(text))
    home = Path.home()
    (home / "notes").mkdir(parents=True, exist_ok=True)
    cfg = home / "agent-context" / "sources.toml"
    assert cli.main(["add-source", str(home / "notes"), "--config", str(cfg)]) == 0
    rels = ["Tabs/a/b", "Contoso Bids/Q3", "Tabs/a-b", "Contoso/Q1"]
    roots = {f"gone-{i}": home / "Projects" / rel for i, rel in enumerate(rels)}
    db = paths.expand(load_config(cfg).state_paths.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    with contextlib.closing(conn), conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for sid, root in roots.items():
            conn.execute("INSERT INTO meta VALUES (?, ?)", (f"scope_root:{sid}", f"3:{root}"))
    python_order = setup_report._unconfigured_roots(load_config(cfg), list(roots))
    assert python_order == sorted(roots.values(), key=str)
    assert python_order != sorted(roots.values())  # the case Path order numbered differently
    monkeypatch.setenv("AGENTSYNC_NO_NEXT_HINT", "1")
    doctor_log = tmp_path / "doctor.log"
    doctor_log.write_text(
        "[FAIL] config.vanished_sources \u2014 sources 'gone-0' (1 file), 'gone-1' (1 file) are mirrored but "
        f"no longer in sources.toml (fix: {doctor.vanished_fix(list(roots.values()), every=True)})\n"
    )
    said = tmp_path / "lines.txt"
    said.write_text("".join(f"{root.relative_to(home / 'Projects')}\n" for root in python_order))
    harness = "\n".join(
        [
            "with_timeout() { return 0; }",
            "repo_slug() { echo contoso/agentsync; }",
            'id() { case "$1" in -un) echo jdoe ;; -F) echo "Jane Doe" ;; esac; }',
            *(ln for ln in text.splitlines() if ln.startswith(("GENERIC_SOURCE_IDS=", "GOVERNANCE_TYPED"))),
            f"FOLDERS=() CONFIG={cfg} SETUP_LOG=/nonexistent DOCTOR_LOG={doctor_log}",
            "INSTALL_OUT=/nonexistent",
            f"SELF_FILE={INSTALL_SH}",
            helpers,
            *(
                m.group(0)
                for m in re.finditer(r"(?ms)^(?:gov_value_sed|report_word|joined_word)\(\) \{\n.*?^\}$", text)
            ),
            funcs,
            f"redact_stream <{said}",
        ]
    )
    cp = subprocess.run(
        [BASH32, "-c", harness],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path), **xcrun_db()},
    )
    assert cp.returncode == 0, cp.stderr
    numbers = [int(m) for m in re.findall(r"^<folder-(\d+)>$", cp.stdout, re.M)]
    # each root's whole path is one placeholder, numbered up in the Python report's order
    assert len(numbers) == len(rels) and numbers == sorted(numbers), cp.stdout + cp.stderr


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
        "2026-10-10T00:00:01Z | step 1 | error | git clone "
        "https://mirror.northwind.invalid:8443/northwind-team/"
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


def test_the_shell_report_reads_a_url_rewrite_written_on_its_header_line(env: dict[str, str]) -> None:
    """B13 round 20 X8: git applies ``[url "<base>"] insteadOf = <url>`` written on one line, and
    git_rewrite_bases ran ``next`` on the header line, so the report said "not set" beside a git error that
    named the rewrite's host and path in clear. The rest of the header line is read as a key line, cut after
    the matched header (a base holding "]" keeps it); a one-line pushInsteadOf is still no rewrite."""
    home = Path(env["HOME"])
    (home / ".gitconfig").write_text(
        '[url "https://proxy.contoso.invalid/fabrikam-zeta/mirror/"] insteadOf = https://github.com/\n'
        '[url "https://push.contoso-two.invalid/fabrikam-push/"] pushInsteadOf = https://github.com/\n'
        '[url "https://[fd00::1]/fabrikam-six/"]insteadOf=https://github.com/v6/\n'
    )
    said = (
        "git clone https://proxy.contoso.invalid/fabrikam-zeta/mirror/someone/agent-context-sync.git/: "
        "Could not resolve host: proxy.contoso.invalid"
    )
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "fabrikam-zeta" not in text and "proxy.contoso.invalid" not in text, text
    assert (
        "git clone https://<proxy-1>/<proxy-2>/someone/agent-context-sync.git/: Could not resolve host: "
        "<proxy-1> |" in text
    ), text
    assert "- URL rewrite in git's config: set (2)\n" in text, text


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
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    folders: list[str],
    said: str,
    org: str = "Contoso",
    *,
    links: Sequence[str] = (),
    others: Sequence[str] = (),
    wrap: Sequence[str] = (),
) -> tuple[str, str]:
    """The friction line ``said`` as the shell report and the Python report redact it, with ``folders``
    configured under OneDrive-<org> (B13 round 10 L66), and in CloudStorage ``links``, symlinks to it, and
    ``others``, folders of their own; install.sh runs under ``wrap`` (:func:`install_sh`), the Python report
    in this process."""
    lib = _cloud(env) / f"OneDrive-{org}"
    lib.mkdir(parents=True, exist_ok=True)
    for name in links:
        (_cloud(env) / name).symlink_to(lib, target_is_directory=True)
    for name in others:
        (_cloud(env) / name).mkdir()
    tables = []
    for n, name in enumerate(folders):
        (lib / name).mkdir(parents=True)
        tables.append(local_source_table(f"s{n}", lib / name))
    cfg = _write_config(env, *tables)
    line = f"2026-10-10T00:00:01Z | step 1 | deviation | saw {said} | -"
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    friction_path(env).write_text(f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\n{line}\n")
    assert install_sh(env, "--report-only", wrap=wrap).returncode == 0
    [shell] = [ln for ln in report_path(env).read_text().splitlines() if " | step 1 | deviation | saw " in ln]
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(cfg, setup_report.ReportHooks(), 12.0))
    return shell.split(" | saw ", 1)[1].removesuffix(" | -"), red.redact(line).split(" | saw ", 1)[
        1
    ].removesuffix(" | -")


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("Zephyrine\tQuarry", "opened Zephyrine\tQuarry and Zephyrine Quarry"),
        ("Q3\tPlans", "opened Q3\tPlans and Q3 Plans"),
        ("Q3\\tPlans", "opened Q3\\tPlans and Q3 Plans"),
    ],
)
def test_a_folder_name_holding_a_tab_is_redacted_alike_in_both_reports(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, name: str, said: str
) -> None:
    """B13 round 20 X75: add-source writes a configured path with json.dumps, so a TAB in a folder's name is
    the two characters ``\\t`` in sources.toml, and redaction_pairs read the value raw: the fuzzy match took
    the backslash as a separator, the tokens were "Zephyrine" and "tQuarry", and both spellings printed in
    clear beside the public issue link. config_path_values reads the value as a TOML basic string, left to
    right (an escaped backslash before a "t" stays one), a whitespace escape as one space."""
    shell, python = _fuzzy_report(env, monkeypatch, [name], said)
    assert shell == python, (shell, python)
    if "\t" in name:
        assert shell == "opened <folder-1> and <folder-1>", shell
    else:  # a backslash and a t: the name is redacted, the other spelling is another name
        assert shell.startswith("opened <folder-1> and "), shell
    report = report_path(env).read_text()
    assert name not in report and ("\t" not in name or name.replace("\t", " ") not in report), report


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


@pytest.mark.parametrize(
    ("org", "said", "want"),
    [
        (
            "Contoso",
            "ContosoGroup, Contoso2026, ContosoCorp.com, AcmeContoso, Contosoft, contosogroup, CONTOSOX",
            "<org-1>Group, <org-1>2026, <org-1>Corp.com, Acme<org-1>, Contosoft, contosogroup, CONTOSOX",
        ),
        # an organisation that begins or ends one of agentsync's own identifiers leaves that one whole
        ("Storage", "CloudStorage, StorageGroup, AcmeStorage", "CloudStorage, <org-1>Group, Acme<org-1>"),
        # one that is such an identifier is still cut where the word beside it completes none (reviewer)
        (
            "Box",
            "xBox, dropBox, 2Box, DropBox, BoxGroup",
            "x<org-1>, drop<org-1>, 2<org-1>, DropBox, <org-1>Group",
        ),
        # the report's own CamelCase words stay whole: the speech line's engine and the LaunchAgent (B13
        # round 12 N57: "<org-1>Audio" and "<org-1>Agents" decoded the placeholder)
        ("Fluid", "FluidAudio 04e363c, FluidGroup", "FluidAudio 04e363c, <org-1>Group"),
        ("Audio", "FluidAudio, AcmeAudio", "FluidAudio, Acme<org-1>"),
        ("Launch", "LaunchAgent, LaunchAgents, LaunchGroup", "LaunchAgent, LaunchAgents, <org-1>Group"),
        # and the plist keys the Background runs line prints (B13 round 13 O54, O68)
        (
            "Standard",
            "StandardOutPath, StandardErrorPath, StandardGroup",
            "StandardOutPath, StandardErrorPath, <org-1>Group",
        ),
        (
            "Interval",
            "StartInterval 900 s, ThrottleInterval, StartCalendarInterval, AcmeInterval",
            "StartInterval 900 s, ThrottleInterval, StartCalendarInterval, Acme<org-1>",
        ),
        # an install.sh code word ("target=", a variable) is no report word: cut out as in Python (B13 round
        # 17 U4)
        ("Target", "MyTargetTeam, Target2026, AcmeTarget", "My<org-1>Team, <org-1>2026, Acme<org-1>"),
        # a report word too, a Python-only one (Gateway) or one both print (Status): cut out alike, the report
        # then private only (B13 round 18 V1)
        ("Gateway", "MyGatewayTeam, Gateway2026, AcmeGateway", "My<org-1>Team, <org-1>2026, Acme<org-1>"),
        ("Status", "MyStatusTeam, Status2026, AcmeStatus", "My<org-1>Team, <org-1>2026, Acme<org-1>"),
    ],
)
def test_an_organisation_is_redacted_inside_a_longer_word(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, org: str, said: str, want: str
) -> None:
    """B13 round 11 M50: since round 10 L66 both readers matched an organisation as a whole word only, so
    "ContosoGroup", "Contoso2026", "ContosoCorp.com" and "AcmeContoso" printed it in clear. An organisation
    matched everywhere is also cut out at a CamelCase, digit or leading-capital boundary, never inside a word
    ("Contosoft") and never out of OneDrive, GoogleDrive, CloudStorage and the like."""
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said, org=org)
    assert shell == want and python == want, (shell, python)


@pytest.mark.parametrize("org", ["Target", "Core", "Ghostty", "Echo", "Gap", "Index", "Token", "Contoso"])
def test_an_organisation_named_like_an_install_sh_code_word_is_redacted_as_in_python(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, org: str
) -> None:
    """B13 round 17 U94 (U4 one defect): report_word read every lower-case word on every line of install.sh,
    code too (``target=``, ``local ... core``, the ``ghostty)`` case label), so an organisation named so
    stayed in clear inside longer words in the shell report, and made it private only and dropped its link,
    where the Python report, whose words are its string literals, cut it out. Contoso is the control. B13
    round 18 V85: Echo, Gap, Index and Token came in from the text between two strings when a string nested
    in $( ... ) put the quote pairing out of step, and from an awk comment."""
    said = f"{org}Group, {org}2026, Acme{org}, {org}Corp.com, {org.lower()} ran"
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said, org=org)
    assert shell == python == "<org-1>Group, <org-1>2026, Acme<org-1>, <org-1>Corp.com, <org-1> ran", (
        shell,
        python,
    )
    text = report_path(env).read_text(encoding="utf-8")
    assert "Private only:" not in text and "issues/new" in text, text[-800:]


def _shell_report_words() -> set[str]:
    """The shell's report words: report_word's scan of install.sh."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    fns = "".join(re.findall(r"(?ms)^(?:joined_word|report_word)\(\) \{\n.*?^\}\n", script))
    cp = subprocess.run(
        [
            BASH32,
            "-c",
            fns + 'SELF_FILE="$1"; REPORT_WORDS=""; report_word zzzz; printf "%s\\n" $REPORT_WORDS',
            "-",
            str(INSTALL_SH),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    return set(cp.stdout.split())


def _shell_case_words(name: str) -> set[str]:
    """The words of install.sh's case-list function ``name`` (joined_word, output_word)."""
    [body] = re.findall(rf"(?ms)^{name}\(\) \{{\n(.*?)^\}}$", INSTALL_SH.read_text(encoding="utf-8"))
    return {w.strip() for ln in re.findall(r"(?m)^\t(\S.*?)\) return 0 ;;$", body) for w in ln.split("|")}


def test_the_shell_report_words_are_its_printed_text() -> None:
    """B13 round 17 U94: the shell's report words are the words install.sh prints (double-quoted strings,
    their expansions removed, and printf or say formats), never a code word, a case label or a pattern
    fragment; every word joined_word holds is one. B13 round 18 V85: each line is lexed, so a string nested
    in $( ... ) does not pair with the quotes around it (echo, dev, null, gap, index) and an awk comment is
    no printed text (token); a single-quoted program's own strings and a sed replacement are (designated
    requirement:, printed only on the launcher path)."""
    words = _shell_report_words()
    assert len(words) > 500, "the scan is broken"
    for code in (
        "target",
        "core",
        "groups",
        "machine",
        "vscode",
        "ghostty",
        "ersonal",
        "usiness",
        "ork",
        "box",
        "echo",
        "dev",
        "null",
        "gap",
        "index",
        "token",
    ):
        assert code not in words, code
    for printed in (
        "folder",
        "report",
        "redacted",
        "private",
        "organization",
        "installer",
        "designated",
        "requirement",
        "builtin",  # the default word of "${v:-not found (builtin patterns only)}", where gitleaks is missing
        "patterns",
    ):
        assert printed in words, printed


def test_the_shell_output_words_are_the_reports() -> None:
    """B13 round 18 V84, V86: output_word lists the words the report prints from a command's or the agent's
    output, setup_report._OUTPUT_WORDS (lane RA's half)."""
    words = _shell_case_words("output_word")
    assert {"arm", "enrolled", "via", "dep", "enrollment", "env", "info", "resolves", "claude"} <= words
    assert words == set(setup_report._OUTPUT_WORDS)


@pytest.mark.parametrize("org", ["Arm", "Via"])
def test_an_organisation_named_like_a_commands_output_word_makes_the_shell_report_private(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, org: str
) -> None:
    """B13 round 18 V84, V86: the shell report prints uname -m's "arm64" and profiles' "Enrolled via DEP",
    which no scan of install.sh's printed text sees, so an organisation Arm cut "arm64" to "<org-1>64" and the
    report kept the public issue link, from which a reader turns it back into the name. Such a word makes the
    report private only (output_word), as the Python report does; how the value is matched is unchanged."""
    _fuzzy_report(env, monkeypatch, ["Plans"], f"{org}Group and {org.lower()}", org=org)
    text = report_path(env).read_text(encoding="utf-8")
    assert "Private only:" in text and "issues/new" not in text, text[-800:]


def test_an_organisation_that_is_a_report_word_is_cut_out_of_a_longer_word_in_the_shell_report(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V1 (the shell half; lane RA builds the Python one): an organisation named like a word the
    report prints (Status) was matched whole-word only, so "MyStatusTeam" printed it in clear. It is cut out
    of a longer word like any organisation, and the report is private only."""
    shell, python = _fuzzy_report(
        env, monkeypatch, ["Plans"], "MyStatusTeam, Status2026, AcmeStatus", org="Status"
    )
    assert shell == python == "My<org-1>Team, <org-1>2026, Acme<org-1>", (shell, python)
    text = report_path(env).read_text(encoding="utf-8")
    assert "Private only:" in text and "issues/new" not in text, text[-800:]


def test_every_lower_case_word_the_shell_report_prints_is_a_report_word(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V84: a word the report prints that its word list lacks is a word an organisation can be
    named after and be read back from. Every lower-case word of 3 or more letters the shell report prints
    outside the embedded logs, paths, URLs, placeholders and code spans is report_word's, output_word's or
    joined_word's: a new uname, profiles or doctor wording fails here instead of leaking."""
    _fuzzy_report(env, monkeypatch, ["Plans"], "nothing", org="Contoso")
    assert _unknown_report_words(report_path(env).read_text(encoding="utf-8")) == {}


def _unknown_report_words(text: str) -> dict[str, str]:
    """The lower-case words of 3 or more letters a shell report prints outside its embedded logs, paths, URLs,
    placeholders and code spans that no word list of install.sh holds, each with a line that prints it."""
    known = _shell_report_words() | _shell_case_words("output_word") | _shell_case_words("joined_word")
    missing: dict[str, str] = {}
    section, fence = "", False
    for line in text.splitlines():
        if line.startswith("~~~"):
            fence = not fence
        if fence or line.startswith("~~~"):
            continue
        section = line if line.startswith("## ") else section
        if section == "## Agent friction log":
            continue
        # and a commit or tree id (hex with a digit)
        bare = re.sub(
            r"https?://\S+|\S*[/~]\S*|<[^<>]+>|`[^`]*`|\S+\.\S+|\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b", " ", line
        )
        for word in re.findall(r"(?<![A-Za-z])[a-z]{3,}(?![A-Za-z])", bare):
            if word not in known:
                missing.setdefault(word, line)
    return missing


def test_the_shell_report_words_hold_where_gitleaks_is_missing(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 19 W78: a Mac with gitleaks always renders the "found" branch of the report's gitleaks line,
    so the census above passed here while CI, which has none, printed "not found (builtin patterns only)" and
    went red (round 18). install.sh alone runs under sandbox-exec with every place it looks for gitleaks
    unreadable, which reads as missing to its `[ -x ]`; the run must take CI's branch, and the census holds
    there too. Only install.sh needs the sandbox, since the census reads only the shell's words, while the
    Python report's words are its string literals. Since W66 the in-process lookup also reads a denied path
    as missing (lints._executable), so wrapping it would add nothing (B13 round 20 X76)."""
    sandbox = shutil.which("sandbox-exec")
    if sandbox is None:
        pytest.skip("no sandbox-exec: not macOS")
    if subprocess.run([sandbox, "-p", "(version 1)(allow default)", "/usr/bin/true"], check=False).returncode:
        pytest.skip("sandbox-exec cannot start here (already inside a sandbox)")
    hidden = {str(p) for p in lints._GITLEAKS_CANDIDATES if p.is_absolute()}
    found = shutil.which("gitleaks", path=env.get("PATH"))
    hidden |= {found, os.path.realpath(found)} if found else set()
    profile = "(version 1)(allow default)" + "".join(
        f'(deny file-read* (literal "{p}"))' for p in sorted(hidden)
    )
    _fuzzy_report(env, monkeypatch, ["Plans"], "nothing", org="Contoso", wrap=[sandbox, "-p", profile])
    text = report_path(env).read_text(encoding="utf-8")
    assert "- gitleaks: not found (builtin patterns only)" in text.splitlines(), "CI's branch, or no test"
    assert _unknown_report_words(text) == {}


def test_a_cloudstorage_symlink_is_a_link_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U61 (round 16 T40's shell twin): the shell's organisation loop followed a symlink, so
    OneDrive-Aardvark, a link sorting first, took <org-1> and the real OneDrive-Contoso <org-2>, where the
    Python report has <org-1> and a whole-name <link-1>; the shell legend had no <link-N> item."""
    said = "Contoso and OneDrive-Aardvark and onedrive-aardvark"
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said, links=["OneDrive-Aardvark"])
    assert shell == python == "<org-1> and <link-1> and <link-1>", (shell, python)
    text = report_path(env).read_text(encoding="utf-8")
    assert "<org-2>" not in text and "Aardvark" not in text, text
    assert "- <link-N>: a symlink's own name in ~/Library/CloudStorage (not an organization)\n" in text


def test_the_onedrive_organisation_is_org_1_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U61: the shell walked CloudStorage in plain name order, so Box-Fabrikam took <org-1>
    before OneDrive-Contoso; the Python report puts OneDrive's entries first."""
    shell, python = _fuzzy_report(
        env, monkeypatch, ["Plans"], "Contoso and Fabrikam", others=["Box-Fabrikam"]
    )
    assert shell == python == "<org-1> and <org-2>", (shell, python)


def test_a_proxy_host_an_earlier_line_names_is_redacted_once_unset(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 11 M29: both readers redacted only the proxies in force when the report ran, so after the
    person unset a stale proxy (the git stop asks them to) the friction line git's error left printed its
    host. A host a resolver message names is redacted in both reports, with one number; the public hosts the
    installer reaches stay readable."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    said = (
        "fatal: unable to access the repository: Could not resolve proxy: WebGW.nwtrd-lan.invalid; Could not "
        "resolve host: github.com; Failed to connect to astral.sh port 443; Failed to connect to "
        "relay.fabrikam-lan.invalid port 3128 after 9 ms"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said)
    assert shell == python, (shell, python)
    assert "webgw" not in shell and "fabrikam" not in shell, shell
    assert "Could not resolve proxy: <proxy-" in shell and "Failed to connect to <proxy-" in shell, shell
    assert (
        "Could not resolve host: github.com" in shell and "Failed to connect to astral.sh port 443" in shell
    )


def test_a_git_remotes_host_is_redacted_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 16 T88 (S6's code half): a hand-set origin's host, which a 403 or a TLS error names and v24's
    step 1 tells the agent to log, was printed in clear by both readers: none read a remote's URL. Both
    register it as a <proxy-N>, last. A local remote (``../x``, a bare word, an absolute path, ``file://``)
    is no host, and GitHub's hosts stay readable. The hosts carry no fixture organization name, so the test
    is not passed by the org match."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    git = Path(env["HOME"]) / "src" / "agent-context-sync" / ".git"
    git.mkdir(parents=True)
    remotes = {
        "origin": "https://git.fabrikam-eng.invalid/tools/agent-context-sync.git",
        "mirror": "deploy@scm.northwind-lab.invalid:tools/agent-context-sync.git",
        "up": "../agent-context-sync",
        "word": "agent-context-sync",
        "abs": "/srv/agent-context-sync.git",
        "file": "file:///srv/agent-context-sync.git",
        "gh": "git@github.com:o/r.git",
        "ssh": "ssh://git@ssh.github.com:443/o/r.git",
    }
    (git / "config").write_text(
        "[core]\n\tbare = false\n"
        + "".join(
            f'[remote "{name}"]\n\t{"URL" if name == "mirror" else "url"} = {url}\n'
            for name, url in remotes.items()
        )
    )
    said = (
        "git pull: fatal: unable to access 'https://git.fabrikam-eng.invalid/tools/agent-context-sync.git/': "
        "The requested URL returned error: 403; SSL: no alternative certificate subject name matches target "
        "host name 'scm.northwind-lab.invalid'; cd ../agent-context-sync; ssh.github.com port 443"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said)
    assert shell == python, (shell, python)
    assert "fabrikam" not in shell and "northwind" not in shell, shell
    assert "'https://<proxy-1>/tools/agent-context-sync.git/'" in shell and "'<proxy-2>'" in shell, shell
    assert "cd ../agent-context-sync; ssh.github.com port 443" in shell, "no local remote or GitHub host"
    assert [h for h, _url in setup_report._git_remotes()] == [
        "git.fabrikam-eng.invalid",
        "scm.northwind-lab.invalid",
    ]


def test_a_git_remotes_host_ends_at_a_query_or_fragment_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 18 V88: Python registered ``git.zeta-corp.example?x`` for a remote ``https://...?x`` (its
    rewrite value cut at ``/`` only), so git's ``Could not resolve host: git.zeta-corp.example`` stayed in
    clear while the shell, which cuts at ``[/?#]``, redacted it."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    git = Path(env["HOME"]) / "src" / "agent-context-sync" / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(
        '[core]\n\tbare = false\n[remote "origin"]\n\turl = https://git.fabrikam-eng.invalid?x\n'
    )
    said = (
        "fatal: unable to access 'https://git.fabrikam-eng.invalid?x/': Could not resolve host: "
        "git.fabrikam-eng.invalid"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said)
    assert shell == python, (shell, python)
    assert "fabrikam" not in shell, shell


def test_a_governance_lines_remote_url_and_the_docs_repos_host_are_redacted_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U93 (U1, U15, U56): a governance remote finding quotes the docs repo's remote URL,
    host and path, and neither reader matched it; the shell had none of round 16 T3's patterns either.
    Both write ``remote <name> (<remote>)`` for the three phrasings (an ssh URL whole, not an <email-N>
    front), ``<label>`` and ``<remote>`` for T3's values, and register the docs repo's remote host last,
    with one number in both, and its path as a rewrite's (U15)."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    git = Path(env["HOME"]) / "agent-context" / "docs" / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text('[remote "origin"]\n\turl = https://git.northwind-docs.invalid/kb/docs.git\n')
    said = (
        "remote origin (https://git.northwind-docs.invalid/kb/docs.git) exists but [governance] allow_remote "
        "= false; remote up (git@git.northwind-docs.invalid:kb/docs.git) is not under a tenant-owned prefix; "
        "refused: remote origin (https://github.com/fabrikam-team/docs.git) is not tenant-owned per "
        "remote_url_prefixes; exclude_label_ids: 'Fabrikam Zeta' is not a label GUID; "
        "remote_url_prefixes: 'git.fabrikam.invalid/x' is not a remote URL; "
        "fatal: unable to access 'https://git.northwind-docs.invalid/kb/docs.git/': 403; "
        f"remote own (git@github.com:{setup_report._REPO_SLUG}.git) exists but [governance] allow_remote"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said)
    assert shell == python, (shell, python)
    assert "northwind" not in shell and "fabrikam" not in shell.lower() and "kb/" not in shell, shell
    assert shell.startswith("remote origin (<remote>) exists but [governance] allow_remote"), shell
    assert "remote up (<remote>) is not under" in shell and "<email-" not in shell, shell
    assert "refused: remote origin (<remote>) is not tenant-owned" in shell, shell
    assert "exclude_label_ids: <label> is not" in shell and "remote_url_prefixes: <remote> is not" in shell
    assert "'https://<proxy-1>/<proxy-2>/': 403" in shell, "the path is a company word's (U15)"
    assert shell.endswith("remote own (<remote>) exists but [governance] allow_remote"), "this repo's too"
    text = report_path(env).read_text()
    assert "- <remote>: a remote URL, or a URL prefix, that a [governance] error or check quotes" in text
    assert "- <label>: a label id or name a policy error quotes" in text


def test_a_policy_or_governance_value_is_redacted_alike_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 17 U54 (T3's shell twin; the patterns are lane RA's U93, gov_value_sed): doctor's policy and
    governance.config [FAIL] lines, which the shell fallback embeds, quote a label id typed as a name and a
    remote_url_prefixes value, a quoted one or a URL with a password, in clear. Both readers write
    ``<label>`` and ``<remote>`` alike; a message with no value ("expected a list of non-empty strings")
    stays as written."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    said = (
        "[FAIL] policy: [policy]: exclude_label_ids: 'Heliotrope Zebra' is not a label GUID; "
        "[FAIL] governance.config: [governance].remote_url_prefixes: "
        "'https://git.tailspin-int.invalid/team' is not a remote URL; remote_url_prefixes: "
        "https://***@git.tailspin-int.invalid/team carries a password; "
        "exclude_label_names: expected a list of non-empty strings"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said)
    assert shell == python, (shell, python)
    assert shell == (
        "[FAIL] policy: [policy]: exclude_label_ids: <label> is not a label GUID; [FAIL] governance.config: "
        "[governance].remote_url_prefixes: <remote> is not a remote URL; remote_url_prefixes: <remote> "
        "carries a password; exclude_label_names: expected a list of non-empty strings"
    ), shell
    # B13 round 18 V44, V72: a [governance] type error's value is <value> in both readers, ending at the
    # first " (fix: " or at the end of the line; the identifier and its form stay
    for folder, said, want in (
        (
            "Ledger",
            "[FAIL] governance.config: [governance].hold_owner: expected a non-empty string, got "
            "['Jane Fabrikam'] (fix: set it); [governance].history_days: expected an integer >= 1, got "
            "'Contoso Legal' (fix: set a number)",
            "[FAIL] governance.config: [governance].hold_owner: expected a non-empty string, got <value> "
            "(fix: set it); [governance].history_days: expected an integer >= 1, got <value> (fix: set a "
            "number)",
        ),
        (
            "Atlas",
            "[governance].hold: expected true/false, got 'Litigation Fabrikam v. Tailspin'",
            "[governance].hold: expected true/false, got <value>",
        ),
    ):
        shell, python = _fuzzy_report(env, monkeypatch, [folder], said)  # each call adds its folder
        assert shell == python == want, (shell, python)
        report = report_path(env).read_text()
        assert "\n- <value>: any other value a [governance] error quotes\n" in report
        assert "Fabrikam" not in report and "Contoso Legal" not in report
    # B13 round 19 W69: the shell cut to " (fix: " or the line's end, so the words after a value (a second
    # refusal, a friction line's " | " fix column) were lost where the Python report keeps them; it now ends a
    # value at its repr's shape too, one level of brackets deep, and both readers print the same
    for folder, said, want in (
        (
            "Orbit",
            "[governance].history_days: expected an integer >= 1, got 'thirty' (exit 78) | fix the value |",
            "[governance].history_days: expected an integer >= 1, got <value> (exit 78) | fix the value |",
        ),
        (
            "Quill",
            "[governance].hold_owner: expected a non-empty string, got ['Jane Fabrikam']; [governance].hold: "
            "expected true/false, got 3 | fix col",
            "[governance].hold_owner: expected a non-empty string, got <value>; [governance].hold: expected "
            "true/false, got <value> | fix col",
        ),
        (
            "Rivet",
            "[governance].hold_reason: expected a non-empty string, got 'Fabrikam | b; c' | fix col",
            "[governance].hold_reason: expected a non-empty string, got <value> | fix col",
        ),
        (
            "Sable",
            "[governance].history_days: expected an integer >= 1, got 1.5; [governance].archive: expected "
            "true/false, got {\"k\": 'Fabrikam', 2: None}. (fix: set it)",
            "[governance].history_days: expected an integer >= 1, got <value>; [governance].archive: "
            "expected true/false, got <value>. (fix: set it)",
        ),
        # B13 round 20 X82: a no-break space ends a value as Python's \s does; the shell cut at the "." and
        # kept ".5" in clear
        (
            "Tundra",
            "[governance].history_days: expected an integer >= 1, got 1.5\u00a0z (fix: set a number)",
            "[governance].history_days: expected an integer >= 1, got <value>\u00a0z (fix: set a number)",
        ),
    ):
        shell, python = _fuzzy_report(env, monkeypatch, [folder], said)
        assert shell == python == want, (shell, python)
    # deeper than one level of brackets: the shell's wide cut, more than Python's, never less
    said = "[governance].hold_owner: expected a non-empty string, got [['Fabrikam']] | fix col"
    shell, python = _fuzzy_report(env, monkeypatch, ["Tamarack"], said)
    assert python == "[governance].hold_owner: expected a non-empty string, got <value> | fix col"
    assert shell == "[governance].hold_owner: expected a non-empty string, got <value>", shell


def test_the_shell_governance_value_program_stays_under_bsd_seds_limit() -> None:
    """B13 round 19 W69: macOS sed rejects a substitute pattern over about 2048 bytes ("unterminated
    substitute pattern") and the whole `sed -E -f` program then prints nothing, which would empty the shell
    report below the redaction stage. Python's two-level repr shape is about 3800 bytes; the shell's one-level
    rules stay well under the limit, and a run of blanks inside brackets is cut at once."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    funcs = "\n".join(
        [
            *(ln for ln in script.splitlines() if ln.startswith("GOVERNANCE_TYPED_KEYS=")),
            *re.findall(r"(?ms)^gov_value_sed\(\) \{\n.*?^\}$", script),
        ]
    )
    prog = subprocess.run(
        [BASH32, "-c", funcs + "\ngov_value_sed"], capture_output=True, check=True
    ).stdout.splitlines()
    assert len(prog) > 10 and max(len(ln) for ln in prog) < 2000, max(len(ln) for ln in prog)
    line = "[governance].hold: expected true/false, got [" + " " * 9000 + "1] | fix col\n"
    start = time.monotonic()
    cp = subprocess.run(
        [BASH32, "-c", funcs + "\nLC_ALL=C sed -E -f <(gov_value_sed)"],
        input=line.encode(),
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert cp.stdout == b"[governance].hold: expected true/false, got <value> | fix col\n", cp.stdout[:200]
    assert time.monotonic() - start < 10


def test_the_shell_governance_value_ends_at_every_blank_pythons_value_pattern_takes() -> None:
    """B13 round 20 X82: the shell ended a value only at a blank, a tab, ";", ",", ")", "|", " (fix: " or
    ".", so after a number "1.5" followed by \\r, \\v, \\f or a Unicode space the "." rule cut at "1" and kept
    ".5". Every character Python's ``\\s`` matches now ends a value as setup_report._GOVERNANCE_VALUE_RE ends
    it. The bytes setup_report's ``splitlines`` breaks a line at are tried here, on the program alone."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    funcs = "\n".join(
        [
            *(ln for ln in script.splitlines() if ln.startswith("GOVERNANCE_TYPED_KEYS=")),
            *re.findall(r"(?ms)^gov_value_sed\(\) \{\n.*?^\}$", script),
        ]
    )
    head = "[governance].history_days: expected an integer >= 1, got "
    blanks = "\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2005\u200a\u2028\u2029\u202f\u205f\u3000"
    assert all(re.fullmatch(r"\s", b) for b in blanks)
    lines = [f"{head}1.5{b}z (fix: x)" for b in blanks] + [f"{head}1.5\r"]
    cp = subprocess.run(
        [BASH32, "-c", funcs + "\nLC_ALL=C sed -E -f <(gov_value_sed)"],
        input="".join(f"{ln}\n" for ln in lines).encode(),
        capture_output=True,
        check=True,
        timeout=60,
    )
    got = cp.stdout.decode().split("\n")[:-1]
    want = [f"{head}<value>{b}z (fix: x)" for b in blanks] + [f"{head}<value>\r"]
    assert got == want, [(g, w) for g, w in zip(got, want, strict=True) if g != w]


def test_the_shell_generic_home_dirs_are_the_reports() -> None:
    """B13 round 18 V46: project_values skips the containers setup_report._project_values skips."""
    [body] = re.findall(r"(?ms)^generic_home_dir\(\) \{\n(.*?)^\}$", INSTALL_SH.read_text(encoding="utf-8"))
    words = {w.strip() for ln in re.findall(r"(?m)^\t(\S.*?)\) return 0 ;;$", body) for w in ln.split("|")}
    assert words == setup_report._GENERIC_HOME_DIRS


def test_the_shell_governance_keys_are_the_reports() -> None:
    """B13 round 18 V44: the keys whose type error the shell's gov_value_sed cuts are
    setup_report._GOVERNANCE_TYPED_KEYS, and its legend line is the Python report's."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    [keys] = re.findall(r'(?m)^GOVERNANCE_TYPED_KEYS="(.*)"$', script)
    assert tuple(keys.split("|")) == setup_report._GOVERNANCE_TYPED_KEYS
    assert f'say "- <value>: {setup_report._LEGEND["value"].removeprefix("<value> ")}"' in script


def test_a_resolver_host_that_is_a_word_the_report_prints_is_matched_only_in_a_url(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 12 N43: both readers took any word after "Could not resolve host:" as a proxy host, so a
    paraphrase made every "the" in the report a placeholder, its own sentences included, and "GitHub" turned
    github.com into "<proxy-2>.com". A dotless host the report prints as a word is matched only where a URL
    puts it, and a public host without its ".com" is none; a dotless host that is no such word (contosogw)
    is still a whole word, as curl prints a proxy, with the same number in both readers."""
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    said = (
        "Could not resolve host: the proxy was down; Could not resolve host: GitHub; see "
        "https://github.com/x/y and http://the:8080; Could not resolve host: an internal mirror is an issue; "
        "Failed to connect to contosogw port 8080"
    )
    shell, python = _fuzzy_report(env, monkeypatch, ["Plans"], said)
    assert shell == python, (shell, python)
    assert shell == (
        "Could not resolve host: the proxy was down; Could not resolve host: GitHub; see "
        "https://github.com/x/y and http://<proxy-1>:8080; Could not resolve host: an internal mirror is an "
        "issue; Failed to connect to <proxy-3> port 8080"
    ), shell
    text = report_path(env).read_text()
    assert " the " in text.split("## Agent friction log", 1)[0], "the shell report's own sentences"
    red = setup_report._build_redactor(setup_report._Run(None, setup_report.ReportHooks(), 12.0))
    assert red.redact("only writes the report, but the plist is absent") == (
        "only writes the report, but the plist is absent"
    )


@pytest.mark.parametrize("host", ["egress.northwind-lan.invalid", "Egress.Northwind-LAN.invalid"])
def test_a_proxy_in_force_at_a_logged_line_is_redacted_after_it_is_unset(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """B13 round 11 M29: --log-start and --log keep the proxy hosts in force (hosts only, 0600) beside the
    friction log, and both readers redact them in every later report, also where a line quotes one in its own
    words. B13 round 12 N42: a mixed-case host was kept in lower case only, which the shell, matching byte for
    byte, did not find in the line's own spelling: it printed in clear. It is kept as written too, and both
    reports give it one number."""
    proxied = {**env, "HTTPS_PROXY": f"http://ops:s3cret@{host}:8080"}
    assert install_sh(proxied, "--log-start", f"prompt v{COMPAT}, Contoso Agent").returncode == 0
    said = f"the proxy {host} timed out"
    assert install_sh(proxied, "--log", "2", "error", said, "-").returncode == 0
    side = friction_path(env).parent / "proxy-hosts"
    want = "".join(dict.fromkeys(["egress.northwind-lan.invalid\n", f"{host}\n"]))
    assert side.read_text() == want and side.stat().st_mode & 0o777 == 0o600
    assert install_sh(proxied, "--log", "2", "error", said, "-").returncode == 0
    assert side.read_text() == want, "a spelling already kept is not kept twice"
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "northwind" not in text.lower() and "the proxy <proxy-" in text
    assert "northwind" not in (report_path(env).parent / "bring-back.md").read_text().lower()
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(None, setup_report.ReportHooks(), 12.0))
    assert "northwind" not in red.redact(said).lower()
    shown = re.search(r"the proxy (<proxy-\d+>) timed out", text)
    assert shown is not None and red.redact(said) == f"the proxy {shown[1]} timed out", "one number in both"


@pytest.mark.parametrize("unset", [True, False])
def test_a_proxy_host_is_redacted_in_every_case_in_both_readers(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, unset: bool
) -> None:
    """B13 round 13 O2, O67 (one defect): the shell matched a proxy pair byte for byte and kept only the host
    as written and in lower case, so a line quoting it in upper or mixed case printed it in clear, with the
    proxy unset (from proxy-hosts) or still set, where the Python reader matches a proxy value in any case.
    Every proxy pair now matches in any ASCII case, with one number; a longer host ending in it still has its
    proxy part cut out, as before."""
    host = "Egress.Northwind-LAN.invalid"
    proxied = {**env, "HTTPS_PROXY": f"http://ops:s3cret@{host}:8080"}
    assert install_sh(proxied, "--log-start", f"prompt v{COMPAT}, Contoso Agent").returncode == 0
    said = (
        f"the proxy {host}, EGRESS.NORTHWIND-LAN.INVALID, egress.Northwind-lan.invalid and "
        "eGRESS.nORTHWIND-lan.INVALID timed out behind my-EGRESS.NORTHWIND-LAN.INVALID"
    )
    assert install_sh(proxied, "--log", "2", "error", said, "-").returncode == 0
    assert install_sh(env if unset else proxied, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "northwind" not in text.lower(), "a spelling the shell report printed in clear"
    [line] = [ln for ln in text.splitlines() if " | step 2 | error | the proxy " in ln]
    shown = line.split(" | error | ", 1)[1].split(" | ", 1)[0]
    assert re.fullmatch(r"the proxy (<proxy-\d+>), \1, \1 and \1 timed out behind my-\1", shown), shown
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    if not unset:
        monkeypatch.setenv("HTTPS_PROXY", proxied["HTTPS_PROXY"])
    monkeypatch.setenv("HOME", env["HOME"])
    red = setup_report._build_redactor(setup_report._Run(None, setup_report.ReportHooks(), 12.0))
    assert red.redact(said) == shown, "the same number in both readers"


def test_a_git_proxy_is_redacted_in_every_case_in_the_shell_report(env: dict[str, str]) -> None:
    """B13 round 13 O2: git's proxy, still configured, printed in clear in the shell report when a line
    named it in upper case: 'proxy <proxy-1>, <proxy-1> and EDGE-GW7...'. The agentsync report gave
    <proxy-1> three times; so does the shell now."""
    host = "Edge-Gw7.Contoso-Corp.invalid"
    (Path(env["HOME"]) / ".gitconfig").write_text(
        f'[http "https://github.com"]\n\tproxy = http://{host}:3128\n'
    )
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v{COMPAT}\nAgent: x\n"
        f"2026-10-10T00:00:01Z | step 1 | error | probe2: proxy {host}, {host.lower()} and {host.upper()} "
        "refused the connection | -\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "probe2: proxy <proxy-1>, <proxy-1> and <proxy-1> refused the connection" in text, text
    assert "gw7" not in text.lower()


def test_the_deploy_docs_list_the_proxy_hosts_file() -> None:
    """B13 round 12 N69: proxy-hosts, the one file setup keeps that holds proxy host names in clear, was in no
    list of what setup writes: not the setup-folder row a person cleans up from, nor the report's sources."""
    docs = INSTALL_SH.parents[1] / "docs" / "deploy"
    row = next(
        ln
        for ln in (docs / "README.md").read_text("utf-8").splitlines()
        if ln.startswith("| `~/agent-context/setup/`")
    )
    assert "`proxy-hosts`" in row and row.endswith("| you |"), row
    page = " ".join((docs / "setup-feedback.md").read_text("utf-8").split())
    section1 = page.split("## 1. How a report is produced", 1)[1].split("## 2.", 1)[0]
    assert "also keep `proxy-hosts` beside friction.md (0600" in section1, "a file setup writes"
    assert "in lower case and, where it differs, as written" in section1, "B13 round 12 N42's two spellings"
    proxy_row = page.split("`<serial>`, `<host>`, `<proxy-N>` | ", 1)[1].split(" |", 1)[0]
    assert "kept in `~/agent-context/setup/proxy-hosts`" in proxy_row


def test_both_readers_keep_the_same_public_hosts() -> None:
    """B13 round 11 M29: install.sh's PUBLIC_HOSTS is setup_report._PUBLIC_HOSTS."""
    words = re.findall(r'^PUBLIC_HOSTS="(?:\$PUBLIC_HOSTS )?([^"]*)"$', INSTALL_SH.read_text(), re.M)
    assert {h for line in words for h in line.split()} == set(setup_report._PUBLIC_HOSTS)


def test_both_readers_hold_the_same_identifiers_whole() -> None:
    """B13 round 11 M50: install.sh's held identifiers are setup_report._HELD_IDENTIFIERS."""
    m = re.search(r'nw = split\("([^"]*)", hw, " "\)', INSTALL_SH.read_text())
    assert m is not None
    assert set(m.group(1).split()) == set(setup_report._HELD_IDENTIFIERS)


def test_an_organisation_environment_leaves_the_plist_key_whole(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 13 O68: round 12 left EnvironmentVariables out of the held list because "environment" was a
    report word, but it is one in install.sh only: the Python report cut ``<org-1>Variables`` out of the
    Background runs line's plist key. Both keep the key whole; the Python report still cuts the organisation
    out of another word, which the shell, whose report word it is, leaves in clear."""
    shell, python = _fuzzy_report(
        env, monkeypatch, ["Plans"], "EnvironmentVariables, EnvironmentGroup", "Environment"
    )
    assert python == "EnvironmentVariables, <org-1>Group", python
    assert shell.startswith("EnvironmentVariables, "), shell


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
        "- local work kept by setup prompt step 1: 1 patch file in the setup folder's local-work, newest "
        f"written 2026-10-14T03:54:44Z ({setup_report.LOCAL_WORK_WHERE}); 2 local-work branches in the "
        "checkout\n" in text
    ), text
    assert f'LOCAL_WORK_WHERE="{setup_report.LOCAL_WORK_WHERE}"\n' in INSTALL_SH.read_text(), "one wording"
    # B13 round 11 M18: "one an earlier report already carried" read as a count beside "1 patch file(s)"
    # B13 round 17 U36, U60, U89: an unreadable carried patch has no hash in section 3 (T24)
    carried = "; any patch an earlier report already carried is listed there, "
    carried += "by its hash when it can be read); "
    assert carried in text, text
    assert "listed there by its hash)" not in text, text
    for word in ("Northwind", "Fabrikam", "pricing", "0001"):
        assert word not in text, word
    # a nested patch is not counted, nor a branch at main's or origin/main's own commit
    nested = patch.parent / "older" / patch.name
    nested.parent.mkdir()
    patch.rename(nested)
    # B13 round 17 U38: a known count takes no "(es)", and one branch has commits of its own
    neutral = "- local-work branches in the checkout: {0} with commits of {1}, no patch file in the "
    neutral += "setup folder's local-work "
    # B13 round 16 T82: it points to section 3's remedy, which the issue path (this report alone) never shows
    neutral += (
        "(moved, deleted or never written; those commits are not in bring-back.md, whose section 3 says "
    )
    neutral += "how to send them)\n"
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert neutral.format(2, "their own") in text and "local work kept" not in text, text

    def section3() -> str:
        back = (report_path(env).parent / "bring-back.md").read_text()
        return back.split("\n## 3. Local work kept by setup prompt step 1 (", 1)[1].split(")\n\n", 1)[1]

    # B13 round 13 O23: section 3 says the branches hold commits it does not carry, not a bare "none"; round
    # 14 Q30: with a remedy that names no branch, and the header says so; round 16 T27, T50, T83: the known
    # count takes its noun and verb
    assert section3() == (
        "none: no patch file is in ~/agent-context/setup/local-work, but 2 local-work branches in "
        "~/src/agent-context-sync hold commits this file does not carry; to send them, for each branch "
        '`git -C ~/src/agent-context-sync branch --list "local-work-*"` lists, run `git -C '
        '~/src/agent-context-sync -c format.filenameMaxLength=128 format-patch -q --suffix=".<branch>.patch" '
        "origin/main..<branch> -o ~/agent-context/setup/local-work` (a branch that writes nothing holds no "
        "new commit), then run ~/src/agent-context-sync/scripts/install.sh --report-only again\n"
    ), section3()
    head = (report_path(env).parent / "bring-back.md").read_text().split("\n## 1. Setup report", 1)[0]
    assert (
        "Section 3 sends no patch: 2 local-work branches hold commits this file does not carry; its line "
        "says how to add them." in " ".join(head.split())
    ), head
    assert "20261014" not in section3() + head, "no branch is named"
    (git / "refs" / "heads" / "local-work-20261014-005444").write_text("c" * 40 + "\n")
    (git / "refs" / "remotes" / "origin").mkdir(parents=True)
    (git / "refs" / "remotes" / "origin" / "main").write_text("b" * 40 + "\n")
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "local work kept" not in text and "local-work branch" not in text, text
    assert section3() == "none\n", "no branch with a commit of its own: nothing is missing"
    (git / "refs" / "heads" / "main").write_text("d" * 40 + "\n")  # a loose main overrides its packed line
    assert install_sh(env, "--report-only").returncode == 0
    assert neutral.format(1, "its own") in report_path(env).read_text()
    head = (report_path(env).parent / "bring-back.md").read_text().split("\n## 1. Setup report", 1)[0]
    assert section3().startswith(
        "none: no patch file is in ~/agent-context/setup/local-work, but 1 local-work branch in "
        "~/src/agent-context-sync holds commits this file does not carry; to send them, "
    ), section3()
    assert (
        "Section 3 sends no patch: 1 local-work branch holds commits this file does not carry;"
        in " ".join(head.split())
    ), head


def test_section_3s_branch_remedy_names_a_commit_as_the_keep_does(tmp_path: Path) -> None:
    """B13 round 15 S25: the branches-only remedy ran format-patch without the keep's
    ``-c format.filenameMaxLength=128``, so git's 64-character default (with the 33-character branch suffix)
    gave the keep's commit a second, shorter file name: sent twice if the original came back. The remedy
    install.sh prints, run on a real branch, writes the file name the README keep writes."""
    [remedy] = re.findall(
        r"run `(git -C ~/src/agent-context-sync [^`]*format-patch[^`]*)`", INSTALL_SH.read_text()
    )
    repo, branch = tmp_path / "checkout", "local-work-20261018-001303"
    git = ["git", "-C", str(repo), "-c", "user.name=x", "-c", "user.email=x@example.com"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    subprocess.run([*git, "switch", "-q", "-c", branch], check=True)
    (repo / "note.txt").write_text("x\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "local changes kept before update"], check=True)
    keep, again = tmp_path / "keep", tmp_path / "again"
    keep_cmd = (
        f"git -C {shlex.quote(str(repo))} -c format.filenameMaxLength=128 format-patch -q "
        f'--suffix=".{branch}.patch" origin/main.. -o {shlex.quote(str(keep))}'
    )
    run = (
        remedy.replace("~/src/agent-context-sync", shlex.quote(str(repo)))
        .replace("<branch>", branch)
        .replace("%s/local-work", shlex.quote(str(again)))
    )
    for cmd in (keep_cmd, run):
        subprocess.run(["/bin/bash", "-c", cmd], check=True)
    names = sorted(p.name for p in keep.iterdir())
    assert names == [f"0001-local-changes-kept-before-update.{branch}.patch"], names
    assert sorted(p.name for p in again.iterdir()) == names


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
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", **xcrun_db()},
    )
    assert (cp.returncode == 0) == setup_report._agent_words(name), (name, cp.stderr)


def _listed(cs: Path, *paths: str) -> str:
    return "# run=20261010T000000Z-1 2026-10-10T00:00:00Z install.sh --list-folders\n" + "".join(
        f"{cs}/{p}\n" for p in paths
    )


def _folder_item(text: str, head: str = "- <folder-N>: ") -> str:
    """The shell legend's <folder-N> item (or the one starting ``head``), its lines joined (each at most 115
    characters)."""
    legend = text.split("\n## Redaction\n", 1)[1].split("\n\n", maxsplit=1)[0].strip().splitlines()
    start = next(i for i, ln in enumerate(legend) if ln.startswith(head))
    end = next((i for i in range(start + 1, len(legend)) if not legend[i].startswith("  ")), len(legend))
    assert max(len(ln) for ln in legend[start:end]) <= 115, legend[start:end]
    return " ".join(" ".join(legend[start:end]).split())


@pytest.mark.parametrize(
    "state",
    [
        "plain",
        "list-only names quoted",
        "a configured Documents",
        "a list-only Documents quoted",
        "a list-only Copilot quoted",
        "a configured Documents, a list-only Copilot quoted",
        "only the Agent line names Copilot",
    ],
)
def test_the_folder_legend_explains_only_the_names_the_report_shows(env: dict[str, str], state: str) -> None:
    """B13 round 11 M3: the shell legend's <folder-N> item always explained step 1's list, the whole-word rule
    for a name every drive has and the list-only rule for coding-agent product words, with "Copilot" as its
    example, though the report kept no such name: its only "Copilot". Each clause is printed only where the
    report shows what it explains."""
    cs = _cloud(env)
    lib = cs / "OneDrive-Contoso"
    for d in ("Project Alpha", "Documents/Copilot"):
        (lib / d).mkdir(parents=True)
    setup = friction_path(env).parent
    setup.mkdir(parents=True)
    picked = lib / ("Documents" if state.startswith("a configured Documents") else "Project Alpha")
    (setup / "install.log").write_text(
        "2026-10-10T00:00:02Z run=20261010T000002Z-1 start install.sh compat=15 commit=- kind=- source=- "
        f"args=--source-local {shlex.quote(str(picked))}\n"
    )
    install_out(env).write_text(
        _listed(
            cs,
            "OneDrive-Contoso/Project Alpha",
            "OneDrive-Contoso/Documents",
            "OneDrive-Contoso/Documents/Copilot",
        )
    )
    quoted = {
        "plain": "Project Alpha",
        "list-only names quoted": "Copilot under Documents",
        "a list-only Copilot quoted": "Copilot",
        "a configured Documents, a list-only Copilot quoted": "Copilot under Documents",
    }.get(state, "Documents")
    # B13 round 13 O10: the agent's own product name is no folder a listing names
    agent_line = "x"
    if state == "only the Agent line names Copilot":
        agent_line, quoted = "GitHub Copilot CLI gpt-5", "Project Alpha"
    friction_path(env).write_text(
        f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\nAgent: {agent_line}\n2026-10-10T00:00:01Z | step 1 | "
        f"deviation | picked {quoted} | -\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    item = _folder_item(report_path(env).read_text())
    base = (
        "- <folder-N>: the names of the folders under CloudStorage that --source-local, sources.toml, "
        "install.log or step 1's folder list in install.out name"
    )
    whole = (
        "; a name every OneDrive or Google Drive has, such as Documents, only where it stands as a whole word"
    )
    only = (
        "; not a name every OneDrive or Google Drive has, such as Documents, nor one made of coding-agent "
        "product words (Copilot, GitHub Copilot), where only step 1's list names it"
    )
    # B13 round 12 N45: one kind of list-only name quoted gives that kind's clause alone
    agent = "one made of coding-agent product words (Copilot, GitHub Copilot)"
    want = {
        "plain": base,
        "only the Agent line names Copilot": base,
        "list-only names quoted": base + only,
        "a configured Documents": base + whole,
        "a list-only Documents quoted": base + "; not a name every OneDrive or Google Drive has, such as "
        "Documents, where only step 1's list names it",
        "a list-only Copilot quoted": base + f"; not {agent}, where only step 1's list names it",
        "a configured Documents, a list-only Copilot quoted": base
        + whole
        + f", and not {agent} where only step 1's list names it",
    }[state]
    # B13 round 20 X22: every item ends on the note the agentsync report's legend has
    want += "; a number can be missing (one given to a name this report does not show)"
    assert item == want, item
    assert ("Copilot" in item) == ("Copilot" in quoted), item


@pytest.mark.parametrize("state", ["a project folder", "a nested project folder", "cloud only"])
def test_the_folder_legend_names_a_project_folder_only_where_one_is_shown(
    env: dict[str, str], state: str
) -> None:
    """B13 round 19 W5, W22: since round 18 V46 the shell report also gives a configured folder outside
    CloudStorage (and a removed source's re-add folder) a <folder-N>, while its legend still said every
    <folder-N> is a folder under CloudStorage. The legend now says so too, only where the body shows such a
    placeholder (round 11 M3's rule); a report with cloud folders alone keeps the item as it was.

    B13 round 20 X6, X23: that clause ("from its first part that names something down") could not be parsed.
    It now says what project_values replaces, and no more: a nested configured path's whole path below its
    first folder that is no common container and that folder alone are <folder-N>, while a later part on its
    own ("notes") stays as written, as the legend says. X22: the item ends on "a number can be missing", and
    so does the <source-N> item."""
    home = Path(env["HOME"])
    cloud = _cloud(env) / "OneDrive-Contoso" / "Project Alpha"
    cloud.mkdir(parents=True)
    roadmap = home / "Projects" / "Contoso Roadmap"
    roadmap.mkdir(parents=True)
    (roadmap / "notes").mkdir()
    config = home / "agent-context" / "sources.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    picked = {"a project folder": roadmap, "a nested project folder": roadmap / "notes"}.get(state, cloud)
    config.write_text(f'[[source]]\nid = "picked"\nkind = "local"\npath = "{picked}"\n')
    friction_path(env).parent.mkdir(parents=True, exist_ok=True)
    said = (
        "picked notes in Contoso Roadmap; full Contoso Roadmap/notes; file notes/plan.md"
        if state == "a nested project folder"
        else f"picked {picked.name} in {picked.parent.name}"
    )
    friction_path(env).write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\nAgent: x\n2026-10-10T00:00:01Z | step 1 | "
        f"deviation | {said} | -\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    item = _folder_item(text)
    clause = (
        "; also a folder outside CloudStorage that sources.toml names, or that doctor's fix adds a removed "
        "source back from: inside the home folder, its path from the first folder that is not a common "
        "container (such as Projects, Documents or a dot folder) down, and that folder's name on its own (in "
        '~/Projects/Harborview/notes, both "Harborview/notes" and "Harborview"); outside the home folder, '
        "its whole path; each as a whole word, in any letter case; a later folder's name on its own, such as "
        "notes, is not replaced"
    )
    assert item.startswith("- <folder-N>: the names of the folders under CloudStorage that "), item
    assert (clause in item) == (state != "cloud only"), item
    assert item.endswith("; a number can be missing (one given to a name this report does not show)"), item
    assert "Contoso Roadmap" not in text and "Project Alpha" not in text
    if state == "a project folder":
        assert "<folder-1> in Projects" in text
    if state == "a nested project folder":
        # the legend promises no more than the body shows: the path and its first folder are replaced, "notes"
        # on its own is not
        assert re.search(r" notes in <folder-(\d+)>; full <folder-\d+>; file notes/plan\.md \|", text), text
    source = text.split("\n- <source-N>: ", 1)[1].split("\n- ", 1)[0].split("\nRead the report", 1)[0]
    assert " ".join(source.split()).endswith("the sync's refusal quote it; a number can be missing"), source


def test_an_agent_name_inside_another_leaves_no_part_for_the_folder_legend(env: dict[str, str]) -> None:
    """B13 round 14 Q49 (round 13 O10's twin): the shell took Agent: values out in the order it read them, so
    "Copilot" first left "GitHub" of a later "GitHub Copilot", and a list-only folder named GitHub then gave
    the list-only agent clause, which the Python report, longest first, did not. Both leave no part of it."""
    from agentsync import setup_report  # noqa: PLC0415

    cs = _cloud(env)
    for d in ("Project Alpha", "GitHub"):
        (cs / "OneDrive-Contoso" / d).mkdir(parents=True)
    setup = friction_path(env).parent
    setup.mkdir(parents=True)
    install_out(env).write_text(_listed(cs, "OneDrive-Contoso/Project Alpha", "OneDrive-Contoso/GitHub"))
    friction = (
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\nAgent: Copilot\n2026-10-10T00:00:01Z | step 1 | "
        "deviation | picked Project Alpha | -\nAttempt: 2026-10-11T00:00:00Z\nPrompt: v15\n"
        "Agent: GitHub Copilot\n"
    )
    friction_path(env).write_text(friction)
    assert "GitHub" not in setup_report._without_agents(friction)
    assert install_sh(env, "--report-only").returncode == 0
    item = _folder_item(report_path(env).read_text())
    assert "coding-agent" not in item, item
    assert item.startswith("- <folder-N>: the names of the folders under CloudStorage"), item


@pytest.mark.parametrize(
    "case",
    [
        "plain",
        "inside a longer word",
        "inside a longer word, mid-word",
        "inside a longer word, a second organization",
        "an email address",
        "a quoted word before it",
        "a quoted word alone",
        "this Mac's own app",
    ],
)
def test_the_org_legend_explains_only_what_the_report_shows(env: dict[str, str], case: str) -> None:
    """B13 round 12 N16 (M3's rule for the <org-N> item): it always named a Dropbox folder and an email
    address, and never the organization cut out of a longer word (FUZZY=o: ContosoDrive reads <org-1>Drive).
    Its example is the app the body shows, and each clause is printed only where the body shows its case.
    B13 round 13 O45: any "<word>-<org-N>" counted as an app's folder, so a quoted "sales-Contoso" gave
    "(OneDrive-<org-N>, sales-<org-N>)"; only an app's name counts, a fixed one or this Mac's own. B13 round
    14 Q5: the in-word example was always "<org-1>Drive"; it is a word the body shows."""
    if case == "inside a longer word, a second organization":
        (_cloud(env) / "OneDrive-Fabrikam" / "Project Beta").mkdir(parents=True)
    provider = {
        "an email address": "GoogleDrive-someone@contoso.com",
        "this Mac's own app": "ProtonDrive-Contoso",
    }.get(case, "OneDrive-Contoso")
    (_cloud(env) / provider / "Project Alpha").mkdir(parents=True)
    quoted = {
        "plain": "Project Alpha under OneDrive-Contoso",
        "inside a longer word": "ContosoDrive under OneDrive-Contoso",
        "inside a longer word, mid-word": "MyContosoTeam under OneDrive-Contoso",
        "inside a longer word, a second organization": "Project Alpha under OneDrive-Contoso, FabrikamHub "
        "under OneDrive-Fabrikam",
        "an email address": f"Project Alpha under {provider}",
        "a quoted word before it": "the sales-Contoso team folder under OneDrive-Contoso",
        "a quoted word alone": "the sales-Contoso team folder",
        "this Mac's own app": f"Project Alpha under {provider}",
    }[case]
    friction_path(env).parent.mkdir(parents=True)
    friction_path(env).write_text(
        f"Attempt: 2026-10-10T00:00:00Z\nPrompt: v15\nAgent: x\n2026-10-10T00:00:01Z | step 1 | deviation | "
        f"picked {quoted} | -\n"
    )
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    assert "contoso" not in text.lower().replace("contoso agent", ""), text
    item = _folder_item(text, "- <org-N>: ")
    base = "- <org-N>: the organization or team after the app's name in a CloudStorage folder "
    want = {
        "plain": base + "(OneDrive-<org-N>)",
        "inside a longer word": base
        + "(OneDrive-<org-N>), also where it stands inside a longer word (such as <org-1>Drive)",
        "inside a longer word, mid-word": base
        + "(OneDrive-<org-N>), also where it stands inside a longer word (such as My<org-1>Team)",
        "inside a longer word, a second organization": base
        + "(OneDrive-<org-N>), also where it stands inside a longer word (such as <org-2>Hub)",
        "an email address": base + "(GoogleDrive-<org-N>), and an account's email address",
        "a quoted word before it": base + "(OneDrive-<org-N>)",
        "a quoted word alone": base + "(OneDrive-<org-N>)",
        "this Mac's own app": base + "(ProtonDrive-<org-N>)",
    }[case]
    assert item == want, item
    assert "sales-" not in item


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
    # a configured folder named so is still redacted, as in the Python report, but in the agent's name (B13
    # round 20 X40, X81: "GitHub <folder-N> CLI" decoded its own placeholder), where the Python report keeps
    # it too (round 19 W17); a path still reads <folder-N>
    (cs / "OneDrive-Contoso" / "Copilot").mkdir()
    _write_config(env, local_source_table("copilot", cs / "OneDrive-Contoso" / "Copilot"))
    said = (
        f"prompt v{COMPAT}, GitHub Copilot CLI: git pull failed in "
        "~/Library/CloudStorage/OneDrive-Contoso/Copilot"
    )
    assert install_sh(env, "--log", "1", "error", said, "-").returncode == 0
    assert install_sh(env, "--report-only").returncode == 0
    text = report_path(env).read_text()
    outcome = next(ln for ln in _summary(text) if ln.startswith("- outcome: "))
    assert outcome.endswith(" · agent: GitHub Copilot CLI"), outcome
    fields = urllib.parse.parse_qs(urllib.parse.urlsplit(text.splitlines()[-1]).query)
    assert fields["agent"] == ["GitHub Copilot CLI"], fields
    assert re.search(
        r"\| prompt v\d+, GitHub Copilot CLI: git pull failed in ~/Library/CloudStorage/"
        r"OneDrive-<org-\d+>/<folder-\d+> \|",
        text,
    ), text
    item = " ".join(_folder_item(text).split())
    assert "; a configured one's coding-agent product word stays as written in the agent's name;" in item, (
        item
    )


def test_the_shells_agent_spans_keep_only_a_product_word_standing_alone(tmp_path: Path) -> None:
    """B13 round 20 X40, X81, the review of the build: agent_protect keeps a configured product-word value in
    the agent's name only. A colon inside the stated run ("prompt v27: ...") or in the fix column is no
    agent's end, a "## " line inside the fenced friction log leaves the spans read, a tab-indented header is
    a header, and a longer value of another kind ("Claude Code" beside "Claude") is the pairs' to replace
    whole, as the agentsync report matches the longest value first. The pairs are stubbed; the body runs
    through agent_protect, a stand-in redaction and agent_restore."""
    pairs = "Copilot\t<folder-1>\tf\nClaude\t<folder-3>\tf\nClaude Code\t<folder-2>\tf\n"
    body = [
        "## Summary",
        "- outcome: x · agent: Claude Code (Opus)",
        "- outcome: y · agent: GitHub Copilot-CLI · z",
        "## Agent friction log",
        "~~~text",
        "## Notes",
        "\tAgent: GitHub Copilot CLI gpt-5 in ~/Development/Copilot",
        "2026-10-10T00:00:01Z | step 1 | error | prompt v27: Copilot CLI: stopped | -",
        "2026-10-10T00:00:01Z | step 1 | error | prompt v27,,: Xcode stopped | -",
        "2026-10-10T00:00:01Z | step 1 | error | prompt v27, Copilot stopped | run: x",
        "2026-10-10T00:00:02Z | step 2 | deviation | Copilot synced | -",
        "Agent: Claude Code and Claude, CopilotX",
        "~~~",
    ]
    flag = tmp_path / "kept"
    script = "\n".join(
        [
            "set -euo pipefail",
            *(
                _shell_function(f)
                for f in ("agent_words_name", "agent_word_values", "agent_protect", "agent_restore")
            ),
            f"redaction_pairs() {{ printf {shlex.quote(pairs)}; }}",
            "agent_protect | LC_ALL=C sed 's/Claude Code/<folder-2>/g; s/Copilot/<folder-1>/g; "
            "s/Claude/<folder-3>/g' | agent_restore",
        ]
    )
    cp = subprocess.run(
        [BASH32, "-c", script],
        input="\n".join(body) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "AGENT_KEPT_FILE": str(flag)},
    )
    assert cp.stdout.splitlines() == [
        "## Summary",
        "- outcome: x · agent: <folder-2> (Opus)",
        "- outcome: y · agent: GitHub Copilot-CLI · z",
        "## Agent friction log",
        "~~~text",
        "## Notes",
        "\tAgent: GitHub Copilot CLI gpt-5 in ~/Development/<folder-1>",
        "2026-10-10T00:00:01Z | step 1 | error | prompt v27: Copilot CLI: stopped | -",
        "2026-10-10T00:00:01Z | step 1 | error | prompt v27,,: Xcode stopped | -",
        "2026-10-10T00:00:01Z | step 1 | error | prompt v27, <folder-1> stopped | run: x",
        "2026-10-10T00:00:02Z | step 2 | deviation | <folder-1> synced | -",
        "Agent: <folder-2> and Claude, <folder-1>X",
        "~~~",
    ], cp.stdout
    assert flag.exists(), "a kept word sets the legend's flag"


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
    "agentsync: another sync is running (the background job, or a sync started another way); the reinstall "
    "waits for it to end (at most {}s)"
)
"""The reinstall's wait line for a holder whose sidecar does not say who started it (B13 round 7 H33): an
older build's, as the tests' locks are. Not "one started in a terminal": the environment cannot tell a
terminal from cron or an agent's shell (B13 round 11 M43, M60)."""


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


@pytest.mark.parametrize("bare", [False, True])
def test_the_reinstall_waits_for_a_running_background_sync_and_holds_its_lock(
    env: dict[str, str], folder: Path, wheel: Path, bare: bool
) -> None:
    """With --force uv rebuilds the tool environment the background sync runs from (2026-10-08 U7): the
    reinstall waits for a running sync, with progress lines, and holds the sync's lock while uv runs, so a
    sync starting then is skipped (exit 75).  The lock's body stays empty: the next sync breaks no stale lock.
    The test lets go once the first progress line is out, not after a fixed sleep.

    B13 round 20 X43: a lock held with an empty body (lockf, a probe, a cycle starting) need not be a sync's,
    and round 19 W31 said so on screen while install.log still noted waited-for-a-background-sync, which the
    report reads as "waited for another sync to end"; that wait notes waited-for-an-unrecorded-holder."""
    e, lock = _held_sync(env)
    e = {**e, "AGENTSYNC_PROGRESS_SECONDS": "1", "AGENTSYNC_REINSTALL_WAIT_SECONDS": "60"}
    waits, tick = REINSTALL_WAITS.format(60), REINSTALL_TICK
    if bare:  # round 19 W31's words for a lock with an empty body
        waits = (
            "agentsync: another sync is running (or install.sh, a status or doctor check, or a cycle "
            "starting holds the lock); the reinstall waits for it to be free (at most 60s)"
        )
        tick = r"agentsync: reinstalling after the lock is free, still running, \d+s"
    if bare:  # held with no body, as lockf holds it
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)

        def release() -> None:
            os.close(fd)
    else:
        held = SingleWriterLock(lock, "poll")
        held.acquire()
        release = held.release
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
                if re.fullmatch(tick, early[-1]) or time.monotonic() > deadline:
                    break
            Path(f"{lock}.released").touch()  # before the release: uv can only start after it
        finally:
            release()
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
    assert waits in lines, out
    ticks = [ln for ln in lines if ln.startswith("agentsync: reinstalling")]
    assert ticks and all(re.fullmatch(tick, t) for t in ticks), out
    assert "still running after" not in out
    assert _lock_log(e) == ["released=yes", "held=True"]
    assert sum(c.startswith("uv tool install") for c in calls(e)) == 1
    assert _step_seconds(e, "agentsync") >= 1
    # install.log says why the step took its seconds (B13 round 2 R34).
    note = "waited-for-an-unrecorded-holder" if bare else "waited-for-a-background-sync"
    assert ("agentsync", "done", "0", note) in steps(install_log(e))
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
    # B13 round 17 U68: the probe found the lock busy and said so, so agentsync's quiet wait stays silent
    assert (Path(e["STUB_LOG"] + ".announced")).read_text(encoding="utf-8").splitlines() == ["announced=1"]
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


@pytest.mark.parametrize(
    ("holder", "words"),
    [
        ("other", "a sync not started by the background job, pass poll"),
        ("launchd", "the background job, pass poll"),
        ("no started_by", "the background job, or a sync started another way"),
        ("another pid", "the background job, or a sync started another way"),
        ("an empty body", ""),
    ],
)
def test_the_reinstall_names_who_started_the_sync_it_waits_for(
    env: dict[str, str], folder: Path, wheel: Path, holder: str, words: str
) -> None:
    """B13 round 11 M43, M60: since B13 round 10 L73 the holder sidecar records who started a cycle, and
    agentsync's own lines name it, but the reinstall's wait line still said the lock cannot tell and offered
    "one started in a terminal". It names the starter when the sidecar says so for the lock body's own pid,
    in agentsync's words, and never the sidecar's host; any other holder gets the either-or words.

    B13 round 19 W31: an empty body is held by no sync (install.sh's lockf, a status or doctor check, a cycle
    starting: ops/lock.py's EMPTY_BODY_HOLDER), as round 18 V40 says in agentsync's own wait, yet the wait,
    its ticker and its run-out line said "another sync" and "the other sync". They now name those holders,
    keeping the opening step 2 of the prompt quotes.

    B13 round 20 X32: the pass reads ", pass poll", as the cycle's own wait line words the same holder
    (ops/lock.py ``_wait_words``, round 19 W29), not "its pass: poll": the two foreground wait lines match."""
    e, lock = _held_sync(env)
    e["AGENTSYNC_REINSTALL_WAIT_SECONDS"] = "1"
    started_by = {"other": "other", "launchd": "launchd"}.get(holder)
    with contextlib.ExitStack() as stack:
        if holder == "an empty body":  # held with no body, as lockf holds it, beside this pid's sidecar
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
            sidecar = {"host": "contoso-mac", "label": "poll", "pid": os.getpid(), "started_by": "other"}
            Path(f"{lock}.json").write_text(json.dumps(sidecar, sort_keys=True, indent=2) + "\n")
        else:
            stack.enter_context(SingleWriterLock(lock, "poll", started_by=started_by))
            if holder == "another pid":
                side = json.loads(Path(f"{lock}.json").read_text())
                side["pid"] += 1
                side["started_by"] = "launchd"
                Path(f"{lock}.json").write_text(json.dumps(side, sort_keys=True, indent=2) + "\n")
        host = json.loads(Path(f"{lock}.json").read_text())["host"]
        cp = install_sh(e, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    if words:
        assert (
            f"agentsync: another sync is running ({words}); the reinstall waits for it to end (at most 1s)"
            in lines
        ), cp.stdout
    else:
        assert (
            "agentsync: another sync is running (or install.sh, a status or doctor check, or a cycle "
            "starting holds the lock); the reinstall waits for it to be free (at most 1s)"
        ) in lines, cp.stdout
        assert "agentsync: the lock is still held after 1s; reinstalling without waiting longer" in lines
        assert not [ln for ln in lines if ln.startswith("agentsync:") and "the other sync" in ln], cp.stdout
        assert "the background job" not in cp.stdout
    assert host not in cp.stdout + cp.stderr
    # B13 round 20 X43: install.log's note for an empty body names no sync either
    note = "wait-ran-out-after-1s" if words else "lock-wait-ran-out-after-1s"
    assert ("agentsync", "done", "0", note) in steps(install_log(e))


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
