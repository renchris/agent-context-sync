"""Microsoft Graph HTTP client over httpx: paging, delta, Retry-After, typed errors (owner: graph-core).

Error mapping (after retries): 401 -> refresh token once, then AuthRequiredError; 404 -> GraphNotFound; 410 ->
GraphGone(location=Location header or error's resync link); 400 on a request whose URL carries a ``token=`` /
``$deltatoken`` / ``$skiptoken`` -> GraphBadCursor; other 4xx -> GraphError; 429/503/504 -> honour Retry-After
(else exponential backoff capped at ``max_backoff_s``), PAUSING every request made through this client until
then, up to ``max_retries``, then GraphThrottled(retry_after). Network errors retry the same way and finally
raise GraphError(status=0, code="network"). The client never logs tokens, delta links or Authorization headers
(URLs are logged with query strings redacted).

Details beyond the contract table:

- A ``Retry-After`` longer than ``max_backoff_s`` is not slept through: the client raises GraphThrottled at
  once and every later request through it fails fast with GraphThrottled until that instant (throttled
  requests still count against the tenant's budget, so hammering is worse than skipping a cycle).
- 500 and 502 are retried like 503/504 (GETs are idempotent) but end in a plain GraphError.
- The bearer token is only ever sent to ``base_url``'s origin; an absolute URL on another origin is refused
  for JSON calls (``code="foreign-url"``) and fetched without Authorization by :meth:`GraphClient.download`.
- A 401 carrying a CAE ``claims=`` challenge passes the decoded claims to the provider's optional
  ``refresh_token(claims_challenge)`` (``MsalAuth`` has one); otherwise ``get_token()`` is asked again.
- httpx logs every request URL at INFO; a filter on the ``httpx`` logger redacts those URLs too.
"""

from __future__ import annotations

import base64
import binascii
import email.utils
import hashlib
import json
import logging
import os
import random
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC as _UTC
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import httpx

from agentsync.errors import (
    AuthRequiredError,
    BudgetExhaustedError,
    GraphBadCursor,
    GraphError,
    GraphGone,
    GraphNotFound,
    GraphThrottled,
)
from agentsync.graph.auth import TokenProvider

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_THROTTLE_STATUSES = frozenset({429, 503, 504})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_CURSOR_PARAMS = frozenset({"token", "$deltatoken", "deltatoken", "$skiptoken", "skiptoken"})
_PATH_TOKEN_RE = re.compile(r"\(\s*token\s*=\s*'[^']*'\s*\)", re.IGNORECASE)  # .../delta(token='...')
_CLAIMS_RE = re.compile(r'claims\s*=\s*"([^"]+)"', re.IGNORECASE)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_CHUNK_BYTES = 1 << 20
_BACKOFF_BASE_S = 1.0
_MAX_JITTER_S = 1.0


def user_agent(company: str, version: str) -> str:
    """Return ``NONISV|<company>|agentsync/<version>`` (SharePoint's decorated-traffic format)."""
    clean = "".join(ch for ch in company if 32 <= ord(ch) < 127 and ch != "|").strip() or "agentsync"
    ver = "".join(ch for ch in version if 32 < ord(ch) < 127 and ch != "|") or "0"
    return f"NONISV|{clean}|agentsync/{ver}"


def redact_url(url: str) -> str:
    """Return ``url`` with its query string replaced by ``?<redacted>`` (safe to log)."""
    parts = urlsplit(url)
    path = _PATH_TOKEN_RE.sub("(token=<redacted>)", parts.path)
    netloc = parts.netloc.rsplit("@", 1)[-1]  # never log userinfo
    return urlunsplit((parts.scheme, netloc, path, "<redacted>" if parts.query else "", ""))


def _redact_text(text: str) -> str:
    """Redact every URL inside free text (exception messages)."""
    return _URL_IN_TEXT_RE.sub(lambda m: redact_url(m.group(0)), text)


def _is_cursor_url(url: str) -> bool:
    """True when ``url`` carries a delta/skip token (a 400 on it means OUR stored cursor is bad)."""
    parts = urlsplit(url)
    if _PATH_TOKEN_RE.search(unquote(parts.path)):
        return True
    keys = {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
    return bool(keys & _CURSOR_PARAMS)


def _is_absolute(path_or_url: str) -> bool:
    """True for an absolute http(s) URL."""
    return path_or_url.lower().startswith(("https://", "http://"))


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP-date); None when absent/unparseable."""
    if value is None or not value.strip():
        return None
    v = value.strip()
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_UTC)
    return max(0.0, (when - datetime.now(_UTC)).total_seconds())


def _json_or_none(response: httpx.Response) -> JsonObject | None:
    """The response body as a JSON object, or None."""
    try:
        body = json.loads(response.content or b"null")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _request_id(response: httpx.Response, body: JsonObject | None = None) -> str | None:
    """Graph's ``request-id`` (header, else ``error.innerError``), else our echoed client-request-id."""
    rid = response.headers.get("request-id")
    if rid:
        return str(rid)
    if body is not None:
        err = body.get("error")
        inner = err.get("innerError") or err.get("innererror") if isinstance(err, dict) else None
        if isinstance(inner, dict) and isinstance(inner.get("request-id"), str):
            return str(inner["request-id"])
    crid = response.headers.get("client-request-id")
    return str(crid) if crid else None


def _error_code_message(response: httpx.Response, body: JsonObject | None) -> tuple[str, str]:
    """(code, message) from a Graph error body, falling back to the HTTP reason phrase."""
    err = body.get("error") if body is not None else None
    if isinstance(err, dict):
        code = str(err.get("code") or f"http-{response.status_code}")
        message = str(err.get("message") or response.reason_phrase or "")
        return code, _redact_text(message)
    if isinstance(err, str):  # OAuth-style {"error": "...", "error_description": "..."}
        assert body is not None
        return err, _redact_text(str(body.get("error_description") or response.reason_phrase or ""))
    return f"http-{response.status_code}", response.reason_phrase or ""


def _resync_location(response: httpx.Response, body: JsonObject | None) -> str | None:
    """The fresh-enumeration link of a 410: ``Location`` header, else a link-like field in the error body."""
    loc = response.headers.get("Location")
    if loc:
        return str(response.url.join(loc))
    stack: list[Any] = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, val in sorted(node.items()):
                lk = str(key).lower()
                if isinstance(val, str) and _is_absolute(val) and ("link" in lk or "location" in lk):
                    return val
                if isinstance(val, dict | list):
                    stack.append(val)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _claims_challenge(response: httpx.Response) -> str | None:
    """Decoded CAE ``claims`` from a 401's ``WWW-Authenticate`` header, if any."""
    m = _CLAIMS_RE.search(response.headers.get("WWW-Authenticate", ""))
    if not m:
        return None
    raw = m.group(1)
    try:
        return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return raw


class _RedactHttpxUrls(logging.Filter):
    """Redact query strings in the ``httpx`` logger's ``HTTP Request: GET <url>`` INFO lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite URL arguments in place; never drops a record."""
        if isinstance(record.args, tuple):
            record.args = tuple(redact_url(str(a)) if isinstance(a, httpx.URL) else a for a in record.args)
        return True


def _install_httpx_log_redaction() -> None:
    """Attach the redaction filter to the ``httpx`` logger once per process (idempotent)."""
    lg = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactHttpxUrls) for f in lg.filters):
        lg.addFilter(_RedactHttpxUrls())


class _RestartDownload(Exception):  # noqa: N818 - internal control flow, never escapes download()
    """A download failed in a way a fresh start can fix (expired pre-authenticated URL, short read)."""

    def __init__(self, error: GraphError) -> None:
        super().__init__(str(error))
        self.error = error


@dataclass(frozen=True, slots=True)
class GraphPage:
    """One page of a collection response."""

    value: tuple[JsonObject, ...]
    next_link: str | None  # @odata.nextLink
    delta_link: str | None  # @odata.deltaLink (final page of a delta round only)


@dataclass(frozen=True, slots=True)
class DeltaResult:
    """A fully drained delta round."""

    items: tuple[JsonObject, ...]  # in server order, duplicates preserved (the consumer dedups by id)
    delta_link: str
    pages: int


def _opt_link(body: JsonObject, key: str) -> str | None:
    """A non-empty string link from ``body[key]``, else None; a non-string value is a malformed page."""
    val = body.get(key)
    if val is None or val == "":
        return None
    if not isinstance(val, str):
        raise GraphError(200, "bad-page", f"{key} is not a string")
    return val


class GraphClient:
    """Synchronous Graph client; one instance per cycle; not thread-safe."""

    def __init__(
        self,
        tokens: TokenProvider,
        *,
        base_url: str = "https://graph.microsoft.com/v1.0",
        user_agent: str,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 6,
        max_backoff_s: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the httpx.Client (``transport`` lets tests inject respx/MockTransport)."""
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if max_backoff_s <= 0:
            raise ValueError("max_backoff_s must be > 0")
        self._tokens = tokens
        self._base_url = base_url.rstrip("/")
        base = httpx.URL(self._base_url)
        self._origin = (base.scheme, base.host, base.port)
        self._max_retries = max_retries
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep
        self._rng = random.Random()
        self._paused_until = 0.0  # time.monotonic(): every request waits until then
        self._blocked_until = 0.0  # time.monotonic(): requests fail fast with GraphThrottled until then
        self._blocked_status = 429
        _install_httpx_log_redaction()
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._http.close()

    def __enter__(self) -> GraphClient:
        """Return self."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close."""
        self.close()

    # -- plumbing ---------------------------------------------------------------------------------------

    def _same_origin(self, url: str) -> bool:
        """True when ``url`` is on base_url's scheme/host/port."""
        u = httpx.URL(url)
        return (u.scheme, u.host, u.port) == self._origin

    def _resolve(self, path_or_url: str) -> tuple[str, bool]:
        """(absolute url, may carry the token): relative paths join base_url; absolute URLs are verbatim."""
        if _is_absolute(path_or_url):
            return path_or_url, self._same_origin(path_or_url)
        return f"{self._base_url}/{path_or_url.lstrip('/')}", True

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with equal jitter, capped at max_backoff_s (attempt >= 1)."""
        ceiling = min(self._max_backoff_s, _BACKOFF_BASE_S * 2.0 ** (attempt - 1))
        return ceiling / 2 + self._rng.uniform(0, ceiling / 2)

    def _jittered(self, retry_after: float) -> float:
        """Retry-After plus a small positive jitter (never less than the server asked for)."""
        return retry_after + self._rng.uniform(0, min(_MAX_JITTER_S, 0.1 * retry_after))

    def _pause(self, delay: float) -> None:
        """Pause every request made through this client for ``delay`` seconds from now."""
        self._paused_until = max(self._paused_until, time.monotonic() + delay)

    def _wait_for_pause(self) -> None:
        """Honour a global pause (sleep) or block (fail fast) before sending anything."""
        now = time.monotonic()
        if now < self._blocked_until:
            remaining = self._blocked_until - now
            raise GraphThrottled(
                self._blocked_status,
                remaining,
                f"client paused for another {remaining:.0f}s by an earlier Retry-After",
            )
        remaining = self._paused_until - now
        if remaining > 0:
            self._sleep(remaining)
        self._paused_until = 0.0

    def _refresh(self, claims: str | None) -> str:
        """A fresh token after a 401: the provider's ``refresh_token`` if it has one, else ``get_token``."""
        refresh = getattr(self._tokens, "refresh_token", None)
        if callable(refresh):
            return str(refresh(claims))
        return self._tokens.get_token()

    def _send(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        auth: bool,
        stream: bool,
        accept_json: bool,
    ) -> httpx.Response:
        """One GET hop with pause/retry/401-refresh; returns a response with status < 400 or raises."""
        attempt = 0
        refreshed = False
        token: str | None = None
        while True:
            self._wait_for_pause()
            h: dict[str, str] = {"client-request-id": str(uuid.uuid4()), "return-client-request-id": "true"}
            if accept_json:
                h["Accept"] = "application/json"
            if headers:
                h.update({k: v for k, v in headers.items() if k.lower() != "authorization"})
            if auth:
                h["Authorization"] = f"Bearer {token or self._tokens.get_token()}"
            request = self._http.build_request("GET", url, params=params, headers=h)
            safe_url = redact_url(str(request.url))
            started = time.monotonic()
            try:
                response = self._http.send(request, stream=stream)
            except httpx.TransportError as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise GraphError(
                        0, "network", f"{type(exc).__name__} on GET {safe_url} after {attempt} attempts"
                    ) from None
                delay = self._backoff(attempt)
                logger.warning(
                    "network error %s on GET %s; retry %d/%d in %.1fs",
                    type(exc).__name__,
                    safe_url,
                    attempt,
                    self._max_retries,
                    delay,
                )
                self._pause(delay)
                continue

            status = response.status_code
            logger.debug(
                "GET %s -> %d in %.0f ms (request-id=%s, client-request-id=%s)",
                safe_url,
                status,
                (time.monotonic() - started) * 1000,
                response.headers.get("request-id"),
                h["client-request-id"],
            )
            if status < 400:
                return response
            try:
                if stream:
                    response.read()
            finally:
                response.close()
            body = _json_or_none(response)
            rid = _request_id(response, body)

            if status == 401 and auth and not refreshed:
                refreshed = True
                logger.info("Graph 401 on GET %s (request-id=%s); refreshing the token once", safe_url, rid)
                token = self._refresh(_claims_challenge(response))
                continue

            if status in _RETRY_STATUSES:
                attempt += 1
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > self._max_backoff_s:
                    self._blocked_until = time.monotonic() + retry_after
                    self._blocked_status = status
                    logger.warning(
                        "Graph %d on GET %s asks for %.0fs (> max_backoff_s %.0fs); pausing this client "
                        "(request-id=%s)",
                        status,
                        safe_url,
                        retry_after,
                        self._max_backoff_s,
                        rid,
                    )
                    raise GraphThrottled(
                        status, retry_after, f"Retry-After {retry_after:.0f}s exceeds the client cap", rid
                    )
                delay = self._jittered(retry_after) if retry_after is not None else self._backoff(attempt)
                self._pause(delay)
                if attempt > self._max_retries:
                    code, message = _error_code_message(response, body)
                    logger.warning(
                        "Graph %d on GET %s after %d retries; giving up (request-id=%s)",
                        status,
                        safe_url,
                        self._max_retries,
                        rid,
                    )
                    if status in _THROTTLE_STATUSES:
                        last_wait = retry_after if retry_after is not None else delay
                        raise GraphThrottled(status, last_wait, f"{code}: {message}", rid)
                    raise GraphError(status, code, message, rid)
                logger.warning(
                    "Graph %d on GET %s; pausing all requests %.1fs (retry %d/%d, request-id=%s)",
                    status,
                    safe_url,
                    delay,
                    attempt,
                    self._max_retries,
                    rid,
                )
                continue

            raise self._map_error(response, body, str(request.url), rid, auth=auth)

    def _map_error(
        self, response: httpx.Response, body: JsonObject | None, url: str, rid: str | None, *, auth: bool
    ) -> Exception:
        """Typed error for a non-retryable status."""
        status = response.status_code
        code, message = _error_code_message(response, body)
        safe_url = redact_url(url)
        if status == 401:
            if auth:
                logger.warning("Graph 401 on GET %s after a token refresh (request-id=%s)", safe_url, rid)
                return AuthRequiredError(
                    f"REAUTH_REQUIRED: Graph returned 401 {code} after a token refresh (request-id={rid})"
                )
            return GraphError(401, "download-url-rejected", message, rid)
        if status == 404:
            logger.debug("Graph 404 on GET %s (request-id=%s)", safe_url, rid)
            return GraphNotFound(404, code, message, rid)
        if status == 410:
            logger.info("Graph 410 %s on GET %s: cursor expired, resync (request-id=%s)", code, safe_url, rid)
            return GraphGone(code, message, _resync_location(response, body), rid)
        if status == 400 and _is_cursor_url(url):
            logger.warning("Graph 400 %s on a cursor URL %s (request-id=%s)", code, safe_url, rid)
            return GraphBadCursor(400, code, message, rid)
        logger.info("Graph %d %s on GET %s (request-id=%s)", status, code, safe_url, rid)
        return GraphError(status, code, message, rid)

    # -- public API -------------------------------------------------------------------------------------

    def get_json(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonObject:
        """GET a relative path (joined to base_url) or an absolute Graph URL (nextLink/deltaLink, used
        verbatim)."""
        url, same_origin = self._resolve(path_or_url)
        if not same_origin:
            raise GraphError(0, "foreign-url", f"refusing to send the Graph token to {redact_url(url)}")
        response = self._send(url, params=params, headers=headers, auth=True, stream=False, accept_json=True)
        rid = _request_id(response)
        if response.status_code >= 300:
            raise GraphError(
                response.status_code,
                "unexpected-redirect",
                f"GET {redact_url(url)} redirected; JSON endpoints are not followed",
                rid,
            )
        if response.status_code == 204 or not response.content:
            return {}
        body = _json_or_none(response)
        if body is None:
            raise GraphError(
                response.status_code, "bad-json", f"GET {redact_url(url)}: not a JSON object", rid
            )
        return body

    def iter_pages(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[GraphPage]:
        """Yield pages following ``@odata.nextLink`` (params apply to the first request only); the last page
        carries ``delta_link`` when the endpoint is a delta.  Consumers persist ``next_link`` to resume."""
        url = path_or_url
        page_params = params
        seen: set[str] = set()
        while True:
            body = self.get_json(url, params=page_params, headers=headers)
            page_params = None
            value = body.get("value")
            if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
                raise GraphError(200, "bad-page", f"GET {redact_url(url)}: 'value' is not a list of objects")
            next_link = _opt_link(body, "@odata.nextLink")
            delta_link = _opt_link(body, "@odata.deltaLink")
            yield GraphPage(tuple(value), next_link, delta_link)
            if next_link is None:
                return
            if next_link in seen:
                raise GraphError(200, "paging-loop", f"GET {redact_url(url)}: @odata.nextLink repeated")
            seen.add(next_link)
            url = next_link

    def delta(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        on_page: Callable[[GraphPage], None] | None = None,
    ) -> DeltaResult:
        """Drain a delta round to its final ``@odata.deltaLink``; ``on_page`` sees each page (resume points).

        Raises GraphGone (410), GraphBadCursor (400 on a token URL), GraphThrottled, AuthRequiredError; raises
        GraphError(code="no-delta-link") when the last page has neither nextLink nor deltaLink.
        """
        items: list[JsonObject] = []
        for pages, page in enumerate(self.iter_pages(path_or_url, params=params, headers=headers), start=1):
            items.extend(page.value)
            if on_page is not None:
                on_page(page)
            if page.next_link is None:
                if page.delta_link is None:
                    raise GraphError(
                        200,
                        "no-delta-link",
                        f"delta round on {redact_url(path_or_url)} ended after {pages} page(s) without "
                        "@odata.deltaLink",
                    )
                logger.debug("delta round: %d page(s), %d item(s)", pages, len(items))
                return DeltaResult(tuple(items), page.delta_link, pages)
        raise GraphError(200, "no-delta-link", f"delta round on {redact_url(path_or_url)} returned no page")

    def download(self, path_or_url: str, dest: Path, *, max_bytes: int | None = None) -> tuple[int, str]:
        """Stream content to ``dest`` (tmp + rename) returning (size, sha256 hex).

        Follows the 302 to the pre-authenticated download URL WITHOUT the Authorization header. More than
        ``max_bytes`` -> BudgetExhaustedError and no dest file.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        url, auth = self._resolve(path_or_url)
        restarts = 0
        while True:
            try:
                return self._download_once(url, auth, dest, max_bytes)
            except _RestartDownload as restart:
                restarts += 1
                if restarts > self._max_retries:
                    raise restart.error from None
                delay = self._backoff(restarts)
                logger.warning(
                    "download of %s restarting (%s); retry %d/%d in %.1fs",
                    redact_url(url),
                    restart.error.code,
                    restarts,
                    self._max_retries,
                    delay,
                )
                self._pause(delay)

    def _download_once(self, url: str, auth: bool, dest: Path, max_bytes: int | None) -> tuple[int, str]:
        """One attempt: follow redirects (dropping Authorization) and stream the body to ``dest``."""
        hop_url, hop_auth = url, auth
        for _hop in range(_MAX_REDIRECTS + 1):
            try:
                response = self._send(
                    hop_url, params=None, headers=None, auth=hop_auth, stream=True, accept_json=False
                )
            except GraphError as exc:
                if exc.status == 401 and not hop_auth and hop_url != url:
                    raise _RestartDownload(exc) from None  # pre-authenticated URL expired: ask Graph again
                raise
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise GraphError(
                            response.status_code, "redirect-without-location", redact_url(hop_url)
                        )
                    hop_url, hop_auth = str(response.url.join(location)), False
                    logger.debug("download redirected to %s (no Authorization)", redact_url(hop_url))
                    continue
                if response.status_code >= 300:
                    raise GraphError(
                        response.status_code,
                        "unexpected-status",
                        f"GET {redact_url(hop_url)}",
                        _request_id(response),
                    )
                return self._stream_to(response, dest, max_bytes, hop_url)
            finally:
                response.close()
        raise GraphError(
            0, "too-many-redirects", f"GET {redact_url(url)}: more than {_MAX_REDIRECTS} redirects"
        )

    def _stream_to(
        self, response: httpx.Response, dest: Path, max_bytes: int | None, url: str
    ) -> tuple[int, str]:
        """Write the body to a tmp file beside ``dest``, fsync, rename; enforce ``max_bytes``."""
        encoded = response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity")
        declared_s = response.headers.get("Content-Length", "")
        declared = int(declared_s) if declared_s.isdigit() and not encoded else None
        if max_bytes is not None and declared is not None and declared > max_bytes:
            raise BudgetExhaustedError(
                f"{redact_url(url)}: {declared} bytes exceeds the remaining budget of {max_bytes} bytes"
            )
        digest = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
        tmp: Path | None = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise BudgetExhaustedError(
                            f"{redact_url(url)}: more than the remaining budget of {max_bytes} bytes"
                        )
                    digest.update(chunk)
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            if declared is not None and declared != size:
                raise _RestartDownload(
                    GraphError(0, "short-read", f"{redact_url(url)}: got {size} of {declared} bytes")
                )
            assert tmp is not None
            tmp.replace(dest)
            tmp = None
        except httpx.TransportError as exc:
            raise _RestartDownload(
                GraphError(0, "network", f"{type(exc).__name__} while reading {redact_url(url)}")
            ) from None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        return size, digest.hexdigest()
