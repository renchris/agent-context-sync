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
from typing import Any

from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.policy import UNTRUSTED_BANNER

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")
_DRM_DATASPACES = "\x06DataSpaces".encode("utf-16-le")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
"""UTF-32LE's and UTF-32BE's byte order marks (``codecs.BOM_UTF32_LE`` and ``BOM_UTF32_BE``), told before
:data:`_UTF16_BOMS`, since ``FF FE 00 00`` opens with UTF-16LE's mark (B13 round 47 C3321)."""

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
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
"""The namespace of ``[Content_Types].xml``'s Override and Default elements, the only ones that type a part:
pptx's ``_decoded_deck_types`` retypes them (B13 round 43 C2517; one copy, here, since round 44 C2708).
:func:`_ooxml_family` reads them as ``<Types>``' direct children in ``<Types>``' own namespace, or in none
or this one when it has none, as openpyxl reads a part with no ``xmlns`` (B13 round 45 C2854; round 46 C3047:
in any namespace, a foreign or ``xmlns=""`` decoy beside a Visio drawing's real Override was the one family
read; round 47 C3264: in none only, a ``ct:``-prefixed child under a no-namespace ``<Types>`` was unread)."""
_MAIN_TYPE_RE = re.compile(
    # whitespace around the "=" is XML's too (B13 round 29 C222)
    rb"\bContentType\s*=\s*[\"']application/vnd\.(?:openxmlformats-officedocument\."
    rb"(wordprocessingml|spreadsheetml|presentationml)|ms-(word|excel|powerpoint))\.[^\"']*\.main(?:\+xml)?[\"']"
)
_MAIN_TYPE_VALUE_RE = re.compile(
    r"application/vnd\.(?:openxmlformats-officedocument\.(wordprocessingml|spreadsheetml|presentationml)"
    r"|ms-(word|excel|powerpoint))\.[^\"']*\.main(?:\+xml)?"
)
""":data:`_MAIN_TYPE_RE` for a ContentType value an XML parser has decoded (B13 round 42 C2303)."""
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
_EXPECTED_NOUN = {**_FAMILY_NOUN, "visio": "a Visio drawing"}
"""The noun :func:`_check_ooxml_family` names the expected type by: a family's own :data:`_FAMILY_NOUN`, so
a deck is ``a PowerPoint deck`` and a workbook ``an Excel workbook`` on both sides of a stub (B13 round 47
C3230: the deck converter said ``a deck`` and the workbook one ``a workbook`` as the expected type), and
Visio's, which is no family ``_ooxml_family`` finds, so it stays out of :data:`_FAMILY_NOUN` and the found
side of :data:`FAMILY_MISMATCH_RE`."""
FAMILY_MISMATCH_RE = re.compile(
    rf"not-ooxml: (?:{'|'.join(map(re.escape, _FAMILY_NOUN.values()))}), not .+ "
    r"\(not the type its name says\)"
)
"""The whole reason :func:`_check_ooxml_family` raises (``not-ooxml: <noun>, not <noun> (not the type its name
says)``), never ``policy.NOT_OOXML_REASON`` (bytes that are not a ZIP): the stubs
``cycle._Cycle._reread_family_stubs`` reads once more after the family rule changed (B13 round 45 C2925).  The
expected side is any text, so a stub cached under an older noun (``not a deck``, ``not a workbook``) still
matches (B13 round 47 C3230)."""


def _ooxml_family(src: Path) -> str | None:
    """The Office family of an OOXML package's main part, read from ``[Content_Types].xml``: ``word``,
    ``spreadsheet`` or ``presentation`` (macro, template and slideshow types included).  None when it cannot
    be told: no such part, one too large (the cap, :data:`_CONTENT_TYPES_MAX`, is on the part's own bytes
    before any decode: over 1 MiB uncompressed, in whichever encoding it is stored), a damaged package, no
    main type, or main types of two families.  Within that cap, a part that opens with a UTF-16 byte order
    mark is read as its UTF-8 twin is when it does not parse (B13 round 34 C827); near the cap the twins can
    differ, in either direction, since UTF-16 takes 2 bytes for an ASCII character and UTF-8 takes 3 for a
    CJK one (B13 round 35 C964).  The parse reads the part's own bytes first, before any decode, so it
    tells a UTF-32 part and a mark-less UTF-16 part that opens with an XML declaration; only the byte
    fallback cannot read either, so a mark-less UTF-16 part with no declaration, which lxml does not parse,
    cannot be told (B13 round 45 C2855).  Only a part that does not parse is decoded, for the fallback, and
    a UTF-16 one that does not decode is None (B13 round 46 C3098: the decode ran first, so a UTF-32LE part
    holding a character past U+FFFF failed it and was None, though it parsed).  A UTF-32 part that does not
    parse is None, because the UTF-16 decode of a UTF-32LE mark leaves a unit no higher than U+0010 after
    every character, and UTF-32BE has no UTF-16 mark, so the byte regex never matches (B13 round 47 C3266:
    an early UTF-32 return here changed no UTF-32 answer; it only turned a crafted UTF-16 part opening U+0000
    from a family stub into a failed conversion).

    The parse decides: the families are read from the decoded ContentType of each Override and Default
    element (:func:`content_types_tree`) that is a direct child of ``<Types>`` in ``<Types>``' own namespace,
    or in none or the content-types namespace when ``<Types>`` has none, as openpyxl reads a part with no
    ``xmlns`` by local name (B13 round 47 C3264: a Word package named ``.xlsx`` whose ``<Types
    xmlns:ct=...>`` holds ``ct:Override`` children gave no family, C2854's failed conversion back; a foreign
    namespace is still not read there, more conservative than openpyxl) (B13 round 45 C2854: read
    in the content-types namespace only, a Word package or a deck named ``.xlsx`` whose ``<Types>`` has no
    ``xmlns`` lost its ``not the type its name says`` stub to a failed conversion; round 46 C3047: read in
    any namespace, a foreign or ``xmlns=""`` decoy beside a Visio drawing's real Override, whose type is no
    family, was the one family read, so a real ``.vsdx`` was a ``not the type its name says`` stub).
    A main-type literal in a comment, a processing instruction or an element of another name never counts,
    and a type spelled with a character reference does.  The byte regex over the part is the fallback, only
    when the part does not parse (B13 round 44 C2708 with C2653, ruling 185: a Word literal in a comment told
    a ``.potx`` whose real Override was escaped as a Word document, a cached, never-retried ``not the type
    its name says`` stub)."""
    try:
        with zipfile.ZipFile(src) as zf:
            info = zf.getinfo("[Content_Types].xml")
            if info.file_size > _CONTENT_TYPES_MAX:
                return None
            raw = types = zf.read(info)
    except Exception:  # KeyError, BadZipFile, OSError, an encrypted member, a compression zipfile lacks
        return None
    tree = content_types_tree(raw)  # its own bytes, which its declaration names, before any decode (C3098)
    if tree is None:  # unparseable (the cap returned None above): the byte read stands
        if raw.startswith(_UTF16_BOMS):
            try:
                types = raw.decode("utf-16").encode("utf-8")
            except UnicodeError:
                return None
        families = {_FAMILY_OF[m[1] or m[2]] for m in _MAIN_TYPE_RE.finditer(types)}
    else:  # decoded, and only the elements that type a part (B13 round 44 C2708 with C2653)
        families = set()
        # <Types>' own namespace, or none or the content-types one when it has none (C2854; B13 round 47
        # C3264: a ct:-prefixed child under a no-namespace <Types>, which openpyxl reads by local name), and
        # only its direct children: a decoy of another namespace or in xmlns="" under a namespaced <Types> is
        # not read at all (B13 round 46 C3047); iterchildren by tag never yields a comment or processing
        # instruction
        tag = tree.tag if isinstance(tree.tag, str) else ""
        ns = tag[1:].partition("}")[0] if tag.startswith("{") else ""
        ct = _CONTENT_TYPES_NS
        names = (
            (f"{{{ns}}}Override", f"{{{ns}}}Default")
            if ns
            else ("Override", "Default", f"{{{ct}}}Override", f"{{{ct}}}Default")
        )
        for el in tree.iterchildren(*names):
            if m := _MAIN_TYPE_VALUE_RE.fullmatch(el.get("ContentType") or ""):
                families.add(_FAMILY_OF[(m[1] or m[2]).encode("ascii")])
    return families.pop() if len(families) == 1 else None


def content_types_tree(types: bytes) -> Any | None:
    """``[Content_Types].xml``'s root element, the first read of :func:`_ooxml_family` and pptx's
    ``_deck_types``, whose byte reads are only the fallback for a part this returns None for (B13 round 44
    C2708, C2722, ruling 185): at most :data:`_CONTENT_TYPES_MAX` bytes, with no entity, DTD or network
    resolution and no huge tree, so a character reference (``templa&#116;e``) reads as the value it stands
    for; None when it is too large or does not parse (B13 round 42 C2303)."""
    if len(types) > _CONTENT_TYPES_MAX:
        return None
    from lxml import etree  # type: ignore[import-untyped]  # noqa: PLC0415 - python-pptx's parser

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)
    try:
        return etree.fromstring(types, parser)
    except Exception:  # XMLSyntaxError, a ValueError on a declared encoding lxml refuses
        return None


def _check_ooxml_family(src: Path, family: str) -> None:
    """Raise UnreadableSourceError when ``src``'s main part is of another Office family than ``family`` (a
    Word document named ``.potx``, B13 round 28 I39): one stub, cached and not retried, except the one re-read
    ``cycle._Cycle._reread_family_stubs`` gives after the family rule changed (B13 round 45 C2925; round 46
    C3048), which the setup report classes "not the type its name says".  Fixed words, the family noun
    only, one per family on both sides (:data:`_EXPECTED_NOUN`, B13 round 47 C3230).  A family that cannot be
    told, or the converter's own one under another main type (a template, a macro deck), passes."""
    found = _ooxml_family(src)
    if found is not None and found != family:
        raise UnreadableSourceError(
            f"not-ooxml: {_FAMILY_NOUN[found]}, not {_EXPECTED_NOUN[family]} (not the type its name says)"
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
    it, is cut only at a path's end (``_PATH_END``), to a fixed word, so a sibling whose name goes on past it
    with a character that is no end (``<folder>.bak``, ``<folder>2``) stays whole, while one that goes on with
    an end (``<folder>,v2``, ``<folder>'s copy``) is cut, and a quoted path never reads ``''`` (C578, B13
    round 32 C646; B13 round 33 C752: the ends are ``_PATH_END``'s, never a list here)."""
    for spelling in spellings(src):
        text = text.replace(spelling, name)
    folder = [spelling for spelling in spellings(src.parent) if len(spelling) > 1]
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
    for spelling in spellings(home):
        if len(spelling) > 1:
            text = text.replace(spelling, "~")
    return text


def spellings(path: Path) -> list[str]:
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
    return len(home) > 1 and any(spelling in reason for spelling in spellings(Path(home)))


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
    """Decode bytes: UTF-8/UTF-32/UTF-16 BOM first, then strict UTF-8, then cp1252, then latin-1.

    cp1252 before latin-1 because Windows-authored corporate text uses its smart quotes (0x91-0x94), which
    latin-1 maps to C1 control characters.  Raises ConversionError on NUL bytes outside UTF-16 and UTF-32
    (binary), and on a marked body that does not decode.  UTF-32's marks are told before UTF-16's, since
    ``FF FE 00 00`` opens with UTF-16LE's mark: a UTF-32LE file read as UTF-16 was NUL characters, and a
    UTF-32BE one fell to the NUL-byte check (B13 round 47 C3321 with C3268 and C3291).  A body of a bad
    length or code point raises ConversionError, never UnicodeDecodeError, so a UTF-16LE part opening
    U+0000 (``FF FE 00 00 41 00``) is still the ConversionError eml's lenient fallback catches.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        text = data[3:].decode("utf-8", errors="replace")
    elif data.startswith(_UTF32_BOMS):  # before UTF-16's, a prefix of one (C3321)
        try:
            text = data.decode("utf-32")
        except UnicodeDecodeError as exc:
            raise ConversionError(f"invalid UTF-32 text: {exc.reason}") from exc
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


def _longest_run(text: str, char: str) -> int:
    """The length of the longest run of ``char`` (a backtick or a tilde) in ``text``; 0 when it has none."""
    return max((len(m.group(0)) for m in re.finditer(re.escape(char) + "+", text)), default=0)


def _fence(text: str, lang: str = "") -> str:
    """Wrap ``text`` in a backtick fence longer than any backtick run inside it."""
    ticks = "`" * max(3, _longest_run(text, "`") + 1)
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


_DIGEST_LINE_HEAD = "Sidecar file "
"""``registry.SIDECAR_DIGEST_PREFIX``, which starts each line of a page's sidecar digest footer (a test holds
the two equal; every converter the registry builds imports this module, so it cannot import the registry)."""

GUARD_RESERVE = (
    len(UNTRUSTED_BANNER.encode("utf-8")) + 2 + 2 + 1 + len(f"{_DIGEST_LINE_HEAD}`` sha256 ") + 64 + 1
)
"""The bytes the registry's guard adds to a converter's page after the converter has sized it: the banner and
the blank line under it, the blank line over the digest footer, one footer line (less the sidecar's name, see
:func:`_footer_bytes`) and its line end, and the line end a body without one gets.  Every fit test against
``max_page_bytes`` takes it off first, so a guarded page stays within the cap (B13 round 41 C2044)."""


def _footer_bytes(name: str) -> int:
    """The bytes one more sidecar's digest footer line adds to a page: the line and its line end."""
    return len(f"{_DIGEST_LINE_HEAD}`{name}` sha256 ".encode()) + 64 + 1


def _cap_body(
    body: str, max_bytes: int, *, sidecar_name: str, others: Sequence[str] = ()
) -> tuple[str, tuple[tuple[str, bytes], ...]]:
    """Cut ``body`` so it fits ``max_bytes``; the full text rides as a sidecar.

    Returns ``(body, ())`` when it already fits.  The cut is deterministic (byte budget, then
    :func:`_line_head`): at a line, or mid-line at a word or character boundary when the last line end
    keeps under half the budget, never inside a ``\\`` escape (B13 round 42 C2272).  A cut inside a code
    block closes it; the note's budget holds a three-character close, and a longer one (a fence opened by a
    long backtick or tilde run) cuts the head again R + 1 bytes shorter, R the head's longest such run, and
    closes the fence still open on that shorter head, so the page stays within ``max_bytes``; a head whose
    close fits keeps its bytes (B13 round 41 C2042).  An open fence longer than half the budget, whose
    opener and close would leave its content no room, gives way to a short one (:func:`_short_fence`), its
    info string kept, so the content shows (C2272).  Both the fit test and the cut's budget leave
    :data:`GUARD_RESERVE` for the guard's banner and footer, and the footer lines of the page's ``others``
    sidecars (a recording window's keyframes), so the guarded page stays within ``max_bytes`` too (B13
    round 41 C2044).  When those footer lines leave the text less than the note's own room, the room is
    worked out again without them: the window keeps the text a keyframe-free page would show and passes
    the cap by its keyframe footer lines only (B13 round 42 C2254; returning it uncut has no bound).
    """
    raw = body.encode("utf-8")
    name_bytes = len(sidecar_name.encode("utf-8"))
    note_budget = 200 + name_bytes
    room = max_bytes - GUARD_RESERVE - sum(_footer_bytes(name) for name in others)
    if room - note_budget - name_bytes < note_budget:  # the footers leave the text no room (C2254)
        room = max_bytes - GUARD_RESERVE
    if len(raw) <= room:
        return body, ()
    budget = max(0, room - note_budget - name_bytes)
    head = _line_head(raw, budget)
    shown = len(head.encode("utf-8"))
    fence = _open_fence(head)
    if fence is not None and 2 * len(fence) > budget:  # its opener and close leave no room (C2272)
        head, fence, shown = _short_fence(raw, head, budget)
    elif fence is not None and len(head.rstrip("\n").encode("utf-8")) + 1 + len(fence) > budget + 4:
        runs = max(_longest_run(head, "`"), _longest_run(head, "~"))
        head = _line_head(raw, max(0, budget - (runs + 1)))
        shown = len(head.encode("utf-8"))
        fence = _open_fence(head)
    head = head.rstrip("\n")
    if fence is not None:  # the cut landed inside a code block: close it so the note renders as text
        head += "\n" + fence
    note = (
        f"\n\n[truncated: first {shown} of {len(raw)} bytes shown; the full text is in the sidecar "
        f"`{sidecar_name}`]\n"
    )
    return head + note, ((sidecar_name, raw),)


_CUT_SPACE = (" ", "\t")


def _line_head(raw: bytes, budget: int) -> str:
    """The first ``budget`` bytes of ``raw``, cut back to the last line end (that line end left out) when it
    keeps at least half the budget; else at the last blank in the budget's second half, else at the
    character boundary the decode gives, a trailing lone ``\\`` dropped so the cut never ends inside an
    escape (B13 round 42 C2272: a short line before one long line kept that short line alone)."""
    head = raw[:budget].decode("utf-8", errors="ignore")
    if len(raw) <= budget:
        return head
    cut = head.rfind("\n")
    if cut > 0 and 2 * len(head[:cut].encode("utf-8")) >= budget:
        return head[:cut]
    blank = max(head.rfind(c) for c in _CUT_SPACE)
    if blank > cut and 2 * len(head[:blank].encode("utf-8")) >= budget:
        head = head[:blank]
    slashes = len(head) - len(head.rstrip("\\"))
    return head[:-1] if slashes % 2 else head


def _short_fence(raw: bytes, head: str, budget: int) -> tuple[str, str, int]:
    """``(head, close, shown)`` for a cut ``head`` whose open fence's opener is longer than half ``budget``:
    the text before the opener, a short fence of the opener's character, longer than any run of it in the
    content it shows (three at least), the opener's info string (what follows the opener line's whole run,
    which the cut head may hold only part of; dropped past a fourth of the budget; B13 round 43 C2450), and
    the block's content after the opener line, up to its own closing line (C2451), cut to the room left;
    ``shown`` counts the source bytes the head stands for, the long opener line's included (B13 round 42
    C2272)."""
    text = raw.decode("utf-8", errors="ignore")
    start, marker = _fence_opener(head)
    line_end = text.find("\n", start)
    opener_end = line_end if line_end >= 0 else len(text)
    before = text[:start]
    line = text[start:opener_end].strip()
    info = line.lstrip(marker[0])  # the whole run off, not the head's part of it (C2450)
    run_length = len(line) - len(info)
    if 4 * len(info.encode("utf-8")) > budget:
        info = ""
    opener_bytes = len(text[: opener_end + 1].encode("utf-8"))
    content = _until_close(raw[opener_bytes:], marker[0], run_length)
    char = marker[0]
    room = max(0, budget - len(before.encode("utf-8")) - len(info.encode("utf-8")) - 8)  # 3 + 1 + 3 + 1
    shows = _line_head(content, room).rstrip("\n")
    run = _longest_run(shows, char)
    if run >= 3:  # a prefix whose run, and so whose fence, is no longer (as C2042's recut)
        shows = _line_head(content, max(0, room - 2 * (run + 1 - 3))).rstrip("\n")
    short = char * max(3, _longest_run(shows, char) + 1)
    shown = min(len(raw), opener_bytes + len(shows.encode("utf-8")))
    return f"{before}{short}{info}\n{shows}", short, shown


def _until_close(content: bytes, char: str, run: int) -> bytes:
    """``content`` up to the start of its block's own closing line: the first fence of ``char`` at least
    ``run`` long with nothing after it, :func:`_open_fence`'s rule; all of it when no line closes the block
    (B13 round 43 C2451: the close inside the room left the short fence no content)."""
    pos = 0
    for line in content.split(b"\n"):
        text = line.decode("utf-8", errors="ignore")
        m = _FENCE_RE.match(text)
        if m and m.group(1)[0] == char and len(m.group(1)) >= run and not text.strip()[len(m.group(1)) :]:
            return content[:pos]
        pos += len(line) + 1
    return content


def _fence_opener(text: str) -> tuple[int, str]:
    """``(start, marker)`` of the fence opener still open at the end of ``text``: its line's first character
    and its marker; ``(0, "```")`` when none is (the caller asks only when :func:`_open_fence` found one)."""
    found = (0, "```")
    open_marker: str | None = None
    pos = 0
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        marker = m.group(1) if m else ""
        if marker and open_marker is None:
            open_marker, found = marker, (pos, marker)
        elif marker and open_marker and marker[0] == open_marker[0] and len(marker) >= len(open_marker):
            open_marker = None if not line.strip()[len(marker) :] else open_marker
        pos += len(line) + 1
    return found


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
    """Serialise rows as RFC 4180 CSV with LF line ends (deterministic).

    Python 3.11's QUOTE_MINIMAL quotes only the characters of the line terminator, so a field holding a lone
    CR went out bare and a reader split it into two records.  A row with a CR in a field goes through a
    QUOTE_ALL writer, every other row's bytes unchanged, so each row is one record on every Python (B13 round
    42 C2302)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    quoted = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_ALL)
    for row in rows:
        (quoted if any("\r" in field for field in row) else writer).writerow(row)
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
