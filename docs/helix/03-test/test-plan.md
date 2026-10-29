---
ddx:
  id: TP-001
  type: test-plan
  activity: test
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: SD-001
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: CONTRACT-004
      kind: informed_by
---

# Tether Test Plan

## Strategy

**Goal**: Qualify the first-release contract combination: one asynchronous Python harness, current identity/policy on every protected call, immutable durable execution state, explicit uncertain effects, bounded model context, and a reconnectable client API. Every implemented port and its dummy passes the same applicable conformance suite. The requirements and CONTRACT-001 to CONTRACT-005 define behavior; this plan is planned verification, not passing evidence.

**Out of scope**: Globally exactly-once remote effects, hostile in-process Python containment, unqualified model/server profiles, and operating-system-crash simulation in ordinary continuous integration. P1 target-specific reconciliation requires separate adapter evidence.

## Selected Agent Qualification

ADR-003 selects Pydantic AI Agent; ADR-002 selects model integration reuse. Test the production Tether integration on that runner, not an owned-loop alternative. The base includes pydantic-ai-slim; foundation-model SDKs remain selected optional extras. Public Tether payloads remain free of SDK types. SPIKE-004's 28 integration tests, three independent regressions and five process exits are bounded baseline evidence, not full release conformance.

SD-001's Q0-Q9 table is the work/owner/release-scope index. The primary proving layers are:

| Qualification | Primary layer / required controls |
|---|---|
| Q0 runtime/base/extras | Installed integration: clean closures, offline custom model, public typing, named import owners and planted forbidden-import/private-graph controls |
| Q1 gate/tool/state | Contract + integration: real public Agent hooks, normalized approval/intent identity, fixed capabilities, current reads, mutation/duplicate/commit faults and serial batches |
| Q2 model/transport | Adapter integration: actual native/compatible SDK transport, every physical attempt, final wire/correlation, fragmented terminal/argument streams, retry/cancellation/deadline and identity; live fabricated profile smoke |
| Q3 source/context | Contract + integration: complete codec/source coverage and order, protected groups, read revocation, summaries through the governed path, full payload fit, immutable originals and failed commits |
| Q4 durable recovery | Real process/storage integration: required JSONL/Postgres profiles, reference/transaction/index/fence faults, lifecycle/archive, reconstruction before a usable SDK checkpoint, unknown barrier and manual resolve |
| Q5 sync workers | Real controlled worker integration: permit/session marker until actual exit, queued cancellation, replacement-work refusal and finite shutdown |
| Q6 Client/reference clients | Concurrency integration + installed end to end: atomic snapshot/feed/commands, bounded access/reconnect/retention and durable offline CLI/TUI; measured budgets keep P1 scope |
| Q7 diagnostics/usage | Contract tests and optional receiver integration: content-free/no-op behavior, exact/incomplete usage and real trace nesting; OTel support requires real exporter/receiver proof |
| Q8 versions/replay | Locked runtime and upgrade integration: serialized checkpoints/history corpus, explicit unsupported versions, replay drift and rerun boundary suites on SDK/Graph/Pydantic upgrades |
| Q9 Gateway | Selected live profile integration (P1): exact source/tag/receipt joins, current-caller reads, bounded SQL, readiness/retention/supplement; no inferred execution |

Required P0 release capabilities pass their applicable gates before support is claimed. Optional native/Gateway/Harness components are qualified when selected for shipment; Agent selection does not certify them. Reuse SPIKE-004 regressions in the production module layout, retaining versions and actual observed effect versus dispatch counts. No benchmark or line count establishes a reason to add another loop.

## Framework Selection & Cost

| Layer | Framework | Justification | CI Time Impact | Maintenance Burden |
|-------|-----------|---------------|----------------|--------------------|
| Contract/unit | pytest, AnyIO plugin, Hypothesis | Existing selected Python stack; deterministic ports and property-based state exploration | Unmeasured; separate fast deterministic suite | Maintain shared factories and reusable conformance cases |
| Integration | pytest/AnyIO; local HTTP scenario server; PostgreSQL 18 | Proves transport cancellation/framing and actual transactional/fenced store behavior | Unmeasured; process/server startup is isolated | Maintain controlled fault points, no network credentials |
| End to end | Reference CLI/TUI drivers and a subprocess effect recorder | Proves library-to-client behavior, restart, approval and visible effect counts | Unmeasured; fewer critical journeys | Fabricated scenarios and controlled barriers rather than timing sleeps |
| Performance | Existing planned benchmark suite | Protect API latency/memory and context/executor bounds | Baseline required; no claimed timing | Commit measured budgets by workload/profile |

**Cost/benefit summary**: Reuse port suites and state-machine properties for most cases; reserve real processes, workers and PostgreSQL for guarantees those boundaries establish. No framework installation or competitor benchmark is a gate. No measured CI/runtime baseline exists in this plan.

## Critical Paths (P0)

1. **Model transport and identity** (FR-1 to FR-5, FR-38/49): concurrent actors, fresh credential access, no credential persistence, a finite request/output budget, valid terminal frames, complete arguments only, no hidden replay after emitted text, and cancellable bounded retries. Malformed/truncated responses never dispatch a tool or become `final`.
2. **Tool pipeline and policy** (FR-6 to FR-10, FR-26/27/42): each policy/hook/guard boundary and its failure; argument/schema/digest validation; approval expiry, supersession, concurrent answer, revocation and restart. Optional guards do not become a claimed sandbox.
3. **Sessions and recovery** (FR-11 to FR-14, FR-16/34/40/41/42/44/46): atomic entries/summary/change commit, owner scoping, fencing and capability-qualified recovery. Kill a real process after intent, after observable fabricated effect, and before result commit. The surviving log says unknown and every new effectful call is blocked even under different ids/names/arguments until explicit resolution.
4. **Resolution** (FR-44/46): pending-effect paging and counts rebuild from the log; present identity and `RESOLVE_EFFECT` authorization; one-winner idempotent resolution; conflicting answers rejected. Resolving never executes a tool, rewrites evidence or treats an ordinary exception as no effect.
5. **Actual worker lifetime** (FR-6/13/20/47): hold a real synchronous tool on a controlled barrier, cancel the await, exhaust the configured worker pool and attempt replacement work. Permits/local session-busy markers remain until real exit. Queued cancellation never starts a function; stale completion never appends or clears an unknown. Shutdown respects its deadline and reports remaining workers.
6. **Context and compaction** (FR-35/45): full/recent/summary strategies, complete source coverage, immutable original arguments, protected user/current/unsettled groups, markers counted in the budget, missing sources, incompatible summaries and disabled auxiliary model. Every summary is gated, credential-isolated, usage-accounted, tool-free and bounded; failed summary/commit preserves history and sends no uncommitted main request.
7. **Context state exploration** (FR-35/45/49): cut every position in generated tool-group histories; each original is retained, summarized or explicitly omitted, never silently lost. Crash before summary commit, after summary commit, after context-use commit and before provider response. Changed redaction, profile, spec or scope invalidates summary reuse. A malicious summary response with a tool call is never dispatched.
8. **Client reconnect** (FR-17 to FR-20/48): race snapshot with entry append, pending approval and answer; bootstrap reads through the returned head and follows after the atomic watermark. Same-commit events are all consumed. Retention loss restarts bootstrap; lag/outage/reconnect clears provisional attempts. A slow subscriber cannot stall another subscriber or the turn.
9. **Telemetry and packaging** (FR-22/23/25/28/31/32/36): content-free metadata across success/failure/context paths, deterministic fabricated cassette replay, clean core install with no foundation-model provider SDK closure, source import boundaries and license texts. Reused upstream code retains its notices.
10. **Reference journeys** (FR-29/30/33): zero-network/credential local JSONL reference profile with explicit fabricated model limits/estimator, plus an explicit volatile in-memory demo with `resume="off"`; terminal approval and context exhaustion are readable; an installed durable-store journey resumes with live identity. Quickstart and client performance use the PRD's proposed budgets until replaced by measurements.

11. **History and local sessions** (FR-50 to FR-53): JSONL content plus journal reopen accepted sessions, preserve ordering/digests and rebuild corrupted indexes; a second writer is refused. Inject torn records, failed content-before-ref writes, failed/unknown journal acknowledgment and orphan records. Hot snapshots/lists never scan content.
12. **External source correlation/coverage** (FR-52/53; Gateway delivery is P1 FR-51): distinct session/turn/call/attempt ids before egress, dedicated request tags, separate header/body/provider invocation ids, bounded caller-scoped reads and no identity fallback. Inject delayed/missing/partial/duplicate/multi-invocation/ambiguous rows. The UI exposes holes; restart never guesses an effect. A controlled source double exercises generic P0 behavior; a separate live fabricated profile proof qualifies an actual Gateway source join at P1.
13. **Effect versus replay** (FR-42/46): an idempotent effectful writer succeeds under its original key then loses acknowledgment. A model emits the same writer or another writer under a NEW id; both remain blocked. Same-invocation replay-safe recovery passes fresh gates under an unchanged definition; read-only calls remain separately allowed.

14. **Recovery and lifecycle boundaries** (FR-11/12/14/34/40/42/46/47): same-invocation recovery reuses the immutable intent under the new turn without an entry-key conflict and repeats all current gates. A started read-only/replay-unsafe call receives transcript closure before the next model request. Race close with turn start, live worker, approval answer and unknown effect; rejected/unknown commits cannot partially close a session. Closed sessions remain readable and reject mutations.
15. **History authority and client exposure** (FR-17/18/19/25/48/50/52/53): call detail preserves all attempts and observations in committed order, refuses unreachable logical ids before backend reads, and never uses provider request ids as application identity. Exercise finite history-link chains, replacement order, cycles, semantic mismatch, denied/unavailable replacements and pinned snapshots. Revoke source access and prove list/search/previews cannot expose cached source content. Unknown usage remains incomplete rather than zero.
16. **Attempt and argument identity** (FR-4/8/26/38/42/49/52/53): reserve attempt identity, render the final tagged wire request, then authorize and commit start before egress. Check canonical full-record/source digest fixtures, incomplete versus complete observations, distinct multi-invocation receipts, local policy denial and rejected/unknown start commits with zero network egress. Explicit caller/application/anonymous modes never substitute identity. SDK coercion/defaults/schema changes bind approval to exactly the canonical executable arguments and pinned tool version; changed values require fresh gates/approval while raw proposals remain preserved.

## Acceptance Criteria Layer Allocation

Feature/story acceptance detail remains to be framed; these requirement classes allocate primary proof without claiming tests exist. The following requirement classes have one primary proving layer; eventual story plans name concrete tests without duplicating these primary allocations.

| AC class / source | Story Test Plan | Primary Layer | Why this layer |
|-------------------|-----------------|---------------|----------------|
| Model-port shapes, terminal rules and replay, FR-1 to FR-5/28/38/49 | Pending story framing | Contract | Shared adapter/dummy expectations; fake HTTP peers supply exact bytes |
| Policy, tool/approval behavior, FR-6 to FR-10/26/27/42 | Pending story framing | Contract | Engine invariants independent of infrastructure |
| Durable log, owner/fencing/idempotency, FR-11 to FR-14/16/34/40/41 | Pending story framing | Integration | Actual transaction and process boundaries |
| Unknown barrier and manual resolution, FR-44/46 | Pending story framing | Integration | Observable effect and durable restart state must agree |
| Worker capacity/cancellation, FR-47 | Pending story framing | Integration | A real worker must outlive its cancelled waiter |
| History/provider identity and JSONL file crash boundaries, FR-50/52/53 | Pending story framing | Integration | Files, receipts and controlled external-source boundaries establish authority and exact correlation; live Gateway joins are P1 |
| Context plans, protection, estimates and failures, FR-35/45 | Pending story framing | Contract | Exhaustive group boundaries and injected failures through public ports |
| Context commit/restart reconstruction, FR-35/45 | Pending story framing | Integration | Commits, fenced writes and restart invalidate in-memory assumptions |
| List/page/snapshot/follow, FR-17 to FR-21/48 | Pending story framing | Integration | Concurrency, watermark and bounded subscriber behavior |
| Diagnostic mapping/receiver, FR-22 to FR-25 | Pending story framing | Integration | Real exporter/receiver is required for OTel claims |
| Demo, installation/licensing and reference clients, FR-29 to FR-33/36 | Pending story framing | End to end | Installed user experience and dependency closure |
| Recovery/close/argument binding, FR-8/11/12/14/26/34/40/42/46/47 | Pending story framing | Integration | Journal keys, current authorization, real workers and competing mutations |
| Call detail, link authority, revocation and usage, FR-17/18/19/25/48/50/52/53 | Pending story framing | Contract + Integration | Controlled history providers and committed session binding order |
| Attempt protocol, identity and canonical digest, FR-4/38/49/52/53 | Pending story framing | Contract | Deterministic wire/record fixtures and zero-egress negative controls |

## Secondary Paths (P1-P2)

- **P1**: target-specific read-only reconcilers; Databricks profile and authoritative Gateway history provider plus supplemental legacy archive read-as-caller (FR-39/43/51); opt-in automatic recovery identity; real OpenTelemetry exporter and committed performance budgets as selected in the PRD. The no-op tracer proves no exporter integration.
- **P2**: fork/rewind, optional SQLite and additional model/server profiles. Each receives its own relevant conformance matrix before a support claim.

P2 fork tests materialize source content under destination-owned/session-scoped refs, preserve provenance, refuse inaccessible sources and never replay copied approvals/effects as executable actions. Generic history availability behavior remains P0 even when a particular external provider ships at P1.

## Test Data Strategy

Use fabricated messages, documents, tools, actors and effects only. Build any credential-shaped runtime probe value from parts; never store a credential literal. Dummies script every model response including explicit finish reason, usage absence, partial stream, malformed arguments, oversized result and summary output. Effect recorders are isolated subprocesses using temporary data; worker barriers coordinate readiness and release without `time.sleep`. Hypothesis shrinks histories, cut boundaries, stale leases and racing answers. Source/summary fixtures include markers that must be absent from telemetry and from future executable arguments.

## Known Gaps

Local JSONL file/restart qualification is recorded in SD-001's execution receipts with its single-writer profile and versions; it does not establish actual operating-system power-cut behavior. Gateway request-tag transport is documented, but exact selected-workspace header/tag/row correspondence still needs live fabricated evidence. AGENTS.md follows the owner's Agent decision and ADR-002/003/012.

The current P0 receipt maps all sixteen paths to executed production cases and selected profile evidence. Its final 2026-10-29 addendum records the complete clean-source gate at tagged commit `e0996bd2c7d7453752c3441f99c8a68a97bb1043`: Python 3.11.17/Linux arm64, 1,709 cases in 79 bounded groups, plus exact stable-version installed profiles on macOS and Linux arm64. Earlier macOS embedded bootstrap failures remain disclosed. No accepted Client performance budget is supplied here. [SPIKE-004](../02-design/spikes/SPIKE-004-pydantic-ai-agent-boundary-integration.md) remains historical foundation evidence; the later production JSONL/PostgreSQL, stream, context, worker and live DS4 cases govern their declared support subsets. Summary fidelity and approximate token estimates are workload/model qualified; no universal recall or server-acceptance guarantee is claimed. Actual OS-crash durability and cross-session business-effect deduplication remain consumer qualification. Downstream platform adoption is a post-release product outcome and is not one of these P0 paths. These limits do not waive any P0 suite.

The [P0 qualification receipt](evidence/p0-qualification-20261028.md) retains the original snapshot and subsequent evidence. Its final addendum records passing selected Q0–Q8 gates, six stable macOS installed profiles, two stable Linux installed profiles, immutable release/source identities, and publication status. Support remains limited to those recorded versions and subsets; release authority came from Erik's explicit direction.

## Build Commands

The complete local release recipe is executable for the implemented subsets. Routine pull-request CI runs `make pr-check` and is not a release gate:

```bash
uv sync --locked
make check
uv build
uv run --locked python scripts/check_distribution.py dist
uv run --locked python scripts/check_distribution.py dist openai
```

The quality recipe collects exact pytest node ids through its public plugin API, refuses an
empty/invalid/duplicate inventory, and executes every node in collection order in groups of
at most 32 cases and 64 KiB of ids. Each subprocess retains the existing 180-second deadline.
This bounds individual jobs while permitting the growing complete suite to finish; it is not
a Client latency budget. Coverage is erased before collection, accumulates branch/source data
across every successful group, and is checked once with the unchanged configured 85% floor.
Any collection, group or final-report failure fails the recipe. Real negative controls prove
that passing assertions with insufficient branch coverage still fail the overall command.

**Blocking gate**: Every implemented P0 contract path passes its primary suite and relevant process/store qualification; boundary negative controls reject planted forbidden imports. Core substitution, vendor-type leakage, private imports/cycles and extension trust obligations receive explicit review. Missing tests, failed commands or missing profile evidence remain failures, never skips or weaker assertions. Exact commands/markers for story suites are added with their implementation, not invented as passing checks here.

## Diagnostic Test Strategy

Use fabricated source/summary/argument markers to prove privacy before any sink, including context-failure and unknown-effect paths. Inject sink/export failures and enforce configured bounds without blocking the turn indefinitely. For a selected real OpenTelemetry adapter, send traces through a real receiver and verify nesting, current actor/request correlation by identifiers, usage purpose, and no duplicate ingestion. Capture source versions, store capabilities, model profile/estimator versions, strategy, fault point, expected/observed effect state, result, diagnostic capture limits and measured runtime/memory. Context source content and operator resolution reasons are not span/audit attributes.
