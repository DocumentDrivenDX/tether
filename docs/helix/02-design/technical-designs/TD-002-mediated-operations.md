---
ddx:
  id: TD-002
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-002
      kind: informed_by
    - id: SD-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-004
      kind: informed_by
---

# Technical Design: Mediated Operations

## Scope

Realizes [[US-002]] under [[SD-001]]; acceptance references US-002-AC1 through US-002-AC4. The governing story owns the observable criteria; contracts own exact interfaces.

FR-54/55/59 and C-03. Apply ADR-011/014/018 and the existing public Agent runner. Application ingestion, scheduling and business schemas remain outside core. CONTRACT-001/002/003 own the new surfaces.

## Technical Approach

The existing OwnedTurns task owner admits every operation. CommandTurns shares its jobs, capacity, acceptance future, current authorization, lease, heartbeat supervision, cancellation and keyed retry lookup. It checks a durable key again under the lease. Fingerprints include the command origin and all submitted inputs; a send and a tool command cannot reuse a request key with different meaning. The accepted operation retains its chosen specification and complete registry generation.

A direct call accepts a tool_command history reference and manifest without an assistant/model entry. ToolExecutor runs the existing source, normalization, provider, current policy, approval and effect gates. Approval-required direct calls record refusal before intent. Tool results use a distinct journal kind so they cannot become orphan tool messages in later model context. Explicit recovery remains model-free, reuses the original invocation and only replays eligible same-intent calls. Unresolved settlements require reconciliation first.

Preparation starts after send deduplication/acceptance, under the same supervised lease. Its waiting and terminal metadata are committed. Workers retains actual hook completion through cancellation; read/retrieval authorization protects source and derived message references. Input identity is compared after the hook; source content is reverified. Only known accepted ready state may create the first model request.

Auxiliary work pins an explicit tool-free profile and source frontier. It reads source-addressed original messages under current policy, bounds accumulated source bytes and estimates the complete payload using the configured estimator/capacity. JournalAttemptRecorder repeats source/read/model and live caller credential checks at each physical start. Profile attempt limits are enforced at admission. The result has its own journal kind, purpose and usage; applying it to application metadata is separate authorized work.

## Component Changes

- `types.commands/preparation/auxiliary`: neutral immutable inputs and versioned companion ports.
- `sessions.commands`: bounded admission shared with OwnedTurns; no extra scheduler.
- `client.operations`: owner-bound command facade.
- `engine.commands/preparation/auxiliary`: existing Governance, Workers and ToolExecutor mediation.
- `engine.resuming`: distinguish direct operation recovery from Agent recovery; auxiliary/transition/reconciliation are not reissued as answer requests.
- `sessions.outcomes`: bounded hydration of accepted operation results.

`check_layers.py` forbids engine imports of stores, application adapters or provider SDKs. The existing canonical gate enforces its negative controls, strict typing and source ceilings.

## Security

No credential enters fingerprints, journal facts or telemetry. Caller copies retain the live credential provider. Source access and model authority are current; profile selection cannot grant application identity. A new request cannot clear an unknown effect or a settlement decision. Derived preparation references require matching owner/session, canonical digest, finite bytes and separate retrieval authorization. Extension callbacks are trusted application code; the runtime retains their actual workers, not arbitrary detached tasks they create.

## Testing

No formal-methods practice is selected for this slice. Executable implementation, refusal, restart and process-frontier cases supply bounded evidence; they are not a proof of arbitrary backend or power-cut behavior.

- Public durable Client deduplication/conflict/reopen and approval-required zero dispatch.
- Preparation ready/needs-input/refused, cancellation before egress and request identity preservation.
- Caller credential absent or revoked between physical attempts; no second egress.
- Direct recovery before intent and edited approval recovery through actual JSONL root reopen.
- One fabricated public Client integration combines these operations with settlement, registry and transitions.

## Migration & Rollback

New optional ports are additive. Existing send/confirm and MessageHistory/HistoryProvider implementations remain usable. Codecs keep opaque journal facts and original content. New command results are excluded from answer transcript projection. Disable new application command paths to roll back; do not delete accepted facts or repeat effects under a new key.

## Diagnostic Wiring

All calls share the Engine observer owner, closed metadata registry and actual Worker lifetime. Naming is configurable at GuardedTracer. New command facts carry no tool text or credential in operational telemetry. RecordingTracer evidence proves port naming/propagation; it is not exporter/receiver qualification.
