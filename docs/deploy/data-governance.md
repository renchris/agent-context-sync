# Data governance: agentsync

For records management, legal, security and the operator. **The one fact that matters:** the docs repo is a
**copy outside Microsoft 365**. Purview retention, legal hold, eDiscovery and DLP cannot see it, delete it or
preserve it. agentsync therefore keeps the copy local (no remote), bounds how long history lasts, and gives the
records owner purge and hold verbs of its own. Background: [C15 §7](../design/receipts/verify/C15-corporate-controls.md).

## Data flow

```text
Microsoft 365 (Graph, delegated GETs) ─┐
OneDrive/SharePoint sync folder ───────┼─► staging (<state>/staging, deleted per item) ─► label screen ─► converter
~/agent-context/inbox (drag and drop) ─┘                                                                 │
                                   converter cache (~/Library/Caches/agentsync) ◄──────────────────────┤
                                   docs repo mirror/ + git history (~/agent-context/docs) ◄────────────┘ ─► the agent reads it
```

Nothing flows back to Microsoft 365, and no copy leaves the Mac unless `[governance] allow_remote = true` names a
tenant-owned remote (`remote_url_prefixes`). `agentsync add-source` creates the repo without a remote, and agentsync
refuses to push to a remote that is not listed.

## Where copies live

| Location | Holds | Bounded by | Purge reaches it |
|---|---|---|---|
| `~/agent-context/docs/mirror/` + `.git` | converted markdown, one page per item, plus git history | compaction on `sync`'s full pass when due (`[governance] history_days`, default 30) | yes: history rewrite, reflog expiry, `gc --prune=now`, verified with `git cat-file -e` |
| `~/Library/Caches/agentsync` | converter outputs by content hash | GC after each complete enumeration | yes |
| `~/Library/Application Support/agentsync/manifest.sqlite` | paths, ids, hashes, labels, cursors (0600); no content | tombstone reap, 180 days | yes (rows forgotten) |
| `…/agentsync/staging`, `…/agentsync/teams/` | raw downloads (per cycle); Teams month stores | emptied each cycle; Teams rolls up by month | staging: n/a; Teams: yes |
| `~/Library/Logs/agentsync`, `…/governance/audit.jsonl` | ids and paths; the purge audit (source id, **path hash**, time, reason, no content) | log rotation (8 MB × 2) | the audit is kept by design |
| login Keychain, service `agentsync` | the MSAL token cache | token lifetime / `graph logout` | `offboard` |

Time Machine: `mirror/`, `.git`, the cache and the manifest are excluded (`agentsync status` checks
`governance.time_machine`). Only hand-written `topics/` pages are backed up. Any other backup of `~/agent-context`
is a copy that no purge reaches, so exclude it too.

## Retention and deletion

- **Upstream deletion or disposition:** after a complete listing and the deletion breaker, the page becomes a
  tombstone and a purge is queued (`purge_on_upstream_delete = true`). `agentsync purge --queue` runs the queue.
  Schedule it (weekly is suggested), or run it on request. Tombstones of purged items carry no `git show` recovery
  hint.
- **Bounded history:** mirror history older than `history_days` is squashed into one snapshot and the dropped
  objects are pruned. This runs automatically on the full pass `agentsync sync` makes when one is due.
  `agentsync status` warns when compaction is due and fails when it is overdue.
- **Explicit purge:** `agentsync purge id=<stable_id> | path=<source glob> | docs=<docs glob> --reason
  {upstream-deleted,label-escalation,dlp-remediation,erasure-request,operator}` (add `--dry-run` first). It
  rewrites history in the repo and the published worktree, deletes cache entries and manifest rows, and writes one
  audit line. For every reason except upstream deletion, the item is also **suppressed**, so it is never fetched or
  published again while it still exists upstream. Every purge changes commit hashes, so consumers must re-clone.
- **Erasure or DLP request:** find the item with `agentsync status` or `rg` in `_manifest/`, then run
  `agentsync purge path=… --reason erasure-request` (or `dlp-remediation`).

## Legal and records hold

Microsoft 365 satisfies a hold in place (the Preservation Hold library). Use the local hold only when legal names
this Mac's copy as custodian data:

```sh
agentsync hold all --reason "Matter 2026-117" --owner "records@<org>"   # or: hold <source-id> …
agentsync hold --list
agentsync hold all --release --owner "records@<org>"
```

A hold suspends purge and compaction for its scope. It appears in `_sync/STATE.md` and `agentsync status` until the
owner releases it. A configuration-level hold (`[governance] hold = true`, `hold_reason`, `hold_owner`) is released only by
editing `sources.toml`.

## Sensitivity labels and encryption

- **Policy:** `[policy] exclude_label_ids`, `exclude_label_names` and `refuse_unlabelled` in `sources.toml`. Compliance
  can own a `policy.toml` next to it, which is merged by union, so it can only add refusals. A policy that does not
  parse stops the run (exit 78) and never falls back to "allow".
- **Where labels are read:** from the file itself, before conversion: `docMetadata/LabelInfo.xml`, then
  `docProps/custom.xml` (MS-OFFCRYPTO 2.6.3), the PDF info dictionary, and the mail `msip_labels` header. A file
  with an excluded label is never converted or published. Its earlier conversions are deleted and a
  `label-escalation` purge is queued. A policy change re-screens every label-capable item.
- **Encryption:** an encrypted Office file (CFB with `EncryptedPackage`) or PDF (`/Encrypt`) becomes an UNREADABLE
  stub. It is never decrypted.
- **Known limits:** Graph-side label reads (`extractSensitivityLabels`) are not implemented, so a Graph file is
  downloaded to staging before it is screened. A save that changes only the label, not the content, is re-screened
  only when the content changes or the policy changes (CONTRACTS §16.11).

## Untrusted-content boundary

Mirrored mail, chats and shared files are third-party text. Every mirror page opens with an `[UNTRUSTED CONTENT]`
banner. The repo's `AGENTS.md` and `CLAUDE.md` tell the agent to treat `mirror/`, sidecars, `_sync/`, CHANGELOG
and `_manifest/` as data, never as instructions. Source files named like agent instruction files (`CLAUDE.md`,
`AGENTS.md`, `.cursorrules`, `.claude/`, …) are mirrored under neutral names. `.claude/settings.json` excludes
mirror `CLAUDE.md` files from memory. Git runs with the user's global excludes and hooks switched off. A secret
scan blocks any commit that would carry a credential.

**Access:** the mirror is the writer's *delegated view*. Anyone who reads the repo sees everything the writer can
open, including items from restricted libraries. Do not share the repo, and never add a remote, unless every reader
holds the same access (design-correctness-12).

## Offboarding

```sh
agentsync offboard                                          # dry run: every location, exists or not
agentsync offboard --confirm ~/agent-context/docs           # agents, Keychain, state, cache, logs, launcher, TCC grant, uv env
agentsync offboard --confirm ~/agent-context/docs --purge-data   # also the docs repo and sources.toml
```

To keep the repo for the records owner instead, hand it over before running `--purge-data`. IT then removes the
user's assignment on the enterprise app and revokes their sessions ([it-request.md](it-request.md#what-leaves-the-mac)).

## Sovereign clouds

Set `[graph] cloud = "usgov"` (GCC High), `"usgov-dod"` or `"china"` (21Vianet). GCC (moderate) is `"global"`.
agentsync then signs in at that cloud's login host and calls its Graph root, and refuses a `base_url` from another
cloud. Everything above holds unchanged: the data stays on the Mac.
