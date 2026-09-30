"""Network plumbing: system trust store, proxy resolution, reachability (owner: auth-tls; C15 §5).

Why this module exists (C15 §5, measured): httpx and MSAL's ``requests`` session both verify TLS against
certifi's bundle, not the macOS keychain, so a corporate TLS-inspection root that MDM installs in the System
keychain is not trusted; and nothing in either stack evaluates a PAC/WPAD proxy, while httpx ignores the macOS
``ExceptionsList``.  This module therefore provides:

- :func:`ssl_context` -- a ``truststore.SSLContext`` (verification through the macOS Security framework, so
  keychain roots are trusted).  :func:`inject_system_trust` is for the CLI entry point only (truststore:
  "must not be used by libraries"), and must run before ``msal``/``requests`` are imported.
- :func:`resolve_proxy` -- precedence ``[network] proxy`` (config) > ``HTTPS_PROXY``/``ALL_PROXY`` (env) >
  the macOS *manual* system proxy (``scutil --proxy``).  PAC is unsupported: when ``ProxyAutoConfigEnable``
  is on and no explicit proxy is set, the result carries ``policy_error = "network-policy: PAC ..."`` and
  callers must fail instead of going direct.  ``NO_PROXY`` and the system ``ExceptionsList`` are honoured.
- :func:`httpx_mounts` / :func:`requests_session` -- the resolved proxy and the truststore context, wired into
  httpx (GraphClient) and requests (MSAL's ``http_client``).
- :func:`probe_reachability` -- an HTTPS request to the Graph host *through the resolved proxy*.  Any HTTP
  answer means online; a certificate failure is ``network-policy: TLS`` and a proxy refusal is
  ``network-policy: proxy`` (both *failed*, never *skipped*); only a DNS/connect failure is ``offline``.  A
  TCP probe such as ``nc -z graph.microsoft.com 443`` reads an explicit-proxy network as offline; this one
  does not.

Proxy URLs can carry credentials; they are never logged (:func:`redact_proxy`).
"""

from __future__ import annotations

import logging
import os
import re
import ssl
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import requests
import truststore
from requests.adapters import HTTPAdapter
from urllib3 import proxy_from_url

logger = logging.getLogger(__name__)

REACH_ONLINE = "online"
REACH_OFFLINE = "offline"
POLICY_TLS = "network-policy: TLS"
POLICY_PAC = "network-policy: PAC"
POLICY_PROXY = "network-policy: proxy"

PAC_UNSUPPORTED = (
    "PAC/WPAD proxy auto-configuration is unsupported: agentsync does not evaluate proxy scripts. Set "
    "[network] proxy in sources.toml (the LaunchAgent does not see a shell's HTTPS_PROXY) to the proxy "
    "the PAC file returns "
    'for graph.microsoft.com, or [network] proxy = "direct" if Graph is reachable without one'
)

_DIRECT_WORDS = frozenset({"direct", "none", "off"})
_SCUTIL = "/usr/sbin/scutil"
_SCUTIL_LINE_RE = re.compile(r"^\s*(?P<key>[^\s:]+)\s*:\s*(?P<value>.*?)\s*$")
_CERT_FAILURE_MARKERS = ("CERTIFICATE_VERIFY_FAILED", "certificate verify failed", "SSLCertVerificationError")

ScutilRunner = Callable[[], str | None]


# ---------------------------------------------------------------------------------------------------------
# trust store
# ---------------------------------------------------------------------------------------------------------


def ssl_context() -> ssl.SSLContext:
    """A client TLS context that verifies through the OS trust store (macOS keychain) via truststore."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def inject_system_trust() -> None:
    """Make later ``ssl.SSLContext``s use the OS trust store (CLI entry point only, before msal/requests).

    truststore's own rule: applications may call this, libraries must not; modules that already imported
    ``ssl.SSLContext`` are unaffected, hence "first thing in main()".  Idempotent.
    """
    if not system_trust_injected():
        truststore.inject_into_ssl()
        logger.debug("truststore injected: TLS verification uses the macOS keychain")


def system_trust_injected() -> bool:
    """True once :func:`inject_system_trust` has replaced ``ssl.SSLContext``."""
    return ssl.SSLContext is truststore.SSLContext


# ---------------------------------------------------------------------------------------------------------
# proxy resolution
# ---------------------------------------------------------------------------------------------------------


def redact_proxy(url: str | None) -> str:
    """A proxy URL safe to log: userinfo (credentials) removed; ``direct`` when None."""
    if not url:
        return "direct"
    parts = urlsplit(url)
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@dataclass(frozen=True, slots=True)
class SystemProxy:
    """The macOS network-service proxy settings (``scutil --proxy``), as far as agentsync uses them."""

    https_proxy: str | None = None  # "http://host:port" when HTTPSEnable = 1
    http_proxy: str | None = None  # "http://host:port" when HTTPEnable = 1
    exceptions: tuple[str, ...] = ()  # ExceptionsList
    exclude_simple: bool = False  # ExcludeSimpleHostnames
    pac_enabled: bool = False  # ProxyAutoConfigEnable
    pac_url: str | None = None  # ProxyAutoConfigURLString
    wpad_enabled: bool = False  # ProxyAutoDiscoveryEnable


def parse_scutil_proxy(text: str) -> SystemProxy:
    """Parse ``scutil --proxy`` output (top-level keys and arrays; nested dictionaries are skipped)."""
    scalars: dict[str, str] = {}
    arrays: dict[str, list[str]] = {}
    depth = 0
    current_array: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "}":
            depth -= 1
            if current_array is not None and depth == 1:
                current_array = None
            continue
        m = _SCUTIL_LINE_RE.match(line)
        if line.startswith("<dictionary>") and depth == 0:
            depth = 1
            continue
        if m is None:
            continue
        key, value = m.group("key"), m.group("value")
        if value.endswith("{"):
            depth += 1
            if depth == 2 and value.startswith("<array>"):
                current_array = key
                arrays[key] = []
            continue
        if current_array is not None and depth == 2:
            arrays[current_array].append(value)
        elif depth <= 1:
            scalars[key] = value

    def _on(key: str) -> bool:
        return scalars.get(key, "0").strip() == "1"

    def _hostport(prefix: str) -> str | None:
        host = scalars.get(f"{prefix}Proxy", "").strip()
        if not _on(f"{prefix}Enable") or not host:
            return None
        port = scalars.get(f"{prefix}Port", "").strip()
        return f"http://{host}:{port}" if port.isdigit() else f"http://{host}"

    return SystemProxy(
        https_proxy=_hostport("HTTPS"),
        http_proxy=_hostport("HTTP"),
        exceptions=tuple(arrays.get("ExceptionsList", [])),
        exclude_simple=_on("ExcludeSimpleHostnames"),
        pac_enabled=_on("ProxyAutoConfigEnable"),
        pac_url=scalars.get("ProxyAutoConfigURLString") or None,
        wpad_enabled=_on("ProxyAutoDiscoveryEnable"),
    )


def _run_scutil() -> str | None:
    """``scutil --proxy`` stdout, or None off macOS / on failure (seam for tests)."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run([_SCUTIL, "--proxy"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("scutil --proxy failed: %s", type(exc).__name__)
        return None
    return proc.stdout if proc.returncode == 0 else None


def system_proxy(runner: ScutilRunner | None = None) -> SystemProxy:
    """The macOS system proxy settings (empty off macOS or when scutil fails)."""
    text = (runner or _run_scutil)()
    return parse_scutil_proxy(text) if text else SystemProxy()


def _env(environ: Mapping[str, str], *names: str) -> str | None:
    """First non-empty value among ``names`` (each tried upper- and lower-case)."""
    for name in names:
        for key in (name, name.lower()):
            value = environ.get(key, "").strip()
            if value:
                return value
    return None


def _normalise_proxy_url(url: str) -> str:
    """Add ``http://`` to a bare ``host:port`` proxy value."""
    return url if "://" in url else f"http://{url}"


@dataclass(frozen=True, slots=True)
class ProxySettings:
    """The resolved proxy for HTTPS traffic to Microsoft endpoints."""

    url: str | None = None  # None = direct
    source: str = "none"  # "config" | "env" | "system" | "none"
    no_proxy: tuple[str, ...] = ()  # hosts/suffixes that bypass the proxy ("*" = everything)
    exclude_simple: bool = False  # dotless hostnames bypass the proxy (macOS ExcludeSimpleHostnames)
    pac_url: str | None = None  # the system PAC URL, when one is configured (never evaluated)
    policy_error: str | None = None  # set => callers must fail with this, never go direct
    warnings: tuple[str, ...] = field(default=())  # doctor-visible, one line each

    @classmethod
    def direct(cls) -> ProxySettings:
        """No proxy, no warnings (tests and explicit ``direct``)."""
        return cls()

    def bypasses(self, url: str) -> bool:
        """True when ``url``'s host matches ``no_proxy`` (or is a simple hostname excluded by the system)."""
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if not host:
            return False
        if self.exclude_simple and "." not in host:
            return True
        for raw in self.no_proxy:
            entry = raw.strip().lower()
            if not entry:
                continue
            if entry == "*":
                return True
            if "/" in entry:  # CIDR such as 169.254/16: only literal IPs could match; not evaluated
                continue
            domain = entry.removeprefix("*").lstrip(".")
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def proxy_for(self, url: str) -> str | None:
        """The proxy URL for a request to ``url`` (None = direct)."""
        if self.url is None or self.bypasses(url):
            return None
        return self.url

    def describe(self) -> str:
        """One line for logs and doctor: where the proxy came from, credentials redacted."""
        if self.policy_error:
            return self.policy_error
        return f"{redact_proxy(self.url)} (source: {self.source})"


def resolve_proxy(
    config_proxy: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    system: SystemProxy | None = None,
    scutil: ScutilRunner | None = None,
) -> ProxySettings:
    """Resolve the proxy: config > HTTPS_PROXY/ALL_PROXY > macOS manual system proxy; PAC-only fails closed.

    ``config_proxy`` is ``[network] proxy``: a URL, or ``"direct"``/``"none"`` to force a direct connection
    (which also overrides a PAC configuration).  ``system`` (or ``scutil``) replaces the ``scutil --proxy``
    call in tests.
    """
    env = os.environ if environ is None else environ
    sysp = system if system is not None else system_proxy(scutil)
    env_no_proxy = _env(env, "NO_PROXY")
    no_proxy = tuple(h.strip() for h in (env_no_proxy or "").split(",") if h.strip())
    no_proxy = no_proxy + tuple(e for e in sysp.exceptions if e not in no_proxy)
    warnings: list[str] = []
    if sysp.pac_enabled or sysp.wpad_enabled:
        what = f"PAC {sysp.pac_url}" if sysp.pac_enabled else "WPAD auto-discovery"
        warnings.append(f"system proxy uses {what}; {PAC_UNSUPPORTED}")

    def _make(url: str | None, source: str, policy_error: str | None = None) -> ProxySettings:
        return ProxySettings(
            url=url,
            source=source,
            no_proxy=no_proxy,
            exclude_simple=sysp.exclude_simple,
            pac_url=sysp.pac_url if sysp.pac_enabled else None,
            policy_error=policy_error,
            warnings=tuple(warnings),
        )

    if config_proxy is not None and config_proxy.strip():
        value = config_proxy.strip()
        if value.lower() in _DIRECT_WORDS:
            return _make(None, "config")
        return _make(_normalise_proxy_url(value), "config")
    env_proxy = _env(env, "HTTPS_PROXY", "ALL_PROXY")
    if env_proxy:
        return _make(_normalise_proxy_url(env_proxy), "env")
    if sysp.https_proxy:
        return _make(sysp.https_proxy, "system")
    if sysp.pac_enabled:
        error = (
            f"{POLICY_PAC}: the system proxy is a PAC file ({sysp.pac_url or 'no URL'}) and no explicit "
            f"proxy is set; {PAC_UNSUPPORTED}"
        )
        return _make(None, "none", error)
    return _make(None, "none")


def proxy_diagnostics(settings: ProxySettings) -> tuple[str, ...]:
    """Doctor lines for the resolved proxy: the route, then every warning (PAC/WPAD unsupported)."""
    lines = [f"proxy: {settings.describe()}"]
    lines.extend(settings.warnings)
    return tuple(lines)


# ---------------------------------------------------------------------------------------------------------
# wiring into httpx and requests
# ---------------------------------------------------------------------------------------------------------


def _no_proxy_patterns(settings: ProxySettings) -> list[str]:
    """httpx mount patterns for the ``no_proxy`` entries httpx can express (CIDR ranges are skipped)."""
    patterns: list[str] = []
    for raw in settings.no_proxy:
        entry = raw.strip().lower()
        if not entry or "/" in entry:
            continue
        domain = entry.removeprefix("*").lstrip(".")
        if re.fullmatch(r"[\d.]+", domain) or domain == "localhost" or ":" in domain:
            patterns.append(f"all://{domain}")
        elif domain:
            patterns.append(f"all://*{domain}")
    return sorted(set(patterns))


def httpx_mounts(settings: ProxySettings, ctx: ssl.SSLContext) -> dict[str, httpx.BaseTransport | None]:
    """httpx ``mounts`` routing through the resolved proxy (empty dict = direct for everything).

    ``no_proxy`` entries map to ``None`` (= the client's default, direct transport).
    """
    if settings.url is None or "*" in {e.strip() for e in settings.no_proxy}:
        return {}
    mounts: dict[str, httpx.BaseTransport | None] = {
        "all://": httpx.HTTPTransport(verify=ctx, proxy=httpx.Proxy(settings.url), trust_env=False)
    }
    for pattern in _no_proxy_patterns(settings):
        mounts[pattern] = None
    return mounts


class _TrustStoreAdapter(HTTPAdapter):
    """requests adapter whose pools (direct and proxied) verify with a truststore context."""

    def __init__(self, ctx: ssl.SSLContext) -> None:
        self._ctx = ctx
        super().__init__()

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any
    ) -> None:
        """Direct pools: add the truststore context."""
        pool_kwargs["ssl_context"] = self._ctx
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        """Proxied pools: the TLS inside the CONNECT tunnel uses the truststore context too."""
        if proxy not in self.proxy_manager and not proxy.lower().startswith("socks"):
            self.proxy_manager[proxy] = proxy_from_url(
                proxy,
                proxy_headers=self.proxy_headers(proxy),
                num_pools=self._pool_connections,
                maxsize=self._pool_maxsize,
                block=self._pool_block,
                ssl_context=self._ctx,
                **proxy_kwargs,
            )
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class _TimeoutSession(requests.Session):
    """A requests Session with a default timeout (MSAL applies none when given an ``http_client``)."""

    def __init__(self, timeout_s: float) -> None:
        super().__init__()
        self._default_timeout = timeout_s

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> requests.Response:
        """Session.request with ``timeout`` defaulted."""
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(method, url, *args, **kwargs)


def requests_session(
    settings: ProxySettings, *, target_url: str, ctx: ssl.SSLContext | None = None, timeout_s: float = 30.0
) -> requests.Session:
    """A requests Session for MSAL: truststore TLS, the resolved proxy for ``target_url``, no env lookups.

    ``trust_env`` is off so requests does not re-read proxies from the environment behind our precedence.
    """
    context = ctx if ctx is not None else ssl_context()
    session = _TimeoutSession(timeout_s)
    session.trust_env = False
    adapter = _TrustStoreAdapter(context)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    proxy = settings.proxy_for(target_url)
    session.proxies = {"https": proxy, "http": proxy} if proxy else {}
    return session


# ---------------------------------------------------------------------------------------------------------
# error classification and reachability
# ---------------------------------------------------------------------------------------------------------


def _chain(exc: BaseException) -> list[BaseException]:
    """``exc`` and its causes/contexts (cycle-safe)."""
    out: list[BaseException] = []
    node: BaseException | None = exc
    while node is not None and node not in out:
        out.append(node)
        node = node.__cause__ or node.__context__
    return out


def is_certificate_failure(exc: BaseException) -> bool:
    """True when TLS verification failed anywhere in the exception chain (untrusted/inspected root)."""
    for node in _chain(exc):
        if isinstance(node, ssl.SSLCertVerificationError):
            return True
        text = f"{type(node).__name__}: {node}"
        if any(marker in text for marker in _CERT_FAILURE_MARKERS):
            return True
    return False


def classify_transport_error(exc: BaseException) -> str | None:
    """``POLICY_TLS`` / ``POLICY_PROXY`` for deterministic network-policy failures, else None (transient)."""
    if is_certificate_failure(exc):
        return POLICY_TLS
    for node in _chain(exc):
        if isinstance(node, httpx.ProxyError | requests.exceptions.ProxyError):
            return POLICY_PROXY
    return None


TLS_HINT = (
    "the server certificate is not trusted: a TLS-inspecting proxy's root CA must be in the macOS System "
    "keychain (MDM), or the Microsoft login/Graph hosts excluded from break-and-inspect"
)
PROXY_HINT = (
    "the proxy refused or failed the CONNECT to the Microsoft endpoint (proxy authentication or policy)"
)


@dataclass(frozen=True, slots=True)
class Reachability:
    """Outcome of :func:`probe_reachability`."""

    state: str  # REACH_ONLINE | REACH_OFFLINE | POLICY_TLS | POLICY_PAC | POLICY_PROXY
    detail: str
    via: str  # "direct" or the redacted proxy URL
    status: int | None = None  # HTTP status when online

    @property
    def online(self) -> bool:
        """The endpoint answered over HTTPS."""
        return self.state == REACH_ONLINE

    @property
    def skipped(self) -> bool:
        """No network: the Graph arm is ``skipped`` this cycle (not failed)."""
        return self.state == REACH_OFFLINE

    @property
    def failed(self) -> bool:
        """A network policy blocks us: ``failed: network-policy (...)``, never ``skipped``."""
        return self.state.startswith("network-policy")


def probe_reachability(
    url: str,
    settings: ProxySettings,
    *,
    timeout_s: float = 10.0,
    transport: httpx.BaseTransport | None = None,
    ctx: ssl.SSLContext | None = None,
) -> Reachability:
    """One HTTPS HEAD to ``url`` through the resolved proxy with truststore TLS; classify the outcome.

    Any HTTP status (401 included) = online; 407 from the proxy or a failed CONNECT = ``network-policy:
    proxy``; a certificate failure = ``network-policy: TLS``; a PAC-only system proxy = ``network-policy:
    PAC`` without sending anything; DNS/connect/timeout failures = offline.
    """
    via = redact_proxy(settings.proxy_for(url))
    if settings.policy_error:
        return Reachability(POLICY_PAC, settings.policy_error, via)
    context = ctx if ctx is not None else ssl_context()
    if transport is not None:
        client = httpx.Client(transport=transport, timeout=timeout_s, trust_env=False)
    else:
        client = httpx.Client(
            verify=context, timeout=timeout_s, trust_env=False, mounts=httpx_mounts(settings, context) or None
        )
    try:
        with client:
            response = client.head(url)
    except httpx.TransportError as exc:
        policy = classify_transport_error(exc)
        if policy == POLICY_TLS:
            return Reachability(POLICY_TLS, f"{TLS_HINT} ({type(exc).__name__})", via)
        if policy == POLICY_PROXY:
            return Reachability(POLICY_PROXY, f"{PROXY_HINT} ({type(exc).__name__}: {exc})", via)
        return Reachability(
            REACH_OFFLINE, f"{type(exc).__name__} reaching {redact_proxy(url)} via {via}", via
        )
    if response.status_code == 407:
        return Reachability(POLICY_PROXY, f"{PROXY_HINT} (HTTP 407)", via, 407)
    return Reachability(REACH_ONLINE, f"HTTP {response.status_code} via {via}", via, response.status_code)
