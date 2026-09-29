"""Converter: pdfminer.six text extraction: one WHOLE unit with page anchors (owner: convert)."""

from __future__ import annotations

import logging
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

_EMITTER_VERSION = "1.0.0"
_SCANNED_MIN_CHARS = 20
_SCANNED = "[scanned page: no text layer]"

# Fixed layout-analysis parameters (pdfminer defaults pinned explicitly, plus all_texts so text inside Form
# XObjects/figures is not lost).  Any change here changes output, so it is part of options().
_LAPARAMS: dict[str, float | bool] = {
    "line_overlap": 0.5,
    "char_margin": 2.0,
    "line_margin": 0.5,
    "word_margin": 0.1,
    "boxes_flow": 0.5,
    "detect_vertical": False,
    "all_texts": True,
}


def _texts(layout: Any) -> Iterator[str]:
    """Text of every text box / line in layout order, descending into figures."""
    from pdfminer.layout import LTFigure, LTTextBox, LTTextLine  # noqa: PLC0415

    for obj in layout:
        if isinstance(obj, LTTextBox | LTTextLine):
            yield str(obj.get_text())
        elif isinstance(obj, LTFigure):
            yield from _texts(obj)


def _page_text(layout: Any) -> str:
    """One page's text: NFC, trailing blanks stripped, markdown-neutralised, blank-line collapsed."""
    raw = "\n".join(t.rstrip("\n") for t in _texts(layout))
    raw = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n"))
    return _escape_plain(raw)


class PdfConverter:
    """Pdfminer.six text extraction: one WHOLE unit with page anchors.

    ``<!-- page: N -->`` anchor per page; layout analysis with fixed LAParams; a page with < 20 chars of text
    is marked ``[scanned page: no text layer]`` (OCR is a later budgeted tier). Encrypted PDFs that need a
    password raise UnreadableSourceError.
    """

    converter_id = "pdf-pdfminer"
    extensions: tuple[str, ...] = (".pdf",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+pdfminer.six-{_dist_version('pdfminer.six')}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        opts: dict[str, OptionValue] = {f"laparams.{k}": v for k, v in _LAPARAMS.items()}
        opts["max_page_bytes"] = self._cfg.max_page_bytes
        opts["scanned_min_chars"] = _SCANNED_MIN_CHARS
        return opts

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        from pdfminer.high_level import extract_pages  # noqa: PLC0415 - heavy import
        from pdfminer.layout import LAParams  # noqa: PLC0415
        from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect  # noqa: PLC0415

        with src.open("rb") as fh:
            if b"%PDF-" not in fh.read(1024):
                raise ConversionError("not a PDF (no %PDF- header)")
        laparams = LAParams(**_LAPARAMS)  # type: ignore[arg-type]
        pages: list[str] = []
        try:
            for layout in extract_pages(src, laparams=laparams):
                pages.append(_page_text(layout))
        except PDFPasswordIncorrect as exc:
            raise UnreadableSourceError("password-protected") from exc
        except PDFEncryptionError as exc:
            raise UnreadableSourceError("encrypted") from exc
        except (RecursionError, MemoryError) as exc:
            raise ConversionError(f"pdfminer gave up: {type(exc).__name__}") from exc
        except Exception as exc:  # PDFSyntaxError, PSEOF, struct/zlib errors from damaged streams
            logging.getLogger(__name__).debug("pdfminer failed on %s", name, exc_info=True)
            raise ConversionError(f"pdfminer cannot read the PDF: {type(exc).__name__}: {exc}") from exc
        out: list[str] = []
        scanned = 0
        for n, text in enumerate(pages, start=1):
            out += [f"<!-- page: {n} -->", ""]
            if len("".join(text.split())) < _SCANNED_MIN_CHARS:
                scanned += 1
                out += [_SCANNED if not text.strip() else f"{text}\n\n{_SCANNED}", ""]
            else:
                out += [text, ""]
        if not pages:
            out = ["[empty PDF: no pages]"]
        body = "\n".join(out)
        first = next((ln.strip() for p in pages for ln in p.split("\n") if len(ln.strip()) >= 3), "")
        title = first.lstrip("\\")[:120] if first else "Untitled PDF"
        summary = f"PDF: {len(pages)} page(s)"
        if scanned:
            summary += f", {scanned} without a text layer (scanned; OCR not run)"
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
