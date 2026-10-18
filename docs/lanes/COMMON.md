## Common brief for every B13 round 15 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the new ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 15.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 14 landed" and "B13 round 15" (the v23 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.116 to §16.121.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round15/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round15/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 15's: S2 and S13 (the logger WARNING a sync prints for a missing root or the alarm); S3 and S41 (the
missing-root wait's causes and the SharePoint library behind a signed-out OneDrive); S11, S28 and S64 (the held-listing
heartbeat note's promise); S12 and S27 (the doctor bucket counts folders, the others checks); S26, S30 and S31 (the
report's "(s)"), with S14 (install.sh), S65 (loop) and S52 (converters) the same "(s)" cluster in other lanes; S33, S58
and S62 (a status that exits non-zero with no [FAIL] line gets the [FAIL] NEXT); S19 and S20 (the older renderer's
stopped copy); S21, S22 and S34 (.not-sent and the unreadable patch); S37 and S56 (the apps=sharedlibraries dated
lines); S8 and S55 (the first sync's refused config in v23's finish; S55 is the prompt half); S29, S50 and S53 (the
pandoc [FAIL] fix wording: S50 is the guide's half, lane D). Earlier rounds are in COMMON5.md to COMMON13.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions (`_pandoc_route`, `_check_pandoc`, their fix texts and warm-up) and
lane B the rest. `scripts/ship-land.sh` (S59, S63) is lane E's; it changes the tool every lane lands with, so lane E
lands it last, after the other five have landed, and says so in its note.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.116 to §16.121); gate green;
landed through /ship; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v24 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT and every "v23" the installer prints). A lane whose fix needs prompt words
sends them to lane P with cc-notify (lane P's session id is in its fire message) and records that in its note.
Lane RB's status NEXT with no [FAIL] line (S33, S58, S62) and lane B's first-sync config refusal touch what v23's step 2
and finish say: send lane P the words.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49); round 5 found new bare ones in several lanes' text.

Dated lines: a dated line in any section other than your item's own (§16.116 to §16.121) begins "Amended
<date>, §16.NN S<n>". A line that records another round's change reads "Amended <date>, §16.NN S<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 14's ids are Q<n> (round 4 used Q too); round 15's
are S<n>.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.

Dated-line test: since round 9 K80 the gate reads every "(S<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The `_ROUND_13_PARAGRAPH_AMENDS` and round 14
pin tables in tests/test_deploy_pack.py take round 15's rows the same way.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set, and it ends `GATE GREEN at <sha>`.

Landing (round 15): land with `scripts/ship-land.sh --lock /tmp/b13-r15-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule (in round 14 lane D's push waited about 4 hours). Every other full test run also takes the
same lock: `lockf /tmp/b13-r15-land.lock scripts/gate.sh --lane` (round 13: three full runs at once filled swap and a
run was SIGKILLed); `--affected` runs need no lock. If ship-land exits 5 with workers lost ("cannot send", exit 137),
that is memory, not your diff: wait for load to fall and run it again. Exit 9 (from lane E's S59, S63) is CI it could
not read, never green or red: `nothing pushed` before the push (retry), `LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI owns its
fix until main is green, even after other lanes land on top. Tests must also pass on the CI runner's git and tool
versions, not only on this Mac (round 10: two v19 tests passed here and failed there).

Makes things worse: 13 round 15 items carry "a fix makes things worse? True" (S2, S3, S4, S13, S21, S22, S26, S27, S31,
S42, S44, S57, S70). For most, the verifier's corrected fix is one that does not.

Regressions: round 15's wrongs sit on seams round 14 left: status exits non-zero with no [FAIL] line yet install.sh
still says "fix the [FAIL] lines above" (S33, S58, S62), a patch read inside one attempt sent again (S34), a mirrored
source's block deleted from sources.toml (S7), and v23 treating the first sync's refused config as one that does not load
(S8, S55). Before you land a new stop, exit code, wait or person-owned fix, test it on a Mac in the state it is meant for
AND on one where it must not fire, and grep the other readers of its words (install.sh, the report, the guides).

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r15-<lane>.md`.
