"""Load and validate ``sources.toml`` into an immutable :class:`Config` (docs/design/CONTRACTS.md,
"config")."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import re
import stat
import tempfile
import tomllib
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from agentsync.errors import AccountHomeError, ConfigError
from agentsync.model import SourceKind, SourceState
from agentsync.paths import (
    DocsLayout,
    StatePaths,
    cloud_storage_roots,
    default_cache_dir,
    default_config_path,
    default_docs_repo,
    default_log_dir,
    default_state_dir,
    expand,
    is_cloud_path,
    is_under,
    same_folder,
)
from agentsync.policy import PolicyConfig, parse_policy_table
from agentsync.skill import AGENTSYNC_BIN

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

# Always ignored by every local arm, whatever ``exclude`` says: Finder metadata, AppleDouble files and the
# custom-icon file. A hand-written ``exclude`` adds to these instead of replacing them (field N12).
OS_JUNK_EXCLUDES: tuple[str, ...] = (".DS_Store", "._*", "Icon\r")

# Always ignored in an inbox, whatever ``exclude`` says: lock files, in-flight downloads and OS junk.
INBOX_IGNORES: tuple[str, ...] = (
    "~$*",
    "*.tmp",
    ".~lock.*#",
    "*.crdownload",
    "*.part",
    "*.partial",
    "*.download",
    *OS_JUNK_EXCLUDES,
)


def always_excluded(kind: SourceKind) -> tuple[str, ...]:
    """The globs the arm for ``kind`` drops on top of ``exclude`` (none for the Graph kinds).

    Part of the scope fingerprint (``manifest._scope_fingerprint``): a change here re-enumerates the source
    and retires what left scope, instead of reading it as deleted upstream and queueing purges.
    """
    if kind is SourceKind.INBOX:
        return INBOX_IGNORES
    if kind is SourceKind.LOCAL:
        return OS_JUNK_EXCLUDES
    return ()


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

_TOP_KEYS = frozenset(
    {"agentsync", "graph", "breaker", "convert", "source", "network", "policy", "governance"}
)
# [policy] is validated here (policy.parse_policy_table); [governance] by governance.load_governance, which
# imports config (so config cannot import it): a bad [governance] table fails `agentsync doctor`/purge.
_NETWORK_KEYS = frozenset({"proxy"})
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
_GRAPH_KEYS = frozenset(
    {"client_id", "tenant", "scopes", "company", "base_url", "cloud", "allow_device_code", "broker"}
)
_CLOUDS = frozenset({"global", "usgov", "usgov-dod", "china"})
_BREAKER_KEYS = frozenset({"fraction", "floor", "hold_days"})
_CONVERT_KEYS = frozenset(
    {
        "xlsx_stream_threshold_bytes",
        "max_rows_per_sheet",
        "max_page_bytes",
        "pandoc_path",
        "ocr",
        "recordings",
    }
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
    base_url: str = DEFAULT_GRAPH_BASE_URL
    cloud: str | None = None  # global | usgov | usgov-dod | china; None = inferred from base_url (graph.auth)
    allow_device_code: bool = False  # C15 1.4: device code is the last rung, off unless IT allows it
    broker: bool = True  # C15 1.1: try the macOS broker (Company Portal SSO extension) first

    @property
    def authority(self) -> str:
        """MSAL authority URL for ``tenant``."""
        return f"https://login.microsoftonline.com/{self.tenant}"


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """``[network]``: ``proxy`` = an http(s) proxy URL, or ``"direct"`` to ignore env/system proxies."""

    proxy: str | None = None


@dataclass(frozen=True, slots=True)
class ConvertConfig:
    """Converter options; every field but ``ocr`` and ``recordings`` participates in the converters'
    ``options_hash``.  ``ocr`` and ``recordings`` are the two switches: each says whether an engine is built
    and run."""

    xlsx_stream_threshold_bytes: int = 20 * 1000**2
    max_rows_per_sheet: int = 5000
    max_page_bytes: int = 1_000_000  # hard cap before a unit is split / row-capped with a sidecar
    # a config sets at least MIN_PAGE_BYTES (8 KiB); the cap bounds the guarded body of every page converted
    # under any cap it accepts, the guard's banner and footer, a fence and a sheet's lists included (a
    # recording's index, and a window with more keyframes than its footer lines fit, which keeps the text a
    # keyframe-free page shows and passes by those lines only, aside: CONTRACTS §16.263, §16.268); the front
    # matter above it (paths, URL, digests, title, summary), sized by the item's metadata rather than its
    # bytes (often under 1 KB, about 2 KB with a long path, URL and summary), sits outside the cap, never
    # budgeted, so a page file passes the cap by its front matter. A [convert] edit re-reads every local and
    # inbox page converted under other options before the edit's sync, a few per sync (its note starts `sync
    # again:` only while another sync reads more), except that an edit made before the first sync that
    # recorded [convert] re-reads nothing, and recordings and cloud (Graph) files are not re-read for an
    # options edit: a Graph file's page takes the new cap when its bytes next change, and a recording's page
    # keeps its cap until it is read again for another reason (B13 round 40 C1886, round 41 C2055, C2091,
    # C2021, round 42 C2220, C2306, C2254, lane B's C2297, C2256 with C2253, C2308, round 43 C2532)
    # None = the pypandoc_binary bundled pandoc, or on macOS its native arm64 copy when the operator installed
    # one (convert.pandoc.resolved_pandoc, CONTRACTS §16.114), by absolute path
    pandoc_path: Path | None = None
    ocr: bool = True  # False = never build or run the on-device OCR helper (convert/ocr.py)
    recordings: bool = True  # False = no media helper; recordings stay unconverted (convert/media.py)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One ``[[source]]`` table, validated.  Kind-specific fields are None when not applicable."""

    id: str
    kind: SourceKind
    state: SourceState = SourceState.LIVE
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    max_materialise_bytes: int = 1024**3  # bytes downloaded per cycle: online-only files only
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
    network: NetworkConfig = field(default_factory=NetworkConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)  # [policy]; load_policy adds policy.toml
    reconcile_interval_s: int = 3600
    poll_interval_s: int = 300
    tombstone_reap_days: int = 180
    principal: str | None = None
    launchd_label_prefix: str = "com.agentsync"
    graph_company_line: int | None = None  # see :func:`_graph_company_line`; status warns when set

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


MIN_PAGE_BYTES = 8192
"""The smallest ``[convert] max_page_bytes`` a config may set: below it the converters' fixed reserves (xlsx's
4,096 bytes, the untrusted-content banner, the sidecar digest footer and the cut notes) leave a page no room,
so a smaller cap is a ConfigError at load; a ConvertConfig built in code is not checked (B13 round 40 C1886,
C1888)."""


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


def _page_cap(table: Mapping[str, Any], where: str) -> int:
    """``[convert] max_page_bytes``, refused below :data:`MIN_PAGE_BYTES` (B13 round 40 C1886)."""
    cap = parse_size(table.get("max_page_bytes", 1_000_000), where=f"{where}: max_page_bytes")
    if cap < MIN_PAGE_BYTES:
        raise ConfigError(f"{where}: max_page_bytes must be at least 8 KiB ({MIN_PAGE_BYTES}), got {cap}")
    return cap


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


def _bool(t: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
    v = t.get(key, default)
    if not isinstance(v, bool):
        raise ConfigError(f"{where}: {key!r} must be true or false, got {v!r}")
    return v


def _str_list(t: Mapping[str, Any], key: str, where: str, default: tuple[str, ...]) -> tuple[str, ...]:
    v = t.get(key)
    if v is None:
        return default
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{where}: {key!r} must be a list of strings")
    return tuple(x for x in v if x.strip())


def canonical_source_root(path: Path) -> Path:
    """Resolve the symlinks in ``path`` up to, never inside, ``~/Library/CloudStorage``.

    ``~/OneDrive - Contoso`` (the link OneDrive creates) becomes ``~/Library/CloudStorage/OneDrive-Contoso``
    so every File Provider safeguard applies, while nothing inside a File Provider tree is touched at config
    load (an lstat there may need a TCC grant; the launcher's canary runs first). The walk stops at either
    form of :func:`~agentsync.paths.cloud_storage_roots`: under a home reached through a link (``/tmp`` is
    ``/private/tmp``) the first link resolves the home, and the lexical root alone would never match again,
    so the walk read the links inside the tree and followed one out of it (B13 round 5 F89).

    A link already being followed stops the walk, which returns that link with the rest of the path, as
    ``posixpath.realpath`` does; a link it has resolved before is reused. So a loop gives one answer from
    every spelling of the path: the walk stopped at a fixed depth, so a home reached through a link spent a
    level on it and returned another link of the loop than its canonical spelling did, and the readers' path
    match failed (B13 round 47 C3300, ruling 210). Never ``Path.resolve()``, which raises RuntimeError on a
    loop on Python 3.11 and 3.12, past every ``except OSError``. A link with no target or none this account
    can read, or a read that fails, stops the walk there, the rest kept as written."""
    clouds = cloud_storage_roots()
    # a link followed -> where it led; None while it is being followed (a loop meets it again)
    seen: dict[Path, Path | None] = {}

    def walk(base: Path, names: tuple[str, ...]) -> tuple[Path, bool]:
        # ``base`` holds no link; False when the walk stopped and the rest was kept as written
        for n, name in enumerate(names):
            candidate = base / name
            rest = names[n + 1 :]
            if any(is_under(base, r) or candidate == r for r in clouds):
                return Path(candidate, *rest), False
            if candidate in seen:
                led = seen[candidate]
                if led is None:
                    return Path(candidate, *rest), False
                base = led
                continue
            try:
                if not candidate.is_symlink():
                    base = candidate
                    continue
                target = candidate.readlink()
            except OSError:
                return Path(candidate, *rest), False
            seen[candidate] = None
            joined = Path(os.path.normpath(target if target.is_absolute() else base / target))
            led, done = walk(Path(joined.parts[0]), joined.parts[1:])
            if not done:
                return Path(led, *rest), False
            seen[candidate] = led
            base = led
        return base, True

    return walk(Path(path.parts[0]), path.parts[1:])[0]


def _expand_value(v: str, key: str, where: str) -> Path:
    """:func:`expand` for a path value read from the config file: a ``~name`` value expand refuses (ruling
    193) is a plain :class:`ConfigError` naming the file, table and key (exit 78, Reason config), never the
    :class:`AccountHomeError` usage form (exit 2, "run the command again"), which stays for a path the
    command line or AGENTSYNC_CONFIG gave (B13 round 46 C3039, ruling 198)."""
    try:
        return expand(v)
    except AccountHomeError:
        raise ConfigError(
            f"{where}: {key!r} {v} names another account's home; give it as an absolute path"
        ) from None


def _path_str(t: Mapping[str, Any], key: str, where: str) -> str | None:
    """A filesystem path value with its end as its writer wrote it: an empty or blank-only string read as
    missing, a trailing blank kept. :func:`_str` strips, so a source path ending in U+3000, U+00A0 or a space,
    which add-source writes as chosen, loaded as its sibling without the blank and a sync published the
    sibling's files (B13 round 47 C3333, ruling 209). A leading blank is dropped: no absolute or home path
    begins with one, and kept it made ``  ~name/x`` a folder under the current one, past ruling 198's refusal
    of another account's home. ``pandoc_path`` and every key that is no path keep :func:`_str`."""
    v = t.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ConfigError(f"{where}: {key!r} must be a string, got {type(v).__name__}")
    return v.lstrip() if v.strip() else None


def _literal_path(v: str, key: str, where: str) -> Path:
    """:func:`_expand_value` of a path read by :func:`_path_str`. A config written while the loader stripped
    may hold a blank its writer never meant; when the path as written is absent and its stripped spelling
    exists, the load stops naming both, never picking one (B13 round 47 C3333, ruling 209). Only a value
    that carries an outer blank is looked up, so a plain path is never touched at load."""
    p = _expand_value(v, key, where)
    stripped = v.strip()
    if stripped != v and not os.path.lexists(p):
        try:
            other = expand(stripped)
        except AccountHomeError:
            return p
        if os.path.lexists(other):
            raise ConfigError(
                f"{where}: {key!r} {v!r} does not exist but {stripped!r} does; edit sources.toml to the "
                "spelling you mean"
            )
    return p


def _path(t: Mapping[str, Any], key: str, where: str, default: Path) -> Path:
    v = _path_str(t, key, where)
    return _literal_path(v, key, where) if v else default


# ---------------------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------------------


RETIRE_ADVICE = (
    'state = "retired" and retired_reason = "<why>" (its pages become tombstones in one labelled commit)'
)
"""How a source is retired, in the words the cycle's vanished-source refusal and doctor's shared-content line
both print (B13 round 5 F97): the settings this module accepts for a retired source, ``retired_reason``
included, so advice followed word for word is not refused again."""


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
        # Resolved: a root reached through a symlink (OneDrive's own ``~/OneDrive - <Org>`` link to
        # ~/Library/CloudStorage/OneDrive-<Org>) must get every File Provider safeguard -- the launcher, the
        # TCC canary, the zero-children rule -- which all test the canonical path (review deploy-ops).
        path = canonical_source_root(_literal_path(_path_str(raw, "path", where) or "", "path", where))
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
        base_url=(_str(g, "base_url", gw, DEFAULT_GRAPH_BASE_URL) or DEFAULT_GRAPH_BASE_URL).rstrip("/"),
        cloud=_str(g, "cloud", gw),
        allow_device_code=_bool(g, "allow_device_code", gw, False),
        broker=_bool(g, "broker", gw, True),
    )
    if graph.cloud is not None and graph.cloud not in _CLOUDS:
        raise ConfigError(f"{gw}: 'cloud' must be one of {', '.join(sorted(_CLOUDS))}, got {graph.cloud!r}")

    n = _table(doc, "network", where)
    nw = f"{where}: [network]"
    _check_keys(n, _NETWORK_KEYS, nw)
    network = NetworkConfig(proxy=_str(n, "proxy", nw))

    content_policy = parse_policy_table(_table(doc, "policy", where), where=f"{where}: [policy]")
    _table(doc, "governance", where)  # must be a table; its keys are validated by governance.load_governance

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
    # read stripped, as before C3333: ruling 171's padded and multi-line pandoc_path spellings are read so by
    # both reports and install.sh, and an executable is no folder whose sibling a sync would publish (B13
    # round 47 C3333, measured: a literal read left those rows' pandoc unfound and the report's cut missed)
    pandoc = _str(c, "pandoc_path", cw)
    convert = ConvertConfig(
        xlsx_stream_threshold_bytes=parse_size(
            c.get("xlsx_stream_threshold_bytes", 20 * 1000**2), where=f"{cw}: xlsx_stream_threshold_bytes"
        ),
        max_rows_per_sheet=_int(c, "max_rows_per_sheet", cw, 5000, minimum=1),
        max_page_bytes=_page_cap(c, cw),
        pandoc_path=_expand_value(pandoc, "pandoc_path", cw) if pandoc else None,
        ocr=_bool(c, "ocr", cw, True),
        recordings=_bool(c, "recordings", cw, True),
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
        network=network,
        policy=content_policy,
        reconcile_interval_s=_int(a, "reconcile_interval_s", aw, 3600, minimum=60),
        poll_interval_s=_int(a, "poll_interval_s", aw, 300, minimum=30),
        tombstone_reap_days=_int(a, "tombstone_reap_days", aw, 180, minimum=1),
        principal=_str(a, "principal", aw),
        launchd_label_prefix=_str(a, "launchd_label_prefix", aw, "com.agentsync") or "com.agentsync",
        graph_company_line=_graph_company_line(text) if "company" in g else None,
    )


_TABLE_HEADER = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def _graph_company_line(text: str) -> int:
    """The 1-based line of ``[graph] company`` in sources.toml text: accepted but ignored since KISS K15 (the
    User-Agent is always ``NONISV|agentsync|agentsync/<version>``), so status names the line to delete. 0
    when the key is set in a form this scan cannot place (an inline ``graph = {...}`` table)."""
    table = ""
    for n, line in enumerate(text.splitlines(), 1):
        header = _TABLE_HEADER.match(line)
        if header is not None:
            table = header.group(1).replace(" ", "").replace('"', "")
        elif line.lstrip().startswith("[["):
            table = "[[]]"  # an array of tables ([[source]]): never [graph]
        elif (table == "graph" and re.match(r"\s*company\s*=", line)) or (
            table == "" and re.match(r"\s*graph\s*\.\s*company\s*=", line)
        ):
            return n
    return 0


NOT_A_FILE = "not a file (a FIFO, a socket or a device)"
""":class:`NotAFileError`'s reason: what a config read says of a path that is there and is neither a regular
file nor a folder, after ``cannot read: `` (B13 round 47 C3307 with C3251, C3254 and C3290)."""


class NotAFileError(OSError):
    """:func:`read_config_bytes`' refusal of a path that is there but is neither a regular file nor a folder
    (a FIFO, a socket, a device): an OSError, so every caller that maps a read's OSError maps this one too,
    with :data:`NOT_A_FILE` as its ``strerror``."""


def read_config_bytes(path: Path) -> bytes:
    """The bytes of the config file at ``path``, read so no reader can block on what is there: opened
    ``O_NONBLOCK | O_CLOEXEC``, links followed (a symlinked sources.toml is a working setup), then judged on
    the open descriptor (``fstat``, no race with a separate stat). A FIFO opens at once and is refused, as are
    a socket and a device, by :class:`NotAFileError`; a folder raises IsADirectoryError, the words a plain
    read gave it (``cannot read: Is a directory``); FileNotFoundError and every other OSError are the open's
    own. load_config, append_to_config, the exclude editor, governance.load_governance and policy's
    ``_policy_from_file`` read through it: a plain read of a FIFO at sources.toml waited forever, so every
    command hung (B13 round 47 C3307 with C3251, C3254 and C3290)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.EOPNOTSUPP, errno.ENXIO):  # a socket (macOS, Linux)
            raise NotAFileError(errno.EINVAL, NOT_A_FILE, str(path)) from None
        raise
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), str(path))
        if not stat.S_ISREG(mode):
            raise NotAFileError(errno.EINVAL, NOT_A_FILE, str(path))
        os.set_blocking(fd, True)
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def read_config_text(path: Path, *, newline: str | None = None) -> str:
    """:func:`read_config_bytes` decoded as UTF-8 the way ``Path.read_text`` (``newline=None``, universal
    newlines) or ``open(newline="")`` (line endings kept) decoded it before (B13 round 47 C3307)."""
    with io.TextIOWrapper(io.BytesIO(read_config_bytes(path)), encoding="utf-8", newline=newline) as fh:
        return fh.read()


def _config_missing(p: Path) -> ConfigError:
    """The error for a sources.toml whose read found nothing: a name that is a link to a missing target is
    told so, with that target, and how to mend it, never add-source, which refuses a name that is there (B13
    round 48 C3469, ruling 225); only an empty name is told to run add-source."""
    try:
        target = p.readlink()
    except OSError:  # nothing at the name, or not a link: the read's own not-found
        return ConfigError(f"{p}: not found; run `{AGENTSYNC_BIN} add-source <folder>` to create it")
    return ConfigError(
        f"{p}: a link to {target}, which is missing; restore its target or move the link aside"
    )


def load_config(path: Path | None = None) -> Config:
    """Read and validate sources.toml (default ``~/agent-context/sources.toml``); raises ConfigError. A
    dangling link at the name is told as one (:func:`_config_missing`, B13 round 48 C3469)."""
    p = expand(path) if path is not None else default_config_path()
    try:
        text = read_config_text(p)
    except FileNotFoundError:
        raise _config_missing(p) from None
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read: {exc.strerror}") from None
    return parse_config(text, config_path=p)


def default_config_text() -> str:
    """Return the sources.toml template that ``agentsync add-source`` (or the hidden ``init``) writes (KISS
    K15): one header line and a commented ``[governance] archive`` line; every other key keeps its default,
    and add-source appends the ``[[source]]`` and inbox tables."""
    return _TEMPLATE


_ID_BAD = re.compile(r"[^a-z0-9-]+")


def derive_source_id(path: Path, taken: Collection[str]) -> str:
    """A deterministic source id for a folder: its name slugged to ``SOURCE_ID_RE``, with ``-2``, ``-3`` …
    appended until it is not in ``taken`` (``add-source`` and :func:`ensure_inbox` use it)."""
    base = _ID_BAD.sub("-", path.name.lower()).strip("-")[:56] or "local"
    if not base[0].isalnum():
        base = "s" + base
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def toml_string(text: str) -> str:
    """``text`` as one TOML basic string that ``tomllib`` reads back as ``text``: ``json.dumps``'s escaping is
    TOML's, but it leaves DEL (U+007F) raw, which TOML 1.0 forbids in a basic string, so a folder named with
    one gave a sources.toml no command could load; DEL is written ``\u007f`` (B13 round 39 R39-B1, lane RB's
    note). The line and paragraph separators (U+2028, U+2029) and NEL (U+0085) are written ``\u2028``,
    ``\u2029`` and ``\u0085``: TOML takes them raw, but both reports split a line there, so a configured name
    holding one printed its pieces in clear (B13 round 41 R41-B1, lane RA's measurement; report_cuts aliases
    the escaped spellings). Every writer of a TOML string in agentsync goes through it."""
    escaped = json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007F")
    return escaped.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").replace("\x85", "\\u0085")


def local_source_table(source_id: str, path: Path) -> str:
    """The ``[[source]]`` table ``add-source`` writes for a folder: a live ``kind = "local"`` source with
    every other key at its default (excludes ``DEFAULT_EXCLUDES``, budget 1 GiB and 5000 files per cycle) and
    the sentinel left as a commented recommendation.  Starts with a blank line, ends with a newline."""
    return (
        f'\n[[source]]\nid = "{source_id}"\nkind = "local"\n'
        f"path = {toml_string(str(path))}\n"
        '# sentinel = "README.txt"   # recommended: a file that must always exist under path\n'
    )


def inbox_source_table(source_id: str, path: Path) -> str:
    """The ``[[source]]`` table :func:`ensure_inbox` writes: a live ``kind = "inbox"`` drop folder for files
    saved by hand (``.eml`` dragged out of Outlook, exports), every other key at its default.  Starts with a
    blank line, ends with a newline."""
    return (
        f'\n[[source]]\nid = "{source_id}"\nkind = "inbox"\n'
        f"path = {toml_string(str(path))}\n"
        "# quiescence_s = 60   # write to a temp name, then rename it into place; expect about a 60 s delay\n"
    )


def ensure_inbox(config_path: Path) -> tuple[Config, SourceConfig | None]:
    """Keep the mail inbox (KISS K05): create ``inbox`` beside the docs repo (``~/agent-context/inbox``, mode
    0700) and append :func:`inbox_source_table` for it with :func:`append_to_config`, unless a source (any
    kind, any state) is already on that folder, in any spelling macOS takes for it
    (:func:`paths.same_folder`), so no folder is configured twice.  Other inbox sources do not count: the
    guides name this folder, and a Mac with hand-added inbox sources elsewhere (field report 2026-10-05: six
    of them) would otherwise get no source on it.  Returns the config as it now stands and
    the source added (None when nothing changed).  Raises ConfigError (sources.toml missing or the result
    would not load) or OSError (the folder cannot be created, or a non-folder has its name), writing no
    table."""
    config = load_config(config_path)
    raw = expand(config.docs_repo).parent / "inbox"
    if not raw.is_dir():
        raw.mkdir(mode=0o700, parents=True)  # FileExistsError when a file has the name
        raw.chmod(0o700)  # mkdir's mode is masked by the umask
    path = canonical_source_root(raw)
    # by identity (paths.same_folder), so a source on ``Inbox`` is on this folder on a case-insensitive Mac
    # and no second source is added for it (B13 round 42 C2318)
    if any(s.path is not None and same_folder(s.path, path) for s in config.sources):
        return config, None
    sid = derive_source_id(path, {s.id for s in config.sources})
    config = append_to_config(config.config_path, inbox_source_table(sid, path))
    return config, next(s for s in config.sources if s.id == sid)


_T = TypeVar("_T")


def bounded_inbox_read(read: Callable[[], _T]) -> _T | None:
    """``read()`` under the listings' time limit (``arm_local.LISTING_TIMEOUT_S``, read at call time), None
    past it: a stat that follows the kept inbox's link may wait on a privacy prompt, and status, sync's NEXT
    and the guide read that entry, so none of them holds the command (B13 round 47 C3311). ``read``'s own
    exception is raised as it is."""
    # arm_local imports this module, so the bound is imported here; its limit is read at call time
    from agentsync import arm_local  # noqa: PLC0415 - arm_local imports config: a module-level import cycles

    try:
        return arm_local.call_with_timeout(read, arm_local.LISTING_TIMEOUT_S, name="agentsync-inbox")
    except arm_local.CallTimedOutError:
        return None


INBOX_ENTRY_ABSENT = "absent"
INBOX_ENTRY_FOLDER = "folder"
INBOX_ENTRY_NOT_A_FOLDER = "not-a-folder"
INBOX_ENTRY_UNREAD = "unread: "  # followed by the OSError's own words, no path


def inbox_entry(folder: Path) -> str:
    """What sits at ``folder`` (the kept inbox's name, as typed), read once with ``os.lstat`` and, for a
    link, ``os.stat`` (B13 round 45 C2936, ruling 194): :data:`INBOX_ENTRY_ABSENT` when the entry itself is
    missing (ENOENT, or ENOTDIR on a parent that is a file), :data:`INBOX_ENTRY_FOLDER` for a folder or a link
    to one, :data:`INBOX_ENTRY_NOT_A_FOLDER` for anything else or a link whose target is missing, not a folder
    or loops, and :data:`INBOX_ENTRY_UNREAD` with the OSError's words for any other error, a parent this
    account cannot search included.  os.path.lexists turns that denial into False and Path.is_dir on Python
    3.14 swallows EACCES, so neither is used."""
    try:
        st = os.lstat(folder)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return INBOX_ENTRY_ABSENT
        return f"{INBOX_ENTRY_UNREAD}{exc.strerror or type(exc).__name__}"
    if stat.S_ISDIR(st.st_mode):
        return INBOX_ENTRY_FOLDER
    if not stat.S_ISLNK(st.st_mode):
        return INBOX_ENTRY_NOT_A_FOLDER
    try:
        target = folder.stat()  # follows the link
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            return INBOX_ENTRY_NOT_A_FOLDER  # a dangling or looping link, or one through a file
        return f"{INBOX_ENTRY_UNREAD}{exc.strerror or type(exc).__name__}"
    return INBOX_ENTRY_FOLDER if stat.S_ISDIR(target.st_mode) else INBOX_ENTRY_NOT_A_FOLDER


def kept_inbox_not_a_folder(config: Config, src: SourceConfig) -> Path | None:
    """The kept inbox folder as typed (``inbox`` beside the docs repo, never resolved) when ``src`` is the
    inbox-kind source on it and :func:`inbox_entry` reads that entry as not a folder (a file or a link that
    does not lead to a folder: a link to a file, a dangling or looping link, one through a file), else None:
    the one test of that state (B13 round 47 C3260, ruling 210), which the cycle reads before a walk so the
    walk's alarm names the folder as typed, never the link's target (B13 round 46 C3007, ruling 204), doctor
    reads for the listable and volume rows, and loop's missing-root arm
    reads (``loop._inbox_root_not_a_folder``; its not-a-folder arm checks the live entry alone, since a
    folder put back is no finding here). The source is matched by its stored path, compared with
    canonical_source_root of that folder; a local source at that path keeps its own root words, as doctor and
    the loop give it."""
    on = kept_inbox_entry(config, src)
    return on[0] if on is not None and on[1] == INBOX_ENTRY_NOT_A_FOLDER else None


def kept_inbox_entry(config: Config, src: SourceConfig) -> tuple[Path, str] | None:
    """``(typed, entry)`` when ``src`` is the inbox-kind source on the kept inbox (its stored path compared
    with canonical_source_root of the folder as typed), ``entry`` being :func:`inbox_entry`'s one read of it;
    else None, a local source at that path included (it keeps its own root words). The state test is
    :func:`kept_inbox_not_a_folder`, built on this; the walk's probe reads the unread entry from it apart
    (B13 round 47 C3211) and doctor the typed path of a link to a real folder (C3217), so neither is a second
    test of "not a folder"."""
    if src.kind is not SourceKind.INBOX or src.path is None:  # a local source keeps its own root words
        return None
    typed = expand(config.docs_repo).parent / "inbox"
    try:
        if expand(src.path) == canonical_source_root(typed):
            return typed, inbox_entry(typed)
    except OSError:
        return None
    return None


# status's line after ``inbox:``, printed only when no live inbox source sits there
INBOX_STATE_LINE = "inbox state: "
INBOX_LIVE = "live "  # inbox_state's words for a live inbox source on the kept folder: ``live <folder>``


def inbox_state(config: Config) -> tuple[Path, str]:
    """The kept inbox folder and what sits on it, decided once for every reader (B13 round 43 C2410, ruling
    162): ``(folder, words)``, the folder ``inbox`` beside the docs repo as typed (``expand``, never resolved)
    and the words status's ``inbox state:`` line and the setup report's ``inbox:`` line print.

    An entry on that name that is not a folder (a file or a link that does not lead to a folder) reads
    ``not added (<folder> is not a folder)`` whatever sources.toml lists: it is what ensure_inbox's mkdir
    stops on.  Otherwise the sources are matched against the form ensure_inbox stores
    (:func:`canonical_source_root`) by identity (:func:`paths.same_folder`): an inbox source there reads its
    state and the folder (``live <folder>``, ``paused <folder>``, ``retired <folder>``; a live one first),
    another kind's source reads ``not added (<folder> is source '<id>' (<kind>, <state>), not an inbox
    source)``, and none reads ``not added (no source on <folder>)``.  Reads the disk only through
    :func:`inbox_entry` (one lstat, and a stat for a link) and the readlinks canonical_source_root and
    same_folder already make; the first two under :func:`bounded_inbox_read`, past which the entry reads
    ``not read (timed out)`` (B13 round 47 C3311).  An entry that cannot be read (a link into a folder this
    account cannot search, a parent this account cannot search, a privacy refusal under CloudStorage) reads
    ``not read (<why>)``, the OSError's own words with no path, never ``is not a folder``: os.path.isdir
    would call such a link to a real folder not a folder, and os.path.lexists would call a denied entry
    absent (B13 round 44 C2644 with C2636, ruling 180; round 45 C2936, ruling 194)."""
    folder = expand(config.docs_repo).parent / "inbox"

    def read() -> tuple[str, Path | None]:
        kind = inbox_entry(folder)
        if kind.startswith(INBOX_ENTRY_UNREAD) or kind == INBOX_ENTRY_NOT_A_FOLDER:
            return kind, None
        return kind, canonical_source_root(folder)

    got = bounded_inbox_read(read)  # past the listings' limit: not read (B13 round 47 C3311)
    kind, kept = got if got is not None else (f"{INBOX_ENTRY_UNREAD}timed out", None)
    if kind.startswith(INBOX_ENTRY_UNREAD):
        return folder, f"not read ({kind[len(INBOX_ENTRY_UNREAD) :]})"
    if kind == INBOX_ENTRY_NOT_A_FOLDER or kept is None:
        return folder, f"not added ({folder} is not a folder)"
    on = [s for s in config.sources if s.path is not None and same_folder(expand(s.path), kept)]
    inbox = sorted((s for s in on if s.kind == SourceKind.INBOX), key=lambda s: s.state != SourceState.LIVE)
    if inbox:
        state = inbox[0].state.value
        return folder, f"{INBOX_LIVE}{folder}" if state == SourceState.LIVE else f"{state} {folder}"
    if on:
        other = on[0]
        kind, state = other.kind.value, other.state.value
        return folder, f"not added ({folder} is source '{other.id}' ({kind}, {state}), not an inbox source)"
    return folder, f"not added (no source on {folder})"


def live_inbox(config: Config) -> Path | list[tuple[Path, str]] | None:
    """The live inbox the guides name, decided once for :meth:`publish.Publisher.root_guide` and loop's
    ``nothing to draft yet`` step (B13 round 44 C2602 with C2649 and C2719, ruling 181): the folder
    :func:`ensure_inbox` keeps, as typed (matched in its stored form, :func:`canonical_source_root`), when a
    live inbox source sits on it, matched by identity (:func:`paths.same_folder`: its id and spelling may
    differ), else the only
    live inbox source's path, else every live inbox source as ``(path, id)`` sorted by id, since no one of
    them is the inbox, else None.

    The kept entry is read once (:func:`inbox_entry`): when it is not a folder (a file or a link that does
    not lead to a folder) or cannot be read, every candidate same_folder with it is dropped from both
    branches, since its stored path was canonicalized into that file or that link's target, so neither guide
    names what inbox_state calls ``not added (<folder> is not a folder)``; a link to a real folder is still
    the kept folder (B13 round 45 C2807 with C2937, ruling 194), returned as typed, as inbox_state names it,
    never the link's target (round 47 C3217; every caller only shows it). That read is bounded by the
    listings' time limit, canonical_source_root's walk with it, since a link's target may sit where a privacy
    prompt holds a stat: past it the guide names no inbox rather than holding the sync (B13 round 47
    C3311)."""
    inboxes = sorted(
        (
            (s.path, s.id)
            for s in config.sources
            if s.kind is SourceKind.INBOX and s.is_live and s.path is not None
        ),
        key=lambda pair: pair[1],
    )
    folder = expand(config.docs_repo).parent / "inbox"
    # the canonical path's lstat walk and the entry's stat together, each able to wait on a privacy prompt
    got = bounded_inbox_read(lambda: (canonical_source_root(folder), inbox_entry(folder)))
    if got is None:  # past the limit: no inbox named, never a held sync (B13 round 47 C3311)
        return None
    kept, entry = got
    if entry not in (INBOX_ENTRY_ABSENT, INBOX_ENTRY_FOLDER):
        inboxes = [(path, sid) for path, sid in inboxes if not same_folder(path, kept)]
    elif any(same_folder(path, kept) for path, _ in inboxes):
        return folder  # as typed, as inbox_state names it, never a link's target (B13 round 47 C3217)
    if len(inboxes) == 1:
        return inboxes[0][0]
    return inboxes or None


def append_to_config(config_path: Path, table: str) -> Config:
    """Append ``table`` (TOML text) to the sources.toml at ``config_path``, keeping every existing byte and
    comment; the result is validated with :func:`parse_config` BEFORE anything is written, then replaces the
    file atomically (same directory, fsync, ``os.replace``) with the file's mode kept.  Returns the new
    Config; raises ConfigError (and writes nothing) when the result would not load."""
    p = expand(config_path)
    target = Path(os.path.realpath(p))  # keeps a link; a loop is an OSError (C3469)
    try:
        text = read_config_text(target)
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        raise _config_missing(p) from None  # a dangling link is told as one, as load_config tells it (C3469)
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read: {exc.strerror}") from None
    if text and not text.endswith("\n"):
        text += "\n"
    new_text = text + table if table.endswith("\n") else text + table + "\n"
    config = parse_config(new_text, config_path=p)  # validate before writing anything
    _replace_config(target, new_text, mode)
    return config


def _replace_config(target: Path, text: str, mode: int) -> None:
    """Write ``text`` over ``target`` atomically: a temporary file in the same directory, fsync, then
    ``os.replace``, with ``mode`` kept (0600 when it is 0)."""
    fd, tmp_name = tempfile.mkstemp(prefix=".sources.", suffix=".toml.tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:  # line endings as given
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        tmp = Path(tmp_name)
        tmp.chmod(mode or 0o600)
        tmp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink()
        raise


_TABLE_LINE = re.compile(r"^\s*\[")
_SOURCE_TABLE_LINE = re.compile(r"^\s*\[\[\s*source\s*\]\]\s*(?:#.*)?$")
_EXCLUDE_KEY_LINE = re.compile(r"^\s*exclude\s*=")
_ID_KEY_LINE = re.compile(r"^\s*id\s*=")


def set_source_exclude(
    config_path: Path, source_id: str, exclude: Collection[str], *, expected: Collection[str] | None = None
) -> Config:
    """Set the ``exclude`` of the ``[[source]]`` table whose id is ``source_id`` to ``exclude``, as one line,
    editing the file's text so every other byte and comment stays (B13 round 4 Q71): an existing ``exclude``
    key's whole span (one line or an array over several) is replaced, else the line goes after the table's
    ``id`` line.  The result must load (:func:`parse_config`), give this source exactly ``exclude`` and leave
    every other table and key as it was, BEFORE anything is written; then the file is replaced atomically
    with its mode kept.  Raises ConfigError (and writes nothing) otherwise, and when ``expected`` (the
    source's exclude ``exclude`` was built from) is given and the file read here no longer has it: an edit
    made since that read is not written over (B13 round 5 F70)."""
    p = expand(config_path)
    target = Path(os.path.realpath(p))  # keeps a link; a loop is an OSError (C3469)
    try:
        text = read_config_text(target, newline="")  # a CRLF file stays CRLF
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        raise ConfigError(f"{p}: not found") from None
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read: {exc.strerror}") from None
    if expected is not None and parse_config(text, config_path=p).source(source_id).exclude != tuple(
        expected
    ):
        raise ConfigError(f"{p}: source {source_id!r}'s exclude changed since it was read; run it again")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += eol
    span = _source_table_span(lines, source_id)
    if span is None:
        raise ConfigError(f"{p}: no [[source]] table with id {source_id!r} that can be edited as text")
    start, end = span
    new_line = f"exclude = [{', '.join(toml_string(g) for g in exclude)}]"
    key = next((i for i in range(start + 1, end) if _EXCLUDE_KEY_LINE.match(lines[i])), None)
    if key is not None:
        stop = next((j for j in range(key + 1, end + 1) if _loads("".join(lines[key:j])) is not None), None)
        if stop is None:
            raise ConfigError(f"{p}: the exclude key of source {source_id!r} does not end inside its table")
        lines[key:stop] = [new_line + _trailing_comment(lines[key:stop]) + eol]
    else:
        at = next((i for i in range(start + 1, end) if _ID_KEY_LINE.match(lines[i])), None)
        if at is None:
            raise ConfigError(f"{p}: the [[source]] table of {source_id!r} has no id line to follow")
        lines.insert(at + 1, new_line + eol)
    new_text = "".join(lines)
    before, after = _loads(text), _loads(new_text)
    if before is None or after is None:
        raise ConfigError(f"{p}: the edit of source {source_id!r}'s exclude does not load")
    for table in before.get("source", []):
        if isinstance(table, dict) and table.get("id") == source_id:
            table["exclude"] = list(exclude)
    if before != after:
        raise ConfigError(f"{p}: the edit of source {source_id!r}'s exclude would change another key")
    config = parse_config(new_text, config_path=p)
    if config.source(source_id).exclude != tuple(exclude):
        raise ConfigError(f"{p}: the edit of source {source_id!r}'s exclude does not read back")
    _replace_config(target, new_text, mode)
    return config


def _trailing_comment(span: list[str]) -> str:
    """The comment after a key's value on its last line (``]  # keep backups out``), with the space before
    it, or "": the first ``#`` before which the span parses (one inside a string leaves it unterminated)."""
    last = span[-1].rstrip("\r\n")
    head = "".join(span[:-1])
    for at, char in enumerate(last):
        if char == "#" and _loads(head + last[:at]) is not None:
            gap = len(last[:at]) - len(last[:at].rstrip())
            return " " * max(gap, 1) + last[at:].rstrip()
    return ""


def _loads(text: str) -> dict[str, Any] | None:
    """``text`` parsed as TOML, or None when it does not parse."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _source_table_span(lines: list[str], source_id: str) -> tuple[int, int] | None:
    """``(header line, end)`` of the one ``[[source]]`` table whose id is ``source_id`` (it ends at the next
    table header), or None when none or more than one is (or a table does not parse on its own)."""
    found: list[tuple[int, int]] = []
    for start, line in enumerate(lines):
        if not _SOURCE_TABLE_LINE.match(line):
            continue
        end = next((j for j in range(start + 1, len(lines)) if _TABLE_LINE.match(lines[j])), len(lines))
        parsed = _loads("".join(lines[start:end]))
        tables = parsed.get("source") if parsed is not None else None
        if isinstance(tables, list) and len(tables) == 1 and tables[0].get("id") == source_id:
            found.append((start, end))
    return found[0] if len(found) == 1 else None


# The command by its full path, which works with ~/.local/bin off PATH (B13 round 6 G79, as §16.51 Q49).
_TEMPLATE = f"""\
# agentsync sources.toml: `{AGENTSYNC_BIN} add-source <folder>` adds one [[source]] table per
# folder below.

# [governance]
# archive = true   # on: keep deleted pages and a snapshot tag per checkpoint, never compact; off: default
"""
