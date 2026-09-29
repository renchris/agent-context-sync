"""Graph mail arm: phase-1 metadata delta, @removed cross-folder check, 410/400 recovery, MIME fetch."""

from __future__ import annotations

import calendar
import hashlib
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agentsync.config import SourceConfig
from agentsync.errors import BudgetExhaustedError, GraphBadCursor, GraphError, GraphGone, GraphNotFound
from agentsync.graph.client import DeltaResult, GraphPage
from agentsync.graph.mail import MAIL_SELECT, MailArm, item_from_message, mailbox_root, message_rel_path
from agentsync.model import ByteBudget, PassKind, SourceItem, SourceKind

BASE = "https://graph.microsoft.com/v1.0"
DELTA_PATH = "/me/mailFolders/Inbox/messages/delta"
CURSOR = f"{BASE}/me/mailFolders('AQMk')/messages/delta?$deltatoken=CUR"
NEXT = f"{BASE}/me/mailFolders('AQMk')/messages/delta?$deltatoken=NEXT"


@dataclass
class FakeClient:
    json: dict[str, Any] = field(default_factory=dict)
    deltas: dict[str, list[list[dict[str, Any]] | Exception]] = field(default_factory=dict)
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

    def iter_pages(self, path: str, **_: Any) -> Iterator[GraphPage]:
        raise AssertionError("the mail arm never lists collections")

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

    def download(self, path: str, dest: Path, *, max_bytes: int | None = None) -> tuple[int, str]:
        self.calls.append(("download", path, {"max_bytes": str(max_bytes)}, None))
        data = self.downloads[path]
        if isinstance(data, Exception):
            raise data
        if max_bytes is not None and len(data) > max_bytes:
            raise BudgetExhaustedError("too big")
        dest.write_bytes(data)
        return len(data), hashlib.sha256(data).hexdigest()

    def called(self, method: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == method]


def msg(
    ident: str, subject: str = "Q3 numbers", received: str = "2026-09-28T23:30:00Z", **extra: Any
) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.message",
        "id": ident,
        "changeKey": f"CK-{ident}",
        "subject": subject,
        "from": {"emailAddress": {"name": "Dana Swope", "address": "dana@contoso.com"}},
        "receivedDateTime": received,
        "hasAttachments": True,
        "internetMessageId": f"<{ident}@contoso.com>",
        "conversationId": "CONV1",
        "parentFolderId": "INBOX-ID",
        **extra,
    }


def removed(ident: str) -> dict[str, Any]:
    return {"@odata.type": "#microsoft.graph.message", "id": ident, "@removed": {"reason": "deleted"}}


def cfg(mailbox: str = "me", folder: str = "Inbox") -> SourceConfig:
    return SourceConfig(id="mail", kind=SourceKind.GRAPH_MAIL, mailbox=mailbox, folder=folder)


def by_id(items: tuple[SourceItem, ...]) -> dict[str, SourceItem]:
    return {i.stable_id: i for i in items}


# ---------------------------------------------------------------------------------------------------------
# pure mapping
# ---------------------------------------------------------------------------------------------------------


def test_mailbox_root() -> None:
    assert mailbox_root(cfg()) == "/me"
    assert mailbox_root(cfg(mailbox="ME")) == "/me"
    assert mailbox_root(cfg(mailbox="finance@contoso.com")) == "/users/finance@contoso.com"


def test_message_rel_path_shape() -> None:
    assert message_rel_path(msg("M1")) == "2026/09/2026-09-28-Dana Swope-Q3 numbers.eml"


def test_message_rel_path_uses_utc_date() -> None:
    raw = msg("M1", received="2026-01-31T23:30:00-02:00")  # 01:30 UTC next day
    assert message_rel_path(raw).startswith("2026/02/2026-02-01-")


def test_message_rel_path_never_nests_or_carries_control_characters() -> None:
    raw = msg("M1", subject="RE: a/b\\c\td\x00e")
    path = message_rel_path(raw)
    assert path.count("/") == 2
    assert "\\" not in path and "\t" not in path and "\x00" not in path
    assert path.endswith("-RE: a b c d e.eml")


def test_message_rel_path_fallbacks_and_bounds() -> None:
    raw = {"id": "M1", "subject": "x" * 500}
    path = message_rel_path(raw)
    assert path.startswith("0000/00/0000-00-00-unknown-sender-")
    assert len(path.rsplit("/", 1)[1]) < 200
    assert message_rel_path({"id": "M2", "from": {"emailAddress": {"address": "a@b.c"}}}).endswith(
        "-a@b.c-(no subject).eml"
    )


def test_item_from_message_fields() -> None:
    item = item_from_message("mail", msg("M1"))
    assert item.stable_id == "M1"
    assert item.etag == "CK-M1"  # changeKey
    assert item.size == 0  # unknown until phase 2
    assert item.mtime_ns == item.ctime_ns == calendar.timegm((2026, 9, 28, 23, 30, 0)) * 1_000_000_000
    assert item.name == "2026-09-28-Dana Swope-Q3 numbers.eml"
    assert item.rel_path.endswith(item.name)
    assert item.content_type == "message/rfc822"
    assert item.suffix == ".eml"
    assert not item.deleted
    assert dict(item.extra) == {
        "conversation_id": "CONV1",
        "from": "Dana Swope <dana@contoso.com>",
        "has_attachments": True,
        "internet_message_id": "<M1@contoso.com>",
        "parent_folder_id": "INBOX-ID",
        "subject": "Q3 numbers",
    }


def test_item_from_removed_record() -> None:
    item = item_from_message("mail", removed("M9"))
    assert item.deleted
    assert item.extra["removed_reason"] == "deleted"
    assert item.stable_id == "M9"


def test_item_from_message_requires_id() -> None:
    with pytest.raises(ValueError):
        item_from_message("mail", {"subject": "x"})


# ---------------------------------------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------------------------------------


def test_first_scan_is_a_full_metadata_only_round() -> None:
    client = FakeClient(deltas={DELTA_PATH: [[msg("M2"), msg("M1"), msg("M2", subject="edited")]]})
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    result = arm.scan(None, full=False)
    _, path, params, headers = client.calls[0]
    assert path == DELTA_PATH
    assert params == {"$select": MAIL_SELECT}  # never the bodies during detection
    assert headers == {"Prefer": "odata.maxpagesize=100"}
    assert result.pass_kind is PassKind.FULL and result.enumeration_complete
    assert result.new_cursor == NEXT
    assert [i.stable_id for i in result.items] == ["M1", "M2"]
    assert by_id(result.items)["M2"].extra["subject"] == "edited"  # last occurrence wins


def test_delta_scan_uses_stored_link() -> None:
    client = FakeClient(deltas={CURSOR: [[msg("M3")]]})
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    result = arm.scan(CURSOR, full=False)
    assert client.calls[0][1] == CURSOR and client.calls[0][2] is None
    assert result.pass_kind is PassKind.DELTA and not result.enumeration_complete


def test_removed_hard_deleted_is_confirmed_by_404() -> None:
    client = FakeClient(
        json={"/me/messages/M9": GraphNotFound(404, "ErrorItemNotFound", "gone")},
        deltas={CURSOR: [[removed("M9")]]},
    )
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    item = by_id(arm.scan(CURSOR, full=False).items)["M9"]
    assert item.deleted
    assert item.extra["removed"] == "deleted"
    assert item.extra["removed_reason"] == "deleted"


def test_removed_but_moved_is_labelled_with_the_new_folder() -> None:
    client = FakeClient(
        json={
            "/me/messages/M9": msg("M9", parentFolderId="ARCHIVE-ID"),
            "/me/mailFolders/Inbox": {"id": "INBOX-ID"},
        },
        deltas={CURSOR: [[removed("M9")]]},
    )
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    item = by_id(arm.scan(CURSOR, full=False).items)["M9"]
    assert item.deleted
    assert item.extra["removed"] == "moved:ARCHIVE-ID"
    _, _, params, _ = next(c for c in client.calls if c[1] == "/me/messages/M9")
    assert params == {"$select": MAIL_SELECT}


def test_removed_but_still_in_the_folder_is_noise_and_stays_live() -> None:
    client = FakeClient(
        json={"/me/messages/M9": msg("M9"), "/me/mailFolders/Inbox": {"id": "INBOX-ID"}},
        deltas={CURSOR: [[removed("M9")]]},
    )
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    item = by_id(arm.scan(CURSOR, full=False).items)["M9"]
    assert not item.deleted
    assert item.rel_path.endswith(".eml")


def test_folder_id_is_resolved_once() -> None:
    client = FakeClient(
        json={
            "/me/messages/A": msg("A", parentFolderId="X"),
            "/me/messages/B": msg("B", parentFolderId="Y"),
            "/me/mailFolders/Inbox": {"id": "INBOX-ID"},
        },
        deltas={CURSOR: [[removed("A"), removed("B")]]},
    )
    MailArm(client, cfg()).scan(CURSOR, full=False)  # type: ignore[arg-type]
    assert client.called("get_json").count("/me/mailFolders/Inbox") == 1


def test_confirm_removed() -> None:
    client = FakeClient(
        json={
            "/me/messages/GONE": GraphNotFound(404, "ErrorItemNotFound", "gone"),
            "/me/messages/MOVED": {"id": "MOVED", "parentFolderId": "DELETED-ITEMS"},
        }
    )
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    assert arm.confirm_removed("GONE") == "deleted"
    assert arm.confirm_removed("MOVED") == "moved:DELETED-ITEMS"


def test_confirm_removed_propagates_other_errors() -> None:
    client = FakeClient(json={"/me/messages/X": GraphError(403, "ErrorAccessDenied", "no")})
    with pytest.raises(GraphError):
        MailArm(client, cfg()).confirm_removed("X")  # type: ignore[arg-type]


def test_shared_mailbox_paths() -> None:
    root = "/users/finance@contoso.com"
    client = FakeClient(
        json={f"{root}/messages/M9": GraphNotFound(404, "ErrorItemNotFound", "gone")},
        deltas={f"{root}/mailFolders/Inbox/messages/delta": [[removed("M9")]]},
    )
    arm = MailArm(client, cfg(mailbox="finance@contoso.com"))  # type: ignore[arg-type]
    result = arm.scan(None, full=True)
    assert by_id(result.items)["M9"].extra["removed"] == "deleted"


def test_410_reenumerates_from_location() -> None:
    location = f"{BASE}/me/mailFolders('AQMk')/messages/delta?$skiptoken=RESYNC"
    client = FakeClient(
        deltas={CURSOR: [GraphGone("syncStateNotFound", "gone", location)], location: [[msg("M1")]]}
    )
    result = MailArm(client, cfg()).scan(CURSOR, full=False)  # type: ignore[arg-type]
    assert client.called("delta") == [CURSOR, location]
    assert result.pass_kind is PassKind.FULL and result.cursor_reset and result.enumeration_complete
    assert any("410" in a for a in result.alarms)


def test_410_without_location_restarts_tokenless() -> None:
    client = FakeClient(
        deltas={CURSOR: [GraphGone("syncStateNotFound", "gone", None)], DELTA_PATH: [[msg("M1")]]}
    )
    result = MailArm(client, cfg()).scan(CURSOR, full=False)  # type: ignore[arg-type]
    assert client.called("delta") == [CURSOR, DELTA_PATH]
    assert result.cursor_reset


def test_400_bad_cursor_is_dropped_with_an_alarm() -> None:
    client = FakeClient(
        deltas={CURSOR: [GraphBadCursor(400, "invalidRequest", "malformed")], DELTA_PATH: [[msg("M1")]]}
    )
    result = MailArm(client, cfg()).scan(CURSOR, full=False)  # type: ignore[arg-type]
    assert "cursor store corrupt: dropped" in result.alarms
    assert result.cursor_reset and result.enumeration_complete


def test_400_on_tokenless_round_propagates() -> None:
    client = FakeClient(deltas={DELTA_PATH: [GraphBadCursor(400, "invalidRequest", "odd")]})
    with pytest.raises(GraphBadCursor):
        MailArm(client, cfg()).scan(None, full=False)  # type: ignore[arg-type]


def test_repeated_410_never_loops() -> None:
    client = FakeClient(
        deltas={CURSOR: [GraphGone("x", "gone", None)], DELTA_PATH: [GraphGone("x", "gone", None)]}
    )
    with pytest.raises(GraphError, match="kept failing"):
        MailArm(client, cfg()).scan(CURSOR, full=False)  # type: ignore[arg-type]


def test_cursor_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = f"{BASE}/me/mailFolders('AQMk')/messages/delta?$deltatoken=SECRET"
    client = FakeClient(deltas={secret: [GraphGone("x", "gone", None)], DELTA_PATH: [[msg("M1")]]})
    with caplog.at_level(logging.DEBUG, logger="agentsync"):
        MailArm(client, cfg()).scan(secret, full=False)  # type: ignore[arg-type]
    assert "SECRET" not in caplog.text


# ---------------------------------------------------------------------------------------------------------
# fetch (phase 2)
# ---------------------------------------------------------------------------------------------------------

MIME = b"From: dana@contoso.com\r\nSubject: Q3\r\n\r\nbody\r\n"


def test_fetch_downloads_mime_and_charges_actual_size(tmp_path: Path) -> None:
    client = FakeClient(downloads={"/me/messages/M1/$value": MIME})
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=1000, max_files=5)
    item = item_from_message("mail", msg("M1"))
    got = arm.fetch(item, tmp_path / "stage", budget)
    assert got.path.suffix == ".eml"
    assert got.path.name == hashlib.sha256(b"M1").hexdigest()[:16] + ".eml"
    assert got.path.read_bytes() == MIME
    assert got.size == len(MIME) and got.content_sha256 == hashlib.sha256(MIME).hexdigest()
    assert budget.used == len(MIME) and budget.files_used == 1
    _, _, params, _ = next(c for c in client.calls if c[0] == "download")
    assert params == {"max_bytes": "1000"}  # max_bytes = remaining budget


def test_fetch_over_budget_refunds_and_leaves_no_file(tmp_path: Path) -> None:
    client = FakeClient(downloads={"/me/messages/M1/$value": MIME})
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=10, max_files=5)
    with pytest.raises(BudgetExhaustedError):
        arm.fetch(item_from_message("mail", msg("M1")), tmp_path, budget)
    assert budget.used == 0 and budget.files_used == 0
    assert list(tmp_path.iterdir()) == []


def test_fetch_file_budget_exhausted_before_any_request(tmp_path: Path) -> None:
    client = FakeClient()
    arm = MailArm(client, cfg())  # type: ignore[arg-type]
    budget = ByteBudget(max_bytes=1000, max_files=0)
    with pytest.raises(BudgetExhaustedError):
        arm.fetch(item_from_message("mail", msg("M1")), tmp_path, budget)
    assert client.called("download") == []


def test_fetch_refuses_removed_items(tmp_path: Path) -> None:
    arm = MailArm(FakeClient(), cfg())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        arm.fetch(item_from_message("mail", removed("M1")), tmp_path, ByteBudget(100, 5))


def test_arm_validates_its_config() -> None:
    with pytest.raises(ValueError):
        MailArm(FakeClient(), SourceConfig(id="d", kind=SourceKind.GRAPH_DRIVE, drive_id="me"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MailArm(FakeClient(), SourceConfig(id="m", kind=SourceKind.GRAPH_MAIL))  # type: ignore[arg-type]
    arm = MailArm(FakeClient(), cfg())  # type: ignore[arg-type]
    assert arm.source_id == "mail" and arm.kind is SourceKind.GRAPH_MAIL
