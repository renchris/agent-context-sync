"""The piece store of a recording read in pieces (spec 4.1, ruling 4).

``<cache_dir>/recordings/<canonical sha256>/``: outside the converter cache's two-character shards and its
``tmp-*`` folders, so ``ConverterCache.gc`` never reaches it.  A piece is the stored state of one stretch of a
recording's picture track (5 minutes of media time), saved under a key that names the stages and options that
made it; a piece whose key no longer matches is redone.  Every write goes to a temporary name in the
recording's folder and is renamed into place, so a killed cycle leaves no half-written piece.
``progress.json`` holds the media time read and the recording's length, which ``loop`` reads for the "N of M
minutes" note without running anything.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import shutil
from collections.abc import Iterable
from pathlib import Path

from agentsync.convert.cache import _fsync_dir, _write_file

_SHA_RE = re.compile(r"[0-9a-f]{64}")
_FORMAT = 1  # on-disk layout of a piece file and of progress.json
_TMP_PREFIX = ".tmp-"  # a write in flight; a killed one is left behind and pruned, never loaded
_PROGRESS = "progress.json"


def _piece_name(piece: int) -> str:
    """The file name of piece ``piece`` (``piece-<n>.bin``)."""
    if piece < 0:
        raise ValueError(f"not a piece number: {piece!r}")
    return f"piece-{piece}.bin"


def _check_sha(sha256: str) -> None:
    """Refuse a hash that is not 64 lowercase hex, before it becomes part of a path."""
    if not _SHA_RE.fullmatch(sha256):
        raise ValueError(f"not a sha256 (64 lowercase hex): {sha256!r}")


class PieceStore:
    """The stored pieces of every recording being read, under ``root`` (``<cache_dir>/recordings``).

    A piece file is one JSON header line (format, key, sha256 and size of the bytes) followed by the bytes, so
    a file saved under another key, cut short or changed on disk reads as none.  Folders and files are
    owner-only (0700 and 0600), as in the converter cache.  Single writer (the ops lock).
    """

    def __init__(self, root: Path) -> None:
        """Bind ``root``; nothing is created until a piece is saved."""
        self.root = root

    @classmethod
    def under(cls, cache_dir: Path) -> PieceStore:
        """The store of ``cache_dir``: ``<cache_dir>/recordings``."""
        return cls(cache_dir / "recordings")

    # -- read ---------------------------------------------------------------------------------------------

    def load(self, sha256: str, piece: int, key: str) -> bytes | None:
        """The bytes saved for piece ``piece`` of recording ``sha256`` under ``key``, or None when there are
        none or they were saved under another key (a corrupt or foreign file counts as none).  Never
        raises."""
        try:
            _check_sha(sha256)
            raw = (self.root / sha256 / _piece_name(piece)).read_bytes()
            line, sep, data = raw.partition(b"\n")
            if not sep:
                return None
            header = json.loads(line.decode("utf-8"))
            if not isinstance(header, dict) or header.get("format") != _FORMAT or header.get("key") != key:
                return None
            if header.get("size") != len(data) or header.get("sha256") != hashlib.sha256(data).hexdigest():
                return None
            return data
        except (OSError, ValueError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            return None

    def progress(self, sha256: str) -> tuple[int, int] | None:
        """(done_ms, total_ms) last recorded for ``sha256``, or None.  Never raises."""
        try:
            _check_sha(sha256)
            doc = json.loads((self.root / sha256 / _PROGRESS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("format") != _FORMAT:
            return None
        done, total = doc.get("done_ms"), doc.get("total_ms")
        if type(done) is not int or type(total) is not int or done < 0 or total < 0:
            return None
        return done, total

    def pending(self) -> list[str]:
        """The hashes that have a folder in the store, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(
            child.name
            for child in self.root.iterdir()
            if _SHA_RE.fullmatch(child.name) and child.is_dir() and not child.is_symlink()
        )

    # -- write --------------------------------------------------------------------------------------------

    def save(self, sha256: str, piece: int, key: str, data: bytes) -> None:
        """Save piece ``piece`` atomically (temporary name, fsync, rename), replacing any piece of that
        number."""
        _check_sha(sha256)
        name = _piece_name(piece)
        digest = hashlib.sha256(data).hexdigest()
        header = {"format": _FORMAT, "key": key, "sha256": digest, "size": len(data)}
        line = json.dumps(header, sort_keys=True, ensure_ascii=True).encode("ascii") + b"\n"
        self._write(sha256, name, line + data)

    def set_progress(self, sha256: str, *, done_ms: int, total_ms: int) -> None:
        """Record how much of the recording's media time is read; atomic."""
        _check_sha(sha256)
        if done_ms < 0 or total_ms < 0:
            raise ValueError(f"negative progress: {done_ms!r} of {total_ms!r}")
        doc = {"done_ms": done_ms, "format": _FORMAT, "total_ms": total_ms}
        self._write(sha256, _PROGRESS, (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8"))

    def _folder(self, sha256: str) -> Path:
        """The recording's folder, created 0700 (with the store root) when missing."""
        for path in (self.root, self.root / sha256):
            if not path.is_dir():
                path.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(FileExistsError):
                    path.mkdir(mode=0o700)
            with contextlib.suppress(OSError):
                path.chmod(0o700)
        return self.root / sha256

    def _write(self, sha256: str, name: str, blob: bytes) -> None:
        """Write ``blob`` to a temporary name in the recording's folder, fsync, rename onto ``name``."""
        folder = self._folder(sha256)
        tmp = folder / f"{_TMP_PREFIX}{secrets.token_hex(8)}"
        try:
            _write_file(tmp, blob)
            tmp.replace(folder / name)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(folder)

    # -- delete -------------------------------------------------------------------------------------------

    def remove(self, sha256: str) -> bool:
        """Delete the recording's folder; True when there was one."""
        _check_sha(sha256)
        folder = self.root / sha256
        if folder.is_symlink() or not folder.is_dir():
            return False
        shutil.rmtree(folder, ignore_errors=True)
        return True

    def prune(self, keep: Iterable[str]) -> int:
        """Delete every recording folder whose hash is not in ``keep`` (and stale temporary files); return the
        number removed."""
        if not self.root.is_dir():
            return 0
        kept = frozenset(keep)
        removed = 0
        for child in sorted(self.root.iterdir()):
            if child.name.startswith(_TMP_PREFIX):
                child.unlink(missing_ok=True)
                removed += 1
                continue
            if not (_SHA_RE.fullmatch(child.name) and child.is_dir() and not child.is_symlink()):
                continue
            if child.name not in kept:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
                continue
            for entry in sorted(child.iterdir()):
                if entry.name.startswith(_TMP_PREFIX):
                    entry.unlink(missing_ok=True)
                    removed += 1
        return removed
