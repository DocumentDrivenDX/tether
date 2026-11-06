# Consumer capability qualification — 2026-10-29

C-01–C-05 are implemented and locally qualified against the declared development profile. The published 0.1.0 package is unchanged. This is source/artifact evidence, not release or consumer-cutover authorization.

## Exact candidate

Baseline: `9ad5cc44440b7e1479a3a1563ce721e2e681be3f`. The source fingerprint measured before checkpointing is `3173be2758727f887c1e7fb48018f88b6ba555aadb3a3e6692e3417791447f73`. [The source manifest](consumer-source-20261029.json) defines the selected file hash map; this fingerprint is not a Git tree ID. The complete source archive below also identifies packaged fixtures/resources. [The machine-readable receipt](consumer-qualification-20261029.json) retains versions, all 63 consumer acceptance case IDs, capture hashes and operator-log digests.

## Executed checks

| Check | Observed result | Scope |
|---|---|---|
| Complete canonical recipe | `make check CHECK_BASE=HEAD` exited 0: 1,777 passed cases in 81 bounded groups; 90% branch-aware aggregate coverage | Python 3.11.17, macOS arm64; lint/format, strict types, shape ceilings, layer/dependency negative controls and the unchanged 85% floor/180-second child deadline |
| Installed base | Passed with zero provider SDKs | Network-denied imports, offline Agent/replay, gated tool and durable Client recovery |
| Installed TUI | Passed with zero provider SDKs | Real offline UI/CLI restart plus terminal PTY; one observed startup 1.30 seconds/reply 0.14 seconds, not a latency commitment |
| Public example | Passed from the installed base wheel using isolated import mode | Three scripted model calls, one preparation and one staged write; fabricated data |
| Artifact fidelity | 367 packaged Python source files and 199 wheel Python files match the candidate | Includes the public example in the source archive |
| HELIX artifacts | Fourteen validate with no blocking findings | One retained advisory about historical release citations in TP-001 |
| Captures | Four assertion-backed states; source/example/asset hashes match | Actual development API viewer, not released 0.1.0 UI or live inference |

The selected versions are Pydantic 2.14.1, Pydantic AI 2.59.0, Textual 8.2.8, psycopg/psycopg-binary 3.3.6, psycopg-pool 3.3.3 and pgserver-pg18 distribution 18.6.1. Exact remaining tool versions are in the JSON receipt. The 63 new consumer cases are included in the complete inventory. The additional deterministic TUI shutdown regression is included too.

## Requirement correspondence

| Slice | Executed boundary |
|---|---|
| C-01 / FR-62–64 | Finite defaults and overrides, explicit model capacity/estimator and Full/Tail selection, unconfigured refusal, configurable nested/worker tracing names; dependency pins retained |
| C-02 / FR-56, FR-61 | Paired content/journal/summary/feed publication; whole-batch key/frontier/position recovery; conflict, unknown acknowledgment and reopen; four shared and two independent process-death frontiers each repeated for commit and rollback |
| C-03 / FR-54, FR-55, FR-59 | Model-free direct command/dedup/reopen and same-invocation recovery; ready/needs-input/refused/cancelled preparation with verified derived references; caller-bound tool-free auxiliary calls and revoked/missing credentials |
| C-04 / FR-57, FR-58, FR-60 | Immutable registry publication and actual-worker retention; completed-group specification transition; semantic edited approval with independent bindings and durable restart before intent |
| C-05 | Combined real public Client example, installed artifacts, four capture states, complete canonical recipe and semantic/boundary review |

[TP-001](../test-plan.md), [TD-001](../../02-design/technical-designs/TD-001-paired-history-transaction.md), [TD-002](../../02-design/technical-designs/TD-002-mediated-operations.md) and [TD-003](../../02-design/technical-designs/TD-003-registry-and-approval-bindings.md) own the test/design correspondence. [The capture ledger](../../../microsite.md#consumer-api-integration-development-source-preview) identifies the reproducible viewer recipe.

## Corrections discovered during qualification

Failed and superseded runs remain recorded by their operator-local log digests; no failed run is counted as the successful gate. The work corrected declaration-snapshot drift in pending reads, cleanup masking a primary unknown acknowledgment, a stale hardcoded unsupported contract version, a resolution check limited to its first journal page, nested-reference batch-key collisions, unkeyed batch replay duplicating entries, a reopened-profile test missing fresh binding, and preservation of the legacy metadata write-error reason. The full run also exposed a TUI follower refreshing after child-widget unmount; a delayed-response regression now covers that race.

During heavy shared-host stress, context/peer-event deadlines also timed out. The unchanged cases passed after contention subsided. No assertion, test inventory, dependency pin, deadline, coverage floor or shape ceiling was relaxed. The canonical gate was repeated after each final correction; only its final passing source fingerprint is claimed above.

## Artifact hashes

| Local development artifact | SHA-256 |
|---|---|
| `novirhiza_tether-0.1.0.post19.dev0+9ad5cc4-py3-none-any.whl` | `84fe7f24e3204c673346fca3023e0d42a75cf80632ebe9dd0fc78cadfcb25522` |
| `novirhiza_tether-0.1.0.post19.dev0+9ad5cc4.tar.gz` | `10195e94e96bb9a16b79a55b3945b4990c05b1b90171d4782cf9a529d885277c` |

Raw logs and built artifacts remain operator-local. Their hashes are retained in the JSON receipt. The build version is a development version derived from the baseline commit; the source manifest and artifact hashes identify its uncommitted contents.

## Support limits and disposition

Qualification covers the selected local Python/macOS and PostgreSQL 18 profiles and exact dependency pins. Process-kill tests do not establish operating-system power-cut durability. The independent participant protocol preserves decisions and uncertainty; applications still own actual business rollback/idempotency semantics. The integrated example's participant is volatile; durable database frontiers are tested separately.

The complete private consumer dependency graph, wider dependency versions, actual consumer deployment, arbitrary remote backends, real OTel receiver and additional live model profiles are not claimed. These limits are not a downstream-adoption release gate. At qualification completion no publication, tag, merge or commit had been performed. Subsequent checkpoint/PR disposition is recorded separately in [the source-control follow-through](consumer-source-control-20261029.md). External paid-service spending was zero; hosted Actions and model-server restarts were not used.
