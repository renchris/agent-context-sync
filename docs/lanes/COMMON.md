## Common brief for every B13 round 16 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 16.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 15 landed" and "B13 round 16" (the v24 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.122 to §16.127.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round16/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round16/*.md`): read the part
about your item. Items that describe one defect from several finders are built once; say so. Round 16's: T1, T10,
T29, T45, T67 and T81 (install.sh's config_failed says "does not load" for a config that loads but a sync or
init/add-source refuses; lane P, code and prompt); T6 and T30 (step 1 says "none is needed" for a mirrored folder
whose [[source]] block is gone; lane P); T5, T13, T31, T57, T71 and T85 (the person's-fix lists in install.sh's
PERSONS_FAILS and status's NEXT leave out "put the [[source]] block back"; lane B builds both twins), with T2 and T9
(status hands the person's policy.toml and [governance] fixes to the agent; lane B); T4 and T14 (the Summary's doctor
part calls the person's FAIL "unexpected") and T20 and T34 (the new doctor bucket's count wording; lane RA); T42, T53
and T66 (a vanished source's id in clear in the report's Doctor section; lane RA, which may change the detail at
`ops/doctor.py` 1880-1890 and tells lane B); T52 and T87 (the report's copy of the vanished-source query; lane RA);
T41 and T79 (the Q40 pandoc first-start CI flake: the ticker's TERM trap exits 1, not 3, under set -e; lane E); T54,
T59, T60 and T65 (the bundled pandoc's fix is a bare `uv tool upgrade`; lane E) and T17 and T77 (doctor's pandoc
slow-start text; lane E); T51 and T69 (the guide quotes the old re-read wording; lane D); T18, T23, T43 and T84 (a
stopped copy rendered by an older agentsync; lane RB). The "(s)" residue is one cluster in four lanes: T27, T50 and
T83 (bring-back.md, lane RB), T49 and T63 (the report, lane RA), T32, T36, T55 and T86 (the CLI and loop, lane B),
T61 and T62 (converters, lane E). T33, T37 and T56 (raw timestamped WARNING lines a sync prints; lane B). Earlier
rounds are in COMMON5.md to COMMON14.md.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha. In
`ops/doctor.py`, lane E owns the pandoc functions (`_pandoc_route`, `_check_pandoc`, their fix texts and warm-up) and
lane B the rest. In `scripts/install.sh`, lane P owns config_failed and the list-folders NEXT for a deleted block
(4633-4646), lane B the PERSONS_FAILS list (5323), lane E the pandoc ticker and wait (5377-5410) and `with_timeout`
(1028-1046), lane RB the rest. Lane E changes `tests/_gate_journal.py` (T90), which every lane's gate uses, so lane E
lands last, after the other five have landed, and says so in its note.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.122 to §16.127); gate green;
landed through `scripts/ship-land.sh`; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v25 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT and every "v24" the installer prints). A lane whose fix needs prompt words
sends them to lane P with cc-notify (lane P's session id is in its fire message) and records that in its note.
Lane B's person's-fix list (T5 cluster, T2, T9) and lane E's pandoc fixes touch what v24's step 2 says a person's
[FAIL] fix is (T70, lane P); T72 (one set of fixes for a folder gone from a signed-in app) is lane P's prompt half of
what doctor, the wait (lane B, T58) and the list say: send lane P the words.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49).

Dated lines: a dated line in any section other than your item's own (§16.122 to §16.127) begins "Amended
<date>, §16.NN T<n>". A line that records another round's change reads "Amended <date>, §16.NN T<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85). Round 15's ids are S<n>; round 16's
are T<n>.

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.
T11 is this seam again: on the field Mac the report after a step 1 stop is rendered by the installed v10 build.

Dated-line test: since round 9 K80 the gate reads every "(T<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added. The `_ROUND_15_PARAGRAPH_AMENDS` and earlier pin
tables in tests/test_deploy_pack.py take round 16's rows the same way.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set, and it ends `GATE GREEN at <sha>`.

Landing (round 16): land with `scripts/ship-land.sh --lock /tmp/b13-r16-land.lock`, typed exactly so (no env prefix,
no `bash` in front, its own command). It takes the one lock, fetches, rebases onto origin/main, runs `scripts/gate.sh`,
waits out and checks trunk's CI, pushes `HEAD:main` with no permission prompt (never forced; a refused push is rebased
and gated again), then reads the push's CI back; exit codes are in its header. Never run a bare `git push`: it stops at
the operator's ask rule. Every other full test run also takes the same lock: `lockf /tmp/b13-r16-land.lock
scripts/gate.sh --lane` (round 13: three full runs at once filled swap and a run was SIGKILLed); `--affected` runs need
no lock. If ship-land exits 5 with workers lost ("cannot send", exit 137), that is memory, not your diff: wait for load
to fall and run it again. Exit 9 is CI it could not read, never green or red: `nothing pushed` before the push (retry),
`LANDED` after it (read the runs back by hand).

Trunk (round 11 M87): ship-land checks trunk's CI before it pushes and stops on red (exit 6). If trunk is red, do not
edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and the
tests (`gh run view <id> --log-failed`), then wait for the owner's fix. The lane that landed a test that is red on CI owns its
fix until main is green, even after other lanes land on top (round 15: RA's counted-noun sweep turned trunk red on a
test that pinned a timeout's words, fixed in 1df9a9ac). Tests must also pass on the CI runner's git and tool versions,
not only on this Mac (round 10: two v19 tests passed here and failed there).

Makes things worse: 15 round 16 items carry "a fix makes things worse? True" (T3, T4, T7, T11, T14, T23, T26, T33,
T35, T37, T40, T47, T52, T56, T88). For each, the verifier's corrected fix is one that does not: the naive fixes it
rejects are registering label names as redaction values (T3), running the checkout's renderer on an old install (T11),
quoting inside ORIG_ARGS, which install.log's `args=` and the report parse (T35), moving a WARNING the LaunchAgent's
err.log needs (T33, T37, T56), printing a symlink's own name (T40), and opening the manifest read-write from the
report (T52).

Regressions: round 16's wrongs sit on seams round 15 left: the config that loads but a sync refuses is still "does not
load" in install.sh and v24 (T1 cluster), the person's-fix lists and status disagree on whose a fix is (T2, T9, T5
cluster), the new config.vanished_sources [FAIL] shows a source id in clear (T42 cluster), the stopped copy's count
fix-up misreads counts of 10 or more (T43), an unreadable earlier-carried patch is counted as missing (T24), doctor's
bundled-pandoc fix drops install.sh's lock and TLS settings (T59, T65), and the ticker's trap drops the pandoc start line
under load (T41, T79). Before you land a new stop, exit code, wait or person-owned fix, test it on a Mac in the state it
is meant for AND on one where it must not fire, and grep the other readers of its words (install.sh, the report, the
guides).

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r16-<lane>.md`.
