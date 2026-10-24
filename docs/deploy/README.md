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
| `~/Applications/AgentSyncLauncher.app` | the signed launcher that LaunchAgents run (bundle id `com.agentsync.launcher`), only with `--confirm-install-agent` | `agentsync offboard` |
| `~/agent-context/sources.toml` | the in-scope set: the only file that names a location | `offboard --purge-data` |
| `~/agent-context/docs/` | the docs git repo (mode 0700, **no remote**) | `offboard --purge-data` |
| `~/Library/Application Support/agentsync/` | manifest (SQLite, 0600), cursors, lock, heartbeat, governance audit | `agentsync offboard` |
| `~/Library/Caches/agentsync/`, `~/Library/Logs/agentsync/` | converter cache (rebuildable), job logs | `agentsync offboard` |
| `~/Library/LaunchAgents/com.agentsync.{poll,reconcile}.plist` | every 5 min (poll) and hourly (reconcile), only with `--confirm-install-agent` | `agentsync uninstall-agent` / `offboard` |
| login Keychain, service `agentsync` | the Graph token cache (only after `agentsync graph login`) | `agentsync graph logout` / `offboard` |
| `~/agent-context/setup/`, `setup-report.md`, `bring-back.md`, `it-request-draft.md` | the setup log and friction log, the coding agent's `fix-request.md` and the `local-work/` patches of step 1, `local-work/.sent` (each patch a `bring-back.md` embedded, by identity and the first report's time, so a later attempt's file lists it by its hash) and, once a report found one of them unreadable, `local-work/.not-sent` (each such patch's device, inode, time and size and that report's time, and, once a report sends it, when that report's attempt began, so the patch is sent once it can be read, in each report of that attempt, and leaves the list once a later attempt reports; it is not copied into `bring-back.md`), `proxy-hosts` (the proxy and git URL-rewrite host names, and since 2026-10-18 the host of each git remote's URL, in force when the agent ran `--log-start` and `--log`, one a line, in lower case and, where it differs, as written; github.com never: the reports read it only to keep those hosts redacted after the proxy is unset or the remote changed, and it is not copied into `bring-back.md`), the setup report, the one file to bring back, the IT request draft (all 0600, folders 0700: every `install.sh` run closes what the agent wrote in `setup/`) | you |

`init` and `install-agent` exclude `mirror/`, the docs repo's `.git`, the cache and the manifest from Time Machine (all re-derivable from their sources; curated `topics/` is backed up).

## Install: one command, no admin

Prerequisite: the Xcode Command Line Tools (`xcode-select -p` prints a path). They provide `git` and the compiler
that builds the launcher; the speech helper also needs them (or Xcode) to bring Swift 6.0 or later, which is Command
Line Tools 16 or later on macOS 14.5 or later. With an older release everything else works and the `speech:` line
says this Mac's Swift is too old. If they are missing, ask IT through Self Service, because installing them may need
admin. A coding agent can run every step below for you: paste the block in the top-level README's
[one-prompt setup](../../README.md#set-up-on-a-new-mac-one-prompt).

Get the code, pick the folders to sync (`install.sh --list-folders` prints the OneDrive and SharePoint folders this
Mac syncs, one full path per line; it exits 3 when there are none, or when a folder this Mac already syncs under
`~/Library/CloudStorage` is missing because its app is signed out, and 4 when macOS denied this terminal access,
and its `NEXT:` line says which), then pass one `--source-local` per folder:

```sh
git clone https://github.com/renchris/agent-context-sync.git ~/src/agent-context-sync
~/src/agent-context-sync/scripts/install.sh --list-folders
~/src/agent-context-sync/scripts/install.sh --source-local "$HOME/Library/CloudStorage/OneDrive-Contoso/Projects"
```

The script is safe to re-run and never prompts. It installs uv and agentsync, writes `sources.toml` with one live
`kind = "local"` source per folder (on a re-run it adds only folders not yet there, through `agentsync add-source`),
runs `agentsync status` and a first sync, and ends with one `NEXT:` line: the loop's next step, the same line
`~/.local/bin/agentsync status` prints. It exits 2 when it created `sources.toml` and has no folder to sync (its
`NEXT:` names `--list-folders`) and 1 on a `[FAIL]` line. It builds no launcher and installs no LaunchAgent. Every
work session then starts with `~/.local/bin/agentsync sync` and does what its `NEXT:` line says.

On a Mac that is already set up nothing has to be chosen again. `--list-folders` starts with
`already synced on this Mac: N folders` (`1 folder` for one) and writes `[synced]` before each folder `sources.toml` already syncs,
and `install.sh` with no `--source-local` keeps every source, updates agentsync and runs status and a sync.
A sync reads everything under a synced folder, so a listed folder inside one has `[inside a synced folder]`
before it, and one that holds one has `[contains a synced folder]`. Add only an unmarked folder: a marked one
would be read twice. A synced folder the list does not reach (deeper than two levels, or outside
`~/Library/CloudStorage`) is printed first with the same mark. When no synced folder under `~/Library/CloudStorage`
is missing, such a Mac gets that line and exit 0 even when nothing else can be listed (for example, OneDrive signed
out and only a folder outside `~/Library/CloudStorage` synced). When a synced folder under `~/Library/CloudStorage`
is missing and its app is signed out (its provider folder, such as `OneDrive-Contoso`, is gone with no other folder
of that app beside it, or is there and empty), it gets the line, the `[synced]` lines and exit 3, logged
`note=not-signed-in apps=<app> missing=M synced=N`. Its `NEXT:` asks for that app's sign-in and a re-run, and says the `[synced]`
folders are kept. Since v20, step 1 of the one-prompt setup waits for that sign-in instead of going on to a step 2
that would fail. Since v21, an agent that cannot ask (an unattended tool, or a question that comes back
unanswered) does not wait there and logs nothing for it: it goes to step 3's report. A folder missing while its app is
signed in (renamed, moved or deleted there, not synced on this Mac, or under another
account's provider folder) keeps exit 0: the `NEXT:` names it, and step 2's status names its fix. So does a folder
in a SharePoint library that is not synced on this Mac (`OneDrive-SharedLibraries-Contoso` gone, or there and empty,
beside `OneDrive-Contoso`): its `NEXT:` says to sync the library with Sync on its SharePoint page, never to sign in.
It gets the line and the marks even when the list ends on a click (exit 4), which comes before the missing check.

### Optional: background sync (the operator's choice)

Background sync is never part of setup, and the one-prompt setup never turns it on: you decide, on your own Mac,
whether to run it. It keeps the mirror current between sessions, at the cost of a second Allow click and two jobs
that run as you:

```sh
~/src/agent-context-sync/scripts/install.sh --confirm-install-agent
```

This builds and ad-hoc signs the launcher (`--launcher PATH` copies a prebuilt, signed one instead), syncs once,
installs two LaunchAgents (a poll every 5 minutes and an hourly reconcile) and waits up to 3 minutes for the first
background run and your Allow click, so give the command a 10-minute timeout (30 minutes while the speech helper
is not built: its build alone can take up to 20 minutes). `agentsync uninstall-agent` removes the LaunchAgents
again without offboarding. A plain `install.sh` run never touches the launcher, so after pulling a
new version re-run `install.sh --confirm-install-agent`: it rebuilds the launcher only if its sources changed, and
an ad-hoc rebuild means one new Allow click. The launcher starts only the interpreter it was built for
(`bin/python` in the uv tool environment), and the jobs name it by that path, also when the updated tool itself
runs as `python3` in the same folder. A build before 2026-10-07 wrote its own path there, and such a job is
refused at every run (exit 64): `agentsync status` names it, and `agentsync install-agent` rewrites it.

Behind TLS inspection, the installer sets `UV_SYSTEM_CERTS=1` and agentsync trusts the macOS keychain.
`[network] proxy` is agentsync's own setting in `sources.toml` and covers only its Graph traffic: there, proxy
precedence is `[network] proxy`, then `HTTPS_PROXY`, then the macOS manual proxy. A PAC-only network fails closed,
so set `[network] proxy`. `agentsync status` checks the whole path whenever a Graph source is live. git, the uv
download and `uv tool install` do not read `[network] proxy`: behind a proxy, export `HTTPS_PROXY` (an `http://`
proxy URL; PAC files are not evaluated) in the terminal before the clone, the pull or `install.sh`. If git's error
names a host other than github.com, step 1 of the setup prompt lists where git may have been pointed elsewhere.

## Day 1, with zero IT involvement

- **Local arm over the corporate OneDrive sync folder.** Add a `kind = "local"` source whose `path` is inside
  `~/Library/CloudStorage/OneDrive-<Org>/`, for example a synced SharePoint library or a "shortcut to My files". It
  sees what the sync client syncs. Online-only files are downloaded within `max_materialise_bytes` per cycle, and
  the rest stay `pending` and are never read as deleted.
- **Manual inbox.** The inbox always exists: `install.sh` and `agentsync add-source` keep a `kind = "inbox"` source
  on the `inbox` folder beside the docs repo (`~/agent-context/inbox` by default) and create the folder. That folder
  is the drop folder; any other `kind = "inbox"` source in `sources.toml` is a folder you added. Drag in exports,
  attachments, PDFs or emails (drag a message out of Outlook to save it as `.eml`; save a meeting transcript as
  `.docx` or `.vtt`), and they are converted once they stop changing (`quiescence_s`). Teams messages go in as
  `.teams.json` files in the `agentsync.teams-month/1` shape (`TEAMS_MONTH_SCHEMA` in `src/agentsync/model.py`),
  one file per channel or chat and month, written by your own export script under the
  [inbox writer contract](../design/CONTRACTS.md#11-local-arm-and-hydration). Only `.eml`, a `.msg` saved as MIME text, `.pdf`
  and the Office formats carry a sensitivity label, so a `.vtt`, a `.teams.json` export or pasted text skips the `[policy]` label exclusions; prefer `.eml` and
  `.docx`. An image carries no label agentsync can read either, so while a label rule is set no image file is
  converted. An Outlook `.msg` is converted like an `.eml`, but the label of a binary Outlook-format one is not read,
  so while a label rule is set it is not converted (one saved as MIME text is screened and read like an `.eml`);
  drag the message out as `.eml` instead. Files stay in the inbox: it is a mirror, not a queue, so never empty it by hand (removing a file turns its
  page into a tombstone and queues a purge). Without IT this is the only route for mail and Teams messages: a tenant
  on Microsoft's default consent policy shows "Need admin approval" for any app that asks to read mail (measured on
  the corporate tenant, 2026-10-01).
- **Check it:** `~/.local/bin/agentsync sync`, do what its `NEXT:` line says, then open
  `~/agent-context/docs/INDEX.md`.

## What needs IT

| You want | IT action | Doc |
|---|---|---|
| libraries you do not sync, Outlook folders, Teams channels and chats | Entra app registration + admin consent + assignment | [it-request.md](it-request.md) |
| a compliant-device Conditional Access policy to pass | Company Portal + Enterprise SSO plug-in on an enrolled Mac | [it-request.md](it-request.md#conditional-access) |
| background reads with nobody to click Allow | a Developer-ID re-sign of the launcher + the PPPC profile | [mdm/README.md](mdm/README.md) |
| the Command Line Tools, a release with Swift 6.0 or later (16 or later) | Self Service or a ticket | — |

`agentsync it-request --out ~/agent-context/it-request-draft.md` writes [it-request.md](it-request.md) as an email
draft for this Mac: your name, the serial number, the CPU, the organization and the sources you already sync are
filled in, the links point at this repository on GitHub, and the first line lists what you still fill ("You fill:
..."). It never sends anything; you send it.

Once IT has registered the app, add these tables to `~/agent-context/sources.toml` with your own values; the
template no longer lists them. A config written before 2026-10-04 already has a `[graph]` table: put `client_id`
and `tenant` in that table (TOML refuses a second one) and append only the `[[source]]` tables. A newer config has
no `[graph]` table, so append the whole block. A Graph source needs `client_id`, and `tenant` must be your tenant
id (GUID) or verified domain: `organizations` and `common` are refused (AADSTS50194). Start each Graph source
paused, because its first pass is a full enumeration. `agentsync graph discover` prints the tables for what you can
already reach.

```toml
[graph]
client_id = "00000000-0000-0000-0000-000000000000"  # the Entra app registration (single-tenant public client)
tenant = "contoso.onmicrosoft.com"
# scopes = ["Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read"]
# cloud = "global"            # global | usgov | usgov-dod | china (default: from base_url)
# broker = true               # sign in via the macOS broker (Company Portal) first
# allow_device_code = false   # last-resort device-code sign-in, only if IT allows it

# A SharePoint document library or OneDrive via Graph delta
[[source]]
id = "finance-library"
kind = "graph_drive"
site = "contoso.sharepoint.com:/sites/finance"  # or: drive_id = "b!..." ; or: drive_id = "me"
folder = "/Shared Documents/FY26"               # subtree filter, "/" = whole drive
state = "paused"

# An Outlook mail folder via Graph message delta
[[source]]
id = "mail-projects"
kind = "graph_mail"
mailbox = "me"     # or a shared mailbox UPN (adds Mail.Read.Shared)
folder = "Inbox"   # well-known name or folder id
state = "paused"

# A Teams channel's messages via channel delta (ChannelMessage.Read.All needs admin consent)
[[source]]
id = "team-acme-general"
kind = "graph_teams"
team_id = "..."
channel_id = "19:...@thread.tacv2"
state = "paused"
```

## The one-time "Allow" click

On the first background run while you are logged in (`install.sh --confirm-install-agent` starts one), macOS asks:

> “agentsync-launcher” wants to access files managed by “OneDrive”.

Click **Allow**. It is asked once, for the launcher, and covers the OneDrive folder only. Approving from Terminal
does not count: a Terminal run grants Terminal, not the background job. Until someone answers, the job waits at
most 10 s and exits **79** (`TCC_PENDING`); it never reads the folder as empty. `agentsync status` shows the
launcher's `TCC_*` tokens.

- **Denied or missed:** turn on agentsync-launcher in System Settings > Privacy & Security > Files and Folders
  (a click, not a command), then re-run the install command (`install.sh ... --confirm-install-agent`); its
  `NEXT:` line names the exact command.
- **MDM suppresses or blocks the prompt:** ask IT for the PPPC profile ([mdm/README.md](mdm/README.md)). Until then,
  `~/.local/bin/agentsync sync` from Terminal runs under Terminal's own grant, and inbox sources need no grant at all.
- **Rebuilding the launcher** ad hoc creates a new identity, so macOS asks again. `install.sh` skips identical
  rebuilds (a developer forces one with `AGENTSYNC_REBUILD_LAUNCHER=1`).

## Exit codes the jobs report

`0` ok · `1` failed: a source or lint failed, or another error that the job's error log names in one line; one
such error is a state file this account cannot open, for example a `manifest.sqlite` left root-owned by a `sudo`
run: give it back to this account (owner, mode 600); the file is not damaged, so do not move it aside · `64` the
launcher refused the job: its program is not the one it starts, or
an option is wrong (a `PROGRAM_REFUSED` or `USAGE` line in the job's error log; `~/.local/bin/agentsync status` gives
the fix) · `65` the state file (`manifest.sqlite`) is no database or a damaged one:
you, not an agent, fix it (the message names the file and the fix; `~/.local/bin/agentsync sync` prints it again)
· `71` the launcher could not start agentsync: the Python it runs is missing or not executable (a
`SPAWN_FAILED` line in the job's error log names the program and the error); one 71 during a reinstall is expected,
a 71 at every run means run `~/.local/bin/agentsync install-agent`, which points the job at the Python agentsync
now runs in, or `install.sh` again with `--confirm-install-agent`, not `--report-only`, which builds nothing ·
`75` another cycle holds the lock · `77` sign-in needed or blocked (the
message names the IT action) · `78` bad `sources.toml` or `policy.toml` · `79` waiting for the Allow click · `80` (the launcher's
canary check) access was denied, `TCC_DENIED`: you, not an agent, turn on agentsync-launcher in System Settings >
Privacy & Security > Files and Folders, then re-run the install command. Logs are in `~/Library/Logs/agentsync/`.

## Leaving

`agentsync offboard` lists every copy. `agentsync offboard --confirm ~/agent-context/docs [--purge-data]` removes
them, including the Keychain item, the LaunchAgents, the launcher and its TCC grant. See
[data-governance.md](data-governance.md#offboarding).

## Setup feedback

Every real `install.sh` run appends one line per step to `~/agent-context/setup/install.log` (0600; set
`AGENTSYNC_SETUP_LOG` to move it). `agentsync setup-report --out
~/agent-context/setup-report.md` turns that log, doctor, status, the background runs and recent log errors into one
redacted report with a summary first. It is read-only, makes no network calls and usually takes under 12 s (about 35 s at most,
when doctor is slow). The coding agent
logs what the prompt did not foresee as it goes, with `scripts/install.sh --log` (`--log-start` opens each attempt
and `--report-only` closes it), into `~/agent-context/setup/friction.md` (0600); the report embeds it with the same
redaction, and works out the outcome and run type itself. It prints a link that opens a pre-filled setup-report
issue; nothing is sent. `install.sh` also writes the report at every exit, and `scripts/install.sh --report-only`
(step 3 of the prompt) writes it whether or not agentsync got installed. [setup-feedback.md](setup-feedback.md) covers how to review it,
send it (publicly, or privately to the machine you administer this setup from) and turn each friction line into a
fix.
