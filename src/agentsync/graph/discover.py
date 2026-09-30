"""Arm 0 discovery: propose sources for a human to accept into sources.toml (owner: graph-arms).

Replaces the deprecated shared-with-me listing, which "will operate in a degraded state until November,
2026, after which it will stop returning data" (C15 section 2; audit design-correctness-08). The route is
*enumerate what the account owns or follows, then declare-and-resolve*:

- ``/me/drive`` and ``/me/drives``: the user's own OneDrive (``drive_id = "me"``) and any other drive.
- ``/me/followedSites`` -> ``/sites/{id}/drives``: every document library of every followed site.
- ``/me/drive/root/children`` ``remoteItem`` entries: shortcuts the user added to "My files", resolved to
  the owning drive and folder (the root delta of the user's own drive never enumerates their contents).
- ``/me/joinedTeams`` -> ``/teams/{id}/channels``: one graph_teams candidate per channel.
- ``/me/chats`` (most recent first, capped): one graph_teams candidate per chat (``team_id = "chats"``).
- ``/me/mailFolders/delta`` (plus ``childFolders`` for any folder whose children the delta did not list):
  one graph_mail candidate per folder; every folder not yet configured is proposed, none is skipped.
- :func:`resolve_url` resolves an operator-supplied sharing or site URL through
  ``/shares/u!{base64url}/driveItem`` or ``/sites/{host}:/{path}``.

A refused endpoint (401 / 403) never hides the others and never reads as "nothing there": it becomes a
:class:`DiscoveryFailure` naming the IT action, and the report is marked incomplete. Nothing here edits
sources.toml; :func:`render_sources_toml` prints a snippet whose every table starts ``state = "paused"``.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import quote, unquote, urlsplit

from agentsync.config import SOURCE_ID_RE, SourceConfig
from agentsync.errors import AuthRequiredError, GraphError
from agentsync.graph.client import GraphClient, JsonObject
from agentsync.graph.drive import DiscoveredScope, _obj, _opt_str, _quote_segment, _site_param
from agentsync.graph.teams import _CHATS_TEAM_ID
from agentsync.model import SourceKind

log = logging.getLogger(__name__)

MAX_DISCOVERED_CHATS = 100
"""Chats proposed at most (most recent first): a long-lived account can have thousands."""

_IMMUTABLE = {"Prefer": 'IdType="ImmutableId"'}
_MAX_MAIL_DEPTH = 32
_SHARING_MARKERS = re.compile(r"^/:[a-z]:/", re.IGNORECASE)  # /:f:/r/..., /:w:/g/... sharing links
_SITE_PATH = re.compile(r"^/(sites|teams|personal)/[^/]+", re.IGNORECASE)

# Least-privileged delegated scope behind each discovery endpoint (C15 section 2 matrix).
_SCOPE_FOR = {
    "/me/drive": "Files.Read",
    "/me/drives": "Files.Read",
    "/me/followedSites": "Sites.Read.All",
    "/sites": "Sites.Read.All",
    "/me/drive/root/children": "Files.Read",
    "/drives": "Files.Read.All",
    "/me/joinedTeams": "Team.ReadBasic.All",
    "/teams": "Channel.ReadBasic.All",
    "/me/chats": "Chat.ReadBasic",
    "/me/mailFolders": "Mail.ReadBasic",
    "/shares": (
        "Files.ReadWrite (the documented least privilege; Files.Read.All is unverified, "
        "C15 section 8 probe 5)"
    ),
}


@dataclass(frozen=True, slots=True)
class DiscoveryFailure:
    """One endpoint discovery could not read, with the action that unblocks it."""

    endpoint: str  # the Graph path family, never a URL with a token
    status: int
    code: str
    action: str


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """Everything one discovery run found, plus what it could not see."""

    scopes: tuple[DiscoveredScope, ...]
    failures: tuple[DiscoveryFailure, ...] = ()

    @property
    def complete(self) -> bool:
        """True when every endpoint answered (STATE.md / the CLI say ``incomplete`` otherwise)."""
        return not self.failures

    @property
    def new_scopes(self) -> tuple[DiscoveredScope, ...]:
        """Candidates no configured source covers yet."""
        return tuple(s for s in self.scopes if s.configured is None)


def it_action(endpoint: str, status: int, code: str = "") -> str:
    """The named action for a refused discovery endpoint (401 / 403 / 404 / other)."""
    family = next((k for k in sorted(_SCOPE_FOR, key=len, reverse=True) if endpoint.startswith(k)), "")
    scope = _SCOPE_FOR.get(family, "the scope this endpoint needs")
    if status == 401:
        return (
            "sign in again (agentsync login); if 401 persists after a fresh sign-in, ask IT whether a "
            "Conditional Access / token-protection policy blocks this client (C15 section 8 probe 4)"
        )
    if status == 403:
        return f"ask IT for tenant admin consent to {scope} on the agentsync app registration (C15 section 2)"
    if status == 404:
        return "not found: the URL is wrong, the item was deleted, or it is not shared with this account"
    return f"Graph {status} {code or 'error'}: retry later; if it persists, report it with the request id"


def _failure(endpoint: str, exc: GraphError | AuthRequiredError) -> DiscoveryFailure:
    """A DiscoveryFailure for ``exc`` raised by ``endpoint``."""
    if isinstance(exc, AuthRequiredError):
        return DiscoveryFailure(endpoint, 401, "reauth-required", it_action(endpoint, 401))
    return DiscoveryFailure(endpoint, exc.status, exc.code, it_action(endpoint, exc.status, exc.code))


def _values(client: GraphClient, path: str, params: Mapping[str, str] | None = None) -> Iterator[JsonObject]:
    """Every object of a paged collection."""
    for page in client.iter_pages(path, params=params):
        yield from page.value


class _Run:
    """One discovery run: collects scopes and failures; stops probing after a sign-in failure."""

    def __init__(self, client: GraphClient) -> None:
        self.client = client
        self.scopes: list[DiscoveredScope] = []
        self.failures: list[DiscoveryFailure] = []
        self.signed_out = False

    def step(self, endpoint: str, fn: Callable[[], Iterable[DiscoveredScope]]) -> None:
        """Run one discovery step; a refusal is recorded, never raised (one endpoint never hides another)."""
        if self.signed_out:
            return
        try:
            self.scopes.extend(fn())
        except AuthRequiredError as exc:
            self.failures.append(_failure(endpoint, exc))
            self.signed_out = True  # every later call would fail the same way
        except GraphError as exc:
            log.warning("discover: %s refused (%d %s)", endpoint, exc.status, exc.code)
            self.failures.append(_failure(endpoint, exc))


# ---------------------------------------------------------------------------------------------------------
# drives and libraries
# ---------------------------------------------------------------------------------------------------------


def _own_drive(client: GraphClient, me: dict[str, str]) -> list[DiscoveredScope]:
    """The signed-in user's own OneDrive (``drive_id = "me"``); records its id to de-duplicate."""
    body = client.get_json("/me/drive", params={"$select": "id,name,driveType,webUrl"})
    me["id"] = _opt_str(body, "id") or ""
    return [
        DiscoveredScope(
            kind=SourceKind.GRAPH_DRIVE,
            name=f"OneDrive ({_opt_str(body, 'name') or 'yours'})",
            drive_id="me",
            site=None,
            web_url=_opt_str(body, "webUrl"),
            note=f"your own OneDrive ({_opt_str(body, 'driveType') or 'unknown type'})",
        )
    ]


def _my_drives(client: GraphClient, me: dict[str, str]) -> list[DiscoveredScope]:
    """Other drives the user owns (``/me/drives``), minus the one ``/me/drive`` already proposed."""
    out: list[DiscoveredScope] = []
    for drive in _values(client, "/me/drives", {"$select": "id,name,driveType,webUrl"}):
        drive_id = _opt_str(drive, "id")
        if not drive_id or drive_id == me.get("id"):
            continue
        out.append(
            DiscoveredScope(
                kind=SourceKind.GRAPH_DRIVE,
                name=f"Drive {_opt_str(drive, 'name') or drive_id}",
                drive_id=drive_id,
                site=None,
                web_url=_opt_str(drive, "webUrl"),
                note=f"one of your drives ({_opt_str(drive, 'driveType') or 'unknown type'})",
            )
        )
    return out


def _site_libraries(client: GraphClient, site: Mapping[str, object], note: str) -> list[DiscoveredScope]:
    """Every document library of one site."""
    site_id = _opt_str(site, "id")
    if not site_id:
        return []
    site_name = str(site.get("displayName") or site.get("name") or site_id)
    site_url = _opt_str(site, "webUrl")
    out: list[DiscoveredScope] = []
    path = f"/sites/{_quote_segment(site_id)}/drives"
    for drive in _values(client, path, {"$select": "id,name,driveType,webUrl"}):
        drive_id = _opt_str(drive, "id")
        if not drive_id:
            continue
        out.append(
            DiscoveredScope(
                kind=SourceKind.GRAPH_DRIVE,
                name=f"{site_name} / {_opt_str(drive, 'name') or drive_id}",
                drive_id=drive_id,
                site=_site_param(site_url),
                web_url=_opt_str(drive, "webUrl") or site_url,
                note=note,
            )
        )
    return out


def _followed_sites(run: _Run) -> list[DiscoveredScope]:
    """Libraries of every site the user follows (added to ``run``); one refused site is a failure only."""
    sites = list(_values(run.client, "/me/followedSites", {"$select": "id,displayName,name,webUrl"}))
    for site in sorted(sites, key=lambda s: str(s.get("id") or "")):
        run.step(
            f"/sites/{{id}}/drives ({site.get('displayName') or site.get('id')})",
            functools.partial(_site_libraries, run.client, site, "document library of a site you follow"),
        )
    return []


def _item_folder(parent_path: str | None, name: str | None, is_folder: bool) -> str | None:
    """``parentReference.path`` (``/drives/x/root:/A/B``) + name -> ``/A/B/name`` (folder) or ``/A/B``."""
    if parent_path is None or "root:" not in parent_path:
        return None
    base = "/" + unquote(parent_path.split("root:", 1)[1]).strip("/")
    if not is_folder:
        return base
    return (base.rstrip("/") + "/" + (name or "")).replace("//", "/") if name else None


def _shortcuts(client: GraphClient) -> list[DiscoveredScope]:
    """Shortcuts in "My files" (``remoteItem``): each needs its own source against the owning drive."""
    out: list[DiscoveredScope] = []
    for raw in _values(client, "/me/drive/root/children", {"$select": "id,name,remoteItem,webUrl"}):
        remote = _obj(raw, "remoteItem")
        owner_drive = _opt_str(_obj(remote, "parentReference"), "driveId")
        remote_id = _opt_str(remote, "id")
        if not remote or not owner_drive or not remote_id:
            continue
        name = _opt_str(raw, "name") or _opt_str(remote, "name") or remote_id
        is_folder = "folder" in remote
        folder: str | None = None
        note = "shortcut in My files: its own source against the owning drive"
        try:
            item = client.get_json(
                f"/drives/{_quote_segment(owner_drive)}/items/{_quote_segment(remote_id)}",
                params={"$select": "id,name,folder,parentReference"},
            )
            folder = _item_folder(
                _opt_str(_obj(item, "parentReference"), "path"), _opt_str(item, "name"), "folder" in item
            )
        except GraphError as exc:
            note += f"; owning drive not readable ({exc.status}: {it_action('/drives', exc.status)})"
        if folder is None:
            note += "; folder path unresolved: set folder by hand"
        elif not is_folder:
            note += f"; a single file ({name}): its parent folder is proposed, narrow with include"
        out.append(
            DiscoveredScope(
                kind=SourceKind.GRAPH_DRIVE,
                name=f"Shortcut {name}",
                drive_id=owner_drive,
                site=None,
                web_url=_opt_str(remote, "webUrl") or _opt_str(raw, "webUrl"),
                note=note,
                folder=folder,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------------
# Teams channels and chats
# ---------------------------------------------------------------------------------------------------------


def _channels(run: _Run) -> list[DiscoveredScope]:
    """Every channel of every joined team; one refused team is a failure, not a stop."""
    teams = list(_values(run.client, "/me/joinedTeams", {"$select": "id,displayName"}))

    def team_channels(team: Mapping[str, object]) -> list[DiscoveredScope]:
        team_id = str(team.get("id"))
        team_name = str(team.get("displayName") or team_id)
        path = f"/teams/{_quote_segment(team_id)}/channels"
        out: list[DiscoveredScope] = []
        for ch in _values(run.client, path, {"$select": "id,displayName,membershipType,webUrl"}):
            channel_id = _opt_str(ch, "id")
            if not channel_id:
                continue
            membership = _opt_str(ch, "membershipType") or "standard"
            out.append(
                DiscoveredScope(
                    kind=SourceKind.GRAPH_TEAMS,
                    name=f"{team_name} / {_opt_str(ch, 'displayName') or channel_id}",
                    drive_id=None,
                    site=None,
                    web_url=_opt_str(ch, "webUrl"),
                    note=f"{membership} channel; needs ChannelMessage.Read.All (admin consent)",
                    team_id=team_id,
                    channel_id=channel_id,
                )
            )
        return out

    for team in sorted(teams, key=lambda t: str(t.get("id") or "")):
        if _opt_str(team, "id"):
            run.step(
                f"/teams/{{id}}/channels ({team.get('displayName') or team.get('id')})",
                functools.partial(team_channels, team),
            )
    return []


def _chats(client: GraphClient, limit: int) -> list[DiscoveredScope]:
    """The ``limit`` most recently active 1:1 / group / meeting chats."""
    out: list[DiscoveredScope] = []
    params = {"$top": "50", "$orderby": "lastMessagePreview/createdDateTime desc"}
    for chat in _values(client, "/me/chats", params):
        chat_id = _opt_str(chat, "id")
        if not chat_id:
            continue
        chat_type = _opt_str(chat, "chatType") or "chat"
        out.append(
            DiscoveredScope(
                kind=SourceKind.GRAPH_TEAMS,
                name=f"Chat {_opt_str(chat, 'topic') or f'{chat_type} {chat_id[:12]}'}",
                drive_id=None,
                site=None,
                web_url=_opt_str(chat, "webUrl"),
                note=f"{chat_type} chat; needs Chat.Read (admin consent in a default tenant)",
                team_id=_CHATS_TEAM_ID,
                channel_id=chat_id,
            )
        )
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------------------------------------
# mail folders
# ---------------------------------------------------------------------------------------------------------


def _mail_folders(client: GraphClient) -> list[DiscoveredScope]:
    """Every mail folder: ``/me/mailFolders/delta`` then ``childFolders`` where the delta missed children."""
    select = "id,displayName,parentFolderId,childFolderCount,totalItemCount"
    folders: dict[str, JsonObject] = {}
    for page in client.iter_pages("/me/mailFolders/delta", params={"$select": select}, headers=_IMMUTABLE):
        for raw in page.value:
            ident = _opt_str(raw, "id")
            if ident and "@removed" not in raw:
                folders[ident] = raw
    # The delta reference does not say whether nested folders are included: complete the tree explicitly.
    queue = sorted(folders)
    depth = 0
    while queue and depth < _MAX_MAIL_DEPTH:
        depth += 1
        nxt: list[str] = []
        for ident in queue:
            raw = folders[ident]
            expected = raw.get("childFolderCount")
            listed = sum(1 for f in folders.values() if f.get("parentFolderId") == ident)
            if not isinstance(expected, int) or expected <= listed:
                continue
            path = f"/me/mailFolders/{_quote_segment(ident)}/childFolders"
            for child in _values_with(client, path, {"$select": select}, _IMMUTABLE):
                cid = _opt_str(child, "id")
                if cid and cid not in folders:
                    folders[cid] = child
                    nxt.append(cid)
        queue = sorted(nxt)

    def full_name(ident: str) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        current: str | None = ident
        while current in folders and current not in seen:
            seen.add(current)
            raw = folders[current]
            parts.append(_opt_str(raw, "displayName") or current)
            current = _opt_str(raw, "parentFolderId")
        return "/".join(reversed(parts))

    out: list[DiscoveredScope] = []
    for ident, raw in folders.items():
        count = raw.get("totalItemCount")
        out.append(
            DiscoveredScope(
                kind=SourceKind.GRAPH_MAIL,
                name=f"Mail {full_name(ident)}",
                drive_id=None,
                site=None,
                web_url=None,
                note=f"mail folder, {count if isinstance(count, int) else '?'} messages, own cursor",
                folder=ident,
                mailbox="me",
            )
        )
    return out


def _values_with(
    client: GraphClient, path: str, params: Mapping[str, str], headers: Mapping[str, str]
) -> Iterator[JsonObject]:
    """Every object of a paged collection, with request headers."""
    for page in client.iter_pages(path, params=params, headers=headers):
        yield from page.value


# ---------------------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------------------


def _key(scope: DiscoveredScope) -> tuple[str, ...]:
    """Identity of a candidate (what makes two candidates the same source)."""
    if scope.kind is SourceKind.GRAPH_DRIVE:
        return (scope.kind.value, scope.drive_id or "", (scope.folder or "/").casefold())
    if scope.kind is SourceKind.GRAPH_MAIL:
        return (scope.kind.value, (scope.mailbox or "me").casefold(), scope.folder or "")
    return (scope.kind.value, scope.team_id or "", scope.channel_id or "")


def _config_key(cfg: SourceConfig) -> tuple[str, ...] | None:
    """The candidate identity a configured source covers (None for local / inbox)."""
    if cfg.kind is SourceKind.GRAPH_DRIVE and cfg.drive_id:
        return (cfg.kind.value, cfg.drive_id, ("/" + (cfg.folder or "/").strip("/")).casefold())
    if cfg.kind is SourceKind.GRAPH_MAIL:
        return (cfg.kind.value, (cfg.mailbox or "me").casefold(), cfg.folder or "")
    if cfg.kind is SourceKind.GRAPH_TEAMS:
        return (cfg.kind.value, cfg.team_id or "", cfg.channel_id or "")
    return None


def _finish(scopes: Iterable[DiscoveredScope], known: Sequence[SourceConfig]) -> tuple[DiscoveredScope, ...]:
    """De-duplicate, mark configured candidates, sort deterministically."""
    covered = {k: c.id for c in known if (k := _config_key(c)) is not None}
    unique: dict[tuple[str, ...], DiscoveredScope] = {}
    for scope in scopes:
        norm = scope
        if scope.kind is SourceKind.GRAPH_DRIVE and scope.folder:
            norm = replace(scope, folder="/" + scope.folder.strip("/"))
        key = _key(norm)
        if key not in unique:
            unique[key] = replace(norm, configured=covered.get(key))
    return tuple(
        sorted(unique.values(), key=lambda s: (s.kind.value, s.name.casefold(), _key(s), s.web_url or ""))
    )


def discover_sources(
    client: GraphClient,
    *,
    known: Sequence[SourceConfig] = (),
    max_chats: int = MAX_DISCOVERED_CHATS,
) -> DiscoveryReport:
    """Enumerate drives, followed sites' libraries, shortcuts, channels, chats and mail folders.

    ``known`` = the configured sources: a candidate they already cover carries ``configured = <id>``.
    Refused endpoints become failures with an IT action; the scopes found elsewhere are still returned.
    """
    run = _Run(client)
    me: dict[str, str] = {}
    run.step("/me/drive", lambda: _own_drive(client, me))
    run.step("/me/drives", lambda: _my_drives(client, me))
    run.step("/me/followedSites", lambda: _followed_sites(run))
    run.step("/me/drive/root/children", lambda: _shortcuts(client))
    run.step("/me/joinedTeams", lambda: _channels(run))
    run.step("/me/chats", lambda: _chats(client, max_chats))
    run.step("/me/mailFolders/delta", lambda: _mail_folders(client))
    failures = tuple(sorted(run.failures, key=lambda f: (f.endpoint, f.status, f.code)))
    return DiscoveryReport(scopes=_finish(run.scopes, known), failures=failures)


def share_id(url: str) -> str:
    """The ``/shares`` id of a sharing URL: ``u!`` + unpadded base64url of the URL (Graph's encoding)."""
    encoded = base64.urlsafe_b64encode(url.strip().encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{encoded}"


def resolve_url(client: GraphClient, url: str, *, known: Sequence[SourceConfig] = ()) -> DiscoveryReport:
    """Resolve an operator-supplied SharePoint / OneDrive URL into candidates.

    A site URL (``https://host/sites/x``) -> ``GET /sites/{host}:/{path}`` then its libraries. Anything else
    (a sharing link, a folder or file URL) -> ``GET /shares/u!{base64url}/driveItem``. A 401 / 403 is
    returned as a failure naming the IT action, never raised.
    """
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.netloc:
        return DiscoveryReport((), (DiscoveryFailure("url", 0, "not-a-url", "pass an https:// link"),))
    run = _Run(client)
    path = unquote(parts.path)
    site_match = _SITE_PATH.match(path)
    if site_match and not _SHARING_MARKERS.match(path) and path.rstrip("/") == site_match.group(0):

        def site_step() -> list[DiscoveredScope]:
            site_path = f"/sites/{parts.netloc}:{quote(site_match.group(0), safe='/')}"
            site = client.get_json(site_path, params={"$select": "id,displayName,name,webUrl"})
            return _site_libraries(client, site, "document library of the site you named")

        run.step("/sites", site_step)
    else:

        def share_step() -> list[DiscoveredScope]:
            item = client.get_json(
                f"/shares/{share_id(url)}/driveItem",
                params={"$select": "id,name,folder,file,parentReference,webUrl"},
            )
            parent = _obj(item, "parentReference")
            drive_id = _opt_str(parent, "driveId")
            if not drive_id:
                raise GraphError(200, "no-drive-id", "the shared item carried no parentReference.driveId")
            is_folder = "folder" in item
            name = _opt_str(item, "name") or "(unnamed)"
            folder = _item_folder(_opt_str(parent, "path"), name, is_folder)
            note = "resolved from the URL you named"
            if folder is None:
                note += "; folder path not returned (the drive root may be unreadable): set folder by hand"
            elif not is_folder:
                note += f"; a single file ({name}): its parent folder is proposed, narrow with include"
            return [
                DiscoveredScope(
                    kind=SourceKind.GRAPH_DRIVE,
                    name=f"Shared {name}",
                    drive_id=drive_id,
                    site=None,
                    web_url=_opt_str(item, "webUrl") or url,
                    note=note,
                    folder=folder,
                )
            ]

        run.step("/shares", share_step)
    return DiscoveryReport(scopes=_finish(run.scopes, known), failures=tuple(run.failures))


# ---------------------------------------------------------------------------------------------------------
# sources.toml snippet
# ---------------------------------------------------------------------------------------------------------

_ID_PREFIX = {SourceKind.GRAPH_DRIVE: "drive", SourceKind.GRAPH_MAIL: "mail", SourceKind.GRAPH_TEAMS: "teams"}


def _toml_str(value: str) -> str:
    """A TOML basic string (JSON's escaping is a valid subset)."""
    return json.dumps(value, ensure_ascii=False)


def _comment(text: str) -> str:
    """Text safe inside one ``#`` comment line."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


def suggest_source_id(scope: DiscoveredScope, taken: set[str]) -> str:
    """A unique ``[[source]] id`` (``^[a-z0-9][a-z0-9-]{0,62}$``) for ``scope``; adds it to ``taken``."""
    if scope.kind is SourceKind.GRAPH_TEAMS and scope.team_id == _CHATS_TEAM_ID:
        prefix = "chat"
    else:
        prefix = _ID_PREFIX.get(scope.kind, "src")
    text = unicodedata.normalize("NFKD", scope.name).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if slug.startswith(prefix + "-"):
        slug = slug[len(prefix) + 1 :]
    base = f"{prefix}-{slug}".strip("-")[:56].rstrip("-") or prefix
    candidate, n = base, 2
    while candidate in taken or not SOURCE_ID_RE.match(candidate):
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def render_sources_toml(report: DiscoveryReport, *, known: Sequence[SourceConfig] = ()) -> str:
    """A sources.toml snippet: one ``[[source]]`` table per NEW candidate, each ``state = "paused"``.

    Already-configured candidates are listed as comments; refused endpoints are listed with their IT action
    so an incomplete discovery is never mistaken for a complete one. Deterministic for a given report.
    """
    taken = {c.id for c in known}
    lines = [
        "# agentsync discover: proposed sources. Paste the tables you want into sources.toml.",
        '# Each starts paused: the first live cycle is a full enumeration (flip state to "live").',
    ]
    if not report.complete:
        lines.append(
            f"# DISCOVERY INCOMPLETE: {len(report.failures)} endpoint(s) refused (listed at the end)."
        )
    for scope in report.scopes:
        if scope.configured is not None:
            continue
        lines += ["", f"# {_comment(scope.name)} ({_comment(scope.note)})"]
        if scope.web_url:
            lines.append(f"# {_comment(scope.web_url)}")
        lines += [
            "[[source]]",
            f"id = {_toml_str(suggest_source_id(scope, taken))}",
            f"kind = {_toml_str(scope.kind.value)}",
        ]
        if scope.kind is SourceKind.GRAPH_DRIVE:
            lines.append(f"drive_id = {_toml_str(scope.drive_id or '')}")
            if scope.folder and scope.folder != "/":
                lines.append(f"folder = {_toml_str(scope.folder)}")
        elif scope.kind is SourceKind.GRAPH_MAIL:
            lines.append(f"mailbox = {_toml_str(scope.mailbox or 'me')}")
            lines.append(f"folder = {_toml_str(scope.folder or '')}")
        else:
            lines.append(f"team_id = {_toml_str(scope.team_id or '')}")
            lines.append(f"channel_id = {_toml_str(scope.channel_id or '')}")
        lines.append('state = "paused"')
    configured = [s for s in report.scopes if s.configured is not None]
    if configured:
        lines += ["", "# Already configured:"]
        lines += [f"#   {_comment(s.name)} -> {s.configured}" for s in configured]
    if report.failures:
        lines += ["", "# Refused endpoints (discovery is incomplete until these are fixed):"]
        lines += [
            f"#   {f.endpoint}: {f.status} {_comment(f.code)} -> {_comment(f.action)}"
            for f in report.failures
        ]
    return "\n".join(lines) + "\n"
