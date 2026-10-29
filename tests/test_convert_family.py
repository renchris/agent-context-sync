"""B13 round 28 I39: a package of another Office family is "not the type its name says", and no conversion
error names the staged path or the home folder."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from agentsync import convert as convert_pkg
from agentsync import cycle as cycle_mod
from agentsync.config import ConvertConfig
from agentsync.convert import ConverterCache, convert_file
from agentsync.convert import pptx as pptx_mod
from agentsync.convert.pandoc import PandocConverter
from agentsync.convert.pptx import PptxConverter
from agentsync.convert.registry import Registry
from agentsync.convert.xlsx import XlsxConverter
from agentsync.errors import ConversionError, UnreadableSourceError
from agentsync.manifest import Manifest
from agentsync.model import ConversionStatus, RowState
from agentsync.setup_report import quarantine_class
from test_convert_builders import build_potx, build_pptx_rich, make_zip
from test_review_fixes import all_objects, git, item_id, make_env, page_fm, run

CFG = ConvertConfig()
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
_WORD_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


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
        _common, "_spellings", lambda path: sorted({str(f) for f in _common._forms(path)}, key=len)[::-1]
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
        assert cut(sibling) == sibling, "a sibling whose name extends it with no space stays whole"


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
    from agentsync.convert._common import _pathless, _spellings  # noqa: PLC0415

    assert "no substring" not in (_spellings.__doc__ or ""), "the docstring says only: longest first"
    staged = tmp_path / "Contoso\\"
    found = _spellings(staged)
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
    not write keeps the key unwritten, and the run after leaves the page byte for byte."""
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

    def refuses(self: PptxConverter, path: Path, *, name: str):  # type: ignore[no-untyped-def]
        raise ConversionError("the deck's Contoso parts " + "cannot be read " * 12 + f"from '{path}'")

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
    assert tmp[:40] in shown, "the page holds the path"
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
