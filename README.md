# Tether

[Microsite](https://DocumentDrivenDX.github.io/tether/) · [Scenario capture workflow](docs/microsite.md)

Install the `novirhiza-tether` distribution; the Python import remains `tether`.

A Python library for building agent harnesses. Tether integrates Pydantic AI Agent with
typed tools, execution policy and durable sessions. Its in-process async Client API exposes
session metadata, history, persisted changes and a partial command facade. An offline
reference CLI and terminal UI exercise the local profile.

**Status: qualified local runtime and Client profiles.** Pydantic AI Agent runs tools through
Tether's policy, validation, approval and commit gates. Model doubles and shared codecs remain
available. Local JSONL journal/history and pending-approval restart reconstruction are implemented;
Owner-bound Client metadata, history, call-detail and verified pending reads are available through an async `Tether` context with explicit `ClientLimits`. Bounded persisted follow/replay is also available. Task-owned `send`, shielded result handles, canonical request retries, current-source result reads, lease heartbeats and explicit cancellation are implemented. Explicit durable resume is implemented for the qualified local profile. Optional PostgreSQL18 session storage and independent content history are implemented for the qualified database profile. See the [build plan](docs/helix/02-design/solution-designs/SD-001-tether-library-design-and-build-plan.md).

The declared support profile is macOS arm64, Python 3.11.17, the locked dependency versions,
local POSIX JSONL storage and PostgreSQL 18, including the optional embedded helper. Live
compatible-model evidence covers the recorded local DS4/Qwen text/function-tool profile,
with explicit response caps, approximate token estimates and configured safety margins.
The [P0 qualification receipt](docs/helix/03-test/evidence/p0-qualification-20261028.md)
names exact versions, workloads, boundary refusals and process-fault evidence. Additional
operating systems, Python versions, model servers, native media/reasoning, Gateway joins
and the OpenTelemetry exporter require separate qualification.

## Offline reference client

From a clone, `uv run --frozen --python 3.11 --no-dev --extra tui tether` starts a
Textual terminal UI with a scripted model, a
fabricated tool call and an approval. It creates a local JSONL store at
`~/.local/share/tether`. No account, credential or network call is needed for the demo.
Install the optional `tui` extra when running an installed distribution outside the
development environment. The terminal UI shows sessions and transcript history and
supports send, approve/deny and explicit resume. The same local demo profile has CLI
commands for `list`, `show`, `send`, `follow`, `confirm`, `resume` and fabricated cassette
`replay`; run `uv run tether --help` for arguments. Use `--root PATH` to select another
local store. These clients are a reference path. One fresh macOS/Python 3.11 clone with an isolated dependency cache reached
the offline UI in 5.51 seconds, including installation; download time varies with
network and package registry conditions.
For a deliberately volatile in-memory UI demo, use `uv run tether --volatile`;
its sessions disappear when the process exits and resume is disabled.

## Optional compatible model reference

To use the same CLI or terminal UI with an explicitly selected OpenAI-compatible
text/tool model, install the `tui` and `openai` extras and run
`tether --model-config /path/to/model.json`. The JSON profile requires `id`,
`base_url`, `model_name`, `output_limit_field` (`max_tokens` or
`max_completion_tokens`), `context_window_tokens`, `output_tokens`,
`safety_margin_tokens`, a complete `TokenizerProfile` object in
`tokenizer_profile`, and paths named `vocabulary_path` and
`authorization_file`. The optional `max_response_bytes` defaults to 8 KiB and
may be set up to 1 MiB for a measured streaming profile. Relative paths resolve
beside the JSON file. Keep the
authorization file outside the checkout; it contains the complete HTTP
Authorization value and is read afresh for each physical attempt. The profile
contains no secret. Its tokenizer vocabulary and server framing values must be
qualified for the chosen model. The offline demo remains the default when
`--model-config` is absent. A controlled local HTTP peer and one local DS4
text/tool/approval run exercise this path; the selected DS4 run and its remaining
tokenizer/framing limits are recorded in the
[P0 qualification receipt](docs/helix/03-test/evidence/p0-qualification-20261028.md).
Broader provider compatibility and semantic summary fidelity remain open.

## Development

Install [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync --locked
make check
uv build
uv run --locked python scripts/check_distribution.py dist
uv run --locked python scripts/check_distribution.py dist openai
make hooks  # optional: install the committed pre-commit hook
```

The base uses `pydantic-ai-slim`, Pydantic and AnyIO with a checked-in lock. Foundation-model
provider SDKs are excluded from the reachable base dependency graph. The optional `openai` extra supplies the native compatible adapter. Development dependencies
include that extra for its tests; the installed base excludes it. No hosted account or credential is needed for local checks.

`make check` is shared by local work, the hook and CI. It includes planted negative controls
for forbidden imports, private SDK modules, cycles, missing dependencies and raised ceilings.
It runs every publicly collected test in bounded subprocess groups and checks aggregate
branch coverage against the same 85% floor; the per-child deadline remains 180 seconds.

## Current model integration

`ModelRequest`/`ModelResponse` are Tether-owned types; `ModelPort.stream` and `tether.models.complete`
are the external boundary. `tether.models.testing` provides `ScriptedModel`, fabricated-only
`RecordingModel`, `ReplayModel` and the volatile `ListAttemptRecorder`. Cassettes exclude credentials
and callbacks, fix the principal to `recorded`, and verify versioned request keys before replay.

`tether.runtime.pydantic_ai` is internal. Its Agent bridge requires history authorization and
per-call preparation callbacks. `tether.engine.Engine` supplies the gated execution path through
fixed serial tools. Checkpoints are bound derivatives, not execution authority.

The `openai` extra supplies `tether.adapters.openai_compat.OpenAICompatModel`; select it locally
with `uv sync --locked --extra openai`. It requires an explicit base URL, tier map, output-limit field, execution
limits and an attempt recorder. Identity is per request. The current profile uses Pydantic AI's
native chat model, disables SDK retries/redirects, and owns bounded transport retries.
It requests identity encoding and refuses compressed bodies before decoding; decompression
resource limits require separate qualification.
Text/function-tool responses are checked over fabricated HTTPX2 peers and the selected live DS4 profile. Empty stop completions
produce a typed `empty_response` failure; they never trigger an unmediated correction. Provisional network
streaming has controlled local and selected DS4 evidence. Media/reasoning content, effort selection,
SDK validation feedback, structured output and additional server profiles require separate qualification or are explicitly refused. A supplied custom
transport is trusted and must not add hidden retries or redirects.

## Gated execution subset

`Engine.run_turn(actor, log, text, spec=spec)` accepts an explicit `AgentSpec`; `Engine.answer`
continues a pending approval through current policy and source checks. The registry pins tool
versions and schemas. Approvals bind normalized executable values to the actor, invocation,
session, tool and spec. An accepted assistant source precedes dispatch; an accepted intent
precedes every effectful or replay-unsafe tool. Unknown effects block new effectful calls.
Agent owns iteration and deferred continuation. Tether owns every execution permission.

Configure the model, history, limits, tools/providers and policy explicitly. The default
journal attempt recorder fences and authorizes physical attempts; an explicit recorder factory
is available for test/profile integrations. `AllowAll` is the permissive default policy; ownership and engine invariants
still apply. Tool and provider plain functions run off the event loop with bounded capacity.
Cancellation retains the worker's capacity and session marker until actual exit. `aclose()` seals
new work and returns `False` if work remains after the configured finite shutdown wait.

`MemoryTurnLog` and `MemoryHistory` in `tether.testing.execution` implement the slice 2 journal
and message-history subsets. Their durability is `none`. For local durability, explicitly open
`JsonlRoot`, `JsonlSessionStore` and `JsonlHistory`, and use `MessageHistoryAdapter` with Engine.
SessionStore contract4 exposes `store.for_actor(actor)` for caller-aware storage acquisition.
Client operations bind the present actor explicitly, including followers and retained results.
These scopes borrow the store lifetime: closing a scope does not close its root, and global
recovery requires explicit maintenance authority. Applications retain ownership of credential
refresh, connection pools, database roles and RLS. The optional PostgreSQL store preserves caller predicates and never falls back to maintenance authority.
The POSIX single-writer profile stores checksummed batches and accepted-prefix witnesses,
fences leased writes, and reconstructs pending approval from journal/source facts with the
original supplied specs. Unknown writes require reopening; close retains ownership while
actual I/O remains. Process-exit evidence does not prove power-cut behavior. The qualification receipt
records the selected recovery faults, scope/spec resolver integration and metadata-only telemetry.
User and guarded tool-result attachment refs are supported as metadata rendered
into text; the original refs remain in history and model requests. Artifact bytes, native media,
non-JSON executable argument leaves and lossy argument serializers remain outside this subset.
The fabricated examples and faults are in [the execution tests](tests/test_gated_execution.py)
and [the integration review](tests/test_execution_review.py).

The optional compatible adapter can supply an estimator with
`model.estimator(profile=tokenizer_profile, vocabulary=verified_local_bytes)`.
`TokenizerProfile` is exported by `tether.adapters.openai_compat`; it requires explicit tokenizer,
byte/capacity and framing settings. Counts cover native serialized JSON and remain approximate.
Vocabulary loading belongs to the application; the library performs no implicit tokenizer download.
Constructor setup is synchronous; Engine deadlines retain actual off-loop counting through cancellation.
See [the context contract](docs/helix/02-design/contracts/CONTRACT-004-context-projection-and-compaction.md)
for configuration and limits. `uv run --locked python scripts/qualify_tokenizer.py VOCABULARY PROFILE_JSON OUTPUT_JSON`
runs fabricated offline wire/BPE qualification with supplied data; it makes no live model request.

The pure `tether.engine.plan_resume(entries, declarations, session_id=...)` planner classifies
interrupted invocation states from scoped journal facts and immutable `ResumeTool` snapshots.
It preserves source-derived invocation ids, refuses canceled or superseded ancestry, and never
hydrates content or dispatches tools/models. `Client.resume(session_id)` now accepts an interrupted durable fenced session under current
read/resume policy. Agent resumes unfinished calls through all gates; safe retries keep the original
invocation id and intent, unsafe starts become unknown, pending approvals retain their bindings,
and step/resume-chain budgets survive reconstruction. Accepted final sources avoid another model call.
Storage-scoped actual worker ownership blocks replacement Engines until physical exit. This local
profile has restart and prefix evidence; broader process/storage fault qualification remains required.

## Client command subset

`client.create(ref=..., spec=..., title=..., tags=..., settings=...)` creates an owner-scoped
session; a repeated reference returns the existing session. `client.update(session_id, ...)`
merges supplied settings and replaces supplied tags. Omitting title preserves it; passing
`title=None` clears it. Public settings refuse binder-managed `held` context. Updates use the
store lease and refuse while another turn owns it. Current read policy governs command access
and returned summaries; a refused final disclosure cannot undo a known committed change.

Within an active `Tether` context, `client.send(session_id, text, request_id=...)`
returns a handle after durable input acceptance. Await `handle.result()` for completion;
canceling that waiter leaves Tether's turn running. Use `handle.cancel()` or
`client.cancel(session_id)` to request cancellation explicitly. An old handle cannot cancel
a newer turn. A waiting approval is not declined by cancellation.

Select a supplied unambiguous spec by the command's `spec`, the session's stored spec id,
or explicit `Tether(default_spec=...)`. A repeated request id must have identical canonical
text, attachment refs and requested spec. The existing turn is returned without another Agent
run, including after local root/composition restart. Each result delivery uses current caller
policy and verified original sources; unavailable or denied sources refuse content rather than
substituting an empty transcript. Result reads have a finite deadline after execution completes.
An external still-running turn without a local task reports `TurnInProgress` on result lookup;
cross-process following and automatic recovery remain separate qualification.

`client.confirm(session_id, invocation_id, Answer(approved=True))` returns a handle after
known decision acceptance, before the continuing Agent finishes. Late or superseded answers
return `None`; concurrent answers have one winner. Continuation uses the current actor's policy,
principal and credential, with the original logical turn id. Existing successful send handles
wait for an accepted continuation before returning its latest outcome. Optional answer notes
are bounded informational history, outside executable arguments and automatic model input.

`client.close(session_id)` seals pending calls with non-success results and closes the session
in one journal commit. It refuses active turns, live workers and unresolved effects; closed
sessions remain readable and repeat close is idempotent. Failed result-content preparation
cannot publish closure. Explicit cancellation returns the session to idle without advertising
resume; an actual interruption is distinct and may be resumable.

`client.resolve(session_id, invocation_id, "completed", reason="operator_checked")` records
an explicit operator decision under current `RESOLVE_EFFECT` policy and the session lease.
It clears that unknown-effect barrier without another tool or model call. Identical decisions
are idempotent; conflicting outcomes/reasons refuse. Missing terminal results are first closed
as unknown, preserving original evidence. Malformed partial groups refuse instead of clearing
the barrier. A live worker still prevents resolution. Resolution reasons are bounded operator
metadata, and credentials must never be included.

Tether bounds its owned jobs by `ClientLimits.max_active_turns`. Lease loss stops the Agent;
shutdown cancels owned turns and retains storage until actual workers and in-flight heartbeats
exit. An incomplete bounded close keeps ownership through deferred cleanup.

## What it is for

You are building an agent application or a multi-user platform in Python and want to own the
interface, the storage, and the policy. The intended runtime capabilities, delivered in later slices, are:

- **Agent and tools.** Pydantic AI Agent calls a model through the Tether integration, runs your tools (plain
  `def` or `async def` functions with typed arguments), and stops for approval when a tool
  asks.
- **Sessions.** A session is an append-only log committed step by step, so a UI can read it
  while a turn runs and a crashed turn can be recovered.
- **A UI boundary.** A client API lists sessions, reads transcripts in pages, and follows live
  changes with a resumable feed. It is a Python API, not a server; a web server is an adapter
  you write over it.
- **Telemetry.** Spans and audit entries for every turn, model call, and tool call, without
  message content by default.
- **Replaceable models.** Pydantic AI native/compatible model integration is selected. Local/self-hosted subsets and optional provider SDK extras need separate qualification; there is no required hosted-provider account.
- **Testing.** Every port has a dummy implementation and a conformance suite. A recorded model
  conversation replays through the loop deterministically.

## What it is not

Not a UI, not a server, not a multi-agent orchestration graph, not a coding agent, and not a
model provider SDK and not tied to a hosted provider: the base is provider-SDK-free and selected optional adapters may use native SDKs. See the
[non-goals](docs/helix/00-discover/product-vision.md#non-goals).

## License

Dual licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option. Copyright 2026 FocusedDiversity LLC.

## Contributing

You can contribute directly: open an issue or a pull request. There is no contributor license agreement to sign. Contributions are accepted as prompts: describe the change you want precisely enough that a maintainer's coding agent can implement it, and include any reference code as illustration. See [CONTRIBUTING.md](CONTRIBUTING.md).

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this project, as defined in the Apache-2.0 license, is dual licensed as above, without any additional terms or conditions.


Optional archive reads use `Tether(..., archive=source)` and explicit `tier="archive"` on
`Client.list/get/entries/history`. Get/entries default to live first and fall back only on actual
absence. Archive failures stay distinct from empty results. Derived sources remain partial;
full history hydration checks current payload permission after the final metadata await.

`InMemoryArchive(ArchiveLimits(...))` is the bounded volatile reference source/sink. Configure
`archive_sink=sink` and explicitly call `app.archive_session(actor, session_id)` after idle/close;
this preserves live data and runs outside turns. Application retention policy schedules it.
`Client.restore(session_id)` requires a full restorable archive and current read/payload/restore
permission, copies verified content before atomic journal restoration, and preserves pending,
unknown effects and closure. A retry with conflicting reference or creation metadata refuses.
Reference-preserving restore currently requires compatible original provider/codec addresses.
See [archive conformance](tests/test_archive_ports.py) and [Client journeys](tests/test_client_archive.py).


Optional PostgreSQL18 session storage and content history use `novirhiza-tether[postgres]` and
`tether.stores.postgres.PostgresSessionStore` and `PostgresHistory`. Supply a `ConnectionSource(actor, session_id)` async
context manager that borrows an idle psycopg async connection. The application owns its pool,
credential refresh, roles and RLS. Tether owns each short transaction and refuses ambient ones.
Run `migrate(source, actor=maintenance_actor, schema="tether")` explicitly, then
`DatabaseRoot.open(source, actor=bootstrap_actor, schema="tether")`; ordinary opening verifies
schema/revision, namespace and durability settings without migration. Compose
`PostgresSessionStore(root, maintenance=maintenance_actor)` and `PostgresHistory(root)`, or use
independent database roots/history and journal backends. Bind every caller via `store.for_actor(actor)`;
unbound caller operations refuse. Root recovery requires explicit maintenance authority. A bound
PostgreSQL facade also offers `recover_owner(now=aware_datetime)` for deployments permitting only
caller-scoped recovery. Borrowed facade close is non-owning; close database roots explicitly.
Tether never closes your pool.
`migration_sql(schema)` exports the fresh-schema plain SQL statements; apply the complete sequence
in one transaction. `migrate` explicitly upgrades a verified revision1 schema to revision2,
preserving its namespace and immutable history bytes; repeat calls verify the current revision.
Ordinary opening refuses revision1 until that upgrade. Conflicting or drifted tables refuse
rather than receiving implicit repair. Revision2 includes indexed journal, metadata/paging,
materialized pending/effect state and persisted feeds. Leased writes enforce holder/generation
and server-time expiry inside the transaction. Uncertain acknowledgments seal the current handle;
reopening reconstructs from canonical facts. Global recovery is one bounded transaction: candidate
count/bytes or result-byte overflow refuses without partial publication. `page_limit` bounds its
candidate set; larger deployments must select an explicit bound and authority.

This checkpoint qualifies PostgreSQL18.6 with logged ordinary tables, `fsync=on`,
`full_page_writes=on` and `synchronous_commit=on`. It preserves canonical bytes, immutable
keys and ordered read statuses. Cancellation/timeouts retain actual connection work, and
release failures after possible commit remain uncertain. Independent roots share namespace
actual-worker tracking with separate root admission gates. Closing one root seals execution through
that root while a sibling remains usable. Real separate-client processes cover lease exclusion,
takeover, stale operations and killed-client recovery; paired SQL Agent/Client cases cover approval
restart, safe/unsafe resume, manual resolution and archive/restore. A fabricated application-owned
unprivileged-role RLS profile demonstrates credential refresh and denied-caller refusal. This does
not qualify arbitrary deployment roles, downstream exactly-once effects or OS power loss.
Optional `novirhiza-tether[embedded-postgres]` installs the database driver. The embedded helper also
requires an operator-installed `pgserver-pg18==18.6.1` binary wheel from the
[pinned PostgreSQL 18.6 release](https://github.com/DocumentDrivenDX/pgserver/releases/tag/pg18.6-pgv0.8.6-r2).
From this checkout, `uv sync --locked --extra embedded-postgres --group embedded-binaries`
installs the platform-matched wheel with the committed hash. For the qualified macOS arm64/Python 3.11 profile,
install the same prerequisite before using the published distribution:

```sh
uv pip install 'https://github.com/DocumentDrivenDX/pgserver/releases/download/pg18.6-pgv0.8.6-r2/pgserver_pg18-18.6.1-cp311-cp311-macosx_14_0_arm64.whl#sha256=fd42f07982693e16478ed42a297890d142d22250116e08887146e690f0dd2649'
```

The helper uses the same pinned PostgreSQL18 binaries as the
qualification suite. `EmbeddedPostgres.start(EmbeddedConfig(...))` requires an explicit bootstrap_user,
absolute data_dir/runtime_dir, startup_seconds/shutdown_seconds/poll_seconds and max_roots. Its
endpoint supplies Unix socket connection fields; create your own source, run migrations explicitly,
and register roots through `server.open_root(source, actor=actor, schema="tether")`. Close the helper
with `await server.aclose()`. False means retained cleanup is still draining physical work. Persistent
data is preserved; unmanaged directories and existing native servers refuse adoption. The helper
uses local trust authentication within its private runtime directory and disables TCP listening.
The selected binary profile is non-root macOS arm64 or Linux arm64/x86_64, Python 3.11–3.13; other
profiles refuse. Parent death can leave a server requiring explicit operator cleanup. Server binaries
stay out of the base and postgres-only runtime extras. See CONTRACT-003 for the lifecycle contract.
Broader release/profile coverage remains work.


Reusable port checks are available from `tether.testing.conformance`. Await
`live_store_suite(factory)` with an async context-manager factory yielding
`LiveStoreFixture(unbound_store, actor, foreign_actor)`. Use an isolated fabricated namespace;
a durable factory reopens that same namespace on its next entry. The factory owns explicit
migrations, connections and cleanup. The same suite runs against memory, JSONL and PostgreSQL.

`archive_suite(factory)` passes a fabricated immutable snapshot to an async factory yielding
`ArchiveFixture(source, actor, foreign_actor, sink=None)`. Supply current actors and credentials;
source and sink remain independent. A source-only fixture preloads the snapshot. A supplied sink
is exercised even when the source facade is read-only. Suite inputs use independent copies so
provider mutation cannot rewrite the expected result.

Each metadata-only SuiteReport lists executed checks, declared source/store and optional sink
capabilities, and `required_external` qualifications. These logical checks do not confer full
fault, process or release qualification. Keep backend corruption/unknown acknowledgment, physical
worker, contended snapshot/approval and process-fencing suites alongside them. The base package
adds no pytest dependency; a pytest/anyio test may simply await either suite. See CONTRACT-003
for fixture shapes, bounds and the complete-profile obligations.
