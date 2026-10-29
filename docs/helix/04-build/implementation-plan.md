---
ddx:
  id: implementation-plan
  type: implementation-plan
  activity: build
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: SD-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-004
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: TP-001
      kind: informed_by
    - id: TD-001
      kind: informed_by
    - id: TD-002
      kind: informed_by
    - id: TD-003
      kind: informed_by
---

# Consumer Contract and Compatibility Implementation

## Scope

This slice implements the requested consumer capabilities after 0.1 publication. FR-54 to FR-61 cover the eight requested consumer seams; FR-62 to FR-64 cover configuration, dependency and telemetry compatibility. The governing PRD, SD-001, CONTRACT-001 to CONTRACT-005 and TP-001 remain authoritative. CONTRACT-002/003/005 own the primary seam paragraphs; CONTRACT-001/004 add the required purpose/profile and context-transition bindings. The merged proposal is implemented in the development revision described below; it does not change the published 0.1.0 API or reopen its P0 qualification.

Erik LaBianca authorized implementation of all remaining C-01–05 capabilities on 2026-10-29, following the merged contract packet (#76). This accepts the requested runtime scope and reference-derived configuration baseline. Resolve concrete transaction/operation state and versioning in the governed design before each dependent source change. Application cutover and release/signing choices remain separate.

## Shared Constraints

- Keep the selected Pydantic AI runner and one authority for journal facts, history content and derived context. No duplicate runner or shadow application transcript.
- Use current actor, owner predicates, policy and per-attempt credential refresh. Never persist credentials or fall back to broader application/maintenance authority.
- Keep intent/result, unknown-effect, actual-worker and source-read boundaries. No exactly-once external-effect claim and no raw connection on generic ToolContext.
- All public I/O remains asynchronous; adapters own pools, roles, RLS, ingestion and business reconciliation. No automatic migrations.
- Public examples, characterization and crash effects are fabricated. No private repository, customer record or credential is copied into this repository.
- Preserve current byte/deadline/worker/refusal limits until an accepted profile and executed evidence justify a change. Unknown per-tier capacity refuses explicitly.

## Implementation Slices

| Slice | Story / area | Governing artifacts | Depends on | Validation gate | Notes |
|---|---|---|---|---|---|
| C-00 | Accept one consumer contract/design packet | PRD FR-54–64; proposed CONTRACT-002/003/005; ADR-011/018; SD-001 | 0.1 publication; no adoption dependency | All eight real contract paragraphs, transaction failure table, acceptance sketches and six owner decisions are reviewable; accepted paragraphs recorded before code | Accepted for development implementation on 2026-10-29; application deployment choices remain separate. |
| C-01 | Defaults, dependency compatibility and telemetry naming | FR-62–64; CONTRACT-002/003 | C-00 acceptance for affected configuration surfaces | Minimal composition/overrides; deterministic estimator and unknown-profile refusal; dependency boundary-version resolution plus codec/typing/installed suites; span/attribute naming through success/failure/await/worker paths | High priority compatibility work. Widen no untested range. |
| C-02 | Shared history transaction and staged settlement | FR-56/61; CONTRACT-002/003/005; ADR-018 | C-00; accepted transaction technical design | Independent-process failure table, same-key recovery/conflict, actor/fence/cancellation, content/reference/summary/feed atomicity; independent-backend uncertainty and idempotent reconciliation | Build both declared profiles; consumer profile selection remains an owner decision. |
| C-03 | Mediated Client invocation, leased preparation and auxiliary models | FR-54/55/59; CONTRACT-002/003; ADR-011 | C-00; C-02 where a selected tool stages writes | No model provenance for Client origin; full gates/approval-required refusal; dedup/preparation/lease/crash cases; missing/revoked credential between scheduling and every retry | Core provides mediation; application schedules and ingests. |
| C-04 | Registry/specification publication and edited approval | FR-57/58/60; CONTRACT-002/003 | C-00; command/operation admission from C-03 | Immutable generation race/retention; transitions only between completed groups; stale approval/digest refusal; tool-semantic narrowing and restart-before-intent | Current policy stays current even when definitions are pinned. |
| C-05 | Integrated fabricated consumer characterization | All accepted next-slice contracts; TP-001 | C-01–04 | Public fabricated end-to-end scenarios combine staged write, preparation, transition, registry update, edited approval and auxiliary call; retain zero-egress/dispatch negatives and process uncertainty evidence | Review adoption readiness after these gates; actual application cutover is separate work. |

## Execution Contract

The user has authorized planning and implementation of every remaining item. Execute C-01 through C-05 with their refusal, crash, conformance and integrated gates. Keep progress and exact verification evidence here across interruptions; a slice boundary is not completion. Do not publish a package or merge source without the applicable authorization. Preserve concurrent work in the shared checkout; execute in an isolated main-based checkout.

Use `uv sync --locked` and the repository's `make check CHECK_BASE=HEAD` for changed executable source. That command owns lint/format, strict typing, file ceilings, import/layer negative controls, dependency isolation and the bounded test groups; local and pre-commit checks share it. Hosted Actions and additional paid model/service calls are excluded. Reuse unchanged passing evidence; documentation-only edits receive artifact/diff validation rather than another runtime run.

Token estimate and comparable usage basis are unknown; no numerical token budget is invented. Additional external-service spending is capped at $0 by the owner's instruction. Host token accounting/enforcement is not available to this repository, so no enforced token ceiling is claimed. Runtime metering and account limits remain host-owned.

Development contract constants are MODEL_PORT_VERSION=4, ENGINE_CONTRACT_VERSION=4 and SESSION_CONTRACT_VERSION=5. Optional history/preparation/settlement/edit/auxiliary companions are revision 1; released 0.1 constants and artifacts remain unchanged. Accepted changes to signatures, entry semantics or precedence require an explicit new contract revision and compatibility/migration evidence during the implementation slice; the proposal alone does not change executable version claims.

## Accepted Configuration Baseline

These values are copied from the already exercised reference composition, not invented provider capacities. The implementation authorization accepts these compatibility defaults with application overrides. In published 0.1.0, eleven reference Client limits require explicit composition, while history-link hops and mutation-receipt bytes already default to 8 and 4,194,304. The development revision defaults all thirteen fields to the values below. Engine accepts omitted context settings but refuses request preparation as context_unconfigured when configuration, an estimator or the selected tier is missing; it already selects FullContext when no strategy is supplied.

| Client field | Existing reference value |
|---|---|
| max_page_size / max_call_ids | 100 / 8 |
| max_read_bytes / max_lookup_entries | 1,048,576 bytes / 1,000 entries |
| read_timeout_seconds / heartbeat_interval_seconds / shutdown_timeout_seconds | 5 / 1 / 5 seconds |
| follower_queue_size / max_followers / max_active_turns | 32 / 8 / 8 |
| max_request_bytes | 65,536 bytes |
| max_history_link_hops / max_mutation_receipt_bytes | 8 / 4,194,304 bytes (existing type defaults) |

The offline reference ModelExecutionLimits are response 8,192 bytes, tool arguments 512 bytes, two attempts, five-second request timeout and 0.01-second maximum retry delay. The live reference composition already selects different timeouts explicitly; it does not silently change these defaults. The development convenience composition retains the existing FullContext planner fallback and obtains limits and deterministic wire estimation from the selected model profile; Tail remains an explicit option. No live profile, tokenizer, output reservation or context capacity is guessed. A custom model without a qualified estimator/configuration must supply one or receive an explicit unconfigured refusal.

For dependencies, 0.1 documents the actually tested singleton versions. C-01 retains the resolver characterization below and exact executable pins in `uv.lock`; broader ranges require separate boundary qualification before published metadata changes. Configurable telemetry implements separate `span_prefix` and `attribute_namespace` Engine composition settings with current `tether` defaults; it does not rename journal kinds, policy actions or content permissions.

Installed Pydantic AI 2.59.0 metadata requires `pydantic>=2.12` and `pydantic-graph==2.59.0`. The consumer's supplied `pydantic>=2.11` constraint alone permits the tested 2.14.1 version; it does not establish that Pydantic 2.11 can run this Agent pin. Boundary characterization must respect the actual transitive minimum or explicitly qualify a different Agent version. A successful resolver result is not runtime/codec qualification.

An isolated uv resolver run against the published `novirhiza-tether==0.1.0` package plus the supplied `pydantic>=2.11` constraint selected Pydantic 2.14.1/Agent 2.59.0. A negative control with `pydantic<2.12` refused resolution because this release pins Pydantic 2.14.1. This characterizes these constraints only; the full selected-source consumer composition and wider runtime compatibility remain to be qualified. No published range was widened.

## Application Adapter Boundaries

Core will refuse proposals that put ingestion pipelines, artifact/research schemas, retention policy, workflow engagement rules, plug-in/MCP discovery, title/suggestion scheduling or browser routes inside the runtime. Those are application adapters using the bounded preparation, registry, transition and auxiliary-call seams. Core will also refuse a raw ToolContext connection, ambient credential/application-authority fallback, an extra runner, inferred external-effect success, or migration during ordinary construction.

The application owns stage payloads and idempotency/reconciliation for independent backends. Its transaction participant may expose only the bounded operations accepted by the shared profile. Business authority and database-role selection remain outside generic session types.

## Characterization Inputs

The consumer can supply its existing 18-scenario characterization inventory as fabricated, public-safe cases mapped to FR-54–64: actor/permission inputs, accepted source/spec/tool bindings, stage/settlement expectations and observable crash/refusal outcomes. Do not port private fixtures or domain records. Read-only comparison may identify the scenario to generalize; the application owner supplies any required business semantics.

A separately selected Gateway profile needs an operator-run fabricated workspace case proving exact request tags/header/body/invocation joins, current-caller denial, bounded reads and missing/partial/duplicate readiness. It remains P1 and cannot be inferred from a successful DS4 or generic source-double case. No credentials or paid run are requested by this plan.

## Continuation Evidence

The following records describe the released baseline and planning checkpoint before implementation authorization. The Active Execution section supersedes their proposed/not-implemented status for the development source.

C-00 has proposed paragraphs for all eight seams in CONTRACT-002/003/005 and a shared-transaction failure table. PRD FR-54–64 and the six named owner decisions are recorded; all six supplied answers remain proposed/open. No implementation of these seams is claimed. The next action is one coherent review of the packet, resolution of affected choices, then accepted technical design and ordered implementation.

The 0.1 release source/artifact qualification and public PyPI publication are complete; the [final release receipt](../03-test/evidence/p0-qualification-20261028.md#final-010-qualification--2026-10-29) retains exact hashes, four public-index installs and the failed macOS bootstrap observations. No unaccepted consumer requirement was moved into its P0 gate.

The [compatibility preflight](../02-design/evidence/consumer-compatibility-preflight-20261029.json) records source hashes at the release commit, all 13 current ClientLimit field dispositions, the 11 reference composition values, existing FullContext/unconfigured behavior, fixed telemetry naming and the positive/negative public-package resolver characterization. Current span families include Client, gate, retrieval/provider hook, tool and actual worker-body observations. Engine exposes neither span_prefix nor attribute_namespace; configurable naming remains proposed. Existing no-op/metadata telemetry evidence does not qualify an OTel receiver. This closes the unaffected provenance/claim audit, not C-01 implementation.

Read-only inspection of the available consumer runtime manifest identified 13 requirements: 12 use the public index, and one is routed through a separate index. The 12 public requirements resolved together with released Tether at Pydantic 2.14.1/Agent 2.59.0. The separately indexed requirement was explicitly excluded from that bounded resolver characterization, so neither the complete selected-source graph nor consumer runtime behavior is claimed. No source or private index configuration was copied into public evidence.

Qualification of the separately routed dependency/full consumer runtime, wider dependency ranges, convenience configuration defaults and configurable telemetry remain dependent on accepted scope/design. No source implementation of these proposed surfaces has begun. The task-created QA container and two disposable public-install environments have been removed; release artifacts, exact success/failure evidence, operator profiles and microsite captures are retained.

## Validation Plan

- [x] Erik LaBianca authorizes implementation of the merged contract packet and all remaining slices (2026-10-29).
- [x] Resolve the choices needed by C-01–05 in TD-001–003 and the accepted reference baseline; preserve current read-permission protections. Application deployment choices remain explicit and separate.
- [x] Validate every changed artifact and deliberate traceability edge; review current versus proposed claims.
- [x] Execute each accepted slice's refusal/crash/conformance cases and the required final source/artifact gate.
- [x] Review integrated characterization once; repeat only for a concrete changed risk or failed gate.

## Risks and Rollbacks

| Risk | Impact | Response | Rollback |
|---|---|---|---|
| A settlement callback is mistaken for atomicity | High | Separate fenced shared transaction from independent durable decision/reconciliation and test every frontier | Disable the unqualified participant/profile; preserve uncertainty and original facts |
| Mutable registry/specification invalidates execution evidence | High | Pin immutable generations/digests and recheck current policy; refuse incompatible pending calls | Retain old active bindings through actual exit; reject the unpublished generation |
| A convenience default hides unknown provider capacity | High | Obtain capacities/estimator from the explicit model profile; test unconfigured refusal | Require explicit configuration for that profile |
| An untested dependency range breaks codecs or consumer resolution | High | Test boundary versions and installed composition before metadata changes | Retain the documented tested constraints |
| Adoption decisions accidentally reopen 0.1 or become implementation claims | Medium | Keep accepted development scope, published 0.1.0 boundaries and remaining application deployment choices explicit | Revert only the proposal; retain released artifact identities |

## Exit Criteria

Planning is complete when the coherent contract/design packet, priorities, acceptance sketches, adapter boundaries and owner decisions are reviewable. Implementation is complete only after every accepted C-01–05 gate passes and its exact profile/version evidence is retained. Consumer adoption readiness follows that integrated evidence; actual cutover and the nonblocking enhancements are separate work.


## Active Execution — 2026-10-29

Source baseline: main `9ad5cc44440b7e1479a3a1563ce721e2e681be3f`. Execution uses an isolated checkout. The shared checkout contains independent CLI/UI work and is preserved.

| Order | Concrete work | Required evidence | State |
|---|---|---|---|
| 1 / C-01 | Finite Client/Model defaults; explicit profile context convenience; Engine span prefix and attribute namespace; retain tested dependency pins | Default/override and unknown-profile refusals; deterministic real estimator; nested/worker/failure naming; published dependency composition | Qualified for the declared development profile |
| 2 / C-02 | Paired PostgreSQL history staging and fenced atomic journal commit; stable operation keys; shared participant and independent settlement decision/reconciliation | Real database separate-connection/process frontier faults, cancellation, expiry, unknown acknowledgment, conflicts and result withholding | Qualified for the declared development profile |
| 3 / C-03 | Durable Client-origin tool command using existing gates; post-admission leased preparation; tool-free caller-bound auxiliary operation | Dedup/reopen, zero-model direct tool, approval refusal, cancelled/denied preparation; source/credential revocation and retry usage | Qualified for the declared development profile |
| 4 / C-04 | Bounded immutable registry generations; completed-group spec transitions; tool-owned approval argument transform | Publication/actual-worker retention, restart and stale bindings; semantic restriction and independent proposal/shown/executable digest tests | Qualified for the declared development profile |
| 5 / C-05 | Fabricated combined public Client scenario and capture; full canonical quality gate and installed artifact checks | All requirements mapped to executed tests, unchanged limits, actual capture receipts, semantic/boundary review | Qualified for the declared development profile |

Existing module ownership is documented in architecture/SD-001 and enforced by `uv run --locked python scripts/check_layers.py`; `scripts/quality.py` also runs its forbidden-import negative controls, source shape ceilings and dependency checks. New neutral values belong in `types`, mediation in `engine`/`sessions`, and transaction implementation in `stores/postgres`. No new runtime dependency was added. The complete `make check CHECK_BASE=HEAD` gate remains required. No external-service spend, hosted Actions, or model server restart is authorized.

C-01–05 are implemented and locally qualified. The final execution receipt below records the exact support subset and observed evidence.


### Final Qualification — 2026-10-29

The complete `make check CHECK_BASE=HEAD` recipe exited 0 with 1,777 passed cases in 81 bounded groups and 90% branch-aware aggregate coverage. The unchanged 85% floor, 180-second child deadline, test inventory, dependency pins and shape ceilings remain in force. All 63 consumer acceptance cases and the added deterministic TUI teardown regression are included.

The [execution receipt](../03-test/evidence/consumer-qualification-20261029.md) and [machine-readable evidence](../03-test/evidence/consumer-qualification-20261029.json) retain exact source/artifact hashes, selected versions, successful/failed log digests, requirement correspondence and support limits. The final uncommitted source fingerprint is `3173be2758727f887c1e7fb48018f88b6ba555aadb3a3e6692e3417791447f73`; its hash-map definition is explicit in the receipt.

Fresh base and TUI installed profiles pass with zero provider SDKs. The public example passes from the installed wheel, and four actual development-viewer captures match current source/asset hashes. Process-death matrices cover shared and independent commit/rollback frontiers. Review preserved separate full-exchange history composition, current caller authority, unknown-effect barriers, actual worker retention and explicit application adapter boundaries.

Qualification also corrected pending-definition drift, primary unknown-error masking, whole-batch retry identity/position handling, legacy error-reason compatibility, bounded resolution lookup and a late TUI refresh during child-widget unmount. Failed and superseded runs remain distinguished from the final passing candidate. Shared-host contention was waited out without changing deadlines.

All requested core implementation slices are complete. Actual consumer deployment, its separately indexed dependency, wider dependency ranges, arbitrary remote backend semantics and release/signing policy are distinct from this qualification. No downstream adoption gate was introduced. The published 0.1.0 release is unchanged. Qualification ran on the uncommitted isolated candidate; [source-control follow-through](../03-test/evidence/consumer-source-control-20261029.md) records its checkpoint and remaining integration gate. The shared checkout's concurrent work is preserved.
