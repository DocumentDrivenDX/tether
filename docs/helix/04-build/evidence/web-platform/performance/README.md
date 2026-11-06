# Reference performance gate prerequisites

The seven source27 baselines and their original numeric values remain preserved
as historical evidence. Their accepted operational definitions used the
predeclared `1.2` factor; first establishment was not a historical regression
pass, production SLA or final acceptance. Source24 and earlier observations and
all later misses remain historical. Nothing in the current gate rewrites those
files or reclassifies a failed observation. See the [accepted target
review](../performance-budget-review27-draft.md), [exact
joins](first-baseline-joins27.json), and [first-establishment
receipt](source27-first-establishment/first-baseline-establishment.json).

The owner decision of 2026-11-02 defines the current controlled gate as six
local profiles and 54 metrics. The [controlled policy](../reference-performance/controlled-policy-20261102.json)
lists all metric nodes and counts: store 7, memory-store 7, client 17,
volatile-client 11, engine 2 and follower-memory 10. The Apps latency values are
optional observations outside performance pass/fail; they create no qualified
nodes and do not gate the six local profiles. The Apps baseline and every Apps
measurement remain unchanged historical evidence.

## Admission requirements

Use only the pinned known reference machine profile and an owner-controlled
exclusive reservation for the exact measurement window. The workflow's
self-hosted runner label, a GitHub concurrency group, a profile file, or the
benchmark lock alone does not establish that unrelated work is absent. The
reservation must identify the owner and window, declare the
`exclusive-known-reference-mac` scope and bind to the pinned hardware/runtime
profile. Its JSON contains exactly `reservation_id`, `owner`, `control_scope`,
`binding_sha256`, `exclusive` (`true`) and `expires_at_utc`; the binding is the
canonical `HostBinding` digest of `python`, `os_family`, `architecture`,
`os_version`, `os_build`, `cpu_model`, `physical_cores`, `logical_cores`,
`memory_bytes` and the four normalized storage fields `SolidState`, `Internal`,
`BusProtocol` and `FilesystemType`. It is not the raw machine-profile file SHA.
On the reserved machine, derive the binding with the project interpreter:

```sh
PYTHONPATH=scripts .venv/bin/python -c \
  'from performance_admission import HostBinding; print(HostBinding.current().profile_sha256)'
```

The measurement command independently compares current machine properties
with the pinned reference, acquires a nonblocking OS lock and
observes activity before, throughout and after the workloads. Unknown or
interfering activity leaves the run unadmitted and its measurements
provisional. The activity observer cannot establish provider-level physical
co-tenancy.

As of 2026-11-02, the available Mac has not met the new admission requirement:
other jobs were running on the host, and the prior source28 performance
attempts did not establish the required exclusive window. Those failures and
the readiness deferral remain visible; they are not passes and do not justify
fitting thresholds. Do not launch the actual workloads until the owner has
reserved an isolated measurement window on the known machine and the independent
admission checks can verify it.

## Run the controlled local profiles

Prepare the owner-controlled reservation and pinned machine profile on the
selected runner. Pass their paths through environment-backed workflow inputs or
environment variables so Make does not re-evaluate user-provided text as a
command-line variable. The workflow requires `reservation_file` and
`machine_profile`; `apps_measurement` is optional and is copied only as an
observational sidecar.

For an already-reserved local run, use a fresh output directory and explicit
paths:

```sh
PERF_RESERVATION='/absolute/path/current-exclusive-reservation.json' \
PERF_MACHINE_PROFILE='/absolute/path/pinned-reference-machine.json' \
  make web-performance-check PERF_OUTPUT='/fresh/reference-performance-output'
```

To retain an Apps observation alongside the local results, additionally set
`PERF_APPS_MEASUREMENT` to a source-bound Apps measurement JSON through the
environment. That sidecar has no qualification status, threshold comparison or
effect on the controlled result. Its provider-managed hardware and physical
co-tenancy remain unknown. Missing, unreadable or malformed optional input is
recorded as a refused observational sidecar and does not affect the local
six-profile result. Failure to retain output or evidence is an operational
incomplete run, never a successful result. This target and workflow do not
acquire cloud credentials or perform external deployment/authentication setup.

The six declared local profiles execute serially. The fixed workload datasets,
sample counts, measurement boundaries and numeric limits remain as declared in
their profile definitions and historical baseline files. Performance workload
children retain their explicit profile-specific containment limits; these are
not functional readiness or service-speed assertions. A failed, incomplete or
unadmitted run must remain visible and cannot be turned into a pass by changing
the historical numeric budgets.

The workflow uploads the complete output directory, including raw measurements,
provisional ratchets, final admission evidence, optional Apps observation, and
incomplete or failure records. Hidden-file retention is enabled only for this
dedicated output tree so `.admission-pending` remains available after an
interrupted or unadmitted run. Keep a fresh output directory; a failed attempt
must not leave an older passing report presented as the current result.

## Scope of the protected measurements

Keep the original PRD proposals, actual misses and explicit first-baseline
validate-or-replace decisions visible. The `1.2` comparison factor applies only
to the accepted operational definitions; it is not a service-level promise or
automatic acceptance of a missed proposal. Preserve each profile's dataset,
sample counts, configured limits, durability and measurement boundary. In
particular, whole-turn latency is not exclusive engine overhead, net whole-trial
traced-Python growth is not isolated per-follower total memory, and
request-to-delivery latency does not isolate the accepted-commit segment.
