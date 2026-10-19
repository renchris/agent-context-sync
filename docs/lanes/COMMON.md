## Common brief for every B13 round 17 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 17.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 16 landed" and "B13 round 17" (the v25 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.128 to §16.133.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round17/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round17/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 17's:
U1, U15, U56 and U93 (the governance.remote [FAIL] puts the docs repo's remote URL, host and path, into the report;
lane RA, which may change the detail at `governance.py` 1590-1607 and tells lane B); U4 and U94 (install.sh's
report_word turns any lower-case code word into a report word, so an organization of that name stays in clear), with
U54 and U61 (the shell fallback's label, [governance] prefix and CloudStorage symlink twins; lane RB); U11, U19, U45,
U52 and U86 (the report's list of the person's fix kinds is older than install.sh's NEXT and cli.PERSONS_KINDS; lane
RA); U8, U23 and U44 (one vanished source carries two or three labels in one report; lane RA); U26, U27, U28, U31,
U34, U53, U81 and U107 (residues of round 16's rewrite of a report the older installed build rendered; lane RB); U36,
U37, U38, U60 and U89 (the reports still say a carried patch "is listed there by its hash"; lane RB, with U29 and U97
the prompt half, lane P); U55 and U65 (install.sh's first sync has no branch for exit 65; lane RB); U57, U67, U87
and U96 (config_failed's exit-65 NEXT re-runs with $ORIG_ARGS, not $RERUN_ARGS; lane P); U2 and U16 (a disallowed
docs-repo remote is handed to the agent to delete; lane P, with U14 and U17, the CLI's "no remote" line, lane B);
U9, U25 and U46 (the config.vanished_sources fix never names the re-add step 1 names; lane B); U64 and U95 (a manifest
with a damaged table page still passes status; lane B); U68, U69 and U92 (the quiet lock wait; lane B); U13, U47,
U79 and U80 (whose step the bundled pandoc's [FAIL] is: lane E builds doctor's fix and sends lane P the words); U30
and U78 (doctor's pandoc fixes name the installer by a checkout-relative path; lane E). The "(s)" residue again sits
in two lanes: U71 and U102 (lane B), U74, U75 and U76 (converters, lane E). Earlier rounds are in COMMON5.md to
COMMON15.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions (`_pandoc_route`, `_check_pandoc`, their fix texts and warm-up) and
lane B the rest. In `scripts/install.sh`, lane P owns config_failed (5690-5760, its exit-65 branch and the add-source
loop's NEXT) and the --list-folders NEXT (4633-4646, 5044), lane RB the rest (the first-sync branches 6074-6105,
older_report_rewrite, redaction_pairs and report_word, write_bring_back and the fallback body). Lane E changes
`scripts/gate.sh` or its journal only if U106 is built; if it does, it lands last, after the other five, and says so in
its note.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.128 to §16.133); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v26 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT and every "v25" the installer prints). A lane whose fix needs prompt words
sends them to lane P with cc-notify (lane P's session id is in its fire message) and records that in its note. Lane
E's pandoc owner (U13, U47, U79, U80) and lane RB's hash wording (U36, U60, U89) touch what v25 says: send lane P the
words. Lane P also carries round 16's deferred wording (U29, U97).

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.128 to §16.133) begins "Amended
<date>, §16.NN U<n>". A line that records another round's change reads "Amended <date>, §16.NN U<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 16's ids are T<n>; round 17's
are U<n>.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
U34 and U107 are this seam again (a report the older build renders after a git stop or a step 1 stop).

Dated-line test: since round 9 K80 the gate reads every "(U<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The `_ROUND_16_PARAGRAPH_AMENDS` and earlier pin
tables in tests/test_deploy_pack.py take round 17's rows the same way (U109: read a dated line with a non-greedy
pattern).

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round17/
scratch-<lane>/`, not in /tmp; a reboot empties /tmp (round 16 lost its fire briefs to one) and every /tmp leftover
became an operator chore. Only the land lock stays in /tmp.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set, and it ends `GATE GREEN at <sha>`.

Landing (round 17): land with `scripts/ship-land.sh --lock /tmp/b13-r17-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r17-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again (round 16
lane E: two such exits at loads of 725 and 805, landed on the third at about 300). Exit 9 is CI it could not read:
`nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. Tests must also pass on the CI runner's git and
tool versions, not only on this Mac. CI now takes 15 to 20 minutes a run (U112).

Makes things worse: 13 round 17 items carry "a fix makes things worse? True" (U2, U9, U18, U25, U34, U41, U46, U51,
U63, U68, U79, U83, U108). For each, the verifier's corrected fix is one that does not: read its "Verifier" part
before you build.

Regressions: round 17's wrongs sit on seams round 16 left: the remote URL a governance [FAIL] carries (U1 cluster),
the shell report's word list (U4 cluster), a disallowed remote handed to the agent (U2, U16), the CLI's "no remote"
line (U17), add-source's "failed" after writing a valid block (U40), --report-only closing the previous session's
attempt (U33), the older build's step 1 and git-stop reports (U34, U53, U81), exit 65 with no first-sync branch (U55,
U65), a damaged table page passing status (U64, U95), the person's-fix kinds the report lists (U52), the first report
dropping its Doctor section (U48), a converter missing at sync time making a permanent "unreadable" stub (U3), the
bundled pandoc's [FAIL] made the person's by v25 (U80) and the company-mirror git message (U32). Before you land a new
stop, exit code, wait or person-owned fix, test it on a Mac in the state it is meant for AND on one where it must not
fire, and grep the other readers of its words (install.sh, the report, the guides).

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r17-<lane>.md`.
