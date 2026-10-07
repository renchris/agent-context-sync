"""L4 curation contract: topics/ ``sources:`` frontmatter, DEPENDS.tsv, refresh queue, STALE banners (owner:
curate).

Path convention (adapts design 4.5 to docs/ being its own repo): DEPENDS.tsv columns 1-2 are relative to the
DOCS REPO ROOT (``topics/…``, ``mirror/…``) and the refresh queue resolves them from the docs repo root.

Every finding this module emits is ``blocking=False``: the curated layer is agent-written, and a bad pin or a
missing ``entity:`` must never stop the mirror from syncing.  ``checkpoint_blockers`` is where they bite: they
hold the ``curated`` checkpoint, and ``agentsync curate`` exits 1 on any of them.  The meeting citation lint's
CITE-* findings never do: ``lint_meeting_citations`` stays a warning.  Outside tables its quote attaches to
the last tag before it in its bullet or line, even past a sentence end: the spec defines only the cell end.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import posixpath
import re
import shutil
import stat
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from agentsync import gitops, slug
from agentsync.errors import CurateError, GitError
from agentsync.frontmatter import FrontmatterError, parse_frontmatter, split_frontmatter
from agentsync.manifest import DependsRow
from agentsync.model import LintFinding
from agentsync.paths import DocsLayout, expand

DEPENDS_HEADER = "page\tsource\tpinned_sha\trole"
BY_ENTITY_HEADER = "entity\tpage"
ROLES: tuple[str, ...] = ("primary", "corroborating")
HAND_WRITTEN = "hand-written"

VERDICTS: tuple[str, ...] = (
    "STALE",
    "SOURCE-DELETED",
    "SOURCE-UNREADABLE",
    "MISSING-OR-UNPARSEABLE",
    "UNPINNED",
    "BAD-PIN",
    "MALFORMED",
)

STALE_BANNER = "> ⚠ STALE — sources changed since {date}; see DEPENDS.tsv"
RETIRED_BANNER = "> ⚠ SOURCE RETIRED — {reason}"

_log = logging.getLogger(__name__)

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX64_BYTES = re.compile(rb"[0-9a-f]{64}")
_PLAIN_YAML_PATH = re.compile(r"[\w./+=~@-]+")  # a path YAML reads back unquoted in a flow mapping
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ADOPTED_AT = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?")
_TOP_LEVEL_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:")
_TSV_UNSAFE = ("\t", "\n", "\r")
_EXCLUDED_PAGE_NAMES = frozenset({"CLAUDE.md", "INDEX.md"})
_RESERVED_ADOPT_NAMES = frozenset({"claude.md", "index.md"})  # casefolded: APFS is case-insensitive
_ADOPT_ASSET_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"})
_ADOPT_STAMP_KEYS = frozenset({"provenance", "adopted_at", "sources"})
_SF_DATALESS = 0x40000000  # st_flags bit of a File Provider placeholder whose bytes are not local
_STALE_PREFIX = STALE_BANNER.split("{date}", 1)[0]
_RETIRED_PREFIX = RETIRED_BANNER.split("{reason}", 1)[0]
_BOM = "﻿"
_MAX_REPORTED_PROBLEMS = 50
_PAGE_MAX_LINES = 400  # design 4.6 page budget: one subject per page, read whole
_PAGE_MAX_BYTES = 25_000
# What a source may cite (else SOURCE-NOT-MIRROR), and the sources: prefixes resolved from the docs root.
_DOCS_ROOT_SOURCE_PREFIXES = ("mirror/", "archive/")

CHECKPOINT_VERDICTS: frozenset[str] = frozenset(
    {"STALE", "UNPINNED", "BAD-PIN", "MALFORMED", "MISSING-OR-UNPARSEABLE"}
)
"""Refresh verdicts that hold the ``curated`` checkpoint for a page changed since it.  SOURCE-DELETED and
SOURCE-UNREADABLE never do: the source's state is not the page's fault and can outlive any session."""
_VERDICT_HINTS: Mapping[str, str] = {
    "STALE": "the source changed since its at_rendered_sha256 pin: re-read it, update the page and the pin",
    "UNPINNED": "no at_rendered_sha256 pin",
    "BAD-PIN": "at_rendered_sha256 is not 64 lowercase hex chars",
    "MALFORMED": "malformed DEPENDS row",
    "MISSING-OR-UNPARSEABLE": "the source has no readable rendered_sha256: fix the path",
}
_NAMED_BY_LINT: Mapping[str, str] = {"MISSING-OR-UNPARSEABLE": "SOURCE-MISSING"}
"""A refresh verdict whose page already carries this lint finding for the same condition is not repeated."""


# ---------------------------------------------------------------------------------------------------------
# value types
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopicSource:
    """One ``sources:`` entry as written (path is PAGE-relative, or docs-root-relative when it starts
    ``mirror/`` or ``archive/``: see ``resolve_source_path``)."""

    path: str
    at_rendered_sha256: str
    role: str


@dataclass(frozen=True, slots=True)
class TopicPage:
    """Parsed frontmatter of one curated page."""

    path: str  # docs-repo-relative, e.g. topics/clients/acme/acme-commercial.md
    kind: str | None
    entity: str | None
    sources: tuple[TopicSource, ...]
    depends_on_pages: tuple[str, ...]
    aliases: tuple[str, ...]
    provenance: str | None  # "hand-written" for adopted pages (exempt from STALE, absent from DEPENDS.tsv)
    adopted_at: str | None
    purpose: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshVerdict:
    """One refresh-queue output row (the design 4.5 script's vocabulary and order: ``sort -u``)."""

    verdict: str
    page: str
    source: str
    detail: str = ""

    def line(self) -> str:
        """Render exactly as the shell script prints it (tab-separated)."""
        if self.verdict == "MALFORMED":  # the script prints no source column for a short row
            return f"{self.verdict}\t{self.page}\t{self.detail}"
        parts = [self.verdict, self.page, self.source]
        if self.detail:
            parts.append(self.detail)
        return "\t".join(parts)


@dataclass(frozen=True, slots=True)
class _Banners:
    """The banner block at the top of a curated page body, and the body below it."""

    stale: str | None
    retired: tuple[str, ...]
    rest: str


# ---------------------------------------------------------------------------------------------------------
# parsing topics/
# ---------------------------------------------------------------------------------------------------------


def iter_topic_pages(layout: DocsLayout) -> list[str]:
    """Docs-repo-relative paths of every curated page: ``topics/**/*.md`` except CLAUDE.md and INDEX.md,
    sorted."""
    root = layout.topics
    if not root.is_dir():
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in filenames:
            if name.endswith(".md") and name not in _EXCLUDED_PAGE_NAMES:
                out.append(layout.rel(Path(dirpath) / name))
    return sorted(out)


def _read_page_text(layout: DocsLayout, rel_path: str) -> str:
    """Read a curated page as UTF-8 (BOM dropped); CurateError when unreadable."""
    try:
        raw = (layout.root / rel_path).read_bytes()
    except OSError as exc:
        raise CurateError(f"{rel_path}: unreadable: {exc.strerror or exc}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CurateError(f"{rel_path}: not UTF-8 ({exc.reason} at byte {exc.start})") from None
    return text.removeprefix(_BOM)


def _scalar_str(value: Any, where: str) -> str | None:
    """Coerce one optional YAML scalar to a stripped string (None/empty -> None); CurateError otherwise."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise CurateError(f"{where}: must be a string, got {type(value).__name__}")
    text = str(value).strip()
    return text or None


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    """Coerce an optional YAML list of scalars (or one scalar) to a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, list):
        out: list[str] = []
        for i, item in enumerate(value):
            text = _scalar_str(item, f"{where}[{i}]")
            if text is not None:
                out.append(text)
        return tuple(out)
    text = _scalar_str(value, where)
    return () if text is None else (text,)


def _tsv_safe(value: str, where: str) -> str:
    """Return ``value`` if it can live in one TSV cell, else raise CurateError."""
    if any(c in value for c in _TSV_UNSAFE):
        raise CurateError(f"{where}: must not contain a tab or a line break")
    return value


def _parse_source(rel_path: str, index: int, entry: Any) -> TopicSource:
    """Validate one ``sources:`` entry's shape (values are linted later, not rejected here)."""
    where = f"{rel_path}: sources[{index}]"
    if not isinstance(entry, dict):
        raise CurateError(f"{where}: must be a mapping {{path, at_rendered_sha256, role}}")
    path = entry.get("path")
    if not isinstance(path, str) or not path.strip():
        raise CurateError(f"{where}: 'path' must be a non-empty string")
    pin_raw = entry.get("at_rendered_sha256")
    if pin_raw is None:
        pin = ""
    elif isinstance(pin_raw, str):
        pin = pin_raw.strip()
    elif isinstance(pin_raw, int) and not isinstance(pin_raw, bool):
        pin = str(pin_raw)  # an all-digit pin YAML read as an int; a wrong length surfaces as BAD-PIN
    else:
        raise CurateError(f"{where}: 'at_rendered_sha256' must be a string")
    role_raw = entry.get("role")
    if role_raw is not None and not isinstance(role_raw, str):
        raise CurateError(f"{where}: 'role' must be a string")
    role = (role_raw or "").strip()
    return TopicSource(
        path=_tsv_safe(path.strip(), f"{where}.path"),
        at_rendered_sha256=_tsv_safe(pin, f"{where}.at_rendered_sha256"),
        role=_tsv_safe(role, f"{where}.role"),
    )


def _parse_topic_text(rel_path: str, text: str) -> TopicPage:
    """Parse a curated page's text (see ``parse_topic_page``)."""
    try:
        data, _body = parse_frontmatter(text.removeprefix(_BOM))
    except FrontmatterError as exc:
        raise CurateError(f"{rel_path}: {exc}") from None
    raw_sources = data.get("sources")
    if raw_sources is None:
        sources: tuple[TopicSource, ...] = ()
    elif isinstance(raw_sources, list):
        sources = tuple(_parse_source(rel_path, i, e) for i, e in enumerate(raw_sources))
    else:
        raise CurateError(f"{rel_path}: 'sources' must be a list of {{path, at_rendered_sha256, role}}")
    entity = _scalar_str(data.get("entity"), f"{rel_path}: entity")
    if entity is not None:
        _tsv_safe(entity, f"{rel_path}: entity")
    return TopicPage(
        path=rel_path,
        kind=_scalar_str(data.get("kind"), f"{rel_path}: kind"),
        entity=entity,
        sources=sources,
        depends_on_pages=_str_list(data.get("depends_on_pages"), f"{rel_path}: depends_on_pages"),
        aliases=_str_list(data.get("aliases"), f"{rel_path}: aliases"),
        provenance=_scalar_str(data.get("provenance"), f"{rel_path}: provenance"),
        adopted_at=_scalar_str(data.get("adopted_at"), f"{rel_path}: adopted_at"),
        purpose=_scalar_str(data.get("purpose"), f"{rel_path}: purpose"),
    )


def parse_topic_page(layout: DocsLayout, rel_path: str) -> TopicPage:
    """Parse one curated page's frontmatter; raises CurateError (no/invalid frontmatter, bad ``sources:``
    shape)."""
    return _parse_topic_text(rel_path, _read_page_text(layout, rel_path))


def normalise_source_path(page_rel: str, source_rel_to_page: str) -> str:
    """Resolve a page-relative ``sources:`` path to docs-repo-relative; CurateError if it escapes docs/."""
    return _normalise(page_rel, source_rel_to_page, posixpath.dirname(page_rel))


def resolve_source_path(page_rel: str, source: str) -> str:
    """Resolve one ``sources:`` entry to docs-repo-relative: an entry starting ``mirror/`` or ``archive/``
    resolves from the docs root (the path an agent copies from DEPENDS.tsv or a curate work list), anything
    else page-relative as before.  CurateError if it escapes docs/.  ``depends_on_pages`` stays page-relative
    (``normalise_source_path``)."""
    root_relative = source.strip().startswith(_DOCS_ROOT_SOURCE_PREFIXES)
    return _normalise(page_rel, source, "" if root_relative else posixpath.dirname(page_rel))


def _normalise(page_rel: str, source: str, base: str) -> str:
    src = source.strip()
    if not src:
        raise CurateError(f"{page_rel}: empty source path")
    if src.startswith("/"):
        raise CurateError(f"{page_rel}: source path {src!r} is absolute; write it relative to the page")
    joined = posixpath.normpath(posixpath.join(base, src))
    if joined in ("", ".", "..") or joined.startswith(("../", "/")):
        raise CurateError(f"{page_rel}: source path {src!r} escapes docs/ (resolves to {joined!r})")
    return unicodedata.normalize("NFC", joined)


def _is_hand_written(layout: DocsLayout, rel_path: str) -> bool:
    """True for an adopted page; an unparseable page falls back to the design's ``grep`` form."""
    try:
        return parse_topic_page(layout, rel_path).provenance == HAND_WRITTEN
    except CurateError:
        try:
            raw = (layout.root / rel_path).read_bytes()
        except OSError:
            return False
        return any(line.startswith(b"provenance: hand-written") for line in raw.split(b"\n"))


def _source_missing(root: Path, source: str) -> bool:
    """SOURCE-MISSING: ``source`` is not a regular file, or its mirror head has no ``rendered_sha256:`` (a
    guide file, a sidecar, broken frontmatter): exactly the rows the refresh queue calls
    MISSING-OR-UNPARSEABLE.  A deleted, unreadable or refused page is SOURCE-DELETED / SOURCE-UNREADABLE
    instead, never this."""
    try:
        if not (root / source).is_file():
            return True
    except (OSError, ValueError):
        return True
    _cur, status, ok = _read_mirror_head(root, os.fsencode(source))
    return not ok and status not in (b"deleted", b"unreadable", b"refused")


def _finding(code: str, path: str, message: str) -> LintFinding:
    """Every curation finding is non-blocking for the sync (see the module docstring)."""
    return LintFinding(code=code, path=path, message=message, blocking=False)


def _page_findings_and_rows(
    layout: DocsLayout, page: TopicPage
) -> tuple[list[DependsRow], list[LintFinding]]:
    """DEPENDS rows and findings of one parsed, non-adopted page."""
    rel = page.path
    rows: list[DependsRow] = []
    findings: list[LintFinding] = []
    seen: dict[str, int] = {}
    for i, src in enumerate(page.sources):
        try:
            source = resolve_source_path(rel, src.path)
        except CurateError as exc:
            findings.append(_finding("CURATE-PARSE", rel, f"sources[{i}]: {exc}"))
            continue
        if source in seen:
            findings.append(
                _finding("DUPLICATE-SOURCE", rel, f"sources[{i}] repeats sources[{seen[source]}] ({source})")
            )
            continue
        seen[source] = i
        if (layout.root / source).is_dir():
            # Never emitted: macOS awk dies reading a directory and the shell queue then drops every later
            # row (rc 0 on a broken queue).  Cite the pages inside it (e.g. ``….xlsx.d/01-q3.md``) instead.
            findings.append(
                _finding("SOURCE-IS-DIRECTORY", rel, f"sources[{i}] ({source}) is a directory, not a page")
            )
            continue
        if src.role not in ROLES:
            findings.append(
                _finding("BAD-ROLE", rel, f"sources[{i}] ({source}): role {src.role!r} not in {list(ROLES)}")
            )
        if not src.at_rendered_sha256:
            findings.append(_finding("UNPINNED", rel, f"sources[{i}] ({source}): no at_rendered_sha256"))
        elif not _HEX64.fullmatch(src.at_rendered_sha256):
            findings.append(
                _finding(
                    "BAD-PIN",
                    rel,
                    f"sources[{i}] ({source}): at_rendered_sha256 must be 64 lowercase hex chars "
                    f"(len={len(src.at_rendered_sha256)})",
                )
            )
        if not source.startswith(_DOCS_ROOT_SOURCE_PREFIXES):
            findings.append(
                _finding(
                    "SOURCE-NOT-MIRROR", rel, f"sources[{i}] ({source}) is not a mirror/ or archive/ page"
                )
            )
        if _source_missing(layout.root, source):
            findings.append(
                _finding(
                    "SOURCE-MISSING",
                    rel,
                    f"sources[{i}] ({source}) is not a mirror or archive page with a rendered_sha256: "
                    "fix the path (mirror/… and archive/… resolve from the docs root, anything else from "
                    "the page)",
                )
            )
        rows.append(DependsRow(page=rel, source=source, pinned_sha=src.at_rendered_sha256, role=src.role))
    for i, dep in enumerate(page.depends_on_pages):
        try:
            target = normalise_source_path(rel, dep)
        except CurateError as exc:
            findings.append(_finding("BROKEN-PAGE-EDGE", rel, f"depends_on_pages[{i}]: {exc}"))
            continue
        if not (layout.root / target).is_file():
            findings.append(
                _finding("BROKEN-PAGE-EDGE", rel, f"depends_on_pages[{i}]: {target} does not exist")
            )
    return rows, findings


def _budget_findings(layout: DocsLayout, rel: str) -> list[LintFinding]:
    """TOPIC-BUDGET when a page outgrows the design's read-whole budget (non-blocking, like every finding)."""
    try:
        raw = (layout.root / rel).read_bytes()
    except OSError:
        return []
    lines = raw.count(b"\n") + (0 if raw.endswith(b"\n") or not raw else 1)
    if lines <= _PAGE_MAX_LINES and len(raw) <= _PAGE_MAX_BYTES:
        return []
    return [
        _finding(
            "TOPIC-BUDGET",
            rel,
            f"{lines} lines / {len(raw)} bytes exceeds {_PAGE_MAX_LINES} lines / {_PAGE_MAX_BYTES} bytes: "
            "split it into one page per subject",
        )
    ]


# ---------------------------------------------------------------------------------------------------------
# meeting citation lint (meeting-video spec 7.4)
# ---------------------------------------------------------------------------------------------------------

CITE_CODES: tuple[str, ...] = tuple(
    f"CITE-{c}" for c in ("UNRESOLVED", "QUOTE", "MISSING", "FRAME", "INFERRED", "BASIS", "SHARED")
)
"""The meeting citation lint's codes in rule order: warnings only, never in ``checkpoint_blockers``."""

_HMS = r"(\d\d):(\d\d):(\d\d)"
_CITE_TAG = re.compile(rf"(seen\+frame|seen|heard)(?: r(\d+))? {_HMS}")
_FREE_TAG = re.compile(r"chat ~\d\d:\d\d|file|recap")
_CELL_TOKEN = re.compile(r'"([^"]*)"|`([^`\n]*)`')  # a quote first, so a backtick inside a quote is text
_CELL_SPLIT = re.compile(r'(?<!\\)\|(?=(?:[^"]*"[^"]*")*[^"]*$)')  # a | inside a quote is text
_TABLE_SEPARATOR = re.compile(r":?-+:?")
_BULLET = re.compile(r"\s*(?:[-*+]|\d+\.)\s")
_EVIDENCE_LINE = re.compile(rf"\[{_HMS}\] (SAID[^:\n]*|SCREEN|SCREEN\+|SCREEN-|TILE|SPEAKING|KEYFRAME): (.*)")
_STATE_HEADING = re.compile(rf"## {_HMS}-{_HMS} · s(\d{{3}})")
_KEYFRAME_LINE = re.compile(rf"\[{_HMS}\] KEYFRAME: (t\d{{6}})")
_CONTINUATION = re.compile(
    r"NOTE: s(\d{3}) began at .*; its on-screen lines and keyframe are in window (\d+)"
)
_FRAME_NAME = re.compile(r"\bt\d{6}\b")
_SHARED_BASIS = re.compile(r"voice \d+, on shared audio of (.+?)(?:, \d+ voices)?", re.IGNORECASE)
_BASIS_FORMS = re.compile(  # fullmatch on the whole Basis cell, whitespace runs collapsed
    r"VOICE line|service transcript tag, one-person check passed|voice \d+, on shared audio of .+"
    r"|mixed|voice \d+, unidentified",
    re.IGNORECASE,
)
_ABBREVIATIONS = frozenset({"vs.", "e.g.", "i.e.", "etc.", "approx.", "no."})
_UNIT_KINDS = ("window", "index")
_WINDOW_SECONDS = 300
_HINT_SECONDS = 10
_QUOTED_SECTIONS = ("decisions", "action items", "numbers shown")
_DECISION_REF = re.compile(r"\bD\d+\b")


@dataclass(frozen=True, slots=True)
class _Evidence:
    """One timed line of a window unit or transcript page: ``heard`` (SAID) or ``seen`` (on-screen)."""

    seconds: int
    channel: str
    text: str


@dataclass(slots=True)
class _Section:
    """One ``## `` section of a meeting page: its table's header and data rows, and every other line."""

    title: str
    header: list[str]
    rows: list[list[str]]
    prose: list[str]


def _secs(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def _hms(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _squash(text: str) -> str:
    return " ".join(text.split())


def _fold(text: str) -> str:
    return _squash(text.strip(" \"'`*.")).casefold()


def _clip(text: str, n: int = 60) -> str:
    text = _squash(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _evidence(lines: Sequence[str]) -> list[_Evidence]:
    out: list[_Evidence] = []
    for line in lines:
        if m := _EVIDENCE_LINE.match(line):
            channel = "heard" if m[4].startswith("SAID") else "seen"
            out.append(_Evidence(_secs(m[1], m[2], m[3]), channel, m[5]))
    return out


def _pieces(quote: str) -> list[str]:
    """A quote's ``...`` pieces, whitespace runs as one space and a trailing ``[?]`` dropped; none for a
    blank quote (``""``, ``"..."``), which is no quote at all."""
    text = _squash(quote.replace("\\|", "|").replace("…", "...")).removesuffix("[?]")
    return [p.strip() for p in text.split("...") if p.strip()]


def _quote_in(quote: str, text: str) -> bool:
    """Rule 4: every piece of ``quote`` occurs in ``text`` in order; a trailing `` [?]`` (an unsure OCR
    line) is ignored on both."""
    hay, pos = _squash(text).removesuffix(" [?]"), 0
    for piece in _pieces(quote):
        found = hay.find(piece, pos)
        if found < 0:
            return False
        pos = found + len(piece)
    return True


def _tagged(unit: str) -> list[tuple[str, list[str]]]:
    """Each backticked tag of a cell, bullet or line, with every quote after it before the next tag."""
    out: list[tuple[str, list[str]]] = []
    for m in _CELL_TOKEN.finditer(unit):
        if m[2] is not None:
            out.append((m[2].strip(), []))
        elif out:
            out[-1][1].append(m[1])
    return out


def _quoted(tagged: Sequence[tuple[str, list[str]]], tag: Callable[[str], object]) -> bool:
    return any(tag(t) and any(_pieces(q) for q in quotes) for t, quotes in tagged)


def _joined(prose: Sequence[str]) -> list[str]:
    """Prose lines with each wrapped continuation (no new bullet marker, no blank line between) joined to
    the bullet or paragraph line it continues."""
    out: list[str] = []
    for prev, line in zip(["", *prose], prose, strict=False):
        if out and prev.strip() and line.strip() and not _BULLET.match(line) and not line.startswith("#"):
            out[-1] += " " + line.strip()
        elif line.strip():
            out.append(line)
    return out


def _sentences(line: str) -> list[str]:
    """Rule 7's scope outside tables: each sentence, a bullet's too.  A full stop inside a quote or a tag,
    of a list number, or after a short abbreviation (``vs.``, ``e.g.``) ends none."""
    masked = _CELL_TOKEN.sub(lambda m: "x" * len(m[0]), line)
    start = bullet.end() if (bullet := _BULLET.match(line)) else 0
    cuts = [
        m.end()
        for m in re.finditer(r"[.!?](?=\s)", masked)
        if m.start() >= start
        and masked[: m.end()].split()[-1].lstrip("(\"'").casefold() not in _ABBREVIATIONS
    ]
    bounds = [0, *cuts, len(line)]
    return [line[a:b] for a, b in itertools.pairwise(bounds) if line[a:b].strip()]


def _sections(body: str) -> list[_Section]:
    """Split a page body at its ``## `` headings; a table's rows before its separator row are the header."""
    out = [_Section("", [], [], [])]
    in_rows = False
    for line in body.splitlines():
        row = line.strip()
        if line.startswith("## "):
            out.append(_Section(line[3:].strip(), [], [], []))
            in_rows = False
        elif row.startswith("|"):
            inner = row[1:-1] if row.endswith("|") and len(row) > 1 else row[1:]
            cells = [c.strip() for c in _CELL_SPLIT.split(inner)]
            if all(_TABLE_SEPARATOR.fullmatch(c) for c in cells):
                in_rows = True
            elif in_rows:
                out[-1].rows.append(cells)
            else:
                out[-1].header = cells
        else:
            out[-1].prose.append(line)
            in_rows = False
    return out


def _states(lines: Sequence[str]) -> list[tuple[str, int, int, list[str]]]:
    """A window unit's ``## HH:MM:SS-HH:MM:SS · sNNN`` states as (id, start, end, lines)."""
    out: list[tuple[str, int, int, list[str]]] = []
    for line in lines:
        if m := _STATE_HEADING.match(line):
            out.append((m[7], _secs(m[1], m[2], m[3]), _secs(m[4], m[5], m[6]), []))
        elif out:
            out[-1][3].append(line)
    return out


def _keyframes(lines: Sequence[str], at: int) -> list[str]:
    """A state's keyframe names (``tHHMMSS``; a revisit's ``tHHMMSS.jpg of sNNN in window N`` too) taken at
    or before ``at``, in order."""
    return [m[4] for line in lines if (m := _KEYFRAME_LINE.match(line)) and _secs(m[1], m[2], m[3]) <= at]


def _column(header: Sequence[str], name: str, default: int) -> int:
    return next((i for i, cell in enumerate(header) if _fold(cell) == name), default)


def _meeting_sources(layout: DocsLayout, page: TopicPage) -> tuple[list[dict[int, list[str]]], list[str]]:
    """The page's recordings in order of first appearance in ``sources:``, each a window index -> body lines
    map (a ``.d`` folder whose units carry ``part: {kind: window|index}``), and the lines of every other
    readable source: a transcript page's SAID lines resolve ``heard`` tags too.  The window index is read
    from ``part`` (``publish`` may suffix a stem), never from the file name."""
    folders: dict[str, dict[int, list[str]]] = {}
    other: list[str] = []
    for src in page.sources:
        try:
            rel = resolve_source_path(page.path, src.path)
            data, body = parse_frontmatter((layout.root / rel).read_text(encoding="utf-8"))
        except (CurateError, OSError, UnicodeDecodeError, ValueError, FrontmatterError):
            continue  # it resolves no tag: CITE-UNRESOLVED names the tags that needed it
        part, folder = data.get("part"), posixpath.dirname(rel)
        if folder.endswith(".d") and isinstance(part, dict) and part.get("kind") in _UNIT_KINDS:
            units = folders.setdefault(folder, {})
            if part["kind"] == "window" and isinstance(part.get("index"), int):
                units[part["index"]] = body.splitlines()
        else:
            other.extend(body.splitlines())
    return list(folders.values()), other


class _MeetingLint:
    """The 7.4 rules over one ``kind: meeting`` page and the units its ``sources:`` lists."""

    def __init__(self, layout: DocsLayout, page: TopicPage, body: str) -> None:
        self.rel = page.path
        self.recordings, other = _meeting_sources(layout, page)
        self.transcript = [e for e in _evidence(other) if e.channel == "heard"]
        self.sections = _sections(body)
        log = [s for s in self.sections if _fold(s.title) == "verification log"]
        self.opened = {n for s in log for line in s.prose for n in _FRAME_NAME.findall(line)}
        decided = [s for s in self.sections if _fold(s.title) == "decisions"]
        self.decisions = {
            r[at] for s in decided for r in s.rows if (at := _column(s.header, "#", 0)) < len(r)
        }
        self.findings: list[LintFinding] = []

    def _add(self, code: str, message: str) -> None:
        self.findings.append(_finding(code, self.rel, message))

    def _window(self, recording: int, index: int) -> list[str] | None:
        if not 1 <= recording <= len(self.recordings):
            return None
        return self.recordings[recording - 1].get(index)

    def _hint(self, r: int, t: int, channel: str, quote: str) -> str:
        """Where a quote is said or shown within 10 s of ``t`` instead (a neighbour window too)."""
        units = self.recordings[r - 1].values() if r <= len(self.recordings) else []
        pool = self.transcript if channel == "heard" else []
        near = sorted(
            (abs(e.seconds - t), e.seconds)
            for e in [*(e for unit in units for e in _evidence(unit)), *pool]
            if e.channel == channel and 0 < abs(e.seconds - t) <= _HINT_SECONDS and _quote_in(quote, e.text)
        )
        return f"; the quote is at {_hms(near[0][1])}, {near[0][0]} s away" if near else ""

    def _cite(self, tag: str, quotes: Sequence[str]) -> None:
        """Rules 2-4 for one ``seen``/``seen+frame``/``heard`` tag (``r1`` when it names no recording) and
        every quote that follows it."""
        m = _CITE_TAG.fullmatch(tag)
        assert m is not None
        r, t = int(m[2] or 1), _secs(m[3], m[4], m[5])
        channel, n = ("heard" if m[1] == "heard" else "seen"), t // _WINDOW_SECONDS + 1
        lines = self._window(r, n)
        pool = self.transcript if channel == "heard" else []
        here = [e for e in [*_evidence(lines or []), *pool] if e.channel == channel and e.seconds == t]
        if not here and lines is None and not pool:
            where = f"window {n} of r{r}" if r <= len(self.recordings) else f"recording r{r}"
            self._add("CITE-UNRESOLVED", f"`{tag}`: {where} is not a readable unit in sources:")
        elif not here:
            kind = "SAID" if channel == "heard" else "on-screen"
            hint = next((h for q in quotes if _pieces(q) and (h := self._hint(r, t, channel, q))), "")
            self._add("CITE-UNRESOLVED", f"`{tag}`: no {kind} line at {_hms(t)} in window {n} of r{r}{hint}")
        for quote in quotes if here else ():
            if not _pieces(quote):
                self._add("CITE-QUOTE", f'`{tag}`: "{quote}" is not a quote: quote the words')
            elif not any(_quote_in(quote, e.text) for e in here):
                hint = self._hint(r, t, channel, quote)
                self._add(
                    "CITE-QUOTE", f'`{tag}`: "{_clip(quote)}" is in no {channel} line at {_hms(t)}{hint}'
                )

    def _frame(self, tag: str) -> str:
        """Rule 6 for one ``seen+frame`` tag: "" when its state's keyframe is in the Verification log."""
        m = _CITE_TAG.fullmatch(tag)
        assert m is not None
        r, t = int(m[2] or 1), _secs(m[3], m[4], m[5])
        lines = self._window(r, t // _WINDOW_SECONDS + 1)
        if lines is None:
            return ""  # CITE-UNRESOLVED already names the tag
        states = _states(lines)
        state = next((s for s in reversed(states) if s[1] <= t <= s[2]), None)  # at a boundary, the later
        if state is None:
            return f"no state of its window holds {_hms(t)}"
        frames: list[str] = []  # the state's frames up to t, from the window it began in first
        if cont := next((c for line in state[3] if (c := _CONTINUATION.search(line))), None):
            earlier = _states(self._window(r, int(cont[2])) or [])
            frames = [f for s in earlier if s[0] == cont[1] for f in _keyframes(s[3], t)]
        frames += _keyframes(state[3], t)
        if not frames:
            return f"state s{state[0]} has no KEYFRAME at or before {_hms(t)}"
        name = frames[-1]
        return "" if name in self.opened else f"keyframe {name} of s{state[0]} is not in the Verification log"

    def run(self) -> list[LintFinding]:
        for sec in self.sections:
            cells, lines = [c for row in sec.rows for c in row], _joined(sec.prose)
            for unit in [*cells, *lines]:
                for tag, quotes in _tagged(unit):
                    if _CITE_TAG.fullmatch(tag):
                        self._cite(tag, quotes)
            for unit in [*cells, *(s for line in lines for s in _sentences(line))]:
                tags = [t for t, _q in _tagged(unit)]
                if "inferred" in tags and not any(
                    _CITE_TAG.fullmatch(t) or _FREE_TAG.fullmatch(t) for t in tags
                ):  # the action-item rubric's "`inferred` from D<n>" rests on a Decisions row of this page
                    refs = set(_DECISION_REF.findall(unit))
                    if not refs or not refs <= self.decisions:
                        missing = ", ".join(sorted(refs - self.decisions)) or "no Decisions row"
                        self._add("CITE-INFERRED", f"`inferred` with no other tag ({missing}): {_clip(unit)}")
            title = _fold(sec.title)
            for i, row in enumerate(sec.rows, 1):
                where = f"{sec.title} row {i} ({_clip(row[0], 30)})"
                if title in _QUOTED_SECTIONS:
                    self._row(where, row, title)
            if title == "people":
                self._people(sec)
        return self.findings

    def _row(self, where: str, row: list[str], title: str) -> None:
        """Rules 5 and 6 for one Decisions, Action items or Numbers shown row.  An action item whose evidence
        is `inferred` from a decision (``D<n>``) needs no quote: rule 7 checks that the row exists."""
        tagged = [p for cell in row for p in _tagged(cell)]
        quoted = _quoted(tagged, lambda t: _CITE_TAG.fullmatch(t) or t == "file" or t.startswith("chat ~"))
        from_decision = title == "action items" and any(
            [t for t, _q in _tagged(cell)] == ["inferred"] and _DECISION_REF.search(cell) for cell in row
        )
        if not quoted and not from_decision:
            self._add("CITE-MISSING", f"{where}: no evidence tag with a quote")
        if title != "numbers shown":
            return
        frames = [t for t, _q in tagged if (m := _CITE_TAG.fullmatch(t)) and m[1] == "seen+frame"]
        if not frames and "picture not kept" not in " ".join(row).casefold():
            self._add("CITE-FRAME", f"{where}: no `seen+frame` tag and the row does not say picture not kept")
        for tag in frames:
            if problem := self._frame(tag):
                self._add("CITE-FRAME", f"{where}: `{tag}`: {problem}")

    def _people(self, sec: _Section) -> None:
        """Rule 8 (C11): each Basis cell is one of the forms of spec section 5 rule 4; a person named beside
        the label of the shared audio it is based on is CITE-SHARED."""
        person_at = _column(sec.header, "person", 0)
        basis_at = _column(sec.header, "basis", 2)
        for i, row in enumerate(sec.rows, 1):
            person = row[person_at] if person_at < len(row) else ""
            basis = row[basis_at] if basis_at < len(row) else ""
            plain = _squash(basis.replace("`", ""))
            tagged = _tagged(basis)  # a heard tag with its quote first, any words after it
            heard = basis.startswith("`heard") and _quoted(tagged[:1], lambda t: t.startswith("heard "))
            where = f"People row {i} ({_clip(person, 30)})"
            if not heard and not _BASIS_FORMS.fullmatch(plain):
                self._add("CITE-BASIS", f"{where}: basis {_clip(basis)!r} is none of the C11 forms")
            if (shared := _SHARED_BASIS.fullmatch(plain)) and _fold(person) == _fold(shared[1]):
                self._add(
                    "CITE-SHARED", f"{where}: a person's name beside someone else's words (shared audio)"
                )


def lint_meeting_citations(layout: DocsLayout) -> list[LintFinding]:
    """CITE-* warnings for every ``kind: meeting`` page (meeting-video spec 7.4): each evidence tag resolves
    to a line of its window unit and its quote occurs there, Decisions / Action items / Numbers shown rows
    carry a quoted tag, a figure's keyframe was opened, an inference names its tags, a People basis is a C11
    form.  A
    function of its own, never part of ``generate_depends``: ``checkpoint_blockers`` would make every finding
    hold the checkpoint, and how often the lint fails a correct citation is not measured yet (R20).
    ``agentsync curate`` prints them as ``warn`` lines.  It reads the page and its mirror units, no image."""
    findings: list[LintFinding] = []
    for rel in iter_topic_pages(layout):
        try:
            text = _read_page_text(layout, rel)
            page = _parse_topic_text(rel, text)
            _fm, body = split_frontmatter(text)
        except (CurateError, FrontmatterError):
            continue  # generate_depends reports CURATE-PARSE
        if page.kind == "meeting":  # a curly “quote” is a quote
            findings.extend(_MeetingLint(layout, page, body.replace("“", '"').replace("”", '"')).run())
    return sorted(set(findings), key=lambda f: (f.path, f.code, f.message))


def generate_depends(layout: DocsLayout) -> tuple[list[DependsRow], list[tuple[str, str]], list[LintFinding]]:
    """Parse every curated page -> (DEPENDS rows sorted by (page, source), (entity, page) rows sorted, lint
    findings: CURATE-PARSE, MISSING-ENTITY, MISSING-PURPOSE, TOPIC-BUDGET, BAD-ROLE, UNPINNED/BAD-PIN rows are
    still emitted for the queue)."""
    rows: list[DependsRow] = []
    entities: set[tuple[str, str]] = set()
    findings: list[LintFinding] = []
    for rel in iter_topic_pages(layout):
        try:
            page = parse_topic_page(layout, rel)
        except CurateError as exc:
            findings.append(_finding("CURATE-PARSE", rel, str(exc)))
            continue
        hand_written = page.provenance == HAND_WRITTEN
        if page.entity is not None:
            entities.add((page.entity, rel))
        elif not hand_written:
            findings.append(_finding("MISSING-ENTITY", rel, "curated page has no 'entity:'"))
        if page.purpose is None and not hand_written:
            findings.append(
                _finding(
                    "MISSING-PURPOSE", rel, "curated page has no 'purpose:' (what it answers and what not)"
                )
            )
        findings.extend(_budget_findings(layout, rel))
        if hand_written:
            if page.sources:
                findings.append(
                    _finding(
                        "HAND-WRITTEN-WITH-SOURCES",
                        rel,
                        "provenance: hand-written pages are exempt and their sources: are ignored; "
                        "drop 'provenance' to put the page in the refresh queue",
                    )
                )
            continue
        page_rows, page_findings = _page_findings_and_rows(layout, page)
        rows.extend(page_rows)
        findings.extend(page_findings)
    rows.sort(key=lambda r: (r.page, r.source))
    findings.sort(key=lambda f: (f.path, f.code, f.message))
    return rows, sorted(entities), findings


# ---------------------------------------------------------------------------------------------------------
# generated files
# ---------------------------------------------------------------------------------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` via a same-directory temp file (owner bits kept, 0600 when new: the docs
    repo holds tenant data and is owner-only)."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode) & 0o700
    except FileNotFoundError:
        mode = 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Write ``data`` unless the file already holds exactly it; return True if written."""
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    _atomic_write(path, data)
    return True


def _render_tsv(header: str, rows: Sequence[Sequence[str]], name: str) -> bytes:
    """Header + rows, tab-separated, LF, trailing newline; CurateError on an unrepresentable cell."""
    lines = [header]
    for row in rows:
        for cell in row:
            _tsv_safe(cell, f"{name} row {list(row)!r}")
        lines.append("\t".join(row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_depends(layout: DocsLayout, rows: Sequence[DependsRow]) -> bool:
    """Write DEPENDS.tsv (header + rows, LF, trailing newline) if different; return True if written."""
    for r in rows:
        if not r.page or not r.source:
            raise CurateError(f"DEPENDS.tsv row with an empty page or source: {r!r}")
    ordered = sorted(rows, key=lambda r: (r.page, r.source, r.pinned_sha, r.role))
    cells = [(r.page, r.source, r.pinned_sha, r.role) for r in ordered]
    return _write_if_changed(layout.depends_tsv, _render_tsv(DEPENDS_HEADER, cells, "DEPENDS.tsv"))


def write_by_entity(layout: DocsLayout, rows: Sequence[tuple[str, str]]) -> bool:
    """Write ``_index/by-entity.tsv`` if different; return True if written."""
    ordered = sorted(set(rows))
    return _write_if_changed(layout.by_entity_tsv, _render_tsv(BY_ENTITY_HEADER, ordered, "by-entity.tsv"))


# ---------------------------------------------------------------------------------------------------------
# the refresh queue (the design 4.5 awk script, ported; the script itself was retired in KISS K09b)
# ---------------------------------------------------------------------------------------------------------


def _depends_usable(tsv: Path) -> bool:
    """The script's two rc-2 guards: non-empty file whose first field of line 1 is ``page``."""
    try:
        with tsv.open("rb") as fh:
            first = fh.readline()
    except OSError:
        return False
    if not first:
        return False
    return first.rstrip(b"\n").split(b"\t", 1)[0] == b"page"


def _read_mirror_head(root: Path, src: bytes) -> tuple[bytes, bytes, bool]:
    """The script's getline loop: (rendered_sha256 value, status value, found) up to the second ``---``."""
    cur, status, ok, dash = b"", b"", False, 0
    if src == b"-":  # awk would read its own stdin here; that is never a mirror page
        return cur, status, ok
    try:
        with (root / os.fsdecode(src)).open("rb") as fh:
            for raw in fh:
                line = raw[:-1] if raw.endswith(b"\n") else raw
                if line == b"---":
                    dash += 1
                    if dash == 2:
                        break
                    continue
                if line.startswith(b"rendered_sha256: "):
                    cur, ok = line[17:], True
                elif line.startswith(b"status: "):
                    status = line[8:]
    except (OSError, ValueError):
        pass  # getline < missing/unreadable file returns -1: whatever was read so far stands
    return cur, status, ok


def _decode(b: bytes) -> str:
    """Bytes of the TSV to str, losslessly (``surrogateescape``) so lines re-encode byte-identical."""
    return b.decode("utf-8", "surrogateescape")


def _queue_row(root: Path, record: bytes) -> RefreshVerdict | None:
    """Evaluate one DEPENDS.tsv data record exactly as the awk body does (None = fresh)."""
    fields = record.split(b"\t") if record else []  # awk: an empty record has NF == 0
    page = _decode(fields[0]) if fields else ""
    if len(fields) < 3:
        return RefreshVerdict("MALFORMED", page, "", f"(fields={len(fields)})")
    src_b, pin = fields[1], fields[2]
    src = _decode(src_b)
    if pin == b"":
        return RefreshVerdict("UNPINNED", page, src)
    if not _HEX64_BYTES.fullmatch(pin):
        return RefreshVerdict("BAD-PIN", page, src, f"(len={len(pin)})")  # bytes, like awk under LC_ALL=C
    if src_b == b"" or (root / os.fsdecode(src_b)).is_dir():
        # Deliberate divergence: macOS awk dies on ``getline < ""`` and on reading a directory, and the script
        # then drops every later row while still exiting 0/1.  Both are generator/path bugs: this verdict.
        # ``generate_depends`` never emits either row, so the two agree on every generated DEPENDS.tsv.
        return RefreshVerdict("MISSING-OR-UNPARSEABLE", page, src)
    cur, status, ok = _read_mirror_head(root, src_b)
    if status == b"deleted":
        return RefreshVerdict("SOURCE-DELETED", page, src)
    if status in (b"unreadable", b"refused"):
        return RefreshVerdict("SOURCE-UNREADABLE", page, src)
    if not ok:
        return RefreshVerdict("MISSING-OR-UNPARSEABLE", page, src)
    if cur != pin:
        return RefreshVerdict("STALE", page, src)
    return None


def _refresh_queue_file(tsv: Path, root: Path) -> tuple[int, list[RefreshVerdict]]:
    """Run the queue over ``tsv`` with source paths resolved from ``root`` (the script's cwd)."""
    try:
        data = tsv.read_bytes()
    except OSError:
        data = b""
    if not data:
        _log.warning("%s: missing or empty", tsv)
        return 2, []
    if not _depends_usable(tsv):
        _log.warning("%s: missing header (page/source/pinned_sha/role)", tsv)
        return 2, []
    records = data.split(b"\n")
    if records[-1] == b"":
        records.pop()  # a trailing newline ends the last record; it does not start a new one
    out: dict[bytes, RefreshVerdict] = {}
    for record in records[1:]:
        verdict = _queue_row(root, record)
        if verdict is not None:
            out.setdefault(verdict.line().encode("utf-8", "surrogateescape"), verdict)
    verdicts = [out[k] for k in sorted(out)]  # sort -u, C-locale byte order
    return (1 if verdicts else 0), verdicts


def refresh_queue(layout: DocsLayout) -> tuple[int, list[RefreshVerdict]]:
    """The design 4.5 refresh queue: (rc, verdicts); rc 0 = fresh, 1 = rows need action, 2 = unusable."""
    return _refresh_queue_file(layout.depends_tsv, layout.root)


# ---------------------------------------------------------------------------------------------------------
# banners
# ---------------------------------------------------------------------------------------------------------


def _split_banners(body: str) -> _Banners:
    """Parse the leading banner lines (plus one blank separator line) off a page body."""
    pos = 0
    stale: str | None = None
    retired: list[str] = []
    found = False
    while pos < len(body):
        nl = body.find("\n", pos)
        end = len(body) if nl == -1 else nl
        line = body[pos:end]
        if line.startswith(_STALE_PREFIX):
            stale = stale if stale is not None else line
        elif line.startswith(_RETIRED_PREFIX):
            retired.append(line)
        else:
            break
        found = True
        pos = end if nl == -1 else nl + 1
    if found and body.startswith("\n", pos):
        pos += 1
    return _Banners(stale=stale, retired=tuple(retired), rest=body[pos:])


def _join_banners(b: _Banners) -> str:
    """Inverse of ``_split_banners``: STALE first, then SOURCE RETIRED lines, one blank line, the body."""
    lines = ([b.stale] if b.stale is not None else []) + list(b.retired)
    return "\n".join(lines) + "\n\n" + b.rest if lines else b.rest


def _rewrite_banners(layout: DocsLayout, rel: str, change: Callable[[_Banners], _Banners]) -> bool:
    """Apply ``change`` to one page's banner block; never touches hand-written or unparseable pages."""
    path = layout.root / rel
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning("banner: skipping unreadable page %s: %s", rel, exc)
        return False
    bom = _BOM if text.startswith(_BOM) else ""
    text = text.removeprefix(_BOM)
    try:
        page = _parse_topic_text(rel, text)
        fm, body = split_frontmatter(text)
    except (CurateError, FrontmatterError) as exc:
        _log.warning("banner: skipping unparseable page %s: %s", rel, exc)
        return False
    if page.provenance == HAND_WRITTEN or fm is None:
        return False
    current = _split_banners(body)
    wanted = change(current)
    if wanted == current:
        return False
    head = text[: len(text) - len(body)]
    if not head.endswith("\n"):
        head += "\n"
    new_text = bom + head + _join_banners(wanted)
    if new_text.encode("utf-8") == raw:
        return False
    _atomic_write(path, new_text.encode("utf-8"))
    return True


def apply_stale_banners(layout: DocsLayout, verdicts: Sequence[RefreshVerdict], today: str) -> list[str]:
    """Deterministic linter: prepend STALE_BANNER (after the frontmatter) to pages with a STALE verdict,
    remove it from pages that are fresh again, never touch hand-written pages; return changed paths."""
    if not _DATE.fullmatch(today):
        raise ValueError(f"today must be YYYY-MM-DD, got {today!r}")
    if not _depends_usable(layout.depends_tsv):
        # rc 2 yields no verdicts; treating that as "everything fresh" would strip every banner.
        _log.warning("STALE banners left unchanged: %s is missing or unusable", layout.depends_tsv)
        return []
    stale_pages = {v.page for v in verdicts if v.verdict == "STALE"}
    banner = STALE_BANNER.format(date=today)
    changed: list[str] = []
    for rel in iter_topic_pages(layout):
        want = rel in stale_pages

        def change(b: _Banners, want: bool = want) -> _Banners:
            if want:
                return b if b.stale is not None else replace(b, stale=banner)  # keep the first date
            return replace(b, stale=None)

        if _rewrite_banners(layout, rel, change):
            changed.append(rel)
    return sorted(changed)


def _apply_retired_banners(
    layout: DocsLayout, rows: Sequence[DependsRow], retired: Mapping[str, str]
) -> list[str]:
    """Design 4.7: pages citing ``mirror/<source_id>/`` of a retired source get RETIRED_BANNER (one line per
    reason, sorted) instead of STALE; the lines go away when no retired source is cited.  Returns changed
    paths.  ``retired`` maps source_id -> retired_reason."""
    wanted: dict[str, set[str]] = {}
    for r in rows:
        for source_id, reason in retired.items():
            if r.source.startswith(f"mirror/{source_id}/"):
                clean = " ".join(reason.split()) or "retired"
                wanted.setdefault(r.page, set()).add(RETIRED_BANNER.format(reason=clean))
    changed: list[str] = []
    for rel in iter_topic_pages(layout):
        lines = tuple(sorted(wanted.get(rel, set())))

        def change(b: _Banners, lines: tuple[str, ...] = lines) -> _Banners:
            return b if tuple(sorted(b.retired)) == lines else replace(b, retired=lines)

        if _rewrite_banners(layout, rel, change):
            changed.append(rel)
    return sorted(changed)


def apply_retired_banners(
    layout: DocsLayout, rows: Sequence[DependsRow], retired: Mapping[str, str]
) -> list[str]:
    """Public entry for the retired-source banner (cycle step 8): see ``_apply_retired_banners``."""
    return _apply_retired_banners(layout, rows, retired)


_NOT_CURATABLE_STATUSES = frozenset({b"deleted", b"superseded", b"unreadable", b"refused"})


def uncovered_mirror_pages(layout: DocsLayout, rows: Sequence[DependsRow]) -> list[str]:
    """Docs-repo-relative paths of every current mirror page that no curated page cites in ``rows``: the
    curation backlog beside the refresh queue.  A page counts when its frontmatter carries
    ``rendered_sha256:`` and a ``status:`` other than deleted/superseded/unreadable/refused (sidecars and the
    guide files have no ``rendered_sha256:``).  Sorted, C-locale byte order."""
    root = layout.mirror
    if not root.is_dir():
        return []
    cited = {r.source for r in rows}
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in filenames:
            if not name.endswith(".md") or name in _EXCLUDED_PAGE_NAMES:
                continue
            rel = layout.rel(Path(dirpath) / name)
            if rel in cited:
                continue
            _sha, status, ok = _read_mirror_head(expand(layout.root), os.fsencode(rel))
            if ok and status not in _NOT_CURATABLE_STATUSES:
                out.append(rel)
    return sorted(out, key=lambda p: p.encode("utf-8", "surrogateescape"))


def source_entry(layout: DocsLayout, rel: str) -> str | None:
    """A ready-made ``sources:`` entry citing mirror page ``rel`` at its current ``rendered_sha256``, as one
    YAML flow mapping (``{path: mirror/…, at_rendered_sha256: <hex>, role: primary}``; the path is quoted when
    YAML would misread it plain); None when ``rel`` is not a curatable mirror page (no ``rendered_sha256:``,
    or a deleted, superseded, unreadable or refused one)."""
    if not rel.startswith(_DOCS_ROOT_SOURCE_PREFIXES[0]) or not rel.endswith(".md"):
        return None
    sha, status, ok = _read_mirror_head(expand(layout.root), os.fsencode(rel))
    if not ok or status in _NOT_CURATABLE_STATUSES or not _HEX64_BYTES.fullmatch(sha):
        return None
    path = rel if _PLAIN_YAML_PATH.fullmatch(rel) else json.dumps(rel, ensure_ascii=False)
    return f"{{path: {path}, at_rendered_sha256: {sha.decode('ascii')}, role: {ROLES[0]}}}"


def lint_unlisted_pages(layout: DocsLayout, rows: Sequence[DependsRow]) -> list[LintFinding]:
    """UNLISTED: every curated page that is not ``provenance: hand-written`` appears in DEPENDS.tsv
    (blocking=False for the sync; ``agentsync curate`` reports it as an error)."""
    listed = {r.page for r in rows}
    return [
        _finding(
            "UNLISTED",
            rel,
            "curated page is in no DEPENDS.tsv row, so it can never go STALE: add pinned sources:",
        )
        for rel in iter_topic_pages(layout)
        if rel not in listed and not _is_hand_written(layout, rel)
    ]


def _without_banners(raw: bytes) -> bytes:
    """A page's bytes minus its STALE / SOURCE RETIRED banner block, the only part a sync's banner rewrite
    touches; a page that is not UTF-8 or has no frontmatter comes back unchanged."""
    try:
        text = raw.decode("utf-8")
        fm, body = split_frontmatter(text.removeprefix(_BOM))
    except (UnicodeDecodeError, FrontmatterError):
        return raw
    if fm is None:
        return raw
    head = text[: len(text) - len(body)]
    head = head if head.endswith("\n") else head + "\n"
    return (head + _split_banners(body).rest).encode("utf-8")


def _banner_only_change(repo: Path, rev: str, rel: str) -> bool:
    """True when ``rel`` existed at ``rev`` and differs from it now only in its banner block: a sync marked
    an old page STALE or RETIRED and nobody edited it, so its verdicts do not hold this checkpoint."""
    before = gitops.file_at(repo, rev, rel)
    if before is None:
        return False
    try:
        now = (repo / rel).read_bytes()
    except OSError:
        return False
    return _without_banners(before) == _without_banners(now)


def _pages_changed_since_checkpoint(repo: Path, since: str | None) -> set[str] | None:
    """Topic pages that differ between ``since`` (default: the ``curated`` tag) and the working tree
    (untracked included), less those whose only change is a sync's banner rewrite (committed or not); None
    (every page counts) before the first checkpoint or when git cannot answer."""
    try:
        checkpoint = gitops.curated_checkpoint(repo) if gitops.head_sha(repo) else None
        if checkpoint is None:
            return None
        base = since if since is not None else checkpoint[0]
        changed = gitops.paths_changed_since(repo, base, ("topics",))
        return {rel for rel in changed if not _banner_only_change(repo, base, rel)}
    except GitError as exc:
        _log.warning("checkpoint: cannot tell which topic pages changed (%s); checking every page", exc)
        return None


def checkpoint_blockers(repo: Path, since: str | None = None) -> list[LintFinding]:
    """Everything that holds the ``curated`` checkpoint, each finding ``blocking=True``: every curation lint
    finding but TOPIC-BUDGET, UNLISTED, and, for topic pages changed since ``since`` (the sync passes its
    checkpoint base, the oldest commit not yet checked, and ``agentsync curate`` the same base through
    ``loop.checkpoint_findings``; the default is the ``curated`` tag; every page before the first tag; a
    change that is only a sync's banner rewrite never counts), SOURCE-MISSING and the CHECKPOINT_VERDICTS
    refresh verdicts.  The sync cannot use the ``curated`` tag: it sits on the commit BEFORE the last
    session's pages, so they would stay in scope, and one that later went stale would hold every later
    checkpoint.  SOURCE-MISSING is scoped like the verdicts because a source also vanishes with no fault of
    the page (a OneDrive rename or move, a tombstone reap, a purge); a typo'd path only happens on a page
    being written.  The verdicts come from the pages as they are now, not from DEPENDS.tsv.  The sync's land
    gate never uses this: its curation findings stay ``blocking=False``."""
    layout = DocsLayout(root=repo)
    rows, _entities, findings = generate_depends(layout)
    changed = _pages_changed_since_checkpoint(repo, since)

    def holds(f: LintFinding) -> bool:
        if f.code == "TOPIC-BUDGET":
            return False
        return f.code != "SOURCE-MISSING" or changed is None or f.path in changed

    out = [replace(f, blocking=True) for f in findings if holds(f)]
    out += [replace(f, blocking=True) for f in lint_unlisted_pages(layout, rows)]
    linted = {(f.code, f.path) for f in out}
    cells = [(r.page, r.source, r.pinned_sha, r.role) for r in rows]
    for v in {_queue_row(layout.root, "\t".join(c).encode("utf-8", "surrogateescape")) for c in cells}:
        if v is None or v.verdict not in CHECKPOINT_VERDICTS:
            continue
        if (_NAMED_BY_LINT.get(v.verdict, v.verdict), v.page) in linted:
            continue  # UNPINNED / BAD-PIN / MISSING-OR-UNPARSEABLE rows already have their lint finding
        if changed is not None and v.page not in changed:
            continue
        message = f"{v.source}: {_VERDICT_HINTS[v.verdict]}"
        out.append(LintFinding(code=v.verdict, path=v.page, message=message, blocking=True))
    return sorted(out, key=lambda f: (f.path, f.code, f.message))


# ---------------------------------------------------------------------------------------------------------
# adopting a hand-made docs tree (design section 10, week 0)
# ---------------------------------------------------------------------------------------------------------


def _walk_adoptable(src: Path) -> Iterator[tuple[Path, str]]:
    """Yield (file, POSIX path relative to ``src``) in sorted order; hidden entries and symlinks skipped."""
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        base = Path(dirpath)
        kept = []
        for d in sorted(dirnames):
            if d.startswith("."):
                continue
            if (base / d).is_symlink():
                _log.warning("adopt: skipping symlinked directory %s", base / d)
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            full = base / name
            if name.startswith((".", "~$")):
                continue
            if full.is_symlink():
                _log.warning("adopt: skipping symlink %s (docs/ allows no symlinks)", full)
                continue
            yield full, full.relative_to(src).as_posix()


def _adopted_rel(rel: str) -> str:
    """Topics-relative destination: NFC, and CLAUDE.md/INDEX.md renamed so curation and publish see them."""
    parts = unicodedata.normalize("NFC", rel).split("/")
    name = parts[-1]
    if name.casefold() in _RESERVED_ADOPT_NAMES:
        parts[-1] = name[: -len(".md")] + ".adopted.md"
    return "/".join(parts)


def _drop_top_level_keys(fm_text: str, keys: frozenset[str]) -> str:
    """Remove top-level ``key:`` entries (and their indented / block-sequence continuation lines)."""
    out: list[str] = []
    skipping = False
    for line in fm_text.splitlines(keepends=True):
        if skipping and (line[:1] in (" ", "\t", "-") or not line.strip()):
            continue
        skipping = False
        m = _TOP_LEVEL_KEY.match(line)
        if m and m.group(1) in keys:
            skipping = True
            continue
        out.append(line)
    return "".join(out)


def _stamp_page(docs_rel: str, raw: bytes, adopted_at: str) -> str:
    """Return the adopted page text: existing frontmatter kept, ``provenance/adopted_at/sources`` stamped."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CurateError(f"{docs_rel}: not UTF-8 ({exc.reason} at byte {exc.start})") from None
    text = text.removeprefix(_BOM).replace("\r\n", "\n")
    stamp = f"provenance: {HAND_WRITTEN}\nadopted_at: {adopted_at}\nsources: []\n"
    data: dict[str, Any] | None = None
    fm_text: str | None = None
    body = text
    try:
        fm_text, fm_body = split_frontmatter(text)
    except FrontmatterError:
        fm_text = None  # a leading thematic break with no closing fence: all body
    if fm_text is not None:
        try:
            loaded = yaml.safe_load(fm_text) if fm_text.strip() else {}
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            data, body = loaded, fm_body
        else:
            _log.warning("adopt: %s starts with '---' but no YAML mapping; keeping it all as body", docs_rel)
    if data is None:
        new_text = "---\n" + stamp + "---\n" + body
    else:
        if data.get("sources") not in (None, []):
            raise CurateError(
                f"{docs_rel}: already cites sources:, so it is a curated page, not a hand-made one; "
                "move it into topics/ by hand instead of adopting it"
            )
        if data.get("provenance") not in (None, HAND_WRITTEN):
            raise CurateError(
                f"{docs_rel}: has 'provenance: {data['provenance']}'; adopting would overwrite it "
                "(rename that key first)"
            )
        assert fm_text is not None
        kept = _drop_top_level_keys(fm_text, _ADOPT_STAMP_KEYS)
        if kept and not kept.endswith("\n"):
            kept += "\n"
        new_fm = kept + stamp
        expected = {k: v for k, v in data.items() if k not in _ADOPT_STAMP_KEYS}
        expected.update(yaml.safe_load(stamp))
        try:
            same = yaml.safe_load(new_fm) == expected
        except yaml.YAMLError:
            same = False
        if not same:  # textual surgery failed on an unusual layout: re-render (comments are lost)
            new_fm = yaml.safe_dump(expected, sort_keys=False, allow_unicode=True, width=10_000)
        new_text = "---\n" + new_fm + "---\n" + body
    page = _parse_topic_text(docs_rel, new_text)
    if page.provenance != HAND_WRITTEN or page.sources:
        raise CurateError(f"{docs_rel}: stamping did not produce an adopted page")  # pragma: no cover
    return new_text


def adopt_pages(src_dir: Path, layout: DocsLayout, adopted_at: str) -> list[str]:
    """Copy an existing hand-made docs tree into ``topics/`` stamping ``provenance: hand-written``,
    ``adopted_at``, ``sources: []``; refuses to overwrite; returns created docs-repo-relative paths."""
    if not _ADOPTED_AT.fullmatch(adopted_at):
        raise ValueError(f"adopted_at must be YYYY-MM-DD (or ISO-8601 UTC ...Z), got {adopted_at!r}")
    src = expand(src_dir)
    if not src.is_dir():
        raise CurateError(f"adopt: {src} is not a directory")
    src_real, root_real = src.resolve(), expand(layout.root).resolve()
    if src_real == root_real or src_real.is_relative_to(root_real) or root_real.is_relative_to(src_real):
        raise CurateError(
            f"adopt: {src} overlaps the docs repo {layout.root}; copy the hand-made tree elsewhere first"
        )
    problems: list[str] = []
    plan: list[tuple[str, Path, Path, str | None]] = []  # (docs_rel, source file, dest, stamped text)
    claimed: dict[str, str] = {}
    for full, rel in _walk_adoptable(src):
        suffix = Path(rel).suffix.lower()
        is_page = rel.endswith(".md")
        if not is_page and suffix not in _ADOPT_ASSET_SUFFIXES:
            _log.warning("adopt: skipping %s (not a markdown page or an image it may embed)", rel)
            continue
        if getattr(full.lstat(), "st_flags", 0) & _SF_DATALESS:
            problems.append(f"{rel}: dataless cloud placeholder (download it first; adopt never hydrates)")
            continue
        docs_rel = "topics/" + _adopted_rel(rel)
        if len(docs_rel) > slug.MAX_PATH_CHARS:
            problems.append(f"{rel}: {docs_rel} is longer than {slug.MAX_PATH_CHARS} characters")
            continue
        key = docs_rel.casefold()
        if key in claimed:
            problems.append(f"{rel}: collides with {claimed[key]} on a case-insensitive volume")
            continue
        claimed[key] = rel
        dest = layout.root / docs_rel
        if dest.exists() or dest.is_symlink():
            problems.append(f"{docs_rel}: already exists (adopt never overwrites)")
            continue
        text: str | None = None
        if is_page:
            try:
                text = _stamp_page(docs_rel, full.read_bytes(), adopted_at)
            except CurateError as exc:
                problems.append(str(exc))
                continue
            except OSError as exc:
                problems.append(f"{rel}: unreadable: {exc.strerror or exc}")
                continue
        plan.append((docs_rel, full, dest, text))
    if problems:
        shown = problems[:_MAX_REPORTED_PROBLEMS]
        more = len(problems) - len(shown)
        tail = f"\n  … and {more} more" if more else ""
        raise CurateError("adopt refused, nothing written:\n  " + "\n  ".join(shown) + tail)
    created: list[Path] = []
    try:
        for _docs_rel, full, dest, text in sorted(plan, key=lambda p: p[0]):
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as out:
                created.append(dest)
                if text is not None:
                    out.write(text.encode("utf-8"))
                else:
                    with full.open("rb") as inp:
                        shutil.copyfileobj(inp, out)
    except OSError as exc:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise CurateError(f"adopt failed and was rolled back: {exc}") from exc
    result = sorted(p[0] for p in plan)
    _log.info("adopt: %d file(s) from %s into topics/", len(result), src)
    return result
