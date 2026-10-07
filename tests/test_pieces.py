"""The piece store of a recording read in pieces (spec 4.1): atomic writes, keys, progress, clean-up."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agentsync.convert.cache import ConverterCache
from agentsync.convert.pieces import PieceStore

A = "a" * 64
B = "b" * 64
C = "c" * 64


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_saved_piece_loads_back_under_its_key(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    assert store.root == tmp_path / "recordings"
    assert not store.root.exists()  # nothing is created until a piece is saved
    store.save(A, 0, "k1", b"\x00state\nwith a newline")
    store.save(A, 1, "k1", b"")
    assert store.load(A, 0, "k1") == b"\x00state\nwith a newline"
    assert store.load(A, 1, "k1") == b""
    assert store.load(A, 2, "k1") is None
    assert store.load(B, 0, "k1") is None


def test_a_piece_saved_under_another_key_is_none_and_a_new_save_replaces_it(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    store.save(A, 0, "decode=1|ocr=1", b"old")
    assert store.load(A, 0, "decode=1|ocr=2") is None
    store.save(A, 0, "decode=1|ocr=2", b"new")
    assert store.load(A, 0, "decode=1|ocr=2") == b"new"
    assert store.load(A, 0, "decode=1|ocr=1") is None
    assert [p.name for p in (store.root / A).iterdir()] == ["piece-0.bin"]


def test_a_corrupt_or_cut_short_piece_is_none(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    store.save(A, 0, "k", b"0123456789")
    path = store.root / A / "piece-0.bin"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1])
    assert store.load(A, 0, "k") is None
    path.write_bytes(raw[:-1] + b"X")
    assert store.load(A, 0, "k") is None
    path.write_bytes(b"not json\n0123456789")
    assert store.load(A, 0, "k") is None
    path.write_bytes(b"\xff\xfe no newline")
    assert store.load(A, 0, "k") is None


def test_the_store_is_owner_only(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    store.save(A, 0, "k", b"x")
    store.set_progress(A, done_ms=1, total_ms=2)
    assert _mode(store.root) == 0o700
    assert _mode(store.root / A) == 0o700
    assert _mode(store.root / A / "piece-0.bin") == 0o600
    assert _mode(store.root / A / "progress.json") == 0o600


def test_progress_round_trips_and_a_bad_file_is_none(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    assert store.progress(A) is None
    store.set_progress(A, done_ms=300_000, total_ms=3_600_000)
    assert store.progress(A) == (300_000, 3_600_000)
    store.set_progress(A, done_ms=600_000, total_ms=3_600_000)
    assert store.progress(A) == (600_000, 3_600_000)
    progress = store.root / A / "progress.json"
    for bad in (
        "{",
        "[]",
        '{"format": 1, "done_ms": "1", "total_ms": 2}',
        '{"format": 9, "done_ms": 1, "total_ms": 2}',
    ):
        progress.write_text(bad)
        assert store.progress(A) is None
    with pytest.raises(ValueError, match="negative"):
        store.set_progress(A, done_ms=-1, total_ms=2)


@pytest.mark.parametrize("bad", ["../" + "a" * 61, "A" * 64, "a" * 63, "", "a" * 64 + "/x", "a" * 64 + "\n"])
def test_a_hash_that_is_not_64_lowercase_hex_never_reaches_a_path(tmp_path: Path, bad: str) -> None:
    store = PieceStore.under(tmp_path)
    assert store.load(bad, 0, "k") is None
    assert store.progress(bad) is None
    with pytest.raises(ValueError, match="sha256"):
        store.save(bad, 0, "k", b"x")
    with pytest.raises(ValueError, match="sha256"):
        store.set_progress(bad, done_ms=0, total_ms=0)
    with pytest.raises(ValueError, match="sha256"):
        store.remove(bad)
    assert not store.root.exists()


def test_a_killed_write_is_never_loaded_and_is_pruned(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    store.save(A, 0, "k", b"whole")
    killed = store.root / A / ".tmp-0123456789abcdef"  # what a kill between write and rename leaves
    killed.write_bytes(b'{"format":1,"key":"k"}\nhalf')
    stray = store.root / ".tmp-fedcba9876543210"
    stray.write_bytes(b"")
    assert store.load(A, 0, "k") == b"whole"
    assert store.load(A, 1, "k") is None
    assert store.pending() == [A]
    assert store.prune([A]) == 2
    assert not killed.exists() and not stray.exists()
    assert store.load(A, 0, "k") == b"whole"
    assert store.prune([A]) == 0


def test_prune_keeps_what_it_is_told_to(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    for sha in (A, B, C):
        store.save(sha, 0, "k", sha.encode())
    (store.root / "notes.txt").write_text("not a recording")
    assert store.pending() == [A, B, C]
    assert store.prune([B, "d" * 64]) == 2
    assert store.pending() == [B]
    assert store.load(B, 0, "k") == B.encode()
    assert (store.root / "notes.txt").exists()  # only hash folders and temporary files are the store's
    assert store.prune([]) == 1
    assert store.pending() == []
    assert PieceStore(tmp_path / "missing").prune([]) == 0
    assert PieceStore(tmp_path / "missing").pending() == []


def test_remove_deletes_one_recordings_folder(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    store.save(A, 0, "k", b"x")
    store.set_progress(B, done_ms=0, total_ms=1)
    assert store.remove(A) is True
    assert store.remove(A) is False
    assert store.pending() == [B]
    assert store.progress(B) == (0, 1)


def test_the_converter_caches_gc_never_touches_recordings(tmp_path: Path) -> None:
    store = PieceStore.under(tmp_path)
    store.save(A, 0, "k", b"piece")
    store.set_progress(A, done_ms=1, total_ms=2)
    cache = ConverterCache(tmp_path)
    (tmp_path / "tmp-deadbeef").mkdir()  # gc's own territory, to prove it ran
    cache.gc([])
    assert not (tmp_path / "tmp-deadbeef").exists()
    assert store.load(A, 0, "k") == b"piece"
    assert store.progress(A) == (1, 2)
