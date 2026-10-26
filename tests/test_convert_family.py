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
    queues it and says the next sync of its source reads it; that sync says it failed again and is not tried
    again; the one after says nothing. Every note keeps "an older build gave up on ", install.sh's grep."""
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
        f"  note: 1 conversion {_SAID} was tried once more in this sync and failed again; "
        "it is not tried again"
    ], out
    record = _run_record(config)
    assert (record["path_reread"], record["path_reread_failed"]) == (1, 1)
    assert "path_reread_waiting" not in record and "path_reread_current" not in record
    for note in _notes(out):
        assert not re.search(r"\bconverted\b|\bdeferred\b|\bread again\b", note), note
    cli.main(["sync", "--config", cfg])
    assert _notes(capsys.readouterr().out) == []


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
    assert "/staging/" in (_row(config, "Contoso Quarterly.pptx").state_reason or "")
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


def test_a_stub_with_a_path_needs_no_home_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """C181: ``_stub_with_a_path`` now runs in the cycle, so a Mac with no home folder to read (no HOME, no
    password entry) gets the staging words alone, as ``_homeless`` does, never a RuntimeError."""
    from agentsync.convert._common import _stub_with_a_path  # noqa: PLC0415

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    assert _stub_with_a_path("file '/Volumes/x/agentsync/staging/ab12/a.pptx' is not")
    assert not _stub_with_a_path("not-ooxml: a Word document, not a deck")
