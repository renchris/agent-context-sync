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
_EMITTER_RE = re.compile(r"(\d{1,9})\.(\d{1,9})\.(\d{1,9})(?:\+|$)", re.ASCII)


def _emitter(version: str) -> tuple[int, int, int] | None:
    """The emitter part of a converter version as numbers: ``(2, 1, 0)`` of ``2.1.0+pypdfium2-…``.  None when
    the version does not start with three plain numbers (``unavailable``, ``2.2.0rc1``): a converter asked
    whether a page it once produced is outdated never says yes to a version it cannot read."""
    found = _EMITTER_RE.match(version)
    return None if found is None else (int(found[1]), int(found[2]), int(found[3]))


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


_CONTENT_TYPES_MAX = 1 << 20
_MAIN_TYPE_RE = re.compile(
    # whitespace around the "=" is XML's too (B13 round 29 C222)
    rb"\bContentType\s*=\s*[\"']application/vnd\.(?:openxmlformats-officedocument\."
    rb"(wordprocessingml|spreadsheetml|presentationml)|ms-(word|excel|powerpoint))\.[^\"']*\.main(?:\+xml)?[\"']"
)
_FAMILY_OF = {
    b"wordprocessingml": "word",
    b"word": "word",
    b"spreadsheetml": "spreadsheet",
    b"excel": "spreadsheet",
    b"presentationml": "presentation",
    b"powerpoint": "presentation",
}
_FAMILY_NOUN = {
    "word": "a Word document",
    "spreadsheet": "an Excel workbook",
    "presentation": "a PowerPoint deck",
}


def _ooxml_family(src: Path) -> str | None:
    """The Office family of an OOXML package's main part, read from ``[Content_Types].xml``: ``word``,
    ``spreadsheet`` or ``presentation`` (macro, template and slideshow types included).  None when it cannot
    be told: no such part, one too large, a damaged package, no main type, or main types of two families."""
    try:
        with zipfile.ZipFile(src) as zf:
            info = zf.getinfo("[Content_Types].xml")
            if info.file_size > _CONTENT_TYPES_MAX:
                return None
            types = zf.read(info)
    except Exception:  # KeyError, BadZipFile, OSError, an encrypted member, a compression zipfile lacks
        return None
    families = {_FAMILY_OF[m[1] or m[2]] for m in _MAIN_TYPE_RE.finditer(types)}
    return families.pop() if len(families) == 1 else None


def _check_ooxml_family(src: Path, family: str, noun: str) -> None:
    """Raise UnreadableSourceError when ``src``'s main part is of another Office family than ``family`` (a
    Word document named ``.potx``, B13 round 28 I39): one stub, cached, never retried, which the setup
    report classes "not the type its name says".  Fixed words, the family noun only.  A family that cannot
    be told, or the converter's own one under another main type (a template, a macro deck), passes."""
    found = _ooxml_family(src)
    if found is not None and found != family:
        raise UnreadableSourceError(
            f"not-ooxml: {_FAMILY_NOUN[found]}, not {noun} (not the type its name says)"
        )


def _pathless(text: str, src: Path, name: str) -> str:
    """``text`` with ``src`` as ``name``, its folder dropped before any other file in it, and the home folder
    as ``~``: a library's error names the staged file (``~/Library/Application Support/agentsync/staging/…``),
    and a stub's reason goes into the page, ``_sync/QUARANTINE.tsv`` and the manifest (B13 round 28 I39).
    Each path's longest form goes first, so a resolved ``/private/tmp/…`` leaves no ``/private`` behind
    (B13 round 29 C183), and each in both its spellings, the escaped one first: an ``OSError`` or a
    ``PosixPath`` prints its path with ``repr``, so a tab is ``\\t`` there and a backslash doubled (B13 round
    30 C364), and in the forced single-quote spelling too, which an error naming a file with a double quote
    gives its folder's apostrophes (B13 round 31 C578). The folder named alone, with nothing of a file after
    it, is cut only at a path's end (``_PATH_END``), to a fixed word, so a sibling whose name extends it with
    no space stays whole and a quoted path never reads ``''`` (C578, B13 round 32 C646)."""
    for spelling in _spellings(src):
        text = text.replace(spelling, name)
    folder = [spelling for spelling in _spellings(src.parent) if len(spelling) > 1]
    for spelling in folder:  # the folder alone, a trailing slash or none, before any path in it
        text = re.sub(re.escape(spelling) + "/?" + _PATH_END, _FOLDER_WORD, text)
    for spelling in folder:
        text = text.replace(spelling + "/", "")
    return _homeless(text)


_FOLDER_WORD = "its folder"
"""What a staged file's folder named alone in a reason becomes (``_pathless``, B13 round 31 C578): fixed
words, never a path and never empty."""
_PATH_END = r"(?=$|\.(?=$|\s)|\\'|[\s'\":,;)\]>])"
"""Where a path named alone ends in an error's text: the end, whitespace, a quote or an escaped quote
(``\\'``), one of ``:,;)]>`` (``<DirEntry …>``), or a sentence-ending ``.`` (the end or whitespace after it),
never a bare ``.``, so ``<folder>.bak`` stays whole. Whitespace stays an end, so the first of two paths
(``cp <folder> /dest``) is cut (B13 round 32 C646)."""
_PATH_GOES_ON = r"(?=/|$|\.(?=$|\s)|\\'|[\s'\":,;)\]>])"
"""Where a staged file's path ends in an error's text (as ``_PATH_END``), or goes on into a part below it
(``<file>/x``)."""


def _homeless(text: str) -> str:
    """``text`` with the home folder, in each of its forms, as ``~``: the one fold a reason with no staged
    file gets too (a converter that cannot start, ``[convert] pandoc_path`` under the home; B13 round 29
    C182), which ``_pathless`` ends with."""
    try:
        home = Path.home()
    except (RuntimeError, KeyError, OSError):  # no HOME and no password entry: nothing to fold
        return text
    for spelling in _spellings(home):
        if len(spelling) > 1:
            text = text.replace(spelling, "~")
    return text


def _spellings(path: Path) -> list[str]:
    """Each form of ``path`` (``_forms``) as written, as ``repr`` prints it (a tab as ``\\t``, a backslash
    doubled, U+00A0 as ``\\xa0``; B13 round 30 C364) and as ``repr`` prints it inside single quotes, each
    apostrophe as ``\\'``, which it does for a path that also holds a double quote (B13 round 31 C578).
    Spellings are replaced longest first (B13 round 29 C183, round 31 C551)."""
    found: list[str] = []
    for form in _forms(path):
        plain = str(form)
        for spelling in (repr(plain)[1:-1], repr(plain + '"')[1:-2], plain):
            if spelling not in found:
                found.append(spelling)
    return sorted(found, key=len, reverse=True)


def _forms(path: Path) -> list[Path]:
    """``path`` and its resolved form when that differs (``/var`` is ``/private/var``); a resolve that fails
    (a link loop) leaves ``path`` alone, as ``_pathless`` runs inside ``convert_file``'s error handling."""
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        return [path]
    return [path] if real == path else [path, real]


def _stub_with_a_path(reason: str) -> bool:
    """True for a stub whose reason holds a staging or home path, which an older build wrote and
    ``convert_file`` now keeps out (``_pathless``; B13 round 28 I39). Only a failed conversion's reason held
    one, so the cycle converts such a stub once more past its retries, once per bytes (``cycle._retries``,
    B13 round 29 C181). With no home folder to read, the staging words alone."""
    if "/staging/" in reason:
        return True
    try:
        home = str(Path.home())
    except (RuntimeError, KeyError, OSError):  # no HOME and no password entry, as in _homeless
        return False
    # every spelling, the forced single-quote one too (B13 round 31 C578)
    return len(home) > 1 and any(spelling in reason for spelling in _spellings(Path(home)))


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
