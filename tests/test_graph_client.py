"""GraphClient: joining, paging, delta, Retry-After, typed errors, downloads, log hygiene (respx only)."""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
import respx

from agentsync.errors import (
    AuthRequiredError,
    BudgetExhaustedError,
    GraphBadCursor,
    GraphError,
    GraphGone,
    GraphNotFound,
    GraphThrottled,
)
from agentsync.graph import client as client_mod
from agentsync.graph import errors as graph_errors
from agentsync.graph.client import DeltaResult, GraphClient, GraphPage, redact_url, user_agent

BASE = "https://graph.microsoft.com/v1.0"
TOKEN = "SECRET-ACCESS-TOKEN-abc123"
REFRESHED = "SECRET-REFRESHED-TOKEN-def456"
DELTA_SECRET = "SECRETDELTATOKEN0987"
UA = "NONISV|Contoso|agentsync/0.1.0"

Handler = Callable[[httpx.Request], httpx.Response]


class FakeTokens:
    """A TokenProvider with an optional refresh hook."""

    def __init__(self) -> None:
        self.get_calls = 0
        self.refresh_calls: list[str | None] = []

    def get_token(self) -> str:
        self.get_calls += 1
        return TOKEN

    def refresh_token(self, claims_challenge: str | None = None) -> str:
        self.refresh_calls.append(claims_challenge)
        return REFRESHED


class PlainTokens:
    """A TokenProvider without refresh_token: the client falls back to get_token()."""

    def __init__(self) -> None:
        self.calls = 0

    def get_token(self) -> str:
        self.calls += 1
        return TOKEN


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    # assert_all_mocked (default True): any request without a route raises -> the network is never touched.
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def tokens() -> FakeTokens:
    return FakeTokens()


@pytest.fixture
def client(tokens: FakeTokens, sleeps: list[float]) -> Iterator[GraphClient]:
    with GraphClient(tokens, user_agent=UA, max_retries=3, max_backoff_s=60.0, sleep=sleeps.append) as c:
        yield c


def seq(*responses: httpx.Response | Exception) -> Callable[[httpx.Request], httpx.Response]:
    """side_effect returning the given responses (or raising the given exceptions) in order."""
    queue = list(responses)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    handler.requests = seen  # type: ignore[attr-defined]
    return handler


def by_url(table: dict[str, httpx.Response]) -> Handler:
    """side_effect answering by the exact request URL string."""

    def handler(request: httpx.Request) -> httpx.Response:
        return table[str(request.url)]

    return handler


# --- pure helpers ----------


def test_user_agent_format_and_sanitising() -> None:
    assert user_agent("Contoso", "0.1.0") == "NONISV|Contoso|agentsync/0.1.0"
    assert user_agent("Con|toso\n", "1.2") == "NONISV|Contoso|agentsync/1.2"
    assert user_agent("", "1") == "NONISV|agentsync|agentsync/1"
    assert user_agent("Contoso Ltd", "0.1.0") == "NONISV|Contoso Ltd|agentsync/0.1.0"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{BASE}/drives/b!x/root/delta?token={DELTA_SECRET}", f"{BASE}/drives/b!x/root/delta?<redacted>"),
        (f"{BASE}/me/messages?$deltatoken=abc&$select=id", f"{BASE}/me/messages?<redacted>"),
        (f"{BASE}/me/drive", f"{BASE}/me/drive"),
        (f"{BASE}/drive/root/delta(token='{DELTA_SECRET}')", f"{BASE}/drive/root/delta(token=<redacted>)"),
        (
            "https://user:pw@contoso.sharepoint.com/dl?tempauth=xyz#frag",
            "https://contoso.sharepoint.com/dl?<redacted>",
        ),
        ("me/drive?x=1", "me/drive?<redacted>"),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected
    assert DELTA_SECRET not in redact_url(url)


def test_parse_retry_after_forms() -> None:
    assert client_mod._parse_retry_after("7") == 7.0
    assert client_mod._parse_retry_after("1.5") == 1.5
    assert client_mod._parse_retry_after("-3") == 0.0
    assert client_mod._parse_retry_after(None) is None
    assert client_mod._parse_retry_after("soon") is None
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    parsed = client_mod._parse_retry_after(when)
    assert parsed is not None and 25 <= parsed <= 31
    past = format_datetime(datetime.now(UTC) - timedelta(seconds=30), usegmt=True)
    assert client_mod._parse_retry_after(past) == 0.0


@pytest.mark.parametrize(
    ("url", "is_cursor"),
    [
        (f"{BASE}/me/drive/root/delta?token=abc", True),
        (f"{BASE}/me/mailFolders/inbox/messages/delta?$deltatoken=abc", True),
        (f"{BASE}/me/mailFolders/inbox/messages/delta?%24deltatoken=abc", True),
        (f"{BASE}/teams/t/channels/c/messages/delta?$skiptoken=abc", True),
        (f"{BASE}/me/drive/root/delta(token='abc')", True),
        (f"{BASE}/me/drive/root/delta?$select=id,name", False),
        (f"{BASE}/me/drive/items/abc", False),
    ],
)
def test_cursor_url_detection(url: str, is_cursor: bool) -> None:
    assert client_mod._is_cursor_url(url) is is_cursor


def test_graph_errors_module_reexports_the_one_hierarchy() -> None:
    assert graph_errors.GraphGone is GraphGone
    assert graph_errors.GraphBadCursor is GraphBadCursor
    assert issubclass(graph_errors.GraphThrottled, graph_errors.GraphError)
    assert sorted(graph_errors.__all__) == graph_errors.__all__


def test_constructor_validates_arguments(tokens: FakeTokens) -> None:
    with pytest.raises(ValueError, match="max_retries"):
        GraphClient(tokens, user_agent=UA, max_retries=-1)
    with pytest.raises(ValueError, match="max_backoff_s"):
        GraphClient(tokens, user_agent=UA, max_backoff_s=0)


# --- get_json ----------


def test_get_json_joins_relative_path_and_sends_headers(
    router: respx.MockRouter, client: GraphClient
) -> None:
    route = router.get(f"{BASE}/me/drive").mock(return_value=httpx.Response(200, json={"id": "d1"}))
    body = client.get_json(
        "/me/drive", params={"$select": "id"}, headers={"Prefer": "x", "Authorization": "no"}
    )
    assert body == {"id": "d1"}
    req = route.calls.last.request
    assert req.url.params["$select"] == "id"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["User-Agent"] == UA
    assert req.headers["Accept"] == "application/json"
    assert req.headers["Prefer"] == "x"
    assert req.headers["return-client-request-id"] == "true"
    assert len(req.headers["client-request-id"]) == 36


def test_get_json_uses_absolute_graph_url_verbatim(router: respx.MockRouter, client: GraphClient) -> None:
    link = f"{BASE}/drives/b!AbC/root/delta?token={DELTA_SECRET}%3D%3D&$select=id,name"
    route = router.get(url__startswith=f"{BASE}/drives/").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    client.get_json(link)
    assert str(route.calls.last.request.url) == link


def test_get_json_refuses_to_send_token_to_foreign_origin(
    router: respx.MockRouter, client: GraphClient
) -> None:
    route = router.get(url__startswith="https://evil.example.com").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(GraphError) as ei:
        client.get_json("https://evil.example.com/v1.0/me?token=x")
    assert ei.value.code == "foreign-url"
    assert not route.called


def test_get_json_204_and_bad_json(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(f"{BASE}/a").mock(return_value=httpx.Response(204))
    router.get(f"{BASE}/b").mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    router.get(f"{BASE}/c").mock(return_value=httpx.Response(200, json=[1, 2]))
    assert client.get_json("a") == {}
    with pytest.raises(GraphError, match="bad-json"):
        client.get_json("b")
    with pytest.raises(GraphError, match="bad-json"):
        client.get_json("c")


def test_get_json_does_not_follow_redirects(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(f"{BASE}/moved").mock(return_value=httpx.Response(302, headers={"Location": f"{BASE}/x"}))
    with pytest.raises(GraphError) as ei:
        client.get_json("moved")
    assert ei.value.code == "unexpected-redirect"


# --- paging and delta ----------


def _paged_delta(router: respx.MockRouter) -> list[httpx.Request]:
    p2 = f"{BASE}/me/drive/root/delta?$skiptoken=P2"
    p3 = f"{BASE}/me/drive/root/delta?$skiptoken=P3"
    final = f"{BASE}/me/drive/root/delta?token={DELTA_SECRET}"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url.startswith((f"{BASE}/me/drive/root/delta?%24select", f"{BASE}/me/drive/root/delta?$select")):
            return httpx.Response(200, json={"value": [{"id": "1"}, {"id": "2"}], "@odata.nextLink": p2})
        if url == p2:
            return httpx.Response(200, json={"value": [{"id": "2"}], "@odata.nextLink": p3})
        if url == p3:
            return httpx.Response(200, json={"value": [{"id": "3"}], "@odata.deltaLink": final})
        raise AssertionError(f"unexpected {url}")

    router.get(url__startswith=f"{BASE}/me/drive/root/delta").mock(side_effect=handler)
    return seen


def test_iter_pages_follows_next_links_params_first_only(
    router: respx.MockRouter, client: GraphClient
) -> None:
    seen = _paged_delta(router)
    pages = list(
        client.iter_pages(
            "me/drive/root/delta", params={"$select": "id"}, headers={"Prefer": "deltaExcludeParent"}
        )
    )
    assert [len(p.value) for p in pages] == [2, 1, 1]
    assert pages[0].next_link is not None and pages[0].delta_link is None
    assert pages[-1].next_link is None
    assert pages[-1].delta_link == f"{BASE}/me/drive/root/delta?token={DELTA_SECRET}"
    assert seen[0].url.params.get("$select") == "id"
    assert all("$select" not in r.url.params for r in seen[1:])
    assert all(r.headers["Prefer"] == "deltaExcludeParent" for r in seen)
    assert isinstance(pages[0], GraphPage)


def test_delta_drains_round_and_reports_each_page(router: respx.MockRouter, client: GraphClient) -> None:
    _paged_delta(router)
    seen_pages: list[GraphPage] = []
    result = client.delta("me/drive/root/delta", params={"$select": "id"}, on_page=seen_pages.append)
    assert isinstance(result, DeltaResult)
    assert [i["id"] for i in result.items] == ["1", "2", "2", "3"]  # server order, duplicates kept
    assert result.pages == 3 == len(seen_pages)
    assert result.delta_link.endswith(f"token={DELTA_SECRET}")
    assert seen_pages[0].next_link and seen_pages[-1].delta_link == result.delta_link


def test_delta_without_delta_link_is_an_error(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(f"{BASE}/me/mailFolders/inbox/messages/delta").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "m"}]})
    )
    with pytest.raises(GraphError) as ei:
        client.delta("me/mailFolders/inbox/messages/delta")
    assert ei.value.code == "no-delta-link"


def test_paging_loop_and_bad_page_are_errors(router: respx.MockRouter, client: GraphClient) -> None:
    loop = f"{BASE}/loop?$skiptoken=same"
    router.get(url__startswith=f"{BASE}/loop").mock(
        return_value=httpx.Response(200, json={"value": [], "@odata.nextLink": loop})
    )
    router.get(f"{BASE}/nolist").mock(return_value=httpx.Response(200, json={"value": {"id": 1}}))
    router.get(f"{BASE}/badlink").mock(
        return_value=httpx.Response(200, json={"value": [], "@odata.nextLink": 5})
    )
    with pytest.raises(GraphError) as ei:
        list(client.iter_pages("loop"))
    assert ei.value.code == "paging-loop"
    with pytest.raises(GraphError) as ei2:
        list(client.iter_pages("nolist"))
    assert ei2.value.code == "bad-page"
    with pytest.raises(GraphError) as ei3:
        list(client.iter_pages("badlink"))
    assert ei3.value.code == "bad-page"


# --- throttling and retries ----------


def test_429_honours_retry_after_with_small_positive_jitter(
    router: respx.MockRouter, client: GraphClient, sleeps: list[float]
) -> None:
    route = router.get(f"{BASE}/me").mock(
        side_effect=seq(
            httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, json={"ok": 1})
        )
    )
    assert client.get_json("me") == {"ok": 1}
    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 4.9 <= sleeps[0] <= 5.6  # >= Retry-After (minus elapsed), jitter <= 10 % / 1 s


def test_503_without_retry_after_uses_capped_exponential_backoff(
    router: respx.MockRouter, tokens: FakeTokens, sleeps: list[float]
) -> None:
    c = GraphClient(tokens, user_agent=UA, max_retries=4, max_backoff_s=3.0, sleep=sleeps.append)
    router.get(f"{BASE}/me").mock(
        side_effect=seq(
            httpx.Response(503),
            httpx.Response(504),
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={}),
        )
    )
    assert c.get_json("me") == {}
    assert len(sleeps) == 4
    ceilings = [1.0, 2.0, 3.0, 3.0]  # 1, 2, 4->cap 3, 8->cap 3
    for s, ceiling in zip(sleeps, ceilings, strict=True):
        assert ceiling / 2 - 0.05 <= s <= ceiling + 0.01


def test_throttling_exhausted_raises_graph_throttled_and_pauses_next_request(
    router: respx.MockRouter, client: GraphClient, sleeps: list[float]
) -> None:
    route = router.get(f"{BASE}/busy").mock(return_value=httpx.Response(429, headers={"Retry-After": "2"}))
    router.get(f"{BASE}/other").mock(return_value=httpx.Response(200, json={"ok": True}))
    with pytest.raises(GraphThrottled) as ei:
        client.get_json("busy")
    assert ei.value.retry_after == 2.0
    assert ei.value.status == 429
    assert route.call_count == 4  # 1 + max_retries(3)
    assert len(sleeps) == 3
    # the pause set by the last 429 applies to the NEXT request through the same client
    assert client.get_json("other") == {"ok": True}
    assert len(sleeps) == 4 and 1.5 <= sleeps[-1] <= 2.3


def test_retry_after_beyond_cap_fails_fast_and_blocks_client(
    router: respx.MockRouter, client: GraphClient, sleeps: list[float]
) -> None:
    route = router.get(f"{BASE}/me").mock(return_value=httpx.Response(503, headers={"Retry-After": "3600"}))
    other = router.get(f"{BASE}/other").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(GraphThrottled) as ei:
        client.get_json("me")
    assert ei.value.retry_after == 3600 and ei.value.status == 503
    assert route.call_count == 1 and sleeps == []
    with pytest.raises(GraphThrottled) as ei2:
        client.get_json("other")
    assert ei2.value.retry_after > 3500
    assert not other.called  # paused client sends nothing


def test_500_is_retried_then_plain_graph_error(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(f"{BASE}/flaky").mock(side_effect=seq(httpx.Response(500), httpx.Response(200, json={"a": 1})))
    assert client.get_json("flaky") == {"a": 1}
    router.get(f"{BASE}/broken").mock(
        return_value=httpx.Response(502, json={"error": {"code": "badGateway", "message": "no"}})
    )
    with pytest.raises(GraphError) as ei:
        client.get_json("broken")
    assert not isinstance(ei.value, GraphThrottled)
    assert (ei.value.status, ei.value.code) == (502, "badGateway")


def test_network_errors_retry_then_graph_error_status_0(
    router: respx.MockRouter, client: GraphClient, sleeps: list[float]
) -> None:
    router.get(f"{BASE}/me").mock(
        side_effect=seq(httpx.ConnectError("dns"), httpx.Response(200, json={"id": 1}))
    )
    assert client.get_json("me") == {"id": 1}
    assert len(sleeps) == 1
    router.get(f"{BASE}/down").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(GraphError) as ei:
        client.get_json("down")
    assert (ei.value.status, ei.value.code) == (0, "network")


def test_ratelimit_headers_are_not_used(
    router: respx.MockRouter, client: GraphClient, sleeps: list[float]
) -> None:
    router.get(f"{BASE}/me").mock(
        return_value=httpx.Response(
            200, json={}, headers={"RateLimit-Remaining": "0", "RateLimit-Reset": "60"}
        )
    )
    client.get_json("me")
    client.get_json("me")
    assert sleeps == []


# --- typed errors ----------


def _err(status: int, code: str, message: str = "m", **headers: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}}, headers=headers)


def test_401_refreshes_once_with_cae_claims_then_succeeds(
    router: respx.MockRouter, client: GraphClient, tokens: FakeTokens
) -> None:
    claims = '{"access_token":{"nbf":{"essential":true,"value":"1700000000"}}}'
    b64 = base64.b64encode(claims.encode()).decode().rstrip("=")
    www = f'Bearer realm="", error="insufficient_claims", claims="{b64}"'
    route = router.get(f"{BASE}/me").mock(
        side_effect=seq(
            _err(401, "InvalidAuthenticationToken", **{"WWW-Authenticate": www}), httpx.Response(200, json={})
        )
    )
    assert client.get_json("me") == {}
    assert tokens.refresh_calls == [claims]
    assert route.calls[1].request.headers["Authorization"] == f"Bearer {REFRESHED}"


def test_second_401_is_auth_required(
    router: respx.MockRouter, client: GraphClient, tokens: FakeTokens
) -> None:
    router.get(f"{BASE}/me").mock(return_value=_err(401, "InvalidAuthenticationToken"))
    with pytest.raises(AuthRequiredError, match="REAUTH_REQUIRED"):
        client.get_json("me")
    assert tokens.refresh_calls == [None]


def test_401_without_refresh_hook_asks_get_token_again(router: respx.MockRouter, sleeps: list[float]) -> None:
    plain = PlainTokens()
    c = GraphClient(plain, user_agent=UA, sleep=sleeps.append)
    router.get(f"{BASE}/me").mock(side_effect=seq(_err(401, "x"), httpx.Response(200, json={})))
    assert c.get_json("me") == {}
    assert plain.calls == 2


def test_404_is_graph_not_found_with_request_id(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(f"{BASE}/me/messages/gone").mock(
        return_value=_err(404, "ErrorItemNotFound", "not found", **{"request-id": "rid-404"})
    )
    with pytest.raises(GraphNotFound) as ei:
        client.get_json("me/messages/gone")
    assert (ei.value.status, ei.value.code, ei.value.request_id) == (404, "ErrorItemNotFound", "rid-404")


def test_request_id_falls_back_to_error_inner_error(router: respx.MockRouter, client: GraphClient) -> None:
    body = {"error": {"code": "accessDenied", "message": "no", "innerError": {"request-id": "rid-inner"}}}
    router.get(f"{BASE}/x").mock(return_value=httpx.Response(403, json=body))
    with pytest.raises(GraphError) as ei:
        client.get_json("x")
    assert (ei.value.status, ei.value.code, ei.value.request_id) == (403, "accessDenied", "rid-inner")


def test_410_is_graph_gone_with_location_header(router: respx.MockRouter, client: GraphClient) -> None:
    fresh = f"{BASE}/me/drive/root/delta?token=FRESH"
    router.get(url__startswith=f"{BASE}/me/drive/root/delta").mock(
        return_value=_err(410, "resyncChangesApplyDifferences", "resync", Location=fresh)
    )
    with pytest.raises(GraphGone) as ei:
        client.delta(f"{BASE}/me/drive/root/delta?token=OLD")
    assert ei.value.status == 410
    assert ei.value.code == "resyncChangesApplyDifferences"
    assert ei.value.location == fresh
    assert not isinstance(ei.value, GraphBadCursor)


def test_410_location_from_error_body_or_none(router: respx.MockRouter, client: GraphClient) -> None:
    body = {
        "error": {
            "code": "resyncRequired",
            "message": "r",
            "innerError": {"resyncLink": f"{BASE}/fresh?token=A"},
        }
    }
    router.get(f"{BASE}/a").mock(return_value=httpx.Response(410, json=body))
    router.get(f"{BASE}/b").mock(return_value=_err(410, "resyncRequired"))
    with pytest.raises(GraphGone) as ei:
        client.get_json("a")
    assert ei.value.location == f"{BASE}/fresh?token=A"
    with pytest.raises(GraphGone) as ei2:
        client.get_json("b")
    assert ei2.value.location is None


@pytest.mark.parametrize(
    "link",
    [
        f"{BASE}/me/drive/root/delta?token=bogus-token-xyz",
        f"{BASE}/me/mailFolders/inbox/messages/delta?$deltatoken=bogus",
        f"{BASE}/teams/t/channels/c/messages/delta?$skiptoken=bogus",
    ],
)
def test_400_on_cursor_url_is_bad_cursor(router: respx.MockRouter, client: GraphClient, link: str) -> None:
    router.get(url__startswith=BASE).mock(
        return_value=_err(400, "invalidRequest", "Provided sync token is malformed")
    )
    with pytest.raises(GraphBadCursor) as ei:
        client.delta(link)
    assert (ei.value.status, ei.value.code) == (400, "invalidRequest")
    assert not isinstance(ei.value, GraphGone)


def test_400_on_plain_url_is_not_bad_cursor(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(url__startswith=f"{BASE}/me/drive/root/delta").mock(return_value=_err(400, "invalidRequest"))
    with pytest.raises(GraphError) as ei:
        client.delta("me/drive/root/delta", params={"$select": "id"})
    assert type(ei.value) is GraphError
    assert ei.value.status == 400


def test_error_without_json_body_uses_reason(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(f"{BASE}/teapot").mock(return_value=httpx.Response(418, text="short and stout"))
    with pytest.raises(GraphError) as ei:
        client.get_json("teapot")
    assert (ei.value.status, ei.value.code) == (418, "http-418")


# --- log hygiene ----------


def test_no_token_or_cursor_in_logs(
    router: respx.MockRouter, client: GraphClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    link = f"{BASE}/me/drive/root/delta?token={DELTA_SECRET}"
    router.get(url__startswith=f"{BASE}/me/drive/root/delta").mock(
        side_effect=seq(
            httpx.Response(429, headers={"Retry-After": "0"}),
            _err(401, "InvalidAuthenticationToken"),
            httpx.Response(200, json={"value": [], "@odata.deltaLink": link}),
        )
    )
    router.get(url__startswith=f"{BASE}/gone").mock(return_value=_err(410, "resyncRequired", Location=link))
    client.delta(link)
    with pytest.raises(GraphGone):
        client.get_json(f"{BASE}/gone?token={DELTA_SECRET}")
    text = caplog.text + " ".join(str(r.args) for r in caplog.records)
    assert caplog.records, "expected debug/warning records"
    assert TOKEN not in text and REFRESHED not in text
    assert DELTA_SECRET not in text
    assert "Bearer" not in text
    httpx_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert httpx_lines and all("<redacted>" in m for m in httpx_lines)


def test_error_messages_do_not_carry_cursors(router: respx.MockRouter, client: GraphClient) -> None:
    router.get(url__startswith=BASE).mock(
        side_effect=httpx.ConnectError(f"failed {BASE}/x?token={DELTA_SECRET}")
    )
    with pytest.raises(GraphError) as ei:
        client.get_json(f"{BASE}/me/drive/root/delta?token={DELTA_SECRET}")
    assert DELTA_SECRET not in str(ei.value)


# --- download ----------

PREAUTH = "https://contoso-my.sharepoint.com/personal/u/_layouts/15/download.aspx?UniqueId=1&tempauth=SECRETTEMPAUTH"


def _content_redirect(router: respx.MockRouter, body: bytes, **headers: str) -> respx.Route:
    router.get(f"{BASE}/drives/d/items/i/content").mock(
        return_value=httpx.Response(302, headers={"Location": PREAUTH})
    )
    return router.get(url__startswith="https://contoso-my.sharepoint.com/").mock(
        return_value=httpx.Response(200, content=body, headers=headers)
    )


def test_download_follows_302_without_authorization(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path
) -> None:
    body = b"PK\x03\x04" + b"x" * 5000
    dl = _content_redirect(router, body)
    dest = tmp_path / "stage" / "file.docx"
    size, sha = client.download("drives/d/items/i/content", dest)
    assert (size, sha) == (len(body), hashlib.sha256(body).hexdigest())
    assert dest.read_bytes() == body
    assert "authorization" not in {k.lower() for k in dl.calls.last.request.headers}
    assert sorted(p.name for p in dest.parent.iterdir()) == ["file.docx"]  # no tmp left behind


def test_download_direct_preauth_url_sends_no_token(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path
) -> None:
    route = router.get(url__startswith="https://contoso-my.sharepoint.com/").mock(
        return_value=httpx.Response(200, content=b"abc")
    )
    assert client.download(PREAUTH, tmp_path / "f.bin") == (3, hashlib.sha256(b"abc").hexdigest())
    assert "authorization" not in {k.lower() for k in route.calls.last.request.headers}


def test_download_over_budget_by_content_length(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path
) -> None:
    _content_redirect(router, b"y" * 100)
    dest = tmp_path / "f.bin"
    with pytest.raises(BudgetExhaustedError):
        client.download("drives/d/items/i/content", dest, max_bytes=99)
    assert list(tmp_path.iterdir()) == []


def test_download_over_budget_while_streaming(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path
) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(5):
            yield b"z" * 1000

    router.get(f"{BASE}/drives/d/items/i/content").mock(
        return_value=httpx.Response(200, content=chunks())  # chunked: no Content-Length
    )
    dest = tmp_path / "f.bin"
    with pytest.raises(BudgetExhaustedError):
        client.download("drives/d/items/i/content", dest, max_bytes=2500)
    assert list(tmp_path.iterdir()) == []
    # exactly at the budget is fine
    router.get(f"{BASE}/drives/d/items/j/content").mock(return_value=httpx.Response(200, content=b"q" * 10))
    assert client.download("drives/d/items/j/content", dest, max_bytes=10)[0] == 10


def test_download_restarts_when_preauth_url_expired(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path
) -> None:
    graph = router.get(f"{BASE}/drives/d/items/i/content").mock(
        return_value=httpx.Response(302, headers={"Location": PREAUTH})
    )
    router.get(url__startswith="https://contoso-my.sharepoint.com/").mock(
        side_effect=seq(httpx.Response(401), httpx.Response(200, content=b"fresh"))
    )
    assert client.download("drives/d/items/i/content", tmp_path / "f")[0] == 5
    assert graph.call_count == 2


def test_download_retries_throttled_preauth_hop(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path, sleeps: list[float]
) -> None:
    router.get(f"{BASE}/drives/d/items/i/content").mock(
        return_value=httpx.Response(302, headers={"Location": PREAUTH})
    )
    router.get(url__startswith="https://contoso-my.sharepoint.com/").mock(
        side_effect=seq(httpx.Response(503, headers={"Retry-After": "1"}), httpx.Response(200, content=b"ok"))
    )
    assert client.download("drives/d/items/i/content", tmp_path / "f")[0] == 2
    assert len(sleeps) == 1


def test_download_short_read_restarts(router: respx.MockRouter, client: GraphClient, tmp_path: Path) -> None:
    router.get(f"{BASE}/drives/d/items/i/content").mock(
        side_effect=seq(
            httpx.Response(200, content=b"abc", headers={"Content-Length": "10"}),
            httpx.Response(200, content=b"0123456789"),
        )
    )
    assert client.download("drives/d/items/i/content", tmp_path / "f") == (
        10,
        hashlib.sha256(b"0123456789").hexdigest(),
    )


def test_download_errors_are_typed_and_leave_nothing(
    router: respx.MockRouter, client: GraphClient, tmp_path: Path
) -> None:
    router.get(f"{BASE}/drives/d/items/missing/content").mock(return_value=_err(404, "itemNotFound"))
    with pytest.raises(GraphNotFound):
        client.download("drives/d/items/missing/content", tmp_path / "f")
    router.get(f"{BASE}/drives/d/items/loop/content").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/drives/d/items/loop/content"})
    )
    with pytest.raises(GraphError) as ei:
        client.download("drives/d/items/loop/content", tmp_path / "g")
    assert ei.value.code == "too-many-redirects"
    assert list(tmp_path.iterdir()) == []


def test_close_closes_http_client(tokens: FakeTokens) -> None:
    c = GraphClient(tokens, user_agent=UA)
    with c as same:
        assert same is c
    assert c._http.is_closed


def test_pause_is_served_once(tokens: FakeTokens, sleeps: list[float]) -> None:
    c = GraphClient(tokens, user_agent=UA, sleep=sleeps.append)
    c._pause(1.0)
    c._wait_for_pause()
    c._wait_for_pause()
    assert len(sleeps) == 1 and 0.9 <= sleeps[0] <= 1.0
