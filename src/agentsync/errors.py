"""Typed exceptions shared by every agentsync module (see docs/design/CONTRACTS.md, "errors")."""

from __future__ import annotations


class AgentSyncError(Exception):
    """Base class for every error agentsync raises on purpose."""


class ConfigError(AgentSyncError):
    """sources.toml is missing, unparseable or semantically invalid; the message names the key."""


class ManifestSchemaError(AgentSyncError):
    """The SQLite manifest's schema or key-schema version does not match this build. Every opener migrates
    an older one, so this means a newer build wrote it: upgrade this install."""


class LockHeldError(AgentSyncError):
    """Another live agentsync process holds the single-writer lock (CLI exit 75, logged as skipped)."""

    def __init__(self, holder: str) -> None:
        super().__init__(f"lock held: {holder}")
        self.holder = holder


class BudgetExhaustedError(AgentSyncError):
    """A per-cycle budget (bytes to materialise, files) would be exceeded by the next read."""


class MaterialiseError(AgentSyncError):
    """Reading a local (possibly dataless) file failed for a reason other than the budget."""

    def __init__(self, path: str, errno_: int | None, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.errno = errno_


class DatalessRefusedError(MaterialiseError):
    """The kernel refused to materialise a dataless file (EDEADLK): policy is OFF for this context; or the
    provider canceled every retry (ECANCELED). Either way the item is deferred, never an error."""


class ProviderTimeoutError(MaterialiseError):
    """The File Provider timed out (ETIMEDOUT) after every retry; a retry-later, never a verdict."""


class ConversionError(AgentSyncError):
    """A converter failed on one input; the row becomes status failed/unreadable, never an empty page."""


class UnreadableSourceError(ConversionError):
    """The bytes are encrypted / password-protected / IRM-protected: an UNREADABLE stub, a quarantined row."""


class CurateError(AgentSyncError):
    """A curated page's frontmatter violates the ``sources:`` contract (unparseable, path escapes docs/)."""


class PublishError(AgentSyncError):
    """Writing docs/ or committing it failed; the cycle must not advance any cursor."""


class LintError(AgentSyncError):
    """A land-gate lint failed; the cycle must not commit."""


class AuthError(AgentSyncError):
    """Signing in or acquiring a token failed."""


class AuthRequiredError(AuthError):
    """No usable token without user interaction (invalid_grant, AADSTS50076/50173, empty cache):
    REAUTH_REQUIRED."""


class GraphError(AgentSyncError):
    """A Microsoft Graph request failed with a non-retryable HTTP status."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None) -> None:
        super().__init__(f"Graph {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id


class GraphGone(GraphError):  # noqa: N818 - name fixed by the contract
    """410 Gone on a delta request: the cursor expired; re-enumerate fully (follow ``location`` if given)."""

    def __init__(self, code: str, message: str, location: str | None, request_id: str | None = None) -> None:
        super().__init__(410, code, message, request_id)
        self.location = location


class GraphBadCursor(GraphError):  # noqa: N818 - name fixed by the contract
    """400 on a stored delta link ("sync token is malformed"): OUR cursor store is corrupt; alarm, drop,
    S0."""


class GraphThrottled(GraphError):  # noqa: N818 - name fixed by the contract
    """429/503 still failing after the client's retry budget; ``retry_after`` seconds from the last
    response."""

    def __init__(self, status: int, retry_after: float, message: str, request_id: str | None = None) -> None:
        super().__init__(status, "throttled", message, request_id)
        self.retry_after = retry_after


class GraphNotFound(GraphError):  # noqa: N818 - name fixed by the contract
    """404 on an item or message (used by the mail arm's cross-folder delete check)."""


class GitError(PublishError):
    """A git subprocess exited non-zero; carries the argv and stderr."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(argv)} failed ({returncode}): {stderr.strip()}")
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
