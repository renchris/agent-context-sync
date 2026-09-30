# Receipts

The evidence behind [`../agent-context-sync.md`](../agent-context-sync.md), as recorded during the
research wave (2026-09-21 to 2026-09-23). Files are kept as written: where a receipt names
`docs/research/agent-context-sync-2026-09-21.md`, that is the design document one level up.

**CORRECTED (2026-09-29):** not every receipt is from that wave. `verify/C15-corporate-controls.md` was researched
and measured on 2026-09-29, and the design is now implemented as `agentsync`
([`src/agentsync/`](../../../src/agentsync/)). The implementation's own verification is recorded in the
[dated rollout](../../plans/implementation.md#rollout--dated), and its install and IT pack is in
[`docs/deploy/`](../../deploy/README.md). These receipts are the evidence the design rests on. They are not the
implementation's test results.

| Path | What it holds |
|---|---|
| `A1` … `I2` (14 files) | One report per research axis: Cursor, DeepWiki and indexers, Graph drive delta, Graph mail and Teams, macOS OneDrive and symlinks, Watchman and FSEvents, rclone, manifest and build graphs, converters, RAG ingestion, agent-docs conventions, the version mess, and two adversarial reviewers |
| `verify/C1` … `C10` | Adversarial verifiers on the load-bearing claims; compiled probes in `verify/experiments/` |
| `verify/C11-local-walk.md` | `getattrlistbulk` walk over 100,000 local files: 0.13–0.31 s |
| `verify/C12-office-resave.md` | Office no-op re-save, per-part diff (§6 has the measured result) |
| `verify/C13-pandoc-docx.md` | pandoc docx conversion, byte-identical across two runs |
| `verify/C14-file-provider.md` | OneDrive File Provider: dataless reads, `GEN_COUNT`, FSEvents on web edits, launchd vs login shell |
| [`verify/C15-corporate-controls.md`](verify/C15-corporate-controls.md) | Corporate controls on a managed Mac (2026-09-29): sign-in via the MSAL broker, then loopback PKCE, then device code; a delegated Graph permissions matrix; TCC on the File Provider domain; sensitivity labels; TLS inspection and proxies; the PDF route; retention and legal hold for a local git mirror. §8 lists the probes still to run on the corporate tenant |
| `review/` | Four post-wave review lenses (facts, operations, retrieval, completeness) and their fixture scripts |

Private data from the original run (account names, tenant and drive ids, personal file names, local
paths) was replaced with placeholders before publication. Measurements, commands and public
references are unchanged.
