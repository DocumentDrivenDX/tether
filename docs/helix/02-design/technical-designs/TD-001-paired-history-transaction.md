---
ddx:
  id: TD-001
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-001
      kind: informed_by
    - id: SD-001
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
---

# Technical Design: Paired History Transaction

## Scope

Realizes [[US-001]] under [[SD-001]]; acceptance references US-001-AC1 through US-001-AC4. The governing story owns the observable criteria; contracts own exact interfaces.

FR-61 and the shared-history foundation of C-02. Inherits SD-001, ADR-018 and CONTRACT-003/005. Application settlement builds on this commit participant; it is qualified separately.

## Technical Approach

Bind a turn-local history view when the message adapter and PostgreSQL journal share the exact verified DatabaseRoot. Stage immutable canonical records in bounded memory and return deterministic references. Reads within that view may resolve staged content; independent readers see only committed content. Ordinary standalone history writes keep their existing explicit content-before-reference behavior.

At each journal append, select only staged records actually referenced by that batch, including nested exchange/compaction references. Stage their canonical bytes in the existing fenced transaction before journal apply/publish. The existing owner-head/session locks, current caller acquisition, generation/holder fence and final fence protect all content, references, journal, summary and feed. A rejected or cancelled transaction publishes none. A successful transaction drops only the accepted staged records after verified local projection publication.

A version-1 history_batch receipt binds the complete journal input digest, actor scope, stable operation key, original expected frontier and accepted entry positions. Unkeyed batches derive identity from every declared nested history address. Equal retries read and validate the original positions, including after reopen; they cannot append unkeyed entries again. Unsupported or malformed receipt shapes refuse explicitly. Stable history keys/digests join that retry identity. Exact retries return requested entries without a new receipt/feed event; conflicts refuse. Conflicting history bytes or journal input refuse and roll back both. Unknown commit acknowledgment poisons the leased handle; reopen and current-authorized key lookup determine acceptance. A failed projection/heartbeat after commit remains unknown. No second write is attempted through a poisoned view.

This avoids an ambient connection/context variable and avoids holding a transaction across model or application awaits. It adds no schema migration. The tradeoff is bounded temporary memory for records awaiting acceptance. Different roots retain independent commits by explicit composition; a requested shared profile refuses a mismatch rather than silently falling back.

## Component Changes

- `types.transactions`: optional companion protocols for pure turn-local binding; no database types.
- `history.messages`: bind the message view and its full HistoryProvider together.
- `engine.agent`: use the bound message/full-history pair for one leased operation, including model-attempt capture and context.
- `stores.postgres.paired`: bounded staged content and reference selection.
- `stores.postgres.history`: reuse immutable insertion on a supplied adapter-owned connection.
- `stores.postgres.handle`/`leases`: enlist selected records in the current fenced append transaction.
- `stores.postgres.atomic`/`batches`: bounded whole-batch identity, accepted-position receipts and exact retry materialization.

The module map remains unchanged. `scripts/check_layers.py` enforces engine-to-neutral-types and adapter-only PostgreSQL imports; no generic ToolContext connection is added.

## API/Interface Design

CONTRACT-003/005 own the paired profile, optional turn-binding companion, finite staging limits and unknown/refusal behavior. Old HistoryProvider/TurnLog implementations remain valid and retain content-before-reference semantics. New companion protocol version is independent of existing record codecs.

## Security

The staged view is scoped to the leased actor/session. Every call checks the live handle and exact root; actor changes and cross-session keys refuse. No content is placed in journal facts or telemetry. Current policy remains an Engine responsibility; database acquisition uses the caller already bound to the lease.

## Testing

No formal-methods practice is selected for this slice. Executable implementation, refusal, restart and process-frontier cases supply bounded evidence; they are not a proof of arbitrary backend or power-cut behavior.

- Real database observers cannot see staged content before accepted references.
- Inject failure after history insertion and after journal staging; assert no partial content, journal, summary or feed.
- Equal/conflicting keys, byte/count limits, wrong actor/session/root, expired lease and cancelled append.
- Lost acknowledgment: poison old handle, reopen and prove keyed acceptance without duplicate content/facts.
- Public Agent composition proves message, exchange and summary records use the paired view.
- Independent processes test the before-commit and after-commit frontiers; process evidence does not establish power-cut durability.

## Migration & Rollback

No table or codec migration. Companion capability is additive. Explicit independent composition disables staging. Do not revert journal facts or delete accepted history when disabling the profile.

## Implementation Sequence

1. Add binding companions and bounded adapter staging with insertion reuse.
2. Enlist selected records in fenced commits and bind Engine operations.
3. Exercise real database refusal/failure/frontier tests and existing Agent profiles.
4. Build settlement participants on the verified transaction boundary.

## Settlement Composition

ToolOutput may name an application-owned stage. Result guards and after-call hooks determine the final delivered message before its digest/decision is bound. The existing effectful intent must already be accepted. A withheld result selects rollback. SettlementBinding pins invocation, stage/participant, result digest, full accepted specification and current actor/principal; it never stores a credential.

For independent settlement, persist the decision and planned result references before invoking the idempotent participant. Known matching outcome publishes result plus terminal settlement state; failure or lost acknowledgment retains uncertainty and the original effect barrier. Reconciliation reads that same durable binding under present resolve authority. It cannot pick a new decision, actor or settlement id. Generic effect resolution cannot discard a pending settlement.

For shared settlement, PostgresSettlementParticipant provides a fixed set of named SQL operations and an asynchronous callback. The adapter-owned capability bounds operation count/parameter bytes and seals after the callback. ToolContext receives no connection. Application rows, selected history, decision, result, summary and feed commit under the same root, actor and fence. Existing accepted keys prevent duplicate participant application on exact retry. Different roots/unsupported participants refuse; no authority fallback or automatic migration occurs.

The declared shared profile requires the exact DatabaseRoot object, including its caller acquisition policy. Database namespace equality alone cannot prove that distinct connection sources have equivalent authority. Independent profiles remain explicit when roots differ.

Process tests kill peers at content staging, participant SQL, journal staging before commit, and after commit before acknowledgment. Independent-settlement tests use two separate transactions against a fabricated application table, kill on either side of its commit, then reconcile the same id. These prove the selected transaction frontiers; they do not prove power-cut durability, arbitrary remote-service idempotency or exactly-once external effects.
