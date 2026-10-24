---
ddx:
  id: component-profile-hermes-agent
  type: component-profile
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: prd
      kind: informed_by
    - id: concerns
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-012
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: competitive-analysis
      kind: informs
---

# Component Profile: Hermes Agent

## Current Tether design disposition

The owner directed pre-release contract changes on 2026-10-22. [ADR-021](../02-design/adr/ADR-021-model-context-is-a-bounded-projection-of-the-immutable-log.md), [CONTRACT-004](../02-design/contracts/CONTRACT-004-context-projection-and-compaction.md), revised CONTRACT-001/002/003, and [TP-001](../03-test/test-plan.md) now define context projection, governed summaries, model/request bounds, effect resolution/barriers, worker lifetime and reconnect snapshots. CONTRACT-005 now separates the execution journal from authoritative original content/model exchanges and derived context. JSONL is the default history provider; Gateway exchanges require correlation and supplemental content. The 2026-10-23 coordination also corrects the earlier blanket SDK criterion: optional provider SDK adapters are eligible under revised ADR-012, and runtime reuse is open under ADR-002/003. Hermes remains a source of design elements and regression cases; no Hermes code was imported.

## Scope

- Component: NousResearch Hermes Agent; selected public source pinned to `92663ae8054ae81f207f46d83512d3f879dc698d`, with current official documentation accessed 2026-10-22. Documentation is not pinned to that source and may describe a different revision.
- Kind: Product; candidate source of harness mechanisms and small reusable components.
- Would fill: optional mechanisms behind Tether-owned boundaries; whole-engine adoption is also screened against the same requirements.
- Feeds: the [framework review](agent-harness-framework-review.md), existing ADR-003, and later context-management design for PRD FR-35. This research changes no accepted decision or implementation scope.
- Incumbent: Tether's specified Python/library boundaries. The underlying runtime selection is open under revised ADR-002/003; its specifications are intent, not evidence that the implementation is complete.
- Researched: 2026-10-22. No package installation, execution, extraction test, benchmark, or dependency-closure verification was performed.
- Excluded: a full security audit, exhaustive source inventory, market-share ranking, and adoption of Hermes's coding tools, messaging gateway, or autonomous delegation.

## Summary

Tether needs reusable mechanisms for a Python harness whose application controls identity, policy, and durable execution. Whole-engine reuse must preserve asynchronous ports, explicit configuration, model-provider independence, and Tether's session contract. Hermes's documented engine is no fit for direct adoption under those constraints, while its context design, recovery ownership, reconnect behavior, regression scenarios, and small utility functions merit targeted adaptation.

## How to Read This Profile

**Met** means the cited public record documents the stated capability; **Unmet** means the record contradicts a requirement; **Unknown** means the inspected record does not settle it. **Fit** requires every capability Met; **Conditional fit** permits only non-design-defining Unknowns; **Undetermined** means a design-defining Unknown remains without an Unmet requirement; **No fit** means at least one requirement is Unmet. These verdicts apply to direct whole-engine adoption; extraction candidates have separate dispositions.

**Published** labels maker-stated figures; **Third-party estimate** identifies its estimator; **Not published** means searched and not found in the recorded sources. **Not researched** names a comparison not performed here and its existing review or future profile. Confidence is **High** only with independent corroboration of all design-defining claims and no design-defining Unknown; **Medium** permits maker-only evidence or Unknowns; **Low** means thin evidence. Source classes are **maker**, **vendor**, **independent**, and **community**. All external findings are as of 2026-10-22; source inspection establishes what code says, not observed runtime behavior.

## Need

Developers need an asynchronous Python harness they can embed alongside their own tools and user interface, with application-controlled authorization and per-call credentials. The library exposes owner-scoped session reads and changes, separates authoritative original content from the execution journal and model context, and keeps its public core/base install free of provider SDKs while allowing isolated optional adapters. These goals come from [the vision](product-vision.md), [PRD](../01-frame/prd.md), [concerns](../01-frame/concerns.md), ADR-003, ADR-011, and ADR-012.

The runtime selection is open. External work is valuable where it removes implementation effort without introducing competing history or execution authority or weakening the gates. The earlier evaluated snapshot treated long-session compaction as P1; CONTRACT-004/005 now specify source-addressed context projection and governed compaction independently of the runtime implementation. Application skills, memory, routing, and sandboxing remain outside the core under ADR-016/017 and CONTRACT-002.

## Required Capabilities

| # | Capability | Serves | Comes from | Settled by |
|---|------------|--------|------------|------------|
| C1 | Embeddable asynchronous Python execution with explicit configuration | In-process application integration and concurrent sessions | ADR-001/003; concerns | Documented async interfaces and configuration lifecycle without core environment reads |
| C2 | SDK-free public core/base and isolated optional provider dependencies; local/sovereign backends remain replaceable | Model-provider independence | Revised ADR-002/012; CONTRACT-001 | Pinned manifests/imports and packaging boundaries for the selected reusable subset, without a required hosted account |
| C3 | Fresh per-call identity, engine-owned gates, and invocation-bound approvals | Multi-user authorization and safe continuation | ADR-011/015/016; CONTRACT-001/002 | Documented credential lifetime, gate precedence, digest binding, and restart behavior |
| C4 | Authoritative session log and explicit uncertain effects after interruption | Durable recovery without duplicate unsafe effects | ADR-005/010/014/018/019/020; CONTRACT-002/003 | Commit, fencing, invocation identity, recovery, and store-capability semantics matching the contracts |
| C5 | Owner-scoped session inspection and reconnectable observation | User interfaces can list, read, follow, and recover pending interaction | ADR-004/015; CONTRACT-003 | Documented authorization, paging, replay, pending approvals, and slow-subscriber behavior |
| C6 | Replaceable bounded model context that preserves original entries | Long-session continuity and FR-35 | PRD FR-35; CONTRACT-002/003 | Documented separation of persisted history from model projection, intact tool groups, and explicit compaction records |

Nice to have: small dependency-free helpers and regression cases portable into existing Tether modules. These do not expand core scope.

## What It Is

Hermes Agent is NousResearch's Python agent application and harness, with CLI, gateway, API, and editor-facing entry points driving an `AIAgent` core. Its official architecture describes model routing, tool dispatch, context engines, memory providers, session storage, and optional integrations. [1] [2]

The inspected distribution declares an OpenAI SDK dependency on its newer runtime path; its documented model modes use OpenAI and Anthropic clients. These dependencies require explicit optional packaging and isolation under Tether's revised SDK policy; their presence alone no longer rejects reuse. [2] [3] The source is MIT-licensed. [4]

Independent installation counts, comparative distribution rank, a release-support window, and a fixed release cadence were not established by the inspected material. The owner's suggestion that Hermes is the most widely distributed Python harness remains unverified; repository popularity is not an installation census. See Sources for the search scope.

## Capability Alignment

### C1. Asynchronous embedding and configuration

The documented `chat()` and `run_conversation()` entry points are synchronous, tool concurrency uses a thread pool, and profile configuration resolves through a context override, `HERMES_HOME`, and a filesystem default. These are useful application choices but do not supply Tether's async-only, explicitly configured library surface. [2] [5]

**Settled by**: documented entry-point and configuration behavior in [2] [5]; no claim that every Hermes operation is synchronous.

**Status**: Unmet for direct whole-engine adoption.

### C2. Provider dependency isolation

The pinned manifest declares `openai`, and documented API modes use provider SDK clients. Those clients are eligible inside optional adapters under revised ADR-012. The reviewed product distribution does not establish a Tether-compatible SDK-free base and isolated optional runtime closure; small extracted components need their own import review. [2] [3]

**Settled by**: manifest and documented client types in [2] [3]; packaging of a selected Tether integration remains unqualified.

**Status**: Unknown for direct whole-engine adoption; SDK use alone is not a rejection reason.

### C3. Identity, gates, and approvals

Hermes documents client capability negotiation, outstanding approval requests, cancellation frames, and reconnect replay. [6] The inspected sources do not establish Tether's exact per-call credential lifetime, approval argument/tool digests, single-use approval behavior, or re-entry through every authorization gate. A profile or session scope alone is not proof of per-actor authorization. [5] [6] [10]

**Settled by**: partial interaction documentation in [6]; exact contract equivalence remains unverified and belongs in a targeted source review before any related extraction.

**Status**: Unknown; design-defining for whole-engine reuse.

### C4. Log authority and uncertain outcomes

Current documentation assigns canonical session writes to one gateway authority, records an owner epoch, durable input admissions, worker executions, and digest-checked receipts, and makes started admissions `unknown` after owner loss without automatic replay. Recovery intentionally excludes a partial runtime ledger to avoid unsafe adoption or replay. This is substantive recovery design and must not be described as mere transcript persistence. [5]

Admission recovery is not proof of Tether's per-tool intent fence, stable invocation id, atomic entry/summary/change commit, Postgres store conformance, or separate live/archive capabilities. Documentation establishes useful mechanisms, not equivalence across those guarantees. [5]

**Settled by**: authority and admission mechanisms documented in [5]; per-tool/store equivalence requires focused source analysis and process-kill conformance evidence if reuse is proposed.

**Status**: Unknown; design-defining.

### C5. Session inspection and reconnect

The TUI gateway documents saved session listing/history, separate active-session controls, several attached subscribers, replay of still-open questions, and an in-flight response projection. Unsupported client request capabilities fail promptly. [6] This does not establish Tether's exact owner-scoped cursor ordering, retention recovery, or bounded slow-subscriber behavior. [6]

**Settled by**: documented interface behaviors in [6]; exact CONTRACT-003 conformance remains unverified.

**Status**: Unknown; design-defining for whole-engine reuse.

### C6. Context projection and compaction

The pinned `ContextEngine.select_context()` explicitly allows request-only replacement while leaving persisted history untouched; lifecycle and usage hooks are separate. The default compressor and documentation describe pruning old results, keeping tool groups together, summaries, and reload markers for pruned skills. [7] [8] [12]

Hermes also documents compaction that soft-archives active rows and inserts a new active generation; micro-compaction can continue after a persistence failure and later recover through batch compaction. This storage design is not an implementation of Tether's immutable-entry FR-35 contract. [5] [9] Tether's current narrowing `before_request` hook cannot introduce an arbitrary summary or retrieved replacement; the new CONTRACT-004 now supplies that explicit surface; the evaluated narrowing hook itself remains unchanged.

**Settled by**: request-only selection is documented in [7]; whole-engine immutable-log/compaction-record equivalence is not established by [5] [9].

**Status**: Unknown for exact contract compliance; request-only separation is a supported design reference.

**Verdict**: No fit for direct whole-engine adoption under C1 (asynchronous embedding and explicit configuration). C2 is now Unknown under the revised optional-SDK policy; it no longer supplies a rejection. Targeted design and leaf-function reuse remain candidates under ADR-002/003/012.

## Technology and Operations

| Aspect | Finding and consequence | Source |
|--------|-------------------------|--------|
| Execution | One agent core serves several interfaces; documented thread-based calls need adaptation to Tether's async-only ports | [1] [2] |
| Configuration | Profile/home configuration and context-local scoping; Tether retains settings passed by its composition root | [5] [10] |
| Storage | SQLite canonical state, gateway writer ownership, input and worker ledgers; map design lessons to CONTRACT-003/005 execution journal/history/archive authorities; local JSONL is the reference default and Postgres the optional multiprocess profile | [5] |
| Tools | Registry combines process-global, profile, and session-scoped entries with locks and snapshots; do not transplant the singleton | [10] |
| Context cost | Stable prompt prefixes support caching; frequent micro-compaction invalidates prefixes and adds auxiliary model calls | [9] [11] |
| Security qualification | No security or tenant-isolation certification was established; this review did not perform a security audit | Search scope below |

### Design elements and reuse candidates

| Candidate | Disposition | Adaptation or boundary |
|-----------|-------------|------------------------|
| Context engine separation | Learn from | Keep original history immutable and use CONTRACT-004 projection with CONTRACT-005 refs; never widen `before_request` silently [7] |
| Old-result recovery stubs and skill reload markers | Learn from | Omitted content must remain retrievable through authorized session reads; missing archived content is explicit [8] [12] |
| Stable skills index plus change notes | Learn from | Deterministic prompt ordering and deliberate updates; skills remain application/provider features [11] |
| Admission unknowns and single authority | Learn from | Compare with invocation-level intents and store fencing; distinguish input, tool, and delivery idempotency [5] |
| Open requests and in-flight reconnect state | Learn from | Restore pending approval state and visible partial output; transport remains the consuming application's [6] |
| Generic Retry-After parsing and jitter | Possible extraction | Isolate generic functions, inject clock/randomness, validate finite bounded delays, keep waiting async, retain one retry owner [13] |
| Explicit omission-marker helpers | Possible extraction | Adapt product wording; fix marker-only overflow when the limit cannot fit the marker; never execute elided arguments [14] |
| Iteration counter | Reference only | Small thread-safe counter, but its refund and per-agent semantics are not Tether's max-step/policy budget contract [15] |
| Transcript sanitizer | Adapt scenarios, not whole module | Includes in-place repairs, name coercion, and argument repair; Tether should preserve evidence and refuse unsafe semantic changes [16] |
| Whole registry, compressor, state layer, agent facade | Do not import by default | Larger runtime/configuration closure and mismatched ownership; any exception needs a concrete deletion and contract-preservation case [1] [5] [7] [10] |

### Verification cases to adapt

These are proposed tests for authorized future slices, not execution results:

1. Interrupt immediately after a tool result, then send another user message; the adapter's provider-facing sequence remains valid without inventing a successful action. Include retry/error early-return paths. [17]
2. Move a compaction cut across each tool-group boundary; every result is kept with its call or included in the summarized region, never silently discarded by later cleanup. [18]
3. Ensure omission markers and credential redaction cannot become executable replacement arguments. Preserve evidence without adopting Hermes's credential-bearing transcript behavior; Tether forbids persisted credentials. [14] [19]
4. Disconnect with approval pending, reconnect, race answers from two clients, and verify Tether's digest/single-use/fresh-identity rules still hold. Hermes supplies interaction examples; Tether supplies the required semantics. [6]
5. Kill the authority after admission or an unsafe tool's intent and before result persistence; verify explicit uncertainty, fencing, and no automatic unsafe replay. Input and tool outcomes need separate assertions. [5]

## Pricing and Licensing

| Item | Finding | Label | Source |
|------|---------|-------|--------|
| Source reuse | MIT permission to use, modify, and distribute; retain the copyright and permission notice in copies or substantial portions | Published, inspected 2026-10-22 | [4] |
| Operating cost | Model, hosting, auxiliary compaction, and integration costs were not measured; no universal cost advantage is established | Not researched | No benchmark |

An extraction keeps upstream provenance and license notices alongside Tether's own licensing; it does not attribute upstream code solely to Tether's copyright holder. Inspect the selected files and transitive code before copying them. No extraction is authorized or performed by this artifact.

## Competitive Landscape

The bounded comparison is Hermes versus Tether's required behavior; the implementation beneath that boundary is open. Broader frameworks remain in the existing framework review; no new market ranking is inferred.

| Candidate | C1 | C2 | C3 | C4 | C5 | C6 | Profile |
|-----------|----|----|----|----|----|----|---------|
| Hermes whole engine | Unmet [2] [5] | Unknown [2] [3] | Unknown [6] | Unknown [5] | Unknown [6] | Unknown [7] [9] | This profile |
| Tether specified behavior | Required | Required | Required | Required | Required | CONTRACT-004/005 | [Architecture](../02-design/architecture.md), CONTRACT-001/002/003; delivery unverified here |

Tether owns the required semantics and pays to implement them. Hermes offers existing mechanisms and a mature set of failure scenarios, but the reviewed runtime does not satisfy the whole-engine adoption constraints. Named alternatives such as Pydantic AI, LangGraph, and Pi Durable are not rescored here; their evidence is in [the framework review](agent-harness-framework-review.md).

## Confidence

**Grade**: Medium. Findings use maker documentation and pinned maker source. Dependency and interface mismatches have direct source support; exact recovery, identity, and client conformance remain Unknown. No independent runtime evidence, installation ranking, compatibility test, or performance result was obtained. Current docs may be ahead of or behind the pinned source.

## Open Questions

| # | Question | Why it matters | Route |
|---|----------|----------------|-------|
| 1 | Which generic helper actually removes more maintenance than an independent small implementation? | Reuse has import, licensing, and update costs | Extraction review with dependency closure and focused tests, only when the owning slice needs it |
| 2 | How should a context projection introduce summaries and retrieval while preserving the immutable log and gate inputs? | Current `before_request` cannot express arbitrary replacement | CONTRACT-004 and ADR-021 now specify it; TP-001 qualifies implementation |
| 3 | Which Hermes admission/worker guarantees are per-tool and backed by process-kill tests at the pinned source? | Avoid equating admission recovery with effect safety | Targeted source review if a recovery mechanism is selected |
| 4 | What are the context fidelity, latency, and cache costs on fabricated Tether workloads? | Neither smaller requests nor continuous summaries establish lower total cost | FR-35 qualification under TP-001 with Tether's workloads; no deadline or budget invented |
| 5 | Can any specific optional integration satisfy fresh per-call identity with isolated optional dependencies and explicit configuration? | Identity and dependency policy are hard constraints | Adapter-specific source review before proposing reuse |

## Sources

All sources below are **maker**, NousResearch, accessed 2026-10-22. Documentation pages show no fixed release/publication date in the retrieved content and describe the then-current application. Source links are pinned to the inspected commit, not a claimed package release; no independent corroboration was collected.

1. [Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture).
2. [Agent loop internals](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop).
3. [Pinned pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/pyproject.toml).
4. [Pinned MIT license](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/LICENSE).
5. [Session storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage).
6. [Programmatic integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration).
7. [Context engine source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/context_engine.py).
8. [Context compression and caching](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching).
9. [Micro-compaction](https://hermes-agent.nousresearch.com/docs/developer-guide/micro-compaction).
10. [Tool registry source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tools/registry.py).
11. [Skills index delta source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/skills_index_delta.py).
12. [Context tail reduction source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/context_compressor_tail.py).
13. [Retry utilities source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/retry_utils.py).
14. [Compression marker source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/compression_marker.py).
15. [Iteration budget source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/iteration_budget.py).
16. [Message sanitization source](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/message_sanitization.py).
17. [Interrupted tool-sequence tests](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tests/agent/test_close_interrupted_tool_sequence.py).
18. [Compaction boundary tests](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tests/agent/test_compressor_tail_cut_tool_pair_floor.py).
19. [Tool-argument redaction regression](https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tests/agent/test_tool_call_arg_no_redaction.py).

**Searched**: web query `Hermes agent NousResearch github python agent harness`; official repository README, architecture, loop, integration, storage, compression, micro-compaction, and prompt-assembly pages; selected pinned source and agent/test directory listings. No independent installation census or explicit support-window/release-cadence policy was established in that scope. Security certification was not established by those pages; this is not an exhaustive certification search. Existing Tether discovery Markdown was searched for `hermes` before authoring and had no match. Broader framework rescoring, cost measurements, and an executable dependency audit were not performed.

[1]: https://hermes-agent.nousresearch.com/docs/developer-guide/architecture
[2]: https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop
[3]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/pyproject.toml
[4]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/LICENSE
[5]: https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage
[6]: https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
[7]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/context_engine.py
[8]: https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching
[9]: https://hermes-agent.nousresearch.com/docs/developer-guide/micro-compaction
[10]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tools/registry.py
[11]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/skills_index_delta.py
[12]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/context_compressor_tail.py
[13]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/retry_utils.py
[14]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/compression_marker.py
[15]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/iteration_budget.py
[16]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/agent/message_sanitization.py
[17]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tests/agent/test_close_interrupted_tool_sequence.py
[18]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tests/agent/test_compressor_tail_cut_tool_pair_floor.py
[19]: https://github.com/NousResearch/hermes-agent/blob/92663ae8054ae81f207f46d83512d3f879dc698d/tests/agent/test_tool_call_arg_no_redaction.py
