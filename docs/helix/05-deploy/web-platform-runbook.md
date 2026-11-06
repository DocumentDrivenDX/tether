---
ddx:
  id: RUN-002
  type: runbook
  activity: deploy
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: IP-002
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: SD-003
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: CONTRACT-007
      kind: informed_by
    - id: TP-002
      kind: informed_by
---

# Tether web platform runbook

## Service Summary

Tether's optional HTTP and static UI distributions share an injected client
provider. The Databricks carrier composes both applications with explicit resource
and lifecycle owners. The deployment operator supplies resource permissions,
caller authentication and external PostgreSQL acquisition. The current execution
record is [IP-002 continuation](../04-build/evidence/web-platform-continuation.md).
This runbook covers fabricated development qualification and the matching
operator-controlled deployment profile. It does not authorize production changes.

A failed transport can leave an accepted turn running. A worker deadline can leave
an external effect unknown. Authoritative journal facts and current authorized
history decide recovery; an HTTP failure or disconnected UI does not decide it.

## Operator Entry Points

Use the selected development profile and app name from the private deployment
inventory. Keep endpoint identities, user identities and credentials out of public
receipts. Commands below use operator-supplied variables, never token arguments.
Their argument syntax was checked against Databricks CLI 1.21.0. A control-plane
mutation can be accepted before its blocking CLI wait times out: prefer a named
`apps stop "$APP" --no-wait` followed by bounded state polling. Check the actual
state before retrying an ambiguous mutation.

| Situation | First check | Decision |
|---|---|---|
| App unavailable | `databricks apps get "$APP" --profile "$PROFILE" --output json` | Inspect compute and deployment states separately. |
| Startup or shutdown error | `databricks apps logs "$APP" --tail-lines 100 --profile "$PROFILE"` | Retain a bounded local diagnostic capture; redact before sharing. |
| UI reports disconnected | Authenticated snapshot, then follow from its exact watermark | Preserve accepted commands and discard provisional reply text. |
| Turn acceptance response lost | Resolve its original request ID and journal turn result | Retry with the original request ID only; do not invent another operation. |
| Missing assets | Manifest readiness and UI wheel digest | Rebuild the matching asset cohort; do not serve an arbitrary directory. |
| Database refusal | Named profile, schema revision, TLS and current role | Refuse readiness; do not create roles or migrate during startup. |

### Qualification and receipt verification

Freeze product source, generated assets and public-guide fingerprints before
collecting final evidence. Use the candidate's qualified Python 3.11,
Node 22.22.2 and npm 11.20.0 toolchain. A diagnostic run while source changes
cannot establish the final cohort's acceptance.

The browser/TUI and held-worker driver performs the authorized live operations,
including one app stop/restart. Supply the primary caller profile and the separate
control-plane operator profile explicitly, and choose a fresh private output:

```bash
uv run --locked python scripts/qualify_live_apps.py \
  --profile "$PROFILE" --admin-profile "$ADMIN_PROFILE" \
  --output "$LIVE_RECEIPT"
```

The driver reads the selected non-secret app/database settings from the explicit
qualification configuration. Add `--admin-config-file "$ADMIN_CONFIG"` when the
operator profile uses a separate CLI configuration. Add `--service-caller` only
for an explicitly selected service-caller primary profile. The shared-session
qualification model and finite unsafe-tool hold must be enabled in that disposable
app; do not run this scenario against a customer application.

The selected shutdown qualification uses a separately reviewed private observer
harness because Apps logs are not retained after compute shuts down. The generic
log-only command above does not provide the required durable observer provenance.
Use the reviewed [generic observer source](../04-build/evidence/web-platform/observer/README.md)
and [raw-byte hash manifest](../04-build/evidence/web-platform/observer/source-hashes.json).
Retain generic code plus sanitized receipts; private settings, substituted SQL,
resource inventories and run bindings stay private. Reconstruct the expected
module layout in a fresh private directory before using its driver:

```bash
OBSERVER_ARCHIVE="$PWD/docs/helix/04-build/evidence/web-platform/observer"
.venv/bin/python - "$OBSERVER_ARCHIVE" "$OBSERVER_WORKDIR" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path
archive, target = map(Path, sys.argv[1:])
files = {
    "prepare_overlay.py.txt": "prepare_overlay.py",
    "run_instrumented_qualification.py.txt": "run_instrumented_qualification.py",
    "observer_launcher.py.txt": "src/observer_launcher.py",
    "observer_metadata.py.txt": "src/observer_metadata.py",
    "read_observer.py.txt": "src/read_observer.py",
    "tether_h2c_probe.py.txt": "src/tether_h2c_probe.py",
    "test_contract.py.txt": "tests/test_contract.py",
    "test_failure_diagnostics.py.txt": "tests/test_failure_diagnostics.py",
    "shutdown_observer.sql.txt": "operator/shutdown_observer.sql",
}
expected = json.loads((archive / "source-hashes.json").read_text())["files"]
assert set(expected) == set(files) | {"README.md"}
for name, digest in expected.items():
    assert hashlib.sha256((archive / name).read_bytes()).hexdigest() == digest
assert not target.exists()
target.mkdir(mode=0o700, parents=True)
for name, relative in files.items():
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive / name, destination)
PY
OBSERVER_DRIVER="$OBSERVER_WORKDIR/run_instrumented_qualification.py"
```

Use that exact copied driver and its recorded hash explicitly:

```bash
.venv/bin/python "$OBSERVER_DRIVER" \
  --project-root "$PWD" --mode instrumented-qualification-only \
  --profile "$PROFILE" --admin-profile "$ADMIN_PROFILE" \
  --settings-file "$QUALIFICATION_SETTINGS" \
  --base-bundle "$BASE_BUNDLE" --overlay-bundle "$INSTRUMENTED_BUNDLE" \
  --deployment-reference "$DEPLOYMENT_REFERENCE" \
  --base-candidate-sha256 "$BASE_CANDIDATE_SHA256" \
  --diagnostic-schema "$OBSERVER_SCHEMA" --output-dir "$OBSERVER_OUTPUT"
```

Add `--config-file "$ADMIN_CONFIG"` or `--service-caller` only for the explicitly
selected configuration. The operator provisions the isolated observer table and
minimum grants before this run; the app performs no startup DDL. Preserve the
candidate launcher, carrier and wheel bytes. Only the reviewed observer entrypoint
and diagnostic environment overlay may differ, with exact immutable deployment
snapshot verification. Keep base-candidate and full-overlay digests separate.

The harness calls the shipped browser/TUI qualifier, correlates its real worker
with a same-process deployed-listener H2C exchange and an independent protected
public-ingress response, then observes the actual platform stop. It must read the
committed metadata row through a fresh post-stop connection. The original report
and actual launcher-main return must both meet the configured sub-15-second bound;
a later insert cannot extend that allowance. A missing row is not a pass.
Outstanding workers remain explicitly outstanding. Retain reviewed observer/probe
source, base/overlay inventories and stop correlation privately; promote only the
sanitized receipt with `instrumented-qualification-only` provenance. Set
`LIVE_RECEIPT` to that validated two-case receipt for the following collector.
This qualifies the declared instrumented profile, not an uninstrumented deployment.

The optional generic `qualify_web_platform.py` diagnostic has its own idle-shutdown
and observed backend-HTTP/2 expectations. It does not replace these held-worker
acceptance cases or establish the selected profile from an idle restart.

This driver covers US-068-AC1, AC5 and AC8. The separate selected-profile collector
must establish AC2, AC3, AC4, AC6, AC7, AC9 and AC10. Genuine OAuth expiry requires
a bounded wait using credentials retained only in memory; forged expiry or an
unbound diagnostic result cannot replace it. Coordinate authorization revocation
and restoration after the browser driver has stopped. All final receipts use the
same primary profile and normalized HTTPS app origin as their opaque binding.

Run that collector after the browser driver has released the app. Its private
reference files identify the owned test service principal, Keychain entry,
selected app, database settings, deployed source and immutable local bundle;
none is a token argument. The primary profile must be the same one used above.
Select revocation administration explicitly. The optional identity-admin
reference selects the existing Azure CLI account-admin authority of the
correlated primary human; ordinary application requests retain their human and
service caller profiles. The adapter verifies the exact owned test principal,
workspace scope and administrator before changing active state. Without that
reference, only an independently authorized workspace-SCIM administrator is
supported; a permission refusal never triggers an implicit account fallback.
Do not grant a role to make qualification pass. AC3 runs after the other quick
cases and AC6 runs last so failed prerequisites refuse before the genuine-expiry
wait:

```bash
PYTHONPATH="$PWD/examples/databricks-app/src" \
  uv run --locked python scripts/qualify_selected_profiles.py \
  --profile "$PROFILE" --service-profile "$TEST_SERVICE_PROFILE" \
  --service-config-file "$SERVICE_CONFIG" \
  --service-secret-reference "$KEYCHAIN_REFERENCE" \
  --settings-file "$QUALIFICATION_SETTINGS" \
  --app-reference-file "$APP_REFERENCE" \
  --identity-reference "$OWNED_IDENTITY_REFERENCE" \
  --identity-admin-reference "$IDENTITY_ADMIN_REFERENCE" \
  --deployment-reference-file "$DEPLOYMENT_REFERENCE" \
  --bundle "$DEPLOYED_BUNDLE" --browser-receipt "$LIVE_RECEIPT" \
  --output "$PROFILE_RECEIPT" \
  --case US-068-AC2 --case US-068-AC4 --case US-068-AC7 \
  --case US-068-AC9 --case US-068-AC10 --case US-068-AC3 \
  --case US-068-AC6
```

The privacy case opens bounded APP and unfiltered platform log followers before
its successful and rejected requests. It requires fresh worker and HTTP request
metadata anchors, then scans both captures and capture diagnostics for fabricated
markers. Keep actual source kinds in the evidence: this profile emits HTTP/BUILD
records and can have no SYSTEM records. Empty captures or missing fresh request
anchors refuse qualification.

The expiry case records observed mint/expiry/rejection times and fresh-connection
results, never credential values. A network failure is not an authentication
rejection. The development-only independent oracle accepts only a structured
post-TLS authentication refusal; fresh connections still use the production
Psycopg stack. Keep the product cohort frozen during this bounded wait; independent
read-only quality work may proceed. Retain a failed run as diagnostic evidence
and resolve its cause before collecting a new successful receipt.
The explicit `PYTHONPATH` exposes this repository's example acquisition helper to
the qualification harness; it does not establish clean installed-consumer proof.
The browser receipt must already pass for this same frozen source/profile cohort.

After promoting only sanitized successful receipts and reconciling all ten
carrier rows in the current acceptance manifest, verify without repeating the
live mutations:

```bash
make web-platform-check WEB_PROFILE="$PROFILE" \
  WEB_APP_ORIGIN="$APP_ORIGIN" WEB_LIVE_RECEIPT="$LIVE_RECEIPT"
```

This command verifies the exact two-case live receipt, shutdown measurements,
runtime cohort, source hashes and all ten profile-bound acceptance rows. It does
not perform another app stop/restart or treat the generic platform probe as
complete acceptance. Missing, stale, failed or skipped required proof refuses
the gate.

Run the deterministic `make web-check CHECK_BASE=HEAD` aggregate once for the
frozen cohort. After its current successful receipt, the performance proof and
complete acceptance evidence exist, use the receipt-based release postcheck:

```bash
make web-release-postcheck WEB_PROFILE="$PROFILE" \
  WEB_APP_ORIGIN="$APP_ORIGIN" WEB_LIVE_RECEIPT="$LIVE_RECEIPT"
```

The postcheck verifies the saved aggregate and current platform/acceptance proof;
it does not rerun unchanged long suites. Source changes invalidate those receipts.
A successful postcheck establishes the documented candidate gates; publication,
tagging and production rollout remain separately authorized actions.

## Dependencies and Failure Boundaries

| Boundary | Failure signal | Action |
|---|---|---|
| Trusted Apps identity | Missing, duplicate or expired forwarded identity/credential | Refuse the protected operation; renew the selected user authorization. The opt-in service-caller profile verifies its projected bearer and canonical principal, requires the forwarded email-claim to match the SDK-verified username, and treats the bounded forwarded user identifier as non-authoritative IdP metadata; it ignores the distinct forwarded access-token value and rejects duplicate headers. |
| Caller-scoped PostgreSQL | Credential exchange, TLS, schema or row-policy refusal | Fix the named deployment binding using its provisioning owner. |
| Static asset cohort | Readiness failure, missing or altered manifest item | Restore the complete verified UI artifact. |
| Optional history source | Typed pending, denied, missing, partial or unavailable item | Preserve that status and source locator; do not replace it with empty history. |
| Accepted worker | Outstanding shutdown report or unknown effect | Retain ownership and inspect committed facts after physical exit. |

The native PostgreSQL 18 profile requires its native durability settings. The
explicit managed Lakebase profile uses an endpoint-attested acquisition provider
and different managed durability checks. Never select the managed profile merely
to bypass a native refusal. Their separate evidence lives with IP-002.

## Alert Triage

| Symptom | Immediate checks | Stop condition |
|---|---|---|
| Increasing API failures | Bounded route/outcome counts and deployment transition | Hold rollout when authentication, storage or schema identity is uncertain. |
| Repeated reconnect/reset | Snapshot watermark, retention gap, event bounds | Stop automatic retries at the client bound; expose reconnect/recovery. |
| Unknown effect count grows | Authorized effect page and invocation identities | Do not resume unsafe work until an operator verifies its external outcome. |
| Shutdown deadline expires | Outstanding resource names and physical workers | Never report those workers stopped or close borrowed dependencies. |
| Browser startup fails | UI manifest, config prefix, CSP/page errors | Do not relax CSP to permit generated-code evaluation. |

## Common Incident Procedures

### Accepted command with a lost response

1. Keep the original request ID, session ID and any accepted turn locator.
2. Reauthenticate and read the current session through the same service.
3. Read the accepted turn result. A known ongoing turn returns no terminal result;
   an unknown turn is a distinct refusal.
4. If acceptance must be retried, use the unchanged request ID and payload.
   A conflicting payload must fail.
5. Cancel only the intended turn ID. A stale target cannot cancel a newer turn.

Validate recovery by reading the persisted terminal result and current session
state. Disconnecting a consumer does not cancel the turn.

### Stream gap or authorization change

1. Clear provisional text and stop the old subscription.
2. Resolve the current identity. Never substitute the app service principal.
3. Obtain an atomic authorized snapshot and follow from its exact decimal position.
4. Preserve complete committed batches only. An incomplete batch has no advanced
   durable cursor and must replay.

Validate with the committed transcript and current pending/effect views. A denied
source stays denied; a transport reconnect cannot widen disclosure authority.

### Unknown external effect

1. Obtain the authorized unresolved-effect page and stable invocation ID.
2. Inspect the external system using the current permitted operator identity.
3. Record `completed` or `not_completed` with evidence and a reason through the
   public client resolution operation.
4. Resume only after Tether accepts the resolution and reports the session resumable.

A platform kill is not evidence that the external action failed. Do not blindly
replay an unsafe action.

## Rollback and Recovery

### Rollback Entry Conditions

Hold a deployment when any required gate fails, resource bindings change identity,
assets do not match their manifest, or the new process cannot verify its schema.
Retain bounded diagnostics and artifact digests before changing the deployment.

### Rollback Procedure

1. Seal new admissions and drain owned work within the configured deadline.
2. Retain any outstanding worker/unknown-effect report and the external database.
3. Deploy the previously qualified immutable app bundle using the private inventory
   and the platform deployment command. Do not rebuild a different artifact under
   the same identity.
4. Restore that bundle's matching settings and asset cohort.
5. Run its explicit compatible schema verification. Do not apply a reverse migration
   or delete authoritative data as an application rollback step.

### Recovery Validation

Read a preexisting session, obtain its persisted turn result, reconnect from a
snapshot, and verify owner isolation. Check pending approvals and unknown effects
before accepting new commands. Successful app compute alone does not establish
application or database readiness.

## Routine Operations

| Operation | Procedure | Verification |
|---|---|---|
| Database credentials expire | Acquire a current endpoint-scoped credential for the next physical connection | Existing current caller identity is retained; failures refuse without fallback. |
| Schema upgrade | Export and review migration SQL, back up the selected resource, apply as the named migration owner | Runtime startup only verifies the resulting revision/checksum/profile. |
| Temporary resource cleanup | Stop only the qualification app; record database retention/disposition before deleting dedicated resources | Private inventory and cleanup receipts identify every created resource. |

Always provide the exact app name when deleting a qualification app. In a project
directory, `databricks apps delete` without a name destroys the project's deployed
resources. Review dependencies and use the named operation for this temporary
qualification cohort.

## Escalation and Communications

The deployment operator owns app/resource configuration and private diagnostics.
The library maintainer owns protocol, storage and lifecycle defects. Workspace
administrators own PIM, app ingress, grants and identity provisioning. Escalate
managed storage/ingress faults through the workspace's established Databricks
support route. Public issue reports contain fabricated reproductions and bounded
metadata only; no automatic notification or raw diagnostic upload is authorized.
