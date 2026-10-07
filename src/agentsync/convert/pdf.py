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
is one no viewer draws (the Hidden or NoView flag), except a review status ("Accepted", "Completed"), which a
viewer lists under the comment it is about.  The pdfminer fallback reads none, and a page whose comments
PDFium cannot read keeps its text; the summary says so in both cases.  What one file's comments may cost is
bounded (``_CommentBudget``).

With an OCR engine (``PdfConverter(cfg, ocr=engine)``; ``convert/ocr.py``) the converter also reads what the
text layer lacks.  A page with under 20 characters of text is rendered and read, and so is a picture on a
page with text, unless the text layer already covers it.  Without an engine nothing here runs: the version,
the options and every page are the ones from before OCR existed.  The same holds for a file the pdfminer
fallback converts.  A failure of the helper is ``OcrError`` with fixed wording, raised before anything is
written: ``convert_file`` then converts the file with the registry's converter that has no engine, so no page
ever holds a half-read document or what a helper said.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import logging
import math
import re
import struct
import tempfile
import time
import unicodedata
import zlib
from bisect import bisect_left, bisect_right
from collections.abc import Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _dist_version,
    _emitter,
    _escape_line,
    _escape_plain,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.image import (
    _DOCUMENT_BUDGET_S,
    _ENGINE_LABEL,
    _OCR_OPTIONS,
    _PICTURES_CUT,
    _PICTURES_READ,
    _ocr_lines,
    _read_pictures,
    _read_without_ocr,
)
from agentsync.convert.image import _FAILED as _OCR_FAILED
from agentsync.convert.ocr import _MIN_PX, MAX_MEGAPIXELS, MAX_PAGES, OcrEngine, OcrError
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "2.1.0"  # 2.1.0: reviewer comments (annotations) follow each page's text
_REREAD_BELOW = "2.1.0"
"""The comments floor.  A PDF whose page an emitter below this wrote is read again once, so that a file
mirrored before comments were kept gains them without its bytes changing (``PdfConverter.outdated``).  Raise
it only for a change worth reading every mirrored PDF again for, and never above ``_EMITTER_VERSION``: a
page at or above the running emitter is never outdated, so such a floor would do nothing."""
_SCANNED_MIN_CHARS = 20
_SCANNED = "[scanned page: no text layer]"
_ENGINE = "pypdfium2:text-range"
# Reasons of the typed errors (the stub page's ``reason``); C15 section 9 item 25 names the quarantine code.
_ENCRYPTED_PDF = "encrypted-pdf"
_NO_TEXT = "no text layer (scanned or image-only PDF; OCR not run)"
# The same file once OCR has read its pages and found nothing: only a converter with an engine says so.
_NO_TEXT_FOUND = "no text layer (scanned or image-only PDF; on-device OCR found no text)"
_NO_TEXT_PAST_LIMIT = (  # {}: the page limit
    "no text layer (scanned or image-only PDF; OCR found no text on the first {} pages, "
    "the rest are over the OCR page limit)"
)
# What a page says about on-device OCR.  Fixed wording: no file name, no path and nothing a helper said.
_OCR_READ = f"[page image without a text layer: text read by on-device OCR ({_ENGINE_LABEL})]"
_OCR_NO_TEXT = "[scanned page: no text layer; OCR found no text]"
_OCR_OVER_LIMIT = "[scanned page: no text layer; over the OCR page limit]"
_OCR_PICTURE = f"[text in an image on this page, read by on-device OCR ({_ENGINE_LABEL}):]"
# How a page under _SCANNED_MIN_CHARS came out: its marker, and the clause the summary counts it under, in
# the summary's order.  Without an engine only the last one occurs.
_SCANNED_OUTCOMES: dict[str, str] = {
    _OCR_READ: "read by on-device OCR",
    _OCR_NO_TEXT: "without a text layer (OCR found no text)",
    _OCR_OVER_LIMIT: "without a text layer (over the OCR page limit)",
    _SCANNED: "without a text layer (scanned; OCR not run)",
}
_RENDER_DPI = 300
_RENDER_MAX_PX = 6000  # the longer side of a page image: a poster is read below 300 dpi
_PAGES_PER_RUN = 4  # page images written, read and removed together
_FORM_DEPTH = 4  # levels of page objects looked at: the page's own and three of nested form XObjects
_PICTURE_MIN_PT = 1.0  # a picture drawn narrower or lower than this has no size on the page
_PICTURE_INSET_PT = 2.0  # text that only touches a picture's edge (a caption) is not text in it
_MAX_PICTURES_SEEN = 4 * MAX_PAGES  # image objects of one file that are looked at
_MAX_PICTURE_PIXELS = 8 * MAX_MEGAPIXELS * 1_000_000  # pixels of one file's pictures decoded here
_OCR_RULES = 1
"""Bumped when a rule here that decides what OCR reads, or how a page shows it, changes without one of the
numbers below changing.  It is in the options only with an engine, so it moves no key of a Mac without one
(the emitter version would)."""
_PDF_OCR_OPTIONS: dict[str, OptionValue] = {
    "ocr_pdf_rules": _OCR_RULES,
    "ocr_page_dpi": _RENDER_DPI,
    "ocr_page_max_px": _RENDER_MAX_PX,
    "ocr_pictures_seen": _MAX_PICTURES_SEEN,
    "ocr_picture_pixels": _MAX_PICTURE_PIXELS,
}
_clock = time.monotonic
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
# FPDF_ANNOT_FLAG_HIDDEN | FPDF_ANNOT_FLAG_NOVIEW: no viewer draws the annotation, so a page that listed its
# text would present words no reviewer saw as a colleague's comment.
_UNSEEN_FLAGS = (1 << 1) | (1 << 5)
# The one exception: a review status.  The status a reviewer sets on a comment is a note that answers it,
# with /StateModel and /State (ISO 32000-1, 12.5.6.3); a viewer lists it under that comment and draws nothing,
# so it is written with the Hidden flag.  Its states by model are below.  Only its author and one of these
# words are taken from it: the /Contents of a hidden annotation is never shown.
_STATUS = "status"
_REVIEW_STATES: dict[str, frozenset[str]] = {
    "Marked": frozenset({"Marked", "Unmarked"}),
    "Review": frozenset({"Accepted", "Rejected", "Cancelled", "Completed", "None"}),
}
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


def _review_state(pdfium_c: Any, annot: Any, budget: _CommentBudget) -> str:
    """The state a review-status note sets, a word of ``_REVIEW_STATES``; "" when it sets none."""
    model = _annot_string(pdfium_c, annot, b"StateModel", budget)
    state = _annot_string(pdfium_c, annot, b"State", budget)
    return state if state in _REVIEW_STATES.get(model, ()) else ""


def _read_comment(
    pdfium_c: Any, page: Any, annot: Any, *, index: int, chars: _PageChars, budget: _CommentBudget
) -> _Comment | None:
    """One open annotation as a comment; None when it is not one, is not drawn (a review status apart), or
    says and marks nothing."""
    subtype = int(pdfium_c.FPDFAnnot_GetSubtype(annot))
    kind = _COMMENT_KINDS.get(subtype)
    if kind is None:
        return None
    rect = pdfium_c.FS_RECTF()
    box: _Box = (0.0, 0.0, 0.0, 0.0)
    if pdfium_c.FPDFAnnot_GetRect(annot, ctypes.byref(rect)):
        xs, ys = (rect.left, rect.right), (rect.bottom, rect.top)
        box = (min(xs), min(ys), max(xs), max(ys))
    parent: int | None = None
    linked = pdfium_c.FPDFAnnot_GetLinkedAnnot(annot, b"IRT")
    if linked:
        try:
            found = int(pdfium_c.FPDFPage_GetAnnotIndex(page.raw, linked))
        finally:
            pdfium_c.FPDFPage_CloseAnnot(linked)
        parent = found if found >= 0 else None
    if int(pdfium_c.FPDFAnnot_GetFlags(annot)) & _UNSEEN_FLAGS:
        # No viewer draws it.  Only a review status is kept (``_REVIEW_STATES``), as the state it sets.
        if kind != _NOTE or parent is None:
            return None
        kind, text, quote = _STATUS, _review_state(pdfium_c, annot, budget), ""
    else:
        text = _one_line(_annot_string(pdfium_c, annot, b"Contents", budget))
        quote = _marked_text(pdfium_c, annot, chars, box) if subtype in _TEXT_MARKUP else ""
        if text == quote:
            text = ""  # several tools copy the marked text into /Contents: it is said once, as the quote
    if not text and not quote:
        return None  # a bare drawing, stamp or empty note, or a hidden note that sets no state
    # Cut here, not where the line is written: a comment is held until the whole file is read, and a
    # markup can cover its page.
    if len(quote) > _QUOTE_MAX_CHARS:
        quote = quote[:_QUOTE_MAX_CHARS].rstrip() + "…"
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
    # So does a review status.
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


# ---------------------------------------------------------------------------------------------------------
# on-device OCR (only with an engine)
# ---------------------------------------------------------------------------------------------------------


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def _png(bitmap: Any) -> bytes:
    """A PDFium bitmap as an 8-bit PNG, gray or RGB; a fourth byte per pixel (alpha, padding) is dropped.

    Written here, a row at a time, and not through an imaging library: the pixels are never copied whole,
    and the file holds nothing but them (no time, no text chunk), so the same pixels give the same bytes."""
    count, width, height, stride = bitmap.n_channels, bitmap.width, bitmap.height, bitmap.stride
    if count not in (1, 3, 4) or width < 1 or height < 1:
        raise ValueError("not a bitmap this writes")
    view = memoryview(bitmap.buffer).cast("B")
    red, blue = (0, 2) if bitmap.rev_byteorder else (2, 0)
    packer = zlib.compressobj(1)
    parts: list[bytes] = []
    row = bytearray(width * 3)
    for at in range(0, height * stride, stride):
        parts.append(packer.compress(b"\x00"))  # the row's filter type: none
        if count == 1:
            parts.append(packer.compress(view[at : at + width]))
            continue
        end = at + width * count
        row[0::3] = view[at + red : end : count]
        row[1::3] = view[at + 1 : end : count]
        row[2::3] = view[at + blue : end : count]
        parts.append(packer.compress(row))
    parts.append(packer.flush())
    head = struct.pack(">IIBBBBB", width, height, 8, 0 if count == 1 else 2, 0, 0, 0)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", head),
            _png_chunk(b"IDAT", b"".join(parts)),
            _png_chunk(b"IEND", b""),
        )
    )


def _render_page(doc: Any, index: int, path: Path) -> bool:
    """Write page ``index`` of the open document to ``path`` as a PNG, as a viewer shows it (its /Rotate
    applied) but without its annotations, which the comments block lists.  300 dpi, less for a page whose
    longer side would pass ``_RENDER_MAX_PX``.  False when PDFium cannot render the page: that is a fact
    about the file, and the page then stays the scanned page it is without OCR."""
    try:
        page = doc[index]
        try:
            longest = max(page.get_size())
            if not (math.isfinite(longest) and longest > 0):
                return False
            scale = min(_RENDER_DPI / 72, _RENDER_MAX_PX / longest)
            bitmap = page.render(scale=scale, rev_byteorder=True, draw_annots=False, may_draw_forms=False)
            try:
                data = _png(bitmap)
            finally:
                bitmap.close()
        finally:
            page.close()
    except MemoryError:  # not a fact about the file: the whole OCR pass fails, and the file is read again
        raise
    except Exception as exc:
        log.debug("page %d not rendered for OCR: %s", index + 1, type(exc).__name__)
        return False
    path.write_bytes(data)
    return True


def _page_box(obj: Any) -> _Box | None:
    """Where a picture sits on its page, in PDF space; None for one drawn with no width or no height (a
    collapsed or hidden placement: no viewer shows it).  A picture inside a form XObject is placed by the
    form's matrix, and by that of each form around it."""
    box: _Box = obj.get_bounds()
    form = obj.container
    while form is not None:
        box = form.get_matrix().on_rect(*box)
        form = form.container
    left, bottom, right, top = box
    if not all(map(math.isfinite, box)) or min(right - left, top - bottom) < _PICTURE_MIN_PT:
        return None
    return box


def _covered(textpage: Any, box: _Box) -> bool:
    """True when the text layer already holds what is in ``box``: ``_SCANNED_MIN_CHARS`` characters or more
    lie inside it (a searchable scan, a picture behind the page's text).  Reading such a picture would say
    the page twice.  The box is drawn in a little first, because PDFium counts a character that only touches
    it."""
    left, bottom, right, top = box
    dx = min(_PICTURE_INSET_PT, (right - left) / 4)
    dy = min(_PICTURE_INSET_PT, (top - bottom) / 4)
    text = str(textpage.get_text_bounded(left + dx, bottom + dy, right - dx, top - dy))
    return len("".join(text.split())) >= _SCANNED_MIN_CHARS


class _PagePictures:
    """The pictures on a PDF's pages with text that may hold text the text layer lacks, as PNG streams for
    ``image._read_pictures``, in page order.

    A picture is offered when it passes the helper's own size rule (no side under 48 px, no more than
    ``MAX_MEGAPIXELS``; checked from its stored size, before a pixel is decoded), is drawn with a size, and
    is not covered by the text layer (``_covered``).  Its stored pixels are offered, not a rendering of the
    page: the same image object gives the same bytes wherever it is drawn, so a logo on every page is read
    once.  A picture PDFium cannot place or decode is skipped; nothing here can fail the document but the
    time limit and memory.

    At most ``_MAX_PICTURES_SEEN`` image objects are looked at and ``_MAX_PICTURE_PIXELS`` pixels decoded.
    Both are counts, so a file gives the same pictures on every run.  ``pages`` holds the page index of each
    picture offered so far, ``cut`` says one of the two limits stopped the looking, and ``done`` that every
    page was looked at.
    """

    def __init__(self, doc: Any, pdfium_c: Any, indexes: Sequence[int], deadline: float) -> None:
        """Bind the open document, the pages to look at and when the document's OCR time ends."""
        self._doc = doc
        self._image = pdfium_c.FPDF_PAGEOBJ_IMAGE
        self._indexes = indexes
        self._deadline = deadline
        self._seen = 0
        self._pixels = 0
        self.pages: list[int] = []
        self.cut = False
        self.done = False

    def _picture(self, obj: Any, textpage: Any) -> bytes | None:
        """One image object as PNG bytes, or None when it is not one to read."""
        if self._seen >= _MAX_PICTURES_SEEN:
            self.cut = True
            return None
        self._seen += 1
        width, height = obj.get_px_size()
        pixels = width * height
        if min(width, height) < _MIN_PX or pixels > MAX_MEGAPIXELS * 1_000_000:
            return None
        box = _page_box(obj)
        if box is None or _covered(textpage, box):
            return None
        if self._pixels + pixels > _MAX_PICTURE_PIXELS:
            self.cut = True
            return None
        self._pixels += pixels
        bitmap = obj.get_bitmap()  # the stored image: its matrix and mask are not applied
        try:
            return _png(bitmap)
        finally:
            bitmap.close()

    def _on_page(self, page: Any, textpage: Any) -> Iterator[bytes]:
        objects = iter(page.get_objects(filter=(self._image,), max_depth=_FORM_DEPTH))
        while not self.cut:
            try:
                obj = next(objects, None)
                if obj is None:
                    return
                data = self._picture(obj, textpage)
            except MemoryError:
                raise
            except Exception as exc:  # this picture is skipped (when the listing failed, the page's rest)
                log.debug("a picture was not read for OCR: %s", type(exc).__name__)
                continue
            if data is not None:
                yield data

    def streams(self) -> Generator[BinaryIO, None, None]:
        """The pictures, one PNG stream at a time; OcrError once the document's OCR time is over."""
        for index in self._indexes:
            if self.cut:
                return
            if _clock() >= self._deadline:
                raise OcrError("the document's OCR time ran out before its pictures were looked at")
            try:
                page = self._doc[index]
            except Exception:
                continue
            try:
                textpage = page.get_textpage()
            except Exception:
                page.close()
                continue
            try:
                for data in self._on_page(page, textpage):
                    self.pages.append(index)
                    yield io.BytesIO(data)
            finally:
                textpage.close()
                page.close()
        self.done = not self.cut


@dataclass(slots=True)
class _OcrText:
    """What on-device OCR read in one PDF, by page index.

    ``pages``: the escaped lines of each page without a text layer that was read; no lines when OCR found
    no text on it.  ``over_limit``: such pages past ``MAX_PAGES``, which were not read.  ``pictures``: for a
    page with a text layer, the lines of each picture first seen on it.  ``pictures_cut``: a limit stopped
    the reading of pictures, so the later ones were not read.
    """

    pages: dict[int, list[str]] = field(default_factory=dict)
    over_limit: frozenset[int] = frozenset()
    pictures: dict[int, list[list[str]]] = field(default_factory=dict)
    pictures_cut: bool = False


def _read_pages(
    doc: Any, engine: OcrEngine, wanted: Sequence[int], folder: Path, deadline: float
) -> dict[int, list[str]]:
    """The escaped lines OCR read on each of the pages ``wanted`` (no lines where it found no text).

    The pages are rendered into ``folder`` ``_PAGES_PER_RUN`` at a time, read and removed, so a long scan
    never has more than a few page images on disk.  A page PDFium cannot render gets no entry.  Raises
    OcrError when the helper fails, runs out of time, or reports a page image it cannot read: the image was
    written here, so that is the helper's failure and no fact about the file.  After the first failure the
    helper is not started for another page."""
    out: dict[int, list[str]] = {}
    for start in range(0, len(wanted), _PAGES_PER_RUN):
        rendered: list[tuple[int, Path]] = []
        for index in wanted[start : start + _PAGES_PER_RUN]:
            path = folder / f"page-{index + 1:05d}.png"
            if _render_page(doc, index, path):
                rendered.append((index, path))
        if not rendered:
            continue
        paths = [path for _index, path in rendered]
        answers = engine.read(paths, work_dir=folder, budget_s=deadline - _clock())
        for (index, path), frames in zip(rendered, answers, strict=True):
            if frames[0].error:
                raise OcrError(f"the OCR helper could not read a page image ({frames[0].error})")
            lines = _ocr_lines(frames[0])
            out[index] = lines if any(lines) else []
            path.unlink()
    return out


def _read_page_pictures(
    scan: _PagePictures, engine: OcrEngine, work_dir: Path, deadline: float
) -> tuple[dict[int, list[list[str]]], bool]:
    """(the lines of each picture ``scan`` offers, under the page it is first seen on; whether a limit left
    later pictures unread).  Raises OcrError when the helper left a picture unread: a page that kept the
    other pictures' text would look complete."""
    pictures: dict[int, list[list[str]]] = {}
    with contextlib.closing(scan.streams()) as streams:
        got = _read_pictures(engine, streams, work_dir=work_dir, budget_s=deadline - _clock())
        if got.unread:  # _read_pictures has logged how many, and the helper's reason
            raise OcrError("the OCR helper left pictures unread")
        first_seen: set[str] = set()
        for index, digest in zip(scan.pages, got.digests, strict=True):
            if digest is not None and digest not in first_seen and digest in got.lines:
                first_seen.add(digest)
                pictures.setdefault(index, []).append(got.lines[digest])
        # A limit of _read_pictures stops it asking.  One more is asked for, to know whether any was left.
        left = None if scan.done else next(streams, None)
        if left is not None:
            left.close()
    return pictures, scan.cut or left is not None or None in got.digests


def _ocr_text(src: Path, engine: OcrEngine, scanned: Sequence[int], texted: Sequence[int]) -> _OcrText:
    """Read what the text layer of ``src`` lacks: the pages ``scanned`` (those under ``_SCANNED_MIN_CHARS``
    characters; at most ``MAX_PAGES`` of them) and the pictures on the pages ``texted``.

    Every image is written into a folder made beside the staged file, so under the cycle's staging folder
    and never ``$TMPDIR``, and removed before this returns.  Pages and pictures share one time limit:
    ``_DOCUMENT_BUDGET_S`` seconds from this call, rendering included.

    Raises OcrError when the helper fails or the time runs out; an image that cannot be written or a
    document that cannot be opened again raises what it raises.  The caller treats every exception alike.
    """
    import pypdfium2  # noqa: PLC0415 - heavy native import
    import pypdfium2.raw as pdfium_c  # noqa: PLC0415

    deadline = _clock() + _DOCUMENT_BUDGET_S
    wanted = scanned[:MAX_PAGES]
    found = _OcrText(over_limit=frozenset(scanned[MAX_PAGES:]))
    doc = pypdfium2.PdfDocument(str(src))
    try:
        if wanted:
            with tempfile.TemporaryDirectory(
                dir=src.parent, prefix=".ocr-", ignore_cleanup_errors=True
            ) as tmp:
                found.pages = _read_pages(doc, engine, wanted, Path(tmp), deadline)
        if texted:
            scan = _PagePictures(doc, pdfium_c, texted, deadline)
            found.pictures, found.pictures_cut = _read_page_pictures(scan, engine, src.parent, deadline)
    finally:
        doc.close()
    return found


class PdfConverter:
    """PDFium (pypdfium2) text extraction, pdfminer.six fallback: one WHOLE unit with page anchors.

    ``<!-- page: N -->`` anchor per page; a page with < 20 chars of text is marked ``[scanned page: no text
    layer]``.  Encrypted PDFs (a password is needed, or ``/Encrypt`` in the trailer) raise
    UnreadableSourceError(``encrypted-pdf ...``); a PDF with no text and no comment on any page raises
    UnreadableSourceError (C15: an empty conversion is a stub, never an empty page).  A page's comments
    follow its text; a page with comments and no text is still a page.  pdfminer.six is used only when
    PDFium cannot load a file for another reason (the summary says so, and that it read no comments).

    With an OCR engine a page with < 20 chars of text is read by it (the page keeps its own short text and
    its comments), and so is each distinct picture on a page with text that the text layer does not cover.
    A PDF of page images is a page when OCR reads text in it.  When the engine fails, ``convert`` raises
    OcrError with fixed wording and ``convert_file`` converts the file without OCR.
    """

    converter_id = "pdf-pypdfium2"
    extensions: tuple[str, ...] = (".pdf",)

    def __init__(self, cfg: ConvertConfig, ocr: OcrEngine | None = None) -> None:
        """Bind converter options from config, and the cycle's OCR engine when there is one."""
        self._cfg = cfg
        self._ocr = ocr

    def version(self) -> str:
        """Version as run (emitter + PDFium/pypdfium2 + the pdfminer.six fallback); with an OCR engine its
        identity comes last.  Without one this is the version from before OCR existed."""
        pdfium = _pdfium_version() or "pypdfium2-unavailable"
        version = f"{_EMITTER_VERSION}+{pdfium}+pdfminer.six-{_dist_version('pdfminer.six')}"
        return version if self._ocr is None else f"{version}+{self._ocr.identity}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash; the OCR ones only with an engine."""
        opts: dict[str, OptionValue] = {"engine": _ENGINE, "fallback": "pdfminer.six"}
        opts.update({f"fallback.laparams.{k}": v for k, v in _LAPARAMS.items()})
        opts["max_page_bytes"] = self._cfg.max_page_bytes
        opts["scanned_min_chars"] = _SCANNED_MIN_CHARS
        if self._ocr is not None:
            opts.update(_OCR_OPTIONS)
            opts.update(_PDF_OCR_OPTIONS)
        return opts

    @property
    def outdated_key(self) -> str:
        """What ``outdated`` goes by besides the engine: the running emitter and the floor.  The cycle keeps
        it in what it remembers having looked for, so a new emitter or a new floor makes it look again."""
        return f"{_EMITTER_VERSION}<{_REREAD_BELOW}"

    def outdated(self, produced: str, reason: str | None = None) -> bool:
        """True when what this converter made of a PDF under version ``produced`` is worth reading the file
        again for.  ``reason`` is None for a page, else the reason of the stub the file got.

        Two things are: an emitter below ``_REREAD_BELOW`` (comments were not kept), and, with an engine, a
        version without one (OCR has not read the file).  Of the stubs only the ``no text layer (… OCR not
        run)`` one is asked about: OCR exists for that file, and a scan can carry comments.  The stub of a
        file OCR read and found nothing in has another reason, and is settled.

        Never when ``produced`` cannot be read as a version or names an emitter newer than the running one.
        A re-read writes under the running emitter, so for the floor the answer about what it wrote is
        always no.  It can still write a version without an engine (the engine failed on the file): the
        cycle remembers each file it has read again and does not ask about it twice."""
        if reason not in (None, _NO_TEXT):
            return False
        emitter, running = _emitter(produced), _emitter(_EMITTER_VERSION)
        if emitter is None or running is None or emitter > running:
            return False
        if self._ocr is not None and _read_without_ocr(produced):
            return True
        floor = _emitter(_REREAD_BELOW)
        return floor is not None and emitter < min(floor, running)

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors.

        With an OCR engine the helper works in a folder made beside ``src``, and OcrError (fixed wording)
        means the engine failed on a file that converts without it."""
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
        scanned = [i for i, text in enumerate(pages) if len("".join(text.split())) < _SCANNED_MIN_CHARS]
        read = _OcrText()
        if self._ocr is not None and engine == "pdfium":
            texted = sorted(set(range(len(pages))).difference(scanned))
            try:
                read = _ocr_text(src, self._ocr, scanned, texted)
            except Exception as exc:  # OcrError, or whatever else the pass let through
                # What went wrong goes to the log, never into a page or a reason, and nothing half-read is
                # returned: convert_file converts the file again without OCR.
                detail = str(exc) if isinstance(exc, OcrError) else type(exc).__name__
                log.warning("%s: on-device OCR failed: %s", name, detail)
                raise OcrError(_OCR_FAILED) from None
        if not comments and not any("".join(p.split()) for p in pages) and not any(read.pages.values()):
            # The reason says what was done: OCR did not run (no engine, the fallback, no page PDFium could
            # render), it read every page it may and found nothing, or it also left pages unread.
            if read.over_limit:
                raise UnreadableSourceError(_NO_TEXT_PAST_LIMIT.format(MAX_PAGES))
            raise UnreadableSourceError(_NO_TEXT_FOUND if read.pages else _NO_TEXT)
        out: list[str] = []
        outcomes = dict.fromkeys(_SCANNED_OUTCOMES, 0)  # marker -> the pages without a text layer it is on
        without_text = set(scanned)
        for i, text in enumerate(pages):
            out += [f"<!-- page: {i + 1} -->", ""]
            if i not in without_text:
                out += [text, ""]
                for lines in read.pictures.get(i, ()):
                    out += [_OCR_PICTURE, *lines, ""]
            elif read.pages.get(i):
                outcomes[_OCR_READ] += 1
                out += [*([text, ""] if text.strip() else []), _OCR_READ, "", *read.pages[i], ""]
            else:
                marker = _SCANNED  # not read: no engine, the fallback, or a page PDFium cannot render
                if i in read.pages:
                    marker = _OCR_NO_TEXT
                elif i in read.over_limit:
                    marker = _OCR_OVER_LIMIT
                outcomes[marker] += 1
                out += [marker if not text.strip() else f"{text}\n\n{marker}", ""]
            if i in comments:
                out += [_COMMENTS_HEAD, *comments[i], ""]
        body = "\n".join(out)
        # The first line of three characters or more, in page order: a scanned cover gives the title.
        first = next(
            (
                ln.strip()
                for i, p in enumerate(pages)
                for ln in (*p.split("\n"), *read.pages.get(i, ()))
                if len(ln.strip()) >= 3
            ),
            "",
        )
        title = first.lstrip("\\")[:120] if first else "Untitled PDF"
        summary = f"PDF: {len(pages)} page(s)"
        for marker, clause in _SCANNED_OUTCOMES.items():
            if outcomes[marker]:
                summary += f", {outcomes[marker]} {clause}"
        if read.pictures:
            # Counts only: a summary is front matter, above the banner, and never holds text OCR read.
            summary += "; " + _PICTURES_READ.format(sum(map(len, read.pictures.values())))
        if read.pictures_cut:
            summary += "; " + _PICTURES_CUT
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
