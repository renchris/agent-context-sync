<!-- Hero: a launch film rendered from film/ by `npm run film:loop` (inline WebP) and `npm run film:mp4` (the linked MP4). Direction: docs/design/hero/DIRECTION.md § Launch video. -->
<div>
<a href="docs/media/launch-film.md"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/media/hero-dark.webp">
  <img src="docs/media/hero-light.webp" width="100%" alt="Keep an agent-readable docs/ folder in sync with OneDrive, SharePoint, Outlook and Teams by processing only what changed. Four steps: 1, ask each source what changed, and make expiry cheap; 2, decide with three hashes before reading a byte; 3, read bytes only on purpose; 4, publish a pure function of the source, in git. The picture: a company's files as four lanes of real, named documents running to the horizon, one lane per source (Word pages, spreadsheets, decks and PDFs, emails, chat messages), beside a fifth lane, docs/, of converted markdown pages with a git line down it. After one sync, two documents stand up in amber among thousands lying flat: proposal.docx from OneDrive and forecast.xlsx from SharePoint, the spreadsheet banded green as a no-op save. The camera flies over them to the next stretch of the field, where the sources report eight changed files; six are decided unchanged before a byte is read. It comes down to the other two, which stand up and turn amber as their bytes arrive, then follows the converted proposal page to docs/, where it lands as one commit, and flies back to the start.">
</picture></a>
<br>
<sub><a href="docs/media/launch-film.md">▶ Watch the 26-second launch film</a> · a design with measured probes, not yet an implementation</sub>
</div>

# agent-context-sync

**Keep an agent-readable `docs/` folder in sync with OneDrive, SharePoint, Outlook and Teams by processing only what changed.**
A since-token per source says *what* changed. A manifest with three hashes decides, before a single file byte is read,
whether that change costs anything. Git carries the result, so "what changed since Tuesday" is `git log`.

> [!NOTE]
> **Status: a design with measured probes. There is no implementation yet.** The build order is
> [§10 of the design](docs/design/agent-context-sync.md#10-build-order). It starts once a target corporate tenant is
> chosen, because five of the probes can only be measured there ([what is measured](#four-probes-are-measured-five-still-need-a-corporate-tenant)).

Coding agents answer best from a folder of markdown they can read and grep. A company's knowledge lives somewhere
else: tens of thousands of Office files, PDFs, mail and chat in Microsoft 365, changing in place under the same name,
and mostly online-only on the laptop. Re-reading all of it on every sync means hours of downloads. The design gets the
diff in four steps, and each step exists so that the next one can skip work:

1. **[Ask each source what changed, and make expiry cheap.](#1-ask-each-source-what-changed-and-make-expiry-cheap)**
   A Graph delta link, an FSEvents event id or a metadata walk. When a token expires, the fallback compares metadata
   and reads zero file bytes.
2. **[Decide with three hashes before reading a byte.](#2-decide-with-three-hashes-before-reading-a-byte)**
   H0 decides whether to read, H1 whether to convert, H2 whether anything downstream changed. Office changes bytes on
   every no-op save, and H2 is what makes that save free.
3. **[Read bytes only on purpose.](#3-read-bytes-only-on-purpose)**
   An online-only file is a placeholder. The pipeline refuses to download it by default, and one budgeted step opts in.
4. **[Publish a pure function of the source, in git.](#4-publish-a-pure-function-of-the-source-in-git)**
   Converted pages are machine-generated and diffable, and each curated page records which version of each source it
   was written from.

<!-- Diagram source: docs/diagrams/pipeline.mmd — edit it, run `npm run diagrams`, commit the regenerated SVGs. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/pipeline-dark.svg">
  <img src="docs/diagrams/pipeline-light.svg" alt="The pipeline, top to bottom. L0 sources: OneDrive, SharePoint, Outlook and Teams; a local sync-client folder; a manual drop. Each feeds an L1 arm with its own since-token: arm A Graph delta, arm B a local walk, arm C an inbox. All three feed the L2 manifest, keyed by stable id and classified with H0, then H1, then H2. If the hash or stat tuple is equal the object is unchanged and zero bytes are read. If it may have changed, a budgeted materialise step downloads it (amber, the only byte cost) and L3 converts it into a write-once cache. If the H2 output hash is equal, an early cutoff stops everything downstream. If it differs, the result lands in docs/mirror, curated pages in L4 docs/topics pin its version, and L5 surfaces docs/ in git. L6 operations re-enumerate metadata back into the manifest on a schedule.">
</picture>

<details>
<summary>Interactive Diagram</summary>

<!-- mermaid-fence: docs/diagrams/pipeline.mmd (auto-synced by `npm run diagrams`) -->
```mermaid
flowchart TD
  GD["<b>L0</b> · OneDrive · SharePoint<br/>Outlook · Teams"]
  FP["<b>L0</b> · local sync-client folder<br/>(File Provider)"]
  MD["<b>L0</b> · manual drop"]
  A["<b>L1 · Arm A · Graph delta</b><br/>stored deltaLink<br/>1 RU per poll"]
  B["<b>L1 · Arm B · local walk</b><br/>FSEvents event id,<br/>getattrlistbulk walk"]
  C["<b>L1 · Arm C · inbox</b><br/>same walk,<br/>max(created, modified)"]
  M["<b>L2 · Manifest</b><br/>identity = stable id, never path<br/>H0 stat tuple, then H1, then H2"]
  U(["<b>unchanged</b><br/>zero bytes read"])
  X["<b>materialise</b><br/>budgeted download"]
  V["<b>L3 · Convert</b><br/>write-once cache<br/>keyed on converter + content"]
  E(["<b>early cutoff</b><br/>nothing downstream moves"])
  MI[("<b>docs/mirror/</b><br/>pure function of source")]
  T["<b>L4 · Curate</b><br/>docs/topics/ pins<br/>at_rendered_sha256"]
  S[("<b>L5 · Surface</b><br/>docs/ in git · INDEX.md<br/>what changed = git log")]
  O["<b>L6 · Operate</b><br/>one writer · flock · heartbeat<br/>hourly full reconcile"]
  GD --> A
  FP --> B
  MD --> C
  A --> M
  B --> M
  C --> M
  M -->|"hash or stat tuple equal"| U
  M -->|"maybe changed"| X
  X --> V
  V -->|"H2 output hash equal"| E
  V -->|"H2 differs"| MI
  MI --> T
  T --> S
  O -.->|"re-enumerates metadata"| M
  classDef neutral fill:#161b22,stroke:#3d444d,color:#e6edf3
  classDef amber fill:#2d2111,stroke:#f0a33a,color:#e6edf3
  classDef green fill:#0f2a19,stroke:#3fb950,color:#e6edf3
  class GD,FP,MD,A,B,C,M,MI,T,S,O neutral
  class X,V amber
  class U,E green
```

<sup><a href="docs/diagrams/pipeline-dark.svg?raw=true">full-screen dark</a> · <a href="docs/diagrams/pipeline-light.svg?raw=true">light</a> · <a href="docs/diagrams/pipeline.mmd">source</a></sup>

</details>

<sub>Colour means cost: amber steps read file bytes, green steps are free exits, everything else touches metadata only.</sub>

## 1. Ask each source what changed, and make expiry cheap

Every source already keeps a change log of its own. The pipeline stores one since-token per source and asks for the
changes since that token, instead of looking at the files:

| Source | Since-token | Cost of asking |
|---|---|---|
| OneDrive, SharePoint, Outlook folders, Teams channels | Microsoft Graph `deltaLink` | 1 resource unit per poll: a drive polled every 60 s uses 0.12 % of the smallest per-app daily budget (Microsoft's published defaults) |
| A folder synced by the OneDrive client | FSEvents event id, backed by a `getattrlistbulk` metadata walk | 0.13–0.31 s per 100,000 files on local APFS ([C11](docs/design/receipts/verify/C11-local-walk.md)); a 2,000-file walk costs the same on OneDrive's File Provider as on local disk ([C14 §3](docs/design/receipts/verify/C14-file-provider.md)) |
| A manual drop folder | the same walk | the same |

**Every token expires by design:** a 410 from Graph, a wrapped FSEvents journal, a changed volume UUID. So the part that
carries the load is the fallback, not the token: list the metadata again, diff it against the manifest, and read no file
bytes. Deletions are trusted only inside a listing that completed.

<!-- Diagram source: docs/diagrams/since-token.mmd — edit it, run `npm run diagrams`, commit the regenerated SVGs. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/since-token-dark.svg">
  <img src="docs/diagrams/since-token-light.svg" alt="The since-token lifecycle. A new source, or the hourly schedule, starts a full enumeration: metadata only, zero file bytes, the listing diffed against the manifest, and deletes allowed only once it completes. When the last page arrives the token is stored outside git and the source is synced. Polling with the token (Graph delta at 1 resource unit, or FSEvents resuming from an event id) has four outcomes: 200 commits docs/ and then advances the cursor; 429 or 503 backs off per Retry-After; 410 Gone, a journal wrap or a volume UUID change means the token expired by design; 400 malformed means our cursor store is corrupt, so alarm and drop it. Both expiry and corruption, in red, lead back to a full enumeration.">
</picture>

<details>
<summary>Interactive Diagram</summary>

<!-- mermaid-fence: docs/diagrams/since-token.mmd (auto-synced by `npm run diagrams`) -->
```mermaid
flowchart TD
  ENTRY(["<b>new source</b><br/>or the hourly schedule"])
  SYNC["<b>synced</b><br/>token stored outside git"]
  POLL["<b>poll with the token</b><br/>Graph delta: 1 RU<br/>FSEvents: resume from event id"]
  COMMIT(["<b>commit docs/</b><br/>then advance cursor"])
  WAIT(["<b>back off</b><br/>Retry-After"])
  EXP["<b>token expired</b><br/>by design"]
  BAD["<b>cursor corrupt</b><br/>alarm, drop it"]
  FULL["<b>full enumeration</b><br/>metadata only, zero file bytes<br/>diff the listing against the manifest<br/>deletes allowed only once it completes"]
  ENTRY --> FULL
  FULL -.->|"last page: store token"| SYNC
  SYNC --> POLL
  POLL -->|"200"| COMMIT
  POLL -->|"429 / 503"| WAIT
  POLL -->|"410 Gone<br/>journal wrap<br/>UUID change"| EXP
  POLL -->|"400<br/>malformed"| BAD
  EXP --> FULL
  BAD --> FULL
  classDef neutral fill:#161b22,stroke:#3d444d,color:#e6edf3
  classDef red fill:#2d1417,stroke:#ff7b72,color:#e6edf3
  class ENTRY,SYNC,POLL,COMMIT,WAIT,FULL neutral
  class EXP,BAD red
```

<sup><a href="docs/diagrams/since-token-dark.svg?raw=true">full-screen dark</a> · <a href="docs/diagrams/since-token-light.svg?raw=true">light</a> · <a href="docs/diagrams/since-token.mmd">source</a></sup>

</details>

A symlink into the OneDrive folder cannot play this role. It carries no since-token, and the agent's own tools do not
see through it: in a three-file fixture, ripgrep, BSD `grep -r` and `-R`, `find`, and Claude Code's Grep and Glob each
found 1 of 3 files, all with exit 0, and `git` stores the link as a single blob
([C1](docs/design/receipts/C1-macos-onedrive-symlinks.md)). The local walk, by contrast, gets a real change signal from
the kernel: `ATTR_CMN_GEN_COUNT`, returned for 2,000 of 2,000 File Provider items, placeholders included
([C14 §3](docs/design/receipts/verify/C14-file-provider.md)).

## 2. Decide with three hashes before reading a byte

Identity is the stable id (Graph `driveItem.id`, or the file id locally), never the path, so a folder rename is one
record rather than a subtree of deletes and creates. Each observed object then passes up to three hashes, and every
decision before the byte read costs nothing:

| Hash | Computed from | Decides |
|---|---|---|
| **H0** | the provider's `quickXorHash`, else `(fileid, gen_count)` and the stat tuple | whether to read the bytes at all |
| **H1** | a canonical content hash: sorted `(part, bytes)` with volatile parts removed | whether to convert |
| **H2** | the converter's output | whether anything downstream changed |

<!-- Diagram source: docs/diagrams/classifier.mmd — edit it, run `npm run diagrams`, commit the regenerated SVGs. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/classifier-dark.svg">
  <img src="docs/diagrams/classifier-light.svg" alt="The three-hash classifier for one observed object, keyed by source_id and never by path. A dataless placeholder is recorded as DATALESS and never opened. Otherwise H0, the zero-byte check (the provider hash, else file id, gen count and the stat tuple), decides: equal means UNCHANGED with zero bytes read; moved, or a new id, means the bytes are read through a budgeted materialise step. H1, the canonical content hash, then decides: equal means TOUCHED, NOT CHANGED, a no-op save, and stops; different means convert through the write-once cache. H2, the hash of the converter output, decides last: equal is an early cutoff with no commit; different commits to docs/ and flags dependent curated pages STALE.">
</picture>

<details>
<summary>Interactive Diagram</summary>

<!-- mermaid-fence: docs/diagrams/classifier.mmd (auto-synced by `npm run diagrams`) -->
```mermaid
flowchart TD
  O["<b>observed object</b><br/>keyed by source_id, never by path"]
  D{"dataless<br/>placeholder?"}
  DL(["<b>DATALESS</b><br/>skip, never open"])
  H0{"<b>H0 · zero bytes</b><br/>provider hash, else<br/>(fileid, gen_count) + stat"}
  U(["<b>UNCHANGED</b><br/>zero bytes read"])
  R["<b>read bytes</b><br/>budgeted materialise"]
  H1{"<b>H1</b> · canonical<br/>content hash"}
  T(["<b>TOUCHED, NOT CHANGED</b><br/>no-op save: stop"])
  CV["<b>convert</b><br/>write-once cache"]
  H2{"<b>H2</b> · hash of<br/>converter output"}
  EC(["<b>early cutoff</b><br/>no commit"])
  G[("<b>commit to docs/</b><br/>dependents flagged STALE")]
  O --> D
  D -->|"yes"| DL
  D -->|"no"| H0
  H0 -->|"equal"| U
  H0 -->|"moved, or new id"| R
  R --> H1
  H1 -->|"equal"| T
  H1 -->|"differs"| CV
  CV --> H2
  H2 -->|"equal"| EC
  H2 -->|"differs"| G
  classDef neutral fill:#161b22,stroke:#3d444d,color:#e6edf3
  classDef amber fill:#2d2111,stroke:#f0a33a,color:#e6edf3
  classDef green fill:#0f2a19,stroke:#3fb950,color:#e6edf3
  class O,D,DL,H0,G neutral
  class R,H1,CV,H2 amber
  class U,T,EC green
```

<sup><a href="docs/diagrams/classifier-dark.svg?raw=true">full-screen dark</a> · <a href="docs/diagrams/classifier-light.svg?raw=true">light</a> · <a href="docs/diagrams/classifier.mmd">source</a></sup>

</details>

H2 is not an optimisation. Real Office apps never re-save a file byte for byte, even with no edit
([C12 §6](docs/design/receipts/verify/C12-office-resave.md)):

| Format | What changed on a no-op save, outside `docProps/` | Converter output (H2) |
|---|---|---|
| `.pptx` | nothing | identical |
| `.xlsx` | `xl/workbook.xml`: a new `documentId` GUID on every save | identical |
| `.docx` | `word/document.xml` and `word/settings.xml`: new revision-save ids (`w:rsid*`) | identical |

A LibreOffice round trip changed the styles part and every sheet. Without H2, each of those saves would become a commit
that says nothing. The bookkeeping is cheap: a 200,000-row manifest is 31 MB of JSON and loads in 0.12 s
([design §4.1](docs/design/agent-context-sync.md#41-the-seven-invariants)).

## 3. Read bytes only on purpose

On macOS, OneDrive keeps most files online-only: a placeholder with the full size, zero allocated blocks and the
`SF_DATALESS` flag. Whether reading one downloads it depends on the caller's materialization policy, and the default
differs by context. This is a real run of the probes against a OneDrive for Business folder:

<p align="center">
  <img src="docs/media/dataless-read.webp" alt="Terminal recording. evict blob.bin makes a OneDrive file online-only; stat shows it as compressed,dataless, 2000000 bytes, 0 blocks. launchd-run.sh readfp default /dev/null runs the probe as a launchd job, which reports policy=off(1). readfp off blob.bin applies that policy to the placeholder: open succeeds, the read fails with Resource deadlock avoided (errno 11), 0 bytes, and the file stays dataless. readfp default blob.bin from the login shell runs with policy=on(2): the read succeeds, 2000000 bytes arrive in under a second, and the file now has 3912 blocks." width="100%">
</p>

<sub>`launchd-run.sh` submits `readfp` as a real launchd job, which reports its default policy: off. `readfp off` applies
that policy to the placeholder, and the read fails with `EDEADLK`. The login shell's default is on, so the same read
downloads 2 MB. The direct read from a launchd job fails the same way, errno 11
([C14 §2](docs/design/receipts/verify/C14-file-provider.md)). Recorded headlessly with VHS
([`scripts/record-demo.sh`](scripts/record-demo.sh)).</sub>

Two consequences shape the design:

- **A launchd job is fail-closed for free.** The walk and hash stages inherit the refusing policy, and one budgeted
  `materialise()` step opts in with `setiopolicy_np(…_ON)` or the job's `MaterializeDatalessFiles` key.
- **A stray walk from a login shell is a download.** `rg` or a hash pass over the sync folder would pull down the whole
  library, and macOS evicts it again under disk pressure. That is why phase 1 reads no bytes, and why a placeholder is
  recorded as `dataless`, a state distinct from both changed and unchanged.

## 4. Publish a pure function of the source, in git

`docs/mirror/` is generated one to one from the sources and never hand-edited. Its frontmatter holds content-derived
fields only (no `converted_at`), so an unchanged source re-renders to identical bytes. `docs/topics/` is written by the
agent, and each page pins the exact version (`at_rendered_sha256`) of every mirror page it cites. In the design, the two
questions an agent asks are each one command:

```sh
git log --since=2026-09-01 --stat -- docs/mirror   # what changed upstream
sh refresh-queue.sh docs/DEPENDS.tsv               # which curated pages are now stale, and why
```

The refresh queue is one awk pass over a generated `DEPENDS.tsv`: 0.08–0.12 s over 1,600 rows
([C11 §3](docs/design/receipts/verify/C11-local-walk.md)). It reports `STALE`, `SOURCE-DELETED` and `SOURCE-UNREADABLE`
separately because each needs a different action ([design §4.5](docs/design/agent-context-sync.md#45-l4--curation-incrementally)).
Freshness never rides on file times: `git clone` resets every mtime, and Claude Code's Glob orders its results by mtime
([retrieval review](docs/design/receipts/review/retrieval.md)).

## Four probes are measured, five still need a corporate tenant

The probes in [design §9](docs/design/agent-context-sync.md#9-what-to-measure-first-on-the-corporate-tenant) each
decide one part of the architecture. Four ran on a Microsoft 365 Business Standard tenant on macOS 15.7.9 (2026-09-23):

| # | Probe | Result |
|---|---|---|
| 3 | Which FSEvents fire when a file is edited on the web | A downloaded file raises `Modified` on the CloudStorage path within about 20 s. A new folder raises only its directory event, so a new directory needs a walk ([C14 §4](docs/design/receipts/verify/C14-file-provider.md)) |
| 4 | Reading a placeholder, by context | Login shell downloads; launchd job fails `EDEADLK`, errno 11 ([C14 §2](docs/design/receipts/verify/C14-file-provider.md)) |
| 5 | Walk cost and `GEN_COUNT` on File Provider | `GEN_COUNT` on 2,000 of 2,000 items; walk cost equal to local APFS ([C14 §3](docs/design/receipts/verify/C14-file-provider.md)) |
| 6 | Office no-op re-save | Never byte-stable; H2 identical for `.pptx`, `.xlsx` and `.docx` ([C12 §6](docs/design/receipts/verify/C12-office-resave.md)) |

Five depend on the tenant itself, so they wait for the target one: whether SharePoint libraries return `quickXorHash`
in delta (1), whether a non-admin may consent to `Files.Read.All` and `Sites.Read.All` (2), whether two downloads of a
sensitivity-labelled file are byte-identical (4b), whether the spreadsheet converter reads a real Excel-saved workbook
correctly (8), and how long an idle delta token lives (9).

## Everything behind these numbers is in this repository

| Path | What it holds |
|---|---|
| [`docs/design/agent-context-sync.md`](docs/design/agent-context-sync.md) | The full design: layers L0–L6, seven invariants, 25 ranked failure modes with the element that closes each, rejected alternatives, and the build order |
| [`docs/design/receipts/`](docs/design/receipts/) | 14 research-axis reports, 14 verifier reports and 4 review lenses, with the commands behind each number |
| [`probes/`](probes/) | The macOS probes in C and Swift, plus the Office re-save script. `make -C probes`, then [`probes/README.md`](probes/README.md) |
| [`docs/diagrams/`](docs/diagrams/) | Mermaid sources for the diagrams. `npm run diagrams` re-renders them, and CI fails when a render is stale |

The receipts were published with account names, tenant ids, file names and local paths replaced by placeholders
(Contoso, `user@example.com`, `~/`). Every measurement is unchanged.

## License

[MIT](LICENSE)
