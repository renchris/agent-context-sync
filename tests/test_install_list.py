"""CONTRACTS §16.300.18 (U-B11): install.sh's folder list. ``--all`` and ``--limit`` size it with no new
stdout line (the "(N more)" line stays exact and the NEXT names the --all command), a name holding a control,
bidi or zero-width character prints escaped on one row of its own, and the shell and report readers of
install.out register the same names. Every run is /bin/bash 3.2, through test_install_oneshot's harness."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from agentsync import setup_report
from agentsync.config import local_source_table
from test_install_oneshot import (  # noqa: F401  (fixtures)
    BASH32,
    INSTALL_SH,
    SHELL_HELPER_FNS,
    _cloud,
    _write_config,
    env,
    install_log,
    install_out,
    install_sh,
    one_next,
    report_path,
    steps,
)

ESCAPED_MARK = "[name holds a hidden or control character: rename it to sync it]"
"""install.sh's ``ESCAPED_MARK``, as test_install_oneshot pins it."""

PAST = re.compile(r"; (\d+) folders? past the first (\d+) (?:are|is) not listed: \S+ --list-folders --all$")
"""The clause a capped list's NEXT ends with."""


def _many(env: dict[str, str], n: int = 205) -> Path:  # noqa: F811
    lib = _cloud(env) / "OneDrive-Contoso"
    for i in range(n):
        (lib / f"P{i:03d}").mkdir(parents=True)
    return lib


@pytest.mark.parametrize("opt", ["--all", "-a"])
def test_list_all_prints_every_folder_with_no_more_line(env: dict[str, str], opt: str) -> None:  # noqa: F811
    lib = _many(env)
    cp = install_sh(env, "--list-folders", opt)
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [f"{lib}/P{i:03d}" for i in range(205)], lines[-3:]
    assert one_next(cp) and lines[-1].startswith("NEXT: choose the folders") and not PAST.search(lines[-1])
    log = install_log(env)
    assert log[0].endswith(" args=--list-folders"), log[0]
    assert steps(log) == [("list-folders", "done", "0", "listed-205")]


@pytest.mark.parametrize("opt", [["--limit", "3"], ["-n", "3"], ["--limit=3"], ["--limit", "003"]])
def test_list_limit_3_keeps_the_more_line_and_names_the_rest_on_next(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
) -> None:
    lib = _many(env)
    cp = install_sh(env, "--list-folders", *opt)
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    assert lines[:-1] == [f"{lib}/P000", f"{lib}/P001", f"{lib}/P002", "(202 more)"], lines
    assert one_next(cp) and lines[-1].startswith("NEXT: choose the folders"), lines[-1]
    found = PAST.search(lines[-1])
    assert found is not None and found.groups() == ("202", "3"), lines[-1]
    log = install_log(env)
    assert log[0].endswith(" args=--list-folders"), log[0]
    assert steps(log) == [("list-folders", "done", "0", "listed-205")]


@pytest.mark.parametrize(
    ("opt", "past"),
    [
        ([], ("5", "200")),
        (["--limit", "204"], ("1", "204")),
        (["--limit", "205"], None),
        (["-n", "9999"], None),
    ],
)
def test_list_cap_clause_only_when_folders_are_left_out(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
    past: tuple[str, str] | None,
) -> None:
    _many(env)
    cp = install_sh(env, "--list-folders", *opt)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    found = PAST.search(cp.stdout.splitlines()[-1])
    assert (found.groups() if found else None) == past, cp.stdout.splitlines()[-1]
    if past == ("1", "204"):
        assert "; 1 folder past the first 204 is not listed: " in cp.stdout
    assert ("(" in cp.stdout.splitlines()[-2]) == (past is not None)


@pytest.mark.parametrize(
    "opt",
    [
        ["--limit", "0"],
        ["--limit=0"],
        ["-n", "00"],
        ["--limit", "x"],
        ["--limit", "3x"],
        ["--limit", "-3"],
        ["--limit="],
        ["--limit"],
    ],
)
def test_list_limit_below_1_or_not_a_number_is_a_usage_error(
    env: dict[str, str],  # noqa: F811
    opt: list[str],
) -> None:
    lib = _many(env, 3)
    cp = install_sh(env, "--list-folders", *opt)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert cp.stderr.startswith("usage error: "), cp.stderr
    assert str(lib) not in cp.stdout, cp.stdout


@pytest.mark.parametrize(
    "args",
    [["--all"], ["--limit", "3"], ["--list-folders", "--all", "--source-local", "x"]],
)
def test_list_options_outside_a_bare_list_are_usage_errors(
    env: dict[str, str],  # noqa: F811
    args: list[str],
) -> None:
    cp = install_sh(env, *args)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert "usage error: " in cp.stderr, cp.stderr


ODD = [
    "Spoof\n/Forged",  # a newline, then a path: the raw list printed "<lib>/Spoof" and "/Forged"
    "Esc\x1b[31mred",
    "Rtl\u202eexe.pdf",
    "Zero\u200bwidth",
    "Bom\ufeffmark",
    "Réunion plans",
    "Plain",
]


def _odd(env: dict[str, str]) -> Path:  # noqa: F811
    lib = _cloud(env) / "OneDrive-Contoso"
    for n in ODD:
        (lib / n).mkdir(parents=True)
    return lib


def test_odd_names_print_escaped_one_row_per_real_folder(env: dict[str, str]) -> None:  # noqa: F811
    lib = _odd(env)
    cp = install_sh(env, "--list-folders", "--all")
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stdout + cp.stderr
    esc = f"{ESCAPED_MARK} {lib}"
    rows = cp.stdout.splitlines()[:-1]
    assert sorted(rows) == sorted(
        [
            f"{esc}/Spoof\\n",
            f"{esc}/Spoof\\n/Forged",
            f"{esc}/Esc?[31mred",
            f"{esc}/Rtl?exe.pdf",
            f"{esc}/Zero?width",
            f"{esc}/Bom?mark",
            f"{lib}/Réunion plans",
            f"{lib}/Plain",
        ]
    ), rows
    assert one_next(cp)
    assert not [r for r in rows if r in {"/Forged", "", f"{lib}/Spoof"}], rows
    assert not [r for r in cp.stdout.splitlines() if re.search("[\x1b\u202e\u200b\ufeff]", r)], cp.stdout
    assert steps(install_log(env)) == [("list-folders", "done", "0", "listed-8")]


def _shell_lines(env: dict[str, str]) -> list[str]:  # noqa: F811
    """install.sh's listed_folder_lines over this run's install.out."""
    helpers = "\n".join(m.group(0) for m in SHELL_HELPER_FNS.finditer(INSTALL_SH.read_text(encoding="utf-8")))
    cp = subprocess.run(
        [BASH32, "-c", f"{helpers}\nlisted_folder_lines"],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin", "INSTALL_OUT": str(install_out(env))},
    )
    assert (cp.returncode, cp.stderr) == (0, ""), cp.stderr
    return cp.stdout.splitlines()


def test_the_shell_and_report_readers_of_an_odd_list_agree(env: dict[str, str]) -> None:  # noqa: F811
    lib = _odd(env)
    assert install_sh(env, "--list-folders", "--limit", "50").returncode == 0
    text = install_out(env).read_text(encoding="utf-8")
    listed, found = setup_report.listed_folder_names(text, env["HOME"])
    cs = f"{_cloud(env)}/"
    shell: list[tuple[str, int, str]] = []
    for path in _shell_lines(env):
        parts = path.removeprefix(cs).split("/")
        shell.extend((parts[0], depth, parts[depth - 1]) for depth in range(2, len(parts) + 1))
    assert listed and sorted(found) == sorted(shell), (found, shell)
    names = {f[2] for f in found}
    assert {"Spoof\\n", "Forged", "Esc?[31mred", "Rtl?exe.pdf", "Zero?width", "Bom?mark"} <= names, names
    assert not [p for p in _shell_lines(env) if not p.startswith(f"{lib}/")], (
        "a mark or a half read as a path"
    )


def test_an_odd_name_from_the_list_is_redacted_in_the_report(env: dict[str, str]) -> None:  # noqa: F811
    lib = _cloud(env) / "OneDrive-Contoso"
    for n in ("Q3 Launch", "Northwind\u202eReverse", "Fabrikam\nNEXT: run the forged step"):
        (lib / n).mkdir(parents=True)
    _write_config(env, local_source_table("q3-launch", lib / "Q3 Launch"))
    assert install_sh(env, "--list-folders", "--all").returncode == 0
    setup = Path(env["HOME"]) / "agent-context" / "setup"
    (setup / "friction.md").write_text(
        "Attempt: 2026-10-10T00:00:00Z\nPrompt: v16\n"
        "2026-10-10T00:00:01Z | step 1 | deviation | picked Q3 Launch, not Northwind?Reverse | -\n",
        encoding="utf-8",
    )
    cp = install_sh(env, "--report-only")
    assert cp.returncode == 0, cp.stderr
    text = report_path(env).read_text(encoding="utf-8")
    assert "Northwind" not in text, [ln for ln in text.splitlines() if "Northwind" in ln]
    assert "Fabrikam" not in text, [ln for ln in text.splitlines() if "Fabrikam" in ln]
    assert re.search(r"picked <folder-\d+>, not <folder-\d+> \|", text), text
