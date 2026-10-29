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
---

# Next Consumer Contract and Compatibility Slice

## Scope

This is the immediately next work after 0.1 publication, before consumer adoption or other P1 enhancement work. FR-54 to FR-61 cover the eight requested consumer seams; FR-62 to FR-64 cover configuration, dependency and telemetry compatibility. The governing PRD, SD-001, CONTRACT-001 to CONTRACT-005 and TP-001 remain authoritative. CONTRACT-002/003/005 own the primary seam paragraphs; CONTRACT-001/004 add the required purpose/profile and context-transition bindings. The proposed paragraphs are not current API claims and do not reopen 0.1 P0 qualification.

Deliver one coherent contract/design proposal and its acceptance packet before implementing a seam. Erik LaBianca must accept the affected paragraph before dependent source work. Technical design must resolve concrete transaction/operation state and versioning before implementation; this plan does not invent a missing accepted design.

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
| C-00 | Accept one consumer contract/design packet | PRD FR-54–64; proposed CONTRACT-002/003/005; ADR-011/018; SD-001 | 0.1 publication; no adoption dependency | All eight real contract paragraphs, transaction failure table, acceptance sketches and six owner decisions are reviewable; accepted paragraphs recorded before code | Current priority. Group related changes rather than scheduling one tiny PR per item. |
| C-01 | Defaults, dependency compatibility and telemetry naming | FR-62–64; CONTRACT-002/003 | C-00 acceptance for affected configuration surfaces | Minimal composition/overrides; deterministic estimator and unknown-profile refusal; dependency boundary-version resolution plus codec/typing/installed suites; span/attribute naming through success/failure/await/worker paths | High priority compatibility work. Widen no untested range. |
| C-02 | Shared history transaction and staged settlement | FR-56/61; CONTRACT-002/003/005; ADR-018 | C-00; accepted transaction technical design | Independent-process failure table, same-key recovery/conflict, actor/fence/cancellation, content/reference/summary/feed atomicity; independent-backend uncertainty and idempotent reconciliation | Build both declared profiles; consumer profile selection remains an owner decision. |
| C-03 | Mediated Client invocation, leased preparation and auxiliary models | FR-54/55/59; CONTRACT-002/003; ADR-011 | C-00; C-02 where a selected tool stages writes | No model provenance for Client origin; full gates/approval-required refusal; dedup/preparation/lease/crash cases; missing/revoked credential between scheduling and every retry | Core provides mediation; application schedules and ingests. |
| C-04 | Registry/specification publication and edited approval | FR-57/58/60; CONTRACT-002/003 | C-00; command/operation admission from C-03 | Immutable generation race/retention; transitions only between completed groups; stale approval/digest refusal; tool-semantic narrowing and restart-before-intent | Current policy stays current even when definitions are pinned. |
| C-05 | Integrated fabricated consumer characterization | All accepted next-slice contracts; TP-001 | C-01–04 | Public fabricated end-to-end scenarios combine staged write, preparation, transition, registry update, edited approval and auxiliary call; retain zero-egress/dispatch negatives and process uncertainty evidence | Review adoption readiness after these gates; actual application cutover is separate work. |

## Execution Contract

The user has authorized contract/design preparation and prioritization. The supplied consumer requirement explicitly forbids source implementation before its contract paragraph is accepted. Continue all independent drafting, characterization and validation; block only dependent source work awaiting that acceptance or an owner choice.

Use `uv sync --locked` and the repository's `make check CHECK_BASE=HEAD` for changed executable source. That command owns lint/format, strict typing, file ceilings, import/layer negative controls, dependency isolation and the bounded test groups; local and pre-commit checks share it. Hosted Actions and additional paid model/service calls are excluded. Reuse unchanged passing evidence; documentation-only edits receive artifact/diff validation rather than another runtime run.

Token estimate and comparable usage basis are unknown; no numerical token budget is invented. Additional external-service spending is capped at $0 by the owner's instruction. Host token accounting/enforcement is not available to this repository, so no enforced token ceiling is claimed. Runtime metering and account limits remain host-owned.

Current contract-version constants describe 0.1. Accepted changes to signatures, entry semantics or precedence require an explicit new contract revision and compatibility/migration evidence during the implementation slice; the proposal alone does not change executable version claims.

## Proposed Configuration Baseline

These values are copied from the already exercised reference composition, not invented provider capacities. They are proposed compatibility defaults requiring acceptance, with application overrides. Current library constructors still require explicit values.

| Client field | Existing reference value |
|---|---|
| max_page_size / max_call_ids | 100 / 8 |
| max_read_bytes / max_lookup_entries | 1,048,576 bytes / 1,000 entries |
| read_timeout_seconds / heartbeat_interval_seconds / shutdown_timeout_seconds | 5 / 1 / 5 seconds |
| follower_queue_size / max_followers / max_active_turns | 32 / 8 / 8 |
| max_request_bytes | 65,536 bytes |

The offline reference ModelExecutionLimits are response 8,192 bytes, tool arguments 512 bytes, two attempts, five-second request timeout and 0.01-second maximum retry delay. The live reference composition already selects different timeouts explicitly; it does not silently change these defaults. A proposed Full-context default obtains limits and deterministic wire estimation from the selected model profile; Tail remains an explicit option. No live profile, tokenizer, output reservation or context capacity is guessed. A custom model without a qualified estimator/configuration must supply one or receive an explicit unconfigured refusal.

For dependencies, 0.1 documents the actually tested singleton versions. C-01 tests proposed range boundaries and the consumer's resolver constraints before widening published metadata; exact executable pins remain in `uv.lock`. Configurable telemetry proposes separate `span_prefix` and `attribute_namespace` Engine composition settings with current `tether` defaults; it does not rename journal kinds, policy actions or content permissions.

Installed Pydantic AI 2.59.0 metadata requires `pydantic>=2.12` and `pydantic-graph==2.59.0`. The consumer's supplied `pydantic>=2.11` constraint alone permits the tested 2.14.1 version; it does not establish that Pydantic 2.11 can run this Agent pin. Boundary characterization must respect the actual transitive minimum or explicitly qualify a different Agent version. A successful resolver result is not runtime/codec qualification.

An isolated uv resolver run accepted the current distribution metadata plus the supplied `pydantic>=2.11` constraint and selected Pydantic 2.14.1/Agent 2.59.0. This characterizes that one constraint only; a complete consumer dependency set and wider runtime compatibility remain to be qualified.

## Application Adapter Boundaries

Core will refuse proposals that put ingestion pipelines, artifact/research schemas, retention policy, workflow engagement rules, plug-in/MCP discovery, title/suggestion scheduling or browser routes inside the runtime. Those are application adapters using the bounded preparation, registry, transition and auxiliary-call seams. Core will also refuse a raw ToolContext connection, ambient credential/application-authority fallback, an extra runner, inferred external-effect success, or migration during ordinary construction.

The application owns stage payloads and idempotency/reconciliation for independent backends. Its transaction participant may expose only the bounded operations accepted by the shared profile. Business authority and database-role selection remain outside generic session types.

## Characterization Inputs

The consumer can supply its existing 18-scenario characterization inventory as fabricated, public-safe cases mapped to FR-54–64: actor/permission inputs, accepted source/spec/tool bindings, stage/settlement expectations and observable crash/refusal outcomes. Do not port private fixtures or domain records. Read-only comparison may identify the scenario to generalize; the application owner supplies any required business semantics.

A separately selected Gateway profile needs an operator-run fabricated workspace case proving exact request tags/header/body/invocation joins, current-caller denial, bounded reads and missing/partial/duplicate readiness. It remains P1 and cannot be inferred from a successful DS4 or generic source-double case. No credentials or paid run are requested by this plan.

## Continuation Evidence

C-00 has proposed paragraphs for all eight seams in CONTRACT-002/003/005 and a shared-transaction failure table. PRD FR-54–64 and the six named owner decisions are recorded; all six supplied answers remain proposed/open. No implementation of these seams is claimed. The next action is one coherent review of the packet, resolution of affected choices, then accepted technical design and ordered implementation.

The 0.1 release source and artifact qualification runs independently of this proposal. Never move an unaccepted consumer requirement into that release's P0 gate.

## Validation Plan

- [ ] Erik LaBianca accepts the affected contract paragraphs before their source changes.
- [ ] Resolve each owner decision only when its dependent implementation/adoption needs it; preserve current read-permission protections while open.
- [ ] Validate every changed artifact and deliberate traceability edge; review current versus proposed claims.
- [ ] Execute each accepted slice's refusal/crash/conformance cases and the required final source/artifact gate.
- [ ] Review integrated characterization once; repeat only for a concrete changed risk or failed gate.

## Risks and Rollbacks

| Risk | Impact | Response | Rollback |
|---|---|---|---|
| A settlement callback is mistaken for atomicity | High | Separate fenced shared transaction from independent durable decision/reconciliation and test every frontier | Disable the unqualified participant/profile; preserve uncertainty and original facts |
| Mutable registry/specification invalidates execution evidence | High | Pin immutable generations/digests and recheck current policy; refuse incompatible pending calls | Retain old active bindings through actual exit; reject the unpublished generation |
| A convenience default hides unknown provider capacity | High | Obtain capacities/estimator from the explicit model profile; test unconfigured refusal | Require explicit configuration for that profile |
| An untested dependency range breaks codecs or consumer resolution | High | Test boundary versions and installed composition before metadata changes | Retain the documented tested constraints |
| Adoption decisions accidentally reopen 0.1 or become implementation claims | Medium | Keep next-slice P1 scope, proposed contract status and six open decisions explicit | Revert only the proposal; retain released artifact identities |

## Exit Criteria

Planning is complete when the coherent contract/design packet, priorities, acceptance sketches, adapter boundaries and owner decisions are reviewable. Implementation is complete only after every accepted C-01–05 gate passes and its exact profile/version evidence is retained. Consumer adoption readiness follows that integrated evidence; actual cutover and the nonblocking enhancements are separate work.
