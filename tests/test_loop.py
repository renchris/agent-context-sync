"""KISS K01: ``loop.next_step`` works the loop's next step out of disk state, one fixture per rule (1-10) and
per operator wait, asserting the exact NEXT / WAITING ON YOU / note lines. No line names a mirror path or
file."""

from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentsync import arm_local, cli, governance, loop, materialise, skill
from agentsync.config import Config, load_config, local_source_table
from agentsync.cycle import run_cycle
from agentsync.manifest import Manifest
from agentsync.model import Verdict

BIN = "~/.local/bin/agentsync"
BASELINE = 'the agentsync-docs skill\'s "Baseline questions" section'
DRAFT_WAIT = (
    "WAITING ON YOU: the baseline questions are a draft: keep about 10 in _eval/questions.md, correct the "
    "answers in _eval/answers.md, and change both files to status: confirmed"
)
NOTE_NAME = "Quarterly Note.txt"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A synced folder holding one small text file."""
    root = tmp_path / "Work"
    root.mkdir()
    (root / NOTE_NAME).write_text("The purchase order is approved.\n", encoding="utf-8")
    return root


def _setup(tmp_path: Path, *tables: str) -> Config:
    """``init`` with no source (a docs repo and the template sources.toml), then ``tables`` appended."""
    cfg = tmp_path / "ctx" / "sources.toml"
    assert cli.main(["init", "--config", str(cfg), "--docs-repo", str(tmp_path / "ctx" / "docs")]) == 0
    cfg.write_text(cfg.read_text(encoding="utf-8") + "".join(tables), encoding="utf-8")
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
    assert _lines(config) == [f"NEXT: the docs repo does not exist yet: run `{BIN} init`"]


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
    text = text.replace('# client_id = "', 'client_id = "', 1)  # the template's commented [graph] key
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
    assert cli.main(["add-source", "--inbox", "--config", str(config.config_path)]) == 0
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


# ---- rules 4-6: the baseline before the first curated page ------------------------------------------------


def test_rule_4_draft_the_baseline_questions(tmp_path: Path, folder: Path) -> None:
    config = _synced(tmp_path, folder)
    assert _lines(config) == [
        f"NEXT: draft the baseline questions: follow step 1 (Draft) of {BASELINE}, then run `{BIN} sync`"
    ]


@pytest.mark.parametrize(("questions", "answers"), [("draft", "draft"), ("confirmed", "draft"), ("", "")])
def test_rule_5_the_operator_confirms_a_draft(
    tmp_path: Path, folder: Path, questions: str, answers: str
) -> None:
    config = _synced(tmp_path, folder)
    _eval(config, questions, answers)
    assert _lines(config) == [
        "NEXT: stop: the operator confirms the baseline questions (WAITING ON YOU below); session done",
        DRAFT_WAIT,
    ]


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
        f"WAITING ON YOU: 1 queued purge(s): run `{BIN} purge --queue`",
        "WAITING ON YOU: the deletion breaker tripped on work (3 file(s) gone): if they really were deleted, "
        f"run `{BIN} accept-deletions work`",
    ]


def test_next_lines_never_raises(tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller's exit status never depends on the hint: an unreadable state is a logged warning, no line."""
    config = _synced(tmp_path, folder)

    def boom(_config: Config, **_kw: object) -> loop.NextStep:
        raise OSError("disk gone")

    monkeypatch.setattr(loop, "next_step", boom)
    assert loop.next_lines(config) == []
