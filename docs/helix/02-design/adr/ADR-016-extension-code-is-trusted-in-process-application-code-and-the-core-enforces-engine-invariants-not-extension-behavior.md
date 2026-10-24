---
ddx:
  id: ADR-016
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-003
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: CONTRACT-002
      kind: informs
    - id: architecture
      kind: informs
    - id: prd
      kind: informs
---

# ADR-016: Extension Code Is Trusted In-Process Application Code, and the Core Enforces Engine Invariants, Not Extension Behavior

## Problem

Tools and providers are Python objects that run in the application's process with its privileges. A protocol in a library cannot stop arbitrary Python from reading a file, opening a socket, or reading `ctx.actor.credential` without going through `ToolContext.read`. The first drafts said that a tool "MUST" read only through `ToolContext.read` and that extensions "cannot widen" a decision. The first is not enforceable and the second is true only for what the engine does with an extension's *return values*. A platform that relies on either claim as a security boundary would be misled.

## Decision

We will declare that Python tools and providers are trusted application code, and we will split what the core enforces from what an extension author must do. Isolating untrusted extensions is outside the core.

- **Trusted code.** A tool or provider has the application's privileges. Tether is not a sandbox, and its documents say so wherever they describe a control.
- **Engine invariants (enforced).** The gate sequence and its presence; the type and shape of what a provider returns (a refusal, a structurally valid message, a hidden-tool set); fail-closed behavior when a hook or guard raises; policy, approval, and guard execution on every call; authorization and recording of reads that go through `ToolContext.read`; audit and span writing by the engine, not by extensions; content-free telemetry; and that a credential is never written to an entry, a span, or an audit record.
- **Extension-author obligations (not enforced).** Reading data only through `ToolContext.read`; doing no independent network, file, or process I/O the application did not intend; not disclosing `ctx.actor.credential`; declaring `replay`, `requires`, and `traits` honestly; not blocking the event loop; and bounded runtime.
- **Hook composition and validation.** Providers run in registration order. `before_request` chains, each receiving the previous output, and its result must be a structural subset of its input (the same or fewer messages, unchanged roles, no new tool calls, attachments a subset); whether a content change is a true redaction is an obligation. `before_call` runs for every provider in order, and the first refusal wins and is recorded with its provider id. `after_call` chains, must keep the message's role, call id, and error flag, and any content change re-runs the guards. A reason without the `x.<provider>.` prefix is normalized. A hook that exceeds the engine's configured time limit fails closed. "Narrow only" applies to authorization and engine decisions, not to prompt content: `prompt_section` adds text.
- **Context planning.** Strategies are trusted code under the same boundary. Their validated outputs reference source ranges or existing summaries; only the engine creates derived context and invokes bounded summary models (CONTRACT-004). A strategy cannot add executable calls or supply a second model client.
- **Untrusted extensions.** A deployment that runs code it does not trust isolates it outside the core: in a subprocess or a sandbox tool, or behind the P2 MCP (Model Context Protocol) adapter. The core does not provide it.

## Why We Chose This

### Over process isolation for every extension
It is the strongest control, and it conflicts with a library that runs in the application's process and starts in seconds. An application that needs it can add it at a tool boundary.

### Over a restricted capability proxy that blocks bypasses
It cannot be made airtight in Python, and a partial block gives false assurance.

### Over leaving the claim implicit
A document that says "cannot widen" without the qualification leads readers to rely on it.

## Consequences

**Good**: The documents state exactly what is and is not guaranteed. A platform team knows which controls hold against a buggy extension (the engine's) and which depend on review (the author's). Providers compose predictably.

**Trade-offs**: A malicious extension is out of scope for the core. The audit's completeness claim covers mediated reads only. An application that wants stronger isolation builds it.

**Drift signals to watch**: A document or a marketing line that presents the provider surface as a sandbox. A contract rule that says an extension "cannot" do something the engine cannot prevent. A provider hook whose output the engine does not validate.

## Concern Impact

- **No concern impact**: This states the trust boundary for `security-owasp`; a `security-architecture` artifact may follow.
