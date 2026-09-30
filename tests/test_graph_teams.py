"""Graph Teams arm: paced high-water walks over channel / chat messages -> deterministic monthly rollups."""

from __future__ import annotations

import hashlib
import itertools
import json
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import agentsync.graph.teams as teams_module
from agentsync.config import SourceConfig
from agentsync.errors import BudgetExhaustedError, GraphError, GraphNotFound
from agentsync.graph.client import GraphPage
from agentsync.graph.drive import _parse_graph_time_ns
from agentsync.graph.teams import TeamsArm, month_stable_id
from agentsync.model import TEAMS_MONTH_SCHEMA, ByteBudget, PassKind, SourceItem, SourceKind

BASE = "https://graph.microsoft.com/v1.0"
TEAM = "T1"
CHANNEL = "19:abc@thread.tacv2"
CH = f"/teams/{TEAM}/channels/{CHANNEL}"
LIST = f"{CH}/messages"
CHAT = "19:chat_xyz@unq.gbl.spaces"
CHAT_PATH = f"/me/chats/{CHAT}"
CHAT_LIST = f"{CHAT_PATH}/messages"


@dataclass
class FakeClock:
    """Monotonic fake: ``sleep`` advances it, every request records the time it went out."""

    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class FakeClient:
    json: dict[str, Any] = field(default_factory=dict)
    collections: dict[str, list[list[dict[str, Any]]] | Exception] = field(default_factory=dict)
    clock: FakeClock = field(default_factory=FakeClock)
    calls: list[tuple[str, str, Mapping[str, str] | None]] = field(default_factory=list)
    request_times: list[float] = field(default_factory=list)

    def get_json(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append(("get_json", path, params))
        self.request_times.append(self.clock.now)
        body = self.json[path]
        if isinstance(body, Exception):
            raise body
        return dict(body)

    def iter_pages(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> Iterator[GraphPage]:
        pages = self.collections.get(path, [[]])
        for i, page in enumerate(pages if not isinstance(pages, Exception) else [[]]):
            self.calls.append(("page", path if i == 0 else f"{path}#page{i + 1}", params if i == 0 else None))
            self.request_times.append(self.clock.now)
            if isinstance(pages, Exception):
                raise pages
            nxt = f"{BASE}{path}?$skiptoken={i + 1}" if i + 1 < len(pages) else None
            yield GraphPage(value=tuple(page), next_link=nxt, delta_link=None)

    def delta(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("the Teams arm never calls a delta endpoint")

    def download(self, *_: Any, **__: Any) -> tuple[int, str]:
        raise AssertionError("the Teams arm never downloads")

    def pages(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "page"]

    def called(self, method: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == method]


def hwm(iso: str) -> str:
    return f"hwm:{iso}"


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


def arm_for(client: FakeClient, store: Path, source: SourceConfig | None = None) -> TeamsArm:
    return TeamsArm(
        client,  # type: ignore[arg-type]
        source or cfg(),
        store,
        clock=client.clock.clock,
        sleep=client.clock.sleep,
    )


def chat_cfg() -> SourceConfig:
    return SourceConfig(id="chat", kind=SourceKind.GRAPH_TEAMS, team_id="chats", channel_id=CHAT)


def chain(root: dict[str, Any], *replies: dict[str, Any], more: str | None = None) -> dict[str, Any]:
    """A root as the list endpoint returns it with $expand=replies."""
    out = {**root, "replies": list(replies)}
    if more:
        out["replies@odata.nextLink"] = more
    return out


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
# bootstrap: a full walk builds the store
# ---------------------------------------------------------------------------------------------------------


def full_client() -> FakeClient:
    """Newest-first chains over two pages (the list is sorted by the chain's last modification)."""
    more = f"{BASE}{LIST}/R1/replies?$skiptoken=MORE"
    client = FakeClient(
        json=names(),
        collections={
            LIST: [
                [
                    chain(
                        message(
                            "R4",
                            "2026-09-07T09:00:00Z",
                            text="a < b\nnext",
                            content_type="text",
                            attachments=[
                                {"name": "z.xlsx", "contentUrl": "https://sp/z.xlsx"},
                                {"name": "a.docx", "contentUrl": "https://sp/a.docx"},
                            ],
                        )
                    ),
                    chain(
                        message(
                            "R3", "2026-09-05T09:00:00Z", deleted_at="2026-09-06T00:00:00Z", text="<p>x</p>"
                        )
                    ),
                ],
                [
                    chain(message("R2", "2026-09-02T09:00:00Z", modified="2026-09-03T10:00:00.5Z")),
                    chain(message("SYS", "2026-09-02T09:30:00Z", message_type="systemEventMessage")),
                    # A September reply to the August root stays with its thread (the root's month); the
                    # second reply sits behind replies@odata.nextLink.
                    chain(
                        message("R1", "2026-08-31T23:00:00Z", text="<p>root in August</p>"),
                        message("R1-a", "2026-09-01T08:00:00Z", reply_to="R1", who="Lee"),
                        more=more,
                    ),
                ],
            ],
            more: [[message("R1-b", "2026-09-01T09:00:00Z", reply_to="R1")]],
        },
    )
    return client


def test_bootstrap_walks_the_whole_channel_and_writes_month_rollups(tmp_path: Path) -> None:
    store = tmp_path / "teams" / "chan"
    client = full_client()
    result = arm_for(client, store).scan(None, full=False)

    assert client.pages()[0] == LIST
    assert client.calls[[c[1] for c in client.calls].index(LIST)][2] == {"$top": "50", "$expand": "replies"}
    assert f"{LIST}#page2" in client.pages()  # walked to the end
    assert result.pass_kind is PassKind.FULL and result.enumeration_complete and not result.cursor_reset
    assert result.new_cursor == hwm("2026-09-07T09:00:00.000Z")  # newest chain modification

    aug, sep = read_month(store, "2026-08"), read_month(store, "2026-09")
    assert aug["schema"] == TEAMS_MONTH_SCHEMA
    assert (aug["team_id"], aug["team_name"], aug["channel_id"], aug["channel_name"]) == (
        TEAM,
        "Finance",
        CHANNEL,
        "General",
    )
    assert [m["id"] for m in aug["messages"]] == ["R1", "R1-a", "R1-b"]  # inline + nextLink replies
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
    second = arm_for(full_client(), store).scan(first.new_cursor, full=True)
    after = {p.name: p.read_bytes() for p in store.iterdir()}
    assert before == after
    assert second.new_cursor == first.new_cursor
    assert [(i.stable_id, i.remote_hashes.sha256) for i in first.items] == [
        (i.stable_id, i.remote_hashes.sha256) for i in second.items
    ]


def test_empty_channel_bootstrap_has_no_cursor(tmp_path: Path) -> None:
    result = arm_for(FakeClient(json=names(), collections={LIST: [[]]}), tmp_path / "s").scan(
        None, full=False
    )
    assert result.items == () and result.new_cursor is None and result.enumeration_complete


# ---------------------------------------------------------------------------------------------------------
# incremental: newest-first, stop at the first chain older than HWM - 5 min (C15 section 9 item 12)
# ---------------------------------------------------------------------------------------------------------


def test_incremental_walk_stops_at_the_first_chain_below_the_high_water_mark(tmp_path: Path) -> None:
    store = tmp_path / "s"
    arm_for(full_client(), store).scan(None, full=False)
    aug_before = (store / "2026-08.json").read_bytes()
    stored = hwm("2026-09-10T00:00:00.000Z")
    client = FakeClient(
        json=names(),
        collections={
            LIST: [
                [
                    chain(message("R5", "2026-10-01T00:00:00Z")),
                    chain(
                        message(
                            "R2",
                            "2026-09-02T09:00:00Z",
                            modified="2026-09-10T00:00:00Z",
                            text="<p>edited</p>",
                        ),
                        message("R2-a", "2026-09-11T00:00:00Z", reply_to="R2"),
                    ),
                    # inside the 5-minute skew window: re-read (idempotent), not a stop
                    chain(message("R6", "2026-09-09T23:57:00Z")),
                    # older than HWM - 5 min: the walk stops here
                    chain(message("R4", "2026-09-07T09:00:00Z", text="<p>must not be merged</p>")),
                    chain(message("R0", "2026-10-02T00:00:00Z")),  # after the stop: never read
                ],
                [chain(message("NEVER", "2026-10-03T00:00:00Z"))],
            ]
        },
    )
    result = arm_for(client, store).scan(stored, full=False)

    assert client.pages() == [LIST]  # the second page is never requested
    assert result.pass_kind is PassKind.DELTA and not result.enumeration_complete and not result.cursor_reset
    assert {i.stable_id for i in result.items} == {f"{CHANNEL}:2026-09", f"{CHANNEL}:2026-10"}
    sep = read_month(store, "2026-09")
    assert [m["id"] for m in sep["messages"]] == ["R2", "R3", "R4", "R6", "R2-a"]  # old messages kept, sorted
    assert sep["messages"][0]["body_html"] == "<p>edited</p>"
    assert next(m for m in sep["messages"] if m["id"] == "R4")["body_html"] != "<p>must not be merged</p>"
    assert [m["id"] for m in read_month(store, "2026-10")["messages"]] == ["R5"]
    assert (store / "2026-08.json").read_bytes() == aug_before  # untouched month not rewritten
    assert result.new_cursor == hwm("2026-10-01T00:00:00.000Z")


def test_walk_is_reply_chain_aware(tmp_path: Path) -> None:
    # The root is months older than the HWM, but a new reply makes the whole chain newer: not a stop.
    store = tmp_path / "s"
    client = FakeClient(
        json=names(),
        collections={
            LIST: [
                [
                    chain(
                        message("OLD", "2026-03-01T00:00:00Z"),
                        message("OLD-r", "2026-09-20T00:00:00Z", reply_to="OLD"),
                    ),
                    chain(message("STOP", "2026-01-01T00:00:00Z")),
                ]
            ]
        },
    )
    result = arm_for(client, store).scan(hwm("2026-09-01T00:00:00.000Z"), full=False)
    assert [i.name for i in result.items] == ["2026-03.teams.json"]  # the thread lives in its root's month
    assert [m["id"] for m in read_month(store, "2026-03")["messages"]] == ["OLD", "OLD-r"]
    assert result.new_cursor == hwm("2026-09-20T00:00:00.000Z")


def test_replies_behind_next_link_are_read_before_the_stop_decision(tmp_path: Path) -> None:
    more = f"{BASE}{LIST}/BIG/replies?$skiptoken=2"
    client = FakeClient(
        json=names(),
        collections={
            LIST: [[chain(message("BIG", "2026-01-01T00:00:00Z"), more=more)]],
            more: [[message("BIG-new", "2026-09-30T00:00:00Z", reply_to="BIG")]],
        },
    )
    result = arm_for(client, tmp_path / "s").scan(hwm("2026-09-01T00:00:00.000Z"), full=False)
    assert more in client.pages()
    assert [i.name for i in result.items] == ["2026-01.teams.json"]
    assert result.new_cursor == hwm("2026-09-30T00:00:00.000Z")


def test_high_water_mark_never_moves_backwards(tmp_path: Path) -> None:
    stored = hwm("2026-09-10T00:00:00.000Z")
    client = FakeClient(json=names(), collections={LIST: [[chain(message("R", "2026-01-01T00:00:00Z"))]]})
    result = arm_for(client, tmp_path / "s").scan(stored, full=False)
    assert result.items == () and result.new_cursor == stored


def test_full_flag_walks_everything_and_emits_every_stored_month(tmp_path: Path) -> None:
    store = tmp_path / "s"
    first = arm_for(full_client(), store).scan(None, full=False)
    client = FakeClient(json=names(), collections={LIST: [[chain(message("R9", "2026-10-01T00:00:00Z"))]]})
    result = arm_for(client, store).scan(first.new_cursor, full=True)
    assert result.pass_kind is PassKind.FULL and result.enumeration_complete
    assert [i.name for i in result.items] == [
        "2026-08.teams.json",
        "2026-09.teams.json",
        "2026-10.teams.json",
    ]


def test_legacy_delta_cursor_is_replaced_by_a_full_walk(tmp_path: Path) -> None:
    legacy = f"{BASE}{CH}/messages/de" + "lta?$deltatoken=OLD"  # an older build's cursor
    client = FakeClient(json=names(), collections={LIST: [[chain(message("R1", "2026-09-01T00:00:00Z"))]]})
    result = arm_for(client, tmp_path / "s").scan(legacy, full=False)
    assert result.pass_kind is PassKind.FULL and result.cursor_reset and result.enumeration_complete
    assert any("high-water" in a for a in result.alarms)
    assert result.new_cursor == hwm("2026-09-01T00:00:00.000Z")


def test_channel_list_errors_propagate(tmp_path: Path) -> None:
    client = FakeClient(
        json=names(), collections={LIST: GraphError(403, "Forbidden", "no ChannelMessage.Read.All")}
    )
    with pytest.raises(GraphError):
        arm_for(client, tmp_path / "s").scan(None, full=False)


# ---------------------------------------------------------------------------------------------------------
# chats: $orderby + $filter on lastModifiedDateTime (C15 section 9 item 13)
# ---------------------------------------------------------------------------------------------------------


def chat_names() -> dict[str, Any]:
    return {CHAT_PATH: {"topic": "Budget huddle", "chatType": "group"}}


def test_chat_bootstrap_orders_by_last_modified_without_a_filter(tmp_path: Path) -> None:
    store = tmp_path / "chat"
    client = FakeClient(
        json=chat_names(),
        collections={
            CHAT_LIST: [
                [message("C2", "2026-09-02T00:00:00Z"), message("C1", "2026-08-30T00:00:00Z")],
                [message("SYS", "2026-08-01T00:00:00Z", message_type="systemEventMessage")],
            ]
        },
    )
    result = arm_for(client, store, chat_cfg()).scan(None, full=False)
    first = next(c for c in client.calls if c[1] == CHAT_LIST)
    assert first[2] == {"$top": "50", "$orderby": "lastModifiedDateTime desc"}
    assert result.pass_kind is PassKind.FULL and result.enumeration_complete
    assert [i.stable_id for i in result.items] == [f"{CHAT}:2026-08", f"{CHAT}:2026-09"]
    doc = read_month(store, "2026-09")
    assert (doc["team_id"], doc["team_name"], doc["channel_id"], doc["channel_name"]) == (
        "chats",
        "Chats",
        CHAT,
        "Budget huddle",
    )
    assert result.new_cursor == hwm("2026-09-02T00:00:00.000Z")
    assert all("/teams/" not in c[1] for c in client.calls)


def test_chat_incremental_uses_orderby_and_filter_on_the_same_property(tmp_path: Path) -> None:
    client = FakeClient(json=chat_names(), collections={CHAT_LIST: [[message("C3", "2026-09-20T00:00:00Z")]]})
    result = arm_for(client, tmp_path / "c", chat_cfg()).scan(hwm("2026-09-10T00:00:00.000Z"), full=False)
    first = next(c for c in client.calls if c[1] == CHAT_LIST)
    assert first[2] == {
        "$top": "50",
        "$orderby": "lastModifiedDateTime desc",
        "$filter": "lastModifiedDateTime gt 2026-09-09T23:55:00.000Z",  # HWM - 5 min
    }
    assert result.pass_kind is PassKind.DELTA and not result.enumeration_complete
    assert [i.name for i in result.items] == ["2026-09.teams.json"]


def test_chat_walk_stops_client_side_when_the_filter_is_ignored(tmp_path: Path) -> None:
    client = FakeClient(
        json=chat_names(),
        collections={
            CHAT_LIST: [
                [message("NEW", "2026-09-20T00:00:00Z"), message("OLD", "2026-01-01T00:00:00Z")],
                [message("OLDER", "2025-12-01T00:00:00Z")],
            ]
        },
    )
    result = arm_for(client, tmp_path / "c", chat_cfg()).scan(hwm("2026-09-10T00:00:00.000Z"), full=False)
    assert client.pages() == [CHAT_LIST]
    assert [i.name for i in result.items] == ["2026-09.teams.json"]


def test_chat_reply_is_filed_under_its_roots_month(tmp_path: Path) -> None:
    store = tmp_path / "c"
    client = FakeClient(
        json={**chat_names(), f"{CHAT_LIST}/ROOT": {"id": "ROOT", "createdDateTime": "2026-07-15T00:00:00Z"}},
        collections={CHAT_LIST: [[message("RE", "2026-09-01T00:00:00Z", reply_to="ROOT")]]},
    )
    result = arm_for(client, store, chat_cfg()).scan(None, full=False)
    assert [i.name for i in result.items] == ["2026-07.teams.json"]
    assert [m["id"] for m in read_month(store, "2026-07")["messages"]] == ["RE"]


def test_chat_reply_to_vanished_root_is_skipped(tmp_path: Path) -> None:
    client = FakeClient(
        json={**chat_names(), f"{CHAT_LIST}/GONE": GraphNotFound(404, "NotFound", "gone")},
        collections={CHAT_LIST: [[message("RE", "2026-09-01T00:00:00Z", reply_to="GONE")]]},
    )
    result = arm_for(client, tmp_path / "c", chat_cfg()).scan(None, full=False)
    assert result.items == ()


# ---------------------------------------------------------------------------------------------------------
# pacing: at most one request per second per channel or chat (C15 section 9 item 14)
# ---------------------------------------------------------------------------------------------------------


def test_requests_are_paced_to_one_per_second_with_a_fake_clock(tmp_path: Path) -> None:
    client = full_client()
    arm_for(client, tmp_path / "s").scan(None, full=False)
    times = client.request_times
    assert len(times) >= 5  # team name, channel name, two list pages, one replies page
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert all(g >= 1.0 - 1e-9 for g in gaps), gaps
    assert sum(client.clock.sleeps) == pytest.approx(times[-1] - times[0])  # no sleep after the last request


def test_pacer_does_not_sleep_when_requests_are_already_spaced() -> None:
    fake = FakeClock()
    pacer = teams_module._Pacer(1.0, fake.clock, fake.sleep)
    pacer.wait()
    fake.now += 5
    pacer.wait()
    assert fake.sleeps == []
    pacer.wait()
    assert fake.sleeps == [1.0]


def test_teams_module_uses_no_delta_or_app_only_endpoint() -> None:
    text = Path(teams_module.__file__).read_text(encoding="utf-8")
    assert "messages/de" + "lta" not in text and "/de" + "lta'" not in text
    assert "getAll" + "Messages" not in text


# ---------------------------------------------------------------------------------------------------------
# names and the store
# ---------------------------------------------------------------------------------------------------------


def test_names_fall_back_when_graph_refuses(tmp_path: Path) -> None:
    store = tmp_path / "s"
    refused = GraphError(403, "Forbidden", "missing Team.ReadBasic.All")
    client = FakeClient(
        json={f"/teams/{TEAM}": refused, CH: refused},
        collections={LIST: [[chain(message("R1", "2026-09-01T00:00:00Z"))]]},
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
        collections={LIST: [[chain(message("R7", "2026-09-20T00:00:00Z"))]]},
    )
    arm_for(client, store).scan(hwm("2026-09-10T00:00:00.000Z"), full=False)
    doc = read_month(store, "2026-09")
    assert (doc["team_name"], doc["channel_name"]) == ("Finance", "General")


def test_corrupt_month_is_set_aside_and_rebuilt(tmp_path: Path) -> None:
    store = tmp_path / "s"
    store.mkdir()
    (store / "2026-09.json").write_text("{not json")
    client = FakeClient(json=names(), collections={LIST: [[chain(message("R1", "2026-09-01T00:00:00Z"))]]})
    result = arm_for(client, store).scan(hwm("2026-08-01T00:00:00.000Z"), full=False)
    assert (store / "2026-09.json.corrupt").read_text() == "{not json"
    assert [m["id"] for m in read_month(store, "2026-09")["messages"]] == ["R1"]
    assert any("unreadable" in a for a in result.alarms)


def test_foreign_channel_month_is_replaced_and_not_emitted(tmp_path: Path) -> None:
    store = tmp_path / "s"
    store.mkdir()
    foreign = {"schema": TEAMS_MONTH_SCHEMA, "channel_id": "19:other", "messages": [], "month": "2026-05"}
    (store / "2026-05.json").write_text(json.dumps(foreign))
    (store / "2026-09.json").write_text(json.dumps({**foreign, "month": "2026-09"}))
    client = FakeClient(json=names(), collections={LIST: [[chain(message("R1", "2026-09-01T00:00:00Z"))]]})
    result = arm_for(client, store).scan(None, full=True)
    assert [i.name for i in result.items] == ["2026-09.teams.json"]  # the foreign May file is ignored
    assert read_month(store, "2026-09")["channel_id"] == CHANNEL
    assert any("another channel" in a for a in result.alarms)


def test_unreadable_untouched_month_is_alarmed_on_full(tmp_path: Path) -> None:
    store = tmp_path / "s"
    store.mkdir()
    (store / "2026-01.json").write_text("{broken")
    client = FakeClient(json=names(), collections={LIST: [[chain(message("R1", "2026-09-01T00:00:00Z"))]]})
    result = arm_for(client, store).scan(None, full=True)
    assert [i.name for i in result.items] == ["2026-09.teams.json"]
    assert any("2026-01" in a and "not emitted" in a for a in result.alarms)


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
        )
    with pytest.raises(ValueError):
        arm.fetch(
            SourceItem(source_id="chan", stable_id=f"{CHANNEL}:../../x", **base), tmp_path, ByteBudget(9, 9)
        )
    with pytest.raises(FileNotFoundError):
        arm.fetch(
            SourceItem(source_id="chan", stable_id=f"{CHANNEL}:2026-01", **base), tmp_path, ByteBudget(9, 9)
        )


def test_arm_validates_its_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        TeamsArm(FakeClient(), SourceConfig(id="c", kind=SourceKind.GRAPH_TEAMS, team_id=TEAM), tmp_path)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TeamsArm(FakeClient(), SourceConfig(id="m", kind=SourceKind.GRAPH_MAIL, folder="Inbox"), tmp_path)  # type: ignore[arg-type]
    arm = arm_for(FakeClient(), tmp_path)
    assert arm.source_id == "chan" and arm.kind is SourceKind.GRAPH_TEAMS and not arm.is_chat
    assert arm_for(FakeClient(), tmp_path, chat_cfg()).is_chat
