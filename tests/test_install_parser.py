"""CONTRACTS §16.300.14 (U-B7): install.sh's option loop. The word ``help``, a stray SOURCE that writes
nothing, ``--name=value`` and ``--`` recorded in the space form, hidden options that warn once, and a checkout
or wheel that must be agentsync. Every run is /bin/bash 3.2, through test_install_oneshot's harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_install_oneshot import (  # noqa: F401  (fixtures)
    calls,
    env,
    folder,
    install_log,
    install_sh,
    last_line,
    one_next,
    report_path,
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
