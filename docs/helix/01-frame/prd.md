---
ddx:
  id: prd
  type: prd
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-22
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
kind: product
---

# Product Requirements Document

## Summary

Tether is an open-source Python library for building agent harnesses. It provides an asynchronous agent loop with a provider-neutral model port, a typed tool-call interface, durable session management with crash recovery, and telemetry. It exposes the session list and session data to a user interface through a fast in-process Python API (list, read in pages, follow live changes), and it ships a command-line client and a terminal UI that work seconds after cloning the repository. It talks to any model server that speaks the OpenAI-compatible protocol, local, self-hosted, sovereign, or hosted, and imports no foundation-model provider's SDK. Python developers and platform teams who embed an agent runtime in their own services use it, keeping their own interface, storage, and policy. Success is that every port has a dummy and a conformance suite, that recorded conversations replay deterministically through the loop, that a killed turn is resumed with each completed step recorded once, that a UI lists, reads, and follows sessions within published budgets, that a developer is using the harness in a terminal within seconds of cloning, and that the first downstream platform runs on a tether release.

## Problem and Goals

### Problem

A Python application needs one coherent boundary for agent execution, policy, live identity, recovery, storage and UI access. LangGraph, Pydantic AI Harness, Pi Durable and managed services already provide substantial loop, persistence and streaming capabilities. Integrating one can use a single authoritative state owner, but must still preserve the application's exact authorization, recovery, session and dependency requirements. The [framework and AgentKit review](../00-discover/agent-harness-framework-review.md) evaluates that fit; ADR-003 retains the owned Python harness. The problem is the cost of implementing and verifying that combination, not the absence of sessions or UI APIs in other products.

### Goals

1. A Python developer clones the repository and uses the harness in a terminal within seconds, with no configuration and no credential.
2. A UI of any kind reads sessions through one Python API that stays fast as sessions and transcripts grow.
3. The loop is testable alone: every external system is behind a port with a dummy, and recorded conversations replay deterministically.
4. A turn that dies with its process is detected and resumed, and no completed step is lost or duplicated.
5. A platform that serves many users puts its policy at ports that extension code cannot widen.
6. Contributors can help without a legal hurdle, and the maintainers keep the code reviewable and consistent.
7. A developer runs the harness on a local or sovereign model, or any server of the OpenAI-compatible protocol, by configuration alone, with no provider SDK installed.

### Success Metrics

The numeric targets below were proposed on 2026-10-22 at the owner's request, as starting points. They are targets, not measurements. The first benchmark baseline validates or replaces each one, after which it becomes a committed budget that CI protects against regression.

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Ports with a dummy implementation that passes its conformance suite | 100% | The conformance suites run in CI |
| Recorded scenarios that replay through the loop and reproduce the saved audit record | 100% of shipped scenarios | The replay tests run in CI |
| A turn killed at any step resumes with each completed step recorded once | Pass for every prefix of every shipped scenario | The prefix test and a real-process kill test in CI |
| Synchronous entry points in the public API | 0 | A review check and an API listing test |
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
- No bundled coding agent, and no model provider SDK.
- No synchronous interface.
- No code written against a foundation-model provider's SDK (Anthropic's, OpenAI's, or any other provider's) unless an ADR shows the protocol cannot do the job (ADR-012).
- No plug-in or workflow manifest format: an application compiles its own declarations to the specification the loop reads.
- No retrieval, embedding, or vector-store toolkit.
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

Each requirement traces to the vision's value propositions. Exact signatures, entry shapes, event shapes, and error semantics are in the contracts under `docs/helix/02-design/contracts/`, not here. The first release (0.1) contains every P0 requirement.

### Must Have (P0)

1. The asynchronous loop and the model port, with the default OpenAI-compatible adapter and scripted, recording, and replay test doubles (FR-1 to FR-5, FR-38).
2. The tool-call interface with typed arguments, approval, typed results, and the replay marker (FR-6 to FR-10).
3. Sessions as append-only logs committed step by step, one turn per session, an in-memory store and a SQLite store (FR-11 to FR-13, FR-16).
4. Detection of an interrupted turn and its explicit resume (FR-14, FR-34).
5. The client API: list, read, follow, and commands (FR-17 to FR-20).
6. Spans and an audit record without content, and token usage (FR-22, FR-23, FR-25).
7. The test kit: a dummy for every port, conformance suites, and cassette record and replay (FR-28).
8. The command-line client, the terminal UI, and the zero-configuration demo (FR-29, FR-30, FR-33).
9. Dual licensing, a minimal-dependency core, and the contribution policy (FR-31, FR-32, FR-36).

### Should Have (P1)

1. Measured performance budgets for the client API, protected in CI (FR-21).
2. The OpenTelemetry adapter (FR-24).
3. Governance ports: policy, scope, result guards, and narrow-only providers (FR-26, FR-27).
4. Opt-in automatic resume for applications that supply an identity (FR-37).
5. Context compaction for long sessions (FR-35).

### Nice to Have (P2)

1. Fork from any entry (FR-15).
2. An MCP (Model Context Protocol) client adapter for tools.
3. Parallel execution of independent tool calls in one reply.
4. Subagents as sessions owned by a tool call.
5. Postgres and other store adapters, and server adapters (HTTP, WebSocket) over the client API.
6. An optional Pydantic AI adapter for providers outside the OpenAI-compatible protocol.

## Functional Requirements

### Subsystem: Agent Loop and Model Port

- **FR-1** — A turn takes one user message and runs model and tool steps until the model answers, a step limit is reached, a gate stops it, an approval is pending, or the model is unavailable, then ends with exactly one stop reason.
- **FR-2** — The loop calls models only through a provider-neutral model port. A default adapter for the OpenAI-compatible protocol, a scripted model, a recording model, and a replay model implement it.
- **FR-3** — Reply text streams to the caller as the model produces it, and an outage after some text discards that text from the transcript.
- **FR-4** — Every model call carries the calling actor's own credential and the session's tags to the provider, and concurrent calls for different actors never share a credential.
- **FR-5** — A model outage ends the turn with a short reason and the provider's request id, and never exposes request or response content.
- **FR-38** — A developer points the harness at any server that speaks the OpenAI-compatible chat-completions protocol, including local and self-hosted servers, by configuration alone, with no foundation-model provider SDK installed or imported; server-specific request parameters (sampling, caching, grammar) pass through unchanged.

### Subsystem: Tools

- **FR-6** — A tool is a plain `def` or `async def` function with typed arguments. Its schema is derived from the types, arguments are validated before the call, and a plain function never blocks the event loop.
- **FR-7** — Tools are registered by name, uniqueness is enforced, and a client can list the tools a session offers.
- **FR-8** — A tool can require approval before it runs. The turn ends with a pending confirmation that a later call answers, and the answer resumes the turn.
- **FR-9** — A tool call ends in a typed result (ok, error, denied, withheld, or interrupted) that the model reads and the record names; a denied or invalid call never records its argument values.
- **FR-10** — A tool declares whether it is safe to rerun after an interrupted turn, and the default is that it is not.

### Subsystem: Sessions

- **FR-11** — A session is an append-only log of typed entries, and each step's entries are committed durably before the next step starts.
- **FR-12** — A session can be created, closed, titled, tagged, and given settings, and each of these is recorded in the log.
- **FR-13** — A session runs one turn at a time. Different sessions run concurrently.
- **FR-15** — A new session can be forked from any entry of an existing one.
- **FR-16** — Sessions are stored through a port. An in-memory store and a SQLite store ship, and the SQLite store works across processes that share its file.

### Subsystem: Crash Recovery and Resume

- **FR-14** — A turn whose process died is detected from its expired lease and closed in the log as interrupted, and its session is listed as interrupted.
- **FR-34** — A caller with a live identity can resume an interrupted turn. The resume continues from the last committed step: a tool call without a recorded result is rerun if the tool is declared safe to rerun and otherwise recorded as interrupted, then the model is called again; each committed step appears once; every gate applies again; and a chain of consecutive interruptions is bounded.
- **FR-37** — An application can opt in to automatic resume by supplying a function that returns an identity for a session, or declines it.

### Subsystem: Client API

- **FR-17** — A client lists sessions for an owner with filters and ordering, in pages, from summaries the store keeps current, without reading transcripts.
- **FR-18** — A client reads one session's view and pages through its entries in either direction.
- **FR-19** — A client follows changes (session updates, appended entries, reply text, pending confirmations) from a position it can resume, receives a resync signal when its position is too old, and cannot slow the loop.
- **FR-20** — A client creates a session, sends a message, answers a confirmation, resumes an interrupted turn, cancels a turn, closes a session, and changes its settings, through the same API.
- **FR-21** — The API's latency and memory stay within budgets that benchmarks measure and protect against regression.

### Subsystem: Telemetry

- **FR-22** — The loop emits spans for each turn, model call, and tool call, carrying trace context across awaits.
- **FR-23** — The loop emits one audit entry per step and one per gate decision, carrying identifiers, sizes, and reasons.
- **FR-24** — Tracing is a port with a no-op default and an OpenTelemetry (OTel) adapter as an optional extra.
- **FR-25** — Token usage is recorded per model call and totaled per session in the summaries.

### Subsystem: Governance

- **FR-26** — A policy port decides every model call, tool call, data read, and resume, defaults to allow-all, and fails closed when it errors.
- **FR-27** — Scope keys, result guards, and providers extend the loop. A provider can refuse, hide, or redact and can never widen a decision, and a provider that errors is treated as a refusal.

### Subsystem: Test Kit

- **FR-28** — Every port has a dummy implementation and a conformance suite, a model conversation can be recorded on fabricated prompts and replayed, and a test can drive the loop with scripted approvals.

### Subsystem: Reference Clients

- **FR-29** — A command-line client lists sessions, shows a session, sends a message, follows a session, answers a confirmation, resumes an interrupted turn, and replays a cassette.
- **FR-30** — A terminal UI shows the session list and a live transcript and handles approvals and resume.
- **FR-33** — After cloning the repository, one command starts the terminal UI against a built-in scripted demo model and a local store, with no configuration, no network access, and no credential, and the demo exercises streaming, a tool call, and an approval.

### Subsystem: Context Management

- **FR-35** — A session's context can be bounded for the model by compaction that is recorded in the log, never alters earlier entries, and can be supplied by an application.

### Subsystem: Packaging and Licensing

- **FR-31** — The core package depends only on `pydantic` and `anyio`; the model adapter, the telemetry adapter, and the clients are optional extras.
- **FR-32** — The library is dual licensed MIT OR Apache-2.0, copyright FocusedDiversity LLC, with both license texts in the package and a history that starts in this repository.
- **FR-36** — The repository states how to contribute: contributions are welcome directly with no contributor license agreement, are accepted as prompts that the maintainers implement, and are licensed as the project is.

### Feature Map

Each subsystem becomes one feature specification: FEAT-001 Agent Loop and Model Port, FEAT-002 Tools, FEAT-003 Sessions, FEAT-004 Crash Recovery and Resume, FEAT-005 Client API, FEAT-006 Telemetry, FEAT-007 Governance, FEAT-008 Test Kit, FEAT-009 Reference Clients, FEAT-010 Context Management, FEAT-011 Packaging and Licensing. Stories are in `docs/helix/01-frame/user-stories/`.

## Non-Functional Requirements

- **NFR-1** — The public API is asynchronous throughout (ADR-001).
- **NFR-2** — The core reads no environment variable; a settings object is built at the composition root.
- **NFR-3** — Every gate fails closed, and a hook or adapter failure is a refusal, never a pass.
- **NFR-4** — Telemetry and audit entries contain no message or tool content by default, and no credential ever appears in a log, a cassette, a span, or a stored entry (ADR-011).
- **NFR-5** — The package is typed, passes `pyright` and `ruff`, and supports Python 3.11 and later.
- **NFR-6** — The client API holds bounded memory per subscriber and never blocks the loop on a slow consumer.
- **NFR-8** — No module imports a foundation-model provider's SDK (ADR-012), and the default install's dependency tree contains none; a boundary check enforces a banned list.
- **NFR-7** — Performance targets (proposed, validated by the first baseline). The reference workload is one owner with 10,000 sessions of about 200 entries each, one session of 100,000 entries, entries of about 1 KiB, one process with one event loop, on a laptop-class machine with a local solid-state disk. All latencies are 99th percentile.

| Operation | In-memory store | SQLite store |
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
| FR-14, FR-34 | A process killed mid-turn | A turn with a safe tool and an unsafe tool, killed after the assistant message with both calls and before either result | Recovery closes the turn as interrupted; resume reruns the safe tool, records the unsafe call as interrupted, calls the model again, and ends `final`; each committed entry appears once |
| FR-17, FR-18 | Listing a large store | Ten thousand sessions, a page size, a cursor | Pages arrive in order without reading transcripts; reading a page of entries returns the requested range |
| FR-19 | A follower resumes | A client disconnects, then follows from its last position | It receives only the changes after that position; a position older than retention yields a resync signal |
| FR-20 | Cancelling a running turn | A cancel during a model stream | The stream stops, the committed steps remain, and the log ends with a cancelled turn entry |
| FR-22, FR-23, NFR-4 | A turn with a recognizable prompt and credential | A fabricated prompt containing a marker string and a credential containing a second marker | Neither marker appears in any span attribute, audit entry, log entry, or cassette |
| FR-28 | Replay | A recorded cassette | The loop reproduces the saved audit record; an altered request fails naming the differing field |
| FR-29 | Command-line client | `list`, `send`, `follow`, `resume` against a store with a scripted model | The commands show the session, stream the reply, resume an interrupted turn, and exit cleanly |
| FR-33 | Clone and try | A fresh clone on a machine with a warm package cache, then `uv run tether` | The terminal UI paints with a demo session within 10 s of the command; a message streams a reply, runs a tool, and asks for approval |
| FR-31, FR-32, FR-36 | Install and contribute | A clean environment with only the core package; the repository's contribution page | The core imports and passes its test suite; both license texts ship; the page states the no-CLA, prompt-based policy |

## Technical Context

This section records decisions; the rationale is in the ADRs.

- **Language/Runtime**: Python 3.11 and later (the floor is an assumption: the lowest runtime of the first downstream consumer). `uv` for environments and builds (concern `python-uv`).
- **Key Libraries**: `pydantic` v2; `anyio`; `httpx` for the default OpenAI-compatible model adapter (ADR-002); no foundation-model provider SDK (ADR-012); Pydantic AI only in an optional adapter; Textual for the terminal UI as an extra (ADR-008); the standard library `sqlite3` for the SQLite store (ADR-009); OpenTelemetry as an extra.
- **Data/Storage**: an in-memory store and a SQLite store in WAL mode with full synchronous commits; other stores through the port.
- **APIs**: the model port, the engine ports, and the client API (contracts `CONTRACT-001` to `CONTRACT-003`).
- **Platform Targets**: Linux and macOS.
- **Hosting and CI**: GitHub (`DocumentDrivenDX/tether`) and GitHub Actions (an assumption to confirm).

## Constraints, Assumptions, Dependencies

### Constraints

- **Technical**: asynchronous only; no environment reads in the core; ports with dummies; telemetry without content; no stored credential.
- **Business**: dual MIT OR Apache-2.0, copyright FocusedDiversity LLC, clean history (ADR-006); contributions accepted as prompts with no CLA (ADR-007).
- **Legal/Compliance**: no private or customer data in the repository, the fixtures, or the recorded conversations.

### Assumptions

- The first downstream platform will adopt a tether release once it exists (the motivating consumer).
- The OpenAI-compatible protocol stays the common ground of the local and self-hosted servers users run; the adapter's conformance matrix finds each server's quirks (SPIKE-002).
- The proposed performance targets are achievable on the reference workload; the first baseline will show where they are not.

### Dependencies

- `pydantic`, `anyio`, `httpx`, Textual, and the OTel libraries for the extras; Pydantic AI only for its optional adapter.
- Operator decisions listed under Open Questions.

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| The proposed performance targets are wrong | Medium | Medium | The first benchmark baseline replaces each with a measured budget; CI protects the budget afterward |
| Per-step commits cost too much latency on SQLite | Medium | Medium | Batch a step's entries into one commit; measure in the benchmarks before 0.1; offer `synchronous=NORMAL` as a documented setting |
| The ports or the provider surface prove too narrow or too wide | Medium | High | Port the first downstream platform's scenarios early; amend the contracts before release |
| Resume re-executes a side effect | Medium | High | Tools default to unsafe and are recorded as interrupted, never rerun, unless declared safe; every gate applies again; resume is explicit by default (ADR-010) |
| Server quirks in the OpenAI-compatible protocol break the default adapter | High | Medium | A conformance matrix from cassettes of local and self-hosted servers on fabricated prompts, live on demand; a quirk table in the adapter; server-specific parameters pass through; the optional Pydantic AI adapter as a fallback |
| Asynchronous code is blocked by a plain function | Medium | High | Offload plain functions to worker threads; lint rules for blocking calls in `async def` |
| The unverified competitor cells hide a product that already does this | Medium | High | Validate the remaining cells in `frame` before building past the client API |
| The demo does not feel instant, defeating the first-impression goal | Medium | Medium | The quickstart test is timed in CI; the demo is a P0 requirement with its own budget |
| Prompt-only contributions slow acceptance | Medium | Low | The contribution page sets expectations; maintainers answer prompts in the issue |

## Open Questions

- [ ] Is the default `resume="report"` (explicit resume, with automatic resume as an opt-in that needs an identity) right? It follows from credentials never being stored (ADR-010, ADR-011). Blocks FEAT-004. Ask Erik LaBianca.
- [ ] Context compaction is proposed as P1 because two surveyed products ship it; should it move to P0 or P2? Blocks FEAT-010 sequencing. Ask Erik LaBianca.
- [ ] Whether GitHub Actions is the CI service. Blocks slice 0. Ask Erik LaBianca.
- [ ] Whether 3.11 is the right Python floor. Blocks the packaging metadata. Ask Erik LaBianca, from the first downstream consumer's runtime.

## Success Criteria

- Every P0 acceptance sketch passes in CI.
- A developer completes the quickstart with the terminal UI against the demo model, then a real provider, then the SQLite store, changing only configuration.
- A turn killed at any step resumes with each completed step recorded once.
- The first downstream platform replaces its in-house loop with a tether release and keeps its session, policy, and audit behavior.
