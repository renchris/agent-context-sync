"""agentsync.net: truststore TLS, proxy precedence (config > env > macOS), PAC fail-closed, reachability.

No test leaves the machine: scutil output is injected, httpx runs over MockTransport, and the "real" tests use
a TLS server and an HTTP proxy bound to 127.0.0.1 in a thread.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import socketserver
import ssl
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import httpcore
import httpx
import pytest
import requests
import truststore
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from agentsync import net
from agentsync.graph.client import GraphClient
from agentsync.graph.errors import NetworkPolicyError

# Output of `scutil --proxy` on this Mac today (measured): no proxy, default exceptions.
SCUTIL_NONE = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
  }
  FTPPassive : 1
}
"""

SCUTIL_MANUAL = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
    2 : *.corp.example
  }
  ExcludeSimpleHostnames : 1
  HTTPEnable : 1
  HTTPPort : 3128
  HTTPProxy : proxy.corp.example
  HTTPSEnable : 1
  HTTPSPort : 3129
  HTTPSProxy : proxy.corp.example
  __SCOPED__ : <dictionary> {
    en0 : <dictionary> {
      HTTPSEnable : 1
      HTTPSProxy : scoped.invalid
    }
  }
}
"""

SCUTIL_PAC = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
  }
  ProxyAutoConfigEnable : 1
  ProxyAutoConfigURLString : http://wpad.corp.example/proxy.pac
}
"""

SCUTIL_WPAD = """<dictionary> {
  ProxyAutoDiscoveryEnable : 1
}
"""

GRAPH = "https://graph.microsoft.com/v1.0/"


def no_env() -> dict[str, str]:
    return {}


# --- trust store (C15 §9.31, §9.32) ----------


def test_ssl_context_is_truststore() -> None:
    ctx = net.ssl_context()
    assert isinstance(ctx, truststore.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_inject_system_trust_is_idempotent_and_reversible() -> None:
    # importing agentsync.cli injects at import time (C15 req 32), so the session may start injected
    was_injected = net.system_trust_injected()
    if was_injected:
        truststore.extract_from_ssl()
    try:
        assert not net.system_trust_injected()
        try:
            net.inject_system_trust()
            net.inject_system_trust()
            assert net.system_trust_injected()
            assert isinstance(ssl.create_default_context(), truststore.SSLContext)
        finally:
            truststore.extract_from_ssl()
        assert not net.system_trust_injected()
    finally:
        if was_injected:
            truststore.inject_into_ssl()


# --- scutil parsing ----------


def test_parse_scutil_this_mac() -> None:
    sp = net.parse_scutil_proxy(SCUTIL_NONE)
    assert sp == net.SystemProxy(exceptions=("*.local", "169.254/16"))


def test_parse_scutil_manual_proxy_ignores_scoped_dicts() -> None:
    sp = net.parse_scutil_proxy(SCUTIL_MANUAL)
    assert sp.https_proxy == "http://proxy.corp.example:3129"
    assert sp.http_proxy == "http://proxy.corp.example:3128"
    assert sp.exceptions == ("*.local", "169.254/16", "*.corp.example")
    assert sp.exclude_simple and not sp.pac_enabled


def test_parse_scutil_pac_and_wpad() -> None:
    pac = net.parse_scutil_proxy(SCUTIL_PAC)
    assert pac.pac_enabled and pac.pac_url == "http://wpad.corp.example/proxy.pac" and pac.https_proxy is None
    assert net.parse_scutil_proxy(SCUTIL_WPAD).wpad_enabled


def test_system_proxy_uses_runner_and_tolerates_failure() -> None:
    assert net.system_proxy(lambda: SCUTIL_MANUAL).https_proxy == "http://proxy.corp.example:3129"
    assert net.system_proxy(lambda: None) == net.SystemProxy()


def test_real_scutil_call_does_not_raise() -> None:
    sp = net.system_proxy()
    assert isinstance(sp, net.SystemProxy)


# --- precedence (C15 §9.33) ----------


def test_precedence_config_then_env_then_system() -> None:
    sysp = net.parse_scutil_proxy(SCUTIL_MANUAL)
    env = {"HTTPS_PROXY": "http://env.proxy:8080"}
    cfg = net.resolve_proxy("http://cfg.proxy:9000", environ=env, system=sysp)
    assert (cfg.url, cfg.source) == ("http://cfg.proxy:9000", "config")
    from_env = net.resolve_proxy(None, environ=env, system=sysp)
    assert (from_env.url, from_env.source) == ("http://env.proxy:8080", "env")
    from_sys = net.resolve_proxy(None, environ=no_env(), system=sysp)
    assert (from_sys.url, from_sys.source) == ("http://proxy.corp.example:3129", "system")
    none = net.resolve_proxy(None, environ=no_env(), system=net.SystemProxy())
    assert (none.url, none.source, none.policy_error) == (None, "none", None)


def test_env_variants_and_bare_host_port() -> None:
    lower = net.resolve_proxy(None, environ={"https_proxy": "proxy.corp:3128"}, system=net.SystemProxy())
    assert lower.url == "http://proxy.corp:3128"
    all_proxy = net.resolve_proxy(None, environ={"ALL_PROXY": "http://all:1"}, system=net.SystemProxy())
    assert all_proxy.url == "http://all:1"


def test_config_direct_overrides_everything_including_pac() -> None:
    sysp = net.parse_scutil_proxy(SCUTIL_PAC)
    direct = net.resolve_proxy("direct", environ={"HTTPS_PROXY": "http://env:1"}, system=sysp)
    assert direct.url is None and direct.source == "config" and direct.policy_error is None
    assert any("unsupported" in w for w in direct.warnings)


def test_pac_only_fails_closed_with_network_policy() -> None:
    settings = net.resolve_proxy(None, environ=no_env(), system=net.parse_scutil_proxy(SCUTIL_PAC))
    assert settings.url is None
    assert settings.policy_error is not None and settings.policy_error.startswith("network-policy: PAC")
    assert "http://wpad.corp.example/proxy.pac" in settings.policy_error
    assert settings.describe() == settings.policy_error


def test_pac_with_explicit_env_proxy_warns_only() -> None:
    sysp = net.parse_scutil_proxy(SCUTIL_PAC)
    settings = net.resolve_proxy(None, environ={"HTTPS_PROXY": "http://env:1"}, system=sysp)
    assert settings.policy_error is None and settings.url == "http://env:1"
    lines = net.proxy_diagnostics(settings)
    assert lines[0] == "proxy: http://env:1 (source: env)"
    assert any("PAC" in line and "unsupported" in line for line in lines[1:])


def test_wpad_alone_is_a_warning_not_a_failure() -> None:
    settings = net.resolve_proxy(None, environ=no_env(), system=net.parse_scutil_proxy(SCUTIL_WPAD))
    assert settings.policy_error is None
    assert any("WPAD" in w for w in settings.warnings)


def test_no_proxy_union_and_bypass_rules() -> None:
    sysp = net.parse_scutil_proxy(SCUTIL_MANUAL)
    s = net.resolve_proxy(None, environ={"NO_PROXY": ".internal.example, localhost"}, system=sysp)
    assert s.no_proxy == (".internal.example", "localhost", "*.local", "169.254/16", "*.corp.example")
    assert s.proxy_for("https://graph.microsoft.com/v1.0/me") == "http://proxy.corp.example:3129"
    assert s.proxy_for("https://a.internal.example/") is None
    assert s.proxy_for("https://internal.example/") is None
    assert s.proxy_for("https://printer.local/") is None
    assert s.proxy_for("https://x.corp.example/") is None
    assert s.proxy_for("https://intranet/") is None  # ExcludeSimpleHostnames
    assert s.proxy_for("https://notcorp.example/") == "http://proxy.corp.example:3129"
    assert net.ProxySettings(url="http://p:1", no_proxy=("*",)).proxy_for(GRAPH) is None


def test_proxy_credentials_are_never_described() -> None:
    s = net.resolve_proxy("http://alice:s3cret@proxy.corp:8080", environ=no_env(), system=net.SystemProxy())
    assert "s3cret" not in s.describe() and "alice" not in s.describe()
    assert net.redact_proxy("http://alice:s3cret@proxy.corp:8080/") == "http://proxy.corp:8080/"
    assert net.redact_proxy(None) == "direct"


# --- wiring into httpx and requests ----------


def test_httpx_mounts_direct_and_proxied() -> None:
    ctx = net.ssl_context()
    assert net.httpx_mounts(net.ProxySettings.direct(), ctx) == {}
    s = net.ProxySettings(url="http://proxy.corp:3128", no_proxy=(".corp.example", "10.0.0.1", "169.254/16"))
    mounts = net.httpx_mounts(s, ctx)
    assert set(mounts) == {"all://", "all://*corp.example", "all://10.0.0.1"}
    assert mounts["all://*corp.example"] is None and mounts["all://10.0.0.1"] is None
    transport = mounts["all://"]
    assert isinstance(transport, httpx.HTTPTransport)
    pool = transport._pool
    assert isinstance(pool, httpcore.HTTPProxy)
    assert pool._ssl_context is ctx


def test_requests_session_truststore_proxy_and_no_env() -> None:
    ctx = net.ssl_context()
    s = net.ProxySettings(url="http://proxy.corp:3128", no_proxy=("login.corp.example",))
    session = net.requests_session(s, target_url="https://login.microsoftonline.com/tid", ctx=ctx)
    assert session.trust_env is False
    assert session.proxies == {"https": "http://proxy.corp:3128", "http": "http://proxy.corp:3128"}
    adapter = session.get_adapter("https://login.microsoftonline.com/")
    assert adapter.poolmanager.connection_pool_kw["ssl_context"] is ctx
    manager = adapter.proxy_manager_for("http://proxy.corp:3128")
    assert manager.connection_pool_kw["ssl_context"] is ctx
    bypassed = net.requests_session(s, target_url="https://login.corp.example/tid", ctx=ctx)
    assert bypassed.proxies == {}


def test_requests_session_applies_a_default_timeout() -> None:
    seen: dict[str, object] = {}

    class Recorder(requests.adapters.BaseAdapter):
        def send(self, request: requests.PreparedRequest, **kwargs: object) -> requests.Response:  # type: ignore[override]
            seen.update(kwargs)
            response = requests.Response()
            response.status_code = 200
            response.request = request
            return response

        def close(self) -> None:
            return None

    session = net.requests_session(net.ProxySettings.direct(), target_url="https://x.invalid/", timeout_s=7.5)
    session.mount("https://x.invalid/", Recorder())
    session.get("https://x.invalid/")
    assert seen["timeout"] == 7.5


# --- error classification ----------


def test_classify_transport_errors() -> None:
    cert = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed")
    assert net.classify_transport_error(cert) == net.POLICY_TLS
    chained = httpx.ConnectError("handshake failed")
    chained.__cause__ = ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
    assert net.classify_transport_error(chained) == net.POLICY_TLS
    assert (
        net.classify_transport_error(httpx.ProxyError("407 Proxy Authentication Required"))
        == net.POLICY_PROXY
    )
    assert net.classify_transport_error(requests.exceptions.ProxyError("refused")) == net.POLICY_PROXY
    assert (
        net.classify_transport_error(httpx.ConnectError("[Errno 8] nodename nor servname provided")) is None
    )
    assert net.classify_transport_error(httpx.ConnectTimeout("timed out")) is None


# --- reachability (C15 §9.34): a proxied network is not "offline" ----------


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _raise(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def test_any_http_answer_is_online() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(401)

    r = net.probe_reachability(GRAPH, net.ProxySettings.direct(), transport=_transport(handler))
    assert r.online and r.status == 401 and not r.skipped and not r.failed
    assert seen == ["HEAD"]


def test_offline_is_skipped_not_failed() -> None:
    r = net.probe_reachability(
        GRAPH,
        net.ProxySettings.direct(),
        transport=_transport(_raise(httpx.ConnectError("nodename nor servname"))),
    )
    assert r.state == net.REACH_OFFLINE and r.skipped and not r.failed


def test_certificate_failure_is_failed_network_policy_tls() -> None:
    exc = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    r = net.probe_reachability(GRAPH, net.ProxySettings.direct(), transport=_transport(_raise(exc)))
    assert r.state == net.POLICY_TLS and r.failed and not r.skipped
    assert "keychain" in r.detail


def test_proxy_refusal_and_407_are_failed_network_policy_proxy() -> None:
    proxied = net.ProxySettings(url="http://proxy.corp:3128", source="env")
    r = net.probe_reachability(
        GRAPH, proxied, transport=_transport(_raise(httpx.ProxyError("403 Forbidden")))
    )
    assert r.state == net.POLICY_PROXY and r.failed and r.via == "http://proxy.corp:3128"
    r407 = net.probe_reachability(GRAPH, proxied, transport=_transport(lambda req: httpx.Response(407)))
    assert r407.state == net.POLICY_PROXY and r407.status == 407


def test_pac_only_is_failed_without_sending() -> None:
    settings = net.resolve_proxy(None, environ=no_env(), system=net.parse_scutil_proxy(SCUTIL_PAC))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not go direct on a PAC network")

    r = net.probe_reachability(GRAPH, settings, transport=_transport(handler))
    assert r.state == net.POLICY_PAC and r.failed


# --- real loopback servers ----------


class _ProxyHandler(socketserver.StreamRequestHandler):
    """A tiny forward proxy: answers absolute-form requests itself, and 407s every CONNECT."""

    def handle(self) -> None:
        line = self.rfile.readline().decode("latin-1").strip()
        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
            pass
        self.server.seen.append(line)  # type: ignore[attr-defined]
        if line.startswith("CONNECT "):
            self.wfile.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
        else:
            self.wfile.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


@pytest.fixture
def local_proxy() -> Iterator[tuple[str, list[str]]]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ProxyHandler)
    server.daemon_threads = True
    server.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.seen  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_explicit_proxy_network_is_online_not_offline(local_proxy: tuple[str, list[str]]) -> None:
    """The measured failure of `nc -z graph.microsoft.com 443`: on a proxy-only network the direct route is
    dead but the proxy answers. Here the direct route is a closed loopback port (no DNS, no network): direct
    is offline, and the same URL through the proxy is online."""
    proxy_url, seen = local_proxy
    url = f"http://127.0.0.1:{_closed_port()}/v1.0/"
    direct = net.probe_reachability(url, net.ProxySettings.direct(), timeout_s=5)
    assert direct.state == net.REACH_OFFLINE and direct.skipped
    settings = net.resolve_proxy(proxy_url, environ=no_env(), system=net.SystemProxy())
    r = net.probe_reachability(url, settings, timeout_s=5)
    assert r.online and r.status == 401 and r.via == proxy_url
    assert seen == [f"HEAD {url} HTTP/1.1"]


def test_real_proxy_407_on_connect_is_network_policy_proxy(local_proxy: tuple[str, list[str]]) -> None:
    proxy_url, seen = local_proxy
    settings = net.resolve_proxy(proxy_url, environ=no_env(), system=net.SystemProxy())
    r = net.probe_reachability("https://graph.invalid/v1.0/", settings, timeout_s=5)
    assert r.state == net.POLICY_PROXY and r.failed
    assert seen and seen[0].startswith("CONNECT graph.invalid:443")


def _self_signed(tmp: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agentsync-test-inspection-root")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "cert.pem", tmp / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return cert_path, key_path


@pytest.fixture
def untrusted_tls_server(tmp_path: Path) -> Iterator[str]:
    """An HTTPS server on 127.0.0.1 whose certificate no keychain trusts (a stand-in for an inspection proxy
    whose root MDM did not install)."""
    cert, key = _self_signed(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    sock.settimeout(0.2)
    port = sock.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(4096)
                    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            except (ssl.SSLError, OSError):
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{port}"
    finally:
        stop.set()
        thread.join(timeout=2)
        sock.close()


def test_real_untrusted_certificate_is_network_policy_tls(untrusted_tls_server: str) -> None:
    r = net.probe_reachability(f"{untrusted_tls_server}/v1.0/", net.ProxySettings.direct(), timeout_s=5)
    assert r.state == net.POLICY_TLS and r.failed and not r.skipped


def test_graph_client_raises_tls_policy_at_once_on_untrusted_certificate(untrusted_tls_server: str) -> None:
    """C15 §9.31/§9.34 through the real client: truststore rejects the chain, no retry, a named failure."""
    sleeps: list[float] = []

    class Tokens:
        def get_token(self) -> str:
            return "SECRET-TOKEN"

    with (
        GraphClient(
            Tokens(),
            base_url=f"{untrusted_tls_server}/v1.0",
            user_agent="NONISV|t|agentsync/0",
            sleep=sleeps.append,
            proxy=net.ProxySettings.direct(),
            timeout_s=5,
        ) as client,
        pytest.raises(NetworkPolicyError) as ei,
    ):
        client.get_json("me")
    assert ei.value.policy == "TLS" and ei.value.code == "network-policy" and ei.value.status == 0
    assert "keychain" in str(ei.value) and "SECRET-TOKEN" not in str(ei.value)
    assert sleeps == []
