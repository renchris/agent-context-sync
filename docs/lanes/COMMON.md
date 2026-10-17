## Common brief for every B13 round 14 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the new ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 14.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 13 landed" and "B13 round 14" (the v22 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.110 to §16.115.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round14/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round14/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 14's: Q1, Q9
and Q33 (status's NEXT on a person's [FAIL]; Q2 is its prompt half); Q45, Q54 and Q63 (persons_fails and the empty
SharePoint library fix); Q3, Q18 and Q36 (a gone folder's FAILs counted as unexpected); Q14 and Q37 (the residue
check flags "Dropbox"); Q10 and Q35 (the heartbeat [warn] promises an end-of-run answer); Q11 and Q34 (the wait for
a source folder that is gone); Q23 and Q41 ("names the folder, up to five"); Q64 and Q77 (the guide on a late step 1
line); Q15 and Q73 (v22's "no longer on disk" trigger); Q56, Q60 and Q76 (doctor checks the wheel's pandoc, not the
native one; Q62 is its CONTRACTS half). The "(s)" beside a known count is one cluster across lanes: Q24, Q38, Q42
(arm_local), Q57 (cli), Q81 (loop), Q39 (install.sh). Earlier rounds are in COMMON5.md to COMMON12.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions (`_pandoc_path`, `_check_pandoc` and their warm-up) and lane B
the rest.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.110 to §16.115); gate green;
landed through /ship; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v23 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT and every "v22" the installer prints). A lane whose fix needs prompt words
sends them to lane P with cc-notify (lane P's session id is in its fire message) and records that in its note.
Lane B's status NEXT (Q1, Q9, Q33) changes what v22 step 3 says status prints: lane B sends lane P the words.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49); round 5 found new bare ones in several lanes' text.

Dated lines: a dated line in any section other than your item's own (§16.110 to §16.115) begins "Amended
<date>, §16.NN Q<n>". A line that records another round's change reads "Amended <date>, §16.NN Q<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 4 also used Q ids: name
round 14's as "round 14 Q<n>" wherever a reader could mix them up.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.

Dated-line test: since round 9 K80 the gate reads every "(Q<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. Round 13 lane B added a
`_ROUND_13_PARAGRAPH_AMENDS` pin table in tests/test_deploy_pack.py; round 14 adds its rows the same way.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set, and it ends `GATE GREEN at <sha>`.

Land lock (new in round 14). In round 13 three lanes ran the full test set at once; swap filled and a pytest run was
SIGKILLed (exit 137). Run every full test run, and the fetch, rebase and land gate before a push, under one lock:
`lockf /tmp/b13-r14-land.lock bash -c 'git fetch -q && git rebase origin/main && scripts/gate.sh'`. `--affected`
runs need no lock. The push itself stays OUTSIDE the lock, as its own command right after the lock exits green: a
`git push` inside `lockf … bash -c` would get past the operator's push approval rule.

Pushes: `git push` stops at an "ask" permission prompt and waits for the operator. Run the push as its own short
command (`git push origin HEAD:main`), not inside a compound command, and right after the locked fetch, rebase and
land gate, so one Yes lands it. In rounds 8 to 13 queued pushes were rejected once another lane landed: take the
lock again (fetch, rebase, `scripts/gate.sh`), then push again as its own command.

Trunk before you push (round 11 M87): right after the final fetch and green gate, and right before the push, read
the runs for origin/main's head: `gh run list --commit "$(git rev-parse origin/main)"`. If one is queued or in
progress, wait for it with `gh run watch <id> --exit-status` (ci cancels in-progress runs on main, so pushing on top
would hide its result); then fetch again and, if main moved, rebase, re-run `scripts/gate.sh` and repeat. If trunk is red, do
not edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and
the tests (`gh run view <id> --log-failed`), then wait for the owner's fix, or land on top only when your diff does
not touch those tests and record the red run id in your note. The lane that landed a test that is red on CI owns its
fix until main is green, even after other lanes land on top. Tests must also pass on the CI runner's git and tool
versions, not only on this Mac (round 10: two v19 tests passed here and failed there).

Makes things worse: 8 round 14 items carry "a fix makes things worse? True" (Q2, Q7, Q10, Q15, Q33, Q40, Q41, Q73).
For most, the verifier's corrected fix is one that does not.

Regressions: round 14's wrongs cluster on two cross-lane seams of round 13: lane B's new empty-library doctor fix
against lane P's persons_fails (Q45), and status's NEXT against v22's step 3 (Q1). Before you land a new stop, exit
code, wait or person-owned [FAIL] fix, test it on a Mac in the state it is meant for AND on one where it must not
fire, and grep the other readers of its words (install.sh's persons_fails, the report, the guides).

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r14-<lane>.md`.
