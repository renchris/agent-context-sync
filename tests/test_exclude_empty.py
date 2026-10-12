"""``agentsync exclude-empty SOURCE`` (B13 round 4 Q71): the empty-folder wait's one command in place of a
hand edit of sources.toml. It excludes every empty cloud folder the last walk stored that holds no mirrored
file, edits only that table's ``exclude`` and checks the file before it writes it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentsync import cli
from agentsync.config import inbox_source_table, load_config, local_source_table, set_source_exclude
from agentsync.cycle import run_cycle
from agentsync.errors import ConfigError
from agentsync.manifest import Manifest

CLOUD = Path("Library") / "CloudStorage" / "OneDrive-Contoso"


def _setup(tmp_path: Path, *tables: str) -> Path:
    """``init`` (no source), then ``tables`` in place of the inbox it adds; returns sources.toml."""
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    (box,) = load_config(cfg).sources
    assert box.path is not None
    text = cfg.read_text(encoding="utf-8").removesuffix(inbox_source_table(box.id, box.path))
    cfg.write_text(text + "".join(tables), encoding="utf-8")
    return cfg


def _root(name: str = "Work") -> Path:
    root = Path(os.environ["HOME"]) / CLOUD / name
    root.mkdir(parents=True)
    (root / "Quarterly Note.txt").write_text("The purchase order is approved.\n", encoding="utf-8")
    return root


def _exclude_empty(cfg: Path, sid: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    rc = cli.main(["exclude-empty", sid, "--config", str(cfg)])
    return rc, capsys.readouterr().out


def test_every_empty_folder_is_excluded_once_and_the_listing_completes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seven empty folders, past the five a paste line named: one run excludes them all, glob characters
    made literal, the configured globs kept; a second run has nothing to do; the next sync is complete."""
    root = _root()
    names = [f"Empty {n}" for n in range(6)] + ["Plans [old]"]
    for name in names:
        (root / name).mkdir()
    cfg = _setup(tmp_path, local_source_table("work", root) + 'exclude = ["*.bak"]\n')
    run_cycle(load_config(cfg), mode=None)
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK, out
    assert "(7 empty cloud folder(s) added)" in out and "not mirrored if it later gains files" in out
    src = load_config(cfg).source("work")
    assert src.exclude == ("*.bak", *(f"/Empty {n}/" for n in range(6)), "/Plans ?old]/")
    assert _exclude_empty(cfg, "work", capsys) == (cli.EXIT_OK, "exclude-empty work: nothing to exclude\n")
    config = load_config(cfg)
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.get_source("work").enumeration_complete  # type: ignore[union-attr]


def test_a_folder_that_held_mirrored_files_is_left_and_named_as_a_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Excluding a folder that held mirrored files would retire their pages at once, past the deletion
    check: it is left as it is, and the output counts it (never a name)."""
    root = _root()
    (root / "Empty").mkdir()
    (root / "Gone").mkdir()
    (root / "Gone" / "Kickoff.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    (root / "Gone" / "Kickoff.txt").unlink()
    run_cycle(load_config(cfg), mode=None)
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK, out
    assert "(1 empty cloud folder(s) added)" in out
    assert "1 empty cloud folder(s) in work were left as they are" in out
    assert "Gone" not in out
    assert load_config(cfg).source("work").exclude[-1] == "/Empty/"
    assert "/Gone/" not in load_config(cfg).source("work").exclude


def test_nothing_stored_and_an_unknown_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _root()
    cfg = _setup(tmp_path, local_source_table("work", root))
    before = cfg.read_bytes()
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK and out.startswith("exclude-empty work: nothing to exclude"), out
    assert cli.main(["exclude-empty", "nope", "--config", str(cfg)]) == cli.EXIT_USAGE
    assert cfg.read_bytes() == before


TABLES = """\
# my sources: keep this comment
[[source]]
id = "alpha"   # first
kind = "local"
path = "/tmp/alpha"
exclude = [
  "*.bak",   # old copies
  "~$*",
]
# between the tables
[[source]]
id = "beta"
kind = "local"
path = "/tmp/beta"
cadence_s = 900
"""


def _file(tmp_path: Path) -> Path:
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text + TABLES, encoding="utf-8")
    cfg.chmod(0o640)
    return cfg


def test_a_multi_line_exclude_becomes_one_line_and_every_other_byte_stays(tmp_path: Path) -> None:
    cfg = _file(tmp_path)
    before = cfg.read_text(encoding="utf-8")
    set_source_exclude(cfg, "alpha", ["*.bak", "~$*", "/Empty/"])
    after = cfg.read_text(encoding="utf-8")
    old = 'exclude = [\n  "*.bak",   # old copies\n  "~$*",\n]\n'
    assert old in before
    assert after == before.replace(old, 'exclude = ["*.bak", "~$*", "/Empty/"]\n')
    assert cfg.stat().st_mode & 0o777 == 0o640, "the file's mode is kept"


def test_a_table_with_no_exclude_gets_one_under_its_id(tmp_path: Path) -> None:
    cfg = _file(tmp_path)
    before = cfg.read_text(encoding="utf-8")
    config = set_source_exclude(cfg, "beta", ["~$*", "/Empty/"])
    assert config.source("beta").exclude == ("~$*", "/Empty/")
    assert cfg.read_text(encoding="utf-8") == before.replace(
        'id = "beta"\n', 'id = "beta"\nexclude = ["~$*", "/Empty/"]\n'
    )
    assert config.source("alpha").exclude == ("*.bak", "~$*")


def test_an_edit_that_cannot_be_made_as_text_writes_nothing(tmp_path: Path) -> None:
    cfg = _file(tmp_path)
    before = cfg.read_bytes()
    with pytest.raises(ConfigError, match="no \\[\\[source\\]\\] table with id 'gamma'"):
        set_source_exclude(cfg, "gamma", ["/Empty/"])
    assert cfg.read_bytes() == before


def test_a_crlf_file_stays_crlf_and_a_comment_on_the_key_stays(tmp_path: Path) -> None:
    """Review of Q71: the edit kept every other byte except the line endings and the key's own comment."""
    cfg = _file(tmp_path)
    text = cfg.read_text(encoding="utf-8").replace(
        'exclude = [\n  "*.bak",   # old copies\n  "~$*",\n]\n', 'exclude = ["*.bak"]  # keep backups out\n'
    )
    cfg.write_bytes(text.replace("\n", "\r\n").encode())
    set_source_exclude(cfg, "alpha", ["*.bak", "/Empty/"])
    after = cfg.read_bytes().decode()
    assert after == text.replace(
        'exclude = ["*.bak"]  # keep backups out\n', 'exclude = ["*.bak", "/Empty/"]  # keep backups out\n'
    ).replace("\n", "\r\n")


def test_a_folder_whose_glob_would_match_a_folder_with_files_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of Q71: a "[" in a name is written as "?", so "/Plans ?old]/" also prunes "Plans Xold]"; when
    that folder holds mirrored files, the empty one is left, so no page is retired past the deletion check."""
    root = _root()
    (root / "Plans [old]").mkdir()
    (root / "Plans Xold]").mkdir()
    (root / "Plans Xold]" / "Kickoff.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    before = cfg.read_bytes()
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK and out.startswith("exclude-empty work: nothing to exclude; 1 empty"), out
    assert cfg.read_bytes() == before
