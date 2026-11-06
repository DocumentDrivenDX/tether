---
ddx:
  id: IP-002
  type: implementation-plan
  activity: build
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: architecture
      kind: informed_by
    - id: FEAT-013
      kind: informed_by
    - id: FEAT-014
      kind: informed_by
    - id: FEAT-015
      kind: informed_by
    - id: FEAT-016
      kind: informed_by
    - id: FEAT-017
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: CONTRACT-007
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: SD-003
      kind: informed_by
    - id: TP-001
      kind: informed_by
    - id: TP-002
      kind: informed_by
    - id: STP-001
      kind: informed_by
    - id: TD-065
      kind: informed_by
    - id: TD-066
      kind: informed_by
    - id: TD-067
      kind: informed_by
    - id: TD-068
      kind: informed_by
    - id: TD-069
      kind: informed_by
---

# IP-002: Modular Client, Web Components and Databricks Carrier

## Scope

Deliver the complete modular interface release: the public Python `tether.client`
embedding facade; a migrated terminal interface; independently installable HTTP
API and browser asset host; schema-derived TypeScript client, React bindings and
components; a reference browser application; and a Databricks Apps carrier.
The carrier composes these components around one runtime and one ASGI host.
ASGI is the asynchronous Python server interface used by the selected web stack.

Governing artifacts are [PRD](../01-frame/prd.md),
[architecture](../02-design/architecture.md), ADR-004/011/013/018/019/020/022,
CONTRACT-003/005/006/007, SD-002/003, [TP-001](../03-test/test-plan.md),
[TP-002](../03-test/web-platform-test-plan.md) and
[STP-001](../03-test/test-plans/STP-001-web-platform-acceptance.md).
Exact interfaces and platform constraints belong in those designs and
contracts. TD-065–069 apply them to concrete implementation paths and governing
story criteria. This plan orders their implementation and proof.

The [consumer plan](implementation-plan.md) retains FR-54–64 and its acceptance
boundary. This plan implements FR-65–70/NFR-9 through FEAT-013–017 and
US-065–069. These requirements retain their P2 classification; the complete
release sequence below adds no deadline or automatic adoption dependency.
An interface does not automatically expose unaccepted consumer seams. Only tasks
whose accepted behavior needs such a seam wait for its corresponding C-series
acceptance and implementation; independent facade and interface work can proceed.

Package boundaries follow ADR-022: retain `novirhiza-tether` and its `tui` extra;
use independent Python distributions/namespaces for `tether_web_api`,
`tether_web_ui` and `tether_databricks`; publish browser packages `@tether/client`,
`@tether/react`, `@tether/ui`; keep `apps/reference-web` as the reference consumer.
Python distribution spellings and concrete repository directories are taken from
ADR-022, never inferred from import namespace spelling.

Out of scope: another agent runner, an owned application framework, business
domain tools, a second ASGI service, private application cutover, automatic schema
migration, public deployment/publication during planning, and claims for untested
provider, browser, database, cloud or region profiles. Existing core behavior and
consumer-slice proposals remain authoritative within their own scope.

## Shared Constraints

- Ordinary embedding, reference clients and adapters use the public `tether.client`
  construction/configuration/lifecycle surface. Advanced consumers implement
  published ports; none imports private engine, session or store implementations.
- Core public I/O stays asynchronous and reads no environment variables. Explicit
  composition roots own settings, credentials, pools and infrastructure. Borrowed
  clients never close an application's host or unrelated workers.
- Each public port has a typed dummy and shared conformance tests. Python payloads
  remain Tether-owned; framework and vendor types stay at their adapter boundary.
- Journal facts, authoritative history, current policy and unknown-effect barriers
  retain their meanings through HTTP, streams, reconnect, restart and UI rendering.
  A disconnected request never proves that accepted work was canceled.
- Identity is explicit and current. User authority, application service-principal
  authority and maintenance authority cannot silently substitute for each other.
  No credentials, private data or content enter diagnostics or committed fixtures.
- Committed replay is authoritative. Provisional text, when supported, is tagged,
  bounded and reconciled; it must not imply cross-process token delivery or durable
  acceptance. Streaming uses the versioned event contract, not ad hoc UI messages.
- Keep existing Python shape ceilings, import gates, bounded test runner and 85%
  branch-coverage floor. Adopt comparable TypeScript gates before browser features;
  no widened baseline, skip, ignore or excluded package may conceal a new violation.
- FastAPI, React, TypeScript, Vite and Playwright follow ADR-022, subject to bounded
  compatibility proof. A proposed dependency range becomes a support claim only
  after its resolver, codec, typing, runtime and installed-artifact gates pass.
- Durable storage is external to the Apps process. Local JSONL remains the qualified
  local profile; ephemeral Apps storage and a generic PostgreSQL compatibility
  statement do not establish restart durability or Lakebase conformance.

## Implementation Slices

The task list below is the selected repository tracker. Every box starts unchecked;
planning completion checks none of them. Task IDs remain stable through reordering.
`Depends` lists hard predecessors; tasks with satisfied dependencies may run in
parallel. A blocked spike blocks only the named dependent profiles and tasks.

Sol orchestrates scope, dependencies, acceptance review and closeout. Luna performs
implementation and bounded probes, with Sol reviewing each public boundary and
integration gate. These are agent roles, not calendar commitments or human staffing
estimates. Every task retains this ownership unless its execution record names an
authorized reassignment. Sol owns acceptance/release disposition; Luna owns code,
tests and reproducible evidence preparation, including documentation updates.

### Foundation and bounded uncertainty probes

- [ ] **WP-00 — Freeze the executable baseline and acceptance map.** Depends: none.
  Read the current governing artifacts, actual implementation, existing release
  receipts and dirty diff, including TD-065–069 and STP-001's concrete case/path map.
  Map every required capability to task/test IDs; identify
  accepted versus proposed C-series behavior and record current violations with
  owners. Prove: baseline `uv sync --locked`, `make check CHECK_BASE=HEAD` and a
  safe inventory receipt. If an infrastructure failure prevents the original
  dirty cohort from completing, preserve its exact inventory, hashes and failure
  as incomplete evidence. Closing baseline handling then requires the corrective
  runner controls and the complete current-cohort aggregate at the unchanged
  quality floor. That current pass does not qualify the original cohort.
  Baseline failures remain explicit, never relabeled as passes.
- [x] **WP-00-C1 — Isolate the aggregate coverage database.** Corrective work after the WP-00 failure.
  `scripts/test_suite.py` now supplies a unique coverage CLI data file for every
  erase/append/report run. Fourteen focused runner tests passed, including real
  concurrent suites that leave an unrelated shared coverage file untouched.
  Collection, bounded groups, child deadlines and the 85% floor remain unchanged.
  This corrects the infrastructure defect; it does not close WP-00's full gate.

Execution deviation: dependent implementation proceeded after the original
1,758-case/80-group baseline was interrupted by a concurrent coverage-database
collision and C1 isolated the runner. The initial dirty cohort cannot be recreated
from committed HEAD. Preserve that incomplete original attempt and its captured
inventory; qualify the final current cohort separately. WP-00 remains unchecked
until that complete gate passes. This records the departure from planned ordering
without a retroactive baseline-success claim or a reduced quality requirement.
- [ ] **WP-01 — Adopt the repository/package boundary and command scaffold.** Depends: WP-00.
  Register ADR-022 package paths, public export maps, package versions and dependency
  directions. Add the command contract below, Python/TypeScript shape checks,
  package-aware coverage collection, local/pre-commit/CI wiring and negative-control
  harness. Prove: allowed imports pass, forbidden/private imports and cycles fail;
  old violations have bounded remediation tasks without expanded exclusions.
- [ ] **WP-02 — Qualify selected stack and build tooling.** Depends: WP-01.
  Bound the probe to Python 3.11/current Pydantic and Agent pins, selected FastAPI/
  ASGI server, React/TypeScript/Vite/Playwright and package manager/runtime pins.
  Check proposed registry/package names; test range boundaries, schema generation, SSE parsing and
  asset build/install. Prove: resolver plus executable compatibility matrix with
  exact versions; failed combinations stay unsupported and block dependent builds.
- [ ] **WP-03 — Prove the browser/HTTP event and lifecycle design.** Depends: WP-02.
  Bound to a dummy client, two users, a slow subscriber, malformed frames and a
  disconnect during acceptance. Resolve stream authentication/refresh, replay/reset,
  heartbeat/queue bounds, provisional versus committed events, abort semantics and
  typed transport errors. Include Unicode scalar offsets and SSE IDs only at the
  final event of each commit. Prove: contract fixtures and wire/browser probe receipt;
  revise CONTRACT-006 before implementing any failed assumption.
- [ ] **WP-04 — Qualify Apps identity, ingress and remote terminal access.** Depends: WP-02.
  Bound to an operator-selected fabricated workspace/profile. Verify trusted proxy
  headers, forwarded-user versus app service principal identity, absent/revoked
  user refusal, OAuth remote-TUI access to `/api`, ingress path and SSE behavior,
  exact deployed-listener HTTP/2 cleartext (H2C) capability plus independently observed
  proxy protocol and actual SIGTERM allowance. Bind observations to one process/nonce
  and loaded candidate hashes; do not infer proxy H2 from frontend HTTP/2. Prove: versioned
  identity/ingress matrix; no workspace access means an explicit profile blocker.
- [ ] **WP-05 — Qualify external durable storage and shutdown.** Depends: WP-04.
  Bound to the selected external PostgreSQL profile and separately Lakebase profile.
  Verify required versions/settings/schema, migrations as explicit operator action,
  per-caller acquisition/role/RLS, pool/token renewal, fencing, restart, failover and
  the 15-second SIGTERM gate with real worker ownership. Prove: process/database
  receipts and limitations. Failed Lakebase checks cannot inherit PostgreSQL proof.
- [ ] **WP-06 — Qualify selected Databricks service capabilities.** Depends: WP-04.
  Bound each probe to the SD-003 release profile: Gateway/model identity/correlation,
  authorized source readiness, governed retrieval/tools and trace receiver where
  selected. Distinguish declared, denied, delayed, unavailable and unsupported.
  Prove: fabricated service/version/permission matrix; untested adapters block their
  acceptance case without obstructing the platform-neutral interface release.
- [ ] **WP-41 — Prove durable remote turn results and targeted cancellation.** Depends: WP-00, WP-03.
  Bound the probe to accepted/terminal journal facts, API process replacement, an
  old turn followed by a new turn and a lost cancel response. Resolve public
  `turn_result` authority, missing/unavailable/ongoing distinctions, lookup bounds,
  unknown versus known-nonterminal turns, current disclosure policy and target-turn
  compare before cancellation. Prove:
  concrete CONTRACT-003/006 revision and process probe; required FR-67 parity
  cannot close with these operations declared unsupported.

### Public core facade and terminal migration

- [ ] **WP-07 — Publish client protocols and complete exported types.** Depends: WP-01, WP-02.
  Implement CONTRACT-006's client/provider/host protocol, request/result/event/error
  types and CONTRACT-003 compatibility mapping through `tether.client`. Add dummy,
  conformance and third-party replacement examples. Prove: `make web-client-contract`,
  strict public consumer typing and exported symbol/schema compatibility fixtures.
- [ ] **WP-08 — Implement explicit construction, configuration and owned lifetime.** Depends: WP-07.
  Provide public `ClientHost.open(ClientSettings)` and `ClientHost.from_ports`
  minimal/offline/local and advanced-port composition; preserve profile refusals and
  the documented legacy compatibility path. Cover startup failure,
  cancellation, bounded close, retained physical workers and borrowed ownership.
  Prove: facade-only installed examples and lifecycle conformance; no consumer imports
  private implementation or reads configuration implicitly inside core.
- [ ] **WP-09 — Publish advanced port/test-kit entrypoints.** Depends: WP-08.
  Expose the supported model/storage/history/policy/telemetry extension ports and
  configuration factories without SDK types or private internals. Each example
  substitutes a dummy then a conforming adapter. Prove: positive/negative boundary
  checks and installed custom-adapter typing/conformance under base-only dependencies.
- [ ] **WP-10 — Migrate CLI and TUI composition to the public facade.** Depends: WP-08, WP-09.
  Retain documented commands, demo, local durable configuration and Textual extra;
  inject the client into presentation factories. Remove runtime/store/adapter
  construction from consumers. Capture the initial snapshot watermark before
  presentation and replay from it; stop the owned subscription even when a
  completed borrowed read consumes cancellation. Prove: commit-during-presentation
  and completed-read/stop regressions, existing command/TUI suites plus dummy-client
  substitution, installed PTY journeys, startup/shutdown and no terminal dependency
  in the base closure. Reconstruct terminal diagnostics through bounded current
  public reads after refresh; prove new-turn/session/read-refusal and concurrent
  prefix invalidation without relying on a transient post-command log write.
  Preserve published entrypoint migration guidance.
- [ ] **WP-42 — Implement public durable result and targeted-cancel semantics.** Depends: WP-07, WP-41.
  Implement the accepted journal-backed result reader and turn-bound cancellation;
  exclude process-local retained handles as outcome authority. Prove: process
  replacement during acceptance/running/terminal states, lost acknowledgement,
  old-turn retry against a newer turn and current read revocation through the shared
  local/remote conformance suite. Closed or unavailable results never trigger rerun.
- [ ] **WP-11 — Add the remote client used by the terminal interface.** Depends: WP-03, WP-10, WP-17.
  Implement the optional core remote extra and accepted HTTP client/provider mapping
  with injected transport/auth and no FastAPI/API-package dependency or host internals.
  Scope credentials to the explicit remote profile, refresh current identity and
  preserve idempotency/reconnect/refusal semantics. Prove: terminal against a real
  loopback API plus dummy HTTP transport, expired credentials and reconnect controls;
  Apps OAuth support additionally requires WP-04 and WP-29.

### Independent Python API and asset host

- [ ] **WP-12 — Scaffold the API distribution and injected factory.** Depends: WP-02, WP-07.
  Implement independently installable `tether_web_api`, public typed factory/router,
  configurable mount prefix, owned versus borrowed lifecycle and no hidden host.
  Prove: API-only and mounted-in-existing-ASGI-host examples against a dummy provider;
  importing core never imports the web framework or optional API package.
- [ ] **WP-13 — Implement authenticated reads and wire errors.** Depends: WP-03, WP-12.
  Translate list/find/get/snapshot/entries/history/effects/call/pending/capability reads and pagination
  through injected current-actor clients. Validate wire versions, request/response
  byte bounds and exception redaction. Prove: `make web-api-contract`, two-user owner/
  payload-read negatives, malformed/oversized inputs and unavailable-history cases.
- [ ] **WP-14 — Implement command and operation transport.** Depends: WP-13, WP-08, WP-42.
  Translate create/update/send/cancel/approve/resume/resolve/close/restore/history-link
  commands and result retrieval; preserve request identity, accepted-operation handles and
  semantic conflicts. Distinguish session cancel from target-turn cancel, accepted
  versus late confirmation and command-specific acknowledgement/retry reconciliation.
  Postcommit disclosure denial never proves rollback. Prove: races, authorization refresh, unknown-effect
  refusal, disconnect-after-acceptance and retry without duplicate accepted execution.
- [ ] **WP-15 — Implement versioned follow and reconnect transport.** Depends: WP-03, WP-13, WP-14.
  Implement server-sent events (SSE), owner-scoped cursors, snapshot watermark replay,
  retention/reset, slow-consumer bounds, `last_in_commit` watermark advancement
  and stream identity expiry. Preserve
  provisional/committed distinction; only commit-final SSE frames carry an ID.
  Prove: real HTTP stream fragmentation, Unicode offsets, cancellation,
  revocation, reset/gap and cross-process committed-feed tests; no polling-only substitute.
- [ ] **WP-16 — Implement independent asset hosting and mount configuration.** Depends: WP-02, WP-12.
  Implement `tether_web_ui` with immutable built assets, nested-prefix/base-path/API
  discovery, cache/security headers and SPA fallback excluding `/api` and missing
  assets. Prove: UI-only host with an alternate API, API-only host without UI,
  nested mounts and packaged-resource installation outside the source checkout.
- [ ] **WP-17 — Close API conformance and OpenAPI/schema publication.** Depends: WP-13, WP-14, WP-15.
  Export deterministic versioned OpenAPI/JSON Schema including event/error variants,
  compatibility rules and capability negotiation. Run the same client conformance
  through local and HTTP implementations. Prove: wire golden corpus, generated-schema
  drift check and unsupported-version refusal; record all endpoints and limits.

### Browser packages and replaceable reference UI

- [ ] **WP-18 — Generate the typed browser contract.** Depends: WP-17.
  Generate `@tether/client` types/codecs from canonical schemas with a pinned generator;
  preserve IDs, exact integers above 2^53, unknown variants, optional/null fields and typed
  errors. Prove: deterministic generation, compile-time fixtures and Python/TypeScript
  round-trip corpus; no hand-maintained competing wire type authority.
- [ ] **WP-19 — Implement browser HTTP/stream client.** Depends: WP-18, WP-03.
  Add explicit transport/auth/base-URL injection, bounded streaming parser, retry/
  idempotency rules, abort/dispose and reconnect/reset handling. Keep React and UI
  imports out. Prove: `make web-ui-check` client layer, real browser/loopback API
  conformance, fragmented frames, unknown variants and credential-expiry negatives.
- [ ] **WP-20 — Implement headless React bindings.** Depends: WP-19.
  Implement `@tether/react` injection, cache/subscription ownership, pagination,
  mutations and current snapshot/replay state. Avoid duplicate stream/command owners
  across mount/unmount and Strict Mode; clear protected caches after auth changes.
  Prove: fake-client tests, React lifecycle races and a custom component consumer.
- [ ] **WP-21 — Implement composable UI components.** Depends: WP-20.
  Implement `@tether/ui` session list/transcript, composer, pending approvals, turn/
  tool state, explicit history/reset/unknown outcomes and recovery controls. Expose
  typed slots/themes/commands; render external content safely with keyboard/focus/
  screen-reader support. Prove: dummy bindings, accessibility and untrusted-content tests.
- [ ] **WP-22 — Assemble the reference browser application.** Depends: WP-21, WP-16.
  Implement `apps/reference-web` as an ordinary public-package consumer; support
  nested mounts, loading/empty/error/reconnect states and a no-network fabricated
  demo. Prove: real Playwright create/send/follow/tool/approve/cancel/resume/close
  journeys; API replacement and UI replacement each work without core changes.
- [ ] **WP-23 — Package and verify built browser assets.** Depends: WP-22.
  Build Vite assets from a clean locked checkout, assemble the independent UI wheel/
  source distribution and browser package tarballs, and declare runtime/peer versions.
  Prove: installed browser smoke outside the repository, asset inventory/hashes,
  source-map/content handling and clean regeneration without untracked local assets.
  Retain the measured gzip bundle report against ADR-022's proposed 250 KiB target;
  a failed target requires review and an explicit decision before scope is changed.

### Databricks composition and security

- [ ] **WP-24 — Implement the external carrier composition root.** Depends: WP-04, WP-08, WP-12, WP-16.
  Implement `tether_databricks` explicit settings/resource mapping and one ASGI host
  composing mounted API/assets plus the public client host. Keep app environment,
  SDK/bootstrap/deployment concerns outside core. Prove: fabricated local deployment
  fixture with injected resources and absence of hidden host/store or secondary service.
  Supply complete `examples/databricks-app` app/bundle definitions, `app.yaml`,
  `pyproject.toml`, `uv.lock` and packaged assets; no `requirements.txt` or Node runtime.
- [ ] **WP-25 — Implement per-request and stream identity binding.** Depends: WP-24, WP-04.
  Apply CONTRACT-007's trusted proxy/user/application identity matrix; verify ingress
  trust, refresh and expiry, scoped credential lifetime and unattended-mode refusals.
  Prove: two concurrent users, forged direct headers, missing/revoked identity and
  no service-principal fallback across ordinary reads, commands and resumed streams.
- [ ] **WP-26 — Wire external PostgreSQL and qualified Lakebase.** Depends: WP-05, WP-24, WP-25.
  Compose explicit acquisition/migration/maintenance ownership through public ports;
  no App-local durable store, implicit migration or pooled identity widening.
  Prove: selected-profile restart, concurrent processes/fencing, credential rotation,
  failed acquisition and role/RLS isolation; publish separate backend/version limits.
- [ ] **WP-27 — Wire selected Gateway and platform service adapters.** Depends: WP-06, WP-25, WP-26.
  Implement only the SD-003 accepted model/history/retrieval/tool capabilities with
  stable correlation, current identity and bounded readiness. Prove: adapter dummy/
  conformance suites plus each selected live fabricated case; unavailable originals
  block affected continuation and unknown remote effects never silently repeat.
- [ ] **WP-28 — Implement proxy streaming and bounded shutdown.** Depends: WP-15, WP-24, WP-26.
  Apply verified SSE/H2C/header/path behavior and explicitly configured server startup.
  Seal admissions on SIGTERM and attempt drain in accepted order. Return an accurate
  complete/outstanding report by the configured sub-15-second deadline; retain actual
  worker/resource ownership until physical completion or platform kill. Prove: local signals
  plus selected Apps proxy process receipt. If a temporary durable observer is used,
  declare instrumentation, bind source/probe/bundle and actual STOPPED correlation,
  require original report and launcher-main return within the configured bound, then
  verify the committed row from a fresh post-stop connection. Outstanding tools stay
  outstanding; observer insertion cannot extend the candidate shutdown allowance.
  Preserve committed `cancelled` on orderly application stop or `interrupted` on
  process/lease loss; both must retain the same unknown-effect barrier without replay.
  Keep the separate killed-process control strictly `interrupted`.
- [ ] **WP-29 — Prove browser and OAuth remote-TUI Apps access.** Depends: WP-11, WP-22, WP-25, WP-28.
  Drive the same accepted session through browser and remote terminal `/api` routes;
  prove correct OAuth audience/scope/token refresh and denial. Prove: simultaneous
  two-interface send/approve/reconnect journey behind actual ingress, without copying
  tokens into URLs, settings fixtures, logs, screenshots or durable records.
- [ ] **WP-30 — Complete cross-component security qualification.** Depends: WP-17, WP-22, WP-25, WP-29.
  Exercise origin/CORS/CSRF where applicable, proxy trust, cookie/token policy,
  request/event bounds, XSS/unsafe links/Markdown, path traversal, session enumeration,
  payload permission revocation, cache leakage and redacted errors. Prove: TP-002
  adversarial/browser cases and dependency audit; affected failures block release.
- [ ] **WP-31 — Wire and prove diagnostics before the pilot.** Depends: WP-27, WP-28, WP-30.
  Apply accepted telemetry naming/correlation contracts, metadata-only spans/audit,
  bounded delivery and failure isolation. Prove: real selected OTel receiver/exporter
  join plus content/credential exclusion, sampling/loss and receiver-failure cases;
  no-op traces or generated IDs cannot satisfy receiver support.

### Artifact, deployment and release acceptance

- [ ] **WP-32 — Prove package and extras isolation from installed artifacts.** Depends: WP-10, WP-17, WP-23, WP-24.
  Build core/API/UI/carrier wheels and source distributions plus browser tarballs.
  Install base, each selected extra, each independent package and supported combinations
  in clean environments. Prove: `make web-package-check`, strict installed typing,
  dependency closure and no source-path escape; old documented TUI installs still work.
- [ ] **WP-33 — Prove reproducible and compatible clean builds.** Depends: WP-32, WP-02.
  Build twice from clean locked sources with declared toolchain/version metadata;
  compare contents and deterministic hashes or explain permitted metadata variation.
  Test published package version constraints, schema compatibility, old/new client
  negotiation and update/rollback. Prove: artifact manifests and compatibility matrix.
- [ ] **WP-43 — Build an independent fabricated embedder repository.** Depends: WP-32, WP-33.
  Create a separate consumer fixture with its own lock/build configuration, import
  installed public packages, supply custom auth/ports/components and embed mounted
  API/UI into its own host. Prove: offline dummy and qualified durable journeys,
  independent API/UI replacement and client-only build; no source checkout path,
  private imports, customer configuration or undeclared shared workspace dependency.
- [ ] **WP-34 — Run platform-neutral full-stack fault acceptance.** Depends: WP-19, WP-22, WP-30, WP-32.
  Use installed artifacts and real browser/terminal/HTTP with fabricated model/tools.
  Race two users/interfaces, kill at acceptance/effect/result boundaries, reconnect
  after retention gaps and revoke access midstream. Prove: TP-001/TP-002 outcomes,
  stable accepted facts, bounded workers and explicit unknown/history/reset failures.
- [ ] **WP-35 — Run selected Apps deployment acceptance.** Depends: WP-26, WP-27, WP-28, WP-29, WP-31, WP-34.
  Prepare the exact deployment bundle, resource/permission inventory and operator
  runbook; after separate authorization deploy to the selected test target. Prove:
  `make web-platform-check` with actual ingress, durable restart, credential rotation,
  shutdown and failure recovery. Unavailable target access leaves this task blocked.
- [ ] **WP-36 — Measure and protect interface budgets.** Depends: WP-34, WP-35.
  Measure existing PRD list/page/follow/startup budgets and bounded CPU/memory/queue/
  reconnect behavior for declared datasets and profiles; distinguish local and Apps
  latency. Prove: reproducible benchmark receipt and ratchets; record failed targets
  and resolve upstream rather than inventing performance support from fixture speed.
  Owner decision on 2026-11-02: the current controlled performance gate is six
  local profiles / 54 metric nodes (7/7/17/11/2/10). Apps handshake/list latency
  is optional observational provenance, outside pass/fail; default controlled
  qualification does not require it or emit its two qualified nodes. Preserve
  historical seven-profile/56-metric files and all source28 failures unchanged.
  This changes performance allocation only; no functional Apps AC or task is waived.
  The [current controlled-node policy](evidence/web-platform/reference-performance/controlled-policy-20261102.json)
  lists 54 exact expected nodes without claiming execution. Current closure uses
  that set and source-bound final-admission joins; the source28 56-node closure
  candidates remain historical planning inputs, not current qualified evidence.
  Source27 disposition (2026-11-02): all seven actual reference profiles were
  schema/source/hash verified and their first measured ratchets established at
  the uniform predeclared observed × 1.2 factor. See the
  [accepted review](evidence/web-platform/performance-budget-review27-draft.md)
  and [seven exact establishment nodes](evidence/web-platform/performance/source27-first-establishment/first-baseline-establishment.json).
  PRD NFR-7 and TP-002 now explicitly replace ambiguous isolated commit/engine/
  total-memory/import definitions with the qualified public operational boundaries;
  original memory-list/volatile-loop-lag/PTY misses and unvalidated meanings remain
  visible. The historical selected metric files remain unchanged; current comparisons use the owner-selected six local profiles above;
  this is not a historical regression pass, old stricter-cap enforcement or SLA.
  The task stays unchecked pending its WP-35 dependency, Root semantic/manual
  reconciliation and overall acceptance; establishment does not close them.
- [ ] **WP-37 — Write installed quickstarts, extension guides and operator runbooks.** Depends: WP-32, WP-34, WP-35.
  Cover base/TUI/API/UI/client-only embedding, alternate transports/components,
  advanced ports, auth/mount configuration, migrations, deployment rollback, shutdown,
  recovery and service support limits. Prove: copy-paste clean-environment examples,
  safe diagnostic retrieval recipes and API/generated-schema reference links.
- [ ] **WP-38 — Capture verified public journeys.** Depends: WP-34, WP-35, WP-37.
  Follow `docs/microsite.md`: fabricate all data, rerun each advertised assertion,
  capture real browser/terminal screenshots or short video, and retain versioned
  reproducible capture recipes. Prove: evidence metadata, credential/content scrub
  and publishable media tied to exact installed profiles; do not stage synthetic proof.
- [ ] **WP-44 — Audit deterministic acceptance closure.** Depends: WP-34, WP-35, WP-38, WP-43.
  Inventory FR-65–70, NFR-9, all US-065–069 ACs, tasks and exact executable test
  IDs/implementation paths from TD-065–069 and TP-002/STP-001. Prove: a
  machine-checkable manifest fails on missing/duplicate
  mappings, empty inventories, skipped/xfail required cases or incomplete evidence;
  ensure each AC has one primary proving layer and every required edge closes.
- [ ] **WP-39 — Close review and final release gates.** Depends: WP-30 through WP-38, WP-43, WP-44.
  Sol reviews changed public boundaries, security/ownership, all task evidence and
  source/artifact identity once; Luna fixes blocking findings and reruns affected gates.
  Prove: `make web-release-check`, full required core/source/browser/installed/profile
  matrix, licenses/notices/SBOM and no accepted-path placeholders or unchecked scope.
- [ ] **WP-40 — Prepare authorized publication and deployment disposition.** Depends: WP-39.
  Prepare release notes, deployment checklist, immutable artifact hashes, tested
  versions/subsets, rollback instructions and evidence links. Keep signing/channel/
  registry and production target approvals explicit. Prove: a reviewable release
  packet; publishing/tagging/deploying requires separate user authorization and receipts.

### Acceptance mapping

TP-002 allocates proving layers; STP-001 owns exact cases and the UX coverage map.
The following task mapping covers every currently defined story criterion.
WP-44 checks it against the actual story/case inventory before execution closes.

| Requirement / feature | Story criteria | Primary implementation tasks |
|---|---|---|
| FR-66 / FEAT-013 | US-065-AC1, AC4, AC5, AC8 | WP-08 (construction, borrowing, actual workers, compatibility) |
| FR-66 / FEAT-013 | US-065-AC2 | WP-09 (advanced-port conformance) |
| FR-66 / FEAT-013 | US-065-AC3 | WP-10 (injected terminal) |
| FR-66 / FEAT-013 | US-065-AC6 | WP-07 (retained current-authority client) |
| FR-66 / FEAT-013 | US-065-AC7 | WP-11 (remote terminal parity) |
| FR-65/67 / FEAT-014 | US-066-AC1 | WP-17 (complete method/capability mapping) |
| FR-65/67 / FEAT-014 | US-066-AC2, AC3 | WP-14 (acceptance and retry) |
| FR-65/67 / FEAT-014 | US-066-AC4, AC5 | WP-15 (commit-aware replay and revocation) |
| FR-65/67 / FEAT-014 | US-066-AC6, AC7 | WP-42 (durable results and target-turn cancel) |
| FR-65/67 / FEAT-014 | US-066-AC8 | WP-11 (optional Python remote-client parity) |
| FR-68 / FEAT-015 | US-067-AC1, AC5 | WP-18 (independent client and exact wire values) |
| FR-68 / FEAT-015 | US-067-AC2 | WP-20 (injected React client) |
| FR-68 / FEAT-015 | US-067-AC3, AC4, AC7 | WP-21 (replacement, safe content, accessibility) |
| FR-68 / FEAT-015 | US-067-AC6 | WP-23 (installed prefix/asset journey) |
| FR-69 / FEAT-016 | US-068-AC1, AC5 | WP-29 (shared interfaces and one-winner decisions) |
| FR-69 / FEAT-016 | US-068-AC2, AC3, AC4 | WP-25 (owner/user/app authority) |
| FR-69 / FEAT-016 | US-068-AC6, AC7 | WP-26 (database acquisition and migration refusal) |
| FR-69 / FEAT-016 | US-068-AC8 | WP-28 (termination and recovery) |
| FR-69 / FEAT-016 | US-068-AC9 | WP-24 (complete carrier bundle) |
| FR-69 / FEAT-016 | US-068-AC10 | WP-31 (actual captured diagnostics) |
| FR-70 / FEAT-017 | US-069-AC1, AC2, AC3, AC7 | WP-32 (installed closures, exports and dependency qualification) |
| FR-70 / FEAT-017 | US-069-AC4 | WP-43 (separate embedder and substitution) |
| FR-70 / FEAT-017 | US-069-AC5, AC6 | WP-33 (manifest compatibility and explicit failure) |
| FR-70 / FEAT-017 | US-069-AC8 | WP-37 (claims grounded in exact evidence) |
| NFR-9 | All five story sets | WP-01/WP-39 (enforced boundaries and semantic review) |

### Command contract

Only `uv sync --locked`, `make check CHECK_BASE=HEAD`, existing Python boundary
scripts and existing tests are present at plan creation. The following `make`
targets are planned interfaces that WP-01 must implement and document; later tasks
cannot close by citing a target that does not exist or skips an unbuilt package.
Each target uses locked tooling and reports its actual selected versions and tests.

| Planned command | Required proof |
|---|---|
| `make web-boundaries` | Python and TypeScript package directions, private-access/cycle checks and planted allowed/forbidden controls |
| `make web-client-contract` | Public client/host/provider/advanced-port dummies, conformance, replacement typing and lifecycle |
| `make web-api-contract` | Real HTTP commands/reads/errors, identity, schema and SSE/reconnect conformance |
| `make web-ui-check` | TypeScript generation/lint/shape/type/unit gates, real Playwright journeys and accessibility/security cases |
| `make web-package-check` | Built wheels/sdists/tarballs, clean installed closures, extras matrix, public typing and asset checks |
| `make web-check` | Required local/pre-commit/CI aggregate: unchanged core gate plus boundaries, client/API/browser/package gates; unqualified live profiles reported explicitly |
| `make web-platform-check` | Operator-selected Apps/Gateway/database/trace profile gates; missing access is blocked, never a successful skip |
| `make web-release-check` | Complete selected-source `web-check`, clean installed artifacts, required live profile/performance/documentation/evidence checks |

### Source29 timing-remediation continuation

Owner direction on 2026-11-02: let functional verification be slow; avoid time
measurements and timeouts unless required by the behavior or necessary emergency
protection. Performance qualification is separate, explicit and isolated on
known hardware. CONTRACT-007, TD-068, TP-001/TP-002 and STP-001 allocate the
changes below. All 45 existing task IDs and all 41 ACs remain required; this is
corrective work within those tasks, not a reduced release or a second tracker.

| Order / existing tasks | Luna implementation scope after Sol design review | Verification before moving on |
|---|---|---|
| 1 / WP-00, WP-01, WP-34 | Inventory timing uses in tests/scripts, TS/Vitest/Playwright, installed/a11y drivers and runner configuration. Classify semantic deadline/expiry, event coordination, emergency watchdog and actual performance. Reconcile AGENTS.md's existing 180s child rule with the owner direction explicitly; preserve collection/group bounds and 85% coverage. | Reviewable disposition by path/use; no case hidden by blanket filter, skip, relaxed assertion or lower coverage. Preserve source28 map, raw failed attempts and source27 baseline bytes. |
| 2 / WP-05, WP-28, WP-30, WP-35 | Implement TD-068's managed-peer event/state coordination and retained group cleanup. Preserve natural lease duration/expiry, exact stale-owner fencing, recovery projections, unknown-effect barriers and credential privacy. | Barrier-driven delayed readiness, explicit failure/EOF, leader-exit/descendant-pipe and repeated-cancellation controls. Genuine expiry remains a later selected integration proof. |
| 3 / WP-01, WP-34, WP-38, WP-43 | Remove incidental elapsed/startup gates and progress sleeps across ordinary Python, installed terminal, browser and a11y checks. Inject clocks in deterministic budget tests; preserve product deadlines and real worker lifetime. Separate real benchmark execution from ordinary check inventory; retain synthetic validation/rejection/cleanup controls. | Focused behavior tests and inventory completeness; ordinary functional checks allow an occupied host; real temporal cases require their actual initial-state preconditions, not startup-speed limits. No broad benchmark workloads in ordinary checks. Verify actual framework/runner watchdog dispositions and truthful incomplete results. |
| 4 / WP-36 | Implement the PRD NFR-7 explicit performance admission contract: the existing known Mac under owner-controlled exclusive reservation, an OS nonblocking benchmark lock and independent pre/through/post activity observation, with typed injectable admission/observer composition outside the library core. Refuse/defer when binding, exclusivity or conditions are unknown; the lock alone excludes only cooperating owners. Qualify exactly six local profiles/54 metrics; Apps latency is optional observational provenance outside pass/fail, with no required precollected Apps input. Keep measurements and baseline disposition separate from functional success. | Offline admission/ratchet/schema/process-owner controls with fabricated observations; preserve both actual source28 failures and the no-workload readiness deferral. No automatic baseline replacement or threshold increase. |
| 5 / WP-00, WP-32–38, WP-39, WP-40, WP-44 | Freeze the changed source29 inventory, build/install its artifacts and run the complete required functional/installed/browser gates. Run selected live expiry/recovery only after establishing the required live initial states and retained ownership; slow ordinary functional execution is permitted on the occupied host. Independently qualify the performance policy/reference before WP-36 closure. | Fresh source29 aggregate and profile receipts, retained failures and exact evidence joins. Root manual US-069-AC8 review, all 41AC/45WP reconciliation, closure audit and release postcheck follow actual successful proof. No publication, commit, production apply or final cleanup is authorized by this continuation alone. |

Source28 attempt05 failed on initial peer readiness before lease/fencing
assertions; its selected cases never began and its created secret was revoked
with absence verified. The [closed failure archive](evidence/web-platform/qualification-ram/source28-attempt05-failed/README.md)
retains that outcome. The stopped offline EOF diagnostic remains prepared and
unexecuted. Other jobs on this Mac are owner-confirmed context, not proof of
the cause of any prior timeout or performance miss. Ordinary functional checks may proceed slowly on the occupied host. Performance
measurement remains unadmitted until the owner reservation and independent
conditions satisfy TP-002. Genuine expiry/recovery cases require verified
initial temporal state; slow setup cannot be mistaken for lease expiry.

Sol owns plan and boundary review; Luna owns the ordered implementation and
meaningful focused checks. Reconcile new findings in these existing tasks.
Source29 qualification must follow code changes; copying source28 PASS labels
or the source27 first-establishment result cannot close this continuation.
Bind any deliberately changed AGENTS.md policy bytes into the source29 proof,
through the new inventory or an explicit governing-file hash. The existing
source28 inventory does not include that file and must remain unchanged.
If test lanes are introduced, prove exhaustive/disjoint collection and keep
default coverage over scripts/packages at the existing global 85% branch floor.
Runner/generic-process, installed PTY, JavaScript timing and AGENTS.md edits
follow the first managed-process and benchmark-control changes as a separately
assigned integration phase. TP-001 selects a configurable 30-minute child
emergency default for that phase, with retained process-group cleanup and
structured incomplete evidence. Update the receipt producer/strict consumers
and pending-inventory reporting; increasing the old 180-second constant alone
does not implement the policy. This does not change product deadlines or grant
an actual benchmark/live qualification run.
Shared process tooling must use explicit owner scope: suite-job children can
own a retained group, while nested benchmark children preserve their existing
supervisor's group or register with it. Do not change generic session ownership
silently across lint, build and benchmark callers or add a temporary-file
runtime dependency.

Phase C is assigned to Luna after the managed revision's named focused tests,
format/lint/type checks and Sol review. Its source scope is `scripts/process.py`,
`test_suite.py`, `test_evidence.py`, `quality.py`, `web_artifacts.py`,
`web_gate_receipt.py`, `web_quality.py`, necessary strict receipt consumers,
AGENTS.md and their focused runner/publication/tooling/pipeline tests. TP-001's
Phase C implementation contract supplies the exact result, status, containment,
ownership and source-policy joins. Keep Makefile under the benchmark lane's
ownership; coordinate an explicit transfer if a phase C change needs it. PTY,
React/browser and broader unit-barrier families follow phase C. This allocation
does not authorize a full qualification, source29 freeze, cloud collector or
actual performance run.

Phase D follows Sol review of phase C. It covers all incidental qualifier and
interface readiness under TP-002's Phase D allocation: log follower attachment
and privacy anchors; Apps control-plane/restart/UI waits; identity-convergence
fake clocks and incomplete observation exhaustion; ordinary worker barriers;
installed PTY; React debounce and browser/a11y framework waits. Keep the 41 AC
rows and existing task ownership; do not waive a live criterion or broaden SDK
refusal classification. Preserve actual unexpired-JWT and natural-expiry proof,
the single shared identity observation budget, core caller I/O deadlines and
the carrier's intrinsic stop-and-report allowance. Prepare future source29
warmup operators from the corrected harness, not the fixed-15-second legacy
recipe. This is a planning allocation only; phase D code/tests or live operators
require their separately assigned next-phase GO.

## Execution Contract

The user authorized full IP-002 implementation on 2026-10-29. Sol selects ready
tasks and Luna executes them through their gates. Source changes, dependency
qualification, builds and deterministic tests are authorized. Platform actions
remain confined to the separately authorized temporary development target.
Branches, commits, registry publication and production deployment require their
own explicit authorization. A task checkbox closes only after its required proof.
Stop only affected work for unresolved authority conflicts, missing access,
required human decisions or concrete failed prerequisites. Ordinary debugging and
required propagation remain part of the authorized implementation scope.

No comparable Sol/Luna usage baseline exists for this combined scope. Token/time
estimates are unknown; no enforced budget is claimed. Use WP-00–03 as the initial
bounded implementation probe after authorization, record observed input/cached/
output tokens and verification effort where the host exposes them, then set an
execution ceiling before the remaining build. Without host metering, record that
limit once. Never add paid workspace/model/service use without its authorization.

Register bounded newly discovered technical unknowns under the task that owns
them, with question, primary evidence, maximum probe scope, pass/refusal outcome,
owner and exact blocked task IDs. A failed probe changes the accepted design or
leaves the affected profile blocked. It never silently weakens acceptance.

## Continuation Evidence

Implementation checkpoint on 2026-10-29: the [baseline receipt](evidence/web-platform/baseline-20261029.json)
retains 343 source/build digests, the successful locked sync and the full baseline
failure after 73 of 80 passing groups. Coverage reported a missing SQLite context
table; no assertion failure preceded it. The isolated retry was invalidated when
implementation edits were released and cannot qualify the original baseline.
WP-00 remains unchecked pending a complete unfiltered integrated-source gate.
WP-00-C1's focused isolation proof corrects the known shared-file defect.

Planning verification on 2026-10-29: the installed HELIX implementation-plan
validator reported zero blocking findings, zero warnings and zero information
findings. The aggregate audit passed all 29 changed/new Markdown artifacts with
zero validation failures. New relative Markdown links and `ddx` target IDs
resolved; no duplicate artifact IDs were found. A focused plan audit found 45
unique unchecked tasks with resolved acyclic dependencies. All 41 unique story
criteria map once to primary tasks and exactly once to STP-001 cases, with no
missing or extra criteria. `git diff --check` remained clean. Validation used
Python 3.11.17 and cached PyYAML 6.0.3 without installing dependencies.

These are planning checks. They establish document structure and execution
traceability; they do not establish runtime, installed-artifact, database, browser,
Apps ingress, shutdown or receiver conformance. All 45 implementation tasks and
validation boxes remain unchecked. WP-02–06 and WP-41 retain bounded unresolved
technical probes before their dependent implementation/support gates can close.

Use this task list as the sole selected task tracker. Keep a compact execution
record beside the plan at `04-build/evidence/web-platform-continuation.md` after
implementation starts; avoid a competing backlog. Record plan content digest,
active task IDs/agent ownership, completed IDs and safe evidence paths, actual diff,
versions/profiles, failed commands, decisions, blockers and the exact next action.

On resume, read AGENTS.md, `.helix.yml`, the bound skill, upstream changes, this
plan and that record. Reconcile boxes with source changes, generated artifacts and
actual receipts. Reopen a completed box if its proving inputs changed. Claim only
ready tasks, retain in-flight worker/ownership facts and continue from the next
unsatisfied gate; a context-window boundary is not a completed task.

Each completion receipt records task ID, command, outcome, tested artifact/source
digest, tool/runtime versions, selected subset/profile, relevant test inventory,
safe evidence location and unresolved limits. Failures remain visible. A source
checkout pass cannot replace an installed-artifact gate, a mocked proxy cannot
replace Apps ingress proof, and documentation review cannot satisfy runtime work.

## Validation Plan

- [ ] Every required capability maps to a task and TP-002 case; every dependency resolves.
- [ ] Public construction/lifecycle, TUI migration, independent replacement and advanced-port extension pass their installed consumer tests.
- [ ] API/schema/browser streaming semantics share canonical fixtures and current-identity/refusal cases.
- [ ] All selected package/extra combinations pass clean build, import, typing, license and dependency gates.
- [ ] Full core requirements and existing consumer acceptance boundaries remain enforced.
- [ ] Real receiver, ingress, database and process evidence qualify each advertised platform profile separately.
- [ ] Required documentation, real fabricated screenshots and release/deployment packet match the tested artifacts.

## Risks and Rollbacks

| Risk | Impact | Response | Rollback |
|---|---|---|---|
| Facade exposes private construction by accident | Consumers remain coupled | Boundary/installed replacement negatives before migration | Keep supported old entrypoint; fix facade before removal |
| Schema and browser types drift | Silent command/event meaning loss | Generated codecs and cross-language corpus | Refuse unsupported versions; retain compatible package set |
| Slow/revoked stream leaks data or blocks work | Cross-owner disclosure or resource exhaustion | Per-variant authorization and bounded queue/replay tests | Close stream with typed reset/refusal; retain committed recovery |
| Apps identity/proxy assumptions fail | Wrong authority or broken remote interfaces | WP-04 proof before carrier wiring | Disable affected mode/profile; retain platform-neutral host |
| Lakebase profile differs from qualified PostgreSQL | Durability or lease claim is false | Separate SQL/profile/process qualification | Refuse unqualified backend; preserve external durable data |
| Platform terminates active physical work | Unknown effects after restart | SIGTERM gates and journal-derived recovery | Report unknown effects; never blindly replay |
| Packaging accidentally pulls web/vendor dependencies into base | Embedding regression | Clean per-distribution/extra installed closure matrix | Restore prior dependency constraints and entrypoints |
| Live profile access or release authority is unavailable | Platform/release gate cannot finish | Mark exact blocked task; finish independent tasks | Retain artifacts and release packet without claiming deployment |

## Exit Criteria

Plan readiness means governing scope, package boundaries, ordered tasks, bounded
unknowns, ownership, evidence and restart rules are concrete. It does not mean
any implementation box has passed.

Execution is complete only when every required task WP-00–44 and validation box
passes with its recorded evidence; all required release/profile gates pass on the
same selected artifact set; public examples and media match those artifacts;
canonical documents and review are complete; and no required blocker remains.
Any deliberately unsupported profile is named in the accepted scope/support
matrix before it can leave the release gate. A failed required profile cannot
be removed merely to report success. Publication/deployment completion, when
separately authorized, also requires the corresponding external receipts.
