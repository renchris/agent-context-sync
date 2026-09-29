"""The converter protocol and helpers every converter uses (owner: convert)."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from agentsync.model import RenderedUnit, UnitKind

OptionValue = str | int | float | bool

_WS_RE = re.compile(r"\s+")
_MAX_LABEL_CHARS = 240  # title / summary are single-line frontmatter values; keep them short and stable


class Converter(Protocol):
    """One format converter.  Must be deterministic by construction (the cache makes it safe if not)."""

    converter_id: str  # stable, lowercase, e.g. "pandoc-gfm"; part of the action key
    extensions: tuple[str, ...]  # lower-case suffixes incl. dot, e.g. (".docx", ".odt")

    def version(self) -> str:
        """Version AS RUN: ``<emitter semver>+<tool>-<tool version>`` (e.g. by running ``pandoc
        --version``)."""
        ...

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options that can change output; hashed into ``options_hash``."""
        ...

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert the staged file ``src`` (original file name ``name``) into one or more units.

        Pre: ``src`` is a regular, fully materialised staging copy.  Post: units sorted by index; bodies are
        NFC text ending in exactly one newline, containing no wall-clock or run-specific value.
        Raises UnreadableSourceError (encrypted/password/IRM) or ConversionError (anything else).
        """
        ...


def estimate_tokens(text: str) -> int:
    """Return a deterministic token estimate for ``text`` (ceil(chars / 4))."""
    return math.ceil(len(text) / 4)


def options_hash(options: Mapping[str, OptionValue]) -> str:
    """Return ``"sha256:" + sha256(json.dumps(options, sort_keys=True, separators=(",", ":")))``."""
    blob = json.dumps(dict(options), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def rendered_sha256(body: str) -> str:
    """Return H2: hex sha256 of ``body.encode("utf-8")``."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _normalise_body(body: str) -> str:
    """NFC, LF line endings, no leading blank lines, exactly one trailing newline."""
    text = unicodedata.normalize("NFC", body.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.lstrip("\n").rstrip("\n")
    return text + "\n"


def _normalise_label(text: str) -> str:
    """One line, NFC, collapsed whitespace, capped at ``_MAX_LABEL_CHARS`` (ellipsis when cut)."""
    one = _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()
    if len(one) > _MAX_LABEL_CHARS:
        one = one[: _MAX_LABEL_CHARS - 1].rstrip() + "…"
    return one


def make_unit(
    *,
    unit_id: str,
    kind: UnitKind,
    index: int,
    of: int,
    name: str,
    file_stem: str,
    title: str,
    summary: str,
    body: str,
    sidecars: tuple[tuple[str, bytes], ...] = (),
) -> RenderedUnit:
    """Build a RenderedUnit, normalising ``body`` (NFC, LF, single trailing newline) and computing H2 +
    tokens."""
    text = _normalise_body(body)
    return RenderedUnit(
        unit_id=unit_id,
        kind=kind,
        index=index,
        of=of,
        name=unicodedata.normalize("NFC", name),
        file_stem=unicodedata.normalize("NFC", file_stem),
        title=_normalise_label(title),
        summary=_normalise_label(summary),
        tokens_estimate=estimate_tokens(text),
        body=text,
        rendered_sha256=rendered_sha256(text),
        sidecars=tuple(sidecars),
    )
