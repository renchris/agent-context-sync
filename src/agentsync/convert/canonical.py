"""H1: canonical content hashes that ignore container noise (design 4.1 #2) (owner: convert)."""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
import zipfile
import zlib
from pathlib import Path

from agentsync.model import CanonicalHash

OOXML_SUFFIXES: tuple[str, ...] = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm")

VOLATILE_PARTS: tuple[str, ...] = (
    "docProps/*",
    "xl/calcChain.xml",
    "*/printerSettings/*",
    "docMetadata/LabelInfo.xml",
)
"""Glob patterns of OOXML parts excluded from H1 entirely (a starting point, not a guarantee)."""

VOLATILE_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("xl/workbook.xml", "xr:revisionPtr/@documentId"),
    ("word/document.xml", "w:rsid*"),
    ("word/settings.xml", "w:rsids"),
)
"""(part, attribute/element) pairs canonicalised away before hashing (measured Office re-save churn, 9 #6)."""

log = logging.getLogger(__name__)

_CHUNK = 1 << 20

# Byte-level rewrites implementing VOLATILE_ATTRIBUTES (and a few save-metadata siblings measured or known to
# churn on a no-edit save).  Each pattern removes save/revision metadata only, never content.
#   xl/workbook.xml   xr:revisionPtr (documentId is a fresh GUID per save - C12 §6; revIDLastSave/uidLastSave
#                     move with co-authoring), x15ac:absPath (the saving machine's folder), fileVersion
#                     (the saving Excel build)
#   word/*.xml        every w:rsid* attribute (revision-save ids; C12 §6: w:rsidRDefault per save), applied to
#                     every WordprocessingML part because headers/footers/footnotes/styles carry them too
#   word/settings.xml the <w:rsids> list (gains the session's id per save)
_WORKBOOK_RULES: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"<(?:[A-Za-z0-9_]+:)?revisionPtr\b[^>]*?/>"),
    re.compile(rb"<(?:[A-Za-z0-9_]+:)?revisionPtr\b[^>]*?>.*?</(?:[A-Za-z0-9_]+:)?revisionPtr>", re.DOTALL),
    re.compile(rb"<(?:[A-Za-z0-9_]+:)?absPath\b[^>]*?/>"),
    re.compile(rb"<(?:[A-Za-z0-9_]+:)?fileVersion\b[^>]*?/>"),
)
_WORD_RSID_ATTR = re.compile(rb"\s+w:rsid[A-Za-z]*=\"[^\"]*\"")
_WORD_RSIDS_ELEMENT = (
    re.compile(rb"<w:rsids\b[^>]*?/>"),
    re.compile(rb"<w:rsids\b[^>]*?>.*?</w:rsids>", re.DOTALL),
)
_WORD_XML = re.compile(r"^word/[^/]+\.xml$")


def _is_volatile_part(name: str) -> bool:
    """True when ``name`` matches a VOLATILE_PARTS glob."""
    return any(fnmatch.fnmatchcase(name, pat) for pat in VOLATILE_PARTS)


def _needs_rewrite(name: str) -> bool:
    """True for the parts whose bytes are canonicalised (so they must be read whole)."""
    return name == "xl/workbook.xml" or bool(_WORD_XML.match(name))


def _canonical_part(name: str, data: bytes) -> bytes:
    """Remove volatile attributes/elements from one part's bytes."""
    if name == "xl/workbook.xml":
        for pat in _WORKBOOK_RULES:
            data = pat.sub(b"", data)
        return data
    if _WORD_XML.match(name):
        data = _WORD_RSID_ATTR.sub(b"", data)
        if name == "word/settings.xml":
            for pat in _WORD_RSIDS_ELEMENT:
                data = pat.sub(b"", data)
    return data


def _bytes_hash(path: Path) -> CanonicalHash:
    """``bytes@1``: sha256 of the whole file, streamed."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return CanonicalHash(sha256=h.hexdigest(), method="bytes@1")


def _ooxml_hash(path: Path) -> CanonicalHash:
    """``ooxml-parts@1``: rollup over sorted (part name, canonical part digest); ZIP metadata ignored."""
    digests: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if _is_volatile_part(name):
                continue
            h = hashlib.sha256()
            if _needs_rewrite(name):
                h.update(_canonical_part(name, zf.read(info)))
            else:
                with zf.open(info) as fh:
                    while chunk := fh.read(_CHUNK):
                        h.update(chunk)
            digests.append((name, h.hexdigest()))
    digests.sort()
    roll = hashlib.sha256(b"ooxml-parts@1\n")
    for name, digest in digests:
        roll.update(name.encode("utf-8") + b"\x00" + digest.encode("ascii") + b"\n")
    return CanonicalHash(sha256=roll.hexdigest(), method="ooxml-parts@1", parts=tuple(digests))


def canonical_hash(path: Path, *, suffix: str) -> CanonicalHash:
    """Return H1 for a staged file.

    OOXML (``OOXML_SUFFIXES``): sha256 over sorted ``(part name, canonical part bytes)`` with all ZIP metadata
    discarded, ``VOLATILE_PARTS`` dropped and ``VOLATILE_ATTRIBUTES`` removed; ``parts`` lists each kept
    part's digest; method ``"ooxml-parts@1"``. Everything else: sha256 of the bytes, method ``"bytes@1"``. A
    corrupt ZIP falls back to ``bytes@1`` (logged), never raises for that reason.
    """
    if suffix.lower() in OOXML_SUFFIXES:
        if not zipfile.is_zipfile(path):
            log.info("canonical_hash: %s is not a ZIP (encrypted or not OOXML); hashing bytes", path.name)
            return _bytes_hash(path)
        try:
            return _ooxml_hash(path)
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
            EOFError,
            ValueError,
            NotImplementedError,
        ) as exc:
            log.warning("canonical_hash: corrupt OOXML package %s (%s); hashing bytes", path.name, exc)
            return _bytes_hash(path)
        except OSError as exc:
            log.warning("canonical_hash: unreadable OOXML part in %s (%s); hashing bytes", path.name, exc)
            return _bytes_hash(path)
    return _bytes_hash(path)


def differing_parts(old: tuple[tuple[str, str], ...], new: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Return sorted part names added, removed or changed between two ``CanonicalHash.parts`` tuples."""
    a = dict(old)
    b = dict(new)
    return tuple(sorted(n for n in set(a) | set(b) if a.get(n) != b.get(n)))
