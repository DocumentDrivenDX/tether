# IP-002 execution continuation

Execution is active under the user's explicit goal to complete IP-002. The sole
tracker remains [IP-002](../web-platform-implementation-plan.md). Task boxes close
only against current implementation evidence. Planning validation is not runtime
qualification. No release completion is claimed.

## Current ownership

- Root: npm packages, React hooks/components, reference browser app, static Python
  UI distribution, browser evidence, live development qualification and integration.
- Sol coordinator: quality infrastructure, package boundaries, root Python workspace,
  CLI/TUI migration and plan task reconciliation.
- Luna interfaces: public client composition foundation (implemented), optional
  PostgreSQL construction, carrier distribution and reusable Apps composition.
- Luna carrier: API and optional Python remote client (implemented); browser
  transport validation/conformance and CSP-safe generated validators in progress.

## Evidence and decisions

The baseline receipt under `web-platform/` records the full baseline failure after
73/80 successful bounded groups. Concurrent coverage writers corrupted the shared
SQLite context table. Sol implemented per-run data isolation and focused concurrency
regressions. An unfiltered final integrated gate remains required; WP-00 is open.

The public client host, worker ownership, turn-result polling and targeted cancellation
have focused Python evidence. API and remote-focused tests passed 12 cases; the
API contract gate passed nine cases and schema freshness. Their installed consumer
matrix and complete acceptance still require integration. One broader TUI gate
reported Textual `NoMatches('#sessions')`; Sol owns its resolution. One native PG
fixture needed escalation because its shared cache lock lies outside the sandbox;
it is not a product failure or an ignored test.

The Python static UI host has five passing focused tests for nested prefixes,
manifest/digest refusal, safe API fallback, bounded assets and symlink refusal.
The npm package graph and browser source currently typecheck. Component/browser
acceptance and accessibility are not yet qualified. Runtime schema validation is
being generated ahead of time to satisfy the static host's restrictive CSP.

Exact Python HTTP definitions are the schema authority. Generated JavaScript types,
validators and exact integer conversion derive from those definitions. Arbitrary
user JSON stays opaque. The browser client has no React dependency; React hooks
and UI consume the injected public client only. All builds and generated asset
manifests must be rerun after input changes.

## Live development qualification

The user authorized a temporary Apps deployment and dedicated temporary Lakebase
project in the selected development workspace, including resource creation. The
selected CLI profile, credentials, endpoint names and resource inventories are
kept outside this public repository. Existing applications and databases are not
used or modified. A mistaken unused PostgreSQL 17 project was soft-deleted; its
cleanup receipt is private. The replacement temporary project explicitly uses
PostgreSQL 18 and its endpoint is active. This is resource readiness only, not
SQL durability, isolation, ingress, reconnect or termination qualification.

Deployment uses fabricated demonstration model responses with real storage and
caller identity, never production user content. The reusable carrier remains
SDK-free; the example owns environment/resource acquisition and token exchange.
Per-user database roles must preserve the authenticated identity; no shared-user
role fallback is permitted. Migration/provisioning is explicit and separate from
runtime construction. Live profile and process gates remain open.

## Exact next work

Finish browser transport and component tests; build static assets; run real-browser
journeys against the public API and capture fabricated media. Complete the carrier
example and SQL provisioning, then deploy only the authorized temporary development
app. Reconcile current evidence with IP-002 and run every selected final gate,
including independent installed artifacts, clean dependency closures, live Apps
and Lakebase proof, documentation and final review. Preserve failures and cleanup
receipts. Do not mark the goal complete with an unsatisfied required gate.

## Subsequent integration checkpoint

Node 22.20.0 was installed as an isolated npm tool runtime. Its full npm unit suite
passed 23 cases, and the clean npm artifact consumer check passed three byte-identical
repacked archives, framework-free closure, typed layout/message slots and server-side
text escaping. Production JavaScript is about 130 KiB gzip, under the proposed
250 KiB budget. Generated validators run without eval and now normalize the Ajv
CommonJS Unicode-length helper correctly in both Node and Vite. A real Chromium
reload caught that interoperability defect before closure.

The first real Chromium journey passes: prefixed assets/config/API, session creation,
exact keyboard tool approval, committed history after reload, literal hostile markup,
other-owner 404 and zero axe accessibility violations. It also caught duplicate React
sibling keys and insufficient muted-text contrast; both were fixed and rerun.
Further cancellation and real crash/resume journeys are still in flight, so the
media capture recipe must not yet write a complete passing receipt. Root owns
`python -m docs.capture.capture_web`, including its optional installed interpreter.
Sol will run that browser fixture against the independently installed combined wheel
profile before an installed-media claim. The new static-host focused suite passes
nine tests; its isolated branch-included coverage is 97%, with no floor change.

Sol removed all 58 terminal private-import migration debts and verified zero remain.
The local public-facade/reference regression passed 32 cases. A remote launcher
now accepts explicit endpoint, API prefix and CLI profile, owns its remote transport,
and borrows that client into the terminal UI. A real host/API/remote/Textual exact
approval journey passed. Its installed closure remains part of the final matrix.

Immediate cancellation in the real browser exposed a JSONL writer cancellation
race. A 20-iteration public-host probe reproduced `store_requires_reopen` at iteration
five when cancellation interrupted an engine-owned authorization commit. Sol owns
a deterministic barrier regression and cancellation deferral through known persistence
completion. The storage uncertainty guard remains intact. Explicit user cancellation
settles idle/non-resumable, with a persisted cancelled turn result; browser resume
must instead exercise an actual crashed process and expired lease.

The temporary Apps deployment is RUNNING on ACTIVE compute. Unauthenticated ingress
returns 401, and an authenticated CLI token reaches the OpenAPI endpoint. Lakebase
migration and dedicated-schema forced owner RLS provisioning are explicit. The app
service principal is not a BYPASSRLS role. Local owner queries have elevated database
authority and cannot prove row isolation. The initial protected request exposed the
real header mapping difference: `X-Forwarded-User` is an IdP identifier while
`X-Forwarded-Email` supplies the Lakebase user role. The resolver now requires both
unique trusted headers, uses email for the logical owner and preserves the user
identifier as principal. Redeployment and protected live proof are in progress.
The verified TLS CA bundle stays in the private qualification bundle.

The root now includes a draft [RUN-002](../../05-deploy/web-platform-runbook.md),
validated with the bound HELIX runbook validator (zero findings). Independent review,
actual live recipes and release closure remain required. API/embedding/React/remote
public guides and a public-import-only `examples/embed` are implemented by Luna.
The example's isolated install and all guide provenance/review still need integration.
Sol owns a machine-observed JUnit/evidence closure audit; mock or prose case names
cannot replace passed required test IDs. Python artifact builds passed individually;
a reproducibility run intentionally failed while concurrent source edits changed
its input. Final source freeze and complete rerun remain required.

## Browser and qualification checkpoint

All three source-browser journeys passed together before final visual review:
exact approval/reload, targeted cancellation/close, and resume after killing and
reopening the fixture process following its actual lease expiry. Desktop/mobile
axe and overflow checks passed. Independent image review then caught duplicate
presentation of the same tool-result HistoryRef and an empty approval-note user
card. Luna owns the fix and stronger exact transcript assertions; earlier images
must be regenerated before final media receipt/installed claims. Root added the
mobile image to the capture receipt recipe and a clearly unreleased microsite
preview. Those documents do not claim published browser packages.

The API now exposes a framework-neutral ASGI factory protocol and enforces bounded
request bodies and exact explicit browser origins. The API contract gate passes
23 cases plus canonical schema generation, Ruff and strict typing. The independent
embedding example composes replaceable API and UI factories using public imports.

Live elevated qualification passed deployment, authenticated ingress, the named
managed PostgreSQL 18 checks, and human-client create/send/result/read/close.
Sandbox profile checks incorrectly appeared unauthenticated; elevated credential
refresh succeeds. A stop command exceeded its initial 30-second CLI deadline but
the control-plane stop did complete. Recovery start completed and returned the
app to RUNNING. Luna is making restart polling operation-aware and rerunning;
backend ASGI HTTP/2, retained-result/shutdown proof and the second nonadmin caller
remain unqualified. The user explicitly authorized creating the second identity.
No shared infrastructure-role or invented-human fallback is accepted.

RUN-002 received an independent content review with no blocking wording finding;
that review did not establish live CLI validity. A release-candidate disposition
packet keeps publication on hold pending required gates. Full Python quality,
installed profiles and current machine acceptance closure remain open. Root's
full native reference benchmark was paused during fixture construction before
sampling to avoid concurrent quality load. The smaller workload's failed proposed
append target remains recorded; no reference-workload or performance-budget pass
is claimed.

## Acceptance review and candidate cohort checkpoint

The Python candidate cohort is explicitly `0.2.0.dev0`, with dependency floors
excluding the published 0.1.0 core; npm packages and their internal exact
references are `0.2.0-dev.0`. A clean resolver probe installed the hash-pinned
published core and rejected the candidate API conflict. A tag alone no longer
sets package versions. Clean npm verification passes three reproducible archives,
exact root license/NOTICE contents, framework-free import, typed visual slots and
text escaping. Its sanitized report includes an actual installed-consumer
CycloneDX dependency SBOM with local artifact paths removed.

Semantic review strengthened exact large-counter reconnect and multi-event commit
proofs, keyboard approval and decline, HTTP lost-acceptance retry, API recreation
with durable result lookup, and stale cancellation while a successor turn remains
active. Public alternate-port conformance exercises the complete client surface.
The capability response now truthfully refuses volatile resume. Public host tests
hold a real synchronous worker beyond bounded close and preserve root ownership;
`wait_closed()` offers a cancellation-isolated way to await initiated cleanup.
These focused passes are not the full final aggregate.

A further archive-capable UI review found that selection discarded its tier, so
archive snapshot/history reads and restore were unreachable. Luna owns typed tier
propagation, read-only archive presentation, restore-to-live behavior and an actual
archive-capable consumer test before the next source freeze. Default unsupported
archive profiles had not exposed this defect.

The second nonadmin identity now has only the minimal workspace consume entitlement
needed for Apps access, in addition to the qualification app permission. Its current
M2M credential is verified by the workspace API and reaches the carrier. The default
human-header resolver correctly refuses it. An explicit opt-in service-caller
extension verifies the actual current principal and separately maps its OAuth
client ID to a dedicated database role. The human path retains its refusal rules;
this is not evidence of two human users. Role provisioning remains administrative,
separate from app runtime. Live isolation, backend protocol and shutdown/recovery
receipts still require completion.

Both performance tools now bind observations to full before/after source inventories.
The old paused reference seed was interrupted and cleaned without taking samples
because it lacked that complete binding. No complete reference performance pass is
claimed. The full workloads must run after source freeze and aggregate load, then
record actual baseline decisions and regression ratchets.

## Tooling and physical shutdown checkpoint

The installed HELIX plugin changed from 0.19.1 to 0.20.0 during execution.
Historical planning receipts retain their original catalog version; final
validation binds the installed 0.20.0 catalog. RUN-002 validates against that
catalog with zero findings. This tooling change does not replace unfinished
runtime evidence.

Carrier shutdown review found that resource closers returning `False` were
treated as complete, allowing dependency cleanup before physical workers exited.
The ordered lifecycle now stops on incomplete or failed closure, retains every
unclosed dependency, isolates caller cancellation and retries remaining resources
on a subsequent explicit close. Three deterministic ownership tests pass. The
example is being integrated with public host and PostgreSQL `wait_closed()`
completion seams before another live deployment.

An independent installed React consumer successfully rendered its injected
provider and custom layout/message components, then unmounted and closed its DOM.
The child still exceeded its deadline: descendant query cleanup scheduled
five-minute cache timers after provider cache clearing. This exposed a provider
lifetime defect; the package check remains failed until cleanup naturally exits.
No forced process exit or timer suppression qualifies that consumer.

## Supported runtime and consumer exit checkpoint

The actual locked JSDOM 30.1.3 metadata requires Node 22.22.2 or another
listed supported branch. Node 22.20.0 was therefore an unsupported development
combination, and its focused receipts remain diagnostic. The final selected
cohort is Node 22.22.2 with npm 11.20.0. The downloaded runtime reports that
exact version; CI and runtime guards now use it. Workspace engine metadata and
the npm lock have been refreshed to respect the harness dependency's floor.

The corrected provider retains its cache through StrictMode effect replay and
clears detached identity caches after descendant unsubscription. Eighteen
focused React/workspace cases pass, including archive restore success and
refusal. An actual compatible-runtime diagnostic took 26.175 seconds to import
JSDOM and reached unmount at 28.941 seconds, then exited naturally. The consumer
now separates a bounded 90-second cold process load from a five-second
post-unmount watchdog. The watchdog does not keep Node alive or alter product
timers. Under Node 22.22.2 the normal workspace consumer exits zero; a deliberately
planted five-minute handle fails with `consumer_resources_remain_after_unmount`.
Final clean installed artifact qualification remains required.

The actual three React guide snippets compile with strict types, exact optional
properties and unchecked-index checking. This review corrected the example's
headers return type, persisted archive routing, restore callback description,
custom layout skip target and failed-command presentation. The clean npm
consumer now compiles those actual snippets against installed artifacts rather
than a parallel handwritten example alone.

The temporary second caller's current credential verifies through the workspace
API and TLS-verified managed PostgreSQL access. The platform explicitly refused
database credential issuance with consume-only access, so the caller received
the required workspace-access entitlement. It remains nonadmin with app-specific
permission and its separate database role. Exact identity and resource values
remain private; app-side owner isolation and shutdown qualification are still
open.

## Current caller and live fault qualification checkpoint

The temporary nonadmin caller's refreshed credential verifies through both the
explicit workspace SDK client and the CLI's actual current-user operation. The
CLI check uses a clean, explicitly selected OAuth M2M environment; its profile
inventory validity flag was a false negative and is not used as proof of refusal.
The credential remains in the operating system keychain, with private lifecycle
metadata outside the repository. A scan of 740 tracked and nonignored candidate
files found none of the actual temporary app or caller identifiers.

The protected live request still refuses authentication. A fixed diagnostic enum
identifies the forwarded access-token header as the conflict; no header values
were emitted. This is diagnostic evidence, not a live acceptance pass. The
selected unsafe-effect shutdown scenario must also retain actual physical work
past the configured deadline and observe its outstanding report, followed by
the durable unknown-effect barrier after process replacement. Cooperative model
cancellation alone cannot establish that branch of US-068-AC8.

Shutdown review found two additional deadline defects. Equal nested lifecycle
budgets let the outer watchdog win before the named-resource report returned;
the carrier now reserves a bounded reporting interval. Hypercorn drains live
connections before ASGI shutdown, so assigning the full budget to both phases
could double the signal allowance. The example now partitions one configured
total across connection drain, carrier cleanup and reporting, records the first
actual signal with a monotonic clock, and collects runtime versions before that
deadline begins.

The actual child-process Hypercorn regression holds a live SSE response and a
physical executor worker, sends OS SIGTERM, and observes an incomplete report
inside its configured one-second total. The report names `client_host` and
`postgres_resources`; neither dependency is released, and the process remains
alive until the test kills its owned child. This test and the three ordered
lifecycle cases pass (four cases, 6.44 seconds); targeted Ruff, formatting and
Pyright pass. Independent Sol review found no blocking issue. This is local
mechanism evidence, not the selected Apps shutdown qualification.

## Integrated gate and live qualification continuation

The ninth integrated gate failed after its Python suite passed 2,271 cases in
101 bounded groups with 87% combined coverage. JavaScript and npm checks passed;
the browser gate passed two of three journeys. The failed process-recovery
journey displayed committed and provisional copies of the same reply. The
product and assertion corrections have focused evidence; the full gate must be
rerun before these results can support aggregate acceptance.

The tenth integrated run uses a fresh retained installation directory and the
selected Node 22.22.2 runtime. Its outcome remains pending. The current source
inventory includes the live-runner diagnostics and corrected collector controls.
The collectors retain physical identity deactivation before restoration through
cancellation, distinguish the administrator's database access from nonadmin row
policy evidence, and check a fresh database connection before stale credentials.
Actual selected-profile receipts remain required.

The live app's immutable deployment snapshot exported successfully for a bounded
format trial. This establishes snapshot availability, not completion of the
locked-artifact acceptance case. A subsequent token acquisition failed inside
the sandbox but succeeded with the same cleaned profile outside it; no user
re-authentication or credential rotation was necessary. A current scan of 777
tracked and nonignored files found no saved temporary app or caller identifiers.

The tenth run subsequently refused collection because a diagnostic test's
automatically generated node ID contained its oversized fixture. Explicit short
case IDs retain the same payloads and assertions within the unchanged child
argument limit. The eleventh run exposed a separate timing assumption: a
50-millisecond heartbeat could consume the test authenticator's revocation
threshold before the second committed-change variant. Current authorization
correctly refused that delivery. The committed-delivery test now separates its
heartbeat interval from its existing three-second observation deadline, and a
new real-TCP test independently observes one authorized idle heartbeat followed
by revocation and a refused reconnect. Both focused cases pass; integrated
acceptance remains pending a fresh run.

The latest live approval timeout was traced to deployment configuration rather
than established UI behavior. The active snapshot lacked both the opt-in
shared-session module and scenario setting. Its basic model accepted the prompt
and returned a final result without pending approval or effects. The next live
attempt requires a newly built current-source bundle, an explicit shared-session
setting and verification of the immutable deployed bytes before the browser
journey. That diagnostic made no stop/restart claim.

The corrected deployment subsequently passed the live shared browser/TUI and
competing-approval cases. Its restart case accepted and started the held effect,
but the log follower ended before collecting a shutdown report. A private
600-second follow control also ended after 80.768 seconds, with no report marker
or parse rejection. Thus the original 180-second observer limit does not explain
the failure. The app recovered to RUNNING/ACTIVE with a protected HTTP 200 route;
AC8 remains unqualified.

Azure Databricks documents that application logs are not persisted when compute
shuts down and recommends an external persistent destination. Missing post-stop
logs therefore do not establish whether the runtime omitted its report.
See [application log persistence](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/monitor#application-logs).
A private local control placed the existing held-worker/live-SSE shutdown test
behind `uv run --locked --no-sync`, sent SIGTERM to the uv parent, and passed the
same one-second report assertions. That control rules out a local uv forwarding
defect in the tested version; it does not qualify Apps shutdown or replace the
required platform-originated SIGTERM evidence.

## Full-cohort and semantic-review checkpoint

The twelfth deterministic aggregate passed on its captured source cohort:
2,292 Python cases in 101 bounded groups at 87% branch-inclusive coverage,
32 JavaScript cases, four clean npm artifact cases, all three source-browser
journeys and ten installed Python profiles. Its exact receipts are retained
with [the full12 aggregate receipt](web-platform/full12-aggregate.json).
Subsequent fixture handling and implementation changes make this historical
evidence; it is not qualification of the current source tree.

The thirteenth run passed the same 2,292 Python cases at 87%, all 32 JavaScript
cases and four npm artifact cases, then failed the third source-browser journey.
Its initial unscoped completion-text selector encountered both committed history
and provisional text before the snapshot became idle. The installed matrix was
not reached. The correction scopes the initial wait to committed Conversation
content and explicitly requires one committed completion and no provisional
article after idle. That corrected recovery journey passed in real Chromium
on Node 22.22.2; a new full-cohort pass remains required.

The literal acceptance review distinguished complete capability listing from
complete protocol forwarding. It identified missing successful history-link,
revoked-send nondispatch, embedded/remote typed TUI parity and optional
attempt/tracer/clock forwarding assertions. Those new public-host cases have
focused evidence. HTTP method parity, withheld-next-batch policy proof and
remote malformed-response/cursor/bounds corrections are being integrated as
one further batch. No capability-name assertion substitutes for route execution.

TUI recovery now closes its prior subscription before snapshot/history reads,
then reconnects at the captured owner watermark. Its fixed failure presentation
does not expose upstream exception text or repeat a command. The eight new
recovery/presentation cases and existing streaming/remote-terminal regressions
passed together, 24 cases in 7.67 seconds. The historical installed full12
offline PTY probe also passed: startup 2.421 seconds, first approval response
0.204 seconds and clean exit, with network access denied by the probe. This
does not replace the final candidate's installed PTY observation.

The selected missing-schema collector now exercises actual example composition
and ASGI startup with an authorized nonadmin caller, followed by refusal of an
isolated nonexistent schema, framework publication checks, cleanup and unchanged
role/schema metadata. This local-caller diagnostic does not impersonate the app
principal; deployed app-principal readiness remains a separate AC4 obligation.
All ten selected live criteria still require current-profile accepted receipts.

## Deployed protocol and absolute-shutdown checkpoint

A metadata-only deployed-runtime probe confirmed that the loaded carrier
composition bytes match the deployed wheel and the launcher bytes match the
candidate source. Protocol capture is present and recorded backend HTTP/1.1.
The previous private self-signal diagnostic's empty protocol list came from its
own filter excluding the string `1.1`; it was not evidence of missing runtime
capture or stale loaded code. The standard app was restored, its immutable
snapshot matched, and its protected route returned HTTP 200. H2C ingress
qualification remains open; successful HTTP/1.1 requests do not establish it.

The self-signal diagnostic measured a report at 14.1903 seconds against a
configured 14-second deadline, exposing a relative phase-budget overrun.
The launcher now supplies an absolute monotonic deadline before Hypercorn
connection draining, and the carrier only tightens that deadline. Late updates
after ASGI shutdown begins are refused. The normal, delayed-connection-cancel
and deadline-validation cases pass locally, three cases in 5.09 seconds, with
Ruff and strict typing clean. These are local mechanism checks, not acceptance
of platform-originated termination, managed recovery or US-068-AC8.

Apps stop still ended the log stream without an observed report. Neither the
longer follow limit nor the metadata probe resolved whether the final report
was emitted but lost by platform log delivery. Independent durable observation
and actual deployed H2C qualification remain under investigation. No live
criterion, performance gate, current full aggregate or publication gate is
marked complete by this checkpoint.

## Batch14 freeze and qualification-control boundary

The bounded semantic-review batch is implemented. Complete typed HTTP forwarding,
including supported/unsupported archive paths and opaque references, has focused
proof alongside constant-identity policy revocation before the next feed commit.
Remote parsing now has finite line/event/idle limits and validates committed
cursors before advancing. Public-host tests prove successful history linking,
revoked send without model/tool dispatch, matching local/remote typed TUI results
and explicit attempt/tracer/clock forwarding. These focused observations require
the next aggregate before current-cohort acceptance.

The corrected AC7 collector was also exercised against the selected managed
PostgreSQL profile: good example startup/shutdown, missing-schema refusal before
framework publication, acquired-resource cleanup and unchanged schema/role
metadata passed. This focused result does not close the seven-case collector or
the all-ten live acceptance gate.

The final carrier review includes startup-return/raise/refusal/cancellation
cleanup and a child that cancels during shutdown; 37 focused carrier cases passed
in 3.71 seconds, with Ruff and strict typing clean. The held qualification tool
now defaults to 300 seconds with a 600-second maximum and a finite tool timeout;
client and app shutdown allowances remain unchanged. Its duration is a control,
not proof of physical ownership or a platform timing claim.

Qualification now distinguishes a real H2C exchange against the exact deployed
listener from independently observed public proxy traffic. Both must bind to the
same process, fresh nonce and loaded module hashes. A temporary external observer
must declare its own source/probe/bundle and actual-stop correlation, retain the
original report plus launcher-main return within the configured sub-15-second
bound, and expose its committed row to a fresh post-stop reader. It does not claim
that outstanding workers stopped or that the deployment was uninstrumented.
The validator refuses contradictory protocols, stale sources, mismatched process
correlation, late or missing main return and undeclared observer metadata.
The focused transport/writer/platform/controller suite passed 165 cases in 5.75
seconds. Five affected HELIX artifacts validate with zero findings.

Full14 started on 2026-10-31 with Node 22.22.2, a fresh retained package workdir,
and a frozen 642-file source inventory. Preflight found 2,407 unique Python cases
in 105 bounded groups; all seven static stages and diff whitespace checks passed.
All 45 public-guide source fingerprints matched. Python AST checks across 78
changed/new test files and TypeScript AST checks across 11 found no complete
credential-shaped literals. The aggregate is pending; no current full pass,
live profile acceptance or performance qualification is claimed here.

Full14 was stopped with SIGINT (exit 130) after 1,477 cases in 55 completed
bounded groups. During the declared freeze, an example-project `uv run` refreshed
its untracked `uv.lock`; subsequent deletion also differed from the captured
source. Restoring bytes later cannot make that an immutable run. Its partial
passes are historical, and no complete aggregate receipt is accepted.

The required example lock was regenerated explicitly before the next freeze,
using the root lock's exact registry pins. All 48 resolved example packages have
matching versions wherever they overlap the root cohort; the four candidate
projects use explicit editable sources at `0.2.0.dev0`. Offline `uv lock --check`
and `uv sync --locked --dry-run` pass. The corrected nested lock SHA-256 is
`e64474450bb747f0d9c4cd5f3bdfb067236aabbd0915c2dfeec91cb8f43b0ec5`.
Private harnesses use an already-installed interpreter during source freeze;
no checkout-project `uv run`, lock refresh or cleanup is allowed then.

Full15 started against the corrected 642-file inventory, canonical map SHA-256
`54d33aff42ba4fa6259284a862b6d3b5bd92e2062195e7787dc0cb1330e2569c`.
The unchanged 2,407-case/105-group inventory, seven static checks, whitespace
check and 45 guide fingerprints passed preflight again. The fresh retained
package directory is separate from earlier runs. Full15 is pending; selected
live profile, performance, media and final closure remain open.

The generic observer harness and fabricated controls are retained under
[observer evidence](web-platform/observer/README.md), with a raw-byte manifest.
The RUN-002 copy recipe was independently executed: all nine hashes matched,
eight files were reconstructed in the expected layout, seven Python sources
compiled, and the ten actual reconstructed unittest controls passed in 1.460
seconds. This verifies reproducible harness controls only. No app stop, database
observation, H2C platform result or live acceptance is inferred from that run.

## Full15 ownership failure and bounded correction

Full15 stopped with 1,708 observed Python passes and one failure in `test_borrowed_view_close_preserves_other_client_and_accepted_work`; [the sanitized failed Python historical summary](web-platform/full15-python.json) records stable source inputs; its original exact receipt is retained privately. JavaScript, browser and installed-package stages were not reached. This is a failed aggregate, not a partial release qualification.

A private reproduction preserved the original deadlines and showed the blocked read entered at about 0.19 seconds, the held model reached its own five-second request timeout, and the UI context returned only at its outer twenty-second deadline. The read consumed the follower cancellation and returned a completed page; the follower then awaited another event instead of stopping. The proposed correction uses explicit owned stop state after awaited presentation work. Independent review also identified an initial read-to-subscription gap; capturing the atomic snapshot watermark before presentation and replaying from that cursor is part of the same bounded correction. Focused regression and a fresh full aggregate are required before closure; neither correction is yet qualified here.

## Batch16 frozen correction checkpoint

The TUI correction has 19 passing focused cases, including the unchanged borrowed-work ownership test. Deterministic controls prove no additional receive or presentation callback after a read consumes stop cancellation, including a close error, and prove replay from a snapshot taken before initial history presentation. The TypeScript client fixture assembly retains identical runtime values and its seven focused cases pass on Node 22.22.2.

All static checks pass, 45 public-guide source fingerprints match, and the credential-shape guard reports zero findings across 78 changed/new Python test ASTs and 43 changed/new TypeScript/JavaScript module ASTs. The frozen inventory contains 642 files; its canonical map SHA-256 is `f42eaba429a9c9013c76718d0ca982e44c27320f8e046ae0b945c0f49a850efe`. Collection contains 2,410 unique cases in 105 bounded groups, retaining the existing case, argument-size, native-group and child-deadline limits. The full aggregate remains pending while the fresh live candidate is diagnosed; these focused and static outcomes do not close release acceptance.

The expanded generic observer archive has ten hash-verified manifest entries. Reconstructing its exact RUN-002 layout and executing the copied controls passed 14 tests; RUN-002 validates with zero HELIX findings. This is source/control evidence only. No selected-profile acceptance receipt is inferred from it.

## Instrumented diagnostic row without final stop binding

The failed live28 invocation reached accepted unknown-effect work and its worker-start barrier. Root independently matched the exact invocation, private observer row, loaded module hashes and deployed wheel bytes. That row records SIGTERM-to-report 12.951907 seconds, actual launcher-main return 12.952065 seconds, and configured budget 14 seconds, with the original report incomplete and client ownership outstanding. Same-process ingress and listener observations contain HTTP versions 1.1 and 2. Earlier conflicting timing summaries were not this invocation and do not justify a deadline change.

The generic runner failed to retain the required platform-stop correlation, so this diagnostic row cannot certify US-068-AC8 or mint a live acceptance receipt. Preserve both facts: actual bounded report/main-return evidence exists, and complete selected-profile qualification remains failed/pending. Improved private failure diagnostics and a fresh controlled invocation are required; no pre-unknown failure or successful stop binding is inferred.

## Public historical evidence and parameter identifiers

The publication guard also examines collected node IDs, not only source literals. Two invalid-URL parametrizations constructed fabricated userinfo at runtime but automatically embedded it in their IDs. Explicit concise IDs now preserve every test value and assertion without writing that shape into public evidence. Both parametrized test sets pass all nine cases. The frozen16 inventory remains historical and unchanged; a fresh17 inventory will govern the final cohort.

The historical full12 and full15 Python originals are retained privately byte-for-byte with SHA-256 digests. Their public files are now explicitly sanitized historical summaries with counts, source-inventory digests and the original-byte hashes. They omit exact per-node evidence rather than renaming an ID and presenting it as executed. Historical aggregate hashes identify those private originals, not the summaries. Neither summary can satisfy a current acceptance gate. The final current receipt must contain the exact clean observed IDs.

## Batch17 publication-safe freeze

The fresh inventory contains 642 files with canonical map SHA-256 `19ffe590549bacaff77bfc24260b5922c81c5ee1c9e6405ae78e394ab1ec007c`. Collection retains all 2,410 cases in 105 groups. Every collected ID passes the publication guard; 79 changed/new Python test ASTs and 43 current TypeScript/JavaScript module ASTs have no credential-shaped literal findings. Both corrected parametrizations pass nine focused cases; all static checks and all 45 guide fingerprints pass.

Source17 supersedes the unexecuted source16 freeze for final qualification. The full deterministic aggregate and installed profile matrix remain pending until the fresh live scenario is classified and passes on unchanged source. Candidate artifact construction and private diagnostic rows do not substitute for those gates.

## Batch18 readiness and evidence publication freeze

The live30 instrumented run reached the held unknown-effect barrier, observed the
actual platform stop, and passed the source/process-correlated durable observer
reader. Its original report and launcher-main return were inside the configured
14-second bound. The complete live run nevertheless failed when the first
post-restart capabilities read returned HTTP 502; a subsequent protected read
returned 200. This is a failed qualification with bounded shutdown evidence,
not a completed selected-profile acceptance result.

The qualifier now retries only post-restart capabilities readiness failures with
HTTP 502 or 503 under one absolute 30-second operation deadline. Each failed owned
transport closes before another attempt. Initial connection remains fail-fast;
commands, authorization denials, transport failures and decode failures are not
retried. Sixty-six focused cases pass, including permanent-status refusal,
transport/decode failure, cancellation cleanup and a never-returning open bounded
by that same deadline.

The public test runner and collection plugin now share one fixed-error validator
for credential-shaped test identifiers. Validation occurs before terminal node
printing or evidence inventory writes. Real isolated pytest controls prove that
unsafe runtime-generated values reach neither output nor destination, while
explicit safe identifiers retain their exact spelling and order. Additional
invalid-URL fixtures use concise explicit IDs and runtime value assembly without
changing their assertions or the existing collection, child or coverage limits.

Source18 contains 644 files with canonical inventory-map SHA-256
`9ce69cc23187c209bfc0a57d053e290ac0f82a67edccf893c259f56dfef7c943`.
Collection contains 2,432 unique cases in 107 bounded groups. All static checks,
45 guide fingerprints and the shared identifier/source-literal guards pass.
Current candidate builds have byte-identical standard payloads, including all
four wheels, to the existing private deployment candidate. These construction
checks do not replace the installed matrix. The full18 deterministic aggregate
remains unstarted until both live browser cases pass on this unchanged source;
selected-profile expiry, performance, media and final closure remain pending.

The earlier full12 private Python receipt records actual passes for all three
independent OTLP/HTTP receiver examples: public API attempt correlation and
content exclusion, sampling/queue loss with retained shutdown ownership, and
receiver failure accounting. Those example files remain byte-identical, but the
whole source cohort has changed. The public historical summary is not exact
node-pass proof, and full18 must observe the three current cases before WP-31
closes. No cloud collector support follows from this local receiver profile.

The fresh live31 run also failed after platform stop, strict observer verification
and successful restart readiness. Its session read succeeded, but the recovery
predicate did not: the accepted turn was terminal `cancelled`, and the public
effects read returned no matching unresolved invocation. This is an observed
failure requiring durable-fact investigation; neither a confirmed storage bug
nor a harmless fixture mismatch is inferred yet. The configured shutdown timing
and listener/proxy observations remain separate successful diagnostic facts.
Source18's freeze is released for the reviewed correction, and full18 was never
started. The private closure draft keeps every task and acceptance row pending.

The initial zero-effect summary above was a diagnostic iteration error: the
reader iterated `EffectPage` rather than its `items`. The corrected authoritative
query finds exactly one matching `outcome_unknown` effect, a committed started
intent, no result or resolution, then `turn_ended(cancelled)`. The worker marker
matches that invocation; SIGTERM followed about 2.62 seconds later, within the
300-second held-body control. The unknown-effect barrier was preserved.

US-068-AC8 and CONTRACT-007 require truthful committed facts and that barrier;
they do not require an interrupted terminal after orderly application shutdown.
ADR-010 distinguishes application cancellation from process/lease interruption.
The narrow correction therefore belongs in the qualifier and browser assertions,
accepting only the truthful terminal `cancelled` or `interrupted` while retaining
the same invocation, exactly one unknown effect, blocked close and all physical
ownership/timing/provenance checks. The separate killed-process control remains
strictly `interrupted`. No core change is justified by these facts. TD-068,
TP-002, STP-001 and IP-002 now state this distinction and validate with zero HELIX
findings. The failed live31 run still produces no final acceptance receipt.

## Batch19 truthful graceful-stop predicate

The correction changes only the live qualifier, its controller controls and the
live browser assertion. Twenty-nine focused controller cases pass, including an
actual recovery read before each rejection, pending-result refusal, missing or
mismatched effect refusal, retained close denial and a failed browser child after
a valid terminal status. TypeScript typechecking and live-spec lint pass. Core
cancellation and durable effect semantics are unchanged.

Source19 contains 644 files with canonical inventory-map SHA-256
`fba37587108836711aa4559136e43d403ffdb2cc73bab8de8f6e002d6e184c98`.
Collection contains 2,442 unique cases in 107 bounded groups. All static checks,
45 unchanged guide fingerprints and shared publication/source-literal guards
pass. The full19 aggregate and retained installed matrix remain unstarted until
a fresh two-case live qualification passes against this exact cohort. Candidate
construction, focused controls and prior diagnostic rows do not close the
selected-profile or release acceptance gates.

The live32 attempt failed before a valid durable-observer read. Its private
overlay selected a fresh diagnostic schema instead of the already provisioned
one; the read failed with PostgreSQL SQLSTATE `42P01` (undefined table). Read-only
catalog verification found the original reviewed schema/table and reader grants,
while the newly selected schema was absent. This does not establish a missing
committed row or a product shutdown failure. No new DDL or product correction is
justified by that lookup. Source19 remains unchanged, full19 remains unstarted,
and the next private invocation must bind the existing provisioned observer
reference before deployment or driver execution.

Full19 has now started in parallel with the repaired live33 qualification. Root
approved this ordering because live32's private schema drift required no product
change, the source19 predicate has reviewed focused evidence, and live33's
existing-schema/grant preflight and shared-client case pass. Source19 remains
immutable; any genuine source defect must stop the aggregate before an edit.
The command uses Node 22.22.2 and a fresh retained installed-package directory.
No aggregate, complete live-profile, performance or release pass is claimed.

Live33's two actual browser cases and strict durable-observer read passed against
source19, with source maps unchanged. Final receipt verification rejected it:
the driver recorded Node 26.12.1 instead of the selected Node 22.22.2 cohort.
The receipt remains private diagnostic evidence and has not been promoted to
current selected-profile proof. The next invocation reuses the reviewed overlay
and must preflight and preserve Node 22.22.2/npm 11.20.0 for the whole driver and
its children. This execution-environment correction requires no source edit;
full19 continues under its explicitly selected Node 22.22.2 environment.

Full19 stopped with 1,761 observed Python passes and one failure in
`test_terminal_ui_keeps_context_failure_visible`: the terminal log lacked the
expected context-failure notice after submission. Its [exact failed Python
receipt](web-platform/full19-python.json) preserves the source19 inventory and
clean collected identifiers. JavaScript, browser and installed-package stages
were not reached. Source19 remains unchanged during the active live35 run;
read-only presentation-order diagnosis must distinguish a lost notice from an
incomplete test wait before any correction. No complete deterministic pass is
claimed, and selected-profile expiry will wait for the corrected full gate.

Live35 passed both actual cases under Node 22.22.2/npm 11.20.0. Independent
`verify_live` validation passed against source19, including the selected profile
binding, app/driver versions and strict transport/observer evidence. Its [exact
qualified source19 live receipt](web-platform/source19-apps-live.json) has SHA-256
`22cdc7896b94737639be64c55d647314dd3ac674eb8553404259abc668afad5e`.
This is explicitly instrumented source19 evidence; it cannot qualify a later
source cohort. The selected seven-case collector and natural-expiry wait have
not started.

A private local trace confirmed full19's TUI failure is a presentation defect:
`_finish` writes the context-failure note, then a terminal feed refresh clears
the log about 53 milliseconds later without reconstructing that note. Waiting
another half-second does not restore it. The reviewed correction reconstructs
terminal status through current public snapshot/entry reads, with finite bounds,
selection/new-turn coherence and authorization safeguards. It does not change
core execution semantics or weaken the original assertion. Source19's freeze is
released after live35's terminal result. Full20 must pass every deterministic and
installed stage before another full-source live run or selected expiry starts.

Source20 freezes 646 source entries with canonical inventory SHA-256
`d0546db880438ca7e1c2d4b301af3239211dac779a7146422ba82f385775084a`.
The terminal correction reconstructs fixed notices from a captured public journal
prefix, checks freshness after metadata reads, and stops further reads when view
shutdown has been claimed. Its scanner refuses empty or gapped prefixes, caps
reads at eight pages and 256 entries, and reports unavailable status after a read
limit. The unchanged context-failure regression passes; the broader focused
ownership/feed/streaming/remote-client group passed 52 cases, followed by the
additional later-page read-limit regression in a 31-case focused pass.

Final static checks passed. Public collection observed 2,461 exact cases in 107
bounded groups. Shared publication checks found no credential-shaped values in
the 83 changed/new Python test ASTs, 43 TypeScript/JavaScript ASTs, collected
identifiers or eight historical public receipts. All 45 public-guide source
fingerprints match. These are focused and preflight observations; the complete
source20 deterministic aggregate and future source20 live/expiry receipts remain
pending.

The full20 deterministic gate has now started with Node 22.22.2/npm 11.20.0 and
a fresh retained package workdir. A final focused check executed all seven
reference-client assertions successfully in 7.46 seconds; its command exited 1
because the focused invocation left the repository-wide coverage floor enabled
and measured only 29% coverage. That subset result is not a full coverage pass.
Full20 retains the unchanged aggregate 85% floor and all required stages.

Full20 stopped in the Python stage with exactly 1,952 passed cases and two
failures. Its [unchanged observed Python receipt](web-platform/full20-python.json)
has SHA-256
`ad62c52b141a4e897b3b2f6ac10ce389332f9853a538badeed4a147571d62c19`
and confirms source20 remained stable. Both original regressions,
`test_terminal_ui_keeps_context_failure_visible` and
`test_borrowed_view_close_preserves_other_client_and_accepted_work`, passed.
The failed cases are
`tests/test_selected_profile_qualifier_controls.py::test_missing_schema_refusal_uses_valid_connection_and_no_mutation`
and
`tests/test_selected_profile_qualifier_controls.py::test_missing_schema_refusal_rejects_unclassified_or_mutating_failure`.
Their isolated fake example package exposes only the PostgreSQL seam, but the
shared error classifier imports the full example runtime and fails importing its
qualification module. No JavaScript, browser or installed-package stage ran;
there is no source20 aggregate pass. The next correction must keep classification
independent of composition and retain the isolated refusal controls. Live,
natural-expiry and full-performance runs remain on hold.

The source21 correction moves the unchanged schema-refusal classification to
`scripts/selected_schema_failure.py`, which imports only the public
`IntegrityError` type. Both original callers delegate to it. The two previously
failing tests passed together in a fresh isolated process, and 45 related controls
passed. Independent source reviews found no weakened refusal rule or fixture.
Source21 freezes 647 entries with canonical inventory SHA-256
`ab782a7e71e80541021b8290f699854f83c90a6a4abfd45485b20d368df65da5`.
Final static and publication checks passed; the 2,461-case, 107-group inventory is
unchanged from source20, and all 45 guide fingerprints match. Full21 has started
with Node 22.22.2 and a new retained installed-package workdir. Its complete
result, installed wheel comparison and terminal probe remain pending.

Full21 passed all 2,461 Python cases at 87% aggregate coverage, then passed
32 JavaScript unit cases, four clean npm-package cases and three real-browser
journeys. Exact, source21-bound stage receipts are retained for
[Python](web-platform/full21-python.json),
[JavaScript](web-platform/full21-javascript.json),
[npm packages](web-platform/full21-npm.json) and
[browser](web-platform/full21-browser.json).
The Python receipt individually confirms both original TUI regressions and both
source20 classifier failures passed.

Full21 nevertheless failed the installed-package stage: the independent embedder
copies `collect_tests.py` as a standalone collector, but that collector now imports
the shared publication validator from `test_suite`, which was not copied. Its
clean interpreter refused with `ModuleNotFoundError: test_suite` before the
embedder tests ran. There is no installed-matrix or complete aggregate pass.
The publication guard must remain enforced when its pure dependency is copied
with the collector. A fresh isolated consumer must exercise that production
copy path before the next full gate. Candidate36 remains undeployed; live,
natural-expiry and full-performance qualification remain on hold.

Source22 isolates the unchanged test-ID policy in `scripts/public_test_ids.py`
and copies that pure helper beside the standalone embedder collector. Both the
collector and bounded suite runner still enforce the same publication refusal.
Copied-collector subprocess controls preserve exact safe IDs and refuse unsafe
IDs before terminal output or inventory creation. Independent review found no
other isolated Python-script copy site. A fresh execution of the production
`qualify_embedder` path, using the retained wheels and a new clean project, passed
its installed closure, strict typing, collector and actual consumer test before
the next aggregate began. That is focused evidence, not a completed package
matrix.

Source22 freezes 648 entries with canonical inventory SHA-256
`216c3c7888af3ffa09e564f3d981163e8f05e55e7e4ab837466430986d5b50c6`.
Final static and publication checks passed, including 2,465 exact test IDs in 107
bounded groups and all 45 guide fingerprints. Full22 has started under Node
22.22.2 with a fresh retained workdir. Its complete result and subsequent
installed wheel/terminal checks remain pending; no new live or expiry run has
started.

Full22 completed successfully against the unchanged 648-entry source identity.
It passed 2,465 Python cases at 87% aggregate coverage, 32 JavaScript unit cases,
four clean npm-package cases, three real-browser journeys and 48 installed checks
across ten clean Python profiles. The independent copied embedder also passed in
the complete matrix. The [exact aggregate receipt](web-platform/full22-aggregate.json)
has SHA-256
`4708be550c8b4c9aeca06deec0bd2fa6d3418192abde6c8b1fb4bb94d712fa1e`.
Stage receipts preserve the actual current outcomes for
[Python](web-platform/full22-python.json),
[JavaScript](web-platform/full22-javascript.json),
[npm packages](web-platform/full22-npm.json),
[browser](web-platform/full22-browser.json) and
[installed packages](web-platform/full22-installed.json).
The Python results include both original TUI regressions, the two classifier
controls and all three real local OTLP receiver/exporter tests. This qualifies
that selected local telemetry profile; it makes no cloud-collector claim.

All four first-built installed wheel archives are byte-identical to candidate36,
as recorded by the [raw archive comparison](web-platform/full22-wheel-comparison.json).
Candidate36's original build source20 provenance is retained: source21 and
source22 changed qualification scripts and controls, not runtime distribution
content. The [actual installed terminal proof](web-platform/full22-terminal.json)
passed with the retained combined interpreter: 0.898088 seconds to startup and
0.185974 seconds to reply, under the original 10-second and 1-second limits.
The network-denied journey used fabricated data and exited cleanly. These
deterministic and installed results do not close selected live, genuine-expiry,
full-performance, manual-review or authentic-capture obligations. Those remain
explicitly pending before acceptance closure and release disposition.

Live36 then passed both required source22 browser/TUI cases. Independent
`verify_live` validation passed against the selected profile/origin binding, all
648 current source entries, the qualified runtime versions and the strict
shutdown/transport observer evidence. Its [exact source22 live receipt](web-platform/source22-apps-live.json)
has SHA-256
`c431cb710e005375f7899756ec5a0ffee2c4c9c7d5805ca2120383c34a6b06ef`.
SIGTERM-to-report was 12.953530 seconds and launcher main returned in 12.953766
seconds within the configured 14-second bound. The receipt preserves the
explicit `instrumented-qualification-only` scope. This establishes the selected
AC1/AC5/AC8 cases, not the seven remaining selected-profile criteria. Their
collector, including genuine credential expiry, remains pending.

The subsequent source22 selected-profile run stopped in AC2 when the remote
client correctly represented a concealed cross-owner refusal as the public
`UnknownSession` type. The collector expected only the wire-level problem type;
no selected-profile receipt was written and no genuine-expiry or performance run
started. The source23 correction accepts only the typed public 403/404 refusals
or their corresponding wire problems, while retaining failure for successful
cross-owner access, unrelated failures and other status codes. The earlier
source22 deterministic and two-case live receipts remain historical proof of
that exact source identity; they are not rebound to source23.

A controlled diagnostic execution then called the production helpers directly,
without issuing a qualifying receipt or bypassing the normal final collector.
AC2's API/raw-RLS assertions and AC9's immutable bundle assertions passed. The
remaining failures were classified before another full aggregate was scheduled:
AC7 attempted to read a workspace host from the runtime settings although the
explicit selected SDK client already held it; AC3's primary caller lacked
workspace-SCIM administration before any deactivation; AC4 could not classify a
connection-startup authentication refusal through the pinned Psycopg interface;
and AC10 expected a nonempty SYSTEM stream that the selected deployment did not
emit. The owned test identity was independently verified active after the
diagnostics. These findings guide bounded collector corrections; they do not
establish passing AC3/AC4/AC6/AC7/AC10 evidence or relax their governing criteria.


Subsequent direct production-helper diagnostics passed AC4's independent,
verified-TLS authentication oracle and AC7's complete reference-constructor
startup/refusal checks. AC10 initially lost its bounded log subscription during
a roughly 51-second fabricated turn; a 180-second CLI subscription enclosing a
150-second absolute evidence window then passed the same actual APP/platform
privacy assertions in 50.7 seconds. This changes only the qualification capture
window, not the product's 14-second shutdown bound. These are focused diagnostics
without a selected-profile acceptance receipt.

AC3's account-level deactivation and workspace propagation checks exposed a
separate restoration problem. The exact owned identity was recovered using the
documented SCIM value-array operation, then independently observed active with a
fresh service client and protected HTTP 200. A later bounded diagnostic failed
restoration authorization when it reused the original captured bearer. That
result does not establish the original bearer's post-restoration behavior. The
collector now separates the captured, still-unexpired revocation credential from
a genuinely fresh recovery credential; final review and an actual passing AC3
diagnostic remain required before source23 freezes. No genuine-expiry or full
performance run has started.

The public-registry read also found a stable core 0.2.0 release whose verified
wheel exposes the older client facade without the candidate public host API.
The candidate's former internal range admitted that unqualified artifact. Internal
Python dependencies now require exactly `0.2.0.dev0`; npm dependencies were already
exact. Deliberate lock updates preserve all existing package identities, versions
and registry records. A fresh API wheel and the real production compatibility
helper passed two independent solver-refusal probes against hash-pinned published
cores 0.1.0 and 0.2.0. This is focused package evidence, not a new complete matrix
or a claim of registry ownership, publication or final source23 acceptance.


The final source23 preflight passed all static checks, 2,558 exact collected
Python IDs in 110 bounded groups, the shared publication checks over changed
Python/TypeScript fixtures and historical receipts, and all 45 guide source
fingerprints. The reviewed qualification changes also passed 151 focused
controls, including an actual SDK OAuth request held on loopback and terminated
by its owned child supervisor. Source23 freezes 667 entries with canonical
inventory SHA-256
`f1ddfbcbfb14643edc4763952116aaeeef48bc3a17405dff7c046fb32804f1b4`.
Its fresh complete deterministic gate is running; no source23 aggregate pass is
claimed yet.

The next AC3 diagnostic stopped in credential setup before any identity mutation.
Its child was waiting for the exact Keychain item, not making an OAuth or SCIM
request. Bounded CLI and native Security-framework reads both waited while the
Mac was locked; the user was asked to unlock it. No Keychain ACL or password
workaround was applied. Local deterministic qualification proceeds independently,
while AC3, the source-bound managed-process probes and final live receipts remain
pending. Prior source22 live evidence is not relabeled for this source cohort.


Full23 completed all ten deterministic stages against the unchanged 667-entry
source identity. It passed 2,558 Python cases at 85% aggregate coverage, 32
JavaScript cases, four clean npm-package cases, three real-browser journeys and
49 installed checks across ten isolated profiles. The new installed check proves
real refusal of the hash-pinned published core 0.2.0 in addition to the existing
0.1.0 negative. The actual browser asset gate measured 131,220 gzip bytes against
the 250 KiB reference budget.

The [exact aggregate receipt](web-platform/full23-aggregate.json) has SHA-256
`9ce1db261913a1856e86c48fdda90f00ce84eac5457006fd0c616c06fe442320`.
Its stage receipts retain exact observed results for
[Python](web-platform/full23-python.json),
[JavaScript](web-platform/full23-javascript.json),
[npm packages](web-platform/full23-npm.json),
[browser journeys](web-platform/full23-browser.json) and
[installed packages](web-platform/full23-installed.json).
The [actual installed terminal proof](web-platform/full23-terminal.json) also
passed: startup took 1.509338 seconds under the original 10-second limit and the
first reply took 0.187045 seconds under the original 1-second limit, with network
connections denied and a clean exit. These results establish the selected local
profiles. Current selected Apps, genuine-expiry, managed-process, performance,
manual-review and final cleanup obligations remain separate; no acceptance
closure is inferred from this aggregate.

The source23 full reference performance execution subsequently failed in the
client workload. The store stage completed its declared 10,000-session by
200-entry workload and separate 100,000-entry session with 768-byte facts and
100 samples. Its measured list-50 p99 was 12.246083 ms, exceeding the original
10 ms proposal. View, middle-page entries and append-four p99 values were
0.017 ms, 0.578292 ms and 13.080667 ms respectively. These are store-only
observations on the recorded local hardware and explicit capacity settings;
they do not qualify the other performance targets.

The following client stage stopped when the 100-turn workload indexed an empty
result-message tuple. No client measurement receipt or complete performance
pass was produced. The actual terminal reason is under investigation before
any source correction or target disposition. The proposed NFR7 amendment
remains a draft, and the first baseline is not a historical regression pass.

A private diagnostic retaining the full client workload reproduced the first
invalid result at turn 28: `context_unavailable` with zero messages after
30.382 seconds. The estimator recorded 867 requests and 674,626 bytes, below
its declared fixture caps. The diagnostic did not record the typed reason, so
the elapsed time alone does not establish a timeout cause. A further bounded
phase profile is required before choosing a correction; no timeout or fixture
capacity was increased.

The bounded full-workload phase profile then reproduced
`context_unavailable` with typed reason `context_failed` at turn 27, after
30.361 seconds. The JSONL turn-log getter made 876 calls and deep-copied
3,097,571 entries in 27.853 seconds; source captures occupied 29.449 seconds.
These nested timings overlap and are diagnostic observations, not accepted
performance measurements. They identify repeated full-journal copying on the
current context-read path as the dominant cost within the unchanged
30-second context deadline.

The approved correction retains fixed accepted prefixes with copies on access,
selects only matching metadata for each freshly authorized read, and keeps
generic borrowed-log behavior. It must preserve current policy decisions,
durable authorization facts, source and link validation, digest/hop checks and
nested mutation isolation. A separate benchmark-only nonrecording fabricated
estimator also removes the recording fixture's finite-request ceiling from the
100-turn reference workload; that ceiling was not the observed failure cause.
Source23 receipts remain evidence for source23. Implementation, full-workload
diagnostics and a fresh complete gate are required for the replacement cohort.

The replacement's comparison and isolation controls passed, including native
PostgreSQL/JSONL checks and preservation of subclass query matching. Its first
full 1,000-follower, 100-sample diagnostic nevertheless failed the nonfinal-or-empty
turn-result guard after 513.005 seconds, without producing a result receipt.
That probe omitted the failing ordinal and typed reason. The 670-entry source
map remained unchanged, and the replacement's formal freeze and complete gate
were withheld. The next private diagnostic records bounded turn/reason metadata
and cumulative inclusive phase profiles, preserving checkpoints in a private
log even if its existing physical deadline expires. No additional product
change follows from the unclassified failure.

The checkpointed combined profile classified the next failure at turn 71:
`context_unavailable` with typed reason `context_failed`, zero messages and
30.502 seconds for that turn. The unchanged 670-entry diagnostic cohort took
623.061 seconds overall. Repeated full-prefix metadata-index construction
accounted for 392.961 seconds across 42,315 calls; detached entry copying took
35.441 seconds across 10,246,923 calls. These cumulative inclusive diagnostic
figures overlap with read/capture timings and are not a performance receipt.
The next approved correction validates dense canonical positions once per fixed
view and uses positional candidates for source/membership lookup, retaining
grouped fallback for sparse, reordered, duplicate and coercive queries.
History-link selection remains grouped by its separate message-index field;
a link's own journal position is not its target message position. All existing
authorization, copy isolation, deadline and workload requirements remain in force.

After the dense-prefix correction and preservation of separate history-link
selection, the next full client diagnostic completed every phase with 1,000
followers and 100 samples. It exited successfully after 899.066 seconds, with
all 100 terminal turns accepted and the 670-entry source inventory unchanged
before and after execution. This is a pre-freeze workload diagnostic, not a
formal performance receipt, target pass or regression comparison. Its measured
streaming loop-lag p99 of approximately 262.78 ms and full-turn p99 of
approximately 17.13 seconds remain observations requiring review against the
appropriate scoped targets.

The remaining NFR7 measurement work is being completed before the next formal
freeze: beginning/middle/end and deterministic spread page positions on both
declared-capacity local and MemoryFiles JSONL stores; actual VolatileSettings
client/follower observations; operational CLI first output; retained-cursor and
retention-floor reconnect; public Engine boundary timing; and direct retained
Python follower allocations with explicit queue size and verified buffer states.
The MemoryFiles full-reference store uses an explicit 4 GiB index capacity; it
does not change the shipped VolatileSettings capacity. Remote Apps read latency
requires its own authenticated, deployment-bound observations. No proposed
budget has been adopted from the diagnostic results, and no replacement-cohort
aggregate, live, managed-process or performance completion is claimed here.

The expanded-harness combined native check subsequently completed with 175
passing cases and two failures in 154.96 seconds. The public-client smoke
refused a changed source inventory while final helper edits overlapped its
measurement. The new RAM credential-producer loopback control also missed its
OAuth POST barrier after its test-only two-second child timeout; the earlier
native credential slice had passed that control. The correction keeps the
production 25-second physical deadline and includes cold SDK import and OAuth
discovery within that same bound. No production timeout, benchmark workload,
coverage floor or terminal assertion is relaxed. The failed combined run is
retained privately; affected controls and the guarded smoke must pass on quiet
source before the next long diagnostic or formal freeze.

Generic RAM qualification and managed-process operators are now archived under
`web-platform/qualification-ram/` with exact hashes and nine offline mocked
lifecycle controls. This is a source archive, not an actual secret creation,
managed-process result, selected seven-case receipt or cloud qualification.
Actual execution must use those exact operator bytes, the current source/gate
and browser bindings, and a physically supervised clean environment. The
normal collector still defaults to the Keychain-backed bounded SDK producer;
the explicitly injected factory labels its credential composition separately.

The quiet small-client rerun passed all five controls. The RAM SDK loopback
control also passed with the production 25-second child deadline, a 20-second
POST readiness barrier and one 35-second test-body bound; its pytest command
wall time includes import and session setup and is not a command-latency metric.
Final quality/privacy checks covered 2,691 collected cases and 45 current guide
source bindings. The 696-file map was handed to the supervised pre-freeze
measurement runner as a diagnostic cohort, with full-gate acceptance withheld.

A subsequent read-only NFR6 review found a genuine lifetime-bounds defect:
each long-lived follower retained terminal attempt IDs in suppression sets,
and each later frame allocated their union. Queue capacity alone did not bound
that accumulated metadata. The approved correction uses a permanently sealed
per-finish disclosure scope so delayed authorization/callback completion cannot
reopen an old phase, then retires suppression IDs only at known producer
terminal notification. Overflow and reconnect suppression must remain effective
while the producer is active, and queued text/reset events must be preserved.
Unexpected failure and cancellation cleanup must seal the scope, attempt every
terminal callback and preserve the original failure without synthetic journal
facts. Source edits remain held until the active memory diagnostic settles;
current results are not promoted to a final freeze or release claim.

The supervised 696-file diagnostic completed 100 engine samples and the full
100-trial/1,000-follower memory profile with unchanged source and no surviving
owned process group. These remain diagnostic measurements. The memory values
are observed whole-trial **net** traced-Python allocation growth, including
fixture, model, producer and observation allocations; subtracting two whole
process snapshots does not establish an isolated per-follower total or a
mathematically guaranteed upper bound. Before formal measurement, the three
full-state growth keys are therefore renamed from `python_growth_upper_bound_bytes`
to `python_growth_bytes`, with explicit limits and matching ratchet identities.
The prior diagnostic keeps its original labels as historical bytes; its numeric
observations are not silently relabeled into a formal bound or current receipt.


## Source24 final fanout and gate checkpoint

The paired source-held streaming diagnostic verified all 1,000 consumers received
the exact ordered 200-character reply and matching committed terminal, with zero
missed events, for both implementations. Original fanout measured p99 event-loop
lag 52.7574 ms; the finite batch-32 variant measured 8.09854 ms. These are scoped
diagnostic observations, not a formal baseline or throughput claim.

The selected implementation snapshots eligible buffers, yields between batches
of 32, then rechecks exact active-attempt generation and current buffer membership.
Focused controls cover terminal completion at the yield, subscription replacement,
cancellation and equal-valued identity re-registration. Together with the prior
phase-sealing and marker-retirement changes, they passed 35 focused live/follow
checks. No policy gate, queue capacity or product deadline was weakened.

The source24 gate is running against the frozen 697-file inventory with canonical
SHA-256 `d89c61ab673ac9506ae26ee1dc4d31ba09bfd6442a9bc97d1be6d777cc3309b3`.
Initial build, whole-source static checks and the base dependency boundary passed;
full bounded Python, browser and installed-artifact results remain pending.
Current formal performance, Apps and selected-profile receipts are also pending.
[The budget review worksheet](web-platform/performance-budget-review24-draft.md)
preserves every original PRD proposal and requires an explicit measured scope and
justified acceptance or replacement before WP-36 can close.


The full source24 run subsequently failed
`tests/test_context_summary_process.py::test_process_kill_reuses_only_accepted_summary[after_context_use]`.
The observed Python receipt records 1,082 passed cases and that one failed case;
the complete 2,705-case inventory did not finish. The child did not emit its
initial `created` event within the existing 20-second receive bound, before the
requested kill boundary. That event follows helper imports, local state opening
and a complete first turn, so the failure alone does not identify which stage
stalled. The fixture discarded captured child stderr on this timeout path.
The source inventory remained unchanged. No deterministic aggregate, installed24,
current media or formal performance success is claimed. One private phase-timed
diagnostic retains the same receive/model limits and actual child cleanup;
source changes remain held pending a concrete cause and reviewed correction.


[The unchanged failed Python receipt](web-platform/full24-python-failed.json)
retains the exact inventory and observed outcomes. A corrected private
[phase diagnostic](web-platform/full24-context-startup-diagnostic.json) reproduced
the initial-event timeout: imports took 16.6446 seconds; opening state took
0.6327 seconds; the first turn started about 17.2779 seconds after the import
marker. It was still underway at the unchanged 20-second receive deadline.
The child produced no exception or protocol event and was killed and reaped;
stderr drained and cleanup reported no error. Its nine relevant source/test
hashes match the frozen inventory. One preceding diagnostic attempt had an
undefined instrumentation variable and provides no product evidence.

The corrected diagnostic observed host load averages of approximately
142.34/105.70/73.93 before execution and 117.47/102.43/73.50 afterward on the
18-core reference machine. This supports severe host contention as a material
startup constraint; it does not prove which work delayed the original child or
establish a product hang. No timeout, assertion or source behavior is changed.
Further targeted checks and the next complete gate wait for a quiet machine
window. Formal measurements, deployment and acceptance closure remain held.


A later unchanged three-phase target check, authorized after process-launch
latency improved, passed two parameters and failed the same first-`created`
20-second wait in `after_context_use`. The targeted pytest module finished in
72.78 seconds; its retained 180-second physical owner exited with code 1 and
verified the complete source inventory unchanged. [The target receipt](web-platform/full24b-context-target-failed.json)
records load rising from about 52.09/86.78/87.23 to 126.39/96.52/90.55. The
complete gate was not restarted, and no further blind retry is authorized.

The `after_summary_commit` and `after_context_use` child paths are identical
through the initial `created` event: both pause on model call three, and their
phase-specific hook is installed only after that event. A failure before it
does not isolate an `after_context_use` branch. The original uninstrumented
failures still do not identify the stalled stage. No source or deadline change,
acceptance closure, formal measurement or deployment follows from these runs.


## Quiet-host source24 continuation

On 2026-11-02 the reference host recovered a quiet workload window. The complete
697-file source inventory still matched the frozen source24 hashes. The exact
unchanged three-parameter context-summary process module then passed all three
cases in 2.52 seconds, with source equality and retained child settlement.
[The targeted receipt](web-platform/full24c-context-target.json) records load
averages around 4.29/5.86/6.86 before and 4.19/5.81/6.84 after execution. The
20-second receive waits, 5-second model bound and 180-second physical owner were
unchanged. The prior failed aggregate, failed targeted check and startup
diagnostic remain separate historical evidence; this targeted pass does not
qualify the full source or establish performance budgets.

The complete source24 gate has restarted with a fresh log and still-unused
installed-artifact directory. All 2,705 tests, global coverage, browser, package
and installed-profile checks remain required. Formal performance, current media,
live selected profiles and acceptance closure remain pending actual receipts.


The complete source24 retry subsequently passed all ten deterministic stages.
[Its aggregate](web-platform/full24-aggregate.json) binds the unchanged 697-file
source inventory and Python 3.11.17, Node 22.22.2 and npm 11.20.0. All 2,705
Python cases passed across 117 bounded groups and the unchanged global 85%
coverage floor passed. JavaScript's 32 unit cases, four npm consumer/archive
checks, three browser cases and the installed matrix's 49 exact checks across
ten profiles passed. The build's JavaScript gzip total remained 131,220 bytes.
The separate failed Nov1 receipts remain retained rather than replaced.

The actual installed combined-profile terminal journey also passed offline with
network connections denied, a fabricated message, visible approval, durable
journal and clean exit. Its one-sample startup was 1.055179 seconds and reply
0.136011 seconds; this is functional journey evidence, not the formal p99 startup
baseline. [The terminal receipt](web-platform/full24-terminal.json) binds the
current matrix and source before and after execution.

One current installed browser capture passed all three journeys. The media
receipt binds the same source, Python 3.11.17, Node 22.22.2, npm 11.20.0 and
Chromium 156.0.8078.4; all three image hashes were checked. Root's visual review
and the formal performance/profile receipts remain independent requirements.
No local deterministic or media result claims live Apps qualification,
publication, final budget adoption or complete acceptance closure.


The six serial source24 reference workloads subsequently completed with exact
source equality, strict workload validation and owned process-group settlement.
[Their observation archive](web-platform/reference-performance/source24-observations/README.md)
retains exact receipt bytes and hashes. Completion is not acceptance of numeric
proposals: list latency, volatile streaming wall lag and operational startup
proposals were exceeded, and engine/memory boundaries require explicit scope
disposition. Apps remains separately pending. The original proposals and all
misses remain visible in the [budget worksheet](web-platform/performance-budget-review24-draft.md).

A final source25 cohort is planned for two bounded changes: localized list-query
allocation/key optimization and generation-event notification removing the
measured 100 ms same-store delivery floor while retaining the existing polling
fallback. Source24 receipts remain evidence of
source24; they will not be rebound to the changed implementation. No current
budget, historical regression result or complete acceptance status is promoted.


## Source25 deterministic gate interruption

Source25 froze 699 included files after the localized query optimization and
immediate accepted-publication notifications with the original polling fallback.
The global static checks, all 45 guide bindings and 37 native query/PostgreSQL
parity cases passed. Collection found 2,733 cases in 117 unchanged bounded groups.
The full gate then stopped with 575 observed passing cases and one failure in
`tests/test_benchmark_follower_memory.py::test_public_follower_trials_prove_queue_and_live_buffer_occupancy`.
[The exact failed receipt](web-platform/full25-python-failed.json) retains this
outcome and unchanged source25 inventory. No full aggregate or installed cohort
was produced, and no later stage or current acceptance status is promoted.

The failed assertion requires an exact coalesced 32 KiB stream tail and matching
identity. It observed an unexpected next event; the failure alone does not
classify its variant or establish lost text. Diagnosis must distinguish a valid
interleaved persisted change from a production ordering/content defect while
retaining exact text, offsets, attempt identity and causal occupancy checks. No
blind rerun, unvalidated event skipping, relaxed content assertion or timeout
increase is authorized.


The subsequent [bounded ordering diagnostic](web-platform/full25-coalesced-order-diagnostic.json)
classified a fixture assumption: the live buffer already held the correct
32,767-character tail at offset one, but the public stream first yielded the
paired `SessionUpdated` and final `EntriesAppended` variants of an authorization
commit, then that exact tail. The initial one-character live event was `q` at
offset zero. The diagnostic reports these fabricated event shapes only; it is
not a full qualification receipt or replacement for the failed gate. A narrow
fixture correction may validate bounded interleaved metadata before checking
the unchanged exact tail. Allocation/occupancy measurement remains before any
drain; content, offsets, identity, chunk count, overflow and cleanup assertions
remain required. No product stream change is indicated by this observation.


## Source26 fixture correction and new gate

The fixture-only correction accepts either the exact live tail immediately or
one validated preterminal authorization-commit pair before that tail. Both
variants must name the selected owner/session/active turn and preserve final
commit boundaries; the tail retains its exact 32,767 characters, offset and
attempt identity. One unchanged aggregate deadline covers all reads. Snapshot
allocation measurement still precedes the drain. Unexpected metadata, wrong
content/identity, repeated pairs and an aggregate-timeout regression are covered.

The final benchmark/follower subset passed all 112 cases in 14.85 seconds. The
source26 map contains 699 files; its exact difference from source25 is only
`benchmark_follower_memory_fixture.py` and its test. The earlier native 37-case
query/PostgreSQL parity result remains applicable to unchanged query code.
Collection found 2,741 cases in 117 bounded groups, and all 45 guide bindings
remain current. A fresh complete gate is now running against source26 and a
new installed-artifact directory; no new aggregate or acceptance PASS is
asserted before that process and its receipt verification finish. The failed
source25 gate remains retained alongside the separately scoped diagnostic.


The complete source26 gate subsequently passed all ten deterministic stages:
2,741 Python cases in 117 bounded groups with the unchanged global 85% coverage
floor, 32 JavaScript unit checks, four npm consumer/archive checks, three browser
journeys and 49 installed checks across ten profiles. Runtime versions remained
Python 3.11.17, Node 22.22.2 and npm 11.20.0; JavaScript gzip stayed 131,220 bytes.
[Its aggregate](web-platform/full26-aggregate.json) and individual exact receipts
are retained with source26's unchanged 699-file map. Installed terminal/media,
formal performance, live qualification and final acceptance were not inferred
from that deterministic result.

A final workflow integration defect was then identified: the performance CLI
requires a fresh external Apps measurement, but the Make target and manually
dispatched reference workflow exposed no way to supply it. A prepared valid
receipt therefore could not make that advertised path usable. The authorized
source27 change is limited to explicit receipt-path forwarding and its controls,
with operator preparation documented in the excluded
[performance prerequisites](web-platform/performance/README.md). Missing or
mismatched input still refuses qualification. No CI credential automation,
optional runtime optimization or weakened profile requirement is authorized.
Source26 remains historical passing evidence; a fresh complete source27 gate
and current installed artifacts remain required before final qualification.


Source27 freezes exactly 700 included files, differing from source26 only in
`Makefile`, the manual reference-performance workflow and its four command-wiring
controls. The Apps receipt is supplied through the `PERF_APPS_MEASUREMENT`
environment; the workflow's required `apps_measurement` input is not interpolated
into shell source. Missing input and unsafe command-line Make assignment refuse
before running workloads. The documented entry path uses environment-prefix
assignment and preserves the existing receipt identity/refusal checks.

Changed-file Ruff/format/Pyright checks passed. Collection found 2,745 exact
cases in 117 bounded groups; all 45 guide bindings remain unchanged. A new full
source27 gate is running with fresh installed artifacts. No source26 receipt
has been rebound to source27, and no current acceptance or performance result
is asserted before the required source27 evidence exists.


The complete source27 gate passed all ten deterministic stages.
[Its immutable aggregate](web-platform/full27-aggregate.json) binds the unchanged
700-file source inventory, Python 3.11.17, Node 22.22.2 and npm 11.20.0. All
2,745 Python cases passed across 117 bounded groups and the unchanged global
85% coverage floor passed. JavaScript unit checks (32), npm checks (four),
browser journeys (three) and installed checks (49 across ten profiles) passed;
JavaScript gzip remained 131,220 bytes. Exact individual receipts are retained
alongside the aggregate. All four raw installed wheel archives were verified
byte-identical to the source26 installed cohort; this does not rebind older
receipts to source27.

Current installed PTY/media, the seven current performance profiles, selected
live/managed acceptance, first-baseline budget disposition, manual claim review
and final resource cleanup remain separate required outcomes. The deterministic
result does not promote those pending scopes or claim final acceptance closure.


The current installed combined-profile terminal journey subsequently passed
offline with network connections denied, a fabricated message, visible approval,
durable accepted facts and clean exit. [Its exact receipt](web-platform/full27-terminal.json)
binds the source27 installed matrix and unchanged source before and after the
journey. Startup was 0.912097 seconds and reply 0.237436 seconds in this one
functional sample; neither value establishes a formal p99 startup budget. The
pending closure draft records these exact observed checks while retaining
manual, performance, live and final acceptance review requirements. Current
media capture remains a separate running operation.


The current installed27 browser capture then passed all three fabricated local
journeys. [Its exact media receipt](web-platform/full27-media.json) binds the
source27 inventory, Python 3.11.17, Node 22.22.2, npm 11.20.0 and Chromium
156.0.8078.4. All three actual image hashes and declared capture-source hashes
were verified. [Root's visual review](web-platform/full27-media-review.json)
records that all three images were actually viewed: approval markup stayed
literal with usable controls; the accepted tool result and final reply were
readable; the mobile composer was contained and session strip scrollable.
The conversation and mobile screenshots show connecting, so uninterrupted
connectivity is not inferred. The approval screenshot shows Connected.

These remain local installed functional and visual observations, not live Apps
qualification, performance budget adoption, complete manual support-claim review
or final acceptance closure. The pending draft records the receipt/review hashes
without promoting acceptance statuses.


The current source27/deploy38 live qualification passed its two exact cases.
[The byte-preserved public receipt](web-platform/full27-live38.json) has SHA-256
`54d5dae949952dc7405d15209dc2de4260a73e189714f806546b6a0d03e4c434`.
The existing `verify_live` verifier independently accepted the explicit selected
profile binding, all 700 current source hashes before and after, exact case IDs,
qualified driver/application versions and structured transport/shutdown evidence.
The browser/remote-client case covers US-068-AC1 and AC5; the held-worker stop,
post-stop durable barrier and restart case covers US-068-AC8. The instrumented
observer's private result also confirmed exact candidate modules, overlay,
protected-probe correlation, worker start, stop and restart. The overlay binds
the reviewed candidate; instrumented qualification does not establish an
uninstrumented production deployment claim.

SIGTERM returned from main after 12.952144456 seconds within the configured
14-second bound. Shutdown reported `complete=false`, retaining `client_host`,
`platform_resources` and `postgres_root`; this is bounded reporting/main return,
not full resource drain. Actual Apps ingress used frontend HTTP/2 and ASGI 1.1.
A separate prior-knowledge H2C probe reached the deployed listener with ASGI 2;
that does not claim the Apps proxy uses H2C. The application ran Linux x86_64
Python 3.11.18, while the driver used macOS arm64 Python 3.11.17, Node 22.22.2
and Chromium 156.0.8078.4.

The archived receipt was checked against 20 retained private operator markers
and for credential-shaped values, URLs, email addresses and absolute private
paths. The private stop-binding record, resource identifiers, settings and URLs
remain private. These bounded checks do not prove absence of every possible
secret. Candidate joins now include only the two observed exact live nodes;
selected-profile seven-case evidence, managed-process two-case evidence, Apps
performance, explicit first-baseline adoption and Root's final manual review
remain pending. The first Apps performance operator attempt failed to decode
a partial stdout read and produced no valid Apps measurement receipt; it is
not a performance result.


All seven actual source27 performance observations are now complete. The
[seven-profile manifest](web-platform/reference-performance/source27-observations/seven-profile-manifest.json)
binds the six serial local observations and separately verified selected Apps
read receipt. The actual Apps receipt has SHA-256
`3acc508dc86fe1c3e9819aa20adfffc4cc0b463a6b4696ef176a08139f266505`:
100 real opens and 100 list requests, stable 50-item pages, persistent sequential
HTTP/2, one accepted preacquired RAM credential, exact deployment/export before
and after. Handshake p99 was 364.316125 ms and list p99 716.556208 ms; managed
server hardware is unknown. This is a selected read profile, not large-catalog,
server CPU, cold-start or SLA evidence. The corrected bounded-EOF operator's
exact archived SHA is `a5bf66c9ebb768b58f8daca446eb5e39e4250c2f8a0a361b3693e808f592ac9e`.
The original partial-read failed attempt remains explicit and has no measurement
receipt; it was not relabelled.

Root accepted the engineering disposition under the PRD's first-baseline
validate-or-replace authority. All seven schemas, full source maps and raw hashes
were independently checked. No prior baseline files existed. The existing
`web_performance.py --establish-from` path then established seven baselines at
the same predeclared tolerance 1.2, without rerunning workloads or comparing an
observation against itself. [The establishment receipt](web-platform/performance/source27-first-establishment/first-baseline-establishment.json)
has SHA-256 `1c33434aa8714878fe9c9d89919a7730515d5f79676d2d038475bf8c0426dc2b`
and seven exact passed establishment nodes. The [exact join manifest](web-platform/performance/first-baseline-joins27.json)
binds all measurements, baselines and 56 selected observed×1.2 metric maxima.
This is first establishment, not a historical regression pass or SLA.

PRD NFR-7, TP-002 and the unchecked WP-36 disposition now explicitly record the
accepted operational definitions and [target review](web-platform/performance-budget-review27-draft.md).
Original memory-list, volatile-loop-lag and PTY proposal misses remain visible;
isolated commit timing, pure engine CPU, isolated native per-follower total and
isolated import are not silently passed. The chosen public boundaries, explicit
queue/capacity/event profiles and net whole-trial Python allocation limits replace
those ambiguous meanings. A higher measured ratchet is not enforcement of a
stricter original cap. The uniform future-variance policy was not independently
fitted to each miss. No additional optional implementation iteration or source/
guide/asset edit was performed. All 45 guide bindings still match and the frozen
700-file inventory remains unchanged.

The first actual RAM-backed qualification operation separately passed both
[managed PostgreSQL process task cases](web-platform/full27-managed38-firstactual.json),
SHA-256 `d35e429f3135cf87846369c41ba140c9c8748ad7bafc2384f19fad1711e2c371`.
They use public source composition against the selected PostgreSQL 18.6 endpoint,
verified TLS and exact nonadmin caller role. A real competing child was fenced,
natural lease expiry allowed takeover, stale append/heartbeat/release could not
change the successor, and actual child kill recovered an interrupted owner with
one unknown effect and no inferred result. This is client-worker/process recovery;
it does not qualify managed-database HA, power loss or region failover, nor claim
installed-wheel/deployed-app execution. There was no DDL or forced SQL expiry.

The subsequent selected seven-case collector failed and produced no receipt.
[The sanitized lifecycle failure](web-platform/qualification-ram/source27-attempt01-failed-selected.json)
retains that failure, exact-new-secret revocation verification and unchanged
operator hashes, without private identifiers or secret metadata. Managed task
observations do not substitute for selected identity/expiry/log acceptance.
Candidate joins add only the two exact managed nodes and seven actual
establishment nodes. Root manual review, selected7, task/dependency reconciliation,
resource cleanup and overall acceptance remain pending; no task checkbox closes
from baseline adoption alone.


[Selected27 preparation/failure evidence](web-platform/selected27-preconditions-and-failures/README.md)
now preserves attempt02's exact safe error frames and a sanitized failed/revoked
lifecycle summary, plus the later warmup/log-readiness preparation. The unchanged
15-second window accepted APP first stdout at2.847750s and unfiltered at2.573644s,
with no stderr and the known fabricated primer marker absent. A private profile
name was redacted only in the public summary; the original private raw hash and
explicit derivation are retained. These are failure/precondition facts, not
selected-case aliases or proof of the fresh privacy invocation. The complete
selected receipt, final manual review and cleanup remain pending.

The [existing candidate release packet](web-platform-release-candidate.md) now
binds current source27 artifacts, local/installed/media/live/managed observations
and seven measured first-baselines, with selected/manual/cleanup still pending.
It records unreleased development versions, tested subsets and prepared closure/
postcheck commands using the unchanged immutable aggregate. No final postcheck,
publication, tag, merge or production rollout is claimed by this preparation.


The latest pending-approval identity observations are GET-only and supersede the
last observed inactive account state without asserting an intervening cause.
Root's exact read-only authority/identity/assignment receipt has SHA-256
`61f5ef6a8d8136701bd00d61d2a2a08c37eda3d2026aa15b33f1ecb3f5625ba5`:
account SCIM reports the exact owned target active, with account-admin operator,
identity/reference and sole workspace-assignment guards verified. The separate
bounded stability receipt has SHA-256
`a1f628c7e7c4fde4d151a3d066075a8ddb01097b983ac69464c4e8ca1d67dcf5`.
It records 39 observations and39 account-active observations, but zero verified
workspace-SCIM active observations and status `active_stability_not_proven`.
Its explicit account/workspace/both three-consecutive flags remain false; the
counter alone is not promoted into a stronger verified convergence statement.
Neither receipt proves the service identity can mint a token or access the
protected application, and the earlier credential rejection's cause is unknown.
Both report no mutations, no credential values/raw errors emitted; the stability
check also reports no credentials minted.

Automatic approval review previously rejected the requested active-false→true
SCIM activation because persistent reactivation could enable credentials; prior
creation authorization was not accepted as reactivation authorization. Root has
requested explicit user approval, which has not arrived. No activation or other
mutation was performed after that denial, and no alternate route was attempted.
The prepared single activation would now refuse its inactive-state precondition;
a fresh exact read and applicable authorization must precede any later use.
A controlled revoke/restore qualification sequence remains held for the explicit
pending approval. All three extra attempt secrets remain verified revoked.
Selected7 still has no receipt, manual acceptance/cleanup remain pending, and
neither the source27 cohort nor its seven established baseline profiles changed.
No final goal completion or live identity acceptance is claimed.


The owner subsequently explicitly authorized restoration of the exact temporary
test service identity. This is new authorization after the earlier automatic
approval rejection; it does not retrospectively authorize a different target or
reinterpret the failed attempts. A fresh GET-only account preflight reports the
exact target active. The read-only workspace diagnosis distinguishes exact-ID
GET permission denial from inactivity: exact application-ID filtered listing
returned one active match with the same account identity. It did not mint a
token, mutate the identity or establish protected application access. Earlier
OAuth rejection still has no proved causal classification.

[The new contextual injected-factory policy](web-platform/qualification-ram/contextual-policy-source27-manifest.json)
is separately archived with operator SHA-256
`13b36ed8dcf6655553e238c7739ae18c80ff450a66af9b216f6fae04dfb9344a`.
It keeps the actual bounded SDK RAM producer and the same immutable secret,
profile, host/clientID and before/after config hash. Only exact base RuntimeError
with the single fixed `identity_credential_rejected` code, after a prior
successful mint whose local unsigned JWT expiry hint remains future, becomes a
typed retryable signal. This is an explicitly contextual bounded-retry policy,
not evidence that the SDK rejection was transient or a change to default
Keychain qualification. Unmatched/initial/expired/opaque-context failures remain
unchanged, cancellation propagates directly, and a failed mint never returns the
cached bearer. A successful new producer call may legitimately return identical
bearer text; string inequality is not a required freshness assertion.

Only the existing restoration consumer retries this typed signal under its
unchanged 90-second absolute deadline, two-second pauses and 30-second minimum
remaining-auth budget. Fresh mint, exact `/Me` active identity and protected200
assertions remain unchanged; no outer loop, timeout extension, old-bearer fallback
or private assertion patch is added. AC6 real expiry assertions remain unchanged.
The [13 offline controls](web-platform/qualification-ram/contextual-policy-controls27.json)
passed and their exact source is archived; no control or policy archive is live
qualification. The unchanged managed operator remains SHA `64408757…09f1f`.

Attempt04 is now running under Root's exclusive cloud/identity ownership with
this reviewed explicit factory. Fresh source/export/log-readiness checks passed
before credential creation, and the source27 cohort/seven baselines remain
unchanged. All original seven selected cases and exact temporary-secret
revocation are still required before any complete selected receipt is accepted.
No earlier returned case, managed repeat or successful preparation is relabelled
as selected evidence. Final selected qualification, manual support-claim review,
cleanup and overall acceptance remain pending while the operation runs.


Attempt04 terminated with no complete selected receipt. The exact safe trace
records `test_identity_deactivation_not_stable` in the selected identity helper
([failure frames](web-platform/selected27-preconditions-and-failures/attempt04-safe-error-frames.json)).
The original helper waited for workspace `/Me` inactivity, then treated the first
account SCIM read not reporting false as failure instead of allowing the two
surfaces to converge within one shared bound. Root reported that retained
restoration subsequently succeeded through fresh credential, exact `/Me` and
protected read. The contextual factory emitted one retryable signal, and the
[sanitized lifecycle](web-platform/selected27-preconditions-and-failures/attempt04-lifecycle-sanitized.json)
records verified exact-new-secret revocation and unchanged operator hashes.
All four extra attempt secrets are verified revoked. No early returned case or
managed repeat is a selected acceptance alias.

This actual qualifier convergence defect requires a source correction rather
than another operator workaround. The authorized minimal fix shares the original
90-second deactivation budget across workspace state, exact account state and
one protected denial. It must retain exact identity/application matching,
strict boolean active state (missing is not false), captured-token unexpired
checks and unconditional retained restoration. It adds no second deadline,
clock restart, longer cap or weaker denial assertion. TP-002 records this
clarification of its existing identity/lifecycle requirement; no new ADR or
workstream is introduced.

The source27 deterministic/installed/live/managed measurements and seven adopted
first-baselines remain actual historical observations of source27. They cannot
be relabelled as current source28 evidence. After the focused fix and review, a
new freeze and full native gate, current installed/media and all required
performance/live/selected evidence must qualify source28. The source27 baseline
bytes will be retained; any later source28 baseline establishment is a deliberate
new-cohort decision, not an overwrite or fabricated regression pass. No source28
freeze, test or cloud operation is asserted by this corrective note. Selected,
manual, cleanup and final acceptance remain pending.


Source28 freezes 701 included files after the qualifier convergence correction,
canonical `db934b15a071e4fb43fe401d7c6589b91989a04ae1819a39f4289cad612d91bf`.
Its [new full aggregate](web-platform/full28-aggregate.json) passed all ten stages
with 2,761 exact Python cases/118 bounded groups/global 85% coverage,32 JavaScript,
four npm,three browser and 49 installed checks across ten profiles. The aggregate
SHA is `87510e53d402dcb7209ea5a4375eaf8cd1fa75497023243e950bf389d3879932`;
[installed matrix](web-platform/full28-installed.json) SHA is
`3483666a0adc4eaad4880b983d122ff2108569f6cbb746ef861c668913f4a300`.
Actual JavaScript gzip remained 131,220 bytes. Python 3.11.17 / Node 22.22.2 / npm 11.20.0
were verified. Four newly built installed28 wheel archives are byte-identical
to27, but these new receipts were actually executed for source28; no source27
receipt is rebound.

[The new installed28 PTY journey](web-platform/full28-terminal.json), SHA
`061fe1b49dab97f2ad454eec3b7f5eee500e4dbe2ce05092bd450db3b2305cc7`,
passed actual offline fabricated approval/reply/durable facts/clean exit with
network denied. Functional startup was 3.395985666 seconds and reply 0.270493791 seconds
in one sample; these are not p99 budgets or comparison results.

A first media invocation failed at import mode before any journey. The documented
module invocation then actually passed all three source28 installed journeys.
[Its exact receipt](web-platform/full28-media.json) SHA is
`32e9abc4795bcbec0ec61597755127ee49caeef5ef8a2e78bd669162940ce9f5`.
All three image hashes and 31 declared capture-source hashes were independently
checked. [Root's actual visual review](web-platform/full28-media-review.json),
SHA `c80cf86aa6027dda2bbc92b68cd205e200c46463df846466958016d9d4227b3a`,
confirms the approval view is Connected with literal escaped text and usable
controls. Conversation/mobile show connecting; content and composer are contained
and the session strip scrolls horizontally. Do not infer uninterrupted
connectivity or general WCAG conformance. Runtime versions are Python 3.11.17,
Node 22.22.2 / npm 11.20.0 and Chromium 156.0.8078.4.

[The contextual factory's 13 offline controls](web-platform/qualification-ram/contextual-policy-controls-source28.json)
also passed once on current source28, with production constants restored and no
cloud/secret operations. This is private-operator source/control provenance only,
not a selected-case receipt. Newly prepared draft28 joins list only exact current
observed nodes and keep source27 files as history. Current live two-case, managed two-case and selected seven-case evidence,
fresh independent seven-profile comparisons against the unchanged committed source27
baselines, Root manual support-claim review, cleanup and acceptance remain
pending. Source28 does not automatically re-establish or replace those budgets.


The two live browser/remote approval and held-worker stop/restart cases were
then actually rerun for source28 against the existing deployment38 after fresh
candidate/base/export byte verification. This reused unchanged deployed bytes;
it was not a new deployment or a source27 receipt rebinding.
[The new exact receipt](web-platform/full28-live38.json) SHA-256 is
`f970549b9fae38352b596f8e25f8ee310ddc1fc157c868832da0f329be9f8d41`.
Independent `verify_live` checks accepted the explicit profile binding, all 701
current source hashes before/after, exact two node IDs, locked driver/application
runtimes and structured shutdown/transports. Fourteen private observer booleans
also confirmed exact base/modules/overlay, protected-probe correlation, durable
row visibility after stop, worker start and actual stop/restart. Twenty retained
operator markers and credential/URL/private-path shapes were absent from the
public receipt; private stop-binding IDs/settings/URLs remain private.

Actual SIGTERM report was 12.952073165 seconds and launcher-main return
12.952256932 seconds, within the configured 14 seconds. Shutdown reports
`complete=false`, retaining `client_host`, `platform_resources` and
`postgres_root`; no full drain is claimed. Actual Apps frontend HTTP/2 reached
ASGI 1.1. The separate prior-knowledge H2C probe reached ASGI 2 at the deployed
listener with no claim that the proxy used H2C. Driver runtime was macOS arm64
Python 3.11.17 / Node 22.22.2 / npm 11.20.0 / Chromium 156.0.8078.4; the application
was Linux x86_64 Python 3.11.18 with Hypercorn 0.18.0, SDK 0.158.0, Psycopg 3.3.6
and exact `0.2.0.dev0` candidates. The observer remains explicitly instrumented.

Draft28 now records only these two exact live nodes, covering US-068-AC1, AC5
and AC8 in their stated scopes. Remaining current managed/selected cases,
independent seven-profile comparisons against committed baselines, Root's final
manual review, resource cleanup and acceptance remain pending. An earlier
source27 live result, repeated candidate bytes or completed local gate does not
substitute for those new source28 observations.


The [fresh source28 Apps read observation](web-platform/reference-performance/source28-observations/apps-measurement.json)
completed and was independently schema/source/workload/hash checked, SHA
`e893043a333e7729dffb7ca9ee7da702e96841d0d9eb7d4b807cc35b6277f885`.
It actually measured 100 opens and 100 list requests, stable 50-item pages and
HTTP/2, persistent sequential transport and the same accepted preacquired RAM
credential. Handshake p99 was 414.041959 ms (median 303.580271); list p99 was
724.861125 ms (median 615.133854). Exact current deployment/export and source28
bindings were verified; managed server hardware remains unknown. The reviewed
bounded-EOF operator is unchanged at `a5bf66c9…2ac9e`. This observation is not a
large-catalog/server-CPU/cold-start/SLA claim.

Root's actual shipped seven-profile regression command is now running with
existing committed source27 baselines and fresh source28 inputs. The reviewed
physical owner `075d8688…b579` retains the whole inherited process group and the
actual per-profile command deadlines; it does not establish or replace budgets.
No all-seven comparison pass, source28 target adoption, selected/manual acceptance
or cleanup is claimed before the actual command's terminal result and receipts.
No additional workload, test, build, browser or cloud action was launched by
archiving this light metadata during Root's exclusive window.

### 2026-11-02 continuation checkpoint: Source29 timing-policy remediation

The preceding running-command note is historical. Both actual Source28
seven-profile regression invocations reached terminal failure against unchanged
Source27 caps. Their distinct store failures remain retained; neither is a pass
or a proven diagnosis. The subsequent controlled store diagnostic reached its
readiness cutoff without starting a measurement child. The actual managed and
selected attempt05 stopped at initial managed-peer readiness before lease-fencing
assertions or selected cases; its temporary secret was revoked and absence
verified. These failures and deferral do not qualify a later revision.

The owner then required functional qualification to tolerate slow, occupied
hosts, using state/event coordination and preserving intrinsic temporal
contracts. Source29 is the mutable working revision implementing that policy.
Bounded reviews have accepted the managed-peer preparation/ownership tranche,
performance workload separation and admission controls, and strict runner and
receipt joins (Phases A/B/C). Focused PTY/React, privacy-log capture, identity
clock/barrier, and restart-transport controls have also passed within their
declared scopes. These are focused implementation checks, not a Source29 full
gate or live qualification. Remaining ordinary worker, browser/UI, live Python
cleanup/watchdog and identity-control review is open; the wider timing inventory
remains part of the same remediation.

The owner's later Apps decision makes its two network-latency measurements
observational, outside performance pass/fail. Current controlled performance
qualification requires six local profiles and 54 metrics under independently
observed exclusive admission. Historical seven-profile/56-metric baselines and
Source28 receipts remain unchanged. The known busy Mac is not admitted for a
new controlled measurement. No cloud, performance workload or full qualification
gate is active at this checkpoint. Source29 is not frozen, no acceptance/task
completion is promoted, and manual review, required fresh qualification, closure
and final authorized cleanup remain pending.

Remaining timing review uses the mutable 343-call/107-path Python snapshot
(`2d534d69…71ae`) as a navigation aid, not a removal count or frozen source proof.
It excludes scheduler `sleep(0)` and does not cover implicit browser defaults.
It is not an exhaustive Python census: positional thread/future waits, joins,
request-timeout configuration, aliases and elapsed/clock assertions require a
supplemental source review. Final disposition must include those uses and the
separate TypeScript/framework inventory, rather than infer completion from 343.
The staged allocation is:

| Owner | Cohesive remaining family | Disposition to verify |
| --- | --- | --- |
| A | Ordinary browser/framework, then Textual/reference clients, terminal/feed presentation and recovery | Outer test/expect/server/seed and UI reply/connected limits are operational observations; wait for the visible state while racing the actual UI/send/feed task. Retain assertions about content, approval, escaping, recovery and release. |
| A | Databricks package lifecycle/startup/shutdown | Replace incidental entered/startup guards; retain actual configured stop/report deadlines and physical ownership after an incomplete stop. |
| A | Embedded/Postgres lifetime, capture, handle, indexed reads, resource/process shutdown; execution/storage faults; context/summary/estimator lifetime | Separate event-entry/cleanup guards and release sleeps from configured I/O/context/shutdown deadline behavior. Prepare the held state before exercising a deadline; retain the actual future/process/root until exit. |
| B | Live Python cleanup/observation policy, identity loopback control, then live browser | Retained pipe drainage and reap, typed observation incompleteness, and event-versus-operation terminal remain review prerequisites. Genuine identity/expiry/report proofs remain required. |
| B | Client turns/follow/remote TCP and disclosure; observer/telemetry/provider/retrieval/worker-observer lifetime | Replace incidental entry/reply/cleanup waits with protocol/state observation. Preserve finite request/caller deadlines, bounded capacity, mutation independence from a held sink, and accepted-work ownership. |

Specific retained distinctions matter: Postgres expiry polling sleeps are cadence,
and the process takeover unit test's explicit forced expiry is not natural-expiry
evidence. Managed natural expiry and the browser fixture's real lease expiry stay
real-clock proofs. `client_turns`' configured shutdown refusal is intrinsic even
though its outer entry/idle guards are incidental. Remote stream-header request
deadlines are intrinsic; a 50ms wait used merely to show another request is still
queued should instead observe that queue/held state. Observer delivery/context
deadlines likewise remain behavior under test; surrounding one-second entry and
cleanup guards do not define product speed. A test must distinguish failure to
establish its initial state from the expected deadline outcome. Every barrier
must observe relevant terminating owners, including handoff/completion proxies,
and release held work in cleanup. None of these pending family dispositions
credits an unexecuted case or closes the remediation inventory.

The same review still includes legacy `qualify_web_platform` follower/feed
readiness and restart supervision, selected-example lifespan observations,
managed resource/session/cleanup observation windows, and installed
`web_remote_probe` readiness/cleanup. They are not covered by the newer live
qualifier's focused controls. Configured service I/O limits remain distinct
from those harness observations. Benchmark measurement/deadline code stays in
the explicit performance lane; manual capture-wrapper containment is not a
functional latency target.

The ordinary framework/command-owner tranche has since passed bounded review.
Its checkpoint `100c6269…38a8` separates native owner six-case controls, typed
Python incomplete-reason controls, strict marker controls and a freshly created
11-assertion React/Vitest JSON report. The prior 46-case seed cohort explicitly
lacks launch-time source-hash capture and is not blended into current-source
qualification. Current listed source hashes were independently checked;
additional runner dependencies are recorded separately. No actual Playwright
journey or full gate is inferred. Two interrupted fixture runs retain their
distinct incomplete outcomes and separate voluntary-release/absence receipts;
the later native passes do not relabel them.

The accepted design uses one whole-command emergency owner, with supported
Vitest test/hook timers disabled and unavoidable inner operational limits later
than that owner. Direct POSIX npm owns its group; nested execution explicitly
reuses the outer command owner. Error text does not establish timeout origin.
The performance workflow/runbook tranche also passed focused review, retaining
hidden provisional evidence and importing optional Apps observations outside
the local admission window. Missing or malformed optional input is recorded as
observational refusal without vetoing the six local profiles; inability to
retain evidence remains operationally incomplete. These are implementation and
focused-control results, not an admitted performance execution. Package
lifecycle/startup/shutdown and the remaining TUI/executor/client/observer timing
families remain staged; the live-browser cleanup/readiness tranche is still
under review. Source29 is mutable and all acceptance and final cleanup
prerequisites remain pending.

The subsequent live-browser harness review accepted the 60-case local control
cohort and a separate five-case fixture correction. Driver output drainage now
starts before reap, failed fallback drainage is explicitly incomplete, and the
passing live receipt is published only after retained driver cleanup succeeds.
The fixture correction removes its incidental 30-second drain gate and retains
cleanup across readiness failure. No actual Playwright/live Apps journey was
executed by those controls, and they do not independently prove complete browser
descendant absence or host-loss behavior. Concrete private operator ownership
composition remains a prerequisite before live execution. Legacy selected
qualifier/example lifespan readiness is now the next scoped implementation
family; package native observer corrections and required web-gate wiring of
the native command-owner controls remain open. The existing Source28 ten-stage
aggregate stays historical; adding an explicit current tooling stage must be
joined through the current producer, consumer and test allocation.

The subsequent bounded review accepted the Databricks package lifecycle,
startup and shutdown test tranche: seventeen focused cases, followed by two
observer controls after their final barrier correction. Native signal/report
deadlines and retained physical-worker assertions remain; startup connection
readiness races the actual request against reader, launcher-main and process
terminal state. The finite held cancellation is released before observing its
report. Earlier interrupted, diagnostic and instrumentation-failure attempts
remain separate incomplete observations with their original cleanup records.

The required native command-owner controls are now wired into the current full
web gate as `native-owner-tests`, its eleventh exact stage. Fresh JUnit must
contain exactly the six approved case identities with passing outcomes and a
completed command exit zero. The aggregate attests that one required stage;
it does not expose six individual proof nodes. Sixty focused producer/consumer
controls passed; the separate pinned Node 22.22.2 integrated stage also exited
zero. The final source delta after that invocation adds only allowlisted
failed-case outcome diagnostics. No full eleven-stage gate has yet run, and
historical Source28 ten-stage receipts are unchanged.

The revised selected-example and legacy qualifier tranche also passed bounded
review with forty-seven focused controls. Feed readiness uses the exact fresh
session metadata-update event replayed from its prior cursor. Lifespan and
thread ownership remain retained through cancellation, and unreliable log
capture stays incomplete even when a report marker was present. The legacy
`--restart` path now reports incomplete before stopping the app because this
harness lacks a correlated APP-log attachment witness; this does not waive or
close restart acceptance. Current public selected-profile handling carries the
typed incomplete outcome rather than reporting behavior failure or pass.

The next staged work is the ordinary TUI/reference-client family for A and a
separate client/feed/observer family for B. Managed observation windows,
installed remote-probe readiness, the wider path/use disposition, fresh full
qualification and private operator ownership integration remain open.
Source29 is mutable; no cloud or performance execution, acceptance promotion,
release completion or final cleanup is inferred from these focused checks.

The subsequent six-file client/feed test review accepted ninety-seven focused
cases, with the final annotation-only delta checked statically. Initial local
loopback sandbox refusal and earlier candidate runs retain their separate
outcomes. Readiness now observes the relevant acceptance/job/feed/server
terminal state, and the single-connection test witnesses the actual queued
request instead of inferring it from a short delay. Created read and feed tasks
are settled before stream closure, including abandoned prerequisites. Finite
stream-header, caller and shutdown contracts remain. This bounded acceptance
does not close the remaining observer/telemetry family or the TUI review;
those continue under the existing timing-remediation allocation.

The read-only managed/installed disposition (`a43c18b9…729b2e`) proposes the
following remaining uses for implementation; these rows are not executed
proofs or completion claims. The reviewed preimages are managed parent
`556b55d4…4aba1`, peer `16402024…095b1`, and `web_remote_probe.py`
`f9972128…aec70`. Function names identify the use independently of mutable line
numbers.

| Path and use | Planned disposition |
| --- | --- |
| `selected_profiles_managed_process.py`: `_managed_context` open, `_database_observation`, `_session` outer 20-second wrappers | Harness observation, not API speed acceptance; use state/operation completion and an explicit optional watchdog. Missing mutation acknowledgment does not prove refusal or authorize blind retry. |
| Same parent: `_close_resources` 20-second wrapper and `qualify_managed_processes` whole 240-second wrapper | Cleanup/run observation; exhaustion is typed incomplete and cannot release physical ownership. Fresh private Source29 operator composition must carry that status. |
| Same parent: `_managed_context` SDK Me/authenticate thread calls | Retain physical thread completion through cancellation; keep the original call order and SDK classification. |
| Same parent and `selected_profiles_managed_process_peer.py`: storage `io_seconds=20`, shutdown 5 and lease 30 | Retain declared caller I/O, shutdown and actual lease contracts; separate their constants from harness observation policy. READY remains after resource preparation and before lease activation. |
| Same peer: `_close_resources` outer 20-second wrapper | Harness cleanup observation; retain actual resource closure and truthful incomplete status. |
| Parent `_recovery_poll_pause` and peer `_takeover` quarter-second sleeps | Retain polling cadence while proving actual database state transitions, not elapsed validity or fabricated expiry. |
| `web_remote_probe.py`: `wait_ready` startup 30 seconds, `poll_ready` connect-attempt 0.25 seconds | Harness setup observations; race the real connection with child/reader terminal state. TCP success alone does not identify the exact API instance. Retain the retry cadence. |
| Same installed probe: `installed_remote` spawn, stderr and 10-second communication cleanup | Retain spawn and drain stderr from launch; preserve enclosing job process-group ownership and settle streams/reap across cancellation. Cleanup containment is not functional latency evidence. |
| Installed fabricated server read/shutdown/model request limits and synthetic 20ms timeout control | Distinguish actual caller deadline assertions from setup-only fixture budgets; replace the observation-timer unit control with deterministic state/terminal/cancellation evidence. Wider installed/Node timer review remains open. |

These changes are queued after the active TUI and observer tranches. The
read-only inventory establishes neither live service behavior nor successful
cleanup, and the final path/use reconciliation still includes the original
Python inventory, its positional/clock supplement and TS/implicit framework
uses.

The five-file observer/telemetry tranche subsequently passed bounded review:
133 focused cases followed by one affected caller-cancellation control after
its final fixture-boundary correction, with named static checks. These are
separate observations, not a full current-source gate. Its path/use disposition
is:

| Reviewed test path (current hash prefix) | Replaced coordination and retained semantics |
| --- | --- |
| `tests/test_worker_observers.py` (`dba674f0`) | Thread-release speed caps and caller/entry/close wrappers become owned event/terminal coordination. Sink entry watches delivery ownership; cleanup releases both worker and sink and settles them before root closure. Configured worker/shutdown refusal behavior remains. |
| `tests/test_observer_delivery.py` (`72f5dd0a`) | Entry/close wrappers use delivery state. Held sink release and closing-task settlement start at submission and remain retained on failure/cancellation; actual delivery and shutdown timeout assertions remain. |
| `tests/test_client_telemetry.py` (`9e0ca7c8`) | Client entry/arrival/read/teardown observations use actual operation or delivery ownership. Configured telemetry delivery/context/shutdown contracts and exact identity/privacy assertions remain. |
| `tests/test_provider_observers.py` (`24d7736c`) | Hook entry races the operation and settles its observer; held hook release remains in cleanup. The configured hook timeout is behavior under test. |
| `tests/test_retrieval_observers.py` (`12cff24e`) | Held thread release no longer expires as a fixture speed limit; entry/caller coordination observes terminal ownership and releases physical work in cleanup. Retrieval cancellation/span assertions remain. |

The installed remote probe is the next proposed B scope. TUI review and the
managed observation-policy remainder are still open; no timing-inventory or
acceptance completion is promoted by this observer tranche.

The subsequent TUI/reference-client review accepted a fresh 68-case focused
cohort and named static checks. An isolated pagination control passed
separately. Its corrected final-state predicate follows history `next_start`
in journal coordinates and requires idle state, the exact committed final
sentence and that sentence visibly rendered. The earlier stopped diagnostic
had an impossible first-page-only predicate: the final message was journal
index 276, outside its first 100-slot page. That run remains incomplete
(exit 2, 67 cases completed); its duration is not a product-speed conclusion.
Earlier 67-case candidate and focused aggregate-coverage failures keep their
separate meanings.

| Reviewed TUI path (current hash prefix) | Accepted timing disposition |
| --- | --- |
| `tests/tui_event_waits.py` (`b4a2fc47`) | Disable only omitted Textual screen deadlines under the outer test owner; explicit caller values remain. State observers are retained through cancellation and race relevant operations. |
| `tests/test_tui_streaming.py` (`40639f83`) | Widget updates observe the follower consumer; successful producer completion permits later delivery, while already-terminal producer errors surface. |
| `tests/test_tui_feed_presentation.py` (`f23614a0`) and `tests/test_tui_feed_recovery.py` (`008d2304`) | Feed/reconstruction state races the actual consumer, preserving cursor, gap, replay, cancellation and borrowed-lifetime assertions. |
| `tests/test_reference_clients.py` (`b7c575c5`), `tests/test_remote_terminal.py` (`d35fc6bd`) and `tests/test_local_startup.py` (`cc73cdbf`) | Approval/reply/session rendering uses explicit state and screen-processing witnesses rather than an idle-delay heuristic. |
| `tests/test_tui_terminal_notice.py` (`093bab5c`) | Entry observes its actual reader task; notice/cancellation and bounded public read semantics remain. |
| `tests/test_tui_common_tools.py` (`3137058e`) | Each exact pending tool and visible approval is witnessed; final durable and visible output is paginated before assertions. The configured Bash timeout and all five tool assertions remain. |

The managed harness observation remainder is now A's next design scope, while
B prepares the installed remote probe. The wider executor/storage/context
families, nested installed-runner budgets and final inventory reconciliation
remain open. No full Source29 gate or actual live/performance qualification
has run as part of these focused TUI checks.

The managed observation implementation subsequently passed bounded review at
parent `e50f5bfe`, peer `68183cf2`, new observation helper `f5001dec` and new
controls `6026eed3`; the existing managed controls remain `ec56e34c`. The fresh
combined cohort passed 33 cases with named static checks. Earlier 32-case and
interrupted deterministic-control observations remain separate. This covers
the parent/peer harness uses proposed above: hidden setup/metadata/session/
cleanup 20-second wrappers and whole-run 240-second validity are removed;
default observation follows state completion. An optional validated caller
watchdog spans the journey and maps only its own expiry to inconclusive after
owned cleanup. SDK thread completion and a resource returned after caller
cancellation remain retained; the latter is closed before cancellation is
propagated. Deterministic controls trigger expiry after an observation barrier
and distinguish a later caller cancellation during held cleanup.

The actual storage I/O 20-second, shutdown 5-second and lease 30-second
contracts, two-peer READY preparation, initial `in_progress` witness, natural
expiry/fencing/recovery and polling cadence remain. This is offline harness
implementation evidence, not new selected PostgreSQL qualification. Fresh
private Source29 operator mapping of inconclusive status and retained cleanup
is still required before live execution. The next A proposal is the bounded
executor/storage-fault/common-tool test family; installed-probe corrections
remain in review for B. No full gate, controlled performance, acceptance
completion or final cleanup is promoted here.

The installed remote-probe harness subsequently passed bounded review at
`web_remote_probe.py` (`280020ad`), extracted program payloads (`bdd9a83d`),
capture helper (`df81dabc`), focused controls (`3c5d4f6a`) and the narrow package
CLI status handoff (`3fc71252`). Eighteen fabricated/MockTransport controls and
named static checks passed; no wheel installation or real installed journey
was executed. A per-run response nonce is passed only to the spawned programs,
not in HTTP requests; exact capabilities-response witness validation refuses
a permissive but different API instance. The bearer challenge alone is not
treated as response identity proof.

The probe retains spawn, first client close and capture settlement through
cancellation, drains stderr from launch, and preserves incomplete status after
reader failure even when exclusive fallback cleanup succeeds. If fallback
cannot read, it closes its exact owned pipe transport before awaiting the dead
leader's reap; the control holds that wait until pipe closure. These claims
cover the direct child and owned streams in the enclosing package job's group,
not independently observed descendant absence or host-loss behavior. Missing
capture cannot produce a passed receipt, and the package CLI carries the
closed incomplete reason/phase with exit 2.

The installed command runner still has an implicit nested 180-second default;
explicit standalone versus externally supervised policy plumbing is the next
B design scope before actual package/full-gate execution. A's three-file
executor/storage/Bash candidate remains under review. The adjacent example
database credential-mint thread also needs a scoped cancellation-ownership
control: PostgreSQL admitted I/O shields its operation, while direct provider
cancellation is a distinct boundary. SDK configuration alone proves neither
physical completion nor live qualification.

## 2026-11-09 source reconciliation and bounded test review

The checkout resumed clean after a fast-forward from `b1a7d57` to
`00c7a9be29bab0c0299f692db7e788a92c23fcba` on `origin/main`.
IP-002 remains the sole tracker; its plan SHA-256 at this checkpoint is
`00ed225aee462300751a8ec3bf4ff686448fced6d9fada6b82a8a25631d778c1`.
The [0.3.2 publication receipt](../../03-test/evidence/release-032-publication-20261108.md)
records release commit `236c569`, its named Linux complete gates and public
package installs. Those observations do not qualify the resumed source cohort
or close Source29's installed, browser, selected-profile, performance or manual
acceptance gates. The upstream CI change moves heavy checks to explicit,
scheduled or labeled execution, with an alternative local evidence gate; final
IP-002 reconciliation must verify the required aggregate against current inputs.

The preceding nested-installed-runner pending statement is superseded by source
inspection. `scripts/package_command_owner.py` (`72d7a4fe`) explicitly selects
standalone owned groups or inherited supervision, both without nested command
deadlines. `scripts/check_web_packages.py` (`99f0230f`) supplies the validated
whole-job watchdog or explicit external owner. Root observed a fresh 20-case pass
of `tests/packaging/test_package_pipeline_controls.py` with `--no-cov`, including
policy selection and incomplete-receipt preservation. Its temporary fixture
directory is `/private/tmp/tether-origin-update-package-policy-20261109-01`;
no separate log was saved. These controller tests do not execute an installed
package matrix.

Sol accepted the bounded executor/storage/Bash test family after source review
and Root-observed focused verification:

| Reviewed path / SHA-256 prefix | Preserved behavior and coordination |
| --- | --- |
| `tests/test_execution_review.py` / `6c159bf2` | Entry races actual operation termination; held workers release in cleanup and retain their futures. Worker exclusion, cancellation and unknown-effect assertions remain. |
| `tests/test_storage_faults.py` / `c4a498e2` | Writer entry and close use state witnesses; cleanup releases and settles actual work. Configured storage shutdown refusal, root locking, uncertain writes and recovery assertions remain. |
| `tests/test_common_tools.py` / `17d3409e` | Fresh captured process groups receive cleanup signals before owner settlement, without a signal-zero preflight. Actual reap and strict group-absence proof remain. Permission refusal never establishes absence. Bash's configured timeout remains. |

The current three-file cohort passed 68 cases with `--no-cov` and unique
`20261109-01` isolation. Separate preliminary observations passed two mocked
signal controls and one native cleanup control; those are not additional cases
to add to the 68-case cohort. The native control verifies final process-group
absence. No stale process identifiers were signaled. Ruff check/format and
Pyright for the three files passed; global file-size and ceiling checks against
HEAD also passed. Native execution used the authorized filesystem/process
boundary needed by the fixture, without cloud or secret access.

Evidence provenance is Root's observed tool results: session `88958` ended
zero with 68 passing cases; session `39542` ended zero with two passing controls;
native chunk `c33d5e` ended zero with one passing control. No standalone log or
machine-readable receipt was captured. The corresponding fixture directories
are `/private/tmp/tether-origin-update-executor-storage-tools-20261109-01`,
`/private/tmp/tether-origin-update-bash-controls-20261109-01` and
`/private/tmp/tether-origin-update-bash-native-20261109-01`; these are temporary
test directories, not receipts. This focused acceptance does not close the
timing inventory or any complete Source29 gate, and no task box changes here.

The next bounded telemetry design covers `tests/test_engine_telemetry.py`,
`tests/test_retrieval_durability.py` and `tests/test_telemetry_ports.py` under the
existing WP-01/WP-34 timing correction. Replace incidental result/entry/cleanup
wait limits with actual operation/delivery witnesses and retained cleanup.
Sink entry must observe delivery ownership rather than race an already completed
successful turn. Preserve the configured telemetry delivery/shutdown deadlines,
incomplete close, root reopening and actual observer drainage assertions.
Remove held-thread release/entry limits while retaining actual worker completion.
For concurrent audit encoding, retain two simultaneous event loops, abort the
barrier if a peer terminates before arrival, and join all workers on every exit.
Keep invalid public conformance-timeout validation unchanged. Luna prepares the
implementation and focused controls; Sol reviews the result before acceptance.

The example credential-mint cancellation boundary and installed React consumer
state/cleanup witnesses are separate active candidates awaiting their own proof
and review. No new full qualification, selected live run, performance workload,
publication or final resource cleanup is established by this checkpoint.

The installed React consumer harness subsequently passed bounded source review
at `scripts/web/consumer-view.mjs` (`26bcd82b`) and
`scripts/web/check-packages.mjs` (`419638bb`). The consumer requires the exact
custom layout/message and a real feed-reader demand witness. A zero stream
high-water mark prevents automatic queue filling from satisfying that witness.
Completed React `act` turns drive state observation without an elapsed deadline;
producer and React terminal failures end the wait. Cleanup releases held history
and stream gates before unmount, verifies all opened feed signals aborted, and
retains primary failures alongside cleanup failures. Natural child exit remains
under the enclosing functional command owner.

The package driver now explicitly requires baseline, delayed history/feed and
exact failed-fetch modes before emitting its unchanged receipt identities.
Luna observed all three consumer modes and both `node --check` syntax checks
exit zero on the reviewed bytes, using Node `v22.22.2` at
`/private/tmp/tether-npm-cache/_npx/5705de4b80ce9477/node_modules/node/node_modules/node-bin-darwin-arm64/bin/node`.
From the repository root, the checks selected `scripts/web/consumer-view.mjs`
and `scripts/web/check-packages.mjs`; the runtime commands selected the consumer
with its default baseline and explicit `TETHER_CONSUMER_VIEW_CONTROL=delayed`
and `TETHER_CONSUMER_VIEW_CONTROL=failed-fetch`. No React `act` warnings were
observed. `git diff --check` also passed. No separate log was saved.

These are focused executions against the existing workspace package resolution.
They do not build or install fresh npm archives or qualify an installed matrix.
The earlier direct Node 26 observation remains separate. Source29's complete
functional, installed, browser, selected-profile and acceptance proof stays open;
this harness review changes no task box or process-owner allowlist identity.

The example database credential-mint boundary subsequently passed bounded
review at `examples/databricks-app/src/tether_databricks_example/postgres.py`
(`71296f26`) and `examples/databricks-app/tests/test_postgres_diagnostics.py`
(`2fbaafaf`). Direct provider cancellation now retains the SDK mint task until
its physical thread exits, including repeated caller cancellation. The original
caller cancellation takes precedence over late mint success or failure; canceled
minting cannot proceed to connection acquisition. Existing closed diagnostic
categories and user/infrastructure principal selection remain unchanged.

The controls hold the actual mint thread, witness both cancellations, release
it in cleanup, and synchronously require physical completion before provider
return. Entry and cancellation witnesses race the actual source task; retained
waiter cleanup cannot strand work if an operation fails before entry. A
not-started mint does not replace that primary failure with a false thread-exit
assertion. No fixture progress timeout or extra thread waiting on a thread event
is introduced.

Luna observed 14 passing cases with
`./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-provider-mint-ownership-final4-20261109 examples/databricks-app/tests/test_postgres_diagnostics.py examples/databricks-app/tests/test_reference_example.py`.
Ruff check, Ruff format check, targeted Pyright and `git diff --check` passed
on the reviewed bytes. Root also observed global file-size and ceiling checks
against HEAD pass after the provider and consumer corrections. The temporary test directory is not a receipt; no
standalone pytest log was saved. These fabricated boundary controls perform
no live credential mint or database operation and establish no new SQL profile,
Apps qualification or complete Source29 pass.
Any later selected live run must bind the changed example source and candidate
bundle, redeploy when required by that diff, and qualify those actual bytes;
Source28 observations cannot automatically qualify the changed provider.

The next A scope is the three-file context lifetime family:
`tests/test_agent_context_gates.py`, `tests/test_agent_summary_lifetime.py` and
`tests/test_wire_estimator_lifetime.py`. Root authorized its bounded
implementation after this provider checkpoint. Luna proposes the path/use
changes before editing; Sol reviews them. Preserve genuine context and public
conformance deadlines while arming timeout behavior after the target entry
witness, replace incidental observation limits with retained state/terminal
coordination, and retain actual thread/model/factory ownership. Repeated
cancellation and queued admission require explicit witnesses. No broader
PostgreSQL change or full qualification is allocated to this tranche.

The context lifetime tranche subsequently passed bounded review on these bytes:

| Reviewed path | SHA-256 prefix |
| --- | --- |
| `tests/test_agent_context_gates.py` | `f292c1f7` |
| `tests/test_agent_summary_lifetime.py` | `a5af87b5` |
| `tests/test_wire_estimator_lifetime.py` | `13b02371` |
| `tests/context_lifetime_controls.py` | `22316917` |

Test-local module facades freeze ContextStep's logical preparation clock during
setup without changing the real event loop. Real captured timers are armed after
the target hydration, count, summary or strategy entry. Summary timeout follows
ContextStep's actual guard, not the separate auxiliary-operation path; its
configured duration and expired-timer assertion remain. Final physical admission
advances the logical deadline after the held policy entry and requires refusal
without model egress. Queued admission and repeated cancellation use explicit
witnesses while preserving capacity, primary cancellation, worker exclusion,
factory retention and absence of late compaction assertions.

Cleanup cancels the indefinitely held hydration run when necessary, releases
held work first, and retains actual task/worker settlement. Conformance factory
closure waits run inside retained cleanup tasks. Two new discovered cases in
`tests/test_wire_estimator_lifetime.py` cover primary failure before entry with
observer drainage and repeated cancellation retaining every owned settlement
task. They are included in the focused three-file cohort, not a separate proof
count to add afterward.

Luna observed 37 passing cases with
`./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-context-lifetime-final-20261109 tests/test_agent_context_gates.py tests/test_agent_summary_lifetime.py tests/test_wire_estimator_lifetime.py`.
Ruff check/format, targeted Pyright, file-size checks, ceilings against HEAD and
`git diff --check` passed. The temporary fixture directory is not a receipt.
Earlier 35-case observations remain separate from this corrected 37-case
candidate. These focused clock and lifetime controls do not qualify the complete
Source29 inventory or close its installed, browser, live or performance gates.

The telemetry follow-up subsequently passed bounded review at
`tests/test_engine_telemetry.py` (`d71ec33d`),
`tests/test_retrieval_durability.py` (`f47b9e3d`) and
`tests/test_telemetry_ports.py` (`9dd0497a`). Sink entry follows the actual
delivery task and state changes, including runner termination and drainage
without entry. Completed turns do not stand in for delivery ownership. App
entry and session creation are protected by teardown; one retained cleanup
task releases the sink and settles application, observer and storage ownership.
Configured delivery/shutdown deadlines and root-reopening assertions remain.

The held tracer worker uses state entry and retained physical settlement.
Its new negative control witnesses two cancellations caught by an async shield
wrapper, races acknowledgments against caller termination, and requires both
caller and worker to remain pending before release. The delivery control checks
terminal-before-entry failure. Concurrent audit encoding retains two actual
threads, a witnessed two-party barrier, and abort/join handling for early peer
termination or partial submission. Public invalid-timeout controls are unchanged.

Luna observed 51 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-telemetry-final-20261109-h tests/test_engine_telemetry.py tests/test_retrieval_durability.py tests/test_telemetry_ports.py`.
Ruff check/format, targeted Pyright, ceilings against HEAD and
`git diff --check` passed on those reviewed bytes. Earlier four-case probes,
unchanged timeout-validation probes and intermediate 51-case observations remain
separate. The temporary fixture directory is not an installed-artifact or full
gate receipt. No dependency installation, cloud collector, live qualification,
performance measurement or complete Source29 gate ran in this tranche.

The public resource lifetime tranche subsequently passed bounded review:

| Reviewed path | SHA-256 prefix |
| --- | --- |
| `tests/test_owned_jsonl_shutdown.py` | `4b6548df` |
| `tests/test_public_host_ownership.py` | `2ab16080` |
| `tests/test_postgres_resource_shutdown.py` | `1b2277b8` |
| `tests/resource_lifetime_controls.py` | `0a7c3f4c` |

Entry witnesses race their actual create, turn, refresh, connection or executor
owner. Canceled public `wait_closed` observers do not cancel physical cleanup.
Retained teardown releases held work first, settles admitted tasks, initiates
close when needed and waits for actual drain. JSONL reopen, preserved session
identity, borrowed PostgreSQL pool usability and physical-worker assertions
remain. The borrowed TUI closes while its shared runtime continues accepted
work; its omitted screen deadline follows the outer functional owner.
Configured I/O and shutdown values retain their original meanings.

Two new controls in `tests/test_owned_jsonl_shutdown.py` prove that close failure
still waits for physical drain while preserving the primary exception, and that
cancellation before publishing a close observer retains that observer through a
second acknowledged cancellation until release. They are part of the final
seven-case cohort.

Luna observed seven passing cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-lifetime3-final5-20261109 tests/test_owned_jsonl_shutdown.py tests/test_public_host_ownership.py tests/test_postgres_resource_shutdown.py`.
Ruff check/format, targeted Pyright, global file-size checks, ceilings against
HEAD and `git diff --check` passed. The earlier five-case observation remains
separate. An initial sandbox refusal involved pgserver process inspection and
its runtime lock; the successful focused run used the authorized native fixture
boundary. The temporary directories are not receipts. This proves the selected
local test behavior, not a new deployment, live database profile or full gate.

Shared PostgreSQL fixture acquisition/connect limits, embedded lifetime,
PostgreSQL process/lease/indexed families and the remaining path/use inventory
retain their separate allocations. No task checkbox or Source29 qualification
claim changes through this bounded acceptance.

## 2026-11-09 owner decision: nonblocking performance qualification

The owner stated, “I don't think this gate should be a blocker.” Sol propagated
that decision through PRD NFR-7, CONTRACT-007, TD-068, TP-001, TP-002,
STP-001, IP-002 and the release/runbook disposition. Actual controlled
performance qualification is optional and nonblocking for implementation,
interface acceptance and release. All 41 ACs and 45 task IDs remain required;
WP-36 closes on the isolated workflow plus deterministic admission, rejection,
separation, privacy and cleanup controls, not on actual benchmark PASS.

Optional measurements retain their actual unrun, deferred, incomplete, failed
or qualified status. The six-profile/54-metric scope, known-hardware isolation,
measurement refusal rules and strict referenced qualified-ratchet joins remain.
Apps latency remains observational. Source27 numeric baselines, source28 failures
and deferred observations remain unchanged. Functional/security/lifecycle proof,
genuine caller deadlines and selected live expiry/recovery requirements remain
blocking within their existing scope.

Code alignment is pending a separately assigned bounded Luna tranche after the
active implementation checkpoints. `scripts/web_closure.py` still derives a
mandatory metric union from WP-36 completion. Remove that dependency while
retaining required workflow/control evidence, strict functional receipts and
complete referenced qualified performance sets. The allocated closure tests must
accept workflow-only WP-36 proof with no benchmark nodes and still reject missing
functional proof, malformed admission joins and partial qualified ratchets.
This policy propagation is not a closure-validator pass, measurement run,
checkbox completion or release qualification.

The installed HELIX 0.21.0 validator passed all eight changed governing
artifacts with zero blocking findings. TP-001 retains one pre-existing
`delivery_history` warning, confirmed against HEAD; the other seven had no
findings. Validation used Python 3.11 and cached PyYAML 6.0.3 without installing
a dependency. Frontmatter and existing relative links were preserved. The
structural closure audit passed seven requirement edges, 41 AC rows and 45
primary tasks with no errors; this was inventory-only, not runtime acceptance.
`git diff --check` passed. Historical performance evidence files were untouched.

The compatible-loopback/live-disclosure tranche subsequently passed bounded
review at `tests/test_compatible_loopback.py` (`4ea72db7`),
`tests/test_live_disclosure_public_lifetime.py` (`3cd31154`),
`tests/test_live_disclosure.py` (`a147fbb0`) and
`tests/loopback_lifetime_controls.py` (`43938f1a`). State waits observe actual
turn, follower, reader or peer-handler ownership; failures before entry settle
their observers and held work. Async context entry self-cleans on failure, and
exit preserves the actual primary exception alongside cleanup failures.
Current READ_SESSION, started-attempt, contiguous-offset, sealed-phase and
reconnect assertions remain. The four deliberate cooperative fanout checkpoints
and exact batch/generation assertions are unchanged.

The native model deadline retains its selected one-second duration, armed after
an actual first delta on the held socket, and asserts the real timer expired.
Retry cancellation retains the actual 30-second retry-delay selection without
waiting for that delay to finish. Socket EOF is observed before teardown closes
peer writers. The loopback owner closes listener admission first, drains its
registered operations, writers and handlers, then awaits listener closure.
This ordering addresses the documented newer-Python listener-close semantics;
it is not a claim of an executed Python 3.12+ matrix.

The two new controls use a real local connection: exact handler failure is
surfaced before cleanup, and repeated caller cancellation leaves the real
retainer, handler and writer owned until explicit release. All passing socket
paths witness callback registration before teardown through handler-produced
responses, deltas, request facts, the handler failure or explicit entry. This
proof is bounded to those registered connections. It does not establish closure
of arbitrary pending or future accept callbacks on Python 3.11, and no general
server supervisor qualification is claimed.

Luna observed 36 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-b3-final-20261109-b tests/test_compatible_loopback.py tests/test_live_disclosure_public_lifetime.py tests/test_live_disclosure.py`.
Runtime was Python 3.11.17 with pytest 9.1.1. Ruff check/format, targeted Pyright,
file-size checks, ceilings against HEAD and `git diff --check` passed. Local
socket execution used the authorized fixture boundary after sandbox refusal of
loopback bind. No standalone log or receipt was generated; provenance is the
command and observed stdout in this session. Earlier 34/36-case candidates stay
separate. No dependency installation, full gate, selected live qualification or
measurement workload ran through this bounded acceptance.

The nonblocking performance closure correction subsequently passed bounded
review at `scripts/web_closure.py` (`c3f7dceb`) and
`tests/packaging/test_acceptance_closure.py` (`aa12d36a`). WP-36 completion no
longer creates a mandatory metric-union requirement. All required task/AC proof,
unchecked-task rejection and receipt/source/admission checks remain. Referenced
qualified ratchet nodes still require the exact 54-node union. A source-bound
workflow-control receipt without benchmark nodes is accepted; an individually
valid but incomplete qualified metric set is refused.

Luna observed 24 passing closure tests with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-wp36-closure-20261109-b tests/packaging/test_acceptance_closure.py`.
Ruff check/format, targeted Pyright, file-size and `git diff --check` passed.
The global ceiling check remained nonzero for concurrent PostgreSQL A3 shape
suppressions outside these two files; those require their own correction and
verification, not an exemption. No standalone log, benchmark workload or full
gate was produced. This implements the owner decision's closure behavior; it
does not qualify the current full source cohort or turn optional measurements
into PASS. The earlier 24-case candidate precedes the explicit workflow fixture
and remains a separate observation.

Root subsequently observed both global file-size and ceiling checks against
HEAD exit zero after the concurrent A3 refactoring removed its suppressions.
This resolves that specific shape-check uncertainty while preserving the earlier
nonzero observation. It is not a complete quality gate or A3 acceptance claim.

The residual TUI/replay/public-conformance tranche passed bounded review at
`tests/test_tui_streaming.py` (`a32435cb`),
`tests/test_session_reads.py` (`64f4618f`) and
`tests/test_public_port_conformance.py` (`30366b4e`). The inflight refresh
observes entry against its actual task, removes the list widget while the
request is held, then releases and retains refresh completion before the app
context exits. Existing refresh/session assertions and earlier TUI coverage
remain. Owner-scoped replay directly awaits the committed event and preserves
its exact sequence, session, title and entry assertions. Public conformance
retains every advertised-method/refusal assertion; one cleanup task settles the
reader before closing its stream, including update or reader-creation failure.

Luna observed 24 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-residual-lifetimes-20261109-a tests/test_tui_streaming.py tests/test_session_reads.py tests/test_public_port_conformance.py`.
Ruff check/format, targeted Pyright, global file-size checks, ceilings against
HEAD and `git diff --check` passed. This is focused evidence for the three files;
it does not qualify browser, selected live, installed or complete source gates.
The separate PostgreSQL ownership candidate remains in error-path review.

The remaining path/use audit includes shared fixtures outside the current
three-file allocations. `tests/postgres_support.py` supplies pool acquisition
and connect limits of three seconds and performs migration/root setup before
its yielded-fixture cleanup protection. `tests/conftest.py` describes the
15-second model request limit as a hang bound; its client read/shutdown values
also need classification by actual use. A configured number alone does not
establish a product deadline or justify retaining an incidental test watchdog.
These uses require separate evidence and allocation; no budget increase,
infinity substitution or shared-fixture change is authorized by this record.

The remote-client lifetime tranche passed bounded review at
`tests/client/test_remote_client.py` (`11cf9f6f`) and
`tests/client/remote_lifetime_controls.py` (`08830017`). The two held-turn
journeys race model entry against the exact accepted result observer and reuse
that observer for the result assertions. Cleanup releases held models, cancels
and joins result observers, then closes the remote client. A separate retained
host cleanup attempts both close and physical drain even if close raises.
Canceling an observer does not establish cancellation of an accepted server
turn. Stale-target refusal, exact successor identity, the committed canceled
target result, lost-response retry and exactly two model calls remain asserted.
Configured HTTP timeout/refusal and credential/privacy assertions are unchanged.

Luna observed 17 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-remote-lifetime-20261109-c tests/client/test_remote_client.py`.
Ruff check/format, targeted Pyright, global file-size checks, ceilings against
HEAD and `git diff --check` passed. A transient concurrent version of the test
file exceeded its existing 800-line ceiling; extracting the coherent helper
resolved that finding without changing the ceiling. Root independently observed
both global shape checks exit zero afterward. No standalone log or receipt was
generated. This focused checkpoint does not qualify complete source, native
process, installed, browser or selected live gates.

The PostgreSQL ownership tranche passed bounded review at
`tests/test_postgres_lifetime.py` (`42b4ef59`),
`tests/test_postgres_capture.py` (`7c8e7efe`),
`tests/test_postgres_handle.py` (`51fe3e0f`) and
`tests/postgres_pending_io_controls.py` (`518cf5d9`). Selected I/O durations
remain unchanged: test-local PendingIO captures are installed after setup,
assert the exact selected duration, arm the real timer after the held phase is
observed, and assert actual expiry. Commit uncertainty, read failure, capacity,
namespace, stale prefix and worker-outstanding assertions remain.

Migration admission is observed through the exact granted holder and ungranted
waiter advisory-lock rows, including their backend PIDs and lock identity, on
fresh observer connections. The source publishes its actual task before
acquisition and marks exit in `finally`. Failed unlock closes only the owned
holder connection and still joins both the public caller and physical source.
The negative control keeps the caller's cancellation cleanup held after source
exit, proving that source completion cannot substitute for caller settlement.
Staging ownership is published before the entry wait; its cancellation control
checks unpublished-return paths still release and drain the real worker.

Thread barriers are released before retained cleanup. Physical workers, roots
and exact lease tokens remain owned until settlement; close errors do not skip
the corresponding wait-closed attempt, remaining sibling roots or exact-token
finalization. This does not qualify failures in unrelated shared fixtures.

Luna observed 37 passing cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-pg-ownership3-final7-20261109 tests/test_postgres_lifetime.py tests/test_postgres_capture.py tests/test_postgres_handle.py`.
Ruff check/format, targeted Pyright, global file-size checks, ceilings against
HEAD and scoped `git diff --check` passed. Earlier final6 evidence precedes the
last caller-settlement and continue-all cleanup fixes and does not qualify these
bytes. The fixture runtime and basetemp are temporary directories, not saved
receipts; no standalone log was generated. No complete source gate, other
PostgreSQL family or selected live profile was qualified by this checkpoint.

Root next allocated bounded native-process work to
`tests/test_jsonl_process_effect.py`, `tests/test_context_summary_process.py`
and `tests/test_postgres_processes.py`, their two named JSONL/summary child
companions, and one essential test control helper. The PostgreSQL child and
shared fixtures remain outside the edit allocation. This is planned work,
without a new execution or qualification claim.

The JSONL effect frontier is inside the default 30-second tool deadline; its
five-second model setting also bounds fabricated setup. The summary child's
five-second model setting covers both nested response timers and the held model
stream; its context preparation deadline can also end the intended frontier.
These crash/recovery cases do not assert deadline behavior. Their allocated
test-local controls preserve selected values while capturing unarmed real
timers in only the relevant model/tool modules and freezing only ContextStep's
logical clock. Product timeout behavior and dedicated deadline tests remain.
The parent must retain exact child ownership from spawn through protocol
failure or caller cancellation, drain pipes and reap after the intentional
kill, without incidental read or cleanup speed caps. Existing private process
pipes and session/turn/phase correlation remain the protocol boundary.

PostgreSQL's five-second natural lease and 120-second live-lease premise before
forced SQL expiry remain actual fencing inputs. Expiry is proved by SQL state;
the short polling sleep is a query throttle, not a pass/fail deadline. An
intentionally killed and reaped peer is an expected terminal in that journey.

The next PostgreSQL allocation is limited to
`tests/test_postgres_leases.py`, `tests/test_postgres_indexed_reads.py` and
`tests/test_postgres_changes_effects.py`, plus an essential local helper if
needed. Planned observation changes use exact physical worker, reader, writer
and committed-feed ownership instead of incidental speed caps. Indexed-read
blocking must identify the held reader as the writer's blocker before release.

The lease tests retain their configured 0.2-second server lease and unchanged
database/frame deadline. Their allocated per-instance clock control freezes an
actual pre-expiry server sample before acquisition through the selected hook,
then polls the real server clock past that exact deadline and restores actual
production clock reads. Helper polling does not count as a production clock
read. Final-fence paths must observe a post-hook production read; the renew
path instead reaches the actual SQL expiry predicate, which refuses before any
final clock read. This phase-controlled expiry allocation preserves causal
clock-freshness and rollback assertions without claiming natural acquisition
timing qualification. No row deadline mutation, configured budget increase,
shared fixture edit or execution result is established by this plan.

The lease/indexed-read/filtered-feed tranche subsequently passed bounded review
at `tests/test_postgres_leases.py` (`c147e39c`),
`tests/test_postgres_indexed_reads.py` (`6e1f701c`),
`tests/test_postgres_changes_effects.py` (`cff4e178`) and the shared
`tests/postgres_pending_io_controls.py` (`1bba7f6c`). The lease controls retain
the unchanged server/frame expiry and distinguish helper polling from actual
production clock reads. Hook entry is observed against the real operation;
expiry cannot pass solely because setup ran past the selected phase. The renew
path reaches its unchanged SQL expiry predicate. Snapshot blocking identifies
the exact held reader among the writer's blockers before release. Committed
filtered-feed events are awaited directly, preserving scope, sequence and
original-fact assertions.

Cleanup releases and retains physical workers, settles reader/writer tasks and
continues through root drain and exact-token finalization. A new held-root
control proves an initial bounded close result of `False` is valid: cleanup
remains pending until `wait_closed()` establishes physical completion. An
earlier candidate incorrectly treated that initial result as failure; it was
corrected before acceptance. Actual close exceptions remain visible.

Luna observed 42 passing cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-pg-ownership4-final2-20261109 tests/test_postgres_leases.py tests/test_postgres_indexed_reads.py tests/test_postgres_changes_effects.py`.
Because the cleanup helper also supports the preceding ownership tranche, Luna
separately reran its three files using
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-pg-ownership3-retained-final-20261109 tests/test_postgres_lifetime.py tests/test_postgres_capture.py tests/test_postgres_handle.py`,
with 37 passing cases. Ruff check/format, targeted Pyright, global file-size
checks, ceilings against HEAD and scoped `git diff --check` passed on the final
bytes. These are two focused observations, not a complete source gate. The
earlier 41-case candidate does not qualify the corrected helper. No standalone
log, selected live profile, other PostgreSQL family or shared-fixture correction
is claimed by these results.

Root allocated a separate prepared-pool feasibility proof to two new test files,
`tests/postgres_prepared_pool.py` and
`tests/test_postgres_prepared_pool.py`. Shared fixtures remain unchanged. The
proposal uses one real eight-connection pool per preparation attempt and public
readiness/configure/reconnect-failure APIs before application caller clocks.
Existing three-second SDK readiness/connect attempts are classified as
environment-attempt bounds, not functional latency requirements. No new
reconnect budget, infinity value, private SDK rebinding or automatic retry loop
is allocated.

Pool workers and scheduler must be retained immediately after open, before
public readiness wait can close and clear their registry. A failed or canceled
attempt must physically drain before any new attempt. Retrying `getconn` on the
same timed-out pool is excluded: installed waiting positions can remain queued
until assignment or closure. Planned controls cover separate failed configure
attempts with zero pending requests/workers, eight concurrent real public
borrows, and repeated cancellation of actual close after readiness. This is a
bounded feasibility plan, without shared-fixture integration or execution proof.

The native-process tranche passed bounded review at
`tests/test_jsonl_process_effect.py` (`89cf3923`),
`tests/jsonl_effect_process.py` (`02319abd`),
`tests/test_context_summary_process.py` (`913ee8f8`),
`tests/context_summary_process.py` (`e886e616`),
`tests/test_postgres_processes.py` (`02301ff8`) and
`tests/process_lifetime_controls.py` (`436b209c`). The direct-child owner
retains a spawn result through cancellation before publication, drains stderr,
settles the sole protocol reader before further stdout drain, and joins the
exact child after intentional kill. Buffered protocol events remain valid when
normal child exit is simultaneous. Caller cancellation cannot cancel the owned
native wait or stderr observer. Recovery acquisition registers resources before
later awaits; cleanup continues from bounded close to physical and observer
drain, preserving the primary failure and other owned resources.

Real-child controls cover failure before the first protocol event, canceled
event/wait observers, and two acknowledged caller cancellations while actual
post-EOF cleanup is held. The close caller is registered before observation;
native wait/stderr completion is awaited, not inferred from scheduler order.
The model/tool/context controls preserve their selected numeric values while
removing incidental elapsed guards from these crash frontiers. Dedicated
deadline qualification is separate. Unsafe-effect refusal, safe invocation
reuse and exact intent/result counts, accepted summary/context-use identity,
compaction integrity, PostgreSQL stale-token fencing and committed recovery
barriers remain asserted. Actual PostgreSQL leases of five and 120 seconds and
server SQL expiry remain unchanged; the polling interval is a throttle.

Luna observed eight passing cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-native-process-final-20261109-v2 tests/test_jsonl_process_effect.py tests/test_context_summary_process.py tests/test_postgres_processes.py`.
Runtime was Python 3.11.17, with authorized local temporary PostgreSQL fixture
access. Ruff check/format, targeted Pyright across the six files, ceilings and
`git diff --check` passed. No standalone log was generated. Earlier eight-case
candidates precede the cancellation/cleanup corrections and do not qualify these
bytes. This is not a newer-Python matrix, complete source gate, shared-fixture
integration, selected live or performance qualification.

The prepared-pool feasibility tranche passed bounded review at
`tests/postgres_prepared_pool.py` (`7a6668b8`) and
`tests/test_postgres_prepared_pool.py` (`32362fb4`). One preparation attempt
uses a real pool, unchanged three-second SDK attempt values and default
reconnect settings; there is no retry loop or private SDK rebinding. Worker and
scheduler references are retained before readiness can close the pool.
Configure failures keep their exact primary identity. Cleanup joins producers
before sweeping every still-open configured connection, including connections
skipped by an earlier public close error. Borrower registration lasts through
the complete public connection context exit, including commit and return.

Four real-pool controls cover two separately rejected configure attempts with
zero pending requests/workers, eight distinct concurrent public borrows held
through owner closure, a native close error that still closes remaining owned
connections, and two acknowledged caller cancellations during actual held
connection close. The reconnect-failure callback is wired and raced as a
separate diagnostic; it did not fire in these live-server cases and is not
qualified by their execution.

Luna observed four passing cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-prepared-pool-final5-20261109 tests/test_postgres_prepared_pool.py`.
Ruff check/format, targeted Pyright, global file-size checks, ceilings against
HEAD and checks of whitespace in both new files passed. This qualifies only the
two-file feasibility allocation. Shared fixtures are unchanged, finite SDK
attempts are not functional latency claims, and no supported unbounded public
pool acquisition API or complete source gate is established. Earlier three/four
case candidates precede the final connection-sweep and borrower-return fixes.

Root next allocated embedded lifetime work to the three named embedded parent
tests, a conditional ordinary-startup control in their existing child companion,
and essential test helpers. A raw-line addition to the accepted process owner
must rerun its earlier native cohort. Ordinary startup captures preserve the
configured value; dedicated phase-armed startup and inner-root expiry controls
retain actual deadline contracts. Final cleanup must preserve temporary runtime
directories until all matching physical owners have finished. Cold/warm timing
output is excluded from functional persistence proof.

The orphan allocation permits only the exact fixture's original native owner:
capture process creation identity, executable/arguments and owned datadir PID
correlation before intentional wrapper kill, then revalidate before one scoped
nonwaiting `pg_ctl` stop request. Final original-native absence and PID-file
removal are required before reopen or runtime-directory deletion. Permission
failure, unknown identity or mismatch does not establish absence or authorize a
fallback PID signal. A pre-publication failure without safely established native
ownership must preserve the runtime directory and report incomplete cleanup.
This is planned work without an embedded execution or qualification claim.

Following the prepared-pool checkpoint, root allocated shared PostgreSQL fixture
integration to `tests/postgres_support.py`, its minimal prepared-pool helper and
proof changes, and fixture-lifetime controls. `tests/conftest.py` remains outside
this allocation. The approved design tracks public `getconn`/`putconn` by
individual acquisition identity through complete return, so an old handoff
cannot erase a newer loan of the same connection. Failed return still leaves
the exact native connection owned by the configured-connection registry.

Setup migration and root-open callers are separately retained; a returned root
is stored before its setup task completes. The synchronous application source
registers actual operation-task identity before acquisition, through complete
task exit. Current migration/bootstrap paths enter that source before their
first await. Cleanup must finish setup, root, source and raw-loan ownership
before dropping the exact generated UUID schema, including uncertain migration
outcomes, then continue pool/worker/connection cleanup after independent errors.
No fixture integration result, unbounded public acquisition, timer-budget change
or complete audit closure is established by this plan.

The next exact-use inventory identifies 29 ordinary timeout-bearing public port
suite calls across nine test paths: `test_context_ports.py` (six),
`test_telemetry_ports.py` (five), `test_archive_conformance_profiles.py` (five),
`test_storage_conformance.py` (four), `test_conformance_oracles.py` (three),
`test_mutation_receipts.py` (three), `test_agent_summary_boundaries.py` (one),
`test_postgres_mutations.py` (one) and `test_wire_estimator.py` (one). These assert
logical conformance, refusal, scope and oracle behavior; they do not assert ten/30-second execution
speed. A future public-configuration-only allocation may pass the canonical
functional watchdog maximum explicitly through `timeout_seconds`. Product
suite defaults remain unchanged; no module-clock hooks or new framework is
needed.

Suffix scans also find five `test_suite_runner.py` tool calls and three
`test_consumer_companions.py` calls whose APIs have no timeout parameter or timer;
these are excluded. Current `test_wire_estimator_lifetime.py` has two explicit
0.1/0.05-second suite expiry cases with phase-armed real timers, not an omitted
ordinary suite timeout. Those cases and invalid-bound tests remain unchanged.
This classification does not turn every configured duration into an exemption
or require a zero textual timeout count.

The shared fabricated model fixture's 15-second hang ceiling and the separate
`test_incomplete_restart.py` 15-second override need a later distinct allocation.
Public typed settings can place ordinary fabricated requests under the existing
outer functional owner, following the browser fixture's canonical-maximum
pattern, while explicit request expiry tests remain real. That change also
requires reviewing native summary assertions against actual prepared-request
timeouts and model limits independently of the outer context deadline. Neither
this model-fixture change nor the 29 suite call changes has been
implemented or qualified by the inventory.

The expanded read-only audit also allocates future qualification-observation
work across four paths. In
`examples/databricks-app/tests/test_qualification_shared_session.py`, result
and worker-entry speed caps and pending-state polling are incidental; accepted
terminal results and durable state must provide witnesses, with retained real
worker/host cleanup. The live scenario's bounded physical hold, approval,
unknown-effect, tool-margin and shutdown contracts remain unchanged.

In `scripts/selected_profiles_logs.py`, process/reader cleanup caps of five
seconds must yield to retained physical reap and pipe settlement. APP and
unfiltered log anchors, privacy refusals and capture-byte limits remain.
The CLI subscription's explicit observation boundary can establish only
incomplete observation when required witnesses are absent.

In `scripts/selected_profiles_identity_credentials.py` and
`scripts/selected_profiles_pg_oracle.py`, explicit SDK/observer acquisition
bounds (including 25/18-second attempts) are separate from provider behavior.
Their exhaustion must be typed `INCOMPLETE` after owned cleanup, never inferred
network failure or authentication refusal. Incidental process/reader cleanup,
four-second query and two-second close speed caps are allocated for removal
with physical retention. A successful connection cannot produce qualified
success before its close is confirmed. Only actual PostgreSQL authentication
SQLSTATE 28000/28P01 establishes authentication refusal; driver version, TLS,
hostname/CA, caller identity, secret-source and privacy requirements remain.

Root approved planning two bounded tranches after the active checkpoints:
example/log observation cleanup, then credential/oracle ownership and outcome
classification. Proposals must allocate focused controls and minimal existing
`LiveQualificationIncomplete` wiring, including direct callers. There is no
implementation GO or new execution proof for these paths yet. A later private
Source29 operator must understand the new typed outcomes before actual live
qualification; immutable Source28 operator bytes must remain unchanged.
Terminal PTY emergency ownership, genuine package/event guards and optional
benchmark waits remain separately classified. No live, cloud, secret or
performance action is authorized by this inventory entry.

The fixture integration candidate subsequently passed nine focused prepared-pool
controls, including cancellation followed by an exact late setup failure, using
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-prepared-pool-integration5-20261109 tests/test_postgres_prepared_pool.py`.
Luna also reported one passing migration/history fixture smoke case. These
observations precede complete affected-family acceptance.

Expanded PostgreSQL collection exposed a compatibility regression: the modified
JSONL process parent no longer exported `receive`/`stop`, which
`test_postgres_engine_effect_process.py` imports. Two independent/shared
consumer process parents also import those helpers from the PostgreSQL process
parent. A run excluding only the blocked PostgreSQL engine-effect file passed
221 cases under
`/private/tmp/tether-postgres-family-except-engine-20261109`; that is explicitly
limited evidence, not the full PostgreSQL family. Source inspection additionally
found legacy `EffectTool` constructor calls incompatible with its new mandatory
instrumentation arguments.

Root authorized a bounded compatibility repair: restore the two existing parent
exports backed by one cached accepted process owner per exact process object,
and make instrumentation optional for legacy `EffectTool` callers. No old read
or cleanup speed caps may return, and consumer assertions may not be skipped.
All PostgreSQL cases, both affected consumer files and the accepted native
cohort require fresh execution afterward. The three additional consumers and
their model/tool crash-frontier timing remain a distinct exact-use audit; the
earlier eight-case native proof did not cover them. Compatibility repair and
complete fixture integration remain pending in this record.

The shared PostgreSQL fixture integration subsequently passed bounded review at
`tests/postgres_support.py` (`fc4f1966`),
`tests/postgres_prepared_pool.py` (`b7154308`),
`tests/postgres_fixture_lifetime.py` (`63412868`) and
`tests/test_postgres_prepared_pool.py` (`cfc1519e`). Public acquisition tracking
starts before `getconn` awaits; each exact loan remains owned through checkout
and return. An old return cannot remove a new checkout's identity. Source tasks
are recorded before acquisition and retained through termination. Setup stores
returned root ownership before completion; cancellation followed by a late
setup failure preserves the exact first cancellation and late failure. Root,
source and raw pool loans settle before the exact owned UUID schema is dropped,
and independent cleanup continues through pool, worker and connection closure.

The compatibility repair restored both parent exports through one cached
process owner per exact process object and restored legacy `EffectTool` calls
through optional instrumentation. Controlled cases retain their capture
assertions. No old speed caps or consumer assertion changes were introduced.
After those repairs, Luna observed 238 passing affected cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 ./.venv/bin/python -m pytest --no-cov -q --basetemp=/private/tmp/tether-postgres-consumer-final-20261109 tests/test_postgres_*.py tests/test_consumer_independent_process.py tests/test_consumer_transaction_process.py tests/test_jsonl_process_effect.py tests/test_context_summary_process.py`.
The repaired process helper, exporters and companion had identical hashes
before and after that run. Ruff check/format, targeted Pyright, global file-size
checks, ceilings and diff checks passed for the integration candidate.

This no-exclusion affected-family result supersedes the earlier collection
block and limited 221-case run for integration acceptance, while preserving
both observations. It is not a full repository gate, qualification of all
remaining timing uses, selected live or performance evidence. The separate
three-consumer crash-frontier timing audit remains open. Any subsequent import
sorting must be recorded against its new bytes and scoped affected rerun.

The embedded tranche subsequently passed bounded review at
`tests/embedded_lifetime_controls.py` (`0cec0516`),
`tests/embedded_process.py` (`58db3714`),
`tests/test_embedded_lifetime.py` (`da213090`),
`tests/test_embedded_postgres.py` (`c8050722`) and
`tests/test_embedded_process.py` (`4f97fe50`). Cold/warm persistence waits for
actual physical closure rather than requiring the first bounded close to
succeed. Ordinary startup captures preserve the selected 20-second setting;
the explicit startup and inner-root tests arm actual timers after phase entry
and assert expiry. Shutdown bounds and authority, adoption, capacity,
durability and actual worker assertions remain.

Matching-owner location and closure run in one retained composite. Acquisition
and orphan stop remain owned through utility reap, native observation and
strict original-identity absence with PID-file removal. Datadir uncertainty is
recorded before native acquisition; a missing published PID cannot authorize
deletion or a fallback signal. The unknown-publication control uses a real
harmless wrapper that exits without a PID, proves its pipe/wait completion and
issues no native stop. The actual orphan case validates original creation
identity, executable, arguments and datadir immediately before one nonwaiting
stop request. Runtime directories remain until ownership is resolved; cold/warm
elapsed measurements are absent from functional proof.

Luna observed 25 passing cases on native macOS/Python 3.11 with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-embedded-process-final-20261109-v5 tests/test_embedded_lifetime.py tests/test_embedded_postgres.py tests/test_embedded_process.py`.
Embedded scoped Ruff/Pyright and diff checks passed. The earlier 24-case
candidate precedes the consolidated cleanup and unknown-publication fixes and
does not qualify these bytes. Global Ruff, whole-project Pyright, file-size,
ceilings and diff checks subsequently passed. No standalone log, complete
repository test gate, selected live profile or performance measurement is
claimed by this checkpoint.

Root separately observed 3,741 tests collected with no collection errors using
`pytest --collect-only -q --no-cov`. Collection executed no test cases and is not
a full test gate or source acceptance receipt.

After import sorting, the JSONL exporter hash became `0526228c`; the shared
process helper remained `06e09ddb`, PostgreSQL exporter `c53672d2` and
optional-instrumentation companion `2abe6c72`. Luna observed 22 passing affected
cases with
`XDG_RUNTIME_DIR=/private/tmp/tether-runtime-20261109 UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-native-compat-after-format-20261109 tests/test_jsonl_process_effect.py tests/test_context_summary_process.py tests/test_postgres_processes.py tests/test_postgres_engine_effect_process.py tests/test_consumer_independent_process.py tests/test_consumer_transaction_process.py`.
This verifies the final compatibility/format bytes across all three original
native parents and all three cross-consumers. It does not close their separate
remaining timing-use classification or replace the earlier 238-case affected
fixture proof with a full repository claim.

During the public-configuration tranche, focused reproduction corrected one
planning assumption: the frozen context deadlines retained 30 seconds, while
the actual inner model timers selected the configured model limit. The prepared
requests in this fixture have no per-request timeout; ContextStep's outer
deadline does not rewrite that input into a model clamp. The allocated assertion
must therefore check each layer's exact inputs independently, retaining exact
equality rather than a permissive upper-bound test. This is a corrected test
expectation, not a runtime deadline change or an execution-pass claim.

The public ordinary-guard tranche subsequently passed bounded review in its
12-file allocation. All 29 previously classified ordinary suite calls now pass
the canonical functional watchdog maximum through their existing public
configuration. Suite API defaults remain unchanged. The fabricated model
fixture (`180c8ec5`) and restart fixture (`5085967c`) use that same accepted
maximum instead of an incidental 15-second hang guard, explicitly leaving useful
hang containment to the outer functional owner. Genuine request/suite expiry,
invalid-bound, read/shutdown and performance assertions are unchanged.

The summary process assertion (`248fd300`) independently verifies nonempty
consistent context remaining time equal to its configured 30 seconds, prepared
model requests with no request-specific timeout, and exact selected inner model
timer values derived from those actual inputs and model limits. It does not
invent a context-to-model clamp or relax equality to an upper-bound check.

Luna observed 230 passing cases with
`TMPDIR=/private/tmp/tether-ordinary-guard-final-tmp XDG_CACHE_HOME=/private/tmp/tether-ordinary-guard-final-xdg .venv/bin/pytest --no-cov --basetemp=/private/tmp/tether-ordinary-guard-final-base-20261109 tests/test_incomplete_restart.py tests/test_context_summary_process.py tests/test_context_ports.py tests/test_telemetry_ports.py tests/test_archive_conformance_profiles.py tests/test_storage_conformance.py tests/test_conformance_oracles.py tests/test_mutation_receipts.py tests/test_agent_summary_boundaries.py tests/test_postgres_mutations.py tests/test_wire_estimator.py tests/test_wire_estimator_lifetime.py tests/test_public_port_conformance.py tests/test_compatible_loopback.py tests/test_agent_bridge.py`.
The native fixture boundary used authorized escalation after an earlier sandbox
permission failure. After the final comment and explicit no-request-timeout
assertion saves, the three summary cases passed with
`XDG_CACHE_HOME=/private/tmp/tether-summary-final-xdg .venv/bin/pytest --no-cov --basetemp=/private/tmp/tether-summary-final-base-20261109 tests/test_context_summary_process.py`.
These counts overlap and are not additive. Ruff check/format, file-size,
ceilings and diff checks passed. Repository-wide Pyright reported a concurrent
log-observer task-variance annotation error outside this allocation; its
correction and verification remain separate. No complete repository test gate,
benchmark, selected live or external service qualification is claimed.

Root next allocated three ordinary functional controls in
`tests/test_benchmark_follower_memory.py` and `tests/test_apps_benchmark.py`.
Their benchmark filenames do not exempt incidental entry/result caps or
`sleep(0)` cancellation guesses. Planned changes retain the actual cleanup
functions, use phase-versus-owner races and real shield-catch cancellation
acknowledgments, then release and settle owned tasks before borrowed resources
close. Exact close order, initial host-close `False` followed by physical drain,
borrowed HTTP transport survival and one remote-view close remain asserted.
The held MockTransport does not enforce HTTPX timeout metadata, so its public
request setting need not change. Benchmark source, metrics, sample counts,
ratchets and measurement runs remain outside this allocation. This is a plan
without new execution proof.

Those three ordinary controls subsequently passed bounded review at
`tests/test_benchmark_follower_memory.py` (`13839ac5`) and
`tests/test_apps_benchmark.py` (`ff9fa654`). Each retainer cancellation is
acknowledged by an actual caught cancellation while the physical close phase
remains held. Release and owned settlement run in `finally`, followed by exact
cancellation and close-order assertions. The Apps task settles before its
borrowed HTTP context exits, including an entry failure, and retains its exact
one-view-close assertion. Request settings remain unchanged.

Luna observed 23 passing cases with
`XDG_CACHE_HOME=/private/tmp/tether-bench-controls-final-xdg .venv/bin/pytest --no-cov --basetemp=/private/tmp/tether-bench-controls-final-base-20261109 tests/test_benchmark_follower_memory.py tests/test_apps_benchmark.py`.
The three changed controls also passed an earlier focused invocation; counts
overlap. Scoped Ruff/format and Pyright, global file-size/ceilings and diff
checks passed. No benchmark workload, metric measurement, hardware
qualification, remote request or performance-ratchet change was performed.

Root next allocated the three cross-consumer process parents and their three
companions for a separate crash-frontier timing tranche, with minimal reuse of
the accepted process controls. The PostgreSQL engine-effect pair uses fabricated
model-five/tool-30 guards; independent settlement holds inside its five-second
settlement hook and performs no model call. Shared-transaction participant and
pre-commit holds are inside the 30-second PendingIO caller envelope, whereas
staged and post-commit holds are outside active PendingIO. These exact phases
require scoped test controls rather than a blanket model-timeout change.

The allocation preserves actual PostgreSQL leases, forced/natural SQL expiry,
negative child termination, history/feed/application atomicity, effect counts,
binding identity and recovery assertions. Child ownership must be registered
before config writes; returned recovery resources require retained physical
drain. Compatibility exports and optional instrumentation remain supported.
This plan adds no new protocol or product deadline and does not reuse the
22-case compatibility pass as proof of these new timing controls.

The final timing-use inventory is now a finite allocation, approved by the
orchestrator on 2026-11-09. The 94-line search observation and four numeric
wait/join observations identify candidates, not a requirement that all sleeps,
timeouts or configured bounds become zero. No further broad inventory expansion
is planned before the complete functional gate; a concrete gate failure can
still require a bounded correction. The remaining checklist is planning and
does not claim new execution proof:

- Current cross-consumer crash tranche: the three parent/companion pairs
  allocated above retain exact model/tool/settlement/PendingIO phase controls,
  physical child/recovery ownership and all persisted crash assertions.
- Current qualification-observation tranche 1:
  `examples/databricks-app/tests/test_qualification_shared_session.py`,
  `scripts/selected_profiles_logs.py` and
  `tests/test_selected_profile_log_cleanup.py`. The first 51-case candidate
  precedes requested corrections for process/reader terminal races, partial
  acquisition gate release, retained test finalizers and ordinary public model
  configuration. Acceptance requires fresh proof of those corrected bytes.
  The held fabricated tool records its real 300-second wait argument but waits
  for explicit release; a scoped test control captures the exact selected
  310-second tool deadline. Production hold range, tool margin and shutdown
  settings remain unchanged.
- Reserved qualification-observation tranche 2:
  `scripts/selected_profiles_identity_credentials.py`,
  `scripts/selected_profiles_pg_oracle.py` and their direct controls, including
  `tests/test_selected_profiles.py`'s physical-disable cancellation case.
  Remove incidental process/reader five-second, query four-second and close
  two-second observation caps through retained physical settlement. Preserve
  SDK attempt/convergence bounds and credential expiry semantics; an uncertain
  observation is typed incomplete, never inferred network/auth refusal.
  Preserve exact SQLSTATE authentication refusal and real connection-close
  proof. Replace the disable worker's release-five/entry-two guards and two
  50-millisecond cancellation guesses with phase and actual catch witnesses.
  Related identity/oracle retainer controls in
  `tests/test_identity_credentials_unit_controls.py`,
  `tests/test_pg_oracle_unit_controls.py`,
  `tests/test_selected_profile_pg_oracle.py` and
  `tests/test_selected_profile_identity_restore_controls.py` stay in this
  allocation. A future Source 29 private operator must recognize the typed
  incomplete outcome; the immutable Source 28 operator is not rewritten.
- Example tranche: only `examples/otel/tests/test_profile.py` and
  `examples/embed/tests/test_consumer.py`. Replace the exporter release-five,
  entry-two, receiver join-two and 100-attempt result polling with actual entry,
  public turn/feed result and physical thread/host/profile ownership. Preserve
  real OTLP HTTP/export/flush/request/shutdown behavior, queue-loss and privacy
  assertions. Receiver/profile setup and all cleanup need retained ownership;
  this is functional example proof, not a benchmark.
- Native readiness tranche: `tests/test_databricks_startup.py`,
  `tests/test_qualify_live_apps.py` and essential existing test controls. Replace
  marker 300-attempt polling, spawn-plus-100-millisecond readiness and child
  sleep-20/sleep-60 automatic completion with a private ready handshake and
  exact owned process/group retention. Preserve CLI privacy, actual process
  cancellation and strict group absence. Child acquisition and every protocol
  failure require release/stop/drain/reap in one retained finalizer.
- Residual R1: `tests/test_process_ownership_branches.py` retainer/settle
  cancellation controls; `tests/test_client_cancel_io.py` repeated persistence
  cancellation; `tests/test_observer_delivery.py` close cancellation and
  abandoned-owner GC; and `tests/test_operation_ownership.py` abandoned-child
  GC. Use actual retained-await entry and cancellation catch acknowledgments.
  For GC, prove ownership survives while the actual child/emission is held,
  then release and await the actual child, delivery runner/callback or owner
  finalizer completion before collecting and testing weak-reference release.
  Do not replace ten scheduler turns with another bounded weak-reference poll.
- Residual R2: only the existing cancellation controls in
  `tests/test_live_apps_timing_controls.py` (two),
  `tests/test_benchmark_client_output.py` (two),
  `tests/test_qualify_web_platform_controls.py`,
  `tests/test_live_apps_spawn_controls.py`,
  `tests/test_selected_profiles_example_runtime_controls.py`,
  `tests/test_suite_runner.py` and
  `tests/packaging/test_performance_gate.py`. Replace cancellation-after-yield
  guesses with actual catch witnesses while exercising the real retainer;
  retain release and settlement on every exit. These are ordinary lifecycle
  controls despite some benchmark filenames. Measurement algorithms, metrics,
  thresholds and real cancellation semantics remain unchanged.
- Residual R3, alongside native readiness: the monitor cancellation control in
  `tests/test_qualify_live_apps.py` and cleanup cancellation control in
  `tests/test_live_apps_browser_cleanup_controls.py`. Retain actual monitor,
  child and reader settlement through acknowledged repeated cancellation. The
  latter file's already-failed capture can be awaited for its exact error
  rather than inferred done after a yield.

Deliberate scheduler yields are not elapsed pass/fail budgets. Existing
cancellation-before-entry, executor-queue admission, posted callback, concurrent
context isolation and explicit fanout checkpoints retain their scheduling
purpose where they do not stand in for repeated cancellation handling or
eventual physical completion. The four live-disclosure cooperative fanout
checkpoints remain intentional. Browser pipe-pressure polling checks actual
buffer pressure or child terminal state and has no arbitrary attempt ceiling.
The browser's three-second failure-summary observation remains failure-only,
records truncation and cannot produce a passed receipt; separately retained
process/pipe cleanup owns physical settlement. Its SIGTERM grace before SIGKILL
is an actual escalation policy, not a functional latency assertion.

Genuine lease expiry (including the UI server's lease-plus-one wait), explicit
deadline expiry/refusal tests, SDK prepared-pool admission attempts and typed
live environment observation budgets remain classified contracts. Optional
performance measurement budgets remain separate; ordinary cancellation
controls in benchmark-named files are explicitly included above. Small shared
test observers may be reused only for matching semantics. No production API,
global clock, test count, coverage exemption or benchmark threshold change is
authorized by this checklist. Each tranche needs its own source review,
affected focused execution and static proof before the inventory is frozen
for the complete eleven-stage functional gate.

The cross-consumer crash-frontier tranche subsequently passed bounded source
review at the six allocated files: PostgreSQL engine parent `5ac4e7d4` and
companion `cc905b23`, independent settlement parent `7ef27369` and companion
`116d262d`, and shared-transaction parent `9f608004` and companion `385aeda5`.
Each child is owned before config writes and protocol reads, and recovery
engines are registered for actual worker/observer drain before use. The exact
local model-five/tool-30 checks now derive from their own engine configuration,
independently of the ordinary database fixture's canonical model guard.
Independent settlement captures its real hook-five call, and only the two
in-flight shared-transaction frontiers capture PendingIO-30. The staged and
post-commit frontiers do not invent an active timer. All crash, negative exit,
SQL visibility, fencing/recovery, binding, history/feed and exact effect-count
assertions remain.

Luna observed 61 passing cases with
`TMPDIR=/private/tmp/tether-crashfrontier-final-tmp XDG_CACHE_HOME=/private/tmp/tether-crashfrontier-final-xdg .venv/bin/pytest --no-cov --basetemp=/private/tmp/tether-crashfrontier-final-base-20261109 tests/test_postgres_engine_effect_process.py tests/test_consumer_independent_process.py tests/test_consumer_transaction_process.py tests/test_jsonl_process_effect.py tests/test_owned_jsonl_shutdown.py tests/test_public_host_ownership.py tests/test_postgres_resource_shutdown.py tests/test_postgres_lifetime.py tests/test_postgres_capture.py tests/test_postgres_handle.py`.
The native fixture boundary used authorized escalation. Scoped Ruff/format,
six-file Pyright, ceilings and diff checks passed. At this observation the
global file-size check refused the concurrent log-control draft's 856-line
file, outside this allocation; that draft requires extraction and fresh proof.
No full repository gate or standalone log is claimed, and overlapping native
and fixture tests are not additive evidence counts.

The approved two-example tranche is now allocated for a concrete proposal
before editing. Its public turn/feed terminal witness must retain the original
HTTP result endpoint assertion. Exporter release, receiver thread completion
and host/profile physical drain replace incidental harness observations while
real exporter, flush, request and bounded-shutdown contracts remain asserted.

Qualification-observation tranche 1 subsequently passed bounded source review
at the example (`a96ba77a`), log observer (`bff42705`), direct controls
(`3fa31f5c`) and extracted test helper (`c33a70d4`). Both fabricated journeys
retain actual host/store drain and public canonical ordinary-model
configuration. The held tool asserts the recorded 300-second argument and
selected 310-second timer at actual worker entry, then waits for explicit
release. The log observer races changed events against actual reader/process
terminal tasks, maps producer failure to typed privacy observation incomplete,
and leaves physical stream settlement to retained cleanup. Known privacy
violations preserve their primary identity even if cleanup also fails.

The real-child factory now retains spawn through cancellation before
publication, recovers its exact returned child, releases both gates and drains
unclaimed pipes. Focused controls cover this path, early child termination,
reader termination without a changed-event signal, typed reader failure,
retained repeated cancellation and privacy-error precedence. The helper
extraction resolves the intermediate 856-line draft refusal without raising
the file ceiling.

Luna observed 55 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-telemetry-20261109 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-qualification-observation-final5-20261109 examples/databricks-app/tests/test_qualification_shared_session.py tests/test_selected_profile_log_cleanup.py tests/test_selected_profile_qualifier_controls.py tests/test_selected_profile_identity_controls.py`.
All four changed paths passed scoped Ruff/Pyright; file-size, ceilings and diff
checks passed. Root subsequently observed whole-repository Ruff/format (671
files) and Pyright with zero errors, superseding the earlier concurrent draft
lint/type observations. Layer and dependency checks also passed. These are
focused functional/control and static observations, with no standalone log,
live CLI/authentication, external service or full eleven-stage execution claim.

The reserved identity/credential/oracle tranche 2 is now allocated for its
concrete proposal before editing. It includes the separately identified
physical-disable and direct cancellation controls and minimal typed-incomplete
caller wiring. Existing SDK-classified failures remain distinct from
observation exhaustion; no private Source 28 operator or credential is touched.

On 2026-11-10, both focused candidates reached review. The two example files
(`5c9e2a61`, `59a42301`) passed four cases under the authorized temporary
loopback bind. The initial sandbox run had two bind-permission failures.
The qualifier candidate (`dbe6b079`, `1433c65b` for the two scripts) passed
165 focused cases, including local TLS controls. Both candidates passed their
reported scoped static checks. Source review found remaining ownership gaps.
The ordinary OTLP assertions still require a drained result from the first
bounded close. The blocked exporter finalizer can skip the worker join after
a close failure. The two qualifier observers still directly await their owned
reader, infer observation expiry from any `TimeoutError`, and turn cancellation
during successful-result cleanup into observation incomplete. Fresh owned-group
stop also retains the signal-zero preflight that previously refused on macOS.
The corresponding narrow corrections are active. These focused observations
refer to the reviewed candidate bytes.

The remaining cancellation controls and native readiness proposals are now
allocated in parallel with those corrections. Their scope follows the finite
checklist above. After the focused reviews, freeze the included source inventory
and record its digest and the selected Python, Node, npm and Chromium runtimes.
Run the complete functional aggregate once from that inventory and retain the
installed package work directory. Verify the aggregate and component receipts
against the frozen files before promoting evidence. A subsequent change to an
included file requires fresh qualification of the changed cohort.

Use the same frozen inventory for the temporary development app, managed peers
and selected profile. Compare current wheel, candidate, observer overlay and
deployed export bytes before reusing any existing deployment. Collect the two
instrumented browser and held-worker cases, the two managed process cases and
all seven selected cases with their exact profile bindings. The current private
operator must preserve typed incomplete outcomes and retained cleanup. The
expiry case requires genuinely observed expiry and classified fresh-connection
results. Close the temporary attempt's exact secret lifecycle and verify it
before promoting its selected receipt.

Review the current public captures and support claims, join all 41 acceptance
criteria and 45 tasks to the actual evidence, and reconcile the plan with the
closure audit. Run the release postcheck against the explicitly retained
aggregate and current profile receipt. After qualification, reconcile the
private owned-resource inventory and authorized cleanup against actual app,
identity, credential and database outcomes. Promote the sanitized disposition
with unresolved resources identified as such. Optional performance keeps its
actual recorded status and its own evidence.

Root's read-only live preflight found the selected workspace refresh token
invalid and opened browser authentication. The earlier flat private reference,
settings and cleanup artifacts are absent from the temporary directory. The old
bundle directories are empty. The public operator and observer archives remain
available. Recover exact creation and deployment identifiers from prior
provenance, then verify them through authenticated read-only inventory. A fresh
private record must bind the workspace, account operator, exact service identity
and assignment, exact app and immutable deployment, and dedicated database
resources and dependencies. Historical hashes identify their original bytes.
Missing private files and matching names establish no cleanup ownership.

Root subsequently restored authentication and matched the exact prior app,
application principal, Lakebase project and endpoint, database and test identity
to retained creation evidence and fresh read-only platform records. Existing
validators confirmed the account administrator, active target and sole workspace
assignment. The immutable deployed snapshot byte map matched the historical
receipt (`a1551802`). Root recovered the non-secret app configuration and CA,
prepared fresh private references and observer inputs, and observed 16 passing
offline observer controls. The baseline secret's metadata was recovered from
the prior plan preview. Its value was not read. The app compute read reported
`UPDATING` during provider migration. Deployment reuse for the frozen candidate
and live qualification still depend on current bytes and observed readiness.

The corrected OTLP/embed example files (`39294d54`, `59a42301`) passed source
review and four focused cases. Each HTTP journey waits for the exact accepted
turn's committed event and then reads the original result endpoint. Ordinary
profile reports await actual shutdown before asserting drain. The held exporter
continues to prove the configured bounded outstanding result, with explicit
release and an independent join of its real SDK worker. The finalizer settles
that worker even if another close step fails. Real receiver status, trace/span
correlation, privacy and delivery accounting remain asserted.

Luna observed the four cases pass with
`.venv/bin/pytest -q --no-cov --basetemp=/private/tmp/tether-otel-embed-report2-20261110 examples/otel/tests/test_profile.py examples/embed/tests/test_consumer.py`
under authorized local loopback binding. Scoped Ruff, Pyright, ceilings and
diff checks passed. The simultaneous global size check refused the separate
qualifier oracle draft at 510 lines against its 500-line ceiling. The first
remaining cancellation and GC proposal is now approved for its four listed
test files.

Before freezing the current source, Root fast-forwarded `origin/main` from
`00c7a9b` to `b55c82a2fbb437c1d628c2e1502befce94c52997` and reconciled the
local PRD changes with the upstream 0.3.x consumer decisions. The upstream
Linux and macOS release support paragraph is retained. README's web interface
section now reflects publication of the three optional Python distributions
at 0.3.2, the current development npm version, the accepted consumer defaults
and optional performance measurement. The publication receipt and current
candidate qualification retain their respective source bindings.

Root's further read-only platform checks confirmed PostgreSQL 18, verify-full
TLS, the primary role and database, and the exact observer SELECT and INSERT
grants. Provider metadata showed the exact expired baseline credential absent
and no other credential IDs. The corresponding Keychain metadata remains and
the earlier dedicated config is absent. The private Source 29 runner derives
from the archived operator with typed incomplete outcome handling. A fresh
cleanup draft binds the verified inventory. Cleanup execution still requires
the current exact executor and actual dispositions.

The four remaining process, persistence, observer and worker cancellation
files passed source review at `cddc6abf`, `f9a11d17`, `be44742f` and
`af02c8b0`. Process holds release and settle in retained finalizers.
Persistence cancellation reaches the real shield twice while work remains
held. The delivery test observes the actual emitter and its first cancellation
and re-entry before starting the independent shutdown deadline. The delivery
and worker GC controls drop caller references, prove ownership while the
physical work is held, and await actual completion and drain before collecting
again.

Luna observed 50 passing cases with
`.venv/bin/pytest -q --no-cov --basetemp=/private/tmp/tether-r1-solfix-20261110 tests/test_process_ownership_branches.py tests/test_client_cancel_io.py tests/test_observer_delivery.py tests/test_operation_ownership.py`.
Scoped Ruff, formatting and Pyright passed, as did file size, ceilings and
diff checks. The seven-file cancellation proposal is approved and active.

The corrected credential and PostgreSQL oracle tranche passed source review at
`754bac82`, `5ff9eca8` and protocol helper `43f8dfc1`. The exact reader remains
owned through observation cancellation. The observed timer must report expiry
before its timeout is classified as incomplete. Cancellation during successful
result cleanup propagates after physical settlement. Oracle connection close
also retains its actual task. Only the closed private expiry report and exit
code map to typed incomplete. Actual SDK-classified failures and SQLSTATE
authentication refusals retain their existing meaning. The owned group stop
removes the preliminary signal-zero check and retains strict final absence.

Luna observed 172 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-tranche2 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-tranche2-final-protocol-20261110 tests/test_identity_credentials_unit_controls.py tests/test_pg_oracle_unit_controls.py tests/test_pg_oracle_parent_controls.py tests/test_selected_profile_identity_credentials_controls.py tests/test_selected_profile_identity_restore_controls.py tests/test_selected_profile_pg_oracle.py tests/test_selected_profiles.py::test_cancelled_revocation_waits_for_physical_disable_before_restore`.
The local fixtures used authorized elevation. Ruff, formatting, Pyright,
file-size, ceilings and diff checks passed. The wire-format extraction resolves
the earlier size refusal. The native readiness and cancellation controls are
now active in their approved test-only scope.

Preparing the closure joins reproduced an installed-matrix reader mismatch.
The canonical schema-1 matrix includes exact Python nodes from the independent
installed embedder. The reader treated those nodes as a schema-2 full Python
aggregate and refused the retained Source 28 matrix with
`python_evidence_schema_invalid`. Root corrected that specific reader path.
The validator requires the canonical matrix structure and its nested external
embedder result, strict typing, lock digest and exact ordered Python outcome
join. Standalone Python receipts still use strict schema-2 validation. Root
observed 25 passing focused closure controls and the historical matrix's 49
outcomes accepted against its original source binding.

The private Source 29 join draft now covers seven requirements, 41 criteria
and 45 tasks. Current source paths, citations and Python function names were
checked. Its 12 receipt roles remain unbound until actual current results,
including Root's public media and support-claim review. The draft separates
same-named workspace and installed nodes by their observed receipt role.
No final acceptance manifest or task completion is recorded here.

The native readiness and cancellation files passed review at `1cd34faf`,
`21b94a00` and `338688bf`. The fabricated CLI publishes the exact child PID
through a private FIFO and retains the captured child through reap. The browser
fixture publishes its descendant's readiness and proves that the leader reaped
that descendant, both pipes settled and the owned group was absent. Early
spawn or protocol failures retain that same physical cleanup. Monitor and
resource cancellation controls observe the real shield catches.

Luna observed 48 passing cases with
`UV_CACHE_DIR=/private/tmp/tether-uv-cache-codex-tranche2 uv run --locked --no-sync pytest -q --no-cov --basetemp=/private/tmp/tether-native-r3-final2-20261110 tests/test_databricks_startup.py tests/test_qualify_live_apps.py tests/test_live_apps_browser_cleanup_controls.py`.
Ruff, formatting, Pyright, file-size, ceilings and diff checks passed. This
used local fabricated processes and pipes.

The independent embedder's installed proof exposed a separate import boundary
in the earlier example candidate. Its copied project contains the public
example and explicit collector utilities, so isolated Python could not import
the repository test helper used by the focused workspace run. Root allocated
a small self-contained example teardown and fresh copied-project proof. The
original HTTP result endpoint and exact committed-turn witness remain required.

Pre-freeze guide review updated the three Python install recipes to the exact
published cohort. Core and API installs now pin `0.3.2` together, and the
remote terminal recipe pins the same core version with both required extras.
The existing compatibility and support limits remain in those guides.

The remaining seven-file cancellation tranche passed review and all nine
selected controls with the final shared test observer (`7db1e823`). The
observer records actual shield catches for the intended caller tasks. Held
thread and process cases release and settle actual owners. The two held
benchmark cancellation controls still select their exact three- and two-second
arguments. Their test-only timer capture separates cancellation from expiry.
The genuine timeout control remains armed. The held measurement receipt control
proves write completion before release of its admission lock.

The focused proof used `/private/tmp/tether-r2-finalproof2-20261110` and
passed nine cases. The four-file process, persistence, observer and worker
regression passed 50 cases with `/private/tmp/tether-r1-r2-regression-20261110`.
An independent rerun of the three native files passed 48 cases with the final
shared helper and `/private/tmp/tether-native-final-shared-helper-20261110`.
Scoped Ruff, formatting and Pyright passed, as did ceilings, size and diff
checks. These are overlapping focused observations.

The corrected independent embedder (`0fa98a3d`) uses only its copied project and
public APIs. The local teardown retains the exact feed and actual host drain,
and preserves the primary failure alongside cleanup errors. Its isolated
copied test passed with the workspace interpreter and editable Tether packages,
without the repository's test helper path. This verifies the import correction.
The four-case local example regression also passed with authorized loopback
binding. The installed aggregate will provide the separate wheel-backed proof.

Root started the complete functional aggregate after these reviews. Its required
initial asset build regenerated four tracked paths for the old and new JavaScript
asset, HTML and manifest. The authoritative freeze is the post-build inventory
of 816 files. The saved inventory file SHA-256 is
`3d2b652782a8b5827859fa516d5c1cf75bb80c87fe258b2ade1c10953c8f40f2`.
The canonical sorted, compact JSON digest is
`38696a61d5b36c54c80a14875a044589088dae1ea1b4a542effdd876b5bb2fc2`.
The pre-build map remains a diagnostic. The built JavaScript gzip size is
131,806 bytes. The aggregate's terminal result and component receipts remain
pending. Subsequent continuation evidence writes are outside that inventory.

The first complete-gate attempt passed all 3,761 Python cases in 151 groups.
Coverage reached 87% with branch measurement enabled. The 32 JavaScript cases
and npm package checks passed. Chromium revision 1248 was missing from the
runtime cache, so the browser cases could not start and the aggregate did not
pass. The retained attempt records the toolchain failure.

Root installed the pinned Chromium 156.0.8078.4 into a private Playwright
cache without changing the frozen source. The second attempt verified that
Chromium could launch. Its nested runtime directory exceeded the macOS Unix
socket path limit at the first native telemetry case, stopping that attempt.

The third attempt uses a shorter private runtime directory and the same
post-build inventory. Its Unix socket probe and one focused native preflight
case passed. A separate diagnostic on the pinned browser passed all three
local journeys in 36.4 seconds with exit code 0. That diagnostic confirmed
that the repaired browser environment could run the journeys.

## Source 29 functional aggregate passed

The third attempt passed all 11 required stages on the exact frozen 816-file
map. Python passed all 3,761 cases in 151 bounded groups with 86% coverage,
branch measurement enabled and the existing 85% floor. JavaScript passed 32
cases, npm passed four package checks, Playwright passed three real browser
journeys and all six native owner validations passed. The installed matrix
passed 49 checks across ten profiles. The separate installed PTY journey
passed against the exact installed matrix receipt. The first attempt lacked
the required pinned browser revision and the second used a runtime directory
too long for native Unix sockets on macOS. The successful attempt used the
pinned private browser cache and the shorter runtime directory, with no
change to the frozen source or the gate requirements.

The archive copies are byte-for-byte identical to the retained outputs. Each
contains the exact frozen source map. The aggregate embeds the byte-identical
Python receipt and the PTY receipt names the installed receipt digest. The
local receipt scan found no actual private paths, selected provider hosts or
bearer values. A synthetic file-URL parameter remains in its collected test
identity.

| Receipt | SHA-256 |
| --- | --- |
| [Aggregate](web-platform/full29-aggregate.json) | `dcf5f916f55b9f3a8c16c26d30b86737928481a1ead6b7f56dd629478aa7fc3e` |
| [Python](web-platform/full29-python.json) | `4d9c23677a64d1e34be0e269e69a330ddf7d59099ac73c251f5a3ccb60eff119` |
| [JavaScript](web-platform/full29-javascript.json) | `33e6428aba06f01967812a8447a59a97377c1d7400b566e9f991b00c4bf08408` |
| [npm](web-platform/full29-npm.json) | `c63454a1969b497a6714ae3f6b833bcfb4927529d3985fc851aa86e55b7eb1ee` |
| [Browser](web-platform/full29-browser.json) | `bc70a4e3d77b01680379f7e29387845cdfb1269323ef8af0f8a208ad8db24fb4` |
| [Installed matrix](web-platform/full29-installed.json) | `839ae09f86ec05788cf0f4cecedd77bbaceae44ff380ce727e99fff5170fda3c` |
| [Installed PTY](web-platform/full29-terminal.json) | `01df81c9432ebd51097bfe2f08c0e162f674eb869db4fe9b86be8ed7a7626947` |

The exact four-wheel app overlay was deployed and its immutable source export
matched the prepared overlay. The instrumented live browser, terminal and
stop/restart qualification was still running at this checkpoint.

The installed capture passed all three browser journeys. Its
[archived capture receipt](web-platform/full29-media.json) is identical to the
user-facing receipt and has SHA-256
`0e118e1e21f004e00e8a41d069951de1e4731c1d8022bc2d1fcd3d76d27d814a`.
The 31 capture source hashes and all three published PNG hashes matched.
[Root's visual review](web-platform/full29-media-review.json), SHA-256
`eab7fa735eb11d620f5cd88225236c97f4360eee822ef4c28f43b37118193562`,
observed readable approval actions and literal script-shaped text, the durable
result and response, a connecting indicator at reload, and a clipped longer
neighboring title in the horizontal session rail at 390 pixels. The review
joins to the exact capture and frozen source map. It is limited to the
observed views and viewport. The selected live receipts, independent support
review, final acceptance and cleanup closure remain pending.

### First selected live attempt

The first exact Source 29 app overlay did not pass the selected live journey.
The protected continuation completed its fabricated tool, then returned
`context_unavailable` with `context_failed` before the shutdown stage. In the
observed durable sequence, `context_preparation` arrived 29.570 seconds after
resume, an authorization fact at 32.062 seconds, `x.context.failed` at 35.018
seconds and `turn_ended` at 36.833 seconds. The existing context step starts its
30-second guard before initial capture and reuses that deadline during model
preparation. The sequence is consistent with that guard expiring during hosted
journal and authorization work. These times locate the failure in this
attempt. No live case or latency result is credited.

### Configured live retry

The retry uses the public `RuntimeBindings.client_settings` injection seam
for the explicitly selected fabricated shared-session deployment. The private
factory delegates to the unchanged reference factory, checks the selected
scenario and original model and context configuration, and replaces only
`ContextConfig.timeout_seconds` with the finite test guard
`(2**31 - 1) / 1000` seconds. The live command owner still bounds the observed
journey. The genuine context-expiration tests, shipped reference and product
settings, and all four wheels are unchanged. In the exact bundle environment,
locked sync and an isolated Python check verified the single setting difference
and refusal of other scenarios.

The two base bundle differences were `app.yaml` and the private runtime
factory. The factory SHA-256 is
`d8e6a170a46ca34f7c6444640c059136ebf5190a7e5a4141acbcb2653ef0fe33`,
the retry base digest is
`9b35cc7ed9e181463f4822310371935ab709aae7b28db33377b318d60919b1e9`, and
the instrumented overlay digest is
`d8ffbcc053392c0e045e6569b29cc0c14327c3838c1a35a185d8762259e66fc8`.
The overlay receipt confirms byte equality of its candidate modules with
that base. A successful retry can qualify the exact selected configuration.

The configured retry passed both observed live journeys with exit code 0.
[The exact selected live receipt](web-platform/full29-live.json) has SHA-256
`dad7aacdc947fe8bbea78210cccbeb025f51fbb826429923aae905aecb56eadd`.
It binds the exact frozen 816-file source before and after qualification,
the selected profile and the loaded instrumented overlay. The shared journey
observed the browser and remote TUI with one approval winner. The stop/restart
journey retained the original unknown-effect barrier and verified its durable
post-stop row. The SIGTERM report arrived at 12.9530 seconds and the main
coroutine returned at 12.9532 seconds within the configured 14-second stop
allowance. The report was incomplete by design while the client host,
platform resources and PostgreSQL root remained outstanding. The frontend
used HTTP/2 while ASGI ingress observed HTTP/1.1. Direct listener H2C observed
ASGI HTTP/2 on the bound invocation. This evidence qualifies the explicit
fabricated shared-session settings above. The remaining managed and selected
collector cases were still running when the live receipt was archived.

The first RAM credential attempt stopped in the managed public process
cases with `RuntimeError`. It produced neither the managed nor the selected
collector receipt. The run operator verified revocation of the exact
credential it created. The saved failure records the exception class, so it
does not identify a cause. Root prepared a fresh private diagnostics copy
that preserves the same proof order and revocation path and records only
allowlisted error codes and safe module and line metadata.

The second RAM attempt observed `managed_peer_recovery_missing` after the
natural fencing phase and the exact worker SIGKILL. It produced neither the
managed nor the selected receipt. The operator verified revocation of this
attempt's exact credential. Read-only owner and lease metadata confirmed that
the first killed fixture was already interrupted and had no lease after the
second run, while the second killed fixture remained running with a naturally
expired lease. Both fencing fixtures were idle without leases. The managed
case expects the first nonempty owner recovery batch to name only its current
killed fixture. A prior expired fixture can enter that batch first. Root's
next private attempt will settle only the exact proven prior fabricated
fixture through public owner recovery before running the unchanged cases.

The third attempt's private preflight settled exactly that one expired
fixture through public actor-scoped owner recovery at the observed database
clock cutoff. The returned session and turn matched the prior fixture.
Its projection was interrupted with one unknown effect and no inferred
result. An immediate repeat was empty. The new managed cases use the
unchanged public source and fresh fabricated sessions. This preflight is
prior-fixture isolation evidence. The third attempt's managed and selected
outcomes remain pending at this checkpoint.

The unchanged third-attempt managed cases passed natural expiry fencing and
killed-worker recovery. [The exact managed receipt](web-platform/full29-managed.json)
has SHA-256 `ccd05989d845b34364352e4362b08995cb2584777bf4449e63335b7ef1e0993f`
and binds the frozen Source 29 map and the same selected profile as the live
receipt. The subsequent selected collector failed AC9 with
`deployment_source_binding_mismatch` and produced no collector receipt. The
attempt's exact credential revocation was verified. Root is independently
verifying the exact postrestart active deployment and immutable export before
rerunning all seven collector cases with a fresh RAM-only credential. The
managed pass stands on its own receipt. Partial collector cases are excluded.

The fresh selected-only attempt reached AC3 restoration and ended with
`IdentityObservationIncomplete(identity_workspace_transition_timeout)` in
`fresh_workspace_access`. The operator recorded three contextual retries and
verified exact credential revocation. There is no selected receipt from this
attempt. TP-001's single 90-second deactivation deadline still applies to the
workspace and account inactive readbacks and the captured unexpired-token
refusal. The separate restoration proof requires a fresh credential, the exact
active identity and a protected 200. The current restoration observer ended
before it proved those states. Root is checking actual restoration readbacks
before retrying.

The fresh account readback showed the exact test identity active and its sole
workspace assignment unchanged. The exact filtered workspace readback still
showed the identity inactive. The primary operator's workspace get-by-ID was
forbidden, so the permitted exact filtered list supplies the workspace
observation. All exact attempt credentials were absent in provider metadata.
The split leaves restoration incomplete. Root is waiting for the workspace
state to converge or for authoritative provider diagnosis before a new run.

The exact identity later converged. Account and filtered workspace SCIM both
showed active, account workspace access reported `ACTIVE`, the sole assignment
was unchanged and the prior credential remained absent. The fifth attempt runs
all seven selected cases using the same frozen source and verified deployment.
Only the exact test principal's private restoration observer uses the canonical
1,800-second command budget. It retains one absolute deadline, the same fresh
credential checks, typed retry handling, exact `/Me` and protected 200, and
account-active re-read. The deactivation budget remains 90 seconds. The
fifth attempt is in progress at this checkpoint.

The fifth selected attempt passed all seven exact cases and exited zero.
[The selected-profile receipt](web-platform/full29-selected.json) has SHA-256
`dbb7bab79b53a605149a4e8419ee1b2bba330e3ce7be4c44fa41855a3bba35b3`.
It binds the same frozen source and selected profile as the live and managed
receipts. The identity case observed refusal with the captured token still
unexpired, and verified fresh restored access. The credential case observed
genuine expiry, refusal and acceptance with fresh credentials. The private
operator verified revocation of its exact RAM-only temporary credential and
unchanged operator provenance. The promoted receipt contains no private
paths, selected origin or bearer values.

The expiry observation occurred after both recorded provider expiries. The
expired database credential returned SQLSTATE `28P01`; fresh credentials
were accepted.

### Current acceptance closure and release postcheck

[Root's independent support review](web-platform/full29-support-review.json)
has SHA-256 `fbe789c33bc660726b385947996487a3f3ccb1a6b91054f046a0806a1bc12af5`.
Its ten exact receipt digests match the archived source, installed, selected
and media evidence. The [acceptance manifest](web-platform/acceptance-closure.json),
SHA-256 `4c3d58fd5591d728f693cde1b1ec0d55229feff828a79092c14837a54aaab3be`,
joins all 41 criteria and 42 ordinary work tasks to current passing receipts.
The exact frozen 816-file map remained unchanged during resolution.

The standalone closure audit and the release postcheck each exited zero and
reported seven requirements, 41 criteria, 45 task records and an empty error
list. [The audit](web-platform/full29-closure-audit.json) and
[postcheck](web-platform/full29-release-postcheck.json) outputs are byte-identical,
with SHA-256 `085f1051425a6f760a20576c02809b439fc31c3813b506439bc9fc0e7237d787`.
The postcheck reverified the exact aggregate, the current live profile and
its allocated carrier proof, and the closure manifest. The 42 ordinary task
boxes, WP-39, WP-44 and the seven validation checks now reflect the observed
results. [The candidate packet](web-platform-release-candidate.md) records the
qualified scope and the actual unrun optional measurements. WP-40 remains
pending the authorized exact cleanup and its provider readbacks.

### Exact selected-resource cleanup completed

The read-only provider snapshot observed 40 exact resources and three owned
upload roots. The reviewed plan bound ten actions to that snapshot, the
qualified source and the exact provider targets. Its SHA-256 is
`0420b2175230c9fa9b7f7d513a2ebc9f76a41b6caff74560f6fa2f58e9267ca5`.
The executor stopped and deleted the selected app, verified its 20 deployment
rows absent and removed the three exact upload roots. An initial workspace
readback surfaced the SDK's typed `ResourceDoesNotExist` subclass. The
private adapter was corrected for that exact typed response and reconciled
the already-absent root without resubmitting its delete. The PostgreSQL
cleanup initially refused an identity mismatch before mutation. The reviewed
correction bound the TLS idle probe to the exact authenticated cleanup
operator. After verified app absence and a fresh idle read, the executor
purged the dedicated PostgreSQL 18 project and verified the project and its
branch, endpoint and database absent. The prior PostgreSQL 17 project was
verified absent without mutation.

The exact account service-principal delete returned success while SCIM still
reported the identity present. The executor stopped with the action retained
in the durable receipt. Fresh SCIM item and application-ID filter readbacks
later converged to absence, consistent with the independent account and
workspace observations. The executor resumed the same plan without a second
SCIM delete and completed all ten actions with exit code zero. The exact
execution receipt SHA-256 is
`3858b948d0c25d48306cc9f7caa77eed2a7072cf2682be79f08dab9c3f19384d`.

[The independent cleanup disposition](web-platform/full29-cleanup-disposition.json),
SHA-256 `ca0406a2af403154b7548038d346b0ca30c0a34b6a76eda34ea68b8a82f72032`,
verifies the managed app principal absent and 39 other exact absences. These
include the app, deployment rows, upload roots, database topology, test
identity and workspace assignment, six provider credentials, the exact
Keychain item and dedicated local configuration. The public receipt binds
the private plan and execution receipt by digest. WP-40 and all 45 work
tasks and seven validation checks are complete for the recorded candidate.
The final frozen 816-file map is unchanged.
