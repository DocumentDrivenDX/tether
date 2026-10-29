---
ddx:
  id: ADR-013
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: ADR-012
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
    - id: prd
      kind: informs
---

# ADR-013: The Databricks AI Gateway Is a First-Class Backend, Delivered as a Profile, and Never the Only Backend

## Problem

The first downstream platform calls models through the Databricks AI Gateway: an OpenAI-compatible chat-completions route in which the target is named by a three-level Unity Catalog service name, callers are attributed by their identity and `User-Agent`, and per-user budgets apply. Tether must keep supporting it (operator direction, Erik LaBianca, 2026-10-22). The risk is that a backend the first consumer depends on becomes the baseline that shapes the core, which would undo ADR-012.

## Decision

We will support the Databricks AI Gateway as a first-class backend, delivered as a profile of the default OpenAI-compatible adapter in an optional `databricks` extra, and we will hold that no feature of the library works on that backend alone.

- **A profile, not a second client.** `tether.adapters.databricks` configures the default adapter (ADR-002): the route, the mapping from a tier name to a gateway service name, a renderer for the attribution tags, and the credential resolution. It adds no protocol code.
- **Attribution and correlation.** Required opaque session/turn/model-call/attempt/purpose tags use the documented `Databricks-Ai-Gateway-Request-Tags` JSON header. Optional bounded User-Agent attribution and real `traceparent` remain secondary signals. Keep header request id, completion body id and provider invocation id distinct; exact row/tag/header joins require profile-specific live qualification (CONTRACT-005). Reuse the inspected downstream capture/read pattern with async I/O and stronger multi-invocation/conflict handling.
- **Identity.** A request's actor credential is used when present (ADR-011). Otherwise the application's own identity comes from the Databricks SDK's unified authentication (a service principal, or a developer's command-line profile), which this module alone imports and only for host discovery and authentication. A `require_user` mode refuses a call that has no user credential instead of falling back to the application's identity, as the first downstream platform's implementation does.
- **Reference launcher.** `--databricks-profile NAME` derives the workspace host from the explicitly selected installed CLI profile; no URL is required. The CLI bridge owns bounded model-service discovery and OAuth token refresh, while the existing compatible adapter owns inference. Discovery initially covers `system.ai` services advertising `mlflow/v1/chat/completions`, with a picker before session creation and an explicit `--model` bypass. Catalog metadata is not invocation permission or context-capacity evidence. The bridge pins the discovered host and refuses later changes; it never persists credentials. This launcher qualifies existing CLI OAuth user profiles only; SDK application identity and full gateway attribution/history qualification remain separate profiles.
- **Declared wire capabilities.** The reference profile omits the unsupported `parallel_tool_calls` option in native inference and offline estimation, uses the gateway request-tag header with string-valued opaque tags, and permits validated partial usage on intermediate SSE chunks while requiring complete terminal input/output counts. These choices do not add an inference client or relax tool approval. Broader cache accounting and history/row joins require separate qualification.
- **Configuration is explicit.** The adapter reads no environment variable. The application builds its settings object and passes the tier map and the identity mode in.
- **Never the only backend.** Every feature of the core runs against the scripted model and against the default adapter on a plain OpenAI-compatible server. The adapter conformance matrix includes at least one backend that is not the Databricks AI Gateway. No contract field, port method, or default exists only to serve the Databricks backend.
- **Gateway authoritative history.** CONTRACT-005 makes inference-table model exchanges a first-class selected HistoryProvider source, composed with a writable supplement. The execution journal holds facts and content refs; a gateway request is the sent model projection, not the full original conversation. Reader discovery/query authentication uses the explicit calling actor, bounded SQL and configured locators; no schema mutation or denied-user fallback. The profile may use the platform SDK for these adapter-owned reads as well as authentication. Delayed/missing/partial/ambiguous observations are explicit.

## Why We Chose This

### Over a separate Databricks adapter with its own client
It would duplicate request mapping, stream assembly, and error handling that the default adapter already has, and the two would drift.

### Over the Databricks SDK's own OpenAI client helper
Its OpenAI SDK dependency is eligible in an explicitly installed optional adapter extra under ADR-012. It is not selected merely for authentication convenience: the chosen Pydantic AI provider/model integration must preserve explicit identity, wire tags, every physical attempt and scrubbed errors. A helper that cannot preserve those boundaries is unsuitable; SDK wrapping alone is not the reason.

### Over using `--gateway` as a Databricks profile name
Gateway connection selection and authentication source are distinct. The Databricks shortcut supplies both from its CLI profile; an explicit `--gateway URL` remains a compatible endpoint URL for any supported server. Brand names do not define the inference protocol, streaming mode or discovery API.

### Over making the Databricks AI Gateway the default or only backend
It matches the first consumer, and it would put one platform's naming, identity, and attribution into every user's quickstart and into the core's types.

### Over leaving Databricks support to the downstream platform
The first downstream platform could not adopt a release without re-implementing it, and the ported implementation is already tested and in use.

## Consequences

**Good**: The first downstream platform can adopt tether without losing its gateway behavior. Other backends and the Databricks backend share one protocol client. Per-user identity, attribution, and the join key keep working.

**Trade-offs**: The reference launcher requires an installed, signed-in Databricks CLI and uses its OAuth token refresh without a Python SDK dependency. The broader application profile may use a `databricks` extra with `databricks-sdk` for authentication only. The tag format and the tier map are Databricks-shaped, and they stay inside the profile. Live verification needs a workspace and a login, so the owner runs the live checks.

**Drift signals to watch**: A core type, port, or contract field that only the Databricks backend can fill. An import of the Databricks SDK outside the profile module. The default install depending on `databricks-sdk`. A quickstart or example that defaults to the Databricks backend. A feature whose only passing conformance test is on the Databricks backend.

## Concern Impact

- **No concern impact beyond ADR-012**: the Databricks SDK is a data-platform SDK, not a foundation-model provider's, so it is outside the banned list; the extra's dependency is covered by this ADR.
