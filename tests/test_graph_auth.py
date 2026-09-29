"""MsalAuth: settings, Keychain/file persistence choice, silent tokens, device-code login, status, logout.

No test touches the login Keychain or the network: the Keychain persistence factory and the MSAL app factory
are replaced with in-memory fakes; the token cache itself is msal-extensions' real PersistedTokenCache.
"""

from __future__ import annotations

import base64
import json
import logging
import stat
from pathlib import Path
from typing import Any

import httpx
import msal
import pytest
import respx
from msal_extensions.persistence import PersistenceNotFound

from agentsync.config import Config, parse_config
from agentsync.errors import AuthError, AuthRequiredError, ConfigError
from agentsync.graph import auth as auth_mod
from agentsync.graph.auth import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    REAUTH_ERROR_CODES,
    AuthSettings,
    AuthStatus,
    MsalAuth,
    settings_from_config,
)
from agentsync.graph.client import GraphClient

CID = "11111111-2222-3333-4444-555555555555"
TID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OID = "99999999-8888-7777-6666-555555555555"
AT = "SECRET-AT-0123456789"
RT = "SECRET-RT-9876543210"
SCOPES = ("Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read")


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


def seed(cache: Any, response: dict[str, Any], scopes: tuple[str, ...] = SCOPES) -> None:
    """Add a token response to the cache exactly the way MSAL does after a sign-in."""
    tid = response["id_token_claims"]["tid"]
    cache.add(
        {
            "client_id": CID,
            "scope": list(scopes),
            "token_endpoint": f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
            "response": dict(response),
            "grant_type": "device_code",
        }
    )


class FakeApp:
    """The slice of msal.PublicClientApplication that MsalAuth uses, over the real persisted cache."""

    def __init__(self, cache: Any) -> None:
        self.cache = cache
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
        return dict(self.device_result)

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
    )


@pytest.fixture
def keychain_store() -> dict[str, str]:
    return {}


@pytest.fixture
def apps(monkeypatch: pytest.MonkeyPatch, keychain_store: dict[str, str]) -> list[FakeApp]:
    """Replace the Keychain and MSAL factories; returns the list of FakeApps built."""
    built: list[FakeApp] = []

    def fake_keychain(s: AuthSettings) -> FakeKeychain:
        return FakeKeychain(s.keychain_marker, keychain_store)

    def fake_app(s: AuthSettings, cache: Any) -> FakeApp:
        app = FakeApp(cache)
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


def _config(tmp_path: Path, graph: str, sources: str = "") -> Config:
    text = f"""
[agentsync]
docs_repo = "{tmp_path / "docs"}"
state_dir = "{tmp_path / "state"}"
cache_dir = "{tmp_path / "cache"}"

[graph]
{graph}
{sources}
"""
    return parse_config(text, config_path=tmp_path / "sources.toml")


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
    monkeypatch.setattr(auth_mod, "_make_app", lambda s, c: FakeApp(c))
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

    def boom(s: AuthSettings, cache: Any) -> Any:
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


def test_login_device_code_success(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
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
    )
    assert apps[0].removed == ["old@contoso.com"]  # one principal per machine
    assert [a["username"] for a in auth._cached_accounts()] == ["chris@contoso.com"]


def test_login_consent_required_names_the_it_action(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
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
    assert "Trace ID" not in msg
    assert not auth.status().signed_in


def test_login_flow_cannot_start(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    auth._app()
    apps[0].flow = {
        "error": "invalid_client",
        "error_description": "AADSTS7000218: client_assertion required",
    }
    emitted: list[str] = []
    with pytest.raises(AuthError, match="public client"):
        auth.login_device_code(emitted.append)
    assert emitted == []


def test_login_expired_device_code(apps: list[FakeApp], settings: AuthSettings) -> None:
    auth = MsalAuth(settings)
    auth._app()
    apps[0].device_result = {"error": "expired_token", "error_description": "code expired"}
    with pytest.raises(AuthError, match="expired"):
        auth.login_device_code(lambda m: None)


def test_login_warns_on_partially_granted_scopes(
    apps: list[FakeApp], settings: AuthSettings, caplog: pytest.LogCaptureFixture
) -> None:
    auth = MsalAuth(settings)
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
    monkeypatch: pytest.MonkeyPatch, settings: AuthSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    break_keychain(monkeypatch, RuntimeError("no keychain"))
    built: list[FakeApp] = []

    def fake_app(s: AuthSettings, cache: Any) -> FakeApp:
        built.append(FakeApp(cache))
        return built[-1]

    monkeypatch.setattr(auth_mod, "_make_app", fake_app)
    auth = MsalAuth(settings)
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
    captured: dict[str, Any] = {}

    class RecordingApp:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            captured.update(client_id=client_id, **kwargs)

    monkeypatch.setattr(auth_mod.msal, "PublicClientApplication", RecordingApp)
    auth_mod._make_app(settings, cache="CACHE")
    assert captured["client_id"] == CID
    assert captured["authority"] == settings.authority
    assert captured["token_cache"] == "CACHE"
    assert captured["app_name"] == "agentsync"


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
        with GraphClient(auth, user_agent="NONISV|t|agentsync/0", sleep=lambda s: None) as c:
            assert c.get_json("me") == {"id": "me"}
    assert [call["force_refresh"] for call in apps[0].silent_calls] == [False, True]
