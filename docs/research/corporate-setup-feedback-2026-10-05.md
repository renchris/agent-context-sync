# Corporate Mac field report, triaged (2026-10-05)

Scope (frozen): triage the operator's field report from the zero-IT corporate Mac against origin/main `97821c0`
and the KISS plan; for each finding, record the outcome, friction kind and evidence, whether it is already fixed,
covered by a KISS item or new, and the smallest fix for each new item.

Method: one verifier per claim checked it against the code on `origin/main`, git history and
`docs/plans/kiss-simplification.md`. A separate skeptic per claim then tried to refute that verdict, and a
completeness critic looked for gaps (Workflow run `wf_4d5f8e76-c8a`, 37 agents). The three data-safety items and
the inbox-path defect were also re-checked by hand.

## Verdict

- **Data safety: three items.**
  - `purge --queue --dry-run` really purged. It is **fixed** on `origin/main` by `cace1d9` (landed during this
    triage); what the field run erased is still unknown.
  - The setup report's redaction misses folder and file names nested below a configured folder.
  - W4 K05, as committed on `kiss-w4`, can leave `~/agent-context/inbox` unsynced on this Mac.
- **Nothing in the report contradicts the KISS end state.** Two requests would grow the surface that KISS cuts:
  `add-source --kind inbox --exclude --quiescence` and `sync --source X --no-quiescence`. They are answered
  without new flags (below).
- **Three questions are the operator's** (§ Open operator decisions): retention of chat-derived history, shipping
  no-IT exporters, and reading the Teams cache for Copilot recaps.
- **The setup outcome cannot be computed.** The paste is a hand-written field report: there is no
  `setup-report.md`, `friction.md` or `install.log`. That puts it outside the measures in setup-feedback.md § 4.

## Inputs and version

| Fact | Value | Source |
|---|---|---|
| Report | hand-written field report, 2026-09-30 to 2026-10-04, macOS 15.7, Apple M4 Max, a large professional-services tenant | the paste |
| Setup prompt | most likely v6, but not proven. v6 landed at 2026-09-30T04:40 (`35c2b5f`, `fb9305f`), inside the window; `53824b3`, main just before the window, has no `setup-prompt-compat` line | `git log` |
| install.sh commit | unknown (no install.log) | — |
| Computed outcome / run type | unknown / unknown (setup-feedback.md § 4: with no friction log and no install.sh run, the outcome is "unknown") | setup-feedback.md:142-157 |
| Companion file `agentsync-fix-request.md` (F1–F12) | not received. The paste names F6 (`.msg`), F8 (TOKEN lint) and F9 (empty cloud folders); F1–F5, F7 and F10–F12 are unmapped. No trace of the file exists in the repo or its history (`docs/design/receipts/review/facts.md` has unrelated F-numbered headings) | `git log --all`, `rg` |

Commits that landed mid-window (`fb9305f`, `3055724`, `5e1cc1a` and `a5d17c1`) are written below as "fixed on
main; field build unknown". Without the clone's HEAD sha nobody can say whether the field Mac ran them.

Friction kinds: the report has no friction log, so each kind below was assigned during triage from the § 4 list.
The agent did not log them.

## 1. What happened

| # | Event | Result | Kind | Evidence line (from the report) |
|---|---|---|---|---|
| 1 | Graph arms for Outlook and Teams | failed (not possible here) | — | "consent is the whole cost: weeks, and possibly never" |
| 2 | Meeting transcripts from `Recordings/` | failed | — | "`Recordings/` held only `.mp4` (3 of 3)" |
| 3 | Copilot meeting notes | worked with friction (outside agentsync) | deviation | "the Teams client caches recaps locally" |
| 4 | `agentsync whoami` with `client_id` unset | worked with friction | error | "exits with a configuration error, not 'not signed in'" |
| 5 | Adding six inbox sources | worked with friction | deviation | "appended to `sources.toml` by hand" |
| 6 | Inbox quiescence with atomic writers | worked with friction | deviation | "Every re-export needed a 65 s sleep, or the cycle reported `INCOMPLETE … unchanged`" |
| 7 | `.eml` attachments, `.msg` (F6) | worked with friction | deviation | "lists attachments without converting them" |
| 8 | Credential quarantine on Teams invites | worked with friction | deviation | "quarantined two ordinary meeting invites whose join URL carries `pwd=`" |
| 9 | TOKEN lint (F8) | worked with friction | error | "False positive on 'bearer securities'" |
| 10 | File Provider walk and downloads (F9) | failed | error | "`baseline INCOMPLETE` forever"; "`errno 89 Operation canceled` or hung until a TCC prompt was answered" |
| 11 | doctor network check | worked with friction | error | "PAC file … unsupported, so it warns on every run" |
| 12 | Moving a source path | worked | — | "retired the old items as tombstones, with no purge" |
| 13 | `purge --queue --dry-run` | failed (destructive) | error | "Executed both queued purges and rewrote 14 mirror commits" |
| 14 | `teams-month` converter on outside JSON | worked | — | "rendered correctly on the first try" |
| 15 | Silent TCC prompts | worked with friction | click | "Reads hang without any error until a human clicks Allow" |
| 16 | Mirror history rescued 46 evicted Teams messages | worked | — | "We recovered them from the agentsync mirror's git history" |
| 17 | Inbox as the integration point for five exporters | worked | — | "none needed agentsync changes beyond `sources.toml`" |
| 18 | `.DS_Store` / capture manifests as sources; "visible" versus "missing" | worked with friction | — | "Finder `.DS_Store` and capture manifests must never be canonical sources" |
| 19 | cp1252 message bodies | worked with friction (exporter side) | — | "Message bodies are sometimes `bytes`, in cp1252" |
| 20 | Loop notes | failed (not possible here) | — | "`.loop` is a compressed Fluid container" |
| 21 | Teams IndexedDB copy (EINTR, macOS 15 "access data from other apps"), browser tab closed by the exporter | failed then worked (exporter side) | click | "One early bug closed a user tab" |

## 2. Status per failure or friction

| # | Status on origin/main | Detail |
|---|---|---|
| 1 | **Not possible on this Mac**, and the fallback is **already fixed** | Graph needs an Entra app and consent (`config.py:538-542`, `graph/auth.py:319-323`), which the 2026-10-01 zero-IT ruling rules out (`b79df08`, `docs/plans/implementation.md:46-48`). The no-IT inbox route predates the window (`502295d`, `11fd30e`), was made usable by `5e1cc1a` `add-source --inbox` (fixed on main; field build unknown), and becomes automatic in **W4 K05** (`aba957e` on `kiss-w4`). Residual: no doc says how a *Teams* message reaches the inbox; that belongs in **W5 K04** (new item N9). |
| 2 | **Not possible on this Mac**; one doc is **new** | Adding meeting scopes to `it-request.md` needs an Entra app, so the zero-IT ruling settles it, and K18 and K04 retire the it-request step anyway. The design doc already carries the correction (`docs/design/agent-context-sync.md:202, :232`). **New:** `docs/plans/implementation.md:51-53` still promises zero-IT "meeting recordings and transcripts stored in OneDrive", which contradicts it, and no converter takes `.mp4` (N10). The "Teams admin toggle" is unverified: the design names an application access policy instead. |
| 3 | **Operator decision** (D3) | The Graph `aiInsights` half is not possible here. The local Teams-cache half needs no IT, but it reads another app's private store. |
| 4 | **Covered by W4 K18** | K18 hides `whoami` for this very reason (plan:261-272). Optional wording fix N11. Exit 78 is pinned by `tests/test_cli.py:447-448` and stays. |
| 5 | **Covered by W4 K05 / K14 / K19b**, with a **K05 defect** (N3) | `5e1cc1a` added `add-source --inbox [--id]`. K05 deletes that flag and K14 deletes `--id`, leaving exactly one inbox that is created automatically. The requested `--kind/--exclude/--quiescence` would re-grow the surface K19b freezes; the inbox defaults already cover exclude and quiescence (`DEFAULT_EXCLUDES` plus `_INBOX_IGNORES`, `quiescence_s = 60`). Moving six inbox sources into one needs `state = "retired"` per source (removal exits 78, `cycle.py:950-963`). Leaving them is safe (K19b `test_old_config_shapes_load`), so consolidating is optional and nobody should be pushed to it. |
| 6 | **New** (N4) | Not fixed. `a5d17c1` only turns the withheld-inbox state into a note that does not block NEXT. `sync --source X --no-quiescence` conflicts with W4 K13b (it deletes `sync --source`) and with "sync takes no visible options" (plan:37). `quiescence_s = 0` is **unsafe** in the single shared inbox K05 creates: a verifier reproduced a half-written `.eml` being committed at q=0. A temp-then-rename write resets ctime, so it is held for about 60 s anyway (`arm_local.py:865-867`). |
| 7 | **New** (N5); attachment conversion and `.msg` are **unplanned features** | `convert/eml.py:115-117, :176` and `docs/design/CONTRACTS.md:2355` promise "the mail arm's second phase", meaning the Graph mail arm, which cannot run here. `.msg` is deliberately unrouted (`CONTRACTS.md:548`, no permissive parser chosen). |
| 8 | **New** (N6) | `lints.py:55` `generic-password` matches `pwd=` in a Teams join URL; gitleaks already ignores it. The redacted field URL has not been seen, so check its host and parameter (`pwd=` against `p=`) before writing the pattern. |
| 9 | **New** (N7) | Not fixed (F8). The page check uses bare `TOKEN_PATTERN` (`token=|deltatoken=|Bearer `, `lints.py:35`). The warning reaches curate, every sync's output, `_sync/STATE.md` § Lint findings (`publish.py:1627-1631`) and the CHANGELOG count. Its advice "route it through the SECRET quarantine" (`lints.py:393`) cannot be acted on: TOKEN hits never feed `_quarantine_secrets` (`cycle.py:1932`). |
| 10 | **Partly fixed** by `a5d17c1` (2026-10-04: an incomplete pass is a WAITING line, not an endless rule 3); the rest is **new** (N8, N12) | errno 89 (`ECANCELED`) falls through to a generic one-try `MaterialiseError` (`materialise.py:315-345`). The interactive sync walk has no time limit (`arm_local.py:337-339` is a bare `os.scandir`). The WAITING line's advice to "exclude it" leads into N12. |
| 11 | **New** (N13) | `cli.py:1028-1040` warns on PAC even though every proxy consumer is gated on a live Graph source (`cycle.py:381-394`), which this Mac cannot have. |
| 12 | **Works** as designed | Scope-change retirement is reversible: putting the old path back revives the pages (`publish.py:1230-1234`). It skips the deletion breaker (`cycle.py:1508-1517`), so a wrong edit retires out-of-scope pages in one pass. No `archive=true` test exists for it (optional). |
| 13 | **Already fixed**: `cace1d9` (on `origin/main`; landed during this triage, after the field window) | `_cmd_purge`'s `--queue` branch (`cli.py:1687-1695`) never reads `args.dry_run`, and the bad run exits 0. `cace1d9` adds `run_purge_queue(dry_run=)`, which reuses its hold handling. A cli-only preview loop would crash on a held source (reproduced). Tests: `test_cli.py::test_purge_queue_dry_run_previews_and_writes_nothing`, `test_governance.py::test_purge_queue_dry_run_writes_nothing_and_keeps_the_queue`. **What the field run erased is unknown** (N1). |
| 14 | **Works** | `model.py:427-434` docstring (`agentsync.teams-month/1`); 9 `-k teams` tests pass. The contract exists only as a docstring (folded into N9). |
| 15 | **New** for agentsync's own reads (N8); the rest is not agentsync | `--list-folders` already raises the terminal's prompt with a 90 s timeout and exits 4 (`fb9305f`, `install.sh:952-1000`). setup-report runs every probe on a timed thread (`3055724`). The Containers and Automation hangs were the exporters' reads. After K11b there is no launcher and no timed TCC probe, which makes N8 more urgent. |
| 16 | **Operator decision** (D1) | Compaction is not new in W1: every scheduled reconcile already squashed history older than `history_days` (30) plus slack (`cycle.py:~875`, `governance.compaction_due`). W1 K12 only keeps it running when background sync is off. |
| 17 | **Works**; whether to ship exporters is an **operator decision** (D2) | The writer contract is undocumented (N14). |
| 18 | **New** (N12); "visible versus missing" is **not possible here** | `.DS_Store` is excluded by default, but a hand-written `exclude` *replaces* `DEFAULT_EXCLUDES` (`config.py:431`), and agentsync's own WAITING line tells people to write one. `Icon\r` is never excluded in either arm: `paths.py:228` strips the `\r`. agentsync has no retention or join-date data for exported chat. |
| 19 | **New** (N15) | Field bodies were cp1252 on the exporter side. The `.eml` converter also loses cp1252 that is mislabelled or has no charset: `convert/eml.py:45-59` decodes with `errors="replace"`, while `_common._decode_text` (`_common.py:98-121`) already tries utf-8 then cp1252. Reproduced. |
| 20 | **Works** as designed | `.loop` gets a "no converter" REFUSED stub before any download (`cycle.py:1634-1636`). The content needs Graph, which is not possible here. |
| 21 | **Not agentsync** (`src/` never touches Containers, LevelDB or a browser; EINTR is retried by Python, PEP 475); one prompt line is **new** (N16) | The exporters' reads raised clicks, and every click counts against "fully one command". |

## 3. New items: smallest fix and where it belongs

Ownership: every file below is the KISS lead's. This doc proposes and does not edit.

| Id | Fix | File | Proof | Wave fit |
|---|---|---|---|---|
| N1 | Code fix done (`cace1d9`, landed). Optional: a hold-is-skipped regression test. Ask the operator what the field run's two queued purges were (a renamed or moved export counts as an upstream delete, `cycle.py:1555-1563`) | `src/agentsync/cli.py`, `governance.py` | the two tests in `cace1d9` | before W5 |
| N2 | **Redaction:** redact every path component after a configured source root or source id in embedded log lines (placeholder `<path-N>`). Have `residue()` flag any unredacted name that follows a source id. Reproduced on `97821c0`: a WARNING line kept a nested folder and file name, and an inbox `.eml` file name was never flagged | `src/agentsync/setup_report.py` (`_recent_errors`, `Redactor`) | new `tests/test_setup_report.py` case: nested folder, file and inbox names come out as placeholders | W5 K16b |
| N3 | **K05 defect:** `ensure_inbox` returns before creating the inbox whenever any `kind = "inbox"` source exists (`aba957e` `config.py:627-628`). Check that the canonical `~/agent-context/inbox` path is configured, or have v7 and the README say "the inbox folder(s) in `sources.toml`" rather than a fixed path | `src/agentsync/config.py` on `kiss-w4` | `tests/test_config.py`: with one non-default inbox source, the default inbox is still created and appended, and running it twice adds nothing | W4 K05, before merge |
| N4 | When the only incomplete items are withheld inbox files, an interactive sync waits until the youngest settles (at most `quiescence_s`) and scans the inbox once more. No flag, no `quiescence_s = 0` advice. The `inbox_source_table` comment (`config.py:615`) says "write to a temp name, then rename; expect about a 60 s delay" | `src/agentsync/cycle.py` (or `loop.py`), `config.py:615` | new `tests/test_arm_local.py` / `test_cycle` case: an atomic re-export converts within one interactive sync | W4 (sync surface) or a follow-up |
| N5 | Replace "the mail arm's second phase" with "attachments are listed, not converted; save one into the inbox to convert it", in both files. Bump `_EMITTER_VERSION` | `src/agentsync/convert/eml.py:115-117, :176`; `docs/design/CONTRACTS.md:2355` | `tests/test_convert_formats.py::test_eml_fixture_headers_body_attachments_no_base64` asserts no "second phase" | any |
| N6 | In `_builtin_secret_scan` (`lints.py:405-418`), strip only the `[?&]pwd=<value>` parameter inside a host-anchored Teams join URL (`teams.microsoft.com`, `.us`, `gov.`/`dod.`, `teams.live.com`, `teams.cloud.microsoft`) before `generic-password` runs | `src/agentsync/lints.py` | `tests/test_lints.py::test_builtin_secret_scan_ignores_teams_join_pwd`: a Teams pwd passes; a lookalike host, a Zoom `pwd=` and `password=` still flag | any |
| N7 | Page TOKEN check requires a token-shaped value: `\bBearer\s+[A-Za-z0-9._~+/=-]{20,}` plus `(?:\$?(?:delta|skip)|access_|refresh_|id_)?token=[^\s&"'<>]{16,}`. Keep page hits non-blocking and give the advice an action that exists. Do not switch pages to `PIPELINE_TOKEN_PATTERN`: that drops opaque bearer tokens, and install.sh ships no gitleaks | `src/agentsync/lints.py:35, :385, :393` | `tests/test_lints.py`: "Bearer Securities" gives no finding; an opaque bearer token and `access_token=` still do; update `test_lints.py:343` and `test_review_fixes.py:1071` | any; affects W3's STATE.md text |
| N8 | (a) `ECANCELED` (89): retry like `ETIMEDOUT`, then end on the existing OS-refused path (`Verdict.DEFERRED` / `HYDRATION_REFUSED`, `cycle.py:1666-1673`) so the person gets the "macOS refused … Download Now" wait. (b) Time-limit agentsync's own cloud reads (doctor `_check_local_source` `doctor.py:526`, the sync walk `arm_local.py:337-339`) on the timed-thread seam setup-report already uses (`setup_report.py:1385-1407`). Expiry ends in a "click Allow, then re-run" FAIL that **blocks** (not K02's launcher-TCC exemption), recorded like EPERM, never as an empty folder | `src/agentsync/materialise.py`; `src/agentsync/ops/doctor.py`; `src/agentsync/arm_local.py` | `tests/test_materialise.py::test_ecanceled_retries_then_defers`; `tests/test_ops_doctor.py::test_cloud_source_listing_times_out_with_click_allow_hint`; a walk-timeout test asserting no tombstones | before W5 (v7 step 3 runs the walk) |
| N9 | Say what goes in the inbox: Outlook mail dragged out as `.eml`, a meeting transcript as `.docx` or `.vtt`, a `.teams.json` in the `agentsync.teams-month/1` shape. Prefer `.eml`/`.docx`, because only OOXML, `.pdf` and `.eml` carry a sensitivity label (`cycle.py:118`, `policy.py:9-11`); pasted text, `.vtt` and `.teams.json` bypass `exclude_label_*`. Files must stay in the inbox: removing one tombstones its page and queues a purge | `README.md` and `docs/deploy/README.md:72-76` | `tests/test_deploy_pack.py`: the inbox line names a Teams route and the label caveat | W5 K04 |
| N10 | Correct "meeting recordings and transcripts stored in OneDrive" to match the design: recordings (`.mp4`) are not converted; a transcript arrives only when the person downloads it | `docs/plans/implementation.md:51-53` | — (doc) | any |
| N11 | Optional: the `graph/auth.py:320-323` error starts "Graph not configured ([graph] client_id unset): local sources work without IT". Exit 78 is kept | `src/agentsync/graph/auth.py` | `tests/test_cli.py::test_unknown_source_and_graph_without_client_id_exit_78` asserts the text (K13b edits that test anyway) | W4 |
| N12 | (a) `paths.py:228` strips only spaces, tabs and `\n`, so `Icon\r` matches. (b) `LocalArm._exclude` (`arm_local.py:664-665`) always adds the OS-junk set, as `InboxArm` does (`:844-846`), so a user `exclude` adds to the defaults instead of replacing them | `src/agentsync/paths.py`, `src/agentsync/arm_local.py` | `tests/test_arm_local.py::test_custom_exclude_still_drops_os_junk`: `Icon\r` and `sub/.DS_Store` are excluded under the defaults and under a custom `exclude` | before W5 tells anyone to write an exclude |
| N13 | With no live Graph source, the PAC `policy_error` and PAC/WPAD lines are INFO with a `note=` and no `fix=` (`format_results` prints a fix at any severity, `doctor.py:1302-1305`). They stay ERROR when Graph is live | `src/agentsync/cli.py:1010-1040` | `tests/test_review_fixes.py::test_pac_proxy_is_info_without_a_live_graph_source` | W4 (K15 removes `[network]` from the template, which the current fix text names) |
| N14 | Writer contract for the inbox: temp name then rename; stable names (identity is `volume_uuid:inode`, and a same-path new inode pairs as a safe-save, `classifier.py:360-391`, so dated names split one unit into a tombstone plus a new page); one file per unit; files are never removed or rotated (the inbox is a mirror, not a queue); expect about a 60 s delay; never `quiescence_s = 0` in a shared inbox | `docs/design/CONTRACTS.md` §11 (near :473) | `tests/test_contracts.py::test_inbox_writer_contract_matches_the_arm` (assert a subset of `_INBOX_IGNORES`; it has 10 entries) | any; W3 owns §14 only |
| N15 | `convert/eml.py:45-59`: decode text parts with strict utf-8, then cp1252, through `_common._decode_text`. Bump `_EMITTER_VERSION` | `src/agentsync/convert/eml.py` | `tests/test_convert_formats.py`: a cp1252 body that is mislabelled or has no charset keeps its curly quotes and "é" | any |
| N16 | One prompt line: "agentsync never needs `~/Library/Containers`, Group Containers or your browser; do not read or drive them" | `README.md` setup prompt v7 | `tests/test_deploy_pack.py` asserts the line | W5 K04 |

Also found, not corporate-specific: `tests/test_loop.py:138` uses `add-source --inbox`. `aba957e` already edits it,
so nothing remains.

## Open operator decisions

These are recorded, not decided. Each carries the facts measured during triage.

**D1 — Retention of chat-derived history.** With `archive` off (the default), chat that a later export dropped
can be lost in two ways:

| Path | When | Mechanism |
|---|---|---|
| automatic compaction | about 30 days plus slack after the dropping export | history older than `history_days` (30) is squashed, by every scheduled reconcile before W1 and by a due interactive sync since W1 K12 (`abc8c32`) |
| purge | as soon as the person follows agentsync's own wait line | a renamed or deleted export counts as an upstream delete (`cycle.py:1555-1563`), so with `purge_on_upstream_delete = true` (default, `governance.py:165`) a purge is queued. `loop.py:243-244` then prints `run ~/.local/bin/agentsync purge --queue`, which erases the content from every commit |

Options:
- (a) Document `archive = true` for an inbox that carries chat exports, in the K15 template line and prompt v7. It
  stops both paths: `governance.py:235-236` forces `purge_on_upstream_delete = false`, and `cycle.py:878-879`
  skips compaction. But it keeps exactly what the tenant's retention deletes, which can outlast company policy
  (README.md:126-128).
- (b) Keep the default and say nothing. The field report's rescue stops working, immediately for renamed exports.
- (c) Raise the default `history_days`. That affects every source and does nothing about the purge path.

Ruled out:
- a per-source exemption: a new `[[source]]` key, which K15 and the freeze pins forbid;
- `agentsync hold`: a legal or records hold, which leaves status permanently in ERROR (`cli.py:1134-1139`).

No conviction is stated: the compliance trade-off is the operator's.

**D2 — Ship no-IT exporters as optional arms?**
- The three exporters: Outlook AppleScript/JXA to `.eml`, a reader for Teams' IndexedDB cache, and Stream
  transcripts pulled through the person's signed-in Chrome.
- The other route: agentsync documents the writer contract (N14), and the person's own scripts feed the inbox.
- Against shipping:
  - All three add the sources, commands, keys and privacy prompts that KISS removes.
  - The last two read another app's private store or drive the person's browser session, past a consent gate
    the tenant enforces.
  - The Teams cache keeps only 30 days, so its exports shrink, which feeds both D1 loss paths.
- Recorded conviction: 80% for "document the contract, ship no exporters". That is the verifier's number; the
  call is the operator's.

**D3 — Copilot recaps from the Teams cache.** This is the same policy question as D2, narrowed to recaps. The
Graph `aiInsights` route is not possible on this Mac.

## What W5 must carry from this report

- **K16b (setup report):** N2's redaction fix. A computed Loop line alone does not make the report safe to post.
- **K04 (prompt v7):**
  - name the inbox by its configured folder(s), not a fixed path (N3);
  - say what to drop there and what carries a label (N9);
  - say the inbox is never emptied by hand (N14);
  - say agentsync never needs Containers or the browser (N16);
  - add D1's line if the operator picks (a);
  - step 3 runs the untimed sync walk, so it should follow N8, or tell the agent that a sync that hangs means
    a macOS Allow prompt is waiting.
- **K02 / K11b:** a terminal-side TCC_PENDING (N8b) blocks; only the launcher's TCC_PENDING goes ahead.
- **Sequencing:** N1 has landed (`cace1d9`); land N12 before any NEXT, WAITING or prompt line tells someone to write an
  `exclude`.

## Still wanted from the operator

To compute the outcome and close the version gap, the operator should copy back from the corporate Mac:
- `~/agent-context/setup-report.md`, or at least `~/agent-context/setup/install.log` and `friction.md`;
- `git -C ~/src/agent-context-sync rev-parse HEAD`;
- `agentsync-fix-request.md`, to map F1–F5, F7 and F10–F12.
- What the field run's two queued purges erased (N1; they rewrote 14 mirror commits). Copy back
  `~/Library/Application Support/agentsync/governance/audit.jsonl`: its `purge-enqueued` lines give each purge's
  `reason` (from a sync: `upstream-deleted` or `label-escalation`) and `source_id`, and its `purge` lines a
  `path_sha256` per removed file (no names are stored; hash candidate source-relative paths to match them).
  Then say whether an export was renamed or moved before that run, which counts as an upstream delete
  (`cycle.py:1595-1603`, `purge_on_upstream_delete`), and whether the originals still exist upstream.
