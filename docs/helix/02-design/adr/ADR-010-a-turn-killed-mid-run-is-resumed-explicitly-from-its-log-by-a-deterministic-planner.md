---
ddx:
  id: ADR-010
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-015
      kind: informed_by
    - id: ADR-014
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: competitive-analysis
      kind: informed_by
    - id: CONTRACT-002
      kind: informs
    - id: CONTRACT-003
      kind: informs
    - id: prd
      kind: informs
---

# ADR-010: A Turn Killed Mid-Run Is Resumed Explicitly From Its Log by a Deterministic Planner

## Problem

Sessions are append-only logs committed per step (ADR-005), so a turn that dies with its process leaves a consistent log that ends mid-turn. The question is what the library does next. Pi Durable resumes unfinished tasks from checkpoints; Pydantic AI integrates external durable-execution engines (Temporal, DBOS, Prefect); the survey ([competitive analysis](../../00-discover/competitive-analysis.md)) shows durability is expected of a durable harness. Three facts constrain the design. A resumed turn must call models and tools as a person, and a credential is never stored (ADR-011), so a library cannot resume on its own unless the application supplies an identity. A tool call in flight at the crash may or may not have taken effect. A model call in flight produced no committed result, and its partial text was never in the log.

## Decision

We will detect an interrupted turn from its expired lease, close it in the log as interrupted, and resume it only by an explicit command from a caller who presents a live identity, continuing from the committed steps through a pure, deterministic planner. Automatic resume is an opt-in that needs an application-supplied identity.

- **Interrupted is not cancelled.** An interrupted turn lost its process or its lease. A cancelled turn was stopped by a person or the application and is never resumed.
- **Detection.** The session service runs recovery when it starts and at a configured interval. Recovery appends `turn_ended` with stop reason `interrupted` to each session whose lease has expired with a turn open, and the summary shows status `interrupted`. Recovery is idempotent.
- **The resume command.** `Client.resume(session_id)` starts a continuation turn: a new turn whose `turn_started` entry names the interrupted turn it resumes. The caller's identity is the actor of the continuation.
- **The planner.** `plan_resume(entries, tools)` is a pure function of the interrupted turn's committed entries and the tool declarations. It reads each tool call in the last assistant message by its derived execution state (ADR-014): a call that is *not started* (no intent entry for an unsafe tool, and no result) runs fresh through every gate; a call that is *started* (an intent and no result) is rerun with the same invocation id if the tool declares `replay="safe"`, and otherwise gets a typed result `interrupted` with reason `outcome_unknown` and is never rerun automatically (this includes a tool that is no longer registered); a *finished* or *resolved* call is left alone. Then the next step, a model call. A turn with only a user message resumes at the model call.
- **Unknowns block new effectful effects.** A recorded `outcome_unknown` remains an unresolved effect even after a terminal interrupted result. The session-wide barrier blocks every fresh unsafe call until authorized manual resolution or a qualified reconciler records the outcome (ADR-014). A new id or approval does not clear it. A still-running same-process worker also blocks replacement turns and resolution.
- **Context reconstruction.** A continuation derives execution from the original log and revalidates source-compatible context records under CONTRACT-004. A summary never marks an invocation finished. Uncommitted summaries/model calls can be repeated and billed again, within the configured limits.
- **Replay is declared per tool.** `Tool.replay` is `"safe"` or `"unsafe"`; the default is `"unsafe"`.
- **Resume passes every gate.** A resume is a policy decision (`Action.RESUME`), and each rerun call goes through the full gate sequence again, because the world may have changed since the crash.
- **Each committed entry appears once; external effects are not promised once.** Entries carry idempotency keys derived from the invocation, so a retry or a crash during resume cannot duplicate a committed entry. The keys do not prevent a duplicate external action: that is the intent entry, the stable invocation id, and the tool author's idempotency downstream (ADR-014). An in-flight `safe` tool may execute twice, by its declaration.
- **A resume chain is bounded.** After a configured number of consecutive interruptions of one turn chain (a proposed default of 3), `resume` is refused with `ResumeLimit`, which stops a crash loop.
- **Automatic resume is opt-in.** With `resume="auto"` the application supplies a function that returns an actor with a live credential for a session, or `None` to skip it. The default is `resume="report"`: the session is marked interrupted and an interface offers a resume action.
- **Tested by prefix.** For every prefix of a completed turn's log, including its intent and approval entries, recovery followed by resume produces a valid completed or explicitly blocked/unknown turn, contains each committed entry of the prefix exactly once, and shows no unsafe tool executed twice.

## Why We Chose This

### Over resuming automatically by default
It needs an identity the library does not store, and it re-executes side effects without a person present. Opt-in automatic resume serves single-user applications and the command-line client.

### Over an external durable-execution engine (Temporal, DBOS, Prefect)
Pydantic AI integrates them, and they provide mature recovery. They add a service or a database to every deployment and the clone-and-try experience, and the session log already holds the state they would checkpoint.

### Over deterministic replay of the whole conversation
Replaying a workflow to rebuild its state needs deterministic steps. Model calls are not, so the log, not a replay, is the state.

### Over no resume (marking the turn interrupted and stopping)
It leaves a person to retype a message after every crash, and a durable harness is expected to do better. The cost of the planner is small once the log exists.

## Consequences

**Good**: A crashed turn is never silently half-recorded. Resume needs no external engine. The planner is a pure function, so it is unit-tested against logs and property-tested against every log prefix. The policy of identity and side effects is explicit.

**Trade-offs**: A tool not declared `safe` is reported to the model as interrupted rather than rerun, and a session-wide barrier prevents a fresh unsafe repeat until authorized resolution. A `safe` tool may run twice, and an interrupted model call may be billed twice. Resume is an extra user action unless the application opts in. The chain limit and the recovery interval need defaults that benchmarks and use will tune.

**Drift signals to watch**: A tool rerun on resume without declaring `safe`. Resume that skips the policy. A credential stored to make automatic resume work. A planner that reads anything but the log and the tool declarations. A cancelled turn offered for resume. A default of `safe` for `replay`.

## Concern Impact

- **No concern impact**: The decision applies the `resilience` concern (idempotent recovery, a bounded retry chain).
