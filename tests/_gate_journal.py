"""scripts/gate.sh's pytest plugin and helper CLI; not a test file, and loaded only by the gate.

Plugin (``-p _gate_journal``, active only when GATE_JOURNAL=<path> is set): the xdist controller appends one
JSON line per test phase that decides a verdict (the call, and a setup or teardown that fails or skips), each
line one ``os.write`` on an O_APPEND descriptor, no fsync. A SIGKILLed run so leaves every line but at most a
torn last one, where pytest's ``--lf`` cache, written at session end, keeps nothing. The session end appends
``{"summary": <exitstatus>}``. GATE_DESELECT=<file> deselects the exact nodeids it lists: ``--deselect``
matches by prefix, so ``test_a`` would also drop ``test_ab``, and ``test_a[1]`` would drop ``test_a[10]``.
A failed phase's line also keeps the last ``_TAIL_LINES`` lines of its report and the last ``_TAIL_CHARS``
characters of its captured stderr, and an xdist worker that goes down with an error appends
``{"node_down": <gwN>, "error": <its text>}``: the only text a worker crash leaves, since the crashed test's
own report says no more than "worker crashed while running" (B13 round 16 T90). Its line has no nodeid, so a
resume never reads it. The whole output of a lane part that fails or is killed, stderr too, is kept beside
its journal as ``<tree>-<salt>.<part>.out`` (appended on a resume, removed when the part is green; B13 round
17 U106), where these tails are cut.

CLI: ``passed <journal>`` prints the nodeids that passed, ``skipped-timing <journal>`` counts the tests the
load skip of ``conftest.pytest_runtest_setup`` decided, ``finished <journal>`` exits 0 when the journal
holds a summary line, and ``affected`` reads changed repo paths on stdin and prints the test files they can
turn red, or ``FULL <reason>``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TIMING_SKIP = re.compile(r"load \d+ per core: a time bound cannot be judged")
_TAIL_LINES = 20
_TAIL_CHARS = 2000


class _Journal:
    def __init__(self, path: str) -> None:
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        if os.fstat(self.fd).st_size and not Path(path).read_bytes().endswith(b"\n"):
            os.write(self.fd, b"\n")  # a killed run's torn line stays one bad line, not the start of ours

    def append(self, record: dict[str, Any]) -> None:
        os.write(self.fd, (json.dumps(record) + "\n").encode())

    @pytest.hookimpl(trylast=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" or report.failed or report.skipped:
            longrepr = report.longrepr
            reason = longrepr[2] if report.skipped and isinstance(longrepr, tuple) else None
            record: dict[str, Any] = {
                "nodeid": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
                "skip_reason": reason,
            }
            if report.failed:
                record["tail"] = "\n".join(str(longrepr).splitlines()[-_TAIL_LINES:])[-_TAIL_CHARS:]
                if report.capstderr:
                    record["stderr"] = report.capstderr[-_TAIL_CHARS:]
            self.append(record)

    @pytest.hookimpl(optionalhook=True)  # xdist's hook: without xdist there is no worker to lose
    def pytest_testnodedown(self, node: Any, error: object | None) -> None:
        if error:
            self.append({"node_down": str(node.gateway.id), "error": str(error)[-_TAIL_CHARS:]})

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        self.append({"summary": int(exitstatus)})
        os.close(self.fd)


def pytest_configure(config: pytest.Config) -> None:
    path = os.environ.get("GATE_JOURNAL")
    if path and not hasattr(config, "workerinput"):  # an xdist worker's reports reach the controller
        config.pluginmanager.register(_Journal(path), "gate-journal")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not (path := os.environ.get("GATE_DESELECT")):
        return
    drop = set(Path(path).read_text(encoding="utf-8").splitlines())
    config.hook.pytest_deselected(items=[item for item in items if item.nodeid in drop])
    items[:] = [item for item in items if item.nodeid not in drop]


def _last(path: Path) -> tuple[dict[str, dict[str, Any]], bool]:
    """Per nodeid its last record (a teardown failure follows its call; a resumed run follows a killed
    one), and whether a summary line is there. A line that does not parse is the torn one a kill leaves."""
    last: dict[str, dict[str, Any]] = {}
    summary = False
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        summary |= "summary" in record
        if "nodeid" in record:
            last[record["nodeid"]] = record
    return last, summary


def _module_of(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts[1:]  # drop "src"
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path, known: set[str]) -> set[str]:
    """The known modules a file imports, with each package the import runs on its way. In tests/, a string
    naming one (``monkeypatch.setattr("agentsync.x.f", …)``) and a ``-m agentsync`` subprocess count too."""
    text = path.read_text(encoding="utf-8")
    names: set[str] = set()
    package = ""
    if path.is_relative_to(ROOT / "src"):
        package = _module_of(path.relative_to(ROOT).as_posix())
        package = package if path.name == "__init__.py" else package.rpartition(".")[0]
    else:
        names.update(re.findall(r"\bagentsync(?:\.\w+)+", text))
        if re.search(r'-m",?\s*"agentsync"|-m agentsync\b', text):
            names.add("agentsync.__main__")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*up, base] if base else up)
            names |= {base, *(f"{base}.{a.name}" for a in node.names)}
    parents = {".".join(n.split(".")[: i + 1]) for n in names for i in range(n.count(".") + 1)}
    return parents & known


def affected(changed: list[str]) -> list[str] | str:
    """The test files a change to ``changed`` can turn red, or why only the full set will do."""
    src = {_module_of(p.relative_to(ROOT).as_posix()): p for p in sorted((ROOT / "src").rglob("*.py"))}
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    helpers = {p.stem: p for p in (ROOT / "tests").glob("*.py") if p not in tests and p.stem != "conftest"}
    helpers.pop("_gate_journal")  # the gate's own helper: no test imports it
    texts = {t: t.read_text(encoding="utf-8") for t in tests}
    hit: set[str] = set()
    picked = {ROOT / "tests" / name for name in _SCANNERS}
    for rel in changed:
        p = Path(rel)
        rule = next((words for prefix, words in _MAP.items() if rel.startswith(prefix)), None)
        if rel in ("tests/conftest.py", "pyproject.toml", "uv.lock") or rel.startswith("tests/fixtures/"):
            return f"{rel} reaches every test"
        if p.parts[0] == "src" and p.suffix == ".py":
            hit.add(_module_of(rel))
        elif p.parts[0] == "src":  # a data file: the modules of the package that holds it
            at = p.parent
            while not (ROOT / at / "__init__.py").is_file() and len(at.parts) > 2:
                at = at.parent
            mod = _module_of(f"{at.as_posix()}/__init__.py")
            hit |= {m for m in src if m == mod or m.startswith(f"{mod}.")}
        elif p.parts[0] == "tests" and p.name.startswith("test_"):
            picked |= {t for t in tests if t.name == p.name}
        elif p.parts[0] == "tests" and p.suffix == ".py":
            hit.add(p.stem)
        elif rule is not None:
            picked |= {t for t in tests if any(w in texts[t] for w in rule)}
        elif p.parts[0] in _GREPPED or (len(p.parts) == 1 and p.suffix == ".md"):
            words = {rel, p.name} | ({p.parent.name} if len(p.parts) > 2 else set())
            picked |= {t for t in tests if any(w in texts[t] for w in words)}
            if "/" in rel:  # a module that reads the file by its repo path
                hit |= {m for m, f in src.items() if rel in f.read_text(encoding="utf-8")}
        else:
            return f"{rel} has no test map"
    # Direct importers only. The import closure reaches every test: conftest's autouse fixture imports
    # agentsync.cli, which imports nearly every module. The land gate's full run covers the transitive rest.
    known = set(src) | set(helpers) | hit  # a deleted module still names its importers
    deps = {m: _imports(f, known) for m, f in helpers.items()}
    while grown := {m for m, d in deps.items() if m not in hit and d & hit}:
        hit |= grown  # a test helper (media_kit, speech_kit) that imports one passes it on
    picked |= {t for t in tests if _imports(t, known) & hit}
    leaves = {m.rpartition(".")[2] for m in hit if "." in m}  # reached as an attribute: manifest.tm_exclude
    picked |= {t for t in tests if any(re.search(rf"\w\.{leaf}\b", texts[t]) for leaf in leaves)}
    return sorted(t.relative_to(ROOT).as_posix() for t in picked)


_SCANNERS = ("test_contracts.py", "test_lints.py", "test_governance.py", "test_deploy_pack.py")
"""Always run: they read the whole tree (README, CONTRACTS, docs/deploy, the citing files, the gate list)."""
_MAP = {
    "README": ("README",),
    "docs/design/CONTRACTS.md": ("CONTRACTS",),
    "docs/deploy/": ('"deploy"', "docs/deploy"),
    ".github/": ('".github"', ".github/"),
    "launcher/": ('"launcher"', "launcher/"),
}
"""Paths tests read by folder or glob, to the words that mark a test reading them (a grep of tests/)."""
_GREPPED = ("docs", "scripts", "probes")
"""Paths a test reads by name: a test naming the path, its file or its folder is picked."""


def main(argv: list[str]) -> int:
    verb = argv[0] if argv else ""
    if verb == "affected" and len(argv) == 1:
        result = affected([line for line in sys.stdin.read().splitlines() if line])
        print(f"FULL {result}" if isinstance(result, str) else "\n".join(result))
        return 0
    if len(argv) != 2 or verb not in ("passed", "skipped-timing", "finished"):
        print("usage: _gate_journal.py passed|skipped-timing|finished <journal> | affected", file=sys.stderr)
        return 2
    last, summary = _last(Path(argv[1]))
    if verb == "finished":
        return 0 if summary else 1
    if verb == "passed":
        print("\n".join(n for n, r in last.items() if r["when"] == "call" and r["outcome"] == "passed"))
    else:
        print(sum(1 for r in last.values() if _TIMING_SKIP.search(r.get("skip_reason") or "")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
