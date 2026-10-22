---
ddx:
  id: competitive-analysis
  type: competitive-analysis
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informs
---

# Competitive Analysis

> Scope and confidence. This survey was made on 2026-10-21 from vendor documentation, a README read through a summarizer, and secondary articles. Nothing was run. Claims from secondary or vendor-written sources are marked low confidence, and cells that could not be checked say `Unverified`. The survey is the research step of genesis: it shapes the vision, and `frame` must validate the unverified cells before any requirement depends on them.

Terms: **MCP** is the Model Context Protocol, a standard for connecting tools to models. **HITL** is human in the loop (a person approves a step). **OTel** is OpenTelemetry, the open telemetry standard. **Harness** here means the runtime around a model: the loop, tool execution, session state, and the controls on them.

## Market Landscape

| Attribute | Assessment |
|-----------|------------|
| Market Maturity | Growing. Many frameworks, frequent releases, and no settled leader (secondary sources, low confidence). |
| Growth Rate | Not measured. Owner Erik LaBianca to supply a sourced figure or drop the row. |
| Key Trends | Provider-native SDKs beside independent frameworks; durable execution layered onto agent frameworks; harnesses offered as small libraries rather than large frameworks. |
| Entry Barriers | Low. Open-source libraries, MIT or Apache licenses, small code bases (Pi Durable is about 15,000 lines excluding tests, per its announcement). |
| Buyer Power | High. A library has low switching cost for a developer, and the model layer is replaceable. |

## Competitive Forces

| Force | Pressure | Evidence / Confidence | Implication |
|-------|----------|-----------------------|-------------|
| Direct Rivalry | High | Pi Durable and LangGraph both target durable agent runtimes (README; secondary sources). Medium. | Differentiate on language, the session-as-data boundary, and testability, not on loop features. |
| Substitutes | High | A bespoke loop on a provider SDK is what most teams build today (assumption; to validate in frame). | The library must beat "write it ourselves" on time to a durable, observable agent. |
| New Entrants | High | Pi 1.0 (2026-10-01) and Pi Durable are new; the Pi project was acquired in April 2026 (secondary sources). Medium. | Expect more small harness libraries; stay small and well-bounded. |
| Buyer Power | High | Libraries are replaced at low cost. | Keep ports narrow so adopting and leaving are both cheap. |

## Competitor Profiles

| Competitor | Type | Positioning | Target Segment | Strengths | Weaknesses | Source / Confidence |
|------------|------|-------------|----------------|-----------|------------|---------------------|
| Pi (coding agent) | Direct, adjacent | A minimal terminal coding agent with a four-tool core (read, write, edit, bash) and a typed TypeScript extension system; sessions branch and rewind; four run modes (interactive, print or JSON, RPC, SDK); MCP, subagents, and permission prompts are deliberately outside the core | Individual developers on their own machine | Small core; large extension catalog; strong community (about 117,000 stars) | TypeScript; built for one trusted user; no policy gates in the core | [Pi repository](https://github.com/earendil-works/pi), [Pi overview](https://agentic-ai.readthedocs.io/en/latest/AgentHarness/pi-dev/), [analysis](https://www.implicator.ai/pi-is-not-a-claude-code-rival-it-is-a-harness-rebellion/). Medium |
| Pi Durable | Direct | An experimental TypeScript framework for long-running, crash-tolerant agents: a harness owns storage and runs many conversations; every model call, tool call, and compaction is a checkpointed task; extensions bundle prompt sections, tools, hooks, and tasks | Developers building durable agents that run anywhere (Node, Bun, edge) | Per-step checkpoints; tools marked safe to replay; exactly-once submission by request id; storage conformance suite; several clients can attach to one conversation; MIT license | TypeScript; API "changes without notice"; no tenant model and no Postgres store; one process owns a storage with no cross-process locking; a throwing non-tool hook is skipped; no server or RPC mode documented | [Announcement](https://earendil.com/posts/pi-durable/), [README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md). Medium (README read through a summarizer; the hook-failure behavior is unconfirmed in source) |
| LangGraph | Direct | Stateful, graph-structured orchestration of agents with checkpointing and human review | Teams building complex multi-step or multi-agent workflows | Durable state, branching, HITL (per secondary sources) | Owns the control flow and, with a checkpointer, the paused turn's state, which collides with an application that already owns sessions; larger dependency tree | [Comparison](https://www.morphllm.com/ai-agent-framework), [comparison](https://uvik.net/python-ai-agent-frameworks/). Low |
| OpenAI Agents SDK | Direct | A provider-native Python SDK for agents with handoffs between agents; the default template for agents on one data platform's app runtime | Teams on OpenAI-centric stacks | Official SDK; simple handoff model; a documented path to one platform's model gateway | Positioned for OpenAI-centric stacks; session list and read for a UI not confirmed | [Platform docs](https://docs.databricks.com/aws/en/agents/custom-agents/author-agent), [comparison](https://www.morphllm.com/ai-agent-framework). Low |
| Claude Agent SDK | Direct | Anthropic's agent SDK, strongest for coding and Anthropic-first agents, with deep MCP support | Teams building on Anthropic models | Native MCP; coding tools | Provider-native; release details conflict across sources | [Comparison](https://www.morphllm.com/ai-agent-framework). Low |
| Pydantic AI | Adjacent (and a component) | A type-safe, model-agnostic Python agent framework in the style of FastAPI | Python developers who want typed agents | Provider-neutral message and tool model; native async; test models (`TestModel`, `FunctionModel`) and a way to block real model requests in tests | Its agent loop owns control flow; no record-and-replay facility is documented | [Direct API](https://pydantic.dev/docs/ai/core-concepts/direct/), [OpenAI models](https://pydantic.dev/docs/ai/models/openai/), [Testing](https://pydantic.dev/docs/ai/guides/testing/). High for the listed facts |

**Indirect Competitors**: Microsoft Agent Framework, Strands Agents, and Google ADK (Agent Development Kit) are agent frameworks with human-approval and workflow features. One vendor-written source argues that they save conversation state but leave failure detection and recovery to the application ([Diagrid](https://www.diagrid.io/blog/still-not-durable-how-microsoft-agent-framework-and-strands-agents-repeat-the-same-mistake), [Splunk overview](https://www.splunk.com/en_us/blog/learn/agent-frameworks.html)); treat it as one view. Threat: medium. A bespoke loop on a provider SDK is the substitute most teams use today (assumption to validate).

## Feature Comparison

| Feature | tether | Pi Durable | LangGraph | OpenAI Agents SDK | Pydantic AI |
|---------|--------|-----------|-----------|-------------------|-------------|
| Python-native | Planned | None (TypeScript) | Full | Full | Full |
| Asynchronous throughout | Planned | Full (per README) | Unverified | Unverified | Full |
| Session list and read exposed to a UI as an API | Planned | Partial (clients attach to a conversation; no session list API found) | Unverified | Unverified | None found |
| In-flight turn survives a process crash | Planned | Full (checkpointed tasks) | Partial (checkpoint-based, per secondary sources) | Unverified | None found |
| A tool declares whether it is safe to rerun after a crash | Planned | Full (`replay: "safe"`) | Unverified | Unverified | None found |
| Policy gates that fail closed around model and tool calls | Planned | Partial (a blocking tool hook; other hooks skip on error) | Unverified | Unverified | Unverified |
| Record and replay of a model conversation as a test double | Planned | Unverified | Unverified | Unverified | None found (test models only) |

**Legend**: Full | Partial | Planned | None | None found (searched, not found) | Unverified (not checked). Cells for other products are not claims about their full capability.

## Differentiation Strategy

These are assessments from the survey, not measurements.

| Differentiator | Why It Matters | Defensibility |
|----------------|----------------|---------------|
| Python and asynchronous throughout, as an embeddable library | Python teams can embed it in their own server and UI without owning a second runtime | M: the language gap to Pi Durable is real, but a TypeScript project could be ported |
| The session log is data a UI reads: list, page, follow, with a resumable change feed | The interface is the product for many teams, and none of the surveyed products documents this boundary | M: depends on the quality of the API, which is the thing to build well |
| Fail-closed governance ports (policy, scope, result guards) and narrow-only providers | A platform that serves many users needs gates the extension code cannot widen | M |
| Deterministic replay of recorded model conversations, and a dummy for every port | The loop can be tested alone and regressions caught before they ship | H for the combination; individual pieces are copyable |
| Per-step durable commits with crash resume | A turn that dies mid-run resumes or reports itself interrupted | L: Pi Durable already does this; the point is parity in Python |

**Positioning**: For Python developers building agent applications and multi-user platforms who need a durable, observable runtime they can embed and drive from their own interface, tether is an asynchronous harness library. Unlike Pi Durable (TypeScript, one process per storage) and the Python agent frameworks that own the control flow and the paused turn's state, tether keeps the session log as data a UI reads and keeps policy and storage at ports the application controls.

## Strategic Implications

- **Attack**: The session boundary for UIs, deterministic replay testing, and fail-closed governance ports in Python.
- **Defend**: Small size and narrow ports, so that adopting tether and leaving it are both cheap.
- **Avoid**: Multi-agent orchestration graphs (LangGraph's ground), a bundled coding agent (Pi's), and competing as a model provider SDK. Use Pydantic AI for the model layer instead of building one (SPIKE-001).

## Open Questions

| Question | Owner |
|----------|-------|
| The unverified cells above; which of them change a requirement | Erik LaBianca, in `frame` |
| Whether Pi Durable's non-tool hooks fail open as the README summary suggests (confirm in its source) | Erik LaBianca |
| A sourced growth figure for the market row, or removing it | Erik LaBianca |
