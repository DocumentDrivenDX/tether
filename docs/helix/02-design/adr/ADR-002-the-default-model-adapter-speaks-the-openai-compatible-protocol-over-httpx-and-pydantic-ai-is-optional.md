---
ddx:
  id: ADR-002
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-012
      kind: informed_by
    - id: SPIKE-001
      kind: informed_by
    - id: SPIKE-002
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
---

# ADR-002: The Default Model Adapter Speaks the OpenAI-Compatible Protocol Over `httpx`, and Pydantic AI Is Optional

## Problem

The loop must call models without depending on any of them, carry each caller's credential and the session's tags to the server, stream text, assemble tool calls, and report outages without leaking content. ADR-012 forbids depending on a foundation-model provider's SDK and makes local and sovereign servers first-class. The first choice for the adapter was Pydantic AI, which SPIKE-001 showed can carry every requirement. SPIKE-002 then showed that Pydantic AI's OpenAI-compatible model cannot be imported without the `openai` package, so using it as the default would put a hyperscale provider's SDK in every installation.

## Decision

We will make the default model adapter tether's own client, written over `httpx`, that speaks the OpenAI-compatible chat-completions protocol, and we will offer Pydantic AI as an optional adapter for providers outside that protocol.

- **The default adapter** is `tether.adapters.openai_compat`, in the `models` extra, whose only third-party dependency is `httpx`. It implements the model port's one method, `stream` (CONTRACT-001). The module name denotes the wire protocol, not the SDK or the service.
- **It carries the requirements the spikes tested.** The caller's credential from the request's `credential`, set as the call's `Authorization` header (SPIKE-001 finding 7, SPIKE-002); the session tags and trace context as headers; server-specific parameters from the request's `extra` merged into the body; token usage when the server sends it; the server's request id; an outage whose reason is a status or an error class and nothing from the body; detection of a truncated stream; cancellation that closes the connection (SPIKE-002 findings 2 to 5).
- **It is a port, not a rewrite.** The adapter ports the first downstream platform's tested client of this protocol (about 460 lines and about 800 lines of tests), read and scrubbed first. It keeps that client's request and reply mapping (including reply content returned as a list of parts, with the model's reasoning parts dropped, and a usage block checked before it is trusted), its stream assembly (tool calls in indexed fragments, an error chunk in mid-stream treated as an outage, the body's id standing in when no header carries a request id, a usage chunk that has no choice), and its error diagnostics (an allow-listed error code and a scrubbed message, never the body). It converts the blocking `urllib` calls to asynchronous `httpx`, and it leaves behind the platform-specific transport, tier mapping, and environment configuration. Its tests port with it, and the first commit that adds it names it by description.
- **Retries live in one layer.** The adapter does not retry; the caller owns the policy (CONTRACT-001).
- **A conformance matrix finds server quirks.** The adapter is tested against recorded cassettes of local and self-hosted servers on fabricated prompts, and live on demand when a server is available; a quirk table in the adapter absorbs differences in tool-call streaming, finish reasons, usage, and reasoning fields.
- **Pydantic AI is an optional adapter.** `tether.adapters.pydantic_ai`, in the `pydantic-ai` extra, implements the same port for providers whose protocols the default adapter does not speak. It MUST NOT import `openai` or any banned SDK itself; it builds its models through Pydantic AI's own provider classes. Installing its extra is the user's decision. It is never a default, and the core never imports it. SPIKE-001 is its evidence.

## Why We Chose This

### Over Pydantic AI as the default adapter (the earlier choice)
It carried every requirement (SPIKE-001) and would have supplied a provider-neutral message model and test models. It lost because its OpenAI-compatible path requires the OpenAI SDK (SPIKE-002 finding 1), which ADR-012 excludes, and because the protocol is small enough to own (SPIKE-002 finding 2). It remains available as an optional adapter, and it is the fallback if the cost of server quirks outgrows the owned adapter.

### Over the `openai` SDK used as a protocol client
It works with local servers, but it is the SDK of a hyperscale provider in the dependency tree of every install, which ADR-012 rules out unless the protocol cannot do the job, and it can.

### Over a bespoke adapter per provider SDK
It would bind tether to each provider's data model and release cycle, and to a feature set that local models may lack.

### Over an aggregation library
It fronts the hyperscale providers' SDKs and its abstractions would define tether's.

## Consequences

**Good**: No provider SDK anywhere in the default install; the dependency is `httpx`. A model path small enough to read in full and to test with cassettes. Exact control of errors, parameters, and cancellation. A local server works by configuration alone.

**Trade-offs**: Tether owns the protocol client, which starts from tested code and so inherits the quirks it already handles; it still discovers each local server's own. Message and tool mapping, retries policy, and content types beyond text (images, audio) are the project's to build, or to leave to the optional adapter. Pydantic AI's test models and provider breadth are not the default.

**Drift signals to watch**: An import of a banned SDK anywhere in the package, including the optional adapter. The optional adapter becoming a default or a dependency of the core or the clients. Quirk handling spreading outside the adapter's quirk table. Error text from a model call in a log, an audit reason, or an API response.

## Concern Impact

- **No concern impact beyond ADR-012**: `python-uv` covers the stack; `httpx` is the one dependency the default adapter adds, and this ADR is the coverage AGENTS.md requires for it.
