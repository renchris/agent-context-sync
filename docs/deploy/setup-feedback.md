# Setup feedback: from one Mac's setup to a one-command setup

The goal of the [one-prompt setup](../../README.md#set-up-on-a-new-mac-one-prompt) is a single paste and a single
command. Every question, wait, retry or manual step on a real Mac is a bug against that goal, including one the agent
worked around. This page is the loop that finds those bugs and closes them: a report is produced on the Mac, reviewed
by the person who ran the setup, sent, and triaged into fixes.

## 1. How a report is produced

Three sources feed one file, `~/agent-context/setup-report.md`:

| Source | Written by | What it knows |
|---|---|---|
| `~/agent-context/setup/install.log` | [`scripts/install.sh`](../../scripts/install.sh), on every real run (never `--report-only`) | per run: the setup-prompt-compat number, install.sh commit, arguments; per step (uv, agentsync, launcher, config, status, first-sync, agent, wait, report; list-folders for a `--list-folders` run): UTC start, seconds, exit status, done / skipped / failed; the run's exit status and total |
| the machine sections | `agentsync setup-report --out ~/agent-context/setup-report.md`, which `install.sh --report-only` runs (step 3 of the prompt; `install.sh` also writes the report at every exit) | macOS and MDM enrollment, Command Line Tools, shell and terminal, uv, Python, pandoc, OneDrive and Company Portal versions, proxy mode, whether this terminal can list `~/Library/CloudStorage`, `~/.local/bin` on `PATH`; the installer runs; the configuration (counts only); doctor's lines that are not ok and the names of the ok checks; status; the LaunchAgents' runs and last exit codes and the launcher's `TCC_*` lines; the last 40 WARNING/ERROR log lines |
| `~/agent-context/setup/friction.md` | `install.sh --log-start` and `--log`, which the coding agent runs as it goes, and `--report-only`, which closes the attempt (the attempt starts in step 1's command, so the log survives a session that dies; always appended to, 0600 in a 0700 folder, so a second attempt keeps the first) | per attempt, three header lines from `--log-start '<agent>'` (`Attempt: <UTC time>`, `Prompt: v7`, `Agent: <tool and model id>`), then one line per event from `--log '<step>' '<kind>' '<what happened>' '<fix>'`: `<time> \| step <n> \| <kind> \| what happened \| what would have avoided it`, with `<kind>` one of the six in section 4 (a failed command's line gives its exit code; a `prompt` line gives better wording). The steps themselves are not logged: install.log times them. `--log` with other than four values, or a kind not in the list, still logs the event as an `error` line and exits 2. The last line is `<time> \| end \| finished`, which `--report-only` in step 3's command appends just before the report when the attempt has none (until 2026-10-05 a separate end-line command) |

The machine part is read-only and bounded: no network (doctor runs without its Graph probe), no sudo, no prompts,
no `tmutil`, every command with a timeout and the whole report within 12 s (`TIME_BUDGET_S`). Each section records its own failure
("This section failed: ...") instead of stopping the report, so a Mac where setup broke half-way still produces one.
The command exits 0 unless `--out` cannot be written; then it prints the report and exits 1. It embeds
`friction.md` with the same redaction, takes the prompt version and agent from the last attempt's header, and works
out the outcome, the run type and the times itself (section 4), so the agent states none of them and re-running it
after the agent's last friction line is always safe. The prompt makes the report its last command, so no friction
line is written after it.

Setup stopped before agentsync was installed (steps 1 and 2)? These reports matter most, and step 3 still produces
one: every failure goes to its report, `~/src/agent-context-sync/scripts/install.sh --report-only`, which needs no
agentsync (prompt v6 ran it after `agentsync it-request`, whose failure a `;` let through; v7 drafts no IT request).
Without agentsync, `--report-only` writes
`~/agent-context/setup-report.md` from what exists (install.log, `friction.md`, the macOS facts). Review it as in
section 2. If the checkout is missing too (step 1 stopped before the clone), there is no installer and no friction
log, and the agent tells you setup stopped before the code was downloaded. Without an agent, run step 3's command
yourself, and log what happened with `install.sh --log` first (or write `friction.md` in your own words).

## 2. Review (the person who ran the setup)

Redaction is on by default and consistent, so the same value is always the same placeholder:

| Placeholder | Replaces |
|---|---|
| `~` | the home folder path |
| `<user>`, `<name>` | the login name and the full name (`id -F`) |
| `<org-N>` | the organization in `OneDrive-<org>`, `OneDrive-SharedLibraries-<org>` and `OneDrive - <org>`, the tenant's and SharePoint host's first label |
| `<library-N>`, `<folder-N>`, `<source-N>` | SharePoint library names; each configured folder name below `CloudStorage/<provider>/`, and a configured folder elsewhere under the home folder from its project folder down; every configured source id, except agentsync's own words such as `inbox` or `mail` |
| `<path>` | an item's path or a document's name in one of agentsync's WARNING or ERROR log lines (Recent errors, and the installer output's tail) |
| `<email-N>`, `<guid-N>`, `<serial>`, `<host>`, `<proxy-N>` | email addresses, GUIDs (client, tenant and volume ids), the serial number, the Mac's host name, proxy hosts |

The "Redaction" section gives the count per kind. The agent's `friction.md` is redacted with the same mapping
when the report embeds it, so the agent should not invent its own placeholders. Before sending, read the whole file
once. Redaction only covers values agentsync knows about, so a project or customer name it has never seen (inside a
log line or in the agent's words) is yours to remove. The CLI always redacts.

## 3. Send

- **Public:** a new [Setup report issue](https://github.com/renchris/agent-context-sync/issues/new?template=setup-report.yml)
  ([form](../../.github/ISSUE_TEMPLATE/setup-report.yml)). Step 3's `install.sh --report-only` prints, just before
  its `NEXT:` line (as `agentsync setup-report --out` does as its last line),
  `issue link (review the report first): <link>`, a link that opens the form with the Summary's four values
  already in their fields (the table below). Paste the whole redacted file into
  the Setup report field and tick the review box. Opened another way, copy the four values from the Summary. The
  form shows the report as a code block. The report fences command output with `~~~`, which the form's backtick
  fence does not break. Keep the agent's additions to `~~~` too.
- **Privately (no public post needed):** copy `~/agent-context/setup-report.md` back to the machine you administer
  this setup from (AirDrop, a USB stick, a shared folder you already use). Whoever looks after agentsync for your
  team triages it there with this page, exactly like an issue; that needs no contact with the agentsync maintainer.
  To hand a report to the maintainer without a public post, the optional route is the contact on the maintainer's
  GitHub profile ([github.com/renchris](https://github.com/renchris)); no email address is published in this
  repository. Keep the report redacted. A privately
  sent report is filed as a public issue, with the organisation removed, only if the person agrees. The issue form's
  "Send a setup report privately" link points here.

The form's field ids are stable, because the pre-filled link names them
(`https://github.com/renchris/agent-context-sync/issues/new?template=setup-report.yml&title=...&outcome=...&run_type=...&prompt_version=...&agent=...&loop_stage=...`,
each value URL-encoded; `title` is the issue title, "Setup report: " and the outcome, run type and prompt version):

| Field id | Type | Value |
|---|---|---|
| `outcome` | dropdown | the computed outcome, exactly one option: `Fully one command`, `Worked with help`, or `Failed at step <n> (<step title>)` with the README step's title (`preflight`, `install and start`, `IT request and report`, `finish`) |
| `run_type` | dropdown | the computed run type: `Real Mac`, `Sandbox` or `Sandbox with simulated launchd` |
| `prompt_version` | input | the prompt version, `v7` (v6 and v5 attempts still report theirs) |
| `agent` | input | the attempt header's `Agent:` value |
| `loop_stage` | input | the stage on the Summary's `Loop:` line (below); empty for a report from before that line |
| `report` | textarea | not in the link (too long for a URL): pasted |
| `review` | checkboxes | ticked by the person |

GitHub documents the field id as the key for pre-filling a form field from the URL. It does not document pre-filling
a dropdown, so if Outcome or Run type opens empty, choose the option the Summary names.

Nothing is sent automatically. Neither the prompt nor agentsync uploads, emails or posts the report.

## 4. Triage (maintainer)

Read in the report's own order: the Summary (computed outcome and run type, prompt version, agent, times), the
friction lines, then the machine sections that explain them (Installer timings for slow or failed steps, Doctor
`[FAIL]`/`[warn]`, Background runs' last exit code and `TCC_*` lines, Recent errors). A friction line is referred to
as `F<n>`, where n is its line number in the embedded friction.md (the Agent friction log section gives each
attempt's line range).

### Friction kinds

The agent gives every line one kind from a closed list, so the report counts turns from the kind column and never
guesses from the wording. Prompt v6 logs only what the prompt did not foresee: not the steps (install.log times
them), not the folder question and not the Allow clicks it announces. The `Attempt:` header and the
`end \| finished` line come from `install.sh --log-start` and `--report-only` and are not kinds. A v5 log's `start` and
`end` step lines are still read, for its step times:

| Kind | Meaning | Counts against "fully one command" | Usual fix class |
|---|---|---|---|
| `question` | the agent asked the person something other than which folders to sync | yes (v6 never logs the folder question; in a v5 log, the first question in its step 2 is that question) | prompt wording |
| `click` | the person clicked something other than an Allow the prompt announced | yes (v6 never logs the announced Allow clicks, section 5; in a v5 log, the first click in its steps 2 and 3 are those) | installer automation, or unavoidable OS step |
| `approval` | the agent's tool asked the person to approve a command (only when the agent can see it) | yes: the README's pre-allow rules avoid it | prompt wording (a pre-allow list) |
| `deviation` | the agent did something the prompt did not say, or worked around a problem | no: agent friction, counted on its own Summary line | prompt wording or installer automation |
| `error` | a command failed; the line gives its exit code | only when it stopped the run (below); otherwise agent friction | installer automation or agentsync code |
| `prompt` | the prompt was wrong or unclear; the line gives better wording | no: agent friction | prompt wording |

A tool approval the agent cannot see is not in the log. For tools that do not tell the agent when they ask the
person to approve a command (Claude Code, GitHub Copilot CLI), the report prints approvals as "not observable" when
none are logged, not as zero.

### The computed outcome

setup-report works the outcome out from what the person saw and did, never from the agent's words, and the
form's Outcome copies it. It reads the last attempt in friction.md and that attempt's last install run in
install.log (a `--list-folders` run is not an install run):

- **Fully one command**: the last install run exited 0, and the attempt has no `question` line and no `click`
  line (prompt v6 logs neither the folder question in step 1 nor the Allow clicks it announces in steps 1 and 2,
  and v7 neither that question nor the one Allow click it announces in step 1, so every logged one is beyond
  them), no `approval` line, no error that stopped the run, and doctor shows no
  `[FAIL]`. An attempt with no event line at all is the expected case.
- **Worked with help**: the last install run exited 0, but one of those conditions fails; the Summary says which.
  Without a friction log, or without the attempt's `Attempt:` line (from `install.sh --log-start`), the human
  turns are unknown, so the outcome is at best this one.
- **Failed at step N**: the last install run did not exit 0 or has no end line (stopped early); then N is 2, the
  step that runs it. When it exited 0 but an `error` line stopped the run, N is that line's step: an error logged
  in step 1 or 2 that no later install.sh run ending 0 resolved (for step 1 any run, a `--list-folders` re-run
  included; for step 2 an install run), with no later step logged before the report. In prompt v7, step 2 is the
  one install.sh command, so any install run of the attempt that ended 0 resolves a step 2 error, whenever the
  line was logged: a step 2 `error` line written after install.sh exited 0 is agent friction. If no install run
  happened, N is the step of the last `error` line, failing that the last step logged, and failing that 1 when
  only `--list-folders` ran.

A line logged after an attempt's `end | finished` line cannot have stopped it, so it never changes that
attempt's outcome. When a later `Attempt:` header follows it in the log, it came from a session whose step 1
stopped before `install.sh --log-start` wrote a header: the report lists it as an attempt of its own, with "no
Attempt: line (logged after the previous attempt finished)". With no later header it stays in its attempt (run
`install.sh --report-only` again to put it in the report).

`deviation` and `prompt` lines, and `error` lines that did not stop the run, are agent friction: the Summary counts
them on their own "agent friction" line with their F-ids, and they do not change the outcome. They are still
triaged like every other line (below).
- **Failed at step 4 (finish)** is never computed. Only the person sees whether the closing lines came, so
  they pick it by hand. With no friction log and no install.sh run, the outcome is "unknown", and the person picks
  the option too.

An attempt whose header says `Prompt: v5` is judged with v5's six steps: its folder question is the first
question in its step 2, its Allow clicks the first click in its steps 2 and 3, a later `end` line of its step
resolves an error, and N is 3 for the installer. The form has v6's steps, so the Outcome option maps v5's steps
1 and 2 to 1, 3 to 2, 4 and 5 to 3, and 6 to 4. A `Prompt: v7` attempt has three steps, numbered as v6's first
three (step 3 runs the sync loop, then the report; there is no IT request step), so its N maps to the same option.

The outcome judges only the install. How far the loop got after it is the Summary's `Loop:` line (KISS K16b):
`Loop: <stage>; NEXT: <the loop's current NEXT line>`. The stage is `installed` (no sync ran), `synced`,
`baseline drafted`, `baseline confirmed`, `before run`, `topics N` (N curated pages) or `after run`, computed from
status's loop line; the NEXT line is the one `agentsync status` would print, with every path cut to its last part
(`~/.local/bin/agentsync` is `agentsync`). So a setup that synced and stopped before drafting the baseline
questions reads "fully one command" with `Loop: synced; NEXT: draft the baseline questions: ...`. When
something waits on the person, the line ends with the first `WAITING ON YOU:` line and `(+N more)` for the
rest; `agentsync status` prints them all. The line holds no path, so redaction is unchanged; its stage prefills
the form's Loop stage.

The run type is computed too:

- **Sandbox with simulated launchd**: the last install.sh run says `launchd=simulated`.
- **Sandbox**: HOME, or what it resolves to, is under `/tmp`, `/private/tmp`, `/var/folders` or
  `/private/var/folders`.
- **Real Mac**: neither of the above.

The agent's own opinion is not asked for.

### Fix classes

Map **every friction line** whose kind counts (and every `approval` line), by its `F<n>`, to one fix class:

| Fix class | Signal in the report | Where the fix lands | Proof it stays fixed |
|---|---|---|---|
| **prompt wording** | the agent asked, guessed or did something unasked; "unclear, wrong or missing" lines | the README one-prompt block | `tests/test_deploy_pack.py` (the block's version and compat line, step 1's one command and its folder listing on fixtures, step 3's one command, the `--log` template under bash and zsh, every command and flag it names, the pre-allow rules) |
| **installer automation** | a step the agent or person did by hand that a script could do; slow or failed install.log steps | `scripts/install.sh` | `tests/test_launcher.py` (stubbed runs, the setup log) |
| **agentsync code** | a doctor `[FAIL]` without a working fix, a wrong status, an error in Recent errors | `src/agentsync/` | a unit or integration test that reproduces the report's lines |
| **IT pack** | MDM, Conditional Access, proxy/TLS inspection, Command Line Tools blocked by policy | [it-request.md](it-request.md), [mdm/README.md](mdm/README.md), this folder | the page names the exact setting and who sets it |
| **unavoidable OS step** | macOS requires a person: an Allow click, a sign-in, an admin password | the prompt says it up front and waits; the list below | the step is in the list, with its evidence |

The rule: **every line whose kind counts becomes either a fix or a documented unavoidable step.** No line is
closed as "won't fix" or "user error". When the person had to help, the setup had a gap. Close the issue with one
comment, one line per `F<n>`, in this form:

```text
Prompt v<N>, run <Real Mac | Sandbox | Sandbox with simulated launchd>, outcome <outcome>, loop <stage>.
F1: prompt wording -> <commit>, test <tests/test_file.py::test_name>
F2: installer automation -> <commit>, test <tests/test_file.py::test_name>
F3: unavoidable: <the list item below, and the evidence: a macOS dialog, an MDM setting, an IT consent>
F4: known K<n> (<commit>)
```

A line that repeats a known item (below) closes as `known K<n>` without being triaged again.

**A sandbox or simulated run never counts as a success metric.** Its friction lines still become wording or
installer fixes, but only runs whose computed run type is "Real Mac" count in the measures: the share of "fully one
command" outcomes, the questions, clicks, approvals and deviations per report, and the install.log totals per step.
"Fully one command" means no human turn beyond the paste and the current unavoidable steps (section 5), as computed
above.

Maintainer setup, once: commit the form, this page and the README block together, so the report's links resolve on
GitHub, and create the form's label with `gh label create setup-report` (a form's label that does not exist is not
applied).

### Known friction

Items already fixed or documented. The evidence is the sandbox validation runs of the earlier prompts, the
maintainer's review of the two v4 runs (its J-numbered findings), the review of the two v5 runs (its own K-numbered
findings, named as "v5 review, its K<n>") and the review of the two later v5 runs (its L- and V-numbered findings,
named as "v5b review, its L<n>").

| Id | Friction | Fix class | Answered by |
|---|---|---|---|
| K1 | step 3's `ls -d` folder glob exited 1 when no depth-3 folder existed, so the agent said OneDrive was not signed in; in v4, a terminal denied access to OneDrive read as "not signed in" too (J9) | prompt wording | v5 step 2 (v6 step 1) runs `install.sh --list-folders`, which exits 3 for no OneDrive folder and 4 for a denied terminal; `test_list_folders_*` |
| K2 | the friction log lived only in the agent's context, and both agents invented an unredacted file in the home folder | prompt wording | v4 names `~/agent-context/setup/friction.md`; since v6, step 1's `install.sh --log-start` starts it; `test_readme_step1_command_clones_then_pulls` |
| K3 | step 9 said to append a friction heading the report already had, so the heading was duplicated | prompt wording | since v4 the report embeds `friction.md`; no heading to write; `test_readme_setup_report_steps_match_the_code` |
| K4 | the installer guard passed while `setup-report` was not published | installer automation | `install.sh --version` prints `setup-prompt-compat <N>`; `test_readme_prompt_compat_matches_the_installer` |
| K5 | turns were counted by keyword (negations too), and the two agents used the clean / needed help status in opposite ways (J1, J2) | prompt wording | v5 lines carry one kind from a closed list (section 4); `test_readme_friction_line_format_and_kinds` |
| K6 | the outcome and run type were the agent's own statement, and the form could not say "sandbox with simulated launchd" (J3, J14) | agentsync code | v5 computes both (section 4); the form's run types; `test_setup_report_form_has_a_run_type` |
| K7 | friction lines written after the report never reached it (J4) | prompt wording | v5 appends `end \| finished`, then runs the report as its last command; `test_readme_report_is_the_last_command` |
| K8 | the IT request was drafted by hand in about six actions, with a guessed start, dead relative links and a false "byte-identical" claim (J10) | agentsync code | `agentsync it-request --out ~/agent-context/it-request-draft.md`; since v7 the prompt drafts no request, and the loop's `NEXT:` names that command only when the network refuses Microsoft Graph; `test_it_request_placeholders_are_in_its_top_table` |
| K9 | a second attempt overwrote the first attempt's friction log or repeated its header (J5) | prompt wording | v5 appends an `Attempt:` header block per attempt; `test_readme_friction_log_two_attempts` |
| K10 | the per-line friction command double-quoted the agent's words, so a backtick or `$(...)` in them ran as a command (v5 review, its K7) | prompt wording | the four values are single-quoted, with ’ for an apostrophe; `test_readme_friction_line_never_runs_the_agents_words` |
| K11 | every distinct command could cost a tool approval, and no pre-allow list was shipped (v5 review, its K11 and K12) | prompt wording | step 1 is one command; the README's "Fewer approval prompts" rules; `test_readme_step1_is_one_command`, `test_readme_pre_allow_rules_cover_every_command` |
| K12 | the pack told the person to reset the privacy grant with a Terminal command after a denied Allow, while the installer's `NEXT:` line names the System Settings toggle (v5 review, its K13) | IT pack | the Files and Folders toggle everywhere; `test_denied_access_remedy_is_the_files_and_folders_toggle` |
| K13 | every friction line was a `printf ... >> ~/agent-context/setup/friction.md`, and Claude Code asks for every redirect target that starts with `~`, whatever the allow rules say (v5b review, its L4) | prompt wording | v6 logs with `install.sh --log`, which appends at 0600; the block has no redirect; `test_readme_block_redirects_to_no_file`, `test_readme_friction_line_never_runs_the_agents_words` |
| K14 | the block took six tool calls, each a possible approval: the friction header, preflight, the folder list, the IT draft, the end line and the report (v5b review, its L5) | prompt wording | v6 has three: step 1 (preflight, code, `--log-start`, `--list-folders`), step 2 (install) and step 3 (IT draft, the end line, report; since 2026-10-05 `--report-only` writes the end line itself); v7's step 3 is the sync loop, whose `sync`, `curate` and `status` have exact pre-allow rules, then the report alone; `test_readme_step1_is_one_command`, `test_readme_report_is_the_last_command` |
| K15 | the `start` and `end` kinds could not be followed literally (step 5's `start` never had an `end`), and the Summary's event count did not add up (v5b review, its L9 and V2) | prompt wording | v6 has no step lines: install.log times the steps and the kinds are the six in section 4; `test_readme_friction_line_format_and_kinds` |
| K16 | the setup ended at the install, and the loop (baseline questions, curation) lived in a second prompt that was never pasted on the field Mac (KISS K04) | prompt wording | v7 replaces both prompts: step 3 runs `agentsync sync` and follows its `NEXT:` line until the session is done or waits on the person, then the report; `test_readme_step3_runs_the_loop_then_the_report` |
| K17 | no prompt said where the inbox was, what to drop in it or that emptying it erases pages, and on the field Mac the agent's own exporters read other apps' private stores and drove the browser, which raised clicks (field report N3, N9, N14, N16) | prompt wording | v7 step 2 names the inbox by its `sources.toml` entries, the formats and the label caveat, and says it is never emptied; the rules say agentsync never needs `~/Library/Containers`, Group Containers or the browser; `test_readme_prompt_carries_the_field_lines` |

## 5. Unavoidable steps so far

Each one has its evidence. A report showing that one of them can be avoided moves it into a fix class above.

Current unavoidable human steps (these do not break "fully one command"):

- **Choosing the folders.** The one question the prompt asks on purpose: what to sync is the user's decision.
- **The TCC Allow click** ("wants to access files managed by OneDrive"), for the terminal (step 1). Setup no
  longer asks a second one: the click for `agentsync-launcher` comes only with the operator's optional background
  sync (`install.sh --confirm-install-agent`, KISS K11b), never in a setup run. The launcher's click goes away when MDM grants Full Disk Access to the
  Developer-ID-signed launcher ([the one-time Allow click](README.md#the-one-time-allow-click),
  [mdm/README.md](mdm/README.md)). After a denied prompt the person turns `agentsync-launcher` (or the terminal)
  on in System Settings > Privacy & Security > Files and Folders (a click the prompt forbids the agent), and the
  agent re-runs the command the `NEXT:` line names.
- **IT consent** for Outlook, Teams and unsynced SharePoint: an Entra admin must register the app and consent
  ([it-request.md](it-request.md)). `agentsync it-request` only drafts the request; the person sends it.

Moved out of this list: **Agent-tool approvals**, the commands the coding agent's tool asks the person to approve.
The operator pre-allows them in the tool's settings with the tested rules for Claude Code and GitHub Copilot CLI
under "Fewer approval prompts" in the README's [one-prompt setup](../../README.md#set-up-on-a-new-mac-one-prompt),
so a logged `approval` line makes the outcome "worked with help". Since v6 the rules cover every command in the
block: it logs through `install.sh --log` and has no `>>` redirect, which Claude Code's documentation says always
needs approval when its target starts with `~` (K13). The rules cover commands only: since v7 the loop also has
the agent write files (the baseline questions in `~/agent-context/docs/_eval/`, and in later sessions subject
pages), and each of those writes still asks in a tool's default mode, so a v7 run that reaches the draft may log
an `approval` line for them. What the rules cannot change: neither tool tells the agent about an approval
(section 4: "not observable").

Preconditions, outside the setup's count because the prompt stops at step 1 and says why:

- **Installing the Xcode Command Line Tools** when they are missing. `xcode-select --install` opens a macOS dialog,
  and a managed Mac may need an admin or Self Service.
- **Signing in to OneDrive** before setup. The OneDrive app owns that sign-in.
