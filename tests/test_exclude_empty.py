"""``agentsync exclude-empty SOURCE`` (B13 round 4 Q71): the empty-folder wait's one command in place of a
hand edit of sources.toml. It excludes every empty cloud folder the last walk stored that holds no mirrored
file, edits only that table's ``exclude`` and checks the file before it writes it."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentsync import cli, governance, loop
from agentsync.config import inbox_source_table, load_config, local_source_table, set_source_exclude
from agentsync.cycle import run_cycle
from agentsync.errors import ConfigError
from agentsync.manifest import Manifest
from agentsync.paths import expand

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


@pytest.mark.parametrize("files", [(1, 1), (2, 1)], ids=["one-each", "two-and-one"])
def test_two_emptied_folders_are_them_and_those_pages_whatever_each_held(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], files: tuple[int, int]
) -> None:
    """B13 round 18 V43: two emptied folders that each held one mirrored file read "excluding such a folder
    retires its pages", in exclude-empty and in the sync's wait. Two or more folders are "them" and "those
    pages"; one folder keeps "such a folder" and its page or pages."""
    root = _root()
    for name, n in zip(("Held A", "Held B"), files, strict=True):
        for i in range(n):
            (root / name).mkdir(exist_ok=True)
            (root / name / f"Note {i}.txt").write_text("Contoso notes.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    for name, n in zip(("Held A", "Held B"), files, strict=True):
        for i in range(n):
            (root / name / f"Note {i}.txt").unlink()
    run_cycle(load_config(cfg), mode=None)
    waits = [ln for ln in loop.next_lines(load_config(cfg)) if ln.startswith("WAITING ON YOU")]
    [held] = [ln for ln in waits if " held " in ln]
    assert "excluding them instead retires those pages at once, with no deletion check" in held, held
    assert "such a folder" not in held and "its pages" not in held, held
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK, out
    assert "(excluding them retires those pages at once, with no deletion check)" in out, out
    assert "such a folder" not in out and "its pages" not in out, out


def test_beside_a_fail_the_syncs_guard_stops_on_the_left_folders_pointer_waits_on_it_c520(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 31 C520 (ruling 60): with holds.json a folder, which the sync's guard stops on,
    exclude-empty's pointer at the alarm said a plain sync prints it again, while every sync stopped before
    it; it names the check whose [FAIL] stops it, as status's wait does."""
    root = _root()
    (root / "Empty").mkdir()
    (root / "Gone").mkdir()
    (root / "Gone" / "Kickoff.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    (root / "Gone" / "Kickoff.txt").unlink()
    run_cycle(load_config(cfg), mode=None)
    governance._holds_file(expand(load_config(cfg).state_paths.root)).mkdir(parents=True)
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK, out
    # B13 round 32 C631 (ruling 65): and that no sync runs until then, said once in the line
    assert (
        "once the governance.hold [FAIL] line is fixed, a plain `~/.local/bin/agentsync sync` prints it "
        "again with the 1 folder left; until then no sync runs"
    ) in out, out
    assert out.count("until then no sync runs") == 1 and out.count("[FAIL] line is fixed") == 1, out
    assert "; the next sync lists work without that folder; " in out, out


def test_beside_a_fail_the_syncs_guard_stops_on_the_head_says_no_sync_runs_c631(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 32 C631 (ruling 65): with no folder left for the alarm to name, exclude-empty's head said
    the next sync lists the source without the folder, while every sync stopped on holds.json as a folder.
    The head names the FAIL and that no sync runs until it is fixed."""
    root = _root()
    (root / "Empty").mkdir()
    (root / "Note.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    governance._holds_file(expand(load_config(cfg).state_paths.root)).mkdir(parents=True)
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK, out
    assert out.rstrip().endswith(
        "files; once the governance.hold [FAIL] line is fixed, the next sync lists work without that "
        "folder; until then no sync runs"
    ), out


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
    # B13 round 16 T38: the count it knows and the one cause it has, not "file(s) ..., or its exclude ..."
    assert (
        "1 empty cloud folder in work was left as it is: it held 1 file the mirror still has (excluding such "
        "a folder retires its page at once, with no deletion check); "
    ) in out, out
    assert "its pages" not in out, out  # B13 round 17 U51: one file is one page
    assert "file(s)" not in out and "or its exclude" not in out and "has a [" not in out, out
    # B13 round 12 N40: "without them" after one folder, beside "files", and the left one is "it"
    assert "the next sync lists work without that folder; " in out and "without them" not in out, out
    assert "which names them" not in out, out
    # B13 round 15 S32: the alarm it points at is from before this exclude and names the folder it excluded
    assert "from its last sync, before the exclude, so it may name folders now excluded" in out, out
    assert "prints it again with the 1 folder left" in out and "which names it" not in out, out
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
    # B13 round 16 T38: the cause in the loop's words, no hedge, and no "held" for a folder that held nothing
    assert (
        "; 1 empty cloud folder in work was left as it is: it has a [ in its name, so an exclude of it would "
        "also match a folder whose files are mirrored: rename or remove the empty folder in the cloud drive; "
    ) in out, out
    assert "held" not in out and "file(s)" not in out and "or its exclude" not in out, out
    # B13 round 5 F65: the wait takes the command's rule, so it never names a command that leaves its folder
    # (the wait would never clear); its own line says what does clear it, and names no folder.
    run_cycle(load_config(cfg), mode=None)
    lines = loop.next_lines(load_config(cfg), count_queue=False)
    assert not any("exclude-empty" in ln for ln in lines), lines
    (wait,) = [ln for ln in lines if ln.startswith("WAITING ON YOU: 1 empty cloud folder in work has a [")]
    assert "rename or remove the empty folder in the cloud drive" in wait
    # B13 round 14 Q23, Q41: one folder stored, which the alarm names in full: no five clause
    # B13 round 17 U49: the noun form, as the clear and gone waits say it
    assert "STATE.md keeps the alarm line for work, which names the folder (a plain " in wait, wait
    assert "Plans" not in wait
    assert "(a plain `~/.local/bin/agentsync sync` prints it too)" in wait and "sync -v" not in wait
    assert "the mirror still has" not in wait, "not the gone line: the folder held no file"


def test_folders_left_for_two_causes_are_said_apart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 16 T38: two gone folders (2 and 1 files) beside two whose glob collides read "each held
    file(s) ..., or its exclude would also match ..."; each cause now has its own clause and count, the
    files summed, since each folder's count differs."""
    root = _root()
    for name, files in (("Gone A", 2), ("Gone B", 1)):
        for i in range(files):
            (root / name).mkdir(exist_ok=True)
            (root / name / f"Note {i}.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    for name in ("Plans [a]", "Plans [b]"):
        (root / name).mkdir()
    for name in ("Plans xa]", "Plans xb]"):
        (root / name).mkdir()
        (root / name / "Kickoff.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    for name, files in (("Gone A", 2), ("Gone B", 1)):
        for i in range(files):
            (root / name / f"Note {i}.txt").unlink()
    run_cycle(load_config(cfg), mode=None)
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK and out.startswith("exclude-empty work: nothing to exclude; "), out
    # B13 round 17 U50: one head with the total, then each cause with its own count (the head said twice)
    assert (
        "; 4 empty cloud folders in work were left as they are: 2 held 3 files the mirror still has "
        "(excluding them retires those pages at once, with no deletion check); 2 have a [ in the "
        "name, so an exclude of one would also match a folder whose files are mirrored: rename or remove "
        "them in the cloud drive; "
    ) in out, out
    assert out.count("left as") == 1, out
    assert "each held" not in out and "file(s)" not in out and "or its exclude" not in out, out


def test_one_folder_left_for_each_cause_is_one_head_with_singular_clauses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 17 U49, U50, U51: an empty 'Team [A]' beside a mirrored 'Team xA]', and a folder whose one
    file was deleted. exclude-empty says one head, "2 ... were left as they are", then "1 held 1 file ...
    its page" and "1 has a [ in its name ... rename or remove it", never "was left as it is" twice; the
    loop's collides and gone waits both point at the alarm by noun, "which names the folders", never
    "which names them" with no plural before it."""
    root = _root()
    (root / "Team [A]").mkdir()
    (root / "Team xA]").mkdir()
    (root / "Team xA]" / "Kickoff.txt").write_text("Kickoff agenda.\n", encoding="utf-8")
    (root / "Held Folder").mkdir()
    (root / "Held Folder" / "Plan.txt").write_text("The plan.\n", encoding="utf-8")
    cfg = _setup(tmp_path, local_source_table("work", root))
    run_cycle(load_config(cfg), mode=None)
    (root / "Held Folder" / "Plan.txt").unlink()
    run_cycle(load_config(cfg), mode=None)
    lines = loop.next_lines(load_config(cfg), count_queue=False)
    waits = [ln for ln in lines if ln.startswith("WAITING ON YOU: 1 empty cloud folder in work ")]
    assert len(waits) == 2, lines
    for wait in waits:
        assert "STATE.md keeps the alarm line for work, which names the folders (a plain " in wait, wait
        assert "which names them" not in wait, wait
    rc, out = _exclude_empty(cfg, "work", capsys)
    assert rc == cli.EXIT_OK and out.startswith("exclude-empty work: nothing to exclude; "), out
    assert (
        "; 2 empty cloud folders in work were left as they are: 1 held 1 file the mirror still has "
        "(excluding such a folder retires its page at once, with no deletion check); 1 has a [ in its name, "
        "so an exclude of it would also match a folder whose files are mirrored: rename or remove it in the "
        "cloud drive; "
    ) in out, out
    assert out.count("left as") == 1 and "was left as it is" not in out, out
    assert "which names the folders" in out and "which names them" not in out, out


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

    def waiting(lock: object, wait_s: float, **kw: object) -> None:
        text = cfg.read_text(encoding="utf-8")
        assert old in text
        cfg.write_text(text.replace(old, new), encoding="utf-8")
        real(lock, wait_s, **kw)  # type: ignore[arg-type]

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


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-000 file")
def test_a_sync_over_a_heartbeat_it_cannot_open_still_prints_the_waits_its_alarm_points_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 24 B43: over a heartbeat at 0000 an interactive sync printed the empty-folder alarm, which
    names "the WAITING ON YOU line ... at the end of a sync's output", then its one NEXT and stopped: no
    WAITING ON YOU line. It prints the loop's waits after its NEXT, the same as status's, and still one NEXT
    and one stderr line."""
    root = _root()
    (root / "Empty A").mkdir()
    cfg = _setup(tmp_path, local_source_table("work", root))
    capsys.readouterr()
    for _ in range(2):  # the second pass's listing is the one the wait names
        assert cli.main(["sync", "--config", str(cfg)]) == cli.EXIT_OK
    first = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("WAITING ON YOU: ") for ln in first), "\n".join(first)
    hb = load_config(cfg).state_paths.heartbeat
    hb.chmod(0)
    try:
        rc = cli.main(["sync", "--config", str(cfg)])
        said = capsys.readouterr()
        cli.main(["status", "--config", str(cfg)])
        status = capsys.readouterr().out.splitlines()
    finally:
        hb.chmod(0o600)
    lines = said.out.splitlines()
    assert rc == cli.EXIT_FAILED, said
    assert [ln for ln in lines if ln.startswith("NEXT: ")] == [f"NEXT: {cli.HEARTBEAT_REFUSED_STEP}"], lines
    waits = [ln for ln in lines if ln.startswith(loop.WAIT_PREFIX)]
    assert any("empty cloud folder" in ln for ln in waits), waits
    assert waits == [ln for ln in status if ln.startswith(loop.WAIT_PREFIX)], (waits, status)
    assert lines.index(waits[0]) > lines.index(f"NEXT: {cli.HEARTBEAT_REFUSED_STEP}")
    assert [ln for ln in said.err.splitlines() if ln.strip()] == [
        ln for ln in said.err.splitlines() if ln.startswith("error: state: ")
    ] and len([ln for ln in said.err.splitlines() if ln.strip()]) == 1, said.err
