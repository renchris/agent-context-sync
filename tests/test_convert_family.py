"""B13 round 28 I39: a package of another Office family is "not the type its name says", and no conversion
error names the staged path or the home folder."""

from __future__ import annotations

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


def test_a_stub_an_older_build_wrote_a_staging_path_into_is_read_again_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row settled with the path in its reason is never converted again by itself. The older build ran
    4 times, so its retries are used up (3 conversions: the first and ``_CONVERT_RETRIES``); this build
    converts it once more past them, the re-read writes it without the path, and then leaves it alone
    (B13 round 29 C181: I39's re-read went through the retries and so never reached such a stub)."""
    config, src = make_env(tmp_path)
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


@pytest.mark.parametrize("gap", ["  ", "\t"])
def test_a_reason_loses_the_paths_before_its_whitespace_is_collapsed(tmp_path: Path, gap: str) -> None:
    """B13 round 29 C221: ``_reason`` collapsed whitespace first, so a staging folder with two spaces in a
    row, or a tab, no longer matched and its path stayed in the reason."""
    from agentsync.convert import _reason  # noqa: PLC0415

    staged = tmp_path / f"Contoso{gap}State" / "staging" / "ab12"
    staged.mkdir(parents=True)
    src = staged / "Q3 deck.pptx"
    for given in (src, src.resolve()):
        said = _reason(ValueError(f"file '{given}' is not a\nPowerPoint file"), src, src.name)
        assert said == "file 'Q3 deck.pptx' is not a PowerPoint file"
        assert _reason(ValueError(f"cannot open '{given.parent}/media/x.png'"), src, src.name) == (
            "cannot open 'media/x.png'"
        )


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


def test_a_stub_with_a_path_needs_no_home_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """C181: ``_stub_with_a_path`` now runs in the cycle, so a Mac with no home folder to read (no HOME, no
    password entry) gets the staging words alone, as ``_homeless`` does, never a RuntimeError."""
    from agentsync.convert._common import _stub_with_a_path  # noqa: PLC0415

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    assert _stub_with_a_path("file '/Volumes/x/agentsync/staging/ab12/a.pptx' is not")
    assert not _stub_with_a_path("not-ooxml: a Word document, not a deck")
