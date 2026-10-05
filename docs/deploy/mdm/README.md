# MDM pack: PPPC profile and managed OneDrive settings

**You need this only if the user cannot answer the one-time macOS prompt** "“agentsync-launcher” wants to access
files managed by “OneDrive”". For example, the Mac runs unattended, the prompt was denied and policy forbids a
reset, or your baseline blocks user TCC approvals. One Allow click grants less than this profile does. The click
covers the File Provider domain only. The profile grants **Full Disk Access** (`SystemPolicyAllFiles`), the only
PPPC key that works here, because PPPC has no File Provider key and TCC checks Full Disk Access first (C15 §3.2).
Treat it as a grant to the code in the user's `uv` tool environment: the launcher refuses every child except the
one interpreter pinned in its sealed Info.plist, but that interpreter's site-packages are user-writable.

## 1. Re-sign the launcher with your Developer ID

An ad-hoc launcher's designated requirement is a `cdhash`, which changes on every build, so PPPC cannot pin it.
Build on a Mac that has your **Developer ID Application** identity. The launcher pins one interpreter by its
absolute path, so build once per user. Ask the user for the output of `echo "$(uv tool dir)/agentsync/bin/python"`
and use it as `ALLOWED_PROGRAM`:

```sh
git clone https://github.com/renchris/agent-context-sync.git && cd agent-context-sync
SIGN_IDENTITY="Developer ID Application: <Org> (<TEAMID>)" \
ALLOWED_PROGRAM="/Users/<user>/.local/share/uv/tools/agentsync/bin/python" launcher/build.sh
codesign -d -r- launcher/build/AgentSyncLauncher.app          # prints "designated => identifier ..."
ditto -c -k --keepParent launcher/build/AgentSyncLauncher.app AgentSyncLauncher.zip
xcrun notarytool submit AgentSyncLauncher.zip --keychain-profile <profile> --wait
xcrun stapler staple launcher/build/AgentSyncLauncher.app
```

The designated requirement is identical for every user's build (bundle id + Team ID), so **one profile covers all
of them**. Hand the `.app` to the user. If they turn on background sync (optional, their choice), they run
`scripts/install.sh --confirm-install-agent --launcher /path/AgentSyncLauncher.app`. Run `agentsync doctor`: `launcher.requirement` must show `certificate leaf[subject.OU]`, not `cdhash`.

## 2. Fill the template

[`agentsync-pppc.mobileconfig`](agentsync-pppc.mobileconfig) carries two payloads:

- **`com.apple.TCC.configuration-profile-policy`:** `SystemPolicyAllFiles`, `Authorization = Allow`, bundle id
  `com.agentsync.launcher`.
- **`com.apple.servicemanagement`:** keeps the `com.agentsync.*` LaunchAgents enabled (macOS 13+).

Replace the three placeholders. Use `plutil`, not PlistBuddy, which strips the quotes inside a requirement:

```sh
DR="$(codesign -d -r- launcher/build/AgentSyncLauncher.app 2>&1 | sed -n 's/^designated => //p')"
P=agentsync-pppc.mobileconfig
plutil -replace PayloadContent.0.Services.SystemPolicyAllFiles.0.CodeRequirement -string "$DR" "$P"
plutil -replace PayloadContent.1.Rules.1.TeamIdentifier -string "<TEAMID>" "$P"
plutil -replace PayloadOrganization -string "<Org>" "$P"
plutil -replace PayloadDisplayName -string "agentsync (<Org>)" "$P"
plutil -lint "$P" && ! grep -q '__' "$P" && grep -q 'certificate leaf\[subject.OU\]' "$P" && echo ready
```

Change the `PayloadUUID`s if you keep several variants.

## 3. Upload

The profile must reach the device channel of a user-approved (or ADE) MDM enrollment. Apple does not allow a
manual install of PPPC or managed-login-items payloads.

- **Intune:** Devices › macOS › Configuration › Create › New policy › Profile type **Templates › Custom**. Name it
  `agentsync PPPC`, set **Deployment channel = Device channel**, upload the `.mobileconfig`, and assign it to the
  pilot device group. (Alternatively, rebuild it in the Settings catalog under *Privacy › Privacy Preferences Policy
  Control* with the same identifier, type and code requirement.)
- **Jamf Pro:** Computers › Configuration Profiles › **Upload**, then choose the `.mobileconfig`, set **Level =
  Computer Level**, scope it to the pilot computers, and Save. Jamf shows the payloads as *Privacy Preferences
  Policy Control* and *Managed Login Items*.

Verify on the Mac: the profile is listed under System Settings › General › Device Management. Then run
`agentsync status`: the next background run shows no `TCC_PENDING` (exit 79) and a source read.

## 4. Managed OneDrive settings to check

Read what is enforced with `defaults read "/Library/Managed Preferences/com.microsoft.OneDrive"` (the standalone
app's domain; the App Store app uses `com.microsoft.OneDrive-mac`). Microsoft's key list:
<https://learn.microsoft.com/en-us/sharepoint/deploy-and-configure-on-macos>.

| Setting | Effect on agentsync | Recommendation |
|---|---|---|
| Files On-Demand | Always on for the File Provider OneDrive on macOS 12+; there is no Mac key to turn it off. Online-only files are dataless, and agentsync hydrates them within each source's `max_materialise_bytes` per cycle. | nothing to set; size the budget |
| `HydrationDisallowedApps` | If it names `agentsync-launcher` or `python3`, every hydration fails and those files stay `pending`. | do not list them |
| `KFMSilentOptIn` / `KFMBlockOptOut` (Folder Backup) | Desktop and Documents move under `~/Library/CloudStorage/OneDrive-<Org>`, so any source or inbox placed there becomes TCC-protected and possibly dataless. | keep the inbox at `~/agent-context/inbox` (the default), outside Documents |
| `BlockExternalSync` | Libraries shared from other tenants do not sync, so the local arm cannot see them. | use a Graph source for them |
| `MinDiskSpaceLimitInMB` / `DownloadBandwidthLimited` | Hydration stops below the disk floor, or runs slowly. | leave headroom for the per-cycle budget |
| `AllowTenantList` / `DisablePersonalSync` | These decide which accounts appear under `~/Library/CloudStorage`. | point sources only at the work tenant's folder |
