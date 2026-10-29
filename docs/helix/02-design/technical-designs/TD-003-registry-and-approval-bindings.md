---
ddx:
  id: TD-003
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-003
      kind: informed_by
    - id: SD-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
---

# Technical Design: Registry and Approval Bindings

## Scope

Realizes [[US-003]] under [[SD-001]]; acceptance references US-003-AC1 through US-003-AC4. The governing story owns the observable criteria; contracts own exact interfaces.

FR-57/58/60 and C-04, using C-03's leased command admission. Apply the current policy, source, approval and physical-worker authorities. No provider discovery or application engagement logic is added.

## Technical Approach

Registry validates a complete generation before publication. Declaration snapshots copy metadata/traits; the generation digest includes tool definition digests, visibility and timeout. The mapping is immutable. Trusted implementation code remains application-owned, with each actual callable captured by Workers at dispatch. Mutating a declaration outside publication is detected before use; this does not claim to sandbox trusted Python code or freeze its business state.

RegistryPublisher publishes without an await. An admitted turn/operation pins one generation; concurrent publication affects later admission. Active completions, pending approvals and unresolved local effects retain required generations. Finite definition, tool/generation and unresolved-effect binding bounds refuse capacity rather than evicting live bindings. Current policy remains current. Pending inspection and confirmation reject removed/replaced definitions.

Specification transitions use an explicit current-policy command after the prior operation's completed call groups. Active turns, pending calls and unresolved effects refuse. One commit binds the expected old digest, new id/version/digest and registry generation with metadata/change publication. The next admitted request uses the accepted new specification. This implementation does not queue a mid-run transition or mutate an active Agent's tool manager.

Edited approval invokes an optional tool-owned semantic transform. The original proposal is unchanged. Separate canonical history records retain exactly shown input and normalized executable values; independent digests enter the accepted approval binding. The edit and approval decision commit together. Before intent, source checks, normalization, transform revision/determinism, providers and current policy run again. A structural JSON subset is never treated as semantic permission. Once intent exists, edits refuse. Restart reloads the accepted executable digest and invokes the same source identity.

## Component Changes

- `engine.registry`: immutable declaration generations, atomic publication, bounded retention.
- `engine.agent`: operation-local generation pin; no actor/credential in the registry ContextVar.
- `engine.transitions`: expected-old/new binding and completed-group refusal.
- `types.approval_edits`, `engine.edits/confirmation_edits`: semantic transform port and atomic approval batch.
- `types.execution`: optional generation and independent argument binding fields.
- `engine.inspection/tools`: current declaration verification before disclosure or execution.

The module map and dependency directions are unchanged. `scripts/check_layers.py`, its negative controls, strict typing and the unchanged shape ceilings apply.

## Testing

No formal-methods practice is selected for this slice. Executable implementation, refusal, restart and process-frontier cases supply bounded evidence; they are not a proof of arbitrary backend or power-cut behavior.

- Add/replace/remove, invalid/duplicate generation, immutable mapping/declarations and stale approval refusal.
- Cancel a live worker; publication cannot evict its generation before actual exit and resolution.
- Transition updates snapshot/next request; equal command retries do not call a model; stale/unsettled transitions refuse.
- Semantic count narrowing, expansion/mutation/exception/drift refusal; original, shown and executable digests differ as expected.
- Close/reopen a real JSONL root after edited approval and before intent; a new Engine executes only accepted edited values.

## Security

Registry publication is trusted composition, not a grant of tool authority. Every execution rechecks current policy and original source bindings. An approval never grants read/model/application identity. Edit records contain content only through HistoryProvider; telemetry retains identifiers/reasons. Applications must version executable semantics and retain compatible generations across their own process deployments.

## Migration & Rollback

Optional fields are omitted when absent, preserving legacy payload representations. Existing approvals without edits retain their old digest semantics. Retain original specifications when reopening old work. Disable publication/transitions/edit participants to roll back feature use; preserve accepted bindings and uncertainty. Capacity exhaustion is an explicit refusal, not a retention eviction policy.
