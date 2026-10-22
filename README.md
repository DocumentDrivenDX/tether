# tether

A Python library for building agent harnesses. Tether provides the underpinnings: an
asynchronous agent loop, a typed tool-call interface, durable session management, and
telemetry. It exposes the session list and session data to a user interface through a fast
in-process Python API, and ships a simple command-line client and terminal UI for
development and testing.

**Status: design stage.** There is no released code yet. The scope, boundaries, and decisions
are in [`docs/helix/`](docs/helix/). Start with the
[product vision](docs/helix/00-discover/product-vision.md), then the
[requirements](docs/helix/01-frame/prd.md) and the
[architecture](docs/helix/02-design/architecture.md).

## What it is for

You are building an agent application or a multi-user platform in Python and want to own the
interface, the storage, and the policy. Tether is the runtime underneath:

- **Loop and tools.** An `async` loop calls a model through a port, runs your tools (plain
  `def` or `async def` functions with typed arguments), and stops for approval when a tool
  asks.
- **Sessions.** A session is an append-only log committed step by step, so a UI can read it
  while a turn runs and a crashed turn can be recovered.
- **A UI boundary.** A client API lists sessions, reads transcripts in pages, and follows live
  changes with a resumable feed. It is a Python API, not a server; a web server is an adapter
  you write over it.
- **Telemetry.** Spans and audit entries for every turn, model call, and tool call, without
  message content by default.
- **Testing.** Every port has a dummy implementation and a conformance suite. A recorded model
  conversation replays through the loop deterministically.

## What it is not

Not a UI, not a server, not a multi-agent orchestration graph, not a coding agent, and not a
model provider SDK (it uses [Pydantic AI](https://ai.pydantic.dev) for model calls). See the
[non-goals](docs/helix/00-discover/product-vision.md#non-goals).

## License

Dual licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option. Copyright 2026 Synaptiq.
