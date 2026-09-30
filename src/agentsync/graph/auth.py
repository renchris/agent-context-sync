"""MSAL public-client sign-in with a Keychain-backed token cache (owner: graph-core / auth-tls; C15 §1, §5).

Sign-in ladder (:meth:`MsalAuth.login`, C15 §1.1-1.4), tried in this order:

1. **Broker** -- ``PublicClientApplication(..., enable_broker_on_mac=True)`` and
   ``acquire_token_interactive(parent_window_handle=CONSOLE_WINDOW_HANDLE)``: the Microsoft Enterprise SSO
   extension (Company Portal) presents the Entra device identity, the only route that satisfies a
   compliant-device Conditional Access policy.  Skipped, with the reason logged, when ``pymsalruntime`` is
   missing (``ImportError``), the SSO extension is inactive (``RuntimeError`` at import -- measured on an
   unmanaged Mac), the process is not arm64, or MSAL disables it.
2. **Loopback** -- interactive auth code + PKCE (MSAL applies S256) in the system browser, redirect
   ``http://localhost:<port>``.
3. **Device code** -- only when ``[graph] allow_device_code = true``; Conditional Access cannot evaluate
   device state on it and many tenants block it.

A rung falls through only when it could not run (broker unavailable, redirect URI not registered for that
rung, no browser/timeout); an answer from Entra that is a tenant decision (consent, assignment, device,
policy) stops the ladder with a classified error (C15 §1.5): ``AuthRequiredError`` for ``reauth-required``,
``AuthBlockedError(state=...)`` for ``blocked: device|policy|consent|assignment`` and ``config-invalid``.
None of them is retried.  Background runs only ever call :meth:`MsalAuth.get_token` (silent).

Authority: ``<login host>/<tenant id>``; ``organizations``/``common``/``consumers`` are refused (a
single-tenant registration answers AADSTS50194).  The login host, Graph root and scope resource follow the
cloud (global, US Gov GCC High / DoD, China 21Vianet), taken from ``[graph] cloud`` or inferred from
``[graph] base_url``.  MSAL's HTTP goes through :func:`agentsync.net.requests_session`: truststore TLS (macOS
keychain roots) and the resolved proxy; a PAC-only system proxy fails closed.

Token custody: msal-extensions ``KeychainPersistence`` (service ``agentsync``, account
``msal_token_cache:<tenant>:<client_id>``).  Only if the Keychain is unavailable (e.g. no GUI session) does it
fall back to a mode-0600 file under the state dir, logged at WARNING.  Tokens are never logged, printed, or
written under docs/.  The account's ``account_source`` (broker / authorization_code / device code) is kept
by MSAL in the cache and reported as :attr:`AuthStatus.sign_in_method`; every acquisition's MSAL
``token_source`` is exposed as :attr:`MsalAuth.last_token_source` so a silent fall-back away from the broker
is visible.

MSAL's ``PublicClientApplication`` performs OpenID discovery (network) when it is constructed, so it is built
lazily: :meth:`MsalAuth.status` and :meth:`MsalAuth.logout` read and edit the persisted cache without any
network access, and :meth:`MsalAuth.get_token` answers "no account" (``AuthRequiredError``) offline too.
"""

from __future__ import annotations

import importlib
import logging
import platform
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import msal
from msal_extensions import FilePersistence, KeychainPersistence, PersistedTokenCache
from msal_extensions.persistence import PersistenceNotFound

from agentsync import __version__, net
from agentsync.config import Config
from agentsync.errors import AuthError, AuthRequiredError, ConfigError
from agentsync.graph import errors as _gerr
from agentsync.graph.errors import AuthBlockedError
from agentsync.paths import is_cloud_path

logger = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "agentsync"
KEYCHAIN_ACCOUNT = "msal_token_cache"

REAUTH_ERROR_CODES: tuple[str, ...] = (
    "invalid_grant",
    "interaction_required",
    "AADSTS50076",
    "AADSTS50173",
    "AADSTS700082",
    "AADSTS50078",
    "AADSTS53003",
)
"""MSAL errors that mean a human must sign in again: map to AuthRequiredError, never retry."""

BROKER_REDIRECT_URI = "msauth.com.msauth.unsignedapp://auth"
"""Redirect URI MSAL hard-codes for the macOS broker (``msal/broker.py``); the registration must list it."""
LOOPBACK_REDIRECT_URI = "http://localhost"
"""Redirect URI of the loopback auth-code + PKCE rung (MSAL adds the port; Entra ignores it for localhost)."""

SIGN_IN_BROKER = "broker"
SIGN_IN_LOOPBACK = "loopback"
SIGN_IN_DEVICE_CODE = "device-code"

MULTI_TENANT_AUTHORITIES = frozenset({"organizations", "common", "consumers"})
"""Tenant values refused for a single-tenant app registration (AADSTS50194)."""


@dataclass(frozen=True, slots=True)
class CloudEndpoints:
    """One Microsoft cloud: its Entra login host and Microsoft Graph root (tokens are not interchangeable)."""

    name: str
    login_host: str
    graph_root: str


CLOUDS: Mapping[str, CloudEndpoints] = {
    "global": CloudEndpoints("global", "https://login.microsoftonline.com", "https://graph.microsoft.com"),
    "usgov": CloudEndpoints("usgov", "https://login.microsoftonline.us", "https://graph.microsoft.us"),
    "usgov-dod": CloudEndpoints(
        "usgov-dod", "https://login.microsoftonline.us", "https://dod-graph.microsoft.us"
    ),
    "china": CloudEndpoints(
        "china", "https://login.chinacloudapi.cn", "https://microsoftgraph.chinacloudapi.cn"
    ),
}
"""learn.microsoft.com/graph/deployments; M365 GCC (moderate) uses ``global``; GCC High ``usgov``; DoD
``usgov-dod``; 21Vianet ``china``."""

_BACKEND_KEYCHAIN = "keychain"
_BACKEND_FILE = "file-0600"

# MSAL adds these itself and raises ValueError if a caller passes them explicitly.
_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})

_AADSTS_RE = re.compile(r"AADSTS\d+")

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_SIGN_IN_BY_ACCOUNT_SOURCE: Mapping[str, str] = {
    "broker": SIGN_IN_BROKER,
    "authorization_code": SIGN_IN_LOOPBACK,
    _DEVICE_GRANT: SIGN_IN_DEVICE_CODE,
    "device_code": SIGN_IN_DEVICE_CODE,
}

AADSTS_STATES: Mapping[str, str] = {
    "AADSTS50076": _gerr.STATE_REAUTH,
    "AADSTS50078": _gerr.STATE_REAUTH,
    "AADSTS50079": _gerr.STATE_REAUTH,
    "AADSTS70043": _gerr.STATE_REAUTH,
    "AADSTS50173": _gerr.STATE_REAUTH,
    "AADSTS700082": _gerr.STATE_REAUTH,
    "AADSTS50097": _gerr.STATE_DEVICE,
    "AADSTS53000": _gerr.STATE_DEVICE,
    "AADSTS53001": _gerr.STATE_DEVICE,
    "AADSTS530003": _gerr.STATE_DEVICE,
    "AADSTS135011": _gerr.STATE_DEVICE,
    "AADSTS53003": _gerr.STATE_POLICY,
    "AADSTS65001": _gerr.STATE_CONSENT,
    "AADSTS90094": _gerr.STATE_CONSENT,
    "AADSTS90095": _gerr.STATE_CONSENT,
    "AADSTS65005": _gerr.STATE_CONSENT,
    "AADSTS50105": _gerr.STATE_ASSIGNMENT,
    "AADSTS50011": _gerr.STATE_CONFIG,
    "AADSTS50194": _gerr.STATE_CONFIG,
    "AADSTS700016": _gerr.STATE_CONFIG,
    "AADSTS7000218": _gerr.STATE_CONFIG,
}
"""AADSTS code -> pipeline state (C15 §1.5 table, plus codes the implementation also meets). Never retried."""

_BROKER_HOW = (
    "sign in with `agentsync graph login` on an Intune-enrolled Apple Silicon Mac with Company Portal "
    "installed, so the Microsoft Enterprise SSO broker presents the device identity; browser and device-code "
    "sign-in cannot prove device state"
)

# Actionable text for the AADSTS codes a managed tenant is most likely to return.
_AADSTS_ACTIONS: Mapping[str, str] = {
    "AADSTS65001": (
        "consent required: the tenant does not let you consent to the requested permissions yourself. "
        "IT action: an Entra administrator grants admin consent for the agentsync app registration "
        "(Entra admin center > Enterprise applications > agentsync > Permissions > Grant admin consent), "
        "or approves your admin consent request"
    ),
    "AADSTS90094": (
        "admin approval needed for the requested permissions. IT action: grant tenant-wide admin consent for "
        "the agentsync app registration, or approve the pending admin consent request"
    ),
    "AADSTS90095": (
        "admin consent required (the tenant routes consent to an admin workflow). IT action: approve the "
        "admin consent request for the agentsync app registration"
    ),
    "AADSTS65005": (
        "the app registration requests a permission the tenant does not allow (or the resource is disabled). "
        "IT action: review the agentsync app registration's API permissions and the tenant's consent policy"
    ),
    "AADSTS50105": (
        "your account is not assigned to the agentsync app ('Assignment required' is on). IT action: add you "
        "under Enterprise applications > agentsync > Users and groups"
    ),
    "AADSTS700016": (
        "the client id is not registered in this tenant. Check [graph] client_id and [graph] tenant in "
        "sources.toml against the IT app registration"
    ),
    "AADSTS7000218": (
        "the app registration is not a public client. IT action: set 'Allow public client flows' = Yes on "
        "the agentsync app registration (Authentication blade); it is needed only for device-code sign-in"
    ),
    "AADSTS50011": (
        "the redirect URI is not registered. IT action: under Authentication > 'Mobile and desktop "
        f"applications' list {LOOPBACK_REDIRECT_URI} and {BROKER_REDIRECT_URI}"
    ),
    "AADSTS50194": (
        "the app registration is single-tenant: set [graph] tenant in sources.toml to your tenant id "
        "(a GUID or contoso.onmicrosoft.com), not organizations/common"
    ),
    "AADSTS53003": (
        "blocked by a Conditional Access policy (device-code sign-in is commonly blocked this way). "
        f"Try the broker: {_BROKER_HOW}. If it is still blocked, IT must allow the agentsync app in the "
        "policy"
    ),
    "AADSTS53000": (
        "Conditional Access requires a compliant device and this Mac is not reported compliant. Bring it "
        f"into Intune compliance (Company Portal > Check status), then {_BROKER_HOW}"
    ),
    "AADSTS53001": f"Conditional Access requires a domain-joined/registered device: {_BROKER_HOW}",
    "AADSTS50097": f"Conditional Access requires device authentication: {_BROKER_HOW}",
    "AADSTS530003": (
        "the device must be managed: enrol the Mac in MDM (Intune) with the Enterprise SSO or Platform SSO "
        f"profile, then {_BROKER_HOW}"
    ),
    "AADSTS135011": "the device is disabled in Entra ID. IT action: re-enable this Mac's device object",
    "AADSTS50076": "multi-factor authentication is required: sign in again with `agentsync graph login`",
    "AADSTS50078": "multi-factor authentication expired: sign in again with `agentsync graph login`",
    "AADSTS50079": "you must register for multi-factor authentication, then run `agentsync graph login`",
    "AADSTS70043": "the sign-in frequency policy ended the session: run `agentsync graph login` again",
    "AADSTS50173": "the refresh token was revoked (password change or admin revocation): sign in again",
    "AADSTS700082": "the refresh token expired from inactivity: sign in again with `agentsync graph login`",
    "AADSTS65004": "you declined the consent prompt; run `agentsync graph login` again to accept it",
}

_CONSENT_CODES = frozenset({"AADSTS65001", "AADSTS90094", "AADSTS90095"})

_DEVICE_FLOW_ERRORS: Mapping[str, str] = {
    "authorization_declined": "the sign-in was declined in the browser",
    "expired_token": "the device code expired before sign-in completed; run `agentsync login` again",
    "code_expired": "the device code expired before sign-in completed; run `agentsync login` again",
    "bad_verification_code": "the device code was not recognised; run `agentsync login` again",
}


class TokenProvider(Protocol):
    """Anything that can hand the Graph client a bearer token."""

    def get_token(self) -> str:
        """Return a valid access token or raise AuthRequiredError (never prompts)."""
        ...


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Everything auth needs, derived from Config."""

    client_id: str
    authority: str
    scopes: tuple[str, ...]
    keychain_marker: Path
    fallback_cache: Path
    allow_device_code: bool = False  # [graph] allow_device_code: the ladder's last rung (C15 §9.3)
    use_broker: bool = True  # [graph] broker: try the macOS broker first
    graph_root: str = "https://graph.microsoft.com"  # the cloud's Graph root (scope resource)
    proxy: str | None = None  # [network] proxy (URL or "direct"); None = env, then macOS system proxy
    interactive_timeout_s: int = 300  # loopback rung: give up waiting for the browser after this


@dataclass(frozen=True, slots=True)
class AuthStatus:
    """What ``agentsync login --status`` / doctor report (no secrets)."""

    signed_in: bool
    username: str | None
    tenant_id: str | None
    cache_backend: str  # "keychain" | "file-0600"
    scopes: tuple[str, ...]
    sign_in_method: str | None = None  # "broker" | "loopback" | "device-code" | None (unknown/signed out)


# ---------------------------------------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------------------------------------


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def cloud_for(name: str | None, base_url: str) -> CloudEndpoints:
    """The cloud named ``name``, else the one whose Graph host is ``base_url``'s, else global."""
    if name:
        key = name.strip().lower()
        if key not in CLOUDS:
            raise ConfigError(f"[graph] cloud must be one of {', '.join(sorted(CLOUDS))}, got {name!r}")
        return CLOUDS[key]
    host = _host(base_url)
    for cloud in CLOUDS.values():
        if _host(cloud.graph_root) == host:
            return cloud
    return CLOUDS["global"]


def _qualify_scopes(scopes: Iterable[str], cloud: CloudEndpoints) -> tuple[str, ...]:
    """National clouds need resource-qualified scopes (``https://graph.microsoft.us/Files.Read.All``)."""
    if cloud.name == "global":
        return tuple(scopes)
    return tuple(s if "://" in s else f"{cloud.graph_root}/{s}" for s in scopes)


def _scope_key(scope: str) -> str:
    """Case-insensitive scope name without its resource prefix."""
    return scope.rsplit("/", 1)[-1].lower()


def _config_attr(obj: object, name: str, default: Any) -> Any:
    """A config attribute that a newer config.py may add (additive contract), else ``default``."""
    value = getattr(obj, name, default)
    return default if value is None else value


def settings_from_config(config: Config) -> AuthSettings:
    """Build AuthSettings; raises ConfigError when ``[graph] client_id`` is unset, the tenant is
    multi-tenant (AADSTS50194), or the cloud and ``base_url`` disagree."""
    client_id = config.graph.client_id
    if not client_id:
        raise ConfigError(
            f"{config.config_path}: [graph] client_id is not set; Graph sources need the IT app "
            "registration's client id (see the IT request pack)"
        )
    tenant = (config.graph.tenant or "").strip()
    if not tenant or tenant.lower() in MULTI_TENANT_AUTHORITIES:
        raise ConfigError(
            f"{config.config_path}: [graph] tenant = {tenant or '<unset>'!r} is a multi-tenant authority; "
            "the agentsync app registration is single-tenant and Entra refuses it with AADSTS50194 ('Use a "
            "tenant-specific endpoint'). Set [graph] tenant to your tenant id (GUID) or verified domain"
        )
    base_url = config.graph.base_url
    cloud = cloud_for(_config_attr(config.graph, "cloud", None), base_url)
    if _host(base_url) != _host(cloud.graph_root) and _host(base_url).endswith(
        tuple(_host(c.graph_root) for c in CLOUDS.values())
    ):
        raise ConfigError(
            f"{config.config_path}: [graph] base_url {base_url} is not the {cloud.name} cloud's Graph root "
            f"{cloud.graph_root}; tokens from one cloud are not accepted by another"
        )
    scopes = _clean_scopes(config.graph_scopes())
    if not scopes:
        raise ConfigError(
            f"{config.config_path}: [graph] scopes is empty after removing MSAL-reserved scopes"
        )
    network = getattr(config, "network", None)
    proxy = _config_attr(network, "proxy", None) if network is not None else None
    sp = config.state_paths
    return AuthSettings(
        client_id=client_id,
        authority=f"{cloud.login_host}/{tenant}",
        scopes=_qualify_scopes(scopes, cloud),
        keychain_marker=sp.keychain_marker,
        fallback_cache=sp.token_cache_fallback,
        allow_device_code=bool(_config_attr(config.graph, "allow_device_code", False)),
        use_broker=bool(_config_attr(config.graph, "broker", True)),
        graph_root=cloud.graph_root,
        proxy=str(proxy) if proxy is not None else None,
    )


def admin_consent_url(settings: AuthSettings) -> str:
    """The tenant-wide admin-consent URL for this app registration (for the IT admin, not the user)."""
    return f"{settings.authority.rstrip('/')}/adminconsent?client_id={settings.client_id}"


def _clean_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    """Drop MSAL-reserved scopes (MSAL adds offline_access itself) and duplicates, keeping order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in scopes:
        s = raw.strip()
        if not s or s.lower() in _RESERVED_SCOPES:
            continue
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return tuple(out)


def _tenant_of(authority: str) -> str:
    """Last path segment of an authority URL (``organizations``, a tenant GUID or a domain)."""
    return authority.rstrip("/").rsplit("/", 1)[-1] or "common"


def _keychain_account(settings: AuthSettings) -> str:
    """Keychain account name for this tenant + client id."""
    return f"{KEYCHAIN_ACCOUNT}:{_tenant_of(settings.authority)}:{settings.client_id}"


# ---------------------------------------------------------------------------------------------------------
# error classification (C15 §1.5)
# ---------------------------------------------------------------------------------------------------------


def _first_line(text: object) -> str:
    """First line of an MSAL error description (they carry trace/correlation ids on later lines)."""
    s = str(text or "").strip()
    return s.splitlines()[0].strip() if s else ""


def _aadsts_code(result: Mapping[str, Any]) -> str | None:
    """Extract ``AADSTSnnnn`` from an MSAL error result (``error_codes`` first, else the description)."""
    codes = result.get("error_codes")
    if isinstance(codes, list) and codes:
        return f"AADSTS{codes[0]}"
    m = _AADSTS_RE.search(str(result.get("error_description") or ""))
    return m.group(0) if m else None


def _all_codes(result: Mapping[str, Any]) -> list[str]:
    """Every AADSTS code in an MSAL error result, ``error_codes`` first, in order, without duplicates."""
    codes = [f"AADSTS{c}" for c in result.get("error_codes") or [] if isinstance(c, int)]
    codes.extend(_AADSTS_RE.findall(str(result.get("error_description") or "")))
    return list(dict.fromkeys(codes))


def classify_auth_error(result: Mapping[str, Any]) -> str | None:
    """Pipeline state for an MSAL error result (C15 §1.5), or None when the error is not a known tenant
    decision (then it is transient or unexpected)."""
    for code in _all_codes(result):
        if code in AADSTS_STATES:
            return AADSTS_STATES[code]
    error = str(result.get("error") or "")
    if error in ("invalid_grant", "interaction_required"):
        return _gerr.STATE_REAUTH
    return None


def _is_reauth(result: Mapping[str, Any]) -> bool:
    """True when an MSAL error result means a human must sign in again."""
    error = str(result.get("error") or "")
    if error in REAUTH_ERROR_CODES:
        return True
    return any(c in REAUTH_ERROR_CODES for c in _all_codes(result))


def _describe_error(result: Mapping[str, Any], *, during: str, consent_url: str | None = None) -> str:
    """Human message for an MSAL error result: the AADSTS code, its IT action if known, MSAL's first line."""
    error = str(result.get("error") or "unknown_error")
    code = next((c for c in _all_codes(result) if c in _AADSTS_ACTIONS), None) or _aadsts_code(result)
    head = f"{during} failed: {error}" + (f" {code}" if code else "")
    action = _AADSTS_ACTIONS.get(code or "") or _DEVICE_FLOW_ERRORS.get(error)
    if action and consent_url and code in _CONSENT_CODES:
        action = f"{action}. Admin-consent URL for the IT admin: {consent_url}"
    desc = _first_line(result.get("error_description"))
    tail = [t for t in (action, f"({desc})" if desc else None) if t]
    return " — ".join([head, *tail])


def _error_for(result: Mapping[str, Any], *, during: str, consent_url: str | None) -> AuthError:
    """The typed, never-retried error for an MSAL error result."""
    message = _describe_error(result, during=during, consent_url=consent_url)
    state = classify_auth_error(result)
    if state is not None and state != _gerr.STATE_REAUTH:
        codes = _all_codes(result)
        code = next((c for c in codes if AADSTS_STATES.get(c) == state), codes[0] if codes else None)
        return AuthBlockedError(state, code, f"REAUTH_REQUIRED ({state}): {message}")
    if state == _gerr.STATE_REAUTH or _is_reauth(result):
        return AuthRequiredError(f"REAUTH_REQUIRED: {message}")
    return AuthError(message)


# ---------------------------------------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------------------------------------


class _FilePersistence0600:
    """msal-extensions FilePersistence that re-asserts mode 0600 after every write (fallback only)."""

    is_encrypted = False

    def __init__(self, location: Path) -> None:
        if is_cloud_path(location):
            raise AuthError(f"refusing to keep a token cache inside ~/Library/CloudStorage: {location}")
        location.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._location = location
        self._inner: Any = FilePersistence(str(location))
        if location.exists():
            location.chmod(0o600)

    def save(self, content: str) -> None:
        """Write the serialized cache and chmod 0600."""
        self._inner.save(content)
        self._location.chmod(0o600)

    def load(self) -> str:
        """Read the serialized cache (PersistenceNotFound if never saved)."""
        return str(self._inner.load())

    def time_last_modified(self) -> float:
        """mtime of the cache file (PersistenceNotFound if never saved)."""
        return float(self._inner.time_last_modified())

    def get_location(self) -> str:
        """Path of the cache file."""
        return str(self._location)


def _keychain_persistence(settings: AuthSettings) -> Any:
    """Build the Keychain persistence (seam: tests replace this, so no test touches the login Keychain)."""
    settings.keychain_marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return KeychainPersistence(str(settings.keychain_marker), KEYCHAIN_SERVICE, _keychain_account(settings))


def _file_persistence(settings: AuthSettings) -> Any:
    """Build the 0600 file persistence (fallback)."""
    return _FilePersistence0600(settings.fallback_cache)


def _probe_keychain(persistence: Any) -> str | None:
    """Read the Keychain item once: its content, None if absent; raise if the Keychain is unusable."""
    try:
        return str(persistence.load())
    except PersistenceNotFound:
        return None


def _open_persistence(settings: AuthSettings) -> tuple[Any, str]:
    """Return (persistence, backend): the Keychain when it answers, else the 0600 file with a WARNING."""
    try:
        keychain = _keychain_persistence(settings)
        content = _probe_keychain(keychain)
    except Exception as exc:  # third-party: KeychainError, OSError, ImportError off macOS, ...
        logger.warning(
            "macOS Keychain unavailable (%s); falling back to a mode-0600 token cache file at %s",
            type(exc).__name__,
            settings.fallback_cache,
        )
        return _file_persistence(settings), _BACKEND_FILE

    fallback = settings.fallback_cache
    if content is None and fallback.is_file():
        # A previous session could not reach the Keychain: move its cache in and remove the plaintext file.
        data = fallback.read_text(encoding="utf-8")
        if data.strip():
            keychain.save(data)
            logger.info("moved the fallback token cache into the Keychain and removed %s", fallback)
        fallback.unlink()
    elif content is not None:
        if not settings.keychain_marker.exists():
            settings.keychain_marker.touch(mode=0o600)  # PersistedTokenCache reads mtime from the marker
        if fallback.is_file():
            logger.warning(
                "a plaintext fallback token cache still exists at %s while the Keychain holds one; "
                "`agentsync logout` removes both",
                fallback,
            )
    return keychain, _BACKEND_KEYCHAIN


# ---------------------------------------------------------------------------------------------------------
# MSAL app and broker
# ---------------------------------------------------------------------------------------------------------


def _import_pymsalruntime() -> object:
    """Import the broker runtime (seam for tests)."""
    return importlib.import_module("pymsalruntime")


def broker_unavailable_reason() -> str | None:
    """Why the macOS broker cannot be used in this process, or None when it can (no network, no UI).

    ``pymsalruntime`` raises ``RuntimeError`` ("SSO extension is inactive or is not available") at import on
    a Mac without an active Enterprise SSO extension; MSAL itself only catches that, not a missing package.
    """
    if sys.platform != "darwin":
        return f"the MSAL broker rung is macOS-only (platform {sys.platform})"
    machine = platform.machine()
    if machine != "arm64":
        return f"MSAL supports the macOS broker on Apple Silicon (arm64) only; this process is {machine}"
    try:
        _import_pymsalruntime()
    except ImportError as exc:
        return f"pymsalruntime is not installed ({_first_line(exc)}); install msal[broker]"
    except RuntimeError as exc:
        context = str(exc).split("ErrorContext:", 1)[-1].split(", ErrorStatus", 1)[0].strip()
        return (
            f"the Microsoft Enterprise SSO extension is not available ({_first_line(context)[:160]}): "
            "Company Portal is not installed or this Mac is not MDM-enrolled with the SSO extension profile"
        )
    except Exception as exc:  # a broken native module must not break sign-in; log and fall through
        return f"pymsalruntime failed to load ({type(exc).__name__})"
    return None


def _http_client(settings: AuthSettings) -> Any:
    """MSAL's HTTP session: truststore TLS and the resolved proxy; a PAC-only proxy fails closed."""
    proxy = net.resolve_proxy(settings.proxy)
    if proxy.policy_error:
        raise AuthError(proxy.policy_error)
    logger.debug("MSAL HTTP via %s", proxy.describe())
    return net.requests_session(proxy, target_url=settings.authority)


def _make_app(settings: AuthSettings, cache: Any, *, broker: bool = False) -> Any:
    """Build the MSAL public client (network: OpenID discovery). Seam for tests."""
    return msal.PublicClientApplication(
        settings.client_id,
        authority=settings.authority,
        token_cache=cache,
        app_name="agentsync",
        app_version=__version__,
        enable_broker_on_mac=broker,
        http_client=_http_client(settings),
    )


def _is_cancel(result: Mapping[str, Any]) -> bool:
    """True when the user cancelled/declined the interactive prompt (never fall through to another rung)."""
    status = str(result.get("_broker_status") or "")
    if "UserCanceled" in status or "UserCancelled" in status:
        return True
    if "AADSTS65004" in _all_codes(result):
        return True
    return str(result.get("error") or "") == "access_denied" and not _all_codes(result)


# ---------------------------------------------------------------------------------------------------------
# MsalAuth
# ---------------------------------------------------------------------------------------------------------


class _FallThrough(Exception):  # noqa: N818 - internal control flow for the sign-in ladder
    """A sign-in rung could not run; the ladder tries the next one."""


class MsalAuth:
    """TokenProvider backed by ``msal.PublicClientApplication`` and a persisted token cache."""

    def __init__(self, settings: AuthSettings) -> None:
        """Create the persisted cache (Keychain, else 0600 file with a WARNING) and the MSAL app."""
        self._settings = settings
        self._persistence, self._backend = _open_persistence(settings)
        self._cache: Any = PersistedTokenCache(self._persistence)
        self._apps: dict[bool, Any] = {}  # built lazily per broker flag: MSAL's constructor does discovery
        self._broker_reason: tuple[str | None] | None = None
        self._last_token_source: str | None = None

    @property
    def cache_backend(self) -> str:
        """``"keychain"`` or ``"file-0600"``."""
        return self._backend

    @property
    def last_token_source(self) -> str | None:
        """MSAL ``token_source`` of the last token this instance obtained (``broker`` / ``identity_provider``
        / ``cache``); None before the first one."""
        return self._last_token_source

    def broker_unavailable_reason(self) -> str | None:
        """Why the broker rung is skipped (config or platform), None when usable; cached per instance."""
        if not self._settings.use_broker:
            return "disabled by [graph] broker = false"
        if self._broker_reason is None:
            self._broker_reason = (broker_unavailable_reason(),)
        return self._broker_reason[0]

    # -- MSAL app ---------------------------------------------------------------------------------------

    def _app(self, broker: bool = False) -> Any:
        """The MSAL app, built on first network use; construction failures become AuthError."""
        if broker not in self._apps:
            try:
                self._apps[broker] = _make_app(self._settings, self._cache, broker=broker)
            except AuthError:
                raise
            except Exception as exc:
                raise AuthError(
                    f"cannot reach the Microsoft identity platform for {self._settings.authority} "
                    f"({type(exc).__name__}: {_first_line(exc)})"
                ) from None
        return self._apps[broker]

    def _consent_url(self) -> str:
        return admin_consent_url(self._settings)

    # -- cache reads (no network) ------------------------------------------------------------------------

    def _cached_accounts(self) -> list[dict[str, Any]]:
        """AAD accounts in the persisted cache, sorted by (username, home_account_id)."""
        ct = msal.TokenCache.CredentialType.ACCOUNT
        accounts = [
            dict(a)
            for a in self._cache.search(ct)
            if a.get("authority_type") in (None, msal.TokenCache.AuthorityType.MSSTS)
        ]
        # One account per home_account_id (realm-specific duplicates collapse).
        by_home: dict[str, dict[str, Any]] = {}
        for a in accounts:
            by_home.setdefault(str(a.get("home_account_id") or ""), a)
        return sorted(
            by_home.values(), key=lambda a: (str(a.get("username") or ""), str(a["home_account_id"]))
        )

    def _has_refresh_token(self, home_account_id: str) -> bool:
        """True when the cache holds a refresh token for this account and client id."""
        ct = msal.TokenCache.CredentialType.REFRESH_TOKEN
        for rt in self._cache.search(ct, query={"home_account_id": home_account_id}):
            if rt.get("client_id") == self._settings.client_id or rt.get("family_id"):
                return True
        return False

    def _granted_scopes(self, home_account_id: str) -> tuple[str, ...]:
        """Scopes on cached access tokens for this account (``now=0`` so the read never prunes the cache)."""
        ct = msal.TokenCache.CredentialType.ACCESS_TOKEN
        granted: set[str] = set()
        query = {"home_account_id": home_account_id, "client_id": self._settings.client_id}
        for at in self._cache.search(ct, query=query, now=0):
            granted.update(str(at.get("target") or "").split())
        return tuple(sorted(s for s in granted if _scope_key(s) not in _RESERVED_SCOPES))

    def _status_for(self, account: Mapping[str, Any] | None) -> AuthStatus:
        """Build an AuthStatus for ``account`` (None = signed out)."""
        if account is None:
            return AuthStatus(False, None, None, self._backend, self._settings.scopes)
        home = str(account.get("home_account_id") or "")
        tenant = account.get("realm") or (home.split(".", 1)[1] if "." in home else None)
        granted = self._granted_scopes(home)
        source = str(account.get("account_source") or "")
        signed_in = self._has_refresh_token(home) or source == "broker"  # the broker keeps its own RT
        return AuthStatus(
            signed_in=signed_in,
            username=account.get("username") or None,
            tenant_id=str(tenant) if tenant else None,
            cache_backend=self._backend,
            scopes=granted or self._settings.scopes,
            sign_in_method=_SIGN_IN_BY_ACCOUNT_SOURCE.get(source),
        )

    # -- interactive sign-in ----------------------------------------------------------------------------

    def login(self, emit: Callable[[str], None]) -> AuthStatus:
        """Interactive sign-in ladder: broker, then loopback auth code + PKCE, then device code (only when
        ``allow_device_code``).  ``emit`` receives user-facing progress lines; blocks until done.

        Raises AuthBlockedError / AuthRequiredError / AuthError with the AADSTS code and its action.
        """
        skipped: list[str] = []
        for method, rung in (
            (SIGN_IN_BROKER, self._broker_rung),
            (SIGN_IN_LOOPBACK, self._loopback_rung),
        ):
            try:
                app, result = rung(emit)
            except _FallThrough as why:
                logger.warning("sign-in: %s rung skipped: %s", method, why)
                skipped.append(f"{method}: {why}")
                continue
            return self._finish_login(app, result, method)
        if not self._settings.allow_device_code:
            raise AuthError(
                "interactive sign-in did not complete ("
                + "; ".join(skipped)
                + "). Device-code sign-in is off: it cannot satisfy device-based Conditional Access and many "
                "tenants block it; set [graph] allow_device_code = true only if IT allows it"
            )
        emit("Falling back to device-code sign-in ([graph] allow_device_code = true).")
        return self._device_code(emit)

    def _broker_rung(self, emit: Callable[[str], None]) -> tuple[Any, dict[str, Any]]:
        """Rung 1: the macOS broker (Company Portal SSO extension)."""
        reason = self.broker_unavailable_reason()
        if reason is not None:
            raise _FallThrough(reason)
        try:
            app = self._app(broker=True)
        except AuthError as exc:
            raise _FallThrough(str(exc)) from None
        if not getattr(app, "_enable_broker", False):
            raise _FallThrough("MSAL disabled the broker on this platform (see the msal.application log)")
        emit("Signing in through the Microsoft Enterprise SSO broker (Company Portal)...")
        try:
            result: dict[str, Any] = app.acquire_token_interactive(
                list(self._settings.scopes), parent_window_handle=app.CONSOLE_WINDOW_HANDLE
            )
        except Exception as exc:  # msal.broker.RedirectUriError (ValueError), pymsalruntime errors, ...
            hint = ""
            if "redirect" in str(exc).lower():
                hint = f"; IT action: register the redirect URI {BROKER_REDIRECT_URI}"
            raise _FallThrough(f"{type(exc).__name__}: {_first_line(exc)[:200]}{hint}") from None
        if "access_token" in result:
            return app, result
        if _is_cancel(result):
            raise AuthError("sign-in was cancelled in the broker window")
        state = classify_auth_error(result)
        if state is None or "AADSTS50011" in _all_codes(result):  # broker-only failure: try the browser
            raise _FallThrough(_describe_error(result, during="broker sign-in"))
        raise _error_for(result, during="broker sign-in", consent_url=self._consent_url())

    def _loopback_rung(self, emit: Callable[[str], None]) -> tuple[Any, dict[str, Any]]:
        """Rung 2: auth code + PKCE in the system browser, redirect http://localhost:<port>."""
        app = self._app(broker=False)  # unreachable identity platform: device code would fail too -> raise
        emit(f"Opening the system browser to sign in (redirect {LOOPBACK_REDIRECT_URI})...")
        timeout = self._settings.interactive_timeout_s
        try:
            result: dict[str, Any] = app.acquire_token_interactive(
                list(self._settings.scopes),
                prompt="select_account",
                timeout=timeout,
                auth_uri_callback=lambda uri: emit(
                    f"No browser could be opened; visit this URL on this Mac: {uri}"
                ),
            )
        except RuntimeError as exc:  # BrowserInteractionTimeoutError, "Timeout. No auth response arrived."
            raise _FallThrough(
                f"the browser sign-in did not complete within {timeout}s ({_first_line(exc)})"
            ) from None
        except Exception as exc:  # port bind failure, no browser, ...
            raise _FallThrough(f"{type(exc).__name__}: {_first_line(exc)[:200]}") from None
        if "access_token" in result:
            return app, result
        if _is_cancel(result):
            raise AuthError(_describe_error(result, during="browser sign-in") + " — sign-in was cancelled")
        if "AADSTS50011" in _all_codes(result):
            raise _FallThrough(_describe_error(result, during="browser sign-in"))
        raise _error_for(result, during="browser sign-in", consent_url=self._consent_url())

    def login_device_code(self, emit: Callable[[str], None]) -> AuthStatus:
        """Run the device-code flow: ``emit`` receives the verification message; blocks until done.

        Refused (AuthError) unless ``[graph] allow_device_code = true`` (C15 §1.4); :meth:`login` is the
        normal entry point.  Raises AuthError with the AADSTS code (e.g. AADSTS65001 consent required ->
        message names the IT action and the admin-consent URL).
        """
        if not self._settings.allow_device_code:
            raise AuthError(
                "device-code sign-in is off: it cannot satisfy device-based Conditional Access and many "
                "tenants block it. Use `agentsync graph login` (broker, then browser), or set [graph] "
                "allow_device_code = true if IT allows it"
            )
        return self._device_code(emit)

    def _device_code(self, emit: Callable[[str], None]) -> AuthStatus:
        """Rung 3: the device-code flow."""
        app = self._app(broker=False)
        scopes = list(self._settings.scopes)
        try:
            flow: dict[str, Any] = app.initiate_device_flow(scopes=scopes)
        except Exception as exc:
            raise AuthError(f"device-code sign-in could not start ({type(exc).__name__})") from None
        if "user_code" not in flow:
            raise _error_for(flow, during="device-code sign-in", consent_url=self._consent_url())
        emit(str(flow.get("message") or f"Enter code {flow['user_code']} at {flow.get('verification_uri')}"))
        try:
            result: dict[str, Any] = app.acquire_token_by_device_flow(flow)
        except Exception as exc:
            raise AuthError(f"device-code sign-in failed ({type(exc).__name__})") from None
        if "access_token" not in result:
            raise _error_for(result, during="device-code sign-in", consent_url=self._consent_url())
        return self._finish_login(app, result, SIGN_IN_DEVICE_CODE)

    def _finish_login(self, app: Any, result: Mapping[str, Any], method: str) -> AuthStatus:
        """Keep one principal, warn on partially granted scopes, record the token source, report status."""
        claims = result.get("id_token_claims") or {}
        oid, tid = claims.get("oid"), claims.get("tid")
        home = f"{oid}.{tid}" if oid and tid else None
        self._forget_other_accounts(app, home)
        granted = {_scope_key(s) for s in str(result.get("scope") or "").split()}
        missing = [s for s in self._settings.scopes if _scope_key(s) not in granted]
        if granted and missing:
            logger.warning("signed in, but these scopes were not granted: %s", " ".join(missing))
        self._last_token_source = str(result.get("token_source") or "") or None
        account = next((a for a in self._cached_accounts() if a.get("home_account_id") == home), None)
        if account is None:
            accounts = self._cached_accounts()
            account = accounts[0] if accounts else None
        status = self._status_for(account)
        logger.info(
            "signed in as %s (tenant %s, via %s, token_source %s, cache %s)",
            status.username,
            status.tenant_id,
            method,
            self._last_token_source,
            self._backend,
        )
        return status

    def _forget_other_accounts(self, app: Any, keep_home_account_id: str | None) -> None:
        """One principal per machine: drop every cached account other than the one just signed in."""
        if keep_home_account_id is None:
            return
        for account in app.get_accounts():
            if account.get("home_account_id") != keep_home_account_id:
                logger.info("removing previously cached account %s", account.get("username"))
                app.remove_account(account)

    # -- silent -----------------------------------------------------------------------------------------

    def _select_account(self, app: Any) -> Any:
        """First cached account (sorted by username for determinism) or AuthRequiredError."""
        accounts = sorted(
            app.get_accounts(),
            key=lambda a: (str(a.get("username") or ""), str(a.get("home_account_id") or "")),
        )
        if not accounts:
            raise AuthRequiredError(
                "REAUTH_REQUIRED: no signed-in account in the token cache; run `agentsync login`"
            )
        if len(accounts) > 1:
            logger.warning(
                "%d accounts in the token cache; using %s (run `agentsync login` to keep only one)",
                len(accounts),
                accounts[0].get("username"),
            )
        return accounts[0]

    def _silent(self, *, force_refresh: bool, claims_challenge: str | None) -> str:
        """acquire_token_silent_with_error, mapping every failure to AuthRequiredError / AuthError."""
        cached = self._cached_accounts()
        if not cached:  # answer offline: no account means a human must sign in
            raise AuthRequiredError(
                "REAUTH_REQUIRED: no signed-in account in the token cache; run `agentsync login`"
            )
        use_broker = cached[0].get("account_source") == "broker"
        if use_broker:
            reason = self.broker_unavailable_reason()
            if reason is not None:
                raise AuthRequiredError(
                    "REAUTH_REQUIRED: the cached account was signed in through the broker, which is "
                    f"unavailable now ({reason}); run `agentsync graph login`"
                )
        app = self._app(broker=use_broker)
        account = self._select_account(app)
        try:
            result: dict[str, Any] | None = app.acquire_token_silent_with_error(
                list(self._settings.scopes),
                account,
                force_refresh=force_refresh,
                claims_challenge=claims_challenge,
            )
        except Exception as exc:  # requests/network errors inside MSAL: transient, not a re-auth
            policy = net.classify_transport_error(exc)
            if policy is not None:
                raise AuthError(
                    f"token refresh failed: {policy} ({type(exc).__name__}); "
                    + (net.TLS_HINT if policy == net.POLICY_TLS else net.PROXY_HINT)
                ) from None
            raise AuthError(f"token refresh failed ({type(exc).__name__}); will retry next cycle") from None
        if not result:
            raise AuthRequiredError(
                "REAUTH_REQUIRED: no usable refresh token for the cached account; run `agentsync login`"
            )
        if "access_token" in result:
            self._last_token_source = str(result.get("token_source") or "") or None
            return str(result["access_token"])
        raise _error_for(result, during="silent token acquisition", consent_url=self._consent_url())

    def get_token(self) -> str:
        """acquire_token_silent for the first cached account; REAUTH_ERROR_CODES or no account ->
        AuthRequiredError; never interactive, never retried."""
        return self._silent(force_refresh=False, claims_challenge=None)

    def refresh_token(self, claims_challenge: str | None = None) -> str:
        """Force a refresh-token redemption (Graph 401 / CAE claims challenge); same errors as get_token."""
        return self._silent(force_refresh=True, claims_challenge=claims_challenge)

    def status(self) -> AuthStatus:
        """Report the cached account without network access."""
        accounts = self._cached_accounts()
        return self._status_for(accounts[0] if accounts else None)

    def logout(self) -> None:
        """Remove every cached account and token from the persisted cache."""
        ct = msal.TokenCache.CredentialType
        for kind in (ct.ACCESS_TOKEN, ct.REFRESH_TOKEN, ct.ID_TOKEN, ct.ACCOUNT, ct.APP_METADATA):
            for entry in list(
                self._cache.search(kind, now=0) if kind == ct.ACCESS_TOKEN else self._cache.search(kind)
            ):
                self._cache.modify(kind, entry)  # no new values = remove, then persist
        fallback = self._settings.fallback_cache
        if self._backend == _BACKEND_KEYCHAIN and fallback.is_file():
            fallback.unlink()
            logger.info("removed the plaintext fallback token cache %s", fallback)
        self._apps.clear()
        logger.info("signed out: token cache (%s) emptied", self._backend)
