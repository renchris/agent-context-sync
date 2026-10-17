## Common brief for every B13 round 13 lane (agent-context-sync)

Read `~/.cache/agentsync-bring-back/b13/lanes/COMMON.md` first: its rules (including the new ones on `git stash`
and the words check-names flags), method, land and finish steps all apply unchanged. This file states only what
differs in round 13.

Context: plan `docs/plans/bring-back-ocr.md`, sections "B13 round 12 landed" and "B13 round 13" (the v21 rehearsal,
cross-lane review and harvest whose confirmed findings you build). Main is at or after the commit that reserved
CONTRACTS §16.104 to §16.109.

Specs: each item is `~/.cache/agentsync-bring-back/b13/round13/items/<id>.md`. It holds the finder's evidence and
fix, then an adversarial verifier's evidence and corrected fix. Build the verifier's corrected fix. The finder's
full report is the file named under "Source" (`~/.cache/agentsync-bring-back/b13/round13/*.md`): read the part
about your item. Two items that describe one defect from two finders (round 13: O1, O5, O29, O39, O42, O58 and O66 on a sign-in stop named "OneDrive" whatever the app; O41, O48,
O52 and O57 on a SharePoint library read as signed out; O40, O43 and O59 on which click counts as the sign-in; O7
and O26 on a bring-back.md that is a folder; O54 and O68; O61, O62 and O71; earlier rounds are in COMMON5.md to
COMMON11.md) are built once; say so.
Some sit in two lanes: the lane that owns the primary file builds it, and the other cites the sha.

Where the item says "a fix makes things worse? True": first look for a fix that does not (the verifier's
corrected fix often is one). Build it if you find one. Otherwise record the measured reason in your note, and
that item is done as not-built. "Cosmetic" or "small" is not a reason.

Scope (frozen): every item id your lane lists is built (a test that fails without it) or carries a measured
reason a build makes things worse; CONTRACTS text in your lane's reserved section (§16.104 to §16.109); gate green;
landed through /ship; CI read back green.

Prompt text: only lane P edits the README setup prompt block. It bumps the prompt to v22 once (README block,
PROMPT digest pin, SETUP_PROMPT_COMPAT and every "v21" the installer prints). A lane whose fix needs prompt words
sends them to lane P with cc-notify (lane P's session id is in its fire message) and records that in its note.

Bare commands: every runnable command the code prints names `~/.local/bin/agentsync`, never a bare `agentsync`
(round 4's Q49); round 5 found new bare ones in several lanes' text.

Dated lines: a dated line in any section other than your item's own (§16.104 to §16.109) begins "Amended
<date>, §16.NN O<n>". A line that records another round's change reads "Amended <date>, §16.NN O<n> (by §16.NN
<item>)"; never stamp another round's item id as if it were yours (round 5 F85).

Field compatibility: install.sh runs Python through the INSTALLED agentsync, which on a field Mac is older than
the checkout (round 7 H7: a new import broke step 1 on every Mac set up before round 6). A new name that install.sh's
Python snippets import must fall back cleanly when the installed build lacks it; test that against an older build.

Dated-line test: since round 9 K80 the gate reads every "(O<n>; amends §A X)" heading in §16.68 onward,
including possessive "§A's X" targets, and fails unless the target paragraph has the dated line.

Dated-line completeness (round 12 lane D): every dated line from §16.92 on must be claimed in its section or
pinned by a test; after a rebase, claim or pin each one you added.

Gate: the repo's `scripts/gate.sh`, never a hand-copied script. While you build, pre-gate with
`scripts/gate.sh --affected` (every static step, then the tests your diff can turn red; `scripts/gate.sh --lane`
is the full set, resumed where a killed run of the same tree stopped). Before every push run the land gate,
`scripts/gate.sh`: the full CLAUDE.md list, the full test set, and it ends `GATE GREEN at <sha>`.

Pushes: `git push` stops at an "ask" permission prompt and waits for the operator. Run the push as its own short
command (`git push origin HEAD:main`), not inside a compound command, and right after a fresh fetch and a green
land gate (`scripts/gate.sh`), so one Yes lands it. In rounds 8 to 12 every queued push after the first was
rejected once another lane landed: fetch, rebase, re-run `scripts/gate.sh`, then push again as its own command.

Trunk before you push (round 11 M87): right after the final fetch and green gate, and right before the push, read
the runs for origin/main's head: `gh run list --commit "$(git rev-parse origin/main)"`. If one is queued or in
progress, wait for it with `gh run watch <id> --exit-status` (ci cancels in-progress runs on main, so pushing on top
would hide its result); then fetch again and, if main moved, rebase, re-run `scripts/gate.sh` and repeat. If trunk is red, do
not edit failing tests outside your lane's files: message the lead and the lane that landed them with the run id and
the tests (`gh run view <id> --log-failed`), then wait for the owner's fix, or land on top only when your diff does
not touch those tests and record the red run id in your note. The lane that landed a test that is red on CI owns its
fix until main is green, even after other lanes land on top. Tests must also pass on the CI runner's git and tool
versions, not only on this Mac (round 10: two v19 tests passed here and failed there).

Makes things worse: 7 round 13 items carry "a fix makes things worse? True" (O9, O30, O35, O51, O53, O59, O74).
For most, the verifier's corrected fix is one that does not.

Regressions: round 12's three turns came from round 11's new sign-in exit (M4), and round 13's wrongs cluster on
its wording. Before you land a new stop, exit
code or wait, test it on a Mac in the state it is meant for AND on one where it must not fire.

Result note: `~/.cache/agentsync-bring-back/b13/lanes/r13-<lane>.md`.
