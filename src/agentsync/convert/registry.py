"""Converter registry: routes a file name to exactly one converter by extension (owner: convert; the
pre-conversion policy guard and the untrusted-content banner are owned by controls, see ``policy.py``).

``Registry.default`` wraps every converter in a guard that, before the converter runs, screens the staged
bytes (encrypted Office container -> ``encrypted-office``, encrypted PDF -> ``encrypted-pdf``, excluded or
missing sensitivity label -> ``refused: …``; all raised as UnreadableSourceError so the result is an
UNREADABLE stub that is cached and settled, never retried), maps pdfminer's password/encryption errors to
``encrypted-pdf``, turns output that is empty after stripping whitespace and form feeds into an UNREADABLE
stub (C15 §9 #26), and prefixes every unit body with ``policy.UNTRUSTED_BANNER`` so H2 covers the banner.

The registry never looks for an OCR engine: the cycle resolves one and hands it in (``ocr=``).  Without one
the default registry is the one from before OCR existed, converter for converter and version for version.
With one it also keeps that registry (``without_ocr``): OCR may never fail a document that converts without
it, so ``convert_file`` converts a file the engine failed on with the converter that has no engine.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from agentsync import policy as policy_mod
from agentsync.config import ConvertConfig
from agentsync.convert.base import Converter, OptionValue, estimate_tokens, rendered_sha256
from agentsync.errors import UnreadableSourceError
from agentsync.model import RenderedUnit
from agentsync.policy import PolicyConfig, Screening

if TYPE_CHECKING:
    from agentsync.convert.media import MediaEngine
    from agentsync.convert.ocr import OcrEngine
    from agentsync.convert.pieces import PieceStore
    from agentsync.convert.speech import SpeechEngine

_PDF_ENCRYPTION_ERRORS = frozenset({"PDFPasswordIncorrect", "PDFEncryptionError"})

SIDECAR_DIGEST_VERSION = 1
"""Bumped when the sidecar digest footer changes (it is in the guard's options, hence in every action key)."""

SIDECAR_DIGEST_PREFIX = "Sidecar file "
"""Start of each footer line naming one sidecar and its sha256: the page body (so H2) covers the sidecars."""

_TEXT_SIDECAR_SUFFIXES = (".txt", ".md")


def sidecar_digest_lines(body: str) -> list[tuple[str, str]]:
    """(sidecar name, sha256) pairs of a page body's sidecar footer (``_pages_intact`` verifies them)."""
    out: list[tuple[str, str]] = []
    for line in body.splitlines():
        if line.startswith(SIDECAR_DIGEST_PREFIX + "`"):
            name, sep, rest = line[len(SIDECAR_DIGEST_PREFIX) + 1 :].partition("`")
            digest = rest.strip().removeprefix("sha256 ").strip().rstrip(".")
            if sep and len(digest) == 64:
                out.append((name, digest))
    return out


def _with_sidecar_digests(unit: RenderedUnit) -> RenderedUnit:
    """Banner text sidecars, then list every sidecar's sha256 at the end of the body.

    A capped page (rows past ``max_rows_per_sheet``, text past ``max_page_bytes``) keeps the same body when
    only the capped-away part changes; the footer makes the H2 early cutoff see the sidecar change, so the
    stale sidecar in docs/ is rewritten and curated pins go STALE (review correctness-h2-cutoff)."""
    if not unit.sidecars:
        return unit
    sidecars: list[tuple[str, bytes]] = []
    banner = policy_mod.UNTRUSTED_BANNER.encode("utf-8")
    for name, raw in unit.sidecars:
        text_sidecar = name.lower().endswith(_TEXT_SIDECAR_SUFFIXES) and not raw.startswith(banner)
        sidecars.append((name, banner + b"\n\n" + raw if text_sidecar else raw))
    footer = "\n".join(
        f"{SIDECAR_DIGEST_PREFIX}`{name}` sha256 {hashlib.sha256(data).hexdigest()}"
        for name, data in sorted(sidecars)
    )
    body = unit.body.rstrip("\n") + "\n\n" + footer + "\n"
    return dataclasses.replace(
        unit,
        body=body,
        sidecars=tuple(sidecars),
        rendered_sha256=rendered_sha256(body),
        tokens_estimate=estimate_tokens(body),
    )


def _bannered(unit: RenderedUnit) -> RenderedUnit:
    body = policy_mod.with_banner(unit.body)
    if body == unit.body:
        return unit
    return dataclasses.replace(
        unit, body=body, rendered_sha256=rendered_sha256(body), tokens_estimate=estimate_tokens(body)
    )


class _GuardedConverter:
    """A converter behind the policy screen (and, for the default registry, the untrusted-content banner)."""

    __slots__ = ("_banner", "_inner", "_policy", "converter_id", "extensions")

    def __init__(self, inner: Converter, policy: PolicyConfig, *, banner: bool) -> None:
        self._inner = inner
        self._policy = policy
        self._banner = banner
        self.converter_id = inner.converter_id
        self.extensions = inner.extensions

    @property
    def inner(self) -> Converter:
        """The wrapped converter."""
        return self._inner

    def version(self) -> str:
        """The wrapped converter's version as run."""
        return self._inner.version()

    def options(self) -> Mapping[str, OptionValue]:
        """The wrapped options, plus the banner version and (when labels are active) the policy fingerprint,
        so a policy or banner change moves the action key and a cached result is never served across it."""
        opts: dict[str, OptionValue] = dict(self._inner.options())
        if self._banner:
            opts["untrusted_banner"] = policy_mod.BANNER_VERSION
            opts["sidecar_digest"] = SIDECAR_DIGEST_VERSION
        if self._policy.labels_active:
            opts["label_policy"] = self._policy.fingerprint()
        return opts

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Screen, convert, check for empty output, add the banner."""
        screening = policy_mod.screen_file(src, name=name, policy=self._policy)
        if screening is not None:
            raise UnreadableSourceError(screening.reason)
        try:
            units = tuple(self._inner.convert(src, name=name))
        except UnreadableSourceError:
            raise
        except Exception as exc:
            if type(exc).__name__ in _PDF_ENCRYPTION_ERRORS:
                raise UnreadableSourceError(policy_mod.ENCRYPTED_PDF_REASON) from exc
            raise
        if units and any(not u.body.strip() for u in units):
            raise UnreadableSourceError(policy_mod.EMPTY_OUTPUT_REASON)
        return tuple(_with_sidecar_digests(_bannered(u)) for u in units) if self._banner else units


class Registry:
    """An immutable extension -> converter map."""

    __slots__ = ("_by_ext", "_converters", "_longest_first", "_policy", "_without_ocr", "_without_speech")

    def __init__(
        self, converters: Sequence[Converter], *, policy: PolicyConfig | None = None, banner: bool = False
    ) -> None:
        """Build the map; raises ValueError if two converters claim one extension.

        With ``policy`` or ``banner`` every converter is wrapped in the pre-conversion guard (see module doc).
        """
        self._policy = policy if policy is not None else PolicyConfig()
        if policy is not None or banner:
            converters = [_GuardedConverter(c, self._policy, banner=banner) for c in converters]
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
        self._without_ocr: Registry | None = None
        self._without_speech: Registry | None = None

    @classmethod
    def default(
        cls,
        cfg: ConvertConfig,
        *,
        policy: PolicyConfig | None = None,
        ocr: OcrEngine | None = None,
        media: MediaEngine | None = None,
        pieces: PieceStore | None = None,
        speech: SpeechEngine | None = None,
    ) -> Registry:
        """Registry of every built-in converter (pandoc, xlsx, pptx, pdf, markdown, text, vtt, eml, teams),
        each behind the policy guard (``policy`` defaults to encryption detection only) and the banner.

        With ``ocr`` (the engine the cycle resolved) the pandoc, pptx and pdf converters read with it, and
        raster images get a converter too, ``image-ocr``, unless a ``[policy]`` label rule is active: an image
        can carry a sensitivity label the screen cannot read, so it then stays the ``no converter`` stub it is
        without an engine.  The pandoc converter with an engine claims ``.docx`` and ``.odt`` only; ``.rtf``
        and ``.html`` go to a second ``pandoc-gfm`` converter without one, so nothing about them changes.
        Such a registry keeps the one without an engine as ``without_ocr``.

        With ``ocr``, ``media`` (the media helper the cycle resolved) and ``[convert] recordings`` on, meeting
        recordings (``.mp4``, ``.m4v``, ``.mov``) get ``recording-av``, which reads its pieces from and into
        ``pieces`` (None: one pass, nothing kept), and hears it through ``speech`` (the speech engine the
        cycle resolved; None: screens only).  Unlike an image it stays registered under a label rule
        (ruling 2): processing stays on this Mac, and each index it writes then says the label was not
        checked.  ``without_ocr`` never holds it, so a recording the engines failed on gets the ``no
        converter`` refusal a later read looks for.  With ``speech`` such a registry keeps the one without it
        as ``without_speech``, so a recording the speech engine failed on keeps its screens."""
        from agentsync.convert.eml import EmlConverter  # noqa: PLC0415 - keep registry import-light
        from agentsync.convert.markdown import MarkdownConverter  # noqa: PLC0415
        from agentsync.convert.pandoc import PandocConverter, _PandocWithoutOcr  # noqa: PLC0415
        from agentsync.convert.pdf import PdfConverter  # noqa: PLC0415
        from agentsync.convert.pptx import PptxConverter  # noqa: PLC0415
        from agentsync.convert.teams import TeamsMonthConverter  # noqa: PLC0415
        from agentsync.convert.text import PlainTextConverter  # noqa: PLC0415
        from agentsync.convert.vtt import VttConverter  # noqa: PLC0415
        from agentsync.convert.xlsx import XlsxConverter  # noqa: PLC0415

        content_policy = policy if policy is not None else PolicyConfig()
        converters: list[Converter] = [
            PandocConverter(cfg, ocr=ocr),
            *([] if ocr is None else [_PandocWithoutOcr(cfg)]),
            XlsxConverter(cfg),
            PptxConverter(cfg, ocr=ocr),
            PdfConverter(cfg, ocr=ocr),
            MarkdownConverter(cfg),
            PlainTextConverter(cfg),
            VttConverter(cfg),
            EmlConverter(cfg),
            TeamsMonthConverter(cfg),
        ]
        if ocr is not None and not content_policy.labels_active:
            from agentsync.convert.image import ImageConverter  # noqa: PLC0415

            converters.append(ImageConverter(cfg, ocr))
        if ocr is not None and media is not None and cfg.recordings:
            from agentsync.convert.recording import RecordingConverter  # noqa: PLC0415

            converters.append(
                RecordingConverter(
                    cfg, ocr, media, pieces=pieces, label_rule=content_policy.labels_active, speech=speech
                )
            )
        registry = cls(converters, policy=content_policy, banner=True)
        if ocr is not None:
            registry._without_ocr = cls.default(cfg, policy=policy)
        if speech is not None:
            registry._without_speech = cls.default(cfg, policy=policy, ocr=ocr, media=media, pieces=pieces)
        return registry

    @property
    def policy(self) -> PolicyConfig:
        """The content policy this registry's guard enforces."""
        return self._policy

    @property
    def without_ocr(self) -> Registry | None:
        """The same registry built without an OCR engine, when this one was built with one
        (``Registry.default(..., ocr=engine)``); else None.

        Its converters are the ones a Mac without an engine has, version for version and option for option,
        behind the same policy guard.  A file converted through it gets the action key it would get there
        (plan D10): ``convert_file`` uses it for a file the engine failed on, and the cycle for every file
        once its OCR time is used up."""
        return self._without_ocr

    @property
    def without_speech(self) -> Registry | None:
        """The same registry built without the speech engine, when this one was built with one; else None.

        ``convert_file`` reads a recording the speech engine failed on through it (spec S8 Failure): the page
        keeps its screens, under a version without ``+asr-`` that ``RecordingConverter.outdated`` reads
        again once speech works."""
        return self._without_speech

    def screen(self, src: Path, *, name: str) -> Screening | None:
        """Pre-conversion screen of a staged file (call BEFORE the cache lookup; ``policy.screen_file``)."""
        return policy_mod.screen_file(src, name=name, policy=self._policy)

    def for_name(self, name: str) -> Converter | None:
        """Return the converter for a file name by its lower-case suffix (``.teams.json`` matched whole)."""
        low = name.lower()
        for ext in self._longest_first:
            if low.endswith(ext) and len(low) > len(ext):
                return self._by_ext[ext]
        return None

    def converters(self) -> tuple[Converter, ...]:
        """All registered converters, sorted by converter_id.  Two can share an id: with an OCR engine
        ``Registry.default`` holds ``pandoc-gfm`` twice, for the suffixes it reads pictures in and for the
        rest."""
        return self._converters

    def extensions(self) -> tuple[str, ...]:
        """All claimed extensions, sorted."""
        return tuple(sorted(self._by_ext))
