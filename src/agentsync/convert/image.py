"""Converter: raster images read by on-device OCR (Apple Vision): one WHOLE unit (owner: convert).

``Registry.default`` registers it only when the cycle hands it an OCR engine (``convert/ocr.py``) and no
``[policy]`` label rule is active: an image can carry a sensitivity label, and nothing here reads one.
Without it an image keeps the ``no converter`` stub it always had.

The page is one header line with the pixel size, then the recognised text in reading order
(``ocr.text_lines``).  Neither the body, the title nor the summary holds the file's name: the converter cache
key has no name in it, so a second file with the same bytes is served the first one's page.  The summary is
facts only (size, pages, line count), never the text.  A multi-page TIFF gets one ``<!-- page: N -->`` anchor
per page read (at most ``ocr.MAX_PAGES``); every other type is read from its first frame.

An image is not always a page.  One with no legible text is ``UnreadableSourceError``, a cached stub (a page
per logo would be curation work for ever), and so is a file that is not a raster image or that the helper
reports as one it cannot read.  A helper that fails is ``OcrError``: a failed conversion, which is never
cached.  Its reason goes to the log and the stub gets fixed wording.

``_RASTERS`` says what a raster image is, by its first bytes, for a file of its own and for a picture inside
another document alike.  :func:`_read_pictures` is for the converters of those documents: it copies a
document's pictures out one at a time, within a count and a byte limit, reads them within a time limit, and
gives back the text of each by the sha256 of its bytes.  A picture the helper fails on costs only itself.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO

from agentsync.config import ConvertConfig
from agentsync.convert._common import _FULL_TEXT_SIDECAR, _cap_body, _escape_line
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.ocr import LANGUAGES, MAX_PAGES, OcrEngine, OcrError, OcrImage, text_lines
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "2.0.0"  # 1.0.0 was a field build that wrote the file name into the page
_ENGINE_LABEL = "Apple Vision"
_DOCUMENT_BUDGET_S = 300.0
"""The seconds the helper may take over one document, whatever it holds (pandoc's limit is the same).  Past
it the read is an ``OcrError``."""
_MAX_PICTURES = MAX_PAGES  # distinct pictures of one document that are read
_MAX_PICTURE_BYTES = 256 * 1024 * 1024  # picture bytes read from one document, copies of a picture included
_PICTURES_PER_RUN = 16  # one helper run; a run that fails is read again one picture at a time
_CHUNK = 1024 * 1024
_HEAD_BYTES = 16  # what the tests of ``_RASTERS`` look at
# The major brands of a HEIF still image or sequence (ISO/IEC 23008-12); ``heic`` is what a phone writes.
_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}
)
_PAGED = ".tif"  # the one type whose frames are pages; the frames of any other are an animation
# The one ``OcrImage.error`` that is not a fact about the bytes (Vision gave up on the frame): no cached stub.
_GAVE_UP = "recognition failed"
# Stub reasons.  Fixed wording: no file name, no path and no exception text.
_NOT_RASTER = "not an image on-device OCR reads (PNG, JPEG, GIF, BMP, TIFF, WebP, HEIC or HEIF)"
_NOT_READABLE = "image not readable by on-device OCR"
_TOO_SMALL = "image too small to hold text"
_NO_TEXT = "no text found in the image by on-device OCR"
_FAILED = "on-device OCR failed"
_NO_PAGE_TEXT = "[no text on this page]"
# In a deck and in a Word document: the line between a picture's ``[image…]`` line and the text read in it.
_PICTURE_HEAD = f"[text in the image above, read by on-device OCR ({_ENGINE_LABEL}):]"
# A document's summary clauses about its pictures.  Counts only: a summary never holds text OCR read.
_PICTURES_READ = "text of {} picture(s) read by on-device OCR"
_PICTURES_CUT = "pictures past the OCR picture limit not read"
# C0 controls are no part of what a picture shows; ``text_lines`` has already made whitespace one space.
# Half a surrogate pair goes with them: a page that holds one cannot be written as UTF-8, so one in a
# helper's answer would fail the document it was read in.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
_BLOCK_RE = re.compile(r"^(?:`{3,}|~{3,}|<)")  # a code fence, or a tag: either can open a block
_clock = time.monotonic


def _heif(head: bytes) -> bool:
    return head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS


_RASTERS: tuple[tuple[Callable[[bytes], bool], tuple[str, ...]], ...] = (
    (lambda head: head.startswith(b"\x89PNG\r\n\x1a\n"), (".png",)),
    (lambda head: head.startswith(b"\xff\xd8\xff"), (".jpg", ".jpeg")),
    (lambda head: head.startswith((b"GIF87a", b"GIF89a")), (".gif",)),
    (lambda head: head.startswith(b"BM"), (".bmp",)),
    (lambda head: head.startswith((b"II*\x00", b"MM\x00*")), (_PAGED, ".tiff")),
    (lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP", (".webp",)),
    (_heif, (".heic", ".heif")),
)
"""The raster types on-device OCR reads: a test of a file's first ``_HEAD_BYTES`` bytes, and the suffixes
the type goes by.  The one table for both paths: its suffixes are what ``ImageConverter`` claims, and its
tests decide for a staged file and for a picture inside a document.  They are the types the helper allows;
the helper, which looks at the whole file, has the last word."""

_OCR_OPTIONS: Mapping[str, OptionValue] = MappingProxyType(
    {
        "ocr_languages": ",".join(LANGUAGES),
        "ocr_max_pages": MAX_PAGES,
        "ocr_max_pictures": _MAX_PICTURES,
        "ocr_max_picture_bytes": _MAX_PICTURE_BYTES,
    }
)
"""The options of every converter that has an engine: the limits here that can change a page.  (What the
helper is run with is in ``OcrEngine.identity``, so in the version.)"""


def _raster_suffix(head: bytes) -> str | None:
    """The first suffix of the raster type ``head`` (a file's first ``_HEAD_BYTES`` bytes) starts; None for
    anything else: vector art (EMF, WMF, SVG), a PDF, text, an empty file."""
    for test, suffixes in _RASTERS:
        if test(head):
            return suffixes[0]
    return None


def _ocr_lines(image: OcrImage, *, escape: bool = True) -> list[str]:
    """One frame as page text: its lines in reading order, "" between blocks; none for a frame that was not
    read.  A picture can show any characters, so each line is neutralised as plain text is (no heading, rule
    or fake ``<!-- page: N -->`` anchor), and a leading code fence or ``<`` is escaped too: an open fence, or
    the HTML block a tag such as ``<pre>`` opens, would take in every anchor after it.

    ``escape=False`` gives the lines as read, less their control characters: for a caller that hands them
    to a writer which escapes what it writes (pandoc).  Such lines must never be put on a page as they are.
    """
    out: list[str] = []
    for line in text_lines(image):
        text = _CONTROL_RE.sub("", line).strip()  # a dropped control can leave an indent
        if escape:
            text = _escape_line(text)
            text = "\\" + text if _BLOCK_RE.match(text) else text
        out.append(text)
    return out


# ---------------------------------------------------------------------------------------------------------
# pictures inside a document
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PictureText:
    """What :func:`_read_pictures` made of one document's pictures.

    ``digests`` has one entry per picture looked at, in the order offered: the sha256 of its bytes, or None
    for one that was not taken (not a raster image, not readable to its end, or the one that passed the byte
    limit).  It is shorter than the pictures offered when a limit stopped the reading.  ``lines`` holds the
    lines of each picture text was read in (escaped, unless the caller asked for them as read), by that
    digest.  ``unread`` counts the pictures taken that a helper failure or the time limit left unread: 0
    means every picture without an entry in ``lines`` holds no text.  ``over_bytes`` says the byte limit
    stopped the reading: the last picture looked at passed it and was not read.
    """

    digests: tuple[str | None, ...]
    lines: Mapping[str, list[str]]
    unread: int
    over_bytes: bool = False


def _read_each(
    engine: OcrEngine, paths: Sequence[Path], *, work_dir: Path, deadline: float
) -> tuple[list[OcrImage | None], str]:
    """The first frame of each of ``paths`` (None where the helper failed), and the first failure's reason.

    The helper is run on ``_PICTURES_PER_RUN`` pictures at a time.  A run that fails gives nothing, so its
    pictures are read again one at a time: one picture that sinks the helper costs only itself.  Past
    ``deadline`` no run is started and every picture left is None."""
    out: list[OcrImage | None] = []
    reason = ""

    def read(batch: Sequence[Path]) -> list[OcrImage]:
        answers = engine.read(batch, work_dir=work_dir, budget_s=deadline - _clock())
        return [frames[0] for frames in answers]

    for start in range(0, len(paths), _PICTURES_PER_RUN):
        batch = paths[start : start + _PICTURES_PER_RUN]
        try:
            out += read(batch)
            continue
        except OcrError as exc:
            reason = reason or str(exc)
        if len(batch) == 1:
            out.append(None)
            continue
        for path in batch:
            try:
                out += read([path])
            except OcrError:
                out.append(None)
    return out, reason


def _next_bytes(stream: BinaryIO, size: int) -> bytes | None:
    """Up to ``size`` bytes of a picture's stream, ``b""`` at its end; None when the stream cannot be read.

    A stream that raises is a damaged entry of the document (a ZIP member that does not inflate, or whose
    checksum is wrong): a fact about the bytes, so the picture is not taken and the rest are still read.
    Only the exception's type is logged: its text can name the entry."""
    try:
        return stream.read(size)
    except Exception as exc:
        log.debug("a picture inside a document could not be read to its end: %s", type(exc).__name__)
        return None


def _read_pictures(
    engine: OcrEngine,
    pictures: Iterable[BinaryIO],
    *,
    work_dir: Path,
    budget_s: float,
    limit: int = _MAX_PICTURES,
    max_bytes: int = _MAX_PICTURE_BYTES,
    escape: bool = True,
) -> _PictureText:
    """OCR the pictures of one document; see :class:`_PictureText` for what comes back.  With
    ``escape=False`` its lines are as read and not escaped (see :func:`_ocr_lines`).

    ``pictures`` is consumed one stream at a time, and each stream is read once, in chunks, and closed: pass
    a generator that opens the next picture only when asked, and nothing of a document is held in memory.  A
    picture whose first bytes are no raster type (``_RASTERS``) is not read past them, and one whose stream
    cannot be read to its end is not taken (``_next_bytes``).  The rest are copied into a folder made inside
    ``work_dir`` (the staged file's own folder, so under the cycle's staging folder) and removed before this
    returns.  The reading stops at ``limit`` distinct pictures and at ``max_bytes`` read (the copies of a
    repeated picture count: offer each picture once).  Both are counts, so the same document gives the same
    pictures on every run.

    The helper then has what is left of ``budget_s`` seconds, counted from the call.  Never raises OcrError:
    a picture the helper could not be run on is counted in ``unread`` (and the first reason logged once).
    """
    deadline = _clock() + budget_s
    digests: list[str | None] = []
    kept: dict[str, Path] = {}
    spent = 0
    over_bytes = False
    offered = iter(pictures)
    with tempfile.TemporaryDirectory(dir=work_dir, prefix=".ocr-", ignore_cleanup_errors=True) as tmp:
        # The next picture is asked for only while there is room for one: none is opened to be turned away.
        while len(kept) < limit and spent < max_bytes and (stream := next(offered, None)) is not None:
            with stream:
                chunk = _next_bytes(stream, _HEAD_BYTES)
                suffix = _raster_suffix(chunk) if chunk else None
                if suffix is None:
                    digests.append(None)
                    continue
                path = Path(tmp) / f"{len(digests):05d}{suffix}"
                sha = hashlib.sha256()
                with path.open("wb") as out:
                    while chunk and spent <= max_bytes:
                        spent += len(chunk)
                        sha.update(chunk)
                        out.write(chunk)
                        chunk = _next_bytes(stream, _CHUNK)
            if chunk is None:  # damaged part-way: what was copied is no picture, and is not read
                path.unlink()
                digests.append(None)
                continue
            if spent > max_bytes:  # the picture that passed the limit is not read, nor any after it
                digests.append(None)
                over_bytes = True
                break
            digest = sha.hexdigest()
            digests.append(digest)
            if digest in kept:
                path.unlink()
            else:
                kept[digest] = path
        images, reason = _read_each(engine, list(kept.values()), work_dir=Path(tmp), deadline=deadline)
    lines: dict[str, list[str]] = {}
    unread = 0
    for digest, image in zip(kept, images, strict=True):
        if image is None or image.error == _GAVE_UP:
            unread += 1
        elif any(block := _ocr_lines(image, escape=escape)):
            lines[digest] = block
    if unread:
        log.warning(
            "on-device OCR left %d of %d picture(s) unread: %s", unread, len(kept), reason or _GAVE_UP
        )
    return _PictureText(tuple(digests), lines, unread, over_bytes)


def _raster_left(pictures: Iterator[BinaryIO]) -> bool:
    """True when a picture :func:`_read_pictures` did not ask for is a raster image: a limit left it unread.

    ``pictures`` is the iterator that call was given.  Each stream still in it is read no further than its
    first bytes and closed, and the looking stops at the first raster image."""
    for stream in pictures:
        with stream:
            head = _next_bytes(stream, _HEAD_BYTES)
        if head and _raster_suffix(head) is not None:
            return True
    return False


# ---------------------------------------------------------------------------------------------------------
# an image file
# ---------------------------------------------------------------------------------------------------------


def _no_page(frames: Sequence[OcrImage]) -> ConversionError:
    """Why an image no frame of which gave a line of text is not a page."""
    first = frames[0]
    if any(frame.error == _GAVE_UP for frame in frames):
        return OcrError(_FAILED)  # not a fact about the bytes: a failure, never a cached stub
    if first.error:
        return UnreadableSourceError(f"{_NOT_READABLE} ({first.error})")
    return UnreadableSourceError(_TOO_SMALL if first.skipped else _NO_TEXT)


class ImageConverter:
    """Raster image -> one header line with its pixel size, then its text as on-device OCR read it."""

    converter_id = "image-ocr"
    extensions: tuple[str, ...] = tuple(suffix for _test, suffixes in _RASTERS for suffix in suffixes)

    def __init__(self, cfg: ConvertConfig, engine: OcrEngine) -> None:
        """Bind converter options and the cycle's OCR engine."""
        self._cfg = cfg
        self._engine = engine

    def version(self) -> str:
        """Version as run: the emitter, then the engine's identity (engine, revision, helper, layout)."""
        return f"{_EMITTER_VERSION}+{self._engine.identity}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {"max_page_bytes": self._cfg.max_page_bytes, **_OCR_OPTIONS}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors.

        ``name`` labels the log line of a helper failure and nothing else.  The helper runs in the staged
        file's own folder, which is under the cycle's staging folder."""
        with src.open("rb") as fh:
            kind = _raster_suffix(fh.read(_HEAD_BYTES))
        if kind is None:
            raise UnreadableSourceError(_NOT_RASTER)
        try:
            (frames,) = self._engine.read(
                [src],
                work_dir=src.parent,
                budget_s=_DOCUMENT_BUDGET_S,
                frames=MAX_PAGES if kind == _PAGED else 1,
            )
        except OcrError as exc:  # the engine's text has no path in it, and still it is not a stub reason
            log.warning("%s: on-device OCR failed: %s", name, exc)
            raise OcrError(_FAILED) from None
        blocks = [_ocr_lines(frame) for frame in frames]
        count = sum(1 for block in blocks for line in block if line)
        if not count:
            if any(frame.error == _GAVE_UP for frame in frames):
                log.warning("%s: on-device OCR failed: %s", name, _GAVE_UP)
            raise _no_page(frames)
        total = frames[0].frames
        paged = kind == _PAGED and total > 1
        out: list[str] = []
        unread = 0
        for frame, block in zip(frames, blocks, strict=True):
            if paged:
                out += [f"<!-- page: {frame.frame + 1} -->", ""]
            if frame.error or frame.skipped:
                unread += 1
                out += [f"[page not read: {frame.error or 'too small to hold text'}]", ""]
            elif any(block):
                out += [*block, ""]
            else:
                out += [_NO_PAGE_TEXT, ""]
        shown = next(frame for frame, block in zip(frames, blocks, strict=True) if any(block))
        size = f"{shown.width}x{shown.height} px"
        head, summary = f"[image · {size}", f"Image {size}"
        if total > 1:
            unit = "pages" if paged else "frames"
            head += f" · {total} {unit}" + (f", first {len(frames)} read" if len(frames) < total else "")
            summary += f", {total} {unit} ({len(frames) - unread} read)"
        head += f" · text read by on-device OCR ({_ENGINE_LABEL})]"
        summary += f"; OCR: {count} line(s)"
        body = "\n".join([head, "", *out]).rstrip("\n")
        body, sidecars = _cap_body(body, self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR)
        return (
            make_unit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title="",
                summary=summary,
                body=body,
                sidecars=sidecars,
            ),
        )
