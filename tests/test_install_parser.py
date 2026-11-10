"""CONTRACTS §16.300.14 (U-B7): install.sh's option loop. The word ``help``, a stray SOURCE that writes
nothing, ``--name=value`` and ``--`` recorded in the space form, hidden options that warn once, and a checkout
or wheel that must be agentsync. Every run is /bin/bash 3.2, through test_install_oneshot's harness."""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

import exec_stub
from test_install_oneshot import (  # noqa: F401  (fixtures)
    INSTALL_SH,
    REPO,
    calls,
    env,
    folder,
    install_log,
    install_sh,
    last_line,
    one_next,
    report_path,
    steps,
    wheel,
)


def _listing(env: dict[str, str]) -> list[str]:  # noqa: F811
    home = Path(env["HOME"])
    return sorted(str(p.relative_to(home)) for p in home.rglob("*"))


def _checkout(root: Path, pyproject: str) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return root


def _start_args(env: dict[str, str]) -> str:  # noqa: F811
    return next(ln for ln in install_log(env) if " start install.sh " in ln).split(" args=", 1)[1]


def test_help_word_shows_help_and_writes_nothing(env: dict[str, str]) -> None:  # noqa: F811
    before = _listing(env)
    word = install_sh(env, "help")
    option = install_sh(env, "--help")
    assert word.returncode == 0, word.stderr
    assert word.stdout == option.stdout and "[help]" in word.stdout
    assert _listing(env) == before


def test_a_stray_source_writes_nothing(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    before = _listing(env)
    cp = install_sh(env, str(tmp_path / "nowhere"))
    assert cp.returncode == 2
    assert "usage error:" in cp.stderr
    assert "NEXT:" not in cp.stdout + cp.stderr
    assert _listing(env) == before


def test_every_bad_source_local_is_named(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    one, two = tmp_path / "gone-one", tmp_path / "gone-two"
    cp = install_sh(env, "--source-local", str(one), "--source-local", str(two))
    assert cp.returncode == 2
    assert f"usage error: --source-local is not an existing folder: {one} (see --help)" in cp.stderr
    assert f"usage error: --source-local is not an existing folder: {two} (see --help)" in cp.stderr
    assert report_path(env).is_file()
    assert one_next(cp)
    argv = [w for i in range(7) for w in ("--source-local", str(tmp_path / f"gone-{i}"))]
    many = install_sh(env, *argv)
    assert many.returncode == 2
    assert sum("is not an existing folder:" in ln for ln in many.stderr.splitlines()) == 5
    assert "2 more folders" in many.stderr


def test_equals_forms_log_the_space_form(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
) -> None:
    cp = install_sh(env, str(wheel), f"--source-local={folder}")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    args = _start_args(env)
    assert "--source-local " + str(folder).replace(" ", "\\ ") in args
    assert "--source-local=" not in args


def test_an_equals_folder_is_registered_for_scrubbing(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
) -> None:
    """The shell report's registry tokenizes install.log's args= on the word --source-local. The folder does
    not exist, so the usage error stops the run before any config holds it: only that line can register it,
    and agentsync is not installed yet, so the report is the shell's."""
    mine = folder.parent / "Zebra Pipeline Notes"
    cp = install_sh(env, f"--source-local={mine}")
    assert cp.returncode == 2, cp.stdout + cp.stderr
    text = report_path(env).read_text()
    assert " start install.sh " in text, "the report embeds the log line that names the folder"
    assert "Zebra" not in text, text


def test_list_folders_with_a_terminator_stays_the_list_path(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
) -> None:
    cp = install_sh(env, "--list-folders", "--")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert str(folder) in cp.stdout
    assert _start_args(env) == "--list-folders"
    assert not report_path(env).exists()


def test_rerun_with_source_after_terminator_parses(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _checkout(tmp_path / "-x", '[project]\nname = "agentsync"\n')
    monkeypatch.chdir(tmp_path)
    first = install_sh(env, "--", "-x")
    assert first.returncode == 2, first.stdout + first.stderr
    assert " ./-x --source-local " in last_line(first), last_line(first)
    assert _start_args(env) == "./-x"
    again = install_sh(env, "./-x", "--source-local", str(folder))
    assert again.returncode == 0, again.stdout + again.stderr


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--launcher", "--help"], "--launcher needs a path, got the option --help"),
        (["--source-local", "--help"], "--source-local needs a folder, got the option --help"),
    ],
    ids=["launcher", "source-local"],
)
def test_an_option_as_a_value_is_a_usage_error(
    env: dict[str, str],  # noqa: F811
    argv: list[str],
    message: str,
) -> None:
    cp = install_sh(env, *argv)
    assert cp.returncode == 2
    assert f"usage error: {message}" in cp.stderr


def test_no_report_warns_once_and_changes_nothing(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
) -> None:
    cp = install_sh(env, str(wheel), "--source-local", str(folder), "--no-report", "--no-report")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert cp.stderr.count("--no-report is deprecated") == 1
    assert one_next(cp) and "--no-report" not in last_line(cp)
    assert "--no-report" not in _start_args(env)
    assert "deprecated" not in report_path(env).read_text()
    assert "deprecated" not in (Path(env["HOME"]) / "agent-context" / "setup" / "install.out").read_text()
    quiet = install_sh(env, "--no-report", "--help")
    assert quiet.returncode == 0 and "deprecated" not in quiet.stderr


def test_log_end_warns_on_stderr_only(env: dict[str, str]) -> None:  # noqa: F811
    end = install_sh(env, "--log-end")
    assert end.returncode == 0
    assert end.stdout == ""
    assert "warning: --log-end is deprecated" in end.stderr


def test_a_foreign_project_is_refused(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    other = _checkout(tmp_path / "other", '[project]\nname = "other"\n')
    cp = install_sh(env, str(other))
    assert cp.returncode == 2
    assert "not an agentsync checkout" in cp.stderr
    assert not [c for c in calls(env) if c.startswith("uv tool install")]


def test_a_foreign_wheel_is_refused(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    other = tmp_path / "other-0.1.0-py3-none-any.whl"
    other.write_text("")
    cp = install_sh(env, str(other))
    assert cp.returncode == 2
    assert "not an agentsync wheel" in cp.stderr
    assert not [c for c in calls(env) if c.startswith("uv tool install")]


def test_a_name_under_another_table_does_not_count(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    other = _checkout(tmp_path / "other", '[tool.foo]\nname = "agentsync"\n\n[project]\nname = "other"\n')
    cp = install_sh(env, str(other))
    assert cp.returncode == 2
    assert "not an agentsync checkout" in cp.stderr


def test_a_single_quoted_name_is_accepted(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    mine = _checkout(tmp_path / "mine", "[project]\nname = 'agentsync'\n")
    cp = install_sh(env, str(mine), "--source-local", str(folder))
    assert cp.returncode == 0, cp.stdout + cp.stderr


# ---- U-C9 (C-9.2, D22 Option A): the uv installer is pinned to one version and its SHA-256 ----

PINNED_URL = "https://astral.sh/uv/0.12.17/install.sh"
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _download_hosts() -> Any:
    spec = importlib.util.spec_from_file_location("download_hosts", REPO / "scripts" / "download_hosts.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_changed_installer_is_refused(env: dict[str, str], tmp_path: Path) -> None:  # noqa: F811
    """A download whose bytes are not the pinned installer's fails step 1 with exit 1, and is never run."""
    if shutil.which("uv", path=BASE_PATH):
        pytest.skip("a system uv is on the base PATH")
    bin_dir = tmp_path / "nouv"
    bin_dir.mkdir()
    exec_stub.write(bin_dir / "scutil", "#!/bin/bash\nexit 0\n")
    ran = tmp_path / "installer-ran"
    curl = exec_stub.write(
        tmp_path / "curl-stub",
        "#!/bin/bash\n"
        'while [ "$#" -gt 0 ]; do\n'
        f'  [ "$1" != -o ] || {{ printf "#!/bin/sh\\ntouch {ran}\\n" > "$2"; exit 0; }}\n'
        "  shift\n"
        "done\n"
        "exit 2\n",
    )
    cp = install_sh({**env, "PATH": f"{bin_dir}:{BASE_PATH}", "__AGENTSYNC_CURL": str(curl)})
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert (
        f"error: the uv installer from {PINNED_URL} is not the pinned one (changed or intercepted); not run"
        in cp.stderr
    )
    assert not ran.exists(), "a changed installer was run"
    assert one_next(cp), cp.stdout + cp.stderr


def test_the_pinned_url_is_literal_for_the_hosts_check() -> None:
    """download_hosts.py reads the URL with a column-0 regex, so the weekly check follows the pinned URL
    itself; the hash beside it is one SHA-256."""
    script = INSTALL_SH.read_text(encoding="utf-8")
    assert _download_hosts().uv_installer_url(script) == PINNED_URL
    assert f'\nUV_INSTALLER_URL="{PINNED_URL}"\n' in script, "a literal, not built from a variable"
    assert re.search(r"^UV_INSTALLER_SHA256='[0-9a-f]{64}'$", script, re.MULTILINE)


def test_the_pinned_uv_is_the_version_the_workflows_use() -> None:
    """D22: the installer's version is the one setup-uv pins in ci.yml, diagrams.yml and
    download-hosts.yml."""
    url = _download_hosts().uv_installer_url(INSTALL_SH.read_text(encoding="utf-8"))
    m = re.fullmatch(r"https://astral\.sh/uv/([0-9][0-9.]*)/install\.sh", url)
    assert m is not None, url
    for name in ("ci.yml", "diagrams.yml", "download-hosts.yml"):
        text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
        pins = re.findall(r'uses: astral-sh/setup-uv@\S+.*\n\s+with:\n\s+version: "([^"]+)"', text)
        assert pins == [m.group(1)], f"{name} pins uv {pins}, the installer {m.group(1)}"


# ---- U-C10: --timeout, --cwd, --debug and --non-interactive ----------------------------------------------


def _limits(cp: Any) -> str:
    return next(ln for ln in cp.stderr.splitlines() if ln.startswith("debug: limits "))


@pytest.mark.parametrize(
    ("argv", "seconds"),
    [
        (["--timeout", "90"], 90),
        (["--timeout", "30s"], 30),
        (["--timeout", "5m"], 300),
        (["--timeout=2m"], 120),
        (["--timeout", "5m", "--timeout", "007"], 7),
        (["--timeout", "soon"], None),
        (["--timeout", "2h"], None),
        (["--timeout", "1.5m"], None),
        (["--timeout", "0"], None),
        (["--timeout", "0m"], None),
        (["--timeout", "-5"], None),
        (["--timeout="], None),
        (["--timeout", "1234567890"], None),
        (["--timeout"], None),
    ],
    ids=lambda v: " ".join(v) if isinstance(v, list) else str(v),
)
def test_timeout_accepts_a_duration_and_rejects_a_word(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    argv: list[str],
    seconds: int | None,
) -> None:
    """30s, 5m and bare seconds are one limit in whole seconds over the first sync, the first background run's
    wait and step 2's lock wait; any other word is a usage error before anything is written."""
    before = _listing(env)
    cp = install_sh(env, str(wheel), "--source-local", str(folder), *argv, "--debug")
    if seconds is None:
        assert cp.returncode == 2, cp.stdout + cp.stderr
        assert "usage error: --timeout " in cp.stderr, cp.stderr
        assert _listing(env) == before
        return
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (
        _limits(cp) == f"debug: limits first-sync={seconds}s first-run-wait={seconds}s lock-wait={seconds}s"
    )


def test_option_beats_the_variable(env: dict[str, str], folder: Path, wheel: Path) -> None:  # noqa: F811
    """The variable beats the default, and --timeout beats the variable: the wait for the first background run
    counts to the option's seconds while AGENTSYNC_WAIT_SECONDS says 60. The option adds no limit on uv."""
    e = {
        **env,
        "AGENTSYNC_PROGRESS_SECONDS": "1",
        "STUB_LC_RUNNING": "1",
        "AGENTSYNC_WAIT_SECONDS": "60",
        "AGENTSYNC_REINSTALL_WAIT_SECONDS": "60",
    }
    cp = install_sh(
        e, str(wheel), "--source-local", str(folder), "--confirm-install-agent", "--timeout", "6s", "--debug"
    )
    assert cp.returncode == 3, cp.stdout + cp.stderr
    wait = [ln for ln in cp.stdout.splitlines() if re.fullmatch(r"wait: \d+s of \d+s, .*", ln)]
    assert wait and all(" of 6s, " in ln for ln in wait), cp.stdout
    assert _limits(cp) == "debug: limits first-sync=6s first-run-wait=6s lock-wait=6s"
    script = INSTALL_SH.read_text(encoding="utf-8")
    assert 'WAIT_SECONDS="${AGENTSYNC_WAIT_SECONDS:-180}"' in script
    assert 'REINSTALL_WAIT="${AGENTSYNC_REINSTALL_WAIT_SECONDS:-120}"' in script
    assert "UV_INSTALL_LIMIT=900 " in script, "D23: --timeout sets no uv limit"
    # a variable that is no whole number is still a usage error alone, and the option replaces it
    bad = {**env, "AGENTSYNC_WAIT_SECONDS": "2m"}
    alone = install_sh(bad, str(wheel), "--source-local", str(folder))
    assert alone.returncode == 2 and "AGENTSYNC_WAIT_SECONDS must be a whole number" in alone.stderr
    beaten = install_sh(bad, str(wheel), "--source-local", str(folder), "--timeout", "2m")
    assert beaten.returncode == 0, beaten.stdout + beaten.stderr


def test_new_options_stay_out_of_the_rerun(env: dict[str, str], wheel: Path, tmp_path: Path) -> None:  # noqa: F811
    """None of the four is recorded: the NEXT's re-run and install.log's args= are a run's without them, the
    debug lines are on stderr only (install.out keeps them), with the home as ~, and --list-folders takes none
    of them (R061's carve-out)."""
    four = [
        "--timeout",
        "5m",
        "--cwd",
        str(tmp_path),
        "--debug",
        "--non-interactive",
        "--timeout=90",
        f"--cwd={tmp_path}",
    ]
    cp = install_sh(env, str(wheel), *four)
    assert one_next(cp), cp.stdout + cp.stderr
    next_line = last_line(cp)
    assert f" {wheel} --source-local " in next_line, next_line
    for opt in ("--timeout", "--cwd", "--debug", "--non-interactive"):
        assert opt not in next_line and opt not in _start_args(env), opt
    assert _start_args(env) == str(wheel)
    assert "debug:" not in cp.stdout
    assert "debug: config ~/agent-context/sources.toml" in cp.stderr.splitlines(), cp.stderr
    assert "debug: limits first-sync=90s first-run-wait=90s lock-wait=90s" in cp.stderr.splitlines()
    out = (Path(env["HOME"]) / "agent-context" / "setup" / "install.out").read_text()
    assert "debug: limits first-sync=90s" in out
    for opt in (["--debug"], ["--non-interactive"], ["--timeout", "5m"], ["--cwd", str(tmp_path)]):
        listed = install_sh(env, "--list-folders", *opt)
        assert listed.returncode == 2, opt
        assert "usage error: --list-folders takes no other option or SOURCE" in listed.stderr, opt


def test_non_interactive_is_a_no_op(env: dict[str, str], folder: Path, wheel: Path) -> None:  # noqa: F811
    """A re-run with --non-interactive does what the same re-run without it does: the same steps, results and
    NEXT, and nothing on stderr names the option."""
    first = install_sh(env, str(wheel), "--source-local", str(folder))
    assert first.returncode == 0, first.stdout + first.stderr
    runs = []
    for extra in ([], ["--non-interactive"]):
        mark = len(install_log(env))
        cp = install_sh(env, str(wheel), *extra)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        assert "--non-interactive" not in cp.stdout + cp.stderr
        runs.append((last_line(cp), [s[:3] for s in steps(install_log(env)[mark:])]))
    assert runs[0] == runs[1]


def test_cwd_applies_before_relative_paths(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--cwd is entered before SOURCE and each --source-local folder are made absolute: the same relative
    words fail from elsewhere and install from DIR. A folder it cannot enter is a usage error that writes
    nothing."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    source = str(wheel.relative_to(tmp_path))
    local = str(folder.relative_to(tmp_path))
    before = _listing(env)
    gone = install_sh(env, "--cwd", str(tmp_path / "nowhere"), source)
    assert gone.returncode == 2
    assert f"usage error: --cwd is not a folder this run can enter: {tmp_path / 'nowhere'}" in gone.stderr
    assert _listing(env) == before
    lost = install_sh(env, source, "--source-local", local)
    assert lost.returncode == 2, lost.stdout + lost.stderr
    cp = install_sh(env, source, "--source-local", local, "--cwd", str(tmp_path), "--debug")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"debug: source wheel {wheel}" in cp.stderr.splitlines(), cp.stderr
    config = (Path(env["HOME"]) / "agent-context" / "sources.toml").read_text()
    assert f'path = "{folder}"' in config, config
    assert any(c.startswith("agentsync add-source ") and str(folder) in c for c in calls(env)), calls(env)


def test_cwd_rerun_names_absolute_folders(
    env: dict[str, str],  # noqa: F811
    folder: Path,  # noqa: F811
    wheel: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-run is not run from --cwd's DIR, so it names a relative SOURCE and --source-local folder as the
    absolute paths this run read, and never --cwd itself: install.log's args= and a NEXT's re-run both."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    source = str(wheel.relative_to(tmp_path))
    local = str(folder.relative_to(tmp_path))
    bare = install_sh(env, "--cwd", str(tmp_path), f"./{source}")  # no folder yet: its NEXT names the re-run
    assert one_next(bare), bare.stdout + bare.stderr
    assert f" {wheel} --source-local " in last_line(bare), last_line(bare)
    assert "--cwd" not in last_line(bare) and f"./{source}" not in last_line(bare)
    cp = install_sh(env, "--cwd", str(tmp_path), source, f"--source-local={local}")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    args = [ln.split(" args=", 1)[1] for ln in install_log(env) if " start install.sh " in ln]
    assert args[-1] == f"{wheel} --source-local " + str(folder).replace(" ", "\\ "), args
