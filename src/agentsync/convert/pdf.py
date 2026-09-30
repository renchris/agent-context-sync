"""Converter: pypdfium2 (PDFium) text extraction, pdfminer.six fallback: one WHOLE unit with page anchors
(owner: convert).

C15 section 6: the design's permissive PDF route is PDFium (BSD-3-Clause/Apache-2.0, via pypdfium2); the
pdfminer family is the route the design's receipts graded C.  PDFium extracts each page's text layer in
reading order (``FPDFText_GetText`` over the whole page).  pdfminer.six (MIT) is kept only as the fallback
for a PDF PDFium cannot load for a reason other than encryption (and for an install where pypdfium2 cannot
be imported); the summary line says when it was used.  Neither engine has a table model: tables come out as
lines of cell text.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _dist_version,
    _escape_plain,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "2.0.0"
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


def _pdfium_pages(src: Path) -> list[str]:
    """Raw text of every page via PDFium; raises UnreadableSourceError when encrypted, else
    _EngineUnavailableError when PDFium cannot load the file."""
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
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    pages.append(str(textpage.get_text_range()))
                finally:
                    textpage.close()
            finally:
                page.close()
        return pages
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


class PdfConverter:
    """PDFium (pypdfium2) text extraction, pdfminer.six fallback: one WHOLE unit with page anchors.

    ``<!-- page: N -->`` anchor per page; a page with < 20 chars of text is marked ``[scanned page: no text
    layer]`` (OCR is a later budgeted tier).  Encrypted PDFs (a password is needed, or ``/Encrypt`` in the
    trailer) raise UnreadableSourceError(``encrypted-pdf ...``); a PDF with no text on any page raises
    UnreadableSourceError (C15: an empty conversion is a stub, never an empty page).  pdfminer.six is used
    only when PDFium cannot load a file for another reason (the summary says so).
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
        try:
            raw_pages = _pdfium_pages(src)
        except _EngineUnavailableError as exc:
            log.info("%s: %s; falling back to pdfminer.six", name, exc)
            engine = "pdfminer"
            raw_pages = _pdfminer_pages(src)
        pages = [_clean(p) for p in raw_pages]
        if not pages:
            raise UnreadableSourceError("empty PDF: no pages")
        if not any("".join(p.split()) for p in pages):
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
        body = "\n".join(out)
        first = next((ln.strip() for p in pages for ln in p.split("\n") if len(ln.strip()) >= 3), "")
        title = first.lstrip("\\")[:120] if first else "Untitled PDF"
        summary = f"PDF: {len(pages)} page(s)"
        if scanned:
            summary += f", {scanned} without a text layer (scanned; OCR not run)"
        if engine == "pdfminer":
            summary += "; text by the pdfminer.six fallback (PDFium could not load it)"
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
