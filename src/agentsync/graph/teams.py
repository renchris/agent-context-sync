"""Graph Teams arm: channel and chat messages -> monthly rollup items (owner: graph-arms).

No delta. v1.0 documents no channel message delta and the chat delta is application-only (C15 section 2,
audit design-correctness-06/07), so both read paths are delegated HIGH-WATER walks:

- **Channel** (``ChannelMessage.Read.All``): ``GET /teams/{t}/channels/{c}/messages?$top=50&$expand=replies``.
  The list "is sorted by the last modified date of the entire reply chain", newest first, so the walk
  computes each chain's modified time as ``max(root, replies[*]).lastModifiedDateTime`` (following
  ``replies@odata.nextLink`` past the inline replies) and stops at the first chain older than
  ``HWM - 5 min``. Without a high-water mark (bootstrap, ``full``) it walks to the end of the channel
  (there is no documented 8-month cap on this endpoint).
- **Chat** (``Chat.Read``; configured as ``team_id = "chats"``, ``channel_id = <chat id>``):
  ``GET /me/chats/{id}/messages?$top=50&$orderby=lastModifiedDateTime desc&$filter=lastModifiedDateTime gt
  <HWM - 5 min>`` (the filter is honoured only when ``$orderby`` names the same property; the walk also
  stops client-side in case it is ignored).

Every request of one arm is paced to at most one per second (Teams: "A maximum of one request per second
per app per tenant can be issued on a given channel or chat"). The cursor is ``hwm:<ISO-8601 UTC>`` (not a
secret, not a URL); a cursor of any other form (an older build's delta link) is replaced by a full walk.

Messages are merged into a per-conversation store ``<state_dir>/teams/<source_id>/<YYYY-MM>.json`` (schema
``model.TEAMS_MONTH_SCHEMA``, written tmp + rename, keys sorted). A thread lives in its root's month. Each
touched month is one SourceItem; a FULL walk also emits every stored month (so absence works).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentsync.config import SourceConfig
from agentsync.errors import GraphError, GraphNotFound
from agentsync.graph.client import GraphClient, GraphPage, JsonObject
from agentsync.graph.drive import _obj, _opt_str, _parse_graph_time_ns, _quote_segment, _refund
from agentsync.model import (
    TEAMS_MONTH_SCHEMA,
    ByteBudget,
    ExtraValue,
    FetchResult,
    PassKind,
    RemoteHashes,
    ScanResult,
    SourceItem,
    SourceKind,
)

log = logging.getLogger(__name__)

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_CHATS_TEAM_ID = "chats"  # sources.toml: team_id = "chats" + channel_id = <chat id> selects the chat reader
_CURSOR_PREFIX = "hwm:"
_SKEW_NS = 5 * 60 * 1_000_000_000  # HWM - 5 min: tolerate server-side commit lag (C15 section 9 item 12)
_PAGE_TOP = "50"  # the documented maximum for both channel and chat message lists
_MIN_INTERVAL_S = 1.0  # at most one request per second per channel or chat
_SUFFIX = ".teams.json"
_CONTENT_MESSAGE_TYPES = frozenset({"message"})  # system events (member added, renamed ...) carry no content

_Record = dict[str, Any]


def month_stable_id(channel_id: str, month: str) -> str:
    """Return the rollup identity ``f"{channel_id}:{month}"`` (month = ``YYYY-MM``)."""
    if not _MONTH_RE.match(month):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    return f"{channel_id}:{month}"


def _iso(ns: int) -> str:
    """UTC ns -> ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (one spelling per instant, so month files stay byte-stable)."""
    secs, rem = divmod(ns, 1_000_000_000)
    when = dt.datetime.fromtimestamp(secs, tz=dt.UTC)
    return f"{when:%Y-%m-%dT%H:%M:%S}.{rem // 1_000_000:03d}Z"


def _month_of(ns: int) -> str:
    """UTC ns -> ``YYYY-MM``."""
    when = dt.datetime.fromtimestamp(ns // 1_000_000_000, tz=dt.UTC)
    return f"{when.year:04d}-{when.month:02d}"


def _sender_name(raw: Mapping[str, Any]) -> str:
    """Display name of the user / application / device that posted (``"unknown"`` when none)."""
    sender = _obj(raw, "from")
    for key in ("user", "application", "device"):
        name = _opt_str(_obj(sender, key), "displayName")
        if name:
            return name
    return "unknown"


def _record(raw: Mapping[str, Any]) -> _Record | None:
    """One chatMessage -> a TEAMS_MONTH_SCHEMA message record (None for system events / undated rows)."""
    ident = _opt_str(raw, "id")
    created = _parse_graph_time_ns(raw.get("createdDateTime"))
    if ident is None or created is None:
        return None
    if (raw.get("messageType") or "message") not in _CONTENT_MESSAGE_TYPES:
        return None
    modified = _parse_graph_time_ns(raw.get("lastModifiedDateTime")) or created
    body = _obj(raw, "body")
    raw_content = body.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    if str(body.get("contentType") or "html").lower() == "text":
        content = html.escape(content).replace("\n", "<br>")
    deleted = raw.get("deletedDateTime") is not None
    attachments = []
    for att in raw.get("attachments") or []:
        if isinstance(att, dict):
            attachments.append(
                {"content_url": str(att.get("contentUrl") or ""), "name": str(att.get("name") or "")}
            )
    attachments.sort(key=lambda a: (a["name"], a["content_url"]))
    return {
        "attachments": attachments,
        "body_html": "" if deleted else content,
        "created": _iso(created),
        "deleted": deleted,
        "from": _sender_name(raw),
        "id": ident,
        "last_modified": _iso(modified),
        "reply_to_id": _opt_str(raw, "replyToId"),
        "subject": _opt_str(raw, "subject"),
    }


def _encode(doc: Mapping[str, Any]) -> bytes:
    """Canonical bytes of a month document: sorted keys, UTF-8, compact, one trailing newline."""
    return (json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a 0600 tmp file + fsync + rename (never a torn month file)."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@dataclass
class _Pacer:
    """At most one request per ``interval_s`` (monotonic clock; injectable for tests)."""

    interval_s: float
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    _next: float | None = None

    def wait(self) -> None:
        """Block until the next request may go, then reserve the slot after it."""
        now = self.clock()
        if self._next is not None and now < self._next:
            self.sleep(self._next - now)
            now = max(self.clock(), self._next)
        self._next = now + self.interval_s


def _cursor_hwm(cursor: str | None) -> int | None:
    """``hwm:<iso>`` -> ns; None for no cursor or any other form."""
    if cursor is None or not cursor.startswith(_CURSOR_PREFIX):
        return None
    return _parse_graph_time_ns(cursor[len(_CURSOR_PREFIX) :])


def _modified_ns(raw: Mapping[str, Any]) -> int | None:
    """A message's lastModifiedDateTime (else createdDateTime) in ns."""
    return _parse_graph_time_ns(raw.get("lastModifiedDateTime")) or _parse_graph_time_ns(
        raw.get("createdDateTime")
    )


@dataclass
class _Walk:
    """What one high-water walk saw."""

    messages: list[tuple[str, JsonObject]]  # (root id or own id for chats, message)
    newest_ns: int | None
    reached_end: bool
    requests: int


class TeamsArm:
    """SourceArm for kind ``graph_teams`` (one channel or one chat, one high-water cursor)."""

    source_id: str
    kind: SourceKind

    def __init__(
        self,
        client: GraphClient,
        cfg: SourceConfig,
        store_dir: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_s: float = _MIN_INTERVAL_S,
    ) -> None:
        """Bind to one channel (or chat: ``team_id = "chats"``); ``store_dir`` = StatePaths.teams_store /
        source_id. ``clock``/``sleep`` pace requests (tests inject a fake clock)."""
        if cfg.kind is not SourceKind.GRAPH_TEAMS:
            raise ValueError(f"source {cfg.id!r} is {cfg.kind.value}, not graph_teams")
        if not cfg.team_id or not cfg.channel_id:
            raise ValueError(f"source {cfg.id!r}: graph_teams needs team_id and channel_id")
        self.source_id = cfg.id
        self.kind = SourceKind.GRAPH_TEAMS
        self._client = client
        self._team_id = cfg.team_id
        self._channel_id = cfg.channel_id
        self._is_chat = cfg.team_id.strip().casefold() == _CHATS_TEAM_ID
        self._store = store_dir
        self._names: tuple[str | None, str | None] | None = None
        self._pacer = _Pacer(min_interval_s, clock, sleep)

    @property
    def is_chat(self) -> bool:
        """True when this source is a 1:1 / group chat (``team_id = "chats"``) rather than a channel."""
        return self._is_chat

    # ---- paths ------------------------------------------------------------------------------------------
    def _channel_path(self) -> str:
        """``/teams/{t}/channels/{c}`` or ``/me/chats/{id}``."""
        if self._is_chat:
            return f"/me/chats/{_quote_segment(self._channel_id)}"
        return f"/teams/{_quote_segment(self._team_id)}/channels/{_quote_segment(self._channel_id)}"

    def _month_path(self, month: str) -> Path:
        """Store file of one month."""
        return self._store / f"{month}.json"

    # ---- paced Graph access ------------------------------------------------------------------------------
    def _get(self, path: str, params: Mapping[str, str] | None = None) -> JsonObject:
        """One paced GET."""
        self._pacer.wait()
        return self._client.get_json(path, params=params)

    def _pages(self, path: str, params: Mapping[str, str] | None = None) -> Iterator[GraphPage]:
        """Paced pages: one pacer slot before every page request, none after the last page."""
        self._pacer.wait()
        pages = self._client.iter_pages(path, params=params)
        while True:
            try:
                page = next(pages)
            except StopIteration:
                return
            yield page
            if page.next_link is None:
                return
            self._pacer.wait()

    # ---- store ------------------------------------------------------------------------------------------
    def _load(self, month: str, alarms: list[str]) -> JsonObject | None:
        """Read a month document of THIS channel; a corrupt or foreign file is set aside (alarmed)."""
        path = self._month_path(month)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            doc = json.loads(data.decode("utf-8"))
            if not isinstance(doc, dict) or doc.get("schema") != TEAMS_MONTH_SCHEMA:
                raise ValueError("wrong schema")
            if not isinstance(doc.get("messages"), list):
                raise ValueError("messages is not a list")
        except ValueError as exc:
            aside = path.with_name(f"{path.name}.corrupt")
            path.replace(aside)
            log.error(
                "source %s: month %s unreadable (%s): set aside as %s", self.source_id, month, exc, aside
            )
            alarms.append(f"teams month {month} store file was unreadable: set aside, rebuilt from Graph")
            return None
        if doc.get("channel_id") != self._channel_id:
            alarms.append(f"teams month {month} belonged to another channel: replaced")
            return None
        return doc

    def _stored_months(self) -> list[str]:
        """Months with a store file, sorted."""
        if not self._store.is_dir():
            return []
        months = [p.name[: -len(".json")] for p in self._store.iterdir() if p.name.endswith(".json")]
        return sorted(m for m in months if _MONTH_RE.match(m))

    def _graph_names(self) -> tuple[str | None, str | None]:
        """(team, channel) display names from Graph, fetched once per arm; (None, None) when refused."""
        if self._names is None:
            team = channel = None
            try:
                if self._is_chat:
                    chat = self._get(self._channel_path(), params={"$select": "topic,chatType"})
                    team = "Chats"
                    channel = _opt_str(chat, "topic") or f"{_opt_str(chat, 'chatType') or 'chat'} chat"
                else:
                    team_path = f"/teams/{_quote_segment(self._team_id)}"
                    team = _opt_str(self._get(team_path, params={"$select": "displayName"}), "displayName")
                    channel = _opt_str(
                        self._get(self._channel_path(), params={"$select": "displayName"}), "displayName"
                    )
            except GraphError as exc:  # e.g. no Team.ReadBasic.All / Chat.ReadBasic: names are cosmetic
                log.info("source %s: team/channel names unavailable (%s)", self.source_id, exc)
            self._names = (team, channel)
        return self._names

    def _channel_names(self, stored: Mapping[str, Any] | None) -> tuple[str, str]:
        """(team_name, channel_name): Graph's, else the month file's, else the configured ids."""
        team, channel = self._graph_names()
        stored = stored or {}
        return (
            team or _opt_str(stored, "team_name") or self._team_id,
            channel or _opt_str(stored, "channel_name") or self._channel_id,
        )

    def _merge(self, month: str, records: Iterable[_Record], alarms: list[str]) -> bool:
        """Merge ``records`` into one month file (latest state per id wins); True when its bytes changed."""
        doc = self._load(month, alarms)
        existing: dict[str, _Record] = {}
        if doc is not None:
            for msg in doc["messages"]:
                if isinstance(msg, dict) and isinstance(msg.get("id"), str):
                    existing[msg["id"]] = msg
        for rec in records:
            existing[rec["id"]] = rec
        team_name, channel_name = self._channel_names(doc)
        new_doc = {
            "channel_id": self._channel_id,
            "channel_name": channel_name,
            "messages": sorted(existing.values(), key=lambda m: (str(m.get("created")), str(m.get("id")))),
            "month": month,
            "schema": TEAMS_MONTH_SCHEMA,
            "team_id": self._team_id,
            "team_name": team_name,
        }
        data = _encode(new_doc)
        path = self._month_path(month)
        try:
            if path.read_bytes() == data:
                return False
        except FileNotFoundError:
            pass
        self._store.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_atomic(path, data)
        return True

    def _month_item(self, month: str) -> SourceItem | None:
        """The rollup SourceItem of one stored month (None when the file is missing or foreign)."""
        path = self._month_path(month)
        try:
            data = path.read_bytes()
            doc = json.loads(data.decode("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("channel_id") != self._channel_id:
            return None
        messages = [m for m in doc.get("messages") or [] if isinstance(m, dict)]
        modified = [_parse_graph_time_ns(m.get("last_modified")) for m in messages]
        created = [_parse_graph_time_ns(m.get("created")) for m in messages]
        mtimes = [t for t in modified if t is not None]
        ctimes = [t for t in created if t is not None]
        mtime_ns = max(mtimes) if mtimes else 0
        digest = hashlib.sha256(data).hexdigest()
        name = f"{month}{_SUFFIX}"
        extra: dict[str, ExtraValue] = {
            "channel_id": self._channel_id,
            "message_count": len(messages),
            "month": month,
            "team_id": self._team_id,
        }
        return SourceItem(
            source_id=self.source_id,
            stable_id=month_stable_id(self._channel_id, month),
            rel_path=name,
            name=name,
            size=len(data),
            mtime_ns=mtime_ns,
            ctime_ns=mtime_ns,
            remote_hashes=RemoteHashes(sha256=digest),
            # The month file's own content hash doubles as its cTag ("an eTag for the content"): phase 1 can
            # then call an untouched month UNCHANGED with zero reads.
            ctag=f"sha256:{digest}",
            created_ns=min(ctimes) if ctimes else None,
            content_type="application/json",
            extra=extra,
        )

    # ---- Graph walks ------------------------------------------------------------------------------------
    def _chain_replies(self, root: Mapping[str, Any]) -> list[JsonObject]:
        """Inline ``replies`` of an expanded root plus every page behind ``replies@odata.nextLink``."""
        replies = [r for r in root.get("replies") or [] if isinstance(r, dict)]
        more = root.get("replies@odata.nextLink")
        if isinstance(more, str) and more:
            try:
                for page in self._pages(more):
                    replies.extend(page.value)
            except GraphNotFound:  # the thread vanished between the two calls
                pass
        return replies

    def _walk_channel(self, stop_below_ns: int | None) -> _Walk:
        """Newest-first chain walk; stops at the first chain whose newest change is below the threshold."""
        out: list[tuple[str, JsonObject]] = []
        newest: int | None = None
        requests = 0
        params = {"$top": _PAGE_TOP, "$expand": "replies"}
        for page in self._pages(f"{self._channel_path()}/messages", params):
            requests += 1
            for root in page.value:
                root_id = _opt_str(root, "id")
                if root_id is None:
                    continue
                replies = self._chain_replies(root)
                times = [t for t in (_modified_ns(m) for m in [root, *replies]) if t is not None]
                chain_ns = max(times) if times else None
                if stop_below_ns is not None and chain_ns is not None and chain_ns < stop_below_ns:
                    return _Walk(out, newest, False, requests)
                if chain_ns is not None:
                    newest = chain_ns if newest is None else max(newest, chain_ns)
                out.append((root_id, {k: v for k, v in root.items() if not k.startswith("replies")}))
                out.extend((root_id, r) for r in replies)
        return _Walk(out, newest, True, requests)

    def _walk_chat(self, stop_below_ns: int | None) -> _Walk:
        """Newest-first chat walk with the server-side lastModifiedDateTime filter (and a client stop)."""
        out: list[tuple[str, JsonObject]] = []
        newest: int | None = None
        requests = 0
        params = {"$top": _PAGE_TOP, "$orderby": "lastModifiedDateTime desc"}
        if stop_below_ns is not None:
            params["$filter"] = f"lastModifiedDateTime gt {_iso(stop_below_ns)}"
        for page in self._pages(f"{self._channel_path()}/messages", params):
            requests += 1
            for msg in page.value:
                ident = _opt_str(msg, "id")
                if ident is None:
                    continue
                mod = _modified_ns(msg)
                if stop_below_ns is not None and mod is not None and mod < stop_below_ns:
                    return _Walk(out, newest, False, requests)  # the filter was ignored: stop anyway
                if mod is not None:
                    newest = mod if newest is None else max(newest, mod)
                out.append((_opt_str(msg, "replyToId") or ident, msg))
        return _Walk(out, newest, True, requests)

    def _root_month(self, root_id: str) -> str | None:
        """Month of a root message not in this walk (GET it); None when it no longer exists."""
        try:
            root = self._get(
                f"{self._channel_path()}/messages/{_quote_segment(root_id)}",
                params={"$select": "id,createdDateTime"},
            )
        except GraphNotFound:
            return None
        created = _parse_graph_time_ns(root.get("createdDateTime"))
        return None if created is None else _month_of(created)

    # ---- SourceArm --------------------------------------------------------------------------------------
    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """High-water walk (channel: ``messages?$top=50&$expand=replies``; chat: ``$orderby``/``$filter`` on
        lastModifiedDateTime); merge messages + replies into month files; emit one item per touched month:
        name ``YYYY-MM.teams.json``, rel_path = name, size = file size, remote_hashes.sha256 = sha256 of the
        month file (comparable only to itself), mtime_ns = max lastModifiedDateTime. A FULL walk (no
        cursor, ``full``, or a cursor from an older build) also emits every stored month."""
        alarms: list[str] = []
        hwm = None if full else _cursor_hwm(cursor)
        cursor_reset = False
        if cursor is not None and _cursor_hwm(cursor) is None:
            cursor_reset = True
            alarms.append("stored Teams cursor is not a high-water mark (older delta build): full walk")
        stop_below = None if hwm is None else hwm - _SKEW_NS
        walk = self._walk_chat(stop_below) if self._is_chat else self._walk_channel(stop_below)
        pass_kind = PassKind.FULL if hwm is None else PassKind.DELTA
        if pass_kind is PassKind.FULL and not walk.reached_end:  # cannot happen without a threshold
            raise GraphError(0, "walk-incomplete", f"source {self.source_id}: full walk stopped early")

        by_month: dict[str, dict[str, _Record]] = {}
        root_month: dict[str, str] = {}
        skipped = 0
        for thread_id, raw in walk.messages:
            month = root_month.get(thread_id)
            if month is None:
                if _opt_str(raw, "id") == thread_id:
                    created = _parse_graph_time_ns(raw.get("createdDateTime"))
                    month = None if created is None else _month_of(created)
                else:
                    month = self._root_month(thread_id)
                if month is None:
                    skipped += 1
                    continue
                root_month[thread_id] = month
            rec = _record(raw)
            if rec is None:
                skipped += 1
                continue
            by_month.setdefault(month, {})[rec["id"]] = rec

        changed = [
            month for month in sorted(by_month) if self._merge(month, by_month[month].values(), alarms)
        ]
        months = self._stored_months() if pass_kind is PassKind.FULL else sorted(by_month)
        emitted: list[SourceItem] = []
        for month in months:
            month_item = self._month_item(month)
            if month_item is not None:
                emitted.append(month_item)
            elif self._month_path(month).exists():
                # Not emitted means "absent" to a FULL pass: say why, so a deletion candidate is explainable.
                alarms.append(f"teams month {month} store file unreadable or foreign: not emitted")
        marks = [t for t in (hwm, walk.newest_ns) if t is not None]
        new_hwm = max(marks) if marks else None  # never moves backwards; None only for an empty channel
        log.info(
            "source %s: %s walk, %d page(s), %d message(s), %d month file(s) rewritten, %d skipped",
            self.source_id,
            pass_kind.value,
            walk.requests,
            len(walk.messages),
            len(changed),
            skipped,
        )
        return ScanResult(
            source_id=self.source_id,
            pass_kind=pass_kind,
            items=tuple(emitted),
            new_cursor=None if new_hwm is None else f"{_CURSOR_PREFIX}{_iso(new_hwm)}",
            enumeration_complete=pass_kind is PassKind.FULL,
            cursor_reset=cursor_reset,
            alarms=tuple(alarms),
        )

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Copy the month file into ``dest_dir`` charging ``budget``."""
        if item.source_id != self.source_id:
            raise ValueError(f"item of source {item.source_id!r} fetched through {self.source_id!r}")
        channel, _, month = item.stable_id.rpartition(":")
        if channel != self._channel_id or not _MONTH_RE.match(month):
            raise ValueError(f"{item.stable_id!r} is not a month of channel {self._channel_id!r}")
        source = self._month_path(month)
        size = source.stat().st_size  # FileNotFoundError when the month vanished: an ERROR row, retried
        budget.charge(size)
        try:
            data = source.read_bytes()
            dest_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(item.stable_id.encode("utf-8")).hexdigest()[:16]
            dest = dest_dir / f"{digest}{_SUFFIX}"
            _write_atomic(dest, data)
        except Exception:
            _refund(budget, size)
            raise
        budget.used += len(data) - size
        return FetchResult(
            stable_id=item.stable_id,
            path=dest,
            size=len(data),
            content_sha256=hashlib.sha256(data).hexdigest(),
        )
