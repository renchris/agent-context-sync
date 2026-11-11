"""install.sh's --help names no test seam and says what its words and its list run mean (U-B8), and what the
optional helpers and uv's failures print. The help is short (U-C11): 80 columns, sorted options, examples that
parse, and a sentinel before the header's full notes.

The harness is test_install_oneshot's: a tmp HOME, a stub ``uv``, a stub ``launchctl``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from agentsync import setup_report
from test_contracts import INSTALL_HIDDEN, INSTALL_OPTIONS
from test_install_oneshot import (  # noqa: F401  (env, wheel and folder are fixtures)
    ESCAPED_MARK,
    INSTALL_SH,
    REPO,
    STUB_TOOL_PYTHON,
    _cloud,
    _renderer_home,
    _write_exe,
    env,
    folder,
    install_log,
    install_sh,
    one_next,
    report_path,
    wheel,
)

GLOSSARY = REPO / "docs" / "design" / "glossary.md"
SENTINEL = "# -- end of help --"

SEAMS = (
    "AGENTSYNC_NO_NEXT_HINT",
    "AGENTSYNC_QUIET_LOCK_WAIT",
    "AGENTSYNC_LOCK_WAIT_ANNOUNCED",
    "AGENTSYNC_AGENT_STEP_PENDING",
    "AGENTSYNC_REPORT_RUN",
    "AGENTSYNC_SIMULATE_LAUNCHD",
    "AGENTSYNC_LAUNCHCTL",
    "AGENTSYNC_WAIT_POLL_SECONDS",
    "AGENTSYNC_PROGRESS_SECONDS",
    "AGENTSYNC_LIST_TIMEOUT",
    "AGENTSYNC_LIST_TOTAL_SECONDS",
)
OPERATOR_VARIABLES = (
    "AGENTSYNC_WAIT_SECONDS",
    "AGENTSYNC_REINSTALL_WAIT_SECONDS",
    "AGENTSYNC_REBUILD_LAUNCHER",
    "AGENTSYNC_SETUP_LOG",
    "AGENTSYNC_FRICTION_LOG",
    "AGENTSYNC_INSTALL_DRY_RUN",
)


def test_help_names_no_test_seam(env: dict[str, str]) -> None:  # noqa: F811
    cp = install_sh(env, "--help")
    assert cp.returncode == 0
    assert [name for name in SEAMS if name in cp.stdout] == []
    assert [name for name in OPERATOR_VARIABLES if name not in cp.stdout] == []


def test_help_has_the_terms_block(env: dict[str, str]) -> None:  # noqa: F811
    help_text = install_sh(env, "--help").stdout
    (block,) = re.findall(r"^Terms .*?(?=^$)", help_text, flags=re.MULTILINE | re.DOTALL)
    for term in ("folder", "source", "LaunchAgent", "background sync", "coding agent", "agent step"):
        assert re.search(rf"^  {term}\s", block, flags=re.MULTILINE), term
    assert help_text.index("Terms ") < help_text.index("The config is $AGENTSYNC_CONFIG")
    assert "a coding agent running the setup" in help_text and "setup agent" not in help_text


def _block(help_text: str, heading: str) -> list[str]:
    """The lines under ``heading`` (a line of its own), up to the next blank line."""
    lines = help_text.splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


def _listed_options(help_text: str) -> list[list[str]]:
    """Each Options row's spellings: ``-a, --all`` is ``["-a", "--all"]``; a value hint is not one."""
    rows: list[list[str]] = []
    for line in _block(help_text, "Options:"):
        if line.startswith("  -"):
            head = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
            rows.append(re.findall(r"(?<![\w<-])(--?[a-z][a-z-]*)", head))
    return rows


def test_help_fits_80_columns_and_sorts_options(env: dict[str, str]) -> None:  # noqa: F811
    help_text = install_sh(env, "--help").stdout
    assert [ln for ln in help_text.splitlines() if len(ln) > 80] == []
    summary, _, usage = help_text.splitlines()[:3]
    assert summary.startswith("agentsync installer: ") and usage.startswith("Usage: scripts/install.sh ")
    longs = [next(s for s in row if s.startswith("--")) for row in _listed_options(help_text)]
    assert longs == sorted(longs, key=lambda s: s.lstrip("-")), longs
    assert "  scripts/install.sh [--help] [help]" in help_text
    assert "[--source-local <DIR>]... " in help_text


def test_help_stops_at_the_sentinel(env: dict[str, str]) -> None:  # noqa: F811
    cp = install_sh(env, "--help")
    assert cp.returncode == 0, cp.stderr
    lines = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    short = lines[1 : lines.index(SENTINEL)]
    assert cp.stdout == "".join(re.sub(r"^# ?", "", ln) + "\n" for ln in short)
    assert "B13 round" not in cp.stdout and "end of help" not in cp.stdout
    assert "Setup prompt: SETUP_PROMPT_COMPAT" not in cp.stdout, "the full notes stay in the file"
    assert next(ln for ln in lines[lines.index(SENTINEL) :] if not ln.startswith("#")).startswith(
        "SETUP_PROMPT_COMPAT="
    ), "the full notes follow the sentinel in the same comment header"


def test_every_help_example_parses(env: dict[str, str], folder: Path) -> None:  # noqa: F811
    """R100: each example runs as printed, <DIR> a synced folder, under the dry run: never a live change."""
    examples = [ln.strip() for ln in _block(install_sh(env, "--help").stdout, "Examples:")]
    assert 2 <= len(examples) <= 3 and not any(ln.startswith("$") for ln in examples), examples
    home = Path(env["HOME"])
    before = sorted(home.rglob("*"))
    for example in examples:
        words = shlex.split(example.replace("<DIR>", shlex.quote(str(folder))))
        variables = {}
        while "=" in words[0]:
            name, value = words.pop(0).split("=", 1)
            variables[name] = value
        assert words[0] == "scripts/install.sh", example
        cp = install_sh({**env, **variables, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, *words[1:])
        assert "usage error" not in cp.stderr and cp.returncode != 2, (example, cp.stdout + cp.stderr)
        assert sorted(home.rglob("*")) == before, example


def test_every_listed_option_is_a_parsed_option(env: dict[str, str]) -> None:  # noqa: F811
    listed = {s for row in _listed_options(install_sh(env, "--help").stdout) for s in row}
    parsed = {o for o in INSTALL_OPTIONS["main"] if o not in {"*", "--"} and not o.endswith("=*")}
    assert listed == parsed - INSTALL_HIDDEN


def test_glossary_lists_meaning_identity_and_banned_synonyms() -> None:
    sections = re.split(r"^## ", GLOSSARY.read_text(encoding="utf-8"), flags=re.MULTILINE)[1:]
    by_noun = {s.splitlines()[0].strip(): s for s in sections}
    for noun in (
        "folder",
        "source",
        "LaunchAgent",
        "background sync",
        "coding agent",
        "agent step",
        "docs repo",
        "manifest",
        "hold",
        "selector",
        "setup report",
    ):
        for field in ("**Meaning:**", "**Identity:**", "**Banned synonyms:**"):
            assert field in by_noun[noun], f"{noun}: no {field}"


def test_dry_run_list_folders_writes_nothing(env: dict[str, str]) -> None:  # noqa: F811
    home = Path(env["HOME"])
    before = sorted(home.rglob("*"))
    cp = install_sh({**env, "AGENTSYNC_INSTALL_DRY_RUN": "1"}, "--list-folders")
    assert cp.returncode in (0, 3), cp.stdout + cp.stderr
    setup = home / "agent-context" / "setup"
    assert not (setup / "install.log").exists()
    assert not (setup / "install.out").exists()
    assert sorted(home.rglob("*")) == before


def test_a_failed_helper_build_warns_once_and_keeps_the_exit(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
) -> None:
    home = Path(env["HOME"])
    tool_py = home / ".local" / "share" / "uv" / "tools" / "agentsync" / "bin" / "python"
    tool_py.parent.mkdir(parents=True, exist_ok=True)
    _write_exe(tool_py, STUB_TOOL_PYTHON)
    ready = install_sh(
        {**env, "STUB_OCR_OUT": "OCR helper: ready (stub)"}, str(wheel), "--source-local", str(folder)
    )
    assert "optional helpers not ready" not in ready.stderr
    log = home / "agent-context" / "setup" / "install.log"
    log.unlink()
    failed = install_sh(
        {
            **env,
            "STUB_OCR_OUT": "OCR helper: not built (stub)",
            "STUB_OCR_RC": "1",
        },
        str(wheel),
        "--source-local",
        str(folder),
    )
    assert failed.returncode == ready.returncode
    warned = [ln for ln in failed.stderr.splitlines() if "optional helpers not ready" in ln]
    assert len(warned) == 1, failed.stderr
    assert warned[0].startswith("warning: optional helpers not ready: ocr; the run goes on")
    assert "OCR helper" not in failed.stderr
    assert one_next(failed)
    assert any(" step=helpers " in ln and "not-built-ocr" in ln for ln in install_log(env))


def test_uv_failure_guesses_no_cause(env: dict[str, str], folder: Path, wheel: Path) -> None:  # noqa: F811
    cp = install_sh({**env, "STUB_UV_INSTALL_RC": "2"}, str(wheel), "--source-local", str(folder))
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert "uv tool install failed (its error is above)" in cp.stderr
    assert "HTTPS_PROXY / UV_SYSTEM_CERTS=1" in cp.stderr
    assert "proxy or TLS inspection?" not in cp.stderr
    assert one_next(cp)


def test_a_missing_folder_name_stays_out_of_the_report(env: dict[str, str]) -> None:  # noqa: F811
    gone = Path(env["HOME"]) / "Projects" / "acquisition-northwind"
    cp = install_sh(env, "--source-local", str(gone))
    assert cp.returncode == 2
    assert f"--source-local is not an existing folder: {gone}" in cp.stderr, "the person still reads it"
    report = report_path(env).read_text(encoding="utf-8")
    assert "acquisition-northwind" not in report, report
    assert "is not an existing folder" in report


@pytest.mark.parametrize("given", ["twin", "marked", "missing twin"])
def test_a_refused_source_local_folder_name_stays_out_of_the_report_c1278(
    env: dict[str, str],  # noqa: F811
    given: str,
) -> None:
    """B13 round 37 ruling 120 (C1278; privacy): a folder step 1 shows with a control character and a
    look-alike beside it was refused with list_name's escaped spelling in LAST_ERROR, which no redaction pair
    matches, so the shell report's last-error and Recent errors lines named it. Every --source-local refusal
    now gives the report its words with <path>, while stderr still names the folder."""
    lib = _cloud(env) / "OneDrive-Contoso"
    for name in ("Northwind Board\nMinutes", "Northwind Board\\nMinutes"):
        (lib / name).mkdir(parents=True)
    arg = {
        "twin": str(lib / "Northwind Board\nMinutes"),
        "marked": f"{ESCAPED_MARK} {lib}/Northwind Board\\nMinutes",
        "missing twin": str(lib / "Northwind Board\x01Minutes").replace("\x01", "?"),
    }[given]
    if given == "missing twin":
        (lib / "Northwind Board\x01Minutes").mkdir()
    cp = install_sh(env, "--source-local", arg)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert "Northwind Board" in cp.stderr, "the person still reads it"
    report = report_path(env).read_text(encoding="utf-8")
    assert "Board" not in report and "Minutes" not in report, report
    # under CloudStorage the provider folder stays, cut by its pair (B13 round 39 C1601)
    assert "--source-local ~/Library/CloudStorage/OneDrive-<org-1>/<path>: " in report, report


@pytest.mark.parametrize("renderer", ["checkout", "older"])
@pytest.mark.parametrize("head", range(4))
def test_each_refused_source_local_name_outside_cloudstorage_stays_out_of_every_render_c1432(
    env: dict[str, str],  # noqa: F811
    renderer: str,
    head: int,
) -> None:
    """B13 round 38 C1432 (ruling 126; privacy): a refused --source-local name with a blank, outside
    CloudStorage, was cut by the agentsync report's one-shell-word rule only to its first blank, so the rest
    of the name printed after `<path>`. Each of the four refusal heads, for a folder under ~/Projects,
    rendered by this checkout's agentsync and by an older one (cut again by this checkout), names no part of
    the folder."""
    env = _renderer_home(env, renderer)
    projects = Path(env["HOME"]) / "Projects"
    for name in ("Deals Alpha\nBoard", "Deals Alpha\\nBoard", "Deals Alpha\x01Bid", "Deals Alpha Kept"):
        (projects / name).mkdir(parents=True)
    arg = [
        f"[synced] {projects}/Deals Alpha Kept",
        str(projects / "Deals Alpha\nBoard"),
        str(projects / "Deals Alpha\\nBoard"),
        str(projects / "Deals Alpha?Bid"),
    ][head]
    cp = install_sh(env, "--source-local", arg)
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert setup_report._REFUSED_HEADS[head] in cp.stderr, cp.stderr
    for path in (report_path(env), report_path(env).parent / "bring-back.md"):
        text = path.read_text(encoding="utf-8")
        assert "Deals" not in text and "Alpha" not in text and "Board" not in text, (path.name, text)
