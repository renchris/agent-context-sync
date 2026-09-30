"""``agentsync it-request``: the IT request (docs/deploy/it-request.md) as a ready-to-send email draft.

The page's placeholder table is the specification. Its "Where the value comes from" column says, per
placeholder, whether a command gives the value (the cell starts with a backtick), the person fills it ("you
fill in"), IT fills it ("IT fills in") or it is part of a format ("leave them"). :data:`FILLERS` fills exactly
the command rows from this Mac; tests/test_it_request.py keeps the two in step. The draft is what follows the
page's ``---`` line, with:

* "You fill: ..." (the person's open fields) as its very first line, then the IT fields and what was filled;
* ``To:`` and ``Subject:`` lines from the page's ``**To:** ... **Subject:** ...`` line;
* every relative link made absolute (https://github.com/renchris/agent-context-sync/blob/main/...);
* the manifest block replaced by ``entra-app.json`` with the same values filled (JSON-escaped), and the page's
  "byte-identical" claim, which a filled copy no longer meets, reworded;
* the sections addressed to the operator (a heading ending in "(operator)") left out.

Nothing is sent, and nothing is written but ``--out`` (0600, atomically; a different earlier draft is kept as
``<out>.<stamp>.bak``). The template comes from the agentsync source checkout this package was built from
when it is on disk (an editable install, or ``uv tool install <checkout>`` as scripts/install.sh does), else
from the copy shipped inside the wheel (``agentsync/_deploy/``, pyproject's hatch ``force-include``).
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import posixpath
import pwd
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import metadata, resources
from pathlib import Path
from urllib.parse import unquote, urlparse

from agentsync.paths import expand

REPO_URL = "https://github.com/renchris/agent-context-sync"
BLOB_BASE = f"{REPO_URL}/blob/main/"
"""Relative links in the page become ``BLOB_BASE + <repo path>`` (GitHub redirects a folder's blob URL)."""
PAGE_REL = "docs/deploy/it-request.md"
ENTRA_REL = "docs/deploy/entra-app.json"
PACKAGE_DATA_DIR = "_deploy"
"""``agentsync/_deploy/{it-request.md,entra-app.json}`` inside the wheel (pyproject hatch force-include)."""
DEFAULT_OUT = "~/agent-context/it-request-draft.md"
YOU_FILL = "you fill in"
IT_FILLS = "IT fills in"
LEAVE = "leave them"
COMMAND_TIMEOUT_S = 20.0

_PLACEHOLDER_RE = re.compile(r"<[a-z][a-z0-9-]*>")
_ROW_RE = re.compile(r"^\| (`<.+?) \| (.+?) \| (.+?) \|$", re.MULTILINE)
_TO_RE = re.compile(r"^\*\*To:\*\* (?P<to>.+?)\. \*\*Subject:\*\* (?P<subject>.+?)\.?$", re.MULTILINE)
_LINK_RE = re.compile(r"(?<!!)(\[[^\]\n]*\])\(([^)\s]+)\)")
_FENCE_RE = re.compile(r"^(```|~~~)[^\n]*\n.*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)
_JSON_FENCE_RE = re.compile(r"^```json\n.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)
_BYTE_IDENTICAL_RE = re.compile(r"The manifest, byte-identical to (\[`entra-app\.json`\]\([^)]*\))\.")
_SERIAL_RE = re.compile(r"^\s*Serial Number(?: \(system\))?:\s*(\S+)\s*$", re.MULTILINE)

Runner = Callable[[Sequence[str]], str | None]
"""Runs a read-only command and returns its stdout, or None when it failed or timed out."""


class TemplateError(Exception):
    """The IT request page or entra-app.json could not be found or does not have the expected shape."""


def run_command(argv: Sequence[str]) -> str | None:
    """stdout of ``argv`` (no shell, no stdin, :data:`COMMAND_TIMEOUT_S`), or None on any failure."""
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


# ---------------------------------------------------------------------------------------------------------
# the template: the source checkout, else the wheel's copy
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Template:
    """The page and the manifest, and where they were read from."""

    page: str
    entra: str
    origin: str


def _is_checkout(root: Path) -> bool:
    return (
        (root / PAGE_REL).is_file() and (root / ENTRA_REL).is_file() and (root / "pyproject.toml").is_file()
    )


def checkout_candidates() -> list[Path]:
    """Source checkouts that may hold the page, in order: the tree this module runs from (an editable or
    in-repo run), then the local directory the installed distribution was built from (PEP 610
    ``direct_url.json``, which ``uv tool install <checkout>`` records)."""
    out: list[Path] = [Path(__file__).resolve().parents[2]]
    with contextlib.suppress(Exception):
        raw = metadata.distribution("agentsync").read_text("direct_url.json")
        if raw:
            url = urlparse(str(json.loads(raw).get("url", "")))
            if url.scheme == "file":
                out.append(Path(unquote(url.path)))
    return list(dict.fromkeys(out))


def source_checkout() -> Path | None:
    """The first of :func:`checkout_candidates` that holds the page and entra-app.json, else None."""
    return next((c for c in checkout_candidates() if _is_checkout(c)), None)


def load_template(checkouts: Iterable[Path] | None = None) -> Template:
    """The page and entra-app.json from the first source checkout that has both, else from the copy packaged
    in the wheel. Raises :class:`TemplateError` when neither exists."""
    for root in checkout_candidates() if checkouts is None else checkouts:
        if _is_checkout(root):
            return Template(
                (root / PAGE_REL).read_text(encoding="utf-8"),
                (root / ENTRA_REL).read_text(encoding="utf-8"),
                str(root / PAGE_REL),
            )
    data = resources.files("agentsync").joinpath(PACKAGE_DATA_DIR)
    try:
        page = data.joinpath("it-request.md").read_text(encoding="utf-8")
        entra = data.joinpath("entra-app.json").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise TemplateError(
            f"cannot find {PAGE_REL}: no agentsync source checkout and no packaged copy "
            f"(agentsync/{PACKAGE_DATA_DIR}/it-request.md): {exc}"
        ) from None
    return Template(page, entra, f"the copy packaged with agentsync (agentsync/{PACKAGE_DATA_DIR})")


# ---------------------------------------------------------------------------------------------------------
# the placeholder table
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Placeholder:
    """One placeholder of the page's top table."""

    name: str
    meaning: str
    source: str

    @property
    def by_command(self) -> bool:
        """The table says a command gives the value (the source cell starts with a code span)."""
        return self.source.startswith("`")

    @property
    def by_person(self) -> bool:
        """The person asking fills it in."""
        return self.source.startswith(YOU_FILL)

    @property
    def by_it(self) -> bool:
        """IT fills it in (the reply carries it)."""
        return self.source.startswith(IT_FILLS)


def placeholder_table(page: str) -> list[Placeholder]:
    """The rows of the table before the page's ``---`` line, one :class:`Placeholder` per name (a row may
    name several)."""
    top = page.split("\n---\n", 1)[0]
    out: list[Placeholder] = []
    for names, meaning, source in _ROW_RE.findall(top):
        out += [Placeholder(n, meaning.strip(), source.strip()) for n in re.findall(r"`(<[^`]+>)`", names)]
    if not out:
        raise TemplateError(f"{PAGE_REL}: no placeholder table before the '---' line")
    return out


# ---------------------------------------------------------------------------------------------------------
# this Mac's values
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MacFacts:
    """What this Mac can say for the command rows of the table (None: not found, the person fills it)."""

    requester_name: str | None = None
    serial: str | None = None
    arch: str | None = None
    orgs: tuple[str, ...] = ()
    arms: str | None = None
    today: date = field(default_factory=date.today)

    @property
    def org(self) -> str | None:
        """The organisation when exactly one was found (two or more: the person chooses)."""
        return self.orgs[0] if len(self.orgs) == 1 else None


def _full_name(run: Runner) -> str | None:
    out = (run(["id", "-F"]) or "").strip()
    if out:
        return out
    with contextlib.suppress(KeyError, OSError):
        gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",", 1)[0].strip()
        return gecos or None
    return None


def _serial(run: Runner) -> str | None:
    m = _SERIAL_RE.search(run(["system_profiler", "SPHardwareDataType"]) or "")
    return m.group(1) if m else None


def org_of(provider: str) -> str | None:
    """``OneDrive-Contoso`` / ``OneDrive-SharedLibraries-Contoso`` -> ``Contoso``; ``Personal`` and anything
    else -> None."""
    for prefix in ("OneDrive-SharedLibraries-", "OneDrive-"):
        if provider.startswith(prefix):
            org = provider[len(prefix) :].strip()
            return None if org in ("", "Personal") else org
    return None


def find_orgs(cloud_root: Path, source_paths: Iterable[Path] = ()) -> tuple[str, ...]:
    """Distinct organisations, those of the configured sources' paths first, then those of the provider
    folders in ``cloud_root`` (names only; no provider's files are listed)."""
    found: list[str] = []
    roots = [cloud_root]
    with contextlib.suppress(OSError):
        roots.append(cloud_root.resolve())
    for path in source_paths:
        for root in roots:
            with contextlib.suppress(ValueError):
                parts = expand(path).relative_to(root).parts
                if parts and (org := org_of(parts[0])):
                    found.append(org)
                    break
    if not found:
        with contextlib.suppress(OSError):
            found += [o for o in (org_of(p.name) for p in sorted(cloud_root.iterdir())) if o]
    return tuple(dict.fromkeys(found))


def describe_kinds(kinds: Iterable[str]) -> str:
    """``["local", "local", "inbox"]`` -> ``"local: 2 sources, inbox: 1 source"``; none -> a sentence."""
    counts = Counter(kinds)
    if not counts:
        return "no sources configured yet"
    return ", ".join(f"{k}: {n} source{'s' if n != 1 else ''}" for k, n in counts.items())


def gather_facts(
    *,
    kinds: Iterable[str] | None,
    source_paths: Iterable[Path] = (),
    cloud_root: Path | None = None,
    run: Runner | None = None,
    today: date | None = None,
) -> MacFacts:
    """This Mac's values, read-only: ``id -F``, ``system_profiler SPHardwareDataType``, ``uname -m`` (the
    interpreter's machine), the ``OneDrive-<org>`` folder names and the sources' kinds (``kinds`` None: the
    configuration could not be read, so the value stays open; ``run`` defaults to :func:`run_command`)."""
    root = cloud_root if cloud_root is not None else Path.home() / "Library" / "CloudStorage"
    run = run or run_command
    return MacFacts(
        requester_name=_full_name(run),
        serial=_serial(run),
        arch=platform.machine() or None,
        orgs=find_orgs(root, source_paths),
        arms=describe_kinds(kinds) if kinds is not None else None,
        today=today or date.today(),
    )


FILLERS: dict[str, Callable[[MacFacts], str | None]] = {
    "<requester-name>": lambda f: f.requester_name,
    "<serial>": lambda f: f.serial,
    "<arch>": lambda f: f.arch,
    "<org>": lambda f: f.org,
    "<arms-today>": lambda f: f.arms,
    "<date>": lambda f: f.today.isoformat(),
}
"""One filler per table row whose source is a command (tests/test_it_request.py checks the two sets match)."""


# ---------------------------------------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Draft:
    """The rendered draft and what is still open in it."""

    text: str
    you_fill: list[str]
    it_fills: list[str]
    filled: dict[str, str]
    notes: list[str]


def _absolute(target: str, page_dir: str) -> str:
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, flags=re.IGNORECASE):
        return target  # https:, mailto: …
    rel, sep, anchor = target.partition("#")
    path = posixpath.normpath(posixpath.join(page_dir, rel)) if rel else f"{page_dir}/it-request.md"
    return f"{BLOB_BASE}{path}{sep}{anchor}"


def absolute_links(text: str, page_rel: str = PAGE_REL) -> str:
    """Every relative Markdown link (outside code fences) as an absolute URL on the repository's main
    branch; ``#anchor`` alone points at the page itself."""
    page_dir = posixpath.dirname(page_rel)

    def links(chunk: str) -> str:
        return _LINK_RE.sub(lambda m: f"{m.group(1)}({_absolute(m.group(2), page_dir)})", chunk)

    return _outside_fences(text, links)


def _outside_fences(text: str, fn: Callable[[str], str]) -> str:
    out: list[str] = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        out += [fn(text[pos : m.start()]), m.group(0)]
        pos = m.end()
    out.append(fn(text[pos:]))
    return "".join(out)


def _fill(text: str, values: dict[str, str], *, escape: Callable[[str], str] = str) -> str:
    for name, value in values.items():
        text = text.replace(f"`{name}`", escape(value)).replace(name, escape(value))
    return text


def _drop_operator_sections(body: str) -> str:
    parts = re.split(r"(?m)^(?=## )", body)
    return "".join(p for p in parts if not re.match(r"## .*\(operator\)\s*$", p.split("\n", 1)[0]))


def render(template: Template, facts: MacFacts) -> Draft:
    """The email draft for this Mac (see the module docstring)."""
    table = placeholder_table(template.page)
    if "\n---\n" not in template.page:
        raise TemplateError(f"{PAGE_REL}: no '---' line between the placeholder table and the request")
    body = template.page.split("\n---\n", 1)[1].lstrip("\n")
    m = _TO_RE.search(body)
    if m is None:
        raise TemplateError(f"{PAGE_REL}: no '**To:** ... **Subject:** ...' line")
    to, subject = m.group("to").strip(), m.group("subject").strip()
    body = (body[: m.start()] + body[m.end() :]).lstrip("\n")
    body = _drop_operator_sections(body)

    filled: dict[str, str] = {}
    for p in table:
        if p.by_command and p.name in FILLERS and (value := FILLERS[p.name](facts)):
            filled[p.name] = value
    entra = _fill(template.entra, filled, escape=lambda v: json.dumps(v)[1:-1])
    body = _JSON_FENCE_RE.sub(lambda _m: f"```json\n{entra.rstrip(chr(10))}\n```", body, count=1)
    body = _BYTE_IDENTICAL_RE.sub(
        r"The manifest: \1 with this request's values filled in (save the block as `entra-app.json` for the"
        r" `az` command).",
        body,
    )
    body = absolute_links(body)
    to_line, subject_line = _fill(to, filled), _fill(subject, filled)
    body = _fill(body, filled)

    left = set(_PLACEHOLDER_RE.findall(to_line + body))
    you_fill = [p.name for p in table if p.name in left and not p.by_it and not p.source.startswith(LEAVE)]
    it_fills = [p.name for p in table if p.name in left and p.by_it]
    notes: list[str] = []
    if len(facts.orgs) > 1 and "<org>" in you_fill:
        notes.append(f"<org>: this Mac syncs more than one organisation ({', '.join(facts.orgs)}); pick one")
    head = [
        f"You fill: {', '.join(you_fill) if you_fill else 'nothing'}",
        f"IT fills (leave them as they are): {', '.join(it_fills) if it_fills else 'nothing'}",
        "Filled from this Mac: "
        + (", ".join(f"{k.strip('<>')} = {v}" for k, v in filled.items()) if filled else "nothing"),
        *notes,
        "This is a draft: nothing has been sent. Fill the fields above, then send everything below the line "
        "yourself.",
        f"When IT replies: {BLOB_BASE}{PAGE_REL}#after-it-replies-operator",
        "",
        "---",
        "",
        f"To: {to_line.replace('`', '')}",
        f"Subject: {subject_line.replace('`', '')}",
        "",
    ]
    return Draft("\n".join(head) + "\n" + body.rstrip("\n") + "\n", you_fill, it_fills, filled, notes)


# ---------------------------------------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------------------------------------


def refused_location(out: Path, roots: Iterable[Path]) -> Path | None:
    """The first of ``roots`` that ``out`` is inside (compared canonically, so a symlinked parent cannot slip
    past), else None."""
    target = expand(out)
    parent = target.parent
    with contextlib.suppress(OSError):
        parent = parent.resolve()
    canon = parent / target.name
    for root in roots:
        r = expand(root)
        with contextlib.suppress(OSError):
            r = r.resolve()
        if canon == r or r in canon.parents:
            return root
    return None


def write_draft(path: Path, text: str, *, now: datetime | None = None) -> Path | None:
    """Write ``text`` to ``path`` atomically with mode 0600 (creating the parent 0700). An existing file with
    different content is first kept as ``<path>.<YYYYmmddTHHMMSS>.bak`` and that path returned; an identical
    one is left as it is. Raises OSError."""
    path = expand(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup: Path | None = None
    with contextlib.suppress(FileNotFoundError):
        if path.read_text(encoding="utf-8") == text:
            path.chmod(0o600)
            return None
        stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        path.replace(backup)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).chmod(0o600)
        Path(tmp).replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return backup
