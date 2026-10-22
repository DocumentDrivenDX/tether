---
ddx:
  id: ADR-003
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-019
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: ADR-017
      kind: informed_by
    - id: ADR-016
      kind: informed_by
    - id: product-vision
      kind: informed_by
    - id: competitive-analysis
      kind: informed_by
    - id: competitive-analysis-agent-harness-review
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: CONTRACT-002
      kind: informs
    - id: concerns
      kind: informs
---

# ADR-003: Tether Is a Library of Ports and Providers and Builds Its Own Loop

## Problem

Tether must choose between an owned Python harness, a TypeScript implementation, and adopting an existing runtime. Python embedding and a human-facing interface are equally important. Python gives direct access to existing tools and application objects; TypeScript offers substantial agent and browser tooling. Existing frameworks already provide loops, sessions, approvals and streaming. Those capabilities alone do not justify rebuilding them.

The product needs application-controlled authorization, explicit continuation with live identity, replaceable persistence, owner-scoped session reads and changes, and model-provider independence. The [framework and AgentKit review](../../00-discover/agent-harness-framework-review.md) compares that combination with current alternatives. The existing application is already deployed on Databricks Apps; another deployment wrapper is not a product need.

## Decision

We will build tether as a library of ports and providers with its own small loop, in Python, with no dependency on an agent framework. The core is the loop, the tool pipeline, the gates, the session service, the client API, and the ports; every external system (model, store, policy, telemetry, interface) is an adapter behind a port the core owns; every port has a dummy implementation and a conformance suite.

- **Ports are the seams.** Model, policy, scope binding and kinds, tool provider, result guard, audit sink, tracer, session store, event sink, interaction, clock, and identifiers (CONTRACT-001, CONTRACT-002, CONTRACT-003). Port payloads are frozen `pydantic` models; the ports are `typing.Protocol` classes; they are asynchronous where they can wait on I/O (ADR-001).
- **Providers contribute features to the loop.** A provider, keyed by an id, may offer tools, put a capability on the tool context, add a prompt section, hide tools, refuse a call before it runs, and transform a message before the model reads it or a result after the guards ran.
- **The engine owns the gate sequence, and providers' outputs are validated.** A provider hook returns a refusal, a hidden-tool set, or a structurally valid message; the engine ignores nothing it returns and cannot be made to skip a gate or remove an audit entry by it, and a hook that raises is a refusal (fail closed). Providers are trusted application code, so this is a statement about what the engine does with their outputs, not a sandbox (ADR-016). Providers compose in a fixed order with validated results.
- **The engine reads an `AgentSpec`.** An `AgentSpec` holds what the loop reads of a task: id, version, instructions, model tier, tool names, step limit, and `ext` blocks passed untouched to providers; scope keys are an optional package (ADR-017). An application's own declarations (workflows, prompts, manifests) are compiled to an `AgentSpec` by the application; tether defines no manifest format.
- **The core reads no environment.** The application builds configuration objects and passes them in.
- **Sessions are not owned by a framework.** The session log and its stores are tether's own (ADR-005, ADR-019), and an application holds a reference to a conversation, not a second copy (ADR-018).

- **Keep one Python harness.** Retain Python for the library and reference clients. A consuming browser UI may use TypeScript through its application's existing server. Do not add a second TypeScript harness, Python tool service, or AgentKit wrapper to hedge the language decision.
- **Do not adopt AgentKit.** Its invocation supervisor overlaps request deduplication, background work, status and event replay, but does not replace the selected model/tool pipeline, authorization, session-store contract or fresh-identity continuation. Keep it out of the dependencies and build plan.
- **Replacement must remove complexity.** Reconsider a runtime only when a concrete replacement identifies code to delete, preserves the required behavior, and simplifies the whole application. An API naming difference, queue policy or cursor shape is not enough reason to build a runtime. A speculative wrapper is not an adoption strategy.

## Why We Chose This

### Over moving the harness to TypeScript
TypeScript would improve direct reuse of JavaScript agent packages and sharing of schemas and types across the server and browser. It would move existing Python tools and live application objects across a process boundary. Python keeps that integration in process; the browser already needs a network boundary with either server language. The accepted cost is maintaining a Python-to-browser contract in the consuming application. Python's runtime introspection helps derive tool schemas, but dynamic registration is possible in both languages and does not settle the decision.

Databricks supports both application runtimes. Its Python AgentKit and TypeScript AppKit demonstrate different integration choices, not a platform requirement to choose either language. The reviewed Genie Code CLI documentation does not establish its implementation language. Ecosystem activity is evidence of useful alternatives, not proof that Tether can adopt their packages without changing its ownership or dependency policy. Source scope and dates are recorded in the research.

### Over adopting Pi Durable
Pi Durable is a close TypeScript reference with checkpointed tasks, replay declarations and conversation watching. Its experimental API and documented single-process storage ownership require qualification against Tether's consumer and store requirements. Its ideas inform the owned implementation. TypeScript alone is not a defect or a complete rejection rationale.

### Over adopting LangGraph or the OpenAI Agents SDK as the engine
These runtimes save real loop, persistence and approval work. Integration can select one authoritative state owner; duplicate ownership is not inevitable. Tether would still need its exact policy, session projections, feed and recovery semantics, plus translation to the adopted runtime. LangGraph becomes more compelling if orchestration graphs are required. The OpenAI Agents SDK conflicts with the selected provider-SDK policy (ADR-012).

### Over extending Pydantic AI's agent loop with hooks
Pydantic AI Harness is the strongest additional Python alternative in the reviewed scope: current capabilities include step persistence, effect records and UI integrations. Adoption would give it the transition and message model while retaining Tether-specific integration work. Tether keeps its own loop; any optional model adapter remains subject to ADR-002 and ADR-012, including transitive-dependency checks. It is inaccurate to justify this decision by claiming that Pydantic always needs a separate service for persistence.

### Over adopting or wrapping AgentKit
The reviewed `databricks-agentbricks==0.6.0` supplies a FastAPI invocation supervisor and Databricks-managed durability. It neither supplies the agent loop nor meets Tether's full session and recovery contract. Request-user recovery fails before the application recovery handler; fresh-identity continuation would remain ours. Wrapping Tether retains that work and adds another invocation lifecycle and event store. Apps hosting is already available without this library. The decision is to retain Tether without an AgentKit layer.

### Over a larger framework with orchestration graphs and multi-agent handoffs
It is a different product (the survey's "avoid" list). The vision's non-goals exclude it.

## Consequences

**Good**: Direct Python embedding, one owned execution model, replaceable integrations, deterministic test doubles, and no additional runtime wrapper. Browser presentation remains an application concern with a defined client boundary.

**Trade-offs**: Tether maintains its loop, gates, recovery, protocol compatibility and session service. Cross-language UI contracts cost work, and TypeScript packages are not directly reusable inside Python. The framework review's uncertain-effect, cancellation and approval findings remain design/verification obligations, not evidence of Tether superiority. In-process extensions are trusted application code; engine checks do not contain arbitrary Python.

**Evidence limit**: This choice is a contract-fit decision supported by operator direction and dated research, not a benchmark or a code-level duplication audit of the deployed application. The reviewed checkout contains the reusable-library design and probes. If a deployed-code comparison demonstrates a replacement that removes substantial machinery while preserving the required behavior, revisit this decision. No unmeasured speed or maintenance-cost advantage is claimed.

**Drift signals to watch**: A second execution supervisor or a second authoritative turn state. AgentKit added without a replacement/deletion case. A platform field added to core types. A provider return value that widens an authorization decision. A port without a dummy or suite. An environment read in the core. A manifest format added to the library. Python embedding ceasing to be a real consumer requirement.

## Concern Impact

- **Override / deviation**: The core reads no environment, so it does not use `pydantic-settings`, which the `python-uv` concern prescribes for configuration. The override is recorded in `docs/helix/01-frame/concerns.md` (Project Overrides). The `hexagonal-architecture` concern is selected.
