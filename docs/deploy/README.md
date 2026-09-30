# Deploying agentsync on a corporate Mac

agentsync keeps `~/agent-context/docs` in sync with OneDrive/SharePoint, the Outlook folders and Teams channels
you pick, and local sync-client folders. That folder is a local git repository of markdown that a coding agent
reads. It runs as you, needs **no admin rights**, and keeps everything on the Mac.

| Pack file | Audience |
|---|---|
| this README | the operator: install, day 1, the one-time Allow click |
| [it-request.md](it-request.md) + [entra-app.json](entra-app.json) | the Entra admin: app registration, consent, Conditional Access |
| [mdm/README.md](mdm/README.md) + [mdm/agentsync-pppc.mobileconfig](mdm/agentsync-pppc.mobileconfig) | the MDM admin: PPPC profile, managed OneDrive settings |
| [data-governance.md](data-governance.md) | records, legal and security: copies, retention, hold, purge, offboarding |
| [tenant-probes.md](tenant-probes.md) + [`scripts/tenant-probes.sh`](../../scripts/tenant-probes.sh) | the operator: read-only tenant measurements after sign-in |

## What gets installed where

| Path | What | Removed by |
|---|---|---|
| `~/.local/bin/uv` | uv (only if none is on `PATH`; the official installer, shell profiles untouched) | you |
| `~/.local/share/uv/tools/agentsync/`, `~/.local/bin/agentsync` | agentsync and a uv-managed Python 3.11 (`uv tool install`) | `agentsync offboard` |
| `~/Applications/AgentSyncLauncher.app` | the signed launcher that LaunchAgents run (bundle id `com.agentsync.launcher`) | `agentsync offboard` |
| `~/agent-context/sources.toml` | the in-scope set: the only file that names a location | `offboard --purge-data` |
| `~/agent-context/docs/` | the docs git repo (mode 0700, **no remote**) | `offboard --purge-data` |
| `~/Library/Application Support/agentsync/` | manifest (SQLite, 0600), cursors, lock, heartbeat, governance audit | `agentsync offboard` |
| `~/Library/Caches/agentsync/`, `~/Library/Logs/agentsync/` | converter cache (rebuildable), job logs | `agentsync offboard` |
| `~/Library/LaunchAgents/com.agentsync.{poll,reconcile}.plist` | every 5 min (poll) and hourly (reconcile), only with `--confirm-install-agent` | `agentsync uninstall-agent` / `offboard` |
| login Keychain, service `agentsync` | the Graph token cache (only after `agentsync graph login`) | `agentsync graph logout` / `offboard` |

`init` and `install-agent` exclude `mirror/`, the docs repo's `.git`, the cache and the manifest from Time Machine (all re-derivable from their sources; curated `topics/` is backed up).

## Install: one command, no admin

Prerequisite: the Xcode Command Line Tools (`xcode-select -p` prints a path). They provide `git` and the compiler
that builds the launcher. If they are missing, ask IT through Self Service, because installing them may need admin.

```sh
git clone https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync && ~/src/agent-context-sync/scripts/install.sh
```

The script is safe to re-run and never prompts; `--dry-run` shows every step first. It installs uv and agentsync,
builds and ad-hoc signs the launcher, writes `sources.toml`, runs `agentsync doctor`, and ends with one `NEXT:`
line. Uncomment your sources in `~/agent-context/sources.toml`, then start background sync:

```sh
~/src/agent-context-sync/scripts/install.sh --confirm-install-agent
```

Behind TLS inspection, the installer sets `UV_SYSTEM_CERTS=1` and agentsync trusts the macOS keychain. Proxy
precedence is `[network] proxy`, then `HTTPS_PROXY`, then the macOS manual proxy. A PAC-only network fails closed,
so set `[network] proxy`. `agentsync doctor --network` checks the whole path.

## Day 1, with zero IT involvement

- **Local arm over the corporate OneDrive sync folder.** Add a `kind = "local"` source whose `path` is inside
  `~/Library/CloudStorage/OneDrive-<Org>/`, for example a synced SharePoint library or a "shortcut to My files". It
  sees what the sync client syncs. Online-only files are downloaded within `max_materialise_bytes` per cycle, and
  the rest stay `pending` and are never read as deleted.
- **Manual inbox.** A `kind = "inbox"` source at `~/agent-context/inbox`. Drag in exports, attachments or PDFs, and
  they are converted once they stop changing (`quiescence_s`).
- **Check it:** `agentsync sync --once`, then `agentsync status`, then open `~/agent-context/docs/INDEX.md`.

## What needs IT

| You want | IT action | Doc |
|---|---|---|
| libraries you do not sync, Outlook folders, Teams channels and chats | Entra app registration + admin consent + assignment | [it-request.md](it-request.md) |
| a compliant-device Conditional Access policy to pass | Company Portal + Enterprise SSO plug-in on an enrolled Mac | [it-request.md](it-request.md#conditional-access) |
| background reads with nobody to click Allow | a Developer-ID re-sign of the launcher + the PPPC profile | [mdm/README.md](mdm/README.md) |
| the Command Line Tools | Self Service or a ticket | — |

## The one-time "Allow" click

On the first background run while you are logged in (`install.sh --confirm-install-agent` starts one), macOS asks:

> “agentsync-launcher” wants to access files managed by “OneDrive”.

Click **Allow**. It is asked once, for the launcher, and covers the OneDrive folder only. Approving from Terminal
does not count: a Terminal run grants Terminal, not the background job. Until someone answers, the job waits at
most 10 s and exits **79** (`TCC_PENDING`); it never reads the folder as empty. `agentsync status` shows the
launcher's `TCC_*` tokens.

- **Denied or missed:** run `tccutil reset All com.agentsync.launcher`, then
  `launchctl kickstart -k gui/$(id -u)/com.agentsync.poll`, and answer the prompt.
- **MDM suppresses or blocks the prompt:** ask IT for the PPPC profile ([mdm/README.md](mdm/README.md)). Until then,
  `agentsync sync --once` from Terminal runs under Terminal's own grant, and inbox sources need no grant at all.
- **Rebuilding the launcher** ad hoc creates a new identity, so macOS asks again. `install.sh` skips identical
  rebuilds.

## Exit codes the jobs report

`0` ok · `1` a source or lint failed · `75` another cycle holds the lock · `77` sign-in needed or blocked (the
message names the IT action) · `78` bad `sources.toml` · `79` waiting for the Allow click. Logs are in
`~/Library/Logs/agentsync/`.

## Leaving

`agentsync offboard` lists every copy. `agentsync offboard --confirm ~/agent-context/docs [--purge-data]` removes
them, including the Keychain item, the LaunchAgents, the launcher and its TCC grant. See
[data-governance.md](data-governance.md#offboarding).
