---
ddx:
  id: ADR-002
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: SPIKE-003
      kind: informed_by
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
    - id: SPIKE-004
      kind: informed_by
---

# ADR-002: Reuse Pydantic AI Model Integrations Behind Tether's Model Boundary

## Problem

Tether needs compatible local/self-hosted models, optional native providers, explicit current identity, bounded streaming and retries, complete attempt correlation and scrubbed errors. Maintaining another protocol stack is not itself a product goal. SPIKE-001/002 established client feasibility; SPIKE-003 established backend substitution and core Agent capabilities. SPIKE-004 exercised the stricter Tether model and execution boundaries with a custom model and fabricated transport.

## Decision

Use Pydantic AI's model APIs and provider integrations as the default implementation path for the Agent runner selected in ADR-003. Keep the Tether ModelPort contract and provider-neutral public types. Do not build an owned HTTPX protocol client as the default.

- **Reuse adapter implementations.** The compatible-server profile uses the qualified Pydantic AI compatible-provider model; optional native profiles may use native models such as AnthropicModel. The runner is selected; individual provider/server subsets still require qualification. A built-in provider adapter is not accepted solely because it exists.
- **Mediate every request.** The runtime bridge and shared model translation layer implement CONTRACT-001/004/005: source authorization, full-payload budgets, terminal/structure limits, current credentials, reserve/render/authorize+journal before each physical attempt, receipt capture and scrubbed errors. Reject incomplete or unknown terminal responses before Agent can dispatch tools or claim final output.
- **Own retries deliberately.** Reuse Agent's bounded model-correction loop. One configured transport layer owns physical retry/backoff; disable SDK retries, fallbacks or continuations that cannot expose every egress to the attempt recorder. Count corrections, summaries and physical attempts against shared policy/deadlines without conflating their identities.
- **Keep dependencies isolated.** The provider-neutral pydantic-ai-slim runtime is a base dependency (ADR-003). Foundation-model provider SDKs belong only to explicitly installed adapter extras (ADR-012); no hosted account is required for the base/demo/custom-model path. Model/client configuration is explicit and per-call identity is not implemented by mutating shared client defaults.
- **Use a custom Model for a demonstrated gap.** An SDK-independent custom model remains a supported integration seam. If a built-in adapter cannot meet wire, identity or attempt requirements, adapt that seam and document its supported subset. Reopening an owned protocol client or replacing Agent requires a concrete failure/complexity case, not an optional SDK dependency or an internal type mismatch.

## Why We Chose This

### Over an owned compatible-protocol client by default

Working provider mapping, response assembly, usage extraction and stream handling can be reused. The smaller SPIKE-002 client proves feasibility, not lower ongoing maintenance. Optional OpenAI SDK use does not imply a hosted OpenAI dependency.

### Over treating all native adapters as qualified

SPIKE-004 used a nonstreaming custom Model with MockTransport, not a native SDK or live server. The production adapter must prove the same behavior, particularly every physical request and strict terminal handling. SD-001 records those release gates.

### Over adopting a provider's client as Tether's public contract

Provider SDK types, ambient identity and provider-specific defaults cannot become requirements of Tether's policy/storage/client interfaces. One translation owner adapts them; another public protocol stack would duplicate that ownership.

## Consequences

**Good**: Agent and native/compatible model integration share an established model abstraction. Tether spends implementation effort on its guarantees and selected backend qualification.

**Trade-offs**: Adapter extras, serialization and SDK upgrades need dependency and behavior checks. Model translation, strict terminal rules and transport-level attempt mediation remain Tether work. Native streaming and live Gateway correspondence are not established by the spike.

**Drift signals to watch**: A parallel owned HTTPX client in the default build; invisible SDK retries; shared mutable caller credentials; provider types escaping public boundaries; a new hosted requirement; two independent wire/history conversion authorities.

## Concern Impact

`model-provider-independence` retains a base free of foundation-model provider SDKs and explicit optional provider closures under ADR-012. This decision selects reuse rather than another protocol implementation. ADR-003 selects Agent; SD-001 and TP-001 own remaining qualification. SPIKE-003/004 establish bounded feasibility, not complete provider support.
