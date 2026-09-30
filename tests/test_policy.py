"""policy + the registry guard: labels (MS-OFFCRYPTO 2.6.3), encryption, the untrusted-content boundary."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentsync import policy
from agentsync.config import ConvertConfig
from agentsync.convert import ConverterCache, Registry, convert_file
from agentsync.convert.canonical import canonical_hash
from agentsync.errors import ConfigError
from agentsync.model import ConversionStatus, RenderedUnit, UnitKind
from agentsync.policy import ContainerKind, PolicyConfig, ScreenStatus

LABEL = "2096f6a2-d2f7-48be-b329-b73aaa526e5d"  # "Highly Confidential" in the fixtures
OTHER_LABEL = "cb46c030-1825-4e81-a295-151c039dbf02"
SITE = "72f988bf-86f1-41af-91ab-2d7cd011db47"
OTHER_SITE = "11111111-2222-3333-4444-555555555555"
CFG = ConvertConfig()


# ---------------------------------------------------------------------------------------------------------
# fixtures: labelled OOXML, encrypted containers, labelled PDF/eml
# ---------------------------------------------------------------------------------------------------------


def labelinfo_xml(*labels: tuple[str, str, str, str]) -> bytes:
    """LabelInfo part (MS-OFFCRYPTO 2.6.4): (id, siteId, enabled, removed) per label, ids in braces."""
    body = "".join(
        f'<clbl:label id="{{{lid.upper()}}}" enabled="{en}" method="Privileged" siteId="{{{site}}}" '
        f'removed="{rm}" contentBits="0"/>'
        for lid, site, en, rm in labels
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<clbl:labelList xmlns:clbl="{policy.LABELINFO_NS}">{body}</clbl:labelList>'
    ).encode()


def custom_xml(*labels: tuple[str, str | None, str, str]) -> bytes:
    """docProps/custom.xml with MSIP_Label_<guid>_{Enabled,SiteId,Name}: (id, site, name, enabled)."""
    props = []
    pid = 2
    for lid, site, name, enabled in labels:
        attrs = [("Enabled", enabled), ("Name", name), ("Method", "Privileged")]
        if site is not None:
            attrs.append(("SiteId", site))
        for attr, value in attrs:
            props.append(
                f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" '
                f'name="MSIP_Label_{lid}_{attr}"><vt:lpwstr>{value}</vt:lpwstr></property>'
            )
            pid += 1
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        + "".join(props)
        + "</Properties>"
    ).encode()


def labelled_docx(
    base: Path,
    dest: Path,
    *,
    labelinfo: bytes | None = None,
    custom: bytes | None = None,
    labelinfo_part: str = "docMetadata/LabelInfo.xml",
    with_rel: bool = True,
) -> Path:
    """Copy ``base`` adding a LabelInfo part (+ its package relationship) and/or docProps/custom.xml."""
    with zipfile.ZipFile(base) as src:
        parts = {i.filename: src.read(i.filename) for i in src.infolist()}
    rels = parts["_rels/.rels"].decode()
    extra_rels = ""
    if labelinfo is not None:
        parts[labelinfo_part] = labelinfo
        if with_rel:
            extra_rels += (
                f'<Relationship Id="rIdLbl" Type="{policy.LABELINFO_REL_TYPE}" Target="/{labelinfo_part}"/>'
            )
    if custom is not None:
        if "docProps/custom.xml" not in parts:
            extra_rels += (
                f'<Relationship Id="rIdCust" Type="{policy.CUSTOM_PROPS_REL_TYPE}" '
                'Target="docProps/custom.xml"/>'
            )
        parts["docProps/custom.xml"] = custom
    parts["_rels/.rels"] = rels.replace("</Relationships>", extra_rels + "</Relationships>").encode()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
        for name in sorted(parts):
            out.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), parts[name])
    return dest


def cfb_file(dest: Path, *, encrypted: bool) -> Path:
    """A compound-file blob: the MS-CFB magic, and (encrypted) a directory entry named EncryptedPackage."""
    data = bytearray(policy.CFB_MAGIC + b"\x00" * 504)
    data += "Root Entry".encode("utf-16-le").ljust(64, b"\x00")
    if encrypted:
        data += "EncryptedPackage".encode("utf-16-le").ljust(64, b"\x00")
        data += "\x06DataSpaces".encode("utf-16-le").ljust(64, b"\x00")
    else:
        data += "WordDocument".encode("utf-16-le").ljust(64, b"\x00")
    data += b"\x00" * 1024
    dest.write_bytes(bytes(data))
    return dest


def build_pdf(dest: Path, *, info: dict[str, str] | None = None, encrypt: bool = False) -> Path:
    """A minimal one-page PDF with a correct xref; optional Info dict entries and an /Encrypt reference."""
    stream = b"BT /F1 12 Tf 72 720 Td (Quarterly plan) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    if info is not None:
        objs.append(("<< " + " ".join(f"/{k} ({v})" for k, v in info.items()) + " >>").encode())
    if encrypt:
        objs.append(b"<< /Filter /Standard /V 4 /R 4 /Length 128 /P -3904 /O <00> /U <00> >>")
    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer = f"/Size {len(objs) + 1} /Root 1 0 R"
    if info is not None:
        trailer += " /Info 6 0 R"
    if encrypt:
        trailer += f" /Encrypt {len(objs)} 0 R /ID [<01><01>]"
    out += f"trailer\n<< {trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    dest.write_bytes(bytes(out))
    return dest


def msip_info(label: str = LABEL, site: str = SITE, name: str = "Highly Confidential") -> dict[str, str]:
    return {
        f"MSIP_Label_{label}_Enabled": "true",
        f"MSIP_Label_{label}_SiteId": site,
        f"MSIP_Label_{label}_Name": name,
    }


def run_convert(src: Path, name: str, registry: Registry, cache_root: Path) -> Any:
    return convert_file(
        src,
        name=name,
        content_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
        canonical_sha256=canonical_hash(src, suffix=Path(name).suffix).sha256,
        registry=registry,
        cache=ConverterCache(cache_root),
    )


# ---------------------------------------------------------------------------------------------------------
# [policy] configuration
# ---------------------------------------------------------------------------------------------------------


def test_parse_policy_table_normalises_and_validates() -> None:
    p = policy.parse_policy_table(
        {
            "exclude_label_ids": [f"{{{LABEL.upper()}}}", LABEL],
            "exclude_label_names": ["Highly  Confidential", "Secret"],
            "refuse_unlabelled": True,
        },
        where="t",
    )
    assert p.exclude_label_ids == (LABEL,)
    assert p.exclude_label_names == ("Highly Confidential", "Secret")
    assert p.refuse_unlabelled and p.labels_active
    assert p.fingerprint().startswith("sha256:") and p.fingerprint() != PolicyConfig().fingerprint()
    assert not PolicyConfig().labels_active
    for bad, match in (
        ({"exclude_label_ids": ["not-a-guid"]}, "not a label GUID"),
        ({"exclude_label_ids": "x"}, "list of strings"),
        ({"refuse_unlabelled": "yes"}, "true or false"),
        ({"exclude_label_names": [" "]}, "empty name"),
        ({"labels": []}, "unknown key"),
    ):
        with pytest.raises(ConfigError, match=match):
            policy.parse_policy_table(bad, where="t")


def test_load_policy_reads_sources_toml_and_merges_policy_toml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sources.toml"
    stub: Any = SimpleNamespace(config_path=cfg_path)
    assert policy.load_policy(stub) == PolicyConfig()  # nothing configured: encryption detection only
    cfg_path.write_text(f'[policy]\nexclude_label_ids = ["{LABEL}"]\n', encoding="utf-8")
    assert policy.load_policy(stub).exclude_label_ids == (LABEL,)
    (tmp_path / "policy.toml").write_text(
        '[policy]\nexclude_label_names = ["Secret"]\nrefuse_unlabelled = true\n', encoding="utf-8"
    )
    merged = policy.load_policy(stub)
    assert merged == PolicyConfig((LABEL,), ("Secret",), True)  # union: fail-safe
    carried: Any = SimpleNamespace(config_path=cfg_path, policy=PolicyConfig((OTHER_LABEL,)))
    assert policy.load_policy(carried).exclude_label_ids == (OTHER_LABEL,)  # the config layer already read it
    (tmp_path / "policy.toml").write_text("[policy\n", encoding="utf-8")
    with pytest.raises(ConfigError):  # a broken policy never silently means "allow"
        policy.load_policy(stub)
    (tmp_path / "policy.toml").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"no \[policy\] table"):
        policy.load_policy(stub)


# ---------------------------------------------------------------------------------------------------------
# labels: C15 §9 #23 (LabelInfo first, custom.xml for siteIds it lacks; fixtures cover both, either, none)
# ---------------------------------------------------------------------------------------------------------


def test_ooxml_labels_none(fixture_files: dict[str, Path]) -> None:
    r = policy.read_ooxml_labels(fixture_files["sample.docx"])
    assert r.capable and r.labels == () and r.error is None


def test_ooxml_labels_labelinfo_only_found_by_relationship(
    fixture_files: dict[str, Path], tmp_path: Path
) -> None:
    doc = labelled_docx(
        fixture_files["sample.docx"],
        tmp_path / "a.docx",
        labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")),
        labelinfo_part="customXml/odd/Labels.xml",  # not the conventional path: only the rel finds it
    )
    [lab] = policy.read_ooxml_labels(doc).labels
    assert (lab.label_id, lab.site_id, lab.origin, lab.name) == (LABEL, SITE, "LabelInfo", None)


def test_ooxml_labels_labelinfo_default_path_without_relationship(
    fixture_files: dict[str, Path], tmp_path: Path
) -> None:
    doc = labelled_docx(
        fixture_files["sample.docx"],
        tmp_path / "a.docx",
        labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")),
        with_rel=False,
    )
    assert [lab.label_id for lab in policy.read_ooxml_labels(doc).labels] == [LABEL]


def test_ooxml_labels_custom_xml_only(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    doc = labelled_docx(
        fixture_files["sample.docx"],
        tmp_path / "a.docx",
        custom=custom_xml((LABEL, SITE, "Highly Confidential", "true"), (OTHER_LABEL, SITE, "Old", "false")),
    )
    [lab] = policy.read_ooxml_labels(doc).labels  # Enabled=false is not a label
    assert (lab.label_id, lab.name, lab.origin) == (LABEL, "Highly Confidential", "custom.xml")


def test_ooxml_labels_both_same_site_labelinfo_wins(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    """MS-OFFCRYPTO 2.6.3: custom.xml is read only for siteIds LabelInfo has no label element for."""
    doc = labelled_docx(
        fixture_files["sample.docx"],
        tmp_path / "a.docx",
        labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")),
        custom=custom_xml(
            (OTHER_LABEL, SITE, "Stale custom copy", "true"), (LABEL, SITE, "Highly Confidential", "true")
        ),
    )
    [lab] = policy.read_ooxml_labels(doc).labels
    assert (lab.label_id, lab.origin, lab.name) == (LABEL, "LabelInfo", "Highly Confidential")


def test_ooxml_labels_both_other_site_adds_custom(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    doc = labelled_docx(
        fixture_files["sample.docx"],
        tmp_path / "a.docx",
        labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")),
        custom=custom_xml((OTHER_LABEL, OTHER_SITE, "Partner", "true")),
    )
    labels = policy.read_ooxml_labels(doc).labels
    assert {(lab.label_id, lab.origin) for lab in labels} == {
        (LABEL, "LabelInfo"),
        (OTHER_LABEL, "custom.xml"),
    }


def test_ooxml_labels_removed_in_labelinfo_suppresses_custom(
    fixture_files: dict[str, Path], tmp_path: Path
) -> None:
    doc = labelled_docx(
        fixture_files["sample.docx"],
        tmp_path / "a.docx",
        labelinfo=labelinfo_xml((LABEL, SITE, "1", "1")),  # removed="1": the label was taken off
        custom=custom_xml((LABEL, SITE, "Highly Confidential", "true")),  # stale co-authoring-era copy
    )
    assert policy.read_ooxml_labels(doc).labels == ()


def test_ooxml_labels_damaged_part_is_an_error_not_a_crash(
    fixture_files: dict[str, Path], tmp_path: Path
) -> None:
    doc = labelled_docx(fixture_files["sample.docx"], tmp_path / "a.docx", labelinfo=b"<not xml")
    r = policy.read_ooxml_labels(doc)
    assert r.capable and r.labels == () and r.error is not None and "ParseError" in r.error


def test_pdf_and_eml_labels(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "l.pdf", info=msip_info())
    [lab] = policy.read_pdf_labels(pdf).labels
    assert (lab.label_id, lab.site_id, lab.name, lab.origin) == (
        LABEL,
        SITE,
        "Highly Confidential",
        "pdf-info",
    )
    assert policy.read_pdf_labels(build_pdf(tmp_path / "p.pdf")).labels == ()
    eml = tmp_path / "m.eml"
    eml.write_bytes(
        b"From: a@example.com\r\nSubject: s\r\n"
        b"msip_labels: MSIP_Label_"
        + LABEL.encode()
        + b"_Enabled=True;\r\n MSIP_Label_"
        + LABEL.encode()
        + b"_SiteId="
        + SITE.encode()
        + b"; MSIP_Label_"
        + LABEL.encode()
        + b"_Name=Highly Confidential;\r\n"
        b"\r\nbody\r\n"
    )
    [lab] = policy.read_eml_labels(eml).labels
    assert (lab.label_id, lab.name, lab.origin) == (LABEL, "Highly Confidential", "msip_labels")
    assert policy.read_labels(tmp_path / "m.eml", name="m.eml").labels == (lab,)
    txt = tmp_path / "t.txt"
    txt.write_text("x\n")
    assert policy.read_labels(txt, name="t.txt").capable is False


def test_label_fingerprint_moves_with_the_label_only(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    base = fixture_files["sample.docx"]
    a = labelled_docx(base, tmp_path / "a.docx", labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")))
    b = labelled_docx(base, tmp_path / "b.docx", labelinfo=labelinfo_xml((OTHER_LABEL, SITE, "1", "0")))
    c = labelled_docx(base, tmp_path / "c.docx", labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")))
    fp = policy.label_fingerprint
    assert fp(a, name="a.docx") == fp(c, name="c.docx") != fp(b, name="b.docx")
    # H1 ignores LabelInfo (canonical VOLATILE_PARTS): a relabel is invisible to H1 without this fingerprint
    assert canonical_hash(a, suffix=".docx").sha256 == canonical_hash(b, suffix=".docx").sha256


# ---------------------------------------------------------------------------------------------------------
# decisions: C15 §9 #27 (exclusion list of GUIDs; unknown/unlabelled refused under refuse_unlabelled)
# ---------------------------------------------------------------------------------------------------------


def test_label_decisions(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    base = fixture_files["sample.docx"]
    labelled = labelled_docx(
        base, tmp_path / "l.docx", custom=custom_xml((LABEL, SITE, "Highly Confidential", "true"))
    )
    by_id = PolicyConfig(exclude_label_ids=(LABEL,))
    by_name = PolicyConfig(exclude_label_names=("highly confidential",))
    strict = PolicyConfig(refuse_unlabelled=True)
    for pol in (by_id, by_name):
        s = policy.screen_file(labelled, name="l.docx", policy=pol)
        assert s is not None and s.status is ScreenStatus.REFUSED and s.code == "label-excluded"
        assert policy.is_refusal_reason(s.reason) and "Highly Confidential" in s.reason
    assert (
        policy.screen_file(labelled, name="l.docx", policy=PolicyConfig(exclude_label_ids=(OTHER_LABEL,)))
        is None
    )
    assert policy.screen_file(labelled, name="l.docx", policy=strict) is None
    unl = policy.screen_file(base, name="sample.docx", policy=strict)
    assert unl is not None and unl.code == "unlabelled"
    assert policy.screen_file(base, name="sample.docx", policy=by_id) is None
    broken = labelled_docx(base, tmp_path / "b.docx", labelinfo=b"<nope")
    s = policy.screen_file(broken, name="b.docx", policy=strict)
    assert s is not None and s.code == "label-unreadable"  # fail closed when the label cannot be read
    txt = tmp_path / "t.txt"
    txt.write_text("plain\n")
    assert policy.screen_file(txt, name="t.txt", policy=strict) is None  # a .txt cannot carry a label
    assert policy.screen_item_label("Highly Confidential", by_name) is not None
    assert policy.screen_item_label(f"{{{LABEL.upper()}}}", by_id) is not None
    assert policy.screen_item_label("General", by_name) is None
    assert policy.screen_item_label(None, by_name) is None


# ---------------------------------------------------------------------------------------------------------
# encryption: C15 §9 #24 / #25, before any converter runs
# ---------------------------------------------------------------------------------------------------------


def test_sniff_container(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    assert policy.sniff_container(fixture_files["sample.docx"]) is ContainerKind.ZIP
    assert (
        policy.sniff_container(cfb_file(tmp_path / "e.docx", encrypted=True)) is ContainerKind.CFB_ENCRYPTED
    )
    assert policy.sniff_container(cfb_file(tmp_path / "l.doc", encrypted=False)) is ContainerKind.CFB_OTHER
    assert policy.sniff_container(build_pdf(tmp_path / "p.pdf")) is ContainerKind.PDF
    assert policy.sniff_container(build_pdf(tmp_path / "x.pdf", encrypt=True)) is ContainerKind.PDF_ENCRYPTED
    assert policy.sniff_container(fixture_files["sample.txt"]) is ContainerKind.OTHER
    (tmp_path / "empty").write_bytes(b"")
    assert policy.sniff_container(tmp_path / "empty") is ContainerKind.EMPTY


def test_encrypted_office_is_an_unreadable_stub_without_running_a_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = cfb_file(tmp_path / "Board Pack.docx", encrypted=True)
    reg = Registry.default(CFG)
    conv = reg.for_name("x.docx")
    assert conv is not None
    calls: list[str] = []
    inner = conv.inner  # type: ignore[attr-defined]
    monkeypatch.setattr(inner, "convert", lambda s, *, name: calls.append(name) or ())
    r1 = run_convert(src, "Board Pack.docx", reg, tmp_path / "cache")
    assert r1.status is ConversionStatus.UNREADABLE and r1.units == ()
    assert r1.reason == policy.ENCRYPTED_OFFICE_REASON and r1.reason.startswith("encrypted-office")
    assert calls == []  # the converter never saw the ciphertext
    r2 = run_convert(src, "Board Pack.docx", reg, tmp_path / "cache")
    assert r2.from_cache and r2.status is ConversionStatus.UNREADABLE  # settled: no retry storm


def test_encrypted_pdf_is_an_unreadable_stub(tmp_path: Path) -> None:
    src = build_pdf(tmp_path / "locked.pdf", encrypt=True)
    r = run_convert(src, "locked.pdf", Registry.default(CFG), tmp_path / "cache")
    assert r.status is ConversionStatus.UNREADABLE and r.reason == policy.ENCRYPTED_PDF_REASON


def test_legacy_cfb_is_not_called_encrypted(tmp_path: Path) -> None:
    assert (
        policy.screen_file(
            cfb_file(tmp_path / "old.doc", encrypted=False), name="old.doc", policy=PolicyConfig()
        )
        is None
    )


class _Fake:
    """A converter whose output or failure the test chooses."""

    converter_id = "fake"
    extensions = (".fk",)

    def __init__(self, body: str | None = "hello\n", raises: Exception | None = None) -> None:
        self.body = body
        self.raises = raises

    def version(self) -> str:
        return "1"

    def options(self) -> dict[str, str]:
        return {}

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        if self.raises is not None:
            raise self.raises
        body = self.body or ""
        return (
            RenderedUnit(
                unit_id="whole",
                kind=UnitKind.WHOLE,
                index=0,
                of=1,
                name="",
                file_stem="",
                title="t",
                summary="s",
                tokens_estimate=1,
                body=body,
                rendered_sha256=hashlib.sha256(body.encode()).hexdigest(),
            ),
        )


class PDFPasswordIncorrect(Exception):  # noqa: N818 - mirrors pdfminer's exception name
    """Stand-in for pdfminer.pdfdocument.PDFPasswordIncorrect."""


def test_guard_maps_pdf_password_errors_and_empty_output(tmp_path: Path) -> None:
    src = tmp_path / "a.fk"
    src.write_text("x\n")
    locked = Registry([_Fake(raises=PDFPasswordIncorrect("no password"))], banner=True)
    r = run_convert(src, "a.fk", locked, tmp_path / "c1")
    assert r.status is ConversionStatus.UNREADABLE and r.reason == policy.ENCRYPTED_PDF_REASON
    blank = Registry([_Fake(body="\x0c\n")], banner=True)  # pdfminer's "empty" page is a form feed
    r = run_convert(src, "a.fk", blank, tmp_path / "c2")
    assert r.status is ConversionStatus.UNREADABLE and r.reason == policy.EMPTY_OUTPUT_REASON
    other = Registry([_Fake(raises=RuntimeError("boom"))], banner=True)
    assert run_convert(src, "a.fk", other, tmp_path / "c3").status is ConversionStatus.FAILED


def test_label_refusal_through_the_default_registry(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    base = fixture_files["sample.docx"]
    labelled = labelled_docx(base, tmp_path / "l.docx", labelinfo=labelinfo_xml((LABEL, SITE, "1", "0")))
    pol = PolicyConfig(exclude_label_ids=(LABEL,))
    reg = Registry.default(CFG, policy=pol)
    assert reg.policy is pol
    s = reg.screen(labelled, name="l.docx")
    assert s is not None and s.code == "label-excluded"
    r = run_convert(labelled, "l.docx", reg, tmp_path / "c")
    # convert_file screens labels BEFORE the cache (integrator fix): a true REFUSED, never cached
    assert r.status is ConversionStatus.REFUSED and r.units == () and policy.is_refusal_reason(r.reason)
    ok = run_convert(base, "sample.docx", reg, tmp_path / "c")
    assert ok.status is ConversionStatus.OK
    # H1 ignores LabelInfo.xml / docProps, so a relabel-only copy can share the unlabelled copy's action key;
    # the cached OK result must still not be served for it
    again = convert_file(
        labelled,
        name="l.docx",
        content_sha256=hashlib.sha256(labelled.read_bytes()).hexdigest(),
        canonical_sha256=ok.canonical_sha256,
        registry=reg,
        cache=ConverterCache(tmp_path / "c"),
    )
    assert again.action_key == ok.action_key
    assert again.status is ConversionStatus.REFUSED and not again.from_cache
    # the policy is part of the action key: changing it can never serve a result cached under another policy
    plain = run_convert(base, "sample.docx", Registry.default(CFG), tmp_path / "c")
    assert plain.action_key != ok.action_key and not plain.from_cache


# ---------------------------------------------------------------------------------------------------------
# the untrusted-content boundary
# ---------------------------------------------------------------------------------------------------------


def test_default_registry_bodies_carry_the_banner_and_h2_covers_it(
    fixture_files: dict[str, Path], tmp_path: Path
) -> None:
    reg = Registry.default(CFG)
    for name, path in sorted(fixture_files.items()):
        r = run_convert(path, name, reg, tmp_path / name)
        assert r.status is ConversionStatus.OK, (name, r.reason)
        for u in r.units:
            assert u.body.startswith(policy.UNTRUSTED_BANNER + "\n\n"), name
            assert u.rendered_sha256 == hashlib.sha256(u.body.encode()).hexdigest(), name
            assert u.body.endswith("\n") and not u.body.endswith("\n\n"), name
    raw = Registry([_Fake()])  # a bare registry (tests, tools) is untouched
    assert run_convert(fixture_files["sample.txt"], "x.fk", raw, tmp_path / "raw").units[0].body == "hello\n"


def test_with_banner_is_idempotent() -> None:
    once = policy.with_banner("# T\n\nbody\n")
    assert once == f"{policy.UNTRUSTED_BANNER}\n\n# T\n\nbody\n"
    assert policy.with_banner(once) == once and policy.has_banner(once)
    assert "\n" not in policy.UNTRUSTED_BANNER  # one line


@pytest.mark.parametrize(
    ("name", "neutral"),
    [
        ("CLAUDE.md", "CLAUDE-doc.md"),
        ("claude.md", "claude-doc.md"),
        ("CLAUDE.local.md", "CLAUDE-doc.local.md"),
        ("AGENTS.md", "AGENTS-doc.md"),
        ("AGENTS.override.md", "AGENTS-doc.override.md"),
        ("AGENT.md", "AGENT-doc.md"),
        ("GEMINI.md", "GEMINI-doc.md"),
        ("CONVENTIONS.md", "CONVENTIONS-doc.md"),
        ("SKILL.md", "SKILL-doc.md"),
        ("copilot-instructions.md", "copilot-instructions-doc.md"),
        (".cursorrules", "dot-cursorrules"),
        (".windsurfrules", "dot-windsurfrules"),
        (".claude", "dot-claude"),
        (".github", "dot-github"),
        ("Budget.xlsx", "Budget.xlsx"),
        ("claude-notes.md", "claude-notes.md"),
        ("CLAUDE-doc.md", "CLAUDE-doc.md"),
        ("dot-claude", "dot-claude"),
    ],
)
def test_neutralise_name(name: str, neutral: str) -> None:
    assert policy.neutralise_name(name) == neutral
    assert policy.neutralise_name(neutral) == neutral  # idempotent


def test_neutralise_rel_path_every_segment() -> None:
    assert policy.neutralise_rel_path(".claude/commands/deploy.md") == "dot-claude/commands/deploy.md"
    assert (
        policy.neutralise_rel_path(".github/copilot-instructions.md")
        == "dot-github/copilot-instructions-doc.md"
    )
    assert policy.neutralise_rel_path(".cursor/rules/style.mdc") == "dot-cursor/rules/style.mdc"
    assert policy.neutralise_rel_path("Team/AGENTS.md") == "Team/AGENTS-doc.md"
    assert policy.is_agent_instruction_name("CLAUDE.md") and not policy.is_agent_instruction_name("notes.md")
