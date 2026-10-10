# Corporate Mac bring-back file, fourth round, triaged (2026-10-10)

Scope (frozen): when the operator pastes the corporate Mac's v10 `~/agent-context/bring-back.md`, triage it against
main, fix what is new, rehearse before any re-paste go-ahead; redact anything identifying.

Method: Workflow run `wf_0f0bb474-b7e`, 13 agents: a verifier and a skeptic for each of six items (E1 to E6) and
one completeness critic (rows C1 to C4). The agents were read-only on main `120fec9` and reproduced only in
throwaway homes under `/tmp`. Where a skeptic corrected a verdict, the corrected one is recorded here. Each item was
also classed under the proposed stop rule (plan section "Outlook and the stop rule"): **turn** (costs or would cost
a person a turn), **wrong** (a false or leaking result), **cosmetic**, **operator** or **none**. Only turn and
wrong items are built.

Names: the paste's sections 2 and 3 are unredacted, and section 1 still shows the name tokens of the two old friction
lines (12 and 14) and the MDM line's product label, all byte for byte as in the v9 file. This doc uses only the
report's placeholders. The paste and the full findings stay outside the repository. Earlier triages:
[2026-10-06](corporate-bring-back-2026-10-06.md), [2026-10-07](corporate-bring-back-2026-10-07.md),
[2026-10-08](corporate-bring-back-2026-10-08.md).

## Verdict

- **Prompt v10 ran clean on the corporate Mac.** Attempt 6 of 6 was one command, with 0 human turns, 0 deviations
  and 0 errors. It ran v10 at `4a42af937d60` (main then), `install.sh` exited 0, and the session ended on the same
  wait as the last three: the baseline questions are still a draft. This is the second clean field run (v9 was
  the first), so by the proposed stop rule setup is done.
- **The one friction line (F34) is cosmetic.** The install took 12m03s, past the prompt's suggested 10-minute
  timeout, because the first speech-helper build took about 687 s. It cost no turn: the agent kept reading.
  It will not recur on this Mac: the built helper is reused while its package source is unchanged. The next
  install's helpers step measured about 1 s in a throwaway home (E1.c).
- **Two latent redaction leaks were wrong-class and are built** (E4, `ba5b993`). The report cut a full name at its
  comma, so a "Last, First" name left the first name unredacted, and this person's corporate display name has
  that form. The MDM server's host was printed as `profiles` gives it, which leaks a hosted MDM's company label
  when it is spelled unlike the OneDrive org. Neither showed in this file, and both changes only redact more.
- **Nothing needs prompt text.** v10 stays and needs no recopy: its block is byte-identical to `69694db`'s.
- **Every B10 and B11 change that this run could exercise read right in the field** (E3.m). Examples: the
  `helpers` step with its own time, every wait on the Loop line, and the exclude advice naming the source id as
  `sources.toml` writes it.
- **The bottleneck is the operator, not setup.** About 1,585 items wait to be curated, against 1,455 last
  round. The open items are listed under "Operator actions, in order" below.

## Inputs

| Fact | Value | Source |
|---|---|---|
| Report | generated 2026-10-10T02:09:50Z in 1.3 s of 12 s; macOS 15.7.3, arm64, MDM-enrolled | Run metadata, Environment |
| Prompt and build | v10, attempt 6 of 6; install.sh at `4a42af937d60`, the checkout's HEAD and origin/main | Summary, install.log |
| Agent | GitHub Copilot CLI, named whole | Summary |
| install.sh | `--list-folders` exit 0 after 1 s (listed 24, synced 2); install exit 0 after 723 s: helpers 702 s, status 10 s, first sync 2 s | Installer |
| Config | 16 sources, all live: 2 local under CloudStorage, 14 inbox-kind; kept 2, added none | Configuration |
| Doctor | 98 checks (the new one is `speech`): 90 ok, 1 info, 7 warn, 0 FAIL; the same not-ok set as v9 | Doctor |
| Background | poll 1,128 runs, reconcile 97, both last exit 0; plists still differ from this build in ProgramArguments only | Background runs |
| Fix request | the v7 to v9 parts unchanged; a v10 part with items 9 and 10 and a "Not used" list | section 2 |
| Local work | "not repeated": the same 3,587-line patch as before | section 3 |

## Items

| Row | Finding | Status | Class | Disposition |
|---|---|---|---|---|
| E1.a | The speech build fetches FluidAudio's git history (about 360 MiB) and an 87 MB binary from github.com, builds, and only then checks for models | by design | none | CONTRACTS §16.35, README "fetches FluidAudio from github.com" |
| E1.b | 687 s in the field vs about 101 s in the rehearsal: the rehearsal's SwiftPM cache was warm, because SwiftPM ignores HOME and used this machine's shared cache | by design (rehearsal method) | none | Learning: a sandbox speech timing is a warm-cache timing |
| E1.c | The next install on the same Mac reuses the helper (keyed on the package source digest); helpers took about 1 s on a second run | by design | none | Measured in `/tmp` |
| E1.d | A tool kill during helpers: SIGTERM logs `rc=143`, writes the report and a "safe to re-run" NEXT; the stopped run is not counted as a retry. SIGKILL leaves no end line. Compiler processes outlive a group kill | by design | cosmetic | Held |
| E1.e | F34 as a whole: cost no turn here, will not recur on this Mac, and a new Mac is covered by the prompt's re-run and background sentences | needs prompt text | cosmetic | Held for v11: step 2's timeout sentence could warn of a first speech build up to 20 minutes (the build's own limit is 1,200 s) |
| E1.f | Skipping the speech build while no models are placed | new | cosmetic | Not built: it adds an install run after models are placed and hides a build failure until then |
| E2 | `config.graph_company` warn ("delete line 22") is on no WAITING ON YOU line (fix-request item 10) | known (10-06 row 10, 10-07 U16, 10-08 D1.j) | cosmetic + operator | Not built: the wait list is closed (§16.20) and the installer never edits an existing config (§16.13). The line came from agentsync's own template between 2026-09-29 and 2026-10-05, or from the pre-K15 IT request; deleting it is the operator's |
| E3 | Every number in section 1 matches the code (13m15s, 723 s, 702 s, 996 s, 182 replacements, 239 log lines, 98 checks). First-sync clause without `-reread-R` is right: the 17 re-reads were background run 1733's, not the installer's sync | refuted / by design | none | |
| E3.e | Section 1 has no speech line of its own; the only `speech: off` is in the friction line | new | cosmetic | Held |
| E3.b, E3.h, C4 | Installer-output counts span earlier attempts; attempt 1 reads "failed at step 2" under v6 rules; Recent errors counts only the last 64 KiB of each log | known | cosmetic | Held |
| E4.a | Name tokens in friction lines 12 and 14, and the MDM product label: byte-identical to v9 | known (10-08 D3) | operator | The operator's hand edit of `friction.md`, as before |
| E4.a′ | The MDM server's host is printed as is; on a Mac whose hosted MDM is named for the company unlike the OneDrive org, it leaks (measured with the real redactor) | new | wrong | **Built** `ba5b993`: the host is `<host>` |
| E4.c′ | `_full_name` cut GECOS at the first comma: for "Doe, Jane" only "Doe" was registered (reproduced). This person's corporate display name is "Last, First" | new | wrong | **Built** `ba5b993`: the whole field is the name, and both parts are registered |
| E4.a″ | A cloud proxy's PAC path can carry the customer's domain; only the URL's host is redacted | new | cosmetic (new Mac only, inferred format) | Held |
| E4.b | The residue check printed exactly what its rule yields (1 true hit, 2 false) | by design | none | |
| E5 | Operator items (baseline, purge queue, empty folders, decisions, plists) | known | operator | See "Operator actions, in order" |
| E6 | The field agent's status claims for items 1 to 10 and its "Not used" lines are right; items 1, 2, 4, 5, 7 and 8 are fixed on main (`ea77262`, `41469c9`, `fb128f0`, `41f2484`/`015bcdc`, `82ff572`, `9186365`). Its "Not used" list differs from v9's in only 2 of 19 lines | already fixed / by design | none | |
| C1 | The speech build downloads a prebuilt third-party binary, which the IT request does not name | new | cosmetic | Held (doc only) |
| C2 | The speech build's `swift package resolve` gets no proxy variables, though step 1 says a corporate proxy may need `HTTPS_PROXY` | known (meeting-video R8) | cosmetic here | Held; this Mac reached github.com directly |
| C3 | 2026-10-08 D9.g is still open: is the inbox bridge `<source-7>` a copy of the library `<source-1>` syncs? | known | operator | Settle before the first curating session |

## Built

`ba5b993` fix(setup-report): a full name written "Last, First" and a hosted MDM server's host are redacted. Two
lines in `setup_report.py` plus a registration in `enrollment()`, three tests, and CONTRACTS (the v4 and v5
redaction paragraphs) and `docs/deploy/setup-feedback.md` amended. Gate green (3,337 passed).

## Operator actions, in order

These are corrected per the E5 skeptic and the critic. None of them is a setup step.

1. Settle D9.g by hand: is `<source-7>` a copy of `<source-1>`'s library? If one should go, drop it before any
   curating (C3).
2. Run the 14 queued purges alone, before the exclude line: `~/.local/bin/agentsync purge --queue --dry-run`. You
   should see 14 blocks with `0 item(s)` and `nothing is targeted`, and no `path:` line. Then run
   `~/.local/bin/agentsync purge --queue`. Any `path:` line: stop and bring the output back.
3. Exclude line for `<source-1>`: the build on the Mac (`4a42af9`) has the scope-change guard, so the printed line
   is now safe to paste. First confirm in the web view that all 5 folders are meant to stay empty. Paste the line
   in place of any existing `exclude =` (never a second key). Then sync twice and read status. Run
   `accept-deletions` only if the breaker trips on files that really were deleted.
4. Confirm the baseline questions in `~/agent-context/docs/_eval` (keep about 10, correct the answers, set both
   files to `status: confirmed`). The next session after that is the first to curate.
5. Delete line 22 of `~/agent-context/sources.toml` (`company =` under `[graph]`).
6. Decisions: `085fac870dd6` (online-only images) is moot on this Mac, with 0 online-only images for a third
   report running. `71d3e66ef726` (empty cloud folders) keeps its default, and step 3 carries it out.
7. Before the next file is sent, edit the name tokens in friction lines 12 and 14 (and the typed placeholders in
   lines 5 and 13).

## Learnings

- A sandbox's speech-build time is a warm SwiftPM-cache time whatever HOME says: SwiftPM keeps its cache under the
  real user's `~/Library/Caches/org.swift.swiftpm`. The corporate network's cold first build was about 7 times
  longer. A rehearsal also writes to that shared cache.
- A field report can stay clean while it carries a latent leak: the "Last, First" name and the hosted MDM host were
  found by reading the redactor against this person's facts, not by a hit in the file.
