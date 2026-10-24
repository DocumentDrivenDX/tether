---
ddx:
  id: CONTRACT-004
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: ADR-021
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: TP-001
      kind: informs
---

# CONTRACT-004: Context Projection and Compaction

**Contract ID**: CONTRACT-004
**Type**: library (asynchronous Python ports)
**Version**: v1, initial pre-release design
**Related**: ADR-003, ADR-014, ADR-016, ADR-018, ADR-021; CONTRACT-001/002/003; PRD FR-35, FR-45 to FR-49

## Purpose

Define a bounded model view of an immutable conversation: selection, explicit omissions, reusable summaries, model-call budgets, and recovery. The engine owns policy, credentials, model calls, validation, and persistence. A context strategy selects what to present and cannot supply executable calls, replace the system prompt, or own session state.

## Scope and Boundaries

- In scope: model profiles, token estimates, source-addressed context plans, engine-mediated summarization, compaction records, deterministic reconstruction, failure rules, and conformance.
- Out of scope: external retrieval/vector stores, application skill formats, autonomous background memory, parallel agents, wire-specific prompt caching, and edits to prior entries.
- Owner: tether core owns the types, ports, validation and executor. Optional strategies are application code under ADR-016.

## Normative Surface

All models are frozen Pydantic models with `extra="forbid"`. All numeric bounds are finite; counts and positions are integers. Empty ranges, negative counts, unknown discriminators, and duplicate source coverage are invalid. `through_index` is inclusive. Names below are normative Python 3.11 surfaces; all I/O methods are asynchronous.

### 1. Profiles and estimates

```python
class ModelLimits(BaseModel):
    profile_id: str; revision: str
    context_window_tokens: int                    # > 0
    output_tokens: int                            # > 0; less than context_window_tokens
    safety_margin_tokens: int                     # >= 0; output + margin < window

class TokenEstimate(BaseModel):
    tokens: int                                  # >= 0; entire serialized input including framing
    method: str; revision: str
    quality: Literal["exact", "conservative", "approximate"]

class TokenEstimator(Protocol):
    async def estimate(self, request: ModelRequest) -> TokenEstimate: ...

class ContextConfig(BaseModel):
    limits: dict[str, ModelLimits]                # keyed by tier; main and optional summary tiers
    summary_model: str | None = None
    max_summary_calls_per_step: int = 1           # >= 0; applies across retries/replanning
    max_source_bytes: int                        # > 0; bound materialized planning/summarization input
    timeout_seconds: float                       # > 0; one context preparation deadline
```

`Engine` receives `ContextConfig`, a `TokenEstimator`, and a `ContextStrategy` explicitly. No profile is guessed from a model name or the environment. A missing tier/profile ends the step `context_unconfigured` before network I/O. The demo supplies a fabricated model profile and deterministic estimator.

Before EVERY model request, including an auxiliary summary request and any retry with changed payload, the engine MUST check `estimate.tokens + output_tokens + safety_margin_tokens <= context_window_tokens`. The estimate MUST cover system text, provider prompt sections, messages, summaries/omission markers, attachment representation, tool schemas, and protocol framing. The adapter supplies wire framing knowledge to its estimator; an estimator unable to bound an attachment MUST refuse with `context_unmeasurable`. Profiles and estimates are versioned inputs, not an assertion that a provider will accept a request. Approximate estimates MUST remain labelled approximate; provider rejection remains possible and MUST NOT cause silent deletion or an unlimited retry loop.

`ModelRequest.max_output_tokens` MUST equal the selected profile's `output_tokens`. Reserved output fields in `extra` that conflict with it are rejected by the adapter as `parameter_conflict`; no silent precedence rule is permitted. Server-specific caching remains an adapter concern. Updated profiles invalidate prior estimates.

### 2. Source-addressed strategy

```python
class ContextSource(BaseModel):
    index: int; message: Message                  # copy of a currently authorized, provider-filtered message

class SourceRange(BaseModel):
    start: int; end: int                          # inclusive original message-entry positions

class ContextPart(BaseModel):
    kind: Literal["source", "summary", "summarize", "omit"]
    ranges: tuple[SourceRange, ...]
    summary_index: int | None = None              # existing compaction for summary; optional predecessor for summarize
    reason: str | None = None                     # required allow-listed reason for omit

class ContextGroup(BaseModel):
    indices: tuple[int, ...]; estimate: TokenEstimate  # indivisible group, including message framing

class ContextInput(BaseModel):
    session_id: str; through_index: int
    sources: tuple[ContextSource, ...]
    summaries: tuple[CompactionRecord, ...]        # only currently reusable records, section 4
    protected: frozenset[int]
    limits: ModelLimits; source_view_digest: str
    fixed_estimate: TokenEstimate; groups: tuple[ContextGroup, ...]

class ContextPlan(BaseModel):
    strategy_id: str; strategy_version: str
    parts: tuple[ContextPart, ...]

class ContextStrategy(Protocol):
    id: str; version: str
    async def plan(self, context: ContextInput) -> ContextPlan: ...
```

| Rule | Requirement |
|------|-------------|
| Source identity | Positions address original `message` entries; ranges include message entries between their endpoints, ignoring non-message facts. A strategy returns references, not arbitrary new `Message` or `ToolCall` objects. |
| Coverage | Every authorized input source MUST be represented exactly once as original, summarized, or explicitly omitted. Parts are chronological and disjoint; ranges MUST exist at or before `through_index`. Omission reasons are `budget`, `provider_filter`, or `attachment_unavailable`. Provider-dropped messages are accounted for by an engine-generated `provider_filter` omission. |
| Atomic groups | A committed assistant reply with calls and every corresponding result is an indivisible group. A retained group MUST NOT have missing, duplicate, invented, or reidentified calls/results. Call ids are scoped to the originating assistant entry; ambiguous wire ids are refused, not rewritten into new executable calls. |
| Protected context | System/spec instructions and current provider prompt sections are always present outside the strategy. The current user input and active exchange, pending approvals, and unresolved-effect status are protected. Unsettled tool groups MUST NOT be summarized or omitted. The engine supplies protected positions, not the strategy. |
| User intent | Built-in strategies preserve user messages verbatim by default. Summarizing older user messages requires explicit `SummarizingContext(summarize_user_messages=True)` configuration. A policy that cannot fit protected content fails; it does not silently relax protection. |
| Payload isolation | The strategy sees copies of the currently authorized, provider-filtered source view, with no credential. It MUST NOT mutate the original log, settings, tool definitions, or gate state. |
| Hooks | `before_request` keeps its narrowing/redaction contract. The engine applies it to source messages first, validates group integrity, then builds context. A summary is derived from that sanitized view, never from pre-redaction content. No later hook may widen the resulting view. |
| Retrieval | A strategy may select earlier source ranges from this session's committed refs after bounded, actor-authorized history hydration. External data first enters through the application's authorized tools; a strategy receives no unrestricted store/network handle. Optional archive access never becomes a turn dependency. The authoritative history provider is a separate bounded dependency: unresolved content is `history_pending` or `history_unavailable`, never inferred from the next model prompt. |
| Bounded work | Planning and summarization share the configured context deadline and byte/call limits. Exceeding a bound ends the step explicitly; no unbounded replan/compress/retry loop. |

The engine supplies a fixed-payload estimate (current system/provider instructions, tool schemas and envelope) and per-group estimates including framing. Strategies use these to select candidates within the input allowance `window - output - margin`; summing group estimates is preliminary, not a replacement for the final full-request estimate. Token estimation may inspect only the already byte-bounded source view. For `source` and `omit` parts, `summary_index` MUST be absent; `summary` requires an existing compatible compaction index; `summarize` may name one compatible predecessor. Only `omit` has a reason, which is required; other parts MUST leave it absent. Each part has at least one range. The engine validates each plan before materialization. An invalid plan ends `context_invalid`; there is no fallback that discards additional history without an explicit valid plan.

### 3. Engine-mediated summarization

A `summarize` part requests one engine-owned auxiliary model call over its source ranges and optionally a prior compatible summary. The engine builds a fixed summarization prompt covering goals, constraints, decisions, progress, unresolved items, source ranges, and material to reload. Source text is delimited as untrusted material. A prompt asks the model not to follow source instructions; this is a heuristic, not a guarantee against injection. Structural validation and the tool-free, gated summary path are the enforceable boundaries. Summary generation does not replace its source material until its response, estimate and provenance are validated and committed. The same `ContextConfig.max_source_bytes` and deadline apply when loading/combining prior summaries. A summary request offers no tools; a response containing calls, a refused/truncated response, an empty summary, or a malformed response is rejected. The engine never repairs and executes a summary response.

The engine authorizes `CALL_MODEL` separately for each summary call with `AuthzRequest.purpose="summary"`, the explicitly selected identity mode and current credential when required, selected tier, scope and accumulated usage. It records a `model_call` fact with `purpose="summary"` and charges its tokens, latency, and call count to turn usage. Auxiliary calls do not consume another agent step but cannot bypass call/cost/deadline policy. Calls are made through the same governed `ModelPort` and selected adapter, including an isolated optional SDK adapter under ADR-012. A strategy cannot bypass that port with its own provider client. Recursive compaction, tools and ungoverned background work are forbidden.

The summary request itself must fit its own profile. If source material is too large, the engine reports `context_exhausted`; it does not trim summary inputs silently or move to a larger/other model without explicit configuration. Strategies may propose bounded chunks in later steps, within the same coverage/protection rules. SDK/model conversation histories are transient conversions of this source-addressed view. A selected runner's cumulative usage and deferred execution metadata must be preserved as versioned derived state separate from the authoritative history and from engine approvals/effect facts. An existing summary may be folded into a new summary only when its entire coverage is included and its provenance is compatible; dependency cycles are invalid.

### 4. Persistence and reuse

```python
class CompactionRecord(BaseModel):
    strategy_id: str; strategy_version: str
    through_index: int; source_ranges: tuple[SourceRange, ...]
    source_view_digest: str; scope_digest: str
    spec_digest: str; profile_id: str; profile_revision: str
    summary_ref: HistoryRef                       # generated content in authoritative HistoryProvider
    predecessor: int | None = None                # earlier compaction-entry index
    lossy: Literal[True] = True

class ContextUse(BaseModel):
    through_index: int
    parts: tuple[ContextPart, ...]                 # summarize replaced with committed summary reference
    source_view_digest: str; request_digest: str
    profile_id: str; profile_revision: str
    estimate: TokenEstimate; output_tokens: int; safety_margin_tokens: int
```

`CompactionRecord` is the payload of a new `compaction` entry; `ContextUse` is a new `context_use` entry. The engine persists/binds successful summary content through CONTRACT-005, then commits its ref, the auxiliary `model_call` fact and associated change records atomically in the execution journal under the current lease before using that summary. After final main-request validation/estimation it commits `context_use` before sending. Failed appends stop preparation; the request MUST NOT be sent on an uncommitted projection. Records contain no credential. Only the referenced summary history record carries derived content; digests, counts, ranges, and reasons remain facts.

A summary is a lossy derivative, never proof that every fact survives. The model view MUST label it as generated and cite its source entry ranges. Every omission MUST have an explicit marker and source ranges. Originals remain unchanged behind HistoryProvider references, resolved through authorized history paging. Journal positions still identify source messages; they do not require the live store to contain source text. If originals are no longer available, the engine reports unavailable context; it cannot treat a partial derived archive as a complete recovery source.

Reuse requires reauthorization of session access and matching source-view, scope, spec, strategy, profile and version digests. A compaction source-view digest covers only its own covered ranges, including immutable HistoryRef ids/digests and the decoded message view: exact post-provider message bytes, positions and attachment metadata. Reuse recomputes that digest for those ranges, so appending unrelated new messages does not invalidate a summary. A context-use source-view digest covers the entire input snapshot. Newly protected positions invalidate any summary that covers them. A changed redaction, actor scope, spec instruction or profile invalidates reuse. Current provider instructions and tool schemas are rebuilt and included in the final estimate, never restored from a summary. The engine adds a current non-executable status notice for unresolved effects and pending interaction outside strategy control; its bytes count in the fixed estimate. A summary may not confer authorization or decide effect/approval state.

Recovery reconstructs from log entries, not an in-memory cursor. A summary committed before a crash is reused only after these checks; an uncommitted summary may be regenerated. A context preparation ordinal is allocated under the session lease for each logical main step, including a resumed step. Summary and context-use keys use this ordinal and the summary part position; uncertain append outcomes are read back by that key before deciding whether a new record is needed. Model calls may consequently incur cost more than once; Tether does not promise exactly-once model billing. `context_use` describes the selected pre-adapter request, not evidence of provider acceptance. Replay cassettes capture the actual model-port request, including its context and output limit.

### 5. Materialization, cache stability, and failure

The engine materializes source messages unchanged except for already-validated provider redactions. It renders summary and omission markers as clearly labelled, non-executable assistant context. It MUST NOT add fictitious successful tool results, rewrite live arguments, erase the effect barrier, or dispatch a call derived from a historical summary. Markers count against the budget; if a marker cannot fit, the whole step fails.

Prompt sections preserve provider registration order; tool schemas use stable tool-name order. Given identical sources, plans, summaries, configuration and definitions, materialization is deterministic. Dynamic capability/skill notices belong to application prompt sections or explicit messages. Their bytes and version enter the request digest; preserving cache hits never permits a stale tool to bypass current dispatch checks. Micro-compaction/background memory are not default behaviors. Frequency and reclaim thresholds, if supplied by a strategy, are explicit settings; any auxiliary work uses this contract's gates and limits.

The engine rejects a plan with more new summary parts than its configured remaining per-step summary-call allowance before making any of those requests. The engine makes at most one strategy plan per main model step. Each proposed `summarize` part counts toward the per-step auxiliary-call bound, including failed requests; an exhausted bound ends `context_exhausted`. After materialization it performs one final estimate. If that does not fit, the step stops; no model request is made. A provider's context-window rejection also stops the step; a new user request may select different explicit limits/strategy. A cancellation propagates and does not become a compaction failure.

### 6. Defaults and conformance

- `FullContext` preserves every source, produces no summary and no omissions, and refuses an over-budget request.
- `TailContext` retains protected groups and as many recent complete groups as fit, marking each omitted range. It preserves all user messages. It does not call a model.
- `SummarizingContext` proposes summaries of completed older groups and retains protected groups. Its explicit options control whether old user messages may be summarized. It uses only engine-mediated summary calls.
- `ScriptedContext` and `FixedEstimator` are deterministic dummies; `FullContext` is the default strategy. The default adapter supplies its declared wire-aware estimator; third-party adapters must supply an estimator explicitly.
- `context_strategy_suite(factory)` verifies coverage, bounds, atomic groups, protected inputs, immutable sources and deterministic materialization. `token_estimator_suite(factory)` verifies full-payload accounting, declared quality, framing/attachments and repeatability. `context_executor_suite` verifies authorization, identity, usage, cancellation, failed commits, source compatibility, bounded calls, and restart reconstruction. Dummies and built-ins MUST pass their applicable suites.

## Precedence and Compatibility

- `CONTEXT_CONTRACT_VERSION=1`; CONTRACT-001/002/003 are revised in place before initial release. There is no old public API compatibility shim.
- Engine authorization, pending/uncertain execution state and protected context override strategy proposals. CONTRACT-003 governs journal commit/order and owner-scoped reads; CONTRACT-005 governs original content and exchange correlation; each context preparation additionally authorizes `READ_SESSION` for the present actor before disclosing source content to a strategy; CONTRACT-001 governs transport.
- Optional descriptive fields are compatible; changed plan semantics, coverage, digest rules or validation require a version change. Unknown strategy/version records are retained but MUST NOT be reused.
- Current records remain interpretable by their declared versions; an unsupported record yields `context_incompatible`, never an inferred summary or silent raw-history expansion beyond the configured budget.

## Error Semantics

| Condition | Error / Outcome | Retry | Recovery Expectation |
|-----------|------------------|-------|----------------------|
| Missing profile, estimator or summary tier | `context_unconfigured` | No automatic retry | Supply explicit configuration |
| Estimate cannot account for payload | `context_unmeasurable` | No | Fix estimator or supported attachment encoding |
| Invalid coverage, altered call, split/protected group | `context_invalid` | No | Fix strategy; originals remain |
| Byte/token/call budget exceeded, protected material too large | `context_exhausted` | No | Select explicit larger limits or a different valid strategy |
| Authoritative history not ready / unavailable | `history_pending` / `history_unavailable` under CONTRACT-005 | Bounded later read | End context preparation without guessing payload or effect |
| Missing/incompatible source or summary | `context_unavailable` / `context_incompatible` | No | Restore authoritative data or prepare compatible context |
| Policy denies summary | `context_denied` | No | No auxiliary or main request is sent |
| Strategy/estimator error, summary failure, timeout or bad terminal response | `context_failed` | No | Preserve originals; report metadata-only reason |
| Append failure / lost lease | `StoreError` / `LeaseLost` | No | No uncommitted context is used; normal store recovery |

Context errors end the turn with `stop_reason="context_unavailable"` and one of the codes above; no synthesized final answer is recorded. There is no fallback to silent history loss. Audit and spans carry ids, counts, versions, quality labels and reasons, never summary/source content.

## Examples

```python
engine = Engine(
    # Other ports are composed as in CONTRACT-002.
    context=FullContext(), estimator=adapter_estimator,
    context_config=ContextConfig(limits={"balanced": model_limits},
                                 max_source_bytes=source_cap,
                                 timeout_seconds=context_deadline),
)
```

A transcript has user message 0, completed tool group 2/4, and current user message 7. `TailContext` can retain 0 and 7 and mark 2..4 omitted; it cannot retain result 4 while omitting call 2. `SummarizingContext` may instead request a summary covering 2..4; the engine commits that summary, then a `context_use` referencing it. A crash changes neither original group nor its invocation ids.

## Non-Normative Notes

ADR-021 records the design rationale. The [Hermes profile](../../00-discover/component-profile-hermes-agent.md) supplies mechanisms and regression examples, not a claim that Hermes implements this contract. An estimator's quality and a summary's fidelity require qualification on actual configured model/server profiles; the design makes their uncertainty explicit.
