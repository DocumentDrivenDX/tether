---
ddx:
  id: prd
  type: prd
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
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
kind: product
---

# Product Requirements Document

## Summary

Tether is an open-source Python library for building agent harnesses. It provides an asynchronous agent loop with a provider-neutral model port, a typed tool-call interface, durable session management, and telemetry. It exposes the session list and session data to a user interface through a fast in-process Python API (list, read in pages, follow live changes), and it ships a simple command-line client and terminal UI for development and testing. Python developers and platform teams who embed an agent runtime in their own services use it, keeping their own interface, storage, and policy. Success is that every port has a dummy and a conformance suite, that recorded conversations replay deterministically through the loop, that turns survive process death, that a UI can list, read, and follow sessions within budgets the owner sets from a measured baseline, and that the first downstream platform runs on a tether release.

## Problem and Goals

### Problem

A team that wants an agent runtime it can embed has three poor options. It can write its own loop on a provider SDK and then build session storage, resume, telemetry, and a UI feed itself. It can adopt a framework that owns the control flow and the paused turn's state (LangGraph, the OpenAI Agents SDK), which collides with an application that already owns its sessions. Or it can follow Pi Durable's design, which is a durable harness, in TypeScript, for a single storage owner. None of the products surveyed ([competitive analysis](../00-discover/competitive-analysis.md)) documents a boundary through which a UI lists sessions, reads transcripts, and follows live turns, and the Python options offer no way to replay a recorded model conversation against the loop. The cost is that each team rebuilds the same runtime and cannot test it against real conversations.

### Goals

1. A Python developer reaches a durable, observable agent with a live interface in one sitting, using plain functions as tools.
2. A UI of any kind reads sessions through one Python API that stays fast as sessions and transcripts grow.
3. The loop is testable alone: every external system is behind a port with a dummy, and recorded conversations replay deterministically.
4. A platform that serves many users puts its policy at ports that extension code cannot widen.
5. A turn that dies with its process is detected and resolved, and no completed step is lost or duplicated.

### Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Ports with a dummy implementation that passes its conformance suite | 100% | The conformance suites run in CI |
| Recorded scenarios that replay through the loop and reproduce the saved audit record | 100% of shipped scenarios | The replay tests run in CI |
| A turn killed between steps resumes or reports interrupted, with each completed step recorded once | Pass | A kill-between-steps test in CI |
| Synchronous entry points in the public API | 0 | A review check and an API listing test |
| Message or tool content found in spans and audit entries | 0 | A telemetry content test in CI |
| Session list, transcript page, and change-feed latency, and memory held per subscriber | Budgets set by Erik LaBianca after the first baseline; no regression afterward | The benchmark suite in CI |
| Time from an empty project to a running agent with the reference client | Target set by Erik LaBianca after the first measurement | The quickstart test in CI |

### Non-Goals

The vision's [non-goals](../00-discover/product-vision.md#non-goals) apply. Each excludes something a reasonable reader might assume is in scope:

- No user interface beyond the reference clients, and no HTTP, WebSocket, or server-sent-events surface in the library.
- No multi-agent orchestration graph or handoff model.
- No bundled coding agent, and no model provider SDK.
- No synchronous interface.
- No plug-in or workflow manifest format: an application compiles its own declarations to the specification the loop reads.
- No retrieval, embedding, or vector-store toolkit.

Deferred items are tracked in `docs/helix/parking-lot.md` (created when the first item is parked; candidates are listed under Priority below).

## Users and Scope

These personas are assumptions from the vision, not research findings; validate them in `frame`.

### Primary Persona: Maya, Python application developer

**Role**: A developer building an internal or product agent in Python.
**Goals**: Write tools as functions, get durable sessions and traces without building them, and put her own interface on top.
**Pain Points**: She has to build the loop, the session store, resume, and the UI feed herself, or take on a framework that owns her state; she cannot test against a recorded conversation.

### Secondary Persona: Sam, platform engineer

**Role**: Builds a multi-user platform on an agent runtime.
**Goals**: Per-user identity on every model call, policy at the model, tool, and read boundaries, an audit record, and storage of his choice.
**Pain Points**: Extension code can bypass the controls he writes; runtimes assume one user or one storage owner.

## Requirements

Each requirement traces to the vision's value propositions. Exact signatures, entry shapes, event shapes, and error semantics are in the contracts under `docs/helix/02-design/contracts/`, not here.

### Must Have (P0)

1. The asynchronous loop and the model port, with a Pydantic AI adapter and scripted, recording, and replay test doubles (FR-1 to FR-5).
2. The tool-call interface with typed arguments, approval, and typed results (FR-6 to FR-9).
3. Sessions as append-only logs committed step by step, with an in-memory store and a one-turn-per-session guarantee (FR-11 to FR-13, FR-16).
4. The client API: list, read, follow, and commands (FR-17 to FR-20).
5. Spans and an audit record without content (FR-22 to FR-25).
6. The test kit: a dummy for every port, conformance suites, and cassette record and replay (FR-28).
7. A minimal command-line client (FR-29).
8. Dual licensing and a core with minimal dependencies (FR-31, FR-32).

### Should Have (P1)

1. Crash recovery of in-flight turns and the tool replay marker (FR-14, FR-10).
2. A durable SQLite store (FR-16).
3. Governance ports: policy, scope, result guards, and narrow-only providers (FR-26, FR-27).
4. The terminal UI client (FR-30).
5. Measured performance budgets for the client API (FR-21).

### Nice to Have (P2)

1. Fork and rewind from any entry (FR-15).
2. Context compaction for long sessions.
3. An MCP (Model Context Protocol) client adapter for tools.
4. Parallel execution of independent tool calls in one reply.
5. Subagents as sessions owned by a tool call.
6. Postgres and other store adapters, and server adapters (HTTP, WebSocket) over the client API.

## Functional Requirements

### Subsystem: Agent Loop and Model Port

- **FR-1** — A turn takes one user message and runs model and tool steps until the model answers, a step limit is reached, a gate stops it, an approval is pending, or the model is unavailable, then ends with exactly one stop reason.
- **FR-2** — The loop calls models only through a provider-neutral model port. A Pydantic AI adapter, a scripted model, a recording model, and a replay model implement it.
- **FR-3** — Reply text streams to the caller as the model produces it, and an outage after some text discards that text from the transcript.
- **FR-4** — Every model call carries the caller's identity and the session's tags to the provider, and concurrent calls for different users never share an identity.
- **FR-5** — A model outage ends the turn with a short reason and the provider's request id, and never exposes request or response content.

### Subsystem: Tools

- **FR-6** — A tool is a plain `def` or `async def` function with typed arguments. Its schema is derived from the types, arguments are validated before the call, and a plain function never blocks the event loop.
- **FR-7** — Tools are registered by name, uniqueness is enforced, and a client can list the tools a session offers.
- **FR-8** — A tool can require approval before it runs. The turn ends with a pending confirmation that a later call answers, and the answer resumes the turn.
- **FR-9** — A tool call ends in a typed result (ok, error, denied, or withheld) that the model reads and the record names; a denied or invalid call never records its argument values.
- **FR-10** — A tool declares whether it is safe to rerun after an interrupted turn.

### Subsystem: Sessions

- **FR-11** — A session is an append-only log of typed entries, and each step's entries are committed durably before the next step starts.
- **FR-12** — A session can be created, closed, titled, tagged, and given settings, and each of these is recorded in the log.
- **FR-13** — A session runs one turn at a time. Different sessions run concurrently.
- **FR-14** — A turn interrupted by process death is detected on restart and resumed or marked interrupted, with each completed step recorded once.
- **FR-15** — A new session can be forked from any entry of an existing one.
- **FR-16** — Sessions are stored through a port. An in-memory store ships first, and a SQLite store follows.

### Subsystem: Client API

- **FR-17** — A client lists sessions for an owner with filters and ordering, in pages, from summaries the store keeps current, without reading transcripts.
- **FR-18** — A client reads one session's view and pages through its entries in either direction.
- **FR-19** — A client follows changes (session updates, turn events, reply text, pending confirmations) from a position it can resume, receives a resync signal when its position is too old, and cannot slow the loop.
- **FR-20** — A client creates a session, sends a message, answers a confirmation, cancels a turn, closes a session, and changes its settings, through the same API.
- **FR-21** — The API's latency and memory stay within budgets that benchmarks measure and protect against regression.

### Subsystem: Telemetry

- **FR-22** — The loop emits spans for each turn, model call, and tool call, carrying trace context across awaits.
- **FR-23** — The loop emits one audit entry per step and one per gate decision, carrying identifiers, sizes, and reasons.
- **FR-24** — Tracing is a port with a no-op default and an OpenTelemetry (OTel) adapter as an optional extra.
- **FR-25** — Token usage is recorded per model call and totaled per session in the summaries.

### Subsystem: Governance

- **FR-26** — A policy port decides every model call, tool call, and data read, defaults to allow-all, and fails closed when it errors.
- **FR-27** — Scope keys, result guards, and providers extend the loop. A provider can refuse, hide, or redact and can never widen a decision, and a provider that errors is treated as a refusal.

### Subsystem: Test Kit

- **FR-28** — Every port has a dummy implementation and a conformance suite, a model conversation can be recorded on fabricated prompts and replayed, and a test can drive the loop with scripted approvals.

### Subsystem: Reference Clients

- **FR-29** — A command-line client lists sessions, shows a session, sends a message, follows a session, answers a confirmation, and replays a cassette.
- **FR-30** — A terminal UI shows the session list and a live transcript and handles approvals.

### Subsystem: Packaging and Licensing

- **FR-31** — The core package depends only on `pydantic` and `anyio`; the model adapter, store, telemetry, and clients are optional extras.
- **FR-32** — The library is dual licensed MIT OR Apache-2.0, copyright Synaptiq, with both license texts in the package and a history that starts in this repository.

### Feature Map

Each subsystem becomes one feature specification in `frame` (`FEAT-001` to `FEAT-009`, in the order above).

## Non-Functional Requirements

- **NFR-1** — The public API is asynchronous throughout (ADR-001).
- **NFR-2** — The core reads no environment variable; a settings object is built at the composition root.
- **NFR-3** — Every gate fails closed, and a hook or adapter failure is a refusal, never a pass.
- **NFR-4** — Telemetry and audit entries contain no message or tool content by default.
- **NFR-5** — The package is typed, passes `pyright` and `ruff`, and supports Python 3.11 and later.
- **NFR-6** — The client API holds bounded memory per subscriber and never blocks the loop on a slow consumer.

## Acceptance Test Sketches

| Requirement | Scenario | Input | Expected Output |
|-------------|----------|-------|-----------------|
| FR-1, FR-2 | A scripted model answers after one tool call | A scripted reply with a tool call, then a text reply | The turn ends `final`; the transcript holds the message, the call, the result, and the answer, in order |
| FR-3, FR-5 | The model fails mid-stream | A scripted stream of two deltas then an outage | The turn ends `model_unavailable` with a short reason; the discarded deltas are absent from the transcript |
| FR-4 | Two users call concurrently | Two turns on one event loop, each with its own identity | The model double sees each identity only on its own calls |
| FR-6, FR-9 | A tool with a bad argument | A call whose argument has the wrong type | A typed error result reaches the model; the audit entry names the call and never the value |
| FR-8 | An approval tool | A tool marked ask-first | The turn ends `confirmation_required` with the pending call listed; approving it resumes and completes the turn |
| FR-11, FR-13 | A second turn on a running session | A send to a session mid-turn | The second send is refused as in progress; the first turn is unaffected |
| FR-17, FR-18 | Listing a large store | Ten thousand sessions, a page size, a cursor | Pages arrive in order without reading transcripts; reading a page of entries returns the requested range |
| FR-19 | A follower resumes | A client disconnects, then follows from its last position | It receives only the changes after that position; a position older than retention yields a resync signal |
| FR-20 | Cancelling a running turn | A cancel during a model stream | The stream stops, the committed steps remain, and the log ends with a cancelled turn entry |
| FR-22, FR-23, NFR-4 | A turn with a recognizable prompt | A fabricated prompt containing a marker string | The marker appears in no span attribute and no audit entry |
| FR-28 | Replay | A recorded cassette | The loop reproduces the saved audit record; an altered request fails naming the differing field |
| FR-29 | Reference client | `list`, `send`, `follow` against the in-memory store with a scripted model | The commands show the session, stream the reply, and exit cleanly |
| FR-31, FR-32 | Install | A clean environment with only the core package | It imports and passes the core test suite; both license texts are in the distribution |

## Technical Context

This section records decisions; the rationale is in the ADRs.

- **Language/Runtime**: Python 3.11 and later (the floor is an assumption: the lowest runtime of the first downstream consumer; see the vision's assumptions). `uv` for environments and builds (concern `python-uv`).
- **Key Libraries**: `pydantic` v2 for models and validation; `anyio` for thread offload and cancellation scopes; Pydantic AI behind the model port as an extra (ADR-002); `httpx` for the adapter; OpenTelemetry as an extra.
- **Data/Storage**: an in-memory store and a SQLite store; other stores through the port.
- **APIs**: the model port, the engine ports, and the client API (contracts `CONTRACT-001` to `CONTRACT-003`).
- **Platform Targets**: Linux and macOS.

## Constraints, Assumptions, Dependencies

### Constraints

- **Technical**: asynchronous only; no environment reads in the core; ports with dummies; telemetry without content.
- **Business**: dual MIT OR Apache-2.0, copyright Synaptiq, clean history (ADR-006).
- **Legal/Compliance**: no private or customer data in the repository, the fixtures, or the recorded conversations.

### Assumptions

- The first downstream platform will adopt a tether release once it exists (the motivating consumer).
- Pydantic AI's direct API remains a stable base (pinned exactly; SPIKE-001).

### Dependencies

- Pydantic AI, `pydantic`, `anyio`, `httpx`, and the OTel libraries for the extras.
- Operator decisions listed under Open Questions.

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| The client API is slow or heavy at scale | Medium | High | Materialized summaries and keyset pages by design (ADR-004); a benchmark suite with budgets owned by Erik LaBianca, run in CI |
| Per-step commits cost too much latency | Medium | Medium | Batch the entries of one step into one commit; measure in the benchmarks before 0.1 |
| The provider surface or the ports prove too narrow or too wide | Medium | High | Port the first downstream platform's features through them early; amend the contracts before release |
| Pydantic AI changes incompatibly | Medium | Medium | Pin exactly; the adapter has wire tests; the port is unchanged if the adapter is replaced |
| Asynchronous code is blocked by a plain function | Medium | High | Offload plain functions to worker threads; lint rules for blocking calls in `async def` |
| The unverified competitor cells hide a product that already does this | Medium | High | Validate the cells in `frame` before building past the P0 set |

## Open Questions

- [ ] Is crash recovery required for the first release, or may 0.1 persist per step and recover in 0.2? Blocks the release plan. Ask Erik LaBianca.
- [ ] The performance budgets for list, page, and follow. Blocks FR-21 acceptance. Ask Erik LaBianca after the first benchmark baseline.
- [ ] The target time for the quickstart. Blocks the quickstart acceptance. Ask Erik LaBianca after the first measurement.
- [ ] The legal entity name for the copyright notice (currently "Synaptiq"). Blocks the first publication. Ask Erik LaBianca.
- [ ] Contribution policy (a developer certificate of origin or a contributor license agreement). Blocks accepting outside contributions. Ask Erik LaBianca.
- [ ] Which terminal UI library the reference TUI uses. Blocks FR-30. Ask Erik LaBianca; the recommendation is Textual, to be recorded in an ADR.
- [ ] SQLite access: the standard library `sqlite3` behind thread offload, or `aiosqlite`. Blocks FR-16 (SQLite). Recommendation: the standard library, to avoid a dependency; record in an ADR.

## Success Criteria

- Every P0 acceptance sketch passes in CI.
- A developer completes the quickstart with the reference client against a scripted model, then a real provider, then the SQLite store, changing only configuration.
- The first downstream platform replaces its in-house loop with a tether release and keeps its session, policy, and audit behavior.
