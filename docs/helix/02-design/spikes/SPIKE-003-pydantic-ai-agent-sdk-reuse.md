---
ddx:
  id: SPIKE-003
  type: tech-spike
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: CONTRACT-005
      kind: informs
    - id: CONTRACT-004
      kind: informs
    - id: ADR-021
      kind: informs
    - id: ADR-002
      kind: informs
    - id: ADR-003
      kind: informs
    - id: ADR-012
      kind: informs
    - id: CONTRACT-001
      kind: informs
    - id: CONTRACT-002
      kind: informs
    - id: CONTRACT-003
      kind: informs
---

# Technical Spike: Pydantic AI Agent SDK Reuse

**Spike ID**: SPIKE-003 | **Lead**: Erik LaBianca | **Time Budget**: one investigation session, capped at half a day | **Status**: Completed 2026-10-23 (corrected-requirements evaluation and targeted offline checks; full adoption qualification remains open)

## Objective

**Technical Question**: Which Pydantic AI model, Agent and Harness components satisfy or can be adapted to Tether's corrected requirements, what gaps remain, and does the evidence justify implementing an owned loop?

Pydantic types are selected independently of this decision. The owner's clarification is that Tether should thoroughly evaluate some or all of Pydantic AI's agent SDK; wrapping OpenAI's SDK is insufficient grounds for rejection. This reopens the dependency-policy rationale in ADR-002/012 and the runtime alternative in ADR-003. It authorizes investigation, not wholesale adoption or weaker authorization/recovery requirements. No application source, schema, or private data is copied from another repository.

**Goals**:
- [x] Separate provider selection, SDK-client injection, HTTP transport injection, and replacing a model implementation.
- [x] Verify an agent loop with typed tools and Pydantic outputs while provider SDK imports are blocked.
- [x] Establish native Claude support and the dependency boundary from official documentation and package source.
- [x] Exercise approval pause, serialized history, resume, repeated submission, and a fresh application authorization decision.
- [x] Compare adoption levels with Tether's contracts and identify the remaining selection evidence.

**Success Criteria**: A reproducible offline probe, qualified findings, and a concrete recommendation for evaluating reuse.

**Out of Scope**: Production integration; live Claude/OpenAI/local-server calls; a full dependency-closure audit; store, stream, cancellation, crash, performance, or security certification.

## Hypothesis

**Primary**: The built-in OpenAI adapter's dependency is separable from the agent runtime. Tether can use Pydantic AI's agent SDK with a custom model, or its built-in native provider adapters, while retaining application-owned authorization and persistence boundaries.

**Assumptions**: Tether's asynchronous Python embedding and CONTRACT-001/002/003 behavior remain required. Choosing one authoritative execution frontier is feasible but needs integration evidence. Existing contracts may need a deliberate implementation mapping; a naming difference is not a rejection reason.

**Expected Outcome**: Pydantic AI remains a viable candidate at multiple adoption levels. Approval and persistence mechanisms reduce work but do not independently establish Tether's complete guarantees.

## Approach

**Method**: Installed-package inspection, official documentation review, and an asynchronous offline prototype.

| Step | Activity | Objective |
|------|----------|-----------|
| 1 | Read vision, PRD, ADR-002/003/012, CONTRACT-001/002/003, SPIKE-001/002 and the framework review | Identify the actual rejection and behavioral requirements |
| 2 | Inspect cached `pydantic-ai-slim==2.59.0` metadata and public model/capability source | Separate mandatory and optional dependencies and integration seams |
| 3 | Run `SPIKE-003-probe/agent_probe.py.txt` in an existing cached environment | Exercise real Agent execution with fabricated custom models and SDK imports blocked |
| 4 | Compare native providers, direct model use, Agent and Harness | Identify what reuse removes and what remains application work |

The run used Python 3.11.17, Pydantic AI Slim 2.59.0, Pydantic Graph 2.59.0 and Pydantic 2.14.1. No dependency was installed or added to Tether. The environment already contained `openai`; an import finder rejected provider SDK imports before importing Pydantic AI. This proves the exercised runtime path needs no loaded provider SDK, not a clean SDK-free installation. The custom model returns fabricated responses without network I/O.

## Findings

**FINDING 1**: The OpenAI SDK is a requirement of the built-in OpenAI adapter, not of every Agent execution path.
- **Evidence**: Agent ran a custom `Model` subclass through model → typed tool → model completion and validated a Pydantic output with provider SDK imports blocked. Importing the built-in `OpenAIChatModel` then failed as expected. Package metadata makes `openai` an extra, while core requirements include `pydantic-graph`, `pydantic`, `httpx2` and other packages. Receipt: `SPIKE-003-probe/receipt.json`.
- **Implications**: SPIKE-002's narrow import finding stands. The inference that it excludes Pydantic AI's entire agent SDK is unsupported. A provider-client replacement inside `OpenAIProvider` still uses OpenAI SDK types; removing that dependency from this path requires a different `Model`, not merely a new URL or HTTP client. A custom production model still owns protocol mapping and streaming implementation.

**FINDING 2**: Claude has a native Anthropic backend.
- **Evidence**: The official [Anthropic guide](https://pydantic.dev/docs/ai/models/anthropic/) documents `AnthropicModel`, `AnthropicProvider`, `anthropic:<model>` selection, custom client injection, and installation with `pydantic-ai-slim[anthropic]`. The inspected provider source imports Anthropic clients; metadata declares `anthropic>=1.12.1` only for that extra. It does not route Claude through OpenAI's adapter.
- **Implications**: An application can select Claude without using an OpenAI-compatible gateway. The native route uses Anthropic's SDK. Changing to Claude therefore resolves an OpenAI-specific concern but still requires a deliberate revision of ADR-012's broader provider-SDK rule. Claude behavior here is documentation/source evidence; the Anthropic extra and a live endpoint were not exercised.

**FINDING 3**: Pluggability exists at four different boundaries.

| Boundary | Supported mechanism | Dependency consequence |
|----------|---------------------|------------------------|
| Model/backend | Pass a built-in native model or custom `Model` to Agent | Agent can run independently of OpenAI's model/SDK |
| Provider client | Inject `openai_client` or `anthropic_client` | Configures that provider's SDK; does not replace its type contract |
| HTTP transport | Inject the provider's supported HTTP client | Controls transport behavior; retains the model adapter and its SDK |
| Owned integration | Implement `Model.request`; implement `request_stream`/`StreamedResponse` for streaming | Can bridge an owned protocol client; retains transport implementation work |

Evidence: the probe, [models and providers](https://pydantic.dev/docs/ai/models/overview/), [provider API](https://pydantic.dev/docs/ai/api/pydantic-ai/providers/), and installed `Model`/provider source. These are separate choices; changing a base URL alone does not remove SDK coupling.

**FINDING 4**: Agent supplies useful approval and typed-execution machinery; Tether must add durable authorization semantics.
- **Evidence**: A `requires_approval` tool did not execute before Agent returned `DeferredToolRequests`. After a JSON history round trip, supplying `DeferredToolResults` executed it. Submitting the same approved history again executed it again. A wrapper checking fresh per-run dependencies denied a later approved submission before its effect. No store or crash was involved.
- **Implications**: This is normal stateless invocation behavior, not a framework defect. Tether must enforce single-use submissions and invocation identity in its authoritative store. A tool wrapper is a demonstrated integration seam for reauthorization; the full ordered gate pipeline, argument/tool digests, lease fencing and durable intent-before-effect remain unqualified. An approval capability alone does not establish those guarantees.

**FINDING 5**: There are useful adoption levels between Pydantic types alone and a complete Harness replacement.

| Level | Planned implementation removed or delegated | Tether still supplies | Assessment |
|-------|-------------------------------------------|----------------------|------------|
| Pydantic types only | Payload validation | Loop, all model adapters, dispatch, approvals, state and telemetry | Baseline; existing use is not evidence for or against Agent |
| Direct model API | Built-in provider mapping, response/stream assembly, usage parsing | Loop, tool pipeline, sessions, recovery and client API | Already supported by SPIKE-001's mock evidence; provider extras require policy reconciliation |
| Agent + native model | Model/tool iteration, typed dispatch and outputs, deferred requests, provider adapters | Contract integration for gates, stable invocation ids, committed state, recovery, UI projections and content-free telemetry | Strong candidate; probe establishes basic mechanisms, not whole-contract fit |
| Agent + custom model | Agent iteration, typed dispatch/outputs and deferral | Above contract integration plus owned protocol transport/streaming | Viable even with SDK imports blocked; does not eliminate protocol maintenance |
| Harness capabilities | Potentially persistence, effect records, context and UI-related machinery | Exact store/API/policy integration and recovery qualification | Evaluate the actual reusable subset; no complete runtime replacement has been proved |

The [direct API](https://pydantic.dev/docs/ai/core-concepts/direct/), [Agents](https://pydantic.dev/docs/ai/core-concepts/agent/) and [deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/) document the first mechanisms. The existing framework review supplies the broader Harness assessment; this probe did not exercise Harness persistence. Installed capability source exposes wrappers around model and tool execution. Wrapper hooks can skip or repeat a handler, so Tether must control capability composition and trusted extension access rather than assume arbitrary hooks preserve its gate sequence.

**FINDING 6**: Retry machinery can be reused with explicit ownership and budgets.
- **Evidence**: The follow-up probe returned one invalid Pydantic output, observed a retry prompt, and returned a valid output on the second request with an output retry budget of one. The [retry guide](https://pydantic.dev/docs/ai/core-concepts/retries/) distinguishes transport, SDK, durable execution and model-correction retries; their independent budgets can multiply requests. Transport retries are optional. This test exercised output correction only, not HTTP backoff.
- **Implications**: Tether can reuse bounded correction behavior while keeping model-port network retries disabled. A blanket rule that Tether must implement every retry is unnecessary. Select limits and a total deadline for each layer; an unsafe tool's uncertain effect must not become an automatic transport or workflow retry.

**FINDING 7**: Core has an explicit conversation state boundary worth adopting.
- **Evidence**: Installed `Conversation` source holds messages, accumulated usage, a stable conversation id and pending deferred requests. `ConversationTypeAdapter` serializes that state. The follow-up probe round-tripped an actual Agent result and continued it: the id persisted and accumulated model requests increased from two to three. The [persistence guide](https://pydantic.dev/docs/ai/core-concepts/persistence/) distinguishes storing a conversation, step persistence, cross-conversation memory and durable execution. It allows application-owned storage and append-based message persistence with the remaining conversation fields stored alongside.
- **Implications**: An application can own its database without reimplementing Agent's conversation bookkeeping. Persisting messages alone loses state carried outside messages. This serialization boundary is a positive design property; it does not by itself implement owner-scoped listing, leases or atomic UI projections. JSON normalization and upgrade compatibility need qualification for the selected payload/version corpus.

**FINDING 8**: Context processing is composable, but its persistence semantics matter.
- **Evidence**: Installed `ProcessHistory.before_model_request` replaces both the request's messages and the run's history. The follow-up probe passed a reduced history to the model and observed the reduced history in the returned conversation while the prior result remained intact and usage remained cumulative. Harness [compaction](https://pydantic.dev/docs/ai/harness/compaction/) documents model-agnostic trimming and summarization strategies, including preservation of tool-call/result pairing. Harness strategies were not executed by this probe.
- **Implications**: Tether should reuse context strategies where possible and adapt them to a source-addressed model-context projection. Its immutable transcript must remain authoritative even when model-visible history is compacted. A summarizer must pass through the model policy, identity and budget controls. The demonstrated `ProcessHistory` mutation is a mapping requirement, not evidence that compaction must be built from scratch.

**FINDING 9**: Harness persistence is a substantive component candidate, with explicit recovery limits.
- **Evidence**: Official [Step Persistence](https://pydantic.dev/docs/ai/harness/step-persistence/) documents append events, snapshots, a store protocol, supplied database backends and a tool-effect ledger. Settled and interrupted snapshots are distinguished; recovery requires examining unresolved effects. Snapshot retention is configurable and storage behavior differs by backend. This is documentation evidence; no Harness version or durable store was installed or executed here.
- **Implications**: Evaluate its store and effect model directly, including failure propagation at intent and result writes, stable effect identity, fencing, retention, and compatibility with Tether's owner feed. A conversation serializer plus step storage is more than a model adapter. The repeated-approved-history test in Finding 4 exercised bare Agent and must not be presented as a defect in Harness persistence. Prefer reuse if the integrated state model meets the behavior, even if Tether's proposed internal types or store decomposition change.

### State design assessment

The inspected core separates model adapters, Agent execution, conversation state and capability hooks. Conversation identity, cumulative usage and pending requests have an explicit serialization boundary. Harness adds opt-in storage and context components. These are concrete reasons to investigate adoption rather than assume a complete rewrite is necessary.

The remaining judgment is integration quality: whether a single chosen authority can connect that state to Tether's authorization, source history, commit and UI requirements with less total machinery. A missing Tether-specific endpoint or an internal schema mismatch is adaptation work to assess. A required safety property with no enforceable hook or commit boundary is a potential rejection reason. Neither conclusion follows from the SDK dependency tree alone.

The follow-up receipt has three passing checks: bounded output correction, conversation round-trip/cumulative usage, and history-processing persistence semantics. This strengthens core-state feasibility; it does not qualify transport retries or Harness durability.

### Evaluation against the corrected requirements

The 2026-10-23 PRD and CONTRACT-001 through CONTRACT-005 govern this comparison. All 53 functional requirements are mapped below. Generic history/correlation behavior is P0; shipping and live qualification of GatewayHistory are P1. A framework's different internal types are adaptation costs, not behavioral failures.

**Recommendation**: Prefer Agent as the reusable runner, with a Tether integration that owns authorization, commits, recovery and the client API. Retain direct model API reuse as the fallback. There is currently insufficient evidence to justify implementing a general-purpose loop or protocol stack from scratch. This is an evaluation recommendation; production selection still requires the integration evidence below.

| Requirements | Reusable component | Remaining work / disposition |
|---|---|---|
| FR-1 to FR-3, FR-5, FR-38, FR-49 | Agent iteration, native/custom Model, response assembly and streaming interfaces | Adapt strict terminal rules, byte limits, error codes and provisional deltas. New offline tests show bare Agent accepts length/unknown text and executes a valid call marked length; a pre-dispatch terminal gate is required. Native streaming and cancellation remain unqualified. |
| FR-4 | Per-run dependencies, explicit model/provider/client injection | Enforce caller/application/anonymous modes and current credentials at each physical attempt. No shared-client credential mutation or ambient fallback. Native Claude is a separate Anthropic adapter, documented/source-inspected only. |
| FR-6, FR-7, FR-9, FR-10 | Typed function tools, validation/defaults, async dispatch, result handling | Translate Tether tool declarations/codecs; ensure pure bounded normalization, exact executable values, typed denials, provider/guard order and no silent argument repair. Hook normalization is now exercised. |
| FR-8, FR-42 | DeferredToolRequests/Results, after-validation and execution hooks | Bind approval to invocation, normalized args and pinned tool/spec; enforce expiry, one-winner consumption, fresh permission, barrier and intent-before-effect under the store lease. Bare repeated approval remains stateless. |
| FR-11 to FR-14, FR-16, FR-34, FR-37, FR-40, FR-41 | Conversation serialization; Harness snapshot/event/effect-store components are candidates | Tether needs one journal authority with atomic summary/feed, fences, stable intent/result keys, lifecycle, deduplication, closure, reference-complete archive and recovery decisions. Harness message checkpoints alone do not establish these. |
| FR-15 (P2) | Conversation history and checkpoint representations | Fork into destination-scoped authorized refs; copied approval/effect facts are historical. No source-ref transplant or copied execution permission. |
| FR-17 to FR-21, FR-48 | Core events and run handles; Harness conversation heads/listing are useful references | Owner-scoped metadata pages, atomic snapshot/watermark, bounded follow/reconnect, command races, pending normalized args and revocation-safe previews remain Tether integration. Existing session facilities are real but not this complete client contract. |
| FR-22 to FR-25 | Optional instrumentation, usage extraction/aggregation | Configure metadata-only spans/errors and no-op default; emit Tether gate audit facts. Reuse unmeasured_requests to map incomplete totals; retain known failed-attempt usage and purpose without double counting. Exact adapter/receiver proof remains open. |
| FR-26, FR-27 | Model, validation, execution and node hooks/capabilities | Fixed trusted composition implements current policy and mediated reads; hook outputs cannot grant access or alter an approved binding. Raw hook exceptions and retry signals need distinct mapping. Hooks are mechanisms, not an authorization policy. |
| FR-28 | Custom Model, TestModel/FunctionModel and serialized messages | Adapt useful doubles to the public model port; provide conformance suites for Tether policy/store/history/context/feed and fabricated cassette drift checks. Existing SDK tests do not prove Tether conformance. |
| FR-29, FR-30, FR-33 | Agent events and a custom offline model | Keep the specified reference CLI/TUI over Client and durable local JSONL demo. Native product clients need not become Tether's UI or storage authority. |
| FR-31, FR-32, FR-36 | Slim runtime plus selected extras; Pydantic typing | Audit/pin exact provider-neutral and optional closures, retain upstream notices where applicable, preserve Tether licensing/contribution rules and prevent vendor types leaking into public APIs. No clean-install audit has been run. |
| FR-35, FR-45 | Request-history hooks; Harness grouping, window and summarization strategies | Adapt to source-addressed coverage/provenance, immutable originals, protected groups and full wire budgets. Govern summary requests independently. Default full context and explicit failure on overflow remain required. |
| FR-39, FR-43, FR-51 (P1) | Compatible/native provider adapters and HTTP/client injection | Implement Gateway profile/attribution and caller-authorized external history plus writable supplement; qualify exact live tag/header/row joins. SDK conversation ids do not establish a join. |
| FR-44, FR-46 | Hooked tool dispatch and serial execution controls; effect-ledger ideas | Enforce session-wide unknown-effect barrier and authorized one-winner resolution; replay safety applies only to the same unchanged invocation. Harness automatic execution recovery is not implemented in the reviewed documentation. |
| FR-47 | Async execution and offload/executor seams | Track actual sync-worker exit, finite permits, queued cancellation, session-busy markers, stale callbacks and bounded shutdown. Default offload alone is insufficient evidence. |
| FR-50, FR-52, FR-53 | Conversation identity/serialization, model hooks and response metadata | Keep originals/exchanges in HistoryProvider and derivative Agent history separate. Reserve/render/authorize+journal before every physical egress, immutable observations, exact receipts/hashes, all readiness states, link resolution and access control. SDK retries/continuations require transport-level visibility. |

### Concrete adaptation gaps and viable seams

**FINDING 10**: Public hooks make Agent integration plausible; an owned loop is not required merely to get awaited gates.
- **Evidence**: In installed 2.59.0, after_tool_validate and before_tool_execute receive validated argument dictionaries. The new probe converts a raw string to an integer, supplies a default, and observes identical executable values in both hooks. The API also exposes wrap_tool_execute, before/after_model_request and node lifecycle hooks; Agent.iter()/next() provide public node control. ToolManager.parallel_execution_mode('sequential') supplies a public whole-run ordering control. These APIs are source-inspected; the full journal integration is not exercised here. See the official [hooks](https://pydantic.dev/docs/ai/core-concepts/hooks/) and [Agent iteration](https://pydantic.dev/docs/ai/core-concepts/agent/#iterating-over-an-agents-graph) documentation.
- **Implications**: A thin execution driver can use public Agent control without implementing its graph. Application-owned policy/store/recovery is compatible with reusing a runner. Restrict capability composition: execution wrappers may repeat/skip handlers, before-execute hooks can change arguments, error hooks can suppress failures, and internal structured-output tools skip ordinary function-tool hooks. Enforce final argument identity at the actual dispatch boundary; never convert journal failure into ModelRetry.

**FINDING 11**: Agent terminal semantics need an explicit Tether gate; usage incompleteness already has a reusable representation.
- **Evidence**: The new contract probe at 2.59.0 observed normal Agent output for text with finish_reason length or None, and actual tool execution for a valid tool call marked length. It also observed raw dict output in after_tool_execute, before message normalization. RequestUsage/RunUsage preserve unmeasured_requests alongside known token totals. Six observations are recorded in contract-receipt.json; both earlier probes were rerun successfully.
- **Implications**: Enforce CONTRACT-001 terminal/structure/size checks before the response reaches dispatch, including output-schema paths. Preserve malformed/truncated source as explicitly incomplete evidence. Do not treat after_tool_execute raw values as already normalized durable results. Map unmeasured_requests to incomplete policy/session totals instead of inventing zero usage.

| Boundary | Gap that remains | Viable integration and decisive test |
|---|---|---|
| Per-tool commits | after_tool_execute sees raw output; after_node_run is a batch checkpoint and is skipped on cancellation | An engine-mediated async tool can normalize/guard/persist its result before returning, with serial dispatch; alternatively prove an awaited normalized-result boundary. Fail intent/result/unknown commits and assert no dependent sibling/model dispatch. Preserve accompanying content and typed failures. |
| Recovery | Serialized messages cannot establish whether an external effect happened | Recover from accepted journal facts, reuse original same-id intent, and supply already-settled results as data; reconstruct Agent context only after authorized source reads. Kill a real process and prove unsafe work stays unknown and new effects remain blocked. |
| Attempts | before_model_request is a logical model lifecycle hook, not proof of each SDK network attempt | Intercept the final rendered transport request or use a custom Model; disable invisible SDK retries/fallbacks/continuations. Test physical retry vs correction logical ids, current credentials, receipt observations and failed start with zero egress. |
| Context | Model-visible history can be changed/persisted as a derivative; summary agents do not automatically inherit outer capabilities | Use authorized source history plus validated projection/provenance. Explicitly attach policy/identity/attempt accounting to auxiliary calls and check final full payload. Reject silent fallback truncation or executable use of rewritten history arguments. |
| Worker lifetime | Cancelling a wait does not stop a synchronous function | Use controlled offload with actual-completion permits/session markers. Prove capacity, stale callback behavior and replacement-turn refusal until real exit. |
| UI/session state | A run event stream is not an atomic owner-scoped change journal | Commit entries, projections and feed together; treat raw text as attempt-scoped provisional output. Race snapshot/reconnect/approval/close and source revocation. |

Harness is a collection of component candidates, not an all-or-nothing runtime choice. Its [compaction documentation](https://pydantic.dev/docs/ai/harness/compaction/) describes pair-aware reductions, receipts, usage-aware summaries and pluggable strategies. It also states that summary capabilities are separately configured and that retention targets do not guarantee full-request fit. These are adaptations to assess against CONTRACT-004, rather than a reason to reimplement every context algorithm.

The reviewed [StepPersistence boundary](https://pydantic.dev/docs/ai/harness/step-persistence/#core-boundary-for-stronger-interrupted-step-recovery) explicitly leaves automatic execution recovery unimplemented and distinguishes raw result hooks from normalized results and settled batches. This limits using it unchanged as Tether's recovery engine. Its event/store/effect machinery may still be reusable if it implements the selected single authority; a second independently authoritative ledger would add complexity. Harness was not installed or executed by SPIKE-003.

### Is an owned loop justified?

The present evidence supports owning Tether's guarantees and integrating Agent. It does not support owning every iteration, schema, dispatch, retry, context and provider implementation. The stricter terminal behavior is a real gap with a model-boundary adaptation; missing owner-specific UI/store behavior is work in either design. Neither independently justifies a new general-purpose runner.

A production selection should compare three bounded alternatives:

| Alternative | Work removed | Work retained / decision |
|---|---|---|
| Agent plus selected provider adapters | Iteration, typed dispatch/output, deferral, correction, model/stream mapping and conversation bookkeeping | Preferred integration candidate; retain Tether's authority, context/history adapter, strict model gate and client service. Pass the boundary tests before selection. |
| Direct model API plus a Tether execution driver | Model/provider/stream mapping and usage parsing | Fallback if Agent hooks cannot preserve per-tool commits/recovery without private internals or near-total replacement. Own the smallest driver that is actually needed. |
| Owned loop and transport | No runtime/provider maintenance delegated beyond validation/HTTP libraries | Currently unjustified. Select only if both reusable layers demonstrably fail required protocol/identity/correlation or impose greater measured maintenance/integration burden. |

Judge integration by removed responsibilities and retained state owners, not prototype line counts. If the adapter ends up replacing most graph transitions, privately reaching into runtime state, translating every event twice, or coordinating two durable ledgers, direct API reuse may be simpler. Use a single normal runner after selection; do not keep two permanent loop implementations to avoid deciding. No cost, speed or maintenance improvement has been measured.

### Integration review closeout

The coordinated SPIKE-004 slice exercises real Agent public hooks with a fabricated nonstreaming model transport and fixture JSONL storage. The final slice passes 28 tests, including five subprocess termination/reopen scenarios. Three independent review regressions also pass. The review found and corrected adapter defects instead of treating them as limitations of Agent:

- Incoming tool proposals now match immutable source arguments before normalization; normalized approval bindings remain separately checked.
- Derivative SDK messages and full Conversation snapshots have journal-bound hashes and verified source/result correspondence; invented tool results or final text cannot become accepted model input.
- READ_SESSION is authorized before initial history hydration, each model projection, raw tool argument hydration, pending approval reads and resume snapshots. Denial, policy error and mid-run revocation prevent dependent disclosure or effects.

The tested path preserves intent-before-effect, stops after failed/unknown intent commits, stops dependent siblings/model requests after result-commit failure, rechecks permissions and tool/spec bindings, and retains unsafe effects as unknown after termination. Safe recovery uses the same invocation key; the fixture distinguishes actual dispatch count from convergent effect count. Physical-attempt tags, wire digests, fresh credentials, correction identities and terminal negative controls pass on the fabricated transport.

**Review disposition**: No remaining blocking defect was found in this bounded integration. Use Agent as the runner and retain Tether's authority/policy/history/client integration. There is no demonstrated functional reason to implement a general-purpose owned loop. An owned driver remains a fallback only for a later demonstrated requirement failure or a concrete integration/maintenance comparison showing less total machinery.

This evidence does not qualify native provider SDK transport/retries, streaming, production store durability/fencing, full client/feed behavior, synchronous-worker lifetime or complete context-plan coverage/order. Recovery before a usable checkpoint and untested SDK message kinds remain outside this slice. These are explicit production acceptance obligations; their absence is not evidence that Agent cannot meet them. The integration uses public SDK controls and has not required private graph internals or a second execution authority.

### Measurements

| Check | Result | Qualification |
|-------|--------|---------------|
| Custom model and typed asynchronous tool | Pass | Fabricated nonstreaming backend |
| Pydantic output validation | Pass | One simple output model; no general schema compatibility claim |
| Deferred approval, JSON history, resume | Pass | In-process serialized-history round trip |
| Repeat an approved submission | Tool runs twice | Demonstrates need for application single-use enforcement |
| Deny with fresh per-run application decision | Pass | Tool wrapper; not complete policy conformance |
| Agent execution without loaded provider SDK | Pass | Import blocking in an environment that has OpenAI installed |
| Built-in OpenAI adapter with SDK blocked | Import fails | Confirms SPIKE-002's dependency boundary |
| Native Claude | Documented/source-inspected | No installed/native call claim |

## Analysis

**Hypothesis**: PARTIALLY CONFIRMED.

**Rationale**: Model substitution and basic Agent reuse are executable facts. Provider SDK wrapping is insufficient to exclude the whole framework. Production state ownership, ordered policy gates and failure recovery have not been qualified. SPIKE-001/002 tested model clients, not Agent or Harness adoption. The prior decision therefore exceeds its evidence if presented as a thorough technical rejection of those layers.

### Risks

| Risk | Prob | Impact | Mitigation |
|------|------|--------|------------|
| Two authoritative execution frontiers or uncommitted effects | Unknown | High | Select one state owner and test commit failure and process death before adoption |
| Approved work bypasses fresh policy or repeats | Demonstrated without application enforcement | High | Persist invocation and approval digests; reauthorize; test duplicate resume and changed permissions |
| Default SDK extras, retries, telemetry or environment reads exceed intended configuration | Unknown for full integration | High | Inspect exact dependency closure; disable implicit retries/content capture; audit explicit configuration and process side effects |
| Custom adapter preserves SDK policy but saves little total code | Unknown | Medium | Compare planned responsibilities removed and integration retained; measure after a prototype |
| Current documentation differs from the pinned package | Possible | Medium | Pin qualification to 2.59.0 and rerun tests on upgrades |

## Conclusions

**Primary Conclusion**: Reuse Agent as the preferred integration candidate and retain Tether's authoritative policy/journal/history/client boundaries. Reuse provider models, typed tools/output, deferral, correction and conversation machinery where qualified; adapt context/persistence components selectively. An owned general-purpose loop is not justified by the current evidence. Native Claude and custom models remain supported integration choices; optional OpenAI SDK use does not select a hosted ecosystem.

**Confidence**: High for the recorded core observations and bounded Agent integration; unqualified for complete production Tether conformance or maintenance savings.

**Limitations**: SPIKE-004 adds bounded fixture evidence for cancellation, concurrent identities and process-kill recovery. Clean-install dependency closure, live/native SDK transport, streaming, production stores/client/context/worker conformance, Harness execution, schema census, performance and maintenance cost remain unqualified. This evaluation recommends Agent reuse; production adoption is a separate governing decision.

## Recommendations

**RECOMMENDATION**: Evaluate the Agent integration first against the corrected acceptance boundaries. Prefer native/provider adapter reuse, qualify selected Harness components individually, and use direct model API reuse if Agent requires disproportionate integration. Do not implement an owned general-purpose loop without a demonstrated failing requirement or a concrete total-complexity case.

- **Rationale**: Agent can remove planned iteration, typed dispatch, output-validation and deferral machinery. Native models can additionally remove protocol mapping. The probe establishes usable seams without claiming that Tether's state and policy contracts are already met.
- **Next Steps**: Use the passing bounded SPIKE-004 integration and independent regression evidence as the basis for Agent reuse. Qualify native-provider and local-compatible transport/streaming, physical SDK attempts and explicit identity, production store faults/fencing, actual worker lifetime, complete context/source codecs and the Client feed. Keep one authoritative Tether journal and derivative SDK state. Reopen an owned driver only for a demonstrated integration failure or a concrete total-complexity comparison; no new generic loop is warranted by the defects corrected here.
- **Concern Impact**: The owner reopened the automatic SDK disqualification. ADR-002 now reopens adapter selection and ADR-003 reopens underlying runtime ownership. The owner's clarification makes optional SDK dependencies an eligible integration choice, not a rejection criterion. ADR-012 now allows isolated optional SDK adapters while retaining provider-free base packaging. A selected production integration must reconcile the associated concerns, PRD, architecture, contracts, build allocations and AGENTS guidance; those older owned-client and framework-free allocations must not be treated as a settled selection or used to prevent this evaluation. Pydantic type use is already selected and is independent of the Agent decision.

## Artifacts

- `SPIKE-003-probe/integration_review_probe.py.txt` and `integration-review-receipt.json`: independent regression checks for the source/cache and READ_SESSION fixes; final slice code hashes and rerun/review disposition.
- `SPIKE-003-probe/contract_probe.py.txt` and `contract-receipt.json`: six targeted offline observations of strict terminal differences, normalized hook arguments, raw result timing and incomplete usage. Run with PYDANTIC_AI_NO_BANNER=1 at the probe composition root; instrumentation is off.
- `SPIKE-003-probe/requirements-evaluation-receipt.json`: corrected-governance hashes, 53-requirement mapping coverage, source hashes and earlier-probe rerun results. This is evaluation evidence, not full conformance.

- `SPIKE-003-probe/state_probe.py.txt` and `state-receipt.json`: follow-up core retry/conversation/history tests at the same package versions. Three checks pass. Harness persistence and compaction are documentation-only assessments; no framework store was installed.

- `SPIKE-003-probe/agent_probe.py.txt`: offline probe; copy to a `.py` file to run with Python 3.11 and `pydantic-ai-slim==2.59.0`/`pydantic==2.14.1`. No provider extras or network are needed by the exercised custom-model path.
- `SPIKE-003-probe/receipt.json`: runtime versions, checks, scope, blocked imports and probe SHA-256. Earlier SPIKE-001/002 evidence remains scoped to its original transport/client tests.
- Official documentation above accessed 2026-10-22 and rechecked 2026-10-23 for this evaluation. Installed package metadata and model/provider/capability source inspected at 2.59.0. Documentation URLs track current content and are not version-pinned behavior evidence.
