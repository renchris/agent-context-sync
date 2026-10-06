"""Graph drive delta arm: mapping, FULL/DELTA state machine, 410/400 recovery, scope filter, fetch, discover.

The GraphClient is a scripted fake of the contract (graph-core builds the real one in parallel).
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentsync.config import SourceConfig
from agentsync.errors import (
    AuthRequiredError,
    BudgetExhaustedError,
    GraphBadCursor,
    GraphError,
    GraphGone,
    GraphNotFound,
)
from agentsync.graph.client import DeltaResult, GraphPage
from agentsync.graph.drive import (
    DELTA_HEADERS,
    DRIVE_SELECT,
    DriveArm,
    _parse_graph_time_ns,
    discover,
    item_from_graph,
    resolve_drive_id,
)
from agentsync.model import ByteBudget, PassKind, SourceItem, SourceKind

BASE = "https://graph.microsoft.com/v1.0"
DELTA_PATH = "/drives/D/root/delta"


# ---------------------------------------------------------------------------------------------------------
# Scripted fake GraphClient
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Round:
    """One scripted delta round: pages of items, then a deltaLink."""

    pages: list[list[dict[str, Any]]]
    delta_link: str = f"{BASE}/drives/D/root/delta?token=NEXT"


@dataclass
class FakeClient:
    json: dict[str, Any] = field(default_factory=dict)  # path -> body or Exception
    collections: dict[str, list[list[dict[str, Any]]] | Exception] = field(default_factory=dict)
    deltas: dict[str, list[Round | Exception]] = field(default_factory=dict)  # consumed in order
    downloads: dict[str, bytes | Exception] = field(default_factory=dict)
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
        pages = self.collections[path]
        if isinstance(pages, Exception):
            raise pages
        for i, page in enumerate(pages):
            nxt = f"{BASE}{path}?$skiptoken={i + 1}" if i + 1 < len(pages) else None
            yield GraphPage(value=tuple(page), next_link=nxt, delta_link=None)

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
        items: list[dict[str, Any]] = []
        for i, page in enumerate(step.pages):
            last = i + 1 == len(step.pages)
            gp = GraphPage(
                value=tuple(page),
                next_link=None if last else f"{BASE}/drives/D/root/delta?$skiptoken=PAGE{i + 1}",
                delta_link=step.delta_link if last else None,
            )
            if on_page is not None:
                on_page(gp)
            items.extend(page)
        return DeltaResult(items=tuple(items), delta_link=step.delta_link, pages=len(step.pages))

    def download(self, path: str, dest: Path, *, max_bytes: int | None = None) -> tuple[int, str]:
        self.calls.append(("download", path, {"max_bytes": str(max_bytes)}, None))
        data = self.downloads[path]
        if isinstance(data, Exception):
            raise data
        if max_bytes is not None and len(data) > max_bytes:
            raise BudgetExhaustedError(f"{len(data)} > {max_bytes}")
        dest.write_bytes(data)
        return len(data), hashlib.sha256(data).hexdigest()

    def called(self, method: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == method]


# ---------------------------------------------------------------------------------------------------------
# driveItem builders
# ---------------------------------------------------------------------------------------------------------


def root(ident: str = "ROOT") -> dict[str, Any]:
    return {"id": ident, "name": "root", "root": {}, "folder": {"childCount": 2}}


def folder(ident: str, name: str, parent: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "name": name,
        "folder": {"childCount": 1},
        "size": 999,
        "eTag": f'"{{{ident}}},1"',
        "cTag": f'"c:{{{ident}}},9"',
        "parentReference": {"id": parent, "driveId": "D"},
        "lastModifiedDateTime": "2026-09-01T10:00:00Z",
        **extra,
    }


def file(
    ident: str, name: str, parent: str, *, qx: str | None = "QX==", size: int = 10, **extra: Any
) -> dict[str, Any]:
    facet: dict[str, Any] = {"mimeType": "application/octet-stream"}
    if qx is not None:
        facet["hashes"] = {"quickXorHash": qx}
    return {
        "id": ident,
        "name": name,
        "file": facet,
        "size": size,
        "eTag": f'"{{{ident}}},2"',
        "cTag": f'"c:{{{ident}}},2"',
        "parentReference": {"id": parent, "driveId": "D"},
        "lastModifiedDateTime": "2026-09-02T11:22:33.1234567Z",
        "createdDateTime": "2026-01-01T00:00:00Z",
        "webUrl": f"https://contoso.sharepoint.com/{name}",
        **extra,
    }


def deleted(ident: str, parent: str | None = None) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": ident, "deleted": {"state": "deleted"}}
    if parent:
        raw["parentReference"] = {"id": parent}
    return raw


def cfg(folder_scope: str = "/", **kw: Any) -> SourceConfig:
    return SourceConfig(id="fin", kind=SourceKind.GRAPH_DRIVE, drive_id="D", folder=folder_scope, **kw)


def lookup_from(rows: Mapping[str, tuple[str | None, str]]) -> Callable[[str], tuple[str | None, str] | None]:
    return rows.get


def by_id(items: tuple[SourceItem, ...]) -> dict[str, SourceItem]:
    return {i.stable_id: i for i in items}


# ---------------------------------------------------------------------------------------------------------
# time parsing
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1970-01-01T00:00:00Z", 0),
        ("1970-01-01T00:00:01.5Z", 1_500_000_000),
        ("1970-01-01T00:00:00.1234567Z", 123_456_700),  # Graph's 7 fractional digits
        ("1970-01-01T01:00:00+01:00", 0),
        ("1970-01-01T00:00:00-0030", 1_800_000_000_000),
        ("1970-01-01T00:00:00", 0),  # no offset = UTC
    ],
)
def test_parse_graph_time(text: str, expected: int) -> None:
    assert _parse_graph_time_ns(text) == expected


@pytest.mark.parametrize("bad", [None, 5, "", "yesterday", "2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z"])
def test_parse_graph_time_rejects(bad: object) -> None:
    assert _parse_graph_time_ns(bad) is None


# ---------------------------------------------------------------------------------------------------------
# item_from_graph
# ---------------------------------------------------------------------------------------------------------


def test_item_from_graph_file_fields() -> None:
    rows = {"F1": ("ROOT", "Finance"), "ROOT": (None, "")}
    raw = file(
        "I1",
        "Budget.xlsx",
        "F1",
        publication={"level": "checkout"},
        lastModifiedBy={"user": {"displayName": "Dana Q"}},
        pendingOperations={"pendingContentUpdate": {"queuedDateTime": "2026-09-02T11:22:00Z"}},
        malware={"description": "Trojan:Win32/Test"},
    )
    raw["file"]["hashes"].update({"sha1Hash": "ABC", "sha256Hash": "DEF"})
    item = item_from_graph("fin", raw, lookup_from(rows))
    assert item.stable_id == "I1"
    assert item.rel_path == "Finance/Budget.xlsx"
    assert item.name == "Budget.xlsx"
    assert item.parent_id == "F1"
    assert item.size == 10
    assert not item.is_dir and not item.deleted
    assert item.remote_hashes.quickxor == "QX=="
    assert item.remote_hashes.sha1 == "ABC" and item.remote_hashes.sha256 == "DEF"
    assert item.etag == '"{I1},2"' and item.ctag == '"c:{I1},2"'
    assert item.mtime_ns == item.ctime_ns == _parse_graph_time_ns("2026-09-02T11:22:33.1234567Z")
    assert item.created_ns == _parse_graph_time_ns("2026-01-01T00:00:00Z")
    assert item.content_type == "application/octet-stream"
    assert item.extra["web_url"] == "https://contoso.sharepoint.com/Budget.xlsx"
    assert item.extra["publication_level"] == "checkout"
    assert "sensitivity_label" not in item.extra  # driveItem has no label property (C15 section 4)
    assert item.extra["last_modified_by"] == "Dana Q"
    assert item.extra["pending_operations"] == "pendingContentUpdate"
    assert item.extra["malware"] == "Trojan:Win32/Test"
    assert item.extra["drive_id"] == "D"
    assert "hash_absent" not in item.extra


def test_item_from_graph_folder_drops_volatile_signals() -> None:
    item = item_from_graph("fin", folder("F1", "Finance", "ROOT"), lookup_from({"ROOT": (None, "")}))
    assert item.is_dir
    assert item.size == 0  # a folder's size is its descendants' sum: never a signal
    assert item.ctag is None  # folder cTags move on any descendant change
    assert item.etag == '"{F1},1"'
    assert item.rel_path == "Finance"


def test_item_from_graph_package_is_a_directory() -> None:
    raw = {"id": "NB", "name": "Notes", "package": {"type": "oneNote"}, "parentReference": {"id": "ROOT"}}
    item = item_from_graph("fin", raw, lookup_from({"ROOT": (None, "")}))
    assert item.is_dir
    assert item.extra["package_type"] == "oneNote"


def test_item_from_graph_hash_absent_is_flagged() -> None:
    item = item_from_graph("fin", file("I1", "a.one", "ROOT", qx=None), lookup_from({"ROOT": (None, "")}))
    assert item.remote_hashes.empty
    assert item.extra["hash_absent"] is True


def test_item_from_graph_nfc_normalises_names_and_paths() -> None:
    nfd = unicodedata.normalize("NFD", "Resumé.docx")
    rows = {"F1": ("ROOT", unicodedata.normalize("NFD", "Café")), "ROOT": (None, "")}
    item = item_from_graph("fin", file("I1", nfd, "F1"), lookup_from(rows))
    assert item.name == "Resumé.docx"
    assert item.rel_path == "Café/Resumé.docx"
    assert unicodedata.is_normalized("NFC", item.rel_path)


def test_item_from_graph_underivable_path_is_empty() -> None:
    item = item_from_graph("fin", file("I1", "a.txt", "UNKNOWN"), lookup_from({}))
    assert item.rel_path == ""


def test_item_from_graph_scope_root_makes_paths_relative() -> None:
    rows = {"SCOPE": ("ROOT", "Shared"), "F1": ("SCOPE", "Q3"), "ROOT": (None, "")}
    item = item_from_graph("fin", file("I1", "a.txt", "F1"), lookup_from(rows), root_id="SCOPE")
    assert item.rel_path == "Q3/a.txt"
    outside = item_from_graph("fin", file("I2", "b.txt", "ROOT"), lookup_from(rows), root_id="SCOPE")
    assert outside.rel_path == ""


def test_item_from_graph_deleted_without_name_uses_tree() -> None:
    rows = {"I1": ("F1", "old.docx"), "F1": ("ROOT", "Docs"), "ROOT": (None, "")}
    item = item_from_graph("fin", deleted("I1"), lookup_from(rows))
    assert item.deleted
    assert item.name == "old.docx"
    assert item.rel_path == "Docs/old.docx"


def test_item_from_graph_parent_cycle_is_underivable() -> None:
    rows = {"A": ("B", "a"), "B": ("A", "b")}
    item = item_from_graph("fin", file("I1", "x.txt", "A"), lookup_from(rows))
    assert item.rel_path == ""


def test_item_from_graph_requires_id() -> None:
    with pytest.raises(ValueError):
        item_from_graph("fin", {"name": "x"}, lookup_from({}))


# ---------------------------------------------------------------------------------------------------------
# resolve_drive_id
# ---------------------------------------------------------------------------------------------------------


def test_resolve_literal_drive_id_makes_no_call() -> None:
    client = FakeClient()
    assert resolve_drive_id(client, cfg()) == "D"  # type: ignore[arg-type]
    assert client.calls == []


def test_resolve_me() -> None:
    client = FakeClient(json={"/me/drive": {"id": "MYDRIVE"}})
    source = SourceConfig(id="me", kind=SourceKind.GRAPH_DRIVE, drive_id="me", folder="/")
    assert resolve_drive_id(client, source) == "MYDRIVE"  # type: ignore[arg-type]


def test_resolve_site_path_then_default_library() -> None:
    site_id = "contoso.sharepoint.com,11111111-1111,22222222-2222"
    client = FakeClient(
        json={
            "/sites/contoso.sharepoint.com:/sites/finance": {"id": site_id},
            f"/sites/{site_id}/drive": {"id": "b!LIB"},
        }
    )
    source = SourceConfig(
        id="fin", kind=SourceKind.GRAPH_DRIVE, site="contoso.sharepoint.com:/sites/finance", folder="/"
    )
    assert resolve_drive_id(client, source) == "b!LIB"  # type: ignore[arg-type]


def test_resolve_site_id_form() -> None:
    client = FakeClient(json={"/sites/root": {"id": "SITE"}, "/sites/SITE/drive": {"id": "LIB"}})
    source = SourceConfig(id="fin", kind=SourceKind.GRAPH_DRIVE, site="root", folder="/")
    assert resolve_drive_id(client, source) == "LIB"  # type: ignore[arg-type]


def test_resolve_missing_id_is_an_error() -> None:
    client = FakeClient(json={"/me/drive": {}})
    source = SourceConfig(id="me", kind=SourceKind.GRAPH_DRIVE, drive_id="me", folder="/")
    with pytest.raises(GraphError):
        resolve_drive_id(client, source)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------
# FULL enumeration
# ---------------------------------------------------------------------------------------------------------


def full_tree() -> list[list[dict[str, Any]]]:
    return [
        [root(), folder("F1", "Finance", "ROOT"), file("I1", "Budget.xlsx", "F1")],
        [file("I2", "notes.txt", "ROOT"), file("I1", "Budget v2.xlsx", "F1", qx="NEW==")],  # I1 repeats
    ]


def test_first_scan_is_a_tokenless_full_enumeration() -> None:
    client = FakeClient(deltas={DELTA_PATH: [Round(full_tree())]})
    saved: list[str | None] = []
    arm = DriveArm(client, cfg(), lookup_from({}), save_page_link=saved.append)  # type: ignore[arg-type]
    result = arm.scan(None, full=False)

    _, path, params, headers = next(c for c in client.calls if c[0] == "delta")
    assert path == DELTA_PATH  # never token=latest
    assert params == {"$select": DRIVE_SELECT}
    # deltaExcludeParent is a request header of its own, never a Prefer value (audit design-correctness-17)
    assert headers == {"deltaExcludeParent": "true"}
    assert "Prefer" not in headers
    assert DELTA_HEADERS == {"deltaExcludeParent": "true"}  # the module constant is never mutated

    assert result.pass_kind is PassKind.FULL
    assert result.enumeration_complete
    assert not result.cursor_reset
    assert result.new_cursor == f"{BASE}/drives/D/root/delta?token=NEXT"
    items = by_id(result.items)
    assert set(items) == {"F1", "I1", "I2"}  # the root itself is not an item
    assert items["F1"].is_dir and items["F1"].rel_path == "Finance"
    assert items["I1"].rel_path == "Finance/Budget v2.xlsx"  # last occurrence wins
    assert items["I1"].remote_hashes.quickxor == "NEW=="
    assert items["I2"].rel_path == "notes.txt"
    assert [i.rel_path for i in result.items] == sorted(i.rel_path for i in result.items)
    # the page cursor of the in-flight enumeration was persisted; the deltaLink was not
    assert saved == [f"{BASE}/drives/D/root/delta?$skiptoken=PAGE1"]
    # the root came in the batch, so no extra GET /root was needed
    assert client.called("get_json") == []


def test_full_flag_ignores_the_cursor() -> None:
    client = FakeClient(deltas={DELTA_PATH: [Round(full_tree())]})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    result = arm.scan("https://graph.microsoft.com/v1.0/drives/D/root/delta?token=OLD", full=True)
    assert result.pass_kind is PassKind.FULL and result.enumeration_complete
    assert client.called("delta") == [DELTA_PATH]


def test_full_scan_with_folder_scope_filters_and_relativises() -> None:
    pages = [
        [
            root(),
            folder("S", "Shared", "ROOT"),
            folder("Q", "Q3", "S"),
            file("IN", "in.docx", "Q"),
            file("OUT", "out.docx", "ROOT"),
            folder("O", "Other", "ROOT"),
            file("OUT2", "x.docx", "O"),
        ]
    ]
    client = FakeClient(
        json={"/drives/D/root:/Shared": {"id": "S", "name": "Shared"}},
        deltas={DELTA_PATH: [Round(pages)]},
    )
    arm = DriveArm(client, cfg("/Shared"), lookup_from({}))  # type: ignore[arg-type]
    result = arm.scan(None, full=False)
    items = by_id(result.items)
    assert set(items) == {"Q", "IN"}  # the scope folder itself is not an item
    assert items["IN"].rel_path == "Q3/in.docx"
    assert arm.scope_root() == "S"
    assert result.enumeration_complete


def test_scope_root_is_none_for_the_whole_drive() -> None:
    arm = DriveArm(FakeClient(), cfg(), lookup_from({}))  # type: ignore[arg-type]
    assert arm.scope_root() is None


def test_missing_scope_folder_is_a_clear_error() -> None:
    client = FakeClient(
        json={"/drives/D/root:/Gone": GraphNotFound(404, "itemNotFound", "nope")},
        deltas={DELTA_PATH: [Round([[root()]])]},
    )
    arm = DriveArm(client, cfg("/Gone"), lookup_from({}))  # type: ignore[arg-type]
    with pytest.raises(GraphNotFound, match="does not exist"):
        arm.scan(None, full=False)


def test_full_scan_applies_include_exclude_to_files_only() -> None:
    pages = [
        [root(), folder("F1", "tmp", "ROOT"), file("L", "~$Budget.xlsx", "F1"), file("K", "keep.md", "F1")]
    ]
    client = FakeClient(deltas={DELTA_PATH: [Round(pages)]})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    items = by_id(arm.scan(None, full=False).items)
    assert set(items) == {"F1", "K"}  # Office lock file excluded by the default globs; folder kept


def test_in_scope_keeps_exactly_the_files_the_scan_keeps() -> None:
    files = {"L": "~$Budget.xlsx", "K": "keep.md", "D": "draft.md", "P": "plan.pdf"}
    pages = [[root(), folder("F1", "tmp", "ROOT"), *(file(i, name, "F1") for i, name in files.items())]]
    client = FakeClient(deltas={DELTA_PATH: [Round(pages)]})
    # "tmp" names the folder: this arm prunes no folder, so the files under it stay in scope
    scoped = cfg(include=("*.md", "*.xlsx"), exclude=("~$*", "draft.*", "tmp"))
    arm = DriveArm(client, scoped, lookup_from({}))  # type: ignore[arg-type]
    listed = {i.rel_path for i in arm.scan(None, full=False).items if not i.is_dir}
    assert listed == {"tmp/keep.md"}
    assert {rel for rel in (f"tmp/{name}" for name in files.values()) if arm.in_scope(rel)} == listed


def test_remote_items_are_reported_not_emitted() -> None:
    shortcut = folder(
        "SC", "Team Share", "ROOT", remoteItem={"id": "X", "parentReference": {"driveId": "OTHER"}}
    )
    client = FakeClient(deltas={DELTA_PATH: [Round([[root(), shortcut]])]})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    result = arm.scan(None, full=False)
    assert result.items == ()
    assert result.unknown_dirs == ("Team Share",)
    assert any("remoteItem" in a for a in result.alarms)


def test_resumed_enumeration_is_never_complete() -> None:
    resume = f"{BASE}/drives/D/root/delta?$skiptoken=PAGE7"
    client = FakeClient(deltas={resume: [Round([[file("I2", "late.txt", "ROOT")]])]})
    arm = DriveArm(
        client,  # type: ignore[arg-type]
        cfg(),
        lookup_from({}),
        resume_link=resume,
    )
    client.json["/drives/D/root"] = {"id": "ROOT"}
    result = arm.scan(None, full=False)
    assert client.called("delta") == [resume]
    assert result.pass_kind is PassKind.FULL
    assert not result.enumeration_complete  # earlier pages' items are missing: absence proves nothing
    assert result.new_cursor is None  # never staged: the next cycle enumerates again from the start
    assert any("resumed" in a for a in result.alarms)
    assert by_id(result.items)["I2"].rel_path == "late.txt"


# ---------------------------------------------------------------------------------------------------------
# DELTA passes
# ---------------------------------------------------------------------------------------------------------

CURSOR = f"{BASE}/drives/D/root/delta?token=CUR"
MANIFEST = {
    "F1": ("ROOT", "Finance"),
    "I1": ("F1", "Budget.xlsx"),
    "I9": ("F1", "Old.docx"),
}


def delta_client(pages: list[list[dict[str, Any]]], **json: Any) -> FakeClient:
    return FakeClient(json={"/drives/D/root": {"id": "ROOT"}, **json}, deltas={CURSOR: [Round(pages)]})


def test_delta_pass_uses_the_stored_link_verbatim() -> None:
    client = delta_client([[file("I1", "Budget.xlsx", "F1", qx="CHANGED==")]])
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    _, path, params, headers = next(c for c in client.calls if c[0] == "delta")
    assert path == CURSOR and params is None and headers is not None
    assert result.pass_kind is PassKind.DELTA
    assert not result.enumeration_complete  # a DELTA pass never enables absence-based deletion
    assert result.new_cursor == f"{BASE}/drives/D/root/delta?token=NEXT"
    item = by_id(result.items)["I1"]
    assert item.rel_path == "Finance/Budget.xlsx"  # parent resolved from the manifest tree
    assert item.remote_hashes.quickxor == "CHANGED=="


def test_delta_deletes_only_known_items_and_keeps_their_path() -> None:
    client = delta_client([[deleted("I9"), deleted("NEVER-SEEN")]])
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert set(items) == {"I9"}
    assert items["I9"].deleted
    assert items["I9"].rel_path == "Finance/Old.docx"
    assert items["I9"].extra["removed"] == "deleted"


def test_delta_folder_rename_emits_the_folder_only() -> None:
    client = delta_client([[folder("F1", "Finance FY26", "ROOT")]])
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert set(items) == {"F1"}  # descendants are re-derived by Manifest.rederive_paths
    assert items["F1"].rel_path == "Finance FY26"
    assert client.called("iter_pages") == []  # a known folder is never re-listed


def test_delta_child_of_renamed_folder_uses_the_new_name() -> None:
    client = delta_client([[folder("F1", "Renamed", "ROOT"), file("I1", "Budget.xlsx", "F1", qx="N==")]])
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert items["I1"].rel_path == "Renamed/Budget.xlsx"


def test_delta_move_out_of_scope_is_an_explicit_delete() -> None:
    rows = {"S": ("ROOT", "Shared"), "I1": ("S", "a.docx"), "O": ("ROOT", "Other")}
    client = FakeClient(
        json={"/drives/D/root:/Shared": {"id": "S"}, "/drives/D/root": {"id": "ROOT"}},
        deltas={CURSOR: [Round([[file("I1", "a.docx", "O")]])]},
    )
    arm = DriveArm(client, cfg("/Shared"), lookup_from(rows))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert items["I1"].deleted
    assert items["I1"].extra["removed"] == "moved-out-of-scope"
    assert items["I1"].rel_path == "a.docx"  # the last in-scope path


def test_full_pass_tombstones_moved_out_items_as_moved() -> None:
    """correctness-folder-move: a FULL listing that shows a known id alive outside the scope is positive
    evidence of a move, never an absence that would read as an upstream deletion (and queue a purge)."""
    rows = {"S": ("ROOT", "Shared"), "I1": ("S", "a.docx")}
    pages = [[root(), folder("S", "Shared", "ROOT"), folder("O", "Other", "ROOT"), file("I1", "a.docx", "O")]]
    client = FakeClient(json={"/drives/D/root:/Shared": {"id": "S"}}, deltas={DELTA_PATH: [Round(pages)]})
    arm = DriveArm(client, cfg("/Shared"), lookup_from(rows))  # type: ignore[arg-type]
    result = arm.scan(None, full=True)
    [item] = result.items
    assert item.stable_id == "I1" and item.deleted and item.extra["removed"] == "moved-out-of-scope"
    assert result.enumeration_complete


def test_known_item_under_an_unreadable_ancestor_is_left_alone() -> None:
    """correctness-underivable-known-item-tombstoned: no evidence, no removal."""
    client = delta_client(
        [[file("I1", "Budget.xlsx", "SECRET")]],
        **{"/drives/D/items/SECRET": GraphError(403, "accessDenied", "item-level permissions")},
    )
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert result.items == ()
    assert any("cannot be derived" in a for a in result.alarms)


def test_delta_newly_excluded_known_file_is_tombstoned() -> None:
    rows = {**MANIFEST, "I5": ("F1", "draft.docx")}
    client = delta_client([[file("I5", "draft.tmp", "F1")]])
    arm = DriveArm(client, cfg(), lookup_from(rows))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert items["I5"].deleted and items["I5"].extra["removed"] == "excluded"


def test_delta_folder_moved_in_lists_its_descendants() -> None:
    client = delta_client(
        [[folder("NEW", "Imported", "F1")]],
    )
    client.collections["/drives/D/items/NEW/children"] = [
        [file("C1", "one.docx", "NEW"), folder("SUB", "Sub", "NEW")],
    ]
    client.collections["/drives/D/items/SUB/children"] = [[file("C2", "two.pdf", "SUB")]]
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert items["NEW"].rel_path == "Finance/Imported"
    assert items["C1"].rel_path == "Finance/Imported/one.docx"
    assert items["SUB"].rel_path == "Finance/Imported/Sub"
    assert items["C2"].rel_path == "Finance/Imported/Sub/two.pdf"
    _, _, params, _ = next(c for c in client.calls if c[1] == "/drives/D/items/NEW/children")
    assert params == {"$select": DRIVE_SELECT}


def test_delta_unknown_parent_is_fetched_once() -> None:
    client = delta_client(
        [[file("I7", "deep.txt", "P2")]],
        **{
            "/drives/D/items/P2": folder("P2", "Inner", "P1"),
            "/drives/D/items/P1": folder("P1", "Outer", "ROOT"),
        },
    )
    client.collections["/drives/D/items/P2/children"] = [[file("I7", "deep.txt", "P2")]]
    client.collections["/drives/D/items/P1/children"] = [[folder("P2", "Inner", "P1")]]
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    items = by_id(arm.scan(CURSOR, full=False).items)
    assert items["I7"].rel_path == "Outer/Inner/deep.txt"
    assert items["P1"].is_dir and items["P2"].is_dir  # the id tree is completed for the manifest
    assert client.called("get_json").count("/drives/D/items/P2") == 1


def test_delta_outside_scope_hint_spares_ancestor_lookups() -> None:
    raw = file("X", "elsewhere.txt", "UNKNOWN")
    raw["parentReference"]["path"] = "/drives/D/root:/Somewhere/Else"
    client = FakeClient(
        json={"/drives/D/root:/Shared": {"id": "S"}},
        deltas={CURSOR: [Round([[raw]])]},
    )
    arm = DriveArm(client, cfg("/Shared"), lookup_from({}))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert result.items == ()
    assert "/drives/D/items/UNKNOWN" not in client.called("get_json")


# ---------------------------------------------------------------------------------------------------------
# 410 / 400 recovery
# ---------------------------------------------------------------------------------------------------------


def test_410_follows_location_as_a_full_reenumeration() -> None:
    location = f"{BASE}/drives/D/root/delta?token=RESYNC"
    client = FakeClient(
        deltas={
            CURSOR: [GraphGone("resyncChangesApplyDifferences", "gone", location)],
            location: [Round([[root(), file("I1", "a.txt", "ROOT")]])],
        }
    )
    saved: list[str | None] = []
    arm = DriveArm(client, cfg(), lookup_from({}), save_page_link=saved.append)  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert client.called("delta") == [CURSOR, location]
    assert result.pass_kind is PassKind.FULL
    assert result.cursor_reset
    assert result.enumeration_complete
    assert any("410" in a for a in result.alarms)
    assert saved[0] is None  # the stale page link is cleared when the round restarts


def test_410_without_location_restarts_tokenless() -> None:
    client = FakeClient(
        deltas={
            CURSOR: [GraphGone("resyncChangesUploadDifferences", "gone", None)],
            DELTA_PATH: [Round([[root()]])],
        }
    )
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert client.called("delta") == [CURSOR, DELTA_PATH]
    assert result.cursor_reset and result.enumeration_complete


def test_400_bad_cursor_alarms_drops_and_starts_over() -> None:
    client = FakeClient(
        deltas={
            CURSOR: [GraphBadCursor(400, "invalidRequest", "Provided sync token is malformed")],
            DELTA_PATH: [Round([[root(), file("I1", "a.txt", "ROOT")]])],
        }
    )
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert "cursor store corrupt: dropped" in result.alarms
    assert result.cursor_reset and result.pass_kind is PassKind.FULL and result.enumeration_complete


def test_400_on_a_tokenless_round_is_not_retried() -> None:
    client = FakeClient(deltas={DELTA_PATH: [GraphBadCursor(400, "invalidRequest", "odd")]})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    with pytest.raises(GraphBadCursor):
        arm.scan(None, full=False)
    assert client.called("delta") == [DELTA_PATH]


def test_repeated_410_never_loops() -> None:
    client = FakeClient(
        deltas={
            CURSOR: [GraphGone("resync", "gone", None)],
            DELTA_PATH: [GraphGone("resync", "gone", None)],
        }
    )
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    with pytest.raises(GraphError, match="kept failing"):
        arm.scan(CURSOR, full=False)
    assert len(client.called("delta")) == 3


def test_rejected_resume_link_falls_back_to_tokenless() -> None:
    resume = f"{BASE}/drives/D/root/delta?$skiptoken=STALE"
    client = FakeClient(
        deltas={
            resume: [GraphBadCursor(400, "invalidRequest", "bad")],
            DELTA_PATH: [Round([[root()]])],
        }
    )
    arm = DriveArm(client, cfg(), lookup_from({}), resume_link=resume)  # type: ignore[arg-type]
    result = arm.scan(None, full=False)
    assert result.enumeration_complete  # a clean token-less pass after the drop
    assert any("resume link" in a for a in result.alarms)


def test_auth_errors_propagate_untouched() -> None:
    client = FakeClient(deltas={CURSOR: [AuthRequiredError("sign in again")]})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    with pytest.raises(AuthRequiredError):
        arm.scan(CURSOR, full=False)


def test_cursor_values_never_reach_the_log(caplog: pytest.LogCaptureFixture) -> None:
    secret = f"{BASE}/drives/D/root/delta?token=SECRET-TOKEN-VALUE"
    location = f"{BASE}/drives/D/root/delta?token=SECRET-LOCATION"
    client = FakeClient(
        deltas={
            secret: [GraphGone("resyncChangesApplyDifferences", "gone", location)],
            location: [Round([[root()]], delta_link=f"{BASE}/drives/D/root/delta?token=SECRET-NEW")],
        }
    )
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    with caplog.at_level(logging.DEBUG, logger="agentsync"):
        result = arm.scan(secret, full=False)
    assert "SECRET" not in caplog.text
    assert not any("SECRET" in a for a in result.alarms)


# ---------------------------------------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------------------------------------


def a_file_item(size: int = 5) -> SourceItem:
    return SourceItem(
        source_id="fin",
        stable_id="I1",
        rel_path="Finance/Budget.xlsx",
        name="Budget.xlsx",
        size=size,
        mtime_ns=1,
        ctime_ns=1,
    )


def test_fetch_downloads_content_and_charges_the_budget(tmp_path: Path) -> None:
    client = FakeClient(downloads={"/drives/D/items/I1/content": b"hello"})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=100, max_files=10)
    got = arm.fetch(a_file_item(), tmp_path / "stage", budget)
    assert got.path.read_bytes() == b"hello"
    assert got.path.suffix == ".xlsx"  # converters route by suffix
    assert got.path.parent == tmp_path / "stage"
    assert got.size == 5
    assert got.content_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert budget.used == 5 and budget.files_used == 1


def test_fetch_accounts_for_served_bytes_differing_from_listed_size(tmp_path: Path) -> None:
    client = FakeClient(downloads={"/drives/D/items/I1/content": b"enriched-bytes"})  # 14 bytes, listed 5
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=100, max_files=10)
    got = arm.fetch(a_file_item(), tmp_path, budget)
    assert got.size == 14 and budget.used == 14
    _, _, params, _ = next(c for c in client.calls if c[0] == "download")
    assert params == {"max_bytes": "100"}


def test_fetch_over_budget_moves_no_bytes(tmp_path: Path) -> None:
    client = FakeClient()
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=4, max_files=10)
    with pytest.raises(BudgetExhaustedError):
        arm.fetch(a_file_item(size=5), tmp_path, budget)
    assert client.called("download") == []
    assert budget.used == 0 and budget.files_used == 0


def test_fetch_failure_refunds_the_budget(tmp_path: Path) -> None:
    client = FakeClient(downloads={"/drives/D/items/I1/content": GraphNotFound(404, "itemNotFound", "gone")})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=100, max_files=10)
    with pytest.raises(GraphNotFound):
        arm.fetch(a_file_item(), tmp_path, budget)
    assert budget.used == 0 and budget.files_used == 0


def test_fetch_refuses_directories_and_tombstones(tmp_path: Path) -> None:
    arm = DriveArm(FakeClient(), cfg(), lookup_from({}))  # type: ignore[arg-type]
    base = a_file_item()
    for bad in (
        SourceItem(
            source_id="fin",
            stable_id="F",
            rel_path="d",
            name="d",
            size=0,
            mtime_ns=0,
            ctime_ns=0,
            is_dir=True,
        ),
        SourceItem(
            source_id="fin",
            stable_id="X",
            rel_path="x",
            name="x",
            size=0,
            mtime_ns=0,
            ctime_ns=0,
            deleted=True,
        ),
        SourceItem(
            source_id="other",
            stable_id=base.stable_id,
            rel_path="a",
            name="a",
            size=1,
            mtime_ns=0,
            ctime_ns=0,
        ),
    ):
        with pytest.raises(ValueError):
            arm.fetch(bad, tmp_path, ByteBudget(100, 10))


def test_arm_rejects_other_kinds() -> None:
    wrong = SourceConfig(id="m", kind=SourceKind.GRAPH_MAIL, folder="Inbox")
    with pytest.raises(ValueError):
        DriveArm(FakeClient(), wrong, lookup_from({}))  # type: ignore[arg-type]


def test_arm_exposes_protocol_attributes() -> None:
    arm = DriveArm(FakeClient(), cfg(), lookup_from({}))  # type: ignore[arg-type]
    assert arm.source_id == "fin" and arm.kind is SourceKind.GRAPH_DRIVE


# ---------------------------------------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------------------------------------


class _AnyPathClient(FakeClient):
    """Unknown paths answer an empty collection / 404, so the full discovery can run against a few stubs."""

    def get_json(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        if path not in self.json:
            self.calls.append(("get_json", path, params, headers))
            raise GraphNotFound(404, "itemNotFound", path)
        return super().get_json(path, params=params, headers=headers)

    def iter_pages(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> Iterator[GraphPage]:
        if path not in self.collections:
            self.calls.append(("iter_pages", path, params, headers))
            return iter([GraphPage(value=(), next_link=None, delta_link=None)])
        return super().iter_pages(path, params=params, headers=headers)


def test_discover_wrapper_uses_supported_endpoints_only() -> None:
    client = _AnyPathClient(
        json={"/me/drive": {"id": "ME", "name": "OneDrive", "driveType": "business", "webUrl": "https://me"}},
        collections={
            "/me/followedSites": [
                [{"id": "SITE1", "displayName": "Finance", "webUrl": "https://c/sites/fin"}]
            ],
            "/sites/SITE1/drives": [[{"id": "b!DOCS", "name": "Documents"}]],
        },
    )
    found = discover(client)  # type: ignore[arg-type]
    assert [(s.name, s.drive_id) for s in found] == [
        ("Finance / Documents", "b!DOCS"),
        ("OneDrive (OneDrive)", "me"),
    ]
    deprecated = "shared" + "WithMe"  # spelled split so the endpoint lint never matches this test
    assert not any(deprecated in c[1] or "insights/shared" in c[1] for c in client.calls)
    assert not any(c[1] == "/sites" for c in client.calls)  # no tenant-wide site search either


# ---------------------------------------------------------------------------------------------------------
# $select lint: every top-level field the arm reads must be selected (audit design-correctness-04)
# ---------------------------------------------------------------------------------------------------------


class _Recording(dict[str, Any]):
    """A driveItem that records every top-level key the arm reads."""

    seen: ClassVar[set[str]] = set()

    def get(self, key: str, default: Any = None) -> Any:
        _Recording.seen.add(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        _Recording.seen.add(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            _Recording.seen.add(key)
        return super().__contains__(key)


def test_drive_select_covers_every_field_the_arm_reads() -> None:
    selected = set(DRIVE_SELECT.split(","))
    for needed in (
        "cTag",
        "package",
        "pendingOperations",
        "publication",
        "malware",
        "createdDateTime",
        "lastModifiedBy",
        "webUrl",
        "remoteItem",
        "file",
        "deleted",
        "root",
    ):
        assert needed in selected, needed
    assert "sensitivityLabel" not in selected  # not a driveItem property: selecting it would 400
    _Recording.seen = set()
    rich = file(
        "I1",
        "a.docx",
        "F1",
        publication={"level": "published"},
        lastModifiedBy={"user": {"displayName": "D"}},
        pendingOperations={"pendingContentUpdate": {}},
        malware={"description": "x"},
    )
    pages = [
        [
            _Recording(root()),
            _Recording(folder("F1", "Finance", "ROOT")),
            _Recording(rich),
            _Recording(
                {
                    "id": "NB",
                    "name": "Notes",
                    "package": {"type": "oneNote"},
                    "parentReference": {"id": "ROOT"},
                }
            ),
            _Recording(
                {"id": "SC", "name": "Short", "remoteItem": {"id": "R"}, "parentReference": {"id": "ROOT"}}
            ),
            _Recording(deleted("GONE", "F1")),
        ]
    ]
    client = FakeClient(deltas={DELTA_PATH: [Round(list(map(list, pages)))]})
    arm = DriveArm(client, cfg(), lookup_from({"GONE": ("F1", "old.txt")}))  # type: ignore[arg-type]
    arm.scan(None, full=True)
    item_from_graph("fin", _Recording(rich), lookup_from({"F1": ("ROOT", "Finance"), "ROOT": (None, "")}))
    read = {k for k in _Recording.seen if not k.startswith("@")}
    assert read - selected == set(), f"read but not selected: {sorted(read - selected)}"


def test_relabel_moves_quickxor_so_it_is_a_content_change_not_metadata_only() -> None:
    # C15 section 4 / audit design-correctness-05: the label lives in docMetadata/LabelInfo.xml inside the
    # package, so a relabel moves eTag, cTag AND quickXorHash. The arm must surface the new hash (the
    # classifier's hash rung then says MAYBE_CHANGED -> fetch), and must not invent a label from delta.
    rows = {"F1": ("ROOT", "Finance"), "ROOT": (None, "")}
    before = item_from_graph("fin", file("I1", "Budget.docx", "F1", qx="OLD=="), lookup_from(rows))
    relabelled = file("I1", "Budget.docx", "F1", qx="NEW==")
    relabelled["eTag"], relabelled["cTag"] = '"{I1},3"', '"c:{I1},3"'
    after = item_from_graph("fin", relabelled, lookup_from(rows))
    assert before.size == after.size and before.mtime_ns == after.mtime_ns
    assert after.remote_hashes.quickxor != before.remote_hashes.quickxor
    assert after.ctag != before.ctag
    assert "sensitivity_label" not in after.extra


# ---------------------------------------------------------------------------------------------------------
# 401 / 403 / 404: per drive (raise, never an empty pass) vs per item (skip / one ERROR row)
# ---------------------------------------------------------------------------------------------------------


def test_drive_level_403_is_a_named_it_action_not_an_empty_pass() -> None:
    client = FakeClient(deltas={CURSOR: [GraphError(403, "accessDenied", "Access denied")]})
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    with pytest.raises(GraphError) as info:
        arm.scan(CURSOR, full=False)
    assert info.value.status == 403 and info.value.code == "drive-access-denied"
    assert "Files.Read.All" in str(info.value) and "Sites.Selected" in str(info.value)


def test_drive_level_404_names_the_missing_drive() -> None:
    client = FakeClient(deltas={DELTA_PATH: [GraphNotFound(404, "itemNotFound", "no drive")]})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    with pytest.raises(GraphNotFound) as info:
        arm.scan(None, full=True)
    assert info.value.code == "drive-not-found" and "agentsync discover" in str(info.value)


def test_drive_level_401_stays_auth_required() -> None:
    client = FakeClient(deltas={DELTA_PATH: [AuthRequiredError("REAUTH_REQUIRED")]})
    with pytest.raises(AuthRequiredError):
        DriveArm(client, cfg(), lookup_from({})).scan(None, full=True)  # type: ignore[arg-type]


def test_drive_level_other_errors_propagate_unchanged() -> None:
    boom = GraphError(500, "generalException", "boom")
    client = FakeClient(deltas={DELTA_PATH: [boom]})
    with pytest.raises(GraphError) as info:
        DriveArm(client, cfg(), lookup_from({})).scan(None, full=True)  # type: ignore[arg-type]
    assert info.value is boom


@pytest.mark.parametrize(
    ("source", "path"),
    [
        (SourceConfig(id="me", kind=SourceKind.GRAPH_DRIVE, drive_id="me", folder="/"), "/me/drive"),
        (
            SourceConfig(
                id="s", kind=SourceKind.GRAPH_DRIVE, site="contoso.sharepoint.com:/sites/x", folder="/"
            ),
            "/sites/contoso.sharepoint.com:/sites/x",
        ),
    ],
)
def test_resolve_403_is_named(source: SourceConfig, path: str) -> None:
    client = FakeClient(json={path: GraphError(403, "accessDenied", "no")})
    with pytest.raises(GraphError) as info:
        resolve_drive_id(client, source)  # type: ignore[arg-type]
    assert info.value.code == "drive-access-denied"


def test_scope_folder_403_is_named() -> None:
    client = FakeClient(json={"/drives/D/root:/Secret": GraphError(403, "accessDenied", "no")})
    arm = DriveArm(client, cfg("/Secret"), lookup_from({}))  # type: ignore[arg-type]
    with pytest.raises(GraphError) as info:
        arm.scope_root()
    assert info.value.code == "drive-access-denied"


def test_item_level_403_on_an_ancestor_skips_only_that_item() -> None:
    client = delta_client(
        [[file("I7", "deep.txt", "LOCKED"), file("I1", "Budget.xlsx", "F1", qx="N==")]],
        **{"/drives/D/items/LOCKED": GraphError(403, "accessDenied", "broken inheritance")},
    )
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert set(by_id(result.items)) == {"I1"}  # the readable item still flows
    assert any("403" in a and "ancestor" in a for a in result.alarms)


def test_item_level_500_on_an_ancestor_propagates() -> None:
    client = delta_client(
        [[file("I7", "deep.txt", "P")]], **{"/drives/D/items/P": GraphError(500, "boom", "server")}
    )
    with pytest.raises(GraphError):
        DriveArm(client, cfg(), lookup_from(MANIFEST)).scan(CURSOR, full=False)  # type: ignore[arg-type]


def test_item_level_403_on_a_moved_in_folder_listing_is_unknown_not_empty() -> None:
    client = delta_client([[folder("NEW", "Imported", "F1")]])
    client.collections["/drives/D/items/NEW/children"] = GraphError(403, "accessDenied", "no")
    arm = DriveArm(client, cfg(), lookup_from(MANIFEST))  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert "NEW" in by_id(result.items)
    assert result.unknown_dirs == ("Finance/Imported",)
    assert any("refused" in a and "403" in a for a in result.alarms)


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (GraphError(403, "accessDenied", "no"), GraphError, "item-access-denied"),
        (GraphNotFound(404, "itemNotFound", "gone"), GraphNotFound, "item-not-found"),
    ],
)
def test_fetch_403_404_are_per_item_errors(
    tmp_path: Path, error: GraphError, kind: type[GraphError], code: str
) -> None:
    client = FakeClient(downloads={"/drives/D/items/I1/content": error})
    arm = DriveArm(client, cfg(), lookup_from({}))  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=100, max_files=10)
    with pytest.raises(kind) as info:
        arm.fetch(a_file_item(), tmp_path, budget)
    assert info.value.code == code and "Finance/Budget.xlsx" in str(info.value)
    assert budget.used == 0 and budget.files_used == 0  # refunded: the row retries next cycle
