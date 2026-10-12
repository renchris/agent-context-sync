"""KISS K01: ``loop.next_step`` works the loop's next step out of disk state, one fixture per rule (1-10) and
per operator wait, asserting the exact NEXT / WAITING ON YOU / note lines. No line names a mirror path or
file."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from errno import EDEADLK
from pathlib import Path

import pytest

from agentsync import arm_local, cli, governance, loop, materialise, skill
from agentsync import cycle as cycle_module
from agentsync.config import Config, ensure_inbox, inbox_source_table, load_config, local_source_table
from agentsync.cycle import HYDRATION_REFUSED, LISTING_HELD, NETWORK_POLICY_FAILED, RECORDING_WAITS, run_cycle
from agentsync.errors import DatalessRefusedError
from agentsync.manifest import Manifest
from agentsync.model import CycleMode, PassKind, RowState, Verdict
from agentsync.ops.doctor import CheckResult, Severity
from test_cli import _assert_fix_parses

BIN = "~/.local/bin/agentsync"
BASELINE = 'the agentsync-docs skill\'s "Baseline questions" section (also in ~/agent-context/docs/AGENTS.md)'
"""Where rules 4, 6 and 8 send the agent: the skill's section, and the docs repo's AGENTS.md by its path
(every test here keeps the default docs repo under its own HOME)."""
DRAFT_WAIT = (
    "WAITING ON YOU: the baseline questions are a draft: in {}, keep about 10 in questions.md, correct the "
    "answers in answers.md, and change both files to status: confirmed"
)
"""The draft baseline's wait; ``{}`` is the docs repo's ``_eval`` folder as the line shows it."""
NOTE_NAME = "Quarterly Note.txt"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A synced folder holding one small text file."""
    root = tmp_path / "Work"
    root.mkdir()
    (root / NOTE_NAME).write_text("The purchase order is approved.\n", encoding="utf-8")
    return root


def _setup(tmp_path: Path, *tables: str) -> Config:
    """``init`` with no source (a docs repo and the template sources.toml), then ``tables`` appended in
    place of the inbox init adds (KISS K05), which a test adds back with ``ensure_inbox``."""
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    (box,) = load_config(cfg).sources
    assert box.path is not None
    text = cfg.read_text(encoding="utf-8").removesuffix(inbox_source_table(box.id, box.path))
    cfg.write_text(text + "".join(tables), encoding="utf-8")
    return load_config(cfg)


def _synced(tmp_path: Path, folder: Path, extra: str = "") -> Config:
    config = _setup(tmp_path, local_source_table("work", folder) + extra)
    assert run_cycle(config, mode=None).exit_code == 0
    return config


def _lines(config: Config) -> list[str]:
    """next_step's lines, checked for leaks: no mirror path and no source file name, ever."""
    lines = loop.next_step(config).lines()
    for line in lines:
        low = line.lower()
        assert "mirror/" not in low and not any(n in low for n in ("quarterly", "big.bin", "small.txt")), line
    return lines


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _eval(config: Config, questions: str, answers: str, *results: str) -> None:
    root = config.docs_repo / "_eval"
    _write(root / "questions.md", f"status: {questions}\n\n1. Who approved the purchase order?\n")
    _write(root / "answers.md", f"status: {answers}\n\n1. Finance.\n")
    for kind in results:
        _write(root / f"results-2026-10-04-{kind}.md", "1. Finance. (1 search, 1 file)\n")


def _hand_written(config: Config, n: int) -> None:
    for i in range(n):
        _write(
            config.docs_repo / "topics" / "area" / f"page-{i:02d}.md",
            "---\nprovenance: hand-written\nsources: []\n---\nA hand-made page.\n",
        )


# ---- rule 1: a status FAIL names its fix -----------------------------------------------------------------


def test_rule_1_missing_docs_repo(tmp_path: Path, folder: Path) -> None:
    config = _setup(tmp_path, local_source_table("work", folder))
    shutil.rmtree(config.docs_repo)
    assert _lines(config) == [
        f'NEXT: the docs repo does not exist yet: run `{BIN} add-source "<folder>"` with the folder of '
        "source 'work' (it creates whatever is missing)"
    ]
    assert " init" not in _lines(config)[0]  # KISS K14: init is hidden, NEXT names the visible verb


def test_rule_1_missing_docs_repo_without_a_folder(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    shutil.rmtree(config.docs_repo)
    assert _lines(config) == [
        f'NEXT: the docs repo does not exist yet: run `{BIN} add-source "<folder>"` with a folder to sync '
        "(it creates whatever is missing)"
    ]


def test_rule_1_missing_or_stale_skill(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    want = [
        f"NEXT: the agentsync-docs skill is missing or out of date: run `{BIN} sync` (it writes the skill; "
        "if this line is still here after that sync, the skills folder cannot be written: tell the operator)"
    ]
    (path,) = skill.skill_paths()
    path.write_text("an older skill\n", encoding="utf-8")
    assert _lines(config) == want
    path.unlink()
    assert _lines(config) == want
    assert loop.next_step(config).rule == 1


def test_rule_1_graph_sign_in_then_the_callers_fix(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    text = config.config_path.read_text(encoding="utf-8")
    text += '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000001"\n'  # no [graph] in the template
    text += '\n[[source]]\nid = "drive"\nkind = "graph_drive"\ndrive_id = "me"\n'
    config.config_path.write_text(text, encoding="utf-8")
    config = load_config(config.config_path)
    skill.write_skill(config.docs_repo)
    with Manifest(config.state_paths.db) as manifest:
        manifest.sync_sources(config.sources)
        manifest.set_auth_state("REAUTH_REQUIRED", ["drive"])
    assert _lines(config) == [f"NEXT: sign-in required for drive: run `{BIN} graph login`"]
    with Manifest(config.state_paths.db) as manifest:
        manifest.set_auth_state("ok", ["drive"])
    step = loop.next_step(config, fixes=["run the fix doctor names", "a second fix"])
    assert (step.rule, step.step) == (1, "run the fix doctor names")


# ---- rule 2: nothing to sync -----------------------------------------------------------------------------


def test_rule_2_no_folder_source(tmp_path: Path) -> None:
    config = _setup(tmp_path)
    skill.write_skill(config.docs_repo)
    want = [
        "NEXT: no folder is synced yet: ask the operator which folders to sync "
        "(`~/src/agent-context-sync/scripts/install.sh --list-folders` lists them), then run "
        f'`{BIN} add-source "<folder>"` for each, then `{BIN} sync`'
    ]
    assert _lines(config) == want
    assert ensure_inbox(config.config_path)[1] is not None
    assert _lines(load_config(config.config_path)) == want, "the inbox alone is not a folder source"


# ---- rule 3: the sync has not caught up --------------------------------------------------------------------


def test_rule_3_never_synced(tmp_path: Path, folder: Path) -> None:
    config = _setup(tmp_path, local_source_table("work", folder))
    skill.write_skill(config.docs_repo)
    assert _lines(config) == [
        f"NEXT: 1 source(s) not fully listed or converted yet (work): run `{BIN} sync` again"
    ]


def test_rule_3_a_local_file_not_converted_yet(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    with Manifest(config.state_paths.db) as manifest:
        (row,) = [r for r in manifest.iter_items("work") if not r.is_dir]
        manifest.set_verdict("work", row.stable_id, Verdict.DEFERRED)  # e.g. over the source's max_files
    assert _lines(config) == [
        f"NEXT: 1 source(s) not fully listed or converted yet (work): run `{BIN} sync` again"
    ]


# ---- online-only files never hold the loop at rule 3 ------------------------------------------------------


def test_online_only_deferrals_are_a_note_and_reach_rules_4_to_9(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A budget-0 first sync defers both online-only files; the one larger than the source's budget can never
    download on its own (a WAITING line), the other is a note. Neither holds the loop at rule 3."""
    (folder / "big.bin.txt").write_text("x" * 4000, encoding="utf-8")
    (folder / "small.txt").write_text("small\n", encoding="utf-8")
    online = {(folder / n).stat().st_ino for n in ("big.bin.txt", "small.txt")}
    real = materialise.is_dataless

    def fake(st: os.stat_result) -> bool:  # mocked SF_DATALESS: no File Provider in a test
        return st.st_ino in online or real(st)

    monkeypatch.setattr(materialise, "is_dataless", fake)
    monkeypatch.setattr(arm_local, "is_dataless", fake)
    config = _setup(tmp_path, local_source_table("work", folder) + "max_materialise_bytes = 1000\n")
    assert run_cycle(config, mode=None, budget_bytes=0).exit_code == 0
    waits_and_note = [
        "WAITING ON YOU: 1 online-only file(s) in work are larger than the per-run download budget: run "
        f"`{BIN} materialise --budget BYTES` with BYTES above their size, or raise that source's "
        "max_materialise_bytes",
        "note: 1 online-only file(s) in work wait for a later sync's download budget; they do not block the "
        "next step",
    ]
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`",
        *waits_and_note,
    ]
    _eval(config, "draft", "draft")
    assert _lines(config)[0].startswith("NEXT: stop: the operator confirms")
    _eval(config, "confirmed", "confirmed")
    assert loop.next_step(config).rule == 6
    _eval(config, "confirmed", "confirmed", "before")
    _hand_written(config, 1)
    assert _lines(config) == [
        f"NEXT: 1 curation row(s) queued: run `{BIN} curate`, curate up to 10 of them, then run "
        f"`{BIN} sync`; session done",
        *waits_and_note,
    ]


def test_an_os_refused_download_is_a_wait_until_a_later_sync_reads_it(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS refusing a download (EDEADLK) is no budget matter: a WAITING line, not the budget note, and never
    rule 3. A later sync that reads the file clears it."""
    (folder / "small.txt").write_text("small\n", encoding="utf-8")
    online = {(folder / "small.txt").stat().st_ino}
    real_dataless, real_materialise = materialise.is_dataless, arm_local.materialise

    def fake(st: os.stat_result) -> bool:  # mocked SF_DATALESS: no File Provider in a test
        return st.st_ino in online or real_dataless(st)

    def refuse(src: Path, dest: Path, budget: materialise.ByteBudget) -> materialise.MaterialiseResult:
        if src.name == "small.txt":
            raise DatalessRefusedError(str(src), EDEADLK, "materialisation refused (EDEADLK)")
        return real_materialise(src, dest, budget)

    monkeypatch.setattr(materialise, "is_dataless", fake)
    monkeypatch.setattr(arm_local, "is_dataless", fake)
    monkeypatch.setattr(arm_local, "materialise", refuse)
    config = _setup(tmp_path, local_source_table("work", folder))
    run_cycle(config, mode=None)
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`",
        "WAITING ON YOU: 1 online-only file(s) in work could not be downloaded (macOS refused): in Finder, "
        f"choose Download Now (or Always Keep on This Device) on their folder, then run `{BIN} sync`",
    ]
    monkeypatch.setattr(arm_local, "materialise", real_materialise)
    assert run_cycle(config, mode=None).exit_code == 0
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`"
    ]


def test_a_folder_a_sync_cannot_list_is_a_wait_and_reaches_rule_4(tmp_path: Path) -> None:
    """An empty folder in a OneDrive tree leaves every local walk incomplete, and no sync clears it: the
    operator's wait, never rule 3 (it would loop). The wait names the table as sources.toml writes it
    (``id = "work"``, so a search for the printed text finds it) and the file by its path, with ``~`` under
    the home folder (v10 rehearsal, 2026-10-08: it said ``id = 'work' in sources.toml``)."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Empty").mkdir(parents=True)
    _write(root / NOTE_NAME, "The purchase order is approved.\n")
    config = _setup(Path.home() / "agent-context", local_source_table("work", root))
    assert config.config_path == Path.home() / "agent-context" / "ctx" / "sources.toml"
    assert 'id = "work"\n' in config.config_path.read_text(encoding="utf-8")
    for _ in range(2):
        run_cycle(config, mode=None)
        assert _lines(config) == [
            f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`",
            "WAITING ON YOU: 1 empty cloud folder(s) keep the listing of work incomplete: an empty folder in "
            "a cloud folder is expected and loses nothing, but while the listing is incomplete, deletions in "
            "work are held, not applied, and another sync does not complete it; if the folder(s) are "
            "meant to be empty, "
            f"run `{BIN} exclude-empty work` (it adds them to that source's exclude in "
            "~/agent-context/ctx/sources.toml; an excluded folder is not mirrored if it later gains files)",
        ]
    with Manifest(config.state_paths.db) as manifest:
        assert not manifest.get_source("work").enumeration_complete  # type: ignore[union-attr]
        assert manifest.get_meta("empty_cloud_dirs:work") == '["Empty"]', "what the sync's walk found"
    state = (config.docs_repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert _lines(config)[1] in state.splitlines(), "STATE.md and status give the same wait"
    # The wait comes from that walk: status lists no folder of the source, here not even a missing one.
    shutil.move(root, root.with_name("Work moved"))
    assert f"run `{BIN} exclude-empty work`" in _lines(config)[1]
    shutil.move(root.with_name("Work moved"), root)
    # B13 round 4 Q71: the command the line names writes the exclude (the default globs kept). The wait is
    # gone and the step is to sync; that pass is complete.
    path = config.config_path
    assert cli.main(["exclude-empty", "work", "--config", str(path)]) == cli.EXIT_OK
    assert 'exclude = ["~$*", "*.tmp", ".~lock.*#", "/Empty/"]\n' in path.read_text(encoding="utf-8")
    config = load_config(path)
    assert _lines(config) == [
        f"NEXT: 1 source(s) not fully listed or converted yet (work): run `{BIN} sync` again"
    ]
    run_cycle(config, mode=None)
    assert not any(ln.startswith("WAITING ON YOU") for ln in _lines(config))
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.get_source("work").enumeration_complete  # type: ignore[union-attr]
        assert manifest.get_meta("empty_cloud_dirs:work") == ""


def test_status_points_the_heartbeat_at_a_wait_only_while_the_loop_has_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R151: status reads the loop before its checks. While the empty cloud folder's wait is there, the
    incomplete listing's check points at it (R38: no command, since status prints it); once the folder is
    excluded and no sync has run, the loop's step is to sync again and so is the check's, with no note."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Empty").mkdir(parents=True)
    _write(root / NOTE_NAME, "The purchase order is approved.\n")
    config = _setup(tmp_path, local_source_table("work", root))
    for _ in range(3):
        run_cycle(config, mode=None)

    def heartbeat() -> CheckResult:
        (r,) = [c for c in cli._build_status(config).checks if c.name == "heartbeat.work"]
        assert not r.ok and r.detail.startswith("enumeration incomplete for 3"), r
        return r

    assert heartbeat().fix == (
        "each WAITING ON YOU line about work says what stops the listing and what to do: another sync "
        "does not clear it"
    )
    path = config.config_path
    path.write_text(path.read_text(encoding="utf-8") + 'exclude = ["/Empty/"]\n', encoding="utf-8")
    config = load_config(path)
    assert heartbeat().fix == "~/.local/bin/agentsync sync; another full walk lists it"
    _assert_fix_parses(heartbeat().fix)
    monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, "1")
    r = heartbeat()
    assert r.note is None and r.fix == "~/.local/bin/agentsync sync; another full walk lists it"


def test_status_next_is_the_loops_with_and_without_a_fail(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R151 review: status reads the loop before its checks, without rule 9's count of the queue. Its NEXT
    is still the loop's, byte for byte: rule 9's count with no FAIL, and rule 1's step with one, which reads
    no queue at all (as before); the queue is counted no more often than before."""
    config = _synced(tmp_path, folder)
    _hand_written(config, 1)
    counted: list[int] = []
    real = loop.queue_rows

    def queue_rows(cfg: Config) -> int:
        counted.append(1)
        return real(cfg)

    monkeypatch.setattr(loop, "queue_rows", queue_rows)
    fail = CheckResult("fake.check", False, "broken", Severity.ERROR, fix="do the thing")
    for checks in ([], [fail]):
        monkeypatch.setattr(cli, "_status_checks", lambda config, **kw: list(checks))  # noqa: B023
        counted.clear()
        status = cli._build_status(config)
        reads = len(counted)
        want = loop.next_lines(config, fixes=[cli._fail_step(c) for c in checks])
        assert list(status.loop) == want and want[0].startswith("NEXT: "), want
        # The loop line counts the queue once ("to curate N"); rule 9 once more, and rule 1 not at all.
        if checks:
            assert reads == 1 and want[0].startswith("NEXT: the fake.check check failed"), reads
        else:
            assert reads == 2 and want[0].startswith("NEXT: 1 curation row(s) queued"), reads


def test_the_exclude_wait_counts_the_mirrored_files_the_walk_did_not_list(tmp_path: Path) -> None:
    """N26: while a cloud folder keeps the listing incomplete, files deleted upstream stay mirrored, and the
    wait counts them (never a path), so the person knows what completing the listing checks. The paste
    retires none of them: the first complete pass marks them absent, the next removes them as deleted."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Empty").mkdir(parents=True)
    for n in range(6):
        _write(root / "Planning" / f"plan {n}.txt", f"Plan {n} is approved.\n")
    config = _setup(tmp_path, local_source_table("work", root))
    run_cycle(config, mode=None)
    first = next(ln for ln in _lines(config) if ln.startswith("WAITING ON YOU: 1 empty cloud folder(s)"))
    assert first.endswith("an excluded folder is not mirrored if it later gains files)"), "none missed yet"
    for n in range(4):
        (root / "Planning" / f"plan {n}.txt").unlink()
    run_cycle(config, mode=None)
    wait = next(ln for ln in _lines(config) if ln.startswith("WAITING ON YOU: 1 empty cloud folder(s)"))
    assert wait == first + (
        "; the last walk of work did not list 4 file(s) the mirror still has: once the listing is complete, "
        "later syncs take out the pages of those still absent, with the usual deletion check"
    )
    assert "plan" not in wait.lower() and "Planning" not in wait
    # A row last listed under an earlier `path` is retired as a scope change, not checked: not counted.
    with Manifest(config.state_paths.db) as manifest:
        kept = manifest.get_meta("scope_root:work")
        manifest.set_meta("scope_root:work", f"99:{root}")
    assert not any("did not list" in ln for ln in _lines(config))
    with Manifest(config.state_paths.db) as manifest:
        manifest.set_meta("scope_root:work", kept or "")
    path = config.config_path
    path.write_text(path.read_text(encoding="utf-8") + 'exclude = ["~$*", "*.tmp", ".~lock.*#", "/Empty/"]\n')
    config = load_config(path)
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.get_source("work").enumeration_complete  # type: ignore[union-attr]
        assert manifest.live_count("work") == 6, "the paste retired none of them"
        assert manifest.unlisted_count("work") == 4
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.live_count("work") == 2, "absent from two complete passes: deletion notices now"
        assert manifest.unlisted_count("work") == 0


def test_a_pass_that_failed_after_its_walk_counts_no_file_as_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N26: a pass that fails after its walk and before its transaction commits records a FULL run_sources
    row with its error and stamps no row. Counted against it, every mirrored file reads as not listed."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Empty").mkdir(parents=True)
    for n in range(6):
        _write(root / "Planning" / f"plan {n}.txt", f"Plan {n} is approved.\n")
    config = _setup(tmp_path, local_source_table("work", root))
    run_cycle(config, mode=None)
    first = next(ln for ln in _lines(config) if ln.startswith("WAITING ON YOU: 1 empty cloud folder(s)"))

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("the classifier failed")

    monkeypatch.setattr(cycle_module, "_classify_pass", fail)
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.last_source_pass("work").error  # type: ignore[union-attr]
        assert manifest.unlisted_count("work") == 0
    assert first in _lines(config), "the wait stands, with no count"


def test_an_empty_cloud_folder_that_held_mirrored_files_gets_no_exclude_line(tmp_path: Path) -> None:
    """A folder usually becomes empty because its files were removed upstream, and while the listing is
    incomplete those deletions are held. Excluding the folder would retire their pages as a scope change:
    no deletion breaker, no purge queued. So the line to paste names only folders with nothing mirrored
    below them, and the other folders get the count and the consequence instead."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Empty").mkdir(parents=True)
    _write(root / NOTE_NAME, "The purchase order is approved.\n")
    for name in ("first.txt", "second.txt"):
        _write(root / "Reports" / "2026" / name, "The quarter closed on time.\n")
    config = _setup(tmp_path, local_source_table("work", root))
    run_cycle(config, mode=None)
    shutil.rmtree(root / "Reports" / "2026")
    (root / "Reports" / "2026").mkdir()
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.live_count("work") == 3, "the listing is incomplete: both deletions are held"
    waits = [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")]
    assert waits == [
        "WAITING ON YOU: 1 empty cloud folder(s) keep the listing of work incomplete: an empty folder in "
        "a cloud folder is expected and loses nothing, but while the listing is incomplete, deletions in "
        "work are held, not applied, and another sync does not complete it; if the folder(s) are "
        "meant to be empty, "
        f"run `{BIN} exclude-empty work` (it adds them to that source's exclude in {config.config_path}; an "
        "excluded folder is not mirrored if it later gains files)",
        "WAITING ON YOU: 1 empty cloud folder(s) in work held 2 file(s) the mirror still has (the listing "
        "stays incomplete, so their deletion is held; another sync does not clear it): if the files were "
        "removed on purpose, remove the empty folder(s) from the cloud drive too, and later syncs take the "
        "pages out with the usual deletion check; excluding such a folder instead retires its pages at "
        f"once, with no deletion check and no purge queued. `{BIN} sync -v` names the folders; in all, the "
        "last walk of work did not list 2 file(s) the mirror still has: once the listing is complete, later "
        "syncs take out the pages of those still absent, with the usual deletion check",
    ]
    assert not any("Reports" in ln or "2026" in ln for ln in _lines(config))
    # Removed upstream too, the folder no longer stops the listing once the other one is excluded, and the
    # deletions take the usual path: absent from two complete passes, then gone.
    shutil.rmtree(root / "Reports")
    (root / "Empty").rmdir()
    for _ in range(3):
        run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.get_source("work").enumeration_complete  # type: ignore[union-attr]
        assert manifest.live_count("work") == 1
    assert [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")] == [
        f"WAITING ON YOU: 2 queued purge(s): preview with `{BIN} purge --queue --dry-run` (writes nothing) "
        f"before you run `{BIN} purge --queue` (rewrites history)"
    ], "deleted upstream, not retired: each deletion queued its purge"


def test_an_empty_cloud_folder_in_an_inbox_gets_the_waits_of_a_local_source(tmp_path: Path) -> None:
    """b13 N27 (b): an empty cloud folder keeps an inbox's listing incomplete on every pass, as in a local
    source, so it gets the same WAITING ON YOU lines (the exclude line only for a folder with nothing
    mirrored below it), never the inbox note's "a later sync lists it"; once the folders are gone the
    held deletion takes the usual path."""
    box = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Drop"
    (box / "Empty").mkdir(parents=True)
    for rel in ("keep.txt", "Kept/old.txt"):
        _write(box / rel, "The purchase order is approved.\n")
    config = _setup(tmp_path, inbox_source_table("drop", box) + "quiescence_s = 0\n")
    run_cycle(config, mode=None)
    (box / "Kept" / "old.txt").unlink()
    for _ in range(2):
        run_cycle(config, mode=None)
        with Manifest(config.state_paths.db) as manifest:
            assert manifest.live_count("drop") == 2, "the listing is incomplete: the deletion is held"
        lines = _lines(config)
        assert [ln for ln in lines if ln.startswith("WAITING ON YOU")] == [
            "WAITING ON YOU: 1 empty cloud folder(s) keep the listing of drop incomplete: an empty folder in "
            "a cloud folder is expected and loses nothing, but while the listing is incomplete, deletions in "
            "drop are held, not applied, and another sync does not complete it; if the folder(s) are "
            "meant to be empty, "
            f"run `{BIN} exclude-empty drop` (it adds them to that source's exclude in {config.config_path}; "
            "an excluded folder is not mirrored if it later gains files)",
            "WAITING ON YOU: 1 empty cloud folder(s) in drop held 1 file(s) the mirror still has (the "
            "listing stays incomplete, so their deletion is held; another sync does not clear it): if the "
            "files were removed on purpose, remove the empty folder(s) from the cloud drive too, and later "
            "syncs take the pages out with the usual deletion check; excluding such a folder instead retires "
            "its pages at once, with no deletion check and no purge queued. "
            f"`{BIN} sync -v` names the folders; in all, the last walk of drop did not list 1 file(s) the "
            "mirror still has: once the listing is complete, later syncs take out the pages of those still "
            "absent, with the usual deletion check",
        ]
        assert not any(ln.startswith("note: ") for ln in lines)
    (box / "Empty").rmdir()
    (box / "Kept").rmdir()
    for _ in range(3):
        run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.get_source("drop").enumeration_complete  # type: ignore[union-attr]
        assert manifest.live_count("drop") == 1
    assert [ln for ln in _lines(config) if ln.startswith(("WAITING ON YOU", "note: "))] == [
        f"WAITING ON YOU: 1 queued purge(s): preview with `{BIN} purge --queue --dry-run` (writes nothing) "
        f"before you run `{BIN} purge --queue` (rewrites history)"
    ], "deleted, not retired: the deletion queued its purge"


def test_doctor_points_an_inbox_held_by_an_empty_cloud_folder_at_the_loops_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop said another sync does not clear an inbox's empty cloud folder while doctor's heartbeat fix
    said a full pass clears it. Doctor now asks the loop's own predicate: that inbox gets the local folder's
    status pointer (a note under install.sh), and an inbox left incomplete by anything else keeps sync -v
    (B13 round 2 R53)."""
    from agentsync.ops import doctor  # noqa: PLC0415 - only this test needs it

    cloud = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    held, other = cloud / "Drop", cloud / "Bare"
    (held / "Empty").mkdir(parents=True)
    _write(held / "keep.txt", "The purchase order is approved.\n")
    other.mkdir()
    config = _setup(
        tmp_path, inbox_source_table("drop", held) + "quiescence_s = 0\n" + inbox_source_table("bare", other)
    )
    for _ in range(4):
        run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        by_id = {s.id: s for s in config.sources}
        assert loop.empty_cloud_folders_held(manifest, by_id["drop"])
        assert not loop.empty_cloud_folders_held(manifest, by_id["bare"])
    assert "heartbeat.drop" in loop.next_step(config, count_queue=False).checks
    checks = {c.name: c for c in doctor._check_heartbeat(config)}
    assert checks["heartbeat.drop"].fix == (
        "each WAITING ON YOU line about drop says what stops the listing and what to do: another sync does "
        "not clear it"
    )
    assert (
        checks["heartbeat.bare"].fix
        == "~/.local/bin/agentsync sync -v (a full pass that lists all of bare clears this)"
    )
    monkeypatch.setenv(loop.NO_NEXT_HINT_ENV, "1")
    checks = {c.name: c for c in doctor._check_heartbeat(config)}
    assert (checks["heartbeat.drop"].fix, checks["heartbeat.drop"].note) == (
        None,
        doctor._LISTING_YOURS_NOTE,
    )
    assert checks["heartbeat.bare"].fix is not None and checks["heartbeat.bare"].note is None


def test_an_inbox_file_the_walk_withheld_is_not_in_the_count(tmp_path: Path) -> None:
    """b13 R101 (was §16.39 N26's Known limit): an inbox walk leaves a file that changed too recently out
    of its listing, but the file is there: the pass stamps its row seen, so it never counts as "not listed"
    beside an empty cloud folder, and is never a deletion candidate."""
    box = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Drop"
    (box / "Empty").mkdir(parents=True)
    _write(box / "keep.txt", "The purchase order is approved.\n")
    config = _setup(tmp_path, inbox_source_table("drop", box) + "quiescence_s = 0\n")
    run_cycle(config, mode=None)
    paste = (
        "WAITING ON YOU: 1 empty cloud folder(s) keep the listing of drop incomplete: an empty folder in "
        "a cloud folder is expected and loses nothing, but while the listing is incomplete, deletions in "
        "drop are held, not applied, and another sync does not complete it; if the folder(s) are "
        "meant to be empty, "
        f"run `{BIN} exclude-empty drop` (it adds them to that source's exclude in {config.config_path}; an "
        "excluded folder is not mirrored if it later gains files)"
    )
    assert [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")] == [paste]
    ahead = time.time() + 3600  # dated ahead of the clock: withheld by every walk, and never waited for
    os.utime(box / "keep.txt", (ahead, ahead))
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.unlisted_count("drop") == 0
        assert manifest.unseen_live("drop", manifest.last_runs(1)[0][0]) == []
        assert manifest.live_count("drop") == 1, "withheld, not deleted"
    assert [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")] == [paste]


def test_withheld_inbox_files_leave_a_real_deletion_in_the_count(tmp_path: Path) -> None:
    """b13 R101: held-back files (new ones with no row, and a mirrored one) take nothing off the count of
    mirrored files the walk did not list: a file deleted upstream still counts, alone."""
    box = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Drop"
    (box / "Empty").mkdir(parents=True)
    for name in ("keep.txt", "gone.txt"):
        _write(box / name, f"The purchase order for {name} is approved.\n")
    config = _setup(tmp_path, inbox_source_table("drop", box) + "quiescence_s = 0\n")
    run_cycle(config, mode=None)
    (box / "gone.txt").unlink()
    ahead = time.time() + 3600
    for name in ("keep.txt", "new 1.txt", "new 2.txt", "new 3.txt"):
        _write(box / name, "Still being written.\n")
        os.utime(box / name, (ahead, ahead))
    run_cycle(config, mode=None)
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.unlisted_count("drop") == 1
        assert manifest.live_count("drop") == 2, "the listing is incomplete: the deletion is held"
    (wait,) = [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")]
    assert wait.endswith(
        "; the last walk of drop did not list 1 file(s) the mirror still has: once the listing is complete, "
        "later syncs take out the pages of those still absent, with the usual deletion check"
    )


def test_an_inbox_listing_left_incomplete_by_no_empty_folder_promises_nothing(tmp_path: Path) -> None:
    """b13 N27 (b): an empty inbox root in a cloud tree is unknown on every pass too (and so is a folder
    it cannot read): the general note says which cause a later sync clears, and what names the cause."""
    box = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Drop"
    box.mkdir(parents=True)
    config = _setup(tmp_path, inbox_source_table("drop", box))
    run_cycle(config, mode=None)
    notes = [ln for ln in _lines(config) if ln.startswith("note: ")]
    assert notes == [
        "note: the inbox (drop) was not fully listed: a later sync lists a file that was still being "
        "written, but not a folder it cannot read or an empty cloud folder "
        f"(`{BIN} sync -v` names the cause); it does not block the next step"
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="off macOS the attribute read fails: a domain")
@pytest.mark.usefixtures("real_domain_check")
def test_an_empty_folder_in_a_plain_provider_folder_gets_no_wait(tmp_path: Path) -> None:
    """b13 R1: a rehearsal's sandbox makes its provider folder with mkdir, not a File Provider domain, so an
    empty subfolder there is just empty: the listing completes and no wait asks for an exclude."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Project Alpha"
    (root / "Empty Subfolder").mkdir(parents=True)
    _write(root / NOTE_NAME, "The purchase order is approved.\n")
    config = _setup(tmp_path, local_source_table("work", root))
    run_cycle(config, mode=None)
    assert not any(ln.startswith(("WAITING ON YOU", "note: ")) for ln in _lines(config))
    with Manifest(config.state_paths.db) as manifest:
        assert manifest.get_source("work").enumeration_complete  # type: ignore[union-attr]
        assert manifest.get_meta("empty_cloud_dirs:work") in (None, "")


def test_a_folder_a_sync_cannot_list_for_another_reason_points_at_sync_v(tmp_path: Path) -> None:
    """A missing sentinel also leaves every local walk incomplete, with no empty folder to name: the wait
    says what names the cause and offers no exclude line."""
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    _write(root / NOTE_NAME, "The purchase order is approved.\n")
    config = _setup(tmp_path, local_source_table("work", root) + 'sentinel = "KEEP.txt"\n')
    run_cycle(config, mode=None)
    (wait,) = [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")]
    assert wait == (
        "WAITING ON YOU: a folder in work could not be listed (no access, or a missing folder or sentinel; "
        f"another sync does not clear it): `{BIN} sync -v` names it; grant Files and Folders access, or add "
        "it to that source's exclude in sources.toml"
    )
    # §16.39 N16: the wait explains that source's heartbeat warn
    assert loop.next_step(config, count_queue=False).checks == ("heartbeat.work",)


def test_a_folder_a_sync_cannot_list_after_it_mirrored_files_warns_what_an_exclude_retires(
    tmp_path: Path, folder: Path
) -> None:
    """b13 R102: once a folder holding mirrored files cannot be listed, the wait counts them and says that
    adding the folder to the exclude retires their pages at once, as the gone line does; it no longer offers
    the exclude beside the access grant as an equal choice."""
    for n in range(30):
        _write(folder / "Locked" / f"memo {n:02d}.txt", f"Memo {n} for Project Alpha.\n")
    config = _synced(tmp_path, folder)
    (folder / "Locked").chmod(0)
    try:
        run_cycle(config, mode=None)
        waits = [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")]
    finally:
        (folder / "Locked").chmod(0o755)
    assert waits == [
        "WAITING ON YOU: a folder in work could not be listed (no access, or a missing folder or sentinel; "
        f"another sync does not clear it): `{BIN} sync -v` names it; grant Files and Folders access (the "
        "safe fix); the last walk of work did not list 30 file(s) the mirror still has, and adding the "
        "folder to that source's exclude retires the pages under it at once, with no deletion check and no "
        "purge queued"
    ]
    assert loop.next_step(config, count_queue=False).checks == ("heartbeat.work",)


def test_a_source_root_a_sync_cannot_list_never_offers_an_exclude(tmp_path: Path, folder: Path) -> None:
    """b13 R102/R103 review: a root that cannot be listed, is missing or is empty cannot be excluded, so its
    wait offers no exclude, also when mirrored files went unlisted; it says what to do for the root itself."""
    config = _synced(tmp_path, folder)
    root_wait = (
        "WAITING ON YOU: the folder of work itself could not be listed or is empty (an unmounted volume, a "
        "sync client that has not filled it yet, or no access; another sync does not clear it): mount it or "
        "let the sync client fill it, or grant Files and Folders access; to stop syncing it on purpose, set "
        f'state = "paused" in [[source]] id = "work" in {config.config_path}'
    )
    folder.chmod(0)
    try:
        run_cycle(config, mode=None)
        waits = [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")]
    finally:
        folder.chmod(0o755)
    assert waits == [root_wait]
    assert loop.next_step(config, count_queue=False).checks == ("heartbeat.work",)
    folder.rename(folder.with_name("Work unmounted"))
    run_cycle(config, mode=None)
    assert [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU")] == [root_wait]
    folder.with_name("Work unmounted").rename(folder)
    run_cycle(config, mode=None)
    assert not any(ln.startswith("WAITING ON YOU") for ln in _lines(config))


def test_a_listing_macos_holds_is_a_click_allow_wait_for_every_source_under_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field N8 review: a walk that times out on a privacy prompt says click Allow, never "exclude it" (once
    the prompt is answered, a narrowed scope would retire the folder's pages past the deletion breaker). A
    second source under the same cloud folder is not read that pass: it would wait on the same prompt."""
    cloud = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso"
    first, second = cloud / "Alpha", cloud / "Beta"
    for root in (first, second):
        _write(root / NOTE_NAME, "The purchase order is approved.\n")
    config = _setup(tmp_path, local_source_table("alpha", first) + local_source_table("beta", second))
    real = arm_local._list_dir
    release = threading.Event()
    listed: list[Path] = []

    def held(path: Path, *, dir_dataless: bool) -> list[os.DirEntry[str]]:
        listed.append(path)
        if path == first:
            release.wait(5.0)
        return real(path, dir_dataless=dir_dataless)

    monkeypatch.setattr(arm_local, "_list_dir", held)
    monkeypatch.setattr(arm_local, "LISTING_TIMEOUT_S", 0.2, raising=False)
    try:
        run_cycle(config, mode=None)
    finally:
        release.set()
    assert not any(p == second or second in p.parents for p in listed), listed
    lines = _lines(config)
    assert (
        "WAITING ON YOU: macOS held the listing of alpha, beta for a privacy prompt: click Allow on the "
        f"macOS prompt (it can sit behind other windows), then run `{BIN} sync`"
    ) in lines, lines
    assert not any("exclude it" in line for line in lines), lines
    # §16.39 N16: the wait explains the heartbeat warn of every source it names
    assert loop.next_step(config, count_queue=False).checks == ("heartbeat.alpha", "heartbeat.beta")
    monkeypatch.setattr(arm_local, "_list_dir", real)
    run_cycle(config, mode=None)
    assert not any("privacy prompt" in line for line in _lines(config))


def test_a_graph_source_the_network_refuses_is_an_it_wait(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    text = config.config_path.read_text(encoding="utf-8")
    text += '\n[graph]\nclient_id = "00000000-0000-0000-0000-000000000001"\n'
    text += '\n[[source]]\nid = "drive"\nkind = "graph_drive"\ndrive_id = "me"\n'
    config.config_path.write_text(text, encoding="utf-8")
    config = load_config(config.config_path)
    with Manifest(config.state_paths.db) as manifest:
        manifest.sync_sources(config.sources)
        manifest.set_auth_state("ok", ["drive"])
        run_id = manifest.begin_run(CycleMode.POLL, host="test", pid=1)
        manifest.record_source_pass(
            run_id, "drive", pass_kind=None, enumeration_complete=False, cursor_reset=False, counts={},
            skipped_reason=f"{NETWORK_POLICY_FAILED}network-policy (TLS inspection)",
        )  # fmt: skip
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`",
        "WAITING ON YOU: the network refuses Microsoft Graph for drive (proxy, TLS inspection or PAC): IT "
        f"must allow it; `{BIN} it-request --out ~/agent-context/it-request-draft.md` drafts the request",
    ]


# ---- rules 4-6: the baseline before the first curated page ------------------------------------------------


def test_rule_4_draft_the_baseline_questions(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`"
    ]


def test_rule_5_the_operator_confirms_a_draft(tmp_path: Path, folder: Path) -> None:
    """One synced repo for the three states: ``_eval`` rewrites both files and ``next_step`` only reads.
    The wait says where the two files are (v9 rehearsal, 2026-10-07: it named ``_eval/questions.md`` and
    left whoever confirms them to work out the folder): the docs repo's ``_eval`` folder. Setup puts the
    docs repo under the home folder, and there it is written with ``~``, as every command in these lines
    is, so the line holds no user name."""
    config = _synced(tmp_path, folder)
    assert config.docs_repo == Path.home() / "agent-context" / "docs"
    for questions, answers in (("draft", "draft"), ("confirmed", "draft"), ("", "")):
        _eval(config, questions, answers)
        assert _lines(config) == [
            "NEXT: stop: the operator confirms the baseline questions (see the WAITING ON YOU lines); "
            "session done",
            DRAFT_WAIT.format("~/agent-context/docs/_eval"),
        ], (questions, answers)


def test_the_draft_wait_names_a_docs_repo_outside_the_home_folder_by_its_full_path(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``docs_repo`` may be set to a folder outside the home folder: the wait then names it as it is. The
    file names in the line are the tool's own two, never a document's."""
    config = _synced(tmp_path, folder)
    _eval(config, "draft", "draft")
    monkeypatch.setenv("HOME", str(tmp_path / "another-home"))  # the docs repo is not under this one
    [wait] = [ln for ln in _lines(config) if ln.startswith("WAITING ON YOU: the baseline questions")]
    assert wait == DRAFT_WAIT.format(config.docs_repo / "_eval")
    assert "~" not in wait and "_eval/" not in wait


def test_rule_5_holds_curation_even_with_pages(tmp_path: Path, folder: Path) -> None:
    """The hard gate (plan, "Decision recorded"): a draft holds the curation work list."""
    config = _synced(tmp_path, folder)
    _hand_written(config, 3)
    _eval(config, "draft", "draft")
    assert loop.next_step(config).rule == 5


def test_rule_6_run_the_before_baseline(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    _eval(config, "confirmed", "Confirmed")
    assert _lines(config) == [
        "NEXT: run the 'before' baseline in a session that did not draft the questions (start a new one if "
        f"this one did): follow step 2 (Run) of {BASELINE}, then run `{BIN} sync`"
    ]


# ---- rules 7-10: curation ---------------------------------------------------------------------------------


def test_rule_7_checkpoint_blockers_uncommitted_then_held(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    _write(
        config.docs_repo / "topics" / "area" / "orders.md",
        "---\nentity: orders\npurpose: who approves orders\nsources: []\n---\nbody\n",  # UNLISTED
    )
    want = [
        f"NEXT: 1 curation error(s) hold the checkpoint: run `{BIN} curate`, fix every ERROR it lists, then "
        f"run `{BIN} sync`"
    ]
    assert _lines(config) == want, "the session's uncommitted page"
    report = run_cycle(config, mode=None)
    assert report.checkpoint == "held"
    assert _lines(config) == want, "committed by the sync, the held checkpoint still names it"


def test_rule_8_run_the_after_baseline(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    _eval(config, "confirmed", "confirmed", "before")
    _hand_written(config, 20)
    assert _lines(config) == [
        "NEXT: 20 curated pages exist: run the 'after' baseline in a session that did not write them (start "
        f"a new one if this one did): follow step 2 (Run) of {BASELINE}, then run `{BIN} sync`"
    ]
    _eval(config, "confirmed", "confirmed", "before", "after")
    assert loop.next_step(config).rule == 9


def test_rule_9_the_queue_is_bounded_per_session(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    _hand_written(config, 1)  # a page exists, no _eval: the baseline rules no longer apply
    assert _lines(config) == [
        f"NEXT: 1 curation row(s) queued: run `{BIN} curate`, curate up to 10 of them, then run "
        f"`{BIN} sync`; session done"
    ]


def test_rule_9_uncounted_sends_the_agent_to_curate_without_walking_the_mirror(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STATE.md is written by every cycle, LaunchAgent polls included: it never pays rule 9's mirror walk."""
    config = _synced(tmp_path, folder)
    _hand_written(config, 1)
    want = (
        f"NEXT: run `{BIN} curate` and follow its NEXT line (it counts the curation queue; curate up to 10 "
        f"rows, then run `{BIN} sync`; session done)"
    )

    def no_walk(*args: object) -> list[str]:
        raise AssertionError("the mirror walk ran")

    monkeypatch.setattr(loop.curate, "uncovered_mirror_pages", no_walk)
    step = loop.next_step(config, count_queue=False)
    assert (step.rule, step.lines()) == (9, [want])
    run_cycle(config, mode=CycleMode.POLL)
    state = (config.docs_repo / "_sync" / "STATE.md").read_text(encoding="utf-8")
    assert f"## Next\n\n{want}\n\n## This run" in state


def test_rule_10_nothing_to_do(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    (page,) = (config.docs_repo / "mirror" / "work").glob("*.md")
    mirror = config.layout.rel(page)
    head = page.read_text(encoding="utf-8")
    m = re.search(r"^rendered_sha256: ([0-9a-f]{64})$", head, re.MULTILINE)
    assert m, head
    _write(
        config.docs_repo / "topics" / "area" / "orders.md",
        f"---\nentity: orders\npurpose: who approves orders\nsources:\n  - {{path: {mirror}, "
        f"at_rendered_sha256: {m.group(1)}, role: primary}}\n---\nFinance approves them.\n",
    )
    assert run_cycle(config, mode=None).checkpoint == "advanced"
    assert _lines(config) == ["NEXT: nothing to do: session done"]


# ---- status's one loop line (KISS K08a) --------------------------------------------------------------------


def test_status_line_tracks_skill_baseline_topics_and_queue(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    line = loop.status_line(config)
    assert line == (
        "loop: skill current · inbox off · baseline missing · topics 0 · checkpoint never · to curate 1 · "
        "archive off"
    )
    (path,) = skill.skill_paths()
    path.write_text("an older skill\n", encoding="utf-8")
    assert loop.skill_state(config.docs_repo) == "stale"
    path.unlink()
    assert loop.skill_state(config.docs_repo) == "missing"
    states = []
    for questions, answers, results in (
        ("draft", "draft", ()),
        ("confirmed", "confirmed", ()),
        ("confirmed", "confirmed", ("before",)),
        ("confirmed", "confirmed", ("before", "after")),
    ):
        _eval(config, questions, answers, *results)
        states.append(loop.baseline_state(config.docs_repo))
    assert states == ["draft", "confirmed", "before", "after"]
    _hand_written(config, 2)
    assert " topics 2 " in loop.status_line(config) and "skill missing" in loop.status_line(config)


def test_status_line_inbox_is_the_kept_inbox_not_every_inbox_source(tmp_path: Path, folder: Path) -> None:
    """2026-10-08 D2.a: the guides name one inbox, the folder ``ensure_inbox`` keeps (publish's root_guide),
    so status's ``inbox`` part is about that folder: a hand-added inbox source whose folder is gone is
    doctor's FAIL, not "inbox missing" while the kept inbox is there. With no live source on the kept folder
    the part covers every live inbox source, as the guide then lists them all."""
    gone = tmp_path / "Contoso" / "old drop"
    config = _synced(tmp_path, folder, inbox_source_table("old-drop", gone))
    assert " · inbox missing · " in loop.status_line(config)  # no source on the kept folder yet
    config, kept = ensure_inbox(config.config_path)
    assert kept is not None and kept.path is not None
    assert " · inbox on · " in loop.status_line(config)
    gone.mkdir(parents=True)
    kept.path.rmdir()
    assert " · inbox missing · " in loop.status_line(config)
    cfg = config.config_path
    head = f'id = "{kept.id}"\n'
    paused = cfg.read_text(encoding="utf-8").replace(head, head + 'state = "paused"\n')
    cfg.write_text(paused, encoding="utf-8")
    assert " · inbox on · " in loop.status_line(load_config(cfg))  # paused: the live one is the inbox
    shutil.rmtree(gone)
    assert " · inbox missing · " in loop.status_line(load_config(cfg))


# ---- the one-time re-read: a note that says "sync again" while a sync reads more --------------------------

REREAD_TAIL = (
    "still to be read again, once, for what this build's converters have gained (each sync reads about 2 "
    "minutes' worth); it does not block the next step"
)
STUCK_TAIL = (
    "wait to be read again, and the last sync read none of them (a converter or on-device OCR that cannot "
    "run, or a folder that could not be listed): another sync does not clear it; it does not block the next "
    "step"
)
CROWDED_TAIL = (
    "wait to be read again, and the last sync read none of them: new and changed files took its OCR time, "
    "more files joined, and more downloads wait. They are read once a sync has OCR time left; it does not "
    "block the next step"
)


def _reread(
    config: Config, *, done: bool, run: dict[str, int] | None, reading: str | None = None, sid: str = "work"
) -> list[str]:
    """Store a re-read record for ``sid`` as a cycle does, and a run that says what it looked for with
    the counts ``run`` (None: no later run); return the loop's note lines."""
    record: dict[str, object] = {"done": done, "for": "ab" * 32, "tried": []}
    if reading is not None:
        record["reading"] = reading
    with Manifest(config.state_paths.db) as manifest:
        manifest.set_meta(f"reread:{sid}", json.dumps([record]))
        if run is not None:
            run_id = manifest.begin_run(CycleMode.POLL, host="mac", pid=1)
            manifest.finish_run(run_id, status="ok", commit_sha=None, counts={"reread_for": 7, **run})
    step = loop.next_step(config)
    assert step.rule == 4, "a re-read that is not finished is a note: it never holds a rule"
    return [line for line in _lines(config) if line.startswith("note: ")]


def _pass_without_a_look(config: Config, sid: str, *, held: bool) -> list[str]:
    """Record a sync that did not get to the files of ``sid``, as a cycle does: macOS held its listing
    (``held``), or the source failed.  Such a run says nothing about a re-read; return the note lines."""
    with Manifest(config.state_paths.db) as manifest:
        run_id = manifest.begin_run(CycleMode.POLL, host="mac", pid=1)
        manifest.record_source_pass(
            run_id,
            sid,
            pass_kind=PassKind.FULL,
            enumeration_complete=False,
            cursor_reset=False,
            counts={},
            skipped_reason=LISTING_HELD + "click Allow on the macOS prompt" if held else None,
            error=None if held else "OSError: the folder is gone",
        )
        manifest.finish_run(
            run_id, status="ok" if held else "partial", commit_sha=None, counts={"converted": 0}
        )
    return [line for line in _lines(config) if line.startswith("note: ")]


def test_an_unfinished_re_read_is_a_sync_again_note_with_the_count_the_last_sync_left(
    tmp_path: Path, folder: Path
) -> None:
    """Files converted before something this build's converters have are read again over several syncs
    (two minutes' worth each).  While one is left and the last sync read some, the note starts "sync
    again:" and gives the count that sync left.  The setup prompt runs sync again while it does."""
    config = _synced(tmp_path, folder)
    assert not [line for line in _lines(config) if line.startswith("note: ")], "nothing to read again"
    said = f"note: {loop.SYNC_AGAIN}37 file(s) in work are {REREAD_TAIL}"
    assert _reread(config, done=False, run={"reread": 12, "reread_left": 37}) == [said]
    assert said.startswith("note: sync again: 37 file(s) in work are still to be read again")
    # The cycle's OCR time ran out before a file was read again: the next sync has it for them.
    assert _reread(config, done=False, run={"ocr_over": 1, "reread_left": 37}) == [said]
    assert _reread(config, done=True, run={"reread": 37}) == [], "finished: no note"


def test_a_re_read_that_downloads_keep_out_of_the_ocr_time_does_not_say_sync_again(
    tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each sync downloads up to its budget, and new files have the OCR time first.  While they use all
    of it, no file is read again, and the ones converted past it join the count.  "Used up its OCR time"
    is then no progress: the count rises, and the next sync downloads more.  The note says so, and an
    agent that syncs while the note says "sync again" stops, instead of downloading for twelve syncs that
    read nothing.  A sync that read a file, or one that left no more than the sync before, says "sync
    again" as before; so does a count that rose with nothing left to download, which the next sync reads."""
    (folder / "small.txt").write_text("small\n", encoding="utf-8")
    online = {(folder / "small.txt").stat().st_ino}
    real = materialise.is_dataless

    def fake(st: os.stat_result) -> bool:  # mocked SF_DATALESS: no File Provider in a test
        return st.st_ino in online or real(st)

    monkeypatch.setattr(materialise, "is_dataless", fake)
    monkeypatch.setattr(arm_local, "is_dataless", fake)
    config = _setup(tmp_path, local_source_table("work", folder))
    assert run_cycle(config, mode=None, budget_bytes=0).exit_code == 0

    def said(run: dict[str, int]) -> list[str]:
        return [note for note in _reread(config, done=False, run=run) if "read again" in note]

    waits = "note: 1 online-only file(s) in work wait for a later sync's download budget"
    assert any(line.startswith(waits) for line in _lines(config)), "a later sync has a file to download"
    again = f"note: {loop.SYNC_AGAIN}%d file(s) in work are {REREAD_TAIL}"
    with Manifest(config.state_paths.db) as manifest:
        manifest._db.execute("UPDATE runs SET counts_json = '{}'")  # as if no run had looked before
    assert said({"ocr_over": 1, "reread_left": 5}) == [again % 5], "the first look: nothing to hold it to"
    for left in (6, 7, 8):
        crowded = f"note: {left} file(s) in work {CROWDED_TAIL}"
        assert said({"ocr_over": 1, "reread_left": left}) == [crowded]
        assert loop.SYNC_AGAIN not in crowded and "another sync does not clear it" not in crowded
    assert said({"ocr_over": 1, "reread_left": 8}) == [again % 8], "no more joined"
    assert said({"ocr_over": 1, "reread": 2, "reread_left": 9}) == [again % 9], (
        "it read two: more joined than were read, and the next sync reads on"
    )
    assert said({"reread_left": 12}) == [f"note: 12 file(s) in work {STUCK_TAIL}"], (
        "OCR time left and nothing read: that is not new files"
    )
    assert said({"ocr_over": 1, "reread_left": 13}) == [f"note: 13 file(s) in work {CROWDED_TAIL}"]
    online.clear()
    assert run_cycle(config, mode=None).exit_code == 0, "the last file is downloaded"
    assert not any(line.startswith(waits) for line in _lines(config))
    assert said({"ocr_over": 1, "reread_left": 14}) == [again % 14], "the next sync brings no new file"


def test_a_re_read_the_last_sync_read_nothing_of_does_not_say_sync_again(
    tmp_path: Path, folder: Path
) -> None:
    """A sync that read none of the files left, with time to spare, was stopped by something another sync
    does not clear: pandoc cannot be run, the OCR helper stopped working, a folder is not listed.  The note
    then gives the count and does not say "sync again", so a loop on that note ends."""
    config = _synced(tmp_path, folder)
    stuck = f"note: 37 file(s) in work {STUCK_TAIL}"
    assert _reread(config, done=False, run={"reread_left": 37}) == [stuck]
    assert _reread(config, done=False, run={"reread": 3, "reread_left": 37, "ocr_down": 1}) == [stuck]
    assert loop.SYNC_AGAIN not in stuck


@pytest.mark.parametrize("held", [True, False], ids=["listing held", "source failed"])
def test_a_sync_that_did_not_get_to_the_source_ends_the_sync_again_note(
    tmp_path: Path, folder: Path, held: bool
) -> None:
    """A sync reaches a source's re-read only after it listed the folder and did the source's own work.
    When macOS holds the listing for a privacy prompt, or the source fails, the run says nothing about a
    re-read, and the note went by the last run that did: "sync again: 37 file(s)" stood for as long as
    the prompt was unanswered, and an agent that follows the note ran every sync it was allowed.  The
    note now goes by the source's newest pass: skipped or failed, it does not say "sync again"."""
    config = _synced(tmp_path, folder)
    assert _reread(config, done=False, run={"reread": 12, "reread_left": 37}) == [
        f"note: {loop.SYNC_AGAIN}37 file(s) in work are {REREAD_TAIL}"
    ]
    for _ in range(12):
        assert _pass_without_a_look(config, "work", held=held) == [f"note: file(s) in work {STUCK_TAIL}"], (
            "no count: no sync has counted them since"
        )
    # The prompt is answered (or the source reads again): the next sync looks, and the note is its own.
    with Manifest(config.state_paths.db) as manifest:
        run_id = manifest.begin_run(CycleMode.POLL, host="mac", pid=1)
        manifest.record_source_pass(
            run_id, "work", pass_kind=PassKind.FULL, enumeration_complete=True, cursor_reset=False, counts={}
        )
        manifest.finish_run(
            run_id, status="ok", commit_sha=None, counts={"reread_for": 7, "reread": 9, "reread_left": 28}
        )
    assert [line for line in _lines(config) if line.startswith("note: ")] == [
        f"note: {loop.SYNC_AGAIN}28 file(s) in work are {REREAD_TAIL}"
    ]


def test_a_re_read_with_no_count_yet_says_sync_again_once(tmp_path: Path, folder: Path) -> None:
    """A record that is not finished and no count to go with it: a file joined after the last sync looked
    (converted past the OCR time by a later step), a cycle died while it read one, or no run of this build
    has looked yet.  One more sync counts them, so the note says "sync again" without a number."""
    config = _synced(tmp_path, folder)
    unknown = f"note: {loop.SYNC_AGAIN}file(s) in work are {REREAD_TAIL}"
    assert _reread(config, done=False, run={}) == [unknown]
    assert _reread(config, done=True, run={}, reading="0123") == [unknown], "a cycle died in that read"
    with Manifest(config.state_paths.db) as manifest:
        manifest._db.execute("UPDATE runs SET counts_json = '{}'")
    assert _reread(config, done=False, run=None) == [unknown], "no run says what it looked for"


def test_the_re_read_note_names_every_source_that_is_not_finished(tmp_path: Path, folder: Path) -> None:
    other = tmp_path / "Archive"
    other.mkdir()
    (other / "Old Note.txt").write_text("An older note.\n", encoding="utf-8")
    config = _synced(tmp_path, folder, local_source_table("archive", other))
    _reread(config, done=False, run=None, sid="archive")
    assert _reread(config, done=False, run={"reread": 5, "reread_left": 9}) == [
        f"note: {loop.SYNC_AGAIN}9 file(s) in archive, work are {REREAD_TAIL}"
    ]
    # One of the two was not reached by the last sync: the count is the other's, and each has its note.
    assert _pass_without_a_look(config, "archive", held=True) == [
        f"note: {loop.SYNC_AGAIN}9 file(s) in work are {REREAD_TAIL}",
        f"note: file(s) in archive {STUCK_TAIL}",
    ]


# ---- the operator's waits ---------------------------------------------------------------------------------


def test_waits_breaker_and_queued_purges(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    now = datetime.now(UTC)
    with Manifest(config.state_paths.db) as manifest:
        manifest.trip_breaker(
            "work",
            candidates=3,
            tripped_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            until=(now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    selector = governance.PurgeSelector(source_id="work", path_glob="*")
    assert governance.enqueue_purge(config.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`",
        f"WAITING ON YOU: 1 queued purge(s): preview with `{BIN} purge --queue --dry-run` (writes nothing) "
        f"before you run `{BIN} purge --queue` (rewrites history)",
        "WAITING ON YOU: the deletion breaker tripped on work (3 file(s) gone): if they really were deleted, "
        f"run `{BIN} accept-deletions work`",
    ]


def test_next_lines_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller's exit status never depends on the hint: an unreadable state is a logged warning, no line."""
    config = _setup(tmp_path)

    def boom(_config: Config, **_kw: object) -> loop.NextStep:
        raise OSError("disk gone")

    monkeypatch.setattr(loop, "next_step", boom)
    assert loop.next_lines(config) == []


# ---- recordings: a wait or a note, never rule 3 (spec S0 rules 3 and 6) -----------------------------------

DRAFT_STEP = f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`"
FINDER_NOTE_TAIL = (
    "could not be downloaded by agentsync: in Finder choose Always Keep on This Device on their folder, or "
    "Download Now on a file; the next background sync reads them; they do not block the next step"
)


def _recordings(tmp_path: Path, folder: Path, *names: str) -> tuple[Config, dict[str, str]]:
    """A synced folder holding ``names`` (made-up bytes) beside the note; their stable ids by name."""
    for name in names:
        (folder / name).write_bytes(b"\x00\x00\x00\x18ftypmp42 not a real recording")
    config = _synced(tmp_path, folder)
    with Manifest(config.state_paths.db) as manifest:
        ids = {r.rel_path: r.stable_id for r in manifest.iter_items("work") if r.rel_path in names}
    assert sorted(ids) == sorted(names)
    return config, ids


def _waiting(config: Config, sid: str, progress: str | None) -> None:
    """Mark ``sid`` as the recording pass leaves a local recording it has not finished reading."""
    with Manifest(config.state_paths.db) as manifest:
        manifest.set_verdict("work", sid, Verdict.DEFERRED)
        manifest.set_state("work", sid, RowState.LIVE, RECORDING_WAITS)
        if progress is not None:
            manifest.set_meta(f"{loop.RECORDING_PROGRESS_META}work:{sid}", progress)


def _refused(config: Config, sid: str, size: int) -> None:
    """Mark ``sid`` as an online-only file whose download failed (errno 89, a refusal or the deadline)."""
    with Manifest(config.state_paths.db) as manifest:
        manifest.set_verdict("work", sid, Verdict.DEFERRED)
        manifest.set_state("work", sid, RowState.DATALESS, HYDRATION_REFUSED)
    conn = sqlite3.connect(config.state_paths.db)
    try:
        conn.execute("UPDATE items SET size = ? WHERE source_id = 'work' AND stable_id = ?", (size, sid))
        conn.commit()
    finally:
        conn.close()


def _install_background_job(config: Config) -> None:
    """What ``loop`` reads to know a background job is installed: the poll job's plist (never launchctl)."""
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{config.launchd_label_prefix}.poll.plist").write_bytes(b"<plist/>")


def _commands(line: str) -> list[str]:
    """The ``agentsync`` commands a line names in backticks, from ``agentsync`` on."""
    return [
        "agentsync " + span.split("agentsync ", 1)[1]
        for span in re.findall(r"`([^`]+)`", line)
        if "agentsync " in span
    ]


def test_the_waiting_note_says_n_of_m_minutes(tmp_path: Path, folder: Path) -> None:
    """The minutes are the waiting rows' progress, summed, then floored: 35 of 127. A row with no length yet
    makes it "of at least"; with no length at all the note has no minutes."""
    config, ids = _recordings(tmp_path, folder, "standup.mp4", "review.mov", "demo.m4v")
    _install_background_job(config)
    _waiting(config, ids["standup.mp4"], "2100000 3600000")  # 35 of 60 minutes
    _waiting(config, ids["review.mov"], "59999 4020000")  # 0 of 67 minutes
    note = (
        "note: 2 recording(s) in work are still being read ({}); each background sync reads more; they do "
        "not block the next step"
    )
    assert _lines(config) == [DRAFT_STEP, note.format("35 of 127 minutes")]
    _waiting(config, ids["demo.m4v"], None)
    assert _lines(config) == [DRAFT_STEP, note.format("35 of at least 127 minutes").replace("2 rec", "3 rec")]
    for sid in ids.values():
        _waiting(config, sid, "")
    assert _lines(config) == [DRAFT_STEP, note.replace(" ({})", "").replace("2 rec", "3 rec")]


def test_a_recording_that_waits_is_a_wait_or_a_note_never_rule_three(tmp_path: Path, folder: Path) -> None:
    """With no background job a waiting recording is a WAITING line naming the two runs that read one; once a
    job is installed it is a note. Either way the loop goes on to rule 4, never rule 3."""
    config, ids = _recordings(tmp_path, folder, "standup.mp4")
    _waiting(config, ids["standup.mp4"], "600000 3600000")
    step = loop.next_step(config)
    assert step.rule == 4
    (wait,) = step.waits
    assert wait == (
        "1 recording(s) in work wait to be read (10 of 60 minutes), and no background sync is installed to "
        "read them (an interactive sync reads no recording): run "
        f"`{loop.INSTALL_SH} --confirm-install-agent`, or `{BIN} materialise <file>` for one recording; they "
        "do not block the next step"
    )
    assert step.notes == ()
    for command in _commands(wait):
        _assert_fix_parses(command)
    assert _commands(wait) == ["agentsync materialise <file>"]
    _install_background_job(config)
    step = loop.next_step(config)
    assert (step.rule, step.waits) == (4, ())
    assert step.notes == (
        "1 recording(s) in work are still being read (10 of 60 minutes); each background sync reads more; "
        "they do not block the next step",
    )


def test_the_finder_note_counts_the_refused_recordings_and_their_size(tmp_path: Path, folder: Path) -> None:
    """Online-only recordings agentsync could not download are one counted note with their size in GB, one
    decimal: never a WAITING line and never rule 3."""
    config, ids = _recordings(tmp_path, folder, "standup.mp4", "review.mov")
    _refused(config, ids["standup.mp4"], 1_234_000_000)
    _refused(config, ids["review.mov"], 400_000_000)
    step = loop.next_step(config)
    assert (step.rule, step.waits) == (4, ())
    assert step.lines() == [
        DRAFT_STEP,
        f"note: 2 online-only recording(s) in work (1.6 GB) {FINDER_NOTE_TAIL}",
    ]


def test_a_document_refused_beside_a_recording_keeps_its_own_wait(tmp_path: Path, folder: Path) -> None:
    """A refused document stays the operator's wait (another sync does not fetch it); the refused recording
    beside it is the Finder note, counted apart."""
    config, ids = _recordings(tmp_path, folder, "standup.mp4")
    with Manifest(config.state_paths.db) as manifest:
        (doc,) = [r.stable_id for r in manifest.iter_items("work") if r.rel_path == NOTE_NAME]
    _refused(config, ids["standup.mp4"], 466_600_000)
    _refused(config, doc, 120)
    assert _lines(config) == [
        DRAFT_STEP,
        "WAITING ON YOU: 1 online-only file(s) in work could not be downloaded (macOS refused): in Finder, "
        f"choose Download Now (or Always Keep on This Device) on their folder, then run `{BIN} sync`",
        f"note: 1 online-only recording(s) in work (0.5 GB) {FINDER_NOTE_TAIL}",
    ]


def test_an_online_only_recording_is_never_over_the_document_budget(tmp_path: Path, folder: Path) -> None:
    """A recording downloads under an allowance of its own (spec S0 rule 3), so one larger than the source's
    max_materialise_bytes is no `materialise --budget` wait: it waits for a later sync, as any online file
    does."""
    config, ids = _recordings(tmp_path, folder, "standup.mp4")
    with Manifest(config.state_paths.db) as manifest:
        manifest.set_verdict("work", ids["standup.mp4"], Verdict.DEFERRED)
        manifest.set_state("work", ids["standup.mp4"], RowState.DATALESS, None)
    conn = sqlite3.connect(config.state_paths.db)
    try:
        conn.execute("UPDATE items SET size = ? WHERE stable_id = ?", (3_000_000_000, ids["standup.mp4"]))
        conn.commit()
    finally:
        conn.close()
    assert _lines(config) == [
        DRAFT_STEP,
        "note: 1 online-only file(s) in work wait for a later sync's download budget; they do not block the "
        "next step",
    ]


def test_the_purge_wait_names_the_preview_before_the_run(tmp_path: Path) -> None:
    """Bring-back D6.h: the wait is built from the queue's length alone, and a run rewrites history. It
    names the dry run first, and both commands are ones the CLI parses."""
    config = _setup(tmp_path)
    selector = governance.PurgeSelector(source_id="work", path_glob="*")
    assert governance.enqueue_purge(config.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    (wait,) = [w for w in loop.next_step(config).waits if "queued purge(s)" in w]
    assert _commands(wait) == ["agentsync purge --queue --dry-run", "agentsync purge --queue"]
    for command in _commands(wait):
        _assert_fix_parses(command)


def test_the_breaker_wait_of_an_incomplete_source_asks_for_a_full_listing_first(
    tmp_path: Path, folder: Path
) -> None:
    config = _synced(tmp_path, folder)
    now = datetime.now(UTC)
    with Manifest(config.state_paths.db) as manifest:
        manifest.trip_breaker(
            "work",
            candidates=3,
            tripped_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            until=(now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest.set_enumeration_complete("work", False, 1)
    lines = _lines(config)
    breaker = [line for line in lines if "deletion breaker" in line]
    assert breaker == [
        "WAITING ON YOU: the deletion breaker tripped on work (3 file(s) gone): if they really were deleted, "
        f"run `{BIN} accept-deletions work` once a sync lists work in full"
    ]


def test_checks_name_the_doctor_check_each_wait_explains(tmp_path: Path, folder: Path) -> None:
    """``NextStep.checks`` (§16.39 N16): the doctor check each wait explains, for the setup report's "yours"
    group; ``lines()`` never prints it. A complete source names none; an empty cloud folder names its
    source's heartbeat, a queued purge the purge queue; an excluded folder (no wait until the next sync)
    names nothing."""
    config = _synced(tmp_path, folder)
    assert loop.next_step(config, count_queue=False).checks == ()
    root = Path(os.environ["HOME"]) / "Library" / "CloudStorage" / "OneDrive-Contoso" / "Work"
    (root / "Empty").mkdir(parents=True)
    _write(root / NOTE_NAME, "The purchase order is approved.\n")
    config = _setup(Path.home() / "agent-context", local_source_table("work", root))
    run_cycle(config, mode=None)
    step = loop.next_step(config, count_queue=False)
    assert step.checks == ("heartbeat.work",)
    assert not any("heartbeat" in ln for ln in step.lines())
    selector = governance.PurgeSelector(source_id="work", path_glob="*")
    assert governance.enqueue_purge(config.state_paths.root, selector, governance.PurgeReason.OPERATOR)
    assert loop.next_step(config, count_queue=False).checks == ("governance.purge_queue", "heartbeat.work")
    path = config.config_path
    path.write_text(path.read_text(encoding="utf-8") + 'exclude = ["/Empty/"]\n', encoding="utf-8")
    assert loop.next_step(load_config(path), count_queue=False).checks == ("governance.purge_queue",)


def test_the_baseline_next_names_the_agents_md_that_carries_the_section(tmp_path: Path, folder: Path) -> None:
    """Bring-back 2026-10-06 row 23 (B13 N34): rules 4, 6 and 8 send the agent to the skill's Baseline
    questions section, which an agent that loads no skill (Codex, Copilot) never reads. The docs repo's root
    AGENTS.md carries the same section (``publish.root_guide``), so the NEXT line names that file by its path:
    a bare "AGENTS.md" is the agent's own project's when it runs from another folder."""
    config = _synced(tmp_path, folder)
    [step] = [ln for ln in _lines(config) if ln.startswith("NEXT: ")]
    m = re.search(r"\(also in (\S+)\)", step)
    assert m, step
    guide = Path(m.group(1)).expanduser()
    assert guide == config.docs_repo / "AGENTS.md" and guide.is_file(), guide
    assert skill.BASELINE in guide.read_text(encoding="utf-8"), "the file named carries the section"
