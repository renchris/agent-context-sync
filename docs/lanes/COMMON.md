## Common brief for every B13 round 18 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 18.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 17 landed" and "B13 round 18" (the v26 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.134 to §16.139.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round18/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round18/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 18's:
V14, V52, V56 and V71 (the setup report's own doctor runs only `_status_checks`, so a manifest damaged under a table
FAILs in status but not in the report's Doctor section; lane B, cli.py's report hook; lane RA's V35 and V15, the
Summary's evidence line for an unreadable state file, read what B's hook then returns); V3 and V49 (the Summary's
doctor cut-off seconds are the time left, not the time doctor ran; lane RA); V1, V84, V85 and V86 (a word both
reports print decides what is redacted: an organization named like a report word stays in clear, and a printed word
that names no organization is over-redacted; lane RA for the Python reader, lane RB for install.sh's report_word,
and the two agree on one rule); V44 and V72 (the shell fallback prints a [governance] type error's value in clear;
lane RB); V45 (the report's `<value>` match swallows the refusal U58 appends; lane RA); V46 (the folder a vanished
source is re-added from is in clear in the shell report; lane RB); V16, V22, V27, V30, V31 and V91 (residues of the
older build's rewrite: the step 1 stop report, the git-stop "may be stale" line, the Doctor header and the fetch
date; lane RB); V47 (add-source "failed" after writing a valid block, again, on exit 1; lane P); V48 (a run that was
not the first labelled "first sync"; lane RA); V12, V17, V32 and V79 (v26's finish asks the agent to name a kept
patch file that no NEXT names; lane P, with RB's NEXT words if needed); V24 and V39 (v26 step 3 says a sync prints
nothing, but it now prints lock-wait and inbox lines; lane P, with lane B's V40 wording); V29 and V70 (v26's git
quote promises an `origin: other` the shell report never prints, and its remote command prints a URL rewrite's host;
lane P); V8, V9, V21 and V36 (step 1's --list-folders tails disagree with step 2 and doctor on a vanished source's
re-add and on a docs-repo remote [governance] refuses; lane P); V57, V61 to V65, V67 and V74 (U3's convert-retry
record: the loop says "sync again" while a converter cannot start, an older build's ordinary settled stub is not
recovered, entries for deleted files stay, and three texts still say a failed conversion is never retried; lane E);
V5 and V28 (a speech helper build that runs out of time leaves a [warn] with no fix; lane B, doctor's _check_speech).
Earlier rounds are in COMMON5.md to COMMON16.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions (`_pandoc_route`, `_check_pandoc`, their fix texts and warm-up) and
lane B the rest (including `_check_speech`). In `scripts/install.sh`, lane P owns config_failed (with its exit-65
branch and the add-source loop's NEXT, V47), the config step's order (V26) and the --list-folders tails and NEXT
(V8, V9, V21, V36), lane RB the rest (older_report_rewrite and its facts, redaction_pairs, report_word,
gov_value_sed, the helpers step's log line, write_bring_back and the fallback body). Lane E changes `scripts/gate.sh`
or `tests/_gate_journal.py` only for V93; if it does, it lands last, after the other five, and says so in its note.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.134 to §16.139); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v27 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT and every "v26" the installer prints). A lane whose fix needs prompt words
sends them to lane P with cc-notify (lane P's session id is in its fire message) and records that in its note. Lane
B's lock-wait line (V40) and lane RB's NEXT words for an unreadable kept patch (V12, V17, V32, V79) touch what v26
says: send lane P the words.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.134 to §16.139) begins "Amended
<date>, §16.NN V<n>". A line that records another round's change reads "Amended <date>, §16.NN V<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 17's ids are U<n>; round 18's
are V<n>.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
V16, V27, V30, V31 and V91 are this seam again (reports the older build renders after a step 1 or git stop).

Dated-line test: since round 9 K80 the gate reads every "(V<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The pin tables in tests/test_deploy_pack.py take
round 18's rows the same way.

Scratch (round 16 U111): write logs, scratch files and probes under `~/.cache/agentsync-bring-back/b13/round18/
scratch-<lane>/`, not in /tmp; a reboot empties /tmp and every /tmp leftover became an operator chore (round 18
V95: 54 round 17 entries, about 68 GB, were left there). Only the land lock stays in /tmp. To damage a manifest for
a test or probe, write the junk with `python3 -I`, not `dd` (`Bash(dd:*)` is denied; V42).

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set, and it ends `GATE GREEN at <sha>`. All three red land
gates of round 17 failed in tests/test_contracts.py (V93): if you touch CONTRACTS.md, run that file before you land.

Landing (round 18): land with `scripts/ship-land.sh --lock /tmp/b13-r18-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r18-land.lock
scripts/gate.sh --lane`; `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit
137), that is memory, not your diff: wait for the 1-minute load to fall under about 400 and run it again. Exit 9 is
CI it could not read: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI
owns its fix until main is green, even after other lanes land on top. Tests must also pass on the CI runner's git and
tool versions, not only on this Mac. CI takes 15 to 20 minutes a run.

Makes things worse: 13 round 18 items carry "a fix makes things worse? True" (V10, V12, V18, V25, V29, V32, V45,
V55, V63, V69, V74, V84, V85). For each, the verifier's corrected fix is one that does not: read its "Verifier" part
before you build.

Regressions: round 18's wrongs sit on seams round 17 left: the report's doctor hook missing U64's table-damage FAIL
(V14 cluster), U58's refusal swallowed by the `<value>` match (V45), U103's `<value>` rule missing from the shell
fallback (V44, V72), U46's re-add folder in clear (V46), U42's "first sync" head word on a later run (V48), U40's
"failed" back on exit 1 (V47), U31 and U34's older-build residues (V16, V27, V30, V31), U3's retry record (V57, V65),
and the report-word rule both ways (V1, V84, V85, V86). Before you land a new stop, exit code, wait or person-owned
fix, test it on a Mac in the state it is meant for AND on one where it must not fire, and grep the other readers of
its words (install.sh, the report, the guides).

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r18-<lane>.md`.
