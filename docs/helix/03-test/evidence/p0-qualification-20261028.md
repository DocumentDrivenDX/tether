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
| Q0 runtime/base/extras | Fresh Python 3.11.17 artifact and five installed-profile checks above; base provider SDK count zero. | Confirm selected OS/Python support matrix and stable-version artifact/metadata at the final candidate. |
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

## Next gate

Produce an exact TP-001 P0 case-to-evidence crosswalk for Q1–Q8, then run any missing selected real-profile and fault cases. Repeat the complete gate and installed checks from the final clean candidate commit. Only after every required row passes should the operator create a stable tag and follow the [deployment checklist](../../05-deploy/deployment-checklist.md).
