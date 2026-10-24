---
ddx:
  id: SPIKE-004
  type: tech-spike
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: SPIKE-003
      kind: informed_by
    - id: ADR-002
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
    - id: CONTRACT-004
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: TP-001
      kind: informs
---

# Technical Spike: Pydantic AI Agent Boundary Integration

**Spike ID**: SPIKE-004 | **Lead**: Codex, directed by Erik LaBianca | **Time Budget**: one 60-minute local investigation window from 2026-10-23 14:55 UTC | **Status**: Completed

## Objective

**Technical Question**: Can real Pydantic AI Agent execution preserve Tether's authorization, invocation-bound approval, interrupted-effect recovery, original-history separation and per-physical-attempt correlation through public integration boundaries?

The owner authorized this executable slice before committing to the runtime. SPIKE-003 supplies independent core capability evidence and a requirement-by-requirement assessment. This slice implements a focused integration, with its tests and records retained below; it selects no production dependency or runtime.

**Goals**:
- [x] Exercise ordered, fail-closed policy and normalized argument gates in real Agent tool dispatch.
- [x] Pause, serialize, approve and resume with Tether's persisted approval as the authority.
- [x] Inject commit faults and real process exits at intent/effect/result boundaries.
- [x] Keep original content immutable while SDK context is reduced and runner state is serialized.
- [x] Reserve, render, authorize/journal and observe every fabricated physical HTTP transport attempt.

**Success Criteria**: Passing executable positive and adversarial tests, independent counterexample review, explicit limits, and a recommendation that separates integration feasibility from production qualification.

**Out of Scope**: Production package/store delivery; complete client/list/follow/archive APIs; native provider SDK integration; streaming; live AI Gateway tables; a general context strategy; untrusted capabilities; full CONTRACT conformance; clean-install dependency closure; OS-crash durability; performance/model-quality claims.

## Hypothesis

**Primary**: Agent can supply iteration, typed tool validation/dispatch, output correction, deferral and conversation state while a Tether integration supplies current authorization, authoritative history references, approvals and effect commit boundaries.

**Assumptions**: Capability composition is fixed/trusted. The chosen model implementation mediates every physical request. The local fixture is a single-writer process-restart profile, not the production JSONL implementation. No exactly-once external-effect claim follows from its journal.

**Expected Outcome**: Evidence for an Agent integration without building a second model/tool loop; precise remaining work before production selection.

## Approach

**Method**: Executable integration tests with actual Pydantic AI Agent 2.59.0, public capability hooks and sequential tool scheduling; HTTPX2 MockTransport; checksummed JSONL/control and separate content fixtures; real subprocess exits and reopen/recovery.

| Activity | Implementation | Evidence |
|----------|----------------|----------|
| Authorization and validation | `before_tool_validate`, `after_tool_validate`, final engine-mediated async tool wrapper | Policy/refusal, READ_SESSION and mid-run revocation, normalization, approval and mutation tests |
| Model boundary | A custom `Model` using an explicit asynchronous HTTP client | Exact tagged request envelope, headers, receipt and terminal tests |
| Durability boundary | Offloaded JSONL writes/fsync, OS root lock, generation-token checks | Write-failure injection and five process-exit scenarios |
| History and state | Immutable source records; source-verified SDK projections; journal-bound immutable runner checkpoints | Compaction, source denial, invented content and corrupt-runner regressions |
| Independent review | Pydantic evaluation agent runs the suite and adversarial counterexamples | Review receipt and incorporated regressions |

The local run uses Python 3.11.17, Pydantic AI Slim/Graph 2.59.0, Pydantic 2.14.1 and HTTPX2 2.16.0 from an existing cached environment. No package was installed or added to Tether. The fixture's HTTP client invokes MockTransport, which returns fabricated responses; it never opens the example URL. Required-tag names are exercised at that mocked transport boundary, not against Databricks.

Each model generation permits at most two transport attempts, and all purposes share a configured turn-attempt budget. Tool scheduling is sequential. Subprocess waits are bounded to 20 seconds, with kill/join cleanup on timeout. Fixture rows and record bytes are capped. The investigation uses no paid model or warehouse work. No hard total token/memory or OS I/O-latency bound is claimed.

### Responsibility boundary

Agent drives its model/tool graph. Tether does not implement a competing iteration loop in the probe. The integration owns the following sequence:

1. Accepted input is written to the separate source history and referenced by the journal.
2. The model implementation allocates logical-call and physical-attempt ids, renders a credential-free envelope with exact outbound tags, authorizes the present actor, and commits prepared-attempt metadata and local request content before transport.
3. Response-header ids and body ids are recorded separately. Observed response content enters source history. Strict terminal validation runs before a tool manifest is accepted.
4. New effectful calls meet the unresolved-effect barrier first, then current tool authorization, pinned definition/source checks, Pydantic validation, normalized provider refusal and approval gates.
5. The async business wrapper verifies the executable digest again, commits intent before the effect, and normalizes/persists the tool result before returning it to Agent. Journal exceptions propagate as dedicated failures, never model corrections.
6. SDK messages and full Conversation state are separate derived snapshots, bound to journal checkpoint digests. Reads authorize required original sources under the current READ_SESSION policy before disclosure. Incoming tool proposals and emitted model/result content must correspond to accepted source/result records.

## Findings

**FINDING 1**: Public Agent hooks can preserve the exercised authorization and approval boundaries.
- **Evidence**: Tool denial occurs before validation, provider refusal and approval. The provider sees normalized `amount=3` with default `count=2` from the original `amount="3"` proposal. Approval binds executable arguments, tool name/version/schema/replay and specification digests plus actor/invocation. Changed permissions, arguments, tool name/definition, specification or post-gate values prevent the effect. An SDK approval boolean without the persisted grant returns deferred work and executes nothing. Duplicate grant/completed resume does not repeat the effect.
- **Implications**: Reuse Agent's validation/deferral/dispatch while retaining authoritative approval and fresh policy. Arbitrary capabilities that can mutate or repeat handlers are excluded from this fixed composition.

**FINDING 2**: Intent/result commits can fence the tested effects and preserve uncertainty across real process death.
- **Evidence**: Rejected and uncertain intent writes produce zero business effects. A result-write rejection stops the next sibling and next model request. Cancellation/exception after effect leaves an unresolved intent. Stale generation tokens prevent dependent dispatch. Real subprocess exits cover before intent, after intent/before effect, after effect/before result, after result, and same-key safe recovery.
- **Implications**: Started unsafe work is reported unknown and not repeated. Safe recovery reuses the original intent/invocation through current gates; the external idempotent fixture records two dispatches but one effect. Unsafe fixtures deliberately count every dispatch, so deduplication cannot conceal unsafe replay. Completed result content is reconstructed into SDK continuation from its authorized source record rather than rerunning the business function.

**FINDING 3**: Source accessibility alone is insufficient to establish SDK-history integrity.
- **Evidence**: Independent review demonstrated that the initial fixture accepted an invented cached tool success and a changed pre-approval proposal. Both are fixed and retained as regressions. A further review found missing READ_SESSION policy mediation despite owner/provider checks; current read policy now precedes context, pending argument and recovery hydration, each model projection and tool-argument source access. Mid-run revocation and policy-error/denial tests refuse dependent disclosure/effects. Incoming SDK state now requires a journal-bound checkpoint or explicit source-verified reconstruction. Model/user/result components are checked against accepted source/result records; incoming raw proposals match their immutable manifest before normalization. Modified final text, invented tool success, pre-pending proposal edits, revoked/pending/missing sources and corrupt runner snapshots refuse dependent work.
- **Implications**: Original history and execution facts remain authoritative. SDK serialization is reusable derived state, not an authorization grant or a replacement transcript. The strict converter supports the tested text/tool forms; additional content forms require versioned codecs and corresponding source-coverage tests.

**FINDING 4**: Attempt correlation can be enforced below Agent generation hooks.
- **Evidence**: MockTransport checks that each tagged envelope matches an already committed attempt digest. A 503 retry gets a fresh physical id and fresh credential access while retaining its logical-call id. Output correction gets a new logical-call id and `correction` purpose; summary uses `summary`, offers no tools, and shares the policy budget. Header receipts remain distinct from completion-body ids. Local denials/rejected/uncertain starts produce no transport request. Concurrent actors retain separate session tags and credential values. Credential values are absent from persisted source/journal content.
- **Implications**: This is evidence at the custom-model MockTransport boundary. A selected native provider SDK must either expose equivalent per-attempt mediation or have implicit retries disabled and a qualified instrumented transport. No live inference-table join or native SDK retry claim is made.

**FINDING 5**: Strict terminal gating is necessary adaptation.
- **Evidence**: SPIKE-003's independent bare-Agent probe executed a syntactically valid call marked `length`. This model boundary rejects truncated/unknown/filtered responses before accepting an executable manifest; the negative cases produce no effect. Output-schema acknowledgments are explicitly distinguished from business results in the version-pinned SDK converter.
- **Implications**: Agent reuse does not remove CONTRACT-001 wire/terminal validation. Output-schema calls cannot be assumed to traverse ordinary tool policy hooks.

### Measurements

The machine-readable receipt is authoritative for versions, enumerated cases and the final run duration. The suite currently contains 28 test methods; several parameterize fault points. The real-process method covers five subprocess exits/restarts. Passing a method is evidence for its exercised fixture/profile, not a percentage of overall contract completion. The final primary run passed 28/28 methods in 43.737 seconds. The independent hash-matching rerun passed 28/28 in 47.69 seconds and its three source/read-policy regressions passed in 1.437 seconds. These are local run durations under varying load, not performance benchmarks.

| Boundary | Concrete checks | Result |
|----------|-----------------|--------|
| Authorization | Denial/exception, normalized provider refusal, changed permission, stale generation, concurrent actors | Pass |
| Approval | Persisted grant, normalized digest, changed definition/spec/name/args, SDK-boolean bypass, duplicate submission, post-gate mutation | Pass |
| Recovery | Rejected/unknown intent, failed result, sibling stop, cancellation/exception, five real process exits | Pass |
| History separation | Actual SDK compaction with original bytes retained; Conversation id/cumulative incomplete usage; source/projection/checkpoint corruption and invented-result rejection | Pass |
| Correlation | Exact prepared envelope, retries vs corrections, purpose, distinct receipts, fresh credentials and budget/no-egress failures | Pass |

## Analysis

**Hypothesis**: PARTIALLY CONFIRMED.

**Rationale**: Real Agent public interfaces support all five requested boundaries in this fabricated, nonstreaming integration. The prototype contains integration mediation and fixture persistence rather than a second model/tool loop. Independent review found defects in that mediation, which were reproduced and corrected. Remaining uncertainty concerns production codecs/stores, streaming and selected native/provider transport integration; the slice cannot certify them.

### Risks

| Risk | Evidence/impact | Mitigation |
|------|-----------------|------------|
| Snapshot availability/integrity mistaken for source correspondence | Demonstrated in the first fixture | Source verification plus journal-bound checkpoints; negative regressions retained |
| Framework changes or arbitrary handler wrappers bypass mediation | Versioned/trusted integration requirement | Pin and qualify APIs/capability composition; no public SDK approval/effect authority |
| Native SDK retries escape the recorder | Not exercised; each escape breaks attribution/gates | Disable retries or prove every egress through instrumented transport |
| General result/media/argument normalization differs | Tested primitive/str subset only | Versioned codecs and exact execution/approval/source correspondence tests |
| JSONL evidence overstated | Fixture does not implement the production file/lease/index profile | Separate production conformance, torn-write, corruption, directory/fsync and cross-process qualification |
| SDK call ids reused across replies or richer partial batches | Fixture generates unique ids and exercises one sequential failure batch | Production source-position mapping and recovery frontier tests; never make SDK ids authoritative |

## Conclusions

**Primary Conclusion**: Agent integration is technically feasible at the tested public seams. This slice supplies no evidence that a new general-purpose Tether loop is necessary. Tether still needs a deliberate model boundary, gate/approval adapter, source converter and authoritative persistence/recovery integration.

**Confidence**: High for the enumerated tests at the recorded versions; medium for production integration feasibility.

**Limitations**: Mocked HTTP transport, custom Model, sequential primitive tools and string results, a bounded fixture store and source converter, local macOS process exits. Streaming/tool fragments, native SDK retries, live Gateway authorization/tag/row matching, source retention, production JSONL/Postgres behavior, full context coverage/order/omission planning, death before the first usable runner checkpoint, sync-worker lifetime, result guards, client paging/feed, distributed fencing, schema/media corpus, upgrade compatibility and performance remain unqualified. These tests do not establish exactly-once external effects or replace TP-001's primary suites.

## Recommendations

**RECOMMENDATION**: Continue with Pydantic AI Agent as the preferred implementation foundation, preserving the demonstrated Tether mediation boundaries. Keep production selection under ADR-002/003 open until the intended adapter/store profile passes its relevant qualification.
- **Rationale**: Existing Agent iteration, validation, deferral, correction and conversation state work with explicit Tether authority. The source-integrity and read-policy findings required correcting our adapter; Agent replacement was unnecessary.
- **Next Steps**: Port the integration seams into the selected production layout; qualify streaming/cancellation and the intended native/compatible transport with retries configured; implement production history/journal/client conformance and live Gateway joins separately. Reuse direct model APIs with an owned execution driver only for a demonstrated public-hook or complexity failure.
- **Concern Impact**: No production framework dependency, default adapter or runtime decision is ratified here. The SDK-free public core/base and isolated optional-adapter policy remain unchanged. No Git commit was created.

## Artifacts

- [Integration](SPIKE-004-probe/integration.py): actual Agent/model/hook integration.
- [Fixture store](SPIKE-004-probe/fixture_store.py): explicitly scoped local persistence/effect fixtures.
- [Tests](SPIKE-004-probe/test_integration.py): runnable positive/adversarial tests and subprocess entry point.
- [Run receipt](SPIKE-004-probe/receipt.json): versions, test ids/results, scope, limits and source hashes.
- [Independent review](SPIKE-004-probe/review.json): counterexamples, resolutions and independent rerun evidence.
- [Independent regression probe](SPIKE-004-probe/integration_review_probe.py): reproducible reviewer-authored counterexample checks; pass the probe directory as its argument.

Run in an environment containing the recorded package versions:

```bash
python -B docs/helix/02-design/spikes/SPIKE-004-probe/test_integration.py
```

An isolated reproduction can use `uv run --no-project --python 3.11 --with pydantic-ai-slim==2.59.0 --with pydantic==2.14.1 --with httpx2==2.16.0 python -B` followed by the same test path. This command may need package access; the recorded run reused a cached environment and performed no installation. The OS-lock fixture currently targets macOS/Linux. Receipts contain fabricated identifiers/counts only, never captured authorization values or source payloads.
