"""L4 curation contract: topics/ ``sources:`` frontmatter, DEPENDS.tsv, refresh queue, STALE banners (owner:
curate).

Path convention (adapts design 4.5 to docs/ being its own repo): DEPENDS.tsv columns 1-2 are relative to the
DOCS REPO ROOT (``topics/…``, ``mirror/…``) and the refresh-queue script runs from the docs repo root.

Every finding this module emits is ``blocking=False``: the curated layer is agent-written, and a bad pin or a
missing ``entity:`` must never stop the mirror from syncing.  The refresh queue (rc 1) and ``agentsync lint``
are where those findings bite.
"""

from __future__ import annotations

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

from agentsync import slug
from agentsync.errors import CurateError
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

REFRESH_QUEUE_SH = r"""#!/bin/sh
# docs/ refresh queue — which curated pages are stale against mirror/.  Run from the docs repo root.
# rc 0 = every row fresh · 1 = rows need action (printed) · 2 = DEPENDS.tsv unusable.
TSV=${1:-DEPENDS.tsv}
[ -s "$TSV" ] || { echo "$TSV: missing or empty" >&2; exit 2; }
awk -F'\t' 'NR==1 && $1!="page"{exit 2} {exit 0}' "$TSV" || {
  echo "$TSV: missing header (page/source/pinned_sha/role)" >&2; exit 2; }
OUT=$(awk -F'\t' '
  NR==1 { next }
  NF < 3 { printf "MALFORMED\t%s\t(fields=%d)\n", $1, NF; next }
  {
    page=$1; src=$2; pin=$3; cur=""; st=""; ok=0; dash=0
    if (pin == "")               { printf "UNPINNED\t%s\t%s\n", page, src; next }
    if (pin !~ /^[0-9a-f]{64}$/) { printf "BAD-PIN\t%s\t%s\t(len=%d)\n", page, src, length(pin); next }
    while ((getline line < src) > 0) {            # frontmatter only: stop at the closing ---
      if (line == "---") { if (++dash == 2) break; else continue }
      if (line ~ /^rendered_sha256: /) { cur = substr(line, 18); ok = 1 }
      else if (line ~ /^status: /)     { st  = substr(line, 9) }
    }
    close(src)
    if (st == "deleted")        { printf "SOURCE-DELETED\t%s\t%s\n", page, src; next }
    if (st == "unreadable" || st == "refused") { printf "SOURCE-UNREADABLE\t%s\t%s\n", page, src; next }
    if (!ok)                    { printf "MISSING-OR-UNPARSEABLE\t%s\t%s\n", page, src; next }
    if (cur != pin)             { printf "STALE\t%s\t%s\n", page, src; next }
  }' "$TSV" | sort -u)
[ -n "$OUT" ] && { printf '%s\n' "$OUT"; exit 1; }
exit 0
"""
"""The design 4.5 script, verbatim except the default TSV path (docs repo root).  Written into README.md."""

_log = logging.getLogger(__name__)

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX64_BYTES = re.compile(rb"[0-9a-f]{64}")
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


# ---------------------------------------------------------------------------------------------------------
# value types
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopicSource:
    """One ``sources:`` entry as written (path is PAGE-relative)."""

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


@dataclass(frozen=True, slots=True)
class RefreshVerdict:
    """One refresh-queue output row (same vocabulary and order as the shell script: ``sort -u``)."""

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
    )


def parse_topic_page(layout: DocsLayout, rel_path: str) -> TopicPage:
    """Parse one curated page's frontmatter; raises CurateError (no/invalid frontmatter, bad ``sources:``
    shape)."""
    return _parse_topic_text(rel_path, _read_page_text(layout, rel_path))


def normalise_source_path(page_rel: str, source_rel_to_page: str) -> str:
    """Resolve a page-relative ``sources:`` path to docs-repo-relative; CurateError if it escapes docs/."""
    src = source_rel_to_page.strip()
    if not src:
        raise CurateError(f"{page_rel}: empty source path")
    if src.startswith("/"):
        raise CurateError(f"{page_rel}: source path {src!r} is absolute; write it relative to the page")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), src))
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
            source = normalise_source_path(rel, src.path)
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
        if not source.startswith("mirror/"):
            findings.append(
                _finding("SOURCE-NOT-MIRROR", rel, f"sources[{i}] ({source}) is not a mirror/ page")
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


def generate_depends(layout: DocsLayout) -> tuple[list[DependsRow], list[tuple[str, str]], list[LintFinding]]:
    """Parse every curated page -> (DEPENDS rows sorted by (page, source), (entity, page) rows sorted, lint
    findings: CURATE-PARSE, MISSING-ENTITY, BAD-ROLE, UNPINNED/BAD-PIN rows are still emitted for the
    queue)."""
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
# the refresh queue (Python twin of REFRESH_QUEUE_SH)
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
    """Python twin of REFRESH_QUEUE_SH: returns (rc, verdicts) with identical semantics (rc 0/1/2)."""
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


def lint_unlisted_pages(layout: DocsLayout, rows: Sequence[DependsRow]) -> list[LintFinding]:
    """UNLISTED: every curated page that is not ``provenance: hand-written`` appears in DEPENDS.tsv
    (blocking=False for the sync; the ``agentsync lint`` command reports it as an error)."""
    listed = {r.page for r in rows}
    return [
        _finding(
            "UNLISTED",
            rel,
            "curated page is in no DEPENDS.tsv row, so it can never go STALE: add pinned sources: "
            f"or mark it 'provenance: {HAND_WRITTEN}'",
        )
        for rel in iter_topic_pages(layout)
        if rel not in listed and not _is_hand_written(layout, rel)
    ]


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
