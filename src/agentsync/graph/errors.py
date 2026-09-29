"""The Graph-facing error types, re-exported from :mod:`agentsync.errors` (owner: graph-core).

Every exception class lives in ``agentsync.errors`` (one hierarchy for the whole program); this module only
gives Graph code one import site. What each means for a caller (CONTRACTS.md section 10):

- ``GraphGone`` (410): the cursor expired; re-enumerate FULL with ``cursor_reset=True`` (``.location`` =
  resync link).
- ``GraphBadCursor`` (400 on a URL carrying a delta/skip token): OUR cursor store is corrupt; alarm, drop, S0.
- ``GraphThrottled``: still 429/503/504 after the client's retries (``.retry_after`` seconds); skip the
  source this cycle.
- ``GraphNotFound`` (404); ``GraphError`` otherwise (``status=0`` = network, ``.code`` names the case).
- ``AuthRequiredError``: a human must run ``agentsync login`` (REAUTH_REQUIRED); ``AuthError``: sign-in or
  token refresh failed for another reason (network, tenant policy).
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

__all__ = [
    "AuthError",
    "AuthRequiredError",
    "BudgetExhaustedError",
    "GraphBadCursor",
    "GraphError",
    "GraphGone",
    "GraphNotFound",
    "GraphThrottled",
]
