---
ddx:
  id: product-vision
  type: product-vision
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: competitive-analysis
      kind: informed_by
---

# Product Vision

> Handling: this repository is public-ready. Examples are fabricated; no credential, private data, or reference to a private repository, client, or organization appears here.

## Mission Statement

Tether gives Python developers the runtime underneath an agent application: an asynchronous agent loop, a typed tool interface, durable sessions, and telemetry, with a fast Python API through which any interface lists sessions and follows live conversations.

## Positioning

For Python developers building agent applications and multi-user platforms who need a durable, observable agent runtime they can embed and drive from their own interface,
tether is an asynchronous, provider-neutral harness library.
Tether combines an application-controlled execution and recovery model with owner-scoped session reads and changes. Frameworks such as LangGraph, Pydantic AI Harness and Pi Durable already supply substantial harness capabilities; the choice is the fit of this combination to an embedded Python application, as assessed in the [framework review](agent-harness-framework-review.md).

## Vision

A developer embeds tether in a Python service, writes tools as plain functions, and points a terminal UI, a web UI, or a notebook at one API. Sessions list instantly however many exist. A conversation is followed live and resumed after a disconnect without a re-read of the whole transcript. On a qualified durable profile, an interrupted turn resumes from accepted journal facts with explicit unknown effects and unavailable history; a volatile profile makes no restart guarantee. Traces show what happened without recording what was said. A recorded model conversation replays through the loop in a test and reproduces the same record. The platform team that serves many users puts its own policy at the ports and knows the extension code cannot widen it.

**North Star**: A developer goes from an empty project to a durable, observable agent with a live interface in one sitting, and replaces any part (model, store, policy, interface) without touching the others.

## User Experience

Maya builds an internal assistant for her company. She hears about tether, clones the repository, and runs one command. Within seconds a terminal UI opens on a demo session: she sends a message, watches the reply stream in, sees a tool run, and approves a call the demo marked ask-first. No configuration, no network, no key. Convinced, she writes two tools of her own as ordinary functions and runs the same terminal client against a scripted model with an empty session list. She creates a session and sends a message. The reply streams in as the model produces it; a tool that she marked "ask first" stops the turn, the client shows the pending call, and she approves it. She kills the process in the middle of a second turn. On restart the client lists the session as interrupted; the library resumes the turn from its last committed step, and the transcript shows each completed step exactly once.

She then replaces the scripted model with a real provider through one line of configuration, uses the local JSONL history/journal pair for durable sessions, and writes a small web server whose only calls into tether are the client API: list sessions, read a page of a transcript, follow the change feed from the last position the browser saw. In her tests she replays a recorded conversation through the loop and compares its audit record to a saved one.

## Target Market

| Attribute | Description |
|-----------|-------------|
| Who | Python developers and platform teams building agent applications, including multi-user platforms with their own interface, storage, and policy |
| Pain | A team needs coherent execution, authorization, recovery and session/UI semantics within its Python application. Existing frameworks and managed services provide many pieces, but the team must evaluate their state model, identity rules, dependencies and portability together. |
| Current Solution | A bespoke loop on a provider SDK or on Pydantic AI; adopting LangGraph or the OpenAI Agents SDK; adapting Pi Durable's design in TypeScript |
| Why They Switch | They need durable sessions their own UI can read, tests that replay real conversations, and policy gates that extensions cannot widen, without taking on a framework that owns their state |

All rows are assumptions to validate in `frame`; the survey ([competitive analysis](competitive-analysis.md)) supports the pain and the gap in part, and one known consumer motivates the project.

## Key Value Propositions

| Value Proposition | Customer Benefit |
|-------------------|------------------|
| A session log committed step by step, read through a list, page, and follow API | An interface shows live and past conversations without owning the runtime, and a crash loses no recorded step |
| Asynchronous throughout, tools as plain functions | One concurrency model; a tool author needs no asynchronous code |
| A port and a dummy for every external system, with conformance suites | The loop is tested alone, and each adapter is held to the same behavior |
| Record and replay of model conversations | Regressions in the loop, the gates, and the audit record are caught by deterministic tests |
| Fail-closed policy, scope, and result-guard ports; providers that narrow and never widen | A platform serving many users controls what extensions can do |
| Telemetry without content | Traces and audit entries are safe to ship to a shared collector |
| Provider neutrality: replaceable local, self-hosted, sovereign or hosted backends; isolated optional SDK adapters | It runs wherever a model server runs, including air-gapped and sovereign deployments, with no required hosted-provider account; optional adapters disclose their dependency/version obligations |

## Non-Goals

- Not a user interface. The command-line client and terminal UI are reference clients for development and testing.
- Not a server. Tether has no HTTP, WebSocket, or server-sent-events surface in the library; a server is an adapter written over the client API.
- Not an agent orchestration framework: no graphs, no multi-agent handoffs, in the first releases.
- Not a coding agent and not a replacement for one.
- Not a model provider SDK, and not tied to any provider. Model calls speak the open OpenAI-compatible protocol, and provider SDKs stay out of base packaging/public core types while explicitly selected optional adapters may reuse them (ADR-012). SDK wrapping alone does not settle runtime selection.
- Not synchronous. There is no synchronous interface.
- Not a workflow, prompt, or plug-in manifest system. An application defines its own declarations and compiles them to what the loop reads.
- Not a retrieval or vector-store toolkit.

## Success Definition

| Metric | Target |
|--------|--------|
| Every port has a dummy implementation that passes its conformance suite | 100% of ports, checked by the suite run in CI |
| Foundation-model provider SDKs in the default install's dependency tree | 0, checked by a dependency-tree test in CI |
| A recorded conversation replays through the loop and reproduces the saved audit record | Passes in CI for each shipped scenario |
| A qualified durable profile preserves accepted steps and resumes with explicit blocked/unknown outcomes | Passes in CI (a prefix test over every shipped scenario and a real-process kill test) |
| Session list, transcript page, append, and change-feed latency and memory | The proposed budgets in the PRD (NFR-7), replaced by measured budgets after the first benchmark baseline; measured by the benchmark suite in CI, with no regression |
| Time from a fresh clone to a running terminal UI on the demo model, warm package cache | At most 10 s (proposed 2026-10-22; validated by the first measurement); measured by the quickstart test in CI |
| The first downstream platform runs on a tether release in place of its in-house loop | Binary, owned by Erik LaBianca |

## Why Now

Harness libraries have just become a distinct category. Pi 1.0 shipped on 2026-10-01, and Pi Durable shows demand for a small, durable, embeddable harness, but only in TypeScript. The OpenAI-compatible chat-completions protocol is now an open convention that local and self-hosted model servers implement, so a harness can be provider-neutral by speaking it directly, and a small client over `httpx` carries streaming, tool calls, per-call identity, and cancellation (SPIKE-002). Tether builds on Pydantic AI Agent for iteration and typed execution while preserving application-owned policy, authoritative journal/history and the Client API (ADR-003). Teams can reuse that foundation without maintaining another general-purpose loop.

## Assumptions to Validate in Frame

| Assumption | Why it matters | Check |
|------------|----------------|-------|
| Enough Python teams want to embed a harness and own their UI and storage | The market claim | Interviews or a downstream adoption signal |
| The complete owner-scoped session and recovery contract is useful enough to justify its maintenance | The product-fit claim; individual capabilities already exist elsewhere | Validate with the first consumer and a concrete replacement comparison |
| Dual MIT and Apache-2.0 licensing, with FocusedDiversity LLC as copyright holder, and contributions accepted as prompts without a CLA, suit adopters and contributors | Adoption | Operator decisions recorded in ADR-006 and ADR-007 |
| Teams with local or sovereign model requirements are a meaningful part of the market | The provider-neutrality differentiator; an operator design goal (ADR-012) rather than a measured demand | Interviews or a downstream adoption signal |
| Python 3.11 is the right floor | A library's floor is the lowest Python its consumers run | The first downstream consumer's runtime |

## Next Action

`frame`: turn this vision into feature specifications and user stories (the requirements in the [PRD](../01-frame/prd.md) are a first draft), and validate the assumptions above.
