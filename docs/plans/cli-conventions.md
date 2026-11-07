# Remediation plan: agent-context-sync (`agentsync`) against the vendor-neutral CLI checklist

Inputs: the verified findings JSON (20 kept, 2 dropped), /tmp/cli-bp/checklist.md (rule IDs), /tmp/cli-bp/guidelines.md
(source). Everything here comes from reading files and from read-only git commands. No agentsync command was run.
Every LOC figure is an **estimate** (sites times typical edit size, plus tests). Every count is labelled with the
command that **measured** it.

**Round 2 (this amendment).** It adds 44 verified findings for `scripts/install.sh` and `scripts/tenant-probes.sh`
(K21-K64) and the verified entries of `/tmp/cli-bp/critic2.json` that concern this repo (K65-K70, plus row fixes).
Rows changed in place carry **Round 2 fix**. New sections are headed "Round 2 additions". §7 lists every change. The
method is the same: static reading only, nothing in the repo was run, edited or committed.

---

## Phase 0: Agent Team Orchestration

### Execution locus per wave

| Wave | Locus | Why |
|---|---|---|
| Prep (U-0) | L | It is one docs file, about 40 lines, and reserving CONTRACTS sections is a lead act in this repo's B13 practice. |
| Wave A (U-A1) | S | Default. |
| Wave B (U-B1a, U-B2, U-B3, U-B1b, U-B4, U-B5) | S, one session per unit | Default. |
| Wave C (U-C1 to U-C6, plus U-C7/U-C8 if approved) | S, one session per unit | Default. The lead decides the operator questions before each unit fires. |
| Round 2, Wave A (U-A1a, U-A1b, U-A2) | S, one session per unit | U-A1 is split because its own parts already sum to 500 LOC (see §2). U-A2 is the install.sh stream layer. |
| Round 2, Wave B (U-B7 to U-B17) | S, one session per unit | Default. These form two serial chains, one per script (see §6). |
| Round 2, Wave C (U-C1b, U-C9 to U-C13, plus U-C8b and U-C14 to U-C17 if approved) | S, one session per unit | Default. The lead decides D10-D39 (§6) before the unit that needs each one fires. |

**The per-unit fire command:**

```
handoff-fire.sh --prompt-file <brief> --worktree clibp/<unit> --notify-back <lead-uuid> --goal '<unit's end state> — proven by scripts/gate.sh printing "GATE GREEN at <sha>" and scripts/ship-land.sh printing LANDED, then the CI read-back loop printing no "CI RED"; do not touch probes/, files outside the unit's ownership row in §6, or published history'
```

### Task size per unit

- Each brief is written to the 40-150K output band: about 30-75 minutes, 60-110 turns, 3-6 files.
- Each unit carries one task and stays under 500 LOC (the §6 estimates).
- Two units fall below the band and are folded rather than fired alone:
  - U-B6 (measure only) becomes the first step of U-B1a's session.
  - U-0 is lead-inline.

### Team

- **Lead:** owns this plan, the operator decisions in §6 and the landing order. It writes no product code.
- **One dispatched session per unit:** each leads its own teammates only if its unit spans 3 or more files, which
  applies to U-B2, U-C2a and U-C4. Round 2 adds U-B7, U-B11 and U-C9 to that list.
- **Background subagents:** read-only census work only, for example the K1 stdout re-pin census and the K4
  `purge --queue` pin census.

### Dependencies (blockedBy)

| Unit | Blocked by |
|---|---|
| U-A1 | U-0 |
| U-B1a | U-0 |
| U-B2 | U-0 |
| U-B3 | U-A1 |
| U-B1b | U-A1, U-B1a, U-B2 |
| U-B4 | U-B1b, the K8 decision |
| U-B5 | U-B4 |
| U-C1 | U-B5 |
| U-C2a | U-C1, U-B3 |
| U-C2b | U-C2a |
| U-C3 | U-C2b |
| U-C4a + U-C4b | U-C3, the K1 decision |
| U-C5 | U-C4 |
| U-C6 | U-C5, the K6 decision |
| U-C7 / U-C8 | their decisions |
| U-A1a (Round 2 split; "U-A1" above means U-A1a and U-A1b both landed, except that U-B3 needs only U-A1a's `text.safe_text`) | U-0 |
| U-A1b | U-A1a |
| U-A2 | U-0 |
| U-B7 | U-A2 |
| U-B8 | U-B7 |
| U-B9 | U-B8 |
| U-B10 | U-B9 |
| U-B11 | U-B10 |
| U-B12 | U-B11, D15 |
| U-B17 | U-B12, D16, D17 |
| U-B13 | U-B3 |
| U-B14 | U-B13 |
| U-B15 | U-B14, D34 |
| U-B16 | U-B15 |
| U-C1b | U-C1, U-B5 |
| U-C9 | U-B17, D11, D22 |
| U-C10 | U-C9, D21 |
| U-C11 | U-C10, D29 |
| U-C12 | U-B16, D10, D32, D33 |
| U-C13 | every decision in §6; lands last |
| U-C8b, U-C14a, U-C14b, U-C15, U-C16, U-C17 | their decisions (D36, D14, D28, D38, D10, D20/D17) |

### Worktrees

- Each unit gets `/Users/chrisren/Development/.worktrees/clibp/<unit>` on branch `clibp/<unit>`, cut from
  `origin/main` at fire time. Never the main checkout: it is 391 commits stale and holds another session's
  untracked `probes/` files.
- U-C4a and U-C4b share one branch and land as one commit.

### Spawn order

1. U-0 (lead).
2. Fire U-A1, U-B1a and U-B2 in parallel; their files are disjoint.
3. Then U-B3 and U-B1b.
4. After that, every unit that touches cli.py fires only after the previous one has landed.
5. **Round 2.** Two more chains run beside the cli.py chain, each strictly serial:
   - install.sh: U-A2, U-B7, U-B8, U-B9, U-B10, U-B11, U-B12, U-B17, U-C9, U-C10, U-C11.
   - tenant-probes.sh: U-B3, U-B13, U-B14, U-B15, U-B16, U-C12.
   - U-A2 fires with step 2, and U-B13 fires after U-B3 lands.
   - No install.sh unit is active while U-C4a is, because U-C4a edits install.sh too. §6 lists the other cross-chain
     holds.

### Lead context budget and succession point

- Reserve at least 50% of the lead's window for decisions. The lead reads only briefs, completion pings and
  gate/CI one-liners.
- Recycle the lead (`handoff-fire.sh --recycle`) at the Wave B to Wave C boundary, after U-B5 lands and before the
  K1/K4/K6 decisions.
- Recycle again after U-C3 lands.
- Round 2: also recycle when the install.sh chain reaches U-C9, because D10-D22 are decided around that point.

---

## 1. Header

### Repo and CLIs covered

- Repo: `/Users/chrisren/Development/agent-context-sync`. The package is `agentsync` 0.1.0, Python >= 3.11, macOS.
  Trunk is `main`.
- CLI covered: **`agentsync`**. The console script is `agentsync = agentsync.cli:main` (pyproject.toml). The
  launchd jobs reach it through `python -m agentsync` (src/agentsync/__main__.py). There is one argparse tree
  (`build_parser`) and one dispatcher with one except-ladder (`_main`).
- **Round 2 fix (critic uncoveredClis).** Round 1 listed `scripts/install.sh` and `scripts/tenant-probes.sh` as "not
  covered". Both are user-facing and are now audited as CLIs: K21-K47 and K48-K64. The round 1 sections below still
  treat them as callers of `agentsync`, and that stays true for those rows.
- Not covered as CLIs: `scripts/gate.sh` and `scripts/ship-land.sh`. They are developer tools and appear below only
  as callers whose parsing a fix must keep working.

### Baseline drift: read this before using any line number

- The findings cite the local main checkout at `ccf696b7b`.
- `origin/main` (`d62ac1487`) is **391 commits ahead**, measured with
  `git rev-list --count HEAD..origin/main`.
- On `origin/main`, `src/agentsync/cli.py` is 4721 lines against 4171 in the checkout, measured with
  `git show origin/main:src/agentsync/cli.py | wc -l`.
- I grepped every kept finding again against `origin/main`, and **all 20 are still present**. Two got worse:
  - `_cmd_purge` now also calls `governance.carry_round28_record` (a write) before its `--forget` usage check.
  - `_cmd_hold` does the same before its `--owner` and `--reason` checks.
- **Implementers branch from `origin/main` and find code by symbol or quoted text.** In this plan, `path:line`
  means the **origin/main `d62ac1487`** line (measured with `grep -n` on `git show origin/main:<path>`). Where only
  the checkout line was known, the anchor is marked "(checkout)".

### Files dirty in another session (do not touch)

- `git -C /Users/chrisren/Development/agent-context-sync status --porcelain` shows `?? probes/common.py` and
  `?? probes/p1.py`. These are untracked, belong to another session, and no unit below touches `probes/`.
- The B13 round-35 lane worktrees (`.worktrees/b13/r35-{b,b-land,b-land2,d,e,lead,ra,rb}`) are all clean (measured
  with `git -C <wt> status --porcelain`). Only `b13/r35-lead` is ahead of origin/main, by 1 docs-only commit
  (`docs/plans/bring-back-ocr.md`).
- Do not work in the main checkout. It is stale, and another session owns it.

### Repo rules that constrain every unit

These come from CLAUDE.md and the whole-tree scanners. There is no `.claude/CLAUDE.md`, and `.claude/rules/` is
empty.

1. **New modules must be in CONTRACTS.** `tests/test_contracts.py::test_module_imports_and_is_in_contract`
   requires every new module (`agentsync.clio`, `agentsync.fsutil`) to have a section in
   `docs/design/CONTRACTS.md` naming it, and every public function, class and UPPERCASE constant to appear in that
   file's text.
2. **No private function only tests call.** `test_no_private_function_in_src_is_left_for_the_tests_alone`
   requires every module-level private function to be called from src.
3. **The CLI surface is frozen.** `test_cli_surface_is_frozen` (origin tests/test_contracts.py:167) checks
   `CLI_GLOBAL_OPTIONS` (:52), `CLI_SURFACE` (:55) and the `common` set (:159).
   `test_contracts_16_10_lists_the_pinned_surface` (:183) checks the §16.10 Visible/Hidden lines.
   `test_help_lists_no_hidden_verb_and_each_still_parses` (:203) checks the hidden verbs.
   **Any new option or verb changes these pins in the same commit.**
4. **ruff `T20` bans `print`** outside `src/agentsync/cli.py`. New modules write with
   `sys.stdout.write`/`sys.stderr.write`. Do not add a per-file ignore, because a `pyproject.toml` change forces
   the full gate.
5. **mypy runs `--strict`** over `src/`.
6. **pytest runs `-n auto`**, and every test keeps its own HOME (`tests/conftest.py::_isolate_home`). New tests
   write only under `tmp_path`.
7. **Escalation (CLAUDE.md).** Stop and ask before rewriting published history, installing a LaunchAgent, or
   committing anything that names the tenant. Use Contoso in tests and examples.
   - No unit rewrites published history: the purge tests run on tmp repos.
   - No unit installs a LaunchAgent.
8. **B13 process.** Lanes reserve CONTRACTS §16.N sections in a lead commit. The last section is §16.234 (round
   35, measured with `git show origin/main:docs/design/CONTRACTS.md | grep -n '^### 16\.'`). Round 36's base is
   on `b13/r35-lead`. This work must reserve its own section numbers after round 36's (unit U-0). If it runs as a
   B13 lane, also read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` (its words check).

### Gates (exact commands, from CLAUDE.md, which mirrors `.github/workflows/ci.yml` and `diagrams.yml`)

```sh
uv sync --locked
[ -d node_modules ] || npm ci  # Round 2 fix: a fresh worktree has none; round 1 dropped this line
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked mypy src
uv run --locked pytest -q -p no:cacheprovider -n auto
uv run --locked --only-group lint shellcheck probes/*.sh scripts/*.sh launcher/*.sh docs/design/receipts/review/scripts/*.sh
uvx ruff@0.15.9 check --isolated scripts
for f in scripts/*.mjs; do node --check "$f"; done
npm run diagrams:check
make -C probes CFLAGS='-O2 -Wall -Wextra -Werror' && make -C probes check
```

- Pre-gate for each unit: `scripts/gate.sh --affected`. Its land gate is `scripts/gate.sh`, which ends
  `GATE GREEN at <sha>`.
- Any unit that touches `pyproject.toml`, `tests/conftest.py` or `uv.lock` runs the full set. The plan avoids all
  three.
- **Round 2 fix (critic planConflicts).** The list above now matches CLAUDE.md's gate block. Every unit works in a
  fresh worktree from origin/main, which has no `node_modules`, so without `npm ci` `npm run diagrams:check` fails.
  `scripts/gate.sh` runs this list itself.
- **Round 2 additions from CLAUDE.md.**
  - `pytest -n auto` or a gate may print `QUEUED` and wait for a slot of the machine-wide cap, with nothing run yet.
    Run them with `run_in_background`. `cc-gate-slot status` shows who holds the slots.
  - A failure at the machine's normal load of 600 to 1000 is "unknown, retry", not a verdict (operator ruling
    2026-10-20).

### Landing flow

1. `gh run list --branch main --limit 5`. If trunk is red, it is fixed first.
2. Make a worktree from `origin/main`. Implement and run `scripts/gate.sh --affected`.
3. Land with `scripts/ship-land.sh`, typed exactly so. It takes the shared lock, fetches, rebases, runs the full
   gate, pushes `HEAD:main` (never forced) and reads CI.
4. Read CI back:

   ```sh
   for id in $(gh run list --commit "$(git rev-parse HEAD)" --json databaseId --jq '.[].databaseId'); do
     gh run watch "$id" --exit-status || echo "CI RED: run $id"
   done
   ```

   **Round 2 fix (critic planConflicts).** `ci` takes about 18 to 62 minutes once its job starts, plus any wait for
   a macOS runner (one waited 63 minutes), against an 80-minute job limit, per CLAUDE.md. Round 1 said 18 to 50.
   A loop that prints nothing has read nothing: an empty list is not green. For `no CI run for <sha>`, follow
   CLAUDE.md's paragraph on when to dispatch and when never to.
5. Units that share `cli.py` land **one at a time**, in the order of section 6.
6. **Round 2.** Units that share `scripts/install.sh` or `scripts/tenant-probes.sh` also land one at a time. ship-land
   stops with exit 6, no gate run, when trunk's last run ended red. A run that lost its hosted runner is rerun with
   `gh run rerun <id> --failed`, never landed past.

### Finding index used below (K = kept finding, in the JSON's order)

| K | Finding | Verdict contract | Wave |
|---|---|---|---|
| K1 | Receipts, progress, NEXT and login text go to stdout | breaking | C (U-C4) |
| K2 | Bare call prints help to stderr and exits 2; no `help`; `--version` only at root; invalid choice lists hidden commands; unwrapped help | breaking (tests only) | C (U-C1) |
| K3 | `allow_abbrev` is left True | compatible | B (U-B1a) |
| K4 | purge, purge --queue and compact-history have no consent gate or preview | breaking | C (U-C2a/b) |
| K5 | offboard's refusal says `pass confirm='...'` | compatible (refined) | B (U-B1b) |
| K6 | `--dry-run`, status and curate migrate the manifest | breaking, **decision** | C (U-C6) |
| K7 | Usage validation runs after config load and writes; conflicts are ignored | compatible | B (U-B1a) |
| K8 | No `--json` and no JSON error documents | compatible (opt-in), **decision** | B (U-B4/U-B5) |
| K9 | No BrokenPipe/SIGTERM/SIGHUP handling; Ctrl+C prints only `interrupted` | compatible | B (U-B1b) |
| K10 | Unknown exceptions re-raise as tracebacks | breaking | C (U-C3) |
| K11 | Remote text printed unsanitized | compatible | B (U-B3, U-B1b) |
| K12 | login opens a browser with no non-interactive guard or `--no-open` | breaking | C (U-C5) |
| K13 | Config, skill and scrub writes are not atomic or use fixed temp names | compatible | B (U-B2, U-B1b) |
| K14 | hold set/release read-modify-write holds.json without a lock | compatible | B (U-B2) |
| K15 | Every command imports the whole package before parse | compatible, measure first | B (U-B6), then **decision** |
| K16 | Fused verb-noun commands, verb flags, `N` metavar, two login names | breaking (in part), **decision** | B (compatible part), C (U-C8) |
| K17 | Hidden legacy aliases do not warn | compatible (one test re-pinned) | B (U-B1a) |
| K18 | Error/empty-state copy: prefixes, inline `(fix: ...)`, `try again`, `(s)`, `0 active holds` | breaking (in part), **decision** | B (compatible part), C (U-C7) |
| K19 | AGENTSYNC_CONFIG missing from help; installer variables use the public prefix | compatible (narrowed) | B (U-B1a) |
| K20 | offboard table has no header; discover mixes TSV and TOML on stdout | compatible | B (U-B1b) |
| K21 | install.sh sends progress, notes and NEXT to stdout, with no capturable result | breaking (Option A), **decision D14** (Option B additive) | B (U-B12); C (U-C14a if A) |
| K22 | install.sh has no `--json` and no stable reason codes | compatible, **decision D15** | A (U-A2), B (U-B12) |
| K23 | install.sh has no machine-readable help or help search | compatible, **decision D16** | B (U-B17) |
| K24 | install.sh `--version` prints two lines; `-v` is rejected | compatible (Option B), **decision D17** | B (U-B17); C (U-C17 if A) |
| K25 | A stray positional, `help` included, is a hidden SOURCE and fails after writing state | compatible (refined), **decision D18** | B (U-B7) |
| K26 | install.sh has no `--name=value` and no `--`, and scans argv twice | compatible (refined), **decision D19** | B (U-B7) |
| K27 | `--report-only` silently ignores conflicting options; an empty `--launcher` falls back | breaking (no in-repo caller) | C (U-C9) |
| K28 | install.sh accepts hidden old options silently | compatible | B (U-B7) |
| K29 | install.sh modes are options rather than a command grammar | compatible (refined), **decision D20** | C (U-C13 record; U-C17 if A) |
| K30 | `--confirm-install-agent` uses a banned consent spelling | compatible (old name keeps parsing), **decision D11** | C (U-C9) |
| K31 | install.sh exposes standard capabilities only through environment variables | breaking for `--dry-run` and `--config`, **decision D21** | C (U-C10) |
| K32 | install.sh exit 3 and 4, exit 3 on an empty list, exit 2 after real work | breaking (Option A), **decision D10** | C (U-C13 record; U-C16 if A) |
| K33 | install.sh runs a downloaded installer with no integrity check or time limit | breaking for the pin (Part B), **decision D22**; Part A compatible | B (U-B9); C (U-C9) |
| K34 | install.sh children run with no time bound | compatible, **decision D23** | B (U-B9) |
| K35 | install.sh signals leave children running; a second Ctrl+C does not exit | compatible, **decision D24** | B (U-B10) |
| K36 | install.sh does not handle a closed stdout | compatible, **decision D25** | A (U-A2) |
| K37 | `--list-folders` caps at 200 with no way to see the rest, and prints raw names | compatible | B (U-B11) |
| K38 | install.sh read-only modes write state | compatible (declaration only), **decision D26** | B (U-B8) |
| K39 | Launcher replacement is not atomic; unguarded failures end with no error or NEXT | compatible | B (U-B9) |
| K40 | install.sh swallows failures its output then treats as fine | compatible, **decision D27** | B (U-B10) |
| K41 | NEXT packs several clauses and commands into one prose line | breaking (Option A), **decision D28** | C (U-C13 record; U-C14b if A) |
| K42 | install.sh error copy does not follow the class formulas | breaking for prefixes, **decision D13**; the rest compatible | B (U-B8); C (U-C7 if approved) |
| K43 | install.sh `--help` is a 311-line dump of implementation notes | breaking (help output and one test), **decision D29** | C (U-C11) |
| K44 | install.sh shows test seams in help and uses the public prefix for them | compatible, **decision D30** | B (U-B8) |
| K45 | install.sh exits 0 when an optional helper did not build | compatible (Option B), **decision D31** | B (U-B8) |
| K46 | install.sh accepts any Python project as the checkout to install | compatible | B (U-B7) |
| K47 | install.sh mixes nouns for one concept | compatible | B (U-B8) |
| K48 | tenant-probes `--dry-run` writes a report when client_id is unset | compatible | B (U-B13) |
| K49 | tenant-probes reports failure only inside the file, with stderr silent | compatible | B (U-B14) |
| K50 | tenant-probes stdout is not a bare identifier and prints on failure; selftest failures go to stdout | breaking (the label only), **decision D32** | B (U-B14); C (U-C12) |
| K51 | tenant-probes exits 0 with probes that failed | breaking (Option A), **decision D33** | C (U-C12) |
| K52 | tenant-probes leaves raw tracebacks on common failures | compatible, **decision D34** | B (U-B15) |
| K53 | tenant-probes has no signal handling, so tenant files can outlive a stop | compatible | B (U-B15) |
| K54 | tenant-probes state and report writes are not atomic or locked | compatible | B (U-B15) |
| K55 | tenant-probes exit 77 and 78; exit 1 for a bad `--python` | breaking, **decision D10** | C (U-C12) |
| K56 | tenant-probes has no `--name=value` and no `--`; repeats are undefined | compatible | B (U-B13) |
| K57 | tenant-probes accepts conflicting and empty values | breaking (no in-repo caller) | C (U-C12) |
| K58 | tenant-probes lacks `--version` and `help` | compatible, **decision D12** for `-v` | B (U-B13) |
| K59 | tenant-probes option names and hints depart from the standard | compatible, **decision D35** | B (U-B14) |
| K60 | tenant-probes help is a raw comment dump | compatible | B (U-B14) |
| K61 | tenant-probes has no `--json` | compatible | B (U-B16) |
| K62 | tenant-probes error copy lacks the class formulas | compatible, **decision D13** | B (U-B14) |
| K63 | tenant-probes does not sanitize remote text and truncates messages | compatible | B (U-B14) |
| K64 | tenant-probes tests cover only the dry-run happy path | compatible | B (U-B13) |
| K65 | `purge` softens an irreversible erasure to "remove" (critic missedFindings) | compatible (help text), **decision D36** for a rename | B (U-B1a); C (U-C8b if A) |
| K66 | `hold --release` of nothing exits 1 (R326; found verifying the critic) | breaking, **decision D37** | C (U-C6) |
| K67 | Counts print no thousands comma (R217; found fixing §5) | breaking for two parsers, **decision D38** | C (U-C15 if A) |
| K68 | agentsync has no `--help --json`, `help --all --json`, `help search` or `--fields` (R106-R108, R252) | compatible | C (U-C1b) |
| K69 | agentsync help has no Examples or task grouping (R088, R091, R092, R096, R100, R101) | compatible | C (U-C1) |
| K70 | A failed `purge --push` loses the run's suppressions, audit line and report (found verifying the critic) | compatible | B (U-B2) |

Totals: 12 compatible (K3, K5, K7, K8, K9, K11, K13, K14, K15, K17, K19, K20) and 8 breaking (K1, K2, K4, K6,
K10, K12, K16, K18). The contract for each is the verifier's verdict, not the finder's label.

Round 2 totals, by the same rule:
- K21-K64: 32 compatible and 12 breaking (K21, K27, K31, K32, K33, K41, K42, K43, K50, K51, K55, K57).
- K65-K70: 4 compatible and 2 breaking (K66, K67).
- The whole plan: 48 compatible and 22 breaking, out of 70.
- A verdict of "breaking" often applies to one option only. Under the recommended options, K21, K32, K33's Part A
  and K41 change no default output or exit code.

---

## 2. Wave A: the shared helper (guide: "Enforce the rules in shared code", R408-R417)

**Shared cause.** No layer sits between commands and the streams. All 24 `_cmd_*` handlers write through two
bare functions: `_out` (cli.py:185, `sys.stdout.write`) and `_err` (cli.py:189). Measured on origin/main with
`grep -c`: 120 `_out(` calls, 57 `_err(` calls, and 33 free-text `error: ` lines. src/ has 0 `isatty` calls (git
grep) and no SIGTERM/SIGHUP handler.

Because of this, eight kept findings each have to be fixed site by site unless one module owns:
- stream routing (K1, K20)
- the closed-stdout rule (K9)
- the signal rule (K9)
- the JSON writer and its error document (K8)
- the bug report (K10)
- remote-text sanitizing (K11)
- the interactive-mode decision (K12)
- the recovery layout (K5, K7, K18)

### Module: `src/agentsync/clio.py` (new, ~240 LOC estimated)

It has no heavy imports (os, sys, signal, json, contextlib, enum, platform). It never uses `print`.

```python
# streams (R016, R177, R341, R410)
def result(text: str = "") -> None            # stdout. On BrokenPipeError: dup2(devnull -> fd 1), set the closed
                                               # flag, return. A no-op in JSON mode.
def say(text: str) -> None                    # stderr, after flushing stdout first (moves _err's B13 r11 M26
                                               # flush here). A no-op in JSON mode.
def stdout_closed() -> bool                   # True once result() met EPIPE
def table(header: Sequence[str], rows: Iterable[Sequence[str]], *, to: Callable[[str], None] = result) -> None
                                               # off a TTY: header + rows joined by exactly one tab, no padding
                                               # (R189); cells pass through text.safe_text(cell=True)
# mode (R408, R014, R172): the only module allowed to call isatty
def stdout_is_tty() -> bool
def interactive() -> bool                     # sys.stdin.isatty() and not CI and not SSH_CONNECTION/SSH_TTY.
                                               # Deliberately ignores stdout/stderr, so `login 2>&1 | tee` still
                                               # works (K12 verdict).
# JSON (R243, R248, R253, R410, R414)
def set_json(on: bool) -> None
def json_mode() -> bool
def emit(doc: Mapping[str, object]) -> None   # one document on stdout. Round 2 fix (R251): 2-space indent when
                                               # stdout is a TTY, compact one line otherwise
class Reason(enum.StrEnum): ...               # usage, config_invalid, lock_held, login_required,
                                               # sign_in_blocked, sign_in_failed, state_unreadable,
                                               # state_misplaced, state_refused, governance_unreadable,
                                               # manifest_schema, confirmation_required, failed,
                                               # internal_error, canceled, api_error, rate_limited,
                                               # network_policy
                                               # Round 2 fix: "canceled" replaces round 1's "interrupted"
                                               # (R262); the last three cover Graph errors (R296, R299)
def error_doc(reason: Reason, message: str, *, status: str = "error", next: Sequence[Mapping[str, object]] = (),
              data: Mapping[str, object] | None = None, user_action_required: bool = False,
              retryable: bool | None = None, retry_after: float | None = None,
              request_id: str | None = None, warnings: Sequence[Mapping[str, str]] = ()) -> None
                                               # exactly one line on stderr, JSON mode only; human mode no-op.
                                               # Round 2 fix: status is "action_required" when blocked before
                                               # any effect (R254); next is [{"command","argv","when"?}]
                                               # (R249, R257), not a string; userActionRequired, retryable,
                                               # retryAfter and requestId appear only when set (R250, R258,
                                               # R259); it is the last line of stderr (R261)
def confirm(*, tier: str, effects: Sequence[str], name: str | None, argv: Sequence[str],
            consequence: str, yes: bool) -> bool
                                               # Round 2 addition (R146, R147, R150, R263, R409, R413): the one
                                               # consent helper; True when --yes, or when an interactive() run
                                               # answers. tier "destructive" asks "...? (y/N)", default No;
                                               # tier "severe" asks 'To confirm, type the <noun> name "<name>":'
                                               # Non-interactive, or in JSON mode, it never prompts: it prints
                                               # the preview and error_doc(confirmation_required,
                                               # status="action_required", user_action_required=True,
                                               # data={"effects": [...]}, next=[{..., "when": "Only after a
                                               # human approves: <consequence>"}]) and returns False
# human error layout (R276-R280)
def recovery(instruction: str, command: str) -> None   # "", "<instruction>:", "    <command>" on stderr
# internal bug (R295, R283)
def bug_report(exc: BaseException, command: str | None, *, version: str, issues_url: str) -> None
                                               # "error: Unexpected error in agentsync <cmd>. This is a bug in
                                               # agentsync."; "  Error: <Type> at <module>:<line>" (innermost
                                               # agentsync frame, no str(exc), no paths);
                                               # "  Version: agentsync <v>, Python <x.y.z>, macOS <ver>";
                                               # "  Report: <issues_url>/issues/new" (no argv)
# signals (R335, R416)
class Terminated(KeyboardInterrupt): signum: int
@contextlib.contextmanager
def signals() -> Iterator[None]               # SIGTERM/SIGHUP raise Terminated(signum); previous handlers are
                                               # restored in finally; main thread only
```

Additions to `src/agentsync/text.py` (the existing shared formatter, which today holds only `plural`), ~30 LOC:
- `UNPRINTABLE_RE`: lifted unchanged from `convert/vtt.py:42` (C0, DEL, C1, surrogates, U+2028/2029, tag
  characters, blank fillers).
- `safe_text(s: str, *, cell: bool = False) -> str`. It:
  - strips CSI/OSC escape sequences first;
  - strips `UNPRINTABLE_RE` matches;
  - strips category `Cf` (bidi and zero-width);
  - shows `\n` as the two characters `\n`;
  - with `cell=True`, also shows `\t` as `\t`.
- The sanitizer lives in `text.py`, not `clio.py`, so `convert/vtt.py` can share the character class without
  importing a CLI module.

Note on the `reason` name: errors.py already has a `reason` attribute that holds strerror words (errors.py:28-31,
checkout). The JSON key stays `"reason"`, as R253 requires, and is always filled from `clio.Reason`, never from
`exc.reason`. A new typed-error attribute, if one is added later, is named `code`.

**Callers.** U-A1 changes none of them. Wave B rewires them:
- `cli._out` becomes `clio.result` and `cli._err` becomes `clio.say` (U-B1b), keeping the two private wrappers so
  existing monkeypatches keep working.
- `vtt._UNPRINTABLE_RE` becomes `text.UNPRINTABLE_RE` (U-B3).

**Tests (`tests/test_clio.py`, new, ~190 LOC estimated):**
- `test_result_survives_a_closed_stdout`: a pipe whose read end is closed is dup2'd over fd 1 (restored after).
  Assert that `clio.result("x")` raises nothing and that `clio.stdout_closed() is True`.
- `test_say_flushes_stdout_first`: `result("a"); say("b")`. Assert `out == "a\n"` and `err == "b\n"`.
- `test_json_mode_mutes_human_lines`: `set_json(True); result("x"); say("y"); emit({"a": 1})`. Assert
  `out == '{"a":1}\n'` and `err == ""`.
- `test_error_doc_is_one_line`: assert `len(err.splitlines()) == 1`, that `json.loads(err)` equals
  `{"status": "error", "reason": "config_invalid", "message": "m", "next": [{"command": "c", "argv": ["c"]}],
  "data": {"k": "v"}}`, and that `out == ""`. Round 2 fix: round 1 pinned `"next": "c"`, which R257 does not allow.
- Round 2 additions, all in `tests/test_clio_json.py` (new, U-A1b):
  - `test_emit_is_pretty_on_a_tty_and_compact_off_it` (R251): one pty case and one pipe case.
  - `test_action_required_carries_user_action` (R254, R257): assert `status == "action_required"` and
    `userActionRequired is True`, and that `retryable` is absent when unset.
  - `test_canceled_doc_carries_remote_state` (R262): assert `reason == "canceled"` and `data.remote == "none"`.
  - `test_confirm_never_prompts_without_a_terminal` (R146, R409): stdin is a pipe, so it returns False, reads no
    byte of stdin, and writes the preview and one `confirmation_required` line.
  - `test_confirm_defaults_to_no_and_a_wrong_name_names_the_expected_one` (R147, R150): a pty answers `""`, then a
    wrong name. Both return False, and the second output names the expected answer.
- `test_safe_text_strips_controls_bidi_zero_width`: input `"a\x1b[31mb‮c​d\x85e\nf"`. Assert the
  result contains no `\x1b`, `‮`, `​` or `\x85`, equals `"abcde\\nf"`, and that `cell=True` turns `"\t"`
  into `"\\t"`.
- `test_table_keeps_the_header_off_a_tty`: assert `out == "kind\tstate\na\tb\n"`.
- `test_interactive_is_false_under_ci_ssh_or_piped_stdin`: three `monkeypatch` cases, each asserting `False`.
- `test_signals_raise_terminated_and_restore`: `os.kill(os.getpid(), SIGTERM)` inside `with clio.signals()`
  raises `Terminated` with `.signum == 15`, and afterwards `signal.getsignal(SIGTERM)` is the previous handler.
- `test_recovery_layout`: assert `err == "\nTo remove, run:\n    agentsync offboard --confirm '/x'\n"`.
- `test_bug_report_leaks_no_message_or_path`: raise `ValueError("/Users/secret")`. Assert the output starts with
  the R295 headline, does not contain `"/Users/secret"`, contains `"Version: agentsync"`, and contains
  `it_request.REPO_URL + "/issues/new"`.
- `test_isatty_lives_only_in_clio` (R408 lint): scan `src/agentsync/**/*.py` for `isatty(` outside clio.py and
  assert the list is `[]`.
- `test_unprintable_re_is_the_vtt_class`: assert `text.UNPRINTABLE_RE.pattern` equals vtt's pattern as of today.

**CONTRACTS:** add a `agentsync.clio` module section and the `text.safe_text` / `text.UNPRINTABLE_RE` entries,
naming every public name: `result`, `say`, `stdout_closed`, `table`, `stdout_is_tty`, `interactive`, `set_json`,
`json_mode`, `emit`, `Reason`, `error_doc`, `confirm`, `recovery`, `bug_report`, `Terminated`, `signals`. About 40
lines.

**Wave A size:** about 470 LOC (estimated: 240 module + 30 text.py + 160 tests + 40 docs).

### Round 2 additions to Wave A

**Round 2 fix: U-A1 is split.** Round 1's size line counts 160 test LOC, but its test list says ~190, so the parts sum
to 500 and break the under-500 limit before any round 2 addition. It becomes two units on the same file, landed in
order. "U-A1" in the round 1 dependency rows means both have landed.

| Unit | Scope | Est. LOC |
|---|---|---|
| U-A1a | `result`, `say`, `stdout_closed`, `table`, `stdout_is_tty`, `interactive`, `recovery`, `Terminated`, `signals`, `text.safe_text`, `text.UNPRINTABLE_RE`; the stream, table, mode, signal, recovery, isatty-lint and sanitizer tests; CONTRACTS section | ~285 |
| U-A1b | `set_json`, `json_mode`, `emit`, `Reason`, `error_doc`, `confirm`, `bug_report`; their tests in `tests/test_clio_json.py` (new); the CONTRACTS names | ~300 |

**Round 2 note on the install.sh side.** R408-R417 ask for one writer, one reason registry and one signal rule per
CLI. install.sh is bash and runs before any Python exists, so it cannot use `clio`. U-A2 gives it its own small
layer, so the install.sh units below do not each invent one.

### A-2 `scripts/install.sh`: the stream layer (unit U-A2; no dependency beyond U-0)

**Rules for all of it:**
- bash 3.2 (tests run it with `BASH32`, tests/test_install_oneshot.py:496; tests/test_deploy_pack.py:565 runs `bash -n`).
- No jq and no python3: `tool_python` exists only after install, and /usr/bin/python3 may be a stub (install.sh:5336).
- `$0` is read for report words. install.sh's word-list awk skips only comment lines (install.sh:2120), so a new
  `say`/`printf` format or double-quoted string adds report words. Keep strings few, and run
  tests/test_report_redaction.py in the affected gate.

**A-2.1 K36 (R341): a closed stdout never kills the run.** `start_capture` (install.sh:1958-1975) and `--help` (:9213).
- Evidence (origin, read): no PIPE trap (`grep -n 'trap .*PIPE'`, 0 hits); the tees ignore only INT, TERM and HUP
  (:1967-1975); on_exit re-arms only those three (:7604-7606); :8699 mentions SIGPIPE for an internal sort only.
  --report-only, dry runs and a run with no install.out have no tee at all (:1959-1963).
- Change:
  1. Do **not** put `trap '' PIPE` in the main shell. Ignored signals are inherited by every child, and with stdout
     gone each of the 203 `say` lines (measured: `grep -cE '^\s*say '`, origin) would fail under `set -e` (:314).
  2. Keep the writers alive and silent when the reader goes:
     ```sh
     exec > >( trap '' INT TERM HUP PIPE; exec tee -a "$target" 2>/dev/null )
     exec 2> >( trap '' INT TERM HUP PIPE; exec tee -a "$target" >&2 2>/dev/null )
     ```
     The redirect order in the second line matters: stdout goes to the real stderr first, then tee's own stderr goes
     to /dev/null. A started mutation then finishes, on_exit writes the report and log, and the run exits with its
     real rc.
  3. Cover the paths with no tee: DRY_RUN, REPORT_ONLY and an uncreatable install.out use the same writer with
     `target=/dev/null`. Gate `trim_install_out` in stop_capture (:1980) on a real capture, so a dry run still changes
     nothing.
  4. In the `--help` arm (:9213), accept a 141 from show_help's awk (:1646) and still `exit 0`.
  5. rc_meaning (:1763) and every documented exit code stay as they are.
- Test (`tests/test_install_streams.py`, new, BASH32): Popen with `stdout=PIPE`, read one line, close the pipe, then
  assert the real returncode, the install.log `end` line, a complete install.out and no broken-pipe text on stderr.
  Parametrize over an install run, `--list-folders`, `--report-only`, a dry run and `--help`. BSD tee's behaviour after
  EPIPE comes from memory of its source, not a run, so this test must confirm it on macOS before the fix is trusted.
- Callers: no repo caller pipes install.sh (verifier's `git grep`, 0 hits). test_install_oneshot.py's Popen drains to
  EOF (:387). rc_meaning's word for 141 stays pinned (tests/test_install_oneshot.py:16577). install.out's `# run=` header stays
  (setup_report.py:4126).
- Decision: D25 (exit code of `--list-folders` when its reader closes).

**A-2.2 K22, shared half (R244, R253, R255, R264, R414): encoder, routing and reason registry.** No behaviour change
until U-B12 calls them.
- `json_str`: `LC_ALL=C awk` that escapes backslash, quote and control bytes. `rg 'json_(escape|str|quote)'` finds no
  existing helper.
- One routing rule, which reconciles K21 and K22: before `start_capture`, `exec 9>&1 8>&2`. fd 9 is the caller's stdout
  for the one JSON document and fd 8 is the caller's stderr for the one error document. **Round 2 fix:** K22's verdict
  said fds 3 and 4, but `uv_run` (:9361) takes fd 3. In a JSON run the tees write to install.out only
  (`tee -a "$INSTALL_OUT" >/dev/null`), and if `start_capture` returns early, human output goes to /dev/null. The JSON
  never enters install.out, so `listed_folder_lines` (:2385), the NEXT count (setup_report.py:7037) and the reader at
  :5142 all keep working.
- Children inherit fds 8 and 9, and one that outlives the run makes `x=$(install.sh --json)` hang on EOF (:1979 already
  notes a stopped first sync can hold output open). So `--json` starts on `--list-folders` only (D15), whose children
  are bounded `find` and `sort` calls.
- `reason_for_list`: one table beside `rc_meaning`, mapping the list branches (:9107-9176) to `denied`, `tcc_pending`,
  `not_signed_in` and `no_folders`. Derive it from the branch, never from the rc: `rc_meaning 3` means "the wait ran out"
  while list exit 3 means none or signed out, and 4 has no entry. Leave the hyphenated `note=` tokens alone, since
  install.sh (:7384) and setup_report read them.
- Test: `test_json_str_escapes_controls_and_quotes` under BASH32, and `test_the_registry_covers_every_list_exit`.

**U-A2 size:** about 260 LOC (estimated: 90 src, 150 tests, 20 docs).

---

## 3. Wave B: compatible fixes, grouped by file (one implementer owns each file group)

### B-1 `src/agentsync/cli.py`, part 1: parser and validation (unit U-B1a; no dependency on Wave A)

**B-1.1 K3 (R065): reject option prefixes.**
- Change:
  - Add `allow_abbrev=False` to the root `argparse.ArgumentParser(prog="agentsync", ...)` at cli.py:229.
  - Add `kwargs["allow_abbrev"] = False` in `add()` (cli.py:~241). argparse uses each parser's own flag; it is not
    inherited from parents.
  - Optionally set it on `_common` (cli.py:205) for consistency. It has no effect there.
  - `-vv` still parses on Python >= 3.11.
- Test: `tests/test_cli_parser.py::test_option_prefixes_are_usage_errors`, parametrized over
  `["offboard","--purge"]`, `["purge","x","--pu"]` and `["hold","all","--rel"]`. Each asserts
  `cli.main([*argv, "--config", str(cfg)]) == cli.EXIT_USAGE` and `"unrecognized arguments" in err`. The
  offboard case also asserts `initialised.docs_repo.exists()`.
- Test: `test_full_option_names_still_parse`. Assert
  `cli.build_parser().parse_args(["offboard","--purge-data"]).purge_data is True` and
  `parse_args(["-vv","status"]).verbose == 2`.
- Callers: none use a prefix (verifier's census of tests/, src/ and install.sh).

**B-1.2 K7 (R070, R302, R015): validate usage before any config load or write.**
- Change: add `_validate_usage(args) -> str | None`. Call it as the first statement of three handlers, and on a
  message do `_err(msg); return EXIT_USAGE`:
  - `_cmd_purge` (cli.py:3844), before `_config` (:3845) and before `governance.carry_round28_record` (:~3856,
    a write).
  - `_cmd_hold` (cli.py:4015), before `_config` and before `carry_round28_record` (:~4020).
  - `_cmd_reconcile` (cli.py:1292).
- Move these checks unchanged, keeping their exact texts, because tests assert them:
  - `purge: pass SELECTOR or --queue, not both`
  - `purge: --accept-lost <at> goes alone (with --dry-run or --folder-lost)`
  - `purge: --folder-lost goes with --accept-lost <at>`
  - `purge: --forget goes with --queue` (today at :3897, after `_open_state_or_raise` and the round-28 carry)
  - `hold: SCOPE ('all' or a source id) is required`
  - `hold: --owner (the records/legal owner) is required` (:4036)
  - `hold: --reason is required to set a hold`
  - `--accept-deletions needs a source: run {loop.BIN} accept-deletions SOURCE` (:1295)
- Add these conflicts, each naming both options:
  - `hold --list` with SCOPE, `--release`, `--reason` or `--owner`
  - `hold --release` with `--reason`
  - `purge --queue` with `--source` but no `--forget`
  - `purge --queue --push`, unless `run_purge_queue` on trunk takes `push`; check before adding it.
- Do not use argparse groups. "--forget needs --queue" cannot be expressed with them, and declaration order must
  stay as it is for `CLI_SURFACE`.
- Test: `tests/test_cli_parser.py::test_purge_usage_conflict_writes_nothing`. Run `["purge","id=x","--forget","id=y"]`
  against an older-schema manifest. Assert it returns 2, that `not list(db.parent.glob(db.name + ".pre-v*"))`, and
  that the sorted `(name, mtime_ns)` listing of the state folder is the same before and after.
- Test: `test_hold_list_with_release_is_usage`. Assert `["hold","all","--list","--release","--owner","o"]` returns
  2, that `"--list"` and `"--release"` are both in err, and that `governance.state_holds(root) == ()`.
- Test: `test_usage_beats_an_unloadable_config`. With a broken sources.toml, assert
  `["purge","--queue","id=x"]` returns 2, not 78.
- These existing tests stay green with no change: `test_cli.py` reconcile, `purge` alone, `hold` without
  `--owner`, `purge --forget` without `--queue`, `hold --list` (verifier's list).

**B-1.3 K17 (R050, R049, R111): hidden legacy names always warn.**
- Change: add `_deprecated(old: str, instead: str) -> None`, which writes
  `warning: \`agentsync {old}\` is deprecated: {instead}` once per process through `_err`. Call it from:
  - `_cmd_checkpoint` (cli.py:3649): "run `{loop.BIN} sync`"
  - `_cmd_install_skill` (:3658): "`{loop.BIN} sync` writes the skill"
  - `_cmd_migrate` (:3678): "every command that changes state migrates the manifest". Keep the stdout
    "migration is automatic" line; tests assert its prefix.
- In `_cmd_status_renamed` (:2436), drop the `NO_NEXT_HINT_ENV` condition so `renamed: run {loop.BIN} status`
  always prints. Keep the text, which tests/test_cli.py:183-185, :1865 and :3799 assert.
- Stdout bytes and exit codes stay the same.
- Re-pin in the same commit: `tests/test_cli.py::test_every_cycle_command_runs_syncs_guard_of_a_state_file_that_is_not_a_file`
  (:9224, exact stderr at :9244). For `argv == ["checkpoint"]`, the expected stderr becomes
  `[deprecation_line, "error: state: ..."]`.
- Test: `tests/test_cli_parser.py::test_hidden_legacy_names_warn_once`, parametrized over checkpoint,
  install-skill, migrate, doctor and `policy show`, with `AGENTSYNC_NO_NEXT_HINT=1` set. Assert
  `err.count("deprecated") + err.count("renamed: run") == 1` and that the exit code equals the replacement
  command's.

**B-1.4 K19 (R079): document the public environment variable.**
- Change: add an `environment:` block to `_EPILOG` (cli.py:~155-180). Every line is at most 80 columns, and
  commands are named only as `{loop.BIN}`:

  ```
  environment:
    AGENTSYNC_CONFIG  path to sources.toml (default ~/agent-context/sources.toml);
                      empty or unset uses the default; --config wins
  ```

  Check the empty-value wording against `paths.py:29-36` before committing.
- In the module docstring and CONTRACTS, mark `AGENTSYNC_NO_NEXT_HINT`, `AGENTSYNC_QUIET_LOCK_WAIT`,
  `AGENTSYNC_LOCK_WAIT_ANNOUNCED` and `AGENTSYNC_FRICTION_LOG` as install.sh-to-agentsync interface. Do not rename
  them; R079 keeps legacy names.
- Test: `tests/test_cli_parser.py::test_help_documents_agentsync_config_only`. Assert that `--help` out contains
  `"AGENTSYNC_CONFIG"`, does not contain `"AGENTSYNC_NO_NEXT_HINT"`, and that every line of the block is at most
  80 columns. The existing help tests (tests/test_cli.py:138, :171) stay green.

**B-1.5 K16, compatible part (R436, R067, R434).**
- Change: the `--keep-days` metavar becomes `"<NUMBER>"` (cli.py:433).
- **Round 2 fix.** Round 1 wrote `"NUMBER"`. Reference E and R067 render hints in angle brackets, the same standard
  K59 applies to tenant-probes. So every visible metavar gets them in this unit:
  - `--url <URL>` (cli.py:221), `<PATH>` (:268, :344, :347), `<SOURCE>` (:285, :294), `<SELECTOR>` (:379, :391),
    `<SCOPE>` (:437), `--budget <BYTES>` (:342), `--confirm <DOCS_REPO>` (:446).
  - `--source ID` (:303, :382) becomes `--source <SOURCE>`. R434 bans a bare `id` when the noun is known, so round 1's
    "nouns are allowed" did not cover it.
  - `AT` (:401) becomes `<TIME>` (R436), keeping the help text's accepted form.
  - Hidden options' metavars and the literal `--budget BYTES` in fix strings stay: tests/test_loop.py:252 and :326 pin
    that text, and it names a placeholder, not help output.
  - Pins, measured with `git grep -E -- '--keep-days N|--source ID|--accept-lost AT'` on tests, README.md and
    docs/deploy: 0 hits. `CLI_SURFACE` pins option names, not metavars.
- Test: `test_keep_days_hint_is_number`. Assert `["compact-history","--help"]` out contains `"--keep-days <NUMBER>"`.
- Test (Round 2): `test_visible_hints_come_from_the_allowed_list`. Walk `build_parser()`'s visible actions and assert
  each metavar is angle-bracketed, is not `<N>`, and is in Reference E's list or a named noun.

**B-1.6 K65, compatible part (R034, R035, R033): name the consequence in help (Round 2).**
- Sites: cli.py:375 ("remove items from the docs repo's whole history, cache and manifest"), :443 (offboard's summary),
  :444 ("also remove the docs repo and sources.toml"), :446 ("required to remove anything"). All are irreversible
  erasure, with `purge --push` force-pushing it (:425). Reference A: delete destroys; remove only detaches; purge is
  only for cached content.
- Change:
  - :375 becomes "permanently delete items from the docs repo's whole history, cache and manifest".
  - :443 becomes "list (default) or delete every local copy agentsync made".
  - :444 becomes "also delete the docs repo and sources.toml".
  - :446 becomes "the docs repo path: required to delete anything".
  - Help text only (R383: no migration). Receipts (`removed:`, `nothing removed`, `would be removed`) stay, because
    15 lines in 5 test files pin them (measured with `git grep -cE`: test_cli 8, test_e2e 2, test_publish 2,
    test_setup_report 2, test_graph_auth 1). The command name stays under D36.
- Test: `test_purge_help_names_the_erasure`. Assert `"permanently delete"` is in `["purge","--help"]` out and `"remove
  items"` is not.
- Callers: no test or doc pins the four strings (measured: `git grep` on tests, docs and README.md hits only cli.py).

U-B1a also adds its own CONTRACTS subsection. Estimated size: about 290 LOC (100 src, 170 tests, 20 docs). Round 2:
about 340 LOC with B-1.5's wider metavar change and B-1.6.

### B-2 `src/agentsync/fsutil.py` (new), `governance.py`, `skill.py`, `gitops.py`, `curate.py` (unit U-B2; no dependency)

**B-2.1 K13 (R351, R353): one atomic-write helper.**
- Change: create `fsutil.atomic_write(path: Path, data: str | bytes, *, mode: int | None = None) -> None`, lifted
  from `curate._atomic_write` (curate.py:921). It:
  - creates the temp file with `mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")` (O_EXCL, 0600);
  - writes, flushes and fsyncs;
  - fchmods to `mode`, or to the target's current bits when `mode is None`, or to 0600 for a new file;
  - `os.replace`s it into place;
  - unlinks the temp on `BaseException`.
- Convert these writers:
  - `skill._write_if_changed` (skill.py:258, `.agentsync-tmp`)
  - `gitops.py:297` (same fixed name; not cited by the finding but the same defect)
  - `governance._scrub_file` (governance.py:4442/4454, `.scrub.tmp`): use `mode=None` so the page keeps its mode
  - `curate._atomic_write`: it delegates, or its callers move and it is deleted (the private-function scan)
- Leave `config._replace_config` (config.py:742): it is already mkstemp-based.
- `cli._write_config` moves in U-B1b, because cli.py is not this unit's file.
- Test: `tests/test_fsutil.py::test_a_symlink_at_the_old_fixed_temp_name_is_not_followed`. Pre-create
  `.SKILL.md.agentsync-tmp` as a symlink to `tmp_path/"outside"`. Run the skill write. Assert outside is unchanged
  and SKILL.md is written.
- Test: `test_an_interrupted_replace_keeps_the_old_file`. Monkeypatch `os.replace` to raise. Assert the old bytes
  are intact and `not list(p.parent.glob(f".{p.name}.*.tmp"))`.
- Test: `test_mode_none_keeps_the_existing_bits`. A 0o640 file stays 0o640, and a new file is 0o600.
- Test: `test_scrub_keeps_the_page_mode`. A 0o644 page is still 0o644 after `_scrub_file`.
- Existing: tests/test_skill.py's "wrote skill" assertions are unchanged.

**B-2.2 K14 (R368, R319): lock holds.json's read-modify-write.**
- Change: add `_holds_lock(state_dir)` in governance.py. It is a contextmanager that takes `fcntl.flock(LOCK_EX)`
  on an `O_RDONLY|O_DIRECTORY|O_CLOEXEC` fd of the **governance/** folder. That is a different inode from
  `_record_lock`'s state-folder flock (governance.py:1142), so the two cannot deadlock. It is **never** the
  SingleWriterLock: `_record_lock`'s docstring says no hold is refused while a sync runs.
- In `set_hold` (:1512), move inside the lock: the `_state_holds` read, the list build, `_audit_appender` and
  `_atomic_write_json`.
- In `release_hold` (:1537), also move the `lost_holds` check and the `return False` inside the lock.
- Call `record_holds(...)` after the lock is released. Never nest it: a second flock on a new fd in the same
  process blocks.
- No caller changes.
- Test: `tests/test_governance_holds_lock.py::test_parallel_sets_keep_every_hold`. Two spawned processes each
  `set_hold` 25 distinct valid scopes (check `_check_scope`). Assert `{h.scope for h in state_holds(root)}` equals
  all 50, and that audit.jsonl has 50 `hold-set` lines.
- Test: `test_a_release_racing_a_set_is_never_undone`. After both finish, assert that scope A is in holds.json
  if and only if the last audit action for A is `hold-set`.
- Test: `test_a_hold_is_not_blocked_by_the_sync_lock`. With `SingleWriterLock` held in-process, `set_hold` in a
  thread joins within 5 s.

**B-2.3 K70 (R003, R017, R293, R300, R336): a failed `purge --push` must not lose the purge's record (Round 2).**
- Why it is here: the critic asked for R293, R300 and R317 to be checked on Graph. Graph is fine (see §7). The one
  non-idempotent network call in the CLI is this push, and its failure path is the defect.
- Evidence (origin, read):
  - `push_rewritten` (governance.py:3945) runs `_git(repo, "push", ...)` (:3972), which raises `GovernanceError` on a
    non-zero exit or a timeout (`_GIT_TIMEOUT_S = 3600`, :124). It is not wrapped.
  - The call (:4765) comes after the history rewrite, the cache removal and the log scrub, and before
    `_add_suppressions` (about :4775) and `_audit_purge` (:4803).
  - So a refused or timed-out push ends in `error:` and exit 1 with the local erasure done, no suppression list
    ("the content must not come back") and no audit line. The next sync can bring the content back.
  - There is no retry, so R020 and R315 hold.
- Change: in `purge`, wrap the one call:
  ```python
  try:
      remote = push_rewritten(repo, gov, result.old_refs)
  except GovernanceError as exc:
      remote = f"push failed, remote state unknown: {exc}"
      push_failed = True
  ```
  The suppressions, the audit line and the report then run as normal. `_print_purge` already prints `remote:` (cli.py
  `rep.remote` line). `_cmd_purge` returns `EXIT_FAILED` when the report says the push failed, in both return lines
  (cli.py:3973 for the queue, :3989 for one selector), the same code as today, and adds recovery by inspecting, never a blind retry (R293, R317): `clio.recovery("Compare the remote with the
  rewritten history", f"git -C {repo} ls-remote {remote_name}")`. Before U-A1 lands, a plain `_err` line does the job.
  `--force-with-lease` carries the old values, so a second push cannot silently overwrite newer work, but it may also
  be refused if the first push landed. Say that, rather than "retry".
- Test (`tests/test_governance_push.py`, new): run a purge with `push=True`, `allow_remote` on, and a remote URL that
  refuses (a hook in a bare tmp repo that exits 1).
  - Assert the report's `remote` starts with `"push failed"`.
  - Assert `lost_suppressions` finds nothing and the suppression file has the item.
  - Assert audit.jsonl has the purge line.
  - Assert the CLI exit is 1 and `"remote state unknown"` is in err.
- Callers: tests/test_governance.py:1543-1556 (`push_rewritten` return values) stay, because the catch is in the caller.
  test_cli.py's purge tests never push. The string "push failed" is new, so nothing parses it. CONTRACTS gets a dated
  note beside the purge row.

CONTRACTS: add the `agentsync.fsutil` section (`atomic_write`) and a governance note. Estimated size: about 300
LOC. Round 2: about 395 LOC with B-2.3, folded here because it is below the session band alone and needs governance.py.

### B-3 `graph/auth.py`, `graph/discover.py`, `convert/vtt.py`, `cycle.py`, `scripts/tenant-probes.sh` (unit U-B3; after U-A1)

**B-3.1 K11 (R359, R297): sanitize at the source modules.**
- Change:
  - `discover._comment` (discover.py:559) uses `text.safe_text` instead of its C0-only regex.
  - auth's `_describe_error` / `_first_line` (find them by `grep -n _first_line src/agentsync/graph/auth.py`)
    pass the MSAL `error_description` through `safe_text`. Keep the `sign-in failed: <error> <AADSTS>` prefix.
  - `vtt._UNPRINTABLE_RE` (vtt.py:42) becomes `text.UNPRINTABLE_RE`, with identical behaviour.
- Test: `tests/test_cli_text_hygiene.py::test_discover_comment_strips_c1_bidi_and_zero_width`. Assert
  `discover._comment("a\x85b‮c​d")` contains none of the three.
- Test: `test_describe_error_strips_escape_sequences`. Assert there is no `"\x1b"` and that
  `.startswith("sign-in failed: invalid_grant AADSTS50105")`. That is the prefix tests/test_graph_auth.py pins.
- Existing vtt converter tests are unchanged.

**B-3.2 K18, compatible part (R195).**
- Change: cycle.py:7173 `ConfigError(f"unknown source id(s): ...")` uses `id` for one and `ids` for several. No
  test pins it (measured with `git grep -n 'unknown source id' origin/main -- tests`, 0 hits).
- Test: `test_one_unknown_source_is_singular`. Assert `cli.main(["accept-deletions","nope","--config",cfg])` returns
  `cli.EXIT_CONFIG` and that `"unknown source id: nope"` is in err.

**B-3.3 K16, compatible part (R047).**
- Change: tenant-probes.sh:525 teaches `agentsync login --device-code`; change it to
  `agentsync graph login --device-code`, the one spelling every other site teaches.
- Test: `test_tenant_probes_teach_graph_login`. Assert the file text has no `"agentsync login --device-code"`.
  The shellcheck gate also covers this file.

Estimated size: about 140 LOC.

### B-4 `src/agentsync/cli.py`, part 2: streams, signals and copy (unit U-B1b; after U-A1, U-B2 and U-B1a)

**B-4.1 K9 (R341, R335, R416, R294, R336).**
- Change:
  - `_out` calls `clio.result` and `_err` calls `clio.say` (cli.py:185/189). Keep both wrappers.
  - In `_main` (:4556), wrap `handler(args)` in `with clio.signals():`.
  - Return `EXIT_OK` when `clio.stdout_closed()` and the command is a read: status, doctor, policy, curate,
    whoami, discover, and hold when `--list`. Otherwise return the handler's real code, because a mutation that
    was already sent finishes (R341).
  - Change `except KeyboardInterrupt as exc:` (:4719) to return `128 + exc.signum` for `clio.Terminated`, and 130
    otherwise.
  - Keep the substring `interrupted` and extend the line:
    - `interrupted: Canceled. Nothing was changed.` when no cycle took the lock. Set a module flag
      `_cycle_started` where `_cmd_sync` enters the single-writer lock.
    - Otherwise `interrupted: Stopped.` followed by `clio.recovery("See what the last pass committed", f"{loop.BIN} status")`.
  - Do not use a global `signal(SIGPIPE, SIG_DFL)`, because git children rely on Popen's own restore.
  - Leave ocr.py's SIGINT handling alone.
- Test: `tests/test_cli_streams.py::test_a_closed_stdout_ends_status_quietly`. Run a subprocess
  `[sys.executable, "-m", "agentsync", "status", "--config", cfg]`, close `proc.stdout` at once, and assert
  `proc.wait() == 0` and that `"Traceback"` and `"BrokenPipeError"` are not in stderr.
- Test: `test_sigterm_during_sync_cleans_up_and_exits_143`. Monkeypatch `cycle_mod.Manifest` to call
  `os.kill(os.getpid(), signal.SIGTERM)`, the same pattern as the KeyboardInterrupt test at tests/test_cli.py:9485.
  Assert `== 143`, `"interrupted" in err`, that `agentsync.lock.json` is absent, and that the SIGTERM handler is
  restored.
- Test: `test_sighup_exits_129`, the same with SIGHUP.
- Existing: tests/test_cli.py:9497 (`"interrupted" in captured.err`, rc 130) is unchanged.

**B-4.2 K5 (R004, R280, R150).**
- Change: in `_cmd_offboard` (:4050), before `governance.offboard` (:4053), add a check:
  - Condition: `args.confirm` is set, it is not a dry run, and the path does not match the docs repo under
    **governance's own normalization**. Read governance.py:~6700-6713 and reuse the same normalization, so the
    CLI and the library agree.
  - Action: `_err("error: Can't offboard: --confirm doesn't match the docs repo path.")`, then
    `clio.recovery("To remove, run", f"{loop.BIN} offboard --confirm {shlex.quote(expected)}" + (" --purge-data" if args.purge_data else ""))`,
    then return `EXIT_FAILED` (the same code as today).
- The dry-run hint (:4063) also uses `shlex.quote` and carries `--purge-data`.
- Leave `governance.py:6713` as it is: it is the library backstop, and test_governance's `match="confirm="` still
  passes.
- Test: `tests/test_cli_conventions.py::test_offboard_wrong_confirm_prints_a_runnable_command`. Assert:
  - the call returns `EXIT_FAILED`;
  - `"confirm=" not in err`;
  - `f"    {loop.BIN} offboard --confirm {shlex.quote(str(docs))} --purge-data" in err.splitlines()`;
  - the docs repo still exists.

**B-4.3 K20 (R189, R178, R016).**
- Change, offboard: the rows (:4055) go through
  `clio.table(("kind","state","location","action"), rows)`. The header prints only off a TTY, and the row bytes
  are unchanged.
- Change, discover (`_discover`, :3713):
  - The scope rows (:3738), the blank line and a header (`kind name configured drive_id site note`) go to stderr
    through `clio.table(..., to=clio.say)`.
  - stdout carries only the TOML snippet.
  - `--toml` keeps parsing and now only drops the stderr table. Update its help text.
- Test: `test_offboard_table_has_a_header_off_a_tty`. Assert
  `out.splitlines()[0] == "kind\tstate\tlocation\taction"`. tests/test_cli.py:3769 (`"state-dir\texists" in out`)
  still passes.
- Test: `test_discover_stdout_is_valid_toml`. Reuse the fakes of
  `test_discover_prints_the_snippet_and_fails_when_incomplete` (tests/test_cli.py:2019). Assert
  `tomllib.loads(out)` succeeds and that `"kind\tname"` is in err. :2044 still passes.

**B-4.4 K11, CLI print sites.**
- Change: apply `text.safe_text(..., cell=True)` to `scope.name`, `scope.note`, `scope.site` and
  `scope.configured` in the discover rows.
- Apply `safe_text` to `f.code` in `discovery incomplete:` (:3745) and to `exc` in the `sign-in blocked/required/failed`
  lines (:~4600-4608) and `error: {exc}` (:4639).
- Keep the stable prefixes. `GraphError.__str__` stays unchanged (CONTRACTS documents it).
- Test: `test_discover_strips_terminal_controls_from_remote_names`. Use the scope name
  `"Q3\x1b]0;x\x07‮plan​\tB"`. Assert out+err contains none of `\x1b`, `\x07`, `‮` or `​`,
  and that `"\\t"` is in the cell.
- Test: `test_a_graph_error_message_is_sanitized`. A handler raises `GraphError` with `"\x1b[2J"` in its message.
  Assert `"\x1b" not in err`.

**B-4.5 K18, compatible part (R227).**
- Change: in `hold --list` (:4030), when there are no holds, call `_err("No active holds.")` and print nothing on
  stdout. Keep `plural(n, "active hold")` on stdout when n >= 1, because tests/test_cli.py:3749 and :6531 read
  `"1 active hold"` there.
- Test: `test_an_empty_hold_list_is_stderr_only`. Assert `out == ""` and `"No active holds." in err`.

**B-4.6 K13, CLI site.**
- Change: `_write_config` (:998) calls `fsutil.atomic_write(cfg_path, text, mode=0o600)`. Drop the separate
  chmod and keep `wrote {cfg_path}`.
- Test: `test_init_leaves_no_partial_sources_toml`. Monkeypatch `os.replace` to raise during `init`. Assert
  `not cfg.exists()` and `not list(cfg.parent.glob(".sources.toml*"))`.

Estimated size: about 380 LOC (130 src, 230 tests, 20 docs).

### B-5 `src/agentsync/cli.py` plus `tests/test_contracts.py`: opt-in `--json` (K8; R243, R114, R248, R253, R255, R414) (units U-B4, then U-B5)

The flag is strictly opt-in. Without `--json`, every byte of stdout and stderr and every exit code stays as it is.
That protects these callers:
- install.sh's prefix greps (`[FAIL`, `NEXT: `, `WAITING ON YOU: `, `^configuration error: `)
- the README agent workflow
- the test files that assert prefixes (31 of them, measured in the checkout by the verifier with grep)

**U-B4 (about 420 LOC).**
- Add `--json` to `_common` (cli.py:202), and in `_main` call `clio.set_json(args.json)`.
- In the except-ladder (:4566-4720), each branch keeps its human `_err` lines (no-ops in JSON mode) and adds one
  `clio.error_doc(...)` call (a no-op in human mode). Reasons map to unchanged exit codes:

  | Exception | Reason | Exit |
  |---|---|---|
  | `LockHeldError` | `lock_held` | 75 |
  | `ConfigError` | `config_invalid` | 78 |
  | `StateUnreadableError`, `sqlite` unreadable | `state_unreadable` | 65 |
  | `AuthBlockedError` | `sign_in_blocked` | 77 |
  | `AuthRequiredError` | `login_required` | 77 |
  | `ManifestSchemaError` | `manifest_schema` | 1 |
  | `AuthError` | `sign_in_failed` | 1 |
  | `ManifestOpenError`, `PermissionError` | `state_refused` | 1 |
  | `ManifestPathError`, `FileExistsError`, `NotADirectoryError`, `LockNotAFileError` | `state_misplaced` | 1 |
  | `GovernanceUnreadError` | `governance_unreadable` | 1 |
  | `GraphThrottled` (Round 2, R258, R259) | `rate_limited`, with `retryable: true` and `retryAfter` | 1 |
  | `NetworkPolicyError` (Round 2) | `network_policy` | 1 |
  | `GraphError` (Round 2, R296, R299) | `api_error`, with `data.status` and `requestId` | 1 |
  | `AgentSyncError` | `failed` | 1 |
  | `KeyboardInterrupt` | `canceled` (Round 2 fix, R262; round 1 said `interrupted`), with `data.remote` | 130 |

  Round 2 notes on this table:
  - The exception types stay in the human ladder at their current arm: `GraphError` and its subclasses reach
    `except AgentSyncError` (cli.py:4669). The JSON branch tells them apart there, in one mapping function (R296). The
    client already builds typed errors in one place (`graph/client.py:520`).
  - `AuthBlockedError` and `AuthRequiredError` also set `userActionRequired: true` (R169, R257) and `next` to
    `graph login`. `LockHeldError` sets `retryable: true` (R259), since repeating is safe.
  - `status` is `action_required` for `login_required`, `sign_in_blocked` and `confirmation_required`, and `error`
    otherwise (R254).
  - `data.remote` is `none` when no cycle took the lock and `unknown` otherwise (R262, R336). Round 1's §5 waived this
    for human output only.
  - Exit 77 and 78 here depart from R260 ("auth exits 1"). That is the D10 conflict; the table keeps today's codes.

- An argparse usage error with `--json` in argv emits `usage` (2). Until U-C1's `error()` override lands, detect
  this by pre-scanning argv.
- Result documents for this unit:
  - `status`: `{"status": {"checks": [{"id","ok","severity","message","fix"}], "next": [...], "waitingOnYou": [...]}}`,
    built from `_Status` so the text and JSON views cannot drift. Round 2 fix (R246): camelCase, not `waiting_on_you`.
  - `curate`: `{"findings": [...], "next": ...}`
  - `sync`: `{"run": {"id","mode","commit","sources": [...]}, "outcome": "committed"|"unchanged"}`
- `tests/test_contracts.py`: add `"--json"` to the `common` set (:159) and to `CLI_GLOBAL_OPTIONS` (:52) in
  declaration order. CONTRACTS §16.10 gets a note. Add a README line next to the NEXT:-line contract.
- Test: `tests/test_cli_json.py::test_status_json_is_one_document`. Assert the rc equals the human run's, that
  `"status" in json.loads(out)`, and that `err == ""`.
- Test: `test_a_config_error_is_one_json_line_on_stderr`. Assert rc == 78, `out == ""`,
  `len(err.splitlines()) == 1` and `json.loads(err)["reason"] == "config_invalid"`.
- Test: `test_every_command_accepts_json`. Parametrize over `CLI_SURFACE["visible"]` and assert `.json is True`.
- Round 2 additions to U-B4:
  - `--version --json` prints `{"version": "0.1.0"}` and `--help` wins when both are given (R086). argparse's
    `action="version"` cannot read `--json`, so `_main` pre-scans argv. Test: `test_version_json_is_one_document`.
  - `sync`'s run document and `status` carry `warnings` and `next` in the R249 shape: `[{"command","argv","when"?}]`,
    built from the same `loop.next_lines` data. `next` ignores guidance settings.
  - Inside a document, an empty list is `[]` and a known-absent value is `null` (R250).
  - Test: `test_no_ansi_and_clean_streams_in_json` (R395): parametrized over status, curate and sync, `"\x1b"` not in
    out or err, one document on out, and an empty err on success.
  - Round 2 size: about 475 LOC.

**U-B5 (about 360 LOC; Round 2: about 390).** Result documents for the rest:
- `hold --list`: `{"holds": [...], "pagination": {"next": null}}`
- `hold` / release: `{"hold": {...}, "outcome": "created"|"released"|"unchanged"}`
- `offboard`: `{"locations","removed","errors","outcome": "planned"|"removed"}`
- `purge` / `compact-history`: `{"purge"|"compaction": {...}, "outcome": "planned"|"purged"|"compacted"}`. **Round 2
  fix (R248, R253):** `purge --queue` is bulk, so it uses
  `{"results": [{"id","outcome","error"?}], "summary": {"succeeded","failed","skipped","unknown"}}`, writes the partial
  result to stdout and writes no error document. `purged` follows the command's verb (R033) and changes with it under
  D36, so decide D36 before U-B5 fires, because this shape becomes a contract.
- **Round 2 addition (R249):** after every mutation, `next` includes the inspect command: `hold --list` after a hold,
  `status` after add-source, and `hold --list` after a release.
- `add-source`: `{"source": {"id","path"}, "outcome": "created"|"unchanged"}`
- `accept-deletions`, `adopt`: the same pattern
- `whoami`: `{"account": {...}}`
- `discover`: `{"scopes": [...], "toml": "..."}`

Test: `tests/test_cli_json.py::test_<cmd>_json_shape` for each command. Each asserts the top-level noun key,
`err == ""` on success, and the same rc as the human run.

**Decision (see section 4):** R243 means that once `--json` exists, every read and mutation command must support
it. U-B4 and U-B5 therefore land in one release, not half-way.

### B-6 K15 (R379, R380, R411): measure only (unit U-B6; no code change)

- An implementer (not this audit) runs, in their worktree:
  `uv run --locked python -X importtime -c 'import agentsync.cli' 2>&1 | sort -t'|' -k2 -n | tail -5`
  and `for i in 1 2 3; do /usr/bin/time -p uv run --locked python -m agentsync --version; done`.
  `--version` exits from argparse before any handler runs.
- If warm help/version is under 200 ms (R380 is a SHOULD), close K15 with a dated CONTRACTS line.
- Otherwise it needs a decision, because the fix reverses ruling B67/A91 and its two pinned tests:
  - tests/test_cli.py:1915 `test_trust_store_is_injected_before_msal...`
  - :12201 `test_cli_imports_graph_auth_once...`
- Do **not** move the truststore injection, because of C15 req 32.

### Round 2 additions to Wave B

**Anchors.** Findings K21-K64 were verified at the checkout (`ccf696b7b`). Every line below is origin/main `d62ac1487`,
mapped by exact line text (measured: `python3 -I /tmp/cli-bp/acs_r2_anchor.py`, 351 of 357 anchors matched; the other 6
re-located with `grep -n`). The cited lines still read as the verifiers quoted them, and the headline counts were
re-measured at origin (§7). `scripts/tenant-probes.sh` is byte-identical at both commits
(measured with `cmp`). `SETUP_PROMPT_COMPAT` is 34 at origin (install.sh:313); the verdicts read 33 in the checkout, so
any bump below goes to 35.

**Rules for every install.sh unit** (from the verifiers' caller census):
- Keep the tail. tests/test_install_oneshot.py:548-551 pin the link line and then NEXT as the last stdout lines.
  `last_line(` appears on 358 lines and `cp.stdout` on 454 (measured with `grep -cE`, origin). New stdout lines go
  nowhere near the tail, and new diagnostics go to stderr.
- A new line must not start with `NEXT:`, `WAITING ON YOU:` or `[warn]`, and must not contain `next:`, `run:` or
  `(fix:`, because the prompt and setup_report's counters read those tokens.
- A new option arm edits `INSTALL_OPTIONS` (tests/test_contracts.py:107) in the same commit, since :255 asserts equality.
  A bare word arm such as `help)` is not captured by the arm regex (:240), and the regex needs `-...)` at the start of a
  line with at most one tab.
- Do not change README prompt text unless the row says so: any change bumps `SETUP_PROMPT_COMPAT`, which stops every
  saved copy at step 1.
- New test files import helpers from test_install_oneshot (`env`, `wheel`, `folder`, `install_sh`, `last_line`,
  `one_next`, `report_path`, `BASH32`, `INSTALL_SH`), as tests/test_install_next_line.py:18 already does.

#### B-7 `scripts/install.sh`: parser and validation (unit U-B7; after U-A2)

**B-7.1 K25 (R081, R064, R015, R302): `help` and a stray positional.**
- Sites: install.sh:9218-9220 (`*)` SOURCE arm), :9227 (`PARSED=1`), :9232 (`start_capture`), :9292, :9304-9313, :1640,
  :7765.
- Change:
  1. Add a separate bare arm before `-*` (:9217): `help) show_help; exit 0 ;;`. Do not merge it into `-h | --help`
     (:9213), which the freeze pins. A checkout folder named help stays reachable as `./help`. The `help <path>` half of
     R081 does not apply, since install.sh has no subcommands. Add `[help]` to the usage line.
  2. Move only the explicit SOURCE check before `PARSED=1` and `start_capture`, and run it only when `$SOURCE` is
     non-empty, `LIST_FOLDERS=0` and `REPORT_ONLY=0`. So `--list-folders x` keeps its current post-parse error, and
     `--report-only x` keeps today's behaviour until K27. The default `SOURCE=$repo` branch stays where it is.
  3. Keep every other post-parse usage error as it is (`--source-local`, `--launcher`, the `AGENTSYNC_*` numeric values).
     Each keeps its report, NEXT, log end line and tighten.
  4. In the `--source-local` loop (:9304-9313), do not stop at the first bad folder. Print one existing-wording line per
     bad folder, at most 5 and then a count, and exit through `usage_error` once.
  5. Keep SOURCE hidden. No `__AGENTSYNC_SOURCE`: `str(wheel)` is on 253 test lines (measured with `grep -c`, not
     filtered to positional use), tests/test_contracts.py pins `*`, and CONTRACTS.md:5668 names it. R023 does not apply,
     because the guide allows hidden seams (guidelines.md:405), but they follow R064 and R015.
  6. In the same commit amend install.sh:222 and :288, docs/deploy/README.md:243, docs/deploy/setup-feedback.md:15 and
     CONTRACTS.md:9769 to say a bad SOURCE is a parse error and is not reported.
- Test (`tests/test_install_parser.py`, new):
  - `test_help_word_shows_help_and_writes_nothing`: rc 0, stdout equals `--help`'s, and the sorted HOME listing is
    unchanged.
  - `test_a_stray_source_writes_nothing`: `install_sh(env, str(tmp_path / "nowhere"))` gives rc 2, `usage error:` in
    stderr, no `NEXT:` and an unchanged HOME listing.
  - `test_every_bad_source_local_is_named`: two bad folders, both in stderr, the report still written, and `one_next(cp)`.
- Callers: tests/test_install_oneshot.py:2402 (substring on the existing wording) and :16814 (usage-error mode case) stay.
  tests/test_launcher.py:625-626 assert only exit 2, and :631 no step ran. No test, doc or prompt passes `help`
  (verifier's grep). Decision: D18.

**B-7.2 K26 (R066, R069, R060; R063 is D19): `--name=value`, `--`, one pass.**
- Sites: install.sh:9190-9225, :9198, :9203, :9217, :1523-1545, :1532.
- Change:
  1. Keep the `while`/`case` parser, whose arms are the repo's pinned contract (CONTRACTS.md:5668-5670). Add
     `--launcher=*`, `--source-local=*` and `--)`. `--)` takes the next word as SOURCE and refuses any further word with
     the existing "only one SOURCE is accepted". Add the three patterns to `INSTALL_OPTIONS["main"]` and a dated
     CONTRACTS note beside K17.
  2. For a space-separated value of `--source-local` and `--launcher`, refuse exactly the known option names with exit
     2: `--source-local needs a folder, got the option --help`. Keep the list beside the parser. Do not refuse every
     dash word, because `-foo` stays a legal folder. The escapes are `--source-local=--help` and `./--help`. One narrow
     change: a real folder named `--help` in the cwd now needs an escape (0 hits for such a caller).
  3. Fold the replay scan (:1523-1545) into the parser's one pass, or run it afterwards from normalized arrays. Add no
     third list.
     - `ORIG_ARGS`, `ORIG_SHOWN` and `BASE_ARGS` always emit the canonical space form. Otherwise an `=` folder escapes
       the scrub registry (:2682-2713 tokenizes on the word `--source-local`), the awk at :3178 and
       setup_report.py:16455 `_without_folders`. That would be a privacy regression.
     - Never record `--`, so the guard at :9239 and the `args=--list-folders$` anchors hold.
     - Write a dash-leading relative SOURCE as `./-x`, so the appended `--source-local` at :11200 and :11224 still parses.
     - `--launcher=VALUE` stays in `BASE_ARGS` like the space form, and `--source-local=VALUE` stays out.
     - `ALL_ARGS` may stay as given, since `_SOURCE_LOCAL_RE` (setup_report.py:17879) already accepts `[ =]`.
  4. State repeat behaviour in the header (lines 9-15): `--launcher` repeated, last wins; SOURCE repeated, an error. The
     empty `--launcher ""` half moves to K27 (U-C9), because it changes an exit code.
  5. Mention `--x=v` and `--` in the header, never naming the SOURCE seam (tests/test_launcher.py:616).
  6. R060: help prints the word "flag" at install.sh:157 and :196. Replace it with "option". No test pins those strings
     (verifier's `git grep`).
- Test (BASH32): `test_equals_forms_log_the_space_form` (install.log `args=` holds `--source-local <f>`);
  `test_an_equals_folder_is_registered_for_scrubbing`; `test_list_folders_with_a_terminator_stays_the_list_path`;
  `test_rerun_with_source_after_terminator_parses`; `test_an_option_as_a_value_is_a_usage_error`, parametrized over
  `--launcher --help` and `--source-local --help`, rc 2, with the new message.
- Callers: tests/test_launcher.py:620, :624, :627 and tests/test_install_oneshot.py:8788-8789 stay green. No caller uses
  the new spellings (verifier's `git grep -E -- '--(source-local|launcher)='`, 0 hits).

**B-7.3 K28 (R050, R308): hidden old options warn once.**
- Sites: install.sh:9195, :796, :1570.
- Change:
  - `--no-report` has no replacement, because nothing can suppress the report now, so its line says so. Initialise
    `NO_REPORT_SEEN=0` beside `REPORT_ONLY=0`, keep the arm head at the start of its line, and set the flag there. After
    the loop's `done`, before `PARSED=1`: `[ "$NO_REPORT_SEEN" -eq 0 ] || warn "--no-report is deprecated and ignored:
    this run still writes the setup report; drop the option"`. It prints once even when doubled, stays out of install.out
    and the report, and prints nothing on `--help` or `--version`.
  - In friction_cmd's `--log-end` arm (:796), after the `$# -ne 0` check and before `friction_close_attempt`:
    `printf 'warning: --log-end is deprecated: --report-only closes the attempt; drop --log-end\n' >&2`. Use printf,
    because the dispatch at :1492 runs before `warn` is defined at :1627.
  - The `warning:` prefix keeps the repo's form (R307's `!` gutter is part of D13).
- Test: `test_no_report_warns_once_and_changes_nothing` and `test_log_end_warns_on_stderr_only` (keeps `end.stdout == ""`).
- Callers: tests/test_install_oneshot.py:20257 (rc 0, empty stdout), :20273 (one NEXT, not naming `--no-report`), :20137
  (`--log-end` with another option exits 2, so the warning comes after that check) and tests/test_launcher.py:610-619
  (help must not name them). `INSTALL_OPTIONS` and `INSTALL_HIDDEN` (tests/test_contracts.py:130) do not change. Amend the
  K17 paragraph at CONTRACTS.md:5675. `&&` chains in saved prompts still proceed.

**B-7.4 K46 (R013, R470): verify the checkout is agentsync.**
- Sites: install.sh:9285-9292 (the message claims a check it does not make), :9286 (the `*.whl` arm), :9478.
- Change: keep every message and the check order, and add after the existing checks:
  ```sh
  source_project_name() { # PYPROJECT: the [project] table's name, as written
  	LC_ALL=C awk '
  		/^[[:space:]]*\[/ { in_project = ($0 ~ /^[[:space:]]*\[project\][[:space:]]*(#.*)?$/); next }
  		in_project && /^[[:space:]]*name[[:space:]]*=/ {
  			sub(/^[^=]*=[[:space:]]*/, "")
  			if (match($0, /^"[^"]*"/) || match($0, /^\047[^\047]*\047/)) { print substr($0, 2, RLENGTH - 2); exit }
  		}' "$1" 2>/dev/null || true
  }
  ```
  - Checkout arm: `[ "$(source_project_name "$SOURCE/pyproject.toml")" = "agentsync" ] || usage_error "not an agentsync
    checkout (the name in pyproject.toml is not agentsync): $SOURCE"`.
  - `*.whl` arm, after `wheel not found`: `case "$(basename "$SOURCE")" in agentsync-*) ;; *) usage_error "not an
    agentsync wheel: $SOURCE" ;; esac`.
  - No python3 or tomllib: this runs before uv is set up. The exit stays 2 through `usage_error`.
- Why it matters beyond the message (read, uv's own behaviour inferred): with agentsync already installed, line :9585
  writes the foreign project's commit into agentsync's stamp, and `--confirm-install-agent` runs the foreign project's
  `launcher/build.sh`.
- Test: `test_a_foreign_project_is_refused` (pyproject `name = "other"`, rc 2, "not an agentsync checkout", no
  `uv tool install` call), `test_a_foreign_wheel_is_refused`, `test_a_name_under_another_table_does_not_count`, and
  `test_a_single_quoted_name_is_accepted`.
- Callers: all 12 `pyproject` fixtures name agentsync, 2 of them in single quotes (verifier's grep, checkout :7152,
  :7206). Wheel fixtures are `agentsync-0.1.0-py3-none-any.whl`. tests/test_launcher.py:626 still gets exit 2. Add a line
  at CONTRACTS.md:5668.

U-B7 size: about 375 LOC (estimated: K25 95, K26 180, K28 40, K46 60).

#### B-8 `scripts/install.sh`: help text, nouns and declarations (unit U-B8; after U-B7)

**B-8.1 K44, Part A (R079): take test seams out of help; names stay.**
- Sites: install.sh:306-312 (the "Test-only seams" paragraph), :243-247 (the internal-variable narrative), :1583-1591.
- Evidence: show_help prints every comment line from line 2 to the first non-comment line (:1646), so all of these are
  `--help` output. `__AGENTSYNC_` appears nowhere (verifier's grep).
- Change:
  - Move the seam paragraph to a plain comment above the assignments at about :1583. This matches the repo's precedent
    for SOURCE (:9218).
  - Reword :243-247 so help no longer names `AGENTSYNC_NO_NEXT_HINT`, `AGENTSYNC_QUIET_LOCK_WAIT`,
    `AGENTSYNC_LOCK_WAIT_ANNOUNCED`, `AGENTSYNC_AGENT_STEP_PENDING` or `AGENTSYNC_REPORT_RUN`, and move the names beside
    the code.
  - Keep the operator variables in help: `AGENTSYNC_WAIT_SECONDS`, `AGENTSYNC_REINSTALL_WAIT_SECONDS`,
    `AGENTSYNC_REBUILD_LAUNCHER`, `AGENTSYNC_SETUP_LOG`, `AGENTSYNC_FRICTION_LOG`, `AGENTSYNC_INSTALL_DRY_RUN`.
  - The rename itself is Part B, under D30.
- Test (`tests/test_install_help.py`, new): `test_help_names_no_test_seam`, asserting the 11 internal names (the five
  above, plus `AGENTSYNC_SIMULATE_LAUNCHD`, `AGENTSYNC_LAUNCHCTL`, `AGENTSYNC_WAIT_POLL_SECONDS`,
  `AGENTSYNC_PROGRESS_SECONDS`, `AGENTSYNC_LIST_TIMEOUT`, `AGENTSYNC_LIST_TOTAL_SECONDS`) are absent, and the operator
  variables present.
- Callers: nothing parses the seam text (verifier: 0 hits). tests/test_deploy_pack.py:1058, :1478 and :4161 read flags and
  the prompt section only. tests/test_install_oneshot.py:16411 searches the script, not help.

**B-8.2 K47 (R009, R046, R423): a glossary, not renames.**
- Sites: install.sh:9, :11, :243; runtime `the agent step` (:6568 area) against `not a step for the agent`.
- Evidence and correction: "folder and source are one thing" is refuted. `SourceKind` has 5 kinds and the graph kinds are
  not folders (src/agentsync/model.py:21-28). The real collision is the bare word "agent", which means both the
  LaunchAgent install step and the coding agent.
- Change: header comment only.
  - Line 11: replace "(a setup agent never passes it)" with "(a coding agent running the setup prompt never passes it)".
  - Add a contiguous `# Terms:` block after the option list, before "# The config is $AGENTSYNC_CONFIG", outside the
    "# Setup prompt:" block: folder, source, LaunchAgent, background sync, coding agent, agent step.
  - **Critic R423, also for `agentsync`:** add `docs/design/glossary.md`, the one noun glossary. Each noun has meaning,
    identity (accepted selector forms) and banned synonyms. Also cover docs repo, manifest, hold, selector and setup
    report. No audited surface has one today (measured: `git grep -il glossary` finds only the refresh-queue verdict and
    speech glossaries).
  - Keep every pinned runtime word: `not a step for the agent` (setup_report.py, test_cli, test_deploy_pack), `the agent
    step`, `step=agent`, `background sync: ...`. R046 would ban the bare words, but setup_report and tests read them, so
    caller compatibility wins and the glossary documents them (recorded in U-C13's table).
- Test: `test_help_has_the_terms_block`, and `test_glossary_lists_meaning_identity_and_banned_synonyms`.
- Callers: tests/test_launcher.py:606-619 forbids `SOURCE ` in capitals, so write lowercase "source". tests/test_deploy_pack.py
  :1058 needs contiguous `#` lines and :1478 splits on `# Setup prompt:` then `#\n`.

**B-8.3 K38, Option B (R137): declare what `--list-folders` writes.**
- Sites: install.sh:29-30, :9237 (comment), :9232, :9250, :7765.
- Evidence and correction: `--report-only` is not a read mode, and lines 17-19 already declare its writes, so it drops out
  of the finding. tests/test_install_oneshot.py:4380 tests the shell report, not `agentsync setup-report`, and the
  load-bearing use of the list run is redaction (setup_report.py:5489, install.sh:2385). Writing nothing would turn the
  report private-only on a stalled listing (tests/test_setup_report.py:23951).
- Change: reword line 29-30 to: "installs nothing and writes no report; it does create the setup folder if missing, append
  its start, list-folders and end lines to the setup log, copy its output to install.out, and leave the setup folder's
  contents owner-only, as every run does (see "Setup log"); under AGENTSYNC_INSTALL_DRY_RUN=1 it writes none of these".
  Correct the comment at :9237.
- Test: `test_dry_run_list_folders_writes_nothing`, because no test pins that path today. Assert no install.log, no
  install.out and an unchanged HOME. Decision: D26 (Option A is rejected as worded).

**B-8.4 K45, Option B (R003): warn when an optional helper did not build.**
- Sites: install.sh:9688-9703, :150, :288.
- Evidence and correction: 8559 in the finding is the no-launcher warn, not a helper site. R318 fits weakly (it is a bulk
  rule). The miss is not silent: each helper prints why, install.log has the note, and the report's row says it. The gap is
  no stderr warning.
- Change: after the helpers `step_end` (:9703), when `$nb` is non-empty: `warn "optional helpers not ready: $nb; the run
  goes on, each line above says why, and re-running install.sh tries the build again"`. The tokens are `ocr+speech`, which
  avoids the strings "OCR helper", "media helper" and "speech:" that the `said()` asserts forbid on stderr. Update the
  header at :150. Add no `--json` clause, since install.sh has none yet. No SETUP_PROMPT_COMPAT bump.
- Test: `test_a_failed_helper_build_warns_once_and_keeps_the_exit`, with the same stubs as
  tests/test_install_oneshot.py:20502.
- Callers: tests/test_install_oneshot.py:20502, :20582, :20655 (exit equals a run without the helper), setup_report.py:6626
  (`_helpers_note`), CONTRACTS.md:24537. Decision: D31.

**B-8.5 K42, the safe half (R275, R217, R226).**
- Sites and change:
  - :9347 becomes `fail "could not download $UV_INSTALLER_URL (its error is above); install uv by hand"`.
  - :9349 becomes `fail "the uv installer failed (its error is above)"`.
  - :9578 keeps the substring `uv tool install failed` and the HTTPS_PROXY / UV_SYSTEM_CERTS=1 advice as a conditional step,
    and drops the guessing question. The finding missed this third guess.
  - :757 prints `$# argument` or `$# arguments`. Update tests/test_install_oneshot.py:20098 in the same commit.
  - :5700 replaces `just` with "a host that is only a common word". Update CONTRACTS.md:16334 in the same commit.
- Dropped from the finding, with evidence: :10407 `just above` is a location, not filler, pinned at
  tests/test_install_oneshot.py:11920 and CONTRACTS.md:23916. R304 at :9217 already names the token and points to help.
- The count "43 `could not`" does not reproduce: 45 at origin (measured with `grep -c`), and several are NEXT text the README
  prompt branches on (README.md, "whose folders could not be read"). So no blanket rewording here. The prefix and formula
  half is D13.
- Test: `test_uv_failure_guesses_no_cause` (`"proxy or TLS inspection?"` not in stderr), plus the re-pinned test.
- Callers: tests/test_install_oneshot.py:16923 (substring) and :23814 stay.

U-B8 size: about 210 LOC (estimated: K44 40, K47 65, K38 40, K45 35, K42 30).

#### B-9 `scripts/install.sh`: bounded network and children, atomic launcher (unit U-B9; after U-B8)

**B-9.1 K33, Part A (R313, R007): bound the download; never leak the temp dir.** R371 is dropped from this finding: it
governs a CLI's upgrade command, not a first-install bootstrap.
- Sites: install.sh:9342, :9345-9350, :7613, :1813.
- Evidence: curl has no `--connect-timeout` or `--max-time`, the installer has no `with_timeout`, `fail` exits before :9350,
  and on_exit never removes `tmp`. :9342's dry-run line omits `--retry 3`.
- Change:
  1. One array for both lines: `UV_CURL=(/usr/bin/curl -LsSf --connect-timeout 20 --max-time 120 --retry 3
     --retry-max-time 300)`. The values are proposed, not measured. `--max-time` is per attempt and curl retries timeouts,
     so `--retry-max-time` bounds the total. A test seam `__AGENTSYNC_CURL` (R079; never in help) replaces the first word,
     because `/usr/bin/curl` is absolute and no test can stub it today.
  2. Add global `UV_TMP=""` near :1609, use it instead of `tmp` at :9345, and remove it in on_exit beside :7613. Keep the
     success-path removal.
  3. Run the installer under `with_timeout` with a generous limit, because its own downloads are not covered by the curl
     flags. Its 143 on a kill reads as a signal, so log a non-signal rc and say "timed out". The watchdog kills only the sh
     pid, so a child downloader may outlive it; say so in a comment.
- Test (`tests/test_install_bounds.py`, new): `test_uv_download_failure_removes_its_temp_dir`, and
  `test_dry_run_prints_the_real_curl_flags`.
- Callers: tests/test_launcher.py:658-659 pin the URL and `UV_NO_MODIFY_PATH=1`/`UV_INSTALL_DIR=` in dry-run stdout, and stay.
  `fail` always exits 1 (:1628). setup_report.py branches on the run's rc, not the step's. grep finds no parser of the two
  fail messages. The pin itself is Part B (U-C9, D22).

**B-9.2 K34 (R313, R019): stated limits, applied narrowly.** R019's `while true` check does not fit these sites, so R313
carries this.
- Sites: install.sh:1837 (`with_progress`, no deadline), :10718 (`status_run`), :10805 (`first_sync_run`), :10874, :11134
  (`loop_status_run`), :11157-11158, :9361 (`uv_run`), :10963 (`install-agent`).
- Evidence and correction: status has per-check limits (doctor's `_run`, default 30 s), and install-agent is bounded per call
  (launchctl `timeout=120`, 5 attempts). The header's lines 211-215 keep a coding tool from stopping the run, and the caller-held
  limit is a separate design. The real gap: README.md:526 allows background mode with no limit, where a hung child waits
  forever.
- Change: do not apply `with_timeout` uniformly.
  1. First sync gets the limit, with `exec` so the signal reaches the tool, because `with_timeout` kills only the pid it
     started. **Round 2 fix to the verdict's snippet:** origin's function carries an fd shuffle that must stay:
     `{ AGENTSYNC_QUIET_LOCK_WAIT=1 AGENTSYNC_LOCK_WAIT_ANNOUNCED="$SYNC_LOCK_BUSY" exec "$AGENTSYNC" "${FIRST_SYNC[@]}"
     --config "$CONFIG" >"$SYNC_OUT" 2>&3 3>&-; } 3>&2 2>/dev/null`.
     Call it as `with_progress "$SYNC_LABEL" with_timeout "$FIRST_SYNC_LIMIT" first_sync_run`. Leave `with_progress`
     unchanged (tests/test_install_oneshot.py extracts it as a function). The default must be well above 600 s, the
     documented lock wait (README.md:500-506, `INTERACTIVE_LOCK_WAIT_S`), plus conversion time: 3600 s, estimated, not
     measured, stated in the header.
  2. Closing status and step 5 status: put `with_timeout` around the binary, not the function, or a live agentsync is
     orphaned and keeps writing the log (see :11155): `status_run() { with_timeout "$STATUS_LIMIT" "$AGENTSYNC" status
     --config "$CONFIG" | tee "$DOCTOR_LOG"; }`, and the same shape on the `env` command in `loop_status_run`, inside its
     existing `{ ...; } 2>/dev/null`.
  3. Keep rc 143 on the wire and never introduce 124, so `srun -ge 128` (:11158) and `_closing_stopped` (setup_report.py:6685)
     keep working. Tell the script's own limit from an outside signal by elapsed time. Only then print `<step>: stopped at its
     limit of Ns; <agentsync> status shows where it got to`, and log the step note `timeout`. The run exits 1 through the
     failed-step path (0 for the closing status, as tests/test_install_oneshot.py:1362 requires).
  4. install-agent: optional `with_timeout 300` on the direct command at :10963, low priority.
  5. uv_run: do not wrap it. A kill hits only the subshell or lockf and, because `--force` deletes the environment first
     (:9405 area), can leave no tool. That is D23.
  6. The test seam is `__AGENTSYNC_FIRST_SYNC_LIMIT`. **Round 2 fix (R079):** the verdict proposed `AGENTSYNC_FIRST_SYNC_LIMIT`,
     the same public-prefix defect K44 reports. The user-facing `--timeout <DURATION>` is in C-10, under R451 and R436;
     the verdict's `--timeout SECONDS` is corrected there and reconciled with K31.
  7. Say in the header and README.md:526 that the tool's own command timeout may still end the run first. Keep the existing
     10 and 30 minute advice.
- Test: `test_a_hung_first_sync_stops_at_its_limit` (a stub that sleeps, limit 2 s: rc 1, the limit line on stderr, note
  `timeout` in install.log, and the stub's pid gone), and `test_a_hung_status_stops_at_its_limit`.
- Callers: tests/test_install_oneshot.py:1362 (closing_status 143/137), :16156 (the `with_timeout 2 /bin/sleep 30` pin),
  :16296 (the `still running` line pin), tests/test_deploy_pack.py:2967-2976, and setup_report.py's
  `_FIRST_SYNC_NOTE_WORDS` (an unknown note shows as it is).

**B-9.3 K39 (R351, R275, R004): atomic launcher swap and a default NEXT.**
- Sites: install.sh:9605-9612, :9625, :9641-9642, :9585, :7770, :1634, :292.
- Evidence and correction: `run rm -rf` then `run ditto` have no `|| fail`, and `set -e` then exits with `NEXT_MSG` empty, so
  :7770 prints nothing, against the header at :292. Signals are not the path (`on_signal` sets a NEXT). The finding's literal
  fix is wrong: `mv` onto an existing directory nests the bundle.
- Change:
  1. In `install_app`, keep `say "launcher: replacing ..."` verbatim (tests/test_launcher.py:736). In a dry run, print the
     plan through `run` and create nothing: return before any `mktemp` (tests/test_install_oneshot.py:8201).
  2. Real run: `run mkdir -p "$HOME/Applications" || fail ...`; `APP_STAGE="$(mktemp -d "$HOME/Applications/.AgentSyncLauncher.XXXXXX")"
     || fail ...`; `/usr/bin/ditto "$1" "$APP_STAGE/new.app" || fail "could not copy the launcher into $HOME/Applications;
     $APP_DEST was not changed"`; move the old bundle to `$APP_STAGE/old.app` if present; `mv "$APP_STAGE/new.app"
     "$APP_DEST"`, and on failure move the old one back and `fail`; finally `rm -rf "$APP_STAGE"; APP_STAGE=""`. The temp name
     is hidden and does not end in .app, and it sits beside `APP_DEST`, so the renames stay on one filesystem. Precedent:
     launcher/build.sh's own move-aside swap.
  3. In on_exit, beside :7613: when `APP_STAGE` is set, restore (`[ -e "$APP_DEST" ] || [ ! -e "$APP_STAGE/old.app" ] || mv
     ...`) and then `rm -rf "${APP_STAGE:?}"`. Also remove the build dir that :9642 leaks when `install_app` fails.
  4. Default NEXT, in on_exit after `[ "$PARSED" -eq 1 ] || exit "$rc"` and before `step_end failed "$rc"`, because `step_end`
     clears `STEP`:
     ```sh
     if [ "$rc" -ne 0 ] && [ -z "$NEXT_MSG" ]; then
       [ -n "$LAST_ERROR" ] || { LAST_ERROR="install.sh stopped in step ${STEP:-unknown}: a command exited $rc (its message, if any, is above)"; printf 'error: %s\n' "$LAST_ERROR" >&2; }
       NEXT_MSG="fix the error above, then re-run: ${RERUN:-$SELF$RERUN_ARGS}"
     fi
     ```
     Setting it before `write_report` fills the report's Recent errors. The rc is unchanged, and pre-parse paths are untouched.
  5. Stamp at :9585, low priority: `mktemp` beside it, `mv -f`, best-effort and never fatal. A truncated stamp self-heals, so
     this is a literal R351 fix only.
- Test: `test_a_failed_copy_keeps_the_old_launcher` (ditto stub exits 1: old bundle byte-identical, exit 1, `one_next(cp)`, no
  `.AgentSyncLauncher.*` left), `test_an_interrupted_swap_restores_the_old_bundle`, and `test_any_set_e_exit_ends_on_one_next`
  (a stub makes the unguarded `want=` at about :9631 fail).
- Callers: tests/test_launcher.py:718, :736-737 (mtime kept vs rebuilt: ditto then mv preserves both), :780-790,
  tests/test_install_oneshot.py:422 (`one_next`), setup_report.py:7037, README.md:526. Tests asserting no NEXT all run before
  the trap (:9228) and stay. Reach: `install_app` runs only under `--confirm-install-agent`, but the default NEXT covers
  every unguarded exit.

U-B9 size: about 430 LOC (estimated: K33A 90, K34 195, K39 145).

#### B-10 `scripts/install.sh`: signals and swallowed failures (unit U-B10; after U-B9)

**B-10.1 K35 (R335, R364, R416, R336, R294): stop what this run started.** Site 128 (SIGKILL) is not a defect, because no trap
sees it.
- Sites: install.sh:1837-1868, :7603-7606, :7614-7616, :7754, :7791-7797, :9229-9231, :1979, :10718, :10805, :11134.
- Evidence and correction: `with_progress` backgrounds the child and only waits on it, and on_exit kills only the helpers. "Safe
  to re-run" is not false, since the lock serializes a re-run; the defect is that NEXT does not say what may still run. TERM
  to the whole group already stops the child. Asynchronous commands ignore SIGINT with job control off (`man bash`;
  convert/ocr.py records the same effect).
- Change: keep exit codes 129/130/143, the trap lines (tests/test_setup_report.py:13971 pins `^trap 'on_signal (\d+)' [A-Z]+$`), the
  `this run was stopped ` prefix (:7754) and the "safe to re-run" clause.
  1. In `with_progress`, set `WAIT_CHILD=$pid` after the `&` and clear it after the wait. Read it as `${WAIT_CHILD:-}`, because a
     test extracts the function alone under `set -u`.
  2. Make the signal reach the real tool. **Round 2 fix:** after U-B9 the first sync's chain is with_progress, then a
     with_timeout subshell, then agentsync, so `$WAIT_CHILD` is a wrapper. Send TERM, not INT, to its children first:
     `pkill -TERM -P "$WAIT_CHILD"; kill -TERM "$WAIT_CHILD"`. `status_run` is a pipeline and needs the same.
  3. In on_exit beside :7614-7616, send TERM and poll for at most about 3 s, with no unbounded wait. If the child is still alive,
     append to NEXT `; <label> (pid N) may still be running, and the re-run waits for its lock`. Add this only in that case, so
     the exact match at tests/test_install_oneshot.py:16444 holds when the stub dies on TERM.
  4. Leave foreground helper builds and the reinstall as they are (CONTRACTS.md:6890, :11220; a pid-only TERM lets a
     foreground build finish, pinned by the test at checkout :17425).
  5. Copy, rc 130 only: replace the `error:` stderr line with `Stopped waiting.` plus the confirmed facts. Still set `LAST_ERROR`
     for the report (:5284, :5519). Keep the TERM and HUP text, which tests pin at :16509 and :16550. Do not use `Stopped
     waiting.` as NEXT text. Update CONTRACTS.md:27010.
- Test (`tests/test_install_signals.py`, new): `test_term_stops_the_first_sync_child` (a stub records its pid; after TERM to the
  script's pid alone, the stub is gone within 5 s and rc 143), `test_a_child_that_ignores_term_is_named_in_next`, and
  `test_int_says_stopped_waiting` (no test sends SIGINT today).
- Callers: setup_report.py STOPPED_EXITS, docs/deploy/setup-feedback.md (`rc` table), README.md:500-506 and the pinned "safe to
  re-run" clause (tests/test_deploy_pack.py:2976, test_setup_report). Decision: D24 (second signal).

**B-10.2 K40 (R017, R275): name the cause.** Site 1596 in the finding is wrong (it is `RUN_STAMP`, origin :1905), R353 fits poorly,
and the blanket "review 88 sites" item is dropped: 21 of the 88 are housekeeping (verifier's grep).
- Sites: install.sh:10980 (`kickstart`), :11037-11046 (the wait), :11114 (the message), :339-343 (`tighten_setup_tree`).
- Change:
  1. Replace `kickstart` with a function that sets `KICK_RC=0`, then `KICK_ERR="$("$LAUNCHCTL" kickstart "gui/$(id -u)/$POLL_LABEL"
     2>&1 >/dev/null)" || KICK_RC=$?`, in the main shell with one launchctl call per kickstart. Print nothing on success or during
     the wait, because a non-zero rc may be normal against a running job.
  2. In the `*)` arm at :11114, when `KICK_RC` is non-zero and the state is not running, append after the closing paren: `;
     launchctl kickstart exited $KICK_RC: <first line of KICK_ERR>`. State it as a fact (R275). This keeps the prefix pinned at
     tests/test_install_oneshot.py:598. Leave NEXT, `WAIT_RESULT`, `WAIT_CODE`, the step note and the exit code unchanged.
     Take the first line with `head -n 1`, since a quoted `${x%%$'\n'*}` misbehaves on bash 3.2.
  3. The closer R017 case: `p="$(launchd_print || true)"` plus `${WAIT_STATE:-not loaded}` reports a failed `launchctl print` as "not
     loaded". Keep the print rc and say `state: unread (launchctl print exited N)` unless it is the not-loaded code. Which rc means
     not loaded rests on the tests' stubbed 113 (tests/test_cli.py), unverified against real launchctl.
  4. `tighten_setup_tree`: Needs decision D27, because CONTRACTS.md:9772 says "Quiet... every error is ignored". Under Option B, keep
     "cannot fail a run", re-run the same `find` for entries still matching `-perm +077` and call `warn` once, with no `next:`,
     `run:` or `(fix:`. Use the re-check, not find's exit code, so a file removed during the walk raises no false warning.
- Test: `test_a_refused_kickstart_is_named_when_the_wait_ends` (stub kickstart exits 37), and
  `test_an_unreadable_launchd_print_is_not_reported_as_not_loaded`.
- Callers: tests/test_install_oneshot.py:598-599 and test_launcher.py:722 (one call per kickstart), the first-run stub call counts,
  :549 (nothing new on stdout: on_exit's tighten runs just before NEXT), :8884 and similar `(rc, stderr) == (0, "")` asserts, which
  hold because a warning fires only on failure.

U-B10 size: about 280 LOC (estimated: K35 175, K40 105).

#### B-11 `scripts/install.sh`, `src/agentsync/setup_report.py`: the folder list (unit U-B11; after U-B10)

**B-11.1 K37 (R347, R343, R344, R444, R446, R359, R191; R192 does not apply, R189 is a recorded conflict).**
- Sites: install.sh:8663 (`max=200`), :8699, :8868, :27-29, :9239-9240, :2385-2391, setup_report.py:4126-4135, :5489, `synced_folders`
  (:7943, whose inner Python splits on `b"\n"`).
- Evidence: the list stops at 200 then `(N more)`, with no way to see the rest, sorted by path, so later providers can be systematically
  hidden. Names print raw. A name holding a newline plus a full path can forge a list line an agent is told to act on (README.md:448).
- Change: all additive; a default run on ordinary names stays byte-identical.
  1. Add `-a | --all` and `-n | --limit` (R444, R446; the verdict's hint `N` is `<NUMBER>`, R436), plus `--limit=*`. A value below 1 or
     non-numeric is a usage error (R344). Default 200. `--all` stays bounded by `LIST_TOTAL`/`LIST_TIMEOUT` and `-maxdepth 2`. Relax only
     the guard at :9239 so these pass; `--list-folders --source-local x` stays exit 2 (tests/test_install_oneshot.py:8788). Keep them out
     of `ORIG_ARGS`, so `args=--list-folders$` holds (:8659). Add to `INSTALL_OPTIONS` and the header in the same commit. No prompt change,
     so no COMPAT bump.
  2. Keep the `(N more)` line exact and add **no new stdout line**: tests/test_install_oneshot.py:8728 and :9538 pin line counts. Only when
     `total > max`, append to NEXT: `; N folders past the first M are not listed: $SELF --list-folders --all`.
  3. Change `find -print` to `-print0`, sort NUL-delimited, count NUL records so `listed-N` stays right, and split on NUL in `synced_folders`.
     The EPERM stubs exec `/usr/bin/find "$@"`, so they pass `-print0` through.
  4. One byte-level sanitize predicate under `LC_ALL=C`, plain bash 3.2 and awk, since a fresh Mac has no tool Python at step 1. It covers C0,
     DEL, C1 and the UTF-8 bidi and zero-width sequences. Only a matching name prints in `shown_arg`'s `$'...'` form after its mark; every
     other name stays raw, including accented names. Escape, do not strip, because a stripped path is not the path (R191).
  5. **Required, or redaction regresses:** teach `listed_folder_lines` (:2385) and `setup_report.listed_folder_names` (:4135) to decode a
     `$'...'` path after removing the mark, so an odd name still registers its real text. A test holds the two decoders equal.
  6. Do **not** change the text marks to tab columns: two parsers and the README prompt read them. The conflict with R189 is recorded in
     U-C13's table and callers win. A `state` field arrives through `--json` (U-B12).
- Test (`tests/test_install_list.py`, new): a 205-folder run with `--all` and with `--limit 3` (R396), `--limit 0` and `--limit x` usage errors, a
  name with newline, ESC and U+202E including a newline-plus-path spoof (one list line per real folder), and the redaction round trip.
- Callers: setup_report.py:12929 `_LIST_STOPS`, install.sh:7384, README.md:448-475, tests/test_install_oneshot.py:8728, :9538, :8659, :8789,
  tests/test_contracts.py:107. Both `# run=` header regexes already accept extra flags (setup_report.py:4126, install.sh:2390).

U-B11 size: about 310 LOC (estimated: 110 src, 180 tests, 20 docs).

#### B-12 `scripts/install.sh`: `--json` on the list (unit U-B12; after U-B11 and D15)

**B-12.1 K22 (R243, R244, R248, R253, R254, R255, R264, R414).** R414's API-error half does not apply: install.sh has no API client.
- Sites: install.sh:9190, :9217, :8662, :1523, :1532, :1628, :1637, :9107-9176.
- Evidence: `grep -n -- --json scripts/install.sh` finds nothing (measured, origin). "Callers must parse prose" is overstated: the real parser
  reads install.out, and install.log already has stable `note=` tokens.
- Change: human mode stays byte-identical. In `--json` runs, install.out and install.log stay exactly as today.
  1. Add `--json) JSON=1 ;;`, plus the `INSTALL_OPTIONS` pin and help. Detect it in the pre-scan at :1523 so errors raised before the
     parser reaches it are JSON too. Put it in the no-record arm at :1532, so it never enters `ORIG_ARGS`, or :9239 rejects
     `--list-folders --json`. **Round 2 fix: one arm.** K22, K23 and K24 each proposed their own `--json` arm. There is one, and after the
     loop: `--json` is valid with `--list-folders`, `--version` and `--help`, otherwise `usage_error "--json works with --list-folders,
     --version and --help"`.
  2. Use U-A2's routing (fds 8 and 9) and encoder.
  3. List document on exit 0: `{"folders":[{"path","state":"listed|synced|inside_synced|contains_synced"}],"total":N,
     "pagination":{"next":null},"next":[{"command","argv"}]}`. Lift the 200 cap in JSON mode, so `next: null` is true. **Round 2 fix (R249):**
     `next` uses the shared shape, not the verdict's bare string.
  4. Exits 3 and 4 stay. Under R253 and R254, stdout is empty and one stderr line carries
     `{"status":"action_required","reason":...,"message":...,"next":[...],"data":{"folders":[...]}}`, keeping the partial list those exits
     print today.
  5. A folder name with a newline is one element only after U-B11's NUL list.
  6. `--version`, `--log*` and the setup prompt stay in human mode. The prompt must not start using `--json`
     (tests/test_deploy_pack.py:63 and the flag-set test keep it equal).
- Test (`tests/test_install_json.py`, new): `test_list_folders_json_is_one_document` (R244: `json.loads(stdout)`, stderr empty, install.out
  unchanged in shape), `test_a_denied_list_is_one_error_line` (rc 4, stdout empty, `len(stderr.splitlines()) == 1`, `reason == "denied"`),
  `test_json_elsewhere_is_a_usage_error`, and `test_redaction_still_sees_a_json_list_run` (`listed_folder_names` reads the human lines in
  install.out).
- Callers: setup_report.py:4135, :5489, :7037; install.sh:5142, :7384; tests/test_install_oneshot.py:8789; tests/test_launcher.py:621-624 pin exit
  2 for four other options only, so `--json` is free. Decision: D15 (mutation document on an install run).

**B-12.2 K21, Option B (R016, R173, R177, R006; R176 and R180 are narrowed).** Recommended, because it preserves every caller.
- Sites: install.sh:1626, :7770, :9321-9322, :9609, :10953, :8868; tests/test_install_oneshot.py:418, :422, :548, :551, :403, :498, :16443.
- Evidence (measured at origin with `grep -cE`): 203 `say` lines, 24 lines with `>&2`. **No program parses install.sh's stdout** (verifier's grep). The
  readers are the setup prompt, by eye (README.md:425, :448, :526, :534), and test pins. R176 is not violated today, because install.sh prints no
  identifier. R180 is small, because the pinned closing line already carries the outcome.
- Change: default stdout stays byte for byte, with NEXT last and the compat line last on `--version`. Record the R422 exception in U-C13's table, naming
  R016, R173, R177 and R006. Reason: the README setup prompt and the test suite read this transcript as the interface. Impact: `x=$(install.sh)` gets
  a transcript. Test: `last_line` and tests/test_install_oneshot.py:548. The machine channel is B-12.1.
- Option A is U-C14a, only if D14 chooses it.
- Test: `test_default_stdout_keeps_next_last` (a named exception test for the record).

U-B12 size: about 395 LOC (estimated: K22 350, K21 B 45).

#### B-13 `scripts/tenant-probes.sh`: test harness, dry run and parser (unit U-B13; after U-B3)

U-B3 changes one teaching line in this file (B-3.3), so this chain starts after it. New tests go in `tests/test_tenant_probes.py` (new), so
tests/test_deploy_pack.py, which many units hold, is untouched: its :854 and :893 tests stay as they are.

**B-13.1 K64 (R390, R391, R397, R398, R399): the harness and the exit table.**
- Evidence: the docstring at tests/test_deploy_pack.py:893 says no test executes the embedded Python past `--dry-run`.
- Change:
  - No stub needed: exit 2 (unknown argument, `--max-pages abc`, `--config` with no value), exit 1 (`--python` missing path), exit 78 (invalid
    sources.toml; client_id unset without `--dry-run`, report present with mode 0600), exit 0 (`--selftest`, `--help`). Assert returncode, stdout and
    stderr separately, under `tmp_path`, with an isolated `TMPDIR`.
  - Paths past sign-in need a shim. A fake interpreter that only runs `exit 77` proves just the bash pass-through. The script runs `"$PY" -I -X utf8 -
    ...`, and `-I` ignores `PYTHONPATH`. So pass as `--python` a small wrapper that drops `-I -X utf8 -` and runs the real interpreter on a shim file with the
    remaining argv. The shim patches `agentsync.graph.auth.MsalAuth` and `agentsync.graph.client.GraphClient`, then `exec()`s `sys.stdin.read()` with
    `__name__ == "__main__"`. Do not use the real MsalAuth, which reaches the login Keychain (graph/auth.py:505-528).
  - Through the shim: 77 from `AuthRequiredError`; 77 from `AuthBlockedError`, with the report naming the state; 77 mid-run, with the report written; 1 from
    `NetworkPolicyError`; 0 with a canned drive, asserting no file names in the report, deltalinks.json at 0600, the links absent from the report, and
    the temp root gone.
- Rule: a test that is red on current code lands in the same commit as its fix: dry-run with client_id unset (K48, here), SIGTERM (K53, U-B15), atomic store
  (K54, U-B15).
- Callers: none executes past `--dry-run`. No live Graph calls and no real Keychain access (CLAUDE.md: tests stay inside `tmp_path`).

**B-13.2 K48 (R325, R015): `--dry-run` writes nothing.**
- Sites: tenant-probes.sh:7, :237, :264-267, :282.
- Change: change only the block at :264-267: under `DRY`, `print("not run: [graph] client_id is unset; dry run wrote nothing", file=sys.stderr); sys.exit(78)`,
  before the existing `emit` and `write_report`. Exit 78 is kept in both modes, as the header (:30-31) and docs/deploy/tenant-probes.md:26 say, and a real run
  still writes its report (docs/plans/implementation.md:76). Do not skip the check under `DRY`, or `settings_from_config` raises a misleading "configuration
  invalid" (graph/auth.py:320-325). Add "no writes" to docs/deploy/tenant-probes.md:14.
- Test: `test_dry_run_without_client_id_writes_nothing`: rc 78, `"client_id is unset"` in stderr, `"report "` not in stderr, stdout empty, unchanged `rglob`
  snapshot; a second case with `--out <tmp>/x` asserts the directory is not created.
- Callers: tests/test_deploy_pack.py:854 sets a client_id and stays. Nothing parses the `report <path>` suffix.

**B-13.3 K56 (R066, R069; R063 is refuted here): `=` forms and `--`.**
- Sites: tenant-probes.sh:51, :54-90, :87. The script has one command and one parser, so R063 does not apply.
- Change: keep the inline `while [ $# -gt 0 ]; do ... done` at column 0 with one-tab arms, because tests/test_contracts.py:272 reads it. **Do not** share a table with
  install.sh: the two share only `-h`/`--help`, and install.sh's arms are pinned. Add `set_value NAME VALUE` (refuses an empty value, runs `is_uint` for `--max-*`,
  assigns). Extend `need_value` to refuse a known option as the separated value, naming the escape: `$2 needs a value, got the option $3 (write $2=$3 if that is
  the value)`. Arms: `--config=* | --out=* | --python=* | --max-pages=* | --max-downloads=* | --max-bytes=*) set_value "${1%%=*}" "${1#*=}" ;;` and `--) shift; [ $# -eq
  0 ] || usage_error "unexpected argument $1"; break ;;`. State in the help header: `--name VALUE` and `--name=VALUE` are the same, a repeat takes the last value, an
  empty value is a usage error, `--` ends the options.
- Why the option-eating check matters: `--out --dry-run` today sets `OUT` to `$PWD/--dry-run` and leaves `DRY_RUN=0`, so a person asking for a dry run gets a live
  sign-in and tenant reads.
- Test: exit 2 before any work for `--out --dry-run`, `--config=` and `-- x`; a positive `--config=PATH --dry-run`, and a bare `--` with `--dry-run`.
- Callers: only `--dry-run` is ever passed (tests/test_deploy_pack.py:854). Run shellcheck and bash 3.2 (:565, :819).

**B-13.4 K58 (R086, R081): `--version` and `help`.**
- Sites: tenant-probes.sh:83, :87.
- Change: change line 83 to `-h | --help | help)`; the parser takes no positionals, so `help` shadows nothing. Add `--version) SHOW_VERSION=1 ;;` and act on it right after the
  parse loop, before config handling and interpreter lookup, so `--help` wins in either order. Print only the checkout commit, read from `$repo/.git/HEAD` without running
  git (mirror `git_head_file`, install.sh:1676), or `unknown`. Do not call /usr/bin/git, which can offer to install developer tools, and the script promises zero
  prompts. Do not print `__version__`: it has been 0.1.0 since the first commit (src/agentsync/cycle.py) and needs the interpreter. `--json` form: U-B16.
- Test: `test_version_prints_one_value_and_exits_0`, `test_help_word_matches_dash_h`, and `test_help_wins_over_version`.
- Callers: tests/test_contracts.py:331 only widens its known set. tests/test_launcher.py:143's "unknown argument" belongs to the launcher's build script. Decision: D12 for `-v`.

U-B13 size: about 380 LOC (estimated: K64 190, K48 40, K56 95, K58 55).

#### B-14 `scripts/tenant-probes.sh`: names, help, copy and sanitizing (unit U-B14; after U-B13)

**B-14.1 K59 (R452, R436, R067; R062 and R008 are D35).**
- Sites: tenant-probes.sh:4-14, :25, :63-67; docs/deploy/tenant-probes.md:20.
- Change: the arm becomes `-o | --output | --out)`. `--out` keeps working with any value (R385), prints one stderr line naming `--output` (R050, R308), and leaves help. `--output -` is a usage
  error, because the directory also holds deltalinks.json and login files, so stdout cannot replace it. `--output json|yaml|table|text` is a usage error whose message says the script has no JSON
  output until U-B16. Hints: `--config <FILE>`, `--output <DIR>`, `--python <PATH>`, `--max-pages <NUMBER>`, `--max-downloads <NUMBER>`, `--max-bytes <NUMBER>`. In the prose at :25, "up to N"
  becomes "up to --max-downloads". Change docs/deploy/tenant-probes.md:20 in the same commit, or tests/test_contracts.py:331 fails.
- Test: `--output DIR --dry-run` and `--out DIR --dry-run` both exit 0 and only `--out` warns; `--output -` exits 2.
- Correction: `<BYTES>` would be a legal noun under Reference E, so only the missing brackets are a clear defect. `<NUMBER>` is fine.

**B-14.2 K60 (R087, R089, R091, R095, R097, R100).**
- Sites: tenant-probes.sh:2-31, :50. Measured by replaying show_help's awk: 30 lines, 22 over 80 columns.
- Change: rewrite only the header comment; keep `show_help` and leave the parser, output and exit codes alone. Order: `Usage:` plus an imperative one-liner; Options sorted by long name, adding
  `--help` (parsed at :83, unlisted today), `--output` and `--version`, with `(default: VALUE)`; `Examples:` with the two verified commands from docs/deploy/tenant-probes.md:14-15; Notes (the login
  prerequisite, read-only and no-prompt, exit status, one line naming probes 1, 2, 4b and 9 including the +30/60/90 re-run); `Learn more: docs/deploy/tenant-probes.md`, which already has the per-probe
  table. Keep the probe IDs: they are operator vocabulary in dry-run output and report headings (:285-289, :295, :379, :440, :471). Drop only the "design §9" wording. Every line is at most 80 columns.
- Constraints: keep the column-0 loop block (tests/test_contracts.py:272). Never write `config.<word>` in help text, since tests/test_deploy_pack.py:893 scans comments too; `sources.toml` is fine. Keep
  contiguous `#` lines ending before `set -euo pipefail`.
- Test: `test_help_lists_exactly_the_parsed_options` (the arm set via `_install_case_arms`), `test_help_fits_80_columns`, and **R100:** `test_every_probe_help_example_parses`: read the `Examples:` block from
  `--help`, append `--dry-run` where absent, run each against a Contoso config with a client_id, and assert rc 0 and an unchanged `tmp_path` snapshot. This never signs in.

**B-14.3 K62 (R060, R276, R285; R277 and R304 are narrowed).**
- Sites: tenant-probes.sh:47, :87, :121, :252, :266, :278.
- Change: this file only. **Do not** share a helper with install.sh: its `usage_error` and `fail` carry installer state, its `usage error:` prefix is pinned (tests/test_deploy_pack.py, test_install_oneshot.py
  :20137 area), and it is copied alone in tests.
  - :87 splits into `-*) usage_error "Unknown option $1 for tenant-probes.sh." ;;` and `*) usage_error "Unexpected positional argument $1 for tenant-probes.sh." ;;`.
  - :121 gets two branches, both exit 1. `[ -z "$PY" ]`: `Can't find a Python with agentsync installed.` with recovery `scripts/install.sh`, or pass `--python`. `[ ! -x "$PY" ]`: `Can't run $PY: it isn't executable.
    Check --python or AGENTSYNC_PYTHON.` Use `printf '%s\n'` so the apostrophe is safe (tests/test_deploy_pack.py apostrophe lints).
  - :252 and :278 keep exit 78 and `{exc}`, with the repo's term `Configuration invalid:` (cli.py:170, setup_report.py:449). Recorded conflict: this keeps the repo term rather than the bare Reference J formula.
  - :266 becomes `Can't run the probes. [graph] client_id is unset.`, then `Report: {path}`.
  - **Round 2 fix (R205):** the verdict's `✗` is for rich output. Plain output keeps the `Error:` prefix, so print `✗ ` only when stderr is a TTY and `Error: ` otherwise, using one small helper in the file. Add a
    tiny `fail_line()` in the heredoc for Python-side lines.
  - :492 is a report row, not a CLI error; at most change it to `Couldn't get a deltaLink: ...`.
- Test: `test_unknown_option_uses_the_formula`, `test_two_interpreter_failures_have_two_messages`, and `test_error_lines_are_plain_off_a_tty`.
- Side effect, recorded: tenant-probes diverges from install.sh's kept `error:` prefixes until D13 is decided.

**B-14.4 K49 (R275, R004; R003 in part; R339 dropped, since no failure path exits 0).**
- Sites: tenant-probes.sh:309-316, :352, :509-517, :529.
- Change: add stderr lines only. Stdout, every exit code and the report text stay. In the except branches at :509-517, set `failure` next to `rc`:
  - `NetworkPolicyError`: `failed: network-policy ({exc.policy}); see docs/deploy/README.md (TLS inspection / proxy)`.
  - `AuthRequiredError`: ``stopped: sign-in required mid-run; run `agentsync graph login`, then re-run``.
  - Other `AgentSyncError`: `failed: {exc.status} {exc.code}` for a `GraphError`, else `failed: {cell(exc)}`, with no invented command.
  - After :529: `if rc: print(f"{failure}; report {report}", file=sys.stderr)`. Initialise `failure = ""` at :352.
  - :315 gains the login command. :311 keeps its line and adds the reason from `exc`, invents no login command, and follows cli.py's precedent: a blocked state is not cured by logging in again.
  - Do not copy the report's tee command from :314 to stderr: it interpolates an unquoted `out_dir`, and the default state dir has a space. Quote it in the report text as a separate one-line fix here.
- Test: stub `AuthRequiredError` and a network-policy failure through the harness. Assert the stderr line, the unchanged stdout `report:` line and the exit (77 or 1).

**B-14.5 K50, the compatible half (R016, R176).**
- Change: add `file=sys.stderr` to the FAILED prints at :197, :210 and :215; keep exit 1 and `selftest ok` (:217) on stdout. Replace :529 with a branch: on `rc != 0`, print `failed; report {report}` (or `sign-in required; report
  {report}` for 77) to stderr, matching :266, :311 and :315. Merge this with B-14.4's line. The bare-path question on exit 0 is D32, in U-C12.
- Test: `test_selftest_failures_go_to_stderr`, and `test_a_failed_run_prints_nothing_on_stdout`.
- Callers: :290 (dry-run text) stays, pinned at tests/test_deploy_pack.py:854-890. docs/plans/implementation.md:76 documents exit 0's line only.

**B-14.6 K63 (R359, R191).**
- Sites: tenant-probes.sh:245-246, :306, :310, :314, :347, :349, :392, :422, :433, :437, :460, :463, :485, :492, :507, :510, :513, :516.
- Evidence: `cell()` only maps `|` and newline and cuts at 160. Graph codes and messages keep controls and bidi characters. The cut always drops what matters: at :310 the admin-consent URL (a 97-character prefix, measured with `len`), at :347
  the request id, at :510 the GET URL and proxy.
- Change: add `re` and `unicodedata` imports (`# noqa: E402`), a `clean()` that strips CSI/OSC and categories `Cc Cf Cs Co Cn Zl Zp`, shows `\n`, `\r`, `\t` as two characters, and `cell(text, limit=160)` that cleans first and cuts with `…`. Use
  `cell(exc, 80)` at :460 and `cell(labels.error, 60)` at :463. Use `limit=None` at :310, :314, :347, :510, :513, :516. Pass remote codes through `cell()` at :349, :392, :433, :492, :485 (`None`), `exc.aadsts` at :310, and stored outcomes at :507. Use `cell(k, 32)` at
  :437, since `ext` comes from remote names (:422). Keep it local: the script's interpreter may hold an older agentsync build than `text.safe_text`, so it cannot rely on U-A1a. A parity test holds the two equal on sample strings.
- Test: extend `selftest()` with one assertion (ESC, bidi, `|` and newline in; a clean escaped string out; `…` only when cut), and `test_cell_matches_safe_text_on_samples`.
- Callers: none reads report cells. `200: still valid` and `410` stay readable (docs/plans/implementation.md:80).

U-B14 size: about 370 LOC (estimated: K59 60, K60 65, K62 45, K49 70, K50 half 30, K63 100).

#### B-15 `scripts/tenant-probes.sh`: tracebacks, signals and atomic state (unit U-B15; after U-B14 and D34)

**B-15.1 K52 (R283, R295, R275, R303; R138 and R375 apply by analogy only).**
- Sites: tenant-probes.sh:120, :127, :224-227, :269-273, :293-308, :462, :473-478, :498-501, :509-517, :528.
- Change: no new options; SystemExit paths 0, 2, 77, 78 are unchanged.
  1. :120: test `[ -f "$PY" ] && [ -x "$PY" ]`, and name the path and `--python`. Still exit 1.
  2. Wrap `import truststore` (:127) and the agentsync imports (:224-227, :269-273) in `except ImportError as exc`: `error: <sys.executable> cannot import <exc.name>: pass --python with the interpreter that has agentsync installed, or run
     scripts/install.sh`, exit 1. Handle this in-process, so `--selftest` still needs only truststore.
  3. :308: add `except AuthError` after the two existing arms and import it at :226. Emit a failed row, write the report, one stderr line, exit 1. This closes PAC-only and token-refresh failures (graph/auth.py:597, :942), which today are a traceback with no
     report.
  4. Store at :473-478: a validating loader that raises a local `AgentSyncError` subclass, so the arm at :515 sets rc 1 and probes 1, 2 and 4b still reach the report. Decode errors report `<store>: line L column C: <msg>`. Never echo `exc.doc` or a link, because links hold
     tokens. Shape errors name the key path and entry index. `version != 1` reports "written by a newer build"; a missing version is damage (every store has carried version 1, `git log -S`). Also report `OSError` on the read and a non-regular file. Raise before :499, so a
     damaged store is never truncated. Print the full line to stderr, since `cell()` caps at 160.
  5. `OSError` on writes: add to the :509-517 chain for :498-501, and guard `write_report` at :266, :311, :315, :528 with `error: cannot write the report under <out_dir>: <strerror>; pass --output`, exit 1.
  6. `read_labels`: at most wrap :462, so an unexpected error becomes this file's `error:` cell.
  7. Unexpected errors: **D34.** Under Option A, install one `sys.excepthook` at the top of the heredoc rather than re-indenting 400 lines: `Unexpected error in tenant-probes.sh. This is a bug in agentsync.`, an Error row with type and `<stdin>:line` only, a Version row, and a
     report link. **Round 2 fix:** the verdict left the URL open because pyproject.toml has no `[project.urls]` (measured with `grep`); use `it_request.REPO_URL` (src/agentsync/it_request.py:43), as C-3 does. It is reached only after the agentsync import succeeded. Delegate
     `KeyboardInterrupt`, and attempt `write_report` for the partial report.
- Test: `test_a_missing_interpreter_module_is_one_line` (rc 1, no `Traceback`), `test_a_damaged_store_keeps_the_other_probes` (bad JSON: rc 1, report has probes 1 and 2, the store bytes unchanged, no link text), and `test_a_newer_store_version_is_refused`.
- Callers: tests/test_deploy_pack.py:854 (dry-run stays silent), the apostrophe lints, docs/deploy/tenant-probes.md's exit table.

**B-15.2 K53 (R335, R416, R294, R007).**
- Sites: tenant-probes.sh:19, :26, :126, :353, :457, :518-520, :530, :532.
- Evidence (measured by the verifier with a stand-in of the same process shape under /bin/bash and python3; the script itself was not run): TERM or HUP to the Python pid or the group leaves the temp dir and no report, and a traceback on INT. TERM to the bash pid alone does not leak, but does not stop the work either. Static reading: at most
  one file's two downloads plus a `.part` remain, in a 0700 mkdtemp.
- Change:
  1. Replace `rc=0; "$PY" ... <<'PYTHON' || rc=$?` and the trailing `exit "$rc"` with `exec "$PY" -I -X utf8 - ... <<'PYTHON'`, so a signal to the script pid reaches Python.
  2. Add `class _Stopped(BaseException)` carrying the signal number, and one handler for INT, TERM and HUP. Before a `probing` flag is set just before mkdtemp at :353, it prints `Stopped. Nothing was downloaded; no report written.` and calls `os._exit(130 or 128+n)`. After it, it raises `_Stopped`.
  3. Add `except _Stopped` to the try at :354: emit a `**stopped: SIGxxx**` line and set rc to 130, 129 or 143.
  4. In the finally at :518-520, set the three signals to SIG_IGN, run `rmtree` first, restore SIG_DFL, then `client.close()`. Today a raising `client.close()` skips `rmtree`. The restore gives the required immediate exit on a second signal (R335).
  5. The tail writes the partial report and prints `report:`. On a stop, also print to stderr: `Stopped waiting. Nothing was written to the tenant; temporary downloads removed. Partial report: <path>`. Remote reads had started, so R294 asks for this lead rather than the finding's `Stopped.`.
  6. Add 130, 129 and 143 to the header (:31) and docs/deploy/tenant-probes.md:22-28.
- Test: `test_sigterm_removes_downloads_and_writes_a_partial_report`, using `os.killpg` under `start_new_session=True` and the shim (slow stub download): rc 143, no `agentsync-probes-*` left, report present. `test_sigint_prints_no_traceback` (rc 130). `test_a_stop_before_probing_says_nothing_was_downloaded`.
- Callers: none reads these paths. tests/test_deploy_pack.py:565, :819 still apply.

**B-15.3 K54 (R351, R353, R368, R319).**
- Sites: tenant-probes.sh:237-239, :473-474, :484, :498-501.
- Change: edit only the embedded Python, standard library only, all after the `--dry-run` exit at :291. Precedents: src/agentsync/ops/lock.py and governance.py (private, so inline).
  1. `_atomic_write(path, text)`: `mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")`, `fchmod 0600`, write, fsync, `os.replace`, unlink on `BaseException`, best-effort directory fsync. Use at :239 and :499. A replaced file is always 0600, so the finding's "fchmod an existing target" is not needed.
  2. Around :474-:501, `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `<out>/.deltalinks.lock` (0600), held through the save. Do not block, because the section makes network calls. On contention, emit a probe-9 line, set rc 1, skip probe 9, and still write the report.
  3. Save tested outcomes even when the loop exits early (after each tested link, or try/finally around :475-:486). Today the `raise` at :484 discards them, and the next run tests one-shot links again. The finding missed this path, and it is likelier than the race.
  4. Modes: if `--output` was absent, `chmod 0700` after mkdir, since header :9 promises it and the documented `mkdir -p` (docs/deploy/tenant-probes.md:12, it-request.md:230-231) defeats it. If it was given, warn when `st_mode & 0o077`, and do not chmod the operator's directory.
  5. Temp and lock names must not match `login-*.txt`, which :303 globs.
- Test: `test_an_interrupted_store_write_keeps_the_old_bytes` (the shim patches `json.dump` to write partial bytes and raise; old bytes kept, no temp left), `test_a_second_run_skips_probe_9_and_still_reports`, `test_outcomes_survive_an_auth_loss_mid_loop`, and `test_a_loose_default_dir_is_tightened`.
- Callers: docs/deploy/tenant-probes.md:41 (`probes/deltalinks.json (0600)`), docs/plans/implementation.md:80. Severity: an operator-run probe, so medium is the top of the range.

U-B15 size: about 425 LOC (estimated: K52 200, K53 115, K54 110).

#### B-16 `scripts/tenant-probes.sh`: `--json` (unit U-B16; after U-B15)

**B-16.1 K61 (R243, R244, R248, R253, R106, R086).** R244, R248 and R253 constrain the fix rather than being separate breaches. No caller needs this today, so it is the first candidate to defer (a priority call, not a refutation).
- Sites: tenant-probes.sh:46-49, :83, :87, :120-123, :126, :141, :241, :252, :266, :278, :283-290, :299, :311, :315, :509-517, :529.
- Change: purely additive; paths without `--json` stay byte-identical.
  1. `--json) JSON=1 ;;` and pass it as a new argv after `MAX_BYTES`. Scan argv before the loop, because `usage_error` and `--help` can fire before a later arm. Under `--json`, `usage_error` and the no-interpreter exit write one error line to stderr with the same code.
  2. `--dry-run --json` prints one document and writes nothing: `{"report":{"path"},"outcome":"planned","probes":[{"id":"2","requests":[...]},...],"authority":...,"requestedScopes":[...],"interpreter":...}`.
  3. A real run collects a structured record beside each `emit` (never parses the markdown). Exit 0: `{"report":{"path"},"outcome":"created","probes":[{"id","status",...}]}`, camelCase. A per-row failure stays a per-row value.
  4. Failures: stdout empty and one stderr line with `reason` from one local registry: `not_signed_in`, `sign_in_blocked`, `config_invalid`, `client_id_unset`, `network_policy`, `failed`, with `report.path` where one exists. Wrap the heredoc body so an uncaught exception still yields one document. Handle the import-failure path at the top.
  5. `--selftest --json` is a usage error (exit 2, R070), the smaller change. `--version --json` prints `{"version":...}`. `--help --json` prints one document without config or network, and plain `--help` stays.
  6. Privacy: only what the report carries. No token, no deltaLink, domain only for the username (:299), counts only for probe 1.
- Test: `test_dry_run_json_is_one_document` (`outcome == "planned"`, no files), `test_usage_error_json_is_one_line`, `test_failure_json_has_empty_stdout`, and `test_json_never_holds_a_delta_link`.
- Callers: tests/test_deploy_pack.py:854 pins the default path only. 77 and 78 are kept (D10).

U-B16 size: about 240 LOC (estimated: 130 src, 90 tests, 20 docs).

#### B-17 `scripts/install.sh`: `--version` and machine help (unit U-B17; after U-B12, D16 and D17)

**B-17.1 K24, Option B (R086).** Recommended, because it preserves every caller.
- Sites: install.sh:1734-1746, :9209, :1532, :9217; README.md:425, :439; tests/test_install_oneshot.py:496-505.
- Evidence and correction: the compat line is at :1746 (the finding's cited line was off). "Two lines" holds only with a `.git` (`:1736`). A third breach the finding missed: `--version --help` prints the version.
- Change:
  1. Keep plain `--version` byte-identical, with the compat line last. Record the named R086 exception in the header (lines 23-25) and U-C13's table, citing README step 1.
  2. The arm becomes `--version | -v)`, with the `-v` spelling only if D12 allows it (R086 itself names only `--version`). In that arm, scan the remaining `"$@"` before printing: `-h|--help` prints help and exits 0, and `--json` selects JSON. `--help` then wins in both orders.
  3. JSON: `{"version":<source commit or null>,"sourceCommit":...,"setupPromptCompat":34}`, on bash 3.2 through U-A2's `json_str`, escaping the `unknown (... ~/path ...)` reason. Read the number from `$SETUP_PROMPT_COMPAT` and add no new literal, because tests/test_deploy_pack.py's `_installer_compat` asserts a single value.
  4. Add `-v` to the skip arm at :1532. Update `INSTALL_OPTIONS`.
- Test (`tests/test_install_json.py`): `-v` matches `--version`; `--version --help` and `--help --version` both print help; `--version --json` and `--json --version` print the document; `--json` alone exits 2. `test_version_prints_the_prompt_compat_line_last` stays.
- Conflict recorded: `-v` means version here and verbose in agentsync (D12).
- Option A is in U-C17, only if D17 chooses it.

**B-17.2 K23, Option A (R106, R107, R108).** Only if D16 chooses it. Option B changes no code and records a deviation under KISS K17. Do not generate human help from a table or wait for R087: moving text out of the header breaks tests/test_deploy_pack.py:1058, :1478, :4161 and CONTRACTS.md:20396.
- Change:
  1. Add a static `show_help_json` using printf only: path, summary, `subcommands: []`, arguments, options, lifecycle label, result schema. List public options only, without `--no-report`, `--log-end` or SOURCE.
  2. Use B-12.1's single `--json` arm. `-h|--help` also checks the remaining args for `--json`, so both orders print JSON.
  3. Handle a first argument of `help` in the first-argument dispatch at :1492, before the SOURCE arm. **Round 2 fix, reconciled with K25:** U-B7's main-loop `help)` arm stays for bare `help`. The new dispatch arm takes only `help --all --json`, which prints the one document in a list, and `help search <query>`, which matches summary and options and exits 0 with `No commands match "<query>"` on no match. It exits before any `log_start`, so the query is never recorded (R108). The SOURCE seam stays reachable as `./help`.
  4. Add a drift test comparing the JSON option names to `INSTALL_OPTIONS["main"]` minus `INSTALL_HIDDEN` and `*`, and a CONTRACTS section.
- Test: `test_help_json_is_one_document_in_both_orders`, `test_help_all_json_lists_one_command`, `test_help_search_no_match_exits_0_and_writes_nothing`, `test_help_json_options_match_the_pin`.
- Callers: every `--help` assertion stays. R107 and R108 are close to vacuous for a tool with no subcommands, which is why D16 exists; R106 has the real value, since setup agents read this help.

U-B17 size: about 295 LOC (estimated: K24 120, K23 175).

---

## 4. Wave C: contract-changing fixes (each lands its callers in the same commit)

All of these units own `cli.py`, so they land one at a time. Each lists its callers (origin/main anchors) and its
migration.

### C-1 K2: help and version contract (R081, R082, R083, R086, R055, R084, R111, R095) (unit U-C1)

- **Change:**
  - (a) Bare `agentsync` (cli.py:4566): `parser.print_help(sys.stdout); return EXIT_OK`.
  - (b) Add a hidden `help [COMMAND...]` subparser that re-parses `[*path, "--help"]`. Hidden keeps §16.10's
    KISS visible list. It must then be added to `CLI_SURFACE["hidden"]` and §16.10's Hidden line.
  - (c) `--version` at any depth: **delete** the root `parser.add_argument("--version")` (:237) and add it once to
    `_common` with `help=SUPPRESS` on the subcommand copy. Keeping both raises an argparse conflict, and the
    verifier caught that defect in the original fix.
  - (d) An `AgentsyncParser(argparse.ArgumentParser)` with `allow_abbrev=False` built in, passed as
    `parser_class=` to `add_subparsers`. Its `error()` turns an invalid command choice into:
    - `Unknown command <x> for agentsync.`
    - at most one `difflib.get_close_matches(x, visible, n=1)` suggestion, drawn from `sub._choices_actions`
      (visible commands only)
    - `Run ~/.local/bin/agentsync --help.`
    - exit 2.

    Every other usage error keeps its text.
  - (e) A formatter that wraps descriptions at `min(COLUMNS, 80)` and returns the epilog verbatim, plus a rewrap
    of `_EPILOG` lines longer than 80 columns.
- **Callers:**
  - No production caller runs agentsync without a verb or reads this output. The verifier grepped install.sh,
    the launchd argv and tenant-probes.
  - Test pins:
    - tests/test_cli.py:251 (`cli.main([]) == cli.EXIT_USAGE`)
    - tests/test_contracts.py:52 (`CLI_GLOBAL_OPTIONS`; keep `"--version"` for the root), :159 (`common`: add
      `"--version"`), `CLI_SURFACE["hidden"]["help"] = ["path"]`, §16.10 Hidden
    - :203 (hidden verbs; add `"help": []` to `needs` if it requires a positional)
- **Migration:** in the same commit:
  - tests/test_cli.py:251 expects `EXIT_OK` and help on out. :252-253 stay at 2.
  - Update the three test_contracts pins and the §16.10/cli CONTRACTS text.
- **New tests** (`tests/test_cli_parser.py`):
  - `test_unknown_command_suggests_one_visible_name`: `["sycn"]` returns 2, err contains
    `"Unknown command sycn"` and `"sync"`, and err contains neither `"doctor"` nor `"init"`.
  - `test_version_works_at_any_depth`: `["status","--version"]` returns 0 and
    `out == f"agentsync {__version__}\n"`.
  - `test_help_verb_matches_dash_h`: `["help","status"]` out == `["status","-h"]` out.
  - `test_help_fits_80_columns`: with `COLUMNS=80`, every line of root and visible-subcommand help is at most 80
    columns.
- **Operator decision:** none needed if `help` stays hidden. Making it visible changes the KISS K19b visible
  list.
- **Round 2 addition, K69 (R088, R091, R092, R096, R100, R101, R099, R090).** C-1 rewrites help, and no round 1 row
  covered section content.
  - Evidence (origin, read): `_EPILOG` (cli.py:156-180) holds exit codes and files. There is no Examples section, and
    root help is argparse's one flat COMMAND list (cli.py:239).
  - Change:
    - Root help groups the visible commands by task, in the description or a custom `_format_help` section: "Keep in
      sync" (add-source, sync, adopt, accept-deletions), "Review" (status, curate), "Delete" (purge, hold, offboard),
      each once with its summary. Hidden verbs stay unlisted.
    - Each visible leaf gets an `Examples:` block with the fewest examples covering distinct jobs, including risky
      use: `{loop.BIN} add-source <PATH>`, `{loop.BIN} purge id=<STABLE_ID> --dry-run`,
      `{loop.BIN} offboard --dry-run`. One template per job, no filled-in duplicate, long options only (R099, R101).
    - The `exit codes:` and `files:` blocks become `Notes:` on the root page, and a one-line pointer on leaf pages
      (R090, R092). `Learn more:` gives `it_request.REPO_URL`.
  - Tests (`tests/test_cli_parser.py`):
    - **`test_every_help_example_parses` (R100, R393):** collect the `Examples:` lines from root and every visible
      leaf, replace each `<UPPER_CASE>` placeholder with a synthetic value, `shlex.split`, strip `loop.BIN`, and call
      `cli.build_parser().parse_args(argv)`. No handler runs, so it is never a live mutation.
    - `test_printed_recovery_commands_parse` (R099, R100): the same parse for the agentsync commands `clio.recovery`
      prints in B-4.1, B-4.2, C-2 and C-5, added to as each unit lands.
    - `test_help_groups_each_visible_command_once` (R096).
  - Callers: tests/test_contracts.py:203's `test_help_lists_no_hidden_verb_and_each_still_parses` and tests/test_cli.py:138,
    :171 read help. Keep the `exit codes:` words those read, and re-pin in the same commit if the heading moves.
  - Round 2 size: U-C1 about 380 LOC (200 plus K69's ~180).

### C-1b K68: machine-readable help and field selection (R106, R107, R108, R252, R454, R393) (unit U-C1b; after U-C1 and U-B5) (Round 2)

- **Critic entry verified:** R107 and R108 are MUSTs no plan scoped. R106 and R252 are in the same position here:
  `git grep -nE -- '--fields|help search|--help --json' origin/main -- src` finds none.
- **Change:**
  - `agentsync <path> --help --json` prints one document on stdout, generated from `build_parser()`'s actions: path,
    summary, subcommands, arguments, options (name, short form, type, value hint, default, accepted values,
    repeatability, deprecation), lifecycle label and result schema. The schemas are the U-B4/U-B5 shapes, kept in one
    table in cli.py. Hidden commands and options stay out.
  - `agentsync help --all --json` prints every visible command from the same metadata. `help` already exists after U-C1.
  - `agentsync help search <query>` works offline, matches summaries, nouns and examples, prints up to 10 canonical paths
    with summaries, `--json` gives `{"commands":[{"path","summary"}]}`, and no match exits 0 with
    `No commands match "<query>"`. The query is never logged (R108). The verb is `help` with a `search` word, so no new
    verb enters `CLI_SURFACE["hidden"]`.
  - `--fields <FIELDS>` on commands with a result schema; an unknown field is a usage error that lists the valid ones
    (R252, R454). Add it to `_common` beside `--json`, and update `common` and `CLI_GLOBAL_OPTIONS` in tests/test_contracts.py.
- **Tests** (`tests/test_cli_json.py`): `test_help_json_matches_the_parser` (each option in `format_help` is in the
  document and the reverse), `test_help_all_lists_only_visible_commands`, `test_help_search_no_match_exits_0`,
  `test_help_works_with_a_broken_config` (R083, R411), `test_fields_keeps_only_named_fields`, and
  `test_unknown_field_lists_the_valid_ones`.
- **Callers:** additive. Keep `--help` text unchanged. None of it runs before `parse_args` succeeds except the
  `--help --json` pre-scan, which must not load config.
- Size: about 410 LOC (estimated: 220 src, 170 tests, 20 docs).

### C-2 K4: consent gate for purge, purge --queue and compact-history (R146, R002, R015, R148, R155, R034, R439, R413, R263) (units U-C2a, U-C2b)

This lands in three commits, so every commit is green and only the last changes behaviour.

**Round 2 fix (critic planConflicts, R439).** Round 1 added a new `--confirm` spelling, which R439 bans, on the ground
that the rule was a Vercel naming table. The checklist generalizes it, so the flag below is `-y, --yes`. `offboard
--confirm` already exists and has its own question: D11.

1. **Accept the flag** (U-C2a, compatible).
   - Add `-y, --yes` to `purge` and `compact-history` (parsed, no effect yet).
   - Update the pins tests/test_contracts.py:63 (`"purge"`) and :90 (`"compact-history"`) and CONTRACTS.
2. **Teach the flag** (U-C2a, compatible). The runnable "then run `purge --queue`" second command gains
   ` --yes` at:
   - cli.py:1651-1652 (status queue line), :3077-3078 (the doctor fix)
   - cycle.py:555-556 (`_PURGE_QUEUE_STEPS`), :4104-4105 (STATE.md)
   - loop.py's WAITING ON YOU wait line (checkout loop.py:927-929)
   - governance.py's invitations (checkout :3459, :3473, :3493). On origin, re-locate them with
     `git grep -n 'purge --queue' -- src`.
   - setup_report.py's (checkout :9370; the `--forget` path stays as it is)

   Prose such as "`purge --queue` refuses until then" is not an instruction and stays. Re-pin the tests that
   assert those exact strings. Census: `git grep -c 'purge --queue' origin/main -- tests` gives 122 lines in 8
   files (test_cli 67, test_setup_report 18, test_deploy_pack 14, test_loop 7, test_governance 5,
   test_review_fixes 5, test_install_oneshot 4, test_e2e 2). Only the lines that pin a runnable second command
   change.
3. **Turn the gate on** (U-C2b, breaking).
   - Placement: in `_cmd_purge`, the gate comes after `_lost_state_stop` / `_open_state_or_raise` and after the
     `--forget` and `--accept-lost` branches, which erase nothing. In `_cmd_compact` (:3992) it comes after
     `_lost_state_stop`.
   - When neither `--dry-run` nor `--yes` is given:
     - run the matching dry run (`governance.purge(..., dry_run=True)`, `run_purge_queue(dry_run=True)` or
       `compact_history(dry_run=True)`);
     - print its report to stderr through the existing formatter;
     - with `--push`, add a line naming the remote and the refs that will be force-pushed (`--force-with-lease`,
       governance.py:3968);
     - end with `This cannot be undone.`, then `clio.recovery("To run it", <exact argv> + " --yes")`;
     - exit 2 with `confirmation_required`, and `clio.error_doc` in JSON mode.
   - **Round 2 addition (R146, R150, R147, R413).** R146 requires a prompt on a terminal, and round 1 only exited 2.
     All of it goes through `clio.confirm`:
     - Interactive (`clio.interactive()` and not `--json`): after the preview, ask. `purge` is severe (R155: irreversible
       loss of user data): `To confirm, type the docs repo name "<name>":`. `compact-history` is destructive:
       `Squash history older than <N> days? (y/N)`, default No. A mismatch names the expected answer; a decline exits 130
       (Reference L). These are the first prompts agentsync has: `git grep -nE 'sys\.stdin|input\('` on src/agentsync
       finds none (measured), so R119's stdin rule is met by default.
     - Non-interactive: exit 2 as above. The JSON document has `status: action_required`, `userActionRequired: true`,
       `data.effects`, and `next` with `when: "Only after a human approves: <consequence>"` (R254, R263).
     - `--yes` with a selector in argv is enough non-interactively (R146, R151). `purge --queue --yes` acts on the queue,
       whose items were each queued by a deliberate act, and the preview lists them. R151 asks for targets from argv, so
       record this in U-C13's table.
   - When the dry run finds nothing to do, skip the gate and exit 0 as now.
- **Callers:**
  - Operator-typed commands only. CONTRACTS says `purge --queue` is "never inside a sync cycle".
  - The pinned exit table in CONTRACTS (checkout :4778-4779).
  - README.md:52 and :143.
  - tests/test_loop.py's glob purge expecting `EXIT_OK` (checkout :2964): add `--yes`.
  - These keep exit 1 because the gate comes after the lost-state stop: tests/test_review_fixes.py's lost-state
    cases (checkout :726-733) and tests/test_cli.py:13517 (`purge --queue` and `compact-history` on a lost
    manifest).
- **Migration:** callers are updated in the same commits as above. There is no legacy flag: the old bare command
  now exits 2 and shows the same preview `--dry-run` gives. Add a dated CONTRACTS and README note.
- **New tests** (`tests/test_cli_consent.py`):
  - `test_purge_without_yes_previews_and_changes_nothing`: rc == 2, `"This cannot be undone." in err`,
    `"--yes" in err.splitlines()[-1]`, and `git rev-parse HEAD`, the reflog and the queue file are unchanged.
  - `test_purge_queue_with_yes_runs`: rc == 0, and the item is gone from history.
  - `test_nothing_to_purge_needs_no_yes`: rc == 0.
  - `test_compact_history_gate`, the same three cases.
  - Round 2: `test_purge_prompt_defaults_to_no_on_a_terminal` (a pty answers `""`: rc 130, nothing changed),
    `test_purge_wrong_name_names_the_expected_one`, `test_short_y_equals_long_yes` (R392), and
    `test_printed_run_command_parses` (R100: the `--yes` recovery line goes through `build_parser`).
- **Operator decision (Round 2 fix):** the plan's default is `-y, --yes` (R439). If the operator prefers one word
  across the CLI, Option B keeps a new `--confirm` for parity with `offboard --confirm`, with an R422 record. D11 holds
  both options together with `offboard` and `--confirm-install-agent`.
- Round 2 size: U-C2b about 420 LOC (300 plus ~120 for the prompt path and its tests).

### C-3 K10: catch-all bug report instead of a traceback (R283, R295) (unit U-C3)

- **Change:** in `main()` (cli.py:4544), around `_main(argv)` and inside the umask try, add `except Exception as exc:`.
  - Always: `_log.debug("unexpected error", exc_info=True)`, so the traceback shows at `-vv`, the repo's existing
    debug level.
  - Otherwise: `clio.bug_report(exc, command, version=__version__, issues_url=it_request.REPO_URL)` (it_request.py:43).
  - Return 1, the same code an uncaught exception gives today.
  - In launchd job runs (`_in_job`, cli.py:4090), also `_log.error(..., exc_info=True)` so the job's err.log keeps
    the traceback. That file is a log, not the user's terminal.
  - The ladder's deliberate `raise`s (the U72/V59 allowlist) stay as they are and now reach this handler. Reword
    their "keeps its traceback" comments.
- **Callers:**
  - Three tests assert the raw raise: tests/test_cli.py:4279 (`pytest.raises(RuntimeError)`), :4767
    (`IntegrityError`/`OperationalError`) and :6272 (`PermissionError`).
  - setup_report.py:480 `_LEVEL_RE`, which matches `^Traceback ` and `^(?:error|fatal):`, used by
    `_recent_errors` to pick crashes out of a job's err.log.
  - install.sh classifies a non-zero status with no `[FAIL` line as `status-error`. The exit code is unchanged,
    so that still works.
- **Migration:** in the same commit:
  - The headline starts with `error:`, so `_LEVEL_RE` matches it unchanged. Keep `^Traceback ` for old logs.
  - Add the internal escape hatch `__AGENTSYNC_RAISE=1` (R079 internal prefix), which re-raises.
  - Change the three tests to `monkeypatch.setenv("__AGENTSYNC_RAISE", "1")` and keep their `pytest.raises`.
- **New tests:**
  - `tests/test_cli_streams.py::test_an_unexpected_error_is_a_bug_report`: a handler raises
    `RuntimeError("/Users/x/secret")`. Assert rc == 1, that `err.startswith("error: Unexpected error in agentsync sync.")`,
    that `"secret" not in err`, and that `"Traceback" not in err`.
  - A setup_report test that the job's crash still lists under Recent errors.
- **Operator decision:** none if the job-log traceback stays as above.

### C-4 K1: stdout carries the result; receipts, progress, NEXT and login text go to stderr (R016, R173, R177) (units U-C4a, U-C4b, one landing commit)

- **Change:**
  - The 38 cited `_out` sites move to `_err` / `clio.say`. These are:
    - `wrote` (:1003) and `tightened` (:1049)
    - the started-over note (:602-604), the run line (:606) and the rest of `_print_report` (:536)
    - `LOCK_WAITED_MARK` (:927)
    - sync's `loop.next_lines` output (:1243, :1249)
    - `auth.login(_out)` (:3690), which becomes `auth.login(clio.say)`
    - install-agent's `check:` (:3782)
    - offboard's `manual:` / `dry run:` (:4061/:4063)
    - the receipt lines of add-source, init, hold, purge and compact-history
  - stdout keeps one bare result per mutation: sync prints the run id; add-source the source id; init the config
    path; hold the scope; purge/compact the selector and outcome.
  - status, curate and setup-report keep their stdout. `loop.next_lines` itself does not change; each caller picks
    its stream (status keeps stdout at :2031).
  - **Round 2 addition (R176):** the bare result prints only on exit 0. On failure, stdout stays empty and the id goes
    in the error's `data`.
- **Legacy flag (the migration).** clio gains `COMPAT_STDOUT_ENV = "AGENTSYNC_COMPAT_STDOUT"`. When it is `1`,
  `say()` routes the moved receipt lines to stdout as today. Errors stay on stderr. CONTRACTS marks it deprecated,
  to be removed when install.sh's parsers read stderr. **Round 2 fix (R388, Reference C):** round 1 named it
  `__AGENTSYNC_LEGACY_STDOUT`, but a compatibility switch for a breaking stream change is `<PREFIX>_COMPAT_<NAME>`, which
  a script owner may set, and `__` is for internal or test use. It also stays visible to the gate's salt
  (scripts/gate.sh:64 anchors on `^AGENTSYNC_`). Apply the new name in every row below.
- **Callers, updated in the same commit:**
  - install.sh add-source loop (:10399): `run "$AGENTSYNC" add-source ... | add_source_filter` gets the env
    prefix. `add_source_filter` (:10358) awks `^added source`, `^added inbox source`, `^docs repo`,
    `^time machine:` and `^sources:`.
  - install.sh `first_sync_run` (:10805-10806) gets the env prefix. Its readers then stay unchanged:
    - `show_first_sync` (:10811) greps `SYNC_SUMMARY_RE` (:10796), `^note: a new manifest was started over`
      (:10819) and `note: .* an older build gave up on` (:10834) in `$SYNC_OUT`
    - :10878 `grep -qx "lock wait: this sync waited for another sync's lock" "$SYNC_OUT"`
  - An older installed agentsync ignores the variable and already prints the old layout, so a new install.sh is
    safe against either build.
  - tests/test_install_oneshot.py's stubs (:197 `echo "added source ..."`, `STUB_SYNC_OUT` at :1114-1219) stay
    unchanged, because install.sh asks for the legacy layout.
  - tests/test_cli.py:
    - `tightened` (:335-367), `LOCK_WAITED_MARK` (:1305-1344) and `checkpoint advanced` (:788, :953-1027) move
      their assertions from `.out` to `.err`.
    - The NEXT-on-stdout assertions on error paths (:6743-6744, :9244-9245 `said.out.splitlines()[-1] == loop.NEXT_PREFIX + ...`,
      :13523) move to `.err`.
    - 228 `readouterr().out` reads in test_cli.py are the census ceiling (measured with `grep -c`); only those
      after a mutation change.
  - Other test files that assert sync/add-source stdout: find them by running the affected gate after the switch.
    They are U-C4b's.
  - README.md:51, :86-90 and :447-537: agents still see NEXT/WAITING lines, because agent tools capture both
    streams. Change "prints" to "prints on stderr" where the README names the stream.
- **New tests:**
  - `tests/test_cli_streams.py::test_sync_stdout_is_only_the_run_id`: `cli.main(["sync","--config",cfg])`;
    `out.strip()` matches the run-id pattern, and the run line and NEXT are in err.
  - `test_add_source_stdout_is_only_the_source_id`.
  - `test_legacy_stdout_keeps_the_old_transcript`: with `AGENTSYNC_COMPAT_STDOUT=1`, assert `"converted"` and
    `LOCK_WAITED_MARK` are on out.
  - Round 2 (R176, not asserted by any round 1 test): `test_a_failed_sync_prints_no_identifier` and
    `test_a_failed_add_source_prints_no_identifier`: rc != 0, `out == ""`.
  - Round 2, U-C4b, `tests/test_cli_modes.py` (new, about 170 LOC; R403, R401, R404, R400, critic):
    - Mode matrix for the stream, consent and login changes. For sync, add-source, `purge id=x`, `purge id=x --yes` and
      `graph login`, each on success and on failure, run under stdout redirected, stdin piped, `--json` on a pty, `CI=1`
      on a pty, and an interactive pty. Assert whether a prompt appeared, what each stream holds and the exit code. The
      `--non-interactive` and detected-agent rows join only if D12 and C-5's decision add them.
    - Transcripts at 40, 80 and 120 columns for `sync`, `status` and `hold --list`, with a long Unicode folder name, plus
      each stream on its own (R401).
    - R404: agentsync has no color today (measured: `git grep -c 'x1b'` on src/agentsync hits only setup_report.py), so
      this is one assertion: with `FORCE_COLOR=1`, `TERM=dumb` and `NO_COLOR=1`, output holds no `\x1b`. A later color
      change meets the rest of R404 then.
    - R400: a closed pipe, a signal and no orphan, shared with `tests/test_cli_streams.py`.
    - Round 2 size: U-C4a about 470 LOC, U-C4b about 420 LOC.
- **Operator decision:**
  - What each mutation prints as its bare result. Sync's run id or its commit sha? The recommendation is the run
    id, because a no-change pass has no sha.
  - Whether to keep the legacy flag (recommended) or rewrite install.sh's parsers to read stderr now. The rewrite
    costs about 150 more LOC in install.sh plus stub rewrites, and changes installer UX: the full sync report
    would stream live.

### C-5 K12: login in non-interactive mode (R172, R014, R168, R019) (unit U-C5; after C-4, which moves login's text to stderr)

- **Change:**
  - `_cmd_graph` login (:3690): if `not clio.interactive()` (stdin not a TTY, `CI`, or `SSH_CONNECTION`/`SSH_TTY`;
    deliberately not stdout/stderr), refuse **before** building an MSAL app. Exit 77 (`EXIT_REAUTH`, which
    CONTRACTS already lists for reauth) with:
    - `error: Can't sign in: this run has no terminal.`
    - `clio.recovery("Run it in a terminal on this Mac", f"{loop.BIN} graph login")`

    Do not print a URL over SSH: the loopback redirect is localhost on this Mac.
  - Add `--no-open` to `_graph_options`. It skips the broker rung and passes an `auth_uri_callback` that only
    prints the URL.
  - In graph/auth.py:
    - the loopback rung always prints the URL. Its `auth_uri_callback` prints the URL, then calls
      `webbrowser.open`, because MSAL calls the callback only on failure;
    - bound the broker rung's `acquire_token_interactive` with a deadline of `interactive_timeout_s` (a thread
      plus `join(timeout)`; a timeout is `_FallThrough`).
  - The success line (`signed in as ...; method ...; token source ...`) stays last and unchanged, because
    tenant-probes.sh:303-306 quotes the last lines of the tee'd file.
- **Callers:**
  - The documented `agentsync graph login 2>&1 | tee login-*.txt` recipe (docs/deploy/tenant-probes.md:13,
    it-request.md:231, tenant-probes.sh:314). stdin is still a TTY there, so it keeps working.
  - tests/test_cli.py's login tests (checkout :1983-2012) run under pytest with a non-TTY stdin and expect exit
    0/1/77/78.
  - tests/test_contracts.py `CLI_SURFACE` entries for `graph` and the hidden `login` alias (both use
    `_graph_options`): add `"--no-open"`.
- **Migration:** in the same commit, add a fixture that monkeypatches `clio.interactive` to `True` for the
  existing login tests, update the surface pins, and add a dated CONTRACTS login-row note.
- **New tests:**
  - `test_login_refuses_without_a_terminal`: rc == 77, err contains `graph login`, and no MSAL app is built
    (the factory is monkeypatched to fail if called).
  - `test_no_open_prints_the_url_only`.
  - `test_broker_rung_times_out_to_loopback`.
- **Operator decision (minor):** whether agent-harness environment variables (for example `CLAUDECODE`) also mark
  non-interactive mode, and whether a global `--non-interactive` is wanted. A global flag changes every `CLI_SURFACE`
  entry. **Round 2 fix:** round 1 said `--no-input`, a spelling Reference B does not have (R062); D12 holds it with the
  other global options. **Critic R117, corrected:** agentsync has no agent detection today (measured: `git grep -nE
  'CLAUDECODE|isatty'` on src and scripts/install.sh, 0 hits). R117 applies only if this decision adds it: a detected
  agent then runs non-interactively and still gets human output.
- **Round 2, critic R167, R170 and R169.**
  - R170: the login row above prints the URL and bounds the wait, and the non-interactive error names the recovery. R170's
    "set the token variable" and R167's `--token` have no counterpart: D39.
  - R169's refresh lock holds: msal-extensions' `PersistedTokenCache` takes `CrossPlatLock` on `.lockfile` around cache
    writes (`.venv/lib/python3.11/site-packages/msal_extensions/token_cache.py:69`, read), and auth.py:641 uses it for both
    persistences. Mid-command expiry fails with `login_required` and `userActionRequired: true` (B-5 table).
  - R171 and R480 are N/A: agentsync creates no credential and has no terms gate.

### C-6 K6: read-only commands and dry runs must not migrate (R325, R137) (unit U-C6) **needs an operator decision**

- **Why a decision:** migrating on every open is documented design. CONTRACTS.md:109-117 (KISS K12 and its
  review) says every opener migrates, `status` and `--dry-run` included, and narrows "no writes" to docs/ and the
  manifest's rows. A full fix reverses that contract.
- **Option (a), full fix (~320 LOC):**
  - Add `Manifest.open_readonly(db)` (`file:...?mode=ro`, the URI ops/doctor.py already uses). It returns a
    "migration pending" sentinel for an older schema.
  - Use it in `loop.next_step` / `_status_lines` (status), `loop.checkpoint_findings` (curate) and
    `_open_state_or_raise` (cli.py:3500) on every dry-run path.
  - status prints one note or WARN line, never `[FAIL]`, so install.sh's exit and `[FAIL` parsing hold:
    `manifest: schema v<old> migrates on the next \`agentsync sync\``.
  - Skip `doctor.record_unreadable` (:4595, :4689) for dry runs.
  - Callers: tests/test_cli.py:1681 `test_old_manifest_migrates_through_status_and_sync` (re-pin: status leaves
    no `.pre-v*`, and the next sync migrates); CONTRACTS:109-117; the purge `--dry-run` help (cli.py:423 "an older
    manifest is still migrated first"); the migrate message (:3681; keep the `"migration is automatic"`
    substring); docs/deploy/data-governance.md:41. The new public Manifest method also needs a src caller and
    CONTRACTS (scanner rules 1-2).
- **Option (b), compatible (~60 LOC, recommended if the K12 design stands):**
  - Keep migration. Whenever a read-only or dry-run command migrates, print a stderr note naming the
    `<db>.pre-v<N>` copy.
  - Fix the purge help text to say so plainly, and fix data-governance.md:41's "writes nothing".
  - Test: `test_status_migration_is_announced`.
- **Round 2 note (critic R432).** R432 (a `status` command has no side effects) is the same question as R137 here, so
  this decision covers it.
- **Round 2 addition, K66 (R326, R003): `hold --release` of nothing** (folded here: too small for its own session;
  **decision D37**).
  - Site: cli.py:4038-4042. `release_hold` returning False prints `hold <scope>: no state hold (config holds live in
    sources.toml)` and exits 1.
  - Change under Option A: when no state hold exists and no config hold names the scope, print `Hold <scope> is already
    released.` and exit 0, `outcome: "unchanged"` in JSON. A hold that still lives in sources.toml keeps exit 1 and its
    line, because the release did not take effect and the person must edit the config.
  - Test: `test_releasing_nothing_is_an_explicit_no_op` and `test_a_config_hold_still_refuses_release`.
  - Callers: `git grep 'no state hold'` finds only cli.py:4040, so no test pins the text. tests/test_cli.py's release calls
    (:3224, :3750, :6346, :6382) release a real hold and expect `EXIT_OK`. Check :8686, whose scope may be absent, before
    landing. Round 2 size: U-C6 option (b) about 110 LOC with this.

### C-7 K18: one error layout (R276, R277, R280, R205) (unit U-C7) **needs an operator decision**

- **Why a decision:** the headline prefixes and the ` (fix: ...)` suffix are a parsed machine contract, not copy.
  - install.sh greps `^configuration error: ` (:3140, :3174) and seds ` (fix: ` (:3555-3571).
  - setup_report.py branches on `error: state:` / `state:` / `configuration error:` and splits on ` (fix: `,
    including when it re-reads old install.out files.
  - doctor.py emits the same format.
  - CONTRACTS documents it.
  - The verifier counted assertion lines in the checkout: test_setup_report 54, test_cli 46,
    test_install_oneshot 17, test_deploy_pack 9.
- **If approved, migration in two commits:**
  1. Compatible: every parser (install.sh, setup_report.py, doctor.py) accepts both the old prefixes and a new
     `Error:` headline plus the indented recovery block (~200 LOC).
  2. Breaking: one `_error(headline, details, instruction, command)` emitter in cli.py and doctor.py, plus re-pins
     (~350 LOC).
- Also in step 2, all test-pinned:
  - `try again when it ends` (cli.py:1928; setup_report repeats it; tests/test_cli.py:4820) becomes
    `Check whether the other run has finished:` / `    {loop.BIN} status`.
  - `page(s) ... deletion notice(s)` (cli.py:627, :1740-1741; tests/test_cli.py and test_cycle.py) is reworded
    with no count, for example "every page of it is now a deletion notice". It cannot be pluralized because the
    page count is unknown there.
- **Recommendation:** land only step 1 now, if at all. The compatible parts (empty hold list, unknown source id)
  are already in Wave B.

### C-8 K16: noun-verb command grammar (R030, R029, R047) (unit U-C8) **needs an operator decision**

- **Why a decision:** the visible surface is frozen on purpose (KISS K18/K19b; tests/test_contracts.py §16.10
  pins).
  - install.sh calls `add-source` (:10399) and `install-agent` (:10957-10963) by name, and branches on
    install-agent's rc.
  - The awk at install.sh:3178 (`index(s, " add-source ")`) and the id redaction (checkout :2604,
    `exclude-empty <id>`) parse the fix strings loop.py prints.
- **If approved:**
  1. Additive commit (~350 LOC): `sources add`, `deletions accept`, `agent install|uninstall`, `skill install`,
     `history compact`, `holds list|set|release`. Old tokens become hidden aliases with byte-identical output and
     no stdout warning (install.sh pipes add-source's stdout). `CLI_SURFACE` and §16.10 are updated.
  2. Teaching commit (~250 LOC): loop.py and setup_report.py fix strings switch spelling, together with
     install.sh's awk accepting both spellings, the redaction, the README and the docs.
- **Recommendation:** defer. The cost is high and the user-visible gain is small.

### Round 2 additions to Wave C

All install.sh units stay in the chain order of §6. U-C4a's two env prefixes (C-4) need install.sh too, so no chain unit
is active while it is.

#### C-8b K65: rename `purge` (R035, R033, R034) (unit U-C8b) **only if D36 chooses Option A**

- **Why a decision:** `purge` is a visible, frozen command (`CLI_SURFACE["visible"]`), and Reference A keeps `purge` for cached
  content only. `git grep -c 'purge --queue' origin/main -- tests` alone gives 122 lines (C-2's census). The compatible half is B-1.6.
- **If approved:** add `delete` as the canonical verb, keep `purge` as a hidden alias (R385, R050, printing the K17 deprecation
  line once per run), rename `--purge-data` to `--delete-data` the same way, make outcomes `deleted` in JSON (R033, U-B5), and teach
  the new word in fix strings, the README and data-governance.md. The queue file and audit records keep their stored
  words. About 300 LOC plus the census's re-pins, split by the affected gate.

#### C-9 `scripts/install.sh`: conflicts, the uv pin, and the consent spelling (unit U-C9; after U-B17, D11, D22)

**C-9.1 K27 (R064, R070, R074): reject what `--report-only` ignores; reject an empty `--launcher`.**
- Sites: install.sh:9192-9208, :9198, :9255-9262, :9239, :9297, :9619, :4-5.
- Evidence: `--report-only` exits 0 at :9262 before anything reads `INSTALL_AGENT`, `LAUNCHER_SRC`, `SOURCE_LOCALS` or SOURCE, while `--list-folders` rejects extras (:9239). `--launcher ""` passes the arity check and is treated as not given at :9297 and :9619, so it skips "--launcher only applies with
  --confirm-install-agent".
- Change:
  1. :9198 becomes `if [ $# -lt 2 ] || [ -z "$2" ]; then usage_error "--launcher needs a path"; fi`, keeping the message (tests/test_launcher.py:627).
  2. Directly after the loop, **before** `PARSED=1` and the traps: `if [ "$REPORT_ONLY" -eq 1 ] && [ "$LIST_FOLDERS" -eq 0 ] && [ -n "$ORIG_ARGS" ]; then usage_error "--report-only takes no other option or SOURCE"; fi`. Do not put it in the arm at :9255: past `PARSED=1`, on_exit would still write a report on exit 2. `ORIG_ARGS` already omits
     `--report-only`, `--no-report`, `--list-folders`, `--version` and help (:1532), so bare `--report-only` and `--no-report --report-only` still exit 0, and the `LIST_FOLDERS` gate keeps the message at tests/test_install_oneshot.py:8789.
  3. The synopsis at lines 4-5 advertises the combination. Give `scripts/install.sh --report-only` its own line (no test pins it).
  4. Record exit 0 to exit 2 in CONTRACTS. `INSTALL_OPTIONS` does not change.
- Test (`tests/test_install_parser.py`): `--report-only` with each of `--confirm-install-agent`, `--source-local X`, `--launcher X` and SOURCE gives exit 2, `usage error:`, no NEXT, HOME untouched, the friction attempt still open. `--launcher ''` with and without `--confirm-install-agent` gives exit 2. `--no-report --report-only` stays exit 0.
- Callers: no in-repo caller passes either input (verifier's AST walk over tests: 175 `install_sh` calls with `--report-only` pass only env; `git grep` for the combinations, 0 hits; `git log -G` over README.md, docs/deploy and install.sh, 0 commits). tests/test_install_oneshot.py:20137 already asserts exit 2 for friction combinations. Saved prompts run separate invocations: `--log-end
  && --report-only` is first-argument dispatch, and `--no-report` is excluded from `ORIG_ARGS`. An operator's own script cannot be ruled out, so a dated note matters.

**C-9.2 K33, Part B (D22, only if Option 1): pin uv.**
- Sites: install.sh:1580, :9346-9348, :1655; scripts/download_hosts.py:69; tests/test_deploy_pack.py:159, tests/test_launcher.py:658; .github/workflows/ci.yml:79.
- Change: `UV_INSTALLER_URL="https://astral.sh/uv/0.12.17/install.sh"`, the version ci.yml already uses, as a **column-0 literal**, because download_hosts.py:69's regex reads it and interpolating `$UV_VERSION` would make the weekly check follow a bad URL. Add `UV_INSTALLER_SHA256="<measured when pinning>"`, since no hash was computed in this audit. After the download and before `/bin/sh`, compare with the helper at :1655
  and `|| fail` with a message naming a changed or intercepted download; the exit stays 1. A committed hash needs the versioned URL, because the floating URL's bytes change with each uv release and would stop every new Mac. Update the two test literals and show the hash check in the dry run. install.sh:534 and docs/deploy/it-request.md:202-203 name `astral.sh` and `releases.astral.sh`, so the redirect stays on named hosts.
- Test: `test_a_changed_installer_is_refused` (through U-B9's `__AGENTSYNC_CURL` seam, rc 1, the script not run), and `test_the_pinned_url_is_literal_for_the_hosts_check`.

**C-9.3 K30 (R439, R437; R010 and R151 are dropped: install.sh has no `--yes` and no consent prompt).**
- Sites: install.sh:4, :11, :9192, :11222, :9298; README.md:1038; docs/deploy/README.md:87; docs/deploy/mdm/README.md:30.
- Correction: site :5 in the finding is :4. "Fuses consent with the action" overstates it: :9192 only sets `INSTALL_AGENT=1`. The "documented reason" is only partly documented: nothing says a distinct word guards an allow-listed agent, and README.md:666's rule `install.sh *` already admits it. The real controls are the prompt omitting the flag and the tests pinning that.
- Under Option A (rename only, no `--yes`): `--install-agent | --confirm-install-agent) INSTALL_AGENT=1 ;;`. Do not add `--yes`: install.sh has no prompt for it to answer (line 2), R151 says `--yes` answers confirmations and nothing more, and an argv-named install that `agentsync uninstall-agent` reverses fits R146's ordinary tier, where R158 forbids confirmation.
  1. In the argv loop (:1523-1545), map the old word to the new one for `ORIG_SHOWN` and `BASE_ARGS`, so NEXT stops teaching it. **This is the rename's trap:** setup_report.py:16455 `_without_folders` and :16474 `_again_words` compare `args=` word for word, so also treat both spellings as one word there, because install logs on disk hold the old one. Add a test beside tests/test_setup_report.py:13380.
  2. Add the old name to `INSTALL_OPTIONS` and `INSTALL_HIDDEN`, the repo's precedent.
  3. In the same commit change the guides, the emitted strings (loop.py:491, doctor.py:3250, :3255, launchd.py:409, setup_report.py:444, install.sh:9298) and their pins (tests/test_deploy_pack.py:4730, tests/test_launcher.py:629, tests/test_loop.py, test_ops_doctor.py, test_ops_launchd.py). `git grep -c` counts 173 lines in tracked files, origin.
  4. Any warning on the old spelling goes to stderr before the closing NEXT.
- Under Option B: no code; the R422 row in U-C13's table states plainly that the name is a social signal, not a guard.
- Test: `test_old_and_new_spelling_install_the_same`, and `test_setup_report_treats_both_spellings_as_the_same_command`.

U-C9 size: about 370 LOC at most (K27 110, K33B 70, K30 Option A 190); about 200 with K30 Option B.

#### C-10 `scripts/install.sh`: standard options (unit U-C10; after U-C9 and D21)

**C-10.1 K31 (R441, R451, R330, R061, R062, R073), and K34's `--timeout`.**
- Sites: install.sh:1565, :1577, :1586, :1588, :9190; tests/test_launcher.py:610-624, :647, :887; tests/test_deploy_pack.py:954, :2967; tests/test_install_oneshot.py:16411, :23818; setup_report.py:16455-16474; CONTRACTS.md:5661-5670.
- Evidence: the missing `--dry-run` and `--config` are a deliberate, documented contract. KISS K17 deleted both, and tests/test_launcher.py:622-624 pin that they exit 2. The finding never mentions K17, so its "compatible" is wrong for those two. `--timeout`, `--debug`, `--cwd` and `--non-interactive` are not pinned as deleted.
- Change, always (the recommended scope; D21's Option A keeps K17 for the other two):
  1. Leave the assignments at :1565, :1577, :1586 and :1588 byte-identical, because tests pin them as text. Override in the parse loop, so the option beats the environment variable, which beats the default.
  2. Handle each new option in the pre-scan (:1523-1545) with `skip_next` for a value, and keep them out of `ORIG_ARGS`, `ORIG_SHOWN` and `BASE_ARGS`, so the check at :9239 and `_again_words` stay right.
  3. `--timeout <DURATION>` (R451, R436; **Round 2 fix:** K34's verdict said `SECONDS`). Parse `30s`, `5m` and bare seconds in the option only; the variables stay whole seconds, because `2m` must remain a usage error (tests/test_install_oneshot.py:23818). It overrides the first-sync limit (U-B9), the first-run wait (180 s) and the lock wait (120 s), which stay separately settable below it. State the defaults in help. The README prompt forbids adding options to its own command (README.md:500); that binds the prompt, not the script, so no COMPAT bump.
  4. `--non-interactive`: an accepted no-op, because line 2 says no interactive prompts. `--debug`: redacted detail on stderr, and check what `start_capture` copies, because the report includes install.out. `--cwd <DIR>`: apply before the relative-path resolution at :9233 and the SOURCE resolution. Any other unknown option keeps exit 2.
- Change, only under D21's Option B (reverse K17 in one commit):
  - `--dry-run` keeps the pinned NEXT text when the variable set the dry run (tests/test_launcher.py:647, tests/test_deploy_pack.py:954), and says "re-run without --dry-run" only when the option did.
  - `--config` exports `AGENTSYNC_CONFIG` (src/agentsync/paths.py:29-34) and is carried in `RERUN` only when the option supplied it, so tests/test_launcher.py:887 still passes. Follow-ups such as `~/.local/bin/agentsync sync` must name the config too, or they silently read the default path.
  - Edit the gone-lists at tests/test_launcher.py:610-624 and the CONTRACTS sentence at :5661.
- Conflict recorded: R061 says globals are accepted at every depth, but `--log-start`, `--log` and `--list-folders` take no other option. They get an explicit carve-out in the header and U-C13's table.
- Test (`tests/test_install_parser.py`): `test_timeout_accepts_a_duration_and_rejects_a_word`, `test_option_beats_the_variable`, `test_new_options_stay_out_of_the_rerun`, `test_non_interactive_is_a_no_op`, and `test_cwd_applies_before_relative_paths`.

U-C10 size: about 200 LOC recommended, about 340 with D21's Option B.

#### C-11 `scripts/install.sh`: short help (unit U-C11; after U-C10 and D29)

**C-11.1 K43 (R087, R089, R091, R092, R095, R097, R067, R436, R093, R100).**
- Sites: install.sh:2-312, :1646; tests/test_install_oneshot.py:1067; tests/test_launcher.py:606-619; tests/test_deploy_pack.py:839 area (exit 0), :1058, :1478, :4161; CONTRACTS.md:20396.
- Evidence (measured at origin by replaying show_help's awk into /tmp/cli-bp/acs_help_d62.txt): 311 lines, 261 over 80 columns, 6 with `B13 round`. No Options or Examples heading.
- **Correction that sets the fix's shape.** The finding says moving comment text can change report redaction. That is backwards: the word-list awk skips comment lines (:2120). The risk is the reverse: a new `printf` format or double-quoted help string adds report words, and that awk has no heredoc handling. So help stays in comments. "Move the notes to docs/deploy" is also wrong: 10 in-script references, CONTRACTS.md:20396 and tests/test_deploy_pack.py:1478, :4161 rely on the header.
- Change: keep the whole header comment where it is and print only a short first section.
  1. Put the new help first in the header, as comment lines of at most 80 columns: Usage, a one-line imperative summary, Options, Examples, Notes. End it with a sentinel comment, `# -- end of help --`, followed by the existing notes as design comments.
  2. Change show_help (:1646) so its awk exits at the sentinel. It stays one awk over `$0`, exit 0.
  3. Options, alphabetical, listing those landed by U-B7 to U-C10 and adding no new ones: `--confirm-install-agent`, `--help` with `-h`, `--launcher <PATH>`, `--list-folders`, `--log`, `--log-start`, `--report-only`, `--source-local <DIR>`, `--version`. Usage writes `[--source-local <DIR>]...`, with the ellipsis right after the bracket (R093). Keep `<PATH>` for `--launcher`, because `AgentSyncLauncher.app` is a bundle directory, so `<FILE>` would be wrong. `AGENT`, `STEP`, `KIND`, `WHAT` and `FIX` become angle-bracketed nouns (R067). Defaults read `(default: VALUE)`.
  4. Examples, 2-3 jobs, one per line, indented, no `$` (R101, R105): `scripts/install.sh --list-folders`, `scripts/install.sh --source-local <DIR>`, and `AGENTSYNC_INSTALL_DRY_RUN=1 scripts/install.sh --source-local <DIR>`.
  5. Notes keep the literal `AGENTSYNC_INSTALL_DRY_RUN=1` and `AGENTSYNC_REBUILD_LAUNCHER=1` (tests/test_launcher.py:608-609), the exit status summary, the `--log-start 'prompt vN, TOOL'` form and the `--log` four-argument form with its KIND values (usage errors at :688 and :758 area point here), K47's `Terms:`, and one line saying the full notes are this file's comment header.
  6. Keep `--no-report`, `--log-end` and SOURCE out (tests/test_launcher.py:610-619).
- Re-point tests/test_install_oneshot.py:1067 to the header text, as `_installer_help` already reads it, or keep the persons sentence in Notes: **D29**. Leave tests/test_deploy_pack.py:1478 and :4161 as they are, because they read the full header, and correct the `_installer_help` docstring at :1058 ("what show_help prints"). Amend CONTRACTS.md:20396.
- Test (`tests/test_install_help.py`):
  - `test_help_fits_80_columns_and_sorts_options`.
  - `test_help_stops_at_the_sentinel` (no `B13 round` in stdout).
  - **R100, `test_every_help_example_parses`:** read the `Examples:` block from `--help` stdout, replace `<DIR>` with a tmp folder, run each under `AGENTSYNC_INSTALL_DRY_RUN=1` with an isolated HOME and stub tools, and assert no `usage error` in stderr, rc != 2, and the HOME listing unchanged. The pattern is tests/test_install_oneshot.py:8201. This is never a live mutation.
  - `test_every_listed_option_is_a_parsed_option` (against `INSTALL_OPTIONS["main"]`).
- Callers: no production caller runs or parses `--help` (verifier's grep). Usage errors send people to it (:1642, pinned at tests/test_install_oneshot.py:4312). Exit code stays 0.

U-C11 size: about 240 LOC (estimated: 65 src, 130 tests, 45 docs).

#### C-12 `scripts/tenant-probes.sh`: the exit and stream contract (unit U-C12; after U-B16, D10, D32, D33)

**C-12.1 K50, the rest (R173).** Under D32's Option A: on exit 0, `print(report)`, the bare path, so `x=$(scripts/tenant-probes.sh)` works, and update docs/deploy/tenant-probes.md:15 and docs/plans/implementation.md:76 in the same commit. Under Option B: keep `report: <path>` on exit 0 and record the R173 deviation. :290's dry-run text stays either way (tests/test_deploy_pack.py:854-890). Test: `test_success_stdout_is_the_bare_path` under Option A.

**C-12.2 K51 (R003, R017, R318).**
- Sites: tenant-probes.sh:30-31, :348-349, :369-376, :391-398, :432-436, :453, :459-460, :484-486, :491-497; docs/deploy/tenant-probes.md:24, :27.
- Evidence and correction: the rows stay in the report with exit 0. Stronger than the finding says: `status_of` catches `GraphError(0, "network")`, so a fully offline run exits 0 against :31's "1 any other failure". `NetworkPolicyError` subclasses `GraphError`, so :391, :432, :459 and :491 swallow it. And a stopped listing can make :453 report "no Office/PDF file". A 403 or 410 is a measurement (docs/deploy/tenant-probes.md:38), so "exit 1 when any probe could not run" as written would fail normal runs.
- Change, both options:
  1. Classify. Measured: HTTP 401, 403, 404, and 410 on probe 9. Incomplete: status 0, 429, 5xx, any other `GraphError`, a failed download, or a delta page with no deltaLink.
  2. Add rows: channels `skipped: joinedTeams returned <st>` or `skipped: no joined teams`; messages likewise; a line at :494-497 `not stored: response carried no deltaLink`.
  3. :453 and :436 say the listing did not complete when a drive was unreadable or stopped, rather than "no file" or "none" (R017).
  4. Add `except NetworkPolicyError: raise` before the handlers at :391, :432, :459 and :491, matching :344 and :482. This delivers the already documented exit 1 and is compatible.
  5. Print `<n> probes incomplete; see report` to stderr and add the count to the report. Stdout stays.
  6. Keep a link usable: :484-486 sets `link=None` on any non-policy `GraphError`, so a transient 0 or 5xx burns a one-shot milestone link. Keep it unless the response was a measurement.
- Option A (rule, recommended; no in-repo caller is affected): after `write_report`, `rc = 1 if rc == 0 and incomplete > 0`. Update :30-31, doc:24, :27 and docs/plans/implementation.md:76. Option B: keep `0 = report written`, and reword :31, which overstates what exits 1.
- Test (through the harness): `test_an_offline_run_is_not_exit_0` (Option A), `test_a_403_is_a_measurement_and_exits_0`, `test_a_network_policy_failure_is_exit_1`, and `test_a_missing_deltalink_says_so`.

**C-12.3 K55 (R337, R260; D10).**
- Part 1: under D10's Option B (recommended), keep 77 and 78 and add one line under the exit table in docs/deploy/tenant-probes.md: they match `EXIT_REAUTH` and `EXIT_CONFIG` (cli.py). Under Option A, exit 1 at :253, :267, :279, :312, :316 and :514, with reasons `login_required` and `invalid_config`.
- Part 2, either option: **do not change :122** (it also covers an auto-detect miss and a bad `AGENTSYNC_PYTHON`, which are not flag errors and stay 1). Add `PY_FLAG=1` in the `--python` arm, and after the loop: `if [ "$PY_FLAG" = 1 ] && { [ ! -f "$PY" ] || [ ! -x "$PY" ]; }; then usage_error "--python needs an executable file, got '$PY'"; fi`. `--python /bad --help` stays exit 0.
- Test: `--python /nonexistent` gives 2; `AGENTSYNC_PYTHON=/nonexistent` gives 1.

**C-12.4 K57 (R070, R074, R344).**
- Sites: tenant-probes.sh:52, :63, :90, :92-96, :221, :429, :287.
- Change, shell side, adding no option arm. U-B13's `set_value` already refuses an empty value, which covers `--config ''` (78 to 2), `--out ''` and `--python ''`. After the loop: `[ "$DRY_RUN$SELFTEST" != 11 ] || usage_error "--dry-run and --selftest cannot be combined; pass one"`. `--max-pages` and `--max-bytes` must be at least 1, with an all-zero pattern that also rejects `00`. Document `--max-downloads 0` as skipping probe 4b in the help and docs/deploy/tenant-probes.md:30, and make its empty row say that. Keep :96 and :255, which are the default path when the option is absent. R344 applies by analogy only (a sampling cap), but its "below 1" part is the useful one, and `--max-pages 0` reads a full page while :287 promises "at most 0 pages".
- Test: exit 2 for each rejected form; bare `--dry-run` and `--selftest` still exit 0.
- Callers: no found caller passes any of them (verifier's grep across the repo).

U-C12 size: about 295 LOC (estimated: K50 25, K51 130, K55 50, K57 90).

#### C-13 The exceptions record (R422) (unit U-C13; lands last)

- **Change:** add one table to CONTRACTS X.30: rule, reason, user impact, test, for every conflict where the repo or a caller won. R422 requires all four, and a rule that keeps needing exceptions gets revised.
- **Rows** (each present only if its decision kept the repo's contract): R337/R260 for agentsync, install.sh and tenant-probes (D10); R439 for `offboard --confirm` and `--confirm-install-agent` (D11); R062/R464 for `-v` (D12); R276/R205/R284/R285/R307 (D13); R016/R173/R177/R006 (K21, D14); R106-R108 (D16); R086 (D17); R024/R028/R030 (K29, D20); R441/R073/R061 (D21); R335 (D24); R137 (D26); R105/R236/R280 (K41, D28); R003 (K45, D31); R079 naming (D30); R173 (D32); R452/R062 (D35); R035 (D36); R217 (D38); R167/R170 (D39); R189 for the list's marks (K37); R046/R423 for the pinned "the agent" strings (K47); R151 for `purge --queue --yes` (C-2); R343 for `hold --list`, a short local list with no `--limit` (found checking the critic's "pagination is N/A" note).
- **Test:** `tests/test_contracts.py::test_every_exception_names_rule_reason_impact_and_a_test`: parse the table, and assert each row has an `R###`, non-empty reason and impact, and a test name that `git grep` (or a file scan) finds under tests/.
- Callers: none. About 130 LOC (estimated: 90 docs, 40 test).

#### Optional units, only if the named decision chooses Option A

These are sized, not recommended.

**C-14a K21, Option A (D14), about 450 LOC.**
- Do **not** retarget `say()`. `fallback_body` (install.sh:5261-5530) writes the shell-fallback report through `say` and is piped into redaction (:6054), so it would empty that report. A `say()` edit alone also misses child stdout, whose `[FAIL]` lines the prompt reads, and the dry-run printf in `run()`.
- Use an fd-level swap once, right after parsing and before `start_capture`, or a separate progress helper. Move everything except the `--list-folders` rows and the `--version` value to stderr, and keep NEXT at the end of stderr.
- Print the bare report path on stdout only on exit 0 (R176), before NEXT: separate tees give no merged order.
- Gate with `SETUP_PROMPT_COMPAT` 34 to 35, the mechanism that refuses a mismatched prompt; pyproject is 0.1.0, so "a major version" alone gates nothing. Keep the R388 switch `AGENTSYNC_COMPAT_STDOUT=1`.
- Reword README.md:526 for an agent that backgrounds with a stdout-only redirect. Rewrite `last_line` (tests/test_install_oneshot.py:418) and the pins at :403, :498, :548, :551, :16443.

**C-14b K41, Option A (D28), about 350 LOC.** Emit the sentence ending in `:`, then a 4-space-indented command line. Teach install.sh:5142, :11169 (the status NEXT relay), setup_report.py:6805 and :6889 (`_AGENT_FIX_RE`, which needs the command inline), :6841 (`_PERSONS_STEP`), and :16029 to read the following line; these silently lose data if missed, while `one_next` would still pass. Rewrite the header at install.sh:228 area, README.md:448-628, skill.py and CONTRACTS.md:5289, and change the 60 `last_line(cp)...NEXT` pins (measured with `grep -cE`) and the 5 `[-2]` pins. Under Option B (recommended) there is a wording-only cleanup: when a NEXT carries a command, end the line with it after `run: `, as :1634 and :7797 do; :11199's trailing prose and `; either way, ` (pinned by 3 test lines) change with their tests.

**C-15 K67, Option A (D38), about 150 LOC.** `text.plural` (text.py) gains `{n:,}`, and the run line's `converted`/`deferred` counts stay raw or the parsers change with them: install.sh:10796 `SYNC_SUMMARY_RE='converted [0-9]+, deferred [0-9]+ online-only'` is a live parser, and `_SPLIT_RE` (cli.py comments) and setup_report re-read old install.out files. Census first: `git grep -nE '\[0-9\]\+|\\d\+' -- scripts/install.sh src/agentsync/setup_report.py`.

**C-16 K32, Option A (D10), about 400 LOC.**
- Map :9107, :9111, :9118, :9170, :11228 and :11235 to exit 1, keeping each NEXT, `step_end` and `note=` word. Change only :9174 (provider present, zero folders) to exit 0, and keep its install.log row as `failed note=no-folders` so `_list_stopped` (setup_report.py:12929) still reports it, or update setup_report in the same change. :9170 is a missing parent, which R194 says is not success, so it maps to 1, not 0.
- Exit 2 at :11199: either refuse before step 1 only when no config exists and no `--source-local` was given, exempting or re-documenting docs/deploy/mdm/README.md:30's invocation, or (simpler) exit 0 with the same NEXT. Re-pin tests/test_install_oneshot.py:1316.
- Update `rc_meaning`'s `3)` arm (:1768), 60 `returncode == 2|3|4` assertions (measured), tests/test_deploy_pack.py, README.md:1028-1031, docs/deploy/README.md and docs/deploy/setup-feedback.md. `AGENTSYNC_COMPAT_EXIT=1` restores 3, 4 and 2 exactly (R388).
- The prompt's branches are unchanged: they read printed paths and NEXT words, not numbers.
- agentsync's own 65/75/77/78/79 are not sized here: launchd's retry and the Swift launcher read them, so Option A there needs its own audit first.

**C-17 K29 and K24, Options A (D20, D17), about 430 LOC.**
- K29: normalize argv once, before the first-argument dispatch (:1492) and the `ALL_ARGS` loop (:1523), so everything downstream sees the old spelling: `folders list` to `--list-folders`, `report write` to `--report-only`, `friction start AGENT` to `--log-start AGENT`, `friction add --step S --kind K --what W --fix F` to `--log S K W F`. Normalizing first is what keeps the `# run=` header, `ORIG_ARGS`, the `list-folders` step, `_LIST_RUN_RE` (setup_report.py:4126), the awk at :2390, `_PROMPT_STOP_RE` and redaction byte-identical. As worded, the finding would break redaction. Keep old spellings as permanent aliases. A deprecation warning goes to stderr, never on the `--log-start` stop path, because CONTRACTS.md:8464 pins "the one error line", and only once the prompt uses the new spelling. That means a COMPAT bump, which stops every saved copy at step 1: the real cost.
- K24: `--version` prints one value, and a new `--prompt-compat` prints the number. README.md:362, :423-425, :439 bump to v35. Update tests/test_deploy_pack.py's prompt pins, the output tests at tests/test_install_oneshot.py:496, and docs/deploy/setup-feedback.md (pinned by tests/test_deploy_pack.py:10099). Saved v34 and older copies fail closed at "printed another number, copy the prompt again".

---

## 5. Dropped

- **Exit codes 65/75/77/78/79 (R337). Round 2 fix: no longer dropped.** Round 1 called the 0/1/2/130 set "the Vercel
  guide's house choice", but the checklist generalizes R337 and Reference L and its exclusion list does not name them.
  The repo does have a documented reason for its contract: sysexits codes are documented in `--help`, and consumed
  by install.sh, setup_report, tenant-probes, the Swift launcher and launchd's retry. So the repo's contract and its
  callers win for now, the conflict is recorded, and **D10** holds both options. Two optional residuals remain: argv's
  unknown source id exits 78, not 2 (cycle.py:7173), and add-source exits 2 for an unloadable sources.toml.
- **System side effects in sync/install (R354).** Refuted. The Time Machine exclusions cover only agentsync's own
  data, are documented in CONTRACTS ("called by `init` and after each CLI cycle"), and can be turned off with
  `AGENTSYNC_TM_EXCLUDE=0`. Recording the gitleaks path installs nothing.
- **K16 `-v`/`-d` (R464, R062). Round 2 fix: no longer dropped.** Reference B and G are generalized rules, not Vercel
  specifics. `-v` for verbose is pinned (tests/test_contracts.py:52, :159), so the repo's contract stays for now. No script
  passes `-v` (measured: `git grep` of src, scripts, launcher, README.md and docs/deploy hits only its definition). **D12**
  holds both options, together with K24's and K58's `-v`.
- **K16 metavars `ID`/`BYTES` (R436). Round 2 fix.** `BYTES` is a noun and stays. `N` changes, and so does `ID` on
  `--source` (R434), and every visible hint gets angle brackets. See B-1.5.
- **K16 R099.** Not violated: printed recovery uses long canonical spellings.
- **K5 rename `--confirm` to `--yes` (R439). Round 2 fix: no longer dropped.** R439 is a generalized rule, so "Vercel's
  naming table" is not a ground. The repo's reason stands on its own: `offboard --confirm <DOCS_REPO>` makes the operator type
  the target path, which meets R146's "target in argv" and R150's exact-match idea in one word, and governance's
  `confirm=` (governance.py:~6713) and docs/deploy/data-governance.md:282-283 use it. **D11** holds both options.
- **K4 "compact-history force-pushes".** False: it has no `--push`. Only `purge --push` force-pushes, and it is
  already gated by `[governance] allow_remote` and a tenant-owned remote.
- **K7 R064.** Not applicable: there is no `parse_known_args`.
- **K1 R136.** Not applicable: login has no `input()` prompt. It is covered by R177.
- **K19 parts.** Putting `AGENTSYNC_NO_NEXT_HINT` in help and renaming the installer variables to `__AGENTSYNC_*`
  are dropped: they are internal, and R079 keeps legacy names. **Round 2 note:** K44's verdict reads R079 more narrowly: its
  exemption names SDK variables that predate the rule, and it says internal and test-only variables take the `__` prefix.
  Help no longer shows them (B-8.1). The rename itself is **D30**, not a drop.
- **K18 `✗` glyph (R276) and `12.5K` abbreviation (R217). Round 2 fix: no longer dropped as house style.**
  - The excluded list keeps the class formulas, and R205 makes `✗` the rich form with `Error:` as the plain form. The repo's
    reason is that `error:`, `state:`, `configuration error:` and ` (fix: ` are parsed (C-7). **D13** holds both options,
    across agentsync, install.sh (K42) and tenant-probes (K62).
  - R217's `12.5K` is a ceiling, not a requirement: "abbreviated only in summaries from 10,000". Nothing abbreviates today
    (measured: `plural` in text.py prints the raw count), so that part needs no change.
  - R217's "commas from 1,000" is a real gap: **K67, D38**.
- **K9 literal `Canceled.` / `Stopped waiting.` strings and `data.remote`.** Adapted, not copied. The `interrupted`
  substring is kept for tests/test_cli.py:9497. **Round 2 note:** this holds for human output. `--json` is new and has no
  caller to protect, so its document uses `reason: "canceled"` and `data.remote` (R262; B-5 table).
- **K15 "move truststore injection into handlers".** It violates C15 req 32 and tests/test_cli.py:1915.
- **K14 "use SingleWriterLock".** It contradicts `_record_lock`'s documented design (no hold refused while a sync
  runs). A separate flock is used instead.
- **K12 "stdin or stderr not a TTY".** It would break the documented `graph login 2>&1 | tee` recipe. Only stdin
  is tested.
- **K6 "record_unreadable, status included".** Overstated: status catches its own read errors.

**Round 2 notes on this section.**
- **"Vercel-specific": none left.** The rows above that gave that ground now point to D10-D13. The other rows stand on
  evidence (not applicable, or refuted by a caller).
- **Parts of K21-K64 the verifiers refuted**, each noted in its row: R371 on K33 (an upgrade rule); R010 and R151 on K30; R023 on
  K25; R063 on K56; R192 on K37; R339 on K49; R353 and site :1596 on K40; R304 on K42 and K62; R318 on K45.
- **Critic entries refuted or narrowed here:**
  - R020, R165, R300, R315, R317 on the Graph client hold. `graph/client.py` sends only GET and retries only GET hops
    (measured: `git grep -nE '\.(post|put|patch|delete)\('` on src/agentsync hits no HTTP call; `_request` is "One GET hop"),
    so R415 holds too. The one non-idempotent network call is `purge --push`, which never retries but has K70's failure path.
  - R117: no agent detection exists (see C-5).
  - R169: the refresh lock exists (see C-5).
  - R119: agentsync reads no stdin today.

---

## 6. Implementation sizing

LOC is **estimated** by counting each cited site times a typical edit size, plus tests and CONTRACTS text. Nothing
was compiled or run.

Updated in round 2 by the same method. Each figure is the round 2 estimate, with round 1's in parentheses.

| Wave | Units | Est. LOC |
|---|---|---|
| Prep | U-0 | ~65 (~40) |
| A | U-A1a 285, U-A1b 300, U-A2 260 | ~845 (~470) |
| B, round 1 units | U-B1a 340, U-B2 395, U-B3 140, U-B1b 380, U-B4 475, U-B5 390, U-B6 ~5 | ~2,125 (~1,895) |
| B, new in round 2 | U-B7 375, U-B8 210, U-B9 430, U-B10 280, U-B11 310, U-B12 395, U-B13 380, U-B14 370, U-B15 425, U-B16 240, U-B17 295 | ~3,710 |
| C, recommended scope, round 1 units | U-C1 380, U-C2a 260, U-C2b 420, U-C3 170, U-C4a 470, U-C4b 420, U-C5 280, U-C6 option (b) 110 | ~2,510 (~1,970) |
| C, recommended scope, new in round 2 | U-C1b 410, U-C9 370, U-C10 200, U-C11 240, U-C12 295, U-C13 130 | ~1,645 |
| **Total, recommended** | | **~10,900 (~4,375)** |
| C, optional if approved | U-C6 option (a) +260, U-C7 ~550, U-C8 ~600, U-C8b ~300, U-C10 Option B +140, U-C14a ~450, U-C14b ~350, U-C15 ~150, U-C16 ~400, U-C17 ~430 | +~3,630 (total ~14,530) |

- Every unit is under 500 LOC. The largest are U-B4 (~475) and U-C4a (~470). If U-B4 runs over, move `--version --json` to U-B5.
- Scheduling (estimated): the recommended scope is 34 landings. ship-land serializes them, and each land waits on CI (18 to
  62 minutes, plus any runner wait), so CI alone is about 10 to 35 hours before any implementation time.

### Implementer units (each under 500 LOC; files exclusive while the unit is active)

| Unit | Owns (exclusive) | Depends on | Runs in parallel with |
|---|---|---|---|
| U-0 reservation (docs-only) | CONTRACTS.md: one new §16.N "CLI conventions" with stub subsections X.1 to X.30 (one per unit; Round 2 fix: round 1 had X.1 to X.12) and stub module sections for `agentsync.clio` and `agentsync.fsutil` | round 36's reservations landed | nothing; lands first, alone |
| U-A1a (Round 2 split of U-A1) | src/agentsync/clio.py (new), src/agentsync/text.py, tests/test_clio.py (new), CONTRACTS subsection X.1 and the clio module section | U-0 | U-A2, U-B1a, U-B2 |
| U-A1b | src/agentsync/clio.py, tests/test_clio_json.py (new), CONTRACTS X.1 | U-A1a | U-B7, U-B3 |
| U-B1a | src/agentsync/cli.py, tests/test_cli_parser.py (new), tests/test_cli.py (one re-pin at :9244), CONTRACTS X.2 | U-0 | U-A1a, U-A2, U-B2 |
| U-B2 | src/agentsync/fsutil.py (new), governance.py, skill.py, gitops.py, curate.py, tests/test_fsutil.py (new), tests/test_governance_holds_lock.py (new), tests/test_governance_push.py (new, K70), CONTRACTS X.3 and the fsutil module section | U-0 | U-A1a, U-A2, U-B1a, U-B3 |
| U-B3 | graph/auth.py, graph/discover.py, convert/vtt.py, cycle.py, scripts/tenant-probes.sh, tests/test_cli_text_hygiene.py (new), CONTRACTS X.4 | U-A1 | U-B2, U-B1b |
| U-B1b | cli.py, tests/test_cli_conventions.py (new), tests/test_cli_streams.py (new), CONTRACTS X.5 | U-A1, U-B1a, U-B2 | U-B3 |
| U-B4 | cli.py, tests/test_contracts.py, tests/test_cli_json.py (new), README.md, CONTRACTS X.6 | U-B1b, plus the K8 decision | U-B6 |
| U-B5 | cli.py, tests/test_cli_json.py, CONTRACTS X.6 | U-B4 (same release) | U-B6 |
| U-B6 | none (measurement; one CONTRACTS line X.7 if closing) | none | any |
| U-C1 | cli.py, tests/test_cli.py, tests/test_contracts.py, tests/test_cli_parser.py, CONTRACTS §16.10 and X.8 | U-B5 | none (cli.py) |
| U-C2a | cli.py, loop.py, cycle.py, governance.py, setup_report.py, the pinned tests (census in C-2), tests/test_contracts.py, README.md | U-C1, U-B2, U-B3 (governance/cycle free) | none |
| U-C2b | cli.py, tests/test_cli_consent.py (new), tests/test_loop.py, CONTRACTS X.9 | U-C2a | none |
| U-C3 | cli.py, setup_report.py, tests/test_cli.py (3 tests), tests/test_cli_streams.py, CONTRACTS X.10 | U-C2b | none |
| U-C4a | cli.py, clio.py (legacy switch), scripts/install.sh (two env prefixes), tests/test_cli.py re-pins, CONTRACTS X.11 | U-C3, plus the K1 decision | U-C4b (same branch, one landing commit) |
| U-C4b | the stdout re-pins outside test_cli.py (test_loop, test_review_fixes, test_e2e, test_deploy_pack, as the affected gate reports them), README.md | U-C4a's diff | U-C4a |
| U-C5 | graph/auth.py, cli.py, tests/test_cli.py (login fixture), tests/test_graph_auth.py, tests/test_contracts.py, docs/deploy/*.md, CONTRACTS X.12 | U-C4 | none |
| U-C6 | option (b): cli.py, docs/deploy/data-governance.md, tests. Option (a) adds manifest.py and loop.py | U-C5, plus the K6 decision | none |
| U-C7, U-C8 | only if approved; each splits into its two commits, as in section 4 | decisions | none |
| U-A2 (Round 2) | scripts/install.sh, tests/test_install_streams.py (new), CONTRACTS X.13 | U-0 | U-A1a, U-B1a, U-B2 |
| U-B7 | scripts/install.sh, tests/test_install_parser.py (new), tests/test_contracts.py (`INSTALL_OPTIONS` only), docs/deploy/README.md, docs/deploy/setup-feedback.md, CONTRACTS X.14 | U-A2 | U-A1b, U-B3 |
| U-B8 | scripts/install.sh, tests/test_install_help.py (new), tests/test_install_oneshot.py (one re-pin at :20098), docs/design/glossary.md (new), CONTRACTS X.15 and the sentence at :16334 | U-B7 | U-B1b, U-B13 |
| U-B9 | scripts/install.sh, tests/test_install_bounds.py (new), README.md:526, CONTRACTS X.16 | U-B8 | U-B5, U-B15; not U-B4, which holds README.md |
| U-B10 | scripts/install.sh, tests/test_install_signals.py (new), CONTRACTS X.17 and the sentences at :9772 and :27010 | U-B9 | U-C1, U-B16 |
| U-B11 | scripts/install.sh, src/agentsync/setup_report.py, tests/test_install_list.py (new), tests/test_contracts.py, CONTRACTS X.18 | U-B10 | U-C12 |
| U-B12 | scripts/install.sh, tests/test_install_json.py (new), tests/test_contracts.py, CONTRACTS X.19 | U-B11, D15 | the tenant-probes chain |
| U-B17 | scripts/install.sh, tests/test_install_json.py, tests/test_contracts.py, CONTRACTS X.20 | U-B12, D16, D17 | the tenant-probes chain |
| U-B13 | scripts/tenant-probes.sh, tests/test_tenant_probes.py (new), docs/deploy/tenant-probes.md, CONTRACTS X.21 | U-B3 | U-B1b, U-B8 |
| U-B14 | scripts/tenant-probes.sh, tests/test_tenant_probes.py, docs/deploy/tenant-probes.md, CONTRACTS X.22 | U-B13 | U-B4 |
| U-B15 | scripts/tenant-probes.sh, tests/test_tenant_probes.py, docs/deploy/tenant-probes.md, CONTRACTS X.23 | U-B14, D34 | U-B5, U-B9 |
| U-B16 | scripts/tenant-probes.sh, tests/test_tenant_probes.py, docs/deploy/tenant-probes.md, CONTRACTS X.24 | U-B15 | U-C1, U-B10 |
| U-C1b | src/agentsync/cli.py, tests/test_cli_json.py, tests/test_contracts.py, CONTRACTS X.25 | U-C1, U-B5 | none |
| U-C9 | scripts/install.sh, tests/test_install_parser.py, tests/test_contracts.py, tests/test_deploy_pack.py, tests/test_launcher.py; with K30 Option A also src/agentsync/setup_report.py, loop.py, ops/doctor.py, ops/launchd.py, tests/test_setup_report.py, tests/test_loop.py, the two ops test files and the guides; CONTRACTS X.26 | U-B17, D11, D22 | U-C2b, but not with K30 Option A |
| U-C10 | scripts/install.sh, tests/test_install_parser.py, tests/test_contracts.py, tests/test_launcher.py, CONTRACTS X.27 | U-C9, D21 | U-C3 |
| U-C11 | scripts/install.sh, tests/test_install_help.py, tests/test_install_oneshot.py (:1067), tests/test_deploy_pack.py (one docstring), CONTRACTS X.28 and the sentence at :20396 | U-C10, D29 | any unit not holding those |
| U-C12 | scripts/tenant-probes.sh, tests/test_tenant_probes.py, docs/deploy/tenant-probes.md, docs/plans/implementation.md, CONTRACTS X.29 | U-B16, D10, D32, D33 | U-B11 |
| U-C13 | CONTRACTS X.30, tests/test_contracts.py | every decision | nothing; lands last |
| U-C8b, U-C14a, U-C14b, U-C15, U-C16, U-C17 | only if approved; each owns what its C-section row names, and they run one at a time | their decisions | none |

**Round 2: what "disjoint" means here.** No two active units share a file. Three files stay single-owner chains, and each
owner holds the file exclusively from fire to land: `src/agentsync/cli.py`, `scripts/install.sh` (11,249 lines at origin, measured
with `wc -l`) and `scripts/tenant-probes.sh`. New install.sh and tenant-probes tests go in new files, not in
tests/test_install_oneshot.py or tests/test_deploy_pack.py, which is what keeps these chains disjoint from the cli.py chain.

**Cross-chain holds.** A file used by more than one chain has one holder at a time. The landing table below already keeps these
apart:
- tests/test_contracts.py: U-B4, U-C1, U-C1b, U-C2a, U-C5, U-C13 and U-B7, U-B11, U-B12, U-B17, U-C9, U-C10.
- src/agentsync/setup_report.py: U-B11, U-C2a, U-C3, plus U-C9 under K30 Option A.
- src/agentsync/governance.py: U-B2, then U-C2a.
- README.md: U-B4, U-B9, U-C2a, U-C4b.
- docs/deploy/tenant-probes.md: the tenant-probes chain, then U-C5.
- tests/test_install_oneshot.py: U-B8, U-C2a, U-C11, one or a few lines each.
- tests/test_deploy_pack.py: U-C2a, U-C4b, U-C9, U-C11.

### Order of landing

1. U-0
2. U-A1, U-B1a and U-B2, in parallel (disjoint files)
3. U-B3 and U-B1b
4. U-B4, then U-B5
5. U-C1
6. U-C2a, then U-C2b
7. U-C3
8. U-C4 (a and b together)
9. U-C5
10. U-C6, then any approved U-C7 / U-C8

Every unit that touches `cli.py` waits for the previous one to land and rebases onto it. CONTRACTS.md is shared
only through U-0's separate stub subsections, so each unit edits only its own hunk.

**Round 2 landing order.** This replaces the numbered list above for the wider scope. A step's cells run in parallel; files are
disjoint within a step; each column is serial.

| Step | cli.py chain | install.sh chain | tenant-probes chain |
|---|---|---|---|
| 1 | U-0, alone | | |
| 2 | U-A1a, U-B1a, U-B2 | U-A2 | |
| 3 | U-A1b | U-B7 | U-B3 |
| 4 | U-B1b | U-B8 | U-B13 |
| 5 | U-B4 | | U-B14 |
| 6 | U-B5 | U-B9 | U-B15 |
| 7 | U-C1 | U-B10 | U-B16 |
| 8 | | U-B11 | U-C12 |
| 9 | U-C1b | | |
| 10 | | U-B12 | |
| 11 | U-C2a | | |
| 12 | | U-B17 | |
| 13 | U-C2b | U-C9 (not beside U-C2b under K30 Option A) | |
| 14 | U-C3 | U-C10 | |
| 15 | | U-C11 | |
| 16 | U-C4a and U-C4b, one commit | | |
| 17 | U-C5 | | |
| 18 | U-C6, then any approved U-C7, U-C8, U-C8b, U-C14a, U-C14b, U-C15, U-C16, U-C17, one at a time | | |
| 19 | U-C13, alone | | |

U-B6 is the first step of U-B1a's session, as in Phase 0. The B13 rounds keep moving trunk, so any step can be delayed by a
rebase; D36 must be decided before U-B5 fires, and D15 before U-B12.

### Operator decisions (summary)

1. **Schedule against B13.** The B13 rounds edit cli.py, install.sh, governance.py and setup_report.py
   continuously: 391 commits in about 10 days, measured with `git rev-list --count`. Decide whether this work
   runs as a lane with reserved §16.N sections or between rounds.
2. **K1.** What the bare stdout result is for each mutation (run id or sha for sync). Keep the
   `AGENTSYNC_COMPAT_STDOUT` switch for install.sh, or rewrite install.sh's parsers now.
3. **K4. Round 2 fix.** The plan's default flag is now `-y, --yes` (R439); the alternative is D11. Also accept that the bare
   `purge --queue` now previews, asks on a terminal, and otherwise exits 2.
4. **K6.** Reverse CONTRACTS K12 so read-only and dry-run commands stop migrating (option a), or announce the
   migration (option b).
5. **K8.** Commit to a public `--json` contract on every read and mutation command (R243 does not allow half of
   them).
6. **K18.** Whether to change the parsed headline prefixes (`configuration error:`, `state:`, ` (fix: `) at all.
7. **K16.** Whether to adopt noun-verb nested commands against the KISS K18/K19b frozen surface. The
   recommendation is to defer.
8. **K15.** Only if import-time measurement is over 200 ms, since the fix reverses rulings B67/A91.
9. **K12 (minor).** Whether to detect agent harnesses through environment variables, and whether to add a global
   `--non-interactive` (Round 2 fix: not `--no-input`; see D12).

### Needs decision (round 2)

Rulings (2026-11-07):

- D14 = Option B. install.sh stdout stays byte for byte; machines use `--json` (U-B12). C-14a is not built. The exceptions
  row stays U-C13's job (R016/R173/R177/R006).
- D28 = Option B. NEXT stays one line; the optional wording cleanup stays allowed. C-14b is not built. The exceptions row
  stays U-C13's job (R105/R236/R280).
- D27 = Option A. `tighten_setup_tree` cannot fail a run, and the end of a run re-checks and warns once. Built on trunk with
  this line (`warn_setup_tree_open`), so U-B10 skips B-10.2's step 4.
- D24 = Option B. A second stop still finishes the report, with the code kept at `on_exit`, and one stderr line says so before
  the report. Built on trunk with this line, so U-B10 does not add it. The exceptions row stays U-C13's job.
- K6 = option (b). Read-only commands and dry runs keep migrating (CONTRACTS K12). The existing warning is the one stderr line,
  now pinned by `test_status_migration_is_announced`, and data-governance.md's sentence is corrected. Built on trunk with this
  line; purge's `--dry-run` help already says it. U-C6 has no other work under this ruling.
- D15 = Option B. `--json` ships on `--list-folders` first; an install run and `--report-only` answer usage_error until decided.
  U-B12 builds it, and this ruling clears its D15 hold.

These are the conflicts where a generalized rule meets a repo contract or a caller. Until the operator decides, **the repo rule and
caller compatibility win, and the guideline rule stays recorded**, never dropped (U-C13). Option A follows the rule. Option B keeps the repo's or
callers' contract. The Default column is a recommendation, not a decision. It follows the verifier's verdict where the verdict gave one (D32, D34,
D37, D38 and D39 are this plan's suggestion).

| # | Question | Option A (rule) | Option B (repo or caller) | Default | Units |
|---|---|---|---|---|---|
| D10 | Exit codes (R337, R260, Reference L): agentsync 65/75/77/78/79; install.sh 3 and 4, exit 3 on an empty list, exit 2 after work (K32); tenant-probes 77 and 78 (K55) | Map to 1 with reason codes, an empty list exits 0, and `AGENTSYNC_COMPAT_EXIT=1` restores the old codes. install.sh and tenant-probes are sized (C-16, C-12.3). agentsync needs its own audit, because launchd's retry and the Swift launcher read its codes | Keep the codes, and record the exception citing `--help`, setup-feedback.md:1501 (a field failure where exit 1 read as "not signed in") and CONTRACTS.md:3875. Reasons reach machines through `--json` | B | U-C12, U-C13; U-C16 if A |
| D11 | Consent spelling (R439, R146, R150, R151): `offboard --confirm DOCS_REPO` (K5), `--confirm-install-agent` (K30), and C-2's new flag | `offboard --yes <DOCS_REPO>` and `--install-agent`, with old names as hidden aliases (R385). C-2 uses `--yes`. K30's cost is C-9.3's setup_report and pin work | Keep the two existing spellings and record them: the typed path is a real severe-tier check, and the prompt omits the install-agent flag by test. State plainly that the install-agent name is a social signal, not a guard. C-2 still uses `--yes` unless the operator wants one word across the CLI, which then adds a new `--confirm` | C-2: `--yes`. Existing two: B | U-C2a, U-C2b, U-C9, U-C13 |
| D12 | `-v` and globals (R062, R464, R466, R086, Reference B and G): agentsync's `-v/--verbose`; K24's and K58's `-v`; `-d/--debug`, `--cwd`, `--non-interactive`, `--no-color` on agentsync | `-v` is version everywhere and `-d, --debug` replaces `-v`/`-vv`. R387 requires removing `-v` first so old scripts fail loudly. Add the missing globals. Every `CLI_SURFACE` entry changes | Keep `-v/--verbose` (pinned, tests/test_contracts.py:52, :159), and record it. Leave `-v` unassigned in install.sh and tenant-probes, so one letter does not mean two things across the product (R464). Add `--non-interactive` only if C-5's decision wants it | B | U-B13, U-B17, U-C5, U-C13 |
| D13 | Error layout (R276, R205, R284, R285, R307): agentsync (K18, C-7), install.sh (K42), and warnings | Rich `✗`, plain `Error:`, the class formulas, `Couldn't`, in one coordinated change. `_QUOTED_LOG_TIME_RE` (setup_report.py:12113) must accept both cases, or the report starts showing local time (tests/test_setup_report.py:723). Parsers keep reading old install.out lines. NEXT strings the prompt branches on stay | Keep `error:`, `warning:`, `usage error:`, `could not` and ` (fix: `, because they are parsed, and record it. tenant-probes (K62) has no parser and adopts the formulas now | B; C-7's step 1 only if at all | U-C7, U-C13 |
| D14 | K21: install.sh's streams (R016, R173, R177, R006) | C-14a: progress to stderr, a bare report path on stdout, a COMPAT bump, `AGENTSYNC_COMPAT_STDOUT=1` | Keep stdout byte for byte, record it, and give machines `--json` | B | U-B12, U-C13; U-C14a if A |
| D15 | K22: `--json` on an install run (R244 against the heartbeat that keeps coding tools from stopping a silent command, install.sh:211) | Add a mutation document with `outcome` and `next` now; a `--json` install run is silent, and callers must allow for it | Ship `--json` on `--list-folders` first; the install run and `--report-only` answer usage_error until decided | B | U-B12 |
| D16 | K23: machine help on a one-command installer (R106-R108 against the frozen-surface policy, tests/test_contracts.py:107, KISS K17) | B-17.2 | No code; record the deviation, with the tested header comment as the help contract | A for R106; the operator's call on R107, R108 | U-B17, U-C13 |
| D17 | K24: `--version` prints two lines (R086 against README step 1, which reads the last line) | C-17: one value, plus `--prompt-compat`, a COMPAT bump, saved copies fail closed | B-17.1: plain output byte-identical, recorded, JSON form added | B | U-B17; U-C17 if A |
| D18 | K25: a report on a usage error (R015, R064 against the documented report-at-usage-error contract, CONTRACTS.md:9769) | No durable write on any usage error; rewrites the tests at tests/test_install_oneshot.py:2402, :16814 and the docs, and a bad folder's field report is lost | B-7.1: a stray SOURCE writes nothing; `--source-local`, `--launcher` and env-value errors keep the report | B | U-B7 |
| D19 | K26: argv parsing (R063 against the pinned literal case arms) | A real option table; rewrite `test_install_options_are_frozen` and the K17 note to pin the table | B-7.2: literal arms with one pass, accepting that the shell parser is not metadata-driven | B | U-B7 |
| D20 | K29: command grammar (R024, R028, R030, R066) | C-17: `folders list`, `report write`, `friction start`, `friction add`, normalized first; the real cost is a COMPAT bump | Keep the spellings and record it, matching C-8's deferral. The caller is not one prompt: saved copies, guides and printed fix strings too | B | U-C13; U-C17 if A |
| D21 | K31: `--dry-run` and `--config` (R441, R073 against KISS K17, CONTRACTS.md:5661, tests/test_launcher.py:622-624) | Reverse K17 in one commit: C-10's second block | Keep K17, and record it. `--timeout`, `--debug`, `--cwd`, `--non-interactive` still land, since K17 never covered them | B | U-C10, U-C13 |
| D22 | K33: pin uv (R313's integrity half; no repo reason either way) | C-9.2: a versioned literal URL plus a committed SHA-256. New Macs get a fixed uv and bumps are manual. Two tests change | Keep the floating official installer and ship Part A, recording that integrity rests on HTTPS | No default: owner's call | U-C9 |
| D23 | K34: a time limit on uv (R313 against `--force` deleting the environment before building) | New code that signals the whole holder chain, with tests | Keep uv under the caller's limit, as documented in the header | B | U-B9 |
| D24 | K35: a second Ctrl+C (R335 against B13 round 21 Y16, CONTRACTS.md:27006, which keeps the report finishing) | In on_exit, an INT after an earlier stop prints NEXT and exits 130. bash runs no trap inside the report's `$( )`, so a truly immediate exit also needs the report moved to a background job | Keep :7604-7606, record it citing the 120 s cap, and print one line before the report saying it is being finished | B | U-B10, U-C13 |
| D25 | K36: `--list-folders` exit when its reader closes (R341 against the documented 3 and 4) | Force exit 0 for a closed reader, which needs a stdout-gone signal back from the tee and hides a denied state | Keep 0, 3 or 4 as documented | B | U-A2 |
| D26 | K38: `--list-folders` writes state (R137 against redaction input) | A new opt-in no-write spelling only; writing nothing by default would turn reports private-only and is rejected | B-8.3: declare the writes | B | U-B8, U-C13 |
| D27 | K40: `tighten_setup_tree` is silent (R017 against CONTRACTS.md:9772) | Keep "cannot fail a run", drop "prints nothing", warn once after a re-check, and amend the contract in the same commit | No code; record it | A (the owner-only promise is printed by `--help`, and nothing else would reveal a failure) | U-B10 |
| D28 | K41: NEXT layout (R105, R236, R280 against scripts/install.sh:228, CONTRACTS.md:5289 and the parsers) | C-14b: a sentence, then the command on its own indented line | Keep one line, record it, and add the optional wording cleanup. No `--json` half | B | U-C13; U-C14b if A |
| D29 | K43: the persons sentence in `--help` (R097 against B13 round 18 V53, tests/test_install_oneshot.py:1067) | Keep it in Notes, about 9 lines at 80 columns (estimated from 6 lines of about 110), so the test stays unchanged | Re-point the test to the header, keeping the wording check and dropping the promise that `--help` prints the list | B, but it reverses V53, so it is the owner's call | U-C11 |
| D30 | K44: rename seams to `__AGENTSYNC_*` (R079 naming half) | Read new then old; change scripts/gate.sh:64 to `grep -E '^(__)?AGENTSYNC_'` so the cache salt sees them; export both for `AGENTSYNC_NO_NEXT_HINT`, unset both, and route cycle.py:470 through loop.py:97's constant; about six test files, tests/test_install_oneshot.py:8188, :8196, :16411, and CONTRACTS.md:5625. A rename does not address `AGENTSYNC_LAUNCHCTL` swapping a binary, which needs its own guard | Part A only (U-B8), recording the naming half as a deviation | B | U-C13 |
| D31 | K45: exit 0 with an optional helper unbuilt (R003 against CONTRACTS.md:24537, V6) | Exit 1 with a partial report, for a build that exited non-zero only. Needs a partial outcome in setup_report (:3672, :3151), new prompt step 2 text and a COMPAT bump. A Mac with old Swift would read "failed" on every run | B-8.4: exit 0, warn | B | U-B8, U-C13 |
| D32 | K50: tenant-probes' success stdout (R173 against docs/deploy/tenant-probes.md:15, docs/plans/implementation.md:76) | The bare path on exit 0; two docs change; no code caller blocks it | Keep `report: <path>`, and record it | A (nothing parses the line) | U-C12 |
| D33 | K51: tenant-probes exit 0 with incomplete probes (R003, R017, R318) | Narrowed: exit 1 when a probe is incomplete, never for a measured 401, 403, 404 or 410 | Keep `0 = report written`, with a stderr count, and reword :31 | A (:31 already promises 1 for any other failure) | U-C12 |
| D34 | K52: traceback for a code defect in tenant-probes (R283, R295 against the repo's stated stance for its own CLI, cli.py ladder, CONTRACTS) | B-15.1 step 7: the bug-report hook | Skip step 7: environmental errors get one line, unlisted defects keep their traceback. Note C-3 already moves agentsync to Option A | A, for consistency with C-3 | U-B15 |
| D35 | K59: `--config` and `--out` (R062, R452, R008): tenant-probes, and agentsync's `setup-report --out` and `it-request --out` (tests/test_contracts.py:78, :84) | `--config` gains `-A, --local-config`, which this script alone would have. `--out` becomes `-o, --output` on all three, with `--out` a warning alias; install.sh's `setup-report --out` call stays until its minimum build has the alias | Keep `--config` everywhere (pinned, and install.sh reads it from plists), and record it. Keep `--out` on agentsync's hidden verbs for parity | `--config`: B. `--out`: B-14.1's alias on tenant-probes now, agentsync's under A later | U-B14, U-C13 |
| D36 | K65: the command name `purge` (R035, R033; Reference A keeps purge for cached content) | C-8b: `delete`, with `purge` a hidden alias and outcomes `deleted` | Keep the visible frozen name (KISS K19b), fix only the help words (B-1.6), and record it | B. Decide before U-B5, since the JSON outcome becomes a contract | U-B5, U-C13; U-C8b if A |
| D37 | K66: `hold --release` of nothing (R326, R003) | C-6's row: exit 0, `outcome: "unchanged"`, config holds keep exit 1 | Keep exit 1; `git grep` finds no pin on the text | A | U-C6 |
| D38 | K67: commas from 1,000 (R217 against `SYNC_SUMMARY_RE`, install.sh:10796, and parsers of old install.out) | C-15: commas in `plural`, with parsers updated | Keep raw counts, and record it | B | U-C13; U-C15 if A |
| D39 | Credentials (R167, R170, R062's `--token`): agentsync has delegated sign-in only (MSAL, Keychain). A bearer token variable would sidestep the broker and Conditional Access design | Add `AGENTSYNC_TOKEN`, read only by Graph calls, with `login_required` naming it | Keep stored login only, and record it: no CI use exists | B | U-C13 |

---

## 7. Round 2 changes

**Inputs:**
- 44 verified findings for `scripts/install.sh` and `scripts/tenant-probes.sh` (K21-K64), which round 1 did not audit as CLIs.
- Every entry of `/tmp/cli-bp/critic2.json` that concerns this repo.

**Method:** static reading only, at `ccf696b7b` (where the verifiers read) and origin/main `d62ac1487` (where implementers branch). No CLI
under audit was run. Nothing in the repo was edited, committed, stashed or fetched. Each critic claim was checked against the code; where a check corrected a
claim, the row says so. Helper files written, all under `/tmp/cli-bp/`: `acs_r2_anchor.py`, `acs_r2_anchors.txt`, `acs_r2_anchors2.txt`,
`acs_r2_anchors2.out`, `acs_help_d62.txt`, and read-only snapshots `acs_*_d62*`.

**Edited in place** (each marked "Round 2 fix" or "Round 2"):
- §1: CLIs covered; the gate list; the CI duration; the finding index and totals.
- §2: `emit`, `Reason`, `error_doc`, `confirm`; `test_error_doc_is_one_line`.
- B-1.5, B-5's reason table, `status` shape and U-B5's `purge --queue` shape.
- C-2 (`--confirm` to `--yes`, in its heading, steps, tests and decision), C-4 (the switch name), C-5 (`--no-input`).
- §5: the exit codes, `-v`/`-d`, metavars, K5, K18 and K19 rows.
- §6: the sizing table, the U-0, U-A1, U-B1a and U-B2 rows, decisions 2, 3 and 9.

**Appended** (all other text is unchanged):
- The Phase 0 rows, spawn step 5 and the recycle point; the §1 gate and landing notes.
- "Round 2 additions to Wave A" (U-A1 split, A-2).
- B-1.6, B-2.3, the U-B4 and U-B5 addition lists, and "Round 2 additions to Wave B" (B-7 to B-17).
- The C-1 K69 row, C-1b, the C-2, C-4, C-5 and C-6 additions, and "Round 2 additions to Wave C" (C-8b to C-13 and the optional units).
- The §5 notes; §6's new unit rows, holds, landing table and "Needs decision (round 2)"; this section.

**Where the 44 findings went:**

| Rows | Findings |
|---|---|
| A-2 (U-A2) | K36, K22's shared half |
| B-7 (U-B7) | K25, K26, K28, K46 |
| B-8 (U-B8) | K44 Part A, K47, K38, K45, K42's safe half |
| B-9 (U-B9) | K33 Part A, K34, K39 |
| B-10 (U-B10) | K35, K40 |
| B-11 (U-B11) | K37 |
| B-12 (U-B12) | K22, K21 Option B |
| B-13 (U-B13) | K64, K48, K56, K58 |
| B-14 (U-B14) | K59, K60, K62, K49, K50's compatible half, K63 |
| B-15 (U-B15) | K52, K53, K54 |
| B-16 (U-B16) | K61 |
| B-17 (U-B17) | K24 Option B, K23 Option A |
| C-9 (U-C9) | K27, K33 Part B, K30 |
| C-10 (U-C10) | K31, and K34's `--timeout` |
| C-11 (U-C11) | K43 |
| C-12 (U-C12) | K50's rest, K51, K55, K57 |
| C-13 (U-C13) | The records for K21, K29, K32, K41 and every other kept contract |
| Optional | K21 Option A (C-14a), K41 Option A (C-14b), K32 Option A (C-16), K29 and K24 Option A (C-17) |

**Critic entries concerning this repo:**
- **uncoveredClis.** install.sh and tenant-probes.sh are now covered (§1). gate.sh and ship-land.sh stay out as developer tools.
- **planConflicts.**
  - Gate list: confirmed against CLAUDE.md. `[ -d node_modules ] || npm ci` restored, and CI corrected to 18 to 62 minutes plus runner waits, 80-minute limit.
  - Rules dropped as "Vercel-specific": confirmed at the 5 cited lines (exit codes, `-v`, `--confirm` twice, `✗` and `12.5K`). All corrected; D10-D13 hold both options. `12.5K` itself needed no change, but checking it found K67.
  - `--confirm` on purge and compact-history: corrected to `-y, --yes`. `grep -n -- '--format\|<CENTS>'` on this plan finds nothing (measured), so those parts of the entry concern other plans.
- **missedFindings.**
  - R035 on `purge`: confirmed at cli.py:375, and also at :443, :444, :446, which the critic did not list. K65: help words in B-1.6, the name under D36 and C-8b. R033: the JSON outcome is tied to the same decision.
  - Plan-level R100 names three other plans, but this plan also adds help examples. Parser tests added for every one: `test_every_help_example_parses` (C-1, agentsync), `test_every_help_example_parses` (C-11, install.sh), `test_every_probe_help_example_parses` (B-14.2), plus `test_printed_recovery_commands_parse` and `test_printed_run_command_parses` for printed recovery commands.
- **unmentionedRules** (the rows that name agentsync, or apply to any plan with this feature):
  - R020, R165, R293, R300, R315, R317: hold on the Graph client (GET only). Checking the one network mutation found K70.
  - R033, R035: K65. R034 on offboard's wording: B-1.6.
  - R112, R117, R119: no agent detection and no stdin read exist (measured). "R117 applies to agentsync's agent detection" is refuted; it applies only if C-5's decision adds detection.
  - R167, R169, R170, R171, R480: R169's lock exists (msal-extensions). R170 is partly met by C-5. R167 is D39. R171 and R480 are N/A.
  - R174, R175, R176: R176 tests added to C-4. R174 and R175 are N/A (no delivered secret, no context).
  - R245-R264: `next` shape (R249, R257), `retryable` and `retryAfter` (R259), `requestId` (R258), `action_required` (R254), `confirmation_required` (R263), `canceled` and `data.remote` (R262), camelCase (R246), TTY pretty-printing (R251), bulk shape (R248, R253) and `--fields` (R252, in C-1b). R245 is N/A, since no command has `--follow`.
  - R281, R282, R286-R291, R296, R299, R301, R305: R296 and R299 are covered by the U-B4 table and `graph/client.py:520`; R305 by C-3. **The rest were not checked** and stay open.
  - R088, R092, R096, R101: K69. R104 was not audited beyond this: printed recovery commands carry argv and local values, not remote ones.
  - R107, R108: K68 and K23. R109 is N/A, since no completion exists.
  - R060: `git grep` of cli.py finds no "flag" in user strings. K26 fixes install.sh's two, and K62 fixes tenant-probes' "unknown argument".
  - R072, R434-R469 (spellings): the critic grepped other repos, not this one. Reading agentsync's `add_argument` list (measured with `grep -n`) finds `--source ID` (B-1.5) and the hidden `--out` (D35). `--budget` is bytes, not money, so R463 does not apply. No `--filter`, `--tag`, `--watch`, `--plan` or `--page`.
  - R073, R075-R080: K31 for install.sh. R079: K44.
  - R100: above. R308: B-1.3 and B-7.3 print once. R326: K66. R343, R345, R346: `hold --list` recorded in U-C13; Graph paging is not exposed.
  - R383-R385: the alias rows follow them. R386: pyproject is 0.1.0 with no CHANGELOG (measured with `ls` and `grep`), so a dated CONTRACTS note is the release note, and `SETUP_PROMPT_COMPAT` is the only real gate. R388: C-4's switch renamed, and K21 and K32 use the same form.
  - R392, R394, R396-R407: R403, R401 and R404 in `tests/test_cli_modes.py`; R392 in C-2 and K26; R396 in K37; R397 in C-2 and K27; R398 and R400 in K34, K35 and K53; R399 in K54; R406 by the shared helpers' tests.
  - R423: `docs/design/glossary.md` (B-8.2). R432: C-6's note. R424-R428, R430, R431, R433: **not checked**, open.
  - Not checked for this repo, and not named for it by the critic: R122-R135 (it has no prompts until C-2), R179-R196, R202-R211, R214-R218 except R217, R221-R234, R235-R242, R265-R272, R327-R333, R340, R342, R470-R478 except R470 (K46).
  - N/A here, by the critic's own note or by reading: R054-R059, R085, R474, R110, R159-R166, R365, R367-R373, R376-R378, R389.

**Corrections to the verified findings, made while integrating them** (the verdicts are not contradicted; these are conflicts between them or with origin):
- K21 and K22 chose different fds, and K22's fd 3 collides with `uv_run`: one rule, fds 8 and 9 (A-2.2).
- K22, K23 and K24 each added their own `--json` arm: one arm (B-12.1).
- K23's first-argument `help` and K25's main-loop `help)`: both kept, with distinct jobs (B-17.2).
- K34's `--timeout SECONDS` against R451, R436 and K31's `<DURATION>`: one option, in C-10.
- K34's `AGENTSYNC_FIRST_SYNC_LIMIT` repeats K44's defect: `__AGENTSYNC_FIRST_SYNC_LIMIT`.
- K34's `first_sync_run` snippet predates origin's fd shuffle: restored (B-9.2).
- K34 and K35 together make `$WAIT_CHILD` a wrapper: `pkill -P` first (B-10.1).
- K37's `--limit N`: `-n, --limit <NUMBER>` and `-a, --all` (R444, R446).
- K22's bare-string `next`: the R249 shape.
- K26's empty `--launcher` moved to K27, so one commit carries the exit change.
- K62's `✗`: plain output keeps `Error:` (R205).
- K52's open report URL: `it_request.REPO_URL`.
- `SETUP_PROMPT_COMPAT` is 34 at origin, so K21, K24 and K29's bumps go to 35.
- Measured values restated at origin: help is 311 lines with 261 over 80 columns (K43, the verdict read 305 and 257); 203 `say` lines and 24 `>&2` (K21, 194 and 22); 45 `could not` (K42, 40); 358 `last_line(` (K21, 293).

**Round 1 defects found while integrating:**
- U-A1's parts sum to 500 LOC, because its size line used 160 test LOC against its own ~190: split.
- `clio.emit` was compact-only (R251), `error_doc`'s `next` a string (R257), and `Reason.interrupted` not R262's `canceled`.
- `status`'s `waiting_on_you` was snake_case (R246).
- U-B5's `purge --queue` was not the bulk shape (R248).
- C-2 never prompted on a terminal (R146).
- C-4's switch used the internal prefix (R388).
- C-5's `--no-input` is not a Reference B spelling.
- K19's drop conflicted with K44.

**Open items the reader should know:**
- Line numbers are origin/main `d62ac1487`. The local ref was not fetched, because the brief forbids changing the repo, so trunk may have moved. Implementers find code by symbol or quoted text, as §1 says.
- Not run: every test name above is a proposal, and every behaviour claim comes from reading, except the K53 stand-in measurement noted in its row.
- The checklist's rules marked "not checked" above stay unaudited for this repo.

**Counts:**
- New findings: 44 (K21-K64) plus 6 found verifying the critic (K65-K70).
- New units: U-A2, U-B7 to U-B17, U-C1b, U-C8b, U-C9 to U-C13, U-C14a, U-C14b, U-C15 to U-C17. Split: U-A1. Folded: K70 into U-B2, K66 into U-C6.
- New decisions: D10-D39, with decisions 2, 3 and 9 of the round 1 list corrected.
- Size (estimated): ~10,900 LOC recommended (round 1: ~4,375), and ~14,530 with every option (round 1: ~5,785). Every unit is under 500.
