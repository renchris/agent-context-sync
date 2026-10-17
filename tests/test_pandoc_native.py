"""The native arm64 pandoc of the wheel's version (load root cause 2026-10-17 X26).

``_bundled_pandoc`` returns ``~/.local/share/agentsync/pandoc/<version>-arm64/bin/pandoc`` on macOS when that
is an executable regular file and ``<version>`` is the one the pypandoc_binary RECORD hash maps to;
otherwise the wheel's binary.  HOME is the test's own tmp dir (``conftest._isolate_home``).
"""

from __future__ import annotations

import importlib.metadata
import inspect
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentsync.convert import pandoc as pandoc_mod

_SHIPPED_TABLE = dict(pandoc_mod._WHEEL_PANDOC_VERSIONS)  # before any test patches it


def _wheel_binary() -> Path:
    import pypandoc  # noqa: PLC0415

    return Path(str(pypandoc.__file__)).resolve().parent / "files" / "pandoc"


def _record_hash() -> str:
    for entry in importlib.metadata.distribution("pypandoc_binary").files or ():
        if entry.as_posix() == "pypandoc/files/pandoc" and entry.hash is not None:
            return entry.hash.value
    raise AssertionError("pypandoc_binary RECORD lists no pypandoc/files/pandoc")


def _stub(home: Path, version: str, *, executable: bool = True) -> Path:
    path = home / ".local/share/agentsync/pandoc" / f"{version}-arm64" / "bin" / "pandoc"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\necho 'pandoc 3.9'\n", encoding="utf-8")
    path.chmod(0o755 if executable else 0o644)
    return path


@pytest.fixture(autouse=True)
def _darwin_known_wheel(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts on darwin with this venv's wheel mapped to 3.9, whatever the host, and reads only its
    own HOME (conftest's wrapper, which also reads the real home's copy, is taken off)."""
    monkeypatch.setattr(pandoc_mod, "_native_pandoc", inspect.unwrap(pandoc_mod._native_pandoc))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(pandoc_mod, "_WHEEL_PANDOC_VERSIONS", {_record_hash(): "3.9"})
    pandoc_mod._wheel_pandoc_version.cache_clear()
    yield
    pandoc_mod._wheel_pandoc_version.cache_clear()


def test_native_copy_of_the_wheels_version_is_returned() -> None:
    native = _stub(Path.home(), "3.9")
    assert pandoc_mod._bundled_pandoc() == native


def test_native_copy_of_another_version_is_ignored() -> None:
    _stub(Path.home(), "3.8")
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_no_native_copy_means_the_wheel() -> None:
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_unknown_record_hash_means_the_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(Path.home(), "3.9")
    monkeypatch.setattr(pandoc_mod, "_WHEEL_PANDOC_VERSIONS", {})
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_missing_record_means_the_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(Path.home(), "3.9")
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: SimpleNamespace(files=None))
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_missing_distribution_means_the_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(Path.home(), "3.9")

    def missing(name: str) -> None:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", missing)
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_not_darwin_means_the_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(Path.home(), "3.9")
    monkeypatch.setattr(sys, "platform", "linux")
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_non_executable_copy_means_the_wheel() -> None:
    _stub(Path.home(), "3.9", executable=False)
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_directory_in_place_of_the_copy_means_the_wheel() -> None:
    (Path.home() / ".local/share/agentsync/pandoc/3.9-arm64/bin/pandoc").mkdir(parents=True)
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


def test_home_is_read_at_each_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()
    other = tmp_path / "other-home"
    native = _stub(other, "3.9")
    monkeypatch.setenv("HOME", str(other))
    assert pandoc_mod._bundled_pandoc() == native


def test_the_operators_copy_reaches_a_test_and_is_only_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _operator_native_pandoc: Callable[[str], Path | None]
) -> None:
    """B13 round 14 Q61: each test's HOME is a tmp folder with no copy, so under the gate the converters ran
    the wheel's pandoc under Rosetta whatever the operator installed. conftest's wrapper finds the copy in
    the home the suite started under, which a test's own copy still outranks; it links and writes nothing."""
    import conftest  # noqa: PLC0415 - the fixture under test

    real = tmp_path / "real-home"
    copy = _stub(real, "3.9")
    before = (copy.read_bytes(), copy.stat().st_mtime_ns)
    monkeypatch.setattr(conftest, "_REAL_HOME", real)
    monkeypatch.setattr(pandoc_mod, "_native_pandoc", _operator_native_pandoc)
    assert Path.home() != real and pandoc_mod._bundled_pandoc() == copy
    assert not (Path.home() / ".local/share/agentsync").exists(), "nothing is linked into the test's home"
    assert (copy.read_bytes(), copy.stat().st_mtime_ns) == before
    own = _stub(Path.home(), "3.9")
    assert pandoc_mod._bundled_pandoc() == own, "a test's own copy comes first"
    monkeypatch.setattr(conftest, "_REAL_HOME", tmp_path / "no-copy")
    (Path.home() / ".local/share/agentsync/pandoc/3.9-arm64/bin/pandoc").unlink()
    assert pandoc_mod._bundled_pandoc() == _wheel_binary()


@pytest.mark.skipif(sys.platform != "darwin", reason="the table lists the macOS wheel")
def test_the_table_knows_this_venvs_wheel() -> None:
    assert _SHIPPED_TABLE.get(_record_hash()) == "3.9"
