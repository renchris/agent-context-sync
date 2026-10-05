---
status: in-progress
---

# Plan — agentsync KISS: fewer options, a loop no agent can stop halfway

Scope (frozen, 2026-10-04): reduce agentsync's flags, config and manual steps to the minimum, and make the full loop (sync, skill, topic pages, checkpoint, baseline questions) automatic or enforced on a new Mac, so a low-effort agent cannot stop partway.

Scope (grown, 2026-10-05): +the corporate field report's fixes N1-N16 (docs/research/corporate-setup-feedback-2026-10-05.md § 3) as wave WF before W5, and its W5 carry list folded into W5.

Source: a 49-agent workflow on 2026-10-04 (six surface readers, three independent designs — deletion-first, mechanism-first, one-verb — a synthesis, two refuting skeptics per change, a final pass). All 19 changes survived; 11 were refuted as written and are kept here in the skeptic's safer form. Run `wf_cb7df7ea-7c0`.

## Phase 0 — Agent Team Orchestration

- **Execution locus per wave:** W1–W5 each **S** (one dispatched handoff session per wave, the default). Order: W1 → W2 → (W3 ∥ W4) → WF (field fixes, added 2026-10-05) → W5. W3 and W4 share only `docs/design/CONTRACTS.md`, in different sections; land the smaller diff first.
- **W1 locus changed to an in-process Workflow (2026-10-04):** the capacity gate refused a net-new session
  (memory compressor 54% of limit, over its 50% ceiling, the level that preceded past watchdog panics), and a named
  teammate is also a new process. Workflow agents run inside the lead's process: sequential build agents in the
  worktree `.worktrees/kiss-w1`, a fresh reviewer per change, a fix pass, then the full gate. The lead lands it. Run
  `wf_bd61960e-f15`. Later waves go back to **S** when the gate admits. W2 also ran in-process (`wf_d4508a0a-f86`): the active-session term
  refused the fire (8 mid-turn at a ceiling of 8).
- **Lead:** the originating session (pane 137) fires each wave with `--notify-back`, collects, verifies by content on `main`, then fires the next. Lead context budget: stay under 50%; recycle between waves if past 35% while waiting.
- **Each wave session:** its own worktree off `origin/main`; full gate before landing: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest -q`; lands with `/ship`.
- **Freeze test:** W1 pins TODAY's CLI/installer surface (`test_cli_surface_is_frozen`, `test_install_options_are_frozen`); every later wave edits the pinned dict in the same commit as its cut.

## Decision recorded

- **Curation waits for confirmed baseline questions (hard gate).** The plan's own rule ("Before the first topic page", 2026-10-02, revised 2026-10-03) already says the baseline runs before the first topic page, so `next_step` withholds the curation work list until `_eval/` is `status: confirmed` and a `before` results file exists, and prints a `WAITING ON YOU:` line every session meanwhile. The workflow rated the alternative (curate right after the draft, warn that the baseline was skipped) as losing the comparison; switching later is a one-rule change in `loop.py`.

## Answer

The root cause is that the loop's order exists only in prose. The one command every agent runs, `sync`, stops at "converted N" and never names the next step.

The fix has two parts:
- **One instruction line.** `sync`, `curate` and `status` each end with one NEXT line worked out from the docs repo's actual state.
- **Nothing left to remember.** Every step agents skip happens inside `sync` or `install.sh`: writing the skill, creating the mail inbox, schema migration, history compaction, the curation checkpoint and the lint check.

Then the surface shrinks:
- 9 visible commands; `sync`, `curate` and `status` take no visible options.
- A config template that holds only your folders and one archive line.
- A 3-step setup prompt that keeps following NEXT until you confirm the baseline questions.

All 19 reviewed changes survive in 5 waves. 11 were refuted as written and kept with the reviewer's fix, mostly hiding old commands rather than deleting them. One call is yours: whether curation waits for your confirmation.

## End state

Any Mac, at any agent effort level: the agent runs `~/.local/bin/agentsync sync` and does what its last line (NEXT) says. It repeats until NEXT says the session is done or prints a `WAITING ON YOU:` line naming an operator command.

What runs automatically:
- `install.sh` always runs the first sync and ends on that same NEXT line.
- Every interactive `sync` migrates the database, waits for a running sync instead of exiting 75, and runs compaction when it is due. It also writes the skill to `~/.claude/skills` (and to `$CLAUDE_CONFIG_DIR/skills` when that is set), keeps the inbox, and moves the `curated` checkpoint once curation is lint-clean.

What is left to choose:
- Visible commands: sync, curate, status, add-source, accept-deletions, adopt, purge, hold, offboard. Every retired command still parses but is hidden.
- `sources.toml` holds only the folders plus one commented archive line.
- The setup prompt is 3 steps and stops at the baseline-confirmation wait, not at "background sync is on".
- The generated guides carry one procedure, with absolute binary paths and paths relative to the docs repo.

A pinned freeze test makes any new flag, key or command a deliberate test edit.

Each wave is one dispatched session. Run the waves in order. W3 may run alongside W4: their only shared file is CONTRACTS.md, and they edit different sections of it.

## W1 Engine: sync does every automatic step itself — DONE (2026-10-04)

Landed on `main` at `0004617` (10 commits, `839396c..0004617`): `839396c` freeze pins (K19a), `90bd05b` skill
written by every sync (K03), `abc8c32` auto-migration, interactive lock wait, due reconcile (K12), `0e01f3b`
checkpoint blockers and docs-root sources (K10), `f992312` automatic checkpoint (K06), then five fixes from the
fresh-context review: `a63ed88`, `cce059a`, `05d92c4`, `f990351`, `0004617`. Gate after rebase: ruff, format, mypy
clean; pytest 1907 passed, 2 skipped.

Learnings:
- The manifest is `manifest.sqlite`, so the pre-migration copy is `manifest.sqlite.pre-v<N>` (excluded from Time
  Machine, deleted by the next unblocked cycle and by purge). The sqlite backup API hangs when the source connection
  holds its own write lock, so the copy is taken just before `BEGIN IMMEDIATE`; versions are re-read under the lock.
- Test isolation: tests that call `monkeypatch.undo()` also undid the shared HOME isolation, and one run wrote a
  real `~/.claude/skills/agentsync-docs` pointing at a pytest tmp dir (removed by the lead). `conftest._isolate_home`
  now uses its own `MonkeyPatch.context()`; regression `test_skill.py::test_monkeypatch_undo_keeps_home_isolated`.
- argparse `help=SUPPRESS` prints `==SUPPRESS==` in the command list on Python 3.11, so hidden commands pass no
  `help=` at all; `add(..., hidden=True)`. The freeze pin counts a parser with no help entry as hidden.
- `README.md` still names `install-skill` and `checkpoint` (both now hidden aliases); W3/W5 rewrite those lines.
- A machine reaper SIGKILLs long pytest runs at times; run the full suite in the background and re-run killed chunks.

## W2 Loop surface: one NEXT line, one curate, one status — DONE (2026-10-04)

Landed on `main` at `b262361` (8 commits, `4c5a596..b262361`): `4c5a596` accept-deletions SOURCE (K13a), `4a646a9`
`loop.next_step` and the NEXT line after interactive sync and status (K01), `3890be1` one `curate` (K09), `8d622f2`
one read-only `status` (K08a), then four fixes from the fresh-context review (13 findings, none rejected): `102c71e`,
`a5d17c1`, `5ea97d5`, `b262361`. Gate: ruff, format, mypy clean; pytest 1960 passed, 2 skipped. Run
`wf_d4508a0a-f86` (in-process Workflow again: the capacity gate refused the fire, 8 sessions mid-turn at a ceiling of 8).

Learnings for W3-W5:
- `status` honors `AGENTSYNC_NO_NEXT_HINT=1` too (no NEXT, WAITING or note lines), and the hidden `doctor` alias
  drops status and policy detail, so install.sh's doctor step keeps one NEXT and never copies `exclude_label_names`
  into install.out. W5 K02 runs status with the variable unset and lifts its NEXT.
- The skill check is a FAIL only when a copy is missing or stale, its folder is not writable, and a sync already ran
  with this build (`Manifest.last_run_started` vs the mtime of `sys.prefix/pyvenv.cfg`); otherwise an info line.
  A FAIL on any missing skill would block install.sh, whose doctor step runs before the first sync.
- The installed-commit WARN reads install.sh's stamp `<sys.prefix>/.agentsync-install-source` and the checkout's
  HEAD from its files, never by running git. No stamp, no line.
- Rule 1 takes doctor's fixes through `next_step(fixes=)`, but NEXT never copies a fix: it points at the `[FAIL]` line.
  Rule 2 names `~/src/agent-context-sync/scripts/install.sh --list-folders` (the installed binary does not know its
  checkout). Rule 8 also needs confirmed baseline questions. Rule 9 counts refresh rows plus uncovered pages, never
  ADDED-since-checkpoint (it shrinks only when the checkpoint moves, so rule 9 would repeat forever).
- An incomplete FULL local pass (no folder access, empty cloud folder, missing root) is a WAITING line, not rule 3
  (`Manifest.last_source_pass`); an OS-refused download cannot be told from a budget deferral on disk, so only
  over-budget files get the `materialise` wait.
- `curate` and rule 7 check blockers from the manifest's `checkpoint_pending` base (or HEAD with uncommitted topic
  pages), not the `curated` tag, which keeps the last session's pages in scope. A blocking finding becomes NEXT.
- setup-report runs `cli._status_checks(offline=True)` once: no TCC canary, no NEXT, no policy detail.
- W4 K13b still owns the incomplete-enumeration fix string at `ops/doctor.py` (`sync --mode reconcile --source X`).
- K13a note: the compaction fix names `sync --mode reconcile`, not accept-deletions, which waits for the lock
  (`run_cycle(wait_for_lock=True)`) because launchd never re-runs an operator's exit 75.

## W3 What agents read: one procedure in every guide

Status: upcoming.

Files: `src/agentsync/publish.py`, `src/agentsync/skill.py`, `src/agentsync/curate.py (REFRESH_QUEUE_SH only)`, `docs/design/CONTRACTS.md (§14)`, `tests/test_publish.py`, `tests/test_skill.py`, `tests/test_curate.py`, `tests/test_frontmatter.py`

### K07 (rewrite-doc, M)

Root CLAUDE.md and AGENTS.md carry one procedure:
1. Run `~/.local/bin/agentsync sync`.
2. Do what NEXT says, and repeat.
3. To look things up: _sync/STATE.md, INDEX.md, `rg topics/`, then `rg mirror/`.
4. Never open _eval/answers.md or _eval/results-* to answer a question.
5. The authoring rules, moved unchanged from the topics seed.
Then the inbox line. BOUNDARY_TEXT stays verbatim after the procedure.

Rules for these files:
- paths are relative to the repo (no docs/ prefix, no `git -C docs`);
- the binary is always `~/.local/bin/agentsync`;
- the archive and snapshot lines appear only when archive = true.

skill.py's skill_text is generated from the same procedure constant, plus the docs-path header and the Baseline section. Its SYNONYMS step and tmp-rename step are dropped.

topics/CLAUDE.md becomes a 2-line pointer, and the current seed's sha is added to _TOPICS_CLAUDE_MD_PRIOR_SHA256. Stop seeding SYNONYMS.tsv, and INDEX lists it only if the file exists. Drop the .agentsync-*.tmp ritual; the gitops exclusion stays.

Docs README:
- keep one single-writer line, reworded to 'agentsync on this Mac (principal: …)';
- keep the principal owner line;
- the delete procedure becomes `agentsync offboard --confirm <docs> --purge-data`, replacing uninstall-agent and logout.

STATE.md's staleness line says 'run ~/.local/bin/agentsync sync first'. The REAUTH/login line prints only when Graph sources exist.

Record the SYNONYMS.tsv departure in CONTRACTS §14.

- **Where:** src/agentsync/publish.py:65-124, 143, 160-207, 618-630, 668-705, 1420, 1579-1592; src/agentsync/skill.py; src/agentsync/policy.py:653-662 (BOUNDARY_TEXT, unchanged)
- **Why:** The three guidance copies disagree. Every path in the auto-loaded guide is wrong from inside docs/. A bare `agentsync` fails because install.sh leaves ~/.local/bin off PATH. A Copilot agent sees a 6-line map with no procedure and no answer-key warning. Fixes from the votes:
- the governance-required single-writer line and delete procedure are kept;
- the skill text is folded into the same constant;
- the binary path is fixed, and W5 pins uv's bin dir so it is always true.
- **Risk:** BOUNDARY_TEXT only moves position. A hand-edited topics/CLAUDE.md is kept. A half-written page committed without the tmp ritual is caught by checkpoint_blockers and recommitted by the next sync.

### K08b (rewrite-doc, S)

STATE.md gains a '## Next' block at the top, rendered from loop.next_step(). INDEX.md prints 'Topics: none yet; run ~/.local/bin/agentsync sync and follow NEXT' when no curated page exists.

- **Where:** src/agentsync/publish.py:1380-1428 (_topic_lines returns [] today), 1566-1600 (write_state)
- **Why:** STATE.md is the file agents read first, and today it says nothing past sync. An INDEX with no Topics section makes the mirror look like the whole system.
- **Risk:** None. The block is computed by the same function status uses, so the two cannot drift.

### K09b (remove, S)

Delete:
- REFRESH_QUEUE_SH;
- the awk script embedded in the docs README, and its glossary;
- the topics guide's 'Run the refresh queue' line.
Fix the glossary line that calls MISSING-OR-UNPARSEABLE 'a bug in the generator' (from K10).

- **Where:** src/agentsync/curate.py:54-81; src/agentsync/publish.py:83, 187-200 (glossary at 198)
- **Why:** It is a second, partial work list that shows 0 rows on a fresh install; curate (W2) replaced it.
- **Risk:** Low; only the tests that pin the awk script are deleted.

Tests that prove it:

- test_generated_guides_use_absolute_binary_and_repo_relative_paths: no generated guide and no SKILL.md contains a docs/ prefix or 'git -C docs', and every 'agentsync ' mention is '~/.local/bin/agentsync'
- BOUNDARY_TEXT is present byte for byte in root CLAUDE.md, AGENTS.md and mirror/CLAUDE.md
- AGENTS.md carries the full procedure and the _eval answer-key warning. The archive lines appear only with archive = true.
- An unedited topics/CLAUDE.md at the current seed upgrades to the pointer; a hand-edited one is kept
- A fresh repo seeds no SYNONYMS.tsv and INDEX has no SYNONYMS row; an existing file is left alone
- STATE.md opens with '## Next' equal to loop.next_step(). INDEX shows 'Topics: none yet' with no curated page. The REAUTH line appears only with Graph sources.
- The docs README keeps the single-writer and principal lines (test_publish.py:84/239) and names offboard, not logout
- test_publish.py:236, 247-253, 808 and 1132-1134 are updated; the awk-pinning tests are removed

## W4 CLI surface: fewer verbs, flags and config keys

Status: upcoming.

Files: `src/agentsync/cli.py`, `src/agentsync/config.py`, `src/agentsync/ops/doctor.py`, `src/agentsync/ops/launchd.py`, `src/agentsync/cycle.py (messages 388, 785)`, `src/agentsync/governance.py (keep-days floor)`, `scripts/install.sh (only 1314-1343 and 1476-1480)`, `docs/deploy/README.md (§What needs IT, 79-92 only)`, `docs/design/CONTRACTS.md (§16.10, §16.16, CLI rows 3219, 3325-3329, 4226-4239)`, `tests/test_cli.py`, `tests/test_config.py`, `tests/test_ops_doctor.py`, `tests/test_ops_launchd.py`, `tests/test_it_request.py`, `tests/test_setup_report.py`, `tests/test_install_oneshot.py (stub at 308)`, `tests/test_deploy_pack.py (stub at 233)`, `tests/test_launcher.py (453, 589)`, `tests/test_contracts.py`

### K05 (default, S)

New helper config.ensure_inbox(config_path). It creates ~/agent-context/inbox with mode 0700 and appends inbox_source_table only when no kind="inbox" source exists. add-source and the hidden init both call it.

Delete `add-source --inbox`.

install.sh's HAVE_SOURCES counts only [[source]] tables whose kind is not inbox. A config with only the inbox therefore still gets 'choose a folder to sync' and exit 1 under --confirm-install-agent.

- **Where:** src/agentsync/config.py:608-616, 724-725; src/agentsync/cli.py:210-216, 554-559, 591; scripts/install.sh:1340-1343
- **Why:** On a zero-IT Mac the inbox is the only route for mail and Teams messages. No install path creates it today, and the live sources.toml has none. Fix from the refuting vote: an always-present inbox would otherwise count as 'has a source' and pass a folderless install as a success.
- **Risk:** An empty inbox needs no macOS file-access grant and raises no doctor warning.

### K14 (remove, S)

add-source creates everything that is missing: sources.toml, the docs repo, the scaffold, the inbox and the state dir. It also applies init's Time Machine exclusions and remote refusal.

init stays as a hidden, idempotent command with no flags; --docs-repo, --force and --source-local are removed. add-source drops --id.

sync still exits 78 when sources.toml is missing; the fix text now names `agentsync add-source <folder>`. The sentinel stays a commented hint.

install.sh step 4 runs add-source for each folder, or the flagless init when no folder is given. Its re-run message becomes 'exists (inbox ensured)'. The `migrate` call is deleted, since K12 migrates automatically.

- **Where:** src/agentsync/cli.py:186-219, 499-600; src/agentsync/config.py:570, 596-605, 630; src/agentsync/ops/doctor.py:393, 408; scripts/install.sh:1314-1339
- **Why:** Two verbs do one job. --force invites wiping the source list, and a non-default docs repo breaks every hard-coded path. Fixes from the refuting vote:
- sync keeps the tested exit 78 instead of silently writing an empty config;
- no automatically picked live sentinel, which colleagues renaming files in a shared folder would break.
- **Risk:** Tests to move: test_cli.py:42, 93, 900; test_it_request.py:223; test_setup_report.py:50; and the install stubs at test_install_oneshot.py:308, test_deploy_pack.py:233 and test_launcher.py:453, 589.

### K15 (rewrite-doc, S)

The sources.toml template shrinks to:
- one header line;
- one commented `# [governance]` / `# archive = true` block, with a one-line explanation of on and off.
add-source appends the [[source]] and inbox tables.

Remove the [agentsync], [graph], [breaker], [convert], [network] and [policy] blocks, the live tenant line and the Graph examples. The examples move to the docs/deploy 'What needs IT' section.

[graph] company is still accepted but ignored, with a status WARN naming the line to delete. principal, cadence_s and launchd_label_prefix stay parsed and honored. _check_keys and the [convert] defaults are unchanged.

- **Where:** src/agentsync/config.py:483, 653-750; src/agentsync/cycle.py:388 and cli.py:1328 (User-Agent); docs/deploy/README.md:79-92
- **Why:** About 17 live lines repeat defaults, the Graph examples read as fields to fill in, and the template ships a tenant value its own comment says is refused. Fix from the refuting vote: principal feeds the README owner and the scope fingerprint, cadence_s sets staleness thresholds, and offboard finds the LaunchAgents through launchd_label_prefix, so all three stay.
- **Risk:** Old configs keep loading. Changing the [convert] defaults would re-convert every page, so they stay as they are.

### K11a (remove, M)

Background sync becomes optional in code.
- launchd.launcher_required, and doctor's launcher and launchd checks, report INFO 'not installed (optional background sync; see docs/deploy)' with no fix, unless a com.agentsync.* plist exists or AGENTSYNC_AGENT_STEP_PENDING is set.
- install-agent is hidden from help. It loses --interval, --reconcile-interval and --no-backup-exclusions; the Time Machine exclusion is always on.
- uninstall-agent stays as install-agent's hidden, operator-only pair.
- The launchd argv stays byte-identical.

- **Where:** src/agentsync/ops/launchd.py:277-279, 334-343; src/agentsync/ops/doctor.py:746-780, 956-960, 1033-1051; src/agentsync/cli.py:306-316, 1357-1402; kept unchanged: ops/launchd.py:137, launcher/Sources/main.swift:58
- **Why:** This carries out the 2026-10-01 ruling (packet eae0934f7b51) in code. Without this, every default OneDrive install would show permanent launcher FAILs pointing back at install-agent, which recreates the two-model confusion.
- **Risk:** Macs that already run the agents keep them and keep today's ERROR/WARN checks. Compaction without agents is covered by K12. Test edits: test_cli.py:59, 314, 324.

### K13b (remove, M)

Trim the remaining sync and maintenance surface.
- sync drops --dry-run and --source.
- --mode, --once and --materialise-budget stay accepted but are hidden from help.
- compact-history, migrate and materialise are hidden. migrate becomes a no-op that prints 'migration is automatic'. compact-history refuses --keep-days below 1.
- Rewrite the fix strings that name dropped spellings: doctor.py:1127 and 1145, cli.py:903, cycle.py:785. The materialise remedy at cycle.py:1604 stays valid.
- install.sh's first sync passes --materialise-budget 0 unconditionally; the `sync --help` probe is deleted.

- **Where:** src/agentsync/cli.py:221-235, 283-292, 336-338, 903; src/agentsync/governance.py:2218-2220; src/agentsync/ops/doctor.py:1127-1145; src/agentsync/cycle.py:785, 1604; scripts/install.sh:1476-1480
- **Why:** --mode dry_run looks like a sync but commits nothing, --source leaves the other sources stale, and --keep-days 0 squashes all history. Fixes from both refuting votes:
- hiding a flag would silently disable install.sh's budget-0 first sync, so the probe goes;
- deleting migrate would break install re-runs, so it becomes a no-op;
- materialise is the only remedy for files over the budget, so it stays.
- **Risk:** Hidden commands remain in the code. The pinned freeze table lists them under hidden, so removing them later is a deliberate edit.

### K18 (remove, S)

Hide from help:
- the top-level login, logout, whoami and discover aliases, and the graph subcommand;
- graph login --device-code (kept working);
- the global --config;
- it-request, whose --out now defaults to it_request.DEFAULT_OUT.
hold --list, offboard --dry-run, graph --toml and purge are untouched.

- **Where:** src/agentsync/cli.py:131-157, 294-304, 376-389
- **Why:** Four extra top-level verbs bury the loop in --help, and on a zero-IT Mac they lead an agent into Entra errors it cannot fix. Fix from both refuting votes: hiding keeps about 20 fix strings and already-published docs working. --device-code is the only probe for Conditional Access when browser sign-in succeeds.
- **Risk:** None at runtime; scripts/tenant-probes.sh keeps working unchanged.

### K16a (remove, S)

Delete setup-report --no-redact and --friction; the friction log's environment default stays. setup-report is hidden from help. --out stays, hidden, defaulting to ~/agent-context/setup-report.md.

- **Where:** src/agentsync/cli.py:355-374; docs/design/CONTRACTS.md:4239, 4422-4434
- **Why:** --no-redact is the one switch that can put the tenant name into a report meant for a public issue. Fix from the vote: install.sh:840/846 always passes --out, with the AGENTSYNC_SETUP_REPORT override.
- **Risk:** Low; the tests at test_setup_report.py:183-187 and 651-652 drop their --no-redact cases.

### K19b (guard, S)

Set the pinned dict to the final surface:
- visible: sync, curate, status, add-source, accept-deletions, adopt, purge, hold, offboard;
- sync, curate and status take no visible option;
- every other verb is listed as hidden.
CONTRACTS §16.10 lists the same surface. Add test_template_has_only_sources and test_old_config_shapes_load, which loads the previous full template and the live config shape.

- **Where:** tests/test_contracts.py; tests/test_config.py:29-60; docs/design/CONTRACTS.md §16.10 (4222-4239)
- **Why:** This pins the target once it exists and makes regrowth a reviewed edit. test_config.py:42 is rewritten together with the template.
- **Risk:** None.

Tests that prove it:

- add-source on a fresh HOME creates the config, docs repo, scaffold, inbox (0700) and state dir. Running it twice leaves one [[source]] and one kind="inbox". The hidden init adds the inbox to an old config.
- sync with a missing sources.toml still exits 78, and the fix names add-source (test_cli.py:234, 827-830 unchanged)
- A fresh install and a re-run both end with exactly one kind="inbox". `--confirm-install-agent` with only the inbox still exits 1 (test_install_oneshot.py:447-458 and test_launcher.py:597-602 unchanged). The first sync passes --materialise-budget 0.
- test_old_config_shapes_load and test_template_has_only_sources. [graph] company gives one status WARN and never reaches the User-Agent.
- doctor on a CloudStorage source with no plist: launcher and launchd report INFO and rc is 0. With a plist present: today's ERROR/WARN.
- `--help` no longer lists graph, login, logout, whoami, discover, it-request, install-agent, setup-report, init, doctor, migrate, materialise, compact-history, checkpoint, install-skill, reconcile, curate-queue, lint or refresh-queue, and each one still parses
- argparse rejects setup-report --no-redact and --friction. compact-history --keep-days 0 is refused. tests/test_ops_launchd.py program_arguments output is unchanged.
- test_cli_surface_is_frozen pins the final visible and hidden sets, matching CONTRACTS §16.10

## WF Field fixes from the corporate Mac (added 2026-10-05)

Status: upcoming; runs after W4 lands (it touches cli.py, config.py and the sync arms W4 also edits), before W5.

Source: docs/research/corporate-setup-feedback-2026-10-05.md § 3 (table N1-N16, each with its file, proof test and
wave fit). Already landed: N1 `cace1d9` (`purge --queue --dry-run` really purged). N2 (setup-report log lines leak
names nested below a source) is fixed on its own branch first, as data safety. N3 is fixed on `kiss-w4` before W4
merges: `ensure_inbox` must check that the canonical `~/agent-context/inbox` is configured, not that any inbox
source exists (the field Mac has six hand-added inbox sources).

Build in this order, one commit each with the doc's proof test: N12 (a user `exclude` keeps the OS-junk defaults;
`Icon\r`), N8 (ECANCELED retries then defers; time-limited cloud reads end in a blocking "click Allow" FAIL, never
an empty folder or tombstones), N4 + N14 (inbox writer contract; one settle-and-rescan in an interactive sync, no
new flag), N6, N7, N13, N5 + N15 (one `_EMITTER_VERSION` bump), N11, N10. No new command, flag or config key: the
freeze pins must not change in this wave.

Operator decisions from the report (filed, not decided here): D1 retention of chat-derived history (`archive = true`
for a chat inbox vs company retention), D2/D3 shipping no-IT exporters or reading the Teams cache.

## W5 Install output, setup prompt v7, setup report

Status: upcoming.

Carry from the field report (docs/research/corporate-setup-feedback-2026-10-05.md § "What W5 must carry"): v7 names
the inbox by its configured folder(s) (N3), says what to drop there and which formats carry a sensitivity label
(N9), that the inbox is never emptied by hand (N14), and that agentsync never needs `~/Library/Containers`, Group
Containers or the browser (N16); a terminal-side TCC_PENDING blocks, only the launcher's goes ahead (N8b); add D1's
line only if the operator picks `archive = true`.

Files: `scripts/install.sh`, `README.md`, `docs/deploy/README.md`, `docs/deploy/setup-feedback.md`, `docs/plans/implementation.md`, `src/agentsync/setup_report.py`, `src/agentsync/loop.py (read only)`, `.github/ISSUE_TEMPLATE/setup-report.yml`, `docs/design/CONTRACTS.md (§16.14, install.sh log)`, `tests/test_install_oneshot.py`, `tests/test_launcher.py`, `tests/test_deploy_pack.py`, `tests/test_setup_report.py`, `tests/test_contracts.py (install case-arm pin)`

### K02 (rewrite-doc, M)

install.sh's final line becomes the loop's NEXT.

NEXT and exit codes:
- Delete every 'nothing is left: background sync is on' branch, including the simulated one.
- After the first sync, install.sh runs status once with AGENTSYNC_NO_NEXT_HINT unset, lifts its NEXT line and prints it as its own single NEXT. If status fails, it falls back to a fixed line: 'run ~/.local/bin/agentsync sync and follow its NEXT line'.
- When agents were installed, the line is prefixed with the background-sync result (running/ok).
- A first run with a created config and no folders exits 2 naming --list-folders. An existing config, including the upgrade path, still exits 0.
- A real doctor FAIL (DOCTOR_BLOCKS) exits 1.

Steps:
- Step 6 (first sync) runs whenever a folder source exists and DOCTOR_BLOCKS is 0, never gated on DOCTOR_RC. TCC_PENDING alone still goes ahead.
- Step 5 calls status, logged as step 'status'.
- Pin UV_TOOL_BIN_DIR=$HOME/.local/bin for uv calls, so the binary always sits where the guides say.

- **Where:** scripts/install.sh:300-314, 1205-1211, 1380-1445, 1694-1736
- **Why:** The installer tells the setup agent it is finished exactly where the mirror half ends and the curation half begins. The sourceless and doctor-FAIL exits are 'successful' installs that sync nothing. Fixes from the votes:
- DOCTOR_BLOCKS rather than DOCTOR_RC, so a TCC-pending corporate Mac is not blocked;
- the exit 2 is scoped so existing and inbox-only configs are not caught;
- install.sh still prints exactly one NEXT line.
- **Risk:** Exit-code changes ripple into tests: test_install_oneshot.py:323, 358, 579, 1353; test_launcher.py:559-581, 709, 720.

### K11b (remove, M)

Setup no longer installs LaunchAgents by default.
- Step 3 (launcher build) and steps 7-8 (install-agent and the 3-minute wait) run only with --confirm-install-agent.
- The --list-folders NEXT line, the README default path and the setup prompt stop passing that flag.
- docs/deploy documents it as optional background sync, owned by the operator.
- --rebuild-launcher becomes the developer variable AGENTSYNC_REBUILD_LAUNCHER=1.

- **Where:** scripts/install.sh:4-17, 1006, 1015-1025, 1240-1312, 1429-1444, 1513-1631, 1723-1725; README.md:50, 168, 564-575; docs/deploy/README.md:46-60, 93-111
- **Why:** This carries out the 2026-10-01 frozen scope, which prompt v6 breaks on every documented path. The background runs made this Mac look healthy (936 runs) while topics/ stayed empty. It also removes the second Allow click, the launcher-build dead end that exits 0, the wait, and the lock contention.
- **Risk:** The mirror advances only when a session syncs, a cost the ruling accepts. Depends on W4's doctor INFO change and W1's due reconcile.

### K17 (remove, M)

Trim install.sh's options.
- --dry-run becomes AGENTSYNC_INSTALL_DRY_RUN=1 and leaves the docs.
- Delete --config and the CONFIG_FLAG block; tests use AGENTSYNC_CONFIG.
- Delete --no-report, together with the baseline prompt that called it.
- Fold --log-end into --report-only, which appends the end line only when the current attempt has none.
- Delete the dead launcher/prebuilt arm.
- The SOURCE positional stays as an undocumented test seam, removed from the usage header and --help.

Remaining options: --source-local, --list-folders, --version, --log-start, --log, --report-only, --help, plus the operator-only --confirm-install-agent and --launcher.

- **Where:** scripts/install.sh:3-48, 1012-1050, 1085-1089, 1286-1289, 1688-1692; README.md:567; docs/deploy/README.md:49, 128
- **Why:** Each removed option is a branch where install.sh exits 0 before the loop starts, or quietly diverges. Fixes from both refuting votes:
- --no-report goes only together with its live caller;
- the --log-end fold comes with the prompt-compat bump to 7;
- the wheel and SOURCE test seam used by about 30 tests is kept.
- **Risk:** Test edits: test_deploy_pack.py:117, 146, 206, 236, 354, 642-646; test_launcher.py:637, 732; test_install_oneshot.py:520, 590-593.

### K04 (rewrite-doc, M)

Setup prompt v7 replaces both v6 and the separate baseline prompt. It has 3 steps:
1. Unchanged: preflight, clone or pull, the --version gate, --log-start, --list-folders, then the one question (which folders).
2. `install.sh --source-local "<folder>"`, announcing the one Allow click.
3. Run `~/.local/bin/agentsync sync` and do what NEXT says. Repeat until NEXT says the session is done or names a WAITING ON YOU. Then run `install.sh --report-only` and finish with three lines: the folders, the last NEXT or WAITING line, and the report path.

Also:
- Delete the it-request step and the 'Next, on the same Mac' block.
- Allowlist: drop `agentsync it-request *`; add exact `Bash(~/.local/bin/agentsync sync)`, `…curate)` and `…status)`, plus the Copilot shell() equivalents.
- Collapse the feature table to set up / every session / operator.
- 'What happens after install' becomes 'every session: sync, then NEXT', keeping the existing CORRECTED notes.
- Remove the README's install-skill mentions and say the inbox always exists.
- setup-prompt-compat becomes 7. Add PROMPT_LAYOUTS[7]; prompt_layout() picks the layout by explicit version, so v6 logs still parse as v6.
- Delete the two-command install route in docs/deploy.

- **Where:** README.md:41-54, 53, 62-98, 130-243, 552-575; docs/deploy/README.md:32-77; scripts/install.sh:391-402; src/agentsync/setup_report.py:111-187; docs/plans/implementation.md:207 (pointer to the deleted prompt)
- **Why:** In v6, step 3 is 'the last command you run' and the finish lines never mention curation. The loop lives in an optional second paste that has never been pasted on this Mac. Fix from the refuting vote: this wave lands after `curate` and the NEXT line exist, so the command-existence test stays a guard.
- **Risk:** The first session gets longer, because drafting the baseline questions follows install. Exact allow rules never cover purge, hold or offboard.

### K16b (guard, S)

The setup report gains a computed 'Loop:' line. It shows the stage (installed / synced / baseline drafted / baseline confirmed / before run / topics N / after run) and the current NEXT line, never paths. The issue form gains a loop_stage field. compute_outcome and the existing outcome options are unchanged. v7's step 3 regenerates the report after the loop, so the line reflects where setup actually stopped.

- **Where:** src/agentsync/setup_report.py:908-1056; .github/ISSUE_TEMPLATE/setup-report.yml:28-32; docs/deploy/setup-feedback.md:78, 106, 121-128, 185-189; docs/design/CONTRACTS.md §16.14 (4488-4532)
- **Why:** Today a sync-only install is indistinguishable in feedback from a full one. Fix from both refuting votes: redefining the outcome would make it constant and erase the 'failed at step N' signal, so loop progress gets its own field.
- **Risk:** Low. Loop text carries no paths, so redaction is unchanged.

Tests that prove it:

- No install.sh output contains 'nothing is left'
- A sourceless first run (created config, no folders) exits 2 naming --list-folders. A re-run over an existing config exits 0. A non-TCC [FAIL] exits 1. TCC_PENDING alone still runs the first sync (test_install_oneshot.py:395-403 kept).
- `install.sh --source-local X` with no flag makes no launchctl call and builds no launcher. It runs the first sync, writes the skill, and its last line equals status's NEXT ('draft the baseline questions'). With --confirm-install-agent, the line also states background sync is running.
- install.sh prints exactly one NEXT line on every path (test_install_oneshot.py:964-991)
- AGENTSYNC_INSTALL_DRY_RUN=1 replaces --dry-run. --config, --no-report and --log-end exit 2 as unknown options. --report-only appends one end line, idempotently. The case arms equal the pinned set.
- Prompt v7: every agentsync subcommand the prompt or the allowlist names exists (test_deploy_pack.py:795 extended). It contains no it-request, no --confirm-install-agent and no second prompt, and closes with --report-only.
- setup_report parses v6 and v7 logs by explicit version. A sync-only fixture reads 'Loop: synced; NEXT: draft the baseline questions' while its outcome stays 'fully one command'. The issue form has loop_stage.

## Dropped or changed by the skeptics

- K01: deleting NO_NEXT_HINT_ENV, and printing NEXT on every sync, including launchd and install.sh runs. Both are kept or limited so install.sh still prints exactly one NEXT line.
- K01: rule 3 blocking on online-only deferrals. Files over the budget and files the OS refuses never clear, so the agent would loop on sync forever.
- K02: the hard-coded install.sh NEXT alternative. Replaced by lifting status's NEXT line, with a fixed fallback.
- K03: deleting install-skill outright. Kept as a hidden alias so the README and the corporate Mac's upgrade one-liners keep working.
- K06: triggering the checkpoint on any commit that staged topics/. Banner and seed rewrites would have moved the tag.
- K08: zero live sources as a FAIL (it would fail sourceless installs), and deleting doctor and `policy show` outright (hidden aliases instead).
- K09: deleting lint without moving its whole-repo checks into curate, and the literal 'topics/ is empty' test.
- K10: blocking the checkpoint on SOURCE-UNREADABLE or SOURCE-DELETED, which the agent cannot fix.
- K11: folding uninstall-agent into offboard. Kept as install-agent's hidden operator pair, so background sync can be removed without offboarding.
- K12: the hard-coded 3600 s, promoting explicit --mode (launchd) runs to reconcile, and migrating only inside a cycle.
- K13: deleting compact-history, migrate, materialise, reconcile, checkpoint and install-skill outright. All are hidden; materialise stays the only remedy for files over the budget.
- K14: sync creating sources.toml when it is missing (exit 78 kept), and the automatically picked live sentinel.
- K15: ignoring principal, cadence_s and launchd_label_prefix. Each is still read by working code (README owner, staleness thresholds, offboard).
- K16: redefining the install outcome as 'fully set up / partial', and fixing --out at one path (kept hidden with the env override).
- K17: deleting the SOURCE positional and the wheel branch. Kept as an undocumented test seam.
- K18: deleting the four aliases and --device-code; changing hold --list, offboard --dry-run and graph --toml; the exact --help list test.
- K19: landing target-surface pins before the cuts. The guide-path and old-config tests moved into the waves that create those behaviors.
