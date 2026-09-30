"""Write-once converter cache keyed on producer identity (design 4.1 #5) (owner: convert).

On disk: ``<root>/<key[:2]>/<key>/result.json`` + ``unit-<index>.md`` + ``sidecar-<index>-<name>``; written
into ``<root>/tmp-<random>/`` and renamed into place, so a half-written key is never readable. Never in git.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentsync.model import ConversionResult, ConversionStatus, RenderedUnit, UnitKind

KEY_SCHEMA_VERSION = 1
"""Bumped ONLY when the composition of the action key changes (never for a converter change)."""

log = logging.getLogger(__name__)

_RESULT_FORMAT = 1  # on-disk layout of result.json (independent of the action-key schema)
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SIDECAR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_CACHEABLE = frozenset({ConversionStatus.OK, ConversionStatus.UNREADABLE})


def action_key(
    *,
    converter_id: str,
    converter_version: str,
    options_hash: str,
    canonical_sha256: str,
    unit_id: str = "whole",
) -> str:
    """Return sha256 hex of ``"|".join([KEY_SCHEMA_VERSION, converter_id, converter_version, options_hash,
    unit_id, canonical_sha256])`` (no env allowlist: converters run with a fixed env)."""
    parts = [
        str(KEY_SCHEMA_VERSION),
        converter_id,
        converter_version,
        options_hash,
        unit_id,
        canonical_sha256,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry to disk (best effort; some filesystems refuse O_RDONLY dir fsync)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_file(path: Path, data: bytes) -> None:
    """Create ``path`` 0600 with ``data`` and fsync it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _unit_to_json(unit: RenderedUnit) -> dict[str, Any]:
    """Unit metadata for result.json (the body and sidecar bytes live in their own files)."""
    return {
        "body_sha256": unit.rendered_sha256,
        "file_stem": unit.file_stem,
        "index": unit.index,
        "kind": unit.kind.value,
        "name": unit.name,
        "of": unit.of,
        "sidecars": [
            {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in unit.sidecars
        ],
        "summary": unit.summary,
        "title": unit.title,
        "tokens_estimate": unit.tokens_estimate,
        "unit_id": unit.unit_id,
    }


class _CorruptEntry(Exception):  # noqa: N818 - private signal, never escapes this module
    """A cache entry failed validation."""


class ConverterCache:
    """Filesystem cache of ConversionResults; safe against crashes; single writer (the ops lock)."""

    def __init__(self, root: Path) -> None:
        """Bind to ``root`` (created 0700 on first put)."""
        self.root = root

    def path_for(self, key: str) -> Path:
        """Return the directory a key lives in (may not exist)."""
        if not _KEY_RE.match(key):
            raise ValueError(f"not an action key (64 lowercase hex): {key!r}")
        return self.root / key[:2] / key

    # -- read ---------------------------------------------------------------------------------------------

    def get(self, key: str) -> ConversionResult | None:
        """Return the cached result with ``from_cache=True``, or None on miss/corrupt entry (corrupt is
        logged)."""
        try:
            entry = self.path_for(key)
        except ValueError:
            log.warning("converter cache: refusing malformed key %r", key)
            return None
        meta_path = entry / "result.json"
        if not meta_path.is_file():
            return None
        try:
            return self._load(entry, key)
        except (_CorruptEntry, OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("converter cache: corrupt entry %s (%s); moved aside for re-conversion", key, exc)
            self._discard(entry)
            return None

    def _load(self, entry: Path, key: str) -> ConversionResult:
        """Parse and verify one entry (every body and sidecar is checked against its recorded digest)."""
        doc = json.loads((entry / "result.json").read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != _RESULT_FORMAT:
            raise _CorruptEntry("unknown result.json format")
        if doc.get("action_key") != key:
            raise _CorruptEntry("action_key does not match the entry directory")
        status = ConversionStatus(doc["status"])
        units: list[RenderedUnit] = []
        for u in doc["units"]:
            index = int(u["index"])
            body_bytes = (entry / f"unit-{index}.md").read_bytes()
            if hashlib.sha256(body_bytes).hexdigest() != u["body_sha256"]:
                raise _CorruptEntry(f"unit-{index}.md digest mismatch")
            sidecars: list[tuple[str, bytes]] = []
            for sc in u["sidecars"]:
                name = str(sc["name"])
                data = (entry / f"sidecar-{index}-{name}").read_bytes()
                if hashlib.sha256(data).hexdigest() != sc["sha256"]:
                    raise _CorruptEntry(f"sidecar {name} digest mismatch")
                sidecars.append((name, data))
            body = body_bytes.decode("utf-8")
            units.append(
                RenderedUnit(
                    unit_id=str(u["unit_id"]),
                    kind=UnitKind(u["kind"]),
                    index=index,
                    of=int(u["of"]),
                    name=str(u["name"]),
                    file_stem=str(u["file_stem"]),
                    title=str(u["title"]),
                    summary=str(u["summary"]),
                    tokens_estimate=int(u["tokens_estimate"]),
                    body=body,
                    rendered_sha256=str(u["body_sha256"]),
                    sidecars=tuple(sidecars),
                )
            )
        if status is ConversionStatus.OK and not units:
            raise _CorruptEntry("OK entry without units")
        reason = doc.get("reason")
        return ConversionResult(
            status=status,
            converter_id=str(doc["converter_id"]),
            converter_version=str(doc["converter_version"]),
            options_hash=str(doc["options_hash"]),
            action_key=key,
            content_sha256=str(doc["content_sha256"]),
            canonical_sha256=str(doc["canonical_sha256"]),
            units=tuple(units),
            reason=None if reason is None else str(reason),
            from_cache=True,
        )

    # -- write --------------------------------------------------------------------------------------------

    def _ensure_root(self) -> None:
        """Create the root 0700 if missing (parents with default mode)."""
        if not self.root.is_dir():
            self.root.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileExistsError):
                self.root.mkdir(mode=0o700)
        with contextlib.suppress(OSError):
            self.root.chmod(0o700)

    def put(self, result: ConversionResult) -> bool:
        """Store ``result`` under ``result.action_key`` if absent (write-once); return True if written.

        Only OK and UNREADABLE results are cached; FAILED/REFUSED are not (a transient failure retries).
        """
        if result.status not in _CACHEABLE:
            return False
        final = self.path_for(result.action_key)
        if final.exists():
            return False
        for unit in result.units:
            for name, _data in unit.sidecars:
                if not _SIDECAR_NAME_RE.match(name) or ".." in name:
                    raise ValueError(f"unsafe sidecar name for the cache: {name!r}")
        self._ensure_root()
        tmp = self.root / f"tmp-{secrets.token_hex(8)}"
        tmp.mkdir(mode=0o700)
        try:
            for unit in result.units:
                _write_file(tmp / f"unit-{unit.index}.md", unit.body.encode("utf-8"))
                for name, data in unit.sidecars:
                    _write_file(tmp / f"sidecar-{unit.index}-{name}", data)
            doc = {
                "action_key": result.action_key,
                "canonical_sha256": result.canonical_sha256,
                "content_sha256": result.content_sha256,
                "converter_id": result.converter_id,
                "converter_version": result.converter_version,
                "format": _RESULT_FORMAT,
                "options_hash": result.options_hash,
                "reason": result.reason,
                "status": result.status.value,
                "units": [_unit_to_json(u) for u in result.units],
            }
            blob = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
            _write_file(tmp / "result.json", blob.encode("utf-8"))  # last: its presence marks completeness
            _fsync_dir(tmp)
            final.parent.mkdir(mode=0o700, exist_ok=True)
            try:
                tmp.rename(final)
            except OSError:
                if final.exists():  # lost a race with another writer: first write wins
                    shutil.rmtree(tmp, ignore_errors=True)
                    return False
                raise
            _fsync_dir(final.parent)
            return True
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def _discard(self, entry: Path) -> None:
        """Move a corrupt entry out of the key space (gc deletes it); never raises."""
        aside = self.root / f"tmp-corrupt-{secrets.token_hex(8)}"
        try:
            entry.rename(aside)
        except OSError:
            shutil.rmtree(entry, ignore_errors=True)

    def delete(self, keys: Iterable[str]) -> int:
        """Delete the entries of ``keys`` now (label escalation, purge); return how many existed."""
        removed = 0
        for key in sorted(set(keys)):
            try:
                entry = self.path_for(key)
            except ValueError:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
            elif entry.exists() or entry.is_symlink():
                entry.unlink(missing_ok=True)
                removed += 1
        return removed

    # -- gc -----------------------------------------------------------------------------------------------

    def gc(self, live_keys: Iterable[str]) -> int:
        """Delete every key not in ``live_keys`` plus stale ``tmp-*`` dirs; return the number removed."""
        if not self.root.is_dir():
            return 0
        live = frozenset(live_keys)
        removed = 0
        for child in sorted(self.root.iterdir()):
            if child.name.startswith("tmp-"):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed += 1
                continue
            if not (child.is_dir() and len(child.name) == 2 and not child.is_symlink()):
                continue
            for entry in sorted(child.iterdir()):
                if entry.name in live:
                    continue
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
            with contextlib.suppress(OSError):
                child.rmdir()  # only succeeds when the shard is now empty
        return removed
