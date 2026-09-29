"""Graph Teams channel message delta arm -> monthly rollup items (owner: graph-arms).

Channel delta returns top-level messages; replies come from ``/messages/{id}/replies`` for each changed
message. Messages are merged into a per-channel store ``<state_dir>/teams/<source_id>/<YYYY-MM>.json`` (schema
``model.TEAMS_MONTH_SCHEMA``, written tmp + rename, keys sorted). Each touched month is one SourceItem.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agentsync.config import SourceConfig
from agentsync.errors import GraphBadCursor, GraphError, GraphGone, GraphNotFound
from agentsync.graph.client import DeltaResult, GraphClient, JsonObject
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
_MAX_ROUND_ATTEMPTS = 3
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


class TeamsArm:
    """SourceArm for kind ``graph_teams`` (one channel, one cursor)."""

    source_id: str
    kind: SourceKind

    def __init__(self, client: GraphClient, cfg: SourceConfig, store_dir: Path) -> None:
        """Bind to one channel; ``store_dir`` = StatePaths.teams_store / source_id."""
        if cfg.kind is not SourceKind.GRAPH_TEAMS:
            raise ValueError(f"source {cfg.id!r} is {cfg.kind.value}, not graph_teams")
        if not cfg.team_id or not cfg.channel_id:
            raise ValueError(f"source {cfg.id!r}: graph_teams needs team_id and channel_id")
        self.source_id = cfg.id
        self.kind = SourceKind.GRAPH_TEAMS
        self._client = client
        self._team_id = cfg.team_id
        self._channel_id = cfg.channel_id
        self._store = store_dir
        self._names: tuple[str | None, str | None] | None = None

    # ---- paths ------------------------------------------------------------------------------------------
    def _channel_path(self) -> str:
        """``/teams/{t}/channels/{c}``."""
        return f"/teams/{_quote_segment(self._team_id)}/channels/{_quote_segment(self._channel_id)}"

    def _month_path(self, month: str) -> Path:
        """Store file of one month."""
        return self._store / f"{month}.json"

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
                team_path = f"/teams/{_quote_segment(self._team_id)}"
                team = _opt_str(
                    self._client.get_json(team_path, params={"$select": "displayName"}), "displayName"
                )
                channel = _opt_str(
                    self._client.get_json(self._channel_path(), params={"$select": "displayName"}),
                    "displayName",
                )
            except GraphError as exc:  # e.g. no Team.ReadBasic.All consent: names are cosmetic
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

    # ---- Graph ------------------------------------------------------------------------------------------
    def _run_round(self, cursor: str | None, *, full: bool) -> tuple[DeltaResult, PassKind, bool, list[str]]:
        """Drain one round: (result, pass kind, cursor_reset, alarms) with the 410 / 400 recovery ladder."""
        alarms: list[str] = []
        cursor_reset = False
        delta_link: str | None = None if (full or cursor is None) else cursor
        start: str | None = None
        for _attempt in range(_MAX_ROUND_ATTEMPTS):
            try:
                if delta_link is not None:
                    return self._client.delta(delta_link), PassKind.DELTA, False, alarms
                if start is not None:
                    result = self._client.delta(start)
                else:
                    result = self._client.delta(f"{self._channel_path()}/messages/delta")
                return result, PassKind.FULL, cursor_reset, alarms
            except GraphGone as exc:
                log.warning(
                    "source %s: channel delta 410 (%s): full re-enumeration", self.source_id, exc.code
                )
                alarms.append(f"cursor expired (410 {exc.code}): full re-enumeration")
                delta_link, start = None, exc.location
            except GraphBadCursor:
                if delta_link is None and start is None:
                    raise
                log.error(
                    "source %s: 400 on a stored channel link: dropped, full enumeration", self.source_id
                )
                alarms.append("cursor store corrupt: dropped")
                delta_link, start = None, None
            cursor_reset = True
        raise GraphError(0, "resync-loop", f"source {self.source_id}: delta kept failing after recovery")

    def _replies(self, root_id: str) -> list[JsonObject]:
        """Every reply of one root message ([] when the thread is gone)."""
        out: list[JsonObject] = []
        try:
            for page in self._client.iter_pages(
                f"{self._channel_path()}/messages/{_quote_segment(root_id)}/replies"
            ):
                out.extend(page.value)
        except GraphNotFound:
            return []
        return out

    def _root_month(self, root_id: str) -> str | None:
        """Month of a root message not in this round (GET it); None when it no longer exists."""
        try:
            root = self._client.get_json(
                f"{self._channel_path()}/messages/{_quote_segment(root_id)}",
                params={"$select": "id,createdDateTime"},
            )
        except GraphNotFound:
            return None
        created = _parse_graph_time_ns(root.get("createdDateTime"))
        return None if created is None else _month_of(created)

    # ---- SourceArm --------------------------------------------------------------------------------------
    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Delta round over ``/teams/{t}/channels/{c}/messages/delta``; merge messages + replies into month
        files; emit one item per touched month: name ``YYYY-MM.teams.json``, rel_path = name, size = file
        size, remote_hashes.sha256 = sha256 of the month file (comparable only to itself), mtime_ns = max
        lastModifiedDateTime. A FULL round also emits every stored month (so absence works)."""
        result, pass_kind, cursor_reset, alarms = self._run_round(cursor, full=full)
        latest: dict[str, JsonObject] = {}
        for raw in result.items:
            ident = raw.get("id")
            if isinstance(ident, str) and ident:
                latest[ident] = raw

        by_month: dict[str, dict[str, _Record]] = {}
        root_month: dict[str, str] = {}
        skipped = 0

        def add(month: str, raw: Mapping[str, Any]) -> None:
            nonlocal skipped
            rec = _record(raw)
            if rec is None:
                skipped += 1
                return
            by_month.setdefault(month, {})[rec["id"]] = rec

        roots = sorted((i for i, raw in latest.items() if not _opt_str(raw, "replyToId")))
        for ident in roots:
            raw = latest[ident]
            created = _parse_graph_time_ns(raw.get("createdDateTime"))
            if created is None:
                skipped += 1
                continue
            month = _month_of(created)  # a thread lives in its root's month, replies included
            root_month[ident] = month
            add(month, raw)
            for reply in self._replies(ident):
                add(month, reply)

        for ident in sorted(i for i in latest if i not in root_month and _opt_str(latest[i], "replyToId")):
            raw = latest[ident]
            parent = str(raw["replyToId"])
            reply_month = root_month.get(parent) or self._root_month(parent)
            if reply_month is None:
                skipped += 1
                continue
            root_month[parent] = reply_month
            add(reply_month, raw)

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
        items = tuple(emitted)
        log.info(
            "source %s: %s pass, %d page(s), %d message(s), %d month file(s) rewritten, %d skipped",
            self.source_id,
            pass_kind.value,
            result.pages,
            len(latest),
            len(changed),
            skipped,
        )
        return ScanResult(
            source_id=self.source_id,
            pass_kind=pass_kind,
            items=items,
            new_cursor=result.delta_link,
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
