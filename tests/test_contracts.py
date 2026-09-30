"""Every module imports, and every public name it defines is documented in docs/design/CONTRACTS.md."""

from __future__ import annotations

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


def test_every_cli_subcommand_is_in_the_contract() -> None:
    """The CLI table (§16.10 and the cli section) names every subcommand the parser accepts."""
    import argparse  # noqa: PLC0415

    from agentsync import cli  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    missing = [name for name in sub.choices if name not in text]
    assert not missing, f"CLI subcommands missing from CONTRACTS.md: {missing}"


def test_superseded_markers_point_at_existing_amendments() -> None:
    """History is kept: every SUPERSEDED marker names a §16 subsection that exists."""
    import re  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    refs = set(re.findall(r"SUPERSEDED \(2026-09-29, §(16\.\d+)\)", text))
    assert refs, "no SUPERSEDED markers"
    for ref in sorted(refs):
        assert f"### {ref} " in text, f"§{ref} is referenced but missing"
