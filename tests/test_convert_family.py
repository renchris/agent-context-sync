"""B13 round 28 I39: a package of another Office family is "not the type its name says", and no conversion
error names the staged path or the home folder."""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from agentsync import convert as convert_pkg
from agentsync import cycle as cycle_mod
from agentsync import policy as policy_mod
from agentsync.config import ConvertConfig
from agentsync.convert import ConverterCache, convert_file
from agentsync.convert import _common as common_mod
from agentsync.convert import msg as msg_mod
from agentsync.convert import pptx as pptx_mod
from agentsync.convert import vtt as vtt_mod
from agentsync.convert.base import Converter, options_hash
from agentsync.convert.msg import NOT_MSG_REASON
from agentsync.convert.pandoc import PandocConverter
from agentsync.convert.pptx import PptxConverter
from agentsync.convert.registry import Registry
from agentsync.convert.xlsx import XlsxConverter
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.manifest import Manifest
from agentsync.model import ConversionStatus, RowState
from agentsync.policy import NOT_OOXML_REASON
from agentsync.setup_report import quarantine_class
from test_convert_builders import build_potx, build_pptx_rich, make_zip
from test_review_fixes import all_objects, git, item_id, make_env, page_fm, run

CFG = ConvertConfig()
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
_WORD_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_PRES_ZW = "application/vnd.openxmlformats-officedocument.presentation\u200bml.presentation.main+xml"


def word_package(path: Path) -> Path:
    """A minimal Word package: its main part is typed wordprocessingml, whatever ``path``'s suffix says."""
    return make_zip(
        path,
        {
            "[Content_Types].xml": (
                f'<?xml version="1.0"?><Types xmlns="{_TYPES}">'
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                f'<Override PartName="/word/document.xml" ContentType="{_WORD_MAIN}"/></Types>'
            ).encode(),
            "_rels/.rels": (
                f'<?xml version="1.0"?><Relationships xmlns="{_RELS}">'
                f'<Relationship Id="rId1" Type="{_OFFICE}" Target="word/document.xml"/></Relationships>'
            ).encode(),
            "word/document.xml": (
                b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                b'2006/main"><w:body><w:p><w:r><w:t>Contoso notes</w:t></w:r></w:p></w:body></w:document>'
            ),
        },
    )


@pytest.mark.parametrize(
    ("name", "converter", "noun"),
    [
        ("Contoso doc.potx", PptxConverter, "a deck"),
        ("Contoso doc.pptx", PptxConverter, "a deck"),
        ("Contoso doc.xlsx", XlsxConverter, "a workbook"),
    ],
)
def test_a_word_package_under_another_offices_name_is_not_the_type_its_name_says(
    tmp_path: Path, name: str, converter: type[PptxConverter] | type[XlsxConverter], noun: str
) -> None:
    src = word_package(tmp_path / name)
    with pytest.raises(UnreadableSourceError) as caught:
        converter(CFG).convert(src, name=name)
    reason = str(caught.value)
    assert reason == f"not-ooxml: a Word document, not {noun} (not the type its name says)"
    assert quarantine_class(reason) == "not the type its name says"


@pytest.mark.parametrize("gap", [" = ", "\n  =\n  ", "\t=\t"])
@pytest.mark.parametrize(
    ("name", "converter", "noun"),
    [
        ("Contoso doc.potx", PptxConverter, "a deck"),
        ("Contoso doc.pptx", PptxConverter, "a deck"),
        ("Contoso doc.xlsx", XlsxConverter, "a workbook"),
    ],
)
def test_a_content_type_with_whitespace_around_its_equals_sign_is_read(
    tmp_path: Path, name: str, converter: type[PptxConverter] | type[XlsxConverter], noun: str, gap: str
) -> None:
    """B13 round 29 C222: XML allows whitespace around an attribute's ``=``; ``ContentType = "..."`` was no
    family, so the Word package went on to python-pptx or openpyxl and became a failed conversion, retried."""
    src = word_package(tmp_path / name)
    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    types = parts["[Content_Types].xml"].replace(b"ContentType=", f"ContentType{gap}".encode())
    ET.fromstring(types)  # still well-formed XML
    parts["[Content_Types].xml"] = types
    make_zip(src, parts)
    with pytest.raises(UnreadableSourceError) as caught:
        converter(CFG).convert(src, name=name)
    assert str(caught.value) == f"not-ooxml: a Word document, not {noun} (not the type its name says)"


def test_a_deck_named_docx_is_not_the_type_its_name_says(tmp_path: Path) -> None:
    deck = tmp_path / "Contoso deck.docx"
    deck.write_bytes(build_pptx_rich(tmp_path / "rich.pptx").read_bytes())
    with pytest.raises(UnreadableSourceError, match=r"^not-ooxml: a PowerPoint deck, not a Word document"):
        PandocConverter(CFG).convert(deck, name=deck.name)


_BOMS = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}


def _types_in(src: Path, codec: str, edit: Callable[[str], str] = lambda t: t) -> Path:
    """``src`` with its ``[Content_Types].xml`` rewritten in ``codec`` behind its byte order mark (``utf-8``:
    no mark), after ``edit``; every other part kept byte for byte."""
    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    text = edit(parts["[Content_Types].xml"].decode("utf-8"))
    parts["[Content_Types].xml"] = _BOMS.get(codec, b"") + text.encode(codec)
    return make_zip(src, parts)


@pytest.mark.parametrize("codec", list(_BOMS))
@pytest.mark.parametrize(
    ("name", "converter", "noun"),
    [
        ("Contoso doc.potx", PptxConverter, "a deck"),
        ("Contoso doc.pptx", PptxConverter, "a deck"),
        ("Contoso doc.xlsx", XlsxConverter, "a workbook"),
    ],
)
def test_a_word_package_whose_content_types_are_utf_16_is_not_the_type_its_name_says(
    tmp_path: Path, name: str, converter: type[PptxConverter] | type[XlsxConverter], noun: str, codec: str
) -> None:
    """B13 round 34 C827: a ``[Content_Types].xml`` with a UTF-16 byte order mark was no family, so the Word
    package went on to python-pptx or openpyxl, a failed conversion tried 3 times, while its UTF-8 twin was
    one not-ooxml stub."""
    src = _types_in(word_package(tmp_path / name), codec)
    with pytest.raises(UnreadableSourceError) as caught:
        converter(CFG).convert(src, name=name)
    assert str(caught.value) == f"not-ooxml: a Word document, not {noun} (not the type its name says)"


@pytest.mark.parametrize("codec", list(_BOMS))
@pytest.mark.parametrize(
    ("name", "converter"), [("Contoso deck.docx", PandocConverter), ("Contoso deck.xlsx", XlsxConverter)]
)
def test_a_deck_whose_content_types_are_utf_16_is_not_the_type_its_name_says(
    tmp_path: Path, name: str, converter: type[PandocConverter] | type[XlsxConverter], codec: str
) -> None:
    src = tmp_path / name
    src.write_bytes(build_pptx_rich(tmp_path / "rich.pptx").read_bytes())
    _types_in(src, codec)
    with pytest.raises(UnreadableSourceError, match=r"^not-ooxml: a PowerPoint deck, not "):
        converter(CFG).convert(src, name=name)


@pytest.mark.parametrize("codec", list(_BOMS))
def test_a_package_whose_content_types_are_utf_16_still_converts_under_its_own_name(
    tmp_path: Path, codec: str
) -> None:
    """C827's decode reclassifies only another family: a UTF-16 Word ``.docx``, deck ``.pptx`` and template
    ``.potx`` convert as their UTF-8 twins do."""
    doc = _types_in(word_package(tmp_path / "Contoso notes.docx"), codec)
    assert "Contoso notes" in PandocConverter(CFG).convert(doc, name=doc.name)[0].body
    deck = _types_in(build_pptx_rich(tmp_path / "Contoso deck.pptx"), codec)
    assert PptxConverter(CFG).convert(deck, name=deck.name)[0].body
    template = _types_in(build_potx(tmp_path / "Contoso template.potx"), codec)
    assert "Contoso Quarterly Template" in PptxConverter(CFG).convert(template, name=template.name)[0].body


@pytest.mark.parametrize("codec", list(_BOMS))
def test_a_utf_16_content_types_part_that_does_not_decode_is_no_family(tmp_path: Path, codec: str) -> None:
    """The decode stays inside the read's try: an odd-length part behind the mark cannot be told."""
    src = word_package(tmp_path / "Contoso doc.potx")
    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    parts["[Content_Types].xml"] = _BOMS[codec] + parts["[Content_Types].xml"].decode().encode(codec) + b"<"
    make_zip(src, parts)
    assert common_mod._ooxml_family(src) is None


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("wordprocessingml.document", "presentation\u200bml.presentation"),
        lambda t: t.replace("wordprocessingml.document", "wordprocessing\u200bml.document"),
        lambda t: t.replace("/></Types>", f' /><Override PartName="/x" ContentType="{_PRES_ZW}"/></Types>'),
    ],
    ids=["zero-width-other", "zero-width-own", "zero-width-beside"],
)
def test_a_utf_16_content_types_part_reads_as_its_utf_8_twin(
    tmp_path: Path, edit: Callable[[str], str]
) -> None:
    """C827 decodes to UTF-8, never to ASCII with the rest dropped: a zero-width character inside a main type
    would vanish there and turn a type no reader takes into another family's."""
    found = {
        codec: common_mod._ooxml_family(_types_in(word_package(tmp_path / f"{codec}.docx"), codec, edit))
        for codec in ("utf-8", *_BOMS)
    }
    assert len(set(found.values())) == 1, found


@pytest.mark.parametrize("codec", list(_BOMS))
def test_a_word_document_named_potx_with_utf_16_content_types_is_converted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codec: str
) -> None:
    """C827's sync: three runs convert the UTF-16 twin once, as the UTF-8 one (the test above)."""
    config, src = make_env(tmp_path)
    _types_in(word_package(src / "Contoso renamed doc.potx"), codec)
    tries = _counted(monkeypatch)
    for _ in range(3):
        assert run(config).exit_code == 0
    assert tries == ["Contoso renamed doc.potx"]
    row = _row(config, "Contoso renamed doc.potx")
    assert row.state is RowState.QUARANTINED
    assert quarantine_class(row.state_reason) == "not the type its name says"


@pytest.mark.parametrize("codec", [c for c in _BOMS if c != "utf-8"])
def test_the_content_types_cap_is_on_stored_bytes_before_the_utf_16_decode_c964(
    tmp_path: Path, codec: str
) -> None:
    """B13 round 35 C964 (words only): ``_CONTENT_TYPES_MAX`` caps the part's stored bytes before C827's
    decode, so the UTF-16 twin is no longer read as its UTF-8 one at 524,288 ASCII characters (1,048,578
    bytes with its mark), one character after the last it reads; the UTF-8 twin is still read there."""

    def padded(chars: int) -> Callable[[str], str]:
        def edit(text: str) -> str:
            pad = chars - len(text) - len("<!---->")
            return text.replace("</Types>", f"<!--{'x' * pad}--></Types>")

        return edit

    for chars, utf16 in ((524_287, "word"), (524_288, None)):
        doc = _types_in(word_package(tmp_path / f"{chars}.potx"), codec, padded(chars))
        assert common_mod._ooxml_family(doc) == utf16, chars
        twin = _types_in(word_package(tmp_path / f"{chars}-utf-8.potx"), "utf-8", padded(chars))
        assert common_mod._ooxml_family(twin) == "word", chars


def _tries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The names the cycle hands to ``convert_file``, one per conversion it starts (but make_env's
    ``README.txt``)."""
    tries: list[str] = []
    real = cycle_mod.convert_file

    def counted(src: Path, *, name: str, **kw):  # type: ignore[no-untyped-def]
        if name != "README.txt":
            tries.append(name)
        return real(src, name=name, **kw)

    monkeypatch.setattr(cycle_mod, "convert_file", counted)
    return tries


@pytest.mark.parametrize(
    "suffix",
    [".potx", ".vsdx", ".docx", ".xlsx", ".msg", ".drawio", ".pdf", ".eml", ".odt", ".teams.json", ".vtt"],
)
def test_a_zero_byte_file_under_a_typed_name_is_one_empty_stub_c930(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """B13 round 35 C930: a zero-byte ``.potx``, ``.vsdx`` or ``.msg`` was classed ``not the type its name
    says`` and a zero-byte ``.drawio`` ``conversion failed``, tried 3 times; no bytes have no type to disagree
    with the name, and the report's class list has ``empty``. Each is one ``empty-output`` stub, read once
    over three syncs.  B13 round 36 C1050 with C1117 (ruling 118): a zero-byte ``.pdf``, ``.eml``, ``.odt``
    or ``.teams.json`` was a ``conversion failed`` tried 3 times and a ``.vtt`` ``other``; each converter now
    says ``empty`` for zero bytes itself."""
    config, src = make_env(tmp_path)
    name = f"Contoso zero{suffix}"
    (src / name).write_bytes(b"")
    tries = _tries(monkeypatch)
    for _ in range(3):
        assert run(config).exit_code == 0
    assert tries == [name]
    row = _row(config, name)
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.EMPTY_OUTPUT_REASON
    assert quarantine_class(row.state_reason) == "empty"


def test_a_zero_byte_vtt_after_an_older_builds_cached_result_is_empty_c1304(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 37 C1304: before C1050 a zero-byte ``.vtt`` was a cached UNREADABLE ``not a WebVTT
    transcript`` (classed ``other``), and C1050 changed the converter's answer without its emitter version,
    so an upgraded Mac served every new zero-byte transcript (one canonical hash) that cached answer while a
    fresh one said ``empty``. vtt's emitter is 1.0.1: an older build's result, cached under 1.0.0, is never
    read, and a new empty transcript is ``empty``, as on a fresh Mac. The row the older build settled keeps
    its class until its bytes change (C930's rule: no re-read)."""
    config, src = make_env(tmp_path)
    real = vtt_mod.VttConverter.convert

    def older(self: vtt_mod.VttConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        if not path.read_bytes():  # the build before C1050: zero bytes fail the WEBVTT line test
            raise UnreadableSourceError(vtt_mod._NOT_VTT)
        return real(self, path, name=name)

    with monkeypatch.context() as old:  # an older build's sync: emitter 1.0.0, its zero-byte answer cached
        old.setattr(vtt_mod, "_EMITTER_VERSION", "1.0.0")
        old.setattr(vtt_mod.VttConverter, "convert", older)
        (src / "Contoso earlier.vtt").write_bytes(b"")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso earlier.vtt").state_reason == vtt_mod._NOT_VTT
    (src / "Contoso zero.vtt").write_bytes(b"")
    (src / "Contoso standup.vtt").write_bytes(b"WEBVTT\n\n00:00.000 --> 00:01.000\n<v Ana>Hello\n")
    assert run(config).exit_code == 0
    row = _row(config, "Contoso zero.vtt")
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.EMPTY_OUTPUT_REASON
    assert quarantine_class(row.state_reason) == "empty"
    assert _row(config, "Contoso standup.vtt").state is RowState.LIVE, "a transcript still converts"
    assert _row(config, "Contoso earlier.vtt").state_reason == vtt_mod._NOT_VTT, "settled: kept, no re-read"


def _older_zero_byte_answers(old: pytest.MonkeyPatch, *, older_key: bool = True) -> None:
    """The build before C930: a zero-byte Office name is ``not-ooxml`` and a zero-byte ``.msg`` ``not-msg``,
    both cached UNREADABLE, under a key with no ``empty_screen`` (``older_key``) or under today's."""
    screen, msg_convert = policy_mod.screen_file, msg_mod.MsgConverter.convert

    def older_screen(path: Path, *, name: str, policy: policy_mod.PolicyConfig):  # type: ignore[no-untyped-def]
        if name.lower().endswith(policy_mod.OOXML_SUFFIXES) and not path.read_bytes():
            return policy_mod.Screening(policy_mod.ScreenStatus.UNREADABLE, "not-ooxml", NOT_OOXML_REASON)
        return screen(path, name=name, policy=policy)

    def older_msg(self: msg_mod.MsgConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        if not path.read_bytes():
            raise UnreadableSourceError(msg_mod.NOT_MSG_REASON)
        return msg_convert(self, path, name=name)

    def older_identity(conv: Converter, name: str) -> tuple[str, str]:
        return conv.version(), options_hash({**conv.options(), "input_suffix": convert_pkg._suffix(name)})

    old.setattr(policy_mod, "screen_file", older_screen)
    old.setattr(msg_mod.MsgConverter, "convert", older_msg)
    if older_key:
        old.setattr(convert_pkg, "_identity", older_identity)
    old.setattr(convert_pkg, "_zero_bytes", lambda src: False)  # nor did it skip the cache for zero bytes


def _options_hash(config, name: str) -> str | None:  # type: ignore[no-untyped-def]
    with Manifest(config.state_paths.db) as m:
        (out,) = m.outputs_for("src", item_id(config, name))
        return out.options_hash


@pytest.mark.parametrize(
    ("suffix", "older", "real"),
    [
        (".docx", NOT_OOXML_REASON, None),
        (".xlsx", NOT_OOXML_REASON, None),
        (".pptx", NOT_OOXML_REASON, build_pptx_rich),
        (".potx", NOT_OOXML_REASON, build_potx),
        (".vsdx", NOT_OOXML_REASON, None),
        (".msg", NOT_MSG_REASON, None),
    ],
)
def test_a_zero_byte_office_file_or_msg_after_an_older_builds_cached_result_is_empty_c1450(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    older: str,
    real: Callable[[Path], object] | None,
) -> None:
    """B13 round 38 C1450 with C1488 (ruling 132): before C930 a zero-byte Office file or ``.msg`` was a
    cached UNREADABLE ``not-ooxml`` or ``not-msg`` (classed ``not the type its name says``), and C930 moved no
    version, so an upgraded Mac served every new zero-byte file of that suffix (one canonical hash) the older
    answer while a fresh one said ``empty``. The guard's ``empty_screen`` option moves those converters' keys:
    a new zero-byte file is ``empty``, a real file still converts, and the row the older build settled keeps
    its class (C930's rule: no re-read)."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / f"Contoso earlier{suffix}").write_bytes(b"")
        assert run(config).exit_code == 0
    earlier = _row(config, f"Contoso earlier{suffix}")
    assert earlier.state_reason == older
    assert quarantine_class(older) == "not the type its name says"
    (src / f"Contoso zero{suffix}").write_bytes(b"")
    if real is not None:
        real(src / f"Contoso real{suffix}")
    assert run(config).exit_code == 0
    row = _row(config, f"Contoso zero{suffix}")
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.EMPTY_OUTPUT_REASON
    assert quarantine_class(row.state_reason) == "empty"
    assert _options_hash(config, f"Contoso zero{suffix}") != _options_hash(config, f"Contoso earlier{suffix}")
    if real is not None:
        assert _row(config, f"Contoso real{suffix}").state is RowState.LIVE, "a real file still converts"
    assert _row(config, f"Contoso earlier{suffix}").state_reason == older, "settled: kept, no re-read"


@pytest.mark.parametrize(("suffix", "older"), [(".docx", NOT_OOXML_REASON), (".msg", NOT_MSG_REASON)])
def test_a_zero_byte_file_is_never_served_a_cached_answer_under_its_own_key_c1466(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, older: str
) -> None:
    """B13 round 38 C1466 (ruling 132): an answer cached for zero bytes under the key a build still uses was
    served to every new zero-byte file of the suffix (one canonical hash), whatever the converter says today.
    ``convert_file`` skips the cache get and put for a staged file of size 0, after the label refusal: a new
    zero-byte file is ``empty`` though the older answer sits under its very key, nothing new is cached for it,
    a real file still converts, and the row the older build settled keeps its class (no re-read)."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:  # the older answer, under today's key (empty_screen included)
        _older_zero_byte_answers(old, older_key=False)
        (src / f"Contoso earlier{suffix}").write_bytes(b"")
        assert run(config).exit_code == 0
    assert _row(config, f"Contoso earlier{suffix}").state_reason == older
    with Manifest(config.state_paths.db) as m:
        (seeded,) = m.outputs_for("src", item_id(config, f"Contoso earlier{suffix}"))
    (src / f"Contoso zero{suffix}").write_bytes(b"")
    if suffix == ".docx":
        word_package(src / "Contoso real.docx")
    assert run(config).exit_code == 0
    row = _row(config, f"Contoso zero{suffix}")
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.EMPTY_OUTPUT_REASON
    assert quarantine_class(row.state_reason) == "empty"
    with Manifest(config.state_paths.db) as m:
        (out,) = m.outputs_for("src", item_id(config, f"Contoso zero{suffix}"))
    assert out.action_key == seeded.action_key, (
        "the same key: only the zero-byte skip keeps the old answer out"
    )
    assert ConverterCache(config.cache_dir).get(out.action_key).reason == older, "the old entry stays"
    if suffix == ".docx":
        assert _row(config, "Contoso real.docx").state is RowState.LIVE, "a real file still converts"
    assert _row(config, f"Contoso earlier{suffix}").state_reason == older, "settled: kept, no re-read"


def test_convert_file_neither_reads_nor_writes_the_cache_for_zero_bytes_c1466(tmp_path: Path) -> None:
    """B13 round 38 C1466: a zero-byte staged file is converted every time and never cached, while the same
    name over real bytes is cached as before."""
    reg = Registry.default(ConvertConfig())
    cache = ConverterCache(tmp_path / "cache")
    calls: list[str] = []
    real_get, real_put = cache.get, cache.put
    cache.get = lambda key: calls.append("get") or real_get(key)  # type: ignore[method-assign]
    cache.put = lambda result: calls.append("put") or real_put(result)  # type: ignore[method-assign]
    zero = tmp_path / "zero.txt"
    zero.write_bytes(b"")
    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    for _ in range(2):
        result = convert_file(
            zero,
            name="Contoso zero.txt",
            content_sha256=digest,
            canonical_sha256=digest,
            registry=reg,
            cache=cache,
        )
        assert not result.from_cache
    assert calls == []
    full = tmp_path / "full.txt"
    full.write_bytes(b"Contoso notes\n")
    digest = "a" * 64
    convert_file(
        full,
        name="Contoso full.txt",
        content_sha256=digest,
        canonical_sha256=digest,
        registry=reg,
        cache=cache,
    )
    assert calls == ["get", "put"]


@pytest.mark.parametrize(
    ("name", "screened"),
    [
        ("a.docx", True),
        ("a.xlsx", True),
        ("a.pptx", True),
        ("a.potx", True),
        ("a.vsdx", True),
        ("a.vsdm", True),
        ("a.XLSM", True),
        ("a.msg", True),
        ("a.rtf", False),
        ("a.html", False),
        ("a.odt", False),
        ("a.drawio", False),
        ("a.pdf", False),
        ("a.eml", False),
        ("a.vtt", False),
        ("a.txt", False),
        ("a.teams.json", False),
        ("a.md", False),
    ],
)
def test_only_an_office_name_or_msg_carries_the_empty_screen_option_c1450(name: str, screened: bool) -> None:
    """B13 round 38 C1450 with C1488 (ruling 132): ``empty_screen`` moves only the keys of the names whose
    zero-byte answer C930 changed; a ``.rtf``, ``.html``, ``.odt`` or ``.drawio`` its converter shares with an
    Office name, and a pdf, mail, transcript or text file, keeps its key and every result the cache holds."""
    conv = Registry.default(ConvertConfig()).for_name(name)
    assert conv is not None
    base = {**conv.options(), "input_suffix": convert_pkg._suffix(name)}
    want = options_hash({**base, "empty_screen": 1} if screened else base)
    assert convert_pkg._identity(conv, name) == (conv.version(), want)


@pytest.mark.parametrize(
    ("name", "data"),
    [("Contoso text.drawio", b"a flow, typed as text\n"), ("Contoso blanks.drawio", b"  \r\n\t")],
)
def test_a_drawio_that_is_no_diagram_is_one_stub_of_its_type_c927(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, data: bytes
) -> None:
    """B13 round 35 C927: a ``.drawio`` holding text was a failed conversion tried 3 times, an error line each
    run, where a ``.vsdx`` that is not a ZIP, or an Office package of another family (B13 round 36 C1033),
    and a wrong-type ``.potx`` or ``.msg`` are one stub at first read. It is one ``not-drawio`` stub, ``not
    the type its name says``."""
    config, src = make_env(tmp_path)
    (src / name).write_bytes(data)
    tries = _tries(monkeypatch)
    for _ in range(3):
        assert run(config).exit_code == 0
    assert tries == [name]
    row = _row(config, name)
    assert row.state is RowState.QUARANTINED and row.state_reason == "not-drawio (not a draw.io diagram)"
    assert quarantine_class(row.state_reason) == "not the type its name says"


@pytest.mark.parametrize("suffix", [".vsdx", ".vsdm"])
def test_an_office_package_of_another_family_named_vsdx_is_one_stub_of_its_type_c1033(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """B13 round 36 C1033: a Word package named ``.vsdx`` was a failed conversion tried 3 times (``not a Visio
    drawing (no visio/pages/pages.xml)``), where the same bytes named ``.potx`` are one settled stub.
    ``_vsdx_pages`` now runs the Office family check, so it is one ``not-ooxml`` stub, ``not the type its
    name says``, read once over three syncs."""
    config, src = make_env(tmp_path)
    name = f"Contoso word{suffix}"
    word_package(src / name)
    tries = _tries(monkeypatch)
    for _ in range(3):
        assert run(config).exit_code == 0
    assert tries == [name]
    row = _row(config, name)
    assert row.state is RowState.QUARANTINED
    assert row.state_reason == "not-ooxml: a Word document, not a Visio drawing (not the type its name says)"
    assert quarantine_class(row.state_reason) == "not the type its name says"


def test_a_zip_that_is_no_office_package_named_vsdx_stays_a_failed_conversion_c1033(tmp_path: Path) -> None:
    """The limit C1033 states: a ZIP that is no Office package at all has no family to tell, so it keeps the
    failed conversion, tried again, as a package still being written must."""
    archive = make_zip(tmp_path / "Contoso archive.vsdx", {"notes.txt": b"not a drawing\n"})
    with pytest.raises(ConversionError, match="not a Visio drawing"):
        Registry.default(ConvertConfig()).for_name(archive.name).convert(archive, name=archive.name)  # type: ignore[union-attr]


def test_a_truncated_drawio_stays_a_failed_conversion_tried_again_c927(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagram cut off part way opens with markup: it may read whole on a later run (a sync app still
    writing it), so it keeps the failed conversion and its tries."""
    config, src = make_env(tmp_path)
    (src / "Contoso cut.drawio").write_bytes(b'<mxfile host="x"><diagram name="P"><mxGraphModel><root>')
    tries = _tries(monkeypatch)
    for _ in range(4):
        assert run(config).exit_code == 0
    assert len(tries) == 1 + cycle_mod._CONVERT_RETRIES
    row = _row(config, "Contoso cut.drawio")
    assert quarantine_class(row.state_reason) == "conversion failed"


@pytest.mark.parametrize(
    ("name", "data"),
    [("Contoso not a zip.potx", b"not a zip"), ("Contoso text.msg", b"plain text, not mail\n")],
)
def test_a_typed_name_over_other_bytes_keeps_not_the_type_its_name_says_c930(
    tmp_path: Path, name: str, data: bytes
) -> None:
    """Only zero bytes are ``empty``: other bytes under an Office or Outlook name keep their stub's class."""
    config, src = make_env(tmp_path)
    (src / name).write_bytes(data)
    assert run(config).exit_code == 0
    assert quarantine_class(_row(config, name).state_reason) == "not the type its name says"


def test_a_zero_byte_eml_under_a_label_rule_keeps_its_label_refusal_c1050(tmp_path: Path) -> None:
    """B13 round 36 C1050: the label decision runs before any converter, so under ``refuse_unlabelled`` a
    zero-byte ``.eml`` stays the never-cached ``label policy`` refusal, never the converter's ``empty``."""
    config, src = make_env(tmp_path, extra="[policy]\nrefuse_unlabelled = true\n")
    assert config.policy.refuse_unlabelled
    (src / "Contoso zero.eml").write_bytes(b"")
    assert run(config).exit_code == 0
    row = _row(config, "Contoso zero.eml")
    assert row.state_reason == f"{policy_mod.REFUSED_PREFIX}no sensitivity label; refuse_unlabelled = true"
    assert quarantine_class(row.state_reason) == "label policy"


@pytest.mark.parametrize("suffix", [".txt", ".url", ".md"])
def test_a_zero_byte_text_file_stays_a_page_c930(tmp_path: Path, suffix: str) -> None:
    """The empty check sits inside the Office guard, never at the top of the screen: zero-byte text types
    are pages today and stay pages (no older test held this)."""
    config, src = make_env(tmp_path)
    name = f"Contoso zero{suffix}"
    (src / name).write_bytes(b"")
    assert run(config).exit_code == 0
    assert _row(config, name).state is RowState.LIVE


def test_a_zero_byte_office_file_under_a_label_rule_stays_label_unreadable_c930(tmp_path: Path) -> None:
    """The empty check comes after the label decision: under ``refuse_unlabelled`` a zero-byte ``.docx`` keeps
    its never-cached REFUSED ``label unreadable``, never a cached UNREADABLE ``empty``."""
    zero = tmp_path / "Contoso zero.docx"
    zero.write_bytes(b"")
    strict = policy_mod.PolicyConfig(refuse_unlabelled=True)
    decision = policy_mod.screen_file(zero, name=zero.name, policy=strict)
    assert decision is not None and decision.status is policy_mod.ScreenStatus.REFUSED
    assert quarantine_class(decision.reason) == "label unreadable"
    plain = policy_mod.screen_file(zero, name=zero.name, policy=policy_mod.PolicyConfig())
    assert plain is not None and (plain.status, plain.code) == (
        policy_mod.ScreenStatus.UNREADABLE,
        "empty-output",
    )


def test_every_presentation_main_type_stays_on_python_pptx(tmp_path: Path) -> None:
    """Only another Office family is reclassified: a macro deck's type is read as python-pptx reads it, and a
    template-typed ``.pptx`` keeps python-pptx's own error (R85)."""
    rich = build_pptx_rich(tmp_path / "rich.pptx")
    macro = tmp_path / "macro.pptx"
    with pptx_mod.zipfile.ZipFile(rich) as zin, pptx_mod.zipfile.ZipFile(macro, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                    b"application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
                )
            zout.writestr(info, data)
    assert "## Numbers" in PptxConverter(CFG).convert(macro, name="macro.pptx")[0].body
    template = build_potx(tmp_path / "Contoso Quarterly.potx")
    with pytest.raises(ConversionError, match="is not a PowerPoint file"):
        PptxConverter(CFG).convert(template, name="Contoso Quarterly.pptx")


def test_a_conversion_error_names_no_staged_path_and_no_home(tmp_path: Path) -> None:
    """python-pptx's error names the file it opened; the stub's reason names the file only."""
    staged = Path.home() / "Library/Application Support/agentsync/staging/f4d3033ed9d8fcb2"
    staged.mkdir(parents=True)
    src = staged / "Contoso Quarterly.pptx"
    src.write_bytes(build_potx(tmp_path / "t.potx").read_bytes())  # a template's type under a deck's name
    with pytest.raises(ConversionError, match="Application Support"):
        PptxConverter(CFG).convert(src, name=src.name)  # the library's words, as they were
    result = convert_file(
        src,
        name=src.name,
        content_sha256="0" * 64,
        canonical_sha256="0" * 64,
        registry=Registry.default(CFG),
        cache=ConverterCache(tmp_path / "cache"),
    )
    assert result.status is ConversionStatus.FAILED and result.reason is not None
    assert "file 'Contoso Quarterly.pptx' is not a PowerPoint file" in result.reason
    for leak in ("staging", "Application Support", str(Path.home()), str(Path.home().resolve())):
        assert leak not in result.reason


def _row(config, name: str):  # type: ignore[no-untyped-def]
    with Manifest(config.state_paths.db) as m:
        return m.get_item("src", item_id(config, name))


def _leaks(config) -> list[str]:  # type: ignore[no-untyped-def]
    """Which of the staging root, its folder words and the home folder a file in the docs repo holds."""
    words = {str(config.state_paths.staging), "Application Support", str(Path.home())}
    found = set()
    for path in config.docs_repo.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(config.docs_repo).parts:
            data = path.read_text(encoding="utf-8", errors="replace")
            found.update(w for w in words if w in data)
    return sorted(found)


def test_a_word_document_named_potx_is_one_stub_converted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finder's probe: a ``.docx`` renamed ``.potx`` was a failed conversion, tried 3 times, whose reason
    put the staging path into the page, QUARANTINE.tsv and the manifest."""
    config, src = make_env(tmp_path)
    word_package(src / "Contoso renamed doc.potx")
    tries: list[str] = []
    real = PptxConverter.convert

    def counted(self: PptxConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        tries.append(name)
        return real(self, path, name=name)

    monkeypatch.setattr(PptxConverter, "convert", counted)
    for _ in range(3):
        assert run(config).exit_code == 0
    assert tries == ["Contoso renamed doc.potx"]
    row = _row(config, "Contoso renamed doc.potx")
    assert row.state is RowState.QUARANTINED and (row.state_reason or "").startswith(
        "not-ooxml: a Word document"
    )
    assert quarantine_class(row.state_reason) == "not the type its name says"
    assert _leaks(config) == []
    assert str(config.state_paths.staging).encode() not in all_objects(config.docs_repo)


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    tries: list[str] = []
    real = PptxConverter.convert

    def counted(self: PptxConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        tries.append(name)
        return real(self, path, name=name)

    monkeypatch.setattr(PptxConverter, "convert", counted)
    return tries


def _older_build(mp: pytest.MonkeyPatch) -> None:
    """The build before I39 (no family check, no path fold) and before C181 (no one-shot re-read)."""
    mp.setattr(pptx_mod, "_check_ooxml_family", lambda *_a: None)
    mp.setattr(convert_pkg, "_pathless", lambda text, _src, _name: text)
    mp.setattr(cycle_mod._Cycle, "_names_a_path", lambda _self, _reason: False)
    mp.setattr(cycle_mod._Cycle, "_queue_path_stubs", lambda _self: None)


def _sized_root(tmp_path: Path) -> Path:
    """A root of about 160 characters (B13 round 31 C549): ``/staging/`` starts past the stored reason's cut
    (200), and the whole staging path fits the page's (300), whatever the length of the tmp folder."""
    base = tmp_path / ("Contoso " * 30)[: max(12, 160 - len(str(tmp_path.resolve())))].strip()
    base.mkdir()
    return base


def test_a_stub_an_older_build_wrote_a_staging_path_into_is_read_again_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row settled with the path in its reason is never converted again by itself. The older build ran
    4 times, so its retries are used up (3 conversions: the first and ``_CONVERT_RETRIES``); this build
    converts it once more past them, the re-read writes it without the path, and then leaves it alone
    (B13 round 29 C181: I39's re-read went through the retries and so never reached such a stub)."""
    config, src = make_env(_sized_root(tmp_path))
    word_package(src / "Contoso renamed doc.potx")
    tries = _counted(monkeypatch)
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    assert len(tries) == 1 + cycle_mod._CONVERT_RETRIES
    page = "mirror/src/contoso-renamed-doc.potx.md"
    assert str(config.state_paths.staging) in page_fm(config, page)["reason"]
    assert "/staging/" not in (_row(config, "Contoso renamed doc.potx").state_reason or ""), "cut before it"
    tries.clear()
    assert run(config).exit_code == 0
    assert tries == ["Contoso renamed doc.potx"], "past the retries: the one-shot re-read"
    assert page_fm(config, page)["reason"].startswith("not-ooxml: a Word document, not a deck")
    assert str(config.state_paths.staging) not in git(config.docs_repo, "show", f"HEAD:{page}")
    assert _leaks(config) == []
    # B13 round 30 C319: on purpose, the older build's page stays in the docs history until compaction drops
    # it; the re-read queues no purge
    older = [
        sha
        for sha in git(config.docs_repo, "rev-list", "HEAD", "--", page).split()
        if str(config.state_paths.staging) in git(config.docs_repo, "show", f"{sha}:{page}")
    ]
    assert older, "the history keeps the older build's page"
    assert not (config.state_paths.root / "governance" / "purge-queue.json").exists()
    for _ in range(2):
        assert run(config).exit_code == 0
    assert tries == ["Contoso renamed doc.potx"], "what the re-read wrote holds no path: not read again"


def test_the_one_shot_re_read_never_repeats_when_its_reason_still_holds_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C181's bound: a re-read whose new reason still names the staging folder is not converted again in
    every cycle; the retry entry records the one-shot, so it is one extra conversion."""
    config, src = make_env(tmp_path)
    word_package(src / "Contoso renamed doc.potx")
    tries = _counted(monkeypatch)
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    tries.clear()
    with monkeypatch.context() as mp:  # this build, but its reason still holds the staging path
        mp.setattr(pptx_mod, "_check_ooxml_family", lambda *_a: None)
        mp.setattr(convert_pkg, "_pathless", lambda text, _src, _name: text)
        for _ in range(3):
            assert run(config).exit_code == 0
        assert (
            str(config.state_paths.staging)
            in page_fm(config, "mirror/src/contoso-renamed-doc.potx.md")["reason"]
        )
    assert tries == ["Contoso renamed doc.potx"]


@pytest.mark.parametrize("where", ["another root's staging", "the home folder"])
def test_a_stub_whose_path_is_outside_this_staging_folder_is_read_again_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """B13 round 30 C365: the two tests above reach the re-read through ``_names_a_path``'s own staging branch
    (the reason names this config's staging folder, or is cut inside it), so they pass with
    ``_stub_with_a_path`` returning False. A stored reason that names another root's ``/staging/`` (a moved
    ``state_dir``) or a home folder path is one only ``_stub_with_a_path`` reads: it is converted exactly once
    more past its retries, and never again."""
    config, src = make_env(tmp_path)
    word_package(src / "Contoso renamed doc.potx")
    tries = _counted(monkeypatch)
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    assert len(tries) == 1 + cycle_mod._CONVERT_RETRIES
    folder = (
        "/Volumes/Contoso/agentsync/staging/ab12" if where != "the home folder" else f"{Path.home()}/Contoso"
    )
    row = _row(config, "Contoso renamed doc.potx")
    with Manifest(config.state_paths.db) as m:
        m.set_state(
            "src",
            row.stable_id,
            RowState.QUARANTINED,
            f"conversion failed: python-pptx cannot read the deck: file '{folder}/Contoso renamed doc.potx'",
        )
    tries.clear()
    assert run(config).exit_code == 0
    assert tries == ["Contoso renamed doc.potx"], "past the retries: the one-shot re-read"
    for _ in range(2):
        assert run(config).exit_code == 0
    assert tries == ["Contoso renamed doc.potx"], "the re-read is recorded: never again"


_SAID = "an older build gave up on (its stored reason named a folder on this Mac)"


def _notes(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "an older build gave up on " in ln]


def test_the_path_re_read_is_said_in_the_sync_that_queues_it_and_the_one_that_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 30 C362, C327: the one-shot re-read was a log.info line a default sync never prints, so its
    error came back with nothing saying why, and no count reached the run record. A run of another source
    queues it and says the next sync of its source reads it; that sync says it is still a stub and is not
    tried again; the one after says nothing. Every note keeps "an older build gave up on ", install.sh's
    grep."""
    from agentsync import cli  # noqa: PLC0415
    from test_cycle import _run_record  # noqa: PLC0415

    other = tmp_path / "other"
    other.mkdir()
    (other / "README.txt").write_text("sentinel\n", encoding="utf-8")
    second = f'\n[[source]]\nid = "other"\nkind = "local"\npath = "{other}"\nsentinel = "README.txt"\n'
    config, src = make_env(tmp_path, source_extra=second)
    word_package(src / "Contoso renamed doc.potx")
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    cfg = str(config.config_path)
    capsys.readouterr()
    cli._print_report(run(config, only=("other",)))  # a run that does not read its source (``only``)
    out = capsys.readouterr().out
    assert _notes(out) == [
        f"  note: 1 conversion {_SAID} is tried once more in the next sync of its source"
    ], out
    assert _run_record(config)["path_reread_waiting"] == 1
    cli.main(["sync", "--config", cfg])
    out = capsys.readouterr().out
    assert _notes(out) == [
        # B13 round 31 C545, C523: the .potx is an unreadable stub now, and the same line says 0 failed, so
        # never "failed again"; round 32 C626: the note says which it is, and where its reason is
        f"  note: 1 conversion {_SAID} was tried once more in this sync and is still a stub (1 unreadable; "
        "_sync/QUARANTINE.tsv in the docs folder gives its reason), and it is not tried again"
    ], out
    record = _run_record(config)
    assert (record["path_reread"], record["path_reread_failed"], record["path_reread_unreadable"]) == (
        1,
        1,
        1,
    )
    assert "path_reread_refused" not in record
    assert "path_reread_waiting" not in record and "path_reread_current" not in record
    for note in _notes(out):
        assert not re.search(r"\bconverted\b|\bdeferred\b|\bread again\b", note), note
    cli.main(["sync", "--config", cfg])
    assert _notes(capsys.readouterr().out) == []


_LABEL = "2096f6a2-d2f7-48be-b329-b73aaa526e5d"


def test_the_still_a_stub_note_splits_its_own_count_beside_a_refused_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 32 C626: the note sent the person to "this sync's counts", which cannot say which bucket the
    one file is in when the same sync refused a mail: `1 unreadable, 0 failed, 1 refused` fits either. The
    note now splits its own count and names where each file's reason is; the refused mail is not credited to
    it."""
    from agentsync import cli  # noqa: PLC0415
    from test_cycle import _run_record  # noqa: PLC0415

    config, src = make_env(tmp_path, extra=f'\n[policy]\nexclude_label_ids = ["{_LABEL}"]\n')
    word_package(src / "Contoso renamed doc.potx")
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    (src / "Contoso board.eml").write_bytes(
        b"From: a@contoso.com\r\nSubject: s\r\nmsip_labels: MSIP_Label_"
        + _LABEL.encode()
        + b"_Enabled=True; MSIP_Label_"
        + _LABEL.encode()
        + b"_Name=Highly Confidential;\r\n\r\nbody\r\n"
    )
    capsys.readouterr()
    cli.main(["sync", "--config", str(config.config_path)])
    out = capsys.readouterr().out
    assert re.search(r"of the converted: 1 unreadable, 0 failed, 1 refused", out), out
    assert _notes(out) == [
        f"  note: 1 conversion {_SAID} was tried once more in this sync and is still a stub (1 unreadable; "
        "_sync/QUARANTINE.tsv in the docs folder gives its reason), and it is not tried again"
    ], out
    assert _row(config, "Contoso board.eml").state is RowState.REFUSED
    record = _run_record(config)
    assert (record["path_reread_failed"], record["path_reread_unreadable"]) == (1, 1)
    assert "path_reread_refused" not in record


def test_a_re_read_the_cycle_stubbed_after_it_converted_is_held_never_unreadable_c754(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 33 C754: a one-shot re-read whose conversion now works, then stubbed by the cycle itself (no
    room for the full-content sidecar beside its page), was counted unreadable in the note, while the sync
    line beside it counted the file converted with U = 0. It is held by this sync: still a stub, never
    unreadable and never failed, and the run record carries ``path_reread_held`` inside the failed total."""
    from agentsync import cli  # noqa: PLC0415
    from agentsync.errors import SidecarPathError  # noqa: PLC0415
    from agentsync.publish import Publisher  # noqa: PLC0415
    from test_cycle import _run_record  # noqa: PLC0415

    config, src = make_env(tmp_path)
    word_package(src / "Contoso renamed doc.potx")
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    deck = build_potx(tmp_path / "deck.potx")
    converts = PptxConverter.convert
    monkeypatch.setattr(PptxConverter, "convert", lambda self, _src, *, name: converts(self, deck, name=name))
    plans = Publisher.plan_pages

    def no_room(self, source, item, result, **kw):  # type: ignore[no-untyped-def]
        if result.status is ConversionStatus.OK:
            raise SidecarPathError("no room for the sidecar")
        return plans(self, source, item, result, **kw)

    monkeypatch.setattr(Publisher, "plan_pages", no_room)
    capsys.readouterr()
    cli.main(["sync", "--config", str(config.config_path)])
    out = capsys.readouterr().out
    assert re.search(r"converted 1, deferred 0 online-only", out) and "of the converted:" not in out, out
    assert _notes(out) == [
        f"  note: 1 conversion {_SAID} was tried once more in this sync and is still a stub (1 held by this "
        "sync; _sync/QUARANTINE.tsv in the docs folder gives its reason), and it is not tried again"
    ], out
    assert _row(config, "Contoso renamed doc.potx").state_reason == cycle_mod._SIDECAR_PATH
    record = _run_record(config)
    assert (record["path_reread"], record["path_reread_failed"], record["path_reread_held"]) == (1, 1, 1)
    assert "path_reread_unreadable" not in record and "path_reread_refused" not in record, record
    for note in _notes(out):
        assert not re.search(r"\bconverted\b|\bdeferred\b|\bread again\b|\bunreadable\b|\bfailed\b", note), (
            note
        )


def test_a_re_read_refused_as_an_inbox_duplicate_by_its_bytes_is_held_c754(
    tmp_path: Path,
    local_source_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B13 round 34 C844: C754's held bucket takes a ``duplicate-of`` stub as well as ``_SIDECAR_PATH``, but
    only the sidecar route was tested. Here the one-shot re-read converts, then ``_inbox_duplicate`` finds a
    Graph file with the same bytes (the canonical hash, after the conversion) and stubs the inbox row: held
    by this sync, never unreadable. A Graph file of the same name and size would be refused before the fetch
    (``_inbox_name_size_duplicate``), so the re-read would never run; the Graph file takes another name."""
    import httpx  # noqa: PLC0415

    from agentsync import cli  # noqa: PLC0415
    from agentsync.graph.client import GraphClient  # noqa: PLC0415
    from test_cycle import INBOX_SOURCE, _run_record  # noqa: PLC0415
    from test_e2e import GRAPH_SOURCE, FakeDrive, FakeTokens, config_with  # noqa: PLC0415

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    drop = word_package(inbox / "Contoso renamed doc.potx")
    config = config_with(tmp_path, local_source_dir, GRAPH_SOURCE + INBOX_SOURCE.format(path=inbox))
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config, only=["inbox"]).exit_code == 0
    deck = build_potx(tmp_path / "deck.potx")
    converts = PptxConverter.convert
    monkeypatch.setattr(PptxConverter, "convert", lambda self, _src, *, name: converts(self, deck, name=name))
    drive = FakeDrive({"I1": ("Contoso board pack.potx", drop.read_bytes())})
    client = GraphClient(
        FakeTokens(),
        user_agent="NONISV|test|agentsync/0",
        transport=httpx.MockTransport(drive.handler),
        sleep=lambda _s: None,
    )
    try:
        report = run(config, client=client, only=["drive", "inbox"])
    finally:
        client.close()
    assert report.exit_code == 0, report
    with Manifest(config.state_paths.db) as m:
        (row,) = m.iter_items("inbox")
    assert row.state is RowState.QUARANTINED, row
    assert (row.state_reason or "").startswith("duplicate-of drive"), row.state_reason
    capsys.readouterr()
    cli._print_report(report)
    out = capsys.readouterr().out
    assert _notes(out) == [
        f"  note: 1 conversion {_SAID} was tried once more in this sync and is still a stub (1 held by this "
        "sync; _sync/QUARANTINE.tsv in the docs folder gives its reason), and it is not tried again"
    ], out
    record = _run_record(config)
    assert (record["path_reread"], record["path_reread_failed"], record["path_reread_held"]) == (1, 1, 1)
    assert "path_reread_unreadable" not in record and "path_reread_refused" not in record, record


def test_the_still_a_stub_note_names_each_bucket_it_holds(capsys: pytest.CaptureFixture[str]) -> None:
    """C626: several files read again and still a stub are split in the note's own order, failed,
    unreadable, refused, a bucket holding none left out, plural words for several."""
    from agentsync import cli  # noqa: PLC0415
    from agentsync.model import CycleMode, CycleReport  # noqa: PLC0415

    report = CycleReport(
        run_id=3,
        mode=CycleMode.POLL,
        sources=(),
        changes=(),
        commit_sha=None,
        path_reread=3,
        path_reread_failed=3,
        path_reread_refused=2,
    )
    cli._out_path_reread_notes(report)
    assert _notes(capsys.readouterr().out) == [
        f"  note: 3 conversions {_SAID} were tried once more in this sync and are still a stub (1 failed, 2 "
        "refused; _sync/QUARANTINE.tsv in the docs folder gives their reasons), and they are not tried again"
    ]
    # B13 round 33 C754: a held one is its own bucket, last, and never counted failed
    cli._out_path_reread_notes(
        dataclasses.replace(report, path_reread=4, path_reread_failed=4, path_reread_held=1)
    )
    assert _notes(capsys.readouterr().out) == [
        f"  note: 4 conversions {_SAID} were tried once more in this sync and are still a stub (1 failed, 2 "
        "refused, 1 held by this sync; _sync/QUARANTINE.tsv in the docs folder gives their reasons), and "
        "they are not tried again"
    ]


def test_a_queued_path_stub_with_new_bytes_is_no_waiting_re_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C362 (review): the waiting count was every queued stub the one-shot did not reach, so a file whose new
    bytes this sync converted as a change was said to wait for the next sync of its source."""
    from agentsync import cli  # noqa: PLC0415
    from test_cycle import _run_record  # noqa: PLC0415

    config, src = make_env(tmp_path)
    word_package(src / "Contoso renamed doc.potx")
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    build_potx(src / "Contoso renamed doc.potx")  # new bytes: a deck template, converted as a change
    capsys.readouterr()
    cli.main(["sync", "--config", str(config.config_path)])
    assert _notes(capsys.readouterr().out) == []
    assert _row(config, "Contoso renamed doc.potx").state is RowState.LIVE
    assert "path_reread_waiting" not in _run_record(config)


def test_a_path_re_read_that_converts_says_it_is_now_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C362: a deck the older build failed on with the staging path in its reason converts now; the note
    says it is current."""
    from agentsync import cli  # noqa: PLC0415
    from test_cycle import _run_record  # noqa: PLC0415

    config, src = make_env(tmp_path)
    (src / "Contoso Quarterly.pptx").write_bytes(build_pptx_rich(tmp_path / "deck.pptx").read_bytes())

    def failing(self: PptxConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        raise ConversionError(f"python-pptx cannot read the deck: file '{path}' timed out")

    with monkeypatch.context() as mp:
        _older_build(mp)
        mp.setattr(PptxConverter, "convert", failing)
        for _ in range(4):
            assert run(config).exit_code == 0
    assert str(config.state_paths.root) in (_row(config, "Contoso Quarterly.pptx").state_reason or "")
    capsys.readouterr()
    cli.main(["sync", "--config", str(config.config_path)])
    out = capsys.readouterr().out
    assert _notes(out) == [
        f"  note: 1 conversion {_SAID} was tried once more in this sync and is now current"
    ]
    assert _row(config, "Contoso Quarterly.pptx").state is RowState.LIVE
    assert _run_record(config)["path_reread_current"] == 1


def test_pathless_replaces_the_longest_form_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B13 round 29 C183: with ``/tmp`` resolving to ``/private/tmp``, the shorter form went first and left
    ``/private`` behind (``file '/privateContoso Q3.pptx'``). Each loop now takes the longest form first."""
    from agentsync.convert._common import _homeless, _pathless  # noqa: PLC0415

    (tmp_path / "real" / "ab12").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    src = tmp_path / "link" / "ab12" / "Contoso Q3.pptx"
    real = src.resolve()
    assert real != src
    for given in (src, real):
        assert _pathless(f"file '{given}' is not", src, src.name) == "file 'Contoso Q3.pptx' is not"
        assert (
            _pathless(f"cannot open '{given.parent}/media/x.png'", src, src.name)
            == "cannot open 'media/x.png'"
        )
    (tmp_path / "home").mkdir()
    (tmp_path / "home-link").symlink_to(tmp_path / "home")
    monkeypatch.setenv("HOME", str(tmp_path / "home-link"))
    for given in (tmp_path / "home-link", tmp_path / "home"):
        assert _homeless(f"cannot open {given}/notes.txt") == "cannot open ~/notes.txt"


@pytest.mark.parametrize("gap", ["  ", "\t", "\\"])
def test_a_reason_loses_the_paths_before_its_whitespace_is_collapsed(tmp_path: Path, gap: str) -> None:
    """B13 round 29 C221: ``_reason`` collapsed whitespace first, so a staging folder with two spaces in a
    row, or a tab, no longer matched and its path stayed in the reason. B13 round 30 C364: only as written;
    an ``OSError`` and a ``PosixPath`` print the path with ``repr`` (a tab as ``\\t``, a backslash doubled),
    and that spelling kept the whole path."""
    from agentsync.convert import _reason  # noqa: PLC0415

    staged = tmp_path / f"Contoso{gap}State" / "staging" / "ab12"
    staged.mkdir(parents=True)
    src = staged / "Q3 deck.pptx"
    src.write_bytes(b"")
    for given in (src, src.resolve()):
        said = _reason(ValueError(f"file '{given}' is not a\nPowerPoint file"), src, src.name)
        assert said == "file 'Q3 deck.pptx' is not a PowerPoint file"
        assert _reason(ValueError(f"cannot open '{given.parent}/media/x.png'"), src, src.name) == (
            "cannot open 'media/x.png'"
        )
        assert _reason(ValueError(f"bad: {given!r}"), src, src.name) == "bad: PosixPath('Q3 deck.pptx')"
    raised: list[OSError] = []
    for path in (staged / "media" / "x.png", src / "x"):
        try:
            path.open("rb")
        except OSError as exc:
            raised.append(exc)
    said = [_reason(exc, src, src.name) for exc in raised]
    assert said == [
        "[Errno 2] No such file or directory: 'media/x.png'",
        "[Errno 20] Not a directory: 'Q3 deck.pptx/x'",
    ], said
    assert not [s for s in said if "staging" in s or "Contoso" in s]


def test_a_staging_folder_with_two_spaces_leaks_no_path_into_the_docs_repo(tmp_path: Path) -> None:
    """C221 end to end: ``state_dir`` with two spaces in a row and a deck python-pptx refuses by its path."""
    from test_review_fixes import cfg_text, write_config  # noqa: PLC0415

    src = tmp_path / "source"
    src.mkdir()
    (src / "README.txt").write_text("sentinel\n", encoding="utf-8")
    text = cfg_text(tmp_path, src).replace(str(tmp_path / "state"), str(tmp_path / "Contoso  State"))
    config = write_config(tmp_path, text)
    assert "  " in str(config.state_paths.staging)
    (src / "Contoso Quarterly.pptx").write_bytes(build_potx(tmp_path / "t.potx").read_bytes())
    assert run(config).exit_code == 0
    row = _row(config, "Contoso Quarterly.pptx")
    assert "file 'Contoso Quarterly.pptx' is not a PowerPoint file" in (row.state_reason or "")
    staging = str(config.state_paths.staging)
    forms = {staging, " ".join(staging.split()), "/staging/", "Contoso State/"}  # README names the state dir
    for path in config.docs_repo.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(config.docs_repo).parts:
            data = path.read_text(encoding="utf-8", errors="replace")
            assert [f for f in forms if f in data] == [], path
    blobs = all_objects(config.docs_repo)
    assert not [f for f in forms if f.encode() in blobs]


@pytest.mark.parametrize("kind", ["missing", "not executable"])
def test_a_converter_that_cannot_start_names_no_home_folder(tmp_path: Path, kind: str) -> None:
    """B13 round 29 C182: the converter-unavailable FAILED reason was built with no staged file, so
    ``_pathless`` never ran, and ``[convert] pandoc_path = "~/bin/pandoc"`` put the home folder in the page,
    QUARANTINE.tsv, STATE.md and the manifest; the cycle's waiting line (cycle.py's ``convert_reason``)
    rebuilt it on the next sync. The home fold now runs on every reason."""
    if kind == "not executable":
        (Path.home() / "bin").mkdir()
        (Path.home() / "bin" / "pandoc").write_text("#!/bin/sh\n", encoding="utf-8")
        (Path.home() / "bin" / "pandoc").chmod(0o644)
    config, src = make_env(tmp_path, extra='\n[convert]\npandoc_path = "~/bin/pandoc"\n')
    (src / "contoso-notes.rtf").write_text(r"{\rtf1\ansi Contoso notes\par}", encoding="utf-8")
    home = str(Path.home())
    for _ in range(2):  # the second sync is the one that rebuilds the waiting line
        run(config)
        assert _leaks(config) == []
        assert home.encode() not in all_objects(config.docs_repo)
    reason = _row(config, "contoso-notes.rtf").state_reason or ""
    assert reason.startswith("conversion failed: converter unavailable: ")
    if kind == "missing":
        assert reason.endswith("[convert] pandoc_path is not an absolute file: ~/bin/pandoc")
    else:
        assert "cannot run pandoc: " in reason and "~/bin/pandoc" in reason


def test_an_older_builds_converter_unavailable_reason_is_folded_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 30 C361: a stub an older build wrote while pandoc could not start kept the home folder in
    its page, QUARANTINE.tsv and the manifest for as long as pandoc still cannot start: such a stub is never
    converted, and its waiting line rewrites nothing. The next sync folds the stored reason in place, once,
    keeping its first cause, and a later waiting sync leaves the page as it is."""
    # a home folder of 150 characters: the stored reason (200) is cut inside it, the page's (300) is not
    home_dir = tmp_path / ("Contoso Home " * 20)[: max(12, 149 - len(str(tmp_path.resolve())))].strip()
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    config, src = make_env(tmp_path, extra='\n[convert]\npandoc_path = "~/bin/pandoc"\n')
    (src / "contoso-notes.rtf").write_text(r"{\rtf1\ansi Contoso notes\par}", encoding="utf-8")
    homes = {str(home_dir), str(home_dir.resolve())}  # a tmp folder is /var and /private/var
    from agentsync.convert import _common  # noqa: PLC0415

    with monkeypatch.context() as mp:  # the build before C182, and before this fold
        mp.setattr(convert_pkg, "_homeless", lambda text: text)
        mp.setattr(_common, "_homeless", lambda text: text)
        mp.setattr(cycle_mod._Cycle, "_fold_unavailable_reasons", lambda _self: None)
        run(config)
    page = "mirror/src/contoso-notes.rtf.md"
    stored = _row(config, "contoso-notes.rtf").state_reason or ""
    assert stored.endswith("…") and "~/bin" not in stored, "the older build cut it inside the home folder"
    assert [h for h in homes if h in page_fm(config, page)["reason"]]
    run(config)
    reason = _row(config, "contoso-notes.rtf").state_reason or ""
    # from the page's full text, as the cut inside the home folder lost what came after it
    assert reason.endswith("[convert] pandoc_path is not an absolute file: ~/bin/pandoc"), reason
    assert "Contoso Home" not in reason
    for rel in (page, "_sync/QUARANTINE.tsv", "_manifest/src.jsonl"):
        assert "Contoso Home" not in git(config.docs_repo, "show", f"HEAD:{rel}"), rel
    assert page_fm(config, page)["reason"].endswith("~/bin/pandoc")
    assert _leaks(config) == []
    before = (config.docs_repo / page).read_bytes()
    run(config)
    assert (config.docs_repo / page).read_bytes() == before, "a waiting sync leaves the stub as it is"


def test_a_paused_sources_converter_unavailable_reason_is_folded_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C361 (review): the fold read only live sources but wrote its once-per-manifest key, so a paused
    source's stub, which QUARANTINE.tsv still lists, kept the home folder for good."""
    extra = '\n[convert]\npandoc_path = "~/bin/pandoc"\n'
    config, src = make_env(tmp_path, extra=extra)
    (src / "contoso-notes.rtf").write_text(r"{\rtf1\ansi Contoso notes\par}", encoding="utf-8")
    from agentsync.convert import _common  # noqa: PLC0415

    with monkeypatch.context() as mp:
        mp.setattr(convert_pkg, "_homeless", lambda text: text)
        mp.setattr(_common, "_homeless", lambda text: text)
        mp.setattr(cycle_mod._Cycle, "_fold_unavailable_reasons", lambda _self: None)
        run(config)
    home = str(Path.home().resolve())
    assert home in (_row(config, "contoso-notes.rtf").state_reason or "")
    paused, _ = make_env(tmp_path, extra=extra, source_extra='state = "paused"')
    run(paused)
    assert home not in (_row(paused, "contoso-notes.rtf").state_reason or "")


def test_a_stub_with_a_path_needs_no_home_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """C181: ``_stub_with_a_path`` now runs in the cycle, so a Mac with no home folder to read (no HOME, no
    password entry) gets the staging words alone, as ``_homeless`` does, never a RuntimeError."""
    from agentsync.convert._common import _stub_with_a_path  # noqa: PLC0415

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    assert _stub_with_a_path("file '/Volumes/x/agentsync/staging/ab12/a.pptx' is not")
    assert not _stub_with_a_path("not-ooxml: a Word document, not a deck")


def _round29_build(mp: pytest.MonkeyPatch) -> None:
    """The round 29 build: plain spellings only (before C364), no escaped fold (before C546)."""
    from agentsync.convert import _common  # noqa: PLC0415

    mp.setattr(
        _common, "spellings", lambda path: sorted({str(f) for f in _common._forms(path)}, key=len)[::-1]
    )
    mp.setattr(cycle_mod._Cycle, "_fold_escaped_reasons", lambda _self: None)


def test_a_stub_a_round_29_build_wrote_an_escaped_staging_path_into_is_folded_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 31 C546: a round 29 build folded only the plain spelling of the staging folder, and an
    ``OSError`` prints its path with ``repr`` (a tab as ``\\t``), so with ``state_dir`` under a folder with a
    tab the staging path stayed in the page, QUARANTINE.tsv and the manifest. Its one-shot re-read was
    used, so no conversion ever wrote the reason again. One full sync folds it in place, converting
    nothing."""
    from test_review_fixes import cfg_text, write_config  # noqa: PLC0415

    src = tmp_path / "source"
    src.mkdir()
    (src / "README.txt").write_text("sentinel\n", encoding="utf-8")
    text = cfg_text(tmp_path, src).replace(str(tmp_path / "state"), str(tmp_path / "Contoso\tState"))
    config = write_config(tmp_path, text)
    (src / "Contoso Quarterly.pptx").write_bytes(build_potx(tmp_path / "t.potx").read_bytes())
    tries: list[str] = []

    def refuses(self: PptxConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        tries.append(name)
        (path / "x").open("rb")  # a real NotADirectoryError, which names the staged path with repr

    monkeypatch.setattr(PptxConverter, "convert", refuses)
    with monkeypatch.context() as mp:
        _round29_build(mp)
        for _ in range(5):
            assert run(config).exit_code == 0
    # the first, the retries, and the one-shot re-read when the stored reason kept "/staging/" (a short tmp)
    assert len(tries) >= 1 + cycle_mod._CONVERT_RETRIES, tries
    escaped = "Contoso\\tState"
    page = "mirror/src/contoso-quarterly.pptx.md"
    assert escaped in (_row(config, "Contoso Quarterly.pptx").state_reason or "")
    assert escaped in git(config.docs_repo, "show", "HEAD:_sync/QUARANTINE.tsv")
    tries.clear()
    assert run(config).exit_code == 0
    assert tries == [], "folded in place: no conversion"
    reason = _row(config, "Contoso Quarterly.pptx").state_reason or ""
    assert reason == "conversion failed: [Errno 20] Not a directory: 'Contoso Quarterly.pptx/x'", reason
    for rel in (page, "_sync/QUARANTINE.tsv", "_manifest/src.jsonl"):
        shown = git(config.docs_repo, "show", f"HEAD:{rel}")
        assert escaped not in shown and "/staging/" not in shown, rel
    assert page_fm(config, page)["reason"].endswith("Not a directory: 'Contoso Quarterly.pptx/x'")
    before = (config.docs_repo / page).read_bytes()
    assert run(config).exit_code == 0
    assert tries == [] and (config.docs_repo / page).read_bytes() == before, "once per manifest"


def test_names_a_path_reads_a_reason_cut_inside_the_escaped_staging_folder() -> None:
    """B13 round 31 C547: the cut-inside-staging branch compared only the plain spelling, but a stored reason
    holds the escaped one an ``OSError`` prints (a tab as ``\\t``), whose real tab ``_one_line`` would have
    flattened anyway."""
    from types import SimpleNamespace  # noqa: PLC0415

    fake = SimpleNamespace(staging=Path("/Volumes/Contoso\tState/agentsync/staging"))
    fake._staging_roots = lambda: cycle_mod._Cycle._staging_roots(fake)  # type: ignore[attr-defined]
    fake._cut_floor = lambda root: cycle_mod._Cycle._cut_floor(fake, root)  # type: ignore[attr-defined]
    names = lambda reason: cycle_mod._Cycle._names_a_path(fake, reason)  # type: ignore[arg-type]  # noqa: E731
    head = "conversion failed: [Errno 20] Not a directory: '/Volumes/Contoso"
    assert names(head + "\\tState/agentsync/sta…")
    assert names(head + " State/agentsync/sta…"), "a real tab flattened to a space"
    assert names(head + "\tState/agentsync/sta…")
    assert not names("conversion failed: [Errno 20] Not a directory: 'Contoso Quarterly.pptx/x'")


def test_a_fold_whose_page_could_not_be_written_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 31 C564, C576: the converter-unavailable fold wrote its once-per-manifest key even when a
    page rewrite failed, so the stored reason was folded and the page kept the home folder for good. The key
    now waits for a run whose rewrites all land."""
    home_dir = tmp_path / "Contoso Home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    config, src = make_env(tmp_path, extra='\n[convert]\npandoc_path = "~/bin/pandoc"\n')
    (src / "contoso-notes.rtf").write_text(r"{\rtf1\ansi Contoso notes\par}", encoding="utf-8")
    from agentsync.convert import _common  # noqa: PLC0415
    from agentsync.publish import Publisher  # noqa: PLC0415

    with monkeypatch.context() as mp:  # the build before C182, and before the fold
        mp.setattr(convert_pkg, "_homeless", lambda text: text)
        mp.setattr(_common, "_homeless", lambda text: text)
        mp.setattr(cycle_mod._Cycle, "_fold_unavailable_reasons", lambda _self: None)
        run(config)
    page = "mirror/src/contoso-notes.rtf.md"
    assert "Contoso Home" in page_fm(config, page)["reason"]
    real = Publisher.write_pages
    calls: list[int] = []

    def full_disk(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as mp:
        mp.setattr(Publisher, "write_pages", full_disk)
        run(config)
    assert calls, "the fold tried the page"
    assert "Contoso Home" not in (_row(config, "contoso-notes.rtf").state_reason or "")
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._UNAVAILABLE_FOLD_META) is None, (
            "a failed rewrite keeps the key unwritten"
        )
    monkeypatch.setattr(Publisher, "write_pages", real)
    run(config)
    assert "Contoso Home" not in git(config.docs_repo, "show", f"HEAD:{page}")
    assert _leaks(config) == []
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._UNAVAILABLE_FOLD_META) == "1"
    before = (config.docs_repo / page).read_bytes()
    run(config)
    assert (config.docs_repo / page).read_bytes() == before


def test_pathless_cuts_every_quote_spelling_and_the_folder_named_alone(tmp_path: Path) -> None:
    """B13 round 31 C578: an error naming a file with a double quote prints its folder in single quotes, each
    apostrophe as ``\\'``, which no spelling matched; and an error naming the staged folder alone kept it,
    since only ``<folder>/`` was cut. The folder alone is cut at a path's end only, to fixed words."""
    from agentsync.convert._common import _FOLDER_WORD, _pathless  # noqa: PLC0415

    staged = tmp_path / "Contoso's state" / "staging" / "abc0123456789def"
    staged.mkdir(parents=True)
    src = staged / "deck.pdf"
    src.write_bytes(b"")
    for name in ('a"b.png', 'imgs/x"y.png'):
        exc = FileNotFoundError(2, "No such file or directory", str(staged / name))
        assert _pathless(str(exc), src, src.name) == f"[Errno 2] No such file or directory: '{name}'"
    for exc in (
        FileNotFoundError(2, "No such file or directory", str(staged)),
        IsADirectoryError(21, "Is a directory", str(staged)),
    ):
        said = _pathless(str(exc), src, src.name)
        assert said.endswith(f'"{_FOLDER_WORD}"') and "Contoso" not in said, said
    plain = tmp_path / "Contoso state" / "staging" / "abc0123456789def"
    plain.mkdir(parents=True)
    exc = FileNotFoundError(2, "No such file or directory", str(plain))
    assert _pathless(str(exc), plain / "deck.pdf", "deck.pdf") == (
        f"[Errno 2] No such file or directory: '{_FOLDER_WORD}'"
    )
    assert _pathless(f"cannot list {plain}", plain / "deck.pdf", "deck.pdf") == f"cannot list {_FOLDER_WORD}"
    sibling = f"cannot open '{plain}2/x.png'"
    assert _pathless(sibling, plain / "deck.pdf", "deck.pdf") == sibling, "a sibling folder stays whole"


def test_pathless_cuts_the_folder_named_alone_before_a_period_a_bracket_or_an_escaped_quote(
    tmp_path: Path,
) -> None:
    """B13 round 32 C646: the folder named alone was kept whole before a sentence-ending ``.``, a ``>``
    (``<DirEntry …>``) or an escaped quote. A bare ``.`` is no end, so a dotted sibling stays whole, and
    whitespace stays one, so the first of two paths is still cut."""
    from agentsync.convert._common import _FOLDER_WORD, _pathless  # noqa: PLC0415

    staged = tmp_path / "Contoso state" / "staging" / "abc0123456789def"
    staged.mkdir(parents=True)
    src = staged / "deck.pdf"
    cut = lambda text: _pathless(text, src, "deck.pdf")  # noqa: E731
    assert cut(f"cannot list {staged}.") == f"cannot list {_FOLDER_WORD}."
    assert cut(f"cannot list {staged}. Try again") == f"cannot list {_FOLDER_WORD}. Try again"
    assert cut(f"<DirEntry {staged}>") == f"<DirEntry {_FOLDER_WORD}>"
    assert cut(f"cannot open {staged}\\'") == f"cannot open {_FOLDER_WORD}\\'"
    assert cut(f"cp {staged} /Volumes/Contoso/x") == f"cp {_FOLDER_WORD} /Volumes/Contoso/x"
    for sibling in (f"cannot open '{staged}.bak/x.png'", f"cannot open '{staged}2/x.png'"):
        assert cut(sibling) == sibling, "a sibling that goes on past the folder with no end stays whole"
    # B13 round 33 C752: one that goes on with an end is cut, so the docstring no longer says every sibling
    # stays whole; it points at _PATH_END for the ends
    assert cut(f"cannot open '{staged},v2/x.png'") == f"cannot open '{_FOLDER_WORD},v2/x.png'"
    doc = " ".join((_pathless.__doc__ or "").split())
    assert "a sibling whose name extends it with no space stays whole" not in doc, doc
    assert "goes on past it with a character that is no end" in doc and "(``_PATH_END``)" in doc, doc


def test_a_home_with_an_apostrophe_is_folded_in_every_quote_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C578's home half: ``_homeless`` and ``_stub_with_a_path`` take the forced single-quote spelling too."""
    from agentsync.convert._common import _homeless, _stub_with_a_path  # noqa: PLC0415

    home = tmp_path / "Contoso's home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    exc = FileNotFoundError(2, "No such file or directory", str(home / 'a"b.txt'))
    assert "\\'" in str(exc)
    assert _homeless(str(exc)) == "[Errno 2] No such file or directory: '~/a\"b.txt'"
    assert _stub_with_a_path(str(exc))


def test_spellings_go_longest_first_for_a_folder_ending_in_a_backslash(tmp_path: Path) -> None:
    """B13 round 31 C551: the plain spelling of a path ending in a backslash is a prefix of its escaped one,
    so only the longest-first order keeps either replace from spoiling the other."""
    from agentsync.convert._common import _pathless, spellings  # noqa: PLC0415

    assert "no substring" not in (spellings.__doc__ or ""), "the docstring says only: longest first"
    staged = tmp_path / "Contoso\\"
    found = spellings(staged)
    assert [len(s) for s in found] == sorted({len(s) for s in found}, reverse=True), "longest first"
    src = staged / "notes.docx"
    assert _pathless(f"[Errno 2] No such file: {str(src)!r}", src, "notes.docx") == (
        "[Errno 2] No such file: 'notes.docx'"
    )
    # B13 round 32 C647: an input whose fold depends on the order, a staged file whose own name ends in a
    # backslash: shortest first leaves 'notes.docx\\'
    src = tmp_path / "Contoso" / "notes\\"
    assert _pathless(f"[Errno 2] No such file: {str(src)!r}", src, "notes.docx") == (
        "[Errno 2] No such file: 'notes.docx'"
    )


def test_the_escaped_fold_takes_a_graph_staged_file_and_cuts_only_a_cut_text() -> None:
    """C546 (review): a Graph source stages a file as ``<staging>/<16 hex><suffix>`` (drive, mail, teams),
    not the local ``<16 hex>/<name>``, so the fold left its escaped path whole; and a text not cut lost a tail
    that only looked like the start of the staging folder."""
    from types import SimpleNamespace  # noqa: PLC0415

    fake = SimpleNamespace(staging=Path("/Volumes/Contoso\tState/agentsync/staging"))
    fake._staging_roots = lambda: cycle_mod._Cycle._staging_roots(fake)  # type: ignore[attr-defined]
    fake._cut_floor = lambda root: cycle_mod._Cycle._cut_floor(fake, root)  # type: ignore[attr-defined]
    fake._cut_in_staging = lambda text, **kw: cycle_mod._Cycle._cut_in_staging(fake, text, **kw)  # type: ignore[attr-defined]
    fold = lambda text, name: cycle_mod._Cycle._fold_staging(fake, text, name)  # type: ignore[arg-type]  # noqa: E731
    root = "/Volumes/Contoso\\tState/agentsync/staging/0123456789abcdef"
    assert (
        fold(f"[Errno 2] No such file: '{root}.docx'", "Plan.docx") == "[Errno 2] No such file: 'Plan.docx'"
    )
    assert fold(f"Not a directory: '{root}.eml/x'", "Plan.eml") == "Not a directory: 'Plan.eml/x'"
    assert fold(f"Not a directory: '{root}/Plan.docx/x'", "Plan.docx") == "Not a directory: 'Plan.docx/x'"
    assert fold(f"cannot list '{root}/'", "Plan.docx") == "cannot list 'its folder'"
    assert fold("bad header in /Volumes/Contoso", "Plan.eml") == "bad header in /Volumes/Contoso", "not cut"
    assert fold("bad header in '/Volumes/Contoso\\tState/agentsync/sta…", "Plan.eml") == "bad header in '…"
    # B13 round 32 C645: a Graph file's folder is the staging folder itself, where a converter makes its temp
    # folders (``.media-``, ``.ocr-``); it lost no folder, and the folder named alone stayed whole
    staging = "/Volumes/Contoso\\tState/agentsync/staging"
    assert (
        fold(f"No such file: '{staging}/.media-ab12/a.wav'", "Talk.wav")
        == "No such file: '.media-ab12/a.wav'"
    )
    assert fold(f"No such file: '{staging}/.ocr-k3j2x/page-1.png'", "Plan.pdf") == (
        "No such file: '.ocr-k3j2x/page-1.png'"
    )
    for named in (staging, staging + "/"):
        assert (
            fold(f"No space left on device: '{named}'", "Talk.wav") == "No space left on device: 'its folder'"
        )
    assert fold(f"{root}.docx in {staging}", "Plan.docx") == "Plan.docx in its folder", (
        "the 16-hex forms first"
    )
    # C646 (review): the suffix ends where a path does, so what follows it stays
    assert fold(f"cannot open {root}.docx. Retry", "Plan.docx") == "cannot open Plan.docx. Retry"
    assert fold(f"<DirEntry {root}.docx>", "Plan.docx") == "<DirEntry Plan.docx>"
    assert fold(f"cannot open '{root}.docx\\'", "Plan.docx") == "cannot open 'Plan.docx\\'"
    assert fold(f"cannot open '{root}.tar.gz'", "Plan.tar.gz") == "cannot open 'Plan.tar.gz'"
    # B13 round 33 C751: a text cut inside the staged file's 16 hex kept those digits, and one cut inside its
    # Graph suffix lost its cut mark to the suffix's match (``file 'Plan.pptx``, read as never cut)
    head = "conversion failed: python-pptx cannot read the deck: file '"
    for cut in ("0123", "0123456789abcde", "0123456789abcdef", "0123456789abcdef.", "0123456789abcdef.pp"):
        assert fold(f"{head}{staging}/{cut}…", "Plan.pptx") == head + "…", cut
        assert fold(f"{head}{staging}/{cut}", "Plan.pptx") != head, f"not cut: {cut} is no cut path"
    assert fold(f"{head}{root}/Pl…", "Plan.pptx") == head + "Pl…", "a local name cut keeps its letters"
    # B13 round 34 C860: a Graph file cut exactly after its whole staged suffix (SourceItem.suffix: lower
    # case, ``.teams.json`` whole) is the row's name and the mark; any other cut still reads ``…``
    assert fold(f"{head}{root}.pptx…", "Plan.pptx") == head + "Plan.pptx…"
    assert fold(f"{head}{root}.pptx…", "Plan.PPTX") == head + "Plan.PPTX…"
    assert fold(f"{head}{root}.teams.json…", "2026-10.teams.json") == head + "2026-10.teams.json…"
    assert fold(f"{head}{root}.pptx…", "A\\qPlan.pptx") == head + "A\\qPlan.pptx…", "the name is no template"
    assert fold(f"{head}{root}.pptx…", "A\\1Plan.pptx") == head + "A\\1Plan.pptx…", "the name is no template"
    assert fold(f"{head}{root}.docx…", "Plan.pptx") == head + "…", "another suffix"
    assert fold(f"{head}{staging}/0123.pptx…", "Plan.pptx") == head + "…", "not 16 hex: no staged file"
    for cut in ("", "0123", "0123456789abcdef"):
        assert fold(f"{head}{staging}/{cut}…", "README") == head + "…", f"no suffix: {cut!r}"


@pytest.mark.parametrize("home", ["/Users/contosoquill", "/Volumes/Contoso Disk/Users/contosoquill"])
def test_a_reason_cut_inside_the_login_is_read_to_one_letter_past_the_homes_parent(
    monkeypatch: pytest.MonkeyPatch, home: str
) -> None:
    """B13 round 32 C660: the floor of 12 left a reason an older build cut 8 to 11 characters into a
    staging folder under the home folder (``/Users/c`` to ``/Users/cont``) unread by ``_names_a_path`` and
    uncut by ``_cut_in_staging``, so its stored reason kept up to 4 letters of the login. Under the home
    folder the floor is one letter past the home folder's parent, never above 12; outside it, or not cut,
    12."""
    from types import SimpleNamespace  # noqa: PLC0415

    monkeypatch.setenv("HOME", home)
    fake = SimpleNamespace(staging=Path(home) / "Library" / "Application Support" / "agentsync" / "staging")
    fake._staging_roots = lambda: cycle_mod._Cycle._staging_roots(fake)  # type: ignore[attr-defined]
    fake._cut_floor = lambda root: cycle_mod._Cycle._cut_floor(fake, root)  # type: ignore[attr-defined]
    fake._cut_in_staging = lambda text, **kw: cycle_mod._Cycle._cut_in_staging(fake, text, **kw)  # type: ignore[attr-defined]
    names = lambda reason: cycle_mod._Cycle._names_a_path(fake, reason)  # type: ignore[arg-type]  # noqa: E731
    fold = lambda text: cycle_mod._Cycle._fold_staging(fake, text, "Plan.docx")  # type: ignore[arg-type]  # noqa: E731
    head = "conversion failed: python-pptx cannot read the deck: file '"
    parent = len(home) - len("contosoquill")
    for letters in (1, 3, 4, 8):
        tail = str(fake.staging)[: parent + letters]
        assert names(head + tail + "…"), tail
        assert fake._cut_in_staging(head + tail + "…") == len(tail)
        assert fold(head + tail + "…") == head + "…", tail
    if parent + 3 < 12:
        assert not names(head + str(fake.staging)[: parent + 3]), "not cut: the floor stays 12"
    if parent + 1 > 12:  # B13 round 33 C753: a long parent, where the cap holds the floor at 12
        assert fake._cut_floor(str(fake.staging) + "/") == 12
        for n in (12, 20, parent):
            tail = str(fake.staging)[:n]
            assert names(head + tail + "…"), tail
            assert fake._cut_in_staging(head + tail + "…") == n
            assert fold(head + tail + "…") == head + "…", tail
    outside = SimpleNamespace(staging=Path("/Volumes/Contoso/agentsync/staging"))
    outside._staging_roots = lambda: cycle_mod._Cycle._staging_roots(outside)  # type: ignore[attr-defined]
    outside._cut_floor = lambda root: cycle_mod._Cycle._cut_floor(outside, root)  # type: ignore[attr-defined]
    assert not cycle_mod._Cycle._names_a_path(outside, head + "/Volumes/C…")  # type: ignore[arg-type]
    assert cycle_mod._Cycle._names_a_path(outside, head + "/Volumes/Con…")  # type: ignore[arg-type]


def _login_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, under: bool = True):  # type: ignore[no-untyped-def]
    """A made-up home ``<tmp>/Contoso Users/contosoquill``, and a config whose state is under it (or not)."""
    from test_review_fixes import cfg_text, write_config  # noqa: PLC0415

    home = tmp_path / "Contoso Users" / "contosoquill"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    src = tmp_path / "source"
    src.mkdir()
    (src / "README.txt").write_text("sentinel\n", encoding="utf-8")
    state = home / "Library" / "Application Support" / "agentsync" if under else tmp_path / "state"
    config = write_config(tmp_path, cfg_text(tmp_path, src).replace(str(tmp_path / "state"), str(state)))
    return config, src, home


@pytest.mark.parametrize("letters", [1, 3, 4])
def test_a_reason_cut_inside_the_login_is_folded_on_a_mac_past_both_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, letters: int
) -> None:
    """B13 round 32 C660: a stored reason an older build cut 1 to 4 letters into the login (8 to 11 characters
    into a staging folder under ``/Users``) was never re-read (C181) nor folded (C546), and its page, whose
    reason holds 300 characters, kept the whole path; on a Mac that already ran a round 29 or later build
    both once-per-manifest keys are written, so lowering the floor alone changed nothing. The
    ``convert-path-fold-v2:done`` pass folds it in place, row and page, converting nothing; a page it could
    not write keeps the key unwritten, and the run after leaves the page byte for byte. A home under tmp has
    a parent far longer than 12 characters, so the floor stays 12 here: the lowered floor itself is
    ``test_a_reason_cut_inside_the_login_is_read_to_one_letter_past_the_homes_parent``'s."""
    from agentsync.publish import Publisher  # noqa: PLC0415

    config, src, home = _login_home(tmp_path, monkeypatch)
    word_package(src / "Contoso renamed doc.potx")
    tries = _counted(monkeypatch)
    with monkeypatch.context() as mp:
        _older_build(mp)
        mp.setattr(cycle_mod._Cycle, "_fold_pathed_reasons", lambda _self: None)
        for _ in range(4):
            assert run(config).exit_code == 0
    page = "mirror/src/contoso-renamed-doc.potx.md"
    staging = str(config.state_paths.staging)
    assert staging in page_fm(config, page)["reason"], "the older build's page holds the whole path"
    row = _row(config, "Contoso renamed doc.potx")
    cut = staging[: len(str(home.parent)) + 1 + letters]
    with Manifest(config.state_paths.db) as m:
        m.set_state(
            "src",
            row.stable_id,
            RowState.QUARANTINED,
            f"conversion failed: python-pptx cannot read the deck: file '{cut}…",
        )
        m.set_meta(cycle_mod._PATH_REREAD_META, "1")  # a round 29 or later build ran both passes
        m.set_meta(cycle_mod._ESCAPED_FOLD_META, "1")
    tries.clear()

    def full_disk(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as mp:
        mp.setattr(Publisher, "write_pages", full_disk)
        run(config)
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._PATH_FOLD_V2_META) is None, "a failed rewrite keeps the key unwritten"
    assert "Contoso Users" not in (_row(config, "Contoso renamed doc.potx").state_reason or "")
    assert run(config).exit_code == 0
    assert tries == [], "folded in place: no conversion"
    reason = _row(config, "Contoso renamed doc.potx").state_reason or ""
    assert (
        reason.startswith("conversion failed: python-pptx cannot read the deck: ")
        and "/staging/" not in reason
    )
    assert "Contoso Users" not in reason and "Application Support" not in reason, reason
    for rel in (page, "_sync/QUARANTINE.tsv", "_manifest/src.jsonl"):
        assert "Contoso Users" not in git(config.docs_repo, "show", f"HEAD:{rel}"), rel
    # every file in the docs repo: the README names the state folder as ``~/Library/...``, never the home
    for path in config.docs_repo.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(config.docs_repo).parts:
            data = path.read_text(encoding="utf-8", errors="replace")
            assert "Contoso Users" not in data and staging not in data, path
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._PATH_FOLD_V2_META) == "1"
    before = (config.docs_repo / page).read_bytes()
    assert run(config).exit_code == 0
    assert tries == [] and (config.docs_repo / page).read_bytes() == before, "once per manifest"


def test_a_page_whose_path_starts_past_the_stored_cut_is_folded_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C660 (verifier): a reason whose path starts past character 200 leaves a clean stored reason, so neither
    the re-read nor either fold read it, while its page, cut at 300, kept the path. The v2 pass reads the
    page's reason too."""
    config, src, _home = _login_home(tmp_path, monkeypatch, under=False)
    (src / "Contoso Quarterly.pptx").write_bytes(build_potx(tmp_path / "t.potx").read_bytes())
    # B13 round 33 C750: the page's 300-character cut fell about 70 characters into the path, past the staging
    # root under a tmp shorter than 54 characters, so the exact end below held only on a long tmp; a filler
    # sized from the reason's own head puts it 20 characters into the root whatever tmp's length
    words = "the deck's Contoso parts " + "cannot be read " * 12
    filler = "~" * max(0, 299 - 20 - len(f"conversion failed: {words}from '"))

    def refuses(self: PptxConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        raise ConversionError(f"{words}{filler}from '{path}'")

    monkeypatch.setattr(PptxConverter, "convert", refuses)
    with monkeypatch.context() as mp:
        _older_build(mp)
        mp.setattr(cycle_mod._Cycle, "_fold_pathed_reasons", lambda _self: None)
        for _ in range(4):
            assert run(config).exit_code == 0
    page = "mirror/src/contoso-quarterly.pptx.md"
    stored = _row(config, "Contoso Quarterly.pptx").state_reason or ""
    shown = page_fm(config, page)["reason"]
    tmp = str(tmp_path.resolve())
    assert tmp not in stored and tmp[:40] not in stored, "the stored cut falls before the path"
    root = str(config.state_paths.staging.resolve()) + "/"
    assert shown.endswith(f"from '{root[:20]}…"), "the page holds the path, cut 20 characters into the root"
    assert run(config).exit_code == 0
    for rel in (page, "_sync/QUARANTINE.tsv", "_manifest/src.jsonl"):
        assert tmp[:40] not in git(config.docs_repo, "show", f"HEAD:{rel}"), rel
    assert page_fm(config, page)["reason"].endswith("from '…")
    before = (config.docs_repo / page).read_bytes()
    assert run(config).exit_code == 0
    assert (config.docs_repo / page).read_bytes() == before


def test_pathless_never_leaves_an_empty_quote_for_a_folder_with_a_trailing_slash(tmp_path: Path) -> None:
    """C578 (review): an error naming the staged folder with a slash after it lost the folder and the slash
    and read ``''``."""
    from agentsync.convert._common import _FOLDER_WORD, _pathless  # noqa: PLC0415

    staged = tmp_path / "Contoso state" / "staging" / "abc0123456789def"
    said = _pathless(f"[Errno 21] Is a directory: '{staged}/'", staged / "deck.pdf", "deck.pdf")
    assert said == f"[Errno 21] Is a directory: '{_FOLDER_WORD}'", said


def test_a_legacy_retry_of_a_stub_whose_reason_named_a_folder_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B13 round 31 C506: on the upgrade path from a build before the retry, the legacy retry takes the stub
    whose stored reason named the staging folder before the path re-read can, so no note said so and
    ``path_reread`` stayed 0. ``legacy_pathed`` counts it, and its legacy note says the reason named a
    folder; ``path_reread`` is untouched."""
    import sqlite3  # noqa: PLC0415

    from agentsync import cli  # noqa: PLC0415
    from test_cycle import _run_record  # noqa: PLC0415

    config, src = make_env(tmp_path)
    word_package(src / "Contoso renamed doc.potx")
    with monkeypatch.context() as mp:
        _older_build(mp)
        for _ in range(4):
            assert run(config).exit_code == 0
    with sqlite3.connect(config.state_paths.db) as db:  # a build that predates the retry
        db.execute("DELETE FROM meta WHERE key LIKE 'convert-retry%'")
    capsys.readouterr()
    cli.main(["sync", "--config", str(config.config_path)])
    notes = _notes(capsys.readouterr().out)
    assert [n for n in notes if "(its stored reason named a folder on this Mac)" in n] == notes, notes
    assert len(notes) == 1, notes
    record = _run_record(config)
    assert (record["legacy_retried"], record["legacy_pathed"]) == (1, 1), record
    assert "path_reread" not in record, record


def test_a_legacy_retry_of_some_stubs_with_a_folder_says_how_many(capsys: pytest.CaptureFixture[str]) -> None:
    """C506: when only some of the legacy retry's stubs named a folder, a note of its own counts them."""
    from agentsync import cli  # noqa: PLC0415
    from agentsync.model import CycleMode, CycleReport  # noqa: PLC0415

    report = CycleReport(
        run_id=1,
        mode=CycleMode.POLL,
        sources=(),
        changes=(),
        commit_sha=None,
        legacy_retried=3,
        legacy_pathed=1,
    )
    cli._print_report(report)
    notes = _notes(capsys.readouterr().out)
    assert notes[-1] == (
        "  note: of those, 1 conversion an older build gave up on was tried again with a stored reason that "
        "named a folder on this Mac"
    ), notes
    assert not [n for n in notes if re.search(r"\bconverted\b|\bdeferred\b|\bread again\b", n)]


def test_the_registry_docstring_tells_a_cached_unreadable_stub_from_an_uncached_refusal() -> None:
    """B13 round 32 C669: the module docstring put the label refusal among the screens raised as
    UnreadableSourceError and cached, but ``convert_file`` returns a label refusal as REFUSED before the cache
    (``_label_refusal``), never cached; the guard's label screen is a backstop only."""
    from agentsync.convert import registry  # noqa: PLC0415

    doc = " ".join((registry.__doc__ or "").split())
    assert "missing sensitivity label -> ``refused: …``; all raised as UnreadableSourceError" not in doc
    assert "``not-ooxml`` are raised as UnreadableSourceError, so the result is an UNREADABLE stub" in doc
    assert "returns them as REFUSED results that are never cached" in doc and "only as a backstop" in doc


def test_the_text_converters_docstrings_give_the_decode_order_the_code_uses() -> None:
    """B13 round 32 C670: both docstrings said utf-8 first, then a BOM, and left out cp1252, while
    ``_common._decode_text`` takes a BOM first, then strict UTF-8, then cp1252, then latin-1, as the options'
    ``decode`` records; the spec copy in CONTRACTS.md said the same. Words only: the emitter and options
    stay."""
    from agentsync.convert import text  # noqa: PLC0415

    said = "decoded (UTF-8/UTF-16 BOM first, then strict UTF-8, then cp1252, else latin-1)"
    contracts = " ".join(
        (Path(__file__).parent.parent / "docs" / "design" / "CONTRACTS.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for doc in (text.__doc__, text.PlainTextConverter.__doc__):
        assert said in " ".join((doc or "").split()), doc
    assert contracts.count(said) == 2 and "decoded (utf-8, then utf-8-sig/utf-16 BOM" not in contracts
    assert text.PlainTextConverter(ConvertConfig()).options()["decode"] == "bom,utf-8,cp1252,latin-1"


def test_the_v2_fold_skips_only_a_stub_whose_re_read_is_still_to_come() -> None:
    """C660 (review): the v2 pass skipped every ERROR stub whose stored reason names a path, so one whose
    one-shot re-read was used up (``_retries`` never converts it again) or a dataless one (never queued)
    was left with its path once the key was written. It skips only a stub a conversion will still read."""
    from types import SimpleNamespace  # noqa: PLC0415

    from agentsync.model import Verdict  # noqa: PLC0415

    tries: dict[str, tuple[str, int]] = {}
    fake = SimpleNamespace(_names_a_path=lambda reason: "/staging/" in reason, _retry_load=lambda _sid: tries)

    def pending(**kw: object) -> bool:
        return cycle_mod._Cycle._reread_pending(fake, SimpleNamespace(id="src"), SimpleNamespace(**kw))  # type: ignore[arg-type]

    row = {
        "stable_id": "a1",
        "last_verdict": Verdict.ERROR,
        "dataless": False,
        "state_reason": "conversion failed: file '/Volumes/x/agentsync/staging/ab12/a.pptx'",
    }
    assert pending(**row), "queued and not read: its conversion writes the reason anew"
    tries["a1"] = ("h1", cycle_mod._CONVERT_RETRIES)
    assert pending(**row), "the one-shot still to come"
    tries["a1"] = ("h1", cycle_mod._CONVERT_RETRIES + 1)
    assert not pending(**row), "the one-shot used up: folded"
    tries.clear()
    assert not pending(**{**row, "dataless": True}), "dataless: never queued, folded"
    assert not pending(**{**row, "last_verdict": None}), "settled: folded"
    assert not pending(**{**row, "state_reason": "conversion failed: not-ooxml"}), (
        "no path: nothing to wait for"
    )
