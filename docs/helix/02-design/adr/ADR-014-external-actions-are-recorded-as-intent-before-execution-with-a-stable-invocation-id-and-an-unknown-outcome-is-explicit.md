---
ddx:
  id: ADR-014
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-005
      kind: informed_by
    - id: ADR-010
      kind: informed_by
    - id: ADR-015
      kind: informed_by
    - id: ADR-016
      kind: informed_by
    - id: CONTRACT-002
      kind: informs
    - id: CONTRACT-003
      kind: informs
    - id: prd
      kind: informs
---

# ADR-014: External Actions Are Recorded as Intent Before Execution, With a Stable Invocation ID, and an Unknown Outcome Is Explicit

## Problem

A tool runs before its result is committed to the session log. A crash after the tool acted and before its result was recorded leaves the outcome uncertain. The log's idempotency keys (CONTRACT-003) stop a duplicate *entry*; they do not stop a duplicate *external action*. Three gaps follow from the first draft of the resume planner (ADR-010). The log holds no record that a tool was about to run, so the planner cannot tell a call that never started from one that started and has an unknown outcome. The call id the model supplies can repeat across replies, so it cannot serve as an identity for the invocation. And the draft promised that each committed step appears once, which a reader can mistake for a promise about external effects. A deployed agent platform's own documentation makes the same point: recovery "can run" agent code more than once, so external calls should be idempotent, and its pages say nothing about skipping tool calls that already completed.

## Decision

We will give every tool invocation a stable identity, commit an intent entry before an effectful tool runs, derive the execution state of each invocation from the log, and report an outcome the library cannot determine as explicitly unknown. We will not claim exactly-once external effects.

- **Invocation id.** The engine derives `invocation_id` from the session id, the index of the committed assistant entry that holds the call, and the call's position in it. It is identical on every attempt and across resume, and it is independent of the model's call id. A tool reads it as `ToolContext.invocation_id` and may pass it downstream as an idempotency key.
- **Intent before effect.** For every tool declared effectful, even if replay-safe, after the gates and any approval have passed (CONTRACT-002 section 8), the engine commits a `tool_call` entry with status `started` (the invocation id, the tool and argument digests, and the approval's position) under the key `intent:<invocation_id>`, before the tool runs. If that commit fails or the lease is lost, the tool does not run. The commit is the fencing point.
- **Effect and replay are independent.** Every effectful tool writes an intent, including a writer idempotent under the same invocation id. Only tools declared both read-only and replay-safe omit intent. A read-only replay-unsafe call also records intent so recovery can honor its no-automatic-repeat declaration. Replay safety does not make a new invocation effect-free; an unresolved effect blocks every new effectful invocation.
- **Derived execution state.** Each invocation is in one of four states, read from the log and never stored separately: *not started* (the call is in an assistant entry, with no required intent and no result), *started* (an intent and no result), *finished* (a result), *resolved* (a `resolved` entry from reconciliation or a person).
- **Resume rules.** A not-started call runs fresh through every gate. A started call is rerun with the same invocation id if the tool is safe; otherwise the engine appends a result with status `interrupted` and reason `outcome_unknown` and does not rerun it automatically. A finished or resolved call is left alone.
- **A failed result append after the tool ran is the same state as a crash.** The engine reports the outcome as unknown.
- **Timeouts.** `Tool.timeout_seconds` is declared by the tool author and enforced by a cancel scope. A timeout after effectful intent is an unknown outcome, including a replay-safe writer. A plain function running in a worker thread cannot be cancelled and is abandoned (ADR-001), which is also an unknown outcome.
- **Unresolved-effect barrier.** An abnormal exit after effectful intent, including a tool exception, is unknown unless authoritative evidence proves the outcome. Every unresolved effectful invocation blocks every subsequent effectful invocation in the session, regardless of name, arguments or new model call id. Approval cannot clear the barrier. Read-only calls/dialogue may continue after any same-process worker has exited. The barrier is derived from the log and survives a restart.
- **Manual resolution is P0.** A present actor uses `Client.resolve` with an outcome and recorded reason under `RESOLVE_EFFECT` authorization and the session lease. Identical retries are idempotent; conflicting outcomes are refused. A still-running same-process worker refuses resolution. Resolving an outcome does not run another operation or authorize one.
- **Reconciliation is an optional P1 adapter.** A tool may supply a read-only check returning completed, not completed or unknown. It uses mediated retrieval; unknown leaves the barrier. Completed/not completed records resolution; a later execution is a new invocation through every gate and its own approval.
- **Guarantee boundary.** Session-wide refusal avoids guessing semantic equivalence of arbitrary calls. It does not deduplicate another session or fence a remote effect; global operation keys and target fencing remain application obligations.
- **What `replay` guarantees.** `safe` is the tool author's assertion that rerunning with the same invocation id converges: the tool is read-only, or its downstream is idempotent by that key. `unsafe` means tether never reruns it automatically. Neither is a guarantee about the downstream system.
- **Stated guarantees and non-guarantees.** Tether records each committed entry once. On a store that declares durable commits and fenced leases (ADR-005), it runs an unsafe tool at most once automatically and a safe tool at least once, and it says "outcome unknown" when it cannot tell. It does not provide exactly-once external effects; that needs support from the downstream system.

## Why We Chose This

### Over recording intent for every call
It is uniform, and a read-only replay-safe call then pays an extra durable commit for no additional recovery distinction, since it may be rerun under the same invocation id.

### Over an external durable-execution engine
ADR-010 rejects it for the same reasons here: it adds a service to every deployment, and the engines themselves rerun an in-flight call, so idempotency stays the tool's job.

### Over claiming exactly-once execution
No library can deliver it for an action it does not control. A claim a crash disproves is worse than an explicit unknown.

### Over recording results only (the first draft)
It cannot tell a call that never started from one that did, so it either reruns an effect that happened or reports unknown for one that did not.

## Consequences

**Good**: A crash after an external action is reported as what it is. A tool that can use an idempotency key has a stable one. The planner is pure and testable over log prefixes that now include intents.

**Trade-offs**: One extra durable commit per effectful or replay-unsafe call; the latency budget is the owner's to set after the baseline (PRD NFR-7). A call whose intent was committed and whose tool never ran is also reported unknown, conservatively; reconciliation or a person resolves it. The session-wide effectful-call barrier may block unrelated writes until resolution, deliberately trading availability for an explicit decision. A tool author must declare `replay` honestly.

**Drift signals to watch**: A tool that runs before its intent commits. An unsafe tool rerun automatically. The phrase "exactly once" applied to effects. The model's call id used as a downstream key. A planner that reads anything but the log and the tool declarations.

## Concern Impact

- **No concern impact**: This applies the `resilience` concern (idempotent recovery with an explicit unknown state).
