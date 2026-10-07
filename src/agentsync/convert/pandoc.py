"""Converter: docx/odt/rtf/html via the pypandoc_binary-bundled pandoc, invoked by ABSOLUTE path (owner:
convert).

With an OCR engine (``PandocConverter(cfg, ocr=engine)``; ``convert/ocr.py``) the text in the pictures of a
Word or OpenDocument file follows the block that shows each picture.  Such a converter claims ``.docx`` and
``.odt`` only.  An ``.rtf`` or ``.html`` file holds no picture that is read, and ``Registry.default`` leaves
those to a converter without an engine (``_PandocWithoutOcr``), so nothing about them changes: not the page,
not the version, not the action key.  Without an engine nothing here runs and all five are as they were
before OCR existed.

The pictures are read before pandoc runs.  What can go wrong on the way costs pictures and never the
document.  A failure of the helper, or of pandoc once it was handed text to place, is ``OcrError`` with fixed
wording: ``convert_file`` then converts the file with the registry's converter that has no engine.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from html import unescape
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast
from xml.etree import ElementTree as ET

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _check_odf_container,
    _check_ooxml_container,
    _decode_text,
    _first_line,
    _summary_from_markdown,
    _title_from_markdown,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.convert.image import (
    _DOCUMENT_BUDGET_S,
    _ENGINE_LABEL,
    _OCR_OPTIONS,
    _PICTURES_CUT,
    _PICTURES_READ,
    _raster_left,
    _read_pictures,
    _read_without_ocr,
)
from agentsync.convert.image import _FAILED as _OCR_FAILED
from agentsync.convert.ocr import OcrEngine, OcrError
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "1.0.0"
_TIMEOUT_S = 300.0
_VERSION_RE = re.compile(r"^pandoc(?:\.exe)?\s+(\S+)", re.MULTILINE)
_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_EMPTY_PLACEHOLDER = "[empty document]"

_FORMATS: dict[str, str] = {".docx": "docx", ".odt": "odt", ".rtf": "rtf", ".html": "html", ".htm": "html"}

_OCR_RULES = 1
"""Bumped when a rule here that decides which pictures OCR reads, or how the filter shows their text,
changes.  It is in the options only with an engine, so it moves no key of a Mac without one (the emitter
version and ``lua_filter`` would)."""
_OCR_SIDE_FILE = "agentsync-ocr.json"  # in pandoc's job folder: what the filter's first pass is to place
_PLACED_RE = re.compile(r"^agentsync-ocr: placed (\d+)$", re.MULTILINE)  # what that pass says on stderr
# A head line of that pass, as it stands in the page.  The document's own brackets come out escaped.
_HEAD_RE = re.compile(
    rf"^\[text in (?:the image|image \d+) above, read by on-device OCR \({re.escape(_ENGINE_LABEL)}\):\]$",
    re.MULTILINE,
)
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAX_RELS_BYTES = 4 * 1024 * 1024  # of a relationships part: one costs about a hundred bytes a picture
_MAX_SCAN_BYTES = 64 * 1024 * 1024  # of a document part looked through for the pictures it uses
_SCAN_CHUNK = 1024 * 1024
_SCAN_OVERLAP = 2048  # longer than anything the two patterns below match
# A relationship id as a docx part uses one: r:embed (a drawing), r:id (a VML picture), r:pict.
_RID_RE = re.compile(rb"""[\s:](?:embed|id|pict)\s*=\s*(["'])([^"'<>]{1,255})\1""")
# A reference as an odt's content.xml writes one: xlink:href.
_HREF_RE = re.compile(rb"""[\s:]href\s*=\s*(["'])([^"'<>]{1,1024})\1""")

# Fixed pandoc flags: every one of them can change the output, so they are part of options().
_PANDOC_FLAGS: tuple[str, ...] = (
    "--sandbox",  # readers/writers may not touch the network or other files
    "-t",
    "gfm",
    "--wrap=none",
    "--eol=lf",
    "--track-changes=accept",  # docx: the document as it reads now (pandoc's default, pinned)
    "--markdown-headings=atx",
)

# No media is extracted: images become text references ``[image: <alt> — <target>]`` (data:/cid: targets are
# dropped, so no base64 ever reaches docs/); figures collapse to a paragraph; div/span wrappers are unwrapped
# so the gfm writer does not leak raw ``<div>`` HTML.  Merged-cell tables stay HTML tables (C13, accepted).
#
# The first pass (``ocr``) is for a docx or odt whose pictures on-device OCR read text in.  The converter
# then writes ``_OCR_SIDE_FILE`` into the job folder: ``pictures`` (image source -> picture key) and ``text``
# (picture key -> the lines read, not escaped).  After each top-level block that shows such a picture, and
# after each block of a footnote that does, the pass adds a head line and the lines, once per key however
# many sources share it.  The lines are built as words and spaces, which the gfm writer escapes as it
# escapes the document's own text: nothing read in a picture is ever written raw.  Without the file the pass
# changes nothing, so the output is what it was.  Whatever goes wrong in it leaves the document as it is
# and unreported (``_PLACED_RE``), never failed.
_LUA_FILTER = r"""
local ocr_text = nil
pcall(function()
  local file = io.open("agentsync-ocr.json", "r")
  if file == nil then return end
  local raw = file:read("a")
  file:close()
  local data = pandoc.json.decode(raw, false)
  if type(data) == "table" and type(data.pictures) == "table" and type(data.text) == "table" then
    ocr_text = data
  end
end)
local OCR_HEAD = "[text in the image above, read by on-device OCR (@ENGINE@):]"
local function ocr_head(n, of)
  if of == 1 then return OCR_HEAD end
  return "[text in image " .. n .. " above, read by on-device OCR (@ENGINE@):]"
end
-- One line as words and spaces.  The writer escapes every word, but inside a paragraph it leaves the start
-- of a later line alone, so a backslash goes before what would start a list there (a bullet, a number) or
-- be a rule or the underline of a heading (a line of dashes, a line of equals signs).
local function ocr_line(line)
  local out = pandoc.Inlines({})
  local digits, mark, rest = line:match("^(%d+)([.)])(.*)$")
  if digits ~= nil and (rest == "" or rest:sub(1, 1) == " ") then
    out:insert(pandoc.Str(digits))
    out:insert(pandoc.RawInline("gfm", "\\"))
    line = mark .. rest
  elseif line:match("^[-+] ") or line:match("^[- ]+$") or line:match("^=+$") then
    out:insert(pandoc.RawInline("gfm", "\\"))
  end
  local first = true
  for word in line:gmatch("%S+") do
    if not first then out:insert(pandoc.Space()) end
    out:insert(pandoc.Str(word))
    first = false
  end
  return out
end
local function ocr_blocks(head, lines)
  local out = pandoc.Blocks({ pandoc.Para({ pandoc.RawInline("gfm", head) }) })
  local block = {}
  for _, line in ipairs(lines) do
    if type(line) == "string" and line:match("%S") then
      block[#block + 1] = ocr_line(line)
    elseif #block > 0 then
      out:insert(pandoc.LineBlock(block))
      block = {}
    end
  end
  if #block > 0 then out:insert(pandoc.LineBlock(block)) end
  return out
end
-- The pictures a block shows where it stands: those of its footnotes are shown with the notes.
local function ocr_sources(block)
  local sources = {}
  local bare = block:walk({ Note = function() return {} end })
  bare:walk({ Image = function(img) sources[#sources + 1] = img.src end })
  return sources
end
-- ``blocks`` with the text of each picture a block shows added after that block.
local function ocr_after(blocks, state)
  local out = pandoc.Blocks({})
  for _, block in ipairs(blocks) do
    out:insert(block)
    local sources = ocr_sources(block)
    for n, source in ipairs(sources) do
      local key = ocr_text.pictures[source]
      local lines = key ~= nil and ocr_text.text[key] or nil
      if type(lines) == "table" and not state.printed[key] then
        state.printed[key] = true
        state.count = state.count + 1
        out:extend(ocr_blocks(ocr_head(n, #sources), lines))
      end
    end
  end
  return out
end
-- The body, then the footnotes: the order the page shows them in.  ``doc`` itself is left as it is.
local function ocr_place(doc)
  local state = { printed = {}, count = 0 }
  local placed = pandoc.Pandoc(ocr_after(doc.blocks, state), doc.meta):walk({
    Note = function(note)
      note.content = ocr_after(note.content, state)
      return note
    end,
  })
  return placed, state.count
end
local ocr = {
  Pandoc = function(doc)
    if ocr_text == nil then return nil end
    local ok, placed, count = pcall(ocr_place, doc)
    if not ok then return nil end
    io.stderr:write("agentsync-ocr: placed " .. count .. "\n")
    return placed
  end,
}
local function clean(s)
  s = s:gsub("[%[%]|`]", " "):gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  return s
end
local function ref(src)
  if src == nil or src == "" then return "" end
  if src:sub(1, 5) == "data:" or src:sub(1, 4) == "cid:" then return "" end
  if src:match("^%a[%w+.-]*:") then
    local path = src:gsub("[?#].*$", "")
    local base = path:match("([^/]+)$") or ""
    if base:match("^[^$][^/]*%.%w+$") then return base end
    return ""
  end
  return src
end
local function label(alt, src)
  alt = clean(alt or "")
  local r = clean(ref(src))
  local parts = {}
  if alt ~= "" then parts[#parts + 1] = alt end
  if r ~= "" and r ~= alt then parts[#parts + 1] = r end
  if #parts == 0 then return pandoc.RawInline("gfm", "[image]") end
  return pandoc.RawInline("gfm", "[image: " .. table.concat(parts, " — ") .. "]")
end
local figures = {
  Figure = function(fig)
    local inls = pandoc.Inlines({})
    local alts = {}
    fig.content:walk({ Image = function(img)
      local alt = pandoc.utils.stringify(img.caption)
      alts[clean(alt)] = true
      if #inls > 0 then inls:insert(pandoc.Space()) end
      inls:insert(label(alt, img.src))
    end })
    local cap = clean(pandoc.utils.stringify(fig.caption.long))
    if cap ~= "" and not alts[cap] then
      if #inls > 0 then inls:insert(pandoc.Space()) end
      inls:insert(pandoc.Str(cap))
    end
    return pandoc.Para(inls)
  end,
}
local rest = {
  Image = function(img) return label(pandoc.utils.stringify(img.caption), img.src) end,
  Div = function(div) return div.content end,
  Span = function(span) return span.content end,
}
return { ocr, figures, rest }
""".replace("@ENGINE@", _ENGINE_LABEL)


def _bundled_pandoc() -> Path:
    """Absolute path of the pypandoc_binary-bundled pandoc (never a PATH lookup)."""
    import pypandoc  # noqa: PLC0415 - heavy import, only when a pandoc converter actually runs

    bundled = Path(str(pypandoc.__file__)).resolve().parent / "files" / "pandoc"
    if bundled.is_file():
        return bundled
    raise ConversionError(
        f"bundled pandoc not found at {bundled} (is pypandoc_binary installed?); set [convert] pandoc_path"
    )


def _decode_html(data: bytes) -> str:
    """Decode HTML bytes: BOM/UTF-8 first, then a declared ``<meta charset>``, then cp1252/latin-1."""
    if not data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        try:
            return unicodedata.normalize("NFC", data.decode("utf-8"))
        except UnicodeDecodeError:
            m = _META_CHARSET_RE.search(data[:4096])
            if m:
                try:
                    return unicodedata.normalize(
                        "NFC", data.decode(m.group(1).decode("ascii"), errors="replace")
                    )
                except LookupError:
                    pass
    return _decode_text(data.replace(b"\x00", b""))


class _PandocRunner:
    """Runs one pandoc binary by absolute path, with a fixed environment, a private data dir and a timeout."""

    def __init__(self, pandoc_path: Path | None) -> None:
        self._configured = pandoc_path
        self._path: Path | None = None
        self._version: str | None = None

    @property
    def source(self) -> str:
        """``bundled`` or ``configured`` (which binary is used, for options())."""
        return "configured" if self._configured is not None else "bundled"

    def path(self) -> Path:
        """Resolve the binary once (absolute)."""
        if self._path is None:
            if self._configured is not None:
                p = self._configured.expanduser()
                if not p.is_absolute() or not p.is_file():
                    raise ConversionError(f"[convert] pandoc_path is not an absolute file: {p}")
                self._path = p
            else:
                self._path = _bundled_pandoc()
        return self._path

    def version(self) -> str:
        """``pandoc --version`` as run, e.g. ``3.9`` (cached per instance)."""
        if self._version is None:
            out, _said = self._run(["--version"], None)
            m = _VERSION_RE.search(out)
            if not m:
                raise ConversionError(f"cannot parse `pandoc --version` output: {out[:80]!r}")
            self._version = m.group(1)
        return self._version

    def _run(self, args: Sequence[str], workdir: Path | None) -> tuple[str, str]:
        """Run pandoc with ``args`` in a scrubbed environment; return (stdout, stderr); raise
        ConversionError."""
        with tempfile.TemporaryDirectory(prefix="agentsync-pandoc-") as tmp:
            home = Path(tmp)
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
            try:
                proc = subprocess.run(
                    [str(self.path()), *args],
                    cwd=str(workdir or home),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=_TIMEOUT_S,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ConversionError(f"pandoc timed out after {_TIMEOUT_S:.0f}s") from exc
            except OSError as exc:
                raise ConversionError(f"cannot run pandoc: {exc}") from exc
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise ConversionError(f"pandoc exited {proc.returncode}: {err[0] if err else 'no message'}")
        said = proc.stderr.decode("utf-8", errors="replace")
        if said:
            # How much, never what: a pandoc warning quotes the document (an id, a file name in it), and
            # this runner also converts mail and Teams HTML.
            log.debug("pandoc wrote %d character(s) to stderr", len(said))
        return proc.stdout.decode("utf-8", errors="replace"), said

    def to_gfm(self, src: Path, fmt: str) -> str:
        """Convert the file ``src`` (pandoc reader ``fmt``) to GFM text (NFC, LF)."""
        return self._to_gfm(src, fmt, None)[0]

    def to_gfm_with_picture_text(self, src: Path, fmt: str, text: Mapping[str, object]) -> tuple[str, int]:
        """``to_gfm`` with the text on-device OCR read in the file's pictures; also returns how many
        pictures' text the filter put on the page.

        ``text`` is what the filter's first pass places (see ``_LUA_FILTER``).  It goes into pandoc's job
        folder, where the converted text itself is written.  Raises OcrError when pandoc ran and the pass
        did not report: the page then holds none of the text, and must not pass for one that does."""
        body, said = self._to_gfm(src, fmt, json.dumps(text, sort_keys=True))
        placed = _PLACED_RE.search(said)
        if placed is None:
            raise OcrError("the pandoc filter did not place the picture text")
        return body, int(placed.group(1))

    def _to_gfm(self, src: Path, fmt: str, picture_text: str | None) -> tuple[str, str]:
        """(GFM text of ``src``, what pandoc wrote to stderr); ``picture_text`` is ``_OCR_SIDE_FILE``'s
        content when there is any to place."""
        with tempfile.TemporaryDirectory(prefix="agentsync-pandoc-job-") as tmp:
            work = Path(tmp)
            (work / "data").mkdir()
            if picture_text is not None:
                (work / _OCR_SIDE_FILE).write_text(picture_text, encoding="utf-8")
            lua = work / "agentsync-images.lua"
            lua.write_text(_LUA_FILTER, encoding="utf-8")
            out = work / "out.md"
            args = [
                f"--data-dir={work / 'data'}",
                "-f",
                fmt,
                *_PANDOC_FLAGS,
                f"--lua-filter={lua}",
                "-o",
                str(out),
                str(src.resolve()),
            ]
            _out, said = self._run(args, work)
            text = out.read_text(encoding="utf-8", errors="replace")
        return unicodedata.normalize("NFC", text.replace("\r\n", "\n")), said

    def html_to_gfm(self, html: str) -> str:
        """Convert an HTML fragment/document (already decoded) to GFM text."""
        with tempfile.TemporaryDirectory(prefix="agentsync-pandoc-html-") as tmp:
            src = Path(tmp) / "in.html"
            src.write_text(html, encoding="utf-8")
            return self.to_gfm(src, "html")


def _pandoc_options(runner: _PandocRunner) -> dict[str, OptionValue]:
    """Options shared by every pandoc-backed emitter."""
    return {
        "pandoc_flags": " ".join(_PANDOC_FLAGS),
        "pandoc_binary": runner.source,
        "images": "text-reference",
        "lua_filter": "agentsync-images@1",
    }


# ---------------------------------------------------------------------------------------------------------
# the pictures of a docx or odt (only with an OCR engine)
# ---------------------------------------------------------------------------------------------------------


def _small_part(zf: zipfile.ZipFile, name: str) -> bytes | None:
    """The bytes of a ZIP entry of at most ``_MAX_RELS_BYTES``; None when it is absent or larger.  No more
    than the limit is inflated, whatever size its header claims."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_RELS_BYTES:
        return None
    with zf.open(info) as fh:
        data = fh.read(_MAX_RELS_BYTES + 1)
    return data if len(data) <= _MAX_RELS_BYTES else None


def _relationships(zf: zipfile.ZipFile, name: str, kind: str) -> Iterator[tuple[str, str]]:
    """(id, target) of each relationship of type ``…/<kind>`` in the relationships part ``name`` that leads
    to a part of the package; none when the part is absent or too large."""
    data = _small_part(zf, name)
    if data is None:
        return
    for rel in ET.fromstring(data).iter(f"{{{_RELS_NS}}}Relationship"):
        if (rel.get("Type") or "").endswith("/" + kind) and rel.get("TargetMode") != "External":
            yield rel.get("Id") or "", rel.get("Target") or ""


def _attribute_values(zf: zipfile.ZipFile, part: str, pattern: re.Pattern[bytes]) -> Iterator[str]:
    """The attribute values ``pattern`` finds in the XML part ``part``, in document order; none when the
    part is absent.  A value near the end of a chunk can come twice.

    The part is streamed past a regular expression and not parsed: it can be as large as the document, the
    order of a few attributes is all that is wanted, and pandoc is the one that reads the XML.  At most
    ``_MAX_SCAN_BYTES`` of it are looked at."""
    try:
        fh = zf.open(part)
    except KeyError:
        return
    with fh:
        tail, left = b"", _MAX_SCAN_BYTES
        while left > 0 and (chunk := fh.read(min(_SCAN_CHUNK, left))):
            left -= len(chunk)
            data = tail + chunk
            for match in pattern.finditer(data):
                yield unescape(match.group(2).decode("utf-8", errors="replace"))
            tail = data[-_SCAN_OVERLAP:]


def _docx_pictures(zf: zipfile.ZipFile) -> Iterator[tuple[str, str]]:
    """(``Image.src`` as pandoc's docx reader gives it, ZIP entry) of each picture of a docx, in order of
    first use: the body, then the footnotes, then the endnotes.

    The pictures are the ones the relationships of those three parts name and the parts use, which is what
    pandoc can show.  Nothing else under ``word/media`` is looked at: the logo of a header or footer is not
    on the page.  pandoc drops a target's leading slashes and ``word/``, and looks the rest up under
    ``word/``; a target that is not under ``word/`` it keeps as written, and looks up from the package
    root when it starts with a slash.  This does the same, so the source matches the one the filter sees."""
    main = next((t for _id, t in _relationships(zf, "_rels/.rels", "officeDocument")), "word/document.xml")
    for part in (main.lstrip("/"), "word/footnotes.xml", "word/endnotes.xml"):
        path = PurePosixPath(part)
        targets = dict(_relationships(zf, f"{path.parent}/_rels/{path.name}.rels", "image"))
        if not targets:
            continue
        for rid in _attribute_values(zf, part, _RID_RE):
            target = targets.pop(rid, "")  # taken out: a picture is listed where it is first used
            if target:
                inside = target.lstrip("/")
                src = inside.removeprefix("word/") if inside.startswith("word/") else target
                yield src, src[1:] if src.startswith("/") else "word/" + src


def _odt_pictures(zf: zipfile.ZipFile) -> Iterator[tuple[str, str]]:
    """(``Image.src`` as pandoc's odt reader gives it, ZIP entry) of each picture ``content.xml`` of an odt
    refers to, in order of first use.

    The source is the reference as written.  A reference that names no entry of the package (a web address,
    a bookmark) is no picture.  ``styles.xml`` is not looked at: the picture of a header or footer is not on
    the page."""
    seen: set[str] = set()
    for href in _attribute_values(zf, "content.xml", _HREF_RE):
        entry = href.removeprefix("./")
        if href not in seen and entry in zf.NameToInfo:
            seen.add(href)
            yield href, entry


_LISTERS: dict[str, Callable[[zipfile.ZipFile], Iterator[tuple[str, str]]]] = {
    "docx": _docx_pictures,
    "odt": _odt_pictures,
}
_PICTURED: tuple[str, ...] = tuple(ext for ext, fmt in _FORMATS.items() if fmt in _LISTERS)
"""The suffixes of the formats whose pictures on-device OCR reads: ``.docx`` and ``.odt``."""


class _Pictures:
    """The pictures of an open docx or odt as streams for ``image._read_pictures``, in order of first use.

    Each ZIP entry is offered once, however many references lead to it, and is inflated only as it is read.
    What goes wrong here costs pictures and never the document: an entry that cannot be opened is skipped,
    and an error while the document is looked through ends the looking (memory apart).  The log gets the
    type of the error and nothing the document says: an error's text can name an entry.

    ``entries`` holds the entry of each stream offered so far, and ``sources`` the entry behind each
    ``Image.src`` met so far.
    """

    def __init__(self, zf: zipfile.ZipFile, fmt: str) -> None:
        """Bind the open package and its format (a key of ``_LISTERS``)."""
        self._zf = zf
        self._fmt = fmt
        self.entries: list[str] = []
        self.sources: dict[str, str] = {}

    def streams(self) -> Iterator[BinaryIO]:
        """The pictures, one stream at a time."""
        found = _LISTERS[self._fmt](self._zf)
        asked: set[str] = set()
        while True:
            try:
                reference = next(found, None)
            except MemoryError:  # not a fact about the file: the whole pass fails, and the file is read again
                raise
            except Exception as exc:
                log.debug("the pictures of a document were not all found: %s", type(exc).__name__)
                return
            if reference is None:
                return
            src, entry = reference
            self.sources.setdefault(src, entry)
            if entry in asked:
                continue
            asked.add(entry)
            try:
                stream = self._zf.open(entry)
            except MemoryError:
                raise
            except Exception as exc:  # no such entry, an encrypted one, a compression zipfile does not read
                log.debug("a picture inside a document could not be opened: %s", type(exc).__name__)
                continue
            self.entries.append(entry)
            yield cast(BinaryIO, stream)


def _picture_text(engine: OcrEngine, src: Path, fmt: str) -> tuple[dict[str, object], bool]:
    """(What the filter needs to place the text of the pictures of ``src``, empty when none holds text;
    whether a limit left pictures unread.)

    What is placed is ``{"pictures": {Image.src: key}, "text": {key: lines}}``, where a picture's key is the
    sha256 of its bytes: a picture stored under several names, or used several times, has one key and is
    printed once.  The lines are as read (``escape=False``): the filter builds them as text pandoc escapes.

    The pictures are read by ``image._read_pictures``: streamed out of the ZIP one at a time, within its
    count and byte limits, in a folder made beside ``src``, within ``_DOCUMENT_BUDGET_S`` seconds.

    Raises OcrError when the helper left a picture unread: a page that kept the other pictures' text would
    look complete."""
    try:
        zf = zipfile.ZipFile(src)
    except MemoryError:
        raise
    except Exception as exc:  # pandoc reads the container its own way: the document may still convert
        log.debug("a document was not opened to look for its pictures: %s", type(exc).__name__)
        return {}, False
    with zf:
        found = _Pictures(zf, fmt)
        streams = found.streams()
        got = _read_pictures(engine, streams, work_dir=src.parent, budget_s=_DOCUMENT_BUDGET_S, escape=False)
        if got.unread:  # _read_pictures has logged how many, and the helper's reason
            raise OcrError("the OCR helper left pictures unread")
        digests = dict(zip(found.entries, got.digests, strict=True))
        cut = got.over_bytes or _raster_left(streams)
    pictures = {
        source: digest
        for source, entry in sorted(found.sources.items())
        if (digest := digests.get(entry)) is not None and digest in got.lines
    }
    if not pictures:
        return {}, cut
    return {"pictures": pictures, "text": {key: got.lines[key] for key in set(pictures.values())}}, cut


class PandocConverter:
    """Docx/odt/rtf/html via the pypandoc_binary-bundled pandoc, invoked by ABSOLUTE path.

    ``pandoc -f <fmt> -t gfm --wrap=none``; media are not extracted inline (figures are a later tier).
    Merged cells make pandoc emit HTML tables: accepted (greppable).  An encrypted/IRM docx (not a ZIP, or
    EncryptedPackage stream) raises UnreadableSourceError.  version() runs ``<pandoc> --version``.

    With an OCR engine the converter claims ``.docx`` and ``.odt`` only, and the text read in a picture
    follows the top-level block that shows it, under ``[text in the image above, read by on-device OCR
    (Apple Vision):]``, once per distinct picture.  When the engine fails, ``convert`` raises OcrError with
    fixed wording and ``convert_file`` converts the file without OCR.
    """

    converter_id = "pandoc-gfm"
    extensions: tuple[str, ...] = (".docx", ".odt", ".rtf", ".html", ".htm")

    def __init__(self, cfg: ConvertConfig, ocr: OcrEngine | None = None) -> None:
        """Bind converter options from config, and the cycle's OCR engine when there is one.  With one,
        ``extensions`` is ``.docx`` and ``.odt``: the formats whose pictures it reads."""
        self._cfg = cfg
        self._runner = _PandocRunner(cfg.pandoc_path)
        self._ocr = ocr
        if ocr is not None:
            self.extensions = _PICTURED

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version); with an OCR engine its
        identity comes last.  Without one this is the version from before OCR existed."""
        version = f"{_EMITTER_VERSION}+pandoc-{self._runner.version()}"
        return version if self._ocr is None else f"{version}+{self._ocr.identity}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash; the OCR ones only with an engine."""
        opts = {**_pandoc_options(self._runner), "max_page_bytes": self._cfg.max_page_bytes}
        if self._ocr is not None:
            opts.update(_OCR_OPTIONS)
            opts["ocr_pandoc_rules"] = _OCR_RULES
        return opts

    def outdated(self, produced: str, reason: str | None = None) -> bool:
        """True when the page this converter wrote under version ``produced`` is worth reading the file
        again for: it was written without OCR and there is an engine now.  Never for a stub (``reason`` is
        the stub's), and never without an engine: the converter of ``.rtf`` and ``.html`` has none."""
        return reason is None and self._ocr is not None and _read_without_ocr(produced)

    def _with_picture_text(self, engine: OcrEngine, src: Path, fmt: str, name: str) -> tuple[str, int, bool]:
        """(GFM text of ``src`` with the text of its pictures, how many pictures' text is in it, whether a
        limit left pictures unread.)

        Whatever goes wrong once OCR is in play is one OcrError: the reading, and pandoc when it was handed
        text to place (it may well convert the file without).  What went wrong goes to the log, never into a
        page or a reason: convert_file converts the file again without OCR.  ``name`` only labels that log
        line.  A file none of whose pictures holds text is converted as without an engine, and pandoc's own
        failure on it is pandoc's."""
        try:
            text, cut = _picture_text(engine, src, fmt)
            if text:
                body, placed = self._runner.to_gfm_with_picture_text(src, fmt, text)
                return body, placed, cut
        except Exception as exc:  # OcrError, pandoc's ConversionError, or whatever else the pass let through
            detail = str(exc) if isinstance(exc, OcrError) else type(exc).__name__
            log.warning("%s: on-device OCR failed: %s", name, detail)
            raise OcrError(_OCR_FAILED) from None
        return self._runner.to_gfm(src, fmt), 0, cut

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors.

        With an OCR engine the helper works in a folder made beside ``src``, and OcrError (fixed wording)
        means OCR failed on a docx or odt that may convert without it."""
        suffix = Path(name.lower()).suffix
        fmt = _FORMATS.get(suffix)
        if fmt is None:
            raise ConversionError(f"pandoc-gfm does not handle {suffix or 'files without an extension'}")
        if fmt == "docx":
            _check_ooxml_container(src)
        elif fmt == "odt":
            _check_odf_container(src)
        placed, cut = 0, False
        if fmt == "html":
            body = self._runner.html_to_gfm(_decode_html(src.read_bytes()))
        elif self._ocr is not None and fmt in _LISTERS:
            body, placed, cut = self._with_picture_text(self._ocr, src, fmt, name)
        else:
            body = self._runner.to_gfm(src, fmt)
        if not body.strip():
            body = _EMPTY_PLACEHOLDER + "\n"
        kind = {"docx": "Word document", "odt": "OpenDocument text", "rtf": "RTF document"}.get(
            fmt, "HTML page"
        )
        # Counts only, before the headings: a summary is front matter, above the banner, and never holds
        # text OCR read.  (No heading is: pandoc escapes a ``#`` it did not write.)
        described = kind
        if placed:
            described += "; " + _PICTURES_READ.format(placed)
        if cut:
            described += "; " + _PICTURES_CUT
        # A title is the document's own.  Without a heading it is the first line, and that is looked for
        # above the first picture's text: never a head line, nor a line OCR read.
        head = _HEAD_RE.search(body) if placed else None
        own = body if head is None else body[: head.start()]
        title = _title_from_markdown(body, _first_line(own) or f"Untitled {kind}")
        summary = _summary_from_markdown(described, body)
        body, sidecars = _cap_body(body, self._cfg.max_page_bytes, sidecar_name=_FULL_TEXT_SIDECAR)
        unit = make_unit(
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
        )
        return (unit,)


class _PandocWithoutOcr(PandocConverter):
    """``PandocConverter`` for the formats on-device OCR reads no picture in: ``.rtf``, ``.html``, ``.htm``.

    ``Registry.default`` registers it beside a converter built with an engine, which claims ``.docx`` and
    ``.odt`` only.  A file of these formats therefore keeps the version, the options and the action key it
    has on a Mac without an engine, and is not converted again when an engine arrives or its time runs out.
    """

    extensions: tuple[str, ...] = tuple(ext for ext in PandocConverter.extensions if ext not in _PICTURED)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config; never an engine."""
        super().__init__(cfg)
