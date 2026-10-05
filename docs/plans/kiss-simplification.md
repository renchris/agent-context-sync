---
status: done
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

## W3 What agents read: one procedure in every guide — DONE (2026-10-05)

Landed on `main` at `e8e7c8f` (6 commits, `a0e0542..e8e7c8f`): `a0e0542` shell refresh queue retired (K09b),
`25f39c3` one procedure in every guide (K07), `631a957` STATE.md opens with `## Next`, INDEX says when no topic
exists (K08b), then three fixes from the fresh-context review (9 findings, none rejected): `b914b6a`, `b842f7e`,
`e8e7c8f`. Gate after rebase onto the purge and redaction fixes: ruff, format, mypy clean; pytest 1976 passed, 2
skipped. Run `wf_62274074-ae4` (in-process, alongside W4).

Learnings for W5:
- The procedure is `skill.procedure(archive=)` and the root guide `publish.root_guide(archive=, inbox=)`; the
  ROOT_CLAUDE_MD/_AGENTS_MD constants are gone. The skill has no config, so it never carries the archive lines.
  Step 2 says "Do what the `NEXT:` line says (it comes before any `WAITING ON YOU:` and `note:` lines)": NEXT is
  not the last line.
- The guide's inbox line names the first live inbox source's folder (with `~`), only when one exists.
- STATE.md: `## Next` (the exact `loop.next_lines` lines, unbulleted, so `grep '^NEXT: '` works), then
  `## This run` with the run fields. `write_state` calls `next_step(count_queue=False)` to skip the mirror walk.
- Left for W5 (README is W5's): README.md still has `sh refresh-queue.sh docs/DEPENDS.tsv`; docstrings in
  paths.py:147 and frontmatter.py:4 still name the refresh-queue script. CONTRACTS §13's file tree still lists
  SYNONYMS.tsv and §15's module stubs are stale (W3 edited §14 only).
- BOUNDARY_TEXT says `docs/mirror/` verbatim, so the no-docs/-prefix test strips it before checking.

## W4 CLI surface: fewer verbs, flags and config keys — DONE (2026-10-05)

Landed on `main` at `49ebce7` (17 commits, `1c1fb13..49ebce7`): `1c1fb13` one inbox (K05), `36ed332` add-source
creates everything, init hidden (K14), `e49e212` template holds only folders and one archive line (K15), `3b070e1`
background sync optional (K11a), `db609e6` sync takes no visible option (K13b), `bc33338` graph verbs, it-request
and --config hidden (K18), `5547b71` setup-report hidden, --no-redact/--friction deleted (K16a), `50334e7` final
surface pinned (K19b); eight review fixes (20 findings, one partly rejected as already fixed); `49ebce7` the
field report's N3 (the named inbox is added even when other inbox sources exist). Gate after rebase onto W3:
ruff, format, mypy clean; pytest 2008 passed, 2 skipped. Run `wf_62274074-ae4`.

Learnings for WF and W5:
- Visible surface is final and pinned: sync, curate, status, add-source, accept-deletions, adopt, purge, hold,
  offboard; 21 hidden commands still parse. WF must not change the pins.
- The inbox sits beside the docs repo (`expand(docs_repo).parent / "inbox"`), not a hard-coded path; with the
  default docs repo that is `~/agent-context/inbox`. An empty inbox outside CloudStorage is ok in doctor.
- `[graph] company` is dropped from the model: `Config.graph_company_line` drives one status WARN (`fix: delete
  line N`); every config from the old template carries it, so existing installs see that WARN once.
- Background sync: doctor gates its launcher/launchd checks on `launchd.agents_installed(config)` or
  `AGENTSYNC_AGENT_STEP_PENDING=1`; `launcher_required` is unchanged because install-agent still relies on it.
- compact-history's keep-days floor lives in `governance.compact_history` (exit 1, not 2). migrate is a hidden
  no-op. Fix strings that name a command are pinned by a test to parse.
- setup_report.py still holds the unreachable `--no-redact` strings (`build_report(redact=False)`); W5 K16b
  owns them.

## WF Field fixes from the corporate Mac — DONE (2026-10-05)

Landed on `main` at `d4c43f4` (22 commits, `6b2da6f..d4c43f4`), items N1-N16 of
docs/research/corporate-setup-feedback-2026-10-05.md § 3 except N9 and N16 (W5's prompt lines): `6b2da6f` N12 a
custom exclude keeps the OS-junk defaults; `6e26190` N8a ECANCELED retries then defers; `f2eb633` N8b cloud
listings time-limited, ending in a blocking click-Allow FAIL; `d787faf` N4 one settle-and-rescan of the inbox per
interactive sync; `0dcd3c5` N14 inbox writer contract in CONTRACTS §11; `de081ae` N6, `f99831d` N7 lints;
`7975a5c` N13 and `8f18dc7` N11 Graph-less messages; `20a713c` N5 + N15 eml; `4b2b34b` N10; `1cb31d3` N1 held
purge test; then the review fixes (14 findings, none rejected). Earlier: N1 `cace1d9`, N2 `bef87c1`, N3 in W4.
Gate: ruff, format, mypy clean; pytest 2059 passed, 2 skipped; freeze pins unchanged. Run `wf_b2a73fa8-634`.

Learnings for W5:
- A listing that runs past `arm_local.LISTING_TIMEOUT_S` (120 s per read, `call_with_timeout`) stops the whole
  walk: the scan is incomplete with `listing_held`, nothing is fetched or tombstoned, and sync's alarm says
  "click Allow, then re-run the sync". Doctor's `source.<id>.listable` is then an ERROR that blocks. So v7 step 3
  should tell the agent that a sync that stops on "click Allow" means a macOS prompt is waiting for the person.
- The loop's WAITING line for an unlisted folder still says "grant Files and Folders access or exclude it"; W5
  may align it with the click-Allow wording.
- setup_report's timed-call seam now comes from `arm_local.call_with_timeout` (W5 K16b edits that file).
- The perf budget test (`AGENTSYNC_PERF=1`) was not completed with the per-listing timer (about 87 µs per directory
  measured; estimated +0.09 s on the 3 s budget).

## W5 Install output, setup prompt v7, setup report — DONE (2026-10-05)

Landed on `main` at `2b331c4` (13 commits, `5d96f3f..2b331c4`): `5d96f3f` fewer install.sh options (K17),
`c60faa0` background sync optional, never part of setup (K11b), `f1a8d0e` install.sh ends on the loop's NEXT (K02),
`5a52194` computed Loop line and `loop_stage` (K16b), `3674be3` setup prompt v7 (K04), then eight review fixes (22
findings, none rejected). Gate: ruff, format, mypy, shellcheck clean; pytest 2088 passed, 2 skipped. Run
`wf_e266c250-c64`. Field carry in v7: inbox named through `sources.toml` (N3), what to drop and which formats carry a
label (N9), never empty the inbox (N14), no Containers or browser (N16), a sync that stops on "click Allow" (N8).

Learnings:
- Setup prompt v7 is README.md § "Set up on a new Mac: one prompt"; `setup-prompt-compat 7`. A saved v6 prompt
  still works: `--log-end` is a hidden first-argument arm that closes the friction log like `--report-only`.
- Every failure path in v7 says "go to step 3's report"; step 3 follows NEXT past a non-zero exit and stops only
  when NEXT says "session done".
- `--launcher` without `--confirm-install-agent` is a usage error; `AGENTSYNC_REBUILD_LAUNCHER=1` forces a rebuild.
- Ruled (2026-10-05, operator): chat-history retention (`7137d8ac6cbb`) keeps the default, `archive` off, because
  `[governance] archive` applies to every source and would keep deleted company content outside Purview; chat
  exports stay safe by the inbox writer contract (one file per month, never renamed or removed). No exporters
  ship (`56c300064f89`): the README and docs/deploy point a person's own scripts at that contract.

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
