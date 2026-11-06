# Reference measurement operator source

This archive contains the reviewed generic sequential measurement operator.
The [source manifest](source-hashes.json) binds its exact bytes. This is source
evidence only; no measurement, adopted target or regression pass is established
by the archive. The commands below document the historical operator snapshot;
they do not satisfy the current admission requirements and are not a current
run instruction. Follow the [current performance runbook](../performance/README.md)
for the six-profile gate, exclusive reservation, pinned machine binding and
retained evidence policy. The archived operator and its manifest remain
unchanged historical evidence.

Copy [the operator](run_reference_performance.py.txt) into a private working
directory with a `.py` extension and verify its SHA-256 against the manifest.
Use the qualified project interpreter after the full deterministic aggregate
passes against the same frozen source inventory:

```sh
"$PROJECT_ROOT/.venv/bin/python" "$REFERENCE_OPERATOR" \
  --project-root "$PROJECT_ROOT" \
  --source-map "$FROZEN_SOURCE_MAP" \
  --machine-profile "$CLIENT_HARDWARE_PROFILE" \
  --aggregate "$CURRENT_AGGREGATE_RECEIPT" \
  --output "$FRESH_PRIVATE_MEASUREMENT_DIRECTORY"
```

The operator runs six declared profiles sequentially: the full local and
MemoryFiles-backed store datasets, local and VolatileSettings public clients,
the public Engine interval measurement, and follower retained-Python allocation
and process-memory observations. Each command retains its own workload identity,
source checks, limits and scope. The full store profiles explicitly use a 4 GiB
index capacity; this does not change product defaults.

Each child runs in an owned process group under its declared physical deadline.
The operator refuses remaining descendants, changed source or hardware/operator
bytes, and incomplete profile identities. Its execution report records the
operator hash and every raw measurement hash. Native process, filesystem and
PTY permissions must be available for the declared local profile.

These are measurement receipts. Target review and first-baseline establishment
are separate, and establishing a baseline is not a historical regression pass.
The separately authenticated [Apps operator](../apps-performance/README.md)
produces the external read profile; this local operator does not acquire cloud
credentials or substitute local observations for Apps latency.
