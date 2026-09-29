"""Converter registry: routes a file name to exactly one converter by extension (owner: convert)."""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType

from agentsync.config import ConvertConfig
from agentsync.convert.base import Converter


class Registry:
    """An immutable extension -> converter map."""

    __slots__ = ("_by_ext", "_converters", "_longest_first")

    def __init__(self, converters: Sequence[Converter]) -> None:
        """Build the map; raises ValueError if two converters claim one extension."""
        by_ext: dict[str, Converter] = {}
        for conv in converters:
            for ext in conv.extensions:
                if not ext.startswith(".") or ext != ext.lower():
                    raise ValueError(
                        f"{conv.converter_id}: extension {ext!r} must be lower-case and start with '.'"
                    )
                if ext in by_ext:
                    raise ValueError(
                        f"extension {ext} claimed by both {by_ext[ext].converter_id} and {conv.converter_id}"
                    )
                by_ext[ext] = conv
        self._by_ext = MappingProxyType(by_ext)
        self._converters = tuple(sorted(converters, key=lambda c: c.converter_id))
        self._longest_first = tuple(sorted(by_ext, key=lambda e: (-len(e), e)))

    @classmethod
    def default(cls, cfg: ConvertConfig) -> Registry:
        """Registry of every built-in converter (pandoc, xlsx, pptx, pdf, markdown, text, eml, teams)."""
        from agentsync.convert.eml import EmlConverter  # noqa: PLC0415 - keep registry import-light
        from agentsync.convert.markdown import MarkdownConverter  # noqa: PLC0415
        from agentsync.convert.pandoc import PandocConverter  # noqa: PLC0415
        from agentsync.convert.pdf import PdfConverter  # noqa: PLC0415
        from agentsync.convert.pptx import PptxConverter  # noqa: PLC0415
        from agentsync.convert.teams import TeamsMonthConverter  # noqa: PLC0415
        from agentsync.convert.text import PlainTextConverter  # noqa: PLC0415
        from agentsync.convert.xlsx import XlsxConverter  # noqa: PLC0415

        return cls(
            [
                PandocConverter(cfg),
                XlsxConverter(cfg),
                PptxConverter(cfg),
                PdfConverter(cfg),
                MarkdownConverter(cfg),
                PlainTextConverter(cfg),
                EmlConverter(cfg),
                TeamsMonthConverter(cfg),
            ]
        )

    def for_name(self, name: str) -> Converter | None:
        """Return the converter for a file name by its lower-case suffix (``.teams.json`` matched whole)."""
        low = name.lower()
        for ext in self._longest_first:
            if low.endswith(ext) and len(low) > len(ext):
                return self._by_ext[ext]
        return None

    def converters(self) -> tuple[Converter, ...]:
        """All registered converters, sorted by converter_id."""
        return self._converters

    def extensions(self) -> tuple[str, ...]:
        """All claimed extensions, sorted."""
        return tuple(sorted(self._by_ext))
