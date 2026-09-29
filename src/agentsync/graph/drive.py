"""Graph drive delta arm: OneDrive / SharePoint document libraries (owner: graph-arms).

State machine (design 4.2): S0 no cursor -> token-less FULL delta (never ``token=latest`` as a bootstrap) ->
follow nextLink, persisting the page link -> final deltaLink = pending cursor -> S3 poll with the stored link.
410 -> GraphGone -> FULL re-enumeration from the Location link (or token-less), ``cursor_reset=True``.
400 on the stored link -> GraphBadCursor -> alarm "cursor store corrupt: dropped", FULL from S0.
One cursor per drive; ``folder`` scope is filtered locally by derived path.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from agentsync.config import SourceConfig
from agentsync.errors import GraphBadCursor, GraphError, GraphGone, GraphNotFound
from agentsync.graph.client import DeltaResult, GraphClient, GraphPage, JsonObject
from agentsync.model import (
    ByteBudget,
    ExtraValue,
    FetchResult,
    PassKind,
    RemoteHashes,
    ScanResult,
    SourceItem,
    SourceKind,
    TreeLookup,
)
from agentsync.paths import is_included

DRIVE_SELECT = (
    "id,name,size,eTag,cTag,file,folder,package,root,parentReference,deleted,lastModifiedDateTime,"
    "createdDateTime,webUrl,remoteItem,publication"
)
"""$select for drive delta: ``file`` MUST be present or quickXorHash is dropped for every item."""

DELTA_HEADERS: dict[str, str] = {"Prefer": "deltaExcludeParent"}

log = logging.getLogger(__name__)

# The driveItem-delta reference (updated 2026-06-06) lists ``deltaExcludeParent`` as a request header of its
# own ("If this request header is included ..."), while the contract constant sends it as a Prefer value.
# Both are sent: the service ignores an unknown Prefer token and an unknown header, so whichever form the
# tenant honours takes effect and neither can fail the request.
_EXCLUDE_PARENT_HEADER = "deltaExcludeParent"
_MAX_ROUND_ATTEMPTS = 3  # the round + at most two 410/400 recoveries (never loop)
_MAX_ANCESTOR_FETCHES = 256  # per scan: unknown parents resolved by GET /items/{id}
_MAX_PATH_DEPTH = 512  # cycle guard for the id -> parent walk
_ALARM_LIST_LIMIT = 5

_TIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:?\d{2})?$"
)


# ---------------------------------------------------------------------------------------------------------
# Helpers shared by the three Graph arms (private: the contract lists the public surface).
# ---------------------------------------------------------------------------------------------------------


def _parse_graph_time_ns(value: object) -> int | None:
    """Parse a Graph ISO-8601 timestamp (``Z``/offset, any fractional digits) to integer UTC ns; None if bad.

    Integer arithmetic only, so one string always yields the same ns.  No offset means UTC (Graph's rule).
    """
    if not isinstance(value, str):
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    frac, tz = m.group(7) or "", m.group(8)
    if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]):
        return None
    if hour > 23 or minute > 59 or second > 60:
        return None
    seconds = calendar.timegm((year, month, day, hour, minute, min(second, 59), 0, 0, 0))
    if tz and tz not in ("Z", "z"):
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        seconds -= sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    return seconds * 1_000_000_000 + int(frac[:9].ljust(9, "0"))


def _nfc(text: str) -> str:
    """NFC-normalise ``text``."""
    return unicodedata.normalize("NFC", text)


def _obj(raw: Mapping[str, Any], key: str) -> JsonObject:
    """``raw[key]`` when it is a JSON object, else ``{}``."""
    val = raw.get(key)
    return val if isinstance(val, dict) else {}


def _opt_str(raw: Mapping[str, Any], key: str) -> str | None:
    """``raw[key]`` when it is a non-empty string, else None."""
    val = raw.get(key)
    return val if isinstance(val, str) and val else None


def _int(raw: Mapping[str, Any], key: str) -> int:
    """``raw[key]`` as a non-negative int (0 when absent or not an int)."""
    val = raw.get(key)
    return val if isinstance(val, int) and not isinstance(val, bool) and val >= 0 else 0


def _quote_segment(segment: str) -> str:
    """Percent-encode one URL path segment (Graph ids may carry ``!``, ``,``, ``=``, ``:`` and ``@``)."""
    return quote(segment, safe="!,=:@")


def _require_id(body: Mapping[str, Any], what: str) -> str:
    """Return ``body["id"]`` or raise GraphError naming ``what``."""
    ident = body.get("id")
    if not isinstance(ident, str) or not ident:
        raise GraphError(0, "no-id", f"{what}: response carried no id")
    return ident


def _pages(client: GraphClient, path: str, params: Mapping[str, str] | None = None) -> Iterator[JsonObject]:
    """Yield every object of a paged collection."""
    for page in client.iter_pages(path, params=params):
        yield from page.value


def _refund(budget: ByteBudget, size: int) -> None:
    """Undo one ``budget.charge(size)`` after a failed transfer (the item is retried next cycle)."""
    budget.used = max(0, budget.used - size)
    budget.files_used = max(0, budget.files_used - 1)


# ---------------------------------------------------------------------------------------------------------
# driveItem mapping
# ---------------------------------------------------------------------------------------------------------


def _is_dir(raw: Mapping[str, Any]) -> bool:
    """Folders, packages (e.g. OneNote notebooks) and the root are directories in the id tree."""
    return "folder" in raw or "package" in raw or "root" in raw


def _parent_id(raw: Mapping[str, Any]) -> str | None:
    """``parentReference.id`` or None."""
    return _opt_str(_obj(raw, "parentReference"), "id")


def _derive_path(parent_id: str | None, name: str, lookup: TreeLookup, root_id: str | None) -> str | None:
    """Walk ``parent_id`` up the id tree; return ``<ancestors>/<name>`` relative to ``root_id``.

    ``root_id`` None means "stop at the first entry whose parent is None" (the drive root, whose own name is
    never part of a path).  Returns None when the chain reaches an unknown id, loops, or ends at a root other
    than ``root_id`` (the item lies outside the scope).
    """
    parts = [name]
    current = parent_id
    seen: set[str] = set()
    while True:
        if current is None:
            return "/".join(reversed(parts)) if root_id is None else None
        if current == root_id:
            return "/".join(reversed(parts))
        if current in seen or len(seen) > _MAX_PATH_DEPTH:
            return None
        seen.add(current)
        entry = lookup(current)
        if entry is None:
            return None
        up, entry_name = entry
        if up is None:
            return "/".join(reversed(parts)) if root_id is None else None
        parts.append(entry_name)
        current = up


def item_from_graph(
    source_id: str, raw: JsonObject, lookup: TreeLookup, *, root_id: str | None = None
) -> SourceItem:
    """Map one driveItem to a SourceItem: stable_id = id; parent_id; rel_path derived from the id tree
    (``lookup`` for parents not in this batch; "" if underivable); remote_hashes.quickxor from
    file.hashes.quickXorHash; etag/ctag opaque; ``deleted`` facet -> deleted=True (name may be absent);
    extra carries web_url, package type, publication level, sensitivity label when present.

    ``root_id`` (extension) makes rel_path relative to a scope folder; None = relative to the drive root.
    """
    stable_id = raw.get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise ValueError("driveItem without an id")
    deleted = "deleted" in raw
    is_dir = _is_dir(raw)
    parent_id = _parent_id(raw)
    name = _nfc(_opt_str(raw, "name") or "")
    if deleted and not name:  # ODB omits name on delete records: fall back to what the tree knows
        known = lookup(stable_id)
        if known is not None:
            parent_id = parent_id or known[0]
            name = known[1]
    rel_path = _nfc(_derive_path(parent_id, name, lookup, root_id) or "") if name else ""

    file_facet = _obj(raw, "file")
    hashes = _obj(file_facet, "hashes")
    remote = RemoteHashes(
        quickxor=_opt_str(hashes, "quickXorHash"),
        sha256=_opt_str(hashes, "sha256Hash"),
        sha1=_opt_str(hashes, "sha1Hash"),
    )
    mtime_ns = _parse_graph_time_ns(raw.get("lastModifiedDateTime")) or 0

    extra: dict[str, ExtraValue] = {}
    web_url = _opt_str(raw, "webUrl")
    if web_url:
        extra["web_url"] = web_url
    package_type = _opt_str(_obj(raw, "package"), "type")
    if package_type:
        extra["package_type"] = package_type
    publication_level = _opt_str(_obj(raw, "publication"), "level")
    if publication_level:
        extra["publication_level"] = publication_level
    label = _obj(raw, "sensitivityLabel")
    label_name = _opt_str(label, "displayName") or _opt_str(label, "labelId")
    if label_name:
        extra["sensitivity_label"] = label_name
    drive_id = _opt_str(_obj(raw, "parentReference"), "driveId")
    if drive_id:
        extra["drive_id"] = drive_id
    if "file" in raw and remote.quickxor is None:
        extra["hash_absent"] = True  # design section 9 probe 1: OneNote / zero-size; phase 1 falls back

    return SourceItem(
        source_id=source_id,
        stable_id=stable_id,
        rel_path=rel_path,
        name=name,
        # A folder's size is the sum of its descendants and moves on every child edit: never a signal.
        size=0 if is_dir else _int(raw, "size"),
        mtime_ns=mtime_ns,
        ctime_ns=mtime_ns,  # Graph has no change time; mirror mtime so the stat tuple stays stable
        is_dir=is_dir,
        remote_hashes=remote,
        etag=_opt_str(raw, "eTag"),
        # Folder cTags move on any descendant change (design section 5): opaque and meaningless for a dir.
        ctag=None if is_dir else _opt_str(raw, "cTag"),
        deleted=deleted,
        parent_id=parent_id,
        created_ns=_parse_graph_time_ns(raw.get("createdDateTime")),
        content_type=_opt_str(file_facet, "mimeType"),
        extra=extra,
    )


# ---------------------------------------------------------------------------------------------------------
# Drive resolution and Arm 0 discovery
# ---------------------------------------------------------------------------------------------------------


def resolve_drive_id(client: GraphClient, cfg: SourceConfig) -> str:
    """``drive_id="me"`` -> GET /me/drive; ``site`` -> GET /sites/{host}:/{path} then /sites/{id}/drive."""
    if cfg.drive_id:
        if cfg.drive_id != "me":
            return cfg.drive_id
        return _require_id(client.get_json("/me/drive", params={"$select": "id"}), "/me/drive")
    if not cfg.site:
        raise GraphError(0, "config", f"source {cfg.id!r}: graph_drive needs drive_id or site")
    site = cfg.site.strip()
    if ":" in site:
        host, _, path = site.partition(":")
        site_path = f"/sites/{host.strip()}:{quote('/' + path.strip().strip('/'), safe='/')}"
    else:
        site_path = f"/sites/{_quote_segment(site)}"  # a site id ("host,guid,guid") or "root"
    site_id = _require_id(client.get_json(site_path, params={"$select": "id"}), f"site {site!r}")
    drive = client.get_json(f"/sites/{_quote_segment(site_id)}/drive", params={"$select": "id"})
    return _require_id(drive, f"default library of site {site!r}")


@dataclass(frozen=True, slots=True)
class DiscoveredScope:
    """One candidate source found by ``discover`` (printed for a human to accept into sources.toml)."""

    kind: SourceKind
    name: str
    drive_id: str | None
    site: str | None
    web_url: str | None
    note: str  # e.g. "sharedWithMe remoteItem: needs its own cursor against the owning drive"


def _site_param(web_url: str | None) -> str | None:
    """``https://host/sites/x`` -> ``host:/sites/x`` (the sources.toml ``site`` form)."""
    if not web_url:
        return None
    parts = urlsplit(web_url)
    if not parts.netloc:
        return None
    return f"{parts.netloc}:{unquote(parts.path) or '/'}"


def _discover_me(client: GraphClient) -> list[DiscoveredScope]:
    """The signed-in user's own OneDrive."""
    me = client.get_json("/me/drive", params={"$select": "id,name,driveType,webUrl"})
    return [
        DiscoveredScope(
            kind=SourceKind.GRAPH_DRIVE,
            name=str(me.get("name") or "OneDrive"),
            drive_id="me",
            site=None,
            web_url=_opt_str(me, "webUrl"),
            note=f'your own OneDrive ({me.get("driveType") or "unknown type"}); drive_id = "me"',
        )
    ]


def _discover_shared(client: GraphClient) -> list[DiscoveredScope]:
    """Items shared into the user's OneDrive (remoteItems: each needs its own cursor on the owning drive)."""
    out: list[DiscoveredScope] = []
    for raw in _pages(client, "/me/drive/sharedWithMe"):
        remote = _obj(raw, "remoteItem")
        owner_drive = _opt_str(_obj(remote, "parentReference"), "driveId")
        kind_word = "folder" if "folder" in remote or "folder" in raw else "file"
        out.append(
            DiscoveredScope(
                kind=SourceKind.GRAPH_DRIVE,
                name=str(raw.get("name") or remote.get("name") or "(unnamed)"),
                drive_id=owner_drive,
                site=None,
                web_url=_opt_str(remote, "webUrl") or _opt_str(raw, "webUrl"),
                note=(
                    f"sharedWithMe remoteItem ({kind_word}): needs its own cursor against the owning drive "
                    "(your OneDrive's root delta never enumerates it)"
                ),
            )
        )
    return out


def _discover_sites(client: GraphClient) -> list[DiscoveredScope]:
    """Every document library of every site the user can find."""
    out: list[DiscoveredScope] = []
    sites = list(_pages(client, "/sites", params={"search": "*", "$select": "id,displayName,name,webUrl"}))
    for site in sites:
        site_id = _opt_str(site, "id")
        if not site_id:
            continue
        site_name = str(site.get("displayName") or site.get("name") or site_id)
        site_url = _opt_str(site, "webUrl")
        try:
            drives = list(
                _pages(
                    client,
                    f"/sites/{_quote_segment(site_id)}/drives",
                    params={"$select": "id,name,driveType,webUrl"},
                )
            )
        except GraphError as exc:
            log.warning("discover: libraries of site %r unavailable: %s", site_name, exc)
            continue
        for drive in drives:
            drive_id = _opt_str(drive, "id")
            if not drive_id:
                continue
            out.append(
                DiscoveredScope(
                    kind=SourceKind.GRAPH_DRIVE,
                    name=f"{site_name} / {drive.get('name') or drive_id}",
                    drive_id=drive_id,
                    site=_site_param(site_url),
                    web_url=_opt_str(drive, "webUrl") or site_url,
                    note="document library; prefer drive_id (site = resolves the default library only)",
                )
            )
    return out


def discover(client: GraphClient) -> list[DiscoveredScope]:
    """Enumerate /me/drive, /me/drive/sharedWithMe, /sites?search=* and each site's /drives (Arm 0
    discover)."""
    found: list[DiscoveredScope] = []
    for label, step in (
        ("/me/drive", _discover_me),
        ("/me/drive/sharedWithMe", _discover_shared),
        ("/sites?search=*", _discover_sites),
    ):
        try:
            found.extend(step(client))
        except GraphError as exc:  # one refused endpoint (403, 404) must not hide the others
            log.warning("discover: %s failed: %s", label, exc)
    found.sort(key=lambda s: (s.kind.value, s.name.casefold(), s.drive_id or "", s.web_url or ""))
    unique: list[DiscoveredScope] = []
    seen: set[tuple[str | None, str]] = set()
    for scope in found:
        if (scope.drive_id, scope.name) not in seen:
            seen.add((scope.drive_id, scope.name))
            unique.append(scope)
    return unique


# ---------------------------------------------------------------------------------------------------------
# The arm
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Round:
    """Outcome of draining one delta round (possibly after a 410/400 recovery)."""

    result: DeltaResult
    pass_kind: PassKind
    complete: bool
    cursor_reset: bool
    alarms: tuple[str, ...]


class DriveArm:
    """SourceArm for kind ``graph_drive``."""

    source_id: str
    kind: SourceKind

    def __init__(
        self,
        client: GraphClient,
        cfg: SourceConfig,
        lookup: TreeLookup,
        *,
        save_page_link: Callable[[str | None], None] | None = None,
        resume_link: str | None = None,
    ) -> None:
        """Bind to one drive source.  ``save_page_link`` persists nextLinks (crash resume)."""
        if cfg.kind is not SourceKind.GRAPH_DRIVE:
            raise ValueError(f"source {cfg.id!r} is {cfg.kind.value}, not graph_drive")
        self.source_id = cfg.id
        self.kind = SourceKind.GRAPH_DRIVE
        self._client = client
        self._cfg = cfg
        self._lookup = lookup
        self._save_page_link = save_page_link
        self._resume_link = resume_link
        self._folder = "/" + (cfg.folder or "/").strip("/")
        self._drive_id: str | None = None
        self._root_id: str | None = None
        self._scope_id: str | None = None

    # ---- resolution (lazy; cached for the arm's lifetime, i.e. one cycle) --------------------------------
    @property
    def drive_id(self) -> str:
        """The resolved drive id (GET /me/drive or the site's default library on first use)."""
        if self._drive_id is None:
            self._drive_id = resolve_drive_id(self._client, self._cfg)
        return self._drive_id

    def _drive_path(self) -> str:
        """``/drives/{id}`` for the resolved drive."""
        return f"/drives/{_quote_segment(self.drive_id)}"

    def _scope_root_id(self) -> str:
        """Item id the derived rel_paths are relative to: the scope folder, or the drive root."""
        if self._scope_id is not None:
            return self._scope_id
        if self._folder == "/":
            if self._root_id is None:
                body = self._client.get_json(f"{self._drive_path()}/root", params={"$select": "id"})
                self._root_id = _require_id(body, "drive root")
            self._scope_id = self._root_id
            return self._scope_id
        try:
            body = self._client.get_json(
                f"{self._drive_path()}/root:{quote(self._folder, safe='/')}", params={"$select": "id,name"}
            )
        except GraphNotFound as exc:
            raise GraphNotFound(
                404,
                "scope-folder-not-found",
                f"source {self.source_id!r}: folder {self._folder!r} does not exist in the drive "
                "(renamed or moved? update sources.toml)",
                exc.request_id,
            ) from exc
        self._scope_id = _require_id(body, f"folder {self._folder!r}")
        return self._scope_id

    def scope_root(self) -> str | None:
        """Item id of the configured ``folder`` scope (None = the drive root), for Manifest.rederive_paths."""
        return None if self._folder == "/" else self._scope_root_id()

    def _hint_outside_scope(self, raw: Mapping[str, Any]) -> bool:
        """True when ``parentReference.path`` (undocumented in delta, used only as a HINT) is outside scope.

        Paths are never derived from it (they come from the id tree); it only spares ancestor GETs for
        changes elsewhere in a large library when the scope is a subfolder.
        """
        if self._folder == "/":
            return False
        hint = _opt_str(_obj(raw, "parentReference"), "path")
        if hint is None or "root:" not in hint:
            return False
        parent_path = unquote(hint.split("root:", 1)[1]) or "/"
        parent_path = "/" + parent_path.strip("/")
        folder = self._folder.casefold()
        parent_cf = parent_path.casefold()
        return not (parent_cf == folder or parent_cf.startswith(folder + "/"))

    # ---- delta rounds ------------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        """Headers for every delta request (a fresh dict: the module constant is never mutated)."""
        headers = dict(DELTA_HEADERS)
        headers[_EXCLUDE_PARENT_HEADER] = "true"
        return headers

    def _on_page(self, page: GraphPage) -> None:
        """Persist the resume point of an in-flight FULL enumeration (its value is never logged)."""
        if self._save_page_link is not None and page.next_link:
            self._save_page_link(page.next_link)

    def _full_round(self, start: str | None) -> DeltaResult:
        """Drain one FULL enumeration from ``start`` (a Location/resume link) or token-less."""
        if start is not None:
            return self._client.delta(start, headers=self._headers(), on_page=self._on_page)
        return self._client.delta(
            f"{self._drive_path()}/root/delta",
            params={"$select": DRIVE_SELECT},
            headers=self._headers(),
            on_page=self._on_page,
        )

    def _run_round(self, cursor: str | None, *, full: bool) -> _Round:
        """Run DELTA (stored link) or FULL (token-less / resume) with the 410 / 400 recovery ladder."""
        alarms: list[str] = []
        cursor_reset = False
        delta_link: str | None = None if (full or cursor is None) else cursor
        start: str | None = self._resume_link if delta_link is None else None
        resumed = start is not None
        self._resume_link = None  # a resume link is used at most once
        for _attempt in range(_MAX_ROUND_ATTEMPTS):
            try:
                if delta_link is not None:
                    result = self._client.delta(delta_link, headers=self._headers())
                    return _Round(result, PassKind.DELTA, False, cursor_reset, tuple(alarms))
                result = self._full_round(start)
                if resumed:
                    alarms.append("resumed an interrupted enumeration: absence-based deletion off this pass")
                return _Round(result, PassKind.FULL, not resumed, cursor_reset, tuple(alarms))
            except GraphGone as exc:
                log.warning("source %s: delta 410 (%s): full re-enumeration", self.source_id, exc.code)
                alarms.append(f"cursor expired (410 {exc.code}): full re-enumeration")
                delta_link, start, resumed = None, exc.location, False
            except GraphBadCursor:
                if delta_link is None and start is None:
                    raise  # a token-less round carries no cursor of ours: never loop on it
                what = "resume link rejected" if resumed else "cursor store corrupt"
                log.error(
                    "source %s: 400 on a stored link (%s): dropped, full enumeration", self.source_id, what
                )
                alarms.append(f"{what}: dropped")
                delta_link, start, resumed = None, None, False
            cursor_reset = True
            if self._save_page_link is not None:
                self._save_page_link(None)
        raise GraphError(0, "resync-loop", f"source {self.source_id}: delta kept failing after recovery")

    # ---- scan ------------------------------------------------------------------------------------------
    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Run one delta round; FULL when ``full`` or ``cursor`` is None (or after 410/400).

        Items include folders (is_dir=True) so the id tree stays complete; items outside ``cfg.folder`` are
        dropped after path derivation. enumeration_complete=True only for a FULL round that reached its
        deltaLink. Pending cursor = that deltaLink.
        """
        rnd = self._run_round(cursor, full=full)
        is_full = rnd.pass_kind is PassKind.FULL
        alarms = list(rnd.alarms)

        batch: dict[str, JsonObject] = {}  # last occurrence of an id wins (the feed may repeat items)
        for raw in rnd.result.items:
            ident = raw.get("id")
            if isinstance(ident, str) and ident:
                batch[ident] = raw
        root_ids = sorted(i for i, raw in batch.items() if "root" in raw)
        if root_ids and self._root_id is None:
            self._root_id = root_ids[0]
        scope_id = self._scope_root_id()
        if scope_id in batch and "deleted" in batch[scope_id]:
            alarms.append(f"scope folder {self._folder!r} was deleted upstream")

        # The id tree: this batch's latest (parent, name) over the manifest's rows.
        tree: dict[str, tuple[str | None, str]] = {}
        if self._root_id is not None:
            tree[self._root_id] = (None, "")

        def combined(ident: str) -> tuple[str | None, str] | None:
            hit = tree.get(ident)
            return hit if hit is not None else self._lookup(ident)

        def remember(ident: str, raw: Mapping[str, Any]) -> None:
            if "deleted" in raw:
                return
            if "root" in raw:
                tree[ident] = (None, "")
                return
            name = _opt_str(raw, "name")
            if name is not None:
                tree[ident] = (_parent_id(raw), _nfc(name))

        for ident, raw in batch.items():
            remember(ident, raw)

        fetched = self._resolve_ancestors(batch, scope_id, combined, remember, alarms)

        items: dict[str, SourceItem] = {}
        unknown_dirs: list[str] = []
        shortcuts: list[str] = []
        new_dirs: list[str] = []
        underivable = 0
        candidates = [(i, r, True) for i, r in batch.items()]
        candidates += [(i, r, False) for i, r in fetched.items() if i not in batch]
        for ident, raw, from_feed in candidates:
            if "root" in raw or ident == scope_id:
                continue
            known = self._lookup(ident) is not None
            if "deleted" in raw:
                if from_feed and known:
                    items[ident] = self._tombstone(ident, raw, "deleted")
                continue
            item = item_from_graph(self.source_id, raw, combined, root_id=scope_id)
            if not item.rel_path:
                if known and not is_full:
                    # Moved out of scope: explicit evidence, so a DELTA pass may delete (FULL uses absence).
                    items[ident] = self._tombstone(ident, raw, "moved-out-of-scope")
                elif not known and not self._hint_outside_scope(raw) and self._folder == "/":
                    underivable += 1
                continue
            if "remoteItem" in raw:
                shortcuts.append(item.rel_path)
                unknown_dirs.append(item.rel_path)
                continue
            if not item.is_dir and not is_included(item.rel_path, self._cfg.include, self._cfg.exclude):
                if known and not is_full:
                    items[ident] = self._tombstone(ident, raw, "excluded")
                continue
            if item.is_dir and not known and not is_full:
                new_dirs.append(ident)
            items[ident] = item

        # A folder new to us in a DELTA pass may have been moved in from outside the scope; its descendants
        # produce no delta records, so list them (the next FULL pass reconciles anything this misses).
        for folder_id in sorted(new_dirs):
            for child in self._walk_children(folder_id, tree):
                cid = str(child["id"])
                if cid in items or cid in batch:
                    continue
                child_item = item_from_graph(self.source_id, child, combined, root_id=scope_id)
                if not child_item.rel_path:
                    continue
                if "remoteItem" in child:
                    unknown_dirs.append(child_item.rel_path)
                    continue
                if child_item.is_dir or is_included(
                    child_item.rel_path, self._cfg.include, self._cfg.exclude
                ):
                    items[cid] = child_item

        if shortcuts:
            listed = ", ".join(sorted(shortcuts)[:_ALARM_LIST_LIMIT])
            alarms.append(f"{len(shortcuts)} shortcut/remoteItem entries need their own source: {listed}")
        if underivable:
            alarms.append(f"{underivable} item(s) with an underivable path were skipped until the next FULL")

        ordered = tuple(sorted(items.values(), key=lambda i: (i.rel_path, i.stable_id)))
        log.info(
            "source %s: %s pass, %d page(s), %d feed item(s), %d emitted",
            self.source_id,
            rnd.pass_kind.value,
            rnd.result.pages,
            len(batch),
            len(ordered),
        )
        return ScanResult(
            source_id=self.source_id,
            pass_kind=rnd.pass_kind,
            items=ordered,
            new_cursor=rnd.result.delta_link,
            enumeration_complete=rnd.complete and is_full,
            cursor_reset=rnd.cursor_reset,
            unknown_dirs=tuple(sorted(set(unknown_dirs))),
            alarms=tuple(alarms),
        )

    def _resolve_ancestors(
        self,
        batch: Mapping[str, JsonObject],
        scope_id: str,
        combined: TreeLookup,
        remember: Callable[[str, Mapping[str, Any]], None],
        alarms: list[str],
    ) -> dict[str, JsonObject]:
        """GET parents that neither the batch nor the manifest knows (bounded; hint-skipped when outside)."""
        fetched: dict[str, JsonObject] = {}
        budget = _MAX_ANCESTOR_FETCHES
        exhausted = False
        for ident in sorted(batch):
            raw = batch[ident]
            if "deleted" in raw or "root" in raw or self._hint_outside_scope(raw):
                continue
            pid = _parent_id(raw)
            hops = 0
            while pid is not None and pid != scope_id and combined(pid) is None and hops < _MAX_PATH_DEPTH:
                if budget <= 0:
                    exhausted = True
                    break
                budget -= 1
                hops += 1
                try:
                    parent = self._client.get_json(
                        f"{self._drive_path()}/items/{_quote_segment(pid)}", params={"$select": DRIVE_SELECT}
                    )
                except GraphNotFound:
                    break
                fetched[pid] = parent
                remember(pid, parent)
                if "root" in parent:
                    if self._root_id is None:
                        self._root_id = pid
                    break
                pid = _parent_id(parent)
        if exhausted:
            alarms.append("ancestor lookups capped: some paths stay underivable until the next FULL")
        return fetched

    def _tombstone(self, ident: str, raw: Mapping[str, Any], reason: str) -> SourceItem:
        """An explicit ``deleted=True`` item carrying the last known path/name from the manifest."""
        known = self._lookup(ident)
        name = known[1] if known else _nfc(_opt_str(raw, "name") or "")
        parent = known[0] if known else _parent_id(raw)
        # The scope root (drive root or folder) is never a manifest row, so walk to its id explicitly.
        rel_path = _derive_path(parent, name, self._lookup, self._scope_root_id()) if name else None
        mtime_ns = _parse_graph_time_ns(raw.get("lastModifiedDateTime")) or 0
        return SourceItem(
            source_id=self.source_id,
            stable_id=ident,
            rel_path=_nfc(rel_path or ""),
            name=name,
            size=0,
            mtime_ns=mtime_ns,
            ctime_ns=mtime_ns,
            is_dir=_is_dir(raw),
            etag=_opt_str(raw, "eTag"),
            deleted=True,
            parent_id=parent,
            extra={"removed": reason},
        )

    def _walk_children(self, folder_id: str, tree: dict[str, tuple[str | None, str]]) -> Iterator[JsonObject]:
        """Yield every descendant of ``folder_id`` via /children paging (breadth-first; registers names)."""
        queue = [folder_id]
        visited: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            path = f"{self._drive_path()}/items/{_quote_segment(current)}/children"
            try:
                children = list(_pages(self._client, path, params={"$select": DRIVE_SELECT}))
            except GraphNotFound:
                continue
            for listed in children:
                cid = listed.get("id")
                if not isinstance(cid, str) or not cid or "deleted" in listed:
                    continue
                child = dict(listed)
                if _parent_id(child) is None:
                    child["parentReference"] = {**_obj(child, "parentReference"), "id": current}
                name = _opt_str(child, "name")
                if name is not None:
                    tree[cid] = (current, _nfc(name))
                if _is_dir(child) and "remoteItem" not in child:
                    queue.append(cid)
                yield child

    # ---- fetch -----------------------------------------------------------------------------------------
    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """GET /drives/{d}/items/{id}/content into ``dest_dir`` charging ``budget`` with item.size first."""
        if item.source_id != self.source_id:
            raise ValueError(f"item of source {item.source_id!r} fetched through {self.source_id!r}")
        if item.is_dir or item.deleted:
            raise ValueError(f"{item.stable_id}: directories and tombstones have no content")
        headroom = budget.remaining_bytes  # the served bytes may differ from item.size (enrichment, labels)
        budget.charge(item.size)  # raises BudgetExhaustedError before any byte moves
        dest_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(item.stable_id.encode("utf-8")).hexdigest()[:16]
        dest = dest_dir / f"{digest}{item.suffix}"
        url = f"{self._drive_path()}/items/{_quote_segment(item.stable_id)}/content"
        try:
            size, sha = self._client.download(url, dest, max_bytes=headroom)
        except Exception:
            _refund(budget, item.size)
            raise
        budget.used += size - item.size  # account for what actually moved
        if size != item.size:
            log.info(
                "source %s: %s served %d bytes, listed %d", self.source_id, item.stable_id, size, item.size
            )
        return FetchResult(stable_id=item.stable_id, path=dest, size=size, content_sha256=sha)
