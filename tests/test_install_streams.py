"""install.sh's stream layer (CLI conventions U-A2): a reader that goes away never kills the run, and the
pieces the JSON document will use, the string encoder and the reason registry of the list branches."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path

import pytest

import exec_stub
from test_install_oneshot import (  # noqa: F401  (fixtures)
    BASH32,
    INSTALL_SH,
    STUB_FIND_EPERM,
    _install_bound,
    env,
    install_log,
    report_path,
    wheel,
)


def _closed_reader_run(e: dict[str, str], reader: str, *args: str) -> tuple[int, str]:
    """One install.sh run whose stdout reader goes away; returns (returncode, stderr). ``one_line`` reads one
    line and closes, as `| head -1` does. ``at_start`` has no reader from the first byte, so the very first
    write meets the closed pipe: a short run can fit in the pipe's buffer and end before a one-line reader
    closes. Either way the run's tee has to outlive EPIPE, BSD tee's behaviour this file confirms on macOS."""
    r, w = os.pipe()
    if reader == "at_start":
        os.close(r)
    proc = subprocess.Popen(
        [BASH32, str(INSTALL_SH), *args],
        stdout=w,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        env=e,
        start_new_session=True,
    )
    os.close(w)
    if reader == "one_line":
        with os.fdopen(r) as reading:
            reading.readline()
    with proc:
        try:
            _, stderr = proc.communicate(timeout=_install_bound(120))
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
    return proc.returncode, stderr


def _synced_folder(e: dict[str, str]) -> Path:
    f = Path(e["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "FY26 Projects"
    f.mkdir(parents=True)
    return f


def _tree(home: Path) -> list[Path]:
    return sorted(home.rglob("*"))


@pytest.mark.parametrize("reader", ["one_line", "at_start"])
@pytest.mark.parametrize(
    ("args", "rc", "stub_find"),
    [
        (["--list-folders"], 0, False),
        (["--list-folders"], 3, False),
        (["--list-folders"], 4, True),
    ],
    ids=["exit-0", "exit-3", "exit-4"],
)
def test_list_folders_keeps_its_documented_exit_when_its_reader_closes(
    env: dict[str, str],  # noqa: F811
    reader: str,
    args: list[str],
    rc: int,
    stub_find: bool,
) -> None:
    """Decision D25: 0, 3 and 4 are what the caller was told, so a closed stdout changes none of them. The run
    finishes: install.log has its end line and install.out holds the whole list and its one NEXT line."""
    if rc != 3:
        _synced_folder(env)
    if stub_find:
        exec_stub.write(Path(env["PATH"].split(":")[0]) / "find", STUB_FIND_EPERM)
    got, stderr = _closed_reader_run(env, reader, *args)
    assert got == rc, stderr
    assert "broken pipe" not in stderr.lower() and "write error" not in stderr.lower(), stderr
    assert f" end rc={rc} " in install_log(env)[-1], install_log(env)
    out = (Path(env["HOME"]) / "agent-context" / "setup" / "install.out").read_text().splitlines()
    assert sum(ln.startswith("# run=") for ln in out) == 1, out
    assert sum(ln.startswith("NEXT: ") for ln in out) == 1, out


@pytest.mark.parametrize("reader", ["one_line", "at_start"])
def test_an_install_run_finishes_and_keeps_its_report_when_its_reader_closes(
    env: dict[str, str],  # noqa: F811
    wheel: Path,  # noqa: F811
    reader: str,
) -> None:
    """A started mutation finishes, on_exit writes the report and the log, and the run keeps its real rc."""
    args = (str(wheel), "--source-local", str(_synced_folder(env)), "--confirm-install-agent")
    got, stderr = _closed_reader_run(env, reader, *args)
    assert got == 0, stderr
    assert "broken pipe" not in stderr.lower() and "write error" not in stderr.lower(), stderr
    assert " end rc=0 " in install_log(env)[-1], install_log(env)
    assert report_path(env).exists()
    out = (Path(env["HOME"]) / "agent-context" / "setup" / "install.out").read_text().splitlines()
    assert sum(ln.startswith("# run=") for ln in out) == 1, out
    assert sum(ln.startswith("NEXT: ") for ln in out) == 1, out


@pytest.mark.parametrize("reader", ["one_line", "at_start"])
def test_report_only_writes_its_report_and_no_copy_when_its_reader_closes(
    env: dict[str, str],  # noqa: F811
    reader: str,
) -> None:
    got, stderr = _closed_reader_run(env, reader, "--report-only")
    assert got == 0, stderr
    assert "broken pipe" not in stderr.lower() and "write error" not in stderr.lower(), stderr
    assert report_path(env).exists()
    assert not (Path(env["HOME"]) / "agent-context" / "setup" / "install.out").exists()


@pytest.mark.parametrize("reader", ["one_line", "at_start"])
def test_a_dry_run_still_changes_nothing_when_its_reader_closes(
    env: dict[str, str],  # noqa: F811
    wheel: Path,  # noqa: F811
    reader: str,
) -> None:
    folder = _synced_folder(env)
    home = Path(env["HOME"])
    before = _tree(home)
    dry = {**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}
    got, stderr = _closed_reader_run(dry, reader, str(wheel), "--source-local", str(folder))
    assert got == 0, stderr
    assert "broken pipe" not in stderr.lower() and "write error" not in stderr.lower(), stderr
    assert _tree(home) == before


@pytest.mark.parametrize("reader", ["one_line", "at_start"])
def test_help_exits_0_when_its_reader_closes(env: dict[str, str], reader: str) -> None:  # noqa: F811
    """show_help's awk takes the SIGPIPE, as `--help | head` gives it; the help was asked for and printed."""
    got, stderr = _closed_reader_run(env, reader, "--help")
    assert got == 0, stderr
    assert "broken pipe" not in stderr.lower() and "write error" not in stderr.lower(), stderr


def _function(name: str) -> str:
    fn = re.search(rf"^{name}\(\) \{{.*?^\}}\n", INSTALL_SH.read_text(encoding="utf-8"), re.M | re.S)
    assert fn is not None, name
    return fn.group(0)


def test_json_str_escapes_controls_and_quotes() -> None:
    controls = [chr(i) for i in range(1, 32)] + [chr(127)]
    plain = [
        "",
        "Contoso",
        "007",
        "1e3",
        "a=b",
        "é",
        "a\\nb",
        'say "hi"',
        "back\\slash",
        "tab\there",
        "two\nlines",
    ]
    values = controls + plain
    run = _function("json_str") + 'for v in "$@"; do json_str "$v"; echo; done'
    cp = subprocess.run(
        [BASH32, "-c", run, "-", *values],
        capture_output=True,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    lines = cp.stdout.split(b"\n")[:-1]  # every line one value: a raw newline in a value would add a line
    assert len(lines) == len(values)
    for value, line in zip(values, lines, strict=True):
        assert json.loads(line.decode("utf-8")) == value, line
        assert not any(b < 0x20 or b == 0x7F for b in line), line
    exact = dict(zip(values, lines, strict=True))
    assert exact['say "hi"'] == b'"say \\"hi\\""'
    assert exact["back\\slash"] == b'"back\\\\slash"'
    assert exact["\x01"] == b'"\\u0001"'
    assert exact["é"] == '"é"'.encode(), "a byte above 127 stays as it is"


def test_the_registry_covers_every_list_exit() -> None:
    """Every failed exit of list_folders' last branch chain has one reason, read from the branch's note word
    and never from the rc: list exit 3 is two different reasons, and rc_meaning's 3 is another thing again."""
    body = _function("list_folders")
    chain = body[body.rindex('if [ -n "$denied" ]; then\n\t\trc=4') :]
    exits = re.findall(r'^\s*rc=(\d+)\n(?:.*\n)*?\s*step_end failed "\$rc" "([a-z-]+)', chain, re.M)
    assert [rc for rc, _ in exits] == ["4", "4", "3", "3", "3"], exits
    assert len(re.findall(r"^\s*rc=[1-9]", chain, re.M)) == len(exits), "an exit the scan did not read"
    assert not re.search(r'step_end failed "?[^"$]', chain), "a failed branch with no $rc"

    run = _function("reason_for_list") + 'for b in "$@"; do reason_for_list "$b"; echo; done'
    branches = [word for _, word in exits]
    never = ["listed-4", "3", "4", "0"]
    cp = subprocess.run(
        [BASH32, "-c", run, "-", *branches, *never], capture_output=True, text=True, check=True
    )
    got = cp.stdout.splitlines()
    assert dict(zip(branches, got, strict=False)) == {
        "denied": "denied",
        "tcc-pending": "tcc_pending",
        "not-signed-in": "not_signed_in",
        "no-folders": "no_folders",
    }
    assert got[len(branches) :] == ["", "", "", ""], "no reason for a success, and none from an rc"
