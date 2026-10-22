---
ddx:
  id: ADR-012
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: SPIKE-002
      kind: informed_by
    - id: ADR-002
      kind: informs
    - id: ADR-003
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
    - id: product-vision
      kind: informs
    - id: concerns
      kind: informs
---

# ADR-012: Tether Depends on No Foundation-Model Provider SDK, and Local and Sovereign Models Are First-Class

## Problem

A harness that is written against one provider's SDK takes on that provider's data model, its release schedule, and its terms, and every feature it uses that only that provider offers becomes a reason the harness cannot run elsewhere. Organizations that run models on their own hardware, or under their own jurisdiction (sovereign models), cannot accept a harness that assumes a hyperscale foundation-model provider. The operator's direction (Erik LaBianca, 2026-10-22) is that tether will not be written against the Claude or OpenAI SDK unless it absolutely has to be, so as to keep maximum leverage for local and sovereign models and to avoid baking in a dependency on a hyperscale provider.

## Decision

We will make provider independence a design goal that binds every change: tether imports no foundation-model provider's SDK, its model path is the open OpenAI-compatible chat-completions protocol that local and self-hosted servers implement, and local and sovereign models are first-class in the defaults, the demo, the tests, and the documentation.

- **No provider SDK in the library.** No module of the core or of the default model adapter imports the SDK of a foundation-model provider: `openai`, `anthropic`, `claude_agent_sdk`, `google.genai`, `google.generativeai`, `vertexai`, `mistralai`, `cohere`, `groq`, or the cloud-provider model SDKs for hosted models. The boundary check enforces a list of these names.
- **The protocol is the dependency.** The default adapter is tether's own client over `httpx` that speaks the OpenAI-compatible chat-completions protocol (SPIKE-002). The name denotes the wire protocol, not the SDK or the service. A hosted provider that offers an endpoint of that protocol works through it; tether takes no position against using one, only against depending on one.
- **Server-specific parameters pass through.** The port carries an `extra` mapping of request parameters the adapter sends unchanged, so the sampling, caching, and grammar controls that local servers expose are usable without the port interpreting them.
- **Provider-only features are optional capabilities.** A feature only one provider offers (a hosted caching mode, a provider's native tool or reasoning format) may be offered by an optional adapter as a declared capability, and the core and the contracts never require it.
- **Local models are first-class.** The demo needs no model and no network (the scripted model); the documentation's first real-model example points at a local server; the adapter's conformance matrix is built from local and self-hosted servers; the benchmarks and the replay tests use scripted models and cassettes recorded on fabricated prompts.
- **An exception needs an ADR.** Using a provider SDK requires an ADR that shows the protocol cannot do the thing ("absolutely have to"), confines the SDK to one optional adapter package, and never makes it a dependency of the core or a default.
- **Examples and defaults stay neutral.** No example, default configuration, or test assumes a hosted provider.

## Why We Chose This

### Over writing adapters against the provider SDKs
It gives each provider's full feature set, at the cost of an adapter, a dependency, and a feature gap per provider, and it makes the most capable hosted services the implicit baseline that local models are measured against.

### Over taking the protocol and the SDK together (the `openai` package as a protocol client)
Many tools use the OpenAI SDK only as an HTTP client for the compatible protocol. It works with local servers, but it is the SDK of a hyperscale provider in the dependency tree of every install, and it brings that provider's data model and release cadence. The protocol is small enough to speak directly (SPIKE-002).

### Over an aggregation library that fronts many providers
It would hide provider differences, at the cost of a large dependency whose provider list is still the hyperscale providers' and whose abstractions would define tether's.

### Over leaving it to individual adapters
A goal that only applies when remembered erodes. A boundary check and an exception process keep it enforceable.

## Consequences

**Good**: A harness that runs wherever an OpenAI-compatible server runs, including air-gapped and sovereign deployments. A small, auditable model path. No provider's release schedule or terms in the dependency tree. The demo and the tests need no network.

**Trade-offs**: Tether maintains its own protocol client and finds the quirks of each server through a conformance matrix. A feature available only through a provider's native API is out of reach by default. Some local servers lag the protocol, which the adapter's quirk table absorbs.

**Drift signals to watch**: An import of a banned SDK name anywhere in the package. A default, an example, or a test that names a hosted provider. A port or contract field that only one provider can fill. The demo requiring a network. A dependency that pulls a banned SDK in transitively by default.

## Concern Impact

- **Override / deviation**: This ADR adds a project-local concern, model provider independence, recorded in `docs/helix/01-frame/concerns.md`.
