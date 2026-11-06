# Local OTLP/HTTP embedder profile

This optional example connects Tether's public `ClientPorts.tracer` seam to a
non-global OpenTelemetry SDK provider and a small protobuf OTLP/HTTP exporter.
It sends only an allowlisted set of low-cardinality operation attributes to an
explicit loopback endpoint. It does not read ambient OTLP headers, proxy
settings, resource detectors, or credentials. The example is separate from the
core, API, Databricks carrier, and React packages.

The exporter uses the OpenTelemetry SDK and OTLP protobuf encoder with an
explicit `httpx.Client(trust_env=False)`. It supports a loopback HTTP collector
and deliberately does not configure TLS, authentication, a cloud collector, or
a gateway. Select a different transport only after a separate security review.

The profile uses a bounded batch queue, parent-based trace sampling, finite
export and shutdown timeouts, and reports sampled, delivered, failed, pending,
and dropped spans. While the worker is active, `pending` is the unaccounted
remainder and can include spans rejected by the full queue; after shutdown
completes it is zero and that remainder is reported as `dropped`. If shutdown
returns `outstanding=True`, retain the profile and its exporter until the worker
exits; a timeout is not proof of
physical cleanup.

From the repository root, run the local receiver, real API/host correlation,
privacy, sampling, exporter failure, and bounded shutdown tests with:

```sh
uv run --no-sync pytest --no-cov -q examples/otel/tests
```

These tests are source-checkout evidence for the installed versions in the
repository lock. They do not qualify a remote collector, cloud export, Apps
deployment, TLS identity, or retention policy.
