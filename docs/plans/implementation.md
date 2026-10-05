---
status: in-progress
---

# Implementation plan — from design to a running sync on managed Macs

Scope (frozen, 2026-09-29): implement agent-context-sync per design §10 weeks 1–3 (local arm, SQLite manifest with the
H0/H1/H2 classifier, converters with a write-once cache, `docs/mirror` + INDEX/CHANGELOG/STATE + one git commit per
cycle, Graph drive + mail + Teams delta arm, curation `DEPENDS.tsv` + STALE lint, single-writer lock + launchd agent)
with tests; verify it end to end on this Mac (local fixture, the `OneDrive-Contoso` File Provider mount, Graph where a
sign-in allows); package it for managed Macs (no-admin install, LaunchAgent, IT request pack: Entra app registration,
configuration profile, data-governance note); fix the audit's confirmed defects; and write a dated corporate rollout
whose only open items are named operator or IT actions.

## Phase 0 — orchestration

- **Execution locus per wave:** W1–W3 run as Workflow fan-outs (the operator opted into multi-agent orchestration with
  "ultracode"; each implementer owns disjoint files in the main tree and never commits, so the lead commits one module
  at a time). The lead holds contracts, merges, commits and the close.
- **Lead context budget:** stay under 50%; succession point is after W2 merge (everything is on disk by then).

| Wave | What | Owner of files |
|---|---|---|
| W0 | Readiness audit (six dimensions, adversarial verify, critic) | read-only |
| W1a | Skeleton: `pyproject.toml`, `src/agentsync/{model,config}.py`, contracts doc, test scaffold | architect agent |
| W1b | Modules in parallel: manifest+classifier · local arm · converters+cache · publish+lints · Graph auth+client · Graph delta arms · curation · ops (lock, launchd, CLI) | one implementer each |
| W2 | Integration: CLI wiring, end-to-end on fixture + File Provider mount, review + fix | integration agents |
| W3 | Corporate pack + audit fixes + rollout plan with dates | docs agents |

## Week-0 decisions, taken as defaults (each is one config key to change)

| Decision (design §10 week 0) | Default | Why |
|---|---|---|
| Single writer | The Mac that runs `agentsync install-agent`; a `flock` on the state dir enforces one | Design §4.7 |
| Where `docs/` lives | Its own git repo, `~/agent-context/docs`, outside any cloud-synced path, **no remote** | Corporate content never leaves the machine unless IT names a tenant-owned remote |
| AGPL (PyMuPDF) | Not used. PDF text via `pdfminer.six` (MIT). CORRECTED (2026-09-29): via `pypdfium2` (Apache-2.0/BSD-3), with pdfminer.six as fallback — the design's own converter evidence supports pypdfium2 (C15 §6, commit d6a12d2) | Avoids a licence review in a corporate rollout |
| Converters | pandoc via `pypandoc_binary` (docx, html, rtf, odt), `openpyxl` emitter (xlsx), `python-pptx` (pptx), `pdfminer.six` (pdf) | All permissive licences; `pypandoc_binary` needs no Homebrew or admin |
| In-scope set | `~/agent-context/sources.toml`, written by `agentsync init` | Design §10 week 0 |
| Graph client id | Configurable; defaults to none, so the local arm works with zero IT involvement | The local arm needs no consent at all |

## Rollout — dated

The corporate pack is [`docs/deploy/`](../deploy/README.md). Every step names its owner and the command that
proves it. Test counts are **measured** unless marked otherwise.

**Operator ruling (2026-10-01): the corporate Mac runs with no IT involvement.** Every row below owned by IT (the
Entra app and admin consent, Developer ID signing, the MDM pack) is out of scope, and so is the Graph-arms row that
depends on them. What that leaves:

- **Files: complete with no IT.** `local` sources over the OneDrive client's sync folder. That covers OneDrive, any
  SharePoint library or Teams channel's Files tab synced with **Sync** or **Add shortcut to My files**, Teams chat
  attachments (the sender's OneDrive "Microsoft Teams Chat Files" folder, shared with you), and meeting transcripts
  the person has downloaded (`.vtt` and `.docx` both convert). Meeting recordings (`.mp4`) are not converted: no
  converter takes video, and no separate transcript file is documented beside a recording, so a transcript arrives
  only when the person downloads it into a synced folder or the inbox. **Corrected (2026-10-05, field N10):** this
  line used to promise "meeting recordings and transcripts stored in OneDrive or SharePoint", which the design's
  Teams correction rules out (`docs/design/agent-context-sync.md` §4.2, the CORRECTED 2026-09-29 note, item 4).
- **Mail and Teams messages: no automatic route.** Microsoft's default user-consent policy excludes `Mail.Read*`,
  `Chat.Read`, `Files.Read.All` and `Sites.Read.All` from user consent (C15, line 237), and channel messages always
  need an admin. The zero-IT route is the `inbox` source: drag messages out of Outlook as `.eml` files (converted by
  `convert/eml.py`), and share Teams messages to Outlook first. Signing in to Graph under another app's identity, such
  as the Microsoft Graph PowerShell client id (design rung (ii)), would get around the company's app-approval
  control, so it is not used. **Measured on the corporate tenant (2026-10-01):** signing in to the Softeria
  ms-365-mcp-server app with mail scopes through the browser (`--org-mode --login --auth-browser`) showed "Need admin
  approval". The device-code route looped back to an empty code page without completing. So the inbox is the mail
  route, and `agentsync add-source --inbox` now creates it.
- **Launcher:** the installer builds and ad-hoc signs it on the Mac. That needs the one Allow click and no IT,
  unless the company's device policy blocks unsigned apps or the Allow prompt (C15 §8 probe 7, measured on the Mac).

| Date | Step | Owner | Proof |
|---|---|---|---|
| **Tue 2026-09-29** (done) | Build and local verification on the dev Mac: the full suite, strict typing, lint, and the deploy pack's own checks | agent | `uv run pytest -q` → 1628 passed, 2 skipped (opt-in File Provider walk, perf budget), 1 xfailed (`content_trust`, CONTRACTS §16.11), 474.6 s, measured 2026-09-30T01:21Z while other agents were editing; `uv run mypy src` → no issues in 47 files; `uv run ruff check src tests` → clean; `scripts/install.sh --dry-run` → every step printed, `NEXT:` line; `plutil -lint docs/deploy/mdm/agentsync-pppc.mobileconfig` → OK; `shellcheck scripts/tenant-probes.sh` and `/bin/bash -n` → clean; `scripts/tenant-probes.sh --selftest` → ok (QuickXorHash also checked against all 70 rclone vectors). Not re-run by the deploy pack: the opt-in live-mount suites (`AGENTSYNC_E2E_FILEPROVIDER=1`, `AGENTSYNC_FP_TEST=1`) |
| **Wed 2026-09-30** | Install on the corporate Mac: one command, no admin | operator | `git clone … && scripts/install.sh` ends `NEXT:`; `agentsync doctor` has no `[FAIL]` |
| Wed 2026-09-30 | Local arm live: a `local` source inside `~/Library/CloudStorage/OneDrive-<Org>/` plus the `inbox`; background agents on; the one Allow click answered | operator | `scripts/install.sh --confirm-install-agent`; `agentsync status` (no `TCC_PENDING`, a last run per source); `git -C ~/agent-context/docs log --oneline -1` shows a `sync:` commit |
| Wed 2026-09-30 | IT request filed: [it-request.md](../deploy/it-request.md) + [entra-app.json](../deploy/entra-app.json); add the [MDM pack](../deploy/mdm/README.md) only if the prompt is blocked | operator | the ticket number, recorded in `sources.toml` as a comment next to `[graph]` |
| Wed 2026-09-30 | Tenant probes, if the tenant allows sign-in: a user-consentable or pre-existing app, or none (then the report says `client_id` is unset). This also starts the probe-9 clock | operator | `scripts/tenant-probes.sh` → `report: …/probes/tenant-probes-<UTC>.md`; exit 0, 77 (names the blocked state) or 78 |
| **target Fri 2026-10-09** | Entra app registration, admin consent for the arms in use, assignment required with the operator assigned | IT (Entra admin) | IT replies with the client and tenant ids; `agentsync graph login` prints `method broker` (or `loopback`); probe 2's `requested but not granted` is `none` |
| Fri 2026-10-09, escalation if IT has not replied | (1) Re-send the request with a narrower ask: files-only scopes, or `Sites.Selected` (it-request.md "Minimum sets"). (2) Escalate to the IT manager and the security owner, linking data-governance.md ("nothing leaves the Mac"). (3) Keep the local arm as the production path, since synced libraries cover most of the file scope. Record "Graph arms deferred" in `sources.toml` and revisit on Mon 2026-10-19 | operator | ticket history; `agentsync status` still green on local sources |
| **Mon 2026-10-12** | Graph arms live: `discover`, paste the tables, first FULL enumeration, then `state = "live"` | operator (agent assists with sources.toml) | `agentsync discover` exits 0 (complete); `agentsync reconcile --source <id>` exits 0; `agentsync status` shows a deltaLink age per Graph source |
| ongoing: Thu 2026-10-30, Sun 2026-11-29, Tue 2026-12-29 | deltaLink idle-lifetime probe at 30, 60 and 90 days after the first probe run. **Non-blocking:** a 410 only falls back to a full enumeration | operator | `scripts/tenant-probes.sh` → the probe-9 row for that milestone reads `200: still valid` or `410 …` |
| ongoing, weekly | Queued purges and history bound | operator (records owner for holds) | `agentsync purge --queue`; `agentsync doctor` shows `governance.compaction ok` |

## Readiness

Every **blocker** and **major** id from the 2026-09-29 readiness audit, and every design "Design gap … the
implementation resolves it". "Closed" names the commit area and the test that proves it (`tests/<file>::<test>`).
"Remaining" names the owner.

| Audit id | Status | Closed by / remaining owner |
|---|---|---|
| repo-hygiene-01 | closed, except signing | install.sh + launcher (`test_launcher.py::test_bundle_signature_identifier_and_info_plist`), deploy pack; **remaining (IT):** Developer ID signing and notarization ([mdm §1](../deploy/mdm/README.md#1-re-sign-the-launcher-with-your-developer-id)) |
| implementation-gap-02 | closed | graph/discover (`test_graph_discover.py::test_discovery_enumerates_every_kind_from_supported_endpoints`); sources.toml lives at `~/agent-context/` (CONTRACTS §14) |
| implementation-gap-03 | closed | graph/drive (`test_e2e.py::test_h_graph_drive_full_cycle_410_resync_delta_edit_and_delete`, `test_graph_client.py::test_ratelimit_headers_are_not_used`) |
| implementation-gap-04 | closed, except OneNote | graph/mail, graph/teams (`test_graph_mail.py::test_every_mail_request_carries_immutable_id`, `test_graph_teams.py::test_bootstrap_walks_the_whole_channel_and_writes_month_rollups`); attachments ride in the `.eml` (`test_convert_formats.py::test_eml_fixture_headers_body_attachments_no_base64`); **remaining (agent, deferred):** OneNote arm |
| implementation-gap-05 | closed (T3 + canary) | arm_local (`test_arm_local.py::test_walk_emits_files_sorted_with_full_stat_tuple`, `test_ops_doctor.py::test_tcc_canary_through_the_launcher`); T1/T2 watchers deferred by design (§9 probe 5: T3 is fast enough) |
| implementation-gap-06 | closed | materialise (`test_arm_local.py::test_fetch_respects_the_budget`, `test_materialise.py::test_etimedout_retries_with_exponential_backoff`, `test_classifier.py::test_scenario_budget_deferral_is_durable_across_cycles`) |
| implementation-gap-07 | closed | manifest (`test_manifest.py::test_schema_version_mismatch_raises_never_rederives`, `test_manifest.py::test_export_shard_is_deterministic_and_carries_nothing_volatile`, `test_cli.py::test_adopt_and_migrate`) |
| implementation-gap-08 | closed | classifier (`test_classifier.py::test_breaker_trips_scoped_and_floored`, `test_classifier.py::test_phase3_office_resave_h1_differs_h2_equal_cuts_off`) |
| implementation-gap-09 | closed | convert, slug (`test_convert_determinism.py::test_double_conversion_is_identical_for_every_input`, `test_policy.py::test_encrypted_office_is_an_unreadable_stub_without_running_a_converter`, `test_arm_local.py::test_inbox_withholds_files_inside_the_quiescence_window`) |
| implementation-gap-10 | closed | convert/cache (`test_convert_core.py::test_cache_is_write_once`, `test_convert_core.py::test_cache_half_written_tmp_dir_is_invisible_and_gc_removes_it`) |
| implementation-gap-11 | closed | publish, gitops (`test_e2e.py::test_a_first_sync_commits_mirror_and_index_then_noop`, `test_gitops.py::test_tag_published_force_moves`, `test_classifier.py::test_scenario_deletion_needs_a_complete_listing_then_tombstones_and_restores`) |
| implementation-gap-12 | closed, except the Skill | publish (`test_publish.py::test_changelog_append_and_index`, `test_publish.py::test_quarantine_tsv`, `test_publish.py::test_generated_guides_state_the_untrusted_boundary`); **remaining (agent):** the Claude Code Skill file. **CLOSED (2026-10-01):** `agentsync install-skill` writes it (`test_cli.py::test_install_skill_writes_once_and_names_the_docs_repo`), and `agentsync curate-queue` lists stale and uncovered pages (`test_cli.py::test_curate_queue_lists_stale_then_uncovered`) |
| implementation-gap-13 | closed in code | curate (`test_curate.py::test_design_fixture_exactly_one_stale_and_one_deleted`); **remaining (operator + agent):** the 20-page prototype on live data, after Wed 2026-09-30 |
| implementation-gap-15 | closed; managed-Mac measurement open | ops (`test_cli.py::test_install_and_uninstall_agent_call_launchd`, `test_cli.py::test_lock_held_exits_75`, `test_e2e.py::test_reachability_gate_fails_on_network_policy_and_skips_when_offline`, `test_ops_doctor.py::test_launcher_canary_probe_hard_timeout`); **remaining (operator, IT):** C15 §8 probe 7 on the managed Mac |
| implementation-gap-16 | closed | graph/auth (`test_graph_auth.py::test_keychain_persistence_uses_service_and_tenant_client_account`, `test_graph_auth.py::test_aadsts_classification_table`, `test_e2e.py::test_h_reauth_required_holds_the_graph_cursor_but_local_sources_publish`) |
| implementation-gap-17 | heartbeat closed; watcher open | ops/lock, doctor (`test_ops_lock.py::test_heartbeat_roundtrip_and_file_shape`, `test_ops_doctor.py::test_heartbeat_states`); **remaining (agent + operator):** a separate watcher job and a named alert channel |
| implementation-gap-18 | closed | lints, policy (`test_lints.py::test_land_gate_clean_then_catches_unreported_hand_edit`, `test_lints.py::test_builtin_secret_scan`, `test_e2e.py::test_label_policy_refuses_a_labelled_file_and_converts_the_rest`) |
| implementation-gap-19 | closed locally; CI open | 1628 tests (`uv run pytest -q`, measured above); **remaining (agent):** a pytest job on a `macos-15` runner in `.github/workflows` |
| implementation-gap-21 | closed, except signing | uv tool env + launcher + install.sh (`test_launcher.py`, `test_ops_launchd.py::test_plist_path_is_under_home_launchagents`); **remaining (IT):** Developer ID + notarization |
| implementation-gap-22 | tooling closed; runs open | `scripts/tenant-probes.sh` (probes 1, 2, 4b, 9; `--selftest`); **remaining (operator):** run Wed 2026-09-30 and at +30/60/90 days; probe 8 (one real Excel workbook through `agentsync materialise`) by hand |
| implementation-gap-23 | open | **operator:** the Wed 2026-09-30 pilot on the managed Mac, plus the tenant probes report |
| docs-consistency-launchd-hang-not-propagated | closed | design §4.7 CORRECTED note; launcher canary and watchdog (`test_ops_doctor.py::test_launcher_canary_probe_hard_timeout`); matrix: see implementation-gap-15 |
| corporate-macos-no-deployable-artifact | closed, except signing | install.sh, launcher, tenant-probes.sh, `docs/deploy/`; **remaining (IT):** signed launcher |
| corporate-macos-device-code-vs-conditional-access | closed | graph/auth ladder (`test_graph_auth.py::test_ladder_rung2_loopback_when_broker_runtime_is_inactive`, `test_graph_auth.py::test_ladder_rung3_device_code_only_when_allowed`) |
| corporate-macos-tcc-file-provider-unresolved | closed in code; measurement open | launcher + canary (`test_ops_doctor.py::test_tcc_canary_through_the_launcher`), PPPC template (`plutil -lint`); **remaining (operator, IT):** C15 §8 probe 7 |
| corporate-macos-data-governance-git-permanence | closed | governance (`test_cli.py::test_purge_removes_every_blob_and_the_item_never_comes_back`, `test_governance.py::test_compact_history_squashes_old_commits_and_prunes`, `test_cli.py::test_hold_suspends_purge_and_compaction_and_shows_in_status`); [data-governance.md](../deploy/data-governance.md) |
| corporate-macos-app-registration-unspecified | closed in docs; test open | [it-request.md](../deploy/it-request.md), [entra-app.json](../deploy/entra-app.json) (13 ids checked against the Graph permissions reference `47f65201`); **remaining (agent):** the C15 req 7 test (`entra-app.json` parses; exactly two redirect URIs; `AzureADMyOrg`; `isFallbackPublicClient: false`) |
| corporate-macos-signing-packaging | closed, except signing | launcher, install.sh, managed-login-items payload in the mobileconfig; **remaining (IT):** Developer ID + notarization |
| corporate-macos-label-acquisition-missing | closed for file bytes; Graph read open | policy (`test_e2e.py::test_label_policy_refuses_a_labelled_file_and_converts_the_rest`, `test_graph_drive.py::test_relabel_moves_quickxor_so_it_is_a_content_change_not_metadata_only`); **remaining (agent):** C15 req 28 `extractSensitivityLabels` |
| corporate-macos-tls-proxy | closed | net (`test_net.py::test_ssl_context_is_truststore`, `test_graph_client.py::test_pac_only_network_fails_closed_without_sending`, `test_e2e.py::test_reachability_gate_fails_on_network_policy_and_skips_when_offline`) |
| corporate-macos-uninstall-remanence | closed | governance offboard (`test_governance.py::test_offboard_dry_run_lists_exact_locations`, `test_governance.py::test_offboard_needs_the_confirm_token`) |
| probes-code-office-auto-edits-active-user-document | closed | commit `10b4235` fix(probes); `make -C probes check` |
| design-correctness-01 | closed | publish (`test_review_fixes.py::test_noop_safe_save_commits_nothing_and_page_matches_shard`, `test_e2e.py::test_h_graph_drive_full_cycle_410_resync_delta_edit_and_delete`) |
| design-correctness-02 | open | pages move on rename (`test_e2e.py::test_e_rename_moves_the_page_under_the_same_stable_id`), but curated `sources:` are not rewritten; **remaining (agent):** MOVED verdict / citation rewrite in curate |
| design-correctness-03 | closed | arm_local, classifier (`test_arm_local.py::test_rename_keeps_identity_safe_save_changes_it`, `test_classifier.py::test_match_safe_saves_pairs_same_path_only`) |
| design-correctness-04 | closed | graph/drive (`test_graph_drive.py::test_drive_select_covers_every_field_the_arm_reads`) |
| design-correctness-05 | closed for Graph; local relabel open | `test_graph_drive.py::test_relabel_moves_quickxor_so_it_is_a_content_change_not_metadata_only`; **remaining (agent):** C15 req 29 (a local relabel-only save is `TOUCHED_NOT_CHANGED`, CONTRACTS §16.11) |
| design-correctness-06 | closed | graph/teams (`test_graph_teams.py::test_bootstrap_walks_the_whole_channel_and_writes_month_rollups`, `test_graph_teams.py::test_teams_module_uses_no_delta_or_app_only_endpoint`) |
| design-correctness-07 | chats closed; transcripts deferred | `test_graph_teams.py::test_chat_incremental_uses_orderby_and_filter_on_the_same_property`; **remaining (agent, out of scope):** transcripts |
| design-correctness-08 | closed | graph/discover (`test_graph_discover.py::test_no_deprecated_or_app_only_endpoint_in_the_arms`) |
| design-correctness-09 | closed | as for corporate-macos-device-code-vs-conditional-access |
| design-correctness-11 | closed; shared-mailbox probe open | `test_graph_mail.py::test_every_mail_request_carries_immutable_id`, `test_graph_mail.py::test_sync_state_40x_resyncs_the_folder_and_is_not_corruption`, `test_graph_mail.py::test_shared_mailbox_is_marked_unverified`; **remaining (operator):** C15 §8 probe 6 |
| design-correctness-12 | mitigated; decision open | no remote by default (`test_gitops.py::test_push_is_disabled_by_default_and_needs_a_remote`, `test_cli.py::test_remote_on_the_docs_repo_is_refused`); [data-governance.md "Access"](../deploy/data-governance.md#untrusted-content-boundary); **remaining (operator + security owner):** any sharing beyond the writer |
| design-correctness-13 | closed | `test_e2e.py::test_confirmed_deletion_queues_a_purge_that_removes_the_tombstone` |
| critic-untrusted-content-injection | closed | publish, policy, slug (`test_publish.py::test_every_mirror_page_kind_carries_the_untrusted_banner`, `test_review_fixes.py::test_sg01_instruction_names_are_neutralised_after_slugging`) |
| critic-git-global-excludes-drop-mirror | closed | gitops (`test_gitops.py::test_hostile_global_config_cannot_drop_or_rewrite_mirror_pages`) |
| critic-plan-pdf-converter-contradicts-receipts | code closed; receipt open | convert/pdf (`test_convert_formats.py::test_pdf_is_pypdfium2_and_says_so`); **remaining (agent):** C15 req 35 receipt (E-converters §4.4 fixture + ×3 sha256) before any PDF converter is marked default |

| Design gap (design line) | Status | Closed by / remaining owner |
|---|---|---|
| §4.1 safe-save re-link (114) | closed | `test_classifier.py::test_match_safe_saves_pairs_same_path_only` |
| §4.1 a removal only nominates (119) | closed | `test_classifier.py::test_scenario_deletion_needs_a_complete_listing_then_tombstones_and_restores` |
| §4.2 T1 FSEvents with volume UUID (193) | deferred | **agent**, only if §9 probe 5 shows T3 too slow on the managed Mac |
| §4.2 arm A/B existence divergence (203) | open | **agent**: no cross-arm divergence state yet |
| §4.4 frontmatter vs H2 (337) | closed | as for design-correctness-01 |
| §4.5 renames break citations (400) | open | as for design-correctness-02 (**agent**) |
| §4.6 tombstones vs disposition (466) | closed | as for design-correctness-13; [data-governance.md](../deploy/data-governance.md#retention-and-deletion) |
| §4.6 untrusted content (467) | closed | as for critic-untrusted-content-injection |
| §4.6 consumers get the writer's view (475) | mitigated | as for design-correctness-12 |
| §4.7 TCC per binary + per-call timeout (478) | closed in code | as for corporate-macos-tcc-file-provider-unresolved |
| §4.7 git isolation (480) | closed | as for critic-git-global-excludes-drop-mirror |
| §5 label change is content (515) | partly | as for design-correctness-05 |

## One-prompt setup and the setup-report loop (2026-09-30, completed)

Scope (grown, 2026-09-29): +a copy-paste setup prompt for a new Mac (README "Set up on a new Mac: one prompt"),
+a redacted setup report that measures everything that was not one command and comes back as a GitHub issue.

- **State:** prompt v6, installer `setup-prompt-compat 6`, live on `origin/main` at `35c2b5f`; issue form
  `.github/ISSUE_TEMPLATE/setup-report.yml` and label `setup-report` exist. 1,849 tests green at the land.
- **Happy path (measured, sandbox, literal agent):** 3 tool calls, 1 human turn (the folder question), install 19.6 s,
  local files converted in the first sync, report outcome computed as "fully one command".
- **How it got there:** five validate→judge rounds (careful + literal agents in sandboxes, a maintainer agent judging
  the reports). Decisions that stick: the outcome is computed from person-facing facts, never the agent's own claim;
  friction goes through `install.sh --log` (a `~` redirect costs a Claude Code approval per line); the first sync
  downloads nothing (`--materialise-budget 0` now charges only online-only files); sandbox runs must set
  `AGENTSYNC_SIMULATE_LAUNCHD=1`, because launchd's gui domain is shared across `HOME`s.
- **Commits:** `3428997` add-source · `53824b3` prompt v3 · `977bc3f` budget fix · `3055724` setup-report/it-request ·
  `fb9305f` one-shot installer · `35c2b5f` prompt v6 + loop.
- **Open (minor, left for real reports to settle):** whether the step-1 compound command is covered by the
  pre-allow rules in a live Claude Code session (judge M6); step 3 could fold into step 2 (M5); the launcher build's
  Developer-ID hint still prints on install (P2).
- **Next:** the operator runs the prompt on the corporate Mac and brings the report back; each friction line becomes a
  fix or a documented unavoidable step (docs/deploy/setup-feedback.md).

## Just-in-time builds (2026-10-02)

Scope (frozen): no scheduled sync or curation by default; a work session catches up with `agentsync sync --once`,
builds, and ends with a checkpoint so the next session sees the diff since the last build.

- **Ruling:** operator, 2026-10-01 (decision packet `eae0934f7b51`, actioned). No nightly curation run at all; the
  LaunchAgents stay optional behind `--confirm-install-agent`. A long gap means one larger catch-up, which the
  operator counts as the feature: the most work done in the session that needs it.
- **Built:** `agentsync checkpoint` (annotated tag `curated` in the docs repo); `curate-queue` now opens with
  `ADDED`/`CHANGED`/`REMOVED` mirror pages since that tag; the installed skill starts sessions with
  `sync --once` and ends them with `checkpoint`. CONTRACTS §16.17. Test:
  `test_cli.py::test_checkpoint_scopes_the_next_curate_queue_to_changes_since_the_session`.
- **Open, the operator's:** sources with a retention window (Teams chat kept 30 days, deleted files) lose content
  that is deleted between sessions, and upstream deletes replace a mirror page with a tombstone. Whether to keep
  deleted content in `docs/mirror` is a company records-policy call (decision packet `5d4707a94b3b`, 80%
  conviction for a per-source option, off by default). On the corporate Mac this does not bite yet: mail and Teams
  messages arrive by hand as files that stay on disk.
- **Next:** the 20-page pilot (implementation-gap-13) with the operator, using `install-skill` and `curate-queue`.
  **Before the first topic page** (added 2026-10-02, from [receipt J](../design/receipts/J-textql-ontology.md)):
  the operator writes about 10 real questions, each with the expected answer and its source paths, to
  `docs/_eval/questions.md` in the private docs repo. Ask them once in a fresh session before curation, recording
  each answer as correct or incorrect plus a rough count of lookups, then again after the pilot. The pilot passes if the
  curated answers are at least as correct, cite their sources, and take fewer lookups. Script it as a `claude -p` A/B
  only if the result is ambiguous and curation is about to grow past 20 pages.
  - **Revised 2026-10-03: the agent drafts, the operator confirms.** Writing 10 questions from a blank page was the
    operator's step, and the real data is only on the corporate Mac. `install-skill`'s skill now carries a
    "Baseline questions" section: asked to draft, the agent reads `mirror/` and writes about 15 candidates, splitting
    them into `_eval/questions.md` (questions only) and `_eval/answers.md` (draft answers and mirror paths), both
    `status: draft`. The operator keeps about 10, corrects the answers and marks both `status: confirmed`. A run reads
    only `questions.md`, records answers, cited paths and look-up counts in `_eval/results-<date>-<before|after>.md`,
    and opens `answers.md` only to score. The skill's look-up steps forbid opening the answer key, which is split
    out so an answering agent never reads it. `_eval` is in `gitops.COMMIT_PATHSPECS`. CONTRACTS §16.19.
    Operator step now: on the corporate Mac, run `agentsync install-skill`, then ask the agent to "draft the
    baseline questions" and review them.
  - **2026-10-04: one prompt for it.** README "Next, on the same Mac: draft the baseline questions" is a paste-once
    block for after setup: `git pull`, `install.sh --no-report` (no `--source-local`: the dry run shows the config
    untouched, only reinstall, `migrate` and `doctor`), `install-skill`, `sync --once`, then the skill's Draft step.
    **CORRECTED (2026-10-05):** that block is deleted (KISS K17, K04). Setup prompt v7's step 3
    ([README "Set up on a new Mac: one prompt"](../../README.md#set-up-on-a-new-mac-one-prompt)) reaches the Draft
    step through the sync loop's `NEXT:` line.
  - **2026-10-04: the KISS simplification supersedes both prompts.** Adoption on low-effort agents stops partway
    because the loop's order lives only in prose. [kiss-simplification.md](kiss-simplification.md) (5 waves, 19
    verified changes) makes `sync` do every automatic step and end on one NEXT line, cuts the surface to 9 commands,
    and folds setup and the baseline draft into one 3-step setup prompt (v7).

## Point-in-time archive (2026-10-02)

Scope (frozen): a `[governance] archive` switch (default false in the public tool). When true, agentsync keeps
everything: content a source deletes stays searchable in docs/archive/, every build-session checkpoint leaves a
permanent dated snapshot tag, and history is never squashed. Manual purge (erasure, legal, operator) still erases.

- **Ruling:** operator, 2026-10-02 (decision packet `5d4707a94b3b`, actioned): "Totally fine. We always save
  notes." Deleted company data may stay on the laptop past company retention; the public default stays off. This
  closes the "Open, the operator's" item in Just-in-time builds above, as one global switch rather than the
  per-source option that packet recommended at 80%.
- **Built:** `archive = true` copies a page and its sidecars to `archive/<path under mirror/>` (`status:
  archived`, `deleted_at`, `last_commit`) before an upstream-delete tombstone, queues no purge, adds a permanent
  `snapshot/<UTC>` tag at each `checkpoint`, skips compaction (`compact-history` exits 1), and `curate-queue`
  names the archive copy on `REMOVED` lines (a page tombstoned since the checkpoint now reads `REMOVED`). `archive`
  is pipeline-owned (`GENERATED_PATHSPECS`), linted like `mirror/` (a hand edit blocks), never reaped, and
  excluded from Time Machine. `purge` rewrites archive pages out of history like mirror pages; snapshot tags are
  remapped. CONTRACTS §16.18. Tests: `test_cli.py::test_archive_keeps_a_deleted_page_and_snapshots_each_checkpoint`
  (delete, archive, snapshot, purge end to end), `test_cycle.py::test_archive_on_upstream_delete_survives_a_head_moved_by_hand`.
- **Learnings:** a tombstone is a modification, not a removal, so the checkpoint diff showed an upstream delete as
  `CHANGED` until the tombstone was reaped 180 days later; `curate-queue` now classifies it `REMOVED`.
