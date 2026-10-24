---
ddx:
  id: ADR-012
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

# ADR-012: Provider Independence Allows Optional SDK Adapters and Keeps Local Models First-Class

## Problem

Tether must remain usable with local, self-hosted and sovereign models, with application-controlled identity, history and policy. An implementation that requires a hosted account, provider-only behavior or an unreplaceable backend would undermine that goal. A dependency's publisher alone does not establish those constraints.

The owner clarified that an optional OpenAI SDK dependency does not lock Tether into OpenAI's ecosystem and that working agent machinery deserves evaluation. SPIKE-002 proves that Pydantic AI's built-in OpenAI adapter needs the OpenAI SDK. SPIKE-003 proves that the exercised Agent path accepts a custom model without loading provider SDKs. Native Claude uses a different adapter. These facts support separating packaging, model implementation and hosted-service dependence.

## Decision

We will preserve provider-independent behavior while allowing explicitly selected optional provider SDK integrations. SDK wrapping alone is insufficient grounds to reject Pydantic AI or another reusable implementation.

- **Keep local and sovereign execution first-class.** Backend selection must support local/self-hosted execution by configuration, and the demo and deterministic tests need no network or hosted account. Required Tether behavior must not depend on a hosted provider's exclusive feature.
- **Keep provider dependencies optional and isolated.** The base install remains free of foundation-model provider SDKs. Selected adapter extras may install and import them, directly or through Pydantic AI. Application-facing core types, policy and storage boundaries must not require a provider SDK's types. Inspect the exact dependency closure of each selected extra; an optional dependency is still a dependency to maintain and disclose.
- **Use the selected Agent runtime separately from optional provider SDKs.** ADR-003 selects Pydantic AI Agent and the provider-neutral pydantic-ai-slim base dependency; ADR-002 selects model integration reuse. Provider extras remain optional. Harness capabilities are assessed individually against the selected journal/context authority. There is no planned owned loop or default owned HTTPX client.
- **Require a documented integration rationale.** Record the selected capability, optional extra, supported model/server subset, state and configuration boundaries, and evidence. The rationale need not prove that speaking a protocol directly is impossible. Reusing working code can be sufficient when the integrated design is better and its behavior is qualified.
- **Preserve explicit identity and configuration.** Provider clients are constructed at the integration boundary. Pass current per-call identity, headers, trace/call identifiers and bounded settings explicitly; keep credentials out of persisted content and operational telemetry. A hosted-provider environment default must not silently choose the caller's identity or backend.
- **Expose backend differences honestly.** Carry supported server parameters and declared capabilities. Native tools, reasoning, caching and compaction may vary; optional features cannot become unconditional core requirements. Qualify support by provider/model version, subset and evidence.
- **Test substitutability.** Boundary checks keep SDK imports in selected adapters, and dependency tests keep them out of the base install. Local-server and native-provider tests exercise the same required model behavior with fabricated data. An optional OpenAI SDK may be used purely as a compatible-protocol client; its presence does not prove a hosted OpenAI dependency.

## Why We Chose This

### Over banning all provider SDKs, including optional transitive dependencies

That rule confuses optional implementation dependencies with required hosted-service coupling. It excludes useful reuse without examining configuration, backend substitution or state ownership. The owner has rejected that as sufficient justification for the Pydantic AI decision.

### Over accepting a framework's provider-neutral label without inspection

A framework can offer many backends and still expose provider-specific data or defaults at application boundaries. Inspect actual extras, types, credentials, state, retry behavior and local-server execution. Optional packaging helps isolation; it does not certify compatibility.

### Over requiring Tether to maintain every protocol client

An owned client offers direct control and remains a valid alternative. Protocol feasibility and small prototype size do not settle maintenance cost or state quality. Compare it with the complete reusable implementation and the integration work retained.

## Consequences

**Good**: Local and sovereign models remain supported goals while optional adapters can reuse working provider and agent code. The selection can consider retries, context and conversation/persistence machinery on their merits. Backend choice remains explicit.

**Trade-offs**: Optional extras need dependency/version and behavior qualification. SDK releases and defaults remain maintenance obligations inside those extras. A provider-native path may require additional metadata preservation and migration handling.

**Drift signals to watch**: A provider SDK leaking into public core types or the base install. A hosted account required for the demo or core behavior. An optional dependency treated as proof of lock-in. Ambient credentials choosing a multi-user caller. A provider-specific feature becoming mandatory. A second authoritative transcript introduced to accommodate a framework.

## Concern Impact

- **Override / deviation**: `model-provider-independence` now allows isolated optional SDK integrations. It requires backend substitutability, provider-free base packaging and explicit capability/configuration boundaries. ADR-002/003 select Agent/model reuse; the associated concern and downstream build/boundary rules reflect the named internal runtime integration. Selection does not qualify every optional backend/profile: release support requires its recorded behavioral evidence.
