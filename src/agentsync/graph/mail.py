"""Graph mail-folder message delta arm with two-phase fetch (owner: graph-arms).

Phase 1 (detect): ``/{mailbox}/mailFolders/{folder}/messages/delta`` with ``$select=MAIL_SELECT`` only (no
body). Phase 2 (fetch): ``/{mailbox}/messages/{id}/$value`` (full MIME incl. attachments) for the diff only.
``@removed`` means deleted OR moved out of the folder: ``confirm_removed`` does the cross-folder check.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import logging
import re
from pathlib import Path

from agentsync.config import SourceConfig
from agentsync.errors import GraphBadCursor, GraphError, GraphGone, GraphNotFound
from agentsync.graph.client import DeltaResult, GraphClient, JsonObject
from agentsync.graph.drive import _nfc, _obj, _opt_str, _parse_graph_time_ns, _quote_segment, _refund
from agentsync.model import ByteBudget, ExtraValue, FetchResult, PassKind, ScanResult, SourceItem, SourceKind

MAIL_SELECT = ",".join(
    (
        "id",
        "changeKey",
        "subject",
        "from",
        "receivedDateTime",
        "hasAttachments",
        "internetMessageId",
        "conversationId",
        "parentFolderId",
    )
)
"""Phase-1 $select: omitting it returns ~9 KB of body per message during change detection (measured)."""

log = logging.getLogger(__name__)

_PAGE_SIZE = 100  # Prefer: odata.maxpagesize (message delta defaults to 10 per page)
_MAX_ROUND_ATTEMPTS = 3
_FROM_MAX = 60
_SUBJECT_MAX = 120
_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f/\\]+")
_SPACE_RE = re.compile(r"\s+")


def mailbox_root(cfg: SourceConfig) -> str:
    """``"me"`` -> ``/me``; a UPN -> ``/users/{upn}`` (shared mailbox; needs Mail.Read.Shared)."""
    box = (cfg.mailbox or "me").strip()
    if box.casefold() == "me" or not box:
        return "/me"
    return f"/users/{_quote_segment(box)}"


def _clean(text: str, limit: int) -> str:
    """One path-safe component: no slashes/control characters, collapsed whitespace, bounded length."""
    text = _SPACE_RE.sub(" ", _UNSAFE_RE.sub(" ", _nfc(text))).strip().strip(".")
    return text[:limit].rstrip()


def _sender(raw: JsonObject) -> tuple[str | None, str | None]:
    """(display name, address) of ``from`` (either may be None)."""
    email = _obj(_obj(raw, "from"), "emailAddress")
    return _opt_str(email, "name"), _opt_str(email, "address")


def message_rel_path(raw: JsonObject) -> str:
    """``YYYY/MM/YYYY-MM-DD-<from>-<subject>.eml`` from receivedDateTime (UTC), sender name, subject (raw
    text; publish slugs it)."""
    received = _parse_graph_time_ns(raw.get("receivedDateTime"))
    if received is None:
        year, month, day = "0000", "00", "00"
    else:
        when = dt.datetime.fromtimestamp(received // 1_000_000_000, tz=dt.UTC)
        year, month, day = f"{when.year:04d}", f"{when.month:02d}", f"{when.day:02d}"
    name, address = _sender(raw)
    sender = _clean(name or address or "", _FROM_MAX) or "unknown-sender"
    subject = _clean(_opt_str(raw, "subject") or "", _SUBJECT_MAX) or "(no subject)"
    return f"{year}/{month}/{year}-{month}-{day}-{sender}-{subject}.eml"


def item_from_message(source_id: str, raw: JsonObject) -> SourceItem:
    """Map a message to a SourceItem: stable_id = message id; etag = changeKey; size 0 (unknown until fetch);
    mtime_ns from receivedDateTime; extra: internet_message_id, conversation_id, has_attachments, from,
    subject. ``@removed`` -> deleted=True with extra["removed_reason"]."""
    stable_id = raw.get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise ValueError("message without an id")
    removed = raw.get("@removed")
    if removed is not None:
        reason = _opt_str(removed, "reason") if isinstance(removed, dict) else None
        # The removal record carries only the id: path and name stay whatever the manifest recorded.
        return SourceItem(
            source_id=source_id,
            stable_id=stable_id,
            rel_path="",
            name="",
            size=0,
            mtime_ns=0,
            ctime_ns=0,
            deleted=True,
            content_type="message/rfc822",
            extra={"removed_reason": reason or "deleted"},
        )
    rel_path = message_rel_path(raw)
    received = _parse_graph_time_ns(raw.get("receivedDateTime")) or 0
    name, address = _sender(raw)
    extra: dict[str, ExtraValue] = {}
    for key, field in (
        ("internet_message_id", "internetMessageId"),
        ("conversation_id", "conversationId"),
        ("parent_folder_id", "parentFolderId"),
    ):
        value = _opt_str(raw, field)
        if value:
            extra[key] = value
    has_attachments = raw.get("hasAttachments")
    if isinstance(has_attachments, bool):
        extra["has_attachments"] = has_attachments
    sender = f"{name} <{address}>" if name and address else (name or address)
    if sender:
        extra["from"] = _nfc(sender)
    subject = _opt_str(raw, "subject")
    if subject:
        extra["subject"] = _nfc(subject)
    return SourceItem(
        source_id=source_id,
        stable_id=stable_id,
        rel_path=rel_path,
        name=rel_path.rsplit("/", 1)[-1],
        size=0,
        mtime_ns=received,
        ctime_ns=received,
        etag=_opt_str(raw, "changeKey"),
        created_ns=received or None,
        content_type="message/rfc822",
        extra=extra,
    )


class MailArm:
    """SourceArm for kind ``graph_mail`` (one folder, one cursor)."""

    source_id: str
    kind: SourceKind

    def __init__(self, client: GraphClient, cfg: SourceConfig) -> None:
        """Bind to one mail folder source."""
        if cfg.kind is not SourceKind.GRAPH_MAIL:
            raise ValueError(f"source {cfg.id!r} is {cfg.kind.value}, not graph_mail")
        if not cfg.folder:
            raise ValueError(f"source {cfg.id!r}: graph_mail needs a folder")
        self.source_id = cfg.id
        self.kind = SourceKind.GRAPH_MAIL
        self._client = client
        self._cfg = cfg
        self._root = mailbox_root(cfg)
        self._folder = cfg.folder.strip().strip("/")
        self._folder_id: str | None = None

    def _folder_path(self) -> str:
        """``/{mailbox}/mailFolders/{folder}`` (a well-known name or a folder id)."""
        return f"{self._root}/mailFolders/{_quote_segment(self._folder)}"

    def folder_id(self) -> str:
        """The watched folder's id (well-known names resolved once via GET)."""
        if self._folder_id is None:
            body = self._client.get_json(self._folder_path(), params={"$select": "id"})
            ident = body.get("id")
            if not isinstance(ident, str) or not ident:
                raise GraphError(0, "no-id", f"mail folder {self._folder!r}: response carried no id")
            self._folder_id = ident
        return self._folder_id

    def _get_message(self, message_id: str) -> JsonObject | None:
        """Phase-1 metadata of one message anywhere in the mailbox; None on 404 (hard-deleted)."""
        try:
            return self._client.get_json(
                f"{self._root}/messages/{_quote_segment(message_id)}", params={"$select": MAIL_SELECT}
            )
        except GraphNotFound:
            return None

    def confirm_removed(self, message_id: str) -> str:
        """Cross-folder check for ``@removed``: GET the message; 404 -> "deleted"; found ->
        "moved:<folderId>"."""
        found = self._get_message(message_id)
        if found is None:
            return "deleted"
        return f"moved:{_opt_str(found, 'parentFolderId') or 'unknown'}"

    def _headers(self) -> dict[str, str]:
        """Page-size preference for every delta request."""
        return {"Prefer": f"odata.maxpagesize={_PAGE_SIZE}"}

    def _run_round(self, cursor: str | None, *, full: bool) -> tuple[DeltaResult, PassKind, bool, list[str]]:
        """Drain one round: (result, pass kind, cursor_reset, alarms) with the 410 / 400 recovery ladder."""
        alarms: list[str] = []
        cursor_reset = False
        delta_link: str | None = None if (full or cursor is None) else cursor
        start: str | None = None  # a 410's Location link, when the service supplies one
        for _attempt in range(_MAX_ROUND_ATTEMPTS):
            try:
                if delta_link is not None:
                    result = self._client.delta(delta_link, headers=self._headers())
                    return result, PassKind.DELTA, False, alarms
                if start is not None:
                    result = self._client.delta(start, headers=self._headers())
                else:
                    result = self._client.delta(
                        f"{self._folder_path()}/messages/delta",
                        params={"$select": MAIL_SELECT},
                        headers=self._headers(),
                    )
                return result, PassKind.FULL, cursor_reset, alarms
            except GraphGone as exc:
                log.warning("source %s: mail delta 410 (%s): full re-enumeration", self.source_id, exc.code)
                alarms.append(f"cursor expired (410 {exc.code}): full re-enumeration")
                delta_link, start = None, exc.location
            except GraphBadCursor:
                if delta_link is None and start is None:
                    raise  # a token-less round carries no cursor of ours: never loop on it
                log.error("source %s: 400 on a stored mail link: dropped, full enumeration", self.source_id)
                alarms.append("cursor store corrupt: dropped")
                delta_link, start = None, None
            cursor_reset = True
        raise GraphError(0, "resync-loop", f"source {self.source_id}: delta kept failing after recovery")

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Phase 1 delta round (FULL when no cursor / full / after 410 or 400); every ``@removed`` goes
        through ``confirm_removed`` and is emitted deleted=True with extra["removed"] = "deleted" |
        "moved:<id>"."""
        result, pass_kind, cursor_reset, alarms = self._run_round(cursor, full=full)
        latest: dict[str, JsonObject] = {}  # last occurrence of an id wins
        for raw in result.items:
            ident = raw.get("id")
            if isinstance(ident, str) and ident:
                latest[ident] = raw

        items: list[SourceItem] = []
        removed = moved = noise = 0
        for ident in sorted(latest):
            raw = latest[ident]
            if "@removed" not in raw:
                items.append(item_from_message(self.source_id, raw))
                continue
            found = self._get_message(ident)
            if found is not None and _opt_str(found, "parentFolderId") == self.folder_id():
                # Still in this folder: delta noise (collection-level tracking), keep it live.
                noise += 1
                items.append(item_from_message(self.source_id, found))
                continue
            verdict = (
                "deleted" if found is None else f"moved:{_opt_str(found, 'parentFolderId') or 'unknown'}"
            )
            if found is None:
                removed += 1
            else:
                moved += 1
            tomb = item_from_message(self.source_id, raw)
            items.append(dataclasses.replace(tomb, extra={**tomb.extra, "removed": verdict}))
        log.info(
            "source %s: %s pass, %d page(s), %d message(s), %d deleted, %d moved out, %d removal noise",
            self.source_id,
            pass_kind.value,
            result.pages,
            len(items),
            removed,
            moved,
            noise,
        )
        return ScanResult(
            source_id=self.source_id,
            pass_kind=pass_kind,
            items=tuple(items),
            new_cursor=result.delta_link,
            enumeration_complete=pass_kind is PassKind.FULL,
            cursor_reset=cursor_reset,
            alarms=tuple(alarms),
        )

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Phase 2: download MIME to ``<dest_dir>/<id-hash>.eml`` with max_bytes = budget.remaining_bytes,
        then charge the actual size."""
        if item.source_id != self.source_id:
            raise ValueError(f"item of source {item.source_id!r} fetched through {self.source_id!r}")
        if item.deleted:
            raise ValueError(f"{item.stable_id}: a removed message has no content")
        budget.charge(0)  # reserves the file slot; raises BudgetExhaustedError before any byte moves
        dest_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(item.stable_id.encode("utf-8")).hexdigest()[:16]
        dest = dest_dir / f"{digest}.eml"
        url = f"{self._root}/messages/{_quote_segment(item.stable_id)}/$value"
        try:
            size, sha = self._client.download(url, dest, max_bytes=budget.remaining_bytes)
        except Exception:
            _refund(budget, 0)
            raise
        budget.used += size  # max_bytes guaranteed it fits
        return FetchResult(stable_id=item.stable_id, path=dest, size=size, content_sha256=sha)
