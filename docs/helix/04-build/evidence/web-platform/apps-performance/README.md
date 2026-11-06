# Apps read measurement operator source

This archive contains the reviewed generic operator source. Its presence is
source evidence only; it does not establish an Apps measurement or a target pass.
The [source manifest](source-hashes.json) records the exact archived bytes.

Copy [the operator](run_apps_performance.py.txt) to a private working directory
with a `.py` extension and verify its SHA-256 against the manifest before running
it. The resulting measurement records this operator hash. Use the project's
qualified interpreter and an existing selected human CLI profile. Supply current
private settings, the exact immutable deployment reference and bundle, the frozen
source inventory, and the allowlisted client hardware profile explicitly:

```sh
"$PROJECT_ROOT/.venv/bin/python" "$APPS_OPERATOR" \
  --project-root "$PROJECT_ROOT" \
  --profile "$HUMAN_PROFILE" \
  --workspace-config "$WORKSPACE_METADATA_CONFIG" \
  --settings-file "$APPS_SETTINGS" \
  --bundle "$IMMUTABLE_BUNDLE" \
  --deployment-reference "$CURRENT_DEPLOYMENT_REFERENCE" \
  --source-map "$FROZEN_SOURCE_MAP" \
  --machine-profile "$CLIENT_HARDWARE_PROFILE" \
  --output "$FRESH_PRIVATE_MEASUREMENT"
```

The operator verifies the current deployment and immutable exported bytes before
and after 100 real capability handshakes and 100 session-list requests. It uses
one preacquired human credential in memory and a borrowed persistent HTTP client;
it does not publish URLs, identifiers, credentials or page contents. The receipt
records page cardinality and observed HTTP versions so later comparisons refuse
a different measured workload. Server hardware is unknown, and these client
observations do not establish server CPU, a large-catalog profile, cold starts
or a service-level guarantee.

The parent enforces a 900-second physical child deadline, bounded output, process
group cleanup and a fresh output file. It writes a measurement only after the
owned group has exited and strict profile/source checks pass. Inputs, deployment
references, inventories and credential state remain private. No cloud execution
is implied by this copy recipe.

The original archive above is retained as the historical one-read operator.
For the corrected reader, use
[`run_apps_performance27_fixed.py.txt`](run_apps_performance27_fixed.py.txt)
and verify its digest in the manifest. This copy drains each child output stream
to EOF in bounded chunks, preserving the parent 2 MiB cap, per-CLI-stream
64 KiB cap, 900-second parent deadline, and existing child cleanup. The offline
reader controls are archived in
[`read_bounded_controls27.py.txt`](read_bounded_controls27.py.txt); they exercise
fragmented reads, exact and over-limit output, propagated read errors, cancellation,
and deadlines without making cloud requests. The fixed archive is source and
control evidence only; it does not establish an Apps measurement.


The corrected operator subsequently completed the actual source27/deploy38 read
measurement, archived as [the exact Apps receipt](../reference-performance/source27-observations/apps-measurement.json)
with SHA-256 `3acc508dc86fe1c3e9819aa20adfffc4cc0b463a6b4696ef176a08139f266505`.
The [failed original attempt](attempt27-01-failed.json) remains explicit: parent
JSON decoding of a partial stream produced no valid receipt. The corrected
operator is a different reviewed source hash; the failed run is not relabelled.
The source-only provenance manifest describes archived operator bytes, while the
separate actual receipt is the measurement evidence. Its baseline adoption is
scoped by [the accepted first-baseline review](../performance-budget-review27-draft.md).
