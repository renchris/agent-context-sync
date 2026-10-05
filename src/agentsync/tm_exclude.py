"""The sticky Time Machine exclusion xattr, written with setxattr(2) (C15 req 42; owner: governance).

A leaf module (standard library only), so ``agentsync.manifest`` can exclude the pre-migration copy it takes
without importing ``agentsync.governance``, which imports the manifest.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

TM_EXCLUDE_XATTR = "com.apple.metadata:com_apple_backup_excludeItem"
"""The sticky Time Machine exclusion ``tmutil addexclusion`` sets on a path (no admin rights needed)."""

TM_EXCLUDE_VALUE = bytes.fromhex(
    "62706C69737430305F1011636F6D2E6170706C652E6261636B75706408000000000000010100000000000000010000000000000000"
    "000000000000001C"
)
"""The value ``tmutil addexclusion`` writes: bplist string ``com.apple.backupd`` (read back 2026-09-29)."""


def _libc_xattr() -> Any:
    """libc with getxattr(2)/setxattr(2) typed (macOS signatures: position and options arguments)."""
    import ctypes  # noqa: PLC0415 — macOS-only, kept off the import path of every other command

    libc = ctypes.CDLL(None, use_errno=True)
    common: list[Any] = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    libc.getxattr.restype = ctypes.c_ssize_t
    libc.getxattr.argtypes = common
    libc.setxattr.restype = ctypes.c_int
    libc.setxattr.argtypes = common
    return libc


def has_xattr(path: Path, name: str) -> bool:
    """Whether ``path`` carries xattr ``name``, via getxattr(2) (``/usr/bin/xattr`` is a Python script that
    costs ~1.8 s per call, measured 2026-09-29; that made every sync cycle ~11 s slower)."""
    return int(_libc_xattr().getxattr(os.fsencode(path), name.encode(), None, 0, 0, 0x0001)) >= 0


def set_exclusion(path: Path) -> bool:
    """Write the sticky exclusion xattr directly with setxattr(2): instant, where ``tmutil`` takes ~11 s.

    ``staging/`` is recreated every cycle, so without this each cycle paid one ``tmutil`` call."""
    buf = TM_EXCLUDE_VALUE
    rc = _libc_xattr().setxattr(os.fsencode(path), TM_EXCLUDE_XATTR.encode(), buf, len(buf), 0, 0x0001)
    return int(rc) == 0 and has_xattr(path, TM_EXCLUDE_XATTR)


def exclude_new_file(path: Path) -> bool:
    """Best-effort exclusion of a file agentsync just created (the manifest's pre-migration copy): True when
    the xattr is now set.  Skipped off macOS and when ``AGENTSYNC_TM_EXCLUDE=0``; never raises."""
    if sys.platform != "darwin" or os.environ.get("AGENTSYNC_TM_EXCLUDE", "1") == "0":
        return False
    try:
        return set_exclusion(path)
    except (OSError, AttributeError):
        return False
