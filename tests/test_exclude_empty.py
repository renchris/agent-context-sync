"""``agentsync exclude-empty SOURCE`` (B13 round 4 Q71): the empty-folder wait's one command in place of a
hand edit of sources.toml. It excludes every empty cloud folder the last walk stored that holds no mirrored
file, edits only that table's ``exclude`` and checks the file before it writes it."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentsync import cli, loop
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
    assert "(7 empty cloud folders added)" in out and "not mirrored if it later gains files" in out
    assert out.rstrip().endswith("the next sync lists work without those folders"), out  # B13 round 12 N40
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
    assert "(1 empty cloud folder added)" in out
    assert "1 empty cloud folder in work was left as it is" in out
    # B13 round 12 N40: "without them" after one folder, beside "files", and the left one is "it"
    assert "the next sync lists work without that folder; " in out and "without them" not in out, out
    assert "which names it" in out and "which names them" not in out, out
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
    # B13 round 5 F65: the wait takes the command's rule, so it never names a command that leaves its folder
    # (the wait would never clear); its own line says what does clear it, and names no folder.
    run_cycle(load_config(cfg), mode=None)
    lines = loop.next_lines(load_config(cfg), count_queue=False)
    assert not any("exclude-empty" in ln for ln in lines), lines
    (wait,) = [ln for ln in lines if ln.startswith("WAITING ON YOU: 1 empty cloud folder in work has a [")]
    assert "rename or remove the empty folder in the cloud drive" in wait
    # B13 round 14 Q23, Q41: one folder stored, which the alarm names in full: no five clause
    assert "STATE.md keeps the alarm line for work, which names it (a plain " in wait and "Plans" not in wait
    assert "(a plain `~/.local/bin/agentsync sync` prints it too)" in wait and "sync -v" not in wait
    assert "the mirror still has" not in wait, "not the gone line: the folder held no file"


class _Rows:
    """A manifest with stored empty folders and file rows; counts how often the rows are read."""

    def __init__(self, stored: str, files: list[str]) -> None:
        self.stored, self.files, self.walks = stored, files, 0

    def get_meta(self, key: str) -> str | None:
        return self.stored if key.startswith("empty_cloud_dirs:") else None

    def iter_items(self, source_id: str, *, states: object) -> object:
        self.walks += 1
        return (SimpleNamespace(rel_path=rel, is_dir=False) for rel in self.files)


def test_a_folder_renamed_in_case_upstream_counts_the_files_still_held_under_it(tmp_path: Path) -> None:
    """B13 round 6 G55: the exclude glob matches in any case, but the files below a stored empty folder were
    counted by exact case, so after a case-only rename upstream (the old rows held while the listing is
    incomplete) REPORTS read as holding no file. It counts them now, and is no folder exclude-empty offers;
    the rows are read once for the counts and the folders holding files, not twice."""
    cfg = _setup(tmp_path, local_source_table("work", _root()))
    src = load_config(cfg).source("work")
    rows = _Rows('["REPORTS", "Drafts"]', ["Reports/a.docx", "Reports/2026/b.docx", "Plans/c.docx"])
    found = loop._empty_dirs(rows, src)  # type: ignore[arg-type]
    assert found.below == {"REPORTS": 2, "Drafts": 0}
    assert loop._excludable(found) == (["Drafts"], [])
    assert {"Reports", "Reports/2026", "Plans"} <= found.holding
    assert rows.walks == 1


_ACQUIRE_WAITING = cli._acquire_waiting


def _edit_during_the_wait(monkeypatch: pytest.MonkeyPatch, cfg: Path, old: str, new: str) -> None:
    """Make the lock wait (``_acquire_waiting``) the moment a hand edit of sources.toml lands."""
    real = _ACQUIRE_WAITING

    def waiting(lock: object, wait_s: float) -> None:
        text = cfg.read_text(encoding="utf-8")
        assert old in text
        cfg.write_text(text.replace(old, new), encoding="utf-8")
        real(lock, wait_s)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "_acquire_waiting", waiting)


def test_an_exclude_edited_during_the_lock_wait_is_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """F70 (B13 round 5): the new exclude was built from the config read before the lock wait (up to 600 s),
    so a glob added by hand during the wait was written over with no word. sources.toml is read again once
    the lock is held."""
    root = _root()
    (root / "Empty").mkdir()
    cfg = _setup(tmp_path, local_source_table("work", root) + 'exclude = ["*.bak"]\n')
    run_cycle(load_config(cfg), mode=None)
    _edit_during_the_wait(monkeypatch, cfg, 'exclude = ["*.bak"]', 'exclude = ["*.bak", "/Private/"]')
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK, out
    assert load_config(cfg).source("work").exclude == ("*.bak", "/Private/", "/Empty/")


def test_a_source_removed_or_a_config_broken_during_the_lock_wait_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """F70: what the read after the wait finds is reported as such, with no traceback and no write."""
    root = _root()
    (root / "Empty").mkdir()
    table = local_source_table("work", root)
    cfg = _setup(tmp_path, table)
    run_cycle(load_config(cfg), mode=None)
    _edit_during_the_wait(monkeypatch, cfg, 'id = "work"', 'id = "other"')
    assert cli.main(["exclude-empty", "work", "--config", str(cfg)]) == cli.EXIT_USAGE
    assert "no local or inbox source with that id" in capsys.readouterr().err
    assert "/Empty/" not in cfg.read_text(encoding="utf-8")
    _edit_during_the_wait(monkeypatch, cfg, 'id = "other"', 'id = "other"\nnot toml [')
    capsys.readouterr()
    assert cli.main(["exclude-empty", "other", "--config", str(cfg)]) == cli.EXIT_CONFIG
    assert "exclude-empty other: sources.toml does not load: " in capsys.readouterr().err


def test_the_write_refuses_an_exclude_changed_since_it_was_read(tmp_path: Path) -> None:
    """F70: between the read the new list is built from and the write, an edit of the same key refuses the
    write (exit 78 and the paste line in the CLI) instead of dropping the edit."""
    cfg = _file(tmp_path)
    before = cfg.read_bytes()
    with pytest.raises(ConfigError, match="'alpha''s exclude changed since it was read"):
        set_source_exclude(cfg, "alpha", ["*.bak", "/Empty/"], expected=("*.bak",))
    assert cfg.read_bytes() == before
    config = set_source_exclude(cfg, "alpha", ["*.bak", "~$*", "/Empty/"], expected=("*.bak", "~$*"))
    assert config.source("alpha").exclude == ("*.bak", "~$*", "/Empty/")
