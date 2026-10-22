---
ddx:
  id: ADR-015
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-010
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-014
      kind: informed_by
    - id: CONTRACT-002
      kind: informs
    - id: CONTRACT-003
      kind: informs
    - id: prd
      kind: informs
---

# ADR-015: An Approval Binds to One Invocation, Its Arguments, and Its Tool Definition, and Satisfies Only the Approval Gate

## Problem

A turn that needs a person's approval ends and waits; a later call answers. The first draft stored a pending confirmation with an undefined hash map and a confirmation entry with only three states. It did not say what an approval is bound to, what happens when two answers race, when a new message arrives while one is pending, when permissions or the tool change in between, whether it expires, or which gates run again. An approval that is too loose approves something other than what the person saw; one that is too tight cannot survive a restart.

## Decision

We will bind an approval to one invocation, the exact arguments the person was shown, and the tool's definition at that time, record the whole lifecycle in the log, and run every other gate again before the tool executes.

- **What it binds to.** The session, the `invocation_id` (ADR-014), an `args_digest` (a hash of the canonical JSON of the arguments shown), a `tool_digest` (a hash of the tool's name, input schema, replay declaration, and always-ask flag, computed for every tool), and the approving actor's id. The credential is never recorded.
- **Single use.** One approval covers one invocation. A safe tool rerun on resume uses the same approval; an unsafe tool is not rerun at all.
- **Only the approval gate.** An approval satisfies the approval step and nothing else. After an answer the engine re-enters the gate sequence at the first step: the `CALL_TOOL` policy decision, the digest checks, the provider `before_call` hooks, and argument validation. Changed permissions deny the call. A digest that no longer matches ends it as denied with reason `approval_invalid`.
- **Who may approve.** `APPROVE` is a policy action decided on the answering actor. The default policy lets the session's owner approve.
- **States, recorded in the log.** `requested`, `approved`, `declined`, `superseded`, `expired`, `invalid`.
- **Declined.** A decline becomes a denied tool result (reason `declined`, with an optional note) and the turn continues.
- **Superseded.** A new message sent while an approval is pending supersedes it. Each pending call gets a denied result with reason `superseded`, so the transcript stays valid, and the new turn starts. (Operator decision, Erik LaBianca, 2026-10-22.)
- **Expiry is optional.** An application may set `approval_ttl`; no default is proposed. It is checked lazily against the clock port when an answer arrives, which appends `expired`.
- **Duplicate and concurrent answers.** The answer is appended under the key `approval:<invocation_id>`. The store keeps one; the loser receives `None`, and both attempts are audited.
- **Across a restart.** The pending state is in the log, and a waiting session holds no lease, so it survives a restart. The answerer presents a live actor (ADR-011); no credential is stored.
- **After a crash between approval and intent.** Resume continues without asking again, provided the digests and the gates still pass.

## Why We Chose This

### Over binding to the model's call id only
It can repeat across replies, and it says nothing of the arguments or the tool, so an approval could be applied to a different call.

### Over a standing grant for the session
It removes the prompt for every later call, which is the opposite of what a person who approves one action expects.

### Over asking again on resume
It is safe, and it makes an approval useless across a crash, which is the case the approval most needs to survive.

## Consequences

**Good**: A person approves what they saw, and only that. An approval is auditable and survives a restart. A race has one winner.

**Trade-offs**: A tool whose schema changes between request and answer must be approved again. Computing digests costs a hash per request and answer. A superseding message discards a pending approval, which a person may not expect; the denied result tells them.

**Drift signals to watch**: An approval satisfying any gate but its own. An answer applied without the digest check. A credential in a confirmation entry. A second answer to one invocation accepted.

## Concern Impact

- **No concern impact**: This applies the `security-owasp` concern (no confused deputy) and the `resilience` concern.
