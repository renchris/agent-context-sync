"""Converter: pypdfium2 (PDFium) text extraction, pdfminer.six fallback: one WHOLE unit with page anchors
(owner: convert).

C15 section 6: the design's permissive PDF route is PDFium (BSD-3-Clause/Apache-2.0, via pypdfium2); the
pdfminer family is the route the design's receipts graded C.  PDFium extracts each page's text layer in
reading order (``FPDFText_GetText`` over the whole page).  pdfminer.six (MIT) is kept only as the fallback
for a PDF PDFium cannot load for a reason other than encryption (and for an install where pypdfium2 cannot
be imported); the summary line says when it was used.  Neither engine has a table model: tables come out as
lines of cell text.

Comments are kept (emitter 2.1.0).  A reviewer's notes, replies, text boxes and markup are annotations, which
sit outside the text layer, so a commented copy used to convert to the same text as the original.  They are
read from the page PDFium already has open and follow that page's text under ``[comments on this page (PDF
annotations):]``, one line per comment.  An annotation that carries no text and marks none is skipped, and so
is one no viewer shows (the Hidden or NoView flag).  The pdfminer fallback reads none, and a page whose
comments PDFium cannot read keeps its text; the summary says so in both cases.  What one file's comments may
cost is bounded (``_CommentBudget``).
"""

from __future__ import annotations

import ctypes
import logging
import math
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _dist_version,
    _escape_line,
    _escape_plain,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "2.1.0"  # 2.1.0: reviewer comments (annotations) follow each page's text
_SCANNED_MIN_CHARS = 20
_SCANNED = "[scanned page: no text layer]"
_ENGINE = "pypdfium2:text-range"
# Reasons of the typed errors (the stub page's ``reason``); C15 section 9 item 25 names the quarantine code.
_ENCRYPTED_PDF = "encrypted-pdf"
_NO_TEXT = "no text layer (scanned or image-only PDF; OCR not run)"
# PDFium document-load error codes (fpdfview.h): FPDF_ERR_PASSWORD, FPDF_ERR_SECURITY.
_ERR_PASSWORD = 4
_ERR_SECURITY = 5
# C0 controls PDFium can emit for generated hyphens/placeholders (\x02, \x00...); \t \n \r \f are kept.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f￾￿]")

_COMMENTS_HEAD = "[comments on this page (PDF annotations):]"
# Annotation subtypes that are comments (fpdf_annot.h, FPDF_ANNOT_*), with the word a reader knows each by.
# Every other subtype (links, popups, form fields, media, watermarks, redactions...) is skipped.
_NOTE = "Note"
_COMMENT_KINDS: dict[int, str] = {
    1: _NOTE,  # TEXT
    3: "Text box",  # FREETEXT
    4: "Line",
    5: "Box",  # SQUARE
    6: "Circle",
    7: "Polygon",
    8: "Polyline",
    9: "Highlight",
    10: "Underline",
    11: "Squiggly underline",
    12: "Strikethrough",  # STRIKEOUT
    13: "Stamp",
    14: "Insert",  # CARET
    15: "Drawing",  # INK
    17: "Attachment",  # FILEATTACHMENT
}
_TEXT_MARKUP = frozenset({9, 10, 11, 12})  # drawn over page text: that text is quoted
# FPDF_ANNOT_FLAG_HIDDEN | FPDF_ANNOT_FLAG_NOVIEW: no viewer shows the annotation on screen, so a page that
# listed it would present text no reviewer saw as a colleague's comment.
_UNSEEN_FLAGS = (1 << 1) | (1 << 5)
_QUOTE_MAX_CHARS = 300
_MAX_INDENT = 4  # reply levels drawn; a deeper reply keeps this indent
# Characters one file's comments may cost (``_CommentBudget``).  1,000 pages of 4,000 characters with every
# line highlighted once cost about 8 million; a file made to cost more stops here after about 2 s (measured).
_COMMENT_CHARS_MAX = 10_000_000
# A comment is one output line, so every run of whitespace becomes one space.  NEL (U+0085) and the Unicode
# line and paragraph separators are spelled out, although ``\s`` covers them, because PDFium returns them
# intact from /Contents and ``str.splitlines`` breaks a line on each.
_WS_RE = re.compile(r"[\s\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]+")

_Box = tuple[float, float, float, float]  # left, bottom, right, top in PDF space (y grows upward)

# Fixed layout-analysis parameters of the pdfminer fallback (its defaults pinned explicitly, plus all_texts
# so text inside Form XObjects/figures is not lost).  Any change here changes output, so it is in options().
_LAPARAMS: dict[str, float | bool] = {
    "line_overlap": 0.5,
    "char_margin": 2.0,
    "line_margin": 0.5,
    "word_margin": 0.1,
    "boxes_flow": 0.5,
    "detect_vertical": False,
    "all_texts": True,
}


class _EngineUnavailableError(Exception):
    """PDFium cannot read this file (or is not importable) for a reason other than encryption."""


class _CommentLimitError(Exception):
    """This file's comments cost more than ``_COMMENT_CHARS_MAX`` characters; the page's are not read."""


def _clean(raw: str) -> str:
    """One page's text: LF line ends, form feeds to LF, stray controls dropped, NFC, markdown-neutralised."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = unicodedata.normalize("NFC", _CONTROL_RE.sub("", text))
    return _escape_plain(text)


def _pdfium_version() -> str | None:
    """``pypdfium2-<v>+pdfium-<build>`` as installed, or None when pypdfium2 cannot be imported."""
    try:
        import pypdfium2  # type: ignore[import-untyped]  # noqa: PLC0415 - heavy native import
    except Exception:  # an ImportError, or a broken native library raising at import
        return None
    return f"pypdfium2-{_dist_version('pypdfium2')}+pdfium-{pypdfium2.PDFIUM_INFO}"


def _pdfium_pages(src: Path, name: str) -> tuple[list[str], dict[int, list[_Comment]], int]:
    """Raw text of every page via PDFium, the comments of each page that has any (by page index), and how
    many pages' comments could not be read; raises UnreadableSourceError when encrypted, else
    _EngineUnavailableError when PDFium cannot load the file.  ``name`` only labels the log line for those
    pages."""
    try:
        import pypdfium2  # noqa: PLC0415 - heavy native import
        import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]  # noqa: PLC0415
    except Exception as exc:
        raise _EngineUnavailableError(f"pypdfium2 unavailable: {type(exc).__name__}: {exc}") from exc
    try:
        doc = pypdfium2.PdfDocument(str(src))
    except pypdfium2.PdfiumError as exc:
        code = getattr(exc, "err_code", None)
        if code in (_ERR_PASSWORD, _ERR_SECURITY):
            raise UnreadableSourceError(f"{_ENCRYPTED_PDF} (password-protected)") from exc
        raise _EngineUnavailableError(f"PDFium cannot load the PDF: {exc}") from exc
    try:
        # A security handler without a user password still means /Encrypt in the trailer (C15 section 9 25):
        # the file is refused, not read, whatever the permissions allow.
        if int(pdfium_c.FPDF_GetSecurityHandlerRevision(doc.raw)) != -1:
            raise UnreadableSourceError(f"{_ENCRYPTED_PDF} (/Encrypt in the trailer)")
        pages: list[str] = []
        comments: dict[int, list[_Comment]] = {}
        unread, first_error = 0, ""  # pages whose comments could not be read
        budget = _CommentBudget()
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    pages.append(str(textpage.get_text_range()))
                    # Comments are read here, on the page and text page already open.  A failure (or a
                    # file past its allowance) costs this page its comments only: it must not reach the
                    # handler below, which would hand a file PDFium reads to the fallback.
                    try:
                        found = _page_comments(pdfium_c, page, textpage, budget)
                    except Exception as exc:
                        unread += 1
                        first_error = first_error or f"page {index + 1}: {type(exc).__name__}: {exc}"
                        found = []
                    if found:
                        comments[index] = found
                finally:
                    textpage.close()
            finally:
                page.close()
        if unread:  # one line per file: a build that cannot read annotations would log every page
            log.warning("%s: comments not read on %d page(s), first on %s", name, unread, first_error)
        return pages, comments, unread
    except pypdfium2.PdfiumError as exc:
        raise _EngineUnavailableError(f"PDFium failed on a page: {exc}") from exc
    finally:
        doc.close()


def _texts(layout: Any) -> Iterator[str]:
    """Text of every text box / line in layout order, descending into figures (pdfminer fallback)."""
    from pdfminer.layout import LTFigure, LTTextBox, LTTextLine  # noqa: PLC0415

    for obj in layout:
        if isinstance(obj, LTTextBox | LTTextLine):
            yield str(obj.get_text())
        elif isinstance(obj, LTFigure):
            yield from _texts(obj)


def _pdfminer_pages(src: Path) -> list[str]:
    """Raw text of every page via pdfminer.six (the fallback engine); typed errors as ``convert``."""
    from pdfminer.high_level import extract_pages  # noqa: PLC0415 - heavy import
    from pdfminer.layout import LAParams  # noqa: PLC0415
    from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect  # noqa: PLC0415

    laparams = LAParams(**_LAPARAMS)  # type: ignore[arg-type]
    pages: list[str] = []
    try:
        for layout in extract_pages(src, laparams=laparams):
            pages.append("\n".join(t.rstrip("\n") for t in _texts(layout)))
    except (PDFPasswordIncorrect, PDFEncryptionError) as exc:
        raise UnreadableSourceError(f"{_ENCRYPTED_PDF} (password-protected)") from exc
    except (RecursionError, MemoryError) as exc:
        raise ConversionError(f"pdfminer gave up: {type(exc).__name__}") from exc
    except Exception as exc:  # PDFSyntaxError, PSEOF, struct/zlib errors from damaged streams
        log.debug("pdfminer failed on %s", src.name, exc_info=True)
        raise ConversionError(f"pdfminer cannot read the PDF: {type(exc).__name__}: {exc}") from exc
    return pages


@dataclass(frozen=True, slots=True)
class _Comment:
    """One comment annotation of a page.  ``author``, ``text`` and ``quote`` are one line each, as shown:
    ``quote`` (the marked text) is already cut, and ``text`` is empty when it only repeated it.  ``index``
    and ``parent`` (the annotation it answers, /IRT) are positions in the page's annotation list."""

    index: int
    kind: str
    author: str
    text: str
    quote: str
    top: float
    left: float
    parent: int | None


def _one_line(text: str) -> str:
    """NFC on one line: every whitespace run (line breaks, form feed, U+0085, U+2028, U+2029) becomes one
    space and stray controls are dropped."""
    flat = _CONTROL_RE.sub("", _WS_RE.sub(" ", text))
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", flat)).strip()


class _CommentBudget:
    """The characters one file's comments may still cost: each annotation string read, the characters of
    each page that has a text markup, and those at the height of each of its quads.

    A reviewed document stays far below ``_COMMENT_CHARS_MAX``.  A file made to be expensive does not, and
    it is converted in the agent's own process.  One string can be the /Contents of every note in a file:
    400 notes sharing 1 MB took 30 s and 1.9 GB.  A highlight can cover its whole page: 1,000 on a page of
    34,000 characters took 9 s.  The allowance is a count, not a clock, so a file converts the same way on
    every run.
    """

    def __init__(self) -> None:
        """A whole allowance, for one file."""
        self._left = _COMMENT_CHARS_MAX

    def spend(self, chars: int) -> None:
        """Take ``chars`` from the allowance before the work they pay for; _CommentLimitError once it is
        used up, and on every call after that."""
        self._left -= chars
        if self._left < 0:
            raise _CommentLimitError(f"comments cost more than {_COMMENT_CHARS_MAX} characters")


def _annot_string(pdfium_c: Any, annot: Any, key: bytes, budget: _CommentBudget) -> str:
    """A text entry of an annotation dictionary (``Contents``, ``T``); "" when absent or empty."""
    size = int(pdfium_c.FPDFAnnot_GetStringValue(annot, key, None, 0))  # bytes of UTF-16LE, NUL included
    if size <= 2:
        return ""
    budget.spend(size // 2)  # before the copy
    buf = ctypes.create_string_buffer(size)
    pdfium_c.FPDFAnnot_GetStringValue(annot, key, ctypes.cast(buf, ctypes.POINTER(pdfium_c.FPDF_WCHAR)), size)
    return buf.raw[: size - 2].decode("utf-16-le", errors="replace")


class _PageChars:
    """Where each character of one text page sits, read once and only for a page with a text markup.

    A character belongs to a marked area when the centre of its loose box (the font's full line height) is
    inside it.  PDFium's own bounded read takes every character whose tight box touches the area, which
    pulls in the lines above and below on single-spaced text; a band through the middle of the area drops
    commas, periods and underscores instead.
    """

    def __init__(self, pdfium_c: Any, textpage: Any, budget: _CommentBudget) -> None:
        """Bind the open text page and the file's allowance; nothing is read yet."""
        self._pdfium_c = pdfium_c
        self._textpage = textpage
        self._budget = budget
        self._centres: list[tuple[float, float, int]] | None = None  # (y, x, character), sorted
        self._ys: list[float] = []

    def text_in(self, boxes: Sequence[_Box]) -> str:
        """The page text whose characters are centred inside one of ``boxes``, in text order, on one line;
        _CommentLimitError when the file's allowance does not cover the characters to look at."""
        if self._centres is None:
            count = int(self._textpage.count_chars())
            self._budget.spend(count)
            rect = self._pdfium_c.FS_RECTF()
            centres: list[tuple[float, float, int]] = []
            for i in range(count):
                if self._pdfium_c.FPDFText_GetLooseCharBox(self._textpage, i, ctypes.byref(rect)):
                    at = ((rect.bottom + rect.top) / 2, (rect.left + rect.right) / 2, i)
                    if math.isfinite(at[0]) and math.isfinite(at[1]):
                        centres.append(at)
            # Sorted by height, so a marked line costs its own characters, not the page's: a heavily
            # highlighted page would otherwise test every character against every highlight.
            centres.sort()
            self._centres = centres
            self._ys = [at[0] for at in centres]
        marked: set[int] = set()
        for left, bottom, right, top in boxes:
            low, high = bisect_left(self._ys, bottom), bisect_right(self._ys, top)
            self._budget.spend(high - low)  # a quad as tall as the page does cost the page
            band = self._centres[low:high]
            marked.update(i for y, x, i in band if left <= x <= right and bottom <= y <= top)
        runs: list[list[int]] = []  # [first character, count] of each unbroken run of marked characters
        for i in sorted(marked):
            if runs and runs[-1][0] + runs[-1][1] == i:
                runs[-1][1] += 1
            else:
                runs.append([i, 1])
        return _one_line(" ".join(str(self._textpage.get_text_range(first, count)) for first, count in runs))


def _marked_text(pdfium_c: Any, annot: Any, chars: _PageChars, rect: _Box) -> str:
    """The page text under a highlight, underline, squiggly or strikethrough (its quads, else its
    rectangle)."""
    boxes: list[_Box] = []
    for q in range(int(pdfium_c.FPDFAnnot_CountAttachmentPoints(annot))):
        quad = pdfium_c.FS_QUADPOINTSF()
        if pdfium_c.FPDFAnnot_GetAttachmentPoints(annot, q, ctypes.byref(quad)):
            xs = (quad.x1, quad.x2, quad.x3, quad.x4)
            ys = (quad.y1, quad.y2, quad.y3, quad.y4)
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return chars.text_in(boxes or [rect])


def _read_comment(
    pdfium_c: Any, page: Any, annot: Any, *, index: int, chars: _PageChars, budget: _CommentBudget
) -> _Comment | None:
    """One open annotation as a comment; None when it is not one, is not shown, or says and marks nothing."""
    subtype = int(pdfium_c.FPDFAnnot_GetSubtype(annot))
    kind = _COMMENT_KINDS.get(subtype)
    if kind is None or int(pdfium_c.FPDFAnnot_GetFlags(annot)) & _UNSEEN_FLAGS:
        return None
    rect = pdfium_c.FS_RECTF()
    box: _Box = (0.0, 0.0, 0.0, 0.0)
    if pdfium_c.FPDFAnnot_GetRect(annot, ctypes.byref(rect)):
        xs, ys = (rect.left, rect.right), (rect.bottom, rect.top)
        box = (min(xs), min(ys), max(xs), max(ys))
    text = _one_line(_annot_string(pdfium_c, annot, b"Contents", budget))
    quote = _marked_text(pdfium_c, annot, chars, box) if subtype in _TEXT_MARKUP else ""
    if text == quote:
        text = ""  # several tools copy the marked text into /Contents: it is said once, as the quote
    if not text and not quote:
        return None  # a bare drawing, stamp or empty note: nothing a reader could use
    # Cut here, not where the line is written: a comment is held until the whole file is read, and a
    # markup can cover its page.
    if len(quote) > _QUOTE_MAX_CHARS:
        quote = quote[:_QUOTE_MAX_CHARS].rstrip() + "…"
    parent: int | None = None
    linked = pdfium_c.FPDFAnnot_GetLinkedAnnot(annot, b"IRT")
    if linked:
        try:
            found = int(pdfium_c.FPDFPage_GetAnnotIndex(page.raw, linked))
        finally:
            pdfium_c.FPDFPage_CloseAnnot(linked)
        parent = found if found >= 0 else None
    return _Comment(
        index=index,
        kind=kind,
        author=_one_line(_annot_string(pdfium_c, annot, b"T", budget)),
        text=text,
        quote=quote,
        top=box[3],
        left=box[0],
        parent=parent,
    )


def _page_comments(pdfium_c: Any, page: Any, textpage: Any, budget: _CommentBudget) -> list[_Comment]:
    """The comments among one open page's annotations, in file order (``textpage`` is that page's);
    _CommentLimitError when the file's allowance runs out on this page."""
    chars = _PageChars(pdfium_c, textpage, budget)
    out: list[_Comment] = []
    for index in range(int(pdfium_c.FPDFPage_GetAnnotCount(page.raw))):
        annot = pdfium_c.FPDFPage_GetAnnot(page.raw, index)
        if not annot:
            continue
        try:
            comment = _read_comment(pdfium_c, page, annot, index=index, chars=chars, budget=budget)
        finally:
            pdfium_c.FPDFPage_CloseAnnot(annot)
        if comment is not None:
            out.append(comment)
    return out


def _comment_line(c: _Comment, depth: int) -> str:
    """One comment as one list item: ``<kind> by <author> on “<marked text>”: <text>``."""
    # Only a note under another comment is a reply.  A markup grouped with its parent (the strikethrough of
    # a replace-text pair, /IRT with /RT /Group) keeps its own word, or the reader cannot tell what it did.
    label = "reply" if depth and c.kind == _NOTE else c.kind
    if c.author:
        label += f" by {c.author}"
    if c.quote:
        label += f" on “{c.quote}”"
    line = f"{label}: {c.text}" if c.text else label
    return "  " * min(depth, _MAX_INDENT) + "- " + _escape_line(line)


def _render_comments(comments: Sequence[_Comment]) -> list[str]:
    """Markdown list of one page's comments, one item each: top to bottom then left to right in PDF space
    (a rotated page is not turned), each one's answers nested under it in file order."""
    by_index = {c.index: c for c in comments}
    answers: dict[int, list[_Comment]] = {}
    roots: list[_Comment] = []
    for c in comments:
        if c.parent is not None and c.parent != c.index and c.parent in by_index:
            answers.setdefault(c.parent, []).append(c)
        else:
            roots.append(c)  # no parent, or one that is not a comment here (skipped, or on no page)
    roots.sort(key=lambda c: (-round(c.top, 1), round(c.left, 1), c.index))
    out: list[str] = []
    emitted: set[int] = set()
    # An explicit stack: a damaged file can chain replies deeper than the interpreter's recursion limit.
    # After the roots come the comments no root reaches (an /IRT cycle), in file order, so none is lost.
    for start in (*roots, *comments):
        stack = [(start, 0)]
        while stack:
            c, depth = stack.pop()
            if c.index in emitted:
                continue
            emitted.add(c.index)
            out.append(_comment_line(c, depth))
            stack.extend((answer, depth + 1) for answer in reversed(answers.get(c.index, ())))
    return out


class PdfConverter:
    """PDFium (pypdfium2) text extraction, pdfminer.six fallback: one WHOLE unit with page anchors.

    ``<!-- page: N -->`` anchor per page; a page with < 20 chars of text is marked ``[scanned page: no text
    layer]`` (OCR is a later budgeted tier).  Encrypted PDFs (a password is needed, or ``/Encrypt`` in the
    trailer) raise UnreadableSourceError(``encrypted-pdf ...``); a PDF with no text and no comment on any
    page raises UnreadableSourceError (C15: an empty conversion is a stub, never an empty page).  A page's
    comments follow its text; a page with comments and no text is still a page.  pdfminer.six is used
    only when PDFium cannot load a file for another reason (the summary says so, and that it read no
    comments).
    """

    converter_id = "pdf-pypdfium2"
    extensions: tuple[str, ...] = (".pdf",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter + PDFium/pypdfium2 + the pdfminer.six fallback)."""
        pdfium = _pdfium_version() or "pypdfium2-unavailable"
        return f"{_EMITTER_VERSION}+{pdfium}+pdfminer.six-{_dist_version('pdfminer.six')}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        opts: dict[str, OptionValue] = {"engine": _ENGINE, "fallback": "pdfminer.six"}
        opts.update({f"fallback.laparams.{k}": v for k, v in _LAPARAMS.items()})
        opts["max_page_bytes"] = self._cfg.max_page_bytes
        opts["scanned_min_chars"] = _SCANNED_MIN_CHARS
        return opts

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        with src.open("rb") as fh:
            if b"%PDF-" not in fh.read(1024):
                raise ConversionError("not a PDF (no %PDF- header)")
        engine = "pdfium"
        found: dict[int, list[_Comment]] = {}
        unread = 0
        try:
            raw_pages, found, unread = _pdfium_pages(src, name)
        except _EngineUnavailableError as exc:
            log.info("%s: %s; falling back to pdfminer.six", name, exc)
            engine = "pdfminer"
            raw_pages = _pdfminer_pages(src)
        pages = [_clean(p) for p in raw_pages]
        if not pages:
            raise UnreadableSourceError("empty PDF: no pages")
        # Rendered before the refusal below: a page counts as commented only when a line comes out.
        comments = {i: lines for i in sorted(found) if (lines := _render_comments(found[i]))}
        if not comments and not any("".join(p.split()) for p in pages):
            raise UnreadableSourceError(_NO_TEXT)
        out: list[str] = []
        scanned = 0
        for n, text in enumerate(pages, start=1):
            out += [f"<!-- page: {n} -->", ""]
            if len("".join(text.split())) < _SCANNED_MIN_CHARS:
                scanned += 1
                out += [_SCANNED if not text.strip() else f"{text}\n\n{_SCANNED}", ""]
            else:
                out += [text, ""]
            if n - 1 in comments:
                out += [_COMMENTS_HEAD, *comments[n - 1], ""]
        body = "\n".join(out)
        first = next((ln.strip() for p in pages for ln in p.split("\n") if len(ln.strip()) >= 3), "")
        title = first.lstrip("\\")[:120] if first else "Untitled PDF"
        summary = f"PDF: {len(pages)} page(s)"
        if scanned:
            summary += f", {scanned} without a text layer (scanned; OCR not run)"
        if comments:
            # One line is one comment, so this counts the comments emitted, not lines of their text.
            summary += f"; {sum(map(len, comments.values()))} comment(s) on {len(comments)} page(s)"
        if unread:
            # Without this a page whose comments failed reads as a page nobody commented on, and the
            # count above as the whole of them.
            summary += f"; comments not read on {unread} page(s)"
        if engine == "pdfminer":
            summary += "; text by the pdfminer.six fallback (PDFium could not load it); comments not read"
        body, sidecars = _cap_body(body, self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR)
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title=title,
                summary=summary,
                body=body,
                sidecars=sidecars,
            ),
        )
