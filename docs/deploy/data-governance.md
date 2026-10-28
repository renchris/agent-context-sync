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
| `~/Library/Logs/agentsync`, `…/governance/audit.jsonl` | ids and paths; the purge audit (source id, **path hash**, time, reason, no content) | log rotation (8 MB × 2) for the logs; the audit trail is append-only | the audit is kept by design |
| `…/agentsync/purge-record.json` | a copy of every purge line of the audit trail (source id; sha256 of the stable id, source path, docs paths, selector and content; reason; time); no content | nothing: a union with the audit trail that never shrinks, kept where it is when governance/ is moved aside | no, it is kept by design, as the audit is; `offboard` removes it with the state folder |
| `…/agentsync/hold-record.json` | the hold scopes only (`all` or source ids); no reason, owner or hashes | the current holds and any the hold list lost, so it shrinks when a hold is released | n/a; `offboard` removes it with the state folder |
| login Keychain, service `agentsync` | the MSAL token cache | token lifetime / `graph logout` | `offboard` |

Time Machine: `mirror/`, `.git`, the cache and the manifest are excluded (`agentsync status` checks
`governance.time_machine`). Only hand-written `topics/` pages are backed up. Any other backup of `~/agent-context`
is a copy that no purge reaches, so exclude it too.

## Retention and deletion

- **Upstream deletion or disposition:** after a complete listing and the deletion breaker, the page becomes a
  tombstone and a purge is queued (`purge_on_upstream_delete = true`). `agentsync purge --queue` runs the queue.
  Preview it first with `agentsync purge --queue --dry-run`. It writes nothing and prints each entry's reason and
  the docs paths a run would erase.
  An `upstream-deleted` entry whose file is listed again (it came back, under its id or a later one) stays queued;
  once the file is gone, the next `agentsync purge --queue` runs it. A file that came back quarantined or refused is still purged.
  Run it weekly (suggested) or on request, after the preview. Tombstones of purged items carry no `git show` recovery
  hint.
- **Bounded history:** mirror history older than `history_days` is squashed into one snapshot and the dropped
  objects are pruned. This runs automatically on the full pass `agentsync sync` makes when one is due.
  `agentsync status` warns when compaction is due and fails when it is overdue.
- **Explicit purge:** `agentsync purge id=<stable_id> | path=<source glob> | docs=<docs glob> --reason
  {upstream-deleted,label-escalation,dlp-remediation,erasure-request,operator}` (add `--dry-run` first). It
  rewrites history in the repo and the published worktree, deletes cache entries and manifest rows, and writes one
  audit line. For every reason except upstream deletion, the item is also **suppressed**, so it is never fetched or
  published again while it still exists upstream. Every purge changes commit hashes, so consumers must re-clone.
  If another file's page now sits at a purged path, the preview marks it `taken:`; that page, its manifest row and
  its index lines stay.
  A purge that verifies also takes off the queue each queued purge of an item it erased, except a queued
  `label-escalation` (or other suppressing) purge when its own reason is `upstream-deleted`, and any entry of another
  source when it was narrowed by `--source`.
- **Erasure or DLP request:** find the item with `agentsync status` or `rg` in `_manifest/`, then run
  `agentsync purge path=… --reason erasure-request` (or `dlp-remediation`).
- **Over a lost manifest:** while `agentsync status` reports that manifest.sqlite
  `is missing, but heartbeat.json records earlier passes`, or, when heartbeat.json is there but damaged or not a
  file, that it
  `is missing, and heartbeat.json beside it cannot be read, so earlier passes cannot be ruled out`
  (for a manifest that is there but too short to be one, the same two endings start
  `is empty or too short to be a manifest`:
  `is empty or too short to be a manifest, but heartbeat.json records earlier passes` and
  `is empty or too short to be a manifest, and heartbeat.json beside it cannot be read, so earlier passes cannot be ruled out`),
  `agentsync purge` (a selector, `--queue`, and the `--dry-run` of either) and
  `agentsync compact-history` stop on that same line, exit 1 and write nothing (a real `agentsync purge`, not a
  `--dry-run`, first moves round 28's hold record from heartbeat.json to hold-record.json if it is still there),
  because no manifest read can tell a current page from a gone one. Queued purges stay queued. While heartbeat.json
  still keeps round 28's hold record and no hold-record.json is there, status's fix starts with `agentsync sync`
  once, which moves round 28's hold record from heartbeat.json to hold-record.json before it stops on the manifest;
  run it before moving heartbeat.json aside, or the move takes the record with it (B13 round 31 C580). Follow
  status's fix: restore manifest.sqlite from a copy
  you trust, or else move heartbeat.json aside (keeping a copy) so the next sync starts a new manifest; a manifest
  too short to be one is moved aside with heartbeat.json, keeping a copy. When heartbeat.json is this account's own
  file at a mode that refuses it, status's NEXT gives its `chmod 600` first, and the manifest's fix comes only if
  the manifest is still lost after that. A new start converts every file again and forgets the deletions, the
  deletion breaker's suspended removals and the archive it had recorded. After a restore, preview with
  `agentsync purge --queue --dry-run`, then run the purge. After a new start, run `agentsync sync` first:
  `purge --queue` before it leaves each entry queued `until the next sync builds the manifest`, and an entry whose
  source that sync did not list in full stays `not judged yet` until a sync does. After that, `purge --queue`
  looks for each entry's id in the docs history and runs the entry once it finds one; a run takes off the queue
  only what history names, and an entry whose id reaches nothing stays queued, `kept until its id is found`. To
  erase such a page by hand, find it with `git log --all --name-only -- mirror/<source>/` in the docs repo,
  preview `agentsync purge id=<the page's id> --source <its source> --reason <its reason> --dry-run` with the id
  in the page's front matter and the entry's own reason, the one `agentsync purge --queue --dry-run` shows for it
  (`upstream-deleted` leaves the file free to sync again if it comes back, `label-escalation` keeps it out), then
  run it without `--dry-run`; `agentsync purge docs=<page path> --reason <its reason>`, which erases every page
  ever at that path, a live one too, is the last resort. The entry stays queued after either: once a run of
  `agentsync purge --queue` (not `--dry-run`, which records nothing) has kept it and no page is left,
  `agentsync purge --queue --forget id=<the queued id> --source <its source>` takes it off, with an audit line,
  and nothing else does. The queued id is not the page's id: copy this command from the entry's `note:` line in
  `agentsync purge --queue --dry-run`, which prints it with the queued id filled in. `agentsync hold` and its
  release still work (B13 round 26 E25, ruling 14; B13 round 27 P111; B13 round 28 I92, I93; B13 round 29 C193,
  C173, C189, C201; B13 round 30 C371, C378).
- **Over a lost purge queue:** while `agentsync status` FAILs `governance.purge_queue` because purge-queue.json
  `no longer holds` a purge the audit trail records `as queued and not done`, each named
  `the <reason> purge at <time>, a <kind> selector of source <source>`, the sync refuses. Put purge-queue.json back
  from a copy you trust. With no copy, run each purge by hand with what the FAIL names for it: for a purge by id the
  manifest still names, the exact `agentsync purge id=<id> --source <source> --reason <reason>`, its id rebuilt
  from the manifest (the audit trail keeps only the selector's hash, never its text); for one it cannot rebuild,
  the id is the `stable_id:` line of the page's front matter in the docs repo's history
  (`git log --all -p -- mirror/<source>/`) while no purge has erased the page, and a `path=` or `docs=` purge
  clears it only when it erases that id; for a glob, the glob comes from your own records, and only a purge of
  the same kind and source clears it. Preview first: `agentsync purge <selector> --dry-run` says of each lost
  purge `this purge clears it` or `this purge does not clear it`, and `agentsync purge --queue --dry-run` names
  each one after its summary. A verified hand purge that erases the item a lost purge by id named clears it too,
  unless its own reason is `upstream-deleted` and the lost purge's is not (B13 round 29 C172; B13 round 30 C370,
  C312, C350, C389).
- **Over a lost suppression list:** while `agentsync status` FAILs `governance.suppressed` because suppressed.json
  `no longer lists what` a purge the audit trail records suppressed (an item, its source-path glob or a content
  hash), the sync refuses. The erased items stay out by their id and by the one source path agentsync's audit trail
  keeps for each item still listed when it was purged. Put suppressed.json back from a copy you trust. With no copy,
  preview `agentsync purge --accept-lost <at> --dry-run`, `<at>` being the time status names beside the purge's
  reason, then run it without `--dry-run`: it writes one `suppression-lost-accepted` audit line for each lost purge
  record that time names and clears those records only. A purge this build runs is timed to the microsecond, so its
  time names one record; an older build timed purges to the second, so one time can name several: the dry run then
  says `loss of <n> purges, as that time names <n> records`, and the act accepts every one of them, so put
  suppressed.json back instead if you do not mean to accept them all (B13 round 31 C582, C553). The accepted purges'
  items stay out by their id and by the one source path the audit trail keeps for each item still listed when it was
  purged, and a copy under another name of content the audit trail fingerprints stays out, as every purge this build
  runs fingerprints what it erased. A new file at another path the list held, an item's older name, its name in other
  letter case or the name of an item no longer listed when it was purged, is no longer kept out, for every accepted
  purge. Only purges an older build ran, whose audit lines hold no content fingerprints, lose their guard against a
  copy of their content under another name, and purges by a source-path glob the list no longer holds their guard
  against a new file under that glob; the act's output says which applies. The sync still refuses until each lost
  purge is put back or accepted (B13 round 31 C537, C577). A purge run again by hand clears its record only when it
  is verified and finds the content again; one that finds nothing, because the first purge erased it, clears nothing
  (B13 round 29 C172; B13 round 30 C383, C402, C401, C344).

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
  `docProps/custom.xml` (MS-OFFCRYPTO 2.6.3), the PDF info dictionary, and the mail `msip_labels` header of an
  `.eml`. A `.msg` that holds MIME text (no compound-file header, an RFC 822 header block) has its `msip_labels`
  header read as an `.eml`'s. That header is read with its RFC 2047 encoded-words decoded, and an `.eml`'s or
  MIME-text `.msg`'s `msip_labels` header that does not parse is refused under any `[policy]` label rule, not only
  under `refuse_unlabelled` (`label-unreadable`, reason `sensitivity label unreadable (msip_labels header not
  parseable); a label rule is set`; CONTRACTS §16.201 C144). That covers a label fragment that is not one
  `MSIP_Label_<guid>_<Attr>=<value>` pair, a value that still holds an encoded-word or a character from a charset
  agentsync does not know, and an encoded-word that does not decode or decodes to nothing. A header whose labels are
  all `Enabled=False`, or one with no `Name`, parses. No label is read from an Outlook-format (binary) `.msg`: it sits in a named MAPI
  property agentsync does not read. A file with an excluded label is never converted or published. Its earlier conversions are deleted and a
  `label-escalation` purge is queued. If the policy stops refusing the file (the rule is taken out of `[policy]` or
  the label comes off the file), the next sync publishes it again and the entry stays queued. While the file is
  published, `agentsync purge --queue` skips it and prints `note: stays queued: ...`, because running it would
  erase the current page and stop the file syncing. If the policy refuses the file again, the next `agentsync
  purge --queue` runs the entry. To erase the file anyway, run `agentsync purge path=<its path> --source <its
  source> --reason label-escalation`, which also takes the entry off the queue. A policy change re-screens every
  label-capable item. An agentsync update whose mail label reader can read a label the old one missed does the same
  for mail, once: while a label rule is on, every published or label-refused `.eml` and `.msg` is screened again (not
  a binary `.msg` withheld by its name), and a page the new reader finds an excluded label on is refused and its
  `label-escalation` purge queued. The sync counts that share apart from a rule change's, `re-screened <n> for a new
  mail label reader`, logs a WARNING, and until every such file is fetched `_sync/STATE.md` says `the mail label
  reader changed: <n> mail files not yet re-screened`; a [policy] re-screen still pending covers the mail already and
  keeps its own words (CONTRACTS §16.206 C404; B13 round 31 C559, lane E's C505, C518, C548). Under a label rule, a
  save that changes only a file's label, not its content, is screened at the next sync that fetches the file, and its
  page is withheld or restored to match the new label (CONTRACTS §16.60 F72).
- **Encryption:** an encrypted Office file (CFB with `EncryptedPackage`) or PDF (`/Encrypt`) becomes an UNREADABLE
  stub. It is never decrypted. An S/MIME-encrypted or IRM-protected (rpmsg) `.msg` is also an UNREADABLE stub
  (`encrypted` / `IRM-protected`), and an Outlook-format `.msg` that carries an encrypted Office attachment is
  screened as `encrypted-office` as a whole. A `.msg` that holds MIME text is read as an `.eml` (CONTRACTS §16.48
  R87), so an encrypted Office attachment in it, like one in an `.eml`, is listed by name, size and sha256 and
  never opened.
- **Known limits:** Graph-side label reads (`extractSensitivityLabels`) are not implemented, so a Graph file is
  downloaded to staging before it is screened.
- **Images and on-device OCR:** text in an image file, on a scanned PDF page and in the pictures of a PDF, deck or
  Word document is read on the Mac by Apple's Vision framework, through a helper `scripts/install.sh` compiles
  locally; agentsync uploads nothing for it. No label is read from an image file, so while any `[policy]` label rule
  is set no image file is converted, and a policy change turns an image page published before it into a stub (no purge is queued for it: its earlier
  text stays in git history until that is squashed or the item is purged). The
  pictures of a document are read only after that document's own label and encryption screen. `[convert] ocr =
  false` in `sources.toml` means the helper is neither built nor run (CONTRACTS §16.25 to §16.27).
- **Outlook `.msg`:** while any `[policy]` label rule is set, a `.msg` on this Mac is read to staging, because only its
  bytes say which kind it is; one that is online-only, on a Graph source or one the OS will not hydrate is not
  downloaded and gets the withheld page below. A `.msg` that holds MIME text (mail saved as `.eml` under a `.msg` name) is screened
  as an `.eml` (an excluded label, an `msip_labels` header that does not parse, or no label under
  `refuse_unlabelled`, refuses it) and then converted. A binary
  (Outlook-format) `.msg` keeps its label in a property agentsync does not read, so it is not converted: its page
  says that a `[policy]` label rule withholds it and that it can be saved as `.eml`. Its page's `reason:` and its
  `_sync/QUARANTINE.tsv` row both read `withheld by label rule: the label of a binary .msg, or of a .msg not on
  this Mac, cannot be read`, as do those of a `.msg` withheld without a download (above); only the reason
  agentsync stores keeps `no converter for .msg`. A policy change turns a binary `.msg` page published before it
  into that stub. No purge is queued for it, so its earlier text stays in git history until that is squashed or the
  item is purged. To publish a binary one under a label rule, drag the message out as `.eml` (CONTRACTS §16.42, §16.54).

## Untrusted-content boundary

Mirrored mail, chats and shared files are third-party text. Every mirror page opens with an `[UNTRUSTED CONTENT]`
banner. A stub or tombstone written before the banner came first (a credential, path or redacted stub among them) is
rendered again once when agentsync can rebuild it from what it stored: an intact tombstone, and the intact stub of a
live source. The banner is in every page body. The repo's `AGENTS.md` and `CLAUDE.md` tell the agent to treat `mirror/`, sidecars, `_sync/`, CHANGELOG
and `_manifest/` as data, never as instructions. Source files named like agent instruction files (`CLAUDE.md`,
`AGENTS.md`, `.cursorrules`, `.claude/`, …) are mirrored under neutral names. `.claude/settings.json` excludes
mirror `CLAUDE.md` files from memory. Git runs with the user's global excludes and hooks switched off. A secret
scan blocks any commit that would carry a credential: agentsync's built-in patterns read every page, and gitleaks,
when installed, reads them as well (it misses a plain `Password: ...`). When the scan's rules change (an agentsync
update, gitleaks installed or upgraded), files held back as `contains a credential` that are on this Mac are read
and scanned again once, and every published page and stub is scanned again. A page the new rules match is replaced
by a stub and a purge of its history is queued. A `[governance] archive` copy they match is deleted with its
sidecars, its tombstone stops naming it, and a purge is queued.

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
