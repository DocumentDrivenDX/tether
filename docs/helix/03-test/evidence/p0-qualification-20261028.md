# Tether P0 qualification snapshot — 2026-10-28

This receipt applies to source commit `9e28341b23b3214eb10146e9372302796ab486a3` and tree `d013f337d70e0440119da28ef6c98ba8e3415757` on `main`. It records executed local evidence and open gates against [TP-001](../test-plan.md) and [SD-001](../../02-design/solution-designs/SD-001-tether-library-design-and-build-plan.md). **The first-release P0 gate is open.** No stable tag, GitHub release, or PyPI upload is authorized by this receipt.

## Executed source and artifact checks

| Check | Observed result | Limit |
|---|---|---|
| Complete source recipe | `make check` passed on PR #63 head `c20202f3013c2f14e7b37a917b04a338b606d6f3`: 1,674 tests in 77 bounded groups, 90% aggregate branch coverage, with static/layer checks. Its tree is identical to the merged main tree above. | Local macOS, Python 3.11.17; it is not a hosted or other-platform result. |
| Fresh artifacts | `uv 0.13.3` built `novirhiza_tether-0.0.0.post155.dev0+9e28341-py3-none-any.whl` and the matching `.tar.gz` from the clean main commit. | This development version is not the proposed `0.1.0` release build. |
| Installed base | `scripts/check_distribution.py` passed from the fresh artifacts; provider SDK count was zero. Offline Agent/replay, gated tool, and durable JSONL Client resume smokes passed. | Selected smoke paths, not all TP-001 behavior. |
| Installed `openai` extra | Same artifact check passed with one provider SDK and offline compatible loopback. | Loopback does not establish a live external provider service. |
| Installed `tui` extra | Same artifact check passed with offline TUI/CLI restart and a real terminal pseudo-terminal. | Local terminal and Python profile only. |
| Installed `postgres` extra | Same artifact check passed, including selected PostgreSQL imports and offline durable Client resume. | Import/smoke does not prove a new multiprocess fault matrix. |
| Installed `embedded-postgres` extra | Same artifact check passed, including embedded binary discovery. | Binary discovery does not prove every supported host/platform. |

The wheel SHA-256 is `e927f3e93b1994ac89048d80d6cd9cff8b2e3a80b7fecab2b2322b39ff16ce38`; the source archive SHA-256 is `b5f90d39626c03229bee0d93a3292542ecae1947938bb8488f96036b30a675d9`. These hashes identify this local development build only. The five installed checks used these exact two files; all exited successfully. The source gate's run log and local artifact files are outside Git and are not retained as release assets.

## Required gate disposition

| Gate | Evidence now present | Remaining release decision |
|---|---|---|
| Q0 runtime/base/extras | Fresh Python 3.11.17 artifact and five installed-profile checks above; base provider SDK count zero. | The owner selected Python 3.11 only. State the supported OS/profile subset from actual evidence and confirm stable-version artifact/metadata at the final candidate. |
| Q1 Agent/tools/gates | Complete local source recipe includes actual Agent, policy, approval, guard, unknown-effect and regression suites. | Trace each required contract path to its executed primary suite; a passing aggregate alone does not close Q1. |
| Q2 model/transport | Native/compatible controlled HTTP, stream, retry, identity and incomplete-terminal suites are in the complete local recipe; installed `openai` loopback passed. | Finish the selected real-profile/final-wire and server framing qualification and record the exact supported subset. |
| Q3 context/history | Source-addressed Full/Tail/Summarizing, canonical history, readiness and current-read suites are in the local recipe. | Complete workload-specific summary fidelity and actual server token/framing evidence; unsupported attachment/media codecs remain explicit refusals. |
| Q4 durable recovery | Local JSONL, PostgreSQL, process-exit and lost-ack suites are in the complete recipe; installed JSONL resume passed. | Audit every required process/storage frontier and selected multiprocess profile against TP-001; do not infer power-cut durability. |
| Q5 worker lifetime | The exact local crosswalk below covers real worker exit, cancellation, queued work, replacement refusal, lease expiry, stale completion and finite shutdown. | Repeat the crosswalk at the final clean candidate; this is local Python 3.11 evidence and does not assert every host profile. |
| Q6 Client/reference journeys | Installed durable Client resume, offline CLI/TUI restart and pseudo-terminal checks passed; source reconnect/read suites passed. | Close each TP-001 reconnect/race/retention journey and any selected consumer parity claims. Performance budgets remain P1. |
| Q7 diagnostics/usage | Source recipe includes content-free telemetry and known/incomplete usage cases. | Audit failure/context/worker paths and their selected version/profile evidence. Real OTel exporter remains P1 and must not be claimed from no-op evidence. |
| Q8 versions/replay | Locked runtime is Pydantic AI 2.59.0; source replay/cassette and versioned corpus suites passed; installed offline replay passed. | Confirm selected release pins and explicitly bound corpus/upgrade scope at the final candidate. No general upgrade guarantee. |

Q9 live Gateway delivery is P1 and outside this P0 release decision. Generic external-history pending/partial/ambiguous/denied/unavailable behavior remains P0; controlled source tests, including the PR #63 same-provider-id/two-attempt and invalid-retry-hint regressions, passed in the complete recipe. A controlled source is not live Gateway proof.

### Q5 actual-worker crosswalk

The complete 1,674-case source run collected and passed these named tests from the selected local JSONL/PostgreSQL and real executor suites:

| Required boundary | Executed test |
|---|---|
| Cancelled waiter retains real synchronous body and session marker; replacement Engine/turn refuses until exit | `tests/test_shared_workers.py::test_replacement_engine_and_lease_refuse_until_actual_exit` |
| Root close is finite, reports incomplete shutdown and retains the JSONL lock until actual worker exit | `tests/test_shared_workers.py::test_root_close_retains_lock_for_another_engines_actual_worker` |
| Queued cancellation never starts the function | `tests/test_operation_ownership.py::test_queued_cancellation_never_enters_function` |
| Cancelled asynchronous child and executor return handoff retain actual ownership | `tests/test_operation_ownership.py::test_async_cancellation_returns_without_releasing_actual_child_or_root`; `::test_executor_return_handoff_never_publishes_idle_and_adopts_after_cancel` |
| Expired database lease and another root cannot acquire while a real worker remains; acquisition becomes possible after exit | `tests/test_postgres_leases.py::test_actual_worker_retention_refuses_expired_takeover_across_roots` |
| Cancelled or timed-out database transaction retains capacity until actual release; shutdown is finite | `tests/test_postgres_lifetime.py::test_pending_transaction_retains_capacity_until_actual_release` (both parameter cases) |
| Late observer/worker exit cannot claim an early release or block root closure | `tests/test_worker_observers.py::test_abandoned_wait_and_late_body_exit_are_distinct`; `::test_worker_exit_releases_root_while_observational_sink_is_still_running` |

These are bounded executor/process tests with controlled barriers. They do not simulate an operating-system power cut or qualify arbitrary deployment worker pools.

### Selected executed cases for the other P0 gates

These named cases were collected and passed in the same complete local source run. They anchor the matrix above; they are not a claim that all required subcases have been traced or that an open gate is closed.

| Gate | Executed cases and proven boundary |
|---|---|
| Q0 | `tests/test_dependencies.py::test_transitive_sdk_contamination_is_rejected` refuses provider SDK leakage into base; `tests/test_distribution.py::test_good_artifacts_and_ambiguous_outputs` checks artifact metadata; `tests/test_reference_clients.py::test_demo_uses_durable_client_and_requires_exact_approval` covers the offline reference composition. The fresh installed-profile results above supply the independent wheel boundary. |
| Q1 | `tests/test_gated_execution.py::test_real_agent_normalizes_once_and_commits_before_dispatch` covers real Agent dispatch; `::test_approval_reenters_current_policy_and_is_single_use` covers the approval gate; `::test_unknown_commits_stop_dependent_dispatch` covers uncertain acceptance; `tests/test_confirmation_acceptance.py::test_cached_runner_cannot_answer_after_another_engine_already_won` covers competing continuation. |
| Q2 | `tests/test_compatible_loopback.py::test_real_loopback_retry_refreshes_identity_and_receipts` crosses a real local HTTP socket; `::test_real_loopback_fragmented_tool_arguments_finish_once` and `::test_real_loopback_missing_terminal_never_replays_or_finishes` cover fragmented/invalid output; `tests/test_adapter_limits.py::test_retry_exhaustion_and_backoff_cancellation` covers finite retry; `tests/test_native_header_boundary.py::test_ambient_sdk_headers_cannot_widen_identity_or_request_metadata` covers actual egress headers. |
| Q3 | `tests/test_context_group_state_exploration.py::test_every_generated_tool_group_cut_is_atomic_and_accounted` covers generated cut positions; `tests/test_agent_summary_restart.py::test_summary_unknown_ack_requires_reopen_and_only_accepted_provenance_reuses` covers uncertain summary acceptance; `tests/test_history_bindings.py::test_exchange_binding_preserves_component_and_full_record_digests` covers canonical observed content; `tests/test_client_reads.py::test_external_source_readiness_keeps_metadata_without_disclosing_content` covers controlled source states. |
| Q4 | `tests/test_storage_faults.py::test_actual_process_exit_preserves_intent_and_recovers_interrupted_turn` covers real JSONL process loss; `tests/test_postgres_engine_effect_process.py::test_real_engine_sql_effect_keeps_unknown_barrier_after_process_kill` covers real SQL effect uncertainty; `tests/test_postgres_leases.py::test_independent_roots_compete_and_stale_tokens_cannot_write_or_release` covers multiprocess store fencing. |
| Q6 | `tests/test_client_follow.py::test_retention_gap_resets_then_continues_from_captured_watermark` covers retained feed loss; `tests/test_client_reads.py::test_delayed_source_and_shared_provider_request_id_keep_attempts_distinct` covers delayed history under distinct attempts; `tests/test_reference_clients.py::test_terminal_ui_keeps_context_failure_visible` covers the reference client failure journey. |
| Q7 | `tests/test_engine_telemetry.py::test_actual_agent_has_metadata_only_known_events_and_source_gate_context` covers accepted metadata observations; `::test_unacknowledged_or_invalid_commits_emit_no_accepted_event` covers uncertain acceptance; `tests/test_attempt_usage.py::test_uncertain_observation_never_publishes_optimistic_usage` preserves incomplete usage. |
| Q8 | `tests/test_replay_version_corpus.py::test_locked_sdk_versions_require_explicit_requalification` binds selected runtime pins; `::test_frozen_cassette_refuses_unsupported_version` rejects drift; `::test_frozen_sdk_checkpoint_round_trips_and_refuses_version_drift` and `::test_frozen_jsonl_history_reopens_and_new_writes_match` cover checkpoint/history corpus behavior. |

## Next gate

Produce an exact TP-001 P0 case-to-evidence crosswalk for Q1–Q8, then run any missing selected real-profile and fault cases. Repeat the complete gate and installed checks from the final clean candidate commit. Only after every required row passes should the operator create a stable tag and follow the [deployment checklist](../../05-deploy/deployment-checklist.md).
