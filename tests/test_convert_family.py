"""B13 round 28 I39: a package of another Office family is "not the type its name says", and no conversion
error names the staged path or the home folder."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

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
from agentsync.policy import ENCRYPTED_PDF_REASON, NOT_OOXML_REASON
from agentsync.setup_report import quarantine_class
from test_convert_builders import build_potx, build_pptx_rich, make_zip
from test_review_fixes import all_objects, cfg_text, git, item_id, make_env, page_fm, run, write_config

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
        ("Contoso doc.potx", PptxConverter, "a PowerPoint deck"),
        ("Contoso doc.pptx", PptxConverter, "a PowerPoint deck"),
        ("Contoso doc.xlsx", XlsxConverter, "an Excel workbook"),
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
        ("Contoso doc.potx", PptxConverter, "a PowerPoint deck"),
        ("Contoso doc.pptx", PptxConverter, "a PowerPoint deck"),
        ("Contoso doc.xlsx", XlsxConverter, "an Excel workbook"),
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


def test_each_family_takes_one_noun_on_both_sides_of_a_stub_c3230(tmp_path: Path) -> None:
    """B13 round 47 C3230 (ruling 215): the found side of a family-mismatch stub came from ``_FAMILY_NOUN``
    (``a PowerPoint deck``, ``an Excel workbook``) and the expected side from each converter's own words
    (``a deck``, ``a workbook``), so a ``.potx`` read ``not a deck`` and a ``.vsdx`` ``a PowerPoint deck,
    not a Visio drawing``. The expected noun is ``_FAMILY_NOUN``'s too, Visio's kept apart (it is no family
    the check finds), and ``FAMILY_MISMATCH_RE``'s expected side stays any text, so a stub cached under the
    older noun still matches and is still re-read and classed."""
    word = word_package(tmp_path / "made.docx").read_bytes()
    for name, converter, family in (
        ("Contoso doc.potx", PptxConverter, "presentation"),
        ("Contoso doc.xlsx", XlsxConverter, "spreadsheet"),
    ):
        src = tmp_path / name
        src.write_bytes(word)
        with pytest.raises(UnreadableSourceError) as caught:
            converter(CFG).convert(src, name=name)
        noun = common_mod._FAMILY_NOUN[family]
        assert str(caught.value) == f"not-ooxml: a Word document, not {noun} (not the type its name says)"
    deck = tmp_path / "Contoso deck.vsdx"
    deck.write_bytes(build_pptx_rich(tmp_path / "rich.pptx").read_bytes())
    with pytest.raises(UnreadableSourceError) as caught:
        common_mod._check_ooxml_family(deck, "visio")
    visio = "not-ooxml: a PowerPoint deck, not a Visio drawing (not the type its name says)"
    assert str(caught.value) == visio
    assert "visio" not in common_mod._FAMILY_NOUN and common_mod._EXPECTED_NOUN["visio"] == "a Visio drawing"
    for old in ("a Word document, not a deck", "a Word document, not a workbook"):
        reason = f"not-ooxml: {old} (not the type its name says)"
        assert common_mod.FAMILY_MISMATCH_RE.fullmatch(reason), "an older cached stub still matches"
        assert quarantine_class(reason) == "not the type its name says"


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
        ("Contoso doc.potx", PptxConverter, "a PowerPoint deck"),
        ("Contoso doc.pptx", PptxConverter, "a PowerPoint deck"),
        ("Contoso doc.xlsx", XlsxConverter, "an Excel workbook"),
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


_ESCAPED_FAMILY = {
    "presentationml": "presentation&#109;l",
    "spreadsheetml": "spreadsheet&#109;l",
    "wordprocessingml": "wordprocessing&#109;l",
}
_DECOY_OF = {
    "potx": _WORD_MAIN,
    "pptx": _WORD_MAIN,
    "xlsx": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "docx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
}


def _decoyed(text: str, decoy: str, codec: str) -> str:
    """``text`` with every family word escaped (``presentation&#109;l``), so no byte read finds the real main
    type, ``decoy``'s literal in a comment before ``<Types``, and a UTF-16 declaration for a UTF-16 part."""
    for word, escaped in _ESCAPED_FAMILY.items():
        text = text.replace(word + ".", escaped + ".")
    if codec != "utf-8":
        text = re.sub(r"encoding=(['\"])UTF-8\1", r"encoding=\1UTF-16\1", text, flags=re.IGNORECASE)
    return text.replace("<Types", f'<!-- ContentType="{decoy}" --><Types', 1)


@pytest.mark.parametrize("codec", ["utf-8", *_BOMS])
@pytest.mark.parametrize("kind", ["potx", "pptx", "xlsx", "docx"])
def test_a_main_type_literal_in_a_comment_never_decides_the_family_c2708(
    tmp_path: Path, codec: str, kind: str
) -> None:
    """B13 round 44 C2708 with C2653 (ruling 185): `_ooxml_family` read the raw bytes first, comments
    included, and parsed only when they found nothing, so a `.potx` whose real Override was spelled
    `presentation&#109;l` and which held a Word literal in a comment was told a Word document: a cached,
    never-retried `not the type its name says` stub. The parse decides now, from content-types Override and
    Default elements only, so each codec's decoy (a Word literal in a template or deck, a deck literal in a
    workbook, a workbook literal in a Word document read by pandoc) converts."""
    from test_convert_builders import build_xlsx_rich  # noqa: PLC0415

    build = {
        "potx": build_potx,
        "pptx": build_pptx_rich,
        "xlsx": build_xlsx_rich,
        "docx": word_package,
    }[kind]
    src = _types_in(
        build(tmp_path / f"Contoso {kind}.{kind}"), codec, lambda t: _decoyed(t, _DECOY_OF[kind], codec)
    )
    family = {"potx": "presentation", "pptx": "presentation", "xlsx": "spreadsheet", "docx": "word"}[kind]
    assert common_mod._ooxml_family(src) == family
    converter = {"potx": PptxConverter, "pptx": PptxConverter, "xlsx": XlsxConverter, "docx": PandocConverter}
    units = converter[kind](CFG).convert(src, name=src.name)
    body = "\n".join(u.body for u in units)
    assert {"potx": "Contoso Quarterly Template", "docx": "Contoso notes"}.get(kind, "") in body and body


@pytest.mark.parametrize("codec", ["utf-8", *_BOMS])
def test_a_content_types_part_that_does_not_parse_is_read_by_its_bytes_c2708(
    tmp_path: Path, codec: str
) -> None:
    """B13 round 44 C2708 (ruling 185): the byte regex is the fallback for a part that does not parse, so a
    Word package named `.potx` whose `[Content_Types].xml` is malformed is still told as not its type; a part
    that parses, with a main type only in a comment, has no family."""
    broken = _types_in(word_package(tmp_path / "Contoso broken.potx"), codec, lambda t: t + "<")
    assert common_mod.content_types_tree(zipfile.ZipFile(broken).read("[Content_Types].xml")) is None
    assert common_mod._ooxml_family(broken) == "word"
    with pytest.raises(UnreadableSourceError, match="not the type its name says"):
        PptxConverter(CFG).convert(broken, name=broken.name)
    only_comment = _types_in(
        word_package(tmp_path / "Contoso comment.docx"),
        codec,
        lambda t: t.replace(_WORD_MAIN, "application/xml").replace(
            "<Types", f'<!-- ContentType="{_WORD_MAIN}" --><Types'
        ),
    )
    assert common_mod._ooxml_family(only_comment) is None


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


_ZERO_BYTE_TYPED = (
    *(".potx", ".vsdx", ".docx", ".xlsx", ".msg", ".drawio", ".pdf", ".eml", ".odt", ".teams.json", ".vtt"),
    *(".pptx", ".xlsm", ".vsdm"),  # B13 round 40 C1852: the Office guard's C930 branch held these too
)
"""Every name the default registry (no OCR or media engine) answers ``empty`` for at zero bytes."""


def test_the_typed_names_a_zero_byte_file_is_empty_under_are_fourteen_and_the_docs_say_so_c1852(
    tmp_path: Path,
) -> None:
    """B13 round 40 C1852 (ruling 141): CONTRACTS' C1647 paragraph said C930's rule `held for eleven typed
    names`, and test_convert_image.py's docstring 11, the count C930's test listed; the Office guard's C930
    branch held `.pptx`, `.xlsm` and `.vsdm` too, so the default registry answers `empty` for fourteen. The
    list, the registry's own answer and both texts agree."""
    registry = Registry.default(ConvertConfig())
    empty = []
    for suffix in sorted(registry.extensions()):
        src = tmp_path / f"zero{suffix}"
        src.write_bytes(b"")
        try:
            registry.for_name(src.name).convert(src, name=src.name)  # type: ignore[union-attr]
        except UnreadableSourceError as exc:
            if str(exc) == policy_mod.ZERO_BYTES_REASON:
                empty.append(suffix)
    assert sorted(empty) == sorted(_ZERO_BYTE_TYPED) and len(_ZERO_BYTE_TYPED) == 14
    root = Path(__file__).resolve().parents[1]
    contracts = " ".join((root / "docs/design/CONTRACTS.md").read_text(encoding="utf-8").split())
    assert "held for fourteen typed names" in contracts and "held for eleven typed names" not in contracts
    image = " ".join((root / "tests/test_convert_image.py").read_text(encoding="utf-8").split())
    assert "held for 14 typed names" in image and "held for 11 typed names" not in image


@pytest.mark.parametrize("suffix", _ZERO_BYTE_TYPED)
def test_a_zero_byte_file_under_a_typed_name_is_one_empty_stub_c930(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """B13 round 35 C930: a zero-byte ``.potx``, ``.vsdx`` or ``.msg`` was classed ``not the type its name
    says`` and a zero-byte ``.drawio`` ``conversion failed``, tried 3 times; no bytes have no type to disagree
    with the name, and the report's class list has ``empty``. Each is one ``empty-output`` stub, read once
    over three syncs.  B13 round 36 C1050 with C1117 (ruling 118): a zero-byte ``.pdf``, ``.eml``, ``.odt``
    or ``.teams.json`` was a ``conversion failed`` tried 3 times and a ``.vtt`` ``other``; each converter now
    says ``empty`` for zero bytes itself.  B13 round 40 C1852: ``.pptx``, ``.xlsm`` and ``.vsdm`` too."""
    config, src = make_env(tmp_path)
    name = f"Contoso zero{suffix}"
    (src / name).write_bytes(b"")
    tries = _tries(monkeypatch)
    for _ in range(3):
        assert run(config).exit_code == 0
    assert tries == [name]
    row = _row(config, name)
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.ZERO_BYTES_REASON
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
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.ZERO_BYTES_REASON
    assert quarantine_class(row.state_reason) == "empty"
    assert _row(config, "Contoso standup.vtt").state is RowState.LIVE, "a transcript still converts"
    earlier = _row(config, "Contoso earlier.vtt")  # read once more (B13 round 42 C2219, ruling 155)
    assert (
        earlier.state_reason == policy_mod.ZERO_BYTES_REASON
        and quarantine_class(earlier.state_reason) == "empty"
    )


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
    # nor screen an Office container before the cache, so its answer was cached (B13 round 45 C2924)
    old.setattr(convert_pkg, "_container_screen", lambda conv, src, name: None)


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
    answer while a fresh one said ``empty``. ``convert._identity``'s ``empty_screen`` option (Office names and
    ``.msg`` only) moves those keys. C1466's zero-byte skip is what keeps the older answer away in a real
    sync; this test turns it off around the second sync so the key move alone answers (B13 round 39 C1646):
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
    older_hash = _options_hash(config, f"Contoso earlier{suffix}")
    (src / f"Contoso zero{suffix}").write_bytes(b"")
    if real is not None:
        real(src / f"Contoso real{suffix}")
    with monkeypatch.context() as no_skip:  # C1466's zero-byte cache skip off: the key move alone answers
        no_skip.setattr(convert_pkg, "_zero_bytes", lambda src: False)
        assert run(config).exit_code == 0
    row = _row(config, f"Contoso zero{suffix}")
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.ZERO_BYTES_REASON
    assert quarantine_class(row.state_reason) == "empty"
    assert _options_hash(config, f"Contoso zero{suffix}") != older_hash
    if real is not None:
        assert _row(config, f"Contoso real{suffix}").state is RowState.LIVE, "a real file still converts"
    earlier = _row(config, f"Contoso earlier{suffix}")  # read once more (B13 round 42 C2219, ruling 155)
    assert (
        earlier.state_reason == policy_mod.ZERO_BYTES_REASON
        and quarantine_class(earlier.state_reason) == "empty"
    )


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
    assert row.state is RowState.QUARANTINED and row.state_reason == policy_mod.ZERO_BYTES_REASON
    assert quarantine_class(row.state_reason) == "empty"
    with Manifest(config.state_paths.db) as m:
        (out,) = m.outputs_for("src", item_id(config, f"Contoso zero{suffix}"))
    assert out.action_key == seeded.action_key, (
        "the same key: only the zero-byte skip keeps the old answer out"
    )
    assert ConverterCache(config.cache_dir).get(out.action_key).reason == older, "the old entry stays"
    if suffix == ".docx":
        assert _row(config, "Contoso real.docx").state is RowState.LIVE, "a real file still converts"
    earlier = _row(config, f"Contoso earlier{suffix}")  # read once more (B13 round 42 C2219, ruling 155)
    assert (
        earlier.state_reason == policy_mod.ZERO_BYTES_REASON
        and quarantine_class(earlier.state_reason) == "empty"
    )


def test_a_zero_byte_stub_an_older_build_settled_is_read_once_and_no_more_c2219(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 42 C2219 (ruling 155 amends ruling 132 once): a zero-byte row an older build settled with a
    converter's own type mismatch is read once more through `stage_if_empty` (no byte read) and ends `empty`;
    the source's `zero-bytes-v1` token then holds, so a read whose answer is the same mismatch is never
    repeated. A `.docx` that holds bytes keeps its `not-ooxml` stub unread, and a `no converter` stub is
    never one."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso earlier.docx").write_bytes(b"")
        (src / "Contoso bytes.docx").write_bytes(b"Contoso plain text, no ZIP header\n")
        (src / "Contoso blank.unknownext").write_bytes(b"")
        assert run(config).exit_code == 0
        assert _row(config, "Contoso earlier.docx").state_reason == NOT_OOXML_REASON
        assert _row(config, "Contoso bytes.docx").state_reason == NOT_OOXML_REASON
        tries = _tries(old)
        for _ in range(2):  # the older answer stays, so the re-read's answer is the same mismatch
            assert run(config).exit_code == 0
        assert tries.count("Contoso earlier.docx") == 1 and "Contoso bytes.docx" not in tries, tries
        assert "Contoso blank.unknownext" not in tries
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._ZERO_BYTES_META + "src") == "done"
    tries = _tries(monkeypatch)
    assert run(config).exit_code == 0
    assert tries == [], "the token holds: no zero-byte re-read again"
    assert _row(config, "Contoso earlier.docx").state_reason == NOT_OOXML_REASON


def test_the_zero_byte_reread_reads_no_byte_and_leaves_a_file_that_grew_c2219(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 42 C2219: the one-shot re-read stages through `stage_if_empty`, never a fetch; a probe that
    answers None (the file holds bytes now) stages nothing and converts nothing."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso earlier.msg").write_bytes(b"")
        assert run(config).exit_code == 0
    probed: list[str] = []
    monkeypatch.setattr(
        cycle_mod.LocalArm, "stage_if_empty", lambda self, item, dest: probed.append(item.rel_path) or None
    )
    tries = _tries(monkeypatch)
    assert run(config).exit_code == 0
    assert probed == ["Contoso earlier.msg"] and tries == []
    assert _row(config, "Contoso earlier.msg").state_reason == NOT_MSG_REASON


def _run_counts(config) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """The newest run's ``runs.counts_json``: what ``_Cycle._run_tally`` adds to its change counts."""
    with Manifest(config.state_paths.db) as m:
        (raw,) = m._db.execute("SELECT counts_json FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    counts: dict[str, int] = json.loads(raw)
    return counts


def test_a_zero_byte_reread_counts_as_read_again_and_never_as_converted_c2422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 43 C2422 (ruling 166): the zero-byte re-read added nothing to ``_reread_n``, so a sync that
    rewrote an older build's stub said neither ``read again`` nor ``converted`` for it, and install.sh's note
    and the run record left it out. It counts as a re-read now, in the report (the sync line's ``read
    again``), the run record's ``reread`` and never ``converted``; the next sync, with the token set, reads
    nothing and counts nothing."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso earlier.docx").write_bytes(b"")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso earlier.docx").state_reason == NOT_OOXML_REASON
    report = run(config)
    assert report.exit_code == 0
    assert _row(config, "Contoso earlier.docx").state_reason == policy_mod.ZERO_BYTES_REASON
    assert report.read_again == 1 and sum(s.converted for s in report.sources) == 0
    counts = _run_counts(config)
    assert (counts["reread"], counts["converted"]) == (1, 0), counts
    again = run(config)
    assert again.read_again == 0 and "reread" not in _run_counts(config)


def test_a_zero_byte_reread_that_fails_rolls_back_its_own_row_alone_c2442(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 43 C2442 (ruling 166): the zero-byte pass ran a batch in one transaction with no per-row
    savepoint, so a row that raised rolled back the rows before it, whose pages were committed anyway. Each
    row is read under its own savepoint now (C2249's shape): the row read first keeps its ``empty`` row and
    its probe mark (C2512) in the manifest and in the committed ``_manifest`` and QUARANTINE.tsv, the failing
    row rolls back, the error is reported, and the token stays unset, so the next sync reads that row, sets
    the token and takes the marks away. B13 round 44 C2703 with C2650 (ruling 175): the failing row's page,
    written before the raise, was committed ahead of its rolled-back row and counted in ``read again``; its
    page, books and both re-read counters now go back with the row (``_put_back``, as the recording replan
    does), so HEAD and the disk hold the page the manifest names, and ``read again`` is 1. ``_put_back``'s
    one residual stays: a page path ``write_pages`` wrote with no change recorded yet stays on disk."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso a.docx").write_bytes(b"")
        (src / "Contoso b.docx").write_bytes(b"")
        assert run(config).exit_code == 0
    real = cycle_mod._Cycle._zero_byte_reread
    called: list[str] = []

    def second_raises(self, src_cfg, arm, row, acc):  # type: ignore[no-untyped-def]
        called.append(row.name)
        real(self, src_cfg, arm, row, acc)
        if len(called) == 2:
            raise OSError("Contoso disk full")

    with monkeypatch.context() as failing:
        failing.setattr(cycle_mod._Cycle, "_zero_byte_reread", second_raises)
        report = run(config)
    assert report.exit_code == 0 and len(called) == 2
    assert "reading files again stopped: OSError" in report.sources[0].errors
    first, second = called
    assert _row(config, first).state_reason == policy_mod.ZERO_BYTES_REASON, "the row before it commits"
    assert _row(config, second).state_reason == NOT_OOXML_REASON, "the failing row rolls back"
    assert report.read_again == 1, "the failing row's re-read is not counted"
    with Manifest(config.state_paths.db) as m:
        (out,) = m.outputs_for("src", item_id(config, second))
    on_disk = (config.docs_repo / out.output_path).read_bytes()
    in_head = git(config.docs_repo, "show", f"HEAD:{out.output_path}")
    assert hashlib.sha256(on_disk).hexdigest() == out.page_sha256, "the page on disk is the row's"
    assert hashlib.sha256(in_head.encode("utf-8")).hexdigest() == out.page_sha256, "HEAD holds the row's page"
    assert NOT_OOXML_REASON in in_head and policy_mod.ZERO_BYTES_REASON not in in_head
    key = cycle_mod._ZERO_BYTES_META + "src"
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(key) is None, "the token stays unset: the next sync reads the row again"
        assert m.get_meta(f"{key}:{item_id(config, first)}") == "1"
        assert m.get_meta(f"{key}:{item_id(config, second)}") is None
    committed = git(config.docs_repo, "show", "HEAD:_sync/QUARANTINE.tsv")
    (line,) = [ln for ln in committed.splitlines() if first in ln]
    assert policy_mod.ZERO_BYTES_REASON in line, committed
    assert policy_mod.ZERO_BYTES_REASON in git(config.docs_repo, "show", "HEAD:_manifest/src.jsonl")
    tries = _tries(monkeypatch)
    assert run(config).exit_code == 0
    assert tries == [second], tries
    assert _row(config, second).state_reason == policy_mod.ZERO_BYTES_REASON
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(key) == "done"
        assert m.get_meta(f"{key}:{item_id(config, first)}") is None, "the marks go with the token"


def test_the_zero_byte_pass_keeps_what_the_recording_replan_keeps_c2703() -> None:
    """B13 round 44 C2703 with C2650 (ruling 175): the zero-byte pass is the recording replan's twin, and it
    kept none of what the replan's savepoint cannot roll back. Both now take the same snapshot before a
    row's savepoint and hand it to the one ``_put_back``; the zero-byte pass restores ``read again``'s
    counters too. B13 round 45 C2925: the zero-byte pass and the family re-read share one loop,
    ``_read_rows_once``, which holds the snapshot, so it is the zero-byte pass's side of the twin."""
    snapshot = (
        "files = self._page_files(src.id, row.stable_id)",
        "row.stable_id in acc.deferred_ids,",
        "acc.counted_as.get(row.stable_id),",
        "len(self._recording_queue.get(src.id, [])),",
        "src, row.stable_id, acc, files=files, books=books, changes=self.changes[mark:]",
    )
    for twin in (cycle_mod._Cycle._replan_recording_stubs, cycle_mod._Cycle._read_rows_once):
        code = inspect.getsource(twin)
        for line in snapshot:
            assert code.count(line) == 1, (twin.__name__, line)
        assert code.index("self._put_back(") < code.index("del self.changes[mark:]"), twin.__name__
    for once in (cycle_mod._Cycle._reread_zero_byte_stubs, cycle_mod._Cycle._reread_family_stubs):
        assert inspect.getsource(once).count("self._read_rows_once(src, acc, rows, ") == 1, once.__name__
    zero = inspect.getsource(cycle_mod._Cycle._read_rows_once)
    assert "reread = (self._reread_n, self._reread_s)" in zero
    assert "self._reread_n, self._reread_s = reread" in zero
    # B13 round 45 C2923 (ruling 196): the counts a first read moved go back with the row too
    assert zero.count("counted = self._row_books(row, acc)") == 1
    assert zero.count("self._restore_row_books(row, acc, counted)") == 1
    assert zero.index("counted = self._row_books(") < zero.index("with self.manifest.savepoint():")


def test_a_zero_byte_first_read_whose_publish_fails_counts_nothing_c2923(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 45 C2923 (ruling 196): the zero-byte pass put back ``read again``'s two counters when a
    row's savepoint rolled back, but not what a first read (its old page deleted, so not intact) counts
    before it publishes: ``converted``, ``read first``, the source's unreadable count and the run tally's
    ``converted_unreadable``. A publish that raised left a row that was never converted counted as one, and
    ``read first`` (1) above ``read again`` (0). ``_row_books`` keeps them before the savepoint and
    ``_restore_row_books`` puts them back; the next sync reads the row and counts it once."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso a.docx").write_bytes(b"")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso a.docx").state_reason == NOT_OOXML_REASON
    with Manifest(config.state_paths.db) as m:
        (out,) = m.outputs_for("src", item_id(config, "Contoso a.docx"))
    (config.docs_repo / out.output_path).unlink()  # the page is gone: the re-read is a first read
    real = cycle_mod._Cycle._publish

    def raises(self, src_cfg, row, *a, **kw):  # type: ignore[no-untyped-def]
        if row.name == "Contoso a.docx":
            raise OSError("Contoso disk full")
        return real(self, src_cfg, row, *a, **kw)

    with monkeypatch.context() as failing:
        failing.setattr(cycle_mod._Cycle, "_publish", raises)
        report = run(config)
    assert report.exit_code == 0
    assert "reading files again stopped: OSError" in report.sources[0].errors
    assert _row(config, "Contoso a.docx").state_reason == NOT_OOXML_REASON, "the row rolls back"
    assert (report.read_again, report.read_first) == (0, 0), report
    assert sum(s.converted for s in report.sources) == 0
    assert sum(s.unreadable for s in report.sources) == 0
    counts = _run_counts(config)
    assert counts.get("converted") == 0, counts
    assert not {"converted_unreadable", "reread_first", "reread"} & set(counts), counts
    # the failed run put the page deleted by hand back from HEAD (B13 round 48 C3534): gone again, the next
    # re-read is a first read once more
    assert (config.docs_repo / out.output_path).is_file()
    (config.docs_repo / out.output_path).unlink()
    report = run(config)
    assert report.exit_code == 0
    assert _row(config, "Contoso a.docx").state_reason == policy_mod.ZERO_BYTES_REASON
    assert (report.read_again, report.read_first) == (1, 1), report
    assert sum(s.converted for s in report.sources) == 1
    counts = _run_counts(config)
    assert (counts["converted"], counts["reread_first"], counts["converted_unreadable"]) == (1, 1, 1), counts


def test_a_zero_byte_row_out_of_scope_keeps_the_token_unset_and_is_read_when_it_returns_c2512(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 43 C2512 (ruling 166): the source-wide token was set once every row in scope was read, so a
    row out of scope and not retired (the root moved to a folder walked incompletely, ``*.docx`` excluded)
    let it be set, and when the row came back unchanged it kept the older build's ``not-ooxml`` for good.
    The token is set only when no such row is left unmarked, out of scope included: the row is read once
    when it returns, and then the token holds."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso earlier.docx").write_bytes(b"")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso earlier.docx").state_reason == NOT_OOXML_REASON
    other = tmp_path / "other"
    (other / "locked").mkdir(parents=True)
    (other / "README.txt").write_text("sentinel\n", encoding="utf-8")
    moved = write_config(tmp_path, cfg_text(tmp_path, other, source_extra='exclude = ["*.docx"]'))
    key = cycle_mod._ZERO_BYTES_META + "src"
    (other / "locked").chmod(0)
    try:
        tries = _tries(monkeypatch)
        run(moved)
    finally:
        (other / "locked").chmod(0o755)
    assert tries == []
    assert _row(config, "Contoso earlier.docx").state_reason == NOT_OOXML_REASON, "out of scope, not retired"
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(key) is None, "a row out of scope is left unmarked: the token waits"
    config = write_config(tmp_path, cfg_text(tmp_path, src))
    assert run(config).exit_code == 0
    assert tries == ["Contoso earlier.docx"], "read once, when it is back in scope"
    assert _row(config, "Contoso earlier.docx").state_reason == policy_mod.ZERO_BYTES_REASON
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(key) == "done"
        assert m.get_meta(f"{key}:{item_id(config, 'Contoso earlier.docx')}") is None
    assert run(config).exit_code == 0
    assert tries == ["Contoso earlier.docx"], "the token holds"


def test_a_marked_zero_byte_row_is_never_probed_again_while_the_token_waits_c2512(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 43 C2512 (ruling 166): a row probed once keeps its mark, whatever the answer, and is never
    probed again while another row keeps the source's token unset. Here one row is not listed (its folder
    cannot be read, so the walk stops short and keeps it), and the listed row's probe answers None: that row
    stays a mismatch and is marked, the next sync probes nothing, and once the other row is listed again it
    alone is read, the token is set and the marks go."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_zero_byte_answers(old)
        (src / "Contoso x.docx").write_bytes(b"")
        (src / "locked").mkdir()
        (src / "locked" / "Contoso y.docx").write_bytes(b"")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso y.docx").state_reason == NOT_OOXML_REASON
    key = cycle_mod._ZERO_BYTES_META + "src"
    mark_x = f"{key}:{item_id(config, 'Contoso x.docx')}"
    probed: list[str] = []
    (src / "locked").chmod(0)
    try:
        with monkeypatch.context() as probe:
            probe.setattr(
                cycle_mod.LocalArm,
                "stage_if_empty",
                lambda self, item, dest: probed.append(item.name) or None,
            )
            for _ in range(2):
                run(config)
    finally:
        (src / "locked").chmod(0o755)
    assert probed == ["Contoso x.docx"], "a marked row is never probed again while the token waits"
    assert _row(config, "Contoso x.docx").state_reason == NOT_OOXML_REASON
    assert _row(config, "Contoso y.docx").state_reason == NOT_OOXML_REASON, "not listed, not retired"
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(key) is None, "the unlisted row is unmarked: the token waits"
        assert m.get_meta(mark_x) == "1"
    tries = _tries(monkeypatch)
    assert run(config).exit_code == 0
    assert tries == ["Contoso y.docx"], (
        "the unlisted row is read when it is listed again, the marked one never"
    )
    assert _row(config, "Contoso y.docx").state_reason == policy_mod.ZERO_BYTES_REASON
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(key) == "done"
        assert m.get_meta(mark_x) is None, "the marks go with the token"


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
    if screened:
        base["empty_screen"] = 1
    if convert_pkg._suffix(name) in policy_mod.OOXML_SUFFIXES:  # B13 round 45 C2925: the family rule
        base["family_parse"] = 1
    assert convert_pkg._identity(conv, name) == (conv.version(), options_hash(base))


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
    assert page_fm(config, page)["reason"].startswith("not-ooxml: a Word document, not a PowerPoint deck")
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
    assert not _stub_with_a_path("not-ooxml: a Word document, not a PowerPoint deck")


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
    stay. B13 round 47 C3321: UTF-32's marks are told before UTF-16's, and the summary names them, the
    options' ``decode`` unchanged, since only inputs that failed before decode now."""
    from agentsync.convert import text  # noqa: PLC0415

    said = "decoded (UTF-8/UTF-32/UTF-16 BOM first, then strict UTF-8, then cp1252, else latin-1)"
    contracts = " ".join(
        (Path(__file__).parent.parent / "docs" / "design" / "CONTRACTS.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for doc in (text.__doc__, text.PlainTextConverter.__doc__):
        assert said in " ".join((doc or "").split()), doc
    assert contracts.count(said) == 2 and "decoded (utf-8, then utf-8-sig/utf-16 BOM" not in contracts
    assert "(UTF-8/UTF-16 BOM first" not in contracts, "round 47 C3321: UTF-32's marks are told too"
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


def _workbook(path: Path, *, preamble: bytes = b"") -> Path:
    """A small real workbook; with ``preamble``, the same package behind those bytes, which zipfile still
    reads, so H1 is the clean package's (B13 round 45 C2924)."""
    from openpyxl import Workbook  # noqa: PLC0415

    book = Workbook()
    book.active.append(["Contoso", "plan"])
    clean = path.with_name("clean-" + path.name)
    book.save(clean)
    path.write_bytes(preamble + clean.read_bytes())
    clean.unlink()
    return path


@pytest.mark.parametrize("order", ["clean first", "preamble first"])
def test_an_office_container_the_screen_rejects_never_shares_the_clean_packages_cache_c2924(
    tmp_path: Path, order: str
) -> None:
    """B13 round 45 C2924 (ruling 196): H1 reads a ZIP's members, which zipfile finds behind a preamble, so a
    workbook with junk bytes in front shared the clean package's action key, and ``convert_file`` read the
    cache before the guard's container screen. Clean first, the preamble copy got the clean page from the
    cache; preamble first, its not-ooxml rejection took the key and the clean workbook got the stub. The
    container is screened after the labels and before ``cache.get`` now, and that answer is never stored: in
    either order the clean workbook is a page and the preamble copy is not-ooxml, never from the cache."""
    from agentsync.convert.canonical import canonical_hash  # noqa: PLC0415

    clean = _workbook(tmp_path / "Contoso clean.xlsx")
    pre = _workbook(tmp_path / "Contoso pre.xlsx", preamble=b"\x00Contoso junk" * 10)
    h_clean, h_pre = (canonical_hash(p, suffix=".xlsx") for p in (clean, pre))
    assert h_clean.sha256 == h_pre.sha256, "the shared key the fix guards"
    registry, cache = Registry.default(CFG), ConverterCache(tmp_path / "cache")

    def convert(path: Path):  # type: ignore[no-untyped-def]
        return convert_file(
            path,
            name=path.name,
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            canonical_sha256=h_clean.sha256,
            registry=registry,
            cache=cache,
        )

    seen = {}
    for path in (clean, pre) if order == "clean first" else (pre, clean):
        seen[path] = convert(path)
    assert seen[clean].status is ConversionStatus.OK, seen[clean]
    assert not seen[clean].from_cache, "the preamble copy stored nothing under the clean key"
    assert (seen[pre].status, seen[pre].reason) == (ConversionStatus.UNREADABLE, NOT_OOXML_REASON), seen[pre]
    assert not seen[pre].from_cache, "the clean page is never served to bytes the screen rejects"
    again = convert(pre)
    assert (again.status, again.from_cache) == (ConversionStatus.UNREADABLE, False), again


@pytest.mark.parametrize("first", ["clean", "preamble"])
def test_a_touched_office_file_whose_container_changes_is_converted_again_c2924(
    tmp_path: Path, first: str
) -> None:
    """B13 round 45 C2924 (ruling 196): the cycle's touched-not-changed shortcut kept the page a row had when
    a save added a preamble (H1 unchanged), and kept a not-ooxml stub when clean bytes with the same H1
    came back. ``same`` beside ``_relabelled`` gains the container term (``_container_moved``), so in both
    directions the bytes are converted, and the screen decides them."""
    config, src = make_env(tmp_path)
    junk = b"\x00Contoso junk" * 10
    path = src / "Contoso book.xlsx"
    states = []
    for preamble in (b"", junk, b"") if first == "clean" else (junk, b"", junk):
        _workbook(path, preamble=preamble)
        assert run(config).exit_code == 0
        row = _row(config, path.name)
        states.append((row.state, row.state_reason))
    page, stub = (RowState.LIVE, None), (RowState.QUARANTINED, NOT_OOXML_REASON)
    assert states == ([page, stub, page] if first == "clean" else [stub, page, stub]), states


_ENCRYPTED_PDF_PREAMBLE = b"%PDF-1.7\n<< /Encrypt << /Filter /Standard >> >>\n" + b"\x00Contoso junk" * 10


def test_an_office_stub_the_pdf_sniff_gave_is_converted_again_when_its_bytes_are_a_clean_zip_c3046(
    tmp_path: Path,
) -> None:
    """B13 round 46 C3046 (ruling 202): a non-ZIP Office name whose bytes hold a PDF header with ``/Encrypt``
    is screened ``encrypted-pdf`` (``policy.screen_file`` sniffs the PDF before the not-ooxml branch), and
    ``_CONTAINER_REASONS`` named only not-ooxml, empty-output and encrypted-office, so clean bytes with the
    same H1 kept the encrypted-pdf stub until their content changed. ``encrypted-pdf`` is one of the
    container's own answers: a clean, polyglot, clean run is a page, the stub, a page."""
    config, src = make_env(tmp_path)
    path = src / "Contoso book.xlsx"
    states = []
    for preamble in (b"", _ENCRYPTED_PDF_PREAMBLE, b""):
        _workbook(path, preamble=preamble)
        assert run(config).exit_code == 0
        row = _row(config, path.name)
        states.append((row.state, row.state_reason))
    page, stub = (RowState.LIVE, None), (RowState.QUARANTINED, ENCRYPTED_PDF_REASON)
    assert states == [page, stub, page], states


@pytest.mark.parametrize("first", ["junk", "pdf"])
def test_a_container_stub_whose_preamble_changes_container_is_converted_once_c3316(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    """B13 round 47 C3316 (ruling 215): on bytes that are not a ZIP at offset 0, ``_container_moved`` moved
    only a page or a family-mismatch stub, so a ``not-ooxml`` stub whose junk preamble became an encrypted
    PDF's (H1 unchanged: zipfile reads the members behind either) kept ``not-ooxml``, and the reverse kept
    ``encrypted-pdf``, for as long as the bytes stayed. A container stub there moves when
    ``policy.container_reason`` of what the bytes sniff to now differs from its reason: junk, PDF, junk and
    the reverse are each converted once to the fresh answer, and a quiet sync after each converts nothing."""
    config, src = make_env(tmp_path)
    clean = _workbook(tmp_path / "clean.xlsx").read_bytes()
    path = src / "Contoso book.xlsx"
    junk = b"\x00Contoso junk" * 10
    order = (junk, _ENCRYPTED_PDF_PREAMBLE, junk) if first == "junk" else (_ENCRYPTED_PDF_PREAMBLE, junk) * 2
    tries = _tries(monkeypatch)
    for step, preamble in enumerate(order[:3]):
        path.write_bytes(preamble + clean)
        assert run(config).exit_code == 0
        want = NOT_OOXML_REASON if preamble == junk else ENCRYPTED_PDF_REASON
        row = _row(config, path.name)
        assert (row.state, row.state_reason) == (RowState.QUARANTINED, want), (step, row.state_reason)
        assert tries == [path.name], (step, tries)
        tries.clear()
        assert run(config).exit_code == 0
        assert tries == [], f"step {step}: the stub agrees with its bytes, so a quiet sync reads nothing"
        assert _row(config, path.name).state_reason == want


def test_a_family_mismatch_stub_whose_bytes_gain_a_preamble_is_converted_once_c3096(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 46 C3096 (ruling 202): a Word package named ``.potx`` settles as the family-mismatch stub;
    junk put before the same ZIP leaves H1 unchanged, and ``_container_moved`` read only a page row as moved
    on non-ZIP bytes, so the stub kept the family reason where a fresh copy of those bytes is
    ``NOT_OOXML_REASON``.
    A family-mismatch stub was made from bytes that passed the container screen, so on non-ZIP bytes it is a
    move: converted once to ``NOT_OOXML_REASON``, which moves nothing there (no loop), and the family stub
    comes back when the junk is taken away."""
    config, src = make_env(tmp_path)
    path = word_package(src / "Contoso notes.potx")
    clean = path.read_bytes()
    assert run(config).exit_code == 0
    assert _row(config, path.name).state_reason == _DECK_STUB
    tries = _tries(monkeypatch)
    path.write_bytes(b"\x00Contoso junk" * 10 + clean)
    assert run(config).exit_code == 0
    assert tries == [path.name], tries
    assert _row(config, path.name).state_reason == NOT_OOXML_REASON
    tries.clear()
    assert run(config).exit_code == 0
    assert tries == [], "a not-ooxml stub on non-ZIP bytes is no move: no loop"
    path.write_bytes(clean)
    assert run(config).exit_code == 0
    assert tries == [path.name], tries
    assert _row(config, path.name).state_reason == _DECK_STUB


def _without_types_namespace(path: Path) -> Path:
    """``path``'s package with ``[Content_Types].xml``'s ``<Types>`` stripped of its ``xmlns``: Override and
    Default in no namespace, which openpyxl still reads by local name (B13 round 45 C2854)."""
    with zipfile.ZipFile(path) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    types = re.sub(rb'<Types xmlns="[^"]*"', b"<Types", parts["[Content_Types].xml"], count=1)
    assert types != parts["[Content_Types].xml"] and b"xmlns=" not in types.split(b">", 2)[1], types[:200]
    parts["[Content_Types].xml"] = types
    path.unlink()
    return make_zip(path, parts)


@pytest.mark.parametrize(
    ("make", "name", "converter", "message"),
    [
        ("word", "Contoso doc.xlsx", XlsxConverter, "a Word document, not an Excel workbook"),
        ("deck", "Contoso doc.xlsx", XlsxConverter, "a PowerPoint deck, not an Excel workbook"),
        ("word", "Contoso doc.pptx", PptxConverter, "a Word document, not a PowerPoint deck"),
    ],
)
def test_a_package_whose_content_types_have_no_namespace_is_still_not_the_type_its_name_says_c2854(
    tmp_path: Path,
    make: str,
    name: str,
    converter: type[PptxConverter] | type[XlsxConverter],
    message: str,
) -> None:
    """B13 round 45 C2854: round 44's parse-first family check read Override and Default in the
    content-types namespace only, so a package whose ``<Types>`` has no ``xmlns`` gave no family, and a Word
    package or a deck named ``.xlsx`` (or a Word package named ``.pptx``) went on to openpyxl or python-pptx
    as a failed conversion, tried twice more, where the byte read before it gave the cached ``not the type
    its name says`` stub. They are read in ``<Types>``' own namespace, or in none (or the content-types
    namespace, B13 round 47 C3264) when it has none, as openpyxl reads a part with no ``xmlns``; round 46
    C3047 narrowed this from any namespace: only ``<Types>``' direct children count, and a foreign or
    ``xmlns=""`` decoy is not read (B13 round 47 C3267)."""
    made = tmp_path / ("made" + Path(name).suffix)
    built = word_package(made) if make == "word" else build_pptx_rich(made)
    src = _without_types_namespace(built.rename(tmp_path / name))
    assert common_mod.content_types_tree(zipfile.ZipFile(src).read("[Content_Types].xml")) is not None
    with pytest.raises(UnreadableSourceError) as caught:
        converter(CFG).convert(src, name=name)
    assert str(caught.value) == f"not-ooxml: {message} (not the type its name says)"


def test_a_prefixed_content_types_child_under_a_no_namespace_types_is_read_c3264(tmp_path: Path) -> None:
    """B13 round 47 C3264 (ruling 215): C3047 read a no-namespace ``<Types>``' no-namespace children only, so
    a Word package named ``.xlsx`` whose ``<Types xmlns:ct=...>`` holds ``ct:Default`` and ``ct:Override``
    (openpyxl reads them by local name) gave no family and was C2854's failed conversion again. Under a
    no-namespace ``<Types>`` the content-types namespace's children are read too, never ``{*}``: a foreign
    child there is still not read, and under a namespaced ``<Types>`` nothing changes (C3047)."""
    src = word_package(tmp_path / "made.docx").rename(tmp_path / "Contoso prefixed.xlsx")
    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    types = parts["[Content_Types].xml"].decode("utf-8")
    prefixed = re.sub(r'<Types xmlns="[^"]*"', f'<Types xmlns:ct="{_TYPES}"', types, count=1)
    prefixed = re.sub(r"<(/?)(Default|Override)\b", r"<\1ct:\2", prefixed)
    assert prefixed != types and "<ct:Override" in prefixed and 'xmlns="' not in prefixed, prefixed[:300]
    parts["[Content_Types].xml"] = prefixed.encode("utf-8")
    src.unlink()
    make_zip(src, parts)
    assert common_mod._ooxml_family(src) == "word"
    with pytest.raises(UnreadableSourceError) as caught:
        XlsxConverter(CFG).convert(src, name=src.name)
    assert (
        str(caught.value) == "not-ooxml: a Word document, not an Excel workbook (not the type its name says)"
    )
    foreign = prefixed.replace(f'xmlns:ct="{_TYPES}"', 'xmlns:ct="urn:contoso:not-content-types"', 1)
    parts["[Content_Types].xml"] = foreign.encode("utf-8")
    src.unlink()
    assert common_mod._ooxml_family(make_zip(src, parts)) is None, "a foreign namespace is still not read"


def test_a_real_workbook_whose_content_types_have_no_namespace_still_converts_c2854(tmp_path: Path) -> None:
    """B13 round 45 C2854's guard: a real workbook with no namespace on ``<Types>`` is its own family, and a
    deck with a foreign-namespace decoy Override beside its real one converts. B13 round 46 C3047: the decoy
    is not read at all (it gave two families, so None, before), so the deck is its own family."""
    src = _without_types_namespace(_workbook(tmp_path / "Contoso book.xlsx"))
    assert common_mod._ooxml_family(src) == "spreadsheet"
    units = XlsxConverter(CFG).convert(src, name=src.name)
    assert any("Contoso" in unit.body for unit in units)
    deck = build_pptx_rich(tmp_path / "Contoso deck.pptx")
    with zipfile.ZipFile(deck) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    decoy = f'<x:Override xmlns:x="urn:contoso" PartName="/w.xml" ContentType="{_WORD_MAIN}"/></Types>'
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(b"</Types>", decoy.encode())
    deck.unlink()
    assert common_mod._ooxml_family(make_zip(deck, parts)) == "presentation"
    assert PptxConverter(CFG).convert(deck, name=deck.name)


_VISIO_MAIN = "application/vnd.ms-visio.drawing.main+xml"
_FOREIGN_DECOYS = (
    f'<x:Override xmlns:x="urn:contoso" PartName="/w.xml" ContentType="{_WORD_MAIN}"/>',
    f'<Override xmlns="" PartName="/w.xml" ContentType="{_WORD_MAIN}"/>',
)


@pytest.mark.parametrize("decoy", _FOREIGN_DECOYS, ids=["foreign-namespace", "xmlns-empty"])
def test_a_decoy_override_outside_the_types_namespace_is_not_read_c3047(tmp_path: Path, decoy: str) -> None:
    """B13 round 46 C3047 (ruling 202): ``_ooxml_family`` read every Override and Default in any namespace,
    and a Visio drawing's main type is no family, so a Word decoy in another namespace (or in ``xmlns=""``
    under a namespaced ``<Types>``) beside a real ``.vsdx``'s Override was the one family read, and the
    drawing was a ``not the type its name says`` stub. Only ``<Types>``' direct children in its own
    namespace are read: the drawing has no family and converts, and a deck with the same decoy is a deck and
    converts."""
    from agentsync.convert.diagram import DiagramConverter  # noqa: PLC0415
    from test_convert_builders import build_vsdx  # noqa: PLC0415

    types = (
        f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{_TYPES}">'
        f'<Override PartName="/visio/document.xml" ContentType="{_VISIO_MAIN}"/>{decoy}</Types>'
    ).encode()
    vsdx = build_vsdx(tmp_path / "Contoso Network.vsdx")
    with zipfile.ZipFile(vsdx) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    parts["[Content_Types].xml"] = types
    vsdx.unlink()
    make_zip(vsdx, parts)
    assert common_mod._ooxml_family(vsdx) is None
    [unit] = DiagramConverter(CFG).convert(vsdx, name=vsdx.name)
    assert "## Site plan" in unit.body
    deck = build_pptx_rich(tmp_path / "Contoso deck.pptx")
    with zipfile.ZipFile(deck) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", decoy.encode() + b"</Types>"
    )
    deck.unlink()
    assert common_mod._ooxml_family(make_zip(deck, parts)) == "presentation"
    assert PptxConverter(CFG).convert(deck, name=deck.name)


@pytest.mark.parametrize("codec", ["utf-32-le", "utf-32-be"])
def test_a_utf32_content_types_part_is_parsed_before_any_decode_c3098(tmp_path: Path, codec: str) -> None:
    """B13 round 46 C3098 (ruling 202): ``_ooxml_family`` decoded a part that opens with a UTF-16 mark before
    the parse, and UTF-32LE's mark opens with UTF-16LE's, so a UTF-32LE part holding a character past U+FFFF
    (``00 D8 01 00`` is a lone high surrogate in UTF-16) failed the decode and was None, though it parses: a
    Word package named ``.potx`` passed the family check. The part is parsed first and decoded only in the
    fallback, UTF-32 marks told before UTF-16's: in both orders it is a Word document, not a PowerPoint
    deck."""
    import codecs  # noqa: PLC0415

    src = word_package(tmp_path / "Contoso notes.potx")
    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    text = parts["[Content_Types].xml"].decode("utf-8")
    text = text.replace('<?xml version="1.0"?>', '<?xml version="1.0" encoding="UTF-32"?><!-- \U0001d800 -->')
    assert "UTF-32" in text and "\U0001d800" in text
    mark = codecs.BOM_UTF32_LE if codec == "utf-32-le" else codecs.BOM_UTF32_BE
    parts["[Content_Types].xml"] = mark + text.encode(codec)
    src.unlink()
    make_zip(src, parts)
    assert common_mod._ooxml_family(src) == "word"
    with pytest.raises(UnreadableSourceError, match=re.escape(_DECK_STUB)):
        common_mod._check_ooxml_family(src, "presentation")
    # a UTF-16 part that does not parse and does not decode (a lone surrogate) is still None, never a raise
    bad = "<Types".encode("utf-16-le") + b"\x00\xd8" + f'ContentType="{_WORD_MAIN}"'.encode("utf-16-le")
    parts["[Content_Types].xml"] = b"\xff\xfe" + bad
    src.unlink()
    assert common_mod._ooxml_family(make_zip(src, parts)) is None


def test_a_content_types_part_that_does_not_parse_gives_utf32_no_family_with_no_early_return_c3266(
    tmp_path: Path,
) -> None:
    """B13 round 47 C3266 (ruling 215): C3098's early return for a UTF-32 mark on a part that does not parse
    changed no UTF-32 answer (the UTF-16 decode of a UTF-32LE mark leaves a unit no higher than U+0010 after
    every character, and UTF-32BE has no UTF-16 mark, so the byte regex never matched), and only turned a
    UTF-16LE part opening U+0000 (``FF FE 00 00``) from a Word document into None, a ``.potx`` of that shape
    a failed conversion rather than the stub. The return is gone: UTF-32 parts that do not parse are still
    None, and that UTF-16 part is a Word document again, as every other UTF-16 part that does not parse."""
    src = word_package(tmp_path / "Contoso notes.potx")
    with zipfile.ZipFile(src) as zin:
        parts = {i.filename: zin.read(i) for i in zin.infolist()}
    text = parts["[Content_Types].xml"].decode("utf-8")
    for codec, mark in _UTF32_MARKS.items():
        parts["[Content_Types].xml"] = mark + (text + "<").encode(codec)
        src.unlink()
        assert common_mod._ooxml_family(make_zip(src, parts)) is None, codec
    parts["[Content_Types].xml"] = b"\xff\xfe" + ("\x00" + text).encode("utf-16-le")
    assert parts["[Content_Types].xml"].startswith(_UTF32_MARKS["utf-32-le"])
    src.unlink()
    assert common_mod._ooxml_family(make_zip(src, parts)) == "word"
    assert "import codecs" not in Path(common_mod.__file__).read_text(encoding="utf-8")


_UTF32_MARKS = {"utf-32-le": b"\xff\xfe\x00\x00", "utf-32-be": b"\x00\x00\xfe\xff"}


@pytest.mark.parametrize("codec", ["utf-32-le", "utf-32-be"])
def test_a_utf32_text_file_is_decoded_before_utf16s_mark_c3321(tmp_path: Path, codec: str) -> None:
    """B13 round 47 C3321 with C3268 and C3291 (ruling 215): ``_decode_text`` told only UTF-8's and UTF-16's
    marks, and UTF-32LE's ``FF FE 00 00`` opens with UTF-16LE's, so a UTF-32LE text file read as UTF-16 failed
    as ``binary content (NUL characters)`` and a UTF-32BE one as ``(NUL bytes)``. UTF-32's marks are told
    first: the text decodes, a character past U+FFFF included, through ``convert_file`` too; a bad length or
    code point is ``invalid UTF-32 text``, a ConversionError, never UnicodeDecodeError; a NUL inside is still
    binary; UTF-16 text is read as before."""
    mark, said = _UTF32_MARKS[codec], "Contoso notes\nline two \U0001d800 caf\u00e9\n"
    assert common_mod._decode_text(mark + said.encode(codec)) == said
    assert common_mod._decode_text(said.encode("utf-32")) == said, "the mark Python writes"
    src = tmp_path / "notes.txt"
    src.write_bytes(mark + said.encode(codec))
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    result = convert_file(
        src,
        name="Contoso notes.txt",
        content_sha256=digest,
        canonical_sha256=digest,
        registry=Registry.default(ConvertConfig()),
        cache=ConverterCache(tmp_path / "cache"),
    )
    assert result.status is ConversionStatus.OK, result.reason
    assert "line two \U0001d800 caf\u00e9" in result.units[0].body
    surrogate = (0xD800).to_bytes(4, "little" if codec.endswith("le") else "big")
    past = (0x110000).to_bytes(4, "little" if codec.endswith("le") else "big")
    for bad in (mark + "Hi".encode(codec) + b"\x00", mark + surrogate, mark + past):
        with pytest.raises(ConversionError, match=r"^invalid UTF-32 text: "):
            common_mod._decode_text(bad)
    with pytest.raises(ConversionError, match=re.escape("binary content (NUL characters)")):
        common_mod._decode_text(mark + "A\x00B".encode(codec))
    for utf16 in ("utf-16-le", "utf-16-be"):
        bom = b"\xff\xfe" if utf16.endswith("le") else b"\xfe\xff"
        assert common_mod._decode_text(bom + "Hi \u00e9".encode(utf16)) == "Hi \u00e9"
    with pytest.raises(ConversionError, match=re.escape("binary content (NUL bytes)")):
        common_mod._decode_text("Hi".encode(codec))  # unmarked UTF-32 stays binary, as before


def _marked_case(case: str) -> tuple[str, bytes, str]:
    """A file name, its bytes with a UTF-32LE (or, for HTML, UTF-16LE) mark, and the text its page holds."""
    import base64  # noqa: PLC0415

    if case == "eml":
        body = base64.b64encode("Contoso marked body\n".encode("utf-32")).decode("ascii")
        raw = (
            "From: a@contoso.com\r\nTo: b@contoso.com\r\nSubject: Contoso marked\r\nMIME-Version: 1.0\r\n"
            f"Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n\r\n{body}\r\n"
        )
        return "Contoso marked.eml", raw.encode("ascii"), "Contoso marked body"
    if case == "vtt":
        cues = "WEBVTT\n\n00:00.000 --> 00:01.000\n<v Ana>Contoso marked cue\n"
        return "Contoso marked.vtt", cues.encode("utf-32"), "Contoso marked cue"
    page = "<html><body><p>Contoso marked page</p></body></html>"
    return "Contoso marked.html", page.encode("utf-16"), "Contoso marked page"


@pytest.mark.parametrize("case", ["eml", "vtt", "html"])
def test_a_marked_file_misses_the_answer_an_older_build_cached_c3321(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """B13 round 47 C3321 (ruling 215, its review): before the UTF-32 branch, eml and msg took the lenient
    fallback's garbage for a marked UTF-32 part and vtt gave the ``not-vtt`` stub, each cached, and pandoc's
    HTML decode dropped every NUL byte first, so a marked UTF-16 or UTF-32 page was CJK garbage, cached too.
    Only a new key stops the cache serving those answers to the same bytes: eml's emitter is 1.1.4 (msg's
    versions carry it), vtt's 1.0.5 and pandoc's 1.0.4, and the HTML decode reads a UTF-32 or UTF-16 mark
    whole. An entry the older build stored is missed, and the page holds the text."""
    from agentsync.convert import eml as eml_mod  # noqa: PLC0415
    from agentsync.convert import pandoc as pandoc_mod  # noqa: PLC0415

    name, data, said = _marked_case(case)
    src = tmp_path / ("staged" + Path(name).suffix)
    src.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    reg, cache = Registry.default(ConvertConfig()), ConverterCache(tmp_path / "cache")

    def converted():  # type: ignore[no-untyped-def]
        return convert_file(
            src, name=name, content_sha256=digest, canonical_sha256=digest, registry=reg, cache=cache
        )

    def old_decode_text(raw: bytes) -> str:
        if raw.startswith(common_mod._UTF32_BOMS):
            raise ConversionError("binary content (NUL characters)")
        return common_mod._decode_text(raw)

    with monkeypatch.context() as older:
        if case == "html":
            older.setattr(pandoc_mod, "_EMITTER_VERSION", "1.0.3")
            older.setattr(
                pandoc_mod, "_decode_html", lambda raw: common_mod._decode_text(raw.replace(b"\0", b""))
            )
        else:
            mod = eml_mod if case == "eml" else vtt_mod
            older.setattr(mod, "_EMITTER_VERSION", "1.1.3" if case == "eml" else "1.0.4")
            older.setattr(mod, "_decode_text", old_decode_text)
        stale = converted()
        assert said not in "".join(u.body for u in stale.units), "the older build's answer holds no text"
        assert converted().from_cache, "the older build serves its own entry"
    result = converted()
    assert not result.from_cache, "the new key misses the older build's entry"
    assert result.status is ConversionStatus.OK, result.reason
    assert said in result.units[0].body
    if case == "html":  # every mark, both orders, decodes whole
        page = "<html><body><p>Contoso marked page</p></body></html>"
        for codec in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
            mark = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff", **_UTF32_MARKS}[codec]
            assert pandoc_mod._decode_html(mark + page.encode(codec)) == page, codec


def test_a_utf16_part_opening_u0000_still_takes_emls_lenient_fallback_c3268(tmp_path: Path) -> None:
    """B13 round 47 C3268 (ruling 215, makes worse True for a bare decode): a UTF-16LE part whose first
    character is U+0000 opens ``FF FE 00 00``, UTF-32LE's mark, and ``FF FE 00 00 41 00 42 00`` is no UTF-32
    text, so a bare ``decode("utf-32")`` raised UnicodeDecodeError past eml's ``except ConversionError`` and
    failed the whole message. The UTF-32 branch raises ConversionError, so the part still takes eml's lenient
    fallback and the message converts."""
    from agentsync.convert.eml import EmlConverter  # noqa: PLC0415

    with pytest.raises(ConversionError, match=r"^invalid UTF-32 text: "):
        common_mod._decode_text(b"\xff\xfe\x00\x00\x41\x00\x42\x00")
    raw = (
        b"From: a@contoso.com\r\nTo: b@contoso.com\r\nSubject: Contoso parts\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="b1"\r\n\r\n'
        b"--b1\r\nContent-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n\r\n//4AAEEAQgA=\r\n"
        b"--b1\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nContoso second part\r\n--b1--\r\n"
    )
    src = tmp_path / "parts.eml"
    src.write_bytes(raw)
    units = EmlConverter(CFG).convert(src, name="Contoso parts.eml")
    # B13 round 48 C3532 (ruling 229): the lenient fallback's NUL bytes are U+FFFD on the page.
    lenient = "\ufffd\ufffd\ufffd\ufffdA\ufffdB\ufffd"
    assert units and lenient in units[0].body, "the part read by eml's lenient fallback, never a failure"


def test_decode_texts_docstring_says_a_u0000_opening_may_read_as_utf32_c3418() -> None:
    """B13 round 48 C3418 (ruling 230): ``_decode_text``'s docstring said a UTF-16LE part opening U+0000 is
    still the ConversionError eml's lenient fallback catches, true of ``FF FE 00 00 41 00`` only: one whose
    16-bit units pair into valid UTF-32 code points, or the bare mark, reads as UTF-32 with no error.  The
    docstring limits the first claim to a rest that is no valid UTF-32 and names the second shape; each
    claim holds of the code."""
    doc = " ".join((common_mod._decode_text.__doc__ or "").split())
    assert "part opening U+0000 whose rest is no valid UTF-32 (``FF FE 00 00 41 00``) is still the" in doc
    assert (
        "One whose 16-bit units pair into valid UTF-32 code points (``FF FE 00 00 41 00 0A 00``, or the bare"
        " mark) is read as UTF-32, so it gives other text or none, a shape only a crafted file has" in doc
    )
    with pytest.raises(ConversionError, match=r"^invalid UTF-32 text: "):
        common_mod._decode_text(b"\xff\xfe\x00\x00\x41\x00")
    assert common_mod._decode_text(b"\xff\xfe\x00\x00\x41\x00\x0a\x00") == "\U000a0041"
    assert common_mod._decode_text(b"\xff\xfe\x00\x00") == ""


def test_the_docs_say_an_office_container_rejection_is_never_cached_c3048_c3101() -> None:
    """B13 round 46 C3048 with C3101 (ruling 202): since C2924 ``convert_file`` gives an Office name's
    container rejection before the cache and never stores it, but registry.py's module doc called it "cached
    and settled, never retried" and policy.py's "cached, settled". Each splits cache from settled now, keeps
    an encrypted PDF cached, and ``_check_ooxml_family`` names the one family re-read."""
    from agentsync.convert import cache as cache_mod  # noqa: PLC0415
    from agentsync.convert import registry as registry_mod  # noqa: PLC0415

    def flat(text: str | None) -> str:
        return " ".join((text or "").split())

    registry_doc = flat(registry_mod.__doc__)
    assert "stub that is cached and settled" not in registry_doc
    assert (
        "gives this guard's answer before the cache (``_container_screen``) and never stores it"
        in registry_doc
    )
    assert "(an encrypted PDF, an encrypted container under another name) is cached" not in registry_doc
    assert (
        "(an encrypted PDF or an encrypted container under a name outside ``policy.OOXML_SUFFIXES``, a "
        "``.pdf`` among them) is cached" in registry_doc
    )
    assert "whichever container answer it is (``encrypted-office``, ``encrypted-pdf``," in registry_doc
    assert "never reads or writes the cache (C1466)" in registry_doc
    policy_doc = flat(policy_mod.__doc__)
    assert "(cached, settled: no retry storm)" not in policy_doc
    # B13 round 47 C3262 with C3275, C3320 and C3329: the rule keyed once on the name and the bytes, with
    # encrypted-pdf among an Office name's answers and the cached PDF under a name outside the suffixes
    assert "an encrypted PDF is cached, while ``encrypted-office`` under an Office name" not in policy_doc
    assert (
        "under a name ending in one of :data:`OOXML_SUFFIXES` whose bytes are no ZIP at offset 0, the "
        "container answer (``encrypted-office``, ``encrypted-pdf``, ``not-ooxml`` or ``empty-output``) is "
        "given before the cache and never stored" in policy_doc
    )
    assert (
        "an encrypted PDF or an encrypted container under a name outside :data:`OOXML_SUFFIXES` (a ``.pdf``) "
        "is cached" in policy_doc
    )
    tally = flat(cycle_mod._Cycle._run_tally.__doc__)  # B13 round 47 C3265: converted_unreadable's words
    assert "cached and kept" not in tally and "until the bytes change" not in tally
    assert "An Office name's container rejection (bytes not a ZIP at offset 0:" in tally
    assert "any other UNREADABLE result is cached and not converted again under the same action key" in tally
    family_doc = flat(common_mod._check_ooxml_family.__doc__)
    assert "cached, never retried" not in family_doc
    assert "except the one re-read ``cycle._Cycle._reread_family_stubs`` gives" in family_doc
    put_doc = flat(cache_mod.ConverterCache.put.__doc__)
    assert "never calls this for an Office name's container rejection" in put_doc


@pytest.mark.parametrize(("name", "calls"), [("Contoso plan.docx", []), ("Contoso plan.pdf", ["get", "put"])])
def test_an_encrypted_pdf_is_cached_only_under_a_name_outside_the_office_suffixes_c3320(
    tmp_path: Path, name: str, calls: list[str]
) -> None:
    """B13 round 47 C3320 with C3262, C3275 and C3329 (ruling 215): the docs said an encrypted PDF is cached,
    but under an ``OOXML_SUFFIXES`` name whose bytes are no ZIP at offset 0 ``convert_file`` gives the
    container answer, ``encrypted-pdf`` among them, before the cache and never stores it. At the cache's
    boundary: the same encrypted-PDF bytes under ``.docx`` read and write nothing, under ``.pdf`` one get and
    one put, and both are the ``encrypted-pdf`` stub."""
    cache = ConverterCache(tmp_path / "cache")
    seen: list[str] = []
    real_get, real_put = cache.get, cache.put
    cache.get = lambda key: seen.append("get") or real_get(key)  # type: ignore[method-assign]
    cache.put = lambda result: seen.append("put") or real_put(result)  # type: ignore[method-assign]
    src = tmp_path / "staged"
    src.write_bytes(_ENCRYPTED_PDF_PREAMBLE + b"trailer << /Encrypt 1 0 R >>\n%%EOF\n")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    result = convert_file(
        src,
        name=name,
        content_sha256=digest,
        canonical_sha256=digest,
        registry=Registry.default(ConvertConfig()),
        cache=cache,
    )
    assert (result.status, result.reason) == (ConversionStatus.UNREADABLE, ENCRYPTED_PDF_REASON)
    assert seen == calls, seen


def _decoy_potx(path: Path) -> Path:
    """A ``.potx`` whose real Override is spelled ``presentation&#109;l`` and whose part holds a Word
    main-type literal in a comment: round 44's family rule (C2708) reads it as a deck, the build before it as
    a Word document (B13 round 45 C2925)."""
    return _types_in(build_potx(path), "utf-8", lambda t: _decoyed(t, _WORD_MAIN, "utf-8"))


def _older_family_rule(old: pytest.MonkeyPatch) -> None:
    """The build before C2925: the byte read told the decoy a Word document, and the key had no
    ``family_parse``."""
    real_family = common_mod._ooxml_family

    def older_family(src: Path) -> str | None:
        try:
            with zipfile.ZipFile(src) as zf:
                types = zf.read("[Content_Types].xml")
        except (KeyError, OSError, zipfile.BadZipFile):
            return real_family(src)
        return "word" if b"presentation&#109;l" in types else real_family(src)

    def older_hash(conv: Converter, name: str) -> str:
        opts = {**conv.options(), "input_suffix": convert_pkg._suffix(name)}
        if convert_pkg._suffix(name) in policy_mod.OOXML_SUFFIXES or convert_pkg._suffix(name) == ".msg":
            opts["empty_screen"] = 1
        return options_hash(opts)

    old.setattr(common_mod, "_ooxml_family", older_family)
    old.setattr(convert_pkg, "effective_options_hash", older_hash)


_DECK_STUB = "not-ooxml: a Word document, not a PowerPoint deck (not the type its name says)"


def test_an_older_family_answer_in_the_cache_is_not_served_under_the_new_key_c2925(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 45 C2925 (ruling 196): C2708 changed what the family check says of some packages and moved no
    key, so the older ``not the type its name says`` stub, cached UNREADABLE, was served to the same bytes
    with ``from_cache`` True. Every Office name's key carries ``family_parse`` now: the older entry stays
    under the older key, and the conversion is a page, not from the cache."""
    from agentsync.convert.canonical import canonical_hash  # noqa: PLC0415

    src = _decoy_potx(tmp_path / "Contoso template.potx")
    registry, cache = Registry.default(CFG), ConverterCache(tmp_path / "cache")

    def convert():  # type: ignore[no-untyped-def]
        return convert_file(
            src,
            name=src.name,
            content_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
            canonical_sha256=canonical_hash(src, suffix=".potx").sha256,
            registry=registry,
            cache=cache,
        )

    with monkeypatch.context() as old:
        _older_family_rule(old)
        older = convert()
        assert (older.status, older.reason) == (ConversionStatus.UNREADABLE, _DECK_STUB), older
        assert convert().from_cache, "the older build served its own stub from the cache"
    now = convert()
    assert (now.status, now.from_cache) == (ConversionStatus.OK, False), now
    assert now.action_key != older.action_key and now.options_hash != older.options_hash
    assert cache.get(older.action_key) is not None, "the older entry stays under the older key"
    for conv in registry.converters():
        for ext in conv.extensions:
            opts = dict(conv.options())
            hashed = convert_pkg.effective_options_hash(conv, f"x{ext}")
            with_flag = {**opts, "input_suffix": ext, "family_parse": 1}
            if ext in policy_mod.OOXML_SUFFIXES:
                assert hashed == options_hash({**with_flag, "empty_screen": 1}), ext
            else:
                assert "family_parse" not in opts and hashed != options_hash(with_flag), ext


def test_a_published_family_mismatch_stub_is_read_once_under_the_new_key_c2925(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 45 C2925 (ruling 196): a stub an older family rule published was never read again (the Office
    converters' ``outdated`` reads no stub by its reason), and a re-read under the old key would get the
    cached stub back. The ``family-parse-v1`` pass reads each Office row whose reason has the family-mismatch
    form once, under the new key: the decoy template becomes a page, a real Word document named ``.pptx``
    keeps its stub (read once, its key moved, no commit), and a ``.docx`` whose bytes are no ZIP
    (``NOT_OOXML_REASON``) is never read. Once every such row is read the token holds and the marks go, so
    nothing is read again."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_family_rule(old)
        _decoy_potx(src / "Contoso template.potx")
        word_package(src / "Contoso notes.pptx")
        (src / "Contoso plain.docx").write_bytes(b"Contoso plain text, no ZIP header\n")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso template.potx").state_reason == _DECK_STUB
    assert _row(config, "Contoso notes.pptx").state_reason == _DECK_STUB
    assert _row(config, "Contoso plain.docx").state_reason == NOT_OOXML_REASON
    head = git(config.docs_repo, "rev-parse", "HEAD").strip()
    tries = _tries(monkeypatch)
    report = run(config)
    assert report.exit_code == 0
    assert sorted(tries) == ["Contoso notes.pptx", "Contoso template.potx"], tries
    assert _row(config, "Contoso template.potx").state is RowState.LIVE
    assert _row(config, "Contoso notes.pptx").state_reason == _DECK_STUB, "a real mismatch keeps its stub"
    assert report.read_again == 2 and sum(s.converted for s in report.sources) == 0, report
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") == "done"
        assert (
            m.get_meta(f"{cycle_mod._FAMILY_PARSE_META}src:{item_id(config, 'Contoso notes.pptx')}") is None
        )
    changed = git(config.docs_repo, "diff", "--name-only", head, "HEAD")
    assert "contoso-template" in changed.lower() and "contoso-notes" not in changed.lower(), changed
    tries.clear()
    assert run(config).exit_code == 0
    assert tries == [], "the token holds: no family re-read again"


def test_a_family_stub_online_only_when_the_pass_runs_holds_the_token_open_c2925(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 45 C2925, the review's finding: the family pass left a dataless row out of the rows it waits
    for, so a stub that was online-only when the pass ran let the token be set, and once the file was back its
    unchanged H1 kept the old stub for good. A dataless row is never read, never marked, and holds the token
    unset; the first pass that finds it on this Mac reads it and sets the token."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_family_rule(old)
        _decoy_potx(src / "Contoso template.potx")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso template.potx").state_reason == _DECK_STUB
    real = Manifest.iter_items

    def online_only(self: Manifest, source_id: str, *, states=None):  # type: ignore[no-untyped-def]
        for row in real(self, source_id, states=states):
            yield dataclasses.replace(row, dataless=True) if row.name == "Contoso template.potx" else row

    tries = _tries(monkeypatch)
    with monkeypatch.context() as evicted:
        evicted.setattr(Manifest, "iter_items", online_only)
        assert run(config).exit_code == 0
    assert tries == [] and _row(config, "Contoso template.potx").state_reason == _DECK_STUB
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") is None, "the token waits for the row"
    assert run(config).exit_code == 0
    assert tries == ["Contoso template.potx"], tries
    assert _row(config, "Contoso template.potx").state is RowState.LIVE
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") == "done"


def test_a_family_reread_whose_fetch_fails_leaves_the_row_unmarked_and_the_token_open_c3107(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 46 C3107 with C3097 (ruling 202): ``_family_reread`` marked the row before its fetch, so a
    file evicted or unreadable between the walk and the family pass (a fetch with no download budget that
    raises BudgetExhaustedError) left the row marked, the token was set, and once the file was back its
    unchanged H1 kept the old stub for good. The mark is set only after the fetch returns: a failed fetch
    leaves the row unmarked and the token unset, and the next pass reads it once and sets the token."""
    from agentsync.arm_local import LocalArm  # noqa: PLC0415
    from agentsync.errors import BudgetExhaustedError  # noqa: PLC0415

    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_family_rule(old)
        _decoy_potx(src / "Contoso template.potx")
        assert run(config).exit_code == 0
    assert _row(config, "Contoso template.potx").state_reason == _DECK_STUB
    mark = f"{cycle_mod._FAMILY_PARSE_META}src:{item_id(config, 'Contoso template.potx')}"
    real = LocalArm.fetch
    failed: list[str] = []

    def evicted(self, item, dest_dir, budget):  # type: ignore[no-untyped-def]
        if budget.max_bytes == 0 and item.name == "Contoso template.potx" and not failed:
            failed.append(item.name)
            raise BudgetExhaustedError("Contoso template.potx is online-only since the walk")
        return real(self, item, dest_dir, budget)

    tries = _tries(monkeypatch)
    with monkeypatch.context() as gone:
        gone.setattr(LocalArm, "fetch", evicted)
        assert run(config).exit_code == 0
    assert failed == ["Contoso template.potx"], "the family pass fetched the row with no download budget"
    assert tries == [] and _row(config, "Contoso template.potx").state_reason == _DECK_STUB
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") is None, "a failed fetch set the token"
        assert m.get_meta(mark) is None, "a failed fetch marked the row read"
    assert run(config).exit_code == 0
    assert tries == ["Contoso template.potx"], tries
    assert _row(config, "Contoso template.potx").state is RowState.LIVE
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") == "done"
        assert m.get_meta(mark) is None, "the token takes the marks away"
    tries.clear()
    assert run(config).exit_code == 0
    assert tries == [], "the token holds: no family re-read again"


def test_a_family_stub_the_walk_queued_waits_one_sync_for_its_reread_c3231(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B13 round 47 C3231 (ruling 215): the family pass skips every row the walk queued this pass, so an
    older-rule stub whose file was touched (or became readable again) before the first upgraded sync is
    settled by the work queue as touched, not changed, and read by the family pass only on the next sync. The
    wait is by design and documented in ``_reread_family_stubs``: the token stays unset until the row is
    read, so nothing is lost, and the read comes on the first sync whose walk does not queue the row (here
    the next; a file touched before every sync waits as long as that goes on)."""
    config, src = make_env(tmp_path)
    with monkeypatch.context() as old:
        _older_family_rule(old)
        path = _decoy_potx(src / "Contoso template.potx")
        assert run(config).exit_code == 0
    assert _row(config, path.name).state_reason == _DECK_STUB
    then = path.stat().st_mtime - 1000
    os.utime(path, (then, then))
    tries = _tries(monkeypatch)
    assert run(config).exit_code == 0
    assert tries == [] and _row(config, path.name).state_reason == _DECK_STUB, "the queued row waits"
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") is None, "the token waits for the row"
    assert run(config).exit_code == 0
    assert tries == [path.name], tries
    assert _row(config, path.name).state is RowState.LIVE
    with Manifest(config.state_paths.db) as m:
        assert m.get_meta(cycle_mod._FAMILY_PARSE_META + "src") == "done"
    doc = " ".join((cycle_mod._Cycle._reread_family_stubs.__doc__ or "").split())
    assert "(a row the walk queued this pass, such as a file touched or readable again, waits for" in doc
    assert "waits for the first cycle whose walk does not queue it, and the token stays unset until it" in doc


def test_a_touched_office_stub_the_container_did_not_give_is_no_container_move_c2924(
    tmp_path: Path,
) -> None:
    """B13 round 45 C2924, the review's finding: ``_container_moved`` read every Office row whose reason is
    not a container's own as a page made from a ZIP, so a stub that says nothing of the container (a failed
    conversion, a label refusal, an inbox duplicate) on non-ZIP bytes was a move on every touch, converted and
    counted again. Only a container stub on a ZIP and a page on non-ZIP bytes move."""
    config, src = make_env(tmp_path)
    clean = _workbook(src / "Contoso book.xlsx")
    assert run(config).exit_code == 0
    page = _row(config, clean.name)
    junk = tmp_path / "Contoso junk.xlsx"
    junk.write_bytes(b"\x00Contoso junk")

    def moved(reason: str | None, state: RowState, path: Path) -> bool:
        row = dataclasses.replace(page, state=state, state_reason=reason)
        return cycle_mod._container_moved(row, SimpleNamespace(path=path))  # type: ignore[arg-type]

    others = ("conversion-failed: openpyxl cannot read the workbook", "refused: sensitivity label Contoso")
    assert moved(None, RowState.LIVE, junk) and moved(NOT_OOXML_REASON, RowState.QUARANTINED, clean)
    assert not moved(NOT_OOXML_REASON, RowState.QUARANTINED, junk) and not moved(None, RowState.LIVE, clean)
    for reason in others:
        for path in (junk, clean):
            assert not moved(reason, RowState.QUARANTINED, path), (reason, path.name)
    # B13 round 46 C3046: encrypted-pdf is the container's own answer for an Office name's non-ZIP bytes
    assert moved(ENCRYPTED_PDF_REASON, RowState.QUARANTINED, clean)
    # B13 round 47 C3316: a container stub on non-ZIP bytes moves when they sniff to another container answer
    assert moved(ENCRYPTED_PDF_REASON, RowState.QUARANTINED, junk)
    polyglot = tmp_path / "Contoso polyglot.xlsx"
    polyglot.write_bytes(_ENCRYPTED_PDF_PREAMBLE + clean.read_bytes())
    assert not moved(ENCRYPTED_PDF_REASON, RowState.QUARANTINED, polyglot)
    assert moved(NOT_OOXML_REASON, RowState.QUARANTINED, polyglot)
    empty = tmp_path / "Contoso empty.xlsx"
    empty.write_bytes(b"")
    assert not moved(NOT_OOXML_REASON, RowState.QUARANTINED, empty), "zero bytes are the zero-byte re-read's"
    pdf = dataclasses.replace(
        page, name="Contoso.pdf", state=RowState.QUARANTINED, state_reason=ENCRYPTED_PDF_REASON
    )
    for path in (junk, clean):
        assert not cycle_mod._container_moved(pdf, SimpleNamespace(path=path))  # type: ignore[arg-type]
    # B13 round 46 C3096: a family-mismatch stub came from a ZIP, so non-ZIP bytes move it; clean ones do not
    assert moved(_DECK_STUB, RowState.QUARANTINED, junk)
    assert not moved(_DECK_STUB, RowState.QUARANTINED, clean)
