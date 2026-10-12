"""Check that docs/deploy/it-request.md still names every host the installer's downloads reach.

The IT request's "Installation downloads" bullet tells IT which hosts to allow. The test that holds it
(tests/test_deploy_pack.py) reads only repository text, so when a vendor moves its downloads (GitHub moved its
release files from objects.githubusercontent.com to release-assets.githubusercontent.com) the page and the test
agree and stay green. This script follows the real redirect chains instead, with HEAD requests and no
download: the speech build's release file must end on the host the page names for release files, and every
hop of uv's installer, uv itself, the Python 3.11 uv installs and a PyPI file must be a host the bullet names
(B13 round 2 R119).

It reads the network, so it is not part of the gate: .github/workflows/download-hosts.yml runs it weekly and
on demand, where a failure shows red on the Actions page and blocks nothing.

    python3 -I scripts/download_hosts.py           # follow every chain; exit 1 on a host the page lacks
    python3 -I scripts/download_hosts.py --plan    # print the URLs it would follow, offline
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IT_REQUEST = ROOT / "docs" / "deploy" / "it-request.md"
INSTALL_SH = ROOT / "scripts" / "install.sh"
UV_LOCK = ROOT / "uv.lock"
UV_ASSET = (
    "uv-aarch64-apple-darwin.tar.gz"  # the Mac uv binary the installer script fetches
)
MAX_HOPS = 10
REDIRECTS = ("301", "302", "303", "307", "308")
REFUSES_HEAD = ("403", "405", "501")


def downloads_bullet(text: str) -> str:
    """The "Installation downloads" bullet of it-request.md's "What leaves the Mac" section, on one line."""
    section = text.split("\n## What leaves the Mac", 1)[1].split("\n## ", 1)[0]
    bullet = section.split("- **Installation downloads:**", 1)[1].split("\n- **", 1)[0]
    return " ".join(bullet.split())


def named_hosts(bullet: str) -> set[str]:
    """The host names the bullet gives in backticks (a path, a commit or a file name is not one)."""
    return set(re.findall(r"`([a-z0-9-]+(?:\.[a-z0-9-]+)+)`", bullet))


def release_file(bullet: str) -> tuple[str, str]:
    """The speech build's prebuilt release file as a github.com URL, and the host the bullet says GitHub
    serves release files from."""
    m = re.search(
        r"`([\w.-]+\.zip)` from the `([\w.-]+/[\w.-]+)` release (v[\d.]+)", bullet
    )
    host = re.search(r"GitHub serves release files from `([a-z0-9.-]+)`", bullet)
    if m is None or host is None:
        raise ValueError(
            "it-request.md no longer names the speech build's release file and its host"
        )
    archive, repo, tag = m.groups()
    return f"https://github.com/{repo}/releases/download/{tag}/{archive}", host.group(1)


def uv_installer_url(script: str) -> str:
    """``UV_INSTALLER_URL`` as scripts/install.sh sets it."""
    m = re.search(r'^UV_INSTALLER_URL="(https://[^"]+)"', script, re.MULTILINE)
    if m is None:
        raise ValueError("scripts/install.sh sets no UV_INSTALLER_URL")
    return m.group(1)


def uv_binary_urls(installer: str) -> list[str]:
    """The URLs uv's installer script downloads the Mac binary from by default (no ``UV_DOWNLOAD_URL`` or
    other override set), in the order it tries them."""
    m = re.search(r'ARTIFACT_DOWNLOAD_URLS="(https://[^"]+)"', installer)
    if m is None:
        raise ValueError("uv's installer script names no ARTIFACT_DOWNLOAD_URLS")
    return [f"{base.rstrip('/')}/{UV_ASSET}" for base in m.group(1).split()]


def pypi_urls(lock: str) -> list[str]:
    """PyPI's index and one file uv.lock pins, so both of the bullet's PyPI hosts are followed."""
    files = re.findall(r'url = "(https://files\.pythonhosted\.org/[^"]+)"', lock)
    return ["https://pypi.org/simple/pip/", *files[:1]]


def python_url() -> str | None:
    """The Python 3.11 download uv picks for an Apple silicon Mac, or None without uv."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    out = subprocess.run(
        [uv, "python", "list", "--show-urls", "--all-platforms"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    m = re.search(
        r"^cpython-3\.11\.\d+-macos-aarch64-none\s+(https://\S+)", out, re.MULTILINE
    )
    return m.group(1) if m else None


def _curl(*args: str) -> str:
    """curl's output (curl, not urllib: a python.org Python on a Mac has no CA bundle of its own). A failure
    is a RuntimeError that carries curl's own message."""
    done = subprocess.run(
        ["curl", "-sS", "--max-time", "60", "-A", "agentsync-download-hosts", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"curl exit {done.returncode}: {done.stderr.strip() or 'no message'}"
        )
    return done.stdout


def hops(url: str) -> list[str]:
    """Every URL a HEAD request to ``url`` passes through, itself first and the one that answered last. Each
    hop is followed by hand, so no request downloads a file."""
    seen = [url]
    for _ in range(MAX_HOPS):
        out = ("-o", "/dev/null", "-w", "%{http_code} %{redirect_url}", seen[-1])
        code, _sep, location = _curl("-I", *out).partition(" ")
        if (
            code in REFUSES_HEAD
        ):  # a server that will not answer HEAD: ask for one byte instead
            code, _sep, location = _curl("-r", "0-0", *out).partition(" ")
        if code not in REDIRECTS or not location.strip():
            if not code.startswith("2"):
                raise RuntimeError(f"HTTP {code} from {seen[-1]}")
            return seen
        seen.append(urllib.parse.urljoin(seen[-1], location.strip()))
    raise RuntimeError(f"more than {MAX_HOPS} redirects from {url}")


def host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


def plan(
    fetch: Callable[[str], str] | None = None,
) -> list[tuple[str, str, str | None]]:
    """The URLs to follow: (what, url, the host the chain must end on, or None when any named host will do).
    ``fetch`` reads uv's installer script; without it the uv binary URLs are left out (``--plan``)."""
    bullet = downloads_bullet(IT_REQUEST.read_text(encoding="utf-8"))
    release, release_host = release_file(bullet)
    installer = uv_installer_url(INSTALL_SH.read_text(encoding="utf-8"))
    out: list[tuple[str, str, str | None]] = [
        ("speech build release file", release, release_host),
        ("uv installer", installer, None),
    ]
    if fetch is not None:
        out += [("uv binary", url, None) for url in uv_binary_urls(fetch(installer))]
    python = python_url()
    if python is not None:
        out.append(("Python 3.11", python, None))
    out += [
        ("PyPI", url, None) for url in pypi_urls(UV_LOCK.read_text(encoding="utf-8"))
    ]
    return out


def check(
    steps: list[tuple[str, str, str | None]],
    named: set[str],
    follow: Callable[[str], list[str]] = hops,
) -> list[str]:
    """One problem line per chain that ends off its host or passes a host ``named`` lacks."""
    problems = []
    for what, url, end in steps:
        try:
            chain = follow(url)
        except (
            RuntimeError,
            OSError,
        ) as exc:  # one chain's failure leaves the others checked
            problems.append(f"{what} could not be followed ({exc}): {url}")
            continue
        print(f"{what}: {' -> '.join(host(u) for u in chain)}")
        if end is not None and host(chain[-1]) != end:
            problems.append(
                f"{what} now ends on {host(chain[-1])}, and it-request.md names {end}: {url}"
            )
        missing = sorted({host(u) for u in chain} - named)
        if missing:
            problems.append(
                f"{what} passes {', '.join(missing)}, which it-request.md does not name: {url}"
            )
    return problems


def _get(url: str) -> str:
    return _curl("-fL", url)


def main(argv: list[str]) -> int:
    if argv == ["--plan"]:
        for what, url, end in plan():
            print(f"{what}: {url}" + (f" (must end on {end})" if end else ""))
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    named = named_hosts(downloads_bullet(IT_REQUEST.read_text(encoding="utf-8")))
    steps = plan(_get)
    problems = check(steps, named)
    if not any(what == "Python 3.11" for what, _url, _end in steps):
        problems.append(
            "uv is not on PATH or lists no Python 3.11 for macOS aarch64: its host is unchecked"
        )
    for line in problems:
        print(f"PROBLEM: {line}", file=sys.stderr)
    print(f"named hosts: {', '.join(sorted(named))}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
