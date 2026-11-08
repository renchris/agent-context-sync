# Tenant probes

The design's §9 probes 1, 2, 4b and 9 measure properties of **the target tenant**: library hash coverage, the
consent outcome, whether downloads are byte-stable, and how long a deltaLink lives idle. They cannot be measured
anywhere else. [`scripts/tenant-probes.sh`](../../scripts/tenant-probes.sh) runs all four, read-only and without a
single prompt, and writes a dated report. Run it again whenever you like; probe 9 needs runs at +30, +60 and +90
days.

## Run

```sh
mkdir -p "$HOME/Library/Application Support/agentsync/probes"
agentsync graph login 2>&1 | tee "$HOME/Library/Application Support/agentsync/probes/login-$(date +%Y%m%d).txt"
scripts/tenant-probes.sh --dry-run      # what it would request; no sign-in, no network, no writes
scripts/tenant-probes.sh                # prints: report: …/probes/tenant-probes-<UTC>.md
```

`agentsync graph login` is the only interactive step, and it is yours. The script then uses the cached token
silently, through the same interpreter, so the Keychain never asks. It never opens a browser and never writes to
the tenant. The report and the probe-9 store are mode 0600 under `<state_dir>/probes/` (override with `--output`).
Both are written whole or not at all, and one run at a time holds the store. The default folder is set to 0700 if
the `mkdir -p` above left it looser; a folder given with `--output` is yours, so it gets a warning instead.

| Exit | Meaning |
|---|---|
| 0 | report written |
| 77 | not signed in, or sign-in blocked; the report names the state (for example `blocked: consent`, AADSTS65001) |
| 78 | `sources.toml` invalid, or no `[graph] client_id` yet (IT request pending); a real run still writes its report, `--dry-run` writes nothing |
| 1 | failed, e.g. `network-policy` (TLS inspection / PAC), see [README](README.md#install-one-command-no-admin); a damaged `deltalinks.json` (left as it was), another run holding probe 9, a report that cannot be written, or an interpreter without agentsync |
| 2 | usage error |
| 130, 129, 143 | stopped by INT (Ctrl-C), HUP or TERM; temporary downloads are removed and, once probing began, a partial report is written |

Limits: `--max-pages 10` (about 2,000 delta items per drive), `--max-downloads 6`, `--max-bytes 20000000`.
`--selftest` checks the offline helpers: 4 QuickXorHash vectors from rclone, a row-fold identity, and claim
decoding. `--version` prints the commit of this checkout, or `unknown`, without running git. Every option that takes
a value also takes the `=` form (`--max-pages=20`), which is how to pass a value that is itself an option; a repeat
takes the last value, an empty value is a usage error, and `--` ends the options.

## What each probe records

| # | Requests | Records | Decides |
|---|---|---|---|
| 2 | silent token; one GET per permission (`/me`, `/me/drive`, `/sites/root`, `/me/followedSites`, `/me/mailFolders`, `/me/chats`, `/me/joinedTeams`, the first channel and one message's status) | sign-in method and MSAL `token_source` (C15 req 4), granted `scp` vs requested scopes, whether a `deviceid` claim is present (device-bound sign-in), `amr`, HTTP status per permission, and the last lines of your `login-*.txt` | whether each arm can run; what to ask IT for |
| 1 | `GET /drives/{id}/root/delta` with the drive arm's `$select`, on `/me/drive`, the root site's library and each `graph_drive` source | counts only: files with `quickXorHash` / `sha256Hash` / no hash, `cTag` on files, zero-size files, OneNote packages, no-hash files by extension | whether phase 1 is zero-byte on this tenant's files |
| 4b | up to 6 Office/PDF files from probe 1, each downloaded twice into a temporary directory that is then deleted | per file: type, size, labels found in the bytes, sha256 equal across the two downloads, and served bytes vs `quickXorHash` | whether `content_hash` is a stable identity for labelled files |
| 9 | three `GET /me/drive/root/delta?token=latest` links, kept in `probes/deltalinks.json` (0600, never in the report); each is used exactly once, at 30, 60 or 90 days | age and outcome per milestone (`200` valid, `410` expired) | the real idle lifetime and the reconcile floor; a 410 only costs one full enumeration |

The report carries counts, extensions, statuses and hashes-equal booleans. It contains **no file names and no
content**. Downloads in probe 4b appear in the tenant's audit log as the user's own file downloads.

## Not covered here

These need an interactive or write action, or a managed device, and are run by hand:

- device-code under Conditional Access (use `agentsync login --device-code`, only if IT sets `allow_device_code`);
- `cTag` lag right after an upload;
- Token Protection, Teams channel delta, shared-mailbox delta, and the launchd × signing × PPPC matrix
  ([C15 §8](../design/receipts/verify/C15-corporate-controls.md) probes 4, 6 and 7).
