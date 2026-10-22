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

We will give every tool invocation a stable identity, commit an intent entry before an unsafe tool runs, derive the execution state of each invocation from the log, and report an outcome the library cannot determine as explicitly unknown. We will not claim exactly-once external effects.

- **Invocation id.** The engine derives `invocation_id` from the session id, the index of the committed assistant entry that holds the call, and the call's position in it. It is identical on every attempt and across resume, and it is independent of the model's call id. A tool reads it as `ToolContext.invocation_id` and may pass it downstream as an idempotency key.
- **Intent before effect.** For a tool declared unsafe to rerun, after the gates and any approval have passed (CONTRACT-002 section 8), the engine commits a `tool_call` entry with status `started` (the invocation id, the tool and argument digests, and the approval's position) under the key `intent:<invocation_id>`, before the tool runs. If that commit fails or the lease is lost, the tool does not run. The commit is the fencing point.
- **A tool declared safe to rerun writes no intent.** It is rerun on resume in any case, so an intent adds a commit and no information.
- **Derived execution state.** Each invocation is in one of four states, read from the log and never stored separately: *not started* (the call is in an assistant entry, with no intent for an unsafe tool and no result), *started* (an intent and no result), *finished* (a result), *resolved* (a `resolved` entry from reconciliation or a person).
- **Resume rules.** A not-started call runs fresh through every gate. A started call is rerun with the same invocation id if the tool is safe; otherwise the engine appends a result with status `interrupted` and reason `outcome_unknown` and does not rerun it automatically. A finished or resolved call is left alone.
- **A failed result append after the tool ran is the same state as a crash.** The engine reports the outcome as unknown.
- **Timeouts.** `Tool.timeout_seconds` is declared by the tool author and enforced by a cancel scope. A timeout on an unsafe tool is an unknown outcome. A plain function running in a worker thread cannot be cancelled and is abandoned (ADR-001), which is also an unknown outcome.
- **Reconciliation and manual recovery** are optional. A tool may declare `reconcile(ctx, args, invocation_id)`, which returns completed (with a result), not completed, or unknown, and the engine calls it for a started unsafe call at resume. A present, authorized caller may record the outcome with `Client.resolve`. Both are P1; the `resolved` state is reserved now.
- **What `replay` guarantees.** `safe` is the tool author's assertion that rerunning with the same invocation id converges: the tool is read-only, or its downstream is idempotent by that key. `unsafe` means tether never reruns it automatically. Neither is a guarantee about the downstream system.
- **Stated guarantees and non-guarantees.** Tether records each committed entry once. On a store that declares durable commits and fenced leases (ADR-005), it runs an unsafe tool at most once automatically and a safe tool at least once, and it says "outcome unknown" when it cannot tell. It does not provide exactly-once external effects; that needs support from the downstream system.

## Why We Chose This

### Over recording intent for every call
It is uniform, and a read-only call then pays an extra durable commit for no information, since it is rerun anyway.

### Over an external durable-execution engine
ADR-010 rejects it for the same reasons here: it adds a service to every deployment, and the engines themselves rerun an in-flight call, so idempotency stays the tool's job.

### Over claiming exactly-once execution
No library can deliver it for an action it does not control. A claim a crash disproves is worse than an explicit unknown.

### Over recording results only (the first draft)
It cannot tell a call that never started from one that did, so it either reruns an effect that happened or reports unknown for one that did not.

## Consequences

**Good**: A crash after an external action is reported as what it is. A tool that can use an idempotency key has a stable one. The planner is pure and testable over log prefixes that now include intents.

**Trade-offs**: One extra durable commit per unsafe call; the latency budget is the owner's to set after the baseline (PRD NFR-7). A call whose intent was committed and whose tool never ran is also reported unknown, conservatively; reconciliation or a person resolves it. A tool author must declare `replay` honestly.

**Drift signals to watch**: A tool that runs before its intent commits. An unsafe tool rerun automatically. The phrase "exactly once" applied to effects. The model's call id used as a downstream key. A planner that reads anything but the log and the tool declarations.

## Concern Impact

- **No concern impact**: This applies the `resilience` concern (idempotent recovery with an explicit unknown state).
