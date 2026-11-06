# IP-002 selected qualification profiles

This matrix records release-profile selection and evidence limits. It does not
turn a dependency declaration, a focused test or a prior release receipt into a
current complete qualification. Current closure belongs to IP-002 and its
source-bound machine receipts.

| Profile | Selection | Qualification boundary |
|---|---|---|
| Public in-process host and optional terminal | Selected | Explicit settings/ports and resource ownership; full current aggregate and installed profiles remain required. |
| HTTP API and Python remote client | Selected | Versioned commands, durable results, exact counters and current authorization. The remote client owns only a transport it creates; supplied transports remain borrowed; UI/API consumers borrow the client protocol. Real TCP plus installed framework-free client proof. |
| Browser transport, React hooks and visual components | Selected | Locked Node/npm/React cohort, production assets, real Chromium, mounted paths, keyboard/accessibility/text safety, typed replacement and archive-capable consumer. |
| Local POSIX JSONL | Selected local profile | Existing native durability contract; separate fabricated reference-workload and public-client performance measurements. No power-loss claim. |
| Native PostgreSQL 18 | Selected control profile | Explicit source/migration; physical connection/worker ownership and transaction semantics. Native proof does not qualify managed Lakebase. |
| Databricks Apps with managed PostgreSQL 18 | Selected development profile | Explicit managed durability attestation, forced owner RLS, current credentials, actual ingress, restart and bounded shutdown. Private inventory holds all deployment bindings. |
| Temporary instrumented Apps qualification observer | Selected evidence profile | Candidate launcher/carrier modules remain byte-identical; private harness records exact same-process listener H2C and public-ingress observations separately, plus original shutdown report/main return and a fresh post-stop durable row. Observer/probe/bundle/actual-stop hashes identify its limits; it is not an uninstrumented-deployment claim. |
| Apps human caller | Selected | Trusted platform projection and current credential; logical owner and database identity remain separate from infrastructure identity. |
| Apps verified service caller | Selected opt-in qualification extension | Workspace-verifiable allowlisted principal and separately configured OAuth client-ID database role. This is distinct from two-human testing and never a shared infrastructure fallback. |
| Scripted qualification model | Selected Apps example | Fabricated requests and recorded attempt receipts only. It establishes no hosted model or Gateway support. |
| Hosted Gateway/model services | Unselected for this carrier cohort | Existing public model factories and controlled tests remain available. Historical model receipts retain their original versions/subsets; no new live carrier/Gateway correlation claim. |
| Governed retrieval, original-source and remote tool services | Unselected | Typed ports remain extension seams. No new connector, source-readiness or remote-effect qualification is inferred from scripted examples. |
| Local OTLP receiver/exporter in independent embedder | Selected diagnostics profile | Real received OpenTelemetry spans, correlation, content/credential exclusion, bounded delivery, sampling/loss and receiver-failure evidence are required. SDK dependencies stay in the example/dev closure. |
| Cloud OTLP collector from Apps | Unselected | Default Apps metadata logs are qualified separately. No cloud receiver support follows from no-op tracing, generated identifiers or the local receiver profile. |

The candidate package cohort is Python `0.2.0.dev0` and npm `0.2.0-dev.0`.
Internal candidate Python and npm dependencies use exact cohort constraints.
The published Python core 0.2.0 is a separate, incompatible client cohort; both
its hash-pinned archive and the older 0.1.0 archive must be refused by the
candidate API dependency solver. The committed locks and resulting artifact
receipts identify exact third-party versions. A permitted dependency range is not an independent support claim for
every version in that range.

WP-06 records capability selection and explicit unavailable/unselected states;
WP-27 implements only the selected SD-003 composition. Unselected optional
services neither receive passing acceptance labels nor silently widen the
selected carrier release. WP-31 still requires the selected real local receiver
proof and actual default Apps log-privacy proof. Required selected profiles stay
open until their own receipts pass.
