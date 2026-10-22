"""Test stubs that run by path as a Mach-O, not as a ``#!`` script (B13 round 23 lane K, A97).

macOS 15.8.1 keeps about 1.1 KiB of kernel memory for every ``#!`` script run by path, until reboot; about
20 GiB panics the Mac. A census on 2026-10-21 put about half of this Mac's ``#!`` execs on this suite's stubs
(the launchctl stub alone 29%). :func:`write` writes a stub as a copy of ``exec_trampoline.c``'s binary that
names its body (the ``#!`` text, in a file of the same name under ``<dir>/../.<dir>-real/``) and runs
that body through the interpreter its first line names. A stub keeps its path, its mode and what it prints;
only ``$0`` becomes the body's path, so a body that reads its own path (``$0``, ``BASH_SOURCE``,
``sys.argv[0]``, ``__file__``) is still written as the plain ``#!`` file, as is every stub when
:data:`TRAMPOLINE` is ``None`` (no compiler, not macOS, or a build that failed its self-test). conftest's
session fixture sets it.

Why a copy per stub and not a symlink to one binary: a test that chmods, rewrites or appends to a stub would
change the shared binary under every other test, and ``cp`` or ``shutil.copy`` of a symlinked stub copies the
binary without its body. A copy carries the body's absolute path in its trailer, so a copied stub still runs.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).with_name("exec_trampoline.c")
MAGIC = b"XTRAMP01"
CC = "/usr/bin/cc"

TRAMPOLINE: bytes | None = None
"""The trampoline's bytes, or ``None``: every stub is then the plain ``#!`` file."""

_SELF_PATH = re.compile(r"\$0|\$\{0|BASH_SOURCE|argv\[0\]|__file__")


def body_path(stub: Path) -> Path:
    """Where a stub's body goes: ``<dir>/../.<dir>-real/<name>`` (the cc-shim layout, shim.md §3), so the body
    keeps the stub's name and depth and the stub's own folder gains nothing a test could list."""
    stub = Path.cwd() / stub
    return stub.parent.parent / f".{stub.parent.name}-real" / stub.name


def _plain(path: Path, text: str, mode: int) -> Path:
    path.write_text(text)
    path.chmod(mode)
    return path


def write(path: Path, text: str, mode: int = 0o755) -> Path:
    """Write ``text`` as an executable stub at ``path`` (see the module docstring)."""
    path = Path(path)
    if TRAMPOLINE is None or not text.startswith("#!") or _SELF_PATH.search(text) or path.is_symlink():
        return _plain(path, text, mode)
    body = body_path(path)
    try:
        body.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            body.unlink()  # a new file: a bash still running the old body reads on from its own copy
        body.write_text(text)
    except OSError:  # no room beside the stub's folder
        return _plain(path, text, mode)
    body.chmod(0o755)
    raw = os.fsencode(str(body))
    with contextlib.suppress(FileNotFoundError):
        path.unlink()  # a new file, never the old one rewritten: macOS kills a signed file changed under it
    path.write_bytes(TRAMPOLINE + raw + struct.pack("<I", len(raw)) + MAGIC)
    path.chmod(mode)
    return path


def build(cache: Path) -> bytes | None:
    """The trampoline's bytes, built once per source hash with /usr/bin/cc and kept under ``cache``; ``None``
    off macOS, without a compiler, or when the build or its self-test fails."""
    if sys.platform != "darwin" or not os.access(CC, os.X_OK):
        return None
    src = SOURCE.read_bytes()
    where = cache / f"{hashlib.sha256(src).hexdigest()[:16]}-{platform.machine()}"
    exe = where / "exec-trampoline"
    if not exe.is_file():
        where.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=where) as tmp:
            out = Path(tmp) / "exec-trampoline"
            cp = subprocess.run(
                [CC, "-Os", "-Wall", "-Wextra", "-Werror", "-o", str(out), str(SOURCE)],
                capture_output=True,
                check=False,
                timeout=120,
            )
            if cp.returncode != 0 or not out.is_file():
                return None
            out.replace(exe)  # one rename: a concurrent build of the same source writes the same bytes
    data = exe.read_bytes()
    return data if _works(data) else None


def _works(data: bytes) -> bool:
    """One stub through the trampoline: its argv and exit code as the kernel would give them."""
    global TRAMPOLINE  # noqa: PLW0603
    saved, TRAMPOLINE = TRAMPOLINE, data
    try:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bin").mkdir()
            stub = write(Path(tmp) / "bin" / "probe", '#!/bin/sh\nprintf "%s|" "$@"\nexit 7\n')
            cp = subprocess.run([stub, "a b", "c"], capture_output=True, text=True, check=False, timeout=30)
            return cp.returncode == 7 and cp.stdout == "a b|c|" and stub.read_bytes().startswith(data)
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        TRAMPOLINE = saved
