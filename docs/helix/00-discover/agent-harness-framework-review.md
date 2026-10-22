---
ddx:
  id: competitive-analysis-agent-harness-review
  type: competitive-analysis
  activity: discover
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-22
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
    - id: ADR-005
      kind: informed_by
    - id: ADR-010
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-012
      kind: informed_by
    - id: ADR-013
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

# Agent harness framework and design review

## Finding and scope

**Recommendation: retain Tether's own harness and model port, with no agent-framework or foundation-model-provider SDK dependency.** The design is defensible for the scoped product: one asynchronous Python loop, application-owned policy and identity, durable sessions, and an in-process client API. The hard work is execution semantics around that loop. Owning those semantics is justified only if Tether implements and verifies them explicitly.

This is discover-phase research and a design review, requested on 2026-10-22. Recommendations below do not amend requirements, accept ADRs, or claim implementation. “Framework-free” means no agent-framework dependency; the selected stack already includes Pydantic, AnyIO, and HTTPX. Literal zero third-party dependencies would be a separate stack decision.

A **harness** runs the model/tool loop and manages its context, state, controls, and diagnostics. A **checkpoint** records execution state. **Recovery** reconciles interrupted work; **resume** continues it. **Idempotency** means repeating an operation has the same effect as executing it once. **Compaction** reduces model input while retaining useful context. **MCP** is the Model Context Protocol for connecting tools and data sources. **OTel** is OpenTelemetry. A software development kit (**SDK**) can support several models while still being maintained by a model provider and depending on its packages.

### Evidence and limits

Primary project inputs: [vision](product-vision.md), [PRD](../01-frame/prd.md), [concerns](../01-frame/concerns.md), [architecture](../02-design/architecture.md), ADR-002/003/005/010/012, CONTRACT-001/002/003, the existing [competitive analysis](competitive-analysis.md), and the library design/build plan. Project sources are working intent, not proof of delivered behavior.

External evidence is official documentation and selected public source/manifest inspection, accessed 2026-10-22; the linked source register records scope. No framework was installed, benchmarked, or exercised. Documentation establishes advertised mechanisms, not conformance with Tether's contracts. Most pages track current releases rather than a fixed version; no package-version compatibility or transitive-dependency closure is certified. LangGraph's retrieved reference identified v1.2.16; CrewAI's Flows URL resolved to v1.15.28; Pydantic AI Harness describes a 0.x API policy. Recheck exact versions and extras before an adoption spike. Market share, maintenance cost, performance, and engineering duration are unmeasured.

## Finalized language and runtime decision

[ADR-003](../02-design/adr/ADR-003-tether-is-a-library-of-ports-and-providers-and-builds-its-own-loop.md) records the accepted direction: retain the Python harness, use the consuming application's existing server for its browser interface, and do not adopt or wrap AgentKit. Python embedding and human-facing UI are equally important. Acceptance selects the implementation path; it is not delivery or conformance evidence. The scoped contract comparison below records the reviewed snapshot; concurrent design revisions may address individual findings and must be checked before treating them as current gaps.

| Choice factor | Python | TypeScript | Decision implication |
|---|---|---|---|
| Existing Python tools and live application objects | Direct calls without a new tool-service boundary | Requires a process/service boundary for Python execution | Retain Python while embedding is a real requirement |
| Browser/server integration | Maintain a cross-language wire contract in the existing application | Share more types, schemas and packages | Accept this Python cost; do not create a second harness |
| Dynamic tools | Runtime annotations and introspection support schema derivation | Dynamic registration with runtime schemas is also possible | Dynamic behavior alone does not select the language |
| Databricks deployment | Apps supports Python; AgentKit is Python | Apps supports Node.js; AppKit provides TypeScript agents and React integration | Databricks does not force a Python harness [TS1] [TS2] |
| Reuse of agent ecosystem work | Python frameworks already cover many harness capabilities | Pi Durable, Mastra and browser tooling offer substantial reuse | Assess actual removable implementation, dependency policy and state ownership; ecosystem activity is not a replacement plan |

AppKit's documented agents run inside the app and expose React streaming integration. That is a genuine TypeScript alternative. Its documented model identity runs as the app service principal, so browser convenience does not establish Tether's per-user model-call behavior. Genie Code CLI is a local coding application with Databricks CLI, skills and MCP integration; its public documentation does not establish the implementation language or an embeddable generic runtime. Neither product makes the harness language equivalent to the language of code an agent can execute. [TS2] [TS3]

The existing Apps deployment removes deployment/scaffolding as a reason to adopt AgentKit. Reconsider adoption only if a specific replacement deletes substantial machinery, preserves identity/recovery and portability, and reduces total complexity. Do not introduce a permanent compatibility wrapper or schedule a framework bake-off as a hedge. H1–H3 remain design/verification questions to resolve against current contracts; they are not proof that an owned harness is safer than a framework.

## Related coordinated discovery

- [Hermes Agent component profile](component-profile-hermes-agent.md) evaluates whole-runtime fit and targeted design/source reuse against the same Python, identity, SDK and recovery constraints. Its inspected engine conflicts with async/configuration and SDK requirements; context projection, reconnect state, uncertain admissions and small utilities remain learning candidates. No source extraction is authorized or implemented.
- [Databricks integration assessment](databricks-integration-assessment.md) maps identity, gateway, governed tools, Lakebase, archives/artifacts, telemetry/evaluation and deployment ownership. It preserves ADR-003 and identifies package/SDK/identity/retention/support choices as proposals; it does not expand ADR-013 or certify workspace behavior.

## Market Landscape

The relevant alternatives span different layers: low-level orchestration (LangGraph), an agent runner (OpenAI Agents SDK), an embedded complete agent runtime (Claude Agent SDK), composable Python harnesses (Pydantic AI Harness and Strands), and durable workflow engines. Treating them as interchangeable obscures the integration cost.

The owner's preference against foundation-model-provider libraries is an architectural constraint in ADR-012. It does not establish that their designs are poor. LangGraph deserves an independent assessment from prior LangChain experience: its orchestration API can be used without adopting the higher-level LangChain agent API, but its current package manifest still includes `langchain-core`. [L1] [L4]

## Competitive Forces

| Force | Assessment and evidence | Implication for Tether |
|---|---|---|
| Direct alternatives | Strong functional overlap: persistence, approvals, context management, and typed tools appear in several official products below | Having sessions or a tool loop is not differentiation |
| Substitutes | An owned loop plus storage, or an agent library plus a durable engine, can meet many of the same needs | Compare the whole operational and semantic burden |
| Entry barriers | A demonstration loop is straightforward; recovery, authorization, protocol compatibility, and usable clients add substantial work (review judgment) | Small source size alone does not establish simplicity |
| Switching cost | Depends on persisted formats, runtime-owned state, tools, telemetry, and provider types (review judgment) | Keep external types and persistence formats behind owned boundaries |

## Competitor Profiles

### LangGraph

**Provides:** an explicit state/graph execution model, checkpoints, thread state/history, replay and state branching, interrupts for external input, and streaming. It mixes deterministic steps and model-directed decisions. This is a substantial orchestration runtime, useful when an application needs branching workflows or multiple coordinated agents. Its low-level API does not require using LangChain's high-level agent loop. [L1]

**Important semantics:** checkpointers persist graph state at super-step boundaries. `sync` durability persists before proceeding, `async` overlaps persistence with execution and has a crash window, and `exit` saves at graph exit. A persistent backend is required for restart survival. Async Python persistence calls and synchronous durability are compatible concepts: the former avoids blocking the event loop; the latter specifies when the next step may start. [L2]

An interrupted node starts again from its beginning on resume. Work before the interrupt can repeat; applications must place effects carefully or make them idempotent. This is a concrete reason to study its interrupt rules even when not adopting it. [L3]

**What Tether needs:** durable transition boundaries, resumable approvals, deliberate replay rules, an inspectable execution frontier, and a stable event model. It does not currently need graph reducers, subgraphs, workflow time travel, or a graph definition language. A custom checkpointer could integrate Tether storage, so “two state owners” is an integration risk rather than an unavoidable framework defect. The implementation would need one authoritative persisted frontier and atomic projections.

**Adoption cost and verdict:** translating gates, state, streaming, and recovery to graph semantics is a large part of Tether's product. Current source declares `langchain-core`, checkpoint, SDK, and prebuilt packages; using the graph API alone does not mean dependency independence. No installed dependency tree was audited. Retain as the main orchestration reference and reconsider if graphs enter the product's scope. [L4]

### OpenAI Agents SDK

**Provides:** a runner over agents and tools, typed function-tool schema generation, handoffs/agents as tools, MCP integration, streamed execution, structured output, sessions, approvals, usage, and tracing. This removes substantial integration work for an application willing to accept the runner's conventions. Handoffs and voice/realtime features are outside Tether's initial scope. [O1]

**Model independence has two meanings:** the SDK supports non-OpenAI models through compatible endpoints, per-run model providers, and concrete model objects. It should not be dismissed as usable only with OpenAI models. Adopting OpenAI's agent library nevertheless conflicts with Tether's dependency policy; model interchangeability does not resolve that policy. [O2]

**Persistence:** the session protocol manages history with `get_items`, `add_items`, `pop_item`, and `clear_session`, with several storage implementations. Current documentation permits passing run context into custom session methods for tenant routing/authorization. These are useful integration seams. They do not by themselves prove Tether's atomic entry/summary/change commit, per-session lease, or reconnectable follower guarantees. [O3]

**Approvals:** a paused run can be serialized and resumed; the application must retain and authorize that state. Documentation recommends version markers for long-lived pending work. An approval pause is an explicit save point, not evidence that arbitrary process death inside a tool is safely replayable. [O4]

**Guardrails and telemetry:** initial-input checks may run concurrently by default, while blocking mode completes them before execution. Agent-level initial/final checks and per-function-tool checks cover different boundaries. Tether's authorization needs synchronous precedence in the logical pipeline before each protected effect. [O5] Tracing is on by default, normally exports to OpenAI, and includes sensitive generation/tool data by default; export and content capture are configurable. Tether should retain metadata-only telemetry and an explicit composition root. [O6]

**Verdict:** useful runner, approval, and lifecycle reference; reject as a Tether dependency under current policy. The capabilities worth reproducing are typed dispatch, precise interruption state, versioned approvals, usage, and consistent stream/final outcomes. Its developer convenience does not replace application authorization or unknown-effect handling.

### Claude Agent SDK

**Provides:** Python and TypeScript access to the Claude Code binary and its agent loop, built-in file/command tools, context management, MCP, hooks, permissions, subagents, and skills. It is closer to embedding a complete capable agent than adopting a small generic model loop. That is valuable for shipping a Claude Code-powered product quickly. It is excessive for Tether's current product boundary and conflicts with provider independence. [C1]

**State and controls:** sessions persist conversation history to disk and support continue, resume, and fork. Session continuation restores conversational context; it does not establish an exactly-once external-effect guarantee. [C2] Permission modes, rules, hooks, and runtime callbacks control tool use. Tether should learn from the explicit precedence between those controls while retaining its narrow-only extension policy. [C3]

**What Tether needs:** bounded context, inspectable session state, durable pending input, usage accounting, and explicit permissions. Built-in coding tools, project configuration conventions, autonomous subagents, and Claude Code workspace behavior belong to an application or optional integration.

**Verdict:** excellent reference for what a finished agent experience needs, poor foundation for a provider-independent harness library. A different Claude hosting route changes deployment, not the fact that this SDK embeds Claude's runtime. No claim is made here about arbitrary models through undocumented gateways or third-party runtime compatibility.

### Additional alternatives

These are screened for architectural fit, not ranked by popularity. Model-neutral positioning never certifies absence of provider SDKs in a particular installation.

| Alternative | Documented capability relevant to Tether | Assessment and lesson |
|---|---|---|
| **Pydantic AI + Pydantic AI Harness** | Typed agent loop plus composable capabilities for context, tools, controls, workspaces, and persistence [P1] | Strongest additional Python adoption counterfactual. It overlaps more of Tether than the earlier survey recognized. Evaluate it seriously if policy changes; do not assume it supplies Tether's exact session API |
| Pydantic AI Step Persistence | Step events, effect records, continuable snapshots; interrupted snapshots are distinct from settled ones; recovery inspection does not infer replay safety [P2] | Direct reference for execution-frontier and unknown-effect design. Full-history snapshot growth and retention need attention |
| Pydantic AI durable integrations | Separately distinguishes storage from execution recovery and integrates multiple engines, including Temporal, DBOS, Prefect, and Restate [P3] | A framework can own the loop while a runtime journals work. External infrastructure varies; “all require a separate engine service” is too broad |
| **Strands Agents / Harness SDK** | Session management persists messages/state and interrupt state; documented model is one live writer per conversation [S1] | Close Python harness comparison. Borrow explicit concurrency constraints; verify any desired multiprocess fencing independently. No zero-provider-SDK claim established |
| **Deep Agents** | LangChain/LangGraph-based harness with filesystem/context facilities, subagents, memory, and optional planning/skills [D1] | More directly comparable to a complete harness than bare LangGraph. Adds more runtime choices than Tether needs; useful context-management reference |
| **Microsoft Agent Framework** | Agents, sessions, context providers, middleware, workflows, harness features; official successor to AutoGen and Semantic Kernel [M1] | Consider the successor when surveying Microsoft options. Broad orchestration/platform fit; exact gates and recovery still require qualification |
| **Google ADK** (Agent Development Kit) | Agent/runtime components, workflows, model integrations, and session-related facilities [G1] | Framework breadth worth studying; provider-owned foundation conflicts with present preference. Multi-model support does not mean dependency independence |
| **CrewAI** | Role/task/crew abstraction and event-driven Flows with persisted state [R1] | Useful for business workflows; adopting its application vocabulary is a scope expansion. A persisted flow is insufficient evidence of safe arbitrary-effect recovery |
| **Agno / AgentOS** | Separates SDK, service runtime, and control plane; advertises session inspection, APIs, storage, and durable execution [A1] | Counterexample to blanket claims that frameworks lack session management or UI surfaces. Service/platform ownership exceeds Tether's embedded-library boundary |
| **LlamaIndex Workflows / AgentWorkflow** | Typed events connect execution steps; agent, streaming, and human-input facilities in its documentation [I1] | Relevant when retrieval and data workflows dominate. Study typed event composition without taking on its broader application toolkit |
| **smolagents** | Small code/tool agent approach and documented executor/sandbox choices [H1] | Useful simplicity and isolation reference. Generated-code execution adds a different security boundary; an in-process executor is not sufficient containment |
| **Mastra** | TypeScript workflows suspend to stored snapshots and resume across restarts [T1] | Strong application-level counterfactual if Python is negotiable. Not a direct Python dependency choice |
| **Vercel AI SDK** | TypeScript agent abstraction around model/tool loops and loop control [V1] | Good API ergonomics and client-boundary reference. Python embedding would require a language/runtime boundary |
| **Pi Durable** | TypeScript checkpointed tasks and per-tool replay behavior; README explicitly notes that replay-safe external tools still need idempotency [B1] | Close design reference, with a language mismatch. Hooks that throw are reported/skipped except `beforeTool`, which blocks; Tether should retain its stricter control-hook failure semantics |
| **Temporal / DBOS / Restate / Prefect** | Durable workflow substrates; Temporal documents why activities need idempotency [E1] [P3] | Adjacent infrastructure rather than interchangeable agent frameworks. Reconsider for distributed workers, durable timers, or stronger workflow scheduling needs |

AutoGen and Semantic Kernel are covered through their official successor. This is a deliberately bounded survey, not an exhaustive directory. Retrieval toolkits, autonomous coding applications, and realtime voice runtimes are secondary to the present loop/session problem; their omission from the adoption shortlist is not a claim that they lack useful harness designs.

## Feature Comparison

`Documented` means official sources describe a mechanism. `Custom` means Tether-specific integration or evidence remains necessary. `Planned` describes Tether's governing intent. `Not established` is not an absence claim.

| Capability | Tether | LangGraph | OpenAI Agents SDK | Claude Agent SDK | Pydantic AI + Harness |
|---|---|---|---|---|---|
| Own a small async Python loop | Planned | Graph runtime owns transitions | Runner owns transitions | Binary owns loop | Agent loop owns transitions |
| Typed tools and validation | Planned | Custom nodes / ecosystem tools | Documented | Custom tools / MCP | Documented |
| Persistent conversation state | Planned append log | Documented checkpoints | Documented sessions | Documented transcripts | Documented persistence |
| Durable pending human input | Planned | Documented interrupts | Documented serialized run state | Documented controls/session support; arbitrary crash guarantee not established | Documented deferred-tool/control capabilities; exact Tether semantics custom |
| Recovery at arbitrary failure points | Planned, ADR-010 | Checkpointer/mode plus replay design | Not established by session/approval docs | Not established by session continuation | Snapshot continuation or separate durable engine; effect safety remains explicit |
| Owner-scoped summary paging + persisted reconnectable feed | Planned | Custom Tether contract; runtime streaming exists | Custom Tether contract | Custom Tether contract; session APIs exist | Custom Tether contract; UI/event integrations exist |
| Tool replay safety | Planned safe/unsafe | Application effect discipline | Application effect discipline | Not established | Effect records/recovery inspection; application policy |
| No framework/provider SDK dependency | Planned framework-free stack | LangChain-family dependencies; provider SDK closure unverified | Conflicts with policy | Conflicts with policy | Default compatible-provider path conflicts per SPIKE-002; other exact extras unverified |
| Metadata-only telemetry | Planned default | Custom configuration/adapter | Must change default capture/export | Verify/configure integration | Verify/configure integration |

The matrix does not compare speed: no comparable workload was measured. Run streams, durable feeds for reconnecting readers, session catalogs, and provider request streams are different capabilities.

## Counterfactual: best practices without a framework

The recurring advice in primary engineering sources is to begin with simple, composable control flow, make tools and context deliberate, persist useful progress, measure real outcomes, and add orchestration when the workload demonstrates its value. The 2024 Anthropic article explicitly warns about abstraction overhead; its page now flags that tooling has changed, so its architectural advice should not be treated as a current product survey. [BP1]

| Practice and evidence | Owned harness approach for Tether (review proposal) | What a framework buys; what remains ours |
|---|---|---|
| Simple control flow and understandable tools [BP1] | Explicit transition table; one authoritative loop and gate pipeline; predictable tool schemas and typed failures | Boilerplate reduction and integrations; application control semantics remain |
| Recoverable progress across context windows [BP2] | Preserve authoritative entries plus compact progress references; treat task plans/checklists as application data | Session/context facilities; application-specific progress and completion checks still needed |
| Context as a bounded resource [BP3] | Separate retained history from model input; budget tool output, retrieval, and history; preserve pending calls and decisions across compaction | Built-in trimming/summarization; correctness of retained meaning remains workload-specific |
| Evaluate outcomes and trajectories [BP4] | Keep deterministic runtime replay tests and separate stochastic task evals; inspect failure transcripts using fabricated inputs | Eval/tracing integrations; choice of tasks and success criteria remains ours |
| Effects may finish before the runtime records success [E1] | Carry stable operation identifiers; reconcile unknown outcomes; never equate entry deduplication with effect deduplication | Journaling/retry machinery; target-system idempotency remains ours |
| Restarted nodes can repeat work [L3] | Commit decisions/frontiers before dispatch; distinguish accepted calls from actual starts; define batch ordering | Checkpoint/interrupt machinery; choosing safe replay boundaries remains ours |
| Permission decisions precede protected actions [O5] [C3] | Blocking, fail-closed authorization on every call; repeat gates on resume | Tool hooks/approval UX; precedence, identity, and policy validity remain ours |
| Isolation is a runtime boundary [H1] | Treat extensions as trusted Python code; run hostile tools/code outside the process with scoped credentials and resources | Sandbox integrations; containment, secrets, filesystem, and network policy remain application responsibilities |
| Trace only appropriate data [O6] | Allow-list identifiers and counts; ensure error reasons and IDs cannot smuggle content | Trace plumbing; safe capture/export defaults still need deliberate configuration |

These are engineering recommendations, not universal experimental proof. The harness articles predominantly describe coding workloads; Tether should test transfer to its own fabricated tool workflows. Model-provider authors' design observations can be useful without adopting their SDKs.

### The strongest case for adopting instead

If the goal were to deliver an agent application quickly, accepting a framework's execution/state conventions would save real work: tool schema plumbing, streamed event translation, pending-input mechanics, context policies, provider integrations, and diagnostics. LangGraph is the strongest option when graph execution is the requirement. Pydantic AI plus Harness is the strongest additional Python option when typed capabilities and a managed loop are acceptable. Claude's SDK is compelling when embedding Claude Code is the product.

Tether is selling the execution and session boundary itself. Wrapping another owner of that boundary risks leaving almost all of Tether's distinctive work intact while adding translation, persisted-version compatibility, and dependency upkeep. That is a product-fit inference, not a finding that those frameworks are inherently messy. One authoritative state owner is possible with any adoption, but it must be chosen explicitly.

## Design review findings

Severity applies to design readiness. The H1–H3/M1–M4 observations below describe the initial review snapshot; use the current disposition first. No implementation defect or passing runtime test is asserted.

### Current disposition after coordinated design reconciliation

The newer ADR-014–020 and revised CONTRACT-002/003 address much of the initial review. Accepted ADR-003 settles Python, the owned harness, the existing Apps server, and no AgentKit wrapper. This review closes the research comparison; it does not turn design intent into delivered guarantees. [SD-001](../02-design/solution-designs/SD-001-tether-library-design-and-build-plan.md#framework-review-verification) owns the remaining failure-boundary verification and unresolved design details.

| Finding | Current disposition | Evidence and remaining work |
|---|---|---|
| H1, unknown external effects | **INCOMPLETE** | ADR-014 and CONTRACT-002 sections 8–10 define stable invocation IDs, pre-effect intents, explicit unknowns, and P1 reconcile/resolve. They do not prevent a fresh model call from repeating the same business action; error/timeout rows still say the model may retry. Define that boundary before slice 9 acceptance; no exactly-once effect claim is justified |
| H2, synchronous cancellation and stale runners | **UNDERSPECIFIED** | ADR-014 explicitly acknowledges thread abandonment; ADR-001 now states the same limit. CONTRACT-003 fences log writes, not external services. Bounded outstanding worker capacity and replacement-work behavior remain to specify and qualify |
| H3, approvals and resume | **ALIGNED in design** | ADR-015 binds an approval to invocation, arguments, tool definition and actor, with lifecycle/races/rechecks; CONTRACT-002 now carries actor credentials plus APPROVE/RESUME and Engine.resume; CONTRACT-003 uses client(actor), confirm(invocation_id), and resume. Version/migration and executed race/crash tests remain qualification work |
| M1, execution limits | **PARTIAL; application-owned thresholds selected** | CONTRACT-002 supplies tool timeouts, hook limits, UsageSoFar and policy budget denials; the core enforces max_steps. Output/request capacity and missing-usage policy remain application/adapter design work. This review does not promote universal token or cost limits into core requirements |
| M2, context bounds | **DEFERRED by current scope** | PRD FR-35 keeps compaction P1; CONTRACT-002 exposes a narrowing before_request hook and reserves a compaction entry. Arbitrary summary/retrieval replacement needs an explicit P1 context contract rather than silently widening that hook. A minimal context-bound/overflow proposal remains advisory until selected; long-session support must stay qualified to the configured model/input policy |
| M3, failure-boundary evidence | **ALLOCATED; NOT RUN** | SD-001 now names dispatch/effect/commit crash points, approval races, uncertain-effect repeats, synchronous cancellation, and replay-versus-task-eval checks. Log-prefix tests alone are insufficient |
| M4, stale competitive/dependency claims | **ALIGNED for this research** | Current comparisons distinguish persistence, recovery and external effects; HTTPX is the default in SD-001; ADR-003 rejects inevitable dual ownership as a framework property. Optional Pydantic adapters are P2 and need a separately compliant dependency closure; none is certified by this review |

The current storage direction is in-memory plus Postgres, with optional embedded Postgres and optional SQLite (ADR-019/020). Store declarations qualify durability/fencing; older references to SQLite as the primary durable store describe earlier intent. Reconciliation/manual recovery remain P1 under FR-44. The review does not silently promote them or certify automatic repeat prevention for an unresolved effect.

### Initial review observations


### H1 — Unknown effects can be duplicated by a new model decision

**Location:** ADR-010, Decision/Consequences; CONTRACT-002, Error Semantics (`tool_error`, “The model may retry”). The planner correctly avoids automatically rerunning an unsafe tool. Returning `interrupted` to the model still allows it to issue a new call with a new ID for the same operation. An ordinary tool exception can also occur after a remote effect completed. Framework checkpointing does not close this gap; Temporal explicitly documents it. [E1]

**Proposal:** distinguish failed-before-effect from outcome-unknown. Supply a stable operation key to side-effecting adapters and require reconciliation or renewed explicit approval for unresolved effects. An unsafe call must not become implicitly retryable merely because the model rephrases it. If the target cannot deduplicate or reconcile, surface the uncertainty to the application.

**Verification:** effect completes, then process dies before append; resume and a newly generated equivalent call cannot silently apply it twice. Include a tool that raises after its effect. Specify the intended guarantee as exactly-once committed records and explicit uncertain effects, not globally exactly-once execution.

### H2 — Worker-thread cancellation and lease expiry do not stop effects

**Location:** CONTRACT-002 section 4 (plain function offload), Error Semantics (cancel/lost lease); CONTRACT-003 section 3 (lease). A cancelled coroutine cannot forcibly stop arbitrary synchronous code already running in a thread. AnyIO documents both its default shielded wait and opt-in abandonment, which leaves the thread running. [PY1] A lease prevents stale log writes; it cannot itself fence a remote system's side effect.

**Proposal:** define whether cancellation waits for a synchronous tool, cooperatively cancels it, or abandons an outcome as unknown. Keep session exclusivity consistent with that choice. For stronger containment, use an isolated process/executor and target-system fencing or idempotency; a process kill alone still cannot roll back a remote effect.

**Verification:** cancel or expire the lease while a real synchronous tool is blocked at a controlled barrier, start a contender, then release the old tool. No silently overlapping unsafe operation or falsely “stopped” outcome is permitted. This is a high-priority qualification of the async-only promise.

### H3 — Approval and resume compatibility need a complete durable boundary

**Location:** CONTRACT-002 `PendingConfirmation`, pinned definitions and gate sequence; CONTRACT-003 `confirm`; ADR-010 planner and repeated gates. The design includes definition hashes and spec versions, which is a good start. It leaves cross-version approval validity and the explicit resume surface incomplete: the displayed `Action` enum lacks `RESUME`, and the client surface does not yet declare the ADR's resume method.

**Proposal:** bind a pending approval to the exact call arguments, relevant definition/spec versions, actor/session, and single-use state. Specify reauthorization, expiry, mismatches, and atomic consumption after restart. Use a versioned safe serialization format. OpenAI's pending-task version guidance is a useful reference. [O4]

**Verification:** duplicate/racing approvals, stale definitions, revoked rights, changed arguments, and restart between recording approval and dispatch. Every successful dispatch is attributable to current authorization and the approved action.

### M1 — Limits need to bound more than the step count

**Location:** CONTRACT-001 request timeout; CONTRACT-002 `AgentSpec.max_steps`, gate/tool pipeline; concerns `resilience`. A step limit does not bound one hung tool, huge output, retries, total duration, or spending. Proposed budgets should accommodate local models that do not report token usage.

**Proposal:** explicit tool/turn deadlines, output/request byte limits, optional usage/cost budgets, and one retry owner with transient/permanent/unknown classification. Treat budget exhaustion as a typed stop; define behavior when usage or pricing is unknown. Reserve output capacity before sending a model request.

**Verification:** hung tools/hooks, oversized fragmented arguments/output, omitted usage, retry exhaustion, and cancellation during backoff. Do not duplicate retries in HTTPX, the adapter, and the engine.

### M2 — Bounded context is needed before useful long sessions

**Location:** CONTRACT-002 Non-Normative Notes (compaction out of v1, `before_request` hook); PRD FEAT-010/P1. The hook is an extension seam, not a policy for bounding the default request. Long logs must remain pageable without all of them becoming model input. Context-management research supports selective retrieval and explicit compacted state. [BP3]

**Proposal:** retain P1 for sophisticated compaction if desired, but provide a safe initial context bound and typed overflow outcome. Keep the append log intact; any summary is a derived, versioned view with provenance. Preserve system instructions, unresolved effects, pending approvals, and tool-call/result pairing. Summaries cannot grant authority.

**Verification:** long conversation on a small-context local model; large tool result; compaction with a pending batch; changed model/context limit; repeated summaries. Success is bounded input with intact safety state, not only shorter text.

### M3 — Crash and replay tests need execution-boundary coverage

**Location:** ADR-010 prefix test; CONTRACT-001 cassettes; CONTRACT-003 conformance suite. Log prefixes cannot represent all failures after a side effect and before its result is committed. Likewise, cassette determinism proves runtime handling of fixed responses, not agent task success.

**Proposal:** extend proposed tests to injected process death at dispatch, external effect, result normalization, append, and notification; include mixed safe/unsafe tool batches. Add separate fabricated task evals across supported local-server profiles, with state-based outcome grading, limits, and repeated trials. [BP4]

**Verification:** entries, summary, and feed agree after recovery; no repeated unsafe effects; replay detects request drift; streamed and final results agree; live model evals report their exact server/model configuration rather than universal quality claims.

### M4 — Current competitive and dependency claims need qualification

**Location:** existing competitive analysis; ADR-002 optional adapter wording; architecture adapter table; solution/build plan slices 1 and 9. Current Pydantic documentation includes Step Persistence, effect records, UI integrations, and its Harness. The claim that Pydantic AI only stores history in application code or always needs a separate service for persistence is incomplete. [P1] [P2] [P3] LangGraph exposes checkpoint listing and several streaming forms; custom checkpoint enumeration is not automatically an owner-authorized session catalog. [L2] [L5]

The optional adapter wording about not directly importing a banned SDK does not prove that a selected Pydantic provider's transitive dependencies comply with ADR-012. SPIKE-002 already identifies the compatible path's `openai` dependency. The build plan still centers its first model slice on Pydantic AI despite ADR-002's new HTTPX default.

**Proposal:** use this dated review to qualify the old survey, audit exact extras before claiming optional-adapter compliance, and reconcile the build plan against current model/recovery ADRs before implementation. The framework-free recommendation stands on fit and control, not an unsupported claim that no competitor has durable sessions or UI APIs.

## AgentKit contract comparison

**Recommendation: keep Tether and do not add an AgentKit integration under the current requirements.** The operator clarified that the application is already deployed on Databricks Apps and that adoption should replace duplicated machinery, not add another execution layer. AgentKit's session store and recovery scheduler do not replace the current contracts. Hosting/scaffolding provides no new deployment capability for an already deployed application. A replacement decision must identify the existing code that can be deleted and the product requirements the replacement preserves; differences in naming, cursors or queue policy alone do not justify an owned runtime.

The inspected Tether checkout contains the proposed library design and probes, not the deployed application's server implementation. This is a contract-fit assessment, not a code-level duplication audit of that deployment. The operator's deployment statement is accepted; a design-stage README does not contradict deployment of the existing application. No claim is made that deployed Tether already implements all the contracts below.

### Scope and evidence

This comparison incorporates the findings above from the “Evaluate agent harness frameworks” investigation, especially H1–H3 and M1–M4. It compares CONTRACT-001/002/003, PRD FR-14/19/20/26/34/37 and NFR-2/4/6/8, and ADR-010/011/012/013 with Databricks AgentKit. It is research, not approval to change governing requirements or add a dependency.

Databricks Apps is the intended deployment target. Agent Bricks is the vendor's agent-development platform and CLI. AgentKit is its Python library, distributed in `databricks-agentbricks`; `DurableAgentServer` is a FastAPI server around an application-supplied agent loop. Choosing Apps does not require AgentKit. [AK1] [AK2]

Published source inspected: `databricks-agentbricks==0.6.0`, released 2026-10-16, Python >=3.10. Its source archive SHA-256 is `a91813ef5bd68d6b604199a37861e43090fbd86c0cbfa07842533f698774f28e`; the downloaded archive matched it. Relevant files under `src/databricks_agentkit/` are `runtime/app.py`, `runtime/auth.py`, `runtime/execution.py`, `runtime/runtime.py`, `runtime/store.py`, `runtime/types.py`, `runtime/tracing.py`, `runtime/durability/{store,execution,heartbeat,recovery,lakebase_runtime_store}.py`, and `session_store.py`. Source was read without installing or executing the package. Mutable documentation was checked on 2026-10-22. CLI/APIs are experimental/preview and managed sessions are Beta. No workspace, performance, or runtime conformance was tested. [AK1] [AK2] [AK3]

### Feature comparison against the contracts

`Reuse` means a useful mechanism exists. `Custom` means Tether must supply a conforming implementation or adapter. `Divergent` means using AgentKit's behavior directly changes the stated requirement. `Unproven` identifies a guarantee neither the inspected interface nor documentation establishes.

| Tether requirement | AgentKit 0.6.0 evidence | Assessment |
|---|---|---|
| CONTRACT-001: provider-neutral model stream, tier mapping, scrubbed failures, per-call credential, request IDs, opaque extras | `DurableAgentServer` calls an arbitrary async handler; it does not implement `ModelPort` | Custom: retain HTTPX adapter, Databricks profile, scripted/recording/replay doubles, protocol conformance |
| CONTRACT-002 sections 2/4/5/7: mandatory gates, typed dispatch, connected-tool hashes, narrow-only providers, guarded results | Runtime wraps the handler; it does not inspect and enforce Tether's model/tool/retrieval pipeline | Custom: keep the engine and route every integration through its gates |
| CONTRACT-003 section 5: asynchronous `send` and `TurnHandle` | Sync, streaming and background invocations; UUID request deduplication | Reuse at the HTTP boundary; map invocation/turn IDs and await the turn's result inside the handler so invocation completion reflects actual work |
| CONTRACT-003 sections 3/5: a second send raises `TurnInProgress` | Accepts queued invocations and runs a session's invocations in acceptance order | Divergent: serialization alone is insufficient; queued work can execute later when Tether required refusal |
| CONTRACT-003 sections 1/3: entries, summary, change records durably committed together; idempotent step keys | Runtime Store commits invocation state/events; Session Store appends opaque JSON separately | Unproven as a Tether store: no exposed transaction spans Tether's required projections or these two services |
| CONTRACT-003 section 3: session lease and lost-lease refusal across processes | Runtime Store fences its invocation writes by attempt; Lakebase coordinates claims across replicas | Reuse concept, custom implementation: it does not fence Tether's separate store or external tool effects; attempt ownership is not the same predicate as Tether's expired lease |
| CONTRACT-003 sections 2/5/6: owner-scoped summary catalog, stable keyset pages, dense position-addressed entry pages | Managed Session Store has list/get/items and metadata; Python SDK auto-pages through synchronous iterators | Partial data access; required projections, cursor guarantees, indexed reads, error masking and benchmarks remain custom; blocking calls need offload or async transport |
| CONTRACT-003 section 4: owner-wide persisted sequence, retained window, resync, bounded followers; reply deltas live only | `context.emit` persists each event; invocation SSE replays stored events. The source also has lower-level session event reads | Divergent feed semantics: the HTTP feed is invocation-scoped; no demonstrated Tether owner feed, coalescing or resync contract; do not equate its cursor with Tether `Seq` |
| CONTRACT-003 sections 4/6: bounded catch-up memory | Lakebase `events()` selects all matching events after the cursor and materializes `.all()`; HTTP loop reads that list | Direct reuse fails the bounded catch-up design for arbitrarily long backlogs; pagination/limits and measured backpressure behavior are needed |
| CONTRACT-003 section 5: confirm, cancel, resume, close and settings commands | Built-in invocation routes start a run and read status/events; arbitrary custom routes are possible | Custom: durable approvals, single-use consumption, cancellation, explicit resume and session commands remain ours |
| ADR-011 / CONTRACT-001: live identity, never serialized, independent per concurrent actor | `RequestAuthContext` retains credentials in process memory, refuses serialization and closes after the attempt; trusted Apps headers supply user credentials | Useful ingress integration. Convert to an explicit Tether actor/credential outside the core; credential refresh and multi-actor isolation need tests |
| ADR-010 / PRD FR-34/37: explicit continuation with fresh identity; bounded chain; full gates reapplied | Automatic stale-attempt recovery with `@app.recover`; request-user recovery fails before the handler; attempts have no built-in cap | Divergent as a recovery authority; Tether must retain the planner, fresh-identity command, safe/unsafe effect policy and chain bound |
| CONTRACT-002 section 6 / PRD NFR-4: metadata-only spans/audit and no credentials in diagnostics | `start_trace(inputs=...)` can capture input; configured framework autologging can capture more; invocation executor uses `logger.exception` on escaped failures | Custom telemetry/error hygiene: avoid content autologging and test exception/log redaction; a raw content-bearing exception can enter logs |
| CONTRACT-001 / PRD FR-28: dummies, conformance and deterministic cassette replay | In-memory invocation store provides local execution; it is not the model cassette/test kit or Tether store conformance suite | Reuse for an adapter test environment only; retain Tether's tests and add real failure-boundary tests from M3 |

Source basis for implementation rows is the versioned archive and file map above. [AK1] API/recovery descriptions are also documented in [AK2] [AK4]; managed store boundaries are documented in [AK3] [AK5]. A missing guarantee is not a claim that Databricks cannot add it.

### Identity and recovery

AgentKit and Tether agree that a user's credential must not be persisted. AgentKit reads trusted Apps ingress headers, namespaces runtime invocation/session identifiers using the requesting principal, and keeps the request credential local to the attempt. Its local-development path uses developer credentials rather than trusting forwarded headers. Those mechanisms are useful, but they are not Tether's `Actor` or `Credential` API. The adapter must derive the actor from verified server context, keep credentials outside invocation JSON, and explicitly pass identity to model and tool calls. The source creates a user workspace client from the forwarded token; it does not establish a refresh mechanism for that user's token during an arbitrarily long attempt. [AK1] [AK2]

For managed Session Stores, `actor_id` is grouping/filtering metadata, not access control. Store permissions authorize access to all actors in the store. That does not implement CONTRACT-003's owner-scoped operations or indistinguishable missing/unauthorized errors. Tether must enforce ownership on all paths; tenants requiring storage-enforced separation need an appropriate physical/store boundary. [AK3] [AK5]

Tether's intended recovery is: mark the old turn interrupted, accept a new authenticated resume command, record a continuation turn, rerun authorization and guards, rerun only safe unfinished calls, and bound the interruption chain. AgentKit's recovery replaces the attempt of the same invocation and calls a recovery handler. For request-user work it raises `MCP_USER_AUTH_RECOVERY_UNSUPPORTED` before our handler, so adding `@app.recover` cannot implement fresh-user resume. Retrying the same invocation ID only retrieves its existing state. A new authenticated invocation or a custom resume route can call Tether's continuation command. [AK1] [AK2] [AK4]

That adapter still needs lifecycle design. A stale `ACTIVE` AgentKit invocation can hold up later invocations in the same runtime session; simply turning off automatic recovery can leave that obstacle. The implementation must demonstrate that interruption is terminalized appropriately and fresh resume cannot remain queued behind dead work. Sessionless outer invocations with Tether owning session exclusion are one candidate to investigate, not a validated recommendation. Persistent queued first attempts are scanned even when stale-active recovery is disabled; an absent recovery handler is not a blanket guarantee against post-restart scheduling. [AK1]

The H1/H2 findings apply to both systems. Runtime request deduplication, log entry deduplication, and preventing duplicate business effects are different guarantees. An unsafe call with an unknown outcome must stay unresolved until reconciliation or renewed approval; a newly generated equivalent call must not silently repeat it. Source inspection also shows the heartbeat loop stops itself after ownership loss; it does not directly stop the invocation handler. Attempt fencing therefore cannot be treated as cancellation or fencing of external effects. [AK1]

### Portability and dependency policy

Adopting AgentKit only in the application composition root or an optional server package can preserve Tether's core portability. Keep AgentKit classes, Apps headers, environment reads, managed-store names, MLflow setup and `agent.toml` outside core types and contracts. Keep the offline demo, in-memory/SQLite stores, local model adapter and generic server path usable without it.

The supported managed durability path provisions its own Lakebase Runtime Store. Local `agentbricks dev` uses in-memory state, so matching HTTP behavior locally does not prove restart durability. Source exposes an injectable `runtime_store` and runtime store protocols, but a portable SQLite/Postgres implementation would be additional integration and verification work; no vendor support or compatibility commitment for that extension is established here. Managed session and memory services remain Databricks services even though REST clients can call them from any language. [AK1] [AK2] [AK3]

The base distribution includes FastAPI/Uvicorn, SQLAlchemy, psycopg, Databricks SDK, MLflow Skinny and `databricks-ai-bridge[memory]`, plus CLI dependencies. `langgraph` and `openai` framework extras are optional. Its explicit `openai` extra conflicts with ADR-012. It is inaccurate to say the base package directly requires that extra or a foundation-model SDK: the inspected manifest does not list one directly. Public metadata for the minimum declared bridge `0.22.0` also includes pandas, tokenization and search packages. This is a partial dependency review, not an installed/resolved dependency-closure certificate. Pin and audit the actual environment before claiming compliance, including optional integrations. [AK1] [AK6]

### Adoption options and remaining work

| Option | Reuse | Remaining work / decision |
|---|---|---|
| Existing application server on Databricks Apps | Already deployed hosting and request handling | Keep it unless replacement removes demonstrated duplication; inspect deployed code before identifying deletion targets |
| AgentKit server around Tether | Invocation API, deduplication/status, selected identity and deployment helpers | Reject as the default path: it retains the core work and adds lifecycle, identity and state translation |
| AgentKit owns sessions/recovery and Tether becomes a thin layer | More vendor execution/storage machinery | Requires explicit changes to product requirements/contracts, a portable replacement path, and a fresh fit evaluation; not equivalent adoption under current intent |

For the optional-server option, Tether's session log is authoritative for turns, approvals, interrupted effects and the UI. AgentKit invocation records describe delivery/execution attempts. Keep one authority for each fact and one policy for deciding when an agent may run. A handler must await the Tether turn rather than returning a `TurnHandle` as if work completed. Client disconnect and invocation completion must not accidentally cancel or terminate Tether's intended lifecycle.

Publishing an AgentKit event after a Tether commit is a second write, not the atomic CONTRACT-003 change record. A crash between the two must be repairable from the authoritative Tether log/outbox. Never make that notification the only way to discover a committed result. Passing every `ReplyText` to `context.emit` would also persist content that CONTRACT-003 declares live-only. Custom feed routes may preserve the intended behavior, at the cost of reducing the amount of AgentKit streaming machinery reused. [AK1]

**Replacement criterion:** first inspect the deployed server and identify a concrete block of invocation supervision that AgentKit would wholly replace. Only if that deletion materially simplifies the application should a replacement experiment exercise two actors, approval, an external effect, process death before result commit, changed policy, fresh-identity resume, browser reconnect and cancellation of a still-running synchronous tool. Check uncertain effects, safe logs, dependency closure and the portable path. Do not add a permanent AgentKit wrapper to hedge the decision. Unknown effects and synchronous-tool cancellation require the remaining H1/H2 design and verification work in either choice; they are not evidence of Tether superiority.

The initial comparison found missing credential/resume signatures. Those details are now reconciled in CONTRACT-002/003: Actor carries an excluded credential, core actions include RESUME, and the client binds an actor and exposes resume. H3 is closed at design level; this does not certify runtime behavior. H1/H2 remain the substantive effect/cancellation qualification risks.

## Differentiation Strategy

Tether's defensible goal is a precise combination: an embedded async Python API for owner-scoped session summaries, pages and reconnectable changes; explicit uncertain-effect recovery; fail-closed application policy; model-provider-independent transport; and useful conformance/test doubles. Each element needs execution evidence. Existing frameworks already have sessions, streaming, typed tools, or persisted steps, so none of those alone supports a uniqueness claim.

Durability here should mean preserving committed history and making uncertainty manageable. Production workflow scheduling, arbitrary untrusted Python containment, multi-agent graphs, and provider-specific hosted tools expand the product and should remain explicit choices.

## Strategic Implications

- **Build:** the owned transition model, gate pipeline, log/store projections, client API, recovery planner, and provider-neutral protocol adapter already selected by the project.
- **Defend:** small public surface, replaceable ports, metadata-only diagnostics, one state authority, and tests that prove the semantics users rely on.
- **Borrow as designs:** LangGraph's durable boundaries; OpenAI's versioned pending-run lifecycle; Claude's context and permission experience; Pydantic's settled/unfinished effect distinction; Pi Durable's replay declarations and store contracts.
- **Avoid:** a generic graph engine, a provider aggregation catalog, a bundled coding agent, or reimplementing every surveyed framework capability. Use MCP, sandbox, and other integrations behind appropriate optional boundaries when a real requirement selects them.
- **Revisit adoption:** if the first consumer needs graphs, distributed workers/timers, or most of Pydantic's capability suite; if protocol conformance upkeep outgrows the owned adapter; or if a measured bake-off shows lower total complexity with one authoritative framework state model.

### Proposed next evidence

Use the current disposition above and the failure-boundary allocation in SD-001. Close the remaining H1/H2 semantics before claiming acceptance, then run one fabricated scenario through the owned harness: tool lookup, approval, unsafe effect, forced crash, changed policy, explicit resume, and client reconnect. Include two concurrent actors and a small-context model profile. Measure completion correctness, duplicated effects, recovery/approval behavior, leaked content, imported dependencies, and client API behavior.

No framework bake-off is scheduled or required. Only if ADR-003's concrete replacement/deletion criterion is met and a later experiment is authorized, implement the same scenario with the selected alternative in isolation. Provider SDKs may be needed by those experiments; that requires an explicit scope/policy decision before installation. A benchmark should count custom integration code, persistence/version migration, deployment components, and unresolved guarantees as well as latency and tokens. No effort or speed estimate is credible from this documentation-only review.

## Open Questions

| Question | Current disposition | Owner |
|---|---|---|
| Does “dependency free” mean literally stdlib-only? | Settled by accepted ADR-003 and current concerns: framework-free within the selected Python stack | Erik LaBianca |
| What should the application do with an unresolved unsafe effect? | ADR-014 selects explicit unknowns; FR-44 reserves reconcile/resolve for P1. Preventing a fresh automatic equivalent action remains the H1 boundary in SD-001 | Erik LaBianca |
| What is the cancellation contract for already-running synchronous tools? | Thread abandonment and unknown outcomes are explicit. Outstanding worker capacity and replacement-work rules remain H2 work in SD-001 | Erik LaBianca |
| Is a minimal context cap P0 while semantic compaction stays P1? | Recommended; exact default/model metadata contract remains open | Erik LaBianca |
| Which optional Pydantic integrations satisfy the dependency policy? | Unknown until exact extras and dependency closure are audited | Erik LaBianca |
| Do published mechanisms meet Tether's performance and isolation requirements? | Unmeasured; needs the same workload and explicit runtime evidence | Erik LaBianca |

## Source register

All links below were accessed on 2026-10-22. They are primary sources; mutable documentation is a dated observation rather than an immutable package certificate. No full third-party source or methodology catalog was copied into this repository.

| Ref | Source and evidence scope |
|---|---|
| L1 | [LangGraph overview][L1]: runtime boundary and LangChain API distinction |
| L2 | [Checkpointers][L2]: persisted frontier, durability modes, history and saver interface |
| L3 | [Interrupts][L3]: resume restarts nodes; approval/replay discipline |
| L4 | [LangGraph package manifest][L4]: declared dependencies on current main, not an installed closure |
| L5 | [LangGraph streaming][L5]: async and runtime event streams |
| O1 | [OpenAI Agents SDK overview][O1]: runner/tool/session feature scope |
| O2 | [Models][O2]: compatible endpoint and model-provider integration |
| O3 | [Sessions][O3]: history protocol, backend scope, run-context integration |
| O4 | [Human in the loop][O4]: pause serialization and pending-task versions |
| O5 | [Guardrails][O5]: blocking/concurrent checks and tool boundaries |
| O6 | [Tracing][O6]: capture and exporter defaults/configurability |
| C1 | [Claude Agent SDK overview][C1]: embedded binary and agent capabilities |
| C2 | [Sessions][C2]: conversation persistence/continue/resume/fork |
| C3 | [Permissions][C3]: tool control mechanisms and evaluation order |
| P1 | [Pydantic AI Harness][P1]: capabilities and 0.x stability policy |
| P2 | [Step Persistence][P2]: snapshots, effect records, recovery limits |
| P3 | [Durable execution][P3]: storage/execution distinction and engine integration |
| S1 | [Strands session management][S1]: persisted state and single-writer boundary |
| D1 | [Deep Agents overview][D1]: harness facilities above LangGraph |
| M1 | [Microsoft Agent Framework][M1]: successor and component scope |
| G1 | [Google ADK][G1]: runtime, workflows, and model component scope |
| R1 | [CrewAI Flows][R1]: flow abstraction and state persistence |
| A1 | [Agno overview][A1]: SDK/service/control-plane separation |
| I1 | [LlamaIndex Workflows][I1]: typed event-driven steps |
| H1 | [smolagents secure code execution][H1]: isolation and executor choices |
| T1 | [Mastra suspend/resume][T1]: snapshot-backed workflow suspension |
| V1 | [Vercel AI SDK agents][V1]: TypeScript loop abstraction |
| B1 | [Pi Durable README][B1]: checkpointed tasks, replay and hook failures |
| E1 | [Temporal activity definition][E1]: idempotency and crash-after-effect window |
| PY1 | [AnyIO working with threads][PY1], documentation identified 4.15.1: shielded waits, abandonment and cooperative cancellation; no Tether runtime test |
| BP1 | [Building effective agents][BP1], 2024-12-19: composable patterns, framework trade-offs |
| BP2 | [Effective harnesses for long-running agents][BP2], 2025-11-26: incremental progress across sessions |
| BP3 | [Effective context engineering][BP3], 2025-09-29: bounded context and selective retrieval |
| BP4 | [Demystifying evals for AI agents][BP4], 2026-01-09: trajectories, outcomes, evaluation design |
| AK1 | [Agent Bricks 0.6.0 release and source][AK1]: exact archive checksum and inspected implementation file map in AgentKit comparison |
| AK2 | [AgentKit agent server][AK2]: invocation lifecycle, identity, recovery and managed Runtime Store |
| AK3 | [Agent memory and sessions][AK3]: state separation, local/deployed behavior and store-level access |
| AK4 | [Query deployed agents][AK4]: public invocation, status and event API |
| AK5 | [Managed agent sessions][AK5]: item semantics, actor grouping, store ACLs and REST availability |
| AK6 | [Databricks AI Bridge 0.22.0 metadata][AK6]: partial transitive dependency inspection, not a resolved install |
| TS1 | [Databricks Apps][TS1]: Python and Node.js deployment support |
| TS2 | [AppKit agentic features][TS2]: TypeScript agent integration, React streaming and model identity |
| TS3 | [Genie Code CLI][TS3]: local coding client, Databricks CLI, skills and MCP; no established harness implementation language |

[L1]: https://docs.langchain.com/oss/python/langgraph/overview
[L2]: https://docs.langchain.com/oss/python/langgraph/checkpointers
[L3]: https://docs.langchain.com/oss/python/langgraph/interrupts
[L4]: https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/pyproject.toml
[L5]: https://docs.langchain.com/oss/python/langgraph/streaming
[O1]: https://openai.github.io/openai-agents-python/
[O2]: https://openai.github.io/openai-agents-python/models/
[O3]: https://openai.github.io/openai-agents-python/sessions/
[O4]: https://openai.github.io/openai-agents-python/human_in_the_loop/
[O5]: https://openai.github.io/openai-agents-python/guardrails/
[O6]: https://openai.github.io/openai-agents-python/tracing/
[C1]: https://code.claude.com/docs/en/agent-sdk/overview
[C2]: https://code.claude.com/docs/en/agent-sdk/sessions
[C3]: https://code.claude.com/docs/en/agent-sdk/permissions
[P1]: https://pydantic.dev/docs/ai/harness/
[P2]: https://pydantic.dev/docs/ai/harness/step-persistence/
[P3]: https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/
[S1]: https://strandsagents.com/docs/user-guide/sdk/agents/session-management/
[D1]: https://docs.langchain.com/oss/python/deepagents/overview
[M1]: https://learn.microsoft.com/agent-framework/overview
[G1]: https://adk.dev/
[R1]: https://docs.crewai.com/v1.15.28/en/concepts/flows
[A1]: https://docs.agno.com/
[I1]: https://developers.llamaindex.ai/python/llamaagents/workflows/
[H1]: https://huggingface.co/docs/smolagents/en/tutorials/secure_code_execution
[T1]: https://mastra.ai/docs/workflows/suspend-and-resume
[V1]: https://ai-sdk.dev/docs/agents/overview
[B1]: https://github.com/earendil-works/pi/blob/main/packages/durable/README.md
[E1]: https://docs.temporal.io/activity-definition
[BP1]: https://www.anthropic.com/research/building-effective-agents
[BP2]: https://www.anthropic.com/research/effective-harnesses-for-long-running-agents
[BP3]: https://www.anthropic.com/research/effective-context-engineering-for-ai-agents
[BP4]: https://www.anthropic.com/research/demystifying-evals-for-ai-agents
[AK1]: https://pypi.org/project/databricks-agentbricks/0.6.0/
[AK2]: https://docs.databricks.com/aws/en/agents/deploy/agent-server
[AK3]: https://developers.databricks.com/docs/agents/memory
[AK4]: https://docs.databricks.com/aws/en/agents/query-llms
[AK5]: https://docs.databricks.com/aws/en/agents/agent-memory/managed-sessions
[AK6]: https://pypi.org/pypi/databricks-ai-bridge/0.22.0/json

[TS1]: https://docs.databricks.com/aws/en/dev-tools/databricks-apps
[TS2]: https://developers.databricks.com/docs/apps/agentic-features
[TS3]: https://docs.databricks.com/aws/en/genie-code/genie-code-cli

[PY1]: https://anyio.readthedocs.io/en/stable/threads.html
