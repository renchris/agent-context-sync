"""Load and validate ``sources.toml`` into an immutable :class:`Config` (docs/design/CONTRACTS.md,
"config")."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentsync.errors import ConfigError
from agentsync.model import SourceKind, SourceState
from agentsync.paths import (
    DocsLayout,
    StatePaths,
    default_cache_dir,
    default_config_path,
    default_docs_repo,
    default_log_dir,
    default_state_dir,
    expand,
    is_cloud_path,
    is_under,
)

SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

DEFAULT_GRAPH_SCOPES: tuple[str, ...] = ("Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read")
"""Delegated scopes requested at device-code sign-in; ``offline_access`` is added by MSAL itself.

``ChannelMessage.Read.All`` (Teams) needs admin consent, so it is requested only when a live graph_teams
source exists (see :meth:`Config.graph_scopes`).
"""

TEAMS_SCOPE = "ChannelMessage.Read.All"
SHARED_MAIL_SCOPE = "Mail.Read.Shared"

DEFAULT_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

DEFAULT_EXCLUDES: tuple[str, ...] = ("~$*", "*.tmp", ".~lock.*#", ".DS_Store", "Icon\r", "._*")

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]i?b|b)?\s*$", re.IGNORECASE)
_SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}

_TOP_KEYS = frozenset({"agentsync", "graph", "breaker", "convert", "source"})
_AGENTSYNC_KEYS = frozenset(
    {
        "docs_repo",
        "state_dir",
        "cache_dir",
        "log_dir",
        "reconcile_interval_s",
        "poll_interval_s",
        "tombstone_reap_days",
        "principal",
        "launchd_label_prefix",
    }
)
_GRAPH_KEYS = frozenset({"client_id", "tenant", "scopes", "company", "base_url"})
_BREAKER_KEYS = frozenset({"fraction", "floor", "hold_days"})
_CONVERT_KEYS = frozenset(
    {"xlsx_stream_threshold_bytes", "max_rows_per_sheet", "max_page_bytes", "pandoc_path"}
)
_COMMON_SOURCE_KEYS = frozenset(
    {
        "id",
        "kind",
        "state",
        "include",
        "exclude",
        "max_materialise_bytes",
        "max_files",
        "cadence_s",
        "principal",
        "retired_reason",
    }
)
_KIND_KEYS: Mapping[SourceKind, frozenset[str]] = {
    SourceKind.LOCAL: frozenset({"path", "sentinel"}),
    SourceKind.INBOX: frozenset({"path", "sentinel", "quiescence_s"}),
    SourceKind.GRAPH_DRIVE: frozenset({"drive_id", "site", "folder"}),
    SourceKind.GRAPH_MAIL: frozenset({"mailbox", "folder"}),
    SourceKind.GRAPH_TEAMS: frozenset({"team_id", "channel_id"}),
}
_KIND_REQUIRED: Mapping[SourceKind, tuple[str, ...]] = {
    SourceKind.LOCAL: ("path",),
    SourceKind.INBOX: ("path",),
    SourceKind.GRAPH_DRIVE: (),  # one of drive_id / site, checked separately
    SourceKind.GRAPH_MAIL: ("folder",),
    SourceKind.GRAPH_TEAMS: ("team_id", "channel_id"),
}
_DEFAULT_CADENCE: Mapping[SourceKind, int] = {
    SourceKind.LOCAL: 900,
    SourceKind.INBOX: 900,
    SourceKind.GRAPH_DRIVE: 300,
    SourceKind.GRAPH_MAIL: 300,
    SourceKind.GRAPH_TEAMS: 900,
}


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    """Deletion circuit breaker: trip when candidates > max(fraction * live rows in scope, floor)."""

    fraction: float = 0.20
    floor: int = 25
    hold_days: int = 7

    def threshold(self, live_rows: int) -> int:
        """Largest deletion count that does NOT trip the breaker for a scope with ``live_rows`` live rows."""
        return max(int(self.fraction * live_rows), self.floor)


@dataclass(frozen=True, slots=True)
class GraphConfig:
    """Microsoft Graph settings; ``client_id`` None means the Graph arms are unavailable (local arm still
    works)."""

    client_id: str | None = None
    tenant: str = "organizations"
    scopes: tuple[str, ...] = DEFAULT_GRAPH_SCOPES
    company: str = "agentsync"  # User-Agent: NONISV|<company>|agentsync/<version>
    base_url: str = DEFAULT_GRAPH_BASE_URL

    @property
    def authority(self) -> str:
        """MSAL authority URL for ``tenant``."""
        return f"https://login.microsoftonline.com/{self.tenant}"


@dataclass(frozen=True, slots=True)
class ConvertConfig:
    """Converter options; every field participates in the converters' ``options_hash``."""

    xlsx_stream_threshold_bytes: int = 20 * 1000**2
    max_rows_per_sheet: int = 5000
    max_page_bytes: int = 1_000_000  # hard cap before a unit is split / row-capped with a sidecar
    pandoc_path: Path | None = None  # None = the pypandoc_binary bundled pandoc, by absolute path


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One ``[[source]]`` table, validated.  Kind-specific fields are None when not applicable."""

    id: str
    kind: SourceKind
    state: SourceState = SourceState.LIVE
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    max_materialise_bytes: int = 1024**3
    max_files: int = 5000
    cadence_s: int = 300
    principal: str | None = None
    retired_reason: str | None = None
    # local / inbox
    path: Path | None = None
    sentinel: str | None = None  # positive control: a relative path that must be present in every walk
    quiescence_s: int = 60  # inbox: skip files whose size/mtime changed within this many seconds
    # graph_drive
    drive_id: str | None = None  # a drive id, or "me" for /me/drive
    site: str | None = None  # "<host>:/sites/<name>" resolved to its default library's drive id
    folder: str | None = None  # drive: subtree filter ("/" default); mail: well-known name or folder id
    # graph_mail
    mailbox: str = "me"  # "me" or a shared mailbox UPN (needs Mail.Read.Shared)
    # graph_teams
    team_id: str | None = None
    channel_id: str | None = None

    @property
    def is_live(self) -> bool:
        """True when the source should be synced this cycle."""
        return self.state is SourceState.LIVE


@dataclass(frozen=True, slots=True)
class Config:
    """The whole validated configuration."""

    config_path: Path
    docs_repo: Path = field(default_factory=default_docs_repo)
    state_dir: Path = field(default_factory=default_state_dir)
    cache_dir: Path = field(default_factory=default_cache_dir)
    log_dir: Path = field(default_factory=default_log_dir)
    sources: tuple[SourceConfig, ...] = ()
    graph: GraphConfig = field(default_factory=GraphConfig)
    breaker: BreakerConfig = field(default_factory=BreakerConfig)
    convert: ConvertConfig = field(default_factory=ConvertConfig)
    reconcile_interval_s: int = 3600
    poll_interval_s: int = 300
    tombstone_reap_days: int = 180
    principal: str | None = None
    launchd_label_prefix: str = "com.agentsync"

    @property
    def state_paths(self) -> StatePaths:
        """Layout of the machine-local state dir."""
        return StatePaths(self.state_dir)

    @property
    def layout(self) -> DocsLayout:
        """Layout of the docs repo."""
        return DocsLayout(self.docs_repo)

    def source(self, source_id: str) -> SourceConfig:
        """Return the source with ``source_id`` or raise ConfigError."""
        for s in self.sources:
            if s.id == source_id:
                return s
        raise ConfigError(f"{self.config_path}: no source with id {source_id!r}")

    def live_sources(self) -> tuple[SourceConfig, ...]:
        """Sources whose state is live, in configuration order."""
        return tuple(s for s in self.sources if s.is_live)

    def graph_scopes(self) -> tuple[str, ...]:
        """Scopes to request: configured ones plus Teams/shared-mail scopes only when a live source needs
        them."""
        scopes = list(self.graph.scopes)
        live = self.live_sources()
        if any(s.kind is SourceKind.GRAPH_TEAMS for s in live) and TEAMS_SCOPE not in scopes:
            scopes.append(TEAMS_SCOPE)
        if (
            any(s.kind is SourceKind.GRAPH_MAIL and s.mailbox != "me" for s in live)
            and SHARED_MAIL_SCOPE not in scopes
        ):
            scopes.append(SHARED_MAIL_SCOPE)
        return tuple(scopes)


# ---------------------------------------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------------------------------------


def parse_size(value: object, *, where: str) -> int:
    """Parse a byte count: a non-negative int, or a string like ``"500MB"`` / ``"2GiB"``."""
    if isinstance(value, bool):
        raise ConfigError(f"{where}: expected a size, got a boolean")
    if isinstance(value, int):
        if value < 0:
            raise ConfigError(f"{where}: size must be >= 0, got {value}")
        return value
    if isinstance(value, str):
        m = _SIZE_RE.match(value)
        if m:
            unit = (m.group(2) or "b").lower()
            return int(float(m.group(1)) * _SIZE_UNITS[unit])
    raise ConfigError(f'{where}: expected a size such as 1073741824 or "1GiB", got {value!r}')


def _check_keys(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
        )


def _table(doc: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = doc.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: [{key}] must be a table")
    return value


def _str(t: Mapping[str, Any], key: str, where: str, default: str | None = None) -> str | None:
    v = t.get(key, default)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ConfigError(f"{where}: {key!r} must be a string, got {type(v).__name__}")
    v = v.strip()
    return v or None


def _int(t: Mapping[str, Any], key: str, where: str, default: int, minimum: int = 0) -> int:
    v = t.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{where}: {key!r} must be an integer, got {v!r}")
    if v < minimum:
        raise ConfigError(f"{where}: {key!r} must be >= {minimum}, got {v}")
    return v


def _str_list(t: Mapping[str, Any], key: str, where: str, default: tuple[str, ...]) -> tuple[str, ...]:
    v = t.get(key)
    if v is None:
        return default
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{where}: {key!r} must be a list of strings")
    return tuple(x for x in v if x.strip())


def _path(t: Mapping[str, Any], key: str, where: str, default: Path) -> Path:
    v = _str(t, key, where)
    return expand(v) if v else default


# ---------------------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------------------


def _parse_source(raw: object, n: int, cfg_where: str) -> SourceConfig:
    where = f"{cfg_where}: [[source]] #{n}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a table")
    sid = _str(raw, "id", where)
    if sid is None:
        raise ConfigError(f"{where}: missing required key 'id'")
    where = f"{where} (id={sid!r})"
    if not SOURCE_ID_RE.match(sid):
        raise ConfigError(f"{where}: id must match {SOURCE_ID_RE.pattern} (lowercase, digits, '-')")
    kind_s = _str(raw, "kind", where)
    try:
        kind = SourceKind(kind_s or "")
    except ValueError:
        kinds = ", ".join(k.value for k in SourceKind)
        raise ConfigError(f"{where}: kind must be one of {kinds}, got {kind_s!r}") from None
    _check_keys(raw, _COMMON_SOURCE_KEYS | _KIND_KEYS[kind], where)
    for req in _KIND_REQUIRED[kind]:
        if _str(raw, req, where) is None:
            raise ConfigError(f"{where}: missing required key {req!r} for kind {kind.value!r}")
    state_s = _str(raw, "state", where, SourceState.LIVE.value)
    try:
        state = SourceState(state_s or "")
    except ValueError:
        raise ConfigError(f"{where}: state must be paused, live or retired, got {state_s!r}") from None
    if state is SourceState.RETIRED and _str(raw, "retired_reason", where) is None:
        raise ConfigError(
            f"{where}: a retired source needs 'retired_reason' (retirement is an explicit verb)"
        )

    drive_id = _str(raw, "drive_id", where)
    site = _str(raw, "site", where)
    if kind is SourceKind.GRAPH_DRIVE and (drive_id is None) == (site is None):
        raise ConfigError(
            f"{where}: kind 'graph_drive' needs exactly one of 'drive_id' (or \"me\") or 'site'"
        )

    path: Path | None = None
    if kind in (SourceKind.LOCAL, SourceKind.INBOX):
        path = expand(_str(raw, "path", where) or "")
    sentinel = _str(raw, "sentinel", where)
    if sentinel is not None and (sentinel.startswith("/") or ".." in Path(sentinel).parts):
        raise ConfigError(f"{where}: 'sentinel' must be a path relative to the source root")

    folder = _str(raw, "folder", where)
    if kind is SourceKind.GRAPH_DRIVE:
        folder = "/" + (folder or "").strip("/")

    max_bytes = parse_size(raw.get("max_materialise_bytes", 1024**3), where=f"{where}: max_materialise_bytes")
    return SourceConfig(
        id=sid,
        kind=kind,
        state=state,
        include=_str_list(raw, "include", where, ()),
        exclude=_str_list(raw, "exclude", where, DEFAULT_EXCLUDES),
        max_materialise_bytes=max_bytes,
        max_files=_int(raw, "max_files", where, 5000, minimum=1),
        cadence_s=_int(raw, "cadence_s", where, _DEFAULT_CADENCE[kind], minimum=30),
        principal=_str(raw, "principal", where),
        retired_reason=_str(raw, "retired_reason", where),
        path=path,
        sentinel=sentinel,
        quiescence_s=_int(raw, "quiescence_s", where, 60),
        drive_id=drive_id,
        site=site,
        folder=folder,
        mailbox=_str(raw, "mailbox", where, "me") or "me",
        team_id=_str(raw, "team_id", where),
        channel_id=_str(raw, "channel_id", where),
    )


def parse_config(text: str, *, config_path: Path) -> Config:
    """Parse and validate sources.toml text; raises ConfigError naming the file, table and key."""
    where = str(config_path)
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{where}: not valid TOML: {exc}") from None
    _check_keys(doc, _TOP_KEYS, where)

    a = _table(doc, "agentsync", where)
    _check_keys(a, _AGENTSYNC_KEYS, f"{where}: [agentsync]")
    aw = f"{where}: [agentsync]"
    docs_repo = _path(a, "docs_repo", aw, default_docs_repo())
    state_dir = _path(a, "state_dir", aw, default_state_dir())
    cache_dir = _path(a, "cache_dir", aw, default_cache_dir())
    log_dir = _path(a, "log_dir", aw, default_log_dir())
    if is_cloud_path(docs_repo):
        raise ConfigError(
            f"{aw}: docs_repo {docs_repo} is inside ~/Library/CloudStorage; the docs repo must live outside "
            "any cloud-synced path (design 4.7)"
        )
    for name, p in (("state_dir", state_dir), ("cache_dir", cache_dir)):
        if is_cloud_path(p):
            raise ConfigError(f"{aw}: {name} {p} is inside ~/Library/CloudStorage; keep it machine-local")
        if is_under(p, docs_repo):
            raise ConfigError(f"{aw}: {name} {p} must not be inside docs_repo {docs_repo} (never in git)")

    g = _table(doc, "graph", where)
    gw = f"{where}: [graph]"
    _check_keys(g, _GRAPH_KEYS, gw)
    graph = GraphConfig(
        client_id=_str(g, "client_id", gw),
        tenant=_str(g, "tenant", gw, "organizations") or "organizations",
        scopes=_str_list(g, "scopes", gw, DEFAULT_GRAPH_SCOPES),
        company=_str(g, "company", gw, "agentsync") or "agentsync",
        base_url=(_str(g, "base_url", gw, DEFAULT_GRAPH_BASE_URL) or DEFAULT_GRAPH_BASE_URL).rstrip("/"),
    )

    b = _table(doc, "breaker", where)
    bw = f"{where}: [breaker]"
    _check_keys(b, _BREAKER_KEYS, bw)
    fraction = b.get("fraction", 0.20)
    if isinstance(fraction, bool) or not isinstance(fraction, int | float) or not 0 < fraction <= 1:
        raise ConfigError(f"{bw}: 'fraction' must be a number in (0, 1], got {fraction!r}")
    breaker = BreakerConfig(
        fraction=float(fraction),
        floor=_int(b, "floor", bw, 25),
        hold_days=_int(b, "hold_days", bw, 7, minimum=1),
    )

    c = _table(doc, "convert", where)
    cw = f"{where}: [convert]"
    _check_keys(c, _CONVERT_KEYS, cw)
    pandoc = _str(c, "pandoc_path", cw)
    convert = ConvertConfig(
        xlsx_stream_threshold_bytes=parse_size(
            c.get("xlsx_stream_threshold_bytes", 20 * 1000**2), where=f"{cw}: xlsx_stream_threshold_bytes"
        ),
        max_rows_per_sheet=_int(c, "max_rows_per_sheet", cw, 5000, minimum=1),
        max_page_bytes=parse_size(c.get("max_page_bytes", 1_000_000), where=f"{cw}: max_page_bytes"),
        pandoc_path=expand(pandoc) if pandoc else None,
    )

    raw_sources = doc.get("source", [])
    if not isinstance(raw_sources, list):
        raise ConfigError(f"{where}: 'source' must be an array of tables ([[source]])")
    sources = tuple(_parse_source(r, i + 1, where) for i, r in enumerate(raw_sources))
    seen: set[str] = set()
    for s in sources:
        if s.id in seen:
            raise ConfigError(f"{where}: duplicate source id {s.id!r}")
        seen.add(s.id)
        if s.path is not None and is_under(s.path, docs_repo):
            raise ConfigError(f"{where}: source {s.id!r} path {s.path} is inside docs_repo {docs_repo}")
        if s.path is not None and is_under(docs_repo, s.path):
            raise ConfigError(f"{where}: docs_repo {docs_repo} is inside source {s.id!r} path {s.path}")
        if s.kind.is_graph and s.is_live and graph.client_id is None:
            raise ConfigError(
                f"{where}: source {s.id!r} is kind {s.kind.value!r} and live, but [graph] client_id is "
                'not set; set it (IT app registration) or mark the source state = "paused"'
            )

    return Config(
        config_path=config_path,
        docs_repo=docs_repo,
        state_dir=state_dir,
        cache_dir=cache_dir,
        log_dir=log_dir,
        sources=sources,
        graph=graph,
        breaker=breaker,
        convert=convert,
        reconcile_interval_s=_int(a, "reconcile_interval_s", aw, 3600, minimum=60),
        poll_interval_s=_int(a, "poll_interval_s", aw, 300, minimum=30),
        tombstone_reap_days=_int(a, "tombstone_reap_days", aw, 180, minimum=1),
        principal=_str(a, "principal", aw),
        launchd_label_prefix=_str(a, "launchd_label_prefix", aw, "com.agentsync") or "com.agentsync",
    )


def load_config(path: Path | None = None) -> Config:
    """Read and validate sources.toml (default ``~/agent-context/sources.toml``); raises ConfigError."""
    p = expand(path) if path is not None else default_config_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{p}: not found; run `agentsync init` to write a commented template") from None
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read: {exc.strerror}") from None
    return parse_config(text, config_path=p)


def default_config_text() -> str:
    """Return the commented sources.toml template that ``agentsync init`` writes."""
    return _TEMPLATE


_TEMPLATE = """\
# agentsync — sources.toml
#
# The in-scope set: nothing else in agentsync names a cloud location.  Edit, then run `agentsync doctor`.
# Every key below shows its default; delete a line to keep the default.

[agentsync]
docs_repo = "~/agent-context/docs"                    # its own git repo, OUTSIDE ~/Library/CloudStorage
state_dir = "~/Library/Application Support/agentsync"  # manifest, cursors (0600), lock, heartbeat
cache_dir = "~/Library/Caches/agentsync"              # converter cache, rebuildable, never in git
log_dir = "~/Library/Logs/agentsync"                  # launchd agent logs
reconcile_interval_s = 3600                           # full enumeration cadence (hourly at <= 1e5 items)
poll_interval_s = 300                                 # delta poll cadence
tombstone_reap_days = 180
# principal = "you@example.com"                       # which signed-in identity this mirror is a view of

[graph]
# client_id = "00000000-0000-0000-0000-000000000000"  # Entra app registration (public client, device code)
#                                                     # unset = no Graph arms; local sources still work
tenant = "organizations"                              # or your tenant id / domain
# scopes = ["Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read"]
company = "agentsync"                                 # User-Agent: NONISV|<company>|agentsync/<version>

[breaker]                                             # deletion circuit breaker, per source
fraction = 0.20                                       # trip when deletions > max(fraction * live rows, floor)
floor = 25
hold_days = 7

[convert]
xlsx_stream_threshold_bytes = "20MB"                  # larger workbooks get a schema + sample page + CSV
max_rows_per_sheet = 5000
# pandoc_path = "/opt/homebrew/bin/pandoc"            # default: pypandoc_binary's bundled pandoc

# ---- sources -------------------------------------------------------------------------------------------
# One [[source]] per scope.  id: lowercase letters, digits and '-'; it names docs/mirror/<id>/.
# state: "paused" | "live" | "retired" (retired needs retired_reason).  Budgets are per cycle.

# A folder inside the OneDrive / SharePoint sync client (no IT involvement needed):
# [[source]]
# id = "onedrive-projects"
# kind = "local"
# path = "~/Library/CloudStorage/OneDrive-Contoso/Projects"
# sentinel = "README.txt"                             # positive control: must exist, or the walk is 'unknown'
# include = []                                        # empty = everything
# exclude = ["~$*", "*.tmp", ".~lock.*#", ".DS_Store", "._*"]
# max_materialise_bytes = "1GiB"                      # hydration budget per cycle (online-only files)
# max_files = 5000

# A manual drag-and-drop inbox:
# [[source]]
# id = "inbox"
# kind = "inbox"
# path = "~/agent-context/inbox"
# quiescence_s = 60                                   # skip files still being written

# A SharePoint document library or OneDrive via Graph delta (needs [graph] client_id):
# [[source]]
# id = "finance-library"
# kind = "graph_drive"
# site = "contoso.sharepoint.com:/sites/finance"      # or: drive_id = "b!..." ; or: drive_id = "me"
# folder = "/Shared Documents/FY26"                   # subtree filter, "/" = whole drive
# state = "paused"                                    # first pass is a full enumeration; flip to live

# An Outlook mail folder via Graph message delta:
# [[source]]
# id = "mail-projects"
# kind = "graph_mail"
# mailbox = "me"                                      # or a shared mailbox UPN (adds Mail.Read.Shared)
# folder = "Inbox"                                    # well-known name or folder id

# A Teams channel's messages via channel delta (ChannelMessage.Read.All needs admin consent):
# [[source]]
# id = "team-acme-general"
# kind = "graph_teams"
# team_id = "..."
# channel_id = "19:...@thread.tacv2"
"""
