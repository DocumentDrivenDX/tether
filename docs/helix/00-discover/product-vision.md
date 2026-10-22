---
ddx:
  id: product-vision
  type: product-vision
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
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
tether is an asynchronous harness library.
Unlike Pi Durable (TypeScript, one process per storage) and the Python agent frameworks that own the control flow and the paused turn's state (LangGraph, the OpenAI Agents SDK), tether treats the session log as data a UI reads and keeps policy and storage at ports the application controls.

## Vision

A developer embeds tether in a Python service, writes tools as plain functions, and points a terminal UI, a web UI, or a notebook at one API. Sessions list instantly however many exist. A conversation is followed live and resumed after a disconnect without a re-read of the whole transcript. A turn that dies with its process resumes or reports itself interrupted, never silently half-recorded. Traces show what happened without recording what was said. A recorded model conversation replays through the loop in a test and reproduces the same record. The platform team that serves many users puts its own policy at the ports and knows the extension code cannot widen it.

**North Star**: A developer goes from an empty project to a durable, observable agent with a live interface in one sitting, and replaces any part (model, store, policy, interface) without touching the others.

## User Experience

Maya builds an internal assistant for her company. She installs tether and writes two tools as ordinary functions. She runs the reference terminal client against a scripted model and sees an empty session list. She creates a session and sends a message. The reply streams in as the model produces it; a tool that she marked "ask first" stops the turn, the client shows the pending call, and she approves it. She kills the process in the middle of a second turn. On restart the client lists the session as interrupted; the library resumes the turn from its last committed step, and the transcript shows each completed step exactly once.

She then replaces the scripted model with a real provider through one line of configuration, replaces the in-memory store with the SQLite store, and writes a small web server whose only calls into tether are the client API: list sessions, read a page of a transcript, follow the change feed from the last position the browser saw. In her tests she replays a recorded conversation through the loop and compares its audit record to a saved one.

## Target Market

| Attribute | Description |
|-----------|-------------|
| Who | Python developers and platform teams building agent applications, including multi-user platforms with their own interface, storage, and policy |
| Pain | Existing runtimes either own the control flow and the paused state (hard to embed beside an application's own sessions), are not Python, or target one user at a terminal; none surveyed documents a session list, read, and follow boundary for a UI; there is little way to test a loop against a recorded conversation |
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

## Non-Goals

- Not a user interface. The command-line client and terminal UI are reference clients for development and testing.
- Not a server. Tether has no HTTP, WebSocket, or server-sent-events surface in the library; a server is an adapter written over the client API.
- Not an agent orchestration framework: no graphs, no multi-agent handoffs, in the first releases.
- Not a coding agent and not a replacement for one.
- Not a model provider SDK. Model calls go through Pydantic AI behind a port.
- Not synchronous. There is no synchronous interface.
- Not a workflow, prompt, or plug-in manifest system. An application defines its own declarations and compiles them to what the loop reads.
- Not a retrieval or vector-store toolkit.

## Success Definition

| Metric | Target |
|--------|--------|
| Every port has a dummy implementation that passes its conformance suite | 100% of ports, checked by the suite run in CI |
| A recorded conversation replays through the loop and reproduces the saved audit record | Passes in CI for each shipped scenario |
| A turn killed between steps resumes or reports interrupted, and each completed step appears once | Passes in CI (a kill-between-steps test) |
| Session list, transcript page, and change-feed latency and memory | Budgets set by Erik LaBianca after the first benchmark baseline exists; measured by the benchmark suite in CI, with no regression |
| Time from an empty project to a running agent with the reference client | Measured by the quickstart test in CI; the target is set by Erik LaBianca after it is first measured |
| The first downstream platform runs on a tether release in place of its in-house loop | Binary, owned by Erik LaBianca |

## Why Now

Harness libraries have just become a distinct category. Pi 1.0 shipped on 2026-10-01, and Pi Durable shows demand for a small, durable, embeddable harness, but only in TypeScript. Python offers a provider-neutral, asynchronous model layer in Pydantic AI, which the spike (SPIKE-001) shows can carry per-user identity, per-call headers, streaming, and concurrent calls through its direct API. The Python frameworks surveyed own the control flow and the paused state, which is the part many applications already own. Waiting means each team keeps building its own loop and its own session store.

## Assumptions to Validate in Frame

| Assumption | Why it matters | Check |
|------------|----------------|-------|
| Enough Python teams want to embed a harness and own their UI and storage | The market claim | Interviews or a downstream adoption signal |
| The session boundary (list, read, follow) is the part existing runtimes lack | The main differentiator | Verify the unverified cells of the feature comparison |
| Dual MIT and Apache-2.0 licensing, with Synaptiq as copyright holder, suits adopters | Adoption | Operator decision recorded in ADR-006; legal entity name to confirm |
| Python 3.11 is the right floor | A library's floor is the lowest Python its consumers run | The first downstream consumer's runtime |

## Next Action

`frame`: turn this vision into feature specifications and user stories (the requirements in the [PRD](../01-frame/prd.md) are a first draft), and validate the assumptions above.
