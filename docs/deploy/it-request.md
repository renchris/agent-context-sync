# IT request: agentsync on a managed Mac

Placeholders, one spelling each. Fill a copy of this page outside the repository and send it yourself. The
one-prompt setup writes that copy with `agentsync it-request --out ~/agent-context/it-request-draft.md` (mode 0600;
it never sends anything). The command fills every value this table says a command gives, lists the ones still open
at the top of the copy ("You fill: ..."), makes the links absolute and leaves out this table and the operator
section. The manifest block below is a copy of [`entra-app.json`](entra-app.json), so its two placeholders are
filled in the copy too.

| Placeholder | Meaning | Where the value comes from |
|---|---|---|
| `<it-contact>` | the IT address or queue this request goes to | you fill in |
| `<requester-name>` | the person asking | `id -F` (the Mac's user; you fill in if you ask for someone else) |
| `<requester-upn>` | the requester's work sign-in address, which IT assigns to the app | you fill in |
| `<team>` | the requester's team, also the owner note in `entra-app.json` | you fill in |
| `<serial>` | the Mac's serial number | `system_profiler SPHardwareDataType`, its `Serial Number` line |
| `<arch>` | the Mac's CPU (`arm64` or `x86_64`) | `uname -m` |
| `<org>` | the organisation name, also the app's display name in `entra-app.json` | `ls ~/Library/CloudStorage`: the name after `OneDrive-` or `OneDrive-SharedLibraries-`, never `Personal` (you choose if there are two) |
| `<arms-today>` | the sources this Mac already syncs without IT | `grep '^kind' ~/agent-context/sources.toml`: each `kind` and how many sources have it |
| `<date>` | the day the request is written | `date +%F` |
| `<wanted-by>` | the date you need it by | you fill in |
| `<it-owner>` | the IT co-owner of the app registration | IT fills in (leave it) |
| `<tenant-id>` | the Directory (tenant) ID | IT fills in (leave it; IT replies with it) |
| `<app-client-id>` | the Application (client) ID, created when IT registers the app | IT fills in (leave it; IT replies with it) |
| `<version>`, `<shared-mailbox-upn>` | parts of a format or an example, not values to fill | leave them |

---

**To:** `<it-contact>`. **Subject:** agentsync on a managed Mac: Entra app registration and admin consent.

**Requester:** `<requester-name>`, `<requester-upn>`, `<team>`. **Device:** `<serial>` (`<arch>` Mac).
**Written:** `<date>`. **Wanted by:** `<wanted-by>`.

agentsync is a command-line tool that runs as the signed-in user. It keeps a local, read-only markdown copy of
chosen OneDrive/SharePoint libraries, mail folders and Teams channels and chats in a git repository on the
Mac, so a coding agent can read them. It uses **delegated, read-only** Microsoft Graph permissions only: no
application permissions, no client secret or certificate, and no server. **No data leaves the Mac** (see the
last section). The local-folder part already runs without IT (on this Mac: `<arms-today>`). This request covers the
Graph part.

## What we ask for (no meeting needed)

1. **Register the app** from [`entra-app.json`](entra-app.json), using the settings below. The file is a Microsoft
   Graph `application` body, and the ids in it are checked (see "Permission ids").
2. **Grant tenant-wide admin consent** for the delegated scopes of the arms we use (matrix below). Remove the
   rows we do not need before you consent.
3. **Set "Assignment required" = Yes** on the enterprise application and assign `<requester-upn>` (or a pilot group).
4. **Optional, if MDM blocks the one-time privacy prompt:** deploy the PPPC profile in [`mdm/`](mdm/README.md).
5. **Reply with** the Application (client) ID and the Directory (tenant) ID. Neither is a secret.

## App registration settings

| Setting | Value | Why |
|---|---|---|
| Supported account types (`signInAudience`) | Single tenant (`AzureADMyOrg`) | a tenant-owned identity, not a vendor multi-tenant client id; agentsync refuses the `organizations`/`common` authorities (AADSTS50194) |
| Platform: **Mobile and desktop applications** (`publicClient.redirectUris`) | `http://localhost` and `msauth.com.msauth.unsignedapp://auth` | the browser sign-in (auth code + PKCE; MSAL picks the port and Entra ignores it for localhost) and the macOS broker (MSAL uses this URI for any Python app) |
| Allow public client flows (`isFallbackPublicClient`) | **No** | needed only for device-code sign-in, which is off by default (see Conditional Access) |
| Certificates & secrets | none | public client; delegated only |
| Enterprise app, **Assignment required** (`appRoleAssignmentRequired`) | **Yes**, pilot users assigned | consent then reaches only assigned users; anyone else gets AADSTS50105 |
| API permissions | delegated Microsoft Graph only, trimmed to the rows below | |
| Owners | `<requester-upn>` + `<it-owner>` | |

The manifest, byte-identical to [`entra-app.json`](entra-app.json). Paste it into a new registration's manifest
(Download, edit, Upload), or post it with `az` as shown below:

```json
{
  "displayName": "agentsync (<org>)",
  "signInAudience": "AzureADMyOrg",
  "isFallbackPublicClient": false,
  "notes": "Public client for agentsync on managed Macs. Delegated, read-only Microsoft Graph scopes; no secrets or certificates. Owner: <team>. Remove scopes for arms not in use (docs/deploy/it-request.md).",
  "publicClient": {
    "redirectUris": [
      "http://localhost",
      "msauth.com.msauth.unsignedapp://auth"
    ]
  },
  "requiredResourceAccess": [
    {
      "resourceAppId": "00000003-0000-0000-c000-000000000000",
      "resourceAccess": [
        { "id": "37f7f235-527c-4136-accd-4a02d197296e", "type": "Scope" },
        { "id": "14dad69e-099b-42c9-810b-d002981feec1", "type": "Scope" },
        { "id": "7427e0e9-2fba-42fe-b0c0-848c9e6a8182", "type": "Scope" },
        { "id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d", "type": "Scope" },
        { "id": "df85f4d6-205c-4ac5-a5ea-6bf408dba283", "type": "Scope" },
        { "id": "205e70e5-aba6-4c52-a976-6d2d46c48043", "type": "Scope" },
        { "id": "570282fd-fa5c-430d-a7fd-fc8dc98a9dca", "type": "Scope" },
        { "id": "7b9103a5-4610-446b-9670-80643382c1fa", "type": "Scope" },
        { "id": "485be79e-c497-4b35-9400-0e3fa7f2a5d4", "type": "Scope" },
        { "id": "9d8982ae-4365-4f57-95e9-d6032a4c0b87", "type": "Scope" },
        { "id": "767156cb-16ae-4d10-8f8b-41b657c8c8c8", "type": "Scope" },
        { "id": "9547fcb5-d03f-419d-9948-5928bbf71b0f", "type": "Scope" },
        { "id": "f501c180-9344-439a-bca0-6cbf209fd270", "type": "Scope" }
      ]
    }
  ]
}
```

From the CLI, using standard `az` verbs (trim the file first):

```sh
az rest --method POST --url https://graph.microsoft.com/v1.0/applications --body @entra-app.json
az ad sp create --id <app-client-id>
az ad sp update --id <app-client-id> --set appRoleAssignmentRequired=true
az ad app permission admin-consent --id <app-client-id>
```

## Delegated permissions, per source kind

"Admin consent" is whether a user can consent under Microsoft's default consent policy. That policy excludes
`Files.Read.All`, `Sites.Read.All`, `Mail.Read*` and `Chat.Read` from user consent even though the reference does not
flag them. In practice every content scope here needs you.

| Source kind (sources.toml `kind`) | Scope | Why agentsync needs it | Admin consent |
|---|---|---|---|
| any Graph source (sign-in, refresh) | `openid`, `profile`, `offline_access`, `User.Read` | sign in, keep a refresh token in the user's Keychain, and read their own profile | no |
| OneDrive / SharePoint library (`graph_drive`) | `Files.Read.All` | `GET /drives/{id}/root/delta` on the user's own drive and on libraries shared with them | **yes** (default policy) |
| SharePoint site by URL, library discovery | `Sites.Read.All` | `GET /sites/{host}:/{path}`, `/sites/{id}/drive`, `/me/followedSites` | **yes** (default policy) |
| Outlook folder (`graph_mail`, `mailbox = "me"`) | `Mail.Read` | `GET /me/mailFolders/delta` and per-folder `messages/delta`, plus the MIME body of each changed message | **yes** (default policy) |
| shared mailbox (`graph_mail`, `mailbox = "<shared-mailbox-upn>"`) | `Mail.Read.Shared` | the same calls under `/users/{shared}/…`, only for mailboxes the user can already open | **yes** (default policy) |
| Teams channel (`graph_teams`) | `Team.ReadBasic.All`, `Channel.ReadBasic.All` | list joined teams and channels (discovery, names) | no |
| Teams channel (`graph_teams`) | `ChannelMessage.Read.All` | `GET /teams/{t}/channels/{c}/messages?$expand=replies`, paced to 1 request/s per channel | **yes** (always) |
| Teams 1:1 / group chat (`graph_teams`, `team_id = "chats"`) | `Chat.ReadBasic`, `Chat.Read` | list chats; read the messages of the chats the user picks | list: no; messages: **yes** (default policy) |

Minimum sets: **files only** is `User.Read Files.Read.All Sites.Read.All` (plus the three sign-in scopes); add
`Mail.Read` for mail and the Teams rows for Teams. Remove every other `resourceAccess` entry from the JSON before
you register it. The table leaves out OneNote (`Notes.Read`), since agentsync has no OneNote arm.

**Least blast radius for SharePoint:** use `Sites.Selected` (delegated id `f89c84ef-20d0-4b54-87e9-02e856d66d53`)
instead of `Sites.Read.All`, and grant it per site. The user then sees only the sites a SharePoint admin grants to
this app. The trade-off is that discovery of other sites reports "403: needs a site grant".

**Not requested, because delegated access does not support them:** `/chats/getAllMessages`, channel
`getAllMessages`, channel message delta, and any application permission. agentsync's build fails on those URLs
(a lint).

**Permission ids.** The ids in `entra-app.json` are the delegated ids from the Microsoft Graph permissions
reference: `concepts/permissions-reference.md` in `microsoftgraph/microsoft-graph-docs-contrib`, last changed at
`47f65201` (2026-08-04) and fetched 2026-09-29. In order: `openid`, `profile`, `offline_access`, `User.Read`,
`Files.Read.All`, `Sites.Read.All`, `Mail.Read`, `Mail.Read.Shared`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`,
`ChannelMessage.Read.All`, `Chat.ReadBasic`, `Chat.Read`. All 13 match the ids in
[C15 §1.6](../design/receipts/verify/C15-corporate-controls.md).

## Admin consent

Use **Entra admin center › App registrations › agentsync (`<org>`) › API permissions › Grant admin consent for
`<org>`**, or open this URL signed in as a Cloud Application Administrator or Application Administrator. Those
roles suffice for delegated Microsoft Graph permissions.

```text
https://login.microsoftonline.com/<tenant-id>/adminconsent?client_id=<app-client-id>
```

Sovereign clouds use their own login host: `login.microsoftonline.us` for GCC High and DoD, and
`login.chinacloudapi.cn` for 21Vianet. GCC (moderate) uses the global host. When a user runs `agentsync graph
login` before consent exists, it prints this URL for them to forward.

## Conditional Access

- **Sign-in order:** the macOS broker (Company Portal with the Enterprise SSO plug-in) comes first, then a system
  browser with auth code + PKCE, then device code, which is used only if `[graph] allow_device_code = true`.
  Background runs only ever redeem cached tokens silently and never open a browser.
- **"Require compliant device":** the broker is the only path by which this Python process presents the Mac's Entra
  device identity. It needs Company Portal, MDM enrollment and the SSO-extension payload
  (`com.microsoft.CompanyPortalMac.ssoextension`, Team ID `UBF8T346G9`). Without them, sign-in fails AADSTS53000 or
  530003 and agentsync reports `blocked: device`. A browser sign-in may also carry device identity: Safari does this
  natively, and Chrome needs the Microsoft SSO extension or version 135 or later. Whether a later silent refresh
  keeps the device claim has not been measured, so the broker is the supported path.
- **Device code:** Microsoft's managed policy "Block device code flow" is expected to refuse it (AADSTS53003,
  `blocked: policy`). We do not ask for an exception.
- **Sign-in frequency / MFA:** when a token expires, the background job stops with exit 77 (`reauth-required`) and
  holds its cursors. The user runs `agentsync graph login` again. Nothing retries against Entra.
- **TLS inspection:** exclude `login.microsoftonline.com` and the other SSO-extension URLs from break-and-inspect
  (Microsoft's requirement for the SSO plug-in). agentsync itself trusts the macOS System keychain (truststore),
  so an MDM-installed inspection root CA works for Graph.

What the user reports, and what IT does about it:

| agentsync says | AADSTS | IT action |
|---|---|---|
| `blocked: consent` | 65001, 90094, 90095 | grant admin consent (above) |
| `blocked: assignment` | 50105 | assign the user to the enterprise app |
| `blocked: device` | 50097, 53000, 53001, 530003, 135011 | enroll the Mac or fix compliance; Company Portal + SSO extension |
| `blocked: policy` | 53003 | a CA policy blocked this sign-in (read the sign-in log for the policy name) |
| `config-invalid` | 50011, 50194 | redirect URI missing, or the app is not single-tenant |

## What leaves the Mac

- **Tenant data: nothing.** Content is read from Microsoft 365 and written only to the user's Mac:
  `~/agent-context/docs` (git, **no remote**, and agentsync refuses to add one unless `[governance]` names a
  tenant-owned remote), `~/Library/Application Support/agentsync` and `~/Library/Caches/agentsync`. There is no
  telemetry.
- **Network:** HTTPS to `login.microsoftonline.com`, `graph.microsoft.com` and the tenant's `*.sharepoint.com`
  download redirects. Installation also fetches `astral.sh`/`github.com` (uv) and `pypi.org`/`files.pythonhosted.org`
  (packages). The User-Agent is `NONISV|agentsync|agentsync/<version>`, so SharePoint logs identify the traffic.
- **Credentials:** the MSAL token cache is kept in the user's login Keychain (service `agentsync`), or by the broker.
  Nothing lands on disk in plaintext unless the Keychain is unavailable, in which case a 0600 file is used and a
  warning is logged.
- **Revoke:** remove the assignment or disable the enterprise app, then revoke the user's sessions. The next
  background run stops with exit 77. The user removes local copies with `agentsync offboard`
  ([data-governance.md](data-governance.md)).

## After IT replies (operator)

```sh
# ~/agent-context/sources.toml, [graph]: client_id = "<app-client-id>", tenant = "<tenant-id>", and the consented scopes, e.g.
#   scopes = ["User.Read", "Files.Read.All", "Sites.Read.All", "Mail.Read", "Team.ReadBasic.All", "Chat.ReadBasic"]
mkdir -p "$HOME/Library/Application Support/agentsync/probes"
agentsync graph login 2>&1 | tee "$HOME/Library/Application Support/agentsync/probes/login-$(date +%Y%m%d).txt"
scripts/tenant-probes.sh          # read-only; writes a dated report (tenant-probes.md)
agentsync discover                # proposes sources.toml tables; paste, set state = "live"
```
