"""Every module imports, and every public name it defines is documented in docs/design/CONTRACTS.md."""

from __future__ import annotations

import argparse
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import agentsync

CONTRACTS = Path(__file__).resolve().parents[1] / "docs" / "design" / "CONTRACTS.md"


def all_modules() -> list[str]:
    names = ["agentsync"]
    for info in pkgutil.walk_packages(agentsync.__path__, prefix="agentsync."):
        if info.name != "agentsync.__main__":
            names.append(info.name)
    return sorted(names)


def public_names(module_name: str) -> list[str]:
    mod = importlib.import_module(module_name)
    out = []
    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        defined_here = (inspect.isfunction(obj) or inspect.isclass(obj)) and obj.__module__ == module_name
        is_constant = name.isupper() and not inspect.ismodule(obj)
        if defined_here or is_constant:
            out.append(name)
    return sorted(out)


@pytest.mark.parametrize("module_name", all_modules())
def test_module_imports_and_is_in_contract(module_name: str) -> None:
    text = CONTRACTS.read_text(encoding="utf-8")
    names = public_names(module_name)
    if names and module_name != "agentsync":
        assert f"`{module_name}`" in text, f"{module_name} has no section in CONTRACTS.md"
    missing = [n for n in names if n not in text]
    assert not missing, f"{module_name}: undocumented public names {missing}"


CLI_GLOBAL_OPTIONS: list[str] = ["--config", "-v/--verbose", "--version"]
"""The pinned options of the root parser itself (-h/--help excluded)."""

CLI_SURFACE: dict[str, dict[str, list[str]]] = {
    "visible": {
        "init": ["--docs-repo", "--source-local", "--force"],
        "add-source": ["path", "--inbox", "--id"],
        "sync": ["--once", "--mode", "--dry-run", "--source", "--materialise-budget"],
        "accept-deletions": ["source"],
        "status": [],
        "doctor": ["--network"],
        "lint": [],
        "refresh-queue": [],
        "curate-queue": [],
        "materialise": ["--budget", "paths"],
        "adopt": ["src_dir"],
        "migrate": [],
        "graph": ["action{login,logout,whoami,discover}", "--device-code", "--url", "--toml"],
        "login": ["--device-code", "--url", "--toml"],
        "logout": ["--device-code", "--url", "--toml"],
        "whoami": ["--device-code", "--url", "--toml"],
        "discover": ["--device-code", "--url", "--toml"],
        "install-agent": ["--interval", "--reconcile-interval", "--no-backup-exclusions"],
        "uninstall-agent": [],
        "purge": ["selector", "--source", "--reason", "--queue", "--dry-run", "--push"],
        "compact-history": ["--keep-days", "--dry-run"],
        "hold": ["scope", "--reason", "--owner", "--release", "--list"],
        "offboard": ["--purge-data", "--dry-run", "--confirm"],
        "policy": ["action{show}"],
        "setup-report": ["--out", "--friction", "--no-redact"],
        "it-request": ["--out"],
    },
    "hidden": {
        "reconcile": ["--source", "--accept-deletions"],
        "install-skill": [],
        "checkpoint": [],
    },
}
"""The pinned CLI surface: subcommand -> its options (every spelling, ``a/b``) and positionals (dest, plus
``{choices}`` when it has them), in declaration order; the common --config/-v and -h are excluded. An option
whose help is suppressed carries `` (hidden)``. Adding, hiding or removing a command, option, spelling,
positional or choice is a deliberate edit here, in the same commit as the change."""

INSTALL_OPTIONS: dict[str, list[str]] = {
    "first-argument": ["--log-start", "--log", "--log-end"],
    "main": [
        "--dry-run",
        "--confirm-install-agent",
        "--rebuild-launcher",
        "--no-report",
        "--report-only",
        "--list-folders",
        "--log-start",
        "--log",
        "--log-end",
        "--launcher",
        "--config",
        "--source-local",
        "--version",
        "-h",
        "--help",
        "*",
    ],
}
"""The pinned install.sh options, per case block: the first-argument dispatch and the main option loop."""


def _cli_entry(action: argparse.Action) -> str:
    """One pinned entry: every spelling of an option (``-v/--verbose``), or a positional's dest with its
    choices (``action{show}``); `` (hidden)`` when its help is suppressed."""
    if action.option_strings:
        entry = "/".join(action.option_strings)
    else:
        entry = action.dest + ("{" + ",".join(map(str, action.choices)) + "}" if action.choices else "")
    return entry + (" (hidden)" if action.help is argparse.SUPPRESS else "")


def _cli_surface() -> tuple[list[str], dict[str, dict[str, list[str]]]]:
    """The root parser's own options, and each subcommand's options and positionals by visibility."""
    from agentsync import cli  # noqa: PLC0415

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    root = [
        _cli_entry(a)
        for a in parser._actions
        if not isinstance(a, argparse._HelpAction | argparse._SubParsersAction)
    ]
    shown = {a.dest for a in sub._choices_actions if a.help is not argparse.SUPPRESS}
    common = {"-h", "--help", "--config", "-v", "--verbose"}
    surface: dict[str, dict[str, list[str]]] = {"visible": {}, "hidden": {}}
    for name, sp in sub.choices.items():
        entries = [_cli_entry(a) for a in sp._actions if not common & set(a.option_strings)]
        surface["visible" if name in shown else "hidden"][name] = entries
    return root, surface


def test_cli_surface_is_frozen() -> None:
    """The root options and every subcommand, visible or hidden, with every option and positional equal
    the pinned surface; each
    subcommand is also named in CONTRACTS.md (§16.10 and the cli section)."""
    root, surface = _cli_surface()
    assert root == CLI_GLOBAL_OPTIONS
    assert surface == CLI_SURFACE
    text = CONTRACTS.read_text(encoding="utf-8")
    missing = [name for kind in surface.values() for name in kind if name not in text]
    assert not missing, f"CLI subcommands missing from CONTRACTS.md: {missing}"


def _install_case_arms(script: str, opener: str, closer: str) -> list[str]:
    """The option patterns of the case arms (at most one tab deep; ``*`` is the positional SOURCE arm, the
    ``-*`` unknown-option arm is dropped) between ``opener`` and the next column-0
    ``closer``."""
    import re  # noqa: PLC0415

    block = script.split(f"\n{opener}\n", 1)[1].split(f"\n{closer}\n", 1)[0]
    arms: list[str] = []
    for m in re.finditer(r"^\t?(-[^)\n]*|\*)\)", block, flags=re.MULTILINE):
        arms += [o for o in m.group(1).split(" | ") if o != "-*"]
    return arms


def test_install_options_are_frozen() -> None:
    """Both install.sh option case blocks equal the pinned options, and every flag the README's one prompt
    names (test_deploy_pack.py) is one of them."""
    from test_deploy_pack import ONE_PROMPT_INSTALL_FLAGS  # noqa: PLC0415

    script = (Path(__file__).resolve().parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    found = {
        "first-argument": _install_case_arms(script, 'case "${1:-}" in', "esac"),
        "main": _install_case_arms(script, "while [ $# -gt 0 ]; do", "done"),
    }
    assert found == INSTALL_OPTIONS
    assert set(INSTALL_OPTIONS["main"]) >= ONE_PROMPT_INSTALL_FLAGS


def test_superseded_markers_point_at_existing_amendments() -> None:
    """History is kept: every SUPERSEDED marker names a §16 subsection that exists."""
    import re  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    refs = set(re.findall(r"SUPERSEDED \(2026-09-29, §(16\.\d+)\)", text))
    assert refs, "no SUPERSEDED markers"
    for ref in sorted(refs):
        assert f"### {ref} " in text, f"§{ref} is referenced but missing"
