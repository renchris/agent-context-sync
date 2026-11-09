"""install.sh's ``--json`` (CLI conventions U-B12, D15 Option B): on ``--list-folders`` only, one document on
stdout or one line on stderr, while install.out and install.log keep the human lines; and the record of D14
Option B, that the default stdout keeps NEXT last."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import exec_stub
from agentsync.setup_report import listed_folder_names
from test_install_oneshot import (  # noqa: F401  (fixtures)
    INSTALL_SH,
    STUB_FIND_EPERM,
    env,
    folder,
    install_sh,
    last_line,
)


def _cloud(e: dict[str, str]) -> Path:
    return Path(e["HOME"]) / "Library" / "CloudStorage"


def _install_out(e: dict[str, str]) -> str:
    return (Path(e["HOME"]) / "agent-context" / "setup" / "install.out").read_text()


def _runs(text: str) -> list[list[str]]:
    """install.out's runs, each its lines after the ``# run=`` header."""
    runs: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("# run="):
            runs.append([])
        elif runs:
            runs[-1].append(line)
    return runs


def test_list_folders_json_is_one_document(env: dict[str, str]) -> None:  # noqa: F811
    """R244: stdout is one document, stderr is empty, and the cap of 200 is lifted, so pagination is null;
    install.out holds the same lines a human run of the whole list writes there."""
    for n in range(201):
        (_cloud(env) / "OneDrive-Contoso" / f"Project {n:03d}").mkdir(parents=True)
    cp = install_sh(env, "--list-folders", "--json")
    assert cp.returncode == 0, cp.stderr
    assert cp.stderr == ""
    doc = json.loads(cp.stdout)
    assert set(doc) == {"folders", "total", "pagination", "next"}
    assert doc["total"] == 201
    assert len(doc["folders"]) == 201
    assert doc["folders"][0] == {"path": f"{_cloud(env)}/OneDrive-Contoso/Project 000", "state": "listed"}
    assert doc["pagination"] == {"next": None}
    assert doc["next"] == [
        {
            "command": f'{INSTALL_SH} --source-local "<folder>"',
            "argv": [str(INSTALL_SH), "--source-local", "<folder>"],
        }
    ]
    human = install_sh(env, "--list-folders", "--all")
    assert human.returncode == 0, human.stderr
    json_run, human_run = _runs(_install_out(env))[-2:]
    assert json_run == human_run
    assert json_run == human.stdout.splitlines()


def test_a_denied_list_is_one_error_line(env: dict[str, str]) -> None:  # noqa: F811
    """R253, R254: exit 4 stays, stdout is empty, and one stderr line carries the reason, the click and the
    folders the list could still read."""
    (_cloud(env) / "OneDrive-Contoso" / "FY26 Projects").mkdir(parents=True)
    (_cloud(env) / "Dropbox" / "Shared").mkdir(parents=True)
    exec_stub.write(Path(env["PATH"].split(":")[0]) / "find", STUB_FIND_EPERM)
    cp = install_sh({**env, "TERM_PROGRAM": "Apple_Terminal"}, "--list-folders", "--json")
    assert cp.returncode == 4, cp.stderr
    assert cp.stdout == ""
    assert len(cp.stderr.splitlines()) == 1, cp.stderr
    doc = json.loads(cp.stderr)
    assert doc["status"] == "action_required"
    assert doc["reason"] == "denied"
    assert "System Settings > Privacy & Security > Files and Folders" in doc["message"]
    assert doc["next"] == [
        {
            "command": f"{INSTALL_SH} --list-folders --json",
            "argv": [str(INSTALL_SH), "--list-folders", "--json"],
        }
    ]
    assert doc["data"] == {"folders": [{"path": f"{_cloud(env)}/Dropbox/Shared", "state": "listed"}]}
    assert "NEXT: this terminal app (Terminal) was denied access" in _install_out(env)


@pytest.mark.parametrize(
    "args",
    [
        ["--json"],
        ["--report-only", "--json"],
        ["--version", "--json"],
        ["--json", "--help"],
        ["--json", "help"],
        ["--list-folders", "--json", "--bogus"],
    ],
)
def test_json_elsewhere_is_a_usage_error(env: dict[str, str], args: list[str]) -> None:  # noqa: F811
    """D15: an install run, --report-only, and until U-B17 --version and --help, refuse --json; the refusal,
    and any usage error of a --json run, is one JSON line on stderr with exit 2 and nothing on stdout."""
    cp = install_sh(env, *args)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert cp.stdout == ""
    assert len(cp.stderr.splitlines()) == 1, cp.stderr
    doc = json.loads(cp.stderr)
    assert doc["status"] == "error"
    assert doc["reason"] == "usage"
    if "--bogus" in args:
        assert doc["message"] == "unknown option --bogus"
    else:
        assert doc["message"] == "--json works with --list-folders only"
    assert not (Path(env["HOME"]) / "agent-context").exists(), "a parse error writes nothing"


def test_redaction_still_sees_a_json_list_run(env: dict[str, str], folder: Path) -> None:  # noqa: F811
    """The setup report keeps listed names in clear by reading install.out's human lines, which a JSON run
    still writes there."""
    cp = install_sh(env, "--list-folders", "--json")
    assert cp.returncode == 0, cp.stderr
    seen, names = listed_folder_names(_install_out(env), env["HOME"])
    assert seen
    assert names == [("OneDrive-Contoso", 2, folder.name)]


def test_default_stdout_keeps_next_last(env: dict[str, str], folder: Path) -> None:  # noqa: F811
    """D14 Option B, the named exception (R016, R173, R177, R006): with no --json, stdout stays the transcript
    the setup prompt reads, the list and then NEXT last, and --version ends on the compat line."""
    cp = install_sh(env, "--list-folders")
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.splitlines()[:-1] == [str(folder)]
    assert last_line(cp).startswith("NEXT: choose the folders to sync from the list above")
    version = install_sh(env, "--version")
    assert version.returncode == 0, version.stderr
    assert last_line(version).startswith("setup-prompt-compat ")
