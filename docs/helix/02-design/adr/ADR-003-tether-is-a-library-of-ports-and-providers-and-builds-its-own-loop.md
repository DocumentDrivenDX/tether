---
ddx:
  id: ADR-003
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: competitive-analysis
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

Whether to build an agent harness or adopt one was not decided before the project began, and the answer shapes everything else. The products surveyed ([competitive analysis](../../00-discover/competitive-analysis.md)) split into a TypeScript durable harness (Pi Durable), Python frameworks that own the control flow and the paused turn's state (LangGraph, the OpenAI Agents SDK), and a provider-neutral model layer (Pydantic AI). The target user embeds a runtime in an application that already owns its interface, storage, and policy. A library that owns the user's state, or that cannot be embedded, defeats the purpose.

## Decision

We will build tether as a library of ports and providers with its own small loop, in Python, with no dependency on an agent framework. The core is the loop, the tool pipeline, the gates, the session service, the client API, and the ports; every external system (model, store, policy, telemetry, interface) is an adapter behind a port the core owns; every port has a dummy implementation and a conformance suite.

- **Ports are the seams.** Model, policy, scope binding and kinds, tool provider, result guard, audit sink, tracer, session store, event sink, interaction, clock, and identifiers (CONTRACT-001, CONTRACT-002, CONTRACT-003). Port payloads are frozen `pydantic` models; the ports are `typing.Protocol` classes; they are asynchronous where they can wait on I/O (ADR-001).
- **Providers contribute features to the loop.** A provider, keyed by an id, may offer tools, put a capability on the tool context, add a prompt section, hide tools, refuse a call before it runs, and transform a message before the model reads it or a result after the guards ran.
- **Providers narrow and never widen.** The engine owns the gate sequence. A provider hook may refuse, hide, or redact; it cannot allow what a gate denied, skip a gate, or remove an audit entry. A hook that raises is a refusal (fail closed).
- **The engine reads an `AgentSpec`.** An `AgentSpec` holds what the loop reads of a task: id, version, instructions, model tier, tool names, step limit, scope keys, and `ext` blocks passed untouched to providers. An application's own declarations (workflows, prompts, manifests) are compiled to an `AgentSpec` by the application; tether defines no manifest format.
- **The core reads no environment.** The application builds configuration objects and passes them in.
- **Sessions are not owned by a framework.** The session log and its store are tether's own (ADR-005).

## Why We Chose This

### Over adopting Pi Durable
Pi Durable (Earendil's experimental framework for crash-tolerant agents, MIT licensed) is TypeScript on Node, so adoption means a different language or a sidecar. By its README it has no tenant model and no Postgres store, allows one process per storage with no cross-process locking, and reports and skips a throwing hook other than `beforeTool`, which would let a failing gate pass (the last point needs confirming in its source). Its API is declared experimental. Its ideas are adopted instead: the extension shape, a tool replay marker, per-step checkpoints, and a storage conformance suite.

### Over adopting LangGraph or the OpenAI Agents SDK as the engine
Either would own the turn's control flow and, with a checkpointer, the paused turn's state, while the target user's application already owns sessions, so a turn would have two owners of state. The gates would still have to be built around the framework's hooks.

### Over extending Pydantic AI's `Agent` loop with hooks
It would give a loop for free and make the loop Pydantic AI's: its control flow and message model would define tether's. Tether uses Pydantic AI only for model calls (ADR-002).

### Over a larger framework with orchestration graphs and multi-agent handoffs
It is a different product (the survey's "avoid" list). The vision's non-goals exclude it.

## Consequences

**Good**: A loop small enough to read, test alone with recorded conversations, and embed. Boundaries enforced by ports and a check. One extension surface. The build-versus-adopt decision is on record with its reasons and its revisit trigger.

**Trade-offs**: The project maintains its own loop, gates, and session service. The provider surface may prove too narrow or too wide; the first downstream platform's features are the test. A bake-off with another loop remains possible through the session service seam; it is not a commitment of this ADR.

**Drift signals to watch**: A platform field added to the engine's session types. A branch on a provider id in the engine. A provider hook whose return value widens a decision. A port without a dummy or a suite. An environment read in the core. A manifest format added to the library.

## Concern Impact

- **Override / deviation**: The core reads no environment, so it does not use `pydantic-settings`, which the `python-uv` concern prescribes for configuration. The override is recorded in `docs/helix/01-frame/concerns.md` (Project Overrides). The `hexagonal-architecture` concern is selected.
