from __future__ import annotations

import pytest

from agentsync.errors import BudgetExhaustedError
from agentsync.model import ByteBudget, RemoteHashes, SourceItem, SourceKind, Verdict


def item(**kw: object) -> SourceItem:
    base: dict[str, object] = {
        "source_id": "s",
        "stable_id": "vol:1",
        "rel_path": "a/b.docx",
        "name": "b.docx",
        "size": 10,
        "mtime_ns": 1,
        "ctime_ns": 1,
    }
    base.update(kw)
    return SourceItem(**base)  # type: ignore[arg-type]


def test_source_item_is_hashable_despite_extra() -> None:
    a = item(extra={"web_url": "x"})
    b = item(extra={"web_url": "y"})
    assert a == b  # extra is excluded from equality
    assert len({a, b}) == 1


@pytest.mark.parametrize(
    ("name", "suffix"),
    [("B.DOCX", ".docx"), ("2026-09.teams.json", ".teams.json"), ("noext", ""), (".hidden", "")],
)
def test_suffix(name: str, suffix: str) -> None:
    assert item(name=name).suffix == suffix


def test_budget_charges_and_refuses_without_charging() -> None:
    b = ByteBudget(max_bytes=100, max_files=2)
    b.charge(60)
    assert b.remaining_bytes == 40
    with pytest.raises(BudgetExhaustedError):
        b.charge(50)
    assert b.used == 60 and b.files_used == 1
    b.charge(40)
    with pytest.raises(BudgetExhaustedError):
        b.charge(0)  # file budget exhausted


def test_enums_are_the_design_vocabulary() -> None:
    assert SourceKind.GRAPH_DRIVE.is_graph and not SourceKind.LOCAL.is_graph
    assert {v.value for v in Verdict} >= {
        "created",
        "unchanged",
        "maybe_changed",
        "metadata_only",
        "dataless",
        "touched_not_changed",
        "changed",
        "output_unchanged",
        "deleted",
        "deletion_candidate",
    }
    assert RemoteHashes().empty and not RemoteHashes(quickxor="x").empty
