---
ddx:
  id: prd
  type: prd
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: competitive-analysis
      kind: informed_by
    - id: concerns
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: ADR-006
      kind: informed_by
    - id: ADR-007
      kind: informed_by
    - id: ADR-008
      kind: informed_by
    - id: ADR-009
      kind: informed_by
    - id: ADR-010
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-012
      kind: informed_by
    - id: ADR-021
      kind: informed_by
    - id: CONTRACT-005
      kind: informs
    - id: CONTRACT-004
      kind: informs
    - id: TP-001
      kind: informs
kind: product
---

# Product Requirements Document

## Summary

Tether is an open-source Python library for building agent harnesses. It provides an asynchronous agent loop with a provider-neutral model port, a typed tool-call interface, durable session management with crash recovery, and telemetry. It exposes the session list and session data to a user interface through a fast in-process Python API (list, read in pages, follow live changes), and it ships a command-line client and a terminal UI that work seconds after cloning the repository. It supports configured local, self-hosted, sovereign or hosted model servers within declared protocol/model subsets, and keeps provider SDKs out of base packaging/public core types while permitting explicitly selected optional adapter SDKs (ADR-012). Python developers and platform teams who embed an agent runtime in their own services use it, keeping their own interface, storage, and policy. Release success is that every port has a dummy and a conformance suite, that recorded conversations replay deterministically through the loop, that a killed turn on a qualified durable profile preserves accepted steps and explicitly reports blocked or unknown outcomes, that a UI lists, reads, and follows sessions within published budgets, and that a developer is using the harness in a terminal within seconds of cloning.

## Problem and Goals

### Problem

A Python application needs one coherent boundary for agent execution, policy, live identity, recovery, storage and UI access. LangGraph, Pydantic AI Harness, Pi Durable and managed services already provide substantial loop, persistence and streaming capabilities. Integrating one can use a single authoritative state owner, but must still preserve the application's exact authorization, recovery, session and dependency requirements. The [framework and AgentKit review](../00-discover/agent-harness-framework-review.md) evaluates that fit; ADR-003 retains Python and the behavioral boundaries while ADR-002/003 select Pydantic AI Agent/model reuse, supported by SPIKE-003/004. The problem is the cost of implementing and verifying that combination, not the absence of sessions or UI APIs in other products.

### Goals

1. A Python developer clones the repository and uses the harness in a terminal within seconds, with no configuration and no credential.
2. A UI of any kind reads sessions through one Python API that stays fast as sessions and transcripts grow.
3. The loop is testable alone: every external system is behind a port with a dummy, and recorded conversations replay deterministically.
4. A qualified durable profile detects interruption, preserves accepted steps once, and supports explicit resume with blocked or unknown outcomes where required.
5. A platform that serves many users puts its policy at engine-owned gates whose validated extension outputs cannot widen authorization; extensions remain trusted in-process code.
6. Contributors can help without a legal hurdle, and the maintainers keep the code reviewable and consistent.
7. A developer runs the harness on a local or sovereign model, or a server within a qualified OpenAI-compatible subset, by configuration alone, with no hosted-provider account requirement and an SDK-free base/custom-model path; optional model adapters have disclosed dependency closures.

### Success Metrics

The numeric targets below were proposed on 2026-10-22 at the owner's request, as starting points. They are targets, not measurements. The first benchmark baseline validates or replaces each one, after which it becomes a committed budget that CI protects against regression.

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Ports with a dummy implementation that passes its conformance suite | 100% | The conformance suites run in CI |
| Recorded scenarios that replay through the loop and reproduce the saved audit record | 100% of shipped scenarios | The replay tests run in CI |
| Durable interruption preserves accepted steps and reports completed, blocked or unknown outcomes | Pass for every prefix of every shipped scenario under its declared durable profile | The prefix test and a real-process kill test in CI |
| Blocking synchronous I/O entry points in the public API | 0 | A review check and an API listing test |
| Message or tool content found in spans and audit entries | 0 | A telemetry content test in CI |
| Foundation-model provider SDKs in the default install's dependency tree | 0 | A dependency-tree test and the boundary check in CI |
| Clone to a running terminal UI on the demo model, dependency install on a warm package cache | At most 10 s | The quickstart test in CI, timed |
| The same on a cold package cache | At most 60 s (network bound) | The quickstart test, run on demand |
| Time from the first keystroke of a message to the first reply text in the demo | At most 1 s | The quickstart test |
| The client API and engine budgets in NFR-7 | Met at the reference workload | The benchmark suite in CI |

### Non-Goals

The vision's [non-goals](../00-discover/product-vision.md#non-goals) apply. Each excludes something a reasonable reader might assume is in scope:

- No user interface beyond the reference clients, and no HTTP, WebSocket, or server-sent-events surface in the library.
- No multi-agent orchestration graph or handoff model.
- No AgentKit wrapper or second harness runtime. The consuming application retains its existing Databricks Apps server; the runtime replacement criterion is recorded in ADR-003.
- No bundled coding agent; optional native SDKs stay behind selected adapter boundaries.
- No blocking synchronous I/O facade; pure planners, factories and non-I/O callbacks may be synchronous.
- No provider SDK types in public core APIs and no provider SDK in the base install. Explicit optional adapters may use direct/transitive SDKs under revised ADR-012; SDK use alone does not disqualify reuse.
- No plug-in or workflow manifest format: an application compiles its own declarations to the specification the loop reads.
- No retrieval, embedding, or vector-store toolkit.
- No destructive session/history deletion API in the initial scope: closing retains accepted records; external retention gaps are explicit. Cleanup must not discard referenced content or unresolved execution facts.
- No automatic resume of a turn as a user without that user's live identity, and no stored credentials.

Deferred items are tracked in `docs/helix/parking-lot.md` (created when the first item is parked).

## Users and Scope

These personas are assumptions from the vision, not research findings; validate them in `frame`.

### Primary Persona: Maya, Python application developer

**Role**: A developer building an internal or product agent in Python.
**Goals**: Judge the harness in minutes, write tools as functions, get durable sessions and traces without building them, and put her own interface on top.
**Pain Points**: She has to build the loop, the session store, recovery, and the UI feed herself, or take on a framework that owns her state; she cannot test against a recorded conversation; she cannot tell in seconds whether a harness suits her.

### Secondary Persona: Sam, platform engineer

**Role**: Builds a multi-user platform on an agent runtime.
**Goals**: Per-user identity on every model call, policy at the model, tool, and read boundaries, an audit record, recovery of crashed turns, and storage of his choice.
**Pain Points**: Extension code can bypass the controls he writes; runtimes assume one user or one storage owner; resuming a crashed turn needs the user's identity, which must not be stored.

### Tertiary Persona: Jo, contributor

**Role**: A developer or user who wants a change.
**Goals**: Propose it without signing anything and see it implemented.
**Pain Points**: A legal agreement or a style fight stops casual contributors.

## Requirements

Each requirement traces to the vision's value propositions. Exact signatures, entry shapes, event shapes, and error semantics are in the contracts under `docs/helix/02-design/contracts/`, not here. The first release (0.1) contains every P0 requirement. ADR-002/003 select Pydantic AI Agent as the runner. These requirements govern its Tether integration and qualified profiles; selecting a runner does not imply every release acceptance gate has passed.

### Must Have (P0)

1. The asynchronous loop and the model port, with the default OpenAI-compatible adapter and scripted, recording, and replay test doubles (FR-1 to FR-5, FR-38).
2. The tool-call interface with typed arguments, approval bound to one invocation, typed results, the replay marker, and intent recorded before an effectful or replay-unsafe tool runs (FR-6 to FR-10, FR-42).
3. Sessions as append-only logs committed step by step, one turn per session, idempotent create and send, an in-memory live store and archive, and a local single-writer JSONL execution journal and a multiprocess Postgres live store with an optional embedded server (FR-11 to FR-13, FR-16, FR-40, FR-41).
4. Detection of an interrupted turn and its explicit resume (FR-14, FR-34).
5. The client API: list, read, follow, and commands (FR-17 to FR-20).
6. Spans and an audit record without content, and token usage (FR-22, FR-23, FR-25).
7. The test kit: a dummy for every port, conformance suites, and cassette record and replay (FR-28).
8. The command-line client, the terminal UI, and the zero-configuration demo (FR-29, FR-30, FR-33).
9. Dual licensing, a minimal-dependency core, and the contribution policy (FR-31, FR-32, FR-36).
10. Explicit model request bounds, source-addressed context selection and compaction, governed auxiliary summary calls, effect resolution, actual worker-lifetime limits, and reconnect snapshots (FR-35, FR-44 to FR-50, FR-52, FR-53).
11. Engine policy/gate/provider ports with no-op dummies and optional scope/guard implementations; authorization and fail-closed enforcement apply from the first runnable slice (FR-26, FR-27).

### Should Have (P1)

The immediately next slice after 0.1 publication is the consumer contract and compatibility work in FR-54 to FR-64. It precedes consumer adoption and the other P1 enhancements below. The proposed contract paragraphs require owner acceptance before dependent implementation; this sequencing does not reopen 0.1 P0 qualification.

1. Measured performance budgets for the client API, protected in CI (FR-21).
2. The OpenTelemetry adapter (FR-24).
3. Opt-in automatic resume for applications that supply an identity (FR-37).
4. The Databricks AI Gateway profile, with an authoritative model-exchange history provider and writable supplement (FR-39, FR-43, FR-51).
5. Target-specific optional reconcilers for unknown tool outcomes; manual resolution and the barrier are P0 (FR-44, FR-46).

### Nice to Have (P2)

1. Fork from any entry (FR-15).
2. An MCP (Model Context Protocol) client adapter for tools.
3. Parallel execution of independent tool calls in one reply.
4. Subagents as sessions owned by a tool call.
5. A SQLite live store, other store adapters, and server adapters (HTTP, WebSocket) over the client API.

Pydantic AI Agent/model reuse is the selected implementation under ADR-002/003, not a P2 product feature.

### Requirement priority index

This index fixes release scope for every stable functional requirement. FR-44's manual resolution is P0; its target-specific reconciler is P1. Generic history/correlation and external-readiness behavior are P0 and can be proved with controlled providers; shipping and live qualification of the optional Gateway provider remain P1.

| Priority | Functional requirement IDs |
|----------|---------------------------|
| P0 | FR-1 to FR-14; FR-16 to FR-20; FR-22 to FR-23; FR-25 to FR-36; FR-38; FR-40 to FR-42; FR-44 to FR-50; FR-52 to FR-53 |
| P1 | FR-21; FR-24; FR-37; FR-39; FR-43; FR-51; FR-54 to FR-64 (immediately next consumer slice); target-specific reconciliation portion of FR-44 |
| P2 | FR-15; the separately listed P2 extensions |

## Functional Requirements

### Subsystem: Agent Loop and Model Port

- **FR-1** — A turn takes one user message and runs model and tool steps until the model answers, a step limit is reached, a gate stops it, an approval is pending, or the model is unavailable, then ends with exactly one stop reason.
- **FR-2** — The loop calls models only through a provider-neutral model port. A default adapter for the OpenAI-compatible protocol, a scripted model, a recording model, and a replay model implement it.
- **FR-3** — Reply text streams to the caller as the model produces it, and an outage after some text discards that text from the transcript.
- **FR-4** — Each model attempt has an explicit identity mode. With a caller credential it uses that actor's current credential; configured application identity or anonymous operation is permitted only when selected, and require-user mode refuses fallback. Concurrent actors never share or inherit another actor's credential. Correlation tags identify session/turn/call/attempt independently of identity.
- **FR-5** — A model outage ends the turn with a short reason and the provider's request id, and never exposes request or response content.
- **FR-38** — A developer configures a server implementing the declared OpenAI-compatible chat-completions subset, including local and self-hosted servers, by configuration alone, with an SDK-free base/custom-model path available; selected optional adapters have explicit qualified SDK closures; non-reserved server-specific request parameters (sampling, caching, grammar) pass through unchanged; a value conflicting with the explicit output limit is refused.

### Subsystem: Tools

- **FR-56** — P1 next consumer slice: a tool may stage application writes for durable commit/rollback settlement after result guards. Shared-PostgreSQL and independent-backend profiles retain explicit decisions, same-id reconciliation and uncertainty barriers; generic tool context exposes no raw connection.
- **FR-58** — P1 next consumer slice: applications can atomically publish immutable tool-registry generations while active invocations retain their pinned definitions; changed pending approvals invalidate and current policy revocation still applies.
- **FR-60** — P1 next consumer slice: an optional trusted approval transform preserves original, shown and executable arguments under separate digests, enforces tool-owned semantic restriction and re-enters every applicable gate before intent.

- **FR-6** — A tool is a plain `def` or `async def` function with typed arguments. Its schema is derived from the types, arguments are validated before the call, and a plain function never blocks the event loop.
- **FR-7** — Tools are registered by name, uniqueness is enforced, and a client can list the tools a session offers.
- **FR-8** — A tool can require approval before it runs. The turn ends with a pending confirmation that a later call answers, and the answer resumes the turn. A new send to the session supersedes a pending approval.
- **FR-9** — A tool call ends in a typed result (ok, error, denied, withheld, or interrupted) that the model reads and the record names; operational telemetry/audit facts for denied or invalid calls never include argument values. The original model message may retain proposed arguments in authorized authoritative history; that retention is not proof the call was approved or executed.
- **FR-10** — A tool declares whether it is safe to rerun after an interrupted turn, and the default is that it is not.
- **FR-42** — Before an effectful tool runs, including a replay-safe writer, the log records an intent entry carrying a stable invocation id derived from the session, the assistant entry, and the call position, so recovery can tell not-started, started, finished, and resolved calls apart. An approval binds to one invocation, its arguments digest, and its tool-definition digest, satisfies only the approval gate, and every gate applies again when it is used. The library does not claim exactly-once external effects. Effect-free and replay-safe are separate tool declarations; effectful calls receive an intent even when safe to repeat under the same invocation key.

### Subsystem: Sessions

- **FR-55** — P1 next consumer slice: bounded asynchronous preparation runs under the accepted turn lease after input deduplication, may wait/refuse/request input, preserves accepted text/attachment identity and forbids model egress on failure.

- **FR-11** — A session has an append-only execution journal of facts and immutable content refs. Each step commits atomically at the store's declared durability before the next step; volatile stores declare no restart guarantee. Original content is retained through a separately selected HistoryProvider with qualified durability/readiness.
- **FR-12** — A session can be created, closed, titled, tagged, and given settings, and each of these is recorded in the log.
- **FR-13** — A session runs one turn at a time. Different sessions run concurrently.
- **FR-15** — A new session can be forked from any entry of an existing one.
- **FR-16** — Sessions are stored through two ports: a live store, authoritative for in-flight and recent sessions, and an optional archive for finished ones. Each declares its capabilities (durability, lease scope, fencing, notifications; authoritative or derived, writable, lossless). In-memory test stores, a local single-writer JSONL live journal/history pair, and an optional Postgres live store ship; Postgres supports multiple processes and may start an optional embedded PostgreSQL 18.
- **FR-40** — An archive can be derived from a platform's own log, such as a gateway's inference tables: read-only, partial, read as the calling actor, bounded, and reporting pending, denied, or unavailable rather than an empty result. Archiving never sits in a turn's path.
- **FR-41** — Creating a session with an application reference is idempotent per owner, a session can be found by that reference, and a send carrying a request id is idempotent; the execution journal owns order/control, the selected history provider owns referenced content, and the application holds the session reference.

### Subsystem: Crash Recovery and Resume

- **FR-14** — A turn whose process died is detected from its expired lease and closed in the log as interrupted, and its session is listed as interrupted.
- **FR-34** — A caller with a live identity can resume an interrupted turn. The resume continues from the last committed step: a call with an intent and no result is rerun under the SAME invocation id only if the recorded/current tool digests match and it is declared replay-safe and otherwise recorded as interrupted with an unknown outcome, a call never started runs fresh, then the model is called again; each committed step appears once; every gate applies again; and a chain of consecutive interruptions is bounded.
- **FR-37** — An application can opt in to automatic resume by supplying a function that returns an identity for a session, or declines it.

### Subsystem: Client API

- **FR-54** — P1 next consumer slice: a Client-origin tool command uses the same lease, source, validation, policy and intent/result pipeline without a model call. It records its origin and refuses approval-required calls before dispatch.

- **FR-17** — A client lists sessions for an owner with filters and ordering, in pages, from summaries the store keeps current, without reading transcripts.
- **FR-18** — A client reads one session's view and pages through its entries in either direction.
- **FR-19** — A client follows changes (session updates, appended entries, reply text, pending confirmations) from a position it can resume, receives a resync signal when its position is too old, and cannot slow the loop.
- **FR-20** — A client creates a session, sends a message, answers a confirmation, resumes an interrupted turn, cancels a turn, closes a session, and changes its settings, through the same API.
- **FR-21** — The API's latency and memory stay within budgets that benchmarks measure and protect against regression.

### Subsystem: Telemetry

- **FR-64** — P1 next compatibility slice: Engine composition can select a stable span prefix and attribute namespace while preserving canonical metadata semantics and content exclusion. The current qualified implementation uses fixed `tether` names; configurable naming remains proposed.

- **FR-22** — The loop emits spans for each turn, model call, and tool call, carrying trace context across awaits.
- **FR-23** — The loop emits one audit entry per step and one per gate decision, carrying identifiers, sizes, and reasons.
- **FR-24** — Tracing is a port with a no-op default and an OpenTelemetry (OTel) adapter as an optional extra.
- **FR-25** — Token usage is recorded per model call and totaled per session in the summaries.

### Subsystem: Governance

- **FR-57** — P1 next consumer slice: an authorized specification transition between completed call groups pins the new id/version/digest, closes or refuses outstanding old-spec calls and invalidates incompatible approvals before the next step.
- **FR-59** — P1 next consumer slice: application-scheduled auxiliary model calls select an explicit purpose/profile, use current caller identity/read/model policy and per-attempt usage, and refuse missing caller credentials without application fallback.

- **FR-26** — A policy port decides every model call, tool call, data read, and resume, defaults to allow-all, and fails closed when it errors.
- **FR-27** — Scope keys, result guards, and providers extend the loop. A provider can refuse, hide, or redact and can never widen a decision, and a provider that errors is treated as a refusal.

### Subsystem: Test Kit

- **FR-28** — Every port has a dummy implementation and a conformance suite, a model conversation can be recorded on fabricated prompts and replayed, and a test can drive the loop with scripted approvals.

### Subsystem: Reference Clients

- **FR-29** — A command-line client lists sessions, shows a session, sends a message, follows a session, answers a confirmation, resumes an interrupted turn, and replays a cassette.
- **FR-30** — A terminal UI shows the session list and a live transcript and handles approvals and resume.
- **FR-33** — After cloning the repository, one command starts the terminal UI against a built-in scripted demo model and a local store, with no configuration, no network access, and no credential, and the demo exercises streaming, a tool call, and an approval.

### Subsystem: Databricks Profile

- **FR-39** — A profile for the Databricks AI Gateway, delivered as an optional extra over the default adapter, supplies the route, the tier-to-service mapping, attribution tags, and identity modes (including one that requires the calling user's own token); the Databricks AI Gateway is supported and is never the only backend.
- **FR-43** — The profile supplies GatewayHistory as an authoritative model-exchange source behind HistoryProvider, with a required writable supplement, bounded caller-authorized reads and exact correlation qualification (FR-50 to FR-53). Legacy partial archive import is optional and is not the live history authority.
- **FR-44** — P0: a present, authorized caller can resolve an unknown tool outcome with a recorded reason and one-winner conflict handling. Resolution does not execute a tool. P1: a tool may supply a read-only reconciler; a claimed not-completed outcome does not itself authorize another execution.

### Subsystem: Context Management

- **FR-35** — P0: an application can select full, recent, or summarized model context through a replaceable source-addressed strategy. The engine validates complete source coverage, protects current instructions and unsettled tool groups, records summaries and omissions with provenance, and never changes original entries or derives execution state from a summary.
- **FR-45** — P0: every main and auxiliary model request uses explicit model limits and full-payload estimation with output reservation, labelled estimate quality, source byte/call/deadline limits, and an explicit failure if it cannot fit. Summaries use the same model port, current identity, authorization and usage accounting, offer no tools, and cannot silently discard context on failure.
- **FR-46** — P0: every unresolved effectful outcome blocks every new effectful invocation in that session until authorized resolution. A different call id, arguments, tool name or approval cannot bypass the barrier. Effect-free calls may continue. Replay-safe means convergence under the SAME invocation id; it is not permission to issue a new idempotency key. Only a not-yet-settled replay-safe invocation may recover under that same id/digest and fresh gates. No semantic-equivalence or cross-session deduplication guarantee is claimed.
- **FR-47** — P0: synchronous workers retain executor capacity until actual completion; cancelling their await does not free a permit or claim the effect stopped. Same-process replacement turns and resolution wait for worker exit; shutdown has a finite configured deadline and reports outstanding work.
- **FR-48** — P0: an atomic owner-scoped snapshot binds the session view and pending approval to an entry head and change watermark. Reconnect resumes without a gap; provisional text is attempt-scoped and reset on outage, lag or reconnect. Pending approval arguments are available without transcript scans.
- **FR-49** — P0: malformed/truncated model output never becomes an executed repaired call or a successful final answer. Output/argument bytes are bounded, terminal reasons are explicit, and retry waits/call counts are finite and cancellable.

### Subsystem: Authoritative History

- **FR-61** — P1 next consumer slice: Postgres history and journal sharing one verified root use the qualified shared-transaction profile by default, atomically accepting content and references with recoverable operation keys. Independent backends retain explicit put-before-reference semantics.

- **FR-50** — P0: authoritative original content/model exchanges are accessed through a replaceable HistoryProvider, default local JSONL, independently of the execution journal and derived model/SDK context. Reference clients retain local session metadata/journal across restart. Provider capabilities qualify durability and coverage.
- **FR-51** — P1: GatewayHistory resolves externally logged model exchanges by exact per-attempt correlation and uses a writable supplement for source content the gateway never receives. The journal keeps only facts/refs plus explicitly bounded derived interaction state; no default full shadow payload store is required.
- **FR-52** — P0: each physical model attempt is identified and journaled before egress with stable session/turn/logical-call ids, a unique attempt id and purpose. Provider header request id, provider invocation id, response-body id, real trace/span ids and selected endpoint/schema remain distinct and are preserved on errors/streams where observed. Unqualified joins never become ready history.
- **FR-53** — P0: position-paged history is authorized as the current actor and reports ready/pending/partial/denied/unavailable/missing/ambiguous/unsupported/integrity-error states per referenced item. Metadata stays usable during source outage. Gateway latency, duplicates, gaps, retention and missing components never become empty or invented history.

### Subsystem: Packaging and Licensing

- **FR-62** — P1 next compatibility slice: documented conservative configuration defaults and owner overrides reduce first-turn setup; deterministic estimator/Full or Tail composition remains explicit about estimate quality and obtains per-tier capacities only from a selected model profile.
- **FR-63** — P1 next compatibility slice: published dependency compatibility is stated from executed version evidence. Metadata ranges widen only after their boundary versions pass the relevant suites; the lock retains exact qualified versions.

- **FR-31** — Installation has a small typed base, deterministic offline testing and explicit optional provider/model, telemetry and reference-client integrations. Pydantic types and asynchronous operation remain selected. The base includes pydantic-ai-slim for the selected Agent runner under ADR-002/003. Release pins and its exact transitive closure require qualification; foundation-model SDKs remain optional adapter dependencies. Provider SDK isolation follows NFR-8.
- **FR-32** — The library is dual licensed MIT OR Apache-2.0, copyright FocusedDiversity LLC, with both license texts in the package and a history that starts in this repository.
- **FR-36** — The repository states how to contribute: contributions are welcome directly with no contributor license agreement, are accepted as prompts that the maintainers implement, and are licensed as the project is.

### Feature Map

The next consumer requirements map FR-54 to Client API, FR-55/57/59 to Agent/Governance, FR-56/58/60 to Tools/Governance, FR-61 to Sessions/Authoritative History, FR-62/63 to Reference Clients/Packaging and FR-64 to Telemetry. CONTRACT-002/003/005 contain the proposed interface obligations; the implementation plan sequences acceptance before source changes.

Authoritative history and correlation (FR-50 to FR-53) belong to the Sessions and Context Management features and the Databricks adapter where selected; CONTRACT-005 fixes their boundary. Local/Gateway readiness, identity modes and replay-safe effectful writers receive distinct acceptance coverage in TP-001.

Each subsystem becomes one feature specification: FEAT-001 Agent Loop and Model Port, FEAT-002 Tools, FEAT-003 Sessions, FEAT-004 Crash Recovery and Resume, FEAT-005 Client API, FEAT-006 Telemetry, FEAT-007 Governance, FEAT-008 Test Kit, FEAT-009 Reference Clients, FEAT-010 Context Management, FEAT-011 Packaging and Licensing, FEAT-012 Databricks Profile. Stories are in `docs/helix/01-frame/user-stories/`.

## Non-Functional Requirements

- **NFR-1** — Every public operation that waits on I/O is asynchronous; pure planners, factories and non-I/O callbacks may be synchronous. There is no blocking synchronous I/O facade (ADR-001).
- **NFR-2** — The core reads no environment variable; a settings object is built at the composition root.
- **NFR-3** — Every gate fails closed, and a hook or adapter failure is a refusal, never a pass.
- **NFR-4** — Operational telemetry and audit entries contain no message/tool content; authoritative history and authorized fabricated cassettes intentionally do, and no credential ever appears in a log, a cassette, a span, or a stored entry (ADR-011).
- **NFR-5** — The package is typed, passes `pyright` and `ruff`, and targets Python 3.11 as its compatibility floor and later explicitly qualified versions; releases name the interpreter matrix actually tested.
- **NFR-6** — The client API holds bounded memory per subscriber and never blocks the loop on a slow consumer.
- **NFR-8** — Base packaging and public core types are provider-SDK-free (ADR-012). Selected optional adapters may import SDKs directly/transitively; boundary/dependency checks verify isolation and disclosed closures. Pydantic AI Agent/model reuse is selected; optional SDK wrapping alone does not imply hosted-provider lock-in.
- **NFR-7** — Performance targets (proposed, validated by the first baseline). The reference workload is one owner with 10,000 sessions of about 200 entries each, one session of 100,000 entries, entries of about 1 KiB, one process with one event loop, on a laptop-class machine with a local solid-state disk. All latencies are 99th percentile.

| Operation | In-memory store | Durable store (qualified local profile) |
|-----------|-----------------|--------------|
| List a page of 50 summaries | at most 3 ms | at most 10 ms |
| Get a session view | at most 2 ms | at most 5 ms |
| Read a page of 100 entries at any position | at most 5 ms | at most 15 ms |
| Append one step (up to 4 entries, one commit, durable) | at most 2 ms | at most 25 ms |
| Commit to delivery to an in-process follower | at most 5 ms | at most 10 ms |
| Commit to delivery to a follower in another process | not applicable | at most 300 ms |
| Engine overhead per step (gates, spans, events), excluding model, tools, and store | at most 1 ms | at most 1 ms |
| Memory per follower at the default queue size | at most 1 MiB | at most 1 MiB |
| Event-loop lag with 1,000 followers and one streaming turn | at most 20 ms | at most 20 ms |
| `import tether` (core) | at most 300 ms | |
| `tether` command line, start to first output | at most 600 ms | |
| Terminal UI, start to first paint | at most 1.5 s | |

## Acceptance Test Sketches

| Requirement | Scenario | Input | Expected Output |
|-------------|----------|-------|-----------------|
| FR-1, FR-2 | A scripted model answers after one tool call | A scripted reply with a tool call, then a text reply | The turn ends `final`; the transcript holds the message, the call, the result, and the answer, in order |
| FR-3, FR-5 | The model fails mid-stream | A scripted stream of two deltas then an outage | The turn ends `model_unavailable` with a short reason; the discarded deltas are absent from the transcript |
| FR-2, FR-38 | A local server | The adapter pointed at a server on the loopback interface that speaks the protocol, with a sampling parameter in `extra` | The reply streams, a tool call is assembled from pieces, the parameter reaches the server unchanged, and no provider SDK is imported |
| FR-4 | Two actors call concurrently | Two turns on one event loop, each with its own credential | The model double sees each credential only on its own calls |
| FR-6, FR-9 | A tool with a bad argument | A call whose argument has the wrong type | A typed error result reaches the model; the audit entry names the call and never the value |
| FR-8 | An approval tool | A tool marked ask-first | The turn ends `confirmation_required` with the pending call listed; approving it resumes and completes the turn |
| FR-11, FR-13 | A second turn on a running session | A send to a session mid-turn | The second send is refused as in progress; the first turn is unaffected |
| FR-14, FR-34/42/46 | A process killed at each dispatch boundary | Kill before intent; after effectful intent but before dispatch; after observable effect but before result commit | Before intent the call is not started and passes fresh gates. After intent without result its outcome is unknown; only same-invocation replay-safe recovery may rerun. A new effectful invocation stays blocked until resolution. Journal entries are not duplicated; final/blocked/unknown outcomes are explicit |
| FR-17, FR-18 | Listing a large store | Ten thousand sessions, a page size, a cursor | Pages arrive in order without reading transcripts; reading a page of entries returns the requested range |
| FR-19 | A follower resumes | A client disconnects, then follows from its last position | It receives only the changes after that position; a position older than retention yields a resync signal |
| FR-20 | Cancelling a running turn | A cancel during a model stream | The stream stops, the committed steps remain, and the log ends with a cancelled turn entry |
| FR-22, FR-23, NFR-4 | A turn with a recognizable prompt and credential | A fabricated prompt containing a marker string and a credential containing a second marker | The prompt marker is absent from operational logs/spans/audit but intentionally retained in authorized history and fabricated cassettes. The credential marker is excluded from every stored history/journal/cassette/diagnostic field |
| FR-28 | Replay | A recorded cassette | The loop reproduces the saved audit record; an altered request fails naming the differing field |
| FR-29 | Command-line client | `list`, `send`, `follow`, `resume` against a store with a scripted model | The commands show the session, stream the reply, resume an interrupted turn, and exit cleanly |
| FR-30, FR-33 | Clone and try | A fresh clone on a machine with a warm package cache, then `uv run tether` | The terminal UI paints with a demo session within 10 s of the command; a message streams a reply, runs a tool, and asks for approval |
| FR-4 | Explicit identity modes | Caller, configured application and anonymous profiles; expired caller credential in require-user mode | Each selected mode is enforced; require-user refuses fallback and concurrent credentials remain isolated |
| FR-41 | Send deduplication | Same request id with equal text, attachments and requested spec; then change each field separately | Equal semantic input returns the accepted turn; every changed input is an idempotency conflict |
| FR-50 | Local history restart | JSONL content committed before journal refs; kill between writes and restart | Only journal-accepted content appears, original digests/order survive, and unaccepted orphans are not messages |
| FR-52, FR-53; P1 FR-51 | External history and correlation | Delayed, duplicated and multi-invocation rows; unavailable source and unsent local messages | Exact per-attempt joins or explicit holes; supplemental content survives; no inferred execution or blind network retry |
| FR-40 | Archive and restore | Snapshot a closed session with local and externally referenced content; restore as the authorized owner; interrupt archive publication | Publish a complete archive only after verified reference closure; unsupported/pending content stays explicit, accepted live data is retained on failure, restore preserves journal order/digests and cannot grant cross-owner access or authorize execution |
| FR-31, FR-32, FR-36 | Install and contribute | A clean environment with only the base/core package; the repository's contribution page | The core imports and passes its test suite; both license texts ship; the page states the no-CLA, prompt-based policy |
| FR-7, FR-10 | Tool registration and declarations | Duplicate names; read-only/effectful tools with safe/unsafe replay declarations | Duplicate names refuse load; replay safety never grants permission for a new effectful invocation |
| FR-12, FR-16, FR-20 | Session lifecycle | Create/update/close across local durable and explicit volatile profiles; race close with send/confirm/resolve | Changes persist under the declared profile; close refuses active workers/turns/unresolved effects, settles waiting approvals atomically and remains readable/idempotent |
| FR-25 | Usage completeness | Known token totals, cache/reasoning subcounts, a failed attempt with missing usage and duplicate receipt | Known usage is counted once per attempt; subcounts are not added twice; missing usage remains explicitly incomplete |
| FR-26, FR-27 | Current policy and extension failure | Policy deny/error and invalid provider outputs before model/tool dispatch, including a later retry | No denied effect/egress; each actual attempt is authorized; failures are typed terminal outcomes and reasons contain no payload |
| FR-35, FR-45 | Bounded model context | Long authorized history, protected tool groups, summary refusal/failure and changed source access | Originals stay immutable; every source is accounted for; full requests fit declared bounds or stop explicitly; summaries use current identity, policy and accounting |
| FR-42, FR-44, FR-46 | Uncertain effect and resolution | Effectful writer succeeds but result commit is lost; retry under same id, issue a new id, then resolve | Only eligible same-id recovery can converge through fresh gates; new effects are blocked; one authorized resolution wins and never executes a tool |
| FR-47 | Worker cancellation | Cancel a real offloaded tool, exhaust worker permits, request close or replacement work | Capacity remains occupied until actual exit; dependent work refuses; bounded shutdown reports outstanding work without claiming it stopped |
| FR-48 | Reconnect and authorization | Snapshot races a commit; source permission is revoked before history/preview access | Replay has no commit gap; pending metadata is atomic; source arguments hydrate separately; revoked payload cannot leak through cached previews/search |
| FR-49 | Malformed model output | Truncated stream, invalid/duplicate tool ids, oversized arguments or unknown terminal reason | No repaired call executes and no incomplete output becomes final; byte, attempt and wait bounds hold |
| FR-34, FR-42 | Same-invocation recovery | Resume a replay-safe call under a new turn with its original committed intent | Fresh gates run, original intent is reused without a changed-payload key conflict, and accepted results are not duplicated |
| FR-18, FR-52, FR-53 | Call detail and source bindings | One logical call with two attempts, repeated provider ids/multiple invocations; append a verified link then revoke access | Results retain every accepted attempt/typed hole; link resolution is deterministic and digest-checked; no fallback bypasses revocation |

### Next consumer slice acceptance sketches (P1)

These are required future acceptance cases, not executed 0.1 results.

| Requirement | Acceptance sketch |
|---|---|
| FR-54 direct invocation | Invoke without a model; race equal/conflicting commands and close; refuse approval-required/denied calls before dispatch; kill after intent/effect and preserve the uncertainty barrier. |
| FR-55 preparation | Deduplicate accepted input, wait under its lease, then cancel/revoke/expire or return changed attachment identity; only accepted ready input reaches a model. |
| FR-56 settlement | Withhold a staged result and roll back; crash at each stage/decision/participant/result frontier under both transaction profiles; reconcile the same id without inferring an external effect. |
| FR-57 specification transition | Transition between completed groups while an approval/specification changes; pin accepted bindings, refuse unsettled calls and invalidate incompatible approvals across restart. |
| FR-58 registry publication | Add/replace/remove while calls and approvals run; snapshots publish atomically, old actual workers retain their generation and current revocation still denies. |
| FR-59 auxiliary models | Schedule as a caller, remove its credential before egress/retry, revoke source access and cancel; no broader identity fallback, hidden retry or lost purpose-specific usage. |
| FR-60 edited approval | Narrow a list through a tool-owned semantic transform; preserve proposal/shown/executable digests; reject stale, invalid, mutated or conflicting edits before intent. |
| FR-61 shared transaction | Observe from another connection after each injected process/commit fault; content/reference/journal/summary/feed are one accepted batch or absent; recover unknown acknowledgment by key. |
| FR-62 defaults | Run the documented minimal composition and each override; counts stay deterministic and unknown model capacity refuses explicitly; finite bounds remain effective. |
| FR-63 dependency compatibility | Resolve consumer constraints and run the selected contract/codec/typing/installed boundary suites at each proposed dependency range boundary before metadata widens. |
| FR-64 telemetry naming | Select a span prefix and attribute namespace, inspect nested spans across awaits/workers/failures, and verify canonical metadata/content exclusions and fixed journal semantics. |

## Technical Context

This section records decisions; the rationale is in the ADRs.

- **Language/Runtime**: Python 3.11 is the selected release floor; later versions require their own qualification before support is claimed. A downstream consumer needing a different floor triggers a separate compatibility decision. `uv` manages environments and builds (concern `python-uv`).
- **Key Libraries**: `pydantic` v2 and the asynchronous baseline; Pydantic AI Agent and model integration reuse are selected under ADR-002/003; SDK-free base/public core types with isolated optional SDK adapters (ADR-012); SPIKE-003/004 provide bounded evidence; production qualifications are planned in SD-001; Textual for the terminal UI as an extra (ADR-008); the standard library `sqlite3` for the SQLite store (ADR-009); OpenTelemetry as an extra.
- **Data/Storage**: execution journal, authoritative HistoryProvider and optional archive (ADR-018/019). Local reference clients use JSONL history plus a specified single-writer JSONL journal; in-memory providers are explicit volatile dummies. Postgres/optional embedded PostgreSQL 18 supplies the multiprocess journal; qualification remains required. GatewayHistory replaces model-exchange authority with explicit supplemental history. SQLite is optional.
- **APIs**: the model port, the engine ports, and the client API (contracts `CONTRACT-001` to `CONTRACT-005`).
- **Platform Targets**: Linux and macOS.
- **Hosting and CI**: GitHub (`DocumentDrivenDX/tether`) and GitHub Actions (an assumption to confirm).

### Current dependency compatibility

The 0.1 tested compatibility set is Python 3.11.17 with Pydantic 2.14.1, Pydantic AI 2.59.0 and Textual 8.2.8 where selected. Current exact metadata constraints disclose that set. No broader Pydantic, Agent or Textual range is claimed from these passes. FR-63 is near the top of the next compatibility slice: qualify proposed lower/upper versions and consumer resolution together before widening published ranges; retain exact executable pins in `uv.lock`.

## Constraints, Assumptions, Dependencies

### Constraints

- **Technical**: asynchronous only; no environment reads in the core; ports with dummies; telemetry without content; no stored credential.
- **Business**: dual MIT OR Apache-2.0, copyright FocusedDiversity LLC, clean history (ADR-006); contributions accepted as prompts with no CLA (ADR-007).
- **Legal/Compliance**: no private or customer data in the repository, the fixtures, or the recorded conversations.

### Assumptions

- The OpenAI-compatible protocol stays the common ground of the local and self-hosted servers users run; the adapter's conformance matrix finds each server's quirks (SPIKE-002).
- The proposed performance targets are achievable on the reference workload; the first baseline will show where they are not.

### Dependencies

- Selected Pydantic types, asynchronous integration and Pydantic AI Agent via pydantic-ai-slim. Exact release pins, native-model extras and their dependency closure require qualification under ADR-002/003. Reference TUI and telemetry integrations use their selected extras. Qualification pins versions before release.
- Operator decisions listed under Open Questions.

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| The proposed performance targets are wrong | Medium | Medium | The first benchmark baseline replaces each with a measured budget; CI protects the budget afterward |
| Per-step commits cost too much latency on the durable store | Medium | Medium | Batch a step's entries into one commit; measure in the benchmarks before 0.1; document the store's durability settings |
| Local durable startup exceeds the clone-and-try budget | Medium | Medium | Measure the selected JSONL reference profile; optimize startup without silently substituting volatile persistence (ADR-020) |
| A derived archive hides a gap by returning less than the platform holds | Medium | High | It reports pending, denied, or unavailable and never an empty result; capability flags say partial (ADR-019) |
| The ports or the provider surface prove too narrow or too wide | Medium | High | Qualify public consumer-shaped scenarios and refine contracts from integration evidence |
| Resume re-executes a side effect | Medium | High | Tools default to unsafe and are recorded as interrupted, never rerun, unless declared safe; every gate applies again; resume is explicit by default (ADR-010) |
| Server quirks in the OpenAI-compatible protocol break the default adapter | High | Medium | A conformance matrix from cassettes of local and self-hosted servers on fabricated prompts, live on demand; a quirk table in the adapter; server-specific parameters pass through; qualify the selected Pydantic AI integration and use a custom Model only for a demonstrated adapter gap under ADR-002 |
| Asynchronous code is blocked by a plain function | Medium | High | Offload plain functions to worker threads; lint rules for blocking calls in `async def` |
| The unverified competitor cells hide a product that already does this | Medium | High | Validate the remaining cells in `frame` before building past the client API |
| The demo does not feel instant, defeating the first-impression goal | Medium | Medium | The quickstart test is timed in CI; the demo is a P0 requirement with its own budget |
| Prompt-only contributions slow acceptance | Medium | Low | The contribution page sets expectations; maintainers answer prompts in the issue |

## Open Questions

- Explicit, present-actor resume is the baseline; automatic resume remains an opt-in requiring a supplied identity (FR-34/37). Local durable reference clients use report mode; explicit volatile demos use off. This is not a missing product decision.
- Context projection and compaction are P0 under the owner's pre-release direction to complete the design now. ADR-021 and CONTRACT-004 define the surface; TP-001 defines required qualification. Limits and summary fidelity remain measured/configured quantities, not assumed support.
- The reference local default is the specified JSONL history/journal pair (ADR-020); its crash/atomicity/index behavior needs qualification. Embedded Postgres remains optional, separately measured.
- Authoritative content by reference is selected: CONTRACT-005 and ADR-018/019 separate history from execution facts and archives; JSONL is the local default and Gateway model exchanges use exact correlation plus writable supplemental history.
- Inference rows own observed model content, not Tether session metadata or effect/approval state. Their retention and availability cannot authorize journal eviction; a complete archive requires reference closure (CONTRACT-003/005).
- CI service and packaging automation are implementation/deployment choices. Routine pull-request CI may run a smaller gate to conserve hosted capacity. The complete required conformance, acceptance, installed-profile and artifact gates must run on the selected release source before tagging; the execution route does not change the behavioral requirements.
- Python 3.11 is the recorded compatibility target; the implementation must qualify its selected dependencies and interpreter matrix. A consumer requiring another floor triggers an explicit compatibility change.

### Consumer decisions awaiting Erik LaBianca

The supplied consumer proposal labels these choices as assumptions. They remain open; none is silently accepted by this plan or a successful 0.1 release. Only dependent implementation/adoption work waits for the relevant choice.

| Decision | Proposed default from consumer request | Owner / state |
|---|---|---|
| Last-turn replacement | Immutable retraction after cutover; refuse unresolved effects | Erik LaBianca — open |
| Batch approval required for cutover | No; serial answers | Erik LaBianca — open |
| Existing-session import required for cutover | No; allow adoption without import | Erik LaBianca — open |
| Application PostgreSQL settlement profile | Shared transaction when the verified root is shared | Erik LaBianca — open |
| Original-transcript versus exchange-payload read permission | Keep two actions, matching the current implementation | Erik LaBianca — open; current protections remain enforced |
| Ongoing release channel and signing authority | PyPI through the existing local publication route; signing authority unspecified | Erik LaBianca — open; the separately authorized 0.1 publication does not establish a signing policy |

Batch approval, immutable retraction, summary facets/bulk lookup, existing-session import and cross-process live text remain nonblocking cutover enhancements. A second process/window sees committed entries when their step commits; token-by-token live delivery requires a separately qualified transport. Committed-result fallback remains authoritative.

## Success Criteria

- Every P0 acceptance sketch passes the complete release qualification gate on the selected source commit before tagging. Routine pull-request CI is a subset and does not qualify a release.
- A developer completes the quickstart with the terminal UI against the demo model, then a real provider, then an optional qualified Postgres journal, changing only configuration; local JSONL sessions remain the reference default.
- On the declared durable journal/history profile, interruption preserves accepted steps exactly once and explicit resume reports completed, blocked or unknown state without duplicating an unqualified external effect.
