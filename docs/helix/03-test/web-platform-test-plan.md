---
ddx:
  id: TP-002
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: FEAT-014
      kind: informed_by
    - id: FEAT-015
      kind: informed_by
    - id: FEAT-016
      kind: informed_by
    - id: FEAT-017
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
    - id: IP-002
      kind: informed_by
    - id: STP-001
      kind: informs
---

# TP-002: Web and Databricks Apps Qualification Plan

## Strategy

**Goal**: Qualify optional HTTP, browser, remote-client and Databricks Apps carrier behavior against FR-65 through FR-70 and NFR-9. Every claim names the package version, runtime, app ingress, identity mode, storage profile, and evidence source. The existing Tether client/store guarantees remain authoritative; a web or platform layer cannot widen them.

**Out of scope**: Universal Databricks, Lakebase, browser or remote-client support; production resources or customer data; a claim that successful bundle validation proves live Apps ingress, role isolation, or durable recovery.

PRD priority remains P2 for FR-65 through FR-70. In this plan, a critical path is a blocking gate before claiming the optional web or Apps profile; it does not reclassify those PRD requirements.

## Framework Selection & Cost

| Layer | Framework | Justification | CI Time Impact | Maintenance Burden |
|---|---|---|---|---|
| Python contract/unit | pytest, AnyIO, pyright, ruff | Existing selected stack; public-client doubles and strict async/type checks | Unmeasured; keep fast tests in ordinary CI | Maintain shared client and auth fixtures |
| HTTP integration | pytest/AnyIO with a local ASGI client | Validates exact HTTP mapping, current actor binding, faults and feed behavior | Unmeasured; no external service for deterministic cases | Maintain fake identity, client provider and SSE reconnect controls |
| TypeScript unit | Vitest, Testing Library, MSW | Selected React testing stack; models user-observable states and API responses | Unmeasured; run in PR CI | Maintain accessible interaction fixtures and fake feed server |
| Browser end to end | Playwright in Chromium | Proves built static assets, real navigation, keyboard behavior and browser/TUI journeys | Unmeasured; selected critical journeys only | Maintain fabricated browser accounts and stable session drivers |
| Platform profile | Real Databricks Apps deployment and selected Postgres-compatible endpoint | Required for H2C/SSE proxy, forwarded identity, OAuth, lifecycle and database evidence | Separate, owner-triggered live profile run | Maintain a versioned workspace, app configuration and non-customer test namespace |
| Package/release | uv locks, npm workspaces/package lock, built Python distributions | Proves optional closures, static asset inclusion and clean installation | Unmeasured; install artifacts in CI | Re-run at supported version boundaries and release candidates |

**Cost/benefit summary**: Deterministic contract and browser tests provide fast feedback; live Apps and database tests run only for the named platform profile. Seven scoped runtime reference baselines are established for source27 under the contract below; CI job duration remains unmeasured. Add no CI duration claim until a commit-pinned measurement exists.

## Functional timing allocation

Apply TP-001's functional time and coordination policy to `make check`,
`web-check`, installed-artifact journeys, Vitest, Playwright and accessibility
checks. Functional installation and terminal journeys assert usable output,
correct actions and eventual owned cleanup; their startup or first-response
speed is not an acceptance criterion. Framework auto-waits and test timeouts
require an explicit classification as behavior or emergency hang protection.
Replace progress sleeps with observable state or deterministic event barriers.

Canonical JavaScript functional commands use an explicit whole-command owner,
started before framework startup. The standalone POSIX npm entrypoint owns its
new process group; under `web_quality`, it explicitly reuses the outer command
owner and inherited group, without a competing detached owner or watchdog.
The current 30-minute default is an implementation choice for emergency
containment, configurable before invocation through
`TETHER_FUNCTIONAL_WATCHDOG_MS` (canonical decimal milliseconds, 1,000 through
2,147,483,647). It is a whole-command budget, not a promise of that allowance
for every sequential case. Exhaustion is incomplete even when no behavior
assertion failed; a longer run needs an explicit larger predeclared allowance.

Disable supported inner Vitest test/hook timers. Unavoidable operational
framework, Testing Library and server ceilings sit after the independent owner;
they do not create functional speed targets. Use actual owner expiration or
trustworthy structured Playwright statuses to classify incompleteness, not
ordinary error-message matching. Ordinary assertion failures stay failures;
missing or partial reports never count as passing evidence. Retain spawn,
stream drainage and exact owned cleanup through repeated cancellation and
output-consumer failure. Refuse unsupported standalone ownership before spawn.
These controls do not establish SIGKILL or host-loss guarantees; nested
supervision must name its real owner.

The current full web gate requires eleven exact stages, including
`native-owner-tests` after `test:e2e`. That POSIX stage executes the six approved
Node process-owner controls with the retained job owner and validates fresh
JUnit identities and outcomes before it can pass. Missing, duplicate, unknown,
failed or skipped cases refuse the stage. Its source-bound aggregate records
one required stage, not six independent acceptance proof nodes. Node remains
outside the base Python `make check` lane. Historical Source28 ten-stage
receipts keep their original schema and observed meaning.

Managed peer readiness has no 20-second functional pass threshold. TD-068 and
CONTRACT-007 govern exact event/EOF coordination and retained group cleanup.
Natural lease expiry and real OAuth/database expiry stay named integration
proofs. The carrier's configured stop-and-report deadline remains required
behavior. Distinguish a truthful incomplete report from full physical drain.
Identity-convergence observation budgets do not establish deactivation: require
exact identity/state, protected refusal and an unexpired captured credential.
Watchdog exhaustion leaves evidence incomplete; it cannot become a refusal or
successful skip.

### Phase D qualifier and interface readiness allocation

After phase C, audit every incidental qualifier readiness budget, not only the
managed peer and installed terminal. In `selected_profiles_logs.py`, replace
the half-second startup guess, 15-second follower readiness and fixed
post-request sleep as functional validity tests. Classify the explicit
180-second CLI timeout and its capture/transport semantics; exhaustion cannot
establish functional privacy validity.
Coordinate from subscription state, explicit process failure/EOF and the fresh
request/invocation anchors required by AC10. Keep APP and unfiltered captures
owned through the required operations and observed anchors; stop and drain them
explicitly afterward. Preserve bounded byte capture, content/credential
redaction and truthful incomplete capture. Nonempty historical output or marker
absence alone cannot qualify privacy. Necessary operator observation exhaustion
is incomplete, never a slow-host privacy failure or a pass.

Classify `qualify_live_apps*` restart/control-plane readiness, CLI follower waits
and Playwright visibility/polling/framework caps separately from the intrinsic
carrier stop-and-report deadline. Await actual deployment/UI/process state and
required gates; a framework emergency stop is incomplete. Preserve the actual
configured sub-15-second report/launcher-main claim and its incomplete-drain
limits. Future private warmup recipes must derive from the corrected source29
harness; the old fixed-15-second source28 recipe is historical only.

Identity-convergence unit tests replace 50-millisecond budgets, blocking sleeps
and elapsed windows with controlled monotonic/wall clocks and event barriers.
Preserve one shared absolute observation budget across Me/account/protected
checks, exact principal/application IDs, strict boolean active state, fresh Me
recheck, unexpired captured JWT at denial and unconditional retained restoration.
Observation-budget exhaustion is incomplete, not revocation or authentication
refusal. Actual OAuth/database expiry and fresh-credential controls remain real
integration proof; SDK error classification and authority scope do not broaden.

Ordinary worker/lifetime tests replace incidental one-to-five-second event caps
with explicit barriers and the phase C emergency owner. React debounce tests use
fake timers instead of a 30-millisecond sleep. Review all Vitest and Playwright
implicit waits, progress timers and teardown limits; retain behavior assertions
for visible states, focus, keyboard, accessibility, cancellation and exact
delivery. PTY journeys retain functional output/actions/exit and cleanup; elapsed
measurements and thresholds belong only in admitted performance qualification.
Core caller I/O budgets and carrier stop-and-report limits remain intrinsic
contracts and must not be removed by this audit.
Barrier/readiness waits in both ordinary and frontend fixtures must race the
operation's terminal result as well as its entered/ready event. A task that
already returned or failed cannot satisfy a later cancellation/ready barrier;
report that state promptly while retaining exact cleanup and cancellation
semantics. Removing short timeouts must not replace a visible exception with an
unbounded wait on an impossible event.

## NFR-7 reference measurement contract

The owner's 2026-11-02 isolation constraint governs future performance
qualification. Actual benchmark workloads run only through the explicit
performance path with known reference hardware, exclusive admission and recorded
conditions under PRD NFR-7. Ordinary functional aggregates keep deterministic
measurement-logic controls, not real reference measurements. Unverified
exclusivity or conflicting work defers measurement; it does not fail a functional
test or grant a performance pass.

Source27 establishment below is historical evidence under its then-adopted
policy. Its original execution did not establish the new isolation admission.
Preserve its exact baseline bytes and both source28 regression failures; neither
can be relabeled a pass. A new qualified reference policy and independent
measurements are required before a current performance support claim. Do not
raise thresholds automatically to fit those failures.

### Performance admission on the existing reference host

Use an owner-controlled exclusive reservation of the existing known reference
Mac after its other jobs finish. Bind the reservation to the recorded host,
hardware and runtime; hold an OS nonblocking exclusive benchmark lock across
admission, measurement and cleanup. The lock excludes cooperating benchmark
owners only; it does not exclude unrelated processes. Require independent
activity observations before, throughout and after the run in addition to the
reservation. Record the reservation's actual control scope, unknowns and observed
interference. Reservation attestation, metadata, a load threshold or a GitHub
concurrency group alone cannot admit a run.

Compose an injectable typed admission port and activity observer at the
performance CLI boundary. Keep these operator mechanisms outside the library
core; an equivalent managed reserved executor can be supplied later. Unknown
reservation/host/runtime binding, lock contention, unavailable observation or
interference refuses or defers admission before measurement. Loss of admission
during or after measurement invalidates current qualification and retains the
partial observations and cleanup outcome. No retry or rebaseline follows
automatically. Use fabricated reservation/observer/lock controls for ordinary
tests. Actual performance qualification waits for a real reservation and its
observed conditions; the currently occupied Mac is unadmitted.
The default Darwin observer recognizes the exact benchmark descendant tree and
exempts only root-owned rows in the exact idle `I` state. Other user agents,
shells, controllers and GUI processes outside that tree remain interference or
unknown, even when they appear idle. An ordinary logged-in Mac may therefore
remain unadmitted after other jobs finish. The selected reservation mechanism
does not prove that a usable exclusive execution context currently exists.
Retain this limitation until the owner can supply actual control and observation
evidence; do not invent an allowlist, claim load alone proves isolation or
provision replacement infrastructure to make admission pass.

### Controlled gate and observational Apps scope

Owner decision on 2026-11-02: keep Databricks Apps network latency
observational, outside performance pass/fail. The controlled gate comprises
six local profiles and 54 metrics: store 7, memory-store 7, client 17,
volatile-client 11, engine 2 and follower-memory 10. Default controlled
qualification requires only those six baselines and their independently
admitted current observations. It does not require an Apps measurement.

The two Apps handshake/list latencies may be collected separately as optional
observations. Preserve exact payload/source, collector/operator, driver/runtime,
immutable bundle/deployment and provider-profile provenance, with actual
collection timestamps and explicit limits. A provenance sidecar may carry these
joins; it is not a collection-admission certificate and cannot emit qualified
metric nodes or gate on the historical thresholds. Managed physical hardware
and co-tenancy remain unknown. No new remote infrastructure or controlled
backend qualification follows from this decision.

The historical seven-profile/56-metric files and source28 failures remain
unchanged. Current closure requires exactly the 54 controlled metric nodes;
Apps observations cannot fill missing controlled nodes or count as passed
performance criteria. All 41 functional ACs and all 45 IP-002 tasks remain
required, including the separate live Apps behavior/compatibility proofs.
The [current controlled-node policy](../04-build/evidence/web-platform/reference-performance/controlled-policy-20261102.json)
lists the exact 54 expected nodes. It is a planning contract, not executed proof.
Each current qualified ratchet must join the final admission record by exact
hash, source and scope; a directory name or nearby observation file is not proof.

### Historical source27 establishment and retained workload definitions

Source27 first-baseline adoption follows the PRD's explicit validate-or-replace
authority. The [accepted seven-profile review](../04-build/evidence/web-platform/performance-budget-review27-draft.md)
preserves every original target, actual miss and unvalidated meaning; the
[seven-node establishment receipt](../04-build/evidence/web-platform/performance/source27-first-establishment/first-baseline-establishment.json)
is first establishment only, not a historical regression comparison. Each
committed selected metric maximum is the observed value × the same predeclared
1.2 future-variance factor. These measured ratchets replace the original caps
for the accepted definitions; do not claim they enforce a stricter old cap,
represent an SLA or establish pure CPU/native-memory performance.

Historical source27 establishment required all seven actual profile receipts,
strict schema/workload/version/source identity and exact raw hashes: POSIX JSONL store,
MemoryFiles JSONL reference-capacity store, POSIX public client, default-capacity
VolatileSettings public client, public engine exclusive-wall boundary, causal
follower traced-Python state/growth, and separately deployed Apps reads. Store
profiles use 10,000 × 200 plus 100,000 entries and 100 samples; client fanout uses
1,000 followers/explicit queue 32 and 100 samples. Page measurements cover three
named positions plus 100 deterministic spread positions with exact indices.
Cursor evidence must replay the exact missed commits and verify real retention
floor/resnapshot; a fresh subscription is not cursor recovery. Capacity-scoped
MemoryFiles store results cannot be described as shipped volatile defaults.

Require every streamed consumer to receive the exact ordered reply and matching
committed terminal with no missed events. Preserve the real volatile 20 ms
proposal miss separately from the durable result. In-process follower timing is
public command → final delivery at fanout, not isolated commit latency; the
cross-process result is a public request → delivery upper boundary for one
follower. Engine timing uses actual Engine.run_turn with required attempt
callbacks; exclude model bodies minus callback intervals, then union all real
store/history bodies. Dedicated delay controls must retain gate work and exclude
adapter work. Untimed getters/bookkeeping remain included and setup/close/
deferred observer drain outside; whole-turn time is never exclusive engine CPU.

Require 100 causal empty/full follower trials, queue/live capacity 32, exact
occupancy/drain/overflow and 32 KiB coalescing controls; report whole-trial net
traced-Python allocations including fixtures and unrelated frees, not isolated
per-follower total or an upper bound. Keep structural terminal-retirement/race
controls and actual 1,000-ready-follower process envelope separate; never divide
RSS by follower count. Startup uses actual cold process import/exit, operational
CLI first byte and PTY first paint with clean ownership, not help/property proxies.

The historical Apps observation used 100 actual opens and 100 list calls, stable page cardinality and
observed HTTP identity, the same accepted preacquired credential, exact current
deployment/export before and after, client machine hash and unknown managed
server hardware disclosure. The failed original partial-stdout operator attempt
produced no measurement; only the corrected bounded-EOF operator's actual receipt
contains the completed historical observation; neither is a current controlled pass. Current controlled comparisons require independent admitted local observations
and all six controlled baselines; missing/stale/mismatched inputs refuse rather than skip.
No further self-comparison is required to establish this first baseline. Manual
review, selected identity/expiry, managed recovery and final cleanup remain
separate gates.

## Critical Paths (P0)

1. **Public client substitution (FR-65, FR-66, NFR-9)**: base install works without terminal, web or Databricks extras; local and remote facades satisfy the same typed client contract; TUI and API can replace client providers without private imports.
2. **HTTP behavior and owner access (FR-67)**: every client read/command has a typed, versioned HTTP mapping, bounded input/output, current actor binding, scrubbed errors and defined retry behavior.
3. **Shared snapshot and feed (FR-48, FR-67)**: race snapshot with commits, reconnect with a watermark, detect lag/retention gaps, clear provisional text on reset and do not cancel accepted work on disconnect. For the injected terminal, commit during initial presentation and prove replay from the earlier snapshot; let a completed read consume cancellation and prove owned stop prevents another receive or late presentation callback.
   Re-render a completed context failure after a terminal feed update and retain its authorized diagnostic. New lifecycle, changed session prefix, selection, read refusal or superseding render must invalidate stale notices; reverse metadata reads have finite bounds and explicit unavailable presentation.
4. **Browser journeys (FR-68)**: built UI follows the shared API contract, preserves committed state, exposes loading/empty/pending/error/success states and remains usable by keyboard and assistive technology.
5. **Databricks authority and lifecycle (FR-69)**: trusted ingress headers cannot be spoofed in local mode; current user identity and app identity stay separate; credential expiry, scope denial, SIGTERM and forced termination fail with explicit outcomes.
   For accepted unsafe work, retain the exact committed terminal reason: orderly shutdown may record `cancelled`, while process/lease loss records `interrupted`. Both paths must preserve the same unresolved invocation and unknown-outcome barrier without replay or inferred success. The separate killed-process control requires `interrupted`.
6. **Storage and recovery (FR-69, ADR-020, CONTRACT-003/005)**: explicit migration and role setup, OAuth/TLS acquisition, token refresh, transaction faults and restart are tested for the named Lakebase/Postgres profile. Existing PostgreSQL 18 qualification does not substitute for these checks.
7. **Packaging and external consumer (FR-70)**: install locked distributions from built artifacts, verify optional closure and asset paths, and build a fabricated embedder independently from this repository.

## Acceptance Criteria Layer Allocation

Every story AC has exactly one primary layer here. Story files own the individual criteria; this table allocates verification only.

| Story / AC scope | Story Test Plan | Primary Layer | Why this layer |
|---|---|---|---|
| US-065 public client host (AC1–8) | [[STP-001]] | Contract + integration | One public facade contract must hold for local and remote clients, borrowed consumers, shutdown and revocation. |
| US-066 HTTP and remote session parity (AC1–8) | [[STP-001]] | Contract + integration | Typed method mappings, accepted-send recovery, stream ordering, live policy and durable reconnect cross protocol boundaries. |
| US-067 replaceable React session UI (AC1–7) | [[STP-001]] | Unit + installed end to end | Public extension seams, safe rendering, exact wire values, mounted assets and keyboard use need package and browser evidence. |
| US-068 shared session Databricks carrier (AC1–10) | [[STP-001]] | Integration + platform profile | Authority, resource and lifecycle guarantees require both deterministic checks and a named live deployment profile. |
| US-069 independent package adoption (AC1–8) | [[STP-001]] | Installed end to end | Built wheels and browser tarballs must install and work in an external consumer fixture. |

STP-001 owns the stable AC-to-test matrix and UX map. The project-level plan allocates story scopes and evidence layers without duplicating individual criterion rows.

## Secondary Paths (P1-P2)

- **P1**: selected Databricks SDK authentication behavior, broader Lakebase features and live workspace release workflows when the maintained compatibility profile includes them.
- **P2**: alternative ASGI servers, hosting prefixes and storage/auth modes; each needs its own declared profile and conformance evidence.

## Test Data Strategy

Use fabricated users, sessions, messages, tools, approvals, ids, tokens and database namespaces only. Construct any credential-shaped value from runtime parts; never commit it. Use independent app and user identities. Capture screenshots and short videos only from fabricated browser sessions. Inject clocks, connection acquisition failures, commit uncertainty, proxy disconnects, migration errors and lifecycle deadlines without `time.sleep`.

Platform evidence records cloud, workspace runtime, app configuration, ingress/server version, Python, package locks, authorization scopes, database version/profile, migrations, roles and test date. No request headers, tokens, customer rows or connection strings enter evidence.

Selected identity-revocation qualification uses an explicit administrative
adapter, independently checked against the owned test principal and selected
workspace. Account-level administration may belong to the same correlated human
as the primary caller; it is a separate authority scope, not proof of a different
person. Test both adapter implementations and the dummy against the same
conformance behavior, reject mismatched authority before mutation, and prove
retained restore ordering under repeated cancellation. No permissions are granted
to make the test pass. For deactivation, the workspace `/Me` state, exact account
service-principal state and protected denial share the original single
90-second absolute budget; no surface may restart it. A lagging first account
read is not a new timeout or inferred success. Require exact service-principal
and application IDs plus an explicit boolean active=false before the single
captured-token protected denial. Missing/non-boolean state is a refusal, never
inferred deactivation. The captured token must remain unexpired at that denial;
unconditional retained restoration still requires fresh credential, exact active
identity and protected200. Attempt04 exposed the need for this qualifier
convergence control; it does not relax the underlying identity requirement.

Use the development-only asyncpg 0.32.0 oracle for structured post-TLS database
authentication refusals, because the selected Psycopg startup path does not expose
that SQLSTATE. Keep the real expiry wait and fresh Psycopg connection controls;
reject TLS, socket-drop, timeout and unrelated-server failures. This tooling does
not alter the runtime dependency profile or turn independent-oracle success into
a Psycopg diagnostic claim.

## UX Coverage

Browser journeys must map each affected behavior to an acceptance criterion and an executable assertion. Screenshots/video use fabricated data and follow `docs/microsite.md` before any public use.

| Journey | Required visible states | Keyboard/accessibility assertion | Evidence layer |
|---|---|---|---|
| Navigate session list and session | Loading, empty, success, unavailable, selected/current session | All links and actions are keyboard reachable; current page exposes `aria-current` | Playwright + accessibility assertions |
| Create session and send a message | Submitting, accepted, validation error, disconnected | Focus returns predictably; labels and errors are announced | Testing Library + Playwright |
| Follow a running turn | Provisional text, committed result, stream lag/reset, reconnect | Status changes are announced without trapping focus | MSW/Vitest + browser proxy scenario |
| Review and answer approval | Pending, approved, declined, conflict/already answered | Dialog opens/closes by keyboard; focus is restored; destructive choice is explicit | Testing Library + competing-client Playwright case |
| Read history and call detail | Ready, partial, denied, pending, unavailable, integrity failure | Error and status text have accessible names; no content is exposed after revocation | Playwright + authorized fixture assertions |
| Change settings or title and close session | Saving, success, validation failure, refusal, closed/read-only | Form validation is announced; action state is apparent and keyboard operable | Testing Library + Playwright |

## Known Gaps

- Core, API, browser, carrier and package-check implementations now exist, with scoped execution recorded in IP-002 continuation evidence. The final current-source aggregate and selected live acceptance remain required; a scoped pass does not close this plan.
- Databricks qualification requires an actual H2C frame exchange against the exact deployed listener and independent protected Apps proxy/browser/SSE/TUI journeys. Bind both to the same process/nonce and loaded source hashes; record actual ingress ASGI protocol separately without claiming the proxy negotiated H2. Generic local capability proof is insufficient.
- Shutdown observation through the temporary external observer is explicitly instrumented. Require source/probe/bundle and actual-stop correlation, original report plus launcher-main return within the configured sub-15-second bound, and fresh post-stop visibility of the committed metadata row. Missing logs or rows cannot pass.
- Lakebase is not covered by the selected PostgreSQL 18 evidence. Its schema, role, auth token lifetime, TLS, pool, transaction and restart behavior remain unqualified for Tether.
- The separate embedder is a fabricated consumer fixture; no customer repository/configuration is copied into this project.
- Framework/runtime version ranges and CI costs remain unmeasured until their respective locked profiles run.

## Build Commands

The repository commands below exist. They run deterministic source/browser checks, installed-artifact checks or an explicitly selected live profile. Their presence does not establish an observed pass; the release postcheck verifies current source-bound receipts without repeating the full aggregate. Missing selected-profile access fails the affected gate.

```bash
uv sync --locked
make check CHECK_BASE=HEAD
make web-boundaries
make web-client-contract
make web-api-contract
make web-ui-check
make web-package-check
make web-check
make web-platform-check
make web-release-check
```

**Blocking gate**: Before claiming web or carrier support, all stable ACs in US-065 through US-069 pass their allocated primary layer; source/package boundary checks pass with real forbidden-import controls; required browser screenshots and accessibility assertions are retained; and selected Apps/storage profile evidence is complete. Bundle validation alone does not qualify deployed ingress, identity, storage or recovery.

## Diagnostic Test Strategy

Test content-free diagnostics before platform capture and fail if fabricated credential/content markers appear in logs, traces, error bodies or stored execution facts. Authorized message and tool content may exist in authoritative history under CONTRACT-005; it must not leak into logs, spans, audit facts or API errors. For Apps evidence, capture nonempty APP and unfiltered platform streams, bind a fresh worker invocation and HTTP success/refusal records to the fabricated request session IDs, and verify redaction across both captures and capture diagnostics. Record the actual source kinds; an absent SYSTEM stream does not substitute for observed platform records. Keep Tether correlation identifiers separate from platform request ids and actual trace ids. A diagnostic identifier or successful deployment is not evidence that an event was durably accepted.
