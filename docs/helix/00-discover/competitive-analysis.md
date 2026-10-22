---
ddx:
  id: competitive-analysis
  type: competitive-analysis
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informs
---

# Competitive Analysis

## Agent harness design review — 2026-10-22

The finalized implementation path is recorded in [ADR-003](../02-design/adr/ADR-003-tether-is-a-library-of-ports-and-providers-and-builds-its-own-loop.md): retain Python and the owned harness, keep the existing application server on Databricks Apps, and do not introduce AgentKit or a compatibility wrapper. The linked review includes the Python/TypeScript trade-offs and the versioned AgentKit contract comparison.

The [agent harness framework and design review](agent-harness-framework-review.md)
compares LangGraph, OpenAI Agents SDK, Claude Agent SDK, Pydantic AI Harness,
and additional alternatives against Tether's contracts, with primary web sources,
a framework-free counterfactual, design findings, and proposed verification.
It recommends retaining the owned harness while resolving uncertain tool effects,
synchronous-tool cancellation, and approval/resume compatibility.

Use that review to qualify the survey below: current Pydantic AI documentation
includes step persistence and a composable Harness, LangGraph documents checkpoint
enumeration and streaming, and session continuation is distinct from safe recovery
of an arbitrary external effect. Framework adoption can use one authoritative
state store with custom integration; duplicate state ownership is a design risk,
not an unavoidable property. The earlier comparison remains a dated survey,
not a certification that unverified capabilities are absent.

> Scope and confidence. The first survey was made on 2026-10-21 from vendor documentation, a README read through a summarizer, and secondary articles. On 2026-10-22 the session, durability, and persistence claims were checked against each product's own documentation. Nothing was run. Cells that could not be checked say `Unverified`; a cell that was searched for and not found says `None found`, which is not a claim that the capability is absent.

Terms: **MCP** is the Model Context Protocol, a standard for connecting tools to models. **HITL** is human in the loop (a person approves a step). **OTel** is OpenTelemetry, the open telemetry standard. **Harness** here means the runtime around a model: the loop, tool execution, session state, and the controls on them. **Compaction** is shortening a long conversation so it fits a model's context.

## Market Landscape

| Attribute | Assessment |
|-----------|------------|
| Market Maturity | Growing. Many frameworks, frequent releases, and no settled leader (secondary sources, low confidence). |
| Growth Rate | Not measured; no sourced figure. |
| Key Trends | Provider-native SDKs beside independent frameworks; durable execution layered onto agent frameworks (Pydantic AI integrates external engines); harnesses offered as small libraries rather than large frameworks; session history and compaction becoming standard features. |
| Entry Barriers | Low. Open-source libraries, MIT or Apache licenses, small code bases (Pi Durable is about 15,000 lines excluding tests, per its announcement). |
| Buyer Power | High. A library has low switching cost for a developer, and the model layer is replaceable. |

## Competitive Forces

| Force | Pressure | Evidence / Confidence | Implication |
|-------|----------|-----------------------|-------------|
| Direct Rivalry | High | Pi Durable, LangGraph, and the OpenAI and Claude agent SDKs all keep sessions or checkpoints (documentation, below). Medium to high. | Differentiate on language, the session boundary for UIs, and testability, not on having sessions. |
| Substitutes | High | A bespoke loop on a provider SDK, or a framework plus an external durable-execution engine, is what teams do today (assumption; to validate with users). | The library must beat "write it ourselves" on time to a durable, observable agent. |
| New Entrants | High | Pi 1.0 (2026-10-01) and Pi Durable are new; the Pi project was acquired in April 2026 (secondary sources). Medium. | Expect more small harness libraries; stay small and well-bounded. |
| Buyer Power | High | Libraries are replaced at low cost. | Keep ports narrow so adopting and leaving are both cheap. |

## Competitor Profiles

| Competitor | Type | Positioning | Target Segment | Strengths | Weaknesses | Source / Confidence |
|------------|------|-------------|----------------|-----------|------------|---------------------|
| Pi (coding agent) | Direct, adjacent | A minimal terminal coding agent with a four-tool core and a typed TypeScript extension system; sessions are trees (all branches in one file) with `/tree` navigation, `/export`, and `/share`; run modes include a JSON protocol over standard input and output (RPC) for non-Node integrations | Individual developers on their own machine | Small core; large extension catalog; strong community (about 117,000 stars) | TypeScript; built for one trusted user; no policy gates in the core; whether another process can list or follow sessions through RPC is not documented on the pages read | [Pi site](https://pi.dev), [repository](https://github.com/earendil-works/pi), [overview](https://agentic-ai.readthedocs.io/en/latest/AgentHarness/pi-dev/). Medium |
| Pi Durable | Direct | An experimental TypeScript framework for crash-tolerant agents: a harness owns storage and runs many conversations; every model call, tool call, and compaction is a checkpointed task; extensions bundle prompt sections, tools, hooks, and tasks | Developers building durable agents that run anywhere (Node, Bun, edge) | Per-step checkpoints; tools marked safe to replay; exactly-once submission by request id; storage conformance suite; several clients can attach to one conversation; MIT license | TypeScript; API "changes without notice"; no tenant model and no Postgres store; one process owns a storage with no cross-process locking; a throwing non-tool hook is skipped; no server or RPC mode documented | [Announcement](https://earendil.com/posts/pi-durable/), [README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md). Medium (README read through a summarizer; the hook-failure behavior is unconfirmed in source) |
| Claude Agent SDK | Direct | Anthropic's agent SDK for coding and Anthropic-first agents, with deep MCP support. Its documented session functions are `list_sessions`, `get_session_messages`, `get_session_info`, `rename_session`, `tag_session`, and `fork_session`; a session resumes by id; a `session_store` option mirrors transcripts to external storage | Teams building on Anthropic models | The only surveyed product that documents session list, read, rename, tag, and fork as functions | The session functions are synchronous with offset paging, return no total or cursor, and work per project directory; no live follow of a session by another consumer is documented | [Python SDK reference](https://code.claude.com/docs/en/agent-sdk/python). High for the listed facts (the page was read in part) |
| LangGraph | Direct | Stateful, graph-structured orchestration of agents; a checkpointer saves state at each step, keyed by thread id, for conversation continuity, HITL, time travel, and fault tolerance | Teams building complex multi-step or multi-agent workflows | Checkpointing, branching, HITL, per-thread state history (`get_state_history`) | Owns the control flow and, with a checkpointer, the paused turn's state; no way to list all threads was found in the open-source documentation read; larger dependency tree | [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers). Medium (the checkpointer API methods were not read) |
| OpenAI Agents SDK | Direct | A provider-native Python SDK with handoffs between agents and a session memory protocol (`get_items`, `add_items`, `pop_item`, `clear_session`) over many backends (SQLite, Redis, SQLAlchemy, MongoDB, Dapr, encrypted, OpenAI-hosted) and a compaction wrapper | Teams on OpenAI-centric stacks | Official SDK; many session backends; compaction; an asynchronous SQLite session | No way to list sessions or subscribe to changes is documented; crash recovery is documented only for resuming a run paused for approval; a session id selects history but does not authorize anyone | [Sessions](https://openai.github.io/openai-agents-python/sessions/). High for the listed facts |
| Pydantic AI | Adjacent (and a component) | A type-safe, model-agnostic Python agent framework in the style of FastAPI; integrates Temporal, DBOS, and Prefect (and Restate, per a newer page) for durable execution; message history is saved by the application | Python developers who want typed agents | Provider-neutral message and tool model; native async; test models and a way to block real model requests; durable execution through external engines | Its agent loop owns control flow; no record-and-replay facility and no list of past runs are documented; the engines rerun an in-flight call after a crash, so idempotency is the tool's job; infrastructure varies by engine; current Harness and Step Persistence also cover local state continuity | [Direct API](https://pydantic.dev/docs/ai/core-concepts/direct/), [OpenAI models](https://pydantic.dev/docs/ai/models/openai/), [Testing](https://pydantic.dev/docs/ai/guides/testing/), [Durable execution](https://pydantic.dev/docs/ai/harness/durable-execution/). High for the listed facts |

**Indirect Competitors**: Microsoft Agent Framework, Strands Agents, and Google ADK (Agent Development Kit) are agent frameworks with human-approval and workflow features. One vendor-written source argues that they save conversation state but leave failure detection and recovery to the application ([Diagrid](https://www.diagrid.io/blog/still-not-durable-how-microsoft-agent-framework-and-strands-agents-repeat-the-same-mistake), [Splunk overview](https://www.splunk.com/en_us/blog/learn/agent-frameworks.html)); treat it as one view. Threat: medium. A bespoke loop on a provider SDK is the substitute most teams use today (assumption to validate).

## Feature Comparison

| Feature | tether | Pi Durable | Claude Agent SDK | LangGraph | OpenAI Agents SDK | Pydantic AI |
|---------|--------|-----------|------------------|-----------|-------------------|-------------|
| Python-native | Planned | None (TypeScript) | Full | Full | Full | Full |
| Asynchronous throughout | Planned | Full (per README) | Partial (session reads are synchronous) | Unverified | Partial (an async SQLite session exists) | Full |
| Session list and read as an API | Planned | Partial (clients attach to a conversation; no list API found) | Full (synchronous, offset paging, per project) | Partial (per-thread history; no thread list found) | None found | None found (the application stores messages) |
| Cursor paging from materialized summaries, scoped by owner | Planned | Unverified | None found (offset paging, per project) | Unverified | None found | None found |
| Live follow of a session by another consumer | Planned | Partial (several clients can attach and steer) | None found | Unverified | None found | None found |
| In-flight turn survives a process crash | Planned | Full (checkpointed tasks) | Unverified | Partial (checkpoint-based) | Partial (resume after an approval pause only) | Full through an external engine (Temporal, DBOS, Prefect) |
| A tool declares whether it is safe to rerun after a crash | Planned | Full (`replay: "safe"`) | Unverified | Unverified | Unverified | Partial (engines rerun the in-flight call; idempotency is the tool's job) |
| Policy gates that fail closed around model and tool calls | Planned | Partial (a blocking tool hook; other hooks skip on error) | Unverified | Unverified | Unverified | Unverified |
| Record and replay of a model conversation as a test double | Planned | Unverified | Unverified | Unverified | Unverified | None found (test models only) |
| Depends on no foundation-model provider's SDK | Planned (ADR-012) | Unverified | None (Anthropic's own SDK) | Unverified | None (OpenAI's own SDK) | Partial (its OpenAI-compatible model needs the `openai` package) |
| Context compaction | Planned (P1) | Full (a compaction task) | Unverified | Unverified | Full (a compaction wrapper) | Unverified |

**Legend**: Full | Partial | Planned | None | None found (searched, not found) | Unverified (not checked). Cells for other products are not claims about their full capability.

## Differentiation Strategy

These are assessments from the survey, not measurements.

| Differentiator | Why It Matters | Defensibility |
|----------------|----------------|---------------|
| Python and asynchronous throughout, as an embeddable library | Python teams can embed it in their own server and UI without owning a second runtime | M: the language gap to Pi Durable is real, but a TypeScript project could be ported |
| A session boundary built for interfaces: owner-scoped lists from materialized summaries with cursor paging, position-addressed entry pages, and a live, resumable change feed | The Claude Agent SDK shows the demand (list, read, rename, tag, fork), but its functions are synchronous, offset-paged, and scan a project directory; other products now document streaming and observer APIs; Tether must prove the exact owner-scoped paging/feed combination | M: depends on the quality of the API, which is the thing to build well |
| Durable turns without an external engine: per-step commits, leases, and a deterministic resume planner over the session log | Current Pydantic AI includes step persistence as well as durable-engine integrations; Tether selects recovery from its own log to preserve its embedded model and identity rules | M: Pi Durable already does this in TypeScript, so the point is Python parity with a UI boundary |
| Fail-closed governance ports (policy, scope, result guards) and narrow-only providers | A platform that serves many users needs gates the extension code cannot widen | M |
| Deterministic replay of recorded model conversations, and a dummy for every port | The loop can be tested alone and regressions caught before they ship | H for the combination; individual pieces are copyable |
| Provider-neutral by construction: no foundation-model SDK in the dependency tree, and local and sovereign servers first-class | The harness runs wherever an OpenAI-compatible server runs, including air-gapped and sovereign deployments, with no provider's release cycle or terms in the tree | M: the claim is easy to make and holds only while the boundary check holds |
| A zero-configuration demo: clone, run one command, and use the TUI against a built-in scripted model | A developer judges a harness in seconds; no surveyed product advertises this | L: easy to copy, valuable to have |

**Positioning**: For Python developers building agent applications and multi-user platforms who need a durable, observable runtime they can embed and drive from their own interface, tether is an asynchronous harness library. Unlike Pi Durable (TypeScript, one process per storage) and the Python agent frameworks that own the control flow and the paused turn's state, tether keeps the session log as data a UI lists, reads, and follows, and keeps policy and storage at ports the application controls.

## Strategic Implications

- **Attack**: The session boundary for UIs (async, cursor-paged, live), recovery from the session log without an external engine, deterministic replay testing, and fail-closed governance ports in Python.
- **Defend**: Small size and narrow ports, so that adopting tether and leaving it are both cheap.
- **Avoid**: Multi-agent orchestration graphs (LangGraph's ground), a bundled coding agent (Pi's and the Claude Agent SDK's), and competing as a model provider SDK. Own a small client of the open OpenAI-compatible protocol instead of using any provider's SDK (ADR-012, SPIKE-002); Pydantic AI stays an optional adapter.
- **Match**: Context compaction, which two surveyed products ship.

## Open Questions

| Question | Owner |
|----------|-------|
| The remaining unverified cells, especially LangGraph's thread listing and streaming, Pi's RPC session access, and the Claude Agent SDK's crash behavior; which of them change a requirement | Erik LaBianca, in `frame` |
| Whether Pi Durable's non-tool hooks fail open as the README summary suggests (confirm in its source) | Erik LaBianca |
