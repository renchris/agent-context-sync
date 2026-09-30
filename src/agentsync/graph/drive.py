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
from typing import Any, NoReturn
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
    "createdDateTime,webUrl,remoteItem,publication,pendingOperations,malware,lastModifiedBy"
)
"""$select for drive delta: ``file`` MUST be present or quickXorHash is dropped for every item.

Every top-level field the arm, the classifier, the quiescence gate and the frontmatter consume is listed
(design 4.2 CORRECTED; audit design-correctness-04): ``cTag`` (classifier rung 2), ``package`` (OneNote),
``publication`` and ``pendingOperations`` (quiescence / quarantine; ``publication`` "isn't returned by
default"), ``malware`` (quarantine), ``createdDateTime`` (arm precedence), ``lastModifiedBy`` and ``webUrl``
(frontmatter), ``remoteItem`` (shortcuts). ``tests/test_graph_drive.py`` fails when the mapping reads a
top-level field that is not selected. Query options are encoded into the deltaLink, so a change here only
reaches an existing source after its next FULL re-enumeration.

``driveItem`` has NO sensitivity-label property (C15 section 4): a label is read only through the per-item
``POST .../extractSensitivityLabels`` action, never selected here.
"""

DELTA_HEADERS: dict[str, str] = {"deltaExcludeParent": "true"}
"""Request headers of every drive delta call. ``deltaExcludeParent`` is a request header of its own (the
driveItem:delta "Request headers" table; receipt B1), not a ``Prefer`` value (audit design-correctness-17)."""

log = logging.getLogger(__name__)

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


def _path_scope(parent_id: str | None, lookup: TreeLookup, root_id: str | None) -> str:
    """Where ``parent_id`` lies relative to the scope root: ``inside``, ``outside`` (the chain reaches a
    root that is not the scope: positive evidence of a move out) or ``unknown`` (an ancestor is not known,
    refused or the chain loops: no evidence either way).  Same walk as :func:`_derive_path`."""
    current = parent_id
    seen: set[str] = set()
    while True:
        if current is None:
            return "inside" if root_id is None else "outside"
        if current == root_id:
            return "inside"
        if current in seen or len(seen) > _MAX_PATH_DEPTH:
            return "unknown"
        seen.add(current)
        entry = lookup(current)
        if entry is None:
            return "unknown"
        up, _name = entry
        if up is None:
            return "inside" if root_id is None else "outside"
        current = up


def item_from_graph(
    source_id: str, raw: JsonObject, lookup: TreeLookup, *, root_id: str | None = None
) -> SourceItem:
    """Map one driveItem to a SourceItem: stable_id = id; parent_id; rel_path derived from the id tree
    (``lookup`` for parents not in this batch; "" if underivable); remote_hashes.quickxor from
    file.hashes.quickXorHash; etag/ctag opaque; ``deleted`` facet -> deleted=True (name may be absent);
    extra carries web_url, package type, publication level, sensitivity label when present.

    ``root_id`` (extension) makes rel_path relative to a scope folder; None = relative to the drive root.

    Labels (C15 section 4, audit design-correctness-05): delta carries no label property, so
    ``sensitivity_label`` is never set here. A label change on an Office file rewrites package parts
    (``docMetadata/LabelInfo.xml``, ``docProps/custom.xml``), so its ``quickXorHash`` moves along with the
    eTag: the item reaches the classifier as a content change (MAYBE_CHANGED), never METADATA_ONLY.
    Also mapped (extension): ``last_modified_by`` (frontmatter), ``pending_operations`` (quiescence gate:
    a pending content change means the served bytes are not final yet), ``malware`` (quarantine).
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
    modified_by = _obj(raw, "lastModifiedBy")
    for key in ("user", "application", "device"):
        who = _opt_str(_obj(modified_by, key), "displayName")
        if who:
            extra["last_modified_by"] = _nfc(who)
            break
    pending = _obj(raw, "pendingOperations")
    if pending:
        extra["pending_operations"] = ",".join(sorted(pending))
    if raw.get("malware") is not None:  # a selected facet may come back as null: only an object counts
        extra["malware"] = _opt_str(_obj(raw, "malware"), "description") or "detected"
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


_DRIVE_IT_ACTION = (
    "ask IT for tenant admin consent to Files.Read.All (or Sites.Read.All) on the agentsync app "
    "registration, or a Sites.Selected grant on this site (C15 section 2)"
)


def _drive_access_error(exc: GraphError, what: str) -> GraphError:
    """Re-raise a drive-level 403 / 404 as a named, actionable error (never read as an empty drive).

    Design 4.2 CORRECTED: lost access is ``access-unknown``, not a tombstone. Raising (instead of returning
    an empty pass) keeps every row, stops the cursor and puts the IT action in STATE.md.
    """
    if exc.status == 403:
        return GraphError(
            403, "drive-access-denied", f"{what}: access denied; {_DRIVE_IT_ACTION}", exc.request_id
        )
    if exc.status == 404:
        return GraphNotFound(
            404,
            "drive-not-found",
            f"{what}: not found (deleted, renamed or no longer shared with you): check the source in "
            "sources.toml (agentsync discover lists what this account can see)",
            exc.request_id,
        )
    return exc


def _raise_access(exc: GraphError, what: str) -> NoReturn:
    """Raise the named form of ``exc`` (chained), or ``exc`` itself when it is not a 403 / 404."""
    named = _drive_access_error(exc, what)
    if named is exc:
        raise exc
    raise named from exc


def resolve_drive_id(client: GraphClient, cfg: SourceConfig) -> str:
    """``drive_id="me"`` -> GET /me/drive; ``site`` -> GET /sites/{host}:/{path} then /sites/{id}/drive."""
    if cfg.drive_id:
        if cfg.drive_id != "me":
            return cfg.drive_id
        try:
            return _require_id(client.get_json("/me/drive", params={"$select": "id"}), "/me/drive")
        except GraphError as exc:
            _raise_access(exc, f"source {cfg.id!r}: your OneDrive (/me/drive)")
    if not cfg.site:
        raise GraphError(0, "config", f"source {cfg.id!r}: graph_drive needs drive_id or site")
    site = cfg.site.strip()
    if ":" in site:
        host, _, path = site.partition(":")
        site_path = f"/sites/{host.strip()}:{quote('/' + path.strip().strip('/'), safe='/')}"
    else:
        site_path = f"/sites/{_quote_segment(site)}"  # a site id ("host,guid,guid") or "root"
    try:
        site_id = _require_id(client.get_json(site_path, params={"$select": "id"}), f"site {site!r}")
        drive = client.get_json(f"/sites/{_quote_segment(site_id)}/drive", params={"$select": "id"})
    except GraphError as exc:
        _raise_access(exc, f"source {cfg.id!r}: site {site!r}")
    return _require_id(drive, f"default library of site {site!r}")


@dataclass(frozen=True, slots=True)
class DiscoveredScope:
    """One candidate source found by ``discover`` (printed for a human to accept into sources.toml).

    Extension (defaults keep the contract's six-field form valid): ``folder`` (drive subtree, or the mail
    folder id), ``mailbox`` (graph_mail), ``team_id`` / ``channel_id`` (graph_teams; ``team_id = "chats"``
    marks a 1:1 / group chat) and ``configured`` (the id of an existing source already covering it).
    """

    kind: SourceKind
    name: str
    drive_id: str | None
    site: str | None
    web_url: str | None
    note: str  # e.g. "shortcut in My files: needs its own source against the owning drive"
    folder: str | None = None
    mailbox: str | None = None
    team_id: str | None = None
    channel_id: str | None = None
    configured: str | None = None


def _site_param(web_url: str | None) -> str | None:
    """``https://host/sites/x`` -> ``host:/sites/x`` (the sources.toml ``site`` form)."""
    if not web_url:
        return None
    parts = urlsplit(web_url)
    if not parts.netloc:
        return None
    return f"{parts.netloc}:{unquote(parts.path) or '/'}"


def discover(client: GraphClient) -> list[DiscoveredScope]:
    """Enumerate /me/drive, /me/drives, /me/followedSites and their libraries, shortcuts in My files,
    /me/joinedTeams channels, /me/chats and /me/mailFolders (Arm 0 discover).

    Thin wrapper over :func:`agentsync.graph.discover.discover_sources` (which also reports refused
    endpoints with their IT action and renders a sources.toml snippet). The deprecated shared-with-me
    listing is never called: it stops returning data after November 2026 (C15 section 2).
    """
    from agentsync.graph.discover import discover_sources  # noqa: PLC0415 - discover imports this module

    return list(discover_sources(client).scopes)


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
        except GraphError as exc:
            _raise_access(exc, f"source {self.source_id!r}: folder {self._folder!r}")
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
        return dict(DELTA_HEADERS)

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
                    alarms.append(
                        "resumed an interrupted enumeration: absence-based deletion off this pass; its "
                        "cursor "
                        "is not kept (the next cycle enumerates again from the start)"
                    )
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
            except GraphError as exc:
                # Drive-level 403 / 404 (401 arrives as AuthRequiredError): the whole scope is
                # access-unknown. Raise, never return an empty pass that absence-based deletion would read.
                _raise_access(exc, f"source {self.source_id!r}: drive delta")
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
        held_known: list[str] = []  # known items whose new place cannot be derived: left untouched
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
                if known:
                    where = _path_scope(_parent_id(raw), combined, scope_id)
                    if where == "unknown" and self._root_id is None and self._resolve_root(tree):
                        where = _path_scope(_parent_id(raw), combined, scope_id)  # the root may end it
                    if where == "outside" or (where == "unknown" and self._hint_outside_scope(raw)):
                        # Derivably outside the scope: positive evidence of a move (in a FULL pass too,
                        # where absence would otherwise read as an upstream deletion and queue a purge).
                        items[ident] = self._tombstone(ident, raw, "moved-out-of-scope")
                    else:
                        # Underivable (an ancestor refused, unknown or past the lookup cap): no evidence
                        # of anything; the row keeps its last path, and a FULL pass is not complete.
                        held_known.append(ident)
                elif not self._hint_outside_scope(raw) and self._folder == "/":
                    underivable += 1
                continue
            if "remoteItem" in raw:
                shortcuts.append(item.rel_path)
                unknown_dirs.append(item.rel_path)
                continue
            if not item.is_dir and not is_included(item.rel_path, self._cfg.include, self._cfg.exclude):
                if known:  # excluded by the (new) globs: a scope decision, never an upstream deletion
                    items[ident] = self._tombstone(ident, raw, "excluded")
                continue
            if item.is_dir and not known and not is_full:
                new_dirs.append(ident)
            items[ident] = item

        # A folder new to us in a DELTA pass may have been moved in from outside the scope; its descendants
        # produce no delta records, so list them (the next FULL pass reconciles anything this misses).
        refused: list[str] = []
        for folder_id in sorted(new_dirs):
            for child in self._walk_children(folder_id, tree, refused):
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

        for folder_id in refused:
            known_entry = combined(folder_id)
            path = _derive_path(known_entry[0], known_entry[1], combined, scope_id) if known_entry else None
            unknown_dirs.append(_nfc(path) if path else folder_id)
        if refused:
            alarms.append(
                f"{len(refused)} folder listing(s) refused (403, item-level permissions): contents unknown"
            )
        if shortcuts:
            listed = ", ".join(sorted(shortcuts)[:_ALARM_LIST_LIMIT])
            alarms.append(f"{len(shortcuts)} shortcut/remoteItem entries need their own source: {listed}")
        if underivable:
            alarms.append(f"{underivable} item(s) with an underivable path were skipped until the next FULL")
        if held_known:
            alarms.append(
                f"{len(held_known)} known item(s) moved where their path cannot be derived (unreadable or "
                "unknown ancestor): left as they were, nothing removed"
                + ("; enumeration incomplete" if is_full else "")
            )

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
            # A resumed FULL round never stages its deltaLink: the pages before the interruption were
            # consumed but never applied, so promoting it would skip their items until some later
            # uninterrupted FULL (review correctness-resumed-enumeration).  Without it the next cycle runs a
            # fresh token-less FULL (no cursor) or hits the old link's 410 and resyncs.
            new_cursor=rnd.result.delta_link if (rnd.complete or not is_full) else None,
            enumeration_complete=rnd.complete and is_full and not held_known,
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
        denied: set[str] = set()
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
                except GraphError as exc:
                    # Per item, not per drive: a vanished (404) or unreadable (403: item-level permission,
                    # broken inheritance) ancestor leaves this item underivable; the drive stays readable.
                    if exc.status == 403:
                        denied.add(pid)
                    elif exc.status != 404:
                        raise
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
        if denied:
            alarms.append(
                f"{len(denied)} ancestor folder(s) refused (403, item-level permissions): their items stay "
                "underivable and are not published"
            )
        return fetched

    def _resolve_root(self, tree: dict[str, tuple[str | None, str]]) -> bool:
        """Learn the drive root's id (one GET) so a chain that climbs out of a folder scope ends there;
        False when it cannot be read (the item then stays underivable, never removed)."""
        try:
            body = self._client.get_json(f"{self._drive_path()}/root", params={"$select": "id"})
            self._root_id = _require_id(body, "drive root")
        except GraphError as exc:
            log.info("source %s: drive root unreadable (%s)", self.source_id, exc.code)
            return False
        tree[self._root_id] = (None, "")
        return True

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

    def _walk_children(
        self, folder_id: str, tree: dict[str, tuple[str | None, str]], denied: list[str]
    ) -> Iterator[JsonObject]:
        """Yield every descendant of ``folder_id`` via /children paging (breadth-first; registers names).

        A folder whose listing is refused (403) is appended to ``denied``: its content is unknown, never
        empty. A vanished one (404) is skipped (the next delta carries its tombstone).
        """
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
            except GraphError as exc:
                if exc.status == 403:
                    denied.append(current)
                elif exc.status != 404:
                    raise
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
        except GraphError as exc:
            _refund(budget, item.size)
            # Per item: the cycle turns this into one ERROR row (retried next cycle); the drive goes on.
            if exc.status == 403:
                raise GraphError(
                    403,
                    "item-access-denied",
                    f"{item.rel_path or item.stable_id}: access denied on this item (item-level "
                    "permission, broken inheritance or a label that blocks download); the rest of the drive "
                    "is unaffected",
                    exc.request_id,
                ) from exc
            if exc.status == 404:
                raise GraphNotFound(
                    404,
                    "item-not-found",
                    f"{item.rel_path or item.stable_id}: gone upstream since the scan; the next delta pass "
                    "carries its tombstone",
                    exc.request_id,
                ) from exc
            raise
        except Exception:
            _refund(budget, item.size)
            raise
        budget.used += size - item.size  # account for what actually moved
        if size != item.size:
            log.info(
                "source %s: %s served %d bytes, listed %d", self.source_id, item.stable_id, size, item.size
            )
        return FetchResult(stable_id=item.stable_id, path=dest, size=size, content_sha256=sha)
