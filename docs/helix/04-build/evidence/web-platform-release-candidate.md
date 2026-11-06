# IP-002 release candidate disposition

This working tree implements an unreleased interface cohort. It has not been
published to package registries, tagged, or merged by this task. Existing public
0.1.0 and 0.2.0 core artifacts remain separate releases. The candidate Python
cohort is `0.2.0.dev0`; the npm cohort is `0.2.0-dev.0`. Internal candidate
dependencies require that exact prerelease cohort. The hash-verified published
0.2.0 core exposes an older client surface and does not qualify the candidate
public host contract. Candidate metadata does not establish registry availability
or operator ownership of a public registry name.
Versions are explicit in the candidate manifests. Before a future publication,
update the Python and npm cohort versions, internal exact dependency constraints and
locks together, then rebuild and requalify that cohort. A Git tag alone no longer
derives the Python version. Keep the historical 0.1.0 release procedure scoped to
its original artifacts.

The acceptance authority is [IP-002](../web-platform-implementation-plan.md), its
41 story criteria, and the machine closure audit. The
[continuation](web-platform-continuation.md) records open gates. A successful
focused test or wheel build does not close the aggregate release gate.

## Artifact cohort

| Artifact | Consumer contract | Build/qualification owner |
|---|---|---|
| Core wheel/sdist | `tether.client`, optional explicit children | Python artifact matrix |
| HTTP API wheel/sdist | `tether_web_api`, canonical HTTP v1 schema | API contract and installed matrix |
| Static UI wheel/sdist | `tether_web_ui`, digested assets and explicit discovery | Static adapter and installed browser matrix |
| Databricks carrier wheel/sdist | `tether_databricks`, injected factories/resources | Carrier and live selected profile |
| Three npm tarballs | `@tether/client`, `@tether/react`, `@tether/ui` | Reproducible pack and clean typed consumer |
| Reference browser assets | Exact generated wire schema and scoped base path | Vite build, asset budget and Chromium journeys |

The build scripts generate digests from actual artifacts; this document does not
substitute hand-entered digests for those receipts. Wheel and sdist license,
NOTICE, namespace and `py.typed` checks run against installed artifact contents.
Clean profiles test required optional dependencies and the absence of frameworks,
SDKs, and higher interface packages in the core closure. npm tarballs include
license files and declarations and are consumed outside the workspace.

The reference Apps bundle builder binds each of the four candidate dependencies
to its freshly built local wheel through `tool.uv.sources`, then records the
archive hash in its lock. Its generated requirement names do not themselves
contain version pins. That exact local artifact binding is distinct from the
published-package solver controls: registry consumers must satisfy the candidate
wheel metadata's exact internal cohort constraints. Neither mechanism establishes
compatibility with an untested registry release.

## Compatibility and migration

The selected Python qualification runtime is 3.11 with the locked dependency
cohort. The selected browser gate uses Node 22.22.2, React 19.3.1 and the installed
Chromium build recorded by its runner. The current source27 aggregate and installed
matrix are retained below; pending selected/manual/cleanup gates remain distinct.
Earlier Node 22.20.0 runs are diagnostic history in the continuation record.
Exact lockfiles govern framework and server versions;
other versions require independent evidence. Public client consumers should
migrate private imports to `tether.client` and documented optional children.
Applications own authentication, routing, lifecycle, resource provisioning and
explicit capacity settings. The web API, UI, React hooks and visual components
are independently replaceable within their documented protocols.

Managed Lakebase qualification is a separately named PostgreSQL 18 profile with
explicit endpoint attestation and transaction checks. Native PostgreSQL and
local JSONL evidence cannot establish its behavior. Caller identity must remain
separate from infrastructure authority. A successful administrator request is
insufficient proof of owner isolation.

## Deployment, rollback and resource disposition

The only authorized live deployment is a temporary development qualification
app with its dedicated temporary database resources and test identity. Exact
names, identifiers, credentials and environment bindings remain in the private
operator inventory. The [runbook](../../05-deploy/web-platform-runbook.md)
covers readiness, accepted-work recovery, bounded shutdown, rollback and cleanup.

Retain the authorized temporary cohort until live isolation/restart checks and
its sanitized evidence are complete. Then stop the app, revoke temporary caller
permissions and credentials, and remove only resources created for this
qualification after checking dependencies. Record actual cleanup outcomes in the
private inventory and a resource-free public disposition. Do not imply cleanup
has occurred before checking it.


## Current source27 candidate readiness — observations, not release authorization

The frozen cohort is 700 included files, canonical source SHA-256
`4eb2d3e8f38e08604eb617ec6de70b50c48838be00aafc1c7aba1238e923c986`.
The Python candidates remain `0.2.0.dev0` and npm candidates `0.2.0-dev.0`.
Nothing in this packet publishes, tags, merges or qualifies a production rollout.
The candidate is reviewed as a development qualification cohort only.

| Current evidence | Binding and observed scope |
|---|---|
| [Full27 aggregate](web-platform/full27-aggregate.json) | SHA `b630d80f4a9a24d4e4d75ce7ad54b98b4c6397a72a34a4819fe43c67cd53a860`; ten deterministic stages, 2745 Python cases/117 bounded groups/global85% coverage, JS32/npm4/browser3. No CI-run claim is inferred from this local execution. |
| [Installed matrix27](web-platform/full27-installed.json) | SHA `9c48ad2c1a506b958a30d57f2ffc381e8d0eb172a3770523be11e8eb24a2b0e3`; 49 checks/ten installed profiles, wheel/sdist reproducibility, licenses/notices, isolated closures and independent fabricated embedder. SBOM data are retained in the actual matrix/npm receipts. |
| [npm packages27](web-platform/full27-npm.json) | SHA `b5b0492e0d9ddaa6ca5914750a40824ded4355db380111989df6b040a5639f2b`; actual pack/license/declaration/reproducibility and external consumer checks. This receipt is the proving layer; this packet does not invent unavailable retained raw-tarball hashes or claim registry publication. |
| [Installed PTY27](web-platform/full27-terminal.json) | SHA `1711b66740dab44bcd0eb29e6439abdb4f01b3f76464be53a8b03d6057cf7ff5`; offline fabricated approval/reply/durable facts/clean exit. One functional startup sample is not a p99 budget. |
| [Media27](web-platform/full27-media.json) and [Root visual review](web-platform/full27-media-review.json) | Receipt SHA `63f2da027b4036a2e84e6f99d7f4ab7235c2caed5f8a22cf54fd4b1ab8f6c9c5`; Root actually viewed three current captures. Connecting shown in conversation/mobile does not prove continuous connectivity. |
| [Seven-profile measurement/baseline joins](web-platform/performance/first-baseline-joins27.json) | Actual seven observations and56 selected metric first-baselines, uniform observed×1.2 future-variance policy. Original misses/replaced operational definitions remain in PRD/TP002/[accepted review](web-platform/performance-budget-review27-draft.md). First establishment is not a historical regression pass, old stricter-cap enforcement or SLA. |
| [Live browser/stop27](web-platform/full27-live38.json) | SHA `54d5dae949952dc7405d15209dc2de4260a73e189714f806546b6a0d03e4c434`; exact two source27/deploy38 browser/remote approval and held-worker stop/durable restart cases. Observer is instrumented with byte-identical candidate modules. Main returned12.952144456s within14s with complete=false and retained client_host/platform_resources/postgres_root; no full-drain claim. Frontend HTTP/2→ASGI1.1 is separate from deployed-listener H2C/ASGI2 capability. |
| [Managed task cases27](web-platform/full27-managed38-firstactual.json) | SHA `d35e429f3135cf87846369c41ba140c9c8748ad7bafc2384f19fad1711e2c371`; real separate-process natural lease takeover/fencing and killed-worker unknown-effect recovery on selected PostgreSQL18.6/TLS/nonadmin role. Public source composition only, no installed/deployed execution or managed-database HA/region/power-loss claim. |
| Selected7 / final manual / cleanup | Pending their own complete actual receipts and final Root review. Earlier collector failures and warmup/log-readiness preparation are not selected-case passes. |

The current installed raw wheel archive hashes are exact artifact identifiers,
not registry coordinates or a promise that a new build under changed inputs
would have the same bytes:

| Python wheel | SHA-256 |
|---|---|
| `novirhiza_tether-0.2.0.dev0-py3-none-any.whl` | `826ca908bb7d89702f6d744ec73bc6c6c5576907030f038c4722aa19abc7582e` |
| `novirhiza_tether_web_api-0.2.0.dev0-py3-none-any.whl` | `cbdaa4b39d87ff3e0200eeccf478b0ade28a2557743ec7b40b49a45f98b501bf` |
| `novirhiza_tether_web_ui-0.2.0.dev0-py3-none-any.whl` | `3166f29de2c73aa69ee990cf562a9a6e571726e957e1573059828efc479885ae` |
| `novirhiza_tether_databricks-0.2.0.dev0-py3-none-any.whl` | `3095bf5c107a300625e71190860610d6f71684c28e1a870c2a4834e2655ea35a` |

The installed matrix records the four corresponding source archives, normalized
content comparisons and raw first/second digests; it is the complete artifact
reference. Candidate runtime qualification is Python3.11 with locked packages,
Node22.22.2/npm11.20.0 and Chromium156.0.8078.4. The live driver used macOS arm64
Python3.11.17; the selected application used Linux x86_64 Python3.11.18,
Hypercorn0.18.0, Databricks SDK0.158.0 and the exact development candidates.
API application protocol version1 is distinct from network HTTP versions.
Untested versions, managed environments and broader model/transport subsets
remain outside the claims.

### Remaining final closure steps

Retain the complete selected seven-case receipt only after all original cases
pass in one source/profile-bound collector execution and the exact temporary
secret revocation is verified. A returned early case, prepared APP tail,
failed collector or successful managed task does not substitute for this.
Root then performs the actual final US-069-AC8 support-claim review, joins the
41 criteria and45 task records to exact current nodes/receipts, and reconciles
completed work-task checkboxes against real evidence. Control tasks WP39/40/44
remain explicitly reviewed controls rather than circular self-proving inputs.
The pending draft is not the final `acceptance-closure.json`.

After Root records those final facts, refresh governing-document hashes in the
baseline join manifest (especially the implementation plan after checkbox edits)
and final packet bindings. Preserve all actual measurement, baseline and receipt
bytes. Do not rerun expensive unchanged workloads just for excluded metadata edits.
The existing postcheck verifies the current full aggregate, all profile-bound
carrier criteria and the exact acceptance audit without rebuilding the cohort.
These commands are prepared instructions only, not an executed final pass:

```sh
# Run from the frozen project root with its qualified interpreter.
.venv/bin/python scripts/audit_web_closure.py > "$FRESH_CLOSURE_AUDIT"
.venv/bin/python scripts/web_release_postcheck.py \
  --aggregate "$IMMUTABLE_FULL27_AGGREGATE" \
  --profile "$SELECTED_PROFILE" \
  --live-receipt docs/helix/04-build/evidence/web-platform/full27-live38.json \
  --app-origin "$SELECTED_APP_ORIGIN" > "$FRESH_FINAL_POSTCHECK"
```

Operator values remain private; origin/profile placeholders are not credentials
or assertions of authority. The direct postcheck takes the immutable aggregate
explicitly, avoiding reliance on a mutable temporary alias. A failed audit or
postcheck is a refusal, never a release pass. Resource cleanup still requires
its separately authorized exact-owned-resource proof and disposition. Publication,
registry signing/channel control, a public release tag and production rollout
remain separately authorized future actions, not new prerequisites invented by
this packet and not actions executed by preparing it.

## Release decision

**Hold while required gates remain open.** Final disposition requires the full
core quality floor, browser and installed package matrices, current exact
acceptance receipts, measured interface budgets, selected live profile evidence,
independent review, and a passing closure audit. Registry publication, a public
release tag and production rollout are separate actions requiring their own
release decision. Audience-facing release notes should be authored for that
actual shipped rollout, with the resulting artifact manifest and limits.
