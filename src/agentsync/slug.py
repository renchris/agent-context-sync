"""The slugifier and mirror path rules (design 4.4 converter contract, 4.7 names) (owner: publish).

Implemented by the W2 integrator from the contract text (the publish role's brief left it out; the
reference in ``tests/test_lints.py`` was the starting point).  Every function is pure and deterministic, and
``slugify`` is idempotent, so ``is_portable_path`` can test a path by re-slugging it.
"""

from __future__ import annotations

import hashlib
import unicodedata

from agentsync.policy import is_agent_instruction_name, neutralise_name

MAX_PATH_CHARS = 200
"""Cap on any docs-repo-relative path, so a Windows checkout clears MAX_PATH without core.longpaths."""

WINDOWS_RESERVED_STEMS: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)

STRIP_SUFFIXES: tuple[str, ...] = (".md", ".markdown", ".teams.json", ".eml")
"""Source suffixes dropped from the mirror name (``notes.md`` -> ``notes.md``, not ``notes.md.md``)."""

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"), "-")
_STRIP_CHARS = frozenset('[](){}#<>:"/\\|?*')
_DISAMBIG_LEN = 9  # "-" + 8 hex, what ``disambiguate`` adds
_GENERATED_CAP = MAX_PATH_CHARS - _DISAMBIG_LEN  # generated paths leave room for a later disambiguation
_DIR_SEGMENT_CAP = 40  # per-directory cap used only when a path is too long
_STEM_CAP = 60  # per-unit stem cap used only when a path is too long


def _hash8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _clean(segment: str) -> str:
    """Strip what makes a truncated segment non-portable (trailing dots/spaces) and keep it non-empty."""
    return segment.rstrip(". ") or "untitled"


def slugify(text: str) -> str:
    """Return a portable slug for ONE path segment.

    Lowercase; NFC; strip diacritics; map dashes to '-'; collapse whitespace to '-'; strip ``[ ] ( ) # { }``,
    the NTFS-illegal set ``< > : " / \\ | ? *`` and control bytes; strip trailing dots and spaces; suffix
    ``-doc`` to a Windows reserved stem (with or without extension); never empty (``"untitled"``).
    Dots inside the segment are kept (``report.v2`` stays).  Deterministic and idempotent.
    """
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFC", text))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(_DASHES).lower()
    s = unicodedata.normalize("NFKD", s)  # lower() can re-introduce decomposable characters
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(
        c
        for c in s
        if c not in _STRIP_CHARS and unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn")
    )
    s = "-".join(s.split())
    s = s.rstrip(". ")
    stem, dot, ext = s.partition(".")
    if stem in WINDOWS_RESERVED_STEMS:
        s = f"{stem}-doc{dot}{ext}"
    s = unicodedata.normalize("NFC", s)
    return s or "untitled"


def _strip_suffix(source_name: str) -> str:
    low = source_name.lower()
    for suffix in STRIP_SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix):
            return source_name[: -len(suffix)]
    return source_name


def safe_segment(text: str) -> str:
    """Slug ONE segment, then neutralise it when the slug is an agent instruction name or dot-prefixed.

    Neutralisation is decided on the slug, never on the raw name: slugify folds fullwidth letters, strips
    diacritics, zero-width characters, brackets and leading whitespace, so fullwidth ``CLAUDE``, ``[CLAUDE]``,
    `` .claude`` or ``.git`` would otherwise slug to exactly the names agents (or git) treat specially.
    The result is a fixed point of both ``slugify`` and ``neutralise_name``.
    """
    return neutralise_name(slugify(text))


def is_safe_segment(segment: str) -> bool:
    """True when ``segment`` (an already-slugged docs path segment) is neither dot-prefixed nor an agent
    instruction name (``claude.md``, ``agents.md``, ``.git``, ``.claude`` ...)."""
    return (
        bool(segment)
        and not segment.startswith(".")
        and not is_agent_instruction_name(segment.removesuffix(".md") if segment.endswith(".md") else segment)
    )


def is_safe_mirror_path(path: str) -> bool:
    """True when no segment below ``mirror/<source_id>/`` of ``path`` is unsafe (:func:`is_safe_segment`)."""
    parts = path.split("/")
    return all(is_safe_segment(p) for p in parts[2:] if p)


def mirror_name(source_name: str) -> str:
    """Return the slugged file name for a WHOLE unit: ``safe_segment(name)`` + ``.md`` (STRIP_SUFFIXES)."""
    return safe_segment(_strip_suffix(source_name)) + ".md"


def mirror_dir_name(source_name: str) -> str:
    """Return the slugged directory name for a multi-unit source: ``safe_segment(name)`` + ``.d``."""
    return safe_segment(source_name) + ".d"


def _shorten(prefix: list[str], dirs: list[str], leaf: str, *, leaf_ext: str, reserve: int, key: str) -> str:
    """Shorten the directory part deterministically so it plus ``reserve`` chars fits _GENERATED_CAP.

    The 8-hex hash of ``key`` (source id + rel_path) keeps shortened names unique; the result depends only on
    the item, never on the unit, so every unit of one item lands in the same ``.d`` directory.
    """
    h = _hash8(key)
    dirs = [_clean(safe_segment(d[:_DIR_SEGMENT_CAP])) for d in dirs]
    base = leaf[: -len(leaf_ext)] if leaf.endswith(leaf_ext) else leaf
    cap = _GENERATED_CAP - reserve

    def build(dir_parts: list[str], base_len: int) -> str:
        name = _clean(safe_segment(base[:base_len])) if base_len > 0 else "x"
        return "/".join([*prefix, *dir_parts, f"{name}-{h}{leaf_ext}"])

    # Keep as many leading directories as fit; fold the deeper rest into one hashed segment.
    for keep in range(len(dirs), -1, -1):
        rest = dirs[keep:]
        dir_parts = dirs[:keep] + ([f"deep-{_hash8('/'.join(rest))}"] if rest else [])
        room = cap - (len(build(dir_parts, 0)) - 1)  # everything but the base name
        if room >= 16 or (keep == 0 and room >= 1):
            head = build(dir_parts, room)
            if len(head) <= cap:
                return head
    return "/".join([*prefix, f"long-{h}{leaf_ext}"])


def _unit_stem(file_stem: str) -> str:
    return _clean(safe_segment(safe_segment(file_stem)[:_STEM_CAP])) + ".md"


def mirror_rel_path(source_id: str, rel_path: str, *, file_stem: str = "") -> str:
    """Map a source item path to its docs-repo-relative mirror path.

    WHOLE unit (``file_stem == ""``): ``mirror/<source_id>/<slug dirs>/<mirror_name(name)>``.
    Unit of a multi-unit source: ``mirror/<source_id>/<slug dirs>/<mirror_dir_name(name)>/<slug(stem)>.md``.
    Paths longer than MAX_PATH_CHARS are shortened deterministically (segment truncation + 8-hex hash).

    Every segment is slugged and THEN neutralised (:func:`safe_segment`), so no upstream spelling of
    ``CLAUDE.md``, ``AGENTS.md``, ``.claude/`` or ``.git/`` lands where an agent auto-loads it or git
    skips it.

    Generated paths are capped at MAX_PATH_CHARS - 9 so a later ``disambiguate`` still fits the cap.  A unit
    stem is capped at 60 characters, and whether (and how) a multi-unit item's directory is shortened is
    decided for that 60-character worst case, so all units of one item always share one ``.d`` directory.
    """
    parts = [p for p in rel_path.split("/") if p not in ("", ".")]
    if not parts:
        parts = ["untitled"]
    prefix = ["mirror", source_id]
    dirs = [safe_segment(p) for p in parts[:-1]]
    key = f"{source_id}\0{rel_path}"
    if not file_stem:
        leaf = mirror_name(parts[-1])
        path = "/".join([*prefix, *dirs, leaf])
        return (
            path
            if len(path) <= _GENERATED_CAP
            else _shorten(prefix, dirs, leaf, leaf_ext=".md", reserve=0, key=key)
        )
    leaf = mirror_dir_name(parts[-1])
    head = "/".join([*prefix, *dirs, leaf])
    reserve = 1 + _STEM_CAP + 3  # "/" + the longest stem + ".md"
    if len(head) + reserve > _GENERATED_CAP:
        head = _shorten(prefix, dirs, leaf, leaf_ext=".d", reserve=reserve, key=key)
    return f"{head}/{_unit_stem(file_stem)}"


def disambiguate(path: str, stable_id: str) -> str:
    """Insert ``-<sha256(stable_id)[:8]>`` before the final ``.md`` (used on case-insensitive/NFC
    collisions)."""
    h = _hash8(stable_id)
    return f"{path[:-3]}-{h}.md" if path.endswith(".md") else f"{path}-{h}"


def collision_key(path: str) -> str:
    """Return the key two paths collide on (NFC + casefold), as APFS and NTFS would see them."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())


def is_portable_path(path: str) -> bool:
    """True when every segment is already a fixed point of the slug rules and len(path) <= MAX_PATH_CHARS."""
    if not path or len(path) > MAX_PATH_CHARS:
        return False
    return all(seg != "" and slugify(seg) == seg for seg in path.split("/"))
