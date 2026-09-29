# C15 — Corporate controls: sign-in, Graph scopes, TCC, labels, TLS, PDF route, retention (researched and measured 2026-09-29)

**Verdict.** Every control a managed tenant applies has a documented route that the pipeline can implement without
tenant-wide app-only rights. None of those routes is the one the design or the W1 contracts currently specify.
(1) Sign-in must go through the MSAL macOS broker (Company Portal), with auth-code + PKCE on a loopback redirect as
the fallback. Device code is last resort, because Conditional Access cannot evaluate device state on it.
(2) Teams channels and chats are readable with delegated scopes, but by high-water paging, not `delta`.
`sharedWithMe` and `insights/shared` both stop returning data after November 2026.
(3) Reads of `~/Library/CloudStorage/…` from a launchd job go through **`kTCCServiceFileProviderDomain`**. What
happens depends on the executable: an Apple platform binary gets `EPERM` at once; an unapproved third-party binary
triggers a user prompt and `open()` blocks until someone answers it; an approved one reads normally. PPPC has no key
for that service, but a Full Disk Access grant to the responsible process is checked first and ends the check.
(4) Labels live in `docMetadata/LabelInfo.xml` first and in `docProps/custom.xml` second, so a label change is a
content change.
(5) Both HTTP stacks (httpx and MSAL's `requests`) ignore the macOS trust store and PAC files by default.
(6) The design's own converter evidence supports Docling with the pypdfium2 backend, not pdfminer.six.
(7) A git mirror sits outside Purview retention, hold and eDiscovery. It therefore needs a purge verb that reaches
history, bounded history, and no remote by default.

Evidence classes: **[documented]** is a primary source, with its URL and a verbatim quote. **[measured]** is a command
run on this Mac today, with the command named. **[reasoning]** is inference and has not been verified.

## 0. Setup and sources

- Mac: macOS 15.7.9 (24G830), arm64, OneDrive 26.168.0830, domain `OneDrive-Contoso` mounted at
  `~/Library/CloudStorage/OneDrive-Contoso`. The Mac is not MDM-enrolled, has no Company Portal
  (`ls "/Applications/Company Portal.app"` → No such file), and uses no proxy (`scutil --proxy` shows only
  `ExceptionsList`). [measured]
- Sources were fetched 2026-09-29. Microsoft Graph docs came from `microsoftgraph/microsoft-graph-docs-contrib` at
  `4ad99fd3`, Entra docs from `MicrosoftDocs/entra-docs` at `c67d748f`, and the Apple PPPC schema from
  `apple/device-management` `release` at `09f249a0`. MSAL Python is tag `1.39.0` (`20fd4d9f`). Learn pages are cited
  with their `updated_at` date.
- Scratch venv `/tmp/c15-enc` (Python 3.11): msoffcrypto-tool 6.0.0 (MIT), olefile 0.47 (BSD), python-docx 1.2.0,
  pypdf 6.19.0, msal[broker] 1.39.0 with pymsalruntime 0.20.6. The project's `.venv` was only read, with
  `PYTHONDONTWRITEBYTECODE=1`. Nothing was written to OneDrive or to any tracked file other than this receipt.

## 1. Sign-in on a managed Mac

### 1.1 Primary: the MSAL macOS broker

- [documented] https://learn.microsoft.com/en-us/entra/msal/python/advanced/macos-broker (updated 2026-02-02):
  "macOS authentication broker support is introduced with `msal` version 1.31.0." The page also says: "Authentication
  brokers are **not** pre-installed on macOS but are applications developed by Microsoft, such as Company Portal.
  These applications are usually installed when a macOS computer is enrolled in a company's device fleet via an
  endpoint management solution like Microsoft Intune." Install with `pip install msal[broker]>=1.31,<2`. Opt in with
  `PublicClientApplication(..., enable_broker_on_mac=True)` and call
  `acquire_token_interactive(scopes, parent_window_handle=app.CONSOLE_WINDOW_HANDLE)`. On the redirect URI: "For
  *unsigned* applications, the URI is: `msauth.com.msauth.unsignedapp://auth`". A missing URI fails with
  `AADSTS50011 … errorCode=-51411`. On caching: "The authentication broker handles refresh and access token caching.
  You do not need to set up custom caching."
- [documented] MSAL Python 1.39.0 source,
  https://github.com/AzureAD/microsoft-authentication-library-for-python/blob/1.39.0/msal/application.py. The
  opt-in table reads "`enable_broker_on_mac` | Apple Silicon Mac with Company Portal installed |
  `msauth.com.msauth.unsignedapp://auth`". An Intel or Rosetta process is refused: "Broker on macOS is supported only on
  Apple Silicon (arm64). We will fallback to non-broker." (release 1.39.0, 2026-09-17, PR #926.) `msal/broker.py`
  hard-codes `_default_redirect_uri_on_mac = "msauth.com.msauth.unsignedapp://auth"`, so a Python CLI registers the
  *unsigned* URI even when its launcher is signed. Release 1.37.0 (2026-06-01) moved silent broker flows to that URI
  and raised the pymsalruntime floor to 0.20.6.
- [measured] PyPI JSON: msal 1.39.0 declares `pymsalruntime<0.21,>=0.20; platform_system == "Darwin" and extra ==
  "broker"`. pymsalruntime 0.20.6 (MIT) ships macOS wheels for cp311–cp314 (`macosx_11_0_arm64`) and cp310
  (`macosx_15_0_arm64`).
- [measured] In `/tmp/c15-enc`, on this Mac without Company Portal,
  `PublicClientApplication("<dummy>", authority=".../organizations", enable_broker_on_mac=True)` logged
  `WARNING:msal.application:Broker is unavailable on this platform. We will fallback to non-broker.` and left
  `_enable_broker == False` (constructor 0.66 s). **The fallback is silent apart from that log line.** The app has to
  record which path issued each token. MSAL returns this as `token_source` (`broker` / `identity_provider` / `cache`),
  per the 1.25.0 release note: "Successful token response will contain a new `token_source` field".

### 1.2 How the broker satisfies a compliant-device Conditional Access policy

- [documented] https://learn.microsoft.com/en-us/entra/identity-platform/apple-sso-plugin (updated 2026-06-15).
  Requirements: "macOS 10.15 and later: Intune Company Portal app"; "The device must be *enrolled in MDM*";
  "Configuration must be *pushed to the device* to enable the Enterprise SSO plug-in." Without Intune the MDM payload
  is Extension ID `com.microsoft.CompanyPortalMac.ssoextension`, Team ID `UBF8T346G9`, type Redirect, with URLs
  `https://login.microsoftonline.com` and the others listed. The page states, of apps that do not use MSAL: "if the
  device is known to Microsoft Entra ID, the SSO plug-in passes the device certificate to satisfy the device-based
  Conditional Access check". Of MSAL for Apple devices (the Objective-C library; MSAL Python reaches the plug-in
  through pymsalruntime instead) it says: "On devices that have the SSO plug-in, MSAL automatically invokes it for
  all interactive and silent token requests." It also states:
  "Managed devices using Secure Enclave for storing device identity keys will also need to be provisioned with
  Enterprise SSO or Platform SSO to report device identity to Microsoft Entra ID." It warns: "If your organization
  uses proxy servers that intercept SSL traffic … ensure that traffic to these URLs are excluded from TLS
  break-and-inspect. Failure to exclude these URLs cause interference with client certificate authentication, cause
  issues with device registration, and device-based Conditional Access."
- [documented] https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-grant
  (updated 2026-06-16): "Require device to be marked as compliant … Only supports Windows 10+, iOS, Android, macOS,
  and Linux Ubuntu devices registered with Microsoft Entra ID and enrolled with Intune."
- [documented] MSAL 1.39.0 docstring: "Broker implicitly gives your device an identity. By using a broker, your device
  becomes a factor that can satisfy MFA … This factor would become mandatory if a tenant's admin enables a
  corresponding Conditional Access (CA) policy."
- [documented] https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-token-protection (updated
  2026-08-20). Token Protection applies to native apps against "Exchange Online, SharePoint Online, Microsoft Teams".
  Its Apple section reads: "macOS 14.0 or later. Requires the Microsoft Enterprise single sign-on (SSO) plug-in.
  Alternatively, you can also use Platform SSO. Only MDM-managed devices are supported." The same page labels macOS
  native apps "Generally Available" in its table and "Apple (Preview)" in its device list.
- [reasoning] The mechanism, then: the broker is the only route in which a Python process presents the Entra device
  identity. That identity is a Secure Enclave key held by the SSO extension. Without it, a compliant-device policy
  answers `AADSTS53000` (DeviceNotCompliant) or `530003` (device must be managed). It is unverified whether a Graph
  token for SharePoint data falls under a Token Protection policy scoped to "SharePoint Online". Probe it (§8).

### 1.3 Fallback: interactive auth code + PKCE on a loopback redirect

- [documented] MSAL 1.39.0 `acquire_token_interactive`: "Prerequisite: In Azure Portal, configure the Redirect URI of
  your "Mobile and Desktop application" as `http://localhost`." The same docstring says "(The rest of the redirect_uri
  is hard coded as `http://localhost`.)" and that the port defaults to a system-allocated one.
  `msal/oauth2cli/oauth2.py`: "This method also provides PKCE protection automatically" (`"transformation": "S256"`).
- [documented] https://learn.microsoft.com/en-us/entra/identity-platform/reply-url: "`http` URI schemes are acceptable
  because the redirect never leaves the device", and "the port component … is ignored for the purposes of matching a
  localhost redirect URI".
- [documented] apple-sso-plugin, same page: a system-browser sign-in can carry device identity. Safari has "Built-in
  SSO integration". Chrome needs the Microsoft Single Sign On extension or Chrome 135+. Edge needs a signed-in
  profile.
- [reasoning] Refresh tokens redeemed later by a non-broker Python process may lose the device claim, and a
  compliant-device policy would then fail with `AADSTS53000` at refresh. This is not measured. It is the reason the
  broker is primary and not merely preferred.

### 1.4 Last resort: device code

- [documented] concept-conditional-access-grant: "When you use the device-code OAuth flow, the required grant control
  for the managed device or a device state condition isn't supported. This is because the device that is performing
  authentication can't provide its device state to the device that is providing a code."
- [documented] https://learn.microsoft.com/en-us/entra/identity/conditional-access/policy-block-authentication-flows
  (updated 2026-04-07): "We recommend organizations get as close as possible to a unilateral block on device code
  flow."
- [documented] https://learn.microsoft.com/en-us/entra/identity/conditional-access/managed-policies (repository
  `docs/identity/conditional-access/managed-policies.md` at `c67d748f`). Prerequisites: "Microsoft Entra ID P2 or
  Microsoft 365 Business Premium licenses is required to use Conditional Access features". Microsoft deploys the
  policies "to eligible tenants based on licensing and feature eligibility", and "Microsoft enables these policies no
  less than 30 days after they're introduced in your tenant if they're left in the **Report-only** state." Of the
  device-code policy itself: "Device code flow is rarely used by customers, but is frequently used by attackers."
  [reasoning] The Business Standard trial tenant used for C14 falls outside that licensing, so it could not have shown
  the block.
- [documented] https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-applications: "By default, allow
  public client flow in your app registration should be disabled unless … using the following OAuth authorization
  protocol or features", and the list includes "Device code flow". So `isFallbackPublicClient` ("Allow public client
  flows") is needed **only** for device code. The broker and loopback rungs work through `publicClient.redirectUris`
  alone [reasoning, from MSAL's redirect-URI prerequisites above].

> CORRECTED (2026-09-29): `docs/design/CONTRACTS.md` §10 specifies "device-code flow only" with a default tenant of
> `organizations`. Device code is blocked in CA-licensed tenants that keep the managed policy (above). A single-tenant
> registration refuses the multi-tenant authority with `AADSTS50194` ("Use a tenant-specific endpoint"). The message
> names `/common`; that `/organizations` behaves the same way is reasoning.
> Use broker → loopback → device code, against `https://login.microsoftonline.com/<tenant-id>`.

### 1.5 Sign-in errors the pipeline must classify

The table below comes from [documented] https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes.
None of these errors is retried.

| AADSTS | Meaning (quoted short name) | Pipeline state |
|---|---|---|
| 50076, 50079 | UserStrongAuthClientAuthNRequired / …EnrollmentRequired | `reauth-required` |
| 50097 | DeviceAuthenticationRequired | `blocked: device` |
| 53000 | DeviceNotCompliant | `blocked: device` |
| 53001 | DeviceNotDomainJoined | `blocked: device` |
| 530003 | device is required to be managed (apple-sso-plugin page, Secure Enclave troubleshooting) | `blocked: device` |
| 53003 | BlockedByConditionalAccess (device code blocked lands here) | `blocked: policy` |
| 70043 | BadTokenDueToSignInFrequency | `reauth-required` |
| 50173 | grant revoked (password reset) | `reauth-required` |
| 65001, 90094, 90095 | DelegationDoesNotExist / AdminConsentRequired | `blocked: consent (IT action)` |
| 50105 | EntitlementGrantsNotFound (user not assigned to the app) | `blocked: assignment (IT action)` |
| 50011 | InvalidReplyTo (redirect URI missing) | `config-invalid` |
| 50194 | single-tenant app used with `/common` | `config-invalid` |
| 135011 | device disabled | `blocked: device` |

### 1.6 The Entra app registration an IT admin creates

| Setting | Value | Why |
|---|---|---|
| Supported account types (`signInAudience`) | `AzureADMyOrg` (single tenant) | tenant-owned identity; not a vendor multi-tenant client id |
| Platform "Mobile and desktop applications" (`publicClient.redirectUris`) | `http://localhost` and `msauth.com.msauth.unsignedapp://auth` | loopback rung (MSAL hard-codes `http://localhost:<port>`); broker rung (MSAL hard-codes the unsigned URI on macOS) |
| Allow public client flows (`isFallbackPublicClient`) | `false`; `true` only if IT approves device code as last resort | msal-client-applications, §1.4 |
| Client secrets / certificates | none | public client, delegated only |
| Enterprise app → Assignment required (`appRoleAssignmentRequired` on the service principal) | `true`, with the pilot users assigned | admin consent then reaches only assigned users; others get `AADSTS50105` |
| API permissions | delegated only; the §2 matrix, trimmed to the arms in use | |
| Admin consent | "Grant admin consent for <tenant>" | §2: the file, mail and chat-message scopes are admin-only under the default consent policy, and `ChannelMessage.Read.All` always is |

[documented] Manifest editing, from
https://learn.microsoft.com/en-us/entra/identity-platform/reference-microsoft-graph-app-manifest: "select **Download**
to edit the manifest locally, and then use **Upload** to reapply it to your application." The JSON below is a
Microsoft Graph `application` body. It can be pasted into a new registration's manifest, or created with
`az rest --method POST --url https://graph.microsoft.com/v1.0/applications --body @entra-app.json`, followed by
`az ad sp create --id <appId>`, `az ad sp update --id <appId> --set appRoleAssignmentRequired=true` and
`az ad app permission admin-consent --id <appId>`. [reasoning: these are standard az verbs, not run here.] The
permission ids were [measured] from `concepts/permissions-reference.md` at `4ad99fd3` (Graph `resourceAppId`
`00000003-0000-0000-c000-000000000000`). The JSON syntax was validated with `python3 -m json.tool`; the body was not
submitted to a tenant.

```json
{
  "displayName": "agentsync (<org>)",
  "signInAudience": "AzureADMyOrg",
  "isFallbackPublicClient": false,
  "notes": "Public client for agentsync on managed Macs. Delegated, read-only Microsoft Graph scopes; no secrets or certificates. Owner: <team>. Remove scopes for arms not in use.",
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
        { "id": "f501c180-9344-439a-bca0-6cbf209fd270", "type": "Scope" },
        { "id": "371361e4-b9e2-4a3f-8315-2a301a3b0a3d", "type": "Scope" }
      ]
    }
  ]
}
```

Id → scope, in the same order: `openid`, `profile`, `offline_access`, `User.Read`, `Files.Read.All`,
`Sites.Read.All`, `Mail.Read`, `Mail.Read.Shared`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`,
`ChannelMessage.Read.All`, `Chat.ReadBasic`, `Chat.Read`, `Notes.Read`. A tenant that grants SharePoint per site
replaces `Sites.Read.All` with `Sites.Selected` (delegated `f89c84ef-20d0-4b54-87e9-02e856d66d53`) plus a per-site
grant made by the SharePoint admin.

## 2. Graph permissions matrix (delegated, work or school)

The "Ref. admin" column is the `AdminConsentRequired` flag in the permissions reference [measured, parsed from
`permissions-reference.md`]. "Default policy" applies [documented]
https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-app-consent-policies, which says the Microsoft
managed policy "is also the default for a new tenant. The setting's rules are currently: End users can consent for any
user consentable delegated permissions EXCEPT: For Microsoft Graph: `Files.Read.All`, `Files.ReadWrite.All`,
`Sites.Read.All`, `Sites.ReadWrite.All`, `Mail.Read`, `Mail.ReadWrite`, `Mail.ReadBasic`, `Mail.Read.Shared`, …
`Chat.Read`, `Chat.ReadWrite`, … `People.Read`." Each endpoint's least-privileged scope comes from that endpoint's
`includes/permissions/*-permissions.md` table [measured].

| Source kind | Endpoint and pattern | Least privileged delegated | Ref. admin | Default policy |
|---|---|---|---|---|
| sign-in, refresh | `openid profile offline_access User.Read` | — | No | user can consent |
| OneDrive / SharePoint files | `GET /drives/{id}/root/delta` (the doc's HTTP section lists only `root/delta` paths) | `Files.Read` (own drive); `Files.Read.All` or `Sites.Read.All` for others' drives and libraries | No | **admin** (`*.Read.All` excluded) |
| SharePoint, least blast radius | same, under `Sites.Selected` plus a per-site grant | `Sites.Selected` | No | user can consent, but no data without the SharePoint admin's site grant |
| label of a driveItem | `POST /drives/{id}/items/{id}/extractSensitivityLabels` | `Files.Read.All` | No | **admin** |
| site / library discovery | `GET /sites/{hostname}:/{path}`, `GET /sites/{id}/drives` | `Sites.Read.All`; `Files.Read` for drive list | No | **admin** |
| shared-link discovery (replaces `sharedWithMe`) | `GET /shares/u!{base64url(url)}/driveItem` | **`Files.ReadWrite`** per the docs table (probe `Files.Read.All`, §8) | No | user can consent |
| content search discovery | `POST /search/query` with `entityTypes: ["driveItem"]` or `["site"]` | per entity type: `Files.Read.All` / `Sites.Read.All` (the table's least privileged entry, `Mail.Read`, is for mail) | No | **admin** |
| mail folder discovery | `GET /me/mailFolders/delta` | `Mail.ReadBasic` (no bodies) | No | **admin** |
| mail messages | `GET /me/mailFolders/{id}/messages/delta`, one cursor per folder | `Mail.Read` (bodies); `Mail.ReadBasic` has no body | No | **admin** |
| shared mailbox | `GET /users/{shared}/mailFolders/{id}/messages/delta` | `Mail.Read.Shared` — **not listed** on the message-delta table (probe, §8) | No | **admin** |
| Teams discovery | `GET /me/joinedTeams`; `GET /teams/{id}/channels` (`allChannels` for shared channels) | `Team.ReadBasic.All`; `Channel.ReadBasic.All` | No | user can consent |
| channel messages | `GET /teams/{t}/channels/{c}/messages?$top=50&$expand=replies`, then `…/messages/{id}/replies`; high-water walk (below) | `ChannelMessage.Read.All` | **Yes** | **admin** |
| 1:1 and group chats | `GET /me/chats?$orderby=lastMessagePreview/createdDateTime desc`; `GET /me/chats/{id}/messages?$orderby=lastModifiedDateTime desc&$filter=lastModifiedDateTime gt {hwm}` | `Chat.ReadBasic` (list); `Chat.Read` (messages) | No | list: user; messages: **admin** |
| OneNote (own) | notebooks and pages | `Notes.Read` | No | user can consent |
| meeting transcripts (out of scope here) | transcripts API | `OnlineMeetingTranscript.Read.All` | **Yes** | admin |
| label catalogue (optional) | beta `GET /me/security/informationProtection/sensitivityLabels` | `InformationProtectionPolicy.Read` | No | user; **beta only** |
| **not usable delegated** | `/users/{id}/chats/getAllMessages` and `/delta`; beta `/teams/{id}/channels/getAllMessages` | "Delegated (work or school account) — Not supported." | | |

**Net [reasoning]:** in a default-policy tenant, every file, mail and chat-message scope needs admin consent, even
though only `ChannelMessage.Read.All` (and the out-of-scope transcripts scope) carries the reference flag. `Notes.Read`
is the one content scope a user can consent to. The IT request therefore has to ask for tenant admin consent
plus assignment-required. Asking users to consent will not work.

Endpoint facts the arms depend on:

- **Channels** [documented] `api/channel-list-messages.md` (ms.date 10/02/2024): "Retrieve the list of messages
  (without the replies)". For `$top`: "The default page size is 20 messages. You can extend up to 50 channel messages
  per page." For `$expand` (replies): "By default, a response can include up to 200 replies … use the request URL
  returned in `replies@odata.nextLink`". "The other OData query parameters aren't currently supported." "The channel
  messages in the response are sorted by the last modified date of the entire reply chain, including both the root
  channel message and its replies." [reasoning] The incremental read is therefore: page newest-first, compute each
  chain's modified time as `max(root.lastModifiedDateTime, replies[*].lastModifiedDateTime)`, and stop at the first
  chain older than `HWM − skew`. The first bootstrap walks to the end of the channel, and there is no documented
  8-month cap on this endpoint.
- **Chats** [documented] `api/chat-list-messages.md`: "`$top` … Maximum allowed `$top` value is 50". For `$orderby`:
  "Currently supports the **lastModifiedDateTime** (default) and **createdDateTime** properties in descending order."
  For `$filter`: "The **lastModifiedDateTime** property supports the `gt` and `lt` operators … You can only filter
  results if the request URL contains the `$orderby` and `$filter` query parameters configured for the same property;
  otherwise, the `$filter` query option is ignored."
- **Chat delta is app-only and bounded** [documented] `api/chatmessage-delta.md`: the only path is
  `GET /users/{id}/chats/getAllMessages/delta`; delegated access is "Not supported"; "Delta only returns messages
  within the last eight months."
- **Teams rate** [documented] `includes/throttling-teams.md`: `GET channel message` and `GET 1:1/group chat message`
  are limited to 20 rps per app per tenant and 1 rps per app per tenant per chat or channel. The text adds: "A maximum
  of one request per second per app per tenant can be issued on a given channel or chat."
- **Mail** [documented] `concepts/delta-query-messages.md`: "Delta query is a per-folder operation." From
  `concepts/outlook-immutable-id.md`: "To opt in, your application needs to send an additional HTTP header … `Prefer:
  IdType="ImmutableId"` … This header only applies to the request it is included with." Also: "immutable ID will NOT
  change if the item is moved to a different folder in the mailbox", and "The `@odata.nextLink` and `@odata.deltaLink`
  values returned by delta queries are compatible with both ID formats". From `concepts/delta-query-overview.md`: "For
  Outlook entities … the upper limit isn't fixed; it's dependent on the size of the internal delta token cache … In
  case the token expires, the service should respond with a 40X-series error with error codes such as
  `syncStateNotFound`." Also: "`410 Gone` and a **Location** header … is an indication that the application must
  restart with a full synchronization".
- **Discovery** [documented] `api/drive-sharedwithme.md`: "The **sharedWithMe** API is deprecated and will operate in
  a degraded state until November, 2026, after which it will stop returning data." `api/insights-list-shared.md`: "The
  `/insights/shared` API is deprecated and will stop returning data after November 2026." [reasoning] No documented
  1:1 replacement exists. The robust route is *declare, then resolve*: the operator pastes a sharing or site URL,
  resolved with `/shares/u!…/driveItem` or `/sites/{host}:/{path}`. Search is the optional *propose* step, and so are
  shortcuts the user added to "My files", which appear as `remoteItem` entries in the user's own root delta.

> CORRECTED (2026-09-29): design §4.2 says per-channel message delta is "delegated-capable". v1.0 documents no
> channel delta, and the beta `channels/getAllMessages` is application-only. The §4.2 source table marks "Teams 1:1 /
> group chats" as "application-only + protected API". Delegated `Chat.Read` lists chat messages with a `lastModifiedDateTime`
> high-water filter. Arm 0's `/me/drive/sharedWithMe` stops returning data after November 2026.

## 3. TCC on `~/Library/CloudStorage/<File Provider domain>`

### 3.1 Which service, and how it decides — measured

- [measured] `strings /System/Library/PrivateFrameworks/TCC.framework/Support/tccd` finds `kTCCServiceFileProviderDomain`
  and `kTCCServiceFileProviderPresence`. The English entries of `TCC.framework/Resources/Localizable.loctable` read:
  `REQUEST_ACCESS_SERVICE_kTCCServiceFileProviderDomain` → "“%@” wants to access files managed by “%@”."
  `…FileProviderPresence` → "Do you want to allow “%@” to see when you are using files managed by it? …" Presence
  is the File Provider *app's* own permission. It does not govern a reader.
- [measured] `tccutil` prints `Usage: tccutil reset SERVICE [BUNDLE_ID]`, and its man page says "One command is
  currently supported: reset". It has no query verb, so it was not run. TCC.db was not read.
- [measured] `log show --style compact --predicate 'subsystem == "com.apple.TCC"' --last 2h` returned 23,655 lines,
  98 of them mentioning File Provider. For every CloudStorage access by a process without FDA, `sandboxd` did the
  following, in order:
  1. asked the **system** `tccd` (`tccd_uid=0`) for `kTCCServiceSystemPolicyAllFiles` for the responsible process;
  2. when that returned `authValue=0`, called `TCCCreateIndirectObjectIdentityForFileProviderDomainFromPath`
     (`kTCCIndirectObjectFileProviderDomainID = "com.microsoft.OneDrive.FileProvider/OneDrive - Contoso"`);
  3. called `TCCAccessRequestIndirect` on the **user's** `tccd` (`tccd_uid=501`).

  In the one logged case where FDA was present, step 2 did not happen. At 14:52:48 `walkfp` ran from the terminal, TCC
  attributed it `responsible={kitty} accessing={walkfp}`, `SystemPolicyAllFiles` returned `authValue=2`, and no File
  Provider request followed. The path is not logged, so this is a sequence observation.

| Executable, run as a launchd job (`probes/launchd-run.sh`) | Signature (`codesign -d -r-`) | TCC path (unified log) | TCC decision time | Job outcome |
|---|---|---|---|---|
| `/bin/cat …/agentsync-probe/note2.txt` (downloaded, 33 B, 8 blocks) | `identifier "com.apple.cat" and anchor apple` (platform) | FDA `authValue=0` → indirect → result **0**, no prompt ("Platform binary prompting is 'Deny'") | 6 ms (15:37:32.235 → .241) | `Operation not permitted`, exit 1 in 3 of 3 runs |
| `/bin/ls` on the same domain, 5 runs by another session (14:52:17–14:53:27) | platform | same | 2–4 ms | result 0 |
| `probes/bin/readfp default …/note2.txt` | `adhoc,linker-signed`, no TeamID | FDA 0 → indirect → result **1** (a grant already existed) | 3 ms | `read=ok bytes=33`, exit 0 |
| `probes/bin/walkfp` (rebuilt 14:39 from modified source), run by another session at 14:52:40 | adhoc | FDA 0 → indirect → **"Prompting for access to indirect object OneDrive by walkfp"** → result 1 → `TCCDEvent: type=Create, service=kTCCServiceFileProviderDomain, identifier_type=Path, identifier=…/probes/bin/walkfp` | **2.24 s** (the prompt was answered) | — |
| `/bin/cat /tmp/c15-local.txt` (control) | platform | none | — | ok, exit 0 |

Harness wall-clock [measured: `time.time()` around `perl -e 'alarm 40; exec @ARGV' probes/launchd-run.sh …`]:
- CloudStorage `EPERM`: 3 runs with the harness as updated today, 0.23–0.29 s. One earlier run with the pre-update
  harness took 0.42 s and exited 0, because that harness did not propagate the job's status.
- Local control: 2 runs, 0.23–0.24 s.
- Approved `readfp`: 2 runs, 0.21–0.26 s.

The stat of `note2.txt` stayed `flags=0x40 blocks=8` throughout. Nothing was downloaded and nothing was written.

> CORRECTED (2026-09-29): design §4.7 predicts "a background agent cannot prompt — an EPERM reads as an empty
> directory — so grant Full Disk Access to the interpreter". Three outcomes are measured, and they depend on the
> executable launchd starts. An Apple platform binary gets `EPERM` at once, with no prompt. A third-party binary with
> no grant raises a user prompt ("… wants to access files managed by OneDrive"), and its access waits on the answer.
> An approved binary reads normally. probes/README records a `readfp` hang of more than 20 s on 2026-09-24, "not yet
> explained". That hang is consistent with the prompt path when nobody answers it, although that exact run was not
> reproduced: reproducing it would put a dialog on the operator's screen.

### 3.2 Can MDM pre-grant it?

- [documented] Apple PPPC schema,
  https://github.com/apple/device-management/blob/release/mdm/profiles/com.apple.TCC.configuration-profile-policy.yaml.
  The `Services` keys include `SystemPolicyAllFiles` ("Allows the application access to all protected files") and
  `FileProviderPresence` ("Allows a File Provider application to know when the user is using files managed by the File
  Provider"). There is **no `FileProviderDomain` key**. The identity dictionary requires `Identifier`, `IdentifierType`
  (`bundleID` or `path`) and `CodeRequirement` ("Obtain this value by running `codesign -display -r -`"). `Allowed`:
  "If `true`, access is granted; otherwise, the process doesn't have access. The user isn't prompted and can't change
  this value." `IdentifierType`: "Application bundles must be identified by bundle ID. Nonbundled binaries must be
  identified by installation path. Helper tools embedded within an application bundle automatically inherit the
  permissions of their enclosing app bundle." Delivery: `userapprovedmdm: true`, `allowmanualinstall: false`, device
  channel only.
- [measured + reasoning] MDM therefore cannot grant the File Provider service directly. It can grant
  `SystemPolicyAllFiles`, and §3.1 shows that FDA on the responsible process is checked first and pre-empts the File
  Provider check. The price is that FDA covers every protected location (Mail, Messages, other apps' data), which is
  what security reviewers object to. The alternative is the narrower File Provider grant, which one user click creates
  (`identifier_type=Path` in the user's TCC database, with the executable's designated requirement).

### 3.3 What this means for a launchd-run Python interpreter

- [measured] The TCC subject of a launchd job is the executable launchd starts; the log showed no `responsible=` for
  `/bin/cat`, `readfp` or `walkfp`. A child inherits its parent as the responsible process, as in the kitty → `walkfp`
  case. The candidates on this Mac:
  - `.venv/bin/python` → `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11` is Developer ID
    `Python Software Foundation (BMM5U3QVKW)`, `identifier python3`. A grant or PPPC entry for it covers **every
    script** that interpreter runs.
  - Homebrew `python3` is ad hoc: `cdhash H"7e5b6e5e…"`. Its grant is lost on every `brew upgrade` [reasoning].
  - `/usr/bin/python3` is `identifier "com.apple.dt.xcode_select.tool-shim" and anchor apple`. As a platform binary it
    gets the `/bin/cat` outcome: `EPERM` with no prompt, and nothing a user can approve.
- [reasoning] Consequences:
  - Ship one Developer-ID-signed launcher executable inside an app bundle with a stable bundle id and TeamID. Make it
    `ProgramArguments[0]` and let it spawn Python as a child.
  - Get approval once. Without MDM, that is the user clicking Allow on the first launchd run while they are logged
    in. Approval must come from the launchd run: running the CLI from Terminal grants *the terminal*. With MDM, it is
    a PPPC `SystemPolicyAllFiles` entry pinned to `identifier "<bundle id>" and anchor apple generic and certificate
    leaf[subject.OU] = "<TEAMID>"`.
  - Treat a CloudStorage access that has not returned within a timeout as `tcc-pending`, never as empty.
  - That launcher-parent attribution under launchd has not been measured. It rests on the kitty case in §3.1, and
    probe 7 in §8 measures it.

## 4. Sensitivity labels and encrypted containers

- [documented] https://learn.microsoft.com/en-us/information-protection/develop/concept-mip-metadata (updated
  2026-09-21). Key format: "`MSIP_Label_GUID_Enabled = true`". The attributes are Enabled, SiteId, ActionId ("Removed
  in MIP SDK 1.8 and later"), Method, SetDate, Name and ContentBits ("ENCRYPT = 0x8"). The page adds: "An object can
  only have **one** label from the same organization." For mail: "all attributes are serialized into a single email
  header called **MSIP_Labels**", with key/value pairs "delimited by a semicolon and a whitespace".
- [documented] https://learn.microsoft.com/en-us/purview/sensitivity-labels-coauthoring (updated 2026-06-25): "After
  you enable the setting for co-authoring, labeling information for unencrypted files is no longer saved in custom
  properties." **So reading `docProps/custom.xml` alone misses labels in tenants that have co-authoring on.**
- [documented] MS-OFFCRYPTO 2.6.2
  (https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-offcrypto/0c4ec12d-aba0-4dd4-8fd1-710da6735219).
  LabelInfo goes "in the \0x06DataSpaces\TransformInfo storage when Information Rights Management (IRM) is applied",
  and otherwise "to the OPC package in the Sensitivity Label Information part". 2.6.3
  (…/13939de6-c833-44ab-b213-e0088bf02341), for an unknown policy: "it shall be inferred to be opted in to the
  LabelInfo stream (2) or not by the presence or absence of sensitivity label metadata in the LabelInfo stream (2) per
  Azure AD tenant as given by the siteId attribute". Under opt-in, labels "shall be first read from the LabelInfo
  location … and subsequently metadata shall only be read for custom document properties where there is no label
  element". 2.6.4 gives the schema: `clbl:labelList/clbl:label@{id, enabled, method, siteId, contentBits, removed}`,
  namespace `http://schemas.microsoft.com/office/2020/mipLabelMetadata`. The part's relationship type is
  `http://schemas.microsoft.com/office/2020/02/relationships/classificationlabels`, content type
  `application/vnd.ms-office.classificationlabels+xml`, and conventional path `docMetadata/LabelInfo.xml` (Open XML
  SDK `data/parts/LabelInfoPart.json`).
- [documented] MS-CFB 2.2: "Header Signature (8 bytes) … MUST be set to the value 0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1,
  0x1A, 0xE1." MS-OFFCRYPTO 2.3.4.4: "The \EncryptedPackage stream is an encrypted stream (1) of bytes containing the
  entire ECMA-376 source file". Legacy `.doc/.xls/.ppt/.msg` are CFB files too, so the magic number alone does not
  mean "encrypted".
- [documented] https://learn.microsoft.com/en-us/purview/sensitivity-labels-office-apps (updated 2026-06-30): "When
  the PDF is created, it inherits the label with any content markings. For Windows, if the label applied encryption,
  that encryption is also inherited." Microsoft does not document where Office writes the label inside a PDF. No
  primary source was found. [reasoning] It is probably `MSIP_Label_*` custom keys in the document information
  dictionary; confirm on a tenant-labelled PDF (§8).
- [documented] Graph `api/driveitem-extractsensitivitylabels.md`: "Extract one or more sensitivity labels assigned to
  a drive item and update the metadata of a drive item". It is per item, via `POST`, least privilege `Files.Read.All`,
  and returns `{labels: [{sensitivityLabelId, assignmentMethod, tenantId}]}`. It fails with `423 Locked` and code
  `fileDoubleKeyEncrypted`, `fileDecryptionNotSupported` or `fileDecryptionDeferred`. [measured] `grep -i sensitiv` of
  `resources/driveitem.md` (v1.0 and beta) finds only the methods `extractSensitivityLabels` and
  `assignSensitivityLabel`. **`driveItem` has no `sensitivityLabel` property**, so delta cannot `$select` one.
- [measured] Reference sniffer `/tmp/c15-enc/sniff.py` (stdlib only), run against fixtures from
  `/tmp/c15-enc/fixture.py`. Both were kept only for this session and are small enough to rebuild from this
  description:
  - `office_container`: `PK\x03\x04` means an OOXML zip. The CFB magic number plus the UTF-16LE bytes of
    `EncryptedPackage` in the file means `cfb-encrypted-package`. The CFB magic number alone means `cfb-other`.
  - `ooxml_labels`: find the LabelInfo part through `_rels/.rels`, falling back to `docMetadata/LabelInfo.xml`. Keep
    labels with `enabled` = 1 and `removed` ≠ 1. Then add `custom.xml` `MSIP_Label_<guid>_Enabled = true` labels whose
    `_SiteId` is absent from LabelInfo.
  - `pdf_state`: the `%PDF-` header, `/Encrypt`, and `MSIP_Label_<guid>_<attr>` byte matches.

| Fixture | Result |
|---|---|
| `plain.docx` (python-docx) | `ooxml-zip`, no labels |
| `labelled.docx` (LabelInfo + custom.xml, same siteId) | `[('LabelInfo', '2096f6a2-…', 'cb46c030-…', 'Privileged')]`; the custom.xml copy is suppressed per 2.6.3 |
| `encrypted.docx` (msoffcrypto-tool agile, password) | first 8 bytes `d0cf 11e0 a1b1 1ae1`; UTF-16LE `EncryptedPackage` found → `cfb-encrypted-package`. `olefile.listdir()` gives `\x06DataSpaces/…/StrongEncryptionDataSpace`, `EncryptedPackage`, `EncryptionInfo` |
| `labelled.pdf` (pypdf, Info key) | `msip_keys: ['MSIP_Label_…_Enabled']`, no `/Encrypt` |
| `encrypted.pdf` (pypdf AES-256) | `/Encrypt` present. The project's pdfminer.six 20260107 raises `pdfminer.pdfdocument.PDFPasswordIncorrect` |
| blank one-page PDF through pdfminer.six | returns `'\x0c'`, a form feed and not an empty string. An "empty" test must strip it |
| `OneDrive-Contoso/agentsync-probe/doc.docx` (read only) | `ooxml-zip`, no labels |

The IRM variant (a `DRMEncryptedDataSpace` transform) was not available; it needs a tenant with RMS. Detection keys on
`EncryptedPackage`, which password and IRM encryption share.

> CORRECTED (2026-09-29): design §5 says a label change is "eTag moves, hash holds → METADATA_ONLY … a frontmatter
> sweep". Labels live in package parts (`docMetadata/LabelInfo.xml`, `docProps/custom.xml`), so relabelling changes
> the file bytes and `quickXorHash`. It is a content change and must be classified before conversion.

## 5. Corporate TLS inspection and proxies

- [measured] httpx 0.28.1 in `.venv`, `_config.create_ssl_context`: with `verify=True` and no `SSL_CERT_FILE`, it runs
  `ssl.create_default_context(cafile=certifi.where())`, which uses certifi's bundle and not the macOS keychain. The
  python.org 3.11 `ssl.get_default_verify_paths().openssl_cafile` is
  `/Library/Frameworks/Python.framework/Versions/3.11/etc/openssl/cert.pem`, and that file **does not exist**.
  MSAL 1.39.0 builds its own `requests.Session()` (`verify=True`, which uses certifi) unless `http_client=` is passed.
  `launchctl getenv HTTPS_PROXY` and `SSL_CERT_FILE` return empty. [reasoning] Behind a TLS-inspecting proxy whose root
  CA MDM installs in the System keychain, both stacks therefore fail verification.
- [documented] truststore, https://truststore.readthedocs.io/ (repository `docs/source/index.md`; 0.10.4, MIT):
  "Truststore **requires Python 3.10 or later**" and works on "macOS 10.8+ via Security framework". Also:
  "`inject_into_ssl()` **must not be used by libraries or packages** … The `inject_into_ssl()` function is intended only
  for use in applications and scripts", and "should be called as early as possible in your program as modules that
  have already imported `ssl.SSLContext` won't be affected." httpx `docs/advanced/ssl.md` gives the pattern:
  `ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT); client = httpx.Client(verify=ctx)`.
  [reasoning] agentsync is an application, so both apply. Pass an explicit context to httpx, where it is testable, and
  call `inject_into_ssl()` first thing in `main()` so that MSAL's `requests` session also trusts the keychain.
- [measured] Proxies. httpx `get_environment_proxies()` calls `urllib.request.getproxies()`. On macOS that is
  `getproxies_environment() or getproxies_macosx_sysconf()` (CPython 3.11 `Lib/urllib/request.py`), and `_scproxy.c`
  reads only `kSCPropNetProxies{HTTP,HTTPS,FTP,Gopher}{Enable,Proxy,Port}`, `ExceptionsList` and
  `ExcludeSimpleHostnames`. **Nothing reads the PAC keys.** On this Mac, `getproxies()` returns `{}` and
  `_scproxy._get_proxy_settings()` returns `{'exclude_simple': False, 'exceptions': ('*.local', '169.254/16')}`.
  httpx maps only env `NO_PROXY` to exclusions; the macOS `ExceptionsList` is ignored [measured, source read].
  [reasoning] Consequences: a LaunchAgent with no proxy env still picks up a *manual* system proxy. A PAC or WPAD
  network goes direct and fails. The `nc -z graph.microsoft.com 443` gate reports an explicit-proxy network as
  offline. The broker (`pymsalruntime` via the SSO extension) uses Apple networking and follows system settings (the
  apple-sso-plugin warning about TLS inspection applies to it).

## 6. PDF extraction under a permissive licence

[documented, from `docs/design/receipts/E-converters.md`]:
- §3 grades MarkItDown's PDF route, "pdfminer+pdfplumber", **C, "2× tokens"**. §4.4 measured it on a 12-page statement
  at "42,796" characters with "**one table per transaction row** … the real column header is emitted as loose prose",
  and concluded "Neither is usable for table-accurate retrieval on this class of document".
- §7 says: "Get this cleared before it becomes the default PDF path, or fall back to Docling (MIT) + pypdfium2." §10.3
  says: "If denied, the tier-1 default becomes Docling-with-pypdfium (2.45 p/s per its own report)". §3's cited anchor
  is "pypdfium backend 2.45 p/s at lower quality" (Docling technical report, arXiv 2408.09869).
- No receipt measures pdfminer.six alone or bare pypdfium2 text extraction; `git grep pdfminer` finds only
  E-converters and CONTRACTS.

[measured] PyPI licences: pypdfium2 5.13.0 is "BSD-3-Clause, Apache-2.0, dependency licenses"; pdfminer.six 20260107 is
MIT; docling 2.131.0 is MIT; pymupdf4llm 1.28.2 is "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial".

**Answer [reasoning from the above].** The design's own evidence supports **pypdfium2 as the backend of Docling
(MIT)**, which is the named permissive fallback. It does not support pdfminer.six: the pdfminer family is the one
route it measured, and it graded that route C and found it table-destroying. Bare pypdfium2 text extraction has no
layout or table model either, so it is in the same class as pdfminer.six's plain text, and neither has a receipt.
Adopting either as a text-only tier-1 is a new decision. It needs the E-converters §4.4 fixture and the ×3 sha256
determinism run first, and table-bearing PDFs must be flagged or routed to the Docling tier instead of silently
flattened.

> CORRECTED (2026-09-29): `docs/plans/implementation.md` says "PDF text via `pdfminer.six` (MIT)" and treats that as
> equivalent to the design's route. The design's receipts measured the pdfminer family as grade C on tables and name
> Docling + pypdfium2 as the permissive fallback.

## 7. Retention, legal hold and a local git mirror

- [documented] https://learn.microsoft.com/en-us/purview/retention (updated 2026-07-23): "These retention settings
  work with content in place that saves you the additional overheads of creating and configuring additional storage
  … In addition, you don't need to implement customized processes to copy and synchronize this data." It also says:
  "Retention labels, unlike sensitivity labels, don't persist if the content is moved outside Microsoft 365."
- [documented] https://learn.microsoft.com/en-us/purview/retention-policies-sharepoint (updated 2026-06-25): when an
  item under retention is changed or deleted, "the original content is copied to the Preservation Hold library". It
  adds that deletion "is always suspended if the same item must be retained because of another retention policy or
  retention label, or it's under eDiscovery holds for legal or investigative reasons."
- [documented] https://learn.microsoft.com/en-us/purview/ediscovery-create-holds (updated 2026-06-11): after a
  location leaves a hold, "the actual removal of the hold is delayed for 30 days to prevent data from being
  permanently deleted (purged)".
- [documented] GitHub, "Removing sensitive data from a repository"
  (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository):
  after a rewrite the data survives "In any clones or forks of your repository". The page also says: "You cannot
  remove sensitive data from other users' clones of your repository", and "If a fellow developer has a clone from
  before your rewrite, and after your rewrite simply runs `git pull` followed by `git push`, the sensitive data will
  return." The tool is `git-filter-repo --sensitive-data-removal --invert-paths --path <file>` (git-filter-repo
  2.47.0, MIT [measured PyPI]). These quotes were checked against the page source,
  `github/docs/content/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository.md`.
- [measured] Local `man git-reflog` (git 2.54.0) says: "`--expire=<time>` … defaults to 90 days". `man git-gc` says:
  "`--prune=<date>` Prune loose objects older than date (default is 2 weeks ago …)". [reasoning] A deleted blob can
  therefore stay recoverable locally for up to 90 days unless both are forced. No scratch repository was built to
  demonstrate it, because this run makes no git writes.
- [reasoning] The implementation must meet the following requirements.
  - The mirror is a copy **outside** retention, hold, eDiscovery and DLP. Purview can neither delete it nor preserve
    it.
  - **No remote by default.** Every clone is a copy that no purge can reach.
  - A **`purge` verb** covers every copy. It rewrites history in the writer and every published worktree, expires
    reflogs, prunes objects, and deletes the converter-cache entries, SQLite rows and log lines that name the item. It
    triggers on:
    - upstream deletion, once the deletion is confirmed and past the breaker;
    - a label escalation above the threshold;
    - a DLP remediation;
    - an erasure request.
  - Tombstones must not advertise `git show <sha>:<path>` recovery for purged items.
  - **History compaction** bounds how long disposed content survives in the mirror, even without an explicit purge:
    squash mirror history older than N days into one snapshot, then expire and prune.
  - **Legal hold** is satisfied in place, in the Preservation Hold library. Legal may still name the local copy as
    custodian data. The implementation therefore needs a `hold <scope>` switch that suspends purge and compaction for
    that scope, recorded in STATE, set and cleared only by the records or legal owner.
  - Backups: mirror content can be derived again from the tenant, so it needs no backup. Exclude `mirror/`, the cache
    and the manifest from Time Machine, and back up only curated, human-written pages.

## 8. Not measured here (corporate-tenant probes)

1. Broker sign-in on an Intune-compliant arm64 Mac with Company Portal: record `token_source`, a silent refresh from
   a LaunchAgent, and the result under a compliant-device CA policy.
2. Loopback sign-in in Safari and in Chrome on the same Mac: record whether a later silent refresh from Python passes
   the compliant-device policy.
3. The state of "Block device code flow" (Microsoft-managed) in the target tenant, and the device-code result
   (`53003` expected).
4. Token Protection scoped to SharePoint Online: record whether Graph `/drives` calls from a broker-less client fail.
5. `GET /shares/u!…/driveItem` with `Files.Read.All` only, against the documented least privilege `Files.ReadWrite`.
6. Shared-mailbox message delta with `Mail.Read.Shared`.
7. The launchd matrix: a Developer-ID launcher spawning Python, with and without a PPPC `SystemPolicyAllFiles`
   profile, against a CloudStorage file. Record latency, errno and the TCC log; the attribution is inferred in §3.3.
8. A labelled and IRM-encrypted `.docx` and PDF from the tenant through `sniff.py`: the `DRMEncryptedDataSpace` path,
   and where the PDF label lives.
9. `extractSensitivityLabels` on a labelled, a DKE and an unlabelled item: the 423 codes and latency.
10. Behind the tenant's TLS-inspecting proxy: httpx with and without truststore, and MSAL with and without
    `inject_into_ssl()`.

## 9. Implementation requirements

1. Build `PublicClientApplication` with `enable_broker_on_mac=True` and depend on `msal[broker]>=1.39,<2`; a unit test asserts both.
2. Set the authority to `https://login.microsoftonline.com/<tenant-id>` and reject `organizations`/`common` at config load (they return AADSTS50194 on a single-tenant app).
3. Try interactive sign-in in this order: broker, then loopback auth code + PKCE, then device code only when `[graph] allow_device_code = true`; a test covers each rung.
4. Record every token's `token_source` in STATE.md, so a silent fallback away from the broker is visible.
5. Map the AADSTS codes in §1.5 to `reauth-required`, `blocked: device|policy|consent|assignment` or `config-invalid` and never retry them; the test is table-driven.
6. Background (launchd) runs call only `acquire_token_silent`; an interaction-required result exits 77 and opens no browser.
7. Ship the Entra manifest as a JSON file; a test checks that it parses, has exactly the two redirect URIs in §1.6, `AzureADMyOrg`, and `isFallbackPublicClient: false`.
8. Compute requested scopes from the enabled source kinds using §2; a Mail or Teams scope appears only when such a source is live.
9. Every mail request carries `Prefer: IdType="ImmutableId"`, including delta and nextLink/deltaLink follow-ups; the request builder is tested.
10. Mail delta `410` follows `Location` into a full resync of that folder; a 40X with `syncStateNotFound` resyncs that folder and is never logged as cursor corruption.
11. Discover mail folders with `/me/mailFolders/delta` and propose new folders; do not skip them.
12. The channel arm pages `messages?$top=50&$expand=replies`, follows `replies@odata.nextLink`, and stops at the first chain whose newest `lastModifiedDateTime` is older than HWM − 5 min.
13. The chat arm uses `$orderby=lastModifiedDateTime desc&$filter=lastModifiedDateTime gt <HWM>` on `/me/chats/{id}/messages`.
14. Throttle Teams requests to at most 1 per second per chat or channel; test with a fake clock.
15. A lint fails the build on any use of `sharedWithMe`, `insights/shared`, `getAllMessages` or a channel `/delta` URL.
16. Discovery resolves operator-supplied URLs through `/shares/u!…/driveItem` or `/sites/{host}:/{path}` and reports 401/403 as a named IT action.
17. The first CloudStorage access of each run is a canary in a child process with a 10 s timeout; a timeout gives `tcc-pending` and EPERM/EACCES gives `tcc-denied`, and neither is ever read as empty.
18. Every CloudStorage read has a per-call timeout, and a timed-out or denied read never counts as a deletion candidate.
19. The LaunchAgent plist's `ProgramArguments[0]` is the signed launcher, never `/usr/bin/python3`, a Homebrew path or a `.venv` symlink; `install-agent` tests assert this.
20. `agentsync doctor` prints the launcher's `codesign -d -r-` and warns when the designated requirement is `cdhash`/adhoc.
21. `install-agent` starts the first launchd run while the user is logged in and prints the exact "wants to access files managed by" prompt to approve.
22. The IT pack contains a PPPC `SystemPolicyAllFiles` payload pinned to the bundle id and TeamID; a lint checks that `CodeRequirement` contains `certificate leaf[subject.OU]`.
23. Before converting `.docx/.xlsx/.pptx`, read labels per MS-OFFCRYPTO 2.6.3: the LabelInfo part (found by relationship type) first, then `custom.xml` for siteIds it lacks. Fixtures cover both, either and none.
24. A file that starts with `D0CF11E0A1B11AE1` and contains an `EncryptedPackage` stream is quarantined `encrypted-office` without running a converter.
25. A PDF whose trailer has `/Encrypt`, or whose extraction raises `PDFPasswordIncorrect` or `PDFEncryptionError`, is quarantined `encrypted-pdf`.
26. Converter output that is empty after stripping whitespace and `\x0c` becomes an UNREADABLE stub, never an empty page.
27. The exclusion threshold is a configured list of label GUIDs. A file whose label is on the list, or is unknown while `labels.fail_closed = true`, is never converted.
28. Arm A calls `extractSensitivityLabels` only for new items or items whose `quickXorHash` changed, within a per-run budget, and maps each 423 code to its own quarantine reason.
29. A change to LabelInfo or custom.xml is classified as a content change, never `METADATA_ONLY`.
30. Mail items parse the `MSIP_Labels` header into `sensitivity_label`.
31. The Graph client is `httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))`; a test asserts the context type.
32. `truststore.inject_into_ssl()` runs in the CLI entrypoint before `msal` and `requests` are imported; a test asserts the import order.
33. Proxy precedence is config `[network] proxy`, then `HTTPS_PROXY`, then the macOS manual system proxy. If `scutil --proxy` shows `ProxyAutoConfigEnable : 1` and no explicit proxy is set, the run fails `network-policy: PAC` and does not go direct.
34. The reachability gate is an HTTPS request to the Graph host through the resolved proxy; a certificate failure is `failed: network-policy (TLS)`, never `skipped`.
35. No PDF converter is marked default until a receipt records its E-converters §4.4 result and a ×3 sha256 determinism run.
36. `install` creates the `docs/` repo with no remote and refuses to add one unless config names a tenant-owned remote.
37. `agentsync purge <selector>` rewrites history, expires reflogs, prunes objects, and cleans published worktrees, the converter cache and SQLite; a test shows `git cat-file -e <blob>` fails afterwards.
38. Confirmed upstream deletions and label escalations above the threshold enqueue a purge, and purged items' tombstones carry no `git show` recovery hint.
39. Scheduled compaction squashes mirror history older than `retention.history_days`, then runs `reflog expire --expire=now --all` and `gc --prune=now`; tested on a fixture repo.
40. `agentsync hold <scope>` suspends purge and compaction for that scope and is shown in STATE.md until it is released.
41. Every purge writes an audit line with source id, path hash, time and reason, and no content.
42. `install` excludes `mirror/`, the converter cache and the manifest from Time Machine with `tmutil addexclusion`; uninstall purges all copies and removes the Keychain items and LaunchAgents.
