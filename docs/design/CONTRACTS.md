---
status: contract-v1
---

# agentsync — interface contract (W1a)

This file is THE contract every W1b implementer builds against. The design is
[`agent-context-sync.md`](agent-context-sync.md); the plan and week-0 defaults are
[`../plans/implementation.md`](../plans/implementation.md). Where this file adapts the design, §14 says so and why.
The stubs under `src/agentsync/` carry exactly the signatures listed in §15 with `raise NotImplementedError` bodies;
§15 is generated from them. `tests/test_contracts.py` fails if a public name in a module is missing here.

## 1. Rules of engagement

- **Own your files only.** Each module below names one owner role. Edit only your files and your own
  `tests/test_<module>*.py`. Never edit another role's module, `model.py`, `config.py`, `paths.py`, `errors.py`,
  `frontmatter.py`, `conftest.py` or `pyproject.toml`; if the contract is wrong or insufficient, implement the
  closest correct thing inside your files, and report the gap in your return (the lead amends this file).
- **Keep every public signature** in §15 exactly (names, parameter names, keyword-only markers, return types).
  You may add private helpers (`_name`) and extra public helpers, never remove or rename a listed one.
- **Conventions:** Python ≥ 3.11; full type hints, `uv run mypy src` (strict) clean; `uv run ruff check src tests`
  clean (line length 110, rules in `pyproject.toml`, including one-line docstrings on every public function);
  `pathlib`; `logging.getLogger(__name__)`, never `print` (only `cli.py` prints); `@dataclass(frozen=True,
  slots=True)` for value types; no module-level mutable state; deterministic output (sorted keys, stable ordering,
  no wall-clock value inside anything under `docs/mirror/`).
- **No git mutation of the agentsync source repo.** Tests create their own repos under `tmp_path`.
- **File Provider safety.** Never write, delete or evict anything under `~/Library/CloudStorage/` except inside
  `~/Library/CloudStorage/OneDrive-Contoso/agentsync-e2e/`, only from tests marked `@pytest.mark.fileprovider`
  using the `fileprovider_e2e_dir` fixture (opt-in: `AGENTSYNC_E2E_FILEPROVIDER=1`).
- **Secrets.** Tokens, delta links and cursor values are never logged, printed, or written under `docs/`.
  STATE.md shows a cursor's age and `manifest.cursor_fingerprint` (12 hex), never the cursor.

## 2. Ownership

| Role | Files | Tests |
|---|---|---|
| architect (done) | `model.py`, `errors.py`, `paths.py`, `config.py`, `frontmatter.py`, `pyproject.toml`, `tests/conftest.py`, `tests/fixtures/` | `test_model.py`, `test_paths.py`, `test_config.py`, `test_frontmatter.py`, `test_fixtures.py`, `test_contracts.py` |
| manifest | `manifest.py`, `classifier.py` | `test_manifest*.py`, `test_classifier*.py` |
| local | `arm_local.py`, `materialise.py` | `test_arm_local*.py`, `test_materialise*.py` |
| convert | `convert/**` | `test_convert*.py` |
| publish | `publish.py`, `slug.py`, `lints.py`, `gitops.py` | `test_publish*.py`, `test_slug*.py`, `test_lints*.py`, `test_gitops*.py` |
| graph-core | `graph/auth.py`, `graph/client.py` | `test_graph_auth*.py`, `test_graph_client*.py` |
| graph-arms | `graph/drive.py`, `graph/mail.py`, `graph/teams.py` | `test_graph_drive*.py`, `test_graph_mail*.py`, `test_graph_teams*.py` |
| curate | `curate.py` | `test_curate*.py` |
| ops | `ops/lock.py`, `ops/launchd.py`, `ops/doctor.py` | `test_ops_*.py` |
| integrator (W2) | `cycle.py`, `cli.py`, `__main__.py` | `test_cycle*.py`, `test_cli*.py`, `test_e2e*.py` |

Dependency direction (no cycles): `errors` ← `model` ← `paths` ← `config` ← `frontmatter` ← {`manifest`,
`convert`, `slug`} ← {`classifier`, `arm_local`/`materialise`, `graph/*`, `publish`, `lints`, `gitops`,
`curate`, `ops/*`} ← `cycle` ← `cli`. W1b modules may import each other's *types and constants* per §15, but must
not call another W1b module's stubbed functions in their own unit tests (they raise until merged); use fakes.

## 3. Identity, paths and time

- **Identity is `(source_id, stable_id)`, never a path.** `source_id` is the configured `[[source]] id`
  (`^[a-z0-9][a-z0-9-]{0,62}$`). `stable_id`: local/inbox = `f"{volume_uuid}:{inode}"` (`arm_local.stable_id_for`;
  volume UUID from `getattrlist(ATTR_VOL_UUID)`, never `st_dev`); graph drive = driveItem `id`; mail = message
  `id`; Teams = `f"{channel_id}:{YYYY-MM}"`. An Office safe-save (new inode at the same path, old inode gone in
  the same FULL pass) is continuity, not delete+create: `classifier.match_safe_saves` → `Manifest.rekey`.
- **`rel_path`** is POSIX, NFC-normalised, relative to the source root; for Graph drives it is derived from the
  id→(parent, name) tree (`Manifest.tree_lookup`, `Manifest.rederive_paths`), never from `parentReference.path`.
- **Docs paths** are docs-repo-relative POSIX strings (`mirror/<source_id>/…`, `topics/…`), produced only by
  `slug.mirror_rel_path` / `Publisher.allocate_path`. Every path ≤ 200 chars; collisions (NFC + casefold) are
  disambiguated with `-<sha256(stable_id)[:8]>`, and an existing owner keeps its path (sticky, via `outputs`).
- **Mirror names:** WHOLE unit `mirror/<sid>/<slug dirs>/<slug(name)>.md` where `name` keeps its extension
  (`fy26-budget.xlsx` → `fy26-budget.xlsx.md`; suffixes `.md .markdown .teams.json .eml` are dropped);
  multi-unit `…/<slug(name)>.d/<slug(file_stem)>.md` (`fy26-budget.xlsx.d/00-index.md`, `01-q3-budget.md`).
- **Time:** all stored times are UTC; nanosecond ints for item stat times, ISO-8601 `…Z` strings for run/cursor
  metadata, `YYYY-MM-DD` for tombstone dates. mtime is compared for inequality only. Wall-clock never enters a
  mirror page except a tombstone's `deleted_at` date, which is recorded once in `tombstones` and re-rendered from
  there (so re-rendering is byte-identical).

## 4. Hashes

| Name | Definition | Owner | Decides |
|---|---|---|---|
| provider hash | Graph `file.hashes.quickXorHash` (also `sha1Hash`/`sha256Hash` if present) → `RemoteHashes` | graph-arms | phase 1, comparable only to itself |
| H0 | stat tuple `(size, mtime_ns, ctime_ns, ino, mode)` + `(ino, gen_count)`; never `st_dev` | local | phase 1 |
| `content_sha256` | sha256 of the exact staged bytes (`FetchResult.content_sha256`) | local / graph-arms | identity/dedup of bytes |
| H1 `canonical_sha256` | `convert.canonical.canonical_hash` (OOXML part rollup minus volatile parts/attrs; else bytes) | convert | phase 2 (TOUCHED_NOT_CHANGED vs CHANGED) |
| H2 `rendered_sha256` | sha256 of a unit's markdown **body** (UTF-8, below the frontmatter) | convert | phase 3 early cutoff; what curated pages pin |
| `page_sha256` | sha256 of the whole written file (frontmatter + body) | publish | skip identical writes |
| `action_key` | sha256 of `KEY_SCHEMA_VERSION\|converter_id\|converter_version\|options_hash\|unit_id\|canonical_sha256` | convert | converter cache key |
| `options_hash` | `"sha256:" + sha256(json.dumps(options, sort_keys=True, separators=(",", ":")))` | convert | part of the action key |

All hashes are lowercase hex sha256 (64 chars) unless named otherwise.

## 5. SQLite manifest (`<state_dir>/manifest.sqlite`, mode 0600)

Opened with `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`. `meta` keys: `manifest_schema_version`
(= `MANIFEST_SCHEMA_VERSION`), `key_schema_version` (= `convert.cache.KEY_SCHEMA_VERSION`), `tree_sha`,
`written_at_ns`. A version mismatch raises `ManifestSchemaError` (never silently re-derived). The `cursors` table
is secret: it is never exported, and the DB lives outside `docs/` on the state volume.

```sql
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,           -- manifest_schema_version | key_schema_version | tree_sha | written_at_ns
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  source_id            TEXT PRIMARY KEY,
  kind                 TEXT NOT NULL,                  -- SourceKind value; immutable for an id
  config_state         TEXT NOT NULL,                  -- SourceState value as last configured
  config_fingerprint   TEXT NOT NULL,                  -- sha256 of scope keys (path/drive/folder/...)
  enumeration_complete INTEGER NOT NULL DEFAULT 0,     -- set only by a finished FULL pass of the scope
  baseline_complete    INTEGER NOT NULL DEFAULT 0,     -- first full enumeration done (bootstrap over)
  last_full_run_id     INTEGER,
  last_success_run_id  INTEGER,
  breaker_tripped_at   TEXT,                           -- UTC ISO-8601, NULL = not tripped
  breaker_until        TEXT,                           -- UTC ISO-8601
  breaker_candidates   INTEGER,
  auth_state           TEXT NOT NULL DEFAULT 'ok'      -- ok | REAUTH_REQUIRED
);

CREATE TABLE IF NOT EXISTS items (
  source_id          TEXT NOT NULL REFERENCES sources(source_id),
  stable_id          TEXT NOT NULL,                    -- NEVER the path
  parent_id          TEXT,
  name               TEXT NOT NULL,
  rel_path           TEXT NOT NULL,                    -- derived, NFC, POSIX, relative to the source root
  prev_path          TEXT,                             -- previous rel_path when the last change was a rename
  is_dir             INTEGER NOT NULL DEFAULT 0,
  size               INTEGER,
  mtime_ns           INTEGER,
  ctime_ns           INTEGER,
  created_ns         INTEGER,
  ino                INTEGER,
  mode               INTEGER,
  gen_count          INTEGER,
  dataless           INTEGER NOT NULL DEFAULT 0,
  quickxor           TEXT,                             -- provider hashes: comparable only to themselves
  sha1_remote        TEXT,
  sha256_remote      TEXT,
  etag               TEXT,
  ctag               TEXT,
  content_type       TEXT,
  content_sha256     TEXT,                             -- sha256 of fetched bytes; NULL = never materialised
  canonical_sha256   TEXT,                             -- H1
  canonical_method   TEXT,
  canonical_parts    TEXT,                             -- JSON [[part, sha256], ...] sorted, OOXML only
  state              TEXT NOT NULL CHECK (state IN ('live','dataless','tombstone','quarantined','refused')),
  state_reason       TEXT,
  last_verdict       TEXT,                             -- Verdict value; DEFERRED/ERROR rows are pending work
  first_seen_run     INTEGER NOT NULL,
  last_seen_run      INTEGER NOT NULL,
  principal          TEXT,
  sensitivity_label  TEXT,
  extra_json         TEXT NOT NULL DEFAULT '{}',       -- json.dumps(extra, sort_keys=True)
  PRIMARY KEY (source_id, stable_id)
);
CREATE INDEX IF NOT EXISTS items_by_path ON items(source_id, rel_path);
CREATE INDEX IF NOT EXISTS items_by_parent ON items(source_id, parent_id);
CREATE INDEX IF NOT EXISTS items_by_canonical ON items(canonical_sha256);
CREATE INDEX IF NOT EXISTS items_by_verdict ON items(source_id, last_verdict);

CREATE TABLE IF NOT EXISTS cursors (                   -- SECRET: never exported, never in docs/
  source_id       TEXT PRIMARY KEY REFERENCES sources(source_id),
  current         TEXT,                                -- last cursor whose change set is committed in git
  current_set_at  TEXT,                                -- UTC ISO-8601
  pending         TEXT,                                -- cursor from the running cycle; promoted after commit
  pending_run_id  INTEGER,
  page_link       TEXT                                 -- @odata.nextLink of an interrupted enumeration
);

CREATE TABLE IF NOT EXISTS cache (                     -- index of the on-disk write-once converter cache
  action_key        TEXT PRIMARY KEY,
  converter_id      TEXT NOT NULL,
  converter_version TEXT NOT NULL,
  options_hash      TEXT NOT NULL,
  canonical_sha256  TEXT NOT NULL,
  status            TEXT NOT NULL,                     -- ConversionStatus value
  unit_count        INTEGER NOT NULL,
  bytes             INTEGER NOT NULL,
  created_run       INTEGER NOT NULL,
  last_used_run     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS outputs (
  output_path       TEXT PRIMARY KEY,                  -- docs-repo-relative, e.g. mirror/<source>/a/b.docx.md
  source_id         TEXT NOT NULL,
  stable_id         TEXT NOT NULL,
  unit_id           TEXT NOT NULL,                     -- whole | index | sheet:<n> | summary
  action_key        TEXT,
  rendered_sha256   TEXT,                              -- H2 (body only)
  page_sha256       TEXT,                              -- sha256 of the whole file (frontmatter + body)
  converter_id      TEXT,
  converter_version TEXT,
  options_hash      TEXT,
  status            TEXT NOT NULL
                    CHECK (status IN ('ok','failed','skipped-dataless','quarantined','refused','tombstone')),
  built_run         INTEGER NOT NULL,
  UNIQUE (source_id, stable_id, unit_id)
);
CREATE INDEX IF NOT EXISTS outputs_by_item ON outputs(source_id, stable_id);
CREATE INDEX IF NOT EXISTS outputs_by_key ON outputs(action_key);

CREATE TABLE IF NOT EXISTS tombstones (
  output_path          TEXT PRIMARY KEY,
  source_id            TEXT NOT NULL,
  stable_id            TEXT NOT NULL,
  unit_id              TEXT NOT NULL,
  deleted_at           TEXT NOT NULL,                  -- UTC date YYYY-MM-DD of the tombstoning run
  deleted_run          INTEGER NOT NULL,
  last_rendered_sha256 TEXT,
  last_commit          TEXT,                           -- git sha whose tree holds the last live content
  reap_after           TEXT NOT NULL,                  -- UTC date; reaped (file removed) on/after this
  reason               TEXT NOT NULL                   -- deleted-upstream|unit-removed|moved|retired:<why>
);

CREATE TABLE IF NOT EXISTS runs (
  run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  mode         TEXT NOT NULL,                          -- CycleMode value
  started_at   TEXT NOT NULL,                          -- UTC ISO-8601 (run metadata, never in docs content)
  finished_at  TEXT,
  status       TEXT NOT NULL DEFAULT 'running',        -- running | ok | partial | failed | aborted
  commit_sha   TEXT,
  host         TEXT NOT NULL,
  pid          INTEGER NOT NULL,
  counts_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_sources (
  run_id               INTEGER NOT NULL REFERENCES runs(run_id),
  source_id            TEXT NOT NULL,
  pass_kind            TEXT,                           -- PassKind value; NULL = skipped
  enumeration_complete INTEGER NOT NULL DEFAULT 0,
  cursor_reset         INTEGER NOT NULL DEFAULT 0,
  counts_json          TEXT NOT NULL DEFAULT '{}',
  skipped_reason       TEXT,
  error                TEXT,
  PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS depends (                   -- mirror of docs/DEPENDS.tsv for reverse lookups
  page       TEXT NOT NULL,                            -- docs-repo-relative topics/... path
  source     TEXT NOT NULL,                            -- docs-repo-relative mirror/... path
  pinned_sha TEXT NOT NULL,
  role       TEXT NOT NULL,
  PRIMARY KEY (page, source)
);
CREATE INDEX IF NOT EXISTS depends_by_source ON depends(source);
```

Semantics the classifier and cycle rely on:

- **Pending work.** A row whose `last_verdict` ∈ {created, maybe_changed, changed, deferred, error} has not been
  published. `Manifest.pending_work` returns them and the cycle processes them before new observations, so a delta
  cursor may advance while a byte budget defers work (the work is durable in the manifest, not in the cursor).
- **Deletion.** `unseen_live` rows are deletion candidates only after a complete FULL pass
  (`enumeration_complete[source]`), then subject to the per-source breaker
  `candidates > max(fraction × live_rows, floor)` (defaults 0.20 / 25; hold 7 days). DELTA passes delete only on an
  explicit provider tombstone (`SourceItem.deleted`). A retired source is tombstoned in one labelled commit exempt
  from the breaker.
- **Cursors.** `stage_cursor` writes `pending` in the same transaction as the item snapshot; `promote_cursors`
  runs only after the git commit; `discard_pending` on a failed run; `drop_cursor` on a 400 bad cursor or scope
  change. `page_link` persists an interrupted enumeration's `@odata.nextLink`.

### 5.1 NDJSON manifest shards (committed)

`docs/_manifest/<source_id>.jsonl`, written by `Publisher.write_manifest_shards` from `Manifest.export_shard`:
one line per non-directory item in state live/dataless/quarantined/refused/tombstone, sorted by `stable_id`,
`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, LF, trailing newline. Fields:
`{"outputs": [{"path", "rendered_sha256", "unit_id"}…sorted by unit_id], "rel_path", "source_id", "stable_id",
"state", "state_reason"}`. Nothing volatile (no H0, no run ids, no content/canonical hashes, no cursor), so a
no-op cycle produces no shard diff and therefore no commit.

## 6. Mirror page contract (`frontmatter.py`, implemented)

A mirror page is `render_mirror_page(MirrorFrontmatter, body)`: a `---` fenced block with keys **only** from
`MIRROR_KEY_ORDER`, **in that order**, `None` omitted, then the body (NFC, LF, one trailing newline).
Hashes, enum values and ids render bare; free text renders JSON-quoted; `part` is one flow mapping. This keeps the
design §4.5 refresh-queue awk (`^rendered_sha256: `, `^status: `) correct — `tests/test_frontmatter.py` runs the
real script against rendered pages.

```
source_kind, source_id, stable_id, source_path, source_web_url, source_etag, source_version,
content_sha256, canonical_sha256, rendered_sha256, part {kind, name, index, of}, unit_index,
converter ("<converter_id>@<converter_version>"), options_hash ("sha256:<hex>"), sensitivity_label,
status (current|deleted|superseded|unreadable|refused), reason, superseded_by, deleted_at, last_rendered_sha256,
last_commit, source_title, summary, tokens_estimate
```

Required: always `source_kind, source_id, stable_id, source_path, status`; `current`/`superseded` add
`content_sha256, canonical_sha256, rendered_sha256, converter, options_hash, summary, tokens_estimate`; `deleted`
adds `deleted_at, last_rendered_sha256`; `unreadable`/`refused` add `reason`. `validate_mirror_frontmatter`
returns every violation; `lints.lint_mirror_frontmatter` blocks the commit on any. No `converted_at`, run id, mtime
or model output ever appears. `source_etag`/`source_version` are Graph-only (local stat values would churn pages).

Stub pages: UNREADABLE/REFUSED/FAILED conversions produce one page `status: unreadable|refused` with `reason`
(`encrypted`, `password-protected`, `no converter for .xyz`, `conversion failed: …`, `not materialised: budget`,
`contains a credential`). Tombstones: body replaced by `# [DELETED UPSTREAM] <title>` plus the literal
`git show <last_commit>:<path>` and `git log -S'<term>' -- <path>` lines (design §4.6); reaped after
`tombstone_reap_days`.

## 7. Conversion

`convert.convert_file` is the only entry point the cycle uses. Routing by lower-case suffix
(`Registry.default`): `.docx .odt .rtf .html .htm` → `pandoc-gfm` (the **pypandoc_binary bundled pandoc by absolute
path**, measured 3.9 here; `[convert] pandoc_path` overrides); `.xlsx .xlsm` → `xlsx-openpyxl` (index + per-sheet
units, streaming summary above 20 MB); `.pptx` → `pptx-python-pptx`; `.pdf` → `pdf-pdfminer` (page anchors;
PyMuPDF is not used — AGPL); `.md .markdown` → `markdown-passthrough`; `.txt .csv .tsv .log .vtt .json .xml .yaml
.yml` → `text-plain`; `.eml` → `eml-stdlib`; `.teams.json` → `teams-month`. Anything else → REFUSED stub.
The cache is write-once under `Config.cache_dir` (`~/Library/Caches/agentsync`), never in git; OK and UNREADABLE
results are cached, FAILED/REFUSED are not. `Converter.version()` is the version **as run**.
`double_conversion_differs` backs the NONDETERMINISTIC land-gate lint.

## 8. Classifier (design §4.3)

Phase 1 (zero bytes, `classify_observed`, rung order in its docstring) → `CREATED | UNCHANGED | MAYBE_CHANGED |
METADATA_ONLY | DATALESS | DELETED`, rename reported as `prev_path`; per pass, `classify_pass` adds safe-save
pairs, `DELETION_CANDIDATE`s and the breaker. Phase 2 (after fetch + H1, `classify_content`) →
`TOUCHED_NOT_CHANGED | CHANGED` with `changed_parts` logged. Phase 3 (after convert, `classify_output`) →
`OUTPUT_UNCHANGED | CHANGED`. Plus row outcomes `DEFERRED` (budget), `QUARANTINED`, `REFUSED`, `ERROR`.
Actions: UNCHANGED → stamp only; METADATA_ONLY or rename-only → `Publisher.rewrite_frontmatter` (bodies kept);
DATALESS with no content yet, CREATED, MAYBE_CHANGED → fetch within budget (else DEFERRED); TOUCHED_NOT_CHANGED
→ update H0/content hash, no convert; CHANGED → convert → OUTPUT_UNCHANGED (frontmatter-only rewrite if metadata
moved) or write pages; DELETED / applied candidates → `Publisher.tombstone`; reappearance of a tombstoned id →
`Publisher.restore` + rewrite.

## 9. The cycle (`cycle.run_cycle`, integrator) — durability order

1. `materialise.fail_closed()` (process materialisation policy OFF, inherited by every child process).
2. `SingleWriterLock(state_paths.lock, label).acquire()` — `LockHeldError` → CLI exit 75 "skipped: lock held";
   `broke_stale=True` forces FULL passes this cycle.
3. `Manifest(state_paths.db)`; `recover()`: `tree_sha ≠ HEAD^{tree}` → `gitops.restore_generated` + re-publish
   from manifest + cache; committed-but-unpromoted pending cursors → promote; otherwise discard pending.
4. `manifest.sync_sources(config.sources)`; `begin_run`; clear `state_paths.staging`.
5. For each live source (errors isolated per source; Graph sources skipped with a reason when no client/network):
   `arm.scan(current_cursor, full=…)` (FULL for local/inbox always, for RECONCILE mode, when no cursor, after a
   stale lock) → `classify_pass` → in ONE transaction: `upsert_observed` for every item, `rekey` safe-saves,
   `stage_cursor(pending)`, `set_page_link(None)`, `set_enumeration_complete` for FULL passes, `record_source_pass`.
6. Work queue = `pending_work(source)` (sorted by stable_id): `arm.fetch` under `ByteBudget(max_materialise_bytes,
   max_files)` → `canonical_hash` → `set_content` → `classify_content` → `convert_file` → `classify_output` →
   `Publisher.plan_pages`/`write_pages` → `set_verdict`. Budget exhaustion → remaining rows `DEFERRED`.
7. Removals: explicit DELETED always; DELETION_CANDIDATEs only if the breaker did not trip (else
   `trip_breaker`, alarm, keep everything). `Publisher.reap(today)`.
8. Curation: `curate.generate_depends` → `write_depends`/`write_by_entity` → `manifest.replace_depends` →
   `refresh_queue` → `apply_stale_banners`.
9. Surface: `write_manifest_shards`, `write_quarantine`, `write_index`, and `append_changelog` only if changes.
10. `lints.run_land_gate(repo, changed_paths)` (+ `lint_secrets` on changed mirror pages: hits are re-published as
    `contains a credential` stubs before committing). Any blocking finding → no commit, no cursor promotion,
    `discard_pending`, exit 1.
11. If `gitops.has_changes(repo)`: `write_state_snapshot` (so it rides in the content commit), then
    `gitops.commit_cycle(repo, commit_subject(changes, sources))` → sha; `manifest.set_tree_sha(head_tree_sha)`;
    `tag_published(sha)`. No changes ⇒ no snapshot, no commit.
12. **Only now** `promote_cursors(run_id)`; `set_last_success`; `write_heartbeat` per source; `finish_run`.
13. `Publisher.write_state(report, statuses)` (gitignored STATE.md, every cycle, including failures); release lock.

`AuthRequiredError` anywhere in a Graph source: no cursor of that source advances, `set_auth_state(
"REAUTH_REQUIRED")`, STATE.md + heartbeat say so, report `auth_required=True` → CLI exit 77 after the local
sources finished. DRY_RUN: steps 1–5 without the transaction's writes, no fetch, nothing under `docs/`.

CLI exit codes (`cli.py`): 0 ok · 1 failed (source error, blocking lint, refresh-queue rows) · 2 usage ·
75 lock held · 77 reauth required · 78 config invalid.

## 10. Microsoft Graph

**Auth (`graph/auth.py`).** MSAL `PublicClientApplication(client_id, authority=https://login.microsoftonline.com/
<tenant>)`, device-code flow only (`login_device_code(emit)`); token cache `msal_extensions.PersistedTokenCache`
over `KeychainPersistence(state_paths.keychain_marker, "agentsync", "msal_token_cache")`; if the Keychain is
unusable, `FilePersistence(state_paths.token_cache_fallback)` chmod 0600 with a WARNING. `get_token()` is silent
only; `REAUTH_ERROR_CODES` / no account → `AuthRequiredError`, never retried. Scopes: `Config.graph_scopes()`
(`Files.Read.All Sites.Read.All Mail.Read User.Read`, + `ChannelMessage.Read.All` only with a live Teams source,
+ `Mail.Read.Shared` only with a shared mailbox). AADSTS65001 (consent) surfaces as `AuthError` naming the IT action.

**Client (`graph/client.py`).** `GraphClient(tokens, base_url, user_agent="NONISV|<company>|agentsync/<ver>")`.

| Method | Contract |
|---|---|
| `get_json(path_or_url, params, headers)` | one GET; relative paths joined to `base_url`, absolute Graph URLs (nextLink/deltaLink) used verbatim |
| `iter_pages(...)` | yields `GraphPage(value, next_link, delta_link)` following `@odata.nextLink`; last page carries `@odata.deltaLink` for delta endpoints |
| `delta(...)` | drains a round to its final `@odata.deltaLink` → `DeltaResult(items, delta_link, pages)`; `on_page` sees each page (persist resume point) |
| `download(path_or_url, dest, max_bytes)` | streams to tmp + rename → `(size, sha256)`; follows 302 **without** Authorization; over `max_bytes` → `BudgetExhaustedError` |

Errors (all in `errors.py`): **`GraphGone`** (410; `.location` = resync link if given) → caller re-enumerates FULL
with `cursor_reset=True`; **`GraphBadCursor`** (400 on a URL carrying `token=`/`$deltatoken`/`$skiptoken`) →
caller alarms "cursor store corrupt", `drop_cursor`, S0 — never conflated with 410; **`GraphThrottled`**
(`.retry_after` seconds) only after the client's own retries, which honour `Retry-After` on 429/503/504 and pause
every request of that client until then; `GraphNotFound` (404); `AuthRequiredError` (401 after one silent
refresh); `GraphError` otherwise. Query strings are redacted in logs (`redact_url`). SharePoint emits no
`RateLimit-*` headers: do not build on them.

**Arms.** Drive: `$select=DRIVE_SELECT` (must include `file`), `Prefer: deltaExcludeParent`, first pass always
token-less FULL (never `token=latest`), one cursor per drive, subtree filtered locally, folders emitted as
`is_dir` items for the id tree. Mail: phase-1 `$select=MAIL_SELECT` (no bodies); phase-2 `$value` MIME →
`.eml`; `@removed` → `confirm_removed` (404 = deleted, else moved:<folder>). Teams: channel message delta +
`/replies`, merged into `<state_dir>/teams/<source_id>/<YYYY-MM>.json` (`model.TEAMS_MONTH_SCHEMA`), one item per
month. `graph/drive.discover` implements Arm 0 discovery (prints candidates; never edits sources.toml).

## 11. Local arm and hydration

`arm_local.walk` uses `os.scandir` + `lstat` only: never follows symlinks, never opens a file, emits files only,
records `SF_DATALESS` (0x40000000 in `st_flags`) as `dataless`, `gen_count` via `getattrlist(ATTR_CMN_GEN_COUNT)`
(None/0 = unknown ⇒ hash to confirm), and reports zero-child cloud directories and EPERM (TCC) directories as
`unknown_dirs` (never empty). A configured `sentinel` must be present or the pass is incomplete. `materialise`
is the only place bytes of a sync-root file are read: THREAD-scope `IOPOL_MATERIALIZE_DATALESS_FILES_ON` around a
tmp-copy, budget charged first, EDEADLK (11) → `DatalessRefusedError`, ETIMEDOUT (60) → retry with backoff then
`ProviderTimeoutError`, vanished → `FileNotFoundError`, changed mid-copy → `MaterialiseError`. Converters only
ever read the staged copy. Inbox: quiescence window, `max(created, modified)`, lock-file ignores, conflict-suffix
folding, two-read agreement; a drop whose canonical hash matches a live Graph row is quarantined
`duplicate-of <source_id>` (via `Manifest.find_by_canonical`).

## 12. docs/ layout (publish owns every generated file)

```
docs/                     its own git repo, default ~/agent-context/docs, outside CloudStorage, no remote
  INDEX.md  README.md  CLAUDE.md  CHANGELOG.md  CHANGELOG/<yyyy-mm>.md  DEPENDS.tsv  SYNONYMS.tsv
  _index/by-entity.tsv  _manifest/<source_id>.jsonl
  _sync/STATE.md (gitignored, every cycle)  _sync/STATE.snapshot.md (content commits only)  _sync/QUARANTINE.tsv
  mirror/CLAUDE.md  mirror/<source_id>/...      generated, never hand-edited
  topics/CLAUDE.md  topics/...                  curated (agent-owned)
  .gitignore (_sync/STATE.md, _manifest/cache/, .sync.lock)   .gitattributes (text=auto eol=lf, *.jsonl -merge, ...)
```

Commit subject `sync: <A>a <M>m <R>r <D>d <source ids>`; one commit per cycle that changed content, none otherwise.
The config file is `~/agent-context/sources.toml` (plan week-0 default; the design's `docs/_sync/sources.toml`
location is not used — see §14).

## 13. Curation (`curate.py`)

Curated page frontmatter (design §4.5): `kind`, `entity` (required), `purpose`, `sources: [{path,
at_rendered_sha256, role}]` (path **page-relative**, pin = full 64-hex H2 of the cited mirror page, role
`primary|corroborating`), `depends_on_pages`, `aliases`, and for adopted pages `provenance: hand-written`,
`adopted_at`, `sources: []`. `DEPENDS.tsv` = header `page<TAB>source<TAB>pinned_sha<TAB>role` + rows sorted by
(page, source), columns 1–2 **docs-repo-relative** (`topics/…`, `mirror/…`); unpinned/short pins are still emitted
so the queue reports `UNPINNED`/`BAD-PIN`. `curate.REFRESH_QUEUE_SH` is written verbatim into `docs/README.md`
and runs from the docs repo root; `refresh_queue` is its Python twin (same verdicts, same `sort -u` order, same rc).
Banner text: `> ⚠ STALE — sources changed since <date>; see DEPENDS.tsv` (added and removed only by
`apply_stale_banners`), `> ⚠ SOURCE RETIRED — <reason>`.

## 14. Decisions and adaptations taken by the architect

1. **Config location** `~/agent-context/sources.toml` (plan) instead of `docs/_sync/sources.toml` (design §4.2):
   the plan's week-0 table wins; nothing in `docs/` names a cloud location either way.
2. **Lock location** `<state_dir>/agentsync.lock` (plan: "flock on the state dir") instead of `docs/.sync.lock`.
   `docs/.gitignore` still lists `.sync.lock` harmlessly.
3. **Budget exhaustion does not clear `enumeration_complete`** (design §4.7 says it does): deferred rows are durable
   `pending_work` in the manifest, so the metadata enumeration stays complete and absence-based deletion stays
   correct; the cursor may advance because nothing is lost. STATE.md shows `deferred` counts per source.
4. **Verdict names** are the design's (§4.3): `CREATED, UNCHANGED, MAYBE_CHANGED, METADATA_ONLY, DATALESS,
   TOUCHED_NOT_CHANGED, CHANGED, DELETED, DELETION_CANDIDATE` plus `OUTPUT_UNCHANGED` (H2 early cutoff) and row
   outcomes `DEFERRED, QUARANTINED, REFUSED, ERROR`. Rename is `Classification.prev_path`, not a verdict.
5. **Local identity** is `volume_uuid:inode` per the brief; the design's `(FILEID, GEN_COUNT)` pair is kept as
   identity (`FILEID` = inode) plus change token (`gen_count`), and safe-saves are re-keyed (§3).
6. **Manifest shards** carry no hashes except output H2s (§5.1) so a no-op or touch-only cycle commits nothing.
7. **DEPENDS.tsv paths** are relative to the docs repo root, because `docs/` is its own repository here; the
   refresh-queue script's only change is its default TSV path.
8. **PDF** via pdfminer.six (plan), **pptx** via python-pptx (plan) instead of MarkItDown; `.msg` is not routed
   (no permissive parser chosen) and becomes a REFUSED stub until one is.
9. **PyYAML** was added as a runtime dependency (curated pages are agent-written YAML; a hand parser would be a
   defect source). Mirror frontmatter is still rendered by a hand-rolled deterministic writer.
10. **One launchd job per mode** (`<prefix>.poll` StartInterval = poll_interval_s, `<prefix>.reconcile`
    StartInterval = reconcile_interval_s), plist `MaterializeDatalessFiles=false`; `materialise` opts in per read.
11. **Token cache** in the login Keychain via msal-extensions; the 0600 file fallback exists only for sessions
    without Keychain access and is logged.

## 15. Module reference (generated from the stubs)

### `agentsync.model` — `src/agentsync/model.py` — owner: **architect (implemented)**

Value types shared by every agentsync module (the vocabulary of docs/design/CONTRACTS.md).

```python
TreeLookup = Callable[[str], tuple[str | None, str] | None]
"""Manifest lookup ``stable_id -> (parent_id, name)`` the Graph drive arm derives paths from (None =
unknown)."""

ExtraValue = str | int | float | bool | None
"""Allowed value type in ``SourceItem.extra`` (JSON scalars only, so it serialises deterministically)."""

class SourceKind(enum.StrEnum):
    """Which arm owns a source (design section 4.2)."""
    LOCAL = "local"  # arm B: a folder (typically a File Provider sync-client folder), T3 metadata walk
    INBOX = "inbox"  # arm C: manual drag-and-drop folder; max(created, modified), quiescence, dedup
    GRAPH_DRIVE = "graph_drive"  # arm A: OneDrive / SharePoint document library, one delta cursor per drive
    GRAPH_MAIL = "graph_mail"  # arm A: one mail folder, folder-scoped message delta
    GRAPH_TEAMS = "graph_teams"  # arm A: one Teams channel, channel message delta -> monthly rollups

    @property
    def is_graph(self) -> bool:
        """True for the three Graph-backed kinds."""

class SourceState(enum.StrEnum):
    """Operator-declared lifecycle of a configured source (design section 4.2 Arm 0, 4.7 retirement)."""
    PAUSED = "paused"
    LIVE = "live"
    RETIRED = "retired"

class PassKind(enum.StrEnum):
    """The two pass kinds the classifier must never confuse (design section 4.3)."""
    DELTA = "delta"  # incremental: deletes only from explicit tombstones
    FULL = "full_enumeration"  # bootstrap / 410 resync / reconcile / T3 walk: absence is evidence if complete

class RowState(enum.StrEnum):
    """Manifest ``items.state`` (design section 4.3): a failure is a row, never a log line."""
    LIVE = "live"
    DATALESS = "dataless"
    TOMBSTONE = "tombstone"
    QUARANTINED = "quarantined"
    REFUSED = "refused"

class Verdict(enum.StrEnum):
    """Per-object classifier verdicts, exactly the states of design section 4.3.

    Phase 1 (zero bytes) yields CREATED, DATALESS, UNCHANGED, MAYBE_CHANGED, METADATA_ONLY, DELETED,
    DELETION_CANDIDATE.  Phase 2 (after ``materialise``) turns MAYBE_CHANGED/CREATED into
    TOUCHED_NOT_CHANGED or CHANGED.  Phase 3 (after conversion) turns CHANGED into OUTPUT_UNCHANGED when every
    unit's H2 equals the recorded one (ninja ``restat`` early cutoff).  A rename is not a verdict: it is
    ``Classification.prev_path`` and classification of content continues.
    """
    CREATED = "created"  # stable id never seen before in this source
    UNCHANGED = "unchanged"  # provider hash / cTag / stat tuple equal (and not racily clean)
    MAYBE_CHANGED = "maybe_changed"  # a zero-byte signal moved; needs H1 to decide
    METADATA_ONLY = "metadata_only"  # eTag moved but size/mtime/hash hold: re-map path, rewrite frontmatter
    DATALESS = "dataless"  # SF_DATALESS placeholder: never open, never hash, never read null hash as a diff
    TOUCHED_NOT_CHANGED = "touched_not_changed"  # bytes read, canonical hash (H1) equal: update H0 only
    CHANGED = "changed"  # H1 differs: convert (through the cache)
    OUTPUT_UNCHANGED = "output_unchanged"  # converted, but every unit's H2 equals the recorded one
    DELETED = "deleted"  # explicit tombstone (Graph ``deleted`` facet, confirmed mail removal)
    DELETION_CANDIDATE = "deletion_candidate"  # absent from a COMPLETE full enumeration; subject to breaker
    DEFERRED = "deferred"  # needed bytes but the per-cycle budget was exhausted; retried next cycle
    QUARANTINED = "quarantined"  # IRM/encrypted/password/pending/checked-out/secret-in-content/duplicate
    REFUSED = "refused"  # no converter for the type, or out of scope: stub page, never silent
    ERROR = "error"  # unexpected failure on this object; recorded on the row, cycle continues

class OutputStatus(enum.StrEnum):
    """Manifest ``outputs.status`` (design section 4.3 ``output`` table)."""
    OK = "ok"
    FAILED = "failed"
    SKIPPED_DATALESS = "skipped-dataless"
    QUARANTINED = "quarantined"
    REFUSED = "refused"
    TOMBSTONE = "tombstone"

class PageStatus(enum.StrEnum):
    """Mirror page frontmatter ``status`` (design section 4.4); read verbatim by the refresh queue."""
    CURRENT = "current"
    DELETED = "deleted"
    SUPERSEDED = "superseded"
    UNREADABLE = "unreadable"
    REFUSED = "refused"

class ConversionStatus(enum.StrEnum):
    """Outcome of one conversion."""
    OK = "ok"
    FAILED = "failed"  # converter raised / produced nothing: stub page, row status failed
    UNREADABLE = "unreadable"  # encrypted / password-protected / IRM bytes: UNREADABLE stub, quarantined row
    REFUSED = "refused"  # no converter registered for the type

class UnitKind(enum.StrEnum):
    """Addressable citation unit kinds (design section 4.4, 'one file per addressable citation unit')."""
    WHOLE = "whole"
    INDEX = "index"  # e.g. <Name>.xlsx.d/00-index.md, the workbook's grep-recall surface
    SHEET = "sheet"
    SUMMARY = "summary"  # streaming path for giant workbooks: schema + sample + CSV sidecar

class ChangeOp(enum.StrEnum):
    """CHANGELOG / commit-subject operation letters."""
    ADDED = "A"
    MODIFIED = "M"
    RENAMED = "R"
    DELETED = "D"

class CycleMode(enum.StrEnum):
    """How a cycle was invoked (one launchd job per mode, design section 4.7)."""
    POLL = "poll"  # delta where a cursor exists; local sources still walk (T3 is the full enumeration)
    RECONCILE = "reconcile"  # full enumeration for every source
    DRY_RUN = "dry_run"  # classify only: no materialise, no publish, no commit, cursors untouched

@dataclass(frozen=True, slots=True)
class RemoteHashes:
    """Provider-side hashes as reported by Graph; each comparable ONLY to itself, never to a local sha256."""
    quickxor: str | None = None
    sha256: str | None = None  # documented "isn't supported. Don't use" for drives; carried if present
    sha1: str | None = None

    @property
    def empty(self) -> bool:
        """True when the provider supplied no hash at all."""

@dataclass(frozen=True, slots=True)
class SourceItem:
    """One observed object from any arm, in one schema (design section 4.2 'one change-record schema').

    Identity is ``(source_id, stable_id)`` and is NEVER the path: local = ``f"{volume_uuid}:{inode}"``;
    graph drive = driveItem id; mail = message id; teams = ``f"{channel_id}:{yyyy-mm}"`` (monthly rollup).
    ``rel_path`` is a derived, mutable POSIX path relative to the source root, NFC-normalised.
    Times are integer nanoseconds since the epoch (UTC); for Graph items they come from
    ``lastModifiedDateTime``/``createdDateTime``.  ``extra`` holds arm-specific JSON scalars (sorted on
    export) and is excluded from hashing/equality of the dataclass.
    """
    source_id: str
    stable_id: str
    rel_path: str
    name: str
    size: int
    mtime_ns: int
    ctime_ns: int
    is_dir: bool = False
    dataless: bool = False
    gen_count: int | None = None  # ATTR_CMN_GEN_COUNT; None/0 = unknown => hash to confirm
    remote_hashes: RemoteHashes = field(default_factory=RemoteHashes)
    etag: str | None = None
    ctag: str | None = None
    deleted: bool = False  # explicit tombstone from the provider (never inferred from absence)
    parent_id: str | None = None  # id -> (parent, name) tree; paths are re-derived from it
    created_ns: int | None = None  # birthtime / createdDateTime; inbox arm keys on max(created, modified)
    ino: int | None = None  # H0 stat tuple (local only)
    mode: int | None = None  # H0 stat tuple (local only)
    content_type: str | None = None  # MIME type where the provider reports it
    extra: Mapping[str, ExtraValue] = field(default_factory=dict, hash=False, compare=False)

    @property
    def suffix(self) -> str:
        """Lower-case extension of ``name`` including the dot, e.g. ``.xlsx`` (``.teams.json`` kept whole)."""

@dataclass(frozen=True, slots=True)
class ScanResult:
    """What one arm's ``scan`` returns for one source and one pass.

    ``new_cursor`` is the PENDING cursor (e.g. the final ``@odata.deltaLink``): the cycle stores it as
    pending and promotes it only after the git commit.  ``enumeration_complete`` is True only for a
    FULL pass that saw the whole scope (every page, canary present, no budget abort).
    """
    source_id: str
    pass_kind: PassKind
    items: tuple[SourceItem, ...]
    new_cursor: str | None
    enumeration_complete: bool
    cursor_reset: bool = False  # a 410 or a dropped bad cursor forced a re-enumeration this pass
    unknown_dirs: tuple[str, ...] = ()  # zero-child cloud dirs / EPERM dirs: 'unknown', never 'empty'
    alarms: tuple[str, ...] = ()  # operator-visible problems (bad cursor dropped, canary missing, ...)

@dataclass(frozen=True, slots=True)
class FetchResult:
    """Bytes of one item, copied into the staging dir by ``SourceArm.fetch``."""
    stable_id: str
    path: Path  # staging copy; converters read ONLY this, never the live source path
    size: int
    content_sha256: str  # sha256 of exactly the bytes at ``path``

class SourceArm(Protocol):
    """The uniform interface every arm class implements; ``cycle.py`` depends only on this."""
    source_id: str
    kind: SourceKind

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Enumerate changes since ``cursor`` (or everything when ``full`` or cursor is None); zero file
        bytes."""

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Copy one item's bytes into ``dest_dir`` charging ``budget``; raises BudgetExhaustedError first."""

class ByteBudget:
    """Mutable per-cycle, per-source byte/file budget; ``charge`` raises BudgetExhaustedError when exceeded.

    Deliberately not a frozen dataclass: one instance lives for one source for one cycle.
    """
    __slots__ = ("files_used", "max_bytes", "max_files", "used")

    def __init__(self, max_bytes: int, max_files: int) -> None:
        self.max_bytes = max_bytes

    def can_afford(self, size: int) -> bool:
        """True when one more file of ``size`` bytes fits in both budgets."""

    def charge(self, size: int) -> None:
        """Reserve one file of ``size`` bytes or raise BudgetExhaustedError without charging."""

    @property
    def remaining_bytes(self) -> int:
        """Bytes still available this cycle."""

@dataclass(frozen=True, slots=True)
class CanonicalHash:
    """H1: canonical content hash plus the per-part digests it rolled up (so a CHANGED verdict can log
    parts)."""
    sha256: str
    method: str  # e.g. "ooxml-parts@1", "pdf-text@1", "bytes@1"
    parts: tuple[tuple[str, str], ...] = ()  # sorted (part name, sha256) for OOXML; () otherwise

@dataclass(frozen=True, slots=True)
class Classification:
    """The classifier's decision for one object."""
    source_id: str
    stable_id: str
    verdict: Verdict
    reason: str  # short machine-greppable reason, e.g. "quickxor-differs", "racily-clean", "canonical-equal"
    prev_path: str | None = None  # set when the object was renamed/moved (rename is reported, not churn)
    replaces_id: str | None = None  # safe-save: new FILEID at a path whose old id vanished in the same pass
    changed_parts: tuple[str, ...] = ()  # OOXML part names that differed (logged on every CHANGED)

@dataclass(frozen=True, slots=True)
class RenderedUnit:
    """One addressable output unit of one conversion (a whole document, one sheet, the workbook index).

    ``body`` is the markdown BELOW the frontmatter; ``rendered_sha256`` (H2) = sha256(body.encode("utf-8")).
    ``file_stem`` is the unit's output name inside a ``<Name>.<ext>.d/`` directory (e.g. ``01-q3-budget``);
    it is "" for a WHOLE unit, which is written as ``<Name>.<ext>.md`` / ``<name>.md``.
    """
    unit_id: str  # "whole" | "index" | "sheet:<n>" | "summary"
    kind: UnitKind
    index: int  # 0 for whole/index
    of: int  # total number of units in this conversion
    name: str  # sheet name etc.; "" for whole
    file_stem: str
    title: str
    summary: str  # converter-derived (title, headings, used range) — never model output
    tokens_estimate: int
    body: str
    rendered_sha256: str
    sidecars: tuple[tuple[str, bytes], ...] = ()  # (relative name, bytes), e.g. CSV for giant sheets

@dataclass(frozen=True, slots=True)
class ConversionResult:
    """The result of converting one fetched source file (cached write-once by ``action_key``)."""
    status: ConversionStatus
    converter_id: str
    converter_version: str
    options_hash: str
    action_key: str
    content_sha256: str
    canonical_sha256: str
    units: tuple[RenderedUnit, ...]
    reason: str | None = None  # for non-OK statuses: human-readable cause, goes into the stub page
    from_cache: bool = False

@dataclass(frozen=True, slots=True)
class MirrorChange:
    """One path-level change in docs/mirror for CHANGELOG and the commit subject."""
    op: ChangeOp
    path: str  # docs-repo-relative, e.g. "mirror/finance/fy26-budget.xlsx.d/01-q3.md"
    source_id: str
    stable_id: str
    prev_path: str | None = None

@dataclass(frozen=True, slots=True)
class LintFinding:
    """One land-gate lint result; any finding with ``blocking=True`` stops the commit."""
    code: str  # e.g. "SYMLINK", "FRONTMATTER", "NONDETERMINISTIC", "SECRET", "PATH", "INDEX-BUDGET"
    path: str
    message: str
    blocking: bool = True

@dataclass(frozen=True, slots=True)
class SourceReport:
    """Per-source outcome of one cycle."""
    source_id: str
    kind: SourceKind
    pass_kind: PassKind | None  # None when the source was skipped (network down, paused, lock, error)
    enumeration_complete: bool
    counts: Mapping[Verdict, int] = field(default_factory=dict, hash=False)
    materialised_bytes: int = 0
    deferred: int = 0
    breaker_tripped: bool = False
    cursor_advanced: bool = False
    skipped_reason: str | None = None
    alarms: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class CycleReport:
    """Everything one ``run_cycle`` did; the CLI prints it and STATE.md is rendered from it."""
    run_id: int
    mode: CycleMode
    sources: tuple[SourceReport, ...]
    changes: tuple[MirrorChange, ...]
    commit_sha: str | None  # None when nothing content-changing happened (no commit by design)
    lint_findings: tuple[LintFinding, ...] = ()
    broke_stale_lock: bool = False
    auth_required: bool = False
    exit_code: int = 0

TEAMS_MONTH_SCHEMA = "agentsync.teams-month/1"
"""``schema`` value of the per-channel-per-month JSON the Teams arm writes and the teams converter reads.

Shape (keys sorted on write, messages sorted by (created, id)):
``{"schema", "team_id", "team_name", "channel_id", "channel_name", "month": "YYYY-MM", "messages": [
{"id", "reply_to_id": str|null, "created": ISO-8601 UTC, "last_modified": ISO-8601 UTC, "from": display name,
"subject": str|null, "body_html": str, "deleted": bool, "attachments": [{"name", "content_url"}]}]}``.
"""
```

### `agentsync.errors` — `src/agentsync/errors.py` — owner: **architect (implemented)**

Typed exceptions shared by every agentsync module (see docs/design/CONTRACTS.md, "errors").

```python
class AgentSyncError(Exception):
    """Base class for every error agentsync raises on purpose."""

class ConfigError(AgentSyncError):
    """sources.toml is missing, unparseable or semantically invalid; the message names the key."""

class ManifestSchemaError(AgentSyncError):
    """The SQLite manifest's schema or key-schema version does not match this build; run migrate."""

class LockHeldError(AgentSyncError):
    """Another live agentsync process holds the single-writer lock (CLI exit 75, logged as skipped)."""

    def __init__(self, holder: str) -> None:
        super().__init__(f"lock held: {holder}")

class BudgetExhaustedError(AgentSyncError):
    """A per-cycle budget (bytes to materialise, files) would be exceeded by the next read."""

class MaterialiseError(AgentSyncError):
    """Reading a local (possibly dataless) file failed for a reason other than the budget."""

    def __init__(self, path: str, errno_: int | None, message: str) -> None:
        super().__init__(f"{path}: {message}")

class DatalessRefusedError(MaterialiseError):
    """The kernel refused to materialise a dataless file (EDEADLK): policy is OFF for this context."""

class ProviderTimeoutError(MaterialiseError):
    """The File Provider timed out (ETIMEDOUT) after every retry; a retry-later, never a verdict."""

class ConversionError(AgentSyncError):
    """A converter failed on one input; the row becomes status failed/unreadable, never an empty page."""

class UnreadableSourceError(ConversionError):
    """The bytes are encrypted / password-protected / IRM-protected: an UNREADABLE stub, a quarantined row."""

class CurateError(AgentSyncError):
    """A curated page's frontmatter violates the ``sources:`` contract (unparseable, path escapes docs/)."""

class PublishError(AgentSyncError):
    """Writing docs/ or committing it failed; the cycle must not advance any cursor."""

class LintError(AgentSyncError):
    """A land-gate lint failed; the cycle must not commit."""

class AuthError(AgentSyncError):
    """Signing in or acquiring a token failed."""

class AuthRequiredError(AuthError):
    """No usable token without user interaction (invalid_grant, AADSTS50076/50173, empty cache):
    REAUTH_REQUIRED."""

class GraphError(AgentSyncError):
    """A Microsoft Graph request failed with a non-retryable HTTP status."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None) -> None:
        super().__init__(f"Graph {status} {code}: {message}")

class GraphGone(GraphError):  # noqa: N818 - name fixed by the contract
    """410 Gone on a delta request: the cursor expired; re-enumerate fully (follow ``location`` if given)."""

    def __init__(self, code: str, message: str, location: str | None, request_id: str | None = None) -> None:
        super().__init__(410, code, message, request_id)

class GraphBadCursor(GraphError):  # noqa: N818 - name fixed by the contract
    """400 on a stored delta link ("sync token is malformed"): OUR cursor store is corrupt; alarm, drop,
    S0."""

class GraphThrottled(GraphError):  # noqa: N818 - name fixed by the contract
    """429/503 still failing after the client's retry budget; ``retry_after`` seconds from the last
    response."""

    def __init__(self, status: int, retry_after: float, message: str, request_id: str | None = None) -> None:
        super().__init__(status, "throttled", message, request_id)

class GraphNotFound(GraphError):  # noqa: N818 - name fixed by the contract
    """404 on an item or message (used by the mail arm's cross-folder delete check)."""

class GitError(PublishError):
    """A git subprocess exited non-zero; carries the argv and stderr."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(argv)} failed ({returncode}): {stderr.strip()}")
```

### `agentsync.paths` — `src/agentsync/paths.py` — owner: **architect (implemented)**

Default locations, the docs/ layout, the state-dir layout, and include/exclude glob matching.

```python
APP_NAME = "agentsync"

CLOUD_STORAGE_ROOT = Path("~/Library/CloudStorage")

def expand(path: str | Path) -> Path:
    """Expand ``~`` and return an absolute path (not resolved: symlinks are not followed)."""

def default_agent_context_dir() -> Path:
    """Return ``~/agent-context``, the parent of the default docs repo and sources.toml."""

def default_config_path() -> Path:
    """Return the default sources.toml path (``~/agent-context/sources.toml``)."""

def default_docs_repo() -> Path:
    """Return the default docs git repo (``~/agent-context/docs``), outside any cloud-synced path."""

def default_state_dir() -> Path:
    """Return the default machine-local state dir (manifest, cursors, lock, heartbeat)."""

def default_cache_dir() -> Path:
    """Return the default converter cache root (rebuildable, never in git)."""

def default_log_dir() -> Path:
    """Return the default log dir used by the launchd agents."""

def is_under(path: Path, root: Path) -> bool:
    """Return True when ``path`` equals or lies beneath ``root`` (lexically, after ``expand``)."""

def is_cloud_path(path: Path) -> bool:
    """Return True for any path under ``~/Library/CloudStorage`` (a File Provider tree)."""

@dataclass(frozen=True, slots=True)
class StatePaths:
    """Every file agentsync keeps under its machine-local state dir (never inside docs/)."""
    root: Path

    @property
    def db(self) -> Path:
        """SQLite manifest + cursors (mode 0600)."""

    @property
    def lock(self) -> Path:
        """Single-writer flock file."""

    @property
    def heartbeat(self) -> Path:
        """Liveness heartbeat JSON, one entry per source, written after every completed pass."""

    @property
    def token_cache_fallback(self) -> Path:
        """0600 MSAL token cache used ONLY when the Keychain is unavailable (logged)."""

    @property
    def keychain_marker(self) -> Path:
        """msal-extensions' Keychain persistence 'location' (a signal file, holds no secret)."""

    @property
    def staging(self) -> Path:
        """Per-cycle scratch for materialised/downloaded bytes; emptied at cycle start."""

    @property
    def teams_store(self) -> Path:
        """Per-channel message store the Teams arm rolls months up from."""

    @property
    def logs(self) -> Path:
        """Log directory used when running outside launchd."""

@dataclass(frozen=True, slots=True)
class DocsLayout:
    """The fixed layout of the docs/ git repo (design section 4.6)."""
    root: Path

    @property
    def mirror(self) -> Path:
        """Tier 1: generated, 1:1, never hand-edited."""

    @property
    def topics(self) -> Path:
        """Tier 2: agent-curated pages with pinned ``sources:``."""

    @property
    def index_md(self) -> Path:
        """Root INDEX.md (llms.txt shape, <= 25 KB)."""

    @property
    def readme_md(self) -> Path:
        """README.md: how the folder is built, the refresh-queue command, retention owner."""

    @property
    def claude_md(self) -> Path:
        """Root CLAUDE.md (three lines)."""

    @property
    def changelog_md(self) -> Path:
        """Generated index of the last 30 days of CHANGELOG sections."""

    @property
    def changelog_dir(self) -> Path:
        """Append-only monthly changelog files ``CHANGELOG/<yyyy-mm>.md``."""

    @property
    def depends_tsv(self) -> Path:
        """Generated page<TAB>source<TAB>pinned_sha<TAB>role."""

    @property
    def synonyms_tsv(self) -> Path:
        """Hand-maintained term<TAB>expansion<TAB>owner."""

    @property
    def by_entity_tsv(self) -> Path:
        """Generated entity<TAB>page."""

    @property
    def sync_dir(self) -> Path:
        """``_sync/``: STATE.md (gitignored), QUARANTINE.tsv, STATE.snapshot.md."""

    @property
    def state_md(self) -> Path:
        """Working-tree-only status page the agent reads first (gitignored)."""

    @property
    def state_snapshot_md(self) -> Path:
        """Committed STATE snapshot, written only on a cycle that already has a content commit."""

    @property
    def quarantine_tsv(self) -> Path:
        """Generated source_id<TAB>path<TAB>reason."""

    @property
    def manifest_dir(self) -> Path:
        """``_manifest/``: NDJSON shards only (one per source), committed."""

    @property
    def gitignore(self) -> Path:
        """docs/.gitignore."""

    @property
    def gitattributes(self) -> Path:
        """docs/.gitattributes."""

    def rel(self, path: Path) -> str:
        """Return ``path`` as a POSIX string relative to the docs repo root (raises ValueError outside it)."""

def glob_match(rel_path: str, pattern: str) -> bool:
    """Match one POSIX relative path against one glob (gitignore-like, case-insensitive).

    ``**`` spans directories, ``*``/``?`` stay within one segment, a pattern with no ``/`` matches the
    basename at any depth, a trailing ``/`` matches everything beneath that directory.
    """

def is_included(rel_path: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    """Return True if ``rel_path`` matches any include glob (empty include = all) and no exclude glob."""
```

### `agentsync.config` — `src/agentsync/config.py` — owner: **architect (implemented)**

Load and validate ``sources.toml`` into an immutable :class:`Config` (docs/design/CONTRACTS.md,
"config").

```python
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

DEFAULT_GRAPH_SCOPES: tuple[str, ...] = ("Files.Read.All", "Sites.Read.All", "Mail.Read", "User.Read")
"""Delegated scopes requested at device-code sign-in; ``offline_access`` is added by MSAL itself.

``ChannelMessage.Read.All`` (Teams) needs admin consent, so it is requested only when a live graph_teams
source exists (see :meth:`Config.graph_scopes`).
"""

TEAMS_SCOPE = "ChannelMessage.Read.All"

SHARED_MAIL_SCOPE = "Mail.Read.Shared"

DEFAULT_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

DEFAULT_EXCLUDES: tuple[str, ...] = ("~$*", "*.tmp", ".~lock.*#", ".DS_Store", "Icon\r", "._*")

@dataclass(frozen=True, slots=True)
class BreakerConfig:
    """Deletion circuit breaker: trip when candidates > max(fraction * live rows in scope, floor)."""
    fraction: float = 0.20
    floor: int = 25
    hold_days: int = 7

    def threshold(self, live_rows: int) -> int:
        """Largest deletion count that does NOT trip the breaker for a scope with ``live_rows`` live rows."""

@dataclass(frozen=True, slots=True)
class GraphConfig:
    """Microsoft Graph settings; ``client_id`` None means the Graph arms are unavailable (local arm still
    works)."""
    client_id: str | None = None
    tenant: str = "organizations"
    scopes: tuple[str, ...] = DEFAULT_GRAPH_SCOPES
    company: str = "agentsync"  # User-Agent: NONISV|<company>|agentsync/<version>
    base_url: str = DEFAULT_GRAPH_BASE_URL

    @property
    def authority(self) -> str:
        """MSAL authority URL for ``tenant``."""

@dataclass(frozen=True, slots=True)
class ConvertConfig:
    """Converter options; every field participates in the converters' ``options_hash``."""
    xlsx_stream_threshold_bytes: int = 20 * 1000**2
    max_rows_per_sheet: int = 5000
    max_page_bytes: int = 1_000_000  # hard cap before a unit is split / row-capped with a sidecar
    pandoc_path: Path | None = None  # None = the pypandoc_binary bundled pandoc, by absolute path

@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One ``[[source]]`` table, validated.  Kind-specific fields are None when not applicable."""
    id: str
    kind: SourceKind
    state: SourceState = SourceState.LIVE
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    max_materialise_bytes: int = 1024**3
    max_files: int = 5000
    cadence_s: int = 300
    principal: str | None = None
    retired_reason: str | None = None
    path: Path | None = None
    sentinel: str | None = None  # positive control: a relative path that must be present in every walk
    quiescence_s: int = 60  # inbox: skip files whose size/mtime changed within this many seconds
    drive_id: str | None = None  # a drive id, or "me" for /me/drive
    site: str | None = None  # "<host>:/sites/<name>" resolved to its default library's drive id
    folder: str | None = None  # drive: subtree filter ("/" default); mail: well-known name or folder id
    mailbox: str = "me"  # "me" or a shared mailbox UPN (needs Mail.Read.Shared)
    team_id: str | None = None
    channel_id: str | None = None

    @property
    def is_live(self) -> bool:
        """True when the source should be synced this cycle."""

@dataclass(frozen=True, slots=True)
class Config:
    """The whole validated configuration."""
    config_path: Path
    docs_repo: Path = field(default_factory=default_docs_repo)
    state_dir: Path = field(default_factory=default_state_dir)
    cache_dir: Path = field(default_factory=default_cache_dir)
    log_dir: Path = field(default_factory=default_log_dir)
    sources: tuple[SourceConfig, ...] = ()
    graph: GraphConfig = field(default_factory=GraphConfig)
    breaker: BreakerConfig = field(default_factory=BreakerConfig)
    convert: ConvertConfig = field(default_factory=ConvertConfig)
    reconcile_interval_s: int = 3600
    poll_interval_s: int = 300
    tombstone_reap_days: int = 180
    principal: str | None = None
    launchd_label_prefix: str = "com.agentsync"

    @property
    def state_paths(self) -> StatePaths:
        """Layout of the machine-local state dir."""

    @property
    def layout(self) -> DocsLayout:
        """Layout of the docs repo."""

    def source(self, source_id: str) -> SourceConfig:
        """Return the source with ``source_id`` or raise ConfigError."""

    def live_sources(self) -> tuple[SourceConfig, ...]:
        """Sources whose state is live, in configuration order."""

    def graph_scopes(self) -> tuple[str, ...]:
        """Scopes to request: configured ones plus Teams/shared-mail scopes only when a live source needs
        them."""

def parse_size(value: object, *, where: str) -> int:
    """Parse a byte count: a non-negative int, or a string like ``"500MB"`` / ``"2GiB"``."""

def parse_config(text: str, *, config_path: Path) -> Config:
    """Parse and validate sources.toml text; raises ConfigError naming the file, table and key."""

def load_config(path: Path | None = None) -> Config:
    """Read and validate sources.toml (default ``~/agent-context/sources.toml``); raises ConfigError."""

def default_config_text() -> str:
    """Return the commented sources.toml template that ``agentsync init`` writes."""
```

### `agentsync.frontmatter` — `src/agentsync/frontmatter.py` — owner: **architect (implemented)**

Deterministic YAML frontmatter: the mirror-page contract (design 4.4/4.6) and a tolerant reader.

Rendering is hand-rolled (fixed key order, no wall-clock, one spelling per value) so two renders of the same
inputs are byte-identical and the refresh-queue awk can read ``rendered_sha256:`` / ``status:`` lines raw.
Parsing uses ``yaml.safe_load`` because curated pages are written by an agent in free YAML.

```python
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

FENCE = "---"

class FrontmatterError(AgentSyncError):
    """A page has no frontmatter, unterminated frontmatter, invalid YAML, or violates the mirror contract."""

@dataclass(frozen=True, slots=True)
class Bare:
    """A scalar rendered without quotes (hashes, enum values, ids); validated to be YAML-safe."""
    value: str

Scalar = str | int | float | bool | Bare

FrontmatterValue = Scalar | Mapping[str, Scalar] | None

def render_frontmatter(fields: Mapping[str, FrontmatterValue]) -> str:
    """Render ``fields`` in their given order as a ``---``-fenced block ending in a newline; None values are
    omitted.

    Nested mappings render as one flow mapping (``part: {kind: sheet, index: 1}``) in their given order.
    """

def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a page into (frontmatter YAML text without fences, body); (None, text) when there is none."""

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a page into (frontmatter mapping, body); dates become ISO strings; raises FrontmatterError."""

MIRROR_KEY_ORDER: tuple[str, ...] = (  # ... (see source)
"""Every key a mirror page may carry, in the one order they are written.  Nothing else is allowed."""

MIRROR_REQUIRED: tuple[str, ...] = ("source_kind", "source_id", "stable_id", "source_path", "status")

MIRROR_REQUIRED_CURRENT: tuple[str, ...] = (
    "content_sha256",
    "canonical_sha256",
    "rendered_sha256",
    "converter",
    "options_hash",
    "summary",
    "tokens_estimate",
)

MIRROR_REQUIRED_DELETED: tuple[str, ...] = ("deleted_at", "last_rendered_sha256")

@dataclass(frozen=True, slots=True)
class MirrorFrontmatter:
    """Content-derived frontmatter of one docs/mirror page.  No wall-clock field exists here by design.

    ``deleted_at`` is the UTC date (``YYYY-MM-DD``) of the run that tombstoned the page, recorded in the
    manifest's ``tombstones`` row, so re-rendering a tombstone from the manifest is byte-identical.
    """
    source_kind: str
    source_id: str
    stable_id: str
    source_path: str
    status: PageStatus
    source_web_url: str | None = None
    source_etag: str | None = None
    source_version: str | None = None
    content_sha256: str | None = None
    canonical_sha256: str | None = None
    rendered_sha256: str | None = None
    part_kind: str | None = None
    part_name: str | None = None
    unit_index: int | None = None
    unit_of: int | None = None
    converter: str | None = None  # "<converter_id>@<converter_version>"
    options_hash: str | None = None  # "sha256:<hex>"
    sensitivity_label: str | None = None
    reason: str | None = None  # unreadable/refused/failed stubs
    superseded_by: str | None = None
    deleted_at: str | None = None
    last_rendered_sha256: str | None = None
    last_commit: str | None = None
    source_title: str | None = None
    summary: str | None = None
    tokens_estimate: int | None = None

    def to_fields(self) -> dict[str, FrontmatterValue]:
        """Return the ordered field mapping ``render_frontmatter`` writes."""

def render_mirror_page(fm: MirrorFrontmatter, body: str) -> str:
    """Render one complete mirror page: frontmatter, then ``body`` (which must end in exactly one newline)."""

def validate_mirror_frontmatter(data: Mapping[str, Any]) -> list[str]:
    """Return every contract violation in a parsed mirror frontmatter mapping ([] when it conforms)."""

def parse_mirror_page(text: str) -> tuple[MirrorFrontmatter, str]:
    """Parse and validate a mirror page; raises FrontmatterError listing every violation."""
```

### `agentsync.manifest` — `src/agentsync/manifest.py` — owner: **manifest**

SQLite manifest: items keyed on stable identity, outputs, tombstones, cursors, runs (owner: manifest).

Contract: docs/design/CONTRACTS.md section "manifest.py".  The schema below IS the contract; change it only
together with MANIFEST_SCHEMA_VERSION and a migration.

```python
MANIFEST_SCHEMA_VERSION = 1

SCHEMA_SQL = """  # ... (see source)

@dataclass(frozen=True, slots=True)
class SourceRow:
    """One ``sources`` row."""
    source_id: str
    kind: SourceKind
    config_state: SourceState
    config_fingerprint: str
    enumeration_complete: bool
    baseline_complete: bool
    last_full_run_id: int | None
    last_success_run_id: int | None
    breaker_tripped_at: str | None
    breaker_until: str | None
    breaker_candidates: int | None
    auth_state: str

@dataclass(frozen=True, slots=True)
class ItemRow:
    """One ``items`` row."""
    source_id: str
    stable_id: str
    parent_id: str | None
    name: str
    rel_path: str
    prev_path: str | None
    is_dir: bool
    size: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    created_ns: int | None
    ino: int | None
    mode: int | None
    gen_count: int | None
    dataless: bool
    quickxor: str | None
    sha1_remote: str | None
    sha256_remote: str | None
    etag: str | None
    ctag: str | None
    content_type: str | None
    content_sha256: str | None
    canonical_sha256: str | None
    canonical_method: str | None
    canonical_parts: tuple[tuple[str, str], ...]
    state: RowState
    state_reason: str | None
    last_verdict: Verdict | None
    first_seen_run: int
    last_seen_run: int
    principal: str | None
    sensitivity_label: str | None
    extra: Mapping[str, ExtraValue] = field(default_factory=dict, hash=False, compare=False)

@dataclass(frozen=True, slots=True)
class OutputRow:
    """One ``outputs`` row."""
    output_path: str
    source_id: str
    stable_id: str
    unit_id: str
    action_key: str | None
    rendered_sha256: str | None
    page_sha256: str | None
    converter_id: str | None
    converter_version: str | None
    options_hash: str | None
    status: OutputStatus
    built_run: int

@dataclass(frozen=True, slots=True)
class TombstoneRow:
    """One ``tombstones`` row."""
    output_path: str
    source_id: str
    stable_id: str
    unit_id: str
    deleted_at: str
    deleted_run: int
    last_rendered_sha256: str | None
    last_commit: str | None
    reap_after: str
    reason: str

@dataclass(frozen=True, slots=True)
class CursorRow:
    """One ``cursors`` row (secret: never logged, never exported; see ``cursor_fingerprint``)."""
    source_id: str
    current: str | None
    current_set_at: str | None
    pending: str | None
    pending_run_id: int | None
    page_link: str | None

@dataclass(frozen=True, slots=True)
class DependsRow:
    """One DEPENDS.tsv / ``depends`` row; paths are docs-repo-relative."""
    page: str
    source: str
    pinned_sha: str
    role: str

def cursor_fingerprint(cursor: str | None) -> str:
    """Return the 12-hex sha256 prefix of a cursor for STATE.md (never the token itself); "-" for None."""

class Manifest:
    """The SQLite working store.  One instance per cycle; not thread-safe; single writer by the ops lock."""

    def __init__(self, db_path: Path) -> None:
        """Open (creating with mode 0600, WAL, synchronous=FULL, foreign_keys=ON) and migrate-check the db.

        Raises ManifestSchemaError when ``meta.manifest_schema_version`` (or the converter cache's
        ``key_schema_version``) differs from this build; never silently re-derives.
        """

    def close(self) -> None:
        """Close the connection (idempotent)."""

    def __enter__(self) -> Manifest:
        """Return self."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close."""

    def transaction(self) -> AbstractContextManager[None]:
        """BEGIN IMMEDIATE ... COMMIT (ROLLBACK on exception).  Not re-entrant: nesting raises
        RuntimeError."""

    def get_meta(self, key: str) -> str | None:
        """Return a meta value or None."""

    def set_meta(self, key: str, value: str) -> None:
        """Upsert a meta value."""

    def written_at_ns(self) -> int:
        """Return meta.written_at_ns (0 if unset): the racily-clean guard's reference time."""

    def begin_run(self, mode: CycleMode, *, host: str, pid: int) -> int:
        """Insert a ``runs`` row with status 'running' and return its run_id (monotonic)."""

    def record_source_pass(
        self,
        run_id: int,
        source_id: str,
        *,
        pass_kind: PassKind | None,
        enumeration_complete: bool,
        cursor_reset: bool,
        counts: Mapping[str, int],
        skipped_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        """Upsert the ``run_sources`` row for one source in one run."""

    def finish_run(
        self, run_id: int, *, status: str, commit_sha: str | None, counts: Mapping[str, int]
    ) -> None:
        """Close a run row; also sets meta.written_at_ns to the current time_ns."""

    def last_runs(self, limit: int = 10) -> list[tuple[int, str, str, str | None]]:
        """Return (run_id, mode, status, commit_sha) newest first."""

    def sync_sources(self, configured: Sequence[SourceConfig]) -> list[str]:
        """Upsert one ``sources`` row per configured source; return ids whose scope fingerprint changed.

        A changed fingerprint (path/drive/folder/mailbox/team/channel) clears ``enumeration_complete``
        and drops the cursor.  A kind change for an existing id raises ConfigError.
        """

    def get_source(self, source_id: str) -> SourceRow | None:
        """Return the ``sources`` row or None."""

    def set_enumeration_complete(self, source_id: str, complete: bool, run_id: int) -> None:
        """Set the flag; True also sets baseline_complete and last_full_run_id."""

    def set_last_success(self, source_id: str, run_id: int) -> None:
        """Record the last run in which this source's pass completed without error."""

    def trip_breaker(self, source_id: str, *, candidates: int, tripped_at: str, until: str) -> None:
        """Record a tripped deletion breaker for the scope (removals suspended until ``until``)."""

    def clear_breaker(self, source_id: str) -> None:
        """Clear the breaker (operator action, or ``until`` passed with candidates under threshold)."""

    def breaker_active(self, source_id: str, now_iso: str) -> bool:
        """True when the breaker is tripped and ``now_iso`` < breaker_until."""

    def set_auth_state(self, state: str, source_ids: Iterable[str]) -> None:
        """Set auth_state (``ok`` | ``REAUTH_REQUIRED``) on the given sources."""

    def get_item(self, source_id: str, stable_id: str) -> ItemRow | None:
        """Return the row or None."""

    def item_by_path(self, source_id: str, rel_path: str) -> ItemRow | None:
        """Return the non-tombstone row currently at ``rel_path`` (NFC, case-sensitive) or None."""

    def iter_items(self, source_id: str, *, states: Iterable[RowState] | None = None) -> Iterator[ItemRow]:
        """Yield rows of one source ordered by stable_id, optionally filtered by state."""

    def live_count(self, source_id: str) -> int:
        """Count rows in state live|dataless (the breaker denominator), files only."""

    def tree_lookup(self, source_id: str) -> TreeLookup:
        """Return a callable ``stable_id -> (parent_id, name)`` over this source's rows (None if unknown)."""

    def upsert_observed(self, item: SourceItem, *, run_id: int, verdict: Verdict, state: RowState) -> None:
        """Insert or update a row from an observation: all H0/provider fields, last_verdict, last_seen_run.

        Content hashes are NOT touched here (see ``set_content``).  A rel_path change stores prev_path.
        """

    def set_content(
        self,
        source_id: str,
        stable_id: str,
        *,
        content_sha256: str,
        canonical_sha256: str,
        canonical_method: str,
        canonical_parts: Sequence[tuple[str, str]],
    ) -> None:
        """Record H1 inputs after a successful fetch."""

    def set_verdict(self, source_id: str, stable_id: str, verdict: Verdict) -> None:
        """Update last_verdict only (phase 2/3 outcomes, DEFERRED, ERROR)."""

    def set_state(self, source_id: str, stable_id: str, state: RowState, reason: str | None) -> None:
        """Set a row's state (quarantine, refuse, tombstone, restore to live)."""

    def pending_work(self, source_id: str) -> list[ItemRow]:
        """Rows whose last_verdict is CREATED/MAYBE_CHANGED/CHANGED/DEFERRED/ERROR (not yet published).

        This is what lets a delta cursor advance safely when a budget deferred work: the work is durable here.
        """

    def unseen_live(self, source_id: str, run_id: int) -> list[ItemRow]:
        """Live/dataless file rows whose last_seen_run < run_id, ordered by stable_id (deletion
        candidates)."""

    def rekey(self, source_id: str, old_id: str, new_id: str) -> None:
        """Safe-save continuity: move the old row's content hashes and outputs to the new stable id, drop
        old."""

    def rederive_paths(self, source_id: str, root_id: str | None) -> list[tuple[str, str, str]]:
        """Recompute rel_path from the id->(parent, name) tree; return (stable_id, old, new) that changed.

        ``root_id`` is the scope root (drive folder id) or None for the drive root.  O(changed subtree).
        """

    def find_by_canonical(self, canonical_sha256: str) -> list[ItemRow]:
        """Rows (any source, live) with this canonical hash — inbox duplicate / alias-of detection."""

    def outputs_for(self, source_id: str, stable_id: str) -> list[OutputRow]:
        """Output rows of one item ordered by unit_id."""

    def output_by_path(self, output_path: str) -> OutputRow | None:
        """Return the output row owning ``output_path`` (case-insensitive + NFC match) or None."""

    def replace_outputs(self, source_id: str, stable_id: str, rows: Sequence[OutputRow]) -> list[OutputRow]:
        """Replace an item's output rows; return the previous rows whose unit_id is no longer present."""

    def live_action_keys(self) -> set[str]:
        """Every action_key referenced by a non-tombstone output (converter-cache GC roots)."""

    def iter_outputs(self, *, source_id: str | None = None) -> Iterator[OutputRow]:
        """Yield output rows ordered by output_path."""

    def record_cache(
        self,
        action_key: str,
        *,
        converter_id: str,
        converter_version: str,
        options_hash: str,
        canonical_sha256: str,
        status: str,
        unit_count: int,
        size: int,
        run_id: int,
    ) -> None:
        """Insert or touch (last_used_run) a cache index row."""

    def add_tombstone(self, row: TombstoneRow) -> None:
        """Insert or replace a tombstone row."""

    def get_tombstone(self, output_path: str) -> TombstoneRow | None:
        """Return the tombstone at ``output_path`` or None."""

    def remove_tombstone(self, output_path: str) -> None:
        """Delete a tombstone row (restore on reappearance, or after reaping)."""

    def tombstones_due(self, today: str) -> list[TombstoneRow]:
        """Tombstones whose reap_after <= today (UTC date), ordered by output_path."""

    def iter_tombstones(self) -> Iterator[TombstoneRow]:
        """Yield all tombstones ordered by output_path."""

    def get_cursor(self, source_id: str) -> CursorRow | None:
        """Return the cursor row or None."""

    def stage_cursor(self, source_id: str, pending: str | None, run_id: int) -> None:
        """Store a pending cursor (same transaction as the item snapshot).  ``current`` is untouched."""

    def promote_cursors(self, run_id: int, now_iso: str) -> list[str]:
        """After the git commit: pending -> current for every cursor staged by ``run_id``; return source
        ids."""

    def discard_pending(self, run_id: int) -> None:
        """Forget pending cursors of a failed run (the next cycle re-reads from ``current``)."""

    def drop_cursor(self, source_id: str) -> None:
        """Delete current+pending+page_link (400 bad cursor, scope change, retirement)."""

    def set_page_link(self, source_id: str, link: str | None) -> None:
        """Persist the in-progress enumeration's nextLink so a crash resumes instead of restarting."""

    def replace_depends(self, rows: Sequence[DependsRow]) -> None:
        """Replace the whole depends table (it is regenerated from topics/ frontmatter every cycle)."""

    def pages_citing(self, source_path: str) -> list[str]:
        """Curated pages (docs-repo-relative) that cite ``source_path``, sorted."""

    def export_shard(self, source_id: str) -> list[str]:
        """Return the deterministic NDJSON lines of ``_manifest/<source_id>.jsonl`` (see CONTRACTS.md)."""

    def tree_sha(self) -> str | None:
        """Return meta.tree_sha: the git tree the manifest last published."""

    def set_tree_sha(self, tree_sha: str) -> None:
        """Record the git tree sha of the commit that published the current manifest state."""
```

### `agentsync.classifier` — `src/agentsync/classifier.py` — owner: **manifest**

The H0/H1/H2 multi-state change classifier (design 4.3) — pure functions, no I/O (owner: manifest).

```python
@dataclass(frozen=True, slots=True)
class ClassifyContext:
    """Inputs every phase-1 decision needs besides the item and its row."""
    run_id: int
    pass_kind: PassKind
    written_at_ns: (
        int  # manifest's last write time: racily-clean guard (row.mtime_ns >= this => MAYBE_CHANGED)
    )

@dataclass(frozen=True, slots=True)
class PassClassification:
    """Everything phase 1 decided for one source pass."""
    verdicts: tuple[Classification, ...]  # one per observed item, ordered by stable_id
    safe_saves: tuple[tuple[str, str], ...]  # (new_id, old_id) rekeys to apply before phase 2
    deletion_candidates: tuple[str, ...]  # stable ids; empty unless FULL and enumeration_complete
    breaker_tripped: bool  # candidates > breaker threshold: NO removal may be applied this pass

def classify_observed(item: SourceItem, row: ItemRow | None, ctx: ClassifyContext) -> Classification:
    """Phase 1 (zero bytes) for one observed object, in the exact rung order of design 4.3.

    new row -> CREATED; item.deleted -> DELETED; path moved -> prev_path set, continue; dataless -> DATALESS;
    quickXorHash both present -> differ ? MAYBE_CHANGED : UNCHANGED; hash absent on either side -> never
    UNCHANGED from hash, fall through (logged); cTag both present (files only) -> differ ? MAYBE_CHANGED :
    UNCHANGED; eTag moved but size/mtime hold -> METADATA_ONLY; stat tuple (size, mtime_ns, ctime_ns, ino,
    mode) and (ino, gen_count) equal -> racily-clean guard: row.mtime_ns >= ctx.written_at_ns ? MAYBE_CHANGED
    : UNCHANGED; gen_count None/0/decreased = unknown -> MAYBE_CHANGED; else MAYBE_CHANGED. mtime compared for
    inequality only. Directories: CREATED / UNCHANGED / METADATA_ONLY / DELETED only.
    """

def classify_content(row: ItemRow | None, canonical: CanonicalHash) -> Classification:
    """Phase 2, after materialise: canonical (H1) equal to row's -> TOUCHED_NOT_CHANGED, else CHANGED.

    On CHANGED, ``changed_parts`` lists the OOXML parts whose digests differ (logged by the caller). A row
    with no previous canonical hash (CREATED) is CHANGED.
    """

def classify_output(previous: Sequence[OutputRow], result: ConversionResult) -> Verdict:
    """Phase 3, H2 early cutoff: OUTPUT_UNCHANGED iff the unit set and every unit's rendered_sha256 match."""

def match_safe_saves(created: Sequence[SourceItem], missing: Sequence[ItemRow]) -> list[tuple[str, str]]:
    """Pair a CREATED file with a vanished row at the SAME rel_path (Office safe-save: new FILEID).

    Only meaningful in a FULL pass.  Returns (new_id, old_id) sorted by new_id; each id used at most once.
    """

def deletion_candidates(
    unseen: Sequence[ItemRow], *, pass_kind: PassKind, enumeration_complete: bool
) -> list[ItemRow]:
    """Absence is evidence only inside a complete FULL pass; returns [] for DELTA or incomplete passes."""

def breaker_trips(candidates: int, live_rows: int, breaker: BreakerConfig) -> bool:
    """True when candidates > max(breaker.fraction * live_rows, breaker.floor) (per-scope, floored)."""

def classify_pass(
    items: Sequence[SourceItem],
    rows: dict[str, ItemRow],
    unseen: Sequence[ItemRow],
    ctx: ClassifyContext,
    *,
    enumeration_complete: bool,
    live_rows: int,
    breaker: BreakerConfig,
    breaker_active: bool,
) -> PassClassification:
    """Run phase 1 over a whole pass: per-item verdicts, safe-save pairing, deletion candidates, breaker.

    ``rows`` maps stable_id -> existing row for this source.  A breaker that is already active, or trips
    now, yields ``breaker_tripped=True`` and the caller applies NO absence-based removal.
    """
```

### `agentsync.arm_local` — `src/agentsync/arm_local.py` — owner: **local**

Arm B (local / sync-client folder) and arm C (manual inbox): T3 metadata walk, zero file opens (owner:
local).

```python
@dataclass(frozen=True, slots=True)
class WalkStats:
    """Counters from one walk (reported in STATE.md and the CycleReport)."""
    files: int
    dirs: int
    dataless: int
    excluded: int
    symlinks_skipped: int
    unknown_dirs: tuple[str, ...]  # zero-child dirs inside a cloud tree, and EPERM/EACCES dirs (TCC)
    sentinel_present: bool | None  # None when no sentinel is configured

def volume_uuid(path: Path) -> str:
    """Return the UUID of the volume holding ``path`` (getattrlist ATTR_VOL_UUID on its mount point).

    Falls back to ``diskutil info -plist <mount>`` VolumeUUID; raises OSError if neither yields one. Stable
    across reboots, unlike st_dev.
    """

def gen_count(path: Path) -> int | None:
    """Return ATTR_CMN_GEN_COUNT via getattrlist (no open, no hydration); None when not returned or 0."""

def stable_id_for(volume: str, ino: int) -> str:
    """Return the local identity string ``f"{volume}:{ino}"`` (never the path)."""

def walk(
    root: Path,
    *,
    source_id: str,
    volume: str,
    include: Sequence[str],
    exclude: Sequence[str],
    with_gen_count: bool = True,
) -> tuple[list[SourceItem], WalkStats]:
    """Walk ``root`` with os.scandir + lstat: never follows symlinks, never opens or reads a file.

    Emits files only (is_dir=False), sorted by rel_path; rel_path is POSIX, NFC-normalised, relative to
    ``root``.  Directories are pruned only by ``exclude`` (``include`` applies to files).  Each item carries
    size, mtime_ns, ctime_ns, created_ns (st_birthtime), ino, mode, dataless (SF_DATALESS), gen_count.
    A directory under ~/Library/CloudStorage with zero children, or one raising EPERM/EACCES, is recorded in
    ``unknown_dirs`` and never read as empty.  Raises FileNotFoundError if ``root`` does not exist.
    """

def fold_conflict_suffix(name: str) -> str:
    """Normalise a name for inbox dedup: NFC, strip ``-<COMPUTERNAME>``, `` (1)``, `` - Copy`` suffixes."""

class LocalArm:
    """SourceArm for kind ``local``: every scan is a FULL enumeration (T3 is the truth tier)."""
    source_id: str
    kind: SourceKind

    def __init__(self, cfg: SourceConfig) -> None:
        """Bind to one configured local source; raises ConfigError if cfg.kind is not LOCAL/INBOX."""

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Walk the source root; ``cursor`` and ``full`` are ignored (always FULL, new_cursor=None).

        enumeration_complete is True only if the root exists, the sentinel (if configured) is present, and
        ``unknown_dirs`` is empty; otherwise False with an alarm naming the cause.  Root missing -> an empty
        ScanResult with enumeration_complete=False (never mass deletion).
        """

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Materialise ``root/item.rel_path`` into ``dest_dir`` via ``materialise.materialise``.

        Re-lstats first: if the inode no longer matches ``item.ino`` raises FileNotFoundError (re-classify).
        """

class InboxArm(LocalArm):
    """SourceArm for kind ``inbox``: LocalArm plus quiescence, lock-file ignores and max(created,
    modified)."""

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """As LocalArm.scan, but items whose size/mtime changed within ``quiescence_s`` are withheld.

        mtime_ns is reported as max(created_ns, mtime_ns) (a copied file keeps its original mtime).  Withheld
        items make enumeration_complete False (they are neither new nor absent this pass).
        """

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """As LocalArm.fetch, plus two-read agreement: hash differs across two reads -> MaterialiseError."""
```

### `agentsync.materialise` — `src/agentsync/materialise.py` — owner: **local**

Fail-closed hydration discipline and the ONE place a File Provider download may happen (owner: local).

Constants are from the macOS 15 SDK ``sys/resource.h`` / ``sys/stat.h``.  Every function here is macOS-only;
on another platform ``set_materialize_policy`` raises OSError(ENOSYS).

```python
IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES = 3

IOPOL_SCOPE_PROCESS = 0

IOPOL_SCOPE_THREAD = 1

IOPOL_MATERIALIZE_DATALESS_FILES_DEFAULT = 0

IOPOL_MATERIALIZE_DATALESS_FILES_OFF = 1

IOPOL_MATERIALIZE_DATALESS_FILES_ON = 2

SF_DATALESS = 0x40000000

EDEADLK = 11  # macOS errno: materialisation refused for this context

ETIMEDOUT = 60  # macOS errno: provider warming up; retry with backoff

@dataclass(frozen=True, slots=True)
class MaterialiseResult:
    """Outcome of copying one source file into staging."""
    src: Path
    dest: Path
    size: int
    content_sha256: str
    was_dataless: bool
    attempts: int

def is_dataless(st: os.stat_result) -> bool:
    """True when ``st.st_flags`` carries SF_DATALESS (an online-only placeholder; lstat does not hydrate)."""

def get_materialize_policy(scope: int = IOPOL_SCOPE_PROCESS) -> int:
    """Return getiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, scope); raises OSError."""

def set_materialize_policy(policy: int, scope: int = IOPOL_SCOPE_PROCESS) -> None:
    """Call setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, scope, policy) via ctypes; raises
    OSError."""

def fail_closed() -> None:
    """Set the PROCESS policy to OFF (inherited by children): any accidental read of a placeholder -> EDEADLK.

    The CLI calls this before any walk, subprocess or conversion.
    """

def materialize_allowed() -> AbstractContextManager[None]:
    """Temporarily set the THREAD policy to ON (restoring the previous thread policy on exit).

    If thread scope proves insufficient on a given macOS build, the implementation may instead toggle process
    scope ON/OFF around the read; the launchd plist keeps ``MaterializeDatalessFiles=false`` regardless.
    """

def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Return the hex sha256 of a file's bytes (callers must only pass non-dataless or staged files)."""

def materialise(
    src: Path,
    dest: Path,
    budget: ByteBudget,
    *,
    retries: int = 3,
    backoff_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> MaterialiseResult:
    """Copy ``src`` to ``dest`` (tmp + rename) hashing on the way, under ``materialize_allowed()``.

    Pre: ``src`` is a regular file (lstat, not a symlink). Charges ``budget`` with lstat().st_size BEFORE
    reading (raises BudgetExhaustedError without reading). EDEADLK -> DatalessRefusedError (no retry);
    ETIMEDOUT -> retry ``retries`` times with exponential backoff, then ProviderTimeoutError; ENOENT ->
    FileNotFoundError propagates (vanished between walk and read: re-classify next cycle); size or mtime
    changed during the copy -> MaterialiseError("unstable"), dest removed. Post: dest holds exactly the bytes
    hashed into ``content_sha256``; src is never written.
    """
```

### `agentsync.convert` — `src/agentsync/convert/__init__.py` — owner: **convert**

L3 conversion: registry, write-once cache, per-format converters (owner: convert).

```python
def convert_file(
    src: Path,
    *,
    name: str,
    content_sha256: str,
    canonical_sha256: str,
    registry: Registry,
    cache: ConverterCache,
) -> ConversionResult:
    """Convert one staged file through the cache; never raises for a per-file failure.

    No converter -> status REFUSED ("no converter for <suffix>"). Cache hit on the action key -> cached result
    (``from_cache=True``). Converter raises UnreadableSourceError -> UNREADABLE; any other exception -> FAILED
    with the reason; OK results are stored write-once. Units never empty for OK.
    """

def double_conversion_differs(src: Path, *, name: str, registry: Registry) -> bool:
    """Convert twice without the cache; True when any unit's rendered_sha256 differs (land-gate lint)."""
```

### `agentsync.convert.base` — `src/agentsync/convert/base.py` — owner: **convert**

The converter protocol and helpers every converter uses (owner: convert).

```python
OptionValue = str | int | float | bool

class Converter(Protocol):
    """One format converter.  Must be deterministic by construction (the cache makes it safe if not)."""
    converter_id: str  # stable, lowercase, e.g. "pandoc-gfm"; part of the action key
    extensions: tuple[str, ...]  # lower-case suffixes incl. dot, e.g. (".docx", ".odt")

    def version(self) -> str:
        """Version AS RUN: ``<emitter semver>+<tool>-<tool version>`` (e.g. by running ``pandoc
        --version``)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options that can change output; hashed into ``options_hash``."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert the staged file ``src`` (original file name ``name``) into one or more units.

        Pre: ``src`` is a regular, fully materialised staging copy.  Post: units sorted by index; bodies are
        NFC text ending in exactly one newline, containing no wall-clock or run-specific value.
        Raises UnreadableSourceError (encrypted/password/IRM) or ConversionError (anything else).
        """

def estimate_tokens(text: str) -> int:
    """Return a deterministic token estimate for ``text`` (ceil(chars / 4))."""

def options_hash(options: Mapping[str, OptionValue]) -> str:
    """Return ``"sha256:" + sha256(json.dumps(options, sort_keys=True, separators=(",", ":")))``."""

def rendered_sha256(body: str) -> str:
    """Return H2: hex sha256 of ``body.encode("utf-8")``."""

def make_unit(
    *,
    unit_id: str,
    kind: UnitKind,
    index: int,
    of: int,
    name: str,
    file_stem: str,
    title: str,
    summary: str,
    body: str,
    sidecars: tuple[tuple[str, bytes], ...] = (),
) -> RenderedUnit:
    """Build a RenderedUnit, normalising ``body`` (NFC, LF, single trailing newline) and computing H2 +
    tokens."""
```

### `agentsync.convert.registry` — `src/agentsync/convert/registry.py` — owner: **convert**

Converter registry: routes a file name to exactly one converter by extension (owner: convert).

```python
class Registry:
    """An immutable extension -> converter map."""

    def __init__(self, converters: Sequence[Converter]) -> None:
        """Build the map; raises ValueError if two converters claim one extension."""

    @classmethod
    def default(cls, cfg: ConvertConfig) -> Registry:
        """Registry of every built-in converter (pandoc, xlsx, pptx, pdf, markdown, text, eml, teams)."""

    def for_name(self, name: str) -> Converter | None:
        """Return the converter for a file name by its lower-case suffix (``.teams.json`` matched whole)."""

    def converters(self) -> tuple[Converter, ...]:
        """All registered converters, sorted by converter_id."""

    def extensions(self) -> tuple[str, ...]:
        """All claimed extensions, sorted."""
```

### `agentsync.convert.cache` — `src/agentsync/convert/cache.py` — owner: **convert**

Write-once converter cache keyed on producer identity (design 4.1 #5) (owner: convert).

On disk: ``<root>/<key[:2]>/<key>/result.json`` + ``unit-<index>.md`` + ``sidecar-<index>-<name>``; written
into ``<root>/tmp-<random>/`` and renamed into place, so a half-written key is never readable. Never in git.

```python
KEY_SCHEMA_VERSION = 1
"""Bumped ONLY when the composition of the action key changes (never for a converter change)."""

def action_key(
    *,
    converter_id: str,
    converter_version: str,
    options_hash: str,
    canonical_sha256: str,
    unit_id: str = "whole",
) -> str:
    """Return sha256 hex of ``"|".join([KEY_SCHEMA_VERSION, converter_id, converter_version, options_hash,
    unit_id, canonical_sha256])`` (no env allowlist: converters run with a fixed env)."""

class ConverterCache:
    """Filesystem cache of ConversionResults; safe against crashes; single writer (the ops lock)."""

    def __init__(self, root: Path) -> None:
        """Bind to ``root`` (created 0700 on first put)."""

    def path_for(self, key: str) -> Path:
        """Return the directory a key lives in (may not exist)."""

    def get(self, key: str) -> ConversionResult | None:
        """Return the cached result with ``from_cache=True``, or None on miss/corrupt entry (corrupt is
        logged)."""

    def put(self, result: ConversionResult) -> bool:
        """Store ``result`` under ``result.action_key`` if absent (write-once); return True if written.

        Only OK and UNREADABLE results are cached; FAILED/REFUSED are not (a transient failure retries).
        """

    def gc(self, live_keys: Iterable[str]) -> int:
        """Delete every key not in ``live_keys`` plus stale ``tmp-*`` dirs; return the number removed."""
```

### `agentsync.convert.canonical` — `src/agentsync/convert/canonical.py` — owner: **convert**

H1: canonical content hashes that ignore container noise (design 4.1 #2) (owner: convert).

```python
OOXML_SUFFIXES: tuple[str, ...] = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm")

VOLATILE_PARTS: tuple[str, ...] = (
    "docProps/*",
    "xl/calcChain.xml",
    "*/printerSettings/*",
    "docMetadata/LabelInfo.xml",
)
"""Glob patterns of OOXML parts excluded from H1 entirely (a starting point, not a guarantee)."""

VOLATILE_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("xl/workbook.xml", "xr:revisionPtr/@documentId"),
    ("word/document.xml", "w:rsid*"),
    ("word/settings.xml", "w:rsids"),
)
"""(part, attribute/element) pairs canonicalised away before hashing (measured Office re-save churn, 9 #6)."""

def canonical_hash(path: Path, *, suffix: str) -> CanonicalHash:
    """Return H1 for a staged file.

    OOXML (``OOXML_SUFFIXES``): sha256 over sorted ``(part name, canonical part bytes)`` with all ZIP metadata
    discarded, ``VOLATILE_PARTS`` dropped and ``VOLATILE_ATTRIBUTES`` removed; ``parts`` lists each kept
    part's digest; method ``"ooxml-parts@1"``. Everything else: sha256 of the bytes, method ``"bytes@1"``. A
    corrupt ZIP falls back to ``bytes@1`` (logged), never raises for that reason.
    """

def differing_parts(old: tuple[tuple[str, str], ...], new: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Return sorted part names added, removed or changed between two ``CanonicalHash.parts`` tuples."""
```

### `agentsync.convert.pandoc` — `src/agentsync/convert/pandoc.py` — owner: **convert**

Converter: docx/odt/rtf/html via the pypandoc_binary-bundled pandoc, invoked by ABSOLUTE path (owner:
convert).

```python
class PandocConverter:
    """Docx/odt/rtf/html via the pypandoc_binary-bundled pandoc, invoked by ABSOLUTE path.

    ``pandoc -f <fmt> -t gfm --wrap=none``; media are not extracted inline (figures are a later tier).
    Merged cells make pandoc emit HTML tables: accepted (greppable).  An encrypted/IRM docx (not a ZIP, or
    EncryptedPackage stream) raises UnreadableSourceError.  version() runs ``<pandoc> --version``.
    """
    converter_id = "pandoc-gfm"
    extensions: tuple[str, ...] = (".docx", ".odt", ".rtf", ".html", ".htm")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.xlsx` — `src/agentsync/convert/xlsx.py` — owner: **convert**

Converter: openpyxl emitter: one unit per sheet plus a 00-index unit (owner: convert).

```python
class XlsxConverter:
    """Openpyxl emitter: one unit per sheet plus a 00-index unit.

    Opens twice (data_only=True for values, False for formulas); cells render ``value `=FORMULA```; merged
    ranges propagate the anchor; integers stay integers (no ``100.0``), empty stays empty (no ``NaN``);
    heading carries the used range; pivots/charts become stub lines.  Units: ``index`` (00-index: title,
    every sheet's name, used range, column headers, distinctive terms) then ``sheet:<n>`` (file_stem
    ``NN-<sheet name>``).  Files above ``xlsx_stream_threshold_bytes`` use read_only streaming and emit
    ``summary`` units (schema + first ``max_rows_per_sheet`` rows) with a CSV sidecar.
    """
    converter_id = "xlsx-openpyxl"
    extensions: tuple[str, ...] = (".xlsx", ".xlsm")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.pptx` — `src/agentsync/convert/pptx.py` — owner: **convert**

Converter: python-pptx emitter: one WHOLE unit with slide anchors (owner: convert).

```python
class PptxConverter:
    """Python-pptx emitter: one WHOLE unit with slide anchors.

    ``<!-- Slide number: N -->`` anchor per slide, title as ``## ``, text frames in reading order, tables as
    GFM, speaker notes under ``### Notes:``.  Pictures become ``[image: <alt text>]`` lines.
    """
    converter_id = "pptx-python-pptx"
    extensions: tuple[str, ...] = (".pptx",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.pdf` — `src/agentsync/convert/pdf.py` — owner: **convert**

Converter: pdfminer.six text extraction: one WHOLE unit with page anchors (owner: convert).

```python
class PdfConverter:
    """Pdfminer.six text extraction: one WHOLE unit with page anchors.

    ``<!-- page: N -->`` anchor per page; layout analysis with fixed LAParams; a page with < 20 chars of text
    is marked ``[scanned page: no text layer]`` (OCR is a later budgeted tier). Encrypted PDFs that need a
    password raise UnreadableSourceError.
    """
    converter_id = "pdf-pdfminer"
    extensions: tuple[str, ...] = (".pdf",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.text` — `src/agentsync/convert/text.py` — owner: **convert**

Converter: plain text: decoded (utf-8, then utf-8-sig/utf-16 BOM, else latin-1) into a fenced block
(owner: convert).

```python
class PlainTextConverter:
    """Plain text: decoded (utf-8, then utf-8-sig/utf-16 BOM, else latin-1) into a fenced block.

    The body is a heading with the file name then the text inside a fenced block (``csv``/``tsv`` rendered as
    a GFM table up to max_rows_per_sheet rows).  NUL bytes -> ConversionError (binary).
    """
    converter_id = "text-plain"
    extensions: tuple[str, ...] = (".txt", ".csv", ".tsv", ".log", ".vtt", ".json", ".xml", ".yaml", ".yml")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.markdown` — `src/agentsync/convert/markdown.py` — owner: **convert**

Converter: markdown passthrough: the source's own frontmatter is stripped (kept as a fenced yaml block)
(owner: convert).

```python
class MarkdownConverter:
    """Markdown passthrough: the source's own frontmatter is stripped (kept as a fenced yaml block).

    Body = source markdown normalised to NFC/LF; an existing leading frontmatter block is moved into a
    fenced ``yaml`` block under a ``Source frontmatter`` heading so the mirror frontmatter stays the contract.
    """
    converter_id = "markdown-passthrough"
    extensions: tuple[str, ...] = (".md", ".markdown")

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.eml` — `src/agentsync/convert/eml.py` — owner: **convert**

Converter: RFC 822 message via the stdlib email package (owner: convert).

```python
class EmlConverter:
    """RFC 822 message via the stdlib email package.

    Header table (From, To, Cc, Date, Subject, Message-ID, In-Reply-To, References) then the text/plain part
    (or text/html through pandoc -> gfm); attachments listed by name, size and sha256 (their conversion is the
    mail arm's second phase). Never emits raw MIME or base64.
    """
    converter_id = "eml-stdlib"
    extensions: tuple[str, ...] = (".eml",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.convert.teams` — `src/agentsync/convert/teams.py` — owner: **convert**

Converter: Teams channel month rollup (model.TEAMS_MONTH_SCHEMA JSON) -> one WHOLE page (owner:
convert).

```python
class TeamsMonthConverter:
    """Teams channel month rollup (model.TEAMS_MONTH_SCHEMA JSON) -> one WHOLE page.

    ``# <team> / <channel> — YYYY-MM`` then one ``### <created> — <from>`` section per top-level message in
    (created, id) order with replies indented as quotes; body_html through pandoc -> gfm; deleted messages
    render ``[deleted]``. Unknown schema -> ConversionError.
    """
    converter_id = "teams-month"
    extensions: tuple[str, ...] = (".teams.json",)

    def __init__(self, cfg: ConvertConfig) -> None:
        """Bind converter options from config."""

    def version(self) -> str:
        """Version as run (emitter version + underlying library/tool version)."""

    def options(self) -> Mapping[str, OptionValue]:
        """Normalised options hashed into options_hash."""

    def convert(self, src: Path, *, name: str) -> tuple[RenderedUnit, ...]:
        """Convert one staged file; see the Converter protocol for pre/postconditions and errors."""
```

### `agentsync.slug` — `src/agentsync/slug.py` — owner: **publish**

The slugifier and mirror path rules (design 4.4 converter contract, 4.7 names) (owner: publish).

```python
MAX_PATH_CHARS = 200
"""Cap on any docs-repo-relative path, so a Windows checkout clears MAX_PATH without core.longpaths."""

WINDOWS_RESERVED_STEMS: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)

STRIP_SUFFIXES: tuple[str, ...] = (".md", ".markdown", ".teams.json", ".eml")
"""Source suffixes dropped from the mirror name (``notes.md`` -> ``notes.md``, not ``notes.md.md``)."""

def slugify(text: str) -> str:
    """Return a portable slug for ONE path segment.

    Lowercase; NFC; strip diacritics; map dashes to '-'; collapse whitespace to '-'; strip ``[ ] ( ) # { }``,
    the NTFS-illegal set ``< > : " / \\ | ? *`` and control bytes; strip trailing dots and spaces; suffix
    ``-doc`` to a Windows reserved stem (with or without extension); never empty (``"untitled"``).
    Dots inside the segment are kept (``report.v2`` stays).  Deterministic and idempotent.
    """

def mirror_name(source_name: str) -> str:
    """Return the slugged file name for a WHOLE unit: ``slugify(name)`` + ``.md`` (see STRIP_SUFFIXES)."""

def mirror_dir_name(source_name: str) -> str:
    """Return the slugged directory name for a multi-unit source: ``slugify(name)`` + ``.d``."""

def mirror_rel_path(source_id: str, rel_path: str, *, file_stem: str = "") -> str:
    """Map a source item path to its docs-repo-relative mirror path.

    WHOLE unit (``file_stem == ""``): ``mirror/<source_id>/<slug dirs>/<mirror_name(name)>``.
    Unit of a multi-unit source: ``mirror/<source_id>/<slug dirs>/<mirror_dir_name(name)>/<slug(stem)>.md``.
    Paths longer than MAX_PATH_CHARS are shortened deterministically (segment truncation + 8-hex hash).
    """

def disambiguate(path: str, stable_id: str) -> str:
    """Insert ``-<sha256(stable_id)[:8]>`` before the final ``.md`` (used on case-insensitive/NFC
    collisions)."""

def collision_key(path: str) -> str:
    """Return the key two paths collide on (NFC + casefold), as APFS and NTFS would see them."""

def is_portable_path(path: str) -> bool:
    """True when every segment is already a fixed point of the slug rules and len(path) <= MAX_PATH_CHARS."""
```

### `agentsync.publish` — `src/agentsync/publish.py` — owner: **publish**

L5 surface: docs/mirror pages, tombstones, INDEX/CHANGELOG/STATE, per-dir CLAUDE.md (owner: publish).

Every file write is temp + rename in the same directory, and skipped when the bytes are identical, so a
no-op cycle leaves the working tree untouched and ``gitops.has_changes`` is False.

```python
MIRROR_CLAUDE_MD = """\
# docs/mirror — GENERATED, DO NOT EDIT

Every page here is a pure function of one source file; edits are overwritten by the next sync.
Read `summary:` and `tokens_estimate:` in the frontmatter to decide whether to open a page; the ids and
hashes are for the pipeline.  `status: deleted` pages are tombstones (the source was deleted upstream).
Cite a page from docs/topics/ with its `rendered_sha256`.  Check docs/_sync/STATE.md before trusting a
negative result.
"""

TOPICS_CLAUDE_MD = """\
# docs/topics — curated synthesis

Every claim cites a docs/mirror/... page in the `sources:` frontmatter as
`{path: <page-relative path>, at_rendered_sha256: <64 hex>, role: primary|corroborating}`;
`entity:` is required.  A page starting with `> ⚠ STALE` is cited as of its pinned sha, never as
current.  Run the refresh queue (docs/README.md) before trusting a STALE page.
Read docs/_sync/STATE.md first: incomplete sources mean a negative answer is "not found in docs/,
and source X was incomplete", never a bare "nothing found".
"""

ROOT_CLAUDE_MD = """\
docs/INDEX.md is the map; read docs/_sync/STATE.md first.
docs/mirror/ is generated (never edit it); docs/topics/ is curated.
What changed: git -C docs log --since=<date> --stat -- mirror topics
"""

GITIGNORE = "_sync/STATE.md\n_manifest/cache/\n.sync.lock\n"

GITATTRIBUTES = "* text=auto eol=lf\n*.png binary\n*.jsonl -merge\nCHANGELOG/*.md merge=union\n"

STALE_BANNER_PREFIX = "> ⚠ STALE — sources changed since "

@dataclass(frozen=True, slots=True)
class PlannedPage:
    """One mirror file ready to write (and the manifest ``outputs`` row it implies)."""
    output_path: str  # docs-repo-relative
    unit_id: str
    text: str  # frontmatter + body
    rendered_sha256: str  # H2 (body)
    page_sha256: str  # sha256 of ``text``
    sidecars: tuple[tuple[str, bytes], ...] = ()  # (docs-repo-relative path, bytes)

@dataclass(frozen=True, slots=True)
class SourceStatus:
    """One source's line in STATE.md / INDEX.md (volatile values: STATE.md only, never committed content)."""
    source_id: str
    kind: SourceKind
    state: str
    pass_kind: PassKind | None
    enumeration_complete: bool
    baseline_complete: bool
    cursor_age_s: int | None
    cursor_fingerprint: str
    cadence_s: int
    live: int
    dataless: int
    quarantined: int
    deferred: int
    breaker: str  # "ok" | "TRIPPED until <iso> (<n> candidates)"
    auth: str  # "ok" | "REAUTH_REQUIRED <iso>"
    last_success: str | None  # UTC ISO-8601

class Publisher:
    """Writes the docs/ working tree from the manifest + conversion results.  Never commits (see gitops)."""

    def __init__(self, config: Config, manifest: Manifest) -> None:
        """Bind to the docs repo layout and the manifest."""

    def ensure_scaffold(self) -> list[str]:
        """Create dirs and the fixed files (.gitignore, .gitattributes, README.md with the refresh-queue
        script verbatim, root/mirror/topics CLAUDE.md, SYNONYMS.tsv header) if missing or different; return
        written paths."""

    def allocate_path(self, source_id: str, stable_id: str, rel_path: str, file_stem: str) -> str:
        """Return the output path for a unit: the existing ``outputs`` path if the item already owns one at
        this rel_path, else ``slug.mirror_rel_path``, disambiguated with the stable id on a collision_key
        clash with any path owned by a different item. Sticky: an existing owner never loses its path."""

    def plan_pages(self, source: SourceConfig, item: ItemRow, result: ConversionResult) -> list[PlannedPage]:
        """Build every page of one conversion (OK -> one page per unit; UNREADABLE/REFUSED/FAILED -> one stub
        page with status unreadable|refused and ``reason``), frontmatter per
        ``frontmatter.MirrorFrontmatter``."""

    def write_pages(self, item: ItemRow, pages: Sequence[PlannedPage], run_id: int) -> list[MirrorChange]:
        """Write pages + sidecars, replace the item's ``outputs`` rows, tombstone units that disappeared
        (reason ``unit-removed``), remove the old files when the item was renamed (op R); return changes.
        """

    def rewrite_frontmatter(self, item: ItemRow, run_id: int) -> list[MirrorChange]:
        """METADATA_ONLY / rename without content change: re-render frontmatter (and move paths), keep
        bodies."""

    def tombstone(
        self, source_id: str, stable_id: str, *, reason: str, run_id: int, today: str, last_commit: str | None
    ) -> list[MirrorChange]:
        """Replace every output page of the item by a tombstone stub (``[DELETED UPSTREAM] <title>``,
        ``status: deleted``, the ``git show <last_commit>:<path>`` recovery line); add ``tombstones`` rows
        with reap_after = today + tombstone_reap_days; set the item row state tombstone. Returns op D changes.
        """

    def restore(self, source_id: str, stable_id: str) -> None:
        """Reappearance: drop the item's tombstone rows and set state live (the pages are rewritten by the
        caller)."""

    def reap(self, today: str) -> list[MirrorChange]:
        """Delete tombstone files whose reap_after <= today and their rows/outputs."""

    def write_manifest_shards(self, source_ids: Sequence[str]) -> list[str]:
        """Write ``_manifest/<source_id>.jsonl`` from ``Manifest.export_shard`` for each id; delete shards of
        sources no longer configured; return written paths."""

    def write_index(self, statuses: Sequence[SourceStatus]) -> None:
        """Regenerate INDEX.md (llms.txt shape; <= 200 lines / 25 KB): H1, blockquote with the what-changed
        command, topics grouped by entity (adopted pages in their own group), per-source mirror roots, and
        ``baseline: INCOMPLETE`` for any source without a completed baseline.  Never lists the mirror tree.
        """

    def append_changelog(
        self, run_id: int, today: str, changes: Sequence[MirrorChange], report: CycleReport
    ) -> None:
        """Append one section to ``CHANGELOG/<yyyy-mm>.md`` (A|M|R|D per path, counts, breaker state) and
        regenerate CHANGELOG.md as the index of the last 30 days.  Called only when ``changes`` is non-empty.
        """

    def write_quarantine(self) -> None:
        """Regenerate ``_sync/QUARANTINE.tsv`` (source_id, path, reason) from quarantined/refused rows,
        sorted."""

    def write_state(self, report: CycleReport, statuses: Sequence[SourceStatus]) -> None:
        """Write the gitignored ``_sync/STATE.md`` (read-side contract fields of design 4.6), every cycle."""

    def write_state_snapshot(self, statuses: Sequence[SourceStatus]) -> None:
        """Write the committed ``_sync/STATE.snapshot.md`` (no cursor values; ages rounded to hours)."""

def render_tombstone(row: TombstoneRow, *, title: str, source_kind: str, source_path: str) -> str:
    """Return the full tombstone page text for a tombstone row (deterministic from the row)."""
```

### `agentsync.lints` — `src/agentsync/lints.py` — owner: **publish**

Land-gate lints: a blocking finding stops the cycle's commit (owner: publish).

```python
TOKEN_PATTERN = re.compile(r"token=|deltatoken=|Bearer ")
"""No committed file may match this (cursor / bearer token custody, design 4.7)."""

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("azure-conn-string", re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}")),
    ("generic-password", re.compile(r"(?i)\b(?:password|pwd)\s*[=:]\s*\S{6,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
)
"""Built-in content secret scan; gitleaks is used instead when on PATH (``lint_secrets`` prefers it)."""

INDEX_MAX_BYTES = 25_000

INDEX_MAX_LINES = 200

def lint_no_symlinks(repo: Path) -> list[LintFinding]:
    """SYMLINK: any symlink anywhere under the docs repo (excluding .git) — ``find docs -type l`` must be
    empty."""

def lint_mirror_frontmatter(repo: Path, paths: Sequence[str] | None = None) -> list[LintFinding]:
    """FRONTMATTER: every mirror page (or just ``paths``) parses and satisfies the mirror contract."""

def lint_paths(repo: Path, paths: Sequence[str] | None = None) -> list[LintFinding]:
    """PATH: tracked/generated paths are slug fixed points, <= 200 chars, with no NFC/case-insensitive
    twins."""

def lint_no_cache_in_git(repo: Path) -> list[LintFinding]:
    """CACHE: nothing under ``_manifest/cache/`` is tracked."""

def lint_no_tokens(repo: Path, paths: Sequence[str] | None = None) -> list[LintFinding]:
    """TOKEN: no pipeline-written file (``_manifest``, ``_sync``, INDEX, CHANGELOG, DEPENDS) matches
    TOKEN_PATTERN (blocking). Mirror pages matching it are reported non-blocking and go through the SECRET
    quarantine path, because corporate API docs legitimately contain ``Bearer ``."""

def lint_secrets(repo: Path, paths: Sequence[str]) -> list[LintFinding]:
    """SECRET: content secret scan over the given mirror pages; each hit names the page (caller quarantines it
    to an ``UNREADABLE: contains a credential`` stub instead of blocking the whole cycle: blocking=False)."""

def lint_index_budget(repo: Path) -> list[LintFinding]:
    """INDEX-BUDGET: INDEX.md <= 25 KB and <= 200 lines."""

def lint_double_conversion(samples: Sequence[tuple[Path, str]], registry: Registry) -> list[LintFinding]:
    """NONDETERMINISTIC: each (staged file, name) converted twice must yield identical rendered_sha256s."""

def run_land_gate(repo: Path, changed_paths: Sequence[str]) -> list[LintFinding]:
    """Run every repo lint (symlinks, frontmatter on changed pages, paths, cache, tokens, index budget)."""
```

### `agentsync.gitops` — `src/agentsync/gitops.py` — owner: **publish**

git via subprocess for the docs repo: init, status, one commit per cycle, recovery (owner: publish).

git is resolved once to an absolute path (launchd has a minimal PATH) and run with ``LC_ALL=C``,
``GIT_TERMINAL_PROMPT=0``, ``GIT_OPTIONAL_LOCKS=0``.  Nothing here ever pushes, fetches or touches a remote.

```python
GENERATED_PATHSPECS: tuple[str, ...] = (
    "mirror",
    "_manifest",
    "_index",
    "CHANGELOG",
    "CHANGELOG.md",
    "INDEX.md",
    "DEPENDS.tsv",
    "_sync/QUARANTINE.tsv",
    "_sync/STATE.snapshot.md",
)
"""Paths the pipeline owns and may reset on recovery."""

COMMIT_PATHSPECS: tuple[str, ...] = (
    *GENERATED_PATHSPECS,
    "topics",
    "README.md",
    "CLAUDE.md",
    "SYNONYMS.tsv",
    ".gitignore",
    ".gitattributes",
)
"""Paths a cycle commit stages (``git add -A -- <these>``); ``_sync/STATE.md`` is gitignored."""

PUBLISHED_TAG = "published"

def git_executable() -> Path:
    """Return the absolute path of git (``/usr/bin/git`` preferred); raises GitError if absent."""

def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``git -C repo *args`` with the fixed env; raises GitError on non-zero exit when ``check``."""

def ensure_repo(repo: Path) -> bool:
    """``git init -b main`` if needed; set core.precomposeunicode=true, core.quotepath=false and a local
    user.name/user.email (``agentsync``/``agentsync@localhost``) only if unset.  Returns True if created.
    Refuses (PublishError) a repo under ~/Library/CloudStorage."""

def head_sha(repo: Path) -> str | None:
    """Return HEAD's commit sha, or None for an unborn branch."""

def head_tree_sha(repo: Path) -> str | None:
    """Return ``git rev-parse HEAD^{tree}``, or None for an unborn branch."""

def has_changes(repo: Path, pathspecs: Sequence[str] = COMMIT_PATHSPECS) -> bool:
    """True when the working tree or index differs from HEAD under ``pathspecs`` (untracked included)."""

def commit_subject(changes: Sequence[MirrorChange], source_ids: Sequence[str]) -> str:
    """Return ``"sync: <A>a <M>m <R>r <D>d <comma-joined sorted source ids>"``."""

def commit_cycle(
    repo: Path, subject: str, body: str = "", pathspecs: Sequence[str] = COMMIT_PATHSPECS
) -> str | None:
    """Stage ``pathspecs`` and commit once; return the new sha, or None (and no commit) if nothing staged."""

def restore_generated(repo: Path) -> None:
    """Recovery: ``git checkout HEAD -- <generated>`` + ``git clean -fd -- <generated>`` (never topics/)."""

def tag_published(repo: Path, sha: str) -> None:
    """Force-move the lightweight ``published`` tag to ``sha`` (the tree readers should consume)."""

def tracked_files(repo: Path, pathspecs: Sequence[str] = ()) -> list[str]:
    """Return ``git ls-files -z`` paths (repo-relative, sorted)."""

def commit_body(changes: Sequence[MirrorChange], *, run_id: int, mode: str, notes: Sequence[str] = ()) -> str:
    """Return the structured commit body: per-source A/M/R/D counts, notes, then ``Agentsync-*`` trailers."""

def push_if_allowed(repo: Path, *, allow: bool, remote: str = "origin") -> str:
    """Push the branch (fast-forward only) and the ``published`` tag only when ``allow`` AND the remote
    exists; returns ``disabled`` | ``no-remote`` | ``refused: …`` | ``pushed …`` (no config key allows it yet)."""
```

W2 note: the cycle reads HEAD's ``Agentsync-Run: <run_id>`` trailer (written by ``commit_body``) in ``recover`` to
tell "the commit of the run that staged these pending cursors landed" from "HEAD moved by hand".

### `agentsync.graph.auth` — `src/agentsync/graph/auth.py` — owner: **graph-core**

MSAL public-client device-code sign-in with a Keychain-backed token cache (owner: graph-core).

Token custody: msal-extensions ``KeychainPersistence`` (service ``agentsync``, account ``msal_token_cache``).
Only if the Keychain is unavailable (e.g. no GUI session) does it fall back to a mode-0600 file under the
state dir, logged at WARNING. Tokens are never logged, printed, or written under docs/.

```python
KEYCHAIN_SERVICE = "agentsync"

KEYCHAIN_ACCOUNT = "msal_token_cache"

REAUTH_ERROR_CODES: tuple[str, ...] = (
    "invalid_grant",
    "interaction_required",
    "AADSTS50076",
    "AADSTS50173",
    "AADSTS700082",
    "AADSTS50078",
    "AADSTS53003",
)
"""MSAL errors that mean a human must sign in again: map to AuthRequiredError, never retry."""

class TokenProvider(Protocol):
    """Anything that can hand the Graph client a bearer token."""

    def get_token(self) -> str:
        """Return a valid access token or raise AuthRequiredError (never prompts)."""

@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Everything auth needs, derived from Config."""
    client_id: str
    authority: str
    scopes: tuple[str, ...]
    keychain_marker: Path
    fallback_cache: Path

@dataclass(frozen=True, slots=True)
class AuthStatus:
    """What ``agentsync login --status`` / doctor report (no secrets)."""
    signed_in: bool
    username: str | None
    tenant_id: str | None
    cache_backend: str  # "keychain" | "file-0600"
    scopes: tuple[str, ...]

def settings_from_config(config: Config) -> AuthSettings:
    """Build AuthSettings; raises ConfigError when ``[graph] client_id`` is unset."""

class MsalAuth:
    """TokenProvider backed by ``msal.PublicClientApplication`` and a persisted token cache."""

    def __init__(self, settings: AuthSettings) -> None:
        """Create the persisted cache (Keychain, else 0600 file with a WARNING) and the MSAL app."""

    @property
    def cache_backend(self) -> str:
        """``"keychain"`` or ``"file-0600"``."""

    def login_device_code(self, emit: Callable[[str], None]) -> AuthStatus:
        """Run the device-code flow: ``emit`` receives the verification message; blocks until done.

        Raises AuthError with the AADSTS code (e.g. AADSTS65001 consent required -> message names the IT
        action).
        """

    def get_token(self) -> str:
        """acquire_token_silent for the first cached account; REAUTH_ERROR_CODES or no account ->
        AuthRequiredError; never interactive, never retried."""

    def status(self) -> AuthStatus:
        """Report the cached account without network access."""

    def logout(self) -> None:
        """Remove every cached account and token from the persisted cache."""
```

### `agentsync.graph.client` — `src/agentsync/graph/client.py` — owner: **graph-core**

Microsoft Graph HTTP client over httpx: paging, delta, Retry-After, typed errors (owner: graph-core).

Error mapping (after retries): 401 -> refresh token once, then AuthRequiredError; 404 -> GraphNotFound; 410 ->
GraphGone(location=Location header or error's resync link); 400 on a request whose URL carries a ``token=`` /
``$deltatoken`` / ``$skiptoken`` -> GraphBadCursor; other 4xx -> GraphError; 429/503/504 -> honour Retry-After
(else exponential backoff capped at ``max_backoff_s``), PAUSING every request made through this client until
then, up to ``max_retries``, then GraphThrottled(retry_after). Network errors retry the same way and finally
raise GraphError(status=0, code="network"). The client never logs tokens, delta links or Authorization headers
(URLs are logged with query strings redacted).

```python
JsonObject = dict[str, Any]

def user_agent(company: str, version: str) -> str:
    """Return ``NONISV|<company>|agentsync/<version>`` (SharePoint's decorated-traffic format)."""

def redact_url(url: str) -> str:
    """Return ``url`` with its query string replaced by ``?<redacted>`` (safe to log)."""

@dataclass(frozen=True, slots=True)
class GraphPage:
    """One page of a collection response."""
    value: tuple[JsonObject, ...]
    next_link: str | None  # @odata.nextLink
    delta_link: str | None  # @odata.deltaLink (final page of a delta round only)

@dataclass(frozen=True, slots=True)
class DeltaResult:
    """A fully drained delta round."""
    items: tuple[JsonObject, ...]  # in server order, duplicates preserved (the consumer dedups by id)
    delta_link: str
    pages: int

class GraphClient:
    """Synchronous Graph client; one instance per cycle; not thread-safe."""

    def __init__(
        self,
        tokens: TokenProvider,
        *,
        base_url: str = "https://graph.microsoft.com/v1.0",
        user_agent: str,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 6,
        max_backoff_s: float = 300.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the httpx.Client (``transport`` lets tests inject respx/MockTransport)."""

    def close(self) -> None:
        """Close the underlying httpx client."""

    def __enter__(self) -> GraphClient:
        """Return self."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close."""

    def get_json(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonObject:
        """GET a relative path (joined to base_url) or an absolute Graph URL (nextLink/deltaLink, used
        verbatim)."""

    def iter_pages(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[GraphPage]:
        """Yield pages following ``@odata.nextLink`` (params apply to the first request only); the last page
        carries ``delta_link`` when the endpoint is a delta.  Consumers persist ``next_link`` to resume."""

    def delta(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        on_page: Callable[[GraphPage], None] | None = None,
    ) -> DeltaResult:
        """Drain a delta round to its final ``@odata.deltaLink``; ``on_page`` sees each page (resume points).

        Raises GraphGone (410), GraphBadCursor (400 on a token URL), GraphThrottled, AuthRequiredError; raises
        GraphError(code="no-delta-link") when the last page has neither nextLink nor deltaLink.
        """

    def download(self, path_or_url: str, dest: Path, *, max_bytes: int | None = None) -> tuple[int, str]:
        """Stream content to ``dest`` (tmp + rename) returning (size, sha256 hex).

        Follows the 302 to the pre-authenticated download URL WITHOUT the Authorization header. More than
        ``max_bytes`` -> BudgetExhaustedError and no dest file.
        """
```

### `agentsync.graph.drive` — `src/agentsync/graph/drive.py` — owner: **graph-arms**

Graph drive delta arm: OneDrive / SharePoint document libraries (owner: graph-arms).

State machine (design 4.2): S0 no cursor -> token-less FULL delta (never ``token=latest`` as a bootstrap) ->
follow nextLink, persisting the page link -> final deltaLink = pending cursor -> S3 poll with the stored link.
410 -> GraphGone -> FULL re-enumeration from the Location link (or token-less), ``cursor_reset=True``.
400 on the stored link -> GraphBadCursor -> alarm "cursor store corrupt: dropped", FULL from S0.
One cursor per drive; ``folder`` scope is filtered locally by derived path.

```python
DRIVE_SELECT = (
    "id,name,size,eTag,cTag,file,folder,package,root,parentReference,deleted,lastModifiedDateTime,"
    "createdDateTime,webUrl,remoteItem,publication"
)
"""$select for drive delta: ``file`` MUST be present or quickXorHash is dropped for every item."""

DELTA_HEADERS: dict[str, str] = {"Prefer": "deltaExcludeParent"}

@dataclass(frozen=True, slots=True)
class DiscoveredScope:
    """One candidate source found by ``discover`` (printed for a human to accept into sources.toml)."""
    kind: SourceKind
    name: str
    drive_id: str | None
    site: str | None
    web_url: str | None
    note: str  # e.g. "sharedWithMe remoteItem: needs its own cursor against the owning drive"

def resolve_drive_id(client: GraphClient, cfg: SourceConfig) -> str:
    """``drive_id="me"`` -> GET /me/drive; ``site`` -> GET /sites/{host}:/{path} then /sites/{id}/drive."""

def item_from_graph(source_id: str, raw: JsonObject, lookup: TreeLookup) -> SourceItem:
    """Map one driveItem to a SourceItem: stable_id = id; parent_id; rel_path derived from the id tree
    (``lookup`` for parents not in this batch; "" if underivable); remote_hashes.quickxor from
    file.hashes.quickXorHash; etag/ctag opaque; ``deleted`` facet -> deleted=True (name may be absent);
    extra carries web_url, package type, publication level, sensitivity label when present.
    """

def discover(client: GraphClient) -> list[DiscoveredScope]:
    """Enumerate /me/drive, /me/drive/sharedWithMe, /sites?search=* and each site's /drives (Arm 0
    discover)."""

class DriveArm:
    """SourceArm for kind ``graph_drive``."""
    source_id: str
    kind: SourceKind

    def __init__(
        self,
        client: GraphClient,
        cfg: SourceConfig,
        lookup: TreeLookup,
        *,
        save_page_link: Callable[[str | None], None] | None = None,
        resume_link: str | None = None,
    ) -> None:
        """Bind to one drive source.  ``save_page_link`` persists nextLinks (crash resume)."""

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Run one delta round; FULL when ``full`` or ``cursor`` is None (or after 410/400).

        Items include folders (is_dir=True) so the id tree stays complete; items outside ``cfg.folder`` are
        dropped after path derivation. enumeration_complete=True only for a FULL round that reached its
        deltaLink. Pending cursor = that deltaLink.
        """

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """GET /drives/{d}/items/{id}/content into ``dest_dir`` charging ``budget`` with item.size first."""
```

### `agentsync.graph.mail` — `src/agentsync/graph/mail.py` — owner: **graph-arms**

Graph mail-folder message delta arm with two-phase fetch (owner: graph-arms).

Phase 1 (detect): ``/{mailbox}/mailFolders/{folder}/messages/delta`` with ``$select=MAIL_SELECT`` only (no
body). Phase 2 (fetch): ``/{mailbox}/messages/{id}/$value`` (full MIME incl. attachments) for the diff only.
``@removed`` means deleted OR moved out of the folder: ``confirm_removed`` does the cross-folder check.

```python
MAIL_SELECT = ",".join(  # ... (see source)
"""Phase-1 $select: omitting it returns ~9 KB of body per message during change detection (measured)."""

def mailbox_root(cfg: SourceConfig) -> str:
    """``"me"`` -> ``/me``; a UPN -> ``/users/{upn}`` (shared mailbox; needs Mail.Read.Shared)."""

def message_rel_path(raw: JsonObject) -> str:
    """``YYYY/MM/YYYY-MM-DD-<from>-<subject>.eml`` from receivedDateTime (UTC), sender name, subject (raw
    text; publish slugs it)."""

def item_from_message(source_id: str, raw: JsonObject) -> SourceItem:
    """Map a message to a SourceItem: stable_id = message id; etag = changeKey; size 0 (unknown until fetch);
    mtime_ns from receivedDateTime; extra: internet_message_id, conversation_id, has_attachments, from,
    subject. ``@removed`` -> deleted=True with extra["removed_reason"]."""

class MailArm:
    """SourceArm for kind ``graph_mail`` (one folder, one cursor)."""
    source_id: str
    kind: SourceKind

    def __init__(self, client: GraphClient, cfg: SourceConfig) -> None:
        """Bind to one mail folder source."""

    def confirm_removed(self, message_id: str) -> str:
        """Cross-folder check for ``@removed``: GET the message; 404 -> "deleted"; found ->
        "moved:<folderId>"."""

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Phase 1 delta round (FULL when no cursor / full / after 410 or 400); every ``@removed`` goes
        through ``confirm_removed`` and is emitted deleted=True with extra["removed"] = "deleted" |
        "moved:<id>"."""

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Phase 2: download MIME to ``<dest_dir>/<id-hash>.eml`` with max_bytes = budget.remaining_bytes,
        then charge the actual size."""
```

### `agentsync.graph.teams` — `src/agentsync/graph/teams.py` — owner: **graph-arms**

Graph Teams channel message delta arm -> monthly rollup items (owner: graph-arms).

Channel delta returns top-level messages; replies come from ``/messages/{id}/replies`` for each changed
message. Messages are merged into a per-channel store ``<state_dir>/teams/<source_id>/<YYYY-MM>.json`` (schema
``model.TEAMS_MONTH_SCHEMA``, written tmp + rename, keys sorted). Each touched month is one SourceItem.

```python
def month_stable_id(channel_id: str, month: str) -> str:
    """Return the rollup identity ``f"{channel_id}:{month}"`` (month = ``YYYY-MM``)."""

class TeamsArm:
    """SourceArm for kind ``graph_teams`` (one channel, one cursor)."""
    source_id: str
    kind: SourceKind

    def __init__(self, client: GraphClient, cfg: SourceConfig, store_dir: Path) -> None:
        """Bind to one channel; ``store_dir`` = StatePaths.teams_store / source_id."""

    def scan(self, cursor: str | None, *, full: bool) -> ScanResult:
        """Delta round over ``/teams/{t}/channels/{c}/messages/delta``; merge messages + replies into month
        files; emit one item per touched month: name ``YYYY-MM.teams.json``, rel_path = name, size = file
        size, remote_hashes.sha256 = sha256 of the month file (comparable only to itself), mtime_ns = max
        lastModifiedDateTime. A FULL round also emits every stored month (so absence works)."""

    def fetch(self, item: SourceItem, dest_dir: Path, budget: ByteBudget) -> FetchResult:
        """Copy the month file into ``dest_dir`` charging ``budget``."""
```

### `agentsync.curate` — `src/agentsync/curate.py` — owner: **curate**

L4 curation contract: topics/ ``sources:`` frontmatter, DEPENDS.tsv, refresh queue, STALE banners (owner:
curate).

Path convention (adapts design 4.5 to docs/ being its own repo): DEPENDS.tsv columns 1-2 are relative to the
DOCS REPO ROOT (``topics/…``, ``mirror/…``) and the refresh-queue script runs from the docs repo root.

```python
DEPENDS_HEADER = "page\tsource\tpinned_sha\trole"

BY_ENTITY_HEADER = "entity\tpage"

ROLES: tuple[str, ...] = ("primary", "corroborating")

HAND_WRITTEN = "hand-written"

VERDICTS: tuple[str, ...] = (
    "STALE",
    "SOURCE-DELETED",
    "SOURCE-UNREADABLE",
    "MISSING-OR-UNPARSEABLE",
    "UNPINNED",
    "BAD-PIN",
    "MALFORMED",
)

STALE_BANNER = "> ⚠ STALE — sources changed since {date}; see DEPENDS.tsv"

RETIRED_BANNER = "> ⚠ SOURCE RETIRED — {reason}"

REFRESH_QUEUE_SH = r"""#!/bin/sh  # ... (see source)
"""The design 4.5 script, verbatim except the default TSV path (docs repo root).  Written into README.md."""

@dataclass(frozen=True, slots=True)
class TopicSource:
    """One ``sources:`` entry as written (path is PAGE-relative)."""
    path: str
    at_rendered_sha256: str
    role: str

@dataclass(frozen=True, slots=True)
class TopicPage:
    """Parsed frontmatter of one curated page."""
    path: str  # docs-repo-relative, e.g. topics/clients/acme/acme-commercial.md
    kind: str | None
    entity: str | None
    sources: tuple[TopicSource, ...]
    depends_on_pages: tuple[str, ...]
    aliases: tuple[str, ...]
    provenance: str | None  # "hand-written" for adopted pages (exempt from STALE, absent from DEPENDS.tsv)
    adopted_at: str | None

@dataclass(frozen=True, slots=True)
class RefreshVerdict:
    """One refresh-queue output row (same vocabulary and order as the shell script: ``sort -u``)."""
    verdict: str
    page: str
    source: str
    detail: str = ""

    def line(self) -> str:
        """Render exactly as the shell script prints it (tab-separated)."""

def iter_topic_pages(layout: DocsLayout) -> list[str]:
    """Docs-repo-relative paths of every curated page: ``topics/**/*.md`` except CLAUDE.md and INDEX.md,
    sorted."""

def parse_topic_page(layout: DocsLayout, rel_path: str) -> TopicPage:
    """Parse one curated page's frontmatter; raises CurateError (no/invalid frontmatter, bad ``sources:``
    shape)."""

def normalise_source_path(page_rel: str, source_rel_to_page: str) -> str:
    """Resolve a page-relative ``sources:`` path to docs-repo-relative; CurateError if it escapes docs/."""

def generate_depends(layout: DocsLayout) -> tuple[list[DependsRow], list[tuple[str, str]], list[LintFinding]]:
    """Parse every curated page -> (DEPENDS rows sorted by (page, source), (entity, page) rows sorted, lint
    findings: CURATE-PARSE, MISSING-ENTITY, BAD-ROLE, UNPINNED/BAD-PIN rows are still emitted for the
    queue)."""

def write_depends(layout: DocsLayout, rows: Sequence[DependsRow]) -> bool:
    """Write DEPENDS.tsv (header + rows, LF, trailing newline) if different; return True if written."""

def write_by_entity(layout: DocsLayout, rows: Sequence[tuple[str, str]]) -> bool:
    """Write ``_index/by-entity.tsv`` if different; return True if written."""

def refresh_queue(layout: DocsLayout) -> tuple[int, list[RefreshVerdict]]:
    """Python twin of REFRESH_QUEUE_SH: returns (rc, verdicts) with identical semantics (rc 0/1/2)."""

def apply_stale_banners(layout: DocsLayout, verdicts: Sequence[RefreshVerdict], today: str) -> list[str]:
    """Deterministic linter: prepend STALE_BANNER (after the frontmatter) to pages with a STALE verdict,
    remove it from pages that are fresh again, never touch hand-written pages; return changed paths."""

def apply_retired_banners(
    layout: DocsLayout, rows: Sequence[DependsRow], retired: Mapping[str, str]
) -> list[str]:
    """Pages citing ``mirror/<source_id>/`` of a retired source (``retired``: source_id -> reason) get
    RETIRED_BANNER; the banner goes when no retired source is cited.  Returns changed paths (cycle step 8)."""

def lint_unlisted_pages(layout: DocsLayout, rows: Sequence[DependsRow]) -> list[LintFinding]:
    """UNLISTED: every curated page that is not ``provenance: hand-written`` appears in DEPENDS.tsv
    (blocking=False for the sync; the ``agentsync lint`` command reports it as an error)."""

def adopt_pages(src_dir: Path, layout: DocsLayout, adopted_at: str) -> list[str]:
    """Copy an existing hand-made docs tree into ``topics/`` stamping ``provenance: hand-written``,
    ``adopted_at``, ``sources: []``; refuses to overwrite; returns created docs-repo-relative paths."""
```

### `agentsync.ops.lock` — `src/agentsync/ops/lock.py` — owner: **ops**

Single-writer ``flock`` lock and the outward liveness heartbeat (owner: ops).

```python
@dataclass(frozen=True, slots=True)
class LockInfo:
    """Body of the lock file: ``<pid> <boot_time> <iso8601> <label>``."""
    pid: int
    boot_time: int  # kern.boottime seconds
    started_at: str  # UTC ISO-8601
    label: str  # e.g. "poll", "reconcile", "cli"

    def render(self) -> str:
        """Return the one-line body."""

    @classmethod
    def parse(cls, text: str) -> LockInfo | None:
        """Parse a body; None if empty or malformed."""

@dataclass(frozen=True, slots=True)
class LockAcquisition:
    """Result of a successful acquire."""
    broke_stale: bool  # previous body named a dead pid or another boot: force a FULL pass this cycle
    previous: LockInfo | None

def boot_time() -> int:
    """Return kern.boottime seconds (sysctl) — distinguishes a pid reused across reboots."""

def pid_alive(pid: int) -> bool:
    """True if ``pid`` exists (os.kill(pid, 0); EPERM counts as alive)."""

class SingleWriterLock:
    """``flock(LOCK_EX|LOCK_NB)`` on StatePaths.lock.  The body is truncated on clean release."""

    def __init__(self, path: Path, label: str) -> None:
        """Bind to the lock path (parent dir created 0700)."""

    def acquire(self) -> LockAcquisition:
        """Take the lock or raise LockHeldError naming the holder's body.  A non-empty body left by a dead pid
        or another boot is 'stale': logged, overwritten, reported as broke_stale=True."""

    def release(self) -> None:
        """Truncate the body and release (idempotent)."""

    def __enter__(self) -> LockAcquisition:
        """acquire()."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """release()."""

def write_heartbeat(
    path: Path,
    source_id: str,
    *,
    run_id: int,
    pass_kind: PassKind | None,
    enumeration_complete: bool,
    ok: bool,
    auth_state: str,
    now_iso: str,
) -> None:
    """Update one source's entry in heartbeat.json atomically (tmp + rename, sorted keys): last_attempt_at,
    and last_success_at only when ``ok``; also run_id, pass_kind, enumeration_complete, auth_state."""

def read_heartbeat(path: Path) -> Mapping[str, Mapping[str, object]]:
    """Return {source_id: entry}; {} if the file is missing."""
```

### `agentsync.ops.launchd` — `src/agentsync/ops/launchd.py` — owner: **ops**

LaunchAgent plists for the two jobs (poll, reconcile) and their install/uninstall (owner: ops).

Per-user agents only (``~/Library/LaunchAgents``, ``launchctl bootstrap gui/<uid>``): no admin rights needed.
Keys (design 4.7): ProcessType Background, ThrottleInterval 60, RunAtLoad true, LimitLoadToSessionType Aqua,
MaterializeDatalessFiles false (hydration is fail-closed; ``materialise`` opts in per read), a fixed PATH,
logs under Config.log_dir.

```python
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One LaunchAgent."""
    label: str
    program_arguments: tuple[str, ...]
    stdout_path: Path
    stderr_path: Path
    start_interval_s: int | None = None
    start_calendar: Mapping[str, int] | None = None  # e.g. {"Minute": 7} for hourly at :07
    environment: Mapping[str, str] = field(default_factory=dict, hash=False)
    run_at_load: bool = True
    materialize_dataless_files: bool = False
    throttle_interval_s: int = 60

def program_arguments(config: Config, mode: str) -> tuple[str, ...]:
    """Absolute interpreter + ``-m agentsync sync --mode <mode> --config <abs path>`` (no PATH lookup)."""

def poll_spec(config: Config) -> AgentSpec:
    """``<prefix>.poll``: StartInterval = config.poll_interval_s."""

def reconcile_spec(config: Config) -> AgentSpec:
    """``<prefix>.reconcile``: StartInterval = config.reconcile_interval_s (hourly by default)."""

def render_plist(spec: AgentSpec) -> bytes:
    """Return the XML plist bytes (plistlib, sort_keys=True: deterministic)."""

def plist_path(label: str) -> Path:
    """Return ``~/Library/LaunchAgents/<label>.plist``."""

def install(spec: AgentSpec) -> Path:
    """Write the plist (0644), ``launchctl bootout`` any loaded copy, then ``launchctl bootstrap gui/<uid>``;
    creates log dirs; returns the plist path.  Raises OSError / subprocess errors with launchctl's stderr."""

def uninstall(label: str) -> bool:
    """``launchctl bootout gui/<uid>/<label>`` (ignore not-loaded) and remove the plist; True if anything
    removed."""

def is_loaded(label: str) -> bool:
    """True when ``launchctl print gui/<uid>/<label>`` succeeds."""
```

### `agentsync.ops.doctor` — `src/agentsync/ops/doctor.py` — owner: **ops**

``agentsync doctor``: preflight checks with a concrete fix per failure (owner: ops).

```python
class Severity(enum.StrEnum):
    """How bad a failed check is."""
    ERROR = "error"  # sync will fail or be wrong
    WARN = "warn"  # sync works, something is degraded
    INFO = "info"

@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check."""
    name: str
    ok: bool
    detail: str
    severity: Severity = Severity.ERROR
    fix: str | None = None  # the exact command or setting that fixes it

def run_checks(config: Config) -> list[CheckResult]:
    """Run every check, in a fixed order, never raising for a single failed check.

    python >= 3.11; git absolute path; pandoc (configured or bundled) runs and reports a version; docs_repo
    outside CloudStorage, a git repo (or creatable), no symlinks; state_dir exists with mode 0700 and the db
    0600; each local/inbox source root is listable (EPERM => "grant Full Disk Access to <interpreter>"),
    sentinel present, File Provider root (volume UUID readable); materialisation policy readable; graph:
    client id set, token cache backend (Keychain vs file), signed in (no network); disk free >= 2 GiB on state
    and docs volumes; launchd agents loaded (WARN if not).
    """

def format_results(results: list[CheckResult]) -> str:
    """Render results as aligned text lines ``[ok|FAIL|warn] name — detail (fix: ...)``."""
```

### `agentsync.cycle` — `src/agentsync/cycle.py` — owner: **integrator (W2)**

One sync cycle, in the durability order of design 4.7 (owner: integrator — signatures only until W2).

Order: fail_closed -> lock -> open manifest -> recover -> sync_sources -> per live source: scan (pending
cursor) -> classify phase 1 -> persist observations + pending cursor (one transaction) -> materialise within
budget -> H1 -> convert via cache -> H2 cutoff -> plan/write pages -> tombstones (unless breaker) -> curate
(DEPENDS, banners) -> INDEX/CHANGELOG/QUARANTINE/shards -> land-gate lints -> ONE git commit if anything
changed -> tree_sha -> promote cursors -> heartbeat -> STATE.md -> release lock.

```python
class RecoveryAction(enum.StrEnum):
    """What ``recover`` found and did before any source was touched."""
    NONE = "none"
    RESET_GENERATED = "reset_generated"  # tree_sha != HEAD^{tree}: died between publish and commit
    ADOPT_PENDING = "adopt_pending"  # commit landed but cursors not promoted: promote pending
    DISCARD_PENDING = "discard_pending"  # died before publish: pending cursors discarded

def build_arms(
    config: Config, manifest: Manifest, client: GraphClient | None, *, persist_page_links: bool = True
) -> dict[str, SourceArm]:
    """Instantiate one arm per live source (Graph kinds skipped with a reason when ``client`` is None)."""

def recover(config: Config, manifest: Manifest) -> RecoveryAction:
    """Compare manifest tree_sha / pending cursors with git HEAD and repair (design 4.7 recovery)."""

def run_cycle(
    config: Config,
    *,
    mode: CycleMode,
    only: Sequence[str] = (),
    now: Callable[[], datetime] | None = None,
    client: GraphClient | None = None,  # W2: inject a GraphClient (tests); default built from [graph]
    budget_bytes: int | None = None,  # W2: override every source's per-cycle materialise budget
    materialise_paths: Sequence[Path] = (),  # W2: restrict the work queue to these files (materialise)
    accept_deletions: Sequence[str] = (),  # W2: operator-asserted deletion: clear breaker, apply removals
) -> CycleReport:
    """Run one cycle under the single-writer lock; returns the report (never raises for per-source failures).

    Raises LockHeldError (CLI exit 75), ConfigError, ManifestSchemaError.  AuthRequiredError is caught:
    no cursor advances, STATE.md/heartbeat record ``auth: REAUTH_REQUIRED``, report.auth_required=True.
    DRY_RUN classifies only: no materialise, no writes under docs/, no commit, no cursor change.
    """

def source_statuses(
    config: Config, manifest: Manifest, *, now: datetime, reports: dict[str, _SourceAcc] | None = None
) -> list[SourceStatus]:
    """One SourceStatus per configured source (STATE.md / INDEX.md / ``agentsync status``)."""
```

### `agentsync.cli` — `src/agentsync/cli.py` — owner: **integrator (W2)**

``agentsync`` command line (owner: integrator).

Subcommands: init [--docs-repo PATH] [--source-local PATH ...] · sync [--once] [--mode poll|reconcile|dry_run]
[--dry-run] [--source ID ...] · reconcile [--source ID ...] [--accept-deletions] · status · doctor · lint ·
refresh-queue · materialise [--budget BYTES] [PATH ...] · adopt SRC_DIR · migrate · graph
login|logout|whoami|discover (also top-level login · logout · whoami · discover) · install-agent [--interval
SECONDS] [--reconcile-interval SECONDS] · uninstall-agent.  ``sync`` is always one cycle (the launchd agents
run ``sync --mode <m> --config <abs>``).  Every subcommand accepts ``--config PATH`` (default
~/agent-context/sources.toml) and ``-v/--verbose``, before or after the subcommand.

```python
EXIT_OK = 0

EXIT_FAILED = 1  # a source failed, a blocking lint fired, or refresh-queue found rows

EXIT_USAGE = 2

EXIT_LOCK_HELD = 75  # EX_TEMPFAIL: another cycle holds the lock; logged "skipped: lock held"

EXIT_REAUTH = 77  # EX_NOPERM: auth REAUTH_REQUIRED

EXIT_CONFIG = 78  # EX_CONFIG: sources.toml invalid

def build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser for every subcommand."""

def main(argv: Sequence[str] | None = None) -> int:
    """Entry point (console script ``agentsync``); returns the process exit code."""
```
