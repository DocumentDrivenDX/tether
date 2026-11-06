---
ddx:
  id: TD-066
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-066
      kind: informed_by
    - id: FEAT-014
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: STP-001
      kind: informs
    - id: TP-002
      kind: informs
---

# Technical Design: TD-066 — HTTP and Remote Session Parity

**User Story**: [[US-066]] | **Feature**: [[FEAT-014]] | **Solution Design**: [[SD-002]]

## Scope

Implement the HTTP boundary and optional Python remote `ClientProtocol` for [US-066](../../01-frame/user-stories/US-066-http-and-remote-session-parity.md). Inherit package ownership and wire semantics from SD-002 and CONTRACT-006. Public shared signatures stay in CONTRACT-006; turn-result persistence or changed cancellation semantics require a CONTRACT-003 update before implementation.

## Technical Approach

**Strategy**: Keep route translation in `tether_web_api`, remote transport in an optional core extra, and all decisions behind the injected public client. The API owns no session table or turn-result cache. It asks the client/store authority for durable state after a restart.

**Key Decisions**:

- Resolve a fresh actor-bound client for each request. Keep FastAPI `Request`, Pydantic wire models, and HTTP exceptions inside `tether_web_api`.
- Generate REST and event types from the checked OpenAPI and event schemas. Reject unknown or malformed input before client invocation.
- Keep send request IDs through acceptance and query accepted turn status by turn ID. Do not automatically replay commands without a contract-defined idempotency key.
- Reconcile result recovery and stale-target cancellation against journal-backed facts. Add a persisted record only through an approved CONTRACT-003 revision; process memory cannot prove completion.
- The remote extra calls an injected credential provider at each request and reconnect and never imports the API server package.

**Trade-offs**: A shared client protocol preserves semantics and enables substitutions, while the adapter must map more individual operations and explicit error states. Durable result reads need a focused store/journal capability; an in-memory shortcut would fail process-restart behavior.

## Component Changes

### Modified: `tether.client`

- **Current State**: `Client` implements local reads and commands; no public protocol-wide remote result lookup or targeted cancellation exists.
- **Changes**: Add the exported protocol and values from CONTRACT-006, implement process-independent `turn_result`, and make cancellation by `turn_id` atomic. Preserve `cancel(session_id)` as its current-active-turn operation.
- **Files**: `src/tether/client/`, `src/tether/types/client.py`, and the smallest store/journal modules identified by the result-recovery spike.

### New: `tether_web_api`

- **Purpose**: Optional FastAPI adapter with injected authenticator/client provider, versioned wire schemas, bounded SSE, and packaged OpenAPI.
- **Interfaces**: Input: FastAPI requests, settings, and injected public clients / Output: validated HTTP/SSE and problem details per CONTRACT-006.
- **Files**: `src/tether_web_api/factory.py`, `routes/sessions.py`, `routes/turns.py`, `routes/events.py`, `auth.py`, `wire/`, and `openapi.json` (generated).
- **Boundary**: Extend `uv run python scripts/check_layers.py` with negative controls that forbid API imports from `tether.client` and `tether_web_ui`, and forbid runtime/storage imports from `tether_web_api`.

### New: remote `tether.client` extra

- **Purpose**: Implement the same protocol from an explicit endpoint for Python embedding and TUI reuse.
- **Interfaces**: Input: base URL and credential provider / Output: `ClientProtocol` values, errors, and handles.
- **Files**: `src/tether/client/remote/`, optional dependency group and `tests/client/remote/`.

## API/Interface Design

| Surface | Governing Contract | Story-Level Usage |
|---------|--------------------|-------------------|
| Client host/protocol, `turn_result`, targeted cancel | CONTRACT-006 and CONTRACT-003 | API depends only on public protocol; remote client passes the same conformance suite |
| REST, acceptance and idempotency | CONTRACT-006 | Route handlers map each protocol method and preserve command-specific reconciliation |
| SSE event union and resume IDs | CONTRACT-006 and CONTRACT-003 | Event adapter emits durable IDs only at commit boundaries and keeps attempt text provisional |
| Authenticator/client provider | CONTRACT-006 | Host app supplies current identity and authorization per request/batch |

## Data Model Changes

No new API-local source of truth is allowed. First verify whether existing journal facts can answer `(session_id, turn_id)` after process replacement. If not, revise CONTRACT-003 to add the minimal durable outcome record and recovery rules before schema/code work. Any store migration must be additive, versioned, restart-safe, and retain unknown external-effect outcomes.

## Integration Points

| From | To | Method | Data |
|------|----|--------|------|
| Browser or remote Python client | `tether_web_api` | REST/SSE | Versioned wire schema and explicit request IDs |
| `tether_web_api` | `ClientProtocol` | Direct async calls | Transport-neutral values and errors |
| Auth integration | `tether_web_api` | Injected authenticator | Current actor, credential, and policy context |
| Remote client | API endpoint | Async HTTP | Same public client semantics and explicit credentials |

### External Dependencies

- **Application authenticator**: Supplied by the embedding host. Denial, expiry, or unavailable identity fails closed without broadening owner scope.
- **HTTP transport**: Optional remote extra. Connection failure preserves the request ID and leaves command outcome unresolved until an authorized result read.

## Security

- **Authentication**: Required per request and stream reconnect through the application authenticator.
- **Authorization**: Check owner/session permission for every route and bounded stream batch; require payload permission for raw exchanges.
- **Data Protection**: Never log credentials, bodies, tool arguments, or model payloads. Return no protected content after revocation.
- **Threats**: Cross-owner ID guessing, stream revocation races, replayed sends, ambiguous acceptance, stale cancellation, and oversized event queues. Use scoped client resolution, durable request IDs, atomic turn targeting, and configured finite limits.

## Performance

- **Expected Load**: Unknown until the PRD benchmark supplies representative session, event, and subscriber volumes.
- **Response Target**: No new latency target is set here; API translation must preserve configured client/page/time limits.
- **Optimizations**: Bounded async event batches, no process-local outcome cache, and no blocking model wait in send acceptance.

## Testing

- [ ] **Unit**: `tests/client/test_protocol_conformance.py`, `tests/client/test_turn_result.py`, `tests/client/test_cancel_turn.py`; cover unknown vs pending vs terminal result and stale IDs.
- [ ] **Integration**: `tests/api/test_restart_recovery.py`, `test_send_idempotency.py`, `test_current_policy.py`, and `test_event_commit_ids.py`; run against a disposable real store and a restarted API process.
- [ ] **Contract**: `tests/api/test_protocol_route_parity.py` maps every protocol method to route/capability; `tests/client/remote/test_remote_conformance.py` uses the local API and injected credentials.
- [ ] **Security**: `tests/api/test_stream_revocation.py`, `test_payload_permission.py`, `test_csrf_cors.py`; verify no payload after revocation and no secret in diagnostics.
- [ ] **Acceptance mapping**: `US-066-AC1` route/capability parity; `AC2–3` acceptance and retry; `AC4` commit resume; `AC5` revocation; `AC6` API restart result; `AC7` stale-target cancellation; `AC8` remote client without FastAPI dependency.

## Migration & Rollback

- **Backward Compatibility**: Keep current in-process `Client` behavior and `Tether(...)` construction path. Add public protocol methods only with a CONTRACT-003 revision and compatibility tests.
- **Data Migration**: None unless the result-recovery spike demonstrates missing durable facts; any required change must be additive and migration-tested first.
- **Feature Toggle**: Keep server and remote transport as optional extras; consumers opt in by installing and mounting them.
- **Rollback**: Unmount API and remove optional extra while leaving core client/store data readable. Do not delete durable outcome records or downgrade a store that contains unknown effects.

## Implementation Sequence

1. Prove durable accepted/result facts and atomic targeted cancel in `tests/client/`; update CONTRACT-003 if the current store contract is insufficient. Register the API/client package boundary rules and negative controls in this first implementation task.
2. Implement and conform the public client methods in `src/tether/client/`; keep API dependent only on exported types.
3. Add wire schemas and API mapping under `src/tether_web_api/`, then remote transport under `src/tether/client/remote/`.
4. Run the restart, route parity, policy, SSE, remote conformance, and security suites; retain the OpenAPI compatibility report and restart evidence.

**Prerequisites**: FEAT-013/TD-065 public host and protocol; CONTRACT-006; result-recovery and targeted-cancel gates in IP-002/TP-002.

## Risks

| Risk | Prob | Impact | Mitigation |
|------|-----|--------|------------|
| Journal lacks enough facts to recover a terminal result | M | H | Resolve with an explicit spike and contract/store revision before API acceptance |
| Authorization changes after a command commits | M | H | Return no protected data; separate command outcome from current read permission |
| Browser SSE advances across a partial commit | M | H | Emit `id` only on `last_in_commit`; real-browser reconnect test |
