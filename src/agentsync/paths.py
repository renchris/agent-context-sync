"""Default locations, the docs/ layout, the state-dir layout, and include/exclude glob matching."""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

APP_NAME = "agentsync"

CLOUD_STORAGE_ROOT = Path("~/Library/CloudStorage")


def expand(path: str | Path) -> Path:
    """Expand ``~`` and return an absolute path (not resolved: symlinks are not followed)."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else Path.cwd() / p


def default_agent_context_dir() -> Path:
    """Return ``~/agent-context``, the parent of the default docs repo and sources.toml."""
    return expand("~/agent-context")


CONFIG_ENV = "AGENTSYNC_CONFIG"
"""Environment variable naming the sources.toml every command uses when ``--config`` is not given."""


def default_config_path() -> Path:
    """Return the default sources.toml path: ``$AGENTSYNC_CONFIG`` when set (scripts/install.sh honours the
    same variable), else ``~/agent-context/sources.toml``."""
    env = os.environ.get(CONFIG_ENV, "").strip()
    if env:
        return expand(env)
    return expand("~/agent-context/sources.toml")


def default_docs_repo() -> Path:
    """Return the default docs git repo (``~/agent-context/docs``), outside any cloud-synced path."""
    return default_agent_context_dir() / "docs"


def default_state_dir() -> Path:
    """Return the default machine-local state dir (manifest, cursors, lock, heartbeat)."""
    return expand(f"~/Library/Application Support/{APP_NAME}")


def default_cache_dir() -> Path:
    """Return the default converter cache root (rebuildable, never in git)."""
    return expand(f"~/Library/Caches/{APP_NAME}")


def default_log_dir() -> Path:
    """Return the default log dir used by the launchd agents."""
    return expand(f"~/Library/Logs/{APP_NAME}")


def is_under(path: Path, root: Path) -> bool:
    """Return True when ``path`` equals or lies beneath ``root`` (lexically, after ``expand``)."""
    p, r = expand(path), expand(root)
    return p == r or r in p.parents


def is_cloud_path(path: Path) -> bool:
    """Return True for any path under ``~/Library/CloudStorage`` (a File Provider tree)."""
    return is_under(path, expand(CLOUD_STORAGE_ROOT))


@dataclass(frozen=True, slots=True)
class StatePaths:
    """Every file agentsync keeps under its machine-local state dir (never inside docs/)."""

    root: Path

    @property
    def db(self) -> Path:
        """SQLite manifest + cursors (mode 0600)."""
        return self.root / "manifest.sqlite"

    @property
    def lock(self) -> Path:
        """Single-writer flock file."""
        return self.root / "agentsync.lock"

    @property
    def heartbeat(self) -> Path:
        """Liveness heartbeat JSON, one entry per source, written after every completed pass."""
        return self.root / "heartbeat.json"

    @property
    def token_cache_fallback(self) -> Path:
        """0600 MSAL token cache used ONLY when the Keychain is unavailable (logged)."""
        return self.root / "msal_token_cache.bin"

    @property
    def keychain_marker(self) -> Path:
        """msal-extensions' Keychain persistence 'location' (a signal file, holds no secret)."""
        return self.root / "msal_token_cache.keychain"

    @property
    def staging(self) -> Path:
        """Per-cycle scratch for materialised/downloaded bytes; emptied at cycle start."""
        return self.root / "staging"

    @property
    def teams_store(self) -> Path:
        """Per-channel message store the Teams arm rolls months up from."""
        return self.root / "teams"

    @property
    def logs(self) -> Path:
        """Log directory used when running outside launchd."""
        return self.root / "logs"


@dataclass(frozen=True, slots=True)
class DocsLayout:
    """The fixed layout of the docs/ git repo (design section 4.6)."""

    root: Path

    @property
    def mirror(self) -> Path:
        """Tier 1: generated, 1:1, never hand-edited."""
        return self.root / "mirror"

    @property
    def archive(self) -> Path:
        """``[governance] archive``: the last full page of every item a source deleted, kept searchable."""
        return self.root / "archive"

    @property
    def topics(self) -> Path:
        """Tier 2: agent-curated pages with pinned ``sources:``."""
        return self.root / "topics"

    @property
    def index_md(self) -> Path:
        """Root INDEX.md (llms.txt shape, <= 25 KB)."""
        return self.root / "INDEX.md"

    @property
    def readme_md(self) -> Path:
        """README.md: how the folder is built, the refresh-queue command, retention owner."""
        return self.root / "README.md"

    @property
    def claude_md(self) -> Path:
        """Root CLAUDE.md (three lines)."""
        return self.root / "CLAUDE.md"

    @property
    def changelog_md(self) -> Path:
        """Generated index of the last 30 days of CHANGELOG sections."""
        return self.root / "CHANGELOG.md"

    @property
    def changelog_dir(self) -> Path:
        """Append-only monthly changelog files ``CHANGELOG/<yyyy-mm>.md``."""
        return self.root / "CHANGELOG"

    @property
    def depends_tsv(self) -> Path:
        """Generated page<TAB>source<TAB>pinned_sha<TAB>role."""
        return self.root / "DEPENDS.tsv"

    @property
    def synonyms_tsv(self) -> Path:
        """Hand-maintained term<TAB>expansion<TAB>owner."""
        return self.root / "SYNONYMS.tsv"

    @property
    def by_entity_tsv(self) -> Path:
        """Generated entity<TAB>page."""
        return self.root / "_index" / "by-entity.tsv"

    @property
    def sync_dir(self) -> Path:
        """``_sync/``: STATE.md (gitignored), QUARANTINE.tsv, STATE.snapshot.md."""
        return self.root / "_sync"

    @property
    def state_md(self) -> Path:
        """Working-tree-only status page the agent reads first (gitignored)."""
        return self.sync_dir / "STATE.md"

    @property
    def state_snapshot_md(self) -> Path:
        """Committed STATE snapshot, written only on a cycle that already has a content commit."""
        return self.sync_dir / "STATE.snapshot.md"

    @property
    def quarantine_tsv(self) -> Path:
        """Generated source_id<TAB>path<TAB>reason."""
        return self.sync_dir / "QUARANTINE.tsv"

    @property
    def manifest_dir(self) -> Path:
        """``_manifest/``: NDJSON shards only (one per source), committed."""
        return self.root / "_manifest"

    @property
    def gitignore(self) -> Path:
        """docs/.gitignore."""
        return self.root / ".gitignore"

    @property
    def gitattributes(self) -> Path:
        """docs/.gitattributes."""
        return self.root / ".gitattributes"

    def rel(self, path: Path) -> str:
        """Return ``path`` as a POSIX string relative to the docs repo root (raises ValueError outside it)."""
        return expand(path).relative_to(expand(self.root)).as_posix()


# ---------------------------------------------------------------------------------------------------------
# include / exclude globs
# ---------------------------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile one glob to a regex over POSIX relative paths."""
    pat = pattern.strip()
    anchored = "/" in pat.rstrip("/")
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    out: list[str] = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pat.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pat[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{body}$", re.IGNORECASE)


def glob_match(rel_path: str, pattern: str) -> bool:
    """Match one POSIX relative path against one glob (gitignore-like, case-insensitive).

    ``**`` spans directories, ``*``/``?`` stay within one segment, a pattern with no ``/`` matches the
    basename at any depth, a trailing ``/`` matches everything beneath that directory.
    """
    return _glob_regex(pattern).match(PurePosixPath(rel_path).as_posix()) is not None


def is_included(rel_path: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    """Return True if ``rel_path`` matches any include glob (empty include = all) and no exclude glob."""
    if include and not any(glob_match(rel_path, p) for p in include):
        return False
    return not any(glob_match(rel_path, p) for p in exclude)
