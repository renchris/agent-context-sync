"""Every module imports, and every public name it defines is documented in docs/design/CONTRACTS.md."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import inspect
import pkgutil
import re
from collections import Counter
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
        "exclude-empty": ["source"],
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
        "--confirm-install-agent",
        "--report-only",
        "--list-folders",
        "--no-report",
        "--log-start",
        "--log",
        "--log-end",
        "--launcher",
        "--source-local",
        "--version",
        "-h",
        "--help",
        "*",
    ],
}
"""The pinned install.sh options, per case block: the first-argument dispatch and the main option loop.
KISS K17 (2026-10-05) deleted --dry-run (now AGENTSYNC_INSTALL_DRY_RUN=1) and --config; --no-report and
--log-end (folded into --report-only) stay as the hidden INSTALL_HIDDEN arms; ``*`` is the SOURCE test seam,
left out of --help. KISS K11b (2026-10-05) turned --rebuild-launcher into the developer variable
AGENTSYNC_REBUILD_LAUNCHER=1."""
INSTALL_HIDDEN = {"--no-report", "--log-end"}
"""Kept out of --help and the guides, still parsed: a saved v6 prompt (its compat gate reads "6 or higher")
runs ``--log-end && --report-only``, and the old baseline prompt's upgrade line
``--no-report && ... install-skill``
(K17 review). --log-end closes the attempt like --report-only; --no-report is ignored."""


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
    needs = {"graph": ["whoami"], "policy": ["show"], "exclude-empty": ["work"]}  # the required positional
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
    install = set(INSTALL_OPTIONS["main"]) - {"*"} - INSTALL_HIDDEN
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


def _dataclass_stubs(text: str) -> dict[tuple[str, str], list[str]]:
    """The first stub of each class in the python blocks under a ``###`` or ``#### `agentsync.<mod>```
    header (§15's module reference and the §16 module sections; B13 round 5 F86), with the names of its
    ``    name: type`` lines in order.  A later partial block (an
    amendment that repeats a class to add a field) is not the stub and is skipped."""
    stubs: dict[tuple[str, str], list[str]] = {}
    seen: set[str] = set()
    module: str | None = None
    in_fence = in_block = in_doc = False
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("```"):
            # any fence opens and closes: a `# comment` in a toml or text block is not a heading
            in_fence, current = not in_fence, None
            in_block = in_fence and line.strip() == "```python" and module is not None
            continue
        if not in_fence:
            header = re.match(r"#{3,4} `(agentsync(?:\.\w+)+)`", line)
            if header or re.match(r"#{1,4} ", line):
                module = header.group(1) if header else None
            continue
        if not in_block:
            continue
        if in_doc:
            in_doc = '"""' not in line
            continue
        cls = re.match(r"class (\w+)\b", line)
        if cls:
            current = None if cls.group(1) in seen else stubs.setdefault((module or "", cls.group(1)), [])
            seen.add(cls.group(1))
        elif line and not line.startswith(" "):
            current = None
        elif current is not None and line.strip().startswith('"""'):
            in_doc = line.strip().count('"""') == 1
        elif current is not None and (field := re.match(r"    (\w+): ", line)):
            current.append(field.group(1))
    return stubs


def test_the_registry_stub_has_the_code_signatures() -> None:
    """B13 round 13 O55: §15's `Registry` stub still read `__init__(self, converters)`, `default(cls, cfg)`
    and eight converters, and had no `policy`, `without_ocr`, `without_speech`, `screen`,
    `label_withheld_suffixes` or `sidecar_digest_lines`. Its python block now has every public member of the
    module and of the class, each with the code's parameter names, kinds and defaults; the old signatures
    are SUPERSEDED comments, each change under its own date and section."""
    import ast  # noqa: PLC0415
    from collections.abc import Callable  # noqa: PLC0415

    from agentsync.convert import registry  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    section = text.split("### `agentsync.convert.registry`", 1)[1]
    block = section.split("```python\n", 1)[1].split("\n```", 1)[0]
    assert "# SUPERSEDED (2026-09-29, §16.6): __init__(self, converters: Sequence[Converter])" in block, block
    assert "# SUPERSEDED (2026-09-29, §16.6; 2026-10-06, §16.26;" in block, block

    def params(fn: ast.FunctionDef) -> list[tuple[str, str, str | None]]:
        a = fn.args
        pos = [*a.posonlyargs, *a.args]
        defaults = [None] * (len(pos) - len(a.defaults)) + [ast.unparse(d) for d in a.defaults]
        out = [(x.arg, "POSITIONAL_OR_KEYWORD", d) for x, d in zip(pos, defaults, strict=True)]
        kw = [None if d is None else ast.unparse(d) for d in a.kw_defaults]
        return out + [(x.arg, "KEYWORD_ONLY", d) for x, d in zip(a.kwonlyargs, kw, strict=True)]

    def code(obj: Callable[..., object]) -> list[tuple[str, str, str | None]]:
        empty = inspect.Parameter.empty
        found = inspect.signature(obj).parameters.values()
        return [(p.name, p.kind.name, None if p.default is empty else repr(p.default)) for p in found]

    tree = ast.parse(block)
    top = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name in public_names("agentsync.convert.registry"):
        if not name.isupper():  # a constant is §16's
            assert name in top, f"§15 registry stub lacks {name}"
    stub = top["Registry"]
    assert isinstance(stub, ast.ClassDef)
    members = {n.name: n for n in stub.body if isinstance(n, ast.FunctionDef)}
    public = [n for n in vars(registry.Registry) if not n.startswith("_") or n == "__init__"]
    assert sorted(public) == sorted(members), (sorted(public), sorted(members))
    for name, fn in members.items():
        obj = inspect.getattr_static(registry.Registry, name)
        if isinstance(obj, property):
            continue
        func = obj.__func__ if isinstance(obj, classmethod) else obj
        assert params(fn) == code(func), name
    fn = top["label_withheld_suffixes"]
    assert isinstance(fn, ast.FunctionDef) and params(fn) == code(registry.label_withheld_suffixes)


def test_dataclass_stubs_list_the_code_fields() -> None:
    """Q87 (B13 round 4): the contract checked that each public name appears, not a dataclass stub's fields,
    so `PurgeReport` lacked `not_run` from §16.38 to §16.45 and `Suppressions` and `GovernanceConfig` were
    still short of fields added only in prose. Each module section's stub lists the code's fields, in
    order, §15's as well as §16's."""
    stubs = _dataclass_stubs(CONTRACTS.read_text(encoding="utf-8"))
    checked = []
    for (module_name, cls_name), fields in stubs.items():
        cls = getattr(importlib.import_module(module_name), cls_name, None)
        if cls is None or not dataclasses.is_dataclass(cls):
            continue
        checked.append(cls_name)
        assert fields == [f.name for f in dataclasses.fields(cls)], f"{module_name}.{cls_name}"
    # B13 round 5 F86: §15's module reference (### headers) too, where 13 stubs had fallen behind
    assert {"PurgeReport", "Suppressions", "GovernanceConfig", "ProxySettings", "CycleReport"} <= set(checked)
    assert len(checked) >= 65


def test_no_private_function_in_src_is_left_for_the_tests_alone() -> None:
    """B13 round 15 S46, S69: doctor's ``_pandoc_path`` and convert.pandoc's ``_bundled_pandoc`` stayed in
    src as one-line wrappers after their last caller moved to the function they wrap, called by tests only.
    Every module-level private function in src is named in src outside its own ``def``."""
    texts = [p.read_text(encoding="utf-8") for p in sorted(Path(agentsync.__file__).parent.rglob("*.py"))]
    named = Counter(n for t in texts for n in re.findall(r"\b_[A-Za-z]\w*", t))
    defs = [d for t in texts for d in re.findall(r"^(?:async )?def (_[A-Za-z]\w*)\(", t, re.M)]
    assert len(defs) > 500, "the scan is broken"
    assert sorted(d for d in defs if named[d] == 1) == []
