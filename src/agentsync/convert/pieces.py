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

from collections.abc import Iterable
from pathlib import Path


class PieceStore:
    """The stored pieces of every recording being read, under ``root`` (``<cache_dir>/recordings``)."""

    def __init__(self, root: Path) -> None:
        """Bind ``root``; nothing is created until a piece is saved."""
        self.root = root

    @classmethod
    def under(cls, cache_dir: Path) -> PieceStore:
        """The store of ``cache_dir``: ``<cache_dir>/recordings``."""
        return cls(cache_dir / "recordings")

    def load(self, sha256: str, piece: int, key: str) -> bytes | None:
        """The bytes saved for piece ``piece`` of recording ``sha256`` under ``key``, or None when there are
        none or they were saved under another key (a corrupt or foreign file counts as none).  Never
        raises."""
        raise NotImplementedError

    def save(self, sha256: str, piece: int, key: str, data: bytes) -> None:
        """Save piece ``piece`` atomically (temporary name, fsync, rename), replacing any piece of that
        number."""
        raise NotImplementedError

    def set_progress(self, sha256: str, *, done_ms: int, total_ms: int) -> None:
        """Record how much of the recording's media time is read; atomic."""
        raise NotImplementedError

    def progress(self, sha256: str) -> tuple[int, int] | None:
        """(done_ms, total_ms) last recorded for ``sha256``, or None.  Never raises."""
        raise NotImplementedError

    def pending(self) -> list[str]:
        """The hashes that have a folder in the store, sorted."""
        raise NotImplementedError

    def remove(self, sha256: str) -> bool:
        """Delete the recording's folder; True when there was one."""
        raise NotImplementedError

    def prune(self, keep: Iterable[str]) -> int:
        """Delete every recording folder whose hash is not in ``keep`` (and stale temporary files); return the
        number removed."""
        raise NotImplementedError
