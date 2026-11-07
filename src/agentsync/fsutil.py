"""One atomic file write: a private temp file beside the target, fsynced, renamed over it.

The temp's name is never fixed, so a link or a file already at a guessable name is never written through,
and two writers never share one temp."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: str | bytes, *, mode: int | None = None) -> None:
    """Replace ``path`` with ``data`` (a str as UTF-8), whole or not at all; its folder must exist.

    ``mode`` sets the file's bits; None keeps the bits the target has now, and a new file gets 0600. A link
    at ``path`` is replaced, never followed. The temp is gone on any failure, and the old file is untouched.
    Raises OSError."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    if mode is None:
        try:
            mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            mode = 0o600
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
