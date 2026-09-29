---
status: in-progress
---

# Implementation plan — from design to a running sync on managed Macs

Scope (frozen, 2026-09-29): implement agent-context-sync per design §10 weeks 1–3 (local arm, SQLite manifest with the
H0/H1/H2 classifier, converters with a write-once cache, `docs/mirror` + INDEX/CHANGELOG/STATE + one git commit per
cycle, Graph drive + mail + Teams delta arm, curation `DEPENDS.tsv` + STALE lint, single-writer lock + launchd agent)
with tests; verify it end to end on this Mac (local fixture, the `OneDrive-Contoso` File Provider mount, Graph where a
sign-in allows); package it for managed Macs (no-admin install, LaunchAgent, IT request pack: Entra app registration,
configuration profile, data-governance note); fix the audit's confirmed defects; and write a dated corporate rollout
whose only open items are named operator or IT actions.

## Phase 0 — orchestration

- **Execution locus per wave:** W1–W3 run as Workflow fan-outs (the operator opted into multi-agent orchestration with
  "ultracode"; each implementer owns disjoint files in the main tree and never commits, so the lead commits one module
  at a time). The lead holds contracts, merges, commits and the close.
- **Lead context budget:** stay under 50%; succession point is after W2 merge (everything is on disk by then).

| Wave | What | Owner of files |
|---|---|---|
| W0 | Readiness audit (six dimensions, adversarial verify, critic) | read-only |
| W1a | Skeleton: `pyproject.toml`, `src/agentsync/{model,config}.py`, contracts doc, test scaffold | architect agent |
| W1b | Modules in parallel: manifest+classifier · local arm · converters+cache · publish+lints · Graph auth+client · Graph delta arms · curation · ops (lock, launchd, CLI) | one implementer each |
| W2 | Integration: CLI wiring, end-to-end on fixture + File Provider mount, review + fix | integration agents |
| W3 | Corporate pack + audit fixes + rollout plan with dates | docs agents |

## Week-0 decisions, taken as defaults (each is one config key to change)

| Decision (design §10 week 0) | Default | Why |
|---|---|---|
| Single writer | The Mac that runs `agentsync install-agent`; a `flock` on the state dir enforces one | Design §4.7 |
| Where `docs/` lives | Its own git repo, `~/agent-context/docs`, outside any cloud-synced path, **no remote** | Corporate content never leaves the machine unless IT names a tenant-owned remote |
| AGPL (PyMuPDF) | Not used. PDF text via `pdfminer.six` (MIT) | Avoids a licence review in a corporate rollout |
| Converters | pandoc via `pypandoc_binary` (docx, html, rtf, odt), `openpyxl` emitter (xlsx), `python-pptx` (pptx), `pdfminer.six` (pdf) | All permissive licences; `pypandoc_binary` needs no Homebrew or admin |
| In-scope set | `~/agent-context/sources.toml`, written by `agentsync init` | Design §10 week 0 |
| Graph client id | Configurable; defaults to none, so the local arm works with zero IT involvement | The local arm needs no consent at all |
