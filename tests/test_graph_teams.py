"""Graph Teams arm: channel delta + replies merged into deterministic monthly rollups; fetch; recovery."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agentsync.config import SourceConfig
from agentsync.errors import BudgetExhaustedError, GraphBadCursor, GraphError, GraphGone, GraphNotFound
from agentsync.graph.client import DeltaResult, GraphPage
from agentsync.graph.drive import _parse_graph_time_ns
from agentsync.graph.teams import TeamsArm, month_stable_id
from agentsync.model import TEAMS_MONTH_SCHEMA, ByteBudget, PassKind, SourceItem, SourceKind

BASE = "https://graph.microsoft.com/v1.0"
TEAM = "T1"
CHANNEL = "19:abc@thread.tacv2"
CH = f"/teams/{TEAM}/channels/{CHANNEL}"
DELTA_PATH = f"{CH}/messages/delta"
CURSOR = f"{BASE}{CH}/messages/delta?$deltatoken=CUR"
NEXT = f"{BASE}{CH}/messages/delta?$deltatoken=NEXT"


@dataclass
class FakeClient:
    json: dict[str, Any] = field(default_factory=dict)
    collections: dict[str, list[list[dict[str, Any]]] | Exception] = field(default_factory=dict)
    deltas: dict[str, list[list[dict[str, Any]] | Exception]] = field(default_factory=dict)
    calls: list[tuple[str, str, Mapping[str, str] | None, Mapping[str, str] | None]] = field(
        default_factory=list
    )

    def get_json(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append(("get_json", path, params, headers))
        body = self.json[path]
        if isinstance(body, Exception):
            raise body
        return dict(body)

    def iter_pages(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> Iterator[GraphPage]:
        self.calls.append(("iter_pages", path, params, headers))
        pages = self.collections.get(path, [[]])
        if isinstance(pages, Exception):
            raise pages
        for page in pages:
            yield GraphPage(value=tuple(page), next_link=None, delta_link=None)

    def delta(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        on_page: Callable[[GraphPage], None] | None = None,
    ) -> DeltaResult:
        self.calls.append(("delta", path, params, headers))
        queue = self.deltas[path]
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(step, Exception):
            raise step
        return DeltaResult(items=tuple(step), delta_link=NEXT, pages=1)

    def download(self, *_: Any, **__: Any) -> tuple[int, str]:
        raise AssertionError("the Teams arm never downloads")

    def called(self, method: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == method]


def names() -> dict[str, Any]:
    return {f"/teams/{TEAM}": {"displayName": "Finance"}, CH: {"displayName": "General"}}


def message(
    ident: str,
    created: str,
    *,
    modified: str | None = None,
    reply_to: str | None = None,
    text: str = "<p>hello</p>",
    content_type: str = "html",
    message_type: str = "message",
    deleted_at: str | None = None,
    who: str = "Dana",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": ident,
        "replyToId": reply_to,
        "messageType": message_type,
        "createdDateTime": created,
        "lastModifiedDateTime": modified or created,
        "deletedDateTime": deleted_at,
        "subject": None,
        "from": {"user": {"displayName": who}, "application": None, "device": None},
        "body": {"contentType": content_type, "content": text},
        "attachments": attachments or [],
    }


def cfg() -> SourceConfig:
    return SourceConfig(id="chan", kind=SourceKind.GRAPH_TEAMS, team_id=TEAM, channel_id=CHANNEL)


def arm_for(client: FakeClient, store: Path) -> TeamsArm:
    return TeamsArm(client, cfg(), store)  # type: ignore[arg-type]


def by_id(items: tuple[SourceItem, ...]) -> dict[str, SourceItem]:
    return {i.stable_id: i for i in items}


def read_month(store: Path, month: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((store / f"{month}.json").read_text(encoding="utf-8"))
    return doc


def test_month_stable_id() -> None:
    assert month_stable_id(CHANNEL, "2026-09") == f"{CHANNEL}:2026-09"
    for bad in ("2026-9", "2026-13", "26-09", "2026-09-01"):
        with pytest.raises(ValueError):
            month_stable_id(CHANNEL, bad)


# ---------------------------------------------------------------------------------------------------------
# FULL scan builds the store
# ---------------------------------------------------------------------------------------------------------


def full_client() -> FakeClient:
    client = FakeClient(
        json=names(),
        deltas={
            DELTA_PATH: [
                [
                    message("R1", "2026-08-31T23:00:00Z", text="<p>root in August</p>"),
                    message("R2", "2026-09-02T09:00:00Z", modified="2026-09-03T10:00:00.5Z"),
                    message("SYS", "2026-09-02T09:30:00Z", message_type="systemEventMessage"),
                    message("R3", "2026-09-05T09:00:00Z", deleted_at="2026-09-06T00:00:00Z", text="<p>x</p>"),
                    message(
                        "R4",
                        "2026-09-07T09:00:00Z",
                        text="a < b\nnext",
                        content_type="text",
                        attachments=[
                            {"name": "z.xlsx", "contentUrl": "https://sp/z.xlsx"},
                            {"name": "a.docx", "contentUrl": "https://sp/a.docx"},
                        ],
                    ),
                ]
            ]
        },
    )
    # A September reply to the August root stays with its thread (the root's month).
    client.collections[f"{CH}/messages/R1/replies"] = [
        [message("R1-a", "2026-09-01T08:00:00Z", reply_to="R1", who="Lee")],
    ]
    client.collections[f"{CH}/messages/R3/replies"] = GraphNotFound(404, "NotFound", "thread gone")
    return client


def test_full_scan_writes_month_rollups(tmp_path: Path) -> None:
    store = tmp_path / "teams" / "chan"
    client = full_client()
    result = arm_for(client, store).scan(None, full=False)

    assert client.called("delta") == [DELTA_PATH]
    assert client.calls[0][2] is None  # no query options that the channel delta might reject
    assert result.pass_kind is PassKind.FULL and result.enumeration_complete
    assert result.new_cursor == NEXT

    aug, sep = read_month(store, "2026-08"), read_month(store, "2026-09")
    assert aug["schema"] == TEAMS_MONTH_SCHEMA
    assert (aug["team_id"], aug["team_name"], aug["channel_id"], aug["channel_name"]) == (
        TEAM,
        "Finance",
        CHANNEL,
        "General",
    )
    assert [m["id"] for m in aug["messages"]] == ["R1", "R1-a"]
    assert aug["messages"][1]["reply_to_id"] == "R1" and aug["messages"][1]["from"] == "Lee"
    assert [m["id"] for m in sep["messages"]] == ["R2", "R3", "R4"]  # system event skipped
    r2, r3, r4 = sep["messages"]
    assert r2["created"] == "2026-09-02T09:00:00.000Z" and r2["last_modified"] == "2026-09-03T10:00:00.500Z"
    assert r3["deleted"] is True and r3["body_html"] == ""
    assert r4["body_html"] == "a &lt; b<br>next"  # text bodies become safe HTML
    assert [a["name"] for a in r4["attachments"]] == ["a.docx", "z.xlsx"]
    assert set(r2) == {
        "id",
        "reply_to_id",
        "created",
        "last_modified",
        "from",
        "subject",
        "body_html",
        "deleted",
        "attachments",
    }

    items = by_id(result.items)
    assert set(items) == {f"{CHANNEL}:2026-08", f"{CHANNEL}:2026-09"}
    item = items[f"{CHANNEL}:2026-09"]
    data = (store / "2026-09.json").read_bytes()
    assert item.name == item.rel_path == "2026-09.teams.json"
    assert item.suffix == ".teams.json"
    assert item.size == len(data)
    assert item.remote_hashes.sha256 == hashlib.sha256(data).hexdigest()
    assert item.ctag == f"sha256:{hashlib.sha256(data).hexdigest()}"
    assert item.mtime_ns == _parse_graph_time_ns("2026-09-07T09:00:00Z")  # max last_modified in the month
    assert item.extra["message_count"] == 3
    assert [i.stable_id for i in result.items] == sorted(items)


def test_month_files_are_canonical_json(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm_for(full_client(), store).scan(None, full=False)
    raw = (store / "2026-09.json").read_bytes()
    doc = json.loads(raw)
    assert raw == (json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    mode = stat.S_IMODE((store / "2026-09.json").stat().st_mode)
    assert mode == 0o600  # message bodies are private state


def test_rescan_is_byte_identical(tmp_path: Path) -> None:
    store = tmp_path / "s"
    first = arm_for(full_client(), store).scan(None, full=False)
    before = {p.name: p.read_bytes() for p in store.iterdir()}
    second = arm_for(full_client(), store).scan(None, full=True)
    after = {p.name: p.read_bytes() for p in store.iterdir()}
    assert before == after
    assert [(i.stable_id, i.remote_hashes.sha256) for i in first.items] == [
        (i.stable_id, i.remote_hashes.sha256) for i in second.items
    ]


# ---------------------------------------------------------------------------------------------------------
# DELTA scans merge
# ---------------------------------------------------------------------------------------------------------


def test_delta_merges_and_emits_only_touched_months(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm_for(full_client(), store).scan(None, full=False)
    aug_before = (store / "2026-08.json").read_bytes()

    client = FakeClient(
        json=names(),
        deltas={
            CURSOR: [
                [
                    message(
                        "R2", "2026-09-02T09:00:00Z", modified="2026-09-10T00:00:00Z", text="<p>edited</p>"
                    ),
                    message("R5", "2026-10-01T00:00:00Z"),
                ]
            ]
        },
    )
    client.collections[f"{CH}/messages/R2/replies"] = [
        [message("R2-a", "2026-09-11T00:00:00Z", reply_to="R2")]
    ]
    result = arm_for(client, store).scan(CURSOR, full=False)

    assert client.called("delta") == [CURSOR]
    assert result.pass_kind is PassKind.DELTA and not result.enumeration_complete
    assert {i.stable_id for i in result.items} == {f"{CHANNEL}:2026-09", f"{CHANNEL}:2026-10"}
    sep = read_month(store, "2026-09")
    assert [m["id"] for m in sep["messages"]] == ["R2", "R3", "R4", "R2-a"]  # old messages kept, sorted
    assert sep["messages"][0]["body_html"] == "<p>edited</p>"
    assert (store / "2026-08.json").read_bytes() == aug_before  # untouched month not rewritten


def test_full_scan_emits_every_stored_month(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm_for(full_client(), store).scan(None, full=False)
    client = FakeClient(json=names(), deltas={DELTA_PATH: [[message("R9", "2026-10-01T00:00:00Z")]]})
    result = arm_for(client, store).scan(None, full=True)
    assert [i.name for i in result.items] == [
        "2026-08.teams.json",
        "2026-09.teams.json",
        "2026-10.teams.json",
    ]


def test_reply_in_feed_is_filed_under_its_roots_month(tmp_path: Path) -> None:
    store = tmp_path / "s"
    client = FakeClient(
        json={**names(), f"{CH}/messages/ROOT": {"id": "ROOT", "createdDateTime": "2026-07-15T00:00:00Z"}},
        deltas={CURSOR: [[message("RE", "2026-09-01T00:00:00Z", reply_to="ROOT")]]},
    )
    result = arm_for(client, store).scan(CURSOR, full=False)
    assert [i.name for i in result.items] == ["2026-07.teams.json"]
    assert [m["id"] for m in read_month(store, "2026-07")["messages"]] == ["RE"]


def test_reply_to_vanished_root_is_skipped(tmp_path: Path) -> None:
    client = FakeClient(
        json={**names(), f"{CH}/messages/GONE": GraphNotFound(404, "NotFound", "gone")},
        deltas={CURSOR: [[message("RE", "2026-09-01T00:00:00Z", reply_to="GONE")]]},
    )
    result = arm_for(client, tmp_path / "s").scan(CURSOR, full=False)
    assert result.items == ()


def test_names_fall_back_when_graph_refuses(tmp_path: Path) -> None:
    store = tmp_path / "s"
    refused = GraphError(403, "Forbidden", "missing Team.ReadBasic.All")
    client = FakeClient(
        json={f"/teams/{TEAM}": refused, CH: refused},
        deltas={DELTA_PATH: [[message("R1", "2026-09-01T00:00:00Z")]]},
    )
    arm_for(client, store).scan(None, full=False)
    doc = read_month(store, "2026-09")
    assert (doc["team_name"], doc["channel_name"]) == (TEAM, CHANNEL)
    assert client.called("get_json").count(f"/teams/{TEAM}") == 1  # asked once per arm, not per month


def test_stored_names_survive_a_later_refusal(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm_for(full_client(), store).scan(None, full=False)
    refused = GraphError(403, "Forbidden", "no")
    client = FakeClient(
        json={f"/teams/{TEAM}": refused, CH: refused},
        deltas={CURSOR: [[message("R7", "2026-09-20T00:00:00Z")]]},
    )
    arm_for(client, store).scan(CURSOR, full=False)
    doc = read_month(store, "2026-09")
    assert (doc["team_name"], doc["channel_name"]) == ("Finance", "General")


def test_corrupt_month_is_set_aside_and_rebuilt(tmp_path: Path) -> None:
    store = tmp_path / "s"
    store.mkdir()
    (store / "2026-09.json").write_text("{not json")
    client = FakeClient(json=names(), deltas={CURSOR: [[message("R1", "2026-09-01T00:00:00Z")]]})
    result = arm_for(client, store).scan(CURSOR, full=False)
    assert (store / "2026-09.json.corrupt").read_text() == "{not json"
    assert [m["id"] for m in read_month(store, "2026-09")["messages"]] == ["R1"]
    assert any("unreadable" in a for a in result.alarms)


def test_foreign_channel_month_is_replaced_and_not_emitted(tmp_path: Path) -> None:
    store = tmp_path / "s"
    store.mkdir()
    foreign = {"schema": TEAMS_MONTH_SCHEMA, "channel_id": "19:other", "messages": [], "month": "2026-05"}
    (store / "2026-05.json").write_text(json.dumps(foreign))
    (store / "2026-09.json").write_text(json.dumps({**foreign, "month": "2026-09"}))
    client = FakeClient(json=names(), deltas={DELTA_PATH: [[message("R1", "2026-09-01T00:00:00Z")]]})
    result = arm_for(client, store).scan(None, full=True)
    assert [i.name for i in result.items] == ["2026-09.teams.json"]  # the foreign May file is ignored
    assert read_month(store, "2026-09")["channel_id"] == CHANNEL
    assert any("another channel" in a for a in result.alarms)


# ---------------------------------------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------------------------------------


def test_410_reenumerates(tmp_path: Path) -> None:
    location = f"{BASE}{CH}/messages/delta?$skiptoken=RESYNC"
    client = FakeClient(
        json=names(),
        deltas={
            CURSOR: [GraphGone("resyncRequired", "gone", location)],
            location: [[message("R1", "2026-09-01T00:00:00Z")]],
        },
    )
    result = arm_for(client, tmp_path / "s").scan(CURSOR, full=False)
    assert client.called("delta") == [CURSOR, location]
    assert result.pass_kind is PassKind.FULL and result.cursor_reset and result.enumeration_complete


def test_400_bad_cursor(tmp_path: Path) -> None:
    client = FakeClient(
        json=names(),
        deltas={CURSOR: [GraphBadCursor(400, "BadRequest", "bad token")], DELTA_PATH: [[]]},
    )
    result = arm_for(client, tmp_path / "s").scan(CURSOR, full=False)
    assert "cursor store corrupt: dropped" in result.alarms
    assert result.cursor_reset and result.pass_kind is PassKind.FULL


def test_400_tokenless_propagates(tmp_path: Path) -> None:
    client = FakeClient(deltas={DELTA_PATH: [GraphBadCursor(400, "BadRequest", "odd")]})
    with pytest.raises(GraphBadCursor):
        arm_for(client, tmp_path / "s").scan(None, full=False)


# ---------------------------------------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------------------------------------


def test_fetch_copies_the_month_file(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm = arm_for(full_client(), store)
    item = by_id(arm.scan(None, full=False).items)[f"{CHANNEL}:2026-09"]
    budget = ByteBudget(max_bytes=10_000, max_files=5)
    got = arm.fetch(item, tmp_path / "stage", budget)
    data = (store / "2026-09.json").read_bytes()
    assert got.path.read_bytes() == data
    assert got.path.name.endswith(".teams.json")
    assert got.content_sha256 == item.remote_hashes.sha256
    assert got.size == item.size == budget.used and budget.files_used == 1


def test_fetch_charges_first(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm = arm_for(full_client(), store)
    item = by_id(arm.scan(None, full=False).items)[f"{CHANNEL}:2026-09"]
    budget = ByteBudget(max_bytes=10, max_files=5)
    with pytest.raises(BudgetExhaustedError):
        arm.fetch(item, tmp_path / "stage", budget)
    assert not (tmp_path / "stage").exists()


def test_fetch_rejects_foreign_or_missing_months(tmp_path: Path) -> None:
    arm = arm_for(FakeClient(), tmp_path / "s")
    base: dict[str, Any] = {
        "rel_path": "x.teams.json",
        "name": "x.teams.json",
        "size": 1,
        "mtime_ns": 0,
        "ctime_ns": 0,
    }
    with pytest.raises(ValueError):
        arm.fetch(
            SourceItem(source_id="chan", stable_id="19:other:2026-09", **base), tmp_path, ByteBudget(9, 9)
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        arm.fetch(
            SourceItem(source_id="chan", stable_id=f"{CHANNEL}:../../x", **base), tmp_path, ByteBudget(9, 9)
        )  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        arm.fetch(
            SourceItem(source_id="chan", stable_id=f"{CHANNEL}:2026-01", **base), tmp_path, ByteBudget(9, 9)
        )  # type: ignore[arg-type]


def test_arm_validates_its_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        TeamsArm(FakeClient(), SourceConfig(id="c", kind=SourceKind.GRAPH_TEAMS, team_id=TEAM), tmp_path)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TeamsArm(FakeClient(), SourceConfig(id="m", kind=SourceKind.GRAPH_MAIL, folder="Inbox"), tmp_path)  # type: ignore[arg-type]
    arm = arm_for(FakeClient(), tmp_path)
    assert arm.source_id == "chan" and arm.kind is SourceKind.GRAPH_TEAMS


def test_unreadable_untouched_month_is_alarmed_on_full(tmp_path: Path) -> None:
    store = tmp_path / "s"
    store.mkdir()
    (store / "2026-01.json").write_text("{broken")
    client = FakeClient(json=names(), deltas={DELTA_PATH: [[message("R1", "2026-09-01T00:00:00Z")]]})
    result = arm_for(client, store).scan(None, full=True)
    assert [i.name for i in result.items] == ["2026-09.teams.json"]
    assert any("2026-01" in a and "not emitted" in a for a in result.alarms)
