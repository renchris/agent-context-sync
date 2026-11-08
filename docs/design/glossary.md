# Glossary

The one list of agentsync's and install.sh's nouns. Each has its meaning, its identity (what names one instance, in
the forms a command accepts) and the words not to use for it. A banned word stays where a caller reads it: the runtime
strings under "Pinned words" are read by the setup prompt, `agentsync setup-report` or the tests, and they stay.

## folder

- **Meaning:** a directory on this Mac, usually inside `~/Library/CloudStorage/<provider>`. A folder is not a source
  until it is added to the config.
- **Identity:** its path, absolute or starting `~/`, as `install.sh --source-local FOLDER` and `agentsync add-source`
  take it. `install.sh --list-folders` prints the candidates in that form.
- **Banned synonyms:** directory (in output), drive, library, source.

## source

- **Meaning:** one `[[source]]` entry of `sources.toml`, which agentsync reads from. It has one of five kinds
  (`SourceKind`): `local` and `inbox` are folders; `graph_drive`, `graph_mail` and `graph_teams` are not.
- **Identity:** its `id`, as `--source ID` and `agentsync hold ID` take it. `add-source` derives the id from the folder's
  name.
- **Banned synonyms:** folder (a source need not be one), connector, feed, input.

## LaunchAgent

- **Meaning:** one of the two macOS jobs, one for the poll and one for the reconcile, that `agentsync install-agent`
  installs and starts. `agentsync uninstall-agent` removes them.
- **Identity:** its label, `com.agentsync.poll` or `com.agentsync.reconcile`. No command selects one alone.
- **Banned synonyms:** agent (alone), daemon, service, cron job.

## background sync

- **Meaning:** the runs those LaunchAgents start with no one at the terminal. It is optional and the operator's choice:
  `install.sh --confirm-install-agent` turns it on.
- **Identity:** one per Mac and config, so nothing selects it. Output names it `background sync: <state>`.
- **Banned synonyms:** daemon, autosync, the agent.

## coding agent

- **Meaning:** the tool, such as Claude Code or Cursor, that runs the setup prompt for the person, or reads the docs
  repo at work.
- **Identity:** the `prompt vN, <your tool and model id>` value of `install.sh --log-start AGENT`.
- **Banned synonyms:** setup agent, agent (alone), bot, assistant.

## agent step

- **Meaning:** install.sh's step that installs the LaunchAgents, with `--confirm-install-agent` only. "Agent" here is the
  LaunchAgent, never the coding agent.
- **Identity:** `step=agent` in install.log. No option runs it alone.
- **Banned synonyms:** install step, launchd step.

## docs repo

- **Meaning:** the git repository of markdown that agentsync writes and a coding agent reads, by default
  `~/agent-context/docs`.
- **Identity:** the `docs_repo` path in `sources.toml`; `purge docs=<glob>` selects inside it.
- **Banned synonyms:** mirror (that is its `docs/mirror/` part), wiki, knowledge base, vault.

## manifest

- **Meaning:** the SQLite database in agentsync's state folder that records every item, its outputs, tombstones, cursors
  and runs. It decides what changed before a file is read.
- **Identity:** one per state folder, so nothing selects it. An item in it is named by a selector.
- **Banned synonyms:** index, database (alone), cache, the state.

## hold

- **Meaning:** a legal or records hold: while it is set, purge and compaction stop for its scope.
- **Identity:** its scope, `all` or a source id, as `agentsync hold SCOPE` and `--release` take it.
- **Banned synonyms:** lock, freeze, pause.

## selector

- **Meaning:** what names the items a `purge` acts on, as `purge SELECTOR` and `purge --forget SELECTOR` take it.
- **Identity:** `id=<stable_id>`, `path=<source glob>` or `docs=<docs-repo glob>`. Bare text holding `*`, `?`, `[` or `/`
  is a path glob, and other bare text is a stable id. `--source ID` narrows any of them.
- **Banned synonyms:** filter, pattern, query.

## setup report

- **Meaning:** the markdown file that says how a setup went, written by `agentsync setup-report`, or by install.sh's
  own fallback when no agentsync can write it. It is redacted, for the person to review and bring back.
- **Identity:** its path, `$AGENTSYNC_SETUP_REPORT`, else `~/agent-context/setup-report.md`.
- **Banned synonyms:** log (install.log is the log), diagnostics, bug report.

## Pinned words

These runtime strings use the bare word, and stay, because a caller reads them:

| Text | Who "agent" is | Read by |
| --- | --- | --- |
| `not a step for the agent` | the coding agent | the setup prompt, `setup_report.py`, tests |
| `the agent step` | the LaunchAgent | install.sh's output, tests |
| `step=agent` | the LaunchAgent | install.log, `setup_report.py`, tests |
| `background sync: ...` | (no agent word) | the setup prompt, `setup_report.py`, tests |
