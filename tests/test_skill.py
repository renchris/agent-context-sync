"""The Claude Code skill (``agentsync.skill``): its text, and the copies every non-dry-run sync writes.

HOME is a tmp dir (conftest ``_isolate_home``) and CLAUDE_CONFIG_DIR is unset unless a test sets it to a tmp
dir, so no test writes the operator's real ~/.claude or $CLAUDE_CONFIG_DIR.
"""

from __future__ import annotations

import logging
import os
import pwd
from pathlib import Path

import pytest

from agentsync import cli, curate, gitops, skill
from agentsync.config import Config, load_config


@pytest.fixture
def initialised(tmp_path: Path, local_source_dir: Path, capsys: pytest.CaptureFixture[str]) -> Config:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["add-source", str(local_source_dir), "--config", str(cfg)]) == cli.EXIT_OK
    capsys.readouterr()
    return load_config(cfg)


def home_skill() -> Path:
    return Path.home() / ".claude" / "skills" / skill.SKILL_NAME / "SKILL.md"


def test_install_skill_writes_once_and_names_the_docs_repo(
    initialised: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = str(initialised.config_path)
    assert cli.main(["install-skill", "--config", cfg]) == cli.EXIT_OK
    text = home_skill().read_text(encoding="utf-8")
    assert text == skill.skill_text(initialised.docs_repo)
    assert text.startswith(f"---\nname: {skill.SKILL_NAME}\n")
    assert (
        f"`cd {initialised.docs_repo}`" in text and f"`{skill.AGENTSYNC_BIN} curate` checks the pins" in text
    )
    assert skill.procedure() in text  # the same procedure the root CLAUDE.md and AGENTS.md carry
    assert ".agentsync-" not in text and "SYNONYMS" not in text  # KISS K07: no tmp-rename, no SYNONYMS step
    assert "_index/by-entity.tsv" in text and "`purpose:`" in text
    assert "search `archive/`" not in text and "snapshot/" not in text  # archive lines: root guide only
    assert "`_eval/questions.md`" in text and "read only `_eval/questions.md`" in text  # baseline questions
    assert "Never open `_eval/answers.md`" in text and "status: confirmed" in text
    assert "_eval" in gitops.COMMIT_PATHSPECS
    assert "wrote skill" in capsys.readouterr().out
    assert cli.main(["install-skill", "--config", cfg]) == cli.EXIT_OK
    assert "skill up to date" in capsys.readouterr().out


def test_procedure_says_what_each_curate_row_asks() -> None:
    """Every refresh-queue verdict curate prints has its action in the procedure; the opposite ones stay
    opposite (a SOURCE-UNREADABLE row is never re-curated on, a SOURCE-DELETED one is re-cited or retired)."""
    text = skill.procedure()
    for verdict in (*curate.VERDICTS, "UNCOVERED", "ADDED"):
        assert f"`{verdict}`" in text, verdict
    unreadable = text[text.index("`SOURCE-UNREADABLE`") : text.index("`MISSING-OR-UNPARSEABLE`")]
    assert "_sync/QUARANTINE.tsv" in unreadable and "never re-curate" in unreadable
    assert "retire" in text[text.index("`SOURCE-DELETED`") : text.index("`SOURCE-UNREADABLE`")]


def test_skill_text_names_the_fixed_binary_and_ends_with_sync() -> None:
    text = skill.skill_text(Path("/srv/docs"))
    assert f"`{skill.AGENTSYNC_BIN} sync`" in text
    assert "Use at the start of any work session that needs company" in text
    assert "75" not in text and "checkpoint`" not in text  # no exit-75 advice, no manual checkpoint step
    assert "agentsync sync --once" not in text
    assert "git -C" not in text and "curate-queue" not in text
    assert text.splitlines()[text.splitlines().index("## Every session") + 2] == (
        f"1. Run `{skill.AGENTSYNC_BIN} sync`."
    )
    assert text.rstrip().endswith(f"Commit `_eval/` with `{skill.AGENTSYNC_BIN} sync`.")


def test_sync_writes_both_copies_once(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    cfg = str(initialised.config_path)
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    copies = [home_skill(), config_dir / "skills" / skill.SKILL_NAME / "SKILL.md"]
    expected = skill.skill_text(initialised.docs_repo)
    for path in copies:
        assert path.read_text(encoding="utf-8") == expected
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))  # any rewrite would move the mtime off this
    assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert [p.stat().st_mtime_ns for p in copies] == [1_000_000_000, 1_000_000_000]


def test_sync_without_claude_config_dir_writes_only_the_home_copy(
    initialised: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "CLAUDE_CONFIG_DIR" not in os.environ  # conftest isolation
    assert skill.skill_paths() == [home_skill()]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))  # the same folder: one copy
    assert skill.skill_paths() == [home_skill()]
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    assert sorted(Path.home().rglob("SKILL.md")) == [home_skill()]  # one copy, nowhere else under HOME


def test_dry_run_writes_no_skill(initialised: Config) -> None:
    assert cli.main(["sync", "--dry-run", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    assert not home_skill().exists()


def test_unwritable_skills_folder_still_exits_0(
    initialised: Config, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = Path.home() / ".claude" / "skills"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a folder\n", encoding="utf-8")  # mkdir under it fails, whoever runs the test
    cfg = str(initialised.config_path)
    with caplog.at_level(logging.WARNING, logger="agentsync.skill"):
        assert cli.main(["sync", "--config", cfg]) == cli.EXIT_OK
    assert "skill: cannot write" in caplog.text
    assert cli.main(["install-skill", "--config", cfg]) == cli.EXIT_OK
    assert "wrote skill" not in capsys.readouterr().out


def test_symlink_loop_config_skills_still_exits_0(
    initialised: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Python 3.11's ``Path.resolve()`` raises RuntimeError on a symlink loop; the skill write stays a
    warning and the HOME copy is still written."""
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / "skills").symlink_to(config_dir / "skills")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    with caplog.at_level(logging.WARNING, logger="agentsync.skill"):
        assert cli.main(["sync", "--config", str(initialised.config_path)]) == cli.EXIT_OK
    assert "skill: cannot write" in caplog.text
    assert home_skill().is_file()


def test_monkeypatch_undo_keeps_home_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """test_cycle.py heals injected faults with ``monkeypatch.undo()``, then syncs; that sync writes the
    skill, so the conftest isolation must survive the undo."""
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real_home / ".claude"))
    monkeypatch.undo()
    assert Path.home() != real_home and "CLAUDE_CONFIG_DIR" not in os.environ
    assert all(not p.is_relative_to(real_home / ".claude") for p in skill.skill_paths())
