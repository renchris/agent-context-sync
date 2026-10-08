"""Converter helpers, write-once cache, H1 canonical hashing and the registry (no real converters run)."""

from __future__ import annotations

import hashlib
import json
import logging
import stat
import unicodedata
from pathlib import Path

import pytest

from agentsync.config import ConvertConfig
from agentsync.convert import image, pdf
from agentsync.convert._common import _cap_body, _emitter, _escape_line
from agentsync.convert.base import estimate_tokens, make_unit, options_hash, rendered_sha256
from agentsync.convert.cache import KEY_SCHEMA_VERSION, ConverterCache, action_key
from agentsync.convert.canonical import OOXML_SUFFIXES, canonical_hash, differing_parts
from agentsync.convert.media import MediaEngine
from agentsync.convert.ocr import OcrEngine
from agentsync.convert.registry import Registry
from agentsync.model import ConversionResult, ConversionStatus, RenderedUnit, UnitKind
from agentsync.policy import PolicyConfig
from test_convert_builders import make_zip, ole_encrypted

H = "a" * 64

# ---------------------------------------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "tokens"), [("", 0), ("a", 1), ("abcd", 1), ("abcde", 2), ("é" * 8, 2)])
def test_estimate_tokens_is_ceil_chars_over_4(text: str, tokens: int) -> None:
    assert estimate_tokens(text) == tokens


def test_options_hash_matches_contract_formula() -> None:
    opts = {"b": 1, "a": "x", "c": True, "d": 0.5}
    blob = json.dumps(opts, sort_keys=True, separators=(",", ":"))
    assert options_hash(opts) == "sha256:" + hashlib.sha256(blob.encode()).hexdigest()
    assert options_hash({"a": "x", "b": 1, "c": True, "d": 0.5}) == options_hash(opts)  # order-free
    assert options_hash({"a": 1}) != options_hash({"a": 2})


def test_rendered_sha256_is_sha256_of_utf8() -> None:
    assert rendered_sha256("café\n") == hashlib.sha256("café\n".encode()).hexdigest()


def test_make_unit_normalises_body_and_labels() -> None:
    nfd = unicodedata.normalize("NFD", "café")
    u = make_unit(
        unit_id="whole",
        kind=UnitKind.WHOLE,
        index=0,
        of=1,
        name="",
        file_stem="",
        title="  A \n title  ",
        summary="x" * 500,
        body=f"\n\nline one\r\nline two {nfd}\r\n\n\n",
    )
    assert u.body == "line one\nline two café\n"
    assert unicodedata.is_normalized("NFC", u.body)
    assert u.rendered_sha256 == rendered_sha256(u.body)
    assert u.tokens_estimate == estimate_tokens(u.body)
    assert u.title == "A title"
    assert len(u.summary) == 240 and u.summary.endswith("…")


def test_make_unit_empty_body_is_single_newline() -> None:
    u = make_unit(
        unit_id="whole",
        kind=UnitKind.WHOLE,
        index=0,
        of=1,
        name="",
        file_stem="",
        title="",
        summary="",
        body="",
    )
    assert u.body == "\n"


# ---------------------------------------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------------------------------------


def _unit(
    index: int = 0, body: str = "hello café\n", sidecars: tuple[tuple[str, bytes], ...] = ()
) -> RenderedUnit:
    return make_unit(
        unit_id="whole" if index == 0 else f"sheet:{index}",
        kind=UnitKind.WHOLE if index == 0 else UnitKind.SHEET,
        index=index,
        of=1,
        name="" if index == 0 else f"S{index}",
        file_stem="" if index == 0 else f"{index:02d}-S{index}",
        title="Title",
        summary="Summary",
        body=body,
        sidecars=sidecars,
    )


def _result(
    status: ConversionStatus = ConversionStatus.OK,
    *,
    key: str | None = None,
    units: tuple[RenderedUnit, ...] | None = None,
    reason: str | None = None,
) -> ConversionResult:
    k = key or action_key(
        converter_id="c", converter_version="1", options_hash="sha256:x", canonical_sha256=H
    )
    return ConversionResult(
        status=status,
        converter_id="c",
        converter_version="1",
        options_hash="sha256:x",
        action_key=k,
        content_sha256="b" * 64,
        canonical_sha256=H,
        units=(_unit(),) if units is None else units,
        reason=reason,
    )


def test_action_key_composition_is_exactly_the_contract() -> None:
    key = action_key(
        converter_id="pandoc-gfm", converter_version="1+p", options_hash="sha256:o", canonical_sha256=H
    )
    raw = "|".join([str(KEY_SCHEMA_VERSION), "pandoc-gfm", "1+p", "sha256:o", "whole", H])
    assert key == hashlib.sha256(raw.encode()).hexdigest()
    assert key != action_key(
        converter_id="pandoc-gfm", converter_version="2+p", options_hash="sha256:o", canonical_sha256=H
    )
    assert key != action_key(
        converter_id="pandoc-gfm",
        converter_version="1+p",
        options_hash="sha256:o",
        canonical_sha256=H,
        unit_id="u",
    )


def test_cache_roundtrip_with_sidecars_and_permissions(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    cache = ConverterCache(root)
    units = (
        make_unit(
            unit_id="index",
            kind=UnitKind.INDEX,
            index=0,
            of=2,
            name="",
            file_stem="00-index",
            title="t",
            summary="s",
            body="idx\n",
        ),
        make_unit(
            unit_id="sheet:1",
            kind=UnitKind.SHEET,
            index=1,
            of=2,
            name="Q3 Büdget",
            file_stem="01-Q3 Büdget",
            title="t1",
            summary="s1",
            body="| a |\n",
            sidecars=(("01-q3.csv", b"a,b\n1,2\n"),),
        ),
    )
    result = _result(units=units)
    assert cache.get(result.action_key) is None
    assert cache.put(result) is True
    got = cache.get(result.action_key)
    assert got is not None and got.from_cache is True
    assert got == ConversionResult(**{**_as_dict(result), "from_cache": True})
    entry = cache.path_for(result.action_key)
    assert entry == root / result.action_key[:2] / result.action_key
    assert sorted(p.name for p in entry.iterdir()) == [
        "result.json",
        "sidecar-1-01-q3.csv",
        "unit-0.md",
        "unit-1.md",
    ]
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((entry / "unit-0.md").stat().st_mode) == 0o600
    assert not [p for p in root.iterdir() if p.name.startswith("tmp-")]


def _as_dict(r: ConversionResult) -> dict[str, object]:
    return {f: getattr(r, f) for f in ConversionResult.__dataclass_fields__}


def test_cache_is_write_once(tmp_path: Path) -> None:
    cache = ConverterCache(tmp_path / "c")
    first = _result(units=(_unit(body="first\n"),))
    second = _result(units=(_unit(body="second\n"),))
    assert cache.put(first) is True
    assert cache.put(second) is False
    got = cache.get(first.action_key)
    assert got is not None and got.units[0].body == "first\n"


def test_cache_stores_unreadable_but_not_failed_or_refused(tmp_path: Path) -> None:
    cache = ConverterCache(tmp_path / "c")
    unreadable = _result(ConversionStatus.UNREADABLE, units=(), reason="encrypted")
    assert cache.put(unreadable) is True
    got = cache.get(unreadable.action_key)
    assert (
        got is not None
        and got.status is ConversionStatus.UNREADABLE
        and got.reason == "encrypted"
        and got.units == ()
    )
    for status in (ConversionStatus.FAILED, ConversionStatus.REFUSED):
        k = action_key(converter_id=status.value, converter_version="1", options_hash="o", canonical_sha256=H)
        assert cache.put(_result(status, key=k, units=(), reason="x")) is False
        assert not cache.path_for(k).exists()


def test_cache_corrupt_entry_is_logged_moved_aside_and_rewritable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = ConverterCache(tmp_path / "c")
    result = _result()
    cache.put(result)
    (cache.path_for(result.action_key) / "unit-0.md").write_bytes(b"tampered\n")
    with caplog.at_level(logging.WARNING, logger="agentsync.convert.cache"):
        assert cache.get(result.action_key) is None
    assert "corrupt entry" in caplog.text
    assert not cache.path_for(result.action_key).exists()
    assert cache.put(result) is True
    assert cache.get(result.action_key) is not None


@pytest.mark.parametrize(
    "payload", [b"{not json", b'{"format": 99}', b"[]", b'{"format": 1, "action_key": "x"}']
)
def test_cache_bad_result_json_is_a_miss(tmp_path: Path, payload: bytes) -> None:
    cache = ConverterCache(tmp_path / "c")
    result = _result()
    cache.put(result)
    (cache.path_for(result.action_key) / "result.json").write_bytes(payload)
    assert cache.get(result.action_key) is None


def test_cache_half_written_tmp_dir_is_invisible_and_gc_removes_it(tmp_path: Path) -> None:
    cache = ConverterCache(tmp_path / "c")
    live, dead = _result(), _result(key="f" * 64)
    cache.put(live)
    cache.put(dead)
    tmp = tmp_path / "c" / "tmp-deadbeef"
    tmp.mkdir()
    (tmp / "unit-0.md").write_text("partial")
    assert cache.get("0" * 64) is None
    removed = cache.gc([live.action_key])
    assert removed == 2  # the dead key + the tmp dir
    assert cache.get(live.action_key) is not None
    assert cache.get(dead.action_key) is None
    assert not tmp.exists()
    assert not (tmp_path / "c" / "ff").exists()  # emptied shard removed
    assert cache.gc([live.action_key]) == 0


def test_cache_rejects_bad_keys_and_unsafe_sidecar_names(tmp_path: Path) -> None:
    cache = ConverterCache(tmp_path / "c")
    with pytest.raises(ValueError, match="action key"):
        cache.path_for("../etc")
    assert cache.get("not-a-key") is None
    bad = _result(units=(_unit(sidecars=(("../evil.csv", b"x"),)),))
    with pytest.raises(ValueError, match="unsafe sidecar"):
        cache.put(bad)
    assert cache.gc([]) == 0


def test_cache_gc_on_missing_root_is_zero(tmp_path: Path) -> None:
    assert ConverterCache(tmp_path / "nope").gc([]) == 0


# ---------------------------------------------------------------------------------------------------------
# canonical (H1)
# ---------------------------------------------------------------------------------------------------------


def test_non_ooxml_is_sha256_of_bytes(tmp_path: Path) -> None:
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4 hello")
    ch = canonical_hash(p, suffix=".pdf")
    assert ch.method == "bytes@1" and ch.parts == ()
    assert ch.sha256 == hashlib.sha256(b"%PDF-1.4 hello").hexdigest()


def test_fixture_resave_has_equal_h1_but_different_bytes(fixture_files: dict[str, Path]) -> None:
    a, b = fixture_files["sample.xlsx"], fixture_files["sample-resaved.xlsx"]
    assert a.read_bytes() != b.read_bytes()
    ha, hb = canonical_hash(a, suffix=".xlsx"), canonical_hash(b, suffix=".xlsx")
    assert ha.method == "ooxml-parts@1"
    assert ha.sha256 == hb.sha256
    assert all(not name.startswith("docProps/") for name, _ in ha.parts)
    assert list(ha.parts) == sorted(ha.parts)


def _docx_parts(text: str, rsid: str, rsids: str) -> dict[str, bytes]:
    doc = (
        '<w:document xmlns:w="w"><w:body>'
        f'<w:p w:rsidR="{rsid}" w:rsidRDefault="{rsid}" w:rsidP="{rsid}">'
        f'<w:r w:rsidRPr="{rsid}"><w:t>{text}</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    settings = (
        f'<w:settings xmlns:w="w"><w:zoom w:percent="100"/><w:rsids><w:rsidRoot w:val="{rsids}"/>'
        f'<w:rsid w:val="{rsids}"/></w:rsids></w:settings>'
    )
    return {
        "[Content_Types].xml": b"<Types/>",
        "word/document.xml": doc.encode(),
        "word/settings.xml": settings.encode(),
        "word/header1.xml": f'<w:hdr xmlns:w="w"><w:p w:rsidR="{rsid}"/></w:hdr>'.encode(),
        "docProps/core.xml": f"<core>{rsid}</core>".encode(),
    }


def test_word_rsid_churn_is_canonicalised_away(tmp_path: Path) -> None:
    a = make_zip(tmp_path / "a.docx", _docx_parts("Hello", "00A1B2C3", "00A1B2C3"))
    b = make_zip(
        tmp_path / "b.docx", _docx_parts("Hello", "00FFEE11", "00FFEE12"), date=(2026, 9, 2, 8, 0, 0)
    )
    c = make_zip(tmp_path / "c.docx", _docx_parts("Hello!", "00A1B2C3", "00A1B2C3"))
    ha, hb, hc = (canonical_hash(p, suffix=".docx") for p in (a, b, c))
    assert ha.sha256 == hb.sha256
    assert ha.sha256 != hc.sha256
    assert differing_parts(ha.parts, hc.parts) == ("word/document.xml",)


def _workbook_parts(doc_id: str, sheet: str, calc: str = "1", printer: bytes = b"p1") -> dict[str, bytes]:
    wb = (
        '<workbook xmlns:xr="x"><fileVersion appName="xl" lastEdited="7" rupBuild="' + calc + '"/>'
        '<mc:AlternateContent><x15ac:absPath url="/Users/' + calc + '/"/></mc:AlternateContent>'
        f'<xr:revisionPtr revIDLastSave="0" documentId="13_ncr:1_{{{doc_id}}}" xr6:coauthVersionLast="47"/>'
        '<sheets><sheet name="Q3" sheetId="1"/></sheets></workbook>'
    )
    return {
        "xl/workbook.xml": wb.encode(),
        "xl/worksheets/sheet1.xml": sheet.encode(),
        "xl/calcChain.xml": f"<calcChain>{calc}</calcChain>".encode(),
        "xl/printerSettings/printerSettings1.bin": printer,
        "docMetadata/LabelInfo.xml": calc.encode(),
    }


def test_excel_save_metadata_is_canonicalised_away(tmp_path: Path) -> None:
    a = make_zip(tmp_path / "a.xlsx", _workbook_parts("AAAA", "<sheetData>1</sheetData>"))
    b = make_zip(
        tmp_path / "b.xlsx", _workbook_parts("BBBB", "<sheetData>1</sheetData>", calc="2", printer=b"p2")
    )
    c = make_zip(tmp_path / "c.xlsx", _workbook_parts("AAAA", "<sheetData>2</sheetData>"))
    ha, hb, hc = (canonical_hash(p, suffix=".XLSX") for p in (a, b, c))
    assert ha.sha256 == hb.sha256
    assert {n for n, _ in ha.parts} == {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}
    assert differing_parts(ha.parts, hc.parts) == ("xl/worksheets/sheet1.xml",)


def test_sheet_names_in_workbook_xml_still_count(tmp_path: Path) -> None:
    a = make_zip(tmp_path / "a.xlsx", _workbook_parts("AAAA", "<s/>"))
    parts = _workbook_parts("AAAA", "<s/>")
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b'name="Q3"', b'name="Q4"')
    b = make_zip(tmp_path / "b.xlsx", parts)
    assert canonical_hash(a, suffix=".xlsx").sha256 != canonical_hash(b, suffix=".xlsx").sha256


def test_corrupt_or_encrypted_ooxml_falls_back_to_bytes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good = make_zip(tmp_path / "g.xlsx", {"xl/workbook.xml": b"<w/>" * 1000})
    raw = bytearray(good.read_bytes())
    raw[60:90] = b"\x00" * 30  # damage the deflate stream of the only member
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(bytes(raw))
    with caplog.at_level(logging.INFO, logger="agentsync.convert.canonical"):
        hb = canonical_hash(bad, suffix=".xlsx")
    assert hb.method == "bytes@1" and hb.sha256 == hashlib.sha256(bytes(raw)).hexdigest()
    assert caplog.records
    enc = ole_encrypted(tmp_path / "e.docx")
    assert canonical_hash(enc, suffix=".docx").method == "bytes@1"
    junk = tmp_path / "j.pptx"
    junk.write_bytes(b"PK\x03\x04garbage")
    assert canonical_hash(junk, suffix=".pptx").method == "bytes@1"


def test_every_ooxml_suffix_uses_parts(tmp_path: Path) -> None:
    for suffix in OOXML_SUFFIXES:
        p = make_zip(tmp_path / f"x{suffix}", {"a.xml": b"<a/>"})
        assert canonical_hash(p, suffix=suffix).method == "ooxml-parts@1"


def test_differing_parts_added_removed_changed_sorted() -> None:
    old = (("a", "1"), ("b", "2"), ("c", "3"))
    new = (("b", "2"), ("c", "9"), ("d", "4"))
    assert differing_parts(old, new) == ("a", "c", "d")
    assert differing_parts(old, old) == ()


# ---------------------------------------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------------------------------------


def test_default_registry_routes_exactly_the_contract_set() -> None:
    reg = Registry.default(ConvertConfig())
    ids = {ext: reg.for_name(f"x{ext}").converter_id for ext in reg.extensions()}  # type: ignore[union-attr]
    routes = {
        "pandoc-gfm": (".docx", ".odt", ".rtf", ".html", ".htm"),
        "xlsx-openpyxl": (".xlsx", ".xlsm"),
        "pptx-python-pptx": (".pptx",),
        "pdf-pypdfium2": (".pdf",),
        "markdown-passthrough": (".md", ".markdown"),
        "text-plain": (".txt", ".csv", ".tsv", ".log", ".json", ".xml", ".yaml", ".yml"),
        "vtt-turns": (".vtt",),
        "eml-stdlib": (".eml",),
        "teams-month": (".teams.json",),
    }
    assert ids == {ext: cid for cid, exts in routes.items() for ext in exts}
    assert reg.extensions() == tuple(sorted(ids))
    assert [c.converter_id for c in reg.converters()] == sorted(c.converter_id for c in reg.converters())
    assert len(reg.converters()) == 9


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif")
LABEL_RULES: dict[str, PolicyConfig] = {
    "an excluded label id": PolicyConfig(exclude_label_ids=("00000000-0000-4000-8000-00000000c0de",)),
    "an excluded label name": PolicyConfig(exclude_label_names=("Secret",)),
    "unlabelled files refused": PolicyConfig(refuse_unlabelled=True),
}


def _engine() -> OcrEngine:
    """An engine nothing here runs: routing never looks at the helper."""
    return OcrEngine(Path("/nowhere/fake-ocr"), name="paper-vision", revision=2, helper_version="0.3.0")


def _identity(reg: Registry) -> dict[str, tuple[str, dict[str, object], tuple[str, ...]]]:
    return {c.converter_id: (c.version(), dict(c.options()), c.extensions) for c in reg.converters()}


def test_the_default_registry_reads_images_only_when_it_is_handed_an_engine() -> None:
    """Without an engine the registry is the one from before OCR existed: an image has no converter, and no
    converter's version or options say anything about OCR. With one, the ten raster suffixes go to
    ``image-ocr`` and every other name goes where it went: to a converter of the same id."""
    plain = Registry.default(ConvertConfig())
    assert _identity(Registry.default(ConvertConfig(), ocr=None)) == _identity(plain)
    assert all(plain.for_name(f"Contoso diagram{ext}") is None for ext in IMAGE_SUFFIXES)
    assert not any("ocr" in f"{version}{options}" for version, options, _exts in _identity(plain).values())
    reg = Registry.default(ConvertConfig(), ocr=_engine())
    routes = {ext: reg.for_name(f"x{ext}").converter_id for ext in reg.extensions()}  # type: ignore[union-attr]
    assert {ext for ext, cid in routes.items() if cid == "image-ocr"} == set(IMAGE_SUFFIXES)
    assert reg.for_name("Contoso diagram.PNG").converter_id == "image-ocr"  # type: ignore[union-attr]
    # Eleven: the nine, ``pandoc-gfm`` a second time for the suffixes it reads no picture in, and the image
    # converter.
    assert len(reg.converters()) == 11 and reg.extensions() == tuple(sorted(routes))
    for ext in plain.extensions():
        assert routes[ext] == plain.for_name(f"x{ext}").converter_id, ext  # type: ignore[union-attr]
    assert reg.for_name("clip.mp4") is None and reg.for_name("drawing.svg") is None


def test_a_registry_with_an_engine_keeps_the_one_without() -> None:
    """Plan D10: a file the engine failed on is converted by the converter a Mac without an engine has, so
    under that Mac's version, options and action key."""
    plain = Registry.default(ConvertConfig())
    assert plain.without_ocr is None and Registry([]).without_ocr is None
    reg = Registry.default(ConvertConfig(), ocr=_engine())
    twin = reg.without_ocr
    assert twin is not None and twin.without_ocr is None
    assert _identity(twin) == _identity(plain) and twin.extensions() == plain.extensions()
    # The suffixes whose files the engine reads something in.  Every other suffix goes to a converter with
    # the version and the options it has without an engine, so nothing about its files changes.
    reading = {".docx", ".odt", ".pptx", ".pdf"}
    for ext in plain.extensions():
        was, now = plain.for_name(f"x{ext}"), reg.for_name(f"x{ext}")
        assert was is not None and now is not None and now.converter_id == was.converter_id, ext
        if ext in reading:
            assert now.version() == f"{was.version()}+ocr-paper-vision-r2-h0.3.0-l1", ext
            options = dict(now.options())
            assert {k: v for k, v in options.items() if not k.startswith("ocr")} == was.options(), ext
            assert options["ocr_languages"] == "en-US" and options["ocr_max_pages"] == 40, ext
        else:
            assert (now.version(), now.options()) == (was.version(), was.options()), ext
    assert twin.for_name("scan.png") is None, "an image has no converter there, so a failed read stays one"


def test_with_an_engine_pandoc_is_two_converters_with_the_suffixes_of_one() -> None:
    """The one that reads pictures claims the two formats that hold them; ``.rtf`` and ``.html`` keep a
    converter without an engine, and so the key of a Mac without one."""
    plain = Registry.default(ConvertConfig())
    reg = Registry.default(ConvertConfig(), ocr=_engine())
    (one,) = [c for c in plain.converters() if c.converter_id == "pandoc-gfm"]
    reading, rest = (c for c in reg.converters() if c.converter_id == "pandoc-gfm")
    assert (reading.extensions, rest.extensions) == ((".docx", ".odt"), (".rtf", ".html", ".htm"))
    assert reading.extensions + rest.extensions == one.extensions
    assert (rest.version(), rest.options()) == (one.version(), one.options())
    assert reading.version() == f"{one.version()}+ocr-paper-vision-r2-h0.3.0-l1"
    assert {name: reg.for_name(name) for name in ("a.docx", "a.ODT", "a.rtf", "a.html", "a.htm")} == {
        "a.docx": reading,
        "a.ODT": reading,
        "a.rtf": rest,
        "a.html": rest,
        "a.htm": rest,
    }


@pytest.mark.parametrize("rule", LABEL_RULES.values(), ids=LABEL_RULES.keys())
def test_the_registry_without_an_engine_enforces_the_same_policy(rule: PolicyConfig) -> None:
    reg = Registry.default(ConvertConfig(), policy=rule, ocr=_engine())
    twin = reg.without_ocr
    assert twin is not None and twin.policy == reg.policy == rule
    assert _identity(twin) == _identity(Registry.default(ConvertConfig(), policy=rule))
    assert _identity(twin)["pdf-pypdfium2"][1]["label_policy"] == rule.fingerprint()


@pytest.mark.parametrize("rule", LABEL_RULES.values(), ids=LABEL_RULES.keys())
def test_a_label_rule_keeps_the_image_converter_out_of_the_registry(rule: PolicyConfig) -> None:
    """An image can carry a sensitivity label the screen cannot read, so any label rule fails closed."""
    assert rule.labels_active
    reg = Registry.default(ConvertConfig(), policy=rule, ocr=_engine())
    assert all(reg.for_name(f"scan{ext}") is None for ext in IMAGE_SUFFIXES)
    assert reg.extensions() == Registry.default(ConvertConfig()).extensions()
    assert len(reg.converters()) == 10, "the nine, and pandoc-gfm a second time: no image converter"
    open_policy = Registry.default(ConvertConfig(), policy=PolicyConfig(), ocr=_engine())
    assert open_policy.for_name("scan.tiff").converter_id == "image-ocr"  # type: ignore[union-attr]


def test_with_a_media_engine_every_other_converter_keeps_its_version_and_options() -> None:
    """``media=`` adds the recording converter and changes nothing else; the registry without an engine,
    which a recording the engines failed on is converted through, holds no recording converter."""
    media = MediaEngine(Path("/nowhere/fake-media"), name="paper-media", helper_version="0.1.0")
    reg = Registry.default(ConvertConfig(), ocr=_engine())
    with_media = Registry.default(ConvertConfig(), ocr=_engine(), media=media)
    ids = _identity(with_media)
    assert ids.pop("recording-av")[2] == (".m4v", ".mov", ".mp4")
    assert ids == _identity(reg)
    twin, plain = with_media.without_ocr, reg.without_ocr
    assert twin is not None and plain is not None and _identity(twin) == _identity(plain)
    assert (
        twin.for_name("clip.mp4") is None
        and Registry.default(ConvertConfig(), media=media).for_name("a.mov") is None
    )


def _outdated(reg: Registry, name: str, produced: str, reason: str | None = None) -> bool:
    """What the converter ``reg`` routes ``name`` to says about a page (or the stub ``reason``) it made
    under version ``produced``, asked behind the guard as the cycle asks."""
    conv = reg.for_name(name)
    assert conv is not None
    rule = getattr(conv.inner, "outdated", None)  # type: ignore[attr-defined]
    return bool(rule(produced, reason)) if rule is not None else False


def test_a_converter_calls_a_page_from_before_ocr_outdated_only_when_it_has_an_engine() -> None:
    """The pages a re-read is for: written by a converter that reads with an engine now, under a version
    without one.  The version a converter runs under is never outdated, with an engine or without, and a
    Mac without an engine calls nothing outdated for OCR."""
    plain, reg = Registry.default(ConvertConfig()), Registry.default(ConvertConfig(), ocr=_engine())
    for name in ("a.pdf", "a.pptx", "a.docx", "a.odt"):
        was, now = plain.for_name(name).version(), reg.for_name(name).version()  # type: ignore[union-attr]
        assert now.startswith(was + image._IDENTITY_MARK), "the mark is how a version with an engine ends"
        assert _outdated(reg, name, was), name
        assert not _outdated(reg, name, now), name
        assert not _outdated(plain, name, was), name
        assert not _outdated(plain, name, now), "an engine that went away asks for nothing"
        assert not _outdated(reg, name, "unavailable") and not _outdated(reg, name, ""), name
    # Suffixes whose converter reads nothing with an engine, and converters with no rule at all.
    for name in ("a.rtf", "a.html", "a.htm", "a.xlsx", "a.md", "a.txt", "a.eml", "a.png"):
        produced = plain.for_name(name).version() if plain.for_name(name) else "2.0.0"  # type: ignore[union-attr]
        assert not _outdated(reg, name, produced), name


_FIELD_IDENTITY = "+ocr-apple-vision-r3+helper-1.4.0+macos-15.6.1"  # how the field build's versions ended


@pytest.mark.parametrize("ending", ["+ocr-off", _FIELD_IDENTITY])
def test_a_page_the_field_build_of_ocr_wrote_is_outdated_once_with_an_engine_or_without(ending: str) -> None:
    """One Mac ran a build of OCR that was never on main.  Its versions end in ``+ocr-off`` (no engine) or
    in an identity with the helper and the macOS build in it, and its pages can hold a helper's failure
    text or picture text that was not escaped.  ``+ocr-off`` holds ``+ocr-``, so it passed for a page OCR
    had read.  Each such page is read again, once: no version this build writes is one of them."""
    plain, reg = Registry.default(ConvertConfig()), Registry.default(ConvertConfig(), ocr=_engine())
    for name in ("a.pdf", "a.pptx", "a.docx", "a.odt", "a.rtf", "a.html", "a.htm"):
        base = plain.for_name(name).version()  # type: ignore[union-attr]
        for registry in (reg, plain):
            assert _outdated(registry, name, base + ending), name
            assert not _outdated(registry, name, registry.for_name(name).version()), name  # type: ignore[union-attr]
            assert not _outdated(registry, name, "unavailable" + ending), "no emitter to read"
    assert _outdated(reg, "scan.pdf", plain.for_name("a.pdf").version() + ending, pdf._NO_TEXT)  # type: ignore[union-attr]
    assert not _outdated(reg, "a.docx", plain.for_name("a.docx").version() + ending, "encrypted")  # type: ignore[union-attr]
    assert not _outdated(reg, "a.xlsx", plain.for_name("a.xlsx").version() + ending), "no rule: it never read"
    assert not any(mark in reg.for_name("a.pdf").version() for mark in image._FIELD_MARKS)  # type: ignore[union-attr]


def test_an_image_page_or_stub_from_the_field_emitter_is_outdated_and_one_from_this_emitter_is_not() -> None:
    """Emitter 1.0.0 of the image converter put the file's name into the page and made a page of every
    image with no text in it.  Pages and stubs alike are read again once; what this emitter wrote is not."""
    reg = Registry.default(ConvertConfig(), ocr=_engine())
    conv = reg.for_name("a.png").inner  # type: ignore[union-attr]
    assert (image._REREAD_BELOW, conv.outdated_key) == ("2.0.0", "2.0.0<2.0.0")
    for produced in ("1.0.0" + _FIELD_IDENTITY, "1.0.0+ocr-apple-vision-r3-h2.0.0-l1", "1.9.9"):
        assert _outdated(reg, "a.png", produced) and _outdated(reg, "a.heic", produced, "any stub"), produced
    for produced in (conv.version(), "2.0.0", "2.0.1+x", "3.0.0", "1.0.0rc1", "unavailable", ""):
        assert not _outdated(reg, "a.png", produced) and not _outdated(reg, "a.png", produced, "a stub")


def test_a_stub_is_outdated_only_when_it_is_the_no_text_stub_of_a_pdf() -> None:
    """A scanned PDF is the file OCR exists for, and its stub is what a Mac without an engine gave it.  No
    other stub is worth a re-read: an encrypted file stays encrypted, a refusal stays a refusal."""
    plain, reg = Registry.default(ConvertConfig()), Registry.default(ConvertConfig(), ocr=_engine())
    was = plain.for_name("a.pdf").version()  # type: ignore[union-attr]
    assert _outdated(reg, "scan.pdf", was, pdf._NO_TEXT)
    assert not _outdated(plain, "scan.pdf", was, pdf._NO_TEXT), (
        "no engine, and the emitter is the running one"
    )
    assert not _outdated(reg, "scan.pdf", reg.for_name("a.pdf").version(), pdf._NO_TEXT)  # type: ignore[union-attr]
    for reason in ("encrypted-pdf", "empty PDF: no pages", "refused: excluded label", ""):
        assert not _outdated(reg, "scan.pdf", was, reason), reason
    for name in ("a.pptx", "a.docx"):
        produced = plain.for_name(name).version()  # type: ignore[union-attr]
        assert not _outdated(reg, name, produced, "empty output"), name


def test_a_pdf_page_from_before_comments_is_outdated_and_nothing_at_or_above_the_running_emitter_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comments floor, with its end: a re-read writes under the running emitter, so what it wrote is
    never outdated.  That holds for a floor set above the running emitter and for a version nobody can read,
    the two ways the same file could otherwise be read again every cycle."""
    plain = Registry.default(ConvertConfig())
    assert pdf._REREAD_BELOW == "2.1.0" and pdf.PdfConverter(ConvertConfig()).outdated_key == "2.1.0<2.1.0"
    assert _emitter(pdf._REREAD_BELOW) <= _emitter(pdf._EMITTER_VERSION)  # type: ignore[operator]
    assert _outdated(plain, "a.pdf", "2.0.0+pypdfium2-4.30.0+pdfium-6462+pdfminer.six-20231228")
    assert _outdated(plain, "a.pdf", "1.9.9") and _outdated(plain, "scan.pdf", "2.0.0+x", pdf._NO_TEXT)
    for produced in ("2.1.0+x", "2.1.1+x", "3.0.0", "2.0.0rc1+x", "2.0+x", "v2.0.0", "unavailable", ""):
        assert not _outdated(plain, "a.pdf", produced), produced
    assert not _outdated(plain, "a.pptx", "0.9.0+python-pptx-1.0.2"), "only the PDF converter has a floor"
    monkeypatch.setattr(pdf, "_REREAD_BELOW", "9.0.0")  # a floor nobody's emitter reaches
    assert _outdated(plain, "a.pdf", "2.0.0+x") and not _outdated(
        plain, "a.pdf", plain.for_name("a.pdf").version()
    )  # type: ignore[union-attr]
    monkeypatch.setattr(pdf, "_EMITTER_VERSION", "2.0.0")  # the floor landed before the emitter it names
    assert not _outdated(plain, "a.pdf", "2.0.0+x") and _outdated(plain, "a.pdf", "1.0.0+x")


@pytest.mark.parametrize(
    ("version", "emitter"),
    [
        ("2.1.0+pypdfium2-4.30.0+pdfium-6462", (2, 1, 0)),
        ("1.0.0", (1, 0, 0)),
        ("10.20.30+x", (10, 20, 30)),
        ("2.1.0rc1+x", None),
        ("2.1+x", None),
        ("2.1.0.4+x", None),
        ("v2.1.0", None),
        ("\u0662.\u0661.\u0660+x", None),  # digits, and not the plain ones
        ("unavailable", None),
        ("0", None),
        ("", None),
    ],
)
def test_the_emitter_of_a_version_is_three_plain_numbers_or_nothing(
    version: str, emitter: tuple[int, int, int] | None
) -> None:
    assert _emitter(version) == emitter


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Report.DOCX", "pandoc-gfm"),
        ("2026-09.teams.json", "teams-month"),
        ("2026-09.TEAMS.JSON", "teams-month"),
        ("data.json", "text-plain"),
        ("archive.tar.md", "markdown-passthrough"),
        ("deck.pptm", None),
        ("mail.msg", None),
        ("Makefile", None),
        (".md", None),
        ("noext.", None),
    ],
)
def test_for_name(name: str, expected: str | None) -> None:
    conv = Registry.default(ConvertConfig()).for_name(name)
    assert (conv.converter_id if conv else None) == expected


class _Fake:
    def __init__(self, cid: str, exts: tuple[str, ...]) -> None:
        self.converter_id = cid
        self.extensions = exts

    def version(self) -> str:
        return "1"

    def options(self) -> dict[str, str]:
        return {}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        return ()


def test_registry_rejects_duplicate_and_malformed_extensions() -> None:
    with pytest.raises(ValueError, match="claimed by both"):
        Registry([_Fake("a", (".x",)), _Fake("b", (".x",))])
    with pytest.raises(ValueError, match="lower-case"):
        Registry([_Fake("a", (".X",))])
    with pytest.raises(ValueError, match="lower-case"):
        Registry([_Fake("a", ("x",))])
    assert Registry([]).for_name("a.x") is None


# ---------------------------------------------------------------------------------------------------------
# private helpers with behaviour worth pinning
# ---------------------------------------------------------------------------------------------------------


def test_cap_body_cuts_at_a_line_and_closes_an_open_fence() -> None:
    body = "# T\n\n```python\n" + "".join(f"x = {i}\n" for i in range(500)) + "```\n\nafter\n"
    capped, sidecars = _cap_body(body, 1000, sidecar_name="full-text.txt")
    assert len(capped.encode()) <= 1000
    assert capped.count("```") == 2 and capped.rstrip().endswith("`full-text.txt`]")
    assert sidecars == (("full-text.txt", body.encode()),)
    assert _cap_body("short\n", 1000, sidecar_name="s") == ("short\n", ())


def test_escape_line_neutralises_block_syntax_only() -> None:
    assert _escape_line("# heading") == "\\# heading"
    assert _escape_line("---") == "\\---"
    assert _escape_line("===") == "\\==="
    assert _escape_line("see <!-- page: 3 -->") == "see &lt;!-- page: 3 -->"
    for keep in ("- bullet", "> quote", "1. item", "plain #tag"):
        assert _escape_line(keep) == keep
