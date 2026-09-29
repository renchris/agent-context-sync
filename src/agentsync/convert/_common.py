"""Private helpers shared by the converters (owner: convert).  Every name here is private on purpose:
``tests/test_contracts.py`` requires each public name to appear in CONTRACTS.md."""

from __future__ import annotations

import csv
import io
import mmap
import re
import sys
import unicodedata
import zipfile
from collections.abc import Iterable, Sequence
from importlib import metadata
from pathlib import Path

from agentsync.errors import ConversionError, UnreadableSourceError

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")
_DRM_DATASPACES = "\x06DataSpaces".encode("utf-16-le")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_CELL_WS_RE = re.compile(r"[ \t\f\v]+")
_SLUG_DROP_RE = re.compile(r"[^a-z0-9]+")
# Block syntax that would change a plain-text line's meaning: ATX headings, setext underlines, rules.
# Lists and quotes are left alone (they render as what they are in e-mail and slide text).
_LEADING_BLOCK_RE = re.compile(r"^(\s{0,3})(#|=+\s*$|-+\s*$|\*{3,}\s*$|_{3,}\s*$)")


def _dist_version(dist: str) -> str:
    """Installed distribution version, as run (``unknown`` if the metadata is missing)."""
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "unknown"


def _python_version() -> str:
    """``major.minor.micro`` of the running interpreter (the stdlib is the library for eml/text)."""
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def _unicode_version() -> str:
    """Unicode database version used by NFC normalisation (it can change output across Pythons)."""
    return unicodedata.unidata_version


# ---------------------------------------------------------------------------------------------------------
# Container sniffing
# ---------------------------------------------------------------------------------------------------------


def _check_ooxml_container(src: Path) -> None:
    """Raise UnreadableSourceError for an encrypted / IRM / OLE package, ConversionError for a non-ZIP.

    Office encrypts a package (password or sensitivity-label/IRM) by wrapping it in an OLE compound file
    whose ``EncryptedPackage`` stream holds the ciphertext; Graph ``/content`` returns exactly those bytes.
    """
    with src.open("rb") as fh:
        head = fh.read(8)
        if head == _OLE_MAGIC:
            size = src.stat().st_size
            if size == 0:
                raise UnreadableSourceError("encrypted")
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                if mm.find(_ENCRYPTED_PACKAGE) >= 0 or mm.find(_DRM_DATASPACES) >= 0:
                    raise UnreadableSourceError("encrypted")
            raise UnreadableSourceError("encrypted or legacy binary Office file (OLE container, not OOXML)")
    if not zipfile.is_zipfile(src):
        raise ConversionError("not an OOXML package (not a ZIP container)")


def _check_odf_container(src: Path) -> None:
    """Raise UnreadableSourceError for a password-protected ODF package, ConversionError for a non-ZIP."""
    if not zipfile.is_zipfile(src):
        raise ConversionError("not an ODF package (not a ZIP container)")
    try:
        with zipfile.ZipFile(src) as zf:
            try:
                manifest = zf.read("META-INF/manifest.xml")
            except KeyError:
                return
    except (zipfile.BadZipFile, OSError) as exc:
        raise ConversionError(f"corrupt ODF package: {exc}") from exc
    if b"encryption-data" in manifest:
        raise UnreadableSourceError("password-protected")


# ---------------------------------------------------------------------------------------------------------
# Text decoding and markdown helpers
# ---------------------------------------------------------------------------------------------------------


def _decode_text(data: bytes) -> str:
    """Decode bytes: UTF-8/UTF-16 BOM first, then strict UTF-8, then cp1252, then latin-1.

    cp1252 before latin-1 because Windows-authored corporate text uses its smart quotes (0x91-0x94), which
    latin-1 maps to C1 control characters.  Raises ConversionError on NUL bytes outside UTF-16 (binary).
    """
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", errors="replace")
    elif data.startswith(_UTF16_BOMS):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ConversionError(f"invalid UTF-16 text: {exc.reason}") from exc
    else:
        if b"\x00" in data:
            raise ConversionError("binary content (NUL bytes)")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1252")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
    if "\x00" in text:
        raise ConversionError("binary content (NUL characters)")
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _fence(text: str, lang: str = "") -> str:
    """Wrap ``text`` in a backtick fence longer than any backtick run inside it."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    inner = text if text.endswith("\n") or not text else text + "\n"
    return f"{ticks}{lang}\n{inner}{ticks}\n"


def _escape_line(line: str) -> str:
    """Neutralise markdown block syntax at the start of a plain-text line and HTML comment openers.

    Plain text (PDF, email, slide text) must not turn into headings, rules or fake ``<!-- page: N -->``
    anchors; everything else is left verbatim so grep still matches.
    """
    out = line.replace("<!--", "&lt;!--")
    m = _LEADING_BLOCK_RE.match(out)
    if m:
        indent = m.group(1)
        out = indent + "\\" + out[len(indent) :]
    return out


def _escape_plain(text: str) -> str:
    """Apply :func:`_escape_line` to every line; strip trailing whitespace; collapse 3+ blank lines to one."""
    lines = [_escape_line(ln.rstrip()) for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip("\n")


def _cell(text: str) -> str:
    """Render one GFM table cell: one line (``<br>`` for newlines), pipes escaped, whitespace collapsed."""
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n")
    t = "<br>".join(_CELL_WS_RE.sub(" ", part).strip() for part in t.split("\n"))
    return t.replace("\\", "\\\\").replace("|", "\\|")


def _gfm_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    """Return GFM pipe-table lines; cells must already be escaped with :func:`_cell`.  Rows are padded."""
    width = len(header)
    materialised = [list(r) for r in rows]
    for r in materialised:
        width = max(width, len(r))
    width = max(width, 1)
    head = list(header) + [""] * (width - len(header))
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    for r in materialised:
        padded = r + [""] * (width - len(r))
        lines.append("| " + " | ".join(padded) + " |")
    return lines


def _headings(markdown: str, *, max_level: int = 3) -> list[tuple[int, str]]:
    """ATX headings (level, text) outside fenced code, in document order."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= max_level:
            text = re.sub(r"[*_`]", "", m.group(2)).strip()
            if text:
                out.append((len(m.group(1)), text))
    return out


def _title_from_markdown(markdown: str, fallback: str) -> str:
    """First level-1 heading, else the first heading of any level, else ``fallback``."""
    heads = _headings(markdown, max_level=6)
    for level, text in heads:
        if level == 1:
            return text
    return heads[0][1] if heads else fallback


def _join_limited(items: Iterable[str], *, limit: int = 180, sep: str = "; ") -> str:
    """Join items until ``limit`` characters; append ``…`` when something was left out."""
    out = ""
    for item in items:
        candidate = item if not out else out + sep + item
        if len(candidate) > limit:
            return (out + sep + "…") if out else item[: limit - 1] + "…"
        out = candidate
    return out


def _summary_from_markdown(kind: str, markdown: str) -> str:
    """Converter-derived summary: kind plus the heading outline (never model output)."""
    heads = [text for _level, text in _headings(markdown, max_level=3)]
    if not heads:
        return kind
    return f"{kind}; headings: " + _join_limited(heads)


def _ascii_slug(text: str, *, fallback: str = "unit") -> str:
    """Portable lowercase ASCII slug for sidecar names (already a fixed point of any stricter slugger)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_DROP_RE.sub("-", folded).strip("-")
    return slug[:60].strip("-") or fallback


# ---------------------------------------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------------------------------------


def _cap_body(body: str, max_bytes: int, *, sidecar_name: str) -> tuple[str, tuple[tuple[str, bytes], ...]]:
    """Cut ``body`` at a line boundary so it fits ``max_bytes``; the full text rides as a sidecar.

    Returns ``(body, ())`` when it already fits.  The cut is deterministic (byte budget, last newline).
    """
    raw = body.encode("utf-8")
    if len(raw) <= max_bytes:
        return body, ()
    note_budget = 200 + len(sidecar_name.encode("utf-8"))
    budget = max(0, max_bytes - note_budget)
    head = raw[:budget].decode("utf-8", errors="ignore")
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    shown = len(head.encode("utf-8"))
    head = head.rstrip("\n")
    fence = _open_fence(head)
    if fence is not None:  # the cut landed inside a code block: close it so the note renders as text
        head += "\n" + fence
    note = (
        f"\n\n[truncated: first {shown} of {len(raw)} bytes shown; the full text is in the sidecar "
        f"`{sidecar_name}`]\n"
    )
    return head + note, ((sidecar_name, raw),)


def _open_fence(markdown: str) -> str | None:
    """The fence marker still open at the end of ``markdown`` (CommonMark rules, simplified), or None."""
    open_marker: str | None = None
    for line in markdown.split("\n"):
        m = _FENCE_RE.match(line)
        if not m:
            continue
        marker = m.group(1)
        if open_marker is None:
            open_marker = marker
        elif (
            marker[0] == open_marker[0]
            and len(marker) >= len(open_marker)
            and not line.strip()[len(marker) :]
        ):
            open_marker = None
    return open_marker


def _csv_bytes(rows: Iterable[Sequence[str]]) -> bytes:
    """Serialise rows as RFC 4180 CSV with LF line ends (deterministic)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


_FULL_TEXT_SIDECAR = "full-text.txt"
"""Sidecar name for the untruncated body of a capped WHOLE unit (publish namespaces sidecars per page)."""


def _first_line(markdown: str, *, limit: int = 120) -> str:
    """First non-empty, non-anchor text line (markup stripped, capped), or ""."""
    for line in markdown.split("\n"):
        t = line.strip()
        if not t or t.startswith(("<!--", "|", "```", "---")):
            continue
        t = re.sub(r"^[#>*\-+\\\s]+", "", t)
        t = re.sub(r"[*_`]", "", t).strip()
        if len(t) >= 2:
            return t[:limit]
    return ""
