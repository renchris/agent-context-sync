"""Microsoft Graph arm A: auth + HTTP client (owner: graph-core) and delta arms (owner: graph-arms).

Modules: ``auth`` (MSAL device-code sign-in, Keychain token cache), ``client`` (httpx client: paging, delta,
Retry-After, typed errors, downloads), ``errors`` (re-exported Graph error types), ``drive`` / ``mail`` /
``teams`` (delta arms). Nothing is imported here so ``import agentsync.graph`` stays cheap and cycle-free.
"""
