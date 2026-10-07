"""L3 conversion: registry, write-once cache, per-format converters (owner: convert)."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path

from agentsync.convert.base import Converter, options_hash
from agentsync.convert.cache import ConverterCache, action_key
from agentsync.convert.registry import Registry
from agentsync.errors import UnreadableSourceError
from agentsync.model import ConversionResult, ConversionStatus, RenderedUnit

log = logging.getLogger(__name__)

NO_CONVERTER_PREFIX = "no converter for "
"""How the reason of a file no converter claims starts (``convert_file``; the stub ``publish`` writes for
such a file says the same).  The cycle goes by it to find the files a converter registered since can read."""

_NO_CONVERTER_ID = "none"
_NO_CONVERTER_VERSION = "0"
_REASON_MAX = 300


def _suffix(name: str) -> str:
    """Lower-case suffix as SourceItem.suffix computes it (``.teams.json`` kept whole)."""
    low = name.lower()
    if low.endswith(".teams.json") and len(low) > len(".teams.json"):
        return ".teams.json"
    dot = low.rfind(".")
    return low[dot:] if dot > 0 else ""


def _reason(exc: BaseException) -> str:
    """One-line, bounded, deterministic reason text for a stub page."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= _REASON_MAX else text[: _REASON_MAX - 1] + "…"


def _validate(units: tuple[RenderedUnit, ...]) -> str | None:
    """Return why a converter's output breaks the protocol postconditions, or None when it is sound."""
    if not units:
        return "converter returned no units"
    ids = [u.unit_id for u in units]
    if len(set(ids)) != len(ids):
        return "converter returned duplicate unit ids"
    if [u.index for u in units] != sorted(u.index for u in units):
        return "converter returned units out of index order"
    for u in units:
        if u.of != len(units):
            return f"unit {u.unit_id}: of={u.of} but {len(units)} units"
        if not u.body.endswith("\n") or u.body.endswith("\n\n"):
            return f"unit {u.unit_id}: body must end in exactly one newline"
        if not u.body.strip():
            return f"unit {u.unit_id}: empty body"
        if hashlib.sha256(u.body.encode("utf-8")).hexdigest() != u.rendered_sha256:
            return f"unit {u.unit_id}: rendered_sha256 does not match the body"
    return None


def _result(
    status: ConversionStatus,
    *,
    converter_id: str,
    version: str,
    opts_hash: str,
    key: str,
    content_sha256: str,
    canonical_sha256: str,
    units: tuple[RenderedUnit, ...] = (),
    reason: str | None = None,
) -> ConversionResult:
    """Assemble a ConversionResult (non-OK results carry no units: publish renders the stub page)."""
    return ConversionResult(
        status=status,
        converter_id=converter_id,
        converter_version=version,
        options_hash=opts_hash,
        action_key=key,
        content_sha256=content_sha256,
        canonical_sha256=canonical_sha256,
        units=units,
        reason=reason,
    )


def _identity(conv: Converter, name: str) -> tuple[str, str]:
    """(converter_version, options_hash) for converting ``name``; may raise (e.g. pandoc missing).

    The input suffix is folded into the effective options: the action key has no file-name component, and a
    converter may render the same bytes differently per suffix (``.csv`` table vs ``.txt`` fence), so the
    suffix must be part of the producer identity.  Nothing else of the name may influence output.
    """
    return conv.version(), options_hash({**conv.options(), "input_suffix": _suffix(name)})


def convert_file(
    src: Path,
    *,
    name: str,
    content_sha256: str,
    canonical_sha256: str,
    registry: Registry,
    cache: ConverterCache,
) -> ConversionResult:
    """Convert one staged file through the cache; never raises for a per-file failure.

    No converter -> status REFUSED ("no converter for <suffix>"). Cache hit on the action key -> cached result
    (``from_cache=True``). Converter raises UnreadableSourceError -> UNREADABLE; any other exception -> FAILED
    with the reason; OK results are stored write-once. Units never empty for OK.

    OCR never fails a document that converts without it. When a converter of a registry built with an OCR
    engine raises OcrError and ``registry.without_ocr`` has the same converter, the file is converted again
    through that registry and its result is the one returned: the page, the version and the action key of a
    Mac without an engine, cached under that key. Nothing the engine said reaches the result. A converter
    with no such twin (an image has no converter without an engine) fails as any other does.
    """
    conv = registry.for_name(name)
    if conv is None:
        suffix = _suffix(name)
        opts = options_hash({})
        return _result(
            ConversionStatus.REFUSED,
            converter_id=_NO_CONVERTER_ID,
            version=_NO_CONVERTER_VERSION,
            opts_hash=opts,
            key=action_key(
                converter_id=_NO_CONVERTER_ID,
                converter_version=_NO_CONVERTER_VERSION,
                options_hash=opts,
                canonical_sha256=canonical_sha256,
            ),
            content_sha256=content_sha256,
            canonical_sha256=canonical_sha256,
            reason=NO_CONVERTER_PREFIX + (suffix or "files without an extension"),
        )
    try:
        version, opts = _identity(conv, name)
    except Exception as exc:
        log.warning("converter %s unavailable for %s: %s", conv.converter_id, name, _reason(exc))
        opts = options_hash({})
        return _result(
            ConversionStatus.FAILED,
            converter_id=conv.converter_id,
            version="unavailable",
            opts_hash=opts,
            key=action_key(
                converter_id=conv.converter_id,
                converter_version="unavailable",
                options_hash=opts,
                canonical_sha256=canonical_sha256,
            ),
            content_sha256=content_sha256,
            canonical_sha256=canonical_sha256,
            reason=f"conversion failed: converter unavailable: {_reason(exc)}",
        )
    key = action_key(
        converter_id=conv.converter_id,
        converter_version=version,
        options_hash=opts,
        canonical_sha256=canonical_sha256,
    )

    def make(
        status: ConversionStatus, *, units: tuple[RenderedUnit, ...] = (), reason: str | None = None
    ) -> ConversionResult:
        return _result(
            status,
            converter_id=conv.converter_id,
            version=version,
            opts_hash=opts,
            key=key,
            content_sha256=content_sha256,
            canonical_sha256=canonical_sha256,
            units=units,
            reason=reason,
        )

    # Label refusals are decided BEFORE the cache: H1 ignores the label parts, so a cached copy of the same
    # content must never be served past an excluded label.
    refusal = _label_refusal(registry, src, name)
    if refusal is not None:
        return make(ConversionStatus.REFUSED, reason=refusal)
    try:
        cached = cache.get(key)
    except Exception as exc:  # a cache fault must never fail the file; convert instead
        log.warning("converter cache read failed for %s: %s", key, _reason(exc))
        cached = None
    if cached is not None:
        # The key is content-addressed on H1, so the cached output also describes these bytes; report the
        # caller's content hash (a touched-not-changed re-save has new bytes with the same H1).
        return dataclasses.replace(cached, content_sha256=content_sha256)
    try:
        units = tuple(conv.convert(src, name=name))
    except UnreadableSourceError as exc:
        result = make(ConversionStatus.UNREADABLE, reason=_reason(exc))
    except Exception as exc:
        # Imported here: ``python -m agentsync.convert.ocr`` (scripts/install.sh) imports this package first,
        # and must not find the module it is about to run already imported.
        from agentsync.convert.ocr import OcrError  # noqa: PLC0415

        plain = registry.without_ocr if isinstance(exc, OcrError) else None
        twin = plain.for_name(name) if plain is not None else None
        if plain is not None and twin is not None and twin.converter_id == conv.converter_id:
            log.info("%s: on-device OCR failed; converted by %s without it", name, conv.converter_id)
            return convert_file(
                src,
                name=name,
                content_sha256=content_sha256,
                canonical_sha256=canonical_sha256,
                registry=plain,
                cache=cache,
            )
        log.info("conversion of %s by %s failed: %s", name, conv.converter_id, _reason(exc))
        return make(ConversionStatus.FAILED, reason=f"conversion failed: {_reason(exc)}")
    else:
        problem = _validate(units)
        if problem is not None:
            log.warning("converter %s broke its contract on %s: %s", conv.converter_id, name, problem)
            return make(ConversionStatus.FAILED, reason=f"conversion failed: {problem}")
        result = make(ConversionStatus.OK, units=units)
    try:
        cache.put(result)
    except Exception as exc:  # disk full, permissions: the result is still good for this cycle
        log.warning("converter cache write failed for %s: %s", key, _reason(exc))
    return result


def _label_refusal(registry: Registry, src: Path, name: str) -> str | None:
    """The policy-refusal reason when ``[policy]`` label rules are active and refuse ``src`` (else None).

    Encryption stays with the converter guard (its UNREADABLE result is cached); only REFUSED screenings are
    decided here, uncached, because the action key cannot see a relabel (C15 section 9 items 27 and 29).
    """
    content_policy = getattr(registry, "policy", None)
    screen = getattr(registry, "screen", None)
    if content_policy is None or not getattr(content_policy, "labels_active", False) or screen is None:
        return None
    try:
        screening = screen(src, name=name)
    except OSError:
        return None  # unreadable bytes: the converter reports it the same way
    if screening is None or str(getattr(screening, "status", "")) != "refused":
        return None
    return str(screening.reason)


def _fingerprint(conv: Converter, src: Path, name: str) -> tuple[object, ...]:
    """What one uncached run produced: unit ids + H2s + sidecar digests, or the error it raised."""
    try:
        units = conv.convert(src, name=name)
    except Exception as exc:
        return ("error", type(exc).__name__, _reason(exc))
    return tuple(
        (u.unit_id, u.rendered_sha256, tuple((n, hashlib.sha256(d).hexdigest()) for n, d in u.sidecars))
        for u in units
    )


def double_conversion_differs(src: Path, *, name: str, registry: Registry) -> bool:
    """Convert twice without the cache; True when any unit's rendered_sha256 differs (land-gate lint)."""
    conv = registry.for_name(name)
    if conv is None:
        return False
    first = _fingerprint(conv, src, name)
    second = _fingerprint(conv, src, name)
    if first != second:
        log.warning("NONDETERMINISTIC: %s differs between two runs of %s", name, conv.converter_id)
    return first != second


__all__ = ["NO_CONVERTER_PREFIX", "ConverterCache", "Registry", "convert_file", "double_conversion_differs"]
