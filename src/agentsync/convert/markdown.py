"""Converter: markdown passthrough: the source's own frontmatter is stripped (kept as a fenced yaml block)
(owner: convert)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from agentsync.config import ConvertConfig
from agentsync.convert._common import (
    _FULL_TEXT_SIDECAR,
    _cap_body,
    _decode_text,
    _fence,
    _first_line,
    _python_version,
    _summary_from_markdown,
    _title_from_markdown,
    _unicode_version,
)
from agentsync.convert.base import OptionValue, make_unit
from agentsync.errors import ConversionError
from agentsync.model import RenderedUnit, UnitKind

_EMITTER_VERSION = "1.0.0"
_FM_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a leading ``---`` frontmatter block (closed by ``---`` or ``...``) from the markdown."""
    if not text.startswith("---\n"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, text  # unterminated: not frontmatter, keep verbatim


def _strip_quotes(value: str) -> str:
    """Remove one level of YAML scalar quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


class MarkdownConverter:
    """Markdown passthrough: the source's own frontmatter is stripped (kept as a fenced yaml block).

    Body = source markdown normalised to NFC/LF; an existing leading frontmatter block is moved into a
    fenced ``yaml`` block under a ``Source frontmatter`` heading so the mirror frontmatter stays the contract.
    """

    converter_id = "markdown-passthrough"
    extensions: tuple[str, ...] = (".md", ".markdown")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""
        self._cfg = cfg

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""
        return f"{_EMITTER_VERSION}+python-{_python_version()}+unicode-{_unicode_version()}"

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""
        return {"frontmatter": "fenced-yaml-top", "max_page_bytes": self._cfg.max_page_bytes}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise ConversionError(f"cannot read: {exc}") from exc
        text = _decode_text(data)
        fm, md = _split_frontmatter(text)
        md = md.strip("\n")
        parts: list[str] = []
        fallback = _first_line(md) or "Untitled markdown document"
        if fm is not None:
            parts += ["## Source frontmatter", "", _fence(fm + "\n" if fm else "", "yaml").rstrip("\n"), ""]
            m = _FM_TITLE_RE.search(fm)
            if m:
                fallback = _strip_quotes(m.group(1)) or fallback
        parts.append(md if md.strip() else "[empty document]")
        body = "\n".join(parts)
        title = _title_from_markdown(md, fallback)
        summary = _summary_from_markdown("Markdown document", md)
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
