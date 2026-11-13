---
ddx:
  id: TD-068
  authoring:
    home: repo
  links:
    - id: US-068
      kind: informed_by
    - id: FEAT-016
      kind: informed_by
    - id: SD-003
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: CONTRACT-007
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-013
      kind: informed_by
    - id: ADR-020
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: STP-001
      kind: informs
---

# Technical Design: TD-068-Databricks Shared Session Carrier

**User Story**: [[US-068-databricks-shared-session-carrier]] | **Feature**: [[FEAT-016-databricks-apps-carrier]] | **Solution Design**: [[SD-003-web-and-databricks-apps-carrier]]

## Scope

- Story-level design for one reusable Databricks Apps carrier and its complete sample app.
- Inherits the public client and web package boundaries from SD-002/003 and ADR-022.
- CONTRACT-007 owns exact carrier settings, identity, resource, factory and lifecycle surfaces. CONTRACT-006 owns HTTP/API/UI factory interfaces. CONTRACT-003/005 own session, recovery and original-history semantics.
- Test intent maps to US-068 AC1–10 in [[STP-001-web-platform-acceptance]].

## Technical Approach

**Strategy**: Add an optional `tether_databricks` Python distribution that composes one `tether_web_api` FastAPI app and one `tether_web_ui` static asset app around a caller-owned public Tether client provider. Keep platform configuration explicit, map current Apps user identity into the public actor/authenticator contract, and acquire only declared infrastructure resources. Ship a locked `examples/databricks-app` with no customer data or credentials.

**Key Decisions**:
- Keep all Databricks-specific code under `packages/databricks/src/tether_databricks`; composition uses public `tether.client`, CONTRACT-006 factories and CONTRACT-007 types only.
- Construct immutable settings from explicit values or a caller-provided mapping. The example may pass its process environment into the mapping function; library code never reads process environment implicitly.
- Treat forwarded user identity and app service identity as separate authorities. The API authenticator binds human operations to the current user actor; app identity is only for explicitly selected shared resource operations. A separately allowlisted service-principal caller may use the opt-in projected credential header when ingress consumes Authorization. The explicit service profile verifies that bearer with the workspace authority and allowlists its canonical SDK principal. It requires the forwarded username claim to match the verified SDK `user_name`; the bounded forwarded user identifier is opaque IdP metadata and cannot select the actor, principal, database role, or owner. It ignores one unused forwarded access-token value; it never decodes, uses or stores that value. Duplicate credentials and invalid or mismatched identities fail closed.
- Require a declared external store/history profile for durable app operation. Do not claim JSONL app-local files or UC volumes provide journal durability.
- On termination, stop admission and attempt bounded drain/close. Return a truthful report by the configured deadline, retaining in-process ownership of physical workers until they exit; forced kill recovery uses committed client facts and unknown-effect barriers.
- Preserve the committed terminal reason: orderly application cancellation may record `cancelled`; loss of an open turn's process or lease records `interrupted` under ADR-010. Neither reason proves that a physical effect stopped. A started unsafe effect without a proved result or authorized resolution remains an `outcome_unknown` barrier after either path.
- Use `uv.lock`, `pyproject.toml`, bundle config and `app.yaml` in the example. Package browser assets with Python and do not require Node at runtime.

**Trade-offs**: A dedicated optional package gives embedders a reusable, testable identity and lifecycle layer while preserving the base install. It creates another release and compatibility surface, and live Apps, H2C, OAuth, Lakebase, role isolation and shutdown claims remain profile-specific qualification work.

## Component Changes

### New: Databricks carrier distribution
- **Purpose**: Provide typed, framework-compatible Apps identity/resource helpers, one-host composition and bounded lifecycle behavior.
- **Interfaces**: Accept `DatabricksAppSettings`, `ClientProvider`, an `Authenticator`, `ResourceProvider`, API/UI factories and `CarrierLifecycle`; return the single composed ASGI app defined in CONTRACT-007.
- **Files**: `packages/databricks/pyproject.toml`; `packages/databricks/src/tether_databricks/{__init__,settings,identity,resources,lifecycle,composition}.py`; `packages/databricks/tests/`.
- **Module boundary**: `tether_databricks` may depend on the public distributions and documented Databricks APIs. It MUST NOT import `tether.engine`, concrete stores, private adapters or `tether_web_api` internals. Public types are re-exported from `tether_databricks` only when owned by this package.

### New: Complete Databricks example
- **Purpose**: Show a reproducible one-host deployment and external profile inputs without embedding a customer app.
- **Interfaces**: Composition root reads the platform-provided values and explicitly constructs settings, authenticated client provider, resources and app.
- **Files**: `examples/databricks-app/databricks.yml`; `examples/databricks-app/app.yaml`; `examples/databricks-app/pyproject.toml`; `examples/databricks-app/uv.lock`; `examples/databricks-app/src/app.py`; `examples/databricks-app/tests/`.
- **Module boundary**: The example imports public package entrypoints only. The actual embedder stays in a separate fabricated consumer fixture for package proof.

### Modified: Architecture and package evidence
- **Current State**: No Databricks Apps carrier, distribution or live Tether profile is qualified.
- **Changes**: Add optional package closure, asset inclusion, compatibility matrix and named profile evidence to the implementation/release workflow.
- **Files**: `docs/helix/04-build/web-platform-implementation-plan.md`; `docs/helix/03-test/test-plans/STP-001-web-platform-acceptance.md`.

## API/Interface Design

| Surface | Governing Contract | Story-Level Usage |
|---------|--------------------|-------------------|
| Actor-bound client provider, host and shutdown | CONTRACT-003, CONTRACT-006 | Reuse the embedder's one client host; API requests receive a client for the current user actor. |
| Current Apps user identity and app resource principal | CONTRACT-007; CONTRACT-006 `Authenticator` | Convert trusted request context to a public `Actor`; reject absent, expired, conflicting or untrusted user identity. Do not substitute app identity. |
| API factory and HTTP routes | CONTRACT-006 | Compose the public `tether_web_api.create_api` factory and preserve all declared read, command, result and stream semantics. |
| Static UI app, mount and asset path | CONTRACT-006; CONTRACT-007 | Mount packaged assets under the configured prefix and discover the same-origin API without credentials in settings. |
| External database/store/history binding | CONTRACT-003, CONTRACT-005, CONTRACT-007 | Supply an explicitly selected profile, role, migration owner, TLS and current credential acquisition. Do not auto-migrate or choose a wider role. |
| Carrier helpers and example layout | CONTRACT-007, ADR-022 | Implement the proposed public settings, resolver, resource provider, lifecycle report and `create_carrier_app` surface. |

No endpoint, payload, error or adapter signature is redefined here. Exact shared signatures belong to the referenced contracts.

## Data Model Changes

No Tether core data model or journal schema change is in this story. A deployment selects a separately qualified external profile and owner-run migration set. Current user identity and credentials are request-scoped inputs; app/resource credentials stay in platform acquisition and are never copied into Tether session facts, example files or logs.

## Integration Points

| From | To | Method | Data |
|------|----|--------|------|
| Databricks Apps ingress | `DatabricksIdentityResolver` | Trusted request context | Current forwarded user id/email/access token in the human profile; parse only in configured Apps mode. |
| Opt-in service caller | Configured projected credential header and `ServiceCallerAuthenticator` | Verified workspace identity | Verify the explicit caller bearer against the allowlist, require its SDK username to equal the forwarded email-claim, and require a bounded opaque forwarded user identifier without granting that value authority; ignore the distinct ingress access-token value. |
| Identity resolver | `tether_web_api.create_api` authenticator | Typed actor return | Current human or explicitly verified service `Actor` and request-scoped authorization; refresh on each request/batch per CONTRACT-006. |
| Carrier composition | `tether.client` provider | Public Python call | User-bound reads and commands; accepted, pending and unknown outcomes remain typed. |
| Carrier composition | `tether_web_ui.create_static_app` | ASGI composition | Packaged static files, mount path and safe API discovery configuration. |
| Resource provider | Databricks app resources and external database | Explicit acquired lease | Declared scope/role only; current tokens are acquired per qualified connection and not persisted. |
| Bundle deploy | Databricks Apps | Bundle/app configuration | One process, `0.0.0.0`, platform-supplied port, declared app resources and health/readiness behavior. |

### External Dependencies
- **Databricks Apps ingress and identity**: Supplies the configured current user context and app principal. Fallback: fail readiness/request closed; local development uses a separately supplied authenticator and ignores forwarded headers.
- **Databricks Apps runtime**: Supplies the app port and a bounded termination window. Fallback: no carrier claim for another runtime until separately qualified.
- **External PostgreSQL/Lakebase profile**: Supplies journal/store and original-history capability. Fallback: fail closed or mark history unavailable; do not use local filesystem as implicit durable storage.
- **Hypercorn or selected ASGI server**: Candidate serving the one host with H2C support. Fallback: select only a server/profile proven by the exact deployed-listener H2C exchange plus independent live Apps proxy journeys; record their negotiated protocols separately. Generic local protocol support is insufficient.

The qualification-only durable observer leaves candidate launcher/carrier modules byte-identical. It binds a fresh process/nonce, loaded module hashes, observer/probe/bundle hashes and real STOPPED correlation. It records the original report and actual launcher-main return before one bounded external insert; a fresh post-stop reader must observe the committed row. Its scope and outstanding physical work remain explicit. `scripts/web_transport_receipt.py` owns the exact tooling schema and negative controls; this is not a new product API.

Qualification tooling keeps administrative authority separate from application
request authority. Revocation may use an explicitly selected account-SCIM
administrator backed by an existing Azure CLI account-admin session; the
administrator must correlate with the selected primary human, and only the
owned, independently verified test principal may be changed. This grants no
workspace role and supplies no replacement application credential. Retained
cleanup restores the bound target even if a later administrative premise changes.

The development-only PostgreSQL authentication oracle uses pinned asyncpg 0.32.0
to classify a structured authentication refusal after verified TLS. The runtime
connection stack remains Psycopg. Qualification keeps fresh production-stack
connections before and after the stale-credential attempt; a transport, TLS or
unclassified startup failure cannot establish credential rejection. The oracle
is a bounded independent test process, with credentials supplied only through
in-memory input and content-free output.

Log qualification captures two actual streams: APP and the unfiltered platform
stream. It requires nonempty captures, a fresh APP worker invocation and HTTP
records for the matching successful and rejected requests. Scan both captures
and capture diagnostics for fabricated content and credential markers. The
selected platform emits HTTP/BUILD records without SYSTEM records; the evidence
records the sources actually observed and preserves US-068-AC10's app/platform
coverage.

## Security

- **Authentication**: Each protected browser/API request resolves the current Apps user through configured trusted ingress. Local mode requires an explicit local authenticator. A per-request bearer is not stored or copied into browser-visible settings.
- **Authorization**: Actor-bound `tether.client` enforces current Tether policy; stream batches and every message/history disclosure revalidate authorization. App service principal is scoped to explicitly declared infrastructure/resource grants.
- **Data Protection**: Redact authorization and database connection values before logs/spans; keep history and model payloads out of operational telemetry. Use TLS and owner-managed least-privilege DB roles. Never put customer configuration, workspace IDs or data in the sample.
- **Threats**: Spoofed forwarded headers, stale user credentials, app-principal substitution, cross-owner reads, connection role widening, missing migration, proxy-replayed events, asset-path escape and process kill with active workers. Mitigations are the fail-closed resolver, public actor boundary, profile-role checks, bounded stream revalidation, normalized mount settings, and journal-based recovery with explicit unknown effects.

## Performance

PRD NFR-7's 2026-11-09 owner decision makes actual performance qualification
optional and nonblocking for implementation, functional carrier acceptance and
release. The isolated workflow and deterministic admission/rejection/privacy/
cleanup controls remain required. Optional measurements retain their actual
outcome and the existing six-profile/54-metric admission contract; Apps latency
remains observational. This changes no identity, lifecycle, caller deadline,
genuine-expiry or storage support requirement.

- **Expected Load**: No tenant, request-rate or session-volume profile has been supplied; do not invent a capacity claim.
- **Response Target**: No latency SLO is selected here. Measure API operation latency, stream batch latency/size, connection acquisition and shutdown report time in the named deployment profile before performance support claims.
- **Optimizations**: Bound SSE batches and queues; reuse only qualified external connection pools; do not cache authorization across current identity or credential changes.

## Testing

- [ ] **Unit**: Settings validation, duplicate/conflicting identity headers, local mode ignoring forwarded headers, resource binding and report serialization. `@covers US-068-AC3`, `@covers US-068-AC9`.
- [ ] **Integration**: Browser/TUI shared owner session, two-user isolation, app/user principal separation, approval races, log redaction and incomplete close retaining physical-worker ownership. `@covers US-068-AC1`, `@covers US-068-AC2`, `@covers US-068-AC4`, `@covers US-068-AC5`, `@covers US-068-AC10`.
- [ ] **Contract**: API authenticator adapter, factory composition, settings/resource helper compatibility and public-only import boundary. `@covers US-068-AC3`, `@covers US-068-AC9`.
- [ ] **Security**: Missing/expired/revoked user credential, forged ingress, role/RLS refusal and token/content absence from logs. `@covers US-068-AC2`, `@covers US-068-AC3`, `@covers US-068-AC6`, `@covers US-068-AC7`, `@covers US-068-AC10`.
- [ ] **Platform profile**: Same-process deployed-listener H2C capability and independently observed Apps proxy/SSE protocols, OAuth scopes/expiry, migration/role isolation, SIGTERM/SIGKILL and restart against the named external database. `@covers US-068-AC1`, `@covers US-068-AC4`, `@covers US-068-AC6`, `@covers US-068-AC7`, `@covers US-068-AC8`, `@covers US-068-AC9`.

Planned commands and exact AC-to-test assertions are tracked in STP-001 and IP-002. No test or platform profile is represented as executed by this design.

### Managed-process qualification coordination

Apply CONTRACT-007's readiness and physical-ownership qualification rules in
`scripts/selected_profiles_managed_process.py` and
`scripts/selected_profiles_managed_process_peer.py`. These are qualification
helpers; they do not change carrier public APIs or the database lease contract.
For US-068-AC4/AC8 recovery proof, separate command-loop readiness from the
state-established events for a held turn or started unknown effect. The parent
waits for the exact expected event, explicit failed event, process exit or EOF.
A slow import or valid event cannot fail a functional first-output threshold.
Classify each use of the shared `_OPEN_SECONDS` constant independently: peer
readiness, resource acquisition/cleanup and a supplied `StorageLimits.io_seconds`
value are different concerns. Removing the readiness threshold must not silently
change a caller's actual I/O deadline or remove coverage for that deadline.

For natural expiry, first observe the original turn in progress, then observe
the real lease becoming recoverable and a contender taking ownership. Require
stale-owner append/heartbeat refusal and exact committed projections. Keep the
configured lease duration and genuine expiry; remove elapsed-duration assertions
that substitute for those facts. Deterministic deadline and polling tests use a
controlled clock and barriers. An operator emergency watchdog, if necessary,
is separate from these assertions and records incomplete execution.

Prepare both peers' actual SDK/database resources before activating the original
lease. A readiness acknowledgment means resources are verified and the command
loop can act, not merely that the interpreter started. Start the original held
turn only after both peers are ready, then require the contender to observe
`in_progress` against that active lease before allowing natural takeover. Keep
the original owner alive without renewal during the expiry observation. Cold
imports, authentication or connection setup must not consume this initial
30-second lease window. If the required initial state cannot be established,
record inconclusive evidence; do not classify a late probe as an exclusivity
defect or infer a successful expiry. Ordinary functional tests can run slowly
on an occupied host; these state preconditions, rather than host-speed limits,
govern the real temporal integration case.

`_Peer.kill_and_reap` owns the process group even when its leader has already
exited. Retain termination, leader reaping and stream draining through repeated
cancellation; claim completion only after group absence and EOF. Keep credential
frames in the existing bounded anonymous stdin pipe. Fixed protocol events carry
no credentials or message/tool content. A setup failure on stdin EOF must occur
before constructing workspace or database clients.

`tests/test_selected_profiles_managed_process.py` exercises delayed valid events,
explicit failed event/EOF, a leader exiting while a descendant retains stdout,
and repeated cancellation during cleanup. Use exact new-child ownership and
event barriers; no elapsed-speed assertions or sleeps to imply readiness.
Add a barrier that holds contender resource preparation while proving no
original lease has yet been activated, plus a control that cannot establish
the initial active-state observation and must remain inconclusive.
Recovery/fencing tests retain all durable-state and unknown-effect assertions.

## Migration & Rollback

- **Backward Compatibility**: Carrier is an independent optional distribution; core-only imports and existing client behavior do not depend on it. The public import name is `tether_databricks`; distribution spelling follows ADR-022 and remains subject to registry availability check.
- **Data Migration**: No automatic schema migration. The embedder's named owner applies and verifies external migrations and roles before readiness.
- **Feature Toggle**: Enable by selecting the carrier app entrypoint and explicit settings; omit the optional distribution to keep the carrier unavailable.
- **Rollback**: Redeploy the prior app bundle and stop new traffic. Preserve committed external journal facts and resolve unknown effects under CONTRACT-003. Revert a database migration only when the external profile's own rollback procedure proves it safe; the carrier never auto-downgrades schema or roles.

## Implementation Sequence

1. Implement settings, identity adapter and typed resource leases -- Files: `packages/databricks/src/tether_databricks/{settings,identity,resources}.py` -- Tests: `packages/databricks/tests/test_settings.py`, `test_identity.py`, `test_resources.py`.
2. Implement bounded lifecycle reporting and public carrier composition -- Files: `packages/databricks/src/tether_databricks/{lifecycle,composition}.py` -- Tests: `packages/databricks/tests/test_lifecycle.py`, `test_composition.py`.
3. Create the locked bundle sample and asset packaging -- Files: `examples/databricks-app/` -- Tests: `examples/databricks-app/tests/test_bundle.py`, `tests/packaging/test_databricks_assets.py`.
4. Prove deterministic contract/package cases, then operator-selected platform and storage profile cases -- Tests: `make web-package-check`, `make web-platform-check`.

**Prerequisites**: CONTRACT-006 and CONTRACT-007 signatures reconciled; public client/export boundary implemented; selected Databricks workspace and disposable external database profile available for live claims.

## Risks

| Risk | Prob | Impact | Mitigation |
|------|------|--------|------------|
| Apps proxy/server H2C behavior differs from local server proof | M | H | Require live ingress test for exact app/server versions before claim. |
| Lakebase transaction, role or token refresh profile differs from PostgreSQL 18 | M | H | Keep separate profile and run migration, connection, fault and restart tests. |
| User token expires during a long stream | M | H | Revalidate every bounded batch; terminate on expiry and reconnect with a current user request. |
| Forced kill interrupts non-cooperative worker/resource cleanup | M | H | Report outstanding work by deadline, retain in-process ownership while possible and rely only on committed facts after restart. |
| Distribution name is unavailable in registry | L | M | Check registry name before release; retain namespace independence and update ADR-022 plus release metadata deliberately. |
