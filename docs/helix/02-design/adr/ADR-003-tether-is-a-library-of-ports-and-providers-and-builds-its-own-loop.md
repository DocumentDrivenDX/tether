---
ddx:
  id: ADR-003
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
    - id: SPIKE-004
      kind: informed_by
---

# ADR-003: Tether Is an Embedded Python Library Built on Pydantic AI Agent

## Problem

Tether needs asynchronous Python embedding, typed tools, model iteration, live identity, application policy, durable recovery and an owner-scoped client API. Existing agent libraries already supply useful iteration and state machinery. Building those mechanisms again needs a demonstrated advantage.

SPIKE-003 established custom/native backend seams, typed tools/output, deferral, bounded correction and conversation serialization. SPIKE-004 used real Agent public hooks with Tether-mediated tools, a fabricated model transport and fixture storage. Its 28 integration tests and three independent regressions exercised authorization, approval/source bindings, commit failures, physical attempts and five process termination/reopen scenarios. The owner selected Agent on that evidence. This is a direction decision; production qualification remains in SD-001 and TP-001.

## Decision

Build Tether as an embedded Python library of ports and providers using Pydantic AI Agent as its runner. Agent supplies model/tool iteration, typed dispatch/output, deferred-tool control, correction and conversation bookkeeping. Tether supplies the invariant-enforcing integration, policy, journal/history authorities, recovery decisions, session service and Client API. Do not implement a second general-purpose loop.

- **One selected runner.** Use public Agent/capability/iteration APIs. Keep initial tool execution sequential. No private graph imports, alternate loop, second supervisor or AgentKit wrapper is planned. A runner replacement needs a demonstrated requirement failure or concrete total-complexity case and an explicit decision revision.
- **A named internal integration.** tether.runtime.pydantic_ai owns Agent, SDK messages, Conversation codecs and capability composition. tether.engine remains the Tether-facing invariant facade and calls that integration through Tether-owned callbacks/types; the runtime does not import engine/session implementations. Provider/client integration belongs to the model adapters. Public contracts expose no Pydantic AI or provider SDK types.
- **A provider-neutral base dependency.** Install pydantic-ai-slim without provider extras for the selected runner, alongside Pydantic and the async baseline. The initial qualification baseline is the exercised 2.59.0 SDK/Graph with Pydantic 2.14.1 on Python 3.11.17; the release lock/matrix must qualify the version actually shipped. Optional provider SDK closures remain isolated under ADR-012. A custom scripted Model supplies offline tests/demo without a provider account.
- **Client boundary amendments.** Distinguish owning/policy subject from explicitly supplied model principal; caller credentials remain separate. Raw exchange disclosure has an additional current payload permission. Async composition binds to one event loop. These mechanisms cannot be added safely by a post-processing provider; CONTRACT-002/003/005 v2 specifies them without adding application personas, workflows or transport.
- **Tether owns the gates and commits.** Fixed trusted capability composition mediates current authorization, normalized arguments, approval, intent-before-effect, result guards and durable result acceptance. READ_SESSION precedes history/context/argument/pending/resume disclosure and fails closed. Recheck executable argument/tool/spec bindings at dispatch; an SDK approval boolean is not execution authority. Failed or uncertain commits stop dependent work.
- **One authority for each state kind.** The execution journal owns order, approvals, effects, resolution and feed projections. HistoryProvider owns original content and observed exchanges. SDK messages/Conversation are derived, versioned, journal-bound projections verified against authorized sources/results. Compaction or a cache cannot invent a result or replace originals. Recovery reconstructs Agent input from accepted facts and current identity; it does not trust an old SDK snapshot to authorize work.
- **Ports and providers remain the product boundary.** Preserve CONTRACT-001 through CONTRACT-005 and their dummies/conformance suites. Providers may contribute tools, capabilities, prompts, visibility, refusals and redactions only through Tether's validated interfaces. Raw SDK capabilities are internal trusted composition; application extensions do not get a second dispatch path. In-process Python remains trusted code, not a sandbox (ADR-016).
- **Context and persistence reuse is selective.** Reuse compatible algorithms/components only through CONTRACT-004/005. Harness is not the selected execution/recovery authority. A proposed Harness component must preserve the one journal, governed auxiliary calls, immutable originals and explicit source coverage; it cannot introduce another effect ledger or transcript authority.
- **Keep Python embedding and the existing server boundary.** The core accepts configuration and reads no environment. The consuming application owns its server/browser transport; CLI/TUI use Client. There is no second TypeScript harness or deployment wrapper.

## Why We Chose This

### Over implementing the loop ourselves

The integration demonstrated public seams for the required boundaries. Review found and corrected source/cache and read-policy adapter defects without replacing Agent. No functional necessity for a new general-purpose loop or maintenance advantage has been demonstrated. Tether still owns its guarantees; that does not require owning every runtime mechanism.

### Over direct model API reuse with an owned execution driver

That would preserve provider reuse but duplicate iteration, typed dispatch, deferral and correction already available in Agent. It remains a fallback only if production evidence exposes a requirement that public Agent integration cannot preserve without disproportionate replacement.

### Over adopting all of Harness or a durable engine

Context/store/effect components can be useful, but snapshot persistence does not establish Tether's complete recovery or Client contract. The chosen journal and source model remain authoritative. No additional durable service or Harness runtime is required by this decision.

### Over TypeScript, AgentKit or a second runtime

Direct Python tools/application objects remain an embedding requirement. The application already owns its server and UI boundary. A second runtime/supervisor would add integration without replacing Tether's policy, history and recovery obligations. Graph orchestration and multi-agent handoffs remain outside scope.

## Consequences

**Good**: Reuse established execution and state mechanisms while preserving Tether's application-facing boundaries. There is one normal runner and one authoritative execution journal.

**Trade-offs**: Tether maintains the runtime/model translation, policy and commit integration, selected version compatibility and source codecs. Public-hook changes require regression qualification. Native SDK transport/streaming, production stores, full context/client behavior and actual worker lifetime remain acceptance work; the fixture spike is not release conformance or a cost benchmark.

**Drift signals to watch**: An owned loop reappearing as a default slice; private graph access; arbitrary capabilities bypassing gates; SDK history accepted without source/checkpoint verification; model calls outside the attempt path; parallel tools before qualification; multiple ledgers or SDK types in public APIs.

## Concern Impact

The hexagonal-architecture concern permits the selected internal runtime integration to depend on pydantic-ai-slim; public payloads and external I/O ports remain Tether-owned. Record this explicit interpretation/override in concerns.md. ADR-012 keeps foundation-model SDKs optional and isolated. Async-only Tether APIs, trusted-extension limits and no environment reads remain in force. Agent selection is accepted; provider/store/profile support claims remain separately qualified.


Contract version 3 adds a concrete storage-scoped actual-worker lifetime shared by leased logs,
stores and file roots. Replacement Engines and pending-inspection workers participate in the same
ownership boundary; cancellation cannot authorize another effect or release the root early.
Provider hook signatures remain compatible with versions 1 and 2. Agent remains the only iterator.
