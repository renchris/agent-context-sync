"""convert_file / double_conversion_differs: routing, cache, status mapping, never-raise (fake converters)."""

from __future__ import annotations

import hashlib
import itertools
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from agentsync.convert import (
    NO_CONVERTER_PREFIX,
    ConverterCache,
    Registry,
    convert_file,
    double_conversion_differs,
)
from agentsync.convert.base import make_unit, options_hash
from agentsync.convert.cache import action_key
from agentsync.convert.media import MediaError
from agentsync.convert.ocr import OcrError
from agentsync.convert.recording import RecordingNotFinished
from agentsync.convert.speech import SpeechError
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.model import ConversionStatus, RenderedUnit, UnitKind

H1 = "c" * 64
CONTENT = "d" * 64


def _unit(body: str, index: int = 0, of: int = 1) -> RenderedUnit:
    return make_unit(
        unit_id="whole" if of == 1 else f"sheet:{index}",
        kind=UnitKind.WHOLE if of == 1 else UnitKind.SHEET,
        index=index,
        of=of,
        name="",
        file_stem="",
        title="t",
        summary="s",
        body=body,
    )


class Fake:
    """A scriptable converter; ``behaviour`` returns units or raises."""

    def __init__(
        self,
        behaviour: Callable[[Path, str], tuple[RenderedUnit, ...]],
        *,
        cid: str = "fake",
        exts: tuple[str, ...] = (".fk", ".fk2"),
        version: Callable[[], str] = lambda: "1.0.0+fake-1",
    ) -> None:
        self.converter_id = cid
        self.extensions = exts
        self._behaviour = behaviour
        self._version = version
        self.calls = 0

    def version(self) -> str:
        return self._version()

    def options(self) -> dict[str, int]:
        return {"k": 1}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        self.calls += 1
        return self._behaviour(src, name)


@pytest.fixture
def src(tmp_path: Path) -> Path:
    p = tmp_path / "staged.bin"
    p.write_bytes(b"bytes")
    return p


@pytest.fixture
def cache(tmp_path: Path) -> ConverterCache:
    return ConverterCache(tmp_path / "cache")


def _run(src: Path, reg: Registry, cache: ConverterCache, name: str = "doc.fk", content: str = CONTENT):
    return convert_file(
        src, name=name, content_sha256=content, canonical_sha256=H1, registry=reg, cache=cache
    )


def test_unknown_suffix_is_refused_not_cached(src: Path, cache: ConverterCache) -> None:
    reg = Registry([Fake(lambda s, n: (_unit("x\n"),))])
    r = _run(src, reg, cache, name="Thing.XYZ")
    assert r.status is ConversionStatus.REFUSED
    assert r.reason == "no converter for .xyz" == NO_CONVERTER_PREFIX + ".xyz"
    assert r.units == () and r.converter_id == "none"
    assert r.content_sha256 == CONTENT and r.canonical_sha256 == H1
    assert not (cache.root.exists() and any(cache.root.iterdir()))
    bare = _run(src, reg, cache, name="Makefile").reason
    assert bare == "no converter for files without an extension" and bare.startswith(NO_CONVERTER_PREFIX)


def test_ok_is_cached_write_once_and_served_from_cache(src: Path, cache: ConverterCache) -> None:
    fake = Fake(lambda s, n: (_unit("hello\n"),))
    reg = Registry([fake])
    first = _run(src, reg, cache)
    assert first.status is ConversionStatus.OK and not first.from_cache
    assert first.action_key == action_key(
        converter_id="fake",
        converter_version="1.0.0+fake-1",
        options_hash=options_hash({"k": 1, "input_suffix": ".fk"}),
        canonical_sha256=H1,
    )
    second = _run(src, reg, cache, content="e" * 64)
    assert fake.calls == 1
    assert second.from_cache is True
    assert second.units == first.units
    assert second.content_sha256 == "e" * 64  # the caller's bytes; same H1 => same output
    assert second.action_key == first.action_key


def test_suffix_is_part_of_the_producer_identity(src: Path, cache: ConverterCache) -> None:
    fake = Fake(lambda s, n: (_unit(f"{n[-3:]}\n"),))
    reg = Registry([fake])
    a = _run(src, reg, cache, name="a.fk")
    b = _run(src, reg, cache, name="a.fk2")
    c = _run(src, reg, cache, name="other-name.FK")
    assert a.action_key != b.action_key and a.options_hash != b.options_hash
    assert c.action_key == a.action_key and c.from_cache  # the name beyond its suffix is not in the key


def test_unreadable_is_cached_with_reason(src: Path, cache: ConverterCache) -> None:
    def boom(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise UnreadableSourceError("encrypted")

    fake = Fake(boom)
    reg = Registry([fake])
    r = _run(src, reg, cache)
    assert r.status is ConversionStatus.UNREADABLE and r.reason == "encrypted" and r.units == ()
    again = _run(src, reg, cache)
    assert again.from_cache and again.status is ConversionStatus.UNREADABLE
    assert fake.calls == 1


@pytest.mark.parametrize(
    "exc", [ConversionError("pandoc exited 64: bad"), ValueError("weird\n  multi-line"), RecursionError()]
)
def test_other_exceptions_fail_and_are_retried(src: Path, cache: ConverterCache, exc: Exception) -> None:
    def boom(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise exc

    fake = Fake(boom)
    reg = Registry([fake])
    r = _run(src, reg, cache)
    assert r.status is ConversionStatus.FAILED
    assert r.reason is not None and r.reason.startswith("conversion failed: ") and "\n" not in r.reason
    _run(src, reg, cache)
    assert fake.calls == 2  # FAILED is never cached


@pytest.mark.parametrize(
    ("units", "problem"),
    [
        ((), "no units"),
        ((_unit("a\n"), _unit("b\n")), "duplicate unit ids"),
        ((_unit("a\n", index=2, of=2), _unit("b\n", index=1, of=2)), "out of index order"),
        ((_unit("a\n", index=1, of=3),), "of=3"),
    ],
)
def test_contract_breaking_output_is_failed(
    src: Path, cache: ConverterCache, units: tuple[RenderedUnit, ...], problem: str
) -> None:
    r = _run(src, Registry([Fake(lambda s, n: units)]), cache)
    assert r.status is ConversionStatus.FAILED
    assert r.reason is not None and problem in r.reason


def test_tampered_unit_hash_is_failed(src: Path, cache: ConverterCache) -> None:
    good = _unit("a\n")
    bad = RenderedUnit(**{**{f: getattr(good, f) for f in RenderedUnit.__dataclass_fields__}, "body": "b\n"})
    assert _run(src, Registry([Fake(lambda s, n: (bad,))]), cache).status is ConversionStatus.FAILED


def test_unavailable_converter_is_failed_not_raised(src: Path, cache: ConverterCache) -> None:
    def no_version() -> str:
        raise ConversionError("bundled pandoc not found")

    r = _run(src, Registry([Fake(lambda s, n: (_unit("x\n"),), version=no_version)]), cache)
    assert r.status is ConversionStatus.FAILED
    assert r.reason == "conversion failed: converter unavailable: bundled pandoc not found"


def test_cache_write_failure_still_returns_ok(
    src: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    cache = ConverterCache(blocker / "cache")  # parent is a file: every cache write fails
    with caplog.at_level(logging.WARNING):
        r = _run(src, Registry([Fake(lambda s, n: (_unit("x\n"),))]), cache)
    assert r.status is ConversionStatus.OK
    assert "cache write failed" in caplog.text


# ---------------------------------------------------------------------------------------------------------
# an OCR failure is not the last word (plan D10)
# ---------------------------------------------------------------------------------------------------------

WITH_OCR = "1.0.0+fake-1+ocr-paper-vision-r2-h0.3.0-l1"


def _ocr_fails(s: Path, n: str) -> tuple[RenderedUnit, ...]:
    raise OcrError("on-device OCR failed")


def _reading(with_ocr: Fake, without: Fake | None) -> Registry:
    """A registry built with an engine, as ``Registry.default(..., ocr=engine)`` leaves one: it keeps the
    registry without an engine."""
    reg = Registry([with_ocr])
    reg._without_ocr = Registry([without] if without is not None else [])
    return reg


def test_an_ocr_failure_is_converted_again_without_ocr_under_the_key_of_no_engine(
    src: Path, cache: ConverterCache, caplog: pytest.LogCaptureFixture
) -> None:
    reading = Fake(_ocr_fails, version=lambda: WITH_OCR)
    plain = Fake(lambda s, n: (_unit("the page without OCR\n"),))
    with caplog.at_level(logging.INFO, logger="agentsync.convert"):
        got = _run(src, _reading(reading, plain), cache)
    assert got == _run(src, Registry([plain]), ConverterCache(cache.root.parent / "other-mac"))
    assert got.status is ConversionStatus.OK and got.reason is None and not got.from_cache
    assert got.converter_version == "1.0.0+fake-1" and got.action_key == action_key(
        converter_id="fake",
        converter_version="1.0.0+fake-1",
        options_hash=options_hash({"k": 1, "input_suffix": ".fk"}),
        canonical_sha256=H1,
    )
    assert caplog.messages == ["doc.fk: on-device OCR failed; converted by fake without it"]
    # The key with OCR holds nothing, so the next read tries OCR again; the page without it is served.
    again = _run(src, _reading(reading, plain), cache)
    assert again.from_cache and again.units == got.units and (reading.calls, plain.calls) == (2, 2)
    # Once the engine works, its result is stored under its own key.
    working = Fake(lambda s, n: (_unit("the page with OCR\n"),), version=lambda: WITH_OCR)
    read = _run(src, _reading(working, plain), cache)
    assert read.status is ConversionStatus.OK and read.converter_version == WITH_OCR and not read.from_cache
    assert read.action_key != got.action_key and plain.calls == 2


def test_what_the_converter_without_ocr_decides_is_the_result(src: Path, cache: ConverterCache) -> None:
    def no_text(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise UnreadableSourceError("no text layer")

    def broken(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise ConversionError("not a PDF")

    reading = Fake(_ocr_fails, version=lambda: WITH_OCR)
    stub = _run(src, _reading(reading, Fake(no_text)), cache)
    assert (stub.status, stub.reason, stub.converter_version) == (
        ConversionStatus.UNREADABLE,
        "no text layer",
        "1.0.0+fake-1",
    )
    assert _run(src, _reading(reading, Fake(no_text)), cache).from_cache, "a settled stub, cached"
    failed = _run(src, _reading(reading, Fake(broken)), cache, name="other.fk2")
    assert (failed.status, failed.reason, failed.converter_version) == (
        ConversionStatus.FAILED,
        "conversion failed: not a PDF",
        "1.0.0+fake-1",
    )


def test_an_ocr_failure_on_a_file_only_the_engine_reads_is_the_refusal_of_a_mac_without_one(
    src: Path, cache: ConverterCache, caplog: pytest.LogCaptureFixture
) -> None:
    """An image has no converter without an engine.  A failed conversion would be settled by the next
    cycle and never read again; the ``no converter`` refusal is what a re-read looks for.  Nothing the
    engine said is in it, and it is not cached, so the next read asks the engine again."""
    reading = Fake(_ocr_fails, version=lambda: WITH_OCR)
    with caplog.at_level(logging.INFO, logger="agentsync.convert"):
        got = _run(src, _reading(reading, None), cache)
    assert got == _run(src, Registry([]), ConverterCache(cache.root.parent / "other-mac"))
    assert (got.status, got.reason) == (ConversionStatus.REFUSED, "no converter for .fk")
    assert (got.converter_id, got.converter_version, got.from_cache) == ("none", "0", False)
    assert caplog.messages == ["doc.fk: on-device OCR failed; nothing else converts it"]
    assert _run(src, _reading(reading, None), cache).status is ConversionStatus.REFUSED
    assert reading.calls == 2


def test_an_ocr_failure_fails_as_any_other_where_no_registry_without_ocr_answers_for_the_file(
    src: Path, cache: ConverterCache
) -> None:
    """A registry that keeps none without an engine, and one whose registry without an engine routes the
    name to another converter: neither result would be this file's without OCR."""
    reading = Fake(_ocr_fails, version=lambda: WITH_OCR)
    other = Fake(lambda s, n: (_unit("another converter's page\n"),), cid="other")
    for registry in (Registry([reading]), _reading(reading, other)):
        got = _run(src, registry, cache)
        assert (got.status, got.reason, got.converter_version) == (
            ConversionStatus.FAILED,
            "conversion failed: on-device OCR failed",
            WITH_OCR,
        )
    assert reading.calls == 2 and other.calls == 0


def test_only_an_ocr_failure_is_converted_again(src: Path, cache: ConverterCache) -> None:
    def broken(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise ConversionError("pandoc exited 64")

    plain = Fake(lambda s, n: (_unit("never asked for\n"),))
    got = _run(src, _reading(Fake(broken, version=lambda: WITH_OCR), plain), cache)
    assert got.status is ConversionStatus.FAILED and got.reason == "conversion failed: pandoc exited 64"
    assert got.converter_version == WITH_OCR and plain.calls == 0


def test_double_conversion_detects_nondeterminism(src: Path) -> None:
    counter = itertools.count()
    flaky = Registry([Fake(lambda s, n: (_unit(f"run {next(counter)}\n"),))])
    stable = Registry([Fake(lambda s, n: (_unit("same\n"),))])
    assert double_conversion_differs(src, name="a.fk", registry=flaky) is True
    assert double_conversion_differs(src, name="a.fk", registry=stable) is False
    assert double_conversion_differs(src, name="a.unknown", registry=stable) is False


def test_double_conversion_consistent_failure_is_deterministic(src: Path) -> None:
    def boom(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise ConversionError("always the same")

    assert double_conversion_differs(src, name="a.fk", registry=Registry([Fake(boom)])) is False
    flip = itertools.count()

    def sometimes(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        if next(flip) % 2:
            raise ConversionError("second run fails")
        return (_unit("ok\n"),)

    assert double_conversion_differs(src, name="a.fk", registry=Registry([Fake(sometimes)])) is True


def test_sidecar_difference_counts_as_nondeterminism(src: Path) -> None:
    counter = itertools.count()

    def conv(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        u = _unit("same\n")
        sc = (("full.csv", hashlib.sha256(str(next(counter)).encode()).digest()),)
        return (
            RenderedUnit(**{**{f: getattr(u, f) for f in RenderedUnit.__dataclass_fields__}, "sidecars": sc}),
        )

    assert double_conversion_differs(src, name="a.fk", registry=Registry([Fake(conv)])) is True


def test_a_media_error_gives_the_no_converter_refusal_and_caches_nothing(
    src: Path, cache: ConverterCache, caplog: pytest.LogCaptureFixture
) -> None:
    """A recording the media helper failed on is what a Mac without the helper has: the ``no converter``
    refusal a later read looks for.  The log line says it was a recording; nothing is cached."""

    def helper_failed(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise MediaError("the media helper failed")

    reading = Fake(helper_failed, exts=(".mp4",), version=lambda: WITH_OCR)
    with caplog.at_level(logging.INFO, logger="agentsync.convert"):
        got = _run(src, _reading(reading, None), cache, name="Contoso review.mp4")
    assert (got.status, got.reason, got.from_cache) == (
        ConversionStatus.REFUSED,
        "no converter for .mp4",
        False,
    )
    assert caplog.messages == [
        "Contoso review.mp4: the recording could not be read on this Mac (the media helper failed); "
        "left for a later read"
    ]
    assert not any(cache.root.rglob("*.json"))
    _run(src, _reading(reading, None), cache, name="Contoso review.mp4")
    assert reading.calls == 2, "read again next time"


@pytest.mark.parametrize("timed_out", [False, True])
def test_a_recording_that_is_not_finished_is_raised_never_failed_or_cached(
    src: Path, cache: ConverterCache, timed_out: bool
) -> None:
    def waits(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise RecordingNotFinished(done_ms=300_000, total_ms=900_000, timed_out=timed_out)

    reading = Fake(waits, exts=(".mp4",), version=lambda: WITH_OCR)
    with pytest.raises(RecordingNotFinished) as caught:
        _run(src, _reading(reading, None), cache, name="review.mp4")
    assert (caught.value.done_ms, caught.value.total_ms, caught.value.timed_out) == (
        300_000,
        900_000,
        timed_out,
    )
    assert not any(cache.root.rglob("*.json"))


def test_a_speech_failure_keeps_the_screens_under_a_version_without_speech(
    src: Path, cache: ConverterCache, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec S8 Failure: a SpeechError (an OcrError) goes to ``without_speech`` before ``without_ocr``, so the
    page keeps its screens; it is cached under the version without ``+asr-``, which a later read finds."""

    def deaf(s: Path, n: str) -> tuple[RenderedUnit, ...]:
        raise SpeechError("the speech helper exited 3")

    heard = Fake(deaf, exts=(".mp4",), version=lambda: "1.0.0+media-h1+cue-r1+asr-parakeet-x-f1-d1-n1")
    screens = Fake(
        lambda s, n: (_unit("the screens\n"),), exts=(".mp4",), version=lambda: "1.0.0+media-h1+cue-r1"
    )
    reg = _reading(heard, None)
    reg._without_speech = Registry([screens])
    with caplog.at_level(logging.INFO, logger="agentsync.convert"):
        got = _run(src, reg, cache, name="Contoso review.mp4")
    assert (got.status, got.converter_version, got.units[0].body) == (
        ConversionStatus.OK,
        "1.0.0+media-h1+cue-r1",
        "the screens\n",
    )
    assert caplog.messages == [
        "Contoso review.mp4: the speech engine failed (the speech helper exited 3); read without speech"
    ]
    assert _run(src, reg, cache, name="Contoso review.mp4").from_cache and heard.calls == 2
    reg._without_speech = None
    assert _run(src, reg, cache, name="Contoso review.mp4").reason == "no converter for .mp4", (
        "without a twin a SpeechError is an OcrError"
    )
