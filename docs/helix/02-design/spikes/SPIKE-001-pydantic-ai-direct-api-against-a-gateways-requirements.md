---
ddx:
  id: SPIKE-001
  type: tech-spike
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: ADR-002
      kind: informs
    - id: ADR-001
      kind: informs
    - id: CONTRACT-001
      kind: informs
---

# Technical Spike: Pydantic AI's Direct API Against a Multi-User Gateway's Requirements

> Handling: the probes run against a mock HTTP transport. They hold no credential, host, or private data; the bearer value is a variable the probe sets, and the "secret content" string is a marker used to test that error bodies do not leak.

**Spike ID**: SPIKE-001 | **Lead**: Erik LaBianca | **Time Budget**: half a day | **Status**: Completed 2026-10-21 (mock transport only; no live provider was called)

## Objective

**Technical Question**: Can Pydantic AI (an open-source Python library from the Pydantic team that wraps model providers behind one message and tool model) carry the requirements of a multi-user, OpenAI-compatible model gateway while the harness keeps its own loop, gates, and types, and can it do so through its native asynchronous calls?

**Goals**:
- [x] A per-call bearer token for the calling user, read from a context variable.
- [x] Per-call `User-Agent` (a session tag) and W3C `traceparent` headers, with the `User-Agent` not overwritten by the client library.
- [x] `reasoning_effort`, tools, tool-call parsing, and token usage on the chat-completions route.
- [x] A streaming call that yields text deltas, then the assembled response with usage, synchronously and asynchronously.
- [x] Concurrent calls, each carrying its own user's token and reading back its own `x-request-id`.
- [x] The gateway's `x-request-id` response header, on success, on a stream, and on failure.
- [x] An HTTP failure maps to an outage whose reason carries a status and never the response body.

**Success Criteria**: Each goal shown against a mock transport, with the gaps named.

**Out of Scope**: A live provider, retry policy, timeouts under load, the Responses route, and production wiring.

## Hypothesis

**Primary**: Pydantic AI's `direct` module (`model_request`, `model_request_stream`, and their `_sync` wrappers) is a thin wrapper over a provider model, so a custom `AsyncOpenAI` client with an `httpx` client underneath can carry every requirement without using Pydantic AI's `Agent` loop.
**Assumptions**: The gateway's chat-completions route is OpenAI-shaped.
**Expected Outcome**: Feasible, with a workaround for reading response headers through the synchronous wrappers.

## Approach

**Method**: Prototype and integration testing against `httpx.MockTransport`. The documentation pages for `direct`, the OpenAI model, and testing were read first; they state the sync functions and the custom-client pattern but not per-call headers, per-call auth, or response headers, so those were tested.

**Activities**:
| Step | Activity | Objective |
|------|----------|-----------|
| 1 | Read the library's documentation for the direct API, the OpenAI model, and testing | Confirm sync and async calls and a custom client exist |
| 2 | Probe (`SPIKE-001-probe/probe.py.txt`) with `pydantic-ai-slim` 2.59.0, `openai` 3.36.0, `httpx` 0.28.1 on Python 3.11 | Show each requirement against a mock transport, through the synchronous wrappers |
| 3 | Async probe (`SPIKE-001-probe/async_probe.py.txt`), same versions | Show the native asynchronous calls and five concurrent calls |

## Findings

**FINDING 1**: Per-user bearer auth works through an `httpx.Auth` object that reads a context variable.
- **Evidence**: The mock server saw `Authorization: Bearer <value set in the caller's context>` on a call made through `model_request_sync`. The sync wrapper runs the request inside the caller's context, so the variable is visible.
- **Implications**: The adapter reads the caller's identity from a context variable it is given and can refuse a call that has none when identity is required.

**FINDING 2**: Per-call headers and request-body fields pass through `ModelSettings`.
- **Evidence**: `extra_headers={"User-Agent": ..., "traceparent": ...}` reached the server and the `User-Agent` was the one supplied, not the client library's. `extra_body={"reasoning_effort": "high"}` appeared as a top-level body field. Tools and the model name appeared in the body unchanged, and the request path was the `base_url` plus `/chat/completions`.
- **Implications**: The session tag, trace join, and effort setting need no change on the wire. The documentation does not describe `extra_headers` for the chat model, so the adapter's tests, not the documentation, are the guarantee.

**FINDING 3**: Tool calls, usage, and synchronous streaming work.
- **Evidence**: A tool-call reply parsed to one `ToolCallPart` with the tool name; usage read 11 input and 7 output tokens. `model_request_stream_sync` yielded the text deltas `Hel`, `lo`; the assembled response is `stream.response` (not a `get()` method) and carried usage 5 and 2 once the usage chunk arrived.
- **Implications**: The port's `complete` and `complete_stream` map directly. The adapter converts the library's parts to the port's `ToolCall`, `TextDelta`, and `TokenUsage`.

**FINDING 4**: Through the synchronous wrappers, the response header `x-request-id` is readable only through a response hook that writes into an object the caller owns.
- **Evidence**: A hook that called `ContextVar.set` inside the client's event loop did not reach the caller (read back `None`). A hook that wrote into a dictionary the caller placed in the context variable before the call returned the id on success (`req-1`), on a stream (`req-stream`), and on a failure (`req-fail`). The body id is available as `provider_response_id`.
- **Implications**: The synchronous wrappers need a holder per call. The native asynchronous calls do not (Finding 6).

**FINDING 5**: A failed call raises an exception whose `str()` includes the response body.
- **Evidence**: A 500 reply with an error message `secret content` raised `ModelServerError` and its text contained `body: {'code': 'INTERNAL', 'message': 'secret content'}`.
- **Implications**: A naive adapter would put provider content into logs and the audit reason. The adapter must catch the library's errors, keep only the status, and never log the exception text. `max_retries` must be set to 0 on the client so retry policy stays in one place.

**FINDING 6**: The native asynchronous calls need less machinery than the synchronous wrappers and hold up under concurrency.
- **Evidence**: With `model_request` and `model_request_stream`, a plain `ContextVar.set` inside the response hook was visible to the caller, so no holder is needed. The stream yielded the deltas `Hel`, `lo` and usage 5 and 2. Five concurrent calls, each in its own task with its own user token, put five distinct bearer values on the wire, and each task read back the request id derived from its own token.
- **Implications**: The adapter is the asynchronous one (ADR-001, ADR-002). The per-call identity design (a context variable read by an `httpx.Auth`) is correct under concurrency, which is the case a harness serving many sessions on one event loop depends on.

### Measurements
| Requirement | Result | Notes |
|-------------|--------|-------|
| Per-call bearer from context | Works | `httpx.Auth` subclass |
| `User-Agent`, `traceparent` per call | Works | `extra_headers`; override honored |
| `reasoning_effort` | Works | `extra_body` |
| Tools and tool-call parsing | Works | |
| Usage (non-stream, stream) | Works | Stream usage needs the final usage chunk |
| Sync and async streaming | Works | `.response` after iteration (sync); `async with` (async) |
| `x-request-id` | Works | Response hook; a caller-owned holder through the sync wrappers, a plain `ContextVar` through the async calls |
| Concurrent calls with distinct users | Works | Five tasks, five tokens, five request ids |
| Error text without the body | Needs adapter code | Library error text includes the body |

## Analysis

**Hypothesis**: CONFIRMED
**Rationale**: Every requirement held against a mock transport, with one piece of adapter code the library does not provide (the error scrub), and one more (the request-id holder) only if the synchronous wrappers were used.

### Risks
| Risk | Prob | Impact | Mitigation |
|------|------|--------|------------|
| The mock transport differs from a live provider (streaming framing, error shapes, tool-call deltas) | M | M | The adapter's acceptance includes a live smoke call against one real provider, and cassettes recorded on fabricated prompts |
| `extra_headers` and `extra_body` are not documented for the chat model and could change | M | M | A wire-level test per requirement; pin the library to an exact version; an upgrade is its own change |
| The library releases breaking changes | M | M | Only the adapter imports it, behind the model port |

## Conclusions

**Primary Conclusion**: Pydantic AI can be the model client behind the model port. The harness keeps its own loop and gates and uses only `direct.model_request` and `direct.model_request_stream`, asynchronously.
**Confidence**: Medium-High. The mock transport proves the library's behavior, not a provider's.
**Limitations**: A live provider, the Responses route, load beyond five concurrent calls, timeouts, and cancellation were not exercised. The library has no record-and-replay facility, so the model port's test doubles must be built.

## Recommendations

**RECOMMENDATION**: Adopt Pydantic AI as the model client inside an adapter behind the model port, and record the decision in ADR-002.
- **Rationale**: It removes the need to write HTTP, streaming assembly, and tool-call parsing while keeping per-call identity, tagging, and the join key (Findings 1 to 4, 6); the one gap is small and testable (Finding 5).
- **Next Steps**: ADR-002 records the dependency; the first build slice implements the adapter and the model test doubles; its acceptance includes one live smoke call.
- **Concern Impact**: Adds a runtime dependency (as an extra) to the `python-uv` concern's tree; no override. ADR-002 ratifies it.

## Artifacts

- `SPIKE-001-probe/probe.py.txt` and `SPIKE-001-probe/async_probe.py.txt`: the probes, run with `uv run --no-project --python 3.11 --with 'pydantic-ai-slim[openai]' --with httpx python <name>.py` after renaming to `.py`.
