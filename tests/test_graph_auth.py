"""MsalAuth: settings, Keychain/file persistence, silent tokens, the sign-in ladder, status, logout.

No test touches the login Keychain or the network: the Keychain persistence factory and the MSAL app factory
are replaced with in-memory fakes; the token cache itself is msal-extensions' real PersistedTokenCache.
The broker probe is faked except in one test that imports the real pymsalruntime (it must never raise).
"""

from __future__ import annotations

import base64
import dataclasses
import importlib.metadata
import json
import logging
import ssl
import stat
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import msal
import msal.oauth2cli.authcode as authcode
import pytest
import requests
import respx
import truststore
from msal.oauth2cli.oauth2 import BrowserInteractionTimeoutError
from msal_extensions.persistence import PersistenceNotFound

from agentsync import net
from agentsync.config import Config, parse_config
from agentsync.errors import AuthError, AuthRequiredError, ConfigError
from agentsync.graph import auth as auth_mod
from agentsync.graph.auth import (
    BROKER_REDIRECT_URI,
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    LOOPBACK_REDIRECT_URI,
    REAUTH_ERROR_CODES,
    AuthSettings,
    AuthStatus,
    MsalAuth,
    admin_consent_url,
    classify_auth_error,
    settings_from_config,
)
from agentsync.graph.client import GraphClient
from agentsync.graph.errors import AuthBlockedError

CID = "11111111-2222-3333-4444-555555555555"
TID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OID = "99999999-8888-7777-6666-555555555555"
AT = "SECRET-AT-0123456789"
RT = "SECRET-RT-9876543210"
SCOPES = ("Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read")
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SSO_INACTIVE = (
    "ErrorTag: 504705889, ErrorCode: 0, ErrorContext: SSO extension is inactive or is not available on this "
    "device., ErrorStatus: Response_Status.Status_ApiContractViolation"
)


# --- fakes ----------


class FakeKeychain:
    """Duck-typed msal-extensions persistence backed by a dict (shared = one 'login Keychain')."""

    is_encrypted = True

    def __init__(self, marker: Path, store: dict[str, str], *, broken: Exception | None = None) -> None:
        self.marker = marker
        self.store = store
        self.broken = broken
        marker.parent.mkdir(parents=True, exist_ok=True)

    def save(self, content: str) -> None:
        self.store["item"] = content
        self.marker.touch()

    def load(self) -> str:
        if self.broken is not None:
            raise self.broken
        if "item" not in self.store:
            raise PersistenceNotFound(message="absent")
        return self.store["item"]

    def time_last_modified(self) -> float:
        try:
            return self.marker.stat().st_mtime
        except FileNotFoundError:
            raise PersistenceNotFound(message="absent") from None

    def get_location(self) -> str:
        return str(self.marker)


def _b64url(obj: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def token_response(
    username: str = "chris@contoso.com", oid: str = OID, tid: str = TID, scopes: tuple[str, ...] = SCOPES
) -> dict[str, Any]:
    claims = {"oid": oid, "tid": tid, "sub": oid, "preferred_username": username, "aud": CID, "iss": "x"}
    id_token = f"{_b64url({'alg': 'none'})}.{_b64url(claims)}.sig"
    return {
        "access_token": AT,
        "refresh_token": RT,
        "id_token": id_token,
        "id_token_claims": claims,
        "client_info": _b64url({"uid": oid, "utid": tid}),
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": " ".join((*scopes, "openid", "profile", "offline_access")),
    }


def seed(
    cache: Any, response: dict[str, Any], scopes: tuple[str, ...] = SCOPES, grant_type: str = DEVICE_GRANT
) -> None:
    """Add a token response to the cache exactly the way MSAL does after a sign-in."""
    tid = response["id_token_claims"]["tid"]
    cache.add(
        {
            "client_id": CID,
            "scope": list(scopes),
            "token_endpoint": f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
            "response": {k: v for k, v in response.items() if k != "token_source"},
            "grant_type": grant_type,
        }
    )


class FakeApp:
    """The slice of msal.PublicClientApplication that MsalAuth uses, over the real persisted cache."""

    CONSOLE_WINDOW_HANDLE = object()

    def __init__(self, cache: Any, *, broker: bool = False) -> None:
        self.cache = cache
        self.broker = broker
        self._enable_broker = broker
        self.interactive_calls: list[dict[str, Any]] = []
        self.interactive_result: dict[str, Any] = token_response()
        self.interactive_raises: Exception | None = None
        self.flow_scopes: list[str] | None = None
        self.silent_result: dict[str, Any] | None = {"access_token": AT}
        self.silent_raises: Exception | None = None
        self.silent_calls: list[dict[str, Any]] = []
        self.flow: dict[str, Any] = {
            "user_code": "ABCD-EFGH",
            "message": "Go to https://microsoft.com/devicelogin",
        }
        self.device_result: dict[str, Any] = token_response()
        self.removed: list[str] = []

    def get_accounts(self) -> list[dict[str, Any]]:
        ct = msal.TokenCache.CredentialType.ACCOUNT
        return [dict(a) for a in self.cache.search(ct)]

    def acquire_token_silent_with_error(
        self, scopes: list[str], account: dict[str, Any], *, force_refresh: bool, claims_challenge: str | None
    ) -> dict[str, Any] | None:
        self.silent_calls.append(
            {"scopes": scopes, "account": account, "force_refresh": force_refresh, "claims": claims_challenge}
        )
        if self.silent_raises is not None:
            raise self.silent_raises
        return self.silent_result

    def initiate_device_flow(self, scopes: list[str]) -> dict[str, Any]:
        self.flow_scopes = scopes
        return dict(self.flow)

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> dict[str, Any]:
        if "access_token" in self.device_result:
            seed(self.cache, self.device_result)
            return {**self.device_result, "token_source": "identity_provider"}
        return dict(self.device_result)

    def acquire_token_interactive(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        self.interactive_calls.append({"scopes": scopes, **kwargs})
        if self.interactive_raises is not None:
            raise self.interactive_raises
        result = dict(self.interactive_result)
        if "access_token" in result:
            if self.broker:  # MSAL stores broker results without a refresh token (the broker keeps it)
                seed(
                    self.cache, {k: v for k, v in result.items() if k != "refresh_token"}, grant_type="broker"
                )
                result["token_source"] = "broker"
            else:
                seed(self.cache, result, grant_type="authorization_code")
                result["token_source"] = "identity_provider"
        return result

    def remove_account(self, account: dict[str, Any]) -> None:
        self.removed.append(str(account.get("username")))
        ct = msal.TokenCache.CredentialType
        for kind in (ct.ACCESS_TOKEN, ct.REFRESH_TOKEN, ct.ID_TOKEN, ct.ACCOUNT):
            for entry in list(self.cache.search(kind, query={"home_account_id": account["home_account_id"]})):
                self.cache.modify(kind, entry)


@pytest.fixture
def settings(tmp_path: Path) -> AuthSettings:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    return AuthSettings(
        client_id=CID,
        authority=f"https://login.microsoftonline.com/{TID}",
        scopes=SCOPES,
        keychain_marker=state / "msal_token_cache.keychain",
        fallback_cache=state / "msal_token_cache.bin",
        interactive_timeout_s=5,
    )


@pytest.fixture
def dc_settings(settings: AuthSettings) -> AuthSettings:
    """Settings with the device-code rung allowed ([graph] allow_device_code = true)."""
    return dataclasses.replace(settings, allow_device_code=True)


@pytest.fixture
def broker_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the broker runtime imports (an Intune-managed arm64 Mac with Company Portal)."""
    monkeypatch.setattr(auth_mod, "broker_unavailable_reason", lambda: None)


@pytest.fixture
def broker_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The broker runtime raises the RuntimeError measured on this unmanaged Mac."""
    monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
    monkeypatch.setattr(auth_mod.platform, "machine", lambda: "arm64")

    def boom() -> object:
        raise RuntimeError(SSO_INACTIVE)

    monkeypatch.setattr(auth_mod, "_import_pymsalruntime", boom)


@pytest.fixture
def keychain_store() -> dict[str, str]:
    return {}


@pytest.fixture
def apps(monkeypatch: pytest.MonkeyPatch, keychain_store: dict[str, str]) -> list[FakeApp]:
    """Replace the Keychain and MSAL factories; returns the list of FakeApps built."""
    built: list[FakeApp] = []

    def fake_keychain(s: AuthSettings) -> FakeKeychain:
        return FakeKeychain(s.keychain_marker, keychain_store)

    def fake_app(s: AuthSettings, cache: Any, *, broker: bool = False) -> FakeApp:
        app = FakeApp(cache, broker=broker)
        built.append(app)
        return app

    monkeypatch.setattr(auth_mod, "_keychain_persistence", fake_keychain)
    monkeypatch.setattr(auth_mod, "_make_app", fake_app)
    return built


def break_keychain(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    monkeypatch.setattr(
        auth_mod, "_keychain_persistence", lambda s: FakeKeychain(s.keychain_marker, {}, broken=exc)
    )


# --- settings ----------


def _config(tmp_path: Path, graph: str, sources: str = "", *, tenant: str | None = TID) -> Config:
    tenant_line = f'tenant = "{tenant}"' if tenant is not None and "tenant" not in graph else ""
    text = f"""
[agentsync]
docs_repo = "{tmp_path / "docs"}"
state_dir = "{tmp_path / "state"}"
cache_dir = "{tmp_path / "cache"}"

[graph]
{graph}
{tenant_line}
{sources}
"""
    return parse_config(text, config_path=tmp_path / "sources.toml")


def _with_graph_attrs(cfg: Config, **extra: Any) -> Any:
    """A Config look-alike whose [graph] carries keys a newer config.py may add (cloud, allow_device_code,
    broker) and an optional [network] table; settings_from_config reads them with getattr."""
    g = cfg.graph
    graph = SimpleNamespace(
        client_id=g.client_id,
        tenant=extra.pop("tenant", g.tenant),
        scopes=g.scopes,
        company=g.company,
        base_url=extra.pop("base_url", g.base_url),
        **{k: v for k, v in extra.items() if k != "network"},
    )
    return SimpleNamespace(
        graph=graph,
        config_path=cfg.config_path,
        state_paths=cfg.state_paths,
        graph_scopes=cfg.graph_scopes,
        network=extra.get("network"),
    )


TEAMS_SOURCE = """
[[source]]
id = "team-general"
kind = "graph_teams"
team_id = "t"
channel_id = "19:c@thread.tacv2"
state = "{state}"
"""


def test_settings_require_client_id(sample_config: Config) -> None:
    with pytest.raises(ConfigError, match="client_id"):
        settings_from_config(sample_config)


def test_settings_default_scopes_and_paths(tmp_path: Path) -> None:
    cfg = _config(tmp_path, f'client_id = "{CID}"\ntenant = "{TID}"')
    s = settings_from_config(cfg)
    assert s.client_id == CID
    assert s.authority == f"https://login.microsoftonline.com/{TID}"
    assert s.scopes == SCOPES
    assert s.keychain_marker == cfg.state_paths.keychain_marker
    assert s.fallback_cache == cfg.state_paths.token_cache_fallback
    assert "offline_access" not in s.scopes and "ChannelMessage.Read.All" not in s.scopes


def test_settings_add_teams_scope_only_for_live_teams_source(tmp_path: Path) -> None:
    live = _config(tmp_path, f'client_id = "{CID}"', TEAMS_SOURCE.format(state="live"))
    paused = _config(tmp_path, f'client_id = "{CID}"', TEAMS_SOURCE.format(state="paused"))
    assert "ChannelMessage.Read.All" in settings_from_config(live).scopes
    assert "ChannelMessage.Read.All" not in settings_from_config(paused).scopes


def test_settings_strip_reserved_and_duplicate_scopes(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        f'client_id = "{CID}"\n'
        'scopes = ["offline_access", "Files.Read.All", "openid", "files.read.all", "Mail.Read"]',
    )
    assert settings_from_config(cfg).scopes == ("Files.Read.All", "Mail.Read")
    only_reserved = _config(tmp_path, f'client_id = "{CID}"\nscopes = ["offline_access", "profile"]')
    with pytest.raises(ConfigError, match="scopes"):
        settings_from_config(only_reserved)


def test_contract_constants() -> None:
    assert KEYCHAIN_SERVICE == "agentsync"
    assert KEYCHAIN_ACCOUNT == "msal_token_cache"
    assert "invalid_grant" in REAUTH_ERROR_CODES and "AADSTS50173" in REAUTH_ERROR_CODES


# --- persistence choice ----------


def test_keychain_persistence_uses_service_and_tenant_client_account(
    monkeypatch: pytest.MonkeyPatch, settings: AuthSettings
) -> None:
    seen: dict[str, Any] = {}

    class RecordingKeychain:
        def __init__(self, signal_location: str, service_name: str, account_name: str) -> None:
            seen.update(signal=signal_location, service=service_name, account=account_name)

    monkeypatch.setattr(auth_mod, "KeychainPersistence", RecordingKeychain)
    auth_mod._keychain_persistence(settings)
    assert seen == {
        "signal": str(settings.keychain_marker),
        "service": "agentsync",
        "account": f"msal_token_cache:{TID}:{CID}",
    }


def test_keychain_backend_when_keychain_answers(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    assert auth.cache_backend == "keychain"
    assert not settings.fallback_cache.exists()
    assert apps == []  # MSAL app (network discovery) is not built until needed


def test_file_fallback_when_keychain_unusable(
    monkeypatch: pytest.MonkeyPatch, settings: AuthSettings, caplog: pytest.LogCaptureFixture
) -> None:
    break_keychain(monkeypatch, OSError(-25308, "errSecInteractionNotAllowed"))
    monkeypatch.setattr(auth_mod, "_make_app", lambda s, c, broker=False: FakeApp(c))
    caplog.set_level(logging.WARNING, logger="agentsync.graph.auth")
    auth = MsalAuth(settings)
    assert auth.cache_backend == "file-0600"
    assert any("Keychain unavailable" in r.getMessage() for r in caplog.records)
    seed(auth._cache, token_response())
    mode = stat.S_IMODE(settings.fallback_cache.stat().st_mode)
    assert mode == 0o600
    assert auth.status().signed_in and auth.status().cache_backend == "file-0600"


def test_file_fallback_tightens_existing_file_mode(
    monkeypatch: pytest.MonkeyPatch, settings: AuthSettings
) -> None:
    settings.fallback_cache.write_text("{}", encoding="utf-8")
    settings.fallback_cache.chmod(0o644)
    break_keychain(monkeypatch, RuntimeError("no keychain"))
    MsalAuth(settings)
    assert stat.S_IMODE(settings.fallback_cache.stat().st_mode) == 0o600


def test_file_fallback_refuses_cloud_storage(monkeypatch: pytest.MonkeyPatch, settings: AuthSettings) -> None:
    break_keychain(monkeypatch, RuntimeError("no keychain"))
    cloudy = AuthSettings(
        client_id=CID,
        authority=settings.authority,
        scopes=SCOPES,
        keychain_marker=settings.keychain_marker,
        fallback_cache=Path.home() / "Library" / "CloudStorage" / "OneDrive-X" / "cache.bin",
    )
    with pytest.raises(AuthError, match="CloudStorage"):
        MsalAuth(cloudy)


def test_fallback_cache_migrates_into_keychain(
    monkeypatch: pytest.MonkeyPatch,
    settings: AuthSettings,
    keychain_store: dict[str, str],
    apps: list[FakeApp],
) -> None:
    # A session without Keychain access signed in to the file fallback ...
    break_keychain(monkeypatch, RuntimeError("no keychain"))
    headless = MsalAuth(settings)
    seed(headless._cache, token_response())
    assert settings.fallback_cache.is_file()
    # ... the next session with the Keychain moves it in and removes the plaintext file.
    monkeypatch.setattr(
        auth_mod, "_keychain_persistence", lambda s: FakeKeychain(s.keychain_marker, keychain_store)
    )
    auth = MsalAuth(settings)
    assert auth.cache_backend == "keychain"
    assert not settings.fallback_cache.exists()
    assert RT in keychain_store["item"]
    assert auth.status().signed_in and auth.status().username == "chris@contoso.com"


def test_stale_fallback_is_warned_about_not_deleted(
    settings: AuthSettings,
    keychain_store: dict[str, str],
    apps: list[FakeApp],
    caplog: pytest.LogCaptureFixture,
) -> None:
    keychain_store["item"] = "{}"
    settings.fallback_cache.write_text("{}", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="agentsync.graph.auth")
    MsalAuth(settings)
    assert settings.fallback_cache.exists()
    assert settings.keychain_marker.exists()
    assert any("plaintext fallback" in r.getMessage() for r in caplog.records)


def test_cache_is_shared_across_instances(apps: list[FakeApp], settings: AuthSettings) -> None:
    first = MsalAuth(settings)
    seed(first._cache, token_response())
    second = MsalAuth(settings)
    assert second.status().signed_in


# --- get_token ----------


def test_get_token_without_account_is_auth_required_offline(
    apps: list[FakeApp], settings: AuthSettings
) -> None:
    auth = MsalAuth(settings)
    with pytest.raises(AuthRequiredError, match="REAUTH_REQUIRED"):
        auth.get_token()
    assert apps == []  # decided from the cache alone, no MSAL app, no network


def test_get_token_silent_success(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    assert auth.get_token() == AT
    call = apps[0].silent_calls[0]
    assert call["scopes"] == list(SCOPES)
    assert call["force_refresh"] is False
    assert call["account"]["username"] == "chris@contoso.com"
    assert auth.get_token() == AT
    assert len(apps) == 1  # the app is built once


def test_refresh_token_forces_refresh_and_passes_claims(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    assert auth.refresh_token('{"access_token":{}}') == AT
    call = apps[0].silent_calls[0]
    assert call["force_refresh"] is True and call["claims"] == '{"access_token":{}}'


@pytest.mark.parametrize(
    "result",
    [
        {
            "error": "invalid_grant",
            "error_description": "AADSTS700082: The refresh token has expired due to inactivity.",
        },
        {
            "error": "interaction_required",
            "error_description": "AADSTS50076: MFA required",
            "error_codes": [50076],
        },
        {
            "error": "access_denied",
            "error_description": "AADSTS53003: Access has been blocked by CA policies.",
        },
        {
            "error": "invalid_grant",
            "error_codes": [50173],
            "error_description": "The provided grant has expired",
        },
        None,
    ],
)
def test_get_token_reauth_errors(
    apps: list[FakeApp], settings: AuthSettings, result: dict[str, Any] | None
) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    auth._app()  # build the fake so we can script it
    apps[0].silent_result = result
    with pytest.raises(AuthRequiredError, match="REAUTH_REQUIRED"):
        auth.get_token()
    assert len(apps[0].silent_calls) == 1  # never retried


def test_get_token_transient_errors_are_auth_error_not_reauth(
    apps: list[FakeApp], settings: AuthSettings
) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    auth._app()
    apps[0].silent_result = {"error": "temporarily_unavailable", "error_description": "AADSTS90033: busy"}
    with pytest.raises(AuthError) as ei:
        auth.get_token()
    assert not isinstance(ei.value, AuthRequiredError)
    apps[0].silent_raises = ConnectionError("offline")
    with pytest.raises(AuthError) as ei2:
        auth.get_token()
    assert not isinstance(ei2.value, AuthRequiredError)
    assert "ConnectionError" in str(ei2.value)


def test_msal_app_construction_failure_is_auth_error(
    monkeypatch: pytest.MonkeyPatch, settings: AuthSettings
) -> None:
    monkeypatch.setattr(auth_mod, "_keychain_persistence", lambda s: FakeKeychain(s.keychain_marker, {}))

    def boom(s: AuthSettings, cache: Any, *, broker: bool = False) -> Any:
        raise ValueError("Unable to get authority configuration")

    monkeypatch.setattr(auth_mod, "_make_app", boom)
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    with pytest.raises(AuthError, match="identity platform"):
        auth.get_token()


def test_multiple_accounts_pick_first_by_username(
    apps: list[FakeApp], settings: AuthSettings, caplog: pytest.LogCaptureFixture
) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response(username="zed@contoso.com", oid="oid-z"))
    seed(auth._cache, token_response(username="amy@contoso.com", oid="oid-a"))
    caplog.set_level(logging.WARNING, logger="agentsync.graph.auth")
    auth.get_token()
    assert apps[0].silent_calls[0]["account"]["username"] == "amy@contoso.com"
    assert any("2 accounts" in r.getMessage() for r in caplog.records)


# --- device-code login ----------


def test_login_device_code_success(apps: list[FakeApp], dc_settings: AuthSettings) -> None:
    auth = MsalAuth(dc_settings)
    seed(auth._cache, token_response(username="old@contoso.com", oid="oid-old"))
    emitted: list[str] = []
    status = auth.login_device_code(emitted.append)
    assert emitted == ["Go to https://microsoft.com/devicelogin"]
    assert apps[0].flow_scopes == list(SCOPES)
    assert status == AuthStatus(
        signed_in=True,
        username="chris@contoso.com",
        tenant_id=TID,
        cache_backend="keychain",
        scopes=tuple(sorted(SCOPES)),
        sign_in_method="device-code",
    )
    assert auth.last_token_source == "identity_provider"
    assert apps[0].removed == ["old@contoso.com"]  # one principal per machine
    assert [a["username"] for a in auth._cached_accounts()] == ["chris@contoso.com"]


def test_login_consent_required_names_the_it_action(apps: list[FakeApp], dc_settings: AuthSettings) -> None:
    auth = MsalAuth(dc_settings)
    auth._app()
    apps[0].device_result = {
        "error": "invalid_grant",
        "error_description": "AADSTS65001: The user or administrator has not consented to use the app.\n"
        "Trace ID: 123",
        "error_codes": [65001],
    }
    with pytest.raises(AuthError) as ei:
        auth.login_device_code(lambda m: None)
    msg = str(ei.value)
    assert "AADSTS65001" in msg and "admin consent" in msg
    assert f"https://login.microsoftonline.com/{TID}/adminconsent?client_id={CID}" in msg
    assert isinstance(ei.value, AuthBlockedError) and ei.value.state == "blocked: consent"
    assert "Trace ID" not in msg
    assert not auth.status().signed_in


def test_login_flow_cannot_start(apps: list[FakeApp], dc_settings: AuthSettings) -> None:
    auth = MsalAuth(dc_settings)
    auth._app()
    apps[0].flow = {
        "error": "invalid_client",
        "error_description": "AADSTS7000218: client_assertion required",
    }
    emitted: list[str] = []
    with pytest.raises(AuthError, match="public client"):
        auth.login_device_code(emitted.append)
    assert emitted == []


def test_login_expired_device_code(apps: list[FakeApp], dc_settings: AuthSettings) -> None:
    auth = MsalAuth(dc_settings)
    auth._app()
    apps[0].device_result = {"error": "expired_token", "error_description": "code expired"}
    with pytest.raises(AuthError, match="expired"):
        auth.login_device_code(lambda m: None)


def test_login_warns_on_partially_granted_scopes(
    apps: list[FakeApp], dc_settings: AuthSettings, caplog: pytest.LogCaptureFixture
) -> None:
    auth = MsalAuth(dc_settings)
    auth._app()
    apps[0].device_result = token_response(scopes=("Files.Read.All", "User.Read"))
    caplog.set_level(logging.WARNING, logger="agentsync.graph.auth")
    auth.login_device_code(lambda m: None)
    assert any("not granted" in r.getMessage() and "Mail.Read" in r.getMessage() for r in caplog.records)


# --- status / logout / hygiene ----------


def test_status_signed_out_reports_requested_scopes(apps: list[FakeApp], settings: AuthSettings) -> None:
    status = MsalAuth(settings).status()
    assert status == AuthStatus(False, None, None, "keychain", SCOPES)
    assert apps == []


def test_status_does_not_prune_or_need_network(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    before = auth._persistence.load()
    status = auth.status()
    assert status.signed_in and status.tenant_id == TID and status.username == "chris@contoso.com"
    assert auth._persistence.load() == before
    assert apps == []


def test_logout_removes_everything(
    apps: list[FakeApp], settings: AuthSettings, keychain_store: dict[str, str]
) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    settings.fallback_cache.write_text("{}", encoding="utf-8")
    auth.logout()
    assert not auth.status().signed_in
    assert RT not in keychain_store["item"] and AT not in keychain_store["item"]
    assert not settings.fallback_cache.exists()
    assert not MsalAuth(settings).status().signed_in
    with pytest.raises(AuthRequiredError):
        auth.get_token()


def test_logout_with_expired_access_token(
    apps: list[FakeApp], settings: AuthSettings, keychain_store: dict[str, str]
) -> None:
    auth = MsalAuth(settings)
    expired = token_response()
    expired["expires_in"] = -10
    seed(auth._cache, expired)
    auth.logout()
    assert AT not in keychain_store["item"] and RT not in keychain_store["item"]


def test_secrets_never_logged(
    monkeypatch: pytest.MonkeyPatch, dc_settings: AuthSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    break_keychain(monkeypatch, RuntimeError("no keychain"))
    built: list[FakeApp] = []

    def fake_app(s: AuthSettings, cache: Any, *, broker: bool = False) -> FakeApp:
        built.append(FakeApp(cache, broker=broker))
        return built[-1]

    monkeypatch.setattr(auth_mod, "_make_app", fake_app)
    auth = MsalAuth(dc_settings)
    auth.login_device_code(lambda m: None)
    auth.get_token()
    auth.status()
    auth.logout()
    assert caplog.records
    assert AT not in caplog.text and RT not in caplog.text


def test_describe_error_shapes() -> None:
    msg = auth_mod._describe_error(
        {"error": "invalid_grant", "error_codes": [50105], "error_description": "AADSTS50105: not assigned"},
        during="sign-in",
    )
    assert msg.startswith("sign-in failed: invalid_grant AADSTS50105")
    assert "Users and groups" in msg
    assert auth_mod._describe_error({}, during="x") == "x failed: unknown_error"


def test_real_msal_app_factory_is_lazy_and_typed(
    settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C15 §9.1 and §9.31/32: enable_broker_on_mac is passed, MSAL's HTTP is a truststore requests session."""
    captured: list[dict[str, Any]] = []

    class RecordingApp:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            captured.append({"client_id": client_id, **kwargs})

    monkeypatch.setattr(auth_mod.msal, "PublicClientApplication", RecordingApp)
    monkeypatch.setattr(net, "_run_scutil", lambda: None)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    auth_mod._make_app(settings, cache="CACHE", broker=True)
    auth_mod._make_app(settings, cache="CACHE")
    first, second = captured
    assert first["client_id"] == CID
    assert first["authority"] == settings.authority
    assert first["token_cache"] == "CACHE"
    assert first["app_name"] == "agentsync"
    assert first["enable_broker_on_mac"] is True and second["enable_broker_on_mac"] is False
    session = first["http_client"]
    assert isinstance(session, requests.Session) and session.trust_env is False
    adapter = session.get_adapter("https://login.microsoftonline.com/")
    assert isinstance(adapter.poolmanager.connection_pool_kw["ssl_context"], truststore.SSLContext)
    assert session.proxies == {}


def test_msal_broker_dependency_is_declared_and_installed() -> None:
    """C15 §9.1: msal[broker] is a dependency and the installed MSAL has the arm64 broker guard (>= 1.39)."""
    root = Path(__file__).resolve().parents[1]
    deps = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert any(d.replace(" ", "").startswith("msal[broker]") for d in deps)
    major, minor = (int(x) for x in importlib.metadata.version("msal").split(".")[:2])
    assert (major, minor) >= (1, 39)


def test_msal_http_client_uses_configured_proxy(
    settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(net, "_run_scutil", lambda: None)
    proxied = dataclasses.replace(settings, proxy="http://user:pw@proxy.corp:8080")
    session = auth_mod._http_client(proxied)
    assert session.proxies == {
        "https": "http://user:pw@proxy.corp:8080",
        "http": "http://user:pw@proxy.corp:8080",
    }


def test_msal_http_client_refuses_pac_only_network(
    settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C15 §9.33: a PAC-only system proxy fails `network-policy: PAC` instead of going direct."""
    pac = "<dictionary> {\n  ProxyAutoConfigEnable : 1\n  ProxyAutoConfigURLString : http://wpad/p.pac\n}\n"
    monkeypatch.setattr(net, "_run_scutil", lambda: pac)
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AuthError, match="network-policy: PAC"):
        auth_mod._http_client(settings)


def test_graph_client_401_drives_msal_force_refresh(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    with respx.mock() as router:
        router.get("https://graph.microsoft.com/v1.0/me").mock(
            side_effect=[
                httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}}),
                httpx.Response(200, json={"id": "me"}),
            ]
        )
        with GraphClient(
            auth, user_agent="NONISV|t|agentsync/0", sleep=lambda s: None, proxy=net.ProxySettings.direct()
        ) as c:
            assert c.get_json("me") == {"id": "me"}
    assert [call["force_refresh"] for call in apps[0].silent_calls] == [False, True]


# --- C15 §9.2: tenant-specific authority, sovereign clouds ----------


@pytest.mark.parametrize("tenant", ["organizations", "common", "consumers", "Organizations"])
def test_multi_tenant_authority_is_refused_citing_aadsts50194(tmp_path: Path, tenant: str) -> None:
    cfg = _config(tmp_path, f'client_id = "{CID}"\ntenant = "{tenant}"')
    with pytest.raises(ConfigError, match="AADSTS50194") as ei:
        settings_from_config(cfg)
    assert "tenant id" in str(ei.value)


def test_default_tenant_is_refused(tmp_path: Path) -> None:
    """config.py still defaults [graph] tenant to "organizations"; auth refuses it early."""
    cfg = _config(tmp_path, f'client_id = "{CID}"', tenant=None)
    assert cfg.graph.tenant == "organizations"
    with pytest.raises(ConfigError, match="AADSTS50194"):
        settings_from_config(cfg)


def test_tenant_domain_is_accepted(tmp_path: Path) -> None:
    s = settings_from_config(_config(tmp_path, f'client_id = "{CID}"', tenant="contoso.onmicrosoft.com"))
    assert s.authority == "https://login.microsoftonline.com/contoso.onmicrosoft.com"
    assert s.graph_root == "https://graph.microsoft.com"
    assert s.allow_device_code is False and s.use_broker is True and s.proxy is None


@pytest.mark.parametrize(
    ("base_url", "login", "graph"),
    [
        ("https://graph.microsoft.us/v1.0", "https://login.microsoftonline.us", "https://graph.microsoft.us"),
        (
            "https://dod-graph.microsoft.us/v1.0",
            "https://login.microsoftonline.us",
            "https://dod-graph.microsoft.us",
        ),
        (
            "https://microsoftgraph.chinacloudapi.cn/v1.0",
            "https://login.chinacloudapi.cn",
            "https://microsoftgraph.chinacloudapi.cn",
        ),
    ],
)
def test_sovereign_cloud_inferred_from_base_url(
    tmp_path: Path, base_url: str, login: str, graph: str
) -> None:
    """critic-sovereign-cloud: the login host and scope resource follow the configured Graph root."""
    s = settings_from_config(_config(tmp_path, f'client_id = "{CID}"\nbase_url = "{base_url}"'))
    assert s.authority == f"{login}/{TID}"
    assert s.graph_root == graph
    assert s.scopes == tuple(f"{graph}/{x}" for x in SCOPES)
    assert admin_consent_url(s) == f"{login}/{TID}/adminconsent?client_id={CID}"


def test_cloud_key_selects_endpoints_and_must_match_base_url(tmp_path: Path) -> None:
    cfg = _config(tmp_path, f'client_id = "{CID}"')
    usgov = _with_graph_attrs(cfg, cloud="usgov", base_url="https://graph.microsoft.us/v1.0")
    assert settings_from_config(usgov).authority == f"https://login.microsoftonline.us/{TID}"
    mismatch = _with_graph_attrs(cfg, cloud="usgov")  # base_url still the global Graph root
    with pytest.raises(ConfigError, match="not accepted by another"):
        settings_from_config(mismatch)
    with pytest.raises(ConfigError, match="cloud must be one of"):
        settings_from_config(_with_graph_attrs(cfg, cloud="mars"))


def test_newer_config_keys_are_read_when_present(tmp_path: Path) -> None:
    cfg = _config(tmp_path, f'client_id = "{CID}"')
    s = settings_from_config(
        _with_graph_attrs(
            cfg, allow_device_code=True, broker=False, network=SimpleNamespace(proxy="http://p.corp:3128")
        )
    )
    assert s.allow_device_code is True and s.use_broker is False and s.proxy == "http://p.corp:3128"


# --- C15 §9.1: the broker probe never raises ----------


def test_broker_probe_on_this_machine_never_raises() -> None:
    """Real pymsalruntime import: on an unmanaged Mac it raises RuntimeError at import (measured); the probe
    turns that into a reason string. On a managed arm64 Mac with Company Portal it returns None."""
    reason = auth_mod.broker_unavailable_reason()
    assert reason is None or isinstance(reason, str)


def test_broker_probe_reasons(monkeypatch: pytest.MonkeyPatch, broker_missing: None) -> None:
    reason = auth_mod.broker_unavailable_reason()
    assert reason is not None and "SSO extension is inactive" in reason and "Company Portal" in reason

    def missing() -> object:
        raise ImportError("No module named 'pymsalruntime'")

    monkeypatch.setattr(auth_mod, "_import_pymsalruntime", missing)
    assert "msal[broker]" in (auth_mod.broker_unavailable_reason() or "")
    monkeypatch.setattr(auth_mod, "_import_pymsalruntime", object)
    assert auth_mod.broker_unavailable_reason() is None
    monkeypatch.setattr(auth_mod.platform, "machine", lambda: "x86_64")
    assert "arm64" in (auth_mod.broker_unavailable_reason() or "")
    monkeypatch.setattr(auth_mod.sys, "platform", "linux")
    assert "macOS-only" in (auth_mod.broker_unavailable_reason() or "")


def test_broker_disabled_by_config_skips_the_probe(
    monkeypatch: pytest.MonkeyPatch, apps: list[FakeApp], settings: AuthSettings
) -> None:
    def must_not_probe() -> str | None:
        raise AssertionError("probed")

    monkeypatch.setattr(auth_mod, "broker_unavailable_reason", must_not_probe)
    auth = MsalAuth(dataclasses.replace(settings, use_broker=False))
    status = auth.login(lambda m: None)
    assert status.sign_in_method == "loopback"
    assert [a.broker for a in apps] == [False]


# --- C15 §9.3: the sign-in ladder, one test per rung ----------


def test_ladder_rung1_broker(apps: list[FakeApp], settings: AuthSettings, broker_ok: None) -> None:
    auth = MsalAuth(settings)
    emitted: list[str] = []
    status = auth.login(emitted.append)
    assert [a.broker for a in apps] == [True]  # the loopback app was never built
    call = apps[0].interactive_calls[0]
    assert call["parent_window_handle"] is FakeApp.CONSOLE_WINDOW_HANDLE
    assert call["scopes"] == list(SCOPES)
    assert status.signed_in and status.sign_in_method == "broker" and status.username == "chris@contoso.com"
    assert auth.last_token_source == "broker"
    assert any("broker" in m for m in emitted)


def test_ladder_rung2_loopback_when_broker_runtime_is_inactive(
    apps: list[FakeApp], settings: AuthSettings, broker_missing: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="agentsync.graph.auth")
    auth = MsalAuth(settings)
    emitted: list[str] = []
    status = auth.login(emitted.append)
    assert [a.broker for a in apps] == [False]  # no broker app: the probe already said why
    call = apps[0].interactive_calls[0]
    assert call["prompt"] == "select_account" and call["timeout"] == 5
    assert "parent_window_handle" not in call
    call["auth_uri_callback"]("http://localhost:1234/?welcome")  # a headless session is told where to go
    assert status.sign_in_method == "loopback" and auth.last_token_source == "identity_provider"
    assert any(LOOPBACK_REDIRECT_URI in m for m in emitted)
    assert any("visit this URL" in m for m in emitted)
    assert any(
        "broker rung skipped" in r.getMessage() and "SSO extension" in r.getMessage() for r in caplog.records
    )


def test_ladder_rung3_device_code_only_when_allowed(
    apps: list[FakeApp], dc_settings: AuthSettings, broker_missing: None
) -> None:
    auth = MsalAuth(dc_settings)
    auth._app()
    apps[0].interactive_raises = BrowserInteractionTimeoutError("User did not complete the flow in time")
    emitted: list[str] = []
    status = auth.login(emitted.append)
    assert status.sign_in_method == "device-code"
    assert apps[0].flow_scopes == list(SCOPES)
    assert "Go to https://microsoft.com/devicelogin" in emitted
    assert any("allow_device_code" in m for m in emitted)


def test_ladder_stops_before_device_code_when_not_allowed(
    apps: list[FakeApp], settings: AuthSettings, broker_missing: None
) -> None:
    auth = MsalAuth(settings)
    auth._app()
    apps[0].interactive_raises = BrowserInteractionTimeoutError("User did not complete the flow in time")
    with pytest.raises(AuthError, match="allow_device_code") as ei:
        auth.login(lambda m: None)
    assert "broker:" in str(ei.value) and "loopback:" in str(ei.value)
    assert apps[0].flow_scopes is None  # device flow never initiated


def test_login_device_code_refused_unless_allowed(apps: list[FakeApp], settings: AuthSettings) -> None:
    with pytest.raises(AuthError, match="allow_device_code"):
        MsalAuth(settings).login_device_code(lambda m: None)
    assert apps == []


def test_broker_redirect_uri_missing_falls_through_to_loopback(
    apps: list[FakeApp], settings: AuthSettings, broker_ok: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="agentsync.graph.auth")
    auth = MsalAuth(settings)
    broker_app = auth._app(broker=True)
    broker_app.interactive_raises = ValueError(
        "MsalRuntime needs the current app to register these redirect_uri"
    )
    status = auth.login(lambda m: None)
    assert status.sign_in_method == "loopback"
    assert any(BROKER_REDIRECT_URI in r.getMessage() for r in caplog.records)


def test_msal_disabling_the_broker_falls_through(
    apps: list[FakeApp], settings: AuthSettings, broker_ok: None
) -> None:
    auth = MsalAuth(settings)
    auth._app(broker=True)._enable_broker = False  # MSAL logged "Broker is unavailable ... fallback"
    assert auth.login(lambda m: None).sign_in_method == "loopback"
    assert apps[0].interactive_calls == []


def test_broker_device_block_stops_the_ladder(
    apps: list[FakeApp], settings: AuthSettings, broker_ok: None
) -> None:
    auth = MsalAuth(dataclasses.replace(settings, allow_device_code=True))
    auth._app(broker=True).interactive_result = {
        "error": "access_denied",
        "error_codes": [53000],
        "error_description": "AADSTS53000: Device is not in required device state: compliant.",
    }
    with pytest.raises(AuthBlockedError) as ei:
        auth.login(lambda m: None)
    assert ei.value.state == "blocked: device" and ei.value.aadsts == "AADSTS53000"
    assert "Company Portal" in str(ei.value)
    assert [a.broker for a in apps] == [True]  # neither the browser nor device code was tried


def test_broker_cancel_stops_the_ladder(apps: list[FakeApp], settings: AuthSettings, broker_ok: None) -> None:
    auth = MsalAuth(settings)
    auth._app(broker=True).interactive_result = {
        "error": "broker_error",
        "error_description": "User canceled",
        "_broker_status": "Response_Status.Status_UserCanceled",
    }
    with pytest.raises(AuthError, match="cancelled"):
        auth.login(lambda m: None)
    assert [a.broker for a in apps] == [True]


def test_unclassified_broker_error_falls_through(
    apps: list[FakeApp], settings: AuthSettings, broker_ok: None
) -> None:
    auth = MsalAuth(settings)
    auth._app(broker=True).interactive_result = {"error": "broker_error", "error_description": "boom"}
    assert auth.login(lambda m: None).sign_in_method == "loopback"


def test_loopback_consent_error_stops_with_admin_consent_url(
    apps: list[FakeApp], dc_settings: AuthSettings, broker_missing: None
) -> None:
    auth = MsalAuth(dc_settings)
    auth._app().interactive_result = {
        "error": "invalid_client",
        "error_codes": [65001],
        "error_description": "AADSTS65001: The user or administrator has not consented.",
    }
    with pytest.raises(AuthBlockedError) as ei:
        auth.login(lambda m: None)
    assert ei.value.state == "blocked: consent"
    assert f"/{TID}/adminconsent?client_id={CID}" in str(ei.value)
    assert apps[0].flow_scopes is None


def test_loopback_redirect_missing_falls_through_to_device_code(
    apps: list[FakeApp], dc_settings: AuthSettings, broker_missing: None
) -> None:
    auth = MsalAuth(dc_settings)
    auth._app().interactive_result = {
        "error": "invalid_request",
        "error_codes": [50011],
        "error_description": "AADSTS50011: The redirect URI 'http://localhost:5555' does not match.",
    }
    assert auth.login(lambda m: None).sign_in_method == "device-code"


def test_loopback_user_cancel_does_not_fall_through(
    apps: list[FakeApp], dc_settings: AuthSettings, broker_missing: None
) -> None:
    auth = MsalAuth(dc_settings)
    auth._app().interactive_result = {"error": "access_denied", "error_description": "the user cancelled"}
    with pytest.raises(AuthError, match="cancelled"):
        auth.login(lambda m: None)
    assert apps[0].flow_scopes is None


# --- C15 §9.5: AADSTS classification (table-driven, never retried) ----------

C15_TABLE = [
    (50076, "reauth-required"),
    (50079, "reauth-required"),
    (50097, "blocked: device"),
    (53000, "blocked: device"),
    (53001, "blocked: device"),
    (530003, "blocked: device"),
    (53003, "blocked: policy"),
    (70043, "reauth-required"),
    (50173, "reauth-required"),
    (65001, "blocked: consent"),
    (90094, "blocked: consent"),
    (90095, "blocked: consent"),
    (50105, "blocked: assignment"),
    (50011, "config-invalid"),
    (50194, "config-invalid"),
    (135011, "blocked: device"),
]


@pytest.mark.parametrize(("code", "state"), C15_TABLE)
def test_aadsts_classification_table(code: int, state: str) -> None:
    by_codes = {"error": "invalid_grant", "error_codes": [code], "error_description": "x"}
    by_text = {"error": "access_denied", "error_description": f"AADSTS{code}: something\nTrace ID: 1"}
    assert classify_auth_error(by_codes) == state
    assert classify_auth_error(by_text) == state


@pytest.mark.parametrize(("code", "state"), C15_TABLE)
def test_silent_path_maps_each_code_and_never_retries(
    apps: list[FakeApp], settings: AuthSettings, code: int, state: str
) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    auth._app()
    apps[0].silent_result = {
        "error": "invalid_grant",
        "error_codes": [code],
        "error_description": f"AADSTS{code}: refused",
    }
    with pytest.raises(AuthRequiredError, match="REAUTH_REQUIRED") as ei:
        auth.get_token()
    if state == "reauth-required":
        assert not isinstance(ei.value, AuthBlockedError)
    else:
        assert isinstance(ei.value, AuthBlockedError)
        assert ei.value.state == state and ei.value.aadsts == f"AADSTS{code}"
        assert state in str(ei.value)
    assert len(apps[0].silent_calls) == 1


def test_unknown_error_is_not_classified() -> None:
    assert classify_auth_error({"error": "temporarily_unavailable", "error_codes": [90033]}) is None
    assert classify_auth_error({"error": "invalid_grant"}) == "reauth-required"


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        (53003, "Conditional Access"),
        (53000, "compliant"),
        (50194, "[graph] tenant"),
        (50011, BROKER_REDIRECT_URI),
        (50105, "Users and groups"),
    ],
)
def test_actionable_messages(code: int, needle: str) -> None:
    msg = auth_mod._describe_error(
        {"error": "x", "error_codes": [code], "error_description": f"AADSTS{code}: y"}, during="sign-in"
    )
    assert needle in msg


# --- silent path: broker accounts, token_source, TLS ----------


def test_silent_broker_account_uses_broker_app(
    apps: list[FakeApp], settings: AuthSettings, broker_ok: None
) -> None:
    auth = MsalAuth(settings)
    seed(
        auth._cache, {k: v for k, v in token_response().items() if k != "refresh_token"}, grant_type="broker"
    )
    assert auth.status().signed_in and auth.status().sign_in_method == "broker"
    auth._app(broker=True).silent_result = {"access_token": AT, "token_source": "broker"}
    assert auth.get_token() == AT
    assert [a.broker for a in apps] == [True]
    assert apps[0].interactive_calls == []  # C15 §9.6: background runs are silent only, no UI
    assert auth.last_token_source == "broker"


def test_silent_broker_account_without_broker_is_reauth(
    apps: list[FakeApp], settings: AuthSettings, broker_missing: None
) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response(), grant_type="broker")
    with pytest.raises(AuthRequiredError, match="broker") as ei:
        auth.get_token()
    assert "SSO extension" in str(ei.value)
    assert apps == []  # no app, no network, no UI


def test_silent_non_broker_account_never_loads_the_broker(
    monkeypatch: pytest.MonkeyPatch, apps: list[FakeApp], settings: AuthSettings
) -> None:
    def must_not_probe() -> str | None:
        raise AssertionError("background runs of a loopback account must not import pymsalruntime")

    monkeypatch.setattr(auth_mod, "broker_unavailable_reason", must_not_probe)
    auth = MsalAuth(settings)
    seed(auth._cache, token_response(), grant_type="authorization_code")
    apps_result = {"access_token": AT, "token_source": "cache"}
    auth._app().silent_result = apps_result
    assert auth.get_token() == AT
    assert auth.last_token_source == "cache"
    assert auth.status().sign_in_method == "loopback"


def test_silent_tls_failure_is_named_not_reauth(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    seed(auth._cache, token_response())
    auth._app()
    err = requests.exceptions.SSLError("HTTPSConnectionPool: certificate verify failed")
    err.__cause__ = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer")
    apps[0].silent_raises = err
    with pytest.raises(AuthError, match="network-policy: TLS") as ei:
        auth.get_token()
    assert not isinstance(ei.value, AuthRequiredError)
    assert "keychain" in str(ei.value)


# --- the loopback rung through the real MSAL (no network: discovery is canned, no browser opens) ----------


def _discovery_response(url: str) -> requests.Response:
    base = f"https://login.microsoftonline.com/{TID}"
    body = {
        "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{base}/oauth2/v2.0/token",
        "device_authorization_endpoint": f"{base}/oauth2/v2.0/devicecode",
        "issuer": f"{base}/v2.0",
    }
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(body).encode()
    response.headers["Content-Type"] = "application/json"
    response.url = url
    return response


class CannedHttp:
    """MSAL http_client: answers OpenID discovery, refuses everything else (no network)."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self.urls.append(url)
        if "openid-configuration" in url:
            return _discovery_response(url)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        raise AssertionError(f"unexpected POST {url}")

    def close(self) -> None:
        return None


def test_real_msal_loopback_rung_uses_pkce_on_localhost(
    monkeypatch: pytest.MonkeyPatch, settings: AuthSettings, broker_missing: None
) -> None:
    """C15 §9.3 rung 2 with the real msal: auth code + PKCE (S256), redirect http://localhost:<port>; with no
    browser the URL goes to ``emit`` and a timeout falls through (device code off -> AuthError)."""
    http = CannedHttp()
    monkeypatch.setattr(auth_mod, "_http_client", lambda s: http)
    monkeypatch.setattr(auth_mod, "_keychain_persistence", lambda s: FakeKeychain(s.keychain_marker, {}))
    monkeypatch.setattr(authcode, "_browse", lambda *a, **k: False)  # never open a real browser
    auth = MsalAuth(dataclasses.replace(settings, interactive_timeout_s=1))
    emitted: list[str] = []
    with pytest.raises(AuthError, match="allow_device_code") as ei:
        auth.login(emitted.append)
    assert "did not complete within 1s" in str(ei.value)
    urls = [m.split("visit this URL on this Mac: ", 1)[1] for m in emitted if "visit this URL" in m]
    assert len(urls) == 1
    uri = urls[0]
    assert uri.startswith(f"https://login.microsoftonline.com/{TID}/oauth2/v2.0/authorize?")
    assert "code_challenge_method=S256" in uri and "code_challenge=" in uri
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A" in uri
    assert "prompt=select_account" in uri
    assert all("openid-configuration" in u for u in http.urls)
