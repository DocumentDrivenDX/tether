---
ddx:
  id: ADR-002
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: SPIKE-001
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
---

# ADR-002: Pydantic AI Is the Model Client Behind the Model Port

## Problem

The loop must call models from many providers without depending on any of them, carry each caller's identity and the session's tags to the provider, stream text, parse tool calls, and report outages without leaking content. Writing that HTTP, streaming, and parsing code for each provider is a large and perpetual cost, and a library that offers one adapter per provider has no end. The loop also needs test doubles, including a record-and-replay pair, that no provider library supplies.

## Decision

We will use Pydantic AI, an open-source Python library from the Pydantic team that wraps model providers behind one message and tool model, as the model client inside an optional adapter behind the model port (the `pydantic-ai` extra). The adapter calls only `pydantic_ai.direct.model_request` and `model_request_stream`, asynchronously (ADR-001). The core imports the port, never Pydantic AI.

- **One port, several implementations.** The port is `complete`, `complete_stream`, and a `provider` name (CONTRACT-001). Its implementations are the Pydantic AI adapter, a scripted model, a recording model that wraps any port and writes a cassette, and a replay model that serves a cassette.
- **The adapter reproduces the requirements SPIKE-001 tested.** The caller's token comes from an `httpx.Auth` that reads a context variable; the session tags and `traceparent` go in `ModelSettings.extra_headers`; `reasoning_effort` goes in `extra_body`; the provider's request id is read in a response hook; a library error maps to an outage whose reason is a short code such as `http 503` and nothing from the body. The client is built with `max_retries=0`, so the retry policy lives in one layer.
- **Pydantic AI's `Agent` is not used.** The loop, the gates, the session, and every decision stay in tether.
- **The version is pinned exactly.** An upgrade is its own change, gated by the adapter's wire tests.

## Why We Chose This

### Over a bespoke adapter per provider
It gives full control and no dependency, at the cost of maintaining HTTP, stream assembly, and tool-call parsing for every provider. Pydantic AI carries every requirement the spike tested, so a second provider is configuration.

### Over the `openai` SDK directly
It would remove the HTTP and streaming code but provides no provider-neutral message and tool model, so each non-OpenAI-shaped provider would still need its own adapter and conversion code, and it has no test models.

### Over a framework's model classes (LangChain)
They bring a framework whose loop and state concepts compete with the harness's own and a larger dependency tree. A model client is the only part wanted.

### Over Pydantic AI's `Agent`
The `Agent` owns the control flow of a turn. Tether's gates sit around the model call and each tool call, the session is persisted by its own store, and a pause for approval ends the turn and resumes in a later call. A second loop owning that flow creates two owners of session state (ADR-003).

## Consequences

**Good**: No provider HTTP code to maintain. A second provider is configuration. The port gains a record-and-replay pair that tests the loop and the adapter against real exchanges. The adapter's wire tests make the provider interaction a checked artifact.

**Trade-offs**: A third-party dependency that moves quickly, pinned exactly and optional (the core does not require it). One piece of adapter code the library does not provide: the error scrub. `extra_headers` and `extra_body` are not documented for the chat model, so the wire tests are the guarantee. The spike used a mock transport, so the first live call is part of the adapter's acceptance. The assumption is reversible because the port is unchanged if the adapter is replaced.

**Drift signals to watch**: An import of `pydantic_ai` outside the adapter module. Exception text from a model call in a log line, an audit reason, or an API response. A client built with retries above zero. An unpinned or range-pinned version. Wire tests replaced by live-only checks.

## Concern Impact

- **No concern impact**: `python-uv` covers the stack; this ADR is the coverage AGENTS.md requires for a new runtime dependency.
