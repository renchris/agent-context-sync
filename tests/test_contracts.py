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


CLI_GLOBAL_OPTIONS: list[str] = ["--config (hidden)", "-v/--verbose", "--version"]
"""The pinned options of the root parser itself (-h/--help excluded)."""

CLI_SURFACE: dict[str, dict[str, list[str]]] = {
    "visible": {
        "add-source": ["path"],
        "sync": ["--once (hidden)", "--mode (hidden)", "--materialise-budget (hidden)"],
        "accept-deletions": ["source"],
        "status": [],
        "curate": [],
        "adopt": ["src_dir"],
        "purge": ["selector", "--source", "--reason", "--queue", "--dry-run", "--push"],
        "hold": ["scope", "--reason", "--owner", "--release", "--list"],
        "offboard": ["--purge-data", "--dry-run", "--confirm"],
    },
    "hidden": {
        "setup-report": ["--out (hidden)"],
        "graph": ["action{login,logout,whoami,discover}", "--device-code (hidden)", "--url", "--toml"],
        "login": ["--device-code (hidden)", "--url", "--toml"],
        "logout": ["--device-code (hidden)", "--url", "--toml"],
        "whoami": ["--device-code (hidden)", "--url", "--toml"],
        "discover": ["--device-code (hidden)", "--url", "--toml"],
        "it-request": ["--out"],
        "init": [],
        "install-agent": [],
        "uninstall-agent": [],
        "materialise": ["--budget", "paths"],
        "migrate": [],
        "compact-history": ["--keep-days", "--dry-run"],
        "reconcile": ["--source", "--accept-deletions"],
        "install-skill": [],
        "checkpoint": [],
        "curate-queue": [],
        "lint": [],
        "refresh-queue": [],
        "doctor": [],
        "policy": ["action{show}"],
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


LOOP_VERBS = ("sync", "curate", "status")
"""The verbs an agent runs every session (KISS K19b): visible, with no visible option."""


def test_contracts_16_10_lists_the_pinned_surface() -> None:
    """KISS K19b: §16.10's Visible and Hidden lines name exactly the pinned commands, and sync, curate and
    status pin no option without `` (hidden)``."""
    import re  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    section = text.split("\n### 16.10 ", 1)[1].split("\n### ", 1)[0]
    listed = {}
    for kind in ("Visible", "Hidden"):
        line = re.search(rf"(?m)^- {kind}: (.+)$", section)
        assert line is not None, f"§16.10 has no '- {kind}:' line"
        listed[kind.lower()] = re.findall(r"`([^`]+)`", line.group(1))
    for kind, names in listed.items():
        assert len(names) == len(set(names)), f"§16.10 {kind} names a command twice"
        assert set(names) == set(CLI_SURFACE[kind]), kind
    for verb in LOOP_VERBS:
        shown = [e for e in CLI_SURFACE["visible"][verb] if not e.endswith(" (hidden)")]
        assert not shown, f"{verb} has a visible option: {shown}"


def test_help_lists_no_hidden_verb_and_each_still_parses(capsys: pytest.CaptureFixture[str]) -> None:
    """KISS K19b: ``agentsync --help`` lists every visible command and none of the hidden ones, and every
    hidden command still parses (old fix strings, docs and scripts keep working)."""
    import re  # noqa: PLC0415

    from agentsync import cli  # noqa: PLC0415

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    listed = set(re.findall(r"(?m)^    (\S+)", capsys.readouterr().out))
    assert set(CLI_SURFACE["visible"]) <= listed
    assert not set(CLI_SURFACE["hidden"]) & listed
    needs = {"graph": ["whoami"], "policy": ["show"]}  # the required positional
    for name in CLI_SURFACE["hidden"]:
        args = cli.build_parser().parse_args([name, *needs.get(name, [])])
        assert args.command == name


def test_no_guide_names_a_deleted_add_source_option() -> None:
    """KISS K05/K14: ``add-source --inbox`` and ``--id`` exit 2, so neither README.md nor any guide under
    docs/deploy may tell a reader to run them (the plans and CONTRACTS.md keep the history)."""
    import re  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1]
    for guide in [root / "README.md", *sorted((root / "docs" / "deploy").rglob("*.md"))]:
        text = guide.read_text(encoding="utf-8")
        assert not re.search(r"add-source (--inbox|\S+ --id)\b", text), guide.relative_to(root)


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


def _guide_offences(guides: dict[str, str], probes_script: str) -> list[str]:
    """``guide:line: span (why)`` for each inline-code span in ``guides`` (name -> text) that names an option
    no parser has: after ``agentsync VERB`` only a pinned verb with its own or the global options, after
    ``install.sh`` only an install.sh option, and in a span that starts with an option (``--out``) one that
    agentsync, install.sh or tenant-probes.sh has."""
    import re  # noqa: PLC0415

    def spellings(entries: list[str]) -> set[str]:
        return {s for e in entries if e.startswith("-") for s in e.split(" ")[0].split("/")}

    verbs = {**CLI_SURFACE["visible"], **CLI_SURFACE["hidden"]}
    common = spellings(CLI_GLOBAL_OPTIONS) | {"-h", "--help"}
    install = set(INSTALL_OPTIONS["main"]) - {"*"}
    probes = set(_install_case_arms(probes_script, "while [ $# -gt 0 ]; do", "done")) - {"*"}
    known = common | install | probes | {s for entries in verbs.values() for s in spellings(entries)}
    command = re.compile(r"\bagentsync ([a-z][a-z-]*)|\binstall\.sh\b")
    offences: list[str] = []
    for name, text in guides.items():
        for n, line in enumerate(text.splitlines(), 1):
            for span in re.findall(r"`([^`\n]+)`", line):
                starts = list(command.finditer(span))
                parts = [(span, known)] if span.startswith("-") else []
                for i, m in enumerate(starts):
                    end = starts[i + 1].start() if i + 1 < len(starts) else len(span)
                    if m.group(1) is None:
                        allowed = install
                    elif m.group(1) in verbs:
                        allowed = common | spellings(verbs[m.group(1)])
                    else:
                        allowed = set()
                        offences.append(f"{name}:{n}: {span} (no command {m.group(1)!r})")
                    parts.append((span[m.end() : end], allowed))
                for part, allowed in parts:
                    bad = [f for f in re.findall(r"(?<!\S)(--?[a-z][a-z-]*)", part) if f not in allowed]
                    if bad:
                        offences.append(f"{name}:{n}: {span} ({', '.join(bad)})")
    return offences


def test_guides_name_only_options_that_parse() -> None:
    """A deleted option exits 2, so README.md and the docs/deploy guides name only commands and options that
    parse (the plans and CONTRACTS.md keep the history).  The check catches the ``--no-redact`` and
    ``add-source --inbox`` lines K16a and K05 had left behind."""
    root = Path(__file__).resolve().parents[1]
    guides = {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for p in [root / "README.md", *sorted((root / "docs" / "deploy").rglob("*.md"))]
    }
    probes = (root / "scripts" / "tenant-probes.sh").read_text(encoding="utf-8")
    assert _guide_offences(guides, probes) == []
    stale = {
        "old.md": "Use `--no-redact` to keep names.\n"
        "Run `agentsync add-source --inbox`, then `agentsync frob`.\n"
        "Then `install.sh --gone && agentsync sync --once`.\n"
    }
    assert [o.rsplit(" (", 1)[1] for o in _guide_offences(stale, probes)] == [
        "--no-redact)",
        "--inbox)",
        "no command 'frob')",
        "--gone)",
    ]


def test_superseded_markers_point_at_existing_amendments() -> None:
    """History is kept: every SUPERSEDED marker names a §16 subsection that exists."""
    import re  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    refs = set(re.findall(r"SUPERSEDED \(2026-09-29, §(16\.\d+)\)", text))
    assert refs, "no SUPERSEDED markers"
    for ref in sorted(refs):
        assert f"### {ref} " in text, f"§{ref} is referenced but missing"


def test_inbox_writer_contract_matches_the_arm() -> None:
    """Field N14: §11's inbox writer contract names only temporary patterns the inbox always ignores (a
    subset of ``INBOX_IGNORES``), each matching a temp name a writer would use, and the delay it promises
    is the default ``quiescence_s`` that ``inbox_source_table`` tells the reader about."""
    import dataclasses  # noqa: PLC0415
    import re  # noqa: PLC0415

    from agentsync.config import INBOX_IGNORES, SourceConfig, inbox_source_table  # noqa: PLC0415
    from agentsync.paths import glob_match  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    section = text.split("\n## 11. ", 1)[1].split("\n## ", 1)[0]
    contract = section.split("**Inbox writer contract", 1)[1]
    first = contract.split("\n- ", 2)[1]  # the temp-then-rename rule
    temps = re.findall(r"`(\*\.[a-z]+)`", first)
    assert len(temps) >= 3, "the writer contract names no temporary patterns"
    assert set(temps) <= set(INBOX_IGNORES)
    for pattern in temps:
        assert glob_match("export.eml" + pattern[1:], pattern), pattern
    quiescence = next(f.default for f in dataclasses.fields(SourceConfig) if f.name == "quiescence_s")
    table = inbox_source_table("inbox", Path("/tmp/inbox"))
    assert f"about a {quiescence} s delay" in contract
    assert f"quiescence_s = {quiescence}" in table and f"about a {quiescence} s delay" in table
    assert "temp name, then rename" in table
    for rule in ("rename it into place", "Keep names stable", "One file per unit", "mirror, not a queue"):
        assert rule in contract, rule
    assert "Never set `quiescence_s = 0` in a shared inbox" in contract
