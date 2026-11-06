---
ddx:
  id: STP-001
  authoring:
    home: repo
  links:
    - id: US-065
      kind: informed_by
    - id: US-066
      kind: informed_by
    - id: US-067
      kind: informed_by
    - id: US-068
      kind: informed_by
    - id: US-069
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: SD-003
      kind: informed_by
    - id: TP-002
      kind: informed_by
    - id: IP-002
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: CONTRACT-007
      kind: informed_by
---

# Story Test Plan: STP-001-Web Platform Acceptance

## Story Reference

**User Stories**: [[US-065-public-client-host]], [[US-066-http-and-remote-session-parity]], [[US-067-replaceable-react-session-ui]], [[US-068-databricks-shared-session-carrier]], [[US-069-independent-package-adoption]]
**Technical Designs**: [[TD-065-public-client-host]], [[TD-066-http-and-remote-session-parity]], [[TD-067-replaceable-react-session-ui]], [[TD-068-databricks-shared-session-carrier]], [[TD-069-independent-package-adoption]].
**Related Solution Design**: [[SD-002-web-interfaces]], [[SD-003-web-and-databricks-apps-carrier]]
**Project Test Plan**: [[web-platform-test-plan]]

This aggregate plan is intentionally the criterion-level proof surface for the five linked stories in this delivery slice. TP-002 allocates strategy and layers; this file owns the stable AC-to-test mapping and UX coverage.

## Scope and Objective

**Goal**: Define executable evidence for every active criterion in US-065 through US-069 before implementation, including public-client parity, HTTP and browser behavior, Databricks authority/lifecycle and independent package consumption.
**Blocking Gate**: `make web-check` for deterministic aggregate verification; `make web-release-check` for clean artifact installs; `make web-platform-check` for the explicitly selected live Apps/Gateway/database/trace profiles. Missing access to a required selected profile blocks its acceptance; it is not a successful skip.

**In Scope**
- All 41 acceptance criteria in US-065 through US-069.
- User-visible browser states and keyboard interaction in the reference UI.
- Package-boundary proof using built artifacts and an external fabricated embedder.
- Live ingress, identity, storage and process evidence for any Databricks support claim.

**Out of Scope**
- Universal compatibility with every browser, database, Databricks workspace or app server.
- Actual registry publication, production deployment, customer data, credentials or customer-specific application composition.
- Claiming live platform qualification from mocks, bundle validation, or generic local ASGI tests.

## Acceptance Criteria Test Mapping

The 41 rows name executable targets and required assertions. A target is not an observed pass. Completion requires the exact observed node IDs and current source-bound receipts in IP-002 execution evidence and the WP-44 closure manifest. Browser/Vitest IDs include their full reported titles and parameter values; installed IDs are emitted only after their assertions succeed. Each proving source must cite its stable `@covers` ID. Selected live carrier criteria remain open until their deployed-profile cases execute.

| AC ID | Acceptance Criterion (Given/When/Then) | New or Existing Test(s) to Create or Run | Asserted Behavior (what the test verifies) | AC-ID Citation (`@covers`) | Test Level | File or Command | Setup / Data | Notes |
|---|---|---|---|---|---|---|---|---|
| US-065-AC1 | Given core-only install, when public facade is used, then no optional UI/server dependency or private import is needed. | `installed::base::host-recovery` (`scripts/web_consumer.py`) | Clean environment imports and executes the facade with only core dependencies installed. | `@covers US-065-AC1` | Installed package | `make web-release-check` | Fresh venv; built core wheel only. | NFR-9 boundary. |
| US-065-AC2 | Given fabricated conforming ports, when injected, then the client conformance suite passes without real providers. | `tests/test_public_port_conformance.py::test_public_client_conformance_across_complete_injected_ports[alternate-ports]` and `[ordinary]`; `tests/test_us065_semantic_gaps.py::test_successful_history_link_through_both_public_construction_paths[ordinary]` and `[alternate-ports]`; `tests/test_public_host_port_wiring.py::test_from_ports_forwards_attempt_factory_tracer_and_clock` | Declared operations share the injected-port contract, a valid history link resolves to its equivalent record, and custom attempt/tracer/clock ports reach execution. | `@covers US-065-AC2` | Contract | `make web-client-contract` | Deterministic fake ports and clock. | No provider credentials. |
| US-065-AC3 | Given an injected client, when TUI starts, then it creates no second runtime or storage authority. | `tests/test_public_host_ownership.py::test_borrowed_view_close_preserves_other_client_and_accepted_work` | TUI routes operations through the supplied client and does not construct a runtime/store. | `@covers US-065-AC3` | Integration | `make web-client-contract` | Injected client spy; construction counters. | Include forbidden-import check. |
| US-065-AC4 | Given two borrowed consumers, when one closes, then the other remains usable and accepted work continues. | `tests/test_public_host_ownership.py::test_borrowed_view_close_preserves_other_client_and_accepted_work` | Closing a view leaves the second view attached and the accepted operation completes. | `@covers US-065-AC4` | Integration | `make web-client-contract` | Two borrowers; barrier-held accepted task. | Distinguish wait cancellation from work cancellation. |
| US-065-AC5 | Given a worker still runs, when host shutdown reaches its bound, then ownership is retained until physical completion. | `tests/test_public_host_ownership.py::test_public_host_incomplete_close_retains_root_until_physical_worker_exit`; `tests/test_owned_jsonl_shutdown.py::test_owned_jsonl_wait_closed_tracks_actual_file_worker_after_timeout`; `tests/test_postgres_resource_shutdown.py` held-connection/worker cases | Shutdown reports bounded outstanding work and does not release owned resources before worker completion. | `@covers US-065-AC5` | Integration | `make web-check` | Blocking worker barrier; controlled shutdown clock. | No sleep-based timing. |
| US-065-AC6 | Given retained actor-bound client, when policy is revoked, then disclosure and execution refuse under current policy. | `tests/test_public_port_conformance.py::test_public_client_conformance_across_complete_injected_ports[alternate-ports]` and `[ordinary]`; `tests/test_us065_semantic_gaps.py::test_revoked_retained_session_refuses_send_before_model_or_tool_dispatch[ordinary]` and `[alternate-ports]` | Reads refuse under the latest policy; sending on the retained session also refuses with model and tool dispatch counts unchanged. | `@covers US-065-AC6` | Integration | `make web-client-contract` | Actor fixture with mutable policy provider. | Verify no stale authorization cache. |
| US-065-AC7 | Given supported remote client, when TUI uses it, then declared consumer cases match embedded behavior. | `tests/test_us065_semantic_gaps.py::test_local_and_remote_tui_return_the_same_typed_final_result`; `tests/test_remote_terminal.py::test_remote_terminal_reads_and_approves_through_http_without_owning_client`; `tests/test_reference_clients.py::test_terminal_ui_shows_approval_then_final` | One deterministic fixture proves equal typed terminal status, reason, messages, tool calls and pending values through embedded and HTTP-backed TUI journeys; borrower lifetime remains separate. | `@covers US-065-AC7` | Contract | `make web-client-contract` | Same fixtures across local and remote providers. | Unsupported capabilities remain explicit. |
| US-065-AC8 | Given an existing composition example, when facade is installed, then documented compatibility path works. | `installed::base::legacy-composition` (`scripts/web_consumer.py`) | Existing supported import/construction path still creates a functioning facade. | `@covers US-065-AC8` | Installed package | `make web-release-check` | Baseline example against built artifact. | Pin compatibility cohort. |
| US-066-AC1 | Given a conforming client and explicit authenticator, when capabilities are read, then every client operation is available or explicitly unsupported. | `tests/client/test_remote_client.py::test_remote_client_executes_against_real_journal_host_and_decodes_counters` | Capability keys equal the complete Client protocol method set and the configured provider's support states. | `@covers US-066-AC1` | Contract | `make web-client-contract` | ASGI app and public client; schema fixtures. | Capability listing is distinct from the FR-67/WP-17 and AC8 route-conformance obligation. |
| US-066-AC2 | Given valid send, when accepted, then response contains stable operation identity and acceptance outcome. | `packages/web-api/tests/test_api_contract.py::test_send_accepts_idempotently_and_result_is_nullable_until_complete`; `tests/client/test_remote_client.py::test_remote_client_returns_and_closes_an_accepted_turn` | First accepted send returns the same idempotent identity later used for status/recovery. | `@covers US-066-AC2` | Contract | `make web-api-contract`; `make web-client-contract` | Fabricated message and idempotency key. | No inferred completion. |
| US-066-AC3 | Given response loss after acceptance, when caller retries, then no second operation is created. | `tests/client/test_remote_client.py::test_remote_client_returns_and_closes_an_accepted_turn` | Retry with the same request identity returns the original accepted operation. | `@covers US-066-AC3` | Integration | `make web-client-contract` | Drop response after commit; retry same key. | Assert one dispatch. |
| US-066-AC4 | Given feed commits and resume watermark, when reconnecting, then events resume in contract order after the watermark. | `packages/client-ts/test/commit.test.ts::@covers US-066-AC4 replays incomplete multi-event commits and resumes only after their final event` | No committed event is duplicated or omitted within retention; gap/reset is explicit. | `@covers US-066-AC4` | Contract | `make web-ui-check` | Interleaved commits and reconnect cursor. | Reset on retention loss. |
| US-066-AC5 | Given active feed and changed policy, when next batch is delivered, then revoked content is withheld. | `tests/client/test_remote_http_server.py::test_remote_feed_reauthorizes_on_each_committed_delivery_over_tcp`; `tests/client/test_remote_http_server.py::test_feed_withholds_next_commit_after_constant_identity_loses_snapshot_access` | The next bounded batch is denied after authorization changes during a stream. | `@covers US-066-AC5` | Integration | `make web-client-contract` | Mutable identity/policy resolver. | Batch delivery boundary. |
| US-066-AC6 | Given the API restarts after an accepted send, when result is read, then journal-backed terminal outcome or explicit pending/unavailable is returned without rerun. | `tests/ui/reference.spec.ts::@covers US-066-AC6 real browser reads the terminal result recovered from a killed process`; `tests/client/test_remote_client.py::test_remote_result_survives_api_and_host_restart` | Process replacement preserves accepted outcome authority; no process-local handle or second dispatch is required. | `@covers US-066-AC6` | Integration | `make web-check` | Restarted API; journal-backed result fixture and unavailable state. | WP-41/42; CONTRACT-003/006. |
| US-066-AC7 | Given turn 1 ended and turn 2 is active, when delayed cancellation targets turn 1, then turn 2 continues. | `tests/client/test_remote_client.py::test_stale_remote_cancellation_leaves_the_new_active_turn_running` | Cancellation compares the requested turn identity and leaves the newer active turn untouched. | `@covers US-066-AC7` | Integration | `make web-client-contract` | Old completed turn and newer barrier-held turn. | WP-41/42. |
| US-066-AC8 | Given endpoint and credential provider, when remote Python client uses Client methods, then it reaches the server contract without API/FastAPI imports. | `tests/client/test_remote_api_conformance.py::test_api_routes_forward_complete_public_protocol_and_decode_typed_results`; `tests/client/test_remote_client.py::test_remote_client_executes_against_real_journal_host_and_decodes_counters`; `installed::remote::separate-server-contract` | All public methods forward exact typed arguments/results, including supported and unsupported archive paths; installed client import closure excludes FastAPI and `tether_web_api`. | `@covers US-066-AC8` | Installed package | `make web-release-check` | Built core/remote-client artifacts and loopback API. | WP-11; test dependency graph. |
| US-067-AC1 | Given framework-free client package, when imported without React, then client APIs work without credential singleton. | `scripts/web/check-packages.mjs::framework-free-import` | Import closure excludes React and all auth is passed through explicit client construction. | `@covers US-067-AC1` | Installed package | `make web-ui-check` | Packed browser package in clean project. | Check package exports. |
| US-067-AC2 | Given injected browser client, when reads and commands occur, then all calls use that client. | `packages/ui/test/workspace.test.tsx::composable session presentation @covers US-067-AC2 @covers US-067-AC3 uses the injected instance and public layout/message slots`; keyboard-decision command cases | Hook calls reach the provided client and never a hidden default instance. | `@covers US-067-AC2` | Unit | `make web-ui-check` | Vitest and Testing Library; mock client. | Include rejection path. |
| US-067-AC3 | Given typed component extension slots, when consumer replaces them, then public seams receive the same session state. | `packages/ui/test/workspace.test.tsx::composable session presentation @covers US-067-AC2 @covers US-067-AC3 uses the injected instance and public layout/message slots`; `scripts/web/check-packages.mjs::typed-react-slots` | Replacement components receive typed props and preserve session behavior. | `@covers US-067-AC3` | Integration | `make web-ui-check` | Consumer-defined fixture components. | No private imports. |
| US-067-AC4 | Given untrusted message/tool text, when rendered, then it remains text and cannot execute markup. | `packages/ui/test/workspace.test.tsx::composable session presentation @covers US-067-AC4 renders hostile message markup as literal text`; `tests/ui/reference.spec.ts` prefixed real-UI journey | Script-like fixture is displayed as text and produces no DOM execution. | `@covers US-067-AC4` | End to end | `make web-ui-check` | Fabricated hostile content in browser test. | Accessibility names remain useful. |
| US-067-AC5 | Given integers beyond JS safe range, when encoded and decoded, then exact values survive. | `packages/client-ts/test/client.test.ts::typed HTTP client @covers US-067-AC5 reconnects with the exact committed counter after an incomplete commit` | IDs/cursors preserve exact decimal values without float rounding. | `@covers US-067-AC5` | Contract | `make web-ui-check` | Boundary values above 2^53. | Test both directions. |
| US-067-AC6 | Given built API and UI under path prefix, when loaded, then asset URLs and API calls resolve under that prefix. | `tests/ui/reference.spec.ts::@covers US-067-AC6 real prefixed UI, approved turn, committed history and keyboard accessibility` in the installed combined profile | Built files load and requests stay within the configured prefix. | `@covers US-067-AC6` | Installed end to end | `make web-package-check` | Built wheels and browser artifact; prefix fixture. | Validate real packaged asset paths. |
| US-067-AC7 | Given keyboard-only user, when navigating and answering approval, then focus and actions remain operable. | `packages/ui/test/workspace.test.tsx::composable session presentation @covers US-067-AC7 supports keyboard decision true and retains accessible outcome focus` and corresponding `false` case; prefixed Chromium journey | All controls are reachable, current route is announced, dialog focus is managed and restored. | `@covers US-067-AC7` | End to end | `make web-check` | Playwright Chromium; fabricated approval. | Include `aria-current`. |
| US-068-AC1 | Given one carrier host and owner namespace, when same session is opened through browser and TUI, then committed state is shared. | Required executable target: `tests/ui/databricks-live.spec.ts::@covers US-068-AC1 @covers US-068-AC5 live browser, remote TUI and one approval winner` through `scripts/qualify_live_apps.py`; observed receipt pending. | Both consumers read the same durable committed session through public clients. | `@covers US-068-AC1` | Integration | `make web-platform-check` | Fabricated owner; selected external store profile. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC2 | Given two users, when either reads the other's session, then owner isolation denies disclosure. | Required executable target: `scripts/selected_profiles_owner.py::owner_isolation` through `scripts/qualify_selected_profiles.py`; observed receipt pending. | Cross-owner reads and mutations are refused at the API and client boundaries. | `@covers US-068-AC2` | Integration | `make web-platform-check` | Two independent users and session namespaces. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC3 | Given missing, expired, revoked or untrusted identity, when protected request arrives, then it is refused before dispatch. | Required executable target: `scripts/selected_profiles_identity.py::revocation_and_refusal` through `scripts/qualify_selected_profiles.py`; observed receipt pending. Also require `scripts/selected_profiles_expiry.py::credential_rotation` for genuine expired-identity refusal. | No client method runs for invalid request identity in either configured trust mode. | `@covers US-068-AC3` | Contract | `make web-platform-check` (local resolver tests: `make web-client-contract`) | Identity resolver doubles; conflicting headers. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC4 | Given user and app principals, when user Tether work and infrastructure work run, then each uses only its intended principal. | Required executable target: `scripts/selected_profiles_identity.py::principal_separation` through `scripts/qualify_selected_profiles.py`; observed receipt pending. | Model/session operations bind to user actor; explicit infrastructure calls bind to app role. | `@covers US-068-AC4` | Integration | `make web-platform-check` | Distinct fabricated principals and resource spy. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC5 | Given competing approval decisions and reconnect, when both race, then exactly one decision wins and feed reflects it. | Required executable target: `tests/ui/databricks-live.spec.ts::@covers US-068-AC1 @covers US-068-AC5 live browser, remote TUI and one approval winner` through `scripts/qualify_live_apps.py`; observed receipt pending. | One terminal decision is committed and reconnect reports the same result. | `@covers US-068-AC5` | Integration | `make web-platform-check` | Two user clients; synchronized barrier. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC6 | Given expiring database OAuth, when a new connection is created after expiry, then a current token is obtained without persistence. | Required executable target: `scripts/selected_profiles_expiry.py::credential_rotation` through `scripts/qualify_selected_profiles.py`; observed receipt pending. | New connection uses current credential and token never appears in pool state or logs. | `@covers US-068-AC6` | Platform profile | `make web-platform-check` | Named Lakebase/Postgres endpoint and short-lived test credential. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC7 | Given unmanaged or mismatched schema, when carrier starts, then it refuses without implicit migration or role widening. | Required executable target: `scripts/selected_profiles_example_runtime.py::selected_example_schema_refusal` through `scripts/qualify_selected_profiles.py`; observed receipt pending. | Actual example construction and ASGI startup succeed with the selected valid schema; missing-schema construction refuses before API/UI publication, cleans acquired resources and leaves schema/role grants unchanged. | `@covers US-068-AC7` | Platform profile | `make web-platform-check` | Disposable nonexistent schema and the explicitly selected nonadmin caller role. | App-principal deployment readiness is separately proved by AC4; direct database open alone does not qualify example readiness. |
| US-068-AC8 | Given SIGTERM and possible forced kill, when app shuts down and restarts, then committed facts recover and uncertain effects remain explicit. | Required executable target: `tests/ui/databricks-live.spec.ts::@covers US-068-AC8 live bounded stop retains unknown effect barrier after restart` through `scripts/qualify_live_apps.py`; observed receipt pending. | `ShutdownReport` and launcher main return by the declared bound; report names retained physical work. Preserve terminal `cancelled` for orderly application cancellation or `interrupted` for process/lease loss; require the same unresolved invocation, exactly one unknown-effect barrier and blocked close without replay or inferred success. The instrumented observer binds the actual stop, process/nonce, source/probe/bundle hashes and durable post-stop row. | `@covers US-068-AC8` | Platform profile | `make web-platform-check` | Deployed Apps app, external store, fault injection; separate killed-process control remains strictly `interrupted`. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC9 | Given locked sample bundle, when installed and built, then UI assets and app composition are present without runtime Node. | Required executable target: `scripts/selected_profiles_bundle.py::deployed_bundle_matches_lock` through `scripts/qualify_selected_profiles.py`; observed receipt pending. | Bundle validates, uv lock resolves, packaged UI assets exist, and runtime command needs no Node. | `@covers US-068-AC9` | Installed end to end | `make web-platform-check` plus `make web-package-check` | `examples/databricks-app`; built distributions. | Open selected-profile proof; local mechanisms are supplementary. |
| US-068-AC10 | Given successful and failed carrier operations, when logs are captured, then no credential or unauthorized content is recorded. | Required executable target: `scripts/qualify_selected_profiles.py::log_privacy` through `scripts/qualify_selected_profiles.py`; observed receipt pending. | Captured app/platform logs contain safe metadata only and redact auth/database values. | `@covers US-068-AC10` | Integration | `make web-platform-check` | Fabricated secrets/content markers; log capture. | Open selected-profile proof; local mechanisms are supplementary. |
| US-069-AC1 | Given built core artifacts, when installed alone in clean fixture, then direct embedding works without optional packages. | `installed::base::host-recovery` (`scripts/web_consumer.py`) | Isolated external project imports and uses core with web/TUI/platform packages absent. | `@covers US-069-AC1` | Installed package | `make web-release-check` | Fresh venv; wheelhouse only. | No source checkout on path. |
| US-069-AC2 | Given built optional Python artifacts, when installed together, then namespace ownership and required assets/types are correct. | `installed::novirhiza-tether::license-namespace-types`, `installed::novirhiza-tether-web-api::license-namespace-types`, `installed::novirhiza-tether-web-ui::license-namespace-types`, `installed::novirhiza-tether-databricks::license-namespace-types` | Installed distributions expose their declared namespaces and no collisions or missing static files. | `@covers US-069-AC2` | Installed package | `make web-release-check` | Built wheels installed into clean environment. | Distribution names follow ADR-022 pending registry check. |
| US-069-AC3 | Given packed browser artifacts, when installed outside monorepo, then exports and React peers work without deep imports. | `scripts/web/check-packages.mjs::typed-react-slots` (packed export, React-peer and strict consumer assertions) | External app imports only documented exports with declared peer dependencies. | `@covers US-069-AC3` | Installed package | `make web-release-check` | npm pack artifacts and clean fixture. | Lockfile-backed install. |
| US-069-AC4 | Given compatible packages, when provider and component are replaced, then behavior remains conformant. | `scripts/web/check-packages.mjs::typed-react-slots`; `examples/embed/tests/test_consumer.py::test_public_embedder_constructs_host_and_serves_fabricated_turn` in the clean external copy | Custom provider and component satisfy the shared contract scenarios. | `@covers US-069-AC4` | Installed end to end | `make web-release-check` | Separate fabricated embedder project. | No private repository dependency. |
| US-069-AC5 | Given pinned cohort, when clean rebuild occurs, then advertised wire/asset manifests match artifacts. | `installed::cohort::reproducible-archives`; each distribution’s `sdist-rebuilt-wheel` observed case; `scripts/web/check-packages.mjs::reproducible-archives` | Reproducible build emits matching protocol and asset manifests for the pinned cohort. | `@covers US-069-AC5` | Package/release | `make web-release-check` | Locked Python/npm toolchains; clean build directory. | Record tool/runtime versions. |
| US-069-AC6 | Given missing asset or protocol mismatch, when app starts/connects, then explicit failure is returned. | `packages/web-ui/tests/test_static_host.py::test_missing_or_corrupt_assets_fail_readiness`; `tests/client/test_remote_http_server.py::test_incompatible_api_major_fails_without_fallback_or_dispatch` | Readiness or connection reports typed incompatibility and never falls back silently. | `@covers US-069-AC6` | Integration | `make web-package-check`; `make web-client-contract` | Corrupted asset manifest and incompatible version fixture. | Require both observed cases in current source-bound receipts; unsupported ASGI scope alone does not prove API-major refusal. |
| US-069-AC7 | Given optional dependency change, when release gate runs, then compatibility ranges and core exclusions are checked. | `installed::base::closure`; `installed::carrier::closure`; `installed::old-core::candidate-api-refused`; `installed::published-core-0.2.0::candidate-api-refused` | Gate detects out-of-profile versions and optional dependencies leaking into core. | `@covers US-069-AC7` | Package/release | `make web-release-check` | Deliberately modified fixture manifests. | Report named compatibility matrix. |
| US-069-AC8 | Given release packet, when support claims are reviewed, then each claim points to executed evidence or stays unqualified. | `scripts/web/check-packages.mjs::typed-react-slots` (actual guide snippet compile), plus independent support-claim review against the final receipts | Packet names exact profile evidence and marks any unsupported environment unqualified. | `@covers US-069-AC8` | Package/release | `make web-release-check` | Evidence packet with local and live profile records. | Local pass cannot qualify Apps. |

## Executable Proof

The listed core, API, browser and artifact checks now exist. Focused execution receipts are recorded in IP-002 continuation evidence; the final current-cohort aggregate and selected live profile remain required. No row in this matrix grants completion from a test name, citation, successful collection or local substitute for a live case.

### Primary Commands

```bash
make web-client-contract
make web-api-contract
make web-ui-check
make web-package-check
make web-platform-check WEB_PROFILE=<explicit-profile> WEB_APP_ORIGIN=<https-origin> WEB_LIVE_RECEIPT=<current-receipt>
make web-release-check
```

### Executable Test Sources

- `tests/test_public_port_conformance.py`, `tests/test_public_host_ownership.py`, `tests/test_remote_terminal.py`.
- `tests/client/test_remote_client.py`, `tests/client/test_remote_http_server.py`, `packages/web-api/tests/`.
- `packages/client-ts/test/`, `packages/react/test/`, `packages/ui/test/` (Vitest and Testing Library).
- `packages/web-ui/tests/` (static ASGI host), `tests/ui/reference.spec.ts` (real Chromium).
- `packages/databricks/tests/`, `examples/databricks-app/tests/`, `tests/ui/databricks-live.spec.ts`, `scripts/qualify_live_apps.py`.
- `scripts/qualify_selected_profiles.py` and `scripts/selected_profiles_*.py` implement the other seven selected-profile targets; declared case IDs do not establish observed success.
- `scripts/web_platform_receipt.py` verifies the selected live receipt and all ten profile-bound carrier acceptance rows; `scripts/qualify_web_platform.py` is a supplementary diagnostic, not blanket acceptance proof.
- `tests/packaging/`, `scripts/check_web_packages.py`, `scripts/web/check-packages.mjs`, `examples/embed/tests/`.

### Timing-remediation cases

These cases implement TP-001/TP-002's timing allocation without changing the 41
criterion IDs or their primary layers. Timing inventories classify each use;
the presence of `sleep`, `timeout` or a benchmark import alone is not a waiver
or a reason to delete useful functional assertions.

| Affected proof | Concrete implementation/test allocation | Required observable assertions |
|---|---|---|
| US-068-AC4/AC8 managed recovery | `scripts/selected_profiles_managed_process.py`, `scripts/selected_profiles_managed_process_peer.py`, `tests/test_selected_profiles_managed_process.py` | Both peers resource-ready before original lease activation, distinct held/unknown-state events, explicit failed event/EOF, delayed valid event accepted, contender observes initial in_progress before actual lease takeover, inconclusive when that precondition is unobserved, stale writes/heartbeat refused, committed projections unchanged. No first-peer20 or elapsed>=29 assertion. |
| US-065-AC5 / US-068-AC8 physical ownership | Existing worker/managed-process lifecycle tests and selected-profile process helpers | Barrier-held worker; leader exits while exact descendant retains stream; cancellation repeated during cleanup; no source release before group absence, physical completion and EOF. |
| US-065/067/069 installed terminal/browser journeys | `scripts/check_terminal_pty.py`, `tests/ui/`, package-installed journey drivers | Usable terminal output, exact reply/action and exit; browser visible state, focus, keyboard and accessibility assertions. Remove startup10/response1 speed gates and fixed progress delays. Retain necessary emergency cleanup separately. |
| US-068-AC2/AC6 deadline and expiry | `tests/test_selected_profile_identity_revocation_convergence.py`, selected identity/expiry scripts | Controlled-clock single-budget propagation for deterministic tests; exact principal/state and fresh Me before protected refusal while captured token remains unexpired; genuine live OAuth/database expiry with expired/fresh controls. Watchdog exhaustion is incomplete. |
| NFR-7 measurement logic / functional inventory | `tests/test_client_benchmark.py`, `tests/test_web_benchmark.py`, `tests/test_benchmark_store_profiles.py`, `tests/test_benchmark_reconnect.py`, `tests/test_benchmark_follower_memory.py`, related benchmark/ratchet tests | Keep fixture correctness, cursor replay, retention/reset, schema/source validation, rejection and cleanup controls using synthetic measurements or untimed functional helpers. Select real measurement workloads only through the explicit isolated performance path; inventory moved nodes without silently skipping required coverage. |

Owner decision on 2026-11-02 allocates NFR-7 performance closure to six local
controlled profiles and exactly 54 metric nodes: store 7, memory-store 7,
client 17, volatile-client 11, engine 2 and follower-memory 10. The two Apps
latencies remain optional observations with provenance and explicit unknown
managed hardware/co-tenancy; they cannot emit qualified performance nodes or
satisfy missing controlled proof. Default controlled qualification requires no
precollected Apps observation. Historical seven-profile/56-node receipts and
source28 failures are retained unchanged. This does not change any of the 41
functional AC rows or the 45 IP-002 tasks.

NFR-7 admission controls fabricate reservation, host/runtime binding, lock
contention, observer unavailability and interference before/during/after a run.
Require fail-closed deferral or invalidation, retained exact child cleanup and no
automatic measurement/retry. A held benchmark lock alone cannot establish that
unrelated jobs are excluded. TP-002 owns the selected reservation mechanism.

For all other Python, Vitest and Playwright timing uses, retain a disposition
with the owning behavior, selected clock/barrier and any justified emergency
watchdog. A test that still requires a duration must identify the actual
deadline/expiry/performance requirement it proves. Run affected focused controls
before a fresh complete source29 aggregate and installed/browser qualification.
Source28 receipts remain evidence for source28; they cannot qualify changed
tests, helpers or artifacts.

Phase D also covers `scripts/selected_profiles_logs.py` and its cleanup/privacy
controls (US-068-AC10), `qualify_live_apps*` plus live browser restart/gates
(US-068-AC1/AC8), identity-convergence fake-clock controls (US-068-AC2/AC6),
ordinary worker event-wait families, installed PTY and React/browser/a11y
drivers. Prove delayed follower/UI readiness through barriers, fresh required
log anchors before capture stop, explicit EOF/failure, capture overflow and
retained cancellation. No fixed 15-second follower-ready limit or post-request
sleep may establish privacy validity. Keep exact identity/state/unexpired-token
checks and the single shared budget; test exhaustion as incomplete. Preserve
real expiry and intrinsic shutdown deadline assertions. New warmup recipes use
the corrected harness without inheriting source28's readiness-speed assumptions.

### Coverage Focus

- P0: Every AC row above, with its exact `@covers` citation, passing before its story is complete.
- P0: Same-process deployed-listener H2C exchange plus independently observed public Apps proxy/SSE protocols, identity, OAuth/database and bounded shutdown evidence before claiming that named profile. HTTP/1.1 on the proxy hop is recorded independently from actual listener H2C support; generic local evidence cannot substitute.
- P1: Additional server, prefix, database and auth profiles only after explicit compatibility scope is added.

## Data and Setup

| Need | Required For | Source / Strategy |
|---|---|---|
| Fabricated actors, sessions, messages, approvals and tools | US-065–068 | Deterministic fixtures; no customer content. |
| Barrier-controlled operations, clocks and policy resolver | US-065/066/068 | Injected test ports; avoid `time.sleep`. |
| Browser account and hostile text fixture | US-067 | Playwright/Testing Library fabricated session. |
| Clean Python and npm environments with built artifacts | US-065/067/069 | Build wheel and npm tarballs; fixture outside repository source tree. |
| Databricks workspace, app, user scopes and external database role | US-068 | Owner-provided disposable profile; capture versions and permission scope, not secrets. |
| Log marker and credential-shaped values | US-068-AC10 | Generate during test from runtime pieces; assert absence from captured output. |

## Edge Cases and Failure Modes

- Canceled request while accepted physical work continues; shutdown while worker remains active.
- Lost accepted-send response, retry, feed replay, retention gap and policy revocation between batches.
- Conflicting identity headers, missing local resolver, token expiry and app/user principal confusion.
- Mismatched migration, inadequate database role, OAuth refresh failure and forced app termination.
- Missing static asset, incompatible protocol version, JS integer precision boundary and hostile rendered content.
- Package registry spelling remains unverified; ADR-022 names are proposed until registry availability is checked.

## Build Handoff

**Implementation Order**
1. Build the public client and shared conformance suite; satisfy US-065 and the client portion of US-066.
2. Build HTTP routes/feed with per-batch current authorization, then browser client and reference UI; satisfy US-066/067 with browser evidence.
3. Package artifacts and independent embedder; run the deterministic locked installation gates for US-069.
4. Compose Databricks helpers/example, then run explicitly configured live identity/storage/lifecycle profiles for US-068.
5. Publish only compatibility claims backed by the exact runtime/package/profile evidence packet.

**Constraints**
- Public app boundaries use `tether.client`, `tether_web_api`, `tether_web_ui` and `tether_databricks`; carrier composition uses public factories.
- The user actor owns Tether access; app identity is limited to explicitly authorized infrastructure operations.
- Treat Apps storage as ephemeral; use an explicitly migrated and role-isolated external store for durable recovery.
- A per-test `@covers US-<n>-AC<m>` citation is required in addition to assertion and passing evidence.

**Done When**
- [ ] All 41 mapped criteria have implemented, passing tests with exact `@covers` citations.
- [ ] Named commands exist and run in clean environments; reports are linked from execution evidence.
- [ ] All affected browser journeys satisfy the UX map below.
- [ ] Live Apps/storage claims identify the exact deployed profile and executed evidence; other profiles remain unqualified.

## UX Coverage

| Pattern | Disposition | AC IDs or Reason |
|---|---|---|
| navigation | covered | US-067-AC7 |
| keyboard-overlays | covered | US-067-AC7 |
| async-actions | covered | US-067-AC2 |
| forms | covered | US-066-AC2 |
| data-states | covered | US-066-AC6 |
| destructive-actions | covered | US-066-AC7 |
