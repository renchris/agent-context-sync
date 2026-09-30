"""Arm 0 discovery: supported endpoints only, named IT actions, mail folders, channels, chats, URL resolve,
and a sources.toml snippet that the real config parser accepts."""

from __future__ import annotations

import base64
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import agentsync.graph.discover as discover_module
import agentsync.graph.drive as drive_module
import agentsync.graph.mail as mail_module
import agentsync.graph.teams as teams_module
from agentsync.config import SOURCE_ID_RE, SourceConfig, parse_config
from agentsync.errors import AuthRequiredError, GraphError, GraphNotFound
from agentsync.graph.client import GraphPage
from agentsync.graph.discover import (
    DiscoveryReport,
    discover_sources,
    it_action,
    render_sources_toml,
    resolve_url,
    share_id,
    suggest_source_id,
)
from agentsync.graph.drive import DiscoveredScope
from agentsync.model import SourceKind

BASE = "https://graph.microsoft.com/v1.0"
IMMUTABLE = 'IdType="ImmutableId"'
DEPRECATED = ("shared" + "WithMe", "insights/" + "shared")  # split so the endpoint lint never matches


@dataclass
class FakeClient:
    """Unknown collection paths are empty; unknown JSON paths are 404."""

    json: dict[str, Any] = field(default_factory=dict)
    collections: dict[str, list[list[dict[str, Any]]] | Exception] = field(default_factory=dict)
    calls: list[tuple[str, str, Mapping[str, str] | None, Mapping[str, str] | None]] = field(
        default_factory=list
    )

    def get_json(
        self, path: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        self.calls.append(("get_json", path, params, headers))
        body = self.json.get(path, GraphNotFound(404, "itemNotFound", path))
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
        for i, page in enumerate(pages):
            nxt = f"{BASE}{path}?$skiptoken={i + 1}" if i + 1 < len(pages) else None
            yield GraphPage(value=tuple(page), next_link=nxt, delta_link=None)

    def paths(self) -> list[str]:
        return [c[1] for c in self.calls]


def tenant() -> FakeClient:
    """A small tenant: own drive, one extra drive, two followed sites (one locked), a shortcut, a team,
    two chats and a nested mail tree whose child the folder delta omits."""
    return FakeClient(
        json={
            "/me/drive": {
                "id": "ME",
                "name": "OneDrive",
                "driveType": "business",
                "webUrl": "https://c-my/me",
            },
            "/drives/b!OWNER/items/R1": {
                "id": "R1",
                "name": "Board Pack",
                "folder": {},
                "parentReference": {"driveId": "b!OWNER", "path": "/drives/b!OWNER/root:/Shared%20Documents"},
            },
        },
        collections={
            "/me/drives": [[{"id": "ME", "name": "OneDrive"}, {"id": "b!EXTRA", "name": "Scratch"}]],
            "/me/followedSites": [
                [
                    {"id": "SITE1", "displayName": "Finance", "webUrl": "https://c.sharepoint.com/sites/fin"},
                    {"id": "SITE2", "displayName": "Legal", "webUrl": "https://c.sharepoint.com/sites/legal"},
                ]
            ],
            "/sites/SITE1/drives": [
                [{"id": "b!DOCS", "name": "Documents"}, {"id": "b!ARCH", "name": "Archive"}]
            ],
            "/sites/SITE2/drives": GraphError(403, "accessDenied", "no"),
            "/me/drive/root/children": [
                [
                    {"id": "L1", "name": "local.docx", "file": {}},
                    {
                        "id": "S1",
                        "name": "Board Pack",
                        "remoteItem": {"id": "R1", "folder": {}, "parentReference": {"driveId": "b!OWNER"}},
                    },
                ]
            ],
            "/me/joinedTeams": [[{"id": "T1", "displayName": "Ops"}]],
            "/teams/T1/channels": [
                [
                    {"id": "19:gen@thread.tacv2", "displayName": "General", "membershipType": "standard"},
                    {"id": "19:priv@thread.tacv2", "displayName": "Leads", "membershipType": "private"},
                ]
            ],
            "/me/chats": [
                [
                    {"id": "19:c1@thread.v2", "topic": "Budget huddle", "chatType": "group"},
                    {"id": "19:c2@unq.gbl.spaces", "topic": None, "chatType": "oneOnOne"},
                ]
            ],
            "/me/mailFolders/delta": [
                [
                    {"id": "INBOX", "displayName": "Inbox", "parentFolderId": "ROOT", "childFolderCount": 1},
                    {"id": "ARCH", "displayName": "Archive", "parentFolderId": "ROOT", "childFolderCount": 0},
                ]
            ],
            "/me/mailFolders/INBOX/childFolders": [
                [{"id": "PROJ", "displayName": "Projects", "parentFolderId": "INBOX", "childFolderCount": 0}]
            ],
        },
    )


def by_name(report: DiscoveryReport) -> dict[str, DiscoveredScope]:
    return {s.name: s for s in report.scopes}


# ---------------------------------------------------------------------------------------------------------
# discover_sources
# ---------------------------------------------------------------------------------------------------------


def test_discovery_enumerates_every_kind_from_supported_endpoints() -> None:
    client = tenant()
    report = discover_sources(client)  # type: ignore[arg-type]
    found = by_name(report)

    assert found["OneDrive (OneDrive)"].drive_id == "me"
    assert found["Drive Scratch"].drive_id == "b!EXTRA"
    assert not any(s.drive_id == "ME" for s in report.scopes)  # own drive proposed once, as "me"
    lib = found["Finance / Documents"]
    assert lib.drive_id == "b!DOCS" and lib.site == "c.sharepoint.com:/sites/fin"
    shortcut = found["Shortcut Board Pack"]
    assert (shortcut.drive_id, shortcut.folder) == ("b!OWNER", "/Shared Documents/Board Pack")
    general = found["Ops / General"]
    assert (general.kind, general.team_id, general.channel_id) == (
        SourceKind.GRAPH_TEAMS,
        "T1",
        "19:gen@thread.tacv2",
    )
    assert "private channel" in found["Ops / Leads"].note
    chat = found["Chat Budget huddle"]
    assert (chat.team_id, chat.channel_id) == ("chats", "19:c1@thread.v2")
    assert any(s.name.startswith("Chat oneOnOne") for s in report.scopes)
    mail = {s.name: s for s in report.scopes if s.kind is SourceKind.GRAPH_MAIL}
    assert set(mail) == {"Mail Archive", "Mail Inbox", "Mail Inbox/Projects"}  # the nested one is not skipped
    assert mail["Mail Inbox/Projects"].folder == "PROJ" and mail["Mail Inbox/Projects"].mailbox == "me"

    # deterministic order, every endpoint supported, mail requests on immutable ids
    keys = [(s.kind.value, s.name.casefold()) for s in report.scopes]
    assert keys == sorted(keys)
    assert not any(d in p for p in client.paths() for d in DEPRECATED)
    assert "/sites" not in client.paths()  # no tenant-wide site search
    for method, path, _, headers in client.calls:
        if path.startswith("/me/mailFolders"):
            assert headers is not None and headers["Prefer"] == IMMUTABLE, (method, path)

    # the locked site is a named failure, not a silent gap
    assert not report.complete
    assert [(f.status, f.code) for f in report.failures] == [(403, "accessDenied")]
    assert "Legal" in report.failures[0].endpoint and "Sites.Read.All" in report.failures[0].action


def test_discovery_is_deterministic() -> None:
    first = discover_sources(tenant())  # type: ignore[arg-type]
    second = discover_sources(tenant())  # type: ignore[arg-type]
    assert first == second
    assert render_sources_toml(first) == render_sources_toml(second)


@pytest.mark.parametrize(
    ("endpoint", "scope"),
    [
        ("/me/joinedTeams", "Team.ReadBasic.All"),
        ("/me/chats", "Chat.ReadBasic"),
        ("/me/mailFolders/delta", "Mail.ReadBasic"),
        ("/me/followedSites", "Sites.Read.All"),
        ("/me/drives", "Files.Read"),
    ],
)
def test_a_refused_endpoint_is_a_named_it_action_and_hides_nothing_else(endpoint: str, scope: str) -> None:
    client = tenant()
    client.collections[endpoint] = GraphError(403, "Forbidden", "consent missing")
    report = discover_sources(client)  # type: ignore[arg-type]
    failure = next(f for f in report.failures if f.endpoint == endpoint)
    assert failure.status == 403 and scope in failure.action and "admin consent" in failure.action
    assert by_name(report)["OneDrive (OneDrive)"].drive_id == "me"  # the rest still came through


def test_sign_in_failure_stops_probing_and_says_so() -> None:
    client = tenant()
    client.json["/me/drive"] = AuthRequiredError("REAUTH_REQUIRED")
    report = discover_sources(client)  # type: ignore[arg-type]
    assert report.scopes == ()
    assert [(f.endpoint, f.status) for f in report.failures] == [("/me/drive", 401)]
    assert "agentsync login" in report.failures[0].action
    assert client.paths() == ["/me/drive"]


def test_chat_discovery_is_capped_most_recent_first() -> None:
    client = tenant()
    client.collections["/me/chats"] = [[{"id": f"19:c{i}@thread.v2", "topic": f"t{i}"} for i in range(10)]]
    report = discover_sources(client, max_chats=3)  # type: ignore[arg-type]
    assert sum(1 for s in report.scopes if s.team_id == "chats") == 3
    _, _, params, _ = next(c for c in client.calls if c[1] == "/me/chats")
    assert params is not None and params["$orderby"] == "lastMessagePreview/createdDateTime desc"


def test_shortcut_on_an_unreadable_drive_is_still_proposed_with_the_action() -> None:
    client = tenant()
    client.json["/drives/b!OWNER/items/R1"] = GraphError(403, "accessDenied", "no")
    shortcut = by_name(discover_sources(client))["Shortcut Board Pack"]  # type: ignore[arg-type]
    assert shortcut.folder is None and "403" in shortcut.note and "set folder by hand" in shortcut.note


def test_known_sources_are_marked_configured() -> None:
    known = (
        SourceConfig(id="od", kind=SourceKind.GRAPH_DRIVE, drive_id="me", folder="/"),
        SourceConfig(id="inbox", kind=SourceKind.GRAPH_MAIL, folder="INBOX"),
        SourceConfig(id="gen", kind=SourceKind.GRAPH_TEAMS, team_id="T1", channel_id="19:gen@thread.tacv2"),
    )
    report = discover_sources(tenant(), known=known)  # type: ignore[arg-type]
    found = by_name(report)
    assert found["OneDrive (OneDrive)"].configured == "od"
    assert found["Mail Inbox"].configured == "inbox"
    assert found["Ops / General"].configured == "gen"
    assert found["Mail Inbox/Projects"].configured is None  # a new folder is proposed, never skipped
    assert found["Mail Inbox/Projects"] in report.new_scopes


# ---------------------------------------------------------------------------------------------------------
# resolve_url (declare, then resolve)
# ---------------------------------------------------------------------------------------------------------


def test_share_id_is_unpadded_base64url() -> None:
    url = "https://c.sharepoint.com/:f:/s/fin/EabcXYZ?e=a1b2"
    sid = share_id(url)
    assert sid.startswith("u!") and "=" not in sid and "/" not in sid and "+" not in sid
    body = sid[2:]
    assert base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode() == url


def test_resolve_a_sharing_link_to_its_drive_and_folder() -> None:
    url = "https://c.sharepoint.com/:f:/s/fin/EabcXYZ"
    client = FakeClient(
        json={
            f"/shares/{share_id(url)}/driveItem": {
                "id": "F9",
                "name": "FY26",
                "folder": {"childCount": 3},
                "parentReference": {"driveId": "b!FIN", "path": "/drives/b!FIN/root:/Budgets"},
                "webUrl": "https://c.sharepoint.com/sites/fin/Budgets/FY26",
            }
        }
    )
    report = resolve_url(client, url)  # type: ignore[arg-type]
    assert report.complete
    (scope,) = report.scopes
    assert (scope.kind, scope.drive_id, scope.folder) == (SourceKind.GRAPH_DRIVE, "b!FIN", "/Budgets/FY26")


def test_resolve_a_shared_file_proposes_its_parent_folder() -> None:
    url = "https://c.sharepoint.com/:x:/s/fin/Efile"
    client = FakeClient(
        json={
            f"/shares/{share_id(url)}/driveItem": {
                "id": "X1",
                "name": "Plan.xlsx",
                "file": {},
                "parentReference": {"driveId": "b!FIN", "path": "/drives/b!FIN/root:/Budgets"},
            }
        }
    )
    (scope,) = resolve_url(client, url).scopes  # type: ignore[arg-type]
    assert scope.folder == "/Budgets" and "Plan.xlsx" in scope.note


def test_resolve_a_site_url_lists_its_libraries() -> None:
    client = FakeClient(
        json={
            "/sites/c.sharepoint.com:/sites/fin": {
                "id": "SITE1",
                "displayName": "Finance",
                "webUrl": "https://c.sharepoint.com/sites/fin",
            }
        },
        collections={"/sites/SITE1/drives": [[{"id": "b!DOCS", "name": "Documents"}]]},
    )
    report = resolve_url(client, "https://c.sharepoint.com/sites/fin/")  # type: ignore[arg-type]
    assert [(s.name, s.drive_id) for s in report.scopes] == [("Finance / Documents", "b!DOCS")]


@pytest.mark.parametrize(
    ("error", "status", "needle"),
    [
        (GraphError(403, "accessDenied", "no"), 403, "Files.ReadWrite"),
        (AuthRequiredError("REAUTH_REQUIRED"), 401, "agentsync login"),
        (GraphNotFound(404, "itemNotFound", "no"), 404, "not shared with this account"),
    ],
)
def test_resolve_reports_refusals_as_named_actions(error: Exception, status: int, needle: str) -> None:
    url = "https://c.sharepoint.com/:f:/s/fin/Elocked"
    client = FakeClient(json={f"/shares/{share_id(url)}/driveItem": error})
    report = resolve_url(client, url)  # type: ignore[arg-type]
    assert report.scopes == () and not report.complete
    (failure,) = report.failures
    assert failure.status == status and needle in failure.action and failure.endpoint == "/shares"


def test_resolve_rejects_non_https() -> None:
    report = resolve_url(FakeClient(), "ftp://x/y")  # type: ignore[arg-type]
    assert report.failures[0].code == "not-a-url"


def test_it_action_other_status() -> None:
    assert "retry later" in it_action("/me/drive", 503, "serviceNotAvailable")


# ---------------------------------------------------------------------------------------------------------
# sources.toml snippet
# ---------------------------------------------------------------------------------------------------------


def test_snippet_is_valid_toml_that_the_config_parser_accepts(tmp_path: Path) -> None:
    known = (SourceConfig(id="od", kind=SourceKind.GRAPH_DRIVE, drive_id="me", folder="/"),)
    report = discover_sources(tenant(), known=known)  # type: ignore[arg-type]
    text = render_sources_toml(report, known=known)
    doc = tomllib.loads(text)
    tables = doc["source"]
    assert len(tables) == len(report.new_scopes)
    assert all(t["state"] == "paused" for t in tables)
    ids = [t["id"] for t in tables]
    assert len(set(ids)) == len(ids) and all(SOURCE_ID_RE.match(i) for i in ids)
    assert "od" not in ids and "#   OneDrive (OneDrive) -> od" in text
    assert "DISCOVERY INCOMPLETE" in text and "Sites.Read.All" in text
    config = parse_config(text, config_path=tmp_path / "sources.toml")
    kinds = {s.kind for s in config.sources}
    assert kinds == {SourceKind.GRAPH_DRIVE, SourceKind.GRAPH_MAIL, SourceKind.GRAPH_TEAMS}
    shortcut = next(s for s in config.sources if s.drive_id == "b!OWNER")
    assert shortcut.folder == "/Shared Documents/Board Pack"
    chat = next(s for s in config.sources if s.team_id == "chats")
    assert chat.channel_id and chat.id.startswith("chat-")


def test_snippet_escapes_hostile_names() -> None:
    scope = DiscoveredScope(
        kind=SourceKind.GRAPH_DRIVE,
        name='Evil "lib"\n[[source]]\nid = "x"',
        drive_id='b!"q\\',
        site=None,
        web_url="https://x\n# injected",
        note="n",
    )
    text = render_sources_toml(DiscoveryReport((scope,)))
    doc = tomllib.loads(text)
    assert len(doc["source"]) == 1 and doc["source"][0]["drive_id"] == 'b!"q\\'


def test_suggest_source_id_is_valid_and_unique() -> None:
    scope = DiscoveredScope(SourceKind.GRAPH_MAIL, "Mail Ünïcode / Pröjects!!", None, None, None, "n")
    taken: set[str] = {"mail-unicode-projects"}
    first = suggest_source_id(scope, taken)
    second = suggest_source_id(scope, taken)
    assert first == "mail-unicode-projects-2" and second == "mail-unicode-projects-3"
    long_scope = DiscoveredScope(SourceKind.GRAPH_DRIVE, "x" * 300, None, None, None, "n")
    assert SOURCE_ID_RE.match(suggest_source_id(long_scope, set()))


# ---------------------------------------------------------------------------------------------------------
# endpoint lint over the four graph-arms modules (C15 section 9 item 15, for these files)
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("module", [discover_module, drive_module, mail_module, teams_module])
def test_no_deprecated_or_app_only_endpoint_in_the_arms(module: Any) -> None:
    text = Path(module.__file__).read_text(encoding="utf-8")
    for needle in (*DEPRECATED, "getAll" + "Messages"):
        assert needle not in text, needle
    assert "channels/{c}/messages/de" + "lta" not in text
