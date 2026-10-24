# tether

A Python library for building agent harnesses. Tether integrates Pydantic AI Agent with
typed tools, execution policy and durable sessions. Its in-process async Client API exposes
session metadata, history, persisted changes and a partial command facade. Telemetry and
reference CLI/terminal clients are planned in later build slices.

**Status: gated execution, local durability and partial Client commands (slices 2–4 subset).** Pydantic AI Agent now runs tools through
Tether's policy, validation, approval and commit gates. Model doubles and shared codecs remain
available. Local JSONL journal/history and pending-approval restart reconstruction are implemented;
Owner-bound Client metadata, history, call-detail and verified pending reads are available through an async `Tether` context with explicit `ClientLimits`. Bounded persisted follow/replay is also available. Task-owned `send`, shielded result handles, canonical request retries, current-source result reads, lease heartbeats and explicit cancellation are implemented. Create/resume/update/close/resolve commands, provisional text production, Postgres and reference clients remain unfinished. See the [build plan](docs/helix/02-design/solution-designs/SD-001-tether-library-design-and-build-plan.md).

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
Text/function-tool responses are checked over fabricated HTTPX2 peers. Empty stop completions
produce a typed `empty_response` failure; they never trigger an unmediated correction. Provisional network
streaming, media/reasoning content, effort selection, SDK validation feedback, structured output
and live server compatibility remain unqualified or explicitly refused. A supplied custom
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
The POSIX single-writer profile stores checksummed batches and accepted-prefix witnesses,
fences leased writes, and reconstructs pending approval from journal/source facts with the
original supplied specs. Unknown writes require reopening; close retains ownership while
actual I/O remains. Process-exit evidence does not prove power-cut behavior. Manual effect
resolution, safe started-invocation recovery, scope/spec resolver integration and telemetry
remain unfinished. Model/tool attachments,
non-JSON executable argument leaves and lossy argument serializers are refused by this subset.
The fabricated examples and faults are in [the execution tests](tests/test_gated_execution.py)
and [the integration review](tests/test_execution_review.py).

## Client command subset

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
