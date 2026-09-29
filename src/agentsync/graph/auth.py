"""MSAL public-client device-code sign-in with a Keychain-backed token cache (owner: graph-core).

Token custody: msal-extensions ``KeychainPersistence`` (service ``agentsync``, account ``msal_token_cache``).
Only if the Keychain is unavailable (e.g. no GUI session) does it fall back to a mode-0600 file under the
state dir, logged at WARNING. Tokens are never logged, printed, or written under docs/.

The Keychain account name is ``msal_token_cache:<tenant>:<client_id>`` (``KEYCHAIN_ACCOUNT`` plus the tenant
and client id), so two app registrations or tenants on one Mac never share a cache entry.

MSAL's ``PublicClientApplication`` performs OpenID discovery (network) when it is constructed, so it is built
lazily: :meth:`MsalAuth.status` and :meth:`MsalAuth.logout` read and edit the persisted cache without any
network access, and :meth:`MsalAuth.get_token` answers "no account" (``AuthRequiredError``) offline too.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import msal
from msal_extensions import FilePersistence, KeychainPersistence, PersistedTokenCache
from msal_extensions.persistence import PersistenceNotFound

from agentsync import __version__
from agentsync.config import Config
from agentsync.errors import AuthError, AuthRequiredError, ConfigError
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

_BACKEND_KEYCHAIN = "keychain"
_BACKEND_FILE = "file-0600"

# MSAL adds these itself and raises ValueError if a caller passes them explicitly.
_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})

_AADSTS_RE = re.compile(r"AADSTS\d+")

# Actionable text for the AADSTS codes a managed tenant is most likely to return at sign-in.
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
        "the agentsync app registration (Authentication blade)"
    ),
    "AADSTS53003": (
        "blocked by a Conditional Access policy. IT action: allow this device/app, or sign in from a "
        "compliant device"
    ),
    "AADSTS50076": "multi-factor authentication is required: sign in again with `agentsync login`",
    "AADSTS50078": "multi-factor authentication expired: sign in again with `agentsync login`",
    "AADSTS50173": "the refresh token was revoked (password change or admin revocation): sign in again",
    "AADSTS700082": "the refresh token expired from inactivity: sign in again with `agentsync login`",
}

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


@dataclass(frozen=True, slots=True)
class AuthStatus:
    """What ``agentsync login --status`` / doctor report (no secrets)."""

    signed_in: bool
    username: str | None
    tenant_id: str | None
    cache_backend: str  # "keychain" | "file-0600"
    scopes: tuple[str, ...]


def settings_from_config(config: Config) -> AuthSettings:
    """Build AuthSettings; raises ConfigError when ``[graph] client_id`` is unset."""
    client_id = config.graph.client_id
    if not client_id:
        raise ConfigError(
            f"{config.config_path}: [graph] client_id is not set; Graph sources need the IT app "
            "registration's client id (see the IT request pack)"
        )
    scopes = _clean_scopes(config.graph_scopes())
    if not scopes:
        raise ConfigError(
            f"{config.config_path}: [graph] scopes is empty after removing MSAL-reserved scopes"
        )
    sp = config.state_paths
    return AuthSettings(
        client_id=client_id,
        authority=config.graph.authority,
        scopes=scopes,
        keychain_marker=sp.keychain_marker,
        fallback_cache=sp.token_cache_fallback,
    )


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


def _is_reauth(result: Mapping[str, Any]) -> bool:
    """True when an MSAL error result means a human must sign in again."""
    error = str(result.get("error") or "")
    if error in REAUTH_ERROR_CODES:
        return True
    codes = {f"AADSTS{c}" for c in result.get("error_codes") or [] if isinstance(c, int)}
    codes.update(_AADSTS_RE.findall(str(result.get("error_description") or "")))
    return any(c in REAUTH_ERROR_CODES for c in codes)


def _describe_error(result: Mapping[str, Any], *, during: str) -> str:
    """Human message for an MSAL error result: the AADSTS code, its IT action if known, MSAL's first line."""
    error = str(result.get("error") or "unknown_error")
    code = _aadsts_code(result)
    head = f"{during} failed: {error}" + (f" {code}" if code else "")
    action = _AADSTS_ACTIONS.get(code or "") or _DEVICE_FLOW_ERRORS.get(error)
    desc = _first_line(result.get("error_description"))
    tail = [t for t in (action, f"({desc})" if desc else None) if t]
    return " — ".join([head, *tail])


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


def _make_app(settings: AuthSettings, cache: Any) -> Any:
    """Build the MSAL public client (network: OpenID discovery). Seam for tests."""
    return msal.PublicClientApplication(
        settings.client_id,
        authority=settings.authority,
        token_cache=cache,
        app_name="agentsync",
        app_version=__version__,
    )


# ---------------------------------------------------------------------------------------------------------
# MsalAuth
# ---------------------------------------------------------------------------------------------------------


class MsalAuth:
    """TokenProvider backed by ``msal.PublicClientApplication`` and a persisted token cache."""

    def __init__(self, settings: AuthSettings) -> None:
        """Create the persisted cache (Keychain, else 0600 file with a WARNING) and the MSAL app."""
        self._settings = settings
        self._persistence, self._backend = _open_persistence(settings)
        self._cache: Any = PersistedTokenCache(self._persistence)
        self._app_obj: Any = None  # built lazily: MSAL's constructor does network discovery

    @property
    def cache_backend(self) -> str:
        """``"keychain"`` or ``"file-0600"``."""
        return self._backend

    # -- MSAL app ---------------------------------------------------------------------------------------

    def _app(self) -> Any:
        """The MSAL app, built on first network use; construction failures become AuthError."""
        if self._app_obj is None:
            try:
                self._app_obj = _make_app(self._settings, self._cache)
            except Exception as exc:
                raise AuthError(
                    f"cannot reach the Microsoft identity platform for {self._settings.authority} "
                    f"({type(exc).__name__}: {_first_line(exc)})"
                ) from None
        return self._app_obj

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
        return tuple(sorted(s for s in granted if s.lower() not in _RESERVED_SCOPES))

    def _status_for(self, account: Mapping[str, Any] | None) -> AuthStatus:
        """Build an AuthStatus for ``account`` (None = signed out)."""
        if account is None:
            return AuthStatus(False, None, None, self._backend, self._settings.scopes)
        home = str(account.get("home_account_id") or "")
        tenant = account.get("realm") or (home.split(".", 1)[1] if "." in home else None)
        granted = self._granted_scopes(home)
        return AuthStatus(
            signed_in=self._has_refresh_token(home),
            username=account.get("username") or None,
            tenant_id=str(tenant) if tenant else None,
            cache_backend=self._backend,
            scopes=granted or self._settings.scopes,
        )

    # -- public API -------------------------------------------------------------------------------------

    def login_device_code(self, emit: Callable[[str], None]) -> AuthStatus:
        """Run the device-code flow: ``emit`` receives the verification message; blocks until done.

        Raises AuthError with the AADSTS code (e.g. AADSTS65001 consent required -> message names the IT
        action).
        """
        app = self._app()
        scopes = list(self._settings.scopes)
        try:
            flow: dict[str, Any] = app.initiate_device_flow(scopes=scopes)
        except Exception as exc:
            raise AuthError(f"device-code sign-in could not start ({type(exc).__name__})") from None
        if "user_code" not in flow:
            raise AuthError(_describe_error(flow, during="device-code sign-in"))
        emit(str(flow.get("message") or f"Enter code {flow['user_code']} at {flow.get('verification_uri')}"))
        try:
            result: dict[str, Any] = app.acquire_token_by_device_flow(flow)
        except Exception as exc:
            raise AuthError(f"device-code sign-in failed ({type(exc).__name__})") from None
        if "access_token" not in result:
            raise AuthError(_describe_error(result, during="device-code sign-in"))

        claims = result.get("id_token_claims") or {}
        oid, tid = claims.get("oid"), claims.get("tid")
        home = f"{oid}.{tid}" if oid and tid else None
        self._forget_other_accounts(app, home)
        granted = set(str(result.get("scope") or "").split())
        missing = [s for s in scopes if s not in granted and s.lower() not in {g.lower() for g in granted}]
        if granted and missing:
            logger.warning("signed in, but these scopes were not granted: %s", " ".join(missing))
        account = next((a for a in self._cached_accounts() if a.get("home_account_id") == home), None)
        if account is None:
            accounts = self._cached_accounts()
            account = accounts[0] if accounts else None
        status = self._status_for(account)
        logger.info("signed in as %s (tenant %s, cache %s)", status.username, status.tenant_id, self._backend)
        return status

    def _forget_other_accounts(self, app: Any, keep_home_account_id: str | None) -> None:
        """One principal per machine: drop every cached account other than the one just signed in."""
        if keep_home_account_id is None:
            return
        for account in app.get_accounts():
            if account.get("home_account_id") != keep_home_account_id:
                logger.info("removing previously cached account %s", account.get("username"))
                app.remove_account(account)

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
        if not self._cached_accounts():  # answer offline: no account means a human must sign in
            raise AuthRequiredError(
                "REAUTH_REQUIRED: no signed-in account in the token cache; run `agentsync login`"
            )
        app = self._app()
        account = self._select_account(app)
        try:
            result: dict[str, Any] | None = app.acquire_token_silent_with_error(
                list(self._settings.scopes),
                account,
                force_refresh=force_refresh,
                claims_challenge=claims_challenge,
            )
        except Exception as exc:  # requests/network errors inside MSAL: transient, not a re-auth
            raise AuthError(f"token refresh failed ({type(exc).__name__}); will retry next cycle") from None
        if not result:
            raise AuthRequiredError(
                "REAUTH_REQUIRED: no usable refresh token for the cached account; run `agentsync login`"
            )
        if "access_token" in result:
            return str(result["access_token"])
        message = _describe_error(result, during="silent token acquisition")
        if _is_reauth(result):
            raise AuthRequiredError(f"REAUTH_REQUIRED: {message}")
        raise AuthError(message)

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
        self._app_obj = None
        logger.info("signed out: token cache (%s) emptied", self._backend)
