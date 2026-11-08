"""install.sh's --help names no test seam and says what its words and its list run mean (U-B8), and what the
optional helpers and uv's failures print.

The harness is test_install_oneshot's: a tmp HOME, a stub ``uv``, a stub ``launchctl``.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_install_oneshot import (  # noqa: F401  (env, wheel and folder are fixtures)
    REPO,
    STUB_TOOL_PYTHON,
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
