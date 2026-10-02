# J — TextQL's Ontology (GA, 2026-10-01): what transfers to agentsync's curated layer

Axis: TextQL's "file tree as business knowledge for agents" ([GA post](https://textql.com/blog/ontology-ga),
[v3 announcement](https://textql.com/blog/ontology-v3), [docs.textql.com](https://docs.textql.com), the public
`TextQLLabs/workshops` repo), read against agentsync's `topics/` layer. Researched 2026-10-02 in a 12-agent workflow:
four extractors (81 mechanisms), one comparison, and one refuting skeptic per candidate.

**Headline:** TextQL's design converges on the one agentsync already has: a git-versioned file tree, an index the
agent reads first, focused files opened on demand, and capture-through-use. On staleness and provenance agentsync
is ahead, because every claim pins a `rendered_sha256` and goes STALE when its source changes; TextQL documents no
equivalent. What transfers is **authoring discipline for the agent that writes `topics/`**, not architecture.

## Brought over (each verified by a skeptic, then shrunk to its smallest form)

| TextQL mechanism | agentsync change |
|---|---|
| `refinery init/update` refreshes only files it installed and never overwrites a hand edit | `publish.py`: an unedited earlier seed of `topics/CLAUDE.md` is upgraded on the next sync (`_TOPICS_CLAUDE_MD_PRIOR_SHA256`); a hand-edited one is kept. Before this, the live repo's guide had been missing the `curate-queue` and `.tmp` rules since `3d4b211`. |
| save-to-ontology skill: search before saving, never -v2/-final copies, edit reference knowledge in place, write dated findings as new files, link instead of copying; when sources disagree, name the winner | Rules in `topics/CLAUDE.md` and the installed skill's curate step |
| Navigation table phrased in the users' own words ("when asked about X, go to Y") | `purpose:` required, `aliases:` asked for; lookup step greps `SYNONYMS.tsv`, then `topics/`; non-blocking `MISSING-PURPOSE` finding |
| One topic per file, organised for retrieval | 400-line / 25 KB page budget in the guide; non-blocking `TOPIC-BUDGET` finding |
| "Golden" (verified) assets | `reviewed_at:` shown in INDEX as `[reviewed <date>]`; an agent edit removes it |
| Lift eval: write questions and answers first, record a baseline with the ontology off | Pre-pilot step in the implementation plan: about 10 questions before the first topic page |

## Already present (18), for example

File tree in git; an always-loaded entry point (root `CLAUDE.md`/`AGENTS.md`); per-area INDEX regenerated every
sync; progressive disclosure under a budget; skills in Anthropic's format (`install-skill`); provenance per claim;
machine-owned `mirror/` with curated `topics/` kept separate; stage-then-rename writes; full history and snapshots;
migration of hand-made pages (`adopt`); incremental revalidation from recorded dependencies (`DEPENDS.tsv`).

## Not applicable (20), for example

`.tql` (a typed-parameter SQL language for warehouses; agentsync has no query runtime); bidirectional git sync to a
SaaS (sync here is one-way, and `data-governance.md` refuses an unapproved remote); propose/approve with
folder-scoped reviewers, RBAC, code owners (one writer on one Mac); warehouse discovery; usage telemetry (needs
instrumentation inside the coding agent); runnable `.py` beside definitions (widens the prompt-injection surface in
a repo full of third-party text).

## Rejected after verification

- **Capture an operator-stated fact as an inbox note, then cite it.** The inbox withholds files inside its
  quiescence window (`arm_local.py` `InboxArm.scan`, 60 s), so the cited page does not exist on that sync. Worse,
  a self-written note cited as `role: primary` looks like corporate evidence. `provenance: hand-written` already
  gives such pages a labelled home.
- **The GA benchmark as evidence** (45 playbooks, median duration −12%, tool calls −7%). TextQL's own methodology
  note says the results "do not isolate the Ontology's effect". It motivates the pre-pilot baseline, nothing more.
