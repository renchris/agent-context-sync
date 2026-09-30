"""The Graph-facing error types (owner: graph-core / auth-tls).

Most classes live in ``agentsync.errors`` (one hierarchy for the whole program) and are re-exported here so
Graph code has one import site. This module adds two subclasses (additive; C15 §1.5 and §5):

- ``AuthBlockedError`` (an ``AuthRequiredError``): Entra refused sign-in or refresh for a reason no retry can
  fix and a plain re-login may not fix either. ``.state`` is one of ``blocked: device``, ``blocked: policy``,
  ``blocked: consent``, ``blocked: assignment`` or ``config-invalid``; ``.aadsts`` is the code. It subclasses
  ``AuthRequiredError`` on purpose: the cycle holds every cursor and the CLI exits 77 (loud), as for
  REAUTH_REQUIRED, but the message names the IT action instead of "sign in again".
- ``NetworkPolicyError`` (a ``GraphError`` with ``status=0``, ``code="network-policy"``): a TLS certificate
  failure (``.policy == "TLS"``), a proxy refusal (``"proxy"``) or a PAC-only system proxy (``"PAC"``). These
  are *failed*, never *skipped*: they do not go away by waiting, unlike an offline laptop.

What each inherited class means for a caller (CONTRACTS.md section 10):

- ``GraphGone`` (410): the cursor expired; re-enumerate FULL with ``cursor_reset=True`` (``.location`` =
  resync link).
- ``GraphBadCursor`` (400 on a URL carrying a delta/skip token): OUR cursor store is corrupt; alarm, drop, S0.
- ``GraphThrottled``: still 429/503/504 after the client's retries (``.retry_after`` seconds); skip the
  source this cycle.
- ``GraphNotFound`` (404); ``GraphError`` otherwise (``status=0`` + ``code="network"`` = offline/transient).
- ``AuthRequiredError``: a human must run ``agentsync login`` (REAUTH_REQUIRED); ``AuthError``: sign-in or
  token refresh failed for another reason (network, transient).
- ``BudgetExhaustedError``: a download would exceed ``max_bytes``; nothing was written.
"""

from __future__ import annotations

from agentsync.errors import (
    AuthError,
    AuthRequiredError,
    BudgetExhaustedError,
    GraphBadCursor,
    GraphError,
    GraphGone,
    GraphNotFound,
    GraphThrottled,
)

STATE_REAUTH = "reauth-required"
STATE_DEVICE = "blocked: device"
STATE_POLICY = "blocked: policy"
STATE_CONSENT = "blocked: consent"
STATE_ASSIGNMENT = "blocked: assignment"
STATE_CONFIG = "config-invalid"

AUTH_STATES: tuple[str, ...] = (
    STATE_REAUTH,
    STATE_DEVICE,
    STATE_POLICY,
    STATE_CONSENT,
    STATE_ASSIGNMENT,
    STATE_CONFIG,
)
"""Pipeline states for sign-in errors (C15 §1.5); none of them is ever retried."""


class AuthBlockedError(AuthRequiredError):
    """Entra refused sign-in for a tenant/device/config reason: ``.state`` (C15 §1.5) and ``.aadsts``."""

    def __init__(self, state: str, aadsts: str | None, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.aadsts = aadsts


class NetworkPolicyError(GraphError):
    """A network policy blocks Graph: ``.policy`` is ``"TLS"``, ``"proxy"`` or ``"PAC"`` (failed, not
    skipped)."""

    def __init__(self, policy: str, message: str) -> None:
        super().__init__(0, "network-policy", f"network-policy ({policy}): {message}")
        self.policy = policy


__all__ = [
    "AUTH_STATES",
    "AuthBlockedError",
    "AuthError",
    "AuthRequiredError",
    "BudgetExhaustedError",
    "GraphBadCursor",
    "GraphError",
    "GraphGone",
    "GraphNotFound",
    "GraphThrottled",
    "NetworkPolicyError",
]
