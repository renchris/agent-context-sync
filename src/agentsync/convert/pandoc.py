"""Converter: docx/odt/rtf/html via the pypandoc_binary-bundled pandoc, invoked by ABSOLUTE path (owner:
convert)."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

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
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

log = logging.getLogger(__name__)

_EMITTER_VERSION = "1.0.0"
_TIMEOUT_S = 300.0
_VERSION_RE = re.compile(r"^pandoc(?:\.exe)?\s+(\S+)", re.MULTILINE)
_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_EMPTY_PLACEHOLDER = "[empty document]"

_FORMATS: dict[str, str] = {".docx": "docx", ".odt": "odt", ".rtf": "rtf", ".html": "html", ".htm": "html"}

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
_LUA_FILTER = r"""
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
return { figures, rest }
"""


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
            out = self._run(["--version"], None)
            m = _VERSION_RE.search(out)
            if not m:
                raise ConversionError(f"cannot parse `pandoc --version` output: {out[:80]!r}")
            self._version = m.group(1)
        return self._version

    def _run(self, args: Sequence[str], workdir: Path | None) -> str:
        """Run pandoc with ``args`` in a scrubbed environment; return stdout; raise ConversionError."""
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
        if proc.stderr:
            log.debug("pandoc stderr: %s", proc.stderr.decode("utf-8", errors="replace").strip()[:500])
        return proc.stdout.decode("utf-8", errors="replace")

    def to_gfm(self, src: Path, fmt: str) -> str:
        """Convert the file ``src`` (pandoc reader ``fmt``) to GFM text (NFC, LF)."""
        with tempfile.TemporaryDirectory(prefix="agentsync-pandoc-job-") as tmp:
            work = Path(tmp)
            (work / "data").mkdir()
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
            self._run(args, work)
            text = out.read_text(encoding="utf-8", errors="replace")
        return unicodedata.normalize("NFC", text.replace("\r\n", "\n"))

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


class PandocConverter:
    """Docx/odt/rtf/html via the pypandoc_binary-bundled pandoc, invoked by ABSOLUTE path.

    ``pandoc -f <fmt> -t gfm --wrap=none``; media are not extracted inline (figures are a later tier).
    Merged cells make pandoc emit HTML tables: accepted (greppable).  An encrypted/IRM docx (not a ZIP, or
    EncryptedPackage stream) raises UnreadableSourceError.  version() runs ``<pandoc> --version``.
    """

    converter_id = "pandoc-gfm"
    extensions: tuple[str, ...] = (".docx", ".odt", ".rtf", ".html", ".htm")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg
        self._runner = _PandocRunner(cfg.pandoc_path)

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+pandoc-{self._runner.version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {**_pandoc_options(self._runner), "max_page_bytes": self._cfg.max_page_bytes}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        suffix = Path(name.lower()).suffix
        fmt = _FORMATS.get(suffix)
        if fmt is None:
            raise ConversionError(f"pandoc-gfm does not handle {suffix or 'files without an extension'}")
        if fmt == "docx":
            _check_ooxml_container(src)
        elif fmt == "odt":
            _check_odf_container(src)
        if fmt == "html":
            body = self._runner.html_to_gfm(_decode_html(src.read_bytes()))
        else:
            body = self._runner.to_gfm(src, fmt)
        if not body.strip():
            body = _EMPTY_PLACEHOLDER + "\n"
        kind = {"docx": "Word document", "odt": "OpenDocument text", "rtf": "RTF document"}.get(
            fmt, "HTML page"
        )
        title = _title_from_markdown(body, _first_line(body) or f"Untitled {kind}")
        summary = _summary_from_markdown(kind, body)
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
