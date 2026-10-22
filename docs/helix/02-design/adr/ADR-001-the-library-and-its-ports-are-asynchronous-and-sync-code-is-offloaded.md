---
ddx:
  id: ADR-001
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: SPIKE-001
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: product-vision
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
    - id: CONTRACT-002
      kind: informs
    - id: CONTRACT-003
      kind: informs
---

# ADR-001: The Library and Its Ports Are Asynchronous, and Synchronous Tool Code Is Offloaded

## Problem

An agent harness spends nearly all of its time waiting: on a streaming model call, on tools, on stores, on a user's approval. An application serving many sessions does that waiting on one event loop or on a thread per turn. A thread cannot be cancelled, so a closed window or a shutdown cannot stop a model stream; a synchronous interface also forces every adapter to be written for blocking I/O. The asynchronous HTTP client the model path uses is asynchronous at its core, and a synchronous wrapper around one starts an event loop per call (SPIKE-001). Tool authors, however, write plain functions and are not all asynchronous programmers.

## Decision

We will make the engine, the session service, the client API, and every port that can wait on I/O asynchronous, and offer no synchronous interface. A synchronous tool or provider method is run by the engine in a worker thread.

- **Which ports are asynchronous.** The model port (`complete` is a coroutine; `complete_stream` returns an asynchronous iterator), the policy, scope, and result-guard ports, the audit sink, the session store, every provider hook, the turn service, the client API, the event sink, and the interaction port. Ports that do no I/O stay synchronous: the tracer, the clock, the identifier source, and the pure checks on a tool.
- **Tool and hook authors may write `def` or `async def`.** The engine runs a plain function in a worker thread through `anyio.to_thread` with the caller's context variables copied and a bounded limiter, so a tool author needs no asynchronous code.
- **No synchronous interface.** The package exports no `run_sync` helper. A script calls `asyncio.run`. The test kit's tests are asynchronous and run with the `anyio` pytest plugin.
- **Concurrency.** Different sessions run concurrently on one event loop. A session runs one turn at a time. Within a turn, steps and tool calls run in order so the transcript and the audit record keep their order; running independent tool calls in parallel is a later decision.
- **Cancellation.** A cancelled turn closes its model stream and requests cancellation of an asynchronous tool. A synchronous tool already running in a worker thread cannot be forcibly stopped; the engine abandons waiting for it, and its external outcome can remain unknown (ADR-014). The steps already committed remain in the session log (ADR-005), and the log ends with a cancelled-turn entry. Cancelling the turn or fencing its log writer does not roll back or fence an external effect. Applications needing stronger isolation run the operation behind an isolated executor and use downstream idempotency or fencing.
- **The engine never blocks the loop.** The project's lint selects ruff's `ASYNC` rules, which flag a blocking call inside an `async def`.

## Why We Chose This

### Over a synchronous library running turns on threads
It is the simplest for tools and tests. It also costs a thread per active turn, cannot cancel a turn, puts every model client behind wrappers that start an event loop per call, and fits only synchronous hosts. A streaming, multi-session consumer benefits from cancellation and cheap concurrency.

### Over offering a synchronous and an asynchronous interface
Two interfaces double the contract and every conformance suite, every port is implemented twice, and the less-used twin is the one that rots.

### Over an asynchronous core with a synchronous facade
A `run_sync` helper gets called from inside event loops by accident, which nests loops or blocks the server. A script can call `asyncio.run`.

## Consequences

**Good**: One interface. Native asynchronous model clients with no event loop per call and no request-id workaround (SPIKE-001 finding 6, SPIKE-002). A turn can be cancelled. Concurrency across sessions without a thread per turn. Streaming and following live turns fit the model naturally.

**Trade-offs**: Every adapter on the turn path must be asynchronous or run in a worker thread (a store on SQLite runs the blocking driver in a thread). A blocking call in the engine stalls every session on the loop, so the lint rule and review matter. Developers who want a synchronous call must wrap it themselves. Abandoned synchronous tools can outlive their turn; applications must account for their resource use and uncertain effects before allowing replacement work.

**Drift signals to watch**: A `time.sleep`, `urllib`, `requests`, or blocking database call inside an `async def`. A `run_sync` or `asyncio.run` inside the package. A port with both a synchronous and an asynchronous method. Thread offload of engine code rather than a tool or provider hook.

## Concern Impact

- **No concern impact**: `python-uv` covers the stack and `concurrency-model` records the async choice. `anyio` is declared as a direct dependency of the core (the core depends on `pydantic` and `anyio` only); this ADR is the coverage for it.
