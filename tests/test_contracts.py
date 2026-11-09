"""Every module imports, and every public name it defines is documented in docs/design/CONTRACTS.md."""

from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib
import inspect
import pkgutil
import re
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


CLI_GLOBAL_OPTIONS: list[str] = ["--config (hidden)", "-v/--verbose", "--json", "--version"]
"""The pinned options of the root parser itself (-h/--help excluded)."""

CLI_SURFACE: dict[str, dict[str, list[str]]] = {
    "visible": {
        "add-source": ["path"],
        "sync": ["--once (hidden)", "--mode (hidden)", "--materialise-budget (hidden)"],
        "accept-deletions": ["source"],
        "status": [],
        "curate": [],
        "adopt": ["src_dir"],
        "purge": [
            "selector",
            "--source",
            "--reason",
            "--queue",
            "--forget",
            "--accept-lost",
            "--folder-lost",
            "--dry-run",
            "--push",
        ],
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
        "-a",
        "--all",
        "-n",
        "--limit",
        "--limit=*",
        "--json",
        "--no-report",
        "--log-start",
        "--log",
        "--log-end",
        "--launcher",
        "--launcher=*",
        "--source-local",
        "--source-local=*",
        "--version",
        "-h",
        "--help",
        "--",
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
    common = {"-h", "--help", "--config", "-v", "--verbose", "--json"}
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
    # the verb only where a command starts: the span's start, a path's last part, a prompt, "run", "sudo",
    # "then", a pipe or a list, or a bracket, so prose such as "the agentsync logged line" names none (B13
    # round 28 I103)
    command = re.compile(
        r"(?:^|(?<=/)|(?<=\$ )|(?<=\brun )|(?<=\bsudo )|(?<=\bthen )|(?<=[;|] )|(?<=&& )|(?<=\$\()|(?<=\()"
        r"|(?<=\( ))agentsync ([a-z][a-z-]*)|\binstall\.sh\b"
    )
    # an inline span as CommonMark pairs it, a backtick run (not an escaped one) closed by a run of the same
    # length, across a line break but not into a blank line, a list item, a table row, a heading or a quote
    block = r"\n[ \t]*(?:\n|[-*+] |\d+[.)] |\||#{1,6} |>)"
    inline = re.compile(rf"(?<![`\\])(`+)(?!`)((?:(?!{block}).)+?)(?<!`)\1(?!`)", re.S)
    offences: list[str] = []
    for name, text in guides.items():
        # a fenced block holds no inline span: blank it, keeping its newlines so the line numbers hold
        lines, fence = [], ""
        for line in text.split("\n"):
            mark = re.match(r"\s*(`{3,}(?![^\n]*`)|~{3,})", line)  # a backtick fence's info holds none
            lines.append("" if fence or mark else line)
            if not fence and mark:
                fence = mark.group(1)
            elif mark and mark.group(1).startswith(fence) and not line.strip()[len(mark.group(1)) :]:
                fence = ""
        flat = "\n".join(lines)
        for found in inline.finditer(flat):
            n = flat.count("\n", 0, found.start()) + 1
            span = found.group(2).replace("\n", " ")
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
    ``add-source --inbox`` lines K16a and K05 had left behind.  B13 round 28 I103: it read one line at a time,
    so a span that wraps (114 in README.md and docs/deploy, 43 of them naming a command or opening with an
    option) was never checked, and read "agentsync <word>" anywhere, prose included; it reads each span whole
    and the verb only where a command starts."""
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
    # B13 round 28 I103: a span that wraps is read whole, at the line it starts on; an odd backtick on a line
    # no longer pairs prose as code; prose "agentsync <word>" names no command; a fenced block is not read
    wrapped = {
        "wrap.md": "Run `agentsync add-source\n--inbox`.\n"
        "see `foo\nbar` and agentsync frob and `x` end\n"
        "Keep `the agentsync logged line` as it is.\n"
        "```sh\nagentsync frob `--x`\n```\n"
        "Then `$(agentsync frob)`.\n"
        "- run `x\n- `agentsync frob --x`\n"
        "```agentsync frob```, then \\`, and `sudo agentsync sync --gone`.\n"
    }
    assert _guide_offences(wrapped, probes) == [
        "wrap.md:1: agentsync add-source --inbox (--inbox)",
        "wrap.md:9: $(agentsync frob) (no command 'frob')",
        "wrap.md:11: agentsync frob --x (no command 'frob')",
        "wrap.md:11: agentsync frob --x (--x)",
        "wrap.md:12: agentsync frob (no command 'frob')",
        "wrap.md:12: sudo agentsync sync --gone (--gone)",
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


def test_the_office_suffix_snapshots_say_policys_eleven_c1038() -> None:
    """B13 round 36 C1038: §15's canonical stub still assigned six Office suffixes, though canonical has
    imported policy's list since §16.54 Q76, and §16.6's policy snapshot listed nine with no note that §16.42
    and §16.48 added `.vsdx` and `.vsdm`; §16.6's `Registry.default` chain stopped at `ocr`. §16 keeps
    replaced text as history, so each old value stays and a dated note gives today's. Every literal constant
    left in the canonical stub equals the module's, and each note's facts are read from the code."""
    from agentsync import policy  # noqa: PLC0415
    from agentsync.convert import canonical, registry  # noqa: PLC0415

    text = CONTRACTS.read_text(encoding="utf-8")
    section = text.split("### `agentsync.convert.canonical`", 1)[1]
    block = section.split("```python\n", 1)[1].split("\n```", 1)[0]
    tree = ast.parse(block)
    assert any(
        isinstance(n, ast.ImportFrom)
        and n.module == "agentsync.policy"
        and n.names[0].name == "OOXML_SUFFIXES"
        for n in tree.body
    )
    assert canonical.OOXML_SUFFIXES is policy.OOXML_SUFFIXES and len(policy.OOXML_SUFFIXES) == 11
    for node in tree.body:
        if isinstance(node, ast.AnnAssign | ast.Assign) and node.value is not None:
            [target] = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            assert isinstance(target, ast.Name)
            assert ast.literal_eval(node.value) == getattr(canonical, target.id), target.id
    flat = " ".join(text.split())
    note = "# amended 2026-11-08, §16.239 C1038 (by §16.42 and §16.48): gains '.vsdx' and '.vsdm', 11 entries"
    assert note in flat and {".vsdx", ".vsdm"} <= set(policy.OOXML_SUFFIXES)
    params = ", ".join(f"{p}=None" for p in list(inspect.signature(registry.Registry.default).parameters)[1:])
    assert params == "policy=None, ocr=None, media=None, pieces=None, speech=None"
    assert f"§16.239 C1038 (by §16.34 and §16.35):** `Registry.default(cfg, *, {params})`" in flat


_SECTION_15_TRUNCATED = frozenset(
    {
        ("agentsync.frontmatter", "MIRROR_KEY_ORDER"),
        ("agentsync.manifest", "SCHEMA_SQL"),
        ("agentsync.graph.mail", "MAIL_SELECT"),
        ("agentsync.curate", "REFRESH_QUEUE_SH"),
    }
)
"""§15's stubs that print only an assignment's first line and `# ... (see source)`: the four blocks that do
not parse as Python, each named so no block is skipped unseen."""


def _section_15_constants() -> list[tuple[str, str, object, str]]:
    """(module, name, value, the comment lines right after it) for every literal top-level constant in a §15
    module block; a truncated stub line is read as `NAME = ...` and must be in :data:`_SECTION_15_TRUNCATED`,
    and a starred name resolves against the block's earlier constants."""
    text = CONTRACTS.read_text(encoding="utf-8")
    start = text.index("\n## 15. Module reference")
    s15 = text[start : text.index("\n## 16. ", start)]
    found: list[tuple[str, str, object, str]] = []
    truncated: set[tuple[str, str]] = set()
    blocks = 0
    for head in re.finditer(r"^### `(agentsync[\w.]*)`(.*?)(?=^### |\Z)", s15, re.S | re.M):
        module = head.group(1)
        for block in re.findall(r"```python\n(.*?)\n```", head.group(2), re.S):
            blocks += 1
            lines = block.split("\n")
            for n, line in enumerate(lines):
                cut = re.match(r"(\w+)(?::[^=]*)? = .*# \.\.\. \(see source\)$", line)
                if cut:
                    truncated.add((module, cut.group(1)))
                    lines[n] = f"{cut.group(1)} = ..."
            tree = ast.parse("\n".join(lines))  # every block parses once its truncated lines are cut
            env: dict[str, object] = {}
            for node in tree.body:
                if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
                    continue
                [target] = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                if not isinstance(target, ast.Name) or (
                    isinstance(node.value, ast.Constant) and (node.value.value is Ellipsis)
                ):
                    continue
                try:
                    value = eval(
                        compile(ast.Expression(node.value), "<§15>", "eval"), {"__builtins__": {}}, dict(env)
                    )
                except NameError:  # a call or a pattern, not a snapshot of a value
                    continue
                env[target.id] = value
                after = []
                for line in lines[node.end_lineno :]:
                    if not line.startswith("#") and not line.startswith('"""'):
                        break
                    after.append(line.removeprefix("#").strip())
                found.append((module, target.id, value, " ".join(after)))
    assert truncated == _SECTION_15_TRUNCATED and blocks >= 35, "the scan is broken"
    return found


def test_every_section_15_constant_is_the_codes_or_says_what_superseded_it_c1287() -> None:
    """B13 round 37 C1287: C1038 fixed §15's canonical stub and left seven stale snapshots as "a later round's
    item": publish's MIRROR_CLAUDE_MD, TOPICS_CLAUDE_MD and GITATTRIBUTES, its removed ROOT_CLAUDE_MD,
    gitops' GENERATED_PATHSPECS and drive's DRIVE_SELECT (DELTA_HEADERS had its note), and C1038's
    literal_eval missed gitops' COMMIT_PATHSPECS behind its starred element. §16 keeps replaced text, so each
    stays as history with a dated SUPERSEDED note. Every literal constant in every §15 block (one block at a
    time, truncated stubs named) equals the module's, or a SUPERSEDED note naming it follows it, and each
    note's facts are read from the code."""
    from agentsync import gitops, policy, publish  # noqa: PLC0415
    from agentsync.graph import drive  # noqa: PLC0415

    noted: dict[tuple[str, str], str] = {}
    checked = 0
    for module, name, value, after in _section_15_constants():
        mod = importlib.import_module(module)
        checked += 1
        if hasattr(mod, name) and getattr(mod, name) == value:
            continue
        assert "SUPERSEDED (" in after and f"): {name} " in after, f"{module}.{name}"
        noted[(module, name)] = after
    assert checked >= 60, "the scan is broken"
    assert sorted(noted) == [
        ("agentsync.gitops", "COMMIT_PATHSPECS"),
        ("agentsync.gitops", "GENERATED_PATHSPECS"),
        ("agentsync.graph.drive", "DELTA_HEADERS"),
        ("agentsync.graph.drive", "DRIVE_SELECT"),
        ("agentsync.publish", "GITATTRIBUTES"),
        ("agentsync.publish", "MIRROR_CLAUDE_MD"),
        ("agentsync.publish", "ROOT_CLAUDE_MD"),
        ("agentsync.publish", "TOPICS_CLAUDE_MD"),
    ]
    ours = "noted 2026-11-10, §16.244 C1287): "
    assert all(ours in note for key, note in noted.items() if key[1] != "DELTA_HEADERS")
    delta = noted[("agentsync.graph.drive", "DELTA_HEADERS")].split("DELTA_HEADERS == ", 1)[1]
    assert ast.literal_eval(delta) == drive.DELTA_HEADERS

    def gains(module: str, name: str) -> list[str]:
        return re.findall(r"'([^']+)'", noted[(module, name)].split(f"): {name} gains ", 1)[1])

    assert set(gains("agentsync.graph.drive", "DRIVE_SELECT")) == (
        set(drive.DRIVE_SELECT.split(",")) - set(dict(_section_15_values())["DRIVE_SELECT"].split(","))
    )
    gen = gains("agentsync.gitops", "GENERATED_PATHSPECS")
    assert gen == ["archive", "mirror"] and gitops.GENERATED_PATHSPECS[:2] == ("mirror", "archive")
    stub = dict(_section_15_values())
    own = gitops.COMMIT_PATHSPECS[len(gitops.GENERATED_PATHSPECS) :]  # past the starred element
    assert gitops.COMMIT_PATHSPECS[: len(gitops.GENERATED_PATHSPECS)] == gitops.GENERATED_PATHSPECS
    added = [p for p in own if p not in stub["COMMIT_PATHSPECS"]]
    assert sorted(added) == sorted(gains("agentsync.gitops", "COMMIT_PATHSPECS"))
    attrs = gains("agentsync.publish", "GITATTRIBUTES")
    assert attrs == ["*.jpg binary", "*.png binary"]
    assert (
        stub["GITATTRIBUTES"].replace("*.png binary\n", "*.png binary\n*.jpg binary\n")
        == publish.GITATTRIBUTES
    )
    mirror = noted[("agentsync.publish", "MIRROR_CLAUDE_MD")]
    [heading] = re.findall(r'opens "(# mirror/[^"]+)"', mirror)
    assert publish.MIRROR_CLAUDE_MD.startswith(heading + "\n\n" + policy.BOUNDARY_TEXT)
    assert "then policy.BOUNDARY_TEXT" in mirror
    topics = noted[("agentsync.publish", "TOPICS_CLAUDE_MD")]
    pointer = re.findall(r'"([^"]+)"', topics.split("2-line pointer ", 1)[1])
    assert "\n".join(pointer) + "\n" == publish.TOPICS_CLAUDE_MD
    assert not hasattr(publish, "ROOT_CLAUDE_MD") and hasattr(publish, "root_guide")
    root = noted[("agentsync.publish", "ROOT_CLAUDE_MD")]
    assert "ROOT_CLAUDE_MD removed;" in root and "publish.root_guide(archive=, inbox=)" in root
    assert list(inspect.signature(publish.root_guide).parameters) == ["archive", "inbox"]


def _section_15_values() -> list[tuple[str, object]]:
    return [(name, value) for _, name, value, _ in _section_15_constants()]


def test_contracts_cites_the_guides_by_name_not_by_line_c1042() -> None:
    """B13 round 36 C1042: §16.230 cited `setup-feedback.md:94` for the legend's `<path>` row, which lines
    added above it moved to 100, so the citation pointed at another row. The guide grows every round:
    CONTRACTS.md names a guide's row or passage, never its line number, and the `<path>` row it names is
    there."""
    text = CONTRACTS.read_text(encoding="utf-8")
    assert re.findall(r"setup-feedback\.md:\d+", text) == []
    flat = " ".join(text.split())
    assert (
        "The legend's `<path>` item (setup-feedback.md's placeholder table, the `<path>` row) says both"
        in flat
    )
    guide = (CONTRACTS.parent.parent / "deploy" / "setup-feedback.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^\| `<path>` \|", guide, flags=re.MULTILINE)) == 1


def test_contracts_cites_the_lanes_brief_by_ruling_not_by_line_c1284() -> None:
    """B13 round 37 C1284: §16.235's C1109 entry cited ruling 84's text as `COMMON.md:677`, the citation form
    C1042 retired for the guide, and C1042's check read only setup-feedback.md. `docs/lanes/COMMON.md` grows
    every round too: CONTRACTS.md names a ruling there by its number, never a line, and the ruling it names
    is there once."""
    text = CONTRACTS.read_text(encoding="utf-8")
    assert re.findall(r"(?:setup-feedback|COMMON)\.md:\d+", text) == []
    flat = " ".join(text.split())
    assert "ruling 84's own text in `docs/lanes/COMMON.md` stays as written." in flat
    lanes = (CONTRACTS.parent.parent / "lanes" / "COMMON.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^\(84\) ", lanes, flags=re.MULTILINE)) == 1


def test_every_item_paragraph_opens_after_a_blank_line_c1043() -> None:
    """B13 round 36 C1043: §16.232's C911 entry followed C937's last line with no blank line, so Markdown
    rendered it inside C937's paragraph. Every `**<title>** (C<n>` entry opens after a blank line."""
    lines = CONTRACTS.read_text(encoding="utf-8").splitlines()
    glued = [
        f"{n + 1}: {line[:60]}"
        for n, line in enumerate(lines)
        if n and re.match(r"\*\*[^*]+\*\* \(C\d+", line) and lines[n - 1].strip()
    ]
    assert glued == []


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


def _module_bindings(tree: ast.Module, module: str) -> tuple[dict[str, str], set[tuple[str, str]]]:
    """What the imports anywhere in ``module`` bind (a lazy import inside a function too): each local name
    bound to a module (``import agentsync.x as y``, ``from agentsync import x``), and each (module, name)
    pair imported by ``from <module> import <name>``, relative imports resolved."""
    package = module.split(".")[:-1]  # a package's own __init__ module included
    modules: dict[str, str] = {}
    names: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                modules[alias.asname or head] = alias.name if alias.asname else head
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package[: len(package) - node.level + 1]
                base = ".".join([*up, *([base] if base else [])])
            for alias in node.names:
                names.add((base, alias.name))
                modules[alias.asname or alias.name] = f"{base}.{alias.name}"
    return modules, names


def _dotted(node: ast.expr, modules: dict[str, str]) -> str | None:
    """The module an attribute's value names (``pandoc`` bound to agentsync.convert.pandoc, or
    ``agentsync.convert.pandoc`` spelled out), else None."""
    if isinstance(node, ast.Name):
        return modules.get(node.id)
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value, modules)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _dead_private_functions(sources: dict[str, str]) -> tuple[list[str], int]:
    """The module-level private functions of ``sources`` (module name -> code) that no code reaches, as
    ``module.name``, and how many there are in all. A function is reached by its name in its own module
    outside its own body, by a ``from <its module> import`` of it in any module, or as an attribute of a
    name bound to its module. Strings, comments, docstrings, ``self._x`` and another module's function of
    the same name never count (B13 round 16 T64)."""
    reached: set[tuple[str, str]] = set()
    defs: list[tuple[str, str]] = []
    for module, code in sources.items():
        tree = ast.parse(code)
        canonical = module.removesuffix(".__init__")
        modules, imported = _module_bindings(tree, module)
        reached |= {(m.removesuffix(".__init__"), n) for m, n in imported}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (owner := _dotted(node.value, modules)) is not None:
                reached.add((owner.removesuffix(".__init__"), node.attr))
        own = [d for d in tree.body if isinstance(d, ast.FunctionDef | ast.AsyncFunctionDef)]
        defs += [(canonical, d.name) for d in own if d.name.startswith("_") and not d.name.startswith("__")]
        inside = {id(n): d.name for d in own for n in ast.walk(d) if n is not d}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and inside.get(id(node)) != node.id:
                reached.add((canonical, node.id))
    dead = sorted(f"{module}.{name}" for module, name in defs if (module, name) not in reached)
    return dead, len(defs)


def test_no_private_function_in_src_is_left_for_the_tests_alone() -> None:
    """B13 round 15 S46, S69: doctor's ``_pandoc_path`` and convert.pandoc's ``_bundled_pandoc`` stayed in
    src as one-line wrappers after their last caller moved to the function they wrap, called by tests only.
    Every module-level private function in src is reached by code in src. Round 16 T64: the scan counted
    every textual mention, so a comment, a docstring, ``self._x`` or another module's function of the same
    name hid a dead one; it now reads the syntax tree (:func:`_dead_private_functions`)."""
    root = Path(agentsync.__file__).parent
    sources = {
        ".".join(("agentsync", *p.relative_to(root).with_suffix("").parts)): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.py"))
    }
    dead, defs = _dead_private_functions(sources)
    assert defs > 500, "the scan is broken"
    assert dead == []


_MANIFEST_TEST_API = {"applied_migrations", "iter_tombstones", "migrate", "pages_citing", "upsert_observed"}
"""Public ``Manifest`` methods older than this scan that only tests call (the migration probes, the per-row
upsert that ``upsert_observed_many`` batches, the tombstone and citation reads)."""


def test_every_public_manifest_method_has_a_caller_in_src() -> None:
    """B13 round 22 Z50, Z57: Y28 moved the cycle to ``retry_settled_failure_rows`` and left the counting
    ``retry_settled_failures`` with no caller, while CONTRACTS still gave it as the API. Every public
    ``Manifest`` method is reached by an attribute in src's syntax tree (a docstring naming it is not), but
    the few older ones only tests use."""
    from agentsync.manifest import Manifest  # noqa: PLC0415

    root = Path(agentsync.__file__).parent
    reached = {
        node.attr
        for p in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
    }
    public = {
        name
        for name, value in vars(Manifest).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, classmethod | staticmethod))
    }
    assert len(public) > 80, "the scan is broken"
    assert sorted(public - reached - _MANIFEST_TEST_API) == []
    assert public - reached >= _MANIFEST_TEST_API, "a listed method has a caller in src now: take it out"


_SCAN_SAMPLE_A = '''
def _dead_in_prose():
    """_dead_in_prose is named here only."""


def _recursive(n):
    return _recursive(n - 1) if n else 0


def _now():
    return 1


def _used():
    return 2


def _via_import():
    return 3


def _via_relative():
    return 4


def _via_alias():
    return 5


VALUE = _used()
'''
_SCAN_SAMPLE_B = """
# _dead_in_prose, in a comment of another module
from agentsync.a import _via_import
from .a import _via_relative


def _now():
    return 0


def run(obj):
    from agentsync import a as mod

    obj._recursive
    return _now(), _via_import(), _via_relative(), mod._via_alias(), "_dead_in_prose"
"""


def test_the_dead_function_scan_reads_code_not_text() -> None:
    """B13 round 16 T64: each way the text scan was fooled is caught (a docstring, a comment and a string
    in another module, ``obj._x``, recursion, another module's function of the same name), and each real
    way to reach a private function still counts (its own module, an import, absolute or relative, and a
    lazy module alias)."""
    dead, defs = _dead_private_functions({"agentsync.a": _SCAN_SAMPLE_A, "agentsync.b": _SCAN_SAMPLE_B})
    assert defs == 8
    assert dead == ["agentsync.a._dead_in_prose", "agentsync.a._now", "agentsync.a._recursive"]


def test_the_plain_text_reference_says_what_text_py_does() -> None:
    """B13 round 33 C782: §15's PlainTextConverter copy kept its old heading sentence and extension list after
    C670 corrected its decode order: it said the body opens with the file name, where text.py says the action
    key does not include the name, and it listed .vtt, which routes to vtt-turns, not .url. The copy gives
    text.py's own words and tuple, names the two routes, and leaves the decode phrase, which
    test_the_text_converters_docstrings_give_the_decode_order_the_code_uses counts, at exactly two."""
    from agentsync.convert import text  # noqa: PLC0415
    from agentsync.convert.vtt import VttConverter  # noqa: PLC0415

    raw = CONTRACTS.read_text(encoding="utf-8")
    start = raw.index("### `agentsync.convert.text` — `src/agentsync/convert/text.py`")
    block = raw[start : raw.index("\n### ", start + 1)]
    flat = " ".join(block.split())
    assert "a heading with the file name" not in flat
    doc = " ".join((text.PlainTextConverter.__doc__ or "").split())
    body = doc.split("into a fenced block. ", 1)[1]
    assert body in flat, "the code's own body paragraph, word for word"
    [listed] = re.findall(r"extensions: tuple\[str, \.\.\.\] = (\([^)]*\))", block)
    assert ast.literal_eval(listed) == text.PlainTextConverter.extensions
    assert ".url" in listed and ".vtt" not in listed
    assert VttConverter.converter_id == "vtt-turns" and VttConverter.extensions == (".vtt",)
    assert "``.vtt`` is not here: it routes to ``vtt-turns`` (§16.35); ``.url`` joined per §16.42." in flat
    said = "decoded (UTF-8/UTF-16 BOM first, then strict UTF-8, then cp1252, else latin-1)"
    assert " ".join(raw.split()).count(said) == 2
